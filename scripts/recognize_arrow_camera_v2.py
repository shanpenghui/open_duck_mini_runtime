#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from collections import Counter, deque
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

try:
    import cv2
except ImportError as exc:  # pragma: no cover - Pi runtime dependency
    raise SystemExit("opencv is required: sudo apt-get install python3-opencv") from exc


LABELS = ["none", "left", "right", "forward", "back", "stop"]


def load_tflite_model(model_path: Path, labels: list[str]):
    if not model_path.exists():
        return None

    try:
        from tflite_runtime.interpreter import Interpreter
    except ImportError:
        try:
            from tensorflow.lite.python.interpreter import Interpreter  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "model path was provided, but neither tflite_runtime nor tensorflow is installed"
            ) from exc

    interpreter = Interpreter(model_path=str(model_path))
    interpreter.allocate_tensors()
    input_detail = interpreter.get_input_details()[0]
    output_detail = interpreter.get_output_details()[0]
    return {
        "interpreter": interpreter,
        "input_detail": input_detail,
        "output_detail": output_detail,
        "labels": labels,
    }


def predict_tflite(model: dict[str, Any], roi: np.ndarray) -> tuple[str, float]:
    input_detail = model["input_detail"]
    output_detail = model["output_detail"]
    shape = input_detail["shape"]
    height = int(shape[1])
    width = int(shape[2])
    channels = int(shape[3])

    if channels == 1:
        image = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
        image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
        image = image[..., np.newaxis]
    else:
        image = cv2.resize(roi, (width, height), interpolation=cv2.INTER_AREA)

    dtype = input_detail["dtype"]
    if dtype == np.float32:
        tensor = image.astype(np.float32) / 255.0
    else:
        scale, zero_point = input_detail.get("quantization", (0.0, 0))
        if scale:
            tensor = image.astype(np.float32) / scale + zero_point
            tensor = np.clip(np.round(tensor), np.iinfo(dtype).min, np.iinfo(dtype).max).astype(dtype)
        else:
            tensor = image.astype(dtype)

    interpreter = model["interpreter"]
    interpreter.set_tensor(input_detail["index"], tensor.reshape(shape))
    interpreter.invoke()
    output = interpreter.get_tensor(output_detail["index"])[0]

    if np.issubdtype(output.dtype, np.floating):
        scores = output.astype(np.float32)
    else:
        scale, zero_point = output_detail.get("quantization", (0.0, 0))
        scores = (output.astype(np.float32) - zero_point) * scale if scale else output.astype(np.float32)

    total = float(np.sum(scores))
    if total > 0 and not np.isclose(total, 1.0):
        scores = scores / total

    index = int(np.argmax(scores))
    labels = model["labels"]
    return labels[index] if index < len(labels) else "none", float(scores[index])


def crop_roi(frame: np.ndarray, roi: tuple[int, int, int, int] | None) -> np.ndarray:
    if roi is None:
        return frame
    x, y, width, height = roi
    return frame[y : y + height, x : x + width]


def orient_frame(frame: np.ndarray, rotate: int, flip_horizontal: bool, flip_vertical: bool) -> np.ndarray:
    if rotate == 90:
        frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    elif rotate == 180:
        frame = cv2.rotate(frame, cv2.ROTATE_180)
    elif rotate == 270:
        frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

    if flip_horizontal and flip_vertical:
        return cv2.flip(frame, -1)
    if flip_horizontal:
        return cv2.flip(frame, 1)
    if flip_vertical:
        return cv2.flip(frame, 0)
    return frame


def parse_roi(value: str | None) -> tuple[int, int, int, int] | None:
    if not value:
        return None
    if value.strip().lower() in {"none", "full", "all"}:
        return None
    parts = [int(part.strip()) for part in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--roi must be x,y,width,height or none")
    return parts[0], parts[1], parts[2], parts[3]


def angle_to_label(dx: float, dy: float) -> str:
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    return "back" if dy > 0 else "forward"


def make_arrow_mask(roi: np.ndarray, threshold_mode: str) -> np.ndarray:
    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    _, dark_mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    _, light_mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if threshold_mode == "dark":
        mask = dark_mask
    elif threshold_mode == "light":
        mask = light_mask
    else:
        mask = dark_mask if cv2.countNonZero(dark_mask) < cv2.countNonZero(light_mask) else light_mask

    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    return mask


def largest_contour_mask(mask: np.ndarray, min_area: float) -> tuple[np.ndarray | None, float]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = [contour for contour in contours if cv2.contourArea(contour) >= min_area]
    if not contours:
        return None, 0.0
    contour = max(contours, key=cv2.contourArea)
    contour_mask = np.zeros_like(mask)
    cv2.drawContours(contour_mask, [contour], -1, 255, thickness=-1)
    return contour_mask, float(cv2.contourArea(contour))


def normalize_binary_mask(mask: np.ndarray, size: int) -> np.ndarray | None:
    coords = cv2.findNonZero(mask)
    if coords is None:
        return None
    x, y, width, height = cv2.boundingRect(coords)
    side = max(width, height)
    pad_x = (side - width) // 2
    pad_y = (side - height) // 2
    cropped = mask[y : y + height, x : x + width]
    squared = cv2.copyMakeBorder(
        cropped,
        pad_y,
        side - height - pad_y,
        pad_x,
        side - width - pad_x,
        cv2.BORDER_CONSTANT,
        value=0,
    )
    normalized = cv2.resize(squared, (size, size), interpolation=cv2.INTER_NEAREST)
    return (normalized > 0).astype(np.uint8)


def draw_arrow_template(label: str, size: int) -> np.ndarray:
    template = np.zeros((size, size), dtype=np.uint8)
    margin = int(size * 0.16)
    thickness = max(6, int(size * 0.18))
    if label == "right":
        start = (margin, size // 2)
        end = (size - margin, size // 2)
    elif label == "left":
        start = (size - margin, size // 2)
        end = (margin, size // 2)
    elif label == "forward":
        start = (size // 2, size - margin)
        end = (size // 2, margin)
    elif label == "back":
        start = (size // 2, margin)
        end = (size // 2, size - margin)
    else:
        return template
    cv2.arrowedLine(template, start, end, 1, thickness=thickness, tipLength=0.42)
    return template


def template_iou(mask: np.ndarray, template: np.ndarray) -> float:
    intersection = np.logical_and(mask, template).sum()
    union = np.logical_or(mask, template).sum()
    return float(intersection / union) if union else 0.0


def rotate_binary_template(template: np.ndarray, angle_deg: float) -> np.ndarray:
    if angle_deg == 0:
        return template
    height, width = template.shape[:2]
    center = (width / 2.0, height / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    rotated = cv2.warpAffine(
        template,
        matrix,
        (width, height),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    return (rotated > 0).astype(np.uint8)


@lru_cache(maxsize=16)
def load_sample_templates(
    sample_dir: str,
    threshold_mode: str,
    min_area: float,
    template_size: int,
    rotation_deg: int,
    rotation_step: int,
) -> tuple[tuple[str, np.ndarray], ...]:
    templates: list[tuple[str, np.ndarray]] = []
    sample_root = Path(sample_dir)
    sample_threshold_mode = "dark" if threshold_mode == "auto" else threshold_mode
    rotation_step = max(1, rotation_step)
    angles = range(-rotation_deg, rotation_deg + 1, rotation_step)
    for label in ("back", "forward", "left", "right"):
        image_path = sample_root / f"{label}.jpg"
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            continue
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        mask = make_arrow_mask(image, sample_threshold_mode)
        contour_mask, _ = largest_contour_mask(mask, min_area)
        if contour_mask is None:
            continue
        normalized = normalize_binary_mask(contour_mask, template_size)
        if normalized is not None:
            for angle in angles:
                templates.append((label, rotate_binary_template(normalized, angle)))
    return tuple(templates)


def detect_arrow_sample(
    roi: np.ndarray,
    min_area: float,
    threshold_mode: str,
    sample_dir: Path,
    sample_rotation_deg: int,
    sample_rotation_step: int,
    template_size: int = 96,
) -> tuple[str, float, np.ndarray]:
    mask = make_arrow_mask(roi, threshold_mode)
    contour_mask, area = largest_contour_mask(mask, min_area)
    if contour_mask is None:
        return "none", 0.0, mask

    normalized = normalize_binary_mask(contour_mask, template_size)
    if normalized is None:
        return "none", 0.0, contour_mask

    templates = load_sample_templates(
        str(sample_dir),
        threshold_mode,
        float(min_area),
        template_size,
        sample_rotation_deg,
        sample_rotation_step,
    )
    if not templates:
        return detect_arrow_tip(roi, min_area, threshold_mode)

    scores = {label: template_iou(normalized, template) for label, template in templates}
    label, best_score = max(scores.items(), key=lambda item: item[1])
    sorted_scores = sorted(scores.values(), reverse=True)
    second_score = sorted_scores[1] if len(sorted_scores) > 1 else 0.0
    margin = best_score - second_score
    area_ratio = area / float(mask.shape[0] * mask.shape[1])
    confidence = float(max(0.0, min(0.98, 0.55 + best_score * 1.0 + margin * 0.7)))
    if area_ratio < 0.01 or area_ratio > 0.85 or best_score < 0.15 or margin < 0.015:
        return "none", confidence, contour_mask
    return label, confidence, contour_mask


def detect_arrow_template(
    roi: np.ndarray,
    min_area: float,
    threshold_mode: str,
    template_size: int = 96,
) -> tuple[str, float, np.ndarray]:
    mask = make_arrow_mask(roi, threshold_mode)
    contour_mask, area = largest_contour_mask(mask, min_area)
    if contour_mask is None:
        return "none", 0.0, mask

    roi_area = float(mask.shape[0] * mask.shape[1])
    area_ratio = area / roi_area
    if area_ratio < 0.01 or area_ratio > 0.75:
        return "none", 0.0, contour_mask

    normalized = normalize_binary_mask(contour_mask, template_size)
    if normalized is None:
        return "none", 0.0, contour_mask

    scores = {
        label: template_iou(normalized, draw_arrow_template(label, template_size))
        for label in ("left", "right", "forward", "back")
    }
    label, best_score = max(scores.items(), key=lambda item: item[1])
    second_score = sorted(scores.values(), reverse=True)[1]
    margin = best_score - second_score
    confidence = float(max(0.0, min(0.98, 0.35 + best_score * 1.1 + margin * 0.7)))
    if best_score < 0.18 or margin < 0.025:
        return "none", confidence, contour_mask
    return label, confidence, contour_mask


def detect_arrow_tip(
    roi: np.ndarray,
    min_area: float,
    threshold_mode: str,
) -> tuple[str, float, np.ndarray]:
    mask = make_arrow_mask(roi, threshold_mode)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = [contour for contour in contours if cv2.contourArea(contour) >= min_area]
    if not contours:
        return "none", 0.0, mask

    contour = max(contours, key=cv2.contourArea)
    area = float(cv2.contourArea(contour))
    moments = cv2.moments(contour)
    if moments["m00"] == 0:
        return "none", 0.0, mask

    center = np.array([moments["m10"] / moments["m00"], moments["m01"] / moments["m00"]])
    points = contour.reshape(-1, 2).astype(np.float32)

    hull = cv2.convexHull(points).reshape(-1, 2).astype(np.float32)
    vectors = hull - center
    distances = np.linalg.norm(vectors, axis=1)
    tip = hull[int(np.argmax(distances))]
    dx, dy = (tip - center).tolist()

    label = angle_to_label(dx, dy)
    roi_area = float(roi.shape[0] * roi.shape[1])
    area_score = min(1.0, area / max(min_area * 4.0, 1.0))
    elongation_score = min(1.0, np.hypot(dx, dy) / max(min(roi.shape[:2]) * 0.35, 1.0))
    confidence = float(max(0.0, min(0.95, 0.35 + 0.4 * area_score + 0.2 * elongation_score)))

    if area / roi_area > 0.85:
        return "none", 0.0, mask
    return label, confidence, mask


def detect_arrow(
    roi: np.ndarray,
    min_area: float,
    threshold_mode: str,
    detector: str,
    sample_dir: Path,
    sample_rotation_deg: int = 25,
    sample_rotation_step: int = 5,
) -> tuple[str, float, np.ndarray]:
    if detector == "sample":
        return detect_arrow_sample(
            roi,
            min_area,
            threshold_mode,
            sample_dir,
            sample_rotation_deg,
            sample_rotation_step,
        )
    if detector == "tip":
        return detect_arrow_tip(roi, min_area, threshold_mode)
    return detect_arrow_template(roi, min_area, threshold_mode)


def stable_prediction(
    votes: deque[str],
    scores: deque[float],
    threshold: float,
    min_votes: int,
) -> tuple[str, float]:
    if not votes:
        return "none", 0.0
    label, count = Counter(votes).most_common(1)[0]
    if label == "none" or count < min_votes:
        return "none", 0.0
    matching = [score for vote, score in zip(votes, scores) if vote == label]
    confidence = float(np.mean(matching)) if matching else 0.0
    if confidence < threshold:
        return "none", confidence
    return label, confidence


def create_camera(width: int, height: int):
    try:
        from picamera2 import Picamera2
    except ImportError as exc:  # pragma: no cover - Pi runtime dependency
        raise SystemExit("picamera2 is required on Raspberry Pi: sudo apt-get install python3-picamera2") from exc

    camera_info = Picamera2.global_camera_info()
    if not camera_info:
        raise SystemExit(
            "No Raspberry Pi camera detected. Check Camera V2 ribbon cable, CSI connector, "
            "and /boot/firmware/config.txt camera_auto_detect/dtoverlay=imx219 settings."
        )

    camera = Picamera2()
    config = camera.create_preview_configuration(main={"size": (width, height), "format": "RGB888"})
    camera.configure(config)
    camera.start()
    time.sleep(0.4)
    return camera


def main() -> None:
    parser = argparse.ArgumentParser(description="Recognize arrows with Raspberry Pi Camera V2.")
    parser.add_argument("--model", type=Path, default=Path("models/arrow_classifier_int8.tflite"))
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=240)
    parser.add_argument("--roi", type=parse_roi, default="80,40,160,160", help="x,y,width,height")
    parser.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=0)
    parser.add_argument("--flip-horizontal", action="store_true")
    parser.add_argument("--flip-vertical", action="store_true")
    parser.add_argument("--hz", type=float, default=6.0)
    parser.add_argument("--confidence", type=float, default=0.6)
    parser.add_argument("--vote-window", type=int, default=5)
    parser.add_argument("--min-votes", type=int, default=3)
    parser.add_argument("--min-area", type=float, default=120.0)
    parser.add_argument("--detector", choices=["sample", "template", "tip"], default="sample")
    parser.add_argument("--sample-dir", type=Path, default=Path("assets/arrow_samples"))
    parser.add_argument("--sample-rotation-deg", type=int, default=25)
    parser.add_argument("--sample-rotation-step", type=int, default=5)
    parser.add_argument("--threshold-mode", choices=["auto", "dark", "light"], default="auto")
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--save-debug", type=Path, default=None)
    args = parser.parse_args()

    model = load_tflite_model(args.model, LABELS)
    mode = "tflite" if model is not None else "geometry"
    print(json.dumps({"event": "start", "mode": mode, "model": str(args.model)}, ensure_ascii=False), flush=True)

    votes: deque[str] = deque(maxlen=args.vote_window)
    scores: deque[float] = deque(maxlen=args.vote_window)
    camera = create_camera(args.width, args.height)
    interval_s = 1.0 / max(args.hz, 0.1)
    frame_count = 0

    try:
        while True:
            started_at = time.monotonic()
            frame = camera.capture_array()
            frame = orient_frame(frame, args.rotate, args.flip_horizontal, args.flip_vertical)
            roi = crop_roi(frame, args.roi)

            if model is not None:
                label, confidence = predict_tflite(model, roi)
                mask = None
            else:
                label, confidence, mask = detect_arrow(
                    roi,
                    args.min_area,
                    args.threshold_mode,
                    args.detector,
                    args.sample_dir,
                    args.sample_rotation_deg,
                    args.sample_rotation_step,
                )

            if confidence < args.confidence:
                label = "none"
            votes.append(label)
            scores.append(confidence)
            stable_label, stable_confidence = stable_prediction(
                votes, scores, args.confidence, args.min_votes
            )

            print(
                json.dumps(
                    {
                        "label": stable_label,
                        "confidence": round(stable_confidence, 4),
                        "raw_label": label,
                        "raw_confidence": round(confidence, 4),
                        "mode": mode,
                        "detector": args.detector if model is None else "tflite",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                flush=True,
            )

            if args.save_debug is not None and mask is not None:
                args.save_debug.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(args.save_debug), mask)

            frame_count += 1
            if args.max_frames and frame_count >= args.max_frames:
                break
            time.sleep(max(0.0, interval_s - (time.monotonic() - started_at)))
    finally:
        camera.stop()


if __name__ == "__main__":
    main()
