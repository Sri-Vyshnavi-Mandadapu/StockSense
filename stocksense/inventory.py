import math
from datetime import date
from .db import transaction, rows

def required(data, key):
    value = str(data.get(key, '')).strip()
    if not value or len(value) > 200:
        raise ValueError(f'{key}: enter between 1 and 200 characters')
    return value

def quantity(value):
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError('Enter a valid quantity')
    if not math.isfinite(result) or result < 0 or result > 1e12:
        raise ValueError('Quantity must be between 0 and 1 trillion')
    if abs(result - round(result, 3)) > 0.00001:
        raise ValueError('Use at most 3 decimal places')
    return round(result, 3)

def snapshot():
    with transaction() as db:
        return {key: rows(db, query) for key, query in {
            'products': 'SELECT p.*,COALESCE(SUM(s.qty),0) total FROM products p LEFT JOIN stock s ON s.product_id=p.id GROUP BY p.id ORDER BY p.name',
            'warehouses': 'SELECT * FROM warehouses ORDER BY name',
            'locations': 'SELECT l.*,w.name warehouse FROM locations l JOIN warehouses w ON w.id=l.warehouse_id ORDER BY w.name,l.name',
            'stock': 'SELECT * FROM stock',
            'documents': 'SELECT d.*,u.name creator FROM documents d LEFT JOIN users u ON u.id=d.created_by ORDER BY d.id DESC',
            'lines': 'SELECT * FROM lines',
            'ledger': 'SELECT l.*,u.name actor FROM ledger l LEFT JOIN users u ON u.id=l.actor_id ORDER BY l.id DESC LIMIT 1000',
        }.items()}

def product(data, actor):
    with transaction() as db:
        values = (required(data,'name'),required(data,'sku').upper(),required(data,'category'),required(data,'unit'),quantity(data.get('reorder',0)))
        if data.get('id'):
            if not db.execute('UPDATE products SET name=?,sku=?,category=?,unit=?,reorder=? WHERE id=?',(*values,data['id'])).rowcount:
                raise ValueError('Product not found')
            return data['id']
        pid = db.execute('INSERT INTO products(name,sku,category,unit,reorder) VALUES(?,?,?,?,?)',values).lastrowid
        initial = quantity(data.get('initial',0))
        if initial:
            loc = data.get('location_id')
            if not loc:
                raise ValueError('Select an initial stock location')
            did = db.execute("INSERT INTO documents(type,status,destination_id,scheduled,notes,created_by) VALUES('Receipt','Done',?,?,?,?)",(loc,date.today().isoformat(),'Opening stock',actor)).lastrowid
            db.execute('INSERT INTO lines(document_id,product_id,qty) VALUES(?,?,?)',(did,pid,initial))
            move(db,did,pid,loc,initial,actor)
        return pid

def warehouse(data):
    with transaction() as db:
        wid = data.get('warehouse_id')
        if not wid:
            wid = db.execute('INSERT INTO warehouses(name) VALUES(?)',(required(data,'name'),)).lastrowid
        db.execute('INSERT INTO locations(warehouse_id,name) VALUES(?,?)',(wid,required(data,'location')))
        return wid

def document(data, actor):
    kind = data.get('type')
    if kind not in ('Receipt','Delivery','Internal','Adjustment'):
        raise ValueError('Unknown operation type')
    source, dest = data.get('source_id') or None, data.get('destination_id') or None
    if kind in ('Delivery','Internal','Adjustment') and not source:
        raise ValueError('Select a source location')
    if kind in ('Receipt','Internal') and not dest:
        raise ValueError('Select a destination location')
    if kind == 'Internal' and str(source) == str(dest):
        raise ValueError('Transfer locations must differ')
    source = source if kind != 'Receipt' else None
    dest = dest if kind in ('Receipt','Internal') else None
    scheduled = date.fromisoformat(data.get('scheduled') or date.today().isoformat()).isoformat()
    items = data.get('lines',[])
    if not isinstance(items,list) or not 1 <= len(items) <= 100:
        raise ValueError('Add between 1 and 100 product lines')
    with transaction() as db:
        did = db.execute('INSERT INTO documents(type,source_id,destination_id,partner,scheduled,notes,created_by) VALUES(?,?,?,?,?,?,?)',(kind,source,dest,str(data.get('partner',''))[:200],scheduled,str(data.get('notes',''))[:2000],actor)).lastrowid
        for line in items:
            qty = quantity(line.get('qty'))
            if kind != 'Adjustment' and qty == 0:
                raise ValueError('Movement quantities must be greater than zero')
            db.execute('INSERT INTO lines(document_id,product_id,qty) VALUES(?,?,?)',(did,line['product_id'],qty))
        return did

def move(db, did, pid, loc, delta, actor):
    old = db.execute('SELECT qty FROM stock WHERE product_id=? AND location_id=?',(pid,loc)).fetchone()
    balance = round((old['qty'] if old else 0) + delta,3)
    if balance < 0:
        name = db.execute('SELECT name FROM products WHERE id=?',(pid,)).fetchone()['name']
        raise ValueError(f'Insufficient stock for {name}; no movements were applied')
    db.execute('INSERT INTO stock VALUES(?,?,?) ON CONFLICT(product_id,location_id) DO UPDATE SET qty=excluded.qty',(pid,loc,balance))
    db.execute('INSERT INTO ledger(document_id,product_id,location_id,delta,balance,actor_id) VALUES(?,?,?,?,?,?)',(did,pid,loc,delta,balance,actor))

def transition(data, actor):
    with transaction() as db:
        doc = db.execute('SELECT * FROM documents WHERE id=?',(data.get('id'),)).fetchone()
        if not doc:
            raise ValueError('Document not found')
        action = data.get('action')
        if doc['status'] in ('Done','Canceled'):
            raise ValueError('This operation is already closed')
        if action in ('pick','pack'):
            if doc['type'] != 'Delivery' or doc['status'] != 'Ready':
                raise ValueError('Only ready deliveries can be picked or packed')
            if action == 'pack' and not doc['picked']:
                raise ValueError('Pick items before packing')
            column = 'picked' if action == 'pick' else 'packed'
            db.execute(f'UPDATE documents SET {column}=1 WHERE id=?',(doc['id'],))
            return
        target = {'wait':'Waiting','ready':'Ready','cancel':'Canceled','validate':'Done'}.get(action)
        allowed = {'Draft':('Waiting','Canceled'),'Waiting':('Ready','Canceled'),'Ready':('Done','Canceled')}
        if target not in allowed.get(doc['status'],()):
            raise ValueError('Invalid status transition')
        if target == 'Done':
            if doc['type'] == 'Delivery' and not doc['packed']:
                raise ValueError('Pick and pack before validating delivery')
            for line in db.execute('SELECT * FROM lines WHERE document_id=?',(doc['id'],)).fetchall():
                pid, qty = line['product_id'], line['qty']
                if doc['type'] == 'Adjustment':
                    old = db.execute('SELECT qty FROM stock WHERE product_id=? AND location_id=?',(pid,doc['source_id'])).fetchone()
                    move(db,doc['id'],pid,doc['source_id'],round(qty-(old['qty'] if old else 0),3),actor)
                else:
                    if doc['source_id']:
                        move(db,doc['id'],pid,doc['source_id'],-qty,actor)
                    if doc['destination_id']:
                        move(db,doc['id'],pid,doc['destination_id'],qty,actor)
        db.execute('UPDATE documents SET status=? WHERE id=?',(target,doc['id']))
