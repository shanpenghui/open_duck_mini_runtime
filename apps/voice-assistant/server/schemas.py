import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from server.config import APP_NAME, MODEL_NAME, PROTOCOL_VERSION


SUPPORTED_TYPES = {"chat", "ping", "robot_command"}


@dataclass(frozen=True)
class ChatRequest:
    type: str
    session_id: str
    text: str
    request_id: str
    timestamp: str | None = None
    device_id: str | None = None
    stream: bool = False


@dataclass(frozen=True)
class PingRequest:
    type: str
    request_id: str
    timestamp: str | None = None


@dataclass(frozen=True)
class RobotCommandRequest:
    type: str
    command_id: str
    request_id: str
    session_id: str | None = None
    priority: str = "high"
    params: dict[str, float] = field(default_factory=dict)
    timestamp: str | None = None


InboundMessage = ChatRequest | PingRequest | RobotCommandRequest


class ProtocolError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        request_id: str | None = None,
        session_id: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id
        self.session_id = session_id
        self.retryable = retryable


def parse_client_message(raw_message: str) -> InboundMessage:
    try:
        message = json.loads(raw_message)
    except json.JSONDecodeError as exc:
        raise ProtocolError("INVALID_JSON", "请求不是合法 JSON") from exc

    if not isinstance(message, dict):
        raise ProtocolError("INVALID_REQUEST", "请求 JSON 必须是对象")

    request_id = _optional_string(message, "request_id")
    session_id = _optional_string(message, "session_id")

    version = message.get("version", PROTOCOL_VERSION)
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            "UNSUPPORTED_VERSION",
            f"不支持的协议版本：{version}",
            request_id=request_id,
            session_id=session_id,
        )

    message_type = _required_string(message, "type")
    if message_type not in SUPPORTED_TYPES:
        raise ProtocolError(
            "UNSUPPORTED_TYPE",
            f"不支持的消息类型：{message_type}",
            request_id=request_id,
            session_id=session_id,
        )

    if message_type == "ping":
        return PingRequest(
            type=message_type,
            request_id=request_id or f"ping-{uuid.uuid4()}",
            timestamp=_optional_string(message, "timestamp"),
        )

    if message_type == "robot_command":
        return _parse_robot_command_message(
            message,
            request_id=request_id,
            session_id=session_id,
        )

    return _parse_chat_message(message, request_id=request_id, session_id=session_id)


def parse_chat_request(raw_message: str) -> ChatRequest:
    message = parse_client_message(raw_message)
    if not isinstance(message, ChatRequest):
        raise ProtocolError(
            "UNSUPPORTED_TYPE",
            "type 必须是 chat",
            request_id=message.request_id,
        )
    return message


def reply_response(
    *,
    request_id: str,
    session_id: str,
    text: str,
    latency_ms: int,
) -> dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "type": "reply",
        "request_id": request_id,
        "session_id": session_id,
        "timestamp": _now_iso(),
        "payload": {
            "text": text,
            "finish_reason": "stop",
            "latency_ms": latency_ms,
        },
        "meta": {
            "server": APP_NAME,
            "model": MODEL_NAME,
        },
        # Compatibility aliases for the current Android parser.
        "text": text,
        "latency_ms": latency_ms,
    }


def pong_response(request_id: str) -> dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "type": "pong",
        "request_id": request_id,
        "timestamp": _now_iso(),
        "payload": {},
    }


def error_response(
    code: str,
    message: str,
    *,
    request_id: str | None = None,
    session_id: str | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "type": "error",
        "request_id": request_id,
        "session_id": session_id,
        "timestamp": _now_iso(),
        "payload": {
            "code": code,
            "message": message,
            "retryable": retryable,
        },
        # Compatibility aliases for simple clients and current Android logging.
        "code": code,
        "message": message,
    }


def _parse_chat_message(
    message: dict[str, Any],
    *,
    request_id: str | None,
    session_id: str | None,
) -> ChatRequest:
    body = message.get("payload")
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise ProtocolError(
            "INVALID_REQUEST",
            "payload 必须是对象",
            request_id=request_id,
            session_id=session_id,
        )

    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        # Compatibility with the current Android flat request:
        # {"type":"chat","session_id":"...","text":"..."}.
        text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ProtocolError(
            "INVALID_REQUEST",
            "缺少 text 字段",
            request_id=request_id,
            session_id=session_id,
        )

    stream = body.get("stream", False)
    if not isinstance(stream, bool):
        raise ProtocolError(
            "INVALID_REQUEST",
            "payload.stream 必须是布尔值",
            request_id=request_id,
            session_id=session_id,
        )

    return ChatRequest(
        type="chat",
        session_id=session_id
        or _required_chat_string(message, "session_id", request_id, session_id),
        text=text.strip(),
        request_id=request_id or f"req-{uuid.uuid4()}",
        timestamp=_optional_string(message, "timestamp"),
        device_id=_optional_string(message, "device_id"),
        stream=stream,
    )


def _parse_robot_command_message(
    message: dict[str, Any],
    *,
    request_id: str | None,
    session_id: str | None,
) -> RobotCommandRequest:
    body = message.get("payload")
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise ProtocolError(
            "INVALID_REQUEST",
            "payload 必须是对象",
            request_id=request_id,
            session_id=session_id,
        )

    command_id = body.get("command_id")
    if not isinstance(command_id, str) or not command_id.strip():
        command_id = message.get("command_id")
    if not isinstance(command_id, str) or not command_id.strip():
        raise ProtocolError(
            "INVALID_REQUEST",
            "缺少 command_id 字段",
            request_id=request_id,
            session_id=session_id,
        )

    priority = body.get("priority")
    if not isinstance(priority, str) or not priority.strip():
        priority = message.get("priority", "high")
    if not isinstance(priority, str) or not priority.strip():
        raise ProtocolError(
            "INVALID_REQUEST",
            "priority 字段必须是字符串",
            request_id=request_id,
            session_id=session_id,
        )

    params_raw = body.get("params")
    if params_raw is None:
        params_raw = message.get("params", {})
    params = _parse_number_params(
        params_raw,
        request_id=request_id,
        session_id=session_id,
    )

    return RobotCommandRequest(
        type="robot_command",
        command_id=command_id.strip(),
        request_id=request_id or f"robot-{uuid.uuid4()}",
        session_id=session_id,
        priority=priority.strip(),
        params=params,
        timestamp=_optional_string(message, "timestamp"),
    )


def _parse_number_params(
    params_raw: Any,
    *,
    request_id: str | None,
    session_id: str | None,
) -> dict[str, float]:
    if params_raw is None:
        return {}
    if not isinstance(params_raw, dict):
        raise ProtocolError(
            "INVALID_REQUEST",
            "params 必须是对象",
            request_id=request_id,
            session_id=session_id,
        )

    params: dict[str, float] = {}
    for key, value in params_raw.items():
        if not isinstance(key, str) or not key.strip():
            raise ProtocolError(
                "INVALID_REQUEST",
                "params 的键必须是字符串",
                request_id=request_id,
                session_id=session_id,
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProtocolError(
                "INVALID_REQUEST",
                f"params.{key} 必须是数字",
                request_id=request_id,
                session_id=session_id,
            )
        params[key.strip()] = float(value)
    return params


def _required_string(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError("INVALID_REQUEST", f"缺少 {field_name} 字段")
    return value.strip()


def _required_chat_string(
    payload: dict[str, Any],
    field_name: str,
    request_id: str | None,
    session_id: str | None,
) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(
            "INVALID_REQUEST",
            f"缺少 {field_name} 字段",
            request_id=request_id,
            session_id=session_id,
        )
    return value.strip()


def _optional_string(payload: dict[str, Any], field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProtocolError("INVALID_REQUEST", f"{field_name} 字段必须是字符串")
    return value.strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
