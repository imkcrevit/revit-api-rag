"""
Google Embedding Provider — 使用 google-genai SDK
你的主要付费厂商，优先实现
"""
from .base import BaseEmbedding
from config import get_api_key


class GoogleEmbedding(BaseEmbedding):

    def __init__(self, model: str = "text-embedding-004", dimension: int = 768, api_key_env: str = "GOOGLE_API_KEY"):
        from google import genai
        self._model = model
        self._dimension = dimension
        self._client = genai.Client(api_key=get_api_key(api_key_env))

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量向量化，Google API 支持批量请求"""
        # google-genai SDK 批量 embedding
        result = self._client.models.embed_content(
            model=self._model,
            contents=texts,
        )
        return [e.values for e in result.embeddings]

    def embed_query(self, query: str) -> list[float]:
        """单条查询向量化"""
        result = self._client.models.embed_content(
            model=self._model,
            contents=[query],
        )
        return result.embeddings[0].values
