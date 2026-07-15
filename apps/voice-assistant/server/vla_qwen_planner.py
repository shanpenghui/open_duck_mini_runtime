from __future__ import annotations

import argparse
import base64
import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from server.qwen_vision_tool import load_config


TOOL_VERSION = "0.1"
SOURCE = "vla-qwen-planner"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTEXT_PATH = PROJECT_ROOT / "data" / "vla" / "context_latest.json"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "vla" / "plan_latest.json"
DEFAULT_TIMEOUT_S = 45.0

ALLOWED_ACTIONS = {
    "none",
    "stop",
    "pause",
    "head_center",
    "look_left",
    "look_right",
    "walk_forward_step",
    "turn_degrees",
    "strafe_left_step",
    "strafe_right_step",
}

BODY_ACTIONS = {
    "walk_forward_step",
    "turn_degrees",
    "strafe_left_step",
    "strafe_right_step",
}


def planner_status() -> dict[str, Any]:
    config = load_config()
    return {
        "ok": True,
        "source": SOURCE,
        "tool_version": TOOL_VERSION,
        "message": "只读 Qwen-VL dry-run 规划器：读取 VLA context，只输出动作建议，不控制机器人。",
        "config": {
            "base_url": config["base_url"],
            "model": config["model"],
            "api_key_configured": bool(config["api_key"]),
            "api_key_sources": config["api_key_sources"],
            "secret_values_printed": False,
        },
        "allowed_actions": sorted(ALLOWED_ACTIONS),
        "executes_robot_actions": False,
        "robot_action_executed": False,
    }


def plan_from_context(
    *,
    context_path: str | Path = DEFAULT_CONTEXT_PATH,
    output_path: str | Path | None = DEFAULT_OUTPUT_PATH,
    instruction: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    allow_no_image: bool = False,
) -> dict[str, Any]:
    context_result = _load_context(context_path)
    if context_result.get("ok") is not True:
        payload = context_result
        _write_json_if_requested(payload, output_path)
        return payload

    context = context_result["context"]
    if instruction:
        context["instruction"] = instruction

    image_path = _context_image_path(context)
    if image_path is None and not allow_no_image:
        payload = _error(
            "image_unavailable",
            "VLA context does not contain a readable RGB image path. Build context after camera is available, or pass --allow-no-image for a conservative fallback.",
            context_path=str(Path(context_path).expanduser()),
            context_summary=context.get("summary"),
        )
        _write_json_if_requested(payload, output_path)
        return payload

    if image_path is None and allow_no_image:
        payload = _fallback_plan(context, reason="image_unavailable")
        _write_json_if_requested(payload, output_path)
        return payload

    config = load_config(base_url=base_url, api_key=api_key, model=model)
    if not config["api_key"]:
        payload = _error(
            "missing_api_key",
            "Qwen-VL API key is not configured.",
            model=config["model"],
            context_path=str(Path(context_path).expanduser()),
            context_summary=context.get("summary"),
        )
        _write_json_if_requested(payload, output_path)
        return payload

    prompt = _build_prompt(context)
    raw_result = _call_qwen_vl(
        image_path=image_path,
        prompt=prompt,
        config=config,
        timeout_s=timeout_s,
    )
    if raw_result.get("ok") is not True:
        payload = raw_result
        payload["context_summary"] = context.get("summary")
        _write_json_if_requested(payload, output_path)
        return payload

    normalized = _normalize_plan(
        raw_result.get("raw_text") or "",
        context=context,
        model=config["model"],
    )
    payload = {
        "ok": True,
        "source": SOURCE,
        "tool_version": TOOL_VERSION,
        "mode": "vla_qwen_dry_run_plan",
        "timestamp": _now_iso(),
        "model": config["model"],
        "context_path": str(Path(context_path).expanduser()),
        "image_path": str(image_path),
        "context_summary": context.get("summary"),
        "plan": normalized,
        "raw_text": raw_result.get("raw_text"),
        "safety": {
            "dry_run_only": True,
            "executes_robot_actions": False,
            "body_actions_require_confirmation": True,
            "allowed_actions": sorted(ALLOWED_ACTIONS),
        },
        "summary": _summary(normalized),
        "robot_action_executed": False,
    }
    _write_json_if_requested(payload, output_path)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        payload = planner_status()
    elif args.command == "plan":
        payload = plan_from_context(
            context_path=args.context,
            output_path=args.output,
            instruction=args.instruction,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            timeout_s=args.timeout,
            allow_no_image=args.allow_no_image,
        )
    else:
        parser.error("missing command")

    _print_payload(payload, output_format=args.format)
    return 0 if payload.get("ok") is True else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.vla_qwen_planner",
        description="Read a VLA context and ask Qwen-VL for a dry-run action suggestion.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Print Qwen-VL planner status.")
    _add_format_arg(status_parser)

    plan_parser = subparsers.add_parser("plan", help="Generate one dry-run VLA plan from context JSON.")
    plan_parser.add_argument("--context", default=str(DEFAULT_CONTEXT_PATH))
    plan_parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    plan_parser.add_argument("--instruction", help="Override instruction in context JSON.")
    plan_parser.add_argument("--base-url")
    plan_parser.add_argument("--api-key")
    plan_parser.add_argument("--model")
    plan_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    plan_parser.add_argument("--allow-no-image", action="store_true")
    _add_format_arg(plan_parser)
    return parser


def _add_format_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        choices=("json", "brief"),
        default="json",
        help="Output format. Use brief for a short human-readable line.",
    )


def _load_context(context_path: str | Path) -> dict[str, Any]:
    path = Path(context_path).expanduser()
    if not path.is_file():
        return _error("context_not_found", f"Context file does not exist: {path}", context_path=str(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _error("context_read_failed", str(exc), context_path=str(path))
    if not isinstance(payload, dict):
        return _error("invalid_context", "Context JSON must be an object.", context_path=str(path))
    return {"ok": True, "context": payload}


def _context_image_path(context: dict[str, Any]) -> Path | None:
    image = context.get("image") if isinstance(context.get("image"), dict) else {}
    path_value = image.get("path")
    if not path_value:
        return None
    path = Path(str(path_value)).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path if path.is_file() else None


def _build_prompt(context: dict[str, Any]) -> str:
    compact_context = {
        "instruction": context.get("instruction"),
        "depth": context.get("depth"),
        "imu": context.get("imu"),
        "localization": context.get("localization"),
        "recent_actions": context.get("recent_actions"),
        "safety_notes": context.get("safety_notes"),
    }
    return (
        "你是 OpenDuck 机器人的 VLA dry-run 规划器。你只输出 JSON，不执行动作。\n"
        "请结合图片和下面的机器人状态，给出下一步动作建议。动作只是建议，必须 dry-run。\n"
        "允许的 action 只有：none, stop, pause, head_center, look_left, look_right, "
        "walk_forward_step, turn_degrees, strafe_left_step, strafe_right_step。\n"
        "如果前方障碍或视觉/深度不可靠，优先 none、stop、pause 或小角度 turn_degrees。\n"
        "walk_forward_step、strafe_left_step、strafe_right_step、turn_degrees 必须 requires_confirmation=true。\n"
        "turn_degrees 的 params 格式为 {\"direction\": 1 或 -1, \"degrees\": 5到45之间}。\n"
        "输出 JSON 字段必须包含：intent, action, params, reason, confidence, requires_confirmation, safety_notes。\n"
        "不要输出 Markdown，不要解释 JSON 以外内容。\n\n"
        f"机器人状态 JSON：{json.dumps(compact_context, ensure_ascii=False)}"
    )


def _call_qwen_vl(
    *,
    image_path: Path,
    prompt: str,
    config: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any]:
    image_url = _image_data_url(image_path)
    if image_url is None:
        return _error("image_read_failed", f"Failed to read image file: {image_path}", model=config["model"])

    payload = {
        "model": config["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
        "temperature": 0.1,
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
        with urlopen(request, timeout=max(5.0, float(timeout_s))) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return _error("qwen_http_error", f"Qwen-VL endpoint returned HTTP {exc.code}", model=config["model"], detail=detail[:500])
    except (OSError, TimeoutError, URLError) as exc:
        return _error("qwen_request_failed", str(exc), model=config["model"])

    try:
        response_payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return _error("qwen_bad_response", "Qwen-VL endpoint returned invalid JSON.", model=config["model"], detail=raw_body[:500])
    raw_text = _extract_message_text(response_payload)
    return {
        "ok": True,
        "source": SOURCE,
        "model": config["model"],
        "raw_text": raw_text,
        "robot_action_executed": False,
    }


def _normalize_plan(raw_text: str, *, context: dict[str, Any], model: str) -> dict[str, Any]:
    parsed = _parse_json_object(raw_text)
    action = str(parsed.get("action") or "none").strip()
    if action not in ALLOWED_ACTIONS:
        action = "none"

    params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
    params = _normalize_params(action, params)
    safety_notes = _string_list(parsed.get("safety_notes"))
    safety_notes.extend(str(item) for item in context.get("safety_notes") or [])
    safety_notes = _dedupe(safety_notes)

    action, params, safety_notes = _apply_local_safety_filter(action, params, safety_notes, context=context)
    requires_confirmation = bool(parsed.get("requires_confirmation"))
    if action in BODY_ACTIONS:
        requires_confirmation = True

    confidence = _optional_float(parsed.get("confidence"))
    if confidence is None:
        confidence = 0.3
    confidence = max(0.0, min(1.0, confidence))

    return {
        "intent": str(parsed.get("intent") or _intent_for_action(action)),
        "action": action,
        "params": params,
        "reason": str(parsed.get("reason") or "模型未给出明确原因"),
        "confidence": round(confidence, 2),
        "requires_confirmation": requires_confirmation,
        "safety_notes": safety_notes,
        "model": model,
        "dry_run": True,
        "robot_action_executed": False,
    }


def _normalize_params(action: str, params: dict[str, Any]) -> dict[str, Any]:
    if action == "turn_degrees":
        direction = _optional_float(params.get("direction"))
        direction = 1.0 if direction is None or direction >= 0 else -1.0
        degrees = _optional_float(params.get("degrees"))
        if degrees is None:
            degrees = 15.0
        degrees = max(5.0, min(45.0, abs(degrees)))
        return {"direction": direction, "degrees": degrees}
    return {}


def _apply_local_safety_filter(
    action: str,
    params: dict[str, Any],
    safety_notes: list[str],
    *,
    context: dict[str, Any],
) -> tuple[str, dict[str, Any], list[str]]:
    depth = context.get("depth") if isinstance(context.get("depth"), dict) else {}
    blocked = bool(depth.get("blocked"))
    front = _optional_float(depth.get("front_min_distance_m"))
    depth_ok = depth.get("ok") is True
    imu = context.get("imu") if isinstance(context.get("imu"), dict) else {}
    imu_safe = imu.get("safe_for_navigation") is True

    if action in BODY_ACTIONS and not imu_safe:
        safety_notes.append("blocked_by_local_filter:imu_not_safe")
        return "none", {}, _dedupe(safety_notes)
    if action == "walk_forward_step" and (not depth_ok or blocked or (front is not None and front < 0.7)):
        safety_notes.append("blocked_by_local_filter:front_not_clear")
        return "none", {}, _dedupe(safety_notes)
    if action in {"strafe_left_step", "strafe_right_step"} and not depth_ok:
        safety_notes.append("blocked_by_local_filter:depth_unavailable")
        return "none", {}, _dedupe(safety_notes)
    return action, params, _dedupe(safety_notes)


def _fallback_plan(context: dict[str, Any], *, reason: str) -> dict[str, Any]:
    plan = {
        "intent": "hold_position",
        "action": "none",
        "params": {},
        "reason": f"无法调用视觉规划：{reason}",
        "confidence": 0.2,
        "requires_confirmation": False,
        "safety_notes": _dedupe([reason, *(str(item) for item in context.get("safety_notes") or [])]),
        "dry_run": True,
        "robot_action_executed": False,
    }
    return {
        "ok": True,
        "source": SOURCE,
        "tool_version": TOOL_VERSION,
        "mode": "vla_qwen_dry_run_plan",
        "timestamp": _now_iso(),
        "context_summary": context.get("summary"),
        "plan": plan,
        "summary": _summary(plan),
        "robot_action_executed": False,
    }


def _summary(plan: dict[str, Any]) -> str:
    action = plan.get("action")
    reason = plan.get("reason")
    return f"VLA dry-run建议: action={action}, 执行=false, 原因={reason}"


def _image_data_url(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _chat_completions_url(base_url: str) -> str:
    clean = base_url.rstrip("/")
    if clean.endswith("/chat/completions"):
        return clean
    return clean + "/chat/completions"


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
            parts = [item.get("text") for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)]
            return "\n".join(parts).strip()
    text = first.get("text")
    return text.strip() if isinstance(text, str) else ""


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    candidates = [text]
    if "```" in text:
        parts = text.split("```")
        candidates.extend(part.strip().removeprefix("json").strip() for part in parts)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _intent_for_action(action: str) -> str:
    if action in {"none", "pause", "stop"}:
        return "hold_position"
    if action.startswith("look") or action == "head_center":
        return "look"
    if action == "turn_degrees":
        return "turn"
    return "move_small"


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _print_payload(payload: dict[str, Any], *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(str(payload.get("summary") or payload.get("message") or payload.get("error") or "OK"))


def _write_json_if_requested(payload: dict[str, Any], output_path: str | Path | None) -> None:
    if output_path is None:
        return
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
