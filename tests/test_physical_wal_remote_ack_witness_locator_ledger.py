from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import base64
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

from core.append_only_sync_delta_batch import canonical_json_bytes
from core.physical_wal_remote_ack import (
    build_physical_wal_remote_ack_binding,
    build_physical_wal_remote_ack_receipt,
    build_physical_wal_remote_ack_request,
)
import core.physical_wal_remote_ack_object_storage_transport as transport
import core.physical_wal_remote_ack_witness_locator_ledger as locator_ledger


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
FI_RECIPIENT = "age1" + "p" * 52
IR_RECIPIENT = "age1" + "q" * 52


def public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


@unittest.skipUnless(os.geteuid() == 0, "root-only Witness locator ledger tests require root")
class PhysicalWalRemoteAckWitnessLocatorLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fi = Ed25519PrivateKey.generate()
        self.ir = Ed25519PrivateKey.generate()
        self.witness = Ed25519PrivateKey.generate()
        self.binding = self._binding()

    def _binding(self, *, writer_epoch: int = 7, campaign: str = "physical-wal-witness-locator-20260731"):
        return build_physical_wal_remote_ack_binding(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            destination_age_recipient=IR_RECIPIENT,
            campaign_id=campaign,
            release_sha=RELEASE,
            stream_generation_id="physical-wal-witness-locator-stream-20260731",
            baseline_generation_id="physical-wal-witness-locator-base-20260731",
            baseline_manifest_sha256="b" * 64,
            writer_epoch=writer_epoch,
            writer_holder_site="webapp_fi",
            writer_lease_id=f"writer-lease-{writer_epoch}",
            witnessed_term_proof_sha256=("a" if writer_epoch == 7 else "d") * 64,
            target_acknowledged_wal_lsn="0/2000000",
            blob_object_frontier_wal_lsn="0/2000000",
            manifest_sha256es=("b" * 64, "c" * 64),
            object_versions=(
                ("physical/fi-ir/base/backup-001.age", "base-version-001"),
                ("physical/fi-ir/wal/0001.age", "wal-version-001"),
            ),
        )

    def _config(self, root: Path, *, binding=None, **overrides: object):
        values: dict[str, object] = {
            "state_root": root,
            "expected_binding": binding or self.binding,
            "expected_witness_public_key": public_key(self.witness),
            "enabled": True,
            "maximum_entries": 8,
        }
        values.update(overrides)
        return locator_ledger.PhysicalWalRemoteAckWitnessLocatorLedgerConfig(**values)

    def _request_raw(self, *, binding=None, request_id: str = "request-id-0000000001") -> bytes:
        return canonical_json_bytes(
            build_physical_wal_remote_ack_request(
                binding=binding or self.binding,
                request_id=request_id,
                request_nonce="R" * 22,
                issued_at=NOW - timedelta(seconds=5),
                source_signer=self.fi,
            )
        )

    @staticmethod
    def _object_pin(
        *,
        binding,
        role: str,
        plaintext: bytes,
        request_sha256: str,
        receipt_sha256: str | None,
    ) -> dict[str, object]:
        recipient = binding.destination_age_recipient if role == "request" else FI_RECIPIENT
        return {
            "role": role,
            "object_key": transport._object_key(
                binding=binding,
                role=role,
                request_sha256=request_sha256,
                receipt_sha256=receipt_sha256,
            ),
            "version_id": f"{role}-version-20260731-0001",
            "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
            "plaintext_bytes": len(plaintext),
            "ciphertext_sha256": hashlib.sha256((role + "-ciphertext").encode()).hexdigest(),
            "ciphertext_bytes": len(plaintext) + 128,
            "encryption": "age-v1",
            "age_recipient": recipient,
        }

    def _signed_locator(
        self,
        *,
        kind: str,
        binding,
        request_raw: bytes,
        locator_id: str,
        locator_nonce: str,
        issued_at: datetime = NOW - timedelta(seconds=1),
        expires_at: datetime = NOW + timedelta(seconds=30),
    ) -> bytes:
        request_sha256 = hashlib.sha256(request_raw).hexdigest()
        request_pin = self._object_pin(
            binding=binding,
            role="request",
            plaintext=request_raw,
            request_sha256=request_sha256,
            receipt_sha256=None,
        )
        unsigned: dict[str, object] = {
            "schema": transport.PHYSICAL_WAL_REMOTE_ACK_OBJECT_STORAGE_TRANSPORT_LOCATOR_SCHEMA,
            "version": transport.PHYSICAL_WAL_REMOTE_ACK_OBJECT_STORAGE_TRANSPORT_VERSION,
            "kind": kind,
            "binding": transport._binding_mapping(binding),
            "source_age_recipient": FI_RECIPIENT,
            "request": request_pin,
            "locator_id": locator_id,
            "locator_nonce": locator_nonce,
            "issued_at": issued_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "witness_signer": {
                "algorithm": "ed25519",
                "public_key_base64": base64.b64encode(public_key(self.witness)).decode("ascii"),
                "key_id": "ed25519-sha256:" + hashlib.sha256(public_key(self.witness)).hexdigest(),
            },
        }
        if kind == "remote_ack_receipt_locator":
            receipt_raw = canonical_json_bytes(
                build_physical_wal_remote_ack_receipt(
                    source_request=request_raw,
                    receipt_id="receipt-id-0000000001",
                    receipt_nonce="S" * 22,
                    acknowledged_at=NOW,
                    destination_signer=self.ir,
                )
            )
            receipt_sha256 = hashlib.sha256(receipt_raw).hexdigest()
            unsigned["receipt"] = self._object_pin(
                binding=binding,
                role="receipt",
                plaintext=receipt_raw,
                request_sha256=request_sha256,
                receipt_sha256=receipt_sha256,
            )
        signature = self.witness.sign(
            transport._WITNESS_LOCATOR_DOMAIN + canonical_json_bytes(unsigned)
        )
        return canonical_json_bytes(
            {
                **unsigned,
                "witness_signature": {
                    "algorithm": "ed25519",
                    "signature_base64": base64.b64encode(signature).decode("ascii"),
                },
            }
        )

    def _request_locator(
        self,
        *,
        binding=None,
        request_raw: bytes | None = None,
        locator_id: str = "request-locator-0001",
        locator_nonce: str = "L" * 22,
        issued_at: datetime = NOW - timedelta(seconds=1),
        expires_at: datetime = NOW + timedelta(seconds=30),
        verify_now: datetime = NOW,
    ):
        selected_binding = binding or self.binding
        raw = self._signed_locator(
            kind="remote_ack_request_locator",
            binding=selected_binding,
            request_raw=request_raw or self._request_raw(binding=selected_binding),
            locator_id=locator_id,
            locator_nonce=locator_nonce,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        return transport.verify_physical_wal_remote_ack_request_locator(
            locator=raw,
            expected_binding=selected_binding,
            expected_witness_public_key=public_key(self.witness),
            now=verify_now,
        )

    def _receipt_locator(
        self,
        *,
        binding=None,
        request_raw: bytes | None = None,
        locator_id: str = "receipt-locator-0001",
        locator_nonce: str = "M" * 22,
    ):
        selected_binding = binding or self.binding
        raw = self._signed_locator(
            kind="remote_ack_receipt_locator",
            binding=selected_binding,
            request_raw=request_raw or self._request_raw(binding=selected_binding),
            locator_id=locator_id,
            locator_nonce=locator_nonce,
        )
        return transport.verify_physical_wal_remote_ack_receipt_locator(
            locator=raw,
            expected_binding=selected_binding,
            expected_witness_public_key=public_key(self.witness),
            now=NOW,
        )

    @staticmethod
    def _root() -> tempfile.TemporaryDirectory[str]:
        return tempfile.TemporaryDirectory(prefix="physical-wal-witness-locator-ledger-")

    @staticmethod
    def _children(root: Path) -> list[str]:
        return sorted(path.name for path in root.iterdir())

    def test_exact_request_then_matching_receipt_is_atomic_ordered_and_redacted(self) -> None:
        with self._root() as temporary:
            root = Path(temporary).resolve()
            os.chmod(root, 0o700)
            config = self._config(root)
            request = self._request_locator()
            receipt = self._receipt_locator()

            with self.assertRaisesRegex(
                locator_ledger.PhysicalWalRemoteAckWitnessLocatorLedgerError,
                "RECEIPT_REQUEST_NOT_ADMITTED",
            ):
                locator_ledger.admit_receipt_locator(config=config, locator=receipt, now=NOW)
            self.assertEqual([], self._children(root))

            admitted_request = locator_ledger.admit_request_locator(
                config=config, locator=request, now=NOW
            )
            retry_request = locator_ledger.admit_request_locator(
                config=config, locator=request, now=NOW
            )
            admitted_receipt = locator_ledger.admit_receipt_locator(
                config=config, locator=receipt, now=NOW
            )
            retry_receipt = locator_ledger.admit_receipt_locator(
                config=config, locator=receipt, now=NOW
            )
            self.assertFalse(admitted_request.idempotent)
            self.assertTrue(retry_request.idempotent)
            self.assertFalse(admitted_receipt.idempotent)
            self.assertTrue(retry_receipt.idempotent)
            self.assertEqual(admitted_request.locator_sha256, retry_request.locator_sha256)
            self.assertEqual(admitted_receipt.locator_sha256, retry_receipt.locator_sha256)
            self.assertNotIn(request.locator_nonce, repr(admitted_request))
            self.assertNotIn(receipt.locator_nonce, repr(admitted_receipt))
            self.assertNotIn(request.signed_locator.decode("ascii"), repr(admitted_request))

            directory = root / "physical-wal-remote-ack-witness-locator-ledger"
            ledger_path = directory / "ledger.json"
            self.assertEqual(0o700, stat.S_IMODE(os.lstat(directory).st_mode))
            self.assertEqual(0o600, stat.S_IMODE(os.lstat(ledger_path).st_mode))
            saved = json.loads(ledger_path.read_text(encoding="ascii"))
            self.assertEqual(locator_ledger.PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_SCHEMA, saved["schema"])
            self.assertEqual(["request", "receipt"], [item["kind"] for item in saved["entries"]])
            self.assertEqual([1, 2], [item["sequence"] for item in saved["entries"]])
            self.assertNotIn("signed_locator", json.dumps(saved))
            self.assertNotIn(request.signed_locator.decode("ascii"), json.dumps(saved))

    def test_invalid_foreign_changed_term_stale_and_tampered_inputs_never_create_state(self) -> None:
        with self._root() as temporary:
            root = Path(temporary).resolve()
            os.chmod(root, 0o700)
            request = self._request_locator()
            changed_term = self._binding(writer_epoch=8)
            with self.assertRaisesRegex(
                locator_ledger.PhysicalWalRemoteAckWitnessLocatorLedgerError,
                "REQUEST_UNVERIFIED_OR_STALE",
            ):
                locator_ledger.admit_request_locator(
                    config=self._config(root, binding=changed_term), locator=request, now=NOW
                )
            self.assertEqual([], self._children(root))

            foreign_binding = self._binding(campaign="physical-wal-witness-locator-foreign-20260731")
            foreign = self._request_locator(binding=foreign_binding)
            with self.assertRaisesRegex(
                locator_ledger.PhysicalWalRemoteAckWitnessLocatorLedgerError,
                "REQUEST_UNVERIFIED_OR_STALE",
            ):
                locator_ledger.admit_request_locator(
                    config=self._config(root), locator=foreign, now=NOW
                )
            self.assertEqual([], self._children(root))

            stale = self._request_locator(
                issued_at=NOW - timedelta(seconds=61),
                expires_at=NOW + timedelta(seconds=29),
                verify_now=NOW - timedelta(seconds=30),
            )
            with self.assertRaisesRegex(
                locator_ledger.PhysicalWalRemoteAckWitnessLocatorLedgerError,
                "REQUEST_UNVERIFIED_OR_STALE",
            ):
                locator_ledger.admit_request_locator(
                    config=self._config(root), locator=stale, now=NOW
                )
            self.assertEqual([], self._children(root))

            tampered = self._request_locator()
            object.__setattr__(tampered, "locator_nonce", "Z" * 22)
            with self.assertRaisesRegex(
                locator_ledger.PhysicalWalRemoteAckWitnessLocatorLedgerError,
                "REQUEST_UNVERIFIED_OR_STALE",
            ):
                locator_ledger.admit_request_locator(
                    config=self._config(root), locator=tampered, now=NOW
                )
            self.assertEqual([], self._children(root))

            with self.assertRaisesRegex(
                locator_ledger.PhysicalWalRemoteAckWitnessLocatorLedgerError,
                "REQUEST_UNVERIFIED_OR_STALE",
            ):
                locator_ledger.admit_request_locator(
                    config=self._config(root), locator=request.signed_locator, now=NOW
                )
            self.assertEqual([], self._children(root))

    def test_mismatch_and_replayed_identity_fail_without_mutating_existing_ledger(self) -> None:
        with self._root() as temporary:
            root = Path(temporary).resolve()
            os.chmod(root, 0o700)
            config = self._config(root)
            request_raw = self._request_raw(request_id="request-id-0000000001")
            request = self._request_locator(request_raw=request_raw)
            locator_ledger.admit_request_locator(config=config, locator=request, now=NOW)
            ledger_path = root / "physical-wal-remote-ack-witness-locator-ledger" / "ledger.json"
            before = ledger_path.read_bytes()

            other_raw = self._request_raw(request_id="request-id-0000000002")
            mismatch_receipt = self._receipt_locator(request_raw=other_raw)
            with self.assertRaisesRegex(
                locator_ledger.PhysicalWalRemoteAckWitnessLocatorLedgerError,
                "RECEIPT_REQUEST_NOT_ADMITTED",
            ):
                locator_ledger.admit_receipt_locator(
                    config=config, locator=mismatch_receipt, now=NOW
                )
            self.assertEqual(before, ledger_path.read_bytes())

            id_reuse = self._request_locator(
                request_raw=other_raw,
                locator_id="request-locator-0001",
                locator_nonce="N" * 22,
            )
            with self.assertRaisesRegex(
                locator_ledger.PhysicalWalRemoteAckWitnessLocatorLedgerError,
                "LOCATOR_ID_REUSE_CONFLICT",
            ):
                locator_ledger.admit_request_locator(config=config, locator=id_reuse, now=NOW)
            self.assertEqual(before, ledger_path.read_bytes())

    def test_concurrent_exact_retry_yields_one_append_and_one_idempotent_result(self) -> None:
        with self._root() as temporary:
            root = Path(temporary).resolve()
            os.chmod(root, 0o700)
            config = self._config(root)
            request = self._request_locator()
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(
                        locator_ledger.admit_request_locator,
                        config=config,
                        locator=request,
                        now=NOW,
                    )
                    for _ in range(2)
                ]
                results = [future.result() for future in futures]
            self.assertEqual({False, True}, {item.idempotent for item in results})
            ledger_path = root / "physical-wal-remote-ack-witness-locator-ledger" / "ledger.json"
            self.assertEqual(1, len(json.loads(ledger_path.read_text(encoding="ascii"))["entries"]))

    def test_unsafe_root_and_tampered_state_fail_closed_without_rewrite(self) -> None:
        request = self._request_locator()
        with self._root() as temporary:
            root = Path(temporary).resolve()
            os.chmod(root, 0o755)
            with self.assertRaisesRegex(
                locator_ledger.PhysicalWalRemoteAckWitnessLocatorLedgerError,
                "STATE_ROOT_UNSAFE",
            ):
                locator_ledger.admit_request_locator(
                    config=self._config(root), locator=request, now=NOW
                )

        with self._root() as temporary:
            root = Path(temporary).resolve()
            os.chmod(root, 0o700)
            directory = root / "physical-wal-remote-ack-witness-locator-ledger"
            directory.mkdir(mode=0o700)
            os.chmod(directory, 0o700)
            receipt = self._receipt_locator()
            with self.assertRaisesRegex(
                locator_ledger.PhysicalWalRemoteAckWitnessLocatorLedgerError,
                "LOCK_MISSING",
            ):
                locator_ledger.admit_receipt_locator(
                    config=self._config(root), locator=receipt, now=NOW
                )
            self.assertEqual([], self._children(directory))

        with self._root() as temporary:
            root = Path(temporary).resolve()
            os.chmod(root, 0o700)
            config = self._config(root)
            locator_ledger.admit_request_locator(config=config, locator=request, now=NOW)
            ledger_path = root / "physical-wal-remote-ack-witness-locator-ledger" / "ledger.json"
            ledger_path.write_bytes(b"{}")
            os.chmod(ledger_path, 0o600)
            before = ledger_path.read_bytes()
            with self.assertRaisesRegex(
                locator_ledger.PhysicalWalRemoteAckWitnessLocatorLedgerError,
                "STATE_FIELDS_INVALID",
            ):
                locator_ledger.admit_request_locator(config=config, locator=request, now=NOW)
            self.assertEqual(before, ledger_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
