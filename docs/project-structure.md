# Project Structure

This repository is organized by runtime responsibility. Avoid moving source directories casually because imports, Docker copy rules, and deployment paths depend on these names.

## Current Ownership Map

| Category | Directory | Role |
|---|---|---|
| Agent runtime | `intent_bridge/` | Intent analysis, slot/question orchestration, skill matching, action plans |
| Revit execution bridge | `mcp_bridge/` | RAG code generation, TCP Revit client, sandbox, tools, dynamic choices |
| Backend | `server/` | FastAPI app, API routes, sessions, skill store, React SPA mounting |
| Frontend | `frontend/` | Standalone Vite React workbench; can run separately from backend |
| Training pipeline | `pipeline/` | API/SDK parsing, quality agents, embeddings, retriever, reranker |
| Prompt templates | `prompts/` | Centralized prompt files loaded by runtime and training code |
| Training/configurable data | `data/` | Local skills, SQLite/ChromaDB data, generated training outputs |
| Designer prompt layer | `prompt_bridge/` | Designer-language prompt refinement knowledge and route |
| Text tools | `text_studio/` | Translation and text polishing service |
| Revit plugin | `revit_plugin/` | C# Revit 2026 TCP JSON-RPC plugin and command set |
| Legacy project | `legacy/` | Archived V1 AutoDL-era implementation and historical data |
| Documentation | `docs/` | Architecture, usage, deployment, style, prompt catalog, structure notes |

## Boundaries

- Keep backend APIs in `server/`, `mcp_bridge/`, and `intent_bridge/`.
- Keep the React frontend independent in `frontend/`; use `VITE_API_BASE_URL` for split deployment instead of changing backend routes.
- Treat React as the primary UI. Gradio is legacy comparison code and is not mounted unless `ENABLE_GRADIO=1` or `server.enable_gradio=true`.
- Keep prompt behavior in `prompts/`; code should load templates instead of embedding long prompt strings.
- Keep generated data out of source control. SQLite, ChromaDB, build output, and plugin binaries stay ignored or shipped through releases.
- Keep `legacy/` read-only unless a task explicitly targets historical behavior.

## Prompt Organization

Prompt filenames use `<domain>.<purpose>.md`:

- `mcp_bridge.*` - executable Revit code generation and bridge classification.
- `intent_bridge.*` - parameter analysis and action planning.
- `server.*` - legacy RAG chat, API Explorer, and Text2Revit prompts.
- `pipeline.*` - training and data-quality prompts.
- `prompt_bridge.*` - designer prompt refinement.
- `text_studio.*` - translation and text rewriting.

Prompt text is version-controlled source. New or changed prompt behavior should
be added to `prompts/*.md`, loaded through `prompts/loader.py`, and documented in
`docs/prompt-catalog.md`. Runtime modules should not carry long inline prompt
strings.

## Runtime Route Locations

FastAPI route ownership is split by feature. When modifying an endpoint, update
the route file listed here and mention it in the commit message body.

| URL area | Route file | Notes |
|---|---|---|
| App mounting | `server/main.py` | Creates the FastAPI app, mounts routers, serves React assets, optionally mounts legacy Gradio |
| Core API | `server/app/api/routes.py` | `/api/chat`, `/api/t2r/chat`, `/api/search`, `/api/config`, `/api/settings` |
| Logs | `server/app/api/log_routes.py` | `/api/logs/*` |
| Skills | `server/app/api/skill_routes.py` | `/api/skills/*` |
| Intent Bridge | `intent_bridge/router.py` | `/api/v1/intent/*` |
| MCP Bridge | `mcp_bridge/router.py` | `/api/v1/bridge/*`, Revit execution, orchestrator queue/rollback, dynamic choices, WebSocket slots |
| PromptBridge | `prompt_bridge/router.py` | `/api/prompt-bridge/*` |
| TextStudio | `text_studio/router.py` | `/api/text-studio/*` |

Prompt route/storage changes in this revision:

| Runtime module | Prompt storage |
|---|---|
| MCP Bridge code generation and retry | `prompts/mcp_bridge.*.md` |
| MCP Bridge intent classification | `prompts/mcp_bridge.classify_intent.md` |
| Intent Bridge orchestration | `prompts/intent_bridge.analyze.md`, `prompts/intent_bridge.analyze_legacy.md` |
| Server RAG/API Explorer/Text2Revit | `prompts/server.*.md` |
| PromptBridge | `prompts/prompt_bridge.system.md` |
| TextStudio | `prompts/text_studio.system.md` |
| Pipeline query rewrite and quality agents | `prompts/pipeline.*.md` |

## Frontend/Backend Separation

Default development still works through Vite proxy:

```bash
cd frontend
npm run dev
```

For a separately hosted frontend, set:

```bash
VITE_API_BASE_URL=https://your-backend.example.com
```

No backend route changes are required for this split.
