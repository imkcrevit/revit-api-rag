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
   `return new {{ ElementId = element.Id.IntegerValue, Status = "Created" }};`
8. Structure code with numbered step comments:
   `// Step 1: [purpose] — [which API and why]`
9. Common pitfalls:
   - FamilySymbol must call Activate() before placing instances
   - FilteredElementCollector needs OfClass() or OfCategory()
   - Do NOT use `using` statements for Revit objects
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
            "\n## User Selections (use these exact values, do NOT query for them):",
        ]
        if "family_type" in selections:
            lines.append(f"- Family Type: {selections['family_type']}")
        if "level" in selections:
            lines.append(f"- Level: {selections['level']}")
        if "host_element_id" in selections:
            lines.append(f"- Host Element ID: {selections['host_element_id']}")
        if "position" in selections:
            pos = selections["position"]
            lines.append(f"- Position: ({pos.get('x', 0)}mm, {pos.get('y', 0)}mm)")
        lines.append(
            "\nIMPORTANT: Do not use FilteredElementCollector to find these. "
            "Use the exact names/IDs above.\n"
        )
        return "\n".join(lines)

    @staticmethod
    def extract_parameters(code: str) -> list[dict]:
        """Extract {param_name} placeholders from code template."""
        params = re.findall(r'\{(\w+)\}', code)
        seen: set[str] = set()
        result = []
        for p in params:
            if p not in seen:
                seen.add(p)
                result.append({
                    "name": p,
                    "type": "string",
                    "description": f"Parameter: {p}",
                })
        return result

    @staticmethod
    def _extract_code(text: str) -> str:
        """Strip markdown code fences if present."""
        m = re.search(r"```(?:csharp|cs)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            return m.group(1).strip()
        return text.strip()
