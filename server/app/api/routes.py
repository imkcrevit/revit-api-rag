"""
API 路由 — FastAPI endpoints

- POST /api/chat      — SSE 流式聊天（代码生成）
- POST /api/t2r/chat  — SSE 流式聊天（Text2Revit 对话）
- POST /api/search    — 纯检索
- GET  /api/config    — 可用模型列表
- POST /api/settings  — 更新 API Key / 模型
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse

from server.app.models import ChatRequest, SearchRequest, SettingsUpdate, ConfigResponse
from server.app.deps import get_session_store, get_config
from server.app.rag.service import process_chat, process_search
from server.app.text2revit.service import process_t2r_chat

router = APIRouter(prefix="/api")


@router.post("/chat")
async def chat(req: ChatRequest):
    store = get_session_store()
    session = store.get_or_create(req.session_id)

    return StreamingResponse(
        process_chat(req.message, session, show_full=req.show_full),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Session-Id": session.session_id,
        },
    )


@router.post("/t2r/chat")
async def t2r_chat(req: ChatRequest):
    store = get_session_store()
    session = store.get_or_create(req.session_id)

    return StreamingResponse(
        process_t2r_chat(req.message, session),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Session-Id": session.session_id,
        },
    )


@router.post("/search")
async def search(req: SearchRequest):
    results = await process_search(req.query, req.api_top_k, req.code_top_k)
    return JSONResponse(content=results)


@router.get("/config")
async def config_info():
    config = get_config()
    llm_cfg = config.get("llm", {})
    models_cfg = llm_cfg.get("models", {})

    available = []
    for provider, mcfg in models_cfg.items():
        available.append({
            "provider": provider,
            "model": mcfg.get("model", ""),
        })

    return ConfigResponse(
        available_models=available,
        default_provider=llm_cfg.get("provider", "claude"),
        revit_version=config.get("revit_version", "2026"),
    )


@router.post("/settings")
async def update_settings(req: SettingsUpdate, request: Request):
    session_id = request.headers.get("X-Session-Id")
    if not session_id:
        return JSONResponse(
            status_code=400,
            content={"error": "X-Session-Id header required"},
        )

    store = get_session_store()
    session = store.get(session_id)
    if not session:
        return JSONResponse(
            status_code=404,
            content={"error": "Session not found"},
        )

    if req.api_key is not None:
        # Empty string = clear user key (revert to system key)
        session.api_key = req.api_key.strip()
    if req.model is not None:
        session.model_provider = req.model

    return {
        "status": "ok",
        "session_id": session.session_id,
        "using_custom_key": bool(session.api_key),
    }
