# Revit API RAG - Agent Notes

## Project Overview

This repository is a Revit 2026 API RAG system for generating, reviewing, and executing C# snippets against a live Revit model.

Main layers:
- `pipeline/`: data processing, Revit API/SDK parsing, embeddings, retrieval, rerank, and LLM clients.
- `server/`: FastAPI entry point. `python -m server.main` serves the React SPA at `/`, API routes, and `/health` on port `7860` by default. Gradio is legacy-only and disabled unless explicitly enabled.
- `frontend/`: Vite + React + TypeScript UI. Built assets are served from `frontend/dist/`.
- `mcp_bridge/`: code generation, Revit TCP client, sandbox review, tool store, dynamic choices, and bridge API routes.
- `intent_bridge/`: intent parsing, slot/question flow, execution mapping, and anti-hallucination parameter handling.
- `prompts/`: version-controlled prompt templates loaded by runtime and pipeline code through `prompts.loader.load_prompt()`.
- `prompt_bridge/`: designer-language prompt refinement layer.
- `text_studio/`: multilingual text polishing and translation routes.
- `revit_plugin/`: Revit 2026 C#/.NET plugin forked from `mcp-servers-for-revit`; exposes TCP JSON-RPC on port `18080`.
- `legacy/`: historical V1 implementation and notes. Treat it as reference unless the task explicitly targets it.
- `docs/`: architecture, usage, frontend style, deployment, prompt catalog, and tool-choice flow documentation.

## Key References

Read these first when changing related behavior:
- `README.md` / `README.en.md`: project architecture, releases, quick start, changelog.
- `docs/architecture.md`: end-to-end system architecture, retrieval pipeline, component map, dependencies.
- `docs/usage-guide.md`: Intent Bridge user flows, execution modes, thinking stream, solidified tools.
- `docs/dev-guide-v0.2.md`: Revit plugin protocol, code execution constraints, MCP bridge development modules.
- `docs/tool-choices-flow.md`: runtime choice loading rules for levels, family types, floor types, and elements.
- `docs/prompt-catalog.md`: prompt and anti-hallucination conventions across modules.
- `docs/project-structure.md`: ownership map, prompt storage, and runtime route locations.
- `docs/agent-workflow-hardening.md`: summary of the current workflow hardening changes.
- `docs/frontend-style-guide.md`: Graptolite BIM/AI frontend style tokens and UI rules.
- `revit_plugin/README.md`: build/deploy/use instructions for the Revit TCP plugin.
- `intent_bridge/README.md`: Intent Bridge flow, model config, endpoints, and deployment notes.
- `prompt_bridge/README.md`: designer prompt-refinement purpose and structure.

## Setup And Run

Server dependencies:

```bash
pip install -r requirements-server.txt
```

Run the combined server:

```bash
python -m server.main
```

Important runtime URLs:
- `http://localhost:7860/` - React SPA, if `frontend/dist` exists.
- `http://localhost:7860/app` - legacy Gradio only when `ENABLE_GRADIO=1`.
- `http://localhost:7860/health` - health check.
- `http://localhost:7860/docs` - FastAPI Swagger docs.

Frontend development:

```bash
cd frontend
npm install
npm run dev
npm run build
npm run lint
```

Pipeline dependencies are separate and intended for Colab/data processing:

```bash
pip install -r requirements-pipeline.txt
```

Docker build uses a React build stage and Python server stage:

```bash
docker build -t revit-api-rag .
```

## Environment And Data

Required or common environment variables:
- `OPENROUTER_API_KEY`: required for OpenRouter LLM and embedding calls.
- `COHERE_API_KEY`: optional; if absent, rerank can be skipped.
- `DATA_DIR`: optional override for data location, defaults to project `data/` or `/app/data` in Docker.

Expected data layout:
- `data/sqlite/revit_api.db`
- `data/sqlite/revit_sdk.db`
- `data/chromadb/`

Large generated data and local secrets are ignored by `.gitignore`; do not commit API keys, local `.env` files, generated ChromaDB directories, SQLite DBs, frontend `dist`, `node_modules`, or Revit plugin build outputs.

## Revit Bridge Rules

The Revit plugin listens on TCP `localhost:18080` using JSON-RPC 2.0. Revit 2026 must be open, the plugin loaded, and the Ribbon switch enabled before execution features can connect.

Dynamic C# execution constraints:
- The plugin wraps execution in its own Transaction. Generated code must not create another Transaction.
- Use the provided variable named `document`; do not assume `doc` or `uidoc` exists.
- Keep generated code grounded in retrieved Revit API documentation and SDK examples. Do not invent classes, methods, enum names, or signatures.
- All missing or ambiguous user parameters should be asked for or sourced from runtime Revit queries. Do not silently default model-specific values.
- Revit internal units are feet; UI/user-facing dimensions are commonly `mm`, `m`, or `feet` and need explicit conversion.

## Dynamic Tool Choices

For parameters with multiple model-derived choices, query Revit at runtime and present a list instead of hard-coding `.First()` or free-form text.

Supported `choices_from` patterns in tool YAML:
- `levels`
- `family_types:OST_XXX`
- `floor_types`
- `elements:OST_XXX`

Relevant files:
- `mcp_bridge/tools/*.yaml`
- `mcp_bridge/tool_store.py`
- `mcp_bridge/router.py`
- `mcp_bridge/interactive.py`
- `mcp_bridge/frontend/app.py`

## Prompt And Route Ownership

Prompt text is product behavior and must stay version-controlled in `prompts/`.
Do not embed long prompt strings back into service modules. Runtime and pipeline
code should import `load_prompt()` from `prompts`.

Prompt storage:
- `prompts/*.md`: editable prompt templates.
- `prompts/loader.py`: cached prompt loader.
- `prompts/README.md`: prompt catalog and version-control policy.

Runtime route locations:
- `server/main.py`: FastAPI app creation and router mounting.
- `server/app/api/routes.py`: core `/api/*` chat/search/settings routes.
- `server/app/api/skill_routes.py`: `/api/skills/*` skill management routes.
- `server/app/api/log_routes.py`: `/api/logs/*` logging routes.
- `mcp_bridge/router.py`: `/api/v1/bridge/*`, Revit execution, orchestration, WebSocket slots.
- `intent_bridge/router.py`: `/api/v1/intent/*`.
- `prompt_bridge/router.py`: `/api/prompt-bridge/*`.
- `text_studio/router.py`: `/api/text-studio/*`.

When committing route or prompt-storage changes, mention the route file and the
prompt storage path in the commit message body.

## Agent Workflow Guarantees

The MCP Bridge orchestrator must avoid deadlocks and stale state:
- Same `session_id` orchestration requests are serialized through the per-session queue in `mcp_bridge/router.py`.
- Queue wait timeout returns HTTP 409 instead of hanging.
- Turn timeout returns HTTP 504 and rolls back the `SessionState` snapshot.
- Failed turns roll back existing sessions and remove failed new sessions.
- Stale idle sessions are cleaned using `mcp_bridge.orchestrate_session_ttl`.

The Intent Bridge decoder must keep model-derived parameters dynamic:
- Slots may contain only values explicitly stated by the user.
- Family/type/level/host choices must become questions with `enrich`.
- LLM-provided options for dynamic `enrich` questions are stripped before UI display.
- Runtime enrichment in `mcp_bridge/router.py` fills options from live Revit queries.

## Frontend Guidelines

Follow `docs/frontend-style-guide.md`:
- Near-white Graptolite BIM/AI tool interface.
- Use tokens from `frontend/src/index.css`.
- Keep UI dense, clear, and workflow-oriented.
- Buttons are rectangular with small radius; cards are only for repeated items, panels, modals, or bounded workspaces.
- Avoid nested cards and text overflow on mobile.

## Testing And Validation

Use focused validation for the touched layer:

```bash
python -m pytest tests/test_sse_thinking.py -v
python -m pytest intent_bridge/tests/test_agent_workflow_contract.py -v
python tests/test_ppt_stats.py
```

Frontend checks:

```bash
cd frontend
npm run build
npm run lint
```

`tests/test_ppt_stats.py` requires local SQLite data files. Revit execution paths require a live Revit 2026 instance with the plugin connected on port `18080`.

## Editing Conventions

- Check `git status --short --branch` before editing and preserve unrelated user changes.
- Keep changes scoped to the requested layer.
- Prefer existing module patterns and prompt conventions over new abstractions.
- Preserve bilingual docs when editing paired Chinese/English documentation.
- Treat `legacy/` as archived reference unless explicitly instructed otherwise.
- Do not rewrite generated data, build output, or vendored/release artifacts unless the task explicitly requires it.
