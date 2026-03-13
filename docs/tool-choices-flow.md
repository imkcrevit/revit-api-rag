# Tool Dynamic Choices — 操作逻辑与步骤图

## 设计原则

> 只要是 multi 结果就需要 list 而非自定义的选择

凡是参数值来自 Revit 模型中多个可选项（Level、FamilyType、FloorType、Element 列表等），
系统必须运行时查询 Revit 获取选项列表，由用户选择，而非 `.First()` 硬编码或手动输入。

---

## 架构总览

```
┌──────────────┐     ┌─────────────────┐     ┌──────────────┐
│  Gradio UI   │     │  Claude Desktop  │     │   其他客户端   │
│  (Tab D)     │     │  (MCP Client)    │     │  (REST API)  │
└──────┬───────┘     └────────┬─────────┘     └──────┬───────┘
       │                      │                       │
       │  HTTP REST           │  MCP Protocol         │  HTTP REST
       ▼                      ▼                       ▼
┌──────────────────────────────────────────────────────────────┐
│                    FastAPI Router                             │
│  GET  /tools/{name}/choices  ← 查询动态参数选项               │
│  POST /tools/{name}/run     ← 用选择的值执行工具              │
├──────────────────────────────────────────────────────────────┤
│                    MCP Server (FastMCP)                       │
│  get_tool_choices(name)     ← 同功能 MCP 工具                 │
│  run_tool(name, params)     ← 同功能 MCP 工具                 │
└──────────────────┬───────────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────┐
│        ToolStore             │
│  get_dynamic_params(name)    │  ← 读 YAML 提取 choices_from
│  render_code(name, params)   │  ← 填充模板参数
│  validate_params(name, p)    │  ← 校验 + 默认值
└──────────────────┬───────────┘
                   │
                   ▼
┌──────────────────────────────┐
│  RevitQueryExecutor / TCP    │  ← 实时查询 Revit 模型
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

## YAML 参数 `choices_from` 规范

| `choices_from` 值 | 含义 | 查询方式 | 返回值字段 |
|---|---|---|---|
| `levels` | 模型中所有 Level | `FilteredElementCollector.OfClass(Level)` | `Name` |
| `family_types:OST_XXX` | 指定类别的族类型 | `get_available_family_types` command | `Name` |
| `floor_types` | 所有楼板类型 | `FilteredElementCollector.OfClass(FloorType)` | `Name` |
| `elements:OST_XXX` | 指定类别的模型实例 | `FilteredElementCollector.OfCategory(cat)` | `Id` |

### YAML 示例

```yaml
parameters:
  - name: level_name
    type: string
    description: Target level name
    choices_from: levels          # ← 声明动态选项来源

  - name: type_name
    type: string
    description: Column family type
    choices_from: family_types:OST_StructuralColumns

  - name: x
    type: double
    description: X position (mm)
    default: '0'                  # ← 静态默认值，无需查询
```

### Code Template 对应变化

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

## 完整流程（步骤图）

### 流程 A: Gradio UI 工具执行

```
用户                     Gradio Tab D                  Router                    Revit
  │                          │                           │                         │
  │  1. 输入工具名            │                           │                         │
  │  (e.g. create_wall)      │                           │                         │
  │─────────────────────────>│                           │                         │
  │                          │                           │                         │
  │  2. 点击 [Load Choices]  │                           │                         │
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
  │  3. 显示选项列表          │                           │                         │
  │  + 预填 JSON:            │                           │                         │
  │  {"level_name":"L1"}     │                           │                         │
  │<─────────────────────────│                           │                         │
  │                          │                           │                         │
  │  4. 用户选择/修改参数     │                           │                         │
  │  {"level_name":"L2",     │                           │                         │
  │   "start_x":0, ...}     │                           │                         │
  │─────────────────────────>│                           │                         │
  │                          │                           │                         │
  │  5. 点击 [Run Tool]      │                           │                         │
  │─────────────────────────>│                           │                         │
  │                          │  POST /tools/create_wall/ │                         │
  │                          │        run                │                         │
  │                          │─────────────────────────>│                         │
  │                          │                           │                         │
  │                          │                           │  render_code()          │
  │                          │                           │  → 替换 {level_name}   │
  │                          │                           │    为 "L2"             │
  │                          │                           │                         │
  │                          │                           │  TCP: send_code(C#)    │
  │                          │                           │────────────────────────>│
  │                          │                           │                         │
  │                          │                           │  {ElementId, Status}   │
  │                          │                           │<────────────────────────│
  │                          │                           │                         │
  │  6. 显示执行结果          │                           │                         │
  │  ✅ Created on L2        │                           │                         │
  │<─────────────────────────│                           │                         │
```

### 流程 B: Claude Desktop (MCP) 工具执行

```
用户                     Claude Desktop              MCP Server                 Revit
  │                          │                           │                         │
  │  "在L2上创建结构柱"       │                           │                         │
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
  │                          │  (LLM 根据用户意图         │                         │
  │                          │   + choices 选择参数)      │                         │
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
  │  "已在L2创建结构柱,        │                           │                         │
  │   类型 M_SC_Reference"    │                           │                         │
  │<─────────────────────────│                           │                         │
```

### 流程 C: 交互式代码生成（非固化工具）

```
用户                     Gradio Tab D                  Router                    Revit
  │                          │                           │                         │
  │  "创建结构柱"             │                           │                         │
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
  │  展示下拉框:              │                           │                         │
  │  [Family Type ▾]         │                           │                         │
  │  [Level ▾]               │                           │                         │
  │  [X] [Y]                 │                           │                         │
  │<─────────────────────────│                           │                         │
  │                          │                           │                         │
  │  选择 + 确认              │                           │                         │
  │─────────────────────────>│                           │                         │
  │                          │  POST /generate-with-     │                         │
  │                          │    selections             │                         │
  │                          │─────────────────────────>│                         │
  │                          │  → RAG + LLM 生成 C#      │                         │
  │                          │<─────────────────────────│                         │
  │                          │                           │                         │
  │  代码预览 + [Execute]     │                           │                         │
  │<─────────────────────────│                           │                         │
```

---

## 涉及的文件

| 文件 | 职责 |
|---|---|
| `mcp_bridge/tools/*.yaml` | 工具定义，`choices_from` 声明动态参数 |
| `mcp_bridge/tool_store.py` | `get_dynamic_params()` 提取需查询的参数 |
| `mcp_bridge/router.py` | `GET /tools/{name}/choices` REST 端点 |
| `mcp_bridge/mcp_server.py` | `get_tool_choices()` MCP 工具 |
| `mcp_bridge/interactive.py` | `RevitQueryExecutor` 执行 Revit 查询 |
| `mcp_bridge/frontend/app.py` | Gradio UI: Load Choices 按钮 + 预填 |

## 当前已支持的 choices_from 类型

| 类型 | 工具 |
|---|---|
| `levels` | create_wall, create_beam, create_floor, create_structural_column |
| `family_types:OST_StructuralColumns` | create_structural_column |
| `family_types:OST_StructuralFraming` | create_beam |
| `floor_types` | create_floor |
| `elements:OST_Walls` | modify_wall_height |
