"""
Prompt templates for the API Explorer module.

Handles:
- API code generation from selected API members
- Query understanding with entity/action prioritization
"""

from prompts import load_prompt

SYSTEM_API_CODEGEN = load_prompt("server.api_explorer_codegen.md")
QUERY_UNDERSTANDING_PROMPT = load_prompt("server.api_explorer_query_understanding.md")


def get_api_codegen_prompt(
    api_context: str,
    revit_version: str = "2026",
) -> str:
    """Build system prompt for API Explorer code generation."""
    return SYSTEM_API_CODEGEN.format(
        revit_version=revit_version,
        api_context=api_context or "(No API reference provided)",
    )
