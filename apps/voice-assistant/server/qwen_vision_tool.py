from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TOOL_VERSION = "0.1"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-vl-plus"
DEFAULT_PROMPT = (
    "请用中文观察这张图，并尽量只输出 JSON。字段包括：scene、objects、risks、"
    "safe_to_move、suggested_action。suggested_action 只能是 pause、stop、"
    "head_center、none。不要建议前进、转向、横移、持续运动或速度控制。"
)
ENV_PATHS = (
    Path.home() / ".openduck" / ".env",
    Path.home() / ".openclaw" / ".env",
)
ALLOWED_SUGGESTED_ACTIONS = {"pause", "stop", "head_center", "none"}
BLOCKED_ACTION_WORDS = {
    "resume",
    "joystick_velocity",
    "walk_forward_step",
    "walk_forward_steps",
    "turn_left",
    "turn_right",
    "strafe_left",
    "strafe_right",
    "forward",
    "backward",
    "walk",
    "turn",
    "strafe",
    "velocity",
    "向前",
    "前进",
    "往前",
    "后退",
    "左转",
    "右转",
    "横移",
    "速度",
    "持续",
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "status":
        payload = status()
    elif args.command == "observe":
        payload = observe(image_path=args.image, prompt=args.prompt)
    else:
        parser.error("missing command")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") is True else 1


def describe_image(
    image_path: str | Path,
    prompt: str | None = None,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Describe one image through an OpenAI-compatible Qwen vision endpoint."""
    config = load_config(base_url=base_url, api_key=api_key, model=model)
    path = Path(image_path)
    if not path.is_file():
        return _error(
            "image_not_found",
            f"Image file does not exist: {path}",
            model=config["model"],
        )

    if not config["api_key"]:
        return _error(
            "missing_api_key",
            "Qwen vision API key is not configured.",
            model=config["model"],
        )

    image_url = _image_data_url(path)
    if image_url is None:
        return _error(
            "image_read_failed",
            f"Failed to read image file: {path}",
            model=config["model"],
        )

    payload = {
        "model": config["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt or DEFAULT_PROMPT},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
        "temperature": 0.2,
        "max_tokens": 512,
    }
    request = Request(
        _chat_completions_url(config["base_url"]),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config['api_key']}",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout_s) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return _error(
            "vision_http_error",
            f"Qwen vision endpoint returned HTTP {exc.code}",
            model=config["model"],
            detail=detail[:300],
        )
    except (OSError, TimeoutError, URLError) as exc:
        return _error(
            "vision_request_failed",
            str(exc),
            model=config["model"],
        )

    try:
        response_payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return _error(
            "vision_bad_response",
            "Qwen vision endpoint returned invalid JSON.",
            model=config["model"],
            detail=raw_body[:300],
        )

    raw_text = _extract_message_text(response_payload)
    parsed = _parse_model_json(raw_text)
    return _normalize_vision_result(parsed, raw_text=raw_text, model=config["model"])


def capture_camera_snapshot(output_path: str | Path | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "available": False,
        "error": "camera_not_implemented",
        "message": "camera snapshot is not implemented in qwen_vision_tool v0.1",
        "output_path": str(output_path) if output_path is not None else None,
    }


def observe(
    image_path: str | Path | None = None,
    prompt: str | None = None,
) -> dict[str, Any]:
    if image_path is None:
        snapshot = capture_camera_snapshot()
        snapshot["source"] = "qwen-vision"
        snapshot["robot_action_executed"] = False
        return snapshot

    return describe_image(image_path, prompt)


def status() -> dict[str, Any]:
    config = load_config()
    return {
        "ok": True,
        "tool": {
            "name": "qwen_vision_tool",
            "version": TOOL_VERSION,
            "mode": "read-only vision observer",
        },
        "config": {
            "base_url": config["base_url"],
            "model": config["model"],
            "api_key_configured": bool(config["api_key"]),
            "api_key_sources": config["api_key_sources"],
            "secret_values_printed": False,
        },
        "camera": {
            "implemented": False,
            "message": "camera snapshot is not implemented in v0.1",
        },
        "safety": {
            "executes_robot_actions": False,
            "allowed_suggested_actions": sorted(ALLOWED_SUGGESTED_ACTIONS),
            "blocked": [
                "resume",
                "joystick_velocity",
                "walk_forward_step",
                "walk_forward_steps",
                "turn_left",
                "turn_right",
                "strafe_left",
                "strafe_right",
                "continuous motion",
                "velocity control",
            ],
        },
    }


def load_config(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    env_files = _load_env_files()
    key_sources: list[str] = []

    resolved_api_key = api_key or os.environ.get("QWEN_VISION_API_KEY")
    if api_key:
        key_sources.append("argument:api_key")
    elif os.environ.get("QWEN_VISION_API_KEY"):
        key_sources.append("env:QWEN_VISION_API_KEY")
    else:
        for path, values in env_files:
            if values.get("QWEN_VISION_API_KEY"):
                resolved_api_key = values["QWEN_VISION_API_KEY"]
                key_sources.append(f"file:{_display_env_path(path)}:QWEN_VISION_API_KEY")
                break

    resolved_base_url = (
        base_url
        or os.environ.get("QWEN_VISION_BASE_URL")
        or _first_env_file_value(env_files, "QWEN_VISION_BASE_URL")
        or DEFAULT_BASE_URL
    )
    resolved_model = (
        model
        or os.environ.get("QWEN_VISION_MODEL")
        or _first_env_file_value(env_files, "QWEN_VISION_MODEL")
        or DEFAULT_MODEL
    )

    return {
        "base_url": resolved_base_url.rstrip("/"),
        "model": resolved_model,
        "api_key": resolved_api_key,
        "api_key_sources": key_sources,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.qwen_vision_tool",
        description="Read-only Qwen vision observer for OpenDuck.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Print safe config summary.")

    observe_parser = subparsers.add_parser("observe", help="Observe one image or return camera placeholder.")
    observe_parser.add_argument("--image", help="Path to a local image. If omitted, camera snapshot placeholder is used.")
    observe_parser.add_argument("--prompt", help="Optional custom vision prompt.")
    return parser


def _load_env_files() -> list[tuple[Path, dict[str, str]]]:
    loaded: list[tuple[Path, dict[str, str]]] = []
    for path in ENV_PATHS:
        values: dict[str, str] = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            loaded.append((path, values))
            continue

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
        loaded.append((path, values))
    return loaded


def _first_env_file_value(env_files: list[tuple[Path, dict[str, str]]], key: str) -> str | None:
    for _, values in env_files:
        value = values.get(key)
        if value:
            return value
    return None


def _display_env_path(path: Path) -> str:
    home = Path.home()
    try:
        return "~/" + str(path.relative_to(home)).replace("\\", "/")
    except ValueError:
        return str(path)


def _chat_completions_url(base_url: str) -> str:
    clean = base_url.rstrip("/")
    if clean.endswith("/chat/completions"):
        return clean
    return clean + "/chat/completions"


def _image_data_url(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _extract_message_text(response_payload: dict[str, Any]) -> str:
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "\n".join(parts).strip()
    text = first.get("text")
    return text.strip() if isinstance(text, str) else ""


def _parse_model_json(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    candidates = [fenced.group(1)] if fenced else []
    candidates.append(text)
    object_match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if object_match:
        candidates.append(object_match.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _normalize_vision_result(parsed: dict[str, Any], *, raw_text: str, model: str) -> dict[str, Any]:
    scene = _string_or_default(parsed.get("scene"), raw_text or "模型没有返回可读描述。")
    objects = _string_list(parsed.get("objects"))
    risks = _string_list(parsed.get("risks"))
    safe_to_move = parsed.get("safe_to_move")
    if not isinstance(safe_to_move, bool):
        safe_to_move = False if risks else None

    action = _normalize_action(parsed.get("suggested_action"), raw_text)
    result = {
        "ok": True,
        "source": "qwen-vision",
        "model": model,
        "scene": scene,
        "objects": objects,
        "risks": risks,
        "safe_to_move": safe_to_move,
        "suggested_action": action,
        "raw_text": raw_text,
        "error": None,
        "robot_action_executed": False,
    }
    blocked_reason = _blocked_action_reason(parsed.get("suggested_action"), raw_text)
    if blocked_reason:
        result["suggested_action"] = "none"
        result["action_blocked"] = True
        result["blocked_reason"] = blocked_reason
    return result


def _normalize_action(value: Any, raw_text: str) -> str:
    if isinstance(value, str):
        action = value.strip().lower()
        if action in ALLOWED_SUGGESTED_ACTIONS:
            return action
    lowered = raw_text.lower()
    if "stop" in lowered or "停止" in raw_text or "停下" in raw_text:
        return "stop"
    if "pause" in lowered or "暂停" in raw_text:
        return "pause"
    if "head_center" in lowered or "头回正" in raw_text:
        return "head_center"
    return "none"


def _blocked_action_reason(value: Any, raw_text: str) -> str | None:
    candidates = []
    if isinstance(value, str):
        candidates.append(value.lower())
    candidates.append(raw_text.lower())
    joined = "\n".join(candidates)
    if any(word in joined for word in BLOCKED_ACTION_WORDS):
        return "vision tool cannot suggest body movement or velocity control"
    return None


def _string_or_default(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _error(
    error: str,
    message: str,
    *,
    model: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "source": "qwen-vision",
        "model": model,
        "error": error,
        "message": message,
        "secret_values_printed": False,
        "robot_action_executed": False,
    }
    if detail:
        payload["detail"] = detail
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
