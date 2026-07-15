from __future__ import annotations

import argparse
import json
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.config import ROBOT_COMMAND_HOST, ROBOT_COMMAND_PORT, ROBOT_COMMAND_TIMEOUT_SECONDS
from server.imu_posture_tool import read_posture
from server.navigation_decision_tool import decide_from_perception
from server.navigation_perception_tool import (
    DEFAULT_ONNX_MODEL,
    observe_loop,
)
from server.pi_health_check_tool import run_health_check
from server.realsense_depth_tool import DEFAULT_DEPTH_SCALE, DEFAULT_V4L2_DEPTH_DEVICE


TOOL_VERSION = "0.1"
SOURCE = "navigation-closed-loop"
DEFAULT_COUNT = 3
MAX_COUNT = 10
DEFAULT_MIN_FRONT_DISTANCE_M = 0.8
DEFAULT_MIN_VALID_DEPTH_RATIO = 0.5
EMERGENCY_STOP_DISTANCE_M = 0.45
DEFAULT_LIN_X = 0.04
MAX_LIN_X = 0.05
DEFAULT_TTL_S = 0.25
MAX_TTL_S = 0.25
DEFAULT_REQUIRE_IMU = True


def status() -> dict[str, Any]:
    return {
        "ok": True,
        "source": SOURCE,
        "tool_version": TOOL_VERSION,
        "mode": "low_speed_single_step_closed_loop",
        "default_execute": False,
        "runtime": {
            "host": ROBOT_COMMAND_HOST,
            "port": ROBOT_COMMAND_PORT,
            "listening": _runtime_listening(),
        },
        "limits": {
            "max_count": MAX_COUNT,
            "min_front_distance_m": DEFAULT_MIN_FRONT_DISTANCE_M,
            "min_valid_depth_ratio": DEFAULT_MIN_VALID_DEPTH_RATIO,
            "max_lin_x": MAX_LIN_X,
            "max_ttl_s": MAX_TTL_S,
            "forward_only": True,
            "require_imu": DEFAULT_REQUIRE_IMU,
        },
        "robot_action_executed": False,
    }


def run_closed_loop(
    *,
    count: int = DEFAULT_COUNT,
    interval: float = 1.0,
    execute: bool = False,
    output_root: str | Path | None = None,
    min_front_distance_m: float = DEFAULT_MIN_FRONT_DISTANCE_M,
    min_valid_depth_ratio: float = DEFAULT_MIN_VALID_DEPTH_RATIO,
    lin_x: float = DEFAULT_LIN_X,
    ttl_s: float = DEFAULT_TTL_S,
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
    require_imu: bool = DEFAULT_REQUIRE_IMU,
) -> dict[str, Any]:
    count = _clamp_int(count, low=1, high=MAX_COUNT)
    interval = max(0.0, float(interval))
    min_front_distance_m = max(EMERGENCY_STOP_DISTANCE_M, float(min_front_distance_m))
    min_valid_depth_ratio = max(0.0, min(1.0, float(min_valid_depth_ratio)))
    lin_x = max(0.0, min(MAX_LIN_X, float(lin_x)))
    ttl_s = max(0.0, min(MAX_TTL_S, float(ttl_s)))

    root = Path(output_root).expanduser() if output_root is not None else _default_output_root()
    root.mkdir(parents=True, exist_ok=True)
    health_path = root / "health.json"
    log_path = root / "closed_loop.jsonl"

    health = run_health_check(output_path=health_path)
    rounds: list[dict[str, Any]] = []
    any_robot_action = False

    for index in range(1, count + 1):
        round_root = root / f"round_{index:03d}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        round_root.mkdir(parents=True, exist_ok=True)

        perception_loop = observe_loop(
            output_root=round_root,
            count=1,
            interval=0.0,
            depth_backend=depth_backend,
            depth_device=depth_device,
            color_device=color_device,
            onnx_model_path=onnx_model_path,
            width=width,
            height=height,
            fps=fps,
            depth_warmup_frames=depth_warmup_frames,
            color_warmup_frames=color_warmup_frames,
            conf=conf,
            threads=threads,
            depth_scale=depth_scale,
        )
        perception_event = _last_iteration(perception_loop)
        fusion = _fusion_from_event(perception_event)

        decision = decide_from_perception(
            fusion,
            emergency_stop_distance_m=EMERGENCY_STOP_DISTANCE_M,
            close_distance_m=min_front_distance_m,
            min_valid_depth_ratio=min_valid_depth_ratio,
        )
        output_dir = Path(str(perception_event.get("output_dir") or round_root)).expanduser()
        perception_path = output_dir / "perception_fusion.json"
        decision_path = output_dir / "navigation_decision.json"
        decision["perception_path"] = str(perception_path)
        decision_path.write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")
        decision["output_path"] = str(decision_path)

        imu_posture = read_posture(output_path=output_dir / "imu_posture.json") if require_imu else None
        gate = _closed_loop_gate(
            decision,
            health=health,
            imu_posture=imu_posture,
            require_imu=require_imu,
            min_front_distance_m=min_front_distance_m,
            min_valid_depth_ratio=min_valid_depth_ratio,
        )
        execution = _execute_round_if_allowed(
            execute=execute,
            gate=gate,
            lin_x=lin_x,
            ttl_s=ttl_s,
        )
        any_robot_action = any_robot_action or execution.get("robot_action_executed") is True

        round_payload = {
            "ok": perception_event.get("ok") is True and decision.get("ok") is True,
            "source": SOURCE,
            "index": index,
            "count": count,
            "timestamp": _now_iso(),
            "output_dir": str(output_dir),
            "perception": _round_perception_summary(fusion),
            "imu_posture": _round_imu_summary(imu_posture),
            "decision": decision,
            "gate": gate,
            "execution": execution,
            "robot_action_executed": execution.get("robot_action_executed") is True,
        }
        rounds.append(round_payload)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(round_payload, ensure_ascii=False) + "\n")

        if index < count:
            time.sleep(interval)

    return {
        "ok": all(item.get("ok") is True for item in rounds),
        "source": SOURCE,
        "mode": "closed_loop_dry_run" if not execute else "closed_loop_execute",
        "timestamp": _now_iso(),
        "execute": execute,
        "output_root": str(root),
        "health_path": str(health_path),
        "jsonl_log": str(log_path),
        "health": _health_summary(health),
        "rounds": rounds,
        "robot_action_executed": any_robot_action,
    }


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        payload = status()
    elif args.command == "run":
        payload = run_closed_loop(
            count=args.count,
            interval=args.interval,
            execute=args.execute,
            output_root=args.output_root,
            min_front_distance_m=args.min_front_distance_m,
            min_valid_depth_ratio=args.min_valid_depth_ratio,
            lin_x=args.lin_x,
            ttl_s=args.ttl_s,
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
            require_imu=not args.skip_imu,
        )
    else:
        parser.error("missing command")

    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_format_text(payload))
    return 0 if payload.get("ok") is True or args.command == "status" else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.navigation_closed_loop_tool",
        description="Safe low-speed closed-loop navigation test tool for OpenDuck.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Print tool status.")
    status_parser.add_argument("--format", choices=("json", "text"), default="text")

    run_parser = subparsers.add_parser("run", help="Run a limited closed-loop navigation test.")
    run_parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    run_parser.add_argument("--interval", type=float, default=1.0)
    run_parser.add_argument("--execute", action="store_true", help="Actually send a short forward command when safe.")
    run_parser.add_argument("--format", choices=("json", "text"), default="text")
    run_parser.add_argument("--output-root")
    run_parser.add_argument("--min-front-distance-m", type=float, default=DEFAULT_MIN_FRONT_DISTANCE_M)
    run_parser.add_argument("--min-valid-depth-ratio", type=float, default=DEFAULT_MIN_VALID_DEPTH_RATIO)
    run_parser.add_argument("--lin-x", type=float, default=DEFAULT_LIN_X)
    run_parser.add_argument("--ttl-s", type=float, default=DEFAULT_TTL_S)
    run_parser.add_argument("--depth-backend", choices=("auto", "pyrealsense2", "v4l2"), default="v4l2")
    run_parser.add_argument("--depth-device", default=DEFAULT_V4L2_DEPTH_DEVICE)
    run_parser.add_argument("--color-device", default="/dev/video4")
    run_parser.add_argument("--onnx-model", default=str(DEFAULT_ONNX_MODEL))
    run_parser.add_argument("--width", type=int, default=640)
    run_parser.add_argument("--height", type=int, default=480)
    run_parser.add_argument("--fps", type=int, default=15)
    run_parser.add_argument("--depth-warmup-frames", type=int, default=2)
    run_parser.add_argument("--color-warmup-frames", type=int, default=2)
    run_parser.add_argument("--conf", type=float, default=0.25)
    run_parser.add_argument("--threads", type=int, default=1)
    run_parser.add_argument("--depth-scale", type=float, default=DEFAULT_DEPTH_SCALE)
    run_parser.add_argument("--skip-imu", action="store_true", help="Do not require BNO055 posture before execution.")
    return parser


def _closed_loop_gate(
    decision: dict[str, Any],
    *,
    health: dict[str, Any],
    imu_posture: dict[str, Any] | None,
    require_imu: bool,
    min_front_distance_m: float,
    min_valid_depth_ratio: float,
) -> dict[str, Any]:
    allow = True
    deny_reasons: list[str] = []
    warnings: list[str] = []

    decision_block = decision.get("decision") if isinstance(decision.get("decision"), dict) else {}
    inputs = decision.get("inputs") if isinstance(decision.get("inputs"), dict) else {}
    intent = str(decision_block.get("intent") or "unknown")
    front = _optional_float(inputs.get("front_min_distance_m"))
    valid_ratio = _optional_float(inputs.get("valid_depth_ratio"))
    blocked = inputs.get("blocked") is True
    depth_valid = inputs.get("depth_valid") is True

    health_status = str(health.get("overall_status") or "warning")
    if health_status == "critical":
        allow = False
        deny_reasons.append("health_critical")
    elif health_status == "warning":
        warnings.extend(str(item) for item in health.get("summary", []) if isinstance(item, str))

    imu_safe = True
    if require_imu:
        imu_safe = isinstance(imu_posture, dict) and imu_posture.get("ok") is True and imu_posture.get("safe_for_navigation") is True
        if not imu_safe:
            allow = False
            deny_reasons.extend(_imu_deny_reasons(imu_posture))
        if isinstance(imu_posture, dict):
            warnings.extend(str(item) for item in imu_posture.get("warnings", []) if isinstance(item, str))

    if intent not in {"go_forward", "go_slow_forward"}:
        allow = False
        deny_reasons.append(f"intent_not_forward:{intent}")
    if front is None:
        allow = False
        deny_reasons.append("front_distance_unknown")
    elif front <= EMERGENCY_STOP_DISTANCE_M:
        allow = False
        deny_reasons.append(f"front_too_close:{front:.3f}m <= {EMERGENCY_STOP_DISTANCE_M:.2f}m")
    elif front < min_front_distance_m:
        allow = False
        deny_reasons.append(f"front_below_safe_distance:{front:.3f}m < {min_front_distance_m:.2f}m")
    if blocked:
        allow = False
        deny_reasons.append("blocked_true")
    if valid_ratio is None:
        allow = False
        deny_reasons.append("valid_depth_ratio_unknown")
    elif valid_ratio < min_valid_depth_ratio:
        allow = False
        deny_reasons.append(f"depth_ratio_low:{valid_ratio:.3f} < {min_valid_depth_ratio:.2f}")
    if not depth_valid:
        allow = False
        deny_reasons.append("decision_depth_not_valid")

    return {
        "ok": True,
        "allow_forward_step": allow,
        "intent": intent,
        "front_min_distance_m": front,
        "valid_depth_ratio": valid_ratio,
        "blocked": blocked,
        "imu_required": require_imu,
        "imu_posture_ok": imu_safe,
        "imu_posture_summary": None if imu_posture is None else imu_posture.get("summary"),
        "min_front_distance_m": min_front_distance_m,
        "min_valid_depth_ratio": min_valid_depth_ratio,
        "deny_reasons": _unique(deny_reasons),
        "warnings": _unique(warnings),
        "robot_action_executed": False,
    }


def _execute_round_if_allowed(*, execute: bool, gate: dict[str, Any], lin_x: float, ttl_s: float) -> dict[str, Any]:
    if gate.get("allow_forward_step") is not True:
        return {
            "ok": True,
            "executed": False,
            "reason": "gate_denied",
            "message": _deny_message(gate),
            "robot_action_executed": False,
        }
    if not execute:
        return {
            "ok": True,
            "executed": False,
            "reason": "dry_run",
            "message": "dry-run，未发动作",
            "robot_action_executed": False,
        }

    move_payload = {
        "type": "robot_command",
        "command_id": "joystick_velocity",
        "priority": "high",
        "params": {
            "lin_x": lin_x,
            "lin_y": 0.0,
            "yaw": 0.0,
            "ttl_s": ttl_s,
        },
    }
    stop_payload = {
        "type": "robot_command",
        "command_id": "stop",
        "priority": "emergency",
        "params": {},
    }

    move_result = _send_robot_payload(move_payload)
    if move_result.get("ok") is not True:
        return {
            "ok": False,
            "executed": False,
            "reason": "robot_command_failed",
            "message": move_result.get("message"),
            "move_result": move_result,
            "robot_action_executed": False,
        }

    time.sleep(ttl_s)
    stop_result = _send_robot_payload(stop_payload)
    return {
        "ok": stop_result.get("ok") is True,
        "executed": True,
        "reason": "executed_forward_then_stop",
        "message": f"已发送低速前进 {ttl_s:.2f}s，然后 stop",
        "lin_x": lin_x,
        "ttl_s": ttl_s,
        "move_result": move_result,
        "stop_result": stop_result,
        "robot_action_executed": True,
    }


def _send_robot_payload(payload: dict[str, Any]) -> dict[str, Any]:
    encoded = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        with socket.create_connection(
            (ROBOT_COMMAND_HOST, ROBOT_COMMAND_PORT),
            timeout=ROBOT_COMMAND_TIMEOUT_SECONDS,
        ) as sock:
            sock.settimeout(ROBOT_COMMAND_TIMEOUT_SECONDS)
            sock.sendall(encoded)
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            raw = sock.recv(4096)
    except ConnectionRefusedError:
        return {
            "ok": False,
            "error": "robot_runtime_unavailable",
            "message": f"机器人运行时未启动：{ROBOT_COMMAND_HOST}:{ROBOT_COMMAND_PORT} 没有监听。",
        }
    except OSError as exc:
        return {
            "ok": False,
            "error": "robot_runtime_error",
            "message": f"机器人命令发送失败：{exc}",
        }

    if not raw:
        return {"ok": True, "message": "命令已发送，机器人运行时没有返回内容。"}
    try:
        response = json.loads(raw.decode("utf-8", errors="replace").strip())
    except json.JSONDecodeError:
        return {"ok": False, "error": "bad_runtime_response", "message": raw.decode("utf-8", errors="replace")}
    if isinstance(response, dict):
        return response
    return {"ok": False, "error": "bad_runtime_response", "message": "机器人运行时返回格式不是 JSON 对象。"}


def _format_text(payload: dict[str, Any]) -> str:
    if payload.get("source") == SOURCE and "rounds" not in payload:
        runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
        limits = payload.get("limits") if isinstance(payload.get("limits"), dict) else {}
        return "\n".join(
            [
                "OpenDuck 导航闭环工具",
                f"版本：{payload.get('tool_version')}",
                f"模式：默认 dry-run，不控制机器人",
                f"机器人运行时：{runtime.get('host')}:{runtime.get('port')} 监听={_yes_no(runtime.get('listening'))}",
                f"安全阈值：前方 >= {limits.get('min_front_distance_m')}m，深度有效 >= {limits.get('min_valid_depth_ratio')}",
                "真执行必须加：--execute",
            ]
        )

    lines = [
        "OpenDuck 导航闭环结果",
        f"模式：{'真执行' if payload.get('execute') else 'dry-run'}",
        f"输出目录：{payload.get('output_root')}",
        f"健康检查：{payload.get('health', {}).get('overall_status', '-')}",
        "",
    ]
    rounds = payload.get("rounds") if isinstance(payload.get("rounds"), list) else []
    for item in rounds:
        perception = item.get("perception") if isinstance(item.get("perception"), dict) else {}
        decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        decision_block = decision.get("decision") if isinstance(decision.get("decision"), dict) else {}
        gate = item.get("gate") if isinstance(item.get("gate"), dict) else {}
        execution = item.get("execution") if isinstance(item.get("execution"), dict) else {}
        lines.extend(
            [
                f"闭环 {item.get('index')}/{item.get('count')}",
                (
                    "感知："
                    f"前 {_format_m(perception.get('front_min_distance_m'))} | "
                    f"左 {_format_m(perception.get('left_min_distance_m'))} | "
                    f"右 {_format_m(perception.get('right_min_distance_m'))} | "
                    f"深度 {_format_percent(perception.get('valid_depth_ratio'))}"
                ),
                f"决策：{_intent_label(str(decision_block.get('intent') or 'unknown'))}",
                f"安全门：{'通过' if gate.get('allow_forward_step') else '拒绝'}{_deny_suffix(gate)}",
                f"执行：{execution.get('message') or '-'}",
                "",
            ]
        )
    lines.append(f"总执行状态：{'已控制机器人' if payload.get('robot_action_executed') else '未控制机器人'}")
    lines.append(f"日志：{payload.get('jsonl_log')}")
    return "\n".join(lines).rstrip()


def _round_perception_summary(fusion: dict[str, Any]) -> dict[str, Any]:
    summary = fusion.get("obstacle_summary") if isinstance(fusion.get("obstacle_summary"), dict) else {}
    return {
        "ok": fusion.get("ok") is True,
        "scene_risk": fusion.get("scene_risk"),
        "front_min_distance_m": _optional_float(summary.get("front_min_distance_m")),
        "left_min_distance_m": _optional_float(summary.get("left_min_distance_m")),
        "right_min_distance_m": _optional_float(summary.get("right_min_distance_m")),
        "blocked": summary.get("blocked") is True,
        "valid_depth_ratio": _optional_float(summary.get("valid_depth_ratio")),
        "object_count": fusion.get("object_count"),
    }


def _round_imu_summary(imu_posture: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(imu_posture, dict):
        return None
    return {
        "ok": imu_posture.get("ok") is True,
        "status": imu_posture.get("status"),
        "safe_for_navigation": imu_posture.get("safe_for_navigation"),
        "summary": imu_posture.get("summary"),
        "posture": imu_posture.get("posture"),
        "calibration": imu_posture.get("calibration"),
        "deny_reasons": imu_posture.get("deny_reasons", []),
        "warnings": imu_posture.get("warnings", []),
    }


def _imu_deny_reasons(imu_posture: dict[str, Any] | None) -> list[str]:
    if not isinstance(imu_posture, dict):
        return ["imu_posture_missing"]
    reasons = [str(item) for item in imu_posture.get("deny_reasons", []) if isinstance(item, str)]
    if reasons:
        return reasons
    if imu_posture.get("ok") is not True:
        return [str(imu_posture.get("error") or "imu_posture_not_ok")]
    return ["imu_posture_not_safe"]


def _last_iteration(payload: dict[str, Any]) -> dict[str, Any]:
    iterations = payload.get("iterations") if isinstance(payload.get("iterations"), list) else []
    return iterations[-1] if iterations and isinstance(iterations[-1], dict) else {"ok": False, "fusion": payload}


def _fusion_from_event(event: dict[str, Any]) -> dict[str, Any]:
    fusion = event.get("fusion")
    return fusion if isinstance(fusion, dict) else {"ok": False, "error": "missing_fusion"}


def _health_summary(health: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": health.get("ok") is True,
        "overall_status": health.get("overall_status"),
        "summary": health.get("summary"),
        "robot_action_executed": False,
    }


def _runtime_listening() -> bool:
    try:
        with socket.create_connection((ROBOT_COMMAND_HOST, ROBOT_COMMAND_PORT), timeout=0.2):
            return True
    except OSError:
        return False


def _default_output_root() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(f"/tmp/openduck_closed_loop_{stamp}")


def _deny_message(gate: dict[str, Any]) -> str:
    reasons = gate.get("deny_reasons") if isinstance(gate.get("deny_reasons"), list) else []
    return "拒绝动作，原因：" + ("；".join(str(item) for item in reasons) if reasons else "安全门未通过")


def _deny_suffix(gate: dict[str, Any]) -> str:
    if gate.get("allow_forward_step"):
        return ""
    reasons = gate.get("deny_reasons") if isinstance(gate.get("deny_reasons"), list) else []
    return "，原因：" + ("；".join(str(item) for item in reasons) if reasons else "未知")


def _intent_label(intent: str) -> str:
    labels = {
        "go_forward": "可以小步向前",
        "go_slow_forward": "慢速小步向前",
        "turn_left": "只建议左转，不执行",
        "turn_right": "只建议右转，不执行",
        "hold_position": "停住别动",
    }
    return f"{labels.get(intent, '未知')} ({intent})"


def _format_m(value: Any) -> str:
    number = _optional_float(value)
    return "-" if number is None else f"{number:.2f}m"


def _format_percent(value: Any) -> str:
    number = _optional_float(value)
    return "-" if number is None else f"{number * 100:.1f}%"


def _yes_no(value: Any) -> str:
    return "是" if value is True else "否"


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_int(value: int, *, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
