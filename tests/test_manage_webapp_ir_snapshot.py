"""Focused protocol tests for the immutable WebApp standby snapshot transport."""

from __future__ import annotations

import datetime as dt
import importlib.util
import io
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Any


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
        self._sequence = 0

    def get_bucket_versioning(self, **kwargs: Any) -> dict[str, Any]:
        return {"Status": "Enabled"}

    def get_bucket_acl(self, **kwargs: Any) -> dict[str, Any]:
        return {"Grants": [{"Grantee": {"ID": "owner"}, "Permission": "FULL_CONTROL"}]}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self.head_calls.append(kwargs)
        if kwargs["Key"] not in self.objects:
            raise FileNotFoundError(kwargs["Key"])
        return {"VersionId": self.objects[kwargs["Key"]][-1]["version_id"]}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.put_calls.append(kwargs)
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
        self.config = snapshot.TransportConfig(
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
        )
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

    def publish(self) -> dict[str, Any]:
        return snapshot.publish_snapshot(
            self.client,
            config=self.config,
            database_dump=self.database_dump,
            uploads_archive=self.uploads_archive,
            audit_archive=None,
            source_site="webapp_fi",
            destination_site="webapp_ir",
            generation="fi-generation-1",
            release_sha=RELEASE_SHA,
            alembic_revision="f2c7d8e9a0b1",
            source_db_client_mode="short_lived_read_only",
            source_db_client_lifetime_seconds=30,
            source_volume_capture_mode="read_only_no_mutation",
            snapshot_id=SNAPSHOT_ID,
            now=NOW,
            encryptor=fake_encrypt,
        )

    def consume(self, *, now: dt.datetime = NOW + dt.timedelta(seconds=10)) -> dict[str, Any]:
        return snapshot.consume_snapshot(
            self.client,
            config=self.config,
            source_site="webapp_fi",
            destination_site="webapp_ir",
            candidate_root=self.candidate_root,
            now=now,
            decryptor=fake_decrypt,
        )

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
            self.assertEqual("age-v1", call["Metadata"]["encryption"])
            self.assertIn("ciphertext-sha256", call["Metadata"])
        exact_readbacks = [call for call in self.client.get_calls if call.get("VersionId")]
        self.assertEqual({"version-1", "version-2", "version-3"}, {call["VersionId"] for call in exact_readbacks})
        self.assertFalse(hasattr(self.client, "delete_object"))

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
        self.assertEqual(
            {"client_mode": "short_lived_read_only", "client_lifetime_seconds": 30},
            receipt["source_database_capture"],
        )
        self.assertEqual({"mode": "read_only_no_mutation"}, receipt["source_volume_capture"])
        self.assertEqual(10, receipt["snapshot_age_seconds"])
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

    def test_consume_refuses_to_overwrite_existing_candidate(self) -> None:
        self.publish()
        self.consume()

        with self.assertRaisesRegex(snapshot.SnapshotTransportError, "overwrite an existing candidate"):
            self.consume()

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
        reverse_config = snapshot.dataclasses.replace(self.config, prefix="campaigns/three-site-ir-to-fi")
        reverse_id = "20260729T120010Z-fedcba9876543210fedcba98"
        published = snapshot.publish_snapshot(
            self.client,
            config=reverse_config,
            database_dump=self.database_dump,
            uploads_archive=self.uploads_archive,
            audit_archive=None,
            source_site="webapp_ir",
            destination_site="webapp_fi",
            generation="ir-final-generation-1",
            release_sha=RELEASE_SHA,
            alembic_revision="f2c7d8e9a0b1",
            source_db_client_mode="short_lived_read_only",
            source_db_client_lifetime_seconds=30,
            source_volume_capture_mode="read_only_no_mutation",
            snapshot_id=reverse_id,
            now=NOW,
            encryptor=fake_encrypt,
        )
        receipt = snapshot.consume_snapshot(
            self.client,
            config=reverse_config,
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


if __name__ == "__main__":
    unittest.main()
