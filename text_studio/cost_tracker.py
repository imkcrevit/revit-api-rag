"""
TextStudio 每日费用追踪器

基于 JSON 文件持久化每日 token 用量和估算费用。
当日费用超过阈值时自动中断服务，等待人工重置。

费用估算基于 OpenRouter 上 DeepSeek Chat v3 定价：
  - Input:  $0.14 / 1M tokens
  - Output: $0.28 / 1M tokens
  - Token 估算：混合文本约 2 chars/token
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# DeepSeek Chat v3 pricing on OpenRouter (USD per token)
_INPUT_PRICE_PER_TOKEN = 0.14 / 1_000_000   # $0.14 / 1M
_OUTPUT_PRICE_PER_TOKEN = 0.28 / 1_000_000   # $0.28 / 1M

# Default daily limit
_DEFAULT_DAILY_LIMIT_USD = 1.0

# Rough chars-per-token ratio for mixed CJK+English text
_CHARS_PER_TOKEN = 2.0


def _estimate_tokens(text: str) -> int:
    """Estimate token count from text length."""
    if not text:
        return 0
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class CostTracker:
    """JSON-file-based daily cost tracker for TextStudio."""

    def __init__(self, data_path: Path | None = None, daily_limit: float | None = None):
        if data_path is None:
            try:
                from server.app.deps import get_config, _resolve_data_dir
                config = get_config()
                data_dir = _resolve_data_dir(config)
                data_path = data_dir / "text_studio_cost.json"
                # Read limit from config
                ts_cfg = config.get("text_studio", {})
                daily_limit = daily_limit or ts_cfg.get("daily_limit_usd", _DEFAULT_DAILY_LIMIT_USD)
            except Exception:
                data_path = Path("./data/text_studio_cost.json")

        self._path = data_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._daily_limit = daily_limit or _DEFAULT_DAILY_LIMIT_USD
        self._lock = threading.Lock()
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self._path.is_file():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save(self):
        try:
            self._path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Failed to save cost data: {e}")

    def _ensure_today(self) -> dict:
        key = _today_key()
        if key not in self._data:
            self._data[key] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "requests": 0,
                "interrupted": False,
            }
        return self._data[key]

    # ── Public API ──────────────────────────────────────────────────────

    def record(self, input_text: str, output_text: str):
        """Record a completed request's estimated cost."""
        input_tokens = _estimate_tokens(input_text)
        output_tokens = _estimate_tokens(output_text)
        cost = (input_tokens * _INPUT_PRICE_PER_TOKEN
                + output_tokens * _OUTPUT_PRICE_PER_TOKEN)

        with self._lock:
            day = self._ensure_today()
            day["input_tokens"] += input_tokens
            day["output_tokens"] += output_tokens
            day["cost_usd"] = round(day["cost_usd"] + cost, 6)
            day["requests"] += 1

            # Check if we just crossed the limit
            if day["cost_usd"] >= self._daily_limit:
                day["interrupted"] = True
                logger.warning(
                    f"TextStudio daily cost limit reached: "
                    f"${day['cost_usd']:.4f} >= ${self._daily_limit:.2f}"
                )

            self._save()

        logger.info(
            f"TextStudio cost: +${cost:.5f} "
            f"(in={input_tokens} out={output_tokens}) "
            f"today=${day['cost_usd']:.4f}/{self._daily_limit:.2f}"
        )

    def is_over_limit(self) -> bool:
        """Check if today's cost exceeds the daily limit."""
        with self._lock:
            day = self._ensure_today()
            return day.get("interrupted", False) or day["cost_usd"] >= self._daily_limit

    def get_status(self) -> dict:
        """Get current day's cost status for API/frontend."""
        with self._lock:
            day = self._ensure_today()
            return {
                "date": _today_key(),
                "cost_usd": round(day["cost_usd"], 4),
                "limit_usd": self._daily_limit,
                "requests": day["requests"],
                "input_tokens": day["input_tokens"],
                "output_tokens": day["output_tokens"],
                "interrupted": day.get("interrupted", False),
                "remaining_usd": round(max(0, self._daily_limit - day["cost_usd"]), 4),
            }

    def reset_today(self):
        """Manual reset — clear today's interrupted flag and counters."""
        with self._lock:
            key = _today_key()
            self._data[key] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "requests": 0,
                "interrupted": False,
            }
            self._save()
        logger.info("TextStudio daily cost reset manually")


# ── Singleton ───────────────────────────────────────────────────────────

_instance: CostTracker | None = None
_instance_lock = threading.Lock()


def get_cost_tracker() -> CostTracker:
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = CostTracker()
    return _instance
