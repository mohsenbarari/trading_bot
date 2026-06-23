import unittest
from types import SimpleNamespace

from api.routers.trades import (
    TRADE_UNAVAILABLE_DETAIL,
    TradeExecutionPlanError,
    _build_trade_execution_plan,
    _build_trade_message_bundle,
    _build_trade_notification_message,
)
from models.customer_relation import CustomerTier


def make_user(user_id: int, account_name: str):
    return SimpleNamespace(id=user_id, account_name=account_name, mobile_number=f"09{user_id:09d}")


class TradeExecutionSeamTests(unittest.TestCase):
    def test_execution_plan_collapses_standard_direct_trade_to_two_nodes(self):
        seller = make_user(9, "seller")
        buyer = make_user(5, "buyer")

        plan = _build_trade_execution_plan(
            offer_user_id=seller.id,
            offer_user=seller,
            source_principal_user_id=seller.id,
            source_principal_user=seller,
            responder_principal_user_id=buyer.id,
            responder_principal_user=buyer,
            owner_user_id=buyer.id,
            owner_user=buyer,
        )

        self.assertFalse(plan.uses_customer_trade_chain)
        self.assertEqual([node.user_id for node in plan.nodes], [seller.id, buyer.id])
        self.assertIs(plan.nodes[0].user, seller)
        self.assertIs(plan.nodes[1].user, buyer)

    def test_execution_plan_builds_customer_chain_without_adjacent_duplicates(self):
        source_customer = make_user(41, "source_customer")
        shared_owner = make_user(78, "shared_owner")
        responder_customer = make_user(52, "responder_customer")

        plan = _build_trade_execution_plan(
            offer_user_id=source_customer.id,
            offer_user=source_customer,
            source_principal_user_id=shared_owner.id,
            source_principal_user=shared_owner,
            responder_principal_user_id=shared_owner.id,
            responder_principal_user=shared_owner,
            owner_user_id=responder_customer.id,
            owner_user=responder_customer,
        )

        self.assertTrue(plan.uses_customer_trade_chain)
        self.assertEqual([node.user_id for node in plan.nodes], [41, 78, 52])
        self.assertIs(plan.nodes[1].user, shared_owner)

    def test_execution_plan_routes_customer_tiers_only_through_their_owner(self):
        source_user = make_user(10, "source_user")
        responder_user = make_user(20, "responder_user")
        source_customer = make_user(41, "source_customer")
        source_owner = make_user(78, "source_owner")
        responder_customer = make_user(52, "responder_customer")
        responder_owner = make_user(88, "responder_owner")
        shared_owner = make_user(90, "shared_owner")

        cases = (
            {
                "name": "tier1_source_customer_with_user",
                "offer_user": source_customer,
                "source_principal": source_owner,
                "responder_principal": responder_user,
                "owner_user": responder_user,
                "customer_owners": {source_customer.id: source_owner.id},
                "expected": [source_customer.id, source_owner.id, responder_user.id],
            },
            {
                "name": "tier2_source_customer_with_user",
                "offer_user": source_customer,
                "source_principal": source_owner,
                "responder_principal": responder_user,
                "owner_user": responder_user,
                "customer_owners": {source_customer.id: source_owner.id},
                "expected": [source_customer.id, source_owner.id, responder_user.id],
            },
            {
                "name": "user_with_tier1_responder_customer",
                "offer_user": source_user,
                "source_principal": source_user,
                "responder_principal": responder_owner,
                "owner_user": responder_customer,
                "customer_owners": {responder_customer.id: responder_owner.id},
                "expected": [source_user.id, responder_owner.id, responder_customer.id],
            },
            {
                "name": "user_with_tier2_responder_customer",
                "offer_user": source_user,
                "source_principal": source_user,
                "responder_principal": responder_owner,
                "owner_user": responder_customer,
                "customer_owners": {responder_customer.id: responder_owner.id},
                "expected": [source_user.id, responder_owner.id, responder_customer.id],
            },
            {
                "name": "customers_with_different_owners",
                "offer_user": source_customer,
                "source_principal": source_owner,
                "responder_principal": responder_owner,
                "owner_user": responder_customer,
                "customer_owners": {
                    source_customer.id: source_owner.id,
                    responder_customer.id: responder_owner.id,
                },
                "expected": [source_customer.id, source_owner.id, responder_owner.id, responder_customer.id],
            },
            {
                "name": "customers_with_same_owner",
                "offer_user": source_customer,
                "source_principal": shared_owner,
                "responder_principal": shared_owner,
                "owner_user": responder_customer,
                "customer_owners": {
                    source_customer.id: shared_owner.id,
                    responder_customer.id: shared_owner.id,
                },
                "expected": [source_customer.id, shared_owner.id, responder_customer.id],
            },
        )

        for case in cases:
            with self.subTest(case=case["name"]):
                plan = _build_trade_execution_plan(
                    offer_user_id=case["offer_user"].id,
                    offer_user=case["offer_user"],
                    source_principal_user_id=case["source_principal"].id,
                    source_principal_user=case["source_principal"],
                    responder_principal_user_id=case["responder_principal"].id,
                    responder_principal_user=case["responder_principal"],
                    owner_user_id=case["owner_user"].id,
                    owner_user=case["owner_user"],
                )
                node_ids = [node.user_id for node in plan.nodes]

                self.assertEqual(node_ids, case["expected"])
                for customer_id, owner_id in case["customer_owners"].items():
                    customer_index = node_ids.index(customer_id)
                    neighbours = [
                        neighbour_id
                        for neighbour_id in (
                            node_ids[customer_index - 1] if customer_index > 0 else None,
                            node_ids[customer_index + 1] if customer_index + 1 < len(node_ids) else None,
                        )
                        if neighbour_id is not None
                    ]
                    self.assertEqual(neighbours, [owner_id])

    def test_execution_plan_rejects_missing_or_invalid_nodes(self):
        buyer = make_user(5, "buyer")

        with self.assertRaises(TradeExecutionPlanError) as exc_info:
            _build_trade_execution_plan(
                offer_user_id=None,
                offer_user=None,
                source_principal_user_id=None,
                source_principal_user=None,
                responder_principal_user_id=buyer.id,
                responder_principal_user=buyer,
                owner_user_id=buyer.id,
                owner_user=buyer,
            )

        self.assertEqual(str(exc_info.exception), TRADE_UNAVAILABLE_DETAIL)

    def test_notification_message_shows_owner_and_hides_non_owner_for_customer_audiences(self):
        for tier in (CustomerTier.TIER_1, CustomerTier.TIER_2):
            with self.subTest(tier=tier.value):
                customer_relation = SimpleNamespace(customer_tier=tier, owner_user_id=78)

                message = _build_trade_notification_message(
                    trade_emoji="🟢",
                    trade_type_label="خرید",
                    trade_price=50_800,
                    trade_quantity=23,
                    commodity_name="ربع",
                    trade_number=10012,
                    trade_datetime="1405/03/27   16:45",
                    counterparty_name="سرگروه",
                    audience_user_id=52,
                    counterparty_user_id=78,
                    customer_relation_map={52: customer_relation},
                    trade_path_summary="مالک ↔ مشتری",
                    offer_notes="تحویل امروز  ",
                )

                self.assertIn("🟢 خرید", message)
                self.assertIn("💰 فی: 50,800", message)
                self.assertIn("📦 تعداد: 23", message)
                self.assertIn("🏷️ کالا: ربع", message)
                self.assertIn("🔢 شماره معامله: 10012", message)
                self.assertIn("🧭 مسیر: مالک ↔ مشتری", message)
                self.assertIn("📝 توضیحات: تحویل امروز", message)
                self.assertIn("👤 طرف معامله: سرگروه", message)

                external_message = _build_trade_notification_message(
                    trade_emoji="🟢",
                    trade_type_label="خرید",
                    trade_price=50_800,
                    trade_quantity=23,
                    commodity_name="ربع",
                    trade_number=10012,
                    trade_datetime="1405/03/27   16:45",
                    counterparty_name="طرف بیرونی",
                    audience_user_id=52,
                    counterparty_user_id=99,
                    customer_relation_map={52: customer_relation},
                )
                self.assertNotIn("👤 طرف معامله", external_message)

    def test_message_bundle_preserves_telegram_and_notification_text_contract(self):
        responder_msg, offer_owner_msg, responder_notif, owner_notif = _build_trade_message_bundle(
            responder_trade_emoji="🟢",
            responder_trade_label="خرید",
            offer_trade_emoji="🔴",
            offer_trade_label="فروش",
            trade_price=50_800,
            trade_quantity=23,
            commodity_name="ربع",
            trade_number=10012,
            trade_datetime="1405/03/27   16:45",
            offer_user_name="محسن",
            responder_user_name="شاهین",
            customer_relation_map={},
            trade_path_summary="مالک ↔ مشتری سطح ۲",
            offer_notes="تسویه فوری",
        )

        self.assertIn("🟢 <b>خرید</b>", responder_msg)
        self.assertIn("👤 طرف معامله: محسن", responder_msg)
        self.assertIn("🔴 <b>فروش</b>", offer_owner_msg)
        self.assertIn("👤 طرف معامله: شاهین", offer_owner_msg)
        self.assertIn("👤 طرف معامله: محسن", responder_notif)
        self.assertIn("👤 طرف معامله: شاهین", owner_notif)
        self.assertIn("🧭 مسیر: مالک ↔ مشتری سطح ۲", responder_msg)
        self.assertIn("🧭 مسیر: مالک ↔ مشتری سطح ۲", responder_notif)
        self.assertIn("📝 توضیحات: تسویه فوری", responder_msg)
        self.assertIn("📝 توضیحات: تسویه فوری", offer_owner_msg)
        self.assertIn("📝 توضیحات: تسویه فوری", responder_notif)
        self.assertIn("📝 توضیحات: تسویه فوری", owner_notif)


if __name__ == "__main__":
    unittest.main()
