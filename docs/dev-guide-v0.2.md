# Revit AI Reasoning Stack — 开发指南 v0.2

> 基于 `revit-ai-reasoning-stack-v0.2.md` 拆分的详细开发任务书
> 日期：2026-03-13

---

## 架构前提：双管道共用 RAG

本系统存在两条管道，共用同一个 RAG 检索层，仅在输出端分叉：

```
                    ┌─── 管道 A: 问答 (Tab A/B/C) ───→ LLM 自然语言解释
用户输入 → RAG ────┤
                    └─── 管道 B: 执行 (Tab D)     ───→ LLM 生成 C# 代码
                                                           ↓
                                                     RevitClient → Revit 插件执行
                                                           ↓
                                                     固化为可复用工具 (ToolStore)
```

| | 管道 A（问答） | 管道 B（执行） |
|---|---|---|
| 前端 | Gradio Tab A/B/C | Gradio Tab D |
| 共享层 | RAGRetriever + LLMClient | RAGRetriever + LLMClient |
| System Prompt | `SYSTEM_PROMPT`（解释型） | `SYSTEM_EXECUTE`（代码生成型） |
| 输出 | 自然语言 + API 引用 | 可执行 C# 代码 |
| 后端链路 | 无 | RevitClient → Revit → ToolStore |

**Phase 2 前端集成时**：Tab 切换即管道切换，不是两套独立系统。

---

## 目录

- [Module A: Revit 插件端部署与通信验证](#module-a-revit-插件端部署与通信验证)
- [Module B: CodeGenerator 增强](#module-b-codegenerator-增强)
- [Module C: RevitClient 增强](#module-c-revitclient-增强)
- [Module D: ToolStore 增强](#module-d-toolstore-增强)
- [Module E: Gradio Tab D 前端](#module-e-gradio-tab-d-前端)
- [Module F: 安全沙箱与错误重试](#module-f-安全沙箱与错误重试)
- [Module G: MCP Server 完善](#module-g-mcp-server-完善)
- [Module H: 集成测试与 Demo 准备](#module-h-集成测试与-demo-准备)
- [Module I: 交互式选择工作流](#module-i-交互式选择工作流)
- [附录：已完成模块清单](#附录已完成模块清单-phase-0)

---

## Module A: Revit 插件端部署与通信验证

> **优先级：P0（阻塞全部后续模块）**
> **依赖：无**
> **产出：Revit 本地运行 monorepo 插件，Python 可连通，协议和模板已确认**

### Phase 1 执行顺序与确认结论

**原则：先确认事实，再写代码。**

```
Step 1  ✅ 已确认 — 通信协议
        来源：plugin/Core/SocketService.cs
        结论：raw TCP (TcpListener/TcpClient)，NOT WebSocket
        端口：8080（硬编码）
        消息格式：JSON-RPC 2.0，UTF-8，无分隔符（raw bytes read，buffer 8192）

Step 2  ✅ 已确认 — send_code_to_revit 存在
        来源：commandset/Commands/ExecuteDynamicCode/ExecuteCodeEventHandler.cs
        代码模板：Roslyn 编译，public static object Execute(Document document, object[] parameters)
        自动 using：System, System.Linq, Autodesk.Revit.DB, Autodesk.Revit.UI, System.Collections.Generic
        关键发现：EventHandler 已包裹 Transaction，用户代码 **不能** 再创建 Transaction
        超时：60s（RaiseAndWaitForCompletion(60000)）

Step 3  ✅ 已完成 — revit_client.py 已适配
        结论：TCP 协议正确，无需迁移 WebSocket
        调整：timeout 从 120s 改为 60s（匹配插件端）
        新增：ping() 方法（say_hello 命令检测连通性）

Step 4  ✅ 已完成 — TCP 连接测试
        发现：端口 8080 被 AdskLicensingAgent.exe 占用
        解决：改用端口 18080（插件 + Python 端同步修改）
        结果：say_hello PASS, send_code_to_revit PASS

Step 5  ✅ 已完成 — 代码执行验证
        测试：document.Title → 返回 "Project1"
        测试：document.Application.VersionNumber → 返回 "2026"

Step 6  ✅ 已完成 — 时延测量
        Ping 平均: ~1.3s (ExternalEvent 排队机制)
        代码执行: ~1.2s (含 Roslyn 编译)
        基础延迟约 1s 为 Revit API 固有限制
```

### A-1. 获取 Monorepo 插件（部分克隆）

| 项 | 说明 |
|----|------|
| 来源 | https://github.com/mcp-servers-for-revit/mcp-servers-for-revit（monorepo，活跃维护） |
| 需要保留的部分 | `plugin/`（Revit add-in 主体）+ `commandset/`（命令实现）+ `command.json`（命令定义） |
| 删除的部分 | `server/`（Node.js MCP Server，已被我们的 Python MCP Server 替代） |
| 关键能力 | TCP (port 18080, 改自 8080 避免 AdskLicensingAgent 冲突) + Roslyn 动态编译 + 23 个预制命令（含 `send_code_to_revit`） |

**任务清单：**

```
A-1-1  Clone monorepo:
       git clone https://github.com/mcp-servers-for-revit/mcp-servers-for-revit.git
       cd mcp-servers-for-revit

A-1-2  ✅ 已阅读关键源码（Step 1 + Step 2）：
       - plugin/Core/SocketService.cs → TCP (TcpListener), port 8080
       - plugin/Core/CommandExecutor.cs → JSON-RPC 路由，反序列化 JObject params
       - commandset/Commands/ExecuteDynamicCode/ → Roslyn 编译，Document document 模板
       - command.json → 23 个命令，全部确认

A-1-3  仅保留插件端：
       - 保留: plugin/          (Revit add-in C# 项目)
       - 保留: commandset/      (命令实现, 含 ExecuteDynamicCode)
       - 保留: command.json     (命令定义清单)
       - 删除: server/          (Node.js MCP Server, 我们用 Python 替代)
       - 保留: README.md, LICENSE 等根目录文件

A-1-4  在项目根目录 README.md 中添加归属说明：
       "## Third-party Components
        Revit plugin (plugin/ + commandset/) is sourced from
        [mcp-servers-for-revit](https://github.com/mcp-servers-for-revit/mcp-servers-for-revit)
        under [LICENSE]. Node.js MCP server has been replaced by our Python RAG-powered server."

A-1-5  确认插件端编译目标：
       - .NET 4.8 for Revit 2020-2024 / .NET 8 for Revit 2025-2026

A-1-6  编译为 DLL + .addin manifest

A-1-7  部署到 Revit Addins 目录：
       Revit 2026: %AppData%\Autodesk\Revit\Addins\2026\

A-1-8  启动 Revit，确认插件加载成功（看日志或 Ribbon 按钮）
```

### A-2. 通信连通性验证（Step 3 + Step 4）

**目标**：确认 `mcp_bridge/revit_client.py` 能连通 Revit 插件。

> **注意**：通信协议在 A-1-2 确认后才能确定验证方式。
> 当前 `revit_client.py` 基于 TCP socket。如果插件端是 WebSocket，需先执行 Module C-0 迁移。

**验证脚本**（新建 `tests/test_revit_connection.py`）：

```python
"""
验证连接到 Revit 插件。
前置：Revit 已启动，插件已加载。
协议：根据 A-1-2 确认结果选择 TCP 或 WebSocket。
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

**验收标准：**

| # | 验收项 | 预期结果 |
|---|--------|---------|
| A-2-1 | `RevitClient.connect()` 成功 | 无异常抛出 |
| A-2-2 | `say_hello` 命令 | Revit 弹出对话框 |
| A-2-3 | `send_code` 最小代码 | Revit 弹出 TaskDialog |
| A-2-4 | `get_available_family_types` | 返回族类型列表 JSON |
| A-2-5 | 超时测试 | 断开 Revit 后 5 秒内返回连接超时错误 |

### A-3. 插件端 send_code_to_revit 执行模板（已确认）

> **状态**：✅ 已通过 clone 本地源码确认。

**确认结论**（来源：`commandset/Commands/ExecuteDynamicCode/ExecuteCodeEventHandler.cs`）：

```
A-3-1  插件端代码模板（Roslyn 编译）：

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

       ✅ 确认：变量名是 `document`（不是 `doc`），参数是 `parameters`（object[]）
       ✅ 确认：这是静态方法，不是 IExternalCommand.Execute
       ✅ 关键发现：EventHandler.Execute() 已包裹 Transaction
          → 用户代码 **不能** 再创建 Transaction，否则嵌套事务报错

A-3-2  自动注入的 using 语句（5 个，已确认）：
       - using System;
       - using System.Linq;
       - using Autodesk.Revit.DB;
       - using Autodesk.Revit.UI;
       - using System.Collections.Generic;

A-3-3  编译器：✅ Roslyn (Microsoft.CodeAnalysis.CSharp)
       引用所有已加载程序集（AppDomain.CurrentDomain.GetAssemblies()）

A-3-4  编译错误回传：✅ 带行号的错误列表
       格式："Line {n}: {error message}"，多个错误换行拼接

A-3-5  运行时异常回传：✅ JSON response
       { "success": false, "errorMessage": "执行失败: {ex.Message}" }
```

**产出**：✅ `mcp_bridge/code_generator.py:SYSTEM_EXECUTE` 已更新为确认后的模板。
关键变更：`doc` → `document`，删除 Transaction 规则（插件已包裹），新增 return 要求。

### A-4. Monorepo 命令清单（已确认）

> ✅ 已通过 clone 后 command.json 本地确认，共 23 个命令。

| # | Command | 用途 | Module I 相关 |
|---|---------|------|---------------|
| 1 | `say_hello` | 连通测试 | |
| 2 | `get_available_family_types` | 按类别查询可用族类型 | **核心** |
| 3 | `get_current_view_elements` | 获取当前视图元素 | |
| 4 | `get_current_view_info` | 获取当前视图信息 | |
| 5 | `get_selected_elements` | 获取用户已选择的元素 | **核心** |
| 6 | `create_point_based_element` | 创建点基族实例 | |
| 7 | `create_line_based_element` | 创建线基族实例 | |
| 8 | `create_surface_based_element` | 创建面基族实例 | |
| 9 | `color_splash` | 按条件着色 | |
| 10 | `tag_walls` | 标记墙体 | |
| 11 | `delete_element` | 删除元素 | |
| 12 | `ai_element_filter` | AI 条件过滤元素 | |
| 13 | `operate_element` | 操作元素（含 Select 模式） | **核心** |
| 14 | `export_room_data` | 导出房间数据 | |
| 15 | `get_material_quantities` | 获取材料量 | |
| 16 | `analyze_model_statistics` | 模型统计 | |
| 17 | `create_grid` | 创建轴网 | |
| 18 | `create_structural_framing_system` | 创建结构框架 | |
| 19 | `create_room` | 创建房间 | |
| 20 | `tag_rooms` | 标记房间 | |
| 21 | `create_level` | 创建标高 | |
| 22 | `send_code_to_revit` | 发送任意 C# 代码执行 | |
| 23 | `create_dimensions` | 创建尺寸标注 | |

### A-5. 端到端时延测量（Step 6）

Step 5 通过后，对结构柱创建案例做完整计时：

```
A-5-1  各阶段打点：
       t0: 用户输入
       t1: Query Rewriting 完成
       t2: ChromaDB 搜索完成
       t3: SQLite 水合完成
       t4: LLM 代码生成完成
       t5: 传输到 Revit 完成
       t6: Revit 编译+执行完成
       t7: 结果回传到 Python

A-5-2  记录各段耗时，写入 Module H-4 作为 baseline
```

---

## Module B: CodeGenerator 增强

> **优先级：P1**
> **依赖：Module A Step 2 ✅ 已完成**
> **文件：`mcp_bridge/code_generator.py`**
> **当前状态：✅ 已更新为 monorepo 模板（Document document + 无 Transaction）**

### B-1. ✅ 修正 SYSTEM_EXECUTE prompt（已完成）

**已完成变更**：
- 模板从 `IExternalCommand` 改为 `static object Execute(Document document, object[] parameters)`
- 变量名 `doc` → `document`
- **删除 Transaction 规则**（插件 EventHandler 已包裹，用户代码不能再创建）
- 新增 return 要求（方法必须返回 object）
- 新增 selections 上下文注入（交互式选择结果）

**参考（已应用到代码）**：

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

**已完成任务：**

```
B-1-1  ✅ 替换 SYSTEM_EXECUTE 为 Document document 模板
B-1-2  ✅ 添加 5 个自动注入 using 列表（告知 LLM 不要重复）
B-1-3  ✅ 添加单位约定（feet/mm/m 换算）、常见陷阱、Step 注释规则
B-1-4  ✅ 添加 "return object" 要求 + "NO Transaction" 规则
B-1-5  ✅ 添加 selections_context 参数（交互式选择注入）
B-1-6  ✅ 新增 extract_parameters() 静态方法
```

### B-2. 添加推理步骤注释强制

**目的**：v0.2 要求"推理过程可见"，体现在代码注释中。

**在 SYSTEM_EXECUTE 的 Rules 中添加：**

```
7. Structure your code with numbered step comments:
   // Step 1: [purpose] — [which API and why]
   // Step 2: [purpose] — [which API and why]
   This makes the reasoning process visible and auditable.
```

### B-3. 参数占位符提取器

**目的**：生成代码后，自动识别 `{param_name}` 占位符，用于固化时的 parameters 字段。

**新增方法**（code_generator.py）：

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

**验收标准：**

| # | 输入 | 预期输出 |
|---|------|---------|
| B-3-1 | `XYZ({x}/304.8, {y}/304.8, 0)` | `[{name:"x"}, {name:"y"}]` |
| B-3-2 | 无占位符的纯代码 | `[]` |

### B-4. 流式代码生成 SSE 支持

**目的**：前端 Tab D 需要流式显示代码生成过程。

**当前状态**：`generate_stream()` 方法已存在（code_generator.py:94-109），但未接入 FastAPI SSE。

**任务：**

```
B-4-1  在 router.py 新增 POST /api/v1/bridge/generate-stream
       返回 StreamingResponse (text/event-stream)
B-4-2  SSE 事件格式：
       event: rag\ndata: "Searching 27,596 API docs..."\n\n
       event: token\ndata: "using(Transaction"\n\n
       event: token\ndata: " tx = new"\n\n
       event: done\ndata: {"code": "...", "rag_context": {...}}\n\n
B-4-3  RAG 检索阶段发送 event: rag（让前端显示检索进度）
       代码生成阶段发送 event: token（逐 token 流式）
       完成时发送 event: done（含完整代码 + RAG 元数据）
```

---

## Module C: RevitClient 增强

> **优先级：P1**
> **依赖：Module A Step 1 ✅ 已确认 — TCP 协议**
> **文件：`mcp_bridge/revit_client.py`**
> **当前状态：✅ TCP 实现正确，已微调（timeout 60s, buffer 8192, 新增 ping()）**

### C-0. ✅ 通信协议已确认 — 无需迁移

```
结论：raw TCP (TcpListener/TcpClient)，NOT WebSocket
       当前 revit_client.py 的 asyncio.open_connection 实现已兼容
已完成调整：
  - timeout: 120s → 60s（匹配插件端 RaiseAndWaitForCompletion(60000)）
  - read buffer: 65536 → 8192（匹配插件端 buffer 大小）
  - 新增 ping() 方法（通过 say_hello 检测连通性）
  - 不需要 websockets 库
```

### C-1. 连接池 / 复用

**当前问题**：每次 `execute_code` 和 `run_tool` 都新建连接再断开。WebSocket 天然适合长连接复用。

**任务：**

```
C-1-1  新增 RevitClientPool 单例：
       - 维护一个 WebSocket 长连接
       - 如果连接断开自动重连
       - 线程安全（asyncio.Lock）
C-1-2  修改 router.py 和 mcp_server.py 中的 execute_code / run_tool
       使用 pool 而非每次新建 client
```

**实现草案：**

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

### C-2. 健康检查端点

**目的**：前端需要知道 Revit 是否在线。

**任务：**

```
C-2-1  新增 RevitClient.ping() 方法
       发送 say_hello 命令，成功返回 True
C-2-2  新增 GET /api/v1/bridge/health
       返回 { "revit_connected": true/false, "latency_ms": 42 }
C-2-3  前端 Tab D 顶部显示 Revit 连接状态指示灯
```

### C-3. 配置外置

**当前问题**：host/port 硬编码在 RevitClient 构造函数（localhost:8080）。

**任务：**

```
C-3-1  在 config/config.yaml 新增：
       mcp_bridge:
         revit_host: "localhost"
         revit_port: 8080
         command_timeout: 120
         connect_timeout: 5
C-3-2  修改 RevitClient 默认值从 config 读取
C-3-3  router.py / mcp_server.py 创建 client 时传入配置
```

---

## Module D: ToolStore 增强

> **优先级：P1**
> **依赖：无（纯本地逻辑）**
> **文件：`mcp_bridge/tool_store.py`（已存在，需增强）**

### D-1. 参数类型校验

**当前问题**：`render_code` 只做字符串替换，不校验参数类型。

**任务：**

```
D-1-1  新增 validate_params(name, params) 方法：
       - 检查必填参数是否存在
       - 检查类型：double 参数是否可转为 float
       - 缺省值填充：如果参数有 default 且用户未提供，自动填入
D-1-2  render_code 内部调用 validate_params
D-1-3  错误信息可读：
       "Parameter 'height' is required but not provided.
        Available params: start_x, start_y, end_x, end_y, height (default: 3000)"
```

### D-2. 工具版本管理

**目的**：同一个工具可能需要迭代代码（如发现 bug 后修复）。

**任务：**

```
D-2-1  YAML 新增字段：version (int, default 1)
D-2-2  solidify() 同名工具时：
       - 旧版本备份为 tools/create_wall.v1.yaml
       - 新版本写入 tools/create_wall.yaml，version += 1
D-2-3  新增 rollback(name) 方法：恢复到上一版本
```

### D-3. 工具导入/导出

**目的**：支持团队共享固化工具。

**任务：**

```
D-3-1  新增 export_all() → 返回所有工具的 JSON 数组
D-3-2  新增 import_tools(json_array) → 批量导入
D-3-3  新增 REST 端点：
       GET  /api/v1/bridge/tools/export → 下载 JSON
       POST /api/v1/bridge/tools/import → 上传 JSON
```

### D-4. 智能工具匹配

**目的**：用户说"创建墙"时，自动匹配到 `create_wall` 工具，跳过 RAG + LLM。

**任务：**

```
D-4-1  新增 match_tool(user_query) 方法：
       - 先做关键词搜索（现有 search 方法）
       - 如果只有 1 个匹配且 execution_count > 0，返回该工具
       - 否则返回 None（走 RAG 生成路径）
D-4-2  在 router.py 的 generate-and-execute 中，先调用 match_tool
       - 匹配到 → 直接用固化工具（需要 LLM 提取参数值）
       - 未匹配 → 走 RAG 生成路径
D-4-3  LLM 参数提取 prompt（新增）：
       "Given this tool and user request, extract parameter values:
        Tool: {tool.description}
        Parameters: {tool.parameters}
        User: {user_query}
        Output JSON: {param_name: value, ...}"
```

---

## Module E: Gradio Tab D 前端

> **优先级：P2**
> **依赖：Module B（流式生成）、Module C（健康检查）**
> **文件：新建 `mcp_bridge/frontend/__init__.py` + `mcp_bridge/frontend/app.py`**

### E-1. 界面布局

```
┌─────────────────────────────────────────────────────────────┐
│  🔴/🟢 Revit Connection: Connected (port 8080, 42ms)       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─ Input ─────────────────────────────────────────────┐   │
│  │ [自然语言输入框，placeholder: "描述你想在 Revit..."] │   │
│  │ [生成代码] [生成并执行] [清空]                       │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─ Generated Code ───────────────────────────────────┐    │
│  │ // Step 1: 获取结构柱族类型                         │    │
│  │ FilteredElementCollector collector = ...             │    │
│  │ // Step 2: 获取标高                                  │    │
│  │ Level level = ...                                    │    │
│  │ ...                                                  │    │
│  │ [可编辑区域，syntax highlighting for C#]             │    │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─ Execution Result ─────────────────────────────────┐    │
│  │ ✅ Success | ElementId: 334521 | Time: 2.3s        │    │
│  │ [固化为工具]                                        │    │
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

### E-2. 实现任务拆分

```
E-2-1  创建 mcp_bridge/frontend/__init__.py
E-2-2  创建 mcp_bridge/frontend/app.py：
       def create_bridge_tab() -> gr.Blocks:
           ...

E-2-3  Revit 状态指示灯：
       - 定时轮询 GET /api/v1/bridge/health（每 10 秒）
       - 显示：🟢 Connected / 🔴 Disconnected

E-2-4  输入区域：
       - gr.Textbox（自然语言输入）
       - gr.Button("生成代码") → 调用 POST /api/v1/bridge/generate-stream
       - gr.Button("生成并执行") → generate-stream → execute 链式调用
       - gr.Button("清空") → 重置所有区域

E-2-5  代码预览区：
       - gr.Code(language="csharp", interactive=True)
       - 流式填充（SSE token 逐个追加）
       - 用户可手动编辑后再执行

E-2-6  执行结果区：
       - gr.Textbox（只读）
       - 显示：成功/失败 + ElementId + 耗时

E-2-7  固化按钮：
       - 执行成功后出现
       - 点击弹出：gr.Textbox(name) + gr.Textbox(description)
       - 确认后调用 POST /api/v1/bridge/solidify
       - 自动填入 parameters（调用 CodeGenerator.extract_parameters）

E-2-8  RAG 上下文折叠区：
       - gr.Accordion("RAG Context", open=False)
       - 显示 query rewriting 结果、检索数量、Top 匹配项

E-2-9  固化工具库：
       - gr.Dataframe 或 gr.Gallery 展示所有工具
       - 每个工具卡片：名称 + 描述 + 使用次数 + [Run] 按钮
       - [Run] 弹出参数填写表单 → 调用 POST /api/v1/bridge/tools/{name}/run
```

### E-3. 集成到主应用

**文件：`server/frontend/gradio_app.py`**

```
E-3-1  导入 create_bridge_tab
E-3-2  在 Tabs 中新增 Tab D:
       with gr.Tab("MCP Bridge"):
           create_bridge_tab()
E-3-3  确认与现有 Tab A/B/C 无冲突
```

---

## Module F: 安全沙箱与错误重试

> **优先级：P2**
> **依赖：Module A + B**
> **文件：新建 `mcp_bridge/sandbox.py`**

### F-1. 代码安全审查

**目的**：防止生成的代码包含危险操作。

```
F-1-1  新建 mcp_bridge/sandbox.py

F-1-2  实现 CodeSandbox.review(code) -> (safe: bool, warnings: list[str])
       白名单命名空间（允许）：
       - Autodesk.Revit.*
       - System (基本类型)
       - System.Collections.Generic
       - System.Linq

       黑名单模式（禁止）：
       - System.IO.*（文件操作）
       - System.Net.*（网络操作）
       - System.Diagnostics.Process（进程启动）
       - System.Reflection（反射）
       - 字符串 "Assembly.Load", "Activator.CreateInstance"

F-1-3  在 router.py 的 execute 和 generate-and-execute 中，
       执行前调用 CodeSandbox.review
       - safe=True → 继续执行
       - safe=False → 返回 warnings 给前端，等待用户确认

F-1-4  前端显示安全审查结果：
       ⚠️ Code contains System.IO.File operations. Continue? [Yes] [Cancel]
```

### F-2. 编译错误重试

**目的**：LLM 生成的代码可能有编译错误，反馈错误信息让 LLM 修复。

```
F-2-1  新建 mcp_bridge/retry.py

F-2-2  实现 retry_on_compile_error(generator, user_query, error_msg, max_retries=2)
       Retry prompt:
       "The previous code failed to compile with error:
        {error_msg}

        Please fix the code. Common issues:
        - Missing namespace qualification
        - Wrong method overload
        - Type mismatch

        Original request: {user_query}

        Generate corrected code:"

F-2-3  在 generate-and-execute 流程中集成：
       1. 生成代码
       2. 执行
       3. 如果返回编译错误（error.code == -32000）
          → 重新生成（最多 2 次）
       4. 如果仍然失败，返回完整错误链给用户

F-2-4  前端显示重试过程：
       "Attempt 1/3: Compile error CS0246... Retrying..."
       "Attempt 2/3: Compile error CS1061... Retrying..."
       "Attempt 3/3: ✅ Success"
```

### F-3. 执行前确认 UI

**目的**：用户在执行前可以审查代码。

```
F-3-1  "生成并执行"按钮改为两步：
       Step 1: 生成代码 → 显示在代码区（可编辑）
       Step 2: 用户点击"确认执行" → 发送代码
F-3-2  或者提供开关：
       gr.Checkbox("Auto-execute after generation", default=False)
       - 勾选：一键生成+执行（Demo 模式）
       - 不勾选：生成后等待确认（安全模式）
```

---

## Module G: MCP Server 完善

> **优先级：P2**
> **依赖：Module B + C + D**
> **文件：`mcp_bridge/mcp_server.py`（已存在，需完善）**

### G-1. Claude Desktop 配置

**文件：`claude_desktop_config.json`（用户侧配置）**

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

**任务：**

```
G-1-1  编写 Claude Desktop 配置模板
G-1-2  编写 Cline 配置模板
G-1-3  验证：Claude Desktop 启动后能看到 7 个 tools
G-1-4  验证：调用 search_revit_api("Wall.Create") 返回 API 文档
```

### G-2. MCP Resource 增强

**当前**：只有 `revit://stats` 一个 resource。

```
G-2-1  新增 revit://tools/{name}
       返回工具的完整 YAML 内容

G-2-2  新增 revit://api/{query}
       等同于 search_revit_api 但作为 resource（供 Claude 自动发现）

G-2-3  新增 revit://connection-status
       返回 Revit 连接状态
```

### G-3. Prompt 模板注入

**目的**：Claude Desktop 连接后，自动获得 Revit 操作上下文。

```
G-3-1  新增 MCP Server instructions (system prompt injection):
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

## Module H: 集成测试与 Demo 准备

> **优先级：P3**
> **依赖：Module A-G 全部完成**

### H-1. 端到端测试用例

**文件：新建 `tests/test_e2e_bridge.py`**

```
H-1-1  测试用例：创建墙体（完整流程）
       Input: "在原点创建一面 5 米长、3 米高的墙"
       Assert:
         - RAG 检索到 Wall.Create
         - 生成的代码包含 Wall.Create
         - 代码包含 Transaction
         - 执行返回 success=True
         - 返回 element_id

H-1-2  测试用例：创建结构柱
       Input: "创建一根结构柱在 (0,0) 位置"
       Assert: 类似上述

H-1-3  测试用例：固化 → 复用
       Step 1: 生成 + 执行 → 成功
       Step 2: solidify("test_wall", code)
       Step 3: run_tool("test_wall", {height: 5000})
       Assert: 第二次执行不调用 LLM

H-1-4  测试用例：编译错误重试
       Input: 故意触发编译错误的代码
       Assert: 重试机制工作，最终成功或返回清晰错误

H-1-5  测试用例：Revit 离线降级
       Input: 任意请求，但 Revit 未启动
       Assert: 返回 502 + 可读错误信息，不崩溃
```

### H-2. Demo 工具预固化

**文件：预先创建 `mcp_bridge/tools/*.yaml`**

```
H-2-1  create_wall.yaml          — 创建墙体（已存在）
H-2-2  create_structural_column.yaml — 创建结构柱
H-2-3  create_floor.yaml         — 创建楼板
H-2-4  create_door.yaml          — 在墙上放门
H-2-5  create_window.yaml        — 在墙上放窗
H-2-6  delete_elements.yaml      — 按类别删除元素
H-2-7  modify_wall_height.yaml   — 修改墙高
H-2-8  query_room_area.yaml      — 查询房间面积
```

每个工具需要：
- 实际在 Revit 中验证过的 C# 代码
- 正确的参数定义
- 有意义的 tags

### H-3. Demo 排练脚本

```
H-3-1  场景 A 排练（首次生成 + 固化）：
       - 确认网络稳定（OpenRouter API 可达）
       - 确认 RAG 检索正常（ChromaDB + SQLite 已加载）
       - 确认代码生成质量（Step 注释、Transaction 包裹）
       - 确认执行成功（Revit 模型变更可见）
       - 确认固化流程完整

H-3-2  场景 B 排练（固化工具复用）：
       - 确认工具列表显示正确
       - 确认 run_tool 跳过 RAG + LLM
       - 确认执行速度明显更快

H-3-3  录屏备份：
       - 完整录制一次成功的 Demo 流程
       - 分辨率 1920x1080，包含 Gradio + Revit 双屏
```

### H-4. 性能基线

```
H-4-1  测量各阶段耗时：
       - Query Rewriting: ~1s
       - ChromaDB 搜索: ~0.5s
       - SQLite 水合: ~0.1s
       - LLM 代码生成: ~3-8s（取决于复杂度）
       - TCP 传输: ~0.05s
       - Revit 编译+执行: ~1-3s
       - 总计预期: ~5-13s

H-4-2  固化工具执行耗时：
       - render_code: ~0.001s
       - TCP 传输+执行: ~1-3s
       - 总计预期: ~1-3s（跳过 RAG + LLM）

H-4-3  记录到文档作为 baseline
```

---

## Module I: 交互式选择工作流

> **优先级：P1**
> **依赖：Module A（插件连通）、Module C（WebSocket 通信）、Module E（前端）**
> **文件：新建 `mcp_bridge/interactive.py`，修改 `mcp_bridge/router.py`，修改 Gradio 前端**

### I-0. 设计理念

当用户发出模糊意图时，系统不应使用默认值直接执行，而应**查询 Revit 当前模型状态**，将可选项呈现给用户选择，然后用选择结果驱动代码生成。

**核心区别：**

| 模式 | 旧流程 | 新流程（交互式） |
|------|--------|------------------|
| "创建结构柱" | RAG → 生成代码（默认族+默认标高） | 查询所有结构柱族类型 → 用户选择 → 查询标高 → 用户选择 → 生成代码 |
| "在墙上创建窗户" | RAG → 生成代码（需要墙 ID？） | 触发 Revit Selection 模式 → 用户点选墙 → 查询窗户族类型 → 用户选择 → 生成代码 |
| "删除选中元素" | 无法执行（不知道选了什么） | 获取当前选择 → 展示元素列表确认 → 执行删除 |

### I-1. 意图分类与交互路由

**目的**：识别用户意图是否需要交互式选择，决定走哪条路径。

**新建 `mcp_bridge/interactive.py`：**

```python
from enum import Enum

class InteractionType(Enum):
    DIRECT = "direct"              # 无需交互，直接生成代码
    SELECT_FAMILY = "select_family"  # 需要选择族类型
    SELECT_ELEMENT = "select_element"  # 需要在 Revit 中选择元素
    SELECT_BOTH = "select_both"    # 先选元素，再选族类型

class IntentClassifier:
    """分类用户意图，决定是否需要交互式选择。"""

    # 意图 → 交互类型 + 所需的 Revit 查询
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
        """用 LLM 或关键词匹配判断意图类型。"""
        ...
```

**任务清单：**

```
I-1-1  新建 mcp_bridge/interactive.py
I-1-2  实现 IntentClassifier.classify(user_query) → InteractionType + queries
       - 第一阶段：关键词匹配（中英文）
       - 第二阶段：LLM 分类（处理模糊意图）
I-1-3  定义 INTENT_MAP 覆盖常见场景：
       - 创建类：柱、墙、梁、板、门、窗、轴网、标高
       - 选择类：在指定元素上操作
       - 查询类：面积、数量统计（直接执行，无需选择）
```

### I-2. Revit 查询执行器

**目的**：执行 monorepo 预制命令查询 Revit 模型数据，返回可选项列表。

```
I-2-1  封装 RevitQueryExecutor 类：

       class RevitQueryExecutor:
           def __init__(self, client: RevitClient):
               self.client = client

           async def get_family_types(self, categories: list[str]) -> list[dict]:
               """调用 get_available_family_types，返回族类型列表。
               返回: [{"id": 12345, "name": "UC305x305x97", "family": "UC-Universal Columns", "category": "Structural Columns"}, ...]
               """
               resp = await self.client.send_command(
                   "get_available_family_types",
                   {"categoryList": categories}
               )
               return resp.result  # 解析为结构化列表

           async def get_levels(self) -> list[dict]:
               """通过 send_code 获取所有标高。
               返回: [{"id": 100, "name": "Level 1", "elevation": 0.0}, {"id": 101, "name": "Level 2", "elevation": 3.6}]
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
               """触发 Revit 选择模式，等待用户选取元素。
               调用 operate_element(action: "Select") → 用户在 Revit 中点选 → 返回选中元素。
               """
               # Step 1: 触发选择模式
               await self.client.send_command("operate_element", {"action": "Select"})
               # Step 2: 获取选择结果
               resp = await self.client.send_command("get_selected_elements", {})
               return resp.result

I-2-2  处理 get_available_family_types 返回数据的解析和格式化
I-2-3  处理 get_levels 的标高单位换算（feet → mm 显示）
I-2-4  处理 trigger_selection 的异步等待（用户在 Revit 中选择可能需要数秒）
```

### I-3. 交互式选择前端（Gradio）

**目的**：在 Gradio 中展示可选项，让用户选择后继续生成代码。

```
I-3-1  交互流程 A：族类型选择（以"创建结构柱"为例）

       [用户输入] "创建结构柱"
              ↓
       [意图分类] → SELECT_FAMILY
              ↓
       [查询 Revit] get_available_family_types(["OST_StructuralColumns"])
              ↓
       [前端展示] gr.Dropdown("选择结构柱类型"):
              - UC305x305x97
              - UC254x254x73
              - HE200A
              - ...
              ↓
       [查询 Revit] get_levels()
              ↓
       [前端展示] gr.Dropdown("选择标高"):
              - Level 1 (0mm)
              - Level 2 (3600mm)
              - Level 3 (7200mm)
              ↓
       [前端展示] gr.Number("X 坐标 (mm)"), gr.Number("Y 坐标 (mm)")
              ↓
       [用户确认] → 选择结果传入 CodeGenerator
              ↓
       [代码生成] 使用选中的族类型名+标高名+坐标生成精确代码

I-3-2  交互流程 B：先选元素再选族类型（以"在墙上创建窗户"为例）

       [用户输入] "在墙上创建窗户"
              ↓
       [意图分类] → SELECT_BOTH
              ↓
       [前端提示] "请在 Revit 中选择要放置窗户的墙体"
       [触发 Revit] operate_element(action: "Select")
              ↓
       [用户在 Revit 中点选墙] → 等待...
              ↓
       [获取结果] get_selected_elements()
       [前端展示] "已选择: Wall [ID: 234567] — Basic Wall: Generic - 200mm"
              ↓
       [查询 Revit] get_available_family_types(["OST_Windows"])
              ↓
       [前端展示] gr.Dropdown("选择窗户类型"):
              - M_Fixed: 0406 x 0610mm
              - M_Fixed: 0610 x 0610mm
              - ...
              ↓
       [前端展示] gr.Number("距墙起点偏移 (mm)"), gr.Number("窗台高度 (mm)")
              ↓
       [用户确认] → 墙 ID + 窗户类型 + 参数传入 CodeGenerator
              ↓
       [代码生成] 使用墙 ID、窗户族类型名生成精确代码

I-3-3  Gradio 组件实现：

       def create_selection_panel():
           with gr.Column(visible=False) as selection_panel:
               status_text = gr.Textbox(label="状态", interactive=False)

               # 动态下拉框（族类型选择）
               family_dropdown = gr.Dropdown(
                   label="族类型", choices=[], interactive=True, visible=False
               )

               # 标高选择
               level_dropdown = gr.Dropdown(
                   label="标高", choices=[], interactive=True, visible=False
               )

               # 坐标输入
               with gr.Row(visible=False) as coord_row:
                   x_input = gr.Number(label="X (mm)", value=0)
                   y_input = gr.Number(label="Y (mm)", value=0)

               # 确认按钮
               confirm_btn = gr.Button("确认选择并生成代码", variant="primary")

           return selection_panel, {...}
```

### I-4. 选择结果注入 CodeGenerator

**目的**：将用户的选择结果传入代码生成 prompt。

```
I-4-1  扩展 CodeGenerator.generate() 签名：

       def generate(
           self,
           user_query: str,
           api_top_k: int = 15,
           code_top_k: int = 5,
           selections: dict | None = None,  # 新增：用户选择结果
       ) -> tuple[str, dict]:

I-4-2  selections 结构示例：

       {
           "family_type": "UC305x305x97",
           "family_id": 12345,
           "level": "Level 1",
           "level_id": 100,
           "host_element_id": 234567,  # 宿主元素（如墙 ID）
           "position": {"x": 0, "y": 0},
       }

I-4-3  在 SYSTEM_EXECUTE prompt 中追加用户选择上下文：

       "## User Selections (use these exact values, do NOT query for them):
        - Family Type: {selections['family_type']}
        - Level: {selections['level']}
        - Host Element ID: {selections.get('host_element_id', 'N/A')}
        - Position: ({selections['position']['x']}mm, {selections['position']['y']}mm)

        IMPORTANT: Do not use FilteredElementCollector to find family types or levels.
        Use the exact names/IDs provided above."

I-4-4  这避免了 LLM "猜测" 族类型名或标高名导致运行时找不到元素的问题。
```

### I-5. REST API 端点

**新增端点（`mcp_bridge/router.py`）：**

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
       → 前端轮询 GET /api/v1/bridge/selection-result
       → 或使用 WebSocket 推送选择完成事件

I-5-4  GET /api/v1/bridge/selection-result
       Response: { "elements": [{"id": 234567, "category": "Walls", "type": "Generic - 200mm"}] }

I-5-5  POST /api/v1/bridge/generate-with-selections
       Request:  {
           "query": "创建结构柱",
           "selections": {"family_type": "UC305x305x97", "level": "Level 1", "position": {"x": 0, "y": 0}}
       }
       Response: { "code": "...", "rag_context": {...} }
```

### I-6. 完整交互序列图

```
用户                    Gradio 前端              Python Server              Revit Plugin
 │                         │                        │                         │
 │  "创建结构柱"           │                        │                         │
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
 │  选择 "UC305x305x97"   │                        │                         │
 │  选择 "Level 1"         │                        │                         │
 │  坐标 (0, 0)            │                        │                         │
 │ ─────────────────────→  │                        │                         │
 │                         │  generate-with-        │                         │
 │                         │  selections            │                         │
 │                         │ ────────────────────→  │  (RAG + LLM)           │
 │                         │  {code: "..."}         │                         │
 │                         │ ←────────────────────  │                         │
 │                         │                        │                         │
 │  确认执行               │                        │                         │
 │ ─────────────────────→  │  execute               │                         │
 │                         │ ────────────────────→  │  send_code_to_revit    │
 │                         │                        │ ─────────────────────→  │
 │                         │                        │  {success, elementId}   │
 │                         │                        │ ←─────────────────────  │
 │                         │  "Created: ID 334521"  │                         │
 │                         │ ←────────────────────  │                         │
 │  看到执行结果           │                        │                         │
 │ ←─────────────────────  │                        │                         │
```

---

## 附录：已完成模块清单

| 文件 | 功能 | 状态 |
|------|------|------|
| `mcp_bridge/__init__.py` | 模块说明 | ✅ |
| `mcp_bridge/revit_client.py` | TCP JSON-RPC 2.0 客户端（port 18080） | ✅ |
| `mcp_bridge/client_pool.py` | 连接池单例 + 自动重连 (C-1) | ✅ |
| `mcp_bridge/code_generator.py` | RAG 驱动 C# 代码生成 | ✅ |
| `mcp_bridge/tool_store.py` | 固化工具 YAML CRUD | ✅ |
| `mcp_bridge/sandbox.py` | 代码安全审查 (F-1) | ✅ |
| `mcp_bridge/interactive.py` | 交互式选择（意图分类+Revit查询）(I-1/I-2) | ✅ |
| `mcp_bridge/router.py` | FastAPI REST API（14 routes, 含 SSE 流式）(B-4) | ✅ |
| `mcp_bridge/frontend/__init__.py` | 前端模块 | ✅ |
| `mcp_bridge/frontend/app.py` | Gradio Tab D (E-2) | ✅ |
| `mcp_bridge/retry.py` | 编译错误重试 (F-2) | ✅ |
| `mcp_bridge/mcp_server.py` | MCP Server（7 tools + 3 resources + instructions）(G-1/G-2/G-3) | ✅ |
| `mcp_bridge/tools/*.yaml` (x8) | Demo 预固化工具 (H-2) | ✅ |
| `server/main.py` | 已注册 bridge_router | ✅ |
| `server/frontend/gradio_app.py` | Tab D 已集成 (E-3) | ✅ |
| `config/config.yaml` | 新增 mcp_bridge 配置段 (C-3) | ✅ |
| `claude_desktop_config.json.example` | Claude Desktop 配置模板 (G-1) | ✅ |
| `revit_plugin/` | Revit 插件源码（125 个 .cs 文件, port 18080） | ✅ |
| `revit_plugin/README.md` | 编译/部署说明 | ✅ |

### 待新建文件清单

| 文件 | 模块 | 功能 |
|------|------|------|
| `tests/test_revit_connection.py` | A | WebSocket 连通验证 |
| `tests/test_e2e_bridge.py` | H | 端到端集成测试 |
| `mcp_bridge/sandbox.py` | F | 代码安全审查 |
| `mcp_bridge/retry.py` | F | 编译错误重试 |
| `mcp_bridge/interactive.py` | I | 交互式选择工作流（意图分类+查询执行） |
| `mcp_bridge/frontend/__init__.py` | E | 前端模块 |
| `mcp_bridge/frontend/app.py` | E | Gradio Tab D |
| `mcp_bridge/tools/*.yaml` (x8) | H | Demo 预固化工具 |

### 模块依赖图

```
A (Monorepo 克隆 + 确认) ── ✅ Step 1-3 已完成，Step 4-6 需 Revit
├── B (CodeGenerator) ────── ✅ B-1 已完成（模板+selections），B-2~B-4 待做
├── C (RevitClient) ─────── ✅ C-0 已完成（TCP 确认），C-1~C-3 待做
├── I (交互式选择) ─────── ✅ 骨架已完成（分类器+查询器+路由），前端待做
│
待做部分
├── D (ToolStore 增强) ──── 无阻塞依赖
├── E (Gradio Tab D) ────── 依赖 B-4 (SSE) + C-2 (健康检查) + I-3 (选择前端)
├── F (安全 + 重试) ────── 依赖 B + C
├── G (MCP Server) ─────── 依赖 B + C + D
│
全部完成
└── H (集成测试 + Demo)
```

### 建议开发顺序

```
Phase 1 — ✅ 全部完成（协议确认 + 连通测试 + 时延测量）：
  A Step 1-3: ✅ TCP 确认, 模板确认, 代码适配
  A Step 4:   ✅ TCP 连通 (port 18080, 避开 AdskLicensingAgent)
  A Step 5:   ✅ 代码执行 (say_hello + send_code_to_revit)
  A Step 6:   ✅ 延迟 ~1.2s (ExternalEvent 固有延迟)
  发现: commandRegistry.json 需手动填充 23 条命令

Phase 2 — ✅ 全部完成（插件编译部署）：
  A-1-3~A-1-8: ✅ 编译 Debug R26, 部署 DLL, 修复重复 addin

Phase 3 — ✅ 全部完成（功能增强）：
  C-1: ✅ RevitClientPool 连接池
  C-2: ✅ GET /revit-health 端点
  C-3: ✅ config.yaml mcp_bridge 段
  B-4: ✅ POST /generate-stream SSE 端点
  E-2: ✅ Gradio Tab D 前端 (含交互选择面板)
  E-3: ✅ 集成到 gradio_app.py
  F-1: ✅ sandbox.py 代码安全审查

Phase 4 — ✅ 全部完成（安全与 MCP）：
  F-2: ✅ retry.py 编译错误重试（LLM 自动修复, 最多 2 次）
  G-1: ✅ claude_desktop_config.json.example
  G-2: ✅ MCP Resources: revit://tools/{name}, revit://connection-status
  G-3: ✅ SERVER_INSTRUCTIONS 系统提示注入

Phase 5 — 部分完成（集成验收）：
  H-2: ✅ 8 个预固化 Demo 工具
  H-1: ⬜ E2E 测试（需 Revit 运行环境）
  H-3: ⬜ Demo 排练
```

---

*文档生成时间：2026-03-13*
