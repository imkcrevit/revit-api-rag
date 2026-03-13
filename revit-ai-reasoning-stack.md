# Revit AI Reasoning Stack — 技术文档

> 版本：v0.1 草稿（用于与现有实现核对）  
> 作者：待填入  
> 日期：2026-03-13

---

## 1. 系统概述

本系统是一个三层架构的 AI 辅助 BIM 设计执行栈，核心目标是：

**将模糊的设计意图转化为 Revit 可执行操作，全程推理过程透明可见。**

区别于现有 MCP 工具（接受精确 API 指令），本系统的输入可以是任意粒度的自然语言，由系统内部完成意图解析、API 路径推理和执行计划生成。

---

## 2. 整体架构

```
┌─────────────────────────────────────────────┐
│           Layer 1: Intent Interface          │
│  自然语言输入 / Web 前端 / Gradio            │
└─────────────────────┬───────────────────────┘
                      ↓
┌─────────────────────────────────────────────┐
│         Layer 2: RAG Reasoning Engine        │
│                                             │
│  ┌─────────────┐    ┌─────────────────────┐ │
│  │  Embedding  │    │     Retriever        │ │
│  │  (向量化)   │ →  │  (Top-K 召回)        │ │
│  └─────────────┘    └──────────┬──────────┘ │
│                                ↓             │
│                    ┌─────────────────────┐   │
│                    │   Multi-Step        │   │
│                    │   API Reasoning     │   │
│                    │   (任务分解)         │   │
│                    └──────────┬──────────┘   │
│                                ↓             │
│                    ┌─────────────────────┐   │
│                    │  Execution Plan     │   │
│                    │  Generation (JSON)  │   │
│                    └─────────────────────┘   │
└─────────────────────┬───────────────────────┘
                      ↓
┌─────────────────────────────────────────────┐
│          Layer 3: MCP Execution              │
│                                             │
│  JSON 调度 → revit-mcp-net → Revit API      │
│  反射调用预编译 DLL Method                   │
│  ExternalEvent 线程安全执行                  │
│  结果 / ElementId 回传                       │
└─────────────────────────────────────────────┘
```

---

## 3. Layer 1：意图输入层

### 3.1 功能定义

- 接受任意粒度的自然语言输入
- 支持中英文
- 当前实现：Gradio Web UI（本地 `0.0.0.0:7860`）

### 3.2 输入粒度示例

| 粒度 | 示例输入 |
|------|---------|
| 精确指令 | "在坐标 (0,0) 创建 400×400 混凝土柱，高度 3600mm" |
| 半模糊 | "创建一根结构柱" |
| 设计意图 | "这个区域需要结构支撑" |

### 3.3 待核对

- [ ] Gradio 前端是否支持多轮对话上下文？
- [ ] 输入是否有预处理（清洗、语言检测）？
- [ ] 是否支持图片/附件输入？

---

## 4. Layer 2：RAG 推理引擎

> 本层是系统核心，也是与所有现有竞品的最大差异点。

### 4.1 知识库

| 属性 | 现状 |
|------|------|
| 覆盖版本 | Revit 2026 全量 API |
| 数据处理 | SDK 剪枝 + API 剪枝 |
| 剪枝内容 | 保留精确方法签名（命名空间、参数类型、参数顺序） |
| 向量化模型 | 待填入 |
| 向量数据库 | 待填入 |

**说明**：SDK 剪枝和 API 剪枝是本系统相对竞品的核心数据优势。原始 CHM 转 Markdown 的方案噪音大，签名不精确，导致代码生成质量低。本系统的剪枝数据直接包含可用于反射调用的精确签名。

### 4.2 Embedding 阶段

```
用户输入
   ↓
文本向量化
   ↓
在 Revit 2026 API 向量空间中定位
   ↓
输出：语义最近邻候选集
```

**待核对**：
- [ ] Embedding 模型是本地还是云端 API？
- [ ] 向量维度是多少？
- [ ] 目前 UI 上展示的 embedding 结果是什么形式（坐标？相似度分数？）

### 4.3 Retriever 阶段

```
候选集
   ↓
Top-K 精排（当前 K 值待确认）
   ↓
输出：Top-K API 文档片段
   包含：类名 / 方法名 / 参数签名 / 简要说明
```

**待核对**：
- [ ] 当前 K 值是多少？
- [ ] 是否有重排序（reranker）步骤？
- [ ] UI 显示的是原始文档片段还是结构化摘要？

### 4.4 多步 API 推理（任务分解）

这是本系统最关键的能力。

**流程**：

```
用户意图 + Top-K API 上下文
              ↓
LLM 判断：这个意图需要几个 API 调用？
              ↓
分解为有序的子任务列表
[Step 1] → [Step 2] → [Step 3] ...
              ↓
对每个 Step 验证 API 可行性
（当前 tools 列表里有没有对应的 command？）
              ↓
生成完整执行计划
```

**当前已验证的案例**：
- 创建结构柱（完整 solution 可输出）

**待核对**：
- [ ] 任务分解是单次 LLM 调用还是多轮？
- [ ] API 可行性验证是基于 tools 列表还是纯 RAG 判断？
- [ ] 推理链的步骤数量当前上限是多少？
- [ ] 有没有依赖关系处理（Step 2 依赖 Step 1 的输出）？

### 4.5 执行计划生成

**输出格式**（待核对实际格式）：

```json
{
  "intent": "创建结构柱",
  "reasoning_steps": [
    {
      "step": 1,
      "api": "FamilySymbol",
      "purpose": "定位结构柱族类型",
      "retrieved_context": "Autodesk.Revit.DB.FamilySymbol"
    },
    {
      "step": 2,
      "api": "Level",
      "purpose": "获取当前标高",
      "retrieved_context": "Autodesk.Revit.DB.Level.Elevation"
    },
    {
      "step": 3,
      "api": "StructuralFramingUtils",
      "purpose": "创建柱实例",
      "retrieved_context": "Autodesk.Revit.DB.Structure.StructuralType.Column"
    }
  ],
  "execution_plan": {
    "command": "create_structural_column",
    "params": {
      "family": "UC305x305x97",
      "level": "Level 1",
      "point": {"x": 0, "y": 0},
      "structural_type": "Column"
    }
  }
}
```

**待核对**：
- [ ] 实际输出的 JSON schema 是什么结构？
- [ ] reasoning_steps 是否包含在最终输出里，还是仅用于 UI 展示？

---

## 5. Layer 3：MCP 执行层

### 5.1 执行机制

```
执行计划 JSON
     ↓
读取 command name
     ↓
LLM 生成调用参数（对照 tools 列表中的 desc）
     ↓
JSON 参数结构化
     ↓
反射调用预编译 DLL 中对应的 Method
     ↓
ExternalEvent 触发（Revit 线程安全）
     ↓
Revit API 执行
     ↓
结果 / ElementId 回传
```

### 5.2 当前 Tools 列表

| 序号 | Command Name | Description | 状态 |
|------|-------------|-------------|------|
| 1 | 待填入 | 待填入 | ✅ |
| 2 | 待填入 | 待填入 | ✅ |
| 3 | 待填入 | 待填入 | ✅ |
| 4 | 待填入 | 待填入 | ✅ |
| 5 | 待填入 | 待填入 | ✅ |

> 注：当前共 5 个预编译 command，覆盖基础操作场景。

### 5.3 执行约束

- **Revit 线程模型**：所有 Revit API 调用必须在主线程执行，通过 `ExternalEvent.Raise()` 触发
- **事务管理**：写操作必须包裹在 `Transaction` 内
- **错误回传**：执行失败时返回错误信息至 Layer 2，当前回传机制待核对

### 5.4 待核对

- [ ] 5 个 command 的具体名称和功能
- [ ] 错误回传是否结构化（错误类型、错误信息、堆栈）？
- [ ] 执行超时处理是否有实现？
- [ ] 当前端到端时延（从输入到 Revit 响应）大概多少秒？

---

## 6. 数据流完整路径

以"创建结构柱"为例：

```
[输入]
"这个区域需要结构支撑"

[Layer 1 → Layer 2]
原始文本传入推理引擎

[Embedding]
文本向量化
相似度：StructuralColumn(0.94) > Wall(0.71) > Beam(0.68)

[Retriever Top-3]
1. Autodesk.Revit.DB.Structure.StructuralType
2. FamilySymbol (StructuralType.Column)
3. StructuralFramingUtils.MakeColumnFamily()

[多步推理]
Step 1: 确认族类型 → StructuralType.Column
Step 2: 获取 Level → Level.Elevation
Step 3: 确定插入点 → 默认模型中心 (0,0)
Step 4: 选择截面 → 默认族 UC305x305x97

[执行计划]
{
  "command": "create_structural_column",
  "params": { "family": "UC305x305x97", "level": "Level 1", "point": {"x":0,"y":0} }
}

[Layer 2 → Layer 3]
JSON 传入 revit-mcp-net

[反射调用]
DLL.Method("create_structural_column", params)

[ExternalEvent]
Revit 主线程执行 API 调用

[回传]
{ "status": "success", "element_id": 334521 }

[Layer 3 → Layer 1]
Gradio 界面显示执行结果
Revit 视图更新
```

---

## 7. 与竞品的差异对比

| 能力维度 | Dynamo | revit-mcp (TS) | RevitGeminiRAG | 本系统 |
|---------|--------|----------------|----------------|--------|
| 输入形式 | 节点连线 | 精确 API 指令 | 自然语言 | 任意粒度自然语言 |
| 需要 API 知识 | 部分 | 必须 | 不需要 | 不需要 |
| RAG 数据质量 | N/A | N/A | 原始 CHM | 剪枝精确签名 |
| 推理过程可见 | ❌ | ❌ | ❌ | ✅ |
| 多步任务分解 | ❌ | ❌ | ❌ | ✅ |
| .NET 原生 | ❌ | ❌ | ❌ | ✅ |
| 维护状态 | 活跃 | 已归档 | 已放弃 | 活跃 |
| API 版本覆盖 | N/A | N/A | 2025 | 2026 全量 |

---

## 8. AU2026 Demo 脚本参考

### 演示场景：结构柱创建（建议时长 60 秒）

| 时间 | 演讲者动作 | 屏幕内容 |
|------|-----------|---------|
| 0–5s | 展示空白 Revit 楼层平面 | Revit 模型视图 |
| 5–8s | 输入："这个区域需要结构支撑" | Gradio 输入框 |
| 8–25s | "让我们看系统在想什么" | Layer 2 推理过程实时滚动：Embedding 结果 + Top-3 召回 + 分解步骤 |
| 25–35s | "这是生成的执行计划" | JSON 执行计划显示 |
| 35–50s | 点击执行 | Revit 模型出现结构柱，ElementId 回传 |
| 50–60s | "没有一行代码，没有 API 文档" | 对比 Dynamo / 普通 MCP 截图 |

### 关键话术

> "所有现有工具解决的是'怎么执行'，我们解决的是'执行什么'。这是上游问题。"

> "推理过程可见，意味着这个系统是可信的、可审计的、可调试的。这是企业级 AI 工具的基本要求。"

---

## 9. 已知限制与风险

| 限制 | 当前状态 | 缓解方案 |
|------|---------|---------|
| Tools 只有 5 个 | 覆盖场景有限 | Demo 聚焦已覆盖场景，将扩展作为 roadmap |
| 动态执行不可用 | 无 Roslyn 编译层 | 当前架构不需要，作为未来方向 |
| 演示稳定性 | 待压测 | 提前准备录屏备份 |
| 网络依赖 | 云端 LLM 调用 | 确认会场网络或准备本地模型 |

---

## 10. 待核对清单（汇总）

### Layer 1
- [ ] Gradio 是否支持多轮上下文
- [ ] 输入预处理逻辑

### Layer 2
- [ ] Embedding 模型和向量数据库具体实现
- [ ] Top-K 当前值
- [ ] 是否有 reranker
- [ ] 任务分解是单次还是多轮 LLM 调用
- [ ] 推理链步骤数量上限
- [ ] 依赖关系处理
- [ ] 实际 JSON 输出 schema

### Layer 3
- [ ] 5 个 command 的具体名称和功能
- [ ] 错误回传结构
- [ ] 执行超时处理
- [ ] 端到端时延实测数据

### 整体
- [ ] 当前最复杂的稳定可复现场景
- [ ] 是否有日志系统
- [ ] 本地部署还是云端部署

---

*文档生成时间：2026-03-13*  
*下一步：对照此文档逐项核对现有实现，标注与实际不符的部分*
