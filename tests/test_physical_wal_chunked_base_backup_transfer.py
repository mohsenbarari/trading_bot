from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_wal_chunked_base_backup_transfer as transfer_module
from core.append_only_sync_delta_batch import canonical_json_bytes
from core.physical_wal_chunked_base_backup_transfer import (
    PhysicalWalChunkedBaseBackupChunk,
    PhysicalWalChunkedBaseBackupTransferError,
    append_physical_wal_chunked_base_backup_witness_accepted_chunk,
    begin_physical_wal_chunked_base_backup_witness_accepted_chunk_set,
    build_physical_wal_chunked_base_backup_binding,
    build_physical_wal_chunked_base_backup_chunk_completion,
    build_physical_wal_chunked_base_backup_chunk_permit,
    build_physical_wal_chunked_base_backup_finalization_permit,
    build_physical_wal_chunked_base_backup_transfer_session,
    build_physical_wal_chunked_base_backup_witness_chunk_commitment,
    derive_physical_wal_chunked_base_backup_chunk_key,
    derive_physical_wal_chunked_base_backup_committed_chunk_set_sha256,
    require_verified_physical_wal_chunked_base_backup_witness_accepted_chunk_set,
    verify_physical_wal_chunked_base_backup_chunk_commitment,
    verify_physical_wal_chunked_base_backup_chunk_completion,
    verify_physical_wal_chunked_base_backup_chunk_permit,
    verify_physical_wal_chunked_base_backup_finalization_permit,
    verify_physical_wal_chunked_base_backup_transfer_session,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
CAMPAIGN = "physical-chunked-transfer-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"


def _public_bytes(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class PhysicalWalChunkedBaseBackupTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.witness = Ed25519PrivateKey.generate()
        self.source = Ed25519PrivateKey.generate()
        self.binding = build_physical_wal_chunked_base_backup_binding(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            object_storage_namespace="physical-wal",
            route_commitment_sha256="a" * 64,
            four_role_binding_sha256="b" * 64,
            destination_age_recipient=RECIPIENT,
            writer_holder_site="webapp_fi",
            writer_epoch=7,
            writer_lease_id="writer-lease-seven",
            witnessed_term_proof_sha256="c" * 64,
        )
        self.session_raw = build_physical_wal_chunked_base_backup_transfer_session(
            binding=self.binding,
            session_id="chunked-session-0000000001",
            session_nonce="S" * 22,
            issued_at=NOW,
            expires_at=NOW + timedelta(hours=2),
            witness_signer=self.witness,
        )
        self.session = verify_physical_wal_chunked_base_backup_transfer_session(
            transfer_session=self.session_raw,
            expected_binding=self.binding,
            expected_witness_public_key=_public_bytes(self.witness),
            now=NOW,
        )

    def permit(self, index: int, *, nonce: str, issued: datetime | None = None, expires: datetime | None = None):
        issued = issued or (NOW + timedelta(seconds=10 + index))
        expires = expires or (issued + timedelta(seconds=90))
        raw = build_physical_wal_chunked_base_backup_chunk_permit(
            transfer_session=self.session,
            permit_id=f"chunked-permit-{index:012d}",
            permit_nonce=nonce,
            chunk_index=index,
            max_ciphertext_bytes=1024 * 1024,
            issued_at=issued,
            expires_at=expires,
            witness_signer=self.witness,
        )
        return verify_physical_wal_chunked_base_backup_chunk_permit(
            chunk_permit=raw,
            transfer_session=self.session,
            expected_witness_public_key=_public_bytes(self.witness),
            now=issued,
            expected_next_chunk_index=index,
        )

    def completion(self, permit, index: int, *, completed_at: datetime | None = None):
        completed_at = completed_at or (permit.issued_at + timedelta(seconds=10))
        chunk = PhysicalWalChunkedBaseBackupChunk(
            index=index,
            object_key=permit.object_key,
            version_id=f"chunk-version-{index:012d}",
            ciphertext_sha256=("d" if index == 0 else "e") * 64,
            ciphertext_bytes=1000 + index,
            plaintext_sha256=("f" if index == 0 else "1") * 64,
            plaintext_bytes=900 + index,
            age_recipient=RECIPIENT,
        )
        raw = build_physical_wal_chunked_base_backup_chunk_completion(
            chunk_permit=permit,
            completion_id=f"chunked-completion-{index:08d}",
            completion_nonce=("C" if index == 0 else "D") * 22,
            completed_at=completed_at,
            chunk=chunk,
            source_signer=self.source,
        )
        return verify_physical_wal_chunked_base_backup_chunk_completion(
            chunk_completion=raw,
            chunk_permit=permit,
            expected_source_public_key=_public_bytes(self.source),
            now=completed_at,
        )

    def commitment(self, completion, index: int, *, committed_at: datetime | None = None):
        committed_at = committed_at or (completion.completed_at + timedelta(seconds=5))
        raw = build_physical_wal_chunked_base_backup_witness_chunk_commitment(
            chunk_completion=completion,
            commitment_id=f"chunked-commitment-{index:08d}",
            commitment_nonce=("E" if index == 0 else "F") * 22,
            durable_ledger_entry_id=f"chunked-ledger-entry-{index:08d}",
            committed_at=committed_at,
            witness_signer=self.witness,
        )
        return verify_physical_wal_chunked_base_backup_chunk_commitment(
            chunk_commitment=raw,
            chunk_completion=completion,
            expected_witness_public_key=_public_bytes(self.witness),
            now=committed_at,
        )

    def accepted_two_chunks(self):
        permit0 = self.permit(0, nonce="A" * 22)
        permit1 = self.permit(1, nonce="B" * 22)
        commitment0 = self.commitment(self.completion(permit0, 0), 0)
        commitment1 = self.commitment(self.completion(permit1, 1), 1)
        state = begin_physical_wal_chunked_base_backup_witness_accepted_chunk_set(
            transfer_session=self.session, now=NOW + timedelta(seconds=50)
        )
        state = append_physical_wal_chunked_base_backup_witness_accepted_chunk(
            accepted_chunk_set=state, chunk_commitment=commitment0, now=NOW + timedelta(seconds=50)
        )
        return append_physical_wal_chunked_base_backup_witness_accepted_chunk(
            accepted_chunk_set=state, chunk_commitment=commitment1, now=NOW + timedelta(seconds=50)
        )

    def test_witness_pins_independent_short_lived_chunks_and_contiguous_state(self):
        state = self.accepted_two_chunks()
        self.assertEqual(2, state.next_chunk_index)
        self.assertEqual((0, 1), tuple(item.chunk.index for item in state.committed_chunks))
        self.assertEqual(
            state.committed_chunk_set_sha256,
            derive_physical_wal_chunked_base_backup_committed_chunk_set_sha256(
                accepted_chunk_set=state, now=NOW + timedelta(seconds=55)
            ),
        )
        self.assertIs(
            state,
            require_verified_physical_wal_chunked_base_backup_witness_accepted_chunk_set(
                state, now=NOW + timedelta(seconds=55)
            ),
        )
        key = derive_physical_wal_chunked_base_backup_chunk_key(
            binding=self.binding,
            session_id=self.session.session_id,
            chunk_index=0,
            permit_nonce="A" * 22,
        )
        self.assertEqual(state.committed_chunks[0].chunk.object_key, key)
        self.assertTrue(key.startswith(f"physical-wal/{CAMPAIGN}/{RELEASE}/base-backup-v2/"))

    def test_permit_is_stale_after_its_deadline_and_late_completion_cannot_be_committed(self):
        issued = NOW + timedelta(seconds=10)
        expires = issued + timedelta(seconds=60)
        permit = self.permit(0, nonce="A" * 22, issued=issued, expires=expires)
        raw = build_physical_wal_chunked_base_backup_chunk_permit(
            transfer_session=self.session,
            permit_id="chunked-permit-000000000000",
            permit_nonce="A" * 22,
            chunk_index=0,
            max_ciphertext_bytes=1024 * 1024,
            issued_at=issued,
            expires_at=expires,
            witness_signer=self.witness,
        )
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupTransferError, "expired"):
            verify_physical_wal_chunked_base_backup_chunk_permit(
                chunk_permit=raw,
                transfer_session=self.session,
                expected_witness_public_key=_public_bytes(self.witness),
                now=expires + timedelta(seconds=1),
            )
        completion = self.completion(permit, 0)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupTransferError, "deadline|expired"):
            build_physical_wal_chunked_base_backup_witness_chunk_commitment(
                chunk_completion=completion,
                commitment_id="chunked-commitment-00000000",
                commitment_nonce="E" * 22,
                durable_ledger_entry_id="chunked-ledger-entry-00000000",
                committed_at=expires + timedelta(seconds=1),
                witness_signer=self.witness,
            )

    def test_foreign_route_term_and_key_fail_closed(self):
        foreign_binding = replace(self.binding, route_commitment_sha256="9" * 64)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupTransferError, "foreign"):
            verify_physical_wal_chunked_base_backup_transfer_session(
                transfer_session=self.session_raw,
                expected_binding=foreign_binding,
                expected_witness_public_key=_public_bytes(self.witness),
                now=NOW,
            )
        foreign_term = replace(
            self.binding,
            writer_term=replace(self.binding.writer_term, writer_epoch=8),
        )
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupTransferError, "foreign"):
            verify_physical_wal_chunked_base_backup_transfer_session(
                transfer_session=self.session_raw,
                expected_binding=foreign_term,
                expected_witness_public_key=_public_bytes(self.witness),
                now=NOW,
            )
        permit = self.permit(0, nonce="A" * 22)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupTransferError, "foreign to its permit"):
            build_physical_wal_chunked_base_backup_chunk_completion(
                chunk_permit=permit,
                completion_id="chunked-completion-00000000",
                completion_nonce="C" * 22,
                completed_at=NOW + timedelta(seconds=25),
                chunk=PhysicalWalChunkedBaseBackupChunk(
                    index=0,
                    object_key="physical-wal/foreign/backup.age",
                    version_id="chunk-version-000000000000",
                    ciphertext_sha256="d" * 64,
                    ciphertext_bytes=1000,
                    plaintext_sha256="f" * 64,
                    plaintext_bytes=900,
                    age_recipient=RECIPIENT,
                ),
                source_signer=self.source,
            )

    def test_reordered_duplicate_and_gapped_chunks_are_not_accepted(self):
        permit0 = self.permit(0, nonce="A" * 22)
        permit1 = self.permit(1, nonce="B" * 22)
        commitment0 = self.commitment(self.completion(permit0, 0), 0)
        commitment1 = self.commitment(self.completion(permit1, 1), 1)
        state = begin_physical_wal_chunked_base_backup_witness_accepted_chunk_set(
            transfer_session=self.session, now=NOW + timedelta(seconds=50)
        )
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupTransferError, "next contiguous"):
            append_physical_wal_chunked_base_backup_witness_accepted_chunk(
                accepted_chunk_set=state, chunk_commitment=commitment1, now=NOW + timedelta(seconds=50)
            )
        state = append_physical_wal_chunked_base_backup_witness_accepted_chunk(
            accepted_chunk_set=state, chunk_commitment=commitment0, now=NOW + timedelta(seconds=50)
        )
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupTransferError, "next contiguous"):
            append_physical_wal_chunked_base_backup_witness_accepted_chunk(
                accepted_chunk_set=state, chunk_commitment=commitment0, now=NOW + timedelta(seconds=50)
            )

    def test_bounded_permits_can_be_reserved_in_parallel_but_acceptance_stays_contiguous(self):
        # Both permits are independently live before either object is accepted.
        # ``reserved_chunk_indexes`` is the Witness durable issuance-window
        # observation, not a serial upload gate.
        permit0 = self.permit(0, nonce="A" * 22)
        issued1 = NOW + timedelta(seconds=11)
        raw1 = build_physical_wal_chunked_base_backup_chunk_permit(
            transfer_session=self.session,
            permit_id="chunked-permit-000000000001",
            permit_nonce="B" * 22,
            chunk_index=1,
            max_ciphertext_bytes=1024 * 1024,
            issued_at=issued1,
            expires_at=issued1 + timedelta(seconds=90),
            witness_signer=self.witness,
        )
        permit1 = verify_physical_wal_chunked_base_backup_chunk_permit(
            chunk_permit=raw1,
            transfer_session=self.session,
            expected_witness_public_key=_public_bytes(self.witness),
            now=issued1,
            reserved_chunk_indexes={0},
            expected_next_chunk_index=1,
        )
        commitment1 = self.commitment(self.completion(permit1, 1), 1)
        state = begin_physical_wal_chunked_base_backup_witness_accepted_chunk_set(
            transfer_session=self.session, now=NOW + timedelta(seconds=40)
        )
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupTransferError, "next contiguous"):
            append_physical_wal_chunked_base_backup_witness_accepted_chunk(
                accepted_chunk_set=state, chunk_commitment=commitment1, now=NOW + timedelta(seconds=40)
            )
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupTransferError, "non-empty accepted"):
            build_physical_wal_chunked_base_backup_finalization_permit(
                transfer_session=self.session,
                accepted_chunk_set=state,
                finalization_permit_id="chunked-finalization-00000002",
                finalization_permit_nonce="G" * 22,
                issued_at=NOW + timedelta(seconds=45),
                expires_at=NOW + timedelta(seconds=100),
                total_plaintext_sha256="2" * 64,
                total_plaintext_bytes=1,
                witness_signer=self.witness,
            )
        commitment0 = self.commitment(self.completion(permit0, 0), 0)
        state = append_physical_wal_chunked_base_backup_witness_accepted_chunk(
            accepted_chunk_set=state, chunk_commitment=commitment0, now=NOW + timedelta(seconds=50)
        )
        state = append_physical_wal_chunked_base_backup_witness_accepted_chunk(
            accepted_chunk_set=state, chunk_commitment=commitment1, now=NOW + timedelta(seconds=50)
        )
        self.assertEqual(2, state.next_chunk_index)

    def test_finalization_requires_opaque_nonempty_witness_accepted_state(self):
        empty = begin_physical_wal_chunked_base_backup_witness_accepted_chunk_set(
            transfer_session=self.session, now=NOW + timedelta(seconds=40)
        )
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupTransferError, "non-empty accepted"):
            build_physical_wal_chunked_base_backup_finalization_permit(
                transfer_session=self.session,
                accepted_chunk_set=empty,
                finalization_permit_id="chunked-finalization-00000001",
                finalization_permit_nonce="G" * 22,
                issued_at=NOW + timedelta(seconds=45),
                expires_at=NOW + timedelta(seconds=100),
                total_plaintext_sha256="2" * 64,
                total_plaintext_bytes=1,
                witness_signer=self.witness,
            )
        state = self.accepted_two_chunks()
        raw = build_physical_wal_chunked_base_backup_finalization_permit(
            transfer_session=self.session,
            accepted_chunk_set=state,
            finalization_permit_id="chunked-finalization-00000001",
            finalization_permit_nonce="G" * 22,
            issued_at=NOW + timedelta(seconds=60),
            expires_at=NOW + timedelta(seconds=110),
            total_plaintext_sha256="2" * 64,
            total_plaintext_bytes=1801,
            witness_signer=self.witness,
        )
        verified = verify_physical_wal_chunked_base_backup_finalization_permit(
            finalization_permit=raw,
            transfer_session=self.session,
            accepted_chunk_set=state,
            expected_witness_public_key=_public_bytes(self.witness),
            now=NOW + timedelta(seconds=65),
        )
        self.assertEqual(2, verified.committed_chunk_count)

    def test_replay_observations_and_tampered_capabilities_fail_closed(self):
        permit = self.permit(0, nonce="A" * 22)
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupTransferError, "replayed"):
            verify_physical_wal_chunked_base_backup_chunk_permit(
                chunk_permit=permit.canonical_permit,
                transfer_session=self.session,
                expected_witness_public_key=_public_bytes(self.witness),
                now=permit.issued_at,
                consumed_permit_ids={permit.permit_id},
            )
        forged = replace(permit, object_key="physical-wal/forged.age")
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupTransferError, "capability|tampered"):
            build_physical_wal_chunked_base_backup_chunk_completion(
                chunk_permit=forged,
                completion_id="chunked-completion-00000000",
                completion_nonce="C" * 22,
                completed_at=permit.issued_at + timedelta(seconds=1),
                chunk=PhysicalWalChunkedBaseBackupChunk(
                    index=0,
                    object_key=permit.object_key,
                    version_id="chunk-version-000000000000",
                    ciphertext_sha256="d" * 64,
                    ciphertext_bytes=1000,
                    plaintext_sha256="f" * 64,
                    plaintext_bytes=900,
                    age_recipient=RECIPIENT,
                ),
                source_signer=self.source,
            )

    def test_source_completion_explicitly_binds_exact_permit_nonce_and_hash(self):
        permit = self.permit(0, nonce="A" * 22)
        completed_at = permit.issued_at + timedelta(seconds=10)
        raw = build_physical_wal_chunked_base_backup_chunk_completion(
            chunk_permit=permit,
            completion_id="chunked-completion-00000000",
            completion_nonce="C" * 22,
            completed_at=completed_at,
            chunk=PhysicalWalChunkedBaseBackupChunk(
                index=0,
                object_key=permit.object_key,
                version_id="chunk-version-000000000000",
                ciphertext_sha256="d" * 64,
                ciphertext_bytes=1000,
                plaintext_sha256="f" * 64,
                plaintext_bytes=900,
                age_recipient=RECIPIENT,
            ),
            source_signer=self.source,
        )
        self.assertEqual(permit.permit_nonce, raw["permit_nonce"])
        self.assertEqual(permit.permit_id, raw["permit_id"])
        changed = dict(raw)
        changed["permit_nonce"] = "Z" * 22
        unsigned = {key: value for key, value in changed.items() if key != "source_signature"}
        changed["source_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": base64.b64encode(
                self.source.sign(transfer_module._COMPLETION_DOMAIN + canonical_json_bytes(unsigned))
            ).decode("ascii"),
        }
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupTransferError, "exact permit and session"):
            verify_physical_wal_chunked_base_backup_chunk_completion(
                chunk_completion=changed,
                chunk_permit=permit,
                expected_source_public_key=_public_bytes(self.source),
                now=completed_at,
            )
