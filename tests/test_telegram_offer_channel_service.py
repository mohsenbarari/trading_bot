import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services import telegram_offer_channel_service as channel_service
from models.offer import OfferStatus, OfferType


class FakeHttpClientContext:
    def __init__(self, *, response=None, error=None):
        self.response = response
        self.error = error
        self.post = AsyncMock(side_effect=self._post)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def _post(self, *_args, **_kwargs):
        if self.error is not None:
            raise self.error
        return self.response


def make_offer(**overrides):
    data = {
        "id": 10,
        "offer_type": OfferType.BUY,
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

        self.assertIn("🟢خرید سکه 30 عدد 51,000", active_message)
        self.assertIn("توضیحات: تحویل فوری", active_message)
        self.assertIn("🤝 ✅", terminal_message)
        self.assertTrue(active_message.endswith(channel_service.INVISIBLE_CHANNEL_PADDING))

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
        url = client.post.await_args.args[0]
        payload = client.post.await_args.kwargs["json"]
        self.assertTrue(url.endswith("/editMessageText"))
        self.assertEqual(payload["reply_markup"], None)
        self.assertIn("🤝 ✅", payload["text"])

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
        url = client.post.await_args.args[0]
        payload = client.post.await_args.kwargs["json"]
        self.assertTrue(url.endswith("/editMessageText"))
        self.assertEqual(payload["chat_id"], -100)
        self.assertEqual(payload["message_id"], 123)
        self.assertEqual(payload["reply_markup"], None)
        self.assertIn("❌", payload["text"])

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
        url = client.post.await_args.args[0]
        payload = client.post.await_args.kwargs["json"]
        self.assertTrue(url.endswith("/editMessageText"))
        self.assertEqual(payload["reply_markup"], None)
        self.assertIn("🤝 20 تا ✅", payload["text"])
        self.assertNotIn("🤝 20تا ✅.", payload["text"])

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


if __name__ == "__main__":
    unittest.main()
