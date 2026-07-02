"""
API 路由 — FastAPI endpoints

- POST /api/chat      — SSE 流式聊天（代码生成）
- POST /api/t2r/chat  — SSE 流式聊天（Text2Revit 对话）
- POST /api/search    — 纯检索
- GET  /api/config    — 可用模型列表
- POST /api/settings  — 更新 API Key / 模型
"""
from __future__ import annotations

import hmac
import os
import threading
import time

from fastapi import APIRouter, Request, Header, HTTPException, Depends
from fastapi.responses import StreamingResponse, JSONResponse

from server.app.models import ChatRequest, SearchRequest, SettingsUpdate, ConfigResponse
from server.app.deps import get_session_store, get_config
from server.app.rag.service import process_chat, process_search
from server.app.text2revit.service import process_t2r_chat
from server.app.log_store import get_client_ip, log_and_stream

router = APIRouter(prefix="/api")


# ── App token + simple in-memory IP rate limiting (no external deps) ──────
_RATE_LIMIT = 30       # max requests per IP
_RATE_WINDOW = 60      # per this many seconds
_rate_lock = threading.Lock()
_rate_hits: dict[str, list[float]] = {}


def verify_app_token(x_app_token: str | None = Header(None)):
    """Reject requests lacking a valid X-App-Token (enforced only when APP_TOKEN is set)."""
    expected = os.getenv("APP_TOKEN", "")
    if not expected:
        return  # not configured → no enforcement (local/dev)
    if not x_app_token or not hmac.compare_digest(x_app_token, expected):
        raise HTTPException(403, "Invalid or missing X-App-Token")


def rate_limit(request: Request):
    """Per-IP sliding-window rate limit to protect the shared OPENROUTER_API_KEY."""
    ip = get_client_ip(request)
    now = time.time()
    with _rate_lock:
        hits = [t for t in _rate_hits.get(ip, []) if now - t < _RATE_WINDOW]
        if len(hits) >= _RATE_LIMIT:
            raise HTTPException(429, "Too many requests")
        hits.append(now)
        _rate_hits[ip] = hits


@router.post("/chat", dependencies=[Depends(verify_app_token), Depends(rate_limit)])
async def chat(req: ChatRequest, request: Request):
    store = get_session_store()
    session = store.get_or_create(req.session_id)

    return StreamingResponse(
        log_and_stream(
            process_chat(req.message, session, show_full=req.show_full),
            module="code_gen",
            session_id=session.session_id,
            client_ip=get_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
            user_input=req.message,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Session-Id": session.session_id,
        },
    )


@router.post("/t2r/chat", dependencies=[Depends(verify_app_token), Depends(rate_limit)])
async def t2r_chat(req: ChatRequest, request: Request):
    """Legacy Text2Revit endpoint. Use /api/v1/intent/* instead."""
    store = get_session_store()
    session = store.get_or_create(req.session_id)

    return StreamingResponse(
        log_and_stream(
            process_t2r_chat(req.message, session),
            module="text2revit",
            session_id=session.session_id,
            client_ip=get_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
            user_input=req.message,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Session-Id": session.session_id,
            "X-Deprecated": "Use /api/v1/intent/parse or /api/v1/intent/session instead",
        },
    )


@router.post("/search", dependencies=[Depends(verify_app_token), Depends(rate_limit)])
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
