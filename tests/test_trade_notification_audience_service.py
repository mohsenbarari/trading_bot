import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.enums import UserAccountStatus, UserRole
from core.services.bot_access_policy import BotAccessDecision
from core.services import trade_notification_audience_service as service
from models.customer_relation import CustomerTier
from models.trade import TradeStatus, TradeType


def make_user(
    user_id: int,
    *,
    account_name: str | None = None,
    role=UserRole.STANDARD,
    telegram_id: int | None = None,
    bot_allowed: bool = True,
):
    return SimpleNamespace(
        id=user_id,
        account_name=account_name or f"user_{user_id}",
        full_name=account_name or f"user_{user_id}",
        role=role,
        telegram_id=telegram_id,
        account_status=UserAccountStatus.ACTIVE,
        is_deleted=False,
        bot_allowed=bot_allowed,
    )


def make_relation(
    *,
    customer_user_id: int,
    owner_user_id: int,
    tier: CustomerTier,
    management_name: str | None = None,
):
    return SimpleNamespace(
        customer_user_id=customer_user_id,
        owner_user_id=owner_user_id,
        customer_tier=tier,
        management_name=management_name,
        deleted_at=None,
    )


def make_trade(
    *,
    trade_id: int = 501,
    trade_number: int = 10025,
    offer_user,
    responder_user,
    trade_type=TradeType.BUY,
    status=TradeStatus.COMPLETED,
    offer_home_server: str = "iran",
    notes: str | None = "توضیح تست",
):
    return SimpleNamespace(
        id=trade_id,
        trade_number=trade_number,
        offer_id=77,
        offer=SimpleNamespace(home_server=offer_home_server, notes=notes),
        offer_user_id=offer_user.id,
        offer_user=offer_user,
        responder_user_id=responder_user.id,
        responder_user=responder_user,
        commodity_id=3,
        commodity=SimpleNamespace(name="امام"),
        trade_type=trade_type,
        quantity=20,
        price=150000,
        status=status,
        created_at=datetime(2026, 6, 23, 7, 30, tzinfo=timezone.utc),
    )


def channel(recipient, channel_name: str):
    for requirement in recipient.channel_requirements:
        if requirement.channel == channel_name:
            return requirement
    raise AssertionError(f"missing channel {channel_name}")


class TradeNotificationAudienceServiceTests(unittest.IsolatedAsyncioTestCase):
    async def build_audience(
        self,
        trade,
        *,
        audience_side_effect,
        users,
        relations=None,
        bot_decisions=None,
    ):
        user_map = {user.id: user for user in users}
        bot_decisions = bot_decisions or {}

        async def fake_bot_access(_db, user):
            decision = bot_decisions.get(user.id)
            if decision is not None:
                return decision
            return BotAccessDecision(bool(getattr(user, "bot_allowed", True)), None if getattr(user, "bot_allowed", True) else "role_forbidden")

        with patch(
            "core.services.trade_notification_audience_service.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=audience_side_effect),
        ) as audience_mock, patch(
            "core.services.trade_notification_audience_service._load_users_by_ids",
            new=AsyncMock(return_value=user_map),
        ) as load_users_mock, patch(
            "core.services.trade_notification_audience_service._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value=relations or {}),
        ) as relation_mock, patch(
            "core.services.trade_notification_audience_service.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ) as identity_mock, patch(
            "core.services.trade_notification_audience_service.evaluate_bot_access",
            new=AsyncMock(side_effect=fake_bot_access),
        ) as bot_access_mock:
            result = await service.build_trade_completion_notification_audience(object(), trade)

        return SimpleNamespace(
            result=result,
            audience_mock=audience_mock,
            load_users_mock=load_users_mock,
            relation_mock=relation_mock,
            identity_mock=identity_mock,
            bot_access_mock=bot_access_mock,
        )

    async def test_direct_user_trade_requires_webapp_and_linked_telegram_for_both_sides(self):
        owner = make_user(10, account_name="seller", telegram_id=9010)
        responder = make_user(20, account_name="buyer", telegram_id=9020)
        trade = make_trade(offer_user=owner, responder_user=responder)

        built = await self.build_audience(
            trade,
            audience_side_effect=[[responder.id], [owner.id]],
            users=[owner, responder],
        )

        self.assertEqual(built.result.event_type, "trade_completed")
        self.assertEqual(built.result.trade_number, 10025)
        self.assertEqual(built.result.offer_home_server, "iran")
        self.assertEqual([recipient.recipient_user_id for recipient in built.result.recipients], [20, 10])
        for recipient in built.result.recipients:
            self.assertTrue(channel(recipient, "webapp").required)
            telegram = channel(recipient, "telegram")
            self.assertTrue(telegram.required)
            self.assertIn("شماره معامله: 10025", telegram.message)
            self.assertIn("توضیحات: توضیح تست", telegram.message)
        self.assertEqual(built.audience_mock.await_args_list[0].args[1], [responder.id])
        self.assertEqual(built.audience_mock.await_args_list[1].args[1], [owner.id])

    async def test_admin_normal_user_trade_keeps_bot_eligible_roles(self):
        admin = make_user(1, account_name="admin", role=UserRole.SUPER_ADMIN, telegram_id=9001)
        normal = make_user(2, account_name="normal", role=UserRole.STANDARD, telegram_id=9002)
        trade = make_trade(offer_user=admin, responder_user=normal, offer_home_server="foreign")

        built = await self.build_audience(
            trade,
            audience_side_effect=[[normal.id], [admin.id]],
            users=[admin, normal],
        )

        self.assertEqual({recipient.recipient_role for recipient in built.result.recipients}, {"responder", "offer_owner"})
        self.assertEqual({channel(recipient, "telegram").reason for recipient in built.result.recipients}, {"telegram_required"})
        self.assertEqual(built.result.offer_home_server, "foreign")

    async def test_tier1_customer_keeps_owner_path_and_linked_telegram(self):
        owner = make_user(10, account_name="owner", telegram_id=9010)
        customer = make_user(30, account_name="tier1", telegram_id=9030)
        trade = make_trade(offer_user=owner, responder_user=customer)
        relations = {
            customer.id: make_relation(
                customer_user_id=customer.id,
                owner_user_id=owner.id,
                tier=CustomerTier.TIER_1,
            )
        }

        built = await self.build_audience(
            trade,
            audience_side_effect=[[customer.id], [owner.id]],
            users=[owner, customer],
            relations=relations,
        )

        customer_recipient = built.result.recipients[0]
        self.assertEqual(built.result.trade_path_kind, "owner_customer_tier1")
        self.assertEqual(customer_recipient.extra_payload["trade_path_summary"], "مالک ↔ مشتری سطح ۱")
        self.assertTrue(channel(customer_recipient, "telegram").required)
        self.assertIn("طرف معامله: owner", customer_recipient.webapp_message)
        self.assertIn("<b>", channel(customer_recipient, "telegram").message)
        self.assertIn("طرف معامله:", channel(customer_recipient, "telegram").message)
        self.assertIn('start=profile_10">owner</a>', channel(customer_recipient, "telegram").message)

    async def test_customer_counterparty_uses_management_name_in_messages(self):
        owner = make_user(10, account_name="owner", telegram_id=9010)
        customer = make_user(30, account_name="customer_0937", telegram_id=9030)
        responder = make_user(40, account_name="buyer", telegram_id=9040)
        trade = make_trade(offer_user=customer, responder_user=responder)
        relations = {
            customer.id: make_relation(
                customer_user_id=customer.id,
                owner_user_id=owner.id,
                tier=CustomerTier.TIER_1,
                management_name="مشتری بازار تهران",
            )
        }

        built = await self.build_audience(
            trade,
            audience_side_effect=[[responder.id], [customer.id]],
            users=[owner, customer, responder],
            relations=relations,
        )

        responder_recipient = built.result.recipients[0]
        self.assertIn("طرف معامله: مشتری بازار تهران", responder_recipient.webapp_message)
        self.assertIn("طرف معامله:", channel(responder_recipient, "telegram").message)
        self.assertIn('start=profile_30">مشتری بازار تهران</a>', channel(responder_recipient, "telegram").message)
        self.assertNotIn("customer_0937", responder_recipient.webapp_message)
        self.assertNotIn("customer_0937", channel(responder_recipient, "telegram").message)

    async def test_tier2_customer_is_webapp_only_and_owner_counterparty_is_visible(self):
        owner = make_user(10, account_name="owner", telegram_id=9010)
        customer = make_user(40, account_name="tier2", telegram_id=9040)
        trade = make_trade(offer_user=owner, responder_user=customer)
        relations = {
            customer.id: make_relation(
                customer_user_id=customer.id,
                owner_user_id=owner.id,
                tier=CustomerTier.TIER_2,
            )
        }

        built = await self.build_audience(
            trade,
            audience_side_effect=[[customer.id], [owner.id]],
            users=[owner, customer],
            relations=relations,
            bot_decisions={customer.id: BotAccessDecision(False, "customer_tier2", customer_tier="tier2")},
        )

        customer_recipient = built.result.recipients[0]
        self.assertEqual(built.result.trade_path_kind, "owner_customer_tier2")
        self.assertTrue(channel(customer_recipient, "webapp").required)
        self.assertFalse(channel(customer_recipient, "telegram").required)
        self.assertEqual(channel(customer_recipient, "telegram").reason, "customer_tier2")
        self.assertIn("طرف معامله: owner", customer_recipient.webapp_message)

    async def test_accountant_monitoring_recipient_is_webapp_only(self):
        owner = make_user(10, account_name="owner", telegram_id=9010)
        responder = make_user(20, account_name="buyer", telegram_id=9020)
        accountant = make_user(77, account_name="accountant", telegram_id=9077)
        trade = make_trade(offer_user=owner, responder_user=responder)

        built = await self.build_audience(
            trade,
            audience_side_effect=[[responder.id], [owner.id, accountant.id]],
            users=[owner, responder, accountant],
        )

        accountant_recipient = [recipient for recipient in built.result.recipients if recipient.recipient_user_id == accountant.id][0]
        self.assertEqual(accountant_recipient.recipient_role, "accountant")
        self.assertEqual(accountant_recipient.principal_user_id, owner.id)
        self.assertTrue(channel(accountant_recipient, "webapp").required)
        self.assertFalse(channel(accountant_recipient, "telegram").required)
        self.assertEqual(channel(accountant_recipient, "telegram").reason, "accountant_webapp_only")
        self.assertEqual(accountant_recipient.extra_payload["recipient_role"], "accountant")

    async def test_customer_chain_is_derived_per_committed_trade_leg(self):
        owner = make_user(10, account_name="owner", telegram_id=9010)
        bridge = make_user(11, account_name="bridge", telegram_id=9011)
        customer = make_user(30, account_name="customer", telegram_id=9030)
        first_leg = make_trade(trade_id=601, trade_number=10031, offer_user=owner, responder_user=bridge)
        second_leg = make_trade(trade_id=603, trade_number=10033, offer_user=bridge, responder_user=customer)
        relations = {
            customer.id: make_relation(
                customer_user_id=customer.id,
                owner_user_id=bridge.id,
                tier=CustomerTier.TIER_1,
            )
        }

        first = await self.build_audience(
            first_leg,
            audience_side_effect=[[bridge.id], [owner.id]],
            users=[owner, bridge, customer],
            relations=relations,
        )
        second = await self.build_audience(
            second_leg,
            audience_side_effect=[[customer.id], [bridge.id]],
            users=[owner, bridge, customer],
            relations=relations,
        )

        self.assertEqual([recipient.recipient_user_id for recipient in first.result.recipients], [bridge.id, owner.id])
        self.assertEqual([recipient.recipient_user_id for recipient in second.result.recipients], [customer.id, bridge.id])
        self.assertIsNone(first.result.trade_path_kind)
        self.assertEqual(second.result.trade_path_kind, "owner_customer_tier1")

    async def test_offer_home_server_does_not_change_audience_rules(self):
        owner = make_user(10, account_name="owner", telegram_id=9010)
        responder = make_user(20, account_name="buyer", telegram_id=None)

        for home_server in ("iran", "foreign"):
            with self.subTest(home_server=home_server):
                trade = make_trade(offer_user=owner, responder_user=responder, offer_home_server=home_server)
                built = await self.build_audience(
                    trade,
                    audience_side_effect=[[responder.id], [owner.id]],
                    users=[owner, responder],
                )
                self.assertEqual(built.result.offer_home_server, home_server)
                self.assertTrue(channel(built.result.recipients[0], "webapp").required)
                self.assertFalse(channel(built.result.recipients[0], "telegram").required)
                self.assertEqual(channel(built.result.recipients[0], "telegram").reason, "telegram_unlinked")
                self.assertTrue(channel(built.result.recipients[1], "telegram").required)

    async def test_non_completed_trade_is_skipped_without_recipients(self):
        owner = make_user(10)
        responder = make_user(20)
        trade = make_trade(offer_user=owner, responder_user=responder, status=TradeStatus.PENDING)

        built = await self.build_audience(
            trade,
            audience_side_effect=[],
            users=[owner, responder],
        )

        self.assertEqual(built.result.skipped_reason, "trade_not_completed")
        self.assertEqual(built.result.recipients, ())
        built.audience_mock.assert_not_awaited()
