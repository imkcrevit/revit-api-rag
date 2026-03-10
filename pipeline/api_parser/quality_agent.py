"""
API 数据质量检查 Agent（两阶段）

流程：
  Stage-1 | Gemini（快速、低成本）
           对每条解析结果做结构化审核，同时比对原始 HTML 文件内容，
           能准确发现「解析漏字段」「摘要与 HTML 不符」等问题。
           返回 JSON 打分 (quality_score 0–1) 和 issues 列表。

  Stage-2 | Claude Sonnet（精准、高质量）
           仅对 Stage-1 标记为「需要修复」的条目介入：
           - 以原始 HTML 为依据，重新生成 summary / parameters / syntax

对外接口：
    from pipeline.api_parser.quality_agent import run_quality_agent

    cleaned_data = run_quality_agent(api_data, config, html_dir)
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
# HTML 辅助：定位并读取原始 HTML 文件
# ─────────────────────────────────────────────────────────────

def _load_html_excerpt(item: dict[str, Any], html_dir: str, max_chars: int = 3000) -> str:
    """
    根据 full_id / name 在 html_dir 里找到对应 HTML 文件并读取前 max_chars 字符。
    CHM 解压后文件名通常与 full_id 或 name 直接关联。
    """
    full_id = item.get("full_id") or ""
    name    = item.get("name") or ""

    candidates: list[str] = []
    if full_id:
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
                return found[0].read_text(encoding="utf-8", errors="ignore")[:max_chars]
            except Exception:
                pass
    return ""


# ─────────────────────────────────────────────────────────────
# Stage-1: Gemini 快速审核（含 HTML 对比）
# ─────────────────────────────────────────────────────────────

_STAGE1_SYSTEM = (
    "You are a strict data-quality auditor for Revit API documentation records. "
    "Always reply with a single valid JSON object and nothing else."
)

_STAGE1_PROMPT_TPL = """\
Audit the following parsed Revit API record by comparing it against the RAW HTML source.
Check whether important information present in the HTML has been correctly extracted into the parsed fields.

Score criteria (cumulative deductions):
  -0.5  name or full_id indicates a constructor (#ctor / "Constructor") → likely noise, skip
  -0.3  summary/info is missing OR is a generic template ("The XYZ type exposes…") while HTML has real description
  -0.25 parameters field is empty but HTML source shows the method has arguments
  -0.2  syntax/C# signature is missing or clearly incorrect compared to HTML
  -0.2  important content visible in HTML is absent from all parsed fields
  -0.15 parameter types are all "Unknown Type" but HTML contains type info
  -0.1  any field contains raw HTML tags, garbled unicode, or excessive whitespace

Parsed record:
  name        : {name}
  full_id     : {full_id}
  syntax      : {syntax}
  summary     : {summary}
  info        : {info}
  parameters  : {parameters}

Raw HTML excerpt (up to 2500 chars):
{html_excerpt}

Return ONLY a JSON object with these keys:
  "quality_score" : float 0–1  (1 = perfect extraction, 0 = completely wrong/missing)
  "issues"        : list of short English strings describing each problem found (empty list if none)
  "needs_rewrite" : boolean  (true when quality_score < {threshold})

JSON only. No markdown fences. No explanation."""


def _stage1_audit(
    item: dict[str, Any],
    gemini: LLMClient,
    html_dir: str | None = None,
) -> dict[str, Any]:
    """
    用 Gemini 对单条记录做结构化质量审核（含原始 HTML 对比）。
    返回 {"quality_score": float, "issues": [...], "needs_rewrite": bool}
    """
    html_excerpt = "(HTML not available)"
    if html_dir:
        raw = _load_html_excerpt(item, html_dir, max_chars=2500)
        if raw:
            # 去掉多余空白，压缩 HTML 噪音
            html_excerpt = re.sub(r"\s{3,}", "  ", raw)

    prompt = _STAGE1_PROMPT_TPL.format(
        threshold=_QUALITY_THRESHOLD,
        name=item.get("name", ""),
        full_id=item.get("full_id", ""),
        syntax=(item.get("syntax") or "")[:300],
        summary=(item.get("summary") or "")[:300],
        info=(item.get("info") or "")[:200],
        parameters=(item.get("parameters") or "")[:400],
        html_excerpt=html_excerpt,
    )

    try:
        raw_resp = gemini.generate_text(prompt, system_prompt=_STAGE1_SYSTEM)
        # 有时模型会包裹在 ```json``` 里
        raw_resp = re.sub(r"^```(?:json)?\s*", "", raw_resp.strip(), flags=re.IGNORECASE)
        raw_resp = re.sub(r"\s*```$", "", raw_resp.strip())
        result = json.loads(raw_resp)
        result.setdefault("quality_score", 1.0)
        result.setdefault("issues", [])
        result.setdefault("needs_rewrite", result["quality_score"] < _QUALITY_THRESHOLD)
        return result
    except Exception as e:
        # 审核失败时保守处理：不触发重写，保留原始数据
        return {"quality_score": 1.0, "issues": [f"audit_failed: {e}"], "needs_rewrite": False}


# ─────────────────────────────────────────────────────────────
# Stage-2: Claude Sonnet 兜底重写
# ─────────────────────────────────────────────────────────────

_STAGE2_SYSTEM = (
    "You are an expert Revit API documentation writer. "
    "Your task is to repair and improve incomplete or garbled API records "
    "using the raw HTML source as the authoritative reference. "
    "Always reply with a single valid JSON object and nothing else."
)

_STAGE2_PROMPT_TPL = """\
The following Revit API record has quality issues. Use the RAW HTML as the ground truth to produce corrected fields.

Identified issues:
{issues}

Original parsed record:
  name        : {name}
  full_id     : {full_id}
  syntax      : {syntax}
  summary     : {summary}
  info        : {info}
  parameters  : {parameters}
  remark      : {remark}

Raw HTML excerpt (up to 3000 chars):
{html_excerpt}

Return ONLY a JSON object. Include only keys where you have confident improvements (omit unchanged fields):
  "summary"    : clear 1-sentence English description of what this API member does
  "parameters" : multiline string, one param per line: "[paramName : Type]  - description"
                 (empty string "" if this API truly has no parameters)
  "syntax"     : the correct C# public signature (single line, from HTML)
  "info"       : concise description if summary is also missing

JSON only. No markdown fences. No explanation."""


def _stage2_rewrite(
    item: dict[str, Any],
    issues: list[str],
    html_dir: str | None,
    claude: LLMClient,
) -> dict[str, Any]:
    """
    用 Claude Sonnet 对低质量条目做字段级修复（以原始 HTML 为依据）。
    返回包含改善后字段的 dict（仅含需要覆写的 key）。
    """
    html_excerpt = "(HTML not available)"
    if html_dir:
        raw = _load_html_excerpt(item, html_dir, max_chars=3000)
        if raw:
            html_excerpt = raw

    prompt = _STAGE2_PROMPT_TPL.format(
        issues="\n".join(f"- {i}" for i in issues) if issues else "- general quality below threshold",
        name=item.get("name", ""),
        full_id=item.get("full_id", ""),
        syntax=(item.get("syntax") or "")[:400],
        summary=(item.get("summary") or "")[:400],
        info=(item.get("info") or "")[:300],
        parameters=(item.get("parameters") or "")[:600],
        remark=(item.get("remark") or "")[:300],
        html_excerpt=html_excerpt,
    )

    try:
        raw_resp = claude.generate_text(prompt, system_prompt=_STAGE2_SYSTEM)
        raw_resp = re.sub(r"^```(?:json)?\s*", "", raw_resp.strip(), flags=re.IGNORECASE)
        raw_resp = re.sub(r"\s*```$", "", raw_resp.strip())
        patch = json.loads(raw_resp)
        return {k: v for k, v in patch.items() if isinstance(v, str)}
    except Exception as e:
        return {"_rewrite_error": str(e)}


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

    Stage-1 (Gemini) 对每条记录评分，同时对比原始 HTML（如果提供了 html_dir）。
    Stage-2 (Claude) 仅对低分条目介入，以 HTML 为依据重写各字段。

    Args:
        api_data:   parse_all_api_html() 的输出
        config:     完整的 config.yaml dict
        html_dir:   CHM 解压 HTML 目录（强烈建议提供，可显著提升 Stage-1 准确度）
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
    html_note = f"  HTML 目录: {html_dir}" if html_dir else "  ⚠ 未提供 html_dir，Stage-1 无法对比 HTML（建议提供）"
    if verbose:
        print(f"Quality Agent 启动 — 共 {total} 条")
        print(f"  Stage-1: {gemini.model}")
        print(f"  Stage-2: {claude.model}")
        print(html_note)
        print()

    for i, item in enumerate(api_data):
        if verbose and i % 100 == 0:
            print(f"  [{i:>5}/{total}] Stage-2 触发: {stage2_count} 条  重写成功: {rewritten_count} 条")

        # ── Stage-1: Gemini 审核（含 HTML 对比）────────
        audit = _stage1_audit(item, gemini, html_dir=html_dir)
        score         = audit.get("quality_score", 1.0)
        issues        = audit.get("issues", [])
        needs_rewrite = audit.get("needs_rewrite", False)

        new_item = dict(item)
        new_item["_quality_score"]  = score
        new_item["_quality_issues"] = issues
        new_item["_rewritten"]      = False

        # ── Stage-2: Claude 重写（仅低质量条目）────────
        if needs_rewrite and stage2_count < max_stage2:
            stage2_count += 1
            if verbose:
                print(f"  [Stage-2 #{stage2_count:>4}] {item.get('name', '')[:50]:<50}  score={score:.2f}")
                if issues:
                    print(f"              issues: {'; '.join(issues[:3])}")

            patch = _stage2_rewrite(item, issues, html_dir, claude)

            if patch and "_rewrite_error" not in patch:
                for k, v in patch.items():
                    old_val = new_item.get(k) or ""
                    # 只覆写空字段，或 patch 内容更长（说明修复后信息更丰富）
                    if not old_val or (isinstance(v, str) and len(v) > len(old_val)):
                        new_item[k] = v
                new_item["_rewritten"] = True
                rewritten_count += 1
            elif "_rewrite_error" in patch:
                new_item["_quality_issues"].append(f"rewrite_failed: {patch['_rewrite_error']}")

        results.append(new_item)

    if verbose:
        low_quality = sum(1 for r in results if r["_quality_score"] < _QUALITY_THRESHOLD)
        print(f"\n{'='*60}")
        print(f"Quality Agent 完成")
        print(f"  总条数          : {total}")
        print(f"  低质量条目      : {low_quality}  ({low_quality/total*100:.1f}%)")
        print(f"  触发 Stage-2    : {stage2_count}")
        print(f"  成功重写        : {rewritten_count}")
        print(f"{'='*60}")

    return results
