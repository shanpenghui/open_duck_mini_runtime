# Memory and Task Module

This module adds local identity, long-term memory, conversation context, and
basic tasks to the Raspberry Pi voice assistant server.

## Storage

The module uses SQLite from the Python standard library.

Default database path:

```text
data/assistant.db
```

The database is created automatically on first use. The schema lives in:

```text
memory/schema.sql
```

## Default Assistant Identity

The default identity profile lives in:

```text
memory/default_profile.json
```

Current profile:

```json
{
  "name": "小派",
  "role": "私人语音智能体",
  "personality": [
    "说中文",
    "简短直接",
    "像长期陪伴型助手",
    "不假装已经做了没做的事"
  ],
  "boundaries": [
    "不执行危险命令",
    "删除、付款、发消息前必须确认"
  ],
  "memory_style": [
    "记住用户偏好",
    "记住项目进度",
    "记住常用设备和路径"
  ]
}
```

Seed it into SQLite:

```bash
python tools/seed_profile.py
```

After seeding, normal user interaction can still update the identity. For
example, saying `以后你叫小莓` overwrites the assistant name in SQLite.

## Public API

The WebSocket service should keep using the existing inference boundary:

```python
generate_reply(text: str, session_id: str) -> str
```

Inside that boundary, `server/inference.py` uses:

```python
from memory.service import (
    append_conversation,
    build_prompt,
    process_direct_command,
)
```

Main memory APIs:

```python
save_identity(key, value)
get_identity_prompt()
save_memory(content, type="fact", importance=1)
get_relevant_memories(text, limit=8)
append_conversation(session_id, role, content)
get_recent_conversation(session_id, limit=10)
add_task(title, due_date=None, priority="normal")
list_today_tasks()
mark_task_done(task_id)
build_prompt(session_id, user_text)
apply_profile(profile)
apply_profile_file(profile_path)
```

## First-Stage Behavior

Clear identity, memory, and task commands are handled locally before calling the
model. This makes persistence easy to verify:

```text
以后你叫小莓
记住，我喜欢简短回答
帮我添加任务：晚上整理项目文档
今天有什么任务
```

Normal chat still goes through the local Gemma/llama-server path. Before that
call, `build_prompt()` injects:

1. Assistant identity and style.
2. Relevant long-term memories.
3. Recent conversation messages.
4. The current user input.

## Manual Verification

Run from the project root:

```bash
python tools/memory_smoke_test.py
```

Expected checks:

- seeding `memory/default_profile.json` persists 小派's default identity;
- saving `以后你叫小莓` persists an updated assistant name;
- asking `你叫什么` builds a prompt that includes the saved name;
- adding a task stores it in SQLite;
- querying today's tasks returns the stored task.

## Raspberry Pi Service Integration

Start `llama-server` first, then start the WebSocket service as before:

```bash
uvicorn server.app:app --host 0.0.0.0 --port 8765
```

No Android protocol change is required. The current Android flat request shape
continues to work.
