#!/bin/bash
# One-click deploy and launch script for Open Duck Mini Runtime
# Usage: ./deploy.sh [start|stop|restart|status|sync]
#
# Prerequisites:
#   - SSH access to duck@192.168.0.34 (key or password)
#   - Local code at the same directory as this script's parent
#
# Environment setup handled automatically:
#   - SDL_VIDEODRIVER=dummy (headless pygame)
#   - PYTHONUNBUFFERED=1 (real-time logs)
#   - hid_generic + hid_microsoft kernel modules (Xbox controller)

set -e

DUCK_HOST="duck@192.168.0.34"
DUCK_REMOTE_DIR="~/open_duck_mini_runtime"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_ACTIVATE="source ~/.venv/bin/activate"
LOG_FILE="/tmp/duck.log"
ONNX_MODEL="BEST_WALK_ONNX_2.onnx"
DUCK_CONFIG="duck_config.json"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ssh_duck() {
    ssh "$DUCK_HOST" "$@"
}

sync_code() {
    echo -e "${YELLOW}[SYNC] Syncing code to $DUCK_HOST...${NC}"
    rsync -avz --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
        "$LOCAL_DIR/" "$DUCK_HOST:$DUCK_REMOTE_DIR/"
    # Sync onnx models separately (large files)
    rsync -avz "$LOCAL_DIR/"*.onnx "$DUCK_HOST:$DUCK_REMOTE_DIR/" 2>/dev/null || true
    echo -e "${GREEN}[SYNC] Done${NC}"
}

check_prereqs() {
    echo -e "${YELLOW}[CHECK] Checking prerequisites...${NC}"
    
    # Check serial port
    ssh_duck "ls -la /dev/ttyACM0" 2>/dev/null || {
        echo -e "${RED}[ERROR] /dev/ttyACM0 not found! Check servo USB connection.${NC}"
        return 1
    }
    
    # Check I2C (IMU)
    ssh_duck "sudo i2cdetect -y 1 | grep -q '28' && echo 'IMU OK' || echo 'IMU NOT FOUND on I2C'"
    
    # Load Xbox controller kernel modules
    ssh_duck "sudo modprobe hid_generic hid_microsoft 2>/dev/null; lsmod | grep -q hid_microsoft && echo 'Xbox driver OK' || echo 'Xbox driver not loaded'"
    
    # Check joystick device
    ssh_duck "ls /dev/input/js0 2>/dev/null && echo 'Joystick OK' || echo 'No joystick - run: bluetoothctl connect C0:D6:D5:E9:D7:D1'"
    
    echo -e "${GREEN}[CHECK] Done${NC}"
}

do_start() {
    echo -e "${YELLOW}[START] Launching duck...${NC}"
    
    # Kill any existing instance
    ssh_duck "pkill -f v2_rl_walk_mujoco 2>/dev/null; sleep 2" || true
    
    # Wait for serial port to be free
    ssh_duck "while fuser /dev/ttyACM0 >/dev/null 2>&1; do sleep 1; done"
    
    ssh_duck "cd $DUCK_REMOTE_DIR && $VENV_ACTIVATE && \
        PYTHONUNBUFFERED=1 nohup python -u scripts/v2_rl_walk_mujoco.py \
        --onnx_model_path $ONNX_MODEL \
        --duck_config_path $DUCK_REMOTE_DIR/$DUCK_CONFIG \
        --commands -p 30 -d 0 \
        > $LOG_FILE 2>&1 &"
    
    sleep 3
    echo -e "${GREEN}[START] Duck launched. PID: $(ssh_duck 'pgrep -f v2_rl_walk_mujoco')${NC}"
    echo -e "${YELLOW}[START] Watching logs (Ctrl+C to stop watching, duck keeps running)...${NC}"
    ssh_duck "tail -f $LOG_FILE"
}

do_stop() {
    echo -e "${YELLOW}[STOP] Stopping duck...${NC}"
    ssh_duck "pkill -f v2_rl_walk_mujoco 2>/dev/null && echo 'Stopped' || echo 'Not running'"
}

do_status() {
    echo -e "${YELLOW}[STATUS] Duck status:${NC}"
    ssh_duck "if pgrep -f v2_rl_walk_mujoco >/dev/null; then \
        echo 'RUNNING (PID:' \$(pgrep -f v2_rl_walk_mujoco) ')'; \
        echo '--- Last 10 log lines ---'; \
        tail -10 $LOG_FILE; \
    else \
        echo 'NOT RUNNING'; \
    fi"
}

case "${1:-start}" in
    sync)
        sync_code
        ;;
    check)
        check_prereqs
        ;;
    start)
        sync_code
        check_prereqs
        do_start
        ;;
    stop)
        do_stop
        ;;
    restart)
        do_stop
        sleep 3
        do_start
        ;;
    status)
        do_status
        ;;
    log)
        ssh_duck "tail -f $LOG_FILE"
        ;;
    *)
        echo "Usage: $0 {sync|check|start|stop|restart|status|log}"
        echo ""
        echo "  sync    - Sync local code to remote"
        echo "  check   - Check hardware prerequisites"
        echo "  start   - Sync + check + launch (default)"
        echo "  stop    - Stop the duck program"
        echo "  restart - Stop and restart"
        echo "  status  - Show running status and recent logs"
        echo "  log     - Tail the duck log"
        exit 1
        ;;
esac
