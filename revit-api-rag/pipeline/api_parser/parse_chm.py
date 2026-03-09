"""
Revit API 文档解析器
从 RevitAPI.chm 解压后的 HTML 文件中提取结构化数据

对应原 split_revit.ipynb 的功能：
- 解析 HTML 提取 class name / info / summary / remark / parameters / exception
- 存入 SQLite 数据库

使用方法（在 Colab 中）：
    from pipeline.api_parser.parse_chm import parse_all_api_html
    from pipeline.api_parser.store import save_to_sqlite

    api_data = parse_all_api_html("./data/raw/api_html/")
    save_to_sqlite(api_data, "./data/sqlite/revit_api.db")
"""
import os
import sqlite3
from pathlib import Path
from bs4 import BeautifulSoup


def parse_single_html(html_path: str) -> dict | None:
    """
    解析单个 API HTML 文件，提取结构化数据

    Returns:
        dict with keys: name, info, summary, remark, parameters, exception
        如果解析失败返回 None
    """
    # TODO: 从你原来的 split_revit.ipynb 中迁移解析逻辑
    # 原始代码大致流程：
    # 1. BeautifulSoup 解析 HTML
    # 2. 提取 class name（标题）
    # 3. 提取 class info（描述）
    # 4. 提取 summary（摘要）
    # 5. 提取 remark（备注）
    # 6. 提取 parameters（参数列表）
    # 7. 提取 exception（异常信息）
    raise NotImplementedError("请从 split_revit.ipynb 迁移解析逻辑")


def parse_all_api_html(html_dir: str) -> list[dict]:
    """
    解析目录下所有 API HTML 文件

    Args:
        html_dir: CHM 解压后的 HTML 文件目录

    Returns:
        list of parsed API data dicts
    """
    results = []
    html_dir = Path(html_dir)

    html_files = list(html_dir.glob("**/*.html")) + list(html_dir.glob("**/*.htm"))
    print(f"找到 {len(html_files)} 个 HTML 文件")

    for i, html_file in enumerate(html_files):
        if i % 100 == 0:
            print(f"  解析进度: {i}/{len(html_files)}")
        try:
            data = parse_single_html(str(html_file))
            if data:
                results.append(data)
        except Exception as e:
            print(f"  解析失败: {html_file.name} - {e}")

    print(f"成功解析 {len(results)} 条 API 数据")
    return results


def save_to_sqlite(api_data: list[dict], db_path: str):
    """
    将解析后的 API 数据存入 SQLite

    Args:
        api_data: parse_all_api_html 的返回值
        db_path: SQLite 数据库文件路径
    """
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS revit_api (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            info TEXT,
            summary TEXT,
            remark TEXT,
            parameters TEXT,
            exception TEXT
        )
    """)

    for item in api_data:
        cursor.execute(
            "INSERT INTO revit_api (name, info, summary, remark, parameters, exception) VALUES (?, ?, ?, ?, ?, ?)",
            (item.get("name"), item.get("info"), item.get("summary"),
             item.get("remark"), item.get("parameters"), item.get("exception"))
        )

    conn.commit()
    conn.close()
    print(f"已保存 {len(api_data)} 条数据到 {db_path}")
