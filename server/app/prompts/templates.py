"""
Prompt 模板 — 代码生成的 system prompt（简洁模式 / 完整模式）
"""

from prompts import load_prompt

SYSTEM_BRIEF = load_prompt("server.rag_system_brief.md")
SYSTEM_FULL = load_prompt("server.rag_system_full.md")


def get_system_prompt(
    show_full: bool,
    api_context: str,
    code_context: str,
    revit_version: str = "2026",
) -> str:
    template = SYSTEM_FULL if show_full else SYSTEM_BRIEF
    return template.format(
        revit_version=revit_version,
        api_context=api_context or "(No API documentation retrieved)",
        code_context=code_context or "(No SDK code examples retrieved)",
    )
