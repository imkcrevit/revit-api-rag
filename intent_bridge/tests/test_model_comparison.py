"""
Multi-model comparison test — semantic understanding across Gemini / Codex / Claude

Tests whether each LLM properly:
1. Extracts quantity into slots (not silently ignore it)
2. Asks about element type/family (not default one)
3. Asks about level (not default one)
4. Asks for positions/coordinates matching quantity (not invent them)
5. Does NOT silently default ANY parameter

Run all:   pytest intent_bridge/tests/test_model_comparison.py -v -s
Run one:   pytest intent_bridge/tests/test_model_comparison.py -v -s -k "三面墙"
Requires:  OPENROUTER_API_KEY env var
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from intent_bridge.llm_adapter import LLMAdapter
from intent_bridge.models import SessionState, SessionStatus
from intent_bridge.slot_engine import (
    ConversationOrchestrator,
    SchemaRegistry,
    _ANALYZE_PROMPT,
    _extract_search_terms,
    _format_api_context,
    _query_api_by_method,
    get_schema_registry,
)

# ---------------------------------------------------------------------------
# Skip if no API key
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    not os.getenv("OPENROUTER_API_KEY"),
    reason="OPENROUTER_API_KEY not set",
)

# ---------------------------------------------------------------------------
# Models to compare (all via OpenRouter)
# ---------------------------------------------------------------------------

MODELS = {
    "gemini": {
        "primary": {"model": "google/gemini-3-flash-preview", "temperature": 0.1},
        "fallback": {"model": "google/gemini-3-flash-preview", "temperature": 0.1},
    },
    "codex": {
        "primary": {"model": "openai/gpt-5.3-codex", "temperature": 0.1},
        "fallback": {"model": "openai/gpt-5.3-codex", "temperature": 0.1},
    },
    "claude": {
        "primary": {"model": "anthropic/claude-sonnet-4", "temperature": 0.1},
        "fallback": {"model": "anthropic/claude-sonnet-4", "temperature": 0.1},
    },
}

# ---------------------------------------------------------------------------
# Test cases — each defines input + expected behavior
# ---------------------------------------------------------------------------

TEST_CASES = {
    "创建两个结构柱": {
        "input": "创建两个结构柱",
        "expected_quantity": 2,
        "expected_intent": "custom",          # column via NewFamilyInstance
        "type_topic_patterns": [r"type", r"类型", r"族", r"family", r"column_type", r"柱.*型"],
        "position_topic_patterns": [r"position", r"coordinate", r"坐标", r"位置", r"xyz", r"放置"],
        "forbidden_slot_keys": {"column_type", "level", "position", "positions_array",
                                "location", "xyz", "coordinates"},
        # Walls use start/end points, not position arrays
        "position_count_patterns": [
            r"(?:柱|column|col)\s*\d",
            r"(\d+)\s*(?:个|根|处|columns?|positions?|points?)",
        ],
    },
    "创建三面墙": {
        "input": "创建三面墙",
        "expected_quantity": 3,
        "expected_intent": "create_wall",     # or "custom"
        "type_topic_patterns": [r"type", r"类型", r"族", r"family", r"wall_type", r"墙.*型",
                                r"wall.*type", r"厚度"],
        "position_topic_patterns": [r"position", r"coordinate", r"坐标", r"位置", r"xyz",
                                    r"起点", r"终点", r"start", r"end", r"放置",
                                    r"线段", r"端点"],
        "forbidden_slot_keys": {"wall_type", "level", "position", "positions_array",
                                "start_point", "end_point", "location", "xyz", "coordinates",
                                "start_points", "end_points"},
        "position_count_patterns": [
            r"(?:墙|wall)\s*\d",
            r"(\d+)\s*(?:面|个|段|道|walls?|lines?|segments?)",
        ],
    },
}

# Shared forbidden value patterns — LLM should NEVER silently fill these
FORBIDDEN_VALUE_PATTERNS = [
    r"\(\s*-?\d+\s*,\s*-?\d+\s*,\s*-?\d+\s*\)",   # (0,0,0)
    r"-?\d+\s*,\s*-?\d+\s*,\s*-?\d+",               # 0,0,0
    r"Level\s*\d",                                     # Level 1
    r"标高\s*\d",                                      # 标高 1
    r"first\s+available",
    r"default",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ModelTestResult:
    model_name: str
    test_input: str = ""
    duration_ms: float = 0
    raw_response: str = ""
    parsed_json: dict = field(default_factory=dict)
    # Checks
    has_quantity_slot: bool = False
    quantity_value: Any = None
    asks_about_type: bool = False
    asks_about_level: bool = False
    asks_about_positions: bool = False
    positions_ask_count: int = 0
    has_forbidden_defaults: bool = False
    forbidden_details: list[str] = field(default_factory=list)
    question_count: int = 0
    questions: list[dict] = field(default_factory=list)
    slots: dict = field(default_factory=dict)
    error: str = ""
    score: int = 0  # out of 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_prompt(user_input: str) -> str:
    """Build the same prompt slot_engine uses (with RAG context)."""
    registry = get_schema_registry()
    search_terms = _extract_search_terms(user_input, registry)
    api_docs = _query_api_by_method(search_terms, 8)
    rag_context = _format_api_context(api_docs)
    return _ANALYZE_PROMPT.format(
        intent_list=registry.get_intent_summary(),
        rag_context=rag_context,
        user_input=user_input,
    )


def _check_topic(questions: list[dict], patterns: list[str]) -> bool:
    """Check if any question text/slot matches any of the patterns."""
    for q in questions:
        text = (q.get("text", "") + " " + q.get("slot", "")).lower()
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                return True
    return False


def _count_positions_asked(questions: list[dict], case: dict) -> int:
    """How many positions does the position question ask for?"""
    pos_pats = case["position_topic_patterns"]
    count_pats = case["position_count_patterns"]

    for q in questions:
        text = (q.get("text", "") + " " + q.get("slot", "")).lower()
        # Is this a position question?
        is_pos = any(re.search(p, text, re.IGNORECASE) for p in pos_pats)
        if not is_pos:
            continue
        # Try count patterns
        for pat in count_pats:
            # Numbered list pattern (墙 1, 墙 2, ...)
            if r"\d" in pat and not pat.startswith("("):
                matches = re.findall(pat, text, re.IGNORECASE)
                if matches:
                    return len(matches)
            else:
                m = re.search(pat, text, re.IGNORECASE)
                if m and m.groups():
                    return int(m.group(1))
    return 0


def _check_forbidden_defaults(slots: dict, case: dict) -> tuple[bool, list[str]]:
    """Check if the model silently defaulted forbidden parameters."""
    issues = []
    forbidden_keys = case["forbidden_slot_keys"]

    for key, value in slots.items():
        if key in ("quantity", "_api_method", "api_method", "structuralType"):
            continue
        val_str = str(value).lower() if value is not None else ""

        for pat in FORBIDDEN_VALUE_PATTERNS:
            if re.search(pat, val_str, re.IGNORECASE):
                issues.append(f"Slot '{key}' has forbidden default: {value}")

        if key.lower() in {k.lower() for k in forbidden_keys} and value is not None:
            issues.append(f"Slot '{key}' was silently filled with: {value}")

    return len(issues) > 0, issues


def _evaluate(result: ModelTestResult, case: dict):
    """Score 0-5."""
    score = 0
    if result.has_quantity_slot and result.quantity_value == case["expected_quantity"]:
        score += 1
    if result.asks_about_type:
        score += 1
    if result.asks_about_level:
        score += 1
    if result.asks_about_positions:
        score += 1
    if not result.has_forbidden_defaults:
        score += 1
    result.score = score


# ---------------------------------------------------------------------------
# Core: test single model with specific input
# ---------------------------------------------------------------------------

async def _test_raw(model_name: str, model_config: dict, case: dict) -> ModelTestResult:
    """Send prompt to one model and analyze raw JSON output."""
    user_input = case["input"]
    result = ModelTestResult(model_name=model_name, test_input=user_input)

    llm_config = {
        **model_config,
        "base_url": "https://openrouter.ai/api/v1",
        "timeout": 60,
        "max_retries": 1,
        "max_tokens": 4096,
    }
    llm = LLMAdapter(config=llm_config)
    prompt = _build_prompt(user_input)
    start = time.time()

    try:
        raw = await llm.complete_async(prompt, temperature=0.1)
        result.duration_ms = (time.time() - start) * 1000
        result.raw_response = raw
        result.parsed_json = LLMAdapter.extract_json(raw)
    except Exception as e:
        result.duration_ms = (time.time() - start) * 1000
        result.error = str(e)
        return result
    finally:
        await llm.aclose()

    _analyze_result(result, case)
    return result


async def _test_orchestrator(model_name: str, model_config: dict, case: dict) -> ModelTestResult:
    """Test through full ConversationOrchestrator pipeline."""
    user_input = case["input"]
    result = ModelTestResult(model_name=model_name, test_input=user_input)

    llm_config = {
        **model_config,
        "base_url": "https://openrouter.ai/api/v1",
        "timeout": 60,
        "max_retries": 1,
        "max_tokens": 4096,
    }
    llm = LLMAdapter(config=llm_config)
    orch = ConversationOrchestrator(llm=llm)
    session = SessionState()

    start = time.time()
    try:
        resp = await orch.process_turn(user_input, session)
        result.duration_ms = (time.time() - start) * 1000
    except Exception as e:
        result.duration_ms = (time.time() - start) * 1000
        result.error = str(e)
        await llm.aclose()
        return result
    await llm.aclose()

    # Extract from TurnResponse
    result.slots = {k: v.get("value") for k, v in resp.slots.items()}
    all_questions = []
    if resp.current_question:
        all_questions.append({
            "slot": resp.current_question.slot,
            "text": resp.current_question.text,
            "options": resp.current_question.options,
        })
    for q in session.pending_questions:
        all_questions.append({"slot": q.slot, "text": q.text, "options": q.options})
    result.questions = all_questions
    result.question_count = len(all_questions)

    # Analyze
    qty = result.slots.get("quantity")
    if qty is not None:
        result.has_quantity_slot = True
        result.quantity_value = qty

    level_pats = [r"level", r"标高", r"楼层", r"floor"]
    result.asks_about_type = _check_topic(result.questions, case["type_topic_patterns"])
    result.asks_about_level = _check_topic(result.questions, level_pats)
    result.asks_about_positions = _check_topic(result.questions, case["position_topic_patterns"])
    result.positions_ask_count = _count_positions_asked(result.questions, case)
    result.has_forbidden_defaults, result.forbidden_details = _check_forbidden_defaults(
        result.slots, case,
    )
    _evaluate(result, case)
    return result


def _analyze_result(result: ModelTestResult, case: dict):
    """Common analysis for raw LLM results."""
    data = result.parsed_json
    if not data:
        result.error = "Failed to parse JSON from response"
        return

    result.slots = data.get("slots", {})
    result.questions = data.get("questions", [])
    result.question_count = len(result.questions)

    qty = result.slots.get("quantity")
    if qty is not None:
        result.has_quantity_slot = True
        result.quantity_value = qty

    level_pats = [r"level", r"标高", r"楼层", r"floor"]
    result.asks_about_type = _check_topic(result.questions, case["type_topic_patterns"])
    result.asks_about_level = _check_topic(result.questions, level_pats)
    result.asks_about_positions = _check_topic(result.questions, case["position_topic_patterns"])
    result.positions_ask_count = _count_positions_asked(result.questions, case)
    result.has_forbidden_defaults, result.forbidden_details = _check_forbidden_defaults(
        result.slots, case,
    )
    _evaluate(result, case)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

_all_results: dict[str, list[ModelTestResult]] = {}   # key: "input|level"


def _generate_report(results: list[ModelTestResult], test_input: str, test_level: str):
    """Generate markdown comparison report."""
    safe_name = re.sub(r"[^\w]", "_", test_input)
    report_path = Path(__file__).parent / f"report_{safe_name}_{test_level}.md"
    case = TEST_CASES[test_input]
    expected_qty = case["expected_quantity"]

    lines = [
        f"# Model Comparison Report -- \"{test_input}\" ({test_level})",
        "",
        f"**Test input**: `{test_input}`",
        f"**Expected quantity**: {expected_qty}",
        f"**Test level**: {test_level}",
        "",
        "## Score Summary (0-5)",
        "",
        "| Model | Score | Quantity | Ask Type | Ask Level | Ask Position | No Defaults | Duration |",
        "|-------|-------|----------|----------|-----------|-------------|-------------|----------|",
    ]

    for r in results:
        def _icon(b): return "[PASS]" if b else "[FAIL]"
        qty_ok = r.has_quantity_slot and r.quantity_value == expected_qty
        lines.append(
            f"| {r.model_name} | **{r.score}/5** | "
            f"{_icon(qty_ok)} | {_icon(r.asks_about_type)} | {_icon(r.asks_about_level)} | "
            f"{_icon(r.asks_about_positions)} | {_icon(not r.has_forbidden_defaults)} | "
            f"{r.duration_ms:.0f}ms |"
        )

    lines.append("")

    for r in results:
        lines.append("---")
        lines.append(f"## {r.model_name}")
        lines.append(f"**Score**: {r.score}/5 | **Duration**: {r.duration_ms:.0f}ms")
        if r.error:
            lines.append(f"**Error**: {r.error}")
            lines.append("")
            continue

        lines.append("")
        lines.append("### Slots extracted")
        lines.append(f"```json\n{json.dumps(r.slots, ensure_ascii=False, indent=2)}\n```")

        lines.append(f"### Questions ({r.question_count} total)")
        for i, q in enumerate(r.questions, 1):
            lines.append(f"**Q{i}** [{q.get('slot', '?')}]: {q.get('text', '?')}")
            opts = q.get("options", [])
            if opts:
                lines.append(f"  Options: {opts}")
        lines.append("")

        if r.asks_about_positions:
            lines.append(f"**Positions asked for**: {r.positions_ask_count} (expected: {expected_qty})")
        else:
            lines.append("**Positions asked for**: [FAIL] NOT ASKED")

        if r.forbidden_details:
            lines.append("### Forbidden defaults detected")
            for d in r.forbidden_details:
                lines.append(f"- [FAIL] {d}")

        lines.append("")

        if r.parsed_json:
            lines.append("<details><summary>Raw LLM JSON</summary>")
            lines.append("")
            lines.append(f"```json\n{json.dumps(r.parsed_json, ensure_ascii=False, indent=2)}\n```")
            lines.append("</details>")
            lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[REPORT] {report_path}")


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

def _assert_raw(result: ModelTestResult, case: dict):
    """Common assertions for raw LLM tests."""
    expected_qty = case["expected_quantity"]
    assert not result.error, f"[{result.model_name}] LLM call failed: {result.error}"
    assert result.parsed_json, f"[{result.model_name}] Failed to parse JSON"

    assert result.has_quantity_slot, (
        f"[{result.model_name}] Did not extract quantity. Slots: {result.slots}"
    )
    assert result.quantity_value == expected_qty, (
        f"[{result.model_name}] quantity={result.quantity_value}, expected {expected_qty}"
    )
    assert result.question_count >= 2, (
        f"[{result.model_name}] Only {result.question_count} questions. "
        f"Must ask about type, level, positions at minimum."
    )
    assert result.asks_about_type, (
        f"[{result.model_name}] Did not ask about element type. "
        f"Questions: {[q.get('slot') for q in result.questions]}"
    )
    assert result.asks_about_positions, (
        f"[{result.model_name}] Did not ask about positions/coordinates. "
        f"Questions: {[q.get('slot') for q in result.questions]}"
    )
    assert not result.has_forbidden_defaults, (
        f"[{result.model_name}] Silently defaulted parameters: {result.forbidden_details}"
    )


def _assert_orch(result: ModelTestResult, case: dict):
    """Common assertions for orchestrator tests."""
    expected_qty = case["expected_quantity"]
    assert not result.error, f"[{result.model_name}] Pipeline failed: {result.error}"

    assert result.question_count >= 2, (
        f"[{result.model_name}] Only {result.question_count} questions via orchestrator."
    )
    assert result.has_quantity_slot, (
        f"[{result.model_name}] quantity not extracted. Slots: {result.slots}"
    )
    assert result.asks_about_type, (
        f"[{result.model_name}] No type question. "
        f"Qs: {[q.get('slot') for q in result.questions]}"
    )
    assert result.asks_about_positions, (
        f"[{result.model_name}] No position question. "
        f"Qs: {[q.get('slot') for q in result.questions]}"
    )
    assert not result.has_forbidden_defaults, (
        f"[{result.model_name}] Forbidden defaults: {result.forbidden_details}"
    )


# ===================================================================
# TEST CLASS: 创建两个结构柱
# ===================================================================

class TestRaw_两个结构柱:
    CASE = TEST_CASES["创建两个结构柱"]

    @pytest.mark.asyncio
    async def test_gemini(self):
        r = await _test_raw("gemini", MODELS["gemini"], self.CASE)
        _all_results.setdefault("创建两个结构柱|raw", []).append(r)
        _assert_raw(r, self.CASE)

    @pytest.mark.asyncio
    async def test_codex(self):
        r = await _test_raw("codex", MODELS["codex"], self.CASE)
        _all_results.setdefault("创建两个结构柱|raw", []).append(r)
        _assert_raw(r, self.CASE)

    @pytest.mark.asyncio
    async def test_claude(self):
        r = await _test_raw("claude", MODELS["claude"], self.CASE)
        _all_results.setdefault("创建两个结构柱|raw", []).append(r)
        _assert_raw(r, self.CASE)


class TestOrch_两个结构柱:
    CASE = TEST_CASES["创建两个结构柱"]

    @pytest.mark.asyncio
    async def test_gemini(self):
        r = await _test_orchestrator("gemini", MODELS["gemini"], self.CASE)
        _all_results.setdefault("创建两个结构柱|orch", []).append(r)
        _assert_orch(r, self.CASE)

    @pytest.mark.asyncio
    async def test_codex(self):
        r = await _test_orchestrator("codex", MODELS["codex"], self.CASE)
        _all_results.setdefault("创建两个结构柱|orch", []).append(r)
        _assert_orch(r, self.CASE)

    @pytest.mark.asyncio
    async def test_claude(self):
        r = await _test_orchestrator("claude", MODELS["claude"], self.CASE)
        _all_results.setdefault("创建两个结构柱|orch", []).append(r)
        _assert_orch(r, self.CASE)


# ===================================================================
# TEST CLASS: 创建三面墙
# ===================================================================

class TestRaw_三面墙:
    CASE = TEST_CASES["创建三面墙"]

    @pytest.mark.asyncio
    async def test_gemini(self):
        r = await _test_raw("gemini", MODELS["gemini"], self.CASE)
        _all_results.setdefault("创建三面墙|raw", []).append(r)
        _assert_raw(r, self.CASE)

    @pytest.mark.asyncio
    async def test_codex(self):
        r = await _test_raw("codex", MODELS["codex"], self.CASE)
        _all_results.setdefault("创建三面墙|raw", []).append(r)
        _assert_raw(r, self.CASE)

    @pytest.mark.asyncio
    async def test_claude(self):
        r = await _test_raw("claude", MODELS["claude"], self.CASE)
        _all_results.setdefault("创建三面墙|raw", []).append(r)
        _assert_raw(r, self.CASE)


class TestOrch_三面墙:
    CASE = TEST_CASES["创建三面墙"]

    @pytest.mark.asyncio
    async def test_gemini(self):
        r = await _test_orchestrator("gemini", MODELS["gemini"], self.CASE)
        _all_results.setdefault("创建三面墙|orch", []).append(r)
        _assert_orch(r, self.CASE)

    @pytest.mark.asyncio
    async def test_codex(self):
        r = await _test_orchestrator("codex", MODELS["codex"], self.CASE)
        _all_results.setdefault("创建三面墙|orch", []).append(r)
        _assert_orch(r, self.CASE)

    @pytest.mark.asyncio
    async def test_claude(self):
        r = await _test_orchestrator("claude", MODELS["claude"], self.CASE)
        _all_results.setdefault("创建三面墙|orch", []).append(r)
        _assert_orch(r, self.CASE)


# ---------------------------------------------------------------------------
# Report generation (after all tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def generate_comparison_report(request):
    yield
    for key, results in _all_results.items():
        if not results:
            continue
        test_input, level = key.split("|", 1)
        _generate_report(results, test_input, level)
