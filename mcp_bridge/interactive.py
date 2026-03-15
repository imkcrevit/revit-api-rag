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
    "beam": {"ost": "OST_StructuralFraming", "label": "梁族类型"},
    "floor": {"ost": "OST_Floors", "label": "楼板族类型"},
    "window": {"ost": "OST_Windows", "label": "窗户族类型"},
    "door": {"ost": "OST_Doors", "label": "门族类型"},
    "ceiling": {"ost": "OST_Ceilings", "label": "天花板族类型"},
    "roof": {"ost": "OST_Roofs", "label": "屋顶族类型"},
    "railing": {"ost": "OST_StairsRailing", "label": "栏杆族类型"},
    "stair": {"ost": "OST_Stairs", "label": "楼梯族类型"},
}

# Hosted element types — these need a host element (wall, floor, etc.)
_HOSTED_TYPES = {"window", "door"}

# LLM system prompt for intent classification
_CLASSIFY_SYSTEM = """\
You are a Revit intent classifier. Given a user query about Revit operations,
determine what type of interaction is needed.

Respond with ONLY valid JSON (no markdown, no explanation):
{
  "interaction_type": "direct|select_family|select_both",
  "element_type": "wall|structural_column|beam|floor|window|door|ceiling|roof|other",
  "need_level": true/false,
  "need_host": true/false,
  "select_prompt": "prompt for host selection if need_host=true, else null"
}

Rules:
- "direct": No element selection needed (queries, modifications, deletions, info requests)
- "select_family": User wants to CREATE an element that needs family type selection
  (walls, columns, beams, floors, etc.)
- "select_both": User wants to CREATE a HOSTED element that needs both:
  1. A host element (e.g., wall for windows/doors)
  2. A family type selection
- need_level: true if the element is placed at a specific level
- need_host: true for hosted elements (windows, doors on walls; skylights on roofs)
- element_type: the PRIMARY element being created, not the host
  Example: "在墙上创建窗户" → element_type="window" (NOT "wall")
  Example: "创建一面墙" → element_type="wall"
- select_prompt: Chinese prompt asking user to select the host element in Revit

Examples:
- "创建结构柱" → select_family, structural_column, need_level=true
- "在墙上放窗户" → select_both, window, need_host=true, select_prompt="请在Revit中选择要放置窗户的墙体"
- "选择一个墙体创建窗户" → select_both, window, need_host=true
- "创建一面墙" → select_family, wall, need_level=true
- "修改墙高度" → direct
- "删除所有柱子" → direct
"""


class IntentClassifier:
    """Classify user intent using LLM for ambiguous queries, with keyword fallback."""

    _llm = None  # lazy-loaded LLM client

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

        # Try LLM classification first
        llm = self._get_llm()
        if llm:
            try:
                result = self._classify_with_llm(llm, user_query)
                if result:
                    result["parsed_coords"] = coords
                    _log.info(f"[classify] LLM result: type={result['interaction_type']} "
                              f"element={result.get('_element_type', '?')}")
                    return result
            except Exception as e:
                _log.warning(f"[classify] LLM classification failed: {e}")

        # Fallback: keyword-based
        _log.info("[classify] using keyword fallback")
        return self._classify_keywords(user_query, coords)

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
        if itype == "direct" or element_type == "other":
            return {
                "interaction_type": InteractionType.DIRECT.value,
                "queries": [],
                "need_level": False,
                "select_prompt": None,
                "_element_type": element_type,
            }

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

        # Keyword rules — ordered: hosted elements first
        _rules = [
            (["窗户", "window", "窗", "创建窗"], "window", InteractionType.SELECT_BOTH),
            (["门", "door", "创建门"], "door", InteractionType.SELECT_BOTH),
            (["结构柱", "structural column", "柱子"], "structural_column", InteractionType.SELECT_FAMILY),
            (["墙", "wall", "创建墙"], "wall", InteractionType.SELECT_FAMILY),
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
                    return {
                        "interaction_type": itype.value,
                        "queries": queries,
                        "need_level": True,
                        "select_prompt": select_prompt,
                        "parsed_coords": coords,
                    }

        return {
            "interaction_type": InteractionType.DIRECT.value,
            "queries": [],
            "need_level": False,
            "select_prompt": None,
            "parsed_coords": coords,
        }

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
