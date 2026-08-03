from pathlib import Path

from mini_agent.core.models import Message
from mini_agent.sessions.store import SessionStore


def test_sessions_are_isolated_and_survive_reopen(tmp_path: Path) -> None:
    database = tmp_path / "agent.db"
    store = SessionStore(database)
    store.create_session("user_a", "window_1")
    store.create_session("user_a", "window_2")
    store.append_message("user_a", "window_1", Message(role="user", content="消息一"))
    store.append_message("user_a", "window_2", Message(role="user", content="消息二"))

    reopened = SessionStore(database)
    assert [message.content for message in reopened.get_messages("user_a", "window_1")] == [
        "消息一"
    ]
    assert [message.content for message in reopened.get_messages("user_a", "window_2")] == [
        "消息二"
    ]
    assert len(reopened.list_sessions("user_a")) == 2


def test_same_session_name_is_isolated_between_users(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "agent.db")
    store.append_message("user_a", "shared", Message(role="user", content="A"))
    store.append_message("user_b", "shared", Message(role="user", content="B"))

    assert store.get_messages("user_a", "shared")[0].content == "A"
    assert store.get_messages("user_b", "shared")[0].content == "B"


def test_two_connections_alternate_writes_and_reset_is_scoped(tmp_path: Path) -> None:
    database = tmp_path / "agent.db"
    first = SessionStore(database)
    second = SessionStore(database)
    first.append_message("user", "one", Message(role="user", content="one-a"))
    second.append_message("user", "two", Message(role="user", content="two-a"))
    second.append_message("user", "one", Message(role="assistant", content="one-b"))
    first.append_message("user", "two", Message(role="assistant", content="two-b"))
    first.add_todo("user", "one", "保留范围测试")
    second.add_todo("user", "two", "不应删除")

    assert second.reset_session("user", "one")
    assert first.get_messages("user", "one") == []
    assert first.list_todos("user", "one") == []
    assert [message.content for message in first.get_messages("user", "two")] == [
        "two-a",
        "two-b",
    ]
    assert [todo.text for todo in first.list_todos("user", "two")] == ["不应删除"]
