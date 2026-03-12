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
    ActionStep,
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

_ANALYZE_PROMPT = """You are a Revit architectural design assistant agent.

## LANGUAGE RULE (HIGHEST PRIORITY):
Detect the user's input language:
- If Chinese input -> ALL question text, option labels, and descriptions MUST be in Chinese.
  FORBIDDEN: English model names like "Casement_W_1200x1500", "Fixed", "Sliding".
  CORRECT: options=["平开窗 1200×1500", "推拉窗 1800×1500", "固定窗 900×1200", "其他 (自定义)"]
  WRONG:   options=["Casement W_1200x1500", "Sliding W_1800x1500"]
- If English input -> ALL output in pure English.

## Available intents:
{intent_list}

{rag_context}

## YOUR TASK:
Analyze the user input. Use the Revit API documentation above to determine the EXACT API method
and ALL its required parameters.

Do ALL of these in ONE response:
1. Classify intent from the available list.
2. Identify the specific API method (from the documentation above).
3. List ALL parameters of that API method.
4. Extract any parameter values the user already provided.
5. For EVERY remaining parameter, create a question. DO NOT skip or default ANY parameter.

## AMBIGUITY DETECTION (MANDATORY):

Scan the user input for these ambiguous Chinese terms. If ANY are found, questions[0] MUST be
a disambiguation question. Never assume meaning silently.

| User text | Possible meanings | Must ask |
|-----------|-------------------|----------|
| 背面 | 北面(north) vs 背面(rear of building) | Which one? |
| 前面 | 南面(south) vs 前面(entrance side) | Which one? |
| 左边/右边 | Depends on viewing direction | From which direction? |
| 那面墙/这个/那个 | Ambiguous reference | Must specify exactly |
| 大的/小的/标准 | Unknown dimensions | Must give concrete sizes |

## PARAMETER RULES (CRITICAL — MUST FOLLOW ALL):

### Rule 1: Ask about EVERY parameter — no silent defaults
For each parameter in the API method signature, you MUST either:
  a) Extract its value from the user's input (put in "slots"), OR
  b) Create a question for it (put in "questions")
DO NOT silently default ANY parameter. Even "structuralType" or "level" must be asked if not stated.

### Rule 2: Parameter values must be Revit-executable types
Every parameter value must be a type that Revit API can actually consume:
- ElementId parameters -> user must provide an actual ElementId (integer number)
- XYZ/coordinate parameters -> numeric coordinates (e.g., "1000,500,0")
- Enum parameters -> valid enum member name
- Type parameters -> FamilySymbol name or type name

NOTE: This system is NOT connected to a live Revit session. The user needs to provide
ElementId numbers directly (they can look them up in Revit). Do NOT accept vague descriptions
like "客厅背面" or "living room back wall" as parameter values.

### Rule 3: Host element / ElementId parameters
For parameters that require an Element or ElementId (e.g., host wall for door/window):
- The question MUST explain that an ElementId is required.
- Chinese example: "请输入宿主墙体的 ElementId（在 Revit 中选择墙体可查看其 Id）"
- English example: "Enter the host wall ElementId (select the wall in Revit to see its Id)"
- Provide "其他 (自定义)" / "Other (custom)" as the ONLY option so user enters the ElementId.
  This is because ElementId values are project-specific and cannot be pre-filled.
- WRONG: slots.host = "客厅背面" (vague text, not an ElementId)
- CORRECT: Ask user to input ElementId, value will be like "12345"

### Rule 4: Type/family parameters are mandatory
Wall type, window type, door type, floor type etc. are NEVER optional.
Always ask about them with concrete options showing dimensions and properties.

### Rule 5: Question options format
Each question MUST have:
- 3-6 concrete options with details (dimensions, specs)
- Last option: "其他 (自定义)" (Chinese) or "Other (custom)" (English)
- Options in the user's language (Chinese options for Chinese input)

GOOD options: ["平开窗 1200×1500", "推拉窗 1800×1500", "固定窗 900×1200", "其他 (自定义)"]
BAD options:  ["窗户类型？"] (no concrete choices)
BAD options:  ["Casement_W_1200x1500"] (English model name for Chinese user)

### Rule 6: Question ordering priority
a) Ambiguity disambiguation (if any found above)
b) Host element / location
c) Type / family selection
d) Dimensions and properties
e) Other API parameters

## MULTI-ACTION DECOMPOSITION (CRITICAL):

Some user requests require MULTIPLE sequential API calls. You MUST detect these and decompose
them into an action plan. Examples:

| User request | Requires | Action plan |
|---|---|---|
| "Create a room" | Room needs enclosed walls | Step 1: Check/create walls -> Step 2: Create room |
| "Add a door to the new wall" | Wall + door | Step 1: Create wall -> Step 2: Place door |
| "Create a room with a window" | Walls + room + window | Step 1: Create walls -> Step 2: Create room -> Step 3: Place window |

For composite actions, use "action_plan" in the output (array of steps).
Each step has its own intent, api_method, and questions.

For a SINGLE action (most cases), just use the flat format (no action_plan field).

**Room creation specifically:**
- Room.Create / NewRoom requires enclosed walls (boundary).
- Ask user: center point, desired area or length+width, wall height, wall type.
- Step 1: create 4 walls forming a closed rectangle.
- Step 2: create room inside the boundary.

## OUTPUT FORMAT (pure JSON, no markdown, no other text):

For SINGLE action:
{{
  "intent": "intent_name",
  "confidence": 0.0-1.0,
  "api_method": "exact Revit API method",
  "slots": {{ "param_name": "extracted_value" }},
  "questions": [
    {{
      "slot": "parameter_name",
      "text": "question in user's language",
      "options": ["Option A", "Option B", "其他 (自定义)"],
      "values": ["value_a", "value_b", "custom"]
    }}
  ],
  "summary": "action description (ONLY when questions is empty)"
}}

For MULTI-ACTION (composite operations):
{{
  "intent": "composite",
  "confidence": 0.0-1.0,
  "action_plan": [
    {{
      "step": 1,
      "intent": "create_wall",
      "display_name": "创建闭合墙体 / Create enclosed walls",
      "api_method": "Wall.Create",
      "description": "Create 4 walls forming a rectangle",
      "questions": [
        {{
          "slot": "center_point",
          "text": "question text",
          "options": ["..."],
          "values": ["..."]
        }}
      ]
    }},
    {{
      "step": 2,
      "intent": "create_room",
      "display_name": "创建房间 / Create room",
      "api_method": "NewRoom",
      "description": "Create room inside the walls",
      "questions": [...]
    }}
  ],
  "summary": ""
}}

IMPORTANT: "summary" should ONLY appear when ALL questions across ALL steps are empty.
Do NOT include "please confirm" in the summary.

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
        action_plan_raw = result.get("action_plan")

        # --- Multi-action plan ---
        if intent_name == "composite" and action_plan_raw:
            return await self._init_action_plan(session, result, lang)

        # --- Single action (original flow) ---
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
        session.pending_questions = self._parse_questions(questions_raw)

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
        For multi-action plans: advances to next action step when current step's questions are done.
        """
        session.touch()
        first_msg = next((m["content"] for m in session.history if m["role"] == "user"), "")
        lang = _detect_language(first_msg)

        question = session.pop_question()
        if not question:
            # Try to advance to next action step
            if session.action_plan:
                return await self._advance_action_plan(session, lang)
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

        # Also store in current action step's filled_slots
        if session.action_plan and session.current_action_index < len(session.action_plan):
            step = session.action_plan[session.current_action_index]
            step.filled_slots[slot_name] = session.intent.slots[slot_name]

        # Record in history
        session.add_message("user", display_text or str(value))

        # Next question or advance action plan
        if session.pending_questions:
            return await self._next_question_response(session, lang)
        elif session.action_plan:
            return await self._advance_action_plan(session, lang)
        else:
            return await self._complete(session, "", lang)

    # -------------------------------------------------------------------
    # Multi-action plan helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _parse_questions(questions_raw: list[dict]) -> list[QuestionItem]:
        """Parse raw question dicts into QuestionItem list."""
        items = []
        for q in questions_raw:
            items.append(QuestionItem(
                slot=q.get("slot", ""),
                text=q.get("text", ""),
                options=q.get("options", []),
                values=q.get("values", []),
                allow_custom=q.get("allow_custom", True),
            ))
        return items

    async def _init_action_plan(
        self, session: SessionState, result: dict, lang: str,
    ) -> TurnResponse:
        """Initialize a multi-action plan from LLM response."""
        confidence = result.get("confidence", 0.0)
        plan_raw = result.get("action_plan", [])

        # Build action steps
        session.action_plan = []
        for step_data in plan_raw:
            step = ActionStep(
                step=step_data.get("step", len(session.action_plan) + 1),
                intent=step_data.get("intent", ""),
                display_name=step_data.get("display_name", ""),
                api_method=step_data.get("api_method", ""),
                description=step_data.get("description", ""),
                slots=step_data.get("slots", {}),
                questions=self._parse_questions(step_data.get("questions", [])),
            )
            session.action_plan.append(step)

        session.current_action_index = 0
        total_steps = len(session.action_plan)

        # Set intent to composite
        session.intent = IntentState(
            name="composite",
            display_name=_bilingual(
                f"组合操作 ({total_steps} 步)",
                f"Composite ({total_steps} steps)",
                lang,
            ),
            confidence=confidence,
        )

        # Load first action step
        return await self._load_action_step(session, lang)

    async def _load_action_step(
        self, session: SessionState, lang: str,
    ) -> TurnResponse:
        """Load the current action step's questions into the session queue."""
        idx = session.current_action_index
        if idx >= len(session.action_plan):
            return await self._complete_action_plan(session, lang)

        step = session.action_plan[idx]
        total = len(session.action_plan)

        # Update intent display to show current step
        session.intent.display_name = _bilingual(
            f"步骤 {idx + 1}/{total}: {step.display_name}",
            f"Step {idx + 1}/{total}: {step.display_name}",
            lang,
        )

        # Store step's api_method
        session.intent.slots["_api_method"] = SlotState(name="_api_method")
        session.intent.slots["_api_method"].fill(step.api_method, source=SlotSource.inferred)

        # Apply any pre-filled slots
        for key, value in step.slots.items():
            if value is not None:
                if key not in session.intent.slots:
                    session.intent.slots[key] = SlotState(name=key)
                session.intent.slots[key].fill(value, source=SlotSource.user_input)

        # Load questions into queue
        session.pending_questions = list(step.questions)

        # Add step header message to chat
        step_msg = _bilingual(
            f"📋 步骤 {idx + 1}/{total}: {step.description}",
            f"📋 Step {idx + 1}/{total}: {step.description}",
            lang,
        )
        session.add_message("assistant", step_msg)

        if not session.pending_questions:
            # No questions for this step, advance
            step.completed = True
            session.current_action_index += 1
            return await self._load_action_step(session, lang)

        return await self._next_question_response(session, lang)

    async def _advance_action_plan(
        self, session: SessionState, lang: str,
    ) -> TurnResponse:
        """Mark current action step as complete, move to next."""
        idx = session.current_action_index
        if idx < len(session.action_plan):
            session.action_plan[idx].completed = True

        session.current_action_index += 1

        if session.current_action_index >= len(session.action_plan):
            return await self._complete_action_plan(session, lang)

        # Clear step-specific slots (keep _api_method and shared ones)
        # but load next step
        return await self._load_action_step(session, lang)

    async def _complete_action_plan(
        self, session: SessionState, lang: str,
    ) -> TurnResponse:
        """All action steps completed — build final output."""
        # Collect all steps' outputs
        all_steps = []
        for step in session.action_plan:
            step_output = {
                "step": step.step,
                "intent": step.intent,
                "api_method": step.api_method,
                "description": step.description,
                "parameters": {
                    name: slot.value
                    for name, slot in step.filled_slots.items()
                },
            }
            all_steps.append(step_output)

        structured = {
            "$schema": "intent_bridge_output_v1",
            "intent": "composite",
            "confidence": session.intent.confidence,
            "action_plan": all_steps,
            "metadata": {
                "session_id": session.session_id,
                "turns": session.turn_count,
                "total_steps": len(session.action_plan),
            },
        }

        # Use LLM to generate a natural summary of all steps
        summary = await self._llm_summary(session, lang)

        session.status = SessionStatus.complete
        session.add_message("assistant", summary)

        return TurnResponse(
            session_id=session.session_id,
            turn=session.turn_count,
            status=SessionStatus.complete,
            intent={
                "name": "composite",
                "display_name": session.intent.display_name,
                "confidence": session.intent.confidence,
            },
            slots=self._build_slots_display(session.intent),
            summary=summary,
            structured_output=structured,
            questions_remaining=0,
        )

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
