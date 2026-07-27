#!/usr/bin/env python3
"""Prepare and centrally publish one immutable six-object staging seed set.

Source hosts only verify their exact backup and encrypt it to public per-target
age recipients.  The controller receives ciphertext plus preparation journals,
proves all six fixed keys absent, and performs conditional versioned PUTs.  No
private age identity is accepted by any controller publication command.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from typing import Any, Callable, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import boto3
    from botocore.config import Config
except ModuleNotFoundError:  # Source preparation and unit tests need no SDK.
    boto3 = None
    Config = None

from core.human_approval import (
    TOKEN_SCHEMA,
    approval_policy_hash,
    approval_subject,
    verify_human_approval,
)
from core.secure_file_io import (
    read_secure_bytes,
    read_secure_text,
    sha256_secure_file,
    write_secure_new_bytes,
)
from scripts.run_three_site_staging_source_backup import verify_backup_manifest
from scripts.verify_three_site_staging_inventory import (
    _canonical_bytes,
    verify_approved_inventory,
)


SOURCE_ROLES = ("bot_fi", "webapp_fi")
TARGET_RECIPIENTS = {
    "bot_fi": ("bot_fi",),
    "webapp_fi": ("webapp_fi", "webapp_ir"),
}
TARGET_ROLES = ("bot_fi", "webapp_fi", "webapp_ir")
ARTIFACT_KINDS = ("postgres", "uploads", "audit")
AGE = "/usr/bin/age"
ARVAN_ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
ARVAN_REGION = "ir-thr-at1"
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024 * 1024
MAX_CIPHERTEXT_BYTES = MAX_ARTIFACT_BYTES + 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RECIPIENT_RE = re.compile(r"^age1[0-9a-z]+$")
MACHINE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
SSE_FIELDS = (
    "ServerSideEncryption",
    "SSECustomerAlgorithm",
    "SSECustomerKeyMD5",
    "SSEKMSKeyId",
)
PREPARATION_CORE_SCHEMA = "three-site-staging-seed-preparation-core-v2"
PUBLICATION_CORE_SCHEMA = "three-site-staging-seed-final-publication-core-v2"
SOURCE_PLAN_SCHEMA = "three-site-staging-seed-source-preparation-plan-v2"
CONTRACT_SCHEMA = "three-site-staging-seed-publication-contract-v2"
PREPARATION_SCHEMA = "three-site-staging-seed-role-preparation-v2"
READINESS_SCHEMA = "three-site-staging-seed-global-readiness-v2"
BASELINE_SCHEMA = "three-site-staging-seed-global-absence-baseline-v2"


class CampaignSeedError(RuntimeError):
    """A seed-publication safety invariant was not proven."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CampaignSeedError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _canonical_encoded(value: Any) -> bytes:
    return _canonical_bytes(value) + b"\n"


def _pretty_encoded(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _secure_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw = read_secure_bytes(
            path,
            label=label,
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except Exception as exc:
        raise CampaignSeedError(f"{label} is unavailable or invalid") from exc
    if not isinstance(payload, dict):
        raise CampaignSeedError(f"{label} must contain one JSON object")
    return payload


def _load_canonical_contract(path: Path) -> tuple[dict[str, Any], str]:
    if os.geteuid() != 0:
        raise CampaignSeedError("campaign seed publication must run as root")
    payload = _secure_json(path, label="campaign seed contract")
    raw = read_secure_bytes(
        path,
        label="campaign seed contract",
        owner_uid=0,
        max_size=MAX_JSON_BYTES,
    )
    if raw != _canonical_encoded(payload):
        raise CampaignSeedError("campaign seed contract is not canonical JSON")
    _validate_contract(payload)
    return payload, _canonical_hash(payload)


def _write_or_verify(
    path: Path,
    payload: bytes,
    *,
    label: str,
    canonical_json: bool = False,
) -> None:
    if path.exists() or path.is_symlink():
        existing = read_secure_bytes(
            path,
            label=label,
            owner_uid=0,
            max_size=max(MAX_JSON_BYTES, len(payload)),
        )
        if existing != payload:
            raise CampaignSeedError(f"{label} already exists with different bytes")
        return
    try:
        write_secure_new_bytes(
            path,
            payload,
            label=label,
            mode=0o600,
            max_size=max(MAX_JSON_BYTES, len(payload)),
        )
    except Exception as exc:
        raise CampaignSeedError(f"cannot publish {label} exclusively") from exc
    if canonical_json:
        parsed = _secure_json(path, label=label)
        if payload != _canonical_encoded(parsed):
            raise CampaignSeedError(f"{label} is not canonical JSON")


def _ensure_private_directory(path: Path) -> None:
    if path.exists() or path.is_symlink():
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise CampaignSeedError(f"unsafe private directory: {path}")
        return
    path.mkdir(mode=0o700, parents=True)
    metadata = path.lstat()
    if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise CampaignSeedError(f"unsafe private directory: {path}")


@contextmanager
def _exclusive_lock(root: Path) -> Iterator[None]:
    _ensure_private_directory(root)
    descriptor = os.open(
        root / ".lock",
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise CampaignSeedError("publication lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CampaignSeedError("campaign publication is already running") from exc
        yield
    finally:
        os.close(descriptor)


def _sha_regular_file(
    path: Path,
    *,
    label: str,
    maximum: int,
    require_private: bool,
) -> tuple[str, int]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CampaignSeedError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or (require_private and stat.S_IMODE(metadata.st_mode) != 0o600)
        or metadata.st_size <= 0
        or metadata.st_size > maximum
    ):
        raise CampaignSeedError(f"{label} is not a safe regular file")
    digest = hashlib.sha256()
    size = 0
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                raise CampaignSeedError(f"{label} exceeds its safety bound")
            digest.update(chunk)
        after = os.fstat(descriptor)
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise CampaignSeedError(f"{label} changed while it was read")
    finally:
        os.close(descriptor)
    return digest.hexdigest(), size


def _recipient_fingerprint(recipient: str) -> str:
    if RECIPIENT_RE.fullmatch(recipient) is None:
        raise CampaignSeedError("age recipient is malformed")
    return hashlib.sha256((recipient + "\n").encode()).hexdigest()


def _machine_id(path: Path = Path("/etc/machine-id")) -> str:
    try:
        value = path.read_text(encoding="ascii").strip().lower()
    except OSError as exc:
        raise CampaignSeedError("cannot read the host machine id") from exc
    if MACHINE_ID_RE.fullmatch(value) is None:
        raise CampaignSeedError("host machine id is malformed")
    return value


def _preparation_core(document: dict[str, Any]) -> dict[str, Any]:
    if document.get("schema") == PREPARATION_CORE_SCHEMA:
        return document
    if isinstance(document.get("core"), dict):
        return document["core"]
    if isinstance(document.get("preparation_core"), dict):
        return document["preparation_core"]
    raise CampaignSeedError("preparation core is unavailable")


def _role_rows(contract: dict[str, Any], role: str) -> list[dict[str, Any]]:
    core = _preparation_core(contract)
    rows = [
        row
        for row in core["objects"]
        if row["source_role"] == role
    ]
    if [row["kind"] for row in rows] != list(ARTIFACT_KINDS):
        raise CampaignSeedError("contract role object order is invalid")
    return rows


def _seed_role_prefix(
    *,
    prefix: str,
    campaign_id: str,
    release_sha: str,
    role: str,
) -> str:
    return f"{prefix}seed-v2/{campaign_id}/{release_sha}/{role}/"


def _expected_object_key(
    prefix: str,
    campaign_id: str,
    release_sha: str,
    role: str,
    kind: str,
) -> str:
    return (
        _seed_role_prefix(
            prefix=prefix,
            campaign_id=campaign_id,
            release_sha=release_sha,
            role=role,
        )
        + f"{kind}.age"
    )


def _validate_contract_core(core: dict[str, Any]) -> None:
    required = {
        "schema",
        "campaign_id",
        "release_sha",
        "deployment_id",
        "immutable_release",
        "inventory",
        "inventory_approval",
        "approval_policy_sha256",
        "witness_relay_public_key_sha256",
        "backup_manifests",
        "source_machine_ids",
        "controller_machine_id",
        "target_identity_paths",
        "source_state_roots",
        "controller_state_root",
        "storage",
        "recipient_distribution",
        "recipients",
        "objects",
        "object_count",
        "consumer_contract",
        "object_overwrite",
        "object_delete",
        "provider_side_sse",
    }
    if (
        not isinstance(core, dict)
        or set(core) != required
        or core.get("schema") != PREPARATION_CORE_SCHEMA
        or SHA_RE.fullmatch(str(core.get("release_sha") or "")) is None
        or not str(core.get("campaign_id") or "")
        or not str(core.get("deployment_id") or "")
        or core.get("object_count") != 6
        or core.get("object_overwrite") is not False
        or core.get("object_delete") is not False
        or core.get("provider_side_sse") is not False
    ):
        raise CampaignSeedError("contract core identity or policy is invalid")
    immutable = core["immutable_release"]
    release_files = immutable.get("files") if isinstance(immutable, dict) else None
    required_release_files = {
        "scripts/publish_three_site_staging_seed_campaign.py",
        "scripts/run_three_site_staging_source_backup.py",
        "scripts/fetch_three_site_staging_seed.py",
        "scripts/verify_three_site_staging_migration_plan.py",
    }
    if (
        not isinstance(immutable, dict)
        or set(immutable) != {"root", "git_head", "files"}
        or not Path(str(immutable["root"])).is_absolute()
        or immutable["git_head"] != core["release_sha"]
        or not isinstance(release_files, dict)
        or set(release_files) != required_release_files
        or any(SHA256_RE.fullmatch(str(value)) is None for value in release_files.values())
    ):
        raise CampaignSeedError("immutable release binding is invalid")
    inventory = core["inventory"]
    inventory_approval = core["inventory_approval"]
    if (
        not isinstance(inventory, dict)
        or set(inventory) != {"sha256", "stage"}
        or SHA256_RE.fullmatch(str(inventory["sha256"])) is None
        or inventory["stage"] != "provisioned"
        or not isinstance(inventory_approval, dict)
        or set(inventory_approval)
        != {"sha256", "approval_id", "expires_at", "action"}
        or SHA256_RE.fullmatch(str(inventory_approval["sha256"])) is None
        or inventory_approval["action"] != "approve_inventory"
        or SHA256_RE.fullmatch(str(core["approval_policy_sha256"])) is None
        or SHA256_RE.fullmatch(str(core["witness_relay_public_key_sha256"])) is None
    ):
        raise CampaignSeedError("inventory approval binding is invalid")
    storage = core["storage"]
    if (
        not isinstance(storage, dict)
        or set(storage)
        != {"bucket", "prefix", "credential_id", "private", "versioning"}
        or not str(storage["bucket"])
        or not str(storage["prefix"]).startswith("staging/")
        or not str(storage["credential_id"])
        or storage["private"] is not True
        or storage["versioning"] is not True
    ):
        raise CampaignSeedError("contract Object Storage boundary is invalid")
    if core["recipient_distribution"] != {
        role: list(TARGET_RECIPIENTS[role]) for role in SOURCE_ROLES
    }:
        raise CampaignSeedError("recipient distribution differs from the reviewed topology")
    recipients = core["recipients"]
    if not isinstance(recipients, dict) or set(recipients) != set(TARGET_ROLES):
        raise CampaignSeedError("per-target recipient set is incomplete")
    for target in TARGET_ROLES:
        row = recipients[target]
        if (
            not isinstance(row, dict)
            or set(row) != {"recipient", "fingerprint"}
            or row["fingerprint"] != _recipient_fingerprint(str(row["recipient"]))
        ):
            raise CampaignSeedError("per-target recipient binding is invalid")
    if len({row["fingerprint"] for row in recipients.values()}) != len(TARGET_ROLES):
        raise CampaignSeedError("target age recipients must be distinct")
    backups = core["backup_manifests"]
    source_ids = core["source_machine_ids"]
    source_roots = core["source_state_roots"]
    if (
        not isinstance(backups, dict)
        or set(backups) != set(SOURCE_ROLES)
        or not isinstance(source_ids, dict)
        or set(source_ids) != set(SOURCE_ROLES)
        or not isinstance(source_roots, dict)
        or set(source_roots) != set(SOURCE_ROLES)
    ):
        raise CampaignSeedError("source contract set is incomplete")
    for role in SOURCE_ROLES:
        backup = backups[role]
        if (
            not isinstance(backup, dict)
            or set(backup)
            != {"path", "sha256", "source_release_sha", "artifacts"}
            or not Path(str(backup["path"])).is_absolute()
            or SHA256_RE.fullmatch(str(backup["sha256"])) is None
            or SHA_RE.fullmatch(str(backup["source_release_sha"])) is None
            or not isinstance(backup["artifacts"], dict)
            or set(backup["artifacts"]) != set(ARTIFACT_KINDS)
            or MACHINE_ID_RE.fullmatch(str(source_ids[role])) is None
            or not Path(str(source_roots[role])).is_absolute()
        ):
            raise CampaignSeedError("source backup binding is invalid")
        for kind in ARTIFACT_KINDS:
            artifact = backup["artifacts"][kind]
            if (
                not isinstance(artifact, dict)
                or set(artifact) != {"sha256", "bytes"}
                or SHA256_RE.fullmatch(str(artifact["sha256"])) is None
                or type(artifact["bytes"]) is not int
                or artifact["bytes"] <= 0
            ):
                raise CampaignSeedError("source artifact binding is invalid")
    identity_paths = core["target_identity_paths"]
    if (
        MACHINE_ID_RE.fullmatch(str(core["controller_machine_id"])) is None
        or not Path(str(core["controller_state_root"])).is_absolute()
        or not isinstance(identity_paths, dict)
        or set(identity_paths) != set(TARGET_ROLES)
        or any(
            not Path(str(identity_paths[target])).is_absolute()
            for target in TARGET_ROLES
        )
        or len({str(identity_paths[target]) for target in TARGET_ROLES}) != 3
    ):
        raise CampaignSeedError("controller host/path binding is invalid")
    objects = core["objects"]
    if not isinstance(objects, list) or len(objects) != 6:
        raise CampaignSeedError("contract must contain exactly six objects")
    observed: set[tuple[str, str]] = set()
    keys: set[str] = set()
    for row in objects:
        if not isinstance(row, dict) or set(row) != {
            "source_role",
            "kind",
            "object_key",
            "plaintext_sha256",
            "plaintext_bytes",
            "recipient_fingerprints",
        }:
            raise CampaignSeedError("contract object fields are invalid")
        role = str(row["source_role"])
        kind = str(row["kind"])
        key = str(row["object_key"])
        expected_fingerprints = {
            target: recipients[target]["fingerprint"]
            for target in TARGET_RECIPIENTS.get(role, ())
        }
        if (
            role not in SOURCE_ROLES
            or kind not in ARTIFACT_KINDS
            or (role, kind) in observed
            or key in keys
            or key
            != _expected_object_key(
                str(storage["prefix"]),
                str(core["campaign_id"]),
                str(core["release_sha"]),
                role,
                kind,
            )
            or row["plaintext_sha256"] != backups[role]["artifacts"][kind]["sha256"]
            or row["plaintext_bytes"] != backups[role]["artifacts"][kind]["bytes"]
            or row["recipient_fingerprints"] != expected_fingerprints
        ):
            raise CampaignSeedError("contract object set is not deterministic and exact")
        observed.add((role, kind))
        keys.add(key)
    if observed != {
        (role, kind) for role in SOURCE_ROLES for kind in ARTIFACT_KINDS
    }:
        raise CampaignSeedError("contract object set is incomplete")
    if core["consumer_contract"] != {
        "schema": "three-site-staging-seed-manifest-v2",
        "target_seed_map": {
            "bot_fi": ["bot_fi", "restore"],
            "webapp_fi": ["webapp_fi", "restore"],
            "webapp_ir": ["webapp_fi", "clone"],
            "witness": [None, "empty"],
        },
        "identity_distribution": "distinct-per-target",
        "controller_publication_identity_input_accepted": False,
    }:
        raise CampaignSeedError("consumer identity contract is invalid")


def verify_inventory_approval_explicit(
    *,
    inventory: dict[str, Any],
    inventory_approval: dict[str, Any],
    approval_policy: dict[str, Any],
    witness_relay_public_key: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify the exact provisioned inventory without ambient relay trust."""

    try:
        verified = verify_approved_inventory(
            inventory,
            approval=inventory_approval,
            approval_policy=approval_policy,
            host_destructive=None,
            now=now,
            require_fresh_approval=True,
            witness_relay_public_key=witness_relay_public_key.strip(),
        )
    except Exception as exc:
        raise CampaignSeedError("inventory relay approval is invalid") from exc
    if verified["inventory_stage"] != "provisioned":
        raise CampaignSeedError("seed publication requires provisioned inventory")
    return {
        **verified,
        "approval_sha256": _canonical_hash(inventory_approval),
        "witness_relay_public_key_sha256": hashlib.sha256(
            (witness_relay_public_key.strip() + "\n").encode()
        ).hexdigest(),
    }


def _immutable_release_binding(root: Path, release_sha: str) -> dict[str, Any]:
    if not root.is_absolute():
        raise CampaignSeedError("immutable release root must be absolute")
    try:
        head = subprocess.run(
            ["/usr/bin/git", "-C", str(root), "rev-parse", "HEAD"],
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
            env=SAFE_ENV,
        )
        dirty = subprocess.run(
            [
                "/usr/bin/git",
                "-C",
                str(root),
                "status",
                "--porcelain",
                "--untracked-files=no",
            ],
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CampaignSeedError("cannot verify immutable release Git identity") from exc
    if (
        head.returncode != 0
        or head.stdout.strip().lower() != release_sha
        or dirty.returncode != 0
        or dirty.stdout.strip()
    ):
        raise CampaignSeedError("release root is not the exact clean immutable Git release")
    relative_files = (
        "scripts/publish_three_site_staging_seed_campaign.py",
        "scripts/run_three_site_staging_source_backup.py",
        "scripts/fetch_three_site_staging_seed.py",
        "scripts/verify_three_site_staging_migration_plan.py",
    )
    files: dict[str, str] = {}
    for relative in relative_files:
        files[relative] = _sha_regular_file(
            root / relative,
            label=f"immutable release file {relative}",
            maximum=4 * 1024 * 1024,
            require_private=False,
        )[0]
    return {"root": str(root), "git_head": release_sha, "files": files}


def build_preparation_core(
    *,
    inventory: dict[str, Any],
    inventory_result: dict[str, Any],
    inventory_approval: dict[str, Any],
    backups: dict[str, dict[str, Any]],
    backup_paths: dict[str, Path],
    recipient_values: dict[str, str],
    immutable_release: dict[str, Any],
    source_state_roots: dict[str, Path],
    controller_state_root: Path,
    target_identity_paths: dict[str, Path],
) -> dict[str, Any]:
    """Build the exact approval subject payload; it is not yet executable."""

    storage = inventory.get("object_storage")
    roles = {
        str(row.get("role")): row
        for row in inventory.get("roles", [])
        if isinstance(row, dict)
    }
    if (
        inventory_result.get("inventory_stage") != "provisioned"
        or inventory_result.get("inventory_sha256") != _canonical_hash(inventory)
        or inventory_result.get("campaign_id") != inventory.get("campaign_id")
        or inventory_result.get("release_sha") != inventory.get("release_sha")
        or inventory_result.get("deployment_id") != inventory.get("deployment_id")
        or not isinstance(storage, dict)
        or set(backups) != set(SOURCE_ROLES)
        or set(backup_paths) != set(SOURCE_ROLES)
        or set(recipient_values) != set(TARGET_ROLES)
        or set(target_identity_paths) != set(TARGET_ROLES)
        or set(source_state_roots) != set(SOURCE_ROLES)
        or set(roles) != {"bot_fi", "webapp_fi", "webapp_ir", "witness"}
    ):
        raise CampaignSeedError("contract inputs differ from the approved inventory")
    if (
        inventory_result.get("approval_policy_sha256") is None
        or inventory_result.get("approval_sha256") != _canonical_hash(inventory_approval)
        or inventory_result.get("witness_relay_public_key_sha256") is None
    ):
        raise CampaignSeedError("contract lacks explicit inventory relay approval evidence")
    backup_bindings: dict[str, Any] = {}
    for role in SOURCE_ROLES:
        backup = backups[role]
        source_release_sha = str(backup.get("source_release_sha") or "")
        try:
            verify_backup_manifest(
                backup,
                campaign_id=str(inventory["campaign_id"]),
                source_role=role,
                source_release_sha=source_release_sha,
                target_release_sha=str(inventory["release_sha"]),
                verify_files=False,
            )
        except Exception as exc:
            raise CampaignSeedError(f"{role} backup manifest is invalid") from exc
        backup_bindings[role] = {
            "path": str(backup_paths[role]),
            "sha256": _canonical_hash(backup),
            "source_release_sha": source_release_sha,
            "artifacts": {
                kind: {
                    "sha256": backup["artifacts"][kind]["sha256"],
                    "bytes": backup["artifacts"][kind]["bytes"],
                }
                for kind in ARTIFACT_KINDS
            },
        }
    recipients = {
        target: {
            "recipient": recipient_values[target],
            "fingerprint": _recipient_fingerprint(recipient_values[target]),
        }
        for target in TARGET_ROLES
    }
    objects: list[dict[str, Any]] = []
    for role in SOURCE_ROLES:
        for kind in ARTIFACT_KINDS:
            artifact = backup_bindings[role]["artifacts"][kind]
            objects.append(
                {
                    "source_role": role,
                    "kind": kind,
                    "object_key": _expected_object_key(
                        str(storage["prefix"]),
                        str(inventory["campaign_id"]),
                        str(inventory["release_sha"]),
                        role,
                        kind,
                    ),
                    "plaintext_sha256": artifact["sha256"],
                    "plaintext_bytes": artifact["bytes"],
                    "recipient_fingerprints": {
                        target: recipients[target]["fingerprint"]
                        for target in TARGET_RECIPIENTS[role]
                    },
                }
            )
    core = {
        "schema": PREPARATION_CORE_SCHEMA,
        "campaign_id": inventory["campaign_id"],
        "release_sha": inventory["release_sha"],
        "deployment_id": inventory["deployment_id"],
        "immutable_release": immutable_release,
        "inventory": {
            "sha256": inventory_result["inventory_sha256"],
            "stage": "provisioned",
        },
        "inventory_approval": {
            "sha256": inventory_result["approval_sha256"],
            "approval_id": inventory_result["approval_id"],
            "expires_at": inventory_result["approval_expires_at"],
            "action": "approve_inventory",
        },
        "approval_policy_sha256": inventory_result["approval_policy_sha256"],
        "witness_relay_public_key_sha256": inventory_result[
            "witness_relay_public_key_sha256"
        ],
        "backup_manifests": backup_bindings,
        "source_machine_ids": {
            role: str(roles[role]["machine_id"]).lower() for role in SOURCE_ROLES
        },
        "controller_machine_id": str(roles["bot_fi"]["machine_id"]).lower(),
        "target_identity_paths": {
            target: str(target_identity_paths[target]) for target in TARGET_ROLES
        },
        "source_state_roots": {
            role: str(source_state_roots[role]) for role in SOURCE_ROLES
        },
        "controller_state_root": str(controller_state_root),
        "storage": {
            "bucket": storage["bucket"],
            "prefix": storage["prefix"],
            "credential_id": storage["credential_id"],
            "private": True,
            "versioning": True,
        },
        "recipient_distribution": {
            role: list(TARGET_RECIPIENTS[role]) for role in SOURCE_ROLES
        },
        "recipients": recipients,
        "objects": objects,
        "object_count": 6,
        "consumer_contract": {
            "schema": "three-site-staging-seed-manifest-v2",
            "target_seed_map": {
                "bot_fi": ["bot_fi", "restore"],
                "webapp_fi": ["webapp_fi", "restore"],
                "webapp_ir": ["webapp_fi", "clone"],
                "witness": [None, "empty"],
            },
            "identity_distribution": "distinct-per-target",
            "controller_publication_identity_input_accepted": False,
        },
        "object_overwrite": False,
        "object_delete": False,
        "provider_side_sse": False,
    }
    _validate_contract_core(core)
    return core


def source_preparation_plan(
    preparation_core: dict[str, Any],
    *,
    role: str,
) -> dict[str, Any]:
    _validate_contract_core(preparation_core)
    if role not in SOURCE_ROLES:
        raise CampaignSeedError("source role is invalid")
    return {
        "schema": SOURCE_PLAN_SCHEMA,
        "preparation_core": preparation_core,
        "preparation_core_sha256": _canonical_hash(preparation_core),
        "source_role": role,
        "inventory_approval": dict(preparation_core["inventory_approval"]),
        "publication_token_present": False,
        "approval_policy_present": False,
        "object_storage_secret_present": False,
    }


def source_confirmation_phrase(plan: dict[str, Any]) -> str:
    return (
        f"prepare-seed:{plan['preparation_core']['campaign_id']}:"
        f"{plan['source_role']}:{_canonical_hash(plan)}"
    )


def _validate_source_plan(plan: dict[str, Any], *, role: str) -> None:
    if (
        not isinstance(plan, dict)
        or set(plan)
        != {
            "schema",
            "preparation_core",
            "preparation_core_sha256",
            "source_role",
            "inventory_approval",
            "publication_token_present",
            "approval_policy_present",
            "object_storage_secret_present",
        }
        or plan.get("schema") != SOURCE_PLAN_SCHEMA
        or plan.get("source_role") != role
        or not isinstance(plan.get("preparation_core"), dict)
        or plan.get("preparation_core_sha256")
        != _canonical_hash(plan["preparation_core"])
        or plan.get("inventory_approval")
        != plan["preparation_core"].get("inventory_approval")
        or plan.get("publication_token_present") is not False
        or plan.get("approval_policy_present") is not False
        or plan.get("object_storage_secret_present") is not False
    ):
        raise CampaignSeedError("source preparation plan is invalid")
    _validate_contract_core(plan["preparation_core"])


def _validate_publication_core(
    publication_core: dict[str, Any],
    *,
    preparation_core: dict[str, Any],
) -> None:
    _validate_contract_core(preparation_core)
    required = {
        "schema",
        "campaign_id",
        "release_sha",
        "deployment_id",
        "inventory_sha256",
        "preparation_core_sha256",
        "preparation_sha256",
        "objects",
        "object_count",
        "fixed_object_keys_sha256",
        "recipient_map_sha256",
        "controller_publication_identity_input_accepted",
        "controller_decryption_planned",
    }
    objects = publication_core.get("objects") if isinstance(publication_core, dict) else None
    if (
        not isinstance(publication_core, dict)
        or set(publication_core) != required
        or publication_core.get("schema") != PUBLICATION_CORE_SCHEMA
        or publication_core.get("campaign_id") != preparation_core["campaign_id"]
        or publication_core.get("release_sha") != preparation_core["release_sha"]
        or publication_core.get("deployment_id") != preparation_core["deployment_id"]
        or publication_core.get("inventory_sha256")
        != preparation_core["inventory"]["sha256"]
        or publication_core.get("preparation_core_sha256")
        != _canonical_hash(preparation_core)
        or publication_core.get("object_count") != 6
        or publication_core.get("controller_publication_identity_input_accepted")
        is not False
        or publication_core.get("controller_decryption_planned") is not False
        or not isinstance(publication_core.get("preparation_sha256"), dict)
        or set(publication_core["preparation_sha256"]) != set(SOURCE_ROLES)
        or any(
            SHA256_RE.fullmatch(str(value)) is None
            for value in publication_core["preparation_sha256"].values()
        )
        or not isinstance(objects, list)
        or len(objects) != 6
    ):
        raise CampaignSeedError("final publication core is invalid")
    object_keys = [row["object_key"] for row in preparation_core["objects"]]
    recipient_map = {
        role: {
            target: preparation_core["recipients"][target]["fingerprint"]
            for target in TARGET_RECIPIENTS[role]
        }
        for role in SOURCE_ROLES
    }
    if (
        publication_core["fixed_object_keys_sha256"] != _canonical_hash(object_keys)
        or publication_core["recipient_map_sha256"] != _canonical_hash(recipient_map)
    ):
        raise CampaignSeedError("final publication key/recipient commitment differs")
    for planned, published in zip(
        preparation_core["objects"],
        publication_core["objects"],
        strict=True,
    ):
        if (
            not isinstance(published, dict)
            or set(published)
            != {
                "source_role",
                "kind",
                "object_key",
                "plaintext_sha256",
                "plaintext_bytes",
                "recipient_fingerprints",
                "ciphertext_sha256",
                "ciphertext_bytes",
            }
            or any(
                published[field] != planned[field]
                for field in (
                    "source_role",
                    "kind",
                    "object_key",
                    "plaintext_sha256",
                    "plaintext_bytes",
                    "recipient_fingerprints",
                )
            )
            or SHA256_RE.fullmatch(str(published["ciphertext_sha256"])) is None
            or type(published["ciphertext_bytes"]) is not int
            or not (
                published["plaintext_bytes"]
                < published["ciphertext_bytes"]
                <= MAX_CIPHERTEXT_BYTES
            )
        ):
            raise CampaignSeedError("final publication ciphertext commitment is invalid")


def publication_contract_subject(
    publication_core: dict[str, Any],
    *,
    preparation_core: dict[str, Any],
) -> dict[str, Any]:
    _validate_publication_core(
        publication_core,
        preparation_core=preparation_core,
    )
    return approval_subject(
        artifact_type=PUBLICATION_CORE_SCHEMA,
        artifact_sha256=_canonical_hash(publication_core),
        release_sha=str(preparation_core["release_sha"]),
        bindings={
            "campaign_id": preparation_core["campaign_id"],
            "deployment_id": preparation_core["deployment_id"],
            "inventory_sha256": preparation_core["inventory"]["sha256"],
            "object_count": 6,
            "fixed_object_keys_sha256": publication_core[
                "fixed_object_keys_sha256"
            ],
            "recipient_map_sha256": publication_core["recipient_map_sha256"],
        },
    )


def seal_contract(
    *,
    preparation_core: dict[str, Any],
    publication_core: dict[str, Any],
    publication_approval: dict[str, Any],
    approval_policy: dict[str, Any],
    witness_relay_public_key: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    _validate_contract_core(preparation_core)
    _validate_publication_core(
        publication_core,
        preparation_core=preparation_core,
    )
    key_hash = hashlib.sha256((witness_relay_public_key.strip() + "\n").encode()).hexdigest()
    if (
        approval_policy_hash(approval_policy)
        != preparation_core["approval_policy_sha256"]
        or key_hash != preparation_core["witness_relay_public_key_sha256"]
    ):
        raise CampaignSeedError("publication approval trust inputs differ from the contract")
    if publication_approval.get("schema") != TOKEN_SCHEMA:
        raise CampaignSeedError(
            "seed publication requires one direct password-plus-possession approval token"
        )
    try:
        verified = verify_human_approval(
            publication_approval,
            policy_payload=approval_policy,
            expected_action="approve_seed_publication",
            expected_environment="staging",
            expected_subject=publication_contract_subject(
                publication_core,
                preparation_core=preparation_core,
            ),
            now=now,
            require_fresh=True,
            witness_relay_public_key=witness_relay_public_key.strip(),
        )
    except Exception as exc:
        raise CampaignSeedError("direct publication approval is invalid") from exc
    contract = {
        "schema": CONTRACT_SCHEMA,
        "core": preparation_core,
        "core_sha256": _canonical_hash(preparation_core),
        "publication_core": publication_core,
        "publication_core_sha256": _canonical_hash(publication_core),
        "approval_policy": approval_policy,
        "witness_relay_public_key": witness_relay_public_key.strip(),
        "publication_approval_token": publication_approval,
        "publication_approval": {
            "sha256": _canonical_hash(publication_approval),
            "approval_id": verified.approval_id,
            "expires_at": verified.expires_at.isoformat(),
            "action": "approve_seed_publication",
        },
    }
    _validate_contract(contract, now=now)
    return contract


def _validate_contract(
    contract: dict[str, Any],
    *,
    require_fresh_approval: bool = False,
    now: datetime | None = None,
) -> None:
    if (
        not isinstance(contract, dict)
        or set(contract) != {
            "schema",
            "core",
            "core_sha256",
            "publication_core",
            "publication_core_sha256",
            "approval_policy",
            "witness_relay_public_key",
            "publication_approval_token",
            "publication_approval",
        }
        or contract.get("schema") != CONTRACT_SCHEMA
        or not isinstance(contract.get("core"), dict)
        or contract.get("core_sha256") != _canonical_hash(contract["core"])
        or not isinstance(contract.get("publication_core"), dict)
        or contract.get("publication_core_sha256")
        != _canonical_hash(contract["publication_core"])
    ):
        raise CampaignSeedError("sealed publication contract is invalid")
    _validate_contract_core(contract["core"])
    _validate_publication_core(
        contract["publication_core"],
        preparation_core=contract["core"],
    )
    if (
        not isinstance(contract["approval_policy"], dict)
        or approval_policy_hash(contract["approval_policy"])
        != contract["core"]["approval_policy_sha256"]
        or not isinstance(contract["witness_relay_public_key"], str)
        or hashlib.sha256(
            (contract["witness_relay_public_key"].strip() + "\n").encode()
        ).hexdigest()
        != contract["core"]["witness_relay_public_key_sha256"]
        or not isinstance(contract["publication_approval_token"], dict)
        or contract["publication_approval_token"].get("schema") != TOKEN_SCHEMA
    ):
        raise CampaignSeedError("sealed publication trust material is invalid")
    try:
        witness_public = base64.b64decode(
            contract["witness_relay_public_key"].strip(),
            validate=True,
        )
    except (ValueError, binascii.Error) as exc:
        raise CampaignSeedError("sealed Witness relay public key is malformed") from exc
    if len(witness_public) != 32:
        raise CampaignSeedError("sealed Witness relay public key is malformed")
    try:
        verified = verify_human_approval(
            contract["publication_approval_token"],
            policy_payload=contract["approval_policy"],
            expected_action="approve_seed_publication",
            expected_environment="staging",
            expected_subject=publication_contract_subject(
                contract["publication_core"],
                preparation_core=contract["core"],
            ),
            now=now,
            require_fresh=require_fresh_approval,
            witness_relay_public_key=contract["witness_relay_public_key"].strip(),
        )
    except Exception as exc:
        raise CampaignSeedError("sealed publication approval signature is invalid") from exc
    approval = contract["publication_approval"]
    if (
        not isinstance(approval, dict)
        or set(approval) != {"sha256", "approval_id", "expires_at", "action"}
        or SHA256_RE.fullmatch(str(approval["sha256"])) is None
        or approval["sha256"]
        != _canonical_hash(contract["publication_approval_token"])
        or not str(approval["approval_id"])
        or approval["approval_id"] != verified.approval_id
        or approval["expires_at"] != verified.expires_at.isoformat()
        or approval["action"] != "approve_seed_publication"
    ):
        raise CampaignSeedError("sealed publication approval binding is invalid")
    try:
        expires = datetime.fromisoformat(str(approval["expires_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise CampaignSeedError("sealed publication approval expiry is invalid") from exc
    if expires.tzinfo is None:
        raise CampaignSeedError("sealed publication approval expiry lacks timezone")


def _verify_release_files(contract: dict[str, Any]) -> None:
    core = _preparation_core(contract)
    immutable = core["immutable_release"]
    root = Path(str(immutable["root"]))
    if (
        REPO_ROOT.resolve() != root.resolve()
        or Path(__file__).resolve()
        != (root / "scripts/publish_three_site_staging_seed_campaign.py").resolve()
    ):
        raise CampaignSeedError(
            "seed helper is not executing from the contract's immutable release"
        )
    for relative, expected in immutable["files"].items():
        actual, _size = _sha_regular_file(
            root / relative,
            label=f"immutable release file {relative}",
            maximum=4 * 1024 * 1024,
            require_private=False,
        )
        if actual != expected:
            raise CampaignSeedError(f"immutable release file changed: {relative}")


def _verify_backup(
    contract: dict[str, Any],
    *,
    role: str,
    verify_files: bool,
) -> dict[str, Any]:
    core = _preparation_core(contract)
    binding = core["backup_manifests"][role]
    backup = _secure_json(Path(str(binding["path"])), label=f"{role} backup manifest")
    if _canonical_hash(backup) != binding["sha256"]:
        raise CampaignSeedError(f"{role} backup manifest differs from the contract")
    try:
        verify_backup_manifest(
            backup,
            campaign_id=core["campaign_id"],
            source_role=role,
            source_release_sha=str(binding["source_release_sha"]),
            target_release_sha=core["release_sha"],
            verify_files=verify_files,
        )
    except Exception as exc:
        raise CampaignSeedError(f"{role} backup failed exact-release verification") from exc
    for kind in ARTIFACT_KINDS:
        artifact = backup["artifacts"][kind]
        if (
            artifact["sha256"] != binding["artifacts"][kind]["sha256"]
            or artifact["bytes"] != binding["artifacts"][kind]["bytes"]
        ):
            raise CampaignSeedError(f"{role} {kind} backup differs from the contract")
    return backup


def _run_age(arguments: list[str], *, timeout: int = 1800) -> None:
    if not Path(AGE).is_file():
        raise CampaignSeedError("age is unavailable at the fixed path")
    try:
        completed = subprocess.run(
            [AGE, *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CampaignSeedError("age encryption failed closed") from exc
    if completed.returncode != 0:
        raise CampaignSeedError("age encryption failed closed")


def _publish_ciphertext(
    *,
    source: Path,
    target: Path,
    recipients: list[str],
    encrypt: Callable[[list[str]], None],
) -> tuple[str, int]:
    if target.exists() or target.is_symlink():
        return sha256_secure_file(
            target,
            label=f"prepared ciphertext {target.name}",
            owner_uid=0,
            max_size=MAX_CIPHERTEXT_BYTES,
        )
    temporary = target.parent / f".{target.name}.encrypting"
    if temporary.exists() or temporary.is_symlink():
        metadata = temporary.lstat()
        if (
            stat.S_ISREG(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and metadata.st_uid == 0
        ):
            temporary.unlink()
        else:
            raise CampaignSeedError("interrupted ciphertext path is unsafe")
    arguments = ["--encrypt"]
    for recipient in recipients:
        arguments.extend(("--recipient", recipient))
    arguments.extend(("--output", str(temporary), str(source)))
    try:
        encrypt(arguments)
        temporary.chmod(0o600)
        digest, size = sha256_secure_file(
            temporary,
            label=f"prepared ciphertext {target.name}",
            owner_uid=0,
            max_size=MAX_CIPHERTEXT_BYTES,
        )
        os.link(temporary, target, follow_symlinks=False)
        directory = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return digest, size
    except FileExistsError as exc:
        raise CampaignSeedError("ciphertext publication raced with another writer") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _validate_preparation(
    contract: dict[str, Any],
    preparation: dict[str, Any],
    *,
    role: str,
) -> None:
    core = _preparation_core(contract)
    expected_plan = source_preparation_plan(core, role=role)
    if (
        not isinstance(preparation, dict)
        or set(preparation)
        != {
            "schema",
            "campaign_id",
            "release_sha",
            "preparation_core_sha256",
            "source_plan_sha256",
            "source_role",
            "source_machine_id",
            "recipient_fingerprints",
            "objects",
            "object_count",
            "object_storage_access_present",
            "publication_identity_input_accepted",
            "identity_path_policy",
        }
        or preparation.get("schema") != PREPARATION_SCHEMA
        or preparation.get("campaign_id") != core["campaign_id"]
        or preparation.get("release_sha") != core["release_sha"]
        or preparation.get("preparation_core_sha256") != _canonical_hash(core)
        or preparation.get("source_plan_sha256") != _canonical_hash(expected_plan)
        or preparation.get("source_role") != role
        or preparation.get("source_machine_id")
        != core["source_machine_ids"][role]
        or preparation.get("recipient_fingerprints")
        != {
            target: core["recipients"][target]["fingerprint"]
            for target in TARGET_RECIPIENTS[role]
        }
        or preparation.get("object_count") != 3
        or preparation.get("object_storage_access_present") is not False
        or preparation.get("publication_identity_input_accepted") is not False
        or not isinstance(preparation.get("objects"), list)
        or len(preparation["objects"]) != 3
    ):
        raise CampaignSeedError("source preparation identity or policy is invalid")
    forbidden_targets = [target for target in TARGET_ROLES if target != role]
    identity_policy = preparation.get("identity_path_policy")
    if (
        not isinstance(identity_policy, dict)
        or set(identity_policy)
        != {
            "allowed_local_target",
            "allowed_local_identity_path",
            "allowed_local_identity_present",
            "forbidden_target_roles",
            "forbidden_target_identity_paths_absent",
        }
        or identity_policy["allowed_local_target"] != role
        or identity_policy["allowed_local_identity_path"]
        != core["target_identity_paths"][role]
        or type(identity_policy["allowed_local_identity_present"]) is not bool
        or identity_policy["forbidden_target_roles"] != forbidden_targets
        or identity_policy["forbidden_target_identity_paths_absent"] is not True
    ):
        raise CampaignSeedError("source target-identity path evidence is invalid")
    for planned, prepared in zip(
        _role_rows(contract, role),
        preparation["objects"],
        strict=True,
    ):
        if (
            not isinstance(prepared, dict)
            or set(prepared)
            != {
                "source_role",
                "kind",
                "object_key",
                "plaintext_sha256",
                "plaintext_bytes",
                "ciphertext_sha256",
                "ciphertext_bytes",
                "ciphertext_name",
                "recipient_fingerprints",
            }
            or prepared["source_role"] != role
            or prepared["kind"] != planned["kind"]
            or prepared["object_key"] != planned["object_key"]
            or prepared["plaintext_sha256"] != planned["plaintext_sha256"]
            or prepared["plaintext_bytes"] != planned["plaintext_bytes"]
            or prepared["recipient_fingerprints"] != planned["recipient_fingerprints"]
            or SHA256_RE.fullmatch(str(prepared["ciphertext_sha256"])) is None
            or type(prepared["ciphertext_bytes"]) is not int
            or not (
                prepared["plaintext_bytes"]
                < prepared["ciphertext_bytes"]
                <= MAX_CIPHERTEXT_BYTES
            )
            or prepared["ciphertext_name"] != f"{planned['kind']}.age"
        ):
            raise CampaignSeedError("prepared ciphertext journal is invalid")


def prepare_role(
    *,
    source_plan: dict[str, Any],
    role: str,
    confirmation: str,
    machine_id_path: Path = Path("/etc/machine-id"),
    encrypt: Callable[[list[str]], None] = _run_age,
) -> dict[str, Any]:
    _validate_source_plan(source_plan, role=role)
    core = source_plan["preparation_core"]
    plan_hash = _canonical_hash(source_plan)
    if confirmation != source_confirmation_phrase(source_plan):
        raise CampaignSeedError("source preparation confirmation is missing or stale")
    if _machine_id(machine_id_path) != core["source_machine_ids"][role]:
        raise CampaignSeedError("source host machine id differs from the preparation core")
    forbidden_targets = [target for target in TARGET_ROLES if target != role]
    for target in forbidden_targets:
        forbidden = Path(str(core["target_identity_paths"][target]))
        if forbidden.exists() or forbidden.is_symlink():
            raise CampaignSeedError(
                f"private identity for forbidden target {target} exists on {role}"
            )
    allowed_identity = Path(str(core["target_identity_paths"][role]))
    allowed_identity_present = allowed_identity.exists() or allowed_identity.is_symlink()
    if allowed_identity_present:
        metadata = allowed_identity.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise CampaignSeedError("allowed local target identity path is unsafe")
    _verify_release_files(source_plan)
    _verify_backup(source_plan, role=role, verify_files=False)
    root = Path(str(core["source_state_roots"][role]))
    _ensure_private_directory(root)
    with _exclusive_lock(root):
        adoption_path = root / "preparation-plan-adoption.json"
        if adoption_path.exists() or adoption_path.is_symlink():
            adoption = _secure_json(
                adoption_path,
                label=f"{role} publication contract adoption",
            )
            if adoption != {
                "schema": "three-site-staging-seed-preparation-plan-adoption-v2",
                "preparation_core_sha256": _canonical_hash(core),
                "source_plan_sha256": plan_hash,
                "source_role": role,
                "operator_confirmation_sha256": hashlib.sha256(
                    (confirmation + "\n").encode()
                ).hexdigest(),
            }:
                raise CampaignSeedError("source preparation-plan adoption journal differs")
        else:
            adoption = {
                "schema": "three-site-staging-seed-preparation-plan-adoption-v2",
                "preparation_core_sha256": _canonical_hash(core),
                "source_plan_sha256": plan_hash,
                "source_role": role,
                "operator_confirmation_sha256": hashlib.sha256(
                    (confirmation + "\n").encode()
                ).hexdigest(),
            }
            _write_or_verify(
                adoption_path,
                _canonical_encoded(adoption),
                label=f"{role} publication contract adoption",
                canonical_json=True,
            )
        backup = _verify_backup(source_plan, role=role, verify_files=True)
        prepared_rows: list[dict[str, Any]] = []
        recipients = [
            core["recipients"][target]["recipient"]
            for target in TARGET_RECIPIENTS[role]
        ]
        for row in _role_rows(source_plan, role):
            kind = row["kind"]
            source = Path(str(backup["artifacts"][kind]["path"]))
            before = sha256_secure_file(
                source,
                label=f"{role} {kind} backup artifact",
                owner_uid=0,
                max_size=MAX_ARTIFACT_BYTES,
            )
            if before != (row["plaintext_sha256"], row["plaintext_bytes"]):
                raise CampaignSeedError("backup artifact changed before encryption")
            ciphertext = root / f"{kind}.age"
            sidecar = root / f"prepared-{kind}.json"
            if sidecar.exists() or sidecar.is_symlink():
                prepared = _secure_json(
                    sidecar,
                    label=f"{role} {kind} preparation sidecar",
                )
                digest, size = sha256_secure_file(
                    ciphertext,
                    label=f"{role} {kind} prepared ciphertext",
                    owner_uid=0,
                    max_size=MAX_CIPHERTEXT_BYTES,
                )
                if (
                    prepared.get("source_role") != role
                    or prepared.get("kind") != kind
                    or prepared.get("object_key") != row["object_key"]
                    or prepared.get("plaintext_sha256") != before[0]
                    or prepared.get("plaintext_bytes") != before[1]
                    or prepared.get("ciphertext_sha256") != digest
                    or prepared.get("ciphertext_bytes") != size
                    or prepared.get("recipient_fingerprints")
                    != row["recipient_fingerprints"]
                ):
                    raise CampaignSeedError("existing preparation sidecar differs")
            else:
                if ciphertext.exists() or ciphertext.is_symlink():
                    metadata = ciphertext.lstat()
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or stat.S_ISLNK(metadata.st_mode)
                        or metadata.st_uid != 0
                    ):
                        raise CampaignSeedError(
                            "interrupted prepared ciphertext path is unsafe"
                        )
                    ciphertext.unlink()
                digest, size = _publish_ciphertext(
                    source=source,
                    target=ciphertext,
                    recipients=recipients,
                    encrypt=encrypt,
                )
            after = sha256_secure_file(
                source,
                label=f"{role} {kind} backup artifact",
                owner_uid=0,
                max_size=MAX_ARTIFACT_BYTES,
            )
            if after != before:
                raise CampaignSeedError("backup artifact changed during encryption")
            if size <= before[1]:
                raise CampaignSeedError("age ciphertext size is inconsistent")
            prepared = {
                "source_role": role,
                "kind": kind,
                "object_key": row["object_key"],
                "plaintext_sha256": before[0],
                "plaintext_bytes": before[1],
                "ciphertext_sha256": digest,
                "ciphertext_bytes": size,
                "ciphertext_name": ciphertext.name,
                "recipient_fingerprints": row["recipient_fingerprints"],
            }
            _write_or_verify(
                sidecar,
                _canonical_encoded(prepared),
                label=f"{role} {kind} preparation sidecar",
                canonical_json=True,
            )
            prepared_rows.append(prepared)
        preparation = {
            "schema": PREPARATION_SCHEMA,
            "campaign_id": core["campaign_id"],
            "release_sha": core["release_sha"],
            "preparation_core_sha256": _canonical_hash(core),
            "source_plan_sha256": plan_hash,
            "source_role": role,
            "source_machine_id": core["source_machine_ids"][role],
            "recipient_fingerprints": {
                target: core["recipients"][target]["fingerprint"]
                for target in TARGET_RECIPIENTS[role]
            },
            "objects": prepared_rows,
            "object_count": 3,
            "object_storage_access_present": False,
            "publication_identity_input_accepted": False,
            "identity_path_policy": {
                "allowed_local_target": role,
                "allowed_local_identity_path": core["target_identity_paths"][role],
                "allowed_local_identity_present": allowed_identity_present,
                "forbidden_target_roles": forbidden_targets,
                "forbidden_target_identity_paths_absent": True,
            },
        }
        _validate_preparation(
            source_plan,
            preparation,
            role=role,
        )
        _write_or_verify(
            root / "role-preparation.json",
            _canonical_encoded(preparation),
            label=f"{role} preparation journal",
            canonical_json=True,
        )
        return preparation


def _copy_secure_new(
    source: Path,
    target: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
) -> None:
    if target.exists() or target.is_symlink():
        digest, size = sha256_secure_file(
            target,
            label=f"ingested ciphertext {target.name}",
            owner_uid=0,
            max_size=MAX_CIPHERTEXT_BYTES,
        )
        if (digest, size) != (expected_sha256, expected_bytes):
            raise CampaignSeedError("existing ingested ciphertext differs")
        return
    source_digest, source_size = sha256_secure_file(
        source,
        label=f"incoming ciphertext {source.name}",
        owner_uid=0,
        max_size=MAX_CIPHERTEXT_BYTES,
    )
    if (source_digest, source_size) != (expected_sha256, expected_bytes):
        raise CampaignSeedError("incoming ciphertext differs from preparation")
    temporary = target.parent / f".{target.name}.ingesting"
    if temporary.exists() or temporary.is_symlink():
        metadata = temporary.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0
        ):
            raise CampaignSeedError("interrupted ciphertext ingest path is unsafe")
        temporary.unlink()
    descriptor = os.open(
        temporary,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    source_descriptor = os.open(
        source,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    digest = hashlib.sha256()
    size = 0
    try:
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > expected_bytes:
                raise CampaignSeedError("incoming ciphertext grew while being copied")
            digest.update(chunk)
            view = memoryview(chunk)
            written = 0
            while written < len(view):
                count = os.write(descriptor, view[written:])
                if count <= 0:
                    raise CampaignSeedError("ciphertext copy made no progress")
                written += count
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except Exception:
        os.close(descriptor)
        descriptor = -1
        temporary.unlink(missing_ok=True)
        raise
    finally:
        os.close(source_descriptor)
        if descriptor >= 0:
            os.close(descriptor)
    if (digest.hexdigest(), size) != (expected_sha256, expected_bytes):
        temporary.unlink(missing_ok=True)
        raise CampaignSeedError("incoming ciphertext changed while being copied")
    try:
        os.link(temporary, target, follow_symlinks=False)
        directory = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileExistsError as exc:
        raise CampaignSeedError("ciphertext ingest raced with another writer") from exc
    finally:
        temporary.unlink(missing_ok=True)


def ingest_role(
    *,
    preparation_core: dict[str, Any],
    role: str,
    preparation_path: Path,
    ciphertext_root: Path,
    machine_id_path: Path = Path("/etc/machine-id"),
) -> dict[str, Any]:
    _validate_contract_core(preparation_core)
    _verify_release_files(preparation_core)
    preparation_core_sha256 = _canonical_hash(preparation_core)
    if role not in SOURCE_ROLES:
        raise CampaignSeedError("source role is invalid")
    if _machine_id(machine_id_path) != preparation_core["controller_machine_id"]:
        raise CampaignSeedError("controller machine id differs from the preparation core")
    preparation = _secure_json(preparation_path, label=f"{role} incoming preparation")
    _validate_preparation(
        preparation_core,
        preparation,
        role=role,
    )
    controller_root = Path(str(preparation_core["controller_state_root"]))
    role_root = controller_root / "incoming" / role
    _ensure_private_directory(controller_root)
    _ensure_private_directory(controller_root / "incoming")
    _ensure_private_directory(role_root)
    with _exclusive_lock(role_root):
        for row in preparation["objects"]:
            _copy_secure_new(
                ciphertext_root / row["ciphertext_name"],
                role_root / row["ciphertext_name"],
                expected_sha256=row["ciphertext_sha256"],
                expected_bytes=row["ciphertext_bytes"],
            )
        _write_or_verify(
            role_root / "role-preparation.json",
            _canonical_encoded(preparation),
            label=f"{role} controller preparation",
            canonical_json=True,
        )
    return {
        "status": "three-ciphertexts-ingested",
        "source_role": role,
        "preparation_core_sha256": preparation_core_sha256,
        "object_count": 3,
    }


def _client_error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return ""
    error = response.get("Error")
    return str(error.get("Code") or "") if isinstance(error, dict) else ""


def _credentials(path: Path) -> tuple[str, str]:
    payload = _secure_json(path, label="campaign Object Storage credentials")
    if (
        set(payload) != {"access_key", "secret_key"}
        or not isinstance(payload["access_key"], str)
        or len(payload["access_key"]) < 8
        or not isinstance(payload["secret_key"], str)
        or len(payload["secret_key"]) < 32
    ):
        raise CampaignSeedError("campaign Object Storage credentials are malformed")
    return payload["access_key"], payload["secret_key"]


def _new_client(access_key: str, secret_key: str):  # noqa: ANN201
    if boto3 is None or Config is None:
        raise CampaignSeedError("boto3 is unavailable")
    return boto3.client(
        "s3",
        endpoint_url=ARVAN_ENDPOINT,
        region_name=ARVAN_REGION,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(
            retries={
                "total_max_attempts": 1,
                "max_attempts": 0,
                "mode": "standard",
            }
        ),
    )


def _require_owner_only_acl(payload: dict[str, Any], *, label: str) -> None:
    owner = payload.get("Owner")
    grants = payload.get("Grants")
    owner_id = str(owner.get("ID") or "") if isinstance(owner, dict) else ""
    if not owner_id or not isinstance(grants, list) or len(grants) != 1:
        raise CampaignSeedError(f"{label} ACL is not strict owner-only")
    grant = grants[0]
    grantee = grant.get("Grantee") if isinstance(grant, dict) else None
    allowed_grantee_fields = {"Type", "ID", "DisplayName"}
    if (
        not isinstance(grant, dict)
        or set(grant) != {"Grantee", "Permission"}
        or grant.get("Permission") != "FULL_CONTROL"
        or not isinstance(grantee, dict)
        or not set(grantee).issubset(allowed_grantee_fields)
        or grantee.get("Type") != "CanonicalUser"
        or grantee.get("ID") != owner_id
    ):
        raise CampaignSeedError(f"{label} ACL is not strict owner-only")


def _require_private_versioned_bucket(client: Any, *, bucket: str) -> None:
    if client.get_bucket_versioning(Bucket=bucket).get("Status") != "Enabled":
        raise CampaignSeedError("seed bucket versioning is not enabled")
    _require_owner_only_acl(client.get_bucket_acl(Bucket=bucket), label="seed bucket")
    try:
        policy = client.get_bucket_policy_status(Bucket=bucket)
    except Exception as exc:
        if _client_error_code(exc) not in {"NoSuchBucketPolicy", "NoSuchPolicy"}:
            raise CampaignSeedError("seed bucket policy status is unavailable") from exc
    else:
        status = policy.get("PolicyStatus") if isinstance(policy, dict) else None
        if not isinstance(status, dict) or status.get("IsPublic") is not False:
            raise CampaignSeedError("seed bucket policy is public or ambiguous")
    try:
        encryption = client.get_bucket_encryption(Bucket=bucket)
    except Exception as exc:
        if _client_error_code(exc) not in {
            "ServerSideEncryptionConfigurationNotFoundError",
            "NoSuchEncryptionConfiguration",
        }:
            raise CampaignSeedError("seed bucket encryption status is unavailable") from exc
    else:
        rules = (
            encryption.get("ServerSideEncryptionConfiguration", {}).get("Rules")
            if isinstance(encryption, dict)
            else None
        )
        if rules:
            raise CampaignSeedError("provider-side default encryption is configured")


def _versions_for_key(
    client: Any,
    *,
    bucket: str,
    key: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    versions: list[dict[str, Any]] = []
    markers: list[dict[str, Any]] = []
    arguments: dict[str, Any] = {"Bucket": bucket, "Prefix": key}
    for _page in range(100):
        response = client.list_object_versions(**arguments)
        versions.extend(
            row for row in response.get("Versions", []) if row.get("Key") == key
        )
        markers.extend(
            row for row in response.get("DeleteMarkers", []) if row.get("Key") == key
        )
        if not response.get("IsTruncated"):
            return versions, markers
        next_key = response.get("NextKeyMarker")
        next_version = response.get("NextVersionIdMarker")
        if not next_key or not next_version:
            raise CampaignSeedError("version listing pagination is ambiguous")
        arguments["KeyMarker"] = next_key
        arguments["VersionIdMarker"] = next_version
    raise CampaignSeedError("version listing exceeds its safety bound")


def _load_controller_preparations(
    contract: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    core = _preparation_core(contract)
    root = Path(str(core["controller_state_root"]))
    preparations: dict[str, dict[str, Any]] = {}
    for role in SOURCE_ROLES:
        role_root = root / "incoming" / role
        preparation = _secure_json(
            role_root / "role-preparation.json",
            label=f"{role} controller preparation",
        )
        _validate_preparation(
            core,
            preparation,
            role=role,
        )
        for row in preparation["objects"]:
            digest, size = sha256_secure_file(
                role_root / row["ciphertext_name"],
                label=f"{role} controller ciphertext",
                owner_uid=0,
                max_size=MAX_CIPHERTEXT_BYTES,
            )
            if (digest, size) != (row["ciphertext_sha256"], row["ciphertext_bytes"]):
                raise CampaignSeedError("controller ciphertext differs from preparation")
        preparations[role] = preparation
    return preparations


def build_publication_core(
    *,
    preparation_core: dict[str, Any],
    preparations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    _validate_contract_core(preparation_core)
    if set(preparations) != set(SOURCE_ROLES):
        raise CampaignSeedError("both source preparations are required")
    prepared_by_key: dict[str, dict[str, Any]] = {}
    for role in SOURCE_ROLES:
        _validate_preparation(
            preparation_core,
            preparations[role],
            role=role,
        )
        for row in preparations[role]["objects"]:
            prepared_by_key[row["object_key"]] = row
    if len(prepared_by_key) != 6:
        raise CampaignSeedError("source preparation set is not exactly six")
    objects = []
    for planned in preparation_core["objects"]:
        prepared = prepared_by_key.get(planned["object_key"])
        if prepared is None:
            raise CampaignSeedError("final publication lacks a prepared ciphertext")
        objects.append(
            {
                **planned,
                "ciphertext_sha256": prepared["ciphertext_sha256"],
                "ciphertext_bytes": prepared["ciphertext_bytes"],
            }
        )
    keys = [row["object_key"] for row in preparation_core["objects"]]
    recipient_map = {
        role: {
            target: preparation_core["recipients"][target]["fingerprint"]
            for target in TARGET_RECIPIENTS[role]
        }
        for role in SOURCE_ROLES
    }
    publication_core = {
        "schema": PUBLICATION_CORE_SCHEMA,
        "campaign_id": preparation_core["campaign_id"],
        "release_sha": preparation_core["release_sha"],
        "deployment_id": preparation_core["deployment_id"],
        "inventory_sha256": preparation_core["inventory"]["sha256"],
        "preparation_core_sha256": _canonical_hash(preparation_core),
        "preparation_sha256": {
            role: _canonical_hash(preparations[role]) for role in SOURCE_ROLES
        },
        "objects": objects,
        "object_count": 6,
        "fixed_object_keys_sha256": _canonical_hash(keys),
        "recipient_map_sha256": _canonical_hash(recipient_map),
        "controller_publication_identity_input_accepted": False,
        "controller_decryption_planned": False,
    }
    _validate_publication_core(
        publication_core,
        preparation_core=preparation_core,
    )
    return publication_core


def establish_global_readiness(
    *,
    contract: dict[str, Any],
    contract_sha256: str,
    client: Any,
    machine_id_path: Path = Path("/etc/machine-id"),
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if contract_sha256 != _canonical_hash(contract):
        raise CampaignSeedError("sealed publication contract hash differs")
    _validate_contract(contract, now=now)
    _verify_release_files(contract)
    if _machine_id(machine_id_path) != contract["core"]["controller_machine_id"]:
        raise CampaignSeedError("controller machine id differs from the contract")
    root = Path(str(contract["core"]["controller_state_root"]))
    _ensure_private_directory(root)
    with _exclusive_lock(root):
        preparations = _load_controller_preparations(contract)
        expected_publication_core = build_publication_core(
            preparation_core=contract["core"],
            preparations=preparations,
        )
        if contract["publication_core"] != expected_publication_core:
            raise CampaignSeedError(
                "sealed publication core differs from six ingested ciphertexts"
            )
        readiness = {
            "schema": READINESS_SCHEMA,
            "campaign_id": contract["core"]["campaign_id"],
            "release_sha": contract["core"]["release_sha"],
            "publication_core_sha256": contract["publication_core_sha256"],
            "preparation_sha256": {
                role: _canonical_hash(preparations[role]) for role in SOURCE_ROLES
            },
            "ciphertext_sha256": {
                row["object_key"]: row["ciphertext_sha256"]
                for role in SOURCE_ROLES
                for row in preparations[role]["objects"]
            },
            "object_count": 6,
            "all_six_local_before_first_put": True,
            "controller_publication_identity_input_accepted": False,
            "controller_decryption_performed": False,
        }
        readiness_path = root / "global-readiness.json"
        _write_or_verify(
            readiness_path,
            _canonical_encoded(readiness),
            label="global six-object readiness",
            canonical_json=True,
        )
        _require_private_versioned_bucket(
            client,
            bucket=contract["core"]["storage"]["bucket"],
        )
        baseline_path = root / "global-remote-absence.json"
        if baseline_path.exists() or baseline_path.is_symlink():
            baseline = _secure_json(baseline_path, label="global remote absence baseline")
            expected = {
                "schema": BASELINE_SCHEMA,
                "campaign_id": contract["core"]["campaign_id"],
                "publication_core_sha256": contract["publication_core_sha256"],
                "readiness_sha256": _canonical_hash(readiness),
                "absent_keys": [
                    row["object_key"] for row in contract["core"]["objects"]
                ],
                "object_count": 6,
                "versioning": "Enabled",
                "strict_owner_only": True,
                "provider_side_sse": False,
                "captured_before_first_put": True,
            }
            if baseline != expected:
                raise CampaignSeedError("global remote absence baseline differs")
        else:
            _validate_contract(
                contract,
                require_fresh_approval=True,
                now=now,
            )
            # This complete six-key scan is deliberately finished before the
            # durable baseline and before the single PUT site can be reached.
            for row in contract["core"]["objects"]:
                versions, markers = _versions_for_key(
                    client,
                    bucket=contract["core"]["storage"]["bucket"],
                    key=row["object_key"],
                )
                if versions or markers:
                    raise CampaignSeedError(
                        "a fixed campaign seed key existed before global readiness"
                    )
            baseline = {
                "schema": BASELINE_SCHEMA,
                "campaign_id": contract["core"]["campaign_id"],
                "publication_core_sha256": contract["publication_core_sha256"],
                "readiness_sha256": _canonical_hash(readiness),
                "absent_keys": [
                    row["object_key"] for row in contract["core"]["objects"]
                ],
                "object_count": 6,
                "versioning": "Enabled",
                "strict_owner_only": True,
                "provider_side_sse": False,
                "captured_before_first_put": True,
            }
            _write_or_verify(
                baseline_path,
                _canonical_encoded(baseline),
                label="global remote absence baseline",
                canonical_json=True,
            )
        return readiness, baseline


def _no_sse(payload: dict[str, Any]) -> bool:
    return all(payload.get(field) in (None, "") for field in SSE_FIELDS) and (
        payload.get("BucketKeyEnabled") in (None, False)
    )


def _metadata(row: dict[str, Any], intent: str) -> dict[str, str]:
    return {
        "plaintext-sha256": str(row["plaintext_sha256"]),
        "ciphertext-sha256": str(row["ciphertext_sha256"]),
        "artifact-kind": str(row["kind"]),
        "publication-intent": intent,
    }


def _readback_started(root: Path, contract_sha256: str) -> dict[str, Any]:
    path = root / "readback-started.json"
    if path.exists() or path.is_symlink():
        value = _secure_json(path, label="first readback timestamp")
        if (
            set(value) != {"schema", "contract_sha256", "started_at"}
            or value["schema"] != "three-site-staging-seed-readback-start-v2"
            or value["contract_sha256"] != contract_sha256
        ):
            raise CampaignSeedError("first readback timestamp differs")
        return value
    value = {
        "schema": "three-site-staging-seed-readback-start-v2",
        "contract_sha256": contract_sha256,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_or_verify(
        path,
        _canonical_encoded(value),
        label="first readback timestamp",
        canonical_json=True,
    )
    return value


def _hash_body(stream: Any, *, maximum: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                raise CampaignSeedError("Object Storage readback exceeds its bound")
            digest.update(chunk)
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    return digest.hexdigest(), size


def _verify_remote_object(
    client: Any,
    *,
    bucket: str,
    row: dict[str, Any],
    version_id: str,
    intent: str,
    controller_root: Path,
    contract_sha256: str,
) -> dict[str, Any]:
    if not version_id or version_id == "null":
        raise CampaignSeedError("seed object lacks a non-null VersionId")
    expected_metadata = _metadata(row, intent)
    head = client.head_object(
        Bucket=bucket,
        Key=row["object_key"],
        VersionId=version_id,
    )
    if (
        str(head.get("VersionId") or "") != version_id
        or int(head.get("ContentLength") or -1) != row["ciphertext_bytes"]
        or head.get("ContentType") != "application/octet-stream"
        or head.get("Metadata") != expected_metadata
        or not _no_sse(head)
    ):
        raise CampaignSeedError("exact seed object HEAD differs")
    _require_owner_only_acl(
        client.get_object_acl(
            Bucket=bucket,
            Key=row["object_key"],
            VersionId=version_id,
        ),
        label="seed object",
    )
    _readback_started(controller_root, contract_sha256)
    response = client.get_object(
        Bucket=bucket,
        Key=row["object_key"],
        VersionId=version_id,
    )
    if (
        str(response.get("VersionId") or "") != version_id
        or int(response.get("ContentLength") or -1) != row["ciphertext_bytes"]
        or response.get("Metadata") != expected_metadata
        or not _no_sse(response)
    ):
        close = getattr(response.get("Body"), "close", None)
        if callable(close):
            close()
        raise CampaignSeedError("exact seed object GET differs")
    digest, size = _hash_body(response["Body"], maximum=MAX_CIPHERTEXT_BYTES)
    if (digest, size) != (row["ciphertext_sha256"], row["ciphertext_bytes"]):
        raise CampaignSeedError("exact seed ciphertext readback differs")
    return {
        "kind": row["kind"],
        "object_key": row["object_key"],
        "version_id": version_id,
        "plaintext_sha256": row["plaintext_sha256"],
        "plaintext_bytes": row["plaintext_bytes"],
        "ciphertext_sha256": digest,
        "ciphertext_bytes": size,
        "publication_intent": intent,
    }


def _prepared_by_key(
    preparations: dict[str, dict[str, Any]],
) -> dict[str, tuple[str, dict[str, Any]]]:
    result: dict[str, tuple[str, dict[str, Any]]] = {}
    for role in SOURCE_ROLES:
        for row in preparations[role]["objects"]:
            result[row["object_key"]] = (role, row)
    if len(result) != 6:
        raise CampaignSeedError("controller preparation set is not exactly six")
    return result


def _owned_publication_version_exists(
    *,
    contract: dict[str, Any],
    publication_core_sha256: str,
    client: Any,
    intents_root: Path,
) -> bool:
    bucket = contract["core"]["storage"]["bucket"]
    owned = False
    for row in contract["publication_core"]["objects"]:
        versions, markers = _versions_for_key(
            client,
            bucket=bucket,
            key=row["object_key"],
        )
        if markers or len(versions) > 1:
            raise CampaignSeedError("seed key has a delete marker or multiple versions")
        if not versions:
            continue
        intent_path = intents_root / f"{row['source_role']}-{row['kind']}.json"
        intent_document = _secure_json(
            intent_path,
            label="historical object PUT intent",
        )
        if (
            intent_document.get("publication_core_sha256")
            != publication_core_sha256
            or intent_document.get("object_key") != row["object_key"]
            or intent_document.get("ciphertext_sha256") != row["ciphertext_sha256"]
            or SHA256_RE.fullmatch(str(intent_document.get("intent") or "")) is None
        ):
            raise CampaignSeedError("remote version is not bound to an owned PUT intent")
        version_id = str(versions[0].get("VersionId") or "")
        head = client.head_object(
            Bucket=bucket,
            Key=row["object_key"],
            VersionId=version_id,
        )
        if (
            not version_id
            or version_id == "null"
            or str(head.get("VersionId") or "") != version_id
            or int(head.get("ContentLength") or -1) != row["ciphertext_bytes"]
            or head.get("Metadata")
            != _metadata(row, str(intent_document["intent"]))
            or not _no_sse(head)
        ):
            raise CampaignSeedError("remote version is foreign to the durable PUT intent")
        owned = True
    return owned


def publish_six(
    *,
    contract: dict[str, Any],
    contract_sha256: str,
    client: Any,
    machine_id_path: Path = Path("/etc/machine-id"),
    after_put: Callable[[dict[str, Any], str], None] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    readiness, baseline = establish_global_readiness(
        contract=contract,
        contract_sha256=contract_sha256,
        client=client,
        machine_id_path=machine_id_path,
        now=now,
    )
    root = Path(str(contract["core"]["controller_state_root"]))
    preparations = _load_controller_preparations(contract)
    by_key = _prepared_by_key(preparations)
    bucket = contract["core"]["storage"]["bucket"]
    receipts_root = root / "receipts"
    intents_root = root / "intents"
    _ensure_private_directory(receipts_root)
    _ensure_private_directory(intents_root)
    if not _owned_publication_version_exists(
        contract=contract,
        publication_core_sha256=contract["publication_core_sha256"],
        client=client,
        intents_root=intents_root,
    ):
        _validate_contract(
            contract,
            require_fresh_approval=True,
            now=now,
        )
    with _exclusive_lock(receipts_root):
        receipts: list[dict[str, Any]] = []
        for planned in contract["publication_core"]["objects"]:
            role, row = by_key[planned["object_key"]]
            ciphertext = root / "incoming" / role / row["ciphertext_name"]
            digest, size = sha256_secure_file(
                ciphertext,
                label=f"{role} {row['kind']} controller ciphertext",
                owner_uid=0,
                max_size=MAX_CIPHERTEXT_BYTES,
            )
            if (digest, size) != (row["ciphertext_sha256"], row["ciphertext_bytes"]):
                raise CampaignSeedError("controller ciphertext changed before publication")
            receipt_path = receipts_root / f"{role}-{row['kind']}.json"
            intent_path = intents_root / f"{role}-{row['kind']}.json"
            if intent_path.exists() or intent_path.is_symlink():
                intent_document = _secure_json(intent_path, label="object PUT intent")
            else:
                intent_document = {
                    "schema": "three-site-staging-seed-put-intent-v2",
                    "publication_core_sha256": contract[
                        "publication_core_sha256"
                    ],
                    "object_key": row["object_key"],
                    "ciphertext_sha256": row["ciphertext_sha256"],
                    "intent": secrets.token_hex(32),
                }
                _write_or_verify(
                    intent_path,
                    _canonical_encoded(intent_document),
                    label="object PUT intent",
                    canonical_json=True,
                )
            if (
                set(intent_document)
                != {
                    "schema",
                    "publication_core_sha256",
                    "object_key",
                    "ciphertext_sha256",
                    "intent",
                }
                or intent_document["schema"] != "three-site-staging-seed-put-intent-v2"
                or intent_document["publication_core_sha256"]
                != contract["publication_core_sha256"]
                or intent_document["object_key"] != row["object_key"]
                or intent_document["ciphertext_sha256"] != row["ciphertext_sha256"]
                or SHA256_RE.fullmatch(str(intent_document["intent"])) is None
            ):
                raise CampaignSeedError("durable object PUT intent differs")
            intent = str(intent_document["intent"])
            versions, markers = _versions_for_key(client, bucket=bucket, key=row["object_key"])
            if markers or len(versions) > 1:
                raise CampaignSeedError("seed key has a delete marker or multiple versions")
            if receipt_path.exists() or receipt_path.is_symlink():
                receipt = _secure_json(receipt_path, label="object publication receipt")
                version_id = str(receipt.get("version_id") or "")
                if (
                    set(receipt)
                    != {
                        "schema",
                        "contract_sha256",
                        "source_role",
                        "kind",
                        "version_id",
                        "intent",
                        "object",
                        "initial_global_absence_proven",
                        "conditional_create",
                    }
                    or receipt["schema"]
                    != "three-site-staging-seed-object-publication-v2"
                    or receipt["contract_sha256"] != contract_sha256
                    or receipt["source_role"] != role
                    or receipt["kind"] != row["kind"]
                    or receipt["intent"] != intent
                    or receipt["initial_global_absence_proven"] is not True
                    or receipt["conditional_create"] is not True
                    or len(versions) != 1
                    or str(versions[0].get("VersionId") or "") != version_id
                ):
                    raise CampaignSeedError("durable receipt differs from remote state")
                verified = _verify_remote_object(
                    client,
                    bucket=bucket,
                    row=row,
                    version_id=version_id,
                    intent=intent,
                    controller_root=root,
                    contract_sha256=contract_sha256,
                )
                if receipt["object"] != verified:
                    raise CampaignSeedError("durable receipt content differs")
                receipts.append(receipt)
                continue
            response_version = ""
            if not versions:
                try:
                    with ciphertext.open("rb") as body:
                        response = client.put_object(
                            Bucket=bucket,
                            Key=row["object_key"],
                            Body=body,
                            ContentLength=row["ciphertext_bytes"],
                            ContentType="application/octet-stream",
                            Metadata=_metadata(row, intent),
                            ACL="private",
                            IfNoneMatch="*",
                        )
                    response_version = str(response.get("VersionId") or "")
                    if after_put is not None:
                        after_put(row, response_version)
                except Exception:
                    # The only safe ambiguity is a committed request with this
                    # exact durable unpredictable intent.  Never blind-retry in
                    # the same invocation.
                    response_version = ""
                versions, markers = _versions_for_key(
                    client,
                    bucket=bucket,
                    key=row["object_key"],
                )
            if markers or len(versions) != 1:
                raise CampaignSeedError("conditional PUT did not yield exactly one version")
            version_id = str(versions[0].get("VersionId") or "")
            if response_version and response_version != version_id:
                raise CampaignSeedError("PUT VersionId differs from version listing")
            verified = _verify_remote_object(
                client,
                bucket=bucket,
                row=row,
                version_id=version_id,
                intent=intent,
                controller_root=root,
                contract_sha256=contract_sha256,
            )
            receipt = {
                "schema": "three-site-staging-seed-object-publication-v2",
                "contract_sha256": contract_sha256,
                "source_role": role,
                "kind": row["kind"],
                "version_id": version_id,
                "intent": intent,
                "object": verified,
                "initial_global_absence_proven": True,
                "conditional_create": True,
            }
            _write_or_verify(
                receipt_path,
                _canonical_encoded(receipt),
                label="object publication receipt",
                canonical_json=True,
            )
            receipts.append(receipt)
        result = {
            "schema": "three-site-staging-seed-six-publication-v2",
            "status": "six-private-versioned-ciphertexts-readback-verified",
            "campaign_id": contract["core"]["campaign_id"],
            "release_sha": contract["core"]["release_sha"],
            "contract_sha256": contract_sha256,
            "readiness_sha256": _canonical_hash(readiness),
            "baseline_sha256": _canonical_hash(baseline),
            "objects": [receipt["object"] for receipt in receipts],
            "object_count": 6,
            "conditional_create": True,
            "object_overwrite": False,
            "object_delete": False,
            "provider_side_sse": False,
            "controller_publication_identity_input_accepted": False,
            "controller_decryption_performed": False,
        }
        _write_or_verify(
            root / "six-publication.json",
            _canonical_encoded(result),
            label="six-object publication result",
            canonical_json=True,
        )
        return result


def finalize_manifests(
    *,
    contract: dict[str, Any],
    contract_sha256: str,
    publication: dict[str, Any],
) -> dict[str, Any]:
    if contract_sha256 != _canonical_hash(contract):
        raise CampaignSeedError("sealed publication contract hash differs")
    _validate_contract(contract)
    _verify_release_files(contract)
    root = Path(str(contract["core"]["controller_state_root"]))
    durable = _secure_json(root / "six-publication.json", label="six-object publication result")
    if durable != publication or publication.get("contract_sha256") != contract_sha256:
        raise CampaignSeedError("publication result differs from the durable controller state")
    if publication.get("object_count") != 6 or len(publication.get("objects", [])) != 6:
        raise CampaignSeedError("publication result is not exactly six objects")
    readback_start = _secure_json(
        root / "readback-started.json",
        label="first readback timestamp",
    )
    final_root = root / "final"
    _ensure_private_directory(final_root)
    by_role = {
        role: [
            row for row in publication["objects"] if row["object_key"].startswith(
                _seed_role_prefix(
                    prefix=contract["core"]["storage"]["prefix"],
                    campaign_id=contract["core"]["campaign_id"],
                    release_sha=contract["core"]["release_sha"],
                    role=role,
                )
            )
        ]
        for role in SOURCE_ROLES
    }
    outputs: dict[str, Any] = {}
    for role in SOURCE_ROLES:
        if [row["kind"] for row in by_role[role]] != list(ARTIFACT_KINDS):
            raise CampaignSeedError("published role object order is invalid")
        readback = {
            "schema": "three-site-staging-seed-readback-v2",
            "campaign_id": contract["core"]["campaign_id"],
            "release_sha": contract["core"]["release_sha"],
            "source_role": role,
            "verified_at": readback_start["started_at"],
            "verification": "controller-ciphertext-readback",
            "plaintext_end_to_end_verification": "deferred-to-target-fetch",
            "objects": [
                {
                    "kind": row["kind"],
                    "object_key": row["object_key"],
                    "version_id": row["version_id"],
                    "ciphertext_sha256": row["ciphertext_sha256"],
                    "plaintext_sha256": row["plaintext_sha256"],
                }
                for row in by_role[role]
            ],
        }
        readback_hash = _canonical_hash(readback)
        recipient_fingerprints = {
            target: contract["core"]["recipients"][target]["fingerprint"]
            for target in TARGET_RECIPIENTS[role]
        }
        manifest = {
            "schema": "three-site-staging-seed-manifest-v2",
            "campaign_id": contract["core"]["campaign_id"],
            "release_sha": contract["core"]["release_sha"],
            "source_role": role,
            "bucket": contract["core"]["storage"]["bucket"],
            "object_prefix": _seed_role_prefix(
                prefix=contract["core"]["storage"]["prefix"],
                campaign_id=contract["core"]["campaign_id"],
                release_sha=contract["core"]["release_sha"],
                role=role,
            ),
            "encryption": "age-x25519-multi-recipient",
            "recipient_fingerprints": recipient_fingerprints,
            "objects": by_role[role],
            "readback_evidence_sha256": readback_hash,
        }
        role_root = final_root / role
        _ensure_private_directory(role_root)
        _write_or_verify(
            role_root / "readback.json",
            _pretty_encoded(readback),
            label=f"{role} readback evidence",
        )
        _write_or_verify(
            role_root / "seed-manifest.json",
            _pretty_encoded(manifest),
            label=f"{role} seed manifest",
        )
        outputs[role] = {
            "manifest": str(role_root / "seed-manifest.json"),
            "manifest_sha256": _canonical_hash(manifest),
            "readback": str(role_root / "readback.json"),
            "readback_evidence_sha256": readback_hash,
        }
    result = {
        "status": "consumer-v2-manifests-finalized",
        "campaign_id": contract["core"]["campaign_id"],
        "release_sha": contract["core"]["release_sha"],
        "contract_sha256": contract_sha256,
        "object_count": 6,
        "controller_decryption": False,
        "manifests": outputs,
    }
    _write_or_verify(
        final_root / "finalization.json",
        _canonical_encoded(result),
        label="seed manifest finalization",
        canonical_json=True,
    )
    return result


def _path_mapping(
    values: list[str],
    *,
    expected: set[str],
    label: str,
) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if (
            not separator
            or name not in expected
            or name in result
            or not Path(raw_path).is_absolute()
        ):
            raise CampaignSeedError(f"{label} must be one unique role=/absolute/path")
        result[name] = Path(raw_path)
    if set(result) != expected:
        raise CampaignSeedError(f"{label} set is incomplete")
    return result


def _load_canonical_document(path: Path, *, label: str) -> dict[str, Any]:
    payload = _secure_json(path, label=label)
    if read_secure_bytes(
        path,
        label=label,
        owner_uid=0,
        max_size=MAX_JSON_BYTES,
    ) != _canonical_encoded(payload):
        raise CampaignSeedError(f"{label} is not canonical JSON")
    return payload


def _main_preparation_core(args: argparse.Namespace) -> dict[str, Any]:
    inventory = _secure_json(args.inventory, label="provisioned inventory")
    inventory_approval = _secure_json(
        args.inventory_approval,
        label="provisioned inventory approval",
    )
    approval_policy = _secure_json(args.approval_policy, label="human approval policy")
    witness_key = read_secure_text(
        args.witness_relay_public_key,
        label="campaign Witness relay public key",
        owner_uid=0,
        max_size=4096,
    ).strip()
    inventory_result = verify_inventory_approval_explicit(
        inventory=inventory,
        inventory_approval=inventory_approval,
        approval_policy=approval_policy,
        witness_relay_public_key=witness_key,
    )
    backup_paths = _path_mapping(
        args.backup_manifest,
        expected=set(SOURCE_ROLES),
        label="--backup-manifest",
    )
    backups = {
        role: _secure_json(path, label=f"{role} backup manifest")
        for role, path in backup_paths.items()
    }
    recipient_paths = _path_mapping(
        args.recipient,
        expected=set(TARGET_ROLES),
        label="--recipient",
    )
    recipient_values = {
        role: read_secure_text(
            path,
            label=f"{role} public age recipient",
            owner_uid=0,
            max_size=4096,
        ).strip()
        for role, path in recipient_paths.items()
    }
    source_state_roots = _path_mapping(
        args.source_state_root,
        expected=set(SOURCE_ROLES),
        label="--source-state-root",
    )
    target_identity_paths = _path_mapping(
        args.target_identity_path,
        expected=set(TARGET_ROLES),
        label="--target-identity-path",
    )
    immutable = _immutable_release_binding(
        args.release_root.resolve(),
        inventory_result["release_sha"],
    )
    core = build_preparation_core(
        inventory=inventory,
        inventory_result=inventory_result,
        inventory_approval=inventory_approval,
        backups=backups,
        backup_paths=backup_paths,
        recipient_values=recipient_values,
        immutable_release=immutable,
        source_state_roots=source_state_roots,
        controller_state_root=args.controller_state_root,
        target_identity_paths=target_identity_paths,
    )
    _write_or_verify(
        args.output_core,
        _canonical_encoded(core),
        label="seed preparation core",
        canonical_json=True,
    )
    output_plans = _path_mapping(
        args.output_source_plan,
        expected=set(SOURCE_ROLES),
        label="--output-source-plan",
    )
    confirmations = {}
    for role in SOURCE_ROLES:
        plan = source_preparation_plan(core, role=role)
        _write_or_verify(
            output_plans[role],
            _canonical_encoded(plan),
            label=f"{role} source preparation plan",
            canonical_json=True,
        )
        confirmations[role] = source_confirmation_phrase(plan)
    return {
        "status": "source-preparation-plans-created",
        "campaign_id": core["campaign_id"],
        "release_sha": core["release_sha"],
        "preparation_core_sha256": _canonical_hash(core),
        "source_confirmations": confirmations,
        "publication_approval_required": False,
    }


def _main_publication_subject(args: argparse.Namespace) -> dict[str, Any]:
    core = _load_canonical_document(
        args.preparation_core,
        label="seed preparation core",
    )
    _validate_contract_core(core)
    _verify_release_files(core)
    if _machine_id(args.machine_id_file) != core["controller_machine_id"]:
        raise CampaignSeedError("controller machine id differs from the preparation core")
    preparations = _load_controller_preparations(core)
    publication_core = build_publication_core(
        preparation_core=core,
        preparations=preparations,
    )
    subject = publication_contract_subject(
        publication_core,
        preparation_core=core,
    )
    _write_or_verify(
        args.output_publication_core,
        _canonical_encoded(publication_core),
        label="final publication core",
        canonical_json=True,
    )
    _write_or_verify(
        args.output_subject,
        _canonical_encoded(subject),
        label="final publication approval subject",
        canonical_json=True,
    )
    return {
        "status": "awaiting-direct-publication-approval",
        "campaign_id": core["campaign_id"],
        "release_sha": core["release_sha"],
        "publication_core_sha256": _canonical_hash(publication_core),
        "approval_action": "approve_seed_publication",
        "subject": str(args.output_subject),
    }


def _main_seal_contract(args: argparse.Namespace) -> dict[str, Any]:
    preparation_core = _load_canonical_document(
        args.preparation_core,
        label="seed preparation core",
    )
    publication_core = _load_canonical_document(
        args.publication_core,
        label="final publication core",
    )
    publication_approval = _secure_json(
        args.publication_approval,
        label="direct publication approval",
    )
    approval_policy = _secure_json(args.approval_policy, label="human approval policy")
    witness_key = read_secure_text(
        args.witness_relay_public_key,
        label="campaign Witness relay public key",
        owner_uid=0,
        max_size=4096,
    ).strip()
    contract = seal_contract(
        preparation_core=preparation_core,
        publication_core=publication_core,
        publication_approval=publication_approval,
        approval_policy=approval_policy,
        witness_relay_public_key=witness_key,
    )
    _write_or_verify(
        args.output,
        _canonical_encoded(contract),
        label="sealed campaign seed contract",
        canonical_json=True,
    )
    return {
        "status": "sealed",
        "campaign_id": preparation_core["campaign_id"],
        "release_sha": preparation_core["release_sha"],
        "contract_sha256": _canonical_hash(contract),
        "contract": str(args.output),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_core = subparsers.add_parser("preparation-core")
    prepare_core.add_argument("--inventory", type=Path, required=True)
    prepare_core.add_argument("--inventory-approval", type=Path, required=True)
    prepare_core.add_argument("--approval-policy", type=Path, required=True)
    prepare_core.add_argument("--witness-relay-public-key", type=Path, required=True)
    prepare_core.add_argument("--backup-manifest", action="append", required=True)
    prepare_core.add_argument("--recipient", action="append", required=True)
    prepare_core.add_argument("--release-root", type=Path, required=True)
    prepare_core.add_argument("--source-state-root", action="append", required=True)
    prepare_core.add_argument("--target-identity-path", action="append", required=True)
    prepare_core.add_argument("--controller-state-root", type=Path, required=True)
    prepare_core.add_argument("--output-core", type=Path, required=True)
    prepare_core.add_argument("--output-source-plan", action="append", required=True)

    publication_subject = subparsers.add_parser("publication-subject")
    publication_subject.add_argument("--preparation-core", type=Path, required=True)
    publication_subject.add_argument(
        "--output-publication-core",
        type=Path,
        required=True,
    )
    publication_subject.add_argument("--output-subject", type=Path, required=True)
    publication_subject.add_argument(
        "--machine-id-file",
        type=Path,
        default=Path("/etc/machine-id"),
    )

    seal = subparsers.add_parser("seal-contract")
    seal.add_argument("--preparation-core", type=Path, required=True)
    seal.add_argument("--publication-core", type=Path, required=True)
    seal.add_argument("--publication-approval", type=Path, required=True)
    seal.add_argument("--approval-policy", type=Path, required=True)
    seal.add_argument("--witness-relay-public-key", type=Path, required=True)
    seal.add_argument("--output", type=Path, required=True)

    validate = subparsers.add_parser("validate-contract")
    validate.add_argument("--contract", type=Path, required=True)

    prepare = subparsers.add_parser("prepare-role")
    prepare.add_argument("--source-plan", type=Path, required=True)
    prepare.add_argument("--source-role", choices=SOURCE_ROLES, required=True)
    prepare.add_argument("--confirm", required=True)
    prepare.add_argument("--machine-id-file", type=Path, default=Path("/etc/machine-id"))

    ingest = subparsers.add_parser("ingest-role")
    ingest.add_argument("--preparation-core", type=Path, required=True)
    ingest.add_argument("--source-role", choices=SOURCE_ROLES, required=True)
    ingest.add_argument("--preparation", type=Path, required=True)
    ingest.add_argument("--ciphertext-root", type=Path, required=True)
    ingest.add_argument("--machine-id-file", type=Path, default=Path("/etc/machine-id"))

    publish = subparsers.add_parser("publish")
    publish.add_argument("--contract", type=Path, required=True)
    publish.add_argument("--credentials", type=Path, required=True)
    publish.add_argument("--machine-id-file", type=Path, default=Path("/etc/machine-id"))

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--contract", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "preparation-core":
            result = _main_preparation_core(args)
        elif args.command == "publication-subject":
            result = _main_publication_subject(args)
        elif args.command == "seal-contract":
            result = _main_seal_contract(args)
        elif args.command == "prepare-role":
            source_plan = _load_canonical_document(
                args.source_plan,
                label="source preparation plan",
            )
            result = prepare_role(
                source_plan=source_plan,
                role=args.source_role,
                confirmation=args.confirm,
                machine_id_path=args.machine_id_file,
            )
        elif args.command == "ingest-role":
            preparation_core = _load_canonical_document(
                args.preparation_core,
                label="seed preparation core",
            )
            result = ingest_role(
                preparation_core=preparation_core,
                role=args.source_role,
                preparation_path=args.preparation,
                ciphertext_root=args.ciphertext_root,
                machine_id_path=args.machine_id_file,
            )
        else:
            contract, contract_sha256 = _load_canonical_contract(args.contract)
            if args.command == "validate-contract":
                result = {
                    "status": "valid",
                    "campaign_id": contract["core"]["campaign_id"],
                    "release_sha": contract["core"]["release_sha"],
                    "contract_sha256": contract_sha256,
                    "fixed_object_count": 6,
                }
            elif args.command == "publish":
                access_key, secret_key = _credentials(args.credentials)
                result = publish_six(
                    contract=contract,
                    contract_sha256=contract_sha256,
                    client=_new_client(access_key, secret_key),
                    machine_id_path=args.machine_id_file,
                )
            else:
                publication = _secure_json(
                    Path(str(contract["core"]["controller_state_root"]))
                    / "six-publication.json",
                    label="six-object publication result",
                )
                result = finalize_manifests(
                    contract=contract,
                    contract_sha256=contract_sha256,
                    publication=publication,
                )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
