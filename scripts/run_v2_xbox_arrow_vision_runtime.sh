#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source ~/.venv/bin/activate

export OPEN_DUCK_RUNTIME_ROOT="${OPEN_DUCK_RUNTIME_ROOT:-/home/duck/open_duck_mini_runtime}"
export PYTHONPATH="$PWD/src:/usr/lib/python3/dist-packages:${OPEN_DUCK_RUNTIME_ROOT}/mini_bdx_runtime:${PYTHONPATH:-}"
export PYGAME_HIDE_SUPPORT_PROMPT=1

exec /home/duck/.venv/bin/python -u scripts/v2_rl_walk_mujoco_arrow_vision.py \
  --onnx_model_path "${OPEN_DUCK_RUNTIME_ROOT}/BEST_WALK_ONNX_2.onnx" \
  --duck_config_path "${OPEN_DUCK_RUNTIME_ROOT}/duck_config.json" \
  --commands \
  --command_source xbox \
  -c 50 \
  -p 22 \
  -d 0 \
  --action_scale 0.2 \
  --min_motor_voltage 6.5 \
  --vision-arrow-assist \
  --vision-threshold-mode dark \
  --vision-roi none \
  --vision-min-area 80 \
  --vision-sample-rotation-deg 35 \
  --vision-confidence 0.55 \
  "$@"
