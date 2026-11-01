"""Regression evidence for the November audit, integration and safe hardware setup."""
import datetime as dt
import unittest
from unittest.mock import patch

from helpers import ShopFixture, key, NOW
from server import core, scale, schema
from server.core import ShopError
from server.db import Store
from server.hardware import profiles


class ReleaseFixes(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()

    def test_meat_receipt_on_shelf_is_atomic_refusal(self):
        p = self.meat()
        before = self.store.scalar('SELECT COUNT(*) FROM receipts')
        with self.assertRaises(ShopError) as err:
            self.receive(p, 1000, 10000, location=self.shelf)
        self.assertEqual(err.exception.code, 'meat_needs_cold_storage')
        self.assertEqual(self.store.scalar('SELECT COUNT(*) FROM receipts'), before)
        self.assertEqual(self.shop.available(p), 0)

    def test_meat_transfer_cannot_bypass_storage_rule(self):
        p = self.meat()
        lot = self.receive(p, 1000, 10000, location=self.fridge)['lines'][0]['lot']
        with self.assertRaises(ShopError):
            self.shop.transfer(self.owner, key(), lot, self.fridge, self.shelf, 500)
        self.assertEqual(self.shop._balance(lot, self.fridge)[0], 1000)
        self.assertEqual(self.shop._balance(lot, self.shelf)[0], 0)

    def test_old_meat_on_shelf_is_not_sellable_and_is_flagged(self):
        p = self.meat()
        with patch.object(self.shop, '_storage_destination'):
            self.receive(p, 1000, 10000, location=self.shelf)
        self.assertEqual(self.shop.available(p), 0)
        self.assertEqual(self.shop.stock_report(self.owner)[0]['state'], 'storage_unsuitable')
        self.open_shift()
        with self.assertRaises(ShopError):
            self.shop.sell(self.cash1, key(), {'lines': [{'product_id': p, 'qty': 500}]})

    def test_prepared_output_on_shelf_rolls_back_inputs(self):
        p = self.meat()
        out = self.meat('OUT', kind='prepared')
        lot = self.receive(p, 1000, 10000, location=self.fridge)['lines'][0]['lot']
        recipe = self.shop.save_recipe(self.owner, out, [{'product_id': p, 'qty': 1000, 'role': 'meat'}], 2, '', 0, 1000)['recipe_version']
        payload = {'kind': 'recipe', 'recipe_version_id': recipe, 'inputs': [{'lot_id': lot, 'location_id': self.fridge, 'qty': 1000}],
                   'outputs': [{'product_id': out, 'qty': 1000, 'location_id': self.shelf, 'expires': '2026-11-01'}],
                   'normal_loss_g': 0, 'abnormal_loss_g': 0}
        with self.assertRaises(ShopError) as err:
            self.shop.produce(self.owner, key(), payload)
        self.assertEqual(err.exception.code, 'meat_needs_cold_storage')
        self.assertEqual(self.shop._balance(lot, self.fridge)[0], 1000)
        self.assertEqual(self.store.scalar('SELECT COUNT(*) FROM productions'), 0)

    def test_promotion_split_rows_and_free_piece_return(self):
        p = self.grocery()
        self.receive(p, 10, 1000)
        self.shop.add_promotion(self.owner, key(), p, 2, 1, '2026-10-30')
        combined = {'lines': [{'product_id': p, 'qty': 3}]}
        split = {'lines': [{'product_id': p, 'qty': 1} for _ in range(3)]}
        self.assertEqual(self.shop.quote(self.owner, combined)['total'], 3000)
        self.assertEqual(self.shop.quote(self.owner, split)['total'], 3000)
        self.assertEqual(self.shop.quote(self.owner, combined)['quote_hash'], self.shop.quote(self.owner, split)['quote_hash'])
        self.open_shift(self.manager)
        sale = self.shop.sell(self.manager, key(), split)
        line = self.shop.sale_detail(self.manager, sale['sale'])['lines']
        self.assertEqual(len(line), 1)
        first = self.shop.return_sale(self.manager, key(), {'sale_id': sale['sale'], 'reason': 'quality', 'method': 'cash',
                                      'lines': [{'sale_line_id': line[0]['id'], 'qty': 1, 'restock': True}]})
        self.assertEqual(first['amount'], 0)
        rest = self.shop.return_sale(self.manager, key(), {'sale_id': sale['sale'], 'reason': 'quality', 'method': 'cash',
                                     'lines': [{'sale_line_id': line[0]['id'], 'qty': 2, 'restock': True}]})
        self.assertEqual(rest['amount'], 3000)
        self.assertTrue(self.shop.reconcile()['ok'])

    def _sale(self, method='credit'):
        p = self.grocery()
        self.receive(p, 10, 1000)
        customer = self.shop.add_party(self.owner, 'customer', 'عميل الاختبار', credit_limit=100000)
        self.open_shift(self.manager, 0)
        sale = self.shop.sell(self.manager, key(), {'lines': [{'product_id': p, 'qty': 1}], 'customer_id': customer,
                    'payments': [{'method': method, 'amount': 1500}]})
        return customer, sale

    def _next_day(self, day):
        shift = self.store.one('SELECT * FROM shifts WHERE closed_at IS NULL AND user_id=?', (self.manager['id'],))
        if shift:
            self.shop.close_shift(self.manager, key(), self.shop.shift_expected(shift['id']))
        core.set_clock(NOW + dt.timedelta(days=day))
        self.open_shift(self.manager, 0)

    def test_prior_debt_is_collected_on_receipt_day_and_period(self):
        customer, _ = self._sale()
        self._next_day(1)
        self.shop.customer_payment(self.manager, key(), customer, 1500)
        expected = [{'method': 'cash', 'state': 'settled', 'amount': 1500}]
        self.assertEqual(self.shop.day_report(self.owner, '2026-10-31')['collections'], expected)
        self.assertEqual(self.shop.period_report(self.owner, '2026-10-31', '2026-10-31')['collections'], expected)
        self.assertEqual(self.shop.day_report(self.owner, '2026-10-30')['collections'], [])

    def test_pending_confirmation_is_collection_on_confirmation_day_only(self):
        _, sale = self._sale('transfer')
        payment = self.store.scalar('SELECT id FROM payments WHERE sale_id=?', (sale['sale'],))
        self.assertEqual(self.shop.day_report(self.owner)['collections'], [])
        self._next_day(1)
        self.shop.confirm_payment(self.owner, key(), payment, 'confirmed')
        self.assertEqual(self.shop.day_report(self.owner, '2026-10-30')['collections'], [])
        self.assertEqual(self.shop.day_report(self.owner)['collections'], [{'method': 'transfer', 'state': 'settled', 'amount': 1500}])

    def test_collection_reversal_posts_negative_on_its_own_day(self):
        customer, _ = self._sale()
        entry = self.shop.customer_payment(self.manager, key(), customer, 1500)['entry']
        self._next_day(1)
        # The reversal needs sufficient drawer cash but is not a new collection.
        self.shop.cash_move(self.manager, key(), 'deposit', 1500, 'test')
        self.shop.reverse_customer_payment(self.manager, key(), entry, 'test')
        self.assertEqual(self.shop.day_report(self.owner)['collections'], [{'method': 'cash', 'state': 'settled', 'amount': -1500}])
        self.assertEqual(self.shop.day_report(self.owner, '2026-10-30')['collections'][0]['amount'], 1500)

    def test_old_debt_migration_number_is_repaired_without_losing_rows(self):
        path = self.folder / 'old-debt'
        old = Store(path, upto=9)
        old.db.execute('ALTER TABLE payments ADD COLUMN shift_id INTEGER REFERENCES shifts(id)')
        old.db.execute("INSERT INTO schema_migrations VALUES(10, 'old debt branch', '2026-10-30')")
        old.db.execute("INSERT INTO settings VALUES('keep_this', 'yes')")
        old.close()
        new = Store(path)
        try:
            self.assertEqual(new.schema_version(), schema.SCHEMA_VERSION)
            self.assertEqual(new.scalar("SELECT value FROM settings WHERE key='keep_this'"), 'yes')
            self.assertIn('pin_hash', {r[1] for r in new.db.execute('PRAGMA table_info(users)')})
            self.assertIn('shift_id', {r[1] for r in new.db.execute('PRAGMA table_info(payments)')})
            self.assertIn('shift_id', {r[1] for r in new.db.execute('PRAGMA table_info(payment_states)')})
        finally:
            new.close()


class HardwareSafety(unittest.TestCase):
    def test_catalog_is_unique_and_does_not_claim_physical_verification(self):
        rows = profiles()
        self.assertEqual(len({r['id'] for r in rows}), len(rows))
        self.assertTrue(all(not r['hardware_verified'] for r in rows))
        self.assertEqual({r['family'] for r in rows}, {'scanner', 'label', 'live'})

    def test_multiple_numbers_are_not_guessed_as_weight(self):
        self.assertIsNone(scale.parse_weight('1.250 kg 450.00 562.50'))
        self.assertIsNone(scale.parse_weight('1.250 kg 0.050 kg'))
        self.assertEqual(scale.parse_weight('ST,GS,+1.250kg'), {'grams': 1250, 'stable': True})

    def test_disconnect_and_garbage_invalidate_previous_reading(self):
        r = scale.ScaleReader({'id': 'audit'}, lambda cfg: None)
        r.status = 'connected'
        r.feed(['1.250 kg'])
        self.assertEqual(r.reading()['grams'], 1250)
        r.feed(['unrecognised frame'])
        self.assertIsNone(r.reading()['grams'])
        r.feed(['1.250 kg'])
        r.status = 'disconnected'
        self.assertIsNone(r.reading()['grams'])
