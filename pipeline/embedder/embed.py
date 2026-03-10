
"""
向量化模块 — 将 API/SDK 数据 embedding 后存入 ChromaDB
"""
import os
import json
import sqlite3
from pathlib import Path
from datetime import datetime

import chromadb

from .providers import create_embedding


def _write_meta(output_dir: str, config: dict, record_count: int, source: str):
    """写入 meta.json，记录 embedding 模型信息"""
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
    """
    os.makedirs(chromadb_dir, exist_ok=True)
    embedder = create_embedding(config)

    conn = sqlite3.connect(api_db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, name, full_id, summary, info "
        "FROM revit_api "
        "WHERE name IS NOT NULL"
    )
    rows = cursor.fetchall()
    conn.close()

    print(f"从 {api_db_path} 读取 {len(rows)} 条 API 数据")

    client = chromadb.PersistentClient(path=chromadb_dir)
    collection = client.get_or_create_collection(
        name="revit_api",
        metadata={"description": "Revit API documentation embeddings"}
    )

    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        ids = [str(row[0]) for row in batch]

        texts = []
        metadatas = []
        for row in batch:
            _id, name, full_id, summary, info = row

            if full_id and summary:
                text = f"{full_id} : {summary}"
            elif name and summary:
                text = f"{name} : {summary}"
            elif name and info:
                text = f"{name} - {info}"
            else:
                # 最后回退：合并所有可用字段
                parts = [p for p in [full_id, name, summary, info] if p]
                text = " - ".join(parts) if parts else ""

            texts.append(text)
            metadatas.append(
                {
                    "name": name,
                    "full_id": full_id,
                    "summary": summary,
                    "info": info,
                }
            )

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
    优先使用 clean_code，如果为空则回退到 code 字段
    """
    os.makedirs(chromadb_dir, exist_ok=True)
    embedder = create_embedding(config)

    conn = sqlite3.connect(sdk_db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, project, filename, code, clean_code, description 
        FROM revit_sdk 
        WHERE code IS NOT NULL AND code != ''
    """)
    rows = cursor.fetchall()
    conn.close()

    print(f"从 {sdk_db_path} 读取 {len(rows)} 条 SDK 代码数据")

    client = chromadb.PersistentClient(path=chromadb_dir)
    collection = client.get_or_create_collection(
        name="revit_sdk",
        metadata={"description": "Revit SDK sample code embeddings"}
    )

    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        ids = [str(row[0]) for row in batch]

        texts = []
        for row in batch:
            # 优先用 clean_code，没有则用 code
            code = row[4] if row[4] else row[3]
            desc = row[5] or ""
            # description + 代码前1000字符作为 embedding 文本
            text = f"{desc}\n{code[:1000]}" if desc else code[:1000]
            texts.append(text)

        metadatas = [{"project": row[1], "filename": row[2]} for row in batch]

        embeddings = embedder.embed_texts(texts)

        collection.add(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas,
        )

        if (i // batch_size) % 10 == 0:
            print(f"  Code embedding 进度: {i + len(batch)}/{len(rows)}")

    _write_meta(chromadb_dir, config, len(rows), "RevitSDK Samples")
    print(f"Code 向量化完成，共 {len(rows)} 条，存入 {chromadb_dir}")
