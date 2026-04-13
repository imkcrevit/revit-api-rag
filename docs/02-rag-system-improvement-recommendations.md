# RAG 检索系统改进建议

> 基于 Claude Code v2.1.88 源码中的工程实践，为你的 RAG 检索系统提出针对性改进建议。
> 聚焦于：Skills 系统、RAG Rerank、LLM 幻觉约束、默认输入值错误、与目标软件的交互性。

---

## 目录

1. [问题诊断框架](#1-问题诊断框架)
2. [Skills 系统改进](#2-skills-系统改进)
3. [RAG Rerank 改进](#3-rag-rerank-改进)
4. [LLM 幻觉约束](#4-llm-幻觉约束)
5. [默认输入值错误防护](#5-默认输入值错误防护)
6. [交互性增强](#6-交互性增强)
7. [综合工程建议](#7-综合工程建议)

---

## 1. 问题诊断框架

你描述的问题可以归纳为四个相互关联的失效模式：

```
用户查询 → [Skills 匹配] → [RAG 检索] → [Rerank] → [LLM 生成] → 回答
                ↓                ↓            ↓           ↓
          技能触发不准确     召回噪声大    排序不精确   幻觉/虚构
                                                         ↓
                                              默认值填充错误
```

以下建议均引用 Claude Code 源码中的具体实现作为参考范例。

---

## 2. Skills 系统改进

### 2.1 问题：技能匹配不精确

Claude Code 的 Skills 系统在技能加载和匹配上有三个值得借鉴的设计：

### 建议 A：条件激活机制（按上下文激活技能）

**参考源码**: `skills/loadSkillsDir.ts:997-1058`

Claude Code 不会一次性加载所有 Skills，而是根据用户操作的文件路径**按需激活**：

```typescript
// Claude Code 的条件技能激活
export function activateConditionalSkillsForPaths(
  filePaths: string[], cwd: string
): string[] {
  for (const [name, skill] of conditionalSkills) {
    const skillIgnore = ignore().add(skill.paths)
    for (const filePath of filePaths) {
      const relativePath = relative(cwd, filePath)
      if (skillIgnore.ignores(relativePath)) {
        dynamicSkills.set(name, skill)       // 激活到可用池
        conditionalSkills.delete(name)        // 从待激活池移除
        activatedConditionalSkillNames.add(name)
        break
      }
    }
  }
}
```

**你的 RAG 系统可以这样做**:

```python
class ConditionalSkillActivator:
    """按上下文条件激活 Skills，避免不相关 Skills 污染检索"""
    
    def __init__(self):
        self.dormant_skills = {}    # 待激活池
        self.active_skills = {}     # 已激活池
    
    def activate_for_context(self, user_query: str, metadata: dict) -> list:
        """基于查询元数据（领域、文件类型、操作类型）激活匹配的 Skills"""
        newly_activated = []
        for name, skill in list(self.dormant_skills.items()):
            if skill.matches_context(metadata):
                self.active_skills[name] = skill
                del self.dormant_skills[name]
                newly_activated.append(name)
        return newly_activated
    
    def get_available_skills(self) -> list:
        """仅返回已激活的 Skills 参与检索"""
        return list(self.active_skills.values())
```

### 建议 B：技能的层级优先级

**参考源码**: `tools/AgentTool/agentDisplay.ts:24-32`

Claude Code 的 Agent 来源有明确的优先级排序，同名 Agent 高优先级覆盖低优先级。你的 Skills 系统也应该建立类似的优先级链：

```python
SKILL_SOURCE_PRIORITY = [
    "user_custom",       # 用户自定义 Skills（最高优先级）
    "project_specific",  # 项目级 Skills
    "domain_expert",     # 领域专家 Skills
    "general_purpose",   # 通用 Skills（最低优先级）
]

def resolve_skill_conflicts(all_skills: list) -> list:
    """同名 Skill 按来源优先级去重"""
    active_map = {}
    for skill in sorted(all_skills, key=lambda s: SKILL_SOURCE_PRIORITY.index(s.source)):
        if skill.name not in active_map:
            active_map[skill.name] = skill
    return list(active_map.values())
```

### 建议 C：技能的 Token 预算感知

**参考源码**: `skills/loadSkillsDir.ts:100-105`

```typescript
// Claude Code 估算技能 frontmatter 的 token 占用
export function estimateSkillFrontmatterTokens(skill: Command): number {
  const frontmatterText = [skill.name, skill.description, skill.whenToUse]
    .filter(Boolean)
    .join(' ')
  return roughTokenCountEstimation(frontmatterText)
}
```

**建议**: 在 Skill 注入 prompt 前估算 token 占用，避免过多 Skills 挤占有效上下文窗口。

---

## 3. RAG Rerank 改进

### 3.1 Claude Code 的多层检索策略

Claude Code 不使用单一的 rerank 模型，而是组合多种策略：

| 策略 | 源文件 | 方法 |
|------|--------|------|
| LLM-as-Judge | `memdir/findRelevantMemories.ts` | Sonnet 模型选择 top-5 记忆 |
| 关键词评分 | `tools/ToolSearchTool/ToolSearchTool.ts` | 分词 + 词边界正则 + 加权 |
| 模糊搜索 | `hooks/unifiedSuggestions.ts` | Fuse.js 模糊匹配 |
| 原生索引 | `native-ts/file-index/` | Rust 文件索引 |

### 建议 A：LLM-as-Judge 相关性过滤

**参考源码**: `memdir/findRelevantMemories.ts:18-24`

Claude Code 使用一个独立的 LLM 调用来筛选相关记忆，而非简单的向量相似度。关键设计：

```typescript
const SELECT_MEMORIES_SYSTEM_PROMPT = `You are selecting memories that will be useful...
Return a list of filenames for the memories that will clearly be useful (up to 5).
- If you are unsure if a memory will be useful, then do not include it. Be selective.
- If there are no memories that would clearly be useful, return an empty list.
- If recently-used tools are provided, do not select usage reference docs for those tools.
  DO still select warnings, gotchas, or known issues about those tools.`
```

**你的 RAG 系统可以这样做**:

```python
RERANK_SYSTEM_PROMPT = """You are a relevance judge for a RAG retrieval system.
Given the user's query and a list of retrieved documents with their summaries,
select the documents that will CLEARLY be useful for answering the query (up to {max_results}).

CRITICAL RULES:
- If you are unsure if a document is relevant, do NOT include it. Be selective.
- If no documents are clearly relevant, return an empty list.
- Do NOT include documents that merely share keywords but answer a different question.
- Prefer documents that directly address the user's intent over tangentially related ones.
- If the user has already accessed certain information (listed as "already_surfaced"),
  prioritize NEW information over re-surfacing the same content.
"""

async def llm_rerank(query: str, candidates: list, already_surfaced: set) -> list:
    """使用 LLM 对候选文档进行相关性判断"""
    # 过滤已展示的文档（节省 judge 的 slot 预算）
    filtered = [c for c in candidates if c.id not in already_surfaced]
    
    manifest = format_document_manifest(filtered)  # 格式化为摘要清单
    
    result = await llm_call(
        model="haiku",  # 使用快速模型降低延迟
        system=RERANK_SYSTEM_PROMPT.format(max_results=5),
        messages=[{"role": "user", "content": f"Query: {query}\n\nDocuments:\n{manifest}"}],
        output_format={"type": "json_schema", "schema": {
            "type": "object",
            "properties": {"selected_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["selected_ids"],
            "additionalProperties": False,
        }},
    )
    
    # 二次验证：仅保留确实存在于候选集中的 ID
    valid_ids = {c.id for c in filtered}
    return [id for id in result["selected_ids"] if id in valid_ids]
```

**核心要点**:

1. **JSON Schema 约束输出** — 避免 LLM 自由发挥导致解析失败
2. **已展示过滤** (`already_surfaced`) — 避免重复推荐
3. **空列表允许** — 没有相关结果时宁可不返回
4. **二次验证** — 过滤 LLM 可能生成的无效 ID

### 建议 B：多层评分策略

**参考源码**: `tools/ToolSearchTool/ToolSearchTool.ts:186-250`

Claude Code 的工具搜索使用分层策略：

```typescript
// 层1：精确匹配 → 立即返回
const exactMatch = deferredTools.find(t => t.name.toLowerCase() === queryLower)
if (exactMatch) return [exactMatch.name]

// 层2：前缀匹配（结构化名称）
if (queryLower.startsWith('mcp__')) {
  const prefixMatches = deferredTools.filter(t => t.name.toLowerCase().startsWith(queryLower))
  if (prefixMatches.length > 0) return prefixMatches.map(t => t.name)
}

// 层3：关键词评分（分词 + 加权）
const queryTerms = queryLower.split(/\s+/)
// 区分必选项（+term）和可选项
const requiredTerms = terms.filter(t => t.startsWith('+'))
const optionalTerms = terms.filter(t => !t.startsWith('+'))
```

**你的 RAG 系统可以这样做**:

```python
class MultiLayerReranker:
    """多层 Rerank 策略"""
    
    def rerank(self, query: str, candidates: list) -> list:
        # 层1：精确匹配（标题/ID 完全匹配）
        exact = [c for c in candidates if c.title.lower() == query.lower()]
        if exact:
            return exact
        
        # 层2：结构化前缀匹配（如领域::子领域::主题）
        prefix = [c for c in candidates if c.path.startswith(query_prefix)]
        if prefix:
            return sorted(prefix, key=lambda c: c.specificity, reverse=True)
        
        # 层3：关键词评分 + 向量相似度混合
        scored = []
        for c in candidates:
            keyword_score = self._keyword_score(query, c)
            vector_score = c.similarity_score
            # 混合评分：关键词匹配权重更高（防止语义漂移）
            combined = 0.6 * keyword_score + 0.4 * vector_score
            scored.append((c, combined))
        
        # 层4：LLM-as-Judge（仅对 top-N 执行，控制成本）
        top_candidates = sorted(scored, key=lambda x: x[1], reverse=True)[:10]
        return self._llm_judge(query, [c for c, _ in top_candidates])
```

### 建议 C：预编译搜索模式

**参考源码**: `tools/ToolSearchTool/ToolSearchTool.ts:167-175`

```typescript
// 预编译词边界正则（每次搜索只编译一次，而非 tools×terms×2 次）
function compileTermPatterns(terms: string[]): Map<string, RegExp> {
  const patterns = new Map<string, RegExp>()
  for (const term of terms) {
    if (!patterns.has(term)) {
      patterns.set(term, new RegExp(`\\b${escapeRegExp(term)}\\b`))
    }
  }
  return patterns
}
```

**建议**: 在 rerank 阶段预编译查询词的匹配模式，避免在每个候选文档上重复编译。

---

## 4. LLM 幻觉约束

### 4.1 问题分析

在 RAG 系统中，幻觉通常表现为：
- 检索到的文档不包含答案，但 LLM 仍然"编造"了一个
- LLM 混淆了多个检索结果的内容
- LLM 错误引用了文档中不存在的信息

### 建议 A：陈旧性标注（Freshness Warning）

**参考源码**: `memdir/memoryAge.ts:33-42`

Claude Code 对每条记忆都添加时间维度的提醒：

```typescript
export function memoryFreshnessText(mtimeMs: number): string {
  const d = memoryAgeDays(mtimeMs)
  if (d <= 1) return ''
  return (
    `This memory is ${d} days old. ` +
    `Memories are point-in-time observations, not live state — ` +
    `claims about code behavior or file:line citations may be outdated. ` +
    `Verify against current code before asserting as fact.`
  )
}
```

**你的 RAG 系统可以这样做**:

```python
def add_freshness_warning(doc: Document) -> str:
    """为检索到的文档添加新鲜度警告"""
    age_days = (datetime.now() - doc.updated_at).days
    
    if age_days <= 7:
        return ""  # 近期文档不加警告
    
    if age_days <= 30:
        return f"[Note: This document was last updated {age_days} days ago. Verify key claims.]"
    
    return (
        f"[WARNING: This document is {age_days} days old. "
        f"Information may be outdated. Do NOT present claims from this document "
        f"as current facts without explicit qualification.]"
    )
```

### 建议 B：召回验证提示词

**参考源码**: `memdir/memoryTypes.ts:240-256`

```
## Before recommending from memory

A memory that names a specific function, file, or flag is a claim that 
it existed *when the memory was written*. Before recommending it:

- If the memory names a file path: check the file exists.
- If the memory names a function or flag: grep for it.

"The memory says X exists" is not the same as "X exists now."
```

**你的 RAG 系统提示词应包含**:

```python
RAG_GROUNDING_PROMPT = """
## Answering from retrieved documents

CRITICAL RULES for using retrieved context:

1. ONLY answer based on information explicitly present in the retrieved documents.
   If the documents don't contain the answer, say "I don't have information about this 
   in the available documents" — do NOT guess or infer.

2. When citing information, always include the document source:
   - "According to [Document Title] (updated: YYYY-MM-DD)..."
   - "Based on the information in [Source]..."

3. If multiple documents contain conflicting information, present BOTH perspectives 
   and note the conflict rather than picking one.

4. If a retrieved document is marked as outdated (>30 days old), explicitly qualify:
   "As of [date], [claim]. This information may have changed since then."

5. NEVER:
   - Merge information from different documents into a claim that neither makes alone
   - Present your general knowledge as if it came from a retrieved document
   - Fill in gaps with assumptions ("probably", "likely", "usually")
   - Paraphrase a document in a way that changes its meaning
"""
```

### 建议 C：对抗性验证 Agent

**参考源码**: `tools/AgentTool/built-in/verificationAgent.ts:10-12`

```
Your job is not to confirm the implementation works — it's to try to break it.

You have two documented failure patterns:
1. Verification avoidance: you find reasons not to run checks
2. Being seduced by the first 80%
```

**在 RAG 系统中的应用** — 添加一个验证步骤：

```python
VERIFICATION_PROMPT = """
You are a fact-checker for RAG responses. Given:
- The original user query
- The retrieved documents
- The generated answer

Your job is to verify EVERY factual claim in the answer against the retrieved documents.

For each claim:
1. Identify the specific document and passage that supports it
2. Check if the claim accurately represents the source
3. Flag any claim that:
   - Is NOT supported by any retrieved document (UNSUPPORTED)
   - Misrepresents the source document (DISTORTED)
   - Combines information from multiple sources in misleading ways (CONFLATED)
   - Uses qualifiers not present in the source ("always", "never", "all") (OVERGENERALIZED)

Output format:
- VERIFIED: [list of claims with source citations]
- FLAGGED: [list of problematic claims with issue type and explanation]
- VERDICT: PASS / NEEDS_REVISION
"""
```

### 建议 D：忠实性报告指令

**参考源码**: `constants/prompts.ts:239-241`

Claude Code 针对 Capybara v8 的 29-30% 假声明率专门添加了：

```typescript
`Report outcomes faithfully: if tests fail, say so with the relevant output; 
 if you did not run a verification step, say that rather than implying it succeeded. 
 Never claim "all tests pass" when output shows failures...`
```

**在你的系统中**:

```python
FAITHFULNESS_INSTRUCTION = """
When answering from retrieved documents:
- If you found relevant information: cite the source precisely.
- If you found partial information: say what you found AND what's missing.
- If you found NO relevant information: say so. Do NOT generate an answer.
- If the retrieved documents are contradictory: present both sides.
- NEVER say "the documents confirm..." when they don't explicitly state the claim.
"""
```

---

## 5. 默认输入值错误防护

### 5.1 问题分析

当用户输入不完整或缺失参数时，LLM 倾向于用"合理"的默认值填充，这在 RAG 系统中表现为：
- 查询参数缺失 → LLM 默认填一个看似合理但错误的值
- 过滤条件缺失 → LLM 假设了一个范围
- 选项未指定 → LLM 选了一个"通常"的值

### 建议 A：AskUserQuestion 模式

**参考源码**: `tools/AskUserQuestionTool/prompt.ts:32-44`

```typescript
export const ASK_USER_QUESTION_TOOL_PROMPT = `Use this tool when you need to:
1. Gather user preferences or requirements
2. Clarify ambiguous instructions
3. Get decisions on implementation choices as you work
4. Offer choices to the user about what direction to take.

Usage notes:
- Users will always be able to select "Other" to provide custom text input
- If you recommend a specific option, make that the first option and add "(Recommended)"`
```

**你的 RAG 系统可以这样做**:

```python
class QueryClarifier:
    """当查询参数缺失或模糊时，主动向用户澄清"""
    
    REQUIRED_FIELDS = {
        "date_range": "时间范围（如：最近7天、2024年Q1）",
        "product_name": "具体产品名称",
        "version": "版本号",
    }
    
    def check_query_completeness(self, query: str, detected_intent: str) -> dict:
        """检查查询是否缺少必要参数"""
        missing = {}
        for field, description in self.REQUIRED_FIELDS.items():
            if not self._field_present_in_query(query, field):
                missing[field] = description
        return missing
    
    def generate_clarification(self, missing_fields: dict) -> dict:
        """生成澄清问题（带推荐选项）"""
        return {
            "type": "clarification_needed",
            "questions": [
                {
                    "field": field,
                    "prompt": f"请指定{desc}：",
                    "options": self._get_options_for_field(field),
                    "allow_custom": True,  # 用户始终可以输入自定义值
                }
                for field, desc in missing_fields.items()
            ]
        }
```

### 建议 B：参数替换验证

**参考源码**: `skills/loadSkillsDir.ts:344-354`

Claude Code 的 Skills 系统使用参数替换而非默认值：

```typescript
async getPromptForCommand(args, toolUseContext) {
  let finalContent = baseDir
    ? `Base directory for this skill: ${baseDir}\n\n${markdownContent}`
    : markdownContent

  // 使用用户提供的参数替换占位符，而非填默认值
  finalContent = substituteArguments(
    finalContent,
    args,
    true,           // strict 模式：未提供的参数不替换
    argumentNames,
  )
}
```

**建议**: 在查询模板中使用**显式占位符 + 严格替换**，未提供的参数保持占位符状态触发澄清流程：

```python
def substitute_query_params(template: str, params: dict, strict: bool = True) -> tuple:
    """替换查询模板参数。strict 模式下未提供的参数会被标记为缺失"""
    missing = []
    for placeholder in extract_placeholders(template):
        if placeholder in params:
            template = template.replace(f"${{{placeholder}}}", str(params[placeholder]))
        elif strict:
            missing.append(placeholder)
        # 不填默认值！
    
    return template, missing
```

### 建议 C：系统提示词中的反默认值指令

**参考源码**: `constants/prompts.ts:232-233`

```typescript
`If an approach fails, diagnose why before switching tactics — read the error, 
check your assumptions, try a focused fix. Don't retry the identical action blindly, 
but don't abandon a viable approach after a single failure either. 
Escalate to the user with AskUserQuestion only when you're genuinely stuck 
after investigation, not as a first response to friction.`
```

**你的系统提示词应包含**:

```python
NO_DEFAULT_VALUES_INSTRUCTION = """
## Handling missing or ambiguous parameters

CRITICAL: Do NOT fill in missing parameters with assumed default values.

When the user's query is missing required information:
1. NEVER assume a date range (e.g., don't default to "last 30 days")
2. NEVER assume a specific product/version (e.g., don't default to "latest")
3. NEVER assume a geographic region (e.g., don't default to "US")
4. NEVER assume a user role or permission level

Instead:
- If 1 parameter is missing: ask a focused question with suggested options
- If 2+ parameters are missing: present a structured form
- If the parameter has a commonly-expected value: suggest it as "(Recommended)" 
  but STILL ask for confirmation

Exception: When the user's phrasing clearly implies a value 
(e.g., "recent sales" implies a short time range), 
you may suggest that interpretation but must state your assumption explicitly.
"""
```

---

## 6. 交互性增强

### 6.1 风险操作确认框架

**参考源码**: `constants/prompts.ts:255-267`

Claude Code 的"三思而行"框架：

```
Carefully consider the reversibility and blast radius of actions.
A user approving an action once does NOT mean they approve it in all contexts.
Authorization stands for the scope specified, not beyond.
Match the scope of your actions to what was actually requested.
```

**在 RAG 系统中的应用**:

```python
class ActionConfirmation:
    """对高风险操作要求用户确认"""
    
    RISK_LEVELS = {
        "read_only": "auto",      # 只读查询自动执行
        "filtered_result": "auto", # 筛选结果自动展示
        "data_modification": "confirm",   # 修改数据需确认
        "external_api_call": "confirm",   # 调用外部API需确认
        "bulk_operation": "confirm",      # 批量操作需确认
    }
    
    def assess_risk(self, action: str) -> str:
        for pattern, level in self.RISK_LEVELS.items():
            if pattern in action:
                return level
        return "confirm"  # 默认需确认
    
    def format_confirmation(self, action: str, details: dict) -> dict:
        """格式化确认请求"""
        return {
            "type": "confirmation_required",
            "action": action,
            "details": details,
            "options": [
                {"id": "proceed", "label": "执行 (Proceed)"},
                {"id": "modify", "label": "修改参数 (Modify)"},
                {"id": "cancel", "label": "取消 (Cancel)"},
            ],
            "warning": self._generate_warning(action, details),
        }
```

### 6.2 渐进式信息展示

**参考源码**: `constants/prompts.ts:403-428`

Claude Code 的输出效率控制区分了两种模式。RAG 系统也应该：

```python
class ProgressiveDisclosure:
    """渐进式展示检索结果"""
    
    def format_response(self, query: str, results: list, detail_level: str = "summary"):
        if detail_level == "summary":
            return {
                "summary": self._generate_summary(results),
                "result_count": len(results),
                "top_3": [self._brief_entry(r) for r in results[:3]],
                "actions": [
                    "查看更多详情 (Show details)",
                    "导出完整结果 (Export all)",
                    "缩小范围 (Narrow down)",
                ],
            }
        elif detail_level == "detailed":
            return {
                "results": [self._full_entry(r) for r in results],
                "metadata": self._result_metadata(results),
            }
```

### 6.3 Prompt Suggestion（提示建议）

**参考源码**: `services/PromptSuggestion/promptSuggestion.ts`

Claude Code 在用户输入为空时主动提供建议。你的 RAG 系统可以：

```python
class QuerySuggester:
    """基于上下文提供查询建议"""
    
    def suggest_next_query(self, conversation_history: list, last_results: list) -> list:
        """分析对话历史和最近结果，建议后续查询"""
        suggestions = []
        
        # 基于最近结果的深入探索
        if last_results:
            topics = extract_topics(last_results)
            suggestions.extend([
                f"关于 {topic} 的更多详情" for topic in topics[:3]
            ])
        
        # 基于对话历史的相关查询
        if conversation_history:
            related = self._find_related_queries(conversation_history)
            suggestions.extend(related[:3])
        
        return suggestions
```

### 6.4 结构化反馈收集

**参考源码**: `memdir/memoryTypes.ts:60-73`

Claude Code 的 feedback 记忆类型同时记录**纠正**和**确认**：

```
<when_to_save>
Any time the user corrects your approach ("no not that", "don't", "stop doing X") 
OR confirms a non-obvious approach worked ("yes exactly", "perfect, keep doing that").
Corrections are easy to notice; confirmations are quieter — watch for them.
Include *why* so you can judge edge cases later.
</when_to_save>
```

**在 RAG 系统中**:

```python
class FeedbackCollector:
    """收集用户对检索结果的反馈"""
    
    def record_feedback(self, query: str, result_id: str, feedback_type: str, details: str):
        """记录反馈，包括原因"""
        entry = {
            "query": query,
            "result_id": result_id,
            "type": feedback_type,  # "correct", "wrong", "outdated", "irrelevant"
            "details": details,
            "why": self._extract_why(details),  # 提取原因
            "how_to_apply": self._extract_how(details),  # 提取应用方式
            "timestamp": datetime.now(),
        }
        self.feedback_store.append(entry)
        
        # 反馈也可以影响 rerank 权重
        if feedback_type == "wrong":
            self.reranker.add_negative_signal(query, result_id)
        elif feedback_type == "correct":
            self.reranker.add_positive_signal(query, result_id)
```

---

## 7. 综合工程建议

### 7.1 系统提示词的缓存架构

**参考源码**: `constants/systemPromptSections.ts`

将系统提示词分为静态和动态两部分，避免不必要的 prompt cache 失效：

```python
class PromptCacheManager:
    """提示词缓存管理，区分静态和动态部分"""
    
    def __init__(self):
        self._static_cache = {}   # 跨session可缓存
        self._dynamic_cache = {}  # 每轮重算
    
    def build_system_prompt(self, context: dict) -> str:
        # 静态部分（缓存）
        static = self._get_or_compute_static("base_instructions", self._base_prompt)
        static += self._get_or_compute_static("rag_rules", self._rag_rules)
        
        # 动态边界标记
        prompt = static + "\n__DYNAMIC_BOUNDARY__\n"
        
        # 动态部分（每轮重算）
        prompt += self._compute_dynamic("session_context", context)
        prompt += self._compute_dynamic("active_skills", context.get("skills", []))
        
        return prompt
```

### 7.2 失败重试策略

**参考源码**: `constants/prompts.ts:232-233`

```
If an approach fails, diagnose why before switching tactics — 
read the error, check your assumptions, try a focused fix. 
Don't retry the identical action blindly, 
but don't abandon a viable approach after a single failure either.
```

```python
class RetryStrategy:
    """智能重试 — 诊断后调整，而非盲目重试"""
    
    async def execute_with_retry(self, query: str, max_retries: int = 3):
        for attempt in range(max_retries):
            result = await self.execute(query)
            
            if result.success:
                return result
            
            # 诊断失败原因
            diagnosis = self.diagnose_failure(result.error, query)
            
            if diagnosis.type == "no_results":
                # 放宽查询条件
                query = self.broaden_query(query)
            elif diagnosis.type == "too_many_results":
                # 缩小查询范围
                query = self.narrow_query(query)
            elif diagnosis.type == "irrelevant_results":
                # 调整检索策略（而非重试相同查询）
                self.switch_retrieval_strategy()
            else:
                # 无法诊断 → 上报用户
                return self.escalate_to_user(query, diagnosis)
        
        return self.escalate_to_user(query, "Max retries reached")
```

### 7.3 检索结果的记录与审计

**参考源码**: `tools/AgentTool/runAgent.ts:735-742`

```typescript
// 记录初始消息和元数据（fire-and-forget，不阻塞主流程）
void recordSidechainTranscript(initialMessages, agentId).catch(_err =>
  logForDebugging(`Failed to record sidechain transcript: ${_err}`)
)
void writeAgentMetadata(agentId, {
  agentType: agentDefinition.agentType,
  ...(worktreePath && { worktreePath }),
  ...(description && { description }),
}).catch(_err => logForDebugging(`Failed to write agent metadata: ${_err}`))
```

**建议**: 对每次检索记录完整链路（fire-and-forget，不影响响应延迟）：

```python
async def record_retrieval_chain(query, retrieved_docs, reranked_docs, 
                                  generated_answer, user_feedback=None):
    """异步记录检索全链路，用于后续分析和改进"""
    asyncio.create_task(
        audit_store.write({
            "timestamp": datetime.now(),
            "query": query,
            "retrieved_count": len(retrieved_docs),
            "reranked_count": len(reranked_docs),
            "retrieval_scores": [d.score for d in retrieved_docs],
            "rerank_scores": [d.score for d in reranked_docs],
            "answer_sources": extract_cited_sources(generated_answer),
            "feedback": user_feedback,
        })
    )
```

---

## 总结：改进优先级矩阵

| 改进项 | 影响范围 | 实现难度 | 推荐优先级 |
|--------|---------|---------|-----------|
| RAG Grounding 提示词 | 直接减少幻觉 | 低 | **P0** |
| 反默认值指令 | 直接解决默认值错误 | 低 | **P0** |
| AskUserQuestion 澄清机制 | 提升交互性 + 减少错误 | 中 | **P0** |
| LLM-as-Judge Rerank | 提升检索精度 | 中 | **P1** |
| 陈旧性标注 | 减少过时信息幻觉 | 低 | **P1** |
| 条件 Skill 激活 | 减少噪声 | 中 | **P1** |
| 对抗性验证步骤 | 深度反幻觉 | 高 | **P2** |
| 提示词缓存架构 | 性能优化 | 高 | **P2** |
| 反馈收集系统 | 长期改进 | 中 | **P2** |
| 检索链路审计 | 可观测性 | 低 | **P2** |

建议的实施顺序：先加 P0 提示词层面的改进（成本最低、见效最快），然后建设 P1 的检索质量改进，最后部署 P2 的系统级架构改进。
