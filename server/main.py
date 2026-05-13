"""
入口 — FastAPI + React(/) + Gradio(/app) 同进程，单端口 7860

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
    fastapi_app.include_router(log_router)
    fastapi_app.include_router(skill_router)

    # Intent Bridge routes
    if intent_router is not None:
        fastapi_app.include_router(intent_router)

    # MCP Bridge routes (code generation + execution + tool solidification)
    if bridge_router is not None:
        fastapi_app.include_router(bridge_router)

    # PromptBridge routes (designer prompt optimization)
    if prompt_bridge_router is not None:
        fastapi_app.include_router(prompt_bridge_router)

    # TextStudio routes (personal text polishing & translation)
    if text_studio_router is not None:
        fastapi_app.include_router(text_studio_router)

    # Health check
    @fastapi_app.get("/health")
    async def health():
        return {"status": "ok"}

    # Mount React SPA at / (primary frontend)
    import os
    from pathlib import Path
    react_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
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
            file_path = react_dist / rest
            if rest and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(react_dist / "index.html")

        print(f"React frontend mounted at / (from {react_dist})")
    else:
        print(f"React frontend not found at {react_dist} — / not available")

    # Mount Gradio at /app (backup — kept for comparison during migration)
    try:
        import gradio as gr
        from pathlib import Path as _Path
        from server.frontend.gradio_app import create_gradio_app
        gradio_app = create_gradio_app()
        _favicon = _Path(__file__).resolve().parent.parent / "images" / "graptolite-icon.svg"
        fastapi_app = gr.mount_gradio_app(
            fastapi_app, gradio_app, path="/app",
            favicon_path=str(_favicon) if _favicon.is_file() else None,
        )
        print("Gradio frontend mounted at /app")
    except ImportError:
        print("Gradio not installed — /app not available")

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
