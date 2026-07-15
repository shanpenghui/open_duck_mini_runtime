from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOL_VERSION = "0.1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT_DIR = PROJECT_ROOT / "data" / "vision_snapshots"
DEFAULT_CANDIDATES = tuple(f"/dev/video{i}" for i in range(12))


def realsense_status() -> dict[str, Any]:
    cv2, cv2_error = _import_module("cv2")
    return {
        "ok": True,
        "source": "realsense-opencv",
        "tool_version": TOOL_VERSION,
        "opencv_importable": cv2 is not None,
        "opencv_error": cv2_error,
        "video_devices": _list_video_devices(),
        "message": "Uses OpenCV/UVC color capture only. Depth is not used in this first visual test.",
        "robot_action_executed": False,
    }


def capture_rgb_snapshot(
    output_path: str | Path | None = None,
    device: str | int | None = None,
    width: int = 640,
    height: int = 480,
    warmup_frames: int = 8,
) -> dict[str, Any]:
    cv2, cv2_error = _import_module("cv2")
    if cv2 is None:
        return _error("opencv_not_installed", "opencv-python-headless is required.", detail=cv2_error)

    candidates = [device] if device is not None else list(_list_video_devices()) or list(DEFAULT_CANDIDATES)
    image_path = _resolve_output_path(output_path)
    errors: list[dict[str, str]] = []

    for candidate in candidates:
        cap = _open_capture(cv2, candidate)
        try:
            if not cap.isOpened():
                errors.append({"device": str(candidate), "error": "open_failed"})
                continue

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

            frame = None
            for _ in range(max(warmup_frames, 1)):
                ok, current = cap.read()
                if ok and current is not None:
                    frame = current
            if frame is None:
                errors.append({"device": str(candidate), "error": "read_failed"})
                continue

            image_path.parent.mkdir(parents=True, exist_ok=True)
            if not bool(cv2.imwrite(str(image_path), frame)):
                return _error("capture_failed", f"OpenCV failed to write image: {image_path}")

            actual_height, actual_width = frame.shape[:2]
            return {
                "ok": True,
                "source": "realsense-opencv",
                "image_path": str(image_path),
                "width": int(actual_width),
                "height": int(actual_height),
                "timestamp": _now_iso(),
                "camera": {
                    "device": str(candidate),
                    "requested_width": width,
                    "requested_height": height,
                    "mode": "opencv-uvc-color",
                },
                "robot_action_executed": False,
            }
        finally:
            cap.release()

    return _error(
        "camera_not_found",
        "No readable RealSense/UVC color stream was found.",
        attempted=[str(item) for item in candidates],
        errors=errors,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        payload = realsense_status()
    elif args.command == "capture":
        payload = capture_rgb_snapshot(
            output_path=args.output,
            device=args.device,
            width=args.width,
            height=args.height,
            warmup_frames=args.warmup_frames,
        )
    else:
        parser.error("missing command")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") is True or args.command == "status" else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.realsense_camera_tool",
        description="Read-only RealSense D435 color snapshot tool using OpenCV/UVC.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Print OpenCV and video device status.")
    capture_parser = subparsers.add_parser("capture", help="Capture one RGB snapshot from D435 color stream.")
    capture_parser.add_argument("--output", help="Output jpg path. Defaults to data/vision_snapshots/.")
    capture_parser.add_argument("--device", help="Video device, for example /dev/video0. Defaults to probing /dev/video*.")
    capture_parser.add_argument("--width", type=int, default=640, help="Requested capture width.")
    capture_parser.add_argument("--height", type=int, default=480, help="Requested capture height.")
    capture_parser.add_argument("--warmup-frames", type=int, default=8, help="Frames to discard before saving.")
    return parser


def _import_module(name: str) -> tuple[Any | None, str | None]:
    try:
        module = __import__(name)
    except Exception as exc:
        return None, str(exc)
    return module, None


def _list_video_devices() -> list[str]:
    return [str(path) for path in sorted(Path("/dev").glob("video*")) if path.exists()]


def _open_capture(cv2: Any, device: str | int) -> Any:
    if isinstance(device, str) and device.isdigit():
        return cv2.VideoCapture(int(device), cv2.CAP_V4L2)
    return cv2.VideoCapture(device, cv2.CAP_V4L2)


def _resolve_output_path(output_path: str | Path | None) -> Path:
    if output_path is not None:
        return Path(output_path).expanduser()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DEFAULT_SNAPSHOT_DIR / f"realsense_camera_{stamp}.jpg"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _error(error: str, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "source": "realsense-opencv",
        "error": error,
        "message": message,
        "timestamp": _now_iso(),
        "robot_action_executed": False,
    }
    payload.update(extra)
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
