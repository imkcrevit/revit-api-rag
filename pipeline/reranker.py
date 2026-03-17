"""
Reranker — post-retrieval reranking using Cohere native API.

Flow:
    1. RAG retrieves top_k candidates from ChromaDB (broad recall)
    2. Reranker scores each candidate against the query (precision)
    3. Returns top_n best results sorted by relevance score

NOTE: Cohere rerank uses their native API (https://api.cohere.com/v2/rerank),
NOT OpenRouter. Set COHERE_API_KEY in environment or config.

Configured in config.yaml:
    rerank:
      enabled: true
      provider: "cohere"
      models:
        cohere:
          model: "rerank-v3.5"
          api_key_env: "COHERE_API_KEY"
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

_log = logging.getLogger("pipeline.reranker")

# Cohere native rerank endpoint
_COHERE_RERANK_URL = "https://api.cohere.com/v2/rerank"


@dataclass
class RerankResult:
    """A single reranked item with its relevance score."""
    index: int          # original index in the input list
    score: float        # relevance score (higher = better)
    text: str           # the document text that was scored


class Reranker:
    """Reranker using Cohere's native rerank API (fast, ~200ms)."""

    def __init__(
        self,
        model: str = "rerank-v3.5",
        api_key_env: str = "COHERE_API_KEY",
        proxy: str | None = None,
    ):
        self._model = model.replace("cohere/", "")  # strip provider prefix
        self._api_key = os.getenv(api_key_env, "")
        self._proxy = proxy

        if self._api_key:
            _log.info(f"[Reranker] initialized model={self._model}")
        else:
            _log.warning(f"[Reranker] no API key ({api_key_env}), rerank will be skipped")

    def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int = 15,
    ) -> list[RerankResult]:
        """Rerank documents against query, return top_n sorted by relevance.

        Uses Cohere's native v2 rerank endpoint — typically <1s for 30 docs.
        """
        if not documents:
            return []
        if not self._api_key:
            _log.debug("[rerank] no API key, skipping rerank")
            return [
                RerankResult(index=i, score=1.0 - i * 0.01, text=doc)
                for i, doc in enumerate(documents[:top_n])
            ]

        import time as _time
        t0 = _time.monotonic()

        try:
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self._model,
                "query": query,
                "documents": [{"text": d} for d in documents],
                "top_n": min(top_n, len(documents)),
                "return_documents": False,
            }

            client_kwargs: dict[str, Any] = {"timeout": 10.0}
            if self._proxy:
                client_kwargs["proxy"] = self._proxy

            with httpx.Client(**client_kwargs) as client:
                resp = client.post(_COHERE_RERANK_URL, headers=headers,
                                   json=payload)
                resp.raise_for_status()
                data = resp.json()

            results = []
            for item in data.get("results", []):
                idx = item.get("index", 0)
                score = item.get("relevance_score", 0.0)
                text = documents[idx] if idx < len(documents) else ""
                results.append(RerankResult(index=idx, score=score, text=text))

            results.sort(key=lambda r: r.score, reverse=True)
            elapsed = _time.monotonic() - t0
            _log.info(f"[rerank] {len(documents)} docs → top {len(results)} "
                      f"in {elapsed:.2f}s "
                      f"scores=[{', '.join(f'{r.score:.3f}' for r in results[:3])}...]")
            return results

        except httpx.HTTPStatusError as e:
            _log.warning(f"[rerank] HTTP {e.response.status_code}: "
                         f"{e.response.text[:200]}")
            return self._fallback(documents, top_n)
        except Exception as e:
            _log.warning(f"[rerank] failed ({e}), using fallback order")
            return self._fallback(documents, top_n)

    @staticmethod
    def _fallback(documents: list[str], top_n: int) -> list[RerankResult]:
        """Return documents in original (vector similarity) order."""
        return [
            RerankResult(index=i, score=1.0 - i * 0.01, text=doc)
            for i, doc in enumerate(documents[:top_n])
        ]


def create_reranker(config: dict) -> Reranker | None:
    """Create a Reranker from config.yaml, or None if disabled.

    Config format:
        rerank:
          enabled: true
          provider: "cohere"
          models:
            cohere:
              model: "rerank-v3.5"
              api_key_env: "COHERE_API_KEY"     # Cohere native key
    """
    rerank_cfg = config.get("rerank", {})
    if not rerank_cfg.get("enabled", False):
        _log.info("[create_reranker] rerank disabled in config")
        return None

    provider = rerank_cfg.get("provider", "cohere")
    model_cfg = rerank_cfg.get("models", {}).get(provider, {})

    if not model_cfg:
        _log.warning(f"[create_reranker] no config for provider={provider}")
        return None

    api_key_env = model_cfg.get("api_key_env", "COHERE_API_KEY")
    if not os.getenv(api_key_env, ""):
        _log.warning(f"[create_reranker] env var {api_key_env} not set, "
                     f"rerank will be disabled")
        return None

    proxy_cfg = config.get("proxy", {})
    proxy_url = None
    if proxy_cfg.get("enabled", False):
        proxy_url = proxy_cfg.get("https") or proxy_cfg.get("http")

    return Reranker(
        model=model_cfg.get("model", "rerank-v3.5"),
        api_key_env=api_key_env,
        proxy=proxy_url,
    )
