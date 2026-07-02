"""
日志查询路由 — 读取交互日志、统计摘要

所有端点需要管理员密码验证（Header: X-Admin-Token 或 Query: token）

- GET  /api/logs       — 查询日志（支持筛选）
- GET  /api/logs/stats — 聚合统计
- DELETE /api/logs     — 清理旧日志
- GET  /api/logs/verify — 验证密码是否正确
"""
from __future__ import annotations

import hmac
import os
from fastapi import APIRouter, Query, Header, HTTPException, Depends
from fastapi.responses import JSONResponse

from server.app.log_store import get_log_store
from server.app.deps import get_config

log_router = APIRouter(prefix="/api/logs", tags=["logs"])


def _get_admin_password() -> str:
    return os.getenv("ADMIN_PASSWORD", "")


async def verify_admin(
    x_admin_token: str | None = Header(None),
    token: str | None = Query(None),
):
    """Dependency: reject request if admin password doesn't match."""
    password = _get_admin_password()
    if not password:
        raise HTTPException(503, "Admin password not configured")

    provided = x_admin_token or token or ""
    if not provided or not hmac.compare_digest(provided, password):
        raise HTTPException(403, "Unauthorized")


@log_router.get("/verify")
async def verify_token(
    x_admin_token: str | None = Header(None),
    token: str | None = Query(None),
):
    """Check if the provided admin token is valid."""
    password = _get_admin_password()
    if not password:
        return JSONResponse(status_code=503, content={"valid": False})
    provided = x_admin_token or token or ""
    valid = bool(provided) and hmac.compare_digest(provided, password)
    if not valid:
        return JSONResponse(status_code=403, content={"valid": False})
    return {"valid": True}


@log_router.get("", dependencies=[Depends(verify_admin)])
async def query_logs(
    module: str | None = Query(None, description="Filter by module name"),
    client_ip: str | None = Query(None, description="Filter by client IP (partial match)"),
    keyword: str | None = Query(None, description="Search in input/output text"),
    start_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    status: str | None = Query(None, description="Filter by status (ok/error)"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Query interaction logs with optional filters."""
    store = get_log_store()
    return store.query(
        module=module,
        client_ip=client_ip,
        keyword=keyword,
        start_date=start_date,
        end_date=end_date,
        status=status,
        limit=limit,
        offset=offset,
    )


@log_router.get("/stats", dependencies=[Depends(verify_admin)])
async def log_stats():
    """Aggregate statistics: per-module counts, IPs, daily volume."""
    store = get_log_store()
    return store.stats()


@log_router.delete("", dependencies=[Depends(verify_admin)])
async def delete_old_logs(
    before: str = Query(..., description="Delete logs before this date (YYYY-MM-DD)"),
):
    """Delete logs older than the specified date."""
    store = get_log_store()
    count = store.delete_before(before)
    return {"deleted": count, "before": before}
