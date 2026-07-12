import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from scripts import staging_object_storage_release as release


class StagingObjectStorageReleaseTests(unittest.TestCase):
    def make_artifacts(self) -> tuple[tempfile.TemporaryDirectory, dict[str, Path]]:
        tmpdir = tempfile.TemporaryDirectory()
        root = Path(tmpdir.name)
        artifacts = {
            "project_payload": root / "project.tar.gz",
            "frontend_dist": root / "frontend.tar.gz",
            "docker_images": root / "images.tar",
        }
        for name, path in artifacts.items():
            path.write_bytes(f"{name} bytes".encode("utf-8"))
        return tmpdir, artifacts

    def upload_args(self, artifacts: dict[str, Path], manifest_out: Path) -> list[str]:
        args = [
            "upload",
            "--bucket",
            "staging-bucket",
            "--prefix",
            "staging/deploy-bridge",
            "--release-sha",
            "abc123",
            "--manifest-out",
            str(manifest_out),
            "--publish-channel",
            "iran-staging",
            "--project-exclude",
            ".env",
            "--project-exclude",
            "uploads",
            "--receiver-protect",
            ".env",
            "--receiver-protect",
            "uploads",
        ]
        for name, path in artifacts.items():
            args.extend(["--artifact", f"{name}={path}"])
        return args

    def test_build_manifest_records_multiple_artifacts_and_transfer_contract(self):
        tmpdir, artifacts = self.make_artifacts()
        self.addCleanup(tmpdir.cleanup)
        manifest_out = Path(tmpdir.name) / "manifest.json"
        args = release.parse_args(self.upload_args(artifacts, manifest_out))
        config = release.validate_config(args, require_credentials=False)

        manifest = release.build_manifest(args, config, release.parse_artifact_specs(args.artifact))

        self.assertEqual(manifest["schema"], release.SCHEMA)
        self.assertEqual(manifest["manifest"]["key"], "staging/deploy-bridge/releases/abc123/manifest.json")
        self.assertEqual(
            manifest["artifacts"]["project_payload"]["key"],
            "staging/deploy-bridge/releases/abc123/project.tar.gz",
        )
        self.assertEqual(manifest["transfer_contract"]["project_payload_excludes"], [".env", "uploads"])
        self.assertEqual(manifest["transfer_contract"]["receiver_protected_paths"], [".env", "uploads"])
        self.assertIn("docker_images", manifest["artifacts"])

        pointer = release.build_channel_pointer(manifest, config, "iran-staging")
        self.assertEqual(pointer["schema"], release.CHANNEL_SCHEMA)
        self.assertEqual(pointer["channel"], "iran-staging")
        self.assertEqual(pointer["release_sha"], "abc123")
        self.assertEqual(pointer["manifest_key"], "staging/deploy-bridge/releases/abc123/manifest.json")

    def test_upload_dry_run_writes_manifest_and_masks_credentials(self):
        tmpdir, artifacts = self.make_artifacts()
        self.addCleanup(tmpdir.cleanup)
        manifest_out = Path(tmpdir.name) / "manifest.json"
        output = io.StringIO()
        env = {
            "ARVAN_OBJECT_STORAGE_ACCESS_KEY": "access-key-sensitive",
            "ARVAN_OBJECT_STORAGE_SECRET_KEY": "secret-key-sensitive",
        }

        with mock.patch.dict(os.environ, env, clear=False):
            with redirect_stdout(output):
                exit_code = release.main(self.upload_args(artifacts, manifest_out))

        self.assertEqual(exit_code, 0)
        raw = output.getvalue()
        self.assertNotIn("access-key-sensitive", raw)
        self.assertNotIn("secret-key-sensitive", raw)
        payload = json.loads(raw)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dry_run"])
        self.assertTrue(manifest_out.exists())
        manifest = json.loads(manifest_out.read_text(encoding="utf-8"))
        self.assertIn("project_payload", manifest["artifacts"])
        self.assertEqual(payload["channels"]["iran-staging"]["key"], "staging/deploy-bridge/channels/iran-staging/latest.json")

    def test_artifact_upload_uses_bounded_managed_multipart_transfer(self):
        tmpdir, artifacts = self.make_artifacts()
        self.addCleanup(tmpdir.cleanup)
        client = mock.Mock()
        item = {
            "key": "staging/deploy-bridge/releases/abc123/project.tar.gz",
            "sha256": "digest",
        }

        release.upload_artifact(
            client,
            bucket="staging-bucket",
            item=item,
            path=artifacts["project_payload"],
            name="project_payload",
            release_sha="abc123",
        )

        client.upload_file.assert_called_once()
        call = client.upload_file.call_args
        self.assertEqual(call.args[:3], (
            str(artifacts["project_payload"]),
            "staging-bucket",
            item["key"],
        ))
        self.assertEqual(call.kwargs["ExtraArgs"]["Metadata"]["release-sha"], "abc123")
        transfer = call.kwargs["Config"]
        self.assertEqual(transfer.multipart_threshold, release.ARTIFACT_MULTIPART_THRESHOLD)
        self.assertEqual(transfer.multipart_chunksize, release.ARTIFACT_MULTIPART_CHUNK_SIZE)
        self.assertFalse(transfer.use_threads)

    def test_non_staging_prefix_is_rejected(self):
        tmpdir, artifacts = self.make_artifacts()
        self.addCleanup(tmpdir.cleanup)
        manifest_out = Path(tmpdir.name) / "manifest.json"
        args = release.parse_args(
            [
                "upload",
                "--bucket",
                "bucket",
                "--prefix",
                "production/deploy",
                "--manifest-out",
                str(manifest_out),
                "--artifact",
                f"project_payload={artifacts['project_payload']}",
            ]
        )

        with self.assertRaisesRegex(ValueError, "Refusing non-staging prefix"):
            release.validate_config(args, require_credentials=False)

    def test_fetch_dry_run_reports_release_manifest_key(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = release.main(
                [
                    "fetch",
                    "--bucket",
                    "staging-bucket",
                    "--prefix",
                    "staging/deploy-bridge",
                    "--release-sha",
                    "abc123",
                    "--download-dir",
                    "/tmp/staging-downloads",
                ]
            )

        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["manifest"]["key"], "staging/deploy-bridge/releases/abc123/manifest.json")

    def test_fetch_latest_dry_run_reports_channel_pointer_key(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = release.main(
                [
                    "fetch-latest",
                    "--bucket",
                    "staging-bucket",
                    "--prefix",
                    "staging/deploy-bridge",
                    "--channel",
                    "iran-staging",
                    "--download-dir",
                    "/tmp/staging-downloads",
                ]
            )

        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["channel"]["key"], "staging/deploy-bridge/channels/iran-staging/latest.json")
        self.assertEqual(payload["channel"]["path"], "/tmp/staging-downloads/channels/iran-staging/latest.json")


if __name__ == "__main__":
    unittest.main()
