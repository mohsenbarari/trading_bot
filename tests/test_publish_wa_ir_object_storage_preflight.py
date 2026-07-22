from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts.publish_wa_ir_object_storage_preflight import (
    PublicationError,
    build_role_materials,
    confirmation_phrase,
    execute,
)


class _FakeS3:
    def __init__(self, *, versioning: bool = True):
        self.versioning = versioning
        self.objects: dict[tuple[str, str], tuple[bytes, dict[str, str], str]] = {}

    def get_bucket_versioning(self, *, Bucket):  # noqa: N803, ARG002
        return {"Status": "Enabled" if self.versioning else "Suspended"}

    def get_bucket_acl(self, *, Bucket):  # noqa: N803, ARG002
        return {"Grants": [{"Grantee": {"Type": "CanonicalUser"}, "Permission": "FULL_CONTROL"}]}

    def put_object(self, *, Bucket, Key, Body, ContentLength, ContentType, Metadata):  # noqa: N803, ARG002
        payload = Body.read()
        assert len(payload) == ContentLength
        self.objects[(Bucket, Key)] = (payload, dict(Metadata), "version-1")
        return {"VersionId": "version-1"}

    def head_object(self, *, Bucket, Key):  # noqa: N803
        payload, metadata, version = self.objects[(Bucket, Key)]
        return {"ContentLength": len(payload), "Metadata": metadata, "VersionId": version}

    def get_object(self, *, Bucket, Key, VersionId):  # noqa: N803
        payload, _metadata, version = self.objects[(Bucket, Key)]
        assert VersionId == version
        return {"Body": io.BytesIO(payload)}

    def generate_presigned_url(self, operation, *, Params, ExpiresIn):  # noqa: N803
        return (
            "https://s3.ir-thr-at1.arvanstorage.ir/"
            f"{Params['Bucket']}/{Params['Key']}?operation={operation}&ttl={ExpiresIn}"
        )


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _fake_encrypt(source: Path, output: Path, recipient: str):  # noqa: ANN001, ARG001
    output.write_bytes(b"AGE" + source.read_bytes())
    output.chmod(0o600)
    payload = output.read_bytes()
    return hashlib.sha256(payload).hexdigest(), len(payload)


class PublishWaIrObjectStoragePreflightTests(unittest.TestCase):
    def _fixture(self, root: Path):
        repo = root / "repo"
        repo.mkdir(mode=0o700)
        (repo / "scripts").mkdir()
        agent = repo / "scripts/wa_ir_object_storage_preflight_agent.py"
        agent.write_text("print('agent')\n", encoding="utf-8")
        _git(repo, "init")
        _git(repo, "add", ".")
        _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial")
        release_sha = _git(repo, "rev-parse", "HEAD")

        materials = root / "materials"
        (materials / "roles").mkdir(parents=True)
        (materials / "secrets").mkdir(parents=True)
        for relative, payload, mode in (
            ("planned-inventory.json", "{}\n", 0o600),
            ("planned-inventory-approval.json", "{}\n", 0o600),
            ("inventory-signers.json", "{}\n", 0o600),
            ("roles/webapp-ir.compose.yml", "services: {}\n", 0o640),
            ("roles/webapp-ir.env", "ROLE=webapp_ir\n", 0o600),
            ("secrets/staging-dr-ca.crt", "ca\n", 0o644),
            ("secrets/webapp-ir-dr.crt", "cert\n", 0o644),
            ("secrets/webapp-ir-dr.key", "key\n", 0o600),
            ("secrets/staging-dr-blob-s3.json", "{}\n", 0o600),
            ("secrets/staging-dr-blob-keyring.json", "{}\n", 0o600),
        ):
            path = materials / relative
            path.write_text(payload, encoding="utf-8")
            path.chmod(mode)

        recipient = root / "recipient.txt"
        recipient.write_text("age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\n", encoding="utf-8")
        recipient.chmod(0o600)
        output = root / "evidence"
        args = argparse.Namespace(
            repo=repo,
            release_sha=release_sha,
            secure_materials_dir=materials,
            credentials=root / "unused.env",
            recipient=recipient,
            bucket="production-sync-coin",
            prefix="staging/test/wa-ir-preflight",
            output_dir=output,
            remote_secure_materials_dir=Path(f"/root/secure-envs/trading-bot/three-site-staging-{release_sha[:8]}"),
            remote_age_identity=Path("/root/secure-envs/trading-bot/wa-ir-object-storage-age-identity.txt"),
            url_ttl_seconds=900,
            apply=True,
            confirm=confirmation_phrase(release_sha, "production-sync-coin", "staging/test/wa-ir-preflight/"),
        )
        return args

    def test_role_material_archive_has_only_the_fixed_allowlist(self):
        with tempfile.TemporaryDirectory() as raw:
            args = self._fixture(Path(raw))
            archive = Path(raw) / "role-materials.tar"
            build_role_materials(args.secure_materials_dir, archive)
            with tarfile.open(archive) as handle:
                self.assertEqual(
                    [member.name for member in handle.getmembers()],
                    [
                        "planned-inventory.json",
                        "planned-inventory-approval.json",
                        "inventory-signers.json",
                        "roles/webapp-ir.compose.yml",
                        "roles/webapp-ir.env",
                        "secrets/staging-dr-ca.crt",
                        "secrets/webapp-ir-dr.crt",
                        "secrets/webapp-ir-dr.key",
                        "secrets/staging-dr-blob-s3.json",
                        "secrets/staging-dr-blob-keyring.json",
                    ],
                )

    def test_publication_is_versioned_private_and_keeps_urls_ephemeral(self):
        with tempfile.TemporaryDirectory() as raw:
            args = self._fixture(Path(raw))
            fake = _FakeS3()
            with patch(
                "scripts.publish_wa_ir_object_storage_preflight.encrypt",
                side_effect=_fake_encrypt,
            ):
                result = execute(args, client=fake)
            self.assertEqual(result["status"], "published-and-readback-verified")
            durable = json.loads((args.output_dir / "publication-evidence.json").read_text())
            ephemeral = json.loads((args.output_dir / "ephemeral-bootstrap.json").read_text())
            self.assertFalse(durable["presigned_urls_persisted"])
            self.assertFalse(durable["ssh_payload_transfer"])
            self.assertNotIn('"url":', json.dumps(durable))
            self.assertIn("X-Amz", json.dumps(ephemeral).replace("operation", "X-Amz"))
            self.assertEqual((args.output_dir / "ephemeral-bootstrap.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(fake.objects), 4)

    def test_suspended_bucket_fails_before_upload(self):
        with tempfile.TemporaryDirectory() as raw:
            args = self._fixture(Path(raw))
            with self.assertRaisesRegex(PublicationError, "versioned bucket"):
                execute(args, client=_FakeS3(versioning=False))


if __name__ == "__main__":
    unittest.main()
