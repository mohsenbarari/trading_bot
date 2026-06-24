import unittest
from unittest.mock import AsyncMock, patch

from core import offer_publication_worker as worker


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class OfferPublicationWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_cycle_repairs_foreign_publications_with_active_gate_open(self):
        fake_db = object()
        report = {"status": "repaired", "processed": 2, "repaired": 2, "failed": 0, "gated": 0}

        with patch("core.offer_publication_worker.assert_background_job_authority") as authority, patch(
            "core.offer_publication_worker.active_publication_is_gated", new=AsyncMock(return_value=False)
        ) as gate, patch(
            "core.offer_publication_worker.AsyncSessionLocal", return_value=FakeSessionContext(fake_db)
        ), patch(
            "core.offer_publication_worker.reconcile_offer_publications", new=AsyncMock(return_value=report)
        ) as reconcile, patch(
            "api.routers.offers.send_offer_to_channel", new=AsyncMock(return_value=777)
        ) as send_offer:
            result = await worker.run_offer_telegram_publication_cycle(limit=3)

        authority.assert_called_once_with(worker.JOB_OFFER_TELEGRAM_PUBLICATION)
        gate.assert_awaited_once()
        reconcile.assert_awaited_once()
        _, kwargs = reconcile.await_args
        self.assertIs(kwargs["send_offer_to_channel"], send_offer)
        self.assertEqual(kwargs["server_mode"], "foreign")
        self.assertFalse(kwargs["dry_run"])
        self.assertEqual(kwargs["limit"], 3)
        self.assertTrue(kwargs["allow_active_publication"])
        self.assertEqual(result.processed, 2)
        self.assertEqual(result.repaired, 2)

    async def test_cycle_respects_active_publication_gate(self):
        fake_db = object()
        report = {"status": "gated", "processed": 1, "repaired": 0, "failed": 0, "gated": 1}

        with patch("core.offer_publication_worker.assert_background_job_authority"), patch(
            "core.offer_publication_worker.active_publication_is_gated", new=AsyncMock(return_value=True)
        ), patch(
            "core.offer_publication_worker.AsyncSessionLocal", return_value=FakeSessionContext(fake_db)
        ), patch(
            "core.offer_publication_worker.reconcile_offer_publications", new=AsyncMock(return_value=report)
        ) as reconcile, patch("api.routers.offers.send_offer_to_channel", new=AsyncMock()):
            result = await worker.run_offer_telegram_publication_cycle(limit=1)

        _, kwargs = reconcile.await_args
        self.assertFalse(kwargs["allow_active_publication"])
        self.assertEqual(result.status, "gated")
        self.assertEqual(result.gated, 1)
