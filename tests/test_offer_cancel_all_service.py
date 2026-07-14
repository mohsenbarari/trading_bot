import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.offer_cancel_all_service import (
    OfferCancelAllCandidate,
    OfferCancelAllItemResult,
    OfferCancelAllItemStatus,
    OfferCancelAllResult,
    _cancel_remote_candidate,
    _classify_local_conflict,
    cancel_all_active_offers_authoritatively,
    format_offer_cancel_all_bot_message,
)
from core.services.offer_expiry_service import OfferExpiryReason, OfferExpirySourceSurface
from models.offer import OfferStatus


def candidate(number: int, home_server: str = "foreign") -> OfferCancelAllCandidate:
    public_id = f"ofr_stage10_{number:020d}"
    offer = SimpleNamespace(
        id=number,
        offer_public_id=public_id,
        home_server=home_server,
    )
    return OfferCancelAllCandidate(
        offer_id=number,
        offer_public_id=public_id,
        home_server=home_server,
        snapshot_offer=offer,
    )


class FakeLease:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.release = AsyncMock()


class OfferCancelAllServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_result_payload_keeps_legacy_count_and_formats_partial_failure(self):
        result = OfferCancelAllResult(
            items=(
                OfferCancelAllItemResult(
                    offer_public_id=candidate(1).offer_public_id,
                    home_server="foreign",
                    status=OfferCancelAllItemStatus.CANCELLED,
                ),
                OfferCancelAllItemResult(
                    offer_public_id=candidate(2, "iran").offer_public_id,
                    home_server="iran",
                    status=OfferCancelAllItemStatus.ALREADY_INACTIVE,
                ),
                OfferCancelAllItemResult(
                    offer_public_id=candidate(3, "iran").offer_public_id,
                    home_server="iran",
                    status=OfferCancelAllItemStatus.FAILED,
                    error_code="remote_503",
                    retryable=True,
                ),
            ),
            remaining_active_count=1,
        )

        payload = result.to_payload()
        self.assertEqual(payload["cancelled_count"], 1)
        self.assertEqual(payload["already_inactive_count"], 1)
        self.assertEqual(payload["failed_count"], 1)
        self.assertFalse(payload["complete"])
        message = format_offer_cancel_all_bot_message(result)
        self.assertIn("کامل نشد", message)
        self.assertIn("لغونشده: 1", message)
        self.assertNotIn("تمام لفظ‌های فعال شما", message)

    async def test_remote_result_distinguishes_replay_inactive_and_failure(self):
        remote = candidate(10, "iran")
        success_forwarder = AsyncMock(
            return_value=(200, {"expired": True, "replayed": True})
        )
        inactive_forwarder = AsyncMock(
            return_value=(400, {"detail": "این لفظ قبلاً غیرفعال شده است."})
        )
        failed_forwarder = AsyncMock(
            return_value=(503, {"detail": "سرور مرجع در دسترس نیست."})
        )
        kwargs = {
            "candidate": remote,
            "owner_user_id": 7,
            "actor_user_id": 7,
            "source_surface": OfferExpirySourceSurface.TELEGRAM_BOT,
            "expire_reason": OfferExpiryReason.BOT_CANCEL_ALL,
            "include_command_identity": True,
        }

        replayed = await _cancel_remote_candidate(**kwargs, forwarder=success_forwarder)
        inactive = await _cancel_remote_candidate(**kwargs, forwarder=inactive_forwarder)
        failed = await _cancel_remote_candidate(**kwargs, forwarder=failed_forwarder)

        self.assertEqual(replayed.status, OfferCancelAllItemStatus.CANCELLED)
        self.assertTrue(replayed.replayed)
        self.assertEqual(inactive.status, OfferCancelAllItemStatus.ALREADY_INACTIVE)
        self.assertEqual(failed.status, OfferCancelAllItemStatus.FAILED)
        self.assertTrue(failed.retryable)
        payload = success_forwarder.await_args.args[1]
        self.assertIn("command_id", payload)
        self.assertIn("idempotency_key", payload)

    async def test_failure_of_one_candidate_does_not_abort_independent_candidate(self):
        local = candidate(1, "foreign")
        remote = candidate(2, "iran")
        local_failure = RuntimeError("isolated database failure")
        remote_success = OfferCancelAllItemResult(
            offer_public_id=remote.offer_public_id,
            home_server="iran",
            status=OfferCancelAllItemStatus.CANCELLED,
        )
        leases = [FakeLease(), FakeLease()]

        with patch(
            "core.services.offer_cancel_all_service._load_active_candidates",
            new=AsyncMock(return_value=(local, remote)),
        ), patch(
            "core.services.offer_cancel_all_service.try_acquire_offer_expiry_gate",
            new=AsyncMock(side_effect=leases),
        ), patch(
            "core.services.offer_cancel_all_service.current_server",
            return_value="foreign",
        ), patch(
            "core.services.offer_cancel_all_service._cancel_local_candidate",
            new=AsyncMock(side_effect=local_failure),
        ), patch(
            "core.services.offer_cancel_all_service._cancel_remote_candidate",
            new=AsyncMock(return_value=remote_success),
        ) as cancel_remote, patch(
            "core.services.offer_cancel_all_service._read_remaining_active_count",
            new=AsyncMock(return_value=0),
        ):
            result = await cancel_all_active_offers_authoritatively(
                owner_user_id=7,
                actor_user_id=7,
                source_surface=OfferExpirySourceSurface.WEBAPP,
                expire_reason=OfferExpiryReason.CANCEL_ALL,
                session_factory=object(),
            )

        self.assertEqual(result.cancelled_count, 1)
        self.assertEqual(result.failed_count, 1)
        self.assertEqual(result.items[0].error_code, "unexpected_error")
        cancel_remote.assert_awaited_once()
        for lease in leases:
            lease.release.assert_awaited_once()

    async def test_busy_gate_is_reported_and_later_candidate_still_runs(self):
        first = candidate(1, "foreign")
        second = candidate(2, "foreign")
        leases = [FakeLease(acquired=False), FakeLease(acquired=True)]
        second_success = OfferCancelAllItemResult(
            offer_public_id=second.offer_public_id,
            home_server="foreign",
            status=OfferCancelAllItemStatus.CANCELLED,
        )
        with patch(
            "core.services.offer_cancel_all_service._load_active_candidates",
            new=AsyncMock(return_value=(first, second)),
        ), patch(
            "core.services.offer_cancel_all_service.try_acquire_offer_expiry_gate",
            new=AsyncMock(side_effect=leases),
        ), patch(
            "core.services.offer_cancel_all_service.current_server",
            return_value="foreign",
        ), patch(
            "core.services.offer_cancel_all_service._cancel_local_candidate",
            new=AsyncMock(return_value=second_success),
        ) as cancel_local, patch(
            "core.services.offer_cancel_all_service._read_remaining_active_count",
            new=AsyncMock(return_value=1),
        ):
            result = await cancel_all_active_offers_authoritatively(
                owner_user_id=7,
                actor_user_id=7,
                source_surface=OfferExpirySourceSurface.WEBAPP,
                expire_reason=OfferExpiryReason.CANCEL_ALL,
                session_factory=object(),
            )

        self.assertEqual(result.failed_count, 1)
        self.assertEqual(result.cancelled_count, 1)
        self.assertEqual(result.items[0].error_code, "gate_busy")
        cancel_local.assert_awaited_once()
        leases[0].release.assert_not_awaited()
        leases[1].release.assert_awaited_once()

    async def test_stale_local_offer_is_already_inactive_only_after_status_check(self):
        local = candidate(9, "foreign")
        with patch(
            "core.services.offer_cancel_all_service._read_offer_status",
            new=AsyncMock(return_value=OfferStatus.COMPLETED),
        ):
            inactive = await _classify_local_conflict(object(), candidate=local)
        with patch(
            "core.services.offer_cancel_all_service._read_offer_status",
            new=AsyncMock(return_value=OfferStatus.ACTIVE),
        ):
            conflict = await _classify_local_conflict(object(), candidate=local)

        self.assertEqual(inactive.status, OfferCancelAllItemStatus.ALREADY_INACTIVE)
        self.assertEqual(conflict.status, OfferCancelAllItemStatus.FAILED)
        self.assertTrue(conflict.retryable)


if __name__ == "__main__":
    unittest.main()
