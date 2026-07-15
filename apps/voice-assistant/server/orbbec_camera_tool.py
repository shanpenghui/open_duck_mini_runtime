from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOL_VERSION = "0.1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT_DIR = PROJECT_ROOT / "data" / "vision_snapshots"


def camera_status() -> dict[str, Any]:
    sdk, import_error = _import_pyorbbecsdk()
    payload: dict[str, Any] = {
        "ok": True,
        "source": "orbbec-camera",
        "tool_version": TOOL_VERSION,
        "pyorbbecsdk_importable": sdk is not None,
        "pyorbbecsdk_error": import_error,
        "device_detected": False,
        "device_count": 0,
        "devices": [],
        "robot_action_executed": False,
    }
    if sdk is None:
        payload["available"] = False
        payload["message"] = "pyorbbecsdk is not installed. Install the official pyorbbecsdk2 package first."
        return payload

    try:
        _set_orbbec_warning_log(sdk)
        context = sdk.Context()
        device_list = context.query_devices()
        count = int(device_list.get_count())
        payload["device_count"] = count
        payload["device_detected"] = count > 0
        payload["devices"] = [_device_summary(device_list.get_device_by_index(i)) for i in range(count)]
        payload["available"] = count > 0
        if count == 0:
            payload["message"] = "No Orbbec camera was detected."
    except Exception as exc:
        payload["available"] = False
        payload["error"] = "camera_status_failed"
        payload["message"] = str(exc)
    return payload


def capture_rgb_snapshot(output_path: str | Path | None = None) -> dict[str, Any]:
    sdk, import_error = _import_pyorbbecsdk()
    if sdk is None:
        return _error(
            "pyorbbecsdk_not_installed",
            "pyorbbecsdk is not installed. Install the official pyorbbecsdk2 package first.",
            detail=import_error,
        )

    numpy_module, numpy_error = _import_module("numpy")
    if numpy_module is None:
        return _error("numpy_not_installed", "numpy is required to convert camera frames.", detail=numpy_error)

    cv2, cv2_error = _import_module("cv2")
    if cv2 is None:
        return _error("opencv_not_installed", "opencv-python-headless is required to save snapshots.", detail=cv2_error)

    try:
        _set_orbbec_warning_log(sdk)
        context = sdk.Context()
        device_list = context.query_devices()
        if int(device_list.get_count()) == 0:
            return _error("camera_not_found", "No Orbbec Gemini 2 camera was detected.")
        camera = _device_summary(device_list.get_device_by_index(0))
    except Exception as exc:
        return _error("camera_not_found", f"Failed to query Orbbec devices: {exc}")

    image_path = _resolve_output_path(output_path)
    pipeline = sdk.Pipeline()
    started = False
    try:
        pipeline.start()
        started = True
        color_frame = None
        for _ in range(30):
            frames = pipeline.wait_for_frames(1000)
            if frames is None:
                continue
            color_frame = frames.get_color_frame()
            if color_frame is not None:
                break
        if color_frame is None:
            return _error("capture_failed", "Timed out while waiting for a color frame.")

        width = int(color_frame.get_width())
        height = int(color_frame.get_height())
        bgr_image = _color_frame_to_bgr(color_frame, width, height, numpy_module, cv2)
        image_path.parent.mkdir(parents=True, exist_ok=True)
        saved = bool(cv2.imwrite(str(image_path), bgr_image))
        if not saved:
            return _error("capture_failed", f"OpenCV failed to write image: {image_path}")

        return {
            "ok": True,
            "source": "orbbec-camera",
            "image_path": str(image_path),
            "width": width,
            "height": height,
            "timestamp": _now_iso(),
            "camera": camera,
            "robot_action_executed": False,
        }
    except Exception as exc:
        return _error("capture_failed", str(exc), camera=camera)
    finally:
        if started:
            try:
                pipeline.stop()
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        payload = camera_status()
    elif args.command == "capture":
        payload = capture_rgb_snapshot(args.output)
    else:
        parser.error("missing command")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") is True or args.command == "status" else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.orbbec_camera_tool",
        description="Read-only Orbbec Gemini 2 camera snapshot tool for OpenDuck.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Print camera and SDK status.")
    capture_parser = subparsers.add_parser("capture", help="Capture one RGB snapshot.")
    capture_parser.add_argument("--output", help="Output jpg path. Defaults to data/vision_snapshots/.")
    return parser


def _import_pyorbbecsdk() -> tuple[Any | None, str | None]:
    module, error = _import_module("pyorbbecsdk")
    return module, error


def _import_module(name: str) -> tuple[Any | None, str | None]:
    try:
        module = __import__(name)
    except Exception as exc:
        return None, str(exc)
    return module, None


def _set_orbbec_warning_log(sdk: Any) -> None:
    try:
        sdk.Context.set_logger_to_console(sdk.OBLogLevel.WARNING)
    except Exception:
        pass


def _device_summary(device: Any) -> dict[str, Any]:
    try:
        info = device.get_device_info()
    except Exception as exc:
        return {"error": f"device_info_failed: {exc}"}
    return {
        "name": _safe_call(info, "get_name"),
        "serial_number": _safe_call(info, "get_serial_number"),
        "firmware_version": _safe_call(info, "get_firmware_version"),
        "vid": _safe_call(info, "get_vid"),
        "pid": _safe_call(info, "get_pid"),
    }


def _safe_call(obj: Any, method_name: str) -> Any:
    method = getattr(obj, method_name, None)
    if method is None:
        return None
    try:
        return method()
    except Exception:
        return None


def _color_frame_to_bgr(color_frame: Any, width: int, height: int, np: Any, cv2: Any) -> Any:
    raw = np.frombuffer(color_frame.get_data(), dtype=np.uint8)
    frame_format = str(_safe_call(color_frame, "get_format") or "").upper()

    decoded = None
    if "MJPG" in frame_format or "MJPEG" in frame_format or "JPEG" in frame_format:
        decoded = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if decoded is None:
            raise RuntimeError(f"failed to decode compressed color frame: {frame_format}")
        return decoded

    if raw.size == width * height * 3:
        image = raw.reshape((height, width, 3))
        if "BGR" in frame_format:
            return image
        return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

    if raw.size == width * height * 2:
        image = raw.reshape((height, width, 2))
        return cv2.cvtColor(image, cv2.COLOR_YUV2BGR_YUY2)

    raise RuntimeError(
        f"unsupported color frame format={frame_format or 'unknown'} bytes={raw.size} size={width}x{height}"
    )


def _resolve_output_path(output_path: str | Path | None) -> Path:
    if output_path is not None:
        return Path(output_path).expanduser()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DEFAULT_SNAPSHOT_DIR / f"openduck_camera_{stamp}.jpg"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _error(error: str, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "source": "orbbec-camera",
        "error": error,
        "message": message,
        "timestamp": _now_iso(),
        "robot_action_executed": False,
    }
    for key, value in extra.items():
        if value is not None:
            payload[key] = value
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
