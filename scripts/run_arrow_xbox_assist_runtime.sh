#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source ~/.venv/bin/activate

export PYTHONPATH="$PWD/src:/usr/lib/python3/dist-packages:${PYTHONPATH:-}"
exec /home/duck/.venv/bin/python -u scripts/run_arrow_xbox_assist_runtime.py "$@"
