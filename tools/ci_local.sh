#!/usr/bin/env sh
# What CI runs, in one command. Starts the app, runs the acceptance sweep, stops it.
set -e
cd "$(dirname "$0")/.."
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3
"$PY" -m compileall -q . -x '\.venv|__pycache__'
"$PY" -c "import app; assert app.app.test_client().get('/').status_code == 200; print('app serves')"
"$PY" -m unittest discover -s tests -v
PORT=${STORYROOM_PORT:-8123}
STORYROOM_URL="http://127.0.0.1:$PORT" "$PY" -c "
import os, app
app.app.run(host='127.0.0.1', port=$PORT, debug=False)
" &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
for i in $(seq 1 60); do
  curl -sf -o /dev/null "http://127.0.0.1:$PORT/" && break
  sleep 1
done
STORYROOM_URL="http://127.0.0.1:$PORT" "$PY" tools/runtime_sweep.py --runtimes 60 --work /tmp/ci-sweep --sources 8
