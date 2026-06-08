"""Request and job context helpers for structured logging.

The values here live in contextvars so async API requests, bot handlers, and
background jobs can attach correlation fields without passing them through every
function call.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_request_context: ContextVar[dict[str, Any]] = ContextVar("request_context", default={})


def get_request_context() -> dict[str, Any]:
    return dict(_request_context.get())


def set_request_context(**values: Any) -> None:
    current = get_request_context()
    for key, value in values.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    _request_context.set(current)


def clear_request_context() -> None:
    _request_context.set({})
