# core/request_context.py
"""Request-scoped logging context shared by API dependencies and log formatters."""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_user_id: ContextVar[int | None] = ContextVar("user_id", default=None)
_session_id: ContextVar[str | None] = ContextVar("session_id", default=None)
_actor_role: ContextVar[str | None] = ContextVar("actor_role", default=None)
_method: ContextVar[str | None] = ContextVar("method", default=None)
_path: ContextVar[str | None] = ContextVar("path", default=None)
_client_ip: ContextVar[str | None] = ContextVar("client_ip", default=None)


def set_request_context(
    *,
    request_id: str | None = None,
    method: str | None = None,
    path: str | None = None,
    client_ip: str | None = None,
) -> None:
    _request_id.set(request_id)
    _method.set(method)
    _path.set(path)
    _client_ip.set(client_ip)
    _user_id.set(None)
    _session_id.set(None)
    _actor_role.set(None)


def bind_actor_context(
    *,
    user_id: int | None = None,
    session_id: str | None = None,
    actor_role: str | None = None,
) -> None:
    if user_id is not None:
        _user_id.set(user_id)
    if session_id is not None:
        _session_id.set(session_id)
    if actor_role is not None:
        _actor_role.set(actor_role)


def clear_request_context() -> None:
    _request_id.set(None)
    _method.set(None)
    _path.set(None)
    _client_ip.set(None)
    _user_id.set(None)
    _session_id.set(None)
    _actor_role.set(None)


def get_request_context() -> dict[str, Any]:
    return {
        "request_id": _request_id.get(),
        "user_id": _user_id.get(),
        "session_id": _session_id.get(),
        "actor_role": _actor_role.get(),
        "method": _method.get(),
        "path": _path.get(),
        "client_ip": _client_ip.get(),
    }
