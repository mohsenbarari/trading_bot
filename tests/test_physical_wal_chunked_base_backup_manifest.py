from __future__ import annotations

import base64
import copy
from datetime import datetime, timedelta, timezone
import inspect
import json
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_wal_chunked_base_backup_manifest as manifest_module
from core.physical_wal_chunked_base_backup_manifest import (
    PhysicalWalChunkedBaseBackupManifestError,
    build_physical_wal_chunked_base_backup_manifest,
    canonical_physical_wal_chunked_base_backup_manifest_bytes,
    require_verified_physical_wal_chunked_base_backup_manifest,
    verify_physical_wal_chunked_base_backup_manifest,
)
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
    verify_physical_wal_chunked_base_backup_chunk_commitment,
    verify_physical_wal_chunked_base_backup_chunk_completion,
    verify_physical_wal_chunked_base_backup_chunk_permit,
    verify_physical_wal_chunked_base_backup_finalization_permit,
    verify_physical_wal_chunked_base_backup_transfer_session,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
CAMPAIGN = "physical-chunked-manifest-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"


def _public_bytes(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class PhysicalWalChunkedBaseBackupManifestTests(unittest.TestCase):
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
        session_raw = build_physical_wal_chunked_base_backup_transfer_session(
            binding=self.binding,
            session_id="manifest-session-0000000001",
            session_nonce="S" * 22,
            issued_at=NOW,
            expires_at=NOW + timedelta(hours=2),
            witness_signer=self.witness,
        )
        self.session = verify_physical_wal_chunked_base_backup_transfer_session(
            transfer_session=session_raw,
            expected_binding=self.binding,
            expected_witness_public_key=_public_bytes(self.witness),
            now=NOW,
        )

    def _commitment(self, index: int, *, permit_nonce: str):
        issued = NOW + timedelta(seconds=10 + index)
        permit_raw = build_physical_wal_chunked_base_backup_chunk_permit(
            transfer_session=self.session,
            permit_id=f"manifest-permit-{index:012d}",
            permit_nonce=permit_nonce,
            chunk_index=index,
            max_ciphertext_bytes=1024 * 1024,
            issued_at=issued,
            expires_at=issued + timedelta(seconds=90),
            witness_signer=self.witness,
        )
        permit = verify_physical_wal_chunked_base_backup_chunk_permit(
            chunk_permit=permit_raw,
            transfer_session=self.session,
            expected_witness_public_key=_public_bytes(self.witness),
            now=issued,
            expected_next_chunk_index=index,
        )
        completed = issued + timedelta(seconds=10)
        completion_raw = build_physical_wal_chunked_base_backup_chunk_completion(
            chunk_permit=permit,
            completion_id=f"manifest-completion-{index:08d}",
            completion_nonce=("C" if index == 0 else "D") * 22,
            completed_at=completed,
            chunk=PhysicalWalChunkedBaseBackupChunk(
                index=index,
                object_key=permit.object_key,
                version_id=f"manifest-version-{index:012d}",
                ciphertext_sha256=("d" if index == 0 else "e") * 64,
                ciphertext_bytes=1000 + index,
                plaintext_sha256=("f" if index == 0 else "1") * 64,
                plaintext_bytes=900 + index,
                age_recipient=RECIPIENT,
            ),
            source_signer=self.source,
        )
        completion = verify_physical_wal_chunked_base_backup_chunk_completion(
            chunk_completion=completion_raw,
            chunk_permit=permit,
            expected_source_public_key=_public_bytes(self.source),
            now=completed,
        )
        committed = completed + timedelta(seconds=5)
        commitment_raw = build_physical_wal_chunked_base_backup_witness_chunk_commitment(
            chunk_completion=completion,
            commitment_id=f"manifest-commitment-{index:08d}",
            commitment_nonce=("E" if index == 0 else "F") * 22,
            durable_ledger_entry_id=f"manifest-ledger-entry-{index:08d}",
            committed_at=committed,
            witness_signer=self.witness,
        )
        return verify_physical_wal_chunked_base_backup_chunk_commitment(
            chunk_commitment=commitment_raw,
            chunk_completion=completion,
            expected_witness_public_key=_public_bytes(self.witness),
            now=committed,
        )

    def _accepted_and_finalization(self):
        first = self._commitment(0, permit_nonce="A" * 22)
        second = self._commitment(1, permit_nonce="B" * 22)
        state = begin_physical_wal_chunked_base_backup_witness_accepted_chunk_set(
            transfer_session=self.session, now=NOW + timedelta(seconds=50)
        )
        state = append_physical_wal_chunked_base_backup_witness_accepted_chunk(
            accepted_chunk_set=state, chunk_commitment=first, now=NOW + timedelta(seconds=50)
        )
        state = append_physical_wal_chunked_base_backup_witness_accepted_chunk(
            accepted_chunk_set=state, chunk_commitment=second, now=NOW + timedelta(seconds=50)
        )
        finalization_raw = build_physical_wal_chunked_base_backup_finalization_permit(
            transfer_session=self.session,
            accepted_chunk_set=state,
            finalization_permit_id="manifest-finalization-00000001",
            finalization_permit_nonce="G" * 22,
            issued_at=NOW + timedelta(seconds=60),
            expires_at=NOW + timedelta(seconds=110),
            total_plaintext_sha256="2" * 64,
            total_plaintext_bytes=1801,
            witness_signer=self.witness,
        )
        finalization = verify_physical_wal_chunked_base_backup_finalization_permit(
            finalization_permit=finalization_raw,
            transfer_session=self.session,
            accepted_chunk_set=state,
            expected_witness_public_key=_public_bytes(self.witness),
            now=NOW + timedelta(seconds=65),
        )
        return state, finalization

    def _manifest(self):
        state, finalization = self._accepted_and_finalization()
        raw = build_physical_wal_chunked_base_backup_manifest(
            finalization_permit=finalization,
            accepted_chunk_set=state,
            manifest_id="manifest-object-000000000001",
            manifest_nonce="H" * 22,
            created_at=NOW + timedelta(seconds=70),
            witness_signer=self.witness,
        )
        return state, finalization, raw

    def _resign(self, payload: dict) -> dict:
        unsigned = {key: value for key, value in payload.items() if key != "witness_signature"}
        signature = self.witness.sign(
            manifest_module._MANIFEST_DOMAIN + canonical_json_bytes(unsigned)
        )
        payload["witness_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        }
        return payload

    def _verify(self, state, finalization, raw, **overrides):
        values = {
            "manifest": raw,
            "finalization_permit": finalization,
            "accepted_chunk_set": state,
            "expected_witness_public_key": _public_bytes(self.witness),
            "now": NOW + timedelta(seconds=75),
        }
        values.update(overrides)
        return verify_physical_wal_chunked_base_backup_manifest(**values)

    def test_builds_exact_ordered_witness_signed_v2_manifest(self):
        state, finalization, raw = self._manifest()
        verified = self._verify(state, finalization, raw)
        self.assertEqual((0, 1), tuple(item.index for item in verified.chunks))
        self.assertEqual(1801, verified.total_plaintext_bytes)
        self.assertEqual("2" * 64, verified.total_plaintext_sha256)
        self.assertEqual(finalization.committed_chunk_set_sha256, raw["committed_chunk_set_sha256"])
        self.assertEqual(
            canonical_physical_wal_chunked_base_backup_manifest_bytes(raw),
            verified.canonical_manifest,
        )
        self.assertIs(
            verified,
            require_verified_physical_wal_chunked_base_backup_manifest(
                verified, now=NOW + timedelta(seconds=75)
            ),
        )
        self.assertNotIn(
            "committed_chunks",
            inspect.signature(build_physical_wal_chunked_base_backup_manifest).parameters,
        )
        self.assertNotIn(
            "total_plaintext_sha256",
            inspect.signature(build_physical_wal_chunked_base_backup_manifest).parameters,
        )
        self.assertNotIn(
            "total_plaintext_bytes",
            inspect.signature(build_physical_wal_chunked_base_backup_manifest).parameters,
        )

    def test_signed_reordered_duplicate_or_gapped_manifest_chunks_are_rejected(self):
        state, finalization, raw = self._manifest()
        reordered = copy.deepcopy(raw)
        reordered["chunks"] = list(reversed(reordered["chunks"]))
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupManifestError, "exact Witness accepted"):
            self._verify(state, finalization, self._resign(reordered))

        duplicate = copy.deepcopy(raw)
        duplicate["chunks"][1] = copy.deepcopy(duplicate["chunks"][0])
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupManifestError, "exact Witness accepted"):
            self._verify(state, finalization, self._resign(duplicate))

        gapped = copy.deepcopy(raw)
        gapped["chunks"][1]["index"] = 2
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupManifestError, "exact Witness accepted"):
            self._verify(state, finalization, self._resign(gapped))

    def test_total_plaintext_bytes_and_finalization_freshness_are_enforced(self):
        state, finalization, raw = self._manifest()
        wrong_total = copy.deepcopy(raw)
        wrong_total["total_plaintext_bytes"] = 1802
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupManifestError, "total plaintext bytes"):
            self._verify(state, finalization, self._resign(wrong_total))
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupTransferError, "expired"):
            self._verify(state, finalization, raw, now=NOW + timedelta(seconds=111))

    def test_witness_resigned_manifest_cannot_alter_total_plaintext_hash_after_finalization(self):
        state, finalization, raw = self._manifest()
        altered = copy.deepcopy(raw)
        altered["total_plaintext_sha256"] = "3" * 64
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupManifestError, "total plaintext hash.*finalization"):
            self._verify(state, finalization, self._resign(altered))

    def test_serialization_and_signature_forgery_fail_closed(self):
        state, finalization, raw = self._manifest()
        noncanonical = json.dumps(raw, sort_keys=True).encode("ascii")
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupManifestError, "canonical"):
            self._verify(state, finalization, noncanonical)
        forged = copy.deepcopy(raw)
        forged["total_plaintext_sha256"] = "3" * 64
        # No corresponding resign: a remote or storage attacker cannot mutate
        # any selector/hash without a pinned Witness key.
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupManifestError, "signature"):
            self._verify(state, finalization, forged)

    def test_manifest_cannot_switch_to_other_accepted_state_or_witness_key(self):
        state, finalization, raw = self._manifest()
        other_witness = Ed25519PrivateKey.generate()
        with self.assertRaisesRegex(PhysicalWalChunkedBaseBackupManifestError, "expected Witness key"):
            self._verify(
                state,
                finalization,
                raw,
                expected_witness_public_key=_public_bytes(other_witness),
            )
