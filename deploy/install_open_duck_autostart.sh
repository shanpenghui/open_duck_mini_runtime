#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${DUCK_SERVICE_NAME:-open-duck-runtime}"
USER_NAME="${DUCK_USER:-orangepi}"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6 || true)"
USER_HOME="${USER_HOME:-/home/$USER_NAME}"
APP_DIR="${DUCK_APP_DIR:-$USER_HOME/open_duck_mini_runtime}"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
SUDOERS_PATH="/etc/sudoers.d/open-duck-poweroff"
RUN_SCRIPT="$APP_DIR/run_duck.sh"

usage() {
    cat <<EOF
Usage: ./deploy/install_open_duck_autostart.sh {install|enable-now|start|stop|restart|status|log|verify|disable|uninstall}

Commands:
  install     Install the systemd service and enable boot autostart.
  enable-now  Install, enable, and start the service immediately.
  start       Start the service now.
  stop        Stop the service now.
  restart     Restart the service now.
  status      Show systemd status.
  log         Follow service logs.
  verify      Check enabled/active state, runtime process, and recent logs.
  disable     Disable boot autostart, without deleting the service file.
  uninstall   Stop, disable, and remove the service file.

Environment overrides:
  DUCK_USER=orangepi
  DUCK_APP_DIR=/home/orangepi/open_duck_mini_runtime
  DUCK_PYTHON=/home/orangepi/open_duck_mini_runtime/.venv/bin/python
EOF
}

detect_python() {
    if [ -n "${DUCK_PYTHON:-}" ]; then
        echo "$DUCK_PYTHON"
        return 0
    fi

    if [ -x "$APP_DIR/.venv/bin/python" ]; then
        echo "$APP_DIR/.venv/bin/python"
        return 0
    fi

    if [ -x "$USER_HOME/.venv/bin/python" ]; then
        echo "$USER_HOME/.venv/bin/python"
        return 0
    fi

    echo ""
}

check_files() {
    if [ ! -d "$APP_DIR" ]; then
        echo "[ERROR] Code directory not found: $APP_DIR" >&2
        exit 1
    fi

    if [ ! -f "$RUN_SCRIPT" ]; then
        echo "[ERROR] Runtime script not found: $RUN_SCRIPT" >&2
        exit 1
    fi

    if [ ! -f "$APP_DIR/duck_config.json" ]; then
        echo "[ERROR] Config not found: $APP_DIR/duck_config.json" >&2
        exit 1
    fi

    if [ ! -f "$APP_DIR/BEST_WALK_ONNX_2.onnx" ]; then
        echo "[ERROR] ONNX model not found: $APP_DIR/BEST_WALK_ONNX_2.onnx" >&2
        exit 1
    fi

    local python_bin
    python_bin="$(detect_python)"
    if [ -z "$python_bin" ]; then
        echo "[ERROR] Python venv not found." >&2
        echo "        Expected one of:" >&2
        echo "        - $APP_DIR/.venv/bin/python" >&2
        echo "        - $USER_HOME/.venv/bin/python" >&2
        echo "        Or set DUCK_PYTHON=/path/to/python." >&2
        exit 1
    fi
}

write_service() {
    check_files

    local python_bin
    python_bin="$(detect_python)"

    chmod +x "$RUN_SCRIPT"

    local tmp_service
    tmp_service="$(mktemp)"
    cat > "$tmp_service" <<EOF
[Unit]
Description=Open Duck Mini walking runtime
Wants=bluetooth.service network-online.target
After=bluetooth.service network-online.target
StartLimitIntervalSec=120
StartLimitBurst=10

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$APP_DIR
Environment=DUCK_APP_DIR=$APP_DIR
Environment=DUCK_PYTHON=$python_bin
Environment=DUCK_WAIT_CONTROLLER=1
Environment=DUCK_WAIT_CONTROLLER_TIMEOUT=0
Environment=OPENDUCK_IMU_I2C_BUS=2
Environment=DUCK_SHUTDOWN_BUTTON_INDEX=15
Environment=DUCK_SHUTDOWN_HOLD_SECONDS=7.0
Environment="DUCK_SHUTDOWN_COMMAND=sudo -n /usr/bin/systemctl poweroff"
Environment=PYTHONUNBUFFERED=1
Environment=SDL_VIDEODRIVER=dummy
Environment=SDL_AUDIODRIVER=dummy
ExecStartPre=/bin/bash -lc 'for i in \$\$(seq 1 90); do [ -e /dev/ttyACM0 ] && exit 0; echo "[WAIT] waiting for /dev/ttyACM0 (\$\$i/90)"; sleep 1; done; echo "[ERROR] /dev/ttyACM0 not found"; exit 1'
ExecStart=$RUN_SCRIPT start-foreground
ExecStop=$RUN_SCRIPT stop
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    sudo install -m 0644 "$tmp_service" "$SERVICE_PATH"
    rm -f "$tmp_service"
    write_sudoers
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME.service"

    echo "[OK] Installed and enabled: $SERVICE_PATH"
    echo "[OK] Code path: $APP_DIR"
    echo "[OK] Runtime script: $RUN_SCRIPT"
    echo "[OK] Python: $python_bin"
    echo "[INFO] Start now with: sudo systemctl start $SERVICE_NAME.service"
}

write_sudoers() {
    local systemctl_bin
    systemctl_bin="$(command -v systemctl || echo /usr/bin/systemctl)"

    local tmp_sudoers
    tmp_sudoers="$(mktemp)"
    cat > "$tmp_sudoers" <<EOF
$USER_NAME ALL=(root) NOPASSWD: $systemctl_bin poweroff
EOF

    sudo install -m 0440 "$tmp_sudoers" "$SUDOERS_PATH"
    rm -f "$tmp_sudoers"
    sudo visudo -cf "$SUDOERS_PATH" >/dev/null
    echo "[OK] Installed shutdown permission: $SUDOERS_PATH"
}

verify_service() {
    echo "[VERIFY] Service file: $SERVICE_PATH"
    if [ -f "$SERVICE_PATH" ]; then
        echo "[OK] service file exists"
    else
        echo "[ERROR] service file missing"
    fi

    echo "[VERIFY] Boot autostart:"
    systemctl is-enabled "$SERVICE_NAME.service" || true

    echo "[VERIFY] Current state:"
    systemctl is-active "$SERVICE_NAME.service" || true

    echo "[VERIFY] Runtime process:"
    pgrep -af 'scripts/v2_rl_walk_mujoco.py|run_duck.sh start-foreground' || true

    echo "[VERIFY] Recent logs:"
    journalctl -u "$SERVICE_NAME.service" -n 60 --no-pager || true

    echo "[VERIFY] Shutdown sudoers:"
    sudo -n true 2>/dev/null && sudo visudo -cf "$SUDOERS_PATH" || true
}

case "${1:-install}" in
    install)
        write_service
        ;;
    enable-now)
        write_service
        sudo systemctl restart "$SERVICE_NAME.service"
        verify_service
        ;;
    start)
        sudo systemctl start "$SERVICE_NAME.service"
        verify_service
        ;;
    stop)
        sudo systemctl stop "$SERVICE_NAME.service"
        ;;
    restart)
        sudo systemctl restart "$SERVICE_NAME.service"
        verify_service
        ;;
    status)
        systemctl status "$SERVICE_NAME.service" --no-pager
        ;;
    log)
        journalctl -u "$SERVICE_NAME.service" -f
        ;;
    verify)
        verify_service
        ;;
    disable)
        sudo systemctl disable "$SERVICE_NAME.service"
        ;;
    uninstall)
        sudo systemctl stop "$SERVICE_NAME.service" 2>/dev/null || true
        sudo systemctl disable "$SERVICE_NAME.service" 2>/dev/null || true
        sudo rm -f "$SERVICE_PATH"
        sudo rm -f "$SUDOERS_PATH"
        sudo systemctl daemon-reload
        echo "[OK] Uninstalled: $SERVICE_NAME.service"
        ;;
    -h|--help|help)
        usage
        ;;
    *)
        echo "[ERROR] Unknown command: ${1:-}" >&2
        usage
        exit 1
        ;;
esac
