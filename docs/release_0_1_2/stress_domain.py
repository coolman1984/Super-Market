"""Independent destructive-on-disposable-data audit. Does not modify production code.
Observations include expected rejections; a failed check is not automatically a confirmed bug.
Run from repo: python docs/release_0_1_2/stress_domain.py [results.json]
"""
import concurrent.futures
import json
import pathlib
import random
import sys
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
from helpers import ShopFixture, ean, key
from server.core import ShopError
from server import core


class Fixture(ShopFixture, unittest.TestCase):
    pass


results = []


def run(name, fn):
    f = Fixture()
    started = time.perf_counter()
    try:
        f.make_shop()
        data = fn(f)
        results.append(dict(case=name, seconds=round(time.perf_counter()-started, 3), **data))
    except Exception as e:
        results.append(dict(case=name, passed=False, exception=type(e).__name__, detail=str(e)))
    finally:
        f.doCleanups()


def raw_on_shelf(f):
    p = f.meat()
    try:
        f.receive(p, 1000, 10000, location=f.shelf)
    except ShopError as e:
        return dict(passed=e.code=='meat_needs_cold_storage', rejected=e.code,
                    receipts=f.store.scalar('SELECT COUNT(*) FROM receipts'), stock=f.shop.available(p))
    return dict(passed=False, accepted=True)


def split_promotion(f):
    p = f.grocery(price=1500)
    f.shop.add_promotion(f.owner, key(), p, 2, 1, '2026-10-30')
    one = f.shop.quote(f.owner, {'lines': [{'product_id': p, 'qty': 3}]})
    split = f.shop.quote(f.owner, {'lines': [{'product_id': p, 'qty': 1} for _ in range(3)]})
    return dict(passed=one['total']==split['total'], single_row=one['total'], split_rows=split['total'],
                invariant='equal eligible quantities of one SKU should not change promotion eligibility by line splitting')


def large_price(f):
    try:
        p = f.grocery(price=2**63-1)
    except ShopError as e:
        return dict(passed=True, controlled_error=e.code, rejection_stage="catalog")
    f.receive(p, 2, 0)
    f.open_shift(f.owner)
    try:
        f.shop.sell(f.owner, key(), {'lines': [{'product_id': p, 'qty': 2}]})
    except ShopError as e:
        return dict(passed=True, controlled_error=e.code)
    except Exception as e:
        return dict(passed=False, exception=type(e).__name__, detail=str(e), stock_after=f.shop.available(p),
                    sales_after=f.store.scalar('SELECT COUNT(*) FROM sales'))
    return dict(passed=False, exception='accepted out-of-range invoice')


def price_label_meat(f):
    p = f.meat(code='00123', price=40000)
    f.receive(p, 1000, 10000, location=f.fridge)
    f.shop.save_label_template(f.owner, {'name':'audit weight','prefix':'21','length':13,'item_start':2,'item_len':5,
        'value_start':7,'value_len':5,'value_kind':'weight','decimals':3})
    code = ean('21'+'00123'+'00500')
    scan = f.shop.resolve_scan(f.cash1, code)
    return dict(passed=scan.get('qty')==500, qty=scan.get('qty'), scan_ok=scan.get('ok'))


def replay_and_reuse(f):
    p=f.grocery()
    f.receive(p, 10, 10000)
    f.open_shift()
    k=key(); payload={'lines':[{'product_id':p,'qty':2}]}
    sale=f.shop.sell(f.cash1,k,payload)
    for _ in range(250):
        replay=f.shop.sell(f.cash1,k,payload)
        assert replay['sale']==sale['sale'] and replay['replayed']
    rejected=[]
    for actor,changed in [(f.cash1,{'lines':[{'product_id':p,'qty':3}]}),(f.cash2,payload)]:
        try: f.shop.sell(actor,k,changed)
        except ShopError as e: rejected.append(e.code)
    return dict(passed=f.shop.available(p)==8 and len(rejected)==2, replays=250, rejected=rejected,
        sales=f.store.scalar('SELECT COUNT(*) FROM sales'), stock=f.shop.available(p), reconcile=f.shop.reconcile()['ok'])


def concurrency(f):
    p=f.grocery()
    f.receive(p,100,10000)
    f.open_shift(f.manager)
    def sell(_):
        try:
            f.shop.sell(f.manager,key(),{'lines':[{'product_id':p,'qty':1}]})
            return 'sold'
        except ShopError as e: return e.code
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        outcomes=list(pool.map(sell,range(200)))
    return dict(passed=outcomes.count('sold')==100 and f.shop.available(p)==0 and f.shop.reconcile()['ok'],
        attempts=200,succeeded=outcomes.count('sold'),rejections={x:outcomes.count(x) for x in set(outcomes) if x!='sold'},
        stock=f.shop.available(p))


def random_sales(f):
    rng=random.Random(20261031)
    products=[f.grocery('G'+str(i),price=1001+i*123,barcodes=()) for i in range(8)]
    for p in products: f.receive(p,10000,500000)
    f.open_shift(f.manager)
    checks=1200
    expected={p:10000 for p in products}
    total=0
    for _ in range(checks):
        chosen=rng.sample(products,rng.randint(1,4))
        lines=[{'product_id':p,'qty':rng.randint(1,5)} for p in chosen]
        gross=sum(f.shop.current_price(l['product_id'])*l['qty'] for l in lines)
        discount=rng.randint(0,gross//25)
        sale=f.shop.sell(f.manager,key(),{'lines':lines,'invoice_discount':discount})
        assert sale['total']==gross-discount
        total+=sale['total']
        for l in lines: expected[l['product_id']]-=l['qty']
    real={p:f.shop.available(p) for p in products}
    report=f.shop.day_report(f.owner)
    return dict(passed=real==expected and report['sales_total']==total and f.shop.reconcile()['ok'],
        invoices=checks,expected_total=total,reported_total=report['sales_total'],stock_matches=real==expected)


def malformed_scans(f):
    rng=random.Random(42)
    chars='0123456789abcdef<>[]\x00\x1d\r\n١٢٣٤٥٦٧٨٩'
    failures=[]
    for i in range(300):
        raw=''.join(rng.choice(chars) for _ in range(rng.randrange(0,150)))
        try: f.shop.resolve_scan(f.cash1,raw)
        except ShopError: pass
        except Exception as e: failures.append({'i':i,'exception':type(e).__name__})
    return dict(passed=not failures,attempts=300,unhandled=failures[:10])


def count_with_movement(f):
    p=f.grocery(); r=f.receive(p,10,10000); lot=r['lines'][0]['lot']
    c=f.shop.open_count(f.owner,f.shelf)
    f.shop.record_count(f.counter,c['count'],lot,9)
    f.open_shift(f.manager)
    f.shop.sell(f.manager,key(),{'lines':[{'product_id':p,'qty':2}]})
    f.shop.approve_count(f.owner,key(),c['count'])
    return dict(passed=f.shop.available(p)==7,stock=f.shop.available(p),expected=7)


def expiry_and_recall(f):
    p=f.meat(); r=f.receive(p,1000,10000,location=f.fridge); lot=r['lines'][0]['lot']
    f.shop.recall(f.owner,key(),lot,'audit')
    f.open_shift(f.manager)
    try: f.shop.sell(f.manager,key(),{'lines':[{'product_id':p,'qty':1}]})
    except ShopError as e: return dict(passed=f.shop.available(p)==0,rejected=e.code,stock=f.shop._balance(lot,f.fridge)[0])
    return dict(passed=False,accepted=True)


def later_credit_collection(f):
    import datetime as dt
    from helpers import NOW
    p=f.grocery();f.receive(p,10,10000)
    customer=f.shop.add_party(f.owner,'customer','Audit credit customer',credit_limit=100000)
    f.open_shift(f.manager,0)
    f.shop.sell(f.manager,key(),{'customer_id':customer,'lines':[{'product_id':p,'qty':1}],
        'payments':[{'method':'credit','amount':1500}]})
    f.shop.close_shift(f.manager,key(),0)
    core.set_clock(NOW+dt.timedelta(days=1))
    shift=f.open_shift(f.manager,0)
    f.shop.customer_payment(f.manager,key(),customer,1500)
    report=f.shop.day_report(f.owner,'2026-10-31')
    period=f.shop.period_report(f.owner,'2026-10-31','2026-10-31')
    return dict(passed=report['collections']==period['collections']==[{'method':'cash','state':'settled','amount':1500}],collection_posted=1500,drawer_expected=f.shop.shift_expected(shift['shift']),
        report_collections=report['collections'],period_collections=f.shop.period_report(f.owner,'2026-10-31','2026-10-31')['collections'],
        invariant='a table labelled collection by payment method must include actual debt collections on the collection day')


def later_transfer_confirmation(f):
    import datetime as dt
    from helpers import NOW
    p=f.grocery();f.receive(p,10,10000);f.open_shift(f.manager,0)
    sale=f.shop.sell(f.manager,key(),{'lines':[{'product_id':p,'qty':1}],
        'payments':[{'method':'transfer','amount':1500,'reference':'AUD-TRANSFER'}]})
    before=f.shop.day_report(f.owner,'2026-10-30')['collections']
    payment=f.store.scalar('SELECT id FROM payments WHERE sale_id=?',(sale['sale'],))
    f.shop.close_shift(f.manager,key(),0)
    core.set_clock(NOW+dt.timedelta(days=1))
    f.shop.confirm_payment(f.owner,key(),payment,'confirmed')
    old=f.shop.day_report(f.owner,'2026-10-30')['collections']
    today=f.shop.day_report(f.owner,'2026-10-31')['collections']
    return dict(passed=not old and today==[{'method':'transfer','state':'settled','amount':1500}],before=before,old_day_after_confirmation=old,confirmation_day=today,
        invariant='collection chronology must not be inferred only from the sale operation date')


if __name__=='__main__':
    for name,fn in [('raw_meat_on_grocery_shelf',raw_on_shelf),('promotion_split_rows',split_promotion),
                    ('maximum_valid_price_arithmetic',large_price),('weighing_barcode',price_label_meat),
                    ('replay_and_cross_user_keys',replay_and_reuse),('concurrent_last_stock',concurrency),
                    ('randomized_accounting',random_sales),('malformed_scanners',malformed_scans),
                    ('count_during_sales',count_with_movement),('recalled_stock_sale',expiry_and_recall),
                    ('later_credit_collection_report',later_credit_collection),('later_transfer_confirmation_report',later_transfer_confirmation)]:
        run(name,fn)
    target=pathlib.Path(sys.argv[1] if len(sys.argv)>1 else '/tmp/auditd-domain.json')
    target.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(results,ensure_ascii=False))
