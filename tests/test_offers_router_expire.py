import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.offers import cancel_all_active_offers, expire_offer
from models.offer import OfferStatus


class FakeScalarRows:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)


class FakeExecuteResult:
    def __init__(self, values):
        self._values = list(values)

    def scalars(self):
        return FakeScalarRows(self._values)


def make_context(owner_user=None, actor_user=None):
    owner = owner_user or SimpleNamespace(id=5)
    actor = actor_user or owner
    return SimpleNamespace(owner_user=owner, actor_user=actor, relation=None, is_accountant_context=getattr(owner, "id", None) != getattr(actor, "id", None))


class FakeDB:
    def __init__(self, *, scalar_result=None, scalar_results=None, get_result=None, execute_results=None):
        self.scalar_result = scalar_result
        self.scalar_results = list(scalar_results or [])
        self.get_result = get_result
        self.execute_results = list(execute_results or [])
        self.commit = AsyncMock()

    async def scalar(self, _stmt):
        if self.scalar_results:
            return self.scalar_results.pop(0)
        return self.scalar_result

    async def get(self, _model, _id, *args, **kwargs):
        return self.get_result

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


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
                await expire_offer(offer_id=1, db=db, context=make_context(current_user))
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
                await expire_offer(offer_id=1, db=db, context=make_context(current_user))
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
                await expire_offer(offer_id=1, db=FakeDB(scalar_result=0, get_result=None), context=make_context(current_user))
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
                await expire_offer(offer_id=1, db=FakeDB(scalar_result=0, get_result=foreign_offer), context=make_context(current_user))
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
                await expire_offer(offer_id=1, db=FakeDB(scalar_result=0, get_result=inactive_offer), context=make_context(current_user))
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "این لفظ قبلاً غیرفعال شده است.")

    async def test_expire_offer_updates_status_and_publishes_side_effects(self):
        settings = SimpleNamespace(
            offer_expire_rate_per_minute=5,
            offer_expire_daily_limit_after_threshold=10,
        )
        offer = SimpleNamespace(user_id=5, status=OfferStatus.ACTIVE, channel_message_id=None)
        db = FakeDB(scalar_results=[1, 0], get_result=offer)
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
            "core.cache.set_active_offer_count",
            new=AsyncMock(),
        ) as set_count_mock:
            result = await expire_offer(offer_id=7, db=db, context=make_context(current_user))

        self.assertIsNone(result)
        self.assertEqual(offer.status, OfferStatus.EXPIRED)
        self.assertIsNotNone(offer.expired_at)
        self.assertEqual(offer.expire_reason, "manual")
        db.commit.assert_awaited_once()
        publish_mock.assert_awaited_once_with("offer:expired", {"id": 7})
        set_count_mock.assert_awaited_once_with(5, 0)

    async def test_expire_offer_applies_channel_state_when_message_exists(self):
        settings = SimpleNamespace(
            offer_expire_rate_per_minute=5,
            offer_expire_daily_limit_after_threshold=10,
        )
        offer = SimpleNamespace(user_id=5, status=OfferStatus.ACTIVE, channel_message_id=333)
        db = FakeDB(scalar_results=[1, 0], get_result=offer)
        current_user = SimpleNamespace(id=5)
        with patch("api.routers.offers.get_trading_settings", return_value=settings), patch(
            "bot.utils.redis_helpers.track_expire_rate",
            new=AsyncMock(return_value=1),
        ), patch(
            "bot.utils.redis_helpers.track_daily_expire",
            new=AsyncMock(return_value={"count": 0}),
        ), patch("api.routers.offers.current_server", return_value="foreign"), patch(
            "api.routers.offers.apply_offer_channel_state",
            new=AsyncMock(),
        ) as apply_offer_channel_state, patch("api.routers.realtime.publish_event", new=AsyncMock()), patch(
            "core.cache.set_active_offer_count",
            new=AsyncMock(),
        ):
            await expire_offer(offer_id=9, db=db, context=make_context(current_user))

        apply_offer_channel_state.assert_awaited_once_with(offer, reason="manual_expire", timeout=10)

    async def test_expire_offer_logs_channel_state_failures(self):
        settings = SimpleNamespace(
            offer_expire_rate_per_minute=5,
            offer_expire_daily_limit_after_threshold=10,
        )
        offer = SimpleNamespace(user_id=5, status=OfferStatus.ACTIVE, channel_message_id=444)
        db = FakeDB(scalar_results=[1, 0], get_result=offer)
        current_user = SimpleNamespace(id=5)
        with patch("api.routers.offers.get_trading_settings", return_value=settings), patch(
            "bot.utils.redis_helpers.track_expire_rate",
            new=AsyncMock(return_value=1),
        ), patch(
            "bot.utils.redis_helpers.track_daily_expire",
            new=AsyncMock(return_value={"count": 0}),
        ), patch("api.routers.offers.current_server", return_value="foreign"), patch(
            "api.routers.offers.apply_offer_channel_state",
            new=AsyncMock(side_effect=RuntimeError("telegram down")),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()), patch(
            "core.cache.set_active_offer_count",
            new=AsyncMock(),
        ), patch("api.routers.offers.logger") as logger:
            await expire_offer(offer_id=10, db=db, context=make_context(current_user))

        logger.warning.assert_called_once()

    async def test_expire_offer_rejects_accountant_market_context(self):
        settings = SimpleNamespace(
            offer_expire_rate_per_minute=5,
            offer_expire_daily_limit_after_threshold=10,
        )
        offer = SimpleNamespace(user_id=5, status=OfferStatus.ACTIVE, channel_message_id=None)
        db = FakeDB(scalar_result=6, get_result=offer)
        owner_user = SimpleNamespace(id=5)
        actor_user = SimpleNamespace(id=44)

        with patch("api.routers.offers.get_trading_settings", return_value=settings), patch(
            "bot.utils.redis_helpers.track_expire_rate",
            new=AsyncMock(return_value=1),
        ) as rate_mock, patch(
            "bot.utils.redis_helpers.track_daily_expire",
            new=AsyncMock(return_value={"count": 0}),
        ) as daily_mock, patch("api.routers.realtime.publish_event", new=AsyncMock()), patch(
            "core.cache.set_active_offer_count",
            new=AsyncMock(),
        ) as set_count_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await expire_offer(offer_id=11, db=db, context=make_context(owner_user, actor_user))

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "حسابدار دسترسی به بازار ندارد.")
        rate_mock.assert_not_awaited()
        daily_mock.assert_not_awaited()
        set_count_mock.assert_not_awaited()

    async def test_cancel_all_active_offers_returns_zero_when_no_offer_exists(self):
        db = FakeDB(execute_results=[FakeExecuteResult([])])

        result = await cancel_all_active_offers(db=db, context=make_context(SimpleNamespace(id=5)))

        self.assertEqual(result, {"cancelled_count": 0})
        db.commit.assert_not_awaited()

    async def test_cancel_all_active_offers_expires_offers_and_logs_channel_state_failures(self):
        offers = [
            SimpleNamespace(id=1, status=OfferStatus.ACTIVE, channel_message_id=333, user_id=5),
            SimpleNamespace(id=2, status=OfferStatus.ACTIVE, channel_message_id=None, user_id=5),
        ]
        db = FakeDB(execute_results=[FakeExecuteResult(offers)])
        with patch("api.routers.offers.current_server", return_value="foreign"), patch(
            "api.routers.offers.apply_offer_channel_state",
            new=AsyncMock(side_effect=RuntimeError("telegram down")),
        ) as apply_offer_channel_state, patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "core.cache.set_active_offer_count",
            new=AsyncMock(),
        ) as set_count_mock, patch("api.routers.offers.logger") as logger:
            result = await cancel_all_active_offers(db=db, context=make_context(SimpleNamespace(id=5)))

        self.assertEqual(result, {"cancelled_count": 2})
        self.assertEqual([offer.status for offer in offers], [OfferStatus.EXPIRED, OfferStatus.EXPIRED])
        self.assertEqual(publish_mock.await_count, 2)
        set_count_mock.assert_awaited_once_with(5, 0)
        apply_offer_channel_state.assert_awaited_once_with(offers[0], reason="cancel_all_active_offers", timeout=5)
        db.commit.assert_awaited_once()
        logger.warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
