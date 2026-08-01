from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import core.physical_wal_remote_ack_object_storage_transport as transport
from core.append_only_sync_delta_batch import canonical_json_bytes
from core.physical_wal_remote_ack import (
    build_physical_wal_remote_ack_binding,
    build_physical_wal_remote_ack_request,
)
from core.physical_wal_remote_ack_receiver_ledger import (
    PhysicalWalRemoteAckReceiverLedgerConfig,
    PhysicalWalRemoteAckReceiverRecoveryEvidence,
    derive_physical_wal_remote_ack_receiver_request_binding_sha256,
    issue_physical_wal_remote_ack_receiver_receipt,
    verify_physical_wal_remote_ack_receiver_recovery_evidence,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
FI_RECIPIENT = "age1" + "p" * 52
IR_RECIPIENT = "age1" + "q" * 52


def public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class FakeBody:
    def __init__(self, value: bytes) -> None:
        self.value = value
        self.offset = 0
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.value) - self.offset
        result = self.value[self.offset : self.offset + size]
        self.offset += len(result)
        return result

    def close(self) -> None:
        self.closed = True


class FakeObjectStorage:
    """Only create-only PUT and exact GET are available to the transport."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, object]] = {}
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.tamper_get = False

    def put_object(self, **request: object):
        self.calls.append(("put_object", dict(request)))
        key = request["Key"]
        assert isinstance(key, str)
        if request.get("IfNoneMatch") != "*" or key in self.records:
            raise RuntimeError("conditional write rejected")
        body = request["Body"]
        value = body.read()
        assert isinstance(value, bytes)
        record = {
            "version_id": f"version-20260731-{len(self.records) + 1:02d}",
            "ciphertext": value,
            "metadata": dict(request["Metadata"]),
        }
        self.records[key] = record
        return {"VersionId": record["version_id"]}

    def get_object(self, **request: object):
        self.calls.append(("get_object", dict(request)))
        key = request["Key"]
        version = request["VersionId"]
        assert isinstance(key, str) and isinstance(version, str)
        record = self.records[key]
        if version != record["version_id"]:
            raise RuntimeError("wrong version")
        ciphertext = record["ciphertext"]
        assert isinstance(ciphertext, bytes)
        if self.tamper_get:
            ciphertext = b"age-encryption.org/v1\nwrong"
        return {
            "Key": key,
            "VersionId": version,
            "ContentLength": len(ciphertext),
            "Metadata": dict(record["metadata"]),
            "Body": FakeBody(ciphertext),
        }


class FakeAge:
    def __init__(self) -> None:
        self.encrypt_calls: list[tuple[str, Path, Path]] = []
        self.decrypt_calls: list[tuple[str, Path, Path]] = []

    def encrypt(self, *, recipient: str, plaintext_path: Path, ciphertext_path: Path) -> None:
        self.encrypt_calls.append((recipient, plaintext_path, ciphertext_path))
        ciphertext_path.write_bytes(b"age-encryption.org/v1\n" + plaintext_path.read_bytes())
        os.chmod(ciphertext_path, 0o600)

    def decrypt(
        self,
        *,
        expected_recipient: str,
        ciphertext_path: Path,
        plaintext_path: Path,
    ) -> None:
        self.decrypt_calls.append((expected_recipient, ciphertext_path, plaintext_path))
        value = ciphertext_path.read_bytes()
        if not value.startswith(b"age-encryption.org/v1\n"):
            raise RuntimeError("not age")
        plaintext_path.write_bytes(value[len(b"age-encryption.org/v1\n") :])
        os.chmod(plaintext_path, 0o600)


@unittest.skipUnless(os.geteuid() == 0, "remote-ack Object Storage transport requires root")
class PhysicalWalRemoteAckObjectStorageTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="physical-wal-ack-transport-")
        self.root = Path(self.temporary.name).resolve()
        self.fi_workspace = self.root / "fi-workspace"
        self.ir_workspace = self.root / "ir-workspace"
        self.ledger_root = self.root / "ledger"
        for path in (self.fi_workspace, self.ir_workspace, self.ledger_root):
            path.mkdir(mode=0o700)
            os.chmod(path, 0o700)
        self.fi = Ed25519PrivateKey.generate()
        self.ir = Ed25519PrivateKey.generate()
        self.witness = Ed25519PrivateKey.generate()
        self.binding = build_physical_wal_remote_ack_binding(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            destination_age_recipient=IR_RECIPIENT,
            campaign_id="physical-wal-ack-transport-20260731",
            release_sha=RELEASE,
            stream_generation_id="physical-wal-ack-transport-stream-20260731",
            baseline_generation_id="physical-wal-ack-transport-base-20260731",
            baseline_manifest_sha256="b" * 64,
            writer_epoch=7,
            writer_holder_site="webapp_fi",
            writer_lease_id="writer-lease-seven",
            witnessed_term_proof_sha256="a" * 64,
            target_acknowledged_wal_lsn="0/2000000",
            blob_object_frontier_wal_lsn="0/2000000",
            manifest_sha256es=("b" * 64, "c" * 64),
            object_versions=(
                ("physical/fi-ir/base/backup-001.age", "base-version-001"),
                ("physical/fi-ir/wal/0001.age", "wal-version-001"),
            ),
        )
        self.storage = FakeObjectStorage()
        self.fi_age = FakeAge()
        self.ir_age = FakeAge()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _config(self, *, site: str, workspace: Path, local_recipient: str, peer_recipient: str, **overrides: object):
        values: dict[str, object] = {
            "workspace": workspace,
            "bucket": "private-remote-ack-objects",
            "local_site": site,
            "peer_site": "webapp_ir" if site == "webapp_fi" else "webapp_fi",
            "local_age_recipient": local_recipient,
            "peer_age_recipient": peer_recipient,
            "enabled": True,
            "maximum_ciphertext_bytes": 1024 * 1024,
            "direct_site_control": "forbidden",
            "destination_object_ingest": "pull-only",
        }
        values.update(overrides)
        return transport.PhysicalWalRemoteAckObjectStorageTransportConfig(**values)

    def _transport(self, *, site: str, **overrides: object):
        if site == "webapp_fi":
            config = self._config(
                site=site,
                workspace=self.fi_workspace,
                local_recipient=FI_RECIPIENT,
                peer_recipient=IR_RECIPIENT,
                **overrides,
            )
            age = self.fi_age
        else:
            config = self._config(
                site=site,
                workspace=self.ir_workspace,
                local_recipient=IR_RECIPIENT,
                peer_recipient=FI_RECIPIENT,
                **overrides,
            )
            age = self.ir_age
        return transport.PhysicalWalRemoteAckObjectStorageTransport(
            config=config,
            client_factory=lambda: self.storage,
            age_encryptor_factory=lambda: age,
            age_decryptor_factory=lambda: age,
            expected_source_public_key=public_key(self.fi),
            expected_destination_public_key=public_key(self.ir),
        )

    def _request_raw(self, *, issued_at: datetime = NOW - timedelta(seconds=5)) -> dict:
        return build_physical_wal_remote_ack_request(
            binding=self.binding,
            request_id="request-id-0000000001",
            request_nonce="R" * 22,
            issued_at=issued_at,
            source_signer=self.fi,
        )

    def _request_publication(self):
        return self._transport(site="webapp_fi").publish_request(
            source_request=self._request_raw(), expected_binding=self.binding, now=NOW
        )

    def _request_locator(self, publication):
        return transport.build_physical_wal_remote_ack_request_locator(
            request_publication=publication,
            locator_id="request-locator-0001",
            locator_nonce="L" * 22,
            issued_at=NOW - timedelta(seconds=1),
            expires_at=NOW + timedelta(seconds=30),
            witness_signer=self.witness,
        )

    def _issue_durable_receipt(self, request):
        recovery = PhysicalWalRemoteAckReceiverRecoveryEvidence(
            source_request_sha256=hashlib.sha256(request.source_request).hexdigest(),
            receiver_recovery_evidence_sha256="e" * 64,
            receiver_site="webapp_ir",
            source_site="webapp_fi",
            destination_site="webapp_ir",
            request_binding_sha256=derive_physical_wal_remote_ack_receiver_request_binding_sha256(
                source_request=request, now=NOW
            ),
            manifest_sha256es=request.binding.manifest_sha256es,
            object_versions=request.binding.object_versions,
            replay_lsn="0/2000000",
            observed_at=NOW,
            in_recovery=True,
            role="standby",
        )
        verified_recovery = verify_physical_wal_remote_ack_receiver_recovery_evidence(
            source_request=request, recovery_evidence=recovery, now=NOW
        )
        return issue_physical_wal_remote_ack_receiver_receipt(
            config=PhysicalWalRemoteAckReceiverLedgerConfig(
                state_root=self.ledger_root,
                expected_binding=self.binding,
                expected_source_public_key=public_key(self.fi),
                expected_destination_public_key=public_key(self.ir),
                enabled=True,
                maximum_entries=8,
            ),
            source_request=request,
            recovery_evidence=verified_recovery,
            destination_signer=self.ir,
            now=NOW,
        )

    def test_encrypted_create_only_exact_roundtrip_uses_separate_witness_locators(self) -> None:
        fi_transport = self._transport(site="webapp_fi")
        ir_transport = self._transport(site="webapp_ir")
        publication = self._request_publication()
        request_locator = self._request_locator(publication)
        verified_request_locator = transport.verify_physical_wal_remote_ack_request_locator(
            locator=request_locator,
            expected_binding=self.binding,
            expected_witness_public_key=public_key(self.witness),
            now=NOW,
        )
        received_request = ir_transport.receive_request(
            locator=verified_request_locator,
            expected_binding=self.binding,
            expected_witness_public_key=public_key(self.witness),
            now=NOW,
        )
        self.assertEqual(publication.source_request, received_request.source_request)
        durable_receipt = self._issue_durable_receipt(received_request)
        receipt_publication = ir_transport.publish_receipt(
            request_publication=publication,
            durable_ledger_result=durable_receipt,
            expected_binding=self.binding,
            now=NOW,
        )
        receipt_locator = transport.build_physical_wal_remote_ack_receipt_locator(
            request_publication=publication,
            receipt_publication=receipt_publication,
            locator_id="receipt-locator-0001",
            locator_nonce="M" * 22,
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=30),
            witness_signer=self.witness,
        )
        verified_receipt_locator = transport.verify_physical_wal_remote_ack_receipt_locator(
            locator=receipt_locator,
            expected_binding=self.binding,
            expected_witness_public_key=public_key(self.witness),
            now=NOW,
        )
        evidence = fi_transport.receive_receipt(
            request_publication=publication,
            locator=verified_receipt_locator,
            expected_binding=self.binding,
            expected_witness_public_key=public_key(self.witness),
            now=NOW,
        )
        self.assertEqual(durable_receipt.destination_receipt, evidence.destination_receipt)
        self.assertEqual(6, len(self.storage.calls))
        self.assertEqual(["put_object", "get_object", "get_object", "put_object", "get_object", "get_object"], [name for name, _ in self.storage.calls])
        for name, request in self.storage.calls:
            self.assertEqual("private-remote-ack-objects", request["Bucket"])
            if name == "put_object":
                self.assertEqual("*", request["IfNoneMatch"])
                self.assertEqual("age-v1", request["Metadata"]["encryption"])
            if name == "get_object":
                self.assertIsInstance(request["VersionId"], str)
        self.assertEqual(IR_RECIPIENT, self.fi_age.encrypt_calls[0][0])
        self.assertEqual(IR_RECIPIENT, self.ir_age.decrypt_calls[0][0])
        self.assertEqual(FI_RECIPIENT, self.ir_age.encrypt_calls[0][0])
        self.assertEqual(FI_RECIPIENT, self.fi_age.decrypt_calls[0][0])
        self.assertIn("requests", publication.object_pin.object_key)
        self.assertIn("receipts", receipt_publication.object_pin.object_key)

    def test_locator_forgery_replay_stale_bool_and_invalid_inputs_do_not_touch_storage(self) -> None:
        publication = self._request_publication()
        locator = self._request_locator(publication)
        initial_calls = len(self.storage.calls)
        with self.assertRaisesRegex(transport.PhysicalWalRemoteAckObjectStorageTransportError, "REPLAYED"):
            transport.verify_physical_wal_remote_ack_request_locator(
                locator=locator,
                expected_binding=self.binding,
                expected_witness_public_key=public_key(self.witness),
                now=NOW,
                consumed_locator_ids={"request-locator-0001"},
            )
        stale = self._resign_locator(locator, changes={"issued_at": (NOW - timedelta(seconds=61)).isoformat(), "expires_at": (NOW - timedelta(seconds=1)).isoformat()})
        with self.assertRaisesRegex(transport.PhysicalWalRemoteAckObjectStorageTransportError, "STALE"):
            transport.verify_physical_wal_remote_ack_request_locator(
                locator=stale,
                expected_binding=self.binding,
                expected_witness_public_key=public_key(self.witness),
                now=NOW,
            )
        invalid_bool = self._resign_locator(locator, changes={"version": True})
        with self.assertRaises(transport.PhysicalWalRemoteAckObjectStorageTransportError):
            transport.verify_physical_wal_remote_ack_request_locator(
                locator=invalid_bool,
                expected_binding=self.binding,
                expected_witness_public_key=public_key(self.witness),
                now=NOW,
            )
        forged = self._resign_locator(locator, nested_version="latest")
        with self.assertRaisesRegex(transport.PhysicalWalRemoteAckObjectStorageTransportError, "REQUEST_OBJECT"):
            transport.verify_physical_wal_remote_ack_request_locator(
                locator=forged,
                expected_binding=self.binding,
                expected_witness_public_key=public_key(self.witness),
                now=NOW,
            )
        invalid_transport = self._transport(site="webapp_fi", enabled=False)
        with self.assertRaisesRegex(transport.PhysicalWalRemoteAckObjectStorageTransportError, "DISABLED"):
            invalid_transport.publish_request(
                source_request=self._request_raw(), expected_binding=self.binding, now=NOW
            )
        self.assertEqual(initial_calls, len(self.storage.calls))

    def test_same_request_can_never_overwrite_the_exact_create_only_object(self) -> None:
        publication = self._request_publication()
        before = len(self.storage.calls)
        with self.assertRaisesRegex(transport.PhysicalWalRemoteAckObjectStorageTransportError, "CREATE_ONLY_PUT_FAILED"):
            self._transport(site="webapp_fi").publish_request(
                source_request=self._request_raw(), expected_binding=self.binding, now=NOW
            )
        self.assertEqual(before + 1, len(self.storage.calls))
        self.assertEqual("put_object", self.storage.calls[-1][0])
        self.assertEqual("*", self.storage.calls[-1][1]["IfNoneMatch"])
        self.assertIn(publication.object_pin.object_key, self.storage.records)

    def test_raw_receipt_tampered_local_locator_wrapper_and_object_readback_fail_closed(self) -> None:
        fi_transport = self._transport(site="webapp_fi")
        ir_transport = self._transport(site="webapp_ir")
        publication = self._request_publication()
        locator = self._request_locator(publication)
        verified_locator = transport.verify_physical_wal_remote_ack_request_locator(
            locator=locator,
            expected_binding=self.binding,
            expected_witness_public_key=public_key(self.witness),
            now=NOW,
        )
        object.__setattr__(verified_locator.request_object, "plaintext_bytes", True)
        with self.assertRaisesRegex(transport.PhysicalWalRemoteAckObjectStorageTransportError, "TAMPERED"):
            transport.require_verified_physical_wal_remote_ack_request_locator(
                verified_locator,
                expected_binding=self.binding,
                expected_witness_public_key=public_key(self.witness),
                now=NOW,
            )
        bool_binding_locator = transport.verify_physical_wal_remote_ack_request_locator(
            locator=locator,
            expected_binding=self.binding,
            expected_witness_public_key=public_key(self.witness),
            now=NOW,
        )
        object.__setattr__(bool_binding_locator.binding.writer_term, "writer_epoch", True)
        with self.assertRaisesRegex(transport.PhysicalWalRemoteAckObjectStorageTransportError, "TAMPERED"):
            transport.require_verified_physical_wal_remote_ack_request_locator(
                bool_binding_locator,
                expected_binding=self.binding,
                expected_witness_public_key=public_key(self.witness),
                now=NOW,
            )
        received = ir_transport.receive_request(
            locator=transport.verify_physical_wal_remote_ack_request_locator(
                locator=locator,
                expected_binding=self.binding,
                expected_witness_public_key=public_key(self.witness),
                now=NOW,
            ),
            expected_binding=self.binding,
            expected_witness_public_key=public_key(self.witness),
            now=NOW,
        )
        before = len(self.storage.calls)
        with self.assertRaisesRegex(transport.PhysicalWalRemoteAckObjectStorageTransportError, "DURABLE_LEDGER_RESULT_REQUIRED"):
            ir_transport.publish_receipt(
                request_publication=publication,
                durable_ledger_result=received.source_request,  # type: ignore[arg-type]
                expected_binding=self.binding,
                now=NOW,
            )
        self.assertEqual(before, len(self.storage.calls))
        durable_receipt = self._issue_durable_receipt(received)
        wrong_peer_transport = self._transport(
            site="webapp_ir", peer_age_recipient="age1" + "r" * 52
        )
        with self.assertRaisesRegex(transport.PhysicalWalRemoteAckObjectStorageTransportError, "ROUTE_MISMATCH"):
            wrong_peer_transport.publish_receipt(
                request_publication=publication,
                durable_ledger_result=durable_receipt,
                expected_binding=self.binding,
                now=NOW,
            )
        self.assertEqual(before, len(self.storage.calls))
        receipt_publication = ir_transport.publish_receipt(
            request_publication=publication,
            durable_ledger_result=durable_receipt,
            expected_binding=self.binding,
            now=NOW,
        )
        receipt_locator = transport.build_physical_wal_remote_ack_receipt_locator(
            request_publication=publication,
            receipt_publication=receipt_publication,
            locator_id="receipt-locator-0002",
            locator_nonce="N" * 22,
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=30),
            witness_signer=self.witness,
        )
        verified_receipt_locator = transport.verify_physical_wal_remote_ack_receipt_locator(
            locator=receipt_locator,
            expected_binding=self.binding,
            expected_witness_public_key=public_key(self.witness),
            now=NOW,
        )
        self.storage.tamper_get = True
        with self.assertRaisesRegex(transport.PhysicalWalRemoteAckObjectStorageTransportError, "IDENTITY|READBACK"):
            fi_transport.receive_receipt(
                request_publication=publication,
                locator=verified_receipt_locator,
                expected_binding=self.binding,
                expected_witness_public_key=public_key(self.witness),
                now=NOW,
            )

    def _resign_locator(self, locator, *, changes: dict[str, object] | None = None, nested_version: str | None = None):
        raw = json.loads(locator.signed_locator)
        if changes:
            raw.update(changes)
        if nested_version is not None:
            raw["request"]["version_id"] = nested_version
        unsigned = {key: value for key, value in raw.items() if key != "witness_signature"}
        signature = self.witness.sign(
            transport._WITNESS_LOCATOR_DOMAIN + canonical_json_bytes(unsigned)
        )
        raw["witness_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        }
        return canonical_json_bytes(raw)


if __name__ == "__main__":
    unittest.main()
