import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.callbacks import ExpireOfferCallback
from core.services import telegram_notification_outbox_service as outbox_service
from core.services.telegram_offer_channel_service import build_offer_channel_message
from core.telegram_delivery_offer_success_contract import (
    OFFER_SUCCESS_COPY_LEGACY,
    OFFER_SUCCESS_COPY_TEXT,
    TELEGRAM_NOTIFICATION_SOURCE_OFFER_SUCCESS,
    build_offer_success_reply_markup,
    build_offer_success_text,
)
from core.telegram_delivery_offer_success_freshness import (
    build_telegram_offer_success_snapshot,
    telegram_offer_success_source_natural_id,
)
from core.telegram_delivery_runtime_composition import (
    configured_telegram_delivery_freshness_registry,
    configured_telegram_delivery_lifecycle_registry,
)
from models.telegram_notification_outbox import TelegramNotificationOutboxStatus


class TelegramDeliveryOfferSuccessContractTests(
    unittest.IsolatedAsyncioTestCase
):
    def _outbox(self):
        offer_text = "🟢خرید سکه 12 عدد\nقیمت: 123,456\n" + "\u2800" * 35
        text = build_offer_success_text(
            success_copy=OFFER_SUCCESS_COPY_LEGACY,
            offer_text=offer_text,
        )
        return SimpleNamespace(
            source_type=TELEGRAM_NOTIFICATION_SOURCE_OFFER_SUCCESS,
            source_id="ofr_success_1",
            recipient_user_id=7,
            telegram_id_at_enqueue=7007,
            dedupe_key=(
                "telegram-notification:offer_success_preview:ofr_success_1:7"
            ),
            text=text,
            parse_mode="Markdown",
            extra_payload={
                "offer_public_id": "ofr_success_1",
                "offer_version": 1,
                "preview_message_id": 81,
                "success_copy": OFFER_SUCCESS_COPY_LEGACY,
                "user_sync_version": 3,
            },
            status=TelegramNotificationOutboxStatus.PENDING,
        )

    def test_reply_markup_matches_real_callback_contract(self):
        markup = build_offer_success_reply_markup(91)
        self.assertEqual(
            markup["inline_keyboard"][0][0]["callback_data"],
            ExpireOfferCallback(offer_id=91).pack(),
        )

    def test_only_approved_success_copies_are_accepted(self):
        for copy in (OFFER_SUCCESS_COPY_LEGACY, OFFER_SUCCESS_COPY_TEXT):
            self.assertIn(
                copy,
                build_offer_success_text(success_copy=copy, offer_text="لفظ"),
            )
        with self.assertRaisesRegex(ValueError, "copy_invalid"):
            build_offer_success_text(success_copy="success", offer_text="لفظ")

    async def test_producer_stamps_immutable_offer_preview_contract(self):
        expected = SimpleNamespace(created=True)
        recipient = outbox_service.TelegramNotificationRecipient(
            user_id=7,
            telegram_id=7007,
        )
        with patch.object(
            outbox_service,
            "enqueue_telegram_notification_once",
            new=AsyncMock(return_value=expected),
        ) as enqueue:
            result = (
                await outbox_service.enqueue_offer_success_preview_notification_once(
                    object(),
                    recipient=recipient,
                    offer_public_id="ofr_success_1",
                    offer_version=2,
                    preview_message_id=81,
                    success_copy=OFFER_SUCCESS_COPY_TEXT,
                    offer_text="لفظ معتبر",
                    user_sync_version=4,
                )
            )
        self.assertIs(result, expected)
        self.assertEqual(
            enqueue.await_args.kwargs["source_type"],
            TELEGRAM_NOTIFICATION_SOURCE_OFFER_SUCCESS,
        )
        self.assertEqual(
            enqueue.await_args.kwargs["extra_payload"],
            {
                "offer_public_id": "ofr_success_1",
                "offer_version": 2,
                "preview_message_id": 81,
                "success_copy": OFFER_SUCCESS_COPY_TEXT,
                "user_sync_version": 4,
            },
        )

    def test_snapshot_keeps_original_private_message_identity(self):
        outbox = self._outbox()
        user = SimpleNamespace(id=7, telegram_id=7007, sync_version=3)
        offer = SimpleNamespace(
            id=19,
            user_id=7,
            offer_public_id="ofr_success_1",
            version_id=1,
            status="active",
            offer_type="buy",
            settlement_type=None,
            commodity=SimpleNamespace(name="سکه"),
            quantity=12,
            remaining_quantity=12,
            price=123456,
            notes=None,
        )
        outbox.text = build_offer_success_text(
            success_copy=OFFER_SUCCESS_COPY_LEGACY,
            offer_text=build_offer_channel_message(offer),
        )
        snapshot = build_telegram_offer_success_snapshot(outbox, user, offer)
        self.assertEqual(snapshot.payload["chat_id"], 7007)
        self.assertEqual(snapshot.payload["message_id"], 81)
        self.assertEqual(snapshot.payload["parse_mode"], "Markdown")
        self.assertTrue(telegram_offer_success_source_natural_id(outbox))

    def test_runtime_coverage_now_leaves_five_source_specific_actions(self):
        freshness = configured_telegram_delivery_freshness_registry(
            channel_id=-1001234567890
        ).coverage("primary")
        lifecycle = configured_telegram_delivery_lifecycle_registry(
            channel_id=-1001234567890
        ).coverage("primary")
        expected = {
            "cosmetic_cleanup",
            "delayed_restriction",
            "noncritical_market",
            "temporary_cleanup",
            "timed_security",
        }
        self.assertEqual(
            {action.value for action in freshness.missing_actions},
            expected,
        )
        self.assertEqual(freshness.missing_actions, lifecycle.missing_actions)


if __name__ == "__main__":
    unittest.main()
