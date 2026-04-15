"""
PromptBridge 路由 — FastAPI endpoints

- POST /api/prompt-bridge/chat  — SSE 流式对话
- POST /api/prompt-bridge/clear — 清除会话历史
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from server.app.deps import get_session_store
from prompt_bridge.service import process_prompt_bridge_chat

prompt_bridge_router = APIRouter(prefix="/api/prompt-bridge")


class PromptBridgeChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    context_type: str | None = None


class PromptBridgeClearRequest(BaseModel):
    session_id: str


@prompt_bridge_router.post("/chat")
async def prompt_bridge_chat(req: PromptBridgeChatRequest):
    store = get_session_store()
    session = store.get_or_create(req.session_id)

    return StreamingResponse(
        process_prompt_bridge_chat(req.message, session, req.context_type),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Session-Id": session.session_id,
        },
    )


@prompt_bridge_router.post("/clear")
async def prompt_bridge_clear(req: PromptBridgeClearRequest):
    store = get_session_store()
    session = store.get(req.session_id)
    if session:
        session.history.clear()
        return {"status": "ok", "message": "History cleared"}
    return JSONResponse(
        status_code=404,
        content={"error": "Session not found"},
    )
