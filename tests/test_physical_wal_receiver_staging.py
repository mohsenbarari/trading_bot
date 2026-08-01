from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import inspect
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import canonical_json_bytes
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_BLOB_CHAIN_GENESIS_SHA256,
    PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256,
    build_physical_wal_base_backup_manifest,
    build_physical_wal_blob_frontier_manifest,
    build_physical_wal_segment_manifest,
    verify_physical_wal_object_storage_bundle,
)
from core.physical_wal_receiver_staging import (
    PHYSICAL_WAL_RECEIVER_BLOCKED_STATUS,
    PHYSICAL_WAL_RECEIVER_STAGING_STATUS,
    PhysicalWalDecryptionReadback,
    PhysicalWalExactVersionReadback,
    PhysicalWalReceiverStagingConfig,
    build_physical_wal_receiver_staging_pin,
    stage_physical_wal_object_storage_bundle,
)


CAMPAIGN = "physical-wal-stage-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
TERM_PROOF = "a" * 64
RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
WAL_BYTES = 16 * 1024 * 1024
WAL_PLAINTEXT = b"W" * WAL_BYTES


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise RuntimeError("short test write")
        view = view[written:]


@dataclass
class _Reader:
    ciphertexts: dict[tuple[str, str], bytes]
    malformed_receipt: bool = False
    tamper: bool = False
    fail_after: int | None = None
    calls: int = 0

    def read_exact_to_fd(
        self, *, object_key: str, version_id: str, destination_fd: int
    ) -> PhysicalWalExactVersionReadback:
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("simulated adapter interruption")
        payload = self.ciphertexts[(object_key, version_id)]
        if self.tamper:
            payload = b"tampered-ciphertext"
        _write_all(destination_fd, payload)
        if self.malformed_receipt:
            return PhysicalWalExactVersionReadback(
                object_key=object_key,
                version_id="foreign-version-001",
                ciphertext_sha256=_sha256(payload),
                ciphertext_bytes=len(payload),
            )
        # This reports the signed value even when the written data is hostile;
        # the receiver must independently hash/read back the actual FD.
        signed = self.ciphertexts[(object_key, version_id)]
        return PhysicalWalExactVersionReadback(
            object_key=object_key,
            version_id=version_id,
            ciphertext_sha256=_sha256(signed),
            ciphertext_bytes=len(signed),
        )


@dataclass
class _Decryptor:
    plaintexts: dict[tuple[str, str], bytes]
    tamper_object_key: str | None = None
    calls: int = 0

    def decrypt_to_fd(
        self,
        *,
        ciphertext_fd: int,
        destination_fd: int,
        object_key: str,
        version_id: str,
        expected_age_recipient: str,
    ) -> PhysicalWalDecryptionReadback:
        self.calls += 1
        # Exercise the supplied ciphertext descriptor rather than replacing it
        # with a source-side file path.  It deliberately has no crypto adapter.
        os.lseek(ciphertext_fd, 0, os.SEEK_SET)
        if not os.read(ciphertext_fd, 1):
            raise RuntimeError("empty staged ciphertext")
        payload = self.plaintexts[(object_key, version_id)]
        if self.tamper_object_key == object_key:
            payload = b"tampered-inventory-plaintext"
        _write_all(destination_fd, payload)
        return PhysicalWalDecryptionReadback(
            object_key=object_key,
            version_id=version_id,
            age_recipient=expected_age_recipient,
            plaintext_sha256=_sha256(payload),
            plaintext_bytes=len(payload),
        )


class _NeverRead:
    def __init__(self) -> None:
        self.called = False

    def read_exact_to_fd(self, **_kwargs):
        self.called = True
        raise AssertionError("idempotent retry attempted a source read")


class _NeverDecrypt:
    def __init__(self) -> None:
        self.called = False

    def decrypt_to_fd(self, **_kwargs):
        self.called = True
        raise AssertionError("idempotent retry attempted decryption")


class PhysicalWalReceiverStagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = Ed25519PrivateKey.generate()
        self.public_key = self.signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    def _descriptor(
        self,
        *,
        kind: str,
        key: str,
        version: str,
        ciphertext: bytes,
    ) -> dict[str, object]:
        return {
            "schema": "gold-trade-physical-wal-object-descriptor-v1",
            "version": 1,
            "object_kind": kind,
            "object_key": key,
            "version_id": version,
            "ciphertext_sha256": _sha256(ciphertext),
            "ciphertext_bytes": len(ciphertext),
            "encryption": "age-v1",
            "age_recipient": RECIPIENT,
            "immutability": "versioned_create_only_readback_v1",
        }

    def _route(
        self,
        *,
        source_site: str = "webapp_fi",
        destination_site: str = "webapp_ir",
        suffix: str = "fi-ir",
        generation: str = "fi-ir-stage-base-20260731",
    ):
        base_ciphertext = ("cipher-base-" + suffix).encode("ascii")
        wal_ciphertext = ("cipher-wal-" + suffix).encode("ascii")
        inventory_ciphertext = ("cipher-inventory-" + suffix).encode("ascii")
        base_key = f"physical/{suffix}/base/backup-001.age"
        wal_key = f"physical/{suffix}/wal/0001.age"
        inventory_key = f"physical/{suffix}/blob/inventory-001.age"
        ciphertexts = {
            (base_key, "base-version-001"): base_ciphertext,
            (wal_key, "wal-version-0001"): wal_ciphertext,
            (inventory_key, "inventory-version-001"): inventory_ciphertext,
        }
        plaintexts = {
            (base_key, "base-version-001"): b"physical-base-backup-v1",
            (wal_key, "wal-version-0001"): WAL_PLAINTEXT,
            (inventory_key, "inventory-version-001"): b'{"inventory":"complete"}\n',
        }
        base = build_physical_wal_base_backup_manifest(
            source_site=source_site,
            destination_site=destination_site,
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=7,
            writer_lease_id="writer-lease-seven",
            witnessed_term_proof_sha256=TERM_PROOF,
            baseline_generation_id=generation,
            database_system_identifier="7234567890123456789",
            timeline_id=1,
            wal_segment_size_bytes=WAL_BYTES,
            baseline_wal_lsn="0/1000000",
            wal_chain_start_lsn="0/1000000",
            base_backup_end_lsn="0/1800000",
            base_backup_object=self._descriptor(
                kind="physical_postgresql_base_backup",
                key=base_key,
                version="base-version-001",
                ciphertext=base_ciphertext,
            ),
            source_signer=self.signer,
        )
        base_hash = _sha256(canonical_json_bytes(base))
        wal = build_physical_wal_segment_manifest(
            source_site=source_site,
            destination_site=destination_site,
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=7,
            writer_lease_id="writer-lease-seven",
            witnessed_term_proof_sha256=TERM_PROOF,
            baseline_generation_id=generation,
            baseline_manifest_sha256=base_hash,
            database_system_identifier="7234567890123456789",
            timeline_id=1,
            wal_segment_size_bytes=WAL_BYTES,
            previous_manifest_sha256=PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256,
            previous_end_lsn="0/1000000",
            previous_segment_ordinal=0,
            segments=(
                {
                    "ordinal": 1,
                    "wal_segment_name": "000000010000000000000001",
                    "timeline_id": 1,
                    "start_lsn": "0/1000000",
                    "end_lsn": "0/2000000",
                    "object": self._descriptor(
                        kind="postgresql_wal_segment",
                        key=wal_key,
                        version="wal-version-0001",
                        ciphertext=wal_ciphertext,
                    ),
                },
            ),
            source_signer=self.signer,
        )
        inventory_plaintext = plaintexts[(inventory_key, "inventory-version-001")]
        blob = build_physical_wal_blob_frontier_manifest(
            source_site=source_site,
            destination_site=destination_site,
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=7,
            writer_lease_id="writer-lease-seven",
            witnessed_term_proof_sha256=TERM_PROOF,
            baseline_generation_id=generation,
            baseline_manifest_sha256=base_hash,
            database_system_identifier="7234567890123456789",
            timeline_id=1,
            wal_segment_size_bytes=WAL_BYTES,
            previous_manifest_sha256=PHYSICAL_WAL_BLOB_CHAIN_GENESIS_SHA256,
            previous_frontier_wal_lsn="0/1000000",
            blob_object_frontier_wal_lsn="0/2000000",
            inventory_shards=(
                {
                    "ordinal": 1,
                    "plaintext_sha256": _sha256(inventory_plaintext),
                    "plaintext_bytes": len(inventory_plaintext),
                    "entry_count": 1,
                    "object": self._descriptor(
                        kind="blob_inventory_shard",
                        key=inventory_key,
                        version="inventory-version-001",
                        ciphertext=inventory_ciphertext,
                    ),
                },
            ),
            source_signer=self.signer,
        )
        bundle = verify_physical_wal_object_storage_bundle(
            base_backup_manifest=base,
            wal_segment_manifests=(wal,),
            blob_frontier_manifest=blob,
            expected_source_public_key=self.public_key,
            expected_source_site=source_site,
            expected_destination_site=destination_site,
            expected_campaign_id=CAMPAIGN,
            expected_release_sha=RELEASE,
            expected_writer_epoch=7,
            expected_writer_lease_id="writer-lease-seven",
            expected_witnessed_term_proof_sha256=TERM_PROOF,
            expected_baseline_generation_id=generation,
            expected_wal_segment_size_bytes=WAL_BYTES,
            expected_destination_age_recipient=RECIPIENT,
        )
        baseline = bundle.baseline
        pin = build_physical_wal_receiver_staging_pin(
            source_site=source_site,
            destination_site=destination_site,
            source_public_key=self.public_key,
            destination_age_recipient=RECIPIENT,
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=7,
            writer_lease_id="writer-lease-seven",
            witnessed_term_proof_sha256=TERM_PROOF,
            baseline_generation_id=generation,
            baseline_manifest_sha256=baseline.manifest_sha256,
            database_system_identifier=baseline.database_system_identifier,
            timeline_id=baseline.timeline_id,
            wal_segment_size_bytes=baseline.wal_segment_size_bytes,
            baseline_wal_lsn=baseline.baseline_wal_lsn,
            wal_chain_start_lsn=baseline.wal_chain_start_lsn,
            base_backup_end_lsn=baseline.base_backup_end_lsn,
        )
        return bundle, pin, ciphertexts, plaintexts

    def _config(self, root: Path) -> PhysicalWalReceiverStagingConfig:
        receiver = root / "receiver"
        state = root / "state"
        receiver.mkdir(mode=0o700)
        state.mkdir(mode=0o700)
        return PhysicalWalReceiverStagingConfig(receiver_root=receiver, state_root=state)

    def _stage(self, *, route=None, config, reader=None, decryptor=None):
        bundle, pin, ciphertexts, plaintexts = route or self._route()
        reader = reader or _Reader(ciphertexts)
        decryptor = decryptor or _Decryptor(plaintexts)
        result = stage_physical_wal_object_storage_bundle(
            bundle=bundle,
            pin=pin,
            config=config,
            exact_version_reader=reader,
            decryptor=decryptor,
        )
        return result, bundle, pin, ciphertexts, plaintexts, reader, decryptor

    def test_stages_exact_versioned_bundle_and_declares_no_replay_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self._config(Path(temporary))
            result, bundle, _pin, _ciphertexts, plaintexts, reader, decryptor = self._stage(
                config=config
            )

            self.assertEqual(PHYSICAL_WAL_RECEIVER_STAGING_STATUS, result.status)
            self.assertFalse(result.idempotent)
            self.assertEqual("webapp_ir", result.receiver_site)
            self.assertEqual(bundle.manifest_sha256es, result.manifest_sha256es)
            self.assertEqual(3, reader.calls)
            self.assertEqual(3, decryptor.calls)
            self.assertIsNotNone(result.candidate_path)
            candidate = result.candidate_path
            assert candidate is not None
            self.assertEqual(
                WAL_PLAINTEXT,
                (candidate / "material/wal/000000010000000000000001").read_bytes(),
            )
            self.assertEqual(
                plaintexts[("physical/fi-ir/blob/inventory-001.age", "inventory-version-001")],
                (candidate / "material/blob-inventory/00000001.inventory").read_bytes(),
            )
            self.assertTrue((candidate / "stage-receipt.json").is_file())
            self.assertEqual(0, stat.S_IMODE(os.stat(candidate / "stage-receipt.json").st_mode) & 0o077)

    def test_reverse_ir_to_fi_route_derives_receiver_site_from_verified_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self._config(Path(temporary))
            route = self._route(
                source_site="webapp_ir",
                destination_site="webapp_fi",
                suffix="ir-fi",
                generation="ir-fi-stage-base-20260731",
            )
            result, _bundle, pin, _ciphertexts, _plaintexts, _reader, _decryptor = self._stage(
                route=route, config=config
            )

            self.assertTrue(result.staged)
            self.assertEqual("webapp_fi", result.receiver_site)
            self.assertEqual("webapp_ir", pin.source_site)
            self.assertEqual("webapp_fi", pin.destination_site)

    def test_malicious_reader_receipt_and_ciphertext_tamper_fail_before_decrypt(self):
        with self.subTest("foreign receipt"):
            with tempfile.TemporaryDirectory() as temporary:
                config = self._config(Path(temporary))
                route = self._route()
                reader = _Reader(route[2], malformed_receipt=True)
                decryptor = _Decryptor(route[3])
                result, *_rest = self._stage(
                    route=route, config=config, reader=reader, decryptor=decryptor
                )
                self.assertEqual(PHYSICAL_WAL_RECEIVER_BLOCKED_STATUS, result.status)
                self.assertEqual(("EXACT_VERSION_READER_RECEIPT_FOREIGN_OR_ALIAS",), result.reason_codes)
                self.assertEqual(0, decryptor.calls)

        with self.subTest("ciphertext readback"):
            with tempfile.TemporaryDirectory() as temporary:
                config = self._config(Path(temporary))
                route = self._route()
                reader = _Reader(route[2], tamper=True)
                decryptor = _Decryptor(route[3])
                result, *_rest = self._stage(
                    route=route, config=config, reader=reader, decryptor=decryptor
                )
                self.assertEqual(PHYSICAL_WAL_RECEIVER_BLOCKED_STATUS, result.status)
                self.assertEqual(("CIPHERTEXT_READBACK_HASH_OR_SIZE_MISMATCH",), result.reason_codes)
                self.assertEqual(0, decryptor.calls)

    def test_tampered_plaintext_cannot_bypass_signed_blob_inventory_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self._config(Path(temporary))
            route = self._route()
            decryptor = _Decryptor(
                route[3], tamper_object_key="physical/fi-ir/blob/inventory-001.age"
            )
            result, *_rest = self._stage(
                route=route, config=config, reader=_Reader(route[2]), decryptor=decryptor
            )

            self.assertEqual(PHYSICAL_WAL_RECEIVER_BLOCKED_STATUS, result.status)
            self.assertEqual(("PLAINTEXT_DOES_NOT_MATCH_SIGNED_INVENTORY_BINDING",), result.reason_codes)

    def test_completed_candidate_resume_is_idempotent_without_source_read_or_decrypt(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self._config(Path(temporary))
            first, bundle, pin, _ciphertexts, _plaintexts, _reader, _decryptor = self._stage(
                config=config
            )
            assert first.bundle_id is not None
            # Simulate a crash after durable object/manifest consumption but
            # immediately before the final completion record is persisted.
            (config.state_root / "completed" / (first.bundle_id + ".json")).unlink()
            never_reader = _NeverRead()
            never_decryptor = _NeverDecrypt()
            resumed = stage_physical_wal_object_storage_bundle(
                bundle=bundle,
                pin=pin,
                config=config,
                exact_version_reader=never_reader,
                decryptor=never_decryptor,
            )

            self.assertEqual(PHYSICAL_WAL_RECEIVER_STAGING_STATUS, resumed.status)
            self.assertTrue(resumed.idempotent)
            self.assertFalse(never_reader.called)
            self.assertFalse(never_decryptor.called)
            self.assertTrue((config.state_root / "completed" / (first.bundle_id + ".json")).is_file())

    def test_partial_candidate_is_quarantined_then_freshly_staged_on_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self._config(Path(temporary))
            route = self._route()
            interrupted = _Reader(route[2], fail_after=1)
            blocked, *_rest = self._stage(
                route=route,
                config=config,
                reader=interrupted,
                decryptor=_Decryptor(route[3]),
            )
            self.assertEqual(("EXACT_VERSION_READER_FAILED",), blocked.reason_codes)
            retried, *_rest = self._stage(
                route=route,
                config=config,
                reader=_Reader(route[2]),
                decryptor=_Decryptor(route[3]),
            )
            self.assertTrue(retried.staged)
            self.assertFalse(retried.idempotent)
            self.assertTrue(any((config.receiver_root / "quarantine").iterdir()))

    def test_local_object_record_conflict_blocks_replay_and_no_data_is_reread(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self._config(Path(temporary))
            first, bundle, pin, _ciphertexts, _plaintexts, _reader, _decryptor = self._stage(
                config=config
            )
            assert first.bundle_id is not None
            (config.state_root / "completed" / (first.bundle_id + ".json")).unlink()
            base_object = bundle.baseline.base_backup_object
            identifier = _sha256(
                canonical_json_bytes(
                    {"object_key": base_object.object_key, "version_id": base_object.version_id}
                )
            )
            record = config.state_root / "consumed/objects" / (identifier + ".json")
            os.chmod(record, 0o600)
            record.write_bytes(b"{}")
            os.chmod(record, 0o400)
            never_reader = _NeverRead()
            never_decryptor = _NeverDecrypt()
            replay = stage_physical_wal_object_storage_bundle(
                bundle=bundle,
                pin=pin,
                config=config,
                exact_version_reader=never_reader,
                decryptor=never_decryptor,
            )

            self.assertEqual(PHYSICAL_WAL_RECEIVER_BLOCKED_STATUS, replay.status)
            self.assertEqual(
                ("LOCAL_OBJECT_VERSION_REPLAY_OR_CONSUME_CONFLICT",), replay.reason_codes
            )
            self.assertFalse(never_reader.called)
            self.assertFalse(never_decryptor.called)

    def test_consumed_object_version_collision_across_a_different_bundle_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self._config(Path(temporary))
            first, *_rest = self._stage(config=config)
            self.assertTrue(first.staged)
            # The signed lineage/pin are deliberately new, but this adversarial
            # second bundle attempts to reuse exact immutable Object versions.
            # The manifest verifier cannot know local durable consumption; the
            # receiver's O_EXCL object-version records must stop it.
            second_route = self._route(generation="fi-ir-stage-alt-base-20260731")
            second, *_rest = self._stage(route=second_route, config=config)

            self.assertEqual(PHYSICAL_WAL_RECEIVER_BLOCKED_STATUS, second.status)
            self.assertEqual(
                ("LOCAL_OBJECT_VERSION_REPLAY_OR_CONSUME_CONFLICT",), second.reason_codes
            )

    def test_roots_and_children_require_exact_private_mode_and_euid_owner(self):
        with self.subTest("root mode"):
            with tempfile.TemporaryDirectory() as temporary:
                config = self._config(Path(temporary))
                os.chmod(config.receiver_root, 0o750)
                route = self._route()
                result, *_rest = self._stage(
                    route=route,
                    config=config,
                    reader=_Reader(route[2]),
                    decryptor=_Decryptor(route[3]),
                )
                self.assertEqual(("RECEIVER_ROOT_UNSAFE",), result.reason_codes)

        for mode in (0o750, 0o710):
            with self.subTest("child mode", mode=oct(mode)):
                with tempfile.TemporaryDirectory() as temporary:
                    config = self._config(Path(temporary))
                    child = config.receiver_root / "candidates"
                    child.mkdir(mode=0o700)
                    os.chmod(child, mode)
                    route = self._route()
                    result, *_rest = self._stage(
                        route=route,
                        config=config,
                        reader=_Reader(route[2]),
                        decryptor=_Decryptor(route[3]),
                    )
                    self.assertEqual(("LOCAL_DIRECTORY_UNSAFE",), result.reason_codes)

        with self.subTest("foreign owner"):
            with tempfile.TemporaryDirectory() as temporary:
                config = self._config(Path(temporary))
                route = self._route()
                # Some CI overlay filesystems do not permit chown even for the
                # namespace-root user.  Model a root owned by another service
                # identity at the ownership boundary instead of weakening it.
                with patch("core.physical_wal_receiver_staging.os.geteuid", return_value=1):
                    result, *_rest = self._stage(
                        route=route,
                        config=config,
                        reader=_Reader(route[2]),
                        decryptor=_Decryptor(route[3]),
                    )
                self.assertEqual(("RECEIVER_ROOT_UNSAFE",), result.reason_codes)

    def test_symlink_roots_and_candidate_paths_are_rejected(self):
        with self.subTest("root"):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                real_receiver = root / "receiver-real"
                real_receiver.mkdir(mode=0o700)
                receiver_link = root / "receiver-link"
                receiver_link.symlink_to(real_receiver, target_is_directory=True)
                state = root / "state"
                state.mkdir(mode=0o700)
                config = PhysicalWalReceiverStagingConfig(receiver_link, state)
                route = self._route()
                result, *_rest = self._stage(
                    route=route,
                    config=config,
                    reader=_Reader(route[2]),
                    decryptor=_Decryptor(route[3]),
                )
                self.assertEqual(("RECEIVER_ROOT_UNSAFE",), result.reason_codes)

        with self.subTest("completed candidate material path"):
            with tempfile.TemporaryDirectory() as temporary:
                config = self._config(Path(temporary))
                first, bundle, pin, _ciphertexts, _plaintexts, _reader, _decryptor = self._stage(
                    config=config
                )
                assert first.candidate_path is not None
                material = first.candidate_path / "material"
                material_real = first.candidate_path / "material-real"
                material.rename(material_real)
                material.symlink_to(material_real, target_is_directory=True)
                retry = stage_physical_wal_object_storage_bundle(
                    bundle=bundle,
                    pin=pin,
                    config=config,
                    exact_version_reader=_NeverRead(),
                    decryptor=_NeverDecrypt(),
                )
                self.assertEqual(PHYSICAL_WAL_RECEIVER_BLOCKED_STATUS, retry.status)
                self.assertEqual(("LOCAL_DIRECTORY_UNSAFE",), retry.reason_codes)

    def test_partial_bundle_and_missing_injected_adapter_are_blocked_before_io(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self._config(Path(temporary))
            route = self._route()
            reader = _Reader(route[2])
            decryptor = _Decryptor(route[3])
            partial = stage_physical_wal_object_storage_bundle(
                bundle=object(),
                pin=route[1],
                config=config,
                exact_version_reader=reader,
                decryptor=decryptor,
            )
            self.assertEqual(("BUNDLE_UNVERIFIED_OR_PARTIAL",), partial.reason_codes)
            self.assertEqual(0, reader.calls)
            self.assertEqual(0, decryptor.calls)
            no_adapter = stage_physical_wal_object_storage_bundle(
                bundle=route[0],
                pin=route[1],
                config=config,
                exact_version_reader=None,
                decryptor=None,
            )
            self.assertEqual(("EXACT_VERSION_READER_REQUIRED",), no_adapter.reason_codes)
            self.assertFalse((config.receiver_root / "candidates").exists())

    def test_module_has_no_default_remote_or_database_adapter_import(self):
        import core.physical_wal_receiver_staging as receiver_module

        tree = ast.parse(inspect.getsource(receiver_module))
        forbidden = {
            "boto3",
            "botocore",
            "docker",
            "http",
            "paramiko",
            "psycopg",
            "requests",
            "subprocess",
            "urllib",
        }
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        self.assertFalse(forbidden & imported)


if __name__ == "__main__":
    unittest.main()
