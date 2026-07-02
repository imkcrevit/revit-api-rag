"""
PPT 数据统计 — 从 revit_api.db / revit_sdk.db 提取分类汇总数据。

直接运行:  python tests/test_ppt_stats.py
pytest:    python -m pytest tests/test_ppt_stats.py -v -s
"""

import json
import sqlite3
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
API_DB = ROOT / "data" / "sqlite" / "revit_api.db"
SDK_DB = ROOT / "data" / "sqlite" / "revit_sdk.db"
CONFIG = ROOT / "config" / "config.yaml"

# 依赖真实构建的数据库，缺失时跳过（保证 CI 可重复、不误报失败）
pytestmark = pytest.mark.skipif(
    not (API_DB.exists() and SDK_DB.exists() and CONFIG.exists()),
    reason="requires built data/sqlite/revit_api.db & revit_sdk.db",
)

# ── 大分类规则 ─────────────────────────────────────────────────────
# namespace → group，按前缀匹配，顺序敏感（先匹配的优先）
GROUP_RULES = [
    ("DB.Structure",    "Autodesk.Revit.DB.Structure"),
    ("DB.Mechanical",   "Autodesk.Revit.DB.Mechanical"),
    ("DB.Electrical",   "Autodesk.Revit.DB.Electrical"),
    ("DB.Architecture", "Autodesk.Revit.DB.Architecture"),
    ("DB.Plumbing",     "Autodesk.Revit.DB.Plumbing"),
    ("DB.Analysis",     "Autodesk.Revit.DB.Analysis"),
    ("DB.Visual",       "Autodesk.Revit.DB.Visual"),
    ("DB.IFC",          "Autodesk.Revit.DB.IFC"),
    ("DB.Fabrication",  "Autodesk.Revit.DB.Fabrication"),
    ("DB.Infrastructure","Autodesk.Revit.DB.Infrastructure"),
    ("Revit.DB",        "Autodesk.Revit.DB"),      # 兜底：所有 DB.*
    ("Revit.UI",        "Autodesk.Revit.UI"),
    ("Revit.Creation",  "Autodesk.Revit.Creation"),
    ("Revit.Exceptions","Autodesk.Revit.Exceptions"),
    ("Revit.AppServices","Autodesk.Revit.ApplicationServices"),
    ("Revit.Attributes","Autodesk.Revit.Attributes"),
]


def _group_namespaces():
    """返回 {group_name: count} 有序字典。"""
    conn = sqlite3.connect(str(API_DB))
    rows = conn.execute(
        "SELECT namespace, COUNT(*) FROM revit_api GROUP BY namespace"
    ).fetchall()
    conn.close()

    result = {}
    for ns, cnt in rows:
        matched = False
        for group_name, prefix in GROUP_RULES:
            if ns.startswith(prefix):
                result[group_name] = result.get(group_name, 0) + cnt
                matched = True
                break
        if not matched:
            result["Other"] = result.get("Other", 0) + cnt

    # 按数量降序
    return dict(sorted(result.items(), key=lambda x: -x[1]))


def _total_api():
    conn = sqlite3.connect(str(API_DB))
    n = conn.execute("SELECT COUNT(*) FROM revit_api").fetchone()[0]
    conn.close()
    return n


def _sdk_projects():
    conn = sqlite3.connect(str(SDK_DB))
    n = conn.execute("SELECT COUNT(*) FROM sdk_info").fetchone()[0]
    conn.close()
    return n


def _sdk_mentioned_apis():
    conn = sqlite3.connect(str(SDK_DB))
    rows = conn.execute(
        "SELECT mentioned_apis FROM sdk_info WHERE mentioned_apis IS NOT NULL"
    ).fetchall()
    conn.close()
    total = 0
    for (raw,) in rows:
        try:
            total += len(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            pass
    return total


def _embed_dim():
    with open(CONFIG, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg["embedding"]["models"]["openai"]["dimension"]


# ── Tests ────────────────────────────────────────────────────────────

def test_ppt_stats():
    groups = _group_namespaces()
    total = _total_api()
    sdk_proj = _sdk_projects()
    sdk_apis = _sdk_mentioned_apis()
    dim = _embed_dim()

    print("\n")
    print("=" * 55)
    print("  COVERAGE BY NAMESPACE")
    print("-" * 55)
    for g, cnt in groups.items():
        bar = "\u2588" * max(1, cnt // 400)
        print(f"  {g:<20} {cnt:>6,}   {bar}")
    print("-" * 55)
    print(f"  {'TOTAL':<20} {total:>6,}")
    print("=" * 55)
    print(f"  API entries          {total:>6,}")
    print(f"  SDK code examples    {sdk_proj + sdk_apis:>6,}")
    print(f"    - SDK projects     {sdk_proj:>6,}")
    print(f"    - mentioned APIs   {sdk_apis:>6,}")
    print(f"  Embedding dims       {dim:>6,}")
    print("=" * 55)

    assert total > 20_000
    assert dim == 3072


if __name__ == "__main__":
    test_ppt_stats()
