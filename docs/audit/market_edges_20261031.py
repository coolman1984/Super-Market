"""Synthetic audit cases on 3129b77. Not acceptance tests; show observations."""
import json,pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'tests'))
from helpers import ShopFixture,key
from server.core import ShopError
class F(ShopFixture,unittest.TestCase):pass
def run(n,fn):
 f=F();f.make_shop()
 try:print(json.dumps({'case':n,'observed':fn(f)},ensure_ascii=False))
 finally:f.doCleanups()
def reverse_after_refund(f):
 p=f.grocery(price=10000);f.receive(p,1,5000,expires=None);f.open_shift(f.manager,0)
 c=f.shop.add_party(f.owner,'customer','C',credit_limit=10000)
 s=f.shop.sell(f.manager,key(),{'lines':[{'product_id':p,'qty':1}],'customer_id':c,'payments':[{'method':'credit','amount':10000}]})
 eid=f.shop.customer_payment(f.manager,key(),c,10000)['entry']
 lid=f.shop.sale_detail(f.owner,s['sale'])['lines'][0]['id']
 f.shop.return_sale(f.manager,key(),{'sale_id':s['sale'],'reason':'quality','method':'original','lines':[{'sale_line_id':lid,'qty':1}]})
 f.shop.cash_move(f.manager,key(),'deposit',10000,'Funding another refund')
 f.shop.reverse_customer_payment(f.manager,key(),eid,'reverse already refunded payment')
 return {'customer_owes_after_full_return':f.shop.customer_balance(c),'cash_refunded_twice':20000}
def packed_input(f):
 p=f.meat();r=f.receive(p,1000,10000,f.fridge);o=f.meat('OUT');o2=f.meat('OUT2')
 a=f.shop.produce(f.owner,key(),{'kind':'cutting','inputs':[{'lot_id':r['lines'][0]['lot'],'location_id':f.fridge,'qty':1000}],
 'outputs':[{'product_id':o,'qty':1000,'location_id':f.fridge}],'packs':[{'net_g':500},{'net_g':500}]})
 lot=a['outputs'][0]['lot']
 f.shop.produce(f.owner,key(),{'kind':'cutting','inputs':[{'lot_id':lot,'location_id':f.fridge,'qty':1000}],
 'outputs':[{'product_id':o2,'qty':1000,'location_id':f.fridge}]})
 return {'old_lot_stock':f.shop._balance(lot,f.fridge)[0],'old_available_packs':f.shop._packed(lot,f.fridge),'reconcile':f.shop.reconcile()['ok']}
def false_meat_role(f):
 sauce=f.meat('SAUCE',kind='ingredient');out=f.meat('TRAY',kind='prepared');r=f.receive(sauce,1000,100,f.fridge)
 v=f.shop.save_recipe(f.owner,out,[{'product_id':sauce,'qty':1000,'role':'meat'}],1,'',0,target_pack_g=1000)
 a=f.shop.produce(f.butcher,key(),{'kind':'recipe','recipe_version_id':v['recipe_version'],
 'inputs':[{'lot_id':r['lines'][0]['lot'],'location_id':f.fridge,'qty':1000}],
 'outputs':[{'product_id':out,'qty':1000,'location_id':f.fridge}],'packs':[{'net_g':1000}]})
 return {'ingredient_kind':'ingredient','claimed_meat_g':a['outputs'][0]['packs'][0]['meat_g']}
def equal_total_prices(f):
 a=f.grocery('A',price=10000,barcodes=[]);b=f.grocery('B',price=20000,barcodes=[])
 f.receive(a,1,1000,expires=None);f.receive(b,1,1000,expires=None);f.open_shift()
 payload={'lines':[{'product_id':a,'qty':1},{'product_id':b,'qty':1}],'expected_total':30000}
 f.shop.set_price(f.owner,key(),a,20000);f.shop.set_price(f.owner,key(),b,10000)
 s=f.shop.sell(f.cash1,key(),payload)
 return {'shown_line_prices':[10000,20000],'charged_line_prices':[x['unit_price'] for x in s['lines']],'total_unchanged':s['total']}
def anonymous_reject(f):
 p=f.grocery(price=1000);f.receive(p,1,100,expires=None);f.open_shift(f.manager,0)
 s=f.shop.sell(f.manager,key(),{'lines':[{'product_id':p,'qty':1}],'payments':[{'method':'transfer','amount':1000}]})
 pay=f.store.scalar('SELECT id FROM payments');f.shop.confirm_payment(f.manager,key(),pay,'rejected')
 lid=f.shop.sale_detail(f.owner,s['sale'])['lines'][0]['id']
 try:f.shop.return_sale(f.manager,key(),{'sale_id':s['sale'],'reason':'wrong_item','method':'original','lines':[{'sale_line_id':lid,'qty':1,'restock':True}]})
 except ShopError as e:return {'goods_cannot_be_returned':e.code,'stock':f.shop.available(p)}
def migration(f):
 # Schema 1 historical cash collections have no credit_allocations. Model that
 # supported historical shape by writing original immutable rows through tx.
 p=f.grocery(price=10000);f.receive(p,1,1000,expires=None);f.open_shift(f.manager,0)
 c=f.shop.add_party(f.owner,'customer','C',credit_limit=10000)
 s=f.shop.sell(f.manager,key(),{'lines':[{'product_id':p,'qty':1}],'customer_id':c,'payments':[{'method':'credit','amount':10000}]})
 op=f.store.scalar('SELECT MAX(id) FROM ops')
 f.store.tx(lambda:f.store.db.execute("INSERT INTO customer_entries(customer_id,op_id,kind,amount,at) VALUES(?,?,'payment',-10000,'2026-10-30')",(c,op)))
 return {'customer_balance':f.shop.customer_balance(c),'sale_outstanding':f.shop._sale_outstanding(s['sale']),'cash_refund_capacity':f.shop._refund_caps(s['sale'])['cash']}
for n,fn in [('E01',reverse_after_refund),('E02',packed_input),('E03',false_meat_role),('E04',equal_total_prices),('E05',anonymous_reject),('E06',migration)]:run(n,fn)
