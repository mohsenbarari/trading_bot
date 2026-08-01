from __future__ import annotations

import ast
from dataclasses import replace
from datetime import timedelta
import hashlib
import inspect
import os
from pathlib import Path
import pickle
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from core.append_only_sync_delta_batch import canonical_json_bytes
from core.physical_postgres_chunked_base_backup_recovery_preflight import (
    PhysicalPostgresChunkedBaseBackupRecoveryPreflightConfig,
    PhysicalPostgresChunkedBaseBackupRecoveryPreflightError,
    PhysicalPostgresChunkedBaseBackupRecoveryPreflightScope,
    PhysicalPostgresChunkedBaseBackupRecoveryReadbackEvidence,
    require_verified_physical_postgres_chunked_base_backup_recovery_preflight,
    verify_physical_postgres_chunked_base_backup_recovery_preflight,
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
    project_verified_physical_wal_chunked_base_backup_recovery_admission,
)
from core import physical_postgres_chunked_base_backup_recovery_preflight as preflight_module
from core import physical_wal_chunked_base_backup_recovery_admission as admission_module
from tests.test_physical_wal_chunked_base_backup_receiver_staging_runtime import (
    NOW,
    _Decryptor,
    _EvidenceFixture,
    _ExactReceiver,
)


class PhysicalPostgresChunkedBaseBackupRecoveryPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(prefix="postgres-chunked-v2-preflight-")
        root = Path(self.temporary.name)
        self.stage_root = root / "stage-root"
        self.ledger_root = root / "ledger-root"
        self.stage_root.mkdir(mode=0o700)
        self.ledger_root.mkdir(mode=0o700)
        os.chmod(self.stage_root, 0o700)
        os.chmod(self.ledger_root, 0o700)
        self.evidence = _EvidenceFixture()
        stage = execute_root_owned_physical_wal_chunked_base_backup_receiver_staging(
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
        self.admission_scope = PhysicalWalChunkedBaseBackupRecoveryAdmissionScope(
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
            scope=self.admission_scope,
            manifest=self.evidence.manifest,
            handoff_receipt=handoff,
            staging_result=stage,
            now=NOW,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def scope(self, **changes: object) -> PhysicalPostgresChunkedBaseBackupRecoveryPreflightScope:
        handoff = self.evidence.handoff
        scope = PhysicalPostgresChunkedBaseBackupRecoveryPreflightScope(
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
        return replace(scope, **changes)

    def config(self, **changes: object) -> PhysicalPostgresChunkedBaseBackupRecoveryPreflightConfig:
        return replace(
            PhysicalPostgresChunkedBaseBackupRecoveryPreflightConfig(
                scope=self.scope(),
                enabled=True,
            ),
            **changes,
        )

    def payload(self, **changes: object) -> dict[str, object]:
        handoff = self.evidence.handoff
        binding = self.evidence.binding
        admission = self.admission
        payload: dict[str, object] = {
            "schema": "gold-trade-physical-postgres-chunked-base-backup-recovery-readback-v2",
            "status": "replay-evidence-observed",
            "observed_at": "2026-07-31T12:00:00Z",
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
            target = payload
            *parents, leaf = dotted_name.split(".")
            for parent in parents:
                target = target[parent]  # type: ignore[assignment,index]
            target[leaf] = value
        return payload

    def readback(self, **changes: object) -> PhysicalPostgresChunkedBaseBackupRecoveryReadbackEvidence:
        raw = canonical_json_bytes(self.payload(**changes))
        return PhysicalPostgresChunkedBaseBackupRecoveryReadbackEvidence(
            raw_evidence=raw,
            evidence_sha256=hashlib.sha256(raw).hexdigest(),
        )

    def verify(self, *, config=None, readback=None, now=NOW):
        return verify_physical_postgres_chunked_base_backup_recovery_preflight(
            config=self.config() if config is None else config,
            recovery_admission=self.admission,
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            receiver_readback=self.readback() if readback is None else readback,
            now=now,
        )

    def test_mints_and_rechecks_only_exact_v2_recovery_evidence(self) -> None:
        readback = self.readback()
        result = self.verify(readback=readback)

        self.assertEqual("webapp_fi", result.source_site)
        self.assertEqual("webapp_ir", result.destination_site)
        self.assertEqual(self.evidence.handoff.base_backup_end_lsn, result.target_replay_lsn)
        self.assertEqual(self.admission.stage_receipt_sha256, result.stage_receipt_sha256)
        self.assertIs(
            result,
            require_verified_physical_postgres_chunked_base_backup_recovery_preflight(
                result,
                config=self.config(),
                recovery_admission=self.admission,
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                receiver_readback=readback,
                now=NOW,
            ),
        )
        with self.assertRaises(TypeError):
            pickle.dumps(result)

    def test_scope_cannot_claim_wal_beyond_v2_base_backup_endpoint(self) -> None:
        for scope in (
            self.scope(expected_target_replay_lsn="0/2900000"),
            self.scope(lineage_sha256="f" * 64),
            self.scope(receiver_site="webapp_fi"),
            self.scope(wal_segment_size_bytes=8 * 1024 * 1024),
        ):
            with self.subTest(scope=scope):
                with self.assertRaisesRegex(
                    PhysicalPostgresChunkedBaseBackupRecoveryPreflightError,
                    "(?:SCOPE_|CROSS_PIN_MISMATCH)",
                ):
                    self.verify(config=self.config(scope=scope))

    def test_readback_must_be_fresh_canonical_and_exactly_pinned(self) -> None:
        cases = (
            {"observed_at": "2026-07-31T11:58:29Z"},
            {"route.four_role_binding_sha256": "f" * 64},
            {"writer_term.writer_epoch": 74},
            {"stage.stage_receipt_sha256": "f" * 64},
            {"baseline.timeline_id": 2},
            {"target_replay_lsn": "0/2900000"},
            {"postgresql.in_recovery": False},
            {"postgresql.role": "primary"},
            {"postgresql.replay_lsn": "0/2900000"},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(PhysicalPostgresChunkedBaseBackupRecoveryPreflightError):
                    self.verify(readback=self.readback(**changes))
        raw = canonical_json_bytes(self.payload()) + b"\n"
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupRecoveryPreflightError,
            "NONCANONICAL",
        ):
            self.verify(
                readback=PhysicalPostgresChunkedBaseBackupRecoveryReadbackEvidence(
                    raw_evidence=raw,
                    evidence_sha256=hashlib.sha256(raw).hexdigest(),
                )
            )

    def test_forged_or_mutated_admission_never_crosses_membership_only_projector(self) -> None:
        with self.assertRaisesRegex(Exception, "CAPABILITY_REQUIRED"):
            project_verified_physical_wal_chunked_base_backup_recovery_admission(object())
        object.__setattr__(self.admission, "chunk_count", self.admission.chunk_count + 1)
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupRecoveryPreflightError,
            "V2_CAPABILITY_INVALID",
        ):
            self.verify()

    def test_projector_and_pure_preflight_do_not_reopen_stage_receipt(self) -> None:
        with patch.object(admission_module, "_verified_facts", side_effect=AssertionError("unexpected stage I/O")):
            self.assertIs(
                self.admission,
                project_verified_physical_wal_chunked_base_backup_recovery_admission(self.admission),
            )
            result = self.verify()
        self.assertEqual(self.admission.manifest_id, result.manifest_id)

    def test_verified_result_rejects_changed_inputs_or_tampering(self) -> None:
        readback = self.readback()
        result = self.verify(readback=readback)
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupRecoveryPreflightError,
            "INPUT_MISMATCH",
        ):
            require_verified_physical_postgres_chunked_base_backup_recovery_preflight(
                result,
                config=self.config(),
                recovery_admission=self.admission,
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                receiver_readback=self.readback(**{"postgresql.replay_lsn": "0/2900000"}),
                now=NOW,
            )
        object.__setattr__(result, "target_replay_lsn", "0/2900000")
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupRecoveryPreflightError,
            "PREFLIGHT_TAMPERED",
        ):
            require_verified_physical_postgres_chunked_base_backup_recovery_preflight(
                result,
                config=self.config(),
                recovery_admission=self.admission,
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                receiver_readback=readback,
                now=NOW,
            )

    def test_module_is_v2_only_and_has_no_readiness_or_runtime_import_surface(self) -> None:
        source = inspect.getsource(preflight_module)
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
            "boto3",
            "requests",
            "psycopg",
            "asyncpg",
            "sqlalchemy",
            "core.physical_wal_object_manifest",
            "core.physical_wal_receiver_staging",
            "core.physical_wal_chunked_base_backup_receiver_staging_runtime",
            "core.physical_postgres_recovery_preflight",
            "core.physical_full_matrix_campaign_readiness",
            "core.physical_full_matrix_execution_driver",
        }
        self.assertFalse(imports & forbidden)
        self.assertNotIn("physical_wal_object_manifest", source)
        self.assertNotIn("physical_wal_receiver_staging", source)
        self.assertNotIn("physical_full_matrix", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
