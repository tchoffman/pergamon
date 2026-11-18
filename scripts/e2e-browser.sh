#!/usr/bin/env bash
# Browser end-to-end test: two people in headless Chrome create keys, post to
# the board and classifieds, and confirm a trade receipt — against a throwaway
# copy of the repo and an in-process test relay, so your build/ data is untouched.
#
# Needs: Google Chrome (or CHROME_PATH), node, and the venv (make venv).
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
PY="$REPO/build/venv/bin/python"
[[ -x "$PY" ]] || { echo "run 'make venv' first" >&2; exit 1; }
command -v node >/dev/null || { echo "node is required" >&2; exit 1; }

WORK="$(mktemp -d)"
cleanup() {
  for pid in ${WEB_PID:-} ${RELAY_PID:-}; do
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
  rm -rf "$WORK"
}
trap cleanup EXIT

free_port() { "$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }
RELAY_PORT="$(free_port)"; WEB_PORT="$(free_port)"

rsync -a --exclude build --exclude .git "$REPO/" "$WORK/app/"
mkdir -p "$WORK/app/build" && ln -s "$REPO/build/venv" "$WORK/app/build/venv"
printf 'preset: default\nnostr:\n  relays:\n    - ws://127.0.0.1:%s/\n' "$RELAY_PORT" > "$WORK/app/config/install.local.yaml"
# Frost dates, so the Guide doesn't reach out to NOAA during the test.
mkdir -p "$WORK/app/build/state"
printf '{"last":"03-27","first":"11-07","last_safe":"04-12","first_safe":"10-27","source":"test data"}' \
  > "$WORK/app/build/state/frost.json"

cd "$WORK/app"
"$PY" -c "
import sys, time; sys.path.insert(0, '.')
from tests.mini_relay import MiniRelay
MiniRelay(port=$RELAY_PORT).start()
while True: time.sleep(3600)
" & RELAY_PID=$!
"$PY" -m uvicorn rag.web:app --host 127.0.0.1 --port "$WEB_PORT" --log-level warning & WEB_PID=$!
for _ in $(seq 1 40); do curl -sf "127.0.0.1:$WEB_PORT/welcome" >/dev/null && break; sleep 0.25; done

CACHE="$REPO/build/e2e-node"
if [[ ! -d "$CACHE/node_modules/puppeteer-core" ]]; then
  mkdir -p "$CACHE" && (cd "$CACHE" && npm init -y >/dev/null && npm install --silent puppeteer-core@22.6.5)
fi
NODE_PATH="$CACHE/node_modules" PERGAMON_URL="http://127.0.0.1:$WEB_PORT" node "$REPO/tests/e2e/browser.js"
