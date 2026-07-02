"""
会话管理 — 内存 dict，按 session_id 存储对话历史、用户设置

2 小时 TTL 自动清理过期会话。
"""
from __future__ import annotations

import time
import uuid
import threading
from dataclasses import dataclass, field


_TTL_SECONDS = 2 * 60 * 60  # 2 hours


@dataclass
class Session:
    session_id: str
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    # Chat history: list of {"role": "user"|"assistant", "content": str}
    history: list[dict] = field(default_factory=list)
    # User settings
    api_key: str = ""
    model_provider: str = ""
    # Cached last search results (for "show full code" feature)
    last_search_results: object = None

    def touch(self):
        self.last_active = time.time()

    def is_expired(self) -> bool:
        return (time.time() - self.last_active) > _TTL_SECONDS

    def add_message(self, role: str, content: str):
        self.history.append({"role": role, "content": content})
        self.touch()


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str | None = None) -> Session:
        with self._lock:
            self._cleanup()
            if session_id and session_id in self._sessions:
                s = self._sessions[session_id]
                s.touch()
                return s
            # Unknown / missing id → always mint a fresh server-side id.
            # Never reuse a client-supplied unknown id (prevents session fixation).
            new_id = uuid.uuid4().hex
            s = Session(session_id=new_id)
            self._sessions[new_id] = s
            return s

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            s = self._sessions.get(session_id)
            if s and not s.is_expired():
                s.touch()
                return s
            return None

    def _cleanup(self):
        """Remove expired sessions (called under lock)."""
        expired = [k for k, v in self._sessions.items() if v.is_expired()]
        for k in expired:
            del self._sessions[k]
