"""
Code Generator — RAG-informed C# code generation for Revit execution.

Uses the existing RAG pipeline to generate code suitable for send_code_to_revit,
which expects code that runs inside an Execute method with access to Document.
"""
from __future__ import annotations

from pipeline.retriever import RAGRetriever
from pipeline.llm_client import LLMClient


SYSTEM_EXECUTE = """\
You are a Revit {revit_version} API expert. Generate C# code that will be executed \
inside a Revit plugin's Execute method.

## Execution Context
Your code runs inside this template — write ONLY the method body:
```csharp
public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
{{
    UIApplication uiapp = commandData.Application;
    UIDocument uidoc = uiapp.ActiveUIDocument;
    Document doc = uidoc.Document;

    // === YOUR CODE HERE ===
    {user_code}
    // === END YOUR CODE ===

    return Result.Succeeded;
}}
```

## Rules
1. Use only Revit {revit_version} API — no invented classes or methods.
2. Output ONLY the code body (no class declaration, no using statements, no Execute wrapper).
3. Always wrap modifications in a Transaction.
4. Use variables `doc`, `uidoc`, `uiapp` directly — they are already declared.
5. Include brief inline comments for key API calls.
6. If parameters are needed, use string interpolation placeholders like {{param_name}}.

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
                 code_top_k: int = 5) -> tuple[str, dict]:
        """
        Generate C# code from user query using RAG.

        Returns:
            (code, context) where context contains RAG search details.
        """
        # 1. RAG retrieval
        results = self.retriever.search(
            user_query, api_top_k=api_top_k, code_top_k=code_top_k
        )
        ctx = self.retriever.build_context(results)

        # 2. Build prompt
        system = SYSTEM_EXECUTE.format(
            revit_version=self.revit_version,
            user_code="// (generated below)",
            api_context=ctx.get("api_context", "(none)"),
            code_context=ctx.get("code_context", "(none)"),
        )

        # 3. LLM generation
        raw = self.llm.generate_text(user_query, system_prompt=system)

        # 4. Extract code from markdown fences if present
        code = self._extract_code(raw)

        return code, {
            "query": user_query,
            "rewritten_query": results.rewritten_query,
            "api_count": len(results.api_items),
            "sdk_count": len(results.sdk_items),
        }

    def generate_stream(self, user_query: str, api_top_k: int = 15,
                        code_top_k: int = 5):
        """Streaming version — yields tokens as they arrive."""
        results = self.retriever.search(
            user_query, api_top_k=api_top_k, code_top_k=code_top_k
        )
        ctx = self.retriever.build_context(results)

        system = SYSTEM_EXECUTE.format(
            revit_version=self.revit_version,
            user_code="// (generated below)",
            api_context=ctx.get("api_context", "(none)"),
            code_context=ctx.get("code_context", "(none)"),
        )

        yield from self.llm.generate_stream(user_query, system_prompt=system)

    @staticmethod
    def _extract_code(text: str) -> str:
        """Strip markdown code fences if present."""
        import re
        # Match ```csharp ... ``` or ``` ... ```
        m = re.search(r"```(?:csharp|cs)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            return m.group(1).strip()
        return text.strip()
