import json
import tempfile
import unittest
from pathlib import Path

from scripts import export_audit_anchor


class AuditAnchorExportTests(unittest.TestCase):
    def _write_audit_trail(self, tmpdir: str) -> Path:
        trail_path = Path(tmpdir) / "audit.jsonl"
        first = {
            "audit_event_id": "evt-1",
            "audit_recorded_at": "2026-06-10T00:00:00Z",
            "audit_durable": True,
            "previous_hash": None,
            "payload": {"action": "login"},
        }
        first["event_hash"] = export_audit_anchor._hash_record_without_event_hash(first)
        second = {
            "audit_event_id": "evt-2",
            "audit_recorded_at": "2026-06-10T00:01:00Z",
            "audit_durable": True,
            "previous_hash": first["event_hash"],
            "payload": {"action": "reset"},
        }
        second["event_hash"] = export_audit_anchor._hash_record_without_event_hash(second)
        trail_path.write_text(
            json.dumps(first, ensure_ascii=False) + "\n" + json.dumps(second, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return trail_path

    def test_load_and_verify_head_returns_last_record_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            trail_path = self._write_audit_trail(tmpdir)
            summary = export_audit_anchor.load_and_verify_head(trail_path)

        self.assertEqual(summary["records"], 2)
        self.assertEqual(summary["head"]["audit_event_id"], "evt-2")
        self.assertEqual(summary["head"]["payload"]["action"], "reset")
        self.assertTrue(summary["trail_sha256"])

    def test_load_and_verify_head_fails_closed_on_hash_break(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            trail_path = self._write_audit_trail(tmpdir)
            lines = trail_path.read_text(encoding="utf-8").splitlines()
            broken = json.loads(lines[-1])
            broken["payload"]["action"] = "tampered"
            lines[-1] = json.dumps(broken, ensure_ascii=False)
            trail_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "event_hash mismatch"):
                export_audit_anchor.load_and_verify_head(trail_path)

    def test_write_anchor_record_appends_compact_anchor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            trail_path = self._write_audit_trail(tmpdir)
            summary = export_audit_anchor.load_and_verify_head(trail_path)
            anchor = export_audit_anchor.build_anchor_record(
                trail_summary=summary,
                release_id="release-1",
                host_id="host-a",
                source_name="foreign",
            )
            output_path = Path(tmpdir) / "anchors.jsonl"
            destination = export_audit_anchor.write_anchor_record(anchor, str(output_path))

            self.assertEqual(destination, str(output_path))
            lines = output_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            rendered = json.loads(lines[0])
            self.assertEqual(rendered["audit_event_id"], "evt-2")
            self.assertEqual(rendered["host_id"], "host-a")
            self.assertEqual(rendered["release_id"], "release-1")
            self.assertEqual(rendered["source_name"], "foreign")


if __name__ == "__main__":
    unittest.main()
