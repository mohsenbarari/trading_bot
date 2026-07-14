import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services import telegram_offer_channel_service as channel_service
from core.enums import SettlementType
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

    async def test_apply_terminal_completed_edits_text_and_removes_buttons_on_foreign(self):
        response = SimpleNamespace(status_code=200, text="")
        client = FakeHttpClientContext(response=response)
        offer = make_offer(status=OfferStatus.COMPLETED)

        with patch("core.services.telegram_offer_channel_service.current_server", return_value="foreign"), \
             patch.object(channel_service.settings, "bot_token", "bot-token"), \
             patch.object(channel_service.settings, "channel_id", -100), \
             patch("core.telegram_gateway.httpx.AsyncClient", return_value=client):
            result = await channel_service.apply_offer_channel_state(offer, reason="test")

        self.assertTrue(result)
        text_call, markup_call = client.post.await_args_list
        text_url = text_call.args[0]
        text_payload = text_call.kwargs["json"]
        markup_url = markup_call.args[0]
        markup_payload = markup_call.kwargs["json"]
        self.assertTrue(text_url.endswith("/editMessageText"))
        self.assertNotIn("reply_markup", text_payload)
        self.assertIn("🤝 ✅", text_payload["text"])
        self.assertTrue(markup_url.endswith("/editMessageReplyMarkup"))
        self.assertEqual(markup_payload["chat_id"], -100)
        self.assertEqual(markup_payload["message_id"], 123)
        self.assertNotIn("reply_markup", markup_payload)

    async def test_apply_pure_expired_edits_text_and_removes_buttons(self):
        response = SimpleNamespace(status_code=200, text="")
        client = FakeHttpClientContext(response=response)
        offer = make_offer(
            status=OfferStatus.EXPIRED,
            expire_reason="time_limit",
            quantity=30,
            remaining_quantity=30,
        )

        with patch("core.services.telegram_offer_channel_service.current_server", return_value="foreign"), \
             patch.object(channel_service.settings, "bot_token", "bot-token"), \
             patch.object(channel_service.settings, "channel_id", -100), \
             patch("core.telegram_gateway.httpx.AsyncClient", return_value=client):
            result = await channel_service.apply_offer_channel_state(offer, reason="test")

        self.assertTrue(result)
        text_call, markup_call = client.post.await_args_list
        text_url = text_call.args[0]
        text_payload = text_call.kwargs["json"]
        markup_url = markup_call.args[0]
        markup_payload = markup_call.kwargs["json"]
        self.assertTrue(text_url.endswith("/editMessageText"))
        self.assertEqual(text_payload["chat_id"], -100)
        self.assertEqual(text_payload["message_id"], 123)
        self.assertNotIn("reply_markup", text_payload)
        self.assertIn("❌", text_payload["text"])
        self.assertTrue(markup_url.endswith("/editMessageReplyMarkup"))
        self.assertNotIn("reply_markup", markup_payload)

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
             patch.object(channel_service.settings, "bot_token", "bot-token"), \
             patch.object(channel_service.settings, "channel_id", -100), \
             patch("core.telegram_gateway.httpx.AsyncClient", return_value=client):
            result = await channel_service.apply_offer_channel_state(offer, reason="test")

        self.assertTrue(result)
        text_call, markup_call = client.post.await_args_list
        text_url = text_call.args[0]
        text_payload = text_call.kwargs["json"]
        markup_url = markup_call.args[0]
        markup_payload = markup_call.kwargs["json"]
        self.assertTrue(text_url.endswith("/editMessageText"))
        self.assertNotIn("reply_markup", text_payload)
        self.assertIn("🤝 20 تا ✅", text_payload["text"])
        self.assertNotIn("🤝 20تا ✅.", text_payload["text"])
        self.assertTrue(markup_url.endswith("/editMessageReplyMarkup"))
        self.assertNotIn("reply_markup", markup_payload)

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

    async def test_message_not_modified_is_idempotent_success(self):
        response = SimpleNamespace(status_code=400, text="Bad Request: message is not modified")
        client = FakeHttpClientContext(response=response)

        with patch("core.services.telegram_offer_channel_service.current_server", return_value="foreign"), \
             patch.object(channel_service.settings, "bot_token", "bot-token"), \
             patch.object(channel_service.settings, "channel_id", -100), \
             patch("core.telegram_gateway.httpx.AsyncClient", return_value=client):
            result = await channel_service.apply_offer_channel_state(make_offer(), reason="replay")

        self.assertTrue(result)

    async def test_apply_terminal_state_returns_markup_failure_classification(self):
        text_response = SimpleNamespace(status_code=200, text="")
        markup_response = SimpleNamespace(
            status_code=429,
            text="Too Many Requests",
            json=lambda: {"ok": False, "parameters": {"retry_after": 7}},
        )
        client = FakeHttpClientContext(responses=[text_response, markup_response])

        with patch("core.services.telegram_offer_channel_service.current_server", return_value="foreign"), \
             patch.object(channel_service.settings, "bot_token", "bot-token"), \
             patch.object(channel_service.settings, "channel_id", -100), \
             patch("core.telegram_gateway.httpx.AsyncClient", return_value=client):
            result = await channel_service.apply_offer_channel_state_with_result(make_offer(), reason="retry-after")

        self.assertFalse(result.ok)
        self.assertEqual(result.response_class, "429")
        self.assertEqual(result.retry_after_seconds, 7)
        self.assertEqual(result.method, "editMessageReplyMarkup")
        self.assertEqual(client.post.await_count, 2)

    async def test_apply_terminal_state_does_not_remove_buttons_after_text_rate_limit(self):
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


if __name__ == "__main__":
    unittest.main()
