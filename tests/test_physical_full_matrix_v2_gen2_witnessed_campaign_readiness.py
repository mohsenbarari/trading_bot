"""Focused Gen2-only readiness tests using the real Gen2 ACK-owner fixture."""

from __future__ import annotations

from dataclasses import replace
import unittest

from core import physical_full_matrix_v2_gen2_witnessed_campaign_readiness as subject
from core.physical_full_matrix_v2_gen2_witnessed_ack_chain import (
    PhysicalFullMatrixV2Gen2WitnessedAckChainPins,
)
from core import physical_full_matrix_v2_witnessed_campaign_readiness as legacy_readiness
from tests.test_physical_full_matrix_v2_gen2_witnessed_ack_chain import (
    Gen2WitnessedAckChainFixture,
)
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW


def _sha(letter: str) -> str:
    return letter * 64


class PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Gen2WitnessedAckChainFixture()
        self.fixture.setUp()
        self.chain = self.fixture.mint_chain()
        self.binding = subject.PhysicalFullMatrixV2Gen2WitnessedCampaignBinding(
            **{
                name: getattr(self.chain, name)
                for name in PhysicalFullMatrixV2Gen2WitnessedAckChainPins.__dataclass_fields__
            }
        )
        self.config = subject.PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessConfig(
            binding=self.binding,
            gen2_witnessed_ack_chain_config=self.fixture.config,
            enabled=True,
        )
        self.inputs = subject.PhysicalFullMatrixV2Gen2WitnessedCampaignInputs(
            gen2_witnessed_ack_chain=self.chain,
        )

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def test_real_gen2_chain_is_the_only_positive_readiness_input(self) -> None:
        with self.fixture._all_owner_clocks():
            report = subject.assess_physical_full_matrix_v2_gen2_witnessed_campaign_readiness(
                self.config,
                self.inputs,
                now=NOW,
            )
            verified = (
                subject.mint_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness(
                    config=self.config,
                    inputs=self.inputs,
                    now=NOW,
                )
            )
            self.assertEqual(
                report,
                subject.require_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness(
                    verified,
                    now=NOW,
                ),
            )
        self.assertEqual(
            subject.PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED,
            report.status,
        )
        self.assertEqual(
            subject.PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_REQUIRED_READINESS_SLOTS,
            report.observed_slots,
        )
        self.assertFalse(report.execution_authorized)

    def test_every_binding_pin_including_base_v1_and_bridge_is_compared(self) -> None:
        for field_name, changed in (
            ("strict_v2_base_configuration_sha256", _sha("e")),
            ("strict_v1_parent_fence_generation", self.binding.strict_v1_parent_fence_generation + 1),
            ("strict_v1_v2_writer_term_bridge_parent_binding_sha256", _sha("f")),
        ):
            with self.subTest(field_name=field_name):
                config = replace(
                    self.config,
                    binding=replace(self.binding, **{field_name: changed}),
                )
                with self.fixture._all_owner_clocks():
                    report = subject.assess_physical_full_matrix_v2_gen2_witnessed_campaign_readiness(
                        config,
                        self.inputs,
                        now=NOW,
                    )
                self.assertEqual("blocked", report.status)
                self.assertIn(
                    "v2-gen2-witness-mediated-ack-chain-mismatch",
                    report.reason_codes,
                )

    def test_legacy_artifact_or_forged_readiness_has_no_fallback(self) -> None:
        historical = object.__new__(
            legacy_readiness.VerifiedPhysicalFullMatrixV2WitnessedCampaignReadiness
        )
        report = subject.assess_physical_full_matrix_v2_gen2_witnessed_campaign_readiness(
            self.config,
            replace(self.inputs, legacy_artifacts=(historical,)),
            now=NOW,
        )
        self.assertEqual("blocked", report.status)
        self.assertEqual(("legacy-gen1-artifact-rejected",), report.reason_codes)
        forged = object.__new__(
            subject.VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessError,
            "CAPABILITY_REQUIRED",
        ):
            subject.require_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness(
                forged
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
