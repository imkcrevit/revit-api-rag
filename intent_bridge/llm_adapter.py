"""
LLM Adapter — async-first, primary + fallback + retry

Uses OpenRouter to call Gemini (primary) and DeepSeek (fallback).
429/5xx auto-switches to fallback, max_retries=2.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, AsyncGenerator

import httpx
import yaml
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("intent_bridge.llm")

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 60  # seconds — LLM calls can be slow


def _load_bridge_config() -> dict:
    """Load intent_bridge/config.yaml"""
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _load_global_proxy() -> str | None:
    """Load proxy from config/config.yaml if enabled, or from env."""
    try:
        from config import get_proxy_url
        return get_proxy_url()
    except ImportError:
        pass
    # Fallback: read config/config.yaml directly
    root = os.path.dirname(os.path.dirname(__file__))
    cfg_path = os.path.join(root, "config", "config.yaml")
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        proxy_cfg = cfg.get("proxy", {})
        if proxy_cfg.get("enabled"):
            return proxy_cfg.get("https") or proxy_cfg.get("http")
    return None


def _get_api_key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "")


# ---------------------------------------------------------------------------
# LLMAdapter
# ---------------------------------------------------------------------------

class LLMAdapter:
    """
    Async-first LLM client with primary/fallback and retry.

    Primary: google/gemini-3-flash-preview (temperature 0.1)
    Fallback: openai/gpt-5.3-codex
    """

    def __init__(self, config: dict | None = None):
        if config is None:
            bridge_cfg = _load_bridge_config()
            llm_cfg = bridge_cfg.get("llm", {})
            config = {
                "primary": llm_cfg.get("primary", {"model": "google/gemini-3-flash-preview", "temperature": 0.1}),
                "fallback": llm_cfg.get("fallback", {"model": "openai/gpt-5.3-codex", "temperature": 0.1}),
                "base_url": llm_cfg.get("base_url", "https://openrouter.ai/api/v1"),
                "timeout": llm_cfg.get("timeout", _DEFAULT_TIMEOUT),
                "max_retries": llm_cfg.get("max_retries", 2),
                "max_tokens": llm_cfg.get("max_tokens", 4096),
            }

        self._primary = config["primary"]
        self._fallback = config["fallback"]
        self._base_url = config.get("base_url", "https://openrouter.ai/api/v1")
        self._timeout = config.get("timeout", _DEFAULT_TIMEOUT)
        self._max_retries = config.get("max_retries", 2)
        self._max_tokens = config.get("max_tokens", 4096)
        self._api_key = _get_api_key()
        if not self._api_key:
            logger.error("OPENROUTER_API_KEY is empty! Check .env file.")
        else:
            logger.info("API key loaded: %s...%s (len=%d)", self._api_key[:8], self._api_key[-4:], len(self._api_key))

        # Proxy: env var > config/config.yaml proxy setting
        self._proxy = (
            os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
            or os.getenv("https_proxy") or os.getenv("http_proxy")
            or _load_global_proxy()
        )
        if self._proxy:
            logger.info("Using proxy: %s", self._proxy)

        # Reusable clients (lazy init)
        self._sync_client: httpx.Client | None = None
        self._async_client: httpx.AsyncClient | None = None

    def _make_timeout(self) -> httpx.Timeout:
        """Split timeout: fast connect, generous read for LLM generation."""
        return httpx.Timeout(
            connect=10.0,
            read=self._timeout,
            write=10.0,
            pool=10.0,
        )

    def _get_sync_client(self) -> httpx.Client:
        if self._sync_client is None or self._sync_client.is_closed:
            self._sync_client = httpx.Client(
                timeout=self._make_timeout(),
                proxy=self._proxy,
            )
        return self._sync_client

    def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(
                timeout=self._make_timeout(),
                proxy=self._proxy,
            )
        return self._async_client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Title": "revit-api-rag-intent-bridge",
            "HTTP-Referer": "https://github.com/imkcrevit/revit-api-rag",
        }

    def _build_payload(
        self, model_cfg: dict, prompt: str, temperature: float | None = None, stream: bool = False,
    ) -> dict:
        return {
            "model": model_cfg["model"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature if temperature is not None else model_cfg.get("temperature", 0.1),
            "max_tokens": self._max_tokens,
            "stream": stream,
        }

    def _should_fallback(self, status_code: int) -> bool:
        return status_code in (403, 429) or status_code >= 500

    def _log_call(self, model: str, prompt_len: int, response_len: int, duration_ms: float, success: bool, error: str = ""):
        if success:
            logger.info(
                "LLM call: model=%s prompt_len=%d response_len=%d duration_ms=%.0f",
                model, prompt_len, response_len, duration_ms,
            )
        else:
            logger.warning(
                "LLM call FAILED: model=%s prompt_len=%d duration_ms=%.0f error=%s",
                model, prompt_len, duration_ms, error,
            )

    # -------------------------------------------------------------------
    # Sync complete
    # -------------------------------------------------------------------

    def complete(self, prompt: str, temperature: float = 0.1) -> str:
        """Synchronous completion with primary → fallback → retry."""
        models = [self._primary, self._fallback]
        last_error: str | Exception = ""

        for attempt in range(self._max_retries + 1):
            model_cfg = models[0] if attempt < self._max_retries else models[-1]
            payload = self._build_payload(model_cfg, prompt, temperature)
            url = f"{self._base_url}/chat/completions"
            start = time.time()

            try:
                client = self._get_sync_client()
                resp = client.post(url, json=payload, headers=self._headers())
                duration_ms = (time.time() - start) * 1000

                if self._should_fallback(resp.status_code) and model_cfg is models[0]:
                    logger.warning("Primary %s returned %d, switching to fallback", model_cfg["model"], resp.status_code)
                    models[0], models[-1] = models[-1], models[0]
                    last_error = f"HTTP {resp.status_code}"
                    continue

                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                self._log_call(model_cfg["model"], len(prompt), len(content), duration_ms, True)
                return content

            except httpx.TimeoutException as e:
                duration_ms = (time.time() - start) * 1000
                err_msg = f"Timeout after {duration_ms:.0f}ms"
                self._log_call(model_cfg["model"], len(prompt), 0, duration_ms, False, err_msg)
                last_error = e
                # Timeout → try fallback model (might be faster)
                if model_cfg is models[0] and len(models) > 1:
                    models[0], models[-1] = models[-1], models[0]
                continue

            except Exception as e:
                duration_ms = (time.time() - start) * 1000
                self._log_call(model_cfg["model"], len(prompt), 0, duration_ms, False, str(e))
                last_error = e
                if model_cfg is models[0] and len(models) > 1:
                    models[0], models[-1] = models[-1], models[0]
                continue

        raise RuntimeError(f"LLM call failed after {self._max_retries + 1} attempts: {last_error}")

    # -------------------------------------------------------------------
    # Async complete
    # -------------------------------------------------------------------

    async def complete_async(self, prompt: str, temperature: float = 0.1) -> str:
        """Async completion with primary → fallback → retry."""
        models = [self._primary, self._fallback]
        last_error: str | Exception = ""

        for attempt in range(self._max_retries + 1):
            model_cfg = models[0] if attempt < self._max_retries else models[-1]
            payload = self._build_payload(model_cfg, prompt, temperature)
            url = f"{self._base_url}/chat/completions"
            start = time.time()

            try:
                client = self._get_async_client()
                resp = await client.post(url, json=payload, headers=self._headers())
                duration_ms = (time.time() - start) * 1000

                if self._should_fallback(resp.status_code):
                    body_preview = resp.text[:300] if resp.text else "(empty)"
                    logger.warning(
                        "Model %s returned %d (attempt %d/%d): %s",
                        model_cfg["model"], resp.status_code, attempt + 1,
                        self._max_retries + 1, body_preview,
                    )
                    if model_cfg is models[0] and len(models) > 1:
                        models[0], models[-1] = models[-1], models[0]
                    last_error = f"HTTP {resp.status_code}: {body_preview}"
                    continue

                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                self._log_call(model_cfg["model"], len(prompt), len(content), duration_ms, True)
                return content

            except httpx.TimeoutException as e:
                duration_ms = (time.time() - start) * 1000
                err_msg = f"Timeout after {duration_ms:.0f}ms"
                self._log_call(model_cfg["model"], len(prompt), 0, duration_ms, False, err_msg)
                last_error = e
                if model_cfg is models[0] and len(models) > 1:
                    models[0], models[-1] = models[-1], models[0]
                continue

            except Exception as e:
                duration_ms = (time.time() - start) * 1000
                self._log_call(model_cfg["model"], len(prompt), 0, duration_ms, False, str(e))
                last_error = e
                if model_cfg is models[0] and len(models) > 1:
                    models[0], models[-1] = models[-1], models[0]
                continue

        raise RuntimeError(f"LLM async call failed after {self._max_retries + 1} attempts: {last_error}")

    # -------------------------------------------------------------------
    # Async stream
    # -------------------------------------------------------------------

    async def stream(self, prompt: str, temperature: float = 0.1) -> AsyncGenerator[str, None]:
        """Async streaming with primary → fallback."""
        models = [self._primary, self._fallback]

        for model_cfg in models:
            payload = self._build_payload(model_cfg, prompt, temperature, stream=True)
            url = f"{self._base_url}/chat/completions"

            try:
                client = self._get_async_client()
                async with client.stream("POST", url, json=payload, headers=self._headers()) as resp:
                    if self._should_fallback(resp.status_code):
                        logger.warning("Stream: %s returned %d, trying fallback", model_cfg["model"], resp.status_code)
                        continue

                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            return
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue
                    return
            except Exception as e:
                logger.warning("Stream failed for %s: %s, trying fallback", model_cfg["model"], e)
                continue

        raise RuntimeError("LLM stream failed for all models")

    # -------------------------------------------------------------------
    # JSON extraction helper
    # -------------------------------------------------------------------

    @staticmethod
    def extract_json(text: str) -> dict[str, Any]:
        """
        Extract JSON from LLM response.
        Strips markdown fences → json.loads → regex fallback.
        """
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", text.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\n?\s*```\s*$", "", cleaned.strip())

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        logger.warning("Failed to extract JSON from LLM response: %s...", text[:200])
        return {}

    # -------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------

    def close(self):
        if self._sync_client and not self._sync_client.is_closed:
            self._sync_client.close()

    async def aclose(self):
        if self._async_client and not self._async_client.is_closed:
            await self._async_client.aclose()
