"""Deterministic checkpoint barrier used by Stage 9 race tests."""

from __future__ import annotations

import asyncio
from collections import Counter


class DeterministicCheckpointBarrier:
    def __init__(self, *held_names: str) -> None:
        if not held_names:
            raise ValueError("at_least_one_checkpoint_required")
        self._held_names = frozenset(held_names)
        self._arrivals = Counter()
        self._arrived = {name: asyncio.Event() for name in self._held_names}
        self._released = {name: asyncio.Event() for name in self._held_names}

    async def __call__(self, name: str) -> None:
        self._arrivals[name] += 1
        if name not in self._held_names:
            return
        self._arrived[name].set()
        await self._released[name].wait()

    async def wait_until_arrived(self, name: str) -> None:
        try:
            event = self._arrived[name]
        except KeyError as exc:
            raise ValueError(f"checkpoint_not_held:{name}") from exc
        await event.wait()

    def release(self, name: str) -> None:
        try:
            self._released[name].set()
        except KeyError as exc:
            raise ValueError(f"checkpoint_not_held:{name}") from exc

    def arrivals(self, name: str) -> int:
        return int(self._arrivals[name])
