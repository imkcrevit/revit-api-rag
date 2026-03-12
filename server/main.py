"""
入口 — FastAPI + Gradio 同进程，单端口 7860

Usage:
    python -m server.main
"""
from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.app.api.routes import router
from server.app.deps import get_config


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

    uvicorn.run("server.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
