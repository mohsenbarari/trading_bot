"""Test-only async checkpoints with no runtime control surface."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, TypeAlias


class AsyncCheckpoint(Protocol):
    async def __call__(self, name: str) -> None: ...  # pragma: no cover


AsyncCheckpointCallback: TypeAlias = AsyncCheckpoint | Callable[[str], Awaitable[None]]


class NoopAsyncCheckpoint:
    async def __call__(self, name: str) -> None:
        return None


NOOP_ASYNC_CHECKPOINT = NoopAsyncCheckpoint()
