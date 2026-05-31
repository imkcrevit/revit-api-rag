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

from prompts import load_prompt
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
    # -- 创建类 --
    "墙": ["Wall.Create", "WallType"],
    "楼板": ["Floor.Create", "FloorType"],
    "地板": ["Floor.Create", "FloorType"],
    "门": ["NewFamilyInstance", "Door"],
    "窗": ["NewFamilyInstance", "Window"],
    "窗户": ["NewFamilyInstance", "Window"],
    "房间": ["NewRoom", "Room.Create"],
    "柱": ["NewFamilyInstance", "Column"],
    "梁": ["NewFamilyInstance", "Beam", "FamilyInstance"],
    "屋顶": ["RoofBase", "FootPrintRoof"],
    "楼梯": ["Stairs", "StairsRun"],
    "栏杆": ["Railing"],
    "坡道": ["Ramp"],
    "幕墙": ["CurtainWall", "Wall.Create"],
    "族": ["FamilyInstance", "FamilySymbol"],
    # -- 修改类 --
    "删除": ["Document.Delete"],
    "修改": ["ElementTransformUtils", "Move", "Rotate"],
    "移动": ["ElementTransformUtils.Move"],
    "旋转": ["ElementTransformUtils.Rotate"],
    # -- 查询类 --
    "查询": ["FilteredElementCollector", "get_Parameter"],
    "获得": ["FilteredElementCollector", "get_Parameter"],
    "获取": ["FilteredElementCollector", "get_Parameter"],
    "搜索": ["FilteredElementCollector"],
    "筛选": ["FilteredElementCollector"],
    "统计": ["FilteredElementCollector", "get_Parameter"],
    "列出": ["FilteredElementCollector"],
    # -- 几何与计算类 --
    "净高": ["BoundingBox", "get_BoundingBox", "ReferenceIntersector"],
    "距离": ["Line.CreateBound", "XYZ.DistanceTo"],
    "碰撞": ["ReferenceIntersector", "ElementIntersectsElementFilter"],
    "相交": ["ReferenceIntersector", "ElementIntersectsElementFilter"],
    "包围盒": ["BoundingBox", "get_BoundingBox"],
    "坐标": ["XYZ", "Transform"],
    "几何": ["GeometryElement", "Solid", "Face"],
    # -- 链接模型类 --
    "链接": ["RevitLinkInstance", "RevitLinkType", "GetLinkDocument"],
    "链接模型": ["RevitLinkInstance", "RevitLinkType", "GetLinkDocument"],
    "外部参照": ["RevitLinkInstance"],
    # -- 视图类 --
    "当前视图": ["ActiveView", "FilteredElementCollector"],
    "视图": ["View", "ViewPlan", "View3D"],
    # -- 参数类 --
    "参数": ["get_Parameter", "BuiltInParameter"],
    "属性": ["get_Parameter", "BuiltInParameter"],
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
    Targets method names, class names, and property names.
    Filters out noise (BuiltInFailures, etc.).
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
            # Search both full_id and name for broader coverage
            conditions.append("(full_id LIKE ? OR name LIKE ?)")
            params.extend([f"%{pattern}%", f"%{pattern}%"])

        if not conditions:
            conn.close()
            return []

        where = " OR ".join(conditions)
        # Relaxed filter: allow docs without syntax/parameters (class-level docs,
        # properties, enums are useful for query/geometry operations)
        query = f"""
            SELECT full_id, name, summary, syntax, parameters, remark
            FROM revit_api
            WHERE ({where})
              AND full_id NOT LIKE '%Failures%'
              AND full_id NOT LIKE '%Exception%'
              AND full_id NOT LIKE '%UnitTypeId%'
              AND full_id NOT LIKE '%Obsolete%'
            ORDER BY
              CASE
                -- Prioritize items with syntax+parameters (method-level docs)
                WHEN syntax IS NOT NULL AND syntax != ''
                     AND parameters IS NOT NULL AND parameters != '' THEN 0
                -- Then class/property docs with summaries
                WHEN summary IS NOT NULL AND summary != '' THEN 1
                ELSE 2
              END,
              -- Within each tier, boost query/geometry APIs, demote creation/array
              CASE
                WHEN full_id LIKE '%Collector%' OR full_id LIKE '%Filter%' THEN 0
                WHEN full_id LIKE '%BoundingBox%' OR full_id LIKE '%Intersect%' THEN 0
                WHEN full_id LIKE '%LinkInstance%' OR full_id LIKE '%Transform%' THEN 0
                WHEN full_id LIKE '%Parameter%' OR full_id LIKE '%Override%' THEN 0
                WHEN full_id LIKE '%Geometry%' OR full_id LIKE '%Solid%' THEN 1
                WHEN full_id LIKE '%.Create%' OR full_id LIKE '%.New%' THEN 2
                WHEN full_id LIKE '%Array%' THEN 3
                ELSE 2
              END,
              length(COALESCE(parameters,'')) DESC
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

    Context-aware: detects whether the user intent is query/analysis vs creation,
    and prioritizes search terms accordingly.

    Strategy:
    1. Detect action context (query/create/modify/delete)
    2. Match registry keywords against input
    3. Map Chinese terms via _ZH_TO_API_KEYWORDS (filtered by context)
    4. Extract English technical terms via regex
    5. Fallback: use raw input words
    """
    # --- Step 0: Detect action context ---
    _QUERY_VERBS = {"获得", "获取", "查询", "查找", "搜索", "筛选", "列出", "统计",
                    "显示", "计算", "检测", "分析", "导出"}
    _CREATE_VERBS = {"创建", "新建", "添加", "放置", "画", "建"}
    # Element nouns that confirm true element creation context
    # "创建墙" = true creation, "创建视图着色" / "创建报表" = non-element creation
    _ELEMENT_NOUNS = {"墙", "柱", "梁", "板", "楼板", "门", "窗", "窗户", "屋顶",
                      "楼梯", "栏杆", "坡道", "幕墙", "房间", "族", "管道", "风管",
                      "桥架", "线管", "构件", "模型"}
    # Creation API patterns to deprioritize in query context
    _CREATION_API_PATTERNS = {
        "Wall.Create", "Floor.Create", "NewFamilyInstance", "NewRoom",
        "Room.Create", "RoofBase", "FootPrintRoof", "Stairs", "StairsRun",
        "Railing", "Ramp", "CurtainWall",
    }

    is_query_context = any(v in user_input for v in _QUERY_VERBS)
    # True element creation: "创建" must be followed by an element noun within 3 chars
    # e.g., "创建墙" → True, "创建视图着色" → False
    is_create_context = False
    for verb in _CREATE_VERBS:
        idx = user_input.find(verb)
        if idx >= 0:
            after = user_input[idx + len(verb):idx + len(verb) + 3]
            if any(noun in after for noun in _ELEMENT_NOUNS):
                is_create_context = True
                break

    terms: list[str] = []
    deprioritized: list[str] = []  # terms to add only if we don't have enough

    # 1. Registry keyword matching
    for intent_name in registry.get_all_intent_names():
        for kw in registry.get_intent_keywords(intent_name):
            if kw.lower() in user_input.lower():
                terms.append(kw)

    # 2. Chinese → API keyword mapping (context-filtered)
    for zh_term, api_terms in _ZH_TO_API_KEYWORDS.items():
        if zh_term in user_input:
            for api_term in api_terms:
                # In query context, deprioritize creation APIs from noun matches
                # (e.g., "梁" matches both "NewFamilyInstance" and "Beam" —
                #  in query context we want "Beam" but not "NewFamilyInstance")
                if is_query_context and not is_create_context and api_term in _CREATION_API_PATTERNS:
                    deprioritized.append(api_term)
                else:
                    terms.append(api_term)

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

    # Add deprioritized terms only if we have few primary terms
    if len(unique) < 3:
        for t in deprioritized:
            if t not in seen:
                seen.add(t)
                unique.append(t)

    # 4. Fallback: if nothing matched, use the raw input as search
    if not unique:
        words = re.findall(r'[a-zA-Z]{3,}', user_input)
        unique = words[:3] if words else [user_input[:50]]

    return unique


def _score_rag_quality(
    api_docs: list[dict], search_terms: list[str],
) -> tuple[float, list[str]]:
    """
    Score RAG result relevance (0.0–1.0). Returns (score, reasons).

    Checks:
    - How many docs have syntax/parameters (method-level, not just class stubs)
    - How many search terms actually appear in returned doc names
    - Whether results are dominated by irrelevant patterns (Array, Failure, etc.)
    """
    if not api_docs:
        return 0.0, ["no_results"]

    reasons: list[str] = []
    n = len(api_docs)

    # 1. Docs with actual method signatures (syntax + parameters)
    method_docs = sum(
        1 for d in api_docs
        if d.get("syntax") and d.get("parameters")
    )
    method_ratio = method_docs / n

    # 2. Term coverage: how many search terms appear in at least one doc name
    terms_lower = [t.lower() for t in search_terms if len(t) > 2]
    if terms_lower:
        covered = sum(
            1 for t in terms_lower
            if any(t in (d.get("name", "") or "").lower() for d in api_docs)
        )
        coverage = covered / len(terms_lower)
    else:
        coverage = 0.5  # neutral if no meaningful terms

    # 3. Noise ratio: docs matching irrelevant patterns
    _NOISE_PATTERNS = {"Array", "Failure", "Exception", "Obsolete", "UnitType",
                       "RadialArray", "LinearArray", "RebarContainer"}
    noise_count = sum(
        1 for d in api_docs
        if any(p in (d.get("name", "") or "") for p in _NOISE_PATTERNS)
    )
    noise_ratio = noise_count / n

    # Composite score
    score = (method_ratio * 0.3) + (coverage * 0.5) + ((1 - noise_ratio) * 0.2)

    if method_ratio < 0.3:
        reasons.append("few_method_docs")
    if coverage < 0.3:
        reasons.append("low_term_coverage")
    if noise_ratio > 0.3:
        reasons.append("high_noise")

    return round(score, 2), reasons


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

_ANALYZE_PROMPT = load_prompt("intent_bridge.analyze_legacy.md")

# ===================================================================
# LLM Prompt V2 — Skill-enhanced, modular prompt
# ===================================================================

_ANALYZE_PROMPT_V2 = load_prompt("intent_bridge.analyze.md")


# Dynamic parameters must be resolved from the live Revit model, not from
# LLM-fabricated option lists.
_DYNAMIC_ENRICH_FIXED = {"level", "host_pick"}
_FABRICATED_DEFAULT_PATTERNS = [
    re.compile(r"\bLevel\s*1\b", re.IGNORECASE),
    re.compile(r"标高\s*1"),
    re.compile(r"\bfirst\s+available\b", re.IGNORECASE),
    re.compile(r"\bdefault\b", re.IGNORECASE),
    re.compile(r"\bgeneric\s*-\s*\d+", re.IGNORECASE),
    re.compile(r"\(\s*0(?:\.0+)?\s*,\s*0(?:\.0+)?\s*,\s*0(?:\.0+)?\s*\)"),
    re.compile(r"\b0(?:\.0+)?\s*,\s*0(?:\.0+)?\s*,\s*0(?:\.0+)?\b"),
]


def _normalize_enrich(enrich: Any) -> str:
    """Normalize an LLM-provided enrich tag without losing category casing."""
    if not isinstance(enrich, str):
        return "none"
    raw = enrich.strip()
    if not raw:
        return "none"
    lower = raw.lower()
    if lower in _DYNAMIC_ENRICH_FIXED or lower == "none":
        return lower
    if lower.startswith("family_type:"):
        category = raw.split(":", 1)[1].strip()
        return f"family_type:{category}" if category else "none"
    return "none"


def _is_dynamic_enrich(enrich: str) -> bool:
    return enrich in _DYNAMIC_ENRICH_FIXED or enrich.startswith("family_type:")


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _sanitize_question_dict(raw: Any) -> dict[str, Any] | None:
    """Apply the question contract before runtime enrichment or UI display."""
    if not isinstance(raw, dict):
        return None

    slot = str(raw.get("slot", "")).strip()
    if not slot:
        return None

    enrich = _normalize_enrich(raw.get("enrich", "none"))
    text = str(raw.get("text", "")).strip() or slot
    options = _as_list(raw.get("options", []))
    values = _as_list(raw.get("values", []))
    allow_custom = raw.get("allow_custom", True)
    allow_custom = allow_custom if isinstance(allow_custom, bool) else True

    if _is_dynamic_enrich(enrich):
        # The live Revit query layer owns these choices. Keeping LLM-supplied
        # options here would make fabricated family types/levels look real.
        options = []
        values = []
        allow_custom = True
    elif options and not values:
        values = list(options)

    return {
        "slot": slot,
        "text": text,
        "options": options,
        "values": values,
        "allow_custom": allow_custom,
        "enrich": enrich,
    }


def _looks_like_fabricated_default(value: Any, user_input: str) -> bool:
    """Detect common defaults that should have been asked for instead."""
    if value is None:
        return False
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    if text and text.lower() in user_input.lower():
        return False
    return any(pattern.search(text) for pattern in _FABRICATED_DEFAULT_PATTERNS)


def _sanitize_slots_dict(
    raw_slots: Any,
    user_input: str,
    question_slots: set[str],
) -> dict[str, Any]:
    """Remove slots that conflict with pending questions or obvious defaults."""
    if not isinstance(raw_slots, dict):
        return {}

    cleaned: dict[str, Any] = {}
    for key, value in raw_slots.items():
        slot = str(key).strip()
        if not slot:
            continue
        if slot in question_slots:
            logger.info("[decode] dropping slot=%s because it is still a question", slot)
            continue
        if _looks_like_fabricated_default(value, user_input):
            logger.info("[decode] dropping slot=%s fabricated default=%r", slot, value)
            continue
        cleaned[slot] = value
    return cleaned


_ROOM_REQUIRED_QUESTIONS: list[dict[str, Any]] = [
    {
        "slot": "boundary_mode",
        "aliases": {"boundary_status", "room_boundary_mode", "boundary_mode"},
        "text": (
            "房间边界如何生成：使用已有闭合边界、创建围护墙，还是创建房间分隔线？ / "
            "How should the room boundary be created: use an existing closed boundary, create enclosing walls, or create room separation lines?"
        ),
        "options": [
            "使用已有闭合边界 / Use existing closed boundary",
            "创建围护墙 / Create enclosing walls",
            "创建房间分隔线 / Create room separation lines",
            "其他 (自定义) / Other (custom)",
        ],
        "values": [
            "existing_closed_boundary",
            "create_enclosing_walls",
            "create_room_separation_lines",
            "custom",
        ],
        "allow_custom": True,
        "enrich": "none",
    },
    {
        "slot": "level",
        "aliases": {"level", "base_level", "room_level"},
        "text": "选择创建房间的标高 / Select the level for the room",
        "options": [],
        "values": [],
        "allow_custom": True,
        "enrich": "level",
    },
    {
        "slot": "room_point",
        "aliases": {"point", "room_point", "room_location", "placement_point", "center_point", "location"},
        "text": "输入房间放置点或起始坐标 / Enter the room placement point or start coordinate",
        "options": [],
        "values": [],
        "allow_custom": True,
        "enrich": "none",
    },
    {
        "slot": "room_size_or_boundary",
        "aliases": {"room_size", "room_dimensions", "dimensions", "width_depth", "boundary_points", "room_boundary"},
        "text": "输入房间尺寸或边界点 / Enter the room size or boundary points",
        "options": [],
        "values": [],
        "allow_custom": True,
        "enrich": "none",
    },
    {
        "slot": "wall_type_or_thickness",
        "aliases": {"wall_type", "wall_thickness", "wall_type_or_thickness", "boundary_wall_type"},
        "text": "选择围护墙类型；如果需要新墙类型，请输入墙厚 / Select the enclosing wall type; if a new wall type is needed, enter wall thickness",
        "options": [],
        "values": [],
        "allow_custom": True,
        "enrich": "family_type:wall",
    },
    {
        "slot": "wall_height",
        "aliases": {"wall_height", "room_height", "height", "boundary_wall_height"},
        "text": "输入围护墙高度 / Enter the enclosing wall height",
        "options": [],
        "values": [],
        "allow_custom": True,
        "enrich": "none",
    },
    {
        "slot": "room_name",
        "aliases": {"room_name", "name"},
        "text": "输入房间名称 / Enter the room name",
        "options": [],
        "values": [],
        "allow_custom": True,
        "enrich": "none",
    },
    {
        "slot": "room_number",
        "aliases": {"room_number", "number"},
        "text": "输入房间编号 / Enter the room number",
        "options": [],
        "values": [],
        "allow_custom": True,
        "enrich": "none",
    },
    {
        "slot": "place_room_tag",
        "aliases": {"place_room_tag", "room_tag", "tag_room", "tag"},
        "text": "是否放置房间标签？ / Should a room tag be placed?",
        "options": [
            "放置房间标签 / Place room tag",
            "不放置房间标签 / Do not place room tag",
        ],
        "values": ["true", "false"],
        "allow_custom": False,
        "enrich": "none",
    },
]


def _slot_value_traceable(value: Any, user_input: str) -> bool:
    if value is None:
        return False
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    return bool(text.strip()) and text.lower() in user_input.lower()


def _ensure_create_room_questions(result: dict[str, Any], user_input: str) -> dict[str, Any]:
    """Create-room workflows need boundary and wall data beyond NewRoom args."""
    if result.get("intent") != "create_room":
        return result

    slots = result.get("slots") if isinstance(result.get("slots"), dict) else {}
    questions = result.get("questions") if isinstance(result.get("questions"), list) else []

    all_room_aliases = {
        str(alias).lower()
        for qdef in _ROOM_REQUIRED_QUESTIONS
        for alias in qdef["aliases"]
    }
    slots = {
        key: value
        for key, value in slots.items()
        if (
            str(key).strip().lower() not in all_room_aliases
            or _slot_value_traceable(value, user_input)
        )
    }

    present = {str(key).strip().lower() for key in slots}
    present.update(
        str(q.get("slot", "")).strip().lower()
        for q in questions
        if isinstance(q, dict)
    )

    for qdef in _ROOM_REQUIRED_QUESTIONS:
        aliases = {str(alias).lower() for alias in qdef["aliases"]}
        if present & aliases:
            continue
        question = {k: v for k, v in qdef.items() if k != "aliases"}
        sanitized = _sanitize_question_dict(question)
        if sanitized:
            questions.append(sanitized)
            present.add(sanitized["slot"].lower())

    result["questions"] = questions
    result["slots"] = slots
    return result


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
        extra_skill_context: str = "",
    ):
        self._llm = llm or LLMAdapter()
        self._registry = registry or get_schema_registry()
        self._use_skills = use_skills
        self._extra_skill_context = extra_skill_context.strip()

    def _with_extra_skill_context(self, skill_context: str) -> str:
        """Append runtime-managed active skills before the LLM extracts slots."""
        if not self._extra_skill_context:
            return skill_context
        if skill_context.strip():
            return (
                f"{skill_context}\n\n---\n\n"
                f"<!-- Runtime active skills -->\n{self._extra_skill_context}"
            )
        return f"<!-- Runtime active skills -->\n{self._extra_skill_context}"

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
        # Progressive RAG: up to 3 rounds, expanding scope if quality is low
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

        # --- Progressive expansion: evaluate quality, retry with broader scope ---
        rag_round = 1
        max_rounds = 3
        quality_threshold = 0.4
        rag_score, rag_reasons = _score_rag_quality(api_docs, search_terms)
        logger.info("RAG round %d: score=%.2f, reasons=%s, docs=%d",
                     rag_round, rag_score, rag_reasons, len(api_docs))

        while rag_score < quality_threshold and rag_round < max_rounds:
            rag_round += 1
            # Expansion strategy per round:
            if rag_round == 2:
                # Round 2: increase limit, add broader class-level terms
                expanded_terms = list(search_terms)
                # Add class-level fallbacks for common patterns
                _CLASS_FALLBACKS = {
                    "FilteredElementCollector": "Collector",
                    "RevitLinkInstance": "LinkInstance",
                    "BoundingBox": "BoundingBoxXYZ",
                    "get_Parameter": "Parameter",
                    "OverrideGraphicSettings": "OverrideGraphicSettings",
                    "ReferenceIntersector": "ReferenceIntersector",
                }
                for term in search_terms:
                    fb = _CLASS_FALLBACKS.get(term)
                    if fb and fb not in expanded_terms:
                        expanded_terms.append(fb)
                api_docs_new = await loop.run_in_executor(
                    None, _query_api_by_method, expanded_terms, 12,
                )
            else:
                # Round 3: most aggressive — use raw Chinese nouns as English search
                broadest = list(search_terms)
                # Extract all Chinese nouns and map to broad API terms
                for zh, apis in _ZH_TO_API_KEYWORDS.items():
                    if zh in user_input:
                        broadest.extend(apis)
                broadest = list(dict.fromkeys(broadest))  # dedupe, preserve order
                api_docs_new = await loop.run_in_executor(
                    None, _query_api_by_method, broadest, 15,
                )

            # Merge: keep existing good docs, add new non-duplicate ones
            existing_names = {d.get("name") for d in api_docs}
            for doc in api_docs_new:
                if doc.get("name") not in existing_names:
                    api_docs.append(doc)
                    existing_names.add(doc.get("name"))

            rag_score, rag_reasons = _score_rag_quality(api_docs, search_terms)
            logger.info("RAG round %d: score=%.2f, reasons=%s, docs=%d",
                         rag_round, rag_score, rag_reasons, len(api_docs))

        # Filter out noise docs before sending to LLM (keep top relevance)
        _NOISE_PATTERNS = {"RadialArray", "LinearArray", "RebarContainer",
                           "ArrayElement", "BaseArray"}
        api_docs = [
            d for d in api_docs
            if not any(p in (d.get("name", "") or "") for p in _NOISE_PATTERNS)
        ]
        # Cap at 10 docs to avoid prompt bloat
        api_docs = api_docs[:10]

        rag_context = _format_api_context(api_docs)
        rag_duration = (time.time() - start) * 1000
        logger.info("API doc lookup: %.0fms, %d docs found (after %d rounds)",
                     rag_duration, len(api_docs), rag_round)

        # Step 3: Build prompt
        if self._use_skills and matched_skills:
            skill_context = self._with_extra_skill_context(
                loader.render_skill_prompt(matched_skills)
            )
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
            skill_context = self._with_extra_skill_context(
                "(No intent-specific skill matched — use Core Rules and API docs above.)"
            )
            prompt = _ANALYZE_PROMPT_V2.format(
                base_skill_rules=base_rules,
                intent_list=self._registry.get_intent_summary(),
                rag_context=rag_context,
                skill_context=skill_context,
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
        result = self._sanitize_llm_result(result, user_input)

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
    def _sanitize_llm_result(result: Any, user_input: str) -> dict[str, Any]:
        """Normalize decoded LLM JSON before it can drive workflow state."""
        if not isinstance(result, dict):
            return {}

        sanitized = dict(result)
        questions = [
            q for q in (_sanitize_question_dict(q) for q in sanitized.get("questions", []))
            if q is not None
        ]
        question_slots = {q["slot"] for q in questions}
        sanitized["questions"] = questions
        sanitized["slots"] = _sanitize_slots_dict(
            sanitized.get("slots", {}),
            user_input,
            question_slots,
        )
        sanitized = _ensure_create_room_questions(sanitized, user_input)

        action_plan = sanitized.get("action_plan")
        if isinstance(action_plan, list):
            clean_steps = []
            for step in action_plan:
                if not isinstance(step, dict):
                    continue
                clean_step = dict(step)
                step_questions = [
                    q for q in (
                        _sanitize_question_dict(q)
                        for q in clean_step.get("questions", [])
                    )
                    if q is not None
                ]
                step_question_slots = {q["slot"] for q in step_questions}
                clean_step["questions"] = step_questions
                clean_step["slots"] = _sanitize_slots_dict(
                    clean_step.get("slots", {}),
                    user_input,
                    step_question_slots,
                )
                clean_step = _ensure_create_room_questions(clean_step, user_input)
                clean_steps.append(clean_step)
            sanitized["action_plan"] = clean_steps

        return sanitized

    @staticmethod
    def _parse_questions(questions_raw: list[dict]) -> list[QuestionItem]:
        """Parse raw question dicts into QuestionItem list."""
        items = []
        for raw in questions_raw:
            q = _sanitize_question_dict(raw)
            if not q:
                continue
            items.append(QuestionItem(
                slot=q["slot"],
                text=q["text"],
                options=q["options"],
                values=q["values"],
                allow_custom=q["allow_custom"],
                enrich=q["enrich"],
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
