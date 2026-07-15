from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOL_VERSION = "0.1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAPTURE_DIR = PROJECT_ROOT / "data" / "realsense_depth"
DEFAULT_V4L2_DEPTH_DEVICE = "/dev/video0"
DEFAULT_V4L2_DEPTH_FORMAT = "Z16 "
DEFAULT_DEPTH_SCALE = 0.001
DEFAULT_VIS_MIN_M = 0.2
DEFAULT_VIS_MAX_M = 4.0
DEFAULT_OBSERVE_ROOT = Path("/tmp/openduck_depth_observe")


def depth_status() -> dict[str, Any]:
    rs, rs_error = _import_module("pyrealsense2")
    cv2, cv2_error = _import_module("cv2")
    numpy, numpy_error = _import_module("numpy")
    return {
        "ok": True,
        "source": "realsense-depth",
        "tool_version": TOOL_VERSION,
        "pyrealsense2_importable": rs is not None,
        "pyrealsense2_error": rs_error,
        "opencv_importable": cv2 is not None,
        "opencv_error": cv2_error,
        "numpy_importable": numpy is not None,
        "numpy_error": numpy_error,
        "default_v4l2_depth_device": DEFAULT_V4L2_DEPTH_DEVICE,
        "v4l2_depth_format": DEFAULT_V4L2_DEPTH_FORMAT,
        "message": "Read-only RealSense D435 depth capture and obstacle summary tool. It does not control the robot.",
        "robot_action_executed": False,
    }


def capture_depth_snapshot(
    output_dir: str | Path | None = None,
    width: int = 640,
    height: int = 480,
    fps: int = 15,
    warmup_frames: int = 8,
    save_color: bool = True,
    backend: str = "auto",
    device: str = DEFAULT_V4L2_DEPTH_DEVICE,
    depth_scale: float = DEFAULT_DEPTH_SCALE,
) -> dict[str, Any]:
    rs, rs_error = _import_module("pyrealsense2")
    if backend == "pyrealsense2" and rs is None:
        return _error("pyrealsense2_not_installed", "pyrealsense2 is required for D435 depth capture.", detail=rs_error)

    cv2, cv2_error = _import_module("cv2")
    if cv2 is None:
        return _error("opencv_not_installed", "opencv-python-headless is required to save png/jpg files.", detail=cv2_error)

    np, np_error = _import_module("numpy")
    if np is None:
        return _error("numpy_not_installed", "numpy is required to save depth arrays.", detail=np_error)

    if backend not in ("auto", "pyrealsense2", "v4l2"):
        return _error("unsupported_backend", "backend must be auto, pyrealsense2, or v4l2.", backend=backend)

    if backend == "v4l2" or (backend == "auto" and rs is None):
        return _capture_depth_snapshot_v4l2(
            cv2=cv2,
            np=np,
            output_dir=output_dir,
            device=device,
            width=width,
            height=height,
            fps=fps,
            warmup_frames=warmup_frames,
            depth_scale=depth_scale,
        )

    out_dir = _resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)
    if save_color:
        config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)

    try:
        profile = pipeline.start(config)
        depth_sensor = profile.get_device().first_depth_sensor()
        depth_scale = float(depth_sensor.get_depth_scale())

        frames = None
        for _ in range(max(warmup_frames, 1)):
            frames = pipeline.wait_for_frames(timeout_ms=5000)
        if frames is None:
            return _error("capture_failed", "RealSense did not return frames.")

        depth_frame = frames.get_depth_frame()
        if not depth_frame:
            return _error("capture_failed", "RealSense did not return a depth frame.")

        depth_raw = np.asanyarray(depth_frame.get_data())
        depth_m = depth_raw.astype("float32") * depth_scale
        timestamp = _now_iso()

        raw_png = out_dir / "depth_raw.png"
        depth_npy = out_dir / "depth_m.npy"
        depth_vis_png = out_dir / "depth_vis.png"
        intrinsics_json = out_dir / "intrinsics.json"
        summary_json = out_dir / "obstacle_summary.json"
        dry_run_json = out_dir / "navigation_dry_run.json"

        if not bool(cv2.imwrite(str(raw_png), depth_raw)):
            return _error("capture_failed", f"OpenCV failed to write depth png: {raw_png}")
        np.save(str(depth_npy), depth_m)
        _save_depth_visualization(cv2, np, depth_m, depth_vis_png)

        intrinsics_payload = _intrinsics_payload(depth_frame, depth_scale, width, height, fps, timestamp)
        intrinsics_json.write_text(json.dumps(intrinsics_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        color_path = None
        if save_color:
            color_frame = frames.get_color_frame()
            if color_frame:
                color = np.asanyarray(color_frame.get_data())
                color_path = out_dir / "color.jpg"
                cv2.imwrite(str(color_path), color)

        summary = summarize_depth_obstacles(depth_npy)
        summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        dry_run = navigation_dry_run(summary)
        dry_run_json.write_text(json.dumps(dry_run, ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "ok": True,
            "source": "realsense-depth",
            "backend": "pyrealsense2",
            "output_dir": str(out_dir),
            "depth_raw_png": str(raw_png),
            "depth_m_npy": str(depth_npy),
            "depth_vis_png": str(depth_vis_png),
            "color_image_path": str(color_path) if color_path is not None else None,
            "intrinsics_json": str(intrinsics_json),
            "obstacle_summary_json": str(summary_json),
            "navigation_dry_run_json": str(dry_run_json),
            "depth_scale": depth_scale,
            "width": int(depth_raw.shape[1]),
            "height": int(depth_raw.shape[0]),
            "timestamp": timestamp,
            "obstacle_summary": summary,
            "navigation_dry_run": dry_run,
            "robot_action_executed": False,
        }
    except Exception as exc:
        return _error("capture_failed", str(exc))
    finally:
        try:
            pipeline.stop()
        except Exception:
            pass


def _capture_depth_snapshot_v4l2(
    cv2: Any,
    np: Any,
    output_dir: str | Path | None,
    device: str,
    width: int,
    height: int,
    fps: int,
    warmup_frames: int,
    depth_scale: float,
) -> dict[str, Any]:
    out_dir = _resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
    try:
        if not cap.isOpened():
            cap.release()
            cap = _open_v4l2_capture_by_index(cv2, device)
        if not cap.isOpened():
            return _capture_depth_snapshot_v4l2_cli(
                cv2=cv2,
                np=np,
                output_dir=output_dir,
                device=device,
                width=width,
                height=height,
                warmup_frames=warmup_frames,
                depth_scale=depth_scale,
                open_error=f"OpenCV failed to open V4L2 depth device: {device}",
            )

        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc("Z", "1", "6", " "))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, fps)
        cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)

        frame = None
        for _ in range(max(warmup_frames, 1)):
            ok, current = cap.read()
            if ok and current is not None:
                frame = current
        if frame is None:
            return _capture_depth_snapshot_v4l2_cli(
                cv2=cv2,
                np=np,
                output_dir=output_dir,
                device=device,
                width=width,
                height=height,
                warmup_frames=warmup_frames,
                depth_scale=depth_scale,
                open_error="OpenCV opened the device but did not return a V4L2 Z16 depth frame.",
            )

        depth_raw = _frame_to_z16_depth(frame, np, width=width, height=height)
        depth_m = depth_raw.astype("float32") * float(depth_scale)
        timestamp = _now_iso()

        raw_png = out_dir / "depth_raw.png"
        depth_npy = out_dir / "depth_m.npy"
        intrinsics_json = out_dir / "intrinsics.json"
        summary_json = out_dir / "obstacle_summary.json"

        if not bool(cv2.imwrite(str(raw_png), depth_raw)):
            return _error("capture_failed", f"OpenCV failed to write depth png: {raw_png}")
        np.save(str(depth_npy), depth_m)

        intrinsics_payload = {
            "ok": True,
            "source": "realsense-depth",
            "backend": "v4l2",
            "timestamp": timestamp,
            "device": device,
            "format": DEFAULT_V4L2_DEPTH_FORMAT,
            "requested_width": width,
            "requested_height": height,
            "requested_fps": fps,
            "depth_scale": depth_scale,
            "intrinsics": None,
            "message": "V4L2 fallback can read depth frames but does not expose calibrated intrinsics. Use pyrealsense2/librealsense later for full SLAM calibration.",
            "robot_action_executed": False,
        }
        intrinsics_json.write_text(json.dumps(intrinsics_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        summary = summarize_depth_obstacles(depth_npy)
        summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "ok": True,
            "source": "realsense-depth",
            "backend": "v4l2",
            "device": device,
            "output_dir": str(out_dir),
            "depth_raw_png": str(raw_png),
            "depth_m_npy": str(depth_npy),
            "color_image_path": None,
            "intrinsics_json": str(intrinsics_json),
            "obstacle_summary_json": str(summary_json),
            "depth_scale": depth_scale,
            "width": int(depth_raw.shape[1]),
            "height": int(depth_raw.shape[0]),
            "timestamp": timestamp,
            "obstacle_summary": summary,
            "robot_action_executed": False,
        }
    except Exception as exc:
        return _error("capture_failed", str(exc), device=device, backend="v4l2")
    finally:
        cap.release()


def _capture_depth_snapshot_v4l2_cli(
    cv2: Any,
    np: Any,
    output_dir: str | Path | None,
    device: str,
    width: int,
    height: int,
    warmup_frames: int,
    depth_scale: float,
    open_error: str,
) -> dict[str, Any]:
    out_dir = _resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_z16 = out_dir / "depth_frame.z16"
    command = [
        "v4l2-ctl",
        "-d",
        device,
        f"--set-fmt-video=width={width},height={height},pixelformat={DEFAULT_V4L2_DEPTH_FORMAT}",
        "--stream-mmap",
        f"--stream-skip={max(warmup_frames, 0)}",
        "--stream-count=1",
        f"--stream-to={raw_z16}",
    ]
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=20)
    except FileNotFoundError:
        return _error(
            "capture_failed",
            "OpenCV could not read depth and v4l2-ctl is not installed.",
            device=device,
            backend="v4l2",
            detail=open_error,
        )
    except subprocess.TimeoutExpired:
        return _error(
            "capture_failed",
            "v4l2-ctl timed out while capturing Z16 depth.",
            device=device,
            backend="v4l2",
            detail=open_error,
        )

    if completed.returncode != 0 or not raw_z16.is_file():
        return _error(
            "capture_failed",
            "v4l2-ctl failed to capture Z16 depth.",
            device=device,
            backend="v4l2",
            detail=open_error,
            stderr=(completed.stderr or "").strip(),
            stdout=(completed.stdout or "").strip(),
            command=" ".join(command),
        )

    expected_bytes = int(width) * int(height) * 2
    raw_bytes = raw_z16.read_bytes()
    if len(raw_bytes) < expected_bytes:
        return _error(
            "capture_failed",
            "v4l2-ctl captured fewer bytes than expected for one Z16 frame.",
            device=device,
            backend="v4l2",
            expected_bytes=expected_bytes,
            actual_bytes=len(raw_bytes),
            detail=open_error,
        )

    depth_raw = np.frombuffer(raw_bytes[:expected_bytes], dtype="<u2").reshape(height, width)
    return _write_depth_outputs(
        cv2=cv2,
        np=np,
        out_dir=out_dir,
        depth_raw=depth_raw,
        depth_scale=depth_scale,
        backend="v4l2-ctl",
        device=device,
        width=width,
        height=height,
        fps=None,
        color_path=None,
    )


def _write_depth_outputs(
    cv2: Any,
    np: Any,
    out_dir: Path,
    depth_raw: Any,
    depth_scale: float,
    backend: str,
    device: str,
    width: int,
    height: int,
    fps: int | None,
    color_path: Path | None,
) -> dict[str, Any]:
    depth_m = depth_raw.astype("float32") * float(depth_scale)
    timestamp = _now_iso()
    raw_png = out_dir / "depth_raw.png"
    depth_npy = out_dir / "depth_m.npy"
    depth_vis_png = out_dir / "depth_vis.png"
    intrinsics_json = out_dir / "intrinsics.json"
    summary_json = out_dir / "obstacle_summary.json"
    dry_run_json = out_dir / "navigation_dry_run.json"

    if not bool(cv2.imwrite(str(raw_png), depth_raw)):
        return _error("capture_failed", f"OpenCV failed to write depth png: {raw_png}")
    np.save(str(depth_npy), depth_m)
    _save_depth_visualization(cv2, np, depth_m, depth_vis_png)

    intrinsics_payload = {
        "ok": True,
        "source": "realsense-depth",
        "backend": backend,
        "timestamp": timestamp,
        "device": device,
        "format": DEFAULT_V4L2_DEPTH_FORMAT,
        "requested_width": width,
        "requested_height": height,
        "requested_fps": fps,
        "depth_scale": depth_scale,
        "intrinsics": None,
        "message": "V4L2 fallback can read depth frames but does not expose calibrated intrinsics. Use pyrealsense2/librealsense later for full SLAM calibration.",
        "robot_action_executed": False,
    }
    intrinsics_json.write_text(json.dumps(intrinsics_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = summarize_depth_obstacles(depth_npy)
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    dry_run = navigation_dry_run(summary)
    dry_run_json.write_text(json.dumps(dry_run, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "source": "realsense-depth",
        "backend": backend,
        "device": device,
        "output_dir": str(out_dir),
        "depth_raw_png": str(raw_png),
        "depth_m_npy": str(depth_npy),
        "depth_vis_png": str(depth_vis_png),
        "color_image_path": str(color_path) if color_path is not None else None,
        "intrinsics_json": str(intrinsics_json),
        "obstacle_summary_json": str(summary_json),
        "navigation_dry_run_json": str(dry_run_json),
        "depth_scale": depth_scale,
        "width": int(depth_raw.shape[1]),
        "height": int(depth_raw.shape[0]),
        "timestamp": timestamp,
        "obstacle_summary": summary,
        "navigation_dry_run": dry_run,
        "robot_action_executed": False,
    }


def summarize_depth_obstacles(
    depth_path: str | Path,
    block_distance_m: float = 0.7,
    min_valid_m: float = 0.15,
    max_valid_m: float = 6.0,
    depth_scale: float = 0.001,
) -> dict[str, Any]:
    np, np_error = _import_module("numpy")
    if np is None:
        return _error("numpy_not_installed", "numpy is required to read depth arrays.", detail=np_error)

    path = Path(depth_path).expanduser()
    if not path.is_file():
        return _error("depth_not_found", f"Depth file does not exist: {path}", depth_path=str(path))

    try:
        depth_m = _load_depth_meters(path, np, depth_scale)
        if depth_m.ndim != 2:
            return _error("invalid_depth", f"Depth array must be 2D, got shape {list(depth_m.shape)}.", depth_path=str(path))

        height, width = depth_m.shape
        y1 = int(height * 0.35)
        y2 = int(height * 0.95)
        roi = depth_m[y1:y2, :]
        left = roi[:, : width // 3]
        front = roi[:, width // 3 : (width * 2) // 3]
        right = roi[:, (width * 2) // 3 :]

        left_distance = _robust_min_distance(left, np, min_valid_m, max_valid_m)
        front_distance = _robust_min_distance(front, np, min_valid_m, max_valid_m)
        right_distance = _robust_min_distance(right, np, min_valid_m, max_valid_m)
        valid_ratio = _valid_ratio(roi, np, min_valid_m, max_valid_m)
        blocked = front_distance is not None and front_distance <= block_distance_m

        return {
            "ok": True,
            "source": "realsense-depth",
            "depth_path": str(path),
            "width": int(width),
            "height": int(height),
            "roi": {
                "y_start": y1,
                "y_end": y2,
                "layout": "left/front/right horizontal thirds, lower-middle depth region",
            },
            "left_min_distance_m": left_distance,
            "front_min_distance_m": front_distance,
            "right_min_distance_m": right_distance,
            "block_distance_m": block_distance_m,
            "blocked": bool(blocked),
            "valid_depth_ratio": valid_ratio,
            "robot_action_executed": False,
        }
    except Exception as exc:
        return _error("summary_failed", str(exc), depth_path=str(path))


def visualize_depth(
    depth_path: str | Path,
    output_path: str | Path | None = None,
    min_m: float = DEFAULT_VIS_MIN_M,
    max_m: float = DEFAULT_VIS_MAX_M,
    depth_scale: float = DEFAULT_DEPTH_SCALE,
) -> dict[str, Any]:
    np, np_error = _import_module("numpy")
    if np is None:
        return _error("numpy_not_installed", "numpy is required to read depth arrays.", detail=np_error)
    cv2, cv2_error = _import_module("cv2")
    if cv2 is None:
        return _error("opencv_not_installed", "OpenCV is required to save depth visualization.", detail=cv2_error)

    path = Path(depth_path).expanduser()
    if not path.is_file():
        return _error("depth_not_found", f"Depth file does not exist: {path}", depth_path=str(path))

    try:
        depth_m = _load_depth_meters(path, np, depth_scale)
        output = Path(output_path).expanduser() if output_path is not None else path.with_name("depth_vis.png")
        _save_depth_visualization(cv2, np, depth_m, output, min_m=min_m, max_m=max_m)
        return {
            "ok": True,
            "source": "realsense-depth",
            "depth_path": str(path),
            "depth_vis_png": str(output),
            "min_m": min_m,
            "max_m": max_m,
            "robot_action_executed": False,
        }
    except Exception as exc:
        return _error("visualize_failed", str(exc), depth_path=str(path))


def navigation_dry_run(summary: dict[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(summary, (str, Path)):
        path = Path(summary).expanduser()
        if not path.is_file():
            return _error("summary_not_found", f"Summary file does not exist: {path}", summary_path=str(path))
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return _error("summary_read_failed", str(exc), summary_path=str(path))
    else:
        payload = summary

    if payload.get("ok") is not True:
        return {
            "ok": False,
            "source": "navigation-dry-run",
            "error": "invalid_summary",
            "message": "Obstacle summary is not ok.",
            "summary": payload,
            "robot_action_executed": False,
        }

    front = payload.get("front_min_distance_m")
    left = payload.get("left_min_distance_m")
    right = payload.get("right_min_distance_m")
    blocked = bool(payload.get("blocked"))
    valid_ratio = float(payload.get("valid_depth_ratio") or 0.0)

    if valid_ratio < 0.2:
        action = "hold_position"
        reason = "depth_valid_ratio_too_low"
    elif front is None:
        action = "hold_position"
        reason = "front_distance_unknown"
    elif blocked:
        left_value = -1.0 if left is None else float(left)
        right_value = -1.0 if right is None else float(right)
        action = "turn_right" if right_value >= left_value else "turn_left"
        reason = "front_blocked"
    elif float(front) < 1.2:
        action = "go_slow_forward"
        reason = "front_clear_but_close"
    else:
        action = "go_forward"
        reason = "front_clear"

    return {
        "ok": True,
        "source": "navigation-dry-run",
        "mode": "navigation_dry_run",
        "suggested_action": action,
        "reason": reason,
        "front_min_distance_m": front,
        "left_min_distance_m": left,
        "right_min_distance_m": right,
        "blocked": blocked,
        "valid_depth_ratio": valid_ratio,
        "robot_action_executed": False,
    }


def observe_loop(
    output_root: str | Path = DEFAULT_OBSERVE_ROOT,
    count: int = 5,
    interval: float = 1.0,
    backend: str = "v4l2",
    device: str = DEFAULT_V4L2_DEPTH_DEVICE,
    width: int = 640,
    height: int = 480,
    fps: int = 15,
    warmup_frames: int = 2,
    depth_scale: float = DEFAULT_DEPTH_SCALE,
) -> dict[str, Any]:
    if count < 1:
        return _error("invalid_count", "count must be at least 1.", count=count)
    if interval < 0:
        return _error("invalid_interval", "interval must be 0 or greater.", interval=interval)

    root = Path(output_root).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / "navigation_dry_run.jsonl"
    iterations: list[dict[str, Any]] = []

    for index in range(count):
        iteration_dir = root / f"{index + 1:03d}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        capture = capture_depth_snapshot(
            output_dir=iteration_dir,
            width=width,
            height=height,
            fps=fps,
            warmup_frames=warmup_frames,
            save_color=False,
            backend=backend,
            device=device,
            depth_scale=depth_scale,
        )
        event = {
            "ok": capture.get("ok") is True,
            "source": "realsense-depth-observe-loop",
            "index": index + 1,
            "count": count,
            "timestamp": _now_iso(),
            "output_dir": str(iteration_dir),
            "obstacle_summary": capture.get("obstacle_summary"),
            "navigation_dry_run": capture.get("navigation_dry_run"),
            "capture_error": None if capture.get("ok") is True else capture,
            "robot_action_executed": False,
        }
        iterations.append(event)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        if index < count - 1:
            time.sleep(interval)

    return {
        "ok": all(item.get("ok") is True for item in iterations),
        "source": "realsense-depth-observe-loop",
        "mode": "navigation_dry_run_observe_loop",
        "output_root": str(root),
        "jsonl_log": str(log_path),
        "count": count,
        "interval": interval,
        "iterations": iterations,
        "robot_action_executed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        payload = depth_status()
    elif args.command == "capture":
        payload = capture_depth_snapshot(
            output_dir=args.output_dir,
            width=args.width,
            height=args.height,
            fps=args.fps,
            warmup_frames=args.warmup_frames,
            save_color=not args.no_color,
            backend=args.backend,
            device=args.device,
            depth_scale=args.depth_scale,
        )
    elif args.command == "obstacle-summary":
        payload = summarize_depth_obstacles(
            args.depth,
            block_distance_m=args.block_distance,
            min_valid_m=args.min_valid,
            max_valid_m=args.max_valid,
            depth_scale=args.depth_scale,
        )
    elif args.command == "visualize":
        payload = visualize_depth(
            args.depth,
            output_path=args.output,
            min_m=args.min_m,
            max_m=args.max_m,
            depth_scale=args.depth_scale,
        )
    elif args.command == "navigation-dry-run":
        payload = navigation_dry_run(args.summary)
    elif args.command == "observe-loop":
        payload = observe_loop(
            output_root=args.output_root,
            count=args.count,
            interval=args.interval,
            backend=args.backend,
            device=args.device,
            width=args.width,
            height=args.height,
            fps=args.fps,
            warmup_frames=args.warmup_frames,
            depth_scale=args.depth_scale,
        )
    else:
        parser.error("missing command")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") is True or args.command == "status" else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.realsense_depth_tool",
        description="Read-only RealSense D435 depth capture and obstacle summary tool.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Print dependency status.")

    capture_parser = subparsers.add_parser("capture", help="Capture D435 depth, color, intrinsics, and obstacle summary.")
    capture_parser.add_argument("--output-dir", help="Output directory. Defaults to data/realsense_depth/<timestamp>.")
    capture_parser.add_argument("--width", type=int, default=640, help="Depth stream width.")
    capture_parser.add_argument("--height", type=int, default=480, help="Depth stream height.")
    capture_parser.add_argument("--fps", type=int, default=15, help="Depth stream FPS.")
    capture_parser.add_argument("--warmup-frames", type=int, default=8, help="Frames to discard before saving.")
    capture_parser.add_argument("--no-color", action="store_true", help="Only capture depth.")
    capture_parser.add_argument("--backend", choices=("auto", "pyrealsense2", "v4l2"), default="auto", help="Depth backend. auto uses pyrealsense2 when available, otherwise V4L2 Z16.")
    capture_parser.add_argument("--device", default=DEFAULT_V4L2_DEPTH_DEVICE, help="V4L2 Z16 depth device. D435 depth is usually /dev/video0.")
    capture_parser.add_argument("--depth-scale", type=float, default=DEFAULT_DEPTH_SCALE, help="Meters per raw depth unit for V4L2 Z16.")

    summary_parser = subparsers.add_parser("obstacle-summary", help="Summarize obstacles from depth_m.npy or depth_raw.png.")
    summary_parser.add_argument("--depth", required=True, help="Depth file path. Prefer depth_m.npy; depth_raw.png is read with --depth-scale.")
    summary_parser.add_argument("--block-distance", type=float, default=0.7, help="Front distance threshold in meters.")
    summary_parser.add_argument("--min-valid", type=float, default=0.15, help="Ignore depth values closer than this.")
    summary_parser.add_argument("--max-valid", type=float, default=6.0, help="Ignore depth values farther than this.")
    summary_parser.add_argument("--depth-scale", type=float, default=0.001, help="Meters per raw png unit when reading depth_raw.png.")

    visualize_parser = subparsers.add_parser("visualize", help="Create a color depth visualization from depth_m.npy or depth_raw.png.")
    visualize_parser.add_argument("--depth", required=True, help="Depth file path. Prefer depth_m.npy.")
    visualize_parser.add_argument("--output", help="Output png path. Defaults to depth_vis.png beside the depth file.")
    visualize_parser.add_argument("--min-m", type=float, default=DEFAULT_VIS_MIN_M, help="Depth value mapped to nearest color.")
    visualize_parser.add_argument("--max-m", type=float, default=DEFAULT_VIS_MAX_M, help="Depth value mapped to farthest color.")
    visualize_parser.add_argument("--depth-scale", type=float, default=DEFAULT_DEPTH_SCALE, help="Meters per raw png unit when reading depth_raw.png.")

    dry_run_parser = subparsers.add_parser("navigation-dry-run", help="Suggest a dry-run navigation action from obstacle_summary.json.")
    dry_run_parser.add_argument("--summary", required=True, help="Path to obstacle_summary.json.")

    observe_parser = subparsers.add_parser("observe-loop", help="Capture depth repeatedly and log navigation dry-run suggestions.")
    observe_parser.add_argument("--output-root", default=str(DEFAULT_OBSERVE_ROOT), help="Root directory for per-iteration captures and JSONL log.")
    observe_parser.add_argument("--count", type=int, default=5, help="Number of observations to capture.")
    observe_parser.add_argument("--interval", type=float, default=1.0, help="Seconds to wait between observations.")
    observe_parser.add_argument("--backend", choices=("auto", "pyrealsense2", "v4l2"), default="v4l2", help="Depth backend.")
    observe_parser.add_argument("--device", default=DEFAULT_V4L2_DEPTH_DEVICE, help="V4L2 Z16 depth device.")
    observe_parser.add_argument("--width", type=int, default=640, help="Depth stream width.")
    observe_parser.add_argument("--height", type=int, default=480, help="Depth stream height.")
    observe_parser.add_argument("--fps", type=int, default=15, help="Depth stream FPS.")
    observe_parser.add_argument("--warmup-frames", type=int, default=2, help="Frames to discard before saving each observation.")
    observe_parser.add_argument("--depth-scale", type=float, default=DEFAULT_DEPTH_SCALE, help="Meters per raw depth unit.")
    return parser


def _frame_to_z16_depth(frame: Any, np: Any, width: int, height: int) -> Any:
    if frame.dtype == np.uint16 and frame.ndim == 2:
        return frame

    raw = np.ascontiguousarray(frame)
    if raw.dtype == np.uint16:
        return raw.reshape(raw.shape[:2])

    if raw.dtype != np.uint8:
        raise RuntimeError(f"Unsupported V4L2 depth frame dtype: {raw.dtype}, shape: {list(raw.shape)}")

    if raw.ndim == 3 and raw.shape[2] >= 2:
        low = raw[:, :, 0].astype("uint16")
        high = raw[:, :, 1].astype("uint16")
        return low + (high << 8)

    flat = raw.reshape(-1)
    expected = int(width) * int(height) * 2
    if flat.size == expected:
        return flat.reshape(height, width, 2).view("<u2").reshape(height, width)

    if raw.ndim == 2 and raw.shape[1] == width * 2:
        return raw.reshape(raw.shape[0], width, 2).view("<u2").reshape(raw.shape[0], width)

    raise RuntimeError(f"Unsupported V4L2 Z16 frame shape: {list(raw.shape)}")


def _open_v4l2_capture_by_index(cv2: Any, device: str) -> Any:
    name = Path(device).name
    if name.startswith("video") and name.removeprefix("video").isdigit():
        return cv2.VideoCapture(int(name.removeprefix("video")), cv2.CAP_V4L2)
    return cv2.VideoCapture()


def _load_depth_meters(path: Path, np: Any, depth_scale: float) -> Any:
    if path.suffix.lower() == ".npy":
        return np.load(str(path)).astype("float32")

    cv2, cv2_error = _import_module("cv2")
    if cv2 is None:
        raise RuntimeError(f"OpenCV is required to read depth png files: {cv2_error}")
    depth_raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if depth_raw is None:
        raise RuntimeError(f"OpenCV failed to read depth file: {path}")
    return depth_raw.astype("float32") * float(depth_scale)


def _save_depth_visualization(
    cv2: Any,
    np: Any,
    depth_m: Any,
    output_path: str | Path,
    min_m: float = DEFAULT_VIS_MIN_M,
    max_m: float = DEFAULT_VIS_MAX_M,
) -> Path:
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    valid = np.isfinite(depth_m) & (depth_m > 0)
    clipped = np.clip(depth_m, min_m, max_m)
    normalized = ((clipped - min_m) / max(max_m - min_m, 1e-6) * 255.0).astype("uint8")
    normalized[~valid] = 0
    # Invert so nearer obstacles look warmer after applying the color map.
    colored = cv2.applyColorMap(255 - normalized, cv2.COLORMAP_TURBO)
    colored[~valid] = (0, 0, 0)
    if not bool(cv2.imwrite(str(output), colored)):
        raise RuntimeError(f"OpenCV failed to write depth visualization: {output}")
    return output


def _robust_min_distance(region: Any, np: Any, min_valid_m: float, max_valid_m: float) -> float | None:
    valid = region[(region >= min_valid_m) & (region <= max_valid_m)]
    if valid.size == 0:
        return None
    return round(float(np.percentile(valid, 5)), 3)


def _valid_ratio(region: Any, np: Any, min_valid_m: float, max_valid_m: float) -> float:
    if region.size == 0:
        return 0.0
    valid = np.count_nonzero((region >= min_valid_m) & (region <= max_valid_m))
    return round(float(valid / region.size), 4)


def _intrinsics_payload(depth_frame: Any, depth_scale: float, width: int, height: int, fps: int, timestamp: str) -> dict[str, Any]:
    intrinsics = depth_frame.profile.as_video_stream_profile().intrinsics
    return {
        "ok": True,
        "source": "realsense-depth",
        "timestamp": timestamp,
        "requested_width": width,
        "requested_height": height,
        "requested_fps": fps,
        "depth_scale": depth_scale,
        "intrinsics": {
            "width": int(intrinsics.width),
            "height": int(intrinsics.height),
            "ppx": float(intrinsics.ppx),
            "ppy": float(intrinsics.ppy),
            "fx": float(intrinsics.fx),
            "fy": float(intrinsics.fy),
            "model": str(intrinsics.model),
            "coeffs": [float(value) for value in intrinsics.coeffs],
        },
        "robot_action_executed": False,
    }


def _resolve_output_dir(output_dir: str | Path | None) -> Path:
    if output_dir is not None:
        return Path(output_dir).expanduser()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DEFAULT_CAPTURE_DIR / stamp


def _import_module(name: str) -> tuple[Any | None, str | None]:
    try:
        module = __import__(name)
    except Exception as exc:
        return None, str(exc)
    return module, None


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _error(error: str, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "source": "realsense-depth",
        "error": error,
        "message": message,
        "timestamp": _now_iso(),
        "robot_action_executed": False,
    }
    payload.update(extra)
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
