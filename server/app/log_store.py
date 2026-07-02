"""
交互日志存储 — SQLite 持久化用户输入/输出、IP、模块、耗时等信息

线程安全，支持按模块/日期/IP 筛选查询。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncGenerator

from functools import lru_cache

logger = logging.getLogger(__name__)


def _get_db_path() -> str:
    """Resolve log database path from config or default."""
    try:
        from server.app.deps import get_config, _resolve_data_dir
        config = get_config()
        data_dir = _resolve_data_dir(config)
        db_path = data_dir / "sqlite" / "interaction_logs.db"
    except Exception:
        db_path = Path("./data/sqlite/interaction_logs.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return str(db_path)


class InteractionLogStore:
    """Thread-safe SQLite store for interaction logs."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _get_db_path()
        self._lock = threading.Lock()
        self._init_db()
        self._purge_old(retention_days=90)

    def _purge_old(self, retention_days: int = 90):
        """Best-effort cleanup of logs older than retention_days (called at startup)."""
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
            deleted = self.delete_before(cutoff.strftime("%Y-%m-%d %H:%M:%S"))
            if deleted:
                logger.info("Purged %d interaction log(s) older than %d days", deleted, retention_days)
        except Exception as e:
            logger.warning("Log retention purge failed: %s", e)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with contextlib.closing(self._get_conn()) as conn, conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS interaction_logs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    NOT NULL,
                    module      TEXT    NOT NULL,
                    session_id  TEXT,
                    client_ip   TEXT,
                    user_agent  TEXT,
                    user_input  TEXT,
                    assistant_output TEXT,
                    model       TEXT,
                    duration_ms INTEGER,
                    status      TEXT    DEFAULT 'ok'
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_logs_ts ON interaction_logs(timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_logs_module ON interaction_logs(module)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_logs_ip ON interaction_logs(client_ip)"
            )
        logger.info(f"InteractionLogStore initialized at {self._db_path}")

    # ── Write ────────────────────────────────────────────────────────────

    def log(
        self,
        *,
        module: str,
        session_id: str | None = None,
        client_ip: str | None = None,
        user_agent: str | None = None,
        user_input: str | None = None,
        assistant_output: str | None = None,
        model: str | None = None,
        duration_ms: int | None = None,
        status: str = "ok",
    ):
        """Insert one interaction log record (thread-safe)."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        # Bound stored text size to avoid unbounded row growth
        if user_input is not None:
            user_input = user_input[:10000]
        if assistant_output is not None:
            assistant_output = assistant_output[:10000]
        with self._lock:
            try:
                with contextlib.closing(self._get_conn()) as conn, conn:
                    conn.execute(
                        """INSERT INTO interaction_logs
                           (timestamp, module, session_id, client_ip, user_agent,
                            user_input, assistant_output, model, duration_ms, status)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (ts, module, session_id, client_ip, user_agent,
                         user_input, assistant_output, model, duration_ms, status),
                    )
            except Exception as e:
                logger.error(f"Failed to write interaction log: {e}")

    # ── Read ─────────────────────────────────────────────────────────────

    def query(
        self,
        *,
        module: str | None = None,
        client_ip: str | None = None,
        keyword: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Query logs with filters. Returns {items, total, limit, offset}."""
        where_clauses: list[str] = []
        params: list = []

        if module:
            where_clauses.append("module = ?")
            params.append(module)
        if client_ip:
            where_clauses.append("client_ip LIKE ?")
            params.append(f"%{client_ip}%")
        if keyword:
            where_clauses.append("(user_input LIKE ? OR assistant_output LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        if start_date:
            where_clauses.append("timestamp >= ?")
            params.append(start_date)
        if end_date:
            where_clauses.append("timestamp <= ?")
            params.append(end_date)
        if status:
            where_clauses.append("status = ?")
            params.append(status)

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        with contextlib.closing(self._get_conn()) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM interaction_logs{where_sql}", params
            ).fetchone()[0]

            rows = conn.execute(
                f"SELECT * FROM interaction_logs{where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()

        items = [dict(row) for row in rows]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def stats(self) -> dict:
        """Aggregate stats: per-module counts, recent IPs, daily volume."""
        with contextlib.closing(self._get_conn()) as conn:
            # Per-module counts
            module_rows = conn.execute(
                "SELECT module, COUNT(*) as count FROM interaction_logs GROUP BY module ORDER BY count DESC"
            ).fetchall()

            # Recent unique IPs (last 100 records)
            ip_rows = conn.execute(
                """SELECT client_ip, COUNT(*) as count
                   FROM interaction_logs
                   WHERE client_ip IS NOT NULL AND client_ip != ''
                   GROUP BY client_ip ORDER BY count DESC LIMIT 20"""
            ).fetchall()

            # Daily volume (last 30 days)
            daily_rows = conn.execute(
                """SELECT DATE(timestamp) as day, COUNT(*) as count
                   FROM interaction_logs
                   GROUP BY DATE(timestamp)
                   ORDER BY day DESC LIMIT 30"""
            ).fetchall()

            # Total
            total = conn.execute(
                "SELECT COUNT(*) FROM interaction_logs"
            ).fetchone()[0]

            # Error count
            errors = conn.execute(
                "SELECT COUNT(*) FROM interaction_logs WHERE status != 'ok'"
            ).fetchone()[0]

        return {
            "total": total,
            "errors": errors,
            "by_module": [dict(r) for r in module_rows],
            "by_ip": [dict(r) for r in ip_rows],
            "daily": [dict(r) for r in daily_rows],
        }

    def delete_before(self, date_str: str) -> int:
        """Delete logs before a given date. Returns count deleted."""
        with self._lock:
            with contextlib.closing(self._get_conn()) as conn, conn:
                cursor = conn.execute(
                    "DELETE FROM interaction_logs WHERE timestamp < ?", (date_str,)
                )
                return cursor.rowcount


# ── Singleton ────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_log_store() -> InteractionLogStore:
    return InteractionLogStore()


# ── Helpers ──────────────────────────────────────────────────────────────

# Direct-connection IPs allowed to set X-Forwarded-For / X-Real-IP.
# Only when the immediate peer is a trusted reverse proxy do we honour the
# forwarded headers; otherwise XFF is attacker-controlled and must be ignored.
_TRUSTED_PROXIES = {"127.0.0.1", "::1"}


def get_client_ip(request) -> str:
    """Extract client IP, trusting proxy headers only from a known reverse proxy."""
    peer = request.client.host if request.client else None

    if peer in _TRUSTED_PROXIES:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip

    return peer or "unknown"


async def log_and_stream(
    sse_generator: AsyncGenerator[str, None],
    *,
    module: str,
    session_id: str | None = None,
    client_ip: str = "unknown",
    user_agent: str = "",
    user_input: str = "",
    model: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Wrap an SSE generator to capture tokens and log the full interaction
    after streaming completes. Transparent pass-through — does not alter output.
    """
    store = get_log_store()
    tokens: list[str] = []
    start = time.time()
    status = "ok"

    try:
        async for chunk in sse_generator:
            # Extract token data from SSE lines
            for line in chunk.split("\n"):
                if line.startswith("data: ") and "[DONE]" not in line:
                    try:
                        token = json.loads(line[6:])
                        if isinstance(token, str):
                            tokens.append(token)
                    except (json.JSONDecodeError, ValueError):
                        pass
            yield chunk
    except Exception as e:
        status = f"error: {e}"
        raise
    finally:
        duration_ms = int((time.time() - start) * 1000)
        output = "".join(tokens)
        # Offload the synchronous SQLite write off the event loop
        await asyncio.to_thread(
            store.log,
            module=module,
            session_id=session_id,
            client_ip=client_ip,
            user_agent=user_agent,
            user_input=user_input,
            assistant_output=output,
            model=model,
            duration_ms=duration_ms,
            status=status,
        )
