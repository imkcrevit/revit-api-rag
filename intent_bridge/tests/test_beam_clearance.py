"""
Test: "获得当前视图的结构梁，获得梁底与链接模型中楼板的净高"
Tests the full prompt pipeline including RAG, skill matching, and LLM output.
"""
import asyncio
import json
import time
import logging
import sys
from pathlib import Path

# Ensure project root on path
_root = str(Path(__file__).resolve().parent.parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from intent_bridge.slot_engine import (
    ConversationOrchestrator,
    _extract_search_terms,
    _query_api_by_method,
    _format_api_context,
    _score_rag_quality,
    get_schema_registry,
)
from intent_bridge.llm_adapter import LLMAdapter
from intent_bridge.models import SessionState
from intent_bridge.skill_loader import get_skill_loader

# Enable logging to see the full chain
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("test_beam_clearance")


USER_INPUT = "找到当前视图所有的结构梁 并且计算梁底与链接模型制定楼板的净高，最终将参数添加到备注标记中，创建视图着色"


async def main():
    print("=" * 80)
    print(f"TEST INPUT: {USER_INPUT}")
    print("=" * 80)

    registry = get_schema_registry()

    # ── Step 1: Search term extraction ────────────────────────────────
    print("\n── STEP 1: Search Term Extraction ──")
    search_terms = _extract_search_terms(USER_INPUT, registry)
    print(f"  Extracted terms: {search_terms}")

    # ── Step 2: RAG lookup (with progressive expansion) ────────────
    print("\n── STEP 2: RAG API Doc Lookup (Progressive) ──")
    t0 = time.time()
    # Round 1: initial query
    api_docs = _query_api_by_method(search_terms, limit=8)
    score, reasons = _score_rag_quality(api_docs, search_terms)
    print(f"  Round 1: {len(api_docs)} docs, score={score}, reasons={reasons}")
    for doc in api_docs:
        name = doc.get("name", "?")
        summary = (doc.get("summary", "") or "")[:80]
        print(f"    - {name}: {summary}")

    # Show expansion would trigger
    if score < 0.4:
        print(f"  ⚠ Score {score} < 0.4 threshold → expansion will trigger in orchestrator")

    rag_context = _format_api_context(api_docs)
    print(f"\n  RAG context length: {len(rag_context)} chars")
    if rag_context and rag_context != "## Retrieved API Documentation\n(No relevant API documentation found for the given search terms.)":
        # Show first 500 chars
        print(f"  RAG context preview:\n{rag_context[:500]}...")

    # ── Step 3: Skill matching ────────────────────────────────────────
    print("\n── STEP 3: Skill Matching ──")
    loader = get_skill_loader()
    matched_skills = loader.match_skills(USER_INPUT, search_terms)
    if matched_skills:
        for s in matched_skills:
            print(f"  Matched: {s.name} (type: {getattr(s, 'skill_type', '?')})")
    else:
        print("  No skills matched")

    # ── Step 4: Full prompt (what LLM sees) ───────────────────────────
    print("\n── STEP 4: Full Prompt Construction ──")
    base_rules = loader.get_base_rules()
    skill_context = loader.render_skill_prompt(matched_skills) if matched_skills else "(No skills matched)"

    from intent_bridge.slot_engine import _ANALYZE_PROMPT_V2
    full_prompt = _ANALYZE_PROMPT_V2.format(
        base_skill_rules=base_rules,
        intent_list=registry.get_intent_summary(),
        rag_context=rag_context,
        skill_context=skill_context,
        user_input=USER_INPUT,
    )
    print(f"  Full prompt length: {len(full_prompt)} chars")
    # Save full prompt to file for inspection
    prompt_path = Path(__file__).parent / "last_prompt.txt"
    prompt_path.write_text(full_prompt, encoding="utf-8")
    print(f"  Full prompt saved to: {prompt_path}")

    # ── Step 5: LLM call ─────────────────────────────────────────────
    print("\n── STEP 5: LLM Call ──")
    llm = LLMAdapter()
    t0 = time.time()
    raw_output = await llm.complete_async(full_prompt, temperature=0.1)
    llm_duration = (time.time() - t0) * 1000
    print(f"  LLM response time: {llm_duration:.0f}ms")
    print(f"  Raw output length: {len(raw_output)} chars")

    # Save raw output
    raw_path = Path(__file__).parent / "last_raw_output.txt"
    raw_path.write_text(raw_output, encoding="utf-8")
    print(f"  Raw output saved to: {raw_path}")

    # Show raw output
    print(f"\n  ── RAW LLM OUTPUT ──")
    print(raw_output)
    print(f"  ── END RAW OUTPUT ──")

    # ── Step 6: JSON extraction ───────────────────────────────────────
    print("\n── STEP 6: JSON Extraction ──")
    try:
        result = LLMAdapter.extract_json(raw_output)
        print(f"  Parsed JSON:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"  JSON extraction FAILED: {e}")
        result = {}

    # ── Step 7: Full orchestrator run ─────────────────────────────────
    print("\n── STEP 7: Full Orchestrator Run ──")
    orch = ConversationOrchestrator(llm=llm)
    session = SessionState()
    t0 = time.time()
    response = await orch.process_turn(USER_INPUT, session)
    total_duration = (time.time() - t0) * 1000
    print(f"  Total duration: {total_duration:.0f}ms")
    print(f"  Status: {response.status.value}")
    print(f"  Intent: {response.intent}")
    print(f"  Slots: {json.dumps(response.slots, ensure_ascii=False, indent=2)}")
    print(f"  Questions remaining: {response.questions_remaining}")
    if response.current_question:
        q = response.current_question
        print(f"  Current question:")
        print(f"    slot: {q.slot}")
        print(f"    text: {q.text}")
        print(f"    options: {q.options}")
        print(f"    enrich: {q.enrich}")
    print(f"  Summary: {response.summary}")

    # Print all pending questions
    if session.pending_questions:
        print(f"\n  All pending questions ({len(session.pending_questions)}):")
        for i, q in enumerate(session.pending_questions):
            print(f"    [{i}] slot={q.slot}")
            print(f"        text={q.text}")
            print(f"        options={q.options}")
            print(f"        enrich={q.enrich}")

    print("\n" + "=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
