import asyncio

import pytest
from fastapi import HTTPException

from intent_bridge.models import SessionState
from intent_bridge.slot_engine import ConversationOrchestrator
from mcp_bridge.interactive import IntentClassifier
from mcp_bridge.router import (
    GenerateWithSelectionsRequest,
    _OrchSessionEntry,
    _acquire_orch_lock,
    _orch_sessions,
    _process_orch_with_rollback,
)


def test_dynamic_questions_do_not_keep_llm_fabricated_options():
    questions = ConversationOrchestrator._parse_questions([
        {
            "slot": "wall_type",
            "text": "请选择墙类型 / Select wall type",
            "options": ["Generic - 200mm", "Basic Wall"],
            "values": ["Generic - 200mm", "Basic Wall"],
            "allow_custom": False,
            "enrich": "family_type:OST_Walls",
        },
        {
            "slot": "level",
            "text": "请选择标高 / Select level",
            "options": ["Level 1"],
            "values": ["Level 1"],
            "enrich": "level",
        },
    ])

    assert len(questions) == 2
    for question in questions:
        assert question.options == []
        assert question.values == []
        assert question.allow_custom is True


def test_decode_sanitizer_prefers_question_over_same_named_slot():
    result = ConversationOrchestrator._sanitize_llm_result(
        {
            "intent": "create_wall",
            "slots": {
                "quantity": 3,
                "wall_type": "Generic - 200mm",
                "level": "Level 1",
            },
            "questions": [
                {
                    "slot": "wall_type",
                    "text": "请选择墙类型 / Select wall type",
                    "enrich": "family_type:wall",
                    "options": ["Generic - 200mm"],
                },
                {
                    "slot": "wall_curves",
                    "text": "请提供 3 条墙线的起点和终点 / Provide start and end points for 3 wall curves",
                    "enrich": "none",
                },
            ],
        },
        "创建三面墙",
    )

    assert result["slots"] == {"quantity": 3}
    assert result["questions"][0]["options"] == []
    assert result["questions"][0]["values"] == []
    assert result["questions"][0]["allow_custom"] is True


class _FakeClassifierLLM:
    def generate_text(self, _prompt, system_prompt=None):
        return """
        {
          "interaction_type": "select_family",
          "revit_categories": ["OST_Walls", "OST_NotARealCategory"],
          "label": "墙类型",
          "need_level": true,
          "need_host": false,
          "select_prompt": null
        }
        """


def test_classifier_rejects_invalid_categories():
    result = IntentClassifier()._classify_with_llm(_FakeClassifierLLM(), "创建一面墙")

    assert result is not None
    assert result["interaction_type"] == "select_family"
    assert result["queries"][0]["params"]["categoryList"] == ["OST_Walls"]


class _MutatingFailingOrchestrator:
    async def process_turn(self, query, session):
        session.turn_count = 7
        session.add_message("user", query)
        raise RuntimeError("boom")


def test_orchestrator_turn_rolls_back_existing_session_on_failure():
    async def run_case():
        sid = "rollback-test"
        entry = _OrchSessionEntry(
            orch=_MutatingFailingOrchestrator(),
            session=SessionState(),
        )
        _orch_sessions[sid] = entry
        try:
            with pytest.raises(HTTPException) as exc:
                await _process_orch_with_rollback(
                    sid,
                    entry,
                    "创建一面墙",
                    is_new=False,
                    timeout=1,
                )

            assert exc.value.status_code == 500
            assert entry.session.turn_count == 0
            assert entry.session.history == []
            assert _orch_sessions[sid] is entry
        finally:
            _orch_sessions.pop(sid, None)

    asyncio.run(run_case())


def test_orchestrator_queue_times_out_instead_of_deadlocking():
    async def run_case():
        sid = "queue-timeout-test"
        entry = _OrchSessionEntry(orch=object(), session=SessionState())
        await entry.lock.acquire()
        try:
            with pytest.raises(HTTPException) as exc:
                await _acquire_orch_lock(sid, entry, timeout=0.01)

            assert exc.value.status_code == 409
            assert entry.queue_depth == 0
        finally:
            if entry.lock.locked():
                entry.lock.release()

    asyncio.run(run_case())


def test_request_model_dict_defaults_are_isolated():
    first = GenerateWithSelectionsRequest(query="a")
    second = GenerateWithSelectionsRequest(query="b")

    first.selections["wall_type"] = "A"

    assert second.selections == {}
