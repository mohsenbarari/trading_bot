from __future__ import annotations

import copy
import base64
from datetime import datetime, timedelta, timezone
import tempfile
from pathlib import Path
import unittest

from core.human_approval import (
    HumanApprovalError,
    approval_subject,
    verify_human_approval,
)
from core.human_approval_issuer import (
    HumanApprovalIssuerError,
    authenticate_and_issue,
    authenticate_and_issue_session,
    create_enrollment,
    decrypt_private_key,
    issuer_paths,
    match_totp,
    secure_json,
    totp_code,
    verify_bootstrap_receipt,
    verify_issuer_audit,
    write_enrollment,
)
from core.secure_file_io import (
    SecureFileError,
    append_hash_chained_jsonl,
    verify_hash_chained_jsonl,
    write_secure_new_bytes,
)


NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
PASSWORD = "correct horse battery staple"


class HumanApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enrollment = create_enrollment(
            operator="mohsen",
            password=PASSWORD,
            now=NOW,
            scrypt_n=2**14,
        )
        self.subject = approval_subject(
            artifact_type="three-site-staging-inventory-v3",
            artifact_sha256="a" * 64,
            release_sha="b" * 40,
            bindings={"inventory_stage": "provisioned", "campaign_id": "campaign-1"},
        )

    def issue(
        self,
        *,
        state=None,
        at: datetime = NOW,
        password: str = PASSWORD,
        code: str | None = None,
        recovery_code: str | None = None,
        subject=None,
        action: str = "approve_inventory",
        ttl: int = 600,
    ):
        if code is None and recovery_code is None:
            code = totp_code(self.enrollment.totp_secret, at=at)[1]
        return authenticate_and_issue(
            secrets_payload=self.enrollment.secrets_payload,
            state_payload=copy.deepcopy(state or self.enrollment.state_payload),
            policy_payload=self.enrollment.policy_payload,
            private_key_envelope=self.enrollment.private_key_envelope,
            password=password,
            totp=code,
            recovery_code=recovery_code,
            action=action,
            environment="staging",
            subject=copy.deepcopy(subject or self.subject),
            ttl_seconds=ttl,
            now=at,
        )

    def test_password_totp_issues_exact_action_bound_token(self):
        token, state, audit = self.issue()
        verified = verify_human_approval(
            token,
            policy_payload=self.enrollment.policy_payload,
            expected_action="approve_inventory",
            expected_environment="staging",
            expected_subject=self.subject,
            now=NOW,
        )
        self.assertEqual(verified.operator, "mohsen")
        self.assertEqual(verified.authentication_methods, ("password", "totp"))
        self.assertEqual(state["last_totp_counter"], int(NOW.timestamp()) // 30)
        self.assertEqual(state["issued_sequence"], 1)
        self.assertEqual(audit["approval_id"], token["approval_id"])
        self.assertNotIn(PASSWORD, str(token))
        self.assertNotIn(totp_code(self.enrollment.totp_secret, at=NOW)[1], str(token))
        self.assertNotIn(self.enrollment.totp_secret, str(token))

    def test_token_rejects_subject_action_environment_policy_and_signature_drift(self):
        token, _state, _audit = self.issue()
        cases = []
        changed_subject = copy.deepcopy(self.subject)
        changed_subject["artifact_sha256"] = "c" * 64
        cases.append((token, "approve_inventory", "staging", changed_subject))
        cases.append((token, "approve_migration", "staging", self.subject))
        cases.append((token, "approve_inventory", "production", self.subject))
        tampered = copy.deepcopy(token)
        tampered["subject"]["bindings"]["campaign_id"] = "other"
        cases.append((tampered, "approve_inventory", "staging", tampered["subject"]))
        wrong_policy = copy.deepcopy(self.enrollment.policy_payload)
        wrong_policy["issuer"]["operator"] = "attacker"
        with self.assertRaises(HumanApprovalError):
            verify_human_approval(
                token,
                policy_payload=wrong_policy,
                expected_action="approve_inventory",
                expected_environment="staging",
                expected_subject=self.subject,
                now=NOW,
            )
        for value, action, environment, subject in cases:
            with self.subTest(action=action, environment=environment, subject=subject):
                with self.assertRaises(HumanApprovalError):
                    verify_human_approval(
                        value,
                        policy_payload=self.enrollment.policy_payload,
                        expected_action=action,
                        expected_environment=environment,
                        expected_subject=subject,
                        now=NOW,
                    )

    def test_totp_is_single_use_and_accepts_only_narrow_clock_drift(self):
        code = totp_code(self.enrollment.totp_secret, at=NOW)[1]
        _token, state, _audit = self.issue(code=code)
        replay_token, replay_state, replay_audit = self.issue(state=state, code=code)
        self.assertEqual(replay_token, {})
        self.assertEqual(replay_state["failed_attempts"], 1)
        self.assertEqual(replay_audit["event"], "human_approval_authentication_failed")
        previous_code = totp_code(
            self.enrollment.totp_secret, at=NOW - timedelta(seconds=30)
        )[1]
        self.assertIsNotNone(
            match_totp(self.enrollment.totp_secret, previous_code, at=NOW, last_counter=-1)
        )
        stale_code = totp_code(
            self.enrollment.totp_secret, at=NOW - timedelta(seconds=60)
        )[1]
        self.assertIsNone(
            match_totp(self.enrollment.totp_secret, stale_code, at=NOW, last_counter=-1)
        )

    def test_wrong_credentials_are_generic_rate_limited_and_never_issue(self):
        state = self.enrollment.state_payload
        for attempt in range(1, 6):
            token, state, audit = self.issue(
                state=state,
                password="wrong-password-value",
                code="000000",
                at=NOW,
            )
            self.assertEqual(token, {})
            self.assertEqual(audit["event"], "human_approval_authentication_failed")
            self.assertEqual(state["failed_attempts"], attempt)
        self.assertIsNotNone(state["locked_until"])
        with self.assertRaises(HumanApprovalIssuerError):
            self.issue(state=state, at=NOW + timedelta(seconds=1))

    def test_recovery_code_requires_password_and_is_single_use(self):
        recovery = self.enrollment.recovery_codes[0]
        token, state, _audit = self.issue(code=None, recovery_code=recovery)
        verified = verify_human_approval(
            token,
            policy_payload=self.enrollment.policy_payload,
            expected_action="approve_inventory",
            expected_environment="staging",
            expected_subject=self.subject,
            now=NOW,
        )
        self.assertEqual(verified.authentication_methods, ("password", "recovery_code"))
        replay, state, _audit = self.issue(
            state=state, code=None, recovery_code=recovery, at=NOW + timedelta(seconds=1)
        )
        self.assertEqual(replay, {})
        wrong_password, _state, _audit = self.issue(
            code=None,
            recovery_code=self.enrollment.recovery_codes[1],
            password="wrong-password-value",
        )
        self.assertEqual(wrong_password, {})

        malformed, malformed_state, malformed_audit = self.issue(
            code=None,
            recovery_code="GT-invalid-unicode-é",
        )
        self.assertEqual(malformed, {})
        self.assertEqual(malformed_state["failed_attempts"], 1)
        self.assertEqual(
            malformed_audit["event"], "human_approval_authentication_failed"
        )

    def test_start_window_expires_but_historical_signature_remains_verifiable(self):
        token, _state, _audit = self.issue(ttl=60)
        later = NOW + timedelta(seconds=61)
        with self.assertRaises(HumanApprovalError):
            verify_human_approval(
                token,
                policy_payload=self.enrollment.policy_payload,
                expected_action="approve_inventory",
                expected_environment="staging",
                expected_subject=self.subject,
                now=later,
            )
        verified = verify_human_approval(
            token,
            policy_payload=self.enrollment.policy_payload,
            expected_action="approve_inventory",
            expected_environment="staging",
            expected_subject=self.subject,
            now=later,
            require_fresh=False,
        )
        self.assertEqual(verified.approval_id, token["approval_id"])

    def test_action_policy_rejects_excessive_lifetime_and_unauthorized_environment(self):
        with self.assertRaises(HumanApprovalIssuerError):
            self.issue(ttl=86401)
        with self.assertRaises(HumanApprovalIssuerError):
            authenticate_and_issue(
                secrets_payload=self.enrollment.secrets_payload,
                state_payload=self.enrollment.state_payload,
                policy_payload=self.enrollment.policy_payload,
                private_key_envelope=self.enrollment.private_key_envelope,
                password=PASSWORD,
                totp=totp_code(self.enrollment.totp_secret, at=NOW)[1],
                recovery_code=None,
                action="approve_inventory",
                environment="production",
                subject=self.subject,
                ttl_seconds=600,
                now=NOW,
            )

    def test_release_bound_staging_session_authorizes_multiple_actions_for_48_hours(self):
        actions = [
            "approve_inventory",
            "approve_migration",
            "start_full_matrix",
        ]
        token, state, audit = authenticate_and_issue_session(
            secrets_payload=self.enrollment.secrets_payload,
            state_payload=copy.deepcopy(self.enrollment.state_payload),
            policy_payload=self.enrollment.policy_payload,
            private_key_envelope=self.enrollment.private_key_envelope,
            password=PASSWORD,
            totp=totp_code(self.enrollment.totp_secret, at=NOW)[1],
            recovery_code=None,
            release_sha="b" * 40,
            allowed_actions=actions,
            ttl_seconds=48 * 60 * 60,
            now=NOW,
        )
        self.assertEqual(token["allowed_actions"], sorted(actions))
        self.assertEqual(state["issued_sequence"], 1)
        self.assertEqual(audit["action"], "authorize_staging_session")
        for action in actions:
            verified = verify_human_approval(
                token,
                policy_payload=self.enrollment.policy_payload,
                expected_action=action,
                expected_environment="staging",
                expected_subject=self.subject,
                now=NOW + timedelta(hours=47, minutes=59),
            )
            self.assertEqual(verified.action, action)
            self.assertEqual(verified.subject, self.subject)

    def test_staging_session_rejects_production_other_release_action_expiry_and_tampering(self):
        token, _state, _audit = authenticate_and_issue_session(
            secrets_payload=self.enrollment.secrets_payload,
            state_payload=copy.deepcopy(self.enrollment.state_payload),
            policy_payload=self.enrollment.policy_payload,
            private_key_envelope=self.enrollment.private_key_envelope,
            password=PASSWORD,
            totp=totp_code(self.enrollment.totp_secret, at=NOW)[1],
            recovery_code=None,
            release_sha="b" * 40,
            allowed_actions=["approve_inventory", "start_full_matrix"],
            ttl_seconds=48 * 60 * 60,
            now=NOW,
        )
        other_release = copy.deepcopy(self.subject)
        other_release["release_sha"] = "c" * 40
        cases = (
            ("approve_inventory", "production", self.subject, NOW),
            ("approve_migration", "staging", self.subject, NOW),
            ("approve_inventory", "staging", other_release, NOW),
            (
                "approve_inventory",
                "staging",
                self.subject,
                NOW + timedelta(hours=48),
            ),
        )
        for action, environment, subject, at in cases:
            with self.subTest(action=action, environment=environment, at=at):
                with self.assertRaises(HumanApprovalError):
                    verify_human_approval(
                        token,
                        policy_payload=self.enrollment.policy_payload,
                        expected_action=action,
                        expected_environment=environment,
                        expected_subject=subject,
                        now=at,
                    )
        tampered = copy.deepcopy(token)
        tampered["allowed_actions"].append("approve_migration")
        tampered["allowed_actions"].sort()
        with self.assertRaisesRegex(HumanApprovalError, "signature"):
            verify_human_approval(
                tampered,
                policy_payload=self.enrollment.policy_payload,
                expected_action="approve_migration",
                expected_environment="staging",
                expected_subject=self.subject,
                now=NOW,
            )

    def test_staging_session_rejects_more_than_48_hours(self):
        with self.assertRaisesRegex(HumanApprovalIssuerError, "48 hours"):
            authenticate_and_issue_session(
                secrets_payload=self.enrollment.secrets_payload,
                state_payload=copy.deepcopy(self.enrollment.state_payload),
                policy_payload=self.enrollment.policy_payload,
                private_key_envelope=self.enrollment.private_key_envelope,
                password=PASSWORD,
                totp=totp_code(self.enrollment.totp_secret, at=NOW)[1],
                recovery_code=None,
                release_sha="b" * 40,
                allowed_actions=["approve_inventory"],
                ttl_seconds=(48 * 60 * 60) + 1,
                now=NOW,
            )

    def test_enrollment_files_are_owner_only_and_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "issuer"
            write_enrollment(directory, self.enrollment)
            for name, path in issuer_paths(directory).items():
                if name == "lock":
                    continue
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            encrypted_key = secure_json(
                issuer_paths(directory)["key"], label="encrypted issuer key"
            )
            self.assertEqual(
                encrypted_key["schema"],
                "three-site-human-approval-private-key-envelope-v1",
            )
            private_key_raw = decrypt_private_key(
                encrypted_key,
                password=PASSWORD,
                policy_payload=self.enrollment.policy_payload,
            )
            self.assertEqual(len(private_key_raw), 32)
            self.assertNotEqual(
                encrypted_key["cipher"]["ciphertext"],
                base64.b64encode(private_key_raw).decode(),
            )
            with self.assertRaisesRegex(HumanApprovalIssuerError, "cannot be decrypted"):
                decrypt_private_key(
                    encrypted_key,
                    password="wrong password with enough length",
                    policy_payload=self.enrollment.policy_payload,
                )
            tampered_envelope = copy.deepcopy(encrypted_key)
            ciphertext = bytearray(
                base64.b64decode(tampered_envelope["cipher"]["ciphertext"])
            )
            ciphertext[0] ^= 1
            tampered_envelope["cipher"]["ciphertext"] = base64.b64encode(
                bytes(ciphertext)
            ).decode()
            with self.assertRaisesRegex(HumanApprovalIssuerError, "cannot be decrypted"):
                decrypt_private_key(
                    tampered_envelope,
                    password=PASSWORD,
                    policy_payload=self.enrollment.policy_payload,
                )
            self.assertEqual(secure_json(issuer_paths(directory)["policy"], label="policy"), self.enrollment.policy_payload)
            records = verify_hash_chained_jsonl(issuer_paths(directory)["audit"])
            self.assertEqual(records[-1]["event"], "human_approval_enrolled")
            verified_receipt = verify_bootstrap_receipt(
                secure_json(issuer_paths(directory)["receipt"], label="receipt"),
                policy_payload=self.enrollment.policy_payload,
            )
            self.assertEqual(verified_receipt["legacy_human_approval_status"], "superseded")
            verify_issuer_audit(
                records,
                bootstrap_receipt=verified_receipt,
                policy_payload=self.enrollment.policy_payload,
                state_payload=self.enrollment.state_payload,
            )
            with self.assertRaisesRegex(HumanApprovalIssuerError, "audit is empty"):
                verify_issuer_audit(
                    [],
                    bootstrap_receipt=verified_receipt,
                    policy_payload=self.enrollment.policy_payload,
                    state_payload=self.enrollment.state_payload,
                )
            _token, issued_state, issued_event = self.issue()
            append_hash_chained_jsonl(
                issuer_paths(directory)["audit"], issued_event
            )
            issued_records = verify_hash_chained_jsonl(
                issuer_paths(directory)["audit"]
            )
            verify_issuer_audit(
                issued_records,
                bootstrap_receipt=verified_receipt,
                policy_payload=self.enrollment.policy_payload,
                state_payload=issued_state,
            )
            inconsistent_state = copy.deepcopy(issued_state)
            inconsistent_state["issued_sequence"] = 0
            with self.assertRaisesRegex(HumanApprovalIssuerError, "durable audit"):
                verify_issuer_audit(
                    issued_records,
                    bootstrap_receipt=verified_receipt,
                    policy_payload=self.enrollment.policy_payload,
                    state_payload=inconsistent_state,
                )
            tampered = copy.deepcopy(verified_receipt)
            tampered["operator"] = "attacker"
            with self.assertRaises(HumanApprovalIssuerError):
                verify_bootstrap_receipt(
                    tampered, policy_payload=self.enrollment.policy_payload
                )
            with self.assertRaises(HumanApprovalIssuerError):
                write_enrollment(directory, self.enrollment)

    def test_enrollment_rejects_relative_missing_or_world_writable_parent(self):
        with self.assertRaisesRegex(HumanApprovalIssuerError, "absolute"):
            write_enrollment(Path("relative-issuer"), self.enrollment)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(HumanApprovalIssuerError, "must already exist"):
                write_enrollment(root / "missing" / "issuer", self.enrollment)
            root.chmod(0o777)
            with self.assertRaisesRegex(HumanApprovalIssuerError, "owner-controlled"):
                write_enrollment(root / "issuer", self.enrollment)

    def test_enrollment_rejects_naive_time_and_unbounded_scrypt_work_factor(self):
        with self.assertRaisesRegex(HumanApprovalIssuerError, "timezone"):
            create_enrollment(
                operator="mohsen",
                password=PASSWORD,
                now=datetime(2026, 7, 22, 12, 0, 0),
                scrypt_n=2**14,
            )
        with self.assertRaisesRegex(HumanApprovalIssuerError, "work factor"):
            create_enrollment(
                operator="mohsen",
                password=PASSWORD,
                now=NOW,
                scrypt_n=2**18,
            )

        with self.assertRaisesRegex(HumanApprovalIssuerError, "TOTP time"):
            totp_code(
                self.enrollment.totp_secret,
                at=datetime(2026, 7, 22, 12, 0, 0),
            )

    def test_verification_rejects_naive_time_and_noncanonical_subject_shape(self):
        token, _state, _audit = self.issue()
        with self.assertRaisesRegex(HumanApprovalError, "timezone"):
            verify_human_approval(
                token,
                policy_payload=self.enrollment.policy_payload,
                expected_action="approve_inventory",
                expected_environment="staging",
                expected_subject=self.subject,
                now=datetime(2026, 7, 22, 12, 0, 0),
            )
        malformed = {**self.subject, "unexpected": "field"}
        with self.assertRaisesRegex(HumanApprovalIssuerError, "subject"):
            self.issue(subject=malformed)

    def test_secure_new_file_publish_never_overwrites_existing_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            token = root / "approval.json"
            write_secure_new_bytes(token, b"first\n", label="approval token")
            self.assertEqual(token.read_bytes(), b"first\n")
            self.assertEqual(token.stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(SecureFileError, "already exists"):
                write_secure_new_bytes(token, b"second\n", label="approval token")
            self.assertEqual(token.read_bytes(), b"first\n")


if __name__ == "__main__":
    unittest.main()
