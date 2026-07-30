"""Focused protocol tests for the immutable WebApp standby snapshot transport."""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import importlib.util
import io
import json
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "manage_webapp_ir_snapshot.py"
SPEC = importlib.util.spec_from_file_location("manage_webapp_ir_snapshot", MODULE_PATH)
assert SPEC and SPEC.loader
snapshot = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = snapshot
SPEC.loader.exec_module(snapshot)


UTC = dt.timezone.utc
NOW = dt.datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
AGE_RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
RELEASE_SHA = "a" * 40
SNAPSHOT_ID = "20260729T120000Z-0123456789abcdef01234567"


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._data) - self._offset
        result = self._data[self._offset : self._offset + size]
        self._offset += len(result)
        return result


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, list[dict[str, Any]]] = {}
        self.put_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.head_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []
        self.version_list_calls: list[dict[str, Any]] = []
        self.delete_markers: dict[str, list[dict[str, Any]]] = {}
        self._sequence = 0

    def get_bucket_versioning(self, **kwargs: Any) -> dict[str, Any]:
        return {"Status": "Enabled"}

    def get_bucket_acl(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "Owner": {"ID": "owner"},
            "Grants": [{"Grantee": {"Type": "CanonicalUser", "ID": "owner"}, "Permission": "FULL_CONTROL"}],
        }

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self.head_calls.append(kwargs)
        if kwargs["Key"] not in self.objects:
            raise FileNotFoundError(kwargs["Key"])
        return {"VersionId": self.objects[kwargs["Key"]][-1]["version_id"]}

    def list_object_versions(self, **kwargs: Any) -> dict[str, Any]:
        self.version_list_calls.append(kwargs)
        prefix = kwargs["Prefix"]
        versions = [
            {"Key": key, "VersionId": item["version_id"], "IsLatest": index == len(entries) - 1}
            for key, entries in self.objects.items()
            if key.startswith(prefix)
            for index, item in enumerate(entries)
        ]
        delete_markers = [
            {"Key": key, "VersionId": item["version_id"], "IsLatest": index == len(entries) - 1}
            for key, entries in self.delete_markers.items()
            if key.startswith(prefix)
            for index, item in enumerate(entries)
        ]
        return {"Versions": versions, "DeleteMarkers": delete_markers, "IsTruncated": False}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.put_calls.append(kwargs)
        if kwargs.get("IfNoneMatch") != "*":
            raise AssertionError("immutable uploads must use IfNoneMatch: *")
        if kwargs["Key"] in self.objects or kwargs["Key"] in self.delete_markers:
            raise FileExistsError(kwargs["Key"])
        self._sequence += 1
        data = kwargs["Body"].read()
        version_id = f"version-{self._sequence}"
        self.objects.setdefault(kwargs["Key"], []).append(
            {
                "version_id": version_id,
                "data": data,
                "metadata": dict(kwargs["Metadata"]),
                "last_modified": NOW + dt.timedelta(seconds=self._sequence),
            }
        )
        return {"VersionId": version_id}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.get_calls.append(kwargs)
        versions = self.objects[kwargs["Key"]]
        requested = kwargs.get("VersionId")
        selected = versions[-1]
        if requested is not None:
            selected = next(item for item in versions if item["version_id"] == requested)
        return {
            "VersionId": selected["version_id"],
            "Metadata": dict(selected["metadata"]),
            "Body": FakeBody(selected["data"]),
        }

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self.list_calls.append(kwargs)
        prefix = kwargs["Prefix"]
        contents = [
            {"Key": key, "LastModified": versions[-1]["last_modified"]}
            for key, versions in self.objects.items()
            if key.startswith(prefix)
        ]
        return {"Contents": contents, "IsTruncated": False}


def fake_encrypt(_binary: str, _recipient: str, source: Path, output: Path) -> None:
    output.write_bytes(b"FAKE-AGE\x00" + source.read_bytes())
    output.chmod(0o600)


def fake_decrypt(_binary: str, _identity: Path, source: Path, output: Path) -> None:
    ciphertext = source.read_bytes()
    if not ciphertext.startswith(b"FAKE-AGE\x00"):
        raise snapshot.SnapshotTransportError("test ciphertext does not have the expected age envelope")
    output.write_bytes(ciphertext[len(b"FAKE-AGE\x00") :])
    output.chmod(0o600)


class SnapshotTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="snapshot-transport-test-")
        self.root = Path(self._temporary.name)
        self.root.chmod(0o700)
        self.workspace = self.root / "workspace"
        self.candidate_root = self.root / "candidates"
        self.identity = self.root / "age-identity.txt"
        self.identity.write_text("AGE-SECRET-KEY-1TEST\n", encoding="utf-8")
        self.identity.chmod(0o600)
        self.database_dump = self.root / "database.dump"
        self.database_dump.write_bytes(b"PGDMP\x00\x01custom-postgres-dump")
        self.database_dump.chmod(0o600)
        self.uploads_archive = self.root / "uploads.tar.gz"
        self._write_uploads_archive(self.uploads_archive)
        self.uploads_archive.chmod(0o600)
        signing_private = Ed25519PrivateKey.generate()
        self.signing_private_key = self.root / "webapp-fi-ed25519-private.key"
        self.signing_private_key.write_bytes(
            signing_private.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        self.signing_private_key.chmod(0o600)
        self.signing_public_key = signing_private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.publisher_config = snapshot.TransportConfig(
            endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
            region="ir-thr-at1",
            bucket="private-snapshots",
            prefix="campaigns/three-site",
            credentials_file=self.root / "credentials.json",
            age_binary="/usr/bin/age",
            age_recipient=AGE_RECIPIENT,
            age_identity_file=self.identity,
            workspace=self.workspace,
            maximum_database_bytes=1024 * 1024,
            maximum_uploads_bytes=1024 * 1024,
            maximum_audit_bytes=1024 * 1024,
            maximum_snapshot_age_seconds=30,
            minimum_free_bytes=0,
            local_artifact_retention="preserve",
            signing_source_site="webapp_fi",
            source_signing_private_key_file=self.signing_private_key,
            source_signing_public_key=None,
        )
        self.consumer_config = snapshot.dataclasses.replace(
            self.publisher_config,
            source_signing_private_key_file=None,
            source_signing_public_key=self.signing_public_key,
        )
        self.config = self.publisher_config
        self.client = FakeS3()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    @staticmethod
    def _write_uploads_archive(path: Path) -> None:
        SnapshotTransportTests._write_rooted_archive(path, root_name="uploads", member_name="example.txt")

    @staticmethod
    def _write_rooted_archive(path: Path, *, root_name: str, member_name: str) -> None:
        with tarfile.open(path, mode="w:gz") as archive:
            root = tarfile.TarInfo(root_name + "/")
            root.type = tarfile.DIRTYPE
            root.mode = 0o700
            archive.addfile(root)
            content = b"attached-upload"
            member = tarfile.TarInfo(root_name + "/" + member_name)
            member.size = len(content)
            member.mode = 0o600
            archive.addfile(member, io.BytesIO(content))

    def publish(
        self,
        *,
        source_db_snapshot_started_at: dt.datetime = NOW,
        source_capture_completed_at: dt.datetime = NOW,
        now: dt.datetime = NOW,
        snapshot_id: str = SNAPSHOT_ID,
        config: snapshot.TransportConfig | None = None,
    ) -> dict[str, Any]:
        return snapshot.publish_snapshot(
            self.client,
            config=config or self.publisher_config,
            database_dump=self.database_dump,
            uploads_archive=self.uploads_archive,
            audit_archive=None,
            source_site="webapp_fi",
            destination_site="webapp_ir",
            generation="fi-generation-1",
            release_sha=RELEASE_SHA,
            alembic_revision="f2c7d8e9a0b1",
            source_db_snapshot_started_at=snapshot.utc_iso(source_db_snapshot_started_at),
            source_capture_completed_at=snapshot.utc_iso(source_capture_completed_at),
            source_db_client_mode="short_lived_read_only",
            source_db_client_lifetime_seconds=30,
            source_volume_capture_mode="read_only_no_mutation",
            snapshot_id=snapshot_id,
            now=now,
            encryptor=fake_encrypt,
        )

    def consume(
        self,
        *,
        now: dt.datetime = NOW + dt.timedelta(seconds=10),
        config: snapshot.TransportConfig | None = None,
    ) -> dict[str, Any]:
        return snapshot.consume_snapshot(
            self.client,
            config=config or self.consumer_config,
            source_site="webapp_fi",
            destination_site="webapp_ir",
            candidate_root=self.candidate_root,
            now=now,
            decryptor=fake_decrypt,
        )

    def replace_manifest_plaintext(self, published: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
        entry = self.client.objects[published["manifest"]["object_key"]][-1]
        data = b"FAKE-AGE\x00" + snapshot.canonical_json_bytes(manifest) + b"\n"
        entry["data"] = data
        entry["metadata"] = snapshot.metadata_for_ciphertext(snapshot.sha256_bytes(data))

    def published_manifest_plaintext(self, published: Mapping[str, Any]) -> dict[str, Any]:
        data = self.client.objects[published["manifest"]["object_key"]][-1]["data"]
        self.assertTrue(data.startswith(b"FAKE-AGE\x00"))
        return json.loads(data[len(b"FAKE-AGE\x00") :].decode("utf-8"))

    def test_publish_is_three_immutable_private_versioned_objects_with_manifest_last(self) -> None:
        receipt = self.publish()

        self.assertEqual(3, len(self.client.put_calls))
        self.assertTrue(self.client.put_calls[-1]["Key"].endswith("/manifest.json.age"))
        self.assertEqual("published", receipt["status"])
        self.assertEqual("version-3", receipt["manifest"]["version_id"])
        self.assertEqual(
            {
                receipt["database"]["object_key"],
                receipt["uploads"]["object_key"],
                receipt["manifest"]["object_key"],
            },
            set(self.client.objects),
        )
        for call in self.client.put_calls:
            self.assertNotIn("ACL", call)
            self.assertNotIn("ServerSideEncryption", call)
            self.assertEqual("*", call["IfNoneMatch"])
            self.assertEqual("age-v1", call["Metadata"]["encryption"])
            self.assertIn("ciphertext-sha256", call["Metadata"])
        exact_readbacks = [call for call in self.client.get_calls if call.get("VersionId")]
        self.assertEqual({"version-1", "version-2", "version-3"}, {call["VersionId"] for call in exact_readbacks})
        self.assertFalse(hasattr(self.client, "delete_object"))
        manifest = self.published_manifest_plaintext(receipt)
        self.assertEqual("ed25519", manifest["source_signature"]["algorithm"])
        self.assertEqual(snapshot.sha256_bytes(self.signing_public_key), manifest["source_signature"]["key_id"])

    def test_publish_and_consume_require_distinct_bound_signing_roles(self) -> None:
        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "private signing key"):
            self.publish(config=self.consumer_config)
        published = self.publish()
        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "pinned source Ed25519 public key"):
            self.consume(config=self.publisher_config)
        self.assertIn(published["manifest"]["object_key"], self.client.objects)

    def test_consumer_rejects_unsigned_legacy_manifest_before_artifact_download(self) -> None:
        published = self.publish()
        manifest = self.published_manifest_plaintext(published)
        del manifest["source_signature"]
        self.replace_manifest_plaintext(published, manifest)
        before = len(self.client.get_calls)

        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "source_signature is required"):
            self.consume()
        artifact_reads = [
            call
            for call in self.client.get_calls[before:]
            if call.get("VersionId") in {published["database"]["version_id"], published["uploads"]["version_id"]}
        ]
        self.assertEqual([], artifact_reads)

    def test_consumer_rejects_wrong_pinned_source_key_before_artifact_download(self) -> None:
        published = self.publish()
        wrong_public_key = Ed25519PrivateKey.generate().public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        wrong_consumer = snapshot.dataclasses.replace(self.consumer_config, source_signing_public_key=wrong_public_key)
        before = len(self.client.get_calls)

        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "key_id does not match"):
            self.consume(config=wrong_consumer)
        artifact_reads = [
            call
            for call in self.client.get_calls[before:]
            if call.get("VersionId") in {published["database"]["version_id"], published["uploads"]["version_id"]}
        ]
        self.assertEqual([], artifact_reads)

    def test_consumer_rejects_signed_manifest_with_tampered_artifact_descriptor_before_download(self) -> None:
        published = self.publish()
        manifest = self.published_manifest_plaintext(published)
        manifest["database"]["ciphertext_bytes"] += 1
        self.replace_manifest_plaintext(published, manifest)
        before = len(self.client.get_calls)

        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "source_signature verification failed"):
            self.consume()
        artifact_reads = [
            call
            for call in self.client.get_calls[before:]
            if call.get("VersionId") in {published["database"]["version_id"], published["uploads"]["version_id"]}
        ]
        self.assertEqual([], artifact_reads)

    def test_publisher_rejects_non_private_or_misbundled_source_signing_key(self) -> None:
        self.signing_private_key.chmod(0o644)
        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "group/other"):
            self.publish()
        self.signing_private_key.chmod(0o600)
        wrong_source_config = snapshot.dataclasses.replace(self.publisher_config, signing_source_site="webapp_ir")
        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "source-bound"):
            self.publish(config=wrong_source_config)

    def test_immutable_upload_rejects_prior_version_or_delete_marker(self) -> None:
        key = "campaigns/three-site/snapshots/v1/webapp_fi/generation/snapshot/database.dump.age"
        self.client.objects[key] = [{"version_id": "old-version"}]
        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "prior versions"):
            snapshot.assert_object_absent(self.client, bucket=self.config.bucket, key=key)

        self.client.objects.clear()
        self.client.delete_markers[key] = [{"version_id": "delete-marker"}]
        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "delete marker"):
            snapshot.assert_object_absent(self.client, bucket=self.config.bucket, key=key)

    def test_immutable_upload_requires_provider_conditional_create_and_exact_returned_version(self) -> None:
        class UnsupportedConditionalPut(FakeS3):
            def put_object(self, **kwargs: Any) -> dict[str, Any]:
                raise TypeError("unknown parameter IfNoneMatch")

        unsupported = UnsupportedConditionalPut()
        self.assertEqual([], unsupported.put_calls)
        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "conditional immutable"):
            snapshot.publish_snapshot(
                unsupported,
                config=self.publisher_config,
                database_dump=self.database_dump,
                uploads_archive=self.uploads_archive,
                audit_archive=None,
                source_site="webapp_fi",
                destination_site="webapp_ir",
                generation="fi-generation-1",
                release_sha=RELEASE_SHA,
                alembic_revision="f2c7d8e9a0b1",
                source_db_snapshot_started_at=snapshot.utc_iso(NOW),
                source_capture_completed_at=snapshot.utc_iso(NOW),
                source_db_client_mode="short_lived_read_only",
                source_db_client_lifetime_seconds=30,
                source_volume_capture_mode="read_only_no_mutation",
                snapshot_id=SNAPSHOT_ID,
                now=NOW,
                encryptor=fake_encrypt,
            )

        class MisreportedVersion(FakeS3):
            def put_object(self, **kwargs: Any) -> dict[str, Any]:
                super().put_object(**kwargs)
                return {"VersionId": "not-the-created-version"}

        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "does not match the conditional-create"):
            snapshot.publish_snapshot(
                MisreportedVersion(),
                config=self.publisher_config,
                database_dump=self.database_dump,
                uploads_archive=self.uploads_archive,
                audit_archive=None,
                source_site="webapp_fi",
                destination_site="webapp_ir",
                generation="fi-generation-1",
                release_sha=RELEASE_SHA,
                alembic_revision="f2c7d8e9a0b1",
                source_db_snapshot_started_at=snapshot.utc_iso(NOW),
                source_capture_completed_at=snapshot.utc_iso(NOW),
                source_db_client_mode="short_lived_read_only",
                source_db_client_lifetime_seconds=30,
                source_volume_capture_mode="read_only_no_mutation",
                snapshot_id=SNAPSHOT_ID,
                now=NOW,
                encryptor=fake_encrypt,
            )

    def test_private_bucket_rejects_unknown_acl_grantee(self) -> None:
        class ForeignGrant(FakeS3):
            def get_bucket_acl(self, **kwargs: Any) -> dict[str, Any]:
                return {
                    "Owner": {"ID": "owner"},
                    "Grants": [
                        {"Grantee": {"Type": "CanonicalUser", "ID": "owner"}, "Permission": "FULL_CONTROL"},
                        {"Grantee": {"Type": "CanonicalUser", "ID": "foreign"}, "Permission": "READ"},
                    ],
                }

        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "outside its canonical owner"):
            snapshot.assert_private_versioned_bucket(ForeignGrant(), self.config.bucket)

    def test_consumer_never_reads_a_manifest_from_a_mutable_key(self) -> None:
        published = self.publish()
        manifest_key = published["manifest"]["object_key"]
        original = self.client.objects[manifest_key][-1]
        self.client.objects[manifest_key].append(
            {
                **original,
                "version_id": "attacker-version",
                "last_modified": original["last_modified"] + dt.timedelta(seconds=1),
            }
        )
        before = len(self.client.get_calls)

        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "exactly one version"):
            self.consume()
        self.assertEqual([], self.client.get_calls[before:])

    def test_downloaded_ciphertext_body_is_created_root_only(self) -> None:
        target = self.root / "ciphertext.age"
        digest, size = snapshot.write_response_body({"Body": FakeBody(b"ciphertext")}, target)

        self.assertEqual(snapshot.sha256_bytes(b"ciphertext"), digest)
        self.assertEqual(len(b"ciphertext"), size)
        self.assertEqual(0o600, stat.S_IMODE(target.stat().st_mode))

    def test_downloaded_ciphertext_never_exceeds_its_capacity_reservation(self) -> None:
        target = self.root / "too-large.age"

        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "capacity reservation"):
            snapshot.write_response_body(
                {"Body": FakeBody(b"ciphertext")}, target, maximum_bytes=len(b"ciphertext") - 1
            )

        self.assertFalse(target.exists())

    def test_decrypted_manifest_rejects_an_output_beyond_its_reservation(self) -> None:
        encrypted = self.root / "manifest.age"
        output = self.root / "manifest.json"
        encrypted.write_bytes(b"fixture")
        encrypted.chmod(0o600)

        def oversized_decrypt(_binary: str, _identity: Path, _source: Path, target: Path) -> None:
            target.write_bytes(b"x" * (snapshot.MAXIMUM_MANIFEST_PLAINTEXT_BYTES + 1))
            target.chmod(0o600)

        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "capacity reservation"):
            snapshot.decrypt_manifest_to_value(
                config=self.consumer_config,
                encrypted_manifest=encrypted,
                output_path=output,
                identity_file=self.identity,
                decryptor=oversized_decrypt,
            )

    def test_secure_directory_allows_non_writable_755_ancestors_but_requires_private_final(self) -> None:
        service_root = self.root / "srv"
        service_root.mkdir(mode=0o755)
        service_root.chmod(0o755)
        final_directory = service_root / "trading-bot" / "production-data"

        snapshot.ensure_root_only_directory(final_directory, field="workspace")
        self.assertEqual(0o700, stat.S_IMODE(final_directory.stat().st_mode))
        insecure_final = self.root / "insecure-final"
        insecure_final.mkdir(mode=0o755)
        insecure_final.chmod(0o755)
        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "must not be accessible"):
            snapshot.ensure_root_only_directory(insecure_final, field="workspace")
        writable_ancestor = self.root / "writable-ancestor"
        writable_ancestor.mkdir(mode=0o777)
        writable_ancestor.chmod(0o777)
        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "ancestors"):
            snapshot.ensure_root_only_directory(writable_ancestor / "child", field="workspace")

    def test_consume_stages_exact_versions_and_emits_bound_ready_receipt(self) -> None:
        published = self.publish()
        receipt = self.consume()

        self.assertEqual("gold-trade-snapshot-ready-v1", receipt["schema"])
        self.assertEqual("ready", receipt["status"])
        self.assertEqual("webapp_fi", receipt["source_site"])
        self.assertEqual("webapp_ir", receipt["destination_site"])
        self.assertEqual("fi-generation-1", receipt["source_generation"])
        self.assertEqual(RELEASE_SHA, receipt["release_sha"])
        self.assertEqual("f2c7d8e9a0b1", receipt["alembic_revision"])
        self.assertEqual(snapshot.utc_iso(NOW), receipt["source_db_snapshot_started_at"])
        self.assertEqual(snapshot.utc_iso(NOW), receipt["source_capture_completed_at"])
        self.assertEqual(
            {"client_mode": "short_lived_read_only", "client_lifetime_seconds": 30},
            receipt["source_database_capture"],
        )
        self.assertEqual({"mode": "read_only_no_mutation"}, receipt["source_volume_capture"])
        self.assertEqual(10, receipt["snapshot_age_seconds"])
        self.assertEqual(10, receipt["source_db_snapshot_age_seconds"])
        self.assertEqual(10, receipt["source_capture_age_seconds"])
        self.assertEqual(0, receipt["source_capture_duration_seconds"])
        self.assertEqual(0, receipt["publish_lag_seconds"])
        self.assertEqual(published["database"], receipt["database"])
        self.assertEqual(published["uploads"], receipt["uploads"])
        self.assertEqual(published["manifest"], receipt["manifest"])
        self.assertEqual(self.database_dump.read_bytes(), Path(receipt["database_dump_path"]).read_bytes())
        self.assertEqual(self.uploads_archive.read_bytes(), Path(receipt["uploads_archive_path"]).read_bytes())
        self.assertEqual(0o600, stat.S_IMODE(Path(receipt["database_dump_path"]).stat().st_mode))
        ready_file = Path(receipt["candidate_directory"]) / "snapshot-ready.json"
        self.assertTrue(ready_file.is_file())
        self.assertEqual(0o600, stat.S_IMODE(ready_file.stat().st_mode))
        self.assertEqual(
            snapshot.sha256_bytes(
                snapshot.canonical_json_bytes({key: value for key, value in receipt.items() if key != "receipt_sha256"})
            ),
            receipt["receipt_sha256"],
        )
        data_gets = [call for call in self.client.get_calls if call.get("VersionId") in {"version-1", "version-2"}]
        self.assertEqual({"version-1", "version-2"}, {call["VersionId"] for call in data_gets})
        self.assertTrue(all(call.get("VersionId") for call in self.client.get_calls))

    def test_consume_rejects_tampered_exact_object_before_stage(self) -> None:
        published = self.publish()
        db_key = published["database"]["object_key"]
        self.client.objects[db_key][-1]["data"] = b"tampered-age-ciphertext"

        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "ciphertext"):
            self.consume()
        self.assertFalse(
            (self.candidate_root / "webapp_fi" / "fi-generation-1" / SNAPSHOT_ID).exists()
        )

    def test_consume_rejects_stale_snapshot_and_never_creates_candidate(self) -> None:
        self.publish()

        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "freshness"):
            self.consume(now=NOW + dt.timedelta(seconds=31))
        self.assertFalse(self.candidate_root.exists())

    def test_rpo_rounds_fractional_seconds_up_instead_of_silently_accepting_them(self) -> None:
        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "freshness"):
            self.publish(now=NOW + dt.timedelta(seconds=30, microseconds=1))
        self.publish()
        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "freshness"):
            self.consume(now=NOW + dt.timedelta(seconds=30, microseconds=1))

    def test_publish_rechecks_rpo_immediately_before_manifest_commit(self) -> None:
        old_utc_now = snapshot.utc_now
        times = iter((NOW, NOW + dt.timedelta(seconds=30, microseconds=1)))
        snapshot.utc_now = lambda: next(times)
        try:
            with self.assertRaisesRegex(snapshot.SnapshotTransportError, "at manifest commit"):
                self.publish(now=None)
        finally:
            snapshot.utc_now = old_utc_now
        self.assertFalse(any(key.endswith("/manifest.json.age") for key in self.client.objects))

    def test_publish_rechecks_rpo_immediately_before_manifest_upload(self) -> None:
        old_utc_now = snapshot.utc_now
        times = iter((NOW, NOW, NOW + dt.timedelta(seconds=30, microseconds=1)))
        snapshot.utc_now = lambda: next(times)
        try:
            with self.assertRaisesRegex(snapshot.SnapshotTransportError, "at manifest upload"):
                self.publish(now=None)
        finally:
            snapshot.utc_now = old_utc_now
        self.assertFalse(any(key.endswith("/manifest.json.age") for key in self.client.objects))

    def test_publish_rechecks_rpo_at_the_manifest_conditional_put_boundary(self) -> None:
        old_utc_now = snapshot.utc_now
        times = iter((NOW, NOW, NOW, NOW + dt.timedelta(seconds=30, microseconds=1)))
        snapshot.utc_now = lambda: next(times)
        try:
            with self.assertRaisesRegex(snapshot.SnapshotTransportError, "at manifest PUT"):
                self.publish(now=None)
        finally:
            snapshot.utc_now = old_utc_now
        self.assertFalse(any(key.endswith("/manifest.json.age") for key in self.client.objects))

    def test_consume_rechecks_rpo_immediately_before_ready_rename(self) -> None:
        self.publish()
        old_utc_now = snapshot.utc_now
        times = iter((NOW + dt.timedelta(seconds=1), NOW + dt.timedelta(seconds=30, microseconds=1)))
        snapshot.utc_now = lambda: next(times)
        try:
            with self.assertRaisesRegex(snapshot.SnapshotTransportError, "before readiness commit"):
                self.consume(now=None)
        finally:
            snapshot.utc_now = old_utc_now
        self.assertFalse((self.candidate_root / "webapp_fi" / "fi-generation-1" / SNAPSHOT_ID).exists())

    def test_freshness_is_measured_from_db_snapshot_start_not_capture_completion(self) -> None:
        self.publish(
            source_db_snapshot_started_at=NOW - dt.timedelta(seconds=20),
            source_capture_completed_at=NOW,
        )

        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "freshness"):
            self.consume(now=NOW + dt.timedelta(seconds=15))

    def test_publisher_rejects_source_snapshot_that_is_already_outside_rpo_bound(self) -> None:
        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "source snapshot"):
            self.publish(
                source_db_snapshot_started_at=NOW - dt.timedelta(seconds=31),
                source_capture_completed_at=NOW,
            )

    def test_publish_capacity_failure_precedes_any_object_write(self) -> None:
        with mock.patch.object(
            snapshot,
            "require_capacity",
            side_effect=snapshot.SnapshotCapacityError("fixture capacity failure"),
        ):
            with self.assertRaisesRegex(snapshot.SnapshotTransportError, "fixture capacity failure"):
                self.publish()
        self.assertEqual([], self.client.put_calls)

    def test_consume_capacity_failure_precedes_manifest_and_artifact_download(self) -> None:
        self.publish()
        get_calls_before = len(self.client.get_calls)
        with mock.patch.object(
            snapshot,
            "require_capacity",
            side_effect=snapshot.SnapshotCapacityError("fixture capacity failure"),
        ):
            with self.assertRaisesRegex(snapshot.SnapshotTransportError, "fixture capacity failure"):
                self.consume()
        self.assertEqual(get_calls_before, len(self.client.get_calls))
        self.assertFalse(self.candidate_root.exists())

    def test_publisher_rejects_capture_completion_before_db_snapshot_start(self) -> None:
        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "precedes"):
            self.publish(
                source_db_snapshot_started_at=NOW,
                source_capture_completed_at=NOW - dt.timedelta(seconds=1),
            )

    def test_consume_is_idempotent_for_the_latest_already_staged_candidate(self) -> None:
        self.publish()
        first = self.consume()
        exact_data_reads_before = len(
            [call for call in self.client.get_calls if call.get("VersionId") in {"version-1", "version-2"}]
        )
        second = self.consume()

        self.assertEqual(first, second)
        exact_data_reads_after = len(
            [call for call in self.client.get_calls if call.get("VersionId") in {"version-1", "version-2"}]
        )
        self.assertEqual(exact_data_reads_before, exact_data_reads_after)

    def test_legacy_ready_receipt_remains_a_valid_preserved_candidate(self) -> None:
        first_id = "20260729T120001Z-111111111111111111111111"
        second_id = "20260729T120002Z-222222222222222222222222"
        self.publish(snapshot_id=first_id)
        ready = self.consume()
        path = Path(ready["candidate_directory"]) / "snapshot-ready.json"
        legacy = json.loads(path.read_text(encoding="utf-8"))
        legacy.pop("capacity_preflight")
        legacy.pop("local_artifact_retention")
        legacy["receipt_sha256"] = snapshot.sha256_bytes(
            snapshot.canonical_json_bytes({key: value for key, value in legacy.items() if key != "receipt_sha256"})
        )
        path.unlink()
        snapshot.atomic_write_json(path, legacy)
        legacy_ready = dict(ready)
        legacy_ready["receipt_sha256"] = legacy["receipt_sha256"]
        self.write_restore_receipt(legacy_ready, active_pointer_state="inactive")

        self.publish(snapshot_id=second_id)
        staged = self.consume()

        self.assertEqual(second_id, staged["snapshot_id"])
        self.assertTrue((Path(ready["candidate_directory"]) / "database.dump").is_file())
        self.assertNotIn("capacity_preflight", snapshot.load_candidate_ready_receipt(Path(ready["candidate_directory"])))

    def write_restore_receipt(self, ready: dict[str, Any], *, active_pointer_state: str) -> None:
        payload: dict[str, Any] = {
            "schema": "gold-trade-snapshot-restore-receipt-v1",
            "status": "restored_verified",
            "source_site": ready["source_site"],
            "destination_site": ready["destination_site"],
            "source_generation": ready["source_generation"],
            "snapshot_id": ready["snapshot_id"],
            "release_sha": ready["release_sha"],
            "alembic_revision": ready["alembic_revision"],
            "source_db_snapshot_started_at": ready["source_db_snapshot_started_at"],
            "source_capture_completed_at": ready["source_capture_completed_at"],
            "published_at": ready["published_at"],
            "ready_at": ready["ready_at"],
            "ready_receipt_sha256": ready["receipt_sha256"],
            "active_pointer_state": active_pointer_state,
        }
        payload["receipt_sha256"] = snapshot.sha256_bytes(snapshot.canonical_json_bytes(payload))
        snapshot.atomic_write_json(Path(ready["candidate_directory"]) / "snapshot-restore.json", payload)

    def test_new_stage_blocks_until_older_candidate_has_verified_restore_marker(self) -> None:
        self.publish(snapshot_id="20260729T120001Z-111111111111111111111111")
        self.consume()
        self.publish(snapshot_id="20260729T120002Z-222222222222222222222222")

        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "awaits verified restore"):
            self.consume()

    def test_retention_preserves_old_inactive_restored_artifacts_by_default(self) -> None:
        first_id = "20260729T120001Z-111111111111111111111111"
        second_id = "20260729T120002Z-222222222222222222222222"
        third_id = "20260729T120003Z-333333333333333333333333"
        self.publish(snapshot_id=first_id)
        first = self.consume()
        self.write_restore_receipt(first, active_pointer_state="inactive")
        self.publish(snapshot_id=second_id)
        second = self.consume()
        self.write_restore_receipt(second, active_pointer_state="inactive")
        self.publish(snapshot_id=third_id)
        third = self.consume()

        first_candidate = Path(first["candidate_directory"])
        second_candidate = Path(second["candidate_directory"])
        self.assertTrue((first_candidate / "database.dump").is_file())
        self.assertTrue((first_candidate / "uploads.tar.gz").is_file())
        self.assertTrue((first_candidate / "snapshot-ready.json").is_file())
        self.assertTrue((first_candidate / "snapshot-restore.json").is_file())
        self.assertTrue((second_candidate / "database.dump").is_file())
        self.assertTrue(Path(third["database_dump_path"]).is_file())

    def test_retention_never_prunes_an_active_restored_candidate(self) -> None:
        first_id = "20260729T120001Z-111111111111111111111111"
        second_id = "20260729T120002Z-222222222222222222222222"
        self.publish(snapshot_id=first_id)
        first = self.consume()
        self.write_restore_receipt(first, active_pointer_state="active")
        self.publish(snapshot_id=second_id)
        self.consume()

        self.assertTrue((Path(first["candidate_directory"]) / "database.dump").is_file())

    def test_optional_audit_archive_is_bound_and_staged_when_present(self) -> None:
        audit = self.root / "audit.tar.gz"
        self._write_rooted_archive(audit, root_name="audit_trail", member_name="entry.json")
        audit.chmod(0o600)
        published = snapshot.publish_snapshot(
            self.client,
            config=self.config,
            database_dump=self.database_dump,
            uploads_archive=self.uploads_archive,
            audit_archive=audit,
            source_site="webapp_fi",
            destination_site="webapp_ir",
            generation="fi-generation-1",
            release_sha=RELEASE_SHA,
            alembic_revision="f2c7d8e9a0b1",
            source_db_snapshot_started_at=snapshot.utc_iso(NOW),
            source_capture_completed_at=snapshot.utc_iso(NOW),
            source_db_client_mode="short_lived_read_only",
            source_db_client_lifetime_seconds=30,
            source_volume_capture_mode="read_only_no_mutation",
            snapshot_id=SNAPSHOT_ID,
            now=NOW,
            encryptor=fake_encrypt,
        )

        self.assertEqual(4, len(self.client.put_calls))
        self.assertTrue(self.client.put_calls[-1]["Key"].endswith("/manifest.json.age"))
        self.assertIn("audit", published)
        receipt = self.consume()
        self.assertEqual(published["audit"], receipt["audit"])
        self.assertEqual(audit.read_bytes(), Path(receipt["audit_archive_path"]).read_bytes())

    def test_same_protocol_supports_controlled_reverse_ir_to_fi_direction(self) -> None:
        reverse_publisher_config = snapshot.dataclasses.replace(
            self.publisher_config,
            prefix="campaigns/three-site-ir-to-fi",
            signing_source_site="webapp_ir",
        )
        reverse_consumer_config = snapshot.dataclasses.replace(
            self.consumer_config,
            prefix="campaigns/three-site-ir-to-fi",
            signing_source_site="webapp_ir",
        )
        reverse_id = "20260729T120010Z-fedcba9876543210fedcba98"
        published = snapshot.publish_snapshot(
            self.client,
            config=reverse_publisher_config,
            database_dump=self.database_dump,
            uploads_archive=self.uploads_archive,
            audit_archive=None,
            source_site="webapp_ir",
            destination_site="webapp_fi",
            generation="ir-final-generation-1",
            release_sha=RELEASE_SHA,
            alembic_revision="f2c7d8e9a0b1",
            source_db_snapshot_started_at=snapshot.utc_iso(NOW),
            source_capture_completed_at=snapshot.utc_iso(NOW),
            source_db_client_mode="short_lived_read_only",
            source_db_client_lifetime_seconds=30,
            source_volume_capture_mode="read_only_no_mutation",
            snapshot_id=reverse_id,
            now=NOW,
            encryptor=fake_encrypt,
        )
        receipt = snapshot.consume_snapshot(
            self.client,
            config=reverse_consumer_config,
            source_site="webapp_ir",
            destination_site="webapp_fi",
            candidate_root=self.candidate_root,
            now=NOW + dt.timedelta(seconds=10),
            decryptor=fake_decrypt,
        )

        self.assertEqual("webapp_ir", receipt["source_site"])
        self.assertEqual("webapp_fi", receipt["destination_site"])
        self.assertEqual("ir-final-generation-1", receipt["source_generation"])
        self.assertTrue(published["manifest"]["object_key"].startswith("campaigns/three-site-ir-to-fi/"))

    def test_source_capture_contract_rejects_non_read_only_or_long_lived_clients(self) -> None:
        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "short_lived_read_only"):
            snapshot.validate_source_database_capture("writer", 10)
        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "short-lived"):
            snapshot.validate_source_database_capture("short_lived_read_only", 301)
        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "read_only_no_mutation"):
            snapshot.validate_source_volume_capture("mounted_rw")

    def test_workspace_lock_rejects_overlapping_timer_cycle(self) -> None:
        snapshot.ensure_root_only_directory(self.workspace, field="workspace")
        with snapshot.exclusive_workspace_lock(self.workspace, name="publish-webapp_fi-webapp_ir"):
            with self.assertRaisesRegex(snapshot.SnapshotTransportError, "already running"):
                with snapshot.exclusive_workspace_lock(self.workspace, name="publish-webapp_fi-webapp_ir"):
                    pass

    def test_workspace_capacity_admission_is_locked_before_temporary_allocation(self) -> None:
        order: list[str] = []

        @contextlib.contextmanager
        def fake_lock(_workspace: Path, *, name: str):
            self.assertEqual("fixture", name)
            order.append("lock")
            yield

        @contextlib.contextmanager
        def fake_workspace(_config: snapshot.TransportConfig):
            order.append("workspace")
            yield "fixture-workspace"

        capacity = {"label": "fixture"}
        with (
            mock.patch.object(snapshot, "exclusive_workspace_lock", fake_lock),
            mock.patch.object(snapshot, "capacity_preflight", side_effect=lambda *_args, **_kwargs: order.append("capacity") or capacity),
            mock.patch.object(snapshot, "_workspace_context", fake_workspace),
        ):
            with snapshot.locked_workspace_capacity_context(
                self.config,
                lock_name="fixture",
                required_new_bytes=1,
                label="fixture",
            ) as (workspace, admitted):
                order.append("body")

        self.assertEqual("fixture-workspace", workspace)
        self.assertIs(capacity, admitted)
        self.assertEqual(["lock", "capacity", "workspace", "body"], order)

    def test_age_backend_encrypts_for_recipient_and_decrypts_only_with_identity(self) -> None:
        identity = self.root / "real-age-identity.txt"
        generated = subprocess.run(
            ["/usr/bin/age-keygen", "-o", str(identity)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        recipient = next(
            line[line.index("age1") :].strip()
            for line in (generated.stdout + generated.stderr).splitlines()
            if "age1" in line
        )
        encrypted = self.root / "real.age"
        decrypted = self.root / "real.dump"

        snapshot.run_age_encrypt("/usr/bin/age", recipient, self.database_dump, encrypted)
        snapshot.run_age_decrypt("/usr/bin/age", identity, encrypted, decrypted)

        self.assertNotEqual(self.database_dump.read_bytes(), encrypted.read_bytes())
        self.assertEqual(self.database_dump.read_bytes(), decrypted.read_bytes())
        self.assertEqual(0o600, stat.S_IMODE(encrypted.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(decrypted.stat().st_mode))

    def test_incomplete_artifacts_without_commit_manifest_are_not_consumable(self) -> None:
        key = "campaigns/three-site/snapshots/v1/webapp_fi/fi-generation-1/20260729T120000Z-0123456789abcdef01234567/database.dump.age"
        self.client.objects[key] = [
            {
                "version_id": "version-1",
                "data": b"FAKE-AGE\x00PGDMPpartial",
                "metadata": snapshot.metadata_for_ciphertext(snapshot.sha256_bytes(b"FAKE-AGE\x00PGDMPpartial")),
                "last_modified": NOW,
            }
        ]

        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "no committed snapshot manifest"):
            self.consume()
        self.assertFalse(self.candidate_root.exists())

    def test_archive_must_be_gzip_tar_rooted_at_uploads(self) -> None:
        invalid = self.root / "invalid.tar.gz"
        with tarfile.open(invalid, mode="w:gz") as archive:
            member = tarfile.TarInfo("outside.txt")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
        invalid.chmod(0o600)

        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "rooted at uploads"):
            snapshot.validate_uploads_archive(invalid, 1024 * 1024)

    def test_config_requires_root_only_mode_and_arvan_endpoint(self) -> None:
        config_path = self.root / "transport.json"
        config_path.write_text(
            """{"schema":"gold-trade-snapshot-transport-v1","endpoint":"http://example.invalid","region":"ir-thr-at1","bucket":"private-snapshots","prefix":"campaigns/three-site","credentials_file":"/root/credentials.json"}""",
            encoding="utf-8",
        )
        config_path.chmod(0o644)

        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "group/other"):
            snapshot.load_transport_config(config_path)
        config_path.chmod(0o600)
        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "Arvan S3"):
            snapshot.load_transport_config(config_path)

    def test_config_pins_exactly_one_source_signing_role(self) -> None:
        config_path = self.root / "transport-signing.json"
        public_key = base64.b64encode(self.signing_public_key).decode("ascii")
        config_path.write_text(
            json.dumps(
                {
                    "schema": "gold-trade-snapshot-transport-v1",
                    "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
                    "region": "ir-thr-at1",
                    "bucket": "private-snapshots",
                    "prefix": "campaigns/three-site",
                    "credentials_file": "/root/credentials.json",
                    "minimum_free_bytes": 0,
                    "signing_source_site": "webapp_fi",
                    "source_signing_public_key_base64": public_key,
                }
            ),
            encoding="utf-8",
        )
        config_path.chmod(0o600)
        loaded = snapshot.load_transport_config(config_path)
        self.assertEqual("webapp_fi", loaded.signing_source_site)
        self.assertEqual(self.signing_public_key, loaded.source_signing_public_key)
        self.assertIsNone(loaded.source_signing_private_key_file)

        config_path.write_text(
            json.dumps(
                {
                    "schema": "gold-trade-snapshot-transport-v1",
                    "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
                    "region": "ir-thr-at1",
                    "bucket": "private-snapshots",
                    "prefix": "campaigns/three-site",
                    "credentials_file": "/root/credentials.json",
                    "minimum_free_bytes": 0,
                    "signing_source_site": "webapp_fi",
                    "source_signing_private_key_file": "/root/webapp-fi-private.key",
                    "source_signing_public_key_base64": public_key,
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "must not contain both"):
            snapshot.load_transport_config(config_path)


if __name__ == "__main__":
    unittest.main()
