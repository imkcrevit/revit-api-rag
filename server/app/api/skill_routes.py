"""
Skill 管理路由 — CRUD + GitHub 导入

- GET    /api/skills          — 列出所有 Skill
- GET    /api/skills/{id}     — 获取单个 Skill 详情
- POST   /api/skills          — 创建新 Skill
- PUT    /api/skills/{id}     — 更新 Skill
- PATCH  /api/skills/{id}     — 切换启用/禁用
- DELETE /api/skills/{id}     — 删除 Skill
- POST   /api/skills/import   — 从 GitHub URL 导入
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from server.app.skill_store import get_skill_store, scan_builtin_skills, get_builtin_skill_content
from server.app.api.log_routes import verify_admin

skill_router = APIRouter(prefix="/api/skills", tags=["skills"])


# ── Request Models ───────────────────────────────────────────────────────

class SkillCreateRequest(BaseModel):
    name: str
    description: str = ""
    version: str = "1.0"
    author: str = ""
    module: str = "global"
    enabled: bool = True
    content: str = ""


class SkillUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    version: str | None = None
    author: str | None = None
    module: str | None = None
    enabled: bool | None = None
    content: str | None = None


class SkillToggleRequest(BaseModel):
    enabled: bool


class SkillImportRequest(BaseModel):
    url: str
    """
    GitHub URL — supports multiple formats:
    - https://github.com/user/repo  (looks for skills/*/SKILL.md or SKILL.md)
    - https://github.com/user/repo/blob/main/path/to/SKILL.md
    - https://raw.githubusercontent.com/user/repo/main/path/to/file.md
    - Any direct raw URL to a .md file
    """


# ── Routes ───────────────────────────────────────────────────────────────

@skill_router.get("")
async def list_skills():
    """List all skills — custom (file-based) + built-in (intent_bridge / prompt_bridge)."""
    store = get_skill_store()
    custom = store.list_all()
    # Tag custom skills
    for s in custom:
        s.setdefault("source", "custom")
        s.setdefault("layer", "")
        s.setdefault("readonly", False)
        s.setdefault("keywords", "")
    builtin = scan_builtin_skills()
    return {"skills": builtin + custom}


@skill_router.get("/{skill_id:path}")
async def get_skill(skill_id: str):
    """Get a single skill with full content. Supports compound IDs (ib:xxx, pb:xxx)."""
    # Built-in skill — compound ID
    if skill_id.startswith("ib:") or skill_id.startswith("pb:"):
        content = get_builtin_skill_content(skill_id)
        if content is None:
            raise HTTPException(404, f"Built-in skill '{skill_id}' not found")
        # Find metadata from scan
        for s in scan_builtin_skills():
            if s["id"] == skill_id:
                return {**s, "content": content, "raw": content}
        return {"id": skill_id, "content": content, "raw": content, "readonly": True}

    # Custom skill — validate id to prevent path traversal
    if not re.fullmatch(r"[\w\-]+", skill_id):
        raise HTTPException(400, f"Invalid skill id: {skill_id}")
    store = get_skill_store()
    skill = store.get(skill_id)
    if not skill:
        raise HTTPException(404, f"Skill '{skill_id}' not found")
    skill.setdefault("source", "custom")
    skill.setdefault("readonly", False)
    return skill


@skill_router.post("", dependencies=[Depends(verify_admin)])
async def create_skill(req: SkillCreateRequest):
    """Create a new skill from provided content."""
    store = get_skill_store()
    meta = {
        "name": req.name,
        "description": req.description,
        "version": req.version,
        "author": req.author,
        "module": req.module,
        "enabled": req.enabled,
    }
    result = store.save(None, meta, req.content)
    return result


@skill_router.put("/{skill_id}", dependencies=[Depends(verify_admin)])
async def update_skill(skill_id: str, req: SkillUpdateRequest):
    """Update an existing skill."""
    store = get_skill_store()
    meta_updates = {}
    for field in ["name", "description", "version", "author", "module", "enabled"]:
        val = getattr(req, field, None)
        if val is not None:
            meta_updates[field] = val

    result = store.update(skill_id, meta=meta_updates or None, content=req.content)
    if not result:
        raise HTTPException(404, f"Skill '{skill_id}' not found")
    return result


@skill_router.patch("/{skill_id}", dependencies=[Depends(verify_admin)])
async def toggle_skill(skill_id: str, req: SkillToggleRequest):
    """Enable or disable a skill."""
    store = get_skill_store()
    result = store.toggle(skill_id, req.enabled)
    if not result:
        raise HTTPException(404, f"Skill '{skill_id}' not found")
    return result


@skill_router.delete("/{skill_id}", dependencies=[Depends(verify_admin)])
async def delete_skill(skill_id: str):
    """Delete a skill."""
    store = get_skill_store()
    if not store.delete(skill_id):
        raise HTTPException(404, f"Skill '{skill_id}' not found")
    return {"status": "ok", "deleted": skill_id}


# ── GitHub Import ────────────────────────────────────────────────────────

def _resolve_github_raw_url(url: str) -> str:
    """Convert various GitHub URL formats to raw content URLs."""
    url = url.strip().rstrip("/")

    # Already a raw URL
    if urlparse(url).hostname == "raw.githubusercontent.com":
        return url

    # github.com/user/repo/blob/branch/path → raw
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)", url)
    if m:
        user, repo, branch, path = m.groups()
        return f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}"

    # github.com/user/repo → try common skill file locations
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/?$", url)
    if m:
        user, repo = m.groups()
        # PUA-style: skills/{repo}/SKILL.md, then root SKILL.md, then README.md
        return f"https://raw.githubusercontent.com/{user}/{repo}/main/skills/{repo}/SKILL.md"

    raise HTTPException(400, "Only GitHub URLs are allowed")


@skill_router.post("/import", dependencies=[Depends(verify_admin)])
async def import_skill(req: SkillImportRequest):
    """Import a skill from a GitHub URL."""
    parsed = urlparse(req.url.strip())
    if parsed.scheme != "https" or parsed.hostname not in ("github.com", "raw.githubusercontent.com"):
        raise HTTPException(400, "Only GitHub URLs are allowed")

    raw_url = _resolve_github_raw_url(req.url)

    # Try multiple paths if the first fails (for repo-level URLs)
    urls_to_try = [raw_url]
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/?$", req.url.strip().rstrip("/"))
    if m:
        user, repo = m.groups()
        urls_to_try = [
            f"https://raw.githubusercontent.com/{user}/{repo}/main/skills/{repo}/SKILL.md",
            f"https://raw.githubusercontent.com/{user}/{repo}/master/skills/{repo}/SKILL.md",
            f"https://raw.githubusercontent.com/{user}/{repo}/main/SKILL.md",
            f"https://raw.githubusercontent.com/{user}/{repo}/master/SKILL.md",
            f"https://raw.githubusercontent.com/{user}/{repo}/main/README.md",
        ]

    text = None
    used_url = ""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for try_url in urls_to_try:
            try:
                resp = await client.get(try_url)
                if resp.status_code == 200:
                    text = resp.text
                    used_url = try_url
                    break
            except Exception:
                continue

    if not text:
        raise HTTPException(
            400,
            f"Could not fetch skill from {req.url}. Tried: {', '.join(urls_to_try[:3])}...",
        )

    store = get_skill_store()
    result = store.import_from_text(text, source=req.url)
    return {**result, "imported_from": used_url}
