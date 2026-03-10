"""
Revit SDK 示例代码解析器
对应原 revit_sdk_prund/sdk_prunding.ipynb + extra_data/ 的功能：
- 遍历 SDK 目录提取 .cs 文件和 ReadMe.rtf
- 基于正则的代码剪枝（P0）
- LLM 清洗代码并生成 description（P1）
- 存入 SQLite

使用方法（在 Colab 中）：
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
    从单个 SDK 项目目录提取 .cs 文件和 ReadMe

    Returns:
        list of {"filename": str, "code": str, "readme": str|None}
    """
    project_dir = Path(project_dir)
    results = []

    # 读取 ReadMe
    readme_text = None
    for readme_name in ["ReadMe.rtf", "ReadMe.txt", "README.md"]:
        readme_path = project_dir / readme_name
        if readme_path.exists():
            try:
                readme_text = readme_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass
            break

    # 读取所有 .cs 文件
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
    遍历 SDK 根目录下所有项目

    Args:
        sdk_root: SDK 示例代码根目录

    Returns:
        list of extracted code data
    """
    sdk_root = Path(sdk_root)
    all_data = []

    # SDK 通常按项目文件夹组织
    project_dirs = [d for d in sdk_root.iterdir() if d.is_dir()]
    print(f"找到 {len(project_dirs)} 个 SDK 项目")

    for project_dir in project_dirs:
        data = extract_cs_files(str(project_dir))
        all_data.extend(data)

    print(f"共提取 {len(all_data)} 个 .cs 文件")
    return all_data


def parse_code_blocks(code: str) -> list[str]:
    """
    使用 tree-sitter 提取代码中的有效代码块（P2）
    - 移除 using / namespace 等无关内容
    - 提取 class/method 级别的“金代码块”
    """
    try:
        from tree_sitter import Language, Parser, Query  # type: ignore
        import tree_sitter_c_sharp  # type: ignore
    except Exception:
        # 如果未安装 tree-sitter，则回退到简单正则剪枝
        return _fallback_prune_code(code)

    cleaned_for_parse = code
    if cleaned_for_parse.startswith("\ufeff"):
        cleaned_for_parse = cleaned_for_parse.lstrip("\ufeff")

    csharp_language = Language(tree_sitter_c_sharp.language())
    parser = Parser(csharp_language)
    tree = parser.parse(bytes(cleaned_for_parse, "utf8"))
    root_node = tree.root_node

    # 查询所有 class_declaration / method_declaration
    query_string = """
        (compilation_unit
            (namespace_declaration
                body: (declaration_list
                    (class_declaration
                        name: (identifier) @class.name
                        body: (declaration_list
                            (method_declaration) @method.node
                        )
                    )
                )
            )
        )
    """
    query = Query(csharp_language, query_string)
    matches = query.matches(root_node)

    blocks: list[str] = []
    for match in matches:
        values = match[1]
        method_nodes = values.get("method.node") or []
        for node in method_nodes:
            text = cleaned_for_parse[node.start_byte : node.end_byte]
            text = text.strip()
            if text:
                blocks.append(text)

    # 如果 tree-sitter 没有提取到任何块，回退到简单剪枝结果
    if not blocks:
        return _fallback_prune_code(code)

    return blocks


def _fallback_prune_code(code: str) -> list[str]:
    """
    P0 正则剪枝实现，作为 tree-sitter 不可用时的回退方案。
    """
    cleaned = code

    if cleaned.startswith("\ufeff"):
        cleaned = cleaned.lstrip("\ufeff")

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
    使用 LLM 对 SDK 代码进行清洗，生成简短的功能描述 description。

    - 输入: extract_all_sdk_projects() 的原始数据（包含 code / clean_code / readme / project / filename）
    - 输出: 追加了 description 字段的列表，用于后续存入 SQLite
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
            # 组合一个简短的上下文给 LLM
            prompt_parts = [
                "你是 Revit SDK 示例代码的分析助手。",
                "请根据下面的 C# 示例代码和可选的 ReadMe 内容，用 1-3 句话用中文概括这个示例的功能和用途。",
                "重点说明：这个示例演示了什么场景、涉及哪些主要 API、对使用者有什么帮助。",
                "",
            ]
            if project or filename:
                prompt_parts.append(f"示例项目: {project} / {filename}")
            if readme:
                prompt_parts.append("ReadMe 摘要（可能为空）:")
                # 控制 ReadMe 长度，避免 prompt 过长
                trimmed_readme = readme[:2000]
                prompt_parts.append(trimmed_readme)

            prompt_parts.append("")
            prompt_parts.append("下面是代码片段（已做基础剪枝）:")
            trimmed_code = code[:3000]
            prompt_parts.append(trimmed_code)
            prompt_parts.append("")
            prompt_parts.append("请直接输出概括性的描述文本，不要输出代码或 JSON。")

            prompt = "\n".join(prompt_parts)

            try:
                description = client.generate_text(prompt).strip()
            except Exception as e:
                # LLM 失败时保留空描述，不影响整体流程
                print(f"LLM 清洗失败: {project}/{filename} - {e}")
                description = ""

        new_item = dict(item)
        new_item["description"] = description
        cleaned_results.append(new_item)

    return cleaned_results


def save_to_sqlite(sdk_data: list[dict], db_path: str):
    """将清洗后的 SDK 数据存入 SQLite"""
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
    print(f"已保存 {len(sdk_data)} 条数据到 {db_path}")
