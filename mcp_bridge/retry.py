"""
Retry logic for compile errors — feeds Revit compile errors back to the LLM
so it can fix and regenerate the code.
"""
from __future__ import annotations

import logging

from prompts import load_prompt

log = logging.getLogger(__name__)

RETRY_PROMPT = load_prompt("mcp_bridge.retry_user.md")
RETRY_SYSTEM = load_prompt("mcp_bridge.retry_system.md")


def is_compile_error(error: str | None) -> bool:
    """Check if an error string looks like a compile/syntax error."""
    if not error:
        return False
    indicators = ["编译", "compile", "CS0", "CS1", "error CS", "Line ", "line "]
    return any(ind in error for ind in indicators)


def fix_compile_error(generator, user_query: str, error_msg: str, code: str) -> str:
    """
    Ask the LLM to fix a single compile error.

    Args:
        generator: CodeGenerator instance (needs .llm and ._extract_code).
        user_query: The original user query.
        error_msg: The compile error from Revit.
        code: The broken code.

    Returns:
        The corrected code string.
    """
    prompt = RETRY_PROMPT.format(
        user_query=user_query,
        code=code,
        error_msg=error_msg,
    )
    raw = generator.llm.generate_text(prompt, system_prompt=RETRY_SYSTEM)
    return generator._extract_code(raw)


async def retry_on_compile_error(
    generator,
    revit_client,
    user_query: str,
    error_msg: str,
    code: str,
    max_retries: int = 2,
) -> tuple[str, bool, str | None, str | None, list[dict]]:
    """
    Retry code generation when Revit returns a compile error.

    Loops up to max_retries times: fix code via LLM, re-execute on Revit,
    check if compile error persists.

    Args:
        generator: CodeGenerator instance.
        revit_client: Revit TCP client with send_code().
        user_query: The original user query.
        error_msg: The compile error message from Revit.
        code: The broken code that failed.
        max_retries: Maximum retry attempts.

    Returns:
        (final_code, success, result, error, attempts_log)
    """
    attempts_log: list[dict] = []
    current_code = code
    current_error = error_msg

    for attempt in range(1, max_retries + 1):
        log.info("Retry attempt %d/%d for compile error", attempt, max_retries)

        # Log the failed attempt
        attempts_log.append({
            "attempt": attempt,
            "error": current_error,
            "code": current_code,
        })

        # Ask LLM to fix
        current_code = fix_compile_error(generator, user_query, current_error, current_code)
        log.info("Retry %d: LLM produced new code (%d chars)", attempt, len(current_code))

        # Re-execute on Revit
        resp = await revit_client.send_code(current_code)

        if resp.success or not is_compile_error(resp.error):
            # Fixed (or a different kind of error — not a compile issue)
            return current_code, resp.success, resp.result, resp.error, attempts_log

        # Still a compile error — loop again
        current_error = resp.error

    # Exhausted retries, return last state
    attempts_log.append({
        "attempt": max_retries + 1,
        "error": current_error,
        "code": current_code,
    })
    return current_code, False, None, current_error, attempts_log
