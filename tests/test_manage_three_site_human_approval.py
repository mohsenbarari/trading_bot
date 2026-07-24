from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest import mock

from core.canonical_json import canonical_json_bytes
from core.human_approval import approval_subject, verify_human_approval
from core.human_approval_issuer import (
    HumanApprovalIssuerError,
    create_enrollment,
    issuer_paths,
    secure_json,
    totp_code,
    verify_bootstrap_receipt,
    verify_issuer_audit,
    write_enrollment,
)
from core.secure_file_io import verify_hash_chained_jsonl
from scripts import manage_three_site_human_approval as manager


class ManageHumanApprovalTests(unittest.TestCase):
    def test_issue_flow_persists_state_audit_and_non_overwriting_token(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        password = "correct horse battery staple"
        artifacts = create_enrollment(
            operator="mohsen",
            password=password,
            now=now,
            scrypt_n=2**14,
        )
        subject = approval_subject(
            artifact_type="three-site-staging-inventory-v3",
            artifact_sha256="a" * 64,
            release_sha="b" * 40,
            bindings={"inventory_stage": "provisioned"},
        )

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: ANN001, ANN206
                return now if tz is not None else now.replace(tzinfo=None)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            issuer = root / "issuer"
            write_enrollment(issuer, artifacts)
            subject_path = root / "subject.json"
            subject_path.write_text(json.dumps(subject) + "\n")
            subject_path.chmod(0o600)
            output = root / "approval-token.json"
            subject_hash = hashlib.sha256(canonical_json_bytes(subject)).hexdigest()
            args = SimpleNamespace(
                directory=issuer,
                subject=subject_path,
                action="approve_inventory",
                environment="staging",
                ttl_seconds=600,
                use_recovery_code=False,
                output=output,
            )
            with (
                mock.patch.object(manager.sys.stdin, "isatty", return_value=True),
                mock.patch.object(manager.sys.stderr, "isatty", return_value=True),
                mock.patch.object(manager, "_assert_synchronized_clock"),
                mock.patch.object(manager, "datetime", FixedDateTime),
                mock.patch("builtins.input", return_value=f"APPROVE approve_inventory staging {subject_hash[:12]}"),
                mock.patch.object(
                    manager.getpass,
                    "getpass",
                    side_effect=[
                        password,
                        totp_code(artifacts.totp_secret, at=now)[1],
                    ],
                ),
            ):
                result = manager._issue(args)
            self.assertEqual(result["status"], "approved")
            token = secure_json(output, label="approval token")
            verify_human_approval(
                token,
                policy_payload=artifacts.policy_payload,
                expected_action="approve_inventory",
                expected_environment="staging",
                expected_subject=subject,
                now=now,
            )
            state = secure_json(issuer_paths(issuer)["state"], label="issuer state")
            self.assertEqual(state["issued_sequence"], 1)
            audit = verify_hash_chained_jsonl(issuer_paths(issuer)["audit"])
            self.assertEqual(audit[-1]["event"], "human_approval_issued")

    def test_clock_must_be_provably_synchronized(self) -> None:
        synchronized = subprocess.CompletedProcess([], 0, "yes\n", "")
        with mock.patch.object(manager.subprocess, "run", return_value=synchronized):
            manager._assert_synchronized_clock()
        for result in (
            subprocess.CompletedProcess([], 0, "no\n", ""),
            subprocess.CompletedProcess([], 1, "", "unavailable"),
        ):
            with (
                mock.patch.object(manager.subprocess, "run", return_value=result),
                self.assertRaisesRegex(HumanApprovalIssuerError, "clock"),
            ):
                manager._assert_synchronized_clock()

    def test_issue_session_authenticates_once_and_persists_reusable_release_scope(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        password = "correct horse battery staple"
        artifacts = create_enrollment(
            operator="mohsen",
            password=password,
            now=now,
            scrypt_n=2**14,
        )
        release_sha = "b" * 40
        subject = approval_subject(
            artifact_type="three-site-staging-inventory-v3",
            artifact_sha256="a" * 64,
            release_sha=release_sha,
            bindings={"campaign_id": "campaign-1"},
        )

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: ANN001, ANN206
                return now if tz is not None else now.replace(tzinfo=None)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            issuer = root / "issuer"
            write_enrollment(issuer, artifacts)
            output = root / "staging-session.json"
            actions = ["approve_inventory", "start_full_matrix"]
            args = SimpleNamespace(
                directory=issuer,
                release_sha=release_sha,
                actions=actions,
                ttl_seconds=48 * 60 * 60,
                use_recovery_code=False,
                output=output,
            )
            with (
                mock.patch.object(manager.sys.stdin, "isatty", return_value=True),
                mock.patch.object(manager.sys.stderr, "isatty", return_value=True),
                mock.patch.object(manager, "_assert_synchronized_clock"),
                mock.patch.object(manager, "datetime", FixedDateTime),
                mock.patch(
                    "builtins.input",
                    return_value=f"AUTHORIZE STAGING {release_sha[:12]} 48H",
                ),
                mock.patch.object(
                    manager.getpass,
                    "getpass",
                    side_effect=[
                        password,
                        totp_code(artifacts.totp_secret, at=now)[1],
                    ],
                ),
            ):
                result = manager._issue_session(args)
            self.assertEqual(result["status"], "session-authorized")
            token = secure_json(output, label="staging session")
            for action in actions:
                verified = verify_human_approval(
                    token,
                    policy_payload=artifacts.policy_payload,
                    expected_action=action,
                    expected_environment="staging",
                    expected_subject=subject,
                    now=now,
                )
                self.assertEqual(verified.action, action)
            audit = verify_hash_chained_jsonl(issuer_paths(issuer)["audit"])
            self.assertEqual(audit[-1]["event"], "human_approval_issued")
            self.assertEqual(audit[-1]["action"], "authorize_staging_session")
            receipt = verify_bootstrap_receipt(
                secure_json(
                    issuer_paths(issuer)["receipt"],
                    label="approval bootstrap receipt",
                ),
                policy_payload=artifacts.policy_payload,
            )
            verify_issuer_audit(
                audit,
                bootstrap_receipt=receipt,
                policy_payload=artifacts.policy_payload,
                state_payload=secure_json(
                    issuer_paths(issuer)["state"],
                    label="issuer state",
                ),
            )

    def test_issuer_directory_must_be_real_owner_only_0700(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            issuer = root / "issuer"
            issuer.mkdir(mode=0o700)
            manager._assert_secure_issuer_directory(issuer)
            issuer.chmod(0o750)
            with self.assertRaisesRegex(HumanApprovalIssuerError, "0700"):
                manager._assert_secure_issuer_directory(issuer)
            issuer.chmod(0o700)
            alias = root / "issuer-link"
            alias.symlink_to(issuer, target_is_directory=True)
            with self.assertRaisesRegex(HumanApprovalIssuerError, "safely"):
                manager._assert_secure_issuer_directory(alias)

    def test_token_output_cannot_overwrite_or_enter_issuer_secret_directory(self) -> None:
        now = datetime.now(timezone.utc)
        artifacts = create_enrollment(
            operator="mohsen",
            password="correct horse battery staple",
            now=now,
            scrypt_n=2**14,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            issuer = root / "issuer"
            write_enrollment(issuer, artifacts)
            subject_path = root / "subject.json"
            subject = approval_subject(
                artifact_type="three-site-staging-inventory-v3",
                artifact_sha256="a" * 64,
                release_sha="b" * 40,
                bindings={},
            )
            subject_path.write_text(json.dumps(subject) + "\n")
            subject_path.chmod(0o600)
            args = SimpleNamespace(
                directory=issuer,
                subject=subject_path,
                action="approve_inventory",
                environment="staging",
                ttl_seconds=600,
                use_recovery_code=False,
                output=issuer / "must-not-write.json",
            )
            with (
                mock.patch.object(manager.sys.stdin, "isatty", return_value=True),
                mock.patch.object(manager.sys.stderr, "isatty", return_value=True),
                mock.patch.object(manager, "_assert_synchronized_clock"),
                self.assertRaisesRegex(HumanApprovalIssuerError, "outside"),
            ):
                manager._issue(args)

            existing = root / "existing-token.json"
            existing.write_text("do not overwrite\n")
            existing.chmod(0o600)
            args.output = existing
            with (
                mock.patch.object(manager.sys.stdin, "isatty", return_value=True),
                mock.patch.object(manager.sys.stderr, "isatty", return_value=True),
                mock.patch.object(manager, "_assert_synchronized_clock"),
                self.assertRaisesRegex(HumanApprovalIssuerError, "already exists"),
            ):
                manager._issue(args)
            self.assertEqual(existing.read_text(), "do not overwrite\n")


if __name__ == "__main__":
    unittest.main()
