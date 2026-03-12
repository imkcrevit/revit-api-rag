"""
Intent Bridge — 10 个端到端真实场景测试

需要 OPENROUTER_API_KEY 环境变量。
运行：pytest intent_bridge/tests/test_e2e.py -v

测试分类：
- 3 个信息完整（一轮完成）
- 3 个信息不完整（触发追问）
- 2 个含歧义
- 2 个边界情况
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from intent_bridge.llm_adapter import LLMAdapter
from intent_bridge.models import SessionState, SessionStatus
from intent_bridge.slot_engine import ConversationOrchestrator, SchemaRegistry

# ---------------------------------------------------------------------------
# Skip if no API key
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    not os.getenv("OPENROUTER_API_KEY"),
    reason="OPENROUTER_API_KEY not set",
)

# ---------------------------------------------------------------------------
# Test cases (from dev doc v0.2)
# ---------------------------------------------------------------------------

TEST_CASES = [
    # --- Complete info, should finish in one turn (CN) ---
    {
        "id": "complete_wall_zh",
        "input": "在客厅北边加一面2.4米高的隔墙",
        "expected_intent": "create_wall",
        "expected_slots": {
            "wall_type": "partition",
            "height": 2400,
        },
        "should_ask": False,
        "description": "Complete wall creation (Chinese)",
    },
    # --- Complete info, should finish in one turn (EN) ---
    {
        "id": "complete_wall_en",
        "input": "Add a 2.4 meter high partition wall on the north side of the living room",
        "expected_intent": "create_wall",
        "expected_slots": {
            "wall_type": "partition",
            "height": 2400,
        },
        "should_ask": False,
        "description": "Complete wall creation (English)",
    },
    {
        "id": "complete_modify_zh",
        "input": "把主卧的门换成推拉门",
        "expected_intent": "modify_element",
        "expected_slots": {
            "property_name": ["door_type", "type"],
            "new_value": ["推拉门", "sliding"],
        },
        "should_ask": False,
        "description": "Complete element modification (Chinese)",
    },
    {
        "id": "complete_query_en",
        "input": "How big is the living room area?",
        "expected_intent": "query_element",
        "should_ask": False,
        "description": "Complete element query (English)",
    },
    # --- Incomplete info, should trigger follow-up (CN) ---
    {
        "id": "incomplete_wall_zh",
        "input": "加一面墙",
        "expected_intent": "create_wall",
        "should_ask": True,
        "description": "Incomplete wall creation (Chinese)",
    },
    # --- Incomplete info, should trigger follow-up (EN) ---
    {
        "id": "incomplete_door_en",
        "input": "add a door",
        "expected_intent": "create_door",
        "should_ask": True,
        "description": "Incomplete door creation (English)",
    },
    {
        "id": "incomplete_floor_en",
        "input": "create a floor slab",
        "expected_intent": "create_floor",
        "should_ask": True,
        "description": "Incomplete floor creation (English)",
    },
    # --- Ambiguous (CN + EN) ---
    {
        "id": "ambiguous_windows_zh",
        "input": "南面开三扇大窗户",
        "expected_intent": "create_window",
        "description": "Ambiguous: quantity + vague size (Chinese)",
        "notes": "LLM must handle '三扇' (quantity=3) and '大' (vague size)",
    },
    {
        "id": "ambiguous_batch_modify_en",
        "input": "raise all load-bearing walls to 3.6 meters",
        "expected_intent": "modify_element",
        "description": "Batch modify + filter condition (English)",
        "notes": "target is batch (all structural walls), not single element",
    },
    # --- Edge cases (CN + EN) ---
    {
        "id": "edge_batch_delete_en",
        "input": "delete all non-structural walls on the second floor",
        "expected_intent": "delete_element",
        "description": "Batch delete + filter + confirmation required (English)",
        "notes": "Batch delete must trigger confirmation",
    },
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def orchestrator():
    llm = LLMAdapter()
    registry = SchemaRegistry()
    return ConversationOrchestrator(llm=llm, registry=registry)


@pytest.fixture
def session():
    return SessionState()


# ---------------------------------------------------------------------------
# Test report collector
# ---------------------------------------------------------------------------

_report_entries: list[dict] = []


def _record_result(case: dict, response, duration_ms: float, passed: bool, details: str = ""):
    _report_entries.append({
        "id": case["id"],
        "description": case.get("description", ""),
        "input": case["input"],
        "expected_intent": case.get("expected_intent", ""),
        "actual_intent": response.intent.get("name", "") if response else "",
        "status": response.status.value if response else "error",
        "slots": response.slots if response else {},
        "missing": [m.model_dump() for m in response.missing] if response else [],
        "duration_ms": duration_ms,
        "passed": passed,
        "details": details,
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _get_case(case_id: str) -> dict:
    """Lookup test case by ID."""
    for c in TEST_CASES:
        if c["id"] == case_id:
            return c
    raise ValueError(f"Test case {case_id} not found")


async def _run_case(orchestrator, case: dict):
    """Run a single test case and record result."""
    sess = SessionState()
    start = time.time()
    resp = await orchestrator.process_turn(case["input"], sess)
    duration = (time.time() - start) * 1000

    intent_ok = resp.intent.get("name") == case["expected_intent"]
    slot_ok = True
    details = []

    if case.get("expected_slots"):
        for key, expected in case["expected_slots"].items():
            actual = resp.slots.get(key, {}).get("value")
            if isinstance(expected, list):
                if actual not in expected:
                    slot_ok = False
                    details.append(f"{key}: expected one of {expected}, got {actual}")
            elif actual != expected:
                slot_ok = False
                details.append(f"{key}: expected {expected}, got {actual}")

    if case.get("should_ask") is True:
        asks = resp.status == SessionStatus.need_followup
        if not asks:
            details.append("Expected follow-up but got none")
        passed = intent_ok and asks
    else:
        passed = intent_ok and slot_ok

    _record_result(case, resp, duration, passed, "; ".join(details))
    return resp, intent_ok, passed, details


class TestIntentComplete:
    """Complete info cases — should finish in one turn (CN + EN)"""

    @pytest.mark.asyncio
    async def test_complete_wall_zh(self, orchestrator):
        case = _get_case("complete_wall_zh")
        resp, intent_ok, passed, details = await _run_case(orchestrator, case)
        assert intent_ok, f"Expected {case['expected_intent']}, got {resp.intent.get('name')}"

    @pytest.mark.asyncio
    async def test_complete_wall_en(self, orchestrator):
        case = _get_case("complete_wall_en")
        resp, intent_ok, passed, details = await _run_case(orchestrator, case)
        assert intent_ok, f"Expected {case['expected_intent']}, got {resp.intent.get('name')}"

    @pytest.mark.asyncio
    async def test_complete_modify_zh(self, orchestrator):
        case = _get_case("complete_modify_zh")
        resp, intent_ok, passed, details = await _run_case(orchestrator, case)
        assert intent_ok, f"Expected {case['expected_intent']}, got {resp.intent.get('name')}"

    @pytest.mark.asyncio
    async def test_complete_query_en(self, orchestrator):
        case = _get_case("complete_query_en")
        resp, intent_ok, passed, details = await _run_case(orchestrator, case)
        assert intent_ok, f"Expected {case['expected_intent']}, got {resp.intent.get('name')}"


class TestIntentIncomplete:
    """Incomplete info — should trigger follow-up (CN + EN)"""

    @pytest.mark.asyncio
    async def test_incomplete_wall_zh(self, orchestrator):
        case = _get_case("incomplete_wall_zh")
        resp, intent_ok, passed, details = await _run_case(orchestrator, case)
        assert intent_ok, f"Expected {case['expected_intent']}, got {resp.intent.get('name')}"
        assert resp.status == SessionStatus.need_followup, "Should trigger follow-up"

    @pytest.mark.asyncio
    async def test_incomplete_door_en(self, orchestrator):
        case = _get_case("incomplete_door_en")
        resp, intent_ok, passed, details = await _run_case(orchestrator, case)
        assert intent_ok, f"Expected {case['expected_intent']}, got {resp.intent.get('name')}"
        assert resp.status == SessionStatus.need_followup, "Should trigger follow-up"

    @pytest.mark.asyncio
    async def test_incomplete_floor_en(self, orchestrator):
        case = _get_case("incomplete_floor_en")
        resp, intent_ok, passed, details = await _run_case(orchestrator, case)
        assert intent_ok, f"Expected {case['expected_intent']}, got {resp.intent.get('name')}"
        assert resp.status == SessionStatus.need_followup, "Should trigger follow-up"


class TestIntentAmbiguous:
    """Ambiguous cases (CN + EN)"""

    @pytest.mark.asyncio
    async def test_ambiguous_windows_zh(self, orchestrator):
        case = _get_case("ambiguous_windows_zh")
        resp, intent_ok, passed, details = await _run_case(orchestrator, case)
        assert intent_ok, f"Expected {case['expected_intent']}, got {resp.intent.get('name')}"

    @pytest.mark.asyncio
    async def test_ambiguous_batch_modify_en(self, orchestrator):
        case = _get_case("ambiguous_batch_modify_en")
        resp, intent_ok, passed, details = await _run_case(orchestrator, case)
        assert intent_ok, f"Expected {case['expected_intent']}, got {resp.intent.get('name')}"


class TestIntentEdge:
    """Edge cases (EN)"""

    @pytest.mark.asyncio
    async def test_edge_batch_delete_en(self, orchestrator):
        case = _get_case("edge_batch_delete_en")
        resp, intent_ok, passed, details = await _run_case(orchestrator, case)
        assert intent_ok, f"Expected {case['expected_intent']}, got {resp.intent.get('name')}"


# ---------------------------------------------------------------------------
# Report generation (runs after all tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def generate_report(request):
    """Generate test report after all tests complete"""
    yield

    if not _report_entries:
        return

    report_path = Path(__file__).parent / "report.md"
    total = len(_report_entries)
    passed = sum(1 for e in _report_entries if e["passed"])
    avg_duration = sum(e["duration_ms"] for e in _report_entries) / total if total else 0

    lines = [
        "# Intent Bridge E2E Test Report",
        "",
        f"**Total**: {total} | **Passed**: {passed} | **Failed**: {total - passed} | **Accuracy**: {passed/total*100:.0f}%",
        f"**Average Duration**: {avg_duration:.0f}ms",
        "",
        "---",
        "",
    ]

    for entry in _report_entries:
        status_icon = "✅" if entry["passed"] else "❌"
        lines.append(f"## {status_icon} {entry['id']}: {entry['description']}")
        lines.append(f"**Input**: `{entry['input']}`")
        lines.append(f"**Expected Intent**: `{entry['expected_intent']}`")
        lines.append(f"**Actual Intent**: `{entry['actual_intent']}`")
        lines.append(f"**Status**: `{entry['status']}`")
        lines.append(f"**Duration**: {entry['duration_ms']:.0f}ms")

        if entry["slots"]:
            lines.append(f"**Slots**: ```json\n{json.dumps(entry['slots'], ensure_ascii=False, indent=2)}\n```")
        if entry["missing"]:
            lines.append(f"**Missing**: {json.dumps(entry['missing'], ensure_ascii=False)}")
        if entry["details"]:
            lines.append(f"**Details**: {entry['details']}")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n📊 Test report written to {report_path}")
