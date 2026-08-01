from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.physical_wal_remote_ack import (
    PhysicalWalRemoteAckObjectVersion,
    build_physical_wal_remote_ack_binding,
    build_physical_wal_remote_ack_request,
    verify_physical_wal_remote_ack_evidence,
    verify_physical_wal_remote_ack_request,
)
from core.physical_wal_remote_ack_receiver_ledger import (
    PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_SCHEMA,
    PhysicalWalRemoteAckReceiverLedgerConfig,
    PhysicalWalRemoteAckReceiverLedgerError,
    PhysicalWalRemoteAckReceiverRecoveryEvidence,
    derive_physical_wal_remote_ack_receiver_request_binding_sha256,
    issue_physical_wal_remote_ack_receiver_receipt,
    verify_physical_wal_remote_ack_receiver_recovery_evidence,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
CAMPAIGN = "physical-wal-ack-ledger-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
RECIPIENT_IR = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
BASE_HASH = "b" * 64
MANIFEST_HASHES = (BASE_HASH, "c" * 64, "d" * 64)


def public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


@unittest.skipUnless(os.geteuid() == 0, "root-only durable-ledger tests require root")
class PhysicalWalRemoteAckReceiverLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fi = Ed25519PrivateKey.generate()
        self.ir = Ed25519PrivateKey.generate()
        self.binding = build_physical_wal_remote_ack_binding(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            destination_age_recipient=RECIPIENT_IR,
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            stream_generation_id="physical-ack-ledger-stream-20260731",
            baseline_generation_id="physical-ack-ledger-base-20260731",
            baseline_manifest_sha256=BASE_HASH,
            writer_epoch=7,
            writer_holder_site="webapp_fi",
            writer_lease_id="writer-lease-seven",
            witnessed_term_proof_sha256="a" * 64,
            target_acknowledged_wal_lsn="0/2000000",
            blob_object_frontier_wal_lsn="0/2000000",
            manifest_sha256es=MANIFEST_HASHES,
            object_versions=(
                ("physical/fi-ir/base/backup-001.age", "base-version-001"),
                ("physical/fi-ir/wal/0001.age", "wal-version-0001"),
                ("physical/fi-ir/blob/inventory-001.age", "inventory-version-001"),
            ),
        )

    def source_request(
        self,
        *,
        request_id: str = "request-id-0000000001",
        request_nonce: str = "R" * 22,
        issued_at: datetime = NOW - timedelta(seconds=5),
    ):
        raw_mapping = build_physical_wal_remote_ack_request(
            binding=self.binding,
            request_id=request_id,
            request_nonce=request_nonce,
            issued_at=issued_at,
            source_signer=self.fi,
        )
        return raw_mapping, verify_physical_wal_remote_ack_request(
            source_request=raw_mapping,
            expected_binding=self.binding,
            expected_source_public_key=public_key(self.fi),
            now=NOW,
        )

    @staticmethod
    def recovery_observation(
        request,
        *,
        evidence_hash: str = "e" * 64,
        replay_lsn: str = "0/2000000",
        observed_at: datetime = NOW,
        in_recovery: bool = True,
        role: str = "standby",
    ) -> PhysicalWalRemoteAckReceiverRecoveryEvidence:
        return PhysicalWalRemoteAckReceiverRecoveryEvidence(
            source_request_sha256=hashlib.sha256(request.source_request).hexdigest(),
            receiver_recovery_evidence_sha256=evidence_hash,
            receiver_site=request.binding.destination_site,
            source_site=request.binding.source_site,
            destination_site=request.binding.destination_site,
            request_binding_sha256=(
                derive_physical_wal_remote_ack_receiver_request_binding_sha256(
                    source_request=request,
                    now=NOW,
                )
            ),
            manifest_sha256es=request.binding.manifest_sha256es,
            object_versions=request.binding.object_versions,
            replay_lsn=replay_lsn,
            observed_at=observed_at,
            in_recovery=in_recovery,
            role=role,
        )

    def verified_recovery(self, request, **overrides):
        observation = self.recovery_observation(request, **overrides)
        return verify_physical_wal_remote_ack_receiver_recovery_evidence(
            source_request=request,
            recovery_evidence=observation,
            now=NOW,
        )

    def ledger_config(self, root: Path, request) -> PhysicalWalRemoteAckReceiverLedgerConfig:
        return PhysicalWalRemoteAckReceiverLedgerConfig(
            state_root=root,
            expected_binding=request.binding,
            expected_source_public_key=request.source_public_key,
            expected_destination_public_key=public_key(self.ir),
            enabled=True,
            maximum_entries=8,
        )

    def issue(self, root: Path, request, recovery, *, destination_signer=Ellipsis):
        if destination_signer is Ellipsis:
            destination_signer = self.ir
        return issue_physical_wal_remote_ack_receiver_receipt(
            config=self.ledger_config(root, request),
            source_request=request,
            recovery_evidence=recovery,
            destination_signer=destination_signer,
            now=NOW,
        )

    def test_durably_issues_exact_signed_receipt_after_verified_recovery_observation(self):
        _raw, request = self.source_request()
        recovery = self.verified_recovery(request)
        with tempfile.TemporaryDirectory() as temporary:
            result = self.issue(Path(temporary).resolve(), request, recovery)

            self.assertFalse(result.idempotent)
            self.assertEqual(hashlib.sha256(request.source_request).hexdigest(), result.source_request_sha256)
            verified = verify_physical_wal_remote_ack_evidence(
                source_request=request.source_request,
                destination_receipt=result.destination_receipt,
                expected_binding=self.binding,
                expected_source_public_key=public_key(self.fi),
                expected_destination_public_key=public_key(self.ir),
                now=NOW,
            )
            self.assertEqual(result.receipt_id, verified.receipt_id)
            self.assertEqual(result.receipt_nonce, verified.receipt_nonce)
            self.assertEqual("0/2000000", result.receiver_replay_lsn)
            self.assertEqual(0o600, stat.S_IMODE(os.lstat(result.ledger_path).st_mode))

            saved = json.loads(result.ledger_path.read_text(encoding="ascii"))
            self.assertEqual(PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_SCHEMA, saved["schema"])
            self.assertEqual(1, len(saved["entries"]))
            self.assertEqual(result.destination_receipt_sha256, saved["entries"][0]["destination_receipt_sha256"])

    def test_same_request_id_and_exact_hash_returns_the_same_receipt_without_signer(self):
        _raw, request = self.source_request()
        recovery = self.verified_recovery(request)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            first = self.issue(root, request, recovery)
            retry = self.issue(root, request, recovery, destination_signer=None)

            self.assertFalse(first.idempotent)
            self.assertTrue(retry.idempotent)
            self.assertEqual(first.destination_receipt, retry.destination_receipt)
            self.assertEqual(first.destination_receipt_sha256, retry.destination_receipt_sha256)
            self.assertEqual(first.receipt_id, retry.receipt_id)

    def test_same_request_id_with_a_different_exact_request_fails_closed(self):
        _raw, first_request = self.source_request()
        _raw, conflicting_request = self.source_request(
            request_nonce="S" * 22,
            issued_at=NOW - timedelta(seconds=4),
        )
        first_recovery = self.verified_recovery(first_request)
        conflicting_recovery = self.verified_recovery(conflicting_request)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.issue(root, first_request, first_recovery)
            with self.assertRaisesRegex(PhysicalWalRemoteAckReceiverLedgerError, "REQUEST_ID_REUSE_CONFLICT"):
                self.issue(root, conflicting_request, conflicting_recovery)

    def test_same_request_nonce_with_a_different_request_id_fails_closed(self):
        _raw, first_request = self.source_request()
        _raw, conflicting_request = self.source_request(
            request_id="request-id-0000000002",
            request_nonce="R" * 22,
            issued_at=NOW - timedelta(seconds=4),
        )
        first_recovery = self.verified_recovery(first_request)
        conflicting_recovery = self.verified_recovery(conflicting_request)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.issue(root, first_request, first_recovery)
            with self.assertRaisesRegex(PhysicalWalRemoteAckReceiverLedgerError, "REQUEST_NONCE_REUSE_CONFLICT"):
                self.issue(root, conflicting_request, conflicting_recovery)

    def test_idempotent_retry_returns_the_original_receipt_without_new_evidence(self):
        _raw, request = self.source_request()
        first_recovery = self.verified_recovery(request, evidence_hash="e" * 64)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            first = self.issue(root, request, first_recovery)
            retry = self.issue(root, request, None, destination_signer=None)
            self.assertTrue(retry.idempotent)
            self.assertEqual(first.destination_receipt, retry.destination_receipt)

    def test_raw_or_fabricated_inputs_cannot_reach_the_signing_runtime(self):
        raw, request = self.source_request()
        observation = self.recovery_observation(request)
        verified_recovery = self.verified_recovery(request)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with self.assertRaisesRegex(
                PhysicalWalRemoteAckReceiverLedgerError,
                "SOURCE_REQUEST_UNVERIFIED_OR_STALE",
            ):
                issue_physical_wal_remote_ack_receiver_receipt(
                    config=self.ledger_config(root, request),
                    source_request=raw,
                    recovery_evidence=verified_recovery,
                    destination_signer=self.ir,
                    now=NOW,
                )
            with self.assertRaisesRegex(
                PhysicalWalRemoteAckReceiverLedgerError,
                "VERIFIED_RECOVERY_EVIDENCE_REQUIRED",
            ):
                issue_physical_wal_remote_ack_receiver_receipt(
                    config=self.ledger_config(root, request),
                    source_request=request,
                    recovery_evidence=observation,
                    destination_signer=self.ir,
                    now=NOW,
                )

    def test_recovery_observation_must_be_exact_fresh_standby_replay_evidence(self):
        _raw, request = self.source_request()
        with self.subTest("behind target"):
            with self.assertRaisesRegex(
                PhysicalWalRemoteAckReceiverLedgerError,
                "RECOVERY_EVIDENCE_REPLAY_LSN_BEHIND_TARGET",
            ):
                self.verified_recovery(request, replay_lsn="0/1000000")
        with self.subTest("not standby"):
            with self.assertRaisesRegex(
                PhysicalWalRemoteAckReceiverLedgerError,
                "RECOVERY_EVIDENCE_NOT_STANDBY_RECOVERY",
            ):
                self.verified_recovery(request, in_recovery=False)
        with self.subTest("stale"):
            _raw, older_request = self.source_request(issued_at=NOW - timedelta(seconds=40))
            with self.assertRaisesRegex(
                PhysicalWalRemoteAckReceiverLedgerError,
                "RECOVERY_EVIDENCE_TIME_STALE",
            ):
                self.verified_recovery(older_request, observed_at=NOW - timedelta(seconds=35))
        with self.subTest("object version substitution"):
            substituted = self.recovery_observation(request)
            object.__setattr__(
                substituted,
                "object_versions",
                (
                    PhysicalWalRemoteAckObjectVersion(
                        "physical/fi-ir/base/backup-001.age", "different-version-001"
                    ),
                ),
            )
            with self.assertRaisesRegex(
                PhysicalWalRemoteAckReceiverLedgerError,
                "RECOVERY_EVIDENCE_OBJECTS_MISMATCH",
            ):
                verify_physical_wal_remote_ack_receiver_recovery_evidence(
                    source_request=request,
                    recovery_evidence=substituted,
                    now=NOW,
                )

    def test_unsafe_root_and_tampered_durable_ledger_fail_closed(self):
        _raw, request = self.source_request()
        recovery = self.verified_recovery(request)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            os.chmod(root, 0o755)
            with self.assertRaisesRegex(PhysicalWalRemoteAckReceiverLedgerError, "LEDGER_STATE_ROOT_UNSAFE"):
                self.issue(root, request, recovery)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            result = self.issue(root, request, recovery)
            result.ledger_path.write_bytes(b"{}")
            os.chmod(result.ledger_path, 0o600)
            with self.assertRaisesRegex(PhysicalWalRemoteAckReceiverLedgerError, "LEDGER_STATE_FIELDS_INVALID"):
                self.issue(root, request, recovery, destination_signer=None)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
