import gzip
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

from scripts.scan_telegram_queue_artifacts import (
    scan_paths,
    scan_release_surfaces,
    scan_tracked_source,
)


class TelegramQueueArtifactSecurityScanTests(unittest.TestCase):
    def test_safe_aggregate_artifact_is_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "queue-report.json"
            artifact.write_text(
                json.dumps(
                    {
                        "queue_depth": 19,
                        "destination_class": "channel",
                        "delivery_correlation_hash": "sha256:" + ("a" * 64),
                        "bot_token": "redacted",
                    }
                ),
                encoding="utf-8",
            )

            report = scan_paths([artifact])

        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["finding_count"], 0)

    def test_raw_sensitive_fields_and_common_secret_shapes_are_blocked_without_echo(self):
        synthetic_token = "1234567890:" + ("A" * 35)
        raw_dedupe = "offer:user-7788:channel-9911"
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "unsafe.log"
            artifact.write_text(
                f'bot_token={synthetic_token}\ndedupe_key="{raw_dedupe}"\nmobile=09123456789\n',
                encoding="utf-8",
            )

            report = scan_paths([artifact])
            rendered = json.dumps(report, ensure_ascii=False)

        kinds = {finding["kind"] for finding in report["findings"]}
        self.assertEqual(report["status"], "blocked")
        self.assertIn("telegram_bot_token", kinds)
        self.assertIn("raw_sensitive_field:bot_token", kinds)
        self.assertIn("raw_sensitive_field:dedupe_key", kinds)
        self.assertIn("iran_mobile", kinds)
        self.assertNotIn(synthetic_token, rendered)
        self.assertNotIn(raw_dedupe, rendered)
        self.assertNotIn("09123456789", rendered)

    def test_nested_zip_tar_and_gzip_members_are_scanned(self):
        synthetic_token = "9876543210:" + ("B" * 35)
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as archive:
            blob = f"authorization=Bearer {synthetic_token}".encode()
            info = tarfile.TarInfo("inner.log")
            info.size = len(blob)
            archive.addfile(info, io.BytesIO(blob))
        compressed_tar = gzip.compress(tar_buffer.getvalue())

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "evidence.zip"
            with zipfile.ZipFile(artifact, "w") as archive:
                archive.writestr("nested.tar.gz", compressed_tar)

            report = scan_paths([artifact])

        self.assertEqual(report["status"], "blocked")
        self.assertTrue(
            any(
                finding["member"] == "nested.tar.gz!gzip!inner.log"
                for finding in report["findings"]
            )
        )

    def test_cli_exit_codes_and_json_report_are_machine_readable(self):
        repo = Path(__file__).resolve().parents[1]
        script = repo / "scripts" / "scan_telegram_queue_artifacts.py"
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "safe.json"
            report_path = Path(directory) / "scan.json"
            artifact.write_text('{"status":"ok"}\n', encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(script), str(artifact), "--report", str(report_path)],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )

            written = json.loads(report_path.read_text(encoding="utf-8"))
            stdout = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(written["status"], "clean")
        self.assertEqual(stdout["status"], "clean")

    def test_missing_or_unreadable_input_fails_closed(self):
        report = scan_paths([Path("/definitely/not/present/topq-artifact")])
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["findings"][0]["kind"], "input_missing")

    def test_tracked_source_scan_uses_high_confidence_secret_shapes(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.check_call(["git", "init", "-q"], cwd=repo)
            safe = repo / "safe.py"
            safe.write_text('bot_token = settings.bot_token\nuser_id = 4\n', encoding="utf-8")
            subprocess.check_call(["git", "add", "safe.py"], cwd=repo)
            subprocess.check_call(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "safe"],
                cwd=repo,
            )
            clean = scan_tracked_source(repo)

            unsafe = repo / "unsafe.env"
            unsafe.write_text(
                "BOT_TOKEN=1234567890:" + ("Z" * 35) + "\n",
                encoding="utf-8",
            )
            subprocess.check_call(["git", "add", "unsafe.env"], cwd=repo)
            subprocess.check_call(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "unsafe"],
                cwd=repo,
            )
            blocked = scan_tracked_source(repo)

        self.assertEqual(clean["status"], "clean")
        self.assertRegex(clean["git_commit"], r"^[0-9a-f]{40}$")
        self.assertRegex(clean["blob_manifest_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["findings"][0]["kind"], "telegram_bot_token")

    def test_release_scan_requires_at_least_one_surface(self):
        report = scan_release_surfaces(
            artifact_paths=(),
            tracked_source_root=None,
        )
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["findings"][0]["kind"], "scan_surface_missing")


if __name__ == "__main__":
    unittest.main()
