**English** | [中文](./tool-choices-flow.md)

# Tool Dynamic Choices — Logic and Flow Diagrams

## Design Principle

> Any multi-result parameter must use a list selection, never a hardcoded or manual choice.

Whenever a parameter value comes from multiple options in the Revit model (Level, FamilyType, FloorType, Element lists, etc.),
the system must query Revit at runtime to obtain the option list and let the user choose, rather than hardcoding with `.First()` or requiring manual input.

---

## Architecture Overview

```
┌──────────────┐     ┌─────────────────┐     ┌──────────────┐
│  Gradio UI   │     │  Claude Desktop  │     │ Other Clients │
│  (Tab D)     │     │  (MCP Client)    │     │  (REST API)  │
└──────┬───────┘     └────────┬─────────┘     └──────┬───────┘
       │                      │                       │
       │  HTTP REST           │  MCP Protocol         │  HTTP REST
       ▼                      ▼                       ▼
┌──────────────────────────────────────────────────────────────┐
│                    FastAPI Router                             │
│  GET  /tools/{name}/choices  ← Query dynamic param options   │
│  POST /tools/{name}/run     ← Execute tool with selections  │
├──────────────────────────────────────────────────────────────┤
│                    MCP Server (FastMCP)                       │
│  get_tool_choices(name)     ← Same functionality (MCP tool)  │
│  run_tool(name, params)     ← Same functionality (MCP tool)  │
└──────────────────┬───────────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────┐
│        ToolStore             │
│  get_dynamic_params(name)    │  ← Read YAML, extract choices_from
│  render_code(name, params)   │  ← Fill in template parameters
│  validate_params(name, p)    │  ← Validate + apply defaults
└──────────────────┬───────────┘
                   │
                   ▼
┌──────────────────────────────┐
│  RevitQueryExecutor / TCP    │  ← Query Revit model at runtime
│  get_levels()                │
│  get_family_types([cat])     │
│  send_code(dynamic query)    │
└──────────────────┬───────────┘
                   │ TCP JSON-RPC 2.0 (port 18080)
                   ▼
┌──────────────────────────────┐
│  Revit 2026 Plugin           │
│  (Roslyn Dynamic Compile)    │
└──────────────────────────────┘
```

---

## YAML Parameter `choices_from` Specification

| `choices_from` Value | Meaning | Query Method | Return Field |
|---|---|---|---|
| `levels` | All Levels in the model | `FilteredElementCollector.OfClass(Level)` | `Name` |
| `family_types:OST_XXX` | Family types of a given category | `get_available_family_types` command | `Name` |
| `floor_types` | All floor types | `FilteredElementCollector.OfClass(FloorType)` | `Name` |
| `elements:OST_XXX` | Model instances of a given category | `FilteredElementCollector.OfCategory(cat)` | `Id` |

### YAML Example

```yaml
parameters:
  - name: level_name
    type: string
    description: Target level name
    choices_from: levels          # ← Declare dynamic option source

  - name: type_name
    type: string
    description: Column family type
    choices_from: family_types:OST_StructuralColumns

  - name: x
    type: double
    description: X position (mm)
    default: '0'                  # ← Static default, no query needed
```

### Corresponding Code Template Changes

```diff
- var level = new FilteredElementCollector(document)
-     .OfClass(typeof(Level)).Cast<Level>()
-     .OrderBy(l => l.Elevation).First();

+ var level = new FilteredElementCollector(document)
+     .OfClass(typeof(Level)).Cast<Level>()
+     .FirstOrDefault(l => l.Name == "{level_name}");
+ if (level == null)
+     return new { Status = "Error", Message = "Level '{level_name}' not found" };
```

---

## Complete Flow (Step-by-Step Diagrams)

### Flow A: Gradio UI Tool Execution

```
User                     Gradio Tab D                  Router                    Revit
  │                          │                           │                         │
  │  1. Enter tool name      │                           │                         │
  │  (e.g. create_wall)      │                           │                         │
  │─────────────────────────>│                           │                         │
  │                          │                           │                         │
  │  2. Click [Load Choices] │                           │                         │
  │─────────────────────────>│                           │                         │
  │                          │  GET /tools/create_wall/  │                         │
  │                          │       choices             │                         │
  │                          │─────────────────────────>│                         │
  │                          │                           │                         │
  │                          │                           │  get_dynamic_params()   │
  │                          │                           │  → choices_from: levels │
  │                          │                           │                         │
  │                          │                           │  TCP: query levels      │
  │                          │                           │────────────────────────>│
  │                          │                           │                         │
  │                          │                           │  [L1(0mm), L2(3600mm)] │
  │                          │                           │<────────────────────────│
  │                          │                           │                         │
  │                          │  {level_name:             │                         │
  │                          │   [{label:"L1(0mm)",      │                         │
  │                          │     value:"L1"}, ...]}    │                         │
  │                          │<─────────────────────────│                         │
  │                          │                           │                         │
  │  3. Display option list  │                           │                         │
  │  + pre-fill JSON:        │                           │                         │
  │  {"level_name":"L1"}     │                           │                         │
  │<─────────────────────────│                           │                         │
  │                          │                           │                         │
  │  4. User selects/edits   │                           │                         │
  │     parameters           │                           │                         │
  │  {"level_name":"L2",     │                           │                         │
  │   "start_x":0, ...}     │                           │                         │
  │─────────────────────────>│                           │                         │
  │                          │                           │                         │
  │  5. Click [Run Tool]     │                           │                         │
  │─────────────────────────>│                           │                         │
  │                          │  POST /tools/create_wall/ │                         │
  │                          │        run                │                         │
  │                          │─────────────────────────>│                         │
  │                          │                           │                         │
  │                          │                           │  render_code()          │
  │                          │                           │  → Replace {level_name} │
  │                          │                           │    with "L2"            │
  │                          │                           │                         │
  │                          │                           │  TCP: send_code(C#)    │
  │                          │                           │────────────────────────>│
  │                          │                           │                         │
  │                          │                           │  {ElementId, Status}   │
  │                          │                           │<────────────────────────│
  │                          │                           │                         │
  │  6. Display result       │                           │                         │
  │  ✅ Created on L2        │                           │                         │
  │<─────────────────────────│                           │                         │
```

### Flow B: Claude Desktop (MCP) Tool Execution

```
User                     Claude Desktop              MCP Server                 Revit
  │                          │                           │                         │
  │  "Create a structural    │                           │                         │
  │   column on L2"          │                           │                         │
  │─────────────────────────>│                           │                         │
  │                          │                           │                         │
  │                          │  list_tools()             │                         │
  │                          │─────────────────────────>│                         │
  │                          │  → create_structural_     │                         │
  │                          │    column available        │                         │
  │                          │<─────────────────────────│                         │
  │                          │                           │                         │
  │                          │  get_tool_choices(        │                         │
  │                          │    "create_structural_    │                         │
  │                          │     column")              │                         │
  │                          │─────────────────────────>│                         │
  │                          │                           │  TCP: query levels     │
  │                          │                           │  TCP: query family     │
  │                          │                           │       types            │
  │                          │                           │────────────────────────>│
  │                          │                           │<────────────────────────│
  │                          │  {type_name: [...],       │                         │
  │                          │   level_name: [...]}      │                         │
  │                          │<─────────────────────────│                         │
  │                          │                           │                         │
  │                          │  (LLM selects params      │                         │
  │                          │   based on user intent    │                         │
  │                          │   + available choices)    │                         │
  │                          │                           │                         │
  │                          │  run_tool(                │                         │
  │                          │    "create_structural_    │                         │
  │                          │     column",              │                         │
  │                          │    '{"type_name":         │                         │
  │                          │      "M_SC_Reference      │                         │
  │                          │       Column",            │                         │
  │                          │      "level_name":"L2",   │                         │
  │                          │      "x":3000,"y":3000}') │                         │
  │                          │─────────────────────────>│                         │
  │                          │                           │  render + execute      │
  │                          │                           │────────────────────────>│
  │                          │                           │<────────────────────────│
  │                          │  ✅ Created               │                         │
  │                          │<─────────────────────────│                         │
  │                          │                           │                         │
  │  "Structural column      │                           │                         │
  │   created on L2,          │                           │                         │
  │   type M_SC_Reference"    │                           │                         │
  │<─────────────────────────│                           │                         │
```

### Flow C: Interactive Code Generation (Non-predefined Tools)

```
User                     Gradio Tab D                  Router                    Revit
  │                          │                           │                         │
  │  "Create structural      │                           │                         │
  │   column"                │                           │                         │
  │─────────────────────────>│                           │                         │
  │                          │  POST /classify-intent    │                         │
  │                          │─────────────────────────>│                         │
  │                          │  → SELECT_FAMILY,         │                         │
  │                          │    need_level: true       │                         │
  │                          │<─────────────────────────│                         │
  │                          │                           │                         │
  │                          │  POST /query-revit        │                         │
  │                          │  (get_available_family_   │                         │
  │                          │   types + get_levels)     │                         │
  │                          │─────────────────────────>│────────────────────────>│
  │                          │<─────────────────────────│<────────────────────────│
  │                          │                           │                         │
  │  Display dropdowns:      │                           │                         │
  │  [Family Type ▾]         │                           │                         │
  │  [Level ▾]               │                           │                         │
  │  [X] [Y]                 │                           │                         │
  │<─────────────────────────│                           │                         │
  │                          │                           │                         │
  │  Select + confirm        │                           │                         │
  │─────────────────────────>│                           │                         │
  │                          │  POST /generate-with-     │                         │
  │                          │    selections             │                         │
  │                          │─────────────────────────>│                         │
  │                          │  → RAG + LLM generate C#  │                         │
  │                          │<─────────────────────────│                         │
  │                          │                           │                         │
  │  Code preview + [Execute]│                           │                         │
  │<─────────────────────────│                           │                         │
```

---

## Related Files

| File | Responsibility |
|---|---|
| `mcp_bridge/tools/*.yaml` | Tool definitions; `choices_from` declares dynamic parameters |
| `mcp_bridge/tool_store.py` | `get_dynamic_params()` extracts parameters that require querying |
| `mcp_bridge/router.py` | `GET /tools/{name}/choices` REST endpoint |
| `mcp_bridge/mcp_server.py` | `get_tool_choices()` MCP tool |
| `mcp_bridge/interactive.py` | `RevitQueryExecutor` executes Revit queries |
| `mcp_bridge/frontend/app.py` | Gradio UI: Load Choices button + pre-fill |

## Currently Supported choices_from Types

| Type | Tools |
|---|---|
| `levels` | create_wall, create_beam, create_floor, create_structural_column |
| `family_types:OST_StructuralColumns` | create_structural_column |
| `family_types:OST_StructuralFraming` | create_beam |
| `floor_types` | create_floor |
| `elements:OST_Walls` | modify_wall_height |
