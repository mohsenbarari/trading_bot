import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from scripts import staging_object_storage_artifact as artifact


class StagingObjectStorageArtifactTests(unittest.TestCase):
    def make_artifact(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        tmpdir = tempfile.TemporaryDirectory()
        path = Path(tmpdir.name) / "bundle.tar.gz"
        path.write_bytes(b"staging artifact bytes")
        return tmpdir, path

    def test_build_manifest_uses_staging_release_path(self):
        tmpdir, path = self.make_artifact()
        self.addCleanup(tmpdir.cleanup)
        args = artifact.parse_args(
            [
                "--artifact",
                str(path),
                "--bucket",
                "staging-bucket",
                "--prefix",
                "staging/deploy-bridge",
                "--release-sha",
                "abc123-dirty",
            ]
        )
        config = artifact.validate_args(args, require_credentials=False)

        manifest = artifact.build_manifest(args, config)

        self.assertEqual(manifest["schema"], "trading_bot_staging_object_storage_artifact_v1")
        self.assertEqual(
            manifest["artifact"]["key"],
            "staging/deploy-bridge/releases/abc123-dirty/trading-bot-staging-abc123-dirty.tar.gz",
        )
        self.assertEqual(manifest["manifest"]["key"], "staging/deploy-bridge/releases/abc123-dirty/manifest.json")
        self.assertEqual(manifest["artifact"]["size_bytes"], path.stat().st_size)

    def test_non_staging_prefix_is_rejected(self):
        tmpdir, path = self.make_artifact()
        self.addCleanup(tmpdir.cleanup)
        args = artifact.parse_args(["--artifact", str(path), "--bucket", "bucket", "--prefix", "production/deploy"])

        with self.assertRaisesRegex(ValueError, "Refusing non-staging prefix"):
            artifact.validate_args(args, require_credentials=False)

    def test_dry_run_does_not_leak_credentials(self):
        tmpdir, path = self.make_artifact()
        self.addCleanup(tmpdir.cleanup)
        env = {
            "ARVAN_OBJECT_STORAGE_ACCESS_KEY": "access-key-sensitive",
            "ARVAN_OBJECT_STORAGE_SECRET_KEY": "secret-key-sensitive",
        }
        output = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False):
            with redirect_stdout(output):
                exit_code = artifact.main(
                    [
                        "--artifact",
                        str(path),
                        "--bucket",
                        "staging-bucket",
                        "--prefix",
                        "staging/deploy-bridge",
                    ]
                )

        self.assertEqual(exit_code, 0)
        raw = output.getvalue()
        self.assertNotIn("access-key-sensitive", raw)
        self.assertNotIn("secret-key-sensitive", raw)
        payload = json.loads(raw)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dry_run"])
        self.assertTrue(payload["config"]["access_key_present"])
        self.assertTrue(payload["config"]["secret_key_present"])

    def test_execute_requires_credentials(self):
        tmpdir, path = self.make_artifact()
        self.addCleanup(tmpdir.cleanup)
        output = io.StringIO()
        with mock.patch.dict(
            os.environ,
            {"ARVAN_OBJECT_STORAGE_ACCESS_KEY": "", "ARVAN_OBJECT_STORAGE_SECRET_KEY": ""},
            clear=False,
        ):
            with redirect_stdout(output):
                exit_code = artifact.main(["--artifact", str(path), "--bucket", "staging-bucket", "--execute"])

        self.assertEqual(exit_code, 2)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertIn(artifact.DEFAULT_ACCESS_KEY_ENV, payload["error"])
        self.assertIn(artifact.DEFAULT_SECRET_KEY_ENV, payload["error"])


if __name__ == "__main__":
    unittest.main()
