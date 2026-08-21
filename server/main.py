"""
入口 — FastAPI + React(/) 单端口 7860

Gradio is optional and disabled by default. Set ENABLE_GRADIO=1 only for
legacy comparison.

Usage:
    python -m server.main
"""
from __future__ import annotations

import logging
import os
import uvicorn
from fastapi import Depends, FastAPI
from starlette.requests import HTTPConnection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
# Suppress noisy httpx request logging (frontend calls itself via httpx)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
from fastapi.middleware.cors import CORSMiddleware

from server.app.api.routes import router
from server.app.api.log_routes import log_router
from server.app.api.skill_routes import skill_router
from server.app.deps import get_config

try:
    from intent_bridge.router import intent_router
except ImportError:
    intent_router = None

try:
    from mcp_bridge.router import bridge_router
except ImportError:
    bridge_router = None

try:
    from prompt_bridge.router import prompt_bridge_router
except ImportError:
    prompt_bridge_router = None

try:
    from text_studio.router import text_studio_router
except ImportError:
    text_studio_router = None


async def _bridge_auth(connection: HTTPConnection):
    """Unified auth dependency placeholder for the three bridge routers (P1-5).

    Framework hook — currently a no-op so behavior is unchanged. Enforce
    X-App-Token / admin auth here to lock down intent/mcp/prompt bridges.
    """
    # HTTPConnection works for both HTTP requests and WebSocket handshakes.
    # Keeping this parameter typed prevents FastAPI from exposing a bogus
    # required `request` query parameter on every bridge route.
    _ = connection
    return None


def create_app() -> FastAPI:
    config = get_config()
    server_cfg = config.get("server", {})

    fastapi_app = FastAPI(title="Revit API RAG", version="1.0.0")

    # CORS
    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=server_cfg.get(
            "cors_origins",
            ["http://localhost:7860", "http://127.0.0.1:7860"],
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routes
    fastapi_app.include_router(router)
    fastapi_app.include_router(log_router)
    fastapi_app.include_router(skill_router)

    # Bridge routers share a unified auth dependency placeholder (P1-5)
    _bridge_deps = [Depends(_bridge_auth)]

    # Intent Bridge routes
    if intent_router is not None:
        fastapi_app.include_router(intent_router, dependencies=_bridge_deps)

    # MCP Bridge routes (code generation + execution + tool solidification)
    if bridge_router is not None:
        fastapi_app.include_router(bridge_router, dependencies=_bridge_deps)

    # PromptBridge routes (designer prompt optimization)
    if prompt_bridge_router is not None:
        fastapi_app.include_router(prompt_bridge_router, dependencies=_bridge_deps)

    # TextStudio routes (personal text polishing & translation)
    if text_studio_router is not None:
        fastapi_app.include_router(text_studio_router)

    # Health check
    @fastapi_app.get("/health")
    async def health():
        return {"status": "ok"}

    # Warm the retriever off the event loop so the first real request doesn't
    # pay the cold-start cost (P2-15).
    @fastapi_app.on_event("startup")
    async def _warm_retriever():
        import asyncio
        from server.app.deps import get_retriever
        try:
            await asyncio.to_thread(get_retriever)
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).warning("Retriever warm-up failed: %s", e)

    from pathlib import Path
    react_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"

    # Gradio is optional and disabled by default; the React UI is primary.
    # When enabled it must be mounted BEFORE the React SPA catch-all route,
    # otherwise the catch-all shadows /app (P2-38). favicon_path is omitted
    # because mount_gradio_app ignores it when path != "/" (avoids the startup
    # UserWarning, P3-8).
    enable_gradio = (
        os.getenv("ENABLE_GRADIO", "").lower() in {"1", "true", "yes"}
        or bool(server_cfg.get("enable_gradio", False))
    )
    if enable_gradio:
        try:
            import gradio as gr
            from server.frontend.gradio_app import create_gradio_app
            gradio_app = create_gradio_app()
            fastapi_app = gr.mount_gradio_app(fastapi_app, gradio_app, path="/app")
            print("Gradio frontend mounted at /app")
        except ImportError:
            print("Gradio not installed — /app not available")
    else:
        print("Gradio disabled — React frontend is primary")

    # Mount React SPA at / (primary frontend) — catch-all registered last
    if react_dist.is_dir():
        from fastapi.staticfiles import StaticFiles
        from fastapi.responses import FileResponse

        # Serve static assets (JS/CSS) directly — must be before SPA catch-all
        fastapi_app.mount(
            "/assets",
            StaticFiles(directory=str(react_dist / "assets")),
            name="react-assets",
        )

        @fastapi_app.get("/")
        async def serve_react_root():
            return FileResponse(react_dist / "index.html")

        @fastapi_app.get("/{rest:path}")
        async def serve_react_spa(rest: str = ""):
            # Serve static files if they exist, otherwise SPA fallback
            file_path = (react_dist / rest).resolve()
            if rest and file_path.is_file() and file_path.is_relative_to(react_dist.resolve()):
                return FileResponse(file_path)
            return FileResponse(react_dist / "index.html")

        print(f"React frontend mounted at / (from {react_dist})")
    else:
        print(f"React frontend not found at {react_dist} — / not available")

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
        forwarded_allow_ips="127.0.0.1",
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
