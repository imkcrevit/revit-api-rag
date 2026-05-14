"""
Code Generator — RAG-informed C# code generation for Revit execution.

Uses the existing RAG pipeline to generate code suitable for send_code_to_revit.
Code runs inside a Roslyn-compiled static method:
    public static object Execute(Document document, object[] parameters)

CRITICAL: The plugin's ExecuteCodeEventHandler already wraps user code in a
Transaction. Generated code must NOT create its own Transaction.
"""
from __future__ import annotations

import json
import logging
import re

from pipeline.retriever import RAGRetriever
from pipeline.llm_client import LLMClient, create_llm_client

_log = logging.getLogger("mcp_bridge.code_generator")


SYSTEM_EXECUTE = """\
You are a Revit {revit_version} API expert. Generate C# code that will be \
dynamically compiled (Roslyn) and executed inside a Revit plugin.

## Execution Context
Your code is inserted into this static method body — write ONLY the body:
```csharp
using System;
using System.Linq;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using System.Collections.Generic;

namespace AIGeneratedCode
{{
    public static class CodeExecutor
    {{
        public static object Execute(Document document, object[] parameters)
        {{
            // === YOUR CODE HERE ===
        }}
    }}
}}
```

## Available Variables
- `document` — the active Revit Document (NOT `doc`, NOT `uidoc`)
- `parameters` — object[] from caller (may be empty)
- The method MUST return an object (return null if no meaningful result)

## Auto-injected usings (do NOT repeat):
System, System.Linq, System.Collections.Generic, Autodesk.Revit.DB, Autodesk.Revit.UI

## Rules
1. Use only Revit {revit_version} API — no invented classes or methods.
2. Output ONLY the method body. No class, no namespace, no using statements.
3. **DO NOT create a Transaction** — the plugin already wraps your code in one.
   Writing `new Transaction(...)` will cause a nested transaction error.
4. Use variable `document` directly. Do NOT declare `doc`, `uidoc`, or `uiapp`.
   If you need UIDocument: `new UIDocument(document)`
5. For sub-namespaces NOT in auto-injected usings, use fully qualified names:
   - Structure: `Autodesk.Revit.DB.Structure.StructuralType.Column`
   - Architecture: `Autodesk.Revit.DB.Architecture.Room`, `.RoomTag`, `.TopographySurface`
   - Mechanical/Electrical/Plumbing: use fully qualified names
   NEVER write bare `Room` or `RoomTag` — always prefix with `Autodesk.Revit.DB.Architecture.`
6. All coordinates in Revit internal units (feet).
{unit_context}
7. Return a meaningful result:
   `return new {{ ElementId = element.Id.Value, Status = "Created" }};`
8. Structure code with numbered step comments:
   `// Step 1: [purpose] — [which API and why]`
9. Common pitfalls:
   - FamilySymbol must call Activate() before placing instances
   - FilteredElementCollector needs OfClass() or OfCategory()
   - Do NOT use `using` statements for Revit objects
   - Revit 2024+: use `ElementId.Value` (long), NOT `ElementId.IntegerValue` (removed)
   - `new ElementId(12345)` — plain integer, do NOT add `L` suffix (e.g. `12345L` is wrong)
10. If the code needs user-supplied values, use placeholders: `{{{{param_name}}}}`.
11. ALWAYS start your response with a <thinking> block that explains your plan:
    - Break down the task into numbered sub-tasks
    - List which Revit API classes/methods you will use for each step
    - Note any potential pitfalls or design decisions
    Then write the code in a ```csharp block.

## API Grounding Rules (CRITICAL — anti-hallucination):
1. ONLY use classes, methods, and properties that appear in the API documentation below
   or in the SDK code examples. If a method is not documented, do NOT use it.
2. Report outcomes faithfully: if you cannot generate correct code for the request because
   the API documentation is insufficient, say so explicitly rather than inventing API calls.
   Never fabricate class names, method signatures, or property names.
3. Before using any API member, verify against the documentation that:
   - The class exists in the documented namespace
   - The method signature matches (parameter types, count, return type)
   - Required enum values are valid members of their enum type
4. NEVER assume a parameter value when the user has not provided it.
   Use placeholders `{{{{param_name}}}}` for ALL user-supplied values.
   Guessing coordinates, ElementIds, or type names will cause silent failures.
5. If the retrieved documentation shows deprecated or version-specific APIs,
   prefer the Revit {revit_version} compatible version.
{selections_context}
{skills_context}
## Retrieved API Documentation
{api_context}

## Retrieved SDK Code Examples
{code_context}
"""


class CodeGenerator:
    """Generate Revit-executable C# code using RAG context."""

    # Unit conversion context templates
    UNIT_CONTEXTS = {
        "mm": "   User input is in millimeters (mm). Convert: `value_mm / 304.8` to get feet.",
        "m": "   User input is in meters (m). Convert: `value_m / 0.3048` to get feet.",
        "feet": "   User input is already in feet (Revit internal units). No conversion needed.",
    }

    def __init__(self, retriever: RAGRetriever, llm_client: LLMClient,
                 revit_version: str = "2026", user_unit: str = "mm",
                 config: dict | None = None):
        self.retriever = retriever
        self.llm = llm_client
        self.revit_version = revit_version
        self.user_unit = user_unit
        self._config = config

    @staticmethod
    def _get_all_skills() -> str:
        """Load all active skills content for code generation modules."""
        try:
            from server.app.skill_store import get_skill_store
            store = get_skill_store()
            return store.get_active_prompt("mcp_bridge")
        except Exception:
            return ""

    def _extract_relevant_skills(self, user_query: str, all_skills: str) -> str:
        """Use Gemini Flash to extract only the relevant skills for the user query."""
        if not all_skills.strip():
            return ""
        if not self._config:
            return all_skills

        try:
            skill_llm = create_llm_client(self._config, provider_override="gemini_flash")
        except Exception:
            _log.info("[skills] Gemini Flash not available, using full skills")
            return all_skills

        system = (
            "You are a BIM standards extraction agent. Given a user's Revit modeling request "
            "and a collection of BIM standards documents, extract ONLY the specific rules, "
            "constraints, tables, and requirements that are DIRECTLY relevant to the user's request.\n\n"
            "Rules:\n"
            "- Keep the original Markdown formatting (tables, headers, lists)\n"
            "- Include dimension constraints, naming rules, area requirements, API usage patterns\n"
            "- Include the section title for context\n"
            "- If a table has relevant rows, include only the relevant rows with the header row\n"
            "- If no rules are relevant, output exactly: NONE\n"
            "- Do NOT add commentary — output raw rules only"
        )
        prompt = f"User request: {user_query}\n\n---\n\nBIM Standards:\n{all_skills}"

        try:
            result = skill_llm.generate_text(prompt, system_prompt=system)
            extracted = result.strip()
            if not extracted or extracted == "NONE":
                return ""
            _log.info(f"[skills] Gemini Flash extracted {len(extracted)} chars from {len(all_skills)} chars")
            return extracted
        except Exception as e:
            _log.warning(f"[skills] Gemini Flash extraction failed: {e}, using full skills")
            return all_skills

    def _build_skills_context(self, user_query: str = "") -> str:
        """Get relevant BIM standards/skills for the given user query."""
        all_skills = self._get_all_skills()
        if not all_skills or not all_skills.strip():
            return ""

        if user_query and self._config:
            relevant = self._extract_relevant_skills(user_query, all_skills)
        else:
            relevant = all_skills

        if not relevant or not relevant.strip():
            return ""

        return (
            "\n## Active BIM Standards & Constraints (MUST follow)\n"
            "The following standards are active and relevant to this request. "
            "Your generated code MUST comply with dimension constraints, naming rules, "
            "area requirements, and API usage patterns defined below.\n\n"
            + relevant + "\n"
        )

    def generate(self, user_query: str, api_top_k: int = 15,
                 code_top_k: int = 5,
                 selections: dict | None = None) -> tuple[str, dict]:
        """
        Generate C# code from user query using RAG.

        Args:
            selections: User selections from interactive workflow, e.g.
                {"family_type": "UC305x305x97", "level": "Level 1", ...}

        Returns:
            (code, context) where context contains RAG search details.
        """
        results = self.retriever.search(
            user_query, api_top_k=api_top_k, code_top_k=code_top_k
        )
        ctx = self.retriever.build_context(results)

        skills_ctx = self._build_skills_context(user_query)
        system = SYSTEM_EXECUTE.format(
            revit_version=self.revit_version,
            api_context=ctx.get("api_context", "(none)"),
            code_context=ctx.get("code_context", "(none)"),
            selections_context=self._build_selections_context(selections),
            unit_context=self.UNIT_CONTEXTS.get(self.user_unit, self.UNIT_CONTEXTS["mm"]),
            skills_context=skills_ctx,
        )

        raw = self.llm.generate_text(user_query, system_prompt=system)
        _log.info(f"[generate] raw LLM response length={len(raw)}, first 200 chars: {raw[:200]!r}")
        _log.info(f"[generate] raw LLM response last 200 chars: {raw[-200:]!r}")
        code = self._extract_code(raw)

        return code, {
            "query": user_query,
            "rewritten_query": results.rewritten_query,
            "api_count": len(results.api_items),
            "sdk_count": len(results.sdk_items),
            "selections": selections,
        }

    def generate_stream(self, user_query: str, api_top_k: int = 15,
                        code_top_k: int = 5,
                        selections: dict | None = None):
        """Streaming version — yields tokens as they arrive."""
        results = self.retriever.search(
            user_query, api_top_k=api_top_k, code_top_k=code_top_k
        )
        ctx = self.retriever.build_context(results)

        skills_ctx = self._build_skills_context(user_query)
        system = SYSTEM_EXECUTE.format(
            revit_version=self.revit_version,
            api_context=ctx.get("api_context", "(none)"),
            code_context=ctx.get("code_context", "(none)"),
            selections_context=self._build_selections_context(selections),
            unit_context=self.UNIT_CONTEXTS.get(self.user_unit, self.UNIT_CONTEXTS["mm"]),
            skills_context=skills_ctx,
        )

        yield from self.llm.generate_stream(user_query, system_prompt=system)

    @staticmethod
    def _build_selections_context(selections: dict | None) -> str:
        """Build prompt section for user selections from interactive workflow."""
        if not selections:
            return ""
        lines = [
            "\n## User Selections — MANDATORY: use these exact values",
        ]
        # Merge orchestrator answers into effective selections for lookup
        orch = selections.get("_orchestrator_answers", {})

        if "family_type" in selections:
            ft = selections["family_type"]
            lines.append(f"- Family Type Name: \"{ft}\"")
            lines.append(f"  YOU MUST filter by name: `.FirstOrDefault(s => s.Name == \"{ft}\")`")
            lines.append(f"  NEVER use `.FirstOrDefault()` without a name filter")
        if "level" in selections:
            lv = selections["level"]
            lines.append(f"- Level Name: \"{lv}\"")
            lines.append(f"  YOU MUST filter: `.FirstOrDefault(l => l.Name == \"{lv}\")`")
        if "host_element_id" in selections:
            lines.append(f"- Host Element ID: {selections['host_element_id']}")
        if "position" in selections:
            pos = selections["position"]
            lines.append(f"- Position: ({pos.get('x', 0)}mm, {pos.get('y', 0)}mm)")

        # Include ALL orchestrator answers as explicit context for the LLM
        if orch:
            lines.append("")
            lines.append("## Orchestrator Answers — user-provided values, use exactly as given:")
            for slot, value in orch.items():
                lines.append(f"- {slot}: {value}")

        lines.append(
            "\nCRITICAL: Do NOT use `.First()` or `.FirstOrDefault()` without a name filter. "
            "Always filter by the exact name provided above."
            "\nNOTE: Type/family values may be in 'FamilyName: TypeName' format. "
            "Use FamilyName to filter `fs.FamilyName` and TypeName to filter `fs.Name`.\n"
        )
        return "\n".join(lines)

    # C# keywords and common code identifiers that are NOT user parameters
    _CS_NON_PARAMS = {
        # Anonymous object fields (return new { ... })
        "ElementId", "Status", "Created", "Error", "Message", "Count",
        "Category", "Name", "FamilyType", "Level", "FamilyName", "TypeName",
        "BaseLevel", "TopLevel", "InsertionPoint", "OldHeightMm", "NewHeightMm",
        "ElevationMm", "Id", "FloorType", "Result",
        # String interpolation fragments
        "F3", "F1", "F2", "0",
    }

    @staticmethod
    def extract_parameters(code: str) -> list[dict]:
        """Extract {param_name} placeholders from code template.

        Filters out C# anonymous object fields (new { Status = ... })
        and string interpolation ({value:F3}).
        """
        # First strip C# anonymous objects: new { Key = value, ... }
        # and string interpolation: $"...{expr}..." or $"...{expr:format}..."
        cleaned = re.sub(r'new\s*\{[^}]*\}', '', code)       # remove anonymous objects
        cleaned = re.sub(r'\$"[^"]*"', '', cleaned)           # remove interpolated strings
        cleaned = re.sub(r'\$@"[^"]*"', '', cleaned)          # remove verbatim interpolated

        params = re.findall(r'\{(\w+)\}', cleaned)
        seen: set[str] = set()
        result = []
        for p in params:
            if p not in seen and p not in CodeGenerator._CS_NON_PARAMS:
                seen.add(p)
                result.append({
                    "name": p,
                    "type": "string",
                    "description": f"Parameter: {p}",
                })
        return result

    @staticmethod
    def _extract_code(text: str) -> str:
        """Strip markdown code fences if present and clean up common LLM mistakes."""
        # Remove <thinking> block first
        cleaned = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL)
        _log.info(f"[_extract_code] after thinking removal: len={len(cleaned)}")

        # Try to match complete code fences
        m = re.search(r"```(?:csharp|cs)?\s*\n(.*?)```", cleaned, re.DOTALL)
        if m:
            _log.info(f"[_extract_code] matched complete fence: code len={len(m.group(1))}")
        else:
            # Fallback: opening fence without closing (truncated LLM response)
            m = re.search(r"```(?:csharp|cs)?\s*\n(.*)", cleaned, re.DOTALL)
            if m:
                _log.warning(f"[_extract_code] matched OPEN fence only (truncated): code len={len(m.group(1))}")
            else:
                _log.warning(f"[_extract_code] NO fence match — using raw cleaned text")

        code = m.group(1).strip() if m else cleaned.strip()
        # Remove any stray leftover code fences
        code = re.sub(r'^```(?:csharp|cs)?\s*\n?', '', code)
        code = re.sub(r'\n?```\s*$', '', code)
        # Fix: LLM sometimes adds L suffix to ElementId constructor args
        code = re.sub(r'new ElementId\((\d+)L\)', r'new ElementId(\1)', code)
        _log.info(f"[_extract_code] final code len={len(code)}, first 100: {code[:100]!r}")
        return code

    @staticmethod
    def _extract_thinking(text: str) -> str:
        """Extract <thinking> block from LLM response."""
        m = re.search(r'<thinking>(.*?)</thinking>', text, re.DOTALL)
        return m.group(1).strip() if m else ""

    def parameterize(self, code: str, source_query: str,
                     thinking: str = "", selections: dict | None = None,
                     ) -> tuple[str, list[dict]]:
        """Use LLM agent to analyze thinking chain + code and determine variable parameters.

        Returns (parameterized_code, parameters_list).
        """
        system = """\
You are an intelligent agent that analyzes Revit API code to determine which values should
become reusable parameters. You have access to the original user query, the LLM's thinking
process, and the user's interactive selections.

## Your Task
Think step by step:
1. Read the THINKING CHAIN to understand what decisions were made and why
2. Read the USER SELECTIONS to see what the user explicitly chose (these are definitely variable)
3. Examine every literal value in the CODE
4. For each literal, reason: "If a different user ran this tool for a different scenario,
   would this value need to change?" If YES → make it a parameter.

## What should be parameterized:
- Values the user SELECTED interactively (family types, levels) — these change per project
- Dimension values (width, height, depth) — users will want different sizes
- Position coordinates — users will want different locations
- Element names/numbers — users will want custom naming
- Type/family names hardcoded as strings — different projects have different types loaded

## What should NOT be parameterized:
- Revit API method names, class names, enum values (BuiltInParameter.*, BuiltInCategory.*)
- Boolean flags that control API behavior (true/false in API calls)
- Unit conversion constants (304.8, 0.3048)
- Structural code patterns (loops, conditions, variable declarations)

## choices_from — for parameters that need dynamic options from Revit:
- Wall type names → choices_from: "family_types:OST_Walls"
- Floor type names → choices_from: "family_types:OST_Floors"
- Column type names → choices_from: "family_types:OST_StructuralColumns"
- Level names → choices_from: "levels"
- Any other family type → choices_from: "family_types:OST_<CategoryName>"

## Output Format
Return ONLY valid JSON (no markdown fences):
{
  "reasoning": "brief explanation of which values are variable and why",
  "code": "// the parameterized code with {placeholder} syntax...",
  "parameters": [
    {"name": "param_name", "type": "string|double", "description": "bilingual description", "default": "value", "choices_from": "optional"}
  ]
}
"""
        # Build a rich context prompt with all available information
        parts = [f"## User Query\n{source_query}"]
        if thinking:
            # Strip markdown formatting from thinking
            clean_thinking = thinking.replace("**Thinking:**\n\n", "").strip()
            if clean_thinking:
                parts.append(f"## LLM Thinking Chain\n{clean_thinking}")
        if selections:
            # Show what the user explicitly selected
            sel_lines = []
            for k, v in selections.items():
                if not k.startswith("_"):  # skip internal keys
                    sel_lines.append(f"- {k}: {v}")
            if sel_lines:
                parts.append(f"## User Selections (these values ARE variable)\n" +
                             "\n".join(sel_lines))
            # Also show orchestrator answers if present
            orch = selections.get("_orchestrator_answers", {})
            if orch:
                orch_lines = [f"- {k}: {v}" for k, v in orch.items()]
                parts.append(f"## Orchestrator Answers\n" + "\n".join(orch_lines))
        parts.append(f"## Code to Parameterize\n```csharp\n{code}\n```")
        prompt = "\n\n".join(parts)

        raw = self.llm.generate_text(prompt, system_prompt=system)

        # Extract JSON from response
        # Strip markdown fences if LLM wraps the JSON
        cleaned = re.sub(r'^```(?:json)?\s*\n', '', raw.strip())
        cleaned = re.sub(r'\n```\s*$', '', cleaned)

        try:
            result = json.loads(cleaned)
            param_code = result.get("code", code)
            parameters = result.get("parameters", [])
            # Ensure all parameters have required fields
            for p in parameters:
                p.setdefault("type", "string")
                p.setdefault("description", f"Parameter: {p.get('name', '?')}")
            return param_code, parameters
        except (json.JSONDecodeError, KeyError):
            # Fallback: return original code with regex-extracted params
            return code, self.extract_parameters(code)
