import json
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from api.routers import trades
from core.enums import NotificationCategory, NotificationLevel
from models.customer_relation import CustomerRelationStatus, CustomerTier
from models.offer import OfferStatus


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


class FakeLoop:
    def __init__(self, *, result=True, error=None):
        self.result = result
        self.error = error
        self.closed = False

    def run_until_complete(self, coro):
        coro.close()
        if self.error is not None:
            raise self.error
        return self.result

    def close(self):
        self.closed = True


class _AsyncSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TradesRouterHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_trade_helper_shortcuts_cover_invalid_inputs_and_projection_fallbacks(self):
        display_identity = SimpleNamespace(display_name="حسابدار نمایش")
        self.assertEqual(
            trades._resolve_trade_participant_name(
                SimpleNamespace(account_name="fallback-user"),
                7,
                {7: display_identity},
            ),
            "حسابدار نمایش",
        )
        self.assertEqual(
            trades._resolve_trade_participant_name(
                SimpleNamespace(account_name="fallback-user"),
                "not-an-id",
                {7: display_identity},
            ),
            "fallback-user",
        )

        self.assertIsNone(trades._normalize_customer_tier_value(SimpleNamespace(value=123)))
        self.assertIsNone(trades._normalize_trade_role_value(SimpleNamespace(value=123)))
        self.assertFalse(
            trades._viewer_can_access_customer_history_relation(
                relation=SimpleNamespace(owner_user_id=None),
                context=SimpleNamespace(
                    owner_user=SimpleNamespace(id=9, role=None),
                    actor_user=SimpleNamespace(id=9, role=None),
                ),
            )
        )

        self.assertEqual(
            await trades._load_trade_customer_relation_map_for_user_ids(AsyncMock(), []),
            {},
        )

        naive_created_relation = SimpleNamespace(
            customer_user_id=51,
            owner_user_id=7,
            status=CustomerRelationStatus.REVOKED,
            deleted_at=None,
            updated_at=None,
            expires_at=None,
            activated_at=None,
            created_at=datetime(2025, 1, 3, 9, 30),
        )
        naive_deleted_relation = SimpleNamespace(
            customer_user_id=52,
            owner_user_id=7,
            status=CustomerRelationStatus.DELETED,
            deleted_at=datetime(2025, 1, 4, 10, 15),
            updated_at=None,
            expires_at=None,
            activated_at=None,
            created_at=None,
        )
        timeless_relation = SimpleNamespace(
            customer_user_id=53,
            owner_user_id=7,
            status=CustomerRelationStatus.EXPIRED,
            deleted_at=None,
            updated_at=None,
            expires_at=None,
            activated_at=None,
            created_at=None,
        )
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [timeless_relation, naive_created_relation, naive_deleted_relation])
        )

        relation_map = await trades._load_trade_customer_relation_map_for_user_ids(
            db,
            [51, 52, 53],
            include_inactive_historical=True,
        )

        self.assertIs(relation_map[51], naive_created_relation)
        self.assertIs(relation_map[52], naive_deleted_relation)
        self.assertIs(relation_map[53], timeless_relation)

        self.assertEqual(
            trades._build_trade_path_payload(
                offer_user_id="bad-user-id",
                responder_user_id=7,
                customer_relation_map={7: SimpleNamespace(owner_user_id=9, customer_tier=CustomerTier.TIER_1)},
            ),
            {"trade_path_kind": None, "trade_path_summary": None},
        )
        self.assertEqual(
            trades._build_trade_path_payload(
                offer_user_id=7,
                responder_user_id=9,
                customer_relation_map={7: SimpleNamespace(owner_user_id=9, customer_tier="tier3")},
            ),
            {"trade_path_kind": None, "trade_path_summary": None},
        )

        self.assertIsNone(trades._build_trade_history_viewer_context(SimpleNamespace(id=None)))
        self.assertIsNone(trades._build_trade_profile_route_from_payload("offer_user", {}))
        self.assertEqual(
            trades._build_trade_profile_route_from_payload(
                "offer_user",
                {"offer_user_profile_user_id": 71},
            ),
            "/users/71",
        )

        actor_offer_trade = SimpleNamespace(offer_user_id=22, responder_user_id=99, actor_user_id=11)
        actor_responder_trade = SimpleNamespace(offer_user_id=99, responder_user_id=22, actor_user_id=11)
        relation_map = {11: SimpleNamespace(owner_user_id=22)}
        self.assertIsNone(
            trades._resolve_trade_history_subject_prefix(
                trade=actor_offer_trade,
                history_target_user_id=11,
                customer_relation_map=relation_map,
            )
        )
        self.assertIsNone(
            trades._resolve_trade_history_subject_prefix(
                trade=actor_responder_trade,
                history_target_user_id=11,
                customer_relation_map=relation_map,
            )
        )
        self.assertIsNone(
            trades._resolve_trade_history_subject_prefix(
                trade=actor_offer_trade,
                history_target_user_id=11,
                customer_relation_map={11: SimpleNamespace(owner_user_id=None)},
            )
        )
        self.assertEqual(
            trades._resolve_trade_history_subject_prefix(
                trade=SimpleNamespace(offer_user_id=22, responder_user_id=11, actor_user_id=11),
                history_target_user_id=11,
                customer_relation_map=relation_map,
            ),
            "responder_user",
        )

    async def test_load_trade_customer_relation_map_preserves_historical_relations_when_requested(self):
        active_relation = SimpleNamespace(
            customer_user_id=42,
            owner_user_id=7,
            status=CustomerRelationStatus.ACTIVE,
            deleted_at=None,
            updated_at=None,
            expires_at=None,
            activated_at=datetime(2025, 1, 5, tzinfo=timezone.utc),
            created_at=datetime(2025, 1, 4, tzinfo=timezone.utc),
            management_name="مشتری فعال",
            customer_tier=CustomerTier.TIER_1,
        )
        deleted_relation = SimpleNamespace(
            customer_user_id=41,
            owner_user_id=7,
            status=CustomerRelationStatus.DELETED,
            deleted_at=datetime(2025, 1, 8, tzinfo=timezone.utc),
            updated_at=datetime(2025, 1, 8, tzinfo=timezone.utc),
            expires_at=None,
            activated_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            management_name="مشتری حذف‌شده",
            customer_tier=CustomerTier.TIER_2,
        )
        stale_deleted_relation = SimpleNamespace(
            customer_user_id=42,
            owner_user_id=7,
            status=CustomerRelationStatus.REVOKED,
            deleted_at=datetime(2024, 12, 30, tzinfo=timezone.utc),
            updated_at=datetime(2024, 12, 30, tzinfo=timezone.utc),
            expires_at=None,
            activated_at=datetime(2024, 12, 1, tzinfo=timezone.utc),
            created_at=datetime(2024, 12, 1, tzinfo=timezone.utc),
            management_name="مشتری قدیمی",
            customer_tier=CustomerTier.TIER_1,
        )
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [deleted_relation, stale_deleted_relation, active_relation])
        )

        relation_map = await trades._load_trade_customer_relation_map_for_user_ids(
            db,
            [41, 42],
            include_inactive_historical=True,
        )

        self.assertIs(relation_map[41], deleted_relation)
        self.assertIs(relation_map[42], active_relation)

    async def test_get_customer_history_relation_for_customer_falls_back_to_inactive_relation(self):
        stale_relation = SimpleNamespace(
            customer_user_id=42,
            owner_user_id=7,
            status=CustomerRelationStatus.REVOKED,
            deleted_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            updated_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            expires_at=None,
            activated_at=None,
            created_at=datetime(2024, 12, 1, tzinfo=timezone.utc),
        )
        latest_relation = SimpleNamespace(
            customer_user_id=42,
            owner_user_id=7,
            status=CustomerRelationStatus.DELETED,
            deleted_at=datetime(2025, 1, 8, tzinfo=timezone.utc),
            updated_at=datetime(2025, 1, 8, tzinfo=timezone.utc),
            expires_at=None,
            activated_at=None,
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [stale_relation, latest_relation])
        )

        with patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ):
            relation = await trades._get_customer_history_relation_for_customer(db, 42)

        self.assertIs(relation, latest_relation)

    async def test_trade_to_response_and_telegram_helpers(self):
        trade = SimpleNamespace(
            id=1,
            trade_number=10001,
            offer_id=10,
            trade_type=SimpleNamespace(value="buy"),
            commodity_id=5,
            commodity=SimpleNamespace(name="Gold"),
            quantity=7,
            price=75000,
            status=SimpleNamespace(value="pending"),
            offer_user_id=11,
            offer_user=SimpleNamespace(account_name="seller"),
            responder_user_id=22,
            responder_user=SimpleNamespace(account_name="buyer"),
            actor_user_id=None,
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )

        with patch("api.routers.trades.to_jalali_str", return_value="1403/10/12"):
            response = trades.trade_to_response(trade)
        self.assertEqual(response.trade_type, "buy")
        self.assertEqual(response.commodity_name, "Gold")
        self.assertEqual(response.offer_user_name, "seller")
        self.assertEqual(response.responder_user_name, "buyer")
        self.assertEqual(response.created_at, "1403/10/12")

        relation_aware_response = trades.trade_to_response(
            trade,
            identity_map={
                11: SimpleNamespace(
                    display_name="حسابدار فروش",
                    profile_user_id=71,
                    profile_account_name="owner-71",
                    resolved_from_accountant_id=11,
                    highlight_accountant_user_id=11,
                    highlight_accountant_relation_display_name="حسابدار فروش",
                ),
                22: SimpleNamespace(
                    display_name="حسابدار خرید",
                    profile_user_id=82,
                    profile_account_name="owner-82",
                    resolved_from_accountant_id=22,
                    highlight_accountant_user_id=22,
                    highlight_accountant_relation_display_name="حسابدار خرید",
                ),
            },
            customer_relation_map={
                11: SimpleNamespace(
                    owner_user_id=22,
                    customer_tier=CustomerTier.TIER_1,
                    management_name="مشتری مستقیم",
                ),
            },
        )
        self.assertEqual(relation_aware_response.offer_user_name, "حسابدار فروش")
        self.assertEqual(relation_aware_response.responder_user_name, "حسابدار خرید")
        self.assertEqual(relation_aware_response.offer_user_profile_user_id, 71)
        self.assertEqual(relation_aware_response.offer_user_profile_account_name, "owner-71")
        self.assertEqual(relation_aware_response.offer_user_resolved_from_accountant_id, 11)
        self.assertEqual(relation_aware_response.offer_user_highlight_accountant_user_id, 11)
        self.assertEqual(relation_aware_response.offer_user_highlight_accountant_relation_display_name, "حسابدار فروش")
        self.assertEqual(relation_aware_response.responder_user_profile_user_id, 82)
        self.assertEqual(relation_aware_response.responder_user_profile_account_name, "owner-82")
        self.assertEqual(relation_aware_response.responder_user_resolved_from_accountant_id, 22)
        self.assertEqual(relation_aware_response.responder_user_highlight_accountant_user_id, 22)
        self.assertEqual(relation_aware_response.responder_user_highlight_accountant_relation_display_name, "حسابدار خرید")
        self.assertEqual(relation_aware_response.trade_path_kind, "owner_customer_tier1")
        self.assertEqual(relation_aware_response.trade_path_summary, "مالک ↔ مشتری سطح ۱")

        mediated_trade = SimpleNamespace(
            id=2,
            trade_number=10002,
            offer_id=15,
            trade_type=SimpleNamespace(value="sell"),
            commodity_id=6,
            commodity=SimpleNamespace(name="Coin"),
            quantity=3,
            price=82000,
            status=SimpleNamespace(value="completed"),
            offer_user_id=22,
            offer_user=SimpleNamespace(account_name="owner-account"),
            responder_user_id=99,
            responder_user=SimpleNamespace(account_name="outsider-account"),
            actor_user_id=11,
            created_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
        )
        owner_view_response = trades.trade_to_response(
            mediated_trade,
            identity_map={
                22: SimpleNamespace(
                    display_name="مالک معامله",
                    profile_user_id=22,
                    profile_account_name="owner-22",
                    resolved_from_accountant_id=None,
                    highlight_accountant_user_id=None,
                    highlight_accountant_relation_display_name=None,
                )
            },
            customer_relation_map={
                11: SimpleNamespace(
                    owner_user_id=22,
                    customer_tier=CustomerTier.TIER_1,
                    management_name="مشتری واسط",
                ),
            },
            viewer_context=SimpleNamespace(
                owner_user=SimpleNamespace(id=22, role=None),
                actor_user=SimpleNamespace(id=22, role=None),
                relation=None,
                is_accountant_context=False,
            ),
            history_target_user_id=99,
        )
        self.assertEqual(owner_view_response.counterparty_user_id, 22)
        self.assertEqual(owner_view_response.counterparty_name, "مالک معامله")
        self.assertEqual(owner_view_response.counterparty_profile_user_id, 22)
        self.assertEqual(owner_view_response.counterparty_profile_account_name, "owner-22")
        self.assertTrue(owner_view_response.customer_context_visible)
        self.assertEqual(owner_view_response.customer_context_user_id, 11)
        self.assertEqual(owner_view_response.customer_context_management_name, "مشتری واسط")
        self.assertEqual(owner_view_response.customer_context_tier, CustomerTier.TIER_1.value)

        customer_view_response = trades.trade_to_response(
            mediated_trade,
            customer_relation_map={
                11: SimpleNamespace(
                    owner_user_id=22,
                    customer_tier=CustomerTier.TIER_1,
                    management_name="مشتری واسط",
                ),
            },
            viewer_context=SimpleNamespace(
                owner_user=SimpleNamespace(id=11, role=None),
                actor_user=SimpleNamespace(id=11, role=None),
                relation=None,
                is_accountant_context=False,
            ),
            history_target_user_id=11,
        )
        self.assertIsNone(customer_view_response.counterparty_user_id)
        self.assertIsNone(customer_view_response.counterparty_name)
        self.assertFalse(customer_view_response.customer_context_visible)

        event_payload = trades._build_trade_created_event_payload(
            trade_id=91,
            trade_number=10002,
            offer_id=10,
            commodity_id=5,
            quantity=7,
            price=75000,
            commodity_name='Gold',
            trade_type='buy',
            status='completed',
            created_at='1403/10/12',
            offer_user=trade.offer_user,
            offer_user_id=trade.offer_user_id,
            responder_user=trade.responder_user,
            responder_user_id=trade.responder_user_id,
            identity_map={
                11: SimpleNamespace(
                    display_name='حسابدار فروش',
                    profile_user_id=71,
                    profile_account_name='owner-71',
                    resolved_from_accountant_id=11,
                    highlight_accountant_user_id=11,
                    highlight_accountant_relation_display_name='حسابدار فروش',
                ),
                22: SimpleNamespace(
                    display_name='حسابدار خرید',
                    profile_user_id=82,
                    profile_account_name='owner-82',
                    resolved_from_accountant_id=22,
                    highlight_accountant_user_id=22,
                    highlight_accountant_relation_display_name='حسابدار خرید',
                ),
            },
            customer_relation_map={
                11: SimpleNamespace(
                    owner_user_id=22,
                    customer_tier=CustomerTier.TIER_1,
                ),
            },
        )
        self.assertEqual(event_payload['id'], 91)
        self.assertEqual(event_payload['commodity_id'], 5)
        self.assertEqual(event_payload['status'], 'completed')
        self.assertEqual(event_payload['created_at'], '1403/10/12')
        self.assertEqual(event_payload['offer_user_profile_user_id'], 71)
        self.assertEqual(event_payload['trade_path_kind'], 'owner_customer_tier1')
        self.assertEqual(event_payload['trade_path_summary'], 'مالک ↔ مشتری سطح ۱')
        self.assertNotIn('audience_user_ids', event_payload)
        self.assertNotIn('recipient_specific', event_payload)

        recipient_payload = trades._build_trade_created_event_payload(
            trade_id=92,
            trade_number=10003,
            offer_id=15,
            commodity_id=6,
            quantity=3,
            price=82000,
            commodity_name='Coin',
            trade_type='sell',
            status='completed',
            created_at='1403/10/13',
            offer_user=mediated_trade.offer_user,
            offer_user_id=mediated_trade.offer_user_id,
            responder_user=mediated_trade.responder_user,
            responder_user_id=mediated_trade.responder_user_id,
            actor_user_id=11,
            identity_map={
                22: SimpleNamespace(
                    display_name='مالک معامله',
                    profile_user_id=22,
                    profile_account_name='owner-22',
                    resolved_from_accountant_id=None,
                    highlight_accountant_user_id=None,
                    highlight_accountant_relation_display_name=None,
                )
            },
            customer_relation_map={
                11: SimpleNamespace(
                    owner_user_id=22,
                    customer_tier=CustomerTier.TIER_1,
                    management_name='مشتری واسط',
                ),
            },
            viewer_context=SimpleNamespace(
                owner_user=SimpleNamespace(id=22, role=None),
                actor_user=SimpleNamespace(id=22, role=None),
                relation=None,
                is_accountant_context=False,
            ),
            history_target_user_id=99,
            recipient_specific=True,
            audience_user_ids=[22, 33],
        )
        self.assertTrue(recipient_payload['recipient_specific'])
        self.assertEqual(recipient_payload['counterparty_user_id'], 22)
        self.assertEqual(recipient_payload['counterparty_name'], 'مالک معامله')
        self.assertTrue(recipient_payload['customer_context_visible'])
        self.assertEqual(recipient_payload['customer_context_management_name'], 'مشتری واسط')
        self.assertEqual(recipient_payload['customer_context_tier'], CustomerTier.TIER_1.value)
        self.assertEqual(recipient_payload['audience_user_ids'], [22, 33])

        profile_route = trades._build_trade_profile_route_from_payload('offer_user', event_payload)
        self.assertEqual(
            profile_route,
            '/users/71?account_name=owner-71&highlight_accountant_user_id=11&highlight_accountant_relation_display_name=%D8%AD%D8%B3%D8%A7%D8%A8%D8%AF%D8%A7%D8%B1+%D9%81%D8%B1%D9%88%D8%B4',
        )

        notification_payload = trades._build_trade_notification_extra_payload(
            'offer_user',
            event_payload,
            trade_number=10002,
        )
        self.assertEqual(notification_payload['route'], profile_route)
        self.assertEqual(notification_payload['counterparty_profile_user_id'], 71)
        self.assertEqual(notification_payload['highlight_accountant_user_id'], 11)

        with patch("api.routers.trades.os.getenv", return_value=None):
            self.assertFalse(await trades.send_telegram_message(1, "hello"))

        with patch("api.routers.trades.os.getenv", return_value="token"), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=FakeHttpClientContext(response=SimpleNamespace(status_code=200)),
        ):
            self.assertTrue(await trades.send_telegram_message(1, "hello"))

        with patch("api.routers.trades.os.getenv", return_value="token"), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=FakeHttpClientContext(error=RuntimeError("telegram down")),
        ), patch.object(trades, "logger") as logger:
            self.assertFalse(await trades.send_telegram_message(1, "hello"))
        logger.error.assert_called_once()

        with patch("api.routers.trades.os.getenv", return_value=None):
            self.assertFalse(trades.send_telegram_message_sync(1, "hello"))

        with patch("api.routers.trades.os.getenv", return_value="token"), patch(
            "core.telegram_gateway.httpx.post",
            return_value=SimpleNamespace(status_code=200),
        ):
            self.assertTrue(trades.send_telegram_message_sync(1, "hello"))

        with patch("api.routers.trades.os.getenv", return_value="token"), patch(
            "core.telegram_gateway.httpx.post",
            side_effect=RuntimeError("telegram down"),
        ), patch.object(trades, "logger") as logger:
            self.assertFalse(trades.send_telegram_message_sync(1, "hello"))
        logger.error.assert_called_once()

    async def test_update_channel_button_helpers(self):
        offer = SimpleNamespace(
            id=9,
            quantity=30,
            remaining_quantity=0,
            is_wholesale=True,
            lot_sizes=None,
            channel_message_id=123,
            status=OfferStatus.ACTIVE,
        )

        with patch("api.routers.trades.os.getenv", return_value=None):
            self.assertFalse(await trades.update_channel_buttons(offer))

        with patch("api.routers.trades.os.getenv", return_value="token"), patch.object(
            trades.settings, "channel_id", -100
        ), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=FakeHttpClientContext(response=SimpleNamespace(status_code=200)),
        ) as client_ctor:
            self.assertTrue(await trades.update_channel_buttons(offer))

        posted_payload = client_ctor.return_value.post.await_args.kwargs["json"]
        self.assertEqual(posted_payload, {"chat_id": -100, "message_id": 123})

        wholesale_offer = SimpleNamespace(
            id=11,
            quantity=30,
            remaining_quantity=12,
            is_wholesale=True,
            lot_sizes=None,
            channel_message_id=125,
            status=OfferStatus.ACTIVE,
        )
        with patch("api.routers.trades.os.getenv", return_value="token"), patch.object(
            trades.settings, "channel_id", -100
        ), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=FakeHttpClientContext(response=SimpleNamespace(status_code=200)),
        ) as client_ctor:
            self.assertTrue(await trades.update_channel_buttons(wholesale_offer))

        self.assertEqual(
            client_ctor.return_value.post.await_args.kwargs["json"]["reply_markup"]["inline_keyboard"][0][0]["text"],
            "12 عدد",
        )

        active_offer = SimpleNamespace(
            id=10,
            quantity=30,
            remaining_quantity=18,
            is_wholesale=False,
            lot_sizes=[10, 8],
            channel_message_id=124,
            status=OfferStatus.ACTIVE,
        )
        with patch("api.routers.trades.os.getenv", return_value="token"), patch.object(
            trades.settings, "channel_id", -100
        ), patch("api.routers.trades.get_available_trade_amounts", return_value=[10, 8]), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=FakeHttpClientContext(response=SimpleNamespace(status_code=200)),
        ) as client_ctor:
            self.assertTrue(await trades.update_channel_buttons(active_offer))

        buttons = client_ctor.return_value.post.await_args.kwargs["json"]["reply_markup"]["inline_keyboard"][0]
        self.assertEqual([button["text"] for button in buttons], ["10 عدد", "8 عدد"])

        with patch("api.routers.trades.os.getenv", return_value="token"), patch.object(
            trades.settings, "channel_id", -100
        ), patch("api.routers.trades.get_available_trade_amounts", return_value=[]), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=FakeHttpClientContext(response=SimpleNamespace(status_code=200)),
        ) as client_ctor:
            self.assertTrue(await trades.update_channel_buttons(active_offer))

        self.assertNotIn("reply_markup", client_ctor.return_value.post.await_args.kwargs["json"])

        with patch("api.routers.trades.os.getenv", return_value="token"), patch.object(
            trades.settings, "channel_id", -100
        ), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=FakeHttpClientContext(error=RuntimeError("telegram down")),
        ), patch.object(trades, "logger") as logger:
            self.assertFalse(await trades.update_channel_buttons(active_offer))
        logger.error.assert_called_once()

    async def test_sync_channel_update_and_expiry_helpers(self):
        with patch("api.routers.trades.os.getenv", return_value=None):
            self.assertFalse(trades.update_channel_buttons_sync(1, 5, OfferStatus.ACTIVE, None))

        fake_loop = FakeLoop(result=True)
        with patch("api.routers.trades.os.getenv", return_value="token"), patch.object(
            trades.settings, "channel_id", -100
        ), patch("asyncio.new_event_loop", return_value=fake_loop), patch(
            "asyncio.set_event_loop"
        ):
            self.assertTrue(trades.update_channel_buttons_sync(1, 5, OfferStatus.ACTIVE, None))
        self.assertTrue(fake_loop.closed)

        fake_loop = FakeLoop(error=RuntimeError("loop down"))
        with patch("api.routers.trades.os.getenv", return_value="token"), patch.object(
            trades.settings, "channel_id", -100
        ), patch("asyncio.new_event_loop", return_value=fake_loop), patch(
            "asyncio.set_event_loop"
        ), patch.object(trades, "logger") as logger:
            self.assertFalse(trades.update_channel_buttons_sync(1, 5, OfferStatus.ACTIVE, None))
        logger.error.assert_called_once()

        session = SimpleNamespace(get=AsyncMock(return_value=None))
        with patch("api.routers.trades.os.getenv", return_value=None), patch.object(trades.settings, "channel_id", -100):
            self.assertFalse(await trades._update_channel_buttons_async(1, 5, OfferStatus.ACTIVE, None))

        with patch("api.routers.trades.os.getenv", return_value="token"), patch.object(
            trades.settings, "channel_id", -100
        ), patch("core.db.AsyncSessionLocal", return_value=_AsyncSessionContext(session)):
            self.assertFalse(await trades._update_channel_buttons_async(1, 5, OfferStatus.ACTIVE, None))

        offer = SimpleNamespace(
            id=1,
            quantity=30,
            remaining_quantity=18,
            is_wholesale=True,
            channel_message_id=321,
        )
        session = SimpleNamespace(get=AsyncMock(return_value=offer))
        with patch("api.routers.trades.os.getenv", return_value="token"), patch.object(
            trades.settings, "channel_id", -100
        ), patch("core.db.AsyncSessionLocal", return_value=_AsyncSessionContext(session)), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=FakeHttpClientContext(response=SimpleNamespace(status_code=200)),
        ) as client_ctor:
            self.assertTrue(await trades._update_channel_buttons_async(1, 18, OfferStatus.ACTIVE, None))

        payload = client_ctor.return_value.post.await_args.kwargs["json"]
        self.assertEqual(payload["reply_markup"]["inline_keyboard"][0][0]["text"], "18 عدد")

        retail_offer = SimpleNamespace(
            id=2,
            quantity=30,
            remaining_quantity=18,
            is_wholesale=False,
            channel_message_id=322,
        )
        session = SimpleNamespace(get=AsyncMock(return_value=retail_offer))
        with patch("api.routers.trades.os.getenv", return_value="token"), patch.object(
            trades.settings, "channel_id", -100
        ), patch("core.db.AsyncSessionLocal", return_value=_AsyncSessionContext(session)), patch(
            "api.routers.trades.get_available_trade_amounts", return_value=[]
        ), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=FakeHttpClientContext(response=SimpleNamespace(status_code=200)),
        ) as client_ctor:
            self.assertTrue(await trades._update_channel_buttons_async(2, 18, OfferStatus.ACTIVE, [10, 8]))
        self.assertEqual(client_ctor.return_value.post.await_args.kwargs["json"], {"chat_id": -100, "message_id": 322})

        session = SimpleNamespace(get=AsyncMock(return_value=retail_offer))
        with patch("api.routers.trades.os.getenv", return_value="token"), patch.object(
            trades.settings, "channel_id", -100
        ), patch("core.db.AsyncSessionLocal", return_value=_AsyncSessionContext(session)), patch(
            "api.routers.trades.get_available_trade_amounts", return_value=[10, 8]
        ), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=FakeHttpClientContext(response=SimpleNamespace(status_code=200)),
        ) as client_ctor:
            self.assertTrue(await trades._update_channel_buttons_async(2, 18, OfferStatus.ACTIVE, [10, 8]))
        self.assertEqual(
            [button["text"] for button in client_ctor.return_value.post.await_args.kwargs["json"]["reply_markup"]["inline_keyboard"][0]],
            ["10 عدد", "8 عدد"],
        )

        session = SimpleNamespace(get=AsyncMock(return_value=retail_offer))
        with patch("api.routers.trades.os.getenv", return_value="token"), patch.object(
            trades.settings, "channel_id", -100
        ), patch("core.db.AsyncSessionLocal", return_value=_AsyncSessionContext(session)), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=FakeHttpClientContext(response=SimpleNamespace(status_code=200)),
        ) as client_ctor:
            self.assertTrue(await trades._update_channel_buttons_async(2, 18, OfferStatus.COMPLETED, [10, 8]))
        text_call, markup_call = client_ctor.return_value.post.await_args_list
        text_payload = text_call.kwargs["json"]
        markup_payload = markup_call.kwargs["json"]
        self.assertTrue(text_call.args[0].endswith("/editMessageText"))
        self.assertEqual(text_payload["chat_id"], -100)
        self.assertEqual(text_payload["message_id"], 322)
        self.assertNotIn("reply_markup", text_payload)
        self.assertIn("🤝 ✅", text_payload["text"])
        self.assertTrue(markup_call.args[0].endswith("/editMessageReplyMarkup"))
        self.assertEqual(markup_payload["chat_id"], -100)
        self.assertEqual(markup_payload["message_id"], 322)
        self.assertNotIn("reply_markup", markup_payload)

        aware = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
        naive = datetime(2025, 1, 1, 12, 0)
        self.assertIsNone(trades._normalize_naive_utc(None))
        self.assertEqual(trades._normalize_naive_utc(aware), naive)
        self.assertEqual(trades._normalize_naive_utc(naive), naive)

        with patch("core.trading_settings.get_trading_settings_async", AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=0))):
            self.assertFalse(await trades._is_offer_expired_for_trade(SimpleNamespace(created_at=naive), None))

        with patch("core.trading_settings.get_trading_settings_async", AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=10))), patch.object(
            trades.settings, "trade_forward_grace_seconds", 120
        ), patch("api.routers.trades.datetime") as datetime_mock:
            datetime_mock.utcnow.side_effect = [datetime(2025, 1, 1, 12, 20), datetime(2025, 1, 1, 12, 20)]
            expired = await trades._is_offer_expired_for_trade(
                SimpleNamespace(created_at=datetime(2025, 1, 1, 12, 0)),
                edge_received_at=datetime(2025, 1, 1, 12, 9),
            )
        self.assertTrue(expired)

        with patch("core.trading_settings.get_trading_settings_async", AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=10))), patch.object(
            trades.settings, "trade_forward_grace_seconds", 120
        ), patch("api.routers.trades.datetime") as datetime_mock:
            datetime_mock.utcnow.side_effect = [datetime(2025, 1, 1, 12, 10, 30), datetime(2025, 1, 1, 12, 10, 30)]
            expired = await trades._is_offer_expired_for_trade(
                SimpleNamespace(created_at=datetime(2025, 1, 1, 12, 0)),
                edge_received_at=datetime(2025, 1, 1, 12, 9, 45),
            )
        self.assertFalse(expired)

    async def test_forward_trade_remote_home_and_notification_fallbacks(self):
        trade_data = SimpleNamespace(offer_id=7, quantity=4, idempotency_key="idem-1")
        current_user = SimpleNamespace(id=99)
        edge_received_at = datetime(2025, 1, 1, 12, 0)

        db = SimpleNamespace(get=AsyncMock(return_value=None))
        self.assertIsNone(await trades._forward_trade_if_remote_home(db, trade_data, current_user, edge_received_at))

        remote_offer = SimpleNamespace(home_server="iran", offer_public_id="ofr_remote_home_7")
        db = SimpleNamespace(get=AsyncMock(return_value=remote_offer))
        with patch("api.routers.trades.is_remote_home", return_value=False):
            self.assertIsNone(await trades._forward_trade_if_remote_home(db, trade_data, current_user, edge_received_at))

        db = SimpleNamespace(get=AsyncMock(return_value=remote_offer))
        with patch("api.routers.trades.is_remote_home", return_value=True), patch(
            "api.routers.trades.current_server", return_value="foreign"
        ), patch(
            "api.routers.trades.forward_trade_to_home_server",
            AsyncMock(return_value=(202, {"ok": True})),
        ) as forward_mock:
            response = await trades._forward_trade_if_remote_home(db, trade_data, current_user, edge_received_at)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(json.loads(response.body), {"ok": True})
        forward_mock.assert_awaited_once_with(
            "iran",
            {
                "offer_id": 7,
                "offer_public_id": "ofr_remote_home_7",
                "quantity": 4,
                "responder_user_id": 99,
                "edge_received_at": edge_received_at.isoformat(),
                "source_surface": "webapp",
                "source_server": "foreign",
                "idempotency_key": "idem-1",
            },
        )


if __name__ == "__main__":
    unittest.main()
