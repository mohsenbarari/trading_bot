#!/usr/bin/env python3
"""Fail-closed validation for the WebApp-IR dark-standby manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from core.secure_file_io import SecureFileError, read_secure_bytes, read_secure_text, sha256_secure_file


REPO_ROOT = Path(__file__).resolve().parents[1]
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
TRUE_VALUES = {"1", "true", "yes", "on"}
EXPECTED_TOPOLOGY = {
    "BOT_FI_HOST": "65.109.216.187",
    "WEBAPP_FI_HOST": "65.109.220.59",
    "WA_IR_HOST": "95.38.164.29",
    "WRITER_WITNESS_HOST": "185.206.95.94",
    "TRANSITIONAL_WRITER_WITNESS_HOST": "185.231.182.6",
}
GIT_EXECUTABLE = "/usr/bin/git"
GIT_ENVIRONMENT = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
}

REQUIRED_VALUES = {
    "DARK_STANDBY_MODE": "1",
    "TOPOLOGY_SCHEMA_VERSION": "three-site-dr-v1",
    "SERVER_TIMEZONE": "UTC",
    "USER_DISPLAY_TIMEZONE": "Asia/Tehran",
    "PRODUCTION_ROOT_DOMAIN": "gold-trade.ir",
    "FAILOVER_TEST_ROOT_DOMAIN": "gold-trading.ir",
    "FAILOVER_TEST_PUBLIC_HOST": "app.gold-trading.ir",
    "ARVAN_CDN_CONFIGURED_ROOT_DOMAIN": "gold-trading.ir",
    "ARVAN_CDN_MUTATION_SCOPE": "test-only",
    "OBJECT_STORAGE_PROVIDER": "arvan",
    "OBJECT_STORAGE_ENDPOINT": "https://s3.ir-thr-at1.arvanstorage.ir",
    "OBJECT_STORAGE_REGION": "ir-thr-at1",
    "OBJECT_STORAGE_BUCKET": "production-sync-coin",
    "OBJECT_STORAGE_PREFIX": "dark-standby",
    "OBJECT_ACL": "private",
    "OBJECT_VERSIONING_REQUIRED": "true",
    "SOURCE_EXPECTED_BRANCH": "main",
    "SOURCE_WORKTREE_CLEAN": "true",
    "TARGET_DB_EXPECTED_STATE": "empty-or-operation-owned",
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
    "SOURCE_PROJECT_DIR", "SOURCE_RUNTIME_ENV", "BOT_FI_HOST", "WEBAPP_FI_HOST",
    "WEBAPP_FI_SSH_USER", "WEBAPP_FI_SSH_PORT", "WEBAPP_FI_SSH_KEY",
    "WEBAPP_FI_PROJECT_DIR", "WA_IR_HOST", "WA_IR_SSH_USER",
    "WA_IR_SSH_PORT", "WA_IR_SSH_KEY", "WA_IR_PROJECT_DIR",
    "WA_IR_DEPLOY_BASE_DIR", "OBJECT_STORAGE_ENDPOINT",
    "OBJECT_STORAGE_REGION", "OBJECT_STORAGE_PREFIX",
    "OBJECT_STORAGE_CREDENTIAL_FILE", "OBJECT_URL_TTL_SECONDS",
    "AGE_IDENTITY_FILE", "AGE_RECIPIENT_FILE", "LOCAL_ARTIFACT_DIR",
    "REMOTE_BACKUP_DIR", "WRITER_WITNESS_HOST", "TRANSITIONAL_WRITER_WITNESS_HOST",
    "SOURCE_TREE_SHA", "RELEASE_ARTIFACT_PATH", "RELEASE_ARTIFACT_SHA256",
    "RESTORE_OPERATION_ID", "TARGET_DB_VOLUME_NAME",
)

SECRET_PATH_KEYS = (
    "SOURCE_RUNTIME_ENV", "OBJECT_STORAGE_CREDENTIAL_FILE", "AGE_IDENTITY_FILE",
)


def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
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


def parse_env(path: Path) -> dict[str, str]:
    return parse_env_text(read_secure_text(path, label="dark-standby manifest"))


def is_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in TRUE_VALUES


def inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO_ROOT.resolve())
        return True
    except ValueError:
        return False


def run_git(source_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run a bounded, non-interactive Git inspection with no ambient config."""

    return subprocess.run(
        [
            GIT_EXECUTABLE,
            "-c", "core.fsmonitor=false",
            "-c", "core.hooksPath=/dev/null",
            "-C", str(source_path),
            *arguments,
        ],
        text=True,
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
        timeout=10,
        env=GIT_ENVIRONMENT,
    )


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
    if not SHA_RE.fullmatch(values.get("SOURCE_TREE_SHA", "")):
        failures.append("SOURCE_TREE_SHA must be an exact 40-character lowercase Git tree SHA")
    if not SHA256_RE.fullmatch(values.get("RELEASE_ARTIFACT_SHA256", "")):
        failures.append("RELEASE_ARTIFACT_SHA256 must be exactly 64 lowercase hex characters")
    if not UUID_RE.fullmatch(values.get("RESTORE_OPERATION_ID", "")):
        failures.append("RESTORE_OPERATION_ID must be a canonical UUID")
    for key, expected in EXPECTED_TOPOLOGY.items():
        if values.get(key) != expected:
            failures.append(f"{key} must match the approved topology address {expected}")
    topology_hosts = [values.get(key) for key in EXPECTED_TOPOLOGY]
    if None not in topology_hosts and len(set(topology_hosts)) != len(topology_hosts):
        failures.append("every physical topology role must use a unique host")
    if values.get("WEBAPP_FI_HOST") == values.get("WA_IR_HOST"):
        failures.append("WEBAPP_FI_HOST and WA_IR_HOST must be different physical hosts")
    production_root = values.get("PRODUCTION_ROOT_DOMAIN", "").lower().rstrip(".")
    test_root = values.get("FAILOVER_TEST_ROOT_DOMAIN", "").lower().rstrip(".")
    test_public_host = values.get("FAILOVER_TEST_PUBLIC_HOST", "").lower().rstrip(".")
    configured_cdn_root = values.get("ARVAN_CDN_CONFIGURED_ROOT_DOMAIN", "").lower().rstrip(".")
    if production_root and production_root == test_root:
        failures.append("production and failover-test root domains must be different")
    if test_root and configured_cdn_root and configured_cdn_root != test_root:
        failures.append("Arvan CDN configured root must equal the failover-test root")
    if test_root and test_public_host and not test_public_host.endswith(f".{test_root}"):
        failures.append("FAILOVER_TEST_PUBLIC_HOST must be below FAILOVER_TEST_ROOT_DOMAIN")
    if production_root and (
        configured_cdn_root == production_root
        or test_public_host == production_root
        or test_public_host.endswith(f".{production_root}")
    ):
        failures.append("dark-standby CDN scope must not include the production root domain")
    endpoint = values.get("OBJECT_STORAGE_ENDPOINT", "")
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
        if inside_repo(path):
            failures.append(f"{key} must not be stored in the tracked repository")
        if check_files:
            try:
                read_secure_bytes(path, label=key)
            except SecureFileError as exc:
                failures.append(str(exc))

    for key in ("WEBAPP_FI_SSH_KEY", "WA_IR_SSH_KEY", "AGE_RECIPIENT_FILE"):
        raw = values.get(key)
        if check_files and raw:
            try:
                read_secure_bytes(Path(raw), label=key)
            except SecureFileError as exc:
                failures.append(str(exc))

    source = values.get("SOURCE_PROJECT_DIR")
    if check_files and source:
        source_path = Path(source)
        if not (source_path / ".git").exists():
            failures.append("SOURCE_PROJECT_DIR is not a Git worktree")
        else:
            result = run_git(source_path, "rev-parse", "HEAD")
            head = result.stdout.strip() if result.returncode == 0 else ""
            if head != release_sha:
                failures.append(
                    f"SOURCE_PROJECT_DIR HEAD {head!r} does not match SOURCE_RELEASE_SHA"
                )
            branch_result = run_git(source_path, "symbolic-ref", "--short", "HEAD")
            branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""
            if branch != values.get("SOURCE_EXPECTED_BRANCH"):
                failures.append(
                    f"SOURCE_PROJECT_DIR branch {branch!r} does not match SOURCE_EXPECTED_BRANCH"
                )
            status_result = run_git(
                source_path, "status", "--porcelain=v1", "--untracked-files=all"
            )
            if status_result.returncode != 0 or status_result.stdout:
                failures.append("SOURCE_PROJECT_DIR worktree must be completely clean")
            tree_result = run_git(source_path, "rev-parse", "HEAD^{tree}")
            tree_sha = tree_result.stdout.strip() if tree_result.returncode == 0 else ""
            if tree_sha != values.get("SOURCE_TREE_SHA"):
                failures.append(
                    f"SOURCE_PROJECT_DIR tree {tree_sha!r} does not match SOURCE_TREE_SHA"
                )
            second_head = run_git(source_path, "rev-parse", "HEAD").stdout.strip()
            if second_head != head:
                failures.append("SOURCE_PROJECT_DIR identity changed during validation")

    artifact_raw = values.get("RELEASE_ARTIFACT_PATH")
    if artifact_raw:
        artifact = Path(artifact_raw)
        if not artifact.is_absolute():
            failures.append("RELEASE_ARTIFACT_PATH must be absolute")
        if check_files:
            try:
                actual_artifact_hash, _ = sha256_secure_file(
                    artifact,
                    label="release artifact",
                    max_size=4 * 1024 * 1024 * 1024,
                )
                if actual_artifact_hash != values.get("RELEASE_ARTIFACT_SHA256"):
                    failures.append("release artifact hash does not match manifest")
            except SecureFileError as exc:
                failures.append(str(exc))

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
    values: dict[str, str] = {}
    try:
        manifest_bytes = read_secure_bytes(path, label="dark-standby manifest")
        values = parse_env_text(manifest_bytes.decode("utf-8"))
        failures, warnings = validate(values, check_files=args.check_files)
    except Exception as exc:
        failures, warnings = [str(exc)], []
    payload = {
        "status": "passed" if not failures else "failed",
        "manifest_sha256": hashlib.sha256(manifest_bytes if 'manifest_bytes' in locals() else b"").hexdigest(),
        "release_sha": values.get("SOURCE_RELEASE_SHA"),
        "source_host": values.get("WEBAPP_FI_HOST"),
        "target_host": values.get("WA_IR_HOST"),
        "production_root_domain": values.get("PRODUCTION_ROOT_DOMAIN"),
        "failover_test_root_domain": values.get("FAILOVER_TEST_ROOT_DOMAIN"),
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
