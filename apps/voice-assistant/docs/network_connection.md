# Network Connection and Autostart

This document covers the first-stage Raspberry Pi autostart and non-fixed-IP
connection plan.

Known deployment values:

- Raspberry Pi user: `duck`
- Project path: `/home/duck/apps/voice-assistant`
- llama.cpp binary path: `/home/duck/apps/llama.cpp/build/bin`
- Current SSH example: `ssh duck@10.255.190.56`
- Android default WebSocket URL: `ws://raspberrypi.local:8765/ws`

The IP address is not fixed. Do not make the app depend on
`10.255.190.56` as a permanent address. Use mDNS/hostname first, then fall
back to the current IP only when hostname resolution is unavailable.

## Existing Services

The project uses two systemd services:

- `voice-assistant-llama.service` starts `llama-server` on
  `127.0.0.1:18080`.
- `voice-assistant-websocket.service` starts the FastAPI WebSocket server on
  `0.0.0.0:8765` and depends on `voice-assistant-llama.service`.

The WebSocket service is reachable from the phone at:

```text
ws://raspberrypi.local:8765/ws
```

If the Raspberry Pi hostname is changed to `voice-assistant`, the address can
be:

```text
ws://voice-assistant.local:8765/ws
```

## Check Hostname and Current IP

Run these commands on the Raspberry Pi:

```bash
hostname
hostname -I
```

From another device on the same network, test mDNS:

```bash
ping raspberrypi.local
```

If `raspberrypi.local` is unavailable, temporarily use the IP shown by
`hostname -I`:

```text
ws://current-ip:8765/ws
```

For example, the current SSH address is:

```bash
ssh duck@10.255.190.56
```

That address is useful for today's setup, but it should not be treated as a
long-term Android default.

## Install Autostart Services

Run on the Raspberry Pi:

```bash
cd /home/duck/apps/voice-assistant/server/deploy
sudo bash install-systemd.sh
```

The installer copies the local service files into `/etc/systemd/system/`,
reloads systemd, and enables both services.

Manual equivalent commands:

```bash
sudo systemctl daemon-reload
sudo systemctl enable voice-assistant-llama.service
sudo systemctl enable voice-assistant-websocket.service
sudo systemctl start voice-assistant-llama.service
sudo systemctl start voice-assistant-websocket.service
```

After this, both services should start automatically on reboot.

## Check Status and Logs

```bash
sudo systemctl status voice-assistant-llama.service
sudo systemctl status voice-assistant-websocket.service
```

Follow logs:

```bash
journalctl -u voice-assistant-llama.service -f
journalctl -u voice-assistant-websocket.service -f
```

Check the WebSocket server health endpoint on the Raspberry Pi:

```bash
curl http://127.0.0.1:8765/healthz
```

From another device on the LAN, use the current IP or hostname:

```text
http://raspberrypi.local:8765/healthz
```

## Android First-Stage Auto Connect

Android should use this order:

1. Try the saved URL if the user has manually entered one.
2. Otherwise try `ws://raspberrypi.local:8765/ws`.
3. If hostname resolution fails, show an offline state and keep the manual URL
   input available.
4. Retry every 3 to 5 seconds while the app is open.
5. Keep connection state visible as `connecting`, `connected`, `offline`, or
   `model busy`.

Temporary manual URL example:

```text
ws://10.255.190.56:8765/ws
```

This is only a fallback for the current network session.

## First-Stage Verification

1. SSH into the Raspberry Pi:

   ```bash
   ssh duck@10.255.190.56
   ```

2. Install and start services:

   ```bash
   cd /home/duck/apps/voice-assistant/server/deploy
   sudo bash install-systemd.sh
   sudo systemctl start voice-assistant-llama.service
   sudo systemctl start voice-assistant-websocket.service
   ```

3. Check service status:

   ```bash
   sudo systemctl status voice-assistant-llama.service
   sudo systemctl status voice-assistant-websocket.service
   ```

4. Check local health:

   ```bash
   curl http://127.0.0.1:8765/healthz
   ```

5. Check hostname-based access from the phone network:

   ```bash
   ping raspberrypi.local
   ```

6. Open the Android app and connect to:

   ```text
   ws://raspberrypi.local:8765/ws
   ```

7. Reboot the Raspberry Pi and verify that both services come back:

   ```bash
   sudo systemctl status voice-assistant-llama.service
   sudo systemctl status voice-assistant-websocket.service
   ```

## Troubleshooting

- If `raspberrypi.local` does not resolve, check that the phone and Raspberry Pi
  are on the same LAN and try the IP from `hostname -I`.
- If `/healthz` works on the Pi but not from the phone, check firewall, Wi-Fi
  isolation, and whether the WebSocket server is listening on `0.0.0.0:8765`.
- If chat returns model errors, check `voice-assistant-llama.service` first.
- If WebSocket cannot connect, check `voice-assistant-websocket.service` logs.
- If the model starts slowly after boot, wait for
  `voice-assistant-llama.service` to finish loading before testing chat.
