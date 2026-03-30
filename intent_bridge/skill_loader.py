"""
Skill Loader — Loads, matches, and renders Markdown skill files for LLM prompts.

Skills are Markdown files organized in two layers:
  - patterns/  — Operation mode skills (line_based, point_based, hosted, etc.)
  - standards/ — Enterprise/team custom standards (MEP routing, fire zones, etc.)

The loader:
1. Loads all .md files from both directories (with mtime-based hot reload)
2. Extracts keywords from the "触发关键词" / "trigger keywords" section
3. Matches relevant skills based on user input
4. Returns raw Markdown content for direct prompt injection (no rendering needed)
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("intent_bridge.skill_loader")

_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "schemas", "skills")


class _SkillEntry:
    """A loaded skill file with extracted metadata."""

    __slots__ = ("name", "path", "content", "keywords_zh", "keywords_en", "mtime", "layer")

    def __init__(self, name: str, path: str, content: str, mtime: float, layer: str):
        self.name = name
        self.path = path
        self.content = content
        self.mtime = mtime
        self.layer = layer  # "pattern" or "standard"
        self.keywords_zh: list[str] = []
        self.keywords_en: list[str] = []
        self._extract_keywords()

    def _extract_keywords(self) -> None:
        """Extract trigger keywords from Markdown content."""
        # Match lines like: 中文：墙、墙体、隔墙  or  Chinese: ...
        zh_match = re.search(
            r"(?:中文|Chinese)[：:]\s*(.+)",
            self.content,
        )
        if zh_match:
            self.keywords_zh = [
                kw.strip()
                for kw in re.split(r"[、,，]+", zh_match.group(1))
                if kw.strip()
            ]

        # Match lines like: English：wall, beam  or  English: ...
        en_match = re.search(
            r"English[：:]\s*(.+)",
            self.content,
        )
        if en_match:
            self.keywords_en = [
                kw.strip()
                for kw in re.split(r"[、,，]+", en_match.group(1))
                if kw.strip()
            ]

        # For standards that may not have explicit keyword lines,
        # extract from the first heading as fallback
        if not self.keywords_zh and not self.keywords_en:
            heading = re.search(r"^#\s+(.+)", self.content, re.MULTILINE)
            if heading:
                title = heading.group(1).strip()
                # Split Chinese title into individual characters/words as keywords
                zh_chars = [ch for ch in title if "\u4e00" <= ch <= "\u9fff"]
                if zh_chars:
                    self.keywords_zh = [title]  # Use full title as keyword


class SkillLoader:
    """Loads and manages Markdown skill files for LLM prompt injection."""

    def __init__(self, skills_dir: str | None = None):
        self._dir = skills_dir or _SKILLS_DIR
        self._base_content: str = ""
        self._skills: dict[str, _SkillEntry] = {}
        self._mtimes: dict[str, float] = {}
        self._load_all()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_all(self) -> None:
        """Load all Markdown skill files from patterns/ and standards/ dirs."""
        if not os.path.isdir(self._dir):
            logger.warning("Skills directory not found: %s", self._dir)
            return

        # Load base rules
        base_path = os.path.join(self._dir, "_base.md")
        if os.path.isfile(base_path):
            with open(base_path, "r", encoding="utf-8") as f:
                self._base_content = f.read()
            self._mtimes["_base.md"] = os.path.getmtime(base_path)

        # Load pattern skills
        patterns_dir = os.path.join(self._dir, "patterns")
        if os.path.isdir(patterns_dir):
            self._load_dir(patterns_dir, "pattern")

        # Load standard skills
        standards_dir = os.path.join(self._dir, "standards")
        if os.path.isdir(standards_dir):
            self._load_dir(standards_dir, "standard")

        logger.info(
            "Loaded %d skills (%d patterns + %d standards) from %s",
            len(self._skills),
            sum(1 for s in self._skills.values() if s.layer == "pattern"),
            sum(1 for s in self._skills.values() if s.layer == "standard"),
            self._dir,
        )

    def _load_dir(self, dirpath: str, layer: str) -> None:
        """Load all .md files from a directory."""
        for fname in os.listdir(dirpath):
            if not fname.endswith(".md") or fname.startswith("_"):
                continue
            fpath = os.path.join(dirpath, fname)
            self._load_file(fpath, layer)

    def _load_file(self, fpath: str, layer: str) -> None:
        """Load a single Markdown skill file."""
        try:
            mtime = os.path.getmtime(fpath)
            fname = os.path.basename(fpath)

            if fname in self._mtimes and self._mtimes[fname] == mtime:
                return

            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()

            if not content.strip():
                return

            name = fname.replace(".md", "")
            self._skills[name] = _SkillEntry(name, fpath, content, mtime, layer)
            self._mtimes[fname] = mtime

        except Exception as e:
            logger.warning("Failed to load skill %s: %s", fpath, e)

    def reload_if_changed(self) -> None:
        """Hot-reload any skill files that have been modified on disk."""
        # Reload base
        base_path = os.path.join(self._dir, "_base.md")
        if os.path.isfile(base_path):
            mtime = os.path.getmtime(base_path)
            if self._mtimes.get("_base.md") != mtime:
                with open(base_path, "r", encoding="utf-8") as f:
                    self._base_content = f.read()
                self._mtimes["_base.md"] = mtime

        # Reload patterns and standards
        for subdir, layer in [("patterns", "pattern"), ("standards", "standard")]:
            dirpath = os.path.join(self._dir, subdir)
            if os.path.isdir(dirpath):
                self._load_dir(dirpath, layer)

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def match_skills(
        self, user_input: str, search_terms: list[str] | None = None,
    ) -> list[_SkillEntry]:
        """
        Match relevant skills based on user input.

        Returns up to 1 pattern skill + all matching standard skills.
        Pattern skills are mutually exclusive (pick best match).
        Standard skills stack (all matching ones are included).
        """
        self.reload_if_changed()

        input_lower = user_input.lower()
        pattern_scores: list[tuple[str, int, _SkillEntry]] = []
        matched_standards: list[_SkillEntry] = []

        # Action skills (delete, modify, query) get a bonus because the action verb
        # is more decisive than the object noun. E.g., "删除柱子" → delete, not point_based.
        _ACTION_SKILLS = {"delete", "modify", "query"}

        for name, skill in self._skills.items():
            score = 0

            for kw in skill.keywords_zh:
                if kw in user_input:
                    base = 3 if name in _ACTION_SKILLS else 2
                    # Action verbs at the start of input get extra weight
                    if name in _ACTION_SKILLS and user_input.strip().startswith(kw):
                        base += 2
                    score += base

            for kw in skill.keywords_en:
                if kw.lower() in input_lower:
                    base = 2 if name in _ACTION_SKILLS else 1
                    if name in _ACTION_SKILLS and input_lower.strip().startswith(kw.lower()):
                        base += 2
                    score += base

            if score > 0:
                if skill.layer == "pattern":
                    pattern_scores.append((name, score, skill))
                else:
                    matched_standards.append(skill)

        # Best pattern skill
        result: list[_SkillEntry] = []
        if pattern_scores:
            pattern_scores.sort(key=lambda x: x[1], reverse=True)
            best = pattern_scores[0]
            result.append(best[2])
            logger.info("Matched pattern skill: %s (score=%d)", best[0], best[1])

        # All matching standards
        for std in matched_standards:
            result.append(std)
            logger.info("Matched standard skill: %s", std.name)

        if not result:
            logger.info("No skill matched for: %s", user_input[:50])

        return result

    # ------------------------------------------------------------------
    # Prompt generation
    # ------------------------------------------------------------------

    def get_base_rules(self) -> str:
        """Return the base rules Markdown content."""
        return self._base_content

    def render_skill_prompt(self, skills: list[_SkillEntry]) -> str:
        """Concatenate matched skill contents for prompt injection."""
        if not skills:
            return ""

        sections: list[str] = []
        for skill in skills:
            label = "操作模式" if skill.layer == "pattern" else "企业规范"
            sections.append(f"<!-- {label}: {skill.name} -->\n{skill.content}")

        return "\n\n---\n\n".join(sections)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_skill(self, name: str) -> _SkillEntry | None:
        return self._skills.get(name)

    def list_skills(self) -> list[dict]:
        """List all loaded skills with metadata."""
        return [
            {
                "name": s.name,
                "layer": s.layer,
                "keywords_zh": s.keywords_zh,
                "keywords_en": s.keywords_en,
                "path": s.path,
            }
            for s in self._skills.values()
        ]


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_loader: SkillLoader | None = None


def get_skill_loader() -> SkillLoader:
    """Get or create the global SkillLoader singleton."""
    global _loader
    if _loader is None:
        _loader = SkillLoader()
    return _loader
