# Intent Bridge Agent

> **Warning** 当前为测试版 (Demo)。Demo 站点连接开发者本机 Revit 端口 (18080)。
> 企业多人使用场景可通过以下方式部署：
> - 每位用户本地运行 Revit Plugin，配置各自的端口
> - 统一服务端 + SSH Tunnel 到各用户机器
> - Docker 分发 + 环境变量配置不同 Revit 实例

## 架构图

```
┌─────────────────────────────────────────────────────┐
│                   User Input                         │
│        "创建两面墙，高3米" / "create 2 walls"         │
└───────────────┬─────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────────┐
│     1. Language Detection         │
│     _detect_language()            │
└───────────────┬───────────────────┘
                │
                ▼
┌───────────────────────────────────┐
│     2. Dynamic RAG Query          │
│     _extract_search_terms()       │──→ SQLite: revit_api.db
│     _query_api_by_method()        │    (API signatures + params)
└───────────────┬───────────────────┘
                │
                ▼
┌───────────────────────────────────┐
│     3. LLM Agent Analysis         │
│     (ONE call, with RAG context)  │──→ Gemini / OpenAI / DeepSeek
│                                   │    via OpenRouter
│     - Intent classification       │
│     - Parameter extraction        │
│     - Question generation         │
│     - Quantity/array detection     │
└───────────────┬───────────────────┘
                │
                ▼
┌───────────────────────────────────┐
│     4. Question Queue             │
│     (No LLM, instant responses)   │◄──→ User answers
│     Fill slots one by one         │
└───────────────┬───────────────────┘
                │ (all answered)
                ▼
┌───────────────────────────────────┐
│     5. Execution Matching         │
│     Intent params → Tool/Command  │──→ Solidified Tool match
│     Parameter format translation   │    OR code generation
└───────────────┬───────────────────┘
                │
                ▼
┌───────────────────────────────────┐
│     6. Revit Execution            │
│     TCP:18080 → Plugin            │──→ Revit Plugin (C#)
└───────────────────────────────────┘
```

## 模型配置

通过 `intent_bridge/config.yaml` 或 `config/config.yaml` 配置 LLM：

| Provider | Model | 适用场景 |
|----------|-------|---------|
| Gemini | google/gemini-2.5-flash | 默认主模型（快速、便宜） |
| OpenAI | openai/gpt-4o | 备选（高质量推理） |
| DeepSeek | deepseek/deepseek-chat | 备选（中文优化） |
| Claude | anthropic/claude-sonnet-4 | 备选（代码生成质量高） |

所有模型通过 OpenRouter API 统一调用。配置 `OPENROUTER_API_KEY` 环境变量即可。

## API Endpoints

- `POST /api/v1/intent/session` — 创建会话
- `POST /api/v1/intent/session/{id}/turn` — 用户输入（触发 LLM）
- `POST /api/v1/intent/session/{id}/answer` — 回答问题（无 LLM，即时）
- `GET /api/v1/intent/session/{id}` — 查询会话状态
- `GET /api/v1/intent/schemas` — 列出支持的意图
- `GET /api/v1/intent/execution-map` — 返回 intent → command/tool 映射

## 多人部署方案

1. **单机 Demo**: 连接本机 Revit (localhost:18080)
2. **局域网**: 各用户运行 Plugin，服务端配置 target host
3. **远程**: SSH Tunnel + Docker 容器化服务端
