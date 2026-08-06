from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mini_agent.config import ConfigError, Settings
from mini_agent.core.agent import Agent
from mini_agent.core.trace import PlainRenderer, RichRenderer, TraceEvent, TraceSink, compact_text
from mini_agent.llm.deepseek import DeepSeekClient
from mini_agent.sessions import SessionStore
from mini_agent.tools.registry import ToolRegistry, build_default_registry


HELP_TEXT = """可用命令：
  /help                   显示本帮助
  /tools                  显示已注册工具和参数摘要
  /sessions               列出当前用户的 sessions
  /new <session>          新建并切换 session
  /switch <session>       切换到已有 session
  /history [n]            查看当前 session 最近 n 条消息
  /context                查看上下文统计和摘要状态
  /trace                  显示最近一次 run 的 trace 路径和摘要
  /reset                  二次确认后重置当前 session
  /exit                   安全退出"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mini-agent", description="最小可用 Agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    chat = subparsers.add_parser("chat", help="进入交互式 Agent 会话")
    chat.add_argument("--user", required=True, help="用户标识")
    chat.add_argument("--session", required=True, help="session 标识")
    chat.add_argument("--no-color", action="store_true", help="使用纯文本输出")

    sessions = subparsers.add_parser("sessions", help="列出指定用户的 sessions")
    sessions.add_argument("--user", required=True, help="用户标识")

    history = subparsers.add_parser("history", help="查看 session 消息历史")
    history.add_argument("--user", required=True, help="用户标识")
    history.add_argument("--session", required=True, help="session 标识")
    history.add_argument("--limit", type=int, default=20, help="显示最近多少条消息")

    trace = subparsers.add_parser("trace", help="查看最近一次运行记录")
    trace.add_argument("--user", required=True, help="用户标识")
    trace.add_argument("--session", required=True, help="session 标识")
    trace.add_argument("--no-color", action="store_true", help="使用纯文本输出")

    return parser


def _renderer(no_color: bool) -> TraceSink:
    return PlainRenderer() if no_color else RichRenderer()


def _show_sessions(store: SessionStore, user_id: str, active: str | None = None) -> None:
    sessions = store.list_sessions(user_id)
    if not sessions:
        print("暂无 session。")
        return
    for session in sessions:
        marker = "*" if session.session_id == active else " "
        print(f"{marker} {session.session_id}\t最后活跃：{session.last_active_at.isoformat()}")


def _show_history(
    store: SessionStore, user_id: str, session_id: str, limit: int = 20
) -> None:
    messages = store.get_messages(user_id, session_id, limit=limit)
    if not messages:
        print("暂无消息。")
        return
    for message in messages:
        suffix = " [已压缩]" if message.is_compressed else ""
        if message.role == "assistant" and message.tool_calls:
            tools = ", ".join(call.name for call in message.tool_calls)
            content = compact_text(message.content or f"调用工具：{tools}", 300)
        elif message.role == "tool":
            content = compact_text(message.content or "", 300)
        else:
            content = compact_text(message.content or "", 500)
        print(f"[{message.role}]{suffix} {content}")


def _trace_path(settings: Settings, run_id: str) -> Path:
    return settings.data_dir / "runs" / run_id / "trace.jsonl"


def _show_trace(
    store: SessionStore,
    settings: Settings,
    user_id: str,
    session_id: str,
    renderer: TraceSink,
) -> None:
    run_id = store.latest_run_id(user_id, session_id)
    if run_id is None:
        print("当前 session 暂无 run。")
        return
    path = _trace_path(settings, run_id)
    print(f"trace：{path}")
    if not path.is_file():
        print("trace 文件不存在或尚未写入。")
        return
    events: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    for item in events[-100:]:
        payload = dict(item)
        event_name = str(payload.pop("event", "unknown"))
        event_run_id = str(payload.pop("run_id", run_id))
        timestamp = str(payload.pop("timestamp", ""))
        renderer.emit(
            TraceEvent(
                event=event_name,
                run_id=event_run_id,
                timestamp=timestamp,
                data=payload,
            )
        )


def _show_context(agent: Agent, user_id: str, session_id: str) -> None:
    session = agent.store.get_session(user_id, session_id)
    if session is None:
        print("当前 session 不存在。")
        return
    messages = agent.context.build(user_id, session_id)
    tokens = agent.context.serialized_tokens(messages)
    uncompressed = agent.store.get_messages(
        user_id, session_id, include_compressed=False
    )
    print(
        f"messages={len(messages)}（含 system），未压缩历史={len(uncompressed)}，"
        f"序列化 token≈{tokens}/{agent.settings.max_context_tokens}"
    )
    if session.summary:
        print(f"滚动摘要 v{session.summary_version}：{compact_text(session.summary, 600)}")
    else:
        print("滚动摘要：尚未生成。")


def _show_tools(registry: ToolRegistry) -> None:
    for spec in registry.list_specs():
        properties = spec.parameters.get("properties", {})
        required = set(spec.parameters.get("required", []))
        parts = [f"{name}{'*' if name in required else ''}" for name in properties]
        print(f"{spec.name}({', '.join(parts)})：{spec.description}")


async def _handle_command(
    raw: str,
    *,
    user_id: str,
    session_id: str,
    agent: Agent,
    store: SessionStore,
    registry: ToolRegistry,
    settings: Settings,
    renderer: TraceSink,
) -> tuple[bool, str]:
    command, _, argument = raw.partition(" ")
    argument = argument.strip()

    if command == "/help":
        print(HELP_TEXT)
    elif command == "/tools":
        _show_tools(registry)
    elif command == "/sessions":
        _show_sessions(store, user_id, session_id)
    elif command == "/new":
        new_session = argument or f"session-{uuid.uuid4().hex[:8]}"
        store.create_session(user_id, new_session)
        print(f"已新建并切换到 session：{new_session}")
        session_id = new_session
    elif command == "/switch":
        if not argument:
            print("用法：/switch <session>")
        elif store.get_session(user_id, argument) is None:
            print(f"当前用户不存在 session：{argument}；请先使用 /new {argument}")
        else:
            session_id = argument
            print(f"已切换到 session：{session_id}")
    elif command == "/history":
        try:
            limit = int(argument) if argument else 20
            if limit < 1:
                raise ValueError
        except ValueError:
            print("用法：/history [正整数]")
        else:
            _show_history(store, user_id, session_id, limit)
    elif command == "/context":
        _show_context(agent, user_id, session_id)
    elif command == "/trace":
        _show_trace(store, settings, user_id, session_id, renderer)
    elif command == "/reset":
        try:
            confirmation = input(
                f"此操作只会清空 {user_id}/{session_id}。请输入 session 名确认："
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消重置。")
        else:
            if confirmation != session_id:
                print("确认内容不匹配，已取消重置。")
            else:
                store.reset_session(user_id, session_id)
                print(f"已重置当前 session：{session_id}")
    elif command == "/exit":
        return False, session_id
    else:
        print("未知命令。输入 /help 查看可用命令。")
    return True, session_id


async def _chat(args: argparse.Namespace, settings: Settings, store: SessionStore) -> int:
    renderer = _renderer(args.no_color)
    registry = build_default_registry()
    agent = Agent(
        settings=settings,
        llm=DeepSeekClient(settings),
        registry=registry,
        store=store,
        trace_renderer=renderer,
    )
    active_session = args.session
    store.create_session(args.user, active_session)
    print(
        f"Mini Agent · user={args.user} · session={active_session} · "
        f"model={settings.deepseek_model}"
    )
    print("已恢复 session。输入 /help 查看命令，/exit 退出。")
    while True:
        try:
            user_text = input(f"You [{active_session}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已安全退出。")
            return 0
        if not user_text:
            continue
        if user_text.startswith("/"):
            keep_running, active_session = await _handle_command(
                user_text,
                user_id=args.user,
                session_id=active_session,
                agent=agent,
                store=store,
                registry=registry,
                settings=settings,
                renderer=renderer,
            )
            if not keep_running:
                print("已安全退出。")
                return 0
            continue
        result = await agent.run_turn(args.user, active_session, user_text)
        print(f"Agent > {result.content}")
        if result.status == "interrupted":
            return 130


def _run(args: argparse.Namespace, settings: Settings) -> int:
    store = SessionStore(settings.database_path)

    if args.command == "chat":
        return asyncio.run(_chat(args, settings, store))
    if args.command == "sessions":
        _show_sessions(store, args.user)
        return 0
    if args.command == "history":
        _show_history(store, args.user, args.session, args.limit)
        return 0
    if args.command == "trace":
        _show_trace(
            store,
            settings,
            args.user,
            args.session,
            _renderer(args.no_color),
        )
        return 0
    raise AssertionError(f"未处理的命令：{args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
        return _run(args, settings)
    except ConfigError as exc:
        parser.exit(2, f"配置错误：{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
