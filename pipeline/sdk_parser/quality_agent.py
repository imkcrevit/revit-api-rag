"""
SDK 数据质量与元数据 Agent（多线程）

使用 Claude Sonnet 对 SDK 项目进行整体分析：
  - 项目级：读取 README，总结项目用途、涉及的 API 类、设计模式、用例分类
  - 文件级：每个 .cs 文件的用途、关键类、关键方法

目的：预计算丰富的元数据，减少最终用户 RAG 查询时的 token 消耗，提高检索准确度。

并发策略：
  Phase 1: 项目级分析 — 5 线程并发（每个项目 1 次 Claude 调用）
  Phase 2: 文件级批量分析 — 5 线程并发（每 5 文件 1 次 Claude 调用）

对外接口：
    from pipeline.sdk_parser.quality_agent import run_sdk_quality_agent

    enriched = run_sdk_quality_agent(sdk_data, config)
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

from pipeline.llm_client import LLMClient, create_llm_client

# ─────────────────────────────────────────────────────────────
# 并发配置
# ─────────────────────────────────────────────────────────────
_NUM_WORKERS = 5
_FILE_BATCH_SIZE = 5
_print_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────
# 项目级分析
# ─────────────────────────────────────────────────────────────

_PROJECT_SYSTEM = (
    "You are a Revit SDK code analyst. You analyze C# sample projects that demonstrate "
    "Revit API usage patterns. Always reply with a single valid JSON object and nothing else."
)

_PROJECT_PROMPT_TPL = """\
Analyze this Revit SDK sample project.

Project name: {project_name}
Number of C# files: {file_count}

README content:
{readme}

File listing with code previews:
{file_previews}

Return a JSON object with:
  "project_summary"   : 2-3 sentence description of what this project demonstrates (in English)
  "api_classes_used"  : list of main Revit API classes/types used (e.g. ["Document", "FamilyInstance", "Transaction"])
  "key_patterns"      : list of design patterns demonstrated (e.g. ["ExternalCommand", "FilteredElementCollector", "Event handling"])
  "use_case_category" : one of: "geometry", "family", "view", "structure", "mep", "annotation", "export", "utility", "ui", "analysis", "database", "other"

JSON only. No markdown. No explanation."""


def _analyze_project(
    project_name: str,
    readme: str,
    files: list[dict[str, Any]],
    claude: LLMClient,
) -> dict[str, Any]:
    """用 Claude 分析单个 SDK 项目，返回项目级元数据。"""
    previews = []
    for f in files[:20]:
        code = (f.get("clean_code") or f.get("code") or "")[:800]
        previews.append(f"--- {f.get('filename', '?')} ---\n{code}")
    file_previews = "\n\n".join(previews)

    readme_trimmed = (readme or "(no README)")[:2000]
    if len(file_previews) > 8000:
        file_previews = file_previews[:8000] + "\n... (truncated)"

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
# 文件级批量分析（5 文件一组，减少 API 调用）
# ─────────────────────────────────────────────────────────────

_FILE_BATCH_SYSTEM = (
    "You are a Revit SDK code analyst. Analyze C# source files and extract metadata. "
    "Always reply with a single valid JSON array and nothing else."
)

_FILE_BATCH_PROMPT_TPL = """\
Analyze these C# files from the Revit SDK project "{project_name}".
Project context: {project_summary}

{file_sections}

Return a JSON array with one object per file (in the same order):
[
  {{
    "filename": "exact filename",
    "file_purpose": "1-sentence description of what this file does",
    "key_classes": ["ClassName1", "ClassName2"],
    "key_methods": ["MethodName1 - brief description", "MethodName2 - brief description"]
  }}
]

JSON array only. No markdown. No explanation."""


def _analyze_file_batch(
    project_name: str,
    project_summary: str,
    file_batch: list[dict[str, Any]],
    claude: LLMClient,
) -> list[dict[str, Any]]:
    """用 Claude 批量分析一组文件（最多 5 个），返回文件级元数据列表。"""
    sections = []
    for idx, f in enumerate(file_batch, 1):
        code = (f.get("clean_code") or f.get("code") or "")[:2000]
        sections.append(f"File {idx}: {f.get('filename', '?')}\n```csharp\n{code}\n```")

    prompt = _FILE_BATCH_PROMPT_TPL.format(
        project_name=project_name,
        project_summary=project_summary[:300],
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
# 项目级 worker（Phase 1：项目分析 + 文件批量分析）
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
    处理单个项目：项目级分析 + 所有文件的批量分析。
    在线程中执行，返回该项目的完整结果。
    """
    readme = (files[0].get("readme") or "") if files else ""

    if verbose:
        with _print_lock:
            print(f"  [{project_idx:>3}/{total_projects}] {proj_name:<40} ({len(files)} files)")

    # ── Phase 1a: Project-level analysis ──
    proj_meta = _analyze_project(proj_name, readme, files, claude)

    if verbose and proj_meta.get("_error"):
        with _print_lock:
            print(f"         project error: {proj_meta['_error'][:80]}")

    proj_summary = proj_meta.get("project_summary", "")
    api_classes = json.dumps(proj_meta.get("api_classes_used", []), ensure_ascii=False)
    key_patterns = json.dumps(proj_meta.get("key_patterns", []), ensure_ascii=False)
    category = proj_meta.get("use_case_category", "other")

    # ── Phase 1b: File-level analysis (batched, sequential within this project) ──
    file_meta_map: dict[str, dict] = {}
    for batch_start in range(0, len(files), _FILE_BATCH_SIZE):
        batch = files[batch_start:batch_start + _FILE_BATCH_SIZE]
        batch_results = _analyze_file_batch(proj_name, proj_summary, batch, claude)

        for j, f in enumerate(batch):
            fname = f.get("filename", "")
            if j < len(batch_results):
                file_meta_map[fname] = batch_results[j]

    # ── Merge ──
    enriched_files: list[dict[str, Any]] = []
    for f in files:
        fname = f.get("filename", "")
        fm = file_meta_map.get(fname, {})

        enriched = dict(f)
        enriched["project_summary"] = proj_summary
        enriched["api_classes_used"] = api_classes
        enriched["key_patterns"] = key_patterns
        enriched["use_case_category"] = category
        enriched["file_purpose"] = fm.get("file_purpose", "")
        enriched["key_classes"] = json.dumps(fm.get("key_classes", []), ensure_ascii=False)
        enriched["key_methods"] = json.dumps(fm.get("key_methods", []), ensure_ascii=False)

        enriched_files.append(enriched)

    return {
        "project_name": proj_name,
        "enriched_files": enriched_files,
        "file_meta_count": len(file_meta_map),
    }


# ─────────────────────────────────────────────────────────────
# 公开入口
# ─────────────────────────────────────────────────────────────

def run_sdk_quality_agent(
    sdk_data: list[dict[str, Any]],
    config: dict[str, Any],
    max_projects: int | None = None,
    num_workers: int = _NUM_WORKERS,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """
    对 SDK 数据运行 Claude 分析 Agent，生成项目级和文件级元数据（多线程并发）。

    Args:
        sdk_data:     extract_all_sdk_projects() 的输出
        config:       完整的 config.yaml dict
        max_projects: 限制处理项目数（调试用），None = 全部
        num_workers:  并发线程数（默认 5）
        verbose:      是否打印进度

    Returns:
        与 sdk_data 结构相同的列表，每条记录增加：
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

    # Group by project
    projects: dict[str, list[dict]] = defaultdict(list)
    for item in sdk_data:
        projects[item.get("project", "unknown")].append(item)

    project_names = list(projects.keys())
    if max_projects:
        project_names = project_names[:max_projects]

    total_files = sum(len(projects[p]) for p in project_names)
    total_projects = len(project_names)

    if verbose:
        print(f"SDK Quality Agent — {total_projects} projects, {total_files} files, {num_workers} threads")
        print(f"  Model: {claude.model}")
        print()

    # ── Parallel project processing ──────────────────────────────
    results_map: dict[tuple[str, str], dict] = {}
    project_meta_count = 0
    file_meta_count = 0

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {}
        for pi, proj_name in enumerate(project_names, 1):
            fut = executor.submit(
                _project_worker,
                proj_name, projects[proj_name], claude,
                verbose, pi, total_projects,
            )
            futures[fut] = proj_name

        for fut in as_completed(futures):
            proj_name = futures[fut]
            try:
                result = fut.result()
                project_meta_count += 1
                file_meta_count += result["file_meta_count"]

                for enriched in result["enriched_files"]:
                    key = (enriched.get("project", ""), enriched.get("filename", ""))
                    results_map[key] = enriched

            except Exception as e:
                if verbose:
                    with _print_lock:
                        print(f"  ERROR [{proj_name}]: {e}")
                # Keep original items for this project
                for f in projects[proj_name]:
                    key = (f.get("project", ""), f.get("filename", ""))
                    results_map[key] = f

    if verbose:
        print(f"\n{'='*60}")
        print(f"SDK Quality Agent 完成")
        print(f"  并发线程数    : {num_workers}")
        print(f"  项目分析调用  : {project_meta_count}")
        print(f"  文件分析条数  : {file_meta_count}")
        print(f"{'='*60}")

    # Return in original order
    results = []
    for item in sdk_data:
        key = (item.get("project", "unknown"), item.get("filename", ""))
        enriched = results_map.get(key)
        results.append(enriched if enriched else item)

    return results


def save_quality_to_sqlite(enriched_data: list[dict[str, Any]], db_path: str):
    """
    将 SDK 质量分析结果更新到已有的 revit_sdk SQLite 表中。
    使用 ALTER TABLE 添加新列（幂等），然后按 (project, filename) 更新。
    """
    if not enriched_data:
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    new_cols = [
        "project_summary", "api_classes_used", "key_patterns",
        "use_case_category", "file_purpose", "key_classes", "key_methods",
    ]
    for col in new_cols:
        try:
            cursor.execute(f"ALTER TABLE revit_sdk ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass

    updated = 0
    for item in enriched_data:
        if not item.get("project_summary") and not item.get("file_purpose"):
            continue

        cursor.execute(
            """UPDATE revit_sdk
               SET project_summary=?, api_classes_used=?, key_patterns=?,
                   use_case_category=?, file_purpose=?, key_classes=?, key_methods=?
               WHERE project=? AND filename=?""",
            (
                item.get("project_summary", ""),
                item.get("api_classes_used", "[]"),
                item.get("key_patterns", "[]"),
                item.get("use_case_category", ""),
                item.get("file_purpose", ""),
                item.get("key_classes", "[]"),
                item.get("key_methods", "[]"),
                item.get("project", ""),
                item.get("filename", ""),
            ),
        )
        if cursor.rowcount > 0:
            updated += 1

    conn.commit()
    conn.close()
    print(f"SDK 元数据已更新 {updated}/{len(enriched_data)} 条到 {db_path}")
