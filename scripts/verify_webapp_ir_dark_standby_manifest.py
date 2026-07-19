#!/usr/bin/env python3
"""Fail-closed validation for the WebApp-IR dark-standby manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TRUE_VALUES = {"1", "true", "yes", "on"}

REQUIRED_VALUES = {
    "DARK_STANDBY_MODE": "1",
    "SERVER_TIMEZONE": "UTC",
    "USER_DISPLAY_TIMEZONE": "Asia/Tehran",
    "OBJECT_STORAGE_PROVIDER": "arvan",
    "OBJECT_STORAGE_BUCKET": "production-sync-coin",
    "PAYLOAD_ENCRYPTION": "age-x25519",
    "BACKGROUND_JOBS_ENABLED": "false",
    "START_APP_SERVICE": "false",
    "START_SYNC_WORKER": "false",
    "RESTORE_REDIS": "false",
    "ALLOW_PUBLIC_INGRESS": "false",
    "ALLOW_CDN_MUTATION": "false",
    "ALLOW_DNS_MUTATION": "false",
    "ALLOW_WRITER_PROMOTION": "false",
    "WRITER_WITNESS_REQUIRED": "false",
    "WRITER_WITNESS_AUTO_RENEW_ENABLED": "false",
}

REQUIRED_KEYS = (
    "SOURCE_PROJECT_DIR", "SOURCE_RUNTIME_ENV", "WEBAPP_FI_HOST",
    "WEBAPP_FI_SSH_USER", "WEBAPP_FI_SSH_PORT", "WEBAPP_FI_SSH_KEY",
    "WEBAPP_FI_PROJECT_DIR", "WA_IR_HOST", "WA_IR_SSH_USER",
    "WA_IR_SSH_PORT", "WA_IR_SSH_KEY", "WA_IR_PROJECT_DIR",
    "WA_IR_DEPLOY_BASE_DIR", "OBJECT_STORAGE_ENDPOINT",
    "OBJECT_STORAGE_REGION", "OBJECT_STORAGE_PREFIX",
    "OBJECT_STORAGE_CREDENTIAL_FILE", "OBJECT_URL_TTL_SECONDS",
    "AGE_IDENTITY_FILE", "AGE_RECIPIENT_FILE", "LOCAL_ARTIFACT_DIR",
    "REMOTE_BACKUP_DIR",
)

SECRET_PATH_KEYS = (
    "SOURCE_RUNTIME_ENV", "OBJECT_STORAGE_CREDENTIAL_FILE", "AGE_IDENTITY_FILE",
)


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"line {number} is not KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in values:
            raise ValueError(f"line {number} has an empty or duplicate key")
        values[key] = value.strip().strip('"').strip("'")
    return values


def is_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in TRUE_VALUES


def inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO_ROOT.resolve())
        return True
    except ValueError:
        return False


def validate(values: dict[str, str], *, check_files: bool) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    for key in REQUIRED_KEYS:
        if not values.get(key):
            failures.append(f"missing required value: {key}")
    for key, expected in REQUIRED_VALUES.items():
        actual = values.get(key, "")
        if actual.lower() != expected.lower():
            failures.append(f"{key} must be {expected!r}, got {actual!r}")

    release_sha = values.get("SOURCE_RELEASE_SHA", "")
    if not SHA_RE.fullmatch(release_sha):
        failures.append("SOURCE_RELEASE_SHA must be an exact 40-character lowercase Git SHA")
    if values.get("WEBAPP_FI_HOST") == values.get("WA_IR_HOST"):
        failures.append("WEBAPP_FI_HOST and WA_IR_HOST must be different physical hosts")
    endpoint = values.get("OBJECT_STORAGE_ENDPOINT", "")
    if endpoint and not endpoint.startswith("https://"):
        failures.append("OBJECT_STORAGE_ENDPOINT must use HTTPS")
    try:
        ttl = int(values.get("OBJECT_URL_TTL_SECONDS", "0"))
    except ValueError:
        ttl = 0
    if not 60 <= ttl <= 900:
        failures.append("OBJECT_URL_TTL_SECONDS must be between 60 and 900")

    for key in (
        "ALLOW_PUBLIC_INGRESS", "ALLOW_CDN_MUTATION", "ALLOW_DNS_MUTATION",
        "ALLOW_WRITER_PROMOTION", "START_APP_SERVICE", "START_SYNC_WORKER",
        "RESTORE_REDIS", "BACKGROUND_JOBS_ENABLED",
    ):
        if is_true(values.get(key)):
            failures.append(f"dark standby forbids {key}=true")

    for key in SECRET_PATH_KEYS:
        raw = values.get(key)
        if not raw:
            continue
        path = Path(raw)
        if not path.is_absolute():
            failures.append(f"{key} must be an absolute path")
        if inside_repo(path) and "/tmp/" not in str(path):
            failures.append(f"{key} must not be stored in the tracked repository")
        if check_files:
            if not path.is_file():
                failures.append(f"{key} does not exist: {path}")
            else:
                mode = stat.S_IMODE(path.stat().st_mode)
                if mode & 0o077:
                    failures.append(f"{key} must not be group/world accessible (mode={mode:o})")

    for key in ("WEBAPP_FI_SSH_KEY", "WA_IR_SSH_KEY", "AGE_RECIPIENT_FILE"):
        raw = values.get(key)
        if check_files and raw and not Path(raw).is_file():
            failures.append(f"{key} does not exist: {raw}")

    source = values.get("SOURCE_PROJECT_DIR")
    if check_files and source:
        source_path = Path(source)
        if not (source_path / ".git").exists():
            failures.append("SOURCE_PROJECT_DIR is not a Git worktree")
        else:
            result = subprocess.run(
                ["git", "-C", str(source_path), "rev-parse", "HEAD"],
                text=True, capture_output=True, check=False,
            )
            head = result.stdout.strip() if result.returncode == 0 else ""
            if head != release_sha:
                failures.append(
                    f"SOURCE_PROJECT_DIR HEAD {head!r} does not match SOURCE_RELEASE_SHA"
                )

    if values.get("WRITER_WITNESS_REQUIRED", "").lower() == "false":
        warnings.append("writer witness is disabled; this host must remain dark and non-writer")
    return failures, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--check-files", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    path = Path(args.manifest)
    if not path.is_file():
        raise SystemExit(f"manifest does not exist: {path}")
    values: dict[str, str] = {}
    try:
        values = parse_env(path)
        failures, warnings = validate(values, check_files=args.check_files)
    except Exception as exc:
        failures, warnings = [str(exc)], []
    payload = {
        "status": "passed" if not failures else "failed",
        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "release_sha": values.get("SOURCE_RELEASE_SHA"),
        "source_host": values.get("WEBAPP_FI_HOST"),
        "target_host": values.get("WA_IR_HOST"),
        "bucket": values.get("OBJECT_STORAGE_BUCKET"),
        "failures": failures,
        "warnings": warnings,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"dark-standby manifest: {payload['status']}")
        for item in failures:
            print(f"FAIL: {item}")
        for item in warnings:
            print(f"WARN: {item}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
