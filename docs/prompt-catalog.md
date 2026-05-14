# Revit API RAG — Agent 提示词索引

按提示词工程最佳实践分类，仅列出项目中实际对齐的案例。

---

## 1. 清晰明确的指令和背景上下文

### PromptBridge — 步骤化指令
分步定义响应流程：Step 1 标记纠正 → Step 2 按 Case A/B/C 分支输出。

```
### Step 1: Inline Corrections
Show the user's ORIGINAL sentence with corrections marked inline:
- Use ~~strikethrough~~ for the wrong / vague part
- Immediately follow with **bold** for the correction

### Step 2: Output Prompts
Case A — Clear request: Output ONE precise prompt using [OPTION]
Case B — Ambiguous request: Output 2-4 [OPTION] blocks
Case C — Missing critical info: Output [CHOICE] blocks for user confirmation
```
`prompt_bridge/service.py` · `_build_system_prompt()`

### MCP Bridge 代码生成 — 直接且具体的禁止项
明确告诉模型"做什么"和"不做什么"，每条规则都有具体原因。

```
Rule 1: Do NOT wrap code in a Transaction — the plugin already handles it
Rule 5: All coordinates are in Revit internal units (feet)
Rule 9: Do NOT use doc or uidoc — use the variable named "document"
```
`mcp_bridge/code_generator.py` · `SYSTEM_EXECUTE`

### RAG 代码生成 — 提供动机与背景
直接说明上下文来源和目标，让模型理解为什么拿到这些材料。

```
Given the user's request and the retrieved API documentation
+ SDK code examples below, generate a concise C# code snippet
that addresses the request.
```
`server/app/prompts/templates.py` · `SYSTEM_BRIEF`

---

## 2. 角色设定

### PromptBridge
```
You are PromptBridge — a prompt refinement assistant for Revit.
You transform vague designer requests into precise, executable Revit AI prompts.
```
`prompt_bridge/service.py`

### TextStudio
```
You are TextStudio — a professional multilingual translation
and text refinement assistant.
```
`text_studio/service.py`

### RAG 代码生成
```
You are a Revit 2026 API expert assistant.
```
`server/app/prompts/templates.py`

---

## 3. 结构化提示词与数据分离

### RAG 模板 — 指令与检索数据分区
模板用 `{api_context}` 和 `{code_context}` 占位符，运行时注入检索结果，指令和数据彻底分离。

```
## Rules
1. Use only Revit {revit_version} API...
2. Output the core method only...

## Retrieved API Documentation
{api_context}

## Retrieved SDK Code Examples
{code_context}
```
`server/app/prompts/templates.py`

### PromptBridge — 知识库与 Skills 分层注入
base prompt（固定指令）+ knowledge context（知识库 .md 文件拼接）+ skills context（用户可配置规范）三层拼接，各自独立加载。

```python
base_prompt = _get_system_prompt()          # 固定指令 + 知识库
skills_ctx = get_skill_store().get_active_prompt("prompt_bridge")  # 用户 Skills
system_prompt = base_prompt + "\n\n---\n\n" + skills_ctx
```
`prompt_bridge/service.py` · `process_prompt_bridge_chat()`

---

## 4. 提供案例

### PromptBridge — 标准输出案例
每种 Case 都给出完整的输入→输出示例，模型可直接模仿格式。

```
Example (inline correction):
帮我~~画一面墙~~**创建一面长度 6000mm、高度 3000mm 的内墙（Generic - 200mm）**

Example (OPTION block):
[OPTION: 放置结构柱 / Place Column]
在坐标 (5000, 3000, 0) 处放置一根 W10x49 结构柱，底部标高 Level 1

Example (CHOICE block):
[CHOICE: 内墙 / Interior Wall]
适用于室内分隔，常见厚度 100-200mm
```
`prompt_bridge/service.py`

### API Explorer 查询理解 — 输入→输出映射
给出自然语言到 JSON 的转换示例，消除输出格式歧义。

```
"创建一面墙" → {"entity": "Wall", "action": "Create",
                 "keywords": ["Wall","Create"], "api_terms": ["Wall.Create"]}

"删除所有房间" → {"entity": "Room", "action": "Delete",
                  "keywords": ["Room","Delete"], "api_terms": ["Room","Delete"]}
```
`server/app/prompts/api_explorer.py` · `QUERY_UNDERSTANDING_PROMPT`

---

## 5. 格式与风格对齐

### PromptBridge — 特定格式指令
严格规定标记语法，禁止替代格式。

```
- [OPTION: title] and [CHOICE: title] MUST each start on its own line
- Do NOT use fenced code blocks (```). Use [OPTION] and [CHOICE] markers instead
- Mark unconfirmed values as [TBD / 待确认]
- Be concise — no tables, no lengthy explanations
```
`prompt_bridge/service.py`

### Intent Bridge — 双语风格强制
所有面向用户的文本必须中英双语，格式固定。

```
HIGHEST PRIORITY — ALL question text MUST be bilingual:
Format: "中文说明 / English description"
```
`intent_bridge/slot_engine.py` · `_ANALYZE_PROMPT`

### TextStudio — 输出风格规则
匹配翻译场景的不同输出格式。

```
For translation: Output the translation directly — no preamble
For polishing: Output polished version first, then note key changes after ---
For grammar check: Show corrections inline: ~~wrong~~**correct**
```
`text_studio/service.py`

---

## 6. 降低幻觉和提供依据

### MCP Bridge 代码生成 — API 锚定规则
明确禁止编造，要求引用检索到的文档。

```
IMPORTANT — API Grounding Rules:
- Only use Revit API classes, methods, properties, and enums
  that appear in the documentation context above
- If the user asks for functionality not covered by the retrieved docs,
  say so — do NOT invent class names or method signatures
```
`mcp_bridge/code_generator.py` · `SYSTEM_EXECUTE`

### MCP Bridge 交互分类 — 限定类别枚举
只允许从预定义列表中选取，未知时用兜底值。

```
Anti-hallucination rules:
- ONLY use BuiltInCategory names from the provided list
- Use OST_GenericModel for uncertain cases
- Do NOT invent category names
```
`mcp_bridge/interactive.py` · `_CLASSIFY_SYSTEM`

### Intent Bridge — 禁止静默默认
宁可追问也不假设参数值。

```
NEVER silently default ANY parameter —
Extract exact values from user input OR generate a question to ask the user.
```
`intent_bridge/slot_engine.py` · `_ANALYZE_PROMPT`

---

## 7. 工具使用和智能体编排

### Intent Bridge — enrich 标记驱动 Revit 数据填充
问题选项不由 LLM 编造，而是标记 `enrich` 字段，运行时由 Revit 插件动态替换为真实数据。

```json
{
  "text": "请选择墙体类型 / Select wall type",
  "enrich": "family_type:OST_Walls",
  "options": ["(由 Revit 运行时填充)"]
}
```
`intent_bridge/slot_engine.py` · enrich 类型：`family_type:<category>` / `level` / `host_pick` / `none`

### MCP Bridge — 交互分类 → 代码生成编排
先用分类提示词判断交互类型，再根据结果编排后续工具调用（选族/选宿主/直接执行）。

```
classify_intent(user_query)
  → "select_family"  → 调用 Revit 获取族列表 → 用户选择 → code_generate()
  → "select_both"    → 选宿主 → 选族 → code_generate()
  → "direct"         → code_generate()
```
`mcp_bridge/interactive.py` + `mcp_bridge/code_generator.py`

### 所有意图模块 — JSON 结构化输出
复杂任务强制 JSON 格式输出，便于程序解析和流程编排。

```
Text2Revit:  {"intent": "create_wall", "extracted_params": {...}}
API Explorer: {"entity": "Wall", "action": "Create", "keywords": [...]}
Interactive:  {"interaction_type": "select_family", "revit_categories": [...]}
```
`server/app/text2revit/intent.py` · `server/app/prompts/api_explorer.py` · `mcp_bridge/interactive.py`

---

## 未匹配的类别

**第 4 条「长文本结构优化：文档内容放顶部，查询和制定放底部」** — 项目中 RAG context 放在 system prompt 中部（Rules 之后），用户消息单独作为 user message 传入，未严格采用"顶部文档 + 底部查询"结构。
