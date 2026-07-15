import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse

from server.config import APP_NAME, INFERENCE_TIMEOUT_SECONDS, REJECT_WHEN_MODEL_BUSY
from server.connection_manager import ConnectionManager
from server.inference import InferenceError, generate_reply
from server.robot_commands import build_direct_robot_command
from server.robot_commands import match_robot_command
from server.robot_executor import execute_robot_command
from server.schemas import (
    ChatRequest,
    ProtocolError,
    RobotCommandRequest,
    error_response,
    parse_client_message,
    pong_response,
    reply_response,
)
from server.video_stream import MJPEG_BOUNDARY, latest_jpeg_snapshot, mjpeg_frame_generator, video_status_payload


CLIENT_REPLY_DEADLINE_SECONDS = 90.0

app = FastAPI(title=APP_NAME)
manager = ConnectionManager()
inference_lock = asyncio.Lock()
logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
_detection_cache: dict[str, Any] = {}
_detection_lock = asyncio.Lock()


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    return {
        "status": "ok",
        "service": APP_NAME,
        "active_connections": manager.active_count,
    }


@app.get("/video/status")
async def video_status(
    device: str | None = None,
    probe: bool = False,
) -> dict[str, object]:
    return video_status_payload(device=device, probe=probe)


@app.get("/video/stream")
async def video_stream(
    device: str | None = None,
    width: int = Query(640, ge=160, le=1920),
    height: int = Query(480, ge=120, le=1080),
    fps: int = Query(15, ge=1, le=30),
    quality: int = Query(80, ge=40, le=95),
) -> StreamingResponse:
    return StreamingResponse(
        mjpeg_frame_generator(
            device=device,
            width=width,
            height=height,
            fps=fps,
            quality=quality,
        ),
        media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/video/detections/latest")
async def video_detections_latest(
    device: str | None = "/dev/video2",
    width: int = Query(640, ge=160, le=1920),
    height: int = Query(480, ge=120, le=1080),
    fps: int = Query(8, ge=1, le=15),
    quality: int = Query(75, ge=40, le=90),
    backend: str = Query("onnx", pattern="^(onnx|ncnn|yolo)$"),
    conf: float = Query(0.25, ge=0.05, le=0.9),
    imgsz: int = Query(640, ge=160, le=1280),
    min_interval: float = Query(5.0, ge=2.0, le=30.0),
) -> dict[str, Any]:
    now = time.monotonic()
    cache_key = f"{device}|{width}|{height}|{fps}|{quality}|{backend}|{conf:.3f}|{imgsz}"
    cached = _detection_cache.get(cache_key)
    if isinstance(cached, dict) and now - float(cached.get("_monotonic", 0.0)) < min_interval:
        payload = dict(cached)
        payload.pop("_monotonic", None)
        payload["cached"] = True
        return payload

    if _detection_lock.locked() and isinstance(cached, dict):
        payload = dict(cached)
        payload.pop("_monotonic", None)
        payload["cached"] = True
        payload["busy"] = True
        return payload

    async with _detection_lock:
        cached = _detection_cache.get(cache_key)
        if isinstance(cached, dict) and time.monotonic() - float(cached.get("_monotonic", 0.0)) < min_interval:
            payload = dict(cached)
            payload.pop("_monotonic", None)
            payload["cached"] = True
            return payload

        payload = await asyncio.to_thread(
            _run_video_detection_once,
            device=device,
            width=width,
            height=height,
            fps=fps,
            quality=quality,
            backend=backend,
            conf=conf,
            imgsz=imgsz,
        )
        payload["_monotonic"] = time.monotonic()
        _detection_cache[cache_key] = payload
        response = dict(payload)
        response.pop("_monotonic", None)
        response["cached"] = False
        return response


@app.get("/video/view", response_class=HTMLResponse)
async def video_view(
    device: str | None = "/dev/video2",
    width: int = Query(640, ge=160, le=1920),
    height: int = Query(480, ge=120, le=1080),
    fps: int = Query(8, ge=1, le=15),
    quality: int = Query(75, ge=40, le=90),
    backend: str = Query("onnx", pattern="^(onnx|ncnn|yolo)$"),
    poll_ms: int = Query(2500, ge=1000, le=10000),
    min_interval: float = Query(5.0, ge=2.0, le=30.0),
) -> str:
    query = (
        f"device={device or ''}&width={width}&height={height}&fps={fps}"
        f"&quality={quality}&backend={backend}&min_interval={min_interval}"
    )
    stream_url = f"/video/stream?device={device or ''}&width={width}&height={height}&fps={fps}&quality={quality}"
    detection_url = f"/video/detections/latest?{query}"
    return _video_overlay_html(stream_url=stream_url, detection_url=detection_url, poll_ms=poll_ms)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    peer = websocket.client
    logger.info("websocket connected peer=%s", peer)
    try:
        while True:
            raw_message = await websocket.receive_text()
            started_at = time.perf_counter()
            request_id = None
            session_id = None

            try:
                request = parse_client_message(raw_message)
                request_id = request.request_id

                if not isinstance(request, ChatRequest):
                    if isinstance(request, RobotCommandRequest):
                        command = build_direct_robot_command(
                            request.command_id,
                            params=request.params,
                            priority=request.priority,
                        )
                        if command is None:
                            if not await _safe_send_json(
                                websocket,
                                _client_error_response(
                                    "UNSUPPORTED_ROBOT_COMMAND",
                                    "\u4e0d\u652f\u6301\u7684\u673a\u5668\u4eba\u76f4\u63a7\u547d\u4ee4",
                                    request_id=request.request_id,
                                    session_id=request.session_id,
                                ),
                                peer,
                            ):
                                return
                            continue
                        result = await asyncio.to_thread(
                            execute_robot_command,
                            command,
                        )
                        latency_ms = int((time.perf_counter() - started_at) * 1000)
                        if not await _safe_send_json(
                            websocket,
                            reply_response(
                                request_id=request.request_id,
                                session_id=request.session_id or "robot-control",
                                text=result.reply_text,
                                latency_ms=latency_ms,
                            ),
                            peer,
                        ):
                            return
                        continue

                    if not await _safe_send_json(websocket, pong_response(request.request_id), peer):
                        return
                    continue

                session_id = request.session_id
                robot_command = match_robot_command(request.text)
                if robot_command is not None:
                    result = await asyncio.to_thread(
                        execute_robot_command,
                        robot_command,
                    )
                    latency_ms = int((time.perf_counter() - started_at) * 1000)
                    if not await _safe_send_json(
                        websocket,
                        reply_response(
                            request_id=request.request_id,
                            session_id=request.session_id,
                            text=result.reply_text,
                            latency_ms=latency_ms,
                        ),
                        peer,
                    ):
                        return
                    continue

                if REJECT_WHEN_MODEL_BUSY and inference_lock.locked():
                    if not await _safe_send_json(
                        websocket,
                        _client_error_response(
                            "MODEL_BUSY",
                            "\u672c\u5730\u6a21\u578b\u6b63\u5728\u5904\u7406\u5176\u4ed6\u8bf7\u6c42",
                            request_id=request.request_id,
                            session_id=request.session_id,
                            retryable=True,
                        ),
                        peer,
                    ):
                        return
                    continue

                model_timeout_s = min(INFERENCE_TIMEOUT_SECONDS, CLIENT_REPLY_DEADLINE_SECONDS)
                async with inference_lock:
                    reply_text = await asyncio.wait_for(
                        asyncio.to_thread(
                            generate_reply,
                            request.text,
                            request.session_id,
                        ),
                        timeout=model_timeout_s,
                    )

                latency_ms = int((time.perf_counter() - started_at) * 1000)
                if not await _safe_send_json(
                    websocket,
                    reply_response(
                        request_id=request.request_id,
                        session_id=request.session_id,
                        text=reply_text,
                        latency_ms=latency_ms,
                    ),
                    peer,
                ):
                    return
            except ProtocolError as exc:
                if not await _safe_send_json(
                    websocket,
                    _client_error_response(
                        exc.code,
                        exc.message,
                        request_id=exc.request_id or request_id,
                        session_id=exc.session_id or session_id,
                        retryable=exc.retryable,
                    ),
                    peer,
                ):
                    return
            except (asyncio.TimeoutError, TimeoutError):
                if not await _safe_send_json(
                    websocket,
                    _client_error_response(
                        "MODEL_TIMEOUT",
                        "\u6a21\u578b90\u79d2\u5185\u6ca1\u6709\u56de\u590d\uff0c\u5df2\u8d85\u8fc7\u7b49\u5f85\u65f6\u95f4\uff0c\u8bf7\u6362\u4e00\u4e2a\u66f4\u77ed\u7684\u95ee\u9898\uff0c\u6216\u7a0d\u540e\u518d\u8bd5\u3002",
                        request_id=request_id,
                        session_id=session_id,
                        retryable=True,
                    ),
                    peer,
                ):
                    return
            except InferenceError as exc:
                if not await _safe_send_json(
                    websocket,
                    _client_error_response(
                        exc.code,
                        exc.message,
                        request_id=request_id,
                        session_id=session_id,
                        retryable=exc.retryable,
                    ),
                    peer,
                ):
                    return
            except Exception:
                logger.exception("websocket request failed peer=%s", peer)
                if not await _safe_send_json(
                    websocket,
                    _client_error_response(
                        "INTERNAL_ERROR",
                        "\u670d\u52a1\u7aef\u5904\u7406\u8bf7\u6c42\u5931\u8d25",
                        request_id=request_id,
                        session_id=session_id,
                        retryable=True,
                    ),
                    peer,
                ):
                    return
    except WebSocketDisconnect:
        logger.info("websocket disconnected peer=%s", peer)
    finally:
        manager.disconnect(websocket)


async def _safe_send_json(websocket: WebSocket, payload: dict[str, Any], peer: object) -> bool:
    try:
        await websocket.send_json(payload)
        return True
    except WebSocketDisconnect:
        logger.info("websocket send skipped after disconnect peer=%s", peer)
    except RuntimeError as exc:
        logger.info("websocket send skipped peer=%s error=%s", peer, exc)
    except OSError as exc:
        logger.info("websocket send failed peer=%s error=%s", peer, exc)
    return False


def _client_error_response(
    code: str,
    message: str,
    *,
    request_id: str | None = None,
    session_id: str | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    payload = error_response(
        code,
        message,
        request_id=request_id,
        session_id=session_id,
        retryable=retryable,
    )
    payload["text"] = message
    return payload


def _run_video_detection_once(
    *,
    device: str | None,
    width: int,
    height: int,
    fps: int,
    quality: int,
    backend: str,
    conf: float,
    imgsz: int,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    snapshot = latest_jpeg_snapshot(
        device=device,
        width=width,
        height=height,
        fps=fps,
        quality=quality,
        wait_s=3.0,
    )
    if snapshot.get("ok") is not True:
        return _detection_error("snapshot_failed", snapshot.get("message") or snapshot.get("error"), backend=backend, snapshot=snapshot)

    output_root = PROJECT_ROOT / "data" / "vision" / "realtime"
    output_root.mkdir(parents=True, exist_ok=True)
    frame_path = output_root / "latest_detection_frame.jpg"
    frame_path.write_bytes(snapshot["jpeg"])

    detection = _detect_image_for_video(backend=backend, image_path=frame_path, conf=conf, imgsz=imgsz)
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    if detection.get("ok") is not True:
        return _detection_error(
            str(detection.get("error") or "detect_failed"),
            str(detection.get("message") or detection.get("error") or "detection failed"),
            backend=backend,
            elapsed_ms=elapsed_ms,
            frame_path=str(frame_path),
            raw_detection=detection,
        )

    objects = detection.get("objects") if isinstance(detection.get("objects"), list) else []
    payload = {
        "ok": True,
        "source": "video-detections",
        "backend": backend,
        "timestamp": _now_iso(),
        "elapsed_ms": elapsed_ms,
        "image_width": snapshot.get("width"),
        "image_height": snapshot.get("height"),
        "selected_device": snapshot.get("selected_device"),
        "frame_path": str(frame_path),
        "objects": objects,
        "count": len(objects),
        "robot_action_executed": False,
    }
    (output_root / "latest_detection.json").write_text(
        __import__("json").dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def _detect_image_for_video(*, backend: str, image_path: Path, conf: float, imgsz: int) -> dict[str, Any]:
    if backend == "ncnn":
        from server.ncnn_vision_tool import detect_image_ncnn

        return detect_image_ncnn(image_path, conf=conf, imgsz=imgsz)
    if backend == "yolo":
        from server.yolo_vision_tool import detect_image

        return detect_image(image_path, conf=conf, imgsz=imgsz)

    from server.onnx_vision_tool import detect_image_onnx

    return detect_image_onnx(image_path, conf=conf, imgsz=imgsz)


def _detection_error(error: str, message: Any, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "source": "video-detections",
        "error": error,
        "message": str(message),
        "timestamp": _now_iso(),
        "objects": [],
        "count": 0,
        "robot_action_executed": False,
    }
    payload.update(extra)
    return payload


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _video_overlay_html(*, stream_url: str, detection_url: str, poll_ms: int) -> str:
    return f"""<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    html, body {{ margin: 0; width: 100%; height: 100%; overflow: hidden; background: #000; }}
    #wrap {{ position: relative; width: 100vw; height: 100vh; background: #000; }}
    #stream, #overlay {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }}
    #status {{ position: absolute; left: 10px; top: 8px; z-index: 3; color: white; font: 14px sans-serif; background: rgba(0,0,0,.55); padding: 5px 8px; border-radius: 6px; }}
  </style>
</head>
<body>
  <div id="wrap">
    <img id="stream" src="{stream_url}" alt="OpenDuck camera stream">
    <canvas id="overlay"></canvas>
    <div id="status">vision starting</div>
  </div>
  <script>
    const img = document.getElementById('stream');
    const canvas = document.getElementById('overlay');
    const statusEl = document.getElementById('status');
    const ctx = canvas.getContext('2d');
    const detectionUrl = "{detection_url}";
    const pollMs = {poll_ms};
    let latest = null;

    function resizeCanvas() {{
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.round(rect.width * dpr));
      canvas.height = Math.max(1, Math.round(rect.height * dpr));
      draw();
    }}

    function imageToCanvas(box, imageWidth, imageHeight) {{
      const cw = canvas.width;
      const ch = canvas.height;
      const scale = Math.max(cw / imageWidth, ch / imageHeight);
      const ox = (cw - imageWidth * scale) / 2;
      const oy = (ch - imageHeight * scale) / 2;
      return [
        ox + box[0] * scale,
        oy + box[1] * scale,
        ox + box[2] * scale,
        oy + box[3] * scale,
      ];
    }}

    function draw() {{
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (!latest || !latest.ok) return;
      const imageWidth = latest.image_width || img.naturalWidth || 640;
      const imageHeight = latest.image_height || img.naturalHeight || 480;
      const objects = latest.objects || [];
      ctx.lineWidth = Math.max(2, canvas.width / 320);
      ctx.font = `${{Math.max(15, canvas.width / 42)}}px sans-serif`;
      ctx.textBaseline = 'top';
      for (const obj of objects) {{
        const box = obj.box_xyxy;
        if (!box || box.length !== 4) continue;
        const [x1, y1, x2, y2] = imageToCanvas(box, imageWidth, imageHeight);
        const label = `${{obj.label || 'object'}} ${{obj.confidence ? Number(obj.confidence).toFixed(2) : ''}}`;
        ctx.strokeStyle = '#00e5ff';
        ctx.fillStyle = 'rgba(0, 0, 0, 0.62)';
        ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
        const metrics = ctx.measureText(label);
        const labelH = Math.max(22, canvas.width / 34);
        ctx.fillRect(x1, Math.max(0, y1 - labelH), metrics.width + 12, labelH);
        ctx.fillStyle = '#ffffff';
        ctx.fillText(label, x1 + 6, Math.max(2, y1 - labelH + 3));
      }}
    }}

    async function poll() {{
      try {{
        const res = await fetch(detectionUrl, {{ cache: 'no-store' }});
        latest = await res.json();
        if (latest.ok) {{
          statusEl.textContent = `${{latest.backend || 'onnx'}} objects=${{latest.count || 0}} ${{latest.cached ? 'cached' : latest.elapsed_ms + 'ms'}}`;
        }} else {{
          statusEl.textContent = `vision error: ${{latest.error || 'unknown'}}`;
        }}
        draw();
      }} catch (err) {{
        statusEl.textContent = 'vision offline';
      }}
    }}

    window.addEventListener('resize', resizeCanvas);
    img.addEventListener('load', resizeCanvas);
    resizeCanvas();
    poll();
    setInterval(poll, pollMs);
  </script>
</body>
</html>"""
