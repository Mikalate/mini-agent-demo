from __future__ import annotations

from typing import Any

from mini_agent.core.models import TodoRecord
from mini_agent.tools.base import ToolContext, ToolFailure, ToolResult, ToolSpec


def _serialize(todo: TodoRecord) -> dict[str, Any]:
    return {
        "id": todo.id,
        "text": todo.text,
        "status": todo.status,
        "created_at": todo.created_at.isoformat(),
        "updated_at": todo.updated_at.isoformat(),
    }


def _validate(arguments: dict[str, Any]) -> None:
    action = arguments["action"]
    has_text = "text" in arguments
    has_id = "todo_id" in arguments
    if action == "add" and (not has_text or not arguments["text"].strip() or has_id):
        raise ToolFailure("SCHEMA_VALIDATION_FAILED", "add 必须提供非空 text，且不能提供 todo_id。")
    if action == "list" and (has_text or has_id):
        raise ToolFailure("SCHEMA_VALIDATION_FAILED", "list 不能提供 text 或 todo_id。")
    if action in {"complete", "delete"} and (not has_id or has_text):
        raise ToolFailure(
            "SCHEMA_VALIDATION_FAILED", f"{action} 必须提供 todo_id，且不能提供 text。"
        )


async def _todo(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    action = arguments["action"]
    if action == "add":
        todo = context.store.add_todo(context.user_id, context.session_id, arguments["text"])
        data: Any = {"todo": _serialize(todo)}
    elif action == "list":
        todos = context.store.list_todos(context.user_id, context.session_id)
        data = {"todos": [_serialize(todo) for todo in todos], "count": len(todos)}
    elif action == "complete":
        todo = context.store.complete_todo(
            context.user_id, context.session_id, arguments["todo_id"]
        )
        if todo is None:
            raise ToolFailure("TODO_NOT_FOUND", "当前 session 中不存在该待办。")
        data = {"todo": _serialize(todo)}
    else:
        deleted = context.store.delete_todo(
            context.user_id, context.session_id, arguments["todo_id"]
        )
        if not deleted:
            raise ToolFailure("TODO_NOT_FOUND", "当前 session 中不存在该待办。")
        data = {"deleted": True, "todo_id": arguments["todo_id"]}
    return ToolResult(ok=True, tool="todo", data=data)


def todo_spec() -> ToolSpec:
    return ToolSpec(
        name="todo",
        description="管理当前用户当前 session 的待办；身份由运行时注入，不能跨 session 操作。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "list", "complete", "delete"]},
                "text": {"type": "string", "minLength": 1, "maxLength": 500},
                "todo_id": {"type": "string", "minLength": 1, "maxLength": 64},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        handler=_todo,
        argument_validator=_validate,
    )

