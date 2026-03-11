"""
Revit SDK sample code parser — V2 pipeline.

Stages:
  Phase 0 : Project discovery  — os.walk finds CS/ dirs, filters boilerplate files
  Phase 1 : ReadMe analysis    — Gemini Flash parses RTF readme → target_files, key_classes, apis
  Phase 1b: Tree-sitter match  — extract methods from target classes only
  Phase 2 : Golden code gen    — Claude produces {summary, content} per project
  Phase 3 : Persist            — save to SQLite sdk_info table

Public API (in Colab):
    from pipeline.sdk_parser.extract import run_sdk_pipeline, save_sdk_to_sqlite

    results = run_sdk_pipeline("/content/sdk_samples/Samples", config)
    save_sdk_to_sqlite(results, "./data/sqlite/revit_sdk.db")
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

try:
    from tqdm.auto import tqdm as _tqdm
except ImportError:
    _tqdm = None  # type: ignore[assignment]

from pipeline.llm_client import LLMClient, create_llm_client

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_IGNORED_FILENAMES = {
    "assemblyinfo.cs",
}

_IGNORED_SUFFIXES = (
    ".designer.cs",
    "assemblyattributes.cs",
)

_IGNORED_PARENT_DIRS = {"obj", "bin", "properties"}

_PRINT_LOCK = threading.Lock()

_NUM_WORKERS_PHASE1 = 10
_NUM_WORKERS_PHASE2 = 5


# ---------------------------------------------------------------------------
# Phase 0: Project Discovery
# ---------------------------------------------------------------------------

def _is_ignored_file(path: Path) -> bool:
    """Return True if this .cs file is boilerplate and should be skipped."""
    name_lower = path.name.lower()
    if name_lower in _IGNORED_FILENAMES:
        return True
    if any(name_lower.endswith(sfx) for sfx in _IGNORED_SUFFIXES):
        return True
    for part in path.parts:
        if part.lower() in _IGNORED_PARENT_DIRS:
            return True
    return False


def discover_sdk_projects(sdk_root: str) -> list[dict[str, Any]]:
    """
    Phase 0: Walk sdk_root and discover all C# SDK projects.

    A project is identified by a directory named 'CS' (case-insensitive) that
    contains at least one .cs file.  VB.NET directories (containing .vb files)
    are skipped.

    Returns:
        list of dicts:
            project_name  : dotted name relative to sdk_root (e.g. "Events.AutoStamp")
            project_path  : Path to project root (parent of CS dir)
            cs_dir        : Path to CS directory
            readme_path   : Path to readme file or None
            all_cs_files  : list[Path] of non-ignored .cs files inside cs_dir
    """
    sdk_root_path = Path(sdk_root)
    projects: list[dict[str, Any]] = []

    # Pre-collect candidate dirs with a progress bar
    candidate_dirs: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(sdk_root_path):
        dp = Path(dirpath)
        # Skip VB.NET directories
        if any(f.lower().endswith(".vb") for f in filenames):
            dirnames[:] = []
            continue
        if dp.name.lower() == "cs" and any(f.lower().endswith(".cs") for f in filenames):
            candidate_dirs.append(dp)
            dirnames[:] = []  # don't recurse deeper from a CS dir

    print(f"Phase 0: found {len(candidate_dirs)} C# project directories under {sdk_root}")

    bar = (
        _tqdm(candidate_dirs, desc="Discovering projects", unit="dir", dynamic_ncols=True)
        if _tqdm else candidate_dirs
    )

    for cs_dir in bar:
        project_root = cs_dir.parent
        rel = project_root.relative_to(sdk_root_path)
        project_name = str(rel).replace(os.sep, ".").replace("/", ".")

        # Find readme (search entire project tree)
        readme_path: Path | None = None
        for root, _, files in os.walk(project_root):
            for fname in files:
                if "readme" in fname.lower():
                    readme_path = Path(root) / fname
                    break
            if readme_path:
                break

        # Collect non-ignored .cs files from cs_dir only
        all_cs_files = [
            p for p in cs_dir.rglob("*.cs")
            if not _is_ignored_file(p)
        ]

        if not all_cs_files:
            continue

        projects.append({
            "project_name": project_name,
            "project_path": project_root,
            "cs_dir": cs_dir,
            "readme_path": readme_path,
            "all_cs_files": all_cs_files,
        })

    print(f"Phase 0 complete: {len(projects)} valid projects with .cs files")
    return projects


# ---------------------------------------------------------------------------
# Phase 1: ReadMe Analysis (Gemini Flash)
# ---------------------------------------------------------------------------

_README_SYSTEM_PROMPT = (
    "You are an AI assistant specializing in codebase analysis, an expert at extracting "
    "structured data from technical documentation."
)

_README_USER_PROMPT_TPL = """\
# ROLE
You are an AI assistant specializing in codebase analysis, an expert at extracting structured data from technical documentation.

# GOAL
Parse the provided ReadMe text to extract key identifiers for code. Output will be used programmatically by an automated code retrieval system.

# INSTRUCTIONS
1. Carefully analyze the text provided.
2. Extract the following three categories:
   - `target_files`: list of all project source filenames (e.g. `.cs` files) explicitly mentioned.
   - `key_classes_and_methods`: list of custom class or method names responsible for core functionality.
   - `mentioned_apis`: list of key API classes from external frameworks (e.g. `Autodesk.Revit.DB.View`).
3. Format output as a single strict JSON object.
4. If no information is found for a field, use an empty list `[]`. Do not omit the key.
5. Response MUST contain ONLY the raw JSON object — no markdown, no explanation.

# EXAMPLE
Input: "This tool is in `Processor.cs`. Core logic is in `DataParser` using `Autodesk.Revit.DB.Transaction`."
Output: {{"target_files": ["Processor.cs"], "key_classes_and_methods": ["DataParser"], "mentioned_apis": ["Autodesk.Revit.DB.Transaction"]}}

# README CONTENT
{readme_text}
"""


def _parse_readme_text(readme_path: Path) -> str:
    """Read a readme file, converting RTF to plain text if needed."""
    if readme_path is None:
        return ""
    try:
        raw = readme_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""

    if readme_path.suffix.lower() == ".rtf":
        try:
            from striprtf.striprtf import rtf_to_text  # type: ignore
            return rtf_to_text(raw)
        except Exception:
            # striprtf unavailable or parse error — strip RTF control words with regex
            plain = re.sub(r"\{\\[^{}]*\}", "", raw)
            plain = re.sub(r"\\[a-z]+\d*\s?", "", plain)
            plain = re.sub(r"[{}]", "", plain)
            return plain.strip()
    return raw


def analyze_readme(
    project: dict[str, Any],
    gemini_client: LLMClient,
) -> dict[str, Any] | None:
    """
    Phase 1: Parse a project's readme with Gemini Flash.

    Returns dict with keys: target_files, key_classes_and_methods, mentioned_apis
    Returns None if readme is missing or LLM call fails.
    """
    readme_path: Path | None = project.get("readme_path")
    if readme_path is None:
        return None

    readme_text = _parse_readme_text(readme_path)
    if not readme_text.strip():
        return None

    prompt = _README_USER_PROMPT_TPL.format(readme_text=readme_text[:3000])

    try:
        raw = gemini_client.generate_text(prompt, system_prompt=_README_SYSTEM_PROMPT)
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw.strip())
        result = json.loads(raw)
        result.setdefault("target_files", [])
        result.setdefault("key_classes_and_methods", [])
        result.setdefault("mentioned_apis", [])
        return result
    except Exception as e:
        with _PRINT_LOCK:
            print(f"  [ReadMe LLM error] {project['project_name']}: {e}")
        return None


# ---------------------------------------------------------------------------
# Phase 1b: Tree-sitter targeted extraction
# ---------------------------------------------------------------------------

def _extract_methods_for_class(code: str, target_class: str) -> list[str]:
    """
    Extract method bodies belonging to `target_class` using tree-sitter DFS.
    Falls back to extracting ALL methods if class is not found.
    """
    try:
        from tree_sitter import Language, Parser  # type: ignore
        import tree_sitter_c_sharp  # type: ignore
    except Exception:
        return _fallback_extract_methods(code)

    cleaned = code.lstrip("\ufeff")
    try:
        lang = Language(tree_sitter_c_sharp.language())
        parser = Parser(lang)
        tree = parser.parse(bytes(cleaned, "utf8"))
    except Exception:
        return _fallback_extract_methods(code)

    target_lower = target_class.lower()
    methods: list[str] = []

    # DFS: find class_declaration whose name matches target_class,
    # then collect all method_declaration nodes within it.
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "class_declaration":
            # Get class name
            class_name_node = next(
                (c for c in node.children if c.type == "identifier"), None
            )
            if class_name_node:
                name = cleaned[class_name_node.start_byte: class_name_node.end_byte]
                if name.lower() == target_lower or target_lower in name.lower():
                    # Extract all methods from this class
                    inner_stack = [node]
                    while inner_stack:
                        inner = inner_stack.pop()
                        if inner.type == "method_declaration":
                            text = cleaned[inner.start_byte: inner.end_byte].strip()
                            if text:
                                methods.append(text)
                            continue
                        inner_stack.extend(reversed(inner.children))
                    if methods:
                        return methods
            # Even if class name didn't match, recurse into it
            stack.extend(reversed(node.children))
        else:
            stack.extend(reversed(node.children))

    # Class not found by name — fall back to all methods
    if not methods:
        return _extract_all_methods(cleaned, tree)
    return methods


def _extract_all_methods(cleaned: str, tree: Any) -> list[str]:
    """Extract all method_declaration nodes from a parsed tree."""
    methods: list[str] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "method_declaration":
            text = cleaned[node.start_byte: node.end_byte].strip()
            if text:
                methods.append(text)
            continue
        stack.extend(reversed(node.children))
    return methods


def _fallback_extract_methods(code: str) -> list[str]:
    """Regex-based method extraction fallback."""
    cleaned = re.sub(r"^\s*/\*[\s\S]*?\*/\s*", "", code, count=1, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*using\s+[^\n;]+;\s*$", "", cleaned, flags=re.MULTILINE)
    return [cleaned.strip()] if cleaned.strip() else []


def match_and_extract(
    project: dict[str, Any],
    readme_analysis: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Phase 1b: Match target files from ReadMe analysis to actual .cs files,
    extract methods for each key class, and return structured code data.

    Returns None if no target files could be matched.
    """
    target_files: list[str] = readme_analysis.get("target_files", [])
    key_classes: list[str] = readme_analysis.get("key_classes_and_methods", [])
    all_cs_files: list[Path] = project["all_cs_files"]

    # Build a filename -> Path map
    file_map: dict[str, Path] = {p.name: p for p in all_cs_files}

    all_class_details: list[dict[str, Any]] = []

    for class_name in key_classes:
        class_found = False
        for target_file in target_files:
            full_path = file_map.get(target_file)
            if full_path is None:
                # Try case-insensitive match
                for fname, fpath in file_map.items():
                    if fname.lower() == target_file.lower():
                        full_path = fpath
                        break

            if full_path is None:
                continue

            try:
                code = full_path.read_text(encoding="utf-8-sig", errors="ignore")
            except Exception:
                continue

            methods = _extract_methods_for_class(code, class_name)
            if methods:
                all_class_details.append({
                    "class_name": class_name,
                    "filename": full_path.name,
                    "methods": methods,
                })
                class_found = True
                break

        if not class_found:
            with _PRINT_LOCK:
                print(f"  [match] cant find class: {class_name} in {project['project_name']}")

    if not all_class_details:
        return None

    return {
        "project_name": project["project_name"],
        "readme_summary": readme_analysis,
        "code_details": all_class_details,
    }


# ---------------------------------------------------------------------------
# Phase 2: Golden Code Generation (Claude)
# ---------------------------------------------------------------------------

_GOLDEN_CODE_SYSTEM = (
    "You are an expert C# software architect and technical writer specializing in the "
    "Autodesk Revit API. You excel at identifying core logic and refactoring it into "
    "clear, concise, and educational code examples. Always reply with a single valid "
    "JSON object and nothing else."
)

_GOLDEN_CODE_PROMPT_TPL = """\
# ROLE
You are an expert C# software architect and technical writer specializing in the Autodesk Revit API.

# GOAL
Analyze the ReadMe context and raw C# code details. Identify the single most relevant code block representing the core functionality described in the ReadMe, then synthesize it into a clean, reusable, documented 'Golden Code Snippet' for a RAG knowledge base.

# CONTEXT
<ReadMeContext>
{readme_summary}
</ReadMeContext>

<RawCodeDetails>
{detail_code_analysis}
</RawCodeDetails>

# STEP-BY-STEP INSTRUCTIONS

1. **Analyze Goal & Context**: Read `<ReadMeContext>` to understand the project's main purpose and key APIs.

2. **Identify Core Logic Block**: Examine `<RawCodeDetails>`.
   - If multiple methods exist, select the ONE that most directly implements the core functionality from the Project Summary.
   - Prioritize methods with significant API logic over simple event handlers or UI boilerplate.
   - Call the selected block the **"Target Code"**.

3. **Filter the Target Code** — remove all:
   - UI interaction code (MessageBox, TaskDialog, control properties like .Text or .Checked)
   - Logging/debugging (Console.WriteLine, Debug.WriteLine)
   - Generic file I/O (unless it IS the core API function)
   - IExternalCommand.Execute boilerplate — assume a Document `doc` is available

4. **Refactor for Reusability**:
   - Create a clear, descriptive method signature with typed parameters
   - Convert hardcoded values into parameters with descriptive names
   - Ensure Transaction pattern wraps all model modifications

5. **Generate XML Documentation** (`/// <summary>...`):
   - `<summary>` describes what the FINAL refactored code does
   - `<param>` for ALL parameters
   - `<returns>` if method returns a value

6. **Final Output**: Single complete syntactically-correct C# method block.

# OUTPUT FORMAT
Return ONLY a JSON object with exactly two keys:
{{
  "summary": "One sentence English description of what this code demonstrates and which Revit APIs it uses.",
  "content": "The complete clean C# method with XML documentation comments."
}}

JSON only. No markdown. No explanation outside the JSON.
"""


def generate_golden_code(
    matched_data: dict[str, Any],
    claude_client: LLMClient,
) -> dict[str, Any] | None:
    """
    Phase 2: Use Claude to produce a golden {summary, content} code snippet.

    Returns dict with keys: project_name, summary, content, mentioned_apis
    Returns None if LLM fails or returns unusable output.
    """
    readme_summary = json.dumps(matched_data["readme_summary"], indent=2, ensure_ascii=False)
    code_details = json.dumps(matched_data["code_details"], indent=2, ensure_ascii=False)

    # Guard against token overflow (rough char limit)
    if len(code_details) > 120_000:
        with _PRINT_LOCK:
            print(f"  [skip] {matched_data['project_name']}: code_details too large ({len(code_details)} chars)")
        return None

    prompt = _GOLDEN_CODE_PROMPT_TPL.format(
        readme_summary=readme_summary[:3000],
        detail_code_analysis=code_details[:80_000],
    )

    try:
        raw = claude_client.generate_text(prompt, system_prompt=_GOLDEN_CODE_SYSTEM)
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw.strip())
        result = json.loads(raw)
        summary = (result.get("summary") or "").strip()
        content = (result.get("content") or "").strip()
        if not summary or not content:
            return None
        return {
            "project_name": matched_data["project_name"],
            "summary": summary,
            "content": content,
            "mentioned_apis": json.dumps(
                matched_data["readme_summary"].get("mentioned_apis", []),
                ensure_ascii=False,
            ),
        }
    except Exception as e:
        with _PRINT_LOCK:
            print(f"  [Claude error] {matched_data['project_name']}: {e}")
        return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_sdk_pipeline(
    sdk_root: str,
    config: dict[str, Any],
    max_projects: int | None = None,
    num_workers_phase1: int = _NUM_WORKERS_PHASE1,
    num_workers_phase2: int = _NUM_WORKERS_PHASE2,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """
    Run the full SDK pruning pipeline.

    Args:
        sdk_root         : path to Samples root (e.g. /content/sdk_samples/Samples)
        config           : full config.yaml dict
        max_projects     : limit projects processed (None = all)
        num_workers_phase1: parallel threads for ReadMe analysis (Gemini Flash)
        num_workers_phase2: parallel threads for golden code gen (Claude)
        verbose          : print progress

    Returns:
        list of dicts: {project_name, summary, content, mentioned_apis}
    """
    # ── Clients ─────────────────────────────────────────────────────────────
    sdk_cfg = config.get("sdk", {})
    stage1_provider = sdk_cfg.get("stage1_provider", "gemini_flash")
    stage2_provider = sdk_cfg.get("stage2_provider", "claude")

    gemini_client = create_llm_client(config, provider_override=stage1_provider)
    gemini_client.max_tokens = 1024

    claude_client = create_llm_client(config, provider_override=stage2_provider)
    claude_client.max_tokens = 4096

    if verbose:
        print(f"\nSDK Pipeline — sdk_root: {sdk_root}")
        print(f"  Stage 1 model (ReadMe): {gemini_client.model}")
        print(f"  Stage 2 model (Code):   {claude_client.model}")

    # ── Phase 0: Discovery ──────────────────────────────────────────────────
    projects = discover_sdk_projects(sdk_root)
    if max_projects:
        projects = projects[:max_projects]

    total = len(projects)
    if verbose:
        print(f"\nPhase 1: ReadMe analysis — {total} projects | {num_workers_phase1} threads")

    # ── Phase 1: ReadMe analysis (parallel) ────────────────────────────────
    readme_results: dict[str, dict | None] = {}

    pbar1 = (
        _tqdm(total=total, desc="ReadMe analysis", unit="proj", dynamic_ncols=True)
        if _tqdm and verbose else None
    )

    def _run_readme(proj: dict) -> tuple[str, dict | None]:
        r = analyze_readme(proj, gemini_client)
        if pbar1:
            pbar1.update(1)
        return proj["project_name"], r

    with ThreadPoolExecutor(max_workers=num_workers_phase1) as ex:
        futs = {ex.submit(_run_readme, p): p for p in projects}
        for fut in as_completed(futs):
            name, result = fut.result()
            readme_results[name] = result

    if pbar1:
        pbar1.close()

    # ── Phase 1b: Match & extract ──────────────────────────────────────────
    matched_list: list[dict[str, Any]] = []

    if verbose:
        print(f"\nPhase 1b: Matching files and extracting methods")

    pbar1b = (
        _tqdm(projects, desc="Matching files", unit="proj", dynamic_ncols=True)
        if _tqdm and verbose else projects
    )

    skipped_no_readme = 0
    skipped_no_match = 0

    for proj in pbar1b:
        name = proj["project_name"]
        readme_analysis = readme_results.get(name)
        if readme_analysis is None:
            skipped_no_readme += 1
            continue
        matched = match_and_extract(proj, readme_analysis)
        if matched is None:
            skipped_no_match += 1
            continue
        matched_list.append(matched)

    if verbose:
        print(f"  Matched: {len(matched_list)} | "
              f"No readme: {skipped_no_readme} | "
              f"No file match: {skipped_no_match}")

    # ── Phase 2: Golden code generation (parallel) ─────────────────────────
    if verbose:
        print(f"\nPhase 2: Golden code generation — {len(matched_list)} projects | "
              f"{num_workers_phase2} threads")

    golden_results: list[dict[str, Any]] = []

    pbar2 = (
        _tqdm(total=len(matched_list), desc="Golden code generation",
              unit="proj", dynamic_ncols=True)
        if _tqdm and verbose else None
    )

    def _run_golden(m: dict) -> dict | None:
        r = generate_golden_code(m, claude_client)
        if pbar2:
            pbar2.update(1)
        return r

    with ThreadPoolExecutor(max_workers=num_workers_phase2) as ex:
        futs2 = {ex.submit(_run_golden, m): m for m in matched_list}
        for fut in as_completed(futs2):
            r = fut.result()
            if r:
                golden_results.append(r)

    if pbar2:
        pbar2.close()

    if verbose:
        print(f"\n{'='*60}")
        print(f"SDK Pipeline complete")
        print(f"  Projects discovered : {total}")
        print(f"  ReadMe matched      : {len(matched_list)}")
        print(f"  Golden snippets     : {len(golden_results)}")
        print(f"{'='*60}")

    return golden_results


# ---------------------------------------------------------------------------
# Persist to SQLite
# ---------------------------------------------------------------------------

def save_sdk_to_sqlite(
    results: list[dict[str, Any]],
    db_path: str,
) -> None:
    """
    Save golden code results to SQLite sdk_info table.

    Schema:
        id             INTEGER PK
        project        TEXT
        summary        TEXT   (used as embedding text)
        content        TEXT   (clean C# code snippet)
        mentioned_apis TEXT   (JSON array string)
    """
    if not results:
        print("No results to save.")
        return

    db = Path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)

    if db.exists():
        db.unlink()
        print(f"Removed old DB: {db}")

    conn = sqlite3.connect(str(db))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE sdk_info (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            project        TEXT,
            summary        TEXT,
            content        TEXT,
            mentioned_apis TEXT
        )
    """)

    SQL = "INSERT INTO sdk_info (project, summary, content, mentioned_apis) VALUES (?, ?, ?, ?)"

    pbar = (
        _tqdm(results, desc="Saving to SQLite", unit="rec", dynamic_ncols=True)
        if _tqdm else results
    )

    batch: list[tuple] = []
    for item in pbar:
        batch.append((
            item.get("project_name"),
            item.get("summary"),
            item.get("content"),
            item.get("mentioned_apis"),
        ))

    cursor.executemany(SQL, batch)
    conn.commit()
    conn.close()
    print(f"Saved {len(results)} records to {db}")
