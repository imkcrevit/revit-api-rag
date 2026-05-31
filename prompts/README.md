# Prompt Catalog

All editable prompt templates live in this directory. Runtime code loads these files through `prompts.loader.load_prompt()` so prompt behavior can be reviewed independently from backend logic.

## Version Control Policy

Prompt files are product behavior, not local configuration. Keep every `*.md`
template and the Python loader in Git so prompt changes can be reviewed,
tested, reverted, and released with the code that depends on them.

Do not commit runtime caches such as `__pycache__/` or `*.pyc`. Do not move
editable prompt text back into service modules unless the runtime loader cannot
support the use case.

## Runtime Prompts

- `mcp_bridge.system_execute.md` - RAG-grounded Revit C# execution prompt.
- `mcp_bridge.classify_intent.md` - MCP Bridge interaction classifier.
- `mcp_bridge.retry_user.md` / `mcp_bridge.retry_system.md` - compile-error retry repair prompts.
- `intent_bridge.analyze.md` - skill-enhanced parameter and action-plan analyzer.
- `intent_bridge.analyze_legacy.md` - fallback analyzer when skills are disabled.
- `server.rag_system_brief.md` / `server.rag_system_full.md` - legacy RAG chat code prompts.
- `server.api_explorer_codegen.md` / `server.api_explorer_query_understanding.md` - API Explorer prompts.
- `server.text2revit_intent.md` / `server.text2revit_intent_system.md` - Text2Revit intent classifier prompts.
- `prompt_bridge.system.md` - designer prompt refinement system prompt.
- `text_studio.system.md` - translation and polishing prompt.

## Pipeline Prompts

- `pipeline.rewrite_query.md` / `pipeline.rewrite_query_system.md` - RAG query rewrite prompts.
- `pipeline.api_quality_*` - Revit API parser audit and repair prompts.
- `pipeline.sdk_quality_*` - SDK project/file metadata prompts.
- `pipeline.sdk_extract_*` - SDK README extraction prompts.
- `pipeline.sdk_golden_code*` - SDK Golden Code generation prompts.

## Unit Policy

Do not write prompt language that makes a non-project unit the preferred user-facing unit. Revit internal storage is feet, but UI/user prompts should preserve the selected project/user unit and convert to feet only at API boundaries.
