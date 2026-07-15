# systemd Deployment

This directory contains project-local systemd unit templates for running the
Raspberry Pi voice assistant services persistently.

Services:

- `voice-assistant-llama.service`: starts `llama-server` on `127.0.0.1:18080`.
- `voice-assistant-websocket.service`: starts the FastAPI WebSocket service on
  `0.0.0.0:8765` and depends on `voice-assistant-llama.service`.

The templates are safe to review in the project directory. They are not active
until copied into `/etc/systemd/system/` and enabled.

## Install

Review the files first, then run on the Raspberry Pi:

```bash
cd /home/duck/apps/voice-assistant/server/deploy
sudo bash install-systemd.sh
```

`sudo` is required because installing units writes to `/etc/systemd/system/`
and reloads the systemd manager.

The install script enables and starts both services. If you need to start them
manually after stopping any temporary foreground or background runs:

```bash
sudo systemctl start voice-assistant-llama.service
sudo systemctl start voice-assistant-websocket.service
```

## Status and Logs

```bash
systemctl status voice-assistant-llama.service
systemctl status voice-assistant-websocket.service
journalctl -u voice-assistant-llama.service -f
journalctl -u voice-assistant-websocket.service -f
```

## Thermal Checks

Before and after model tests:

```bash
vcgencmd measure_temp
vcgencmd get_throttled
```

Stop continuous load tests above `75C`. Stop model tests and let the Raspberry
Pi cool above `80C` when there is no active cooling.

## Non-Fixed IP Connection

The Android app should prefer the mDNS hostname instead of a fixed IP:

```text
ws://raspberrypi.local:8765/ws
```

If `raspberrypi.local` is not reachable, check the current address on the
Raspberry Pi:

```bash
hostname
hostname -I
ping raspberrypi.local
```

More details are in `docs/network_connection.md`.
