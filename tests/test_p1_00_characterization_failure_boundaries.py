"""Inject named P1-00 family×boundary cases and assert state / side-effect / retry."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from datetime import timedelta

from core import offer_publication_worker as worker
from core import telegram_gateway
from core.enums import MessageType
from core.services import telegram_notification_outbox_service as tg_service
from core.services.bot_access_policy import BotAccessDecision
from core.services.chat_service import persist_sent_direct_message
from core.services.telegram_offer_channel_service import OfferChannelStateApplyResult
from core.utils import utc_now
from models.offer import OfferStatus, OfferType
from models.offer_publication_state import OfferPublicationStatus, OfferPublicationSurface
from models.telegram_notification_outbox import TelegramNotificationOutboxStatus
from tests.test_telegram_notification_outbox_service import FakeDeliveryDB, _outbox


class _FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeDB:
    def __init__(self):
        self.commit = AsyncMock()


def _make_offer(**overrides):
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


def _make_state(offer=None, **overrides):
    offer = offer or _make_offer()
    data = {
        "id": int(getattr(offer, "id", 1)) * 10,
        "offer_public_id": offer.offer_public_id,
        "surface": OfferPublicationSurface.TELEGRAM_CHANNEL,
        "status": OfferPublicationStatus.SENT,
        "offer_version_id": max(1, int(offer.version_id) - 1),
        "publisher_bot_identity": "primary",
        "telegram_chat_id": -100123,
        "telegram_message_id": offer.channel_message_id,
        "surface_resource_id": str(offer.channel_message_id) if offer.channel_message_id is not None else None,
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


def _candidate(offer, state=None):
    return worker.OfferChannelStateCandidate(offer=offer, state=state or _make_state(offer))


async def _run_channel_cycle(candidates, apply_result, *, limit=None, lock_result=True):
    fake_db = _FakeDB()
    backlog = worker.OfferChannelStateBacklog()
    with patch("core.offer_publication_worker.assert_background_job_authority"), patch(
        "core.offer_publication_worker.AsyncSessionLocal", return_value=_FakeSessionContext(fake_db)
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
        new=AsyncMock(
            side_effect=apply_result if isinstance(apply_result, list) else None,
            return_value=None if isinstance(apply_result, list) else apply_result,
        ),
    ) as apply_state, patch(
        "core.offer_publication_worker._channel_edit_spacing_seconds", return_value=0
    ):
        report = await worker.run_offer_channel_state_cycle(limit=limit or max(1, len(candidates)))
    return report, fake_db, apply_state


class P100FailureBoundaryCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_msg_failure_before_db_commit_rolls_back_and_skips_publish(self):
        """MSG failure_before_db_commit: commit raises; no durable message and no event publish."""
        db = SimpleNamespace(
            add=Mock(),
            commit=AsyncMock(side_effect=RuntimeError("injected failure_before_db_commit")),
            refresh=AsyncMock(),
            rollback=AsyncMock(),
        )
        sender = SimpleNamespace(id=11)
        receiver = SimpleNamespace(id=22)
        fake_message = SimpleNamespace(
            id=None,
            reply_to_message_id=None,
            forwarded_from_id=None,
            forwarded_from_name_override=None,
        )
        publish = AsyncMock()
        with patch("core.services.chat_service.Message", return_value=fake_message), patch(
            "api.routers.chat.publish_direct_message_event", new=publish
        ):
            with self.assertRaisesRegex(RuntimeError, "failure_before_db_commit"):
                await persist_sent_direct_message(
                    db,
                    sender=sender,
                    receiver=receiver,
                    content="hello",
                    message_type=MessageType.TEXT,
                )
        db.add.assert_called_once_with(fake_message)
        db.commit.assert_awaited_once()
        db.refresh.assert_not_awaited()
        publish.assert_not_awaited()
        self.assertIsNone(fake_message.id)

    async def test_offer_commit_before_event_side_effect_keeps_offer_and_schedules_retry(self):
        """OFFER commit_before_event_side_effect: committed Offer stays; 5xx side effect is retryable once."""
        offers = [
            _make_offer(id=21, offer_public_id="ofr_21", remaining_quantity=25, version_id=4),
            _make_offer(id=22, offer_public_id="ofr_22", remaining_quantity=25, version_id=5),
        ]
        items = [_candidate(offer) for offer in offers]
        report, fake_db, apply_state = await _run_channel_cycle(
            items,
            [
                OfferChannelStateApplyResult(ok=True, response_class="2xx", reason="ok"),
                OfferChannelStateApplyResult(ok=False, response_class="5xx", reason="telegram_server_error"),
            ],
        )
        self.assertEqual(apply_state.await_count, 2)
        self.assertEqual(report.retryable_failed, 1)
        self.assertEqual(fake_db.commit.await_count, 2)
        self.assertEqual(items[0].state.offer_version_id, offers[0].version_id)
        self.assertIsNotNone(worker._channel_state_next_retry_at(items[1].state))
        self.assertNotEqual(items[1].offer.status, "deleted")
        self.assertFalse(report.applied == 2)

    async def test_tg_commit_before_event_side_effect_keeps_outbox_when_gateway_fails(self):
        """TG commit_before_event_side_effect: committed outbox stays retryable after gateway 5xx; no duplicate send."""
        db = FakeDeliveryDB(user=SimpleNamespace(id=7, telegram_id=7777))
        outbox = _outbox(attempt_count=1)
        original_id = outbox.id
        gateway_send = AsyncMock(
            return_value=telegram_gateway.TelegramGatewayResult(
                ok=False,
                method="sendMessage",
                status_code=500,
                response_text="injected commit_before_event_side_effect",
            )
        )
        with patch.object(
            tg_service, "evaluate_bot_access", new=AsyncMock(return_value=BotAccessDecision(True))
        ), patch.object(
            tg_service, "get_active_customer_relation_for_user", new=AsyncMock(return_value=None)
        ):
            result = await tg_service.deliver_claimed_telegram_notification_outbox(
                db,
                outbox,
                current_server="foreign",
                gateway_send=gateway_send,
                now=_outbox().created_at,
            )
        gateway_send.assert_awaited_once()
        self.assertEqual(outbox.id, original_id)
        self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.RETRYABLE_FAILED)
        self.assertNotEqual(outbox.status, TelegramNotificationOutboxStatus.SENT)
        self.assertEqual(result.status, tg_service.TELEGRAM_NOTIFICATION_DELIVERY_STATUS_RETRY_PENDING)
        self.assertIsNone(outbox.terminal_at)
        self.assertIsNotNone(outbox.next_retry_at)


if __name__ == "__main__":
    unittest.main()
