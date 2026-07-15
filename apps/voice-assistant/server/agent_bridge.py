from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from server.config import (
    APP_NAME,
    LLAMA_MODEL_NAME,
    LLAMA_SERVER_URL,
    PORT,
    ROBOT_COMMAND_HOST,
    ROBOT_COMMAND_PORT,
)
from server.robot_commands import COMMANDS
from server.robot_executor import execute_robot_command


BRIDGE_VERSION = "0.1"
HEALTH_URL = f"http://127.0.0.1:{PORT}/healthz"
ALLOWED_ACTIONS = ("stop", "pause", "resume", "head_center")
EXPLICIT_BLOCKED_ACTIONS = (
    "joystick_velocity",
    "walk_forward_step",
    "walk_forward_steps",
    "turn_left",
    "turn_right",
    "turn_left_small",
    "turn_right_small",
    "turn_left_3s",
    "turn_right_3s",
    "strafe_left",
    "strafe_right",
    "strafe_left_step",
    "strafe_right_step",
)
BLOCKED_ACTIONS = tuple(
    sorted((set(COMMANDS) | set(EXPLICIT_BLOCKED_ACTIONS)) - set(ALLOWED_ACTIONS))
)
ACTION_BLOCK_REASON = (
    "OpenClaw bridge v0.1 only allows stop, pause, resume, and head_center. "
    "Body movement, joystick velocity, and parameterized motion are blocked."
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "status":
        payload = build_status()
    elif args.command == "health":
        payload = check_health()
    elif args.command == "robot-state":
        payload = robot_state()
    elif args.command == "action":
        payload = handle_action(args.action, execute=args.execute)
    else:
        parser.error("missing command")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") is True else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.agent_bridge",
        description="Restricted OpenDuck bridge for OpenClaw agent calls.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Print bridge capabilities and safe config summary.")
    subparsers.add_parser("health", help="Check the local OpenDuck WebSocket service health endpoint.")
    subparsers.add_parser("robot-state", help="Report what robot state can be read safely in v0.1.")

    action_parser = subparsers.add_parser("action", help="Preview or execute a whitelisted safe action.")
    action_parser.add_argument("action", help="Action name, for example stop, pause, resume, head_center.")
    action_parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually send the action to the robot runtime. Without this flag, action is dry-run only.",
    )
    action_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only. This is the default and is accepted for explicit OpenClaw prompts.",
    )
    return parser


def build_status() -> dict[str, Any]:
    return {
        "ok": True,
        "bridge": {
            "name": "openduck-openclaw-agent-bridge",
            "version": BRIDGE_VERSION,
            "service": APP_NAME,
            "mode": "restricted-cli",
            "ws_main_chain_touched": False,
        },
        "openduck": {
            "health_url": HEALTH_URL,
            "robot_runtime_endpoint": f"{ROBOT_COMMAND_HOST}:{ROBOT_COMMAND_PORT}",
        },
        "actions": {
            "allowed": list(ALLOWED_ACTIONS),
            "blocked": list(BLOCKED_ACTIONS),
            "default": "dry-run",
            "execute_requires": "--execute",
            "blocked_reason": ACTION_BLOCK_REASON,
        },
        "model": safe_model_config(),
    }


def safe_model_config() -> dict[str, Any]:
    env_file = Path.home() / ".openclaw" / ".env"
    env_values = _read_env_file_keys(env_file)
    base_url = (
        os.environ.get("OPENDUCK_OPENAI_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or LLAMA_SERVER_URL
    )
    api_key_sources = []
    if os.environ.get("OPENAI_API_KEY"):
        api_key_sources.append("env:OPENAI_API_KEY")
    if "OPENAI_API_KEY" in env_values:
        api_key_sources.append("file:~/.openclaw/.env:OPENAI_API_KEY")

    return {
        "api": "OpenAI-compatible",
        "base_url": base_url,
        "default_model": LLAMA_MODEL_NAME,
        "api_key_optional": True,
        "api_key_configured": bool(api_key_sources),
        "api_key_sources": api_key_sources,
        "secret_values_printed": False,
    }


def _read_env_file_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return keys

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key:
            keys.add(key)
    return keys


def check_health() -> dict[str, Any]:
    request = Request(HEALTH_URL, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=2.0) as response:
            raw = response.read().decode("utf-8", errors="replace")
            payload = json.loads(raw) if raw.strip() else {}
            return {
                "ok": 200 <= response.status < 300,
                "url": HEALTH_URL,
                "status_code": response.status,
                "payload": payload,
            }
    except HTTPError as exc:
        return {
            "ok": False,
            "url": HEALTH_URL,
            "status_code": exc.code,
            "error": str(exc),
        }
    except (OSError, URLError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "url": HEALTH_URL,
            "status_code": None,
            "error": str(exc),
        }


def robot_state() -> dict[str, Any]:
    return {
        "ok": True,
        "state": "unknown",
        "direct_runtime_state_readable": False,
        "message": (
            "runtime state is not directly readable in bridge v0.1. Use health, "
            "systemd status, or add a future read-only runtime state endpoint."
        ),
        "safe_sources_now": [
            "OpenDuck /healthz",
            "systemctl status voice-assistant-websocket.service",
            "systemctl status open-duck-runtime.service",
        ],
    }


def handle_action(action: str, *, execute: bool) -> dict[str, Any]:
    normalized_action = action.strip()
    if normalized_action not in ALLOWED_ACTIONS:
        return {
            "ok": False,
            "action": normalized_action,
            "executed": False,
            "blocked": True,
            "message": "action blocked by OpenClaw bridge allowlist",
            "reason": ACTION_BLOCK_REASON,
            "detail": {
                "allowed": list(ALLOWED_ACTIONS),
            },
        }

    command = COMMANDS.get(normalized_action)
    if command is None:
        return {
            "ok": False,
            "action": normalized_action,
            "executed": False,
            "blocked": True,
            "message": "allowed action is missing from server.robot_commands.COMMANDS",
            "reason": "Bridge refuses to synthesize robot commands outside the existing command table.",
            "detail": None,
        }

    if not execute:
        return {
            "ok": True,
            "action": normalized_action,
            "executed": False,
            "blocked": False,
            "message": "dry-run only; add --execute after human confirmation to send this action",
            "detail": _command_preview(command),
        }

    result = execute_robot_command(command)
    return {
        "ok": result.success,
        "action": normalized_action,
        "executed": True,
        "blocked": False,
        "message": result.reply_text,
        "detail": result.detail,
    }


def _command_preview(command: Any) -> dict[str, Any]:
    return {
        "command_id": command.command_id,
        "priority": command.priority,
        "duration_s": command.duration_s,
        "cooldown_s": command.cooldown_s,
        "params": command.params,
    }


if __name__ == "__main__":
    raise SystemExit(main())
