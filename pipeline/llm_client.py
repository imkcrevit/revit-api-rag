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

import time

import httpx

# SDK 代码归纳总结任务耗时较长，使用更宽松的超时
_DEFAULT_TIMEOUT = 120.0

# 瞬时错误（OpenRouter / 上游 provider 偶发）——可重试。
# 403 经验上是 Anthropic 经 OpenRouter 的瞬时拒绝，并非持久权限问题，故纳入重试。
_RETRYABLE_STATUS = frozenset({403, 408, 409, 429, 500, 502, 503, 504, 529})
_MAX_RETRIES = 4          # 总尝试次数 = 1 + 重试
_BACKOFF_BASE = 1.5       # 退避基数（秒）：1.5, 3.0, 4.5 …
_MAX_403_RETRIES = 1      # 403 视为持久权限/额度问题：最多重试 1 次即熔断，不浪费退避时间


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

    def close(self) -> None:
        """关闭底层 httpx.Client，释放连接池。"""
        try:
            self._client.close()
        except Exception:
            pass

    def __enter__(self) -> "LLMClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _build_request(
        self,
        prompt: str,
        system_prompt: str | None = None,
        stream: bool = False,
        messages: list[dict] | None = None,
    ) -> tuple[str, dict, dict]:
        """Build request URL, headers, and payload.

        If `messages` is provided it is used verbatim as the chat messages array
        (structured multi-turn: system + per-role turns). Otherwise a simple
        [system, user] pair is built from `system_prompt` / `prompt`.
        """
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "revit-api-rag",
        }

        if messages is not None:
            msgs = messages
        else:
            _system = system_prompt or (
                "You are an expert assistant for summarizing Revit SDK C# sample code. "
                "Always respond in English."
            )
            msgs = [
                {"role": "system", "content": _system},
                {"role": "user", "content": prompt},
            ]

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": msgs,
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

        import logging
        _llm_log = logging.getLogger("pipeline.llm_client")

        resp = None
        last_err: Exception | None = None
        consecutive_403 = 0
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = self._client.post(url, headers=headers, json=payload)
                if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                    # 403 熔断：连续 403 超过 _MAX_403_RETRIES 次就停止重试，
                    # 避免把持久的权限/额度错误当成瞬时错误反复退避。
                    if resp.status_code == 403:
                        consecutive_403 += 1
                        if consecutive_403 > _MAX_403_RETRIES:
                            _llm_log.warning(
                                f"[generate_text] {self.model} HTTP 403 x{consecutive_403} "
                                "— circuit break, no more retries."
                            )
                            resp.raise_for_status()
                    else:
                        consecutive_403 = 0
                    body = resp.text[:300].replace("\n", " ")
                    wait = _BACKOFF_BASE * attempt
                    _llm_log.warning(
                        f"[generate_text] {self.model} HTTP {resp.status_code} "
                        f"(attempt {attempt}/{_MAX_RETRIES}) — retrying in {wait:.1f}s. Body: {body}"
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                break
            except (httpx.TransportError, httpx.TimeoutException) as e:
                # 网络层瞬时错误：同样重试
                last_err = e
                if attempt < _MAX_RETRIES:
                    wait = _BACKOFF_BASE * attempt
                    _llm_log.warning(
                        f"[generate_text] {self.model} transport error "
                        f"(attempt {attempt}/{_MAX_RETRIES}): {e} — retrying in {wait:.1f}s"
                    )
                    time.sleep(wait)
                    continue
                raise
            except httpx.HTTPStatusError as e:
                # 非重试状态码，或已用尽重试：把响应体一并抛出，便于诊断
                body = e.response.text[:500] if e.response is not None else ""
                raise httpx.HTTPStatusError(
                    f"{e} | OpenRouter response: {body}",
                    request=e.request, response=e.response,
                ) from None

        if resp is None:  # 理论上不会到这里
            raise last_err or RuntimeError("generate_text: no response")

        data = resp.json()

        choices = data.get("choices") or []
        if not choices:
            return ""

        choice = choices[0]
        finish_reason = choice.get("finish_reason", "unknown")
        message = choice.get("message") or {}
        content = message.get("content") or ""

        _llm_log.info(
            f"[generate_text] finish_reason={finish_reason} "
            f"content_len={len(content)} max_tokens={self.max_tokens}"
        )
        if finish_reason == "length":
            _llm_log.warning(
                f"[generate_text] RESPONSE TRUNCATED — hit max_tokens={self.max_tokens}. "
                f"Increase max_tokens in config."
            )

        return str(content)

    def generate_stream(
        self, prompt: str, system_prompt: str | None = None,
        messages: list[dict] | None = None,
    ) -> Generator[str, None, None]:
        """
        流式生成文本，逐 token yield。
        使用 SSE (Server-Sent Events) 协议解析。
        `messages` 可传入结构化多轮消息（system + 逐条 role）。
        """
        url, headers, payload = self._build_request(
            prompt, system_prompt, stream=True, messages=messages,
        )

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

