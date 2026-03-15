"""Unit test for SSE thinking extraction + streaming display logic."""
import re
import json


# ── Reproduce _parse_sse_stream from app.py ──

def parse_sse_stream(lines: list[str]):
    """Reproduce _parse_sse_stream logic exactly from app.py."""
    thinking_buf = ""
    code_buf = ""
    full_buf = ""

    current_event = None
    for line in lines:
        if not line:
            continue
        if line.startswith("event: "):
            current_event = line[7:]
            continue
        if not line.startswith("data: "):
            continue
        data_str = line[6:]

        if current_event == "progress":
            try:
                yield "progress", json.loads(data_str), None
            except (json.JSONDecodeError, TypeError):
                pass
        elif current_event == "token":
            try:
                token = json.loads(data_str)
                full_buf += token

                # Separate thinking from code
                thinking_match = re.search(
                    r'<thinking>(.*?)(?:</thinking>|$)', full_buf, re.DOTALL
                )
                if thinking_match:
                    thinking_buf = thinking_match.group(1).strip()

                # Extract code portion (after </thinking>)
                after_thinking = re.sub(
                    r'<thinking>.*?</thinking>', '', full_buf, flags=re.DOTALL
                )
                code_match = re.search(
                    r'```(?:csharp|cs)?\s*\n(.*?)(?:```|$)',
                    after_thinking, re.DOTALL,
                )
                if code_match:
                    code_buf = code_match.group(1).strip()

                yield "token", thinking_buf, code_buf
            except (json.JSONDecodeError, TypeError):
                pass
        elif current_event == "done":
            try:
                yield "done", json.loads(data_str), None
            except (json.JSONDecodeError, TypeError):
                pass


# ── Reproduce on_generate token-handling logic from app.py ──

def simulate_on_generate_yields(sse_lines: list[str]) -> list[dict]:
    """Simulate on_generate's streaming loop, return what it would yield.

    Matches the fixed app.py: thinking_display is always-visible Markdown,
    content is set to "" when idle and markdown text when streaming.
    """
    final_code = ""
    final_thinking = ""
    token_count = 0
    pipeline_log = []
    yields = []

    for event_type, data1, data2 in parse_sse_stream(sse_lines):
        if event_type == "progress":
            msg = data1
            pipeline_log.append(msg)
            # During progress phase, thinking is empty string (hidden by CSS)
            thinking_md = (f"**Thinking:**\n\n{final_thinking}"
                           if final_thinking else "")
            yields.append({
                "type": "progress",
                "thinking_md": thinking_md,
                "thinking_raw": final_thinking,
                "code": final_code,
                "pipeline_log": list(pipeline_log),
            })

        elif event_type == "token":
            final_thinking = data1
            final_code = data2
            token_count += 1
            # Skip throttle for test — always yield
            thinking_md = (f"**Thinking:**\n\n{final_thinking}"
                           if final_thinking
                           else "*Waiting for LLM response...*")
            yields.append({
                "type": "token",
                "token_count": token_count,
                "thinking_md": thinking_md,
                "thinking_raw": final_thinking,
                "code": final_code,
            })

        elif event_type == "done":
            yields.append({"type": "done", "data": data1})

    return yields


def build_sse_lines(tokens: list[str], progress_msgs: list[str] = None) -> list[str]:
    """Build SSE lines from token list."""
    lines = []
    for msg in (progress_msgs or []):
        lines.append(f"event: progress")
        lines.append(f"data: {json.dumps(msg)}")
    for token in tokens:
        lines.append("event: token")
        lines.append(f"data: {json.dumps(token)}")
    lines.append("event: done")
    lines.append(f'data: {json.dumps({"code": "final", "safe": True})}')
    return lines


# ── Tests ──

def test_thinking_regex_partial():
    """Verify regex captures thinking even without closing tag."""
    buf = "<thinking>\nStep 1: find family type\nStep 2: place column"
    m = re.search(r'<thinking>(.*?)(?:</thinking>|$)', buf, re.DOTALL)
    result = m.group(1).strip() if m else ""
    print(f"Partial thinking: {result!r}")
    assert "find family type" in result, f"Should capture partial thinking, got: {result!r}"
    print("PASS: Regex captures partial thinking")


def test_thinking_regex_complete():
    """Verify regex captures thinking with closing tag."""
    buf = "<thinking>\nMy plan:\n1. Do X\n</thinking>\n```csharp\ncode here\n```"
    m = re.search(r'<thinking>(.*?)(?:</thinking>|$)', buf, re.DOTALL)
    result = m.group(1).strip() if m else ""
    print(f"Complete thinking: {result!r}")
    assert "My plan" in result
    assert "</thinking>" not in result
    print("PASS: Regex captures complete thinking")


def test_streaming_token_by_token():
    """Simulate realistic token-by-token streaming and verify thinking shows progressively."""
    tokens = [
        "<",
        "thinking",
        ">\n",
        "I need to create a structural column.\n",
        "Step 1: Find the FamilySymbol for the selected type.\n",
        "Step 2: Activate the symbol.\n",
        "Step 3: Place using NewFamilyInstance.\n",
        "</",
        "thinking",
        ">\n\n",
        "```csharp\n",
        "// Step 1: Find the family symbol\n",
        "var symbol = new FilteredElementCollector(document)\n",
        "    .OfClass(typeof(FamilySymbol))\n",
        "    .Cast<FamilySymbol>()\n",
        '    .FirstOrDefault(s => s.Name == "UC305");\n',
        "\n",
        "symbol.Activate();\n",
        "return new { Status = \"Created\" };\n",
        "```",
    ]

    progress = ["Query Rewrite done", "Vector Search done", "LLM generating..."]
    sse_lines = build_sse_lines(tokens, progress)
    yields = simulate_on_generate_yields(sse_lines)

    print(f"\nTotal yields: {len(yields)}")
    print(f"  progress: {sum(1 for y in yields if y['type'] == 'progress')}")
    print(f"  token:    {sum(1 for y in yields if y['type'] == 'token')}")
    print(f"  done:     {sum(1 for y in yields if y['type'] == 'done')}")

    # Track when thinking first becomes non-empty
    first_thinking_idx = None
    first_code_idx = None
    for i, y in enumerate(yields):
        if y["type"] == "token":
            if first_thinking_idx is None and y["thinking_raw"]:
                first_thinking_idx = i
                print(f"\n  First thinking at yield #{i}: {y['thinking_raw'][:80]!r}...")
            if first_code_idx is None and y["code"]:
                first_code_idx = i
                print(f"  First code at yield #{i}: {y['code'][:80]!r}...")

    token_yields = [y for y in yields if y["type"] == "token"]

    # Check: thinking text should grow progressively
    thinking_lengths = [len(y["thinking_raw"]) for y in token_yields]
    print(f"  Thinking lengths: {thinking_lengths}")

    # KEY ASSERTION: thinking should appear BEFORE code
    assert first_thinking_idx is not None, "Thinking should appear at some point"
    if first_code_idx is not None:
        assert first_thinking_idx < first_code_idx, \
            f"Thinking (#{first_thinking_idx}) should appear before code (#{first_code_idx})"

    # KEY ASSERTION: thinking should grow over time (streaming)
    non_empty = [l for l in thinking_lengths if l > 0]
    assert len(non_empty) >= 3, \
        f"Thinking should grow over multiple yields, got {len(non_empty)} non-empty"
    # Thinking may dip slightly when </thinking> tag partially arrives — OK
    max_len = max(non_empty)
    assert max_len > 50, f"Thinking should have substantial content, max={max_len}"

    print("\nPASS: Thinking streams progressively")


def test_no_thinking_tag():
    """Test LLM response without <thinking> tags — should show 'Waiting...'."""
    tokens = [
        "```csharp\n",
        "var x = 1;\n",
        "return x;\n",
        "```",
    ]
    sse_lines = build_sse_lines(tokens)
    yields = simulate_on_generate_yields(sse_lines)

    token_yields = [y for y in yields if y["type"] == "token"]
    for y in token_yields:
        if not y["thinking_raw"]:
            assert "Waiting" in y["thinking_md"], \
                f"Should show 'Waiting...' when no thinking, got: {y['thinking_md']!r}"

    print("\nPASS: No-thinking case shows 'Waiting...'")


def test_thinking_empty_during_progress():
    """During progress events, thinking_md should be empty (no tokens arrived yet)."""
    sse_lines = [
        "event: progress",
        f"data: {json.dumps('Query Rewrite...')}",
        "event: progress",
        f"data: {json.dumps('Vector Search...')}",
        "event: token",
        f"data: {json.dumps('<thinking>')}",
        "event: token",
        f"data: {json.dumps(chr(10) + 'Planning...')}",
        "event: done",
        f'data: {json.dumps({"code": "", "safe": True})}',
    ]
    yields = simulate_on_generate_yields(sse_lines)

    progress_yields = [y for y in yields if y["type"] == "progress"]
    for y in progress_yields:
        assert y["thinking_md"] == "", \
            f"Thinking should be empty during progress, got: {y['thinking_md']!r}"

    token_yields = [y for y in yields if y["type"] == "token"]
    assert any(y["thinking_raw"] for y in token_yields), \
        "Thinking should have content during token phase"

    print("\nPASS: Thinking content correct across phases")


if __name__ == "__main__":
    print("=" * 60)
    test_thinking_regex_partial()
    print()
    test_thinking_regex_complete()
    print()
    test_streaming_token_by_token()
    print()
    test_no_thinking_tag()
    print()
    test_thinking_empty_during_progress()
    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")
