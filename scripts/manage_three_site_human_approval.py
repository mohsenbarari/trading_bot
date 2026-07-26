#!/usr/bin/env python3
"""Enroll or use the isolated password + TOTP human approval issuer."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.canonical_json import canonical_json_bytes
from core.human_approval import (
    SESSION_TOKEN_SCHEMA,
    approval_subject,
    verify_human_approval,
)
from core.human_approval_issuer import (
    DEFAULT_SESSION_TTL_SECONDS,
    DEFAULT_STAGING_SESSION_ACTIONS,
    DEFAULT_TOKEN_TTL_SECONDS,
    HumanApprovalIssuerError,
    authenticate_and_issue,
    authenticate_and_issue_session,
    create_enrollment,
    issuer_paths,
    match_totp,
    secure_json,
    verify_bootstrap_receipt,
    verify_issuer_audit,
    write_enrollment,
)
from core.secure_file_io import (
    append_hash_chained_jsonl,
    verify_hash_chained_jsonl,
    write_secure_atomic_bytes,
    write_secure_new_bytes,
)


DEFAULT_DIRECTORY = Path("/etc/trading-bot/security/human-approval")
SUPERSESSION_PHRASE = "REPLACE LEGACY TWO DEVICE APPROVAL"


def _assert_synchronized_clock() -> None:
    """Fail closed when the host cannot prove that its clock is synchronized."""

    try:
        result = subprocess.run(
            ["timedatectl", "show", "--property=NTPSynchronized", "--value"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HumanApprovalIssuerError(
            "cannot prove host clock synchronization; repair NTP before approval work"
        ) from exc
    if result.returncode != 0 or result.stdout.strip().lower() != "yes":
        raise HumanApprovalIssuerError(
            "host clock is not NTP-synchronized; approval work is blocked"
        )


def _assert_secure_issuer_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError as exc:
        raise HumanApprovalIssuerError("approval issuer directory cannot be opened safely") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise HumanApprovalIssuerError(
                "approval issuer directory must be a real owner-only 0700 directory"
            )
    finally:
        os.close(descriptor)


def _enroll(args: argparse.Namespace) -> dict:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise HumanApprovalIssuerError("enrollment requires an interactive TTY")
    _assert_synchronized_clock()
    acknowledgement = input(
        f"Type {SUPERSESSION_PHRASE!r} to supersede legacy human approvals: "
    )
    if acknowledgement != SUPERSESSION_PHRASE:
        raise HumanApprovalIssuerError("legacy approval supersession was not explicitly confirmed")
    password = getpass.getpass("New fixed approval passphrase: ")
    confirmation = getpass.getpass("Repeat approval passphrase: ")
    if password != confirmation:
        raise HumanApprovalIssuerError("approval passphrases do not match")
    now = datetime.now(timezone.utc)
    artifacts = create_enrollment(operator=args.operator, password=password, now=now)
    print("\nAdd this account to Google Authenticator, Aegis, or 2FAS.", file=sys.stderr)
    print(f"Manual setup key: {artifacts.totp_secret}", file=sys.stderr)
    print(f"Authenticator URI: {artifacts.otpauth_uri}", file=sys.stderr)
    first_code = getpass.getpass("Enter the current 6-digit code to confirm enrollment: ")
    if match_totp(
        artifacts.totp_secret,
        first_code,
        at=datetime.now(timezone.utc),
        last_counter=-1,
    ) is None:
        raise HumanApprovalIssuerError("Authenticator confirmation code is invalid")
    print("\nStore these one-use recovery codes offline. They will not be shown again:", file=sys.stderr)
    for code in artifacts.recovery_codes:
        print(code, file=sys.stderr)
    recovery_ack = input("Type 'RECOVERY CODES SAVED' after storing them securely: ")
    if recovery_ack != "RECOVERY CODES SAVED":
        raise HumanApprovalIssuerError("recovery-code custody was not confirmed")
    write_enrollment(args.directory, artifacts)
    return {
        "status": "enrolled",
        "directory": str(args.directory),
        "policy": str(issuer_paths(args.directory)["policy"]),
        "bootstrap_receipt": str(issuer_paths(args.directory)["receipt"]),
        "legacy_human_approval_status": "superseded",
    }


def _secure_lock(path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        os.close(descriptor)
        raise HumanApprovalIssuerError("approval issuer lock file is unsafe")
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    return descriptor


def _issue(args: argparse.Namespace) -> dict:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise HumanApprovalIssuerError("approval issuance requires an interactive TTY")
    _assert_synchronized_clock()
    _assert_secure_issuer_directory(args.directory)
    paths = issuer_paths(args.directory)
    issuer_root = args.directory.resolve(strict=True)
    output = args.output.resolve(strict=False)
    try:
        output.relative_to(issuer_root)
    except ValueError:
        pass
    else:
        raise HumanApprovalIssuerError(
            "approval token output must be outside the issuer secret directory"
        )
    if output.exists() or output.is_symlink():
        raise HumanApprovalIssuerError(
            "approval token output already exists; use a new owner-controlled path"
        )
    subject = secure_json(args.subject, label="approval subject")
    subject_hash = hashlib.sha256(canonical_json_bytes(subject)).hexdigest()
    print(
        json.dumps(
            {
                "action": args.action,
                "environment": args.environment,
                "subject": subject,
                "subject_sha256": subject_hash,
                "ttl_seconds": args.ttl_seconds,
            },
            sort_keys=True,
            indent=2,
        ),
        file=sys.stderr,
    )
    confirmation = f"APPROVE {args.action} {args.environment} {subject_hash[:12]}"
    if input(f"Type {confirmation!r} to continue: ") != confirmation:
        raise HumanApprovalIssuerError("exact approval intent was not confirmed")
    password = getpass.getpass("Approval passphrase: ")
    if args.use_recovery_code:
        totp = None
        recovery_code = getpass.getpass("One-use recovery code: ")
    else:
        totp = getpass.getpass("Authenticator code: ")
        recovery_code = None
    descriptor = _secure_lock(paths["lock"])
    try:
        policy = secure_json(paths["policy"], label="human approval policy")
        receipt = secure_json(paths["receipt"], label="approval bootstrap receipt")
        verify_bootstrap_receipt(receipt, policy_payload=policy)
        state = secure_json(paths["state"], label="human approval state")
        audit_records = verify_hash_chained_jsonl(
            paths["audit"], label="human approval audit log"
        )
        verify_issuer_audit(
            audit_records,
            bootstrap_receipt=receipt,
            policy_payload=policy,
            state_payload=state,
        )
        secrets_payload = secure_json(paths["secrets"], label="human approval secrets")
        private_key_envelope = secure_json(
            paths["key"], label="encrypted human approval issuer private key"
        )
        token, new_state, audit = authenticate_and_issue(
            secrets_payload=secrets_payload,
            state_payload=state,
            policy_payload=policy,
            private_key_envelope=private_key_envelope,
            password=password,
            totp=totp,
            recovery_code=recovery_code,
            action=args.action,
            environment=args.environment,
            subject=subject,
            ttl_seconds=args.ttl_seconds,
            now=datetime.now(timezone.utc),
        )
        write_secure_atomic_bytes(
            paths["state"],
            (json.dumps(new_state, sort_keys=True, indent=2) + "\n").encode(),
            label="human approval state",
            mode=0o600,
        )
        append_hash_chained_jsonl(paths["audit"], audit)
        if not token:
            raise HumanApprovalIssuerError("password or possession factor is invalid")
        verify_human_approval(
            token,
            policy_payload=policy,
            expected_action=args.action,
            expected_environment=args.environment,
            expected_subject=subject,
        )
        write_secure_new_bytes(
            output,
            (json.dumps(token, sort_keys=True, indent=2) + "\n").encode(),
            label="human approval token",
            mode=0o600,
        )
        return {
            "status": "approved",
            "approval_id": token["approval_id"],
            "action": token["action"],
            "environment": token["environment"],
            "expires_at": token["expires_at"],
            "output": str(output),
        }
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _verify(args: argparse.Namespace) -> dict:
    policy = secure_json(args.policy, label="human approval policy")
    token = secure_json(args.token, label="human approval token")
    subject = secure_json(args.subject, label="approval subject")
    result = verify_human_approval(
        token,
        policy_payload=policy,
        expected_action=args.action,
        expected_environment=args.environment,
        expected_subject=subject,
        require_fresh=not args.historical,
    )
    return {
        "status": "approved",
        "approval_id": result.approval_id,
        "operator": result.operator,
        "action": result.action,
        "environment": result.environment,
        "expires_at": result.expires_at.isoformat(),
        "token_sha256": result.token_hash,
    }


def _issue_session(args: argparse.Namespace) -> dict:
    """Authenticate once and create a release-bound 48-hour staging session."""

    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise HumanApprovalIssuerError("session issuance requires an interactive TTY")
    _assert_synchronized_clock()
    _assert_secure_issuer_directory(args.directory)
    paths = issuer_paths(args.directory)
    issuer_root = args.directory.resolve(strict=True)
    output = args.output.resolve(strict=False)
    try:
        output.relative_to(issuer_root)
    except ValueError:
        pass
    else:
        raise HumanApprovalIssuerError(
            "approval session output must be outside the issuer secret directory"
        )
    if output.exists() or output.is_symlink():
        raise HumanApprovalIssuerError(
            "approval session output already exists; use a new owner-controlled path"
        )

    actions = sorted(set(args.actions))
    hours = args.ttl_seconds / 3600
    print(
        json.dumps(
            {
                "authorization": "staging operations session",
                "environment": "staging",
                "release_sha": args.release_sha.lower(),
                "allowed_actions": actions,
                "ttl_seconds": args.ttl_seconds,
                "ttl_hours": hours,
                "production_authorized": False,
            },
            sort_keys=True,
            indent=2,
        ),
        file=sys.stderr,
    )
    confirmation = (
        f"AUTHORIZE STAGING {args.release_sha.lower()[:12]} "
        f"{args.ttl_seconds // 3600}H"
    )
    if input(f"Type {confirmation!r} to continue: ") != confirmation:
        raise HumanApprovalIssuerError("staging session intent was not confirmed")
    password = getpass.getpass("Approval passphrase: ")
    if args.use_recovery_code:
        totp = None
        recovery_code = getpass.getpass("One-use recovery code: ")
    else:
        totp = getpass.getpass("Authenticator code: ")
        recovery_code = None

    descriptor = _secure_lock(paths["lock"])
    try:
        policy = secure_json(paths["policy"], label="human approval policy")
        receipt = secure_json(paths["receipt"], label="approval bootstrap receipt")
        verify_bootstrap_receipt(receipt, policy_payload=policy)
        state = secure_json(paths["state"], label="human approval state")
        audit_records = verify_hash_chained_jsonl(
            paths["audit"], label="human approval audit log"
        )
        verify_issuer_audit(
            audit_records,
            bootstrap_receipt=receipt,
            policy_payload=policy,
            state_payload=state,
        )
        secrets_payload = secure_json(paths["secrets"], label="human approval secrets")
        private_key_envelope = secure_json(
            paths["key"], label="encrypted human approval issuer private key"
        )
        token, new_state, audit = authenticate_and_issue_session(
            secrets_payload=secrets_payload,
            state_payload=state,
            policy_payload=policy,
            private_key_envelope=private_key_envelope,
            password=password,
            totp=totp,
            recovery_code=recovery_code,
            release_sha=args.release_sha,
            allowed_actions=actions,
            ttl_seconds=args.ttl_seconds,
            now=datetime.now(timezone.utc),
        )
        write_secure_atomic_bytes(
            paths["state"],
            (json.dumps(new_state, sort_keys=True, indent=2) + "\n").encode(),
            label="human approval state",
            mode=0o600,
        )
        append_hash_chained_jsonl(paths["audit"], audit)
        if not token:
            raise HumanApprovalIssuerError("password or possession factor is invalid")
        probe_subject = approval_subject(
            artifact_type=SESSION_TOKEN_SCHEMA,
            artifact_sha256=hashlib.sha256(
                canonical_json_bytes(
                    {
                        "release_sha": args.release_sha.lower(),
                        "allowed_actions": actions,
                    }
                )
            ).hexdigest(),
            release_sha=args.release_sha,
            bindings={},
        )
        verify_human_approval(
            token,
            policy_payload=policy,
            expected_action=actions[0],
            expected_environment="staging",
            expected_subject=probe_subject,
        )
        write_secure_new_bytes(
            output,
            (json.dumps(token, sort_keys=True, indent=2) + "\n").encode(),
            label="human approval session",
            mode=0o600,
        )
        return {
            "status": "session-authorized",
            "approval_id": token["approval_id"],
            "environment": token["environment"],
            "release_sha": token["release_sha"],
            "allowed_actions": token["allowed_actions"],
            "expires_at": token["expires_at"],
            "output": str(output),
        }
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    enroll = subparsers.add_parser("enroll")
    enroll.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    enroll.add_argument("--operator", required=True)
    enroll.set_defaults(handler=_enroll)

    issue = subparsers.add_parser("issue")
    issue.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    issue.add_argument("--action", required=True)
    issue.add_argument("--environment", choices=("staging", "production"), required=True)
    issue.add_argument("--subject", type=Path, required=True)
    issue.add_argument("--ttl-seconds", type=int, default=DEFAULT_TOKEN_TTL_SECONDS)
    issue.add_argument("--use-recovery-code", action="store_true")
    issue.add_argument("--output", type=Path, required=True)
    issue.set_defaults(handler=_issue)

    issue_session = subparsers.add_parser("issue-session")
    issue_session.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    issue_session.add_argument("--release-sha", required=True)
    issue_session.add_argument(
        "--actions",
        nargs="+",
        default=list(DEFAULT_STAGING_SESSION_ACTIONS),
    )
    issue_session.add_argument(
        "--ttl-seconds",
        type=int,
        default=DEFAULT_SESSION_TTL_SECONDS,
    )
    issue_session.add_argument("--use-recovery-code", action="store_true")
    issue_session.add_argument("--output", type=Path, required=True)
    issue_session.set_defaults(handler=_issue_session)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--policy", type=Path, required=True)
    verify.add_argument("--token", type=Path, required=True)
    verify.add_argument("--action", required=True)
    verify.add_argument("--environment", choices=("staging", "production"), required=True)
    verify.add_argument("--subject", type=Path, required=True)
    verify.add_argument("--historical", action="store_true")
    verify.set_defaults(handler=_verify)
    args = parser.parse_args(argv)
    try:
        result = args.handler(args)
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
