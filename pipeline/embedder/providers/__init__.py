"""
Embedding Provider 工厂 — 根据配置创建对应的 Embedding 实例
"""
from .base import BaseEmbedding


def create_embedding(config: dict) -> BaseEmbedding:
    """根据配置创建 Embedding provider"""
    provider = config["embedding"]["provider"]
    model_config = config["embedding"]["models"][provider]

    if provider == "google":
        from .google import GoogleEmbedding
        return GoogleEmbedding(**model_config)

    elif provider == "openai":
        from .openai import OpenAIEmbedding
        return OpenAIEmbedding(**model_config)

    elif provider == "local_hf":
        # TODO: 实现本地 HuggingFace 模型 embedding
        raise NotImplementedError("本地 HF embedding 待实现")

    elif provider == "zhipu":
        # TODO: 实现智谱 embedding
        raise NotImplementedError("智谱 embedding 待实现")

    else:
        raise ValueError(f"不支持的 embedding provider: {provider}")
