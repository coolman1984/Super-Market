#!/bin/sh
# Fresh TRAINING server on a temp folder, then the UI smoke and flow tests.
set -e
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
TMP=$(mktemp -d)
OUT=${1:-$TMP/shots}; mkdir -p "$OUT"
cd "$ROOT"
python3 -m server.app --training --data "$TMP/training-data" --port 8799 >/dev/null 2>&1 &
PID=$!
trap 'kill $PID; rm -rf "$TMP"' EXIT
sleep 2
node tests/ui/smoke.js http://127.0.0.1:8799 "$OUT"
node tests/ui/flows.js http://127.0.0.1:8799 "$OUT"
node tests/ui/rows.js http://127.0.0.1:8799 "$OUT"
