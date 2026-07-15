import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

from fastapi import HTTPException

from api.routers.offers import OfferCreate, create_offer
from core.services.market_transition_service import MarketOfferAdmissionClosedError
from core.services.offer_creation_service import OfferCreationLimitExceededError
from core.services.offer_republish_service import OfferNotRepeatableError
from core.enums import UserRole
from models.offer import OfferStatus, OfferType
from tests.offer_creation_quota_test_helpers import bypass_local_offer_quota


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        value = self._value
        if value is None:
            values = []
        elif isinstance(value, list):
            values = value
        else:
            values = [value]
        return SimpleNamespace(all=lambda: values)


def empty_customer_read_context_results():
    return [FakeExecuteResult([]), FakeExecuteResult(None)]


class FakeDB:
    def __init__(self, *, get_results=None, execute_results=None, scalar_results=None):
        self.get_results = list(get_results or [])
        self.execute_results = list(execute_results or [])
        self.scalar_results = list(scalar_results or [])
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.refresh = AsyncMock(side_effect=self._refresh)
        self.added = []

    async def get(self, _model, _id):
        if not self.get_results:
            raise AssertionError("Unexpected get() call")
        return self.get_results.pop(0)

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    async def scalar(self, _stmt):
        if self.scalar_results:
            self.last_scalar_value = self.scalar_results.pop(0)
            return self.last_scalar_value

        # In SQLAlchemy 2.0, scalar() is often a shortcut for execute().standard_scalar()
        # Fall back to the execute queue only when a dedicated scalar result was not supplied.
        res = await self.execute(_stmt)
        if isinstance(res, FakeExecuteResult):
            self.last_scalar_value = res.scalar_one()
        else:
            self.last_scalar_value = res
        return self.last_scalar_value

    def add(self, item):
        self.added.append(item)

    async def _refresh(self, item):
        if getattr(item, "id", None) is None:
            item.id = 77
        if getattr(item, "created_at", None) is None:
            item.created_at = datetime(2026, 1, 1, 12, 0, 0)
        return item


def make_offer(**overrides):
    data = {
        "offer_type": "buy",
        "commodity_id": 1,
        "quantity": 10,
        "price": 123456,
        "is_wholesale": True,
        "lot_sizes": None,
        "notes": "urgent",
        "republished_from_id": None,
        "republished_from_public_id": None,
    }
    data.update(overrides)
    return OfferCreate(**data)


def make_user(**overrides):
    data = {
        "id": 5,
        "role": UserRole.STANDARD,
        "trading_restricted_until": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_context(owner_user=None, actor_user=None):
    owner = owner_user or make_user()
    actor = actor_user or owner
    return SimpleNamespace(owner_user=owner, actor_user=actor, relation=None, is_accountant_context=owner.id != actor.id)


def make_reloaded_offer(*, offer_id=77, channel_message_id=None, notes="urgent"):
    return SimpleNamespace(
        id=offer_id,
        offer_public_id=f"ofr_offer_{offer_id}",
        user_id=5,
        actor_user_id=5,
        home_server="iran",
        offer_type=OfferType.BUY,
        settlement_type="cash",
        commodity_id=1,
        commodity=SimpleNamespace(name="Gold"),
        user=SimpleNamespace(account_name="user1"),
        quantity=10,
        remaining_quantity=10,
        price=123456,
        is_wholesale=True,
        lot_sizes=None,
        original_lot_sizes=None,
        notes=notes,
        status=OfferStatus.ACTIVE,
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        channel_message_id=channel_message_id,
        republished_from_offer_public_id=None,
        idempotency_fingerprint_version=None,
        idempotency_fingerprint=None,
    )


class OffersRouterCreateSuccessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        market_eval_patcher = patch(
            "api.routers.offers.evaluate_current_market_schedule",
            new=AsyncMock(return_value=SimpleNamespace(is_open=True, reason="daily_window_open")),
        )
        market_eval_patcher.start()
        self.addCleanup(market_eval_patcher.stop)
        customer_relation_patcher = patch(
            "api.routers.offers.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        )
        customer_relation_patcher.start()
        self.addCleanup(customer_relation_patcher.stop)
        register_offer_patcher = patch(
            "api.routers.offers.register_market_offer_created",
            new=AsyncMock(),
        )
        self.register_market_offer_created_mock = register_offer_patcher.start()
        self.addCleanup(register_offer_patcher.stop)
        admission_fence_patcher = patch(
            "core.services.offer_creation_service.acquire_market_offer_admission_fence",
            new=AsyncMock(return_value=SimpleNamespace(is_open=True)),
        )
        self.admission_fence_mock = admission_fence_patcher.start()
        self.addCleanup(admission_fence_patcher.stop)
        router_admission_fence_patcher = patch(
            "api.routers.offers.acquire_market_offer_admission_fence",
            new=AsyncMock(return_value=SimpleNamespace(is_open=True)),
        )
        self.router_admission_fence_mock = router_admission_fence_patcher.start()
        self.addCleanup(router_admission_fence_patcher.stop)
        quota_patcher = patch(
            "core.services.offer_creation_service._admit_local_offer_quota",
            new=AsyncMock(side_effect=bypass_local_offer_quota),
        )
        quota_patcher.start()
        self.addCleanup(quota_patcher.stop)

    async def test_republish_creates_independent_offer_from_remaining_source(self):
        commodity = SimpleNamespace(id=1)
        old_offer = SimpleNamespace(
            id=99,
            offer_public_id="ofr_source_99",
            user_id=5,
            status=OfferStatus.EXPIRED,
            offer_type=OfferType.BUY,
            settlement_type="cash",
            commodity_id=1,
            quantity=10,
            remaining_quantity=8,
            price=123456,
            is_wholesale=False,
            lot_sizes=[8],
            original_lot_sizes=[5, 7, 8],
            notes="urgent",
            republished_offer_id=None,
            channel_message_id=None,
        )
        reloaded_offer = make_reloaded_offer(offer_id=77)
        reloaded_offer.quantity = 8
        reloaded_offer.remaining_quantity = 8
        reloaded_offer.is_wholesale = False
        reloaded_offer.lot_sizes = [8]
        reloaded_offer.original_lot_sizes = [8]
        db = FakeDB(
            get_results=[commodity],
            scalar_results=[0],
            execute_results=[
                FakeExecuteResult(None),
                FakeExecuteResult(reloaded_offer),
                *empty_customer_read_context_results(),
            ],
        )
        current_user = make_user()
        settings = SimpleNamespace(max_active_offers=1)
        async_settings = SimpleNamespace(offer_expiry_minutes=30)

        with patch(
            "api.routers.offers.lock_repeatable_offer", new=AsyncMock(return_value=old_offer)
        ), patch("api.routers.offers.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "api.routers.offers.get_trading_settings",
            return_value=settings,
        ), patch("core.cache.get_active_offer_count", new=AsyncMock(return_value=0)), patch(
            "core.services.trade_service.validate_quantity",
            return_value=(True, None),
        ), patch("core.services.trade_service.validate_price", return_value=(True, None)), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "core.services.trade_service.detect_offer_price_warning",
            new=AsyncMock(return_value=None),
        ), patch("api.routers.offers.current_server", return_value="iran"), patch(
            "api.routers.offers.publish_offer_to_telegram_channel_once",
            new=AsyncMock(return_value=SimpleNamespace(message_id=None)),
        ), patch("core.cache.set_active_offer_count", new=AsyncMock()) as set_count_mock, patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=async_settings),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.offers.offer_to_response",
            return_value={"id": 77, "user_id": 5},
        ) as response_mock:
            result = await create_offer(
                make_offer(
                    quantity=8,
                    is_wholesale=False,
                    lot_sizes=[8],
                    republished_from_id=99,
                    republished_from_public_id="ofr_source_99",
                    idempotency_key="republish-source-99",
                ),
                db=db,
                context=make_context(current_user),
            )

        new_offer = db.added[0]
        self.assertEqual(new_offer.user_id, 5)
        self.assertEqual(new_offer.home_server, "iran")
        self.assertTrue(new_offer.offer_public_id.startswith("ofr_"))
        self.assertEqual(new_offer.offer_type, OfferType.BUY)
        self.assertEqual(new_offer.quantity, 8)
        self.assertEqual(new_offer.remaining_quantity, 8)
        self.assertEqual(new_offer.lot_sizes, [8])
        self.assertEqual(new_offer.original_lot_sizes, [8])
        self.assertEqual(new_offer.republished_from_offer_public_id, "ofr_source_99")
        self.assertEqual(old_offer.status, OfferStatus.EXPIRED)
        self.assertIsNone(old_offer.republished_offer_id)
        self.assertEqual(db.commit.await_count, 1)
        set_count_mock.assert_awaited_once_with(5, 1)
        self.assertEqual(publish_mock.await_count, 1)
        response_mock.assert_called_once_with(
            reloaded_offer,
            async_settings,
            viewer_user_id=5,
            include_owner_identity=True,
            offer_owner_relation=None,
            viewer_customer_relation=None,
        )
        self.assertEqual(result, {"id": 77, "user_id": 5})
        self.router_admission_fence_mock.assert_awaited_once_with(db)
        self.register_market_offer_created_mock.assert_awaited_once_with(db)

    async def test_republish_rejects_ineligible_source_before_side_effects(self):
        db = FakeDB(execute_results=[FakeExecuteResult(None)])
        with patch(
            "api.routers.offers.lock_repeatable_offer",
            new=AsyncMock(side_effect=OfferNotRepeatableError("offer_ineligible")),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(
                    make_offer(
                        republished_from_id=99,
                        republished_from_public_id="ofr_source_99",
                        idempotency_key="republish-ineligible-99",
                    ),
                    db=db,
                    context=make_context(),
                )

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(db.added, [])
        db.commit.assert_not_awaited()

    async def test_republish_rejects_payload_not_matching_source_remainder(self):
        source = SimpleNamespace(
            id=98,
            offer_public_id="ofr_source_98",
            user_id=5,
            offer_type=OfferType.BUY,
            settlement_type="cash",
            commodity_id=1,
            quantity=10,
            remaining_quantity=5,
            price=123456,
            is_wholesale=True,
            lot_sizes=None,
            notes="urgent",
        )
        db = FakeDB(execute_results=[FakeExecuteResult(None)])
        with patch("api.routers.offers.lock_repeatable_offer", new=AsyncMock(return_value=source)):
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(
                    make_offer(
                        quantity=10,
                        republished_from_id=98,
                        republished_from_public_id="ofr_source_98",
                        idempotency_key="republish-mismatch-98",
                    ),
                    db=db,
                    context=make_context(),
                )

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(db.added, [])

    async def test_create_offer_idempotent_replay_returns_existing_offer_without_side_effects(self):
        existing_offer = make_reloaded_offer(offer_id=72)
        db = FakeDB(execute_results=[FakeExecuteResult(existing_offer), *empty_customer_read_context_results()])
        current_user = make_user()
        async_settings = SimpleNamespace(offer_expiry_minutes=30)

        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=async_settings),
        ), patch(
            "api.routers.offers.offer_to_response",
            return_value={"id": 72, "replayed": True},
        ) as response_mock, patch(
            "api.routers.offers.publish_offer_to_telegram_channel_once",
            new=AsyncMock(),
        ) as channel_mock, patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock:
            result = await create_offer(
                make_offer(idempotency_key="offer-create-1"),
                db=db,
                context=make_context(current_user),
            )

        self.assertEqual(result, {"id": 72, "replayed": True})
        self.assertEqual(db.added, [])
        db.commit.assert_not_awaited()
        db.refresh.assert_not_awaited()
        channel_mock.assert_not_awaited()
        publish_mock.assert_not_awaited()
        self.register_market_offer_created_mock.assert_not_awaited()
        response_mock.assert_called_once_with(
            existing_offer,
            async_settings,
            viewer_user_id=5,
            include_owner_identity=True,
            offer_owner_relation=None,
            viewer_customer_relation=None,
        )

    async def test_create_offer_rejects_same_idempotency_key_with_different_payload(self):
        existing_offer = make_reloaded_offer(offer_id=73)
        db = FakeDB(execute_results=[FakeExecuteResult(existing_offer)])

        with patch(
            "api.routers.offers.publish_offer_to_telegram_channel_once",
            new=AsyncMock(),
        ) as channel_mock, patch(
            "api.routers.realtime.publish_event",
            new=AsyncMock(),
        ) as publish_mock:
            with self.assertRaises(HTTPException) as raised:
                await create_offer(
                    make_offer(idempotency_key="offer-create-1", price=123457),
                    db=db,
                    context=make_context(),
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("مشخصات متفاوت", raised.exception.detail)
        self.assertEqual(db.added, [])
        db.commit.assert_not_awaited()
        channel_mock.assert_not_awaited()
        publish_mock.assert_not_awaited()
        self.register_market_offer_created_mock.assert_not_awaited()

    async def test_create_offer_uses_webapp_home_server_even_when_owner_and_runtime_are_foreign(self):
        commodity = SimpleNamespace(id=1)
        reloaded_offer = make_reloaded_offer(offer_id=88)
        db = FakeDB(
            get_results=[commodity],
            scalar_results=[0],
            execute_results=[FakeExecuteResult(reloaded_offer), *empty_customer_read_context_results()],
        )
        current_user = make_user(home_server="foreign")
        settings = SimpleNamespace(max_active_offers=5)
        async_settings = SimpleNamespace(offer_expiry_minutes=15)

        with patch("api.routers.offers.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "api.routers.offers.get_trading_settings",
            return_value=settings,
        ), patch("core.cache.get_active_offer_count", new=AsyncMock(return_value=0)), patch(
            "core.services.trade_service.validate_quantity",
            return_value=(True, None),
        ), patch("core.services.trade_service.validate_price", return_value=(True, None)), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "core.services.trade_service.detect_offer_price_warning",
            new=AsyncMock(return_value=None),
        ), patch("api.routers.offers.current_server", return_value="foreign"), patch(
            "api.routers.offers.publish_offer_to_telegram_channel_once",
            new=AsyncMock(return_value=SimpleNamespace(message_id=555)),
        ), patch("core.cache.set_active_offer_count", new=AsyncMock()) as set_count_mock, patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=async_settings),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.offers.offer_to_response",
            return_value={"id": 88, "channel_message_id": 555},
        ) as response_mock:
            result = await create_offer(make_offer(), db=db, context=make_context(current_user))

        new_offer = db.added[0]
        self.assertEqual(new_offer.home_server, "iran")
        self.assertTrue(new_offer.offer_public_id.startswith("ofr_"))
        self.assertEqual(reloaded_offer.channel_message_id, 555)
        self.assertEqual(db.commit.await_count, 2)
        set_count_mock.assert_awaited_once_with(5, 1)
        publish_mock.assert_awaited_once_with(
            "offer:created",
            {
                "id": 88,
                "offer_public_id": "ofr_offer_88",
                "public_link": "/market?offer=ofr_offer_88",
                "user_id": None,
                "offer_type": "buy",
                "settlement_type": "cash",
                "commodity_id": 1,
                "commodity_name": "Gold",
                "quantity": 10,
                "remaining_quantity": 10,
                "price": 123456,
                "status": "active",
                "created_at": unittest.mock.ANY,
                "user_account_name": "",
                "is_own_offer": False,
                "notes": "urgent",
                "is_wholesale": True,
                "lot_sizes": None,
                "original_lot_sizes": None,
                "expires_at_ts": int(reloaded_offer.created_at.timestamp() + 15 * 60),
            },
        )
        response_mock.assert_called_once_with(
            reloaded_offer,
            async_settings,
            viewer_user_id=5,
            include_owner_identity=True,
            offer_owner_relation=None,
            viewer_customer_relation=None,
        )
        self.assertEqual(result, {"id": 88, "channel_message_id": 555})

    async def test_create_offer_tolerates_post_commit_cache_and_realtime_failures(self):
        commodity = SimpleNamespace(id=1)
        reloaded_offer = make_reloaded_offer(offer_id=89)
        db = FakeDB(
            get_results=[commodity],
            scalar_results=[0],
            execute_results=[FakeExecuteResult(reloaded_offer), *empty_customer_read_context_results()],
        )
        current_user = make_user()
        settings = SimpleNamespace(max_active_offers=5)
        async_settings = SimpleNamespace(offer_expiry_minutes=15)

        with patch("api.routers.offers.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "api.routers.offers.get_trading_settings",
            return_value=settings,
        ), patch("core.cache.get_active_offer_count", new=AsyncMock(return_value=0)), patch(
            "core.services.trade_service.validate_quantity",
            return_value=(True, None),
        ), patch("core.services.trade_service.validate_price", return_value=(True, None)), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "core.services.trade_service.detect_offer_price_warning",
            new=AsyncMock(return_value=None),
        ), patch("api.routers.offers.current_server", return_value="foreign"), patch(
            "api.routers.offers.publish_offer_to_telegram_channel_once",
            new=AsyncMock(return_value=SimpleNamespace(message_id=None)),
        ), patch(
            "core.cache.set_active_offer_count",
            new=AsyncMock(side_effect=RuntimeError("redis down")),
        ), patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=async_settings),
        ), patch(
            "api.routers.realtime.publish_event",
            new=AsyncMock(side_effect=RuntimeError("pubsub down")),
        ) as publish_mock, patch(
            "api.routers.offers.offer_to_response",
            return_value={"id": 89},
        ), patch("api.routers.offers.logger") as logger:
            result = await create_offer(make_offer(), db=db, context=make_context(current_user))

        self.assertEqual(result, {"id": 89})
        self.assertEqual(db.added[0].status, OfferStatus.ACTIVE)
        db.commit.assert_awaited_once()
        publish_mock.assert_awaited_once()
        self.assertGreaterEqual(logger.warning.call_count, 2)

    async def test_create_offer_tolerates_sse_expiry_calculation_failures(self):
        commodity = SimpleNamespace(id=1)
        reloaded_offer = make_reloaded_offer(offer_id=99)
        reloaded_offer.created_at = SimpleNamespace(timestamp=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        db = FakeDB(
            get_results=[commodity],
            scalar_results=[0],
            execute_results=[FakeExecuteResult(reloaded_offer), *empty_customer_read_context_results()],
        )
        current_user = make_user()
        settings = SimpleNamespace(max_active_offers=5)
        async_settings = SimpleNamespace(offer_expiry_minutes=15)

        with patch("api.routers.offers.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "api.routers.offers.get_trading_settings",
            return_value=settings,
        ), patch("core.cache.get_active_offer_count", new=AsyncMock(return_value=0)), patch(
            "core.services.trade_service.validate_quantity",
            return_value=(True, None),
        ), patch("core.services.trade_service.validate_price", return_value=(True, None)), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "core.services.trade_service.detect_offer_price_warning",
            new=AsyncMock(return_value=None),
        ), patch("api.routers.offers.current_server", return_value="foreign"), patch(
            "api.routers.offers.publish_offer_to_telegram_channel_once",
            new=AsyncMock(return_value=SimpleNamespace(message_id=None)),
        ), patch("core.cache.set_active_offer_count", new=AsyncMock()), patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=async_settings),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.offers.offer_to_response",
            return_value={"id": 99},
        ), patch("api.routers.offers.to_jalali_str", return_value=""):
            result = await create_offer(make_offer(), db=db, context=make_context(current_user))

        publish_mock.assert_awaited_once_with(
            "offer:created",
            {
                "id": 99,
                "offer_public_id": "ofr_offer_99",
                "public_link": "/market?offer=ofr_offer_99",
                "user_id": None,
                "offer_type": "buy",
                "settlement_type": "cash",
                "commodity_id": 1,
                "commodity_name": "Gold",
                "quantity": 10,
                "remaining_quantity": 10,
                "price": 123456,
                "status": "active",
                "created_at": "",
                "user_account_name": "",
                "is_own_offer": False,
                "notes": "urgent",
                "is_wholesale": True,
                "lot_sizes": None,
                "original_lot_sizes": None,
                "expires_at_ts": None,
            },
        )
        self.assertEqual(result, {"id": 99})

    async def test_create_offer_rejects_when_market_closes_at_final_admission_without_side_effects(self):
        commodity = SimpleNamespace(id=1)
        current_user = make_user(id=5, home_server="iran")
        db = FakeDB(
            get_results=[commodity],
            scalar_results=[0],
            execute_results=[FakeExecuteResult(None)],
        )
        self.admission_fence_mock.side_effect = MarketOfferAdmissionClosedError(
            "market_closed_during_offer_admission"
        )

        with patch("api.routers.offers.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "api.routers.offers.get_trading_settings",
            return_value=SimpleNamespace(max_active_offers=5),
        ), patch("core.cache.get_active_offer_count", new=AsyncMock(return_value=0)), patch(
            "core.services.trade_service.validate_quantity",
            return_value=(True, None),
        ), patch("core.services.trade_service.validate_price", return_value=(True, None)), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "core.services.trade_service.detect_offer_price_warning",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.offers.publish_offer_to_telegram_channel_once",
            new=AsyncMock(),
        ) as channel_mock, patch(
            "api.routers.realtime.publish_event",
            new=AsyncMock(),
        ) as publish_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(make_offer(), db=db, context=make_context(current_user))

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(
            exc_info.exception.detail,
            "بازار در حال حاضر بسته است. لطفاً در زمان فعال بودن بازار اقدام کنید.",
        )
        self.assertEqual(db.added, [])
        db.rollback.assert_awaited_once()
        db.commit.assert_not_awaited()
        channel_mock.assert_not_awaited()
        publish_mock.assert_not_awaited()
        self.register_market_offer_created_mock.assert_not_awaited()

    async def test_create_offer_maps_final_quota_rejection_without_side_effects(self):
        commodity = SimpleNamespace(id=1)
        current_user = make_user(id=5, home_server="iran")
        db = FakeDB(get_results=[commodity], scalar_results=[0])
        quota_error = OfferCreationLimitExceededError(
            "offer_active_limit_exceeded",
            "شما حداکثر 1 لفظ فعال دارید. لطفاً ابتدا یکی را منقضی کنید.",
        )

        with patch("api.routers.offers.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "api.routers.offers.get_trading_settings",
            return_value=SimpleNamespace(max_active_offers=1),
        ), patch("core.cache.get_active_offer_count", new=AsyncMock(return_value=0)), patch(
            "core.services.trade_service.validate_quantity",
            return_value=(True, None),
        ), patch("core.services.trade_service.validate_price", return_value=(True, None)), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "core.services.trade_service.detect_offer_price_warning",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.offers.create_authoritative_offer_with_outcome",
            new=AsyncMock(side_effect=quota_error),
        ), patch(
            "api.routers.offers.publish_offer_to_telegram_channel_once",
            new=AsyncMock(),
        ) as channel_mock, patch(
            "api.routers.realtime.publish_event",
            new=AsyncMock(),
        ) as publish_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(make_offer(), db=db, context=make_context(current_user))

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, quota_error.detail)
        db.rollback.assert_awaited_once()
        db.commit.assert_not_awaited()
        channel_mock.assert_not_awaited()
        publish_mock.assert_not_awaited()
        self.register_market_offer_created_mock.assert_not_awaited()

    async def test_create_offer_stamps_owner_actor_id_for_self_context(self):
        commodity = SimpleNamespace(id=1)
        current_user = make_user(id=5, home_server="iran")
        reloaded_offer = make_reloaded_offer(offer_id=120)
        reloaded_offer.user_id = 5
        db = FakeDB(
            get_results=[commodity],
            scalar_results=[0],
            execute_results=[FakeExecuteResult(reloaded_offer), *empty_customer_read_context_results()],
        )
        settings = SimpleNamespace(max_active_offers=5)
        async_settings = SimpleNamespace(offer_expiry_minutes=30)

        with patch("api.routers.offers.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "api.routers.offers.get_trading_settings",
            return_value=settings,
        ), patch("core.cache.get_active_offer_count", new=AsyncMock(return_value=0)), patch(
            "core.services.trade_service.validate_quantity",
            return_value=(True, None),
        ), patch("core.services.trade_service.validate_price", return_value=(True, None)), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "core.services.trade_service.detect_offer_price_warning",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.offers.publish_offer_to_telegram_channel_once",
            new=AsyncMock(return_value=SimpleNamespace(message_id=None)),
        ) as send_mock, patch("core.cache.set_active_offer_count", new=AsyncMock()) as set_count_mock, patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=async_settings),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()), patch(
            "api.routers.offers.offer_to_response",
            return_value={"id": 120, "user_id": 5},
        ) as response_mock:
            result = await create_offer(make_offer(), db=db, context=make_context(current_user))

        new_offer = db.added[0]
        self.assertEqual(new_offer.user_id, 5)
        self.assertEqual(new_offer.actor_user_id, 5)
        self.assertEqual(new_offer.home_server, "iran")
        send_mock.assert_awaited_once_with(
            db,
            reloaded_offer,
            current_user,
            send_offer_to_channel=ANY,
        )
        set_count_mock.assert_awaited_once_with(5, 1)
        response_mock.assert_called_once_with(
            reloaded_offer,
            async_settings,
            viewer_user_id=5,
            include_owner_identity=True,
            offer_owner_relation=None,
            viewer_customer_relation=None,
        )
        self.assertEqual(result, {"id": 120, "user_id": 5})

    async def test_create_offer_flags_acknowledged_warning_offers_for_competitive_exclusion(self):
        commodity = SimpleNamespace(id=1)
        reloaded_offer = make_reloaded_offer(offer_id=140)
        db = FakeDB(
            get_results=[commodity],
            scalar_results=[0],
            execute_results=[FakeExecuteResult(reloaded_offer), *empty_customer_read_context_results()],
        )
        current_user = make_user()
        settings = SimpleNamespace(max_active_offers=5)
        async_settings = SimpleNamespace(offer_expiry_minutes=15)
        warning_payload = {
            "error_code": "OFFER_PRICE_WARNING",
            "warning_type": "sell_below_lowest_active",
            "title": "هشدار قیمت فروش",
            "detail": "warn",
            "message": "warn",
            "reference_label": "ref",
            "reference_price": 100000,
            "proposed_price": 99900,
            "difference_percent": 0.1,
        }

        with patch("api.routers.offers.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "api.routers.offers.get_trading_settings",
            return_value=settings,
        ), patch("core.cache.get_active_offer_count", new=AsyncMock(return_value=0)), patch(
            "core.services.trade_service.validate_quantity",
            return_value=(True, None),
        ), patch("core.services.trade_service.validate_price", return_value=(True, None)), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ) as competitive_price_mock, patch(
            "core.services.trade_service.detect_offer_price_warning",
            new=AsyncMock(return_value=warning_payload),
        ) as price_warning_mock, patch("api.routers.offers.current_server", return_value="foreign"), patch(
            "api.routers.offers.publish_offer_to_telegram_channel_once",
            new=AsyncMock(return_value=SimpleNamespace(message_id=None)),
        ), patch("core.cache.set_active_offer_count", new=AsyncMock()), patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=async_settings),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()), patch(
            "api.routers.offers.offer_to_response",
            return_value={"id": 140},
        ):
            await create_offer(
                make_offer(offer_type="sell", price=99900, warning_acknowledged=True),
                db=db,
                context=make_context(current_user),
            )

        self.assertEqual(competitive_price_mock.await_args.kwargs["settlement_type"], "cash")
        self.assertEqual(price_warning_mock.await_args.kwargs["settlement_type"], "cash")
        new_offer = db.added[0]
        self.assertTrue(new_offer.exclude_from_competitive_price)
        self.assertEqual(new_offer.price_warning_type, "sell_below_lowest_active")


if __name__ == "__main__":
    unittest.main()
