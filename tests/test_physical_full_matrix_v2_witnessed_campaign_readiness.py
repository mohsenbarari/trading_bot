from __future__ import annotations

import ast
from contextlib import contextmanager
from dataclasses import fields, replace
from datetime import datetime, timedelta
from pathlib import Path
import pickle
import unittest
from unittest.mock import patch

from core import physical_full_matrix_v2_witnessed_ack_chain as witnessed_chain
from core import physical_full_matrix_v2_witnessed_campaign_readiness as readiness
from core import physical_wal_v2_witness_roundtrip_strict_writer_response as strict
from tests import test_physical_full_matrix_v2_witnessed_ack_chain as chain_tests
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v2_witnessed_campaign_readiness.py"
)

_BINDING_FIELDS = frozenset(
    {
        "chain_sha256",
        "campaign_id",
        "release_sha",
        "source_site",
        "destination_site",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "writer_holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witnessed_term_proof_sha256",
        "context_sha256",
        "context_certificate_sha256",
        "source_request_sha256",
        "source_envelope_sha256",
        "destination_receipt_sha256",
        "durable_ledger_entry_sha256",
        "receiver_recovery_evidence_sha256",
        "receiver_replay_lsn",
        "ir_durable_assertion_sha256",
        "roundtrip_attestation_sha256",
        "target_recovery_evidence_sha256",
        "readback_attestation_sha256",
        "stage_receipt_sha256",
        "witness_transition_id",
        "activation_mode",
        "activation_stream_generation_id",
        "activation_route_artifact_sha256",
        "activation_source_cutover_attestation_sha256",
        "activation_receiver_permit_sha256",
        "witness_sequence",
        "witness_ledger_entry_sha256",
        "witness_ledger_previous_head_sha256",
        "witness_ledger_binding_sha256",
        "roundtrip_configuration_sha256",
        "strict_observation_sha256",
        "strict_runtime_commit_receipt_sha256",
        "strict_commit_id",
        "strict_local_commit_record_id",
        "strict_local_response_id",
        "strict_attestation_consumption_id",
        "strict_committed_at",
        "target_lsn",
        "object_version_set_sha256",
    }
)


class PhysicalFullMatrixV2WitnessedCampaignReadinessTests(unittest.TestCase):
    """Readiness consumes only a freshly projected, real Witness V2 chain."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._bridge_case = chain_tests.PhysicalFullMatrixV2WitnessedAckChainTests(
            "runTest"
        )
        cls._bridge_case.setUpClass()
        cls.chain = cls._bridge_case._mint()
        binding_values = {
            item.name: getattr(cls.chain, item.name)
            for item in fields(readiness.PhysicalFullMatrixV2WitnessedCampaignBinding)
        }
        cls.binding = readiness.PhysicalFullMatrixV2WitnessedCampaignBinding(
            **binding_values
        )
        cls.config = readiness.PhysicalFullMatrixV2WitnessedCampaignReadinessConfig(
            binding=cls.binding,
            witnessed_ack_chain_config=cls._bridge_case.config,
            enabled=True,
        )
        cls.inputs = readiness.PhysicalFullMatrixV2WitnessedCampaignInputs(
            witnessed_ack_chain=cls.chain,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._bridge_case.tearDownClass()

    @contextmanager
    def _bridge_owner_context(self, *, now: datetime = NOW):
        """Supply the same local strict-writer facts used by the real bridge."""

        with patch.object(strict, "_trusted_now", return_value=now), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(
                self._bridge_case.term,
                self._bridge_case.activation,
                self._bridge_case.live,
            ),
        ):
            yield

    def _assess(self, *, config=None, inputs=None, now: datetime = NOW):
        with self._bridge_owner_context(now=now):
            return readiness.assess_physical_full_matrix_v2_witnessed_campaign_readiness(
                self.config if config is None else config,
                self.inputs if inputs is None else inputs,
                now=now,
            )

    @staticmethod
    def _alternate(field_name: str, value: object) -> object:
        if field_name in {"source_site", "destination_site", "writer_holder_site"}:
            return "webapp_ir" if value == "webapp_fi" else "webapp_fi"
        if field_name == "campaign_id":
            return "physical-base-20260732"
        if field_name == "release_sha":
            return "f" * 40 if value != "f" * 40 else "e" * 40
        if field_name.endswith("_sha256"):
            return "f" * 64 if value != "f" * 64 else "e" * 64
        if field_name in {"writer_epoch", "witness_sequence"}:
            return int(value) + 1
        if field_name == "strict_committed_at":
            return value + timedelta(seconds=1)
        if field_name.endswith("_lsn"):
            return "0/DEADBEEF" if value != "0/DEADBEEF" else "0/BADC0DE"
        return f"{value}-tampered"

    def test_real_chain_is_projected_as_local_non_authorizing_evidence(self) -> None:
        with patch.object(
            readiness,
            "project_verified_physical_full_matrix_v2_witnessed_ack_chain",
            wraps=witnessed_chain.project_verified_physical_full_matrix_v2_witnessed_ack_chain,
        ) as project, self._bridge_owner_context():
            report = readiness.assess_physical_full_matrix_v2_witnessed_campaign_readiness(
                self.config,
                self.inputs,
                now=NOW,
            )

        self.assertEqual(
            readiness.PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED,
            report.status,
        )
        self.assertEqual(
            readiness.PHYSICAL_FULL_MATRIX_V2_WITNESSED_REQUIRED_READINESS_SLOTS,
            report.observed_slots,
        )
        self.assertEqual((), report.reason_codes)
        self.assertEqual(self.binding.campaign_id, report.campaign_id)
        self.assertEqual(self.binding.release_sha, report.release_sha)
        self.assertFalse(report.external_execution_authorized)
        self.assertFalse(report.promotion_authorized)
        self.assertFalse(report.execution_authorized)
        project.assert_called_once_with(
            self.chain,
            config=self._bridge_case.config,
            now=NOW,
        )

        with self._bridge_owner_context():
            verified = readiness.mint_verified_physical_full_matrix_v2_witnessed_campaign_readiness(
                config=self.config,
                inputs=self.inputs,
                now=NOW,
            )
            self.assertEqual(report, verified.report)
            self.assertIs(
                verified.report,
                readiness.require_verified_physical_full_matrix_v2_witnessed_campaign_readiness(
                    verified, now=NOW
                ),
            )
        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
            pickle.dumps(verified)

    def test_every_bridge_projection_pin_is_cross_checked(self) -> None:
        self.assertEqual(
            _BINDING_FIELDS,
            {item.name for item in fields(readiness.PhysicalFullMatrixV2WitnessedCampaignBinding)},
        )
        with self._bridge_owner_context():
            projection = witnessed_chain.project_verified_physical_full_matrix_v2_witnessed_ack_chain(
                self.chain,
                config=self._bridge_case.config,
                now=NOW,
            )

        for field_name in sorted(_BINDING_FIELDS):
            with self.subTest(field_name=field_name), patch.object(
                readiness,
                "project_verified_physical_full_matrix_v2_witnessed_ack_chain",
                return_value=replace(
                    projection,
                    **{
                        field_name: self._alternate(
                            field_name, getattr(projection, field_name)
                        )
                    },
                ),
            ):
                report = readiness.assess_physical_full_matrix_v2_witnessed_campaign_readiness(
                    self.config,
                    self.inputs,
                    now=NOW,
                )
            self.assertEqual(
                readiness.PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_STATUS_BLOCKED,
                report.status,
            )
            self.assertEqual(
                ("v2-witness-mediated-ack-chain-mismatch",), report.reason_codes
            )

    def test_raw_legacy_stale_and_tampered_capabilities_fail_closed(self) -> None:
        raw = self._assess(
            inputs=replace(self.inputs, witnessed_ack_chain={"raw": "chain"})
        )
        self.assertEqual(
            readiness.PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_STATUS_BLOCKED,
            raw.status,
        )
        self.assertEqual(("v2-witness-mediated-ack-chain-mismatch",), raw.reason_codes)

        with patch.object(
            readiness,
            "project_verified_physical_full_matrix_v2_witnessed_ack_chain",
        ) as project:
            legacy = readiness.assess_physical_full_matrix_v2_witnessed_campaign_readiness(
                self.config,
                replace(self.inputs, legacy_runner_artifacts=("retired-v1",)),
                now=NOW,
            )
        project.assert_not_called()
        self.assertEqual(("legacy-v1-artifact-rejected",), legacy.reason_codes)

        stale = self._assess(now=NOW + timedelta(seconds=16))
        self.assertEqual(
            readiness.PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_STATUS_BLOCKED,
            stale.status,
        )
        self.assertEqual(("v2-witness-mediated-ack-chain-mismatch",), stale.reason_codes)

        with self._bridge_owner_context():
            verified = readiness.mint_verified_physical_full_matrix_v2_witnessed_campaign_readiness(
                config=self.config,
                inputs=self.inputs,
                now=NOW,
            )
        object.__setattr__(verified.report, "campaign_id", "physical-base-tampered")
        with self.assertRaisesRegex(
            readiness.PhysicalFullMatrixV2WitnessedCampaignReadinessError,
            "CAPABILITY_TAMPERED",
        ):
            readiness.require_verified_physical_full_matrix_v2_witnessed_campaign_readiness(
                verified
            )

        with self._bridge_owner_context():
            authority_tampered = (
                readiness.mint_verified_physical_full_matrix_v2_witnessed_campaign_readiness(
                    config=self.config,
                    inputs=self.inputs,
                    now=NOW,
                )
            )
        object.__setattr__(authority_tampered.report, "execution_authorized", True)
        with self.assertRaisesRegex(
            readiness.PhysicalFullMatrixV2WitnessedCampaignReadinessError,
            "CAPABILITY_TAMPERED",
        ):
            readiness.require_verified_physical_full_matrix_v2_witnessed_campaign_readiness(
                authority_tampered
            )

    def test_default_off_and_new_generation_static_fences(self) -> None:
        disabled = self._assess(config=replace(self.config, enabled=False))
        self.assertEqual(
            readiness.PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_STATUS_BLOCKED,
            disabled.status,
        )
        self.assertEqual(("driver-disabled",), disabled.reason_codes)

        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertIn("core.physical_full_matrix_v2_witnessed_ack_chain", imported)
        for forbidden in (
            "core.physical_full_matrix_campaign_readiness",
            "core.physical_full_matrix_v2_campaign_readiness",
            "core.physical_full_matrix_v2_ack_chain",
            "core.physical_wal_v2_remote_ack_receiver_ledger",
            "core.physical_wal_v2_witness_roundtrip_delivery_contract",
            "socket",
            "subprocess",
            "requests",
            "boto3",
        ):
            self.assertNotIn(forbidden, imported)
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("connect(", source)
        self.assertNotIn("open(", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
