import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.offers import expire_offer
from models.offer import OfferStatus


class FakeDB:
    def __init__(self, *, scalar_result=None, get_result=None):
        self.scalar_result = scalar_result
        self.get_result = get_result
        self.commit = AsyncMock()

    async def scalar(self, _stmt):
        return self.scalar_result

    async def get(self, _model, _id):
        return self.get_result


class FakeAsyncClient:
    def __init__(self):
        self.post = AsyncMock(return_value=SimpleNamespace(status_code=200, text="ok"))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class OffersRouterExpireTests(unittest.IsolatedAsyncioTestCase):
    async def test_expire_offer_rejects_rate_limited_or_daily_limit_requests(self):
        db = FakeDB()
        current_user = SimpleNamespace(id=5)
        settings = SimpleNamespace(
            offer_expire_rate_per_minute=2,
            offer_expire_daily_limit_after_threshold=3,
        )

        with patch("api.routers.offers.get_trading_settings", return_value=settings), patch(
            "bot.utils.redis_helpers.track_expire_rate",
            new=AsyncMock(return_value=3),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await expire_offer(offer_id=1, db=db, current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 429)
        self.assertEqual(exc_info.exception.detail, "حداکثر 2 منقضی در دقیقه مجاز است")

        db = FakeDB(scalar_result=9)
        with patch("api.routers.offers.get_trading_settings", return_value=settings), patch(
            "bot.utils.redis_helpers.track_expire_rate",
            new=AsyncMock(return_value=1),
        ), patch(
            "bot.utils.redis_helpers.track_daily_expire",
            new=AsyncMock(return_value={"count": 3}),
        ), patch("api.routers.offers.date", wraps=date):
            with self.assertRaises(HTTPException) as exc_info:
                await expire_offer(offer_id=1, db=db, current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertIn("امروز 3 لفظ منقضی کرده", exc_info.exception.detail)

    async def test_expire_offer_rejects_missing_foreign_or_inactive_offers(self):
        settings = SimpleNamespace(
            offer_expire_rate_per_minute=5,
            offer_expire_daily_limit_after_threshold=10,
        )
        current_user = SimpleNamespace(id=5)

        with patch("api.routers.offers.get_trading_settings", return_value=settings), patch(
            "bot.utils.redis_helpers.track_expire_rate",
            new=AsyncMock(return_value=1),
        ), patch(
            "bot.utils.redis_helpers.track_daily_expire",
            new=AsyncMock(return_value={"count": 0}),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await expire_offer(offer_id=1, db=FakeDB(scalar_result=0, get_result=None), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 404)

        foreign_offer = SimpleNamespace(user_id=8, status=OfferStatus.ACTIVE, channel_message_id=None)
        with patch("api.routers.offers.get_trading_settings", return_value=settings), patch(
            "bot.utils.redis_helpers.track_expire_rate",
            new=AsyncMock(return_value=1),
        ), patch(
            "bot.utils.redis_helpers.track_daily_expire",
            new=AsyncMock(return_value={"count": 0}),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await expire_offer(offer_id=1, db=FakeDB(scalar_result=0, get_result=foreign_offer), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "شما مالک این لفظ نیستید.")

        inactive_offer = SimpleNamespace(user_id=5, status=OfferStatus.EXPIRED, channel_message_id=None)
        with patch("api.routers.offers.get_trading_settings", return_value=settings), patch(
            "bot.utils.redis_helpers.track_expire_rate",
            new=AsyncMock(return_value=1),
        ), patch(
            "bot.utils.redis_helpers.track_daily_expire",
            new=AsyncMock(return_value={"count": 0}),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await expire_offer(offer_id=1, db=FakeDB(scalar_result=0, get_result=inactive_offer), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "این لفظ قبلاً غیرفعال شده است.")

    async def test_expire_offer_updates_status_and_publishes_side_effects(self):
        settings = SimpleNamespace(
            offer_expire_rate_per_minute=5,
            offer_expire_daily_limit_after_threshold=10,
        )
        offer = SimpleNamespace(user_id=5, status=OfferStatus.ACTIVE, channel_message_id=None)
        db = FakeDB(scalar_result=1, get_result=offer)
        current_user = SimpleNamespace(id=5)

        with patch("api.routers.offers.get_trading_settings", return_value=settings), patch(
            "bot.utils.redis_helpers.track_expire_rate",
            new=AsyncMock(return_value=1),
        ), patch(
            "bot.utils.redis_helpers.track_daily_expire",
            new=AsyncMock(return_value={"count": 0}),
        ), patch(
            "api.routers.realtime.publish_event",
            new=AsyncMock(),
        ) as publish_mock, patch(
            "core.cache.decr_active_offer_count",
            new=AsyncMock(),
        ) as decr_mock:
            result = await expire_offer(offer_id=7, db=db, current_user=current_user)

        self.assertIsNone(result)
        self.assertEqual(offer.status, OfferStatus.EXPIRED)
        db.commit.assert_awaited_once()
        publish_mock.assert_awaited_once_with("offer:expired", {"id": 7})
        decr_mock.assert_awaited_once_with(5)

    async def test_expire_offer_removes_channel_buttons_when_message_exists(self):
        settings = SimpleNamespace(
            offer_expire_rate_per_minute=5,
            offer_expire_daily_limit_after_threshold=10,
        )
        offer = SimpleNamespace(user_id=5, status=OfferStatus.ACTIVE, channel_message_id=333)
        db = FakeDB(scalar_result=1, get_result=offer)
        current_user = SimpleNamespace(id=5)
        fake_client = FakeAsyncClient()

        with patch("api.routers.offers.get_trading_settings", return_value=settings), patch(
            "bot.utils.redis_helpers.track_expire_rate",
            new=AsyncMock(return_value=1),
        ), patch(
            "bot.utils.redis_helpers.track_daily_expire",
            new=AsyncMock(return_value={"count": 0}),
        ), patch.object(
            __import__("api.routers.offers", fromlist=["settings"]).settings,
            "channel_id",
            "@channel",
        ), patch("api.routers.offers.os.getenv", return_value="bot-token"), patch(
            "api.routers.offers.httpx.AsyncClient",
            return_value=fake_client,
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()), patch(
            "core.cache.decr_active_offer_count",
            new=AsyncMock(),
        ):
            await expire_offer(offer_id=9, db=db, current_user=current_user)

        fake_client.post.assert_awaited_once_with(
            "https://api.telegram.org/botbot-token/editMessageReplyMarkup",
            json={"chat_id": "@channel", "message_id": 333},
            timeout=10,
        )


if __name__ == "__main__":
    unittest.main()