"""
Pydantic 数据模型 — API 请求/响应
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str | None = None
    show_full: bool = Field(default=False, description="Show full plugin code instead of brief mode")


class SearchRequest(BaseModel):
    query: str
    api_top_k: int = Field(default=15, ge=1, le=50)
    code_top_k: int = Field(default=5, ge=1, le=20)


class SettingsUpdate(BaseModel):
    api_key: str | None = Field(default=None, max_length=200)
    model: str | None = None


class ConfigResponse(BaseModel):
    available_models: list[dict]
    default_provider: str
    revit_version: str
