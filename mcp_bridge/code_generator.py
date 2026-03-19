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
from pipeline.llm_client import LLMClient

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
{selections_context}
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
                 revit_version: str = "2026", user_unit: str = "mm"):
        self.retriever = retriever
        self.llm = llm_client
        self.revit_version = revit_version
        self.user_unit = user_unit

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

        system = SYSTEM_EXECUTE.format(
            revit_version=self.revit_version,
            api_context=ctx.get("api_context", "(none)"),
            code_context=ctx.get("code_context", "(none)"),
            selections_context=self._build_selections_context(selections),
            unit_context=self.UNIT_CONTEXTS.get(self.user_unit, self.UNIT_CONTEXTS["mm"]),
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

        system = SYSTEM_EXECUTE.format(
            revit_version=self.revit_version,
            api_context=ctx.get("api_context", "(none)"),
            code_context=ctx.get("code_context", "(none)"),
            selections_context=self._build_selections_context(selections),
            unit_context=self.UNIT_CONTEXTS.get(self.user_unit, self.UNIT_CONTEXTS["mm"]),
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
        lines.append(
            "\nCRITICAL: Do NOT use `.First()` or `.FirstOrDefault()` without a name filter. "
            "Always filter by the exact name provided above.\n"
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

    def parameterize(self, code: str, source_query: str) -> tuple[str, list[dict]]:
        """Use LLM to replace hardcoded values with {placeholders} for tool solidification.

        Returns (parameterized_code, parameters_list).
        """
        system = """\
You are a Revit API code parameterization expert. Given C# code generated for a specific task,
identify hardcoded values that should become reusable parameters, and replace them with {placeholder} syntax.

## Rules
1. Replace hardcoded values with {param_name} placeholders (lowercase_snake_case)
2. MANDATORY parameters — you MUST extract these if the code uses them:
   - Any wall/floor/column type name string (e.g. "Generic - 200mm", "Basic Wall") → {wall_type} with choices_from
   - Any level name string (e.g. "L1", "Level 1") → {level_name} with choices_from: "levels"
   - Any .FirstOrDefault() or .First() on type/level collections → replace the filter string with a parameter
   - Dimensions (width, depth, height in mm) → {width_mm}, {depth_mm}, {height_mm}
   - Coordinates → {x}, {y}
   - Room/element names/numbers → {room_name}, {room_number}
3. For type name parameters, ALWAYS add choices_from based on category:
   - Wall types → "family_types:OST_Walls"
   - Structural columns → "family_types:OST_StructuralColumns"
   - Floor types → "family_types:OST_Floors"
   - Levels → "levels"
   - Rooms → "family_types:OST_Rooms"
4. Keep structural code unchanged — only replace literal values
5. For numeric placeholders in expressions like `5.0 / 0.3048`, replace the user-facing value:
   `{width_mm} / 304.8` (width in mm)
6. Do NOT parameterize Revit API constants, enum values, or boolean flags
7. CRITICAL: If the code references ANY WallType/Level by hardcoded name, it MUST become a parameter.
   Never leave type names or level names hardcoded.

## Output Format
Return ONLY valid JSON (no markdown fences):
{
  "code": "// the parameterized code...",
  "parameters": [
    {"name": "param_name", "type": "string|double", "description": "...", "default": "value", "choices_from": "optional"}
  ]
}
"""
        prompt = f"Source query: {source_query}\n\nCode to parameterize:\n```csharp\n{code}\n```"

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
