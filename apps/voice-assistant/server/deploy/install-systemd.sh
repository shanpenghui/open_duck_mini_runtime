#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run with sudo: sudo bash ${SCRIPT_DIR}/install-systemd.sh"
  exit 1
fi

install -m 0644 "${SCRIPT_DIR}/voice-assistant-llama.service" "${SYSTEMD_DIR}/voice-assistant-llama.service"
install -m 0644 "${SCRIPT_DIR}/voice-assistant-websocket.service" "${SYSTEMD_DIR}/voice-assistant-websocket.service"

systemctl daemon-reload
systemctl enable voice-assistant-llama.service
systemctl enable voice-assistant-websocket.service
systemctl start voice-assistant-llama.service
systemctl start voice-assistant-websocket.service

echo "Installed, enabled, and started systemd services."
echo
echo "Check status with:"
echo "  systemctl status voice-assistant-llama.service"
echo "  systemctl status voice-assistant-websocket.service"
echo
echo "Android default WebSocket URL:"
echo "  ws://raspberrypi.local:8765/ws"
