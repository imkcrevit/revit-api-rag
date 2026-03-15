**English** | [中文](./usage-guide.md)

# Usage Guide — Intent Bridge Interactive Operations

This document demonstrates how to interact with Revit through the Gradio Web UI with AI assistance, covering two modes: single-step command execution and multi-step interactive selection.

> Prerequisites: Revit 2026 with the plugin installed ([installation instructions](../README.en.md#revit-plugin--v02)), and the status bar at the top of the page shows `Revit Connected`.

---

## Interface Overview

The UI consists of 5 steps, each corresponding to a collapsible panel:

| Step | Name | Description |
|------|------|-------------|
| Step 1 | **Input** | Enter a natural language instruction |
| Step 2 | **Select Options** | Select parameters such as family type, level, etc. in multi-step mode |
| Step 3 | **Review Code** | View the LLM-generated C# code and Thinking reasoning process |
| Step 4 | **Execute** | Send the code to Revit for execution and view results |
| Step 5 | **Solidify** | Save successful code as a reusable tool |

The progress bar at the top marks the current step in real time, and a timer is displayed in the upper-right corner.

---

## Mode 1: Single-Step Command Execution (Direct)

Suitable for queries, modifications, deletions, and other operations that **do not require** pre-selecting a family type or host element.

### Example Instructions

```
查询所有墙体的信息
删除所有结构柱
修改墙高度为 4000mm
获取当前选中元素的属性
列出所有楼层标高
```

### Execution Flow

```
Step 1: Input
    │  Input: "查询所有墙体的信息"
    │  Click "Generate Code"
    │
    ▼
Intent Classification → Direct (single-step)
    │
    ▼
Step 3: Review Code (skips Step 2)
    │  Pipeline progress log displayed step by step:
    │    ✓ Query Rewrite done
    │    ✓ Embedding generated
    │    ✓ Vector Search — API: 15, Code: 5
    │    ✓ Hydrating results from SQLite
    │    ✓ Combining API docs + SDK code into RAG context
    │    ✓ Assembling system prompt
    │    ● LLM generating... 28 lines, 156 tokens
    │    ✓ Code extracted & security reviewed — Safe
    │
    │  Thinking panel shows the LLM reasoning process (streaming)
    │  Code panel shows the generated C# code
    │
    ▼
Step 4: Execute
    │  After reviewing the code, click "Execute in Revit"
    │  → Code is sent to the Revit plugin via TCP
    │  → Roslyn dynamically compiles and executes it
    │  → Results are displayed in the UI
    │
    ▼
Step 5: Solidify (optional)
    │  After successful execution, enter a tool name and description
    │  Click "Solidify Tool" to save as a reusable tool
```

### Step-by-Step Instructions

1. **Enter instruction** — Type a natural language command in the Step 1 text box, e.g., `查询所有墙体的信息`
2. **Click Generate Code** — The system enters the pipeline automatically:
   - Intent is classified as `Direct`, skipping Step 2
   - Progress log displays each stage line by line
   - Thinking panel shows the LLM's chain of reasoning in real time
   - Code panel displays the final C# code
3. **Review code** — Expand the Step 3 panel to view the full code and confirm the security review status is `Safe`
4. **Execute** — Click `Execute in Revit` and wait for Revit to return results
5. **(Optional) Solidify** — If the code is worth reusing, enter a name and description in Step 5 and save

---

## Mode 2: Multi-Step Interaction — Family Type Selection (Select Family)

Suitable for **creating** elements that require specifying a family type, such as walls, structural columns, beams, floors, etc.

### Example Instructions

```
创建结构柱
在 (3000, 5000) 位置创建一面墙
放置一根梁
创建楼板
```

### Supported Element Types

| Instruction Keyword | Element Type | Revit Category | Requires Level |
|---------------------|-------------|----------------|----------------|
| 墙 / wall | Wall | OST_Walls | Yes |
| 结构柱 / structural column | Structural Column | OST_StructuralColumns | Yes |
| 梁 / beam | Beam | OST_StructuralFraming | Yes |
| 楼板 / floor | Floor | OST_Floors | Yes |
| 天花板 / ceiling | Ceiling | OST_Ceilings | Yes |
| 屋顶 / roof | Roof | OST_Roofs | Yes |
| 栏杆 / railing | Railing | OST_StairsRailing | Yes |
| 楼梯 / stair | Stair | OST_Stairs | Yes |

### Execution Flow

```
Step 1: Input
    │  Input: "创建结构柱"
    │  Click "Generate Code"
    │
    ▼
Intent Classification → Select Family (multi-step)
    │  System identifies requirements: family type + level
    │
    ▼
Query Revit
    │  → get_available_family_types(OST_StructuralColumns)
    │  → get_levels()
    │
    ▼
Step 2: Select Options
    │  Family Type dropdown: shows all available structural column family types in Revit
    │    e.g.: UC305x305x97, HEB200, W10x49 ...
    │  Level radio buttons: shows all levels
    │    e.g.: Level 1 (0mm), Level 2 (4000mm) ...
    │  X / Y input fields: placement coordinates (auto-filled if included in the instruction)
    │
    │  After selecting, click "Confirm & Generate Code"
    │
    ▼
Step 3: Review Code
    │  Pipeline streaming generation (same as single-step mode)
    │  LLM generates precise code based on the selected family type, level, and coordinates
    │  Thinking panel shows the reasoning process
    │
    ▼
Step 4: Execute
    │  After review, click "Execute in Revit"
    │  → Structural column is created in Revit
    │
    ▼
Step 5: Solidify (optional)
```

### Step-by-Step Instructions

1. **Enter instruction** — `创建结构柱` or `在 (3000, 5000) 位置创建一面墙`
2. **Click Generate Code** — The system performs intent classification, identifying it as a multi-step operation
3. **Wait for Revit query** — Progress shows `Querying Revit for family types...` → `Querying levels...`
4. **Select parameters** — The Step 2 panel opens automatically:
   - Select a family type from the **Family Type** dropdown (supports search filtering)
   - Select a placement level from the **Level** radio buttons
   - Confirm or modify the **X / Y** coordinates
5. **Click Confirm & Generate Code** — Enters SSE streaming code generation
6. **Review and execute** — Same as single-step mode: Step 3 → Step 4

### Automatic Coordinate Extraction

When coordinates are included in the instruction, the system automatically parses and fills the X/Y input fields:

```
在 (3000, 5000) 位置创建结构柱    → X=3000, Y=5000
创建墙 (1000, 2000, 4000)         → X=1000, Y=2000, Z=4000mm → auto-matches nearest level
```

---

## Mode 3: Multi-Step Interaction — Host Selection + Family Type (Select Both)

Suitable for **creating host-dependent elements**, such as placing windows on walls or installing doors on walls.

### Example Instructions

```
在墙上创建窗户
放置一扇门
选择一个墙体创建窗户
```

### Supported Host Element Types

| Instruction Keyword | Element Type | Requires Host |
|---------------------|-------------|---------------|
| 窗户 / window | Window | Wall |
| 门 / door | Door | Wall |

### Execution Flow

```
Step 1: Input
    │  Input: "在墙上创建窗户"
    │  Click "Generate Code"
    │
    ▼
Intent Classification → Select Both (multi-step + host selection)
    │  System identifies requirements: host element (wall) + window family type + level
    │
    ▼
Query Revit
    │  → get_available_family_types(OST_Windows)
    │  → get_levels()
    │
    ▼
Step 2: Select Options
    │  Status message: "Please select the wall to place the window on in Revit"
    │  Family Type dropdown: window family type list
    │  Level radio buttons: level list
    │
    │  [Host Selection Area — only shown in Select Both mode]
    │  Click "Select Host in Revit" button
    │    → Revit enters PickObject selection mode
    │    → User clicks a wall in Revit
    │    → UI displays: "Basic Wall (ID: 12345, Walls)"
    │
    │  After selection, click "Confirm & Generate Code"
    │
    ▼
Step 3 → Step 4 → Step 5 (same as above)
```

### Step-by-Step Instructions

1. **Enter instruction** — `在墙上创建窗户`
2. **Click Generate Code** — Classified as `Select Both`
3. **Select family type and level** — Choose from the dropdown and radio buttons
4. **Select host wall** — Click the `Select Host in Revit` button:
   - The Revit window will come to the foreground and enter element selection mode
   - **Click a wall** in the Revit view
   - The UI automatically displays the selected wall's name and ID
5. **Confirm and generate** — Click `Confirm & Generate Code`
6. **Execute** — After reviewing the code, click `Execute in Revit`

---

## Thinking Reasoning Process

The LLM produces a `<thinking>` reasoning process while generating code, showing its analytical logic:

```
Thinking:
I need to create a structural column at the specified position.

Step 1: Find the FamilySymbol for "UC305x305x97" using
        FilteredElementCollector with OST_StructuralColumns.
Step 2: Activate the symbol if not already active.
Step 3: Use Document.Create.NewFamilyInstance() to place the column
        at the given XYZ position on the specified level.
Step 4: Need to convert mm coordinates to feet (internal units).
```

The Thinking panel is located above Step 3, with a fixed height of 200px and scrollable content. It updates progressively during LLM streaming output.

---

## Pipeline Progress Log

Each code generation goes through 9 stages, with the progress log panel updating in real time:

| Stage | Description |
|-------|-------------|
| Query Rewrite | LLM rewrites the natural language input into API retrieval keywords |
| Embedding | Generates query vectors |
| Vector Search | ChromaDB semantic retrieval (API + SDK) |
| Hydrating | Retrieves full document content from SQLite |
| Combining | Merges API documentation and SDK code context |
| Assembling | Assembles the system prompt (rules + context + unit configuration) |
| LLM Generating | Streams C# code generation (displays line count and token count in real time) |
| Extracting | Extracts code blocks from LLM output |
| Security Review | Scans for dangerous API calls (Process.Start, File.Delete, etc.) |

---

## Solidified Tools — Tool Solidification and Reuse

Successfully executed code can be saved as a reusable tool, allowing direct invocation next time without regeneration.

### Solidification Steps

1. After successful code execution, expand the **Step 5: Solidify** panel
2. Enter a tool name (in English, e.g., `create_structural_column`)
3. Enter a tool description (e.g., `Create a structural column at a specified position`)
4. Click **Solidify Tool**

### Using Solidified Tools

1. Switch to the **Tool Library** tab
2. Click to select a tool from the tool list
3. Click **Load Choices** — The system automatically queries Revit for parameter options
   - Family type parameters → dropdown (dynamically queried from Revit)
   - Level parameters → dropdown (dynamically queried from Revit)
   - Other parameters → text input fields
4. Fill in the parameters and click **Run Tool**

---

## Unit Configuration

The system supports three units: `mm` (default), `m`, `feet`

- Project units are automatically detected from Revit on page load
- Can be manually switched in the **Settings** panel
- The LLM automatically inserts the correct unit conversion logic (mm/m → feet) when generating code

---

## Frequently Asked Questions

### Status bar shows Revit Disconnected

- Confirm that Revit 2026 is running and has loaded the `mcp-servers-for-revit` plugin
- Confirm that TCP port 18080 is not occupied
- Click the **Refresh** button to retry

### Family Type dropdown is empty

- The current Revit project may not have loaded families for the corresponding category
- Try loading the required family files in Revit first

### Code execution failed

- Check the security review status in Step 3
- Review the error message — common causes: family not activated, level does not exist, invalid element ID
- You can manually edit the code in the Code panel and re-execute

### Select Host button is unresponsive

- Confirm that the Revit window is not blocked by a dialog
- PickObject requires Revit to be in an interactive state (not executing a command)
- If PickObject fails, the system automatically falls back to reading the currently selected element
