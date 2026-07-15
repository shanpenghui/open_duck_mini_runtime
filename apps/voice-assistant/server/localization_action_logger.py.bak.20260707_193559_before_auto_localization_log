from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.localization_state_tool import (
    DEFAULT_GYRO_DEADBAND_DPS,
    record_action,
)


TOOL_VERSION = "0.1"
SOURCE = "localization-action-logger"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACTION_LOG = PROJECT_ROOT / "data" / "localization" / "localization_action_log.jsonl"

TURN_COMMANDS = {
    "turn_degrees",
    "turn_left_small",
    "turn_right_small",
    "turn_left_3s",
    "turn_right_3s",
}

MOTION_COMMANDS = {
    "walk_forward_step",
    "strafe_left_step",
    "strafe_right_step",
}

SUPPORTED_COMMANDS = TURN_COMMANDS | MOTION_COMMANDS


def logger_status() -> dict[str, Any]:
    return {
        "ok": True,
        "source": SOURCE,
        "tool_version": TOOL_VERSION,
        "message": (
            "\u53ea\u8bfb\u5b9a\u4f4d\u52a8\u4f5c\u8bb0\u5f55\u5de5\u5177\uff1a\u53ea\u66f4\u65b0\u5b9a\u4f4d\uff0c\u4e0d\u63a7\u5236\u673a\u5668\u4eba\u52a8\u4f5c\u3002"
        ),
        "supported_commands": sorted(SUPPORTED_COMMANDS),
        "default_action_log": str(DEFAULT_ACTION_LOG),
        "robot_action_executed": False,
    }


def log_action(
    *,
    command_id: str,
    params: dict[str, Any] | None = None,
    gyro_duration_s: float = 0.0,
    gyro_deadband_dps: float = DEFAULT_GYRO_DEADBAND_DPS,
    state_path: str | Path | None = None,
    history_path: str | Path | None = None,
    action_log_path: str | Path | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    params = dict(params or {})
    command_id = str(command_id)
    if command_id not in SUPPORTED_COMMANDS:
        return _error(
            "unsupported_command",
            f"Unsupported localization action: {command_id}",
            command_id=command_id,
            supported_commands=sorted(SUPPORTED_COMMANDS),
        )

    is_turn = command_id in TURN_COMMANDS
    use_gyro = is_turn and gyro_duration_s > 0
    state = record_action(
        command_id=command_id,
        params=params,
        state_path=state_path,
        history_path=history_path,
        output_path=None,
        skip_imu=False,
        gyro_duration_s=gyro_duration_s if use_gyro else 0.0,
        gyro_deadband_dps=gyro_deadband_dps,
    )

    last_action = state.get("last_action") if isinstance(state.get("last_action"), dict) else {}
    estimated_delta = (
        last_action.get("estimated_delta")
        if isinstance(last_action.get("estimated_delta"), dict)
        else {}
    )
    source = "gyro_z_integrated" if estimated_delta.get("source") == "gyro_z_integrated" else "action_estimate"
    pose = state.get("pose") if isinstance(state.get("pose"), dict) else {}

    payload = {
        "ok": state.get("ok") is True,
        "source": SOURCE,
        "tool_version": TOOL_VERSION,
        "timestamp": _now_iso(),
        "mode": "localization_action_log",
        "command_id": command_id,
        "params": params,
        "localization_source": source,
        "gyro_duration_s": gyro_duration_s if use_gyro else 0.0,
        "gyro_deadband_dps": gyro_deadband_dps,
        "estimated_delta": estimated_delta,
        "pose": pose,
        "confidence": state.get("confidence"),
        "summary": _summary_text(pose=pose, source=source),
        "localization_state": state,
        "robot_action_executed": False,
    }
    _append_action_log(_resolve_action_log_path(action_log_path), payload)
    _write_json_if_requested(payload, output_path)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        payload = logger_status()
    elif args.command == "log-action":
        payload = log_action(
            command_id=args.command_id,
            params=_params_from_args(args),
            gyro_duration_s=args.gyro_duration,
            gyro_deadband_dps=args.gyro_deadband_dps,
            state_path=args.state,
            history_path=args.history,
            action_log_path=args.action_log,
            output_path=args.output,
        )
    else:
        parser.error("missing command")

    _print_payload(payload, output_format=args.format)
    return 0 if payload.get("ok") is True else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.localization_action_logger",
        description="Update localization from high-level action records without moving the robot.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Print logger status.")
    _add_format_arg(status_parser)

    log_parser = subparsers.add_parser("log-action", help="Record one action into localization state.")
    log_parser.add_argument("--command-id", required=True)
    log_parser.add_argument("--params-json", help="Optional JSON object with command params.")
    log_parser.add_argument("--direction", type=float, help="Turn direction: 1 for left, -1 for right.")
    log_parser.add_argument("--degrees", type=float, help="Turn degrees for turn_degrees.")
    log_parser.add_argument("--distance-m", type=float, help="Forward distance override in meters.")
    log_parser.add_argument("--strafe-m", type=float, help="Left/right distance override in meters.")
    log_parser.add_argument("--gyro-duration", type=float, default=0.0)
    log_parser.add_argument("--gyro-deadband-dps", type=float, default=DEFAULT_GYRO_DEADBAND_DPS)
    log_parser.add_argument("--state")
    log_parser.add_argument("--history")
    log_parser.add_argument("--action-log")
    log_parser.add_argument("--output")
    _add_format_arg(log_parser)
    return parser


def _add_format_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        choices=("json", "brief"),
        default="json",
        help="Output format. Use brief for a short human-readable line.",
    )


def _params_from_args(args: argparse.Namespace) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if args.params_json:
        try:
            loaded = json.loads(args.params_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--params-json is not valid JSON: {exc}") from exc
        if not isinstance(loaded, dict):
            raise SystemExit("--params-json must be a JSON object")
        params.update(loaded)
    if args.direction is not None:
        params["direction"] = args.direction
    if args.degrees is not None:
        params["degrees"] = args.degrees
    if args.distance_m is not None:
        params["distance_m"] = args.distance_m
    if args.strafe_m is not None:
        params["strafe_m"] = args.strafe_m
    return params


def _summary_text(*, pose: dict[str, Any], source: str) -> str:
    x_m = _optional_float(pose.get("x_m")) or 0.0
    y_m = _optional_float(pose.get("y_m")) or 0.0
    yaw_deg = _optional_float(pose.get("yaw_deg")) or 0.0
    label = "gyro_z\u79ef\u5206" if source == "gyro_z_integrated" else "\u52a8\u4f5c\u4f30\u7b97"
    return f"\u5b9a\u4f4d\u5df2\u66f4\u65b0: x={x_m:.2f}m, y={y_m:.2f}m, yaw={yaw_deg:.1f}deg, \u6765\u6e90={label}"


def _print_payload(payload: dict[str, Any], *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if payload.get("ok") is True:
        print(str(payload.get("summary") or payload.get("message") or "OK"))
        return
    print(f"\u5b9a\u4f4d\u8bb0\u5f55\u5931\u8d25: {payload.get('error') or 'unknown'} - {payload.get('message') or ''}")


def _append_action_log(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slim_payload = {
        "timestamp": payload.get("timestamp"),
        "command_id": payload.get("command_id"),
        "params": payload.get("params"),
        "localization_source": payload.get("localization_source"),
        "estimated_delta": payload.get("estimated_delta"),
        "pose": payload.get("pose"),
        "confidence": payload.get("confidence"),
        "summary": payload.get("summary"),
        "robot_action_executed": False,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(slim_payload, ensure_ascii=False) + "\n")


def _write_json_if_requested(payload: dict[str, Any], output_path: str | Path | None) -> None:
    if output_path is None:
        return
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_action_log_path(path: str | Path | None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    return DEFAULT_ACTION_LOG


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _error(error: str, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "source": SOURCE,
        "tool_version": TOOL_VERSION,
        "timestamp": _now_iso(),
        "error": error,
        "message": message,
        "robot_action_executed": False,
        **extra,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
