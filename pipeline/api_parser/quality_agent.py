"""
API 数据质量检查 Agent（两阶段）

流程：
  Stage-1 | Gemini（快速、低成本）
           对每条解析结果做结构化审核，返回 JSON 打分。
           - 判断 parameters / summary / syntax 是否完整、可读
           - 给出 quality_score（0~1）和 issues 列表

  Stage-2 | Claude Sonnet（精准、高质量）
           仅对 Stage-1 标记为「需要修复」的条目介入：
           - 从原始 HTML 中重新提取关键字段
           - 生成高质量的 summary / parameters / syntax

对外接口：
    from pipeline.api_parser.quality_agent import run_quality_agent

    cleaned_data = run_quality_agent(api_data, config, html_dir)
    # cleaned_data 与 api_data 结构相同，问题条目的字段已被补全/重写
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pipeline.llm_client import LLMClient, create_llm_client

# ─────────────────────────────────────────────────────────────
# 阈值配置
# ─────────────────────────────────────────────────────────────
_QUALITY_THRESHOLD = 0.6   # Stage-1 得分低于此值时触发 Stage-2
_MAX_STAGE2_ITEMS = 2000   # 单次运行最多处理多少条 Stage-2（防止费用过高）


# ─────────────────────────────────────────────────────────────
# Stage-1: Gemini 快速审核
# ─────────────────────────────────────────────────────────────

_STAGE1_SYSTEM = (
    "You are a strict data-quality auditor for Revit API documentation records. "
    "Always reply with a single valid JSON object and nothing else."
)

_STAGE1_PROMPT_TPL = """\
Audit the following parsed Revit API record and return a JSON object with these keys:
  "quality_score": float 0–1 (1 = perfect, 0 = garbage)
  "issues": list of short strings describing problems (empty list if none)
  "needs_rewrite": boolean (true when quality_score < {threshold})

Score criteria:
- summary/info is missing or is a generic template string like "The XYZ type exposes..." → −0.3
- parameters field is empty but syntax suggests the method takes arguments → −0.25
- syntax/C# signature is missing entirely → −0.2
- parameter types are all "Unknown Type" → −0.15
- name or full_id looks like a constructor (#ctor, "Constructor") → −0.5 (likely noise)
- any field contains garbled text, HTML tags, or repeated whitespace → −0.1 each

Record:
  name        : {name}
  full_id     : {full_id}
  syntax      : {syntax}
  summary     : {summary}
  info        : {info}
  parameters  : {parameters}

JSON only, no markdown fences."""


def _stage1_audit(item: dict[str, Any], gemini: LLMClient) -> dict[str, Any]:
    """
    用 Gemini 对单条记录做结构化质量审核。
    返回 {"quality_score": float, "issues": [...], "needs_rewrite": bool}
    """
    prompt = _STAGE1_PROMPT_TPL.format(
        threshold=_QUALITY_THRESHOLD,
        name=item.get("name", ""),
        full_id=item.get("full_id", ""),
        syntax=(item.get("syntax") or "")[:300],
        summary=(item.get("summary") or "")[:300],
        info=(item.get("info") or "")[:200],
        parameters=(item.get("parameters") or "")[:400],
    )

    try:
        raw = gemini.generate_text(prompt, system_prompt=_STAGE1_SYSTEM)
        # 有时模型会包裹在 ```json``` 里
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw.strip())
        result = json.loads(raw)
        # 保证字段完整
        result.setdefault("quality_score", 1.0)
        result.setdefault("issues", [])
        result.setdefault("needs_rewrite", result["quality_score"] < _QUALITY_THRESHOLD)
        return result
    except Exception as e:
        # 审核失败时保守处理：不触发重写，继续使用原始数据
        return {"quality_score": 1.0, "issues": [f"audit_failed: {e}"], "needs_rewrite": False}


# ─────────────────────────────────────────────────────────────
# Stage-2: Claude Sonnet 兜底重写
# ─────────────────────────────────────────────────────────────

_STAGE2_SYSTEM = (
    "You are an expert Revit API documentation writer. "
    "Your task is to repair and improve incomplete or garbled API records. "
    "Always reply with a single valid JSON object and nothing else."
)

_STAGE2_PROMPT_TPL = """\
The following Revit API record has quality issues that need fixing.

Identified issues:
{issues}

Original record fields:
  name        : {name}
  full_id     : {full_id}
  syntax      : {syntax}
  summary     : {summary}
  info        : {info}
  parameters  : {parameters}
  remark      : {remark}

Raw HTML excerpt (first 3000 chars):
{html_excerpt}

Please produce a repaired version. Return a JSON object with these keys
(only include keys where you have confident improvements; omit the rest):
  "summary"    : clear 1-sentence description of what this API does
  "parameters" : list of "[paramName : Type]  - description" strings joined by \\n
                 (leave empty string "" if this API truly has no parameters)
  "syntax"     : the correct C# public signature (single line)
  "info"       : concise description if summary is also missing

JSON only, no markdown fences."""


def _stage2_rewrite(
    item: dict[str, Any],
    issues: list[str],
    html_dir: str | None,
    claude: LLMClient,
) -> dict[str, Any]:
    """
    用 Claude Sonnet 对低质量条目做字段级修复。
    返回包含改善后字段的 dict（仅含需要覆写的 key）。
    """
    html_excerpt = ""
    if html_dir:
        # 尝试找回原始 HTML，用于辅助重写
        html_excerpt = _load_html_excerpt(item, html_dir)

    prompt = _STAGE2_PROMPT_TPL.format(
        issues="\n".join(f"- {i}" for i in issues) if issues else "- general quality below threshold",
        name=item.get("name", ""),
        full_id=item.get("full_id", ""),
        syntax=(item.get("syntax") or "")[:400],
        summary=(item.get("summary") or "")[:400],
        info=(item.get("info") or "")[:300],
        parameters=(item.get("parameters") or "")[:600],
        remark=(item.get("remark") or "")[:300],
        html_excerpt=html_excerpt[:3000] if html_excerpt else "(HTML not available)",
    )

    try:
        raw = claude.generate_text(prompt, system_prompt=_STAGE2_SYSTEM)
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw.strip())
        patch = json.loads(raw)
        return {k: v for k, v in patch.items() if isinstance(v, str)}
    except Exception as e:
        return {"_rewrite_error": str(e)}


def _load_html_excerpt(item: dict[str, Any], html_dir: str) -> str:
    """
    根据 full_id / name 在 html_dir 里找到对应 HTML 文件并读取前 3000 字符。
    CHM 解压后文件名通常与 full_id 或 name 直接关联。
    """
    # 用 full_id 的最后一段（去掉命名空间前缀）或 name 作为文件名线索
    full_id = item.get("full_id") or ""
    name    = item.get("name") or ""

    # 候选文件名列表（CHM 不同版本命名规则不一）
    candidates: list[str] = []
    if full_id:
        # "Autodesk.Revit.DB.Wall.Create" → "Autodesk.Revit.DB.Wall.Create.htm"
        candidates.append(full_id + ".htm")
        candidates.append(full_id + ".html")
        # 只取类名部分
        short = full_id.split(".")[-1]
        candidates.append(short + ".htm")
        candidates.append(short + ".html")
    if name:
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
        candidates.append(safe_name + ".htm")
        candidates.append(safe_name + ".html")

    root = Path(html_dir)
    for cand in candidates:
        found = list(root.rglob(cand))
        if found:
            try:
                return found[0].read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass

    return ""


# ─────────────────────────────────────────────────────────────
# 公开入口
# ─────────────────────────────────────────────────────────────

def run_quality_agent(
    api_data: list[dict[str, Any]],
    config: dict[str, Any],
    html_dir: str | None = None,
    max_stage2: int = _MAX_STAGE2_ITEMS,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """
    对解析后的 API 数据运行两阶段质量 Agent。

    Args:
        api_data:   parse_all_api_html() 的输出
        config:     完整的 config.yaml dict（需包含 llm / openrouter 节）
        html_dir:   CHM 解压 HTML 目录（可选，提供时 Stage-2 可读原始 HTML）
        max_stage2: Stage-2 最多处理多少条（防止 token 消耗过高）
        verbose:    是否打印进度和统计

    Returns:
        与 api_data 结构完全相同的列表，低质量条目的字段已被补全/重写。
        每条记录额外增加：
          "_quality_score"  : float
          "_quality_issues" : list[str]
          "_rewritten"      : bool
    """
    gemini = create_llm_client(config, provider_override="gemini")
    claude = create_llm_client(config, provider_override="claude")

    stage2_count = 0
    rewritten_count = 0
    results: list[dict[str, Any]] = []

    total = len(api_data)
    if verbose:
        print(f"Quality Agent 启动 — 共 {total} 条，Stage-1 模型: {gemini.model}，Stage-2 模型: {claude.model}")

    for i, item in enumerate(api_data):
        if verbose and i % 500 == 0:
            print(f"  Stage-1 进度: {i}/{total}  (已触发 Stage-2: {stage2_count} 条)")

        audit = _stage1_audit(item, gemini)
        score  = audit.get("quality_score", 1.0)
        issues = audit.get("issues", [])
        needs_rewrite = audit.get("needs_rewrite", False)

        new_item = dict(item)
        new_item["_quality_score"]  = score
        new_item["_quality_issues"] = issues
        new_item["_rewritten"]      = False

        if needs_rewrite and stage2_count < max_stage2:
            stage2_count += 1
            if verbose and stage2_count <= 5:
                print(f"  [Stage-2] {item.get('name')} | score={score:.2f} | {issues}")

            patch = _stage2_rewrite(item, issues, html_dir, claude)

            if patch and "_rewrite_error" not in patch:
                for k, v in patch.items():
                    # 只覆写空字段，或旧版字段比 patch 短（说明旧版质量更差）
                    old_val = new_item.get(k) or ""
                    if not old_val or (isinstance(v, str) and len(v) > len(old_val)):
                        new_item[k] = v
                new_item["_rewritten"] = True
                rewritten_count += 1
            elif "_rewrite_error" in patch:
                new_item["_quality_issues"].append(f"rewrite_failed: {patch['_rewrite_error']}")

        results.append(new_item)

    if verbose:
        low_quality = sum(1 for r in results if r["_quality_score"] < _QUALITY_THRESHOLD)
        print(f"\nQuality Agent 完成")
        print(f"  总条数          : {total}")
        print(f"  低质量条目      : {low_quality}  ({low_quality/total*100:.1f}%)")
        print(f"  触发 Stage-2    : {stage2_count}")
        print(f"  成功重写        : {rewritten_count}")

    return results
