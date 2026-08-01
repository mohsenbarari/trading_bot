from __future__ import annotations

import ast
from dataclasses import replace
from datetime import timedelta, timezone
import hashlib
import inspect
import os
from pathlib import Path
import pickle
from tempfile import TemporaryDirectory
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_postgres_chunked_base_backup_recovery_readback_attestation as attestation_module
from core.physical_postgres_chunked_base_backup_recovery_readback_attestation import (
    PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError,
    PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationScope,
    VerifiedPhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestation,
    build_physical_postgres_chunked_base_backup_recovery_readback_attestation,
    require_verified_physical_postgres_chunked_base_backup_recovery_readback_attestation,
    verify_physical_postgres_chunked_base_backup_recovery_readback_attestation,
)
from core.physical_wal_chunked_base_backup_receiver_receipt_ledger import (
    PhysicalWalChunkedBaseBackupReceiverReceiptLedgerConfig,
)
from core.physical_wal_chunked_base_backup_receiver_staging_runtime import (
    RootOwnedPhysicalWalChunkedBaseBackupReceiverStagingConfig,
    execute_root_owned_physical_wal_chunked_base_backup_receiver_staging,
)
from core.physical_wal_chunked_base_backup_recovery_admission import (
    PhysicalWalChunkedBaseBackupRecoveryAdmissionScope,
    RootOwnedPhysicalWalChunkedBaseBackupRecoveryAdmissionConfig,
    admit_root_owned_physical_wal_chunked_base_backup_recovery,
)
from tests.test_physical_wal_chunked_base_backup_receiver_staging_runtime import (
    NOW,
    _Decryptor,
    _EvidenceFixture,
    _ExactReceiver,
)


def _public(value: Ed25519PrivateKey) -> bytes:
    return value.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _zulu(value) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(prefix="postgres-readback-attestation-")
        root = Path(self.temporary.name)
        self.stage_root = root / "stage-root"
        self.ledger_root = root / "ledger-root"
        self.stage_root.mkdir(mode=0o700)
        self.ledger_root.mkdir(mode=0o700)
        os.chmod(self.stage_root, 0o700)
        os.chmod(self.ledger_root, 0o700)
        self.evidence = _EvidenceFixture()
        staged = execute_root_owned_physical_wal_chunked_base_backup_receiver_staging(
            RootOwnedPhysicalWalChunkedBaseBackupReceiverStagingConfig(
                staging_root=self.stage_root,
                receipt_ledger_config=PhysicalWalChunkedBaseBackupReceiverReceiptLedgerConfig(
                    ledger_root=self.ledger_root,
                    enabled=True,
                ),
                receiver_site="webapp_ir",
                enabled=True,
            ),
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            exact_version_receiver=_ExactReceiver(self.evidence.objects),
            age_decryptor=_Decryptor(),
            clock=lambda: NOW,
        )
        handoff = self.evidence.handoff
        admission_scope = PhysicalWalChunkedBaseBackupRecoveryAdmissionScope(
            transfer_binding=self.evidence.binding,
            baseline_generation_id=handoff.baseline_generation_id,
            database_system_identifier=handoff.database_system_identifier,
            timeline_id=handoff.timeline_id,
            wal_segment_size_bytes=handoff.wal_segment_size_bytes,
            baseline_wal_lsn=handoff.baseline_wal_lsn,
            wal_chain_start_lsn=handoff.wal_chain_start_lsn,
            base_backup_end_lsn=handoff.base_backup_end_lsn,
            completion_attestation_sha256=handoff.completion_attestation_sha256,
            legacy_route_binding_sha256=handoff.legacy_route_binding_sha256,
            witness_transition_id=handoff.witness_transition_id,
        )
        self.admission = admit_root_owned_physical_wal_chunked_base_backup_recovery(
            RootOwnedPhysicalWalChunkedBaseBackupRecoveryAdmissionConfig(
                staging_root=self.stage_root,
                receiver_site="webapp_ir",
                enabled=True,
            ),
            scope=admission_scope,
            manifest=self.evidence.manifest,
            handoff_receipt=handoff,
            staging_result=staged,
            now=NOW,
        )
        self.attester = Ed25519PrivateKey.generate()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def scope(
        self,
        **changes: object,
    ) -> PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationScope:
        handoff = self.evidence.handoff
        result = PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationScope(
            transfer_binding=self.evidence.binding,
            receiver_site="webapp_ir",
            lineage_sha256=handoff.lineage_sha256,
            baseline_generation_id=handoff.baseline_generation_id,
            database_system_identifier=handoff.database_system_identifier,
            timeline_id=handoff.timeline_id,
            wal_segment_size_bytes=handoff.wal_segment_size_bytes,
            baseline_wal_lsn=handoff.baseline_wal_lsn,
            wal_chain_start_lsn=handoff.wal_chain_start_lsn,
            base_backup_end_lsn=handoff.base_backup_end_lsn,
            completion_attestation_sha256=handoff.completion_attestation_sha256,
            witness_transition_id=handoff.witness_transition_id,
            witness_public_key_sha256=hashlib.sha256(handoff.witness_public_key).hexdigest(),
            expected_target_replay_lsn=handoff.base_backup_end_lsn,
        )
        return replace(result, **changes)

    def payload(self, **changes: object) -> dict[str, object]:
        binding = self.evidence.binding
        handoff = self.evidence.handoff
        admission = self.admission
        result: dict[str, object] = {
            "schema": "gold-trade-physical-postgres-chunked-base-backup-recovery-readback-v2",
            "status": "replay-evidence-observed",
            "observed_at": _zulu(NOW),
            "receiver_site": "webapp_ir",
            "source_site": binding.source_site,
            "destination_site": binding.destination_site,
            "campaign_id": binding.campaign_id,
            "release_sha": binding.release_sha,
            "route": {
                "binding_sha256": handoff.binding_sha256,
                "route_commitment_sha256": binding.route_commitment_sha256,
                "four_role_binding_sha256": binding.four_role_binding_sha256,
                "object_storage_namespace": binding.object_storage_namespace,
                "destination_age_recipient": binding.destination_age_recipient,
                "transport_plane": binding.transport_plane,
                "direct_webapp_transport": binding.direct_webapp_transport,
            },
            "writer_term": {
                "writer_holder_site": binding.writer_term.writer_holder_site,
                "writer_epoch": binding.writer_term.writer_epoch,
                "writer_lease_id": binding.writer_term.writer_lease_id,
                "witnessed_term_proof_sha256": binding.writer_term.witnessed_term_proof_sha256,
            },
            "stage": {
                "recovery_admission_scope_sha256": admission.scope_sha256,
                "stage_receipt_sha256": admission.stage_receipt_sha256,
                "stage_directory_name": admission.stage_directory_name,
                "receipt_id": admission.receipt_id,
                "receipt_nonce": admission.receipt_nonce,
                "manifest_id": admission.manifest_id,
                "manifest_sha256": admission.manifest_sha256,
                "session_sha256": admission.session_sha256,
                "finalization_permit_id": admission.finalization_permit_id,
                "finalization_permit_sha256": admission.finalization_permit_sha256,
                "committed_chunk_set_sha256": admission.committed_chunk_set_sha256,
                "lineage_sha256": admission.lineage_sha256,
                "snapshot_sha256": admission.snapshot_sha256,
                "snapshot_bytes": admission.snapshot_bytes,
                "total_plaintext_sha256": admission.total_plaintext_sha256,
                "total_plaintext_bytes": admission.total_plaintext_bytes,
                "chunk_count": admission.chunk_count,
            },
            "baseline": {
                "baseline_generation_id": handoff.baseline_generation_id,
                "database_system_identifier": handoff.database_system_identifier,
                "timeline_id": handoff.timeline_id,
                "wal_segment_size_bytes": handoff.wal_segment_size_bytes,
                "baseline_wal_lsn": handoff.baseline_wal_lsn,
                "wal_chain_start_lsn": handoff.wal_chain_start_lsn,
                "base_backup_end_lsn": handoff.base_backup_end_lsn,
                "completion_attestation_sha256": handoff.completion_attestation_sha256,
                "witness_transition_id": handoff.witness_transition_id,
                "witness_public_key_sha256": hashlib.sha256(handoff.witness_public_key).hexdigest(),
            },
            "target_replay_lsn": handoff.base_backup_end_lsn,
            "postgresql": {
                "in_recovery": True,
                "role": "standby",
                "database_system_identifier": handoff.database_system_identifier,
                "timeline_id": handoff.timeline_id,
                "wal_segment_size_bytes": handoff.wal_segment_size_bytes,
                "baseline_generation_id": handoff.baseline_generation_id,
                "replay_lsn": handoff.base_backup_end_lsn,
            },
        }
        for dotted_name, value in changes.items():
            target = result
            *parents, leaf = dotted_name.split(".")
            for parent in parents:
                target = target[parent]  # type: ignore[assignment,index]
            target[leaf] = value
        return result

    def readback(self, **changes: object) -> bytes:
        return canonical_json_bytes(self.payload(**changes))

    def raw_attestation(
        self,
        *,
        scope: PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationScope | None = None,
        canonical_readback: bytes | None = None,
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=45),
    ) -> dict[str, object]:
        return build_physical_postgres_chunked_base_backup_recovery_readback_attestation(
            scope=self.scope() if scope is None else scope,
            recovery_admission=self.admission,
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            canonical_readback=self.readback() if canonical_readback is None else canonical_readback,
            attestation_id="host-recovery-attestation-000001",
            attestation_nonce="A" * 22,
            issued_at=issued_at,
            expires_at=expires_at,
            attester_signer=self.attester,
        )

    def verify(self, *, raw=None, scope=None, now=NOW, expected_public_key=None):
        return verify_physical_postgres_chunked_base_backup_recovery_readback_attestation(
            attestation=self.raw_attestation(scope=scope) if raw is None else raw,
            expected_attester_public_key=(
                _public(self.attester) if expected_public_key is None else expected_public_key
            ),
            scope=self.scope() if scope is None else scope,
            recovery_admission=self.admission,
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            now=now,
        )

    def require(self, capability, *, scope=None, now=NOW):
        return require_verified_physical_postgres_chunked_base_backup_recovery_readback_attestation(
            capability,
            expected_attester_public_key=_public(self.attester),
            scope=self.scope() if scope is None else scope,
            recovery_admission=self.admission,
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            now=now,
        )

    def test_signed_exact_v2_readback_mints_only_opaque_capability(self) -> None:
        capability = self.verify()

        self.assertIsInstance(
            capability,
            VerifiedPhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestation,
        )
        self.assertEqual(self.evidence.manifest.manifest_id, capability.manifest_id)
        self.assertEqual(self.admission.stage_directory_name, capability.stage_directory_name)
        self.assertEqual(self.admission.stage_receipt_sha256, capability.stage_receipt_sha256)
        self.assertEqual(self.evidence.handoff.base_backup_end_lsn, capability.target_replay_lsn)
        self.assertIs(capability, self.require(capability))
        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
            pickle.dumps(capability)

    def test_bare_or_forged_readback_never_becomes_execution_capability(self) -> None:
        raw_readback = self.readback()
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError,
            "CAPABILITY_REQUIRED",
        ):
            self.require(raw_readback)
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError,
            "ATTESTATION_INVALID",
        ):
            self.verify(raw=raw_readback)

        forged = self.readback(**{"postgresql.replay_lsn": "0/2900000"})
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError,
            "READBACK_PIN_MISMATCH",
        ):
            self.raw_attestation(canonical_readback=forged)
        wrong_stage = self.readback(**{"stage.stage_receipt_sha256": "f" * 64})
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError,
            "READBACK_PIN_MISMATCH",
        ):
            self.raw_attestation(canonical_readback=wrong_stage)
        wrong_directory = self.readback(
            **{"stage.stage_directory_name": "stage-" + "f" * 48}
        )
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError,
            "READBACK_PIN_MISMATCH",
        ):
            self.raw_attestation(canonical_readback=wrong_directory)

    def test_wrong_attester_stale_and_noncanonical_artifacts_fail_closed(self) -> None:
        raw = self.raw_attestation()
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError,
            "SIGNER_MISMATCH",
        ):
            self.verify(raw=raw, expected_public_key=_public(Ed25519PrivateKey.generate()))

        expiring = self.raw_attestation(expires_at=NOW + timedelta(seconds=10))
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError,
            "ATTESTATION_STALE",
        ):
            self.verify(raw=expiring, now=NOW + timedelta(seconds=10))
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError,
            "ATTESTATION_STALE",
        ):
            self.verify(raw=expiring, now=NOW + timedelta(seconds=20))

        raw_bytes = canonical_json_bytes(raw) + b"\n"
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError,
            "NONCANONICAL",
        ):
            self.verify(raw=raw_bytes)

    def test_signed_target_and_stage_pins_are_exact_and_capability_tampering_fails(self) -> None:
        target = "0/2900000"
        target_scope = self.scope(expected_target_replay_lsn=target)
        target_readback = self.readback(
            **{
                "target_replay_lsn": target,
                "postgresql.replay_lsn": target,
            }
        )
        raw = self.raw_attestation(scope=target_scope, canonical_readback=target_readback)
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError,
            "PIN_MISMATCH",
        ):
            self.verify(raw=raw, scope=self.scope())

        capability = self.verify()
        object.__setattr__(capability, "stage_receipt_sha256", "f" * 64)
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError,
            "CAPABILITY_TAMPERED",
        ):
            self.require(capability)

    def test_module_is_v2_only_and_has_no_runtime_or_readiness_import_surface(self) -> None:
        source = inspect.getsource(attestation_module)
        tree = ast.parse(source)
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imports.add(module)
                imports.update(
                    f"{module}.{alias.name}" if module else alias.name
                    for alias in node.names
                )
        forbidden = {
            "os",
            "pathlib",
            "socket",
            "subprocess",
            "requests",
            "boto3",
            "psycopg",
            "asyncpg",
            "sqlalchemy",
            "core.physical_wal_object_manifest",
            "core.physical_wal_receiver_staging",
            "core.physical_wal_chunked_base_backup_receiver_staging_runtime",
            "core.physical_postgres_chunked_base_backup_recovery_preflight",
            "core.physical_full_matrix_campaign_readiness",
            "core.physical_full_matrix_execution_driver",
        }
        self.assertFalse(imports & forbidden)
        self.assertNotIn("physical_wal_object_manifest", source)
        self.assertNotIn("physical_wal_receiver_staging", source)
        self.assertNotIn("physical_full_matrix", source)
        self.assertNotIn("open(", source)
        self.assertNotIn("connect(", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
