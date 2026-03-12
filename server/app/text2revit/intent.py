"""
意图识别 + 参数引导

1. 用 LLM 分析用户意图 → 映射到 RevitAction
2. 对比已提供 vs 缺失参数
3. 生成引导提示（中英双语）
4. 参数完整后生成结构化指令
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from pipeline.llm_client import LLMClient
from server.app.text2revit.actions import (
    ACTIONS, RevitAction, ParamDef, get_action, get_actions_summary,
)


# ---------------------------------------------------------------------------
# Intent recognition prompt
# ---------------------------------------------------------------------------

_INTENT_PROMPT = """\
You are a Revit design assistant. Analyze the user's message and determine which Revit operation they want to perform.

## Supported operations:
{actions_summary}

## Rules:
1. Return a JSON object with "intent" (one of the supported intents, or "UNKNOWN") and "extracted_params" (any parameters you can extract from the message).
2. For point coordinates, normalize to [x, y, z] arrays.
3. For numeric values, convert to numbers.
4. If the user's intent is unclear or not supported, set intent to "UNKNOWN".
5. Output ONLY valid JSON, no explanation.

## Examples:
User: "创建一面从(0,0,0)到(10,0,0)的墙，高3米"
Output: {{"intent": "CREATE_WALL", "extracted_params": {{"start_point": [0,0,0], "end_point": [10,0,0], "height": 3.0}}}}

User: "放一根柱子在(5,5,0)"
Output: {{"intent": "CREATE_COLUMN", "extracted_params": {{"location": [5,5,0]}}}}

User: "帮我画个圆"
Output: {{"intent": "UNKNOWN", "extracted_params": {{}}}}

User message: {message}
"""

# ---------------------------------------------------------------------------
# Parameter guidance prompt
# ---------------------------------------------------------------------------

_GUIDE_TEMPLATE_ZH = """\
好的，我来帮你{description_zh}。

还需要以下信息：
{missing_list}

{optional_hint}\
请提供上述信息，或输入 "默认" 使用默认值。"""

_GUIDE_TEMPLATE_EN = """\
Sure, I'll help you {description_en}.

I still need the following information:
{missing_list}

{optional_hint}\
Please provide the above, or type "defaults" to use default values."""


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
            system_prompt="You are a Revit intent classifier. Output JSON only.",
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
            conn = sqlite3.connect(self._db_path)
            row = conn.execute(
                "SELECT syntax, parameters, remark FROM revit_api WHERE full_id LIKE ? LIMIT 1",
                (action.sql_pattern,),
            ).fetchone()
            conn.close()
            if row:
                parts = []
                if row[0]:
                    parts.append(f"Syntax: {row[0]}")
                if row[1]:
                    parts.append(f"Parameters: {row[1]}")
                if row[2]:
                    parts.append(f"Remarks: {row[2]}")
                return "\n".join(parts)
        except Exception:
            pass
        return None
