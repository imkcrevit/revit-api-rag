"""
SDK data quality and metadata Agent (multi-threaded).

Uses Claude Sonnet to analyse SDK sample projects at two levels:
  - Project level : README + file listing → project_summary, api_classes_used,
                    key_patterns, use_case_category
  - File level    : every .cs file (batched 5 at a time) → file_purpose,
                    key_classes, key_methods

Concurrency:
  Phase 1: one Claude call per project — num_workers threads in parallel
  Phase 2: one Claude call per 5-file batch — sequential within each project

Public API:
    from pipeline.sdk_parser.quality_agent import run_sdk_quality_agent, save_enriched_to_sqlite

    enriched = run_sdk_quality_agent(sdk_data, config)
    save_enriched_to_sqlite(enriched, db_path)   # deletes old DB, writes fresh
"""
from __future__ import annotations

import json
import re
import sqlite3
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from collections import defaultdict

try:
    from tqdm.auto import tqdm
except ImportError:
    tqdm = None  # type: ignore[assignment]

from prompts import load_prompt

from pipeline.llm_client import LLMClient, create_llm_client

# ─────────────────────────────────────────────────────────────
# Concurrency settings
# ─────────────────────────────────────────────────────────────
_NUM_WORKERS    = 5
_FILE_BATCH_SIZE = 5
_print_lock     = threading.Lock()


# ─────────────────────────────────────────────────────────────
# Project-level analysis
# ─────────────────────────────────────────────────────────────

_PROJECT_SYSTEM = load_prompt("pipeline.sdk_quality_project_system.md")
_PROJECT_PROMPT_TPL = load_prompt("pipeline.sdk_quality_project.md")


def _analyze_project(
    project_name: str,
    readme: str,
    files: list[dict[str, Any]],
    claude: LLMClient,
) -> dict[str, Any]:
    """Analyse a single SDK project with Claude; returns project-level metadata."""
    previews = []
    total_preview_chars = 0
    for f in files[:20]:
        # Use full clean_code (method bodies only, already pruned).
        # Fall back to raw code capped at 2000 chars if clean_code is empty.
        code = f.get("clean_code") or ""
        if not code.strip():
            code = (f.get("code") or "")[:2000]
        # Cap individual file preview at 2000 chars to keep total prompt reasonable.
        code = code[:2000]
        total_preview_chars += len(code)
        previews.append(f"--- {f.get('filename', '?')} ---\n{code}")
        if total_preview_chars >= 12_000:
            previews.append("... (remaining files omitted for brevity)")
            break

    file_previews = "\n\n".join(previews)
    readme_trimmed = (readme or "(no README)")[:2000]

    prompt = _PROJECT_PROMPT_TPL.format(
        project_name=project_name,
        file_count=len(files),
        readme=readme_trimmed,
        file_previews=file_previews,
    )

    try:
        raw = claude.generate_text(prompt, system_prompt=_PROJECT_SYSTEM)
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw.strip())
        result = json.loads(raw)
        result.setdefault("project_summary", "")
        result.setdefault("api_classes_used", [])
        result.setdefault("key_patterns", [])
        result.setdefault("use_case_category", "other")
        return result
    except Exception as e:
        return {
            "project_summary": "",
            "api_classes_used": [],
            "key_patterns": [],
            "use_case_category": "other",
            "_error": str(e),
        }


# ─────────────────────────────────────────────────────────────
# File-level batch analysis (5 files per Claude call)
# ─────────────────────────────────────────────────────────────

_FILE_BATCH_SYSTEM = load_prompt("pipeline.sdk_quality_file_system.md")
_FILE_BATCH_PROMPT_TPL = load_prompt("pipeline.sdk_quality_file_batch.md")


def _analyze_file_batch(
    project_name: str,
    project_summary: str,
    file_batch: list[dict[str, Any]],
    claude: LLMClient,
) -> list[dict[str, Any]]:
    """
    Analyse a batch of .cs files with Claude (up to _FILE_BATCH_SIZE files).
    Uses full clean_code per file (no truncation); falls back to raw code.
    """
    sections = []
    for idx, f in enumerate(file_batch, 1):
        # Full clean_code (method bodies); fall back to raw if empty.
        code = f.get("clean_code") or ""
        if not code.strip():
            code = (f.get("code") or "")[:5000]
        sections.append(f"File {idx}: {f.get('filename', '?')}\n```csharp\n{code}\n```")

    prompt = _FILE_BATCH_PROMPT_TPL.format(
        project_name=project_name,
        project_summary=(project_summary or "")[:300],
        file_sections="\n\n".join(sections),
    )

    try:
        raw = claude.generate_text(prompt, system_prompt=_FILE_BATCH_SYSTEM)
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw.strip())
        results = json.loads(raw)
        if isinstance(results, list):
            return results
        return []
    except Exception as e:
        return [{"_error": str(e)} for _ in file_batch]


# ─────────────────────────────────────────────────────────────
# Per-project worker (Phase 1 + 2 combined)
# ─────────────────────────────────────────────────────────────

def _project_worker(
    proj_name: str,
    files: list[dict[str, Any]],
    claude: LLMClient,
    verbose: bool,
    project_idx: int,
    total_projects: int,
) -> dict[str, Any]:
    """
    Process a single project: project-level analysis + all file batches.
    Runs in a thread; returns the fully enriched file list for the project.
    """
    readme = (files[0].get("readme") or "") if files else ""

    if verbose and not tqdm:
        with _print_lock:
            print(f"  [{project_idx:>3}/{total_projects}] {proj_name:<40} ({len(files)} files)")

    # ── Project-level analysis ──
    proj_meta = _analyze_project(proj_name, readme, files, claude)

    if verbose and proj_meta.get("_error"):
        with _print_lock:
            print(f"         project error: {proj_meta['_error'][:80]}")

    proj_summary  = proj_meta.get("project_summary", "")
    api_classes   = json.dumps(proj_meta.get("api_classes_used", []), ensure_ascii=False)
    key_patterns  = json.dumps(proj_meta.get("key_patterns", []),      ensure_ascii=False)
    category      = proj_meta.get("use_case_category", "other")

    # ── File-level analysis (batched, sequential within this project) ──
    file_meta_map: dict[str, dict] = {}
    for batch_start in range(0, len(files), _FILE_BATCH_SIZE):
        batch = files[batch_start: batch_start + _FILE_BATCH_SIZE]
        batch_results = _analyze_file_batch(proj_name, proj_summary, batch, claude)
        for j, f in enumerate(batch):
            fname = f.get("filename", "")
            if j < len(batch_results):
                file_meta_map[fname] = batch_results[j]

    # ── Merge project + file metadata ──
    enriched_files: list[dict[str, Any]] = []
    for f in files:
        fname   = f.get("filename", "")
        fm      = file_meta_map.get(fname, {})
        enriched = dict(f)
        enriched["project_summary"]   = proj_summary
        enriched["api_classes_used"]  = api_classes
        enriched["key_patterns"]      = key_patterns
        enriched["use_case_category"] = category
        enriched["file_purpose"]      = fm.get("file_purpose", "")
        enriched["key_classes"]       = json.dumps(fm.get("key_classes",  []), ensure_ascii=False)
        enriched["key_methods"]       = json.dumps(fm.get("key_methods",  []), ensure_ascii=False)
        enriched_files.append(enriched)

    return {
        "project_name":    proj_name,
        "enriched_files":  enriched_files,
        "file_meta_count": len(file_meta_map),
    }


# ─────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────

def run_sdk_quality_agent(
    sdk_data: list[dict[str, Any]],
    config: dict[str, Any],
    max_projects: int | None = None,
    num_workers: int = _NUM_WORKERS,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """
    Run Claude analysis on SDK data to generate project- and file-level metadata.

    Args:
        sdk_data:     output of extract_all_sdk_projects()
        config:       full config.yaml dict
        max_projects: limit number of projects processed (debug); None = all
        num_workers:  concurrent threads (default 5)
        verbose:      print progress

    Returns:
        Same list as sdk_data with added fields per record:
          "project_summary"    : str
          "api_classes_used"   : str  (JSON array string)
          "key_patterns"       : str  (JSON array string)
          "use_case_category"  : str
          "file_purpose"       : str
          "key_classes"        : str  (JSON array string)
          "key_methods"        : str  (JSON array string)
    """
    claude = create_llm_client(config, provider_override="claude")
    claude.max_tokens = 4096

    # Group files by project
    projects: dict[str, list[dict]] = defaultdict(list)
    for item in sdk_data:
        projects[item.get("project", "unknown")].append(item)

    project_names = list(projects.keys())
    if max_projects:
        project_names = project_names[:max_projects]

    total_files    = sum(len(projects[p]) for p in project_names)
    total_projects = len(project_names)

    if verbose:
        print(f"SDK Quality Agent — {total_projects} projects | {total_files} files | {num_workers} threads")
        print(f"  Model: {claude.model}")
        print()

    # ── Parallel project processing ──────────────────────────────
    results_map: dict[tuple[str, str], dict] = {}
    project_meta_count = 0
    file_meta_count    = 0

    pbar_proj = (
        tqdm(total=total_projects, desc="Projects", unit="proj", dynamic_ncols=True)
        if tqdm and verbose else None
    )
    pbar_file = (
        tqdm(total=total_files, desc="Files    ", unit="file", dynamic_ncols=True)
        if tqdm and verbose else None
    )

    def _on_project_done(future):
        proj_name = futures.get(future, "")
        n_files = len(projects.get(proj_name, []))
        if pbar_proj:
            pbar_proj.update(1)
        if pbar_file:
            pbar_file.update(n_files)

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures: dict = {}
        for pi, proj_name in enumerate(project_names, 1):
            fut = executor.submit(
                _project_worker,
                proj_name, projects[proj_name], claude,
                verbose, pi, total_projects,
            )
            fut.add_done_callback(_on_project_done)
            futures[fut] = proj_name

        for fut in as_completed(futures):
            proj_name = futures[fut]
            try:
                result = fut.result()
                project_meta_count += 1
                file_meta_count    += result["file_meta_count"]
                for enriched in result["enriched_files"]:
                    key = (enriched.get("project", ""), enriched.get("filename", ""))
                    results_map[key] = enriched
            except Exception as e:
                if verbose:
                    with _print_lock:
                        print(f"  ERROR [{proj_name}]: {e}")
                for f in projects[proj_name]:
                    key = (f.get("project", ""), f.get("filename", ""))
                    results_map[key] = f

    if pbar_proj:
        pbar_proj.close()
    if pbar_file:
        pbar_file.close()

    if verbose:
        print(f"\n{'='*60}")
        print(f"SDK Quality Agent complete")
        print(f"  Threads          : {num_workers}")
        print(f"  Projects analysed: {project_meta_count}")
        print(f"  Files analysed   : {file_meta_count}")
        print(f"{'='*60}")

    # Return in original sdk_data order
    results = []
    for item in sdk_data:
        key = (item.get("project", "unknown"), item.get("filename", ""))
        results.append(results_map.get(key, item))
    return results


def save_enriched_to_sqlite(enriched_data: list[dict[str, Any]], db_path: str):
    """
    Delete the existing DB (if any) and write all enriched SDK records fresh.

    Schema includes both extraction columns (code, clean_code, readme, description)
    and quality-agent columns (project_summary, api_classes_used, …).
    A tqdm progress bar tracks the INSERT.
    """
    try:
        from tqdm.auto import tqdm as _tqdm
    except ImportError:
        _tqdm = None  # type: ignore[assignment]

    if not enriched_data:
        print("No data to save.")
        return

    db = Path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)

    if db.exists():
        db.unlink()
        print(f"Removed old DB: {db}")

    conn   = sqlite3.connect(str(db))
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE revit_sdk (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            project          TEXT,
            filename         TEXT,
            code             TEXT,
            clean_code       TEXT,
            readme           TEXT,
            description      TEXT,
            project_summary  TEXT,
            api_classes_used TEXT,
            key_patterns     TEXT,
            use_case_category TEXT,
            file_purpose     TEXT,
            key_classes      TEXT,
            key_methods      TEXT
        )
    """)

    pbar = (
        _tqdm(total=len(enriched_data), desc="Saving to SQLite", unit="rec", dynamic_ncols=True)
        if _tqdm else None
    )

    BATCH = 500
    batch: list = []
    SQL = """INSERT INTO revit_sdk
               (project, filename, code, clean_code, readme, description,
                project_summary, api_classes_used, key_patterns, use_case_category,
                file_purpose, key_classes, key_methods)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

    for item in enriched_data:
        batch.append((
            item.get("project"),
            item.get("filename"),
            item.get("code"),
            item.get("clean_code"),
            item.get("readme"),
            item.get("description"),
            item.get("project_summary"),
            item.get("api_classes_used"),
            item.get("key_patterns"),
            item.get("use_case_category"),
            item.get("file_purpose"),
            item.get("key_classes"),
            item.get("key_methods"),
        ))
        if len(batch) >= BATCH:
            cursor.executemany(SQL, batch)
            conn.commit()
            if pbar:
                pbar.update(len(batch))
            batch = []

    if batch:
        cursor.executemany(SQL, batch)
        conn.commit()
        if pbar:
            pbar.update(len(batch))

    if pbar:
        pbar.close()

    conn.close()
    print(f"Saved {len(enriched_data)} records to {db}")


# Keep old name as an alias for backward compatibility
def save_quality_to_sqlite(enriched_data: list[dict[str, Any]], db_path: str):
    """Deprecated alias — use save_enriched_to_sqlite instead."""
    save_enriched_to_sqlite(enriched_data, db_path)
