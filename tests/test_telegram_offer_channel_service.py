import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services import telegram_offer_channel_service as channel_service
from core.enums import SettlementType
from core.telegram_delivery_runtime_policy import TelegramDeliveryRuntimeMode
from models.offer import OfferStatus, OfferType


class FakeHttpClientContext:
    def __init__(self, *, response=None, responses=None, error=None):
        self.response = response
        self.responses = list(responses or [])
        self.error = error
        self.post = AsyncMock(side_effect=self._post)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def _post(self, *_args, **_kwargs):
        if self.error is not None:
            raise self.error
        if self.responses:
            return self.responses.pop(0)
        return self.response


def make_offer(**overrides):
    data = {
        "id": 10,
        "offer_type": OfferType.BUY,
        "settlement_type": SettlementType.CASH,
        "commodity": SimpleNamespace(name="سکه"),
        "quantity": 30,
        "remaining_quantity": 0,
        "price": 51000,
        "is_wholesale": True,
        "lot_sizes": None,
        "notes": None,
        "status": OfferStatus.COMPLETED,
        "expire_reason": None,
        "channel_message_id": 123,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class TelegramOfferChannelServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_zero_remaining_quantity_never_builds_trade_buttons(self):
        self.assertIsNone(channel_service.build_offer_channel_reply_markup(make_offer()))

    def test_history_tag_contract(self):
        self.assertEqual(
            channel_service.get_offer_channel_history_tag(make_offer(status=OfferStatus.COMPLETED)),
            "🤝 ✅",
        )
        self.assertEqual(
            channel_service.get_offer_channel_history_tag(
                make_offer(
                    status=OfferStatus.EXPIRED,
                    expire_reason="time_limit",
                    quantity=30,
                    remaining_quantity=7,
                )
            ),
            "🤝 23 تا ✅",
        )
        self.assertEqual(
            channel_service.get_offer_channel_history_tag(
                make_offer(status=OfferStatus.EXPIRED, expire_reason="time_limit", quantity=30, remaining_quantity=30)
            ),
            "❌",
        )
        self.assertEqual(
            channel_service.get_offer_channel_history_tag(
                make_offer(status=OfferStatus.EXPIRED, expire_reason="manual", quantity=30, remaining_quantity=30)
            ),
            "❌",
        )

    def test_channel_message_uses_same_text_for_active_and_terminal(self):
        offer = make_offer(notes="تحویل فوری")

        active_message = channel_service.build_offer_channel_message(offer)
        terminal_message = channel_service.build_offer_channel_message(offer, history_tag="🤝 ✅")

        self.assertIn("🟢خرید سکه 30 عدد نقد حاضر ☀️ 51,000", active_message)
        self.assertIn("توضیحات: تحویل فوری", active_message)
        self.assertIn("🤝 ✅", terminal_message)
        self.assertTrue(active_message.endswith(channel_service.INVISIBLE_CHANNEL_PADDING))

        tomorrow_message = channel_service.build_offer_channel_message(
            make_offer(settlement_type=SettlementType.TOMORROW)
        )
        self.assertIn("🟢خرید سکه 30 عدد فردا 📆 51,000", tomorrow_message)

    async def test_apply_terminal_completed_edits_text_and_removes_buttons_in_one_request(self):
        response = SimpleNamespace(status_code=200, text="")
        client = FakeHttpClientContext(response=response)
        offer = make_offer(status=OfferStatus.COMPLETED)

        with patch("core.services.telegram_offer_channel_service.current_server", return_value="foreign"), \
             patch.object(channel_service.settings, "bot_token", "bot-token"), \
             patch.object(channel_service.settings, "channel_id", -100), \
             patch("core.telegram_gateway.httpx.AsyncClient", return_value=client):
            result = await channel_service.apply_offer_channel_state(offer, reason="test")

        self.assertTrue(result)
        self.assertEqual(client.post.await_count, 1)
        call = client.post.await_args
        self.assertTrue(call.args[0].endswith("/editMessageText"))
        payload = call.kwargs["json"]
        self.assertIn("🤝 ✅", payload["text"])
        self.assertEqual(payload["reply_markup"], {"inline_keyboard": []})

    async def test_apply_pure_expired_edits_text_and_keeps_buttons(self):
        response = SimpleNamespace(status_code=200, text="")
        client = FakeHttpClientContext(response=response)
        offer = make_offer(
            status=OfferStatus.EXPIRED,
            expire_reason="time_limit",
            quantity=30,
            remaining_quantity=30,
        )

        with patch("core.services.telegram_offer_channel_service.current_server", return_value="foreign"), \
             patch(
                 "core.services.telegram_offer_channel_service.publication_send_backlog_due_count",
                 new=AsyncMock(return_value=0),
             ), \
             patch.object(channel_service.settings, "bot_token", "bot-token"), \
             patch.object(channel_service.settings, "channel_id", -100), \
             patch("core.telegram_gateway.httpx.AsyncClient", return_value=client):
            result = await channel_service.apply_offer_channel_state(offer, reason="test")

        self.assertTrue(result)
        self.assertEqual(client.post.await_count, 1)
        call = client.post.await_args
        self.assertTrue(call.args[0].endswith("/editMessageText"))
        payload = call.kwargs["json"]
        self.assertEqual(payload["chat_id"], -100)
        self.assertEqual(payload["message_id"], 123)
        self.assertIn("❌", payload["text"])
        self.assertNotIn("reply_markup", payload)

    async def test_non_trade_edit_defers_when_send_backlog_due(self):
        client = FakeHttpClientContext(response=SimpleNamespace(status_code=200, text=""))
        offer = make_offer(
            status=OfferStatus.EXPIRED,
            expire_reason="time_limit",
            quantity=30,
            remaining_quantity=30,
        )

        with patch("core.services.telegram_offer_channel_service.current_server", return_value="foreign"), \
             patch(
                 "core.services.telegram_offer_channel_service.publication_send_backlog_due_count",
                 new=AsyncMock(return_value=12),
             ), \
             patch.object(channel_service.settings, "bot_token", "bot-token"), \
             patch.object(channel_service.settings, "channel_id", -100), \
             patch("core.telegram_gateway.httpx.AsyncClient", return_value=client) as client_ctor:
            result = await channel_service.apply_offer_channel_state_with_result(
                offer,
                reason="auto_expire_time_limit",
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.response_class, "deferred")
        self.assertEqual(result.reason, "send_backlog_defers_non_trade_edit")
        client_ctor.assert_not_called()

    async def test_trade_edit_not_deferred_when_send_backlog_due(self):
        response = SimpleNamespace(status_code=200, text="")
        client = FakeHttpClientContext(response=response)
        offer = make_offer(status=OfferStatus.COMPLETED)

        with patch("core.services.telegram_offer_channel_service.current_server", return_value="foreign"), \
             patch(
                 "core.services.telegram_offer_channel_service.publication_send_backlog_due_count",
                 new=AsyncMock(return_value=12),
             ), \
             patch.object(channel_service.settings, "bot_token", "bot-token"), \
             patch.object(channel_service.settings, "channel_id", -100), \
             patch("core.telegram_gateway.httpx.AsyncClient", return_value=client):
            result = await channel_service.apply_offer_channel_state(offer, reason="trade")

        self.assertTrue(result)
        self.assertEqual(client.post.await_count, 1)

    async def test_apply_partially_traded_expired_edits_text_and_removes_buttons(self):
        response = SimpleNamespace(status_code=200, text="")
        client = FakeHttpClientContext(response=response)
        offer = make_offer(
            status=OfferStatus.EXPIRED,
            expire_reason="time_limit",
            quantity=40,
            remaining_quantity=20,
        )

        with patch("core.services.telegram_offer_channel_service.current_server", return_value="foreign"), \
             patch(
                 "core.services.telegram_offer_channel_service.publication_send_backlog_due_count",
                 new=AsyncMock(return_value=0),
             ), \
             patch.object(channel_service.settings, "bot_token", "bot-token"), \
             patch.object(channel_service.settings, "channel_id", -100), \
             patch("core.telegram_gateway.httpx.AsyncClient", return_value=client):
            result = await channel_service.apply_offer_channel_state(offer, reason="test")

        self.assertTrue(result)
        self.assertEqual(client.post.await_count, 1)
        call = client.post.await_args
        self.assertTrue(call.args[0].endswith("/editMessageText"))
        payload = call.kwargs["json"]
        self.assertIn("🤝 20 تا ✅", payload["text"])
        self.assertNotIn("🤝 20تا ✅.", payload["text"])
        self.assertEqual(payload["reply_markup"], {"inline_keyboard": []})

    async def test_apply_terminal_state_can_use_publication_state_message_id(self):
        response = SimpleNamespace(status_code=200, text="")
        client = FakeHttpClientContext(response=response)
        offer = make_offer(status=OfferStatus.COMPLETED, channel_message_id=None)
        publication_state = SimpleNamespace(telegram_message_id=901)

        with patch("core.services.telegram_offer_channel_service.current_server", return_value="foreign"), \
             patch.object(channel_service.settings, "bot_token", "bot-token"), \
             patch.object(channel_service.settings, "channel_id", -100), \
             patch("core.telegram_gateway.httpx.AsyncClient", return_value=client):
            result = await channel_service.apply_offer_channel_state(
                offer,
                publication_state=publication_state,
                reason="test",
            )

        self.assertTrue(result)
        payload = client.post.await_args.kwargs["json"]
        self.assertEqual(payload["message_id"], 901)
        self.assertEqual(offer.channel_message_id, 901)

    async def test_apply_state_is_foreign_only(self):
        client = FakeHttpClientContext(response=SimpleNamespace(status_code=200, text=""))

        with patch("core.services.telegram_offer_channel_service.current_server", return_value="iran"), \
             patch.object(channel_service.settings, "bot_token", "bot-token"), \
             patch.object(channel_service.settings, "channel_id", -100), \
             patch("core.telegram_gateway.httpx.AsyncClient", return_value=client) as client_ctor:
            result = await channel_service.apply_offer_channel_state(make_offer(), reason="test")

        self.assertFalse(result)
        client_ctor.assert_not_called()

    async def test_queue_owner_never_calls_legacy_channel_gateway(self):
        client = FakeHttpClientContext(response=SimpleNamespace(status_code=200, text=""))
        with patch(
            "core.services.telegram_offer_channel_service.current_server",
            return_value="foreign",
        ), patch(
            "core.services.telegram_offer_channel_service.configured_telegram_delivery_producer_mode",
            return_value=TelegramDeliveryRuntimeMode.QUEUE_V1,
        ), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=client,
        ) as client_ctor:
            result = await channel_service.apply_offer_channel_state_with_result(
                make_offer(),
                reason="queue-cutover",
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.response_class, "queued")
        self.assertEqual(result.reason, "telegram_delivery_queue_owned")
        client_ctor.assert_not_called()

    async def test_message_not_modified_is_idempotent_success(self):
        response = SimpleNamespace(status_code=400, text="Bad Request: message is not modified")
        client = FakeHttpClientContext(response=response)

        with patch("core.services.telegram_offer_channel_service.current_server", return_value="foreign"), \
             patch.object(channel_service.settings, "bot_token", "bot-token"), \
             patch.object(channel_service.settings, "channel_id", -100), \
             patch("core.telegram_gateway.httpx.AsyncClient", return_value=client):
            result = await channel_service.apply_offer_channel_state(make_offer(), reason="replay")

        self.assertTrue(result)

    async def test_apply_terminal_state_returns_combined_edit_failure_classification(self):
        response = SimpleNamespace(
            status_code=429,
            text="Too Many Requests",
            json=lambda: {"ok": False, "parameters": {"retry_after": 7}},
        )
        client = FakeHttpClientContext(response=response)

        with patch("core.services.telegram_offer_channel_service.current_server", return_value="foreign"), \
             patch.object(channel_service.settings, "bot_token", "bot-token"), \
             patch.object(channel_service.settings, "channel_id", -100), \
             patch("core.telegram_gateway.httpx.AsyncClient", return_value=client):
            result = await channel_service.apply_offer_channel_state_with_result(make_offer(), reason="retry-after")

        self.assertFalse(result.ok)
        self.assertEqual(result.response_class, "429")
        self.assertEqual(result.retry_after_seconds, 7)
        self.assertEqual(result.method, "editMessageText")
        self.assertEqual(client.post.await_count, 1)

    async def test_apply_terminal_state_429_is_one_atomic_retryable_operation(self):
        response = SimpleNamespace(
            status_code=429,
            text="Too Many Requests",
            json=lambda: {"ok": False, "parameters": {"retry_after": 9}},
        )
        client = FakeHttpClientContext(response=response)

        with patch("core.services.telegram_offer_channel_service.current_server", return_value="foreign"), \
             patch.object(channel_service.settings, "bot_token", "bot-token"), \
             patch.object(channel_service.settings, "channel_id", -100), \
             patch("core.telegram_gateway.httpx.AsyncClient", return_value=client):
            result = await channel_service.apply_offer_channel_state_with_result(make_offer(), reason="rate-limited")

        self.assertFalse(result.ok)
        self.assertEqual(result.response_class, "429")
        self.assertEqual(result.retry_after_seconds, 9)
        self.assertEqual(result.method, "editMessageText")
        self.assertEqual(client.post.await_count, 1)

    def test_overtime_marker_appended_as_trailing_line(self):
        created = datetime(2026, 8, 5, 12, 0, 0)
        offer = make_offer(
            status=OfferStatus.ACTIVE,
            remaining_quantity=30,
            overtime_minutes_snapshot=5,
            created_at=created,
            notes=None,
        )
        message = channel_service.build_offer_channel_message(
            offer,
            lifecycle_phase="overtime",
        )
        self.assertIn(f"\n{channel_service.TELEGRAM_OFFER_OVERTIME_MARKER}\n", message)
        self.assertTrue(
            message.index("نقد حاضر")
            < message.index(channel_service.TELEGRAM_OFFER_OVERTIME_MARKER)
        )

    def test_final_tail_keeps_marker_and_strips_trade_buttons(self):
        offer = make_offer(
            status=OfferStatus.ACTIVE,
            remaining_quantity=30,
            overtime_minutes_snapshot=5,
        )
        message = channel_service.build_offer_channel_message(
            offer,
            lifecycle_phase="final_tail",
        )
        self.assertIn(channel_service.TELEGRAM_OFFER_OVERTIME_MARKER, message)
        self.assertIsNone(
            channel_service.build_offer_channel_reply_markup(
                offer,
                accepts_new_public_interaction=False,
            )
        )

    def test_terminal_marker_retained_only_when_overtime_trade_committed(self):
        traded = make_offer(
            status=OfferStatus.COMPLETED,
            overtime_trade_committed=True,
        )
        plain = make_offer(
            status=OfferStatus.EXPIRED,
            expire_reason="time_limit",
            remaining_quantity=30,
            overtime_trade_committed=False,
        )
        traded_message = channel_service.build_offer_channel_message(
            traded,
            history_tag="🤝 ✅",
        )
        plain_message = channel_service.build_offer_channel_message(
            plain,
            history_tag="❌",
        )
        self.assertIn(channel_service.TELEGRAM_OFFER_OVERTIME_MARKER, traded_message)
        self.assertIn("🤝 ✅", traded_message)
        self.assertTrue(
            traded_message.index(channel_service.TELEGRAM_OFFER_OVERTIME_MARKER)
            < traded_message.index("🤝 ✅")
        )
        self.assertNotIn(channel_service.TELEGRAM_OFFER_OVERTIME_MARKER, plain_message)
        self.assertIn("❌", plain_message)

    def test_marker_coexists_with_partial_trade_history_tag(self):
        offer = make_offer(
            status=OfferStatus.EXPIRED,
            expire_reason="time_limit",
            quantity=40,
            remaining_quantity=20,
            overtime_trade_committed=True,
        )
        message = channel_service.build_offer_channel_message(
            offer,
            history_tag=channel_service.get_offer_channel_history_tag(offer),
        )
        self.assertIn(channel_service.TELEGRAM_OFFER_OVERTIME_MARKER, message)
        self.assertIn("🤝 20 تا ✅", message)

    async def test_apply_active_overtime_uses_edit_message_text_with_marker(self):
        response = SimpleNamespace(status_code=200, text="")
        client = FakeHttpClientContext(response=response)
        created = datetime.now().replace(tzinfo=None) - timedelta(minutes=3)
        offer = make_offer(
            status=OfferStatus.ACTIVE,
            remaining_quantity=30,
            overtime_minutes_snapshot=5,
            created_at=created,
            notes=None,
        )

        with patch(
            "core.services.telegram_offer_channel_service.current_server",
            return_value="foreign",
        ), patch(
            "core.services.telegram_offer_channel_service.publication_send_backlog_due_count",
            new=AsyncMock(return_value=0),
        ), patch.object(
            channel_service.settings, "bot_token", "bot-token"
        ), patch.object(
            channel_service.settings, "channel_id", -100
        ), patch(
            "core.trading_settings.get_trading_settings",
            return_value=SimpleNamespace(offer_expiry_minutes=2),
        ), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=client,
        ):
            result = await channel_service.apply_offer_channel_state(offer, reason="ot")

        self.assertTrue(result)
        call = client.post.await_args
        self.assertTrue(call.args[0].endswith("/editMessageText"))
        payload = call.kwargs["json"]
        self.assertIn(channel_service.TELEGRAM_OFFER_OVERTIME_MARKER, payload["text"])
        self.assertIn("inline_keyboard", payload["reply_markup"])


if __name__ == "__main__":
    unittest.main()
