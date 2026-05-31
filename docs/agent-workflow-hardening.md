# Agent Workflow Hardening

This document summarizes the workflow changes in this revision and records
where future changes should be made.

## Change Summary

| Area | Files | Change |
|---|---|---|
| Prompt storage | `prompts/`, `prompts/loader.py` | Moved editable prompt templates into version-controlled files loaded by `load_prompt()` |
| Prompt deployment | `Dockerfile`, `.dockerignore` | Copies `prompts/` into the runtime image and allows prompt markdown through Docker ignore rules |
| Agent decoding | `intent_bridge/slot_engine.py` | Sanitizes LLM JSON output, strips fabricated dynamic options, and removes default-like slot values |
| Dynamic choices | `mcp_bridge/router.py`, `mcp_bridge/interactive.py` | Preserves `family_type:OST_*` enrich tags and validates Revit categories before querying live choices |
| Queue and rollback | `mcp_bridge/router.py` | Serializes same-session orchestration requests, times out stalled queues, and rolls back failed turns |
| Config | `config/config.example.yaml` | Adds orchestrator queue timeout, turn timeout, and session TTL knobs |
| Frontend UI | `frontend/src/**` | Improves responsive controls, dynamic pick display, split API base URL support, and typed error handling |
| Tests | `intent_bridge/tests/test_agent_workflow_contract.py` | Covers dynamic option sanitization, category validation, rollback, queue timeout, and Pydantic default isolation |

## Prompt Storage Route

Prompt templates are product behavior and must be committed under `prompts/`.
Runtime modules should import `load_prompt()` instead of embedding long strings.

| Prompt domain | Storage path | Runtime consumer |
|---|---|---|
| MCP Bridge execution | `prompts/mcp_bridge.system_execute.md` | `mcp_bridge/code_generator.py` |
| MCP Bridge retry | `prompts/mcp_bridge.retry_*.md` | `mcp_bridge/retry.py` |
| MCP Bridge classification | `prompts/mcp_bridge.classify_intent.md` | `mcp_bridge/interactive.py` |
| Intent Bridge analysis | `prompts/intent_bridge.analyze*.md` | `intent_bridge/slot_engine.py` |
| Server RAG and API Explorer | `prompts/server.*.md` | `server/app/prompts/*.py`, `server/app/text2revit/intent.py` |
| PromptBridge and TextStudio | `prompts/prompt_bridge.system.md`, `prompts/text_studio.system.md` | `prompt_bridge/service.py`, `text_studio/service.py` |
| Pipeline quality and rewrite | `prompts/pipeline.*.md` | `pipeline/**` |

## Runtime Route Locations

| URL area | Route file |
|---|---|
| Router mounting and React/Gradio serving | `server/main.py` |
| Core `/api/*` routes | `server/app/api/routes.py` |
| `/api/skills/*` | `server/app/api/skill_routes.py` |
| `/api/logs/*` | `server/app/api/log_routes.py` |
| `/api/v1/intent/*` | `intent_bridge/router.py` |
| `/api/v1/bridge/*` | `mcp_bridge/router.py` |
| `/api/prompt-bridge/*` | `prompt_bridge/router.py` |
| `/api/text-studio/*` | `text_studio/router.py` |

Commit messages for endpoint or prompt-location changes should include the
route file and prompt storage path. Example:

```text
Route locations: mcp_bridge/router.py, server/main.py
Prompt storage: prompts/*.md via prompts/loader.py
```

## Agent Workflow Contract

Intent analysis must distinguish user-stated values from runtime model choices:

- Keep only exact user-stated values in `slots`.
- Convert model-derived values such as family type, level, host, material, view,
  sheet, and system selections into `questions`.
- Use `enrich` to mark dynamic questions: `family_type:<category>`, `level`,
  `host_pick`, or `none`.
- Strip LLM-provided options and values for dynamic `enrich` questions before UI
  display. Live Revit queries own those options.
- Never silently default to `Level 1`, first available type, generic wall type,
  or `(0,0,0)` unless the user explicitly provided that value.

MCP Bridge orchestration must remain single-turn-per-session:

- Requests with the same `session_id` queue behind the current turn.
- Queue timeout returns HTTP 409.
- Turn timeout returns HTTP 504.
- Failed or cancelled existing turns roll back to the previous `SessionState`.
- Failed new sessions are removed instead of leaving stale state.

## Validation

Use the focused checks below after changing this area:

```bash
python -m pytest intent_bridge/tests/test_agent_workflow_contract.py -v
python -m pytest tests/test_sse_thinking.py -v
cd frontend && npm run lint
cd frontend && npm run build
```
