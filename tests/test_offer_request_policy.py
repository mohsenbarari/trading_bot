import unittest

from core.offer_request_policy import (
    OfferRequestVisibility,
    map_legacy_expire_reason,
    sanitize_offer_request_payload,
)


class OfferRequestPolicyTests(unittest.TestCase):
    def test_public_link_payload_does_not_expose_sensitive_request_metadata(self):
        payload = {
            "offer_public_id": "ofr_public",
            "requested_quantity": 20,
            "result_status": "rejected_business_rule",
            "public_failure_code": "limit",
            "public_failure_message": "درخواست قابل انجام نیست.",
            "requester_user_id": 5,
            "actor_user_id": 6,
            "request_source_surface": "webapp",
            "request_source_server": "iran",
            "mobile_number": "09120000000",
            "customer_relation_id": 7,
            "customer_management_name_snapshot": "VIP",
            "internal_failure_code": "raw_block_rule",
            "internal_failure_context": {"blocked_by": 10},
        }

        public_payload = sanitize_offer_request_payload(payload, OfferRequestVisibility.PUBLIC_LINK)

        self.assertEqual(public_payload["offer_public_id"], "ofr_public")
        self.assertEqual(public_payload["requested_quantity"], 20)
        self.assertEqual(public_payload["result_status"], "rejected_business_rule")
        for forbidden in {
            "requester_user_id",
            "actor_user_id",
            "request_source_surface",
            "request_source_server",
            "mobile_number",
            "customer_relation_id",
            "customer_management_name_snapshot",
            "internal_failure_code",
            "internal_failure_context",
        }:
            self.assertNotIn(forbidden, public_payload)

    def test_owner_and_admin_audit_visibility_are_explicitly_gated(self):
        payload = {
            "offer_public_id": "ofr_public",
            "requester_user_id": 5,
            "request_source_surface": "telegram_bot",
            "request_source_server": "foreign",
            "customer_relation_id": 7,
            "internal_failure_code": "db_timeout",
            "internal_failure_context": {"trace": "redacted"},
        }

        owner_payload = sanitize_offer_request_payload(payload, OfferRequestVisibility.OWNER)
        admin_payload = sanitize_offer_request_payload(payload, OfferRequestVisibility.ADMIN_AUDIT)

        self.assertEqual(owner_payload["requester_user_id"], 5)
        self.assertEqual(owner_payload["request_source_server"], "foreign")
        self.assertEqual(owner_payload["customer_relation_id"], 7)
        self.assertNotIn("internal_failure_context", owner_payload)
        self.assertEqual(admin_payload["internal_failure_code"], "db_timeout")
        self.assertEqual(admin_payload["internal_failure_context"], {"trace": "redacted"})

    def test_legacy_expire_reason_mapping_does_not_fabricate_missing_metadata(self):
        self.assertEqual(map_legacy_expire_reason("time_limit").normalized_category, "lifetime_expiry")
        self.assertEqual(map_legacy_expire_reason("market_closed").default_source_surface, "system")
        manual = map_legacy_expire_reason("manual")
        self.assertEqual(manual.normalized_category, "owner_action")
        self.assertEqual(manual.default_source_surface, "legacy_unknown")
        self.assertFalse(manual.metadata_known)
        unknown = map_legacy_expire_reason("custom_old_reason")
        self.assertEqual(unknown.normalized_category, "legacy_unknown")
        self.assertEqual(unknown.default_source_surface, "legacy_unknown")


if __name__ == "__main__":
    unittest.main()
