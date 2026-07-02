"""
AI Skill 管理 — 文件驱动的 Skill 存储

每个 Skill 是一个 Markdown 文件（YAML frontmatter + 行为协议内容），
参考 PUA (github.com/tanweai/pua) 的 SKILL.md 格式。

存储路径: data/skills/*.md
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from functools import lru_cache
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def _get_skills_dir() -> Path:
    """Resolve skills directory."""
    try:
        from server.app.deps import get_config, _resolve_data_dir
        config = get_config()
        data_dir = _resolve_data_dir(config)
        skills_dir = data_dir / "skills"
    except Exception:
        skills_dir = Path("./data/skills")
    skills_dir.mkdir(parents=True, exist_ok=True)
    return skills_dir


# ── YAML frontmatter parsing ────────────────────────────────────────────

_FM_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_skill_file(text: str) -> dict[str, Any]:
    """Parse a SKILL.md file into {meta: {...}, content: str}."""
    m = _FM_PATTERN.match(text)
    if m:
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            meta = {}
        content = text[m.end():]
    else:
        meta = {}
        content = text
    return {"meta": meta, "content": content.strip()}


def build_skill_file(meta: dict, content: str) -> str:
    """Build a SKILL.md file from meta dict and content string."""
    fm = yaml.dump(meta, allow_unicode=True, default_flow_style=False, sort_keys=False).strip()
    return f"---\n{fm}\n---\n\n{content}\n"


# ── Skill Store ──────────────────────────────────────────────────────────

class SkillStore:
    """File-based skill store. Each skill = one .md file in skills_dir."""

    def __init__(self, skills_dir: Path | None = None):
        self._dir = skills_dir or _get_skills_dir()

    def _slug(self, name: str) -> str:
        """Convert name to safe filename slug."""
        s = re.sub(r"[^\w\-]", "-", name.lower().strip())
        return re.sub(r"-+", "-", s).strip("-") or "unnamed"

    def _file_for(self, skill_id: str) -> Path:
        if not re.fullmatch(r"[\w\-]+", skill_id):
            raise ValueError(f"Invalid skill id: {skill_id}")
        return self._dir / f"{skill_id}.md"

    # ── List ─────────────────────────────────────────────────────────

    def list_all(self) -> list[dict]:
        """List all skills with metadata."""
        skills = []
        for f in sorted(self._dir.glob("*.md")):
            try:
                text = f.read_text(encoding="utf-8")
                parsed = parse_skill_file(text)
                meta = parsed["meta"]
                skills.append({
                    "id": f.stem,
                    "name": meta.get("name", f.stem),
                    "description": meta.get("description", ""),
                    "version": str(meta.get("version", "1.0")),
                    "author": meta.get("author", ""),
                    "module": meta.get("module", "global"),
                    "enabled": meta.get("enabled", True),
                    "file_size": f.stat().st_size,
                })
            except Exception as e:
                logger.warning(f"Failed to read skill {f.name}: {e}")
        return skills

    # ── Read ─────────────────────────────────────────────────────────

    def get(self, skill_id: str) -> dict | None:
        """Get full skill detail including content."""
        f = self._file_for(skill_id)
        if not f.is_file():
            return None
        text = f.read_text(encoding="utf-8")
        parsed = parse_skill_file(text)
        meta = parsed["meta"]
        return {
            "id": skill_id,
            "name": meta.get("name", skill_id),
            "description": meta.get("description", ""),
            "version": str(meta.get("version", "1.0")),
            "author": meta.get("author", ""),
            "module": meta.get("module", "global"),
            "enabled": meta.get("enabled", True),
            "content": parsed["content"],
            "raw": text,
        }

    # ── Create / Update ──────────────────────────────────────────────

    def save(self, skill_id: str | None, meta: dict, content: str) -> dict:
        """Create or update a skill. Returns the saved skill dict."""
        if not skill_id:
            skill_id = self._slug(meta.get("name", "unnamed"))

        # Avoid overwriting — append number if exists on create
        f = self._file_for(skill_id)
        if f.is_file() and not meta.get("_overwrite"):
            i = 2
            while self._file_for(f"{skill_id}-{i}").is_file():
                i += 1
            skill_id = f"{skill_id}-{i}"

        # Ensure required fields
        meta.pop("_overwrite", None)
        meta.setdefault("name", skill_id)
        meta.setdefault("enabled", True)
        meta.setdefault("module", "global")
        meta.setdefault("version", "1.0")

        f = self._file_for(skill_id)
        f.write_text(build_skill_file(meta, content), encoding="utf-8")
        logger.info(f"Skill saved: {skill_id} ({f.stat().st_size} bytes)")
        return self.get(skill_id)  # type: ignore

    def update(self, skill_id: str, meta: dict | None = None, content: str | None = None) -> dict | None:
        """Update an existing skill's meta and/or content."""
        existing = self.get(skill_id)
        if not existing:
            return None

        # Merge meta
        text = self._file_for(skill_id).read_text(encoding="utf-8")
        parsed = parse_skill_file(text)
        if meta:
            parsed["meta"].update(meta)
        if content is not None:
            parsed["content"] = content

        parsed["meta"]["_overwrite"] = True
        return self.save(skill_id, parsed["meta"], parsed["content"])

    # ── Toggle ───────────────────────────────────────────────────────

    def toggle(self, skill_id: str, enabled: bool) -> dict | None:
        """Enable or disable a skill."""
        return self.update(skill_id, meta={"enabled": enabled})

    # ── Delete ───────────────────────────────────────────────────────

    def delete(self, skill_id: str) -> bool:
        """Delete a skill file."""
        f = self._file_for(skill_id)
        if f.is_file():
            f.unlink()
            logger.info(f"Skill deleted: {skill_id}")
            return True
        return False

    # ── Import from raw text ─────────────────────────────────────────

    def import_from_text(self, raw_text: str, source: str = "") -> dict:
        """Import a SKILL.md from raw text (e.g., fetched from GitHub)."""
        parsed = parse_skill_file(raw_text)
        meta = parsed["meta"]
        if source:
            meta["source"] = source
        skill_id = self._slug(meta.get("name", "imported"))
        return self.save(skill_id, meta, parsed["content"])

    # ── Active skills for prompt injection ────────────────────────────

    def get_active_prompt(self, module: str) -> str:
        """
        Get concatenated prompt from all enabled skills targeting this module.
        Returns empty string if no active skills.
        """
        parts: list[str] = []
        for f in sorted(self._dir.glob("*.md")):
            try:
                text = f.read_text(encoding="utf-8")
                parsed = parse_skill_file(text)
                meta = parsed["meta"]
                if not meta.get("enabled", True):
                    continue
                target = meta.get("module", "global")
                if target == "global" or target == module:
                    parts.append(parsed["content"])
            except Exception:
                continue
        return "\n\n---\n\n".join(parts)


# ── Built-in Skill Scanner ───────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Layer names for intent_bridge skill sub-directories
_LAYER_MAP = {"patterns": "pattern", "workflows": "workflow", "standards": "standard"}


def _extract_title_and_desc(text: str) -> tuple[str, str]:
    """Extract first H1 title and first paragraph from Markdown."""
    title = ""
    desc = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not title and stripped.startswith("# "):
            title = stripped[2:].strip()
        elif title and not desc and stripped and not stripped.startswith("#"):
            # Take first non-heading line as description
            desc = stripped.rstrip("。.").strip()
            break
    return title, desc


def _extract_keywords(text: str) -> str:
    """Extract trigger keywords from intent_bridge skill format."""
    kw: list[str] = []
    for m in re.finditer(r"(?:中文|English)[：:]\s*(.+)", text):
        kw.append(m.group(1).strip())
    return " / ".join(kw) if kw else ""


def scan_builtin_skills() -> list[dict]:
    """
    Scan intent_bridge skills and prompt_bridge scenarios.
    Returns unified list with source/layer tags, all read-only.
    """
    results: list[dict] = []

    # ── Intent Bridge skills ──
    ib_dir = _PROJECT_ROOT / "intent_bridge" / "schemas" / "skills"
    if ib_dir.is_dir():
        # _base.md — special: base rules
        base = ib_dir / "_base.md"
        if base.is_file():
            text = base.read_text(encoding="utf-8")
            title, desc = _extract_title_and_desc(text)
            results.append({
                "id": "ib:_base",
                "name": title or "Base Rules",
                "description": desc or "Intent Bridge base rules (zero-default, enrich, question ordering)",
                "version": "-",
                "author": "built-in",
                "module": "intent_bridge",
                "enabled": True,
                "source": "intent_bridge",
                "layer": "base",
                "keywords": "",
                "readonly": True,
                "file_size": base.stat().st_size,
            })

        # patterns, workflows, standards
        for subdir_name, layer in _LAYER_MAP.items():
            subdir = ib_dir / subdir_name
            if not subdir.is_dir():
                continue
            for f in sorted(subdir.glob("*.md")):
                if f.name.startswith("_"):
                    continue
                try:
                    text = f.read_text(encoding="utf-8")
                    title, desc = _extract_title_and_desc(text)
                    keywords = _extract_keywords(text)
                    results.append({
                        "id": f"ib:{subdir_name}/{f.stem}",
                        "name": title or f.stem,
                        "description": desc,
                        "version": "-",
                        "author": "built-in",
                        "module": "intent_bridge",
                        "enabled": True,
                        "source": "intent_bridge",
                        "layer": layer,
                        "keywords": keywords,
                        "readonly": True,
                        "file_size": f.stat().st_size,
                    })
                except Exception as e:
                    logger.warning(f"Failed to scan {f}: {e}")

    # ── PromptBridge scenarios ──
    pb_dir = _PROJECT_ROOT / "prompt_bridge" / "scenarios"
    if pb_dir.is_dir():
        for f in sorted(pb_dir.glob("*.md")):
            try:
                text = f.read_text(encoding="utf-8")
                title, desc = _extract_title_and_desc(text)
                results.append({
                    "id": f"pb:{f.stem}",
                    "name": title or f.stem.replace("_", " ").title(),
                    "description": desc,
                    "version": "-",
                    "author": "built-in",
                    "module": "prompt_bridge",
                    "enabled": True,
                    "source": "prompt_bridge",
                    "layer": "scenario",
                    "keywords": "",
                    "readonly": True,
                    "file_size": f.stat().st_size,
                })
            except Exception as e:
                logger.warning(f"Failed to scan {f}: {e}")

    return results


def get_builtin_skill_content(skill_id: str) -> str | None:
    """Read the content of a built-in skill by its compound id (e.g. 'ib:patterns/point_based')."""
    if skill_id.startswith("ib:"):
        rel = skill_id[3:]  # e.g. '_base' or 'patterns/point_based'
        if ".." in rel or rel.startswith(("/", "\\")):
            return None
        base = _PROJECT_ROOT / "intent_bridge" / "schemas" / "skills"
        path = base / (rel + ".md")
        if path.is_file() and path.resolve().is_relative_to(base.resolve()):
            return path.read_text(encoding="utf-8")
    elif skill_id.startswith("pb:"):
        rel = skill_id[3:]
        if ".." in rel or rel.startswith(("/", "\\")):
            return None
        base = _PROJECT_ROOT / "prompt_bridge" / "scenarios"
        path = base / (rel + ".md")
        if path.is_file() and path.resolve().is_relative_to(base.resolve()):
            return path.read_text(encoding="utf-8")
    return None


# ── Singleton ────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_skill_store() -> SkillStore:
    return SkillStore()
