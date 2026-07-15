from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.onnx_vision_tool import detect_image_onnx, onnx_status
from server.realsense_camera_tool import capture_rgb_snapshot, realsense_status
from server.realsense_depth_tool import (
    DEFAULT_DEPTH_SCALE,
    DEFAULT_V4L2_DEPTH_DEVICE,
    capture_depth_snapshot,
    depth_status,
    navigation_dry_run,
)


TOOL_VERSION = "0.1"
DEFAULT_OUTPUT_ROOT = Path("/tmp/openduck_fusion_loop")
DEFAULT_ONNX_MODEL = Path("/home/duck/.openduck/models/yolo11n_320.onnx")


def perception_status() -> dict[str, Any]:
    return {
        "ok": True,
        "source": "navigation-perception",
        "tool_version": TOOL_VERSION,
        "depth": depth_status(),
        "rgb": realsense_status(),
        "onnx": onnx_status(),
        "message": "Read-only fusion of D435 obstacle summary and optional ONNX YOLO objects. It does not control the robot.",
        "robot_action_executed": False,
    }


def observe_perception(
    depth_summary_path: str | Path,
    image_path: str | Path | None = None,
    onnx_model_path: str | Path | None = None,
    annotated_output_path: str | Path | None = None,
    output_path: str | Path | None = None,
    conf: float = 0.25,
    threads: int = 1,
) -> dict[str, Any]:
    summary = _load_json(depth_summary_path, "depth_summary")
    if summary.get("ok") is not True:
        return summary

    dry_run = navigation_dry_run(summary)
    yolo_result = None
    if image_path is not None:
        yolo_result = detect_image_onnx(
            image_path,
            model_path=onnx_model_path,
            conf=conf,
            annotated_output_path=annotated_output_path,
            threads=threads,
        )

    payload = {
        "ok": dry_run.get("ok") is True and (yolo_result is None or yolo_result.get("ok") is True),
        "source": "navigation-perception",
        "mode": "perception_fusion_dry_run",
        "timestamp": _now_iso(),
        "depth_summary_path": str(Path(depth_summary_path).expanduser()),
        "image_path": str(Path(image_path).expanduser()) if image_path is not None else None,
        "scene_risk": _scene_risk(summary),
        "obstacle_summary": summary,
        "navigation_dry_run": dry_run,
        "objects": [] if yolo_result is None else yolo_result.get("objects", []),
        "object_count": 0 if yolo_result is None else yolo_result.get("count", 0),
        "onnx_detection": yolo_result,
        "robot_action_executed": False,
    }

    if output_path is not None:
        output = Path(output_path).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["output_path"] = str(output)
    return payload


def observe_loop(
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    count: int = 5,
    interval: float = 1.0,
    depth_backend: str = "v4l2",
    depth_device: str = DEFAULT_V4L2_DEPTH_DEVICE,
    color_device: str = "/dev/video4",
    onnx_model_path: str | Path = DEFAULT_ONNX_MODEL,
    width: int = 640,
    height: int = 480,
    fps: int = 15,
    depth_warmup_frames: int = 2,
    color_warmup_frames: int = 2,
    conf: float = 0.25,
    threads: int = 1,
    depth_scale: float = DEFAULT_DEPTH_SCALE,
) -> dict[str, Any]:
    if count < 1:
        return _error("invalid_count", "count must be at least 1.", count=count)
    if interval < 0:
        return _error("invalid_interval", "interval must be 0 or greater.", interval=interval)

    root = Path(output_root).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / "perception_fusion.jsonl"
    iterations: list[dict[str, Any]] = []

    for index in range(count):
        iteration_dir = root / f"{index + 1:03d}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        iteration_dir.mkdir(parents=True, exist_ok=True)

        depth_result = capture_depth_snapshot(
            output_dir=iteration_dir,
            width=width,
            height=height,
            fps=fps,
            warmup_frames=depth_warmup_frames,
            save_color=False,
            backend=depth_backend,
            device=depth_device,
            depth_scale=depth_scale,
        )

        rgb_path = iteration_dir / "realsense_rgb.jpg"
        rgb_result = capture_rgb_snapshot(
            output_path=rgb_path,
            device=color_device,
            width=width,
            height=height,
            warmup_frames=color_warmup_frames,
        )

        if depth_result.get("ok") is True and rgb_result.get("ok") is True:
            fusion = observe_perception(
                depth_summary_path=iteration_dir / "obstacle_summary.json",
                image_path=rgb_path,
                onnx_model_path=onnx_model_path,
                annotated_output_path=iteration_dir / "yolo_annotated.jpg",
                output_path=iteration_dir / "perception_fusion.json",
                conf=conf,
                threads=threads,
            )
        else:
            fusion = {
                "ok": False,
                "source": "navigation-perception",
                "mode": "perception_fusion_dry_run",
                "error": "capture_failed",
                "depth_capture": depth_result,
                "rgb_capture": rgb_result,
                "robot_action_executed": False,
            }

        event = {
            "ok": fusion.get("ok") is True,
            "source": "navigation-perception-observe-loop",
            "index": index + 1,
            "count": count,
            "timestamp": _now_iso(),
            "output_dir": str(iteration_dir),
            "fusion": fusion,
            "robot_action_executed": False,
        }
        iterations.append(event)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        if index < count - 1:
            time.sleep(interval)

    return {
        "ok": all(item.get("ok") is True for item in iterations),
        "source": "navigation-perception-observe-loop",
        "mode": "perception_fusion_observe_loop",
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
        payload = perception_status()
    elif args.command == "observe":
        payload = observe_perception(
            depth_summary_path=args.depth_summary,
            image_path=args.image,
            onnx_model_path=args.onnx_model,
            annotated_output_path=args.annotated_output,
            output_path=args.output,
            conf=args.conf,
            threads=args.threads,
        )
    elif args.command == "observe-loop":
        payload = observe_loop(
            output_root=args.output_root,
            count=args.count,
            interval=args.interval,
            depth_backend=args.depth_backend,
            depth_device=args.depth_device,
            color_device=args.color_device,
            onnx_model_path=args.onnx_model,
            width=args.width,
            height=args.height,
            fps=args.fps,
            depth_warmup_frames=args.depth_warmup_frames,
            color_warmup_frames=args.color_warmup_frames,
            conf=args.conf,
            threads=args.threads,
            depth_scale=args.depth_scale,
        )
    else:
        parser.error("missing command")

    if getattr(args, "format", "json") == "text":
        print(_format_text(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") is True or args.command == "status" else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.navigation_perception_tool",
        description="Read-only navigation perception fusion for OpenDuck.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    status_parser = subparsers.add_parser("status", help="Print dependency and model status.")
    _add_format_arg(status_parser)

    observe_parser = subparsers.add_parser("observe", help="Fuse depth obstacle summary and optional ONNX YOLO objects.")
    observe_parser.add_argument("--depth-summary", required=True, help="Path to obstacle_summary.json.")
    observe_parser.add_argument("--image", help="Optional RGB image path for ONNX YOLO detection.")
    observe_parser.add_argument("--onnx-model", help="Optional ONNX model path. Defaults to onnx_vision_tool candidates.")
    observe_parser.add_argument("--annotated-output", help="Optional output path for YOLO annotated image.")
    observe_parser.add_argument("--output", help="Optional output path for fused perception JSON.")
    observe_parser.add_argument("--conf", type=float, default=0.25, help="ONNX YOLO confidence threshold.")
    observe_parser.add_argument("--threads", type=int, default=1, help="ONNXRuntime CPU threads.")
    _add_format_arg(observe_parser)

    loop_parser = subparsers.add_parser("observe-loop", help="Repeatedly capture depth and RGB, run ONNX YOLO, and log fused dry-run perception.")
    loop_parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Root directory for per-iteration outputs and JSONL log.")
    loop_parser.add_argument("--count", type=int, default=5, help="Number of fusion observations.")
    loop_parser.add_argument("--interval", type=float, default=1.0, help="Seconds to wait between observations.")
    loop_parser.add_argument("--depth-backend", choices=("auto", "pyrealsense2", "v4l2"), default="v4l2", help="Depth backend.")
    loop_parser.add_argument("--depth-device", default=DEFAULT_V4L2_DEPTH_DEVICE, help="V4L2 depth device.")
    loop_parser.add_argument("--color-device", default="/dev/video4", help="V4L2 color device.")
    loop_parser.add_argument("--onnx-model", default=str(DEFAULT_ONNX_MODEL), help="ONNX model path.")
    loop_parser.add_argument("--width", type=int, default=640, help="Capture width.")
    loop_parser.add_argument("--height", type=int, default=480, help="Capture height.")
    loop_parser.add_argument("--fps", type=int, default=15, help="Depth stream FPS.")
    loop_parser.add_argument("--depth-warmup-frames", type=int, default=2, help="Depth frames to discard before saving.")
    loop_parser.add_argument("--color-warmup-frames", type=int, default=2, help="Color frames to discard before saving.")
    loop_parser.add_argument("--conf", type=float, default=0.25, help="ONNX YOLO confidence threshold.")
    loop_parser.add_argument("--threads", type=int, default=1, help="ONNXRuntime CPU threads.")
    loop_parser.add_argument("--depth-scale", type=float, default=DEFAULT_DEPTH_SCALE, help="Meters per raw depth unit.")
    _add_format_arg(loop_parser)
    return parser


def _add_format_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("json", "text"), default="json", help="Output format.")


def _error(error: str, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "source": "navigation-perception",
        "error": error,
        "message": message,
        "timestamp": _now_iso(),
        "robot_action_executed": False,
    }
    payload.update(extra)
    return payload


def _load_json(path: str | Path, label: str) -> dict[str, Any]:
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        return {
            "ok": False,
            "source": "navigation-perception",
            "error": f"{label}_not_found",
            "message": f"{label} file does not exist: {resolved}",
            "path": str(resolved),
            "robot_action_executed": False,
        }
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "source": "navigation-perception",
            "error": f"{label}_read_failed",
            "message": str(exc),
            "path": str(resolved),
            "robot_action_executed": False,
        }
    return payload if isinstance(payload, dict) else {
        "ok": False,
        "source": "navigation-perception",
        "error": f"{label}_invalid",
        "message": f"{label} must be a JSON object.",
        "path": str(resolved),
        "robot_action_executed": False,
    }


def _scene_risk(summary: dict[str, Any]) -> str:
    if summary.get("ok") is not True:
        return "unknown"
    valid_ratio = float(summary.get("valid_depth_ratio") or 0.0)
    front = summary.get("front_min_distance_m")
    blocked = bool(summary.get("blocked"))
    if valid_ratio < 0.2:
        return "low_depth_confidence"
    if blocked:
        return "blocked"
    if front is not None and float(front) < 1.2:
        return "close"
    return "clear"


def _format_text(payload: dict[str, Any]) -> str:
    if payload.get("ok") is not True and payload.get("iterations") is None:
        return "\n".join(
            [
                "感知结果：失败",
                f"原因：{payload.get('message') or payload.get('error') or '未知错误'}",
                "状态：未控制机器人",
            ]
        )

    if payload.get("mode") == "perception_fusion_observe_loop":
        return _format_loop_text(payload)
    return _format_fusion_text(payload)


def _format_loop_text(payload: dict[str, Any]) -> str:
    iterations = payload.get("iterations")
    if not isinstance(iterations, list) or not iterations:
        return "感知结果：没有生成帧\n状态：未控制机器人"

    lines = [
        f"感知循环：{len(iterations)} 帧",
        "序号 | 场景 | 前方 | 左侧 | 右侧 | 建议 | 文件夹",
    ]
    for item in iterations:
        fusion = item.get("fusion") if isinstance(item, dict) else {}
        if not isinstance(fusion, dict):
            fusion = {}
        lines.append(
            (
                f"{item.get('index', '-')} | "
                f"{fusion.get('scene_risk', '-')} | "
                f"{_distance_from_fusion(fusion, 'front_min_distance_m')} | "
                f"{_distance_from_fusion(fusion, 'left_min_distance_m')} | "
                f"{_distance_from_fusion(fusion, 'right_min_distance_m')} | "
                f"{_suggestion_from_fusion(fusion)} | "
                f"{item.get('output_dir', '-')}"
            )
        )

    latest = iterations[-1]
    latest_fusion = latest.get("fusion") if isinstance(latest, dict) else {}
    latest_fusion = latest_fusion if isinstance(latest_fusion, dict) else {}
    lines.extend(
        [
            "",
            f"最后一帧：{_plain_latest_tip(latest_fusion)}",
            f"日志：{payload.get('jsonl_log', '-')}",
            "状态：dry-run，未控制机器人",
        ]
    )
    return "\n".join(lines)


def _format_fusion_text(payload: dict[str, Any]) -> str:
    lines = [
        "感知结果",
        (
            "距离："
            f"前 {_distance_from_fusion(payload, 'front_min_distance_m')} | "
            f"左 {_distance_from_fusion(payload, 'left_min_distance_m')} | "
            f"右 {_distance_from_fusion(payload, 'right_min_distance_m')}"
        ),
        (
            "状态："
            f"场景 {payload.get('scene_risk', '-')} | "
            f"障碍 {_yes_no(_blocked_from_fusion(payload))} | "
            f"深度有效 {_percent_from_fusion(payload)}"
        ),
        f"建议：{_suggestion_from_fusion(payload)}",
        f"提示：{_plain_latest_tip(payload)}",
        "执行状态：dry-run，未控制机器人",
    ]
    if payload.get("output_path"):
        lines.append(f"JSON已保存：{payload['output_path']}")
    return "\n".join(lines)


def _plain_latest_tip(fusion: dict[str, Any]) -> str:
    summary = fusion.get("obstacle_summary") if isinstance(fusion.get("obstacle_summary"), dict) else {}
    front = _optional_float(summary.get("front_min_distance_m"))
    blocked = bool(summary.get("blocked"))
    suggestion = _suggestion_from_fusion(fusion)
    if front is not None and front <= 0.45:
        return "前方太近，停住别动。"
    if blocked:
        return f"前方被挡住，不要前进；如果要测试，只考虑 {suggestion}。"
    if front is not None and front < 1.2:
        return "前方偏近，只适合人工确认后的极小动作。"
    return "前方比较空，可以继续做人工确认的小步测试。"


def _distance_from_fusion(fusion: dict[str, Any], key: str) -> str:
    summary = fusion.get("obstacle_summary") if isinstance(fusion.get("obstacle_summary"), dict) else {}
    number = _optional_float(summary.get(key))
    return "-" if number is None else f"{number:.2f}m"


def _percent_from_fusion(fusion: dict[str, Any]) -> str:
    summary = fusion.get("obstacle_summary") if isinstance(fusion.get("obstacle_summary"), dict) else {}
    number = _optional_float(summary.get("valid_depth_ratio"))
    return "-" if number is None else f"{number * 100:.1f}%"


def _blocked_from_fusion(fusion: dict[str, Any]) -> bool | None:
    summary = fusion.get("obstacle_summary") if isinstance(fusion.get("obstacle_summary"), dict) else {}
    value = summary.get("blocked")
    return value if isinstance(value, bool) else None


def _suggestion_from_fusion(fusion: dict[str, Any]) -> str:
    dry_run = fusion.get("navigation_dry_run") if isinstance(fusion.get("navigation_dry_run"), dict) else {}
    suggestion = str(dry_run.get("suggested_action") or "-")
    labels = {
        "go_forward": "向前",
        "go_slow_forward": "慢速向前",
        "turn_left": "左转",
        "turn_right": "右转",
        "hold_position": "停住",
    }
    return f"{labels.get(suggestion, suggestion)} ({suggestion})" if suggestion != "-" else "-"


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _yes_no(value: Any) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return "-"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
