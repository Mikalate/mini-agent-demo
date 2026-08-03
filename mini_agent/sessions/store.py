from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Iterator, Sequence

from mini_agent.core.models import Message, RunState, SessionRecord, TodoRecord, ToolCall


class SessionNotFoundError(LookupError):
    pass


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class SessionStore:
    """SQLite-backed session storage with strict user/session isolation."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        schema = files("mini_agent.sessions").joinpath("schema.sql").read_text(encoding="utf-8")
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(schema)

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> SessionRecord:
        return SessionRecord(
            id=row["id"],
            user_id=row["user_id"],
            session_id=row["session_id"],
            summary=row["summary"],
            summary_version=row["summary_version"],
            created_at=_parse_datetime(row["created_at"]),
            last_active_at=_parse_datetime(row["last_active_at"]),
        )

    @staticmethod
    def _ensure_session(
        connection: sqlite3.Connection, user_id: str, session_id: str
    ) -> sqlite3.Row:
        if not user_id.strip() or not session_id.strip():
            raise ValueError("user_id 和 session_id 不能为空。")
        connection.execute(
            """
            INSERT INTO sessions(user_id, session_id)
            VALUES (?, ?)
            ON CONFLICT(user_id, session_id) DO UPDATE SET
                last_active_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (user_id, session_id),
        )
        row = connection.execute(
            "SELECT * FROM sessions WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        ).fetchone()
        assert row is not None
        return row

    def create_session(self, user_id: str, session_id: str) -> SessionRecord:
        with self._connect() as connection:
            row = self._ensure_session(connection, user_id, session_id)
            return self._session_from_row(row)

    def get_session(self, user_id: str, session_id: str) -> SessionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE user_id = ? AND session_id = ?",
                (user_id, session_id),
            ).fetchone()
            return self._session_from_row(row) if row is not None else None

    def list_sessions(self, user_id: str) -> list[SessionRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sessions
                WHERE user_id = ?
                ORDER BY last_active_at DESC, id DESC
                """,
                (user_id,),
            ).fetchall()
            return [self._session_from_row(row) for row in rows]

    def reset_session(self, user_id: str, session_id: str) -> bool:
        """Clear only one isolated session while keeping its reusable identity."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM sessions WHERE user_id = ? AND session_id = ?",
                (user_id, session_id),
            ).fetchone()
            if row is None:
                return False
            session_pk = row["id"]
            connection.execute("DELETE FROM messages WHERE session_pk = ?", (session_pk,))
            connection.execute("DELETE FROM todos WHERE session_pk = ?", (session_pk,))
            connection.execute("DELETE FROM runs WHERE session_pk = ?", (session_pk,))
            connection.execute(
                """
                UPDATE sessions SET
                    summary = '', summary_version = 0,
                    last_active_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (session_pk,),
            )
            return True

    def latest_run_id(self, user_id: str, session_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT r.id FROM runs AS r
                JOIN sessions AS s ON s.id = r.session_pk
                WHERE s.user_id = ? AND s.session_id = ?
                ORDER BY r.started_at DESC, r.rowid DESC
                LIMIT 1
                """,
                (user_id, session_id),
            ).fetchone()
            return str(row["id"]) if row is not None else None

    def run_statuses(self, user_id: str, session_id: str) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT r.id, r.status FROM runs AS r
                JOIN sessions AS s ON s.id = r.session_pk
                WHERE s.user_id = ? AND s.session_id = ?
                """,
                (user_id, session_id),
            ).fetchall()
            return {str(row["id"]): str(row["status"]) for row in rows}

    def compact_messages(
        self,
        user_id: str,
        session_id: str,
        message_ids: Sequence[int],
        summary: str,
    ) -> int:
        """Atomically replace closed history with a rolling summary marker."""

        summary = summary.strip()
        ids = list(dict.fromkeys(message_ids))
        if not summary:
            raise ValueError("summary 不能为空。")
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM sessions WHERE user_id = ? AND session_id = ?",
                (user_id, session_id),
            ).fetchone()
            if row is None:
                raise SessionNotFoundError(f"session 不存在：{user_id}/{session_id}")
            session_pk = int(row["id"])
            cursor = connection.execute(
                f"""
                UPDATE messages SET is_compressed = 1
                WHERE session_pk = ? AND id IN ({placeholders})
                """,
                [session_pk, *ids],
            )
            if cursor.rowcount != len(ids):
                raise LookupError("待压缩消息不完整或不属于当前 session。")
            connection.execute(
                """
                UPDATE sessions SET
                    summary = ?, summary_version = summary_version + 1,
                    last_active_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (summary, session_pk),
            )
            return cursor.rowcount

    def append_message(self, user_id: str, session_id: str, message: Message) -> int:
        tool_calls_json = None
        if message.tool_calls:
            tool_calls_json = json.dumps(
                [
                    {
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "raw_arguments": call.raw_arguments,
                    }
                    for call in message.tool_calls
                ],
                ensure_ascii=False,
            )

        with self._connect() as connection:
            session_row = self._ensure_session(connection, user_id, session_id)
            cursor = connection.execute(
                """
                INSERT INTO messages(
                    session_pk, run_id, role, content, tool_calls_json,
                    tool_call_id, reasoning_content, is_compressed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_row["id"],
                    message.run_id,
                    message.role,
                    message.content,
                    tool_calls_json,
                    message.tool_call_id,
                    message.reasoning_content,
                    int(message.is_compressed),
                ),
            )
            connection.execute(
                """
                UPDATE sessions
                SET last_active_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (session_row["id"],),
            )
            return int(cursor.lastrowid)

    def get_messages(
        self,
        user_id: str,
        session_id: str,
        *,
        limit: int | None = None,
        include_compressed: bool = True,
    ) -> list[Message]:
        if limit is not None and limit < 1:
            raise ValueError("limit 必须大于 0。")

        conditions = ["s.user_id = ?", "s.session_id = ?"]
        parameters: list[object] = [user_id, session_id]
        if not include_compressed:
            conditions.append("m.is_compressed = 0")

        query = f"""
            SELECT m.* FROM messages AS m
            JOIN sessions AS s ON s.id = m.session_pk
            WHERE {' AND '.join(conditions)}
            ORDER BY m.id DESC
        """
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(limit)

        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()

        messages: list[Message] = []
        for row in reversed(rows):
            raw_calls = json.loads(row["tool_calls_json"]) if row["tool_calls_json"] else []
            messages.append(
                Message(
                    id=row["id"],
                    role=row["role"],
                    content=row["content"],
                    tool_calls=[ToolCall(**call) for call in raw_calls],
                    tool_call_id=row["tool_call_id"],
                    reasoning_content=row["reasoning_content"],
                    run_id=row["run_id"],
                    is_compressed=bool(row["is_compressed"]),
                    created_at=_parse_datetime(row["created_at"]),
                )
            )
        return messages

    def start_run(self, state: RunState) -> None:
        with self._connect() as connection:
            session_row = self._ensure_session(connection, state.user_id, state.session_id)
            connection.execute(
                "INSERT INTO runs(id, session_pk, status) VALUES (?, ?, ?)",
                (state.run_id, session_row["id"], state.status),
            )

    def finish_run(self, state: RunState, *, error: str | None = None) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE runs SET
                    ended_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    status = ?, rounds = ?, tool_steps = ?,
                    prompt_tokens = ?, completion_tokens = ?, total_tokens = ?, error = ?
                WHERE id = ?
                """,
                (
                    state.status,
                    state.round,
                    state.tool_step,
                    state.prompt_tokens,
                    state.completion_tokens,
                    state.total_tokens,
                    error,
                    state.run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"run 不存在：{state.run_id}")

    @staticmethod
    def _todo_from_row(row: sqlite3.Row) -> TodoRecord:
        return TodoRecord(
            id=row["id"],
            text=row["text"],
            status=row["status"],
            created_at=_parse_datetime(row["created_at"]),
            updated_at=_parse_datetime(row["updated_at"]),
        )

    def add_todo(self, user_id: str, session_id: str, text: str) -> TodoRecord:
        text = text.strip()
        if not text:
            raise ValueError("待办内容不能为空。")
        todo_id = uuid.uuid4().hex[:12]
        with self._connect() as connection:
            session_row = self._ensure_session(connection, user_id, session_id)
            connection.execute(
                "INSERT INTO todos(id, session_pk, text) VALUES (?, ?, ?)",
                (todo_id, session_row["id"], text),
            )
            row = connection.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
            assert row is not None
            return self._todo_from_row(row)

    def list_todos(
        self, user_id: str, session_id: str, *, status: str | None = None
    ) -> list[TodoRecord]:
        parameters: list[object] = [user_id, session_id]
        status_clause = ""
        if status is not None:
            if status not in {"pending", "done"}:
                raise ValueError("status 只能是 pending 或 done。")
            status_clause = " AND t.status = ?"
            parameters.append(status)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT t.* FROM todos AS t
                JOIN sessions AS s ON s.id = t.session_pk
                WHERE s.user_id = ? AND s.session_id = ?{status_clause}
                ORDER BY t.created_at, t.id
                """,
                parameters,
            ).fetchall()
            return [self._todo_from_row(row) for row in rows]

    def complete_todo(self, user_id: str, session_id: str, todo_id: str) -> TodoRecord | None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE todos SET
                    status = 'done',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND session_pk = (
                    SELECT id FROM sessions WHERE user_id = ? AND session_id = ?
                )
                """,
                (todo_id, user_id, session_id),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
            assert row is not None
            return self._todo_from_row(row)

    def delete_todo(self, user_id: str, session_id: str, todo_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM todos
                WHERE id = ? AND session_pk = (
                    SELECT id FROM sessions WHERE user_id = ? AND session_id = ?
                )
                """,
                (todo_id, user_id, session_id),
            )
            return cursor.rowcount == 1
