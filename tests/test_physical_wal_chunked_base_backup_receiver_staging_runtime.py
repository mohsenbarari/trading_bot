from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

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
from core.physical_wal_chunked_base_backup_receiver_receipt_ledger import (
    PhysicalWalChunkedBaseBackupReceiverReceiptLedgerConfig,
    PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError,
    claim_root_owned_physical_wal_chunked_base_backup_receiver_handoff,
    complete_root_owned_physical_wal_chunked_base_backup_receiver_handoff,
    fail_root_owned_physical_wal_chunked_base_backup_receiver_handoff,
)
from core.physical_wal_chunked_base_backup_receiver_staging_runtime import (
    PhysicalWalChunkedBaseBackupAgeDecryptionObservation,
    PhysicalWalChunkedBaseBackupExactVersionGetObservation,
    PhysicalWalChunkedBaseBackupExactVersionHeadObservation,
    PhysicalWalChunkedBaseBackupReceiverStagingError,
    RootOwnedPhysicalWalChunkedBaseBackupReceiverStagingConfig,
    execute_root_owned_physical_wal_chunked_base_backup_receiver_staging,
)
from core import physical_wal_chunked_base_backup_receiver_staging_runtime as receiver_runtime_module
from core import physical_wal_chunked_base_backup_receiver_receipt_ledger as ledger_module
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
RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
PARTS = (b"first-verified-base-backup-chunk\n" * 300, b"second-verified-base-backup-chunk\n" * 350)


def _public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _nonce(number: int) -> str:
    return f"{number:022d}"


class _ExactAction:
    def __init__(self, receiver: "_ExactReceiver", selector) -> None:
        self.receiver = receiver
        self.selector = selector

    def head_exact_object_version(self, *, object_key: str, version_id: str):
        ciphertext = self.receiver.objects[(object_key, version_id)]
        if self.receiver.wrong_version:
            version_id = "wrong-version-00000000"
        return PhysicalWalChunkedBaseBackupExactVersionHeadObservation(
            object_key=object_key,
            version_id=version_id,
            ciphertext_sha256=hashlib.sha256(ciphertext).hexdigest(),
            ciphertext_bytes=len(ciphertext),
        )

    def get_exact_object_version_to_fd(self, *, object_key: str, version_id: str, destination_fd: int):
        ciphertext = self.receiver.objects[(object_key, version_id)]
        written = b"wrong-ciphertext" if self.receiver.wrong_ciphertext else ciphertext
        os.write(destination_fd, written)
        self.receiver.get_calls += 1
        return PhysicalWalChunkedBaseBackupExactVersionGetObservation(
            object_key=object_key,
            version_id=version_id,
            ciphertext_sha256=hashlib.sha256(ciphertext).hexdigest(),
            ciphertext_bytes=len(ciphertext),
        )


class _ExactReceiver:
    def __init__(self, objects: dict[tuple[str, str], bytes], *, wrong_version: bool = False, wrong_ciphertext: bool = False, callback_leak: bool = False) -> None:
        self.objects = objects
        self.wrong_version = wrong_version
        self.wrong_ciphertext = wrong_ciphertext
        self.callback_leak = callback_leak
        self.get_calls = 0

    def with_exact_chunk_receiver(self, *, selector, callback):
        result = callback(_ExactAction(self, selector))
        return object() if self.callback_leak else result


class _Decryptor:
    def __init__(self, *, wrong_plaintext: bool = False, fail: bool = False) -> None:
        self.wrong_plaintext = wrong_plaintext
        self.fail = fail
        self.calls = 0

    def decrypt_exact_chunk_to_fd(self, *, ciphertext_fd: int, plaintext_fd: int, object_key: str, version_id: str, expected_age_recipient: str):
        self.calls += 1
        if self.fail:
            raise RuntimeError("age failure")
        os.lseek(ciphertext_fd, 0, os.SEEK_SET)
        ciphertext = b""
        while True:
            part = os.read(ciphertext_fd, 1024 * 1024)
            if not part:
                break
            ciphertext += part
        if not ciphertext.startswith(b"age-v2:"):
            raise RuntimeError("not expected age envelope")
        plaintext = b"wrong-plaintext" if self.wrong_plaintext else ciphertext[len(b"age-v2:") :]
        os.write(plaintext_fd, plaintext)
        return PhysicalWalChunkedBaseBackupAgeDecryptionObservation(
            object_key=object_key,
            version_id=version_id,
            age_recipient=expected_age_recipient,
            plaintext_sha256=hashlib.sha256(ciphertext[len(b"age-v2:") :]).hexdigest(),
            plaintext_bytes=len(ciphertext[len(b"age-v2:") :]),
        )


class _EvidenceFixture:
    def __init__(self) -> None:
        self.term_signer = Ed25519PrivateKey.generate()
        self.witness = Ed25519PrivateKey.generate()
        self.source = Ed25519PrivateKey.generate()
        proof = build_object_delta_role_matrix_witnessed_term_proof(
            holder_site="webapp_fi",
            writer_epoch=73,
            writer_lease_id="writer-lease-73",
            witness_transition_id="witness-transition-73",
            issued_at=NOW - timedelta(seconds=10),
            expires_at=NOW + timedelta(seconds=50),
            witness_signer=self.term_signer,
        )
        self.term = verify_object_delta_role_matrix_witnessed_term(
            proof,
            witness_public_key=_public(self.term_signer),
            maximum_lease_duration_seconds=90,
            safety_margin_seconds=5,
            now=NOW,
        )
        total = b"".join(PARTS)
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
                artifact_name="receiver-staging-base-backup.tar",
                plaintext_sha256=hashlib.sha256(total).hexdigest(),
                plaintext_bytes=len(total),
                completion_attestation_sha256=hashlib.sha256(b"completed").hexdigest(),
            ),
            witnessed_term=self.term,
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
            writer_epoch=self.term.writer_epoch,
            writer_lease_id=self.term.writer_lease_id,
            witnessed_term_proof_sha256=self.term.proof_sha256,
        )
        session_raw = build_physical_wal_chunked_base_backup_transfer_session(
            binding=self.binding,
            session_id="receiver-session-0000000001",
            session_nonce=_nonce(1),
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=10),
            witness_signer=self.witness,
        )
        self.session = verify_physical_wal_chunked_base_backup_transfer_session(
            transfer_session=session_raw,
            expected_binding=self.binding,
            expected_witness_public_key=_public(self.witness),
            now=NOW,
        )
        commitments = []
        self.objects: dict[tuple[str, str], bytes] = {}
        for index, plaintext in enumerate(PARTS):
            permit_raw = build_physical_wal_chunked_base_backup_chunk_permit(
                transfer_session=self.session,
                permit_id=f"receiver-permit-{index:012d}",
                permit_nonce=_nonce(100 + index),
                chunk_index=index,
                max_ciphertext_bytes=1024 * 1024,
                issued_at=NOW,
                expires_at=NOW + timedelta(seconds=90),
                witness_signer=self.witness,
            )
            permit = verify_physical_wal_chunked_base_backup_chunk_permit(
                chunk_permit=permit_raw,
                transfer_session=self.session,
                expected_witness_public_key=_public(self.witness),
                now=NOW,
            )
            ciphertext = b"age-v2:" + plaintext
            chunk = PhysicalWalChunkedBaseBackupChunk(
                index=index,
                object_key=permit.object_key,
                version_id=f"receiver-version-{index:012d}",
                ciphertext_sha256=hashlib.sha256(ciphertext).hexdigest(),
                ciphertext_bytes=len(ciphertext),
                plaintext_sha256=hashlib.sha256(plaintext).hexdigest(),
                plaintext_bytes=len(plaintext),
                age_recipient=RECIPIENT,
            )
            completion_raw = build_physical_wal_chunked_base_backup_chunk_completion(
                chunk_permit=permit,
                completion_id=f"receiver-completion-{index:010d}",
                completion_nonce=_nonce(200 + index),
                completed_at=NOW,
                chunk=chunk,
                source_signer=self.source,
            )
            completion = verify_physical_wal_chunked_base_backup_chunk_completion(
                chunk_completion=completion_raw,
                chunk_permit=permit,
                expected_source_public_key=_public(self.source),
                now=NOW,
            )
            commitment_raw = build_physical_wal_chunked_base_backup_witness_chunk_commitment(
                chunk_completion=completion,
                commitment_id=f"receiver-commitment-{index:010d}",
                commitment_nonce=_nonce(300 + index),
                durable_ledger_entry_id=f"receiver-ledger-{index:012d}",
                committed_at=NOW,
                witness_signer=self.witness,
            )
            commitments.append(
                verify_physical_wal_chunked_base_backup_chunk_commitment(
                    chunk_commitment=commitment_raw,
                    chunk_completion=completion,
                    expected_witness_public_key=_public(self.witness),
                    now=NOW,
                )
            )
            self.objects[(chunk.object_key, chunk.version_id)] = ciphertext
        accepted = begin_physical_wal_chunked_base_backup_witness_accepted_chunk_set(
            transfer_session=self.session, now=NOW
        )
        for commitment in commitments:
            accepted = append_physical_wal_chunked_base_backup_witness_accepted_chunk(
                accepted_chunk_set=accepted,
                chunk_commitment=commitment,
                now=NOW,
            )
        finalization_raw = build_physical_wal_chunked_base_backup_finalization_permit(
            transfer_session=self.session,
            accepted_chunk_set=accepted,
            finalization_permit_id="receiver-finalization-00000001",
            finalization_permit_nonce=_nonce(400),
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=90),
            total_plaintext_sha256=hashlib.sha256(total).hexdigest(),
            total_plaintext_bytes=len(total),
            witness_signer=self.witness,
        )
        finalization = verify_physical_wal_chunked_base_backup_finalization_permit(
            finalization_permit=finalization_raw,
            transfer_session=self.session,
            accepted_chunk_set=accepted,
            expected_witness_public_key=_public(self.witness),
            now=NOW,
        )
        manifest_raw = build_physical_wal_chunked_base_backup_manifest(
            finalization_permit=finalization,
            accepted_chunk_set=accepted,
            manifest_id="receiver-manifest-00000000001",
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
        self.lineage = build_physical_wal_chunked_base_backup_lineage_envelope(
            source_binding=source_binding,
            transfer_binding=self.binding,
            now=NOW,
        )
        handoff_raw = build_physical_wal_chunked_base_backup_handoff_receipt(
            manifest=self.manifest,
            lineage_envelope=self.lineage,
            receipt_id="receiver-handoff-receipt-001",
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

    def alternate_handoff(self, *, receipt_id: str, receipt_nonce: str):
        raw = build_physical_wal_chunked_base_backup_handoff_receipt(
            manifest=self.manifest,
            lineage_envelope=self.lineage,
            receipt_id=receipt_id,
            receipt_nonce=receipt_nonce,
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=60),
            witness_signer=self.witness,
        )
        return verify_physical_wal_chunked_base_backup_handoff_receipt(
            handoff_receipt=raw,
            manifest=self.manifest,
            expected_witness_public_key=_public(self.witness),
            now=NOW,
        )


class PhysicalWalChunkedBaseBackupReceiverStagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(prefix="chunked-v2-receiver-")
        self.root = Path(self.temporary.name)
        self.stage_root = self.root / "stage-root"
        self.ledger_root = self.root / "ledger-root"
        self.stage_root.mkdir(mode=0o700)
        self.ledger_root.mkdir(mode=0o700)
        os.chmod(self.stage_root, 0o700)
        os.chmod(self.ledger_root, 0o700)
        self.evidence = _EvidenceFixture()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def ledger_config(self):
        return PhysicalWalChunkedBaseBackupReceiverReceiptLedgerConfig(
            ledger_root=self.ledger_root,
            enabled=True,
        )

    def config(self):
        return RootOwnedPhysicalWalChunkedBaseBackupReceiverStagingConfig(
            staging_root=self.stage_root,
            receipt_ledger_config=self.ledger_config(),
            receiver_site="webapp_ir",
            enabled=True,
        )

    def execute(self, *, receiver=None, decryptor=None, handoff=None, clock=lambda: NOW):
        return execute_root_owned_physical_wal_chunked_base_backup_receiver_staging(
            self.config(),
            manifest=self.evidence.manifest,
            handoff_receipt=handoff or self.evidence.handoff,
            exact_version_receiver=receiver or _ExactReceiver(self.evidence.objects),
            age_decryptor=decryptor or _Decryptor(),
            clock=clock,
        )

    def test_claims_before_get_stages_exact_versions_and_completes_once(self) -> None:
        receiver = _ExactReceiver(self.evidence.objects)
        result = self.execute(receiver=receiver)

        self.assertEqual("staged-not-restored-or-promoted", result.status)
        self.assertEqual(2, result.chunk_count)
        self.assertEqual(hashlib.sha256(b"".join(PARTS)).hexdigest(), result.total_plaintext_sha256)
        self.assertTrue(result.stage_receipt_path.is_file())
        self.assertEqual(2, receiver.get_calls)
        receipt = result.stage_receipt_path.read_bytes()
        self.assertEqual(hashlib.sha256(receipt).hexdigest(), result.stage_receipt_sha256)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverStagingError, "RECEIPT_REPLAYED"):
            self.execute(receiver=_ExactReceiver(self.evidence.objects))

    def test_ledger_independently_burns_id_and_nonce_and_terminal_is_opaque(self) -> None:
        claim = claim_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
            self.ledger_config(), manifest=self.evidence.manifest, handoff_receipt=self.evidence.handoff, now=NOW
        )
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError, "RECEIPT_REPLAYED"):
            claim_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
                self.ledger_config(), manifest=self.evidence.manifest, handoff_receipt=self.evidence.handoff, now=NOW
            )
        same_id = self.evidence.alternate_handoff(
            receipt_id=self.evidence.handoff.receipt_id,
            receipt_nonce=_nonce(601),
        )
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError, "RECEIPT_REPLAYED"):
            claim_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
                self.ledger_config(), manifest=self.evidence.manifest, handoff_receipt=same_id, now=NOW
            )
        same_nonce = self.evidence.alternate_handoff(
            receipt_id="receiver-handoff-receipt-002",
            receipt_nonce=self.evidence.handoff.receipt_nonce,
        )
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError, "RECEIPT_REPLAYED"):
            claim_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
                self.ledger_config(), manifest=self.evidence.manifest, handoff_receipt=same_nonce, now=NOW
            )
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError, "CLAIM_INVALID"):
            complete_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
                self.ledger_config(),
                claim=replace(claim, manifest_sha256="f" * 64),
                stage_receipt_sha256="e" * 64,
                now=NOW,
            )
        fail_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
            self.ledger_config(), claim=claim, failure_code="SYNTHETIC_FAILURE", now=NOW
        )
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError, "RECEIPT_REPLAYED"):
            claim_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
                self.ledger_config(), manifest=self.evidence.manifest, handoff_receipt=self.evidence.handoff, now=NOW
            )

    def test_forged_or_stale_handoff_never_claims_or_gets(self) -> None:
        receiver = _ExactReceiver(self.evidence.objects)
        forged = replace(self.evidence.handoff, snapshot_bytes=self.evidence.handoff.snapshot_bytes + 1)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverStagingError, "HANDOFF_INVALID"):
            self.execute(receiver=receiver, handoff=forged)
        self.assertEqual(0, receiver.get_calls)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverStagingError, "HANDOFF_INVALID"):
            self.execute(receiver=receiver, clock=lambda: NOW + timedelta(minutes=3))
        self.assertEqual(0, receiver.get_calls)

    def test_wrong_selector_ciphertext_or_plaintext_marks_failed_and_cannot_reuse(self) -> None:
        receiver = _ExactReceiver(self.evidence.objects, wrong_version=True)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverStagingError, "EXACT_READBACK_MISMATCH"):
            self.execute(receiver=receiver)
        self.assertEqual(0, receiver.get_calls)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverStagingError, "RECEIPT_REPLAYED"):
            self.execute(receiver=_ExactReceiver(self.evidence.objects))

    def test_wrong_ciphertext_readback_never_returns_receipt(self) -> None:
        receiver = _ExactReceiver(self.evidence.objects, wrong_ciphertext=True)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverStagingError, "CIPHERTEXT_READBACK_MISMATCH"):
            self.execute(receiver=receiver)
        self.assertEqual(1, receiver.get_calls)
        self.assertFalse(any(path.name == "stage-receipt.json" for path in self.stage_root.rglob("*")))

    def test_wrong_plaintext_readback_never_returns_receipt(self) -> None:
        decryptor = _Decryptor(wrong_plaintext=True)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverStagingError, "PLAINTEXT_READBACK_MISMATCH"):
            self.execute(decryptor=decryptor)
        self.assertGreater(decryptor.calls, 0)
        self.assertFalse(any(path.name == "stage-receipt.json" for path in self.stage_root.rglob("*")))

    def test_partial_decryption_failure_never_succeeds_or_reuses_receipt(self) -> None:
        decryptor = _Decryptor(fail=True)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverStagingError, "DECRYPTION_FAILED"):
            self.execute(decryptor=decryptor)
        self.assertFalse(any(path.name == "stage-receipt.json" for path in self.stage_root.rglob("*")))
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverStagingError, "RECEIPT_REPLAYED"):
            self.execute()

    def test_handoff_expiring_during_stage_never_returns_success_receipt(self) -> None:
        calls = 0

        def expiry_clock():
            nonlocal calls
            calls += 1
            return NOW if calls == 1 else NOW + timedelta(minutes=3)

        receiver = _ExactReceiver(self.evidence.objects)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverStagingError, "HANDOFF_INVALID"):
            self.execute(receiver=receiver, clock=expiry_clock)
        self.assertGreater(receiver.get_calls, 0)
        self.assertFalse(any(path.name == "stage-receipt.json" for path in self.stage_root.rglob("*")))

    def test_symlink_root_and_get_failure_are_failed_orphans_not_success(self) -> None:
        target = self.root / "outside-stage"
        target.mkdir(mode=0o700)
        self.stage_root.rmdir()
        self.stage_root.symlink_to(target, target_is_directory=True)
        receiver = _ExactReceiver(self.evidence.objects)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverStagingError, "ROOT_UNSAFE"):
            self.execute(receiver=receiver)
        self.assertEqual(0, receiver.get_calls)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverStagingError, "RECEIPT_REPLAYED"):
            self.execute(receiver=_ExactReceiver(self.evidence.objects))

    def test_nonroot_ledger_and_persistence_failure_never_return_success(self) -> None:
        with patch.object(ledger_module.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError, "ROOT_REQUIRED"):
                claim_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
                    self.ledger_config(), manifest=self.evidence.manifest, handoff_receipt=self.evidence.handoff, now=NOW
                )
        with patch.object(
            receiver_runtime_module,
            "complete_root_owned_physical_wal_chunked_base_backup_receiver_handoff",
            side_effect=PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError("SYNTHETIC_TERMINAL_FAILURE"),
        ):
            with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverStagingError, "FAILURE_LEDGER_MARK_FAILED|SYNTHETIC_TERMINAL_FAILURE"):
                self.execute()

    def test_ledger_lock_or_record_symlink_is_not_followed(self) -> None:
        lock_target = self.root / "lock-target"
        lock_target.write_bytes(b"x")
        lock_path = self.ledger_root / ".receiver-receipt-ledger.lock"
        lock_path.symlink_to(lock_target)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError, "LOCK_UNSAFE"):
            claim_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
                self.ledger_config(), manifest=self.evidence.manifest, handoff_receipt=self.evidence.handoff, now=NOW
            )
        lock_path.unlink()
        (self.ledger_root / "open").symlink_to(self.root / "lock-target")
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError, "DIRECTORY_UNSAFE"):
            claim_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
                self.ledger_config(), manifest=self.evidence.manifest, handoff_receipt=self.evidence.handoff, now=NOW
            )

    def test_crash_after_durable_intent_burns_both_id_and_nonce_axes(self) -> None:
        original = ledger_module._create_json_exclusive
        calls = 0

        def interrupt_after_intent(directory_fd, name, payload):
            nonlocal calls
            calls += 1
            if calls == 2:  # intent was fsync'd; receipt-id index has not begun.
                raise PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError("SYNTHETIC_AFTER_INTENT")
            return original(directory_fd, name, payload)

        with patch.object(ledger_module, "_create_json_exclusive", side_effect=interrupt_after_intent):
            with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError, "SYNTHETIC_AFTER_INTENT"):
                claim_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
                    self.ledger_config(), manifest=self.evidence.manifest, handoff_receipt=self.evidence.handoff, now=NOW
                )
        same_id = self.evidence.alternate_handoff(
            receipt_id=self.evidence.handoff.receipt_id,
            receipt_nonce=_nonce(777),
        )
        same_nonce = self.evidence.alternate_handoff(
            receipt_id="receiver-handoff-receipt-777",
            receipt_nonce=self.evidence.handoff.receipt_nonce,
        )
        for candidate in (same_id, same_nonce):
            with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError, "RECEIPT_REPLAYED"):
                claim_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
                    self.ledger_config(), manifest=self.evidence.manifest, handoff_receipt=candidate, now=NOW
                )

    def test_intent_directory_symlink_is_not_followed(self) -> None:
        target = self.root / "intent-target"
        target.mkdir(mode=0o700)
        (self.ledger_root / "intent").symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError, "DIRECTORY_UNSAFE"):
            claim_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
                self.ledger_config(), manifest=self.evidence.manifest, handoff_receipt=self.evidence.handoff, now=NOW
            )

    def test_malformed_or_partial_intent_is_fail_closed_before_claim_side_effect(self) -> None:
        intent = self.ledger_root / "intent"
        intent.mkdir(mode=0o700)
        os.chmod(intent, 0o700)
        partial = intent / ("a" * 64 + ".json")
        partial.write_bytes(b'{"partial":true')
        os.chmod(partial, 0o600)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError, "JOURNAL_INVALID"):
            claim_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
                self.ledger_config(), manifest=self.evidence.manifest, handoff_receipt=self.evidence.handoff, now=NOW
            )
        self.assertEqual([], list((self.ledger_root / "receipt-id-index").iterdir()))
        self.assertEqual([], list((self.ledger_root / "receipt-nonce-index").iterdir()))

    def test_runtime_has_no_v1_or_network_sdk_surface(self) -> None:
        source = inspect.getsource(receiver_runtime_module)
        ledger_source = inspect.getsource(ledger_module)
        for forbidden in (
            "physical_wal_receiver_staging",
            "PhysicalWalBaseBackupSpoolResult",
            "capture_physical_wal_base_backup",
            "boto3",
            "requests",
            "socket",
            "subprocess",
            "list_objects",
        ):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("boto3", ledger_source)
        self.assertNotIn("socket", ledger_source)
