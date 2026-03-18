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
    Two-tier retriever: ChromaDB (semantic search) + SQLite (content store)
    with optional post-retrieval reranking.

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
        import logging
        self._log = logging.getLogger("pipeline.retriever")

        from pipeline.embedder.providers import create_embedding
        self._embedder = create_embedding(config)
        self._config = config

        # Retrieval config
        ret_cfg = config.get("retrieval", {})
        self._api_top_k = ret_cfg.get("api", {}).get("top_k", 30)
        self._api_rerank_n = ret_cfg.get("api", {}).get("rerank_top_n", 15)
        self._code_top_k = ret_cfg.get("code", {}).get("top_k", 5)
        self._code_rerank_n = ret_cfg.get("code", {}).get("rerank_top_n", 3)

        # ChromaDB collections
        import os
        for label, d in [("api", chromadb_api_dir), ("code", chromadb_code_dir)]:
            if not os.path.isdir(d):
                self._log.error(f"ChromaDB {label} dir missing: {d}")
            elif not os.path.exists(os.path.join(d, "chroma.sqlite3")):
                self._log.error(f"ChromaDB {label} dir has no chroma.sqlite3: {d} — "
                                "check volume mounts or COPY in Dockerfile")

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

        # Reranker (lazy init from config)
        self._reranker = None
        self._reranker_initialized = False

    def _detect_sdk_schema(self) -> bool:
        """Check if sdk_db uses the new sdk_info table."""
        try:
            conn = sqlite3.connect(self._sdk_db)
            conn.execute("SELECT id FROM sdk_info LIMIT 1")
            conn.close()
            return True
        except Exception:
            return False

    def _get_reranker(self):
        """Lazy-init reranker from config."""
        if not self._reranker_initialized:
            self._reranker_initialized = True
            try:
                from pipeline.reranker import create_reranker
                self._reranker = create_reranker(self._config)
            except Exception as e:
                self._log.warning(f"[_get_reranker] failed to init: {e}")
                self._reranker = None
        return self._reranker

    # ------------------------------------------------------------------
    # Query rewriting
    # ------------------------------------------------------------------

    _REWRITE_PROMPT = """\
You are a Revit API expert. Given a user query (possibly in Chinese), extract ALL relevant \
Revit API class names, method names, properties, enums, and English technical keywords.

Rules:
1. Translate non-English terms to their EXACT Revit API equivalents
2. Be EXHAUSTIVE — include parent classes, related interfaces, relevant enums
3. Include the full namespace path (e.g. Autodesk.Revit.DB.Wall)
4. Include method signatures, property names, and BuiltInParameter/BuiltInCategory enums
5. Think about what a developer would search for when implementing this feature
6. Output ONLY a JSON object, no explanation

Examples:
- "结构柱" → {{"keywords": "structural column FamilyInstance BuiltInCategory.OST_StructuralColumns NewFamilyInstance StructuralType", "api_terms": ["FamilyInstance", "FamilySymbol", "NewFamilyInstance", "StructuralType", "BuiltInCategory.OST_StructuralColumns", "Level", "XYZ"]}}
- "创建墙体" → {{"keywords": "create wall Wall.Create WallType Level Line CurveLoop", "api_terms": ["Wall", "Wall.Create", "WallType", "WallUtils", "CurtainGrid", "Line.CreateBound", "Level"]}}
- "获取房间面积" → {{"keywords": "room area Room get_Area SpatialElement BoundarySegment", "api_terms": ["Room", "SpatialElement", "Area", "Room.Area", "Room.get_BoundarySegments", "SpatialElementBoundaryOptions"]}}
- "修改墙类型" → {{"keywords": "change wall type WallType ChangeTypeId Element.ChangeTypeId GetTypeId", "api_terms": ["Element.ChangeTypeId", "Element.GetTypeId", "WallType", "FilteredElementCollector", "Wall"]}}
- "Part" → {{"keywords": "Part PartUtils PartMaker CreateParts DivideParts PartType", "api_terms": ["Part", "PartUtils", "PartMaker", "PartUtils.CreateParts", "PartUtils.AreElementsValidForCreateParts"]}}

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
        api_top_k: int | None = None,
        code_top_k: int | None = None,
        rewrite: bool = True,
    ) -> SearchResults:
        """
        Tier 0: (Optional) LLM query rewriting for better keyword alignment.
        Tier 1: Embed query → search both ChromaDB collections (broad recall).
        Tier 2: Fetch full records from SQLite by matched IDs.
        Tier 3: (Optional) Rerank results for precision using cross-encoder.

        When rewrite=False (fast mode), skips both query rewriting AND reranking.
        """
        import time as _time
        t0 = _time.monotonic()

        # Use config defaults if not specified
        final_api_n = api_top_k or self._api_rerank_n   # final output (e.g. 15)
        final_code_n = code_top_k or self._code_rerank_n  # final output (e.g. 3)

        # In fast mode: retrieve exactly final_api_n, skip rewrite + rerank
        # In full mode: retrieve broad (top_k), then rerank down to final_api_n
        use_rerank = rewrite  # rerank only in full mode
        retrieve_api_k = self._api_top_k if use_rerank else final_api_n
        retrieve_code_k = self._code_top_k if use_rerank else final_code_n

        # Tier 0: Query rewriting
        search_query = self.rewrite_query(query) if rewrite else query
        t_rewrite = _time.monotonic() - t0

        # Tier 1: Embedding + Vector search
        query_embedding = self._embedder.embed_query(search_query)
        t_embed = _time.monotonic() - t0 - t_rewrite

        api_raw = self._api_collection.query(
            query_embeddings=[query_embedding],
            n_results=retrieve_api_k,
        )
        code_raw = self._code_collection.query(
            query_embeddings=[query_embedding],
            n_results=retrieve_code_k,
        )

        # Tier 2: Hydrate from SQLite
        api_items = self._hydrate_api(api_raw)
        sdk_items = self._hydrate_sdk(code_raw)
        t_hydrate = _time.monotonic() - t0 - t_rewrite - t_embed

        # Tier 2.5: Keyword search on SQLite — merge with vector results
        # This catches exact name/API matches that embedding search misses
        kw_items = self._keyword_search_api(query, limit=final_api_n)
        if kw_items:
            existing_ids = {item.chromadb_id for item in api_items}
            merged_count = 0
            for kw_item in kw_items:
                if kw_item.chromadb_id not in existing_ids:
                    api_items.append(kw_item)
                    existing_ids.add(kw_item.chromadb_id)
                    merged_count += 1
                else:
                    # Keyword match is also in vector results — boost its ranking
                    for ai in api_items:
                        if ai.chromadb_id == kw_item.chromadb_id:
                            ai.distance = min(ai.distance, kw_item.distance)
                            break
            if merged_count:
                self._log.info(f"[search] merged {merged_count} keyword results")
            # Re-sort by distance after merging
            api_items.sort(key=lambda x: x.distance)

        # Tier 3: Rerank (only in full mode)
        t_rerank = 0.0
        reranker = self._get_reranker() if use_rerank else None
        if reranker and api_items:
            t_rr_start = _time.monotonic()
            # Build document texts for reranking
            api_docs = []
            for item in api_items:
                doc = f"{item.full_id or item.name}: {item.summary}"
                if item.syntax:
                    doc += f" | {item.syntax}"
                api_docs.append(doc)

            reranked = reranker.rerank(query, api_docs, top_n=final_api_n)
            # Reorder api_items by rerank scores
            reranked_items = []
            for rr in reranked:
                if rr.index < len(api_items):
                    item = api_items[rr.index]
                    item.distance = 1.0 - rr.score  # convert score to distance
                    reranked_items.append(item)
            api_items = reranked_items
            t_rerank = _time.monotonic() - t_rr_start
            self._log.info(f"[search] reranked {len(api_docs)} → {len(api_items)} "
                           f"in {t_rerank:.2f}s")
        else:
            # No reranker — just trim to final count
            api_items = api_items[:final_api_n]

        sdk_items = sdk_items[:final_code_n]

        elapsed = _time.monotonic() - t0
        self._log.info(
            f"[search] query={query[:40]!r} rewrite={t_rewrite:.2f}s "
            f"embed={t_embed:.2f}s hydrate={t_hydrate:.2f}s "
            f"rerank={t_rerank:.2f}s total={elapsed:.2f}s "
            f"api={len(api_items)} sdk={len(sdk_items)}"
        )

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
    # Keyword search (SQLite direct)
    # ------------------------------------------------------------------

    def _keyword_search_api(self, query: str, limit: int = 15) -> list[RetrievedItem]:
        """Search API docs by keyword matching on name/full_id/summary.

        Two-pass approach:
        1. Narrow SQL: rows where ALL tokens match in name/full_id (precise)
        2. Broad SQL: rows where ANY token matches (recall), lower priority
        Python scoring ranks by match quality, penalizes long compound names.
        """
        tokens = [t.strip() for t in query.lower().split() if len(t.strip()) >= 2]
        if not tokens:
            return []

        conn = sqlite3.connect(self._api_db)
        conn.row_factory = sqlite3.Row

        # Pass 1: ALL tokens in name or full_id (high precision)
        all_conditions = []
        all_params = []
        for token in tokens:
            like = f"%{token}%"
            all_conditions.append(
                "(LOWER(name) LIKE ? OR LOWER(full_id) LIKE ?)"
            )
            all_params.extend([like, like])
        all_where = " AND ".join(all_conditions)
        narrow_sql = (
            f"SELECT id, name, full_id, summary, info, syntax, parameters, remark "
            f"FROM revit_api WHERE {all_where} LIMIT 100"
        )
        narrow_rows = conn.execute(narrow_sql, all_params).fetchall()
        seen_ids = {r["id"] for r in narrow_rows}

        # Pass 2: ANY token in name/full_id/summary (broader recall)
        any_conditions = []
        any_params = []
        for token in tokens:
            like = f"%{token}%"
            any_conditions.append(
                "(LOWER(name) LIKE ? OR LOWER(full_id) LIKE ? OR LOWER(summary) LIKE ?)"
            )
            any_params.extend([like, like, like])
        any_where = " OR ".join(any_conditions)
        broad_sql = (
            f"SELECT id, name, full_id, summary, info, syntax, parameters, remark "
            f"FROM revit_api WHERE {any_where} LIMIT 200"
        )
        broad_rows = [
            r for r in conn.execute(broad_sql, any_params).fetchall()
            if r["id"] not in seen_ids
        ]
        conn.close()

        all_rows = list(narrow_rows) + broad_rows

        # Score each row in Python
        scored: list[tuple[float, dict]] = []
        for r in all_rows:
            name_l = (r["name"] or "").lower()
            fid_l = (r["full_id"] or "").lower()
            summary_l = (r["summary"] or "").lower()
            score = 0.0

            for token in tokens:
                # Name match is most valuable
                if token in name_l:
                    score += 5.0
                    # Bonus: token starts a name segment (Wall in "Wall.Create")
                    name_parts = re.split(r'[.\s(]', name_l)
                    if any(part == token or part.startswith(token) for part in name_parts):
                        score += 3.0
                # full_id match (less weight since full_id often contains namespace)
                elif token in fid_l:
                    score += 1.5
                # summary match
                if token in summary_l:
                    score += 0.5

            if score <= 0:
                continue

            # Penalize long/deeply-nested names
            name_len = len(r["name"] or "")
            dot_count = (r["name"] or "").count(".")
            if name_len > 80:
                score *= 0.3
            elif name_len > 60:
                score *= 0.5
            elif name_len > 40:
                score *= 0.7
            if dot_count >= 3:
                score *= 0.5
            # BuiltInFailures/BuiltInParameter are rarely the target API
            if name_l.startswith("builtinfailures."):
                score *= 0.3
            elif name_l.startswith("builtinparameter."):
                score *= 0.6

            # Strong bonus for ALL tokens matching in name
            name_matched = sum(1 for t in tokens if t in name_l)
            if name_matched == len(tokens):
                score *= 2.0  # all tokens in name — very relevant

            scored.append((score, dict(r)))

        scored.sort(key=lambda x: x[0], reverse=True)

        items = []
        for i, (sc, r) in enumerate(scored[:limit]):
            items.append(RetrievedItem(
                source="api",
                chromadb_id=str(r["id"]),
                distance=0.1 + i * 0.01,  # low distance = high relevance
                name=r["name"] or "",
                full_id=r["full_id"] or "",
                summary=r["summary"] or "",
                info=r["info"] or "",
                syntax=r["syntax"] or "",
                parameters=r["parameters"] or "",
                remark=r["remark"] or "",
            ))
        self._log.info(f"[keyword_search] query={query!r} tokens={tokens} "
                       f"found={len(items)} "
                       f"top3={[(s[1]['name'][:50], f'{s[0]:.1f}') for s in scored[:3]]}")
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
