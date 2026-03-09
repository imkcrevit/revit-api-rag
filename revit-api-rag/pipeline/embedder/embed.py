"""
向量化模块 — 将 API/SDK 数据 embedding 后存入 ChromaDB

使用方法（在 Colab 中）：
    from pipeline.embedder.embed import embed_api_data, embed_code_data

    embed_api_data(config, api_db_path, chromadb_dir)
    embed_code_data(config, sdk_db_path, chromadb_dir)
"""
import os
import json
import sqlite3
from pathlib import Path
from datetime import datetime

import chromadb

from .providers import create_embedding


def _write_meta(output_dir: str, config: dict, record_count: int, source: str):
    """写入 meta.json，记录 embedding 模型信息，服务端启动时校验"""
    provider = config["embedding"]["provider"]
    model_config = config["embedding"]["models"][provider]

    meta = {
        "revit_version": config.get("revit_version", "unknown"),
        "embedding_provider": provider,
        "embedding_model": model_config.get("model", "unknown"),
        "embedding_dimension": model_config.get("dimension", 0),
        "created_at": datetime.now().isoformat(),
        "record_count": record_count,
        "source": source,
    }

    meta_path = os.path.join(output_dir, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"已写入 {meta_path}")


def embed_api_data(config: dict, api_db_path: str, chromadb_dir: str, batch_size: int = 50):
    """
    将 API 数据向量化并存入 ChromaDB

    Args:
        config: 全局配置
        api_db_path: revit_api.db 的路径
        chromadb_dir: ChromaDB 输出目录（如 ./data/chromadb/2026/api/）
        batch_size: 每批 embedding 的数量
    """
    os.makedirs(chromadb_dir, exist_ok=True)
    embedder = create_embedding(config)

    # 读取 SQLite 中的 API 数据
    conn = sqlite3.connect(api_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, info FROM revit_api WHERE name IS NOT NULL AND info IS NOT NULL")
    rows = cursor.fetchall()
    conn.close()

    print(f"从 {api_db_path} 读取 {len(rows)} 条 API 数据")

    # 创建 ChromaDB 集合
    client = chromadb.PersistentClient(path=chromadb_dir)
    collection = client.get_or_create_collection(
        name="revit_api",
        metadata={"description": "Revit API documentation embeddings"}
    )

    # 分批 embedding 并存入
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        ids = [str(row[0]) for row in batch]
        texts = [f"{row[1]} - {row[2]}" for row in batch]  # name + info 拼接
        metadatas = [{"name": row[1], "info": row[2]} for row in batch]

        embeddings = embedder.embed_texts(texts)

        collection.add(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas,
        )

        if (i // batch_size) % 10 == 0:
            print(f"  API embedding 进度: {i + len(batch)}/{len(rows)}")

    _write_meta(chromadb_dir, config, len(rows), "RevitAPI CHM")
    print(f"API 向量化完成，共 {len(rows)} 条，存入 {chromadb_dir}")


def embed_code_data(config: dict, sdk_db_path: str, chromadb_dir: str, batch_size: int = 20):
    """
    将 SDK 代码数据向量化并存入 ChromaDB

    Args:
        config: 全局配置
        sdk_db_path: revit_sdk.db 的路径
        chromadb_dir: ChromaDB 输出目录（如 ./data/chromadb/2026/code/）
        batch_size: 每批 embedding 的数量
    """
    os.makedirs(chromadb_dir, exist_ok=True)
    embedder = create_embedding(config)

    # 读取 SQLite 中的 SDK 数据
    conn = sqlite3.connect(sdk_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, project, filename, clean_code, description FROM revit_sdk WHERE clean_code IS NOT NULL")
    rows = cursor.fetchall()
    conn.close()

    print(f"从 {sdk_db_path} 读取 {len(rows)} 条 SDK 代码数据")

    # 创建 ChromaDB 集合
    client = chromadb.PersistentClient(path=chromadb_dir)
    collection = client.get_or_create_collection(
        name="revit_sdk",
        metadata={"description": "Revit SDK sample code embeddings"}
    )

    # 分批 embedding 并存入
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        ids = [str(row[0]) for row in batch]
        # 用 description + code 片段作为 embedding 文本
        texts = [f"{row[4] or ''}\n{row[3][:500]}" for row in batch]
        metadatas = [{"project": row[1], "filename": row[2]} for row in batch]

        embeddings = embedder.embed_texts(texts)

        collection.add(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas,
        )

        if (i // batch_size) % 5 == 0:
            print(f"  Code embedding 进度: {i + len(batch)}/{len(rows)}")

    _write_meta(chromadb_dir, config, len(rows), "RevitSDK Samples")
    print(f"Code 向量化完成，共 {len(rows)} 条，存入 {chromadb_dir}")
