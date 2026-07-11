#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source ~/.venv/bin/activate

export PYTHONPATH="$PWD/src:/usr/lib/python3/dist-packages:${PYTHONPATH:-}"
exec "$HOME/open_duck_mini_runtime/.venv/bin/python" -u scripts/run_arrow_xbox_assist_runtime.py "$@"
