"""
Tool Store — solidify successful code executions into reusable named tools.

Flow:
    1. RAG + LLM generates C# code
    2. Code executes successfully in Revit
    3. User calls solidify() → saves as YAML tool definition
    4. Next time: load tool by name → fill parameters → execute directly

Storage: mcp_bridge/tools/*.yaml
"""
from __future__ import annotations

import re
import yaml
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field


TOOLS_DIR = Path(__file__).parent / "tools"


@dataclass
class SolidifiedTool:
    """A reusable tool created from a successful code execution."""
    name: str                           # e.g. "create_curtain_wall"
    display_name: str                   # e.g. "Create Curtain Wall"
    description: str                    # what it does
    code_template: str                  # C# code with {param} placeholders
    parameters: list[dict]              # [{name, type, description, default?}]
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    source_query: str = ""              # original user query that generated it
    execution_count: int = 0
    last_used: str = ""


class ToolStore:
    """Manages solidified tools on disk."""

    def __init__(self, tools_dir: Path | str | None = None):
        self.tools_dir = Path(tools_dir) if tools_dir else TOOLS_DIR
        self.tools_dir.mkdir(parents=True, exist_ok=True)

    def _tool_path(self, name: str) -> Path:
        safe = re.sub(r"[^\w\-]", "_", name)
        return self.tools_dir / f"{safe}.yaml"

    # -- CRUD ------------------------------------------------------------------

    def solidify(
        self,
        name: str,
        code: str,
        description: str = "",
        display_name: str = "",
        parameters: list[dict] | None = None,
        tags: list[str] | None = None,
        source_query: str = "",
    ) -> SolidifiedTool:
        """Save a successful code execution as a reusable tool."""
        tool = SolidifiedTool(
            name=name,
            display_name=display_name or name.replace("_", " ").title(),
            description=description,
            code_template=code,
            parameters=parameters or [],
            tags=tags or [],
            created_at=datetime.now().isoformat(),
            source_query=source_query,
        )
        data = {
            "name": tool.name,
            "display_name": tool.display_name,
            "description": tool.description,
            "code_template": tool.code_template,
            "parameters": tool.parameters,
            "tags": tool.tags,
            "created_at": tool.created_at,
            "source_query": tool.source_query,
            "execution_count": 0,
            "last_used": "",
        }
        path = self._tool_path(name)
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return tool

    def load(self, name: str) -> SolidifiedTool | None:
        """Load a tool by name."""
        path = self._tool_path(name)
        if not path.exists():
            return None
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return SolidifiedTool(**data)

    def list_tools(self) -> list[SolidifiedTool]:
        """List all solidified tools."""
        tools = []
        for p in sorted(self.tools_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(p.read_text(encoding="utf-8"))
                tools.append(SolidifiedTool(**data))
            except Exception:
                continue
        return tools

    def delete(self, name: str) -> bool:
        """Delete a tool by name."""
        path = self._tool_path(name)
        if path.exists():
            path.unlink()
            return True
        return False

    def record_usage(self, name: str) -> None:
        """Increment execution count after successful use."""
        path = self._tool_path(name)
        if not path.exists():
            return
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["execution_count"] = data.get("execution_count", 0) + 1
        data["last_used"] = datetime.now().isoformat()
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # -- Validation ------------------------------------------------------------

    def validate_params(
        self, name: str, params: dict | None = None,
    ) -> tuple[bool, list[str], dict]:
        """Validate and fill default values for tool parameters.

        Returns (valid, errors, filled_params):
          - valid: True if all checks pass
          - errors: list of human-readable error strings
          - filled_params: params dict with defaults filled in
        """
        tool = self.load(name)
        if not tool:
            return False, [f"Tool '{name}' not found"], {}

        params = dict(params or {})
        errors: list[str] = []

        for pdef in tool.parameters:
            pname = pdef["name"]
            ptype = pdef.get("type", "string").lower()
            has_default = "default" in pdef

            if pname not in params:
                if has_default:
                    params[pname] = pdef["default"]
                else:
                    errors.append(f"Missing required parameter: {pname}")
                    continue

            # Type coercion check for numeric types
            if pname in params and ptype in ("double", "number", "float", "int", "integer"):
                try:
                    float(params[pname])
                except (ValueError, TypeError):
                    errors.append(
                        f"Parameter '{pname}' expects {ptype}, got {params[pname]!r}"
                    )

        return (len(errors) == 0, errors, params)

    # -- Render ----------------------------------------------------------------

    def render_code(self, name: str, params: dict | None = None) -> str | None:
        """Load tool and fill parameter placeholders in code template.

        Validates params first; returns None if validation fails.
        """
        tool = self.load(name)
        if not tool:
            return None

        valid, errors, filled = self.validate_params(name, params)
        if not valid:
            return None

        code = tool.code_template
        for k, v in filled.items():
            code = code.replace(f"{{{k}}}", str(v))
        return code

    def get_dynamic_params(self, name: str) -> list[dict]:
        """Extract parameters that need dynamic choices from Revit.

        Returns list of dicts: [{name, choices_from, description}, ...]
        """
        tool = self.load(name)
        if not tool:
            return []
        return [
            {
                "name": p["name"],
                "choices_from": p["choices_from"],
                "description": p.get("description", ""),
            }
            for p in tool.parameters
            if "choices_from" in p
        ]

    def search(self, query: str) -> list[SolidifiedTool]:
        """Simple keyword search across tool names, descriptions, and tags."""
        query_lower = query.lower()
        results = []
        for tool in self.list_tools():
            searchable = f"{tool.name} {tool.display_name} {tool.description} {' '.join(tool.tags)}".lower()
            if query_lower in searchable:
                results.append(tool)
        return results

    def match_tool(self, user_query: str) -> SolidifiedTool | None:
        """Smart tool matching: return a tool only if there is exactly one
        high-confidence keyword match that has been successfully executed before.

        Uses keyword overlap scoring to find the best match.
        Returns None when the caller should fall back to RAG generation.
        """
        # First try exact substring match
        matches = self.search(user_query)
        if len(matches) == 1 and matches[0].execution_count > 0:
            return matches[0]

        # Keyword-based matching: split query into tokens and score tools
        # Chinese-aware: treat each character as a potential keyword
        query_lower = user_query.lower()
        # Extract meaningful keywords (Chinese chars + English words)
        import re as _re
        keywords = set(_re.findall(r'[\u4e00-\u9fff]+|[a-z_]+', query_lower))
        if not keywords:
            return None

        scored: list[tuple[float, SolidifiedTool]] = []
        for tool in self.list_tools():
            if tool.execution_count <= 0:
                continue
            searchable = (
                f"{tool.name} {tool.display_name} {tool.description} "
                f"{' '.join(tool.tags)} {tool.source_query}"
            ).lower()
            # Count how many keywords match
            hit = sum(1 for kw in keywords if kw in searchable)
            if hit > 0:
                score = hit / len(keywords)
                scored.append((score, tool))

        if not scored:
            return None

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_tool = scored[0]

        # Only return if score > 50% and clear winner (no close second)
        if best_score < 0.4:
            return None
        if len(scored) > 1 and scored[1][0] >= best_score * 0.8:
            return None  # ambiguous — two tools score similarly

        return best_tool
