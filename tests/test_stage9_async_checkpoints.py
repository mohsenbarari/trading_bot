from __future__ import annotations

import asyncio
import unittest

from core.async_checkpoints import NOOP_ASYNC_CHECKPOINT
from tests.stage9_checkpoint import DeterministicCheckpointBarrier


class Stage9AsyncCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_production_noop_never_blocks_or_changes_result(self):
        self.assertIsNone(await NOOP_ASYNC_CHECKPOINT("before_commit"))

    async def test_barrier_holds_only_named_boundary_deterministically(self):
        barrier = DeterministicCheckpointBarrier("after_invitation_lock")
        completed: list[str] = []

        async def contender() -> None:
            await barrier("before_invitation_lock")
            await barrier("after_invitation_lock")
            completed.append("released")

        task = asyncio.create_task(contender())
        await barrier.wait_until_arrived("after_invitation_lock")
        self.assertFalse(task.done())
        self.assertEqual(barrier.arrivals("before_invitation_lock"), 1)
        self.assertEqual(barrier.arrivals("after_invitation_lock"), 1)

        barrier.release("after_invitation_lock")
        await task
        self.assertEqual(completed, ["released"])

    async def test_unknown_barrier_name_fails_closed(self):
        barrier = DeterministicCheckpointBarrier("known")
        with self.assertRaisesRegex(ValueError, "checkpoint_not_held:unknown"):
            await barrier.wait_until_arrived("unknown")
        with self.assertRaisesRegex(ValueError, "checkpoint_not_held:unknown"):
            barrier.release("unknown")
