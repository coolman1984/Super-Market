#!/bin/sh
# Fresh TRAINING server on a temp folder, then the UI smoke and flow tests.
set -e
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
TMP=$(mktemp -d)
OUT=${1:-$TMP/shots}; mkdir -p "$OUT"
cd "$ROOT"
python3 -m server.app --training --data "$TMP/training-data" --port 8799 >/dev/null 2>&1 &
PID=$!
python3 -m server.app --data "$TMP/fresh-data" --port 8798 >/dev/null 2>&1 &
PID2=$!
cleanup() {
  kill "$PID" "$PID2" 2>/dev/null || true
  wait "$PID" "$PID2" 2>/dev/null || true
  # Windows file handles close only after the native processes have exited.
  python3 - "$TMP" <<'CLEANUP'
import pathlib, shutil, sys, time
folder = pathlib.Path(sys.argv[1])
for attempt in range(10):
    try:
        shutil.rmtree(folder)
        break
    except FileNotFoundError:
        break
    except OSError:
        if attempt == 9:
            raise
        time.sleep(0.1)
CLEANUP
}
trap cleanup EXIT
python3 - <<'PY'
import sys, time, urllib.request
for port in (8799, 8798):
    ok = False
    for _ in range(50):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=1)
            ok = True
            break
        except Exception:
            time.sleep(0.2)  # poll until the server answers; not a UI pause
    if not ok:
        sys.exit("server on %d did not answer" % port)
PY
node tests/ui/smoke.js http://127.0.0.1:8799 "$OUT"
node tests/ui/flows.js http://127.0.0.1:8799 "$OUT"
node tests/ui/rows.js http://127.0.0.1:8799 "$OUT"
node tests/ui/review_b.js http://127.0.0.1:8799 "$OUT"
node tests/ui/accounts.js http://127.0.0.1:8799 "$OUT"
node tests/ui/draft_review.js http://127.0.0.1:8799
node tests/ui/guide.js http://127.0.0.1:8799 "$OUT"
node tests/ui/cloud.js http://127.0.0.1:8799 "$OUT"
node tests/ui/review_c.js http://127.0.0.1:8799 "$OUT"
node tests/ui/simple_accounts.js http://127.0.0.1:8799 http://127.0.0.1:8798 "$OUT"
node tests/ui/review_d.js http://127.0.0.1:8799 "$OUT"
node tests/ui/sm02.js http://127.0.0.1:8799 "$OUT"
node tests/ui/shift_shortage.js http://127.0.0.1:8799 "$OUT"
node tests/ui/count_sheet.js http://127.0.0.1:8799 "$OUT"

node tests/ui/pay_empty.js http://127.0.0.1:8799 "$OUT"
node tests/ui/shift_view.js http://127.0.0.1:8799 "$OUT"
node tests/ui/release_hardware.js http://127.0.0.1:8799 "$OUT"
