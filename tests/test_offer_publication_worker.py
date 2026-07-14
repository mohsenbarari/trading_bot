import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import offer_publication_worker as worker
from core.services.telegram_offer_channel_service import OfferChannelStateApplyResult
from core.utils import utc_now
from models.offer import OfferStatus, OfferType
from models.offer_publication_state import OfferPublicationStatus, OfferPublicationSurface


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDB:
    def __init__(self):
        self.commit = AsyncMock()


def make_offer(**overrides):
    now = utc_now()
    data = {
        "id": 10,
        "offer_public_id": "ofr_10",
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
        "archived": False,
        "created_at": now - timedelta(minutes=2),
        "updated_at": now - timedelta(minutes=1),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_state(offer=None, **overrides):
    offer = offer or make_offer()
    data = {
        "id": int(getattr(offer, "id", 1)) * 10,
        "offer_public_id": offer.offer_public_id,
        "surface": OfferPublicationSurface.TELEGRAM_CHANNEL,
        "status": OfferPublicationStatus.SENT,
        "offer_version_id": max(1, int(offer.version_id) - 1),
        "telegram_message_id": offer.channel_message_id,
        "state_metadata": None,
        "last_attempt_at": None,
        "last_success_at": None,
        "next_retry_at": None,
        "error_code": None,
        "error_message": None,
        "disabled_at": None,
        "last_known_offer_status": OfferStatus.ACTIVE.value,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def candidate(offer, state=None):
    return worker.OfferChannelStateCandidate(offer=offer, state=state or make_state(offer))


class OfferPublicationWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_cycle_repairs_foreign_publications_with_active_gate_open(self):
        fake_db = object()
        report = {
            "status": "repaired",
            "processed": 2,
            "repaired": 2,
            "failed": 0,
            "gated": 0,
            "backlog_total": 4,
            "backlog_due": 3,
        }

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
        _, kwargs = reconcile.await_args
        self.assertIs(kwargs["send_offer_to_channel"], send_offer)
        self.assertEqual(kwargs["server_mode"], "foreign")
        self.assertFalse(kwargs["dry_run"])
        self.assertEqual(kwargs["limit"], 3)
        self.assertTrue(kwargs["allow_active_publication"])
        self.assertTrue(kwargs["collect_observability"])
        self.assertEqual(result.processed, 2)
        self.assertEqual(result.backlog_total, 4)
        self.assertEqual(result.backlog_due, 3)

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

    async def _run_channel_cycle(self, candidates, apply_result, *, limit=None, lock_result=True):
        fake_db = FakeDB()
        backlog = worker.OfferChannelStateBacklog()
        with patch("core.offer_publication_worker.assert_background_job_authority"), patch(
            "core.offer_publication_worker.AsyncSessionLocal", return_value=FakeSessionContext(fake_db)
        ), patch(
            "core.offer_publication_worker._load_channel_state_reconciliation_candidates",
            new=AsyncMock(return_value=list(candidates)),
        ), patch(
            "core.offer_publication_worker._channel_state_backlog", new=AsyncMock(return_value=backlog)
        ), patch(
            "core.offer_publication_worker._try_acquire_channel_state_lock",
            new=AsyncMock(return_value=lock_result),
        ), patch(
            "core.offer_publication_worker.apply_offer_channel_state_with_result",
            new=AsyncMock(side_effect=apply_result if isinstance(apply_result, list) else None,
                          return_value=None if isinstance(apply_result, list) else apply_result),
        ) as apply_state, patch(
            "core.offer_publication_worker._channel_edit_spacing_seconds", return_value=0
        ):
            report = await worker.run_offer_channel_state_cycle(limit=limit or max(1, len(candidates)))
        return report, fake_db, apply_state

    async def test_channel_state_cycle_applies_terminal_and_partial_published_offers(self):
        offers = [
            make_offer(id=21, offer_public_id="ofr_21", status=OfferStatus.COMPLETED, version_id=4),
            make_offer(
                id=22,
                offer_public_id="ofr_22",
                status=OfferStatus.ACTIVE,
                remaining_quantity=25,
                version_id=5,
            ),
        ]
        candidates = [candidate(offer) for offer in offers]
        report, fake_db, apply_state = await self._run_channel_cycle(
            candidates,
            [
                OfferChannelStateApplyResult(ok=True, response_class="2xx", reason="ok"),
                OfferChannelStateApplyResult(ok=False, response_class="5xx", reason="telegram_server_error"),
            ],
        )

        self.assertEqual(apply_state.await_count, 2)
        self.assertEqual(report.processed, 2)
        self.assertEqual(report.applied, 1)
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.retryable_failed, 1)
        self.assertEqual(dict(report.response_counts), {"2xx": 1, "5xx": 1})
        self.assertEqual(fake_db.commit.await_count, 2)
        self.assertEqual(candidates[0].state.offer_version_id, offers[0].version_id)
        self.assertIsNotNone(worker._channel_state_next_retry_at(candidates[1].state))

    async def test_channel_state_success_is_persistent_across_restart(self):
        offer = make_offer(id=31, offer_public_id="ofr_31", status=OfferStatus.COMPLETED, version_id=7)
        item = candidate(offer)
        report, _, apply_state = await self._run_channel_cycle(
            [item],
            OfferChannelStateApplyResult(ok=True, response_class="2xx", reason="ok"),
        )

        self.assertEqual(report.applied, 1)
        self.assertEqual(item.state.offer_version_id, offer.version_id)
        self.assertIsNone(worker._channel_state_next_retry_at(item.state))
        self.assertEqual(apply_state.await_count, 1)
        self.assertFalse(
            item.state.offer_version_id != offer.version_id
            or worker._channel_state_next_retry_at(item.state) is not None
        )

    async def test_channel_state_cycle_stops_on_rate_limit_and_schedules_retry(self):
        items = [
            candidate(make_offer(id=41, offer_public_id="ofr_41", version_id=8)),
            candidate(make_offer(id=42, offer_public_id="ofr_42", version_id=9)),
        ]
        report, fake_db, apply_state = await self._run_channel_cycle(
            items,
            OfferChannelStateApplyResult(
                ok=False,
                response_class="429",
                reason="telegram_rate_limited",
                retry_after_seconds=7,
            ),
        )

        self.assertEqual(apply_state.await_count, 1)
        self.assertEqual(fake_db.commit.await_count, 1)
        self.assertEqual(report.processed, 1)
        self.assertEqual(report.rate_limited, 1)
        self.assertEqual(report.cooldown_seconds, 7)
        self.assertIsNotNone(worker._channel_state_next_retry_at(items[0].state))
        self.assertEqual(items[0].state.state_metadata[worker._CHANNEL_STATE_RETRY_METADATA_KEY], 1)

    async def test_terminal_bad_request_is_persistently_set_aside(self):
        offer = make_offer(id=51, offer_public_id="ofr_51", status=OfferStatus.EXPIRED, version_id=10)
        item = candidate(offer)
        report, _, _ = await self._run_channel_cycle(
            [item],
            OfferChannelStateApplyResult(ok=False, response_class="400", reason="telegram_bad_request"),
        )

        self.assertEqual(report.failed, 1)
        self.assertEqual(report.non_retryable_remembered, 1)
        self.assertEqual(item.state.offer_version_id, offer.version_id)
        self.assertEqual(item.state.status, OfferPublicationStatus.DISABLED)
        self.assertIsNone(worker._channel_state_next_retry_at(item.state))
        self.assertEqual(item.state.error_code, "telegram_terminal_bad_request")

    async def test_active_bad_request_uses_bounded_retry(self):
        offer = make_offer(
            id=61,
            offer_public_id="ofr_61",
            status=OfferStatus.ACTIVE,
            remaining_quantity=25,
            version_id=11,
        )
        item = candidate(offer)
        report, _, _ = await self._run_channel_cycle(
            [item],
            OfferChannelStateApplyResult(ok=False, response_class="400", reason="telegram_bad_request"),
        )

        self.assertEqual(report.retryable_failed, 1)
        self.assertNotEqual(item.state.offer_version_id, offer.version_id)
        self.assertIsNotNone(worker._channel_state_next_retry_at(item.state))

    async def test_locked_candidate_is_skipped_without_provider_side_effect(self):
        item = candidate(make_offer(id=71, offer_public_id="ofr_71", version_id=12))
        report, fake_db, apply_state = await self._run_channel_cycle(
            [item],
            OfferChannelStateApplyResult(ok=True, response_class="2xx", reason="ok"),
            lock_result=False,
        )

        self.assertEqual(report.skipped_locked, 1)
        self.assertEqual(report.processed, 0)
        apply_state.assert_not_awaited()
        fake_db.commit.assert_awaited_once()

    async def test_backlog_of_101_candidates_drains_in_bounded_batches(self):
        items = [
            candidate(make_offer(id=index, offer_public_id=f"ofr_{index}", version_id=index + 2))
            for index in range(1, 102)
        ]
        fake_db = FakeDB()

        async def load_due(_db, *, limit, now=None):
            due = [
                item
                for item in items
                if item.state.offer_version_id != item.offer.version_id
                or worker._channel_state_next_retry_at(item.state) is not None
            ]
            return due[:limit]

        async def backlog(_db, *, now=None):
            remaining = sum(
                item.state.offer_version_id != item.offer.version_id
                or worker._channel_state_next_retry_at(item.state) is not None
                for item in items
            )
            return worker.OfferChannelStateBacklog(total=remaining, due=remaining)

        apply_state = AsyncMock(
            return_value=OfferChannelStateApplyResult(ok=True, response_class="2xx", reason="ok")
        )
        with patch("core.offer_publication_worker.assert_background_job_authority"), patch(
            "core.offer_publication_worker.AsyncSessionLocal", return_value=FakeSessionContext(fake_db)
        ), patch(
            "core.offer_publication_worker._load_channel_state_reconciliation_candidates", side_effect=load_due
        ), patch(
            "core.offer_publication_worker._channel_state_backlog", side_effect=backlog
        ), patch(
            "core.offer_publication_worker._try_acquire_channel_state_lock", new=AsyncMock(return_value=True)
        ), patch(
            "core.offer_publication_worker.apply_offer_channel_state_with_result", new=apply_state
        ), patch("core.offer_publication_worker._channel_edit_spacing_seconds", return_value=0):
            reports = [await worker.run_offer_channel_state_cycle(limit=25) for _ in range(5)]

        self.assertEqual(apply_state.await_count, 101)
        self.assertEqual(sum(report.applied for report in reports), 101)
        self.assertEqual(reports[-1].backlog_total, 0)
        self.assertTrue(all(item.state.offer_version_id == item.offer.version_id for item in items))

    async def test_permanent_first_failure_does_not_block_later_candidates(self):
        items = [
            candidate(make_offer(id=index, offer_public_id=f"ofr_{index}", version_id=index + 2))
            for index in range(1, 27)
        ]
        results = [
            OfferChannelStateApplyResult(ok=False, response_class="5xx", reason="telegram_server_error"),
            *[
                OfferChannelStateApplyResult(ok=True, response_class="2xx", reason="ok")
                for _ in range(25)
            ],
        ]
        report, _, apply_state = await self._run_channel_cycle(items, results)

        self.assertEqual(apply_state.await_count, 26)
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.applied, 25)
        self.assertIsNotNone(worker._channel_state_next_retry_at(items[0].state))
        self.assertTrue(all(item.state.offer_version_id == item.offer.version_id for item in items[1:]))

    async def test_two_concurrent_cycles_do_not_duplicate_channel_edit(self):
        item = candidate(make_offer(id=81, offer_public_id="ofr_81", version_id=13))
        fake_db = FakeDB()
        lock_results = iter([True, False])

        async def lock_once(_db, _offer):
            return next(lock_results)

        apply_state = AsyncMock(
            return_value=OfferChannelStateApplyResult(ok=True, response_class="2xx", reason="ok")
        )
        with patch("core.offer_publication_worker.assert_background_job_authority"), patch(
            "core.offer_publication_worker.AsyncSessionLocal", return_value=FakeSessionContext(fake_db)
        ), patch(
            "core.offer_publication_worker._load_channel_state_reconciliation_candidates",
            new=AsyncMock(return_value=[item]),
        ), patch(
            "core.offer_publication_worker._channel_state_backlog",
            new=AsyncMock(return_value=worker.OfferChannelStateBacklog()),
        ), patch(
            "core.offer_publication_worker._try_acquire_channel_state_lock", side_effect=lock_once
        ), patch(
            "core.offer_publication_worker.apply_offer_channel_state_with_result", new=apply_state
        ), patch("core.offer_publication_worker._channel_edit_spacing_seconds", return_value=0):
            reports = await asyncio.gather(
                worker.run_offer_channel_state_cycle(limit=1),
                worker.run_offer_channel_state_cycle(limit=1),
            )

        self.assertEqual(apply_state.await_count, 1)
        self.assertEqual(sum(report.applied for report in reports), 1)
        self.assertEqual(sum(report.skipped_locked for report in reports), 1)

    def test_retry_delay_is_exponential_and_bounded(self):
        with patch.object(worker.settings, "offer_publication_worker_retry_base_seconds", 5), patch.object(
            worker.settings, "offer_publication_worker_retry_max_seconds", 60
        ):
            delays = [worker._channel_state_retry_delay_seconds(attempt) for attempt in range(1, 8)]

        self.assertEqual(delays, [5, 10, 20, 40, 60, 60, 60])


if __name__ == "__main__":
    unittest.main()
