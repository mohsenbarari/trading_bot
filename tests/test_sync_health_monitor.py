import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import sample_sync_health


ROOT = Path(__file__).resolve().parents[1]


class SyncHealthMonitorTests(unittest.TestCase):
    def _render_monitor_unit(self, *, skip_iran: str) -> str:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_bin = root / "bin"
            systemd_dir = root / "systemd"
            fake_bin.mkdir()
            systemd_dir.mkdir()
            fake_systemctl = fake_bin / "systemctl"
            fake_systemctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_systemctl.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "PYTHON_BIN": "/usr/bin/python3",
                    "SERVICE_NAME": "test-sync-sampler",
                    "SYSTEMD_DIR": str(systemd_dir),
                    "SYNC_HEALTH_MONITOR_SKIP_IRAN": skip_iran,
                }
            )
            subprocess.run(
                ["bash", str(ROOT / "scripts/install_sync_health_monitor.sh")],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            return (systemd_dir / "test-sync-sampler.service").read_text(encoding="utf-8")

    def test_installer_keeps_cross_server_probe_for_foreign_host(self):
        unit = self._render_monitor_unit(skip_iran="0")

        self.assertIn("scripts/sample_sync_health.py", unit)
        self.assertNotIn("--skip-iran", unit)

    def test_installer_limits_iran_host_to_its_local_probe(self):
        unit = self._render_monitor_unit(skip_iran="1")

        self.assertIn("scripts/sample_sync_health.py --skip-iran", unit)

    def test_installer_rejects_invalid_skip_iran_value(self):
        env = os.environ.copy()
        env["SYNC_HEALTH_MONITOR_SKIP_IRAN"] = "yes"
        completed = subprocess.run(
            ["bash", str(ROOT / "scripts/install_sync_health_monitor.sh")],
            capture_output=True,
            text=True,
            env=env,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("must be 0 or 1", completed.stderr)

    def test_load_env_file_sets_defaults_without_overwriting_existing_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("OBSERVABILITY_API_KEY=file-key\nIRAN_DIR=/srv/app\n", encoding="utf-8")
            with patch.dict(os.environ, {"OBSERVABILITY_API_KEY": "existing-key"}, clear=True):
                sample_sync_health.load_env_file(str(env_path))
                self.assertEqual(os.environ["OBSERVABILITY_API_KEY"], "existing-key")
                self.assertEqual(os.environ["IRAN_DIR"], "/srv/app")

    def test_build_iran_ssh_command_targets_remote_localhost_probe(self):
        args = SimpleNamespace(
            iran_host="root@example",
            iran_dir="/srv/trading-bot",
            ssh_option=["StrictHostKeyChecking=accept-new", "ConnectTimeout=5"],
        )

        command = sample_sync_health.build_iran_ssh_command(args)

        self.assertEqual(command[:5], ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=5"])
        self.assertEqual(command[5], "root@example")
        self.assertIn("cd /srv/trading-bot", command[6])
        self.assertIn("python3 scripts/sample_sync_health.py --skip-iran", command[6])

    def test_main_skip_iran_prints_local_sample(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "scripts.sample_sync_health.fetch_sync_health",
            return_value={"status": "ok", "server_mode": "foreign"},
        ), patch("sys.argv", ["sample_sync_health.py", "--skip-iran"]), patch("sys.stdout") as stdout:
            self.assertEqual(sample_sync_health.main(), 0)

        payload = json.loads("".join(call.args[0] for call in stdout.write.call_args_list).strip())
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["failures"], 0)
        self.assertEqual(payload["samples"]["local"]["server_mode"], "foreign")


if __name__ == "__main__":
    unittest.main()
