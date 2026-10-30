"""Read-only audit of app code; each case creates a temporary synthetic shop.
Run: python docs/audit/reproduce_review.py
These checks intentionally demonstrate current faults, not passing acceptance.
"""
import datetime as dt
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT))
from helpers import ShopFixture, key, ean, NOW
from server import core
from server.core import ShopError
from server.scanner import ScannerHub

class Fixture(ShopFixture, unittest.TestCase):
    pass

def case(name, fn):
    f = Fixture(); f.make_shop()
    try:
        result = fn(f)
        print(json.dumps({'id': name, 'observed': result}, ensure_ascii=False))
    finally:
        f.doCleanups()

def mixed_return(f):
    p = f.grocery(price=10000); f.receive(p, 1, 5000, expires=None); f.open_shift()
    sale = f.shop.sell(f.cash1, key(), {'lines':[{'product_id':p,'qty':1}],
        'payments':[{'method':'card','amount':5000}], 'cash_tendered':5000})
    line = f.shop.sale_detail(f.owner, sale['sale'])['lines'][0]['id']
    results = {}
    for method in ('cash','original','credit_note'):
        try:
            f.shop.return_sale(f.cash1,key(),{'sale_id':sale['sale'],'lines':[{'sale_line_id':line,'qty':1}],
                'reason':'quality','method':method})
            results[method] = 'accepted'
        except ShopError as e: results[method] = e.code
    assert results['cash'] == results['original'] == 'refund_exceeds_paid_by_method'
    return results

def paid_credit(f):
    p=f.grocery(price=10000); f.receive(p,1,5000,expires=None); f.open_shift(f.manager,0)
    c=f.shop.add_party(f.owner,'customer','Synthetic customer',credit_limit=10000)
    sale=f.shop.sell(f.manager,key(),{'lines':[{'product_id':p,'qty':1}],'customer_id':c,
        'payments':[{'method':'credit','amount':10000}]})
    f.shop.customer_payment(f.manager,key(),c,10000)
    line=f.shop.sale_detail(f.manager,sale['sale'])['lines'][0]['id']
    payload={'sale_id':sale['sale'],'lines':[{'sale_line_id':line,'qty':1}],'reason':'quality'}
    try: f.shop.return_sale(f.manager,key(),dict(payload,method='cash'))
    except ShopError as e: cash_error=e.code
    f.shop.return_sale(f.manager,key(),dict(payload,method='credit_note'))
    assert f.shop.customer_balance(c)==-10000
    return {'cash_refund':cash_error,'customer_balance':f.shop.customer_balance(c),
        'cash_still_in_drawer':f.shop.shift_expected(f.shop._open_shift_of(f.manager)['id'])}

def recalled_return(f):
    p=f.grocery(price=1000); f.receive(p,1,500,expires=None); f.open_shift()
    lot=f.store.scalar('SELECT id FROM lots')
    sale=f.shop.sell(f.cash1,key(),{'lines':[{'product_id':p,'qty':1}]})
    line=f.shop.sale_detail(f.owner,sale['sale'])['lines'][0]['id']
    f.shop.recall(f.owner,key(),lot,'Safety recall')
    r=f.shop.return_sale(f.cash1,key(),{'sale_id':sale['sale'],'lines':[{'sale_line_id':line,'qty':1,'restock':True}],
        'reason':'wrong_item','method':'cash'})
    available=f.shop.available(p)
    sale2=f.shop.sell(f.cash1,key(),{'lines':[{'product_id':p,'qty':1}]})
    assert available==1
    return {'returned_disposition':r['lines'][0]['disposition'],'available_after_recall_return':available,'resale':sale2['no']}

def recipe_omission(f):
    meat=f.meat(); sauce=f.meat('SAUCE',price=1000,kind='ingredient'); out=f.meat('TRAY',kind='prepared')
    r=f.receive(sauce,1000,100,f.fridge)
    v=f.shop.save_recipe(f.owner,out,[{'product_id':meat,'qty':800,'role':'meat'},
        {'product_id':sauce,'qty':200,'role':'food'}],1,'',0)
    result=f.shop.produce(f.butcher,key(),{'kind':'recipe','recipe_version_id':v['recipe_version'],
        'inputs':[{'lot_id':r['lines'][0]['lot'],'location_id':f.fridge,'qty':1000}],
        'outputs':[{'product_id':out,'qty':1000,'location_id':f.fridge}],
        'packs':[{'net_g':1000,'tare_g':10,'meat_g':900}]})
    assert result['outputs'][0]['qty']==1000
    return {'meat_consumed_g':0,'output_g':1000,'label_claimed_meat_g':900,'production':result['production']}

def gs1_ignored(f):
    p=f.meat(); gtin=ean('0622123456789'); f.shop.add_barcode(f.owner,gtin,p)
    r=f.receive(p,1000,10000,f.fridge,expires='2026-11-05',supplier_lot='FRESH')
    raw='(01)'+gtin+'(17)261029(10)RECALLED'
    scan=f.shop.resolve_scan(f.cash1,raw)
    f.open_shift()
    sale=f.shop.sell(f.cash1,key(),{'lines':[{'product_id':p,'qty':500,'scanned':raw}]})
    assert scan['expiry_hint']=='2026-10-29' and sale['total']>0
    return {'scanned_expiry':scan['expiry_hint'],'scan_accepted':scan['ok'],'sale':sale['no'],
        'allocated_supplier_lot':f.store.scalar('SELECT supplier_lot FROM lots WHERE id=(SELECT lot_id FROM sale_allocations LIMIT 1)')}

def negative_drawer(f):
    f.open_shift(f.manager,0)
    f.shop.cash_move(f.manager,key(),'expense',10000,'No cash present')
    result=f.shop.shift_expected(f.shop._open_shift_of(f.manager)['id'])
    assert result<0
    return {'drawer_before':0,'cash_paid_out':10000,'drawer_after':result}

def payment_status(f):
    p=f.grocery(price=1000); f.receive(p,1,100,expires=None); f.open_shift(f.manager,0)
    sale=f.shop.sell(f.manager,key(),{'lines':[{'product_id':p,'qty':1}],
        'payments':[{'method':'transfer','amount':1000,'reference':'T-TEST'}]})
    payment=f.store.scalar('SELECT id FROM payments')
    f.shop.confirm_payment(f.manager,key(),payment,'confirmed')
    detail=f.shop.sale_detail(f.manager,sale['sale'])
    report=f.shop.day_report(f.manager)
    assert detail['payments'][0]['state']=='pending'
    return {'pending_queue':len(f.shop.pending_payments(f.manager)),
        'receipt_payment_state':detail['payments'][0]['state'],'report':report['by_method']}

def scanner_restart(f):
    old=ScannerHub(); old.push('d','first');old.push('d','second')
    cursor=old.since(0)[1]
    new=ScannerHub();new.push('d','new-scan')
    events,last=new.since(cursor)
    browser_next=max(cursor,last)
    assert not events and browser_next==2
    return {'old_cursor':cursor,'new_server_last':last,'events_delivered':len(events),'browser_cursor_after_poll':browser_next}

def price_changed(f):
    p=f.grocery(price=10000);f.receive(p,2,5000,expires=None);f.open_shift()
    quote=f.shop.resolve_scan(f.cash1,ean('622123456789'))['product']['price']
    f.shop.set_price(f.owner,key(),p,15000)
    sale=f.shop.sell(f.cash1,key(),{'lines':[{'product_id':p,'qty':1,'scanned':ean('622123456789')}],
        'cash_tendered':20000})
    assert sale['total']!=quote
    return {'cart_price_at_scan':quote,'charged_without_quote_confirmation':sale['total'],'cash_received':20000}

def cross_packaging(f):
    a=f.grocery('A',barcodes=[])
    b=f.grocery('B',barcodes=[],carton=ean('1622123456789'))
    pk=f.store.scalar('SELECT id FROM packagings WHERE product_id=?',(b,))
    wrongcode=ean('622123456789')
    f.shop.add_barcode(f.owner,wrongcode,a,pk)
    result=f.shop.resolve_scan(f.cash1,wrongcode)
    assert result['product']['id']==a and result['packaging']['product_id']==b
    return {'sold_product_id':a,'packaging_belongs_to_product_id':b,'proposed_quantity':result['qty']}

def scanner_broadcast(f):
    hub=ScannerHub(); hub.push('till-one-device','6221234567899')
    a=hub.since(0,0)[0]; b=hub.since(0,0)[0]
    assert a==b and len(a)==1
    return {'two_consumers_receive_same_event':a[0]['id']==b[0]['id'],'no_workstation_argument':True}

def idempotency_actor(f):
    p=f.grocery(); f.receive(p,5,500,expires=None); f.open_shift(f.cash1);f.open_shift(f.cash2)
    k=key();payload={'lines':[{'product_id':p,'qty':1}]}
    first=f.shop.sell(f.cash1,k,payload); second=f.shop.sell(f.cash2,k,payload)
    assert first['sale']==second['sale']
    return {'other_cashier_received_same_sale':True,'sales_posted':f.store.scalar('SELECT COUNT(*) FROM sales')}

def accountant_supplier(f):
    from helpers import PASSWORD
    f.store.tx(lambda:f.shop.auth.create_user('acct','Accountant','accountant',PASSWORD))
    actor=f.actor('acct'); sup=f.shop.add_party(f.owner,'supplier','Supplier')
    try:f.shop.open_shift(actor,key(),10000)
    except ShopError as e: opening=e.code
    try:f.shop.supplier_payment(actor,key(),sup,100)
    except ShopError as e: paying=e.code
    assert paying=='no_open_shift'
    return {'open_shift':opening,'supplier_payment':paying}

def packed_loss(f):
    p=f.meat();r=f.receive(p,1000,10000,f.fridge)
    out=f.meat('OUT')
    result=f.shop.produce(f.owner,key(),{'kind':'cutting','inputs':[{'lot_id':r['lines'][0]['lot'],'location_id':f.fridge,'qty':1000}],
        'outputs':[{'product_id':out,'qty':1000,'location_id':f.fridge}], 'packs':[{'net_g':500},{'net_g':500}]})
    lot=result['outputs'][0]['lot']
    f.shop.waste(f.owner,key(),lot,f.fridge,500,'damaged')
    stock=f.store.scalar('SELECT SUM(qty) FROM stock_balances WHERE lot_id=?',(lot,))
    packs=f.store.scalar("SELECT SUM(net_g) FROM packs WHERE lot_id=? AND status='available'",(lot,))
    reconciled=f.shop.reconcile()['ok']
    assert packs>stock and reconciled
    return {'physical_stock_g':stock,'available_pack_g':packs,'reconcile_says_ok':reconciled}

if __name__=='__main__':
    for name,fn in [('R01',mixed_return),('R02',paid_credit),('R03',recalled_return),('R04',recipe_omission),
        ('R05',gs1_ignored),('R06',negative_drawer),('R07',payment_status),
        ('R09',scanner_broadcast),('R10',idempotency_actor),('R11',accountant_supplier),('R12',packed_loss),
        ('R13',scanner_restart),('R14',price_changed),('R15',cross_packaging)]:
        case(name,fn)
