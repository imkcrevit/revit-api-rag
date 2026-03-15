"""
Interactive Selection Workflow — intent classification + Revit querying.

When a user says "create structural column", the system:
1. Classifies intent → needs family type + level selection
2. Queries Revit for available options
3. Presents choices to user
4. Feeds selections into CodeGenerator

When a user says "create window on wall", the system:
1. Classifies intent → needs element selection + family type
2. Triggers Revit selection mode (user picks wall)
3. Queries available window types
4. Feeds wall ID + window type into CodeGenerator
"""
from __future__ import annotations

import re
from enum import Enum

from mcp_bridge.revit_client import RevitClient, RevitResponse


class InteractionType(str, Enum):
    DIRECT = "direct"
    SELECT_FAMILY = "select_family"
    SELECT_ELEMENT = "select_element"
    SELECT_BOTH = "select_both"


# Intent keyword mapping (Chinese + English)
_INTENT_RULES: list[dict] = [
    {
        "keywords": ["结构柱", "structural column", "柱子"],
        "type": InteractionType.SELECT_FAMILY,
        "queries": [
            {"command": "get_available_family_types",
             "params": {"categoryList": ["OST_StructuralColumns"]},
             "label": "结构柱族类型"},
        ],
        "need_level": True,
    },
    {
        "keywords": ["墙", "wall", "创建墙"],
        "type": InteractionType.SELECT_FAMILY,
        "queries": [
            {"command": "get_available_family_types",
             "params": {"categoryList": ["OST_Walls"]},
             "label": "墙族类型"},
        ],
        "need_level": True,
    },
    {
        "keywords": ["梁", "beam", "结构梁"],
        "type": InteractionType.SELECT_FAMILY,
        "queries": [
            {"command": "get_available_family_types",
             "params": {"categoryList": ["OST_StructuralFraming"]},
             "label": "梁族类型"},
        ],
        "need_level": True,
    },
    {
        "keywords": ["楼板", "floor", "板"],
        "type": InteractionType.SELECT_FAMILY,
        "queries": [
            {"command": "get_available_family_types",
             "params": {"categoryList": ["OST_Floors"]},
             "label": "楼板族类型"},
        ],
        "need_level": True,
    },
    {
        "keywords": ["窗户", "window", "窗"],
        "type": InteractionType.SELECT_BOTH,
        "queries": [
            {"command": "get_available_family_types",
             "params": {"categoryList": ["OST_Windows"]},
             "label": "窗户族类型"},
        ],
        "select_prompt": "请在 Revit 中选择要放置窗户的墙体",
    },
    {
        "keywords": ["门", "door"],
        "type": InteractionType.SELECT_BOTH,
        "queries": [
            {"command": "get_available_family_types",
             "params": {"categoryList": ["OST_Doors"]},
             "label": "门族类型"},
        ],
        "select_prompt": "请在 Revit 中选择要放置门的墙体",
    },
]


class IntentClassifier:
    """Classify user intent to determine interaction type."""

    # Patterns for extracting coordinates from queries like:
    #   "在100,0,0"  "at 100,200,0"  "坐标100 200 0"  "(100, 200, 3600)"
    _COORD_PATTERNS = [
        # x,y,z or x, y, z (with optional parentheses)
        re.compile(
            r'[\(（]?\s*(-?\d+(?:\.\d+)?)\s*[,，\s]\s*(-?\d+(?:\.\d+)?)\s*[,，\s]\s*(-?\d+(?:\.\d+)?)\s*[\)）]?'
        ),
    ]
    # Fallback: just x,y (no z)
    _COORD_2D_PATTERN = re.compile(
        r'[\(（]?\s*(-?\d+(?:\.\d+)?)\s*[,，\s]\s*(-?\d+(?:\.\d+)?)\s*[\)）]?'
    )

    def classify(self, user_query: str) -> dict:
        """
        Classify user query by keyword matching + coordinate extraction.

        Returns:
            {
                "interaction_type": InteractionType,
                "queries": [...],
                "need_level": bool,
                "select_prompt": str|None,
                "parsed_coords": {"x": float, "y": float, "z": float|None} | None,
            }
        """
        query_lower = user_query.lower()
        coords = self._extract_coords(user_query)

        for rule in _INTENT_RULES:
            for kw in rule["keywords"]:
                if kw in query_lower:
                    return {
                        "interaction_type": rule["type"].value,
                        "queries": rule["queries"],
                        "need_level": rule.get("need_level", False),
                        "select_prompt": rule.get("select_prompt"),
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
        """Trigger Revit selection mode and return selected elements."""
        await self.client.send_command("operate_element", {"action": "Select"})
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
