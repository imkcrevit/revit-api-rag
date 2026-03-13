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

    def classify(self, user_query: str) -> dict:
        """
        Classify user query by keyword matching.

        Returns:
            {
                "interaction_type": InteractionType,
                "queries": [...],        # Revit queries to execute
                "need_level": bool,      # whether to also query levels
                "select_prompt": str|None  # prompt for Revit element selection
            }
        """
        query_lower = user_query.lower()

        for rule in _INTENT_RULES:
            for kw in rule["keywords"]:
                if kw in query_lower:
                    return {
                        "interaction_type": rule["type"].value,
                        "queries": rule["queries"],
                        "need_level": rule.get("need_level", False),
                        "select_prompt": rule.get("select_prompt"),
                    }

        return {
            "interaction_type": InteractionType.DIRECT.value,
            "queries": [],
            "need_level": False,
            "select_prompt": None,
        }


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
