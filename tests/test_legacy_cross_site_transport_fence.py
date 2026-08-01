from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_SCRIPT = REPO_ROOT / "scripts" / "production_deploy_online.sh"
RECOVERY_SCRIPT = REPO_ROOT / "scripts" / "recover_cross_server_sync.sh"


def run_shell(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )


class LegacyCrossSiteTransportFenceTests(unittest.TestCase):
    def test_mutating_or_payload_legacy_commands_fail_before_manifest_or_network(self):
        blocked_commands = (
            "release",
            "deploy-foreign",
            "bootstrap-iran",
            "configure-nginx",
            "issue-cert",
            "sync-project",
            "ship-images",
            "load-images",
            "deploy-iran",
            "seed-shared-data",
        )
        missing_manifest = "/tmp/legacy-cross-site-fence-missing.env"

        for command in blocked_commands:
            with self.subTest(command=command):
                result = run_shell(
                    str(PRODUCTION_SCRIPT), "--manifest", missing_manifest, command
                )

                self.assertNotEqual(result.returncode, 0)
                output = result.stdout + result.stderr
                self.assertIn("blocked before manifest/SSH", output)
                self.assertIn("private, versioned, age-encrypted Object Storage", output)
                self.assertIn("no environment or configuration bypass", output)
                self.assertNotIn("Manifest not found", output)

    def test_only_artifact_build_is_allowlisted(self):
        source = PRODUCTION_SCRIPT.read_text(encoding="utf-8")
        guard = source.split("assert_legacy_cross_site_transport_fenced() {", 1)[1].split(
            "\n}\n", 1
        )[0]

        self.assertIn("build-release", guard)

        self.assertIn("before a manifest can supply host credentials", guard)
        allowlisted_case = guard.split('case "$COMMAND" in', 1)[1].split(")", 1)[0]
        for command in ("deploy-foreign", "check-local", "inspect-shared-data", "healthcheck"):
            with self.subTest(command=command):
                self.assertNotIn(command, allowlisted_case)
        main_body = source.split("main() {", 1)[1].split("\n}\n\nmain", 1)[0]
        self.assertLess(
            main_body.index("assert_legacy_cross_site_transport_fenced"),
            main_body.index("ensure_manifest_file"),
        )

    def test_former_read_only_peer_commands_fail_before_manifest_or_ssh(self):
        missing_manifest = "/tmp/legacy-cross-site-fence-missing.env"
        for command in ("check-local", "inspect-shared-data", "healthcheck"):
            with self.subTest(command=command):
                result = run_shell(str(PRODUCTION_SCRIPT), "--manifest", missing_manifest, command)

                self.assertNotEqual(result.returncode, 0)
                output = result.stdout + result.stderr
                self.assertIn("blocked before manifest/SSH", output)
                self.assertNotIn("Manifest not found", output)

    def test_recovery_script_fails_before_reading_configuration_or_contacting_apis(self):
        result = run_shell(str(RECOVERY_SCRIPT))

        self.assertNotEqual(result.returncode, 0)
        output = result.stdout + result.stderr
        self.assertIn("blocked before configuration/network", output)
        self.assertIn("private, versioned, age-encrypted Object Storage", output)
        self.assertIn("no environment or configuration bypass", output)
        self.assertNotIn("IRAN_HOST is required", output)

    def test_recovery_help_is_inert_and_describes_the_replacement_transport(self):
        result = run_shell(str(RECOVERY_SCRIPT), "--help")

        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        self.assertIn("legacy direct cross-server recovery path is retired", result.stdout)
        self.assertIn("private/versioned age-encrypted", result.stdout)


if __name__ == "__main__":
    unittest.main()
