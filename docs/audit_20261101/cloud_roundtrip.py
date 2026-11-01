"""Larger cloud-copy audit against the real Worker running ONLY on localhost."""
import hashlib
import json
import pathlib
import sys

ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests'))
from helpers import key
from test_cloudsync import CloudCopy
from server import cloudsync
from server.db import Store
from server.services import Shop


def digest(db,table):
    rows=db.execute('SELECT * FROM "%s" ORDER BY rowid' % table).fetchall()
    data=[list(r) for r in rows]
    return {'rows':len(data),'sha256':hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()}


CloudCopy.setUpClass()
fixture=CloudCopy()
try:
    fixture.connect()
    fixture.receive(fixture.pid,500,50000,expires=None)
    for i in range(400):
        sale=fixture.sell_one()
        if i%10==0:
            fixture.shop.return_sale(fixture.cash1,key(),{'sale_id':sale['sale'],'reason':'wrong_item','method':'cash',
                'lines':[{'sale_line_id':sale['lines'][0]['line'],'qty':1,'restock':True}]})
    real_push=fixture.sync.cloud.push
    def lost_reply(records):
        real_push(records)
        raise cloudsync.CloudError(0,'simulated reply loss after storage')
    fixture.sync.cloud.push=lost_reply
    try: fixture.sync.push_once()
    except cloudsync.CloudError: pass
    waiting=fixture.sync.pending()
    fixture.sync.cloud.push=real_push
    batches=fixture.sync.drain()
    config=fixture.device(fixture.store_id)
    target=fixture.folder/'restore-audit'
    report=cloudsync.restore_from_cloud(cloudsync.Cloud(config),target)
    restored=Store(target,'shop')
    try:
        compared={}
        for t in ['sales','sale_lines','sale_allocations','payments','cash_entries','stock_entries','lots','receipts','return_lines','returns']:
            a,b=digest(fixture.store.db,t),digest(restored.db,t)
            compared[t]={'rows':a['rows'],'identical':a==b}
        result={'local_worker_only':True,'sales':fixture.store.scalar('SELECT COUNT(*) FROM sales'),
            'returns':fixture.store.scalar('SELECT COUNT(*) FROM returns'),'queued_after_lost_reply':waiting,
            'drained_rows':batches,'pending_after':fixture.sync.pending(),'restore_report':report,
            'compared':compared,'restored_stock':Shop(restored).available(fixture.pid),
            'source_stock':fixture.shop.available(fixture.pid)}
    finally: restored.close()
    pathlib.Path(sys.argv[1] if len(sys.argv)>1 else '/tmp/auditd-cloud-roundtrip.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
finally:
    fixture.doCleanups()
    CloudCopy.tearDownClass()
