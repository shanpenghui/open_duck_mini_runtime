import json
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from memory.service import append_conversation, build_prompt, process_direct_command
from server.config import (
    LLAMA_MAX_TOKENS,
    LLAMA_MODEL_NAME,
    LLAMA_SERVER_URL,
    LLAMA_TEMPERATURE,
    LLAMA_TIMEOUT_SECONDS,
)


class InferenceError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def generate_reply(text: str, session_id: str) -> str:
    """Call the local llama-server OpenAI-compatible chat endpoint."""
    direct_reply = process_direct_command(session_id, text)
    if direct_reply is not None:
        append_conversation(session_id, "user", text)
        append_conversation(session_id, "assistant", direct_reply)
        return direct_reply

    messages = build_prompt(session_id, text)
    payload = {
        "model": LLAMA_MODEL_NAME,
        "messages": messages,
        "max_tokens": LLAMA_MAX_TOKENS,
        "temperature": LLAMA_TEMPERATURE,
    }

    request = Request(
        LLAMA_SERVER_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=LLAMA_TIMEOUT_SECONDS) as response:
            raw_body = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise InferenceError(
            "MODEL_HTTP_ERROR",
            f"llama-server returned HTTP {exc.code}: {detail[:200]}",
            retryable=500 <= exc.code < 600,
        ) from exc
    except (URLError, socket.timeout, TimeoutError) as exc:
        raise InferenceError(
            "MODEL_UNAVAILABLE",
            "llama-server is unavailable or timed out",
            retryable=True,
        ) from exc

    try:
        response_payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise InferenceError(
            "MODEL_BAD_RESPONSE",
            "llama-server returned invalid JSON",
            retryable=True,
        ) from exc

    reply_text = _extract_reply_text(response_payload)
    append_conversation(session_id, "user", text)
    append_conversation(session_id, "assistant", reply_text)
    return reply_text


def _extract_reply_text(response_payload: dict[str, Any]) -> str:
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise InferenceError("MODEL_BAD_RESPONSE", "llama-server response is missing choices")

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise InferenceError("MODEL_BAD_RESPONSE", "llama-server choices format is invalid")

    message = first_choice.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()

    text = first_choice.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()

    raise InferenceError(
        "MODEL_EMPTY_RESPONSE",
        "llama-server did not return speakable reply text",
        retryable=True,
    )
