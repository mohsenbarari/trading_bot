"""Contract tests for the bidirectional retired direct-transport fence."""

from __future__ import annotations

import unittest

from core.legacy_direct_fi_ir_transport_fence import (
    DIRECT_FI_IR_TRANSPORT_RETIREMENT_REASON,
    LegacyDirectFiIrTransportRetiredError,
    assert_legacy_direct_fi_ir_transport_retired,
    blocked_legacy_direct_fi_ir_transport_payload,
)


class LegacyDirectFiIrTransportFenceContractTests(unittest.TestCase):
    def test_reason_explicitly_covers_both_directions_and_positive_controls(self) -> None:
        self.assertIn("FI-to-IR", DIRECT_FI_IR_TRANSPORT_RETIREMENT_REASON)
        self.assertIn("IR-to-FI", DIRECT_FI_IR_TRANSPORT_RETIREMENT_REASON)
        self.assertIn("Object Storage", DIRECT_FI_IR_TRANSPORT_RETIREMENT_REASON)
        self.assertIn("Witness", DIRECT_FI_IR_TRANSPORT_RETIREMENT_REASON)

    def test_guard_fails_closed_with_component_and_operation_only(self) -> None:
        with self.assertRaisesRegex(
            LegacyDirectFiIrTransportRetiredError,
            r"FI-to-IR and IR-to-FI transport is retired.*transport-test \(probe\)",
        ):
            assert_legacy_direct_fi_ir_transport_retired(
                component="transport-test",
                operation="probe",
            )

    def test_blocked_payload_is_stable_and_redacted(self) -> None:
        self.assertEqual(
            blocked_legacy_direct_fi_ir_transport_payload(component="transport-test"),
            {
                "status": "blocked_legacy_direct_fi_ir_transport_retired",
                "component": "transport-test",
                "error": DIRECT_FI_IR_TRANSPORT_RETIREMENT_REASON,
                "error_class": "LegacyDirectFiIrTransportRetiredError",
            },
        )


if __name__ == "__main__":
    unittest.main()
