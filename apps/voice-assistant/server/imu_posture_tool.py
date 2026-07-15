from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOL_VERSION = "0.1"
SOURCE = "imu-posture"
DEFAULT_RUNTIME_PYTHON = Path("/home/duck/.venv/bin/python")
DEFAULT_SAMPLES = 5
DEFAULT_SAMPLE_INTERVAL_S = 0.05
DEFAULT_MAX_ABS_PITCH_DEG = 20.0
DEFAULT_MAX_ABS_ROLL_DEG = 20.0
DEFAULT_MAX_ABS_YAW_RATE_DPS = 45.0
DEFAULT_MIN_ABS_GRAVITY_MPS2 = 7.0
DEFAULT_MAX_ABS_GRAVITY_MPS2 = 12.5
DIRECT_ENV = "OPENDUCK_IMU_DIRECT"


def imu_status() -> dict[str, Any]:
    return _base_payload(
        {
            "tool_version": TOOL_VERSION,
            "message": "Read-only BNO055 posture check. It does not control the robot.",
        }
    )


def read_posture(
    *,
    samples: int = DEFAULT_SAMPLES,
    sample_interval_s: float = DEFAULT_SAMPLE_INTERVAL_S,
    max_abs_pitch_deg: float = DEFAULT_MAX_ABS_PITCH_DEG,
    max_abs_roll_deg: float = DEFAULT_MAX_ABS_ROLL_DEG,
    max_abs_yaw_rate_dps: float = DEFAULT_MAX_ABS_YAW_RATE_DPS,
    min_abs_gravity_mps2: float = DEFAULT_MIN_ABS_GRAVITY_MPS2,
    max_abs_gravity_mps2: float = DEFAULT_MAX_ABS_GRAVITY_MPS2,
    runtime_python: str | Path = DEFAULT_RUNTIME_PYTHON,
    direct: bool = False,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    limits = {
        "max_abs_pitch_deg": float(max_abs_pitch_deg),
        "max_abs_roll_deg": float(max_abs_roll_deg),
        "max_abs_yaw_rate_dps": float(max_abs_yaw_rate_dps),
        "min_abs_gravity_mps2": float(min_abs_gravity_mps2),
        "max_abs_gravity_mps2": float(max_abs_gravity_mps2),
    }

    if not direct and os.environ.get(DIRECT_ENV) != "1":
        external = _read_with_runtime_python(
            samples=samples,
            sample_interval_s=sample_interval_s,
            limits=limits,
            runtime_python=runtime_python,
        )
        if external is not None:
            _write_json_if_requested(external, output_path)
            return external

    payload = _read_direct(
        samples=samples,
        sample_interval_s=sample_interval_s,
        limits=limits,
    )
    _write_json_if_requested(payload, output_path)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        payload = imu_status()
    elif args.command == "read":
        payload = read_posture(
            samples=args.samples,
            sample_interval_s=args.sample_interval,
            max_abs_pitch_deg=args.max_abs_pitch_deg,
            max_abs_roll_deg=args.max_abs_roll_deg,
            max_abs_yaw_rate_dps=args.max_abs_yaw_rate_dps,
            runtime_python=args.runtime_python,
            direct=args.direct,
            output_path=args.output,
        )
    else:
        parser.error("missing command")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") is True else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.imu_posture_tool",
        description="Read BNO055 posture and print a simple safety JSON payload.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Print tool status without probing hardware.")

    read_parser = subparsers.add_parser("read", help="Read BNO055 posture once.")
    read_parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    read_parser.add_argument("--sample-interval", type=float, default=DEFAULT_SAMPLE_INTERVAL_S)
    read_parser.add_argument("--max-abs-pitch-deg", type=float, default=DEFAULT_MAX_ABS_PITCH_DEG)
    read_parser.add_argument("--max-abs-roll-deg", type=float, default=DEFAULT_MAX_ABS_ROLL_DEG)
    read_parser.add_argument("--max-abs-yaw-rate-dps", type=float, default=DEFAULT_MAX_ABS_YAW_RATE_DPS)
    read_parser.add_argument("--runtime-python", default=str(DEFAULT_RUNTIME_PYTHON))
    read_parser.add_argument("--direct", action="store_true", help="Read using the current Python process.")
    read_parser.add_argument("--output")
    return parser


def _read_with_runtime_python(
    *,
    samples: int,
    sample_interval_s: float,
    limits: dict[str, float],
    runtime_python: str | Path,
) -> dict[str, Any] | None:
    python_path = Path(runtime_python).expanduser()
    if not python_path.is_file() or str(python_path) == sys.executable:
        return None

    script_path = Path(__file__).resolve()
    command = [
        str(python_path),
        str(script_path),
        "read",
        "--direct",
        "--samples",
        str(samples),
        "--sample-interval",
        str(sample_interval_s),
        "--max-abs-pitch-deg",
        str(limits["max_abs_pitch_deg"]),
        "--max-abs-roll-deg",
        str(limits["max_abs_roll_deg"]),
        "--max-abs-yaw-rate-dps",
        str(limits["max_abs_yaw_rate_dps"]),
    ]
    env = os.environ.copy()
    env[DIRECT_ENV] = "1"
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=6,
            env=env,
        )
    except Exception:
        return None

    if not completed.stdout.strip():
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    payload["reader_python"] = str(python_path)
    return payload


def _read_direct(*, samples: int, sample_interval_s: float, limits: dict[str, float]) -> dict[str, Any]:
    try:
        import time

        import adafruit_bno055
        import board
        import busio
    except Exception as exc:
        return _error(
            "imu_dependency_missing",
            "BNO055 dependency is missing in this Python environment.",
            detail=str(exc),
            limits=limits,
        )

    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        sensor = adafruit_bno055.BNO055_I2C(i2c)
    except Exception as exc:
        return _error(
            "imu_unavailable",
            "BNO055 was not reachable on I2C.",
            detail=str(exc),
            limits=limits,
        )

    samples = max(1, min(20, int(samples)))
    sample_interval_s = max(0.0, min(1.0, float(sample_interval_s)))
    readings: list[dict[str, Any]] = []
    for index in range(samples):
        readings.append(_read_sensor_once(sensor))
        if index < samples - 1:
            time.sleep(sample_interval_s)

    usable = [item for item in readings if item.get("ok") is True]
    if not usable:
        return _error(
            "imu_no_valid_samples",
            "BNO055 returned no complete posture sample.",
            samples=readings,
            limits=limits,
        )

    posture = _summarize_readings(usable)
    warnings, deny_reasons = _evaluate_posture(posture, limits)
    safe = not deny_reasons
    status = "ok" if safe and not warnings else "warning"
    if deny_reasons:
        status = "critical"

    payload = _base_payload(
        {
            "mode": "bno055_posture_read",
            "timestamp": _now_iso(),
            "status": status,
            "safe_for_navigation": safe,
            "summary": _human_summary(posture, safe),
            "posture": posture,
            "limits": limits,
            "calibration": usable[-1].get("calibration"),
            "warnings": warnings,
            "deny_reasons": deny_reasons,
            "sample_count": len(usable),
            "reader_python": sys.executable,
        }
    )
    payload["ok"] = True
    return payload


def _read_sensor_once(sensor: Any) -> dict[str, Any]:
    try:
        euler = sensor.euler
        gyro = sensor.gyro
        accel = sensor.acceleration
        calibration = sensor.calibration_status
        calibrated = sensor.calibrated
    except Exception as exc:
        return {"ok": False, "error": "imu_read_failed", "message": str(exc)}

    yaw_deg = roll_deg = pitch_deg = None
    if _valid_vector(euler, 3):
        yaw_deg = _normalize_degrees(float(euler[0]))
        roll_deg = float(euler[1])
        pitch_deg = float(euler[2])

    accel_tuple = _tuple_or_none(accel, 3)
    if accel_tuple is not None:
        estimated = _estimate_tilt_from_accel(accel_tuple)
        pitch_deg = estimated["pitch_deg"]
        roll_deg = estimated["roll_deg"]

    gyro_tuple = _tuple_or_none(gyro, 3)
    yaw_rate_dps = None
    if gyro_tuple is not None:
        yaw_rate_dps = math.degrees(float(gyro_tuple[2]))

    return {
        "ok": pitch_deg is not None and roll_deg is not None,
        "yaw_deg": yaw_deg,
        "pitch_deg": pitch_deg,
        "roll_deg": roll_deg,
        "yaw_rate_dps": yaw_rate_dps,
        "accel_mps2": accel_tuple,
        "gyro_radps": gyro_tuple,
        "calibration": _calibration_payload(calibration, calibrated),
    }


def _summarize_readings(readings: list[dict[str, Any]]) -> dict[str, Any]:
    accel_values = [item.get("accel_mps2") for item in readings if _valid_vector(item.get("accel_mps2"), 3)]
    gyro_values = [item.get("gyro_radps") for item in readings if _valid_vector(item.get("gyro_radps"), 3)]
    return {
        "pitch_deg": _round(_median_number(item.get("pitch_deg") for item in readings)),
        "roll_deg": _round(_median_number(item.get("roll_deg") for item in readings)),
        "yaw_deg": _round(_median_number(item.get("yaw_deg") for item in readings)),
        "yaw_rate_dps": _round(_median_number(item.get("yaw_rate_dps") for item in readings)),
        "accel_norm_mps2": _round(_median_number(_vector_norm(item) for item in accel_values)),
        "accel_mps2": _round_vector(_median_vector(accel_values)),
        "gyro_radps": _round_vector(_median_vector(gyro_values)),
    }


def _evaluate_posture(posture: dict[str, Any], limits: dict[str, float]) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    deny_reasons: list[str] = []
    pitch = _optional_float(posture.get("pitch_deg"))
    roll = _optional_float(posture.get("roll_deg"))
    yaw_rate = _optional_float(posture.get("yaw_rate_dps"))
    accel_norm = _optional_float(posture.get("accel_norm_mps2"))

    if pitch is None:
        deny_reasons.append("pitch_unknown")
    elif abs(pitch) > limits["max_abs_pitch_deg"]:
        deny_reasons.append(f"pitch_too_large:{pitch:.1f}deg")

    if roll is None:
        deny_reasons.append("roll_unknown")
    elif abs(roll) > limits["max_abs_roll_deg"]:
        deny_reasons.append(f"roll_too_large:{roll:.1f}deg")

    if yaw_rate is None:
        warnings.append("yaw_rate_unknown")
    elif abs(yaw_rate) > limits["max_abs_yaw_rate_dps"]:
        deny_reasons.append(f"yaw_rate_too_large:{yaw_rate:.1f}deg/s")

    if accel_norm is None:
        warnings.append("gravity_unknown")
    elif accel_norm < limits["min_abs_gravity_mps2"] or accel_norm > limits["max_abs_gravity_mps2"]:
        warnings.append(f"gravity_unusual:{accel_norm:.2f}m/s2")

    return warnings, deny_reasons


def _human_summary(posture: dict[str, Any], safe: bool) -> str:
    state = "姿态正常" if safe else "姿态不安全"
    pitch = _format_number(posture.get("pitch_deg"), "deg")
    roll = _format_number(posture.get("roll_deg"), "deg")
    yaw_rate = _format_number(posture.get("yaw_rate_dps"), "deg/s")
    return f"{state}: 前后倾 {pitch}, 左右歪 {roll}, 转动速度 {yaw_rate}"


def _estimate_tilt_from_accel(accel: tuple[float, float, float]) -> dict[str, float]:
    ax, ay, az = accel
    pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az)))
    roll = math.degrees(math.atan2(ay, az))
    return {"pitch_deg": pitch, "roll_deg": roll}


def _calibration_payload(calibration: Any, calibrated: Any) -> dict[str, Any]:
    values = tuple(calibration) if _valid_vector(calibration, 4) else None
    return {
        "system": values[0] if values else None,
        "gyro": values[1] if values else None,
        "accel": values[2] if values else None,
        "mag": values[3] if values else None,
        "calibrated": bool(calibrated) if isinstance(calibrated, bool) else calibrated,
    }


def _valid_vector(value: Any, length: int) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == length and all(item is not None for item in value)


def _tuple_or_none(value: Any, length: int) -> tuple[float, ...] | None:
    if not _valid_vector(value, length):
        return None
    try:
        return tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None


def _median_vector(values: list[Any]) -> list[float] | None:
    vectors = [tuple(item) for item in values if _valid_vector(item, 3)]
    if not vectors:
        return None
    return [_median_number(vector[index] for vector in vectors) for index in range(3)]


def _median_number(values: Any) -> float | None:
    numbers = sorted(float(item) for item in values if item is not None and math.isfinite(float(item)))
    if not numbers:
        return None
    middle = len(numbers) // 2
    if len(numbers) % 2 == 1:
        return numbers[middle]
    return (numbers[middle - 1] + numbers[middle]) / 2


def _vector_norm(value: Any) -> float | None:
    if not _valid_vector(value, 3):
        return None
    return math.sqrt(sum(float(item) * float(item) for item in value))


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)


def _round_vector(value: list[float] | None) -> list[float] | None:
    if value is None:
        return None
    return [_round(item) for item in value]


def _normalize_degrees(value: float) -> float:
    return ((value + 180.0) % 360.0) - 180.0


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _format_number(value: Any, unit: str) -> str:
    number = _optional_float(value)
    return "未知" if number is None else f"{number:.1f}{unit}"


def _write_json_if_requested(payload: dict[str, Any], output_path: str | Path | None) -> None:
    if output_path is None:
        return
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _error(error: str, message: str, **extra: Any) -> dict[str, Any]:
    return _base_payload(
        {
            "ok": False,
            "mode": "bno055_posture_read",
            "timestamp": _now_iso(),
            "status": "critical",
            "safe_for_navigation": False,
            "error": error,
            "message": message,
            "warnings": [],
            "deny_reasons": [error],
            **extra,
        }
    )


def _base_payload(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "source": SOURCE,
        "robot_action_executed": False,
    }
    if extra:
        payload.update(extra)
    payload["robot_action_executed"] = False
    return payload


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
