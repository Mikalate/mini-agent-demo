from __future__ import annotations

import hashlib
import json
import logging
from importlib.resources import files

from mini_agent.core.models import ExperienceRecord, RunState
from mini_agent.core.trace import compact_text
from mini_agent.sessions.store import SessionStore

LOGGER = logging.getLogger(__name__)
_SEED_FILE = "experience_seeds.json"


class ExperienceManager:
    """Cross-session lesson/error memory with trigger-based recall.

    Reads inject matched experiences as a low-priority system segment before a
    run; writes distill run outcomes automatically (the model never writes its
    own experience, to avoid hallucinations).
    """

    def __init__(self, store: SessionStore):
        self.store = store
        self._load_seeds()

    def _load_seeds(self) -> int:
        try:
            seeds = json.loads(
                files("data").joinpath(_SEED_FILE).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            LOGGER.exception("experience 种子加载失败")
            return 0
        inserted = 0
        for seed in seeds:
            trigger = seed["trigger"]
            if self.store.get_experiences([trigger], limit=1):
                continue
            self.store.upsert_experience(
                ExperienceRecord(
                    id=self._record_id(seed["kind"], trigger),
                    kind=seed["kind"],
                    trigger=trigger,
                    content=seed["content"],
                    source_run_id="seed",
                )
            )
            inserted += 1
        return inserted

    @staticmethod
    def _record_id(kind: str, trigger: str) -> str:
        digest = hashlib.sha256(f"{kind}:{trigger}".encode("utf-8")).hexdigest()
        return digest[:16]

    def read(self, user_id: str, session_id: str) -> list[ExperienceRecord]:
        """Recall experiences matching the user's recent failure/tool path.

        The experience store is shared globally per user: a lesson learned in
        one session is recalled in another, matching the "error book / best
        practices" design.
        """
        del session_id
        triggers: list[str] = []
        error = self.store.latest_run_error(user_id)
        if error:
            triggers.append(f"err:{error}")
        tool_names = self.store.recent_tool_names(user_id)
        if tool_names:
            triggers.append("seq:" + "→".join(tool_names))
        records = self.store.get_experiences(triggers, limit=6)
        if records:
            self.store.increment_experience_hit([record.id for record in records])
        return records

    def write(
        self,
        state: RunState,
        *,
        status: str,
        error_code: str | None = None,
        content: str = "",
    ) -> list[ExperienceRecord]:
        """Distill a finished run into lessons/errors (upsert, deduplicated)."""
        tool_names = self.store.recent_tool_names(
            state.user_id, state.session_id, limit=8
        )
        candidates: list[ExperienceRecord] = []
        if status == "completed" and tool_names:
            trigger = "seq:" + "→".join(tool_names)
            candidates.append(
                ExperienceRecord(
                    id=self._record_id("lesson", trigger),
                    kind="lesson",
                    trigger=trigger,
                    content=(
                        f"成功路径：此类任务使用 {', '.join(tool_names)} 完成；"
                        "保持该调用顺序与参数形态可提高成功率。"
                    ),
                    source_run_id=state.run_id,
                )
            )
        if status == "incomplete" and error_code:
            trigger = f"err:{error_code}"
            hint = compact_text(content, 160)
            candidates.append(
                ExperienceRecord(
                    id=self._record_id("error", trigger),
                    kind="error",
                    trigger=trigger,
                    content=(
                        f"失败模式：任务以 {error_code} 终止（{hint}）。"
                        "先确认参数或工具边界，或拆分任务后重试，避免重复相同调用组合。"
                    ),
                    source_run_id=state.run_id,
                )
            )
        return [self.store.upsert_experience(record) for record in candidates]

    @staticmethod
    def format_system_segment(records: list[ExperienceRecord]) -> str:
        if not records:
            return ""
        lines = "\n".join(f"- {record.content}" for record in records)
        return (
            "以下是与当前任务相关的历史经验，仅作为低优先级参考；"
            "与最新消息或实时工具结果冲突时以后者为准。\n\n" + lines
        )
