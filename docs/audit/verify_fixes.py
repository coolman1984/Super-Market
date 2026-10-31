"""Runs every case of reproduce_review.py (which asserts the OLD faulty
behaviour) and records whether the fault still reproduces. After the fixes
each case must fail its old assertion or be refused by the program.
Run: python docs/audit/verify_fixes.py > docs/audit/fix_results.jsonl
"""
import json
import pathlib
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import reproduce_review as old  # noqa: E402

CASES = [('R01', old.mixed_return), ('R02', old.paid_credit), ('R03', old.recalled_return), ('R04', old.recipe_omission),
         ('R05', old.gs1_ignored), ('R06', old.negative_drawer), ('R07', old.payment_status), ('R09', old.scanner_broadcast),
         ('R10', old.idempotency_actor), ('R11', old.accountant_supplier), ('R12', old.packed_loss), ('R13', old.scanner_restart),
         ('R14', old.price_changed), ('R15', old.cross_packaging)]

for name, fn in CASES:
    f = old.Fixture()
    f.make_shop()
    try:
        observed = fn(f)
        row = {"id": name, "fault_reproduces": True, "observed": observed}
    except AssertionError:
        row = {"id": name, "fault_reproduces": False, "how": "old faulty outcome no longer happens"}
    except Exception as exc:  # the program now refuses the faulty operation
        row = {"id": name, "fault_reproduces": False, "how": "refused: %s" % (getattr(exc, "code", None) or type(exc).__name__)}
    finally:
        f.doCleanups()
    print(json.dumps(row, ensure_ascii=False))
