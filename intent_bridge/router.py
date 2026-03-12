"""
Intent Bridge — FastAPI Routes

Prefix: /api/v1/intent/
Endpoints:
  POST /parse                       — Stateless single-turn parse
  POST /session                     — Create session
  POST /session/{id}/turn           — Initial user text (LLM call)
  POST /session/{id}/answer         — Answer wizard question (NO LLM, instant)
  GET  /session/{id}                — Query session state
  POST /session/{id}/slots          — Direct slot update (card UI)
  GET  /schemas                     — List all intents
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger("intent_bridge.router")

from intent_bridge.llm_adapter import LLMAdapter
from intent_bridge.models import (
    AnswerRequest,
    ParseRequest,
    SessionCreateRequest,
    SessionState,
    SessionStatus,
    SlotUpdateRequest,
    TurnRequest,
    TurnResponse,
)
from intent_bridge.slot_engine import ConversationOrchestrator, SchemaRegistry, get_schema_registry

intent_router = APIRouter(prefix="/api/v1/intent", tags=["Intent Bridge"])


# ---------------------------------------------------------------------------
# Session store
# ---------------------------------------------------------------------------

class IntentSessionStore:
    def __init__(self, timeout_seconds: int = 600, max_turns: int = 10):
        self._sessions: dict[str, SessionState] = {}
        self._lock = asyncio.Lock()
        self._timeout = timeout_seconds
        self._max_turns = max_turns

    async def create(self) -> SessionState:
        async with self._lock:
            self._cleanup()
            session = SessionState()
            self._sessions[session.session_id] = session
            return session

    async def get(self, session_id: str) -> SessionState | None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session and not session.is_expired(self._timeout):
                session.touch()
                return session
            return None

    def _cleanup(self):
        expired = [k for k, v in self._sessions.items() if v.is_expired(self._timeout)]
        for k in expired:
            del self._sessions[k]


@lru_cache(maxsize=1)
def _get_session_store() -> IntentSessionStore:
    return IntentSessionStore()


@lru_cache(maxsize=1)
def _get_orchestrator() -> ConversationOrchestrator:
    llm = LLMAdapter()
    registry = get_schema_registry()
    return ConversationOrchestrator(llm=llm, registry=registry)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@intent_router.post("/parse")
async def parse_intent(req: ParseRequest):
    orchestrator = _get_orchestrator()
    session = SessionState()
    try:
        response = await orchestrator.process_turn(req.user_input, session)
        return response.model_dump()
    except RuntimeError as e:
        logger.error("Parse failed: %s", e)
        raise HTTPException(status_code=504, detail=f"LLM call failed: {e}")


@intent_router.post("/session")
async def create_session(req: SessionCreateRequest | None = None):
    store = _get_session_store()
    session = await store.create()
    return {
        "session_id": session.session_id,
        "status": session.status.value,
        "created_at": session.created_at,
    }


@intent_router.post("/session/{session_id}/turn")
async def session_turn(session_id: str, req: TurnRequest):
    """Initial user text → LLM analysis → returns first question or complete."""
    store = _get_session_store()
    session = await store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    orchestrator = _get_orchestrator()
    try:
        response = await orchestrator.process_turn(req.user_input, session)
        return response.model_dump()
    except RuntimeError as e:
        logger.error("Turn failed for session %s: %s", session_id, e)
        raise HTTPException(status_code=504, detail=f"LLM call failed: {e}")


@intent_router.post("/session/{session_id}/answer")
async def answer_question(session_id: str, req: AnswerRequest):
    """Answer a wizard question — NO LLM call, instant response."""
    store = _get_session_store()
    session = await store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    if not session.intent.name:
        raise HTTPException(status_code=400, detail="No intent recognized yet")

    orchestrator = _get_orchestrator()
    response = await orchestrator.answer_question(session, req.value, req.option_index)
    return response.model_dump()


@intent_router.get("/session/{session_id}")
async def get_session(session_id: str):
    store = _get_session_store()
    session = await store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    return {
        "session_id": session.session_id,
        "status": session.status.value,
        "turn_count": session.turn_count,
        "intent": {
            "name": session.intent.name,
            "display_name": session.intent.display_name,
            "confidence": session.intent.confidence,
        },
        "slots": {
            name: {
                "value": slot.value,
                "status": slot.status.value,
                "source": slot.source.value,
            }
            for name, slot in session.intent.slots.items()
        },
        "pending_questions": len(session.pending_questions),
        "history": session.history,
    }


@intent_router.post("/session/{session_id}/slots")
async def update_slots(session_id: str, req: SlotUpdateRequest):
    store = _get_session_store()
    session = await store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    if not session.intent.name:
        raise HTTPException(status_code=400, detail="No intent recognized yet")

    orchestrator = _get_orchestrator()
    response = await orchestrator.update_slots_directly(session, req.slots)
    return response.model_dump()


@intent_router.get("/schemas")
async def list_schemas():
    registry = get_schema_registry()
    intents = []
    for name in registry.get_all_intent_names():
        schema = registry.get_intent_schema(name)
        if schema:
            intents.append({
                "name": name,
                "display_name": schema.get("display_name", name),
                "description": schema.get("description", ""),
                "api_method": schema.get("api_method", ""),
                "slots": list(schema.get("slots", {}).keys()),
            })
    return {"intents": intents}


# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------

def _sse_event(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
