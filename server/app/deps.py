"""
依赖注入 — 单例初始化 RAGRetriever / SessionStore / Config

DATA_DIR 环境变量支持 Docker 和本地开发切换数据路径。
"""
from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache

from config import load_config
from pipeline.retriever import RAGRetriever
from pipeline.llm_client import LLMClient, create_llm_client


def _resolve_data_dir(config: dict) -> Path:
    """Resolve data root: DATA_DIR env > config.yaml relative paths."""
    env = os.getenv("DATA_DIR")
    if env:
        return Path(env)
    # Default: project root / data
    return Path(config.get("data", {}).get("sqlite_dir", "./data/sqlite")).parent


@lru_cache(maxsize=1)
def get_config() -> dict:
    return load_config()


@lru_cache(maxsize=1)
def get_retriever() -> RAGRetriever:
    config = get_config()
    data_dir = _resolve_data_dir(config)
    data_cfg = config.get("data", {})

    # Resolve paths: use DATA_DIR as root, fall back to config relative paths
    sqlite_dir = data_dir / "sqlite"
    chromadb_dir = data_dir / "chromadb"

    api_db = str(sqlite_dir / "revit_api.db")
    sdk_db = str(sqlite_dir / "revit_sdk.db")
    chromadb_api = str(chromadb_dir / "chromadb_api")
    chromadb_code = str(chromadb_dir / "chromadb_code")

    return RAGRetriever(
        config=config,
        api_db_path=api_db,
        sdk_db_path=sdk_db,
        chromadb_api_dir=chromadb_api,
        chromadb_code_dir=chromadb_code,
    )


def get_session_store():
    from server.app.session import SessionStore
    return _session_store_singleton()


@lru_cache(maxsize=1)
def _session_store_singleton():
    from server.app.session import SessionStore
    return SessionStore()


def _find_model_config(config: dict, model_name: str) -> dict | None:
    """Find model config by OpenAI-format model name (e.g. 'anthropic/claude-sonnet-4.6')."""
    models_cfg = config.get("llm", {}).get("models", {})
    for mcfg in models_cfg.values():
        if mcfg.get("model") == model_name:
            return mcfg
    return None


def create_llm_for_session(session) -> LLMClient:
    """
    Create an LLM client for a user session.
    Uses the user's custom API key/model if set, otherwise falls back to config defaults.
    """
    config = get_config()
    llm_cfg = config.get("llm", {})
    base_url = config.get("openrouter", {}).get("base_url", "https://openrouter.ai/api/v1")

    # Resolve model: session override (OpenAI format) > config default
    model = None
    if session.model_provider:
        # model_provider now stores OpenAI-format name like "anthropic/claude-sonnet-4.6"
        mcfg = _find_model_config(config, session.model_provider)
        if mcfg:
            model = mcfg.get("model")
            base_url = mcfg.get("base_url") or base_url

    if not model:
        provider = llm_cfg.get("provider", "claude")
        default_cfg = llm_cfg.get("models", {}).get(provider, {})
        model = default_cfg.get("model", "anthropic/claude-sonnet-4.6")
        base_url = default_cfg.get("base_url") or base_url

    # Resolve API key: session override > env var
    api_key = session.api_key or os.getenv("OPENROUTER_API_KEY", "")

    # Proxy from global config
    proxy_cfg = config.get("proxy", {})
    proxy_url = None
    if proxy_cfg.get("enabled", False):
        proxy_url = proxy_cfg.get("https") or proxy_cfg.get("http")

    return LLMClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=llm_cfg.get("temperature", 0.3),
        max_tokens=llm_cfg.get("max_tokens", 4096),
        proxy=proxy_url,
    )
