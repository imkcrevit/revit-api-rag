"""
Slot Engine — RAG-driven LLM Agent with Question Queue

Architecture:
  1. User sends text → query RAG for real Revit API docs → ONE LLM call
  2. LLM sees actual API syntax/parameters and decides what to ask
  3. Questions stored in session queue
  4. User answers each question → no LLM, just fill param + pop next question
  5. When queue empty → complete with summary + structured JSON

Parameters are driven by the Revit API documentation (via RAG retrieval),
NOT hardcoded YAML slot definitions.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from functools import lru_cache
from typing import Any

import yaml

from intent_bridge.llm_adapter import LLMAdapter
from intent_bridge.models import (
    IntentState,
    QuestionItem,
    SessionState,
    SessionStatus,
    SlotSource,
    SlotState,
    SlotStatus,
    TurnResponse,
)

logger = logging.getLogger("intent_bridge.engine")


# ===================================================================
# Language helpers
# ===================================================================

def _detect_language(text: str) -> str:
    if not text:
        return "en"
    cjk_count = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
    return "zh" if cjk_count / len(text) > 0.3 else "en"


def _bilingual(zh: str, en: str, lang: str) -> str:
    return zh if lang == "zh" else en


def _get_localized(sdef: dict, key: str, lang: str) -> str:
    if lang == "en":
        en_val = sdef.get(f"{key}_en")
        if en_val:
            return en_val
    return sdef.get(key, "")


# ===================================================================
# SchemaRegistry (intent classification only, NOT for slot definitions)
# ===================================================================

class SchemaRegistry:
    """Loads intent YAML for classification. Slot definitions come from RAG."""

    def __init__(self, yaml_path: str | None = None):
        if yaml_path is None:
            yaml_path = os.path.join(os.path.dirname(__file__), "schemas", "intent_slots.yaml")
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        self._intents: dict[str, dict] = data.get("intents", {})

    def get_intent_schema(self, intent_name: str) -> dict | None:
        return self._intents.get(intent_name)

    def get_all_intent_names(self) -> list[str]:
        return list(self._intents.keys())

    def get_intent_display_name(self, intent_name: str, lang: str = "zh") -> str:
        schema = self._intents.get(intent_name, {})
        return _get_localized(schema, "display_name", lang) or intent_name

    def get_intent_summary(self) -> str:
        lines = []
        for name, schema in self._intents.items():
            zh = schema.get("display_name", name)
            en = schema.get("display_name_en", name)
            api = schema.get("api_method", "")
            lines.append(f"- {name} ({zh}/{en}) api={api}")
        return "\n".join(lines)


@lru_cache(maxsize=1)
def get_schema_registry() -> SchemaRegistry:
    return SchemaRegistry()


# ===================================================================
# RAG retrieval — direct SQLite lookup for real Revit API docs
# ===================================================================

def _get_api_db_path() -> str:
    """Resolve path to revit_api.db."""
    try:
        from config import load_config
        config = load_config()
        sqlite_dir = config.get("data", {}).get("sqlite_dir", "./data/sqlite")
        return os.path.join(sqlite_dir, "revit_api.db")
    except Exception:
        return os.path.join("data", "sqlite", "revit_api.db")


def _query_api_by_method(method_patterns: list[str], limit: int = 5) -> list[dict]:
    """
    Query SQLite for Revit API docs matching specific method patterns.
    Targets exact method names (e.g. 'Wall.Create', 'NewFamilyInstance')
    and filters out noise (BuiltInFailures, etc.).
    """
    db_path = _get_api_db_path()
    if not os.path.exists(db_path):
        logger.warning("revit_api.db not found at %s", db_path)
        return []

    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        conditions = []
        params = []
        for pattern in method_patterns:
            conditions.append("full_id LIKE ?")
            params.append(f"%{pattern}%")

        if not conditions:
            conn.close()
            return []

        where = " OR ".join(conditions)
        query = f"""
            SELECT full_id, name, summary, syntax, parameters, remark
            FROM revit_api
            WHERE ({where})
              AND syntax IS NOT NULL AND syntax != ''
              AND parameters IS NOT NULL AND parameters != ''
              AND full_id NOT LIKE '%Failures%'
              AND full_id NOT LIKE '%Exception%'
              AND full_id NOT LIKE '%UnitTypeId%'
            ORDER BY
              CASE
                WHEN full_id LIKE '%Create(%' OR full_id LIKE '%NewFamily%' THEN 0
                WHEN full_id LIKE '%.Create%' OR full_id LIKE '%.New%' THEN 1
                ELSE 2
              END,
              length(parameters) DESC
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        conn.close()

        return [
            {
                "name": row["full_id"] or row["name"],
                "summary": row["summary"] or "",
                "syntax": row["syntax"] or "",
                "parameters": row["parameters"] or "",
                "remark": row["remark"] or "",
            }
            for row in rows
        ]
    except Exception as e:
        logger.warning("SQLite API query failed: %s", e)
        return []


# Per-intent API method patterns (targeted, not generic)
_INTENT_API_PATTERNS: dict[str, list[str]] = {
    "create_wall": ["Wall.Create("],
    "create_floor": ["Floor.Create("],
    "create_door": ["NewFamilyInstance("],
    "create_window": ["NewFamilyInstance("],
    "create_room": ["NewRoom(", "Room.Create("],
    "modify_element": ["ElementTransformUtils", "Move(", "Rotate("],
    "query_element": ["FilteredElementCollector", "get_Parameter("],
    "delete_element": ["Document.Delete("],
}


def _format_api_context(api_docs: list[dict]) -> str:
    """Format API docs into readable context for LLM."""
    if not api_docs:
        return "(No API documentation found — use your knowledge of Revit API)"

    parts = ["## Revit API Documentation (from database):"]
    for i, item in enumerate(api_docs, 1):
        name = item.get("name", "")
        summary = item.get("summary", "")
        syntax = item.get("syntax", "")
        parameters = item.get("parameters", "")
        remark = item.get("remark", "")

        block = f"\n### [{i}] {name}"
        if summary:
            block += f"\n{summary}"
        if syntax:
            block += f"\nSyntax: {syntax}"
        if parameters:
            block += f"\nParameters:\n{parameters}"
        if remark:
            block += f"\nRemarks: {remark}"
        parts.append(block)

    return "\n".join(parts)


# ===================================================================
# LLM Prompt — RAG-enhanced, ONE call, returns ALL questions
# ===================================================================

_ANALYZE_PROMPT = """你是一个 Revit 建筑设计助手 Agent / You are a Revit architectural design assistant agent.

## 语言规则 / Language Rule (MANDATORY — HIGHEST PRIORITY):
检测用户输入的语言：
- 如果用户使用**中文**，你的**所有输出必须是纯中文**。包括：问题文本、选项文本、参数值描述。
  **禁止**出现英文型号名（如 Casement_W_1200x1500, Fixed, Sliding）。
  正确示例：options=["平开窗 1200×1500", "推拉窗 1800×1500", "固定窗 900×1200", "其他 (自定义)"]
  错误示例：options=["Casement W_1200x1500", "Sliding W_1800x1500", "Fixed", "其他"]
- If user writes in **English**, ALL output must be in **pure English**.

## Available intents:
{intent_list}

{rag_context}

## Your task:
Analyze the user's input and use the **retrieved Revit API documentation** above to determine
what parameters are actually needed for this operation.

Do ALL of these in ONE response:

1. **Classify intent** — pick from the available intents.
2. **Extract parameters** — get everything you can from the user's input.
   Match parameters to the ACTUAL API signature shown in the documentation above.
3. **Plan ALL remaining questions** — based on the real API parameters:
   - Check which API parameters are still missing from the user's input.
   - For each missing parameter, create a question with sensible options.
   - The parameter names in your output should match the API documentation.

## ⚠️ 歧义检测规则 (MANDATORY — MUST FOLLOW):

你**必须**扫描用户输入中的以下歧义词。如果发现任何一个，**第一个问题必须是歧义澄清问题**。
不要假设用户的意思，不要静默选择一个解释。**必须询问**。

**必须检测的歧义词列表：**
| 用户原文 | 可能含义 | 必须提问 |
|---------|---------|---------|
| 背面    | 北面(朝北) 或 背面(后方) | "您是指**北面（朝北方向）**还是**背面（建筑后方）**？" |
| 前面    | 南面(朝南) 或 前面(入口方向) | "您是指**南面（朝南方向）**还是**前面（入口方向）**？" |
| 左边/右边 | 取决于观察方向 | "从哪个方向看的左/右？" |
| 那面墙/这个/那个 | 指代不明 | 必须要求用户明确指定 |
| 大的/小的/标准 | 具体尺寸不明 | 必须给出具体尺寸选项 |

**判断流程：**
1. 逐字扫描用户输入
2. 如果包含上表中任何词语 → questions[0] 必须是歧义澄清
3. 如果不包含 → 跳过此步骤

## 问题规划规则：

**优先级顺序：**
a) **歧义澄清** — 如上表，必须第一个问
b) **位置/朝向** — 如果用户没有明确指定位置，**必须**询问。永远不要跳过位置问题。
c) **类型/规格选择** — 基于 API 文档中的参数要求
d) **其他 API 参数** — 用户未指定的
e) 只跳过有明显默认值且建筑上不重要的参数

**你来决定需要多少个问题 — 没有上限。** 询问所有 API 要求但用户未指定的参数。

## ⚠️ 位置参数规则 (CRITICAL):

Revit API 中的位置参数需要 **ElementId 或坐标**，不能使用模糊描述（如"客厅背面"、"墙体后方"）。
当用户提到位置时，你必须将其转化为 Revit 可执行的参数：

**对于需要宿主元素的操作（如在墙上插入门/窗）：**
- slot 名称用 `host_wall`，值应该是具体的墙体描述（如"客厅北墙"、"卧室南墙"）
- 不要使用 "墙体背面" 这种无法映射到 ElementId 的表达
- 提问选项示例：["客厅北墙", "客厅南墙", "客厅东墙", "客厅西墙", "其他 (自定义)"]

**对于需要坐标的操作（如放置构件）：**
- 使用 `position_on_wall`（在墙上的位置）: "居中"、"偏左1/3"、"偏右1/3"
- 使用 `height_offset`（距地面高度）: 具体数值如 900、1200

**每个问题必须有具体的选项，最后一个选项必须是 "其他 (自定义)" / "Other (custom)"。**
选项示例（中文输入时）：
- 好的：options=["平开窗 1200×1500", "推拉窗 1800×1500", "固定窗 900×1200", "其他 (自定义)"]
- 好的：options=["居中", "偏左 1/3", "偏右 1/3", "其他 (自定义)"]
- 坏的：options=["Casement_W_1200x1500", "Sliding_W_1800x1500"]  ← 禁止英文型号
- 坏的：slots 中 location="客厅背面" ← 不可执行，必须拆分为 host_wall + position

**如果不需要问题**（所有参数都已提供或有合理默认值），设 questions=[] 并提供 summary。
**summary 中不要包含"请确认"或"点击确认"等提示，只描述将执行的操作。**

## Output (pure JSON, no other text):
{{
  "intent": "intent_name",
  "confidence": 0.0-1.0,
  "api_method": "the Revit API method to call (from documentation above)",
  "slots": {{ "param_name": "value" }},
  "questions": [
    {{
      "slot": "api_parameter_name",
      "text": "question text in user's language",
      "options": ["选项A (细节)", "选项B (细节)", "其他 (自定义)"],
      "values": ["value_a", "value_b", "custom"]
    }}
  ],
  "summary": "complete action sentence (only if questions is empty)"
}}

## User input:
"{user_input}"
"""


# ===================================================================
# ConversationOrchestrator
# ===================================================================

class ConversationOrchestrator:
    """
    RAG-enhanced: query API docs → one LLM call → question queue → instant answers → complete.

    process_turn(): called for initial user text (RAG + LLM call)
    answer_question(): called for each option selection (NO LLM, instant)
    """

    def __init__(
        self,
        llm: LLMAdapter | None = None,
        registry: SchemaRegistry | None = None,
    ):
        self._llm = llm or LLMAdapter()
        self._registry = registry or get_schema_registry()

    async def process_turn(
        self, user_input: str, session: SessionState,
    ) -> TurnResponse:
        """Initial user text → RAG API lookup → LLM call → returns first question or complete."""
        session.touch()
        session.turn_count += 1
        session.add_message("user", user_input)

        lang = _detect_language(user_input)
        start = time.time()

        # Step 1: Look up relevant Revit API docs from SQLite
        # Fetch docs for all intents (fast SQL — <10ms) so LLM can classify + extract in one call
        all_patterns = set()
        for iname in self._registry.get_all_intent_names():
            all_patterns.update(_INTENT_API_PATTERNS.get(iname, []))

        loop = asyncio.get_event_loop()
        api_docs = await loop.run_in_executor(
            None, _query_api_by_method, list(all_patterns), 8,
        )
        rag_context = _format_api_context(api_docs)

        rag_duration = (time.time() - start) * 1000
        logger.info("API doc lookup: %.0fms, %d docs found", rag_duration, len(api_docs))

        # Step 2: Build prompt with real API documentation
        prompt = _ANALYZE_PROMPT.format(
            intent_list=self._registry.get_intent_summary(),
            rag_context=rag_context,
            user_input=user_input,
        )

        raw = await self._llm.complete_async(prompt, temperature=0.1)
        result = LLMAdapter.extract_json(raw)

        duration = (time.time() - start) * 1000
        logger.info("LLM analysis (with API docs): %.0fms", duration)

        intent_name = result.get("intent", "unknown")
        confidence = result.get("confidence", 0.0)
        api_method = result.get("api_method", "")
        extracted_slots = result.get("slots", {})
        questions_raw = result.get("questions", [])
        summary = result.get("summary", "")

        # Unknown intent
        if intent_name == "unknown" or not self._registry.get_intent_schema(intent_name):
            return self._unknown_response(session, lang)

        # Set intent
        display = self._registry.get_intent_display_name(intent_name, lang)
        session.intent = IntentState(name=intent_name, display_name=display, confidence=confidence)

        # Store api_method in session metadata
        if api_method:
            session.intent.slots["_api_method"] = SlotState(name="_api_method")
            session.intent.slots["_api_method"].fill(api_method, source=SlotSource.inferred)

        # Apply extracted slots
        self._apply_slots(session.intent, extracted_slots)

        # Parse questions into queue
        session.pending_questions = []
        for q in questions_raw:
            session.pending_questions.append(QuestionItem(
                slot=q.get("slot", ""),
                text=q.get("text", ""),
                options=q.get("options", []),
                values=q.get("values", []),
                allow_custom=q.get("allow_custom", True),
            ))

        # If no questions → complete immediately
        if not session.pending_questions:
            return await self._complete(session, summary, lang)

        # Return first question
        return await self._next_question_response(session, lang)

    async def answer_question(
        self, session: SessionState, value: Any, option_index: int = -1,
    ) -> TurnResponse:
        """
        Answer current question — fills slot, pops next question or completes.
        LLM call only on final completion (for summary).
        """
        session.touch()
        first_msg = next((m["content"] for m in session.history if m["role"] == "user"), "")
        lang = _detect_language(first_msg)

        question = session.pop_question()
        if not question:
            return await self._complete(session, "", lang)

        # Resolve value from option index
        if 0 <= option_index < len(question.values):
            resolved_value = question.values[option_index]
        else:
            resolved_value = value

        # Fill the slot — use the option display text for display
        slot_name = question.slot
        if slot_name not in session.intent.slots:
            session.intent.slots[slot_name] = SlotState(name=slot_name)
        display_text = ""
        if 0 <= option_index < len(question.options):
            display_text = question.options[option_index]
        session.intent.slots[slot_name].fill(
            resolved_value, source=SlotSource.follow_up,
            display=display_text or str(resolved_value),
        )

        # Record in history
        session.add_message("user", display_text or str(value))

        # Next question or complete
        if session.pending_questions:
            return await self._next_question_response(session, lang)
        else:
            return await self._complete(session, "", lang)

    # -------------------------------------------------------------------
    # Response builders
    # -------------------------------------------------------------------

    async def _next_question_response(self, session: SessionState, lang: str) -> TurnResponse:
        question = session.peek_question()
        if not question:
            return await self._complete(session, "", lang)

        intent = session.intent
        session.status = SessionStatus.need_followup

        return TurnResponse(
            session_id=session.session_id,
            turn=session.turn_count,
            status=SessionStatus.need_followup,
            intent={
                "name": intent.name,
                "display_name": intent.display_name,
                "confidence": intent.confidence,
            },
            slots=self._build_slots_display(intent),
            followup_question=question.text,
            current_question=question,
            questions_remaining=len(session.pending_questions),
        )

    async def _complete(self, session: SessionState, llm_summary: str, lang: str) -> TurnResponse:
        intent = session.intent
        structured = self._build_structured_output(session)
        summary = llm_summary or await self._llm_summary(session, lang)

        session.status = SessionStatus.complete
        session.add_message("assistant", summary)

        return TurnResponse(
            session_id=session.session_id,
            turn=session.turn_count,
            status=SessionStatus.complete,
            intent={
                "name": intent.name,
                "display_name": intent.display_name,
                "confidence": intent.confidence,
            },
            slots=self._build_slots_display(intent),
            summary=summary,
            structured_output=structured,
            questions_remaining=0,
        )

    def _unknown_response(self, session: SessionState, lang: str) -> TurnResponse:
        summary = self._registry.get_intent_summary()
        msg = _bilingual(
            f"抱歉，无法理解该操作。支持的操作：\n{summary}\n\n请重新描述。",
            f"Sorry, could not understand. Supported:\n{summary}\n\nPlease try again.",
            lang,
        )
        return TurnResponse(
            session_id=session.session_id,
            turn=session.turn_count,
            status=SessionStatus.need_followup,
            intent={"name": "unknown", "display_name": _bilingual("未识别", "Unrecognized", lang), "confidence": 0.0},
            followup_question=msg,
        )

    # -------------------------------------------------------------------
    # Slot helpers
    # -------------------------------------------------------------------

    def _apply_slots(self, intent: IntentState, extracted: dict[str, Any]):
        """Apply LLM-extracted parameters directly (no alias resolution needed — LLM handles it)."""
        for key, value in extracted.items():
            if value is None:
                continue
            if key not in intent.slots:
                intent.slots[key] = SlotState(name=key)
            intent.slots[key].fill(value, source=SlotSource.user_input)

    def _build_slots_display(self, intent: IntentState) -> dict[str, dict[str, Any]]:
        result = {}
        for name, slot in intent.slots.items():
            if name.startswith("_"):  # skip internal metadata
                continue
            result[name] = {
                "value": slot.value,
                "status": slot.status.value,
                "display": slot.display or str(slot.value) if slot.value is not None else "",
                "source": slot.source.value,
            }
        return result

    def _build_structured_output(self, session: SessionState) -> dict[str, Any]:
        intent = session.intent
        slot_values = {}
        slot_sources = {}
        api_method = ""
        for name, slot in intent.slots.items():
            if name == "_api_method":
                api_method = slot.value or ""
                continue
            slot_values[name] = slot.value
            slot_sources[name] = slot.source.value

        # Fallback: look up api_method from YAML schema if not from LLM
        if not api_method:
            schema = self._registry.get_intent_schema(intent.name)
            api_method = schema.get("api_method", "") if schema else ""

        return {
            "$schema": "intent_bridge_output_v1",
            "intent": intent.name,
            "confidence": intent.confidence,
            "api_method": api_method,
            "parameters": slot_values,
            "metadata": {
                "session_id": session.session_id,
                "turns": session.turn_count,
                "parameter_sources": slot_sources,
            },
        }

    async def _llm_summary(self, session: SessionState, lang: str) -> str:
        """Use LLM to generate a natural language summary from collected parameters."""
        intent = session.intent
        sv = {n: s.value for n, s in intent.slots.items()
              if s.value is not None and not n.startswith("_")}

        if not sv:
            return f"{intent.display_name}。" if lang == "zh" else f"{intent.display_name}."

        params_str = json.dumps(sv, ensure_ascii=False, indent=2)
        if lang == "zh":
            prompt = (
                f"你是 Revit 建筑设计助手。根据以下意图和参数，用一句自然的中文描述将要执行的操作。\n"
                f"不要使用英文参数名，不要使用 key=value 格式，用自然语言描述。\n"
                f"不要添加'请确认'等提示语，只描述操作本身。\n\n"
                f"意图：{intent.display_name}\n"
                f"参数：{params_str}\n\n"
                f"请用一句话描述："
            )
        else:
            prompt = (
                f"You are a Revit design assistant. Describe the following action in one natural sentence.\n"
                f"Do not use key=value format. Just describe the action naturally.\n"
                f"Do not add 'please confirm' or similar prompts.\n\n"
                f"Intent: {intent.display_name}\n"
                f"Parameters: {params_str}\n\n"
                f"Describe in one sentence:"
            )

        try:
            summary = await self._llm.complete_async(prompt, temperature=0.3)
            # Strip any markdown fences or extra whitespace
            summary = summary.strip().strip('"').strip("```").strip()
            return summary
        except Exception as e:
            logger.warning("LLM summary failed, using fallback: %s", e)
            return self._fallback_summary(session, lang)

    def _fallback_summary(self, session: SessionState, lang: str) -> str:
        """Fallback: build summary without LLM."""
        intent = session.intent
        sv = {n: s.value for n, s in intent.slots.items()
              if s.value is not None and not n.startswith("_")}

        display = intent.display_name
        if not sv:
            return f"{display}。" if lang == "zh" else f"{display}."

        # Build readable param list
        params = "，".join(f"{k}={v}" for k, v in sv.items()) if lang == "zh" else \
                 ", ".join(f"{k}={v}" for k, v in sv.items())

        if lang == "zh":
            return f"{display}：{params}。"
        return f"{display}: {params}."

    async def update_slots_directly(
        self, session: SessionState, slot_updates: dict[str, Any],
    ) -> TurnResponse:
        intent = session.intent
        for key, value in slot_updates.items():
            if key not in intent.slots:
                intent.slots[key] = SlotState(name=key)
            intent.slots[key].fill(value, source=SlotSource.follow_up)
        lang = _detect_language(str(slot_updates))
        return await self._complete(session, "", lang)
