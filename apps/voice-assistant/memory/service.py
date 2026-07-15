from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import memory.store as store


IDENTITY_KEYS = {
    "assistant_name": "助手名字",
    "assistant_role": "助手身份",
    "assistant_style": "回答风格",
    "language": "语言偏好",
    "assistant_personality": "性格设定",
    "assistant_boundaries": "行为边界",
    "assistant_memory_style": "记忆策略",
}


def save_identity(key: str, value: str, *, db_path: str | Path | None = None) -> None:
    clean_key = key.strip()
    clean_value = value.strip()
    if not clean_key or not clean_value:
        return

    timestamp = store.now_iso()
    store.execute(
        """
        INSERT INTO assistant_profile(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (clean_key, clean_value, timestamp),
        db_path=db_path,
    )


def get_identity_prompt(*, db_path: str | Path | None = None) -> str:
    rows = store.fetch_all(
        "SELECT key, value FROM assistant_profile ORDER BY key",
        db_path=db_path,
    )
    if not rows:
        return "你是手机语音助手。请直接用中文回答，不要展示思考过程。"

    lines = ["你是手机语音助手。请直接用中文回答，不要展示思考过程。"]
    for row in rows:
        label = IDENTITY_KEYS.get(row["key"], row["key"])
        value = row["value"]
        if "\n" in value:
            lines.append(f"{label}:")
            lines.extend(f"- {item}" for item in value.splitlines() if item.strip())
        else:
            lines.append(f"- {label}: {value}")
    return "\n".join(lines)


def apply_profile(
    profile: dict[str, Any],
    *,
    db_path: str | Path | None = None,
) -> None:
    name = _optional_profile_string(profile, "name")
    if name:
        save_identity("assistant_name", name, db_path=db_path)
        save_memory(f"助手名字是{name}", "identity", importance=3, db_path=db_path)

    role = _optional_profile_string(profile, "role")
    if role:
        save_identity("assistant_role", role, db_path=db_path)
        save_memory(f"助手身份是{role}", "identity", importance=3, db_path=db_path)

    _save_profile_list(
        profile,
        "personality",
        "assistant_personality",
        "preference",
        db_path=db_path,
    )
    _save_profile_list(
        profile,
        "boundaries",
        "assistant_boundaries",
        "identity",
        db_path=db_path,
    )
    _save_profile_list(
        profile,
        "memory_style",
        "assistant_memory_style",
        "preference",
        db_path=db_path,
    )


def apply_profile_file(
    profile_path: str | Path,
    *,
    db_path: str | Path | None = None,
) -> None:
    path = Path(profile_path)
    profile = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(profile, dict):
        raise ValueError("profile file must contain a JSON object")
    apply_profile(profile, db_path=db_path)


def save_memory(
    content: str,
    type: str = "fact",
    importance: int = 1,
    *,
    db_path: str | Path | None = None,
) -> None:
    clean_content = content.strip()
    clean_type = type.strip() or "fact"
    if not clean_content:
        return

    timestamp = store.now_iso()
    store.execute(
        """
        INSERT INTO memories(type, content, importance, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (clean_type, clean_content, importance, timestamp, timestamp),
        db_path=db_path,
    )


def get_relevant_memories(
    text: str,
    limit: int = 8,
    *,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    tokens = _tokenize(text)
    rows = store.fetch_all(
        """
        SELECT id, type, content, importance, created_at
        FROM memories
        ORDER BY importance DESC, updated_at DESC, id DESC
        LIMIT 50
        """,
        db_path=db_path,
    )
    if not tokens:
        return rows[:limit]

    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        content = row["content"].lower()
        score = sum(1 for token in tokens if token in content)
        score += int(row.get("importance", 1))
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in scored[:limit]]


def append_conversation(
    session_id: str,
    role: str,
    content: str,
    *,
    db_path: str | Path | None = None,
) -> None:
    clean_content = content.strip()
    if not clean_content:
        return

    store.execute(
        """
        INSERT INTO conversations(session_id, role, content, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (session_id.strip() or "default", role.strip(), clean_content, store.now_iso()),
        db_path=db_path,
    )


def get_recent_conversation(
    session_id: str,
    limit: int = 10,
    *,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    rows = store.fetch_all(
        """
        SELECT role, content, created_at
        FROM conversations
        WHERE session_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (session_id, limit),
        db_path=db_path,
    )
    return list(reversed(rows))


def add_task(
    title: str,
    due_date: str | None = None,
    priority: str = "normal",
    *,
    db_path: str | Path | None = None,
) -> int:
    clean_title = _clean_task_title(title)
    if not clean_title:
        raise ValueError("task title is required")

    today = date.today().isoformat()
    timestamp = store.now_iso()
    cursor = store.execute(
        """
        INSERT INTO tasks(title, status, due_date, priority, created_at, updated_at)
        VALUES (?, 'pending', ?, ?, ?, ?)
        """,
        (clean_title, due_date or today, priority, timestamp, timestamp),
        db_path=db_path,
    )
    return int(cursor.lastrowid)


def list_today_tasks(*, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    today = date.today().isoformat()
    return store.fetch_all(
        """
        SELECT id, title, status, due_date, priority, created_at, updated_at
        FROM tasks
        WHERE due_date = ? AND status != 'cancelled'
        ORDER BY status ASC, priority DESC, id ASC
        """,
        (today,),
        db_path=db_path,
    )


def mark_task_done(task_id: int, *, db_path: str | Path | None = None) -> bool:
    existing = store.fetch_one(
        "SELECT id FROM tasks WHERE id = ?",
        (task_id,),
        db_path=db_path,
    )
    if existing is None:
        return False

    store.execute(
        "UPDATE tasks SET status = 'done', updated_at = ? WHERE id = ?",
        (store.now_iso(), task_id),
        db_path=db_path,
    )
    return True


def build_prompt(
    session_id: str,
    user_text: str,
    *,
    db_path: str | Path | None = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": get_identity_prompt(db_path=db_path)}
    ]

    memories = get_relevant_memories(user_text, limit=8, db_path=db_path)
    if memories:
        memory_text = "\n".join(f"- {row['content']}" for row in memories)
        messages.append(
            {
                "role": "system",
                "content": f"以下是用户明确要求你长期记住的信息，回答时自然参考：\n{memory_text}",
            }
        )

    recent = get_recent_conversation(session_id, limit=10, db_path=db_path)
    for item in recent:
        role = item["role"] if item["role"] in {"user", "assistant", "system"} else "user"
        messages.append({"role": role, "content": item["content"]})

    messages.append({"role": "user", "content": user_text})
    return messages


def process_direct_command(
    session_id: str,
    user_text: str,
    *,
    db_path: str | Path | None = None,
) -> str | None:
    text = user_text.strip()

    identity_reply = _handle_identity(text, db_path=db_path)
    if identity_reply:
        return identity_reply

    memory_content = _extract_memory(text)
    if memory_content:
        save_memory(memory_content, "fact", importance=2, db_path=db_path)
        return f"好的，我记住了：{memory_content}"

    task_title = _extract_new_task(text)
    if task_title:
        task_id = add_task(task_title, db_path=db_path)
        save_memory(f"用户添加了任务：{task_title}", "task", importance=1, db_path=db_path)
        return f"已添加任务 #{task_id}：{task_title}"

    done_task_id = _extract_done_task_id(text)
    if done_task_id is not None:
        if mark_task_done(done_task_id, db_path=db_path):
            return f"已标记任务 #{done_task_id} 为完成。"
        return f"没有找到任务 #{done_task_id}。"

    if _asks_today_tasks(text):
        tasks = list_today_tasks(db_path=db_path)
        if not tasks:
            return "今天还没有记录任务。"
        lines = ["今天的任务："]
        for task in tasks:
            marker = "已完成" if task["status"] == "done" else "待完成"
            lines.append(f"{task['id']}. [{marker}] {task['title']}")
        return "\n".join(lines)

    if _asks_today_summary(text):
        tasks = list_today_tasks(db_path=db_path)
        if not tasks:
            return "今天还没有任务记录，暂时没有可总结的内容。"
        done = [task for task in tasks if task["status"] == "done"]
        pending = [task for task in tasks if task["status"] != "done"]
        lines = [f"今天共记录 {len(tasks)} 个任务，已完成 {len(done)} 个，待完成 {len(pending)} 个。"]
        if pending:
            lines.append("待完成：" + "；".join(task["title"] for task in pending))
        return "\n".join(lines)

    return None


def _handle_identity(text: str, *, db_path: str | Path | None) -> str | None:
    name_match = re.search(r"(?:以后)?你(?:就)?叫(.+?)[。.!！]?$", text)
    if (
        name_match
        and len(name_match.group(1).strip()) <= 16
        and not any(word in text for word in ("什么", "啥", "谁"))
    ):
        name = name_match.group(1).strip(" ，,。.!！")
        save_identity("assistant_name", name, db_path=db_path)
        save_memory(f"助手的名字是{name}", "identity", importance=3, db_path=db_path)
        return f"好的，以后我叫{name}。"

    role_match = re.search(r"你是(?:我的)?(.+?)[。.!！]?$", text)
    if role_match and not text.endswith("谁"):
        role = role_match.group(1).strip(" ，,。.!！")
        save_identity("assistant_role", role, db_path=db_path)
        save_memory(f"助手身份是{role}", "identity", importance=3, db_path=db_path)
        return f"好的，我会记住自己的身份：{role}。"

    style_match = re.search(r"你(?:的)?(?:性格|回答|风格).*(简洁|详细|温柔|直接|正式|轻松)", text)
    if style_match:
        style = style_match.group(1)
        save_identity("assistant_style", style, db_path=db_path)
        save_memory(f"用户希望助手回答风格更{style}", "preference", importance=3, db_path=db_path)
        return f"好的，我会尽量用更{style}的方式回答。"

    language_match = re.search(r"记住[，,]?\s*我喜欢你用(.+?)回答", text)
    if language_match:
        language = language_match.group(1).strip(" ，,。.!！")
        save_identity("language", language, db_path=db_path)
        save_memory(f"用户喜欢助手用{language}回答", "preference", importance=3, db_path=db_path)
        return f"好的，我会记住你喜欢我用{language}回答。"

    return None


def _extract_memory(text: str) -> str | None:
    match = re.search(r"记住[，,]?\s*(.+)$", text)
    if not match:
        return None
    content = match.group(1).strip(" ，,。.!！")
    if not content:
        return None
    if "我喜欢你用" in content and "回答" in content:
        return None
    return content


def _extract_new_task(text: str) -> str | None:
    patterns = [
        r"(?:帮我)?(?:添加|新增|记录)(?:一个)?任务[:：]?\s*(.+)$",
        r"记(?:一个)?任务[:：]?\s*(.+)$",
        r"待办[:：]\s*(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _clean_task_title(match.group(1))
    return None


def _extract_done_task_id(text: str) -> int | None:
    match = re.search(r"(?:完成|标记完成|做完).*?#?(\d+)", text)
    if not match:
        return None
    return int(match.group(1))


def _asks_today_tasks(text: str) -> bool:
    return "今天" in text and "任务" in text and any(word in text for word in ("什么", "哪些", "列表", "有什么", "查询"))


def _asks_today_summary(text: str) -> bool:
    return "今天" in text and any(word in text for word in ("总结", "摘要", "汇总"))


def _clean_task_title(title: str) -> str:
    return title.strip(" ，,。.!！")


def _tokenize(text: str) -> list[str]:
    ascii_tokens = re.findall(r"[a-zA-Z0-9_]+", text.lower())
    chinese_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    return ascii_tokens + chinese_chunks


def _optional_profile_string(profile: dict[str, Any], key: str) -> str | None:
    value = profile.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _save_profile_list(
    profile: dict[str, Any],
    source_key: str,
    identity_key: str,
    memory_type: str,
    *,
    db_path: str | Path | None,
) -> None:
    values = profile.get(source_key)
    if not isinstance(values, list):
        return

    items = [str(item).strip() for item in values if str(item).strip()]
    if not items:
        return

    save_identity(identity_key, "\n".join(items), db_path=db_path)
    for item in items:
        save_memory(item, memory_type, importance=2, db_path=db_path)
