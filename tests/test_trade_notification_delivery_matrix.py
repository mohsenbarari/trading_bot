import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.trades import _build_trade_execution_plan
from core.enums import UserAccountStatus, UserRole
from core.services import trade_notification_audience_service as audience_service
from core.services.bot_access_policy import BotAccessDecision
from models.customer_relation import CustomerTier
from models.trade import TradeStatus, TradeType


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "report_trade_notification_delivery_matrix.py"

spec = importlib.util.spec_from_file_location("report_trade_notification_delivery_matrix", MODULE_PATH)
delivery_matrix = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = delivery_matrix
spec.loader.exec_module(delivery_matrix)


def make_user(user_id: int, account_name: str):
    return SimpleNamespace(
        id=user_id,
        account_name=account_name,
        full_name=account_name,
        mobile_number=f"09{user_id:09d}"[:11],
        telegram_id=900000 + user_id,
        role=UserRole.STANDARD,
        account_status=UserAccountStatus.ACTIVE,
        is_deleted=False,
    )


def make_relation(*, customer_user_id: int, owner_user_id: int, tier: CustomerTier):
    return SimpleNamespace(
        customer_user_id=customer_user_id,
        owner_user_id=owner_user_id,
        customer_tier=tier,
        deleted_at=None,
    )


def channel(recipient, channel_name: str):
    for requirement in recipient.channel_requirements:
        if requirement.channel == channel_name:
            return requirement
    raise AssertionError(f"missing channel {channel_name}")


class TradeNotificationDeliveryMatrixTests(unittest.IsolatedAsyncioTestCase):
    def build_actor_context(self, actor_pair, index: int):
        base = 10_000 + index * 100
        source_user = make_user(base + 1, f"{actor_pair.pair_id}_source_user")
        responder_user = make_user(base + 2, f"{actor_pair.pair_id}_responder_user")
        shared_owner = make_user(base + 3, f"{actor_pair.pair_id}_shared_owner")
        source_owner = make_user(base + 4, f"{actor_pair.pair_id}_source_owner")
        responder_owner = make_user(base + 5, f"{actor_pair.pair_id}_responder_owner")
        source_customer = make_user(base + 6, f"{actor_pair.pair_id}_source_customer")
        responder_customer = make_user(base + 7, f"{actor_pair.pair_id}_responder_customer")

        users = {
            user.id: user
            for user in (
                source_user,
                responder_user,
                shared_owner,
                source_owner,
                responder_owner,
                source_customer,
                responder_customer,
            )
        }
        relations = {}

        if actor_pair.source_kind == "user":
            source_actor = source_user
        else:
            source_actor = source_customer
            if actor_pair.group_relation == "same_owner" and actor_pair.responder_kind == "user":
                owner = responder_user
            elif actor_pair.group_relation == "same_owner":
                owner = shared_owner
            else:
                owner = source_owner
            relations[source_actor.id] = make_relation(
                customer_user_id=source_actor.id,
                owner_user_id=owner.id,
                tier=CustomerTier.TIER_1 if actor_pair.source_kind == "tier1" else CustomerTier.TIER_2,
            )

        if actor_pair.responder_kind == "user":
            responder_actor = responder_user
        else:
            responder_actor = responder_customer
            if actor_pair.group_relation == "same_owner" and actor_pair.source_kind == "user":
                owner = source_user
            elif actor_pair.group_relation == "same_owner":
                owner = shared_owner
            else:
                owner = responder_owner
            relations[responder_actor.id] = make_relation(
                customer_user_id=responder_actor.id,
                owner_user_id=owner.id,
                tier=CustomerTier.TIER_1 if actor_pair.responder_kind == "tier1" else CustomerTier.TIER_2,
            )

        owner_ids = {
            source_user.id,
            responder_user.id,
            shared_owner.id,
            source_owner.id,
            responder_owner.id,
        }
        accountant_by_owner = {}
        for owner_id in owner_ids:
            accountant = make_user(owner_id + 50, f"accountant_for_{owner_id}")
            users[accountant.id] = accountant
            accountant_by_owner[owner_id] = accountant.id

        def principal_for(actor):
            relation = relations.get(actor.id)
            if relation is None:
                return actor
            return users[relation.owner_user_id]

        return SimpleNamespace(
            users=users,
            relations=relations,
            accountant_by_owner=accountant_by_owner,
            source_actor=source_actor,
            responder_actor=responder_actor,
            source_principal=principal_for(source_actor),
            responder_principal=principal_for(responder_actor),
        )

    def audience_for_participant(self, context, user_id: int):
        values = [user_id]
        accountant_id = context.accountant_by_owner.get(user_id)
        if accountant_id is not None:
            values.append(accountant_id)
        return values

    def build_trade(self, *, leg_index: int, offer_user, responder_user, offer_home_server: str):
        return SimpleNamespace(
            id=50_000 + leg_index,
            trade_number=90_000 + leg_index,
            offer_id=70_000 + leg_index,
            offer=SimpleNamespace(home_server=offer_home_server, notes="matrix notes"),
            offer_user_id=offer_user.id,
            offer_user=offer_user,
            responder_user_id=responder_user.id,
            responder_user=responder_user,
            commodity_id=3,
            commodity=SimpleNamespace(name="امام"),
            trade_type=TradeType.BUY,
            quantity=20,
            price=150000,
            status=TradeStatus.COMPLETED,
            created_at=datetime(2026, 6, 23, 12, 30, tzinfo=timezone.utc),
        )

    async def build_audience_for_leg(self, context, trade):
        async def fake_bot_access(_db, user):
            relation = context.relations.get(getattr(user, "id", None))
            if relation is not None and relation.customer_tier == CustomerTier.TIER_2:
                return BotAccessDecision(False, "customer_tier2", customer_tier=CustomerTier.TIER_2.value)
            return BotAccessDecision(True)

        responder_audience = self.audience_for_participant(context, trade.responder_user_id)
        offer_audience = self.audience_for_participant(context, trade.offer_user_id)
        with patch(
            "core.services.trade_notification_audience_service.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=[responder_audience, offer_audience]),
        ), patch(
            "core.services.trade_notification_audience_service._load_users_by_ids",
            new=AsyncMock(return_value=context.users),
        ), patch(
            "core.services.trade_notification_audience_service._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value=context.relations),
        ), patch(
            "core.services.trade_notification_audience_service.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch(
            "core.services.trade_notification_audience_service.evaluate_bot_access",
            new=AsyncMock(side_effect=fake_bot_access),
        ):
            return await audience_service.build_trade_completion_notification_audience(object(), trade)

    def expected_trade_path_kind(self, context, offer_user_id: int, responder_user_id: int):
        for user_id in (offer_user_id, responder_user_id):
            relation = context.relations.get(user_id)
            if relation is None:
                continue
            other_id = responder_user_id if user_id == offer_user_id else offer_user_id
            if relation.owner_user_id != other_id:
                continue
            if relation.customer_tier == CustomerTier.TIER_1:
                return "owner_customer_tier1"
            if relation.customer_tier == CustomerTier.TIER_2:
                return "owner_customer_tier2"
        return None

    def assert_recipient_channels(self, context, recipient):
        webapp = channel(recipient, "webapp")
        telegram = channel(recipient, "telegram")
        self.assertTrue(webapp.required)
        self.assertIn("شماره معامله", webapp.message)
        self.assertIn("توضیحات: matrix notes", webapp.message)

        relation = context.relations.get(recipient.recipient_user_id)
        if recipient.recipient_role == "accountant":
            self.assertFalse(telegram.required)
            self.assertEqual(telegram.reason, "accountant_webapp_only")
            return
        if relation is not None:
            self.assertNotIn("طرف معامله", webapp.message)
            if relation.customer_tier == CustomerTier.TIER_2:
                self.assertFalse(telegram.required)
                self.assertEqual(telegram.reason, "customer_tier2")
                return
            self.assertTrue(telegram.required)
            self.assertNotIn("طرف معامله", telegram.message)
            return

        self.assertTrue(telegram.required)
        self.assertIn("شماره معامله", telegram.message)
        self.assertIn("توضیحات: matrix notes", telegram.message)

    async def test_trade_notification_delivery_matrix_runs_all_actor_surface_pairs_against_audience_builder(self):
        actor_pairs = delivery_matrix.build_actor_pairs()
        surface_pairs = delivery_matrix.build_surface_pairs()

        for actor_index, actor_pair in enumerate(actor_pairs, start=1):
            context = self.build_actor_context(actor_pair, actor_index)
            plan = _build_trade_execution_plan(
                offer_user_id=context.source_actor.id,
                offer_user=context.source_actor,
                source_principal_user_id=context.source_principal.id,
                source_principal_user=context.source_principal,
                responder_principal_user_id=context.responder_principal.id,
                responder_principal_user=context.responder_principal,
                owner_user_id=context.responder_actor.id,
                owner_user=context.responder_actor,
            )
            self.assertGreaterEqual(len(plan.nodes), 2, msg=actor_pair.pair_id)
            self.assertLessEqual(len(plan.nodes), 4, msg=actor_pair.pair_id)

            for surface_pair in surface_pairs:
                with self.subTest(actor_pair=actor_pair.pair_id, surface_pair=surface_pair.name):
                    for leg_index, (offer_node, responder_node) in enumerate(zip(plan.nodes, plan.nodes[1:]), start=1):
                        trade = self.build_trade(
                            leg_index=actor_index * 10 + leg_index,
                            offer_user=offer_node.user,
                            responder_user=responder_node.user,
                            offer_home_server=surface_pair.offer_home_server,
                        )
                        audience = await self.build_audience_for_leg(context, trade)
                        expected_recipients = []
                        for user_id in [
                            *self.audience_for_participant(context, responder_node.user_id),
                            *self.audience_for_participant(context, offer_node.user_id),
                        ]:
                            if user_id not in expected_recipients:
                                expected_recipients.append(user_id)

                        self.assertEqual(
                            [recipient.recipient_user_id for recipient in audience.recipients],
                            expected_recipients,
                        )
                        self.assertEqual(audience.offer_home_server, surface_pair.offer_home_server)
                        self.assertEqual(
                            audience.trade_path_kind,
                            self.expected_trade_path_kind(context, offer_node.user_id, responder_node.user_id),
                        )
                        for recipient in audience.recipients:
                            self.assert_recipient_channels(context, recipient)

    def test_delivery_scenario_catalog_covers_actor_surface_outage_product(self):
        actor_pairs = delivery_matrix.build_actor_pairs()
        surface_pairs = delivery_matrix.build_surface_pairs()
        outage_classes = delivery_matrix.build_outage_classes()
        scenarios = delivery_matrix.build_delivery_scenarios()

        self.assertEqual(len(actor_pairs), 17)
        self.assertEqual({pair.name for pair in surface_pairs}, {
            "webapp_offer__webapp_request",
            "webapp_offer__telegram_request",
            "telegram_offer__webapp_request",
            "telegram_offer__telegram_request",
        })
        self.assertEqual({outage.outage_id for outage in outage_classes}, {
            "stable",
            "short_under_2m",
            "medium_around_60m",
        })
        self.assertEqual(len(scenarios), 17 * 4 * 3)
        self.assertEqual({scenario.offer_home_server for scenario in scenarios}, {"iran", "foreign"})
        self.assertTrue(all(scenario.expected_remote_delivery_policy for scenario in scenarios))
        medium = [scenario for scenario in scenarios if scenario.outage_id == "medium_around_60m"]
        self.assertTrue(all("skipped" in scenario.expected_remote_delivery_policy for scenario in medium))

    def test_cli_outputs_parseable_matrix_json(self):
        payload = delivery_matrix.build_matrix_payload()

        self.assertEqual(payload["schema_version"], delivery_matrix.MATRIX_SCHEMA_VERSION)
        self.assertEqual(payload["scenario_count"], 204)
        self.assertEqual(
            (payload["production_gate"] or {}).get("status"),
            "blocked_until_owner_staging_validation",
        )


if __name__ == "__main__":
    unittest.main()
