#!/usr/bin/env python3
"""Read-only production Queue-v1 plan, status, and preflight.

This tool cannot mutate env files, services, databases, Git, or Telegram.  The
only provider calls made by ``preflight`` are getMe/getChat/getChatMember.
Cutover/apply/rollback deliberately live outside this command and must be wired
to the guarded production deployment choreography separately.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.deploy_config import parse_env_file, resolve_deploy_settings
from scripts.run_production_backup import (
    backup_target_binding_sha256,
    database_identity_sha256,
)
from scripts.scan_telegram_queue_artifacts import scan_paths


DEFAULT_MANIFEST = REPO_ROOT / "deploy/production/online.env"
DEFAULT_STAGING_ENV = REPO_ROOT / ".env.staging"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BACKUP_MAXIMUM_AGE_SECONDS = 3600
BACKUP_MAXIMUM_CLOCK_SKEW_SECONDS = 300
PRODUCTION_IRAN_PROJECT_DIR = "/srv/trading-bot/current"
PRODUCTION_IRAN_APP_DOMAIN = "coin.gold-trade.ir"
PRODUCTION_FOREIGN_DOMAIN = "coin.362514.ir"
PRODUCTION_MANIFEST_IDENTITY_KEYS = (
    "LOCAL_PROJECT_DIR",
    "IRAN_HOST",
    "IRAN_SSH_USER",
    "IRAN_SSH_PORT",
    "IRAN_PROJECT_DIR",
    "IRAN_APP_DOMAIN",
    "IRAN_PUBLIC_DOMAIN",
    "FOREIGN_PUBLIC_DOMAIN",
)
REQUIRED_QUEUE_TABLES = (
    "telegram_delivery_jobs",
    "telegram_delivery_provider_outcomes",
    "telegram_delivery_resume_operations",
    "telegram_delivery_runtime_gates",
    "telegram_publisher_dispatch_commands",
    "telegram_notification_outbox",
)
QUEUE_PROFILE = {
    "TELEGRAM_DELIVERY_PRODUCER_MODE": "queue-v1",
    "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": "queue-v1",
    "TELEGRAM_DELIVERY_EXECUTION_OWNER": "queue-v1",
    "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "true",
    "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "true",
    "TELEGRAM_MULTI_PUBLISHER_ENABLED": "true",
    "TELEGRAM_B2B_DISPATCH_ENABLED": "true",
}
TOKEN_KEYS = (
    "BOT_TOKEN",
    *(f"TELEGRAM_PUBLISHER_{index}_BOT_TOKEN" for index in range(1, 6)),
    "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN",
    "TELEGRAM_MONITORING_BOT_TOKEN",
)
SHARED_PUBLISHER_FLEET_OPT_IN_KEY = (
    "TELEGRAM_DELIVERY_QUEUE_SHARED_PUBLISHER_FLEET_ENABLED"
)
SHARED_PUBLISHER_MINIMUM_DESTINATION_INTERVAL_SECONDS = 1.05
SHARED_PUBLISHER_UPDATE_OWNERSHIP_BLOCKER = (
    "BLOCKED_SHARED_PUBLISHER_UPDATE_OWNERSHIP_UNSUPPORTED"
)


class ReadinessBlocked(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Identity:
    role: str
    token: str
    bot_id: int
    username: str | None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(value: str | None) -> int | None:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _username(value: Any) -> str | None:
    normalized = str(value or "").strip().lstrip("@").lower()
    return normalized or None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise ReadinessBlocked("BLOCKED_GIT_BINDING")
    return (result.stdout or "").strip()


def git_binding() -> dict[str, str]:
    return {
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "head": _git("rev-parse", "HEAD"),
        "tree": _git("rev-parse", "HEAD^{tree}"),
        "origin_main": _git("rev-parse", "origin/main"),
        "worktree": "clean" if not _git("status", "--porcelain") else "dirty",
    }


def _immutable_source(manifest: Path) -> tuple[Path, dict[str, str]]:
    if not manifest.is_file():
        raise ReadinessBlocked("BLOCKED_PRODUCTION_MANIFEST")
    manifest_values = parse_env_file(manifest)
    if any(
        not str(manifest_values.get(key) or "").strip()
        for key in PRODUCTION_MANIFEST_IDENTITY_KEYS
    ):
        raise ReadinessBlocked("BLOCKED_EXPLICIT_PRODUCTION_IDENTITY")
    iran_project = str(manifest_values["IRAN_PROJECT_DIR"]).strip()
    local_project = Path(
        str(manifest_values["LOCAL_PROJECT_DIR"]).strip()
    ).expanduser().resolve(strict=False)
    if (
        iran_project != PRODUCTION_IRAN_PROJECT_DIR
        or "staging" in iran_project.lower()
        or local_project != REPO_ROOT
        or str(manifest_values["IRAN_APP_DOMAIN"]).strip().lower()
        != PRODUCTION_IRAN_APP_DOMAIN
        or str(manifest_values["IRAN_PUBLIC_DOMAIN"]).strip().lower()
        != PRODUCTION_IRAN_APP_DOMAIN
        or str(manifest_values["FOREIGN_PUBLIC_DOMAIN"]).strip().lower()
        != PRODUCTION_FOREIGN_DOMAIN
    ):
        raise ReadinessBlocked("BLOCKED_EXPLICIT_PRODUCTION_IDENTITY")
    raw_source = str(manifest_values.get("RUNTIME_ENV_SOURCE_PATH") or "").strip()
    raw_foreign = str(manifest_values.get("FOREIGN_RUNTIME_ENV_PATH") or "").strip()
    raw_iran = str(manifest_values.get("IRAN_RUNTIME_ENV_PATH") or "").strip()
    if not raw_source or not raw_foreign or not raw_iran:
        raise ReadinessBlocked("BLOCKED_IMMUTABLE_ENV_CONTRACT")
    source = Path(raw_source).expanduser().resolve(strict=False)
    foreign = Path(raw_foreign).expanduser().resolve(strict=False)
    iran = Path(raw_iran).expanduser().resolve(strict=False)
    if (
        source in {foreign, iran}
        or foreign == iran
        or REPO_ROOT in source.parents
        or any("staging" in str(path).lower() for path in (source, foreign, iran))
    ):
        raise ReadinessBlocked("BLOCKED_IMMUTABLE_ENV_CONTRACT")
    if not source.is_file() or not os.access(source, os.R_OK):
        raise ReadinessBlocked("BLOCKED_IMMUTABLE_ENV_SOURCE")
    mode = stat.S_IMODE(source.stat().st_mode)
    if mode & 0o077:
        raise ReadinessBlocked("BLOCKED_IMMUTABLE_ENV_PERMISSIONS")
    database_name = str(parse_env_file(source).get("POSTGRES_DB") or "").strip().lower()
    if not database_name or any(
        marker in database_name for marker in ("staging", "test", "scratch")
    ):
        raise ReadinessBlocked("BLOCKED_EXPLICIT_PRODUCTION_DATABASE")
    return source, manifest_values


def _profile(values: Mapping[str, str]) -> dict[str, Any]:
    mismatches = sorted(
        key
        for key, expected in QUEUE_PROFILE.items()
        if str(values.get(key) or "").strip().lower() != expected
    )
    non_bot_owner = str(
        values.get("TELEGRAM_NON_BOT_DELIVERY_EXECUTION_OWNER") or ""
    ).strip().lower()
    if non_bot_owner != "producer-only":
        mismatches.append("TELEGRAM_NON_BOT_DELIVERY_EXECUTION_OWNER")
    if str(values.get("TELEGRAM_NON_BOT_BOT_TOKEN") or "").strip():
        mismatches.append("TELEGRAM_NON_BOT_BOT_TOKEN")
    return {"ready": not mismatches, "mismatch_keys": sorted(set(mismatches))}


def source_profile(values: Mapping[str, str]) -> str:
    """Classify the immutable source without accepting a mixed transition."""

    producer = str(values.get("TELEGRAM_DELIVERY_PRODUCER_MODE") or "legacy").strip().lower()
    expected = str(
        values.get("TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER") or "legacy"
    ).strip().lower()
    executor = str(
        values.get("TELEGRAM_DELIVERY_EXECUTION_OWNER") or "legacy"
    ).strip().lower()
    controls = tuple(
        _truthy(values.get(key))
        for key in (
            "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED",
            "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY",
            "TELEGRAM_MULTI_PUBLISHER_ENABLED",
            "TELEGRAM_B2B_DISPATCH_ENABLED",
        )
    )
    if (producer, expected, executor) == ("legacy", "legacy", "legacy") and not any(controls):
        return "legacy"
    if (producer, expected, executor) == ("queue-v1", "queue-v1", "queue-v1") and all(controls):
        return "queue-v1"
    raise ReadinessBlocked("BLOCKED_SOURCE_PROFILE_SPLIT_BRAIN")


def queue_target_values(values: Mapping[str, str]) -> dict[str, str]:
    """Build the in-memory Queue target used by preflight; never writes source."""

    target = {str(key): str(value) for key, value in values.items()}
    target.update(QUEUE_PROFILE)
    target["TELEGRAM_NON_BOT_DELIVERY_EXECUTION_OWNER"] = "producer-only"
    target["TELEGRAM_NON_BOT_BOT_TOKEN"] = ""
    target["TELEGRAM_PROVIDER_TEST_AUTHORITY"] = "false"
    for index in range(1, 6):
        target[f"TELEGRAM_PUBLISHER_{index}_ENABLED"] = "true"
    return target


def _identities(values: Mapping[str, str]) -> tuple[tuple[Identity, ...], list[str]]:
    missing: list[str] = []
    identities: list[Identity] = []
    primary_token = str(values.get("BOT_TOKEN") or "").strip()
    primary_id = _positive_int(values.get("TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID"))
    primary_username = _username(values.get("BOT_USERNAME"))
    if not primary_token:
        missing.append("BOT_TOKEN")
    if primary_id is None:
        missing.append("TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID")
    if primary_username is None:
        missing.append("BOT_USERNAME")
    if primary_token and primary_id and primary_username:
        identities.append(
            Identity("primary", primary_token, primary_id, primary_username)
        )
    for index in range(1, 6):
        prefix = f"TELEGRAM_PUBLISHER_{index}"
        token = str(values.get(f"{prefix}_BOT_TOKEN") or "").strip()
        bot_id = _positive_int(values.get(f"{prefix}_EXPECTED_BOT_ID"))
        username = _username(values.get(f"{prefix}_EXPECTED_USERNAME"))
        if not _truthy(values.get(f"{prefix}_ENABLED")):
            missing.append(f"{prefix}_ENABLED")
        if not token:
            missing.append(f"{prefix}_BOT_TOKEN")
        if bot_id is None:
            missing.append(f"{prefix}_EXPECTED_BOT_ID")
        if username is None:
            missing.append(f"{prefix}_EXPECTED_USERNAME")
        if token and bot_id and username:
            identities.append(Identity(f"publisher_{index}", token, bot_id, username))
    if _truthy(values.get("TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_ENABLED")):
        editor_token = str(
            values.get("TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN") or ""
        ).strip()
        editor_id = _positive_int(
            values.get("TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_EDITOR_BOT_ID")
        )
        if not editor_token:
            missing.append("TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN")
        if editor_id is None:
            missing.append("TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_EDITOR_BOT_ID")
        if editor_token and editor_id:
            identities.append(Identity("channel_editor", editor_token, editor_id, None))
    return tuple(identities), sorted(set(missing))


def credential_status(
    production: Mapping[str, str], staging: Mapping[str, str]
) -> tuple[dict[str, Any], tuple[Identity, ...]]:
    identities, missing = _identities(production)
    channel = str(production.get("CHANNEL_ID") or "").strip()
    expected_channel = str(
        production.get("TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID") or ""
    ).strip()
    if not channel:
        missing.append("CHANNEL_ID")
    if not expected_channel:
        missing.append("TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID")
    elif channel != expected_channel:
        missing.append("TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID_MISMATCH")
    configured_tokens = [
        token
        for key in TOKEN_KEYS
        if (token := str(production.get(key) or "").strip())
    ]
    fingerprints = [_fingerprint(token) for token in configured_tokens]
    ids = [item.bot_id for item in identities]
    identity_names = [item.username for item in identities if item.username]
    # Collision evidence is meaningful only when it describes the complete
    # staging fleet.  Treating a partial or stale file as an empty comparison
    # set could incorrectly approve production reuse of a staging identity.
    staging_identities, staging_missing = _identities(staging)
    staging_channel = str(staging.get("CHANNEL_ID") or "").strip()
    staging_expected_channel = str(
        staging.get("TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID") or ""
    ).strip()
    if not staging_channel:
        staging_missing.append("CHANNEL_ID")
    if not staging_expected_channel:
        staging_missing.append("TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID")
    elif staging_channel != staging_expected_channel:
        staging_missing.append("TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID_MISMATCH")
    production_by_role = {item.role: item for item in identities}
    staging_by_role = {item.role: item for item in staging_identities}
    staging_fingerprints = {_fingerprint(item.token) for item in staging_identities}
    staging_ids = {item.bot_id for item in staging_identities}
    staging_usernames = {item.username for item in staging_identities if item.username}
    token_collision = bool(set(fingerprints) & staging_fingerprints)
    id_collision = bool(set(ids) & staging_ids)
    username_collision = bool(set(identity_names) & staging_usernames)
    channel_collision = bool(
        expected_channel
        and staging_expected_channel
        and expected_channel == staging_expected_channel
    )
    production_shared_opt_in = _truthy(
        production.get(SHARED_PUBLISHER_FLEET_OPT_IN_KEY)
    )
    staging_shared_opt_in = _truthy(staging.get(SHARED_PUBLISHER_FLEET_OPT_IN_KEY))
    shared_opt_in = production_shared_opt_in and staging_shared_opt_in
    shared_mode_requested = production_shared_opt_in or staging_shared_opt_in

    production_primary = production_by_role.get("primary")
    staging_primary = staging_by_role.get("primary")
    primary_token_collision = bool(
        (production_primary and any(
            _fingerprint(production_primary.token) == _fingerprint(item.token)
            for item in staging_identities
        ))
        or (staging_primary and any(
            _fingerprint(staging_primary.token) == _fingerprint(item.token)
            for item in identities
        ))
    )
    primary_id_collision = bool(
        (production_primary and any(
            production_primary.bot_id == item.bot_id for item in staging_identities
        ))
        or (staging_primary and any(
            staging_primary.bot_id == item.bot_id for item in identities
        ))
    )
    primary_username_collision = bool(
        (production_primary and production_primary.username and any(
            production_primary.username == item.username for item in staging_identities
        ))
        or (staging_primary and staging_primary.username and any(
            staging_primary.username == item.username for item in identities
        ))
    )

    publisher_roles = tuple(f"publisher_{index}" for index in range(1, 6))
    shared_publisher_role_matches = {
        role: bool(
            production_by_role.get(role)
            and staging_by_role.get(role)
            and _fingerprint(production_by_role[role].token)
            == _fingerprint(staging_by_role[role].token)
            and production_by_role[role].bot_id == staging_by_role[role].bot_id
            and production_by_role[role].username
            == staging_by_role[role].username
        )
        for role in publisher_roles
    }
    exact_shared_publisher_fleet = bool(
        shared_publisher_role_matches
        and all(shared_publisher_role_matches.values())
    )
    production_publishers = tuple(
        production_by_role[role]
        for role in publisher_roles
        if role in production_by_role
    )
    staging_publishers = tuple(
        staging_by_role[role] for role in publisher_roles if role in staging_by_role
    )
    any_publisher_collision = bool(
        {
            _fingerprint(item.token) for item in production_publishers
        }
        & {_fingerprint(item.token) for item in staging_publishers}
        or {item.bot_id for item in production_publishers}
        & {item.bot_id for item in staging_publishers}
        or {item.username for item in production_publishers if item.username}
        & {item.username for item in staging_publishers if item.username}
    )
    try:
        production_destination_interval = float(
            production.get(
                "TELEGRAM_DELIVERY_QUEUE_DESTINATION_MIN_INTERVAL_SECONDS",
                SHARED_PUBLISHER_MINIMUM_DESTINATION_INTERVAL_SECONDS,
            )
        )
        staging_destination_interval = float(
            staging.get(
                "TELEGRAM_DELIVERY_QUEUE_DESTINATION_MIN_INTERVAL_SECONDS",
                SHARED_PUBLISHER_MINIMUM_DESTINATION_INTERVAL_SECONDS,
            )
        )
    except (TypeError, ValueError):
        production_destination_interval = 0.0
        staging_destination_interval = 0.0
    shared_rate_safety = bool(
        production_destination_interval
        >= SHARED_PUBLISHER_MINIMUM_DESTINATION_INTERVAL_SECONDS
        and staging_destination_interval
        >= SHARED_PUBLISHER_MINIMUM_DESTINATION_INTERVAL_SECONDS
    )
    blockers: list[str] = []
    publisher_count = sum(item.role.startswith("publisher_") for item in identities)
    if missing or publisher_count != 5:
        blockers.append("BLOCKED_CREDENTIALS")
    if staging_missing or len(staging_identities) != 6:
        blockers.append("BLOCKED_STAGING_COLLISION_EVIDENCE")
    if len(fingerprints) != len(set(fingerprints)) or len(ids) != len(set(ids)):
        blockers.append("BLOCKED_CREDENTIAL_DUPLICATE")
    if len(identity_names) != len(set(identity_names)):
        blockers.append("BLOCKED_CREDENTIAL_DUPLICATE")
    if primary_token_collision or primary_id_collision or primary_username_collision:
        blockers.append("BLOCKED_STAGING_PRIMARY_REUSE")
    if any_publisher_collision or shared_mode_requested:
        # Owner approval, exact lane bindings, and conservative destination
        # cadence are retained as diagnostics, but they cannot make two Bot API
        # runtimes safe consumers of one publisher update stream. The current
        # runtime long-polls every enabled publisher for both B2B ACKs and
        # channel callbacks, while each environment owns a separate DB/Redis
        # context and limiter. Until a durable single-ingress router plus a
        # token-global limiter exists, every cross-environment publisher reuse
        # must stop production cutover before any mutation.
        blockers.append(SHARED_PUBLISHER_UPDATE_OWNERSHIP_BLOCKER)
        if any_publisher_collision and not shared_opt_in:
            blockers.append("BLOCKED_STAGING_PUBLISHER_REUSE")
        elif any_publisher_collision and not exact_shared_publisher_fleet:
            blockers.append("BLOCKED_PARTIAL_SHARED_PUBLISHER_FLEET")
        elif any_publisher_collision and not shared_rate_safety:
            blockers.append("BLOCKED_SHARED_PUBLISHER_RATE_SAFETY")
    if channel_collision:
        blockers.append("BLOCKED_STAGING_CHANNEL_REUSE")
    return (
        {
            "status": (
                SHARED_PUBLISHER_UPDATE_OWNERSHIP_BLOCKER
                if any_publisher_collision or shared_mode_requested
                else ("ready" if not blockers else blockers[0])
            ),
            "identity_count": len(identities),
            "publisher_count": publisher_count,
            "missing_keys": sorted(set(missing)),
            "production_tokens_distinct": len(fingerprints) == len(set(fingerprints)),
            "expected_ids_distinct": len(ids) == len(set(ids)),
            "expected_usernames_distinct": len(identity_names) == len(set(identity_names)),
            "staging_token_collision": token_collision,
            "staging_expected_id_collision": id_collision,
            "staging_expected_username_collision": username_collision,
            "staging_channel_collision": channel_collision,
            "staging_primary_collision": bool(
                primary_token_collision
                or primary_id_collision
                or primary_username_collision
            ),
            "shared_publisher_fleet_opt_in": shared_opt_in,
            "shared_publisher_fleet_exact": exact_shared_publisher_fleet,
            "shared_publisher_rate_safety": shared_rate_safety,
            "shared_publisher_update_ownership_supported": (
                False if any_publisher_collision or shared_mode_requested else None
            ),
            "shared_publisher_max_combined_channel_rps_per_bot": round(
                (1.0 / production_destination_interval)
                + (1.0 / staging_destination_interval),
                6,
            )
            if production_destination_interval > 0
            and staging_destination_interval > 0
            else None,
            "staging_collision_evidence_complete": (
                not staging_missing and len(staging_identities) == 6
            ),
            "blockers": sorted(set(blockers)),
            "secret_or_fingerprint_values_disclosed": False,
        },
        identities,
    )


def _backup_status(
    path: Path | None,
    expected_digest: str | None,
    *,
    manifest_values: Mapping[str, str],
    expected_release_sha: str,
    expected_database_name: str,
    expected_database_identities: Mapping[str, str],
    expected_schema_head: str,
) -> dict[str, Any]:
    if path is None or not path.is_file() or not SHA256_RE.fullmatch(str(expected_digest or "")):
        raise ReadinessBlocked("BLOCKED_BACKUP_EVIDENCE")
    receipt_metadata = path.lstat()
    receipt_parent_metadata = path.resolve(strict=False).parent.stat()
    if (
        path.is_symlink()
        or receipt_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(receipt_metadata.st_mode) != 0o600
        or receipt_metadata.st_nlink != 1
        or receipt_parent_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(receipt_parent_metadata.st_mode) & 0o077
        or REPO_ROOT == path.resolve(strict=False)
        or REPO_ROOT in path.resolve(strict=False).parents
    ):
        raise ReadinessBlocked("BLOCKED_BACKUP_EVIDENCE_SECURITY")
    actual = _sha256(path)
    if actual != expected_digest:
        raise ReadinessBlocked("BLOCKED_BACKUP_DIGEST")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ReadinessBlocked("BLOCKED_BACKUP_EVIDENCE") from None
    try:
        parsed_created_at = datetime.fromisoformat(
            str(payload.get("created_at") or "").replace("Z", "+00:00")
        )
    except (AttributeError, TypeError, ValueError):
        raise ReadinessBlocked("BLOCKED_BACKUP_FRESHNESS") from None
    if parsed_created_at.utcoffset() is None:
        raise ReadinessBlocked("BLOCKED_BACKUP_FRESHNESS")
    created_at = parsed_created_at.astimezone(timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - created_at).total_seconds()
    if (
        age_seconds < -BACKUP_MAXIMUM_CLOCK_SKEW_SECONDS
        or age_seconds > BACKUP_MAXIMUM_AGE_SECONDS
    ):
        raise ReadinessBlocked("BLOCKED_BACKUP_FRESHNESS")
    results = payload.get("results") if isinstance(payload, dict) else None
    by_role = {
        str(item.get("role")): item
        for item in results or []
        if isinstance(item, dict)
    }
    if payload.get("status") != "ok" or set(by_role) != {"foreign", "iran"}:
        raise ReadinessBlocked("BLOCKED_BACKUP_BOTH_HOSTS_REQUIRED")
    for item in by_role.values():
        restore = item.get("restore_smoke")
        files = item.get("files")
        role = str(item.get("role") or "")
        expected_project = "trading_bot" if role == "foreign" else "current"
        expected_target_binding = backup_target_binding_sha256(
            role, dict(manifest_values)
        )
        if (
            item.get("project_label") != expected_project
            or item.get("release_sha") != expected_release_sha
            or item.get("database_name") != expected_database_name
            or item.get("target_binding_sha256") != expected_target_binding
            or item.get("database_identity_sha256")
            != expected_database_identities.get(role)
            or item.get("schema_head") != expected_schema_head
            or not SHA256_RE.fullmatch(
                str(item.get("database_identity_sha256") or "")
            )
        ):
            raise ReadinessBlocked("BLOCKED_BACKUP_RUNTIME_BINDING")
        try:
            parsed_role_created_at = datetime.fromisoformat(
                str(item.get("created_at") or "").replace("Z", "+00:00")
            )
        except (AttributeError, TypeError, ValueError):
            raise ReadinessBlocked("BLOCKED_BACKUP_FRESHNESS") from None
        if parsed_role_created_at.utcoffset() is None:
            raise ReadinessBlocked("BLOCKED_BACKUP_FRESHNESS")
        role_created_at = parsed_role_created_at.astimezone(timezone.utc)
        role_age = (datetime.now(timezone.utc) - role_created_at).total_seconds()
        if (
            item.get("status") != "ok"
            or item.get("command_role") != role
            or role_age < -BACKUP_MAXIMUM_CLOCK_SKEW_SECONDS
            or role_age > BACKUP_MAXIMUM_AGE_SECONDS
            or not isinstance(files, list)
            or not files
            or {
                str(file_item.get("kind") or "")
                for file_item in files
                if isinstance(file_item, dict)
            }
            != {"db", "redis", "uploads", "audit"}
            or any(
                sum(
                    isinstance(file_item, dict)
                    and file_item.get("kind") == required_kind
                    for file_item in files
                )
                != 1
                for required_kind in ("db", "redis", "uploads", "audit")
            )
            or any(
                not isinstance(file_item, dict)
                or not SHA256_RE.fullmatch(str(file_item.get("sha256") or ""))
                or _positive_int(file_item.get("bytes")) is None
                for file_item in files
            )
            or not isinstance(restore, dict)
            or restore.get("status") != "passed"
            or _positive_int(restore.get("table_count")) is None
        ):
            raise ReadinessBlocked("BLOCKED_BACKUP_RESTORE_SMOKE")
        pulled_by_remote = {
            str(pulled.get("remote_path") or ""): str(
                pulled.get("local_path") or ""
            )
            for pulled in item.get("pulled_files") or []
            if isinstance(pulled, dict)
        }
        for file_item in files:
            recorded_path = str(file_item.get("path") or "")
            artifact_path = Path(
                pulled_by_remote.get(recorded_path, "")
                if role == "iran"
                else recorded_path
            ).expanduser()
            resolved = artifact_path.resolve(strict=False)
            artifact_metadata = (
                resolved.stat() if resolved.is_file() else None
            )
            parent_metadata = (
                resolved.parent.stat() if resolved.parent.is_dir() else None
            )
            if (
                not artifact_path.is_absolute()
                or artifact_path.is_symlink()
                or not resolved.is_file()
                or REPO_ROOT == resolved
                or REPO_ROOT in resolved.parents
                or artifact_metadata is None
                or artifact_metadata.st_uid != os.geteuid()
                or stat.S_IMODE(artifact_metadata.st_mode) != 0o600
                or artifact_metadata.st_nlink != 1
                or parent_metadata is None
                or parent_metadata.st_uid != os.geteuid()
                or stat.S_IMODE(parent_metadata.st_mode) & 0o077
                or artifact_metadata.st_size != int(file_item["bytes"])
                or _sha256(resolved) != str(file_item["sha256"])
            ):
                raise ReadinessBlocked("BLOCKED_BACKUP_ARTIFACT_DRIFT")
    return {
        "status": "verified",
        "roles": ["foreign", "iran"],
        "restore_smoke": "passed",
        "fresh": True,
        "maximum_age_seconds": BACKUP_MAXIMUM_AGE_SECONDS,
        "digest": actual,
        "target_binding_exact": True,
        "release_and_database_identity_exact": True,
        "source_paths_disclosed": False,
    }


def _run(args: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=REPO_ROOT, text=True, capture_output=True, check=False, timeout=timeout
    )


def _inspect_local_container(container: str) -> tuple[str, str]:
    project = _run(
        ["docker", "inspect", "-f", '{{index .Config.Labels "com.docker.compose.project"}}', container]
    )
    mode = _run(["docker", "exec", container, "printenv", "SERVER_MODE"])
    if project.returncode or mode.returncode:
        raise ReadinessBlocked("BLOCKED_PRODUCTION_RUNTIME_IDENTITY")
    return (project.stdout.strip(), mode.stdout.strip())


def _local_migration_head() -> str:
    result = _run([sys.executable, "-m", "alembic", "heads"])
    heads = {
        match.group(1)
        for line in (result.stdout or "").splitlines()
        if (match := re.match(r"^([0-9a-f]+)\s+\(head\)", line.strip()))
    }
    if result.returncode or len(heads) != 1:
        raise ReadinessBlocked("BLOCKED_PRODUCTION_SCHEMA_CAPABILITY")
    return next(iter(heads))


def _schema_query(expected_head: str) -> str:
    table_array = ",".join(f"'public.{table}'" for table in REQUIRED_QUEUE_TABLES)
    return (
        "select json_build_object("
        "'head',(select version_num from alembic_version limit 1),"
        "'database_name',current_database(),"
        "'system_identifier',(select system_identifier::text from pg_control_system()),"
        f"'table_count',(select count(*) from unnest(array[{table_array}]) t(name) "
        "where to_regclass(name) is not null))"
    )


def _inspect_local_release_and_schema(
    expected_head: str,
) -> tuple[str, str, int, str, str, str]:
    release = _run(["docker", "exec", "trading_bot_app", "printenv", "RELEASE_SHA"])
    db_project = _run(
        [
            "docker",
            "inspect",
            "-f",
            '{{index .Config.Labels "com.docker.compose.project"}}',
            "trading_bot_db",
        ]
    )
    schema = _run(
        [
            "docker",
            "exec",
            "trading_bot_db",
            "sh",
            "-lc",
            'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc '
            + shlex.quote(_schema_query(expected_head)),
        ]
    )
    if release.returncode or db_project.returncode or schema.returncode:
        raise ReadinessBlocked("BLOCKED_PRODUCTION_BASE_RELEASE_REQUIRED")
    try:
        payload = json.loads((schema.stdout or "").strip())
        return (
            release.stdout.strip(),
            str(payload.get("head") or ""),
            int(payload.get("table_count") or 0),
            db_project.stdout.strip(),
            str(payload.get("database_name") or ""),
            str(payload.get("system_identifier") or ""),
        )
    except (TypeError, ValueError):
        raise ReadinessBlocked("BLOCKED_PRODUCTION_SCHEMA_CAPABILITY") from None


def _inspect_remote_iran(settings: Mapping[str, str]) -> tuple[str, str]:
    base = [
        "ssh",
        "-p",
        str(settings["IRAN_SSH_PORT"]),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-i",
        str(settings["IRAN_SSH_PRIVATE_KEY_PATH"]),
        str(settings["IRAN_SSH_TARGET"]),
    ]
    project = _run(
        [*base, "docker inspect -f '{{index .Config.Labels \"com.docker.compose.project\"}}' trading_bot_app"]
    )
    mode = _run([*base, "docker exec trading_bot_app printenv SERVER_MODE"])
    if project.returncode or mode.returncode:
        raise ReadinessBlocked("BLOCKED_PRODUCTION_HOST_READBACK")
    return (project.stdout.strip(), mode.stdout.strip())


def _inspect_remote_release_and_schema(
    settings: Mapping[str, str], expected_head: str
) -> tuple[str, str, int, str, str, str]:
    base = [
        "ssh",
        "-p",
        str(settings["IRAN_SSH_PORT"]),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-i",
        str(settings["IRAN_SSH_PRIVATE_KEY_PATH"]),
        str(settings["IRAN_SSH_TARGET"]),
    ]
    release = _run([*base, "docker exec trading_bot_app printenv RELEASE_SHA"])
    db_project = _run(
        [
            *base,
            "docker inspect -f '{{index .Config.Labels \"com.docker.compose.project\"}}' trading_bot_db",
        ]
    )
    query = _schema_query(expected_head)
    remote_query = (
        "docker exec trading_bot_db sh -lc "
        + shlex.quote(
            'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc '
            + shlex.quote(query)
        )
    )
    schema = _run([*base, remote_query])
    if release.returncode or db_project.returncode or schema.returncode:
        raise ReadinessBlocked("BLOCKED_PRODUCTION_BASE_RELEASE_REQUIRED")
    try:
        payload = json.loads((schema.stdout or "").strip())
        return (
            release.stdout.strip(),
            str(payload.get("head") or ""),
            int(payload.get("table_count") or 0),
            db_project.stdout.strip(),
            str(payload.get("database_name") or ""),
            str(payload.get("system_identifier") or ""),
        )
    except (TypeError, ValueError):
        raise ReadinessBlocked("BLOCKED_PRODUCTION_SCHEMA_CAPABILITY") from None


def host_status(
    manifest: Path,
    *,
    expected_release_sha: str | None = None,
    expected_database_name: str | None = None,
) -> dict[str, Any]:
    manifest_values = parse_env_file(manifest)
    key_path_raw = str(manifest_values.get("IRAN_SSH_PRIVATE_KEY_PATH") or "").strip()
    if (
        str(manifest_values.get("IRAN_SSH_AUTH_METHOD") or "").strip().lower() != "key"
        or not key_path_raw
        or str(manifest_values.get("IRAN_SSH_PASSWORD") or "").strip()
    ):
        raise ReadinessBlocked("BLOCKED_PRODUCTION_KEY_ONLY_SSH")
    key_path = Path(key_path_raw).expanduser().resolve(strict=False)
    if (
        not key_path.is_file()
        or not os.access(key_path, os.R_OK)
        or stat.S_IMODE(key_path.stat().st_mode) & 0o077
    ):
        raise ReadinessBlocked("BLOCKED_PRODUCTION_KEY_ONLY_SSH")
    settings = resolve_deploy_settings(manifest_path=str(manifest), environ={})
    settings["IRAN_SSH_PRIVATE_KEY_PATH"] = str(key_path)
    expected_release = str(expected_release_sha or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", expected_release):
        raise ReadinessBlocked("BLOCKED_PRODUCTION_BASE_RELEASE_REQUIRED")
    expected_head = _local_migration_head()
    foreign_project, foreign_mode = _inspect_local_container("trading_bot_app")
    bot_project, bot_mode = _inspect_local_container("trading_bot_bot")
    iran_project, iran_mode = _inspect_remote_iran(settings)
    (
        foreign_release,
        foreign_head,
        foreign_tables,
        foreign_db_project,
        foreign_database,
        foreign_system_identifier,
    ) = (
        _inspect_local_release_and_schema(expected_head)
    )
    (
        iran_release,
        iran_head,
        iran_tables,
        iran_db_project,
        iran_database,
        iran_system_identifier,
    ) = (
        _inspect_remote_release_and_schema(settings, expected_head)
    )
    release_exact = foreign_release == expected_release == iran_release
    schema_exact = (
        foreign_head == expected_head == iran_head
        and foreign_tables == len(REQUIRED_QUEUE_TABLES)
        and iran_tables == len(REQUIRED_QUEUE_TABLES)
        and foreign_db_project == "trading_bot"
        and iran_db_project == "current"
        and bool(expected_database_name)
        and foreign_database == expected_database_name == iran_database
        and bool(re.fullmatch(r"[0-9]+", foreign_system_identifier))
        and bool(re.fullmatch(r"[0-9]+", iran_system_identifier))
    )
    ready = (
        foreign_project == "trading_bot"
        and bot_project == "trading_bot"
        and foreign_mode == "foreign"
        and bot_mode == "foreign"
        and iran_project == "current"
        and iran_mode == "iran"
        and release_exact
        and schema_exact
    )
    return {
        "ready": ready,
        "foreign_project_exact": foreign_project == "trading_bot" and bot_project == "trading_bot",
        "foreign_roles_exact": foreign_mode == "foreign" and bot_mode == "foreign",
        "iran_project_exact": iran_project == "current",
        "iran_role_exact": iran_mode == "iran",
        "release_sha_exact": release_exact,
        "schema_head_and_queue_tables_exact": schema_exact,
        "database_identity_exact": schema_exact,
        "_database_identity_sha256": {
            "foreign": database_identity_sha256(
                "foreign", foreign_database, foreign_system_identifier
            ),
            "iran": database_identity_sha256(
                "iran", iran_database, iran_system_identifier
            ),
        },
        "_schema_head": expected_head,
        "host_or_address_values_disclosed": False,
    }


def _provider_call(
    identity: Identity, method: str, payload: Mapping[str, str]
) -> Mapping[str, Any]:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{identity.token}/{method}",
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise ReadinessBlocked("BLOCKED_PROVIDER_PREFLIGHT") from None
    if not isinstance(body, dict) or body.get("ok") is not True or not isinstance(body.get("result"), dict):
        raise ReadinessBlocked("BLOCKED_PROVIDER_PREFLIGHT")
    return body["result"]


def provider_preflight(
    values: Mapping[str, str],
    identities: tuple[Identity, ...],
    *,
    gateway: Callable[[Identity, str, Mapping[str, str]], Mapping[str, Any]] = _provider_call,
) -> dict[str, Any]:
    channel = str(values.get("CHANNEL_ID") or "").strip()
    reports: list[dict[str, str]] = []
    for identity in identities:
        me = gateway(identity, "getMe", {})
        if int(me.get("id") or 0) != identity.bot_id:
            raise ReadinessBlocked("BLOCKED_PROVIDER_IDENTITY_MISMATCH")
        if identity.username and _username(me.get("username")) != identity.username:
            raise ReadinessBlocked("BLOCKED_PROVIDER_USERNAME_MISMATCH")
        chat = gateway(identity, "getChat", {"chat_id": channel})
        if str(chat.get("id") or "") != channel or chat.get("type") != "channel":
            raise ReadinessBlocked("BLOCKED_PROVIDER_CHANNEL_MISMATCH")
        member = gateway(
            identity,
            "getChatMember",
            {"chat_id": channel, "user_id": str(identity.bot_id)},
        )
        if identity.role == "channel_editor":
            required = {"can_manage_chat", "can_edit_messages"}
        elif identity.role == "primary":
            required = {
                "can_manage_chat",
                "can_post_messages",
                "can_edit_messages",
                "can_restrict_members",
            }
        else:
            required = {
                "can_manage_chat",
                "can_post_messages",
                "can_edit_messages",
                "can_delete_messages",
            }
        if (
            member.get("status") != "administrator"
            or member.get("is_anonymous") is not False
            or any(member.get(permission) is not True for permission in required)
        ):
            raise ReadinessBlocked("BLOCKED_PROVIDER_PERMISSIONS")
        reports.append({"role": identity.role, "status": "approved"})
    return {
        "status": "approved",
        "identity_count": len(reports),
        "identities": reports,
        "read_only_provider_call_count": len(reports) * 3,
        "sensitive_values_disclosed": False,
    }


def build_plan() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "environment": "production",
        "mode": "read-only",
        "created_at": _utc_now(),
        "commands": ["plan", "status", "preflight"],
        "apply_supported": False,
        "required_gates": [
            "immutable source env and exact queue profile",
            "clean pushed main",
            "exact foreign/iran production project and role readback",
            "fresh full db/redis/uploads/audit backup for both hosts, restored and bound to target/project/release/schema/database identity",
            "environment-specific central identity plus five complete publisher lanes",
            "complete staging collision evidence; owner-approved credential reuse remains blocked until one durable update ingress and one token-global limiter own shared publishers",
            "production and staging getMe/getChat/getChatMember identity, channel, and permission readback",
        ],
        "next_step": "wire an independently approved apply/rollback choreography; this tool cannot deploy",
    }


def build_status(manifest: Path, staging_env: Path) -> dict[str, Any]:
    source, _manifest_values = _immutable_source(manifest)
    source_values = parse_env_file(source)
    staging_values = parse_env_file(staging_env) if staging_env.is_file() else {}
    credentials, _ = credential_status(source_values, staging_values)
    return {
        "schema_version": 1,
        "environment": "production",
        "mode": "read-only",
        "observed_at": _utc_now(),
        "git": git_binding(),
        "immutable_source": {
            "present": True,
            "outside_repository": REPO_ROOT not in source.parents,
            "path_disclosed": False,
        },
        "queue_profile": _profile(source_values),
        "credentials": credentials,
        "provider_network_calls": 0,
    }


def run_preflight(
    manifest: Path,
    staging_env: Path,
    backup_receipt: Path | None,
    backup_digest: str | None,
    *,
    target_queue_cutover: bool = False,
    gateway: Callable[[Identity, str, Mapping[str, str]], Mapping[str, Any]] = _provider_call,
    host_inspector: Callable[[Path], dict[str, Any]] = host_status,
) -> dict[str, Any]:
    source, manifest_values = _immutable_source(manifest)
    if not staging_env.is_file():
        raise ReadinessBlocked("BLOCKED_STAGING_COLLISION_EVIDENCE")
    source_values = parse_env_file(source)
    expected_database_name = str(source_values.get("POSTGRES_DB") or "").strip()
    observed_source_profile = source_profile(source_values)
    evaluated_values = (
        queue_target_values(source_values) if target_queue_cutover else source_values
    )
    staging_values = parse_env_file(staging_env)
    credentials, identities = credential_status(evaluated_values, staging_values)
    # Credential and cross-environment ownership blockers intentionally win
    # over later gates without touching hosts or the Telegram provider.
    if credentials["blockers"]:
        raise ReadinessBlocked(str(credentials["status"]))
    profile = _profile(evaluated_values)
    if not profile["ready"]:
        raise ReadinessBlocked("BLOCKED_QUEUE_PROFILE")
    binding = git_binding()
    if binding["branch"] != "main" or binding["worktree"] != "clean" or binding["head"] != binding["origin_main"]:
        raise ReadinessBlocked("BLOCKED_CLEAN_PUSHED_MAIN")
    try:
        hosts = host_inspector(
            manifest,
            expected_release_sha=binding["head"],
            expected_database_name=expected_database_name,
        )
    except TypeError:
        # Custom inspectors used by older callers are not silently accepted;
        # the exact release binding is part of the production cutover gate.
        raise ReadinessBlocked("BLOCKED_PRODUCTION_BASE_RELEASE_REQUIRED") from None
    if not hosts.get("ready"):
        raise ReadinessBlocked("BLOCKED_PRODUCTION_RUNTIME_IDENTITY")
    database_identities = hosts.pop("_database_identity_sha256", None)
    expected_schema_head = str(hosts.pop("_schema_head", "") or "")
    if (
        not isinstance(database_identities, dict)
        or set(database_identities) != {"foreign", "iran"}
        or any(
            not SHA256_RE.fullmatch(str(value or ""))
            for value in database_identities.values()
        )
        or not re.fullmatch(r"[0-9A-Za-z_]+", expected_schema_head)
    ):
        raise ReadinessBlocked("BLOCKED_PRODUCTION_DATABASE_IDENTITY")
    backup = _backup_status(
        backup_receipt,
        backup_digest,
        manifest_values=manifest_values,
        expected_release_sha=binding["head"],
        expected_database_name=expected_database_name,
        expected_database_identities=database_identities,
        expected_schema_head=expected_schema_head,
    )
    provider = provider_preflight(evaluated_values, identities, gateway=gateway)
    staging_identities, staging_missing = _identities(staging_values)
    if staging_missing or len(staging_identities) != 6:
        raise ReadinessBlocked("BLOCKED_STAGING_COLLISION_EVIDENCE")
    staging_provider = provider_preflight(
        staging_values, staging_identities, gateway=gateway
    )
    provider["staging"] = staging_provider
    provider["staging_identity_count"] = staging_provider["identity_count"]
    provider["read_only_provider_call_count"] += staging_provider[
        "read_only_provider_call_count"
    ]
    return {
        "schema_version": 1,
        "environment": "production",
        "observed_at": _utc_now(),
        "status": "READY_FOR_SEPARATE_CUTOVER_CHOREOGRAPHY",
        "mode": "read-only",
        "git": binding,
        "queue_profile": profile,
        "source_profile": observed_source_profile,
        "source_sha256": _sha256(source),
        "target_queue_cutover": bool(target_queue_cutover),
        "credentials": credentials,
        "backup": backup,
        "hosts": hosts,
        "provider": provider,
        "apply_supported": False,
    }


def _write_report(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    scan = scan_paths([path])
    if scan.get("status") != "clean":
        path.unlink(missing_ok=True)
        raise ReadinessBlocked("BLOCKED_REPORT_REDACTION")
    return {"report_sha256": _sha256(path), "security_scan": "clean"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "status", "preflight"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--staging-env", type=Path, default=DEFAULT_STAGING_ENV)
    parser.add_argument("--backup-receipt", type=Path)
    parser.add_argument("--backup-receipt-sha256")
    parser.add_argument("--target-queue-cutover", action="store_true")
    parser.add_argument("--report", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "plan":
            payload = build_plan()
        elif args.command == "status":
            payload = build_status(args.manifest, args.staging_env)
        else:
            payload = run_preflight(
                args.manifest,
                args.staging_env,
                args.backup_receipt,
                args.backup_receipt_sha256,
                target_queue_cutover=bool(args.target_queue_cutover),
            )
        if args.report:
            payload["evidence"] = _write_report(args.report, payload)
    except ReadinessBlocked as exc:
        print(
            json.dumps(
                {
                    "status": exc.code,
                    "environment": "production",
                    "mode": "read-only",
                    "provider_mutations": 0,
                    "secrets_disclosed": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 4
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
