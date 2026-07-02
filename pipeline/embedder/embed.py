
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
    # 全量重建：先删除旧 collection，保证与 SQLite 完全对齐
    try:
        client.delete_collection("revit_api")
    except Exception:
        pass
    collection = client.get_or_create_collection(
        name="revit_api",
        metadata={"description": "Revit API documentation embeddings"}
    )

    # 断点续传：已存在的 id 直接跳过（upsert 幂等，重复运行安全）
    existing = set(collection.get(include=[])["ids"])

    for i in range(0, len(rows), batch_size):
        batch = [row for row in rows[i:i + batch_size] if str(row[0]) not in existing]
        if not batch:
            continue
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

        collection.upsert(
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
    将 SDK 代码数据向量化并存入 ChromaDB。

    读取新版 sdk_info 表（project, summary, content, mentioned_apis），
    仅使用 summary 字段作为 embedding 文本，大幅节省 token。
    兼容旧版 revit_sdk 表（fallback）。
    """
    os.makedirs(chromadb_dir, exist_ok=True)
    embedder = create_embedding(config)

    conn = sqlite3.connect(sdk_db_path)
    cursor = conn.cursor()

    # Prefer new sdk_info table; fall back to legacy revit_sdk
    try:
        cursor.execute("""
            SELECT id, project, summary, content, mentioned_apis
            FROM sdk_info
            WHERE summary IS NOT NULL AND summary != ''
        """)
        rows = cursor.fetchall()
        use_new_schema = True
    except sqlite3.OperationalError:
        try:
            cursor.execute("""
                SELECT id, project, filename, code, clean_code, description,
                       project_summary, file_purpose, use_case_category
                FROM revit_sdk
                WHERE code IS NOT NULL AND code != ''
            """)
            rows = cursor.fetchall()
            use_new_schema = False
        except sqlite3.OperationalError:
            rows = []
            use_new_schema = False

    conn.close()

    schema_label = "sdk_info (summary-only)" if use_new_schema else "revit_sdk (legacy)"
    print(f"从 {sdk_db_path} 读取 {len(rows)} 条 SDK 数据 [{schema_label}]")

    client = chromadb.PersistentClient(path=chromadb_dir)
    # 全量重建：先删除旧 collection，保证与 SQLite 完全对齐
    try:
        client.delete_collection("revit_sdk")
    except Exception:
        pass
    collection = client.get_or_create_collection(
        name="revit_sdk",
        metadata={"description": "Revit SDK sample code embeddings"}
    )

    # 断点续传：已存在的 id 直接跳过（upsert 幂等，重复运行安全）
    existing = set(collection.get(include=[])["ids"])

    for i in range(0, len(rows), batch_size):
        batch = [row for row in rows[i:i + batch_size] if str(row[0]) not in existing]
        if not batch:
            continue
        ids = [str(row[0]) for row in batch]

        texts = []
        metadatas = []

        for row in batch:
            if use_new_schema:
                # row: id, project, summary, content, mentioned_apis
                project = row[1] or ""
                summary = row[2] or ""
                mentioned_apis = row[4] or ""
                # Embedding text = summary only (concise, semantic-rich, minimal tokens)
                text = summary if summary else (row[3] or "")[:500]
                texts.append(text)
                metadatas.append({
                    "project": project,
                    "mentioned_apis": mentioned_apis,
                })
            else:
                # Legacy schema fallback
                # row: id, project, filename, code, clean_code, description, project_summary, file_purpose, use_case_category
                code = row[4] if len(row) > 4 and row[4] else (row[3] or "")
                desc = row[5] if len(row) > 5 else ""
                proj_summary = row[6] if len(row) > 6 else ""
                file_purpose = row[7] if len(row) > 7 else ""
                prefix = " | ".join(filter(None, [proj_summary or "", file_purpose or "", desc or ""]))
                text = f"{prefix}\n{code[:800]}" if prefix else code[:1000]
                texts.append(text)
                metadatas.append({"project": row[1] or "", "filename": row[2] or ""})

        embeddings = embedder.embed_texts(texts)

        collection.upsert(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas,
        )

        if (i // batch_size) % 10 == 0:
            print(f"  Code embedding 进度: {i + len(batch)}/{len(rows)}")

    _write_meta(chromadb_dir, config, len(rows), "RevitSDK Samples")
    print(f"Code 向量化完成，共 {len(rows)} 条，存入 {chromadb_dir}")
