
"""
OpenAI Embedding Provider — 兼容 OpenRouter
OpenRouter 使用 OpenAI 兼容格式，只需设置 base_url
"""
from .base import BaseEmbedding
from config import get_api_key, get_proxy_url


class OpenAIEmbedding(BaseEmbedding):

    def __init__(self, model: str = "openai/text-embedding-3-large", dimension: int = 3072,
                 api_key_env: str = "OPENROUTER_API_KEY", base_url: str = "https://openrouter.ai/api/v1"):
        from openai import OpenAI
        import httpx

        self._model = model
        self._dimension = dimension

        proxy = get_proxy_url()
        self._http_client = httpx.Client(proxy=proxy) if proxy else None

        self._client = OpenAI(
            api_key=get_api_key(api_key_env),
            base_url=base_url,
            http_client=self._http_client,
        )

    def close(self) -> None:
        """关闭底层 httpx.Client（若存在），释放连接池。"""
        if self._http_client is not None:
            try:
                self._http_client.close()
            except Exception:
                pass

    def __enter__(self) -> "OpenAIEmbedding":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(model=self._model, input=texts)
        return [item.embedding for item in response.data]

    def embed_query(self, query: str) -> list[float]:
        response = self._client.embeddings.create(model=self._model, input=[query])
        return response.data[0].embedding
