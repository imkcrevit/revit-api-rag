"""
OpenAI Embedding Provider
"""
from .base import BaseEmbedding
from config import get_api_key


class OpenAIEmbedding(BaseEmbedding):

    def __init__(self, model: str = "text-embedding-3-small", dimension: int = 1536, api_key_env: str = "OPENAI_API_KEY"):
        from openai import OpenAI
        self._model = model
        self._dimension = dimension
        self._client = OpenAI(api_key=get_api_key(api_key_env))

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
