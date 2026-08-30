from __future__ import annotations

from contextlib import redirect_stdout
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import bind_production_backup_manifest as binder
from scripts import prepare_production_private_primary_manifest as preparer


class ProductionBackupManifestBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        if os.geteuid() != 0:
            self.skipTest("root ownership contract requires root")
        self.temporary = tempfile.TemporaryDirectory(prefix="backup-manifest-")
        base = Path(self.temporary.name)
        self.control = base / "release-control"
        self.evidence = base / "evidence"
        self.rehearsals = base / "rehearsals"
        self.control.mkdir(mode=0o700)
        self.evidence.mkdir(mode=0o700)
        self.rehearsals.mkdir(mode=0o700)
        self.source = self.control / "source.env"
        self.output = self.control / "bound.env"
        self.receipt = self.control / "bound.receipt.json"
        source_test = __import__(
            "tests.test_prepare_production_private_primary_manifest",
            fromlist=["ProductionPrivatePrimaryManifestPreparationTests"],
        ).ProductionPrivatePrimaryManifestPreparationTests
        probe = source_test(methodName="test_checked_in_manifest_schema_is_transform_compatible")
        probe.secret_marker = "must-stay-private"
        source_text = probe._source_text() + (
            "PRODUCTION_BACKUP_RECEIPT_PATH=/root/old-backup.json\n"
            "PRODUCTION_BACKUP_RECEIPT_SHA256=" + "1" * 64 + "\n"
            "PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_PATH=/root/old-rehearsal.json\n"
            "PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_SHA256=" + "2" * 64 + "\n"
        )
        self.source.write_text(source_text, encoding="utf-8")
        self.source.chmod(0o600)
        self.backup = self.evidence / "backup.json"
        payload = {
            "status": "ok",
            "roles": ["foreign", "iran"],
            "results": [
                {"role": "foreign", "status": "ok", "restore_smoke": {"status": "passed"}},
                {"role": "iran", "status": "ok", "restore_smoke": {"status": "passed"}},
            ],
        }
        self.backup.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        self.backup.chmod(0o600)
        self.rehearsal = self.rehearsals / "rehearsal.json"
        rehearsal = {
            "contract": "production-migration-rehearsal-v1",
            "status": "passed",
            "production_mutation": False,
            "backup_receipt_sha256": sha256(self.backup.read_bytes()).hexdigest(),
            "roles": ["foreign", "iran"],
            "results": [
                {"role": "foreign", "status": "passed"},
                {"role": "iran", "status": "passed"},
            ],
        }
        self.rehearsal.write_text(json.dumps(rehearsal) + "\n", encoding="utf-8")
        self.rehearsal.chmod(0o600)
        self.patches = (
            mock.patch.object(preparer, "APPROVED_ROOT", self.control),
            mock.patch.object(binder, "APPROVED_BACKUP_ROOT", self.evidence),
            mock.patch.object(
                binder, "APPROVED_REHEARSAL_ROOT", self.rehearsals
            ),
        )
        for patch in self.patches:
            patch.start()

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        self.temporary.cleanup()

    def _args(self, backup_digest: str | None = None) -> list[str]:
        return [
            "--source", str(self.source),
            "--expected-source-sha256", sha256(self.source.read_bytes()).hexdigest(),
            "--backup-receipt", str(self.backup),
            "--expected-backup-receipt-sha256", backup_digest or sha256(self.backup.read_bytes()).hexdigest(),
            "--migration-rehearsal-receipt", str(self.rehearsal),
            "--expected-migration-rehearsal-receipt-sha256", sha256(self.rehearsal.read_bytes()).hexdigest(),
            "--output", str(self.output),
            "--receipt", str(self.receipt),
            "--confirm", binder.CONFIRMATION,
        ]

    def _run(self, args: list[str] | None = None) -> tuple[int, dict[str, object]]:
        stream = io.StringIO()
        with redirect_stdout(stream):
            status = binder.main(args or self._args())
        return status, json.loads(stream.getvalue())

    def test_binds_only_fresh_backup_fields_and_preserves_source(self) -> None:
        source = self.source.read_bytes()
        status, result = self._run()
        self.assertEqual(status, 0)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(self.source.read_bytes(), source)
        rendered = self.output.read_text(encoding="utf-8")
        self.assertIn(f"PRODUCTION_BACKUP_RECEIPT_PATH={self.backup}\n", rendered)
        self.assertIn(
            f"PRODUCTION_BACKUP_RECEIPT_SHA256={sha256(self.backup.read_bytes()).hexdigest()}\n",
            rendered,
        )
        self.assertEqual(set(result["changed_keys"]), set(binder.TARGET_KEYS))

    def test_wrong_digest_and_failed_restore_smoke_fail_closed(self) -> None:
        status, result = self._run(self._args("0" * 64))
        self.assertEqual(status, 2)
        self.assertEqual(result["reason_code"], "backup_receipt_cas_mismatch")
        self.assertFalse(self.output.exists())

        document = json.loads(self.backup.read_text())
        document["results"][0]["restore_smoke"]["status"] = "failed"
        self.backup.write_text(json.dumps(document) + "\n")
        self.backup.chmod(0o600)
        status, result = self._run()
        self.assertEqual(status, 2)
        self.assertEqual(result["reason_code"], "backup_receipt_contract_invalid")
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
