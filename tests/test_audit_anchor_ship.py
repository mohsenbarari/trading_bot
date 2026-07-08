import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import ship_audit_anchor


class AuditAnchorShipTests(unittest.TestCase):
    def _write_anchor_file(self, tmpdir: str) -> Path:
        path = Path(tmpdir) / "audit-anchor.jsonl"
        first = {
            "anchor_exported_at": "2026-06-10T00:00:00Z",
            "source_name": "foreign",
            "host_id": "host-a",
            "release_id": "release-a",
            "audit_event_id": "evt-1",
            "audit_recorded_at": "2026-06-10T00:00:00Z",
            "event_hash": "hash-1",
            "previous_hash": None,
            "audit_trail_records": 10,
            "audit_trail_sha256": "sha-1",
        }
        second = dict(first)
        second["audit_event_id"] = "evt-2"
        second["event_hash"] = "hash-2"
        path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8")
        return path

    def test_load_latest_anchor_line_uses_last_non_empty_line(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_anchor_file(tmpdir)
            line, payload = ship_audit_anchor._load_latest_anchor_line(path)

        self.assertIn('"audit_event_id": "evt-2"', line)
        self.assertEqual(payload["audit_event_id"], "evt-2")

    def test_load_latest_anchor_line_rejects_missing_required_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit-anchor.jsonl"
            path.write_text(json.dumps({"audit_event_id": "evt-1"}) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing required fields"):
                ship_audit_anchor._load_latest_anchor_line(path)

    def test_build_remote_append_command(self):
        command = ship_audit_anchor._build_remote_append_command("root@example:/srv/audit/anchor.jsonl", '{"k":"v"}')

        self.assertEqual(command[0], "ssh")
        self.assertEqual(command[1], "root@example")
        self.assertIn("install -d", command[2])
        self.assertIn("/srv/audit/anchor.jsonl", command[2])

    def test_build_remote_append_command_accepts_optional_port(self):
        command = ship_audit_anchor._build_remote_append_command(
            "root@example:/srv/audit/anchor.jsonl",
            '{"k":"v"}',
            port="37067",
        )

        self.assertEqual(command[:3], ["ssh", "-p", "37067"])
        self.assertEqual(command[3], "root@example")
        self.assertIn("install -d", command[4])

    def test_append_remote_uses_ssh_subprocess_with_latest_line(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_anchor_file(tmpdir)
            latest_line, _payload = ship_audit_anchor._load_latest_anchor_line(path)
            with patch("scripts.ship_audit_anchor.subprocess.run") as run_mock:
                ship_audit_anchor._append_remote("root@example:/srv/audit/anchor.jsonl", latest_line)

        run_mock.assert_called_once()
        self.assertIn("evt-2", run_mock.call_args.kwargs["input"])


if __name__ == "__main__":
    unittest.main()
