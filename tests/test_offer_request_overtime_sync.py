"""Stage 7: overtime OfferRequest sync payload and terminal guard set."""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from api.routers.sync import TERMINAL_OFFER_REQUEST_STATUSES
from core.events import _offer_request_sync_payload
from models.offer_request import OVERTIME_TERMINAL_STATUSES, OfferRequestStatus, OfferRequestWorkflow


class OfferRequestOvertimeSyncTests(unittest.TestCase):
    def test_sync_payload_includes_overtime_workflow_fields(self):
        presented = datetime(2026, 8, 5, 12, 2, 30, tzinfo=timezone.utc)
        deadline = datetime(2026, 8, 5, 12, 3, 0, tzinfo=timezone.utc)
        target = SimpleNamespace(
            id=11,
            version_id=3,
            request_public_id="req_ot_sync",
            request_home_server="iran",
            local_offer_id=7,
            offer_public_id="ofr_7",
            requester_user_id=9,
            actor_user_id=9,
            request_source_surface=SimpleNamespace(value="webapp"),
            request_source_server="foreign",
            requested_quantity=4,
            idempotency_key="ot:sync:1",
            workflow_kind=OfferRequestWorkflow.OVERTIME,
            offer_owner_user_id=1,
            queue_sequence=2,
            presented_at=presented,
            decision_deadline_at=deadline,
            decided_by_user_id=None,
            terminal_reason=None,
            telegram_message_id=555,
            telegram_delivery_job_id=99,
            received_at=presented,
            decided_at=None,
            result_status=OfferRequestStatus.OVERTIME_PRESENTED,
            public_failure_code=None,
            public_failure_message=None,
            internal_failure_code=None,
            internal_failure_context=None,
            resulting_trade=None,
            resulting_trade_id=None,
            customer_relation_id=None,
            customer_owner_user_id=None,
            customer_tier_snapshot=None,
            customer_management_name_snapshot=None,
            customer_commission_rate_snapshot=None,
            customer_commission_context=None,
            archived=False,
            created_at=presented,
            updated_at=None,
        )
        payload = _offer_request_sync_payload(target)
        self.assertEqual(payload["workflow_kind"], "overtime")
        self.assertEqual(payload["request_public_id"], "req_ot_sync")
        self.assertEqual(payload["offer_owner_user_id"], 1)
        self.assertEqual(payload["queue_sequence"], 2)
        self.assertEqual(payload["telegram_message_id"], 555)
        self.assertNotIn("telegram_delivery_job_id", payload)
        self.assertEqual(payload["result_status"], "overtime_presented")
        self.assertEqual(payload["version_id"], 3)

    def test_sync_terminal_set_includes_overtime_outcomes(self):
        for status in OVERTIME_TERMINAL_STATUSES:
            with self.subTest(status=status):
                self.assertIn(status.value, TERMINAL_OFFER_REQUEST_STATUSES)
        self.assertIn("completed_trade", TERMINAL_OFFER_REQUEST_STATUSES)
        # Nonterminals must remain mutable under version guards.
        for status in (
            OfferRequestStatus.OVERTIME_QUEUED,
            OfferRequestStatus.OVERTIME_DELIVERING,
            OfferRequestStatus.OVERTIME_PRESENTED,
        ):
            self.assertNotIn(status.value, TERMINAL_OFFER_REQUEST_STATUSES)


if __name__ == "__main__":
    unittest.main()
