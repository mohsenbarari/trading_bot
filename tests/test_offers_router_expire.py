import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.offers import InternalOfferExpireRequest, cancel_all_active_offers, expire_offer, expire_offer_internal
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
        current_user = SimpleNamespace(id=5)
        offer = SimpleNamespace(id=1, user_id=5, status=OfferStatus.ACTIVE, home_server="iran", channel_message_id=None)
        db = FakeDB(get_result=offer)
        settings = SimpleNamespace(
            offer_expire_rate_per_minute=2,
            offer_expire_daily_limit_after_threshold=3,
        )

        with patch("api.routers.offers.get_trading_settings", return_value=settings), patch(
            "bot.utils.redis_helpers.track_expire_rate",
            new=AsyncMock(return_value=3),
        ), patch("api.routers.offers.is_remote_home", return_value=False):
            with self.assertRaises(HTTPException) as exc_info:
                await expire_offer(offer_id=1, db=db, context=make_context(current_user))
        self.assertEqual(exc_info.exception.status_code, 429)
        self.assertEqual(exc_info.exception.detail, "حداکثر 2 منقضی در دقیقه مجاز است")

        offer = SimpleNamespace(id=1, user_id=5, status=OfferStatus.ACTIVE, home_server="iran", channel_message_id=None)
        db = FakeDB(scalar_result=9, get_result=offer)
        with patch("api.routers.offers.get_trading_settings", return_value=settings), patch(
            "bot.utils.redis_helpers.track_expire_rate",
            new=AsyncMock(return_value=1),
        ), patch(
            "bot.utils.redis_helpers.track_daily_expire",
            new=AsyncMock(return_value={"count": 3}),
        ), patch("core.services.offer_expiry_limits.date", wraps=date), patch(
            "api.routers.offers.is_remote_home",
            return_value=False,
        ):
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
        offer = SimpleNamespace(
            id=7,
            user_id=5,
            status=OfferStatus.ACTIVE,
            home_server="iran",
            offer_public_id="ofr_api_7",
            channel_message_id=None,
        )
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
        ) as set_count_mock, patch("api.routers.offers.current_server", return_value="iran"), patch(
            "core.services.offer_expiry_service.current_server",
            return_value="iran",
        ), patch(
            "api.routers.offers.is_remote_home",
            return_value=False,
        ):
            result = await expire_offer(offer_id=7, db=db, context=make_context(current_user))

        self.assertIsNone(result)
        self.assertEqual(offer.status, OfferStatus.EXPIRED)
        self.assertIsNotNone(offer.expired_at)
        self.assertEqual(offer.expire_reason, "manual")
        self.assertEqual(offer.expired_by_user_id, 5)
        self.assertEqual(offer.expired_by_actor_user_id, 5)
        self.assertEqual(offer.expire_source_surface, "webapp")
        self.assertEqual(offer.expire_source_server, "iran")
        db.commit.assert_awaited_once()
        publish_mock.assert_awaited_once_with("offer:expired", {"id": 7})
        set_count_mock.assert_awaited_once_with(5, 0)

    async def test_expire_offer_forwards_remote_home_without_local_mutation(self):
        settings = SimpleNamespace(
            offer_expire_rate_per_minute=5,
            offer_expire_daily_limit_after_threshold=10,
        )
        offer = SimpleNamespace(
            id=12,
            user_id=5,
            status=OfferStatus.ACTIVE,
            home_server="foreign",
            offer_public_id="ofr_remote_12",
            channel_message_id=None,
        )
        db = FakeDB(scalar_result=0, get_result=offer)
        current_user = SimpleNamespace(id=5)

        with patch("api.routers.offers.get_trading_settings", return_value=settings), patch(
            "bot.utils.redis_helpers.track_expire_rate",
            new=AsyncMock(return_value=1),
        ), patch(
            "bot.utils.redis_helpers.track_daily_expire",
            new=AsyncMock(return_value={"count": 0}),
        ), patch("api.routers.offers.is_remote_home", return_value=True), patch(
            "api.routers.offers.current_server",
            return_value="iran",
        ), patch(
            "api.routers.offers.forward_offer_expiry_to_home_server",
            new=AsyncMock(return_value=(200, {"expired": True})),
        ) as forward_mock:
            response = await expire_offer(offer_id=12, db=db, context=make_context(current_user))

        self.assertEqual(response.status_code, 204)
        self.assertEqual(offer.status, OfferStatus.ACTIVE)
        self.assertFalse(hasattr(offer, "expired_at"))
        db.commit.assert_not_awaited()
        forward_mock.assert_awaited_once()
        payload = forward_mock.await_args.args[1]
        self.assertEqual(payload["offer_id"], 12)
        self.assertEqual(payload["offer_public_id"], "ofr_remote_12")
        self.assertEqual(payload["source_surface"], "webapp")
        self.assertEqual(payload["source_server"], "iran")
        self.assertEqual(payload["expire_reason"], "manual")

    async def test_expire_offer_remote_home_outage_does_not_mutate_locally(self):
        settings = SimpleNamespace(
            offer_expire_rate_per_minute=5,
            offer_expire_daily_limit_after_threshold=10,
        )
        offer = SimpleNamespace(
            id=13,
            user_id=5,
            status=OfferStatus.ACTIVE,
            home_server="foreign",
            offer_public_id="ofr_remote_13",
            channel_message_id=None,
        )
        db = FakeDB(scalar_result=0, get_result=offer)
        current_user = SimpleNamespace(id=5)

        with patch("api.routers.offers.get_trading_settings", return_value=settings), patch(
            "bot.utils.redis_helpers.track_expire_rate",
            new=AsyncMock(return_value=1),
        ), patch(
            "bot.utils.redis_helpers.track_daily_expire",
            new=AsyncMock(return_value={"count": 0}),
        ), patch("api.routers.offers.is_remote_home", return_value=True), patch(
            "api.routers.offers.current_server",
            return_value="iran",
        ), patch(
            "api.routers.offers.forward_offer_expiry_to_home_server",
            new=AsyncMock(return_value=(503, {"detail": "سرور مرجع لفظ در دسترس نیست."})),
        ):
            response = await expire_offer(offer_id=13, db=db, context=make_context(current_user))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(offer.status, OfferStatus.ACTIVE)
        self.assertFalse(hasattr(offer, "expired_at"))
        self.assertFalse(hasattr(offer, "expire_reason"))
        db.commit.assert_not_awaited()

    async def test_internal_expire_records_forwarded_source_metadata(self):
        offer = SimpleNamespace(
            id=21,
            user_id=5,
            status=OfferStatus.ACTIVE,
            home_server="foreign",
            offer_public_id="ofr_internal_21",
            channel_message_id=None,
        )
        db = FakeDB(scalar_result=0, get_result=offer)
        request = SimpleNamespace(
            body=AsyncMock(return_value=b'{"offer_id":21}'),
            headers={
                "x-source-server": "iran",
                "x-timestamp": "123",
                "x-signature": "sig",
                "x-api-key": "key",
            },
        )
        payload = InternalOfferExpireRequest(
            offer_id=21,
            offer_public_id="ofr_internal_21",
            owner_user_id=5,
            actor_user_id=8,
            source_surface="webapp",
            source_server="iran",
            expire_reason="manual",
        )

        with patch("api.routers.offers.verify_internal_signature", return_value=True), patch(
            "api.routers.offers.current_server",
            return_value="foreign",
        ), patch("core.services.offer_expiry_service.current_server", return_value="foreign"), patch(
            "api.routers.realtime.publish_event",
            new=AsyncMock(),
        ) as publish_mock, patch(
            "core.cache.set_active_offer_count",
            new=AsyncMock(),
        ) as set_count_mock, patch(
            "bot.utils.redis_helpers.track_expire_rate",
            new=AsyncMock(return_value=1),
        ), patch(
            "bot.utils.redis_helpers.track_daily_expire",
            new=AsyncMock(return_value={"count": 0}),
        ):
            result = await expire_offer_internal(payload, request, db=db)

        self.assertEqual(result, {"expired": True, "offer_id": 21})
        self.assertEqual(offer.status, OfferStatus.EXPIRED)
        self.assertEqual(offer.expire_reason, "manual")
        self.assertEqual(offer.expired_by_user_id, 5)
        self.assertEqual(offer.expired_by_actor_user_id, 8)
        self.assertEqual(offer.expire_source_surface, "webapp")
        self.assertEqual(offer.expire_source_server, "iran")
        db.commit.assert_awaited_once()
        publish_mock.assert_awaited_once_with("offer:expired", {"id": 21})
        set_count_mock.assert_awaited_once_with(5, 0)

    async def test_expire_offer_applies_channel_state_when_message_exists(self):
        settings = SimpleNamespace(
            offer_expire_rate_per_minute=5,
            offer_expire_daily_limit_after_threshold=10,
        )
        offer = SimpleNamespace(id=9, user_id=5, status=OfferStatus.ACTIVE, channel_message_id=333)
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
        offer = SimpleNamespace(id=10, user_id=5, status=OfferStatus.ACTIVE, channel_message_id=444)
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
            SimpleNamespace(id=1, status=OfferStatus.ACTIVE, home_server="foreign", channel_message_id=333, user_id=5),
            SimpleNamespace(id=2, status=OfferStatus.ACTIVE, home_server="foreign", channel_message_id=None, user_id=5),
        ]
        db = FakeDB(execute_results=[FakeExecuteResult(offers)])
        order = []
        db.commit.side_effect = lambda: order.append("commit")

        async def fail_channel_state(*_args, **_kwargs):
            order.append("channel")
            raise RuntimeError("telegram down")

        with patch("api.routers.offers.current_server", return_value="foreign"), patch(
            "core.services.offer_expiry_service.current_server",
            return_value="foreign",
        ), patch(
            "api.routers.offers.apply_offer_channel_state",
            new=AsyncMock(side_effect=fail_channel_state),
        ) as apply_offer_channel_state, patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "core.cache.set_active_offer_count",
            new=AsyncMock(side_effect=lambda *_args, **_kwargs: order.append("cache")),
        ) as set_count_mock, patch("api.routers.offers.logger") as logger:
            result = await cancel_all_active_offers(db=db, context=make_context(SimpleNamespace(id=5)))

        self.assertEqual(result, {"cancelled_count": 2})
        self.assertEqual([offer.status for offer in offers], [OfferStatus.EXPIRED, OfferStatus.EXPIRED])
        self.assertEqual([offer.expire_reason for offer in offers], ["cancel_all", "cancel_all"])
        self.assertEqual([offer.expired_by_user_id for offer in offers], [5, 5])
        self.assertEqual([offer.expired_by_actor_user_id for offer in offers], [5, 5])
        self.assertEqual([offer.expire_source_surface for offer in offers], ["webapp", "webapp"])
        self.assertEqual([offer.expire_source_server for offer in offers], ["foreign", "foreign"])
        self.assertEqual(publish_mock.await_count, 2)
        set_count_mock.assert_awaited_once_with(5, 0)
        apply_offer_channel_state.assert_awaited_once_with(offers[0], reason="cancel_all_active_offers", timeout=5)
        db.commit.assert_awaited_once()
        self.assertEqual(order[0], "commit")
        logger.warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
