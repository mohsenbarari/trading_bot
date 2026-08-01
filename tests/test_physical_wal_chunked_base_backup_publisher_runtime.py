from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
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
    require_verified_physical_wal_chunked_base_backup_lineage_envelope,
)
from core.physical_wal_chunked_base_backup_manifest import (
    build_physical_wal_chunked_base_backup_manifest,
    verify_physical_wal_chunked_base_backup_manifest,
)
from core.physical_wal_chunked_base_backup_publisher_runtime import (
    MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_PLAINTEXT_BYTES,
    PhysicalWalChunkedBaseBackupPublisherObjectHeadObservation,
    PhysicalWalChunkedBaseBackupPublisherObjectPutObservation,
    PhysicalWalChunkedBaseBackupPublisherRuntimeError,
    RootOwnedPhysicalWalChunkedBaseBackupPublisherConfig,
    execute_root_owned_physical_wal_chunked_base_backup_publisher,
)
from core import physical_wal_chunked_base_backup_publisher_runtime as publisher_runtime_module
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
DATA = (b"v2-root-owned-staged-base-backup\n" * 5_000) + b"end\n"


def _public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _nonce(number: int) -> str:
    return f"{number:022d}"


class _CheckpointSink:
    def __init__(self) -> None:
        self.values: list[bytes] = []

    def persist_checkpoint(self, *, checkpoint: bytes) -> None:
        self.values.append(checkpoint)


class _ObjectAction:
    def __init__(self, *, worker: "_Worker") -> None:
        self.worker = worker

    def put_object_if_none_match(self, *, object_key: str, ciphertext: bytes, if_none_match: str):
        if if_none_match != "*" or object_key != self.worker.permit.object_key:
            raise AssertionError("publisher escaped exact permit")
        with self.worker.mediator.lock:
            if object_key in self.worker.mediator.objects:
                raise AssertionError("object key reused")
            version = f"object-version-{self.worker.permit.chunk_index:012d}"
            self.worker.mediator.objects[object_key] = (version, ciphertext)
            self.worker.mediator.put_calls += 1
        return PhysicalWalChunkedBaseBackupPublisherObjectPutObservation(version_id=version)

    def head_exact_object_version(self, *, object_key: str, version_id: str):
        observed_version, ciphertext = self.worker.mediator.objects[object_key]
        return PhysicalWalChunkedBaseBackupPublisherObjectHeadObservation(
            object_key=object_key,
            version_id=observed_version if not self.worker.bad_head else "wrong-version-00000000",
            ciphertext_sha256=hashlib.sha256(ciphertext).hexdigest(),
            ciphertext_bytes=len(ciphertext),
        )


class _Worker:
    def __init__(
        self,
        *,
        mediator: "_Mediator",
        permit,
        overflow: bool = False,
        completion_failure: bool = False,
        callback_leak: bool = False,
        bad_head: bool = False,
    ) -> None:
        self.mediator = mediator
        self.permit = permit
        self.overflow = overflow
        self.completion_failure = completion_failure
        self.callback_leak = callback_leak
        self.bad_head = bad_head

    def encrypt_chunk(self, *, recipient: str, plaintext: bytes) -> bytes:
        if recipient != RECIPIENT:
            raise AssertionError("recipient was caller-selected")
        if self.overflow:
            return b"X" * (self.permit.max_ciphertext_bytes + 1)
        return b"age-v2:\x00" + plaintext

    def with_exact_chunk_publisher(self, *, permit, callback):
        if permit.canonical_permit != self.permit.canonical_permit:
            raise AssertionError("worker received foreign permit")
        result = callback(_ObjectAction(worker=self))
        return object() if self.callback_leak else result

    def build_completion(self, *, permit, chunk: PhysicalWalChunkedBaseBackupChunk, completed_at: datetime):
        if self.completion_failure:
            raise RuntimeError("source signer unavailable")
        return build_physical_wal_chunked_base_backup_chunk_completion(
            chunk_permit=permit,
            completion_id=f"completion-{permit.chunk_index:012d}",
            completion_nonce=_nonce(50_000 + permit.chunk_index),
            completed_at=completed_at,
            chunk=chunk,
            source_signer=self.mediator.source_signer,
        )


class _WorkerFactory:
    def __init__(self, mediator: "_Mediator", **worker_options: bool) -> None:
        self.mediator = mediator
        self.worker_options = worker_options
        self.created: list[_Worker] = []

    def open_chunk_worker(self, *, permit):
        worker = _Worker(mediator=self.mediator, permit=permit, **self.worker_options)
        self.created.append(worker)
        return worker


class _ReusingWorkerFactory(_WorkerFactory):
    def __init__(self, mediator: "_Mediator") -> None:
        super().__init__(mediator)
        self.shared = None

    def open_chunk_worker(self, *, permit):
        if self.shared is None:
            self.shared = _Worker(mediator=self.mediator, permit=permit)
        return self.shared


class _Mediator:
    parallel_safe = True

    def __init__(self, *, binding, witness: Ed25519PrivateKey, source_signer: Ed25519PrivateKey, reverse_permits: bool = False, expired_finalization: bool = False) -> None:
        self.binding = binding
        self.witness = witness
        self.source_signer = source_signer
        self.reverse_permits = reverse_permits
        self.expired_finalization = expired_finalization
        self.lock = threading.Lock()
        self.objects: dict[str, tuple[str, bytes]] = {}
        self.open_calls = 0
        self.put_calls = 0
        self.accept_calls = 0
        self.finalization_calls = 0
        self.manifest_calls = 0
        self.session = None

    def open_transfer_session(self, *, binding, now: datetime):
        self.open_calls += 1
        raw = build_physical_wal_chunked_base_backup_transfer_session(
            binding=binding,
            session_id="runtime-session-0000000001",
            session_nonce=_nonce(1),
            issued_at=now,
            expires_at=now + timedelta(hours=2),
            witness_signer=self.witness,
        )
        self.session = verify_physical_wal_chunked_base_backup_transfer_session(
            transfer_session=raw,
            expected_binding=binding,
            expected_witness_public_key=_public(self.witness),
            now=now,
        )
        return self.session

    def reserve_chunk_permits(self, *, transfer_session, chunk_indexes: tuple[int, ...], now: datetime):
        values = []
        for index in chunk_indexes:
            raw = build_physical_wal_chunked_base_backup_chunk_permit(
                transfer_session=transfer_session,
                permit_id=f"runtime-permit-{index:012d}",
                permit_nonce=_nonce(1_000 + index),
                chunk_index=index,
                max_ciphertext_bytes=1024 * 1024,
                issued_at=now,
                expires_at=now + timedelta(seconds=90),
                witness_signer=self.witness,
            )
            values.append(
                verify_physical_wal_chunked_base_backup_chunk_permit(
                    chunk_permit=raw,
                    transfer_session=transfer_session,
                    expected_witness_public_key=_public(self.witness),
                    now=now,
                )
            )
        return tuple(reversed(values)) if self.reverse_permits else tuple(values)

    def accept_chunk_completion(self, *, transfer_session, chunk_permit, completion, now: datetime):
        completed = verify_physical_wal_chunked_base_backup_chunk_completion(
            chunk_completion=completion,
            chunk_permit=chunk_permit,
            expected_source_public_key=_public(self.source_signer),
            now=now,
        )
        raw = build_physical_wal_chunked_base_backup_witness_chunk_commitment(
            chunk_completion=completed,
            commitment_id=f"runtime-commitment-{chunk_permit.chunk_index:012d}",
            commitment_nonce=_nonce(10_000 + chunk_permit.chunk_index),
            durable_ledger_entry_id=f"runtime-ledger-{chunk_permit.chunk_index:012d}",
            committed_at=now,
            witness_signer=self.witness,
        )
        with self.lock:
            self.accept_calls += 1
        return verify_physical_wal_chunked_base_backup_chunk_commitment(
            chunk_commitment=raw,
            chunk_completion=completed,
            expected_witness_public_key=_public(self.witness),
            now=now,
        )

    def begin_accepted_chunk_set(self, *, transfer_session, now: datetime):
        return begin_physical_wal_chunked_base_backup_witness_accepted_chunk_set(
            transfer_session=transfer_session, now=now
        )

    def append_accepted_chunk(self, *, accepted_chunk_set, chunk_commitment, now: datetime):
        return append_physical_wal_chunked_base_backup_witness_accepted_chunk(
            accepted_chunk_set=accepted_chunk_set,
            chunk_commitment=chunk_commitment,
            now=now,
        )

    def issue_finalization_permit(self, *, transfer_session, accepted_chunk_set, total_plaintext_sha256: str, total_plaintext_bytes: int, now: datetime):
        self.finalization_calls += 1
        expires = now if self.expired_finalization else now + timedelta(seconds=90)
        raw = build_physical_wal_chunked_base_backup_finalization_permit(
            transfer_session=transfer_session,
            accepted_chunk_set=accepted_chunk_set,
            finalization_permit_id="runtime-finalization-0000001",
            finalization_permit_nonce=_nonce(20_000),
            issued_at=now,
            expires_at=expires,
            total_plaintext_sha256=total_plaintext_sha256,
            total_plaintext_bytes=total_plaintext_bytes,
            witness_signer=self.witness,
        )
        return verify_physical_wal_chunked_base_backup_finalization_permit(
            finalization_permit=raw,
            transfer_session=transfer_session,
            accepted_chunk_set=accepted_chunk_set,
            expected_witness_public_key=_public(self.witness),
            now=now,
        )

    def build_finalized_manifest(self, *, finalization_permit, accepted_chunk_set, now: datetime):
        self.manifest_calls += 1
        raw = build_physical_wal_chunked_base_backup_manifest(
            finalization_permit=finalization_permit,
            accepted_chunk_set=accepted_chunk_set,
            manifest_id="runtime-manifest-00000000001",
            manifest_nonce=_nonce(30_000),
            created_at=now,
            witness_signer=self.witness,
        )
        return verify_physical_wal_chunked_base_backup_manifest(
            manifest=raw,
            finalization_permit=finalization_permit,
            accepted_chunk_set=accepted_chunk_set,
            expected_witness_public_key=_public(self.witness),
            now=now,
        )

    def issue_receiver_handoff_receipt(self, *, manifest, lineage_envelope, now: datetime):
        raw = build_physical_wal_chunked_base_backup_handoff_receipt(
            manifest=manifest,
            lineage_envelope=lineage_envelope,
            receipt_id="runtime-handoff-receipt-001",
            receipt_nonce=_nonce(40_000),
            issued_at=now,
            expires_at=now + timedelta(seconds=60),
            witness_signer=self.witness,
        )
        return verify_physical_wal_chunked_base_backup_handoff_receipt(
            handoff_receipt=raw,
            manifest=manifest,
            expected_witness_public_key=_public(self.witness),
            now=now,
        )


class _RejectAfterPutMediator(_Mediator):
    def accept_chunk_completion(self, *, transfer_session, chunk_permit, completion, now: datetime):
        with self.lock:
            self.accept_calls += 1
        raise RuntimeError("permit was stale at Witness acceptance")


class PhysicalWalChunkedBaseBackupPublisherRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(prefix="physical-wal-chunked-runtime-")
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)
        self.path = self.root / "staged-base-backup.tar"
        self.path.write_bytes(DATA)
        os.chmod(self.path, 0o600)
        self.term_signer = Ed25519PrivateKey.generate()
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
        source_manifest = PhysicalWalBaseBackupManifestBinding(
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
        )
        completed = PhysicalWalBaseBackupCompletedArtifact(
            artifact_name="staged-base-backup.tar",
            plaintext_sha256=hashlib.sha256(DATA).hexdigest(),
            plaintext_bytes=len(DATA),
            completion_attestation_sha256=hashlib.sha256(b"completed").hexdigest(),
        )
        self.source_binding = authorize_physical_wal_base_backup_binding(
            manifest_binding=source_manifest,
            completed_artifact=completed,
            witnessed_term=self.term,
            now=NOW,
        )
        self.binding = build_physical_wal_chunked_base_backup_binding(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=source_manifest.campaign_id,
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
        self.witness = Ed25519PrivateKey.generate()
        self.source_signer = Ed25519PrivateKey.generate()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def config(self):
        return RootOwnedPhysicalWalChunkedBaseBackupPublisherConfig(
            transfer_binding=self.binding,
            source_base_backup_binding=self.source_binding,
            staged_root=self.root,
            staged_filename=self.path.name,
            maximum_chunk_plaintext_bytes=64 * 1024,
            maximum_in_flight_chunks=2,
            enabled=True,
        )

    def execute_runtime(self, *, mediator: _Mediator | None = None, factory=None, **kwargs):
        mediator = mediator or _Mediator(
            binding=self.binding, witness=self.witness, source_signer=self.source_signer
        )
        sink = _CheckpointSink()
        result = execute_root_owned_physical_wal_chunked_base_backup_publisher(
            self.config(),
            chunk_worker_factory=factory or _WorkerFactory(mediator),
            witness_mediator=mediator,
            checkpoint_sink=sink,
            clock=lambda: NOW,
            **kwargs,
        )
        return result, mediator, sink

    def assert_failure(self, code: str, *, mediator: _Mediator, factory=None, **kwargs) -> None:
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupPublisherRuntimeError, f"^{code}$"):
            self.execute_runtime(mediator=mediator, factory=factory, **kwargs)

    def test_publishes_out_of_order_permits_but_finalizes_contiguous_lineage_pinned_manifest(self) -> None:
        mediator = _Mediator(
            binding=self.binding,
            witness=self.witness,
            source_signer=self.source_signer,
            reverse_permits=True,
        )
        result, observed, sink = self.execute_runtime(mediator=mediator)

        self.assertEqual(hashlib.sha256(DATA).hexdigest(), result.staged_plaintext_sha256)
        self.assertEqual(len(DATA), result.staged_plaintext_bytes)
        self.assertEqual(result.manifest.total_plaintext_sha256, result.receiver_handoff_receipt.snapshot_sha256)
        self.assertEqual(result.manifest.total_plaintext_bytes, result.receiver_handoff_receipt.snapshot_bytes)
        self.assertEqual(tuple(range(result.uploaded_chunk_count)), tuple(chunk.index for chunk in result.manifest.chunks))
        self.assertGreaterEqual(len(sink.values), 3)
        last = json.loads(sink.values[-1])
        self.assertEqual(hashlib.sha256(DATA).hexdigest(), last["staged_plaintext_sha256"])
        self.assertEqual(len(DATA), last["staged_plaintext_bytes"])
        self.assertRegex(last["lineage_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(result.uploaded_chunk_count, len(last["accepted_commitments"]))
        self.assertEqual(result.uploaded_chunk_count, observed.accept_calls)

    def test_snapshot_mismatch_blocks_before_creating_a_witness_session(self) -> None:
        self.path.write_bytes(b"changed-but-not-authorized")
        os.chmod(self.path, 0o600)
        mediator = _Mediator(binding=self.binding, witness=self.witness, source_signer=self.source_signer)

        self.assert_failure("CHUNKED_BASE_BACKUP_PUBLISHER_STAGED_SNAPSHOT_MISMATCH", mediator=mediator)
        self.assertEqual(0, mediator.open_calls)

    def test_ciphertext_overflow_has_no_put_completion_or_finalization(self) -> None:
        mediator = _Mediator(binding=self.binding, witness=self.witness, source_signer=self.source_signer)
        factory = _WorkerFactory(mediator, overflow=True)

        self.assert_failure("CHUNKED_BASE_BACKUP_PUBLISHER_CIPHERTEXT_INVALID", mediator=mediator, factory=factory)
        self.assertEqual(0, mediator.put_calls)
        self.assertEqual(0, mediator.accept_calls)
        self.assertEqual(0, mediator.finalization_calls)

    def test_source_completion_failure_never_finalizes(self) -> None:
        mediator = _Mediator(binding=self.binding, witness=self.witness, source_signer=self.source_signer)
        factory = _WorkerFactory(mediator, completion_failure=True)

        self.assert_failure("CHUNKED_BASE_BACKUP_PUBLISHER_SOURCE_COMPLETION_FAILED", mediator=mediator, factory=factory)
        self.assertGreater(mediator.put_calls, 0)
        self.assertEqual(0, mediator.accept_calls)
        self.assertEqual(0, mediator.finalization_calls)

    def test_callback_escape_and_reused_worker_fail_closed(self) -> None:
        mediator = _Mediator(binding=self.binding, witness=self.witness, source_signer=self.source_signer)
        self.assert_failure(
            "CHUNKED_BASE_BACKUP_PUBLISHER_CALLBACK_LEAK_OR_PUT_INVALID",
            mediator=mediator,
            factory=_WorkerFactory(mediator, callback_leak=True),
        )
        self.assertEqual(0, mediator.accept_calls)

        mediator = _Mediator(binding=self.binding, witness=self.witness, source_signer=self.source_signer)
        self.assert_failure(
            "CHUNKED_BASE_BACKUP_PUBLISHER_READBACK_MISMATCH",
            mediator=mediator,
            factory=_WorkerFactory(mediator, bad_head=True),
        )
        self.assertGreater(mediator.put_calls, 0)
        self.assertEqual(0, mediator.accept_calls)

        mediator = _Mediator(binding=self.binding, witness=self.witness, source_signer=self.source_signer)
        self.assert_failure(
            "CHUNKED_BASE_BACKUP_PUBLISHER_WORKER_ISOLATION_INVALID",
            mediator=mediator,
            factory=_ReusingWorkerFactory(mediator),
        )
        self.assertEqual(0, mediator.put_calls)

    def test_stale_permit_after_immutable_put_has_no_manifest_authority(self) -> None:
        mediator = _RejectAfterPutMediator(
            binding=self.binding, witness=self.witness, source_signer=self.source_signer
        )
        self.assert_failure("CHUNKED_BASE_BACKUP_PUBLISHER_WITNESS_ACCEPTANCE_FAILED", mediator=mediator)
        self.assertGreater(mediator.put_calls, 0)
        self.assertGreater(mediator.accept_calls, 0)
        self.assertEqual(0, mediator.finalization_calls)
        self.assertEqual(0, mediator.manifest_calls)

    def test_resume_blob_or_foreign_checkpoint_never_starts_a_fresh_session(self) -> None:
        mediator = _Mediator(binding=self.binding, witness=self.witness, source_signer=self.source_signer)
        self.assert_failure(
            "CHUNKED_BASE_BACKUP_PUBLISHER_RESUME_ADAPTER_REQUIRED",
            mediator=mediator,
            resume_checkpoint=b'{"foreign":"stale"}',
        )
        self.assertEqual(0, mediator.open_calls)

    def test_v1_fallback_or_multipart_policy_is_rejected_before_session(self) -> None:
        for changed in ({"v1_fallback": "allowed"}, {"multipart_upload": "allowed"}):
            mediator = _Mediator(binding=self.binding, witness=self.witness, source_signer=self.source_signer)
            with self.assertRaisesRegex(
                PhysicalWalChunkedBaseBackupPublisherRuntimeError,
                "^CHUNKED_BASE_BACKUP_PUBLISHER_CONFIG_INVALID$",
            ):
                execute_root_owned_physical_wal_chunked_base_backup_publisher(
                    replace(self.config(), **changed),
                    chunk_worker_factory=_WorkerFactory(mediator),
                    witness_mediator=mediator,
                    checkpoint_sink=_CheckpointSink(),
                    clock=lambda: NOW,
                )
            self.assertEqual(0, mediator.open_calls)

    def test_runtime_has_no_legacy_capture_or_uploader_invocation_surface(self) -> None:
        source = inspect.getsource(publisher_runtime_module)
        self.assertNotIn("PhysicalWalBaseBackupSpoolResult", source)
        self.assertNotIn("capture_physical_wal_base_backup", source)
        self.assertNotIn("PhysicalWalBaseBackupUploader", source)

    def test_non_parallel_safe_witness_mediator_is_rejected_before_session(self) -> None:
        mediator = _Mediator(binding=self.binding, witness=self.witness, source_signer=self.source_signer)
        mediator.parallel_safe = False
        self.assert_failure("CHUNKED_BASE_BACKUP_PUBLISHER_DEPENDENCY_INVALID", mediator=mediator)
        self.assertEqual(0, mediator.open_calls)

    def test_resident_plaintext_and_ciphertext_cap_rejects_before_file_or_session_side_effect(self) -> None:
        mediator = _Mediator(binding=self.binding, witness=self.witness, source_signer=self.source_signer)
        constrained = replace(
            self.config(),
            maximum_chunk_plaintext_bytes=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_PLAINTEXT_BYTES,
            maximum_in_flight_chunks=3,
        )
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupPublisherRuntimeError,
            "^CHUNKED_BASE_BACKUP_PUBLISHER_IN_FLIGHT_MEMORY_CAP_EXCEEDED$",
        ):
            execute_root_owned_physical_wal_chunked_base_backup_publisher(
                constrained,
                chunk_worker_factory=_WorkerFactory(mediator),
                witness_mediator=mediator,
                checkpoint_sink=_CheckpointSink(),
                clock=lambda: NOW,
            )
        self.assertEqual(0, mediator.open_calls)

    def test_symlink_staged_file_and_expired_finalization_fail_closed(self) -> None:
        outside = self.root.parent / "outside-staged-backup.tar"
        outside.write_bytes(DATA)
        try:
            self.path.unlink()
            self.path.symlink_to(outside)
            mediator = _Mediator(binding=self.binding, witness=self.witness, source_signer=self.source_signer)
            self.assert_failure("CHUNKED_BASE_BACKUP_PUBLISHER_STAGED_FILE_UNAVAILABLE", mediator=mediator)
            self.assertEqual(0, mediator.open_calls)
        finally:
            if self.path.exists() or self.path.is_symlink():
                self.path.unlink()
            outside.unlink()
            self.path.write_bytes(DATA)
            os.chmod(self.path, 0o600)
        mediator = _Mediator(
            binding=self.binding,
            witness=self.witness,
            source_signer=self.source_signer,
            expired_finalization=True,
        )
        self.assert_failure("CHUNKED_BASE_BACKUP_PUBLISHER_FINALIZATION_FAILED", mediator=mediator)
        self.assertEqual(0, mediator.manifest_calls)

    def test_lineage_envelope_is_opaque_and_rejects_replace_forgery(self) -> None:
        envelope = build_physical_wal_chunked_base_backup_lineage_envelope(
            source_binding=self.source_binding,
            transfer_binding=self.binding,
            now=NOW,
        )
        self.assertFalse(hasattr(envelope, "_source_binding"))
        forged = replace(envelope, snapshot_sha256="f" * 64)
        with self.assertRaisesRegex(Exception, "TAMPERED|REQUIRED"):
            require_verified_physical_wal_chunked_base_backup_lineage_envelope(
                forged,
                transfer_binding=self.binding,
                now=NOW,
            )
