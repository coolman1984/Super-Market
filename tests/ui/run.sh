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
trap 'kill $PID $PID2; rm -rf "$TMP"' EXIT
sleep 2
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
