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


# OST reference — provided to LLM as context, not used for hard-coded mapping
_OST_REFERENCE = {
    "OST_Walls": "墙", "OST_StructuralColumns": "结构柱", "OST_Columns": "柱",
    "OST_StructuralFraming": "梁/结构框架", "OST_Floors": "楼板",
    "OST_Windows": "窗户", "OST_Doors": "门",
    "OST_Ceilings": "天花板", "OST_Roofs": "屋顶",
    "OST_StairsRailing": "栏杆", "OST_Stairs": "楼梯",
    "OST_Furniture": "家具", "OST_FurnitureSystems": "家具系统",
    "OST_PlumbingFixtures": "卫浴洁具", "OST_LightingFixtures": "灯具",
    "OST_MechanicalEquipment": "机械设备", "OST_ElectricalEquipment": "电气设备",
    "OST_GenericModel": "常规模型",
    "OST_CurtainWallPanels": "幕墙嵌板", "OST_CurtainWallMullions": "幕墙竖梃",
    "OST_Rooms": "房间", "OST_Parking": "停车场",
    "OST_Site": "场地", "OST_Topography": "地形",
    "OST_Casework": "橱柜", "OST_SpecialityEquipment": "专用设备",
    "OST_Entourage": "环境", "OST_Planting": "植物",
}

# Hosted element categories — need a host element (wall, floor, etc.)
_HOSTED_CATEGORIES = {"OST_Windows", "OST_Doors"}

# LLM system prompt for intent classification
_CLASSIFY_SYSTEM = """\
You are a Revit intent classifier. Analyze the user query and determine
what interaction workflow is needed.

Respond with ONLY valid JSON (no markdown, no explanation):
{
  "interaction_type": "direct|select_family|select_both",
  "revit_categories": ["OST_xxx", ...],
  "label": "human-readable label for the family type selection",
  "need_level": true/false,
  "need_host": true/false,
  "select_prompt": "Chinese prompt for host selection, or null"
}

## interaction_type

- "direct": purely informational, deletion, property modification (NOT type/family
  change). No family selection needed.
- "select_family": user wants to CREATE an element, or CHANGE an element's
  TYPE/FAMILY. Requires querying Revit for available family types.
- "select_both": user wants to CREATE a HOSTED element (e.g., window on wall,
  door on wall). Needs host element selection + family type selection.

## revit_categories

The Revit BuiltInCategory OST names to query for available family types.
You may return one or more categories.

Common categories for reference (you are not limited to these):
  OST_Walls, OST_StructuralColumns, OST_Columns, OST_StructuralFraming,
  OST_Floors, OST_Windows, OST_Doors, OST_Ceilings, OST_Roofs,
  OST_StairsRailing, OST_Stairs, OST_Furniture, OST_FurnitureSystems,
  OST_PlumbingFixtures, OST_LightingFixtures,
  OST_MechanicalEquipment, OST_ElectricalEquipment,
  OST_GenericModel, OST_CurtainWallPanels, OST_Casework,
  OST_SpecialityEquipment, OST_Entourage, OST_Planting

If you are unsure which category fits, use OST_GenericModel.

## Other fields

- label: a short Chinese label describing what the user is selecting,
  e.g. "结构柱族类型", "家具族类型", "墙族类型"
- need_level: true only for element creation that requires a level
- need_host: true only for hosted elements
- select_prompt: Chinese prompt for host selection (null if not needed)

## Key rules

- ANY query involving creation/placement of a physical element → "select_family"
  or "select_both". NEVER classify creation as "direct".
- For complex multi-part queries (e.g., "创建房间并放置家具"), use the PRIMARY
  creation target's category. The code generator will handle multi-step logic.
- For queries mentioning multiple distinct element types to create, pick the
  categories for ALL of them in revit_categories.
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
        """Use LLM to classify intent — trusts LLM judgment for interaction type
        and Revit categories. No hard-coded element_type mapping."""
        raw = llm.generate_text(user_query, system_prompt=_CLASSIFY_SYSTEM)

        # Strip markdown fences
        cleaned = re.sub(r'^```(?:json)?\s*\n?', '', raw.strip())
        cleaned = re.sub(r'\n?```\s*$', '', cleaned)

        data = json.loads(cleaned)
        itype = data.get("interaction_type", "direct")
        need_level = data.get("need_level", False)
        need_host = data.get("need_host", False)
        select_prompt = data.get("select_prompt")
        categories = data.get("revit_categories", [])
        label = data.get("label", "族类型")

        # --- backward compat: if LLM returned old element_type format ---
        if not categories and data.get("element_type"):
            et = data["element_type"]
            ost = _OST_REFERENCE and f"OST_{et}" not in _OST_REFERENCE
            # Try common mapping
            _compat = {
                "wall": "OST_Walls", "structural_column": "OST_StructuralColumns",
                "column": "OST_Columns", "beam": "OST_StructuralFraming",
                "floor": "OST_Floors", "window": "OST_Windows",
                "door": "OST_Doors", "ceiling": "OST_Ceilings",
                "roof": "OST_Roofs", "furniture": "OST_Furniture",
                "generic_model": "OST_GenericModel",
            }
            if et in _compat:
                categories = [_compat[et]]
            elif et != "other":
                categories = ["OST_GenericModel"]

        if itype == "direct":
            return {
                "interaction_type": InteractionType.DIRECT.value,
                "queries": [],
                "need_level": False,
                "select_prompt": None,
            }

        # Ensure we have at least one category for non-direct
        if not categories:
            categories = ["OST_GenericModel"]
            _log.info("[classify] LLM returned no categories, using OST_GenericModel")

        queries = [{
            "command": "get_available_family_types",
            "params": {"categoryList": categories},
            "label": label,
        }]

        # Determine interaction type
        if need_host or any(c in _HOSTED_CATEGORIES for c in categories):
            actual_type = InteractionType.SELECT_BOTH.value
            if not select_prompt:
                select_prompt = f"请在 Revit 中选择宿主元素"
        else:
            actual_type = InteractionType.SELECT_FAMILY.value

        return {
            "interaction_type": actual_type,
            "queries": queries,
            "need_level": need_level,
            "select_prompt": select_prompt if actual_type == InteractionType.SELECT_BOTH.value else None,
        }

    @staticmethod
    def _classify_keywords(user_query: str, coords: dict | None) -> dict:
        """Fallback keyword-based classification (used only when LLM unavailable).

        Uses simple keyword hints to map to OST categories. Not meant to be
        exhaustive — the LLM path handles the full range of queries.
        """
        query_lower = user_query.lower()

        # Non-creation intents → direct (delete, query, list, etc.)
        _direct_keywords = ["删除", "delete", "remove", "查看", "查询", "列出",
                            "list", "获取", "get", "显示", "show", "统计", "count"]
        if any(dk in query_lower for dk in _direct_keywords):
            return {
                "interaction_type": InteractionType.DIRECT.value,
                "queries": [],
                "need_level": False,
                "select_prompt": None,
                "parsed_coords": coords,
            }

        # Check if query is about modifying/changing TYPE (not other properties)
        _type_change_keywords = ["类型", "type", "族型"]
        _modify_keywords = ["修改", "更换", "更改", "变更", "切换", "改变", "change", "modify"]
        is_type_change = (
            any(tk in query_lower for tk in _type_change_keywords)
            and any(mk in query_lower for mk in _modify_keywords)
        )

        # Keyword → (categories, label, interaction_type) — ordered by specificity
        _rules = [
            (["窗户", "窗", "window"],  ["OST_Windows"], "窗户族类型", InteractionType.SELECT_BOTH),
            (["门", "door"],            ["OST_Doors"], "门族类型", InteractionType.SELECT_BOTH),
            (["结构柱", "柱子"],         ["OST_StructuralColumns"], "结构柱族类型", InteractionType.SELECT_FAMILY),
            (["墙", "wall", "墙体"],    ["OST_Walls"], "墙族类型", InteractionType.SELECT_FAMILY),
            (["梁", "beam", "结构梁"],  ["OST_StructuralFraming"], "梁族类型", InteractionType.SELECT_FAMILY),
            (["楼板", "floor"],         ["OST_Floors"], "楼板族类型", InteractionType.SELECT_FAMILY),
            (["天花", "ceiling"],       ["OST_Ceilings"], "天花板族类型", InteractionType.SELECT_FAMILY),
            (["屋顶", "roof"],          ["OST_Roofs"], "屋顶族类型", InteractionType.SELECT_FAMILY),
            (["家具", "桌", "椅", "沙发", "床", "柜"], ["OST_Furniture"], "家具族类型", InteractionType.SELECT_FAMILY),
            (["灯", "照明"],            ["OST_LightingFixtures"], "灯具族类型", InteractionType.SELECT_FAMILY),
            (["栏杆", "railing"],       ["OST_StairsRailing"], "栏杆族类型", InteractionType.SELECT_FAMILY),
            (["楼梯", "stair"],         ["OST_Stairs"], "楼梯族类型", InteractionType.SELECT_FAMILY),
        ]

        for keywords, categories, label, itype in _rules:
            if any(kw in query_lower for kw in keywords):
                select_prompt = None
                if itype == InteractionType.SELECT_BOTH:
                    select_prompt = "请在 Revit 中选择宿主元素"
                if is_type_change:
                    itype = InteractionType.SELECT_FAMILY
                    select_prompt = None
                return {
                    "interaction_type": itype.value,
                    "queries": [{
                        "command": "get_available_family_types",
                        "params": {"categoryList": categories},
                        "label": label,
                    }],
                    "need_level": False if is_type_change else True,
                    "select_prompt": select_prompt,
                    "parsed_coords": coords,
                }

        # Creation-intent catch-all → generic_model
        _creation_keywords = ["创建", "放置", "放", "添加", "新建", "create", "place", "add"]
        if any(ck in query_lower for ck in _creation_keywords):
            _log.info("[classify/keywords] creation intent, no specific match → OST_GenericModel")
            return {
                "interaction_type": InteractionType.SELECT_FAMILY.value,
                "queries": [{
                    "command": "get_available_family_types",
                    "params": {"categoryList": ["OST_GenericModel"]},
                    "label": "族类型",
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
