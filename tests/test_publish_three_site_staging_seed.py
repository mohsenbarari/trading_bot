from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts.publish_three_site_staging_seed import (
    SeedPublicationError,
    build_plan,
    execute,
)
from scripts.verify_three_site_staging_inventory import verify_signed_inventory
from tests.test_three_site_staging_signed_inventory import _inventory, _signed_documents


class _FakeS3:
    def __init__(self, *, versioning: bool = True):
        self.versioning = versioning
        self.objects = {}

    def get_bucket_versioning(self, *, Bucket):  # noqa: N803
        return {"Status": "Enabled" if self.versioning else "Suspended"}

    def put_object(self, *, Bucket, Key, Body, ContentLength, ContentType, Metadata):  # noqa: N803
        payload = Body.read()
        assert len(payload) == ContentLength
        self.objects[(Bucket, Key)] = (payload, dict(Metadata), "version-1")
        return {"VersionId": "version-1"}

    def head_object(self, *, Bucket, Key):  # noqa: N803
        payload, metadata, version = self.objects[(Bucket, Key)]
        return {"ContentLength": len(payload), "Metadata": metadata, "VersionId": version}

    def get_object(self, *, Bucket, Key, VersionId):  # noqa: N803
        payload, _metadata, version = self.objects[(Bucket, Key)]
        assert version == VersionId
        return {"Body": io.BytesIO(payload)}


def _fake_age(arguments, *, timeout=1800):  # noqa: ANN001, ARG001
    output = Path(arguments[arguments.index("--output") + 1])
    source = Path(arguments[-1])
    if "--encrypt" in arguments:
        output.write_bytes(b"AGE" + source.read_bytes())
    else:
        payload = source.read_bytes()
        if not payload.startswith(b"AGE"):
            raise AssertionError("invalid fake age payload")
        output.write_bytes(payload[3:])


class PublishThreeSiteStagingSeedTests(unittest.TestCase):
    def _fixture(self, root: Path, *, versioning: bool = True):
        repo = root / "repo"
        repo.mkdir()
        output = root / "evidence"
        artifacts = {}
        postgres = root / "postgres.custom"
        postgres.write_bytes(b"postgres-dump")
        postgres.chmod(0o600)
        artifacts["postgres"] = {
            "path": str(postgres),
            "bytes": postgres.stat().st_size,
            "sha256": hashlib.sha256(postgres.read_bytes()).hexdigest(),
        }
        for kind in ("uploads", "audit"):
            source = root / f"{kind}.txt"
            source.write_text(kind, encoding="utf-8")
            archive = root / f"{kind}.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                handle.add(source, arcname=f"{kind}.txt")
            archive.chmod(0o600)
            artifacts[kind] = {
                "path": str(archive),
                "bytes": archive.stat().st_size,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "safe_member_count": 1,
            }
        inventory = _inventory()
        now = datetime.now(timezone.utc)
        policy, approval = _signed_documents(inventory, now)
        inventory_result = verify_signed_inventory(
            inventory,
            approval=approval,
            signer_policy=policy,
            host_destructive=True,
            now=now,
        )
        backup = {
            "schema": "three-site-staging-source-backup-v2",
            "campaign_id": inventory["campaign_id"],
            "source_role": "webapp_fi",
            "source_release_sha": "b" * 40,
            "target_release_sha": inventory["release_sha"],
            "created_at": now.isoformat(),
            "source_postgres_system_id": "8000000000000000001",
            "source_alembic_revision": "c431d2e3f5a6",
            "source_freeze_evidence_sha256": "f" * 64,
            "redis_observation": {
                "dbsize": 2,
                "appendonly": True,
                "lastsave_unix": 1700000000,
                "restore": False,
            },
            "artifacts": artifacts,
            "restore_drill": {
                "status": "passed",
                "restored_alembic_revision": "c431d2e3f5a6",
                "scratch_postgres_system_id": "7000000000000000001",
                "database_fingerprint_sha256": hashlib.sha256(b"database").hexdigest(),
                "database_row_count": 1,
                "public_table_count": 1,
            },
            "redis_restore": False,
            "application_mutation": False,
        }
        credentials = root / "s3.json"
        credentials.write_text(
            json.dumps({"access_key": "access-key", "secret_key": "s" * 40}),
            encoding="utf-8",
        )
        credentials.chmod(0o600)
        recipient = root / "recipient.txt"
        recipient.write_text("age1testrecipient\n", encoding="utf-8")
        recipient.chmod(0o600)
        identity = root / "identity.txt"
        identity.write_text("AGE-SECRET-KEY-TEST\n", encoding="utf-8")
        identity.chmod(0o600)
        args = argparse.Namespace(
            source_role="webapp_fi",
            repo=repo,
            output_dir=output,
            credentials=credentials,
            recipient=recipient,
            identity=identity,
            confirm=None,
        )
        planned = build_plan(
            source_role="webapp_fi", backup=backup, inventory_result=inventory_result
        )
        args.confirm = planned["required_confirmation"]
        return args, inventory, inventory_result, backup, _FakeS3(versioning=versioning)

    def test_publish_requires_versioning_and_proves_decrypted_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            args, inventory, inventory_result, backup, client = self._fixture(Path(directory))
            with patch("scripts.publish_three_site_staging_seed._client", return_value=client), patch(
                "scripts.publish_three_site_staging_seed._run_age", side_effect=_fake_age
            ):
                result = execute(
                    args,
                    inventory=inventory,
                    inventory_result=inventory_result,
                    backup=backup,
                )
            self.assertEqual(result["status"], "published-and-readback-verified")
            self.assertEqual(result["object_count"], 3)
            manifest = json.loads((args.output_dir / "seed-manifest.json").read_text())
            self.assertEqual(len(manifest["objects"]), 3)
            self.assertTrue(all(item["version_id"] == "version-1" for item in manifest["objects"]))
            self.assertEqual((args.output_dir / "seed-manifest.json").stat().st_mode & 0o777, 0o600)

    def test_suspended_bucket_fails_before_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            args, inventory, inventory_result, backup, client = self._fixture(
                Path(directory), versioning=False
            )
            with patch("scripts.publish_three_site_staging_seed._client", return_value=client):
                with self.assertRaisesRegex(SeedPublicationError, "versioning"):
                    execute(
                        args,
                        inventory=inventory,
                        inventory_result=inventory_result,
                        backup=backup,
                    )
            self.assertFalse(client.objects)


if __name__ == "__main__":
    unittest.main()
