from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOL_VERSION = "0.1"
DEFAULT_EMERGENCY_STOP_DISTANCE_M = 0.45
DEFAULT_CLOSE_DISTANCE_M = 1.2
DEFAULT_MIN_VALID_DEPTH_RATIO = 0.2


def decision_status() -> dict[str, Any]:
    return {
        "ok": True,
        "source": "navigation-decision",
        "tool_version": TOOL_VERSION,
        "message": "Dry-run navigation decision gate. It reads perception JSON and never controls the robot.",
        "robot_action_executed": False,
    }


def decide_from_perception(
    perception: dict[str, Any],
    emergency_stop_distance_m: float = DEFAULT_EMERGENCY_STOP_DISTANCE_M,
    close_distance_m: float = DEFAULT_CLOSE_DISTANCE_M,
    min_valid_depth_ratio: float = DEFAULT_MIN_VALID_DEPTH_RATIO,
) -> dict[str, Any]:
    fusion = _unwrap_fusion(perception)
    if not isinstance(fusion, dict):
        return _error("invalid_perception", "Perception payload must be a JSON object.")

    obstacle = fusion.get("obstacle_summary") if isinstance(fusion.get("obstacle_summary"), dict) else {}
    dry_run = fusion.get("navigation_dry_run") if isinstance(fusion.get("navigation_dry_run"), dict) else {}
    objects = fusion.get("objects") if isinstance(fusion.get("objects"), list) else []

    ok = fusion.get("ok") is True
    scene_risk = str(fusion.get("scene_risk") or "unknown")
    front = _optional_float(obstacle.get("front_min_distance_m"))
    left = _optional_float(obstacle.get("left_min_distance_m"))
    right = _optional_float(obstacle.get("right_min_distance_m"))
    valid_ratio = _optional_float(obstacle.get("valid_depth_ratio"))
    blocked = bool(obstacle.get("blocked"))
    depth_valid = valid_ratio is not None and valid_ratio >= min_valid_depth_ratio
    suggested = str(dry_run.get("suggested_action") or "hold_position")

    object_labels = [str(item.get("label")) for item in objects if isinstance(item, dict) and item.get("label")]
    object_caution = any(label in {"person", "cat", "dog", "chair"} for label in object_labels)

    if not ok:
        intent, reason = "hold_position", "perception_not_ok"
    elif not depth_valid:
        intent, reason = "hold_position", "depth_confidence_too_low"
    elif front is None:
        intent, reason = "hold_position", "front_distance_unknown"
    elif front <= emergency_stop_distance_m:
        intent, reason = "hold_position", "front_inside_emergency_stop_distance"
    elif blocked or scene_risk == "blocked":
        intent, reason = _turn_toward_open_side(left, right), "front_blocked_choose_more_open_side"
    elif suggested in {"turn_left", "turn_right"}:
        intent, reason = suggested, "depth_dry_run_turn_suggestion"
    elif front < close_distance_m or scene_risk == "close":
        intent, reason = "go_slow_forward", "front_clear_but_close"
    else:
        intent, reason = "go_forward", "front_clear"

    profile = _motion_profile(intent, object_caution=object_caution)
    safety_notes = _safety_notes(
        front=front,
        valid_ratio=valid_ratio,
        object_labels=object_labels,
        emergency_stop_distance_m=emergency_stop_distance_m,
        min_valid_depth_ratio=min_valid_depth_ratio,
    )

    return {
        "ok": True,
        "source": "navigation-decision",
        "mode": "navigation_decision_dry_run",
        "timestamp": _now_iso(),
        "decision": {
            "intent": intent,
            "reason": reason,
            "motion_profile": profile,
            "execution_enabled": False,
            "requires_manual_enable": True,
        },
        "inputs": {
            "scene_risk": scene_risk,
            "front_min_distance_m": front,
            "left_min_distance_m": left,
            "right_min_distance_m": right,
            "blocked": blocked,
            "valid_depth_ratio": valid_ratio,
            "depth_valid": depth_valid,
            "object_labels": object_labels,
            "object_count": len(objects),
            "perception_suggested_action": suggested,
        },
        "safety": {
            "emergency_stop_distance_m": emergency_stop_distance_m,
            "close_distance_m": close_distance_m,
            "min_valid_depth_ratio": min_valid_depth_ratio,
            "notes": safety_notes,
        },
        "robot_action_executed": False,
    }


def decide_from_file(
    perception_path: str | Path,
    output_path: str | Path | None = None,
    emergency_stop_distance_m: float = DEFAULT_EMERGENCY_STOP_DISTANCE_M,
    close_distance_m: float = DEFAULT_CLOSE_DISTANCE_M,
    min_valid_depth_ratio: float = DEFAULT_MIN_VALID_DEPTH_RATIO,
) -> dict[str, Any]:
    payload = _load_json(perception_path)
    if payload.get("ok") is not True:
        return payload

    result = decide_from_perception(
        payload["json"],
        emergency_stop_distance_m=emergency_stop_distance_m,
        close_distance_m=close_distance_m,
        min_valid_depth_ratio=min_valid_depth_ratio,
    )
    result["perception_path"] = str(Path(perception_path).expanduser())
    _write_json_if_requested(result, output_path)
    return result


def decide_log(
    input_jsonl: str | Path,
    output_jsonl: str | Path | None = None,
    summary_output: str | Path | None = None,
    emergency_stop_distance_m: float = DEFAULT_EMERGENCY_STOP_DISTANCE_M,
    close_distance_m: float = DEFAULT_CLOSE_DISTANCE_M,
    min_valid_depth_ratio: float = DEFAULT_MIN_VALID_DEPTH_RATIO,
) -> dict[str, Any]:
    path = Path(input_jsonl).expanduser()
    if not path.is_file():
        return _error("log_not_found", f"JSONL log does not exist: {path}", input_jsonl=str(path))

    decisions: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    output_handle = None
    try:
        if output_jsonl is not None:
            output = Path(output_jsonl).expanduser()
            output.parent.mkdir(parents=True, exist_ok=True)
            output_handle = output.open("w", encoding="utf-8")

        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except Exception as exc:
                error = _error("invalid_jsonl_line", str(exc), line_number=line_number)
                errors.append(error)
                continue

            decision = decide_from_perception(
                event,
                emergency_stop_distance_m=emergency_stop_distance_m,
                close_distance_m=close_distance_m,
                min_valid_depth_ratio=min_valid_depth_ratio,
            )
            decision["line_number"] = line_number
            if isinstance(event, dict) and "index" in event:
                decision["index"] = event.get("index")
            decisions.append(decision)
            if output_handle is not None:
                output_handle.write(json.dumps(decision, ensure_ascii=False) + "\n")
    finally:
        if output_handle is not None:
            output_handle.close()

    summary = _summarize_decisions(decisions, errors)
    summary["input_jsonl"] = str(path)
    if output_jsonl is not None:
        summary["output_jsonl"] = str(Path(output_jsonl).expanduser())
    _write_json_if_requested(summary, summary_output)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        payload = decision_status()
    elif args.command == "decide":
        payload = decide_from_file(
            args.perception,
            output_path=args.output,
            emergency_stop_distance_m=args.emergency_stop_distance_m,
            close_distance_m=args.close_distance_m,
            min_valid_depth_ratio=args.min_valid_depth_ratio,
        )
    elif args.command == "decide-log":
        payload = decide_log(
            args.input_jsonl,
            output_jsonl=args.output_jsonl,
            summary_output=args.summary_output,
            emergency_stop_distance_m=args.emergency_stop_distance_m,
            close_distance_m=args.close_distance_m,
            min_valid_depth_ratio=args.min_valid_depth_ratio,
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
        prog="python -m server.navigation_decision_tool",
        description="Dry-run navigation decision gate for OpenDuck perception JSON.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    status_parser = subparsers.add_parser("status", help="Print tool status.")
    _add_format_arg(status_parser)

    decide_parser = subparsers.add_parser("decide", help="Read one perception_fusion.json and print one dry-run decision.")
    decide_parser.add_argument("--perception", required=True, help="Path to perception_fusion.json.")
    decide_parser.add_argument("--output", help="Optional output path for navigation_decision.json.")
    _add_format_arg(decide_parser)
    _add_threshold_args(decide_parser)

    log_parser = subparsers.add_parser("decide-log", help="Read perception_fusion.jsonl and summarize dry-run decisions.")
    log_parser.add_argument("--input-jsonl", required=True, help="Path to perception_fusion.jsonl.")
    log_parser.add_argument("--output-jsonl", help="Optional path for per-frame navigation decisions.")
    log_parser.add_argument("--summary-output", help="Optional path for decision summary JSON.")
    _add_format_arg(log_parser)
    _add_threshold_args(log_parser)
    return parser


def _add_format_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("json", "text"), default="json", help="Output format.")


def _add_threshold_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--emergency-stop-distance-m", type=float, default=DEFAULT_EMERGENCY_STOP_DISTANCE_M)
    parser.add_argument("--close-distance-m", type=float, default=DEFAULT_CLOSE_DISTANCE_M)
    parser.add_argument("--min-valid-depth-ratio", type=float, default=DEFAULT_MIN_VALID_DEPTH_RATIO)


def _unwrap_fusion(payload: dict[str, Any]) -> dict[str, Any]:
    fusion = payload.get("fusion")
    return fusion if isinstance(fusion, dict) else payload


def _turn_toward_open_side(left: float | None, right: float | None) -> str:
    left_value = -1.0 if left is None else left
    right_value = -1.0 if right is None else right
    return "turn_right" if right_value >= left_value else "turn_left"


def _motion_profile(intent: str, object_caution: bool) -> dict[str, Any]:
    if intent == "go_forward":
        linear = 0.05 if object_caution else 0.08
        angular = 0.0
        duration = 0.5
    elif intent == "go_slow_forward":
        linear = 0.03 if object_caution else 0.04
        angular = 0.0
        duration = 0.4
    elif intent == "turn_left":
        linear = 0.0
        angular = 0.35
        duration = 0.35
    elif intent == "turn_right":
        linear = 0.0
        angular = -0.35
        duration = 0.35
    else:
        linear = 0.0
        angular = 0.0
        duration = 0.0
    return {
        "linear_velocity_mps": linear,
        "angular_velocity_radps": angular,
        "duration_s": duration,
        "not_for_direct_execution": True,
    }


def _safety_notes(
    front: float | None,
    valid_ratio: float | None,
    object_labels: list[str],
    emergency_stop_distance_m: float,
    min_valid_depth_ratio: float,
) -> list[str]:
    notes: list[str] = []
    if valid_ratio is None or valid_ratio < min_valid_depth_ratio:
        notes.append("depth signal is not reliable enough for movement")
    if front is None:
        notes.append("front distance is unknown")
    elif front <= emergency_stop_distance_m:
        notes.append("front obstacle is inside emergency stop distance")
    if object_labels:
        notes.append("YOLO objects are treated as caution signals, depth remains primary")
    notes.append("dry-run only; no robot command was sent")
    return notes


def _summarize_decisions(decisions: list[dict[str, Any]], errors: list[dict[str, Any]]) -> dict[str, Any]:
    intents = Counter(str(item.get("decision", {}).get("intent")) for item in decisions)
    reasons = Counter(str(item.get("decision", {}).get("reason")) for item in decisions)
    fronts = [
        float(item["inputs"]["front_min_distance_m"])
        for item in decisions
        if isinstance(item.get("inputs"), dict) and item["inputs"].get("front_min_distance_m") is not None
    ]
    return {
        "ok": not errors,
        "source": "navigation-decision-log",
        "mode": "navigation_decision_log_summary",
        "timestamp": _now_iso(),
        "frames": len(decisions),
        "errors": errors,
        "intent_counts": dict(intents),
        "reason_counts": dict(reasons),
        "front_min_distance_m": min(fronts) if fronts else None,
        "front_max_distance_m": max(fronts) if fronts else None,
        "front_avg_distance_m": round(sum(fronts) / len(fronts), 4) if fronts else None,
        "robot_action_executed": False,
    }


def _format_text(payload: dict[str, Any]) -> str:
    if payload.get("ok") is not True:
        return "\n".join(
            [
                "导航决策：失败",
                f"原因：{payload.get('message') or payload.get('error') or '未知错误'}",
                f"文件：{payload.get('perception_path') or payload.get('input_jsonl') or '-'}",
                "状态：未控制机器人",
            ]
        )

    if payload.get("source") == "navigation-decision-log":
        return _format_log_text(payload)

    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    profile = decision.get("motion_profile") if isinstance(decision.get("motion_profile"), dict) else {}
    intent = str(decision.get("intent") or "unknown")
    reason = str(decision.get("reason") or "unknown")
    blocked = inputs.get("blocked")
    depth_valid = inputs.get("depth_valid")

    lines = [
        f"导航决策：{_intent_label(intent)}",
        f"原因：{_reason_label(reason)}",
        (
            "距离："
            f"前 {_format_m(inputs.get('front_min_distance_m'))} | "
            f"左 {_format_m(inputs.get('left_min_distance_m'))} | "
            f"右 {_format_m(inputs.get('right_min_distance_m'))}"
        ),
        (
            "状态："
            f"场景 {inputs.get('scene_risk', '-')} | "
            f"障碍 {_yes_no(blocked)} | "
            f"深度有效 {_format_percent(inputs.get('valid_depth_ratio'))} | "
            f"深度可信 {_yes_no(depth_valid)}"
        ),
        (
            "建议动作："
            f"线速度 {_format_float(profile.get('linear_velocity_mps'))} m/s | "
            f"角速度 {_format_float(profile.get('angular_velocity_radps'))} rad/s | "
            f"时长 {_format_float(profile.get('duration_s'))} s"
        ),
        f"人工提示：{_human_tip(intent, inputs)}",
        "执行状态：dry-run，未控制机器人",
    ]
    if payload.get("perception_path"):
        lines.append(f"输入文件：{payload['perception_path']}")
    if payload.get("output_path"):
        lines.append(f"JSON已保存：{payload['output_path']}")
    return "\n".join(lines)


def _format_log_text(payload: dict[str, Any]) -> str:
    lines = [
        "导航日志汇总",
        f"帧数：{payload.get('frames', 0)}",
        (
            "前方距离："
            f"最小 {_format_m(payload.get('front_min_distance_m'))} | "
            f"最大 {_format_m(payload.get('front_max_distance_m'))} | "
            f"平均 {_format_m(payload.get('front_avg_distance_m'))}"
        ),
        f"动作统计：{payload.get('intent_counts', {})}",
        f"状态：{'正常' if payload.get('ok') else '有错误'}，未控制机器人",
    ]
    return "\n".join(lines)


def _intent_label(intent: str) -> str:
    labels = {
        "go_forward": "可以小步向前",
        "go_slow_forward": "慢速小步向前",
        "turn_left": "原地向左转一点",
        "turn_right": "原地向右转一点",
        "hold_position": "停住别动",
    }
    return f"{labels.get(intent, '未知')} ({intent})"


def _reason_label(reason: str) -> str:
    labels = {
        "front_clear": "前方比较空",
        "front_clear_but_close": "前方没堵死，但距离偏近",
        "front_blocked_choose_more_open_side": "前方被挡住，选择更空的一侧",
        "depth_dry_run_turn_suggestion": "深度检测建议转向",
        "front_inside_emergency_stop_distance": "前方太近，进入急停距离",
        "front_distance_unknown": "看不到前方距离",
        "depth_confidence_too_low": "深度数据不够可信",
        "perception_not_ok": "感知结果异常",
    }
    return f"{labels.get(reason, reason)} ({reason})"


def _human_tip(intent: str, inputs: dict[str, Any]) -> str:
    front = _optional_float(inputs.get("front_min_distance_m"))
    if intent == "hold_position":
        return "不要发运动命令，先人工挪开障碍或重新观察。"
    if front is not None and front < DEFAULT_CLOSE_DISTANCE_M:
        return "距离偏近，只适合人工确认后的极小动作。"
    if intent in {"turn_left", "turn_right"}:
        return "前方不适合前进，只能人工确认后小角度转向。"
    return "环境看起来较空，仍建议只执行一次很短的小步测试。"


def _format_m(value: Any) -> str:
    number = _optional_float(value)
    return "-" if number is None else f"{number:.2f} m"


def _format_percent(value: Any) -> str:
    number = _optional_float(value)
    return "-" if number is None else f"{number * 100:.1f}%"


def _format_float(value: Any) -> str:
    number = _optional_float(value)
    return "-" if number is None else f"{number:.2f}"


def _yes_no(value: Any) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return "-"


def _load_json(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        return _error("perception_not_found", f"Perception file does not exist: {resolved}", perception_path=str(resolved))
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception as exc:
        return _error("perception_read_failed", str(exc), perception_path=str(resolved))
    if not isinstance(payload, dict):
        return _error("invalid_perception", "Perception payload must be a JSON object.", perception_path=str(resolved))
    return {"ok": True, "json": payload}


def _write_json_if_requested(payload: dict[str, Any], output_path: str | Path | None) -> None:
    if output_path is None:
        return
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["output_path"] = str(output)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _error(error: str, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "source": "navigation-decision",
        "error": error,
        "message": message,
        "timestamp": _now_iso(),
        "robot_action_executed": False,
    }
    payload.update(extra)
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
