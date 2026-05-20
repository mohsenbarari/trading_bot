import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, HTTPException

from api.routers.trades import TradeCreate, _execute_trade_authoritatively
from core.enums import NotificationCategory, NotificationLevel, UserRole
from models.offer import OfferStatus, OfferType
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
        self.added = []

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
        offer = make_offer()
        reloaded_trade = SimpleNamespace(id=88)
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
            new=AsyncMock(side_effect=[[locked_user.id], [offer.user_id]]),
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
                context=make_context(locked_user),
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
        self.assertEqual(offer.remaining_quantity, 0)
        self.assertEqual(offer.status, OfferStatus.COMPLETED)
        db.refresh.assert_awaited_once_with(offer, ["user", "commodity"])
        db.commit.assert_awaited_once()
        update_buttons_mock.assert_awaited_once_with(offer)
        self.assertEqual(len(background_tasks.tasks), 2)
        self.assertEqual(notif_mock.await_count, 2)
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
        response_mock.assert_called_once_with(reloaded_trade, identity_map={})
        self.assertEqual(result, {"id": 88, "trade_number": 10000})

    async def test_execute_trade_authoritatively_updates_retail_lots_and_tolerates_side_effect_failures(self):
        locked_user = make_user()
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
                context=make_context(locked_user),
            )

        self.assertEqual(offer.remaining_quantity, 1)
        self.assertEqual(offer.lot_sizes, [1])
        flag_modified.assert_called_once_with(offer, "lot_sizes")
        self.assertEqual(len(background_tasks.tasks), 2)
        notif_mock.assert_awaited_once()
        counter_mock.assert_awaited_once_with(db, locked_user, "trade", 3)
        self.assertEqual(publish_mock.await_count, 2)
        logger.error.assert_called_once()
        response_mock.assert_called_once_with(reloaded_trade, identity_map={})
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

    async def test_execute_trade_authoritatively_stamps_actor_user_id_for_accountant_context(self):
        owner_user = make_user(id=5, account_name="owner", telegram_id=555)
        actor_user = make_user(id=44, account_name="accountant", telegram_id=444)
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
            new=AsyncMock(side_effect=[[owner_user.id, actor_user.id], [offer.user_id]]),
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
                context=make_context(owner_user, actor_user),
            )

        new_trade = db.added[0]
        self.assertEqual(new_trade.responder_user_id, owner_user.id)
        self.assertEqual(new_trade.actor_user_id, actor_user.id)
        self.assertEqual(notif_mock.await_count, 3)
        self.assertEqual(notif_mock.await_args_list[0].args[1], owner_user.id)
        self.assertEqual(notif_mock.await_args_list[1].args[1], actor_user.id)
        self.assertEqual(notif_mock.await_args_list[2].args[1], offer.user_id)
        counter_mock.assert_awaited_once_with(db, owner_user, "trade", 4)
        self.assertEqual(publish_mock.await_args_list[0].args[1]["responder_user_id"], owner_user.id)
        self.assertEqual(result, {"id": 91, "trade_number": 10004})

    async def test_execute_trade_authoritatively_fans_out_notifications_to_both_owner_sides(self):
        owner_user = make_user(id=5, account_name="owner_principal", telegram_id=555)
        actor_user = make_user(id=44, account_name="delegate_actor", telegram_id=444)
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
            new=AsyncMock(side_effect=[[owner_user.id, actor_user.id], [offer.user_id, offer_side_accountant_id]]),
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
                context=make_context(owner_user, actor_user),
            )

        self.assertEqual(audience_mock.await_count, 2)
        self.assertEqual(
            [call.args[1] for call in notif_mock.await_args_list],
            [owner_user.id, actor_user.id, offer.user_id, offer_side_accountant_id],
        )

    async def test_execute_trade_authoritatively_keeps_counterpart_payloads_on_owner_principal(self):
        owner_user = make_user(id=5, account_name="owner_principal", telegram_id=555)
        actor_user = make_user(id=44, account_name="delegate_actor", telegram_id=444)
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
            new=AsyncMock(side_effect=[[owner_user.id, actor_user.id], [offer.user_id]]),
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
                context=make_context(owner_user, actor_user),
            )

        notification_messages = {call.args[1]: call.args[2] for call in notif_mock.await_args_list}
        self.assertIn(owner_user.account_name, notification_messages[offer.user_id])
        self.assertNotIn(actor_user.account_name, notification_messages[offer.user_id])

        trade_created_payload = publish_mock.await_args_list[0].args[1]
        self.assertEqual(trade_created_payload["responder_user_id"], owner_user.id)
        self.assertEqual(trade_created_payload["responder_user_name"], owner_user.account_name)
        self.assertNotIn("actor_user_id", trade_created_payload)
        self.assertNotIn(actor_user.account_name, str(trade_created_payload))

        owner_side_telegram_task = next(task for task in background_tasks.tasks if task.args[0] == offer.user.telegram_id)
        self.assertIn(owner_user.account_name, owner_side_telegram_task.args[1])
        self.assertNotIn(actor_user.account_name, owner_side_telegram_task.args[1])

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