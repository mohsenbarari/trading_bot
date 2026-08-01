from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "preflight_human_approval_bootstrap_receipt.py"
SPEC = importlib.util.spec_from_file_location("human_approval_receipt_preflight", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class HumanApprovalBootstrapReceiptPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="approval-receipt-preflight-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.issuer_directory = self.root / "human-approval"
        self.issuer_directory.mkdir(mode=0o700)
        self.receipt = self.issuer_directory / "bootstrap-receipt.json"
        self.owner_uid = os.geteuid()

    def _write_receipt(self, payload: str = "receipt-content-must-not-appear") -> None:
        self.receipt.write_text(payload, encoding="utf-8")
        self.receipt.chmod(0o600)

    def _report(self, receipt: Path | None = None) -> dict:
        return MODULE.diagnose_bootstrap_receipt(
            receipt or self.receipt,
            owner_uid=self.owner_uid,
            trust_anchor=self.root,
        )

    @staticmethod
    def _codes(report: dict) -> set[str]:
        return {finding["code"] for finding in report["findings"]}

    def test_ready_report_is_metadata_only_and_never_reads_payload(self) -> None:
        secret_like_payload = "receipt-content-must-not-appear"
        self._write_receipt(secret_like_payload)
        with mock.patch.object(MODULE.os, "read", side_effect=AssertionError("payload read")):
            report = self._report()

        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["runtime_readiness"], "ready")
        self.assertEqual(report["hardened_path_readiness"], "ready")
        self.assertTrue(report["local_only"])
        self.assertFalse(report["payload_read"])
        self.assertEqual(report["receipt"]["kind"], "regular")
        self.assertNotIn(secret_like_payload, json.dumps(report, sort_keys=True))
        self.assertNotIn("size_bytes", report["receipt"])

    def test_missing_receipt_reports_the_exact_local_cause(self) -> None:
        report = self._report()

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["runtime_readiness"], "blocked")
        self.assertIn("receipt_missing", self._codes(report))
        self.assertIsNone(report["receipt"])

    def test_symlink_is_reported_without_opening_target_payload(self) -> None:
        target = self.root / "other-receipt"
        target.write_text("do-not-read", encoding="utf-8")
        target.chmod(0o600)
        self.receipt.symlink_to(target)

        with mock.patch.object(MODULE.os, "read", side_effect=AssertionError("payload read")):
            report = self._report()

        self.assertEqual(report["status"], "blocked")
        self.assertIn("receipt_symlink", self._codes(report))
        self.assertEqual(report["receipt"]["kind"], "symlink")

    def test_leaf_permissions_and_hard_link_policy_are_actionable(self) -> None:
        self._write_receipt()
        self.receipt.chmod(0o640)
        report = self._report()
        self.assertIn("receipt_group_or_world_accessible", self._codes(report))
        self.assertEqual(report["runtime_readiness"], "blocked")

        self.receipt.chmod(0o600)
        duplicate = self.root / "duplicate-receipt"
        os.link(self.receipt, duplicate)
        report = self._report()
        self.assertIn("receipt_hard_link_count_invalid", self._codes(report))
        self.assertTrue(report["receipt"]["hard_link_count"] >= 2)

    def test_issuer_directory_requires_exact_owner_only_0700(self) -> None:
        self._write_receipt()
        self.issuer_directory.chmod(0o750)

        report = self._report()

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["runtime_readiness"], "blocked")
        self.assertIn("issuer_directory_not_exact_owner_0700", self._codes(report))

    def test_unsafe_ancestor_is_a_hardened_path_blocker(self) -> None:
        unsafe = self.root / "unsafe"
        unsafe.mkdir(mode=0o700)
        unsafe.chmod(0o777)
        issuer = unsafe / "human-approval"
        issuer.mkdir(mode=0o700)
        receipt = issuer / "bootstrap-receipt.json"
        receipt.write_text("do-not-read", encoding="utf-8")
        receipt.chmod(0o600)

        report = self._report(receipt)

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["runtime_readiness"], "ready")
        self.assertEqual(report["hardened_path_readiness"], "blocked")
        self.assertIn("ancestor_group_or_world_writable", self._codes(report))

    def test_ancestor_symlink_is_named_instead_of_collapsing_to_open_error(self) -> None:
        target = self.root / "real"
        target.mkdir(mode=0o700)
        (target / "human-approval").mkdir(mode=0o700)
        receipt = target / "human-approval" / "bootstrap-receipt.json"
        receipt.write_text("do-not-read", encoding="utf-8")
        receipt.chmod(0o600)
        link = self.root / "linked"
        link.symlink_to(target, target_is_directory=True)

        report = self._report(link / "human-approval" / "bootstrap-receipt.json")

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["hardened_path_readiness"], "blocked")
        self.assertIn("ancestor_symlink", self._codes(report))

    def test_cli_emits_one_nonsecret_json_document_and_exit_status(self) -> None:
        self._write_receipt("never-render-this")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = MODULE.main(
                [
                    "--receipt",
                    str(self.receipt),
                    "--owner-uid",
                    str(self.owner_uid),
                    "--trust-anchor",
                    str(self.root),
                ]
            )

        report = json.loads(stdout.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(report["status"], "ready")
        self.assertNotIn("never-render-this", stdout.getvalue())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
