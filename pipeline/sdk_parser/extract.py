"""
Revit SDK 示例代码解析器
对应原 revit_sdk_prund/sdk_prunding.ipynb + extra_data/ 的功能：
- 遍历 SDK 目录提取 .cs 文件和 ReadMe.rtf
- tree-sitter 提取代码块
- LLM 清洗代码 + JSON 格式化输出
- 存入 SQLite

使用方法（在 Colab 中）：
    from pipeline.sdk_parser.extract import extract_all_sdk_projects
    from pipeline.sdk_parser.clean import clean_code_with_llm
    from pipeline.sdk_parser.store import save_to_sqlite

    raw_data = extract_all_sdk_projects("./data/raw/sdk_samples/")
    cleaned = clean_code_with_llm(raw_data, config)
    save_to_sqlite(cleaned, "./data/sqlite/revit_sdk.db")
"""
import os
import json
import sqlite3
from pathlib import Path


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
            results.append({
                "filename": cs_file.name,
                "code": code,
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
    使用 tree-sitter 提取代码中的有效代码块
    移除 using / namespace 等无关内容

    TODO: 从 sdk_prunding.ipynb 迁移 tree-sitter 逻辑
    """
    # TODO: 实现 tree-sitter 解析
    # 临时方案：直接返回完整代码
    return [code]


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
