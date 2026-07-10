import json
import pathlib
import tempfile
import unittest
import unittest.mock

from scripts.report_production_recoverability import evaluate_backup, parse_json_output as parse_report_json_output
from scripts import run_production_backup
from scripts.run_production_backup import HostTarget, build_backup_shell, parse_json_from_stdout


class ProductionRecoverabilityToolsTests(unittest.TestCase):
    def test_backup_shell_includes_core_artifacts_and_restore_smoke(self):
        shell = build_backup_shell(
            HostTarget(role="iran", project_dir="/srv/trading-bot/current", compose_file="docker-compose.iran.yml", remote=True),
            stamp="20260611T150000Z",
            backup_dir="/srv/trading-bot/backups",
            include_uploads=True,
            include_audit=True,
            include_redis=True,
            restore_smoke=True,
        )

        self.assertIn("pg_dump", shell)
        self.assertIn("trading_bot_redis", shell)
        self.assertIn("/app/uploads", shell)
        self.assertIn("/app/audit_trail", shell)
        self.assertIn("trading_bot_restore_drill_iran_20260611T150000Z", shell)
        self.assertIn("postgres:15-alpine", shell)
        self.assertIn("OWNER TO", shell)
        self.assertIn("CREATE ROLE", shell)

    def test_parse_json_from_stdout_uses_last_json_object(self):
        payload = parse_json_from_stdout("noise\n{\"status\":\"old\"}\nmore\n{\"status\":\"ok\",\"x\":1}\n")
        self.assertEqual(payload, {"status": "ok", "x": 1})

    def test_pull_iran_files_uses_accept_new_host_key_policy(self):
        seen_args = []

        def fake_run(args, timeout):
            seen_args.append(args)

            class Result:
                returncode = 0
                stderr = ""

            return Result()

        payload = {"files": [{"path": "/srv/trading-bot/backups/iran-db-test.sql.gz"}]}
        with tempfile.TemporaryDirectory() as tmp_dir, unittest.mock.patch.object(
            run_production_backup, "run_command", side_effect=fake_run
        ):
            pulled = run_production_backup.pull_iran_files(
                {
                    "IRAN_HOST": "65.109.220.59",
                    "IRAN_SSH_PORT": "37067",
                    "IRAN_SSH_USER": "root",
                },
                payload,
                pathlib.Path(tmp_dir),
            )

        self.assertEqual(pulled[0]["remote_path"], "/srv/trading-bot/backups/iran-db-test.sql.gz")
        self.assertIn("StrictHostKeyChecking=accept-new", seen_args[0])
        self.assertNotIn("StrictHostKeyChecking=no", seen_args[0])

    def test_report_json_parser_accepts_pretty_json(self):
        payload = parse_report_json_output(json.dumps({"status": "ok", "items": [1, 2]}, indent=2))
        self.assertEqual(payload["status"], "ok")

    def test_evaluate_backup_requires_all_artifacts_and_restore_when_requested(self):
        payload = {
            "status": "ok",
            "results": [
                {
                    "role": "iran",
                    "files": [
                        {"kind": "db", "bytes": 100, "sha256": "a"},
                        {"kind": "redis", "bytes": 100, "sha256": "b"},
                        {"kind": "uploads", "bytes": 100, "sha256": "c"},
                        {"kind": "audit", "bytes": 100, "sha256": "d"},
                    ],
                    "restore_smoke": {"status": "passed", "table_count": 20},
                }
            ],
        }

        failures, warnings = evaluate_backup(payload, require_restore_smoke=True)

        self.assertEqual(failures, [])
        self.assertEqual(warnings, [])

    def test_evaluate_backup_warns_when_restore_smoke_is_skipped(self):
        payload = {
            "status": "ok",
            "results": [
                {
                    "role": "iran",
                    "files": [
                        {"kind": "db", "bytes": 100, "sha256": "a"},
                        {"kind": "redis", "bytes": 100, "sha256": "b"},
                        {"kind": "uploads", "bytes": 100, "sha256": "c"},
                        {"kind": "audit", "bytes": 100, "sha256": "d"},
                    ],
                    "restore_smoke": {"status": "skipped"},
                }
            ],
        }

        failures, warnings = evaluate_backup(payload, require_restore_smoke=False)

        self.assertEqual(failures, [])
        self.assertEqual(warnings, ["iran DB restore smoke was skipped"])


if __name__ == "__main__":
    unittest.main()
