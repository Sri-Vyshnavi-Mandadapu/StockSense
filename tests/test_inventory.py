import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from stocksense import auth, inventory
from stocksense.db import initialize, transaction

class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ,{'STOCKSENSE_DB':self.temp.name+'/test.db'})
        self.env.start()
        initialize()
        with transaction() as db:
            self.actor=db.execute("INSERT INTO users(name,email,password,role) VALUES('Manager','test@example.com','unused','manager')").lastrowid
        inventory.warehouse({'name':'Main','location':'Store'})
        inventory.warehouse({'warehouse_id':1,'location':'Production'})
        self.pid=inventory.product({'name':'Steel','sku':'STL','category':'Materials','unit':'kg','reorder':10},self.actor)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def doc(self,kind,qty,source=None,dest=None,lines=None):
        did=inventory.document({'type':kind,'source_id':source,'destination_id':dest,'lines':lines or [{'product_id':self.pid,'qty':qty}]},self.actor)
        for action in ('wait','ready'):
            inventory.transition({'id':did,'action':action},self.actor)
        if kind=='Delivery':
            for action in ('pick','pack'):
                inventory.transition({'id':did,'action':action},self.actor)
        return did

    def validate(self,did):
        inventory.transition({'id':did,'action':'validate'},self.actor)

    def balance(self,location):
        return next((s['qty'] for s in inventory.snapshot()['stock'] if s['product_id']==self.pid and s['location_id']==location),0)

    def test_complete_inventory_flow(self):
        self.validate(self.doc('Receipt',100,dest=1))
        self.validate(self.doc('Internal',100,source=1,dest=2))
        self.assertEqual(self.balance(1)+self.balance(2),100)
        self.validate(self.doc('Delivery',20,source=2))
        self.validate(self.doc('Adjustment',77,source=2))
        self.assertEqual(self.balance(2),77)
        ledger=inventory.snapshot()['ledger']
        self.assertEqual(len(ledger),5)
        self.assertEqual(ledger[0]['delta'],-3)

    def test_insufficient_stock_rolls_back_all_lines(self):
        self.validate(self.doc('Receipt',10,dest=1))
        other=inventory.product({'name':'Chairs','sku':'CHR','category':'Furniture','unit':'units'},self.actor)
        did=self.doc('Delivery',0,source=1,lines=[{'product_id':self.pid,'qty':5},{'product_id':other,'qty':1}])
        with self.assertRaisesRegex(ValueError,'Insufficient'):
            self.validate(did)
        self.assertEqual(self.balance(1),10)
        self.assertEqual(len(inventory.snapshot()['ledger']),1)

    def test_duplicate_validation_and_concurrent_deliveries(self):
        receipt=self.doc('Receipt',10,dest=1)
        self.validate(receipt)
        with self.assertRaises(ValueError):
            self.validate(receipt)
        docs=[self.doc('Delivery',7,source=1) for _ in range(2)]
        def attempt(did):
            try:
                self.validate(did)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(attempt,docs)),1)
        self.assertEqual(self.balance(1),3)

    def test_transfer_failure_is_atomic(self):
        self.validate(self.doc('Receipt',4,dest=1))
        with self.assertRaises(ValueError):
            self.validate(self.doc('Internal',5,source=1,dest=2))
        self.assertEqual(self.balance(1),4)
        self.assertEqual(self.balance(2),0)

    def test_zero_count_and_immutable_ledger(self):
        self.validate(self.doc('Receipt',4,dest=1))
        self.validate(self.doc('Adjustment',0,source=1))
        self.assertEqual(self.balance(1),0)
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError),transaction() as db:
            db.execute('DELETE FROM ledger')

    def test_invalid_values_and_workflow(self):
        for value in (-1,float('nan'),float('inf'),'bad',0.0001):
            with self.assertRaises(ValueError):inventory.quantity(value)
        with self.assertRaises(ValueError):self.doc('Internal',1,source=1,dest=1)
        did=inventory.document({'type':'Delivery','source_id':1,'lines':[{'product_id':self.pid,'qty':1}]},self.actor)
        with self.assertRaises(ValueError):self.validate(did)
        for action in ('wait','ready'):inventory.transition({'id':did,'action':action},self.actor)
        with self.assertRaises(ValueError):inventory.transition({'id':did,'action':'pack'},self.actor)
        with self.assertRaises(ValueError):self.validate(did)
        inventory.transition({'id':did,'action':'cancel'},self.actor)
        with self.assertRaises(ValueError):self.validate(did)

    def test_opening_stock_is_audited(self):
        inventory.product({'name':'Bolts','sku':'BLT','category':'Hardware','unit':'units','initial':8,'location_id':1},self.actor)
        self.assertEqual(inventory.snapshot()['ledger'][0]['delta'],8)

class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'STOCKSENSE_DB':self.temp.name+'/auth.db','STOCKSENSE_DEV_OTP':'1','SMTP_HOST':''})
        self.env.start();initialize()

    def tearDown(self):
        self.env.stop();self.temp.cleanup()

    def test_signup_roles_login_logout_and_reset(self):
        credentials={'name':'Owner','email':'owner@example.com','password':'SecurePass123!'}
        user,token=auth.authenticate(credentials,True)
        self.assertEqual(user['role'],'manager')
        staff,_=auth.authenticate({**credentials,'email':'staff@example.com'},True)
        self.assertEqual(staff['role'],'staff')
        self.assertEqual(auth.session(token)['id'],user['id'])
        with patch('stocksense.auth.secrets.randbelow',return_value=123456),patch('builtins.print'):
            auth.request_reset(credentials)
        with self.assertRaises(ValueError):auth.reset_password({**credentials,'code':'000000'})
        auth.reset_password({**credentials,'code':'123456','password':'NewSecurePass123!'})
        self.assertIsNone(auth.session(token))
        with self.assertRaises(ValueError):auth.reset_password({**credentials,'code':'123456'})
        with self.assertRaises(ValueError):auth.authenticate(credentials)
        _,new_token=auth.authenticate({**credentials,'password':'NewSecurePass123!'})
        auth.logout(new_token)
        self.assertIsNone(auth.session(new_token))

    def test_reset_attempt_limit_and_expiry(self):
        credentials={'name':'Owner','email':'owner@example.com','password':'SecurePass123!'}
        auth.authenticate(credentials,True)
        with patch('stocksense.auth.secrets.randbelow',return_value=123456),patch('builtins.print'):
            auth.request_reset(credentials)
        for _ in range(5):
            with self.assertRaises(ValueError):auth.reset_password({**credentials,'code':'000000'})
        with self.assertRaises(ValueError):auth.reset_password({**credentials,'code':'123456'})
        with transaction() as db:db.execute('UPDATE resets SET attempts=0,expires=0')
        with self.assertRaises(ValueError):auth.reset_password({**credentials,'code':'123456'})

if __name__=='__main__':unittest.main()
