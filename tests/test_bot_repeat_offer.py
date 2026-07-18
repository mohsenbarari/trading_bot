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
    resolve_bot_repeat_offer_button_candidate,
)
from core.enums import SettlementType, UserRole
from models.offer import OfferType


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


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
