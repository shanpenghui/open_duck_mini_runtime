from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any


TOOL_VERSION = "0.1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_CANDIDATES = (
    PROJECT_ROOT / "models" / "yolo11n.pt",
    PROJECT_ROOT / "model" / "yolo11n.pt",
    Path.home() / ".openduck" / "models" / "yolo11n.pt",
)


def yolo_status() -> dict[str, Any]:
    ultralytics_found = importlib.util.find_spec("ultralytics") is not None
    cv2_found = importlib.util.find_spec("cv2") is not None
    model_path = _find_default_model()
    return {
        "ok": True,
        "source": "yolo",
        "tool_version": TOOL_VERSION,
        "ultralytics_importable": ultralytics_found,
        "ultralytics_error": None if ultralytics_found else "No module named 'ultralytics'",
        "opencv_importable": cv2_found,
        "opencv_error": None if cv2_found else "No module named 'cv2'",
        "default_model_candidates": [str(path) for path in DEFAULT_MODEL_CANDIDATES],
        "default_model_path": str(model_path) if model_path is not None else None,
        "model_found": model_path is not None,
        "auto_download": False,
        "robot_action_executed": False,
    }


def detect_image(
    image_path: str | Path,
    model_path: str | Path | None = None,
    conf: float = 0.35,
    imgsz: int = 640,
    annotated_output_path: str | Path | None = None,
) -> dict[str, Any]:
    image = Path(image_path).expanduser()
    if not image.is_file():
        return _error("image_not_found", f"Image file does not exist: {image}", image_path=str(image))

    ultralytics, ultralytics_error = _import_module("ultralytics")
    if ultralytics is None:
        return _error(
            "ultralytics_not_installed",
            "ultralytics is not installed. Install it before running local YOLO detection.",
            detail=ultralytics_error,
            image_path=str(image),
        )

    resolved_model = _resolve_model_path(model_path)
    if resolved_model is None:
        return _error(
            "model_not_found",
            "YOLO model file was not found. Put yolo11n.pt under models/, model/, or ~/.openduck/models/.",
            image_path=str(image),
            model_candidates=[str(path) for path in DEFAULT_MODEL_CANDIDATES],
        )

    try:
        model = ultralytics.YOLO(str(resolved_model))
        results = model.predict(source=str(image), conf=conf, imgsz=imgsz, verbose=False)
        objects = _objects_from_results(results)
        payload = {
            "ok": True,
            "source": "yolo",
            "model": str(resolved_model),
            "image_path": str(image),
            "objects": objects,
            "count": len(objects),
            "conf": conf,
            "imgsz": imgsz,
            "robot_action_executed": False,
        }
        if annotated_output_path is not None:
            annotated = _save_annotated_image(results, annotated_output_path)
            payload["annotated_image_path"] = str(annotated)
        return payload
    except Exception as exc:
        return _error(
            "detect_failed",
            str(exc),
            image_path=str(image),
            model=str(resolved_model),
        )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        payload = yolo_status()
    elif args.command == "detect":
        payload = detect_image(
            args.image,
            model_path=args.model,
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
        prog="python -m server.yolo_vision_tool",
        description="Read-only local YOLO object detector for OpenDuck.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Print dependency and model status.")
    detect_parser = subparsers.add_parser("detect", help="Detect objects in one image.")
    detect_parser.add_argument("--image", required=True, help="Input image path.")
    detect_parser.add_argument("--model", help="YOLO model path. Defaults to local yolo11n.pt candidates.")
    detect_parser.add_argument("--conf", type=float, default=0.35, help="Confidence threshold.")
    detect_parser.add_argument("--imgsz", type=int, default=640, help="Inference image size.")
    detect_parser.add_argument("--annotated-output", help="Optional output path for a jpg with detection boxes.")
    return parser


def _import_module(name: str) -> tuple[Any | None, str | None]:
    try:
        module = __import__(name)
    except Exception as exc:
        return None, str(exc)
    return module, None


def _resolve_model_path(model_path: str | Path | None) -> Path | None:
    if model_path is not None:
        path = Path(model_path).expanduser()
        return path if path.is_file() or path.is_dir() else None
    return _find_default_model()


def _find_default_model() -> Path | None:
    for path in DEFAULT_MODEL_CANDIDATES:
        expanded = path.expanduser()
        if expanded.is_file() or expanded.is_dir():
            return expanded
    return None


def _objects_from_results(results: Any) -> list[dict[str, Any]]:
    if not results:
        return []
    result = results[0]
    names = getattr(result, "names", {}) or {}
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []

    objects: list[dict[str, Any]] = []
    for box in boxes:
        class_id = int(box.cls[0].item())
        confidence = float(box.conf[0].item())
        xyxy = box.xyxy[0].tolist()
        label = names.get(class_id, str(class_id)) if isinstance(names, dict) else str(class_id)
        objects.append(
            {
                "label": str(label),
                "confidence": round(confidence, 4),
                "box_xyxy": [int(round(float(value))) for value in xyxy],
            }
        )
    return objects


def _save_annotated_image(results: Any, output_path: str | Path) -> Path:
    if not results:
        raise RuntimeError("YOLO returned no results to annotate.")
    cv2, cv2_error = _import_module("cv2")
    if cv2 is None:
        raise RuntimeError(f"OpenCV is required to save annotated images: {cv2_error}")
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    annotated = results[0].plot()
    if not bool(cv2.imwrite(str(output), annotated)):
        raise RuntimeError(f"OpenCV failed to write annotated image: {output}")
    return output


def _error(error: str, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "source": "yolo",
        "error": error,
        "message": message,
        "robot_action_executed": False,
    }
    payload.update(extra)
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
