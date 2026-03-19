"""
Interactive Selection Workflow — LLM-based intent classification + Revit querying.

When a user says "create structural column", the system:
1. Uses LLM to classify intent → needs family type + level selection
2. Queries Revit for available options
3. Presents choices to user
4. Feeds selections into CodeGenerator

When a user says "create window on wall", the system:
1. Uses LLM to classify intent → needs element selection + family type
2. Triggers Revit selection mode (user picks wall)
3. Queries available window types
4. Feeds wall ID + window type into CodeGenerator
"""
from __future__ import annotations

import json
import logging
import re
from enum import Enum

from mcp_bridge.revit_client import RevitClient, RevitResponse

_log = logging.getLogger("mcp_bridge.interactive")


class InteractionType(str, Enum):
    DIRECT = "direct"
    SELECT_FAMILY = "select_family"
    SELECT_ELEMENT = "select_element"
    SELECT_BOTH = "select_both"


# Category mapping — maps LLM-returned element_type to Revit OST category
_CATEGORY_MAP = {
    "wall": {"ost": "OST_Walls", "label": "墙族类型"},
    "structural_column": {"ost": "OST_StructuralColumns", "label": "结构柱族类型"},
    "column": {"ost": "OST_Columns", "label": "柱族类型"},
    "beam": {"ost": "OST_StructuralFraming", "label": "梁族类型"},
    "floor": {"ost": "OST_Floors", "label": "楼板族类型"},
    "window": {"ost": "OST_Windows", "label": "窗户族类型"},
    "door": {"ost": "OST_Doors", "label": "门族类型"},
    "ceiling": {"ost": "OST_Ceilings", "label": "天花板族类型"},
    "roof": {"ost": "OST_Roofs", "label": "屋顶族类型"},
    "railing": {"ost": "OST_StairsRailing", "label": "栏杆族类型"},
    "stair": {"ost": "OST_Stairs", "label": "楼梯族类型"},
    "furniture": {"ost": "OST_Furniture", "label": "家具族类型"},
    "plumbing": {"ost": "OST_PlumbingFixtures", "label": "卫浴族类型"},
    "lighting": {"ost": "OST_LightingFixtures", "label": "灯具族类型"},
    "mechanical": {"ost": "OST_MechanicalEquipment", "label": "机械设备族类型"},
    "electrical": {"ost": "OST_ElectricalEquipment", "label": "电气设备族类型"},
    "generic_model": {"ost": "OST_GenericModel", "label": "常规模型族类型"},
}

# Keyword → element_type fallback — used when LLM returns "other"
_KEYWORD_TO_ELEMENT = [
    (["家具", "桌", "椅", "沙发", "床", "柜", "furniture"], "furniture"),
    (["灯", "照明", "light"], "lighting"),
    (["卫浴", "洁具", "马桶", "水槽", "plumbing"], "plumbing"),
    (["设备", "空调", "mechanical"], "mechanical"),
    (["电气", "配电", "electrical"], "electrical"),
    (["墙", "wall"], "wall"),
    (["柱", "column"], "structural_column"),
    (["梁", "beam"], "beam"),
    (["窗", "window"], "window"),
    (["门", "door"], "door"),
    (["楼板", "floor", "板"], "floor"),
    (["天花", "ceiling"], "ceiling"),
    (["屋顶", "roof"], "roof"),
    (["栏杆", "railing"], "railing"),
    (["楼梯", "stair"], "stair"),
]

# Hosted element types — these need a host element (wall, floor, etc.)
_HOSTED_TYPES = {"window", "door"}

# LLM system prompt for intent classification
_CLASSIFY_SYSTEM = """\
You are a Revit intent classifier. Given a user query about Revit operations,
determine what type of interaction is needed.

Respond with ONLY valid JSON (no markdown, no explanation):
{
  "interaction_type": "direct|select_family|select_both",
  "element_type": "<see list below>",
  "need_level": true/false,
  "need_host": true/false,
  "select_prompt": "prompt for host selection if need_host=true, else null"
}

element_type must be one of:
  wall, structural_column, column, beam, floor, window, door, ceiling, roof,
  railing, stair, furniture, plumbing, lighting, mechanical, electrical,
  generic_model, other

Rules:
- "direct": No element selection needed (queries, info requests, deletions,
  modifications that do NOT involve changing an element's TYPE/family,
  purely informational or analytical requests)
- "select_family": User wants to CREATE an element OR CHANGE/MODIFY an element's
  TYPE/FAMILY TYPE. This requires presenting available family types for selection.
  ANY request that involves creating/placing a physical element should use this.
- "select_both": User wants to CREATE a HOSTED element that needs both:
  1. A host element (e.g., wall for windows/doors)
  2. A family type selection
- need_level: true if the element is placed at a specific level (creation only)
- need_host: true for hosted elements (windows, doors on walls; skylights on roofs)
- element_type: the PRIMARY element being created/modified.
  For complex queries mentioning multiple elements, pick the MAIN target element.
  If the element doesn't fit any specific type, use "generic_model".
  AVOID using "other" — only use it for non-creation operations.
- select_prompt: Chinese prompt asking user to select the host element in Revit

IMPORTANT: If the query involves CREATING or PLACING any physical element
(even if complex or multi-step), classify as "select_family" with the most
relevant element_type. Do NOT classify creation requests as "direct".

Examples:
- "创建结构柱" → select_family, structural_column, need_level=true
- "在墙上放窗户" → select_both, window, need_host=true
- "创建一面墙" → select_family, wall, need_level=true
- "放一个沙发" → select_family, furniture, need_level=true
- "创建灯具" → select_family, lighting, need_level=true
- "修改墙体类型" → select_family, wall, need_level=false
- "修改墙高度" → direct (not changing type)
- "删除所有柱子" → direct
"""


class IntentClassifier:
    """Classify user intent using LLM for ambiguous queries, with keyword fallback."""

    _llm = None  # lazy-loaded LLM client

    # Chinese number words → digits
    _CN_NUMS = {
        "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    }
    _QUANTITY_PATTERN = re.compile(
        r'([两二三四五六七八九十]|\d+)\s*[个根面道堵条块]'
    )

    # Patterns for extracting coordinates from queries
    _COORD_PATTERNS = [
        re.compile(
            r'[\(（]?\s*(-?\d+(?:\.\d+)?)\s*[,，\s]\s*(-?\d+(?:\.\d+)?)\s*[,，\s]\s*(-?\d+(?:\.\d+)?)\s*[\)）]?'
        ),
    ]
    _COORD_2D_PATTERN = re.compile(
        r'[\(（]?\s*(-?\d+(?:\.\d+)?)\s*[,，\s]\s*(-?\d+(?:\.\d+)?)\s*[\)）]?'
    )

    @classmethod
    def _get_llm(cls):
        """Lazy-load a fast LLM client for classification."""
        if cls._llm is None:
            try:
                from server.app.deps import get_config
                from pipeline.llm_client import create_llm_client
                config = get_config()
                cls._llm = create_llm_client(config)
            except Exception:
                _log.warning("[IntentClassifier] Failed to load LLM, using keyword fallback")
        return cls._llm

    def classify(self, user_query: str) -> dict:
        """
        Classify user query using LLM with keyword fallback.

        Returns:
            {
                "interaction_type": InteractionType,
                "queries": [...],
                "need_level": bool,
                "select_prompt": str|None,
                "parsed_coords": {"x": float, "y": float, "z": float|None} | None,
            }
        """
        coords = self._extract_coords(user_query)
        quantity = self._extract_quantity(user_query)

        # Try LLM classification first
        llm = self._get_llm()
        if llm:
            try:
                result = self._classify_with_llm(llm, user_query)
                if result:
                    result["parsed_coords"] = coords
                    result["quantity"] = quantity
                    _log.info(f"[classify] LLM result: type={result['interaction_type']} "
                              f"element={result.get('_element_type', '?')} "
                              f"quantity={quantity}")
                    return result
            except Exception as e:
                _log.warning(f"[classify] LLM classification failed: {e}")

        # Fallback: keyword-based
        _log.info("[classify] using keyword fallback")
        result = self._classify_keywords(user_query, coords)
        result["quantity"] = quantity
        return result

    def _classify_with_llm(self, llm, user_query: str) -> dict | None:
        """Use LLM to classify intent."""
        raw = llm.generate_text(user_query, system_prompt=_CLASSIFY_SYSTEM)

        # Strip markdown fences
        cleaned = re.sub(r'^```(?:json)?\s*\n?', '', raw.strip())
        cleaned = re.sub(r'\n?```\s*$', '', cleaned)

        data = json.loads(cleaned)
        itype = data.get("interaction_type", "direct")
        element_type = data.get("element_type", "other")
        need_level = data.get("need_level", False)
        need_host = data.get("need_host", False)
        select_prompt = data.get("select_prompt")

        # Map to our format
        if itype == "direct":
            return {
                "interaction_type": InteractionType.DIRECT.value,
                "queries": [],
                "need_level": False,
                "select_prompt": None,
                "_element_type": element_type,
            }

        # When LLM says select_family/select_both but element_type is unknown,
        # try keyword fallback to find a matching category
        if element_type == "other" or element_type not in _CATEGORY_MAP:
            resolved = self._resolve_element_type(user_query)
            if resolved:
                element_type = resolved
                _log.info(f"[classify] resolved 'other' → {element_type} via keywords")
            else:
                # Use generic_model as last resort for creation intents
                element_type = "generic_model"
                _log.info("[classify] no keyword match, using generic_model")

        # Build queries from element_type
        cat_info = _CATEGORY_MAP.get(element_type)
        if not cat_info:
            return None  # fall through to keyword

        queries = [{
            "command": "get_available_family_types",
            "params": {"categoryList": [cat_info["ost"]]},
            "label": cat_info["label"],
        }]

        # Determine interaction type
        if need_host or element_type in _HOSTED_TYPES:
            actual_type = InteractionType.SELECT_BOTH.value
            if not select_prompt:
                select_prompt = f"请在 Revit 中选择要放置{cat_info['label'][:2]}的宿主元素"
        else:
            actual_type = InteractionType.SELECT_FAMILY.value

        return {
            "interaction_type": actual_type,
            "queries": queries,
            "need_level": need_level,
            "select_prompt": select_prompt if actual_type == InteractionType.SELECT_BOTH.value else None,
            "_element_type": element_type,
        }

    @staticmethod
    def _classify_keywords(user_query: str, coords: dict | None) -> dict:
        """Fallback keyword-based classification."""
        query_lower = user_query.lower()

        # Check if query is about modifying/changing TYPE (not other properties)
        _type_change_keywords = ["类型", "type", "族型"]
        _modify_keywords = ["修改", "更换", "更改", "变更", "切换", "改变", "change", "modify"]
        is_type_change = (
            any(tk in query_lower for tk in _type_change_keywords)
            and any(mk in query_lower for mk in _modify_keywords)
        )

        # Keyword rules — ordered: hosted elements first
        _rules = [
            (["窗户", "window", "窗", "创建窗"], "window", InteractionType.SELECT_BOTH),
            (["门", "door", "创建门"], "door", InteractionType.SELECT_BOTH),
            (["结构柱", "structural column", "柱子"], "structural_column", InteractionType.SELECT_FAMILY),
            (["墙", "wall", "创建墙", "墙体"], "wall", InteractionType.SELECT_FAMILY),
            (["梁", "beam", "结构梁"], "beam", InteractionType.SELECT_FAMILY),
            (["楼板", "floor", "板"], "floor", InteractionType.SELECT_FAMILY),
        ]

        for keywords, elem_type, itype in _rules:
            for kw in keywords:
                if kw in query_lower:
                    cat_info = _CATEGORY_MAP[elem_type]
                    queries = [{
                        "command": "get_available_family_types",
                        "params": {"categoryList": [cat_info["ost"]]},
                        "label": cat_info["label"],
                    }]
                    select_prompt = None
                    if itype == InteractionType.SELECT_BOTH:
                        select_prompt = f"请在 Revit 中选择要放置{cat_info['label'][:2]}的宿主元素"
                    # For type change queries, always use SELECT_FAMILY and no level needed
                    if is_type_change:
                        itype = InteractionType.SELECT_FAMILY
                        select_prompt = None
                    return {
                        "interaction_type": itype.value,
                        "queries": queries,
                        "need_level": False if is_type_change else True,
                        "select_prompt": select_prompt,
                        "parsed_coords": coords,
                    }

        # Extended fallback — check _KEYWORD_TO_ELEMENT for broader coverage
        _creation_keywords = ["创建", "放置", "放", "添加", "新建", "create", "place", "add"]
        has_creation = any(ck in query_lower for ck in _creation_keywords)
        if has_creation:
            for keywords, elem_type in _KEYWORD_TO_ELEMENT:
                if any(kw in query_lower for kw in keywords):
                    cat_info = _CATEGORY_MAP.get(elem_type)
                    if cat_info:
                        return {
                            "interaction_type": InteractionType.SELECT_FAMILY.value,
                            "queries": [{
                                "command": "get_available_family_types",
                                "params": {"categoryList": [cat_info["ost"]]},
                                "label": cat_info["label"],
                            }],
                            "need_level": True,
                            "select_prompt": None,
                            "parsed_coords": coords,
                        }

        return {
            "interaction_type": InteractionType.DIRECT.value,
            "queries": [],
            "need_level": False,
            "select_prompt": None,
            "parsed_coords": coords,
        }

    @staticmethod
    def _resolve_element_type(query: str) -> str | None:
        """Try to resolve element_type from keywords when LLM returns 'other'."""
        q = query.lower()
        for keywords, elem_type in _KEYWORD_TO_ELEMENT:
            if any(kw in q for kw in keywords):
                return elem_type
        return None

    def _extract_quantity(self, query: str) -> int:
        """Extract quantity from Chinese number words like 两个/三面/5根.

        Returns 1 if no quantity found.
        """
        m = self._QUANTITY_PATTERN.search(query)
        if not m:
            return 1
        raw = m.group(1)
        if raw in self._CN_NUMS:
            return self._CN_NUMS[raw]
        try:
            return max(1, int(raw))
        except ValueError:
            return 1

    def _extract_coords(self, query: str) -> dict | None:
        """Extract coordinates from natural language query.

        Returns {"x": float, "y": float, "z": float|None} or None.
        """
        for pat in self._COORD_PATTERNS:
            m = pat.search(query)
            if m:
                return {
                    "x": float(m.group(1)),
                    "y": float(m.group(2)),
                    "z": float(m.group(3)),
                }
        m = self._COORD_2D_PATTERN.search(query)
        if m:
            return {
                "x": float(m.group(1)),
                "y": float(m.group(2)),
                "z": None,
            }
        return None

    @staticmethod
    def match_level_by_elevation(levels: list[dict], elevation_mm: float) -> str | None:
        """Find the level closest to a given elevation (mm).

        Returns level name or None if no levels available.
        """
        if not levels:
            return None
        best = None
        best_dist = float("inf")
        for lv in levels:
            elev = lv.get("ElevationMm", lv.get("elevation", 0))
            try:
                dist = abs(float(elev) - elevation_mm)
            except (ValueError, TypeError):
                continue
            if dist < best_dist:
                best_dist = dist
                best = lv.get("Name", lv.get("name", ""))
        return best


class RevitQueryExecutor:
    """Execute monorepo pre-built commands to query Revit model data."""

    def __init__(self, client: RevitClient):
        self.client = client

    async def get_family_types(self, categories: list[str]) -> list[dict]:
        """Query available family types by category via get_available_family_types command."""
        resp = await self.client.send_command(
            "get_available_family_types",
            {"categoryList": categories},
        )
        if resp.success and resp.result:
            return resp.result if isinstance(resp.result, list) else [resp.result]
        return []

    async def get_levels(self) -> list[dict]:
        """Query all levels via send_code_to_revit (no dedicated command for this)."""
        code = (
            'var levels = new FilteredElementCollector(document)\n'
            '    .OfClass(typeof(Level))\n'
            '    .Cast<Level>()\n'
            '    .OrderBy(l => l.Elevation)\n'
            '    .Select(l => new { Id = l.Id.Value, Name = l.Name, '
            'ElevationMm = Math.Round(l.Elevation * 304.8, 1) })\n'
            '    .ToList();\n'
            'return levels;'
        )
        resp = await self.client.send_code(code)
        if resp.success and resp.result:
            return resp.result if isinstance(resp.result, list) else [resp.result]
        return []

    async def trigger_selection(self) -> list[dict]:
        """Trigger Revit interactive pick mode — user clicks an element in Revit.

        Uses UIDocument.Selection.PickObject() which blocks until user picks.
        Falls back to get_selected_elements if PickObject fails.
        """
        # Use PickObject for interactive selection — this shows a Revit prompt
        pick_code = (
            'var uidoc = new UIDocument(document);\n'
            'var reference = uidoc.Selection.PickObject(\n'
            '    Autodesk.Revit.UI.Selection.ObjectType.Element,\n'
            '    "Please select a host element (wall/floor)");\n'
            'var element = document.GetElement(reference);\n'
            'return new {\n'
            '    Id = element.Id.Value,\n'
            '    Name = element.Name,\n'
            '    Category = element.Category != null ? element.Category.Name : ""\n'
            '};\n'
        )
        resp = await self.client.send_code(pick_code)
        if resp.success and resp.result:
            data = resp.result if isinstance(resp.result, list) else [resp.result]
            return data

        # Fallback: return currently selected elements
        _log.warning(f"[trigger_selection] PickObject failed: {resp.error}, trying get_selected_elements")
        resp = await self.client.send_command("get_selected_elements", {})
        if resp.success and resp.result:
            return resp.result if isinstance(resp.result, list) else [resp.result]
        return []

    async def get_selected_elements(self) -> list[dict]:
        """Get currently selected elements without triggering selection mode."""
        resp = await self.client.send_command("get_selected_elements", {})
        if resp.success and resp.result:
            return resp.result if isinstance(resp.result, list) else [resp.result]
        return []
