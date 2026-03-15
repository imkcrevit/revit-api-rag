"""
Code Generator — RAG-informed C# code generation for Revit execution.

Uses the existing RAG pipeline to generate code suitable for send_code_to_revit.
Code runs inside a Roslyn-compiled static method:
    public static object Execute(Document document, object[] parameters)

CRITICAL: The plugin's ExecuteCodeEventHandler already wraps user code in a
Transaction. Generated code must NOT create its own Transaction.
"""
from __future__ import annotations

import re

from pipeline.retriever import RAGRetriever
from pipeline.llm_client import LLMClient


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
5. For Structure namespace, use fully qualified names:
   `Autodesk.Revit.DB.Structure.StructuralType.Column`
6. All coordinates in Revit internal units (feet).
   User mm -> divide by 304.8. User m -> divide by 0.3048.
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
{selections_context}
## Retrieved API Documentation
{api_context}

## Retrieved SDK Code Examples
{code_context}
"""


class CodeGenerator:
    """Generate Revit-executable C# code using RAG context."""

    def __init__(self, retriever: RAGRetriever, llm_client: LLMClient,
                 revit_version: str = "2026"):
        self.retriever = retriever
        self.llm = llm_client
        self.revit_version = revit_version

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
        )

        raw = self.llm.generate_text(user_query, system_prompt=system)
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
        m = re.search(r"```(?:csharp|cs)?\s*\n(.*?)```", text, re.DOTALL)
        code = m.group(1).strip() if m else text.strip()
        # Fix: LLM sometimes adds L suffix to ElementId constructor args
        code = re.sub(r'new ElementId\((\d+)L\)', r'new ElementId(\1)', code)
        return code
