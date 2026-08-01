from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import hashlib
import pickle
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_full_matrix_v2_recovery_evidence as bridge_module
from core.physical_full_matrix_v2_recovery_evidence import (
    PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_SCHEMA,
    PhysicalFullMatrixV2RecoveryEvidenceConfig,
    PhysicalFullMatrixV2RecoveryEvidenceError,
    PhysicalFullMatrixV2RecoveryEvidenceInputs,
    PhysicalFullMatrixV2RecoveryEvidenceScope,
    VerifiedPhysicalFullMatrixV2RecoveryEvidence,
    mint_verified_physical_full_matrix_v2_recovery_evidence,
    require_verified_physical_full_matrix_v2_recovery_evidence,
)
from core.physical_wal_chunked_base_backup_blob_frontier_coverage import (
    PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope,
    PhysicalWalV2BlobObjectVersionSelector,
    build_physical_wal_v2_blob_object_version_coverage,
    derive_physical_wal_v2_blob_object_version_prefix,
    mint_physical_wal_chunked_base_backup_blob_frontier_coverage,
    verify_physical_wal_v2_blob_object_version_coverage,
)
from core.physical_wal_chunked_base_backup_remote_ack_bridge import (
    PhysicalWalChunkedBaseBackupRemoteAckScope,
    mint_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence,
)
from core.physical_wal_v2_remote_ack_coverage import (
    PhysicalWalV2RemoteAckCoverageScope,
    mint_physical_wal_v2_remote_ack_coverage,
)
from tests.test_physical_postgres_chunked_base_backup_target_recovery_preflight import (
    PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightTests as _TargetFixture,
)
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW, RECIPIENT, _nonce


def _public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class PhysicalFullMatrixV2RecoveryEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target_fixture = _TargetFixture(
            "test_exact_signed_target_wal_proof_and_readback_attestation_mint_opaque_evidence"
        )
        self.target_fixture.setUp()
        fixture = self.target_fixture
        self.base_scope = PhysicalWalChunkedBaseBackupRemoteAckScope(
            transfer_binding=fixture.evidence.binding,
            stream_generation_id="physical-wal-stream-20260731",
            baseline_generation_id=fixture.evidence.handoff.baseline_generation_id,
            lineage_sha256=fixture.evidence.handoff.lineage_sha256,
            database_system_identifier=fixture.evidence.handoff.database_system_identifier,
            timeline_id=fixture.evidence.handoff.timeline_id,
            wal_segment_size_bytes=fixture.evidence.handoff.wal_segment_size_bytes,
            baseline_wal_lsn=fixture.evidence.handoff.baseline_wal_lsn,
            wal_chain_start_lsn=fixture.evidence.handoff.wal_chain_start_lsn,
            base_backup_end_lsn=fixture.evidence.handoff.base_backup_end_lsn,
        )
        self.base = mint_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence(
            manifest=fixture.evidence.manifest,
            handoff_receipt=fixture.evidence.handoff,
            scope=self.base_scope,
            now=NOW,
        )
        self.owner = Ed25519PrivateKey.generate()
        self.owner_public = _public(self.owner)
        prefix = derive_physical_wal_v2_blob_object_version_prefix(
            transfer_binding=fixture.evidence.binding,
            lineage_sha256=fixture.evidence.handoff.lineage_sha256,
        )
        self.blob_objects = (
            PhysicalWalV2BlobObjectVersionSelector(
                ordinal=0,
                blob_id="full-matrix-v2-blob-000001",
                object_key=prefix + "000000000001.age",
                version_id="full-matrix-v2-blob-version-000001",
                ciphertext_sha256="1" * 64,
                ciphertext_bytes=111,
                plaintext_sha256="2" * 64,
                plaintext_bytes=101,
                age_recipient=RECIPIENT,
            ),
        )
        self.blob_scope = PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope(
            transfer_binding=fixture.evidence.binding,
            lineage_sha256=fixture.evidence.handoff.lineage_sha256,
            target_wal_lsn=fixture.target_lsn,
            required_blob_object_versions=self.blob_objects,
        )
        raw_blob = build_physical_wal_v2_blob_object_version_coverage(
            transfer_binding=fixture.evidence.binding,
            canonical_base_backup_manifest_sha256=hashlib.sha256(
                fixture.evidence.manifest.canonical_manifest
            ).hexdigest(),
            lineage_sha256=fixture.evidence.handoff.lineage_sha256,
            baseline_generation_id=fixture.evidence.handoff.baseline_generation_id,
            database_system_identifier=fixture.evidence.handoff.database_system_identifier,
            timeline_id=fixture.evidence.handoff.timeline_id,
            wal_segment_size_bytes=fixture.evidence.handoff.wal_segment_size_bytes,
            baseline_wal_lsn=fixture.evidence.handoff.baseline_wal_lsn,
            wal_chain_start_lsn=fixture.evidence.handoff.wal_chain_start_lsn,
            base_backup_end_lsn=fixture.evidence.handoff.base_backup_end_lsn,
            target_wal_lsn=fixture.target_lsn,
            coverage_id="full-matrix-v2-blob-coverage-0001",
            coverage_nonce="V" * 22,
            observed_at=NOW,
            expires_at=NOW + timedelta(seconds=50),
            objects=self.blob_objects,
            owner_signer=self.owner,
        )
        self.owner_coverage = verify_physical_wal_v2_blob_object_version_coverage(
            coverage=raw_blob,
            expected_owner_public_key=self.owner_public,
            now=NOW,
        )
        self.blob = mint_physical_wal_chunked_base_backup_blob_frontier_coverage(
            owner_coverage=self.owner_coverage,
            expected_owner_public_key=self.owner_public,
            manifest=fixture.evidence.manifest,
            handoff_receipt=fixture.evidence.handoff,
            scope=self.blob_scope,
            now=NOW,
        )
        self.coverage_scope = PhysicalWalV2RemoteAckCoverageScope(
            base_backup_scope=self.base_scope,
            target_lsn=fixture.target_lsn,
        )
        self.coverage = mint_physical_wal_v2_remote_ack_coverage(
            base_backup_evidence=self.base,
            blob_frontier_coverage=self.blob,
            blob_owner_coverage=self.owner_coverage,
            blob_expected_owner_public_key=self.owner_public,
            target_wal_continuity=fixture.continuity,
            target_wal_continuity_receipt=fixture.continuity_receipt,
            manifest=fixture.evidence.manifest,
            handoff_receipt=fixture.evidence.handoff,
            blob_scope=self.blob_scope,
            continuity_scope=fixture.continuity_scope,
            scope=self.coverage_scope,
            now=NOW,
        )
        self.attestation = fixture.attestation()
        self.target = fixture.mint(attestation=self.attestation)

    def tearDown(self) -> None:
        self.target_fixture.tearDown()

    def config(self, **changes: object) -> PhysicalFullMatrixV2RecoveryEvidenceConfig:
        fixture = self.target_fixture
        values: dict[str, object] = {
            "scope": PhysicalFullMatrixV2RecoveryEvidenceScope(
                transfer_binding=fixture.evidence.binding,
                target_replay_lsn=fixture.target_lsn,
            ),
            "target_recovery_config": fixture.config(),
            "blob_expected_owner_public_key": self.owner_public,
            "blob_frontier_scope": self.blob_scope,
            "target_wal_continuity_scope": fixture.continuity_scope,
            "remote_ack_coverage_scope": self.coverage_scope,
            "enabled": True,
        }
        values.update(changes)
        return PhysicalFullMatrixV2RecoveryEvidenceConfig(**values)

    def inputs(self, **changes: object) -> PhysicalFullMatrixV2RecoveryEvidenceInputs:
        fixture = self.target_fixture
        values: dict[str, object] = {
            "manifest": fixture.evidence.manifest,
            "handoff_receipt": fixture.evidence.handoff,
            "recovery_admission": fixture.admission,
            "target_wal_continuity_receipt": fixture.continuity_receipt,
            "target_wal_continuity": fixture.continuity,
            "recovery_readback_attestation": self.attestation,
            "target_recovery_preflight": self.target,
            "base_backup_evidence": self.base,
            "blob_owner_coverage": self.owner_coverage,
            "blob_frontier_coverage": self.blob,
            "remote_ack_coverage": self.coverage,
        }
        values.update(changes)
        return PhysicalFullMatrixV2RecoveryEvidenceInputs(**values)

    def mint(self, **changes: object) -> VerifiedPhysicalFullMatrixV2RecoveryEvidence:
        values: dict[str, object] = {
            "config": self.config(),
            "inputs": self.inputs(),
            "now": NOW,
        }
        values.update(changes)
        return mint_verified_physical_full_matrix_v2_recovery_evidence(**values)

    def test_revalidates_full_v2_recovery_and_coverage_chain(self) -> None:
        result = self.mint()
        self.assertEqual(PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_SCHEMA, result.schema)
        self.assertEqual(self.target_fixture.target_lsn, result.target_replay_lsn)
        self.assertEqual(self.coverage.object_version_set_sha256, result.object_version_set_sha256)
        self.assertEqual(self.target.readback_attestation_sha256, result.readback_attestation_sha256)
        self.assertFalse(result.recovery_authorized)
        self.assertFalse(result.promotion_authorized)
        self.assertFalse(result.execution_authorized)
        self.assertIs(result, require_verified_physical_full_matrix_v2_recovery_evidence(result, now=NOW))
        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
            pickle.dumps(result)

    def test_raw_or_forged_inputs_and_outer_capability_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            PhysicalFullMatrixV2RecoveryEvidenceError,
            "INPUTS_INVALID",
        ):
            self.mint(inputs=self.inputs(remote_ack_coverage={"v": 1}))
        result = self.mint()
        forged = replace(result, target_replay_lsn="0/2B00000")
        with self.assertRaisesRegex(
            PhysicalFullMatrixV2RecoveryEvidenceError,
            "CAPABILITY_REQUIRED",
        ):
            require_verified_physical_full_matrix_v2_recovery_evidence(forged, now=NOW)

    def test_scope_mismatch_and_stale_upstream_chain_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            PhysicalFullMatrixV2RecoveryEvidenceError,
            "SCOPE_MISMATCH",
        ):
            self.mint(
                config=self.config(
                    scope=PhysicalFullMatrixV2RecoveryEvidenceScope(
                        transfer_binding=self.target_fixture.evidence.binding,
                        target_replay_lsn="0/2B00000",
                    )
                )
            )
        result = self.mint()
        with self.assertRaisesRegex(
            PhysicalFullMatrixV2RecoveryEvidenceError,
            "UPSTREAM_INVALID",
        ):
            require_verified_physical_full_matrix_v2_recovery_evidence(
                result,
                now=NOW + timedelta(seconds=61),
            )

    def test_bridge_cross_pin_is_independent_of_upstream_verifier_shapes(self) -> None:
        result = self.mint()
        with patch.object(
            bridge_module,
            "require_verified_physical_wal_v2_remote_ack_coverage",
            return_value=self.coverage,
        ), patch.object(
            bridge_module,
            "require_verified_physical_postgres_chunked_base_backup_target_recovery_preflight",
            return_value=replace(self.target, target_replay_lsn="0/2B00000"),
        ), patch.object(
            bridge_module,
            "require_verified_physical_wal_chunked_base_backup_manifest",
            return_value=self.target_fixture.evidence.manifest,
        ), patch.object(
            bridge_module,
            "require_verified_physical_wal_chunked_base_backup_handoff_receipt",
            return_value=self.target_fixture.evidence.handoff,
        ):
            with self.assertRaisesRegex(
                PhysicalFullMatrixV2RecoveryEvidenceError,
                "CROSS_PIN_MISMATCH",
            ):
                mint_verified_physical_full_matrix_v2_recovery_evidence(
                    config=self.config(),
                    inputs=self.inputs(),
                    now=NOW,
                )
        self.assertIsInstance(result, VerifiedPhysicalFullMatrixV2RecoveryEvidence)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
