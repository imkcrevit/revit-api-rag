"""
Revit 操作定义 — 支持的 Text2Revit 操作模板

每个操作包含：
  - intent: 意图标识
  - api_method: 对应的 Revit API 方法
  - description: 操作描述（中英双语）
  - required_params: 必填参数（含类型、描述、验证）
  - optional_params: 可选参数（含默认值）
  - sql_pattern: 用于从 SQLite 查询 API 文档的模式
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParamDef:
    name: str
    type: str
    description_en: str
    description_zh: str
    default: str | None = None
    validation: str | None = None  # e.g. "positive_number", "point_3d"


@dataclass
class RevitAction:
    intent: str
    api_method: str
    description_en: str
    description_zh: str
    required_params: list[ParamDef] = field(default_factory=list)
    optional_params: list[ParamDef] = field(default_factory=list)
    sql_pattern: str = ""


# ---------------------------------------------------------------------------
# Action registry
# ---------------------------------------------------------------------------

ACTIONS: dict[str, RevitAction] = {}


def _register(action: RevitAction):
    ACTIONS[action.intent] = action


# --- Wall ---
_register(RevitAction(
    intent="CREATE_WALL",
    api_method="Wall.Create",
    description_en="Create a wall along a line",
    description_zh="沿直线创建墙体",
    required_params=[
        ParamDef("start_point", "point_3d", "Start point (x, y, z)", "起点坐标 (x, y, z)"),
        ParamDef("end_point", "point_3d", "End point (x, y, z)", "终点坐标 (x, y, z)"),
        ParamDef("height", "positive_number", "Wall height (meters)", "墙高 (米)"),
    ],
    optional_params=[
        ParamDef("wall_type", "string", "Wall type", "墙体类型",
                 default="Basic Wall"),
        ParamDef("structural", "boolean", "Is structural wall", "是否为结构墙",
                 default="false"),
        ParamDef("offset", "number", "Base offset from level", "距楼层的底部偏移",
                 default="0"),
    ],
    sql_pattern="%Wall.Create%",
))

# --- Column ---
_register(RevitAction(
    intent="CREATE_COLUMN",
    api_method="FamilyInstance (StructuralColumns)",
    description_en="Place a structural column",
    description_zh="放置结构柱",
    required_params=[
        ParamDef("location", "point_3d", "Column base point (x, y, z)", "柱底部坐标 (x, y, z)"),
        ParamDef("height", "positive_number", "Column height (meters)", "柱高 (米)"),
    ],
    optional_params=[
        ParamDef("column_type", "string", "Column family type", "柱族类型",
                 default="Rectangular Column"),
        ParamDef("rotation", "number", "Rotation angle (degrees)", "旋转角度 (度)",
                 default="0"),
    ],
    sql_pattern="%NewFamilyInstance%StructuralColumn%",
))

# --- Beam ---
_register(RevitAction(
    intent="CREATE_BEAM",
    api_method="FamilyInstance (StructuralFraming)",
    description_en="Place a structural beam",
    description_zh="放置结构梁",
    required_params=[
        ParamDef("start_point", "point_3d", "Beam start point (x, y, z)", "梁起点 (x, y, z)"),
        ParamDef("end_point", "point_3d", "Beam end point (x, y, z)", "梁终点 (x, y, z)"),
    ],
    optional_params=[
        ParamDef("beam_type", "string", "Beam family type", "梁族类型",
                 default="W Shapes-W10X49"),
        ParamDef("structural_type", "string", "Structural type (Beam/Brace)", "结构类型",
                 default="Beam"),
    ],
    sql_pattern="%NewFamilyInstance%StructuralFraming%",
))

# --- Floor / Slab ---
_register(RevitAction(
    intent="CREATE_FLOOR",
    api_method="Floor.Create",
    description_en="Create a floor/slab from boundary curves",
    description_zh="通过边界线创建楼板",
    required_params=[
        ParamDef("boundary_points", "point_list", "Boundary corner points [(x,y,z), ...]",
                 "边界角点列表 [(x,y,z), ...]"),
    ],
    optional_params=[
        ParamDef("floor_type", "string", "Floor type", "楼板类型",
                 default="Generic Floor"),
        ParamDef("structural", "boolean", "Is structural", "是否为结构楼板",
                 default="true"),
    ],
    sql_pattern="%Floor.Create%",
))

# --- Door ---
_register(RevitAction(
    intent="CREATE_DOOR",
    api_method="FamilyInstance (Doors)",
    description_en="Place a door in a wall",
    description_zh="在墙上放置门",
    required_params=[
        ParamDef("host_wall", "string", "Host wall identifier", "宿主墙标识"),
        ParamDef("location", "point_3d", "Door insertion point (x, y, z)", "门插入点 (x, y, z)"),
    ],
    optional_params=[
        ParamDef("door_type", "string", "Door family type", "门族类型",
                 default="Single-Flush"),
        ParamDef("width", "positive_number", "Door width (meters)", "门宽 (米)",
                 default="0.9"),
        ParamDef("height", "positive_number", "Door height (meters)", "门高 (米)",
                 default="2.1"),
    ],
    sql_pattern="%NewFamilyInstance%Door%",
))

# --- Window ---
_register(RevitAction(
    intent="CREATE_WINDOW",
    api_method="FamilyInstance (Windows)",
    description_en="Place a window in a wall",
    description_zh="在墙上放置窗户",
    required_params=[
        ParamDef("host_wall", "string", "Host wall identifier", "宿主墙标识"),
        ParamDef("location", "point_3d", "Window insertion point (x, y, z)", "窗插入点 (x, y, z)"),
    ],
    optional_params=[
        ParamDef("window_type", "string", "Window family type", "窗族类型",
                 default="Fixed"),
        ParamDef("width", "positive_number", "Window width (meters)", "窗宽 (米)",
                 default="1.2"),
        ParamDef("height", "positive_number", "Window height (meters)", "窗高 (米)",
                 default="1.5"),
        ParamDef("sill_height", "positive_number", "Sill height from floor (meters)",
                 "窗台高度 (米)", default="0.9"),
    ],
    sql_pattern="%NewFamilyInstance%Window%",
))


def get_action(intent: str) -> RevitAction | None:
    return ACTIONS.get(intent)


def get_all_intents() -> list[str]:
    return list(ACTIONS.keys())


def get_actions_summary() -> str:
    """Return a brief summary of all supported actions for LLM context."""
    lines = []
    for action in ACTIONS.values():
        params = ", ".join(p.name for p in action.required_params)
        lines.append(f"- {action.intent}: {action.description_en} / {action.description_zh} (params: {params})")
    return "\n".join(lines)
