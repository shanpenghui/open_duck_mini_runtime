#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f "$HOME/.venv/bin/activate" ]; then
  echo "Missing virtual environment: $HOME/.venv" >&2
  echo "Create it on the Pi, then install dependencies with:" >&2
  echo "  python3 -m venv --system-site-packages ~/.venv" >&2
  echo "  source ~/.venv/bin/activate" >&2
  echo "  python -m pip install -e ." >&2
  exit 1
fi

source "$HOME/.venv/bin/activate"
export PYTHONPATH="$PWD/src:/usr/lib/python3/dist-packages:${PYTHONPATH:-}"

exec python scripts/recognize_arrow_camera_v2.py "$@"
