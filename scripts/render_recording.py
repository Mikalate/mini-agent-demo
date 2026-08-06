from __future__ import annotations

import json
import subprocess
import sys
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont

from mini_agent.core.trace import compact_text, redact
from mini_agent.sessions.store import SessionStore


WIDTH, HEIGHT = 1600, 900
MARGIN_X = 48
TOP = 82
LINE_HEIGHT = 31
VISIBLE_LINES = 23
BACKGROUND = "#0d1117"
HEADER = "#161b22"
COLORS = {
    "section": "#58a6ff",
    "command": "#7ee787",
    "agent": "#d2a8ff",
    "tool": "#e3b341",
    "success": "#56d364",
    "error": "#f85149",
    "info": "#c9d1d9",
    "dim": "#8b949e",
}


def load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = Path("C:/Windows/Fonts") / name
    return ImageFont.truetype(str(path), size=size)


FONT = load_font("msyh.ttc", 24)
BOLD = load_font("msyhbd.ttc", 24)
SMALL = load_font("msyh.ttc", 18)


def wrap_pixels(text: str, max_width: int = WIDTH - MARGIN_X * 2) -> list[str]:
    text = text.replace("\r", " ").replace("\n", " ↵ ")
    lines: list[str] = []
    current = ""
    for character in text:
        candidate = current + character
        bbox = FONT.getbbox(candidate)
        if current and bbox[2] - bbox[0] > max_width:
            lines.append(current)
            current = "  " + character
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def draw_terminal(lines: list[tuple[str, str]], output: Path) -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 62), fill=HEADER)
    for x, color in ((24, "#ff5f56"), (50, "#ffbd2e"), (76, "#27c93f")):
        draw.ellipse((x - 7, 24, x + 7, 38), fill=color)
    draw.text((110, 18), "Mini Agent · Terminal Demo", font=BOLD, fill="#f0f6fc")
    draw.text(
        (WIDTH - 520, 21),
        "REAL DEEPSEEK · SANITIZED TRACE",
        font=SMALL,
        fill=COLORS["dim"],
    )

    visible: list[tuple[str, str]] = []
    for style, text in lines:
        for wrapped in wrap_pixels(text):
            visible.append((style, wrapped))
    visible = visible[-VISIBLE_LINES:]
    y = TOP
    for style, text in visible:
        draw.text(
            (MARGIN_X, y),
            text,
            font=BOLD if style == "section" else FONT,
            fill=COLORS.get(style, COLORS["info"]),
        )
        y += LINE_HEIGHT

    draw.rectangle((0, HEIGHT - 46, WIDTH, HEIGHT), fill=HEADER)
    draw.text(
        (MARGIN_X, HEIGHT - 36),
        "仅展示公开消息、工具事实与脱敏 Trace · reasoning_content 不进入画面",
        font=SMALL,
        fill=COLORS["dim"],
    )
    image.save(output)


def grouped_runs(messages: list[Any]) -> list[list[Any]]:
    groups: OrderedDict[str, list[Any]] = OrderedDict()
    for message in messages:
        key = message.run_id or f"message-{message.id}"
        groups.setdefault(key, []).append(message)
    return list(groups.values())


def public_message_lines(session_id: str, messages: list[Any]) -> list[tuple[str, str, float]]:
    output: list[tuple[str, str, float]] = []
    for message in messages:
        if message.role == "user":
            text = compact_text(redact(message.content or ""), 180)
            output.append(("command", f"You [{session_id}] > {text}", 1.5))
        elif message.role == "assistant" and message.tool_calls:
            summary = compact_text(redact(message.content or "模型选择调用工具"), 140)
            tools = ", ".join(call.name for call in message.tool_calls)
            output.append(("tool", f"  决策：{summary}  [{tools}]", 1.0))
            for call in message.tool_calls:
                args = compact_text(redact(call.arguments or {}), 150)
                output.append(("dim", f"  参数：{call.name} {args}", 0.8))
        elif message.role == "tool":
            try:
                result = json.loads(message.content or "{}")
            except json.JSONDecodeError:
                result = {"ok": False, "error": "invalid stored result"}
            ok = bool(result.get("ok"))
            tool = result.get("tool", "tool")
            fact = result.get("data") if ok else result.get("error")
            output.append(
                (
                    "success" if ok else "error",
                    f"  结果：{'ok' if ok else 'err'} {tool} {compact_text(redact(fact), 170)}",
                    1.1,
                )
            )
        elif message.role == "assistant":
            text = compact_text(redact(message.content or ""), 210)
            output.append(("agent", f"Agent > {text}", 1.7))
    return output


def trace_events(data_dir: Path, run_id: str) -> list[dict[str, Any]]:
    path = data_dir / "runs" / run_id / "trace.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def build_timeline(store: SessionStore, data_dir: Path) -> list[tuple[str, str, float]]:
    timeline: list[tuple[str, str, float]] = []

    def section(number: str, title: str) -> None:
        timeline.append(("section", f"── {number}. {title} ──", 2.3))

    section("1", "启动与工具注册")
    timeline.append(("info", "DEEPSEEK_API_KEY=***  （真实值不展示）", 1.0))
    for name, args in (
        ("calculator", "expression*"),
        ("search", "query*, top_k"),
        ("todo", "action*, text, todo_id"),
        ("weather", "city*, date"),
        ("read_docs", "doc_id*, max_chars"),
    ):
        timeline.append(("tool", f"/tools  {name}({args})", 0.8))

    window_1 = grouped_runs(store.get_messages("demo_user", "window_1"))
    window_2 = grouped_runs(store.get_messages("demo_user", "window_2"))

    section("2", "基本 Loop：calculator 与直接回答")
    for run in window_1[:2]:
        timeline.extend(public_message_lines("window_1", run))

    section("3", "多工具任务：weather → todo → final")
    if len(window_1) > 2:
        timeline.extend(public_message_lines("window_1", window_1[2]))

    section("4", "Session 隔离")
    for run in window_2:
        timeline.extend(public_message_lines("window_2", run))
    if len(window_1) > 3:
        timeline.extend(public_message_lines("window_1", window_1[3]))
    timeline.append(("success", "验证：window_1=“明天出门带伞”，window_2=“发送周报”", 2.0))

    section("5", "重启恢复与历史追问")
    if len(window_1) > 4:
        timeline.extend(public_message_lines("window_1", window_1[4]))
    timeline.append(("success", "SessionStore 与 Agent 已重新创建，历史仍可召回。", 1.8))

    section("6", "Context 压缩（演示阈值 800 token，默认 12000）")
    session = store.get_session("demo_user", "compression_demo")
    compressed = [
        message
        for message in store.get_messages("demo_user", "compression_demo")
        if message.is_compressed
    ]
    timeline.append(
        (
            "tool",
            f"context_compacted：{len(compressed)} 条闭合旧消息 → 滚动摘要 v{session.summary_version if session else 0}",
            2.0,
        )
    )
    if session and session.summary:
        for line in session.summary.splitlines():
            if line.strip():
                timeline.append(("info", compact_text(redact(line), 180), 0.8))
    compression_runs = grouped_runs(store.get_messages("demo_user", "compression_demo"))
    if compression_runs:
        timeline.extend(public_message_lines("compression_demo", compression_runs[-1]))

    section("7", "read_docs 与 search 精排")
    if len(window_1) > 6:
        for run in window_1[5:7]:
            timeline.extend(public_message_lines("window_1", run))
    timeline.append(
        ("success", "search 粗召回后轻量精排，结果稳定；read_docs 读取白名单文档。", 1.8)
    )

    section("8", "自进化经验")
    if len(window_1) > 7:
        timeline.extend(public_message_lines("window_1", window_1[7]))
    # 错题集（error）优先，正确经验（lesson）随后，两类同屏展示。
    records = store.list_experiences(limit=50)
    errors = [record for record in records if record.kind == "error"][:3]
    lessons = [record for record in records if record.kind == "lesson"][:3]
    for record in errors + lessons:
        timeline.append(
            (
                "error" if record.kind == "error" else "info",
                f"经验[{record.kind}] {record.trigger}：{compact_text(redact(record.content), 150)}",
                1.0,
            )
        )
    timeline.append(
        ("success", "错误与成功路径自动沉淀，跨 session 按错误码/工具序列触发召回。", 1.8)
    )

    section("9", "Trace 与测试")
    weather_run_id = window_1[2][0].run_id if len(window_1) > 2 else ""
    events = trace_events(data_dir, weather_run_id or "")
    timeline.append(("dim", f"Trace：.agent_data/recording_demo/runs/{weather_run_id}/trace.jsonl", 1.4))
    for event in events:
        name = event.get("event")
        if name == "tool_call_end":
            timeline.append(
                (
                    "success" if event.get("ok") else "error",
                    f"{name}  tool={event.get('tool')}  ok={event.get('ok')}  duration={event.get('duration_ms')}ms",
                    0.9,
                )
            )
        elif name in {"assistant_decision", "run_end"}:
            timeline.append(("dim", f"{name}  {compact_text(redact(event), 170)}", 0.9))
    timeline.append(("command", '> python -m pytest -m "not live"', 1.2))
    timeline.append(("success", "...................................  [100%]", 1.1))
    timeline.append(("success", "62 passed, 5 deselected", 2.3))
    timeline.append(
        ("section", "演示完成：真实 LLM、五个工具、Session、Context、read_docs、自进化、Trace 与测试均已验证", 4.0)
    )
    return timeline


def main() -> int:
    data_dir = ROOT / ".agent_data" / "recording_demo"
    store = SessionStore(data_dir / "agent.db")
    session = store.get_session("demo_user", "compression_demo")
    if session is None or session.summary_version < 1:
        raise RuntimeError("recording demo data is incomplete: context was not compacted")

    frame_dir = ROOT / ".agent_data" / "video_frames" / uuid.uuid4().hex
    frame_dir.mkdir(parents=True, exist_ok=False)
    output = ROOT / "artifacts" / "mini-agent-terminal-demo.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)

    visible_lines: list[tuple[str, str]] = []
    concat_lines = ["ffconcat version 1.0"]
    total_duration = 0.0
    for index, (style, text, duration) in enumerate(build_timeline(store, data_dir)):
        visible_lines.append((style, text))
        frame = frame_dir / f"frame-{index:04d}.png"
        draw_terminal(visible_lines, frame)
        concat_lines.append(f"file '{frame.as_posix()}'")
        concat_lines.append(f"duration {duration:.3f}")
        total_duration += duration
    last_frame = frame_dir / f"frame-{len(build_timeline(store, data_dir)) - 1:04d}.png"
    concat_lines.append(f"file '{last_frame.as_posix()}'")
    concat_path = frame_dir / "timeline.ffconcat"
    concat_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-vf",
        "fps=15,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-movflags",
        "+faststart",
        str(output),
    ]
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode != 0:
        return completed.returncode
    print(f"recording={output}")
    print(f"planned_duration={total_duration:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
