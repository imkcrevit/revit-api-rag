"""
入口 — FastAPI + Gradio 同进程，单端口 7860

Usage:
    python -m server.main
"""
from __future__ import annotations

import logging
import uvicorn
from fastapi import FastAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
# Suppress noisy httpx request logging (frontend calls itself via httpx)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
from fastapi.middleware.cors import CORSMiddleware

from server.app.api.routes import router
from server.app.deps import get_config

try:
    from intent_bridge.router import intent_router
except ImportError:
    intent_router = None

try:
    from mcp_bridge.router import bridge_router
except ImportError:
    bridge_router = None


def create_app() -> FastAPI:
    config = get_config()
    server_cfg = config.get("server", {})

    fastapi_app = FastAPI(title="Revit API RAG", version="1.0.0")

    # CORS
    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=server_cfg.get("cors_origins", ["*"]),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routes
    fastapi_app.include_router(router)

    # Intent Bridge routes
    if intent_router is not None:
        fastapi_app.include_router(intent_router)

    # MCP Bridge routes (code generation + execution + tool solidification)
    if bridge_router is not None:
        fastapi_app.include_router(bridge_router)

    # Health check
    @fastapi_app.get("/health")
    async def health():
        return {"status": "ok"}

    # Mount Gradio at root
    try:
        import gradio as gr
        from server.frontend.gradio_app import create_gradio_app
        gradio_app = create_gradio_app()
        fastapi_app = gr.mount_gradio_app(fastapi_app, gradio_app, path="/")
    except ImportError:
        print("Gradio not installed — running API-only mode")

    return fastapi_app


app = create_app()


def main():
    config = get_config()
    server_cfg = config.get("server", {})
    port = server_cfg.get("gradio_port", 7860)
    host = server_cfg.get("host", "0.0.0.0")

    uvicorn.run(
        "server.main:app",
        host=host,
        port=port,
        reload=False,
        forwarded_allow_ips="*",
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
