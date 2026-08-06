"""离线从 run Trace 挖掘经验种子（错误模式）并输出为 JSON。

用法：
    python -m scripts.extract_experiences .agent_data/runs --output data/experience_mined.json

只读取脱敏后的 trace.jsonl，绝不读取 SQLite 或消息正文。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def extract_errors(trace_dir: Path) -> list[dict[str, Any]]:
    """Collect error patterns from run traces, deduplicated by trigger."""
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for trace_path in sorted(trace_dir.glob("*/trace.jsonl")):
        run_id = trace_path.parent.name
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            event_name = event.get("event")
            if event_name == "tool_call_end":
                error_code = event.get("error_code")
                tool = event.get("tool")
                if error_code and tool:
                    trigger = f"err:{error_code}"
                    if trigger not in seen:
                        seen.add(trigger)
                        errors.append(
                            {
                                "kind": "error",
                                "trigger": trigger,
                                "content": (
                                    f"失败模式：工具 {tool} 返回错误 {error_code}；"
                                    "先确认参数或工具边界再重试。"
                                ),
                                "source_run_id": run_id,
                            }
                        )
            elif event_name == "run_end":
                code = event.get("error_code")
                if code and code not in {"INTERRUPTED", "SESSION_STORE_FAILED"}:
                    trigger = f"err:{code}"
                    if trigger not in seen:
                        seen.add(trigger)
                        errors.append(
                            {
                                "kind": "error",
                                "trigger": trigger,
                                "content": (
                                    f"失败模式：任务以 {code} 终止；"
                                    "避免重复相同调用组合，拆分任务或调整参数后重试。"
                                ),
                                "source_run_id": run_id,
                            }
                        )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 Trace 挖掘经验种子")
    parser.add_argument("trace_dir", type=Path, help=".agent_data/runs 目录")
    parser.add_argument(
        "--output", type=Path, default=None, help="输出 JSON 路径（默认打印到 stdout）"
    )
    args = parser.parse_args(argv)
    errors = extract_errors(args.trace_dir)
    payload = json.dumps(errors, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
