import hashlib
import hmac
import os
import re
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from .db import transaction
from .inventory import required

def password_hash(password, salt=None):
    if not isinstance(password,str) or not 10 <= len(password) <= 256:
        raise ValueError('Password must contain 10–256 characters')
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),600000).hex()
    return salt + ':' + digest

def email_address(data):
    email = required(data,'email').lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):
        raise ValueError('Enter a valid email address')
    return email

def public(user):
    return {k:user[k] for k in ('id','name','email','role')}

def authenticate(data, signup=False):
    email = email_address(data)
    token = secrets.token_urlsafe(32)
    with transaction() as db:
        if signup:
            # The first account owns the installation; subsequent signups are warehouse staff.
            role = 'manager' if not db.execute('SELECT 1 FROM users LIMIT 1').fetchone() else 'staff'
            db.execute('INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)',(required(data,'name'),email,password_hash(data.get('password')),role))
        user = db.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
        supplied = data.get('password','')
        candidate = password_hash(supplied,user['password'].split(':')[0] if user else '0'*32)
        if not user or not hmac.compare_digest(candidate,user['password']):
            raise ValueError('Invalid email or password')
        db.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
        db.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user['id'],time.time()+86400))
        return public(user),token

def session(token):
    with transaction() as db:
        user = db.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?',(hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
        return public(user) if user else None

def logout(token):
    with transaction() as db:
        db.execute('DELETE FROM sessions WHERE token=?',(hashlib.sha256(token.encode()).hexdigest(),))

def request_reset(data):
    email = email_address(data)
    host = os.environ.get('SMTP_HOST')
    dev = os.environ.get('STOCKSENSE_DEV_OTP') == '1'
    if not host and not dev:
        raise ValueError('Password reset email is not configured. Contact your administrator.')
    code = f'{secrets.randbelow(1000000):06d}'
    with transaction() as db:
        if not db.execute('SELECT 1 FROM users WHERE email=?',(email,)).fetchone():
            return
        db.execute('INSERT OR REPLACE INTO resets VALUES(?,?,?,0)',(email,password_hash('otp:'+code),time.time()+600))
    if host:
        message = EmailMessage()
        message['From'] = os.environ.get('SMTP_FROM',os.environ.get('SMTP_USER',''))
        message['To'] = email
        message['Subject'] = 'Your StockSense password reset code'
        message.set_content(f'Your StockSense code is {code}. It expires in 10 minutes. If you did not request this, ignore this email.')
        with smtplib.SMTP(host,int(os.environ.get('SMTP_PORT','587')),timeout=15) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if os.environ.get('SMTP_USER'):
                smtp.login(os.environ['SMTP_USER'],os.environ['SMTP_PASSWORD'])
            smtp.send_message(message)
    elif dev:
        print(f'DEVELOPMENT ONLY — password reset for {email}: {code}',flush=True)

def reset_password(data):
    email = email_address(data)
    hashed = password_hash(data.get('password'))
    code = str(data.get('code',''))
    valid = False
    with transaction() as db:
        reset = db.execute('SELECT * FROM resets WHERE email=?',(email,)).fetchone()
        if reset and reset['expires'] > time.time() and reset['attempts'] < 5:
            db.execute('UPDATE resets SET attempts=attempts+1 WHERE email=?',(email,))
            if re.fullmatch(r'\d{6}',code):
                valid = hmac.compare_digest(password_hash('otp:'+code,reset['code'].split(':')[0]),reset['code'])
            if valid:
                db.execute('UPDATE users SET password=? WHERE email=?',(hashed,email))
                db.execute('DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE email=?)',(email,))
                db.execute('DELETE FROM resets WHERE email=?',(email,))
    if not valid:
        raise ValueError('Invalid or expired code. Request a new code after five failed attempts.')
