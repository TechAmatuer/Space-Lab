#!/bin/bash
# Double-click to start Space Lab. It opens http://localhost:8790 in your browser.
# Keep this window open while you use it. Closing it (or Ctrl+C) stops Space Lab and any job it is running.
cd "$(dirname "$0")" || exit 1
PORT=8790

# Already running in another window: just show the page.
if curl -s --max-time 2 "http://127.0.0.1:$PORT/api/ping" | grep -q spacelab; then
  echo "Space Lab is already running."
  open "http://localhost:$PORT"
  exit 0
fi
pkill -f "spacelab[.]server" 2>/dev/null; pkill -f "spacelab[.]jobs" 2>/dev/null   # leftovers from a window that was force-closed

# First start, after a move or after an update that needs new packages: set up (a few minutes, only then).
want="$(cat requirements.txt requirements-exotic.txt requirements-fpp.txt 2>/dev/null | shasum | cut -c1-40)"
have="$(cat .venv/.req-stamp 2>/dev/null)"
if [ "$want" != "$have" ] ||
   ! .venv/bin/python -c "import numpy" >/dev/null 2>&1 ||
   ! .venv-exotic/bin/python -c "import importlib.util as u, sys; sys.exit(not u.find_spec('exotic'))" >/dev/null 2>&1; then
  echo "Setting up Space Lab (only the first time, after a move or after an update) ..."
  if ! bash setup.sh; then
    read -r -p "Space Lab setup hit a problem. Paste this window's text to Claude, then press Return to close."
    exit 1
  fi
fi

mkdir -p logs
PYTHONPYCACHEPREFIX="$PWD/data/cache/pycache" nohup .venv/bin/python -m spacelab.server --no-browser >> logs/server.log 2>&1 &
stop_spacelab() { pkill -f "spacelab[.]server"; pkill -f "spacelab[.]jobs"; } 2>/dev/null
trap stop_spacelab EXIT
trap 'exit 130' INT TERM HUP

for _ in $(seq 1 30); do
  curl -s --max-time 1 "http://127.0.0.1:$PORT/api/ping" | grep -q spacelab && break
  sleep 1
done
if ! curl -s --max-time 2 "http://127.0.0.1:$PORT/api/ping" | grep -q spacelab; then
  tail -n 30 logs/server.log
  read -r -p "Space Lab did not start. Paste this window's text to Claude, then press Return to close."
  exit 1
fi
open "http://localhost:$PORT"
echo
echo "Space Lab is running at http://localhost:$PORT"
echo "Keep this window open. Close it (or press Ctrl+C) to stop Space Lab."
echo
tail -n 0 -f logs/server.log
