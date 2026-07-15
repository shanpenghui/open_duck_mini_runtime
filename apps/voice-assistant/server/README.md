# Raspberry Pi Voice Assistant Server

This directory contains the Raspberry Pi WebSocket service for the local voice
assistant project.

Current goal: keep the Android to Pi protocol stable, expose a real WebSocket
endpoint, and call the local `llama-server` OpenAI-compatible HTTP API from
`server/inference.py`.

## Protocol

The service follows `docs/protocol.md` WebSocket JSON protocol v0.1.

Supported inbound messages:

- `chat`
- `ping`

Supported outbound messages:

- `reply`
- `pong`
- `error`

The service accepts both shapes:

Preferred v0.1:

```json
{
  "version": "0.1",
  "type": "chat",
  "request_id": "req-001",
  "session_id": "phone-001",
  "timestamp": "2026-06-16T10:30:00+08:00",
  "payload": {
    "text": "帮我总结今天的任务"
  }
}
```

Current Android compatibility shape:

```json
{
  "type": "chat",
  "session_id": "phone-001",
  "text": "帮我总结今天的任务",
  "timestamp": "2026-06-16T10:30:00+08:00"
}
```

Responses include v0.1 fields plus top-level compatibility aliases that the
current Android parser can read:

```json
{
  "version": "0.1",
  "type": "reply",
  "request_id": "req-001",
  "session_id": "phone-001",
  "payload": {
    "text": "树莓派服务端已收到：帮我总结今天的任务",
    "finish_reason": "stop",
    "latency_ms": 12
  },
  "text": "树莓派服务端已收到：帮我总结今天的任务",
  "latency_ms": 12
}
```

## Error Codes

The server currently returns these shared v0.1 codes:

- `INVALID_JSON`
- `INVALID_REQUEST`
- `UNSUPPORTED_VERSION`
- `UNSUPPORTED_TYPE`
- `MODEL_BUSY`
- `MODEL_TIMEOUT`
- `INTERNAL_ERROR`

See `docs/error-codes.md` for the full cross-end list.

## Install Dependencies

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r server\requirements.txt
```

Linux / Raspberry Pi OS:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt
```

## Start the Real Server

From the project root:

```powershell
uvicorn server.app:app --host 0.0.0.0 --port 8765
```

Raspberry Pi OS:

```bash
uvicorn server.app:app --host 0.0.0.0 --port 8765
```

Android should connect to:

```text
ws://<raspberry-pi-lan-ip>:8765/ws
```

Example:

```text
ws://192.168.1.10:8765/ws
```

Replace `192.168.1.10` with the Raspberry Pi IP address shown by
`hostname -I`, `ip addr`, or the router client list.

## Health Check

```text
GET http://<raspberry-pi-lan-ip>:8765/healthz
```

Example response:

```json
{
  "status": "ok",
  "service": "raspberry-pi-voice-assistant-server",
  "active_connections": 0
}
```

## Test the Real Server

In one terminal:

```powershell
uvicorn server.app:app --host 0.0.0.0 --port 8765
```

In another terminal:

```powershell
python tools\ws_client_test.py --url ws://127.0.0.1:8765/ws
```

Expected:

- first response is `type=reply`
- second response is `type=pong`
- third response is `type=error` with `INVALID_REQUEST`

## Switch Between Mock and Real Server

Use the mock server when Android only needs to verify fixed-reply networking:

```powershell
python tools\ws_mock_server.py --host 0.0.0.0 --port 8765
```

Use the real server when testing the service code and inference boundary:

```powershell
uvicorn server.app:app --host 0.0.0.0 --port 8765
```

Only run one of them on port `8765` at a time.

## Model Integration Boundary

The WebSocket handler calls only:

```python
generate_reply(text: str, session_id: str) -> str
```

from `server/inference.py`.

`server/inference.py` calls the local OpenAI-compatible `llama-server` endpoint:

```text
http://127.0.0.1:18080/v1/chat/completions
```

If `llama-server` is not running, WebSocket chat requests return a structured
`MODEL_UNAVAILABLE` error instead of crashing the service.

## Start llama-server on Raspberry Pi

Run this on the Raspberry Pi before starting the WebSocket service:

```bash
cd ~/apps/llama.cpp/build/bin
./llama-server \
  -m ~/models/gemma4/gemma-4-E2B-it-Q4_K_M.gguf \
  --host 127.0.0.1 \
  --port 18080 \
  -t 3 \
  -c 2048 \
  --reasoning off
```

Direct model HTTP test:

```bash
curl -sS \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma-4-E2B-it","messages":[{"role":"user","content":"请直接用中文回答，不要展示思考过程。问题：你是谁？"}],"max_tokens":64,"temperature":0.7}' \
  http://127.0.0.1:18080/v1/chat/completions
```

Then start the WebSocket service:

```bash
cd ~/apps/voice-assistant
.venv/bin/uvicorn server.app:app --host 0.0.0.0 --port 8765
```

Thermal checks before and after model tests:

```bash
vcgencmd measure_temp
vcgencmd get_throttled
```

Stop load testing above `75°C`. Stop model testing and let the Pi cool above
`80°C`, especially when there is no active cooling.
## Manual Raspberry Pi Runbook

Start `llama-server`:

```bash
cd ~/apps/llama.cpp/build/bin
./llama-server \
  -m ~/models/gemma4/gemma-4-E2B-it-Q4_K_M.gguf \
  --host 127.0.0.1 \
  --port 18080 \
  -t 3 \
  -c 2048 \
  --reasoning off
```

Start the WebSocket service in another terminal:

```bash
cd ~/apps/voice-assistant
.venv/bin/uvicorn server.app:app --host 0.0.0.0 --port 8765
```

Check health:

```bash
curl http://127.0.0.1:8765/healthz
```

## systemd Deployment

Project-local unit templates live in `server/deploy/`:

- `voice-assistant-llama.service`
- `voice-assistant-websocket.service`
- `install-systemd.sh`

The WebSocket service has `Requires=` and `After=` dependencies on the
`llama-server` service. Both services run as user `duck`.

Review templates first. To install on the Raspberry Pi:

```bash
cd /home/duck/apps/voice-assistant/server/deploy
sudo bash install-systemd.sh
```

`sudo` is required because the script copies unit files to
`/etc/systemd/system/` and runs `systemctl daemon-reload`.

The script enables the services but does not start them. After stopping any
temporary manual processes, start with:

```bash
sudo systemctl start voice-assistant-llama.service
sudo systemctl start voice-assistant-websocket.service
```

Check status:

```bash
systemctl status voice-assistant-llama.service
systemctl status voice-assistant-websocket.service
```

## Android Device Test

1. Keep the phone and Raspberry Pi on the same reachable network.
2. Open the Android app.
3. Set the WebSocket URL to:

```text
ws://10.255.190.56:8765/ws
```

4. Use a short prompt first, for example `你是谁？`.
5. Check the Raspberry Pi temperature after each early test:

```bash
vcgencmd measure_temp
vcgencmd get_throttled
```

## Thermal and Troubleshooting

- If `/healthz` fails, check whether `uvicorn` is listening on `8765`.
- If chat returns `MODEL_UNAVAILABLE`, check whether `llama-server` is listening
  on `127.0.0.1:18080`.
- Keep `--reasoning off`; without it, model output may appear under
  `reasoning_content` instead of `message.content`.
- Stop continuous tests above `75C`.
- Stop model tests and let the Raspberry Pi cool above `80C`.
