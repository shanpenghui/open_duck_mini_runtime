from __future__ import annotations

import argparse
import importlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOL_VERSION = "0.1"
SOURCE = "pi-health-check"
COMMAND_TIMEOUT_SECONDS = 8


def health_status() -> dict[str, Any]:
    return {
        "ok": True,
        "source": SOURCE,
        "tool_version": TOOL_VERSION,
        "message": "Read-only Raspberry Pi health check tool. It does not control the robot.",
        "robot_action_executed": False,
    }


def run_health_check(output_path: str | Path | None = None) -> dict[str, Any]:
    checks = {
        "power": check_power(),
        "temperature": check_temperature(),
        "disk": check_disk(),
        "memory": check_memory(),
        "usb": check_usb(),
        "recent_logs": check_recent_logs(),
        "python_dependencies": check_python_dependencies(),
        "openduck_tools": check_openduck_tools(),
    }
    overall_status = _overall_status(checks)
    payload: dict[str, Any] = {
        "ok": overall_status != "critical",
        "source": SOURCE,
        "tool_version": TOOL_VERSION,
        "timestamp": _now_iso(),
        "overall_status": overall_status,
        "summary": _summary(checks),
        "checks": checks,
        "robot_action_executed": False,
    }
    _write_json_if_requested(payload, output_path)
    return payload


def check_power() -> dict[str, Any]:
    result = _run_command(["vcgencmd", "get_throttled"])
    raw = result["raw"].strip()
    payload = _base_check(result, raw_key="raw")
    if result["ok"] is not True:
        payload.update({"status": "warning", "message": "vcgencmd get_throttled is not available."})
        return payload

    match = re.search(r"throttled=(0x[0-9a-fA-F]+|\d+)", raw)
    throttled = match.group(1) if match else None
    payload["throttled"] = throttled
    if throttled == "0x0" or throttled == "0":
        payload.update({"status": "ok", "message": "power ok"})
    elif throttled is None:
        payload.update({"status": "warning", "message": "Could not parse throttled value."})
    else:
        payload.update(
            {
                "status": "warning",
                "message": "Undervoltage or throttling risk was reported now or in the past.",
            }
        )
    return payload


def check_temperature() -> dict[str, Any]:
    result = _run_command(["vcgencmd", "measure_temp"])
    payload = _base_check(result, raw_key="raw")
    if result["ok"] is not True:
        payload.update({"status": "warning", "message": "vcgencmd measure_temp is not available."})
        return payload

    celsius = _parse_first_float(result["raw"])
    payload["celsius"] = celsius
    if celsius is None:
        payload.update({"status": "warning", "message": "Could not parse temperature."})
    elif celsius >= 80:
        payload.update({"status": "critical", "message": "temperature is at or above 80C"})
    elif celsius >= 70:
        payload.update({"status": "warning", "message": "temperature is between 70C and 80C"})
    else:
        payload.update({"status": "ok", "message": "temperature ok"})
    return payload


def check_disk() -> dict[str, Any]:
    result = _run_command(["df", "-h", "/"])
    payload = _base_check(result, raw_key="raw")
    if result["ok"] is not True:
        payload.update({"status": "warning", "message": "df -h / is not available."})
        return payload

    usage = _parse_disk_usage_percent(result["raw"])
    payload["root_usage_percent"] = usage
    if usage is None:
        payload.update({"status": "warning", "message": "Could not parse root disk usage."})
    elif usage >= 95:
        payload.update({"status": "critical", "message": "root disk usage is at or above 95%"})
    elif usage >= 85:
        payload.update({"status": "warning", "message": "root disk usage is between 85% and 95%"})
    else:
        payload.update({"status": "ok", "message": "disk ok"})
    return payload


def check_memory() -> dict[str, Any]:
    result = _run_command(["free", "-h"])
    payload = _base_check(result, raw_key="raw")
    if result["ok"] is not True:
        payload.update({"status": "warning", "message": "free -h is not available."})
        return payload

    payload["available"] = _parse_available_memory(result["raw"])
    payload.update({"status": "ok", "message": "memory command returned data"})
    return payload


def check_usb() -> dict[str, Any]:
    lsusb = _run_command(["lsusb"])
    v4l2 = _run_command(["v4l2-ctl", "--list-devices"])
    combined = f"{lsusb['raw']}\n{v4l2['raw']}"
    d435_detected = _contains_any(combined, ("RealSense", "Intel"))
    video0_detected = "/dev/video0" in combined
    video4_detected = "/dev/video4" in combined
    commands_available = lsusb["error"] != "command_not_found" or v4l2["error"] != "command_not_found"

    if not commands_available:
        status = "warning"
        message = "USB and V4L2 commands are not available on this system."
    elif d435_detected and video0_detected and video4_detected:
        status = "ok"
        message = "D435 detected"
    elif d435_detected:
        status = "warning"
        message = "D435 is visible, but expected /dev/video0 or /dev/video4 was not confirmed."
    else:
        status = "critical"
        message = "D435 was not detected."

    return {
        "status": status,
        "message": message,
        "d435_detected": d435_detected,
        "video0_detected": video0_detected,
        "video4_detected": video4_detected,
        "raw_lsusb": lsusb["raw"],
        "raw_v4l2": v4l2["raw"],
        "lsusb_error": lsusb["error"],
        "v4l2_error": v4l2["error"],
    }


def check_recent_logs() -> dict[str, Any]:
    if shutil.which("sh") is None:
        return {
            "status": "warning",
            "message": "dmesg log check is not available because sh was not found.",
            "matches": [],
            "raw": "",
            "error": "command_not_found",
        }

    command = [
        "sh",
        "-c",
        'dmesg -T | grep -i -E "voltage|under|thrott|power|usb|reset" | tail -40',
    ]
    result = _run_command(command)
    raw = result["raw"].strip()
    matches = [line for line in raw.splitlines() if _is_risky_log_line(line)]

    if result["error"] in (None, "non_zero_exit") and not raw:
        status = "ok"
        message = "no recent voltage, power, USB, or reset log matches"
    elif result["ok"] is not True and result["returncode"] not in (0, 1):
        status = "warning"
        message = "dmesg log check could not be completed."
    elif matches:
        status = "warning"
        message = "recent power or USB risk logs were found"
    else:
        status = "ok"
        message = "recent logs returned no known risk keywords"

    return {
        "status": status,
        "message": message,
        "matches": matches,
        "raw": raw,
        "error": result["error"],
        "returncode": result["returncode"],
    }


def check_python_dependencies() -> dict[str, Any]:
    modules = {}
    errors = {}
    for name in ("cv2", "numpy", "onnxruntime"):
        ok, error = _can_import(name)
        modules[name] = ok
        errors[name] = error

    missing = [name for name, ok in modules.items() if not ok]
    status = "ok" if not missing else "warning"
    message = "python dependencies ok" if not missing else f"missing python modules: {', '.join(missing)}"
    return {
        "status": status,
        "message": message,
        "modules": modules,
        "errors": errors,
    }


def check_openduck_tools() -> dict[str, Any]:
    specs = (
        ("realsense_camera_tool", "server.realsense_camera_tool", "realsense_status"),
        ("realsense_depth_tool", "server.realsense_depth_tool", "depth_status"),
        ("onnx_vision_tool", "server.onnx_vision_tool", "onnx_status"),
        ("navigation_perception_tool", "server.navigation_perception_tool", "perception_status"),
        ("navigation_decision_tool", "server.navigation_decision_tool", "decision_status"),
    )
    tools: dict[str, bool] = {}
    errors: dict[str, str | None] = {}
    results: dict[str, Any] = {}

    for label, module_name, function_name in specs:
        try:
            module = importlib.import_module(module_name)
            function = getattr(module, function_name)
            result = function()
            tools[label] = isinstance(result, dict)
            errors[label] = None
            results[label] = result
        except Exception as exc:
            tools[label] = False
            errors[label] = str(exc)
            results[label] = None

    missing = [name for name, ok in tools.items() if not ok]
    status = "ok" if not missing else "warning"
    message = "OpenDuck tools importable" if not missing else f"OpenDuck tools need attention: {', '.join(missing)}"
    return {
        "status": status,
        "message": message,
        "tools": tools,
        "errors": errors,
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        payload = health_status()
    elif args.command == "check":
        payload = run_health_check(output_path=args.output)
    else:
        parser.error("missing command")

    if args.format == "text":
        print(_format_text(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.pi_health_check_tool",
        description="Read-only Raspberry Pi, D435, Python, and OpenDuck health check tool.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    status_parser = subparsers.add_parser("status", help="Print this health check tool status without probing hardware.")
    status_parser.add_argument("--format", choices=("json", "text"), default="json", help="Output format.")
    check_parser = subparsers.add_parser("check", help="Run read-only health checks.")
    check_parser.add_argument("--output", help="Optional output JSON path.")
    check_parser.add_argument("--format", choices=("json", "text"), default="json", help="Output format.")
    return parser


def _format_text(payload: dict[str, Any]) -> str:
    if "overall_status" not in payload:
        return _format_status_text(payload)
    return _format_check_text(payload)


def _format_status_text(payload: dict[str, Any]) -> str:
    lines = [
        "OpenDuck Raspberry Pi Health Check",
        "===================================",
        f"Tool: {payload.get('source')} v{payload.get('tool_version')}",
        f"OK: {_yes_no(payload.get('ok') is True)}",
        "Mode: read-only",
        f"Robot action executed: {_yes_no(payload.get('robot_action_executed') is True)}",
        f"Message: {payload.get('message')}",
    ]
    return "\n".join(lines)


def _format_check_text(payload: dict[str, Any]) -> str:
    checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
    lines = [
        "OpenDuck Raspberry Pi Health Check",
        "===================================",
        f"Time: {payload.get('timestamp')}",
        f"Overall: {_status_label(str(payload.get('overall_status', 'warning')))}",
        f"OK field: {_yes_no(payload.get('ok') is True)}",
        "",
        "Checks",
        "------",
        _format_power_line(checks.get("power", {})),
        _format_temperature_line(checks.get("temperature", {})),
        _format_disk_line(checks.get("disk", {})),
        _format_memory_line(checks.get("memory", {})),
        _format_usb_line(checks.get("usb", {})),
        _format_recent_logs_line(checks.get("recent_logs", {})),
        _format_python_dependencies_line(checks.get("python_dependencies", {})),
        _format_openduck_tools_line(checks.get("openduck_tools", {})),
        "",
        f"Robot action executed: {_yes_no(payload.get('robot_action_executed') is True)}",
    ]
    if payload.get("output_path"):
        lines.append(f"JSON saved to: {payload.get('output_path')}")
    return "\n".join(lines)


def _format_power_line(check: Any) -> str:
    check = check if isinstance(check, dict) else {}
    detail = check.get("throttled") or check.get("error") or "unknown"
    return _check_line("Power", check, detail)


def _format_temperature_line(check: Any) -> str:
    check = check if isinstance(check, dict) else {}
    celsius = check.get("celsius")
    detail = f"{celsius} C" if celsius is not None else check.get("error") or "unknown"
    return _check_line("Temperature", check, detail)


def _format_disk_line(check: Any) -> str:
    check = check if isinstance(check, dict) else {}
    usage = check.get("root_usage_percent")
    detail = f"{usage}% used on /" if usage is not None else check.get("error") or "unknown"
    return _check_line("Disk", check, detail)


def _format_memory_line(check: Any) -> str:
    check = check if isinstance(check, dict) else {}
    detail = f"available {check.get('available')}" if check.get("available") else check.get("error") or "unknown"
    return _check_line("Memory", check, detail)


def _format_usb_line(check: Any) -> str:
    check = check if isinstance(check, dict) else {}
    detail = (
        f"D435={_yes_no(check.get('d435_detected') is True)}, "
        f"video0={_yes_no(check.get('video0_detected') is True)}, "
        f"video4={_yes_no(check.get('video4_detected') is True)}"
    )
    return _check_line("USB/D435", check, detail)


def _format_recent_logs_line(check: Any) -> str:
    check = check if isinstance(check, dict) else {}
    matches = check.get("matches") if isinstance(check.get("matches"), list) else []
    detail = f"{len(matches)} risk match(es)"
    if matches:
        detail = f"{detail}; latest: {matches[-1]}"
    elif check.get("error"):
        detail = str(check.get("error"))
    return _check_line("Recent logs", check, detail)


def _format_python_dependencies_line(check: Any) -> str:
    check = check if isinstance(check, dict) else {}
    modules = check.get("modules") if isinstance(check.get("modules"), dict) else {}
    detail = ", ".join(f"{name}={_yes_no(ok is True)}" for name, ok in modules.items()) or "unknown"
    return _check_line("Python deps", check, detail)


def _format_openduck_tools_line(check: Any) -> str:
    check = check if isinstance(check, dict) else {}
    tools = check.get("tools") if isinstance(check.get("tools"), dict) else {}
    ok_count = sum(1 for ok in tools.values() if ok is True)
    detail = f"{ok_count}/{len(tools)} importable" if tools else "unknown"
    return _check_line("OpenDuck tools", check, detail)


def _check_line(label: str, check: dict[str, Any], detail: Any) -> str:
    status = str(check.get("status", "warning"))
    message = str(check.get("message") or "")
    suffix = f" - {message}" if message else ""
    return f"{_status_label(status):8} {label:16} {detail}{suffix}"


def _status_label(status: str) -> str:
    normalized = status.lower()
    if normalized == "ok":
        return "[OK]"
    if normalized == "critical":
        return "[CRIT]"
    return "[WARN]"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _run_command(command: list[str], timeout: int = COMMAND_TIMEOUT_SECONDS) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return _command_result(command, ok=False, error="command_not_found")
    except subprocess.TimeoutExpired as exc:
        raw = _combine_output(exc.stdout, exc.stderr)
        return _command_result(command, ok=False, error="timeout", raw=raw, timeout_seconds=timeout)
    except PermissionError as exc:
        return _command_result(command, ok=False, error="permission_denied", raw=str(exc))
    except Exception as exc:
        return _command_result(command, ok=False, error="execution_failed", raw=str(exc))

    raw = _combine_output(completed.stdout, completed.stderr)
    return _command_result(
        command,
        ok=completed.returncode == 0,
        error=None if completed.returncode == 0 else "non_zero_exit",
        raw=raw,
        returncode=completed.returncode,
    )


def _command_result(
    command: list[str],
    ok: bool,
    error: str | None,
    raw: str = "",
    returncode: int | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "command": command,
        "raw": raw,
        "error": error,
        "returncode": returncode,
        "timeout_seconds": timeout_seconds,
    }


def _base_check(result: dict[str, Any], raw_key: str) -> dict[str, Any]:
    return {
        "status": "ok" if result["ok"] is True else "warning",
        raw_key: result["raw"].strip(),
        "error": result["error"],
        "returncode": result["returncode"],
    }


def _overall_status(checks: dict[str, dict[str, Any]]) -> str:
    statuses = [str(check.get("status", "warning")) for check in checks.values()]
    if "critical" in statuses:
        return "critical"
    if "warning" in statuses:
        return "warning"
    return "ok"


def _summary(checks: dict[str, dict[str, Any]]) -> list[str]:
    items = [
        _summary_item("power", checks["power"]),
        _summary_item("temperature", checks["temperature"]),
        _summary_item("disk", checks["disk"]),
        _summary_item("memory", checks["memory"]),
        _summary_item("USB/D435", checks["usb"]),
        _summary_item("recent logs", checks["recent_logs"]),
        _summary_item("python dependencies", checks["python_dependencies"]),
        _summary_item("OpenDuck tools", checks["openduck_tools"]),
    ]
    return items


def _summary_item(label: str, check: dict[str, Any]) -> str:
    message = str(check.get("message") or check.get("status") or "unknown")
    return f"{label}: {message}"


def _parse_first_float(text: str) -> float | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if match is None:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_disk_usage_percent(text: str) -> int | None:
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 5 and parts[-1] == "/":
            return _parse_percent(parts[4])
    for token in text.split():
        value = _parse_percent(token)
        if value is not None:
            return value
    return None


def _parse_percent(text: str) -> int | None:
    if not text.endswith("%"):
        return None
    try:
        return int(text[:-1])
    except ValueError:
        return None


def _parse_available_memory(text: str) -> str | None:
    for line in text.splitlines():
        if line.lower().startswith("mem:"):
            parts = line.split()
            if len(parts) >= 7:
                return parts[6]
    return None


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def _is_risky_log_line(line: str) -> bool:
    lowered = line.lower()
    return (
        "undervoltage detected" in lowered
        or "usb disconnect" in lowered
        or "reset superspeed usb device" in lowered
    )


def _can_import(name: str) -> tuple[bool, str | None]:
    try:
        importlib.import_module(name)
    except Exception as exc:
        return False, str(exc)
    return True, None


def _write_json_if_requested(payload: dict[str, Any], output_path: str | Path | None) -> None:
    if output_path is None:
        return
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["output_path"] = str(output)


def _combine_output(stdout: Any, stderr: Any) -> str:
    output = stdout or ""
    error = stderr or ""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    if isinstance(error, bytes):
        error = error.decode("utf-8", errors="replace")
    if output and error:
        return f"{output.rstrip()}\n{error.rstrip()}\n"
    return str(output or error or "")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
