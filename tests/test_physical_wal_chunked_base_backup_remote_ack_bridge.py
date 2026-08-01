from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.object_delta_role_matrix_rollover import (
    build_object_delta_role_matrix_witnessed_term_proof,
    verify_object_delta_role_matrix_witnessed_term,
)
from core.physical_wal_base_backup_spool import (
    PhysicalWalBaseBackupCompletedArtifact,
    PhysicalWalBaseBackupManifestBinding,
    authorize_physical_wal_base_backup_binding,
)
from core.physical_wal_chunked_base_backup_handoff_receipt import (
    build_physical_wal_chunked_base_backup_handoff_receipt,
    verify_physical_wal_chunked_base_backup_handoff_receipt,
)
from core.physical_wal_chunked_base_backup_lineage_envelope import (
    build_physical_wal_chunked_base_backup_lineage_envelope,
)
from core.physical_wal_chunked_base_backup_manifest import (
    build_physical_wal_chunked_base_backup_manifest,
    verify_physical_wal_chunked_base_backup_manifest,
)
from core.physical_wal_chunked_base_backup_remote_ack_bridge import (
    PHYSICAL_WAL_CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SCHEMA,
    PhysicalWalChunkedBaseBackupRemoteAckBridgeError,
    PhysicalWalChunkedBaseBackupRemoteAckScope,
    VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence,
    mint_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence,
    require_verified_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence,
)
from core import physical_wal_chunked_base_backup_remote_ack_bridge as bridge_module
from core.physical_wal_chunked_base_backup_transfer import (
    PhysicalWalChunkedBaseBackupChunk,
    append_physical_wal_chunked_base_backup_witness_accepted_chunk,
    begin_physical_wal_chunked_base_backup_witness_accepted_chunk_set,
    build_physical_wal_chunked_base_backup_binding,
    build_physical_wal_chunked_base_backup_chunk_completion,
    build_physical_wal_chunked_base_backup_chunk_permit,
    build_physical_wal_chunked_base_backup_finalization_permit,
    build_physical_wal_chunked_base_backup_transfer_session,
    build_physical_wal_chunked_base_backup_witness_chunk_commitment,
    verify_physical_wal_chunked_base_backup_chunk_commitment,
    verify_physical_wal_chunked_base_backup_chunk_completion,
    verify_physical_wal_chunked_base_backup_chunk_permit,
    verify_physical_wal_chunked_base_backup_finalization_permit,
    verify_physical_wal_chunked_base_backup_transfer_session,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
PLAINTEXT = b"verified-v2-base-backup-remote-ack-bridge\n" * 100


def _public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _nonce(number: int) -> str:
    return f"{number:022d}"


class _V2Evidence:
    """Independent one-chunk V2 evidence fixture; no bridge internals used."""

    def __init__(self) -> None:
        self.term_signer = Ed25519PrivateKey.generate()
        self.witness = Ed25519PrivateKey.generate()
        self.source_signer = Ed25519PrivateKey.generate()
        term_raw = build_object_delta_role_matrix_witnessed_term_proof(
            holder_site="webapp_fi",
            writer_epoch=73,
            writer_lease_id="writer-lease-73",
            witness_transition_id="witness-transition-73",
            issued_at=NOW - timedelta(seconds=10),
            expires_at=NOW + timedelta(seconds=50),
            witness_signer=self.term_signer,
        )
        term = verify_object_delta_role_matrix_witnessed_term(
            term_raw,
            witness_public_key=_public(self.term_signer),
            maximum_lease_duration_seconds=90,
            safety_margin_seconds=5,
            now=NOW,
        )
        source_binding = authorize_physical_wal_base_backup_binding(
            manifest_binding=PhysicalWalBaseBackupManifestBinding(
                source_site="webapp_fi",
                destination_site="webapp_ir",
                campaign_id="physical-base-20260731",
                release_sha=RELEASE,
                baseline_generation_id="physical-base-generation-20260731",
                database_system_identifier="7392847193847192834",
                timeline_id=1,
                wal_segment_size_bytes=16 * 1024 * 1024,
                baseline_wal_lsn="0/1800000",
                wal_chain_start_lsn="0/1000000",
                base_backup_end_lsn="0/2800000",
                destination_age_recipient=RECIPIENT,
                object_storage_namespace="physical-wal",
            ),
            completed_artifact=PhysicalWalBaseBackupCompletedArtifact(
                artifact_name="remote-ack-v2-fixture.tar",
                plaintext_sha256=hashlib.sha256(PLAINTEXT).hexdigest(),
                plaintext_bytes=len(PLAINTEXT),
                completion_attestation_sha256=hashlib.sha256(b"completed").hexdigest(),
            ),
            witnessed_term=term,
            now=NOW,
        )
        self.binding = build_physical_wal_chunked_base_backup_binding(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id="physical-base-20260731",
            release_sha=RELEASE,
            object_storage_namespace="physical-wal",
            route_commitment_sha256="a" * 64,
            four_role_binding_sha256="b" * 64,
            destination_age_recipient=RECIPIENT,
            writer_holder_site="webapp_fi",
            writer_epoch=term.writer_epoch,
            writer_lease_id=term.writer_lease_id,
            witnessed_term_proof_sha256=term.proof_sha256,
        )
        session_raw = build_physical_wal_chunked_base_backup_transfer_session(
            binding=self.binding,
            session_id="remote-ack-session-00000001",
            session_nonce=_nonce(1),
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=10),
            witness_signer=self.witness,
        )
        session = verify_physical_wal_chunked_base_backup_transfer_session(
            transfer_session=session_raw,
            expected_binding=self.binding,
            expected_witness_public_key=_public(self.witness),
            now=NOW,
        )
        permit_raw = build_physical_wal_chunked_base_backup_chunk_permit(
            transfer_session=session,
            permit_id="remote-ack-permit-00000001",
            permit_nonce=_nonce(100),
            chunk_index=0,
            max_ciphertext_bytes=1024 * 1024,
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=90),
            witness_signer=self.witness,
        )
        permit = verify_physical_wal_chunked_base_backup_chunk_permit(
            chunk_permit=permit_raw,
            transfer_session=session,
            expected_witness_public_key=_public(self.witness),
            now=NOW,
        )
        ciphertext = b"age-v2:" + PLAINTEXT
        chunk = PhysicalWalChunkedBaseBackupChunk(
            index=0,
            object_key=permit.object_key,
            version_id="remote-ack-version-0000001",
            ciphertext_sha256=hashlib.sha256(ciphertext).hexdigest(),
            ciphertext_bytes=len(ciphertext),
            plaintext_sha256=hashlib.sha256(PLAINTEXT).hexdigest(),
            plaintext_bytes=len(PLAINTEXT),
            age_recipient=RECIPIENT,
        )
        completion_raw = build_physical_wal_chunked_base_backup_chunk_completion(
            chunk_permit=permit,
            completion_id="remote-ack-completion-0001",
            completion_nonce=_nonce(200),
            completed_at=NOW,
            chunk=chunk,
            source_signer=self.source_signer,
        )
        completion = verify_physical_wal_chunked_base_backup_chunk_completion(
            chunk_completion=completion_raw,
            chunk_permit=permit,
            expected_source_public_key=_public(self.source_signer),
            now=NOW,
        )
        commitment_raw = build_physical_wal_chunked_base_backup_witness_chunk_commitment(
            chunk_completion=completion,
            commitment_id="remote-ack-commitment-0001",
            commitment_nonce=_nonce(300),
            durable_ledger_entry_id="remote-ack-ledger-00000001",
            committed_at=NOW,
            witness_signer=self.witness,
        )
        commitment = verify_physical_wal_chunked_base_backup_chunk_commitment(
            chunk_commitment=commitment_raw,
            chunk_completion=completion,
            expected_witness_public_key=_public(self.witness),
            now=NOW,
        )
        accepted = begin_physical_wal_chunked_base_backup_witness_accepted_chunk_set(
            transfer_session=session,
            now=NOW,
        )
        accepted = append_physical_wal_chunked_base_backup_witness_accepted_chunk(
            accepted_chunk_set=accepted,
            chunk_commitment=commitment,
            now=NOW,
        )
        finalization_raw = build_physical_wal_chunked_base_backup_finalization_permit(
            transfer_session=session,
            accepted_chunk_set=accepted,
            finalization_permit_id="remote-ack-finalization-0001",
            finalization_permit_nonce=_nonce(400),
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=90),
            total_plaintext_sha256=hashlib.sha256(PLAINTEXT).hexdigest(),
            total_plaintext_bytes=len(PLAINTEXT),
            witness_signer=self.witness,
        )
        finalization = verify_physical_wal_chunked_base_backup_finalization_permit(
            finalization_permit=finalization_raw,
            transfer_session=session,
            accepted_chunk_set=accepted,
            expected_witness_public_key=_public(self.witness),
            now=NOW,
        )
        manifest_raw = build_physical_wal_chunked_base_backup_manifest(
            finalization_permit=finalization,
            accepted_chunk_set=accepted,
            manifest_id="remote-ack-manifest-00000001",
            manifest_nonce=_nonce(500),
            created_at=NOW,
            witness_signer=self.witness,
        )
        self.manifest = verify_physical_wal_chunked_base_backup_manifest(
            manifest=manifest_raw,
            finalization_permit=finalization,
            accepted_chunk_set=accepted,
            expected_witness_public_key=_public(self.witness),
            now=NOW,
        )
        lineage = build_physical_wal_chunked_base_backup_lineage_envelope(
            source_binding=source_binding,
            transfer_binding=self.binding,
            now=NOW,
        )
        handoff_raw = build_physical_wal_chunked_base_backup_handoff_receipt(
            manifest=self.manifest,
            lineage_envelope=lineage,
            receipt_id="remote-ack-handoff-receipt-01",
            receipt_nonce=_nonce(600),
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=60),
            witness_signer=self.witness,
        )
        self.handoff = verify_physical_wal_chunked_base_backup_handoff_receipt(
            handoff_receipt=handoff_raw,
            manifest=self.manifest,
            expected_witness_public_key=_public(self.witness),
            now=NOW,
        )

    def scope(self, **changes: object) -> PhysicalWalChunkedBaseBackupRemoteAckScope:
        values: dict[str, object] = {
            "transfer_binding": self.binding,
            "stream_generation_id": "physical-wal-stream-20260731",
            "baseline_generation_id": self.handoff.baseline_generation_id,
            "lineage_sha256": self.handoff.lineage_sha256,
            "database_system_identifier": self.handoff.database_system_identifier,
            "timeline_id": self.handoff.timeline_id,
            "wal_segment_size_bytes": self.handoff.wal_segment_size_bytes,
            "baseline_wal_lsn": self.handoff.baseline_wal_lsn,
            "wal_chain_start_lsn": self.handoff.wal_chain_start_lsn,
            "base_backup_end_lsn": self.handoff.base_backup_end_lsn,
        }
        values.update(changes)
        return PhysicalWalChunkedBaseBackupRemoteAckScope(**values)


class PhysicalWalChunkedBaseBackupRemoteAckBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = _V2Evidence()

    def mint(self, **scope_changes: object):
        return mint_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence(
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            scope=self.evidence.scope(**scope_changes),
            now=NOW,
        )

    def test_derives_only_exact_v2_postgres_base_backup_evidence(self) -> None:
        capability = self.mint()
        self.assertIsInstance(capability, VerifiedPhysicalWalChunkedBaseBackupRemoteAckBaseBackupEvidence)
        self.assertEqual(PHYSICAL_WAL_CHUNKED_BASE_BACKUP_REMOTE_ACK_BRIDGE_SCHEMA, capability.schema)
        manifest_hash = hashlib.sha256(self.evidence.manifest.canonical_manifest).hexdigest()
        self.assertEqual(manifest_hash, capability.canonical_manifest_sha256)
        self.assertEqual("0/2800000", capability.base_backup_end_lsn)
        self.assertEqual(
            self.evidence.manifest.chunks,
            capability.chunks,
        )
        self.assertIs(
            require_verified_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence(
                capability,
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                scope=self.evidence.scope(),
                now=NOW,
            ),
            capability,
        )

    def test_scope_route_recipient_term_lineage_baseline_and_wal_geometry_are_exact(self) -> None:
        wrong_source = replace(self.evidence.binding, source_site="webapp_ir")
        wrong_destination = replace(self.evidence.binding, destination_site="webapp_fi")
        wrong_campaign = replace(self.evidence.binding, campaign_id="physical-base-20260730")
        wrong_release = replace(self.evidence.binding, release_sha="f" * 40)
        wrong_recipient = replace(
            self.evidence.binding,
            destination_age_recipient="age1pppppppppppppppppppppppppppppppppppppppppppppppppp",
        )
        wrong_term = replace(
            self.evidence.binding,
            writer_term=replace(self.evidence.binding.writer_term, writer_epoch=74),
        )
        cases = (
            ("source", {"transfer_binding": wrong_source}, "SCOPE_ROUTE_MISMATCH"),
            ("destination", {"transfer_binding": wrong_destination}, "SCOPE_ROUTE_MISMATCH"),
            ("campaign", {"transfer_binding": wrong_campaign}, "SCOPE_ROUTE_MISMATCH"),
            ("release", {"transfer_binding": wrong_release}, "SCOPE_ROUTE_MISMATCH"),
            ("recipient", {"transfer_binding": wrong_recipient}, "SCOPE_RECIPIENT_MISMATCH"),
            ("term", {"transfer_binding": wrong_term}, "SCOPE_TERM_MISMATCH"),
            ("lineage", {"lineage_sha256": "f" * 64}, "SCOPE_LINEAGE_MISMATCH"),
            (
                "baseline generation",
                {"baseline_generation_id": "physical-base-generation-20260730"},
                "SCOPE_BASELINE_GENERATION_MISMATCH",
            ),
            ("timeline", {"timeline_id": 2}, "SCOPE_WAL_GEOMETRY_MISMATCH"),
            ("base end", {"base_backup_end_lsn": "0/2900000"}, "SCOPE_WAL_GEOMETRY_MISMATCH"),
        )
        for label, changes, code in cases:
            with self.subTest(label=label), self.assertRaisesRegex(
                PhysicalWalChunkedBaseBackupRemoteAckBridgeError, code
            ):
                self.mint(**changes)

    def test_no_caller_frontier_or_generic_ack_binding_can_be_derived_from_postgres_base_only_evidence(self) -> None:
        capability = self.mint()
        self.assertFalse(hasattr(capability, "remote_ack_binding"))
        self.assertFalse(hasattr(bridge_module, "mint_physical_wal_chunked_base_backup_remote_ack_binding"))
        with self.assertRaisesRegex(TypeError, "blob_object_frontier_wal_lsn"):
            self.evidence.scope(blob_object_frontier_wal_lsn="0/2800000")
        with self.assertRaisesRegex(TypeError, "objects_complete"):
            self.evidence.scope(objects_complete=True)

    def test_forged_manifest_handoff_or_bridge_capability_fails_closed(self) -> None:
        forged_manifest = replace(self.evidence.manifest, manifest_id="forged-manifest-00000001")
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupRemoteAckBridgeError, "MANIFEST_INVALID"):
            mint_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence(
                manifest=forged_manifest,
                handoff_receipt=self.evidence.handoff,
                scope=self.evidence.scope(),
                now=NOW,
            )
        forged_handoff = replace(self.evidence.handoff, manifest_sha256="f" * 64)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupRemoteAckBridgeError, "HANDOFF_INVALID"):
            mint_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence(
                manifest=self.evidence.manifest,
                handoff_receipt=forged_handoff,
                scope=self.evidence.scope(),
                now=NOW,
            )
        capability = self.mint()
        forged_capability = replace(capability, canonical_manifest_sha256="f" * 64)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupRemoteAckBridgeError, "CAPABILITY_REQUIRED"):
            require_verified_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence(
                forged_capability,
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                scope=self.evidence.scope(),
                now=NOW,
            )

    def test_duplicate_or_changed_v2_chunk_selector_and_stale_handoff_cannot_seed_join_evidence(self) -> None:
        duplicate_chunks = replace(
            self.evidence.manifest,
            chunks=(self.evidence.manifest.chunks[0], self.evidence.manifest.chunks[0]),
        )
        object.__setattr__(duplicate_chunks, "_capability", self.evidence.manifest._capability)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupRemoteAckBridgeError, "MANIFEST_INVALID"):
            mint_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence(
                manifest=duplicate_chunks,
                handoff_receipt=self.evidence.handoff,
                scope=self.evidence.scope(),
                now=NOW,
            )
        changed_selector = replace(
            self.evidence.manifest,
            chunks=(replace(self.evidence.manifest.chunks[0], version_id="wrong-version-0000000001"),),
        )
        object.__setattr__(changed_selector, "_capability", self.evidence.manifest._capability)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupRemoteAckBridgeError, "MANIFEST_INVALID"):
            mint_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence(
                manifest=changed_selector,
                handoff_receipt=self.evidence.handoff,
                scope=self.evidence.scope(),
                now=NOW,
            )
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupRemoteAckBridgeError, "HANDOFF_INVALID"):
            mint_physical_wal_chunked_base_backup_remote_ack_base_backup_evidence(
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                scope=self.evidence.scope(),
                now=NOW + timedelta(seconds=61),
            )

    def test_bridge_has_no_v1_object_bundle_or_remote_ack_runtime_import_surface(self) -> None:
        source = inspect.getsource(bridge_module)
        tree = ast.parse(source)
        imported_modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        joined_imports = "\n".join(imported_modules)
        self.assertNotIn("PhysicalWalObjectStorageBundle", source)
        self.assertNotIn("base_backup_object", source)
        self.assertNotIn("physical_wal_object_manifest", joined_imports)
        self.assertNotIn("physical_wal_base_backup_spool", joined_imports)
        self.assertNotIn("physical_wal_remote_ack", joined_imports)
        self.assertNotIn("os", imported_modules)
        self.assertNotIn("pathlib", imported_modules)
        self.assertNotIn("socket", imported_modules)

