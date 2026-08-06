from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from mini_agent.config import Settings
from mini_agent.core.agent import Agent, RunResult
from mini_agent.core.trace import RichRenderer, TraceEvent
from mini_agent.llm.deepseek import DeepSeekClient
from mini_agent.sessions.store import SessionStore
from mini_agent.tools.registry import build_default_registry

console = Console()


def section(title: str, description: str) -> None:
    console.print()
    console.print(Panel(description, title=title, border_style="cyan"))
    time.sleep(1.2)


def build_agent(settings: Settings, store: SessionStore) -> Agent:
    return Agent(
        settings=settings,
        llm=DeepSeekClient(settings),
        registry=build_default_registry(),
        store=store,
        trace_renderer=RichRenderer(console),
    )


async def ask(
    agent: Agent, user_id: str, session_id: str, prompt: str
) -> RunResult:
    console.print(f"\n[bold green]You [{session_id}] >[/bold green] {prompt}")
    result = await agent.run_turn(user_id, session_id, prompt)
    console.print(f"[bold magenta]Agent >[/bold magenta] {result.content}")
    time.sleep(0.8)
    return result


def show_tools() -> None:
    registry = build_default_registry()
    table = Table(title="已注册工具", show_lines=True)
    table.add_column("工具", style="cyan")
    table.add_column("参数")
    table.add_column("说明")
    for spec in registry.list_specs():
        properties = spec.parameters.get("properties", {})
        required = set(spec.parameters.get("required", []))
        arguments = ", ".join(
            f"{name}{'*' if name in required else ''}" for name in properties
        )
        table.add_row(spec.name, arguments, spec.description)
    console.print(table)


def show_experiences(store: SessionStore) -> None:
    records = store.list_experiences(limit=10)
    table = Table(title="经验库（错题集 / 正确经验）", show_lines=True)
    table.add_column("类型", style="cyan")
    table.add_column("触发键")
    table.add_column("经验内容")
    for record in records:
        table.add_row(record.kind, record.trigger, record.content)
    console.print(table)


def show_context(store: SessionStore, user_id: str, session_id: str) -> None:
    session = store.get_session(user_id, session_id)
    messages = store.get_messages(user_id, session_id, include_compressed=False)
    assert session is not None
    console.print(
        f"[yellow]Context：未压缩消息 {len(messages)}，"
        f"滚动摘要 v{session.summary_version}[/yellow]"
    )
    if session.summary:
        console.print(Panel(session.summary, title="滚动摘要", border_style="yellow"))


def show_trace(settings: Settings, run_id: str) -> None:
    path = settings.data_dir / "runs" / run_id / "trace.jsonl"
    console.print(f"[cyan]Trace：{path}[/cyan]")
    renderer = RichRenderer(console)
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        if item.get("event") in {
            "assistant_decision",
            "tool_call_start",
            "tool_call_end",
            "context_compacted",
            "error",
            "run_end",
        }:
            events.append(item)
    for item in events[-10:]:
        payload = dict(item)
        event = payload.pop("event")
        event_run_id = payload.pop("run_id")
        timestamp = payload.pop("timestamp")
        renderer.emit(TraceEvent(event, event_run_id, timestamp, payload))


async def main() -> int:
    settings = Settings.from_env(ROOT / ".env")
    demo_data = settings.data_dir / "recording_demo"
    settings = replace(
        settings,
        data_dir=demo_data,
        database_path=demo_data / "agent.db",
    )
    store = SessionStore(settings.database_path)
    user_id = "demo_user"
    for session_id in ("window_1", "window_2", "compression_demo"):
        if store.get_session(user_id, session_id) is not None:
            store.reset_session(user_id, session_id)
    store.clear_experiences()  # 录屏从种子经验开始，避免残留旧数据

    console.print(
        Panel(
            f"user: {user_id}   model: {settings.deepseek_model}\n"
            "真实 DeepSeek API · 密钥不会显示",
            title="Mini Agent 终端演示",
            border_style="bold cyan",
        )
    )

    section("1. 启动与工具注册", "五个工具由统一 ToolRegistry 动态导出严格 Schema。")
    show_tools()

    agent = build_agent(settings, store)
    section("2. 基本 Loop", "对比 calculator 工具调用与无需工具的直接回答。")
    await ask(
        agent,
        user_id,
        "window_1",
        "请使用 calculator 计算 18*24+7，并根据真实工具结果回答。",
    )
    await ask(agent, user_id, "window_1", "不使用工具，简短回答：中国的首都是哪里？")

    section("3. 多工具任务", "真实展示 weather → todo → final。")
    weather_result = await ask(
        agent,
        user_id,
        "window_1",
        "先查询上海天气；如果建议带伞，就添加“明天出门带伞”待办，最后回答。",
    )

    section("4. Session 隔离", "同一用户的 window_1 与 window_2 拥有独立历史和待办。")
    await ask(
        agent,
        user_id,
        "window_2",
        "使用 todo 添加“发送周报”，然后列出当前待办。",
    )
    await ask(agent, user_id, "window_1", "使用 todo 列出我当前的待办。")
    await ask(agent, user_id, "window_2", "使用 todo 列出我当前的待办。")

    section("5. 恢复与追问", "重新创建 Store 和 Agent，再从 window_1 历史继续对话。")
    reopened_store = SessionStore(settings.database_path)
    reopened_agent = build_agent(settings, reopened_store)
    await ask(
        reopened_agent,
        user_id,
        "window_1",
        "根据当前 session 历史简短回答：刚才为什么建议带伞？",
    )

    section("6. Context 压缩", "演示配置临时降低 token 阈值；默认仍为 12000。")
    compact_settings = replace(
        settings,
        max_context_tokens=800,
        keep_recent_messages=4,
    )
    compact_agent = build_agent(compact_settings, reopened_store)
    facts = (
        "只回复“已记住”，不要调用工具。请记住：项目代号是晨星，"
        "交付偏好是回答简洁，并且尚未解决的事项是补充录屏说明。"
    )
    for index in range(1, 7):
        await ask(
            compact_agent,
            user_id,
            "compression_demo",
            f"第 {index} 次确认。{facts}" + ("这是用于触发字符预算的演示文本。" * 6),
        )
    show_context(reopened_store, user_id, "compression_demo")
    await ask(
        compact_agent,
        user_id,
        "compression_demo",
        "根据历史摘要回答：项目代号和未解决事项分别是什么？",
    )

    section("7. read_docs 与 search 精排", "read_docs 读取白名单文档；search 粗召回后轻量精排并返回摘要。")
    await ask(
        agent,
        user_id,
        "window_1",
        "请调用 search 搜索“context compression”，并说出第一条结果标题。",
    )
    await ask(
        agent,
        user_id,
        "window_1",
        "请调用 read_docs 读取 readme 文档，用一两句话概括它的用途。",
    )

    section(
        "8. 自进化经验",
        "失败模式与成功路径自动沉淀进经验库，按错误码/工具序列跨 session 触发召回。",
    )
    await ask(
        agent,
        user_id,
        "window_1",
        "请查询南京天气（演示数据中没有这个城市，按工具返回提示处理）。",
    )
    show_experiences(reopened_store)
    console.print(
        Panel(
            "说明：工具错误与成功路径在 run 结束后自动写入 experiences 表；"
            "后续任意 session 触发相同错误码或工具序列时，会在 context 中注入低优先级经验段。",
            title="自进化机制",
            border_style="green",
        )
    )

    section("9. Trace 与测试", "Trace 只展示公开事件；最后运行默认离线测试。")
    show_trace(settings, weather_result.state.run_id)
    console.print("\n[bold cyan]> python -m pytest -m \"not live\"[/bold cyan]")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "not live",
            "--basetemp",
            str(demo_data / "pytest_tmp"),
        ],
        cwd=ROOT,
        check=False,
    )
    if completed.returncode != 0:
        return completed.returncode
    console.print(
        Panel(
            "演示完成：真实 LLM、五个工具、多 session、恢复、Context、read_docs、"
            "自进化经验、Trace 与测试均已展示。",
            border_style="green",
        )
    )
    time.sleep(3)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
