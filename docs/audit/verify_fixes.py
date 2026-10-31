"""Runs every case of reproduce_review.py (which asserts the OLD faulty
behaviour) and the second review's market_edges cases and records the result.

A fault counts as FIXED only when the program answers with an EXPECTED
commercial refusal (a ShopError with one of the listed codes) or the old faulty
outcome simply no longer happens (its assertion fails). Any other exception
(TypeError, missing setup, missing table ...) is a VERIFICATION FAILURE, never
an "ok" - the exit status is non-zero then.
Run: python docs/audit/verify_fixes.py > docs/audit/fix_results.jsonl
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
import reproduce_review as old  # noqa: E402
import market_edges_20261031 as edges  # noqa: E402
from server.core import ShopError  # noqa: E402

# case -> commercial refusal codes that are the CORRECT answer for the old faulty step
EXPECTED_REFUSALS = {
    "R02": {"return_exceeds_sold", "refund_exceeds_paid_by_method"},
    "R03": {"insufficient_stock", "lot_recalled", "lot_under_recall"},
    "R04": {"recipe_component_missing"},
    "R05": {"scanned_label_expired"},
    "R06": {"drawer_insufficient"},
    "R10": {"request_key_other_user"},
    "R12": {"stock_reserved_by_packs"},
    "R15": {"packaging_other_product"},
    "E01": {"collection_already_refunded"},
    "E02": {"stock_reserved_by_packs"},
    "E03": {"meat_role_needs_meat_product"},
    "E04": {"price_changed"},
    "E05": set(),
    "E06": {"collections_unallocated", "account_needs_review"},
}
# cases whose old script exercises an internal path that is intentionally still open
# (diagnostic scanner view, internal pricing call): covered by dedicated tests instead
COVERED_ELSEWHERE = {"R09": "tests/test_audit.py::test_r09 + tests/test_api.py::test_r09",
                     "R13": "tests/test_audit.py::test_r13",
                     "R14": "tests/test_audit.py::test_r14 + tests/test_market.py::test_e04 (API requires quote_hash)",
                     "E04": "tests/test_market.py::test_e04_* (the old script calls the internal service without a quote_hash; the API requires it)",
                     "E06": "tests/test_market.py::UpgradeFromV1 (real schema-1 database) + test_unallocated_cash_collection_*"}

CASES = [('R01', old.mixed_return), ('R02', old.paid_credit), ('R03', old.recalled_return), ('R04', old.recipe_omission),
         ('R05', old.gs1_ignored), ('R06', old.negative_drawer), ('R07', old.payment_status), ('R09', old.scanner_broadcast),
         ('R10', old.idempotency_actor), ('R11', old.accountant_supplier), ('R12', old.packed_loss), ('R13', old.scanner_restart),
         ('R14', old.price_changed), ('R15', old.cross_packaging),
         ('E01', edges.reverse_after_refund), ('E02', edges.packed_input), ('E03', edges.false_meat_role),
         ('E04', edges.equal_total_prices), ('E05', edges.anonymous_reject), ('E06', edges.migration)]

failures = 0
for name, fn in CASES:
    f = old.Fixture()
    f.make_shop()
    try:
        observed = fn(f)
        if name in COVERED_ELSEWHERE:
            row = {"id": name, "fault_reproduces": True, "status": "covered_by_other_test", "covered_by": COVERED_ELSEWHERE[name]}
        elif observed is None:
            row = {"id": name, "fault_reproduces": False, "status": "fixed", "how": "case ended without the old faulty outcome"}
        else:
            row = {"id": name, "fault_reproduces": True, "status": "STILL_FAULTY", "observed": observed}
            failures += 1
    except AssertionError:
        row = {"id": name, "fault_reproduces": False, "status": "fixed", "how": "old faulty outcome no longer happens"}
    except ShopError as exc:
        if exc.code in EXPECTED_REFUSALS.get(name, set()):
            row = {"id": name, "fault_reproduces": False, "status": "fixed", "how": "refused: %s" % exc.code}
        else:
            row = {"id": name, "status": "VERIFICATION_FAILED", "how": "unexpected refusal %s" % exc.code}
            failures += 1
    except Exception as exc:  # programming or setup error: never counted as a fix
        row = {"id": name, "status": "VERIFICATION_FAILED", "how": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
        failures += 1
    finally:
        f.doCleanups()
    print(json.dumps(row, ensure_ascii=False))
sys.exit(1 if failures else 0)
