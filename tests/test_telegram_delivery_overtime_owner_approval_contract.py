"""Stage 8: overtime owner-approval queue contract and priority placement."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from core.offer_request_identity import generate_offer_request_public_id
from core.telegram_delivery_overtime_owner_approval_contract import (
    M23_OWNER_APPROVAL_TITLE,
    M25_OWNER_APPROVAL_DEADLINE,
    M27_OWNER_APPROVAL_QUANTITY_TEMPLATE,
    M28_OWNER_APPROVE_BUTTON,
    M28_OWNER_REJECT_BUTTON,
    OVERTIME_OWNER_APPROVAL_FRESHNESS_ACTIONS,
    build_overtime_owner_approval_callback_data,
    build_overtime_owner_approval_payload,
    build_overtime_owner_approval_text,
    overtime_owner_approval_delivery_deadline,
    overtime_owner_approval_feeder,
    parse_overtime_owner_approval_callback_data,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryPriority,
    TelegramFeederKind,
    feeder_internal_rank,
    priority_and_rank_for_action,
)
from core.telegram_delivery_relink_policy import (
    TelegramRelinkBehavior,
    telegram_delivery_relink_behavior,
)
from core.telegram_delivery_runtime_composition import (
    configured_telegram_delivery_freshness_registry,
    configured_telegram_delivery_lifecycle_registry,
)


CREATED = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


class OvertimeOwnerApprovalContractTests(unittest.TestCase):
    def test_priority_is_m0_rank_1_on_direct_feeder(self):
        self.assertEqual(
            priority_and_rank_for_action(
                TelegramDeliveryAction.OVERTIME_OWNER_APPROVAL
            ),
            (TelegramDeliveryPriority.M0, 1),
        )
        self.assertEqual(
            feeder_internal_rank(
                TelegramFeederKind.DIRECT,
                TelegramDeliveryAction.OVERTIME_OWNER_APPROVAL,
            ),
            0,
        )
        self.assertEqual(
            overtime_owner_approval_feeder(),
            TelegramFeederKind.DIRECT,
        )
        self.assertEqual(
            telegram_delivery_relink_behavior(
                TelegramDeliveryAction.OVERTIME_OWNER_APPROVAL
            ),
            TelegramRelinkBehavior.SUPPRESS_ON_RELINK,
        )
        self.assertIn(
            TelegramDeliveryAction.OVERTIME_OWNER_APPROVAL,
            OVERTIME_OWNER_APPROVAL_FRESHNESS_ACTIONS,
        )

    def test_primary_lane_registers_overtime_approval_freshness_and_lifecycle(self):
        freshness = configured_telegram_delivery_freshness_registry(
            channel_id=-1001234567890
        ).coverage("primary")
        lifecycle = configured_telegram_delivery_lifecycle_registry(
            channel_id=-1001234567890
        ).coverage("primary")
        self.assertTrue(freshness.complete)
        self.assertTrue(lifecycle.complete)
        self.assertIn(
            TelegramDeliveryAction.OVERTIME_OWNER_APPROVAL,
            freshness.required_actions,
        )

    def test_callback_payload_uses_opaque_public_id_never_sequential_pk(self):
        public_id = generate_offer_request_public_id()
        approve = build_overtime_owner_approval_callback_data(
            request_public_id=public_id,
            decision="approve",
        )
        reject = build_overtime_owner_approval_callback_data(
            request_public_id=public_id,
            decision="reject",
        )
        self.assertEqual(
            parse_overtime_owner_approval_callback_data(approve),
            (public_id, "approve"),
        )
        self.assertEqual(
            parse_overtime_owner_approval_callback_data(reject),
            (public_id, "reject"),
        )
        # The opaque random token may legitimately contain the digits "42";
        # prove that the callback round-trips the public identity and rejects
        # the sequential database id instead of making a probabilistic string
        # assertion.
        self.assertNotEqual(public_id, "42")
        self.assertIsNone(
            parse_overtime_owner_approval_callback_data(f"ota:not-a-req:approve")
        )
        with self.assertRaises(ValueError):
            build_overtime_owner_approval_callback_data(
                request_public_id="12",
                decision="approve",
            )

    def test_approved_copy_and_buttons_match_inventory(self):
        text = build_overtime_owner_approval_text(
            offer_text="🔴 فروش\n💰 فی: 100",
            requested_quantity=3,
            include_quantity_line=True,
        )
        self.assertIn(M23_OWNER_APPROVAL_TITLE, text)
        self.assertIn(M25_OWNER_APPROVAL_DEADLINE, text)
        self.assertIn(
            M27_OWNER_APPROVAL_QUANTITY_TEMPLATE.format(count=3),
            text,
        )
        payload = build_overtime_owner_approval_payload(
            chat_id=999001,
            request_public_id=generate_offer_request_public_id(),
            offer_text="🔴 فروش",
        )
        buttons = payload["reply_markup"]["inline_keyboard"][0]
        self.assertEqual(buttons[0]["text"], M28_OWNER_APPROVE_BUTTON)
        self.assertEqual(buttons[1]["text"], M28_OWNER_REJECT_BUTTON)
        self.assertEqual(payload["parse_mode"], "Markdown")

    def test_delivery_deadline_is_offer_final_end(self):
        offer = SimpleNamespace(
            created_at=CREATED.replace(tzinfo=None),
            overtime_minutes_snapshot=5,
        )
        deadline = overtime_owner_approval_delivery_deadline(
            offer,
            normal_lifetime_minutes=2,
        )
        self.assertEqual(
            deadline,
            CREATED + timedelta(minutes=7),
        )


if __name__ == "__main__":
    unittest.main()
