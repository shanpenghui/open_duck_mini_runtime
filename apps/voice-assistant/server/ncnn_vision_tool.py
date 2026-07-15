from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOL_VERSION = "0.1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_NAME = "yolo11n_ncnn_model"
DEFAULT_MODEL_DIR_CANDIDATES = (
    PROJECT_ROOT / "models" / DEFAULT_MODEL_NAME,
    PROJECT_ROOT / "model" / DEFAULT_MODEL_NAME,
    Path.home() / ".openduck" / "models" / DEFAULT_MODEL_NAME,
    Path("/home/duck/.openduck/models") / DEFAULT_MODEL_NAME,
)
MODEL_PARAM_FILENAMES = ("model.ncnn.param", "model.param")
MODEL_BIN_FILENAMES = ("model.ncnn.bin", "model.bin")
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


def ncnn_status() -> dict[str, Any]:
    ncnn, ncnn_error = _import_module("ncnn")
    cv2, cv2_error = _import_module("cv2")
    model_dir = _find_default_model_dir()
    return {
        "ok": True,
        "source": "ncnn",
        "tool_version": TOOL_VERSION,
        "ncnn_importable": ncnn is not None,
        "ncnn_error": ncnn_error,
        "opencv_importable": cv2 is not None,
        "opencv_error": cv2_error,
        "default_model_candidates": [str(path) for path in DEFAULT_MODEL_DIR_CANDIDATES],
        "default_model_dir": str(model_dir) if model_dir is not None else None,
        "model_found": model_dir is not None,
        "model_files": _list_model_files(model_dir) if model_dir is not None else [],
        "robot_action_executed": False,
    }


def detect_image_ncnn(
    image_path: str | Path,
    model_dir: str | Path | None = None,
    conf: float = 0.25,
    imgsz: int = 640,
    annotated_output_path: str | Path | None = None,
) -> dict[str, Any]:
    image = Path(image_path).expanduser()
    if not image.is_file():
        return _error("image_not_found", f"Image file does not exist: {image}", image_path=str(image))

    ncnn, ncnn_error = _import_module("ncnn")
    if ncnn is None:
        return _error(
            "ncnn_not_installed",
            "ncnn is not installed. Install it before running NCNN detection.",
            detail=ncnn_error,
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

    resolved_model_dir = _resolve_model_dir(model_dir)
    if resolved_model_dir is None:
        return _error(
            "model_not_found",
            "NCNN model directory was not found. Put yolo11n_ncnn_model under models/, model/, or ~/.openduck/models/.",
            image_path=str(image),
            model_candidates=[str(path) for path in DEFAULT_MODEL_DIR_CANDIDATES],
        )

    model_files = _find_model_files(resolved_model_dir)
    if model_files is None:
        return _error(
            "model_not_found",
            "NCNN model directory exists but model.ncnn.param/model.ncnn.bin were not found.",
            image_path=str(image),
            model_dir=str(resolved_model_dir),
            model_files=_list_model_files(resolved_model_dir),
        )

    return _run_worker_detection(
        image_path=image,
        model_dir=resolved_model_dir,
        conf=conf,
        imgsz=imgsz,
        annotated_output_path=annotated_output_path,
    )


def _detect_image_ncnn_in_process(
    image_path: str | Path,
    model_dir: str | Path,
    conf: float,
    imgsz: int,
    annotated_output_path: str | Path | None,
) -> dict[str, Any]:
    image = Path(image_path).expanduser()
    ncnn, ncnn_error = _import_module("ncnn")
    if ncnn is None:
        return _error("ncnn_not_installed", "ncnn is not installed.", detail=ncnn_error, image_path=str(image))
    cv2, cv2_error = _import_module("cv2")
    if cv2 is None:
        return _error("opencv_not_installed", "opencv-python-headless is required.", detail=cv2_error, image_path=str(image))
    resolved_model_dir = Path(model_dir).expanduser()
    model_files = _find_model_files(resolved_model_dir)
    if model_files is None:
        return _error("model_not_found", "NCNN model files were not found.", image_path=str(image), model_dir=str(resolved_model_dir))

    try:
        original = cv2.imread(str(image))
        if original is None:
            return _error("image_not_found", f"OpenCV failed to read image: {image}", image_path=str(image))

        letterboxed, ratio, pad_x, pad_y = _letterbox(cv2, original, imgsz)
        input_tensor = _image_to_tensor(cv2, letterboxed)
        net = _load_ncnn_net(ncnn, model_files["param"], model_files["bin"])
        input_name = _first_name(net, "input_names", "in0")
        output_name = _first_name(net, "output_names", "out0")

        extractor = net.create_extractor()
        extractor.input(input_name, _make_ncnn_mat(ncnn, input_tensor))
        ret, output = extractor.extract(output_name)
        if ret != 0:
            raise RuntimeError(f"NCNN extract failed with code {ret} for output {output_name}")

        labels = _load_labels(resolved_model_dir)
        objects = _objects_from_output(
            output,
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
            "source": "ncnn",
            "model": resolved_model_dir.name,
            "model_dir": str(resolved_model_dir),
            "image_path": str(image),
            "objects": objects,
            "count": len(objects),
            "conf": conf,
            "imgsz": imgsz,
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
            model_dir=str(resolved_model_dir),
        )


def _run_worker_detection(
    image_path: Path,
    model_dir: Path,
    conf: float,
    imgsz: int,
    annotated_output_path: str | Path | None,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "server.ncnn_vision_tool",
        "_worker-detect",
        "--image",
        str(image_path),
        "--model-dir",
        str(model_dir),
        "--conf",
        str(conf),
        "--imgsz",
        str(imgsz),
    ]
    if annotated_output_path is not None:
        command.extend(["--annotated-output", str(Path(annotated_output_path).expanduser())])

    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return _error(
            "detect_failed",
            "NCNN worker timed out.",
            image_path=str(image_path),
            model_dir=str(model_dir),
        )

    payload = _json_from_output(completed.stdout)
    if payload is not None:
        return payload

    detail = (completed.stderr or completed.stdout or "").strip()
    return _error(
        "detect_failed",
        "NCNN worker exited before returning JSON.",
        image_path=str(image_path),
        model_dir=str(model_dir),
        exit_code=completed.returncode,
        detail=detail[-1000:] if detail else None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        payload = ncnn_status()
    elif args.command == "detect":
        payload = detect_image_ncnn(
            args.image,
            model_dir=args.model_dir,
            conf=args.conf,
            imgsz=args.imgsz,
            annotated_output_path=args.annotated_output,
        )
    elif args.command == "_worker-detect":
        payload = _detect_image_ncnn_in_process(
            args.image,
            model_dir=args.model_dir,
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
        prog="python -m server.ncnn_vision_tool",
        description="Read-only NCNN object detector for OpenDuck.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Print dependency and NCNN model status.")
    detect_parser = subparsers.add_parser("detect", help="Detect objects in one image with NCNN.")
    detect_parser.add_argument("--image", required=True, help="Input image path.")
    detect_parser.add_argument("--model-dir", help="NCNN model directory. Defaults to yolo11n_ncnn_model candidates.")
    detect_parser.add_argument("--conf", type=float, default=0.25, help="Confidence threshold.")
    detect_parser.add_argument("--imgsz", type=int, default=640, help="Inference image size.")
    detect_parser.add_argument("--annotated-output", help="Optional output path for a jpg with detection boxes.")
    worker_parser = subparsers.add_parser("_worker-detect", help=argparse.SUPPRESS)
    worker_parser.add_argument("--image", required=True)
    worker_parser.add_argument("--model-dir", required=True)
    worker_parser.add_argument("--conf", type=float, required=True)
    worker_parser.add_argument("--imgsz", type=int, required=True)
    worker_parser.add_argument("--annotated-output")
    return parser


def _json_from_output(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _import_module(name: str) -> tuple[Any | None, str | None]:
    try:
        if importlib.util.find_spec(name) is None:
            return None, f"No module named '{name}'"
        module = __import__(name)
    except Exception as exc:
        return None, str(exc)
    return module, None


def _resolve_model_dir(model_dir: str | Path | None) -> Path | None:
    if model_dir is not None:
        path = Path(model_dir).expanduser()
        return path if path.is_dir() else None
    return _find_default_model_dir()


def _find_default_model_dir() -> Path | None:
    for path in DEFAULT_MODEL_DIR_CANDIDATES:
        expanded = path.expanduser()
        if expanded.is_dir():
            return expanded
    return None


def _find_model_files(model_dir: Path) -> dict[str, Path] | None:
    param = _first_existing(model_dir, MODEL_PARAM_FILENAMES)
    bin_file = _first_existing(model_dir, MODEL_BIN_FILENAMES)
    if param is None or bin_file is None:
        return None
    return {"param": param, "bin": bin_file}


def _first_existing(model_dir: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        path = model_dir / name
        if path.is_file():
            return path
    return None


def _list_model_files(model_dir: Path | None) -> list[str]:
    if model_dir is None or not model_dir.is_dir():
        return []
    return [path.name for path in sorted(model_dir.iterdir()) if path.is_file()]


def _load_ncnn_net(ncnn: Any, param_path: Path, bin_path: Path) -> Any:
    net = ncnn.Net()
    if hasattr(net, "opt"):
        net.opt.num_threads = max(1, min(4, os.cpu_count() or 1))
        net.opt.use_vulkan_compute = False
    param_ret = net.load_param(str(param_path))
    if param_ret not in (0, None):
        raise RuntimeError(f"NCNN load_param failed with code {param_ret}: {param_path}")
    bin_ret = net.load_model(str(bin_path))
    if bin_ret not in (0, None):
        raise RuntimeError(f"NCNN load_model failed with code {bin_ret}: {bin_path}")
    return net


def _make_ncnn_mat(ncnn: Any, tensor: Any) -> Any:
    try:
        mat = ncnn.Mat(tensor, batch_index=0)
    except TypeError:
        mat = ncnn.Mat(tensor)
    clone = getattr(mat, "clone", None)
    return clone() if callable(clone) else mat


def _first_name(net: Any, method_name: str, fallback: str) -> str:
    method = getattr(net, method_name, None)
    if callable(method):
        try:
            names = list(method())
            if names:
                return str(names[0])
        except Exception:
            pass
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
    output: Any,
    labels: list[str],
    conf: float,
    image_width: int,
    image_height: int,
    ratio: float,
    pad_x: int,
    pad_y: int,
) -> list[dict[str, Any]]:
    import numpy as np

    try:
        predictions = output.numpy(batch_index=0)
    except Exception:
        predictions = np.array(output)
    predictions = np.squeeze(predictions)
    if predictions.ndim != 2:
        raise RuntimeError(f"Unsupported NCNN output shape: {list(predictions.shape)}")

    if predictions.shape[0] <= 256 and predictions.shape[1] > predictions.shape[0]:
        predictions = predictions.T

    if predictions.shape[1] == 6:
        candidates = _objects_from_xyxy_rows(predictions, labels, conf)
    elif predictions.shape[1] > 6:
        candidates = _objects_from_yolo_rows(predictions, labels, conf)
    else:
        raise RuntimeError(f"Unsupported NCNN output width: {predictions.shape[1]}")

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


def _load_labels(model_dir: Path) -> list[str]:
    metadata = model_dir / "metadata.yaml"
    if metadata.is_file():
        parsed = _parse_metadata_names(metadata.read_text(encoding="utf-8", errors="ignore"))
        if parsed:
            return parsed
    return list(COCO80_NAMES)


def _parse_metadata_names(text: str) -> list[str]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("names:"):
            continue
        inline = stripped.removeprefix("names:").strip()
        if inline:
            return _parse_inline_names(inline)

        names_by_id: dict[int, str] = {}
        for child in lines[index + 1 :]:
            if child and not child.startswith((" ", "\t")):
                break
            item = child.strip()
            if ":" not in item:
                continue
            key, value = item.split(":", 1)
            if key.strip().isdigit():
                names_by_id[int(key.strip())] = value.strip().strip("'\"")
        return [names_by_id[item] for item in sorted(names_by_id)] if names_by_id else []
    return []


def _parse_inline_names(value: str) -> list[str]:
    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, dict):
            return [str(parsed[key]) for key in sorted(parsed, key=_name_sort_key)]
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except Exception:
        pass

    cleaned = value.strip().strip("{}[]")
    names_by_id: dict[int, str] = {}
    names: list[str] = []
    for item in cleaned.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            key, label = item.split(":", 1)
            if key.strip().isdigit():
                names_by_id[int(key.strip())] = label.strip().strip("'\"")
        else:
            names.append(item.strip().strip("'\""))
    if names_by_id:
        return [names_by_id[key] for key in sorted(names_by_id)]
    return names


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
        "source": "ncnn",
        "error": error,
        "message": message,
        "timestamp": _now_iso(),
        "robot_action_executed": False,
    }
    payload.update(extra)
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
