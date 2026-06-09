import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.audit_logger import audit_log
from scripts.export_audit_logs import _verify_audit_chain, main


class AuditExportTests(unittest.TestCase):
    def test_file_export_writes_manifest_and_verifies_chain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"
            output_path = Path(tmpdir) / "export.jsonl"
            manifest_path = Path(tmpdir) / "manifest.json"
            with patch("core.audit_sink._audit_trail_path", return_value=str(audit_path)):
                audit_log("user.update", target_type="user", target_id=1, actor_id=9)
                audit_log("session.reset", target_type="session", target_id=2, actor_id=9)

            with patch(
                "sys.argv",
                [
                    "export_audit_logs.py",
                    "--source=file",
                    f"--input={audit_path}",
                    f"--output={output_path}",
                    f"--manifest-output={manifest_path}",
                ],
            ):
                self.assertEqual(main(), 0)

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["source"], "file")
            self.assertEqual(manifest["records"], 2)
            self.assertTrue(manifest["integrity"]["integrity_ok"])
            self.assertEqual(len(output_path.read_text(encoding="utf-8").splitlines()), 2)

    def test_verify_audit_chain_rejects_tampered_record(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"
            with patch("core.audit_sink._audit_trail_path", return_value=str(audit_path)):
                audit_log("user.update", target_type="user", target_id=1, actor_id=9)
            record = json.loads(audit_path.read_text(encoding="utf-8"))
            record["payload"]["target_id"] = 99
            audit_path.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

            verification = _verify_audit_chain(audit_path)

        self.assertFalse(verification["integrity_ok"])
        self.assertEqual(verification["error"], "event_hash_mismatch")


if __name__ == "__main__":
    unittest.main()
