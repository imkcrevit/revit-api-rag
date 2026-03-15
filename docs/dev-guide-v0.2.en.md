**English** | [中文](./dev-guide-v0.2.md)

# Revit AI Reasoning Stack — Developer Guide v0.2

> Detailed development task document derived from `revit-ai-reasoning-stack-v0.2.md`
> Date: 2026-03-13

---

## Architecture Premise: Dual Pipeline Sharing RAG

The system has two pipelines that share a single RAG retrieval layer and diverge only at the output:

```
                    ┌─── Pipeline A: Q&A (Tab A/B/C) ───→ LLM natural language explanation
User Input → RAG ──┤
                    └─── Pipeline B: Execution (Tab D) ───→ LLM generates C# code
                                                               ↓
                                                         RevitClient → Revit plugin execution
                                                               ↓
                                                         Solidified as reusable tool (ToolStore)
```

| | Pipeline A (Q&A) | Pipeline B (Execution) |
|---|---|---|
| Frontend | Gradio Tab A/B/C | Gradio Tab D |
| Shared Layer | RAGRetriever + LLMClient | RAGRetriever + LLMClient |
| System Prompt | `SYSTEM_PROMPT` (explanatory) | `SYSTEM_EXECUTE` (code generation) |
| Output | Natural language + API references | Executable C# code |
| Backend Chain | None | RevitClient → Revit → ToolStore |

**During Phase 2 frontend integration**: Tab switching equals pipeline switching — these are not two independent systems.

---

## Table of Contents

- [Module A: Revit Plugin Deployment and Communication Verification](#module-a-revit-plugin-deployment-and-communication-verification)
- [Module B: CodeGenerator Enhancement](#module-b-codegenerator-enhancement)
- [Module C: RevitClient Enhancement](#module-c-revitclient-enhancement)
- [Module D: ToolStore Enhancement](#module-d-toolstore-enhancement)
- [Module E: Gradio Tab D Frontend](#module-e-gradio-tab-d-frontend)
- [Module F: Security Sandbox and Error Retry](#module-f-security-sandbox-and-error-retry)
- [Module G: MCP Server Refinement](#module-g-mcp-server-refinement)
- [Module H: Integration Testing and Demo Preparation](#module-h-integration-testing-and-demo-preparation)
- [Module I: Interactive Selection Workflow](#module-i-interactive-selection-workflow)
- [Appendix: Completed Module Checklist](#appendix-completed-module-checklist-phase-0)

---

## Module A: Revit Plugin Deployment and Communication Verification

> **Priority: P0 (blocks all subsequent modules)**
> **Dependencies: None**
> **Deliverables: Revit running the monorepo plugin locally, Python connectivity confirmed, protocol and template verified**

### Phase 1 Execution Order and Confirmed Findings

**Principle: Confirm facts first, then write code.**

```
Step 1  ✅ Confirmed — Communication Protocol
        Source: plugin/Core/SocketService.cs
        Conclusion: raw TCP (TcpListener/TcpClient), NOT WebSocket
        Port: 8080 (hardcoded)
        Message format: JSON-RPC 2.0, UTF-8, no delimiter (raw bytes read, buffer 8192)

Step 2  ✅ Confirmed — send_code_to_revit exists
        Source: commandset/Commands/ExecuteDynamicCode/ExecuteCodeEventHandler.cs
        Code template: Roslyn compilation, public static object Execute(Document document, object[] parameters)
        Auto-injected usings: System, System.Linq, Autodesk.Revit.DB, Autodesk.Revit.UI, System.Collections.Generic
        Key finding: EventHandler already wraps Transaction — user code **must NOT** create another Transaction
        Timeout: 60s (RaiseAndWaitForCompletion(60000))

Step 3  ✅ Completed — revit_client.py adapted
        Conclusion: TCP protocol is correct, no need to migrate to WebSocket
        Adjustment: timeout changed from 120s to 60s (matching plugin side)
        Added: ping() method (say_hello command for connectivity check)

Step 4  ✅ Completed — TCP Connection Test
        Finding: Port 8080 was occupied by AdskLicensingAgent.exe
        Resolution: Switched to port 18080 (plugin + Python side updated simultaneously)
        Result: say_hello PASS, send_code_to_revit PASS

Step 5  ✅ Completed — Code Execution Verification
        Test: document.Title → returned "Project1"
        Test: document.Application.VersionNumber → returned "2026"

Step 6  ✅ Completed — Latency Measurement
        Ping average: ~1.3s (ExternalEvent queuing mechanism)
        Code execution: ~1.2s (including Roslyn compilation)
        Base latency ~1s is inherent to the Revit API
```

### A-1. Obtain Monorepo Plugin (Partial Clone)

| Item | Description |
|----|------|
| Source | https://github.com/mcp-servers-for-revit/mcp-servers-for-revit (monorepo, actively maintained) |
| Parts to keep | `plugin/` (Revit add-in main body) + `commandset/` (command implementations) + `command.json` (command definitions) |
| Parts to remove | `server/` (Node.js MCP Server, replaced by our Python MCP Server) |
| Key capabilities | TCP (port 18080, changed from 8080 to avoid AdskLicensingAgent conflict) + Roslyn dynamic compilation + 23 built-in commands (including `send_code_to_revit`) |

**Task List:**

```
A-1-1  Clone monorepo:
       git clone https://github.com/mcp-servers-for-revit/mcp-servers-for-revit.git
       cd mcp-servers-for-revit

A-1-2  ✅ Read key source code (Step 1 + Step 2):
       - plugin/Core/SocketService.cs → TCP (TcpListener), port 8080
       - plugin/Core/CommandExecutor.cs → JSON-RPC routing, deserialize JObject params
       - commandset/Commands/ExecuteDynamicCode/ → Roslyn compilation, Document document template
       - command.json → 23 commands, all confirmed

A-1-3  Keep only the plugin side:
       - Keep: plugin/          (Revit add-in C# project)
       - Keep: commandset/      (command implementations, including ExecuteDynamicCode)
       - Keep: command.json     (command definition list)
       - Remove: server/        (Node.js MCP Server, replaced by our Python implementation)
       - Keep: README.md, LICENSE and other root-level files

A-1-4  Add attribution note in the project root README.md:
       "## Third-party Components
        Revit plugin (plugin/ + commandset/) is sourced from
        [mcp-servers-for-revit](https://github.com/mcp-servers-for-revit/mcp-servers-for-revit)
        under [LICENSE]. Node.js MCP server has been replaced by our Python RAG-powered server."

A-1-5  Confirm plugin build target:
       - .NET 4.8 for Revit 2020-2024 / .NET 8 for Revit 2025-2026

A-1-6  Compile to DLL + .addin manifest

A-1-7  Deploy to Revit Addins directory:
       Revit 2026: %AppData%\Autodesk\Revit\Addins\2026\

A-1-8  Start Revit, confirm plugin loads successfully (check logs or Ribbon button)
```

### A-2. Communication Connectivity Verification (Step 3 + Step 4)

**Goal**: Confirm that `mcp_bridge/revit_client.py` can connect to the Revit plugin.

> **Note**: The verification method can only be determined after confirming the communication protocol in A-1-2.
> The current `revit_client.py` is based on TCP socket. If the plugin side uses WebSocket, Module C-0 migration must be executed first.

**Verification script** (create `tests/test_revit_connection.py`):

```python
"""
Verify connection to the Revit plugin.
Prerequisites: Revit is running, plugin is loaded.
Protocol: Choose TCP or WebSocket based on A-1-2 confirmation result.
"""
import asyncio
from mcp_bridge.revit_client import RevitClient

async def test_connection():
    client = RevitClient(host="localhost", port=8080)

    # Test 1: 连接
    await client.connect()
    assert client.connected, "连接失败"
    print("[PASS] 连接成功")

    # Test 2: say_hello（最简单的命令）
    resp = await client.send_command("say_hello", {"message": "ping from Python"})
    print(f"[{'PASS' if resp.success else 'FAIL'}] say_hello: {resp.result or resp.error}")

    # Test 3: send_code（Step 4 最小化测试）
    resp = await client.send_code(
        'TaskDialog.Show("Test", "Hello from RAG Bridge!");'
    )
    print(f"[{'PASS' if resp.success else 'FAIL'}] send_code: {resp.result or resp.error}")

    # Test 4: get_available_family_types（查询命令）
    resp = await client.send_command(
        "get_available_family_types",
        {"categoryList": ["OST_Walls"]}
    )
    print(f"[{'PASS' if resp.success else 'FAIL'}] get_available_family_types: {resp.result or resp.error}")

    await client.disconnect()

asyncio.run(test_connection())
```

**Acceptance Criteria:**

| # | Acceptance Item | Expected Result |
|---|--------|---------|
| A-2-1 | `RevitClient.connect()` succeeds | No exception thrown |
| A-2-2 | `say_hello` command | Revit shows a dialog box |
| A-2-3 | `send_code` minimal code | Revit shows a TaskDialog |
| A-2-4 | `get_available_family_types` | Returns family type list JSON |
| A-2-5 | Timeout test | Returns connection timeout error within 5 seconds after Revit is disconnected |

### A-3. Plugin-Side send_code_to_revit Execution Template (Confirmed)

> **Status**: ✅ Confirmed via cloned local source code.

**Confirmed Findings** (source: `commandset/Commands/ExecuteDynamicCode/ExecuteCodeEventHandler.cs`):

```
A-3-1  Plugin-side code template (Roslyn compilation):

       using System;
       using System.Linq;
       using Autodesk.Revit.DB;
       using Autodesk.Revit.UI;
       using System.Collections.Generic;

       namespace AIGeneratedCode
       {
           public static class CodeExecutor
           {
               public static object Execute(Document document, object[] parameters)
               {
                   // === USER CODE HERE ===
                   {code}
                   // === END USER CODE ===
               }
           }
       }

       ✅ Confirmed: variable name is `document` (not `doc`), parameter is `parameters` (object[])
       ✅ Confirmed: this is a static method, not IExternalCommand.Execute
       ✅ Key finding: EventHandler.Execute() already wraps Transaction
          → User code **must NOT** create another Transaction, otherwise nested transaction error

A-3-2  Auto-injected using statements (5, confirmed):
       - using System;
       - using System.Linq;
       - using Autodesk.Revit.DB;
       - using Autodesk.Revit.UI;
       - using System.Collections.Generic;

A-3-3  Compiler: ✅ Roslyn (Microsoft.CodeAnalysis.CSharp)
       References all loaded assemblies (AppDomain.CurrentDomain.GetAssemblies())

A-3-4  Compilation error reporting: ✅ Error list with line numbers
       Format: "Line {n}: {error message}", multiple errors joined with newlines

A-3-5  Runtime exception reporting: ✅ JSON response
       { "success": false, "errorMessage": "执行失败: {ex.Message}" }
```

**Deliverable**: ✅ `mcp_bridge/code_generator.py:SYSTEM_EXECUTE` has been updated with the confirmed template.
Key changes: `doc` → `document`, removed Transaction rule (plugin already wraps it), added return requirement.

### A-4. Monorepo Command List (Confirmed)

> ✅ Confirmed via local command.json after cloning, 23 commands total.

| # | Command | Purpose | Module I Related |
|---|---------|------|---------------|
| 1 | `say_hello` | Connectivity test | |
| 2 | `get_available_family_types` | Query available family types by category | **Core** |
| 3 | `get_current_view_elements` | Get elements in current view | |
| 4 | `get_current_view_info` | Get current view information | |
| 5 | `get_selected_elements` | Get user-selected elements | **Core** |
| 6 | `create_point_based_element` | Create point-based family instance | |
| 7 | `create_line_based_element` | Create line-based family instance | |
| 8 | `create_surface_based_element` | Create surface-based family instance | |
| 9 | `color_splash` | Color elements by condition | |
| 10 | `tag_walls` | Tag walls | |
| 11 | `delete_element` | Delete element | |
| 12 | `ai_element_filter` | AI-based conditional element filtering | |
| 13 | `operate_element` | Operate on element (includes Select mode) | **Core** |
| 14 | `export_room_data` | Export room data | |
| 15 | `get_material_quantities` | Get material quantities | |
| 16 | `analyze_model_statistics` | Model statistics | |
| 17 | `create_grid` | Create grid | |
| 18 | `create_structural_framing_system` | Create structural framing | |
| 19 | `create_room` | Create room | |
| 20 | `tag_rooms` | Tag rooms | |
| 21 | `create_level` | Create level | |
| 22 | `send_code_to_revit` | Send arbitrary C# code for execution | |
| 23 | `create_dimensions` | Create dimensions | |

### A-5. End-to-End Latency Measurement (Step 6)

After Step 5 passes, do full timing on the structural column creation case:

```
A-5-1  Timing checkpoints:
       t0: User input
       t1: Query Rewriting complete
       t2: ChromaDB search complete
       t3: SQLite hydration complete
       t4: LLM code generation complete
       t5: Transmission to Revit complete
       t6: Revit compilation + execution complete
       t7: Result returned to Python

A-5-2  Record per-stage latency, write into Module H-4 as baseline
```

---

## Module B: CodeGenerator Enhancement

> **Priority: P1**
> **Dependencies: Module A Step 2 ✅ Completed**
> **File: `mcp_bridge/code_generator.py`**
> **Current status: ✅ Updated to monorepo template (Document document + no Transaction)**

### B-1. ✅ Fix SYSTEM_EXECUTE Prompt (Completed)

**Completed changes**:
- Template changed from `IExternalCommand` to `static object Execute(Document document, object[] parameters)`
- Variable name `doc` → `document`
- **Removed Transaction rule** (plugin EventHandler already wraps it, user code must not create another)
- Added return requirement (method must return object)
- Added selections context injection (interactive selection results)

**Reference (already applied to code)**:

```python
SYSTEM_EXECUTE = """\
You are a Revit {revit_version} API expert. Generate C# code that will be compiled and
executed inside a Revit plugin via Roslyn dynamic compilation.

## Execution Context
Your code runs inside this static method — write ONLY the method body:

```csharp
using System;
using System.Linq;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using System.Collections.Generic;

namespace AIGeneratedCode
{{
    public static class CodeExecutor
    {{
        public static object Execute(Document document, object[] parameters)
        {{
            // === YOUR CODE HERE ===
            {{user_code}}
            // === END YOUR CODE ===
        }}
    }}
}}
```

## CRITICAL: Available Variables
- `document` — the active Revit Document (NOT `doc`)
- `parameters` — object[] passed from caller (may be null)
- The method MUST return an object (return null if no meaningful result)

## Auto-injected usings (do NOT repeat these):
- System, System.Linq, System.Collections.Generic
- Autodesk.Revit.DB, Autodesk.Revit.UI

## Rules
1. Use only Revit {{revit_version}} API. Do not invent methods.
2. Output ONLY the method body (no class/namespace/using).
3. Always wrap modifications in a Transaction:
   Transaction tx = new Transaction(document, "description");
   tx.Start();
   // ... operations ...
   tx.Commit();
4. Use variable `document` (NOT `doc`, `uidoc`, or `uiapp`).
   If you need UIDocument: new UIDocument(document)
   If you need UIApplication: new UIDocument(document).Application
5. For Structure namespace, use fully qualified names:
   Autodesk.Revit.DB.Structure.StructuralType.Column
6. All coordinates in Revit internal units (feet).
   If user provides mm: mm / 304.8 = feet
   If user provides m: m / 0.3048 = feet
7. Always return a meaningful result:
   return new {{ ElementId = element.Id.IntegerValue, Status = "Created" }};
8. Common pitfalls:
   - FamilySymbol must be Activate() before use
   - FilteredElementCollector must specify OfClass or OfCategory
   - Transaction must be Committed, not just Started
   - Do NOT use `using` statement for Transaction (use try/finally)
9. If parameters are needed, use string interpolation placeholders like {{{{param_name}}}}.
...
"""
```

**Completed tasks:**

```
B-1-1  ✅ Replaced SYSTEM_EXECUTE with Document document template
B-1-2  ✅ Added 5 auto-injected using list (inform LLM not to repeat)
B-1-3  ✅ Added unit conventions (feet/mm/m conversion), common pitfalls, Step comment rules
B-1-4  ✅ Added "return object" requirement + "NO Transaction" rule
B-1-5  ✅ Added selections_context parameter (interactive selection injection)
B-1-6  ✅ Added extract_parameters() static method
```

### B-2. Add Mandatory Reasoning Step Comments

**Purpose**: v0.2 requires "visible reasoning process", reflected in code comments.

**Add to the Rules section of SYSTEM_EXECUTE:**

```
7. Structure your code with numbered step comments:
   // Step 1: [purpose] — [which API and why]
   // Step 2: [purpose] — [which API and why]
   This makes the reasoning process visible and auditable.
```

### B-3. Parameter Placeholder Extractor

**Purpose**: After code generation, automatically identify `{param_name}` placeholders for use in the parameters field during solidification.

**New method** (code_generator.py):

```python
@staticmethod
def extract_parameters(code: str) -> list[dict]:
    """从代码模板中提取 {param_name} 占位符，返回参数列表。"""
    import re
    params = re.findall(r'\{(\w+)\}', code)
    seen = set()
    result = []
    for p in params:
        if p not in seen:
            seen.add(p)
            result.append({
                "name": p,
                "type": "string",
                "description": f"Parameter: {p}",
            })
    return result
```

**Acceptance Criteria:**

| # | Input | Expected Output |
|---|------|---------|
| B-3-1 | `XYZ({x}/304.8, {y}/304.8, 0)` | `[{name:"x"}, {name:"y"}]` |
| B-3-2 | Pure code with no placeholders | `[]` |

### B-4. Streaming Code Generation SSE Support

**Purpose**: Frontend Tab D needs to display the code generation process in streaming fashion.

**Current status**: `generate_stream()` method already exists (code_generator.py:94-109), but not yet wired to FastAPI SSE.

**Tasks:**

```
B-4-1  Add POST /api/v1/bridge/generate-stream in router.py
       Returns StreamingResponse (text/event-stream)
B-4-2  SSE event format:
       event: rag\ndata: "Searching 27,596 API docs..."\n\n
       event: token\ndata: "using(Transaction"\n\n
       event: token\ndata: " tx = new"\n\n
       event: done\ndata: {"code": "...", "rag_context": {...}}\n\n
B-4-3  RAG retrieval phase sends event: rag (to show retrieval progress in frontend)
       Code generation phase sends event: token (streaming token by token)
       Completion sends event: done (with full code + RAG metadata)
```

---

## Module C: RevitClient Enhancement

> **Priority: P1**
> **Dependencies: Module A Step 1 ✅ Confirmed — TCP Protocol**
> **File: `mcp_bridge/revit_client.py`**
> **Current status: ✅ TCP implementation correct, fine-tuned (timeout 60s, buffer 8192, added ping())**

### C-0. ✅ Communication Protocol Confirmed — No Migration Needed

```
Conclusion: raw TCP (TcpListener/TcpClient), NOT WebSocket
           Current revit_client.py's asyncio.open_connection implementation is compatible
Completed adjustments:
  - timeout: 120s → 60s (matching plugin-side RaiseAndWaitForCompletion(60000))
  - read buffer: 65536 → 8192 (matching plugin-side buffer size)
  - Added ping() method (connectivity check via say_hello)
  - No websockets library needed
```

### C-1. Connection Pool / Reuse

**Current issue**: Each `execute_code` and `run_tool` call creates a new connection and then disconnects. WebSocket is naturally suited for long-lived connection reuse.

**Tasks:**

```
C-1-1  Add RevitClientPool singleton:
       - Maintain one WebSocket long-lived connection
       - Auto-reconnect if connection drops
       - Thread-safe (asyncio.Lock)
C-1-2  Modify execute_code / run_tool in router.py and mcp_server.py
       to use pool instead of creating new client each time
```

**Implementation draft:**

```python
class RevitClientPool:
    _instance = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_client(cls) -> RevitClient:
        async with cls._lock:
            if cls._instance is None or not cls._instance.connected:
                cls._instance = RevitClient()
                await cls._instance.connect()
            return cls._instance
```

### C-2. Health Check Endpoint

**Purpose**: Frontend needs to know whether Revit is online.

**Tasks:**

```
C-2-1  Add RevitClient.ping() method
       Sends say_hello command, returns True on success
C-2-2  Add GET /api/v1/bridge/health
       Returns { "revit_connected": true/false, "latency_ms": 42 }
C-2-3  Frontend Tab D shows Revit connection status indicator at the top
```

### C-3. Externalize Configuration

**Current issue**: host/port is hardcoded in the RevitClient constructor (localhost:8080).

**Tasks:**

```
C-3-1  Add to config/config.yaml:
       mcp_bridge:
         revit_host: "localhost"
         revit_port: 8080
         command_timeout: 120
         connect_timeout: 5
C-3-2  Modify RevitClient defaults to read from config
C-3-3  Pass config when creating client in router.py / mcp_server.py
```

---

## Module D: ToolStore Enhancement

> **Priority: P1**
> **Dependencies: None (pure local logic)**
> **File: `mcp_bridge/tool_store.py` (already exists, needs enhancement)**

### D-1. Parameter Type Validation

**Current issue**: `render_code` only does string replacement without validating parameter types.

**Tasks:**

```
D-1-1  Add validate_params(name, params) method:
       - Check required parameters are present
       - Check types: whether double parameters can be converted to float
       - Default value filling: if a parameter has a default and user didn't provide it, auto-fill
D-1-2  render_code internally calls validate_params
D-1-3  Readable error messages:
       "Parameter 'height' is required but not provided.
        Available params: start_x, start_y, end_x, end_y, height (default: 3000)"
```

### D-2. Tool Version Management

**Purpose**: The same tool may need code iteration (e.g., fixing a discovered bug).

**Tasks:**

```
D-2-1  Add new field to YAML: version (int, default 1)
D-2-2  When solidify() encounters a tool with the same name:
       - Back up old version as tools/create_wall.v1.yaml
       - Write new version to tools/create_wall.yaml, version += 1
D-2-3  Add rollback(name) method: restore to previous version
```

### D-3. Tool Import/Export

**Purpose**: Support team sharing of solidified tools.

**Tasks:**

```
D-3-1  Add export_all() → returns JSON array of all tools
D-3-2  Add import_tools(json_array) → bulk import
D-3-3  Add REST endpoints:
       GET  /api/v1/bridge/tools/export → download JSON
       POST /api/v1/bridge/tools/import → upload JSON
```

### D-4. Smart Tool Matching

**Purpose**: When a user says "create wall", automatically match to the `create_wall` tool, skipping RAG + LLM.

**Tasks:**

```
D-4-1  Add match_tool(user_query) method:
       - First do keyword search (existing search method)
       - If only 1 match and execution_count > 0, return that tool
       - Otherwise return None (go through RAG generation path)
D-4-2  In router.py's generate-and-execute, call match_tool first
       - Matched → use solidified tool directly (needs LLM to extract parameter values)
       - Not matched → go through RAG generation path
D-4-3  LLM parameter extraction prompt (new):
       "Given this tool and user request, extract parameter values:
        Tool: {tool.description}
        Parameters: {tool.parameters}
        User: {user_query}
        Output JSON: {param_name: value, ...}"
```

---

## Module E: Gradio Tab D Frontend

> **Priority: P2**
> **Dependencies: Module B (streaming generation), Module C (health check)**
> **Files: Create `mcp_bridge/frontend/__init__.py` + `mcp_bridge/frontend/app.py`**

### E-1. Interface Layout

```
┌─────────────────────────────────────────────────────────────┐
│  🔴/🟢 Revit Connection: Connected (port 8080, 42ms)       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─ Input ─────────────────────────────────────────────┐   │
│  │ [Natural language input, placeholder: "Describe..."] │   │
│  │ [Generate Code] [Generate & Execute] [Clear]         │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─ Generated Code ───────────────────────────────────┐    │
│  │ // Step 1: Get structural column family type        │    │
│  │ FilteredElementCollector collector = ...             │    │
│  │ // Step 2: Get level                                │    │
│  │ Level level = ...                                    │    │
│  │ ...                                                  │    │
│  │ [Editable area, syntax highlighting for C#]          │    │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─ Execution Result ─────────────────────────────────┐    │
│  │ ✅ Success | ElementId: 334521 | Time: 2.3s        │    │
│  │ [Solidify as Tool]                                   │    │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─ RAG Context (collapsible) ────────────────────────┐    │
│  │ Query Rewriting: "structural column FamilyInstance"  │    │
│  │ API docs retrieved: 15 | SDK examples: 3             │    │
│  │ Top matches: FamilySymbol, StructuralType, Level     │    │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│  Solidified Tools Library                                   │
│  ┌──────────────┬──────────────┬──────────────┐            │
│  │ create_wall  │ create_col   │ delete_walls │            │
│  │ ⚡ 12x used  │ ⚡ 5x used   │ ⚡ 3x used   │            │
│  │ [Run]        │ [Run]        │ [Run]        │            │
│  └──────────────┴──────────────┴──────────────┘            │
└─────────────────────────────────────────────────────────────┘
```

### E-2. Implementation Task Breakdown

```
E-2-1  Create mcp_bridge/frontend/__init__.py
E-2-2  Create mcp_bridge/frontend/app.py:
       def create_bridge_tab() -> gr.Blocks:
           ...

E-2-3  Revit status indicator:
       - Poll GET /api/v1/bridge/health periodically (every 10 seconds)
       - Display: 🟢 Connected / 🔴 Disconnected

E-2-4  Input area:
       - gr.Textbox (natural language input)
       - gr.Button("Generate Code") → calls POST /api/v1/bridge/generate-stream
       - gr.Button("Generate & Execute") → generate-stream → execute chained call
       - gr.Button("Clear") → reset all areas

E-2-5  Code preview area:
       - gr.Code(language="csharp", interactive=True)
       - Streaming fill (SSE tokens appended one by one)
       - User can manually edit before executing

E-2-6  Execution result area:
       - gr.Textbox (read-only)
       - Display: success/failure + ElementId + duration

E-2-7  Solidify button:
       - Appears after successful execution
       - Click opens: gr.Textbox(name) + gr.Textbox(description)
       - After confirmation, calls POST /api/v1/bridge/solidify
       - Auto-fills parameters (calls CodeGenerator.extract_parameters)

E-2-8  RAG context collapsible area:
       - gr.Accordion("RAG Context", open=False)
       - Shows query rewriting result, retrieval count, top matches

E-2-9  Solidified tools library:
       - gr.Dataframe or gr.Gallery to display all tools
       - Each tool card: name + description + usage count + [Run] button
       - [Run] opens parameter form → calls POST /api/v1/bridge/tools/{name}/run
```

### E-3. Integration into Main Application

**File: `server/frontend/gradio_app.py`**

```
E-3-1  Import create_bridge_tab
E-3-2  Add Tab D in Tabs:
       with gr.Tab("MCP Bridge"):
           create_bridge_tab()
E-3-3  Confirm no conflicts with existing Tab A/B/C
```

---

## Module F: Security Sandbox and Error Retry

> **Priority: P2**
> **Dependencies: Module A + B**
> **File: Create `mcp_bridge/sandbox.py`**

### F-1. Code Security Review

**Purpose**: Prevent generated code from containing dangerous operations.

```
F-1-1  Create mcp_bridge/sandbox.py

F-1-2  Implement CodeSandbox.review(code) -> (safe: bool, warnings: list[str])
       Whitelisted namespaces (allowed):
       - Autodesk.Revit.*
       - System (basic types)
       - System.Collections.Generic
       - System.Linq

       Blacklisted patterns (forbidden):
       - System.IO.* (file operations)
       - System.Net.* (network operations)
       - System.Diagnostics.Process (process launching)
       - System.Reflection (reflection)
       - Strings "Assembly.Load", "Activator.CreateInstance"

F-1-3  In router.py's execute and generate-and-execute,
       call CodeSandbox.review before execution
       - safe=True → proceed with execution
       - safe=False → return warnings to frontend, wait for user confirmation

F-1-4  Frontend displays security review result:
       ⚠️ Code contains System.IO.File operations. Continue? [Yes] [Cancel]
```

### F-2. Compilation Error Retry

**Purpose**: LLM-generated code may have compilation errors; feed back error information for LLM to fix.

```
F-2-1  Create mcp_bridge/retry.py

F-2-2  Implement retry_on_compile_error(generator, user_query, error_msg, max_retries=2)
       Retry prompt:
       "The previous code failed to compile with error:
        {error_msg}

        Please fix the code. Common issues:
        - Missing namespace qualification
        - Wrong method overload
        - Type mismatch

        Original request: {user_query}

        Generate corrected code:"

F-2-3  Integrate into generate-and-execute flow:
       1. Generate code
       2. Execute
       3. If compilation error returned (error.code == -32000)
          → Regenerate (up to 2 times)
       4. If still fails, return full error chain to user

F-2-4  Frontend displays retry process:
       "Attempt 1/3: Compile error CS0246... Retrying..."
       "Attempt 2/3: Compile error CS1061... Retrying..."
       "Attempt 3/3: ✅ Success"
```

### F-3. Pre-Execution Confirmation UI

**Purpose**: Allow users to review code before execution.

```
F-3-1  Change "Generate & Execute" button to two steps:
       Step 1: Generate code → display in code area (editable)
       Step 2: User clicks "Confirm Execution" → send code
F-3-2  Or provide a toggle:
       gr.Checkbox("Auto-execute after generation", default=False)
       - Checked: one-click generate + execute (Demo mode)
       - Unchecked: wait for confirmation after generation (Safe mode)
```

---

## Module G: MCP Server Refinement

> **Priority: P2**
> **Dependencies: Module B + C + D**
> **File: `mcp_bridge/mcp_server.py` (already exists, needs refinement)**

### G-1. Claude Desktop Configuration

**File: `claude_desktop_config.json` (user-side configuration)**

```json
{
  "mcpServers": {
    "revit-rag-bridge": {
      "command": "python",
      "args": ["-m", "mcp_bridge.mcp_server"],
      "cwd": "F:\\DevProjects\\imkcrevit\\revit-api-rag\\revit-api-rag",
      "env": {
        "OPENROUTER_API_KEY": "sk-..."
      }
    }
  }
}
```

**Tasks:**

```
G-1-1  Write Claude Desktop configuration template
G-1-2  Write Cline configuration template
G-1-3  Verify: Claude Desktop shows 7 tools after startup
G-1-4  Verify: calling search_revit_api("Wall.Create") returns API documentation
```

### G-2. MCP Resource Enhancement

**Current**: Only one resource `revit://stats`.

```
G-2-1  Add revit://tools/{name}
       Returns the tool's complete YAML content

G-2-2  Add revit://api/{query}
       Equivalent to search_revit_api but as a resource (for Claude auto-discovery)

G-2-3  Add revit://connection-status
       Returns Revit connection status
```

### G-3. Prompt Template Injection

**Purpose**: After Claude Desktop connects, automatically provide Revit operation context.

```
G-3-1  Add MCP Server instructions (system prompt injection):
       "You are connected to a Revit AI Bridge with these capabilities:
        1. search_revit_api: Search 27,596 Revit 2026 API docs
        2. get_code_examples: Get real SDK code samples
        3. generate_code: Generate C# code using RAG context
        4. execute_code: Send C# to Revit for execution
        5. solidify_tool: Save successful code as reusable tool
        6. list_tools / run_tool: Manage and execute saved tools

        Workflow: search API → generate code → execute → solidify if successful.
        Always search API docs before generating code for accuracy."
```

---

## Module H: Integration Testing and Demo Preparation

> **Priority: P3**
> **Dependencies: All of Modules A-G completed**

### H-1. End-to-End Test Cases

**File: Create `tests/test_e2e_bridge.py`**

```
H-1-1  Test case: Create wall (full flow)
       Input: "Create a 5m long, 3m high wall at the origin"
       Assert:
         - RAG retrieves Wall.Create
         - Generated code contains Wall.Create
         - Code contains Transaction
         - Execution returns success=True
         - Returns element_id

H-1-2  Test case: Create structural column
       Input: "Create a structural column at position (0,0)"
       Assert: Similar to above

H-1-3  Test case: Solidify → Reuse
       Step 1: Generate + execute → success
       Step 2: solidify("test_wall", code)
       Step 3: run_tool("test_wall", {height: 5000})
       Assert: Second execution does not invoke LLM

H-1-4  Test case: Compilation error retry
       Input: Code that intentionally triggers compilation error
       Assert: Retry mechanism works, ultimately succeeds or returns clear error

H-1-5  Test case: Revit offline degradation
       Input: Any request, but Revit is not running
       Assert: Returns 502 + readable error message, no crash
```

### H-2. Pre-Solidified Demo Tools

**Files: Pre-create `mcp_bridge/tools/*.yaml`**

```
H-2-1  create_wall.yaml              — Create wall (already exists)
H-2-2  create_structural_column.yaml — Create structural column
H-2-3  create_floor.yaml             — Create floor slab
H-2-4  create_door.yaml              — Place door on wall
H-2-5  create_window.yaml            — Place window on wall
H-2-6  delete_elements.yaml          — Delete elements by category
H-2-7  modify_wall_height.yaml       — Modify wall height
H-2-8  query_room_area.yaml          — Query room area
```

Each tool requires:
- C# code verified in an actual Revit instance
- Correct parameter definitions
- Meaningful tags

### H-3. Demo Rehearsal Script

```
H-3-1  Scenario A rehearsal (first-time generation + solidification):
       - Confirm stable network (OpenRouter API reachable)
       - Confirm RAG retrieval works (ChromaDB + SQLite loaded)
       - Confirm code generation quality (Step comments, Transaction wrapping)
       - Confirm execution success (Revit model changes visible)
       - Confirm solidification flow is complete

H-3-2  Scenario B rehearsal (solidified tool reuse):
       - Confirm tool list displays correctly
       - Confirm run_tool skips RAG + LLM
       - Confirm execution speed is noticeably faster

H-3-3  Screen recording backup:
       - Record one complete successful Demo flow
       - Resolution 1920x1080, including Gradio + Revit dual screen
```

### H-4. Performance Baseline

```
H-4-1  Measure per-stage latency:
       - Query Rewriting: ~1s
       - ChromaDB search: ~0.5s
       - SQLite hydration: ~0.1s
       - LLM code generation: ~3-8s (depends on complexity)
       - TCP transmission: ~0.05s
       - Revit compilation + execution: ~1-3s
       - Total expected: ~5-13s

H-4-2  Solidified tool execution latency:
       - render_code: ~0.001s
       - TCP transmission + execution: ~1-3s
       - Total expected: ~1-3s (skips RAG + LLM)

H-4-3  Record in documentation as baseline
```

---

## Module I: Interactive Selection Workflow

> **Priority: P1**
> **Dependencies: Module A (plugin connectivity), Module C (WebSocket communication), Module E (frontend)**
> **Files: Create `mcp_bridge/interactive.py`, modify `mcp_bridge/router.py`, modify Gradio frontend**

### I-0. Design Philosophy

When a user issues an ambiguous intent, the system should not use default values and execute directly. Instead, it should **query the current Revit model state**, present available options to the user for selection, then use the selection result to drive code generation.

**Key distinction:**

| Mode | Old Flow | New Flow (Interactive) |
|------|--------|------------------|
| "Create structural column" | RAG → generate code (default family + default level) | Query all structural column family types → user selects → query levels → user selects → generate code |
| "Create window on wall" | RAG → generate code (needs wall ID?) | Trigger Revit Selection mode → user clicks wall → query window family types → user selects → generate code |
| "Delete selected elements" | Cannot execute (don't know what's selected) | Get current selection → display element list for confirmation → execute deletion |

### I-1. Intent Classification and Interaction Routing

**Purpose**: Identify whether user intent requires interactive selection and decide which path to take.

**Create `mcp_bridge/interactive.py`:**

```python
from enum import Enum

class InteractionType(Enum):
    DIRECT = "direct"              # No interaction needed, generate code directly
    SELECT_FAMILY = "select_family"  # Need to select family type
    SELECT_ELEMENT = "select_element"  # Need to select element in Revit
    SELECT_BOTH = "select_both"    # Select element first, then family type

class IntentClassifier:
    """Classify user intent to determine whether interactive selection is needed."""

    # Intent → interaction type + required Revit queries
    INTENT_MAP = {
        "创建结构柱": {
            "type": InteractionType.SELECT_FAMILY,
            "queries": [
                {"command": "get_available_family_types", "params": {"categoryList": ["OST_StructuralColumns"]}, "label": "结构柱族类型"},
                {"command": "get_levels", "params": {}, "label": "标高"},
            ]
        },
        "创建墙": {
            "type": InteractionType.SELECT_FAMILY,
            "queries": [
                {"command": "get_available_family_types", "params": {"categoryList": ["OST_Walls"]}, "label": "墙族类型"},
                {"command": "get_levels", "params": {}, "label": "标高"},
            ]
        },
        "在墙上创建窗户": {
            "type": InteractionType.SELECT_BOTH,
            "queries": [
                {"command": "operate_element", "params": {"action": "Select"}, "label": "选择宿主墙"},
                {"command": "get_available_family_types", "params": {"categoryList": ["OST_Windows"]}, "label": "窗户族类型"},
            ]
        },
        "在墙上创建门": {
            "type": InteractionType.SELECT_BOTH,
            "queries": [
                {"command": "operate_element", "params": {"action": "Select"}, "label": "选择宿主墙"},
                {"command": "get_available_family_types", "params": {"categoryList": ["OST_Doors"]}, "label": "门族类型"},
            ]
        },
    }

    def classify(self, user_query: str) -> dict:
        """Use LLM or keyword matching to determine intent type."""
        ...
```

**Task List:**

```
I-1-1  Create mcp_bridge/interactive.py
I-1-2  Implement IntentClassifier.classify(user_query) → InteractionType + queries
       - Phase 1: Keyword matching (Chinese and English)
       - Phase 2: LLM classification (handle ambiguous intents)
I-1-3  Define INTENT_MAP covering common scenarios:
       - Creation: column, wall, beam, slab, door, window, grid, level
       - Selection: operate on specified elements
       - Query: area, quantity statistics (direct execution, no selection needed)
```

### I-2. Revit Query Executor

**Purpose**: Execute monorepo built-in commands to query Revit model data and return a list of options.

```
I-2-1  Encapsulate RevitQueryExecutor class:

       class RevitQueryExecutor:
           def __init__(self, client: RevitClient):
               self.client = client

           async def get_family_types(self, categories: list[str]) -> list[dict]:
               """Call get_available_family_types, return family type list.
               Returns: [{"id": 12345, "name": "UC305x305x97", "family": "UC-Universal Columns", "category": "Structural Columns"}, ...]
               """
               resp = await self.client.send_command(
                   "get_available_family_types",
                   {"categoryList": categories}
               )
               return resp.result  # Parse into structured list

           async def get_levels(self) -> list[dict]:
               """Get all levels via send_code.
               Returns: [{"id": 100, "name": "Level 1", "elevation": 0.0}, {"id": 101, "name": "Level 2", "elevation": 3.6}]
               """
               code = '''
               var levels = new FilteredElementCollector(document)
                   .OfClass(typeof(Level))
                   .Cast<Level>()
                   .OrderBy(l => l.Elevation)
                   .Select(l => new { Id = l.Id.IntegerValue, Name = l.Name, Elevation = l.Elevation * 304.8 })
                   .ToList();
               return levels;
               '''
               resp = await self.client.send_code(code)
               return resp.result

           async def trigger_selection(self) -> list[dict]:
               """Trigger Revit selection mode, wait for user to pick elements.
               Calls operate_element(action: "Select") → user picks in Revit → returns selected elements.
               """
               # Step 1: Trigger selection mode
               await self.client.send_command("operate_element", {"action": "Select"})
               # Step 2: Get selection result
               resp = await self.client.send_command("get_selected_elements", {})
               return resp.result

I-2-2  Handle parsing and formatting of get_available_family_types return data
I-2-3  Handle level unit conversion (feet → mm for display)
I-2-4  Handle async waiting for trigger_selection (user selecting in Revit may take several seconds)
```

### I-3. Interactive Selection Frontend (Gradio)

**Purpose**: Display available options in Gradio and let the user select before continuing code generation.

```
I-3-1  Interaction Flow A: Family type selection (e.g., "create structural column")

       [User input] "Create structural column"
              ↓
       [Intent classification] → SELECT_FAMILY
              ↓
       [Query Revit] get_available_family_types(["OST_StructuralColumns"])
              ↓
       [Frontend display] gr.Dropdown("Select structural column type"):
              - UC305x305x97
              - UC254x254x73
              - HE200A
              - ...
              ↓
       [Query Revit] get_levels()
              ↓
       [Frontend display] gr.Dropdown("Select level"):
              - Level 1 (0mm)
              - Level 2 (3600mm)
              - Level 3 (7200mm)
              ↓
       [Frontend display] gr.Number("X coordinate (mm)"), gr.Number("Y coordinate (mm)")
              ↓
       [User confirms] → selection results passed to CodeGenerator
              ↓
       [Code generation] Generate precise code using selected family type name + level name + coordinates

I-3-2  Interaction Flow B: Select element first, then family type (e.g., "create window on wall")

       [User input] "Create window on wall"
              ↓
       [Intent classification] → SELECT_BOTH
              ↓
       [Frontend prompt] "Please select the wall to place the window on in Revit"
       [Trigger Revit] operate_element(action: "Select")
              ↓
       [User picks wall in Revit] → waiting...
              ↓
       [Get result] get_selected_elements()
       [Frontend display] "Selected: Wall [ID: 234567] — Basic Wall: Generic - 200mm"
              ↓
       [Query Revit] get_available_family_types(["OST_Windows"])
              ↓
       [Frontend display] gr.Dropdown("Select window type"):
              - M_Fixed: 0406 x 0610mm
              - M_Fixed: 0610 x 0610mm
              - ...
              ↓
       [Frontend display] gr.Number("Offset from wall start (mm)"), gr.Number("Sill height (mm)")
              ↓
       [User confirms] → wall ID + window type + parameters passed to CodeGenerator
              ↓
       [Code generation] Generate precise code using wall ID and window family type name

I-3-3  Gradio component implementation:

       def create_selection_panel():
           with gr.Column(visible=False) as selection_panel:
               status_text = gr.Textbox(label="Status", interactive=False)

               # Dynamic dropdown (family type selection)
               family_dropdown = gr.Dropdown(
                   label="Family Type", choices=[], interactive=True, visible=False
               )

               # Level selection
               level_dropdown = gr.Dropdown(
                   label="Level", choices=[], interactive=True, visible=False
               )

               # Coordinate input
               with gr.Row(visible=False) as coord_row:
                   x_input = gr.Number(label="X (mm)", value=0)
                   y_input = gr.Number(label="Y (mm)", value=0)

               # Confirm button
               confirm_btn = gr.Button("Confirm Selection and Generate Code", variant="primary")

           return selection_panel, {...}
```

### I-4. Inject Selection Results into CodeGenerator

**Purpose**: Pass user selection results into the code generation prompt.

```
I-4-1  Extend CodeGenerator.generate() signature:

       def generate(
           self,
           user_query: str,
           api_top_k: int = 15,
           code_top_k: int = 5,
           selections: dict | None = None,  # New: user selection results
       ) -> tuple[str, dict]:

I-4-2  selections structure example:

       {
           "family_type": "UC305x305x97",
           "family_id": 12345,
           "level": "Level 1",
           "level_id": 100,
           "host_element_id": 234567,  # Host element (e.g., wall ID)
           "position": {"x": 0, "y": 0},
       }

I-4-3  Append user selection context to SYSTEM_EXECUTE prompt:

       "## User Selections (use these exact values, do NOT query for them):
        - Family Type: {selections['family_type']}
        - Level: {selections['level']}
        - Host Element ID: {selections.get('host_element_id', 'N/A')}
        - Position: ({selections['position']['x']}mm, {selections['position']['y']}mm)

        IMPORTANT: Do not use FilteredElementCollector to find family types or levels.
        Use the exact names/IDs provided above."

I-4-4  This prevents the LLM from "guessing" family type names or level names,
       which would cause runtime element-not-found errors.
```

### I-5. REST API Endpoints

**New endpoints (`mcp_bridge/router.py`):**

```
I-5-1  POST /api/v1/bridge/classify-intent
       Request:  { "query": "创建结构柱" }
       Response: {
           "interaction_type": "select_family",
           "queries": [
               {"command": "get_available_family_types", "params": {...}, "label": "结构柱族类型"},
               {"command": "get_levels", "params": {}, "label": "标高"}
           ]
       }

I-5-2  POST /api/v1/bridge/query-revit
       Request:  { "command": "get_available_family_types", "params": {"categoryList": ["OST_StructuralColumns"]} }
       Response: { "result": [{"id": 12345, "name": "UC305x305x97", ...}, ...] }

I-5-3  POST /api/v1/bridge/trigger-selection
       Request:  {}
       Response: { "status": "waiting" }
       → Frontend polls GET /api/v1/bridge/selection-result
       → Or uses WebSocket to push selection completion event

I-5-4  GET /api/v1/bridge/selection-result
       Response: { "elements": [{"id": 234567, "category": "Walls", "type": "Generic - 200mm"}] }

I-5-5  POST /api/v1/bridge/generate-with-selections
       Request:  {
           "query": "创建结构柱",
           "selections": {"family_type": "UC305x305x97", "level": "Level 1", "position": {"x": 0, "y": 0}}
       }
       Response: { "code": "...", "rag_context": {...} }
```

### I-6. Complete Interaction Sequence Diagram

```
User                    Gradio Frontend          Python Server              Revit Plugin
 │                         │                        │                         │
 │  "Create struct column" │                        │                         │
 │ ─────────────────────→  │                        │                         │
 │                         │  classify-intent       │                         │
 │                         │ ────────────────────→  │                         │
 │                         │  {type: SELECT_FAMILY} │                         │
 │                         │ ←────────────────────  │                         │
 │                         │                        │                         │
 │                         │  query-revit           │                         │
 │                         │  (family types)        │  get_available_         │
 │                         │ ────────────────────→  │  family_types           │
 │                         │                        │ ─────────────────────→  │
 │                         │                        │  [types list]           │
 │                         │                        │ ←─────────────────────  │
 │                         │  [types dropdown]      │                         │
 │                         │ ←────────────────────  │                         │
 │                         │                        │                         │
 │  Select "UC305x305x97" │                        │                         │
 │  Select "Level 1"       │                        │                         │
 │  Coords (0, 0)          │                        │                         │
 │ ─────────────────────→  │                        │                         │
 │                         │  generate-with-        │                         │
 │                         │  selections            │                         │
 │                         │ ────────────────────→  │  (RAG + LLM)           │
 │                         │  {code: "..."}         │                         │
 │                         │ ←────────────────────  │                         │
 │                         │                        │                         │
 │  Confirm execution      │                        │                         │
 │ ─────────────────────→  │  execute               │                         │
 │                         │ ────────────────────→  │  send_code_to_revit    │
 │                         │                        │ ─────────────────────→  │
 │                         │                        │  {success, elementId}   │
 │                         │                        │ ←─────────────────────  │
 │                         │  "Created: ID 334521"  │                         │
 │                         │ ←────────────────────  │                         │
 │  See execution result   │                        │                         │
 │ ←─────────────────────  │                        │                         │
```

---

## Appendix: Completed Module Checklist

| File | Functionality | Status |
|------|------|------|
| `mcp_bridge/__init__.py` | Module description | ✅ |
| `mcp_bridge/revit_client.py` | TCP JSON-RPC 2.0 client (port 18080) | ✅ |
| `mcp_bridge/client_pool.py` | Connection pool singleton + auto-reconnect (C-1) | ✅ |
| `mcp_bridge/code_generator.py` | RAG-driven C# code generation | ✅ |
| `mcp_bridge/tool_store.py` | Solidified tool YAML CRUD | ✅ |
| `mcp_bridge/sandbox.py` | Code security review (F-1) | ✅ |
| `mcp_bridge/interactive.py` | Interactive selection (intent classification + Revit queries) (I-1/I-2) | ✅ |
| `mcp_bridge/router.py` | FastAPI REST API (14 routes, including SSE streaming) (B-4) | ✅ |
| `mcp_bridge/frontend/__init__.py` | Frontend module | ✅ |
| `mcp_bridge/frontend/app.py` | Gradio Tab D (E-2) | ✅ |
| `mcp_bridge/retry.py` | Compilation error retry (F-2) | ✅ |
| `mcp_bridge/mcp_server.py` | MCP Server (7 tools + 3 resources + instructions) (G-1/G-2/G-3) | ✅ |
| `mcp_bridge/tools/*.yaml` (x8) | Pre-solidified Demo tools (H-2) | ✅ |
| `server/main.py` | bridge_router registered | ✅ |
| `server/frontend/gradio_app.py` | Tab D integrated (E-3) | ✅ |
| `config/config.yaml` | Added mcp_bridge config section (C-3) | ✅ |
| `claude_desktop_config.json.example` | Claude Desktop config template (G-1) | ✅ |
| `revit_plugin/` | Revit plugin source (125 .cs files, port 18080) | ✅ |
| `revit_plugin/README.md` | Build/deployment instructions | ✅ |

### Files to Create

| File | Module | Functionality |
|------|------|------|
| `tests/test_revit_connection.py` | A | WebSocket connectivity verification |
| `tests/test_e2e_bridge.py` | H | End-to-end integration tests |
| `mcp_bridge/sandbox.py` | F | Code security review |
| `mcp_bridge/retry.py` | F | Compilation error retry |
| `mcp_bridge/interactive.py` | I | Interactive selection workflow (intent classification + query execution) |
| `mcp_bridge/frontend/__init__.py` | E | Frontend module |
| `mcp_bridge/frontend/app.py` | E | Gradio Tab D |
| `mcp_bridge/tools/*.yaml` (x8) | H | Pre-solidified Demo tools |

### Module Dependency Graph

```
A (Monorepo clone + verification) ── ✅ Steps 1-3 completed, Steps 4-6 require Revit
├── B (CodeGenerator) ────── ✅ B-1 completed (template + selections), B-2~B-4 pending
├── C (RevitClient) ─────── ✅ C-0 completed (TCP confirmed), C-1~C-3 pending
├── I (Interactive Selection) ── ✅ Skeleton completed (classifier + query executor + routing), frontend pending
│
Pending
├── D (ToolStore Enhancement) ── No blocking dependencies
├── E (Gradio Tab D) ────── Depends on B-4 (SSE) + C-2 (health check) + I-3 (selection frontend)
├── F (Security + Retry) ── Depends on B + C
├── G (MCP Server) ─────── Depends on B + C + D
│
All completed
└── H (Integration Testing + Demo)
```

### Recommended Development Order

```
Phase 1 — ✅ All completed (protocol confirmation + connectivity test + latency measurement):
  A Step 1-3: ✅ TCP confirmed, template confirmed, code adapted
  A Step 4:   ✅ TCP connectivity (port 18080, avoiding AdskLicensingAgent)
  A Step 5:   ✅ Code execution (say_hello + send_code_to_revit)
  A Step 6:   ✅ Latency ~1.2s (ExternalEvent inherent delay)
  Finding: commandRegistry.json needs manual population with 23 commands

Phase 2 — ✅ All completed (plugin compilation and deployment):
  A-1-3~A-1-8: ✅ Compiled Debug R26, deployed DLL, fixed duplicate addin

Phase 3 — ✅ All completed (feature enhancement):
  C-1: ✅ RevitClientPool connection pool
  C-2: ✅ GET /revit-health endpoint
  C-3: ✅ config.yaml mcp_bridge section
  B-4: ✅ POST /generate-stream SSE endpoint
  E-2: ✅ Gradio Tab D frontend (with interactive selection panel)
  E-3: ✅ Integrated into gradio_app.py
  F-1: ✅ sandbox.py code security review

Phase 4 — ✅ All completed (security and MCP):
  F-2: ✅ retry.py compilation error retry (LLM auto-fix, up to 2 attempts)
  G-1: ✅ claude_desktop_config.json.example
  G-2: ✅ MCP Resources: revit://tools/{name}, revit://connection-status
  G-3: ✅ SERVER_INSTRUCTIONS system prompt injection

Phase 5 — Partially completed (integration acceptance):
  H-2: ✅ 8 pre-solidified Demo tools
  H-1: ⬜ E2E tests (requires running Revit environment)
  H-3: ⬜ Demo rehearsal
```

---

*Document generated: 2026-03-13*
