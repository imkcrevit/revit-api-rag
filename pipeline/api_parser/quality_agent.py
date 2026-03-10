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
_MAX_STAGE2_ITEMS = 2000


# ─────────────────────────────────────────────────────────────
# HTML 辅助：定位并读取原始 HTML 文件
# ─────────────────────────────────────────────────────────────

def _load_html_excerpt(item: dict[str, Any], html_dir: str | None, max_chars: int = 3000) -> str:
    """
    读取原始 HTML 内容。
    优先使用 parse_single_html 存储的 _source_file 精确路径；
    若无则按 full_id / name 做名称猜测。
    """
    # 最直接：parse_single_html 已存储原始文件路径
    src = item.get("_source_file", "")
    if src:
        p = Path(src)
        if p.exists():
            try:
                return p.read_text(encoding="utf-8", errors="ignore")[:max_chars]
            except Exception:
                pass

    # 回退：按名称在 html_dir 里搜索
    if not html_dir:
        return ""

    full_id = item.get("full_id") or ""
    name    = item.get("name") or ""

    candidates: list[str] = []
    if full_id:
        candidates.append(full_id + ".htm")
        candidates.append(full_id + ".html")
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
    "Be critical and precise. Always reply with a single valid JSON object and nothing else."
)

_STAGE1_PROMPT_WITH_HTML = """\
Audit the following parsed Revit API record by comparing it against the RAW HTML source.

Score criteria (cumulative deductions from 1.0):
  -0.5  name/full_id indicates a constructor (#ctor / "Constructor") → noise
  -0.4  summary AND info are BOTH empty or missing
  -0.3  summary/info is a generic boilerplate ("The XYZ type exposes…", "Initializes a new instance…")
        while the HTML contains a real description
  -0.25 parameters field is empty/missing but HTML shows the method takes arguments
  -0.2  syntax/C# signature is missing or clearly differs from what's in the HTML
  -0.2  important content visible in HTML (description, return value) is absent from parsed fields
  -0.15 all parameter types are "Unknown Type" but HTML has type information
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

Return ONLY a JSON object:
  "quality_score" : float 0.0–1.0  (be strict; most records should be < 0.9)
  "issues"        : list of short English strings (empty list only if truly perfect)
  "needs_rewrite" : boolean (true when quality_score < {threshold})

JSON only. No markdown. No explanation."""

_STAGE1_PROMPT_NO_HTML = """\
Audit the following parsed Revit API record for data quality.
No raw HTML is available, so judge strictly on field completeness and plausibility.

Score criteria (cumulative deductions from 1.0):
  -0.5  name/full_id indicates a constructor (#ctor / "Constructor") → noise
  -0.4  summary AND info are BOTH empty or missing
  -0.3  summary or info is a generic boilerplate ("The XYZ type exposes…", "Initializes a new instance…")
  -0.25 parameters field is empty but syntax shows the method takes arguments
  -0.2  syntax/C# signature is missing entirely
  -0.15 all parameter types shown as "Unknown Type"
  -0.1  any field contains raw HTML tags, garbled text, or excessive whitespace
  -0.1  summary/info is extremely short (under 20 characters) for a non-trivial API member

Parsed record:
  name        : {name}
  full_id     : {full_id}
  syntax      : {syntax}
  summary     : {summary}
  info        : {info}
  parameters  : {parameters}

Return ONLY a JSON object:
  "quality_score" : float 0.0–1.0  (be strict; average should be around 0.7, not 1.0)
  "issues"        : list of short English strings (empty list only if truly perfect)
  "needs_rewrite" : boolean (true when quality_score < {threshold})

JSON only. No markdown. No explanation."""


def _stage1_audit(
    item: dict[str, Any],
    gemini: LLMClient,
    html_dir: str | None = None,
) -> dict[str, Any]:
    """
    用 Gemini 对单条记录做结构化质量审核（含原始 HTML 对比）。
    """
    html_excerpt = _load_html_excerpt(item, html_dir, max_chars=2500)

    if html_excerpt:
        # 压缩连续空白，减少 token
        html_excerpt = re.sub(r"\s{3,}", "  ", html_excerpt)
        prompt = _STAGE1_PROMPT_WITH_HTML.format(
            threshold=_QUALITY_THRESHOLD,
            name=item.get("name", ""),
            full_id=item.get("full_id", ""),
            syntax=(item.get("syntax") or "")[:300],
            summary=(item.get("summary") or "")[:300],
            info=(item.get("info") or "")[:200],
            parameters=(item.get("parameters") or "")[:400],
            html_excerpt=html_excerpt,
        )
    else:
        prompt = _STAGE1_PROMPT_NO_HTML.format(
            threshold=_QUALITY_THRESHOLD,
            name=item.get("name", ""),
            full_id=item.get("full_id", ""),
            syntax=(item.get("syntax") or "")[:300],
            summary=(item.get("summary") or "")[:300],
            info=(item.get("info") or "")[:200],
            parameters=(item.get("parameters") or "")[:400],
        )

    try:
        raw_resp = gemini.generate_text(prompt, system_prompt=_STAGE1_SYSTEM)
        raw_resp = re.sub(r"^```(?:json)?\s*", "", raw_resp.strip(), flags=re.IGNORECASE)
        raw_resp = re.sub(r"\s*```$", "", raw_resp.strip())
        result = json.loads(raw_resp)
        result.setdefault("quality_score", 1.0)
        result.setdefault("issues", [])
        result.setdefault("needs_rewrite", result["quality_score"] < _QUALITY_THRESHOLD)
        result["_html_found"] = bool(html_excerpt)
        return result
    except Exception as e:
        return {
            "quality_score": 1.0,
            "issues": [f"audit_failed: {e}"],
            "needs_rewrite": False,
            "_html_found": bool(html_excerpt),
        }


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

JSON only. No markdown. No explanation."""


def _stage2_rewrite(
    item: dict[str, Any],
    issues: list[str],
    html_dir: str | None,
    claude: LLMClient,
) -> dict[str, Any]:
    """
    用 Claude Sonnet 对低质量条目做字段级修复（以原始 HTML 为依据）。
    """
    html_excerpt = _load_html_excerpt(item, html_dir, max_chars=3000) or "(HTML not available)"

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

    Stage-1 (Gemini) 对每条记录评分，优先使用 item['_source_file'] 读取 HTML（
    parse_single_html 现在会存储该字段），html_dir 作为回退的搜索目录。

    Args:
        api_data:   parse_all_api_html() 的输出
        config:     完整的 config.yaml dict
        html_dir:   CHM 解压 HTML 目录（备用，当 _source_file 不存在时搜索用）
        max_stage2: Stage-2 最多处理多少条
        verbose:    是否打印进度和统计

    Returns:
        与 api_data 结构完全相同的列表，低质量条目的字段已被补全/重写。
        每条记录额外增加：
          "_quality_score"  : float
          "_quality_issues" : list[str]
          "_rewritten"      : bool
          "_html_found"     : bool  (诊断：Stage-1 是否成功加载 HTML)
    """
    gemini = create_llm_client(config, provider_override="gemini")
    claude = create_llm_client(config, provider_override="claude")

    stage2_count = 0
    rewritten_count = 0
    html_found_count = 0
    results: list[dict[str, Any]] = []

    total = len(api_data)
    if verbose:
        print(f"Quality Agent — {total} records")
        print(f"  Stage-1: {gemini.model}")
        print(f"  Stage-2: {claude.model}")
        print()

    for i, item in enumerate(api_data):
        if verbose and i % 50 == 0:
            pct_html = html_found_count / max(i, 1) * 100
            print(
                f"  [{i:>5}/{total}]  HTML found: {html_found_count}/{i} ({pct_html:.0f}%)"
                f"  Stage-2: {stage2_count}  Rewritten: {rewritten_count}"
            )

        # ── Pre-checks: deterministic quality flags ──────
        # These catch obvious issues before calling Gemini,
        # and act as a hard floor that Gemini cannot override upward.
        pre_issues: list[str] = []
        summary = item.get("summary") or ""
        info    = item.get("info") or ""
        params  = item.get("parameters") or ""
        syntax  = item.get("syntax") or ""

        # Both text fields empty
        if not summary.strip() and not info.strip():
            pre_issues.append("summary and info are both empty")
        # Generic boilerplate templates
        for tmpl in ("The ", "Initializes a new instance", "Gets or sets", "Gets the"):
            if (summary.startswith(tmpl) or info.startswith(tmpl)) and len(summary + info) < 80:
                pre_issues.append("summary/info looks like a short boilerplate template")
                break
        # Syntax has parameters but field is empty
        if syntax and "(" in syntax and params.strip() == "" and "void" not in syntax.lower():
            pre_issues.append("parameters field empty but syntax shows method takes arguments")
        # Unknown types only
        if params and params.count("Unknown Type") > 1 and "Unknown Type" in params:
            pre_issues.append("parameter types are all 'Unknown Type'")

        pre_deduction = min(len(pre_issues) * 0.25, 0.6)  # cap deduction at 0.6
        pre_score = round(1.0 - pre_deduction, 2) if pre_issues else None

        # ── Stage-1: Gemini 审核（含 HTML 对比）────────
        audit = _stage1_audit(item, gemini, html_dir=html_dir)
        score         = audit.get("quality_score", 1.0)
        issues        = list(audit.get("issues", []))
        needs_rewrite = audit.get("needs_rewrite", False)
        html_found    = audit.get("_html_found", False)

        # Merge pre-check results: take the lower score
        if pre_score is not None:
            if pre_score < score:
                score = pre_score
            issues = pre_issues + [iss for iss in issues if iss not in pre_issues]
            if score < _QUALITY_THRESHOLD:
                needs_rewrite = True

        if html_found:
            html_found_count += 1

        new_item = dict(item)
        new_item["_quality_score"]  = score
        new_item["_quality_issues"] = issues
        new_item["_rewritten"]      = False
        new_item["_html_found"]     = html_found

        # ── Stage-2: Claude 重写（仅低质量条目）────────
        if needs_rewrite and stage2_count < max_stage2:
            stage2_count += 1
            if verbose:
                print(f"  [Stage-2 #{stage2_count:>4}] {item.get('name', '')[:50]:<50}  score={score:.2f}  html={'Y' if html_found else 'N'}")
                if issues:
                    print(f"              → {'; '.join(issues[:3])}")

            patch = _stage2_rewrite(item, issues, html_dir, claude)

            if patch and "_rewrite_error" not in patch:
                for k, v in patch.items():
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
        html_total  = sum(1 for r in results if r.get("_html_found"))
        print(f"\n{'='*60}")
        print(f"Quality Agent 完成")
        print(f"  总条数          : {total}")
        print(f"  HTML 加载成功   : {html_total}  ({html_total/total*100:.1f}%)  ← 关键诊断")
        print(f"  低质量条目      : {low_quality}  ({low_quality/total*100:.1f}%)")
        print(f"  触发 Stage-2    : {stage2_count}")
        print(f"  成功重写        : {rewritten_count}")
        print(f"{'='*60}")

    return results
