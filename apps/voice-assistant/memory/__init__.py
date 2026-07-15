"""Local identity, memory, and task helpers for the voice assistant."""

from memory.service import (
    add_task,
    append_conversation,
    apply_profile,
    apply_profile_file,
    build_prompt,
    get_identity_prompt,
    get_recent_conversation,
    get_relevant_memories,
    list_today_tasks,
    mark_task_done,
    process_direct_command,
    save_identity,
    save_memory,
)

__all__ = [
    "add_task",
    "append_conversation",
    "apply_profile",
    "apply_profile_file",
    "build_prompt",
    "get_identity_prompt",
    "get_recent_conversation",
    "get_relevant_memories",
    "list_today_tasks",
    "mark_task_done",
    "process_direct_command",
    "save_identity",
    "save_memory",
]
