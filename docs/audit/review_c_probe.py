"""Read-only review reproductions on disposable databases; observations, not acceptance tests.
Run: python docs/audit/review_c_probe.py
"""
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / 'tests'))
from helpers import ShopFixture, key
from server import guidebook


class Fixture(ShopFixture, unittest.TestCase):
    pass


def observe(name, fn, mode='shop'):
    f = Fixture()
    try:
        f.make_shop(mode)
        print(json.dumps({'case': name, 'observed': fn(f)}, ensure_ascii=False))
    finally:
        f.doCleanups()


def transformed_cost(f):
    s, o = f.shop, f.owner
    sup = s.add_party(o, 'supplier', 'Supplier')
    raw, cut = f.meat('RAW'), f.meat('CUT')
    receipt = s.receive(o, key(), {'supplier_id': sup, 'lines': [
        {'product_id': raw, 'qty': 1000, 'line_cost': 10000, 'location_id': f.prep, 'expires': '2026-11-05'}]})
    child = s.produce(o, key(), {'kind': 'cutting', 'inputs': [
        {'lot_id': receipt['lines'][0]['lot'], 'location_id': f.prep, 'qty': 1000}],
        'outputs': [{'product_id': cut, 'qty': 1000, 'location_id': f.fridge}]})['outputs'][0]['lot']
    adj = s.supplier_adjustment(o, key(), {'kind': 'invoice_correction', 'supplier_id': sup,
        'receipt_id': receipt['receipt'], 'amount': 1000, 'reference': 'FINAL'})
    return {'adjustment': adj, 'unsold_child_stock': s._balance(child, f.fridge),
            'sales_count': f.store.scalar('SELECT COUNT(*) FROM sales'), 'reconcile': s.reconcile()['ok']}


def thaw_then_shelf(f):
    s, o = f.shop, f.owner
    freezer = s.add_location(o, 'FZ', 'مجمد', 'Freezer', 'freezer', 0)
    raw = f.meat()
    parent = f.receive(raw, 1000, 10000, location=freezer)['lines'][0]['lot']
    s.update_settings(o, {'thaw_shelf_life_days': 2})
    child = s.thaw(o, key(), parent, freezer, f.prep, 1000)['lot']
    moved = s.transfer(o, key(), child, f.prep, f.shelf, 1000)
    return {'transfer_accepted': moved, 'shelf_stock': s._balance(child, f.shelf),
            'sellable_qty': s.available(raw)}


def ingredient_to_meat(f):
    s, o = f.shop, f.owner
    spice = s.add_product(o, {'code': 'SP', 'name_ar': 'تتبيلة بلا لحم', 'name_en': 'Meat-free marinade',
        'kind': 'ingredient', 'measure': 'g', 'price': 1000})
    raw = f.meat()
    lot = f.receive(spice, 1000, 1000, location=f.prep)['lines'][0]['lot']
    return s.produce(f.butcher, key(), {'kind': 'cutting', 'inputs': [
        {'lot_id': lot, 'location_id': f.prep, 'qty': 1000}], 'outputs': [
        {'product_id': raw, 'qty': 1000, 'location_id': f.fridge}],
        'packs': [{'net_g': 1000, 'meat_g': 1000}]})


def partial_credit(f):
    s, o = f.shop, f.owner
    sup = s.add_party(o, 'supplier', 'Supplier')
    p = f.grocery()
    r = s.receive(o, key(), {'supplier_id': sup, 'lines': [
        {'product_id': p, 'qty': 10, 'line_cost': 10000, 'location_id': f.shelf}]})
    ret = s.supplier_return(o, key(), sup, r['lines'][0]['lot'], f.shelf, 10, 'quality')['supplier_return']
    first = s.supplier_credit_note(o, key(), ret, 6000, 'C1')
    second = s.supplier_credit_note(o, key(), ret, 4000, 'C2')
    return {'first': first, 'second': second, 'return': s.supplier_open_returns(sup)}


def training_product(f):
    before = guidebook.live_states(f.store, f.owner['id'])
    f.grocery()
    return {'before': before, 'after_product_added': guidebook.live_states(f.store, f.owner['id']),
            'actual_product_adds': f.store.scalar("SELECT COUNT(*) FROM audit WHERE action='product.add'")}


if __name__ == '__main__':
    observe('C01_transformed_inventory_cost', transformed_cost)
    observe('C02_thawed_stock_to_shelf', thaw_then_shelf)
    observe('C03_marinade_becomes_raw_meat', ingredient_to_meat)
    observe('C04_cumulative_credit_difference', partial_credit)
    observe('C05_training_product_evidence', training_product, 'training')
