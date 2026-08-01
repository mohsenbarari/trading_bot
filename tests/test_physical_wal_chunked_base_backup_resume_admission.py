from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_wal_chunked_base_backup_publisher_runtime as publisher_runtime
from core.physical_wal_chunked_base_backup_resume_admission import (
    PhysicalWalChunkedBaseBackupFreshWitnessResumePlan,
    PhysicalWalChunkedBaseBackupResumeAdmissionError,
    PhysicalWalChunkedBaseBackupResumeExactObjectHeadObservation,
    PhysicalWalChunkedBaseBackupResumeScope,
    RootOwnedPhysicalWalChunkedBaseBackupResumeAdmissionConfig,
    admit_root_owned_physical_wal_chunked_base_backup_resume,
    require_verified_physical_wal_chunked_base_backup_resume_admission,
)
from core.physical_wal_chunked_base_backup_transfer import (
    PhysicalWalChunkedBaseBackupChunk,
    build_physical_wal_chunked_base_backup_binding,
    build_physical_wal_chunked_base_backup_chunk_completion,
    build_physical_wal_chunked_base_backup_chunk_permit,
    build_physical_wal_chunked_base_backup_transfer_session,
    build_physical_wal_chunked_base_backup_witness_chunk_commitment,
    verify_physical_wal_chunked_base_backup_chunk_commitment,
    verify_physical_wal_chunked_base_backup_chunk_completion,
    verify_physical_wal_chunked_base_backup_chunk_permit,
    verify_physical_wal_chunked_base_backup_transfer_session,
)


OLD = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
NOW = OLD + timedelta(minutes=3)
RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
DATA = b"durably-reconciled-v2-chunk-0\n" + b"durably-reconciled-v2-chunk-1\n"


def _public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _nonce(number: int) -> str:
    return f"{number:022d}"


class _Reconciler:
    def __init__(self, commitments, heads) -> None:
        self.commitments = commitments
        self.heads = heads
        self.commitment_calls: list[tuple[str, str]] = []
        self.head_calls: list[tuple[str, str]] = []
        self.bad_commitment = False
        self.bad_head = False

    def read_exact_durable_chunk_commitment(self, *, previous_session, commitment_id, commitment_sha256):
        self.commitment_calls.append((commitment_id, commitment_sha256))
        raw = self.commitments[commitment_id]
        return raw + b" " if self.bad_commitment else raw

    def head_exact_object_version(self, *, object_key, version_id):
        self.head_calls.append((object_key, version_id))
        head = self.heads[(object_key, version_id)]
        return replace(head, ciphertext_sha256="f" * 64) if self.bad_head else head


class PhysicalWalChunkedBaseBackupResumeAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)
        self.filename = "sealed-resume-checkpoint.json"
        self.path = self.root / self.filename
        self.witness = Ed25519PrivateKey.generate()
        self.source = Ed25519PrivateKey.generate()
        self.binding = build_physical_wal_chunked_base_backup_binding(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id="campaign-resume-v2-001",
            release_sha=RELEASE,
            object_storage_namespace="physical-wal",
            route_commitment_sha256="a" * 64,
            four_role_binding_sha256="b" * 64,
            destination_age_recipient=RECIPIENT,
            writer_holder_site="webapp_fi",
            writer_epoch=7,
            writer_lease_id="writer-lease-resume-0001",
            witnessed_term_proof_sha256="c" * 64,
        )
        self.scope = PhysicalWalChunkedBaseBackupResumeScope(
            transfer_binding=self.binding,
            lineage_sha256="d" * 64,
            staged_plaintext_sha256=hashlib.sha256(DATA).hexdigest(),
            staged_plaintext_bytes=len(DATA),
        )
        self.old_session, self.old_permits, self.commitments = self._old_evidence()
        checkpoint = publisher_runtime._build_checkpoint(
            session=self.old_session,
            lineage_sha256=self.scope.lineage_sha256,
            staged_plaintext_sha256=self.scope.staged_plaintext_sha256,
            staged_plaintext_bytes=self.scope.staged_plaintext_bytes,
            issued_permits=self.old_permits,
            accepted_commitments=self.commitments,
        )
        self.raw_checkpoint = checkpoint.canonical_checkpoint
        self.path.write_bytes(self.raw_checkpoint)
        os.chmod(self.path, 0o600)
        self.fresh_plan = self._fresh_plan()
        heads = {
            (item.chunk.object_key, item.chunk.version_id): PhysicalWalChunkedBaseBackupResumeExactObjectHeadObservation(
                object_key=item.chunk.object_key,
                version_id=item.chunk.version_id,
                ciphertext_sha256=item.chunk.ciphertext_sha256,
                ciphertext_bytes=item.chunk.ciphertext_bytes,
            )
            for item in self.commitments
        }
        self.reconciler = _Reconciler(
            {item.commitment_id: item.canonical_commitment for item in self.commitments}, heads
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def config(self):
        return RootOwnedPhysicalWalChunkedBaseBackupResumeAdmissionConfig(
            checkpoint_root=self.root,
            checkpoint_filename=self.filename,
            enabled=True,
        )

    def _old_evidence(self):
        raw_session = build_physical_wal_chunked_base_backup_transfer_session(
            binding=self.binding,
            session_id="old-resume-session-00000001",
            session_nonce=_nonce(1),
            issued_at=OLD,
            expires_at=OLD + timedelta(minutes=1),
            witness_signer=self.witness,
        )
        session = verify_physical_wal_chunked_base_backup_transfer_session(
            transfer_session=raw_session,
            expected_binding=self.binding,
            expected_witness_public_key=_public(self.witness),
            now=OLD,
        )
        chunks = (DATA[: len(DATA) // 2], DATA[len(DATA) // 2 :])
        permits = []
        commitments = []
        for index, plaintext in enumerate(chunks):
            permit_now = OLD + timedelta(seconds=index + 1)
            raw_permit = build_physical_wal_chunked_base_backup_chunk_permit(
                transfer_session=session,
                permit_id=f"old-resume-permit-{index:08d}",
                permit_nonce=_nonce(100 + index),
                chunk_index=index,
                max_ciphertext_bytes=1024 * 1024,
                issued_at=permit_now,
                expires_at=OLD + timedelta(seconds=45),
                witness_signer=self.witness,
            )
            permit = verify_physical_wal_chunked_base_backup_chunk_permit(
                chunk_permit=raw_permit,
                transfer_session=session,
                expected_witness_public_key=_public(self.witness),
                now=permit_now,
            )
            ciphertext = b"age-v2:" + plaintext
            chunk = PhysicalWalChunkedBaseBackupChunk(
                index=index,
                object_key=permit.object_key,
                version_id=f"immutable-version-{index:08d}",
                ciphertext_sha256=hashlib.sha256(ciphertext).hexdigest(),
                ciphertext_bytes=len(ciphertext),
                plaintext_sha256=hashlib.sha256(plaintext).hexdigest(),
                plaintext_bytes=len(plaintext),
                age_recipient=RECIPIENT,
            )
            completion_now = OLD + timedelta(seconds=10 + index)
            raw_completion = build_physical_wal_chunked_base_backup_chunk_completion(
                chunk_permit=permit,
                completion_id=f"old-resume-completion-{index:04d}",
                completion_nonce=_nonce(200 + index),
                completed_at=completion_now,
                chunk=chunk,
                source_signer=self.source,
            )
            completion = verify_physical_wal_chunked_base_backup_chunk_completion(
                chunk_completion=raw_completion,
                chunk_permit=permit,
                expected_source_public_key=_public(self.source),
                now=completion_now,
            )
            committed_at = OLD + timedelta(seconds=20 + index)
            raw_commitment = build_physical_wal_chunked_base_backup_witness_chunk_commitment(
                chunk_completion=completion,
                commitment_id=f"old-resume-commitment-{index:04d}",
                commitment_nonce=_nonce(300 + index),
                durable_ledger_entry_id=f"old-resume-ledger-entry-{index:04d}",
                committed_at=committed_at,
                witness_signer=self.witness,
            )
            commitment = verify_physical_wal_chunked_base_backup_chunk_commitment(
                chunk_commitment=raw_commitment,
                chunk_completion=completion,
                expected_witness_public_key=_public(self.witness),
                now=committed_at,
            )
            permits.append(permit)
            commitments.append(commitment)
        return session, tuple(permits), tuple(commitments)

    def _fresh_plan(
        self,
        *,
        permit_expired: bool = False,
        reuse_old: bool = False,
        reuse_old_nonce: bool = False,
        session_issued_at: datetime | None = None,
    ):
        if reuse_old:
            return PhysicalWalChunkedBaseBackupFreshWitnessResumePlan(
                transfer_session=self.old_session,
                chunk_permits=self.old_permits,
            )
        raw_session = build_physical_wal_chunked_base_backup_transfer_session(
            binding=self.binding,
            session_id="fresh-resume-session-0000001",
            session_nonce=_nonce(1_000),
            issued_at=NOW if session_issued_at is None else session_issued_at,
            expires_at=NOW + timedelta(minutes=2),
            witness_signer=self.witness,
        )
        session = verify_physical_wal_chunked_base_backup_transfer_session(
            transfer_session=raw_session,
            expected_binding=self.binding,
            expected_witness_public_key=_public(self.witness),
            now=NOW,
        )
        permits = []
        for index in range(len(self.old_permits)):
            expires = NOW + timedelta(seconds=5 if permit_expired else 45)
            raw_permit = build_physical_wal_chunked_base_backup_chunk_permit(
                transfer_session=session,
                permit_id=f"fresh-resume-permit-{index:06d}",
                permit_nonce=_nonce(100 + index) if reuse_old_nonce else _nonce(1_100 + index),
                chunk_index=index,
                max_ciphertext_bytes=1024 * 1024,
                issued_at=NOW,
                expires_at=expires,
                witness_signer=self.witness,
            )
            permits.append(
                verify_physical_wal_chunked_base_backup_chunk_permit(
                    chunk_permit=raw_permit,
                    transfer_session=session,
                    expected_witness_public_key=_public(self.witness),
                    now=NOW,
                )
            )
        return PhysicalWalChunkedBaseBackupFreshWitnessResumePlan(
            transfer_session=session,
            chunk_permits=tuple(permits),
        )

    def admit(self, **kwargs):
        return admit_root_owned_physical_wal_chunked_base_backup_resume(
            self.config(),
            scope=kwargs.pop("scope", self.scope),
            witness_public_key=_public(self.witness),
            source_public_key=_public(self.source),
            fresh_plan=kwargs.pop("fresh_plan", self.fresh_plan),
            reconciler=kwargs.pop("reconciler", self.reconciler),
            now=kwargs.pop("now", NOW),
            **kwargs,
        )

    def assert_failure(self, code: str, **kwargs) -> None:
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupResumeAdmissionError, f"^{code}$"):
            self.admit(**kwargs)

    def test_reconciles_exact_durable_evidence_but_mints_only_opaque_future_adapter_gate(self) -> None:
        admission = self.admit()

        self.assertEqual(2, admission.committed_chunk_count)
        self.assertEqual(self.old_session.session_id, admission.previous_session_id)
        self.assertEqual(self.fresh_plan.transfer_session.session_id, admission.fresh_session_id)
        self.assertEqual(2, len(self.reconciler.commitment_calls))
        self.assertEqual(2, len(self.reconciler.head_calls))
        self.assertIs(admission, require_verified_physical_wal_chunked_base_backup_resume_admission(admission, now=NOW))
        self.assertFalse(hasattr(admission, "checkpoint"))
        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
            admission.__reduce_ex__(4)

    def test_torn_or_partial_checkpoint_never_admits(self) -> None:
        payload = __import__("json").loads(self.raw_checkpoint)
        payload["accepted_commitments"] = payload["accepted_commitments"][:-1]
        self.path.write_bytes(canonical_json_bytes(payload))
        os.chmod(self.path, 0o600)

        self.assert_failure("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_PARTIAL_OR_NONCONTIGUOUS")

    def test_foreign_or_changed_snapshot_scope_never_admits(self) -> None:
        self.assert_failure(
            "CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_SCOPE_MISMATCH",
            scope=replace(self.scope, staged_plaintext_sha256="e" * 64),
        )
        foreign = build_physical_wal_chunked_base_backup_binding(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            campaign_id="campaign-resume-v2-001",
            release_sha=RELEASE,
            object_storage_namespace="physical-wal",
            route_commitment_sha256="a" * 64,
            four_role_binding_sha256="b" * 64,
            destination_age_recipient=RECIPIENT,
            writer_holder_site="webapp_ir",
            writer_epoch=7,
            writer_lease_id="writer-lease-resume-0001",
            witnessed_term_proof_sha256="c" * 64,
        )
        self.assert_failure(
            "CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_SCOPE_MISMATCH",
            scope=replace(self.scope, transfer_binding=foreign),
        )

    def test_remote_commitment_or_exact_version_mismatch_never_admits(self) -> None:
        self.reconciler.bad_commitment = True
        self.assert_failure("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_WITNESS_RECONCILIATION_MISMATCH")
        self.reconciler.bad_commitment = False
        self.reconciler.bad_head = True
        self.assert_failure("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_OBJECT_RECONCILIATION_MISMATCH")

    def test_old_replay_or_expired_fresh_permit_never_admits(self) -> None:
        self.assert_failure(
            "CHUNKED_BASE_BACKUP_RESUME_ADMISSION_FRESH_SESSION_INVALID",
            fresh_plan=self._fresh_plan(reuse_old=True),
        )
        self.assert_failure(
            "CHUNKED_BASE_BACKUP_RESUME_ADMISSION_FRESH_PERMIT_INVALID",
            fresh_plan=self._fresh_plan(permit_expired=True),
            now=NOW + timedelta(seconds=10),
        )
        self.assert_failure(
            "CHUNKED_BASE_BACKUP_RESUME_ADMISSION_FRESH_PERMIT_REUSE",
            fresh_plan=self._fresh_plan(reuse_old_nonce=True),
        )

    def test_fresh_session_at_previous_expiry_is_not_disjoint(self) -> None:
        self.assert_failure(
            "CHUNKED_BASE_BACKUP_RESUME_ADMISSION_FRESH_SESSION_NOT_FRESH",
            fresh_plan=self._fresh_plan(session_issued_at=self.old_session.expires_at),
        )

    def test_admission_rechecks_checkpoint_before_any_future_adapter_can_use_it(self) -> None:
        admission = self.admit()
        payload = __import__("json").loads(self.raw_checkpoint)
        payload["staged_plaintext_sha256"] = "f" * 64
        self.path.write_bytes(canonical_json_bytes(payload))
        os.chmod(self.path, 0o600)

        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupResumeAdmissionError,
            "^CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_SCOPE_MISMATCH$",
        ):
            require_verified_physical_wal_chunked_base_backup_resume_admission(admission, now=NOW)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
