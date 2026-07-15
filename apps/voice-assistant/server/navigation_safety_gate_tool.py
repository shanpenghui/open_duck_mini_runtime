from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from server.imu_posture_tool import read_posture


TOOL_VERSION = "0.1"
SOURCE = "navigation-safety-gate"
AUTHORIZATION_PATH = Path("/tmp/openduck_manual_authorization.json")
EMERGENCY_STOP_PATH = Path("/tmp/openduck_emergency_stop.json")
DEFAULT_AUTHORIZATION_TTL_SEC = 60
EMERGENCY_STOP_DISTANCE_M = 0.45
LOW_SPEED_INTENTS = {"go_slow_forward", "turn_left", "turn_right"}
DEFAULT_IMU_POSTURE_PATH = Path("/tmp/openduck_imu_posture.json")


def safety_gate_status() -> dict[str, Any]:
    return _base_payload(
        {
            "tool_version": TOOL_VERSION,
            "message": "Dry-run safety gate. It does not control the robot.",
        }
    )


def authorize(reason: str, ttl_sec: int = DEFAULT_AUTHORIZATION_TTL_SEC) -> dict[str, Any]:
    ttl_sec = max(1, int(ttl_sec))
    now = _now()
    payload = _base_payload(
        {
            "mode": "manual_authorization",
            "authorized": True,
            "ttl_sec": ttl_sec,
            "created_at": _iso(now),
            "expires_at": _iso(now + timedelta(seconds=ttl_sec)),
            "reason": reason,
        }
    )
    _write_json(AUTHORIZATION_PATH, payload)
    return payload


def authorization_status() -> dict[str, Any]:
    status = _read_authorization_status()
    return _base_payload(
        {
            "mode": "manual_authorization_status",
            "authorized": status["authorized"],
            "reason": status["reason"],
            "authorization_path": str(AUTHORIZATION_PATH),
            "expires_at": status.get("expires_at"),
        }
    )


def revoke_authorization(reason: str) -> dict[str, Any]:
    payload = _base_payload(
        {
            "mode": "manual_authorization",
            "authorized": False,
            "revoked_at": _now_iso(),
            "reason": reason,
        }
    )
    _write_json(AUTHORIZATION_PATH, payload)
    return payload


def emergency_stop(reason: str) -> dict[str, Any]:
    payload = _base_payload(
        {
            "mode": "emergency_stop",
            "emergency_stop": True,
            "timestamp": _now_iso(),
            "reason": reason,
        }
    )
    _write_json(EMERGENCY_STOP_PATH, payload)
    return payload


def clear_emergency_stop(reason: str) -> dict[str, Any]:
    payload = _base_payload(
        {
            "mode": "emergency_stop",
            "emergency_stop": False,
            "timestamp": _now_iso(),
            "reason": reason,
        }
    )
    _write_json(EMERGENCY_STOP_PATH, payload)
    return payload


def evaluate(
    health_path: str | Path,
    decision_path: str | Path,
    output_path: str | Path | None = None,
    imu_path: str | Path | None = None,
    require_imu: bool = True,
) -> dict[str, Any]:
    warnings: list[str] = []
    deny_reasons: list[str] = []

    health_result = _load_json_file(health_path, missing_reason="health_missing", invalid_reason="health_invalid")
    health = health_result.get("json") if health_result["ok"] else None
    health_gate = _evaluate_health(health, health_result, warnings, deny_reasons)

    emergency_gate = _evaluate_emergency_stop(deny_reasons)
    authorization_gate = _evaluate_authorization(deny_reasons)
    imu_gate = _evaluate_imu_posture(imu_path, require_imu, warnings, deny_reasons)

    decision_result = _load_json_file(
        decision_path,
        missing_reason="decision_missing",
        invalid_reason="decision_invalid",
    )
    decision = decision_result.get("json") if decision_result["ok"] else None
    decision_gate = _evaluate_decision(decision, decision_result, warnings, deny_reasons)
    warnings = _unique(warnings)
    deny_reasons = _unique(deny_reasons)

    dry_run_allowed = (
        not deny_reasons
        and decision_gate["intent"] in LOW_SPEED_INTENTS
        and decision_gate["front_distance_safe"] is True
        and decision_gate["depth_valid"] is True
        and decision_gate["decision_dry_run_marked"] is True
        and (not require_imu or imu_gate["imu_posture_ok"] is True)
    )
    would_allow = dry_run_allowed

    payload = _base_payload(
        {
            "mode": "safety_gate_dry_run",
            "timestamp": _now_iso(),
            "dry_run_allowed": dry_run_allowed,
            "would_allow_low_speed_execution": would_allow,
            "decision": {
                "intent": decision_gate["intent"],
                "reason": decision_gate["reason"],
            },
            "gate": {
                "health_ok": health_gate["health_ok"],
                "power_stable": health_gate["power_stable"],
                "emergency_stop_active": emergency_gate["emergency_stop_active"],
                "manual_authorization_valid": authorization_gate["manual_authorization_valid"],
                "imu_posture_ok": imu_gate["imu_posture_ok"],
                "decision_dry_run_marked": decision_gate["decision_dry_run_marked"],
                "front_distance_safe": decision_gate["front_distance_safe"],
                "depth_valid": decision_gate["depth_valid"],
            },
            "imu_posture": imu_gate["imu_posture"],
            "warnings": warnings,
            "deny_reasons": deny_reasons,
            "health_path": str(Path(health_path).expanduser()),
            "decision_path": str(Path(decision_path).expanduser()),
            "imu_path": imu_gate["imu_path"],
        }
    )

    if output_path is not None:
        output = Path(output_path).expanduser()
        if not _is_tmp_path(output):
            payload["ok"] = False
            payload["dry_run_allowed"] = False
            payload["would_allow_low_speed_execution"] = False
            payload["deny_reasons"] = deny_reasons + ["output_not_in_tmp"]
            payload["error"] = "output_not_in_tmp"
            payload["message"] = "Safety gate output must be written under /tmp/."
        else:
            _write_json(output, payload)
            payload["output_path"] = str(output)

    return payload


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        payload = _run_command(args)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    except Exception as exc:
        payload = _base_payload({"ok": False, "error": "unexpected_error", "message": str(exc)})

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") is True else 1


def _run_command(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "status":
        return safety_gate_status()
    if args.command == "authorize":
        return authorize(args.reason, ttl_sec=args.ttl_sec)
    if args.command == "authorization-status":
        return authorization_status()
    if args.command == "revoke-authorization":
        return revoke_authorization(args.reason)
    if args.command == "emergency-stop":
        return emergency_stop(args.reason)
    if args.command == "clear-emergency-stop":
        return clear_emergency_stop(args.reason)
    if args.command == "evaluate":
        return evaluate(
            args.health,
            args.decision,
            output_path=args.output,
            imu_path=args.imu,
            require_imu=not args.skip_imu,
        )
    raise ValueError("missing command")


def _build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        prog="python -m server.navigation_safety_gate_tool",
        description="Dry-run safety gate for OpenDuck navigation decisions.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Print tool status without probing hardware.")

    authorize_parser = subparsers.add_parser("authorize", help="Create short-lived manual authorization.")
    authorize_parser.add_argument("--reason", required=True)
    authorize_parser.add_argument("--ttl-sec", type=int, default=DEFAULT_AUTHORIZATION_TTL_SEC)

    subparsers.add_parser("authorization-status", help="Check whether manual authorization is still valid.")

    revoke_parser = subparsers.add_parser("revoke-authorization", help="Revoke manual authorization.")
    revoke_parser.add_argument("--reason", required=True)

    stop_parser = subparsers.add_parser("emergency-stop", help="Set dry-run emergency stop state.")
    stop_parser.add_argument("--reason", required=True)

    clear_parser = subparsers.add_parser("clear-emergency-stop", help="Clear dry-run emergency stop state.")
    clear_parser.add_argument("--reason", required=True)

    evaluate_parser = subparsers.add_parser("evaluate", help="Evaluate health, authorization, stop state, and decision.")
    evaluate_parser.add_argument("--health", required=True)
    evaluate_parser.add_argument("--decision", required=True)
    evaluate_parser.add_argument("--imu")
    evaluate_parser.add_argument("--skip-imu", action="store_true")
    evaluate_parser.add_argument("--output")
    return parser


def _evaluate_health(
    health: dict[str, Any] | None,
    health_result: dict[str, Any],
    warnings: list[str],
    deny_reasons: list[str],
) -> dict[str, bool]:
    if health is None:
        deny_reasons.append(health_result["reason"])
        return {"health_ok": False, "power_stable": False}

    overall_status = str(health.get("overall_status") or "warning").lower()
    if overall_status == "critical":
        deny_reasons.append("health_critical")
    elif overall_status == "warning":
        warnings.extend(_health_warnings(health))
    elif overall_status != "ok":
        warnings.append(f"health overall_status is {overall_status}")

    checks = health.get("checks") if isinstance(health.get("checks"), dict) else {}
    power = checks.get("power") if isinstance(checks.get("power"), dict) else {}
    power_status = str(power.get("status") or "warning").lower()
    power_stable = power_status == "ok"
    if not power_stable:
        deny_reasons.append("power_not_stable")
        message = str(power.get("message") or "power status is not ok")
        warnings.append(f"power: {message}")

    return {"health_ok": overall_status != "critical", "power_stable": power_stable}


def _evaluate_emergency_stop(deny_reasons: list[str]) -> dict[str, bool]:
    payload = _safe_read_json(EMERGENCY_STOP_PATH)
    active = isinstance(payload, dict) and payload.get("emergency_stop") is True
    if active:
        deny_reasons.append("emergency_stop_active")
    return {"emergency_stop_active": active}


def _evaluate_authorization(deny_reasons: list[str]) -> dict[str, bool]:
    status = _read_authorization_status()
    valid = status["authorized"] is True
    if not valid:
        deny_reasons.append("manual_authorization_required")
    return {"manual_authorization_valid": valid}


def _evaluate_imu_posture(
    imu_path: str | Path | None,
    require_imu: bool,
    warnings: list[str],
    deny_reasons: list[str],
) -> dict[str, Any]:
    if not require_imu:
        return {"imu_posture_ok": True, "imu_posture": None, "imu_path": None}

    resolved_path = Path(imu_path).expanduser() if imu_path is not None else DEFAULT_IMU_POSTURE_PATH
    if imu_path is None:
        posture = read_posture(output_path=resolved_path)
    else:
        result = _load_json_file(resolved_path, missing_reason="imu_missing", invalid_reason="imu_invalid")
        posture = result.get("json") if result["ok"] else None
        if posture is None:
            deny_reasons.append(result["reason"])
            return {"imu_posture_ok": False, "imu_posture": None, "imu_path": str(resolved_path)}

    if posture.get("ok") is not True:
        deny_reasons.append(str(posture.get("error") or "imu_not_ok"))
    if posture.get("safe_for_navigation") is not True:
        deny_reasons.extend(str(item) for item in posture.get("deny_reasons", []) if isinstance(item, str))
    warnings.extend(str(item) for item in posture.get("warnings", []) if isinstance(item, str))

    return {
        "imu_posture_ok": posture.get("ok") is True and posture.get("safe_for_navigation") is True,
        "imu_posture": {
            "status": posture.get("status"),
            "safe_for_navigation": posture.get("safe_for_navigation"),
            "summary": posture.get("summary"),
            "posture": posture.get("posture"),
            "calibration": posture.get("calibration"),
        },
        "imu_path": str(resolved_path),
    }


def _evaluate_decision(
    decision: dict[str, Any] | None,
    decision_result: dict[str, Any],
    warnings: list[str],
    deny_reasons: list[str],
) -> dict[str, Any]:
    if decision is None:
        deny_reasons.append(decision_result["reason"])
        return {
            "intent": None,
            "reason": decision_result["reason"],
            "decision_dry_run_marked": False,
            "front_distance_safe": False,
            "depth_valid": False,
        }

    decision_block = decision.get("decision") if isinstance(decision.get("decision"), dict) else decision
    inputs = decision.get("inputs") if isinstance(decision.get("inputs"), dict) else {}
    motion_profile = decision_block.get("motion_profile") if isinstance(decision_block.get("motion_profile"), dict) else {}

    intent = str(decision_block.get("intent") or decision.get("intent") or "unknown")
    reason = str(decision_block.get("reason") or decision.get("reason") or "unknown")
    front = _optional_float(inputs.get("front_min_distance_m", decision.get("front_min_distance_m")))
    depth_valid = _optional_bool(inputs.get("depth_valid", decision.get("depth_valid")))
    execution_disabled = decision_block.get("execution_enabled") is False
    manual_enable_required = decision_block.get("requires_manual_enable") is True
    not_for_direct_execution = (
        decision_block.get("not_for_direct_execution") is True
        or motion_profile.get("not_for_direct_execution") is True
        or decision.get("not_for_direct_execution") is True
    )
    decision_dry_run_marked = execution_disabled and manual_enable_required and not_for_direct_execution

    if not execution_disabled:
        warnings.append("decision execution_enabled was not false; safety gate keeps execution disabled")
    if not manual_enable_required:
        warnings.append("decision did not explicitly require manual enable")
    if not not_for_direct_execution:
        warnings.append("decision did not explicitly mark not_for_direct_execution")

    front_distance_safe = front is not None and front > EMERGENCY_STOP_DISTANCE_M
    if intent == "hold_position":
        deny_reasons.append("decision_hold_position")
    if front is None:
        deny_reasons.append("front_distance_unknown")
    elif front <= EMERGENCY_STOP_DISTANCE_M:
        deny_reasons.append("front_inside_emergency_stop_distance")
    if depth_valid is not True:
        deny_reasons.append("depth_not_valid")
    if not decision_dry_run_marked:
        deny_reasons.append("decision_not_marked_dry_run")
    if intent not in LOW_SPEED_INTENTS and intent != "hold_position":
        deny_reasons.append("decision_intent_not_low_speed")

    return {
        "intent": intent,
        "reason": reason,
        "decision_dry_run_marked": decision_dry_run_marked,
        "front_distance_safe": front_distance_safe,
        "depth_valid": depth_valid is True,
    }


def _read_authorization_status() -> dict[str, Any]:
    payload = _safe_read_json(AUTHORIZATION_PATH)
    if not isinstance(payload, dict):
        return {"authorized": False, "reason": "authorization_missing"}
    if payload.get("authorized") is not True:
        return {
            "authorized": False,
            "reason": str(payload.get("reason") or "authorization_revoked"),
            "expires_at": payload.get("expires_at"),
        }
    expires_at = _parse_datetime(payload.get("expires_at"))
    if expires_at is None:
        return {"authorized": False, "reason": "authorization_invalid", "expires_at": payload.get("expires_at")}
    if expires_at <= _now():
        return {"authorized": False, "reason": "authorization_expired", "expires_at": payload.get("expires_at")}
    return {
        "authorized": True,
        "reason": str(payload.get("reason") or "manual_authorization_valid"),
        "expires_at": payload.get("expires_at"),
    }


def _health_warnings(health: dict[str, Any]) -> list[str]:
    checks = health.get("checks") if isinstance(health.get("checks"), dict) else {}
    warnings: list[str] = []
    for name, check in checks.items():
        if not isinstance(check, dict):
            continue
        status = str(check.get("status") or "").lower()
        if status == "warning":
            message = str(check.get("message") or "warning")
            warnings.append(f"{name}: {message}")
    if not warnings:
        summary = health.get("summary")
        if isinstance(summary, list):
            warnings.extend(str(item) for item in summary)
    return warnings


def _load_json_file(path: str | Path, missing_reason: str, invalid_reason: str) -> dict[str, Any]:
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        return {"ok": False, "reason": missing_reason}
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {"ok": False, "reason": invalid_reason, "message": str(exc)}
    if not isinstance(payload, dict):
        return {"ok": False, "reason": invalid_reason}
    return {"ok": True, "json": payload}


def _safe_read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_tmp_path(path: Path) -> bool:
    normalized = path.as_posix()
    return normalized == "/tmp" or normalized.startswith("/tmp/")


def _base_payload(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "source": SOURCE,
        "execution_enabled": False,
        "robot_action_executed": False,
    }
    if extra:
        payload.update(extra)
    payload["execution_enabled"] = False
    payload["robot_action_executed"] = False
    return payload


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _now_iso() -> str:
    return _iso(_now())


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        payload = _base_payload({"ok": False, "error": "argument_error", "message": message})
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
