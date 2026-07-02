"""
意图识别 + 参数引导

1. 用 LLM 分析用户意图 → 映射到 RevitAction
2. 对比已提供 vs 缺失参数
3. 生成引导提示（中英双语）
4. 参数完整后生成结构化指令
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
import sqlite3
from typing import Any

from pipeline.llm_client import LLMClient
from prompts import load_prompt
from server.app.text2revit.actions import (
    ACTIONS, RevitAction, ParamDef, get_action, get_actions_summary,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intent recognition prompt
# ---------------------------------------------------------------------------

_INTENT_PROMPT = load_prompt("server.text2revit_intent.md")
_INTENT_SYSTEM = load_prompt("server.text2revit_intent_system.md")

# ---------------------------------------------------------------------------
# Parameter guidance prompt
# ---------------------------------------------------------------------------

_GUIDE_TEMPLATE_ZH = """\
好的，我来帮你{description_zh}。

还需要以下信息：
{missing_list}

{optional_hint}\
请提供上述信息。"""

_GUIDE_TEMPLATE_EN = """\
Sure, I'll help you {description_en}.

I still need the following information:
{missing_list}

{optional_hint}\
Please provide the above."""


class IntentRecognizer:
    def __init__(self, llm: LLMClient, db_path: str | None = None):
        self._llm = llm
        self._db_path = db_path

    def recognize(self, message: str) -> dict[str, Any]:
        """
        Recognize intent and extract parameters from user message.

        Returns:
            {"intent": str, "extracted_params": dict, "action": RevitAction | None}
        """
        prompt = _INTENT_PROMPT.format(
            actions_summary=get_actions_summary(),
            message=message,
        )
        raw = self._llm.generate_text(
            prompt,
            system_prompt=_INTENT_SYSTEM,
        )
        # Parse JSON
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw.strip())
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            return {"intent": "UNKNOWN", "extracted_params": {}, "action": None}

        intent = result.get("intent", "UNKNOWN")
        params = result.get("extracted_params", {})
        action = get_action(intent)

        return {"intent": intent, "extracted_params": params, "action": action}

    def check_missing_params(
        self, action: RevitAction, provided: dict[str, Any]
    ) -> list[ParamDef]:
        """Return list of required params not yet provided."""
        return [p for p in action.required_params if p.name not in provided]

    def generate_guidance(
        self,
        action: RevitAction,
        missing: list[ParamDef],
        language: str = "zh",
    ) -> str:
        """Generate a bilingual guidance message for missing parameters."""
        if language == "zh":
            missing_list = "\n".join(
                f"  {i}. **{p.name}** — {p.description_zh} ({p.type})"
                for i, p in enumerate(missing, 1)
            )
            optional_hint = ""
            if action.optional_params:
                opt = ", ".join(
                    f"{p.description_zh}(默认: {p.default})"
                    for p in action.optional_params
                )
                optional_hint = f"可选参数（将使用默认值）：{opt}\n\n"
            return _GUIDE_TEMPLATE_ZH.format(
                description_zh=action.description_zh,
                missing_list=missing_list,
                optional_hint=optional_hint,
            )
        else:
            missing_list = "\n".join(
                f"  {i}. **{p.name}** — {p.description_en} ({p.type})"
                for i, p in enumerate(missing, 1)
            )
            optional_hint = ""
            if action.optional_params:
                opt = ", ".join(
                    f"{p.description_en} (default: {p.default})"
                    for p in action.optional_params
                )
                optional_hint = f"Optional parameters (defaults will be used): {opt}\n\n"
            return _GUIDE_TEMPLATE_EN.format(
                description_en=action.description_en,
                missing_list=missing_list,
                optional_hint=optional_hint,
            )

    def build_instruction(
        self, action: RevitAction, params: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Build the final structured Revit instruction once all params are collected.
        """
        # Merge defaults for optional params
        full_params = {}
        for p in action.required_params:
            full_params[p.name] = params.get(p.name)
        for p in action.optional_params:
            full_params[p.name] = params.get(p.name, p.default)

        return {
            "action": action.api_method,
            "intent": action.intent,
            "params": full_params,
        }

    def enrich_from_db(self, action: RevitAction) -> str | None:
        """Query SQLite for additional API documentation about this action."""
        if not self._db_path or not action.sql_pattern:
            return None
        try:
            with contextlib.closing(sqlite3.connect(self._db_path)) as conn:
                row = conn.execute(
                    "SELECT syntax, parameters, remark FROM revit_api WHERE full_id LIKE ? LIMIT 1",
                    (action.sql_pattern,),
                ).fetchone()
            if row:
                parts = []
                if row[0]:
                    parts.append(f"Syntax: {row[0]}")
                if row[1]:
                    parts.append(f"Parameters: {row[1]}")
                if row[2]:
                    parts.append(f"Remarks: {row[2]}")
                return "\n".join(parts)
        except Exception as e:
            logger.warning("enrich_from_db query failed for %s: %s", action.sql_pattern, e)
        return None
