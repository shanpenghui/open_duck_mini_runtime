from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server import qwen_vision_tool


SAFE_FAILURE_ERRORS = {
    "missing_api_key",
    "image_not_found",
    "camera_not_implemented",
}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    status_payload = qwen_vision_tool.status()
    observe_payload = qwen_vision_tool.observe(
        image_path=args.image,
        prompt=args.prompt,
    )

    output = {
        "ok": _is_smoke_ok(observe_payload),
        "tool": "qwen_vision_smoke_test",
        "image": str(args.image),
        "status": status_payload,
        "observe": observe_payload,
        "notes": [
            "API key values are never printed by this smoke test.",
            "missing_api_key is treated as a safe failure for offline deployment checks.",
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["ok"] else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python tools/qwen_vision_smoke_test.py",
        description="Run Qwen-VL status + single-image observe smoke test without printing secrets.",
    )
    parser.add_argument(
        "--image",
        required=True,
        help="Path to the image that Qwen-VL should analyze.",
    )
    parser.add_argument(
        "--prompt",
        help="Optional custom Chinese prompt, for example: 前面安全吗？",
    )
    return parser


def _is_smoke_ok(observe_payload: dict[str, Any]) -> bool:
    if observe_payload.get("ok") is True:
        return True
    return observe_payload.get("error") in SAFE_FAILURE_ERRORS


if __name__ == "__main__":
    raise SystemExit(main())
