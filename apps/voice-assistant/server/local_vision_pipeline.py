from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from server.orbbec_camera_tool import camera_status, capture_rgb_snapshot
from server.ncnn_vision_tool import detect_image_ncnn, ncnn_status
from server.onnx_vision_tool import detect_image_onnx, onnx_status
from server.yolo_vision_tool import detect_image, yolo_status


TOOL_VERSION = "0.1"
DEFAULT_YOLO_CONF = 0.35
DEFAULT_NCNN_CONF = 0.25
DEFAULT_ONNX_CONF = 0.25


def status() -> dict[str, Any]:
    return {
        "ok": True,
        "source": "local-vision",
        "tool_version": TOOL_VERSION,
        "camera": camera_status(),
        "yolo": yolo_status(),
        "ncnn": ncnn_status(),
        "onnx": onnx_status(),
        "robot_action_executed": False,
    }


def observe_once(
    image_path: str | Path | None = None,
    output_path: str | Path | None = None,
    backend: str = "yolo",
    conf: float | None = None,
    imgsz: int = 640,
    annotated_output_path: str | Path | None = None,
) -> dict[str, Any]:
    if backend not in ("yolo", "ncnn", "onnx"):
        return {
            "ok": False,
            "source": "local-vision",
            "error": "unsupported_backend",
            "message": "backend must be yolo, ncnn, or onnx.",
            "backend": backend,
            "robot_action_executed": False,
        }

    if image_path is None:
        image_result = capture_rgb_snapshot(output_path)
        if image_result.get("ok") is not True:
            return {
                "ok": False,
                "source": "local-vision",
                "image": image_result,
                "backend": backend,
                backend: None,
                "robot_action_executed": False,
            }
        detection_result = _detect_with_backend(
            backend,
            image_result["image_path"],
            conf=conf,
            imgsz=imgsz,
            annotated_output_path=annotated_output_path,
        )
    else:
        image = Path(image_path).expanduser()
        image_result = {
            "ok": image.is_file(),
            "source": "provided-image",
            "image_path": str(image),
        }
        detection_result = _detect_with_backend(
            backend,
            image_path,
            conf=conf,
            imgsz=imgsz,
            annotated_output_path=annotated_output_path,
        )

    payload = {
        "ok": detection_result.get("ok") is True,
        "source": "local-vision",
        "backend": backend,
        "image": image_result,
        backend: detection_result,
        "detection": detection_result,
        "robot_action_executed": False,
    }
    if backend != "yolo":
        payload["yolo"] = None
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        payload = status()
    elif args.command == "observe":
        payload = observe_once(
            image_path=args.image,
            output_path=args.output,
            backend=args.backend,
            conf=args.conf,
            imgsz=args.imgsz,
            annotated_output_path=args.annotated_output,
        )
    else:
        parser.error("missing command")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") is True or args.command == "status" else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.local_vision_pipeline",
        description="One-shot read-only local vision pipeline for OpenDuck.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Print camera, YOLO, NCNN, and ONNX status.")
    observe_parser = subparsers.add_parser("observe", help="Capture or read one image, then run local detection.")
    observe_parser.add_argument("--image", help="Existing image path. If omitted, capture one Orbbec snapshot.")
    observe_parser.add_argument("--output", help="Snapshot output path when --image is omitted.")
    observe_parser.add_argument("--backend", choices=("yolo", "ncnn", "onnx"), default="yolo", help="Detection backend.")
    observe_parser.add_argument("--conf", type=float, help="Confidence threshold. Defaults to backend-specific value.")
    observe_parser.add_argument("--imgsz", type=int, default=640, help="Inference image size.")
    observe_parser.add_argument("--annotated-output", help="Optional output path for a jpg with detection boxes.")
    return parser


def _detect_with_backend(
    backend: str,
    image_path: str | Path,
    conf: float | None,
    imgsz: int,
    annotated_output_path: str | Path | None,
) -> dict[str, Any]:
    if backend == "ncnn":
        return detect_image_ncnn(
            image_path,
            conf=DEFAULT_NCNN_CONF if conf is None else conf,
            imgsz=imgsz,
            annotated_output_path=annotated_output_path,
        )
    if backend == "onnx":
        return detect_image_onnx(
            image_path,
            conf=DEFAULT_ONNX_CONF if conf is None else conf,
            imgsz=imgsz,
            annotated_output_path=annotated_output_path,
        )
    return detect_image(
        image_path,
        conf=DEFAULT_YOLO_CONF if conf is None else conf,
        imgsz=imgsz,
        annotated_output_path=annotated_output_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
