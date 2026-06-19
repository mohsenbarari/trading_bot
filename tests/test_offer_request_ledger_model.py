import unittest

from models.offer_request import OfferRequest, OfferRequestSourceSurface, OfferRequestStatus


class OfferRequestLedgerModelTests(unittest.TestCase):
    def test_model_has_required_columns_indexes_and_status_values(self):
        columns = OfferRequest.__table__.columns
        for column_name in {
            "local_offer_id",
            "offer_public_id",
            "requester_user_id",
            "actor_user_id",
            "request_source_surface",
            "request_source_server",
            "requested_quantity",
            "idempotency_key",
            "received_at",
            "decided_at",
            "result_status",
            "public_failure_code",
            "public_failure_message",
            "internal_failure_code",
            "internal_failure_context",
            "resulting_trade_id",
            "customer_relation_id",
            "customer_owner_user_id",
            "customer_tier_snapshot",
            "customer_management_name_snapshot",
            "customer_commission_rate_snapshot",
            "customer_commission_context",
            "archived",
            "version_id",
        }:
            self.assertIn(column_name, columns)

        self.assertEqual(
            {status.value for status in OfferRequestStatus},
            {
                "received",
                "authorized",
                "rejected_business_rule",
                "rejected_offer_expired",
                "rejected_lot_unavailable",
                "rejected_conflict",
                "completed_trade",
                "duplicate_replay",
                "failed_internal",
            },
        )
        self.assertEqual(
            {surface.value for surface in OfferRequestSourceSurface},
            {"webapp", "telegram_bot", "internal_forward"},
        )

        index_names = {index.name for index in OfferRequest.__table__.indexes}
        self.assertIn("ux_offer_requests_home_idempotency_key", index_names)
        unique_index = next(index for index in OfferRequest.__table__.indexes if index.name == "ux_offer_requests_home_idempotency_key")
        self.assertTrue(unique_index.unique)
        self.assertIn("idempotency_key IS NOT NULL", str(unique_index.dialect_options["postgresql"]["where"]))
        self.assertIn("ix_offer_requests_offer_public_id", index_names)
        self.assertIn("ix_offer_requests_result_status", index_names)


if __name__ == "__main__":
    unittest.main()
