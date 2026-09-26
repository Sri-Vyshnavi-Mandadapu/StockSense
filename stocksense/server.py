import json
import logging
import os
import sqlite3
import threading
import time
from collections import defaultdict, deque
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from . import auth, inventory
from .db import initialize, transaction

WEB = Path(__file__).resolve().parent.parent / 'web'
ATTEMPTS = defaultdict(deque)
LOCK = threading.Lock()

class Handler(BaseHTTPRequestHandler):
    def reply(self, status, data, cookie=None, content_type='application/json'):
        body = json.dumps(data).encode() if content_type == 'application/json' else data
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','same-origin')
        self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        self.send_header('Cache-Control','no-store')
        if cookie is not None:
            secure = '; Secure' if os.environ.get('STOCKSENSE_SECURE_COOKIE') == '1' else ''
            self.send_header('Set-Cookie',f'session={cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age={86400 if cookie else 0}{secure}')
        self.end_headers()
        self.wfile.write(body)

    def token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie',''))
            return cookie['session'].value if 'session' in cookie else ''
        except Exception:
            return ''

    def do_GET(self):
        path = urlsplit(self.path).path
        if path.startswith('/api/'):
            user = auth.session(self.token())
            if not user:
                return self.reply(401,{'error':'Please sign in'})
            if path == '/api/me':
                return self.reply(200,user)
            if path == '/api/state':
                return self.reply(200,inventory.snapshot())
            return self.reply(404,{'error':'Not found'})
        files = {'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/styles.css':('styles.css','text/css; charset=utf-8')}
        if path not in files:
            return self.reply(404,{'error':'Not found'})
        filename,mime = files[path]
        self.reply(200,(WEB/filename).read_bytes(),content_type=mime)

    def do_POST(self):
        try:
            if self.headers.get('X-StockSense') != '1' or self.headers.get('Content-Type','').split(';')[0] != 'application/json':
                return self.reply(403,{'error':'Invalid request origin or content type'})
            length = int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 65536:
                return self.reply(413,{'error':'Request is empty or too large'})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data,dict):
                raise ValueError('Expected a JSON object')
            path = urlsplit(self.path).path
            if path in ('/api/login','/api/signup','/api/forgot','/api/reset'):
                now = time.time()
                key = self.client_address[0]
                with LOCK:
                    # Bound limiter memory and use the socket peer, never untrusted forwarded headers.
                    for expired in [k for k,v in ATTEMPTS.items() if not v or v[-1] < now-600]:
                        del ATTEMPTS[expired]
                    attempts = ATTEMPTS[key]
                    while attempts and attempts[0] < now-600:
                        attempts.popleft()
                    if len(attempts) >= 20:
                        return self.reply(429,{'error':'Too many attempts. Try again in 10 minutes.'})
                    attempts.append(now)
                if path in ('/api/login','/api/signup'):
                    user,token = auth.authenticate(data,path == '/api/signup')
                    return self.reply(200,user,cookie=token)
                if path == '/api/forgot':
                    auth.request_reset(data)
                    return self.reply(200,{'message':'If that account exists, a reset code has been sent.'})
                auth.reset_password(data)
                return self.reply(200,{'message':'Password updated. Please sign in.'},cookie='')
            user = auth.session(self.token())
            if not user:
                return self.reply(401,{'error':'Please sign in'})
            if path == '/api/logout':
                auth.logout(self.token())
                return self.reply(200,{'ok':True},cookie='')
            if path in ('/api/products','/api/warehouses') and user['role'] != 'manager':
                return self.reply(403,{'error':'Only inventory managers can change products or warehouses'})
            if path == '/api/products':
                result = inventory.product(data,user['id'])
            elif path == '/api/warehouses':
                result = inventory.warehouse(data)
            elif path == '/api/documents':
                result = inventory.document(data,user['id'])
            elif path == '/api/transition':
                result = inventory.transition(data,user['id'])
            elif path == '/api/profile':
                with transaction() as db:
                    db.execute('UPDATE users SET name=? WHERE id=?',(inventory.required(data,'name'),user['id']))
                result = True
            else:
                return self.reply(404,{'error':'Not found'})
            self.reply(200,{'ok':True,'id':result})
        except sqlite3.IntegrityError:
            self.reply(400,{'error':'Duplicate SKU, email, name or product line, or an invalid reference. Check your entries.'})
        except (ValueError,KeyError,TypeError) as error:
            self.reply(400,{'error':str(error) or 'Invalid request'})
        except Exception:
            logging.exception('Request failed')
            self.reply(500,{'error':'Unable to complete this request. Please try again.'})

def main():
    initialize()
    host,port = os.environ.get('HOST','127.0.0.1'),int(os.environ.get('PORT','8000'))
    print(f'StockSense is running at http://{host}:{port}',flush=True)
    ThreadingHTTPServer((host,port),Handler).serve_forever()
