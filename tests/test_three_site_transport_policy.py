from __future__ import annotations

import unittest

from core.three_site_transport_policy import (
    DIRECT_FINLAND_TRANSPORT,
    IRAN_OBJECT_STORAGE_TRANSPORT,
    ThreeSiteTransportPolicyError,
    payload_transport_decision,
    verify_payload_transport,
)


class ThreeSiteTransportPolicyTests(unittest.TestCase):
    def test_finland_payloads_are_direct_in_both_directions(self):
        for source, destination in (
            ("bot_fi", "webapp_fi"),
            ("webapp-fi", "bot-fi"),
        ):
            decision = payload_transport_decision(source, destination)
            self.assertEqual(decision.required_transport, DIRECT_FINLAND_TRANSPORT)
            self.assertFalse(decision.object_storage_allowed)
            verify_payload_transport(
                source_role=source,
                destination_role=destination,
                transport=DIRECT_FINLAND_TRANSPORT,
                object_storage_used=False,
                arvan_endpoint_contacted=False,
            )

    def test_finland_payload_cannot_hairpin_through_iran(self):
        with self.assertRaisesRegex(
            ThreeSiteTransportPolicyError, "must not contact"
        ):
            verify_payload_transport(
                source_role="bot_fi",
                destination_role="webapp_fi",
                transport=DIRECT_FINLAND_TRANSPORT,
                object_storage_used=True,
                arvan_endpoint_contacted=True,
            )
        with self.assertRaisesRegex(
            ThreeSiteTransportPolicyError, "required regional"
        ):
            verify_payload_transport(
                source_role="bot_fi",
                destination_role="webapp_fi",
                transport=IRAN_OBJECT_STORAGE_TRANSPORT,
                object_storage_used=True,
                arvan_endpoint_contacted=True,
            )

    def test_iran_boundary_payloads_require_object_storage_evidence(self):
        for source, destination in (
            ("webapp_fi", "webapp_ir"),
            ("webapp_ir", "webapp_fi"),
            ("bot_fi", "witness"),
        ):
            decision = verify_payload_transport(
                source_role=source,
                destination_role=destination,
                transport=IRAN_OBJECT_STORAGE_TRANSPORT,
                object_storage_used=True,
                arvan_endpoint_contacted=True,
            )
            self.assertTrue(decision.object_storage_allowed)

    def test_invalid_or_identical_roles_are_rejected(self):
        for source, destination in (
            ("bot_fi", "bot_fi"),
            ("unknown", "webapp_fi"),
        ):
            with self.assertRaises(ThreeSiteTransportPolicyError):
                payload_transport_decision(source, destination)


if __name__ == "__main__":
    unittest.main()
