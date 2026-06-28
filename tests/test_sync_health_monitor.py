import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import sample_sync_health


class SyncHealthMonitorTests(unittest.TestCase):
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
