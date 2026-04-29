#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f "$HOME/.venv/bin/activate" ]; then
  echo "Missing virtual environment: $HOME/.venv" >&2
  exit 1
fi

source "$HOME/.venv/bin/activate"
export PYTHONPATH="$PWD/src:/usr/lib/python3/dist-packages:${PYTHONPATH:-}"

exec python scripts/run_arrow_open_duck_runtime.py "$@"
