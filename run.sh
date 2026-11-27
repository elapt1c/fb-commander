#!/usr/bin/env bash
# One-command launcher: builds a private venv on first run, then starts the TUI.
#   ./run.sh                 -> interactive UI
#   ./run.sh probe           -> headless access check
#   ./run.sh backup -o ~/fb  -> headless full backup
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
VENV=".venv"

if [ ! -x "$VENV/bin/python" ]; then
  "$PY" -m venv "$VENV"
fi

if [ ! -f "$VENV/.deps-ok" ] || [ requirements.txt -nt "$VENV/.deps-ok" ]; then
  "$VENV/bin/python" -m pip install -q --upgrade pip
  "$VENV/bin/python" -m pip install -q -r requirements.txt
  touch "$VENV/.deps-ok"
fi

exec "$VENV/bin/python" -m fbcmd "$@"
