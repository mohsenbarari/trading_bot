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
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_postgres_chunked_base_backup_target_recovery_preflight as preflight_module
from core.physical_postgres_chunked_base_backup_recovery_readback_attestation import (
    PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError,
    PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationScope,
    build_physical_postgres_chunked_base_backup_recovery_readback_attestation,
    verify_physical_postgres_chunked_base_backup_recovery_readback_attestation,
)
from core.physical_postgres_chunked_base_backup_target_recovery_preflight import (
    PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightConfig,
    PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightContext,
    PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError,
    mint_physical_postgres_chunked_base_backup_target_recovery_preflight,
    require_verified_physical_postgres_chunked_base_backup_target_recovery_preflight,
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
from core.physical_wal_chunked_base_backup_target_wal_continuity import (
    PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector,
    PhysicalWalChunkedBaseBackupTargetWalContinuityScope,
    build_physical_wal_chunked_base_backup_target_wal_continuity_receipt,
    mint_physical_wal_chunked_base_backup_target_wal_continuity,
    verify_physical_wal_chunked_base_backup_target_wal_continuity_receipt,
)
from core import physical_wal_chunked_base_backup_recovery_admission as admission_module
from tests.test_physical_wal_chunked_base_backup_receiver_staging_runtime import (
    NOW,
    _Decryptor,
    _EvidenceFixture,
    _ExactReceiver,
)
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import _nonce


def _public(value: Ed25519PrivateKey) -> bytes:
    return value.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _zulu(value) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(prefix="postgres-chunked-v2-target-preflight-")
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
            staging_result=stage,
            now=NOW,
        )
        self.target_lsn = "0/2A00000"
        self.continuity_scope = PhysicalWalChunkedBaseBackupTargetWalContinuityScope(
            transfer_binding=self.evidence.binding,
            lineage_sha256=handoff.lineage_sha256,
            baseline_generation_id=handoff.baseline_generation_id,
            database_system_identifier=handoff.database_system_identifier,
            timeline_id=handoff.timeline_id,
            wal_segment_size_bytes=handoff.wal_segment_size_bytes,
            baseline_wal_lsn=handoff.baseline_wal_lsn,
            wal_chain_start_lsn=handoff.wal_chain_start_lsn,
            base_backup_end_lsn=handoff.base_backup_end_lsn,
            target_lsn=self.target_lsn,
        )
        raw_receipt = build_physical_wal_chunked_base_backup_target_wal_continuity_receipt(
            manifest=self.evidence.manifest,
            handoff_receipt=handoff,
            scope=self.continuity_scope,
            wal_object_selectors=self.selectors(),
            receipt_id="target-recovery-continuity-receipt-0001",
            receipt_nonce=_nonce(90_001),
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=50),
            witness_signer=self.evidence.witness,
        )
        self.continuity_receipt = (
            verify_physical_wal_chunked_base_backup_target_wal_continuity_receipt(
                continuity_receipt=raw_receipt,
                manifest=self.evidence.manifest,
                handoff_receipt=handoff,
                scope=self.continuity_scope,
                now=NOW,
            )
        )
        self.continuity = mint_physical_wal_chunked_base_backup_target_wal_continuity(
            manifest=self.evidence.manifest,
            handoff_receipt=handoff,
            continuity_receipt=self.continuity_receipt,
            scope=self.continuity_scope,
            now=NOW,
        )
        self.attester = Ed25519PrivateKey.generate()
        self.attester_public = _public(self.attester)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def selectors(self):
        handoff = self.evidence.handoff
        recipient = self.evidence.binding.destination_age_recipient
        prefix = (
            f"{self.evidence.binding.object_storage_namespace}/"
            f"{self.evidence.binding.campaign_id}/"
            f"{self.evidence.binding.release_sha}/wal-v2/"
            f"{handoff.lineage_sha256}/"
        )
        return (
            PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector(
                index=0,
                object_key=prefix + "000000010000000000000002-a.age",
                version_id="target-wal-version-0000000001",
                ciphertext_sha256="d" * 64,
                ciphertext_bytes=1024 * 1024 + 128,
                plaintext_sha256="e" * 64,
                plaintext_bytes=1024 * 1024,
                timeline_id=handoff.timeline_id,
                start_lsn=handoff.base_backup_end_lsn,
                end_lsn="0/2900000",
                age_recipient=recipient,
            ),
            PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector(
                index=1,
                object_key=prefix + "000000010000000000000002-b.age",
                version_id="target-wal-version-0000000002",
                ciphertext_sha256="f" * 64,
                ciphertext_bytes=1024 * 1024 + 128,
                plaintext_sha256="a" * 64,
                plaintext_bytes=1024 * 1024,
                timeline_id=handoff.timeline_id,
                start_lsn="0/2900000",
                end_lsn=self.target_lsn,
                age_recipient=recipient,
            ),
        )

    def context(
        self,
        **changes: object,
    ) -> PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightContext:
        handoff = self.evidence.handoff
        admission = self.admission
        continuity = self.continuity
        key_sha = hashlib.sha256(self.attester_public).hexdigest()
        context = PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightContext(
            transfer_binding=self.evidence.binding,
            receiver_site="webapp_ir",
            recovery_admission_scope_sha256=admission.scope_sha256,
            stage_directory_name=admission.stage_directory_name,
            stage_receipt_sha256=admission.stage_receipt_sha256,
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
            target_replay_lsn=continuity.target_lsn,
            continuity_receipt_id=continuity.continuity_receipt_id,
            continuity_receipt_nonce=continuity.continuity_receipt_nonce,
            continuity_receipt_sha256=continuity.continuity_receipt_sha256,
            continuity_scope_sha256=continuity.scope_sha256,
            continuity_selector_set_sha256=continuity.selector_set_sha256,
            expected_readback_attester_public_key=self.attester_public,
            expected_readback_attester_public_key_sha256=key_sha,
            expected_readback_attester_key_id="ed25519-sha256:" + key_sha,
        )
        return replace(context, **changes)

    def config(
        self,
        **changes: object,
    ) -> PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightConfig:
        return replace(
            PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightConfig(
                context=self.context(),
                expected_readback_attester_public_key=self.attester_public,
                enabled=True,
            ),
            **changes,
        )

    def attestation_scope(
        self,
        **changes: object,
    ) -> PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationScope:
        handoff = self.evidence.handoff
        scope = PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationScope(
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
            expected_target_replay_lsn=self.target_lsn,
        )
        return replace(scope, **changes)

    def payload(self, **changes: object) -> dict[str, object]:
        handoff = self.evidence.handoff
        binding = self.evidence.binding
        admission = self.admission
        payload: dict[str, object] = {
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
            "target_replay_lsn": self.target_lsn,
            "postgresql": {
                "in_recovery": True,
                "role": "standby",
                "database_system_identifier": handoff.database_system_identifier,
                "timeline_id": handoff.timeline_id,
                "wal_segment_size_bytes": handoff.wal_segment_size_bytes,
                "baseline_generation_id": handoff.baseline_generation_id,
                "replay_lsn": self.target_lsn,
            },
        }
        for dotted_name, value in changes.items():
            target = payload
            *parents, leaf = dotted_name.split(".")
            for parent in parents:
                target = target[parent]  # type: ignore[assignment,index]
            target[leaf] = value
        return payload

    def readback(self, **changes: object) -> bytes:
        return canonical_json_bytes(self.payload(**changes))

    def raw_attestation(
        self,
        *,
        scope: PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationScope | None = None,
        canonical_readback: bytes | None = None,
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=45),
        signer: Ed25519PrivateKey | None = None,
    ) -> dict[str, object]:
        return build_physical_postgres_chunked_base_backup_recovery_readback_attestation(
            scope=self.attestation_scope() if scope is None else scope,
            recovery_admission=self.admission,
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            canonical_readback=self.readback() if canonical_readback is None else canonical_readback,
            attestation_id="target-recovery-attestation-000001",
            attestation_nonce="T" * 22,
            issued_at=issued_at,
            expires_at=expires_at,
            attester_signer=self.attester if signer is None else signer,
        )

    def attestation(self, *, raw=None, scope=None, now=NOW, expected_public_key=None):
        return verify_physical_postgres_chunked_base_backup_recovery_readback_attestation(
            attestation=self.raw_attestation(scope=scope) if raw is None else raw,
            expected_attester_public_key=(
                self.attester_public if expected_public_key is None else expected_public_key
            ),
            scope=self.attestation_scope() if scope is None else scope,
            recovery_admission=self.admission,
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            now=now,
        )

    def mint(self, *, config=None, attestation=None, now=NOW, continuity=None, receipt=None, scope=None):
        return mint_physical_postgres_chunked_base_backup_target_recovery_preflight(
            config=self.config() if config is None else config,
            recovery_admission=self.admission,
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            target_wal_continuity=self.continuity if continuity is None else continuity,
            target_wal_continuity_receipt=self.continuity_receipt if receipt is None else receipt,
            target_wal_continuity_scope=self.continuity_scope if scope is None else scope,
            recovery_readback_attestation=self.attestation() if attestation is None else attestation,
            now=now,
        )

    def require(self, result, *, config=None, attestation=None, now=NOW, continuity=None, receipt=None, scope=None):
        return require_verified_physical_postgres_chunked_base_backup_target_recovery_preflight(
            result,
            config=self.config() if config is None else config,
            recovery_admission=self.admission,
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            target_wal_continuity=self.continuity if continuity is None else continuity,
            target_wal_continuity_receipt=self.continuity_receipt if receipt is None else receipt,
            target_wal_continuity_scope=self.continuity_scope if scope is None else scope,
            recovery_readback_attestation=(
                self.attestation() if attestation is None else attestation
            ),
            now=now,
        )

    def test_exact_signed_target_wal_proof_and_readback_attestation_mint_opaque_evidence(self) -> None:
        attestation = self.attestation()
        result = self.mint(attestation=attestation)

        self.assertEqual("webapp_fi", result.source_site)
        self.assertEqual("webapp_ir", result.destination_site)
        self.assertEqual(self.target_lsn, result.target_replay_lsn)
        self.assertGreater(result.continuity_selector_count, 0)
        self.assertEqual(
            hashlib.sha256(self.attester_public).hexdigest(),
            result.expected_readback_attester_public_key_sha256,
        )
        self.assertIs(result, self.require(result, attestation=attestation))
        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
            pickle.dumps(result)

    def test_context_requires_strict_target_and_pins_stage_continuity_and_attester_policy(self) -> None:
        attestation = self.attestation()
        base = self.evidence.handoff.base_backup_end_lsn
        cases = (
            self.context(target_replay_lsn=base),
            self.context(stage_directory_name="stage-" + "f" * 48),
            self.context(continuity_receipt_sha256="f" * 64),
            self.context(receiver_site="webapp_fi"),
        )
        for context in cases:
            with self.subTest(context=context), self.assertRaises(
                PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError
            ):
                self.mint(config=self.config(context=context), attestation=attestation)
        with self.assertRaises(PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError):
            self.mint(
                attestation=attestation,
                scope=replace(self.continuity_scope, target_lsn="0/2B00000"),
            )
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError,
            "CONFIG_INVALID",
        ):
            self.mint(
                config=self.config(
                    expected_readback_attester_public_key=_public(Ed25519PrivateKey.generate())
                ),
                attestation=attestation,
            )

    def test_raw_wrong_signer_expired_target_and_stage_evidence_fail_closed(self) -> None:
        raw_readback = self.readback()
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError,
            "READBACK_ATTESTATION_INVALID",
        ):
            self.mint(attestation=raw_readback)
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError,
            "READBACK_ATTESTATION_INVALID",
        ):
            self.mint(attestation=self.raw_attestation())

        wrong_signer = Ed25519PrivateKey.generate()
        wrong_signer_capability = self.attestation(
            raw=self.raw_attestation(signer=wrong_signer),
            expected_public_key=_public(wrong_signer),
        )
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError,
            "READBACK_ATTESTATION_INVALID",
        ):
            self.mint(attestation=wrong_signer_capability)

        expiry = NOW + timedelta(seconds=10)
        expiring = self.attestation(
            raw=self.raw_attestation(expires_at=expiry),
            now=NOW,
        )
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError,
            "READBACK_ATTESTATION_INVALID",
        ):
            self.mint(attestation=expiring, now=expiry)

        other_target = "0/2B00000"
        target_scope = self.attestation_scope(expected_target_replay_lsn=other_target)
        target_capability = self.attestation(
            raw=self.raw_attestation(
                scope=target_scope,
                canonical_readback=self.readback(
                    **{
                        "target_replay_lsn": other_target,
                        "postgresql.replay_lsn": other_target,
                    }
                ),
            ),
            scope=target_scope,
        )
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError,
            "READBACK_ATTESTATION_INVALID",
        ):
            self.mint(attestation=target_capability)

        wrong_stage = self.readback(
            **{"stage.stage_directory_name": "stage-" + "f" * 48}
        )
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError,
            "READBACK_PIN_MISMATCH",
        ):
            self.raw_attestation(canonical_readback=wrong_stage)

        tampered_capability = self.attestation()
        object.__setattr__(tampered_capability, "stage_directory_name", "stage-" + "f" * 48)
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError,
            "READBACK_ATTESTATION_INVALID",
        ):
            self.mint(attestation=tampered_capability)

    def test_recovery_admission_is_membership_only_and_never_reopens_stage_receipt(self) -> None:
        with patch.object(
            admission_module,
            "_verified_facts",
            side_effect=AssertionError("unexpected stage readback"),
        ):
            result = self.mint()
        self.assertEqual(self.admission.stage_directory_name, result.stage_directory_name)

    def test_require_rejects_changed_inputs_and_tampering(self) -> None:
        attestation = self.attestation()
        result = self.mint(attestation=attestation)
        different = self.attestation()
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError,
            "INPUT_MISMATCH",
        ):
            self.require(result, attestation=different)
        object.__setattr__(result, "target_replay_lsn", "0/2900000")
        with self.assertRaisesRegex(
            PhysicalPostgresChunkedBaseBackupTargetRecoveryPreflightError,
            "PREFLIGHT_TAMPERED",
        ):
            self.require(result, attestation=attestation)

    def test_module_is_v2_only_and_has_no_runtime_or_raw_readback_import_surface(self) -> None:
        source = inspect.getsource(preflight_module)
        tree = ast.parse(source)
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imports.add(module)
                imports.update(f"{module}.{alias.name}" if module else alias.name for alias in node.names)
        forbidden = {
            "os", "pathlib", "socket", "subprocess", "boto3", "requests", "psycopg",
            "asyncpg", "sqlalchemy", "core.physical_wal_object_manifest",
            "core.physical_wal_receiver_staging",
            "core.physical_wal_chunked_base_backup_receiver_staging_runtime",
            "core.physical_postgres_chunked_base_backup_recovery_preflight",
            "core.physical_postgres_recovery_preflight",
            "core.physical_full_matrix_campaign_readiness",
            "core.physical_full_matrix_execution_driver",
        }
        self.assertFalse(imports & forbidden)
        self.assertNotIn("physical_wal_object_manifest", source)
        self.assertNotIn("physical_wal_receiver_staging", source)
        self.assertNotIn("physical_full_matrix", source)
        self.assertNotIn("TargetRecoveryPostgresReadback", source)
        self.assertNotIn("receiver_readback", source)
        self.assertNotIn("open(", source)
        self.assertNotIn("connect(", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
