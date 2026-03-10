"""
Revit API 文档解析器
从 RevitAPI.chm 解压后的 HTML 文件中提取结构化数据

HTML 结构（Microsoft Help CHM 格式）：
- <title> 包含类/方法名称
- <meta name="Microsoft.Help.Id"> 包含完整 API 路径
- <meta name="container"> 包含命名空间
- <meta name="System.Keywords"> 包含关键词
- <table class="titleTable"> 包含标题
- <table class="members"> 包含成员列表（方法/属性/事件）
- <div id="TopicContent"> 包含主要内容
- 各种 section 包含 Summary / Remarks / Parameters / Exceptions 等
"""
import os
import re
import sqlite3
from pathlib import Path
from bs4 import BeautifulSoup


def parse_single_html(html_path: str) -> dict | None:
    """
    解析单个 API HTML 文件，提取结构化数据

    Returns:
        dict with keys: name, full_id, namespace, content_type, info, summary,
                        remark, parameters, exceptions, members, keywords
        如果不是有效的 API 文档页面返回 None
    """
    try:
        with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return None

    soup = BeautifulSoup(content, "html.parser")

    # --- 提取 meta 信息 ---
    title_tag = soup.find("title")
    if not title_tag or not title_tag.string:
        return None

    title = title_tag.string.strip()
    if not title:
        return None

    # Microsoft.Help.Id = 完整 API 路径，如 "Methods.T:Autodesk.Revit.DB.ExportLayerInfo"
    help_id_meta = soup.find("meta", attrs={"name": "Microsoft.Help.Id"})
    full_id = help_id_meta["content"] if help_id_meta and help_id_meta.get("content") else ""

    # 命名空间
    container_meta = soup.find("meta", attrs={"name": "container"})
    namespace = container_meta["content"] if container_meta and container_meta.get("content") else ""

    # 内容类型（Reference, Concepts 等）
    content_type_meta = soup.find("meta", attrs={"name": "Microsoft.Help.ContentType"})
    content_type = content_type_meta["content"] if content_type_meta and content_type_meta.get("content") else ""

    # 关键词
    keywords_meta = soup.find("meta", attrs={"name": "System.Keywords"})
    keywords = keywords_meta["content"] if keywords_meta and keywords_meta.get("content") else ""

    # --- 提取正文内容 ---
    topic_content = soup.find("div", id="TopicContent")
    if not topic_content:
        return None

    # 提取第一段描述（紧跟标题后的 <p>）
    info = ""
    first_p = topic_content.find("p")
    if first_p:
        info = _clean_text(first_p.get_text())

    # --- 提取各 Section ---
    summary = _extract_section(topic_content, "summary")
    remark = _extract_section(topic_content, "remarks")
    parameters = _extract_section(topic_content, "parameters")
    exceptions = _extract_section(topic_content, "exceptions")
    return_value = _extract_section(topic_content, "return")
    syntax = _extract_syntax(topic_content)

    # --- 提取成员表格（方法/属性/事件列表）---
    members = _extract_members_table(topic_content)

    # 跳过完全空的页面
    if not info and not summary and not members and not syntax:
        return None

    return {
        "name": title,
        "full_id": full_id,
        "namespace": namespace,
        "content_type": content_type,
        "keywords": keywords,
        "info": info,
        "summary": summary,
        "remark": remark,
        "parameters": parameters,
        "exceptions": exceptions,
        "return_value": return_value,
        "syntax": syntax,
        "members": members,
    }


def _clean_text(text: str) -> str:
    """清理提取的文本：去除多余空白"""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _extract_section(topic_content, section_name: str) -> str:
    """
    提取指定 section 的内容
    CHM HTML 中 section 通常是 <div class="collapsibleSection"> 
    前面有一个 <span class="collapsibleRegionTitle"> 包含标题
    """
    # 方式1：通过 section 标题文本查找
    section_keywords = {
        "summary": ["Summary", "Description"],
        "remarks": ["Remarks", "Remark"],
        "parameters": ["Parameters"],
        "exceptions": ["Exceptions"],
        "return": ["Return Value", "Returns"],
    }

    keywords = section_keywords.get(section_name, [section_name])

    for span in topic_content.find_all("span", class_="collapsibleRegionTitle"):
        span_text = span.get_text().strip()
        if any(kw.lower() in span_text.lower() for kw in keywords):
            # 找到对应的 section div（通常是下一个兄弟 div）
            section_div = span.find_parent("div")
            if section_div:
                next_div = section_div.find_next_sibling("div", class_="collapsibleSection")
                if next_div:
                    return _clean_text(next_div.get_text())

    # 方式2：通过 id 查找（有些页面用 id 标识）
    for div in topic_content.find_all("div"):
        div_id = div.get("id", "").lower()
        if any(kw.lower() in div_id for kw in keywords):
            return _clean_text(div.get_text())

    return ""


def _extract_syntax(topic_content) -> str:
    """提取语法/代码块"""
    results = []

    # 查找代码片段 div
    for code_div in topic_content.find_all("div", class_="codeSnippetContainerCode"):
        code = code_div.get_text().strip()
        if code:
            results.append(code)

    # 也查找 <pre> 和 <code> 标签
    if not results:
        for pre in topic_content.find_all("pre"):
            code = pre.get_text().strip()
            if code:
                results.append(code)

    return "\n---\n".join(results)


def _extract_members_table(topic_content) -> str:
    """
    提取成员列表表格（方法/属性/事件）
    表格 class="members"，每行有 Name 和 Description
    """
    members = []

    for table in topic_content.find_all("table", class_="members"):
        rows = table.find_all("tr")
        for row in rows[1:]:  # 跳过表头
            cells = row.find_all("td")
            if len(cells) >= 3:
                name = _clean_text(cells[1].get_text())
                desc = _clean_text(cells[2].get_text())
                if name:
                    members.append(f"{name}: {desc}")
            elif len(cells) >= 2:
                name = _clean_text(cells[0].get_text())
                desc = _clean_text(cells[1].get_text())
                if name:
                    members.append(f"{name}: {desc}")

    return "\n".join(members)


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
        if i % 1000 == 0:
            print(f"  解析进度: {i}/{len(html_files)}")
        try:
            data = parse_single_html(str(html_file))
            if data:
                results.append(data)
        except Exception as e:
            if i < 10:  # 只打印前10个错误，避免刷屏
                print(f"  解析失败: {html_file.name} - {e}")

    print(f"成功解析 {len(results)} 条 API 数据")
    return results


def save_to_sqlite(api_data: list[dict], db_path: str):
    """
    将解析后的 API 数据存入 SQLite
    """
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS revit_api (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            full_id TEXT,
            namespace TEXT,
            content_type TEXT,
            keywords TEXT,
            info TEXT,
            summary TEXT,
            remark TEXT,
            parameters TEXT,
            exceptions TEXT,
            return_value TEXT,
            syntax TEXT,
            members TEXT
        )
    """)

    # 清空旧数据
    cursor.execute("DELETE FROM revit_api")

    for item in api_data:
        cursor.execute(
            """INSERT INTO revit_api 
               (name, full_id, namespace, content_type, keywords, info, 
                summary, remark, parameters, exceptions, return_value, syntax, members) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (item.get("name"), item.get("full_id"), item.get("namespace"),
             item.get("content_type"), item.get("keywords"), item.get("info"),
             item.get("summary"), item.get("remark"), item.get("parameters"),
             item.get("exceptions"), item.get("return_value"), item.get("syntax"),
             item.get("members"))
        )

    conn.commit()
    conn.close()
    print(f"已保存 {len(api_data)} 条数据到 {db_path}")