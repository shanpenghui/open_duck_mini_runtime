from __future__ import annotations

import tempfile
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory.service import (
    add_task,
    append_conversation,
    apply_profile_file,
    build_prompt,
    get_identity_prompt,
    list_today_tasks,
    process_direct_command,
)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "assistant.db"
        session_id = "memory-smoke-session"

        apply_profile_file(PROJECT_ROOT / "memory" / "default_profile.json", db_path=db_path)
        seeded_prompt = get_identity_prompt(db_path=db_path)
        assert "小派" in seeded_prompt
        assert "私人语音智能体" in seeded_prompt
        assert "删除、付款、发消息前必须确认" in seeded_prompt

        reply = process_direct_command(session_id, "以后你叫小莓", db_path=db_path)
        assert reply and "小莓" in reply
        assert "小莓" in get_identity_prompt(db_path=db_path)

        append_conversation(session_id, "user", "你叫什么", db_path=db_path)
        prompt = build_prompt(session_id, "你叫什么", db_path=db_path)
        assert any("小莓" in message["content"] for message in prompt)
        assert process_direct_command(session_id, "你叫什么", db_path=db_path) is None
        assert "小莓" in get_identity_prompt(db_path=db_path)

        task_reply = process_direct_command(
            session_id,
            "帮我添加任务：晚上整理项目文档",
            db_path=db_path,
        )
        assert task_reply and "晚上整理项目文档" in task_reply
        tasks = list_today_tasks(db_path=db_path)
        assert any(task["title"] == "晚上整理项目文档" for task in tasks)

        task_id = add_task("测试第二个任务", db_path=db_path)
        assert isinstance(task_id, int)

    print("memory smoke test passed")


if __name__ == "__main__":
    main()
