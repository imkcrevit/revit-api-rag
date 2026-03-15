# 使用指南 — Intent Bridge 交互操作

本文档演示如何通过 Gradio Web UI 与 Revit 进行 AI 辅助交互，涵盖单步命令执行和多步交互选择两种模式。

> 前置条件：Revit 2026 已安装插件（[安装方法](../README.md#revit-插件--v02)），页面顶部状态栏显示 `Revit Connected`。

---

## 界面概览

UI 由 5 个步骤组成，每一步对应一个可折叠面板：

| 步骤 | 名称 | 说明 |
|------|------|------|
| Step 1 | **Input** | 输入自然语言指令 |
| Step 2 | **Select Options** | 多步模式下选择族类型、标高等参数 |
| Step 3 | **Review Code** | 查看 LLM 生成的 C# 代码和 Thinking 推理过程 |
| Step 4 | **Execute** | 将代码发送到 Revit 执行并查看结果 |
| Step 5 | **Solidify** | 将成功的代码固化为可复用工具 |

顶部进度条实时标记当前所处步骤，右上角显示耗时计时器。

---

## 模式一：单步命令执行（Direct）

适用于查询、修改、删除等**不需要**预先选择族类型或宿主元素的操作。

### 示例指令

```
查询所有墙体的信息
删除所有结构柱
修改墙高度为 4000mm
获取当前选中元素的属性
列出所有楼层标高
```

### 执行流程

```
Step 1: Input
    │  输入: "查询所有墙体的信息"
    │  点击 "Generate Code"
    │
    ▼
意图分类 → Direct（单步）
    │
    ▼
Step 3: Review Code（跳过 Step 2）
    │  Pipeline 进度日志逐步显示：
    │    ✓ Query Rewrite done
    │    ✓ Embedding generated
    │    ✓ Vector Search — API: 15, Code: 5
    │    ✓ Hydrating results from SQLite
    │    ✓ Combining API docs + SDK code into RAG context
    │    ✓ Assembling system prompt
    │    ● LLM generating... 28 lines, 156 tokens
    │    ✓ Code extracted & security reviewed — Safe
    │
    │  Thinking 面板显示 LLM 推理过程（流式）
    │  Code 面板显示生成的 C# 代码
    │
    ▼
Step 4: Execute
    │  审查代码后点击 "Execute in Revit"
    │  → 代码通过 TCP 发送到 Revit 插件
    │  → Roslyn 动态编译并执行
    │  → 结果回显到界面
    │
    ▼
Step 5: Solidify（可选）
    │  执行成功后，输入工具名称和描述
    │  点击 "Solidify Tool" 保存为可复用工具
```

### 操作步骤（图文）

1. **输入指令** — 在 Step 1 的文本框中输入自然语言，例如 `查询所有墙体的信息`
2. **点击 Generate Code** — 系统自动进入 Pipeline：
   - 意图分类为 `Direct`，跳过 Step 2
   - 进度日志逐行显示每个阶段
   - Thinking 面板实时展示 LLM 的推理链
   - Code 面板展示最终 C# 代码
3. **审查代码** — 展开 Step 3 面板查看完整代码，确认安全审查状态为 `Safe`
4. **执行** — 点击 `Execute in Revit`，等待 Revit 返回结果
5. **（可选）固化** — 如果代码值得复用，在 Step 5 填写名称和描述后保存

---

## 模式二：多步交互 — 族类型选择（Select Family）

适用于**创建**需要指定族类型的元素，如墙体、结构柱、梁、楼板等。

### 示例指令

```
创建结构柱
在 (3000, 5000) 位置创建一面墙
放置一根梁
创建楼板
```

### 支持的元素类型

| 指令关键词 | 元素类型 | Revit 类别 | 需要标高 |
|-----------|---------|-----------|---------|
| 墙 / wall | 墙体 | OST_Walls | 是 |
| 结构柱 / structural column | 结构柱 | OST_StructuralColumns | 是 |
| 梁 / beam | 梁 | OST_StructuralFraming | 是 |
| 楼板 / floor | 楼板 | OST_Floors | 是 |
| 天花板 / ceiling | 天花板 | OST_Ceilings | 是 |
| 屋顶 / roof | 屋顶 | OST_Roofs | 是 |
| 栏杆 / railing | 栏杆 | OST_StairsRailing | 是 |
| 楼梯 / stair | 楼梯 | OST_Stairs | 是 |

### 执行流程

```
Step 1: Input
    │  输入: "创建结构柱"
    │  点击 "Generate Code"
    │
    ▼
意图分类 → Select Family（多步）
    │  系统识别出需要: 族类型 + 标高
    │
    ▼
查询 Revit
    │  → get_available_family_types(OST_StructuralColumns)
    │  → get_levels()
    │
    ▼
Step 2: Select Options
    │  Family Type 下拉框: 显示 Revit 中所有可用的结构柱族类型
    │    例如: UC305x305x97, HEB200, W10x49 ...
    │  Level 单选框: 显示所有标高
    │    例如: Level 1 (0mm), Level 2 (4000mm) ...
    │  X / Y 输入框: 放置坐标（如指令中包含坐标则自动填入）
    │
    │  用户选择后点击 "Confirm & Generate Code"
    │
    ▼
Step 3: Review Code
    │  Pipeline 流式生成（同单步模式）
    │  LLM 根据用户选择的族类型、标高、坐标生成精确代码
    │  Thinking 面板展示推理过程
    │
    ▼
Step 4: Execute
    │  审查后点击 "Execute in Revit"
    │  → Revit 中创建结构柱
    │
    ▼
Step 5: Solidify（可选）
```

### 操作步骤（图文）

1. **输入指令** — `创建结构柱` 或 `在 (3000, 5000) 位置创建一面墙`
2. **点击 Generate Code** — 系统进行意图分类，识别为多步操作
3. **等待 Revit 查询** — 进度显示 `Querying Revit for family types...` → `Querying levels...`
4. **选择参数** — Step 2 面板自动展开：
   - 从 **Family Type** 下拉框选择族类型（支持搜索过滤）
   - 从 **Level** 单选框选择放置标高
   - 确认或修改 **X / Y** 坐标
5. **点击 Confirm & Generate Code** — 进入 SSE 流式代码生成
6. **审查并执行** — 同单步模式的 Step 3 → Step 4

### 坐标自动提取

指令中包含坐标时系统自动解析并填入 X/Y 输入框：

```
在 (3000, 5000) 位置创建结构柱    → X=3000, Y=5000
创建墙 (1000, 2000, 4000)         → X=1000, Y=2000, Z=4000mm → 自动匹配最近标高
```

---

## 模式三：多步交互 — 宿主选择 + 族类型（Select Both）

适用于**创建宿主依赖元素**，如在墙上放窗户、在墙上安装门。

### 示例指令

```
在墙上创建窗户
放置一扇门
选择一个墙体创建窗户
```

### 支持的宿主元素类型

| 指令关键词 | 元素类型 | 需要宿主 |
|-----------|---------|---------|
| 窗户 / window | 窗 | 墙体 |
| 门 / door | 门 | 墙体 |

### 执行流程

```
Step 1: Input
    │  输入: "在墙上创建窗户"
    │  点击 "Generate Code"
    │
    ▼
意图分类 → Select Both（多步 + 宿主选择）
    │  系统识别出需要: 宿主元素（墙） + 窗户族类型 + 标高
    │
    ▼
查询 Revit
    │  → get_available_family_types(OST_Windows)
    │  → get_levels()
    │
    ▼
Step 2: Select Options
    │  状态提示: "请在 Revit 中选择要放置窗户的墙体"
    │  Family Type 下拉框: 窗户族类型列表
    │  Level 单选框: 标高列表
    │
    │  【宿主选择区域 — 仅 Select Both 模式显示】
    │  点击 "Select Host in Revit" 按钮
    │    → Revit 进入 PickObject 选择模式
    │    → 用户在 Revit 中点击一面墙
    │    → 界面显示: "Basic Wall (ID: 12345, Walls)"
    │
    │  选择完成后点击 "Confirm & Generate Code"
    │
    ▼
Step 3 → Step 4 → Step 5（同上）
```

### 操作步骤（图文）

1. **输入指令** — `在墙上创建窗户`
2. **点击 Generate Code** — 分类为 `Select Both`
3. **选择族类型和标高** — 从下拉框和单选框选择
4. **选择宿主墙体** — 点击 `Select Host in Revit` 按钮：
   - 此时 Revit 窗口会弹到前台，进入元素选择模式
   - 在 Revit 视图中**点击一面墙**
   - 界面自动显示选中墙体的名称和 ID
5. **确认并生成** — 点击 `Confirm & Generate Code`
6. **执行** — 审查代码后点击 `Execute in Revit`

---

## Thinking 推理过程

LLM 在生成代码时会产生 `<thinking>` 推理过程，展示其分析逻辑：

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

Thinking 面板位于 Step 3 上方，固定高度 200px 可滚动查看，在 LLM 流式输出期间逐步更新。

---

## Pipeline 进度日志

每次代码生成会经过 9 个阶段，进度日志面板实时显示：

| 阶段 | 说明 |
|------|------|
| Query Rewrite | LLM 将自然语言改写为 API 检索关键词 |
| Embedding | 生成查询向量 |
| Vector Search | ChromaDB 语义检索（API + SDK） |
| Hydrating | 从 SQLite 回查完整文档内容 |
| Combining | 合并 API 文档和 SDK 代码上下文 |
| Assembling | 组装系统 Prompt（规则 + 上下文 + 单位配置） |
| LLM Generating | 流式生成 C# 代码（实时显示行数和 token 数） |
| Extracting | 从 LLM 输出中提取代码块 |
| Security Review | 扫描危险 API 调用（Process.Start, File.Delete 等） |

---

## Solidified Tools — 工具固化与复用

成功执行的代码可以保存为可复用工具，下次直接调用无需重新生成。

### 固化步骤

1. 代码执行成功后，展开 **Step 5: Solidify** 面板
2. 输入工具名称（英文，如 `create_structural_column`）
3. 输入工具描述（如 `创建结构柱到指定位置`）
4. 点击 **Solidify Tool**

### 使用已固化的工具

1. 切换到 **Tool Library** 标签页
2. 从工具列表中点击选择一个工具
3. 点击 **Load Choices** — 系统自动查询 Revit 获取参数选项
   - 族类型参数 → 下拉框（从 Revit 动态查询）
   - 标高参数 → 下拉框（从 Revit 动态查询）
   - 其他参数 → 文本输入框
4. 填写参数后点击 **Run Tool**

---

## 单位配置

系统支持三种单位：`mm`（默认）、`m`、`feet`

- 页面加载时自动检测 Revit 项目单位
- 可在 **Settings** 面板手动切换
- LLM 生成代码时自动插入正确的单位转换逻辑（mm/m → feet）

---

## 常见问题

### 状态栏显示 Revit Disconnected

- 确认 Revit 2026 已启动且加载了 `mcp-servers-for-revit` 插件
- 确认 TCP 端口 18080 未被占用
- 点击 **Refresh** 按钮重试

### Family Type 下拉框为空

- 当前 Revit 项目中可能没有加载对应类别的族
- 尝试在 Revit 中先载入所需的族文件

### 代码执行失败

- 检查 Step 3 的安全审查状态
- 查看错误信息 — 常见原因：族未激活、标高不存在、元素 ID 无效
- 可以手动修改 Code 面板中的代码后重新执行

### Select Host 按钮无响应

- 确认 Revit 窗口没有被对话框阻塞
- PickObject 需要 Revit 处于可交互状态（非命令执行中）
- 如果 PickObject 失败，系统会自动回退到读取当前选中元素
