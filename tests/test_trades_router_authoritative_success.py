import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, HTTPException

from api.routers.trades import TradeCreate, _execute_trade_authoritatively
from core.enums import NotificationCategory, NotificationLevel, UserRole
from models.customer_relation import CustomerTier
from models.offer import OfferStatus, OfferType
from models.offer_request import OfferRequest, OfferRequestSourceSurface, OfferRequestStatus
from models.trade import TradeStatus, TradeType


class FakeStaleDataError(Exception):
    pass


class FakeExecuteResult:
    def __init__(self, *, single=None, single_or_none=None):
        self._single = single
        self._single_or_none = single_or_none

    def scalar_one(self):
        return self._single

    def scalar_one_or_none(self):
        return self._single_or_none


class FakeDB:
    def __init__(self, *, get_results=None, execute_results=None, scalar_result=None, commit_side_effect=None):
        self.get_results = list(get_results or [])
        self.execute_results = list(execute_results or [])
        self.scalar_result = scalar_result
        self.commit = AsyncMock(side_effect=commit_side_effect)
        self.rollback = AsyncMock()
        self.refresh = AsyncMock()
        self.flush = AsyncMock()
        self.added = []
        self.offer_requests = []

    async def get(self, _model, _id, **_kwargs):
        if not self.get_results:
            raise AssertionError("Unexpected get() call")
        return self.get_results.pop(0)

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    async def scalar(self, _stmt):
        return self.scalar_result

    def add(self, item):
        if isinstance(item, OfferRequest):
            self.offer_requests.append(item)
            return
        self.added.append(item)


def make_user(**overrides):
    data = {
        "id": 5,
        "role": UserRole.STANDARD,
        "trading_restricted_until": None,
        "mobile_number": "09120000000",
        "account_name": "buyer",
        "telegram_id": 555,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_offer(**overrides):
    data = {
        "id": 7,
        "status": OfferStatus.ACTIVE,
        "user_id": 9,
        "quantity": 4,
        "remaining_quantity": 4,
        "is_wholesale": True,
        "lot_sizes": None,
        "offer_type": OfferType.SELL,
        "price": 123456,
        "offer_public_id": "ofr_test_7",
        "home_server": "foreign",
        "commodity_id": 1,
        "commodity": SimpleNamespace(name="Gold"),
        "user": SimpleNamespace(account_name="seller", mobile_number="09125555555", telegram_id=999),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_context(owner_user, actor_user=None):
    actor = actor_user or owner_user
    return SimpleNamespace(owner_user=owner_user, actor_user=actor, relation=None, is_accountant_context=owner_user.id != actor.id)


class TradesRouterAuthoritativeSuccessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        customer_relation_patcher = patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        )
        customer_relation_patcher.start()
        self.addCleanup(customer_relation_patcher.stop)
        trade_relation_map_patcher = patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value={}),
        )
        trade_relation_map_patcher.start()
        self.addCleanup(trade_relation_map_patcher.stop)
        user_event_patcher = patch(
            "api.routers.realtime.publish_user_event",
            new=AsyncMock(),
        )
        self.publish_user_event_mock = user_event_patcher.start()
        self.addCleanup(user_event_patcher.stop)
        market_eval_patcher = patch(
            "api.routers.trades.evaluate_current_market_schedule",
            new=AsyncMock(return_value=SimpleNamespace(is_open=True, reason="daily_window_open")),
        )
        market_eval_patcher.start()
        self.addCleanup(market_eval_patcher.stop)

    async def test_execute_trade_authoritatively_converts_stale_commit_to_conflict(self):
        locked_user = make_user()
        offer = make_offer()
        db = FakeDB(
            get_results=[offer],
            execute_results=[FakeExecuteResult(single=locked_user), FakeExecuteResult(single_or_none=None)],
            commit_side_effect=FakeStaleDataError("stale"),
            scalar_result=10000,
        )

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await _execute_trade_authoritatively(
                    TradeCreate(offer_id=7, quantity=4),
                    BackgroundTasks(),
                    db=db,
                    context=make_context(locked_user),
                )

        db.rollback.assert_awaited_once()
        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(exc_info.exception.detail, "این لفظ توسط کاربر دیگری در حال معامله است. لطفاً دوباره تلاش کنید.")

    async def test_execute_trade_authoritatively_persists_trade_and_runs_side_effects(self):
        locked_user = make_user()
        context = make_context(locked_user)
        offer = make_offer(
            user=SimpleNamespace(id=9, account_name="seller", mobile_number="09125555555", telegram_id=999),
        )
        reloaded_trade = SimpleNamespace(id=88, created_at=datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc))
        db = FakeDB(
            get_results=[offer],
            execute_results=[
                FakeExecuteResult(single=locked_user),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=9999,
        )
        background_tasks = BackgroundTasks()

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=[[locked_user.id, 15], [offer.user_id, 19]]),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.update_channel_buttons", new=AsyncMock(return_value=True)) as update_buttons_mock, patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ) as notif_mock, patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ) as counter_mock, patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.trades.trade_to_response",
            return_value={"id": 88, "trade_number": 10000},
        ) as response_mock:
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4),
                background_tasks,
                db=db,
                context=context,
            )

        self.assertEqual(len(db.added), 1)
        new_trade = db.added[0]
        self.assertEqual(new_trade.trade_number, 10000)
        self.assertEqual(new_trade.offer_id, 7)
        self.assertEqual(new_trade.trade_type, TradeType.BUY)
        self.assertEqual(new_trade.status, TradeStatus.COMPLETED)
        self.assertEqual(new_trade.quantity, 4)
        self.assertEqual(new_trade.responder_user_id, locked_user.id)
        self.assertEqual(new_trade.actor_user_id, locked_user.id)
        self.assertEqual(len(db.offer_requests), 1)
        self.assertEqual(db.offer_requests[0].result_status, OfferRequestStatus.COMPLETED_TRADE)
        self.assertEqual(db.offer_requests[0].request_source_surface, OfferRequestSourceSurface.WEBAPP)
        self.assertEqual(db.offer_requests[0].offer_public_id, "ofr_test_7")
        self.assertEqual(offer.remaining_quantity, 0)
        self.assertEqual(offer.status, OfferStatus.COMPLETED)
        db.refresh.assert_awaited_once_with(offer, ["user", "commodity"])
        db.commit.assert_awaited_once()
        update_buttons_mock.assert_awaited_once_with(offer)
        self.assertEqual(len(background_tasks.tasks), 2)
        self.assertEqual(notif_mock.await_count, 4)
        responder_notification_message = notif_mock.await_args_list[0].args[2]
        self.assertNotIn("|", responder_notification_message)
        self.assertIn("👤 طرف معامله: seller", responder_notification_message)
        self.assertIn("🔢 شماره معامله: 10000", responder_notification_message)
        self.assertIn("🕐 زمان معامله: 1405/03/06   15:30", responder_notification_message)
        self.assertEqual(
            notif_mock.await_args_list[0].kwargs,
            {
                "level": NotificationLevel.SUCCESS,
                "category": NotificationCategory.TRADE,
                "extra_payload": {
                    "route": "/users/9?account_name=seller",
                    "trade_number": 10000,
                    "counterparty_profile_user_id": 9,
                    "counterparty_profile_account_name": "seller",
                    "highlight_accountant_user_id": None,
                    "highlight_accountant_relation_display_name": None,
                },
            },
        )
        counter_mock.assert_awaited_once_with(db, locked_user, "trade", 4)
        self.assertEqual(publish_mock.await_count, 2)
        self.assertEqual(publish_mock.await_args_list[0].args[0], "trade:created")
        self.assertEqual(publish_mock.await_args_list[1].args[0], "offer:updated")
        self.assertEqual(publish_mock.await_args_list[1].args[1]["status"], "completed")
        self.assertEqual(self.publish_user_event_mock.await_count, 4)
        self.assertEqual(
            [call.args[0] for call in self.publish_user_event_mock.await_args_list],
            [locked_user.id, 15, offer.user_id, 19],
        )
        self.assertTrue(all(call.args[1] == "trade:created" for call in self.publish_user_event_mock.await_args_list))
        self.assertTrue(
            all(call.args[2]["recipient_specific"] for call in self.publish_user_event_mock.await_args_list)
        )
        self.assertTrue(
            all(call.args[2]["trade_number"] == 10000 for call in self.publish_user_event_mock.await_args_list)
        )
        self.assertEqual(publish_mock.await_args_list[0].args[1]["audience_user_ids"], [locked_user.id, offer.user_id, 15, 19])
        response_mock.assert_called_once_with(
            reloaded_trade,
            identity_map={},
            customer_relation_map={},
            viewer_context=context,
            history_target_user_id=locked_user.id,
        )
        self.assertEqual(result, {"id": 88, "trade_number": 10000})

    async def test_execute_trade_authoritatively_updates_retail_lots_and_tolerates_side_effect_failures(self):
        locked_user = make_user()
        context = make_context(locked_user)
        offer = make_offer(
            offer_type=OfferType.BUY,
            is_wholesale=False,
            lot_sizes=[3, 1],
            quantity=4,
            remaining_quantity=4,
        )
        reloaded_trade = SimpleNamespace(id=89)
        db = FakeDB(
            get_results=[offer],
            execute_results=[
                FakeExecuteResult(single=locked_user),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10001,
        )
        background_tasks = BackgroundTasks()

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 3, []),
        ), patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=[[locked_user.id], [offer.user_id]]),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch("sqlalchemy.orm.attributes.flag_modified") as flag_modified, patch(
            "api.routers.trades.update_channel_buttons",
            new=AsyncMock(side_effect=RuntimeError("button failure")),
        ), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(side_effect=RuntimeError("notif failure")),
        ) as notif_mock, patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ) as counter_mock, patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.trades.trade_to_response",
            return_value={"id": 89, "trade_number": 10001},
        ) as response_mock, patch("api.routers.trades.logger") as logger:
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=3),
                background_tasks,
                db=db,
                context=context,
            )

        self.assertEqual(offer.remaining_quantity, 1)
        self.assertEqual(offer.lot_sizes, [1])
        flag_modified.assert_called_once_with(offer, "lot_sizes")
        self.assertEqual(len(background_tasks.tasks), 2)
        notif_mock.assert_awaited_once()
        counter_mock.assert_awaited_once_with(db, locked_user, "trade", 3)
        self.assertEqual(publish_mock.await_count, 2)
        logger.error.assert_called_once()
        response_mock.assert_called_once_with(
            reloaded_trade,
            identity_map={},
            customer_relation_map={},
            viewer_context=context,
            history_target_user_id=locked_user.id,
        )
        self.assertEqual(result, {"id": 89, "trade_number": 10001})

    async def test_execute_trade_authoritatively_allows_full_remaining_retail_trade_and_clears_lots(self):
        locked_user = make_user()
        offer = make_offer(
            offer_type=OfferType.BUY,
            is_wholesale=False,
            lot_sizes=[20, 15, 10],
            quantity=45,
            remaining_quantity=25,
        )
        reloaded_trade = SimpleNamespace(id=90)
        db = FakeDB(
            get_results=[offer],
            execute_results=[
                FakeExecuteResult(single=locked_user),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10002,
        )

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 25, [25, 15, 10]),
        ), patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=[[locked_user.id], [offer.user_id]]),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch("sqlalchemy.orm.attributes.flag_modified") as flag_modified, patch(
            "api.routers.trades.update_channel_buttons",
            new=AsyncMock(return_value=True),
        ), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ), patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()), patch(
            "api.routers.trades.trade_to_response",
            return_value={"id": 90, "trade_number": 10003},
        ):
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=25),
                BackgroundTasks(),
                db=db,
                context=make_context(locked_user),
            )

        self.assertEqual(offer.remaining_quantity, 0)
        self.assertEqual(offer.status, OfferStatus.COMPLETED)
        self.assertIsNone(offer.lot_sizes)
        flag_modified.assert_called_once_with(offer, "lot_sizes")
        self.assertEqual(result, {"id": 90, "trade_number": 10003})

    async def test_execute_trade_authoritatively_reraises_non_stale_commit_errors(self):
        locked_user = make_user()
        offer = make_offer()
        db = FakeDB(
            get_results=[offer],
            execute_results=[FakeExecuteResult(single=locked_user), FakeExecuteResult(single_or_none=None)],
            commit_side_effect=RuntimeError("db boom"),
            scalar_result=10002,
        )

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ):
            with self.assertRaises(RuntimeError) as exc_info:
                await _execute_trade_authoritatively(
                    TradeCreate(offer_id=7, quantity=4),
                    BackgroundTasks(),
                    db=db,
                    context=make_context(locked_user),
                )

        self.assertEqual(str(exc_info.exception), "db boom")

    async def test_execute_trade_authoritatively_uses_owner_actor_id_for_standard_context(self):
        owner_user = make_user(id=5, account_name="owner", telegram_id=555)
        offer = make_offer()
        reloaded_trade = SimpleNamespace(id=91)
        db = FakeDB(
            get_results=[offer],
            execute_results=[
                FakeExecuteResult(single=owner_user),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10003,
        )

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=[[owner_user.id], [offer.user_id]]),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.update_channel_buttons", new=AsyncMock(return_value=True)), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ) as notif_mock, patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ) as counter_mock, patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.trades.trade_to_response",
            return_value={"id": 91, "trade_number": 10004},
        ):
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4),
                BackgroundTasks(),
                db=db,
                context=make_context(owner_user),
            )

        new_trade = db.added[0]
        self.assertEqual(new_trade.responder_user_id, owner_user.id)
        self.assertEqual(new_trade.actor_user_id, owner_user.id)
        self.assertEqual(notif_mock.await_count, 2)
        self.assertEqual(notif_mock.await_args_list[0].args[1], owner_user.id)
        self.assertEqual(notif_mock.await_args_list[1].args[1], offer.user_id)
        counter_mock.assert_awaited_once_with(db, owner_user, "trade", 4)
        self.assertEqual(publish_mock.await_args_list[0].args[1]["responder_user_id"], owner_user.id)
        self.assertEqual(result, {"id": 91, "trade_number": 10004})

    async def test_execute_trade_authoritatively_projects_tier2_customer_price_on_owner_offer(self):
        customer_user = make_user(id=42, account_name="tier2_customer", telegram_id=None)
        offer = make_offer(
            user_id=9,
            price=50000,
            quantity=4,
            remaining_quantity=4,
            offer_type=OfferType.BUY,
            user=SimpleNamespace(account_name="owner", mobile_number="09125555555", telegram_id=999),
        )
        reloaded_trade = SimpleNamespace(id=93)
        db = FakeDB(
            get_results=[offer],
            execute_results=[
                FakeExecuteResult(single=customer_user),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10005,
        )

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    owner_user_id=9,
                    customer_tier=CustomerTier.TIER_2,
                    commission_rate="0.5",
                )
            ),
        ), patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(
                return_value={
                    customer_user.id: SimpleNamespace(
                        owner_user_id=9,
                        customer_tier=CustomerTier.TIER_2,
                    ),
                }
            ),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=[[customer_user.id], [offer.user_id]]),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.update_channel_buttons", new=AsyncMock(return_value=True)), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ) as notif_mock, patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ) as counter_mock, patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.trades.trade_to_response",
            return_value={"id": 93, "trade_number": 10005},
        ):
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4),
                BackgroundTasks(),
                db=db,
                context=make_context(customer_user),
            )

        new_trade = db.added[0]
        self.assertEqual(new_trade.responder_user_id, customer_user.id)
        self.assertEqual(new_trade.actor_user_id, customer_user.id)
        self.assertEqual(new_trade.price, 49700)
        tier2_notification_message = notif_mock.await_args_list[0].args[2]
        owner_notification_message = notif_mock.await_args_list[1].args[2]
        self.assertIn("💰 فی: 49,700", tier2_notification_message)
        self.assertIn("📦 تعداد: 4", tier2_notification_message)
        self.assertIn(f"🔢 شماره معامله: {new_trade.trade_number}", tier2_notification_message)
        self.assertIn("🕐 زمان معامله:", tier2_notification_message)
        self.assertNotIn("|", tier2_notification_message)
        self.assertNotIn("👤 طرف معامله:", tier2_notification_message)
        self.assertIn("👤 طرف معامله: tier2_customer", owner_notification_message)
        counter_mock.assert_awaited_once_with(db, customer_user, "trade", 4)
        self.assertEqual(publish_mock.await_args_list[0].args[1]["price"], 49700)
        self.assertEqual(result, {"id": 93, "trade_number": 10005})

    async def test_execute_trade_authoritatively_creates_two_legs_for_tier2_customer_on_outsider_owner_offer(self):
        customer_user = make_user(id=42, account_name="tier2_customer", telegram_id=None)
        mediator_owner = make_user(id=77, account_name="mediator_owner", telegram_id=777)
        offer = make_offer(
            user_id=9,
            price=100000,
            quantity=4,
            remaining_quantity=4,
            offer_type=OfferType.SELL,
            user=SimpleNamespace(account_name="source_owner", mobile_number="09125555555", telegram_id=999),
        )
        reloaded_trade = SimpleNamespace(
            id=94,
            trade_number=10007,
            offer_id=None,
            trade_type=TradeType.BUY,
            commodity_id=offer.commodity_id,
            commodity=offer.commodity,
            quantity=4,
            price=100700,
            status=TradeStatus.COMPLETED,
            offer_user_id=mediator_owner.id,
            offer_user=mediator_owner,
            responder_user_id=customer_user.id,
            responder_user=customer_user,
            created_at=None,
        )
        db = FakeDB(
            get_results=[offer, mediator_owner],
            execute_results=[
                FakeExecuteResult(single=customer_user),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10005,
        )

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    owner_user_id=mediator_owner.id,
                    customer_tier=CustomerTier.TIER_2,
                    commission_rate="0.7",
                )
            ),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(return_value=[customer_user.id, mediator_owner.id, offer.user_id]),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.update_channel_buttons", new=AsyncMock(return_value=True)), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ), patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()), patch(
            "api.routers.trades.trade_to_response",
            side_effect=lambda trade, identity_map=None, customer_relation_map=None, **_kwargs: {
                "id": trade.id,
                "trade_number": trade.trade_number,
                "offer_id": trade.offer_id,
                "price": trade.price,
            },
        ):
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4),
                BackgroundTasks(),
                db=db,
                context=make_context(customer_user),
            )

        self.assertEqual(len(db.added), 2)
        raw_leg, customer_leg = db.added
        self.assertEqual(raw_leg.offer_user_id, offer.user_id)
        self.assertEqual(raw_leg.responder_user_id, mediator_owner.id)
        self.assertEqual(raw_leg.offer_id, offer.id)
        self.assertEqual(raw_leg.price, 100000)
        self.assertEqual(customer_leg.offer_user_id, mediator_owner.id)
        self.assertEqual(customer_leg.responder_user_id, customer_user.id)
        self.assertIsNone(customer_leg.offer_id)
        self.assertEqual(customer_leg.price, 100700)
        self.assertEqual(result, {"id": 94, "trade_number": 10007, "offer_id": None, "price": 100700})

    async def test_execute_trade_authoritatively_creates_two_legs_for_tier2_customer_on_outsider_owner_buy_offer(self):
        customer_user = make_user(id=52, account_name="tier2_customer_buy", telegram_id=None)
        mediator_owner = make_user(id=78, account_name="mediator_owner_buy", telegram_id=778)
        offer = make_offer(
            user_id=11,
            price=50000,
            quantity=4,
            remaining_quantity=4,
            offer_type=OfferType.BUY,
            user=SimpleNamespace(account_name="source_buyer", mobile_number="09126666666", telegram_id=911),
        )
        reloaded_trade = SimpleNamespace(
            id=95,
            trade_number=10008,
            offer_id=None,
            trade_type=TradeType.SELL,
            commodity_id=offer.commodity_id,
            commodity=offer.commodity,
            quantity=4,
            price=49700,
            status=TradeStatus.COMPLETED,
            offer_user_id=mediator_owner.id,
            offer_user=mediator_owner,
            responder_user_id=customer_user.id,
            responder_user=customer_user,
            created_at=None,
        )
        db = FakeDB(
            get_results=[offer, mediator_owner],
            execute_results=[
                FakeExecuteResult(single=customer_user),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10006,
        )

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    owner_user_id=mediator_owner.id,
                    customer_tier=CustomerTier.TIER_2,
                    commission_rate="0.5",
                )
            ),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(return_value=[customer_user.id, mediator_owner.id, offer.user_id]),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.update_channel_buttons", new=AsyncMock(return_value=True)), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ), patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()), patch(
            "api.routers.trades.trade_to_response",
            side_effect=lambda trade, identity_map=None, customer_relation_map=None, **_kwargs: {
                "id": trade.id,
                "trade_number": trade.trade_number,
                "offer_id": trade.offer_id,
                "price": trade.price,
            },
        ):
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4),
                BackgroundTasks(),
                db=db,
                context=make_context(customer_user),
            )

        self.assertEqual(len(db.added), 2)
        raw_leg, customer_leg = db.added
        self.assertEqual(raw_leg.offer_user_id, offer.user_id)
        self.assertEqual(raw_leg.responder_user_id, mediator_owner.id)
        self.assertEqual(raw_leg.offer_id, offer.id)
        self.assertEqual(raw_leg.price, 50000)
        self.assertEqual(raw_leg.trade_type, TradeType.SELL)
        self.assertEqual(customer_leg.offer_user_id, mediator_owner.id)
        self.assertEqual(customer_leg.responder_user_id, customer_user.id)
        self.assertIsNone(customer_leg.offer_id)
        self.assertEqual(customer_leg.price, 49700)
        self.assertEqual(customer_leg.trade_type, TradeType.SELL)
        self.assertEqual(result, {"id": 95, "trade_number": 10008, "offer_id": None, "price": 49700})

    async def test_execute_trade_authoritatively_creates_two_legs_for_tier1_source_and_same_owner_tier2_responder(self):
        customer_user = make_user(id=52, account_name="tier2_customer_same_owner", telegram_id=None)
        source_customer = make_user(id=41, account_name="tier1_source_same_owner", telegram_id=None)
        shared_owner = make_user(id=78, account_name="shared_owner", telegram_id=778)
        offer = make_offer(
            user_id=source_customer.id,
            price=50000,
            quantity=4,
            remaining_quantity=4,
            offer_type=OfferType.SELL,
            user=SimpleNamespace(
                account_name=source_customer.account_name,
                mobile_number="09127777777",
                telegram_id=941,
            ),
        )
        reloaded_trade = SimpleNamespace(
            id=96,
            trade_number=10009,
            offer_id=None,
            trade_type=TradeType.BUY,
            commodity_id=offer.commodity_id,
            commodity=offer.commodity,
            quantity=4,
            price=50300,
            status=TradeStatus.COMPLETED,
            offer_user_id=shared_owner.id,
            offer_user=shared_owner,
            responder_user_id=customer_user.id,
            responder_user=customer_user,
            created_at=None,
        )
        db = FakeDB(
            get_results=[offer, shared_owner],
            execute_results=[
                FakeExecuteResult(single=customer_user),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10007,
        )

        async def customer_relation_lookup(_db, user_id):
            if user_id == customer_user.id:
                return SimpleNamespace(
                    owner_user_id=shared_owner.id,
                    customer_tier=CustomerTier.TIER_2,
                    commission_rate="0.5",
                )
            if user_id == source_customer.id:
                return SimpleNamespace(
                    owner_user_id=shared_owner.id,
                    customer_tier=CustomerTier.TIER_1,
                    commission_rate=None,
                )
            return None

        participant_relation_map = {
            customer_user.id: SimpleNamespace(
                owner_user_id=shared_owner.id,
                customer_tier=CustomerTier.TIER_2,
            ),
            source_customer.id: SimpleNamespace(
                owner_user_id=shared_owner.id,
                customer_tier=CustomerTier.TIER_1,
            ),
        }

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=customer_relation_lookup),
        ), patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value=participant_relation_map),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=[[customer_user.id], [shared_owner.id], [shared_owner.id], [source_customer.id]]),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.update_channel_buttons", new=AsyncMock(return_value=True)), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ) as notif_mock, patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.trades.trade_to_response",
            side_effect=lambda trade, identity_map=None, customer_relation_map=None, **_kwargs: {
                "id": trade.id,
                "trade_number": trade.trade_number,
                "offer_id": trade.offer_id,
                "price": trade.price,
                "trade_path_summary": (customer_relation_map or {}).get(trade.responder_user_id) and "مالک ↔ مشتری سطح ۲" or None,
            },
        ):
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4),
                BackgroundTasks(),
                db=db,
                context=make_context(customer_user),
            )

        self.assertEqual(len(db.added), 2)
        source_leg, customer_leg = db.added
        self.assertEqual(source_leg.offer_user_id, source_customer.id)
        self.assertEqual(source_leg.responder_user_id, shared_owner.id)
        self.assertEqual(source_leg.offer_id, offer.id)
        self.assertEqual(source_leg.price, 50000)
        self.assertEqual(source_leg.trade_type, TradeType.BUY)
        self.assertEqual(customer_leg.offer_user_id, shared_owner.id)
        self.assertEqual(customer_leg.responder_user_id, customer_user.id)
        self.assertIsNone(customer_leg.offer_id)
        self.assertEqual(customer_leg.price, 50300)
        self.assertEqual(customer_leg.trade_type, TradeType.BUY)
        self.assertEqual(result["price"], 50300)

        notification_messages = [call.args[2] for call in notif_mock.await_args_list]
        self.assertTrue(any("🧭 مسیر: مالک ↔ مشتری سطح ۲" in message for message in notification_messages))
        self.assertTrue(any("🧭 مسیر: مالک ↔ مشتری سطح ۱" in message for message in notification_messages))

        trade_payloads = [call.args[1] for call in publish_mock.await_args_list if call.args[0] == "trade:created"]
        self.assertEqual(len(trade_payloads), 2)
        self.assertEqual({payload["trade_path_summary"] for payload in trade_payloads}, {"مالک ↔ مشتری سطح ۱", "مالک ↔ مشتری سطح ۲"})

    async def test_execute_trade_authoritatively_creates_three_legs_for_tier1_source_and_other_owner_tier2_sell_offer(self):
        responder_customer = make_user(id=52, account_name="tier2_customer_other_owner_sell", telegram_id=None)
        source_customer = make_user(id=41, account_name="tier1_source_other_owner_sell", telegram_id=None)
        responder_owner = make_user(id=78, account_name="responder_owner_sell", telegram_id=778)
        source_owner = make_user(id=79, account_name="source_owner_sell", telegram_id=779)
        offer = make_offer(
            user_id=source_customer.id,
            price=50000,
            quantity=4,
            remaining_quantity=4,
            offer_type=OfferType.SELL,
            user=SimpleNamespace(
                account_name=source_customer.account_name,
                mobile_number="09127777777",
                telegram_id=941,
            ),
        )
        reloaded_trade = SimpleNamespace(
            id=97,
            trade_number=10010,
            offer_id=None,
            trade_type=TradeType.BUY,
            commodity_id=offer.commodity_id,
            commodity=offer.commodity,
            quantity=4,
            price=50300,
            status=TradeStatus.COMPLETED,
            offer_user_id=responder_owner.id,
            offer_user=responder_owner,
            responder_user_id=responder_customer.id,
            responder_user=responder_customer,
            created_at=None,
        )
        db = FakeDB(
            get_results=[offer, responder_owner, source_owner],
            execute_results=[
                FakeExecuteResult(single=responder_customer),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10007,
        )

        async def customer_relation_lookup(_db, user_id):
            if user_id == responder_customer.id:
                return SimpleNamespace(
                    owner_user_id=responder_owner.id,
                    customer_tier=CustomerTier.TIER_2,
                    commission_rate="0.5",
                )
            if user_id == source_customer.id:
                return SimpleNamespace(
                    owner_user_id=source_owner.id,
                    customer_tier=CustomerTier.TIER_1,
                    commission_rate=None,
                )
            return None

        participant_relation_map = {
            responder_customer.id: SimpleNamespace(
                owner_user_id=responder_owner.id,
                customer_tier=CustomerTier.TIER_2,
            ),
            source_customer.id: SimpleNamespace(
                owner_user_id=source_owner.id,
                customer_tier=CustomerTier.TIER_1,
            ),
        }

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=customer_relation_lookup),
        ), patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value=participant_relation_map),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=[[responder_customer.id], [responder_owner.id], [source_owner.id], [source_customer.id], [source_owner.id]]),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.update_channel_buttons", new=AsyncMock(return_value=True)), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ) as notif_mock, patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.trades.trade_to_response",
            side_effect=lambda trade, identity_map=None, customer_relation_map=None, **_kwargs: {
                "id": trade.id,
                "trade_number": trade.trade_number,
                "offer_id": trade.offer_id,
                "price": trade.price,
            },
        ):
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4),
                BackgroundTasks(),
                db=db,
                context=make_context(responder_customer),
            )

        self.assertEqual(len(db.added), 3)
        source_leg, bridge_leg, customer_leg = db.added
        self.assertEqual(source_leg.offer_user_id, source_customer.id)
        self.assertEqual(source_leg.responder_user_id, source_owner.id)
        self.assertEqual(source_leg.offer_id, offer.id)
        self.assertEqual(source_leg.price, 50000)
        self.assertEqual(source_leg.trade_type, TradeType.BUY)
        self.assertEqual(bridge_leg.offer_user_id, source_owner.id)
        self.assertEqual(bridge_leg.responder_user_id, responder_owner.id)
        self.assertIsNone(bridge_leg.offer_id)
        self.assertEqual(bridge_leg.price, 50000)
        self.assertEqual(bridge_leg.trade_type, TradeType.BUY)
        self.assertEqual(customer_leg.offer_user_id, responder_owner.id)
        self.assertEqual(customer_leg.responder_user_id, responder_customer.id)
        self.assertIsNone(customer_leg.offer_id)
        self.assertEqual(customer_leg.price, 50300)
        self.assertEqual(customer_leg.trade_type, TradeType.BUY)
        self.assertEqual(result, {"id": 97, "trade_number": 10010, "offer_id": None, "price": 50300})

        notification_messages = [call.args[2] for call in notif_mock.await_args_list]
        self.assertTrue(any("🧭 مسیر: مالک ↔ مشتری سطح ۲" in message for message in notification_messages))
        self.assertTrue(any("🧭 مسیر: مالک ↔ مشتری سطح ۱" in message for message in notification_messages))

        trade_payloads = [call.args[1] for call in publish_mock.await_args_list if call.args[0] == "trade:created"]
        self.assertEqual(len(trade_payloads), 3)
        self.assertEqual(
            {payload.get("trade_path_summary") for payload in trade_payloads},
            {None, "مالک ↔ مشتری سطح ۱", "مالک ↔ مشتری سطح ۲"},
        )

    async def test_execute_trade_authoritatively_creates_three_legs_for_tier1_source_and_other_owner_tier2_buy_offer(self):
        responder_customer = make_user(id=62, account_name="tier2_customer_other_owner_buy", telegram_id=None)
        source_customer = make_user(id=51, account_name="tier1_source_other_owner_buy", telegram_id=None)
        responder_owner = make_user(id=88, account_name="responder_owner_buy", telegram_id=888)
        source_owner = make_user(id=89, account_name="source_owner_buy", telegram_id=889)
        offer = make_offer(
            user_id=source_customer.id,
            price=100000,
            quantity=4,
            remaining_quantity=4,
            offer_type=OfferType.BUY,
            user=SimpleNamespace(
                account_name=source_customer.account_name,
                mobile_number="09128888888",
                telegram_id=951,
            ),
        )
        reloaded_trade = SimpleNamespace(
            id=98,
            trade_number=10011,
            offer_id=None,
            trade_type=TradeType.SELL,
            commodity_id=offer.commodity_id,
            commodity=offer.commodity,
            quantity=4,
            price=99300,
            status=TradeStatus.COMPLETED,
            offer_user_id=responder_owner.id,
            offer_user=responder_owner,
            responder_user_id=responder_customer.id,
            responder_user=responder_customer,
            created_at=None,
        )
        db = FakeDB(
            get_results=[offer, responder_owner, source_owner],
            execute_results=[
                FakeExecuteResult(single=responder_customer),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10008,
        )

        async def customer_relation_lookup(_db, user_id):
            if user_id == responder_customer.id:
                return SimpleNamespace(
                    owner_user_id=responder_owner.id,
                    customer_tier=CustomerTier.TIER_2,
                    commission_rate="0.7",
                )
            if user_id == source_customer.id:
                return SimpleNamespace(
                    owner_user_id=source_owner.id,
                    customer_tier=CustomerTier.TIER_1,
                    commission_rate=None,
                )
            return None

        participant_relation_map = {
            responder_customer.id: SimpleNamespace(
                owner_user_id=responder_owner.id,
                customer_tier=CustomerTier.TIER_2,
            ),
            source_customer.id: SimpleNamespace(
                owner_user_id=source_owner.id,
                customer_tier=CustomerTier.TIER_1,
            ),
        }

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=customer_relation_lookup),
        ), patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value=participant_relation_map),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=[[responder_customer.id], [responder_owner.id], [source_owner.id], [source_customer.id], [source_owner.id]]),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.update_channel_buttons", new=AsyncMock(return_value=True)), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ) as notif_mock, patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.trades.trade_to_response",
            side_effect=lambda trade, identity_map=None, customer_relation_map=None, **_kwargs: {
                "id": trade.id,
                "trade_number": trade.trade_number,
                "offer_id": trade.offer_id,
                "price": trade.price,
            },
        ):
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4),
                BackgroundTasks(),
                db=db,
                context=make_context(responder_customer),
            )

        self.assertEqual(len(db.added), 3)
        source_leg, bridge_leg, customer_leg = db.added
        self.assertEqual(source_leg.offer_user_id, source_customer.id)
        self.assertEqual(source_leg.responder_user_id, source_owner.id)
        self.assertEqual(source_leg.offer_id, offer.id)
        self.assertEqual(source_leg.price, 100000)
        self.assertEqual(source_leg.trade_type, TradeType.SELL)
        self.assertEqual(bridge_leg.offer_user_id, source_owner.id)
        self.assertEqual(bridge_leg.responder_user_id, responder_owner.id)
        self.assertIsNone(bridge_leg.offer_id)
        self.assertEqual(bridge_leg.price, 100000)
        self.assertEqual(bridge_leg.trade_type, TradeType.SELL)
        self.assertEqual(customer_leg.offer_user_id, responder_owner.id)
        self.assertEqual(customer_leg.responder_user_id, responder_customer.id)
        self.assertIsNone(customer_leg.offer_id)
        self.assertEqual(customer_leg.price, 99300)
        self.assertEqual(customer_leg.trade_type, TradeType.SELL)
        self.assertEqual(result, {"id": 98, "trade_number": 10011, "offer_id": None, "price": 99300})

        notification_messages = [call.args[2] for call in notif_mock.await_args_list]
        self.assertTrue(any("🧭 مسیر: مالک ↔ مشتری سطح ۲" in message for message in notification_messages))
        self.assertTrue(any("🧭 مسیر: مالک ↔ مشتری سطح ۱" in message for message in notification_messages))

        trade_payloads = [call.args[1] for call in publish_mock.await_args_list if call.args[0] == "trade:created"]
        self.assertEqual(len(trade_payloads), 3)
        self.assertEqual(
            {payload.get("trade_path_summary") for payload in trade_payloads},
            {None, "مالک ↔ مشتری سطح ۱", "مالک ↔ مشتری سطح ۲"},
        )

    async def test_execute_trade_authoritatively_creates_two_legs_for_owner_source_and_other_owner_tier1_responder(self):
        responder_customer = make_user(id=72, account_name="tier1_customer_other_owner", telegram_id=None)
        responder_owner = make_user(id=82, account_name="responder_owner_tier1", telegram_id=882)
        source_owner = make_user(id=83, account_name="source_owner_direct", telegram_id=883)
        offer = make_offer(
            user_id=source_owner.id,
            price=100000,
            quantity=4,
            remaining_quantity=4,
            offer_type=OfferType.SELL,
            user=source_owner,
        )
        reloaded_trade = SimpleNamespace(
            id=99,
            trade_number=10012,
            offer_id=None,
            trade_type=TradeType.BUY,
            commodity_id=offer.commodity_id,
            commodity=offer.commodity,
            quantity=4,
            price=100000,
            status=TradeStatus.COMPLETED,
            offer_user_id=responder_owner.id,
            offer_user=responder_owner,
            responder_user_id=responder_customer.id,
            responder_user=responder_customer,
            created_at=None,
        )
        db = FakeDB(
            get_results=[offer, responder_owner],
            execute_results=[
                FakeExecuteResult(single=responder_customer),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10009,
        )

        async def customer_relation_lookup(_db, user_id):
            if user_id == responder_customer.id:
                return SimpleNamespace(
                    owner_user_id=responder_owner.id,
                    customer_tier=CustomerTier.TIER_1,
                    commission_rate=None,
                )
            return None

        participant_relation_map = {
            responder_customer.id: SimpleNamespace(
                owner_user_id=responder_owner.id,
                customer_tier=CustomerTier.TIER_1,
            ),
        }

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=customer_relation_lookup),
        ), patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value=participant_relation_map),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=[[responder_customer.id], [responder_owner.id], [source_owner.id]]),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.update_channel_buttons", new=AsyncMock(return_value=True)), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ) as notif_mock, patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.trades.trade_to_response",
            side_effect=lambda trade, identity_map=None, customer_relation_map=None, **_kwargs: {
                "id": trade.id,
                "trade_number": trade.trade_number,
                "offer_id": trade.offer_id,
                "price": trade.price,
            },
        ):
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4),
                BackgroundTasks(),
                db=db,
                context=make_context(responder_customer),
            )

        self.assertEqual(len(db.added), 2)
        owner_leg, customer_leg = db.added
        self.assertEqual(owner_leg.offer_user_id, source_owner.id)
        self.assertEqual(owner_leg.responder_user_id, responder_owner.id)
        self.assertEqual(owner_leg.offer_id, offer.id)
        self.assertEqual(owner_leg.price, 100000)
        self.assertEqual(owner_leg.trade_type, TradeType.BUY)
        self.assertEqual(customer_leg.offer_user_id, responder_owner.id)
        self.assertEqual(customer_leg.responder_user_id, responder_customer.id)
        self.assertIsNone(customer_leg.offer_id)
        self.assertEqual(customer_leg.price, 100000)
        self.assertEqual(customer_leg.trade_type, TradeType.BUY)
        self.assertEqual(result, {"id": 99, "trade_number": 10012, "offer_id": None, "price": 100000})

        notification_messages = [call.args[2] for call in notif_mock.await_args_list]
        self.assertTrue(any("🧭 مسیر: مالک ↔ مشتری سطح ۱" in message for message in notification_messages))

        trade_payloads = [call.args[1] for call in publish_mock.await_args_list if call.args[0] == "trade:created"]
        self.assertEqual(len(trade_payloads), 2)
        self.assertEqual(
            {payload.get("trade_path_summary") for payload in trade_payloads},
            {None, "مالک ↔ مشتری سطح ۱"},
        )

    async def test_execute_trade_authoritatively_creates_two_legs_for_tier1_source_and_other_owner_responder(self):
        responder_owner = make_user(id=92, account_name="responder_owner_direct", telegram_id=992)
        source_customer = make_user(id=53, account_name="tier1_source_other_owner_direct", telegram_id=None)
        source_owner = make_user(id=93, account_name="source_owner_bridge", telegram_id=993)
        offer = make_offer(
            user_id=source_customer.id,
            price=50000,
            quantity=4,
            remaining_quantity=4,
            offer_type=OfferType.SELL,
            user=SimpleNamespace(
                account_name=source_customer.account_name,
                mobile_number="09129999991",
                telegram_id=953,
            ),
        )
        reloaded_trade = SimpleNamespace(
            id=100,
            trade_number=10013,
            offer_id=None,
            trade_type=TradeType.BUY,
            commodity_id=offer.commodity_id,
            commodity=offer.commodity,
            quantity=4,
            price=50000,
            status=TradeStatus.COMPLETED,
            offer_user_id=source_owner.id,
            offer_user=source_owner,
            responder_user_id=responder_owner.id,
            responder_user=responder_owner,
            created_at=None,
        )
        db = FakeDB(
            get_results=[offer, source_owner],
            execute_results=[
                FakeExecuteResult(single=responder_owner),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10010,
        )

        async def customer_relation_lookup(_db, user_id):
            if user_id == source_customer.id:
                return SimpleNamespace(
                    owner_user_id=source_owner.id,
                    customer_tier=CustomerTier.TIER_1,
                    commission_rate=None,
                )
            return None

        participant_relation_map = {
            source_customer.id: SimpleNamespace(
                owner_user_id=source_owner.id,
                customer_tier=CustomerTier.TIER_1,
            ),
        }

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=customer_relation_lookup),
        ), patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value=participant_relation_map),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=[[responder_owner.id], [source_owner.id], [source_owner.id], [source_customer.id]]),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.update_channel_buttons", new=AsyncMock(return_value=True)), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ) as notif_mock, patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.trades.trade_to_response",
            side_effect=lambda trade, identity_map=None, customer_relation_map=None, **_kwargs: {
                "id": trade.id,
                "trade_number": trade.trade_number,
                "offer_id": trade.offer_id,
                "price": trade.price,
            },
        ):
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4),
                BackgroundTasks(),
                db=db,
                context=make_context(responder_owner),
            )

        self.assertEqual(len(db.added), 2)
        source_leg, owner_leg = db.added
        self.assertEqual(source_leg.offer_user_id, source_customer.id)
        self.assertEqual(source_leg.responder_user_id, source_owner.id)
        self.assertEqual(source_leg.offer_id, offer.id)
        self.assertEqual(source_leg.price, 50000)
        self.assertEqual(source_leg.trade_type, TradeType.BUY)
        self.assertEqual(owner_leg.offer_user_id, source_owner.id)
        self.assertEqual(owner_leg.responder_user_id, responder_owner.id)
        self.assertIsNone(owner_leg.offer_id)
        self.assertEqual(owner_leg.price, 50000)
        self.assertEqual(owner_leg.trade_type, TradeType.BUY)
        self.assertEqual(result, {"id": 100, "trade_number": 10013, "offer_id": None, "price": 50000})

        notification_messages = [call.args[2] for call in notif_mock.await_args_list]
        self.assertTrue(any("🧭 مسیر: مالک ↔ مشتری سطح ۱" in message for message in notification_messages))

        trade_payloads = [call.args[1] for call in publish_mock.await_args_list if call.args[0] == "trade:created"]
        self.assertEqual(len(trade_payloads), 2)
        self.assertEqual(
            {payload.get("trade_path_summary") for payload in trade_payloads},
            {None, "مالک ↔ مشتری سطح ۱"},
        )

    async def test_execute_trade_authoritatively_creates_two_legs_for_tier1_source_and_same_owner_tier1_responder(self):
        responder_customer = make_user(id=73, account_name="tier1_same_owner_responder", telegram_id=None)
        source_customer = make_user(id=74, account_name="tier1_same_owner_source", telegram_id=None)
        shared_owner = make_user(id=94, account_name="shared_owner_tier1", telegram_id=994)
        offer = make_offer(
            user_id=source_customer.id,
            price=100000,
            quantity=4,
            remaining_quantity=4,
            offer_type=OfferType.SELL,
            user=SimpleNamespace(
                account_name=source_customer.account_name,
                mobile_number="09129999992",
                telegram_id=954,
            ),
        )
        reloaded_trade = SimpleNamespace(
            id=101,
            trade_number=10014,
            offer_id=None,
            trade_type=TradeType.BUY,
            commodity_id=offer.commodity_id,
            commodity=offer.commodity,
            quantity=4,
            price=100000,
            status=TradeStatus.COMPLETED,
            offer_user_id=shared_owner.id,
            offer_user=shared_owner,
            responder_user_id=responder_customer.id,
            responder_user=responder_customer,
            created_at=None,
        )
        db = FakeDB(
            get_results=[offer, shared_owner],
            execute_results=[
                FakeExecuteResult(single=responder_customer),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10011,
        )

        async def customer_relation_lookup(_db, user_id):
            if user_id == responder_customer.id:
                return SimpleNamespace(
                    owner_user_id=shared_owner.id,
                    customer_tier=CustomerTier.TIER_1,
                    commission_rate=None,
                )
            if user_id == source_customer.id:
                return SimpleNamespace(
                    owner_user_id=shared_owner.id,
                    customer_tier=CustomerTier.TIER_1,
                    commission_rate=None,
                )
            return None

        participant_relation_map = {
            responder_customer.id: SimpleNamespace(
                owner_user_id=shared_owner.id,
                customer_tier=CustomerTier.TIER_1,
            ),
            source_customer.id: SimpleNamespace(
                owner_user_id=shared_owner.id,
                customer_tier=CustomerTier.TIER_1,
            ),
        }

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=customer_relation_lookup),
        ), patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value=participant_relation_map),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=[[responder_customer.id], [shared_owner.id], [source_customer.id]]),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.update_channel_buttons", new=AsyncMock(return_value=True)), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ) as notif_mock, patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.trades.trade_to_response",
            side_effect=lambda trade, identity_map=None, customer_relation_map=None, **_kwargs: {
                "id": trade.id,
                "trade_number": trade.trade_number,
                "offer_id": trade.offer_id,
                "price": trade.price,
            },
        ):
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4),
                BackgroundTasks(),
                db=db,
                context=make_context(responder_customer),
            )

        self.assertEqual(len(db.added), 2)
        source_leg, customer_leg = db.added
        self.assertEqual(source_leg.offer_user_id, source_customer.id)
        self.assertEqual(source_leg.responder_user_id, shared_owner.id)
        self.assertEqual(source_leg.offer_id, offer.id)
        self.assertEqual(source_leg.price, 100000)
        self.assertEqual(source_leg.trade_type, TradeType.BUY)
        self.assertEqual(customer_leg.offer_user_id, shared_owner.id)
        self.assertEqual(customer_leg.responder_user_id, responder_customer.id)
        self.assertIsNone(customer_leg.offer_id)
        self.assertEqual(customer_leg.price, 100000)
        self.assertEqual(customer_leg.trade_type, TradeType.BUY)
        self.assertEqual(result, {"id": 101, "trade_number": 10014, "offer_id": None, "price": 100000})

        notification_messages = [call.args[2] for call in notif_mock.await_args_list]
        self.assertTrue(any("🧭 مسیر: مالک ↔ مشتری سطح ۱" in message for message in notification_messages))

        trade_payloads = [call.args[1] for call in publish_mock.await_args_list if call.args[0] == "trade:created"]
        self.assertEqual(len(trade_payloads), 2)
        self.assertEqual({payload.get("trade_path_summary") for payload in trade_payloads}, {"مالک ↔ مشتری سطح ۱"})

    async def test_execute_trade_authoritatively_creates_three_legs_for_tier1_source_and_other_owner_tier1_responder(self):
        responder_customer = make_user(id=75, account_name="tier1_other_owner_responder", telegram_id=None)
        source_customer = make_user(id=76, account_name="tier1_other_owner_source", telegram_id=None)
        responder_owner = make_user(id=95, account_name="responder_owner_tier1_chain", telegram_id=995)
        source_owner = make_user(id=96, account_name="source_owner_tier1_chain", telegram_id=996)
        offer = make_offer(
            user_id=source_customer.id,
            price=200000,
            quantity=4,
            remaining_quantity=4,
            offer_type=OfferType.SELL,
            user=SimpleNamespace(
                account_name=source_customer.account_name,
                mobile_number="09129999993",
                telegram_id=955,
            ),
        )
        reloaded_trade = SimpleNamespace(
            id=102,
            trade_number=10015,
            offer_id=None,
            trade_type=TradeType.BUY,
            commodity_id=offer.commodity_id,
            commodity=offer.commodity,
            quantity=4,
            price=200000,
            status=TradeStatus.COMPLETED,
            offer_user_id=responder_owner.id,
            offer_user=responder_owner,
            responder_user_id=responder_customer.id,
            responder_user=responder_customer,
            created_at=None,
        )
        db = FakeDB(
            get_results=[offer, source_owner, responder_owner],
            execute_results=[
                FakeExecuteResult(single=responder_customer),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10012,
        )

        async def customer_relation_lookup(_db, user_id):
            if user_id == responder_customer.id:
                return SimpleNamespace(
                    owner_user_id=responder_owner.id,
                    customer_tier=CustomerTier.TIER_1,
                    commission_rate=None,
                )
            if user_id == source_customer.id:
                return SimpleNamespace(
                    owner_user_id=source_owner.id,
                    customer_tier=CustomerTier.TIER_1,
                    commission_rate=None,
                )
            return None

        participant_relation_map = {
            responder_customer.id: SimpleNamespace(
                owner_user_id=responder_owner.id,
                customer_tier=CustomerTier.TIER_1,
            ),
            source_customer.id: SimpleNamespace(
                owner_user_id=source_owner.id,
                customer_tier=CustomerTier.TIER_1,
            ),
        }

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=customer_relation_lookup),
        ), patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value=participant_relation_map),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=[[responder_customer.id], [responder_owner.id], [source_owner.id], [source_customer.id], [source_owner.id]]),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.update_channel_buttons", new=AsyncMock(return_value=True)), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ) as notif_mock, patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.trades.trade_to_response",
            side_effect=lambda trade, identity_map=None, customer_relation_map=None, **_kwargs: {
                "id": trade.id,
                "trade_number": trade.trade_number,
                "offer_id": trade.offer_id,
                "price": trade.price,
            },
        ):
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4),
                BackgroundTasks(),
                db=db,
                context=make_context(responder_customer),
            )

        self.assertEqual(len(db.added), 3)
        source_leg, bridge_leg, customer_leg = db.added
        self.assertEqual(source_leg.offer_user_id, source_customer.id)
        self.assertEqual(source_leg.responder_user_id, source_owner.id)
        self.assertEqual(source_leg.offer_id, offer.id)
        self.assertEqual(source_leg.price, 200000)
        self.assertEqual(source_leg.trade_type, TradeType.BUY)
        self.assertEqual(bridge_leg.offer_user_id, source_owner.id)
        self.assertEqual(bridge_leg.responder_user_id, responder_owner.id)
        self.assertIsNone(bridge_leg.offer_id)
        self.assertEqual(bridge_leg.price, 200000)
        self.assertEqual(bridge_leg.trade_type, TradeType.BUY)
        self.assertEqual(customer_leg.offer_user_id, responder_owner.id)
        self.assertEqual(customer_leg.responder_user_id, responder_customer.id)
        self.assertIsNone(customer_leg.offer_id)
        self.assertEqual(customer_leg.price, 200000)
        self.assertEqual(customer_leg.trade_type, TradeType.BUY)
        self.assertEqual(result, {"id": 102, "trade_number": 10015, "offer_id": None, "price": 200000})

        notification_messages = [call.args[2] for call in notif_mock.await_args_list]
        self.assertTrue(any("🧭 مسیر: مالک ↔ مشتری سطح ۱" in message for message in notification_messages))

        trade_payloads = [call.args[1] for call in publish_mock.await_args_list if call.args[0] == "trade:created"]
        self.assertEqual(len(trade_payloads), 3)
        self.assertEqual(
            {payload.get("trade_path_summary") for payload in trade_payloads},
            {None, "مالک ↔ مشتری سطح ۱"},
        )

    async def test_execute_trade_authoritatively_keeps_owner_to_own_tier1_as_single_direct_leg(self):
        responder_customer = make_user(id=77, account_name="tier1_own_customer", telegram_id=None)
        source_owner = make_user(id=97, account_name="owner_source_tier1", telegram_id=997)
        offer = make_offer(
            user_id=source_owner.id,
            price=188000,
            quantity=4,
            remaining_quantity=4,
            offer_type=OfferType.SELL,
            user=source_owner,
        )
        reloaded_trade = SimpleNamespace(
            id=103,
            trade_number=10016,
            offer_id=offer.id,
            trade_type=TradeType.BUY,
            commodity_id=offer.commodity_id,
            commodity=offer.commodity,
            quantity=4,
            price=188000,
            status=TradeStatus.COMPLETED,
            offer_user_id=source_owner.id,
            offer_user=source_owner,
            responder_user_id=responder_customer.id,
            responder_user=responder_customer,
            created_at=None,
        )
        db = FakeDB(
            get_results=[offer],
            execute_results=[
                FakeExecuteResult(single=responder_customer),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10013,
        )

        async def customer_relation_lookup(_db, user_id):
            if user_id == responder_customer.id:
                return SimpleNamespace(
                    owner_user_id=source_owner.id,
                    customer_tier=CustomerTier.TIER_1,
                    commission_rate=None,
                )
            return None

        participant_relation_map = {
            responder_customer.id: SimpleNamespace(
                owner_user_id=source_owner.id,
                customer_tier=CustomerTier.TIER_1,
            ),
        }

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=customer_relation_lookup),
        ), patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value=participant_relation_map),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=[[responder_customer.id], [source_owner.id]]),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.update_channel_buttons", new=AsyncMock(return_value=True)), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ), patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.trades.trade_to_response",
            side_effect=lambda trade, identity_map=None, customer_relation_map=None, **_kwargs: {
                "id": trade.id,
                "trade_number": trade.trade_number,
                "offer_id": trade.offer_id,
                "price": trade.price,
            },
        ):
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4),
                BackgroundTasks(),
                db=db,
                context=make_context(responder_customer),
            )

        self.assertEqual(len(db.added), 1)
        direct_trade = db.added[0]
        self.assertEqual(direct_trade.offer_user_id, source_owner.id)
        self.assertEqual(direct_trade.responder_user_id, responder_customer.id)
        self.assertEqual(direct_trade.offer_id, offer.id)
        self.assertEqual(direct_trade.price, 188000)
        self.assertEqual(direct_trade.trade_type, TradeType.BUY)
        self.assertEqual(result, {"id": 103, "trade_number": 10016, "offer_id": offer.id, "price": 188000})

        trade_payload = next(call.args[1] for call in publish_mock.await_args_list if call.args[0] == "trade:created")
        self.assertEqual(trade_payload.get("trade_path_summary"), "مالک ↔ مشتری سطح ۱")

    async def test_execute_trade_authoritatively_keeps_tier1_to_own_owner_as_single_direct_leg(self):
        responder_owner = make_user(id=98, account_name="owner_responder_tier1", telegram_id=998)
        source_customer = make_user(id=78, account_name="tier1_source_own_owner", telegram_id=None)
        offer = make_offer(
            user_id=source_customer.id,
            price=200000,
            quantity=4,
            remaining_quantity=4,
            offer_type=OfferType.SELL,
            user=SimpleNamespace(
                account_name=source_customer.account_name,
                mobile_number="09129999994",
                telegram_id=956,
            ),
        )
        reloaded_trade = SimpleNamespace(
            id=104,
            trade_number=10017,
            offer_id=offer.id,
            trade_type=TradeType.BUY,
            commodity_id=offer.commodity_id,
            commodity=offer.commodity,
            quantity=4,
            price=200000,
            status=TradeStatus.COMPLETED,
            offer_user_id=source_customer.id,
            offer_user=source_customer,
            responder_user_id=responder_owner.id,
            responder_user=responder_owner,
            created_at=None,
        )
        db = FakeDB(
            get_results=[offer, responder_owner],
            execute_results=[
                FakeExecuteResult(single=responder_owner),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10014,
        )

        async def customer_relation_lookup(_db, user_id):
            if user_id == source_customer.id:
                return SimpleNamespace(
                    owner_user_id=responder_owner.id,
                    customer_tier=CustomerTier.TIER_1,
                    commission_rate=None,
                )
            return None

        participant_relation_map = {
            source_customer.id: SimpleNamespace(
                owner_user_id=responder_owner.id,
                customer_tier=CustomerTier.TIER_1,
            ),
        }

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=customer_relation_lookup),
        ), patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value=participant_relation_map),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=[[responder_owner.id], [source_customer.id]]),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.update_channel_buttons", new=AsyncMock(return_value=True)), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ), patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.trades.trade_to_response",
            side_effect=lambda trade, identity_map=None, customer_relation_map=None, **_kwargs: {
                "id": trade.id,
                "trade_number": trade.trade_number,
                "offer_id": trade.offer_id,
                "price": trade.price,
            },
        ):
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4),
                BackgroundTasks(),
                db=db,
                context=make_context(responder_owner),
            )

        self.assertEqual(len(db.added), 1)
        direct_trade = db.added[0]
        self.assertEqual(direct_trade.offer_user_id, source_customer.id)
        self.assertEqual(direct_trade.responder_user_id, responder_owner.id)
        self.assertEqual(direct_trade.offer_id, offer.id)
        self.assertEqual(direct_trade.price, 200000)
        self.assertEqual(direct_trade.trade_type, TradeType.BUY)
        self.assertEqual(result, {"id": 104, "trade_number": 10017, "offer_id": offer.id, "price": 200000})

        trade_payload = next(call.args[1] for call in publish_mock.await_args_list if call.args[0] == "trade:created")
        self.assertEqual(trade_payload.get("trade_path_summary"), "مالک ↔ مشتری سطح ۱")

    async def test_execute_trade_authoritatively_fans_out_notifications_to_both_owner_sides(self):
        owner_user = make_user(id=5, account_name="owner_principal", telegram_id=555)
        offer = make_offer(user_id=9)
        offer_side_accountant_id = 77
        reloaded_trade = SimpleNamespace(id=92)
        db = FakeDB(
            get_results=[offer],
            execute_results=[
                FakeExecuteResult(single=owner_user),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10004,
        )

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=[[owner_user.id], [offer.user_id, offer_side_accountant_id]]),
        ) as audience_mock, patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch(
            "api.routers.trades.update_channel_buttons",
            new=AsyncMock(return_value=True),
        ), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ) as notif_mock, patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()), patch(
            "api.routers.trades.trade_to_response",
            return_value={"id": 92, "trade_number": 10005},
        ):
            await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4),
                BackgroundTasks(),
                db=db,
                context=make_context(owner_user),
            )

        self.assertEqual(audience_mock.await_count, 2)
        self.assertEqual(
            [call.args[1] for call in notif_mock.await_args_list],
            [owner_user.id, offer.user_id, offer_side_accountant_id],
        )

    async def test_execute_trade_authoritatively_keeps_counterpart_payloads_on_owner_principal(self):
        owner_user = make_user(id=5, account_name="owner_principal", telegram_id=555)
        offer = make_offer(
            user_id=9,
            user=SimpleNamespace(account_name="seller_principal", mobile_number="09125555555", telegram_id=999),
        )
        reloaded_trade = SimpleNamespace(id=93)
        db = FakeDB(
            get_results=[offer],
            execute_results=[
                FakeExecuteResult(single=owner_user),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10005,
        )
        background_tasks = BackgroundTasks()

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=[[owner_user.id], [offer.user_id]]),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch(
            "api.routers.trades.update_channel_buttons",
            new=AsyncMock(return_value=True),
        ), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ) as notif_mock, patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.trades.trade_to_response",
            return_value={"id": 93, "trade_number": 10006},
        ):
            await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4),
                background_tasks,
                db=db,
                context=make_context(owner_user),
            )

        notification_messages = {call.args[1]: call.args[2] for call in notif_mock.await_args_list}
        self.assertIn(owner_user.account_name, notification_messages[offer.user_id])

        trade_created_payload = publish_mock.await_args_list[0].args[1]
        self.assertEqual(trade_created_payload["responder_user_id"], owner_user.id)
        self.assertEqual(trade_created_payload["responder_user_name"], owner_user.account_name)
        self.assertNotIn("actor_user_id", trade_created_payload)

        owner_side_telegram_task = next(task for task in background_tasks.tasks if task.args[0] == offer.user.telegram_id)
        self.assertIn(owner_user.account_name, owner_side_telegram_task.args[1])

    async def test_execute_trade_authoritatively_uses_relation_aware_counterparty_labels_in_payloads_and_notifications(self):
        owner_user = make_user(id=5, account_name="owner_principal", telegram_id=555)
        offer_accountant_id = 77
        offer = make_offer(
            user_id=offer_accountant_id,
            user=SimpleNamespace(account_name="seller_accountant", mobile_number="09125555555", telegram_id=999),
        )
        reloaded_trade = SimpleNamespace(id=94)
        db = FakeDB(
            get_results=[offer],
            execute_results=[
                FakeExecuteResult(single=owner_user),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10006,
        )
        background_tasks = BackgroundTasks()

        relation_identity = {
            offer_accountant_id: SimpleNamespace(
                display_name="حسابدار فروش",
                profile_user_id=19,
                profile_account_name="seller_owner",
                resolved_from_accountant_id=offer_accountant_id,
                highlight_accountant_user_id=offer_accountant_id,
                highlight_accountant_relation_display_name="حسابدار فروش",
            ),
        }

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=[[owner_user.id], [offer.user_id]]),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value=relation_identity),
        ), patch(
            "api.routers.trades.update_channel_buttons",
            new=AsyncMock(return_value=True),
        ), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ) as notif_mock, patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.trades.trade_to_response",
            return_value={"id": 94, "trade_number": 10007},
        ):
            await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4),
                background_tasks,
                db=db,
                context=make_context(owner_user),
            )

        responder_notification = notif_mock.await_args_list[0].args[2]
        self.assertIn("حسابدار فروش", responder_notification)
        self.assertEqual(
            notif_mock.await_args_list[0].kwargs['extra_payload']['route'],
            '/users/19?account_name=seller_owner&highlight_accountant_user_id=77&highlight_accountant_relation_display_name=%D8%AD%D8%B3%D8%A7%D8%A8%D8%AF%D8%A7%D8%B1+%D9%81%D8%B1%D9%88%D8%B4',
        )

        trade_created_payload = publish_mock.await_args_list[0].args[1]
        self.assertEqual(trade_created_payload['id'], 94)
        self.assertEqual(trade_created_payload["offer_user_name"], "حسابدار فروش")
        self.assertEqual(trade_created_payload['status'], 'completed')
        self.assertEqual(trade_created_payload["offer_user_profile_user_id"], 19)
        self.assertEqual(trade_created_payload["offer_user_profile_account_name"], "seller_owner")
        self.assertEqual(trade_created_payload["offer_user_resolved_from_accountant_id"], offer_accountant_id)
        self.assertEqual(trade_created_payload["offer_user_highlight_accountant_user_id"], offer_accountant_id)
        self.assertEqual(trade_created_payload["offer_user_highlight_accountant_relation_display_name"], "حسابدار فروش")

        responder_telegram_task = next(task for task in background_tasks.tasks if task.args[0] == owner_user.telegram_id)
        self.assertIn("حسابدار فروش", responder_telegram_task.args[1])


if __name__ == "__main__":
    unittest.main()
