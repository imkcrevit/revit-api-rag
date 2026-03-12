"""
通用 LLM 客户端封装

通过 OpenRouter 统一调用 Claude / OpenAI GPT / Gemini / DeepSeek 等模型，
配置来自 config.yaml 中的 llm 节。

推荐用于 SDK 代码归纳总结的模型（在 config.yaml 中设置 provider）：
  claude  → anthropic/claude-sonnet-4-5（推荐，质量高、速度快）
  openai  → openai/gpt-4.5-preview（最强 GPT）

所有模型均通过同一 OPENROUTER_API_KEY 访问，无需额外密钥。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Generator

import httpx

# SDK 代码归纳总结任务耗时较长，使用更宽松的超时
_DEFAULT_TIMEOUT = 120.0


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = _DEFAULT_TIMEOUT,
        proxy: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

        # Configure HTTP proxy if provided (helps with Gemini routing in restricted networks)
        if proxy:
            self._client = httpx.Client(
                timeout=timeout,
                proxy=proxy,
            )
        else:
            self._client = httpx.Client(timeout=timeout)

    def _build_request(
        self,
        prompt: str,
        system_prompt: str | None = None,
        stream: bool = False,
    ) -> tuple[str, dict, dict]:
        """Build request URL, headers, and payload."""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "revit-api-rag",
        }

        _system = system_prompt or (
            "You are an expert assistant for summarizing Revit SDK C# sample code. "
            "Always respond in English."
        )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _system},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": stream,
        }
        return url, headers, payload

    def generate_text(self, prompt: str, system_prompt: str | None = None) -> str:
        """
        使用 OpenRouter 兼容的 Chat Completions 接口生成文本。
        兼容 Claude / OpenAI / Gemini / DeepSeek 等所有 OpenRouter 模型。
        只返回第一条 message 的 content。
        """
        url, headers, payload = self._build_request(prompt, system_prompt, stream=False)

        resp = self._client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices") or []
        if not choices:
            return ""

        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        return str(content)

    def generate_stream(
        self, prompt: str, system_prompt: str | None = None
    ) -> Generator[str, None, None]:
        """
        流式生成文本，逐 token yield。
        使用 SSE (Server-Sent Events) 协议解析。
        """
        url, headers, payload = self._build_request(prompt, system_prompt, stream=True)

        with self._client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]  # strip "data: "
                if data_str.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    token = delta.get("content", "")
                    if token:
                        yield token
                except (json.JSONDecodeError, IndexError):
                    continue

    def stream_print(
        self, prompt: str, system_prompt: str | None = None
    ) -> str:
        """流式生成并实时打印，返回完整文本。"""
        full_text = []
        for token in self.generate_stream(prompt, system_prompt):
            print(token, end="", flush=True)
            full_text.append(token)
        print()  # final newline
        return "".join(full_text)


def create_llm_client(config: dict[str, Any], provider_override: str | None = None) -> LLMClient:
    """
    根据 config.yaml 中的 llm 配置创建 LLMClient。

    Args:
        config:           完整的 config.yaml 内容（dict）
        provider_override: 可临时覆盖 config 中的 provider，如 "claude" / "openai"
    """
    llm_cfg = config.get("llm", {})
    provider = provider_override or llm_cfg.get("provider", "claude")
    models_cfg = llm_cfg.get("models", {})
    model_cfg = models_cfg.get(provider, {})

    if not model_cfg:
        available = list(models_cfg.keys())
        raise RuntimeError(
            f"config.yaml 中未找到 provider='{provider}' 的配置。"
            f"当前可用 provider：{available}"
        )

    model = model_cfg.get("model")
    base_url = model_cfg.get("base_url") or config.get("openrouter", {}).get("base_url")
    api_key_env = (
        model_cfg.get("api_key_env")
        or config.get("openrouter", {}).get("api_key_env", "OPENROUTER_API_KEY")
    )
    api_key = os.getenv(api_key_env, "")

    if not model:
        raise RuntimeError(f"config.yaml 中 llm.models.{provider}.model 未设置。")
    if not base_url:
        raise RuntimeError(f"config.yaml 中 llm.models.{provider}.base_url 未设置。")
    if not api_key:
        raise RuntimeError(
            f"环境变量 {api_key_env} 未设置，无法调用 {provider} 模型。"
            f"请在 .env 文件或系统环境变量中添加：{api_key_env}=你的 OpenRouter API Key"
        )

    temperature = llm_cfg.get("temperature", 0.3)
    max_tokens  = llm_cfg.get("max_tokens", 4096)

    # Proxy: model-level override > global proxy config
    proxy_cfg   = config.get("proxy", {})
    proxy_url: str | None = None
    if proxy_cfg.get("enabled", False):
        proxy_url = proxy_cfg.get("https") or proxy_cfg.get("http")

    return LLMClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        proxy=proxy_url,
    )

