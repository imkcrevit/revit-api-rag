"""
Slot Engine — RAG-driven LLM Agent with Question Queue

Architecture:
  1. User sends text → dynamic RAG query for Revit API docs → ONE LLM call
  2. LLM sees actual API syntax/parameters and decides what to ask
  3. Questions stored in session queue
  4. User answers each question → no LLM, just fill param + pop next question
  5. When queue empty → complete with summary + structured JSON
  6. Execution matching: intent params → solidified tool or code generation

Parameters are driven by the Revit API documentation (via RAG retrieval),
NOT hardcoded YAML slot definitions.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from functools import lru_cache
from typing import Any

import yaml

from intent_bridge.llm_adapter import LLMAdapter
from intent_bridge.skill_loader import get_skill_loader
from intent_bridge.models import (
    ActionStep,
    IntentState,
    QuestionItem,
    SessionState,
    SessionStatus,
    SlotSource,
    SlotState,
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
# Chinese → API keyword mapping (for dynamic RAG search)
# ===================================================================

_ZH_TO_API_KEYWORDS: dict[str, list[str]] = {
    "墙": ["Wall.Create", "WallType"],
    "楼板": ["Floor.Create", "FloorType"],
    "地板": ["Floor.Create", "FloorType"],
    "门": ["NewFamilyInstance", "Door"],
    "窗": ["NewFamilyInstance", "Window"],
    "窗户": ["NewFamilyInstance", "Window"],
    "房间": ["NewRoom", "Room.Create"],
    "删除": ["Document.Delete"],
    "修改": ["ElementTransformUtils", "Move", "Rotate"],
    "移动": ["ElementTransformUtils.Move"],
    "旋转": ["ElementTransformUtils.Rotate"],
    "查询": ["FilteredElementCollector", "get_Parameter"],
    "柱": ["NewFamilyInstance", "Column"],
    "梁": ["NewFamilyInstance", "Beam"],
    "屋顶": ["RoofBase", "FootPrintRoof"],
    "楼梯": ["Stairs", "StairsRun"],
    "栏杆": ["Railing"],
    "坡道": ["Ramp"],
    "幕墙": ["CurtainWall", "Wall.Create"],
    "族": ["FamilyInstance", "FamilySymbol"],
}


# ===================================================================
# SchemaRegistry — loads intent_registry.yaml
# ===================================================================

class SchemaRegistry:
    """Loads intent registry for classification and command mapping."""

    def __init__(self, yaml_path: str | None = None):
        if yaml_path is None:
            yaml_path = os.path.join(os.path.dirname(__file__), "schemas", "intent_registry.yaml")
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

    def get_intent_keywords(self, intent_name: str) -> list[str]:
        schema = self._intents.get(intent_name, {})
        return schema.get("keywords", [])

    def get_mapped_commands(self, intent_name: str) -> list[str]:
        schema = self._intents.get(intent_name, {})
        return schema.get("mapped_commands", [])

    def get_intent_summary(self) -> str:
        lines = []
        for name, schema in self._intents.items():
            if name == "custom":
                continue
            zh = schema.get("display_name", name)
            en = schema.get("display_name_en", name)
            desc = schema.get("description", "")
            lines.append(f"- {name} ({zh}/{en}): {desc}")
        lines.append("- custom (自定义操作/Custom Operation): RAG-matched Revit API operations")
        return "\n".join(lines)

    def get_all_keywords(self) -> list[str]:
        """Get all keywords from all intents (for search term extraction)."""
        keywords = []
        for schema in self._intents.values():
            keywords.extend(schema.get("keywords", []))
        return keywords


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


def _extract_search_terms(user_input: str, registry: SchemaRegistry) -> list[str]:
    """
    Dynamically extract API search terms from user input.
    Strategy:
    1. Match registry keywords against input
    2. Map Chinese terms via _ZH_TO_API_KEYWORDS
    3. Extract English technical terms via regex
    4. Fallback: use raw input words
    """
    terms: list[str] = []

    # 1. Registry keyword matching
    for intent_name in registry.get_all_intent_names():
        for kw in registry.get_intent_keywords(intent_name):
            if kw.lower() in user_input.lower():
                terms.append(kw)

    # 2. Chinese → API keyword mapping
    for zh_term, api_terms in _ZH_TO_API_KEYWORDS.items():
        if zh_term in user_input:
            terms.extend(api_terms)

    # 3. English technical terms (PascalCase, camelCase, dotted names)
    tech_terms = re.findall(r'[A-Z][a-z]+(?:[A-Z][a-z]+)+', user_input)  # PascalCase
    tech_terms += re.findall(r'\b\w+\.\w+\b', user_input)  # Dotted (Wall.Create)
    terms.extend(tech_terms)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            unique.append(t)

    # 4. Fallback: if nothing matched, use the raw input as search
    if not unique:
        # Extract meaningful words (skip very short ones)
        words = re.findall(r'[a-zA-Z]{3,}', user_input)
        unique = words[:3] if words else [user_input[:50]]

    return unique


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
# LLM Prompt — Agent-style, RAG-enhanced, ONE call
# ===================================================================

_ANALYZE_PROMPT = """You are a Revit API Agent. Analyze user requests using the retrieved API documentation below.

## LANGUAGE RULE (HIGHEST PRIORITY):
ALL question text and descriptions MUST be bilingual (Chinese + English), regardless of input language.
Format: "中文说明 / English description"
Example question text: "请选择墙体类型 / Select wall type:"
Example option labels: Keep Revit family names as-is (e.g. "Generic - 200mm"), add Chinese prefix if helpful (e.g. "常规 - 200mm")
Last option should be: "其他 (自定义) / Other (custom)"

{rag_context}

## Known intents (for classification):
{intent_list}

If the user's request doesn't match any known intent above, use intent="custom" with the
api_method derived from the API documentation. Do NOT reject unknown operations.

## Core Rules:

### Rule 1: NEVER silently default ANY parameter (HIGHEST PRIORITY AFTER LANGUAGE)
You are NOT connected to a live Revit session. You CANNOT look up types, levels, or positions.
For EVERY parameter in the API method signature, you MUST either:
  a) Extract its EXACT value from the user's input text (put in "slots"), OR
  b) Create a question for the user (put in "questions")

FORBIDDEN behaviors (these are ERRORS):
- Picking a default type/family (e.g., "use the first available column type") — ASK the user
- Inventing coordinates (e.g., "(0,0,0)" and "(10,0,0)") — ASK the user
- Assuming a level (e.g., "use the first level") — ASK the user
- Picking StructuralType silently — ASK the user if ambiguous
- Saying "I'll find it in the document" — you CANNOT, the user must provide it

If the user says "创建两个结构柱", you must ask about ALL of:
- Column type/family (with options)
- Level (with options)
- XYZ coordinates for EACH column (since quantity=2, ask for 2 positions)
- StructuralType if not obvious

### Rule 2: Parameter values must be Revit-executable types
- ElementId parameters -> user must provide an actual ElementId (integer)
- XYZ/coordinate parameters -> numeric coordinates (e.g., "1000,500,0")
- Enum parameters -> valid enum member name
- Type parameters -> FamilySymbol name or type name
- Level parameters -> level name string (e.g., "Level 1", "F1", "标高 1")

### Rule 3: Host element / ElementId parameters
For parameters requiring Element or ElementId (e.g., host wall for door/window):
- Explain that an ElementId is required
- Chinese: "请输入宿主墙体的 ElementId（在 Revit 中选择墙体可查看其 Id）"
- English: "Enter the host wall ElementId (select the wall in Revit to see its Id)"

### Rule 4: Type/family parameters are ALWAYS mandatory
Wall type, column type, window type, door type, floor type, beam type etc. are NEVER optional.
Always ask about them with concrete options showing dimensions and properties.
You MUST NOT silently pick "the first available" or "default" type.

### Rule 5: Position/coordinate parameters are ALWAYS mandatory
XYZ coordinates, start/end points, placement locations are NEVER optional.
You MUST NOT invent coordinates. Always ask the user to provide them.

### Rule 6: Question options format and `enrich` tagging
Each question MUST have:
- 3-6 concrete options with details (dimensions, specs)
- Last option: "其他 (自定义)" (Chinese) or "Other (custom)" (English)
- Options in the user's language
- An `enrich` field indicating what Revit data should replace these placeholder options:
  - `"family_type:<category>"` — replace with real Revit family types (category = column, beam, wall, floor, window, door, ceiling, roof, furniture, etc.)
  - `"level"` — replace with real Revit levels
  - `"host_pick"` — this parameter requires the user to select an element in Revit interactively
  - `"none"` — no enrichment needed (for coordinates, booleans, enums, free text, dimensions, etc.)

  Examples: `"enrich": "family_type:column"`, `"enrich": "level"`, `"enrich": "host_pick"`, `"enrich": "none"`

### Rule 7: Question ordering priority
a) Ambiguity disambiguation (if any)
b) Type / family selection
c) Level selection
d) Position / coordinates
e) Dimensions and properties
f) Other API parameters

### Rule 8: QUANTITY — When user requests N items (N>1)
Detect quantity keywords: 两/三/四/五/多个/几个/two/three/four/multiple etc.
- Extract "quantity" into "slots"
- Position-dependent params (coordinates, host elements) MUST ask for ALL N values in ONE question
- Shared params (type, height, material) stay single values — ask once
- Question text MUST clearly list N entries with numbered placeholders

**CRITICAL**: Do NOT ask for only 1 position when quantity > 1. The code generator CANNOT invent positions.

Example output for "创建两个结构柱" (quantity=2):
{{
  "intent": "custom",
  "confidence": 0.85,
  "api_method": "NewFamilyInstance",
  "slots": {{ "quantity": 2 }},
  "questions": [
    {{
      "slot": "column_type",
      "text": "请选择结构柱族类型：",
      "options": ["矩形柱 300×300mm", "矩形柱 300×450mm", "矩形柱 450×450mm", "圆柱 D300mm", "其他 (自定义)"],
      "values": ["300x300", "300x450", "450x450", "D300", "custom"],
      "enrich": "family_type:column"
    }},
    {{
      "slot": "level",
      "text": "放置在哪个标高？",
      "options": ["标高 1 (0mm)", "标高 2 (3000mm)", "标高 3 (6000mm)", "其他 (自定义)"],
      "values": ["Level 1", "Level 2", "Level 3", "custom"],
      "enrich": "level"
    }},
    {{
      "slot": "positions_array",
      "text": "请输入 2 个柱子的放置坐标（每个柱子一组 XYZ）：\\n柱 1: (x, y, z)\\n柱 2: (x, y, z)\\n格式示例: 1000,0,0; 5000,0,0",
      "options": ["其他 (自定义)"],
      "values": ["custom"],
      "enrich": "none"
    }}
  ]
}}

For English input with quantity=3:
{{
  "slot": "positions_array",
  "text": "Enter XYZ coordinates for 3 columns:\\nColumn 1: (x,y,z)\\nColumn 2: (x,y,z)\\nColumn 3: (x,y,z)\\nFormat: 1000,0,0; 5000,0,0; 9000,0,0",
  "options": ["Other (custom)"],
  "values": ["custom"],
  "enrich": "none"
}}

Example for "在墙上创建窗户" (window on host wall):
{{
  "slot": "host_wall",
  "text": "请在 Revit 中选择宿主墙体：",
  "options": ["在 Revit 中选择"],
  "values": ["pick"],
  "enrich": "host_pick"
}},
{{
  "slot": "window_type",
  "text": "请选择窗户类型：",
  "options": ["固定窗 600×900mm", "推拉窗 1200×1500mm", "其他 (自定义)"],
  "values": ["600x900", "1200x1500", "custom"],
  "enrich": "family_type:window"
}}

### Rule 9: MULTI-ACTION DECOMPOSITION
Some requests need multiple API calls (e.g., "create a room" needs walls first).
Use "action_plan" format for composite operations.

## AMBIGUITY DETECTION:
| User text | Possible meanings | Must ask |
|-----------|-------------------|----------|
| 背面 | 北面(north) vs 背面(rear) | Which one? |
| 前面 | 南面(south) vs 前面(entrance) | Which one? |
| 左边/右边 | Depends on viewing direction | From which direction? |
| 那面墙/这个 | Ambiguous reference | Must specify exactly |
| 大的/小的/标准 | Unknown dimensions | Must give concrete sizes |

## OUTPUT FORMAT (pure JSON, no markdown):

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
      "values": ["value_a", "value_b", "custom"],
      "enrich": "none|level|host_pick|family_type:<category>"
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
      "questions": [...]
    }}
  ],
  "summary": ""
}}

IMPORTANT: "summary" should ONLY appear when ALL questions are empty.

## User input:
"{user_input}"
"""


# ===================================================================
# LLM Prompt V2 — Skill-enhanced, modular prompt
# ===================================================================

_ANALYZE_PROMPT_V2 = """You are a Revit API Agent. Analyze user requests using the retrieved API documentation below.

## Core Rules:

{base_skill_rules}

{rag_context}

## Known intents (for classification):
{intent_list}

If the user's request doesn't match any known intent above, use intent="custom" with the
api_method derived from the API documentation. Do NOT reject unknown operations.

## Active Skills (MUST FOLLOW these intent-specific rules):
{skill_context}

## User input:
"{user_input}"
"""


# ===================================================================
# ConversationOrchestrator
# ===================================================================

class ConversationOrchestrator:
    """
    RAG-enhanced: dynamic search → query API docs → one LLM call → question queue → complete.

    process_turn(): called for initial user text (RAG + LLM call)
    answer_question(): called for each option selection (NO LLM, instant)
    """

    def __init__(
        self,
        llm: LLMAdapter | None = None,
        registry: SchemaRegistry | None = None,
        use_skills: bool = True,
    ):
        self._llm = llm or LLMAdapter()
        self._registry = registry or get_schema_registry()
        self._use_skills = use_skills

    async def process_turn(
        self, user_input: str, session: SessionState,
    ) -> TurnResponse:
        """Initial user text → dynamic RAG → LLM call → returns first question or complete."""
        session.touch()
        session.turn_count += 1
        session.add_message("user", user_input)

        lang = _detect_language(user_input)
        start = time.time()

        # Step 1: Extract search terms (sync, fast)
        search_terms = _extract_search_terms(user_input, self._registry)
        logger.info("Dynamic search terms: %s", search_terms)

        # Step 2: RAG lookup + skill matching in PARALLEL
        loop = asyncio.get_event_loop()
        rag_future = loop.run_in_executor(
            None, _query_api_by_method, search_terms, 8,
        )

        if self._use_skills:
            loader = get_skill_loader()
            skill_future = loop.run_in_executor(
                None, loader.match_skills, user_input, search_terms,
            )
            api_docs, matched_skills = await asyncio.gather(rag_future, skill_future)
        else:
            api_docs = await rag_future
            matched_skills = []

        rag_context = _format_api_context(api_docs)
        rag_duration = (time.time() - start) * 1000
        logger.info("API doc lookup: %.0fms, %d docs found", rag_duration, len(api_docs))

        # Step 3: Build prompt
        if self._use_skills and matched_skills:
            skill_context = loader.render_skill_prompt(matched_skills)
            base_rules = loader.get_base_rules()

            prompt = _ANALYZE_PROMPT_V2.format(
                base_skill_rules=base_rules,
                intent_list=self._registry.get_intent_summary(),
                rag_context=rag_context,
                skill_context=skill_context,
                user_input=user_input,
            )
            logger.info("Using skill-enhanced prompt (matched %d skills)", len(matched_skills))
        elif self._use_skills:
            base_rules = loader.get_base_rules()
            prompt = _ANALYZE_PROMPT_V2.format(
                base_skill_rules=base_rules,
                intent_list=self._registry.get_intent_summary(),
                rag_context=rag_context,
                skill_context="(No intent-specific skill matched — use Core Rules and API docs above.)",
                user_input=user_input,
            )
            logger.info("Using skill-enhanced prompt (no skills matched)")
        else:
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

        # --- Single action ---
        api_method = result.get("api_method", "")
        extracted_slots = result.get("slots", {})
        questions_raw = result.get("questions", [])
        summary = result.get("summary", "")

        # Flexible intent handling: unknown → try "custom", don't reject
        known_intents = self._registry.get_all_intent_names()
        if intent_name == "unknown" or (intent_name not in known_intents and intent_name != "composite"):
            if api_method:
                # LLM found an API method but not a known intent → use custom
                intent_name = "custom"
            else:
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
            # Add mapped_commands for each step
            mapped = self._registry.get_mapped_commands(step.intent)
            if mapped:
                step_output["mapped_commands"] = mapped
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
        """Apply LLM-extracted parameters directly."""
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

        # Get mapped commands from registry
        mapped_commands = self._registry.get_mapped_commands(intent.name)

        structured = {
            "$schema": "intent_bridge_output_v1",
            "intent": intent.name,
            "confidence": intent.confidence,
            "api_method": api_method,
            "parameters": slot_values,
            "execution": {
                "strategy": "solidified_tool",
                "mapped_commands": mapped_commands,
            },
            "metadata": {
                "session_id": session.session_id,
                "turns": session.turn_count,
                "parameter_sources": slot_sources,
            },
        }

        return structured

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
