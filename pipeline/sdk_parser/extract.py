"""
Revit SDK sample code parser.

Usage (in Colab):
    from pipeline.sdk_parser.extract import extract_all_sdk_projects, clean_code_with_llm, save_to_sqlite

    raw_data = extract_all_sdk_projects("./data/raw/sdk_samples/")
    cleaned = clean_code_with_llm(raw_data, config)
    save_to_sqlite(cleaned, "./data/sqlite/revit_sdk.db")
"""
import os
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from pipeline.llm_client import create_llm_client


def extract_cs_files(project_dir: str) -> list[dict]:
    """
    Extract .cs files and ReadMe from a single SDK project directory.

    Returns:
        list of {"filename": str, "code": str, "clean_code": str, "readme": str|None, "project": str}
    """
    project_dir = Path(project_dir)
    results = []

    readme_text = None
    for readme_name in ["ReadMe.rtf", "ReadMe.txt", "README.md"]:
        readme_path = project_dir / readme_name
        if readme_path.exists():
            try:
                readme_text = readme_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass
            break

    for cs_file in project_dir.rglob("*.cs"):
        try:
            code = cs_file.read_text(encoding="utf-8", errors="ignore")
            code_blocks = parse_code_blocks(code)
            clean_code = "\n\n".join(code_blocks) if code_blocks else ""
            results.append({
                "filename": cs_file.name,
                "code": code,
                "clean_code": clean_code,
                "readme": readme_text,
                "project": project_dir.name,
            })
        except Exception as e:
            print(f"  读取失败: {cs_file} - {e}")

    return results


def extract_all_sdk_projects(sdk_root: str) -> list[dict]:
    """
    Walk all project directories under sdk_root and extract .cs files.

    Returns:
        list of extracted code data dicts
    """
    sdk_root = Path(sdk_root)
    all_data = []

    project_dirs = [d for d in sdk_root.iterdir() if d.is_dir()]
    print(f"Found {len(project_dirs)} SDK projects")

    for project_dir in project_dirs:
        data = extract_cs_files(str(project_dir))
        all_data.extend(data)

    print(f"Extracted {len(all_data)} .cs files total")
    return all_data


def parse_code_blocks(code: str) -> list[str]:
    """
    Extract method_declaration blocks from C# source via direct AST walk.

    Uses iterative DFS on the tree-sitter AST instead of Query.matches(),
    so it works with all tree-sitter versions (Query.matches was removed
    in >= 0.21). Falls back to regex pruning when tree-sitter is unavailable.
    """
    try:
        from tree_sitter import Language, Parser  # type: ignore
        import tree_sitter_c_sharp  # type: ignore
    except Exception:
        return _fallback_prune_code(code)

    cleaned = code.lstrip("\ufeff")

    try:
        csharp_language = Language(tree_sitter_c_sharp.language())
        parser = Parser(csharp_language)
        tree = parser.parse(bytes(cleaned, "utf8"))
    except Exception:
        return _fallback_prune_code(code)

    # Iterative DFS: collect all method_declaration nodes anywhere in tree.
    # Avoids Query.matches() which was removed in tree-sitter >= 0.21.
    blocks: list[str] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "method_declaration":
            text = cleaned[node.start_byte: node.end_byte].strip()
            if text:
                blocks.append(text)
            # Don't descend — C# has no nested method declarations
            continue
        stack.extend(reversed(node.children))

    if not blocks:
        return _fallback_prune_code(code)

    return blocks


def _fallback_prune_code(code: str) -> list[str]:
    """
    Regex-based pruning fallback when tree-sitter is unavailable.
    Strips license headers, using directives, and namespace wrappers.
    """
    cleaned = code.lstrip("\ufeff")

    license_pattern = r"^\s*/\*[\s\S]*?\*/\s*"
    cleaned = re.sub(license_pattern, "", cleaned, count=1, flags=re.MULTILINE)

    using_pattern = r"^\s*using\s+[^\n;]+;\s*$"
    cleaned = re.sub(using_pattern, "", cleaned, flags=re.MULTILINE)

    namespace_pattern = r"^\s*namespace\s+[^\n{]+{\s*$"
    cleaned = re.sub(namespace_pattern, "", cleaned, flags=re.MULTILINE)

    cleaned_lines = cleaned.splitlines()
    result_lines: list[str] = []
    empty_count = 0
    for line in cleaned_lines:
        if line.strip() == "":
            empty_count += 1
            if empty_count <= 2:
                result_lines.append("")
        else:
            empty_count = 0
            result_lines.append(line.rstrip())

    cleaned = "\n".join(result_lines).strip()
    return [cleaned] if cleaned else []


def clean_code_with_llm(raw_data: list[dict], config: dict[str, Any]) -> list[dict]:
    """
    Use an LLM to generate a short English description for each SDK file.

    Input : list from extract_all_sdk_projects() (fields: code, clean_code, readme, project, filename)
    Output: same list with an added "description" field, ready for save_to_sqlite()
    """
    if not raw_data:
        return []

    client = create_llm_client(config)
    cleaned_results: list[dict] = []

    for item in raw_data:
        code = item.get("clean_code") or item.get("code") or ""
        readme = (item.get("readme") or "").strip()
        project = item.get("project") or ""
        filename = item.get("filename") or ""

        description = ""
        if code:
            prompt_parts = [
                "You are an expert Revit SDK code analyst.",
                "Based on the C# sample code and optional ReadMe below, write a concise 2-3 sentence",
                "English description covering: what scenario this sample demonstrates, which main",
                "Revit APIs are involved, and why it is useful to a developer.",
                "",
            ]
            if project or filename:
                prompt_parts.append(f"Sample project: {project} / {filename}")
            if readme:
                prompt_parts.append("ReadMe (may be empty):")
                prompt_parts.append(readme[:2000])

            prompt_parts.append("")
            # clean_code is already pruned to method bodies — send the full text.
            # If clean_code was empty we fell back to the raw file, cap at 20 000 chars.
            prompt_parts.append("Code (pruned, full):")
            prompt_parts.append(code if item.get("clean_code", "").strip() else code[:20_000])
            prompt_parts.append("")
            prompt_parts.append("Output the description text only. No JSON, no code, no bullet points.")

            prompt = "\n".join(prompt_parts)

            try:
                description = client.generate_text(prompt).strip()
            except Exception as e:
                print(f"LLM error: {project}/{filename} - {e}")
                description = ""

        new_item = dict(item)
        new_item["description"] = description
        cleaned_results.append(new_item)

    return cleaned_results


def save_to_sqlite(sdk_data: list[dict], db_path: str):
    """Save cleaned SDK data to SQLite."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS revit_sdk (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT,
            filename TEXT,
            code TEXT,
            clean_code TEXT,
            readme TEXT,
            description TEXT
        )
    """)

    for item in sdk_data:
        cursor.execute(
            "INSERT INTO revit_sdk (project, filename, code, clean_code, readme, description) VALUES (?, ?, ?, ?, ?, ?)",
            (item.get("project"), item.get("filename"), item.get("code"),
             item.get("clean_code"), item.get("readme"), item.get("description"))
        )

    conn.commit()
    conn.close()
    print(f"Saved {len(sdk_data)} records to {db_path}")
