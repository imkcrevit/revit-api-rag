"""
TextStudio 路由 — FastAPI endpoints

- POST /api/text-studio/chat     — SSE 流式对话（带费用守卫）
- POST /api/text-studio/clear    — 清除会话历史
- GET  /api/text-studio/status   — 查询当日费用状态
- GET  /api/text-studio/languages — 支持的语言列表
- POST /api/text-studio/reset    — 管理员手动重置费用（需 admin token）
"""
from __future__ import annotations

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from server.app.deps import get_session_store
from server.app.log_store import get_client_ip, log_and_stream
from text_studio.service import process_text_studio_chat, LANGUAGES
from text_studio.cost_tracker import get_cost_tracker

text_studio_router = APIRouter(prefix="/api/text-studio")


class TextStudioChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    source_lang: str = "auto"
    target_lang: str = "en"
    accept_experimental: bool = False


class TextStudioClearRequest(BaseModel):
    session_id: str


def _is_experimental() -> bool:
    """Check if TextStudio is in experimental mode."""
    try:
        from server.app.deps import get_config
        return get_config().get("text_studio", {}).get("experimental", False)
    except Exception:
        return False


@text_studio_router.post("/chat")
async def text_studio_chat(req: TextStudioChatRequest, request: Request):
    # Experimental gate — only allow if client explicitly opts in
    if _is_experimental():
        if not req.accept_experimental:
            return JSONResponse(
                status_code=403,
                content={
                    "error": "experimental_gate",
                    "message": "TextStudio is in experimental mode. "
                               "Please confirm before using.",
                },
            )

    # Cost guard — reject if daily limit exceeded
    tracker = get_cost_tracker()
    if tracker.is_over_limit():
        status = tracker.get_status()
        return JSONResponse(
            status_code=503,
            content={
                "error": "daily_limit_exceeded",
                "message": f"Daily cost limit (${status['limit_usd']:.2f}) exceeded. "
                           f"Today's cost: ${status['cost_usd']:.4f}. "
                           f"Service interrupted — awaiting manual reset.",
                "status": status,
            },
        )

    store = get_session_store()
    session = store.get_or_create(req.session_id)

    return StreamingResponse(
        log_and_stream(
            process_text_studio_chat(
                req.message, session,
                source_lang=req.source_lang,
                target_lang=req.target_lang,
            ),
            module="text_studio",
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


@text_studio_router.post("/clear")
async def text_studio_clear(req: TextStudioClearRequest):
    store = get_session_store()
    session = store.get(req.session_id)
    if session:
        session.history.clear()
        return {"status": "ok", "message": "History cleared"}
    return JSONResponse(
        status_code=404,
        content={"error": "Session not found"},
    )


@text_studio_router.get("/status")
async def text_studio_status():
    """Return current day's cost status + experimental flag."""
    status = get_cost_tracker().get_status()
    status["experimental"] = _is_experimental()
    return status


@text_studio_router.get("/languages")
async def text_studio_languages():
    """Return supported language list."""
    return {"languages": LANGUAGES}


@text_studio_router.post("/reset")
async def text_studio_reset(request: Request):
    """Admin-only: reset today's cost counter."""
    # Verify admin token
    token = request.headers.get("x-admin-token") or request.query_params.get("token")
    if not token:
        raise HTTPException(401, "Admin token required")

    import hmac
    from server.app.deps import get_config
    config = get_config()
    password = config.get("admin", {}).get("password", "")
    if not password or not hmac.compare_digest(token, password):
        raise HTTPException(403, "Invalid admin token")

    get_cost_tracker().reset_today()
    return {"status": "ok", "message": "Daily cost counter reset"}
