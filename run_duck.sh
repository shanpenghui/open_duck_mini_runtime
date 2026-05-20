#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${DUCK_APP_DIR:-/home/duck/open_duck_mini_runtime}"
PYTHON="${DUCK_PYTHON:-/home/duck/.venv/bin/python}"
LOG_FILE="${DUCK_LOG_FILE:-/tmp/duck.log}"
PID_FILE="${DUCK_PID_FILE:-/tmp/duck_walk.pid}"
MODEL="${DUCK_ONNX_MODEL:-BEST_WALK_ONNX_2.onnx}"
CONFIG="${DUCK_CONFIG:-$APP_DIR/duck_config.json}"
XBOX_ADDRESS="${DUCK_XBOX_ADDRESS:-91:B4:9E:A2:3C:ED}"
BNO_I2C_BUS="${DUCK_BNO_I2C_BUS:-1}"
BNO_I2C_ADDR="${DUCK_BNO_I2C_ADDR:-0x28}"
I2CDETECT="${DUCK_I2CDETECT:-/usr/sbin/i2cdetect}"

CONTROL_FREQ="${DUCK_CONTROL_FREQ:-50}"
KP="${DUCK_KP:-22}"
KD="${DUCK_KD:-0}"
ACTION_SCALE="${DUCK_ACTION_SCALE:-0.2}"
MIN_MOTOR_VOLTAGE="${DUCK_MIN_MOTOR_VOLTAGE:-6.3}"
POWER_LOG_INTERVAL="${DUCK_POWER_LOG_INTERVAL:-1.0}"
WAIT_CONTROLLER="${DUCK_WAIT_CONTROLLER:-1}"
WAIT_CONTROLLER_TIMEOUT="${DUCK_WAIT_CONTROLLER_TIMEOUT:-0}"
WAIT_CONTROLLER_INTERVAL="${DUCK_WAIT_CONTROLLER_INTERVAL:-2}"

export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-dummy}"
export SDL_AUDIODRIVER="${SDL_AUDIODRIVER:-dummy}"
export PYTHONUNBUFFERED=1

usage() {
    cat <<EOF
Usage: ./run_duck.sh {start|start-headless|start-foreground|stop|restart|status|log|check|voltage}

Commands:
  start           Start with Xbox controller commands enabled.
  start-headless  Start without Xbox controller commands.
  start-foreground Start in foreground for systemd.
  stop            Stop runtime and turn motor torque off.
  restart         Stop, then start.
  status          Show process status and recent logs.
  log             Follow $LOG_FILE.
  check           Check files, hardware nodes, and Xbox pairing status.
  voltage         Read servo bus voltage/current.

Environment overrides:
  DUCK_MIN_MOTOR_VOLTAGE=6.3 DUCK_KP=22 DUCK_ACTION_SCALE=0.2
  DUCK_WAIT_CONTROLLER=1 DUCK_WAIT_CONTROLLER_TIMEOUT=0
EOF
}

ensure_app() {
    cd "$APP_DIR"
    if [ ! -x "$PYTHON" ]; then
        echo "[ERROR] Python venv not found: $PYTHON" >&2
        exit 1
    fi
    if [ ! -f "$MODEL" ]; then
        echo "[ERROR] ONNX model not found: $APP_DIR/$MODEL" >&2
        exit 1
    fi
    if [ ! -f "$CONFIG" ]; then
        echo "[ERROR] duck_config.json not found: $CONFIG" >&2
        exit 1
    fi
    if [ ! -f "$APP_DIR/scripts/v2_rl_walk_mujoco.py" ]; then
        echo "[ERROR] runtime script not found: $APP_DIR/scripts/v2_rl_walk_mujoco.py" >&2
        exit 1
    fi
}

check_bno055() {
    local dev="/dev/i2c-${BNO_I2C_BUS}"
    if [ ! -e "$dev" ]; then
        echo "[ERROR] BNO055 I2C device missing: $dev" >&2
        return 1
    fi
    if [ ! -x "$I2CDETECT" ]; then
        I2CDETECT="$(command -v i2cdetect || true)"
    fi
    if [ -z "$I2CDETECT" ] || [ ! -x "$I2CDETECT" ]; then
        echo "[ERROR] i2cdetect not found; install i2c-tools." >&2
        return 1
    fi

    local scan
    scan="$($I2CDETECT -y "$BNO_I2C_BUS")"
    if echo "$scan" | grep -Eiq "(^|[[:space:]])(28|UU)([[:space:]]|$)"; then
        echo "[OK] BNO055: $dev address $BNO_I2C_ADDR"
        return 0
    fi

    echo "[ERROR] BNO055 not found on I2C bus ${BNO_I2C_BUS} at ${BNO_I2C_ADDR}." >&2
    echo "$scan" >&2
    return 1
}

check_xbox_ready() {
    "$PYTHON" "$APP_DIR/scripts/wait_xbox_ready.py" \
        --address "$XBOX_ADDRESS" \
        --timeout "${1:-8}" \
        --connect-timeout 8
}

runtime_pids() {
    pgrep -f "[s]cripts/v2_rl_walk_mujoco.py" || true
}

turn_torque_off() {
    "$PYTHON" - <<PY
import rustypot

ids = [20, 21, 22, 23, 24, 30, 31, 32, 33, 10, 11, 12, 13, 14]
io = rustypot.Sts3215PyController("/dev/ttyACM0", 1000000, 0.08)
io.sync_write_torque_enable(ids, [False] * len(ids))
print("torque off")
PY
}

check_duck() {
    ensure_app
    echo "[CHECK] App dir: $APP_DIR"
    echo "[CHECK] Python: $("$PYTHON" --version 2>&1)"
    echo "[CHECK] ONNX model: $APP_DIR/$MODEL"
    echo "[CHECK] Config: $CONFIG"
    echo "[CHECK] Runtime script: $APP_DIR/scripts/v2_rl_walk_mujoco.py"
    check_bno055
    check_xbox_ready 10
    [ -e /dev/input/js0 ] && echo "[OK] joystick node: /dev/input/js0" || { echo "[ERROR] joystick node missing: /dev/input/js0" >&2; return 1; }
    [ -e /dev/ttyACM0 ] && echo "[OK] servo bus: /dev/ttyACM0" || echo "[WARN] servo bus missing: /dev/ttyACM0"
    bluetoothctl info "$XBOX_ADDRESS" 2>/dev/null | sed -n '/Name:/p;/Paired:/p;/Bonded:/p;/Trusted:/p;/Connected:/p' || true
}

wait_for_controller() {
    if [ "$WAIT_CONTROLLER" != "1" ]; then
        return 0
    fi

    echo "[WAIT] Waiting for Xbox controller $XBOX_ADDRESS (timeout=${WAIT_CONTROLLER_TIMEOUT}s; 0 means forever)."
    check_xbox_ready "$WAIT_CONTROLLER_TIMEOUT"
}

stop_duck() {
    local pids
    pids="$(runtime_pids)"
    if [ -n "$pids" ]; then
        echo "[STOP] Killing runtime PID(s): $pids"
        kill $pids 2>/dev/null || true
        sleep 2
        pids="$(runtime_pids)"
        if [ -n "$pids" ]; then
            echo "[STOP] Runtime still alive, sending SIGKILL: $pids"
            kill -9 $pids 2>/dev/null || true
        fi
    else
        echo "[STOP] Runtime is not running"
    fi
    rm -f "$PID_FILE"
    turn_torque_off || true
}

start_duck() {
    local commands_flag="$1"
    ensure_app

    check_bno055

    local pids
    pids="$(runtime_pids)"
    if [ -n "$pids" ]; then
        echo "[ERROR] Runtime is already running: $pids" >&2
        echo "Use ./run_duck.sh restart or ./run_duck.sh stop first." >&2
        exit 1
    fi

    if [ "$commands_flag" = "--commands" ]; then
        wait_for_controller
    fi

    : > "$LOG_FILE"
    echo "[START] Launching runtime. Logs: $LOG_FILE"
    echo "[START] min_motor_voltage=$MIN_MOTOR_VOLTAGE"
    nohup "$PYTHON" -u scripts/v2_rl_walk_mujoco.py \
        --onnx_model_path "$MODEL" \
        --duck_config_path "$CONFIG" \
        "$commands_flag" \
        -c "$CONTROL_FREQ" \
        -p "$KP" \
        -d "$KD" \
        --action_scale "$ACTION_SCALE" \
        --min_motor_voltage "$MIN_MOTOR_VOLTAGE" \
        --power_log_interval "$POWER_LOG_INTERVAL" \
        > "$LOG_FILE" 2>&1 &

    echo "$!" > "$PID_FILE"
    sleep 2
    status_duck
}

start_duck_foreground() {
    ensure_app

    check_bno055

    local pids
    pids="$(runtime_pids)"
    if [ -n "$pids" ]; then
        echo "[ERROR] Runtime is already running: $pids" >&2
        exit 1
    fi

    wait_for_controller
    echo "[START] Launching runtime in foreground."
    echo "[START] min_motor_voltage=$MIN_MOTOR_VOLTAGE"
    exec "$PYTHON" -u scripts/v2_rl_walk_mujoco.py \
        --onnx_model_path "$MODEL" \
        --duck_config_path "$CONFIG" \
        --commands \
        -c "$CONTROL_FREQ" \
        -p "$KP" \
        -d "$KD" \
        --action_scale "$ACTION_SCALE" \
        --min_motor_voltage "$MIN_MOTOR_VOLTAGE" \
        --power_log_interval "$POWER_LOG_INTERVAL"
}

status_duck() {
    local pids
    pids="$(runtime_pids)"
    if [ -n "$pids" ]; then
        echo "[STATUS] RUNNING: $pids"
    else
        echo "[STATUS] NOT RUNNING"
    fi
    if [ -f "$LOG_FILE" ]; then
        echo "[STATUS] Last 30 log lines:"
        tail -30 "$LOG_FILE"
    else
        echo "[STATUS] No log file yet: $LOG_FILE"
    fi
}

case "${1:-}" in
    start)
        start_duck "--commands"
        ;;
    start-headless)
        start_duck "--no-commands"
        ;;
    start-foreground)
        start_duck_foreground
        ;;
    stop)
        stop_duck
        ;;
    restart)
        stop_duck
        sleep 2
        start_duck "--commands"
        ;;
    status)
        status_duck
        ;;
    log)
        touch "$LOG_FILE"
        tail -f "$LOG_FILE"
        ;;
    check)
        check_duck
        ;;
    voltage)
        ensure_app
        "$PYTHON" scripts/check_voltage.py
        ;;
    -h|--help|help|"")
        usage
        ;;
    *)
        echo "[ERROR] Unknown command: $1" >&2
        usage
        exit 1
        ;;
esac
