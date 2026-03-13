# Revit AI Reasoning Stack — 技术文档 v0.2

> 版本：v0.2（基于 v0.1 核对 + MCP Bridge 整合）
> 日期：2026-03-13
> 变更：回答全部待核对项，新增 Layer 3 MCP Bridge 模块，废弃 Intent Bridge

---

## 0. v0.1 → v0.2 变更摘要

| 变更项 | v0.1 状态 | v0.2 状态 |
|--------|----------|----------|
| Layer 2 待核对项 | 全部未填 | **全部已回答**（见 §4） |
| Layer 3 执行方式 | 预编译 DLL 反射调用（5 个 command） | **云端生成 C# + TCP 发送执行 + 固化为 tool** |
| Intent Bridge | 独立模块 | **废弃**（RAG 已能完整理解意图） |
| 动态执行 | "无 Roslyn 编译层" | **已实现**：revit-mcp 插件端编译执行 |
| 工具扩展 | 需手写 C# command | **自增长**：成功执行 → 自动固化为可复用工具 |
| 新增模块 | — | `mcp_bridge/`（5 个文件 + tools 目录） |

---

## 1. 系统概述

本系统是一个三层架构的 AI 辅助 BIM 设计执行栈，核心目标是：

**将模糊的设计意图转化为 Revit 可执行操作，全程推理过程透明可见。**

### v0.2 核心突破

v0.1 的 Layer 3 受限于 5 个预编译 command——系统虽然"理解了"用户意图，但"能做的事"只有 5 件。

v0.2 通过整合 [revit-mcp](https://github.com/mcp-servers-for-revit/revit-mcp) 的 `send_code_to_revit` 机制，实现了：

> **RAG 知识库（27,596 条 API）→ LLM 生成 C# 代码 → TCP 发送到 Revit 编译执行 → 成功则固化为可复用工具**

这意味着系统的执行能力不再受限于预定义 command 数量，而是**等于整个 Revit API 的覆盖范围**。

---

## 2. 整体架构（v0.2 更新）

```
┌─────────────────────────────────────────────────────────────┐
│                Layer 1: Intent Interface                     │
│  Gradio Web UI (0.0.0.0:7860)                               │
│  支持多轮对话（最近 6 条历史） / 中英文 / 代码预览          │
└──────────────────────────┬──────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│              Layer 2: RAG Reasoning Engine                   │
│                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │ Query        │    │  ChromaDB    │    │   SQLite     │  │
│  │ Rewriting    │ →  │  Semantic    │ →  │   Hydration  │  │
│  │ (Gemini)     │    │  Search      │    │   (full doc) │  │
│  └──────────────┘    └──────────────┘    └──────┬───────┘  │
│                                                  ↓          │
│                                     ┌────────────────────┐  │
│                                     │  Code Generator    │  │
│                                     │  (RAG context →    │  │
│                                     │   C# code body)    │  │
│                                     └────────┬───────────┘  │
└──────────────────────────────────────────────┬──────────────┘
                                               ↓
┌─────────────────────────────────────────────────────────────┐
│              Layer 3: MCP Bridge (NEW)                       │
│                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │  TCP Client   │ →  │  Revit       │ →  │  Execution   │  │
│  │  (JSON-RPC)   │    │  Plugin      │    │  Result      │  │
│  │  port 8080    │    │  compile+run │    │  + ElementId  │  │
│  └──────────────┘    └──────────────┘    └──────┬───────┘  │
│                                                  ↓          │
│                                     ┌────────────────────┐  │
│                                     │  Tool Store        │  │
│                                     │  成功 → 固化 YAML  │  │
│                                     │  下次 → 直接调用    │  │
│                                     └────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Layer 1：意图输入层

### 3.1 功能定义

- 接受任意粒度的自然语言输入
- 支持中英文（LLM Query Rewriting 自动翻译关键词）
- 当前实现：Gradio Web UI（`0.0.0.0:7860`）

### 3.2 输入粒度示例

| 粒度 | 示例输入 |
|------|---------|
| 精确指令 | "在坐标 (0,0) 创建 400×400 混凝土柱，高度 3600mm" |
| 半模糊 | "创建一根结构柱" |
| 设计意图 | "这个区域需要结构支撑" |

### 3.3 核对结果（v0.1 待核对 → v0.2 已确认）

| 问题 | 答案 |
|------|------|
| Gradio 是否支持多轮对话？ | **是**。Session 保留最近 6 条消息（3 轮），UUID session_id，TTL 2 小时 |
| 输入是否有预处理？ | **是**。Query Rewriting（Gemini Flash）提取 Revit API 关键词；CJK 占比 >30% 判定为中文 |
| 是否支持图片/附件？ | **否**。当前仅文本输入 |

### 3.4 前端 Tab 结构

| Tab | 功能 | 输出形式 | 状态 |
|-----|------|---------|------|
| Tab A: Code Generation | RAG + LLM 生成 C# 代码 | Markdown 代码 | ✅ 生产可用 |
| Tab B: Text2Revit | 旧版结构化 JSON 生成 | SSE 事件流 | ⚠️ 遗留，将被 MCP Bridge 替代 |
| Tab C: Intent Bridge | NLP 意图解析 + 参数填槽 | 多轮问答 | ❌ 废弃（RAG 已覆盖） |
| Tab D: MCP Bridge | **新增** 代码生成 → 执行 → 固化 | 代码 + 执行结果 | 🔧 待实现前端 |

---

## 4. Layer 2：RAG 推理引擎

> 本层是系统核心，也是与所有现有竞品的最大差异点。

### 4.1 知识库（v0.2 已填入）

| 属性 | 现状 |
|------|------|
| 覆盖版本 | Revit 2026 全量 API |
| API 条目数 | **27,596** 条（CHM 解析 → tree-sitter 剪枝） |
| SDK 代码样本 | **153** 条 golden code（SDK Samples → LLM 归纳） |
| 数据处理 | SDK 剪枝 + API 剪枝，保留精确方法签名 |
| 向量化模型 | **OpenAI text-embedding-3-large**（3072 维，via OpenRouter） |
| 向量数据库 | **ChromaDB**（API 集合 286MB + SDK 集合 1.8MB） |
| 全文存储 | **SQLite**（revit_api.db 33MB + revit_sdk.db 932KB） |

### 4.2 Embedding 阶段（已核对）

```
用户输入
   ↓
Query Rewriting (Gemini Flash, max_tokens=256, temp=0.1)
   提取 Revit API 关键词，中文 → 英文 API 术语
   例："结构柱" → "structural column FamilyInstance BuiltInCategory.OST_StructuralColumns"
   ↓
OpenAI text-embedding-3-large (3072 维)
   ↓
ChromaDB 余弦相似度搜索
   ↓
输出：语义最近邻候选集（ID + distance score）
```

### 4.3 Retriever 阶段（已核对）

```
ChromaDB 候选集
   ↓
Top-K 召回
   API: top_k=30 → rerank_top_n=15
   SDK: top_k=5  → rerank_top_n=3
   ↓
SQLite 全文水合（batch-fetch by ID）
   ↓
输出：RetrievedItem[]
   包含：full_id / name / summary / syntax / parameters / remark
```

| 核对项 | 答案 |
|--------|------|
| Top-K 值 | API: 30 → rerank 15；SDK: 5 → rerank 3 |
| 是否有 reranker | **是**。Cohere rerank-v3.5（via OpenRouter） |
| UI 显示内容 | 结构化上下文：`### Full.API.Id` + summary + syntax + parameters |

### 4.4 多步 API 推理（v0.2 更新）

v0.1 设想的"LLM 分解 → 对照 tools 列表验证"模式在 v0.2 中被简化：

**v0.2 方案：RAG 上下文直接驱动代码生成，不再依赖固定 tools 列表。**

```
用户意图 + Top-K API 上下文 + SDK golden code
                    ↓
     LLM 生成完整 C# 代码（单次调用）
     系统 prompt 规定：
       - 代码在 Execute 方法体内运行
       - 可用变量：doc, uidoc, uiapp
       - 修改操作必须包裹 Transaction
                    ↓
     输出：可直接发送给 Revit 执行的 C# 代码
```

| 核对项 | 答案 |
|--------|------|
| 任务分解是单次还是多轮 | **单次 LLM 调用**（RAG 上下文已足够） |
| API 可行性验证 | **RAG 驱动**：检索到的 API 签名即是可行的 |
| 推理链步骤上限 | 不适用（代码生成替代了步骤分解） |
| 依赖关系处理 | 在生成的 C# 代码内部处理（如先获取 Level 再创建 Wall） |

### 4.5 代码生成输出格式（v0.2 新增）

取代 v0.1 的 JSON 执行计划，v0.2 直接输出 C# 代码：

```csharp
// 生成的代码示例（在 Execute 方法体内运行）
using(Transaction tx = new Transaction(doc, "Create Structural Column"))
{
    tx.Start();

    // Step 1: 获取结构柱族类型
    FilteredElementCollector collector = new FilteredElementCollector(doc);
    FamilySymbol columnType = collector
        .OfCategory(BuiltInCategory.OST_StructuralColumns)
        .OfClass(typeof(FamilySymbol))
        .FirstElement() as FamilySymbol;

    if (!columnType.IsActive) columnType.Activate();

    // Step 2: 获取标高
    Level level = new FilteredElementCollector(doc)
        .OfClass(typeof(Level)).FirstElement() as Level;

    // Step 3: 创建柱实例
    XYZ point = new XYZ(0, 0, 0);
    doc.Create.NewFamilyInstance(
        point, columnType, level,
        StructuralType.Column);

    tx.Commit();
}
```

> 推理步骤体现在代码注释中（Step 1/2/3），保持了 v0.1 要求的"推理过程可见"。

---

## 5. Layer 3：MCP Bridge 执行层（v0.2 重写）

### 5.1 架构变更

| 维度 | v0.1 | v0.2 |
|------|------|------|
| 执行方式 | 预编译 DLL 反射调用 | **TCP 发送 C# 代码，插件端编译执行** |
| 工具数量 | 5 个固定 command | **无限制**（任何 Revit API 调用） |
| 通信协议 | 未定义 | **JSON-RPC 2.0 over TCP (port 8080)** |
| 扩展方式 | 手写 C# + 编译 DLL | **自动固化**：成功代码 → YAML 工具 |
| 来源 | 自建 | 整合自 revit-mcp（MIT License） |

### 5.2 模块结构

```
mcp_bridge/
├── __init__.py          # 模块说明
├── revit_client.py      # TCP JSON-RPC 2.0 客户端（from revit-mcp SocketClient.ts）
├── code_generator.py    # RAG 上下文 → C# 代码生成（专用 system prompt）
├── tool_store.py        # 固化工具存储（YAML 文件 CRUD）
├── mcp_server.py        # MCP Server（7 tools, stdio transport）
├── router.py            # FastAPI REST API（/api/v1/bridge/*）
└── tools/               # 固化工具目录
    └── create_wall.yaml # 示例工具
```

### 5.3 执行机制

```
C# 代码（由 Layer 2 生成）
     ↓
RevitClient.send_code(code)
     ↓
TCP 连接 localhost:8080
     ↓
JSON-RPC 2.0 请求：
{
  "jsonrpc": "2.0",
  "method": "send_code_to_revit",
  "params": { "code": "...", "parameters": [] },
  "id": "1710300000123456"
}
     ↓
Revit 插件端：
  1. 接收 JSON-RPC
  2. 将代码嵌入 Execute 模板
  3. 编译（Roslyn / CSharpCodeProvider）
  4. ExternalEvent.Raise() 线程安全执行
  5. 返回结果 / ElementId
     ↓
JSON-RPC 响应：
{ "jsonrpc": "2.0", "result": {...}, "id": "1710300000123456" }
     ↓
RevitResponse(success=True/False, result=..., error=...)
```

### 5.4 工具固化机制（核心创新）

```
首次执行：                           再次执行：

用户: "创建 3m 高的墙"               用户: "创建 5m 高的墙"
     ↓                                    ↓
RAG 检索 Wall.Create 文档            查找固化工具: create_wall ✓
     ↓                                    ↓
LLM 生成 C# 代码                    render_code(height=5000)
     ↓                                    ↓
send_code_to_revit                   send_code_to_revit
     ↓                                    ↓
✅ 成功                              ✅ 成功（跳过 RAG + LLM）
     ↓
solidify("create_wall", code)
     ↓
保存 → tools/create_wall.yaml
```

**固化工具 YAML 格式：**

```yaml
name: create_wall
display_name: Create Wall
description: Create a wall between two points with specified height.
code_template: |
  using(Transaction tx = new Transaction(doc, "Create Wall"))
  {
      tx.Start();
      XYZ start = new XYZ({start_x} / 304.8, {start_y} / 304.8, 0);
      XYZ end = new XYZ({end_x} / 304.8, {end_y} / 304.8, 0);
      Line wallLine = Line.CreateBound(start, end);
      Wall wall = Wall.Create(doc, wallLine, level.Id, false);
      wall.get_Parameter(BuiltInParameter.WALL_USER_HEIGHT_PARAM)
          .Set({height} / 304.8);
      tx.Commit();
  }
parameters:
  - {name: start_x, type: double, description: "起点 X (mm)"}
  - {name: start_y, type: double, description: "起点 Y (mm)"}
  - {name: end_x,   type: double, description: "终点 X (mm)"}
  - {name: end_y,   type: double, description: "终点 Y (mm)"}
  - {name: height,  type: double, description: "墙高 (mm)", default: "3000"}
tags: [wall, create, basic]
source_query: "创建 3m 高的墙"
execution_count: 0
```

### 5.5 双访问通道

| 通道 | 协议 | 用途 | 启动方式 |
|------|------|------|---------|
| MCP Server | stdio (MCP SDK) | Claude Desktop / Cline | `python -m mcp_bridge.mcp_server` |
| REST API | HTTP (FastAPI) | Web UI / 自定义客户端 | 随主服务启动（`/api/v1/bridge/*`） |

**REST API 端点：**

| Method | Path | 功能 |
|--------|------|------|
| POST | `/api/v1/bridge/generate` | RAG + LLM 生成 C# 代码 |
| POST | `/api/v1/bridge/execute` | 发送 C# 到 Revit 执行 |
| POST | `/api/v1/bridge/generate-and-execute` | 一键：生成 → 执行 |
| POST | `/api/v1/bridge/solidify` | 固化成功代码为工具 |
| GET | `/api/v1/bridge/tools` | 列出所有固化工具 |
| GET | `/api/v1/bridge/tools/{name}` | 获取工具详情 |
| POST | `/api/v1/bridge/tools/{name}/run` | 用参数执行固化工具 |
| DELETE | `/api/v1/bridge/tools/{name}` | 删除工具 |

**MCP Server Tools（7 个）：**

| Tool | 功能 | 调用 LLM | 调用 Revit |
|------|------|---------|-----------|
| `search_revit_api` | RAG 检索 API 文档 | ❌ | ❌ |
| `get_code_examples` | 检索 SDK 代码样本 | ❌ | ❌ |
| `generate_code` | RAG → C# 代码生成 | ✅ | ❌ |
| `execute_code` | 发送代码到 Revit | ❌ | ✅ |
| `solidify_tool` | 固化为可复用工具 | ❌ | ❌ |
| `list_tools` | 列出固化工具 | ❌ | ❌ |
| `run_tool` | 执行固化工具 | ❌ | ✅ |

### 5.6 执行约束（保持不变）

- **Revit 线程模型**：所有 API 调用通过 `ExternalEvent.Raise()` 在主线程执行
- **事务管理**：生成的代码强制包含 `Transaction` 包裹
- **超时处理**：TCP 命令超时 120 秒，连接超时 5 秒
- **错误回传**：结构化 JSON-RPC error response（`{error: {code, message}}`）

---

## 6. 数据流完整路径（v0.2 更新）

以"创建结构柱"为例：

```
[输入]
"这个区域需要结构支撑"

[Layer 1 → Layer 2]
原始文本传入推理引擎
Session 历史：最近 6 条消息

[Query Rewriting] (Gemini Flash)
"这个区域需要结构支撑"
→ "structural column FamilyInstance StructuralType.Column OST_StructuralColumns"

[Embedding] (text-embedding-3-large, 3072 维)
在 ChromaDB 中搜索
API 候选 30 个 → rerank 15 个
SDK 候选 5 个 → rerank 3 个

[SQLite Hydration]
批量获取完整 API 文档 + SDK golden code

[Code Generation] (Claude Sonnet 4.6)
System prompt 注入：
  - 执行上下文说明（doc, uidoc, uiapp 可用）
  - 15 条 API 文档（FamilySymbol, StructuralType, Level...）
  - 3 段 SDK 代码（StructuralColumns 示例）

输出：可执行 C# 代码体

[Layer 2 → Layer 3]
RevitClient.send_code(code)
TCP → localhost:8080

[Revit 插件端]
JSON-RPC 接收 → 代码嵌入模板 → 编译 → ExternalEvent 执行

[回传]
{ "jsonrpc": "2.0", "result": { "element_id": 334521 }, "id": "..." }

[固化（可选）]
solidify("create_structural_column", code, params=[...])
→ tools/create_structural_column.yaml

[Layer 3 → Layer 1]
Gradio 界面显示：
  - 生成的 C# 代码（可审查）
  - 执行结果（ElementId）
  - 固化按钮（保存为工具）
```

---

## 7. 与竞品的差异对比（v0.2 更新）

| 能力维度 | Dynamo | revit-mcp (TS) | RevitGeminiRAG | **本系统 v0.2** |
|---------|--------|----------------|----------------|-----------------|
| 输入形式 | 节点连线 | 精确 API 指令 | 自然语言 | 任意粒度自然语言 |
| 需要 API 知识 | 部分 | 必须 | 不需要 | 不需要 |
| RAG 数据质量 | N/A | N/A | 原始 CHM | **剪枝精确签名 27,596 条** |
| 推理过程可见 | ❌ | ❌ | ❌ | ✅ 代码注释 + RAG 上下文 |
| 多步任务分解 | ❌ | ❌ | ❌ | ✅ 体现在生成代码中 |
| 执行能力 | 全量 | 28 tools | 无 | **全量（任意 C# 代码）** |
| 工具自增长 | ❌ | ❌ | ❌ | **✅ 固化机制** |
| .NET 原生 | ❌ | ❌ | ❌ | ✅ 生成的代码即 .NET |
| API 版本覆盖 | N/A | N/A | 2025 | **2026 全量** |
| 维护状态 | 活跃 | 已归档 | 已放弃 | 活跃 |

---

## 8. AU2026 Demo 脚本参考（v0.2 更新）

### 演示场景 A：结构柱创建（首次，60 秒）

| 时间 | 演讲者动作 | 屏幕内容 |
|------|-----------|---------|
| 0–5s | 展示空白 Revit 楼层平面 | Revit 模型视图 |
| 5–8s | 输入："这个区域需要结构支撑" | Gradio 输入框 |
| 8–18s | "系统正在检索 API 文档" | RAG 检索过程：Query Rewriting → ChromaDB 搜索 → Top-15 API 文档 |
| 18–28s | "基于真实 API 签名生成代码" | C# 代码实时流式输出（带 Step 注释） |
| 28–32s | "代码是可审查的——每一行都有据可查" | 高亮代码中的 API 调用与 RAG 检索结果的对应关系 |
| 32–42s | 点击"执行" | 代码通过 TCP 发送 → Revit 模型出现结构柱 → ElementId 回传 |
| 42–48s | 点击"固化" | 工具保存为 create_structural_column.yaml |
| 48–60s | "下次只需说参数，工具已经在那里了" | 展示固化工具列表 |

### 演示场景 B：墙体创建（第二次，用固化工具，20 秒）

| 时间 | 演讲者动作 | 屏幕内容 |
|------|-----------|---------|
| 0–5s | "上次我们固化了 create_wall" | 展示 tools/ 目录 |
| 5–10s | 调用 run_tool("create_wall", {height: 5000}) | 无 RAG、无 LLM，直接填参数执行 |
| 10–15s | Revit 模型出现墙体 | 执行结果回传 |
| 15–20s | "工具库越用越大，这是自进化的 AI 系统" | 固化工具列表增长图 |

### 关键话术（更新）

> "其他工具给你 28 个固定按钮。我们给你整个 Revit API——27,596 个可能性。"

> "第一次是 AI 生成。第二次是一键复用。系统自己长出工具。"

> "推理过程不是黑盒——每一行生成的代码都能追溯到具体的 API 文档。"

---

## 9. 已知限制与风险（v0.2 更新）

| 限制 | v0.1 状态 | v0.2 状态 | 缓解方案 |
|------|----------|----------|---------|
| Tools 数量 | 只有 5 个 | **无限制**（代码生成） | 固化机制持续扩展 |
| 动态执行 | 无 Roslyn | **已解决**（revit-mcp 插件端编译） | — |
| send_code 安全性 | N/A | **新风险**：任意代码执行 | 代码审查 UI + 白名单 API 命名空间 |
| 生成代码质量 | N/A | 取决于 LLM + RAG 上下文 | RAG 提供真实签名，减少幻觉 |
| 网络依赖 | 云端 LLM | 仍需云端 LLM | 确认会场网络 / 本地模型备选 |
| Revit 插件端 | 需自建 | 需部署 revit-mcp 插件 | 插件已开源，MIT License |
| revit-mcp 已归档 | N/A | 原 repo archived | fork 或使用 monorepo 后继者 |

---

## 10. 废弃模块

### Intent Bridge（废弃原因）

经过 10 条调查指令测试，现有 RAG 助手（Tab A）对所有 8 类意图的理解率 ≈ 100%。Intent Bridge 的"意图解析 + 参数填槽"与 RAG 的能力完全重叠。

| 文件 | 处置 |
|------|------|
| `intent_bridge/slot_engine.py` | 废弃（RAG Code Generator 替代） |
| `intent_bridge/models.py` | 废弃 |
| `intent_bridge/router.py` | 废弃（bridge_router 替代） |
| `intent_bridge/llm_adapter.py` | 废弃（pipeline/llm_client.py 复用） |
| `intent_bridge/schemas/intent_slots.yaml` | 保留参考（参数约束定义仍有参考价值） |

### Text2Revit（遗留）

| 文件 | 处置 |
|------|------|
| `server/app/text2revit/` | 遗留保留，不再扩展 |
| Tab B 前端 | 保留但标记为 Legacy |

---

## 11. 实施计划

### Phase 0：已完成 ✅

- [x] 调研 revit-mcp 项目，确认 `send_code_to_revit` 可行性
- [x] 创建 `mcp_bridge/` 模块（5 个 Python 文件）
- [x] 实现 RevitClient（TCP JSON-RPC 2.0）
- [x] 实现 ToolStore（YAML 固化 CRUD）
- [x] 实现 CodeGenerator（RAG 驱动代码生成）
- [x] 实现 MCP Server（7 tools）
- [x] 实现 FastAPI Router（`/api/v1/bridge/*`）
- [x] 注册 bridge_router 到 server/main.py
- [x] 创建示例固化工具 create_wall.yaml

### Phase 1：Revit 插件端对接

- [ ] 部署 revit-mcp 插件到 Revit（Revit 2025/2026）
- [ ] 验证 TCP 连接：Python → localhost:8080 → Revit
- [ ] 端到端测试：generate → execute → 确认 Revit 模型变更
- [ ] 测量端到端时延

### Phase 2：前端集成

- [ ] Gradio Tab D：MCP Bridge 界面
  - 输入框（自然语言）
  - 代码预览区（生成的 C#，可编辑）
  - 执行按钮 + 结果展示
  - 固化按钮（填名称 + 描述 → 保存）
  - 固化工具列表 + 一键执行
- [ ] 推理过程可视化（RAG 检索结果 → 代码生成 → 执行结果）

### Phase 3：稳定性 & 安全

- [ ] 代码沙箱：限制可用命名空间（只允许 Autodesk.Revit.*）
- [ ] 执行前确认 UI（显示代码摘要，用户确认后执行）
- [ ] 错误重试：编译失败 → 将错误信息反馈给 LLM → 重新生成
- [ ] 压力测试：连续 20 次执行稳定性

### Phase 4：Demo 准备

- [ ] AU2026 Demo 场景 A 排练（结构柱首次生成 + 固化）
- [ ] AU2026 Demo 场景 B 排练（固化工具复用）
- [ ] 录屏备份（网络故障预案）
- [ ] 固化 5-10 个常用工具作为 Demo 基础库

---

## 12. 技术栈汇总

| 组件 | 技术 | 版本 |
|------|------|------|
| 后端框架 | FastAPI + Uvicorn | latest |
| 前端 | Gradio | latest |
| 向量数据库 | ChromaDB | latest |
| 全文存储 | SQLite | 3.x |
| Embedding | OpenAI text-embedding-3-large | 3072 维 |
| LLM（代码生成） | Claude Sonnet 4.6 via OpenRouter | latest |
| LLM（Query Rewriting） | Gemini 3 Flash via OpenRouter | latest |
| Reranker | Cohere rerank-v3.5 via OpenRouter | latest |
| MCP SDK | mcp (Python) | latest |
| Revit 通信 | TCP JSON-RPC 2.0 (port 8080) | — |
| Revit 插件 | revit-mcp (fork) | MIT |
| 工具存储 | YAML 文件 | — |
| 目标 Revit | Revit 2026 | — |

---

*文档更新时间：2026-03-13*
*下一步：Phase 1 — 部署 revit-mcp 插件，验证端到端执行*
