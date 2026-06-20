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

    def test_notification_message_hides_counterparty_for_tier2_audience(self):
        tier2_relation = SimpleNamespace(customer_tier=CustomerTier.TIER_2)

        message = _build_trade_notification_message(
            trade_emoji="🟢",
            trade_type_label="خرید",
            trade_price=50_800,
            trade_quantity=23,
            commodity_name="ربع",
            trade_number=10012,
            trade_datetime="1405/03/27   16:45",
            counterparty_name="محسن",
            audience_user_id=52,
            customer_relation_map={52: tier2_relation},
            trade_path_summary="مالک ↔ مشتری سطح ۲",
            offer_notes="تحویل امروز  ",
        )

        self.assertIn("🟢 خرید", message)
        self.assertIn("💰 فی: 50,800", message)
        self.assertIn("📦 تعداد: 23", message)
        self.assertIn("🏷️ کالا: ربع", message)
        self.assertIn("🔢 شماره معامله: 10012", message)
        self.assertIn("🧭 مسیر: مالک ↔ مشتری سطح ۲", message)
        self.assertIn("📝 توضیحات: تحویل امروز", message)
        self.assertNotIn("👤 طرف معامله", message)

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
