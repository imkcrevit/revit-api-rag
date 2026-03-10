"""
Embedding Provider 抽象基类
所有 Embedding 实现（OpenAI / Google / Local HF / 智谱）都继承此类
"""
from abc import ABC, abstractmethod


class BaseEmbedding(ABC):
    """Embedding 统一接口"""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """模型名称，用于 meta.json 记录"""
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        """向量维度"""
        pass

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量文本向量化"""
        pass

    @abstractmethod
    def embed_query(self, query: str) -> list[float]:
        """单条查询向量化"""
        pass
