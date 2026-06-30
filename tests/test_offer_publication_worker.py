import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import offer_publication_worker as worker
from core.services.telegram_offer_channel_service import OfferChannelStateApplyResult
from models.offer import OfferStatus, OfferType


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return FakeScalarResult(self._rows)


class FakeExecuteSession:
    def __init__(self, rows):
        self.rows = rows
        self.execute = AsyncMock(return_value=FakeExecuteResult(rows))


def make_offer(**overrides):
    data = {
        "id": 10,
        "offer_type": OfferType.BUY,
        "commodity": SimpleNamespace(name="سکه"),
        "quantity": 50,
        "remaining_quantity": 0,
        "price": 100000,
        "is_wholesale": False,
        "lot_sizes": [25],
        "notes": None,
        "status": OfferStatus.COMPLETED,
        "channel_message_id": 777,
        "version_id": 3,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class OfferPublicationWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        worker._channel_state_applied_signatures.clear()

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
            "api.routers.offers.send_offer_to_channel_with_result", new=AsyncMock(return_value=777)
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
        self.assertIn("telegram_send_spacing_seconds", kwargs)
        self.assertGreaterEqual(kwargs["telegram_send_spacing_seconds"], 0)

    async def test_cycle_maps_publication_rate_limit_to_cooldown(self):
        fake_db = object()
        report = {
            "status": "partial",
            "processed": 1,
            "repaired": 0,
            "failed": 1,
            "gated": 0,
            "telegram_rate_limited": 1,
            "telegram_retry_after_seconds": 6,
            "telegram_response_counts": {"429": 1},
        }

        with patch("core.offer_publication_worker.assert_background_job_authority"), patch(
            "core.offer_publication_worker.active_publication_is_gated", new=AsyncMock(return_value=False)
        ), patch(
            "core.offer_publication_worker.AsyncSessionLocal", return_value=FakeSessionContext(fake_db)
        ), patch(
            "core.offer_publication_worker.reconcile_offer_publications", new=AsyncMock(return_value=report)
        ), patch("api.routers.offers.send_offer_to_channel_with_result", new=AsyncMock()):
            result = await worker.run_offer_telegram_publication_cycle(limit=3)

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.rate_limited, 1)
        self.assertEqual(result.cooldown_seconds, 6)
        self.assertEqual(dict(result.response_counts), {"429": 1})

    async def test_cycle_respects_active_publication_gate(self):
        fake_db = object()
        report = {"status": "gated", "processed": 1, "repaired": 0, "failed": 0, "gated": 1}

        with patch("core.offer_publication_worker.assert_background_job_authority"), patch(
            "core.offer_publication_worker.active_publication_is_gated", new=AsyncMock(return_value=True)
        ), patch(
            "core.offer_publication_worker.AsyncSessionLocal", return_value=FakeSessionContext(fake_db)
        ), patch(
            "core.offer_publication_worker.reconcile_offer_publications", new=AsyncMock(return_value=report)
        ) as reconcile, patch("api.routers.offers.send_offer_to_channel_with_result", new=AsyncMock()):
            result = await worker.run_offer_telegram_publication_cycle(limit=1)

        _, kwargs = reconcile.await_args
        self.assertFalse(kwargs["allow_active_publication"])
        self.assertEqual(result.status, "gated")
        self.assertEqual(result.gated, 1)

    async def test_channel_state_cycle_applies_terminal_and_partial_published_offers(self):
        offers = [
            make_offer(id=21, status=OfferStatus.COMPLETED, remaining_quantity=0, version_id=4),
            make_offer(id=22, status=OfferStatus.ACTIVE, remaining_quantity=25, version_id=5),
        ]
        fake_db = FakeExecuteSession(offers)

        with patch("core.offer_publication_worker.assert_background_job_authority") as authority, patch(
            "core.offer_publication_worker.AsyncSessionLocal", return_value=FakeSessionContext(fake_db)
        ), patch(
            "core.offer_publication_worker.apply_offer_channel_state_with_result",
            new=AsyncMock(
                side_effect=[
                    OfferChannelStateApplyResult(ok=True, response_class="2xx", reason="ok"),
                    OfferChannelStateApplyResult(ok=False, response_class="5xx", reason="telegram_server_error"),
                ]
            ),
        ) as apply_state, patch(
            "core.offer_publication_worker._channel_edit_spacing_seconds", return_value=0.01
        ), patch("core.offer_publication_worker.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await worker.run_offer_channel_state_cycle(limit=2)

        authority.assert_called_once_with(worker.JOB_OFFER_TELEGRAM_PUBLICATION)
        fake_db.execute.assert_awaited_once()
        self.assertEqual(apply_state.await_count, 2)
        self.assertEqual(apply_state.await_args_list[0].kwargs["reason"], "offer_channel_state_reconcile")
        sleep.assert_awaited_once()
        self.assertEqual(result.processed, 2)
        self.assertEqual(result.applied, 1)
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.skipped_recent, 0)
        self.assertEqual(result.retryable_failed, 1)
        self.assertEqual(dict(result.response_counts), {"2xx": 1, "5xx": 1})

    async def test_channel_state_cycle_skips_already_applied_signature(self):
        offer = make_offer(id=31, status=OfferStatus.COMPLETED, remaining_quantity=0, version_id=7)
        fake_db = FakeExecuteSession([offer])

        with patch("core.offer_publication_worker.assert_background_job_authority"), patch(
            "core.offer_publication_worker.AsyncSessionLocal", return_value=FakeSessionContext(fake_db)
        ), patch(
            "core.offer_publication_worker.apply_offer_channel_state_with_result",
            new=AsyncMock(return_value=OfferChannelStateApplyResult(ok=True, response_class="2xx", reason="ok")),
        ) as apply_state:
            first = await worker.run_offer_channel_state_cycle(limit=1)
            second = await worker.run_offer_channel_state_cycle(limit=1)

        self.assertEqual(apply_state.await_count, 1)
        self.assertEqual(first.applied, 1)
        self.assertEqual(first.skipped_recent, 0)
        self.assertEqual(second.processed, 0)
        self.assertEqual(second.applied, 0)
        self.assertEqual(second.skipped_recent, 1)

    async def test_channel_state_cycle_stops_on_rate_limit_and_sets_cooldown(self):
        offers = [
            make_offer(id=41, status=OfferStatus.COMPLETED, remaining_quantity=0, version_id=8),
            make_offer(id=42, status=OfferStatus.COMPLETED, remaining_quantity=0, version_id=9),
        ]
        fake_db = FakeExecuteSession(offers)

        with patch("core.offer_publication_worker.assert_background_job_authority"), patch(
            "core.offer_publication_worker.AsyncSessionLocal", return_value=FakeSessionContext(fake_db)
        ), patch(
            "core.offer_publication_worker.apply_offer_channel_state_with_result",
            new=AsyncMock(
                return_value=OfferChannelStateApplyResult(
                    ok=False,
                    response_class="429",
                    reason="telegram_rate_limited",
                    retry_after_seconds=7,
                )
            ),
        ) as apply_state, patch("core.offer_publication_worker.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await worker.run_offer_channel_state_cycle(limit=2)

        self.assertEqual(apply_state.await_count, 1)
        sleep.assert_not_awaited()
        self.assertEqual(result.processed, 1)
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.rate_limited, 1)
        self.assertEqual(result.retryable_failed, 1)
        self.assertEqual(result.cooldown_seconds, 7)
        self.assertEqual(dict(result.response_counts), {"429": 1})

    async def test_channel_state_cycle_remembers_terminal_bad_request_signature(self):
        offer = make_offer(id=51, status=OfferStatus.EXPIRED, remaining_quantity=30, version_id=10)
        fake_db = FakeExecuteSession([offer])

        with patch("core.offer_publication_worker.assert_background_job_authority"), patch(
            "core.offer_publication_worker.AsyncSessionLocal", return_value=FakeSessionContext(fake_db)
        ), patch(
            "core.offer_publication_worker.apply_offer_channel_state_with_result",
            new=AsyncMock(
                return_value=OfferChannelStateApplyResult(
                    ok=False,
                    response_class="400",
                    reason="telegram_bad_request",
                )
            ),
        ) as apply_state:
            first = await worker.run_offer_channel_state_cycle(limit=1)
            second = await worker.run_offer_channel_state_cycle(limit=1)

        self.assertEqual(apply_state.await_count, 1)
        self.assertEqual(first.processed, 1)
        self.assertEqual(first.failed, 1)
        self.assertEqual(first.bad_request, 1)
        self.assertEqual(first.non_retryable_remembered, 1)
        self.assertEqual(second.processed, 0)
        self.assertEqual(second.skipped_recent, 1)

    async def test_channel_state_cycle_keeps_active_bad_request_visible(self):
        offer = make_offer(id=61, status=OfferStatus.ACTIVE, remaining_quantity=25, version_id=11)
        fake_db = FakeExecuteSession([offer])

        with patch("core.offer_publication_worker.assert_background_job_authority"), patch(
            "core.offer_publication_worker.AsyncSessionLocal", return_value=FakeSessionContext(fake_db)
        ), patch(
            "core.offer_publication_worker.apply_offer_channel_state_with_result",
            new=AsyncMock(
                return_value=OfferChannelStateApplyResult(
                    ok=False,
                    response_class="400",
                    reason="telegram_bad_request",
                )
            ),
        ) as apply_state:
            first = await worker.run_offer_channel_state_cycle(limit=1)
            second = await worker.run_offer_channel_state_cycle(limit=1)

        self.assertEqual(apply_state.await_count, 2)
        self.assertEqual(first.failed, 1)
        self.assertEqual(first.bad_request, 1)
        self.assertEqual(first.non_retryable_remembered, 0)
        self.assertEqual(first.retryable_failed, 1)
        self.assertEqual(second.processed, 1)
        self.assertEqual(second.skipped_recent, 0)
