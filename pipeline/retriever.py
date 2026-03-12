"""
Two-tier retriever — ChromaDB (semantic index) + SQLite (content store).

Architecture:
    ChromaDB holds lightweight embeddings (summary-based) + document IDs.
    SQLite holds the full content (code, API docs, parameters, etc.).
    At query time:
        0. (Optional) LLM query rewriting: extract Revit API keywords from user query
        1. Embed the rewritten query and search ChromaDB → ranked IDs + scores
        2. Batch-fetch full records from SQLite by ID
        3. Assemble structured context dicts ready for prompt injection

Public API:
    retriever = RAGRetriever(config, api_db, sdk_db, chromadb_api_dir, chromadb_code_dir)
    results   = retriever.search(query, api_top_k=15, code_top_k=5)
    context   = retriever.build_context(results)
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

import chromadb


@dataclass
class RetrievedItem:
    """A single retrieved item with full content from SQLite."""
    source: str               # "api" or "sdk"
    chromadb_id: str
    distance: float
    # API fields
    name: str = ""
    full_id: str = ""
    summary: str = ""
    info: str = ""
    syntax: str = ""
    parameters: str = ""
    remark: str = ""
    # SDK fields
    project: str = ""
    content: str = ""         # golden code snippet
    mentioned_apis: str = ""


@dataclass
class SearchResults:
    """Container for all retrieval results."""
    query: str
    rewritten_query: str = ""
    api_items: list[RetrievedItem] = field(default_factory=list)
    sdk_items: list[RetrievedItem] = field(default_factory=list)


class RAGRetriever:
    """
    Two-tier retriever: ChromaDB for semantic search, SQLite for full content.

    Works with both existing (pre-trained) API ChromaDB and new SDK ChromaDB
    without requiring re-embedding.
    """

    def __init__(
        self,
        config: dict[str, Any],
        api_db_path: str,
        sdk_db_path: str,
        chromadb_api_dir: str,
        chromadb_code_dir: str,
    ):
        from pipeline.embedder.providers import create_embedding
        self._embedder = create_embedding(config)
        self._config = config

        # ChromaDB collections
        self._api_collection = (
            chromadb.PersistentClient(path=chromadb_api_dir)
            .get_collection("revit_api")
        )
        self._code_collection = (
            chromadb.PersistentClient(path=chromadb_code_dir)
            .get_collection("revit_sdk")
        )

        # SQLite paths (opened per-query to avoid threading issues)
        self._api_db = api_db_path
        self._sdk_db = sdk_db_path

        # Detect SDK schema
        self._sdk_new_schema = self._detect_sdk_schema()

        # Query rewriting LLM (lazy init)
        self._rewrite_client = None

    def _detect_sdk_schema(self) -> bool:
        """Check if sdk_db uses the new sdk_info table."""
        try:
            conn = sqlite3.connect(self._sdk_db)
            conn.execute("SELECT id FROM sdk_info LIMIT 1")
            conn.close()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Query rewriting
    # ------------------------------------------------------------------

    _REWRITE_PROMPT = """\
You are a Revit API expert. Given a user query (possibly in Chinese), extract the most relevant \
Revit API class names, method names, and English technical keywords for semantic search.

Rules:
1. Translate non-English terms to their exact Revit API equivalents
2. Include the primary Revit API class/namespace (e.g. FamilyInstance, Wall, Document)
3. Include relevant method names (e.g. NewFamilyInstance, Create)
4. Keep it concise — output ONLY a JSON object, no explanation

Examples:
- "结构柱" → {{"keywords": "structural column FamilyInstance BuiltInCategory.OST_StructuralColumns", "api_terms": ["FamilyInstance", "StructuralColumn", "NewFamilyInstance"]}}
- "创建墙体" → {{"keywords": "create wall Wall.Create Wall WallType", "api_terms": ["Wall", "Wall.Create", "WallType"]}}
- "获取房间面积" → {{"keywords": "room area Room get_Area SpatialElement", "api_terms": ["Room", "Area", "SpatialElement"]}}

User query: {query}
"""

    def _get_rewrite_client(self):
        """Lazy-init the query rewriting LLM client."""
        if self._rewrite_client is None:
            from pipeline.llm_client import create_llm_client
            self._rewrite_client = create_llm_client(
                self._config, provider_override="gemini_flash"
            )
            self._rewrite_client.max_tokens = 256
            self._rewrite_client.temperature = 0.1
        return self._rewrite_client

    def rewrite_query(self, query: str) -> str:
        """
        Use LLM to extract Revit API keywords from user query.
        Returns enriched query string for embedding.
        Falls back to original query on any error.
        """
        try:
            client = self._get_rewrite_client()
            raw = client.generate_text(
                self._REWRITE_PROMPT.format(query=query),
                system_prompt="You are a Revit API keyword extractor. Output JSON only.",
            )
            raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
            raw = re.sub(r"\s*```$", "", raw.strip())
            result = json.loads(raw)
            keywords = result.get("keywords", "")
            api_terms = result.get("api_terms", [])
            # Combine: original query + extracted keywords + API terms
            enriched = f"{query} {keywords} {' '.join(api_terms)}"
            return enriched.strip()
        except Exception:
            return query

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        api_top_k: int = 15,
        code_top_k: int = 5,
        rewrite: bool = True,
    ) -> SearchResults:
        """
        Tier 0: (Optional) LLM query rewriting for better keyword alignment.
        Tier 1: Embed query → search both ChromaDB collections.
        Tier 2: Fetch full records from SQLite by matched IDs.
        """
        search_query = self.rewrite_query(query) if rewrite else query
        query_embedding = self._embedder.embed_query(search_query)

        api_raw = self._api_collection.query(
            query_embeddings=[query_embedding],
            n_results=api_top_k,
        )
        code_raw = self._code_collection.query(
            query_embeddings=[query_embedding],
            n_results=code_top_k,
        )

        api_items = self._hydrate_api(api_raw)
        sdk_items = self._hydrate_sdk(code_raw)

        return SearchResults(
            query=query, rewritten_query=search_query,
            api_items=api_items, sdk_items=sdk_items,
        )

    # ------------------------------------------------------------------
    # Hydrate from SQLite
    # ------------------------------------------------------------------

    def _hydrate_api(self, raw: dict) -> list[RetrievedItem]:
        """Fetch full API records from SQLite by ChromaDB IDs."""
        ids = raw["ids"][0] if raw["ids"] else []
        distances = raw["distances"][0] if raw["distances"] else []
        if not ids:
            return []

        conn = sqlite3.connect(self._api_db)
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id, name, full_id, summary, info, syntax, parameters, remark "
            f"FROM revit_api WHERE id IN ({placeholders})",
            [int(i) for i in ids],
        ).fetchall()
        conn.close()

        row_map = {str(r["id"]): r for r in rows}

        items = []
        for cid, dist in zip(ids, distances):
            r = row_map.get(cid)
            if r is None:
                continue
            items.append(RetrievedItem(
                source="api",
                chromadb_id=cid,
                distance=dist,
                name=r["name"] or "",
                full_id=r["full_id"] or "",
                summary=r["summary"] or "",
                info=r["info"] or "",
                syntax=r["syntax"] or "",
                parameters=r["parameters"] or "",
                remark=r["remark"] or "",
            ))
        return items

    def _hydrate_sdk(self, raw: dict) -> list[RetrievedItem]:
        """Fetch full SDK records from SQLite by ChromaDB IDs."""
        ids = raw["ids"][0] if raw["ids"] else []
        distances = raw["distances"][0] if raw["distances"] else []
        if not ids:
            return []

        conn = sqlite3.connect(self._sdk_db)
        conn.row_factory = sqlite3.Row

        placeholders = ",".join("?" * len(ids))
        int_ids = [int(i) for i in ids]

        if self._sdk_new_schema:
            rows = conn.execute(
                f"SELECT id, project, summary, content, mentioned_apis "
                f"FROM sdk_info WHERE id IN ({placeholders})",
                int_ids,
            ).fetchall()
            row_map = {str(r["id"]): r for r in rows}
        else:
            rows = conn.execute(
                f"SELECT id, project, filename, code, clean_code, description "
                f"FROM revit_sdk WHERE id IN ({placeholders})",
                int_ids,
            ).fetchall()
            row_map = {str(r["id"]): r for r in rows}

        conn.close()

        items = []
        for cid, dist in zip(ids, distances):
            r = row_map.get(cid)
            if r is None:
                continue
            if self._sdk_new_schema:
                items.append(RetrievedItem(
                    source="sdk",
                    chromadb_id=cid,
                    distance=dist,
                    project=r["project"] or "",
                    summary=r["summary"] or "",
                    content=r["content"] or "",
                    mentioned_apis=r["mentioned_apis"] or "",
                ))
            else:
                code = r["clean_code"] if r["clean_code"] else (r["code"] or "")
                items.append(RetrievedItem(
                    source="sdk",
                    chromadb_id=cid,
                    distance=dist,
                    project=r["project"] or "",
                    name=r["filename"] or "",
                    summary=r["description"] or "",
                    content=code,
                ))
        return items

    # ------------------------------------------------------------------
    # Context assembly
    # ------------------------------------------------------------------

    def build_context(
        self,
        results: SearchResults,
        api_max_chars: int = 600,
        code_max_chars: int = 2000,
    ) -> dict[str, str]:
        """
        Assemble structured context strings for LLM prompt injection.

        Returns:
            {"api_context": str, "code_context": str}
        """
        api_parts = []
        for item in results.api_items:
            header = item.full_id or item.name
            body_parts = []
            if item.summary:
                body_parts.append(item.summary)
            if item.syntax:
                body_parts.append(f"Syntax: {item.syntax}")
            if item.parameters:
                body_parts.append(f"Parameters: {item.parameters}")
            if item.remark:
                body_parts.append(f"Remarks: {item.remark}")
            if not body_parts and item.info:
                body_parts.append(item.info)
            body = "\n".join(body_parts)[:api_max_chars]
            api_parts.append(f"### {header}\n{body}")

        code_parts = []
        for item in results.sdk_items:
            header = f"Project: {item.project}"
            if item.mentioned_apis:
                header += f"  |  APIs: {item.mentioned_apis}"
            body = item.content[:code_max_chars] if item.content else item.summary
            code_parts.append(f"// {header}\n{body}")

        return {
            "api_context": "\n\n".join(api_parts),
            "code_context": "\n\n".join(code_parts),
        }

    # ------------------------------------------------------------------
    # Pretty-print
    # ------------------------------------------------------------------

    def format_results(self, results: SearchResults) -> str:
        """Format search results for display."""
        lines = []
        if results.rewritten_query and results.rewritten_query != results.query:
            lines.append(f"原始查询: {results.query}")
            lines.append(f"改写查询: {results.rewritten_query}")
            lines.append("")
        lines.append("=" * 60)
        lines.append("API 检索结果：")
        lines.append("=" * 60)
        for i, item in enumerate(results.api_items, 1):
            sim = 1 - item.distance
            lines.append(f"\n[{i}] 相似度: {sim:.4f}")
            lines.append(f"    名称: {item.full_id or item.name}")
            lines.append(f"    摘要: {item.summary[:150]}...")

        lines.append("\n" + "=" * 60)
        lines.append("SDK 代码检索结果：")
        lines.append("=" * 60)
        for i, item in enumerate(results.sdk_items, 1):
            sim = 1 - item.distance
            lines.append(f"\n[{i}] 相似度: {sim:.4f}")
            lines.append(f"    项目: {item.project}")
            lines.append(f"    摘要: {item.summary[:150]}...")
            if item.content:
                preview = item.content[:200].replace("\n", "\n    ")
                lines.append(f"    代码: {preview}...")

        return "\n".join(lines)
