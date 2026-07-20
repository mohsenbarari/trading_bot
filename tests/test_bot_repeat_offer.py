import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from bot.repeat_offer import (
    BOT_REPEAT_OFFER_BUTTON_MAX_LENGTH,
    BotRepeatOfferCandidate,
    bot_repeat_offer_candidate,
    decorate_navigation_keyboard,
    is_bot_repeat_offer_button_text,
    load_latest_bot_repeat_offer_candidate,
    prepend_repeat_offer_button,
    refresh_repeat_offer_menu_for_expired_offer,
    resolve_bot_repeat_offer_button_candidate,
)
from core.enums import SettlementType, UserRole
from models.offer import Offer, OfferStatus, OfferType
from models.user import User


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeLookupSession:
    def __init__(self, offer, user):
        self.offer = offer
        self.user = user
        self.commit_count = 0

    async def get(self, model, record_id):
        if model is Offer and int(record_id) == int(self.offer.id):
            return self.offer
        if model is User and int(record_id) == int(self.user.id):
            return self.user
        return None

    async def commit(self):
        self.commit_count += 1


def make_offer(**overrides):
    values = {
        "id": 17,
        "offer_public_id": "ofr_repeat_source_17",
        "offer_type": OfferType.SELL,
        "settlement_type": SettlementType.TOMORROW,
        "commodity": SimpleNamespace(name="ربع بهار"),
        "quantity": 40,
        "remaining_quantity": 25,
        "price": 178000,
        "is_wholesale": False,
        "lot_sizes": [15, 10],
        "notes": "تحویل حضوری",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class BotRepeatOfferTests(unittest.IsolatedAsyncioTestCase):
    def test_candidate_uses_remaining_snapshot_and_compact_button(self):
        candidate = bot_repeat_offer_candidate(make_offer())

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.source_offer_id, 17)
        self.assertIn("25 عدد", candidate.draft_text)
        self.assertIn("15 10", candidate.draft_text)
        self.assertIn("تحویل حضوری", candidate.draft_text)
        self.assertIn("تحویل حضوری", candidate.button_text)
        self.assertNotIn("#17", candidate.button_text)
        self.assertTrue(is_bot_repeat_offer_button_text(candidate.button_text))
        self.assertLessEqual(len(candidate.button_text), BOT_REPEAT_OFFER_BUTTON_MAX_LENGTH)

    def test_candidate_rejects_invalid_remaining_lots(self):
        self.assertIsNone(bot_repeat_offer_candidate(make_offer(lot_sizes=[15, 5])))
        self.assertIsNone(bot_repeat_offer_candidate(make_offer(lot_sizes=["invalid"])))

    def test_long_button_text_is_bounded(self):
        candidate = bot_repeat_offer_candidate(
            make_offer(commodity=SimpleNamespace(name="کالای " * 30))
        )

        self.assertIsNotNone(candidate)
        self.assertLessEqual(len(candidate.button_text), BOT_REPEAT_OFFER_BUTTON_MAX_LENGTH)
        self.assertTrue(candidate.button_text.endswith("..."))

    async def test_loader_requests_latest_foreign_home_candidate(self):
        offer = make_offer()
        with patch(
            "bot.repeat_offer.list_repeatable_offers",
            new=AsyncMock(return_value=[offer]),
        ) as list_mock:
            candidate = await load_latest_bot_repeat_offer_candidate(
                SimpleNamespace(),
                owner_user_id=9,
            )

        self.assertEqual(candidate.source_offer_public_id, offer.offer_public_id)
        self.assertEqual(list_mock.await_args.kwargs["limit"], 1)
        self.assertEqual(list_mock.await_args.kwargs["since_hours"], 1)
        self.assertEqual(list_mock.await_args.kwargs["replacement_home_server"], "foreign")

    async def test_resolver_rejects_stale_button_instead_of_preparing_latest(self):
        latest_offer = make_offer(
            id=19,
            offer_public_id="ofr_repeat_source_19",
            price=180000,
            notes=None,
        )
        older_offer = make_offer()
        older_candidate = bot_repeat_offer_candidate(older_offer)
        latest_candidate = bot_repeat_offer_candidate(latest_offer)

        with patch(
            "bot.repeat_offer.load_latest_bot_repeat_offer_candidate",
            new=AsyncMock(return_value=latest_candidate),
        ):
            resolved, needs_refresh, reason = await resolve_bot_repeat_offer_button_candidate(
                SimpleNamespace(),
                owner_user_id=9,
                button_text=older_candidate.button_text,
            )

        self.assertIsNone(resolved)
        self.assertTrue(needs_refresh)
        self.assertEqual(reason, "stale_button")

    async def test_resolver_keeps_current_full_button_without_refresh(self):
        offer = make_offer()
        candidate = bot_repeat_offer_candidate(offer)
        with patch(
            "bot.repeat_offer.load_latest_bot_repeat_offer_candidate",
            new=AsyncMock(return_value=candidate),
        ):
            resolved, needs_refresh, reason = await resolve_bot_repeat_offer_button_candidate(
                SimpleNamespace(),
                owner_user_id=9,
                button_text=candidate.button_text,
            )

        self.assertEqual(resolved.source_offer_public_id, offer.offer_public_id)
        self.assertFalse(needs_refresh)
        self.assertIsNone(reason)

    async def test_expiry_event_pushes_current_keyboard_and_debounces_batch(self):
        offer = make_offer(
            status=OfferStatus.EXPIRED,
            expire_reason="time_limit",
            home_server="foreign",
            expire_source_surface="system",
            user_id=9,
        )
        user = SimpleNamespace(id=9, telegram_id=99, role=UserRole.STANDARD)
        candidate = bot_repeat_offer_candidate(offer)
        bot = SimpleNamespace(send_message=AsyncMock())
        session = FakeLookupSession(offer, user)

        from bot import repeat_offer as repeat_offer_module

        repeat_offer_module._repeat_offer_refresh_sent_at.clear()
        with patch(
            "bot.repeat_offer.AsyncSessionLocal",
            return_value=FakeSessionContext(session),
        ), patch(
            "bot.repeat_offer.evaluate_bot_access",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True)),
        ), patch(
            "bot.repeat_offer.load_latest_bot_repeat_offer_candidate",
            new=AsyncMock(return_value=candidate),
        ), patch(
            "bot.repeat_offer.time.monotonic",
            side_effect=[100.0, 100.5],
        ):
            first = await refresh_repeat_offer_menu_for_expired_offer(bot, offer.id)
            second = await refresh_repeat_offer_menu_for_expired_offer(bot, offer.id)

        self.assertTrue(first)
        self.assertFalse(second)
        bot.send_message.assert_awaited_once()
        self.assertEqual(
            bot.send_message.await_args.kwargs["text"],
            "منو با آخرین وضعیت به‌روزرسانی شد",
        )
        keyboard = bot.send_message.await_args.kwargs["reply_markup"]
        self.assertEqual(keyboard.keyboard[0][0].text, candidate.button_text)
        self.assertNotIn("#17", keyboard.keyboard[0][0].text)

    async def test_expiry_event_skips_foreign_manual_bot_expiry(self):
        offer = make_offer(
            status=OfferStatus.EXPIRED,
            expire_reason="manual",
            home_server="foreign",
            expire_source_surface="telegram_bot",
            user_id=9,
        )
        user = SimpleNamespace(id=9, telegram_id=99, role=UserRole.STANDARD)
        bot = SimpleNamespace(send_message=AsyncMock())

        with patch(
            "bot.repeat_offer.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeLookupSession(offer, user)),
        ):
            sent = await refresh_repeat_offer_menu_for_expired_offer(bot, offer.id)

        self.assertFalse(sent)
        bot.send_message.assert_not_awaited()

    async def test_expiry_event_queue_owner_persists_refresh_without_telegram(self):
        offer = make_offer(
            status=OfferStatus.EXPIRED,
            expire_reason="time_limit",
            home_server="foreign",
            expire_source_surface="system",
            user_id=9,
        )
        user = SimpleNamespace(id=9, telegram_id=99, role=UserRole.STANDARD)
        bot = SimpleNamespace(send_message=AsyncMock())
        session = FakeLookupSession(offer, user)
        queue_runtime = SimpleNamespace(mode="queue-v1")
        enqueue_result = SimpleNamespace(created=True)

        from bot import repeat_offer as repeat_offer_module

        repeat_offer_module._repeat_offer_refresh_sent_at.clear()

        with patch(
            "bot.repeat_offer.AsyncSessionLocal",
            return_value=FakeSessionContext(session),
        ), patch(
            "bot.repeat_offer.evaluate_bot_access",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True)),
        ), patch(
            "bot.repeat_offer.configured_telegram_delivery_runtime",
            return_value=queue_runtime,
        ), patch(
            "bot.repeat_offer.enqueue_offer_repeat_response_notification",
            new=AsyncMock(return_value=enqueue_result),
        ) as enqueue, patch(
            "bot.repeat_offer.time.monotonic",
            side_effect=[100.0, 100.5],
        ):
            first = await refresh_repeat_offer_menu_for_expired_offer(
                bot,
                offer.id,
            )
            second = await refresh_repeat_offer_menu_for_expired_offer(
                bot,
                offer.id,
            )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(session.commit_count, 1)
        bot.send_message.assert_not_awaited()
        enqueue.assert_awaited_once()
        self.assertEqual(
            enqueue.await_args.kwargs["source_id"],
            f"expiry:{offer.offer_public_id}",
        )

    async def test_decorator_prepends_row_and_fails_open(self):
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📈 معامله")]],
            resize_keyboard=True,
            is_persistent=True,
        )
        candidate = BotRepeatOfferCandidate(1, "ofr_1", "خ ن سکه 10 عدد 100000", "🔁 خ ن سکه 10 عدد 100000")
        user = SimpleNamespace(id=4, role=UserRole.STANDARD)

        with patch("bot.repeat_offer.AsyncSessionLocal", return_value=FakeSessionContext(SimpleNamespace())), patch(
            "bot.repeat_offer.evaluate_bot_access",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True)),
        ), patch(
            "bot.repeat_offer.load_latest_bot_repeat_offer_candidate",
            new=AsyncMock(return_value=candidate),
        ):
            decorated = await decorate_navigation_keyboard(keyboard, user)

        self.assertEqual(decorated.keyboard[0][0].text, candidate.button_text)
        self.assertEqual(decorated.keyboard[1][0].text, "📈 معامله")

        with patch("bot.repeat_offer.AsyncSessionLocal", side_effect=RuntimeError("db down")):
            fallback = await decorate_navigation_keyboard(keyboard, user)
        self.assertIs(fallback, keyboard)

    def test_prepend_without_candidate_preserves_keyboard(self):
        keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="منو")]])
        self.assertIs(prepend_repeat_offer_button(keyboard, None), keyboard)


if __name__ == "__main__":
    unittest.main()
