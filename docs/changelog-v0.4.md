# V0.4 Changelog — Skill System + Progressive RAG + Anti-Hallucination

> 2026.04 更新详情 | [English below](#english-version)

---

## 中文版

### 模块化 Skill 系统

三层 Skill 架构，替代硬编码提示词：

```
intent_bridge/schemas/skills/
├── _base.md              # 全局基础规则（所有 prompt 共享）
├── patterns/             # 操作模式层（8 个）
│   ├── line_based.md     # 线性元素（墙、梁、管道）
│   ├── point_based.md    # 点放置元素（柱、族实例）
│   ├── surface_based.md  # 面元素（楼板、屋顶、天花板）
│   ├── hosted.md         # 宿主元素（门、窗）
│   ├── query.md          # 查询操作
│   ├── modify.md         # 修改操作
│   ├── delete.md         # 删除操作
│   └── ...
├── workflows/            # 工作流层（NEW — 复合多步操作蓝图）
│   ├── clearance_calculation.md   # 净高/间距计算（5 阶段蓝图）
│   └── element_data_export.md     # 元素数据导出（4 阶段蓝图）
└── standards/            # 企业规范层
    ├── mep_routing.md
    └── fire_zone.md
```

**Skill 匹配优先级**: workflow (score×4) > action pattern (score×3) > object pattern (score×2) > standard (all stacked)

**工作流 Skill 特点**:
- 参考蓝图（Reference Blueprint）而非固定流程 — LLM 根据用户实际需求灵活裁剪
- 每个蓝图包含：阶段分解、关键 API 速查表、用户交互决策点、适配指南
- 触发关键词匹配后自动注入 prompt，引导 LLM 生成结构化 `action_plan`

**关键文件变更**:
| 文件 | 变更 |
|------|------|
| `intent_bridge/skill_loader.py` | 新增 `workflows/` 层加载、workflow 优先级评分、热重载 |
| `intent_bridge/schemas/skills/_base.md` | 新增反幻觉规则、参数来源声明、自检清单 |
| `intent_bridge/schemas/skills/workflows/*.md` | 新增 2 个工作流蓝图 |

---

### 渐进式 RAG 扩展（Progressive RAG Expansion）

首次 RAG 查询结果不佳时，自动逐步扩大搜索范围（最多 3 轮）：

```
Round 1: 原始搜索词, limit=8
    ↓ 质量评分 < 0.4?
Round 2: + 类级别回退词 (Collector→FilteredElementCollector), limit=12
    ↓ 质量评分 < 0.4?
Round 3: + 全量中文→API映射, limit=15
    ↓
过滤噪声 (RadialArray, LinearArray...) → 截取 top 10
```

**RAG 质量评分** (`_score_rag_quality`):
- 方法级文档比例（有 syntax + parameters 的 docs）× 0.3
- 搜索词覆盖率（多少 search terms 出现在结果中）× 0.5
- 噪声比例（Array、Exception 等无关文档）× 0.2

**上下文感知搜索词提取** (`_extract_search_terms`):
- 检测查询语境 vs 创建语境，动态调整 API 映射优先级
- "创建墙" → 保留 `Wall.Create`（真元素创建）
- "创建视图着色" → 去除 `Floor.Create` 等（非元素创建动作）
- 判断逻辑：`创建` 后 3 字符内是否包含元素名词（墙/柱/梁/板/门/窗...）

**SQL 查询优化**:
- ORDER BY 优先级调整：Collector/Filter/BoundingBox/Parameter > Geometry > Create > Array
- 移除 `syntax IS NOT NULL` 硬性过滤 — 允许类级别文档（FilteredElementCollector Class）进入结果

**关键文件变更**:
| 文件 | 变更 |
|------|------|
| `intent_bridge/slot_engine.py` | `_score_rag_quality()` 新增、`_extract_search_terms()` 上下文检测、`process_turn()` 三轮扩展、SQL ORDER BY 优化 |

---

### 多层反幻觉防线（Anti-Hallucination Defense）

从 Claude Code 源码工程实践中提取的反幻觉策略，应用到所有 LLM 交互点：

**1. 合理化倾向识别（Rationalizations Recognition）**

在 `_base.md` 中加入 5 种常见 LLM 借口模式：
- "根据我的理解..." → 理解 ≠ 验证
- "应该可以用..." → 应该 ≠ 确认
- "文档中似乎提到..." → 似乎 ≠ 确实
- "通常做法是..." → 通常 ≠ 一定
- "这个方法应该存在..." → 应该存在 ≠ 文档记载

**2. 忠实报告（Faithful Reporting）**

5 项禁止行为：
- 禁止声称 API 存在但文档未记录
- 禁止合并不同 API 的参数
- 禁止静默省略必需参数
- 禁止用"合理"默认值填充 slot
- 禁止将不确定的信息呈现为确定

**3. RAG Grounding Rules**

在 `_ANALYZE_PROMPT_V2` 中强制 LLM：
- 仅使用 RAG 返回的 API（不可自创）
- 方法签名必须精确匹配文档
- 文档不足时如实报告而非捏造
- slot 值必须从用户原文可追溯

**4. API Grounding Rules（代码生成层）**

在 `code_generator.py` 的 `SYSTEM_EXECUTE` 中：
- 只使用文档中记录的类/方法
- 验证方法签名匹配
- 优先使用版本兼容 API

**5. 参数来源声明（Parameter Source Protocol）**

| source 类型 | LLM 行为 |
|------------|---------|
| `query:levels` | 从 atom 查询结果中选择，禁止编造 |
| `interactive:pick_object` | 标记为需要用户在 Revit 中操作 |
| `ask_user` | 必须生成 question |
| `default` | 使用预设值，但告知用户 |
| `compute` | 运行时计算，LLM 不填值 |

**关键文件变更**:
| 文件 | 变更 |
|------|------|
| `intent_bridge/schemas/skills/_base.md` | 合理化识别、忠实报告、参数来源、自检清单 |
| `intent_bridge/slot_engine.py` | RAG Grounding Rules（`_ANALYZE_PROMPT_V2`）|
| `mcp_bridge/code_generator.py` | API Grounding Rules |
| `mcp_bridge/interactive.py` | 反幻觉分类规则 |
| `mcp_bridge/mcp_server.py` | 参数来源协议、合理化识别 |
| `mcp_bridge/retry.py` | 根因诊断指令（不盲目重试） |

---

### Atom Registry（Revit 查询原语注册表）

统一注册 20 个 Revit 查询/交互原语，供 Tool 参数声明引用：

```python
# 14 个 Query 原语
levels, views, phases, worksets, materials, line_styles, fill_patterns,
rooms, wall_types, floor_types, ceiling_types, roof_types,
family_types:{category}, elements:{category}

# 6 个 Interactive 原语
pick_object, pick_point, pick_edge, pick_face, pick_objects, select_current
```

每个原语包含：`key`, `label`, `returns`, `cacheable`, `revit_code`（C# 查询代码）

**AtomResolver**:
- `resolve(atom_key)` → 执行 C# 代码查询 Revit，返回 `[{label, value}]` 选项列表
- `resolve_tool_params(tool_params)` → 批量解析 Tool 中所有 atom-sourced 参数
- 支持缓存（`cacheable=True` 的原语跨会话复用）

**关键文件变更**:
| 文件 | 变更 |
|------|------|
| `mcp_bridge/atom_registry.py` | 新增（~400 行）— 完整的原语注册表 + 解析器 |

---

### Tool 健康检查与自动降级

**Tool 元数据增强** (`tool_store.py`):
- `applies_when: list[str]` — 适用场景（提升匹配精度）
- `not_for: list[str]` — 排除场景（防止误匹配）
- `preconditions: list[str]` — 前置条件
- `failure_count: int` — 连续失败计数

**健康检查** (`health_check()`):
- `failure_count >= 2` → 标记为不健康
- 30 天未使用 → 标记为过时
- 从未执行过 → 标记为未验证

**自动降级**: 不健康的 Tool 在 `match_tool()` 中被排除，系统自动回退到 RAG 代码生成

**关键文件变更**:
| 文件 | 变更 |
|------|------|
| `mcp_bridge/tool_store.py` | `health_check()`, `record_usage()`, `validate_params()`, `applies_when`/`not_for` |
| `mcp_bridge/mcp_server.py` | 执行前健康检查、失败记录、降级提示 |
| `mcp_bridge/tools/_example_create_wall_v2.yaml` | 新格式示例（含 source、preconditions） |

---

### 测试

新增端到端测试脚本 `intent_bridge/tests/test_beam_clearance.py`：

7 步验证链：
1. Search Term Extraction — 搜索词提取（含上下文检测）
2. RAG API Doc Lookup — API 文档检索（含质量评分）
3. Skill Matching — 技能匹配（workflow + pattern）
4. Full Prompt Construction — 完整 prompt 组装
5. LLM Call — LLM 调用
6. JSON Extraction — 结构化输出解析
7. Full Orchestrator Run — 完整编排器运行

测试输入："找到当前视图所有的结构梁 并且计算梁底与链接模型制定楼板的净高，最终将参数添加到备注标记中，创建视图着色"

验证结果：5 步 composite action_plan，正确覆盖梁收集 → 链接模型选择 → 楼板选择 → 参数写入 → 视图着色。

---

<a id="english-version"></a>

## English Version

### Modular Skill System

Three-layer Skill architecture replacing hardcoded prompts:

```
intent_bridge/schemas/skills/
├── _base.md              # Global base rules (shared by all prompts)
├── patterns/             # Operation mode layer (8 skills)
│   ├── line_based.md     # Linear elements (wall, beam, pipe)
│   ├── point_based.md    # Point-placed elements (column, family instance)
│   ├── surface_based.md  # Surface elements (floor, roof, ceiling)
│   ├── hosted.md         # Hosted elements (door, window)
│   ├── query.md          # Query operations
│   ├── modify.md         # Modification operations
│   ├── delete.md         # Deletion operations
│   └── ...
├── workflows/            # Workflow layer (NEW — composite multi-step blueprints)
│   ├── clearance_calculation.md   # Clearance/distance calculation (5-phase blueprint)
│   └── element_data_export.md     # Element data export (4-phase blueprint)
└── standards/            # Enterprise standards layer
    ├── mep_routing.md
    └── fire_zone.md
```

**Skill matching priority**: workflow (score×4) > action pattern (score×3) > object pattern (score×2) > standard (all stacked)

**Workflow Skill design**:
- Reference blueprints, not rigid templates — LLM adapts based on actual user needs
- Each blueprint includes: phase breakdown, key API lookup table, user interaction decision points, adaptation guide
- Auto-injected into prompt when trigger keywords match, guiding the LLM to generate structured `action_plan`

---

### Progressive RAG Expansion

When the initial RAG query returns unsatisfactory results, the system automatically expands search scope (up to 3 rounds):

```
Round 1: Original search terms, limit=8
    ↓ Quality score < 0.4?
Round 2: + Class-level fallback terms, limit=12
    ↓ Quality score < 0.4?
Round 3: + Full Chinese→API mappings, limit=15
    ↓
Filter noise (RadialArray, LinearArray...) → Cap at top 10
```

**RAG Quality Scoring** (`_score_rag_quality`):
- Method-level doc ratio (docs with syntax + parameters) × 0.3
- Search term coverage (how many terms appear in results) × 0.5
- Noise ratio (irrelevant docs like Array, Exception) × 0.2

**Context-Aware Search Term Extraction**:
- Detects query context vs creation context, dynamically adjusts API mapping priority
- "创建墙" (create wall) → keeps `Wall.Create` (true element creation)
- "创建视图着色" (create view coloring) → removes `Floor.Create` etc. (not element creation)
- Detection logic: checks if element nouns appear within 3 characters after the creation verb

---

### Multi-Layer Anti-Hallucination Defense

Strategies extracted from Claude Code source engineering practices, applied to all LLM interaction points:

1. **Rationalizations Recognition** — 5 common LLM excuse patterns identified and flagged in base prompt
2. **Faithful Reporting** — 5 forbidden behaviors (fabricating APIs, merging signatures, inventing slot values, etc.)
3. **RAG Grounding Rules** — LLM must only use documented APIs, match signatures exactly, report gaps honestly
4. **API Grounding Rules** — Code generator must verify method signatures, prefer version-compatible APIs
5. **Parameter Source Protocol** — Each tool parameter declares its data source (`query:*`, `interactive:*`, `ask_user`, `default`, `compute`)

---

### Atom Registry

Unified registry of 20 Revit query/interaction primitives (14 Query + 6 Interactive atoms) in `mcp_bridge/atom_registry.py`.

Each atom defines: key, label, return type, cacheability, and C# query code. The `AtomResolver` executes queries against Revit and returns `[{label, value}]` choice lists for tool parameter population.

---

### Tool Health Check & Auto-Degradation

- Tools now carry `applies_when`, `not_for`, `preconditions`, and `failure_count` metadata
- `health_check()` flags tools as unhealthy (2+ consecutive failures), stale (30+ days unused), or unverified (never executed)
- Unhealthy tools are excluded from `match_tool()`, system auto-falls back to RAG code generation

---

### End-to-End Testing

New test script `intent_bridge/tests/test_beam_clearance.py` — 7-step verification chain covering search term extraction through full orchestrator output.

Test input: "Find all structural beams in current view, calculate clearance between beam bottom and floors in linked model, write results to Comments parameter, create view coloring."

Result: 5-step composite action_plan correctly covering beam collection → link model selection → floor selection → parameter writing → view coloring.
