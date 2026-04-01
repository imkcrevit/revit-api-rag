"""
Atom Registry — enumerable Revit query & interaction primitives.

An "atom" is the smallest unit of Revit data retrieval or user interaction.
Tools declare parameter dependencies via atom keys; the runtime resolves
them automatically before execution.

Two kinds:
  - QueryAtom: auto-executed against Revit (no user action)
  - InteractiveAtom: requires user action in Revit UI (pick, select)

Usage:
    registry = AtomRegistry(revit_client)

    # Resolve a single atom
    levels = await registry.resolve("levels")

    # Resolve all atoms a tool needs
    choices = await registry.resolve_tool_params(tool)
    # → {"level_name": [{"label": "1F (0mm)", "value": "1F"}, ...], ...}
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable

from mcp_bridge.revit_client import RevitClient

_log = logging.getLogger("mcp_bridge.atom_registry")


# ── Atom Definitions ─────────────────────────────────────────────────────────

class AtomKind(str, Enum):
    QUERY = "query"             # auto-resolved, no user interaction
    INTERACTIVE = "interactive"  # needs user action in Revit


@dataclass
class AtomDef:
    """Definition of a single atom."""
    key: str                    # e.g. "levels", "family_types:OST_Walls"
    kind: AtomKind
    display_name: str           # e.g. "楼层 / Levels"
    description: str            # what this atom returns
    returns: str                # shape hint, e.g. "[{Id, Name, ElevationMm}]"
    parameterized: bool = False # True if key has a `:param` suffix


# ── Static atom catalog ──────────────────────────────────────────────────────
# Keys use the format:  base_name  or  base_name:{param}
# When parameterized=True, the part after ":" is a runtime argument.

ATOM_CATALOG: dict[str, AtomDef] = {}


def _register(key: str, kind: AtomKind, display_name: str,
              description: str, returns: str, parameterized: bool = False):
    ATOM_CATALOG[key] = AtomDef(
        key=key, kind=kind, display_name=display_name,
        description=description, returns=returns, parameterized=parameterized,
    )


# ── Query Atoms (auto-resolved) ─────────────────────────────────────────────

_register(
    "levels", AtomKind.QUERY,
    "楼层 / Levels",
    "All levels in the project, sorted by elevation",
    "[{Id, Name, ElevationMm}]",
)

_register(
    "views", AtomKind.QUERY,
    "视图 / Views",
    "All views in the project (plans, sections, 3D, etc.)",
    "[{Id, Name, ViewType}]",
)

_register(
    "phases", AtomKind.QUERY,
    "阶段 / Phases",
    "All phases in the project",
    "[{Id, Name}]",
)

_register(
    "worksets", AtomKind.QUERY,
    "工作集 / Worksets",
    "All worksets (workshared projects only)",
    "[{Id, Name, IsOpen}]",
)

_register(
    "materials", AtomKind.QUERY,
    "材质 / Materials",
    "All materials in the project",
    "[{Id, Name}]",
)

_register(
    "line_styles", AtomKind.QUERY,
    "线样式 / Line Styles",
    "All line styles (GraphicsStyle)",
    "[{Id, Name}]",
)

_register(
    "fill_patterns", AtomKind.QUERY,
    "填充图案 / Fill Patterns",
    "All fill patterns (drafting + model)",
    "[{Id, Name, Target}]",
)

_register(
    "rooms", AtomKind.QUERY,
    "房间 / Rooms",
    "All placed rooms with area and level info",
    "[{Id, Name, Number, Level, Area}]",
)

_register(
    "wall_types", AtomKind.QUERY,
    "墙类型 / Wall Types",
    "All wall types loaded in the project",
    "[{Id, Name, Width}]",
)

_register(
    "floor_types", AtomKind.QUERY,
    "楼板类型 / Floor Types",
    "All floor types loaded in the project",
    "[{Id, Name}]",
)

_register(
    "ceiling_types", AtomKind.QUERY,
    "天花板类型 / Ceiling Types",
    "All ceiling types loaded in the project",
    "[{Id, Name}]",
)

_register(
    "roof_types", AtomKind.QUERY,
    "屋顶类型 / Roof Types",
    "All roof types loaded in the project",
    "[{Id, Name}]",
)

# Parameterized atoms — key pattern: "family_types:{category}"
_register(
    "family_types", AtomKind.QUERY,
    "族类型 / Family Types",
    "Family types for a given BuiltInCategory (e.g. OST_Doors, OST_Furniture)",
    "[{Id, Name, FamilyName}]",
    parameterized=True,
)

_register(
    "elements", AtomKind.QUERY,
    "元素实例 / Element Instances",
    "Element instances of a given BuiltInCategory",
    "[{Id, Name, Category}]",
    parameterized=True,
)

# ── Interactive Atoms (need user action) ─────────────────────────────────────

_register(
    "pick_object", AtomKind.INTERACTIVE,
    "点选元素 / Pick Object",
    "User picks a single element in Revit viewport",
    "{Id, Name, Category}",
)

_register(
    "pick_point", AtomKind.INTERACTIVE,
    "点选坐标 / Pick Point",
    "User clicks a point in Revit viewport, returns XYZ",
    "{X, Y, Z}",
)

_register(
    "pick_edge", AtomKind.INTERACTIVE,
    "点选边 / Pick Edge",
    "User picks an edge of an element",
    "{ElementId, EdgeIndex, MidPoint}",
)

_register(
    "pick_face", AtomKind.INTERACTIVE,
    "点选面 / Pick Face",
    "User picks a face of an element",
    "{ElementId, FaceIndex, Normal}",
)

_register(
    "pick_objects", AtomKind.INTERACTIVE,
    "框选元素 / Pick Multiple Objects",
    "User selects multiple elements (box select or Ctrl+click)",
    "[{Id, Name, Category}]",
)

_register(
    "select_current", AtomKind.INTERACTIVE,
    "当前选择 / Current Selection",
    "Elements currently selected in Revit (no new pick action)",
    "[{Id, Name, Category}]",
)


# ── Atom Resolver ────────────────────────────────────────────────────────────

class AtomResolver:
    """Resolves atom keys into actual data from Revit."""

    def __init__(self, client: RevitClient):
        self.client = client
        # Cache query results within a single resolution session
        self._cache: dict[str, Any] = {}

    def clear_cache(self):
        self._cache.clear()

    async def resolve(self, atom_key: str, prompt: str | None = None) -> list[dict]:
        """Resolve a single atom key, returning a list of choice dicts.

        Args:
            atom_key: e.g. "levels", "family_types:OST_Doors", "pick_object"
            prompt: optional prompt text for interactive atoms

        Returns:
            List of {label, value} dicts for query atoms,
            or raw result dicts for interactive atoms.
        """
        if atom_key in self._cache:
            return self._cache[atom_key]

        # Parse parameterized keys: "family_types:OST_Doors" → base="family_types", param="OST_Doors"
        base, _, param = atom_key.partition(":")

        # Look up definition
        atom_def = ATOM_CATALOG.get(base)
        if atom_def is None:
            _log.warning(f"[resolve] Unknown atom key: {atom_key}")
            return []

        # Dispatch to appropriate resolver
        if atom_def.kind == AtomKind.QUERY:
            result = await self._resolve_query(base, param)
        else:
            result = await self._resolve_interactive(base, prompt)

        self._cache[atom_key] = result
        return result

    async def resolve_tool_params(self, tool_params: list[dict]) -> dict[str, list[dict]]:
        """Resolve all atom-sourced parameters for a tool.

        Args:
            tool_params: list of parameter defs, each may have a "source" field
                         with value like "query:levels" or "interactive:pick_object"

        Returns:
            {param_name: [{label, value}, ...]} for each param that has an atom source
        """
        choices: dict[str, list[dict]] = {}
        for p in tool_params:
            source = p.get("source", "")
            if not source or source in ("ask_user", "default", "compute"):
                continue

            # source format: "query:levels" or "interactive:pick_object"
            # or shorthand: "levels", "family_types:OST_Walls", "pick_object"
            atom_key = source.replace("query:", "").replace("interactive:", "")
            items = await self.resolve(atom_key)
            if items:
                choices[p["name"]] = items

        return choices

    # ── Query resolvers ──────────────────────────────────────────────────────

    async def _resolve_query(self, base: str, param: str) -> list[dict]:
        """Resolve a query atom by executing C# code or commands against Revit."""
        handler = self._QUERY_HANDLERS.get(base)
        if handler:
            return await handler(self, param)

        _log.warning(f"[resolve_query] No handler for query atom: {base}")
        return []

    async def _q_levels(self, _param: str) -> list[dict]:
        code = (
            'var levels = new FilteredElementCollector(document)\n'
            '    .OfClass(typeof(Level)).Cast<Level>()\n'
            '    .OrderBy(l => l.Elevation)\n'
            '    .Select(l => new { Id = l.Id.Value, Name = l.Name,\n'
            '        ElevationMm = Math.Round(l.Elevation * 304.8, 1) }).ToList();\n'
            'return levels;'
        )
        return await self._exec_code_as_choices(
            code, label_fn=lambda r: f"{r.get('Name','?')} ({r.get('ElevationMm',0)}mm)",
            value_fn=lambda r: r.get("Name", ""),
        )

    async def _q_views(self, _param: str) -> list[dict]:
        code = (
            'var views = new FilteredElementCollector(document)\n'
            '    .OfClass(typeof(View)).Cast<View>()\n'
            '    .Where(v => !v.IsTemplate)\n'
            '    .Select(v => new { Id = v.Id.Value, Name = v.Name,\n'
            '        ViewType = v.ViewType.ToString() }).ToList();\n'
            'return views;'
        )
        return await self._exec_code_as_choices(
            code, label_fn=lambda r: f"{r.get('Name','?')} [{r.get('ViewType','')}]",
            value_fn=lambda r: r.get("Name", ""),
        )

    async def _q_phases(self, _param: str) -> list[dict]:
        code = (
            'var phases = new FilteredElementCollector(document)\n'
            '    .OfClass(typeof(Phase)).Cast<Phase>()\n'
            '    .Select(p => new { Id = p.Id.Value, Name = p.Name }).ToList();\n'
            'return phases;'
        )
        return await self._exec_code_as_choices(code)

    async def _q_worksets(self, _param: str) -> list[dict]:
        code = (
            'if (!document.IsWorkshared) return new List<object>();\n'
            'var wsc = new FilteredWorksetCollector(document)\n'
            '    .OfKind(WorksetKind.UserWorkset);\n'
            'var result = wsc.Select(w => new { Id = w.Id.IntegerValue,\n'
            '    Name = w.Name, IsOpen = w.IsOpen }).ToList();\n'
            'return result;'
        )
        return await self._exec_code_as_choices(code)

    async def _q_materials(self, _param: str) -> list[dict]:
        code = (
            'var mats = new FilteredElementCollector(document)\n'
            '    .OfClass(typeof(Material)).Cast<Material>()\n'
            '    .OrderBy(m => m.Name)\n'
            '    .Select(m => new { Id = m.Id.Value, Name = m.Name }).ToList();\n'
            'return mats;'
        )
        return await self._exec_code_as_choices(code)

    async def _q_line_styles(self, _param: str) -> list[dict]:
        code = (
            'var cat = document.Settings.Categories.get_Item(BuiltInCategory.OST_Lines);\n'
            'var styles = cat.SubCategories.Cast<Category>()\n'
            '    .Select(c => new { Id = c.Id.Value, Name = c.Name }).ToList();\n'
            'return styles;'
        )
        return await self._exec_code_as_choices(code)

    async def _q_fill_patterns(self, _param: str) -> list[dict]:
        code = (
            'var pats = new FilteredElementCollector(document)\n'
            '    .OfClass(typeof(FillPatternElement)).Cast<FillPatternElement>()\n'
            '    .Select(p => new { Id = p.Id.Value, Name = p.Name,\n'
            '        Target = p.GetFillPattern().Target.ToString() }).ToList();\n'
            'return pats;'
        )
        return await self._exec_code_as_choices(code)

    async def _q_rooms(self, _param: str) -> list[dict]:
        code = (
            'var rooms = new FilteredElementCollector(document)\n'
            '    .OfCategory(BuiltInCategory.OST_Rooms)\n'
            '    .WhereElementIsNotElementType().Cast<Autodesk.Revit.DB.Architecture.Room>()\n'
            '    .Where(r => r.Area > 0)\n'
            '    .Select(r => new { Id = r.Id.Value, Name = r.get_Parameter(\n'
            '        BuiltInParameter.ROOM_NAME)?.AsString() ?? r.Name,\n'
            '        Number = r.Number, Level = r.Level?.Name ?? "",\n'
            '        Area = Math.Round(r.Area * 0.092903, 2) }).ToList();\n'
            'return rooms;'
        )
        return await self._exec_code_as_choices(
            code, label_fn=lambda r: f"{r.get('Number','')} {r.get('Name','?')} ({r.get('Level','')})",
            value_fn=lambda r: str(r.get("Id", "")),
        )

    async def _q_wall_types(self, _param: str) -> list[dict]:
        code = (
            'var types = new FilteredElementCollector(document)\n'
            '    .OfClass(typeof(WallType)).Cast<WallType>()\n'
            '    .Select(t => new { Id = t.Id.Value, Name = t.Name,\n'
            '        Width = Math.Round(t.Width * 304.8, 1) }).ToList();\n'
            'return types;'
        )
        return await self._exec_code_as_choices(
            code, label_fn=lambda r: f"{r.get('Name','?')} ({r.get('Width',0)}mm)",
        )

    async def _q_floor_types(self, _param: str) -> list[dict]:
        code = (
            'var types = new FilteredElementCollector(document)\n'
            '    .OfClass(typeof(FloorType)).Cast<FloorType>()\n'
            '    .Select(t => new { Id = t.Id.Value, Name = t.Name }).ToList();\n'
            'return types;'
        )
        return await self._exec_code_as_choices(code)

    async def _q_ceiling_types(self, _param: str) -> list[dict]:
        code = (
            'var types = new FilteredElementCollector(document)\n'
            '    .OfClass(typeof(CeilingType)).Cast<CeilingType>()\n'
            '    .Select(t => new { Id = t.Id.Value, Name = t.Name }).ToList();\n'
            'return types;'
        )
        return await self._exec_code_as_choices(code)

    async def _q_roof_types(self, _param: str) -> list[dict]:
        code = (
            'var types = new FilteredElementCollector(document)\n'
            '    .OfClass(typeof(RoofType)).Cast<RoofType>()\n'
            '    .Select(t => new { Id = t.Id.Value, Name = t.Name }).ToList();\n'
            'return types;'
        )
        return await self._exec_code_as_choices(code)

    async def _q_family_types(self, category: str) -> list[dict]:
        """Parameterized: family_types:OST_Doors → get types for that category."""
        if not category:
            return []
        resp = await self.client.send_command(
            "get_available_family_types",
            {"categoryList": [category]},
        )
        if resp.success and resp.result:
            items = resp.result if isinstance(resp.result, list) else [resp.result]
            return [
                {"label": t.get("name", t.get("Name", str(t))),
                 "value": t.get("name", t.get("Name", str(t)))}
                for t in items
            ]
        return []

    async def _q_elements(self, category: str) -> list[dict]:
        """Parameterized: elements:OST_Walls → get instances of that category."""
        if not category:
            return []
        code = (
            f'var elems = new FilteredElementCollector(document)\n'
            f'    .OfCategory(BuiltInCategory.{category})\n'
            f'    .WhereElementIsNotElementType()\n'
            f'    .Select(e => new {{ Id = e.Id.Value, Name = e.Name,\n'
            f'        Category = e.Category?.Name ?? "" }}).ToList();\n'
            f'return elems;'
        )
        return await self._exec_code_as_choices(
            code, label_fn=lambda r: f"{r.get('Name','?')} (ID:{r.get('Id','?')})",
            value_fn=lambda r: str(r.get("Id", "")),
        )

    # Handler dispatch table
    _QUERY_HANDLERS: dict[str, Callable[["AtomResolver", str], Awaitable[list[dict]]]] = {
        "levels":        _q_levels,
        "views":         _q_views,
        "phases":        _q_phases,
        "worksets":      _q_worksets,
        "materials":     _q_materials,
        "line_styles":   _q_line_styles,
        "fill_patterns": _q_fill_patterns,
        "rooms":         _q_rooms,
        "wall_types":    _q_wall_types,
        "floor_types":   _q_floor_types,
        "ceiling_types": _q_ceiling_types,
        "roof_types":    _q_roof_types,
        "family_types":  _q_family_types,
        "elements":      _q_elements,
    }

    # ── Interactive resolvers ────────────────────────────────────────────────

    async def _resolve_interactive(self, base: str, prompt: str | None) -> list[dict]:
        handler = self._INTERACTIVE_HANDLERS.get(base)
        if handler:
            return await handler(self, prompt)
        _log.warning(f"[resolve_interactive] No handler for: {base}")
        return []

    async def _i_pick_object(self, prompt: str | None) -> list[dict]:
        msg = prompt or "Please select an element"
        code = (
            'var uidoc = new UIDocument(document);\n'
            'var reference = uidoc.Selection.PickObject(\n'
            '    Autodesk.Revit.UI.Selection.ObjectType.Element,\n'
            f'    "{msg}");\n'
            'var el = document.GetElement(reference);\n'
            'return new { Id = el.Id.Value, Name = el.Name,\n'
            '    Category = el.Category != null ? el.Category.Name : "" };'
        )
        resp = await self.client.send_code(code)
        if resp.success and resp.result:
            data = resp.result if isinstance(resp.result, list) else [resp.result]
            return data
        return []

    async def _i_pick_point(self, prompt: str | None) -> list[dict]:
        msg = prompt or "Please click a point"
        code = (
            'var uidoc = new UIDocument(document);\n'
            f'var pt = uidoc.Selection.PickPoint("{msg}");\n'
            'return new { X = Math.Round(pt.X * 304.8, 1),\n'
            '    Y = Math.Round(pt.Y * 304.8, 1),\n'
            '    Z = Math.Round(pt.Z * 304.8, 1) };'
        )
        resp = await self.client.send_code(code)
        if resp.success and resp.result:
            data = resp.result if isinstance(resp.result, list) else [resp.result]
            return data
        return []

    async def _i_pick_edge(self, prompt: str | None) -> list[dict]:
        msg = prompt or "Please select an edge"
        code = (
            'var uidoc = new UIDocument(document);\n'
            'var reference = uidoc.Selection.PickObject(\n'
            '    Autodesk.Revit.UI.Selection.ObjectType.Edge,\n'
            f'    "{msg}");\n'
            'var el = document.GetElement(reference);\n'
            'return new { ElementId = el.Id.Value,\n'
            '    Name = el.Name };'
        )
        resp = await self.client.send_code(code)
        if resp.success and resp.result:
            data = resp.result if isinstance(resp.result, list) else [resp.result]
            return data
        return []

    async def _i_pick_face(self, prompt: str | None) -> list[dict]:
        msg = prompt or "Please select a face"
        code = (
            'var uidoc = new UIDocument(document);\n'
            'var reference = uidoc.Selection.PickObject(\n'
            '    Autodesk.Revit.UI.Selection.ObjectType.Face,\n'
            f'    "{msg}");\n'
            'var el = document.GetElement(reference);\n'
            'return new { ElementId = el.Id.Value,\n'
            '    Name = el.Name };'
        )
        resp = await self.client.send_code(code)
        if resp.success and resp.result:
            data = resp.result if isinstance(resp.result, list) else [resp.result]
            return data
        return []

    async def _i_pick_objects(self, prompt: str | None) -> list[dict]:
        msg = prompt or "Please select elements (press Finish when done)"
        code = (
            'var uidoc = new UIDocument(document);\n'
            'var refs = uidoc.Selection.PickObjects(\n'
            '    Autodesk.Revit.UI.Selection.ObjectType.Element,\n'
            f'    "{msg}");\n'
            'return refs.Select(r => {{\n'
            '    var el = document.GetElement(r);\n'
            '    return new {{ Id = el.Id.Value, Name = el.Name,\n'
            '        Category = el.Category != null ? el.Category.Name : "" }};\n'
            '}}).ToList();'
        )
        resp = await self.client.send_code(code)
        if resp.success and resp.result:
            data = resp.result if isinstance(resp.result, list) else [resp.result]
            return data
        return []

    async def _i_select_current(self, _prompt: str | None) -> list[dict]:
        resp = await self.client.send_command("get_selected_elements", {})
        if resp.success and resp.result:
            return resp.result if isinstance(resp.result, list) else [resp.result]
        return []

    _INTERACTIVE_HANDLERS: dict[str, Callable[["AtomResolver", str | None], Awaitable[list[dict]]]] = {
        "pick_object":    _i_pick_object,
        "pick_point":     _i_pick_point,
        "pick_edge":      _i_pick_edge,
        "pick_face":       _i_pick_face,
        "pick_objects":   _i_pick_objects,
        "select_current": _i_select_current,
    }

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _exec_code_as_choices(
        self, code: str,
        label_fn: Callable[[dict], str] | None = None,
        value_fn: Callable[[dict], str] | None = None,
    ) -> list[dict]:
        """Execute C# code and transform results into [{label, value}] format."""
        resp = await self.client.send_code(code)
        if not resp.success or not resp.result:
            _log.warning(f"[exec_code_as_choices] failed: {resp.error}")
            return []

        items = resp.result if isinstance(resp.result, list) else [resp.result]
        results = []
        for item in items:
            if isinstance(item, dict):
                label = label_fn(item) if label_fn else item.get("Name", str(item))
                value = value_fn(item) if value_fn else item.get("Name", str(item))
                results.append({"label": label, "value": value, "_raw": item})
            else:
                results.append({"label": str(item), "value": str(item)})
        return results


# ── Convenience ──────────────────────────────────────────────────────────────

def list_atoms() -> list[dict]:
    """Return all registered atoms as dicts (for API/MCP exposure)."""
    return [
        {
            "key": a.key,
            "kind": a.kind.value,
            "display_name": a.display_name,
            "description": a.description,
            "returns": a.returns,
            "parameterized": a.parameterized,
        }
        for a in ATOM_CATALOG.values()
    ]


def get_atom_keys() -> list[str]:
    """Return all registered atom keys."""
    return list(ATOM_CATALOG.keys())
