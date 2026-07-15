from __future__ import annotations

from dataclasses import dataclass
import json
import os
import socket
from typing import Any

from server.config import (
    ROBOT_COMMAND_HOST,
    ROBOT_COMMAND_PORT,
    ROBOT_COMMAND_TIMEOUT_SECONDS,
)
from server.robot_commands import RobotCommand


LOCALIZATION_AUTO_LOG_ENV = "OPENDUCK_LOCALIZATION_AUTO_LOG"
LOCALIZATION_AUTO_LOG_DEFAULT = "1"


@dataclass(frozen=True)
class RobotExecutionResult:
    handled: bool
    success: bool
    reply_text: str
    detail: str | None = None


def execute_robot_command(command: RobotCommand) -> RobotExecutionResult:
    if command.command_id == "unsupported_backward":
        return RobotExecutionResult(
            handled=True,
            success=False,
            reply_text=command.reply_on_match,
            detail="unsupported_backward",
        )

    payload = _build_payload(command)
    try:
        response = _send_payload(payload)
    except (OSError, ValueError) as exc:
        return RobotExecutionResult(
            handled=True,
            success=False,
            reply_text=(
                "\u673a\u5668\u4eba\u8fd0\u884c\u7a0b\u5e8f\u672a\u542f\u52a8"
                "\uff0c\u65e0\u6cd5\u6267\u884c\u52a8\u4f5c\u3002"
            ),
            detail=str(exc),
        )

    if not response:
        localization_detail = _try_auto_log_localization(command)
        return RobotExecutionResult(
            handled=True,
            success=True,
            reply_text=f"{command.reply_on_match}\uff0c\u5df2\u53d1\u9001\u5230\u673a\u5668\u4eba\u8fd0\u884c\u7a0b\u5e8f\u3002",
            detail=localization_detail,
        )

    if response.get("ok") is True:
        reply_text = response.get("message")
        if not isinstance(reply_text, str) or not reply_text.strip():
            reply_text = f"{command.reply_on_match}\uff0c\u5df2\u6267\u884c\u3002"
        localization_detail = _try_auto_log_localization(command)
        return RobotExecutionResult(
            handled=True,
            success=True,
            reply_text=reply_text.strip(),
            detail=localization_detail,
        )

    reply_text = response.get("message")
    if not isinstance(reply_text, str) or not reply_text.strip():
        reply_text = "\u673a\u5668\u4eba\u547d\u4ee4\u6267\u884c\u5931\u8d25\u3002"
    return RobotExecutionResult(
        handled=True,
        success=False,
        reply_text=reply_text.strip(),
        detail=json.dumps(response, ensure_ascii=False),
    )


def _build_payload(command: RobotCommand) -> dict[str, Any]:
    params: dict[str, Any] = dict(command.params)
    if command.duration_s is not None:
        params["duration_s"] = command.duration_s
    if command.cooldown_s:
        params["cooldown_s"] = command.cooldown_s
    return {
        "type": "robot_command",
        "command_id": command.command_id,
        "priority": command.priority,
        "params": params,
    }


def _send_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    encoded = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
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

    if not raw:
        return None
    decoded = raw.decode("utf-8", errors="replace").strip()
    if not decoded:
        return None
    response = json.loads(decoded)
    if not isinstance(response, dict):
        return {
            "ok": False,
            "message": "\u673a\u5668\u4eba\u8fd0\u884c\u7a0b\u5e8f\u8fd4\u56de\u683c\u5f0f\u9519\u8bef\u3002",
        }
    return response


def _try_auto_log_localization(command: RobotCommand) -> str | None:
    if not _localization_auto_log_enabled():
        return None

    try:
        from server.localization_action_logger import log_action

        result = log_action(
            command_id=command.command_id,
            params=_localization_params(command),
            gyro_duration_s=_localization_gyro_duration(command),
        )
    except Exception as exc:
        return json.dumps(
            {
                "localization_auto_log": {
                    "ok": False,
                    "error": "localization_log_exception",
                    "message": str(exc),
                }
            },
            ensure_ascii=False,
        )

    if result.get("ok") is not True:
        return json.dumps(
            {
                "localization_auto_log": {
                    "ok": False,
                    "error": result.get("error"),
                    "message": result.get("message"),
                }
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "localization_auto_log": {
                "ok": True,
                "summary": result.get("summary"),
                "source": result.get("localization_source"),
            }
        },
        ensure_ascii=False,
    )


def _localization_auto_log_enabled() -> bool:
    value = os.environ.get(LOCALIZATION_AUTO_LOG_ENV, LOCALIZATION_AUTO_LOG_DEFAULT)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _localization_params(command: RobotCommand) -> dict[str, Any]:
    params: dict[str, Any] = dict(command.params)
    if command.duration_s is not None:
        params["duration_s"] = command.duration_s
    return params


def _localization_gyro_duration(command: RobotCommand) -> float:
    if command.command_id in {"turn_left_small", "turn_right_small"}:
        return 0.5
    if command.command_id in {"turn_left_3s", "turn_right_3s"}:
        return 2.0
    if command.command_id != "turn_degrees":
        return 0.0

    degrees = _optional_float(command.params.get("degrees"))
    duration_s = _optional_float(command.duration_s)
    if duration_s is not None:
        return max(0.5, min(2.0, duration_s * 0.5))
    if degrees is not None:
        return max(0.5, min(2.0, abs(degrees) / 45.0))
    return 1.0


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number
