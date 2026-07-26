from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.market_intelligence import job_queue
from models.coin_intelligence_shadow import CoinIntelligenceShadowJob


NOW = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)


class FakeSession:
    def __init__(self) -> None:
        self.added = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def scalar(self, _statement):
        return None

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.committed = True


class JobQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_contains_only_normalized_local_identity(self) -> None:
        session = FakeSession()
        await job_queue.enqueue_project_job(
            kind="OFFER",
            local_id=42,
            requested_at_utc=NOW,
            session_factory=lambda: session,
        )

        row = next(
            item
            for item in session.added
            if isinstance(item, CoinIntelligenceShadowJob)
        )
        self.assertTrue(session.committed)
        self.assertEqual(row.job_kind, "PROJECT_OFFER")
        self.assertEqual(row.local_id, 42)
        self.assertEqual(row.payload, {})
        self.assertNotIn("text", row.payload)

    async def test_handler_failure_is_retried_not_acknowledged(self) -> None:
        job = SimpleNamespace(id="job", attempts=1, max_attempts=5)
        handler = AsyncMock(side_effect=RuntimeError("failure"))
        with patch.object(
            job_queue,
            "claim_job",
            new=AsyncMock(return_value=job),
        ), patch.object(
            job_queue,
            "fail_job",
            new=AsyncMock(return_value=True),
        ) as fail, patch.object(
            job_queue,
            "complete_job",
            new=AsyncMock(return_value=True),
        ) as complete:
            processed = await job_queue.process_one_job(
                worker_token="worker",
                handler=handler,
            )

        self.assertTrue(processed)
        fail.assert_awaited_once()
        complete.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
