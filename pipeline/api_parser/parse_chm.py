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

    # --- 提取标题 ---
    title_tag = soup.find("title")
    if not title_tag or not title_tag.string:
        return None

    title = title_tag.string.strip()
    if not title:
        return None

    # --- 提取 API 全名（优先使用 Microsoft.Help.F1） ---
    full_id = _extract_api_fullname(soup, title)
    if full_id is None:
        # F1 meta 表明是命名空间目录页等噪音，直接跳过
        return None

    # --- 过滤构造函数页面（ctor） ---
    # 构造函数仅描述如何实例化对象，不含方法逻辑，对 RAG 检索意义有限
    if _is_constructor_page(title, full_id):
        return None

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

    # 优先使用结构化参数提取，回退到通用 section 抽取
    parameters_structured = _extract_parameters_structured(soup)
    if parameters_structured:
        parameters = parameters_structured
    else:
        parameters = _extract_section(topic_content, "parameters")
    exceptions = _extract_section(topic_content, "exceptions")
    return_value = _extract_section(topic_content, "return")

    # --- 提取 C# 签名 / 语法 ---
    csharp_signature = _extract_csharp_signature(soup)
    syntax = csharp_signature or _extract_syntax(topic_content)

    # --- 提取成员表格（方法/属性/事件列表）---
    members = _extract_members_table(topic_content)

    # 跳过完全空的页面
    if not info and not summary and not members and not syntax:
        return None

    # 旧版逻辑：如果既没有 C# 签名，又没有成员列表，多为目录/命名空间页，跳过
    if not csharp_signature and not members:
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
        "_source_file": str(html_path),  # exact path used by quality_agent to load raw HTML
    }


def _clean_text(text: str) -> str:
    """清理提取的文本：去除多余空白"""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _is_constructor_page(title: str, full_id: str) -> bool:
    """
    判断是否是构造函数页面，应跳过该类页面（不含业务逻辑，对 RAG 无意义）。

    匹配规则：
    1. full_id 含 '.#ctor' 或 '#ctor('（CHM 标准 M: 签名中的构造函数标识）
    2. title 末尾词为 'Constructor' 或 'Constructors'（避免误伤 "ReconstructionMethod" 等）
    """
    full_id_lower = full_id.lower()

    # 规则1：full_id 中的 #ctor（方法签名标准格式，精确匹配避免误判）
    if ".#ctor" in full_id_lower or "#ctor(" in full_id_lower or full_id_lower.endswith("#ctor"):
        return True

    # 规则2：title 末尾是 Constructor / Constructors（单词边界，忽略大小写）
    # 使用 $ 锚定，防止 "ReconstructionXxx" 误触发
    if re.search(r"\bconstructors?\s*$", title, flags=re.IGNORECASE):
        return True

    return False


def _extract_api_fullname(soup: BeautifulSoup, title: str) -> str | None:
    """
    提取 API 全名
    优先使用 Microsoft.Help.F1，兼容旧版逻辑，并过滤命名空间目录页。
    """
    metas = soup.find_all("meta", attrs={"name": "Microsoft.Help.F1"})
    if metas:
        # 单个 F1 且标题包含 NameSpace，视为命名空间目录页，跳过
        if len(metas) == 1:
            if "NameSpace" in title:
                return None
            content = metas[0].get("content", "").strip()
            if content:
                return content
        else:
            # 多个 F1 时，旧版取第二个
            content = metas[1].get("content", "").strip()
            if content:
                return content

    # 回退：使用 Microsoft.Help.Id
    help_id_meta = soup.find("meta", attrs={"name": "Microsoft.Help.Id"})
    if help_id_meta and help_id_meta.get("content"):
        return help_id_meta["content"].strip()

    # 最后回退到标题
    return title


def _extract_csharp_signature(soup: BeautifulSoup) -> str:
    """
    从 HTML 中提取 C# 方法签名，仅保留包含 public 的签名。
    对应旧版 get_csharp_full_name 逻辑。
    """
    div = soup.find("div", id="IDAB_code_Div1")
    if not div:
        return ""

    pre = div.find("pre")
    if not pre:
        return ""

    text = pre.get_text(separator=" ", strip=True)
    if "public" not in text:
        return ""

    # 归一化空白
    cleaned = re.sub(r"\s+", " ", text)
    return cleaned


def _extract_parameters_structured(soup: BeautifulSoup) -> str:
    """
    从 Parameters section 提取结构化参数信息：
    [paramName : Type]  - description
    优先使用 h4 \"Parameters\" + dl/dt/dd 结构。
    """
    h4 = soup.find("h4", string=lambda s: isinstance(s, str) and s.strip() == "Parameters")
    if not h4:
        return ""

    dl = h4.find_next_sibling("dl")
    if not dl:
        return ""

    dts = dl.find_all("dt")
    dds = dl.find_all("dd")
    if not dts or not dds:
        return ""

    lines: list[str] = []

    for dt, dd in zip(dts, dds):
        # 参数名
        name_span = dt.find("span", class_="parameter")
        param_name = name_span.get_text(strip=True) if name_span else dt.get_text(strip=True)

        # 参数类型：依次尝试 <a> → span.noLink → span.selflink → Unknown Type
        param_type = ""
        a_tags = dt.find_all("a")
        if a_tags:
            param_type = a_tags[-1].get_text(strip=True)
        else:
            no_link = dt.find("span", class_="noLink") or dt.find("span", class_="selflink")
            if no_link:
                param_type = no_link.get_text(strip=True)
            else:
                param_type = "Unknown Type"

        # 参数描述
        param_desc = dd.get_text(" ", strip=True)

        line = f"[{param_name} : {param_type}]"
        if param_desc:
            line += f"  - {param_desc}"
        lines.append(line)

    return "\n".join(lines)


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

    # 旧版精确定位：div.summary / div#IDBCSection
    if section_name == "summary":
        summary_div = topic_content.find("div", class_="summary")
        if summary_div:
            return _clean_text(summary_div.get_text())

    if section_name == "remarks":
        remarks_div = topic_content.find("div", id="IDBCSection")
        if remarks_div:
            return _clean_text(remarks_div.get_text())

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


def parse_all_api_html(html_dir: str, *, limit: int | None = None) -> list[dict]:
    """
    解析目录下所有（或前 limit 个）API HTML 文件

    Args:
        html_dir: CHM 解压后的 HTML 文件目录
        limit:    仅解析前 N 个文件（调试用），None 表示全量

    Returns:
        list of parsed API data dicts（已过滤构造函数、命名空间页等噪音）
    """
    results = []
    html_dir = Path(html_dir)

    html_files = list(html_dir.glob("**/*.html")) + list(html_dir.glob("**/*.htm"))
    if limit:
        html_files = html_files[:limit]
    total = len(html_files)
    print(f"找到 {total} 个 HTML 文件{f'（限制前 {limit} 个）' if limit else ''}")

    skipped_ctor = 0
    skipped_noise = 0

    for i, html_file in enumerate(html_files):
        if i % 1000 == 0 and i > 0:
            print(f"  解析进度: {i}/{total}  有效={len(results)}  ctor跳过={skipped_ctor}  噪音跳过={skipped_noise}")
        try:
            data = parse_single_html(str(html_file))
            if data:
                results.append(data)
            else:
                # 区分 ctor 跳过和其他跳过（通过再次调用轻量检查）
                _title, _fid = _peek_title_and_fullid(html_file)
                if _title and _is_constructor_page(_title, _fid):
                    skipped_ctor += 1
                else:
                    skipped_noise += 1
        except Exception as e:
            if i < 10:
                print(f"  解析失败: {html_file.name} - {e}")
            skipped_noise += 1

    print(f"\n解析完成：有效={len(results)}  ctor过滤={skipped_ctor}  其他噪音={skipped_noise}  总计={total}")
    return results


def _peek_title_and_fullid(html_file: Path) -> tuple[str, str]:
    """快速读取 HTML 的 title 和 F1/Id meta，不做完整解析（用于统计 ctor 跳过数量）"""
    try:
        content = html_file.read_text(encoding="utf-8", errors="ignore")
        soup = BeautifulSoup(content, "html.parser")
        title_tag = soup.find("title")
        title = title_tag.string.strip() if title_tag and title_tag.string else ""
        f1 = soup.find("meta", attrs={"name": "Microsoft.Help.F1"})
        fid = f1.get("content", "") if f1 else ""
        if not fid:
            id_meta = soup.find("meta", attrs={"name": "Microsoft.Help.Id"})
            fid = id_meta.get("content", "") if id_meta else ""
        return title, fid
    except Exception:
        return "", ""


def save_to_sqlite(api_data: list[dict], db_path: str, batch_size: int = 500):
    """Save parsed API data to SQLite with a tqdm progress bar.

    重建 SQLite 后必须重建 ChromaDB：本函数会清空并重写 revit_api 表且重置
    自增主键 id，SQLite 主键即 ChromaDB 的文档 id。因此每次调用后必须重新运行
    embedding 流程重建 ChromaDB，否则两库 id 将错位、双库不同步。
    """
    try:
        from tqdm.auto import tqdm as _tqdm
    except ImportError:
        _tqdm = None  # type: ignore[assignment]

    _db_dir = os.path.dirname(db_path)
    if _db_dir:  # 相对文件名（无目录部分）时 dirname 为空，makedirs("") 会抛错
        os.makedirs(_db_dir, exist_ok=True)

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
            members TEXT,
            quality_score REAL,
            quality_issues TEXT,
            rewritten INTEGER DEFAULT 0
        )
    """)
    cursor.execute("DELETE FROM revit_api")
    # 重置自增主键，避免 id 漂移导致与 ChromaDB 文档 id 错位（双库不同步根因）
    cursor.execute("DELETE FROM sqlite_sequence WHERE name='revit_api'")

    total = len(api_data)
    pbar = _tqdm(total=total, desc="Saving to SQLite", unit="rec",
                 dynamic_ncols=True) if _tqdm else None

    batch: list = []
    for item in api_data:
        issues = item.get("_quality_issues") or []
        batch.append((
            item.get("name"), item.get("full_id"), item.get("namespace"),
            item.get("content_type"), item.get("keywords"), item.get("info"),
            item.get("summary"), item.get("remark"), item.get("parameters"),
            item.get("exceptions"), item.get("return_value"), item.get("syntax"),
            item.get("members"),
            item.get("_quality_score"),
            "; ".join(issues) if issues else None,
            1 if item.get("_rewritten") else 0,
        ))
        if len(batch) >= batch_size:
            cursor.executemany(
                """INSERT INTO revit_api
                   (name, full_id, namespace, content_type, keywords, info,
                    summary, remark, parameters, exceptions, return_value, syntax, members,
                    quality_score, quality_issues, rewritten)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                batch,
            )
            if pbar:
                pbar.update(len(batch))
            batch = []

    if batch:
        cursor.executemany(
            """INSERT INTO revit_api
               (name, full_id, namespace, content_type, keywords, info,
                summary, remark, parameters, exceptions, return_value, syntax, members,
                quality_score, quality_issues, rewritten)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            batch,
        )
        if pbar:
            pbar.update(len(batch))

    if pbar:
        pbar.close()

    # 单次提交：避免循环内分批 commit 在中途失败时留下半空库
    conn.commit()
    conn.close()
    print(f"Saved {total} records to {db_path}")