"""
API 数据质量检查 Agent（两阶段，多线程）

流程：
  Stage-1 | Gemini（快速、低成本）— 5 线程并发
           对每条解析结果做结构化审核，同时比对原始 HTML 文件内容，
           能准确发现「解析漏字段」「摘要与 HTML 不符」等问题。
           返回 JSON 打分 (quality_score 0–1) 和 issues 列表。

  Stage-2 | Claude Sonnet（精准、高质量）— 5 线程并发
           仅对 Stage-1 标记为「需要修复」的条目介入：
           - 以原始 HTML 为依据，重新生成 summary / parameters / syntax

对外接口：
    from pipeline.api_parser.quality_agent import run_quality_agent

    cleaned_data = run_quality_agent(api_data, config, html_dir)
"""
from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import socket

try:
    from tqdm.auto import tqdm
except ImportError:
    tqdm = None  # type: ignore[assignment]

from pipeline.llm_client import LLMClient, create_llm_client
from prompts import load_prompt

# ─────────────────────────────────────────────────────────────
# 并发配置
# ─────────────────────────────────────────────────────────────
_NUM_WORKERS = 5
_print_lock = threading.Lock()


def check_connectivity(config: dict[str, Any]) -> dict[str, Any]:
    """
    Pre-flight connectivity check: verifies proxy port and OpenRouter API.

    Returns a dict with keys:
      proxy_ok    : bool  (False if proxy not configured or not reachable)
      api_ok      : bool  (True if OpenRouter responds)
      proxy_addr  : str   (proxy address used, or 'disabled')
      error       : str | None
    """
    import httpx

    proxy_cfg  = config.get("proxy", {})
    proxy_url  = None
    proxy_ok   = False
    proxy_addr = "disabled"

    # ── 1. Check proxy port ──────────────────────────────────────
    if proxy_cfg.get("enabled", False):
        proxy_url  = proxy_cfg.get("https") or proxy_cfg.get("http", "")
        proxy_addr = proxy_url

        # Parse host:port from "http://127.0.0.1:10808"
        try:
            import urllib.parse
            parsed = urllib.parse.urlparse(proxy_url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 10808
            sock = socket.create_connection((host, port), timeout=3)
            sock.close()
            proxy_ok = True
        except Exception as e:
            return {
                "proxy_ok": False,
                "api_ok": False,
                "proxy_addr": proxy_addr,
                "error": f"Proxy {proxy_addr} unreachable: {e}",
            }

    # ── 2. Check OpenRouter API ──────────────────────────────────
    try:
        client_kwargs: dict = {"timeout": 10}
        if proxy_ok and proxy_url:
            client_kwargs["proxy"] = proxy_url
        resp = httpx.get("https://openrouter.ai/api/v1/models", **client_kwargs)
        api_ok = resp.status_code < 500
    except Exception as e:
        return {
            "proxy_ok": proxy_ok,
            "api_ok": False,
            "proxy_addr": proxy_addr,
            "error": f"OpenRouter unreachable: {e}",
        }

    return {"proxy_ok": proxy_ok, "api_ok": api_ok, "proxy_addr": proxy_addr, "error": None}

# ─────────────────────────────────────────────────────────────
# 阈值配置
# ─────────────────────────────────────────────────────────────
_QUALITY_THRESHOLD = 0.6   # Stage-1 得分低于此值时触发 Stage-2
_MAX_STAGE2_ITEMS = 2000


# ─────────────────────────────────────────────────────────────
# HTML 辅助：定位并读取原始 HTML 文件
# ─────────────────────────────────────────────────────────────

def _html_to_clean_text(raw_html: str, max_chars: int = 2000) -> str:
    """
    Convert raw HTML to clean plain text safe for inclusion in LLM prompts.
    Strips tags, removes control characters and excessive whitespace.
    """
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(raw_html, "html.parser")
        # Remove script/style nodes
        for tag in soup(["script", "style", "head"]):
            tag.decompose()
        text = soup.get_text(separator=" ")
    except Exception:
        # Fallback: crude tag strip
        text = re.sub(r"<[^>]+>", " ", raw_html)

    # Remove null bytes and other control characters (cause 400 errors in API calls)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:max_chars]


def _load_html_excerpt(item: dict[str, Any], html_dir: str | None, max_chars: int = 2000) -> str:
    """
    读取原始 HTML 并返回干净的纯文本（已去除标签和控制字符）。
    优先使用 parse_single_html 存储的 _source_file 精确路径；
    若无则按 full_id / name 做名称猜测。
    """
    raw = ""

    # 最直接：parse_single_html 已存储原始文件路径
    src = item.get("_source_file", "")
    if src:
        p = Path(src)
        if p.exists():
            try:
                raw = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass

    # 回退：按名称在 html_dir 里搜索
    if not raw and html_dir:
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
                    raw = found[0].read_text(encoding="utf-8", errors="ignore")
                    break
                except Exception:
                    pass

    if not raw:
        return ""

    return _html_to_clean_text(raw, max_chars=max_chars)


# ─────────────────────────────────────────────────────────────
# Stage-1: Gemini 快速审核（含 HTML 对比）
# ─────────────────────────────────────────────────────────────

_STAGE1_SYSTEM = load_prompt("pipeline.api_quality_stage1_system.md")
_STAGE1_PROMPT_WITH_HTML = load_prompt("pipeline.api_quality_stage1_with_html.md")
_STAGE1_PROMPT_NO_HTML = load_prompt("pipeline.api_quality_stage1_no_html.md")


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
        err_str = str(e)
        is_403 = "403" in err_str
        # 403 means the record was NOT audited — treat as unknown quality
        # and send to Stage-2 so Claude can attempt a rewrite.
        return {
            "quality_score": 0.0 if is_403 else 1.0,
            "issues": [f"audit_failed: {err_str}"],
            "needs_rewrite": is_403,
            "_html_found": bool(html_excerpt),
        }


# ─────────────────────────────────────────────────────────────
# Stage-2: Claude Sonnet 兜底重写
# ─────────────────────────────────────────────────────────────

_STAGE2_SYSTEM = load_prompt("pipeline.api_quality_stage2_system.md")
_STAGE2_PROMPT_TPL = load_prompt("pipeline.api_quality_stage2.md")


def _stage2_rewrite(
    item: dict[str, Any],
    issues: list[str],
    html_dir: str | None,
    claude: LLMClient,
) -> dict[str, Any]:
    """
    用 Claude Sonnet 对低质量条目做字段级修复（以原始 HTML 为依据）。
    """
    html_excerpt = _load_html_excerpt(item, html_dir, max_chars=5000) or "(HTML not available)"

    prompt = _STAGE2_PROMPT_TPL.format(
        issues="\n".join(f"- {i}" for i in issues) if issues else "- general quality below threshold",
        name=item.get("name", ""),
        full_id=item.get("full_id", ""),
        syntax=(item.get("syntax") or "")[:600],
        summary=(item.get("summary") or "")[:600],
        info=(item.get("info") or "")[:500],
        parameters=(item.get("parameters") or "")[:800],
        remark=(item.get("remark") or "")[:400],
        html_excerpt=html_excerpt,
    )

    try:
        raw_resp = claude.generate_text(prompt, system_prompt=_STAGE2_SYSTEM)
        raw_resp = re.sub(r"^```(?:json)?\s*", "", raw_resp.strip(), flags=re.IGNORECASE)
        raw_resp = re.sub(r"\s*```$", "", raw_resp.strip())
        patch = json.loads(raw_resp)
        return {k: v for k, v in patch.items() if isinstance(v, str)}
    except Exception as e:
        err_str = str(e)
        result = {"_rewrite_error": err_str}
        if "403" in err_str:
            result["_is_403"] = True
        return result


# ─────────────────────────────────────────────────────────────
# 公开入口
# ─────────────────────────────────────────────────────────────

def _stage1_worker(
    idx: int,
    item: dict[str, Any],
    gemini: LLMClient,
    html_dir: str | None,
) -> tuple[int, dict[str, Any]]:
    """
    单条记录的 Stage-1 处理（在线程中执行）：pre-check + Gemini 审核。
    返回 (原始索引, 带 _quality_* 字段的新 item)。
    """
    # ── Pre-checks ──────────────────────────────────────────
    pre_issues: list[str] = []
    summary = item.get("summary") or ""
    info    = item.get("info") or ""
    params  = item.get("parameters") or ""

    html_preview = _load_html_excerpt(item, html_dir, max_chars=500)

    if not summary.strip() and not info.strip() and html_preview:
        pre_issues.append("summary and info are both empty (HTML has content)")
    _BOILERPLATE = [
        "type exposes the following members",
        "Initializes a new instance",
    ]
    combined_text = (summary + " " + info).lower()
    for tmpl in _BOILERPLATE:
        if tmpl.lower() in combined_text:
            pre_issues.append(f"boilerplate template detected: '{tmpl}'")
            break
    if params and params.count("Unknown Type") > 1:
        pre_issues.append("parameter types are all 'Unknown Type'")

    pre_deduction = min(len(pre_issues) * 0.2, 0.5)
    pre_score = round(1.0 - pre_deduction, 2) if pre_issues else None

    # ── Gemini audit ────────────────────────────────────────
    audit = _stage1_audit(item, gemini, html_dir=html_dir)
    score         = audit.get("quality_score", 1.0)
    issues        = list(audit.get("issues", []))
    needs_rewrite = audit.get("needs_rewrite", False)
    html_found    = audit.get("_html_found", False)

    if pre_score is not None:
        if pre_score < score:
            score = pre_score
        issues = pre_issues + [iss for iss in issues if iss not in pre_issues]
        if score < _QUALITY_THRESHOLD:
            needs_rewrite = True

    new_item = dict(item)
    new_item["_quality_score"]  = score
    new_item["_quality_issues"] = issues
    new_item["_rewritten"]      = False
    new_item["_html_found"]     = html_found
    new_item["_needs_rewrite"]  = needs_rewrite  # temporary flag for Stage-2 selection

    return (idx, new_item)


def _stage2_worker(
    idx: int,
    item: dict[str, Any],
    html_dir: str | None,
    claude: LLMClient,
    failed_403_log: list[str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """
    单条记录的 Stage-2 处理（在线程中执行）：Claude 重写。
    返回 (原始索引, 更新后的 item)。
    若 Claude 也返回 403，将 source_file / full_id 追加到 failed_403_log。
    """
    issues = item.get("_quality_issues", [])
    patch = _stage2_rewrite(item, issues, html_dir, claude)

    new_item = dict(item)
    if patch and "_rewrite_error" not in patch:
        for k, v in patch.items():
            old_val = new_item.get(k) or ""
            if not old_val or (isinstance(v, str) and len(v) > len(old_val) * 0.5):
                new_item[k] = v
        new_item["_rewritten"] = True
    elif "_rewrite_error" in patch:
        err_msg = patch["_rewrite_error"]
        new_item["_quality_issues"] = list(new_item.get("_quality_issues", []))
        new_item["_quality_issues"].append(f"rewrite_failed: {err_msg}")
        # Log source file for persistent 403 so we can retry later
        if patch.get("_is_403") and failed_403_log is not None:
            src = (
                item.get("_source_file")
                or item.get("full_id")
                or item.get("name")
                or f"idx:{idx}"
            )
            failed_403_log.append(str(src))

    return (idx, new_item)


def run_quality_agent(
    api_data: list[dict[str, Any]],
    config: dict[str, Any],
    html_dir: str | None = None,
    max_stage2: int = _MAX_STAGE2_ITEMS,
    num_workers: int = _NUM_WORKERS,
    verbose: bool = True,
    failed_403_log_path: str | None = None,
) -> list[dict[str, Any]]:
    """
    对解析后的 API 数据运行两阶段质量 Agent（多线程并发）。

    Phase 1: Pre-check + Stage-1 Gemini 审核（num_workers 线程并发）
    Phase 2: Stage-2 Claude 重写低质量条目（num_workers 线程并发）

    Args:
        api_data:             parse_all_api_html() 的输出
        config:               完整的 config.yaml dict
        html_dir:             CHM 解压 HTML 目录
        max_stage2:           Stage-2 最多处理多少条
        num_workers:          并发线程数（默认 5）
        verbose:              是否打印进度和统计
        failed_403_log_path:  若提供，Stage-2 遭遇 403 时将 source_file 写入此文件
                              （每行一条，可用于后续逐个重试）

    Returns:
        与 api_data 结构完全相同的列表，低质量条目的字段已被补全/重写。
        每条记录额外增加：
          "_quality_score"  : float
          "_quality_issues" : list[str]
          "_rewritten"      : bool
          "_html_found"     : bool
    """
    import datetime

    # ── Pre-flight: connectivity check ──────────────────────────
    if verbose:
        print("Checking connectivity...")
    conn = check_connectivity(config)
    if verbose:
        proxy_status = f"OK ({conn['proxy_addr']})" if conn["proxy_ok"] else (
            "DISABLED" if conn["proxy_addr"] == "disabled" else f"FAILED ({conn['error']})"
        )
        api_status = "OK" if conn["api_ok"] else f"FAILED ({conn['error']})"
        print(f"  Proxy  : {proxy_status}")
        print(f"  API    : {api_status}")

    # 降级重试与连通性校验必须无条件执行（不能被 verbose 包裹，否则静默模式下
    # 会带着不可用的 API 继续跑并耗费额度）。仅打印语句保留 verbose 判断。
    if not conn["api_ok"] and config.get("proxy", {}).get("enabled", False):
        if verbose:
            print("  Proxy unreachable — retrying without proxy...")
        config["proxy"]["enabled"] = False
        conn = check_connectivity(config)
        if verbose:
            api_status = "OK" if conn["api_ok"] else f"FAILED ({conn['error']})"
            print(f"  API (direct) : {api_status}")

    if not conn["api_ok"]:
        raise RuntimeError(
            f"OpenRouter API is not reachable.\n"
            f"Error: {conn['error']}\n"
            f"Proxy : {conn['proxy_addr']}\n"
            "Fix: ensure proxy is running, or set proxy.enabled=false in config.yaml"
        )

    if verbose:
        print()

    gemini = create_llm_client(config, provider_override="gemini")
    claude = create_llm_client(config, provider_override="claude")
    claude.max_tokens = 8192

    total = len(api_data)
    if verbose:
        print(f"Quality Agent — {total} records, {num_workers} threads")
        print(f"  Stage-1: {gemini.model}")
        print(f"  Stage-2: {claude.model}")
        if failed_403_log_path:
            print(f"  403 log : {failed_403_log_path}")
        print()

    # ════════════════════════════════════════════════════════════
    # Phase 1: Stage-1 Gemini 审核（并发）
    # ════════════════════════════════════════════════════════════
    if verbose:
        print(f"Phase 1: Stage-1 Gemini audit ({num_workers} threads)...")

    results: list[dict[str, Any] | None] = [None] * total

    pbar1 = (
        tqdm(total=total, desc="Stage-1 Gemini", unit="rec", dynamic_ncols=True)
        if tqdm and verbose else None
    )

    def _on_stage1_done(future):
        if pbar1:
            pbar1.update(1)

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {}
        for i, item in enumerate(api_data):
            fut = executor.submit(_stage1_worker, i, item, gemini, html_dir)
            fut.add_done_callback(_on_stage1_done)
            futures[fut] = i

        for fut in as_completed(futures):
            try:
                idx, new_item = fut.result()
                results[idx] = new_item
            except Exception as e:
                idx = futures[fut]
                fallback = dict(api_data[idx])
                fallback["_quality_score"] = 1.0
                fallback["_quality_issues"] = [f"stage1_thread_error: {e}"]
                fallback["_rewritten"] = False
                fallback["_html_found"] = False
                fallback["_needs_rewrite"] = False
                results[idx] = fallback

    if pbar1:
        pbar1.close()

    html_found_count   = sum(1 for r in results if r and r.get("_html_found"))
    stage1_needs_rewrite = sum(1 for r in results if r and r.get("_needs_rewrite"))
    stage1_403_count   = sum(
        1 for r in results if r
        and any("403" in str(iss) for iss in (r.get("_quality_issues") or []))
    )
    if verbose:
        print(
            f"  Stage-1 complete: {total} records | "
            f"HTML found: {html_found_count} | "
            f"needs-rewrite: {stage1_needs_rewrite} | "
            f"403 errors: {stage1_403_count}"
        )
        print()

    # ════════════════════════════════════════════════════════════
    # Phase 2: Stage-2 Claude 重写（并发，仅低质量 + 有 HTML）
    # ════════════════════════════════════════════════════════════
    stage2_candidates: list[tuple[int, dict[str, Any]]] = []
    for i, item in enumerate(results):
        if not item:
            continue
        if item.get("_needs_rewrite") and item.get("_html_found"):
            stage2_candidates.append((i, item))
        elif item.get("_needs_rewrite") and not item.get("_html_found"):
            item["_quality_issues"] = list(item.get("_quality_issues", []))
            item["_quality_issues"].append("stage2_skipped: no HTML source available")

    stage2_candidates = stage2_candidates[:max_stage2]
    stage2_total = len(stage2_candidates)

    if verbose:
        print(f"Phase 2: Stage-2 Claude rewrite — {stage2_total} candidates ({num_workers} threads)...")

    # Thread-safe list for Stage-2 403 logging
    failed_403_log: list[str] = []

    pbar2 = (
        tqdm(total=stage2_total, desc="Stage-2 Claude", unit="rec", dynamic_ncols=True)
        if tqdm and verbose and stage2_total > 0 else None
    )

    def _on_stage2_done(future):
        if pbar2:
            pbar2.update(1)

    if stage2_candidates:
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = {}
            for orig_idx, item in stage2_candidates:
                fut = executor.submit(
                    _stage2_worker, orig_idx, item, html_dir, claude, failed_403_log
                )
                fut.add_done_callback(_on_stage2_done)
                futures[fut] = orig_idx

            for fut in as_completed(futures):
                try:
                    idx, updated_item = fut.result()
                    results[idx] = updated_item
                except Exception as e:
                    idx = futures[fut]
                    if results[idx]:
                        results[idx]["_quality_issues"] = list(results[idx].get("_quality_issues", []))
                        results[idx]["_quality_issues"].append(f"stage2_thread_error: {e}")

    if pbar2:
        pbar2.close()

    # ── Write 403 log file ───────────────────────────────────────
    if failed_403_log:
        log_path = failed_403_log_path or "quality_agent_403_failed.log"
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"# Stage-2 403 failures logged at {ts}\n")
            for src in failed_403_log:
                f.write(src + "\n")
        if verbose:
            print(f"\n  [403 Log] {len(failed_403_log)} Stage-2 items still got 403 → saved to: {log_path}")

    # ── Clean up temporary flag & compute stats ──────────────────
    rewritten_count = 0
    for item in results:
        if item:
            item.pop("_needs_rewrite", None)
            if item.get("_rewritten"):
                rewritten_count += 1

    if verbose:
        low_quality = sum(1 for r in results if r and r["_quality_score"] < _QUALITY_THRESHOLD)
        html_total  = sum(1 for r in results if r and r.get("_html_found"))
        stage2_403_count = len(failed_403_log)
        print(f"\n{'='*60}")
        print(f"Quality Agent complete")
        print(f"  Total records   : {total}")
        print(f"  Threads         : {num_workers}")
        print(f"  HTML loaded     : {html_total}  ({html_total/total*100:.1f}%)")
        print(f"  Low quality     : {low_quality}  ({low_quality/total*100:.1f}%)")
        print(f"  Stage-2 runs    : {stage2_total}")
        print(f"  Rewritten       : {rewritten_count}")
        print(f"  Stage-1 403 err : {stage1_403_count}")
        print(f"  Stage-2 403 err : {stage2_403_count}"
              + (f"  ← see {failed_403_log_path or 'quality_agent_403_failed.log'}" if stage2_403_count else ""))
        print(f"{'='*60}")

    return [r for r in results if r is not None]
