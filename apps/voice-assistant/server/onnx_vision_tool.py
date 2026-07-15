from __future__ import annotations

import argparse
import ast
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOL_VERSION = "0.1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_NAME = "yolo11n.onnx"
DEFAULT_MODEL_CANDIDATES = (
    PROJECT_ROOT / "models" / DEFAULT_MODEL_NAME,
    PROJECT_ROOT / "model" / DEFAULT_MODEL_NAME,
    Path.home() / ".openduck" / "models" / DEFAULT_MODEL_NAME,
    Path("/home/duck/.openduck/models") / DEFAULT_MODEL_NAME,
)
COCO80_NAMES = (
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
)


def onnx_status() -> dict[str, Any]:
    onnxruntime, onnxruntime_error = _import_module("onnxruntime")
    cv2, cv2_error = _import_module("cv2")
    model_path = _find_default_model()
    return {
        "ok": True,
        "source": "onnx",
        "tool_version": TOOL_VERSION,
        "onnxruntime_importable": onnxruntime is not None,
        "onnxruntime_error": onnxruntime_error,
        "opencv_importable": cv2 is not None,
        "opencv_error": cv2_error,
        "default_model_candidates": [str(path) for path in DEFAULT_MODEL_CANDIDATES],
        "default_model_path": str(model_path) if model_path is not None else None,
        "model_found": model_path is not None,
        "robot_action_executed": False,
    }


def detect_image_onnx(
    image_path: str | Path,
    model_path: str | Path | None = None,
    conf: float = 0.25,
    imgsz: int = 640,
    annotated_output_path: str | Path | None = None,
    threads: int = 1,
) -> dict[str, Any]:
    image = Path(image_path).expanduser()
    if not image.is_file():
        return _error("image_not_found", f"Image file does not exist: {image}", image_path=str(image))

    onnxruntime, onnxruntime_error = _import_module("onnxruntime")
    if onnxruntime is None:
        return _error(
            "onnxruntime_not_installed",
            "onnxruntime is not installed. Install it before running ONNX detection.",
            detail=onnxruntime_error,
            image_path=str(image),
        )

    cv2, cv2_error = _import_module("cv2")
    if cv2 is None:
        return _error(
            "opencv_not_installed",
            "opencv-python-headless is required for image loading and annotation.",
            detail=cv2_error,
            image_path=str(image),
        )

    resolved_model = _resolve_model_path(model_path)
    if resolved_model is None:
        return _error(
            "model_not_found",
            "ONNX model file was not found. Put yolo11n.onnx under models/, model/, or ~/.openduck/models/.",
            image_path=str(image),
            model_candidates=[str(path) for path in DEFAULT_MODEL_CANDIDATES],
        )

    try:
        session_options = onnxruntime.SessionOptions()
        session_options.intra_op_num_threads = max(1, int(threads))
        session_options.inter_op_num_threads = 1
        session_options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
        session = onnxruntime.InferenceSession(
            str(resolved_model),
            sess_options=session_options,
            providers=["CPUExecutionProvider"],
        )
        input_info = session.get_inputs()[0]
        input_name = input_info.name
        model_imgsz = _input_size_from_shape(input_info.shape, imgsz)

        original = cv2.imread(str(image))
        if original is None:
            return _error("image_not_found", f"OpenCV failed to read image: {image}", image_path=str(image))

        letterboxed, ratio, pad_x, pad_y = _letterbox(cv2, original, model_imgsz)
        input_tensor = _image_to_tensor(cv2, letterboxed)
        outputs = session.run(None, {input_name: input_tensor})
        labels = _load_labels(session)
        objects = _objects_from_output(
            outputs,
            labels=labels,
            conf=conf,
            image_width=int(original.shape[1]),
            image_height=int(original.shape[0]),
            ratio=ratio,
            pad_x=pad_x,
            pad_y=pad_y,
        )
        payload = {
            "ok": True,
            "source": "onnx",
            "model": resolved_model.name,
            "model_path": str(resolved_model),
            "image_path": str(image),
            "objects": objects,
            "count": len(objects),
            "conf": conf,
            "imgsz": model_imgsz,
            "threads": max(1, int(threads)),
            "robot_action_executed": False,
        }
        if annotated_output_path is not None:
            annotated = _save_annotated_image(cv2, original, objects, annotated_output_path)
            payload["annotated_image_path"] = str(annotated)
        return payload
    except Exception as exc:
        return _error(
            "detect_failed",
            str(exc),
            image_path=str(image),
            model_path=str(resolved_model),
        )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        payload = onnx_status()
    elif args.command == "detect":
        payload = detect_image_onnx(
            args.image,
            model_path=args.model,
            conf=args.conf,
            imgsz=args.imgsz,
            annotated_output_path=args.annotated_output,
            threads=args.threads,
        )
    else:
        parser.error("missing command")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") is True or args.command == "status" else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.onnx_vision_tool",
        description="Read-only ONNXRuntime object detector for OpenDuck.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Print dependency and ONNX model status.")
    detect_parser = subparsers.add_parser("detect", help="Detect objects in one image with ONNXRuntime.")
    detect_parser.add_argument("--image", required=True, help="Input image path.")
    detect_parser.add_argument("--model", help="ONNX model path. Defaults to local yolo11n.onnx candidates.")
    detect_parser.add_argument("--conf", type=float, default=0.25, help="Confidence threshold.")
    detect_parser.add_argument("--imgsz", type=int, default=640, help="Inference image size.")
    detect_parser.add_argument("--annotated-output", help="Optional output path for a jpg with detection boxes.")
    detect_parser.add_argument("--threads", type=int, default=1, help="ONNXRuntime CPU worker threads. Defaults to 1 for Raspberry Pi stability.")
    return parser


def _import_module(name: str) -> tuple[Any | None, str | None]:
    try:
        if importlib.util.find_spec(name) is None:
            return None, f"No module named '{name}'"
        module = __import__(name)
    except Exception as exc:
        return None, str(exc)
    return module, None


def _resolve_model_path(model_path: str | Path | None) -> Path | None:
    if model_path is not None:
        path = Path(model_path).expanduser()
        return path if path.is_file() else None
    return _find_default_model()


def _find_default_model() -> Path | None:
    for path in DEFAULT_MODEL_CANDIDATES:
        expanded = path.expanduser()
        if expanded.is_file():
            return expanded
    return None


def _input_size_from_shape(shape: list[Any], fallback: int) -> int:
    if len(shape) >= 4 and isinstance(shape[2], int) and isinstance(shape[3], int) and shape[2] == shape[3]:
        return int(shape[2])
    return fallback


def _letterbox(cv2: Any, image: Any, imgsz: int) -> tuple[Any, float, int, int]:
    height, width = image.shape[:2]
    ratio = min(imgsz / width, imgsz / height)
    resized_width = int(round(width * ratio))
    resized_height = int(round(height * ratio))
    resized = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
    pad_x = (imgsz - resized_width) // 2
    pad_y = (imgsz - resized_height) // 2
    canvas = cv2.copyMakeBorder(
        resized,
        pad_y,
        imgsz - resized_height - pad_y,
        pad_x,
        imgsz - resized_width - pad_x,
        cv2.BORDER_CONSTANT,
        value=(114, 114, 114),
    )
    return canvas, ratio, pad_x, pad_y


def _image_to_tensor(cv2: Any, image: Any) -> Any:
    import numpy as np

    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    tensor = rgb.astype(np.float32) / 255.0
    tensor = tensor.transpose(2, 0, 1)
    tensor = tensor.reshape(1, *tensor.shape)
    return np.ascontiguousarray(tensor)


def _objects_from_output(
    outputs: list[Any],
    labels: list[str],
    conf: float,
    image_width: int,
    image_height: int,
    ratio: float,
    pad_x: int,
    pad_y: int,
) -> list[dict[str, Any]]:
    import numpy as np

    if not outputs:
        return []
    predictions = np.squeeze(outputs[0])
    if predictions.ndim != 2:
        raise RuntimeError(f"Unsupported ONNX output shape: {list(predictions.shape)}")

    if predictions.shape[0] <= 256 and predictions.shape[1] > predictions.shape[0]:
        predictions = predictions.T

    if predictions.shape[1] == 6:
        candidates = _objects_from_xyxy_rows(predictions, labels, conf)
    elif predictions.shape[1] > 6:
        candidates = _objects_from_yolo_rows(predictions, labels, conf)
    else:
        raise RuntimeError(f"Unsupported ONNX output width: {predictions.shape[1]}")

    decoded: list[dict[str, Any]] = []
    for candidate in candidates:
        x1, y1, x2, y2 = _undo_letterbox(candidate["box_xyxy"], ratio, pad_x, pad_y)
        x1 = _clip_int(x1, 0, image_width - 1)
        y1 = _clip_int(y1, 0, image_height - 1)
        x2 = _clip_int(x2, 0, image_width - 1)
        y2 = _clip_int(y2, 0, image_height - 1)
        if x2 <= x1 or y2 <= y1:
            continue
        decoded.append(
            {
                "label": candidate["label"],
                "confidence": candidate["confidence"],
                "box_xyxy": [x1, y1, x2, y2],
            }
        )
    return _nms(decoded)


def _objects_from_yolo_rows(predictions: Any, labels: list[str], conf: float) -> list[dict[str, Any]]:
    import numpy as np

    boxes = predictions[:, :4]
    scores_by_class = predictions[:, 4:]
    class_ids = np.argmax(scores_by_class, axis=1)
    scores = scores_by_class[np.arange(scores_by_class.shape[0]), class_ids]
    keep = scores >= conf

    objects: list[dict[str, Any]] = []
    for xywh, class_id, score in zip(boxes[keep], class_ids[keep], scores[keep]):
        cx, cy, width, height = [float(value) for value in xywh]
        label = labels[int(class_id)] if int(class_id) < len(labels) else str(int(class_id))
        objects.append(
            {
                "label": label,
                "confidence": round(float(score), 4),
                "box_xyxy": [
                    cx - width / 2.0,
                    cy - height / 2.0,
                    cx + width / 2.0,
                    cy + height / 2.0,
                ],
            }
        )
    return objects


def _objects_from_xyxy_rows(predictions: Any, labels: list[str], conf: float) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for row in predictions:
        score = float(row[4])
        if score < conf:
            continue
        class_id = int(row[5])
        label = labels[class_id] if class_id < len(labels) else str(class_id)
        objects.append(
            {
                "label": label,
                "confidence": round(score, 4),
                "box_xyxy": [float(row[0]), float(row[1]), float(row[2]), float(row[3])],
            }
        )
    return objects


def _undo_letterbox(box: list[float], ratio: float, pad_x: int, pad_y: int) -> list[float]:
    x1, y1, x2, y2 = box
    return [
        (x1 - pad_x) / ratio,
        (y1 - pad_y) / ratio,
        (x2 - pad_x) / ratio,
        (y2 - pad_y) / ratio,
    ]


def _nms(objects: list[dict[str, Any]], iou_threshold: float = 0.45) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for item in sorted(objects, key=lambda obj: obj["confidence"], reverse=True):
        if all(_iou(item["box_xyxy"], chosen["box_xyxy"]) <= iou_threshold for chosen in selected):
            selected.append(item)
    return selected


def _iou(a: list[int], b: list[int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_width = max(0, inter_x2 - inter_x1)
    inter_height = max(0, inter_y2 - inter_y1)
    intersection = inter_width * inter_height
    a_area = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    b_area = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = a_area + b_area - intersection
    return 0.0 if union <= 0 else intersection / union


def _save_annotated_image(cv2: Any, image: Any, objects: list[dict[str, Any]], output_path: str | Path) -> Path:
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    annotated = image.copy()
    for obj in objects:
        x1, y1, x2, y2 = obj["box_xyxy"]
        label = f"{obj['label']} {obj['confidence']:.2f}"
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 190, 255), 2)
        cv2.putText(annotated, label, (x1, max(16, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 190, 255), 2)
    if not bool(cv2.imwrite(str(output), annotated)):
        raise RuntimeError(f"OpenCV failed to write annotated image: {output}")
    return output


def _load_labels(session: Any) -> list[str]:
    try:
        metadata = session.get_modelmeta().custom_metadata_map or {}
    except Exception:
        metadata = {}
    for key in ("names", "classes"):
        value = metadata.get(key)
        if value:
            parsed = _parse_names(value)
            if parsed:
                return parsed
    return list(COCO80_NAMES)


def _parse_names(value: str) -> list[str]:
    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, dict):
            return [str(parsed[key]) for key in sorted(parsed, key=_name_sort_key)]
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except Exception:
        pass
    return []


def _name_sort_key(value: Any) -> tuple[int, int | str]:
    text = str(value)
    if text.isdigit():
        return (0, int(text))
    return (1, text)


def _clip_int(value: float, lower: int, upper: int) -> int:
    return int(round(min(max(value, lower), upper)))


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _error(error: str, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "source": "onnx",
        "error": error,
        "message": message,
        "timestamp": _now_iso(),
        "robot_action_executed": False,
    }
    payload.update(extra)
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
