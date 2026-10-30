"""Run from supermarket: python -m unittest discover -s tests -v."""

import concurrent.futures
import json
import pathlib
import sqlite3
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from server.domain import Conflict, DomainError, Ledger


TODAY = "2026-10-30"


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger()
        self.addCleanup(self.ledger.close)

    def receive(self, batch="raw", qty=1000, cost=101, product="meat", **kwargs):
        kwargs.setdefault("expires", TODAY if kwargs.get("unit", "g") == "g" else None)
        return self.ledger.receipt("receive-" + batch, product, batch, "shop", qty, cost, **kwargs)

    def output(self, batch="out", qty=900, product="mince", expires=TODAY):
        return dict(batch=batch, product=product, location="shop", qty=qty, expires=expires)

    def test_receipt_and_exact_replay(self):
        first = self.receive()
        self.assertEqual(first, self.receive())
        self.assertEqual(self.ledger.balance("raw", "shop"), [1000, 101])
        self.assertEqual(self.ledger.balance("raw", "elsewhere"), [0, 0])

    def test_malformed_production_payload_is_domain_error_and_key_is_reusable(self):
        self.receive(qty=1000)
        before = list(self.ledger.db.iterdump())
        valid_input = [["raw", "shop", 1000]]
        out = self.output(qty=1000)
        cases = [
            (None, [out]),
            (["raw"], [out]),
            ([["raw", "shop"]], [out]),
            ([[3, "shop", 1000]], [out]),
            (valid_input, [None]),
            (valid_input, [{k: v for k, v in out.items() if k != "qty"}]),
            (valid_input, [dict(out, batch=3)]),
            (valid_input, [dict(out, product=[])]),
            (valid_input, [dict(out, unexpected=True)]),
        ]
        for inputs, outputs in cases:
            with self.subTest(inputs=inputs, outputs=outputs), self.assertRaises(DomainError):
                self.ledger.produce("retry", inputs, outputs, 0, TODAY)
            self.assertEqual(list(self.ledger.db.iterdump()), before)
        self.ledger.produce("retry", valid_input, [out], 0, TODAY)
        self.assertEqual(self.ledger.balance("out", "shop"), [1000, 101])

    def test_idempotency_conflicts_across_payload_and_operation_kind(self):
        self.receive()
        with self.assertRaises(Conflict):
            self.receive(cost=102)
        with self.assertRaises(Conflict):
            self.ledger.sale("receive-raw", "meat", "shop", 1, TODAY)
        self.assertEqual(self.ledger.balance("raw", "shop"), [1000, 101])

    def test_integer_and_positive_quantity_validation(self):
        for qty in (0, -1, 1.5, True, "1"):
            with self.subTest(qty=qty), self.assertRaises(DomainError):
                self.receive(qty=qty)
        for cost in (-1, 1.5, True):
            with self.subTest(cost=cost), self.assertRaises(DomainError):
                self.receive(cost=cost)
        self.receive(cost=0)
        for qty in (-1, 0, True):
            with self.assertRaises(DomainError):
                self.ledger.sale("invalid", "meat", "shop", qty, TODAY)

    def test_product_unit_and_batch_identity_are_fixed(self):
        self.receive()
        with self.assertRaises(DomainError):
            self.receive("other", product="meat", unit="unit")
        with self.assertRaises(DomainError):
            self.ledger.receipt("new-key", "meat", "raw", "other", 1, 1)
        self.assertEqual(self.ledger.balance("other", "shop"), [0, 0])

    def test_sqlite_integer_bounds_and_production_cost_overflow_rollback(self):
        maximum = 2**63 - 1
        for values in ({"qty": maximum + 1}, {"cost": maximum + 1}):
            with self.assertRaises(DomainError):
                self.receive(**values)
        self.receive("a", qty=1, cost=maximum)
        self.receive("b", qty=1, cost=1)
        before = list(self.ledger.db.iterdump())
        with self.assertRaises(DomainError):
            self.ledger.produce("make", [["a", "shop", 1], ["b", "shop", 1]],
                                [self.output(qty=2)], 0, TODAY)
        self.assertEqual(list(self.ledger.db.iterdump()), before)
        self.assertEqual(self.ledger.balance("a", "shop"), [1, maximum])

    def test_dates_and_units_are_validated(self):
        for expires in ("2026-02-30", "20261030", 123):
            with self.assertRaises(DomainError):
                self.receive(expires=expires)
        with self.assertRaises(DomainError):
            self.receive(unit="kg")
        self.receive()
        with self.assertRaises(DomainError):
            self.ledger.sale("sale", "meat", "shop", 1, "yesterday")

    def test_fefo_tie_break_and_undated_last(self):
        self.receive("undated", qty=10, cost=10, expires=None)
        self.receive("b", qty=10, cost=20, expires=TODAY)
        self.receive("a", qty=10, cost=30, expires=TODAY)
        self.receive("later", qty=10, cost=40, expires="2026-11-01")
        parts = self.ledger.sale("sale", "meat", "shop", 35, TODAY)
        self.assertEqual([p["batch"] for p in parts], ["a", "b", "later", "undated"])
        self.assertEqual(sum(p["cost"] for p in parts), 95)
        self.assertEqual(parts, self.ledger.sale("sale", "meat", "shop", 35, TODAY))

    def test_expired_and_other_locations_never_fill_sale(self):
        self.receive("expired", expires="2026-10-29")
        self.ledger.receipt("elsewhere", "meat", "remote", "warehouse", 1000, 10)
        with self.assertRaises(DomainError):
            self.ledger.sale("sale", "meat", "shop", 1, TODAY)
        self.assertEqual(self.ledger.balance("expired", "shop"), [1000, 101])

    def test_short_sale_rolls_back_all_parts_and_key_is_retryable(self):
        self.receive(qty=3, cost=10)
        with self.assertRaises(DomainError):
            self.ledger.sale("sale", "meat", "shop", 4, TODAY)
        self.assertEqual(self.ledger.balance("raw", "shop"), [3, 10])
        self.receive("extra", qty=1, cost=2)
        parts = self.ledger.sale("sale", "meat", "shop", 4, TODAY)
        self.assertEqual(sum(p["cost"] for p in parts), 12)

    def test_partial_consumption_preserves_final_cost_remainder(self):
        self.receive(qty=3, cost=10)
        costs = [self.ledger.sale(str(i), "meat", "shop", 1, TODAY)[0]["cost"]
                 for i in range(3)]
        self.assertEqual(costs, [3, 3, 4])
        self.assertEqual(self.ledger.balance("raw", "shop"), [0, 0])

    def test_production_mass_packaging_cost_and_full_genealogy(self):
        self.receive("r1", qty=600, cost=61)
        self.receive("r2", qty=400, cost=41)
        self.receive("pack", qty=4, cost=7, product="bags", unit="unit")
        inputs = [["r1", "shop", 600], ["r2", "shop", 400], ["pack", "shop", 4]]
        outputs = [self.output("z", 450), self.output("a", 450)]
        result = self.ledger.produce("make", inputs, outputs, 100, TODAY)
        self.assertEqual(result, [{"batch": "a", "qty": 450, "cost": 55},
                                  {"batch": "z", "qty": 450, "cost": 54}])
        self.assertEqual(result, self.ledger.produce("make", inputs, outputs, 100, TODAY))
        for parent in ("r1", "r2", "pack"):
            self.assertEqual(self.ledger.balance(parent, "shop"), [0, 0])
            self.assertEqual(self.ledger.descendants(parent), sorted([parent, "a", "z"]))
        payload = json.loads(self.ledger.db.execute(
            "SELECT payload FROM operations WHERE key='make'").fetchone()[0])
        self.assertEqual(payload[1][2], 100)

    def test_mass_mismatch_and_bad_loss_rollback_consumption(self):
        self.receive()
        for loss in (99, -1, True):
            with self.assertRaises(DomainError):
                self.ledger.produce("make", [["raw", "shop", 1000]],
                                    [self.output()], loss, TODAY)
            self.assertEqual(self.ledger.balance("raw", "shop"), [1000, 101])
        self.assertEqual(self.ledger.balance("out", "shop"), [0, 0])

    def assert_expiry_rejection_rolls_back(self, inputs, outputs, message):
        before = list(self.ledger.db.iterdump())
        with self.assertRaisesRegex(DomainError, message):
            self.ledger.produce("make", inputs, outputs, 100, TODAY)
        self.assertEqual(list(self.ledger.db.iterdump()), before)
        self.assertFalse(self.ledger.db.in_transaction)

    def test_production_requires_date_on_every_gram_input(self):
        self.receive("dated", qty=600, expires="2026-11-02")
        self.receive("undated", qty=400, expires=None)
        # The first portion is consumed before the undated input is rejected.
        self.assert_expiry_rejection_rolls_back(
            [["dated", "shop", 600], ["undated", "shop", 400]],
            [self.output()], "food input expiry required")
        self.receive("replacement", qty=400, expires="2026-11-01")
        self.ledger.produce("make", [["dated", "shop", 600], ["replacement", "shop", 400]],
                            [self.output()], 100, TODAY)

    def test_production_requires_output_expiry(self):
        self.receive()
        for output in (self.output(expires=None), self.output()):
            if output["expires"] is not None:
                del output["expires"]
            self.assert_expiry_rejection_rolls_back(
                [["raw", "shop", 1000]], [output],
                "expected ISO date" if "expires" in output else "output requires.*expiry")
        self.ledger.produce("make", [["raw", "shop", 1000]], [self.output()], 100, TODAY)

    def test_production_rejects_expired_output(self):
        self.receive()
        self.assert_expiry_rejection_rolls_back(
            [["raw", "shop", 1000]], [self.output(expires="2026-10-29")],
            "output expiry outside food-input bounds")
        self.ledger.produce("make", [["raw", "shop", 1000]], [self.output()], 100, TODAY)

    def test_production_rejects_any_output_later_than_earliest_food_input(self):
        self.receive("later", qty=600, expires="2026-11-05")
        self.receive("earlier", qty=400, expires="2026-11-01")
        self.receive("pack", qty=2, cost=7, product="bags", unit="unit")
        inputs = [["later", "shop", 600], ["earlier", "shop", 400], ["pack", "shop", 2]]
        outputs = [self.output("a", 450), self.output("z", 450, expires="2026-11-02")]
        self.assert_expiry_rejection_rolls_back(inputs, outputs, "output expiry outside food-input bounds")
        # Both inclusive boundaries are valid; undated packaging imposes no bound.
        outputs[1]["expires"] = "2026-11-01"
        self.ledger.produce("make", inputs, outputs, 100, TODAY)
        for output in outputs:
            self.assertEqual(self.ledger._batch(output["batch"])["expires"], output["expires"])

    def test_failed_late_output_rolls_back_products_batches_edges_and_key(self):
        self.receive()
        bad = [self.output("same", 450), self.output("same", 450)]
        with self.assertRaises(DomainError):
            self.ledger.produce("make", [["raw", "shop", 1000]], bad, 100, TODAY)
        self.assertEqual(self.ledger.descendants("raw"), ["raw"])
        self.assertEqual(self.ledger.balance("raw", "shop"), [1000, 101])
        self.assertIsNone(self.ledger.db.execute("SELECT 1 FROM products WHERE id='mince'").fetchone())
        self.ledger.produce("make", [["raw", "shop", 1000]], [self.output()], 100, TODAY)

    def test_duplicate_input_and_expired_production_are_rejected(self):
        self.receive()
        with self.assertRaises(DomainError):
            self.ledger.produce("make", [["raw", "shop", 500]] * 2, [self.output()], 100, TODAY)
        self.assertEqual(self.ledger.balance("raw", "shop"), [1000, 101])
        self.receive("old", expires="2026-10-29")
        with self.assertRaises(DomainError):
            self.ledger.produce("make", [["old", "shop", 1000]], [self.output()], 100, TODAY)

    def test_returns_quarantine_sale_cap_and_actual_cost(self):
        self.receive(qty=3, cost=10)
        self.ledger.sale("sold", "meat", "shop", 3, TODAY)
        costs = []
        for i in range(3):
            result = self.ledger.customer_return("ret" + str(i), "sold", "raw", "q" + str(i), "shop", 1)
            costs.append(result["cost"])
        self.assertEqual(costs, [3, 3, 4])
        self.assertEqual(self.ledger.customer_return("ret0", "sold", "raw", "q0", "shop", 1)["cost"], 3)
        with self.assertRaises(DomainError):
            self.ledger.customer_return("excess", "sold", "raw", "q3", "shop", 1)
        with self.assertRaises(DomainError):
            self.ledger.sale("resell", "meat", "shop", 1, TODAY)
        with self.assertRaises(DomainError):
            self.ledger.produce("reuse", [["q0", "shop", 1]], [self.output(qty=1)], 0, TODAY)
        self.assertEqual(self.ledger.descendants("raw"), ["q0", "q1", "q2", "raw"])

    def test_return_requires_real_sale_and_source(self):
        self.receive()
        for sale in ("missing", "receive-raw"):
            with self.assertRaises(DomainError):
                self.ledger.customer_return("ret", sale, "raw", "q", "shop", 1)
        self.ledger.sale("sold", "meat", "shop", 1, TODAY)
        with self.assertRaises(DomainError):
            self.ledger.customer_return("ret", "sold", "missing", "q", "shop", 1)

    def test_transitive_recall_holds_all_descendants(self):
        self.receive()
        self.ledger.produce("make1", [["raw", "shop", 900]], [self.output("first", 800)], 100, TODAY)
        self.ledger.produce("make2", [["first", "shop", 700]], [self.output("second", 600)], 100, TODAY)
        affected = self.ledger.recall("recall", "raw")
        self.assertEqual(affected, ["first", "raw", "second"])
        self.assertEqual(affected, self.ledger.recall("recall", "raw"))
        for product in ("meat", "mince"):
            with self.assertRaises(DomainError):
                self.ledger.sale("blocked-" + product, product, "shop", 1, TODAY)
        with self.assertRaises(DomainError):
            self.ledger.produce("blocked", [["raw", "shop", 1]], [self.output("third", 1)], 0, TODAY)

    def test_immutable_posted_tables_and_nonnegative_trigger(self):
        self.receive()
        self.ledger.sale("sold", "meat", "shop", 1, TODAY)
        self.ledger.customer_return("ret", "sold", "raw", "q", "shop", 1)
        self.ledger.recall("recall", "raw")
        for table in ("operations", "products", "batches", "entries", "edges", "holds", "returns"):
            column = self.ledger.db.execute("PRAGMA table_info(" + table + ")").fetchone()[1]
            for sql in (f"DELETE FROM {table}", f"UPDATE {table} SET {column}={column}"):
                with self.subTest(sql=sql), self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                    self.ledger.db.execute(sql)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "negative"):
            self.ledger.db.execute("INSERT INTO entries VALUES('sold','raw','other',-1,0)")

    def test_file_reopen_preserves_stock_and_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(pathlib.Path(directory) / "stock.sqlite")
            first = Ledger(path)
            try:
                result = first.receipt("r", "meat", "b", "shop", 2, 5)
            finally:
                first.close()
            second = Ledger(path)
            try:
                self.assertEqual(second.receipt("r", "meat", "b", "shop", 2, 5), result)
                self.assertEqual(second.balance("b", "shop"), [2, 5])
            finally:
                second.close()

    def concurrent_sales(self, same_key):
        with tempfile.TemporaryDirectory() as directory:
            path = str(pathlib.Path(directory) / "stock.sqlite")
            ledgers = [Ledger(path), Ledger(path)]
            try:
                ledgers[0].receipt("r", "meat", "b", "shop", 1, 7)
                barrier = threading.Barrier(2)

                def sell(i):
                    barrier.wait(timeout=5)
                    try:
                        return ledgers[i].sale("same" if same_key else str(i), "meat", "shop", 1, TODAY)
                    except DomainError as exc:
                        return exc

                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(sell, range(2)))
                self.assertEqual(ledgers[0].balance("b", "shop"), [0, 0])
                if same_key:
                    self.assertEqual(results[0], results[1])
                    self.assertIsInstance(results[0], list)
                else:
                    self.assertEqual(sum(isinstance(r, list) for r in results), 1)
                    self.assertEqual(sum(isinstance(r, DomainError) for r in results), 1)
                self.assertEqual(ledgers[0].db.execute("SELECT COUNT(*) FROM entries WHERE qty<0").fetchone()[0], 1)
            finally:
                for ledger in ledgers:
                    ledger.close()

    def test_concurrent_distinct_sales_cannot_sell_same_last_stock(self):
        self.concurrent_sales(False)

    def test_concurrent_same_key_sales_post_once(self):
        self.concurrent_sales(True)


if __name__ == "__main__":
    unittest.main()
