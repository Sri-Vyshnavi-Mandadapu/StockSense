import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT NOT NULL,email TEXT NOT NULL UNIQUE,password TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'staff');
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id),expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS resets(email TEXT PRIMARY KEY,code TEXT NOT NULL,expires REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS warehouses(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS locations(id INTEGER PRIMARY KEY,warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),name TEXT NOT NULL,UNIQUE(warehouse_id,name));
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY,name TEXT NOT NULL,sku TEXT NOT NULL UNIQUE,category TEXT NOT NULL,unit TEXT NOT NULL,reorder REAL NOT NULL DEFAULT 0 CHECK(reorder>=0));
CREATE TABLE IF NOT EXISTS stock(product_id INTEGER REFERENCES products(id),location_id INTEGER REFERENCES locations(id),qty REAL NOT NULL CHECK(qty>=0),PRIMARY KEY(product_id,location_id));
CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY,type TEXT NOT NULL CHECK(type IN ('Receipt','Delivery','Internal','Adjustment')),status TEXT NOT NULL DEFAULT 'Draft',partner TEXT NOT NULL DEFAULT '',source_id INTEGER REFERENCES locations(id),destination_id INTEGER REFERENCES locations(id),scheduled TEXT NOT NULL,notes TEXT NOT NULL DEFAULT '',picked INTEGER NOT NULL DEFAULT 0,packed INTEGER NOT NULL DEFAULT 0,created_by INTEGER REFERENCES users(id),created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS lines(id INTEGER PRIMARY KEY,document_id INTEGER NOT NULL REFERENCES documents(id),product_id INTEGER NOT NULL REFERENCES products(id),qty REAL NOT NULL CHECK(qty>=0),UNIQUE(document_id,product_id));
CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY,document_id INTEGER NOT NULL REFERENCES documents(id),product_id INTEGER NOT NULL REFERENCES products(id),location_id INTEGER NOT NULL REFERENCES locations(id),delta REAL NOT NULL,balance REAL NOT NULL,actor_id INTEGER REFERENCES users(id),created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS ledger_document ON ledger(document_id);
CREATE INDEX IF NOT EXISTS document_status ON documents(status,type);
CREATE INDEX IF NOT EXISTS lines_document ON lines(document_id);
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger BEGIN SELECT RAISE(ABORT,'Ledger is immutable'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger BEGIN SELECT RAISE(ABORT,'Ledger is immutable'); END;
'''

def connect():
    path = Path(os.environ.get('STOCKSENSE_DB', 'data/stocksense.db'))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA journal_mode=WAL')
    return db

def initialize():
    db = connect()
    try:
        db.executescript(SCHEMA)
    finally:
        db.close()

@contextmanager
def transaction():
    db = connect()
    try:
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def rows(db, sql, args=()):
    return [dict(row) for row in db.execute(sql, args)]
