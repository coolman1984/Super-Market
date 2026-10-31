"""Review C acceptance: economic attribution, inherited storage and guide evidence."""
import json
import pathlib
import sqlite3
import tempfile
import unittest

from helpers import ShopFixture, key
from server import backup, core, guidebook, schema, training
from server.core import ShopError
from server.db import Store
from server.services import Shop


class ReviewC(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()
        self.sup = self.shop.add_party(self.owner, 'supplier', 'Supplier')
        self.raw = self.meat('RAW')
        self.cut = self.meat('CUT')

    def receipt(self, qty=1000, cost=10000, product=None, location=None):
        return self.shop.receive(self.owner, key(), {'supplier_id': self.sup, 'lines': [
            {'product_id': product or self.raw, 'qty': qty, 'line_cost': cost,
             'location_id': location or self.prep, 'expires': '2026-11-05'}]})

    def cut_lots(self, inputs, outputs=None, **extra):
        return self.shop.produce(self.owner, key(), {'kind': 'cutting', 'inputs': inputs,
            'outputs': outputs or [{'product_id': self.cut, 'qty': sum(x['qty'] for x in inputs), 'location_id': self.fridge}], **extra})

    def inp(self, lot, qty, loc=None):
        return {'lot_id': lot, 'location_id': loc or self.prep, 'qty': qty}

    def adjust(self, r, amount=1000):
        return self.shop.supplier_adjustment(self.owner, key(), {'kind': 'invoice_correction', 'supplier_id': self.sup,
            'receipt_id': r['receipt'], 'amount': amount, 'reference': key()})

    def sale(self, product, qty):
        if not self.shop._open_shift_of(self.manager, required=False):
            self.open_shift(self.manager)
        lines = [{'product_id': product, 'qty': qty}]
        q = self.shop.quote(self.manager, {'lines': lines})
        return self.shop.sell(self.manager, key(), {'lines': lines, 'quote_hash': q['quote_hash']})

    def test_unsold_cut_cost_follows_goods_and_reversal_is_exact(self):
        r = self.receipt()
        lot = self.cut_lots([self.inp(r['lines'][0]['lot'], 1000)])['outputs'][0]['lot']
        before = self.store.all('SELECT * FROM productions')
        a = self.adjust(r)
        self.assertEqual((a['to_stock'], a['to_cost_of_sales']), (1000, 0))
        self.assertEqual(self.shop._balance(lot, self.fridge), (1000, 11000))
        self.assertEqual(self.shop.day_report(self.owner)['cost_of_sales'], 0)
        self.adjust(r, -1000)
        self.assertEqual(self.shop._balance(lot, self.fridge), (1000, 10000))
        self.assertEqual(self.store.all('SELECT * FROM productions'), before)
        self.assertTrue(self.shop.reconcile()['ok'])

    def test_mixed_sources_multiple_outputs_two_stages_and_partial_sale(self):
        r1, r2 = self.receipt(cost=10000), self.receipt(cost=20000)
        outs = self.cut_lots([self.inp(r1['lines'][0]['lot'], 1000), self.inp(r2['lines'][0]['lot'], 1000)], [
            {'product_id': self.cut, 'qty': 1000, 'location_id': self.fridge},
            {'product_id': self.raw, 'qty': 1000, 'location_id': self.prep}])['outputs']
        third = self.meat('THIRD')
        self.cut_lots([self.inp(outs[1]['lot'], 1000)], [{'product_id': third, 'qty': 1000, 'location_id': self.fridge}])
        self.sale(self.cut, 500)
        a = self.adjust(r1)
        self.assertEqual((a['to_stock'], a['to_cost_of_sales']), (750, 250))
        self.assertEqual(self.shop.day_report(self.owner)['cost_adjustments'], 250)
        self.assertEqual(self.store.scalar('SELECT SUM(cost) FROM stock_balances'), 23250)

    def test_zero_cost_value_weights_are_recorded_not_inferred_from_zero_money(self):
        r = self.receipt(cost=0)
        self.cut_lots([self.inp(r['lines'][0]['lot'], 1000)], [
            {'product_id': self.cut, 'qty': 500, 'value_weight': 3, 'location_id': self.fridge},
            {'product_id': self.raw, 'qty': 500, 'value_weight': 1, 'location_id': self.prep}], cost_method='value')
        self.adjust(r, 1000)
        self.assertEqual(sorted(r['cost'] for r in self.store.all('SELECT cost FROM stock_balances WHERE qty>0')), [250, 750])

    def test_raw_price_correction_follows_unsold_recipe_and_packaging_once(self):
        r = self.receipt()
        spice = self.shop.add_product(self.owner, {'code': 'SP', 'name_ar': 'تتبيلة', 'name_en': 'Marinade', 'kind': 'ingredient', 'measure': 'g'})
        tray = self.shop.add_product(self.owner, {'code': 'TR', 'name_ar': 'صينية', 'name_en': 'Tray', 'kind': 'packaging', 'measure': 'unit'})
        out = self.meat('READY', kind='prepared')
        a = self.receipt(qty=400, cost=4000, product=spice)
        b = self.receipt(qty=1, cost=500, product=tray)
        recipe = self.shop.save_recipe(self.owner, out, [
            {'product_id': self.raw, 'qty': 600, 'role': 'meat'},
            {'product_id': spice, 'qty': 400, 'role': 'food'},
            {'product_id': tray, 'qty': 1, 'role': 'packaging'}], 2, '', 0, 1000)
        made = self.shop.produce(self.owner, key(), {'kind': 'recipe', 'recipe_version_id': recipe['recipe_version'], 'units': 1,
            'inputs': [self.inp(r['lines'][0]['lot'], 600), self.inp(a['lines'][0]['lot'], 400), self.inp(b['lines'][0]['lot'], 1)],
            'outputs': [{'product_id': out, 'qty': 1000, 'location_id': self.fridge}], 'packs': [{'net_g': 1000, 'meat_g': 600}]})
        self.adjust(r)
        self.assertEqual(self.shop._balance(made['outputs'][0]['lot'], self.fridge), (1000, 11100))
        self.assertEqual(self.store.scalar('SELECT SUM(cost) FROM stock_balances'), 15500)
        self.assertEqual(self.shop.day_report(self.owner)['cost_of_sales'], 0)

    def test_closed_day_snapshot_stays_fixed_while_next_day_stock_takes_correction(self):
        import datetime as dt
        from helpers import NOW
        r = self.receipt()
        lot = self.cut_lots([self.inp(r['lines'][0]['lot'], 1000)])['outputs'][0]['lot']
        self.shop.close_day(self.owner, key(), '2026-10-30')
        snapshot = self.store.scalar("SELECT snapshot FROM day_closes WHERE day='2026-10-30'")
        core.set_clock(NOW + dt.timedelta(days=1))
        self.adjust(r)
        self.assertEqual(self.store.scalar("SELECT snapshot FROM day_closes WHERE day='2026-10-30'"), snapshot)
        self.assertEqual(self.shop._balance(lot, self.fridge)[1], 11000)
        self.assertEqual(self.store.scalar('SELECT COUNT(*) FROM day_corrections'), 1)

    def test_losses_and_supplier_returns_do_not_masquerade_as_sales(self):
        r = self.receipt()
        lot = r['lines'][0]['lot']
        self.shop.waste(self.owner, key(), lot, self.prep, 200, 'damaged')
        self.shop.supplier_return(self.owner, key(), self.sup, lot, self.prep, 300, 'quality')
        self.adjust(r)
        report = self.shop.day_report(self.owner)
        self.assertEqual(report['cost_adjustment_parts'], {'loss': 200, 'stock': 500, 'supplier_return': 300})
        self.assertEqual((report['cost_of_sales'], report['losses']), (0, 2200))

    def test_production_abnormal_loss_and_natural_loss_are_distinct(self):
        r = self.receipt()
        self.cut_lots([self.inp(r['lines'][0]['lot'], 1000)], [
            {'product_id': self.cut, 'qty': 800, 'location_id': self.fridge}], natural_loss_g=100, abnormal_loss_g=100)
        self.adjust(r)
        report = self.shop.day_report(self.owner)
        self.assertEqual(report['cost_adjustment_parts'], {'loss': 100, 'stock': 900})
        self.assertEqual(report['cost_of_sales'], 0)

    def test_thaw_and_transfer_retain_cost_exposure(self):
        freezer = self.shop.add_location(self.owner, 'FZ', 'مجمد', 'Freezer', 'freezer', 0)
        r = self.receipt(location=freezer)
        self.shop.update_settings(self.owner, {'thaw_shelf_life_days': 2})
        child = self.shop.thaw(self.owner, key(), r['lines'][0]['lot'], freezer, self.prep, 400)['lot']
        self.shop.transfer(self.owner, key(), child, self.prep, self.fridge, 400)
        self.adjust(r)
        self.assertEqual(self.shop._balance(child, self.fridge), (400, 4400))
        self.assertEqual(self.shop.day_report(self.owner)['cost_of_sales'], 0)

    def test_correction_after_sale_is_carried_back_by_later_returns(self):
        p = self.grocery()
        r = self.receipt(qty=10, product=p, location=self.shelf)
        sale = self.sale(p, 10)
        self.adjust(r)
        for qty in (3, 7):
            self.shop.return_sale(self.manager, key(), {'sale_id': sale['sale'], 'reason': 'quality', 'method': 'cash',
                'lines': [{'sale_line_id': sale['lines'][0]['line'], 'qty': qty, 'restock': True}]})
        self.assertEqual(self.store.scalar('SELECT SUM(cost) FROM stock_balances'), 11000)
        self.assertEqual(self.shop.day_report(self.owner)['cost_of_sales'], 0)

    def test_old_returns_then_new_adjustment_only_allocate_remaining_sale(self):
        p = self.grocery()
        r = self.receipt(qty=10, product=p, location=self.shelf)
        sale = self.sale(p, 10)
        def ret(n):
            return self.shop.return_sale(self.manager, key(), {'sale_id': sale['sale'], 'reason': 'quality', 'method': 'cash',
                'lines': [{'sale_line_id': sale['lines'][0]['line'], 'qty': n, 'restock': True}]})
        ret(2)
        self.adjust(r, 1001)
        ret(8)
        self.assertEqual(self.store.scalar('SELECT SUM(cost) FROM stock_balances'), 11001)
        self.assertEqual(self.shop.day_report(self.owner)['cost_of_sales'], 0)

    def test_excess_negative_correction_after_sale_cannot_create_negative_return_stock(self):
        p = self.grocery()
        r = self.receipt(qty=10, cost=1000, product=p, location=self.shelf)
        sale = self.sale(p, 10)
        a = self.adjust(r, -1500)
        self.assertEqual((a['to_cost_of_sales'], a['to_variance']), (-1000, -500))
        self.shop.return_sale(self.manager, key(), {'sale_id': sale['sale'], 'reason': 'wrong_item', 'method': 'cash',
            'lines': [{'sale_line_id': sale['lines'][0]['line'], 'qty': 10, 'restock': True}]})
        self.assertEqual(self.store.scalar('SELECT SUM(cost) FROM stock_balances'), 0)
        self.assertEqual(self.shop.day_report(self.owner)['cost_of_sales'], 0)

    def test_thawed_stock_cannot_be_moved_or_produced_onto_shelf(self):
        freezer = self.shop.add_location(self.owner, 'FZ', 'مجمد', 'Freezer', 'freezer', 0)
        r = self.receipt(location=freezer)
        self.shop.update_settings(self.owner, {'thaw_shelf_life_days': 2})
        lot = self.shop.thaw(self.owner, key(), r['lines'][0]['lot'], freezer, self.prep, 1000)['lot']
        with self.assertRaises(ShopError) as e:
            self.shop.transfer(self.owner, key(), lot, self.prep, self.shelf, 1000)
        self.assertEqual(e.exception.code, 'thaw_needs_fridge')
        with self.assertRaises(ShopError):
            self.cut_lots([self.inp(lot, 1000)], [{'product_id': self.cut, 'qty': 1000, 'location_id': self.shelf}])
        self.assertEqual(self.shop._balance(lot, self.prep)[0], 1000)

    def test_cutting_cannot_relabel_ingredients_as_meat(self):
        spice = self.shop.add_product(self.owner, {'code': 'SP', 'name_ar': 'تتبيلة', 'name_en': 'Spice', 'kind': 'ingredient', 'measure': 'g'})
        r = self.receipt(product=spice)
        with self.assertRaises(ShopError) as e:
            self.cut_lots([self.inp(r['lines'][0]['lot'], 1000)], packs=[{'net_g': 1000, 'meat_g': 1000}])
        self.assertEqual(e.exception.code, 'cutting_needs_raw_meat')
        self.assertEqual(self.store.scalar('SELECT COUNT(*) FROM productions'), 0)

    def test_positive_count_and_release_cannot_bypass_thaw_storage(self):
        freezer = self.shop.add_location(self.owner, 'FZ', 'مجمد', 'Freezer', 'freezer', 0)
        r = self.receipt(location=freezer)
        self.shop.update_settings(self.owner, {'thaw_shelf_life_days': 2})
        lot = self.shop.thaw(self.owner, key(), r['lines'][0]['lot'], freezer, self.prep, 1000)['lot']
        # Model a legacy placement made before this fix, using append-only entries.
        def legacy(op):
            cost = self.shop._consume(op, 'transfer_out', lot, self.prep, 1000, check=False)
            self.shop._entry(op, 'transfer_in', lot, self.shelf, 1000, cost)
            return {}
        self.store.command('legacy_fixture', key(), [], self.owner, legacy)
        self.shop.set_lot_status(self.owner, key(), lot, 'held', 'Review old placement')
        with self.assertRaises(ShopError):
            self.shop.set_lot_status(self.owner, key(), lot, 'released', 'Cannot release onto shelf')
        c = self.shop.open_count(self.owner, self.shelf)
        self.shop.record_count(self.counter, c['count'], lot, 1001)
        with self.assertRaises(ShopError):
            self.shop.approve_count(self.owner, key(), c['count'])
        self.assertEqual(self.shop._balance(lot, self.shelf)[0], 1000)

    def test_cost_details_do_not_leak_to_receiving_role(self):
        r = self.receipt()
        rid = self.shop.supplier_return(self.owner, key(), self.sup, r['lines'][0]['lot'], self.prep, 1000, 'quality')['supplier_return']
        self.shop.close_supplier_return(self.owner, key(), rid, 'No credit accepted')
        actor = dict(self.owner, role='receiver')
        row = self.shop.supplier_statement(actor, self.sup)['open_returns'][0]
        self.assertNotIn('cost', row)
        self.assertNotIn('difference_from_cost', row)
        self.assertNotIn('cost_difference', row['settlement'])

    def test_credit_difference_is_cumulative_and_short_settlement_is_audited(self):
        r = self.receipt()
        rid = self.shop.supplier_return(self.owner, key(), self.sup, r['lines'][0]['lot'], self.prep, 1000, 'quality')['supplier_return']
        self.shop.supplier_credit_note(self.owner, key(), rid, 6000, 'ONE')
        c = self.shop.supplier_credit_note(self.owner, key(), rid, 4000, 'TWO')
        self.assertEqual((c['credit_note_amount'], c['credited_total'], c['difference_from_cost']), (4000, 10000, 0))
        close_key = key()
        c = self.shop.close_supplier_return(self.owner, close_key, rid, 'Final agreed settlement')
        replay = self.shop.close_supplier_return(self.owner, close_key, rid, 'Final agreed settlement')
        self.assertTrue(replay.pop('replayed'))
        self.assertEqual(c, replay)
        with self.assertRaises(ShopError):
            self.shop.supplier_credit_note(self.owner, key(), rid, 1, 'THREE')
        self.assertEqual(self.shop.supplier_balance(self.sup), 0)

    def test_short_settlement_closes_claim_without_changing_debt(self):
        r = self.receipt()
        rid = self.shop.supplier_return(self.owner, key(), self.sup, r['lines'][0]['lot'], self.prep, 1000, 'quality')['supplier_return']
        self.shop.supplier_credit_note(self.owner, key(), rid, 6000, 'PART')
        with self.assertRaises(ShopError):
            self.shop.close_supplier_return(self.cash1, key(), rid, 'No authority')
        self.shop.close_supplier_return(self.owner, key(), rid, 'Supplier rejected the remaining claim')
        self.assertEqual(self.shop.supplier_balance(self.sup), 4000)
        row = self.shop.supplier_open_returns(self.sup)[0]
        self.assertFalse(row['open'])
        self.assertEqual(row['settlement']['cost_difference'], -4000)

    def test_training_seed_is_fixed_across_user_work_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = Store(pathlib.Path(tmp)/'data', mode='training')
            s = Shop(st)
            training.seed(s)
            owner = s.auth.public(st.one("SELECT * FROM users WHERE username='owner'"))
            self.assertNotIn('product.added', guidebook.live_states(st, owner['id']))
            s.add_product(owner, {'code': 'NEW', 'name_ar': 'جديد', 'name_en': 'New', 'kind': 'grocery', 'measure': 'unit'})
            self.assertIn('product.added', guidebook.live_states(st, owner['id']))
            st.close()
            st = Store(pathlib.Path(tmp)/'data', mode='training')
            try:
                self.assertIn('product.added', guidebook.live_states(st, owner['id']))
            finally:
                st.close()

    def test_upgrade_from_v7_preserves_history_and_verified_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            from server.db import DB_NAME
            path = pathlib.Path(tmp)/'old'/DB_NAME
            path.parent.mkdir()
            conn = sqlite3.connect(path)
            conn.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT,applied_at TEXT)')
            for version, name, fn in schema.MIGRATIONS[:7]:
                fn(conn)
                conn.execute('INSERT INTO schema_migrations VALUES(?,?,?)', (version, name, core.now_iso()))
            conn.execute("INSERT INTO settings VALUES('mode','shop')")
            conn.commit()
            conn.close()
            st = Store(path.parent)
            try:
                self.assertEqual(st.schema_version(), schema.SCHEMA_VERSION)
                self.assertTrue(list((path.parent/'pre-upgrade').glob('*.db')))
                made = backup.create_backup(st, pathlib.Path(tmp)/'backups')
                backup.restore_backup(made['folder'], pathlib.Path(tmp)/'restored')
                restored = Store(pathlib.Path(tmp)/'restored')
                self.assertEqual(restored.schema_version(), schema.SCHEMA_VERSION)
                restored.close()
            finally:
                st.close()
