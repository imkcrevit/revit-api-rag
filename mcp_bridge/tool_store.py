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
    """A reusable tool created from a successful code execution.

    Parameter source types (in each parameter dict's "source" field):
      - "query:<atom_key>"       → auto-resolved from Revit (e.g. "query:levels")
      - "interactive:<atom_key>" → needs user action (e.g. "interactive:pick_object")
      - "ask_user"               → must ask user, LLM must NOT guess
      - "default"                → use the "default" field value
      - "compute"                → derived from other params at runtime
      - (absent/empty)           → legacy param, treated as "ask_user"
    """
    name: str                           # e.g. "create_curtain_wall"
    display_name: str                   # e.g. "Create Curtain Wall"
    description: str                    # what it does
    code_template: str                  # C# code with {param} placeholders
    parameters: list[dict]              # [{name, type, description, source?, default?}]
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    source_query: str = ""              # original user query that generated it
    execution_count: int = 0
    last_used: str = ""
    failure_count: int = 0              # consecutive failures (reset on success)
    preconditions: list[str] = field(default_factory=list)   # when this tool can be used
    applies_when: list[str] = field(default_factory=list)    # trigger phrases
    not_for: list[str] = field(default_factory=list)         # explicitly excluded scenarios


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
        preconditions: list[str] | None = None,
        applies_when: list[str] | None = None,
        not_for: list[str] | None = None,
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
            preconditions=preconditions or [],
            applies_when=applies_when or [],
            not_for=not_for or [],
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
            "failure_count": 0,
            "preconditions": tool.preconditions,
            "applies_when": tool.applies_when,
            "not_for": tool.not_for,
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

    def record_usage(self, name: str, success: bool = True) -> None:
        """Record tool execution result.

        On success: increment execution_count, reset failure_count.
        On failure: increment failure_count (for health check / auto-degradation).
        """
        path = self._tool_path(name)
        if not path.exists():
            return
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if success:
            data["execution_count"] = data.get("execution_count", 0) + 1
            data["failure_count"] = 0
            data["last_used"] = datetime.now().isoformat()
        else:
            data["failure_count"] = data.get("failure_count", 0) + 1
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def health_check(self, name: str) -> dict:
        """Check if a tool is healthy or should fall back to RAG regeneration.

        Returns:
            {"status": "healthy"|"stale"|"failing"|"not_found",
             "issues": [...], "recommendation": "use_tool"|"fallback_to_rag"}
        """
        tool = self.load(name)
        if not tool:
            return {"status": "not_found", "issues": [], "recommendation": "fallback_to_rag"}

        issues: list[str] = []

        # Consecutive failures
        if tool.failure_count >= 2:
            issues.append(f"连续失败 {tool.failure_count} 次 / {tool.failure_count} consecutive failures")

        # Time decay: stale if unused for 30+ days
        if tool.last_used:
            try:
                last = datetime.fromisoformat(tool.last_used)
                days = (datetime.now() - last).days
                if days > 30:
                    issues.append(f"未使用 {days} 天 / unused for {days} days")
            except ValueError:
                pass

        # Never successfully used
        if tool.execution_count == 0:
            issues.append("从未成功执行 / never successfully executed")

        if issues:
            status = "failing" if tool.failure_count >= 2 else "stale"
            return {"status": status, "issues": issues, "recommendation": "fallback_to_rag"}

        return {"status": "healthy", "issues": [], "recommendation": "use_tool"}

    def get_atom_sources(self, name: str) -> list[dict]:
        """Extract all atom-sourced parameters for a tool.

        Returns list of {name, source, description} for params that reference atoms.
        """
        tool = self.load(name)
        if not tool:
            return []
        return [
            {"name": p["name"], "source": p["source"], "description": p.get("description", "")}
            for p in tool.parameters
            if p.get("source", "").startswith(("query:", "interactive:"))
        ]

    # -- Validation ------------------------------------------------------------

    def validate_params(
        self, name: str, params: dict | None = None,
        resolved_atoms: dict | None = None,
    ) -> tuple[bool, list[str], dict]:
        """Validate and fill default values for tool parameters.

        Args:
            name: tool name
            params: user/LLM-provided parameter values
            resolved_atoms: {param_name: [{label,value},...]} from AtomResolver

        Returns (valid, errors, filled_params):
          - valid: True if all checks pass
          - errors: list of human-readable error strings
          - filled_params: params dict with defaults filled in
        """
        tool = self.load(name)
        if not tool:
            return False, [f"Tool '{name}' not found"], {}

        params = dict(params or {})
        resolved_atoms = resolved_atoms or {}
        errors: list[str] = []

        for pdef in tool.parameters:
            pname = pdef["name"]
            ptype = pdef.get("type", "string").lower()
            source = pdef.get("source", "")
            has_default = "default" in pdef

            # Check atom-sourced params: value must come from resolved choices
            if source.startswith(("query:", "interactive:")):
                if pname not in params:
                    if pname in resolved_atoms and resolved_atoms[pname]:
                        errors.append(
                            f"Parameter '{pname}' requires selection from Revit "
                            f"(source: {source}). Available: "
                            f"{[c['label'] for c in resolved_atoms[pname][:5]]}"
                        )
                    else:
                        errors.append(
                            f"Parameter '{pname}' must be resolved from Revit "
                            f"(source: {source}) — call resolve atoms first"
                        )
                    continue

            # "ask_user" or empty source: must be provided, no guessing
            if source in ("ask_user", "") and pname not in params:
                if has_default:
                    params[pname] = pdef["default"]
                else:
                    errors.append(f"Missing required parameter: {pname}")
                    continue

            # "default" source: fill from default
            if source == "default" and pname not in params:
                if has_default:
                    params[pname] = pdef["default"]
                else:
                    errors.append(f"Parameter '{pname}' has source=default but no default value")
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
        """Smart tool matching using enriched metadata.

        Scoring considers: name, description, tags, source_query,
        AND the new applies_when / not_for fields.

        Returns None when the caller should fall back to RAG generation.
        Also returns None if the tool is unhealthy (failing/stale).
        """
        query_lower = user_query.lower()
        import re as _re
        keywords = set(_re.findall(r'[\u4e00-\u9fff]+|[a-z_]+', query_lower))
        if not keywords:
            return None

        scored: list[tuple[float, SolidifiedTool]] = []
        for tool in self.list_tools():
            # Skip never-executed or unhealthy tools
            if tool.execution_count <= 0:
                continue
            health = self.health_check(tool.name)
            if health["recommendation"] == "fallback_to_rag":
                continue

            # Check not_for exclusions first
            not_for_texts = [nf.lower() for nf in (tool.not_for or [])]
            if any(nf in query_lower for nf in not_for_texts):
                continue

            # Score from applies_when (high weight — explicit trigger phrases)
            applies_texts = [aw.lower() for aw in (tool.applies_when or [])]
            applies_hits = sum(2.0 for aw in applies_texts if aw in query_lower)

            # Score from standard fields
            searchable = (
                f"{tool.name} {tool.display_name} {tool.description} "
                f"{' '.join(tool.tags)} {tool.source_query}"
            ).lower()
            keyword_hits = sum(1 for kw in keywords if kw in searchable)

            total_hits = applies_hits + keyword_hits
            max_possible = len(keywords) + len(applies_texts) * 2
            if max_possible == 0:
                continue
            score = total_hits / max_possible
            if total_hits > 0:
                scored.append((score, tool))

        if not scored:
            return None

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_tool = scored[0]

        if best_score < 0.3:
            return None
        if len(scored) > 1 and scored[1][0] >= best_score * 0.8:
            return None  # ambiguous

        return best_tool
