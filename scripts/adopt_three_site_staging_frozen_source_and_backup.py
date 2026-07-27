#!/usr/bin/env python3
"""Adopt one already-frozen legacy source and create verified fresh backups.

All campaign, release, approval, historical-chain, image, path, and source
volume identities come from one root-owned mode-0600 adoption contract.  The
contract and provisioned-inventory approval are provenance, not operational
authority: apply additionally requires a short-lived, action-specific
``approve_source_adoption_backup`` token over the exact contract bytes.

The default mode is read-only ``plan``.  ``apply`` requires the exact printed
confirmation phrase.  It never starts, stops, or recreates a Compose service,
never pulls or builds an image, never restores Redis, and never overwrites an
output.  The only containers it may create are two sequential read-only archive
workers and one isolated PostgreSQL 15 restore worker.  One named scratch
volume is allowed and all temporary resources must be proven absent on return.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import signal
import stat
import subprocess
import sys
import tarfile
import time
from typing import Any, Callable

sys.dont_write_bytecode = True

import yaml


CURRENT_CAMPAIGN_ID = ""
CURRENT_RELEASE_SHA = ""
CURRENT_DEPLOYMENT_ID = ""
CURRENT_HOST_SAFETY_MODE = ""
CURRENT_CAMPAIGN_ROOT = Path("/")
CURRENT_RELEASE_ROOT = Path("/")
CURRENT_INVENTORY_PATH = Path("/")
CURRENT_INVENTORY_RAW_SHA256 = ""
CURRENT_INVENTORY_SHA256 = ""
CURRENT_APPROVAL_POLICY_PATH = Path("/")
CURRENT_APPROVAL_POLICY_RAW_SHA256 = ""
CURRENT_APPROVAL_POLICY_SHA256 = ""
CURRENT_APPROVAL_PUBLIC_KEY_SHA256 = ""
SCRATCH_POSTGRES_IMAGE_ID = ""
SCRATCH_POSTGRES_ENTRYPOINT: tuple[str, ...] | None = None
SCRATCH_POSTGRES_CMD: tuple[str, ...] | None = None
ADOPTION_CONTRACT_PATH = Path("/")
ADOPTION_CONTRACT_SHA256 = ""
EXPECTED_HELPER_PATH = Path("/")
EXPECTED_HELPER_SHA256 = ""

HISTORICAL_CAMPAIGN_ID = ""
HISTORICAL_TARGET_RELEASE_SHA = ""
SOURCE_RELEASE_SHA = ""
HISTORICAL_ROOT = Path("/")
ROLLBACK_STORAGE_ROOT = Path("/")

DOCKER = "/usr/bin/docker"
GIT = "/usr/bin/git"
DATA_SERVICES = frozenset({"db", "redis"})
IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SAFE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
VOLUME_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
TEMP_LABEL_KEY = "trading-bot.three-site-staging.frozen-source-backup"
SOURCE_ADOPTION_ACTION = "approve_source_adoption_backup"
SOURCE_ADOPTION_ARTIFACT_TYPE = "three-site-staging-source-adoption-backup-v1"
SOURCE_ADOPTION_OPERATION = "read-only-backup-restore-verify-zero-residue"
SOURCE_ADOPTION_OUTPUT_INTENT = (
    "create-exclusive-backup-evidence-under-campaign-root"
)
MAX_TEMPORARY_CONTAINERS = 3
MAX_SCRATCH_VOLUMES = 1
EXPECTED_SOURCE_VOLUMES: dict[str, dict[str, tuple[str, str]]] = {}
SAFE_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/root",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
}
INVENTORY_APPROVAL_BINDING_FIELDS = frozenset(
    {
        "approval_id",
        "approval_token_sha256",
        "approval_expires_at",
        "inventory_sha256",
        "approval_policy_sha256",
    }
)
SOURCE_ADOPTION_APPROVAL_BINDING_FIELDS = frozenset(
    {
        "action",
        "environment",
        "approval_path",
        "approval_id",
        "approval_token_sha256",
        "approval_token_raw_sha256",
        "approval_issued_at",
        "approval_expires_at",
        "approval_policy_sha256",
        "approval_subject_sha256",
        "adoption_contract_sha256",
    }
)
RESOURCE_JOURNAL_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "release_sha",
        "deployment_id",
        "host_safety_mode",
        "source_role",
        "run_id",
        "resource_prefix",
        "adoption_contract",
        "inventory_approval",
        "source_adoption_approval",
        "historical_freeze_sha256",
        "source_snapshot_sha256",
        "source_measurement_sha256",
        "protected_source_identities",
        "operation_label",
        "allowed_containers",
        "allowed_named_volume",
        "container_limit",
        "named_volume_limit",
        "intent_persisted_before_docker_side_effect",
        "automatic_cleanup_scope",
        "created_at",
    }
)


@dataclass(frozen=True)
class HistoricalContract:
    role: str
    project: str
    app_service: str
    env_file: Path
    env_sha256: str
    inventory_approval: Path
    expected_approval_id: str
    expected_approval_token_sha256: str
    expected_approval_raw_sha256: str
    source_adoption_subject: Path
    source_adoption_approval: Path
    output_dir: Path
    run_id: str
    restore_evidence_path: Path
    restore_evidence_sha256: str
    adopted_freeze_path: Path
    adopted_freeze_sha256: str
    freeze_evidence_path: Path
    freeze_evidence_sha256: str
    restore_bundle_path: Path
    restore_bundle_sha256: str
    compose_path: Path
    compose_sha256: str
    service_images: tuple[tuple[str, str], ...]


CONTRACTS: dict[str, HistoricalContract] = {}


class AdoptionError(RuntimeError):
    """Fail-closed operational validation error."""


class ControlledInterruption(AdoptionError):
    """Raised by scoped apply-mode signal handlers."""


MIN_APPROVAL_REMAINING_SECONDS = 20 * 60
_ACTIVE_CHILD: subprocess.Popen[Any] | None = None


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AdoptionError("JSON contains a duplicate key")
        result[key] = value
    return result


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _utc(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdoptionError(f"{label} timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise AdoptionError(f"{label} timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def _secure_bytes(
    path: Path,
    *,
    maximum: int,
    expected_mode: int | tuple[int, ...] = 0o600,
) -> bytes:
    """Read one owner-only, non-linked file without following a final symlink."""
    if os.geteuid() != 0:
        raise AdoptionError("secure adoption inputs require effective uid 0")
    if not path.is_absolute() or ".." in path.parts:
        raise AdoptionError("secure input path must be absolute")
    for ancestor in reversed(path.parents):
        try:
            metadata = ancestor.lstat()
        except OSError as exc:
            raise AdoptionError(f"cannot inspect secure input ancestor: {ancestor}") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        sticky_root_directory = metadata.st_uid == 0 and bool(mode & stat.S_ISVTX)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0
            or (mode & 0o022 and not sticky_root_directory)
        ):
            raise AdoptionError(f"secure input ancestor is unsafe: {ancestor}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AdoptionError(f"cannot securely open input: {path}") from exc
    try:
        before = os.fstat(descriptor)
        allowed_modes = (
            (expected_mode,) if isinstance(expected_mode, int) else expected_mode
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) not in allowed_modes
            or not 1 <= before.st_size <= maximum
        ):
            raise AdoptionError(f"secure input mode/owner/size is invalid: {path}")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise AdoptionError(f"secure input changed during read: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise AdoptionError(f"secure input grew during read: {path}")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise AdoptionError(f"secure input changed during read: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _secure_json(path: Path, *, maximum: int = 1024 * 1024) -> tuple[dict[str, Any], bytes]:
    raw = _secure_bytes(path, maximum=maximum)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdoptionError(f"secure JSON is invalid: {path.name}") from exc
    if not isinstance(value, dict):
        raise AdoptionError(f"secure JSON root is not an object: {path.name}")
    return value, raw


def _absolute_contract_path(value: Any, *, label: str) -> Path:
    path = Path(str(value))
    if not path.is_absolute() or ".." in path.parts:
        raise AdoptionError(f"{label} must be an absolute normalized path")
    return path


def _exact_fields(value: Any, expected: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise AdoptionError(f"{label} fields are invalid")
    return value


def _command_vector(
    value: Any, *, label: str
) -> tuple[str, ...] | None:
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or len(value) > 128
        or any(
            not isinstance(item, str)
            or not item
            or "\x00" in item
            or len(item) > 4096
            for item in value
        )
    ):
        raise AdoptionError(f"{label} command vector is invalid")
    return tuple(value)


def _load_adoption_contract(path: Path) -> dict[str, Any]:
    payload, raw = _secure_json(path)
    return _install_adoption_contract(path=path, payload=payload, raw=raw)


def _install_adoption_contract(
    *, path: Path, payload: dict[str, Any], raw: bytes
) -> dict[str, Any]:
    """Validate and install one generic, fully bound source-adoption contract."""
    global ADOPTION_CONTRACT_PATH, ADOPTION_CONTRACT_SHA256
    global CURRENT_CAMPAIGN_ID, CURRENT_RELEASE_SHA, CURRENT_DEPLOYMENT_ID
    global CURRENT_HOST_SAFETY_MODE
    global CURRENT_CAMPAIGN_ROOT, CURRENT_RELEASE_ROOT
    global CURRENT_INVENTORY_PATH, CURRENT_APPROVAL_POLICY_PATH
    global CURRENT_INVENTORY_RAW_SHA256, CURRENT_INVENTORY_SHA256
    global CURRENT_APPROVAL_POLICY_RAW_SHA256
    global CURRENT_APPROVAL_POLICY_SHA256, CURRENT_APPROVAL_PUBLIC_KEY_SHA256
    global SCRATCH_POSTGRES_IMAGE_ID
    global SCRATCH_POSTGRES_ENTRYPOINT, SCRATCH_POSTGRES_CMD
    global EXPECTED_HELPER_PATH, EXPECTED_HELPER_SHA256
    global HISTORICAL_CAMPAIGN_ID, HISTORICAL_TARGET_RELEASE_SHA
    global SOURCE_RELEASE_SHA, HISTORICAL_ROOT, ROLLBACK_STORAGE_ROOT
    global CONTRACTS, EXPECTED_SOURCE_VOLUMES

    try:
        reparsed = json.loads(raw, object_pairs_hook=_strict_object)
    except (json.JSONDecodeError, AdoptionError) as exc:
        raise AdoptionError("adoption contract raw bytes are invalid") from exc
    if reparsed != payload:
        raise AdoptionError("adoption contract payload/raw bytes differ")
    _exact_fields(
        payload,
        {"schema", "current", "historical", "roles"},
        label="adoption contract",
    )
    if payload["schema"] != "three-site-staging-frozen-source-adoption-contract-v2":
        raise AdoptionError("adoption contract schema is unsupported")
    current = _exact_fields(
        payload["current"],
        {
            "campaign_id",
            "release_sha",
            "deployment_id",
            "host_safety_mode",
            "campaign_root",
            "release_root",
            "inventory",
            "approval_policy",
            "scratch_postgres_image",
            "helper",
        },
        label="current adoption contract",
    )
    historical = _exact_fields(
        payload["historical"],
        {
            "campaign_id",
            "target_release_sha",
            "source_release_sha",
            "evidence_root",
            "rollback_storage_root",
        },
        label="historical adoption contract",
    )
    campaign_id = str(current["campaign_id"])
    release_sha = str(current["release_sha"])
    deployment_id = str(current["deployment_id"])
    host_safety_mode = str(current["host_safety_mode"])
    historical_campaign_id = str(historical["campaign_id"])
    historical_target_sha = str(historical["target_release_sha"])
    source_release_sha = str(historical["source_release_sha"])
    if (
        re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            campaign_id,
        )
        is None
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            historical_campaign_id,
        )
        is None
        or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,127}", deployment_id) is None
        or host_safety_mode
        not in {"shared-host-safe", "dedicated-host-destructive"}
        or any(
            re.fullmatch(r"[0-9a-f]{40}", value) is None
            for value in (release_sha, historical_target_sha, source_release_sha)
        )
    ):
        raise AdoptionError("adoption contract identity is malformed")

    scratch_image = _exact_fields(
        current["scratch_postgres_image"],
        {"id", "entrypoint", "cmd"},
        label="scratch PostgreSQL image",
    )
    scratch_image_id = str(scratch_image["id"])
    scratch_entrypoint = _command_vector(
        scratch_image["entrypoint"],
        label="scratch PostgreSQL entrypoint",
    )
    scratch_cmd = _command_vector(
        scratch_image["cmd"], label="scratch PostgreSQL command"
    )
    if IMAGE_ID_RE.fullmatch(scratch_image_id) is None:
        raise AdoptionError("scratch PostgreSQL image ID is malformed")

    campaign_root = _absolute_contract_path(
        current["campaign_root"], label="current campaign root"
    )
    release_root = _absolute_contract_path(
        current["release_root"], label="current release root"
    )
    inventory_reference = _exact_fields(
        current["inventory"],
        {"path", "raw_sha256", "canonical_sha256"},
        label="current provisioned inventory",
    )
    inventory_path = _absolute_contract_path(
        inventory_reference["path"], label="current inventory"
    )
    inventory_raw_sha256 = str(inventory_reference["raw_sha256"])
    inventory_sha256 = str(inventory_reference["canonical_sha256"])
    approval_policy_reference = _exact_fields(
        current["approval_policy"],
        {
            "path",
            "raw_sha256",
            "canonical_sha256",
            "public_key_sha256",
        },
        label="current approval policy",
    )
    approval_policy_path = _absolute_contract_path(
        approval_policy_reference["path"], label="current approval policy"
    )
    approval_policy_raw_sha256 = str(approval_policy_reference["raw_sha256"])
    approval_policy_sha256 = str(approval_policy_reference["canonical_sha256"])
    approval_public_key_sha256 = str(
        approval_policy_reference["public_key_sha256"]
    )
    helper_reference = _exact_fields(
        current["helper"], {"path", "sha256"}, label="adoption helper"
    )
    helper_path = _absolute_contract_path(
        helper_reference["path"], label="adoption helper"
    )
    helper_sha256 = str(helper_reference["sha256"])
    evidence_root = _absolute_contract_path(
        historical["evidence_root"], label="historical evidence root"
    )
    rollback_root = _absolute_contract_path(
        historical["rollback_storage_root"], label="historical rollback root"
    )
    secure_env_root = Path("/root/secure-envs/trading-bot")
    if (
        campaign_root.parent != secure_env_root
        or not campaign_root.name.startswith("three-site-staging-")
        or release_root != Path("/srv/trading-bot-three-site/releases") / release_sha
        or inventory_path.parent != campaign_root
        or inventory_path.name
        != f"provisioned-inventory-snapshot-{inventory_raw_sha256}.json"
        or SHA256_RE.fullmatch(inventory_raw_sha256) is None
        or SHA256_RE.fullmatch(inventory_sha256) is None
        or approval_policy_path.parent != campaign_root
        or approval_policy_path.name
        != (
            "human-approval-policy-snapshot-"
            f"{approval_policy_raw_sha256}.json"
        )
        or helper_path
        not in {
            (
                release_root
                / "scripts"
                / "adopt_three_site_staging_frozen_source_and_backup.py"
            ),
            (
                campaign_root
                / f"adopt-three-site-frozen-source-{helper_sha256}.py"
            ),
        }
        or SHA256_RE.fullmatch(helper_sha256) is None
        or any(
            SHA256_RE.fullmatch(value) is None
            for value in (
                approval_policy_raw_sha256,
                approval_policy_sha256,
                approval_public_key_sha256,
            )
        )
        or evidence_root.parent != secure_env_root
        or rollback_root == Path("/")
        or not rollback_root.is_relative_to(
            Path("/srv/trading-bot-three-site-staging-data/legacy-rollback")
        )
    ):
        raise AdoptionError("adoption contract roots are outside approved boundaries")

    raw_roles = payload["roles"]
    if (
        not isinstance(raw_roles, dict)
        or not raw_roles
        or not set(raw_roles).issubset({"bot_fi", "webapp_fi"})
    ):
        raise AdoptionError("adoption contract source role set is invalid")
    contracts: dict[str, HistoricalContract] = {}
    expected_volumes: dict[str, dict[str, tuple[str, str]]] = {}
    for role, untyped_role in raw_roles.items():
        role_value = _exact_fields(
            untyped_role,
            {
                "project_name",
                "app_service",
                "env_file",
                "env_sha256",
                "inventory_approval_path",
                "expected_approval_id",
                "expected_approval_token_sha256",
                "expected_approval_raw_sha256",
                "source_adoption_subject_path",
                "source_adoption_approval_path",
                "output_dir",
                "run_id",
                "restore_evidence",
                "adopted_freeze_evidence",
                "freeze_evidence",
                "restore_bundle",
                "compose",
                "service_images",
                "source_volumes",
            },
            label=f"{role} adoption contract",
        )
        expected_app = {"bot_fi": "foreign_app", "webapp_fi": "app"}[role]
        expected_project = {
            "bot_fi": "trading_bot_staging",
            "webapp_fi": "trading_bot_staging_iran",
        }[role]
        project = str(role_value["project_name"])
        app_service = str(role_value["app_service"])
        if (
            app_service != expected_app
            or SAFE_NAME_RE.fullmatch(project) is None
            or project != expected_project
        ):
            raise AdoptionError(f"{role} source project/service is not approved")
        env_file = _absolute_contract_path(role_value["env_file"], label=f"{role} env")
        env_sha256 = str(role_value["env_sha256"])
        approval_path = _absolute_contract_path(
            role_value["inventory_approval_path"], label=f"{role} approval"
        )
        source_adoption_subject_path = _absolute_contract_path(
            role_value["source_adoption_subject_path"],
            label=f"{role} source-adoption subject",
        )
        source_adoption_approval_path = _absolute_contract_path(
            role_value["source_adoption_approval_path"],
            label=f"{role} source-adoption approval",
        )
        output_dir = _absolute_contract_path(
            role_value["output_dir"], label=f"{role} output directory"
        )
        run_id = str(role_value["run_id"])
        if (
            not env_file.is_relative_to(secure_env_root)
            or SHA256_RE.fullmatch(env_sha256) is None
            or approval_path.parent != campaign_root
            or source_adoption_subject_path.parent != campaign_root
            or source_adoption_approval_path.parent != campaign_root
            or source_adoption_approval_path
            in {
                approval_path,
                source_adoption_subject_path,
                approval_policy_path,
                inventory_path,
            }
            or source_adoption_subject_path
            in {approval_path, approval_policy_path, inventory_path}
            or output_dir.parent != campaign_root
            or output_dir
            in {
                approval_path,
                source_adoption_subject_path,
                source_adoption_approval_path,
                approval_policy_path,
                inventory_path,
            }
            or SAFE_NAME_RE.fullmatch(output_dir.name) is None
            or re.fullmatch(r"[0-9a-f]{16}", run_id) is None
        ):
            raise AdoptionError(f"{role} env/approval path is outside approved roots")

        references: dict[str, tuple[Path, str]] = {}
        for field in (
            "restore_evidence",
            "adopted_freeze_evidence",
            "freeze_evidence",
            "restore_bundle",
            "compose",
        ):
            reference = _exact_fields(
                role_value[field], {"path", "sha256"}, label=f"{role} {field}"
            )
            reference_path = _absolute_contract_path(
                reference["path"], label=f"{role} {field}"
            )
            reference_sha = str(reference["sha256"])
            if SHA256_RE.fullmatch(reference_sha) is None:
                raise AdoptionError(f"{role} {field} digest is malformed")
            references[field] = (reference_path, reference_sha)
        if (
            any(
                not references[field][0].is_relative_to(evidence_root)
                for field in (
                    "restore_evidence",
                    "adopted_freeze_evidence",
                    "freeze_evidence",
                )
            )
            or any(
                not references[field][0].is_relative_to(rollback_root)
                for field in ("restore_bundle", "compose")
            )
        ):
            raise AdoptionError(f"{role} historical paths escape approved roots")

        images = role_value["service_images"]
        expected_services = {"db", "redis", app_service}
        if (
            not isinstance(images, dict)
            or set(images) != expected_services
            or any(IMAGE_ID_RE.fullmatch(str(value)) is None for value in images.values())
        ):
            raise AdoptionError(f"{role} exact service image IDs are invalid")
        volumes = role_value["source_volumes"]
        if (
            not isinstance(volumes, dict)
            or set(volumes) != {"/app/uploads", "/app/audit_trail"}
        ):
            raise AdoptionError(f"{role} exact source volumes are invalid")
        normalized_volumes: dict[str, tuple[str, str]] = {}
        for destination, untyped_volume in volumes.items():
            volume = _exact_fields(
                untyped_volume,
                {"name", "compose_volume"},
                label=f"{role} {destination} source volume",
            )
            name = str(volume["name"])
            logical = str(volume["compose_volume"])
            if (
                VOLUME_NAME_RE.fullmatch(name) is None
                or SAFE_NAME_RE.fullmatch(logical) is None
            ):
                raise AdoptionError(f"{role} exact source volume identity is invalid")
            normalized_volumes[str(destination)] = (name, logical)
        approval_id = str(role_value["expected_approval_id"])
        approval_sha = str(role_value["expected_approval_token_sha256"])
        approval_raw_sha = str(
            role_value["expected_approval_raw_sha256"]
        )
        if (
            re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                approval_id,
            )
            is None
            or SHA256_RE.fullmatch(approval_sha) is None
            or SHA256_RE.fullmatch(approval_raw_sha) is None
        ):
            raise AdoptionError(f"{role} expected approval identity is malformed")
        if (
            (role_slug := role.replace("_", "-")) == ""
            or
            approval_path.name
            != (
                f"provisioned-inventory-approval-{role_slug}-"
                f"{approval_raw_sha}.json"
            )
            or source_adoption_subject_path.name
            != f"source-adoption-subject-{role_slug}-{run_id}.json"
            or source_adoption_approval_path.name
            != f"source-adoption-approval-{role_slug}-{run_id}.json"
            or output_dir.name
            != f"source-adoption-output-{role_slug}-{run_id}"
        ):
            raise AdoptionError(
                f"{role} approval/output paths are not immutable snapshots"
            )
        contracts[role] = HistoricalContract(
            role=role,
            project=project,
            app_service=app_service,
            env_file=env_file,
            env_sha256=env_sha256,
            inventory_approval=approval_path,
            expected_approval_id=approval_id,
            expected_approval_token_sha256=approval_sha,
            expected_approval_raw_sha256=approval_raw_sha,
            source_adoption_subject=source_adoption_subject_path,
            source_adoption_approval=source_adoption_approval_path,
            output_dir=output_dir,
            run_id=run_id,
            restore_evidence_path=references["restore_evidence"][0],
            restore_evidence_sha256=references["restore_evidence"][1],
            adopted_freeze_path=references["adopted_freeze_evidence"][0],
            adopted_freeze_sha256=references["adopted_freeze_evidence"][1],
            freeze_evidence_path=references["freeze_evidence"][0],
            freeze_evidence_sha256=references["freeze_evidence"][1],
            restore_bundle_path=references["restore_bundle"][0],
            restore_bundle_sha256=references["restore_bundle"][1],
            compose_path=references["compose"][0],
            compose_sha256=references["compose"][1],
            service_images=tuple(sorted((str(key), str(value)) for key, value in images.items())),
        )
        expected_volumes[role] = normalized_volumes

    contract_sha256 = _sha256(raw)
    if (
        path.parent != campaign_root
        or path.name
        != f"source-adoption-contract-{contract_sha256}.json"
    ):
        raise AdoptionError(
            "adoption contract path is not a content-addressed snapshot"
        )
    ADOPTION_CONTRACT_PATH = path
    ADOPTION_CONTRACT_SHA256 = contract_sha256
    CURRENT_CAMPAIGN_ID = campaign_id
    CURRENT_RELEASE_SHA = release_sha
    CURRENT_DEPLOYMENT_ID = deployment_id
    CURRENT_HOST_SAFETY_MODE = host_safety_mode
    CURRENT_CAMPAIGN_ROOT = campaign_root
    CURRENT_RELEASE_ROOT = release_root
    CURRENT_INVENTORY_PATH = inventory_path
    CURRENT_INVENTORY_RAW_SHA256 = inventory_raw_sha256
    CURRENT_INVENTORY_SHA256 = inventory_sha256
    CURRENT_APPROVAL_POLICY_PATH = approval_policy_path
    CURRENT_APPROVAL_POLICY_RAW_SHA256 = approval_policy_raw_sha256
    CURRENT_APPROVAL_POLICY_SHA256 = approval_policy_sha256
    CURRENT_APPROVAL_PUBLIC_KEY_SHA256 = approval_public_key_sha256
    SCRATCH_POSTGRES_IMAGE_ID = scratch_image_id
    SCRATCH_POSTGRES_ENTRYPOINT = scratch_entrypoint
    SCRATCH_POSTGRES_CMD = scratch_cmd
    EXPECTED_HELPER_PATH = helper_path
    EXPECTED_HELPER_SHA256 = helper_sha256
    HISTORICAL_CAMPAIGN_ID = historical_campaign_id
    HISTORICAL_TARGET_RELEASE_SHA = historical_target_sha
    SOURCE_RELEASE_SHA = source_release_sha
    HISTORICAL_ROOT = evidence_root
    ROLLBACK_STORAGE_ROOT = rollback_root
    CONTRACTS = contracts
    EXPECTED_SOURCE_VOLUMES = expected_volumes
    return payload


def _verify_adoption_contract_unchanged() -> None:
    raw = _secure_bytes(ADOPTION_CONTRACT_PATH, maximum=1024 * 1024)
    if _sha256(raw) != ADOPTION_CONTRACT_SHA256:
        raise AdoptionError("adoption contract changed after secure preflight")


def _import_exact_release_module(
    module_name: str, relative_path: str
) -> Any:
    expected = (CURRENT_RELEASE_ROOT / relative_path).resolve()
    if not expected.is_file():
        raise AdoptionError(
            f"exact-release module is unavailable: {module_name}"
        )
    root_text = str(CURRENT_RELEASE_ROOT)
    if not sys.path or sys.path[0] != root_text:
        sys.path.insert(0, root_text)
    importlib.invalidate_caches()
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise AdoptionError(
            f"cannot import exact-release module: {module_name}"
        ) from exc
    module_file = getattr(module, "__file__", None)
    if (
        not isinstance(module_file, str)
        or Path(module_file).resolve() != expected
    ):
        raise AdoptionError(
            f"ambient module shadowing rejected: {module_name}"
        )
    return module


def _exclusive_write(path: Path, raw: bytes, *, mode: int = 0o600) -> None:
    """Durably create one file and refuse every pre-existing directory entry."""
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        os.fchmod(descriptor, mode)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise AdoptionError(f"short exclusive write: {path.name}")
            offset += written
        os.fsync(descriptor)
    except Exception:
        os.close(descriptor)
        descriptor = -1
        path.unlink(missing_ok=True)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != mode
        or metadata.st_size != len(raw)
    ):
        raise AdoptionError(f"exclusive output verification failed: {path.name}")
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _prepare_output_directory(path: Path) -> Path:
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path.parent != CURRENT_CAMPAIGN_ROOT
    ):
        raise AdoptionError("output directory must be a direct child of the current campaign root")
    root = CURRENT_CAMPAIGN_ROOT
    root_meta = root.lstat()
    if (
        not stat.S_ISDIR(root_meta.st_mode)
        or stat.S_ISLNK(root_meta.st_mode)
        or root_meta.st_uid != 0
        or stat.S_IMODE(root_meta.st_mode) != 0o700
    ):
        raise AdoptionError("current campaign root must be owner-owned mode-0700")
    try:
        path.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise AdoptionError("output directory already exists; refusing overwrite/reuse") from exc
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise AdoptionError("new output directory failed owner-only verification")
    directory_fd = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    root_fd = -1
    try:
        bound = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(bound.st_mode)
            or bound.st_uid != 0
            or stat.S_IMODE(bound.st_mode) != 0o700
            or (bound.st_dev, bound.st_ino)
            != (metadata.st_dev, metadata.st_ino)
        ):
            raise AdoptionError(
                "new output directory descriptor binding failed"
            )
        os.fsync(directory_fd)
        root_fd = os.open(
            root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        bound_root = os.fstat(root_fd)
        if (
            not stat.S_ISDIR(bound_root.st_mode)
            or bound_root.st_uid != 0
            or stat.S_IMODE(bound_root.st_mode) != 0o700
            or (bound_root.st_dev, bound_root.st_ino)
            != (root_meta.st_dev, root_meta.st_ino)
        ):
            raise AdoptionError(
                "campaign root descriptor binding failed"
            )
        os.fsync(root_fd)
    finally:
        os.close(directory_fd)
        if root_fd >= 0:
            os.close(root_fd)
    return path


def _validate_new_output_path(path: Path) -> None:
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path.parent != CURRENT_CAMPAIGN_ROOT
    ):
        raise AdoptionError("output directory must be a direct child of the current campaign root")
    root_meta = CURRENT_CAMPAIGN_ROOT.lstat()
    if (
        not stat.S_ISDIR(root_meta.st_mode)
        or stat.S_ISLNK(root_meta.st_mode)
        or root_meta.st_uid != 0
        or stat.S_IMODE(root_meta.st_mode) != 0o700
    ):
        raise AdoptionError("current campaign root must be owner-owned mode-0700")
    if path.exists() or path.is_symlink():
        raise AdoptionError("output directory already exists; a unique path is required")


def _run(arguments: list[str], *, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            arguments,
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AdoptionError(f"required command unavailable: {Path(arguments[0]).name}") from exc
    if result.returncode != 0:
        raise AdoptionError(f"command failed closed: {Path(arguments[0]).name}")
    return result.stdout.strip()


def _probe(arguments: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            arguments,
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AdoptionError(f"required probe unavailable: {Path(arguments[0]).name}") from exc


def _terminate_active_child() -> None:
    global _ACTIVE_CHILD
    process = _ACTIVE_CHILD
    if process is None or process.poll() is not None:
        _ACTIVE_CHILD = None
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    _ACTIVE_CHILD = None


class ApplySignalGuard:
    def __init__(self):
        self.original: dict[signal.Signals, Any] = {}

    def __enter__(self):
        def interrupt(signum, _frame):  # noqa: ANN001
            raise ControlledInterruption(f"apply interrupted by signal {signum}")

        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            self.original[signum] = signal.getsignal(signum)
            signal.signal(signum, interrupt)
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        for signum, handler in self.original.items():
            signal.signal(signum, handler)
        self.original.clear()
        return False


class RoleApplyLock:
    """One nonblocking root-only apply lock per source role and host."""

    def __init__(self, role: str):
        if role not in CONTRACTS:
            raise AdoptionError("apply lock source role is invalid")
        self.path = CURRENT_CAMPAIGN_ROOT / f".adopt-frozen-backup-{role}.lock"
        self.descriptor = -1

    def __enter__(self):
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            self.descriptor = os.open(self.path, flags, 0o600)
            os.fchmod(self.descriptor, 0o600)
            metadata = os.fstat(self.descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise AdoptionError("source-role apply lock file is unsafe")
            try:
                fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AdoptionError(
                    "another source-role apply is already running"
                ) from exc
        except Exception:
            if self.descriptor >= 0:
                os.close(self.descriptor)
                self.descriptor = -1
            raise
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        if self.descriptor >= 0:
            try:
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            finally:
                os.close(self.descriptor)
                self.descriptor = -1
        return False


def _stream_command_to_exclusive_file(
    arguments: list[str], target: Path, *, timeout: int
) -> None:
    global _ACTIVE_CHILD
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.PIPE,
                env=SAFE_ENV,
            )
            if _ACTIVE_CHILD is not None:
                process.kill()
                process.wait()
                raise AdoptionError("an untracked child process is already active")
            _ACTIVE_CHILD = process
            try:
                _stderr = process.communicate(timeout=timeout)[1]
            except subprocess.TimeoutExpired as exc:
                _terminate_active_child()
                raise AdoptionError("backup stream timed out") from exc
            finally:
                if process.poll() is not None and _ACTIVE_CHILD is process:
                    _ACTIVE_CHILD = None
            if process.returncode != 0:
                raise AdoptionError("backup stream failed closed")
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        _terminate_active_child()
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
        target.unlink(missing_ok=True)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if target.stat().st_size <= 0:
        target.unlink(missing_ok=True)
        raise AdoptionError("backup stream produced an empty file")


def _run_file_input(arguments: list[str], source_path: Path, *, timeout: int) -> None:
    global _ACTIVE_CHILD
    with source_path.open("rb") as source:
        process = subprocess.Popen(
            arguments,
            stdin=source,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=SAFE_ENV,
        )
        if _ACTIVE_CHILD is not None:
            process.kill()
            process.wait()
            raise AdoptionError("an untracked child process is already active")
        _ACTIVE_CHILD = process
        try:
            try:
                _stdout, _stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                _terminate_active_child()
                raise AdoptionError("restore input command timed out") from exc
            if process.returncode != 0:
                raise AdoptionError("restore input command failed closed")
        except BaseException:
            _terminate_active_child()
            raise
        finally:
            if process.poll() is not None and _ACTIVE_CHILD is process:
                _ACTIVE_CHILD = None


def _file_reference(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def _expected_historical_paths(
    contract: HistoricalContract,
) -> tuple[Path, Path, Path]:
    return (
        contract.restore_evidence_path,
        contract.freeze_evidence_path,
        contract.restore_bundle_path,
    )


def _expected_adopted_freeze_path(contract: HistoricalContract) -> Path:
    return contract.adopted_freeze_path


def _validate_adopted_restore_chain(
    *,
    contract: HistoricalContract,
    path: Path,
    restore: dict[str, Any],
) -> dict[str, Any]:
    if path != _expected_adopted_freeze_path(contract):
        raise AdoptionError("adopted freeze path differs from the fixed role contract")
    adopted, adopted_raw = _secure_json(path)
    if (
        _sha256(adopted_raw) != contract.adopted_freeze_sha256
        or _canonical_hash(adopted) != restore["freeze_evidence_sha256"]
    ):
        raise AdoptionError("restore is not bound to the exact adopted freeze evidence")
    reference = adopted.get("legacy_restore_bundle")
    if (
        not isinstance(reference, dict)
        or set(reference) != {"schema", "path", "sha256", "size"}
        or reference.get("schema")
        != "three-site-staging-legacy-restore-bundle-reference-v1"
        or reference.get("sha256") != restore["legacy_restore_bundle_sha256"]
    ):
        raise AdoptionError("restore is not bound to the adopted rollback bundle")
    restore_verifier = _import_exact_release_module(
        "scripts.restore_three_site_staging_sources",
        "scripts/restore_three_site_staging_sources.py",
    )
    restore_verifier.verify_restore_input(
        adopted,
        campaign_id=HISTORICAL_CAMPAIGN_ID,
        release_sha=HISTORICAL_TARGET_RELEASE_SHA,
        project_name=contract.project,
    )
    adopted_bundle, _compose_path = (
        restore_verifier._load_legacy_restore_bundle(
            reference, evidence=adopted
        )
    )
    if adopted_bundle.get("service_images") != restore["service_images"]:
        raise AdoptionError("restored images differ from the adopted rollback bundle")
    if not _utc(
        adopted["observed_at"], label="adopted freeze"
    ) < _utc(restore["restored_at"], label="historical restore"):
        raise AdoptionError("adopted freeze did not precede its source restore")
    return {
        "path": path,
        "raw_sha256": _sha256(adopted_raw),
        "canonical_sha256": _canonical_hash(adopted),
        "bundle_sha256": reference["sha256"],
        "observed_at": adopted["observed_at"],
    }


def _validate_historical_chain(
    *,
    contract: HistoricalContract,
    adopted_freeze_path: Path,
    restore_evidence_path: Path,
    freeze_evidence_path: Path,
    restore_bundle_path: Path,
) -> dict[str, Any]:
    expected_restore, expected_freeze, expected_bundle = _expected_historical_paths(contract)
    if (
        restore_evidence_path != expected_restore
        or freeze_evidence_path != expected_freeze
        or restore_bundle_path != expected_bundle
    ):
        raise AdoptionError("historical restore/freeze paths differ from the fixed role contract")

    restore, restore_raw = _secure_json(restore_evidence_path)
    freeze, freeze_raw = _secure_json(freeze_evidence_path)
    bundle, bundle_raw = _secure_json(restore_bundle_path)
    if _sha256(restore_raw) != contract.restore_evidence_sha256:
        raise AdoptionError("historical restore evidence differs from the reviewed bytes")
    if _sha256(freeze_raw) != contract.freeze_evidence_sha256:
        raise AdoptionError("historical freeze evidence differs from the reviewed bytes")
    if _sha256(bundle_raw) != contract.restore_bundle_sha256:
        raise AdoptionError("historical rollback bundle differs from the reviewed bytes")

    expected_images = dict(contract.service_images)
    expected_services = sorted(expected_images)
    restore_fields = {
        "schema",
        "status",
        "campaign_id",
        "release_sha",
        "freeze_evidence_sha256",
        "restored_at",
        "running_services",
        "legacy_restore_bundle_sha256",
        "service_images",
    }
    if (
        set(restore) != restore_fields
        or restore.get("schema") != "three-site-staging-source-restore-v1"
        or restore.get("status") != "restored"
        or restore.get("campaign_id") != HISTORICAL_CAMPAIGN_ID
        or restore.get("release_sha") != HISTORICAL_TARGET_RELEASE_SHA
        or restore.get("running_services") != expected_services
        or restore.get("service_images") != expected_images
        or SHA256_RE.fullmatch(str(restore.get("freeze_evidence_sha256", ""))) is None
        or SHA256_RE.fullmatch(
            str(restore.get("legacy_restore_bundle_sha256", ""))
        )
        is None
    ):
        raise AdoptionError("historical source restore evidence identity/content is invalid")
    adopted_chain = _validate_adopted_restore_chain(
        contract=contract,
        path=adopted_freeze_path,
        restore=restore,
    )

    freeze_fields = {
        "schema",
        "campaign_id",
        "target_release_sha",
        "project_name",
        "observed_at",
        "source_roles",
        "previously_running_services",
        "stopped_services",
        "running_services",
        "postgres",
        "redis_observation",
        "legacy_restore_bundle",
    }
    source_rows = freeze.get("source_roles")
    rollback_reference = freeze.get("legacy_restore_bundle")
    if (
        set(freeze) != freeze_fields
        or freeze.get("schema") != "three-site-staging-source-freeze-v1"
        or freeze.get("campaign_id") != HISTORICAL_CAMPAIGN_ID
        or freeze.get("target_release_sha") != HISTORICAL_TARGET_RELEASE_SHA
        or freeze.get("project_name") != contract.project
        or freeze.get("running_services") != ["db", "redis"]
        or freeze.get("previously_running_services") != expected_services
        or not isinstance(freeze.get("stopped_services"), list)
        or contract.app_service not in freeze["stopped_services"]
        or not isinstance(source_rows, list)
        or source_rows
        != [
            {
                "source_role": contract.role,
                "app_service": contract.app_service,
                "source_release_sha": SOURCE_RELEASE_SHA,
            }
        ]
        or not isinstance(rollback_reference, dict)
        or set(rollback_reference) != {"schema", "path", "sha256", "size"}
        or rollback_reference.get("schema")
        != "three-site-staging-legacy-restore-bundle-reference-v1"
        or rollback_reference.get("path") != str(restore_bundle_path)
        or rollback_reference.get("sha256") != contract.restore_bundle_sha256
        or rollback_reference.get("size") != len(bundle_raw)
    ):
        raise AdoptionError("historical official freeze identity/content is invalid")

    bundle_fields = {
        "schema",
        "campaign_id",
        "target_release_sha",
        "project_name",
        "captured_at",
        "source_releases",
        "previously_running_services",
        "compose",
        "service_images",
    }
    compose = bundle.get("compose")
    if (
        set(bundle) != bundle_fields
        or bundle.get("schema") != "three-site-staging-legacy-restore-bundle-v1"
        or bundle.get("campaign_id") != HISTORICAL_CAMPAIGN_ID
        or bundle.get("target_release_sha") != HISTORICAL_TARGET_RELEASE_SHA
        or bundle.get("project_name") != contract.project
        or bundle.get("source_releases") != {contract.role: SOURCE_RELEASE_SHA}
        or bundle.get("previously_running_services") != expected_services
        or bundle.get("service_images") != expected_images
        or not isinstance(compose, dict)
        or set(compose) != {"path", "sha256", "size"}
        or SHA256_RE.fullmatch(str(compose.get("sha256", ""))) is None
        or type(compose.get("size")) is not int
        or not 1 <= compose["size"] <= 10 * 1024 * 1024
    ):
        raise AdoptionError("historical official rollback manifest is invalid")
    if compose["sha256"] != contract.compose_sha256:
        raise AdoptionError("historical Compose digest differs from the fixed role contract")
    compose_path = Path(str(compose["path"]))
    if (
        compose_path != contract.compose_path
        or not compose_path.is_absolute()
        or ".." in compose_path.parts
    ):
        raise AdoptionError("historical Compose path differs from the bound contract")
    compose_raw = _secure_bytes(compose_path, maximum=10 * 1024 * 1024)
    if len(compose_raw) != compose["size"] or _sha256(compose_raw) != compose["sha256"]:
        raise AdoptionError("historical Compose bytes differ from the official manifest")

    restored_at = _utc(restore["restored_at"], label="historical restore")
    captured_at = _utc(bundle["captured_at"], label="historical bundle capture")
    frozen_at = _utc(freeze["observed_at"], label="historical freeze")
    if not restored_at < captured_at < frozen_at:
        raise AdoptionError("historical restore->official-freeze chronology is invalid")

    postgres = freeze.get("postgres")
    redis = freeze.get("redis_observation")
    if (
        not isinstance(postgres, dict)
        or set(postgres)
        != {
            "system_id",
            "alembic_revision",
            "database_fingerprint_sha256",
            "database_row_count",
            "public_table_count",
        }
        or not re.fullmatch(r"[0-9]{10,20}", str(postgres.get("system_id", "")))
        or not isinstance(postgres.get("alembic_revision"), str)
        or SHA256_RE.fullmatch(
            str(postgres.get("database_fingerprint_sha256", ""))
        )
        is None
        or type(postgres.get("database_row_count")) is not int
        or type(postgres.get("public_table_count")) is not int
        or not isinstance(redis, dict)
        or set(redis) != {"dbsize", "appendonly", "lastsave_unix", "restore"}
        or type(redis.get("dbsize")) is not int
        or redis["dbsize"] < 0
        or redis.get("appendonly") is not True
        or type(redis.get("lastsave_unix")) is not int
        or redis["lastsave_unix"] <= 0
        or redis.get("restore") is not False
    ):
        raise AdoptionError("historical PostgreSQL/Redis evidence is malformed")

    return {
        "contract": contract,
        "restore": restore,
        "adopted_restore_chain": adopted_chain,
        "restore_raw_sha256": _sha256(restore_raw),
        "freeze": freeze,
        "freeze_raw_sha256": _sha256(freeze_raw),
        "bundle": bundle,
        "bundle_raw_sha256": _sha256(bundle_raw),
        "compose_path": compose_path,
        "compose_raw": compose_raw,
        "compose_sha256": compose["sha256"],
        "compose_size": compose["size"],
        "service_images": expected_images,
        "previously_running_services": expected_services,
    }


def _expected_approval_path(role: str) -> Path:
    if role not in CONTRACTS:
        raise AdoptionError("source role is outside the approval path contract")
    return CONTRACTS[role].inventory_approval


def _verify_approval_policy_binding(
    policy: dict[str, Any], raw: bytes
) -> None:
    issuer = policy.get("issuer")
    if (
        _sha256(raw) != CURRENT_APPROVAL_POLICY_RAW_SHA256
        or _canonical_hash(policy) != CURRENT_APPROVAL_POLICY_SHA256
        or not isinstance(issuer, dict)
        or not isinstance(issuer.get("public_key"), str)
    ):
        raise AdoptionError("approval policy differs from the contract-bound bytes")
    try:
        public_key = base64.b64decode(issuer["public_key"], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise AdoptionError("approval policy public key is malformed") from exc
    if (
        len(public_key) != 32
        or _sha256(public_key) != CURRENT_APPROVAL_PUBLIC_KEY_SHA256
    ):
        raise AdoptionError("approval policy public key differs from the contract")


def _verify_current_approval(
    args: argparse.Namespace, *, require_fresh: bool = True
) -> dict[str, Any]:
    expected_approval = _expected_approval_path(args.source_role)
    expected_inputs = (
        CURRENT_INVENTORY_PATH,
        expected_approval,
        CURRENT_APPROVAL_POLICY_PATH,
    )
    if (
        args.inventory,
        args.inventory_approval,
        args.approval_policy,
    ) != expected_inputs:
        raise AdoptionError("provisioned inventory approval inputs are not canonical")
    inventory, inventory_raw = _secure_json(args.inventory)
    approval, approval_raw = _secure_json(args.inventory_approval)
    policy, policy_raw = _secure_json(args.approval_policy)
    _verify_approval_policy_binding(policy, policy_raw)
    contract = CONTRACTS[args.source_role]
    if (
        _sha256(inventory_raw) != CURRENT_INVENTORY_RAW_SHA256
        or _canonical_hash(inventory) != CURRENT_INVENTORY_SHA256
        or _sha256(approval_raw)
        != contract.expected_approval_raw_sha256
    ):
        raise AdoptionError(
            "inventory provenance snapshots differ from the contract"
        )

    inventory_verifier = _import_exact_release_module(
        "scripts.verify_three_site_staging_inventory",
        "scripts/verify_three_site_staging_inventory.py",
    )
    verified = inventory_verifier.verify_approved_inventory(
        inventory,
        approval=approval,
        approval_policy=policy,
        host_destructive=CURRENT_HOST_SAFETY_MODE
        == "dedicated-host-destructive",
        require_fresh_approval=require_fresh,
    )
    if (
        verified.get("inventory_stage") != "provisioned"
        or verified.get("campaign_id") != CURRENT_CAMPAIGN_ID
        or verified.get("release_sha") != CURRENT_RELEASE_SHA
        or verified.get("deployment_id") != CURRENT_DEPLOYMENT_ID
        or verified.get("host_safety_mode") != CURRENT_HOST_SAFETY_MODE
    ):
        raise AdoptionError("approval is not bound to the exact provisioned campaign")
    if (
        verified.get("inventory_sha256") != CURRENT_INVENTORY_SHA256
        or
        verified.get("approval_id") != contract.expected_approval_id
        or verified.get("approval_token_sha256")
        != contract.expected_approval_token_sha256
    ):
        raise AdoptionError("approval differs from the contract-bound exact token")
    boundaries = inventory.get("production_boundaries")
    if not isinstance(boundaries, dict):
        raise AdoptionError("approved inventory lacks production boundaries")
    return {**verified, "_production_boundaries": boundaries}


def _operation_id(role: str, run_id: str) -> str:
    if role not in CONTRACTS or re.fullmatch(r"[0-9a-f]{16}", run_id) is None:
        raise AdoptionError("source-adoption operation identity is invalid")
    return (
        f"frozen-backup-{CURRENT_CAMPAIGN_ID[:8]}-"
        f"{role.replace('_', '-')}-{run_id}"
    )


def source_adoption_approval_subject(role: str) -> dict[str, Any]:
    if role not in CONTRACTS:
        raise AdoptionError("source role is outside the adoption contract")
    contract = CONTRACTS[role]
    return {
        "artifact_type": SOURCE_ADOPTION_ARTIFACT_TYPE,
        "artifact_sha256": ADOPTION_CONTRACT_SHA256,
        "release_sha": CURRENT_RELEASE_SHA,
        "bindings": {
            "campaign_id": CURRENT_CAMPAIGN_ID,
            "deployment_id": CURRENT_DEPLOYMENT_ID,
            "source_role": role,
            "host_safety_mode": CURRENT_HOST_SAFETY_MODE,
            "helper_sha256": EXPECTED_HELPER_SHA256,
            "historical_freeze_sha256": contract.freeze_evidence_sha256,
            "run_id": contract.run_id,
            "operation_id": _operation_id(role, contract.run_id),
            "output_dir": str(contract.output_dir),
            "max_temporary_containers": MAX_TEMPORARY_CONTAINERS,
            "max_scratch_volumes": MAX_SCRATCH_VOLUMES,
            "network_mode": "none",
            "operation": SOURCE_ADOPTION_OPERATION,
            "output_intent": SOURCE_ADOPTION_OUTPUT_INTENT,
        },
    }


def _verify_source_adoption_approval(
    args: argparse.Namespace, *, require_fresh: bool
) -> dict[str, Any]:
    contract = CONTRACTS[args.source_role]
    if (
        args.source_adoption_approval != contract.source_adoption_approval
        or args.approval_policy != CURRENT_APPROVAL_POLICY_PATH
    ):
        raise AdoptionError("source-adoption approval inputs are not canonical")
    approval, approval_raw = _secure_json(args.source_adoption_approval)
    policy, policy_raw = _secure_json(args.approval_policy)
    _verify_approval_policy_binding(policy, policy_raw)
    subject = source_adoption_approval_subject(args.source_role)
    try:
        _import_exact_release_module(
            "core.canonical_json", "core/canonical_json.py"
        )
        human_approval = _import_exact_release_module(
            "core.human_approval", "core/human_approval.py"
        )
        verified = human_approval.verify_human_approval(
            approval,
            policy_payload=policy,
            expected_action=SOURCE_ADOPTION_ACTION,
            expected_environment="staging",
            expected_subject=subject,
            require_fresh=require_fresh,
            allow_session=False,
        )
    except Exception as exc:
        raise AdoptionError(
            "direct source-adoption backup approval is invalid"
        ) from exc
    if (
        verified.expires_at <= verified.issued_at
        or verified.expires_at - verified.issued_at > timedelta(hours=1)
    ):
        raise AdoptionError(
            "direct source-adoption approval lifetime exceeds one hour"
        )
    return {
        "action": SOURCE_ADOPTION_ACTION,
        "environment": "staging",
        "approval_path": str(contract.source_adoption_approval),
        "approval_id": verified.approval_id,
        "approval_token_sha256": verified.token_hash,
        "approval_token_raw_sha256": _sha256(approval_raw),
        "approval_issued_at": verified.issued_at.isoformat(),
        "approval_expires_at": verified.expires_at.isoformat(),
        "approval_policy_sha256": CURRENT_APPROVAL_POLICY_SHA256,
        "approval_subject_sha256": _canonical_hash(subject),
        "adoption_contract_sha256": ADOPTION_CONTRACT_SHA256,
    }


def _verify_exact_release() -> None:
    if (
        Path(__file__).resolve() != EXPECTED_HELPER_PATH
        or _sha256(
            _secure_bytes(
                EXPECTED_HELPER_PATH,
                maximum=10 * 1024 * 1024,
                expected_mode=(0o700, 0o755),
            )
        )
        != EXPECTED_HELPER_SHA256
    ):
        raise AdoptionError("running helper differs from the contract-bound bytes")
    if _run([GIT, "-C", str(CURRENT_RELEASE_ROOT), "rev-parse", "HEAD"]) != CURRENT_RELEASE_SHA:
        raise AdoptionError("immutable target checkout is not the exact approved release")
    if _run(
        [
            GIT,
            "-C",
            str(CURRENT_RELEASE_ROOT),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ]
    ):
        raise AdoptionError("immutable target checkout is dirty")


def _verify_local_image(image_id: str, *, label: str) -> None:
    if IMAGE_ID_RE.fullmatch(image_id) is None:
        raise AdoptionError(f"{label} image ID is malformed")
    observed = _run([DOCKER, "image", "inspect", "--format", "{{.Id}}", image_id])
    if observed != image_id:
        raise AdoptionError(f"{label} exact image is unavailable")


def _verify_scratch_image_identity(image_id: str) -> None:
    _verify_local_image(image_id, label="scratch PostgreSQL")
    try:
        entrypoint = _command_vector(
            json.loads(
                _run(
                    [
                        DOCKER,
                        "image",
                        "inspect",
                        "--format",
                        "{{json .Config.Entrypoint}}",
                        image_id,
                    ]
                )
            ),
            label="scratch image entrypoint",
        )
        command = _command_vector(
            json.loads(
                _run(
                    [
                        DOCKER,
                        "image",
                        "inspect",
                        "--format",
                        "{{json .Config.Cmd}}",
                        image_id,
                    ]
                )
            ),
            label="scratch image command",
        )
    except (json.JSONDecodeError, AdoptionError) as exc:
        raise AdoptionError(
            "scratch image command identity is unreadable"
        ) from exc
    if (
        entrypoint != SCRATCH_POSTGRES_ENTRYPOINT
        or command != SCRATCH_POSTGRES_CMD
    ):
        raise AdoptionError(
            "scratch image command identity differs from the contract"
        )


def _inspect_value(container: str, template: str) -> str:
    return _run([DOCKER, "inspect", "--format", template, container])


def _project_snapshot(contract: HistoricalContract, images: dict[str, str]) -> dict[str, Any]:
    raw_ids = _run(
        [
            DOCKER,
            "container",
            "ls",
            "--no-trunc",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={contract.project}",
        ]
    )
    container_ids = [value for value in raw_ids.splitlines() if value]
    if (
        not container_ids
        or len(container_ids) != len(set(container_ids))
        or any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in container_ids)
    ):
        raise AdoptionError("legacy project container inventory is absent or duplicated")

    by_service: dict[str, dict[str, Any]] = {}
    for container_id in container_ids:
        service = _inspect_value(
            container_id, '{{index .Config.Labels "com.docker.compose.service"}}'
        )
        project = _inspect_value(
            container_id, '{{index .Config.Labels "com.docker.compose.project"}}'
        )
        image_id = _inspect_value(container_id, "{{.Image}}")
        status = _inspect_value(container_id, "{{.State.Status}}")
        running_text = _inspect_value(container_id, "{{.State.Running}}")
        restart_count = _inspect_value(container_id, "{{.RestartCount}}")
        started_at = _inspect_value(container_id, "{{.State.StartedAt}}")
        finished_at = _inspect_value(container_id, "{{.State.FinishedAt}}")
        created_at = _inspect_value(container_id, "{{.Created}}")
        if (
            project != contract.project
            or not service
            or service in by_service
            or running_text not in {"true", "false"}
            or not restart_count.isdigit()
            or IMAGE_ID_RE.fullmatch(image_id) is None
        ):
            raise AdoptionError("legacy project container labels/state are ambiguous")
        by_service[service] = {
            "container_id": container_id,
            "image_id": image_id,
            "status": status,
            "running": running_text == "true",
            "restart_count": int(restart_count),
            "started_at": started_at,
            "finished_at": finished_at,
            "created_at": created_at,
        }

    required = DATA_SERVICES | {contract.app_service}
    if not required.issubset(by_service):
        raise AdoptionError("legacy project lacks one exact DB/Redis/application container")
    running_services = sorted(
        service for service, row in by_service.items() if row["running"]
    )
    if running_services != ["db", "redis"]:
        raise AdoptionError("legacy project is not exactly frozen with only DB and Redis running")
    if (
        by_service["db"]["status"] != "running"
        or by_service["redis"]["status"] != "running"
        or by_service[contract.app_service]["status"] != "exited"
    ):
        raise AdoptionError("legacy DB/Redis/application state is not exact")
    for service in required:
        if by_service[service]["image_id"] != images[service]:
            raise AdoptionError(f"legacy current container image differs: {service}")

    app_id = str(by_service[contract.app_service]["container_id"])
    try:
        mounts = json.loads(
            _inspect_value(app_id, "{{json .Mounts}}"),
            object_pairs_hook=_strict_object,
        )
    except json.JSONDecodeError as exc:
        raise AdoptionError("legacy application mount inventory is unreadable") from exc
    if not isinstance(mounts, list):
        raise AdoptionError("legacy application mount inventory is invalid")
    source_volumes: dict[str, str] = {}
    for destination in ("/app/uploads", "/app/audit_trail"):
        matching = [
            row
            for row in mounts
            if isinstance(row, dict) and row.get("Destination") == destination
        ]
        if (
            len(matching) != 1
            or matching[0].get("Type") != "volume"
            or not isinstance(matching[0].get("Name"), str)
            or VOLUME_NAME_RE.fullmatch(matching[0]["Name"]) is None
        ):
            raise AdoptionError(
                "legacy application lacks one exact named upload/audit volume"
            )
        source_volumes[destination] = matching[0]["Name"]
    return {
        "project": contract.project,
        "running_services": running_services,
        "services": dict(sorted(by_service.items())),
        "app_source_volumes": dict(sorted(source_volumes.items())),
    }


def _secure_env(
    path: Path, *, expected_sha256: str
) -> dict[str, str]:
    if SHA256_RE.fullmatch(expected_sha256) is None:
        raise AdoptionError("legacy environment digest is malformed")
    raw = _secure_bytes(path, maximum=1024 * 1024)
    if _sha256(raw) != expected_sha256:
        raise AdoptionError(
            "legacy environment bytes differ from the adoption contract"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdoptionError("legacy environment is not UTF-8") from exc
    result: dict[str, str] = {}
    for source_line in text.splitlines():
        line = source_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if (
            not separator
            or not re.fullmatch(r"^[A-Z][A-Z0-9_]*$", name)
            or name in result
            or "\x00" in value
        ):
            raise AdoptionError("legacy environment contains an invalid/duplicate entry")
        result[name] = value
    return result


def _psql(container: str, user: str, database: str, sql: str) -> str:
    return _run(
        [
            DOCKER,
            "exec",
            "-e",
            "PGOPTIONS=-c default_transaction_read_only=on",
            container,
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            user,
            "-d",
            database,
            "-Atqc",
            sql,
        ],
        timeout=120,
    )


def _database_fingerprint(query: Callable[[str], str]) -> tuple[str, int, int]:
    tables = [
        value
        for value in query(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname='public' ORDER BY tablename"
        ).splitlines()
        if value
    ]
    summaries: list[list[Any]] = []
    total_rows = 0
    for table in tables:
        if IDENT_RE.fullmatch(table) is None:
            raise AdoptionError("database has an unsupported public table identifier")
        result = query(
            "SELECT count(*)::text || '|' || "
            "coalesce(md5(string_agg(row_data, E'\\n' ORDER BY row_data)), md5('')) "
            f'FROM (SELECT row_to_json(source_row)::text AS row_data '
            f'FROM public."{table}" source_row) rows'
        )
        count_text, separator, digest = result.partition("|")
        if (
            not separator
            or not count_text.isdigit()
            or re.fullmatch(r"[0-9a-f]{32}", digest) is None
        ):
            raise AdoptionError("database table fingerprint is malformed")
        count = int(count_text)
        total_rows += count
        summaries.append([table, count, digest])
    sequences = query(
        "SELECT sequencename || '|' || coalesce(last_value::text, 'null') "
        "FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"
    ).splitlines()
    payload = {"tables": summaries, "sequences": sequences}
    return _canonical_hash(payload), total_rows, len(tables)


def _measure_source(
    *,
    snapshot: dict[str, Any],
    env: dict[str, str],
) -> dict[str, Any]:
    user = env.get("POSTGRES_USER", "")
    database = env.get("POSTGRES_DB", "")
    if IDENT_RE.fullmatch(user) is None or IDENT_RE.fullmatch(database) is None:
        raise AdoptionError("legacy PostgreSQL user/database identity is invalid")
    db_container = str(snapshot["services"]["db"]["container_id"])
    redis_container = str(snapshot["services"]["redis"]["container_id"])
    system_id = _psql(
        db_container, user, database, "SELECT system_identifier FROM pg_control_system()"
    )
    revision = _psql(
        db_container, user, database, "SELECT version_num FROM alembic_version"
    )
    fingerprint, row_count, table_count = _database_fingerprint(
        lambda sql: _psql(db_container, user, database, sql)
    )
    dbsize = _run(
        [DOCKER, "exec", redis_container, "redis-cli", "--raw", "DBSIZE"]
    )
    appendonly = _run(
        [
            DOCKER,
            "exec",
            redis_container,
            "redis-cli",
            "--raw",
            "CONFIG",
            "GET",
            "appendonly",
        ]
    ).splitlines()
    lastsave = _run(
        [DOCKER, "exec", redis_container, "redis-cli", "--raw", "LASTSAVE"]
    )
    if (
        not re.fullmatch(r"[0-9]{10,20}", system_id)
        or not revision
        or not dbsize.isdigit()
        or appendonly != ["appendonly", "yes"]
        or not lastsave.isdigit()
    ):
        raise AdoptionError("fresh PostgreSQL/Redis observation is invalid")
    return {
        "postgres": {
            "system_id": system_id,
            "alembic_revision": revision,
            "database_fingerprint_sha256": fingerprint,
            "database_row_count": row_count,
            "public_table_count": table_count,
        },
        "redis_observation": {
            "dbsize": int(dbsize),
            "appendonly": True,
            "lastsave_unix": int(lastsave),
            "restore": False,
        },
        "postgres_user": user,
        "postgres_database": database,
    }


def _validate_source_against_official_freeze(
    observed: dict[str, Any], historical: dict[str, Any]
) -> None:
    expected_postgres = historical["freeze"]["postgres"]
    expected_redis = historical["freeze"]["redis_observation"]
    if observed["postgres"] != expected_postgres:
        raise AdoptionError("PostgreSQL changed after the latest official freeze")
    current_redis = observed["redis_observation"]
    if (
        current_redis["appendonly"] is not True
        or current_redis["restore"] is not False
        or current_redis["dbsize"] > expected_redis["dbsize"]
        or current_redis["lastsave_unix"] < expected_redis["lastsave_unix"]
    ):
        raise AdoptionError("Redis observation is incompatible with the latest official freeze")


def _validate_app_stop_chronology(
    *,
    contract: HistoricalContract,
    snapshot: dict[str, Any],
    historical: dict[str, Any],
) -> dict[str, str]:
    app = snapshot["services"][contract.app_service]
    created_at = _utc(app["created_at"], label="legacy application creation")
    started_at = _utc(app["started_at"], label="legacy application start")
    finished_at = _utc(app["finished_at"], label="legacy application stop")
    official_freeze = _utc(
        historical["freeze"]["observed_at"], label="latest official freeze"
    )
    if not created_at <= started_at <= finished_at:
        raise AdoptionError("legacy application lifecycle chronology is invalid")
    if finished_at > official_freeze + timedelta(seconds=2):
        raise AdoptionError("legacy application was restarted after the official freeze")
    return {
        "created_at": created_at.isoformat(),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "official_freeze_observed_at": official_freeze.isoformat(),
    }


def _validate_protected_source_identities(
    *,
    contract: HistoricalContract,
    boundaries: dict[str, Any],
    snapshot: dict[str, Any],
    measurement: dict[str, Any],
    scratch_volume_name: str,
) -> dict[str, str]:
    required = {"postgres_system_ids", "volume_ids", "audit_root_ids"}
    if not required.issubset(boundaries):
        raise AdoptionError("approved production boundaries lack source identity sets")
    postgres_ids = boundaries["postgres_system_ids"]
    volume_ids = boundaries["volume_ids"]
    audit_ids = boundaries["audit_root_ids"]
    if (
        not isinstance(postgres_ids, list)
        or not all(isinstance(value, str) for value in postgres_ids)
        or not isinstance(volume_ids, list)
        or not all(isinstance(value, str) for value in volume_ids)
        or not isinstance(audit_ids, list)
        or not all(isinstance(value, str) for value in audit_ids)
    ):
        raise AdoptionError("approved production boundary identity sets are invalid")
    source_volumes = snapshot["app_source_volumes"]
    uploads = source_volumes["/app/uploads"]
    audit = source_volumes["/app/audit_trail"]
    expected_volumes = EXPECTED_SOURCE_VOLUMES[contract.role]
    if any(
        source_volumes[destination] != expected_volumes[destination][0]
        for destination in expected_volumes
    ):
        raise AdoptionError("legacy upload/audit volume names differ from the fixed role contract")
    for destination, (volume_name, logical_name) in expected_volumes.items():
        observed_project = _run(
            [
                DOCKER,
                "volume",
                "inspect",
                "--format",
                '{{index .Labels "com.docker.compose.project"}}',
                volume_name,
            ]
        )
        observed_logical = _run(
            [
                DOCKER,
                "volume",
                "inspect",
                "--format",
                '{{index .Labels "com.docker.compose.volume"}}',
                volume_name,
            ]
        )
        if observed_project != contract.project or observed_logical != logical_name:
            raise AdoptionError(
                f"legacy protected volume labels differ: {destination}"
            )
    system_id = measurement["postgres"]["system_id"]
    if system_id not in postgres_ids:
        raise AdoptionError("legacy PostgreSQL system ID is outside approved boundaries")
    if uploads not in volume_ids:
        raise AdoptionError("legacy uploads volume is outside approved boundaries")
    if audit not in volume_ids or audit not in audit_ids:
        raise AdoptionError("legacy audit volume is outside approved audit boundaries")
    if scratch_volume_name in set(volume_ids) | set(audit_ids):
        raise AdoptionError("scratch volume name collides with a protected source identity")
    return {
        "postgres_system_id": system_id,
        "uploads_volume_id": uploads,
        "audit_volume_id": audit,
    }


def _derive_pinned_compose(
    historical: dict[str, Any],
    *,
    role: str,
    raw_copy_path: Path,
) -> bytes:
    try:
        source = yaml.safe_load(historical["compose_raw"])
    except yaml.YAMLError as exc:
        raise AdoptionError("historical expanded Compose is invalid YAML") from exc
    if not isinstance(source, dict) or not isinstance(source.get("services"), dict):
        raise AdoptionError("historical expanded Compose lacks services")
    images = historical["service_images"]
    image_reference_to_id: dict[str, str] = {}
    for service, image_id in sorted(images.items()):
        raw_service = source["services"].get(service)
        if not isinstance(raw_service, dict) or not isinstance(
            raw_service.get("image"), str
        ):
            raise AdoptionError(f"historical Compose lacks rollback service: {service}")
        reference = raw_service["image"]
        previous = image_reference_to_id.setdefault(reference, image_id)
        if previous != image_id:
            raise AdoptionError("historical Compose image reference maps ambiguously")

    pinned_services: dict[str, Any] = {}
    execution_images: dict[str, str] = {}
    for service, raw_service in sorted(source["services"].items()):
        if not isinstance(raw_service, dict):
            raise AdoptionError("historical Compose service is invalid")
        reference = raw_service.get("image")
        if not isinstance(reference, str) or reference not in image_reference_to_id:
            raise AdoptionError(
                "historical Compose has a service without an exact reviewed image mapping"
            )
        service_payload = copy.deepcopy(raw_service)
        service_payload.pop("build", None)
        service_payload["image"] = image_reference_to_id[reference]
        service_payload["pull_policy"] = "never"
        pinned_services[str(service)] = service_payload
        execution_images[str(service)] = image_reference_to_id[reference]

    result = copy.deepcopy(source)
    if "x-three-site-adoption-provenance" in result:
        raise AdoptionError("historical Compose collides with adoption provenance extension")
    result["services"] = pinned_services
    result["x-three-site-adoption-provenance"] = {
        "schema": "three-site-staging-frozen-source-compose-provenance-v1",
        "current_campaign_id": CURRENT_CAMPAIGN_ID,
        "current_release_sha": CURRENT_RELEASE_SHA,
        "source_role": role,
        "source_release_sha": SOURCE_RELEASE_SHA,
        "historical_campaign_id": HISTORICAL_CAMPAIGN_ID,
        "historical_target_release_sha": HISTORICAL_TARGET_RELEASE_SHA,
        "historical_restore_evidence_sha256": historical["restore_raw_sha256"],
        "historical_freeze_evidence_sha256": historical["freeze_raw_sha256"],
        "historical_restore_bundle_sha256": historical["bundle_raw_sha256"],
        "historical_compose_sha256": historical["compose_sha256"],
        "historical_compose_copy": str(raw_copy_path),
        "rollback_service_images": dict(sorted(images.items())),
        "execution_service_images": dict(sorted(execution_images.items())),
        "pull_policy": "never",
    }
    return yaml.safe_dump(
        result,
        allow_unicode=False,
        default_flow_style=False,
        sort_keys=True,
    ).encode("utf-8")


def _verify_tar_artifact(path: Path) -> int:
    count = 0
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive:
                normalized = PurePosixPath(member.name)
                if (
                    not member.name
                    or normalized.is_absolute()
                    or ".." in normalized.parts
                    or not (member.isfile() or member.isdir())
                ):
                    raise AdoptionError("archive contains an unsafe member")
                count += 1
                if count > 1_000_000:
                    raise AdoptionError("archive member count exceeds the safety bound")
    except (OSError, tarfile.TarError) as exc:
        raise AdoptionError("archive integrity verification failed") from exc
    if count <= 0:
        raise AdoptionError("archive is empty")
    return count


def postgres_dump_command(
    *,
    container_id: str,
    user: str,
    database: str,
) -> list[str]:
    if (
        re.fullmatch(r"[0-9a-f]{64}", container_id) is None
        or IDENT_RE.fullmatch(user) is None
        or IDENT_RE.fullmatch(database) is None
    ):
        raise AdoptionError("PostgreSQL dump identity is invalid")
    return [
        DOCKER,
        "exec",
        "-e",
        "PGOPTIONS=-c default_transaction_read_only=on",
        container_id,
        "pg_dump",
        "-U",
        user,
        "-d",
        database,
        "-Fc",
        "--no-owner",
        "--no-acl",
    ]


def archive_container_create_command(
    *,
    name: str,
    operation_id: str,
    source_volume: str,
    app_image_id: str,
) -> list[str]:
    if (
        SAFE_NAME_RE.fullmatch(name) is None
        or SAFE_NAME_RE.fullmatch(operation_id) is None
        or VOLUME_NAME_RE.fullmatch(source_volume) is None
        or IMAGE_ID_RE.fullmatch(app_image_id) is None
    ):
        raise AdoptionError("archive worker command identity is invalid")
    return [
        DOCKER,
        "container",
        "create",
        "--name",
        name,
        "--label",
        f"{TEMP_LABEL_KEY}={operation_id}",
        "--pull=never",
        "--network=none",
        "--log-driver=none",
        "--restart=no",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--memory=256m",
        "--memory-swap=256m",
        "--cpus=1",
        "--pids-limit=128",
        "--mount",
        f"type=volume,src={source_volume},dst=/source,readonly,volume-nocopy",
        "--entrypoint=tar",
        app_image_id,
        "-C",
        "/source",
        "-czf",
        "-",
        ".",
    ]


def scratch_container_create_command(
    *,
    name: str,
    operation_id: str,
    volume: str,
    image_id: str,
) -> list[str]:
    if (
        SAFE_NAME_RE.fullmatch(name) is None
        or SAFE_NAME_RE.fullmatch(operation_id) is None
        or SAFE_NAME_RE.fullmatch(volume) is None
        or IMAGE_ID_RE.fullmatch(image_id) is None
    ):
        raise AdoptionError("scratch restore command identity is invalid")
    return [
        DOCKER,
        "container",
        "create",
        "--name",
        name,
        "--label",
        f"{TEMP_LABEL_KEY}={operation_id}",
        "--pull=never",
        "--network=none",
        "--log-driver=none",
        "--restart=no",
        "--read-only",
        "--memory=1g",
        "--memory-swap=1g",
        "--cpus=2",
        "--pids-limit=256",
        "--shm-size=128m",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=64m",
        "--tmpfs",
        "/var/run/postgresql:rw,noexec,nosuid,nodev,size=16m",
        "--mount",
        (
            f"type=volume,src={volume},dst=/var/lib/postgresql/data,"
            "volume-nocopy"
        ),
        "--security-opt=no-new-privileges",
        "-e",
        "POSTGRES_USER=restore",
        "-e",
        "POSTGRES_DB=restore",
        "-e",
        "POSTGRES_HOST_AUTH_METHOD=trust",
        image_id,
    ]


class TemporaryResources:
    """Track exactly three possible containers and one possible volume."""

    def __init__(self, *, role: str, run_id: str | None = None):
        if role not in CONTRACTS:
            raise AdoptionError("temporary resource role is invalid")
        self.role = role
        self.run_id = run_id or secrets.token_hex(8)
        if re.fullmatch(r"[0-9a-f]{16}", self.run_id) is None:
            raise AdoptionError("temporary resource run identity is invalid")
        self.operation_id = _operation_id(role, self.run_id)
        self.prefix = (
            f"tb3-{CURRENT_CAMPAIGN_ID[:8]}-{role.replace('_', '-')}-{self.run_id}"
        )
        self.container_reservations: dict[str, str] = {}
        self.container_constraints: dict[str, dict[str, Any]] = {}
        self.container_ids: dict[str, str] = {}
        self.created_container_ids: dict[str, str] = {}
        self.created_containers: list[str] = []
        self.active_volume: str | None = None
        self.active_volume_recorded = False

    @property
    def active_containers(self) -> set[str]:
        return set(self.container_reservations)

    def _container_exists(self, identity: str) -> bool:
        result = _probe([DOCKER, "container", "inspect", identity])
        if result.returncode not in {0, 1}:
            raise AdoptionError("cannot safely determine scratch container existence")
        return result.returncode == 0

    def _volume_exists(self, name: str) -> bool:
        result = _probe([DOCKER, "volume", "inspect", name])
        if result.returncode not in {0, 1}:
            raise AdoptionError("cannot safely determine scratch volume existence")
        return result.returncode == 0

    def assert_initially_absent(self) -> None:
        container_names = [
            f"{self.prefix}-uploads",
            f"{self.prefix}-audit",
            f"{self.prefix}-restore",
        ]
        volume_name = f"{self.prefix}-pgdata"
        if any(self._container_exists(name) for name in container_names):
            raise AdoptionError("a fixed temporary container name is already occupied")
        if self._volume_exists(volume_name):
            raise AdoptionError("the fixed temporary volume name is already occupied")
        labeled_containers = _run(
            [
                DOCKER,
                "container",
                "ls",
                "-aq",
                "--filter",
                f"label={TEMP_LABEL_KEY}={self.operation_id}",
            ]
        )
        labeled_volumes = _run(
            [
                DOCKER,
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label={TEMP_LABEL_KEY}={self.operation_id}",
            ]
        )
        if labeled_containers or labeled_volumes:
            raise AdoptionError("labeled temporary resource residue already exists")

    def reserve_container(
        self,
        purpose: str,
        *,
        expected_image_id: str,
        expected_mount_volume: str | None = None,
        expected_mount_destination: str | None = None,
        expected_mount_readonly: bool | None = None,
        expected_entrypoint: list[str] | None = None,
        expected_cmd: list[str] | None = None,
        recovery: bool = False,
    ) -> str:
        if purpose not in {"uploads", "audit", "restore"}:
            raise AdoptionError("temporary container purpose is invalid")
        if IMAGE_ID_RE.fullmatch(expected_image_id) is None:
            raise AdoptionError("temporary container image reservation is invalid")
        mount_values = (
            expected_mount_volume,
            expected_mount_destination,
            expected_mount_readonly,
        )
        if any(value is not None for value in mount_values) and (
            expected_mount_volume is None
            or VOLUME_NAME_RE.fullmatch(expected_mount_volume) is None
            or expected_mount_destination
            not in {"/source", "/var/lib/postgresql/data"}
            or type(expected_mount_readonly) is not bool
        ):
            raise AdoptionError("temporary container mount reservation is invalid")
        if purpose in {"uploads", "audit"}:
            expected_entrypoint = ["tar"]
            expected_cmd = ["-C", "/source", "-czf", "-", "."]
        else:
            expected_entrypoint = (
                list(SCRATCH_POSTGRES_ENTRYPOINT)
                if SCRATCH_POSTGRES_ENTRYPOINT is not None
                else None
            )
            expected_cmd = (
                list(SCRATCH_POSTGRES_CMD)
                if SCRATCH_POSTGRES_CMD is not None
                else None
            )
        name = f"{self.prefix}-{purpose}"
        if name in self.created_containers or len(self.created_containers) >= 3:
            raise AdoptionError("temporary container creation budget exceeded")
        if self._container_exists(name) and not recovery:
            raise AdoptionError("temporary container name became occupied")
        self.created_containers.append(name)
        self.container_reservations[name] = expected_image_id
        self.container_constraints[name] = {
            "purpose": purpose,
            "mount_volume": expected_mount_volume,
            "mount_destination": expected_mount_destination,
            "mount_readonly": expected_mount_readonly,
            "entrypoint": (
                _command_vector(
                    expected_entrypoint,
                    label="reserved temporary entrypoint",
                )
                if expected_entrypoint is not None
                else None
            ),
            "cmd": (
                _command_vector(
                    expected_cmd, label="reserved temporary command"
                )
                if expected_cmd is not None
                else None
            ),
        }
        return name

    def record_container(self, name: str, creation_output: str) -> str:
        if name not in self.container_reservations:
            raise AdoptionError("cannot record an unreserved temporary container")
        observed_id = _inspect_value(name, "{{.Id}}")
        if (
            re.fullmatch(r"[0-9a-f]{64}", creation_output) is None
            or observed_id != creation_output
        ):
            raise AdoptionError("temporary container returned an ambiguous full identity")
        self._verify_container_ownership(name, observed_id)
        self.container_ids[name] = observed_id
        self.created_container_ids[name] = observed_id
        return observed_id

    def _verify_container_ownership(self, name: str, container_id: str) -> None:
        expected_image = self.container_reservations.get(name)
        if (
            expected_image is None
            or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
            or not self._container_exists(container_id)
            or _inspect_value(container_id, "{{.Id}}") != container_id
            or _inspect_value(container_id, "{{.Name}}") != f"/{name}"
            or _inspect_value(name, "{{.Id}}") != container_id
            or _inspect_value(container_id, "{{.Image}}") != expected_image
            or _inspect_value(
                container_id,
                f'{{{{index .Config.Labels "{TEMP_LABEL_KEY}"}}}}',
            )
            != self.operation_id
        ):
            raise AdoptionError("temporary container ownership revalidation failed")

    def _adopt_unrecorded_container(self, name: str) -> str:
        constraints = self.container_constraints.get(name)
        if constraints is None or constraints.get("mount_volume") is None:
            raise AdoptionError(
                "unrecorded container lacks exact recovery constraints"
            )
        container_id = _inspect_value(name, "{{.Id}}")
        self._verify_container_ownership(name, container_id)
        purpose = constraints["purpose"]
        expected_memory = "1073741824" if purpose == "restore" else "268435456"
        expected_cpus = "2000000000" if purpose == "restore" else "1000000000"
        expected_pids = "256" if purpose == "restore" else "128"
        expected_tmpfs = (
            {
                "/tmp": "rw,noexec,nosuid,nodev,size=64m",
                "/var/run/postgresql": (
                    "rw,noexec,nosuid,nodev,size=16m"
                ),
            }
            if purpose == "restore"
            else {}
        )
        expected_cap_drop = [] if purpose == "restore" else ["ALL"]
        try:
            binds = json.loads(
                _inspect_value(container_id, "{{json .HostConfig.Binds}}"),
                object_pairs_hook=_strict_object,
            )
            tmpfs = json.loads(
                _inspect_value(container_id, "{{json .HostConfig.Tmpfs}}"),
                object_pairs_hook=_strict_object,
            )
            cap_drop = json.loads(
                _inspect_value(container_id, "{{json .HostConfig.CapDrop}}"),
                object_pairs_hook=_strict_object,
            )
            cap_add = json.loads(
                _inspect_value(container_id, "{{json .HostConfig.CapAdd}}"),
                object_pairs_hook=_strict_object,
            )
            devices = json.loads(
                _inspect_value(container_id, "{{json .HostConfig.Devices}}"),
                object_pairs_hook=_strict_object,
            )
            device_requests = json.loads(
                _inspect_value(
                    container_id,
                    "{{json .HostConfig.DeviceRequests}}",
                ),
                object_pairs_hook=_strict_object,
            )
            security_opt = json.loads(
                _inspect_value(container_id, "{{json .HostConfig.SecurityOpt}}"),
                object_pairs_hook=_strict_object,
            )
            port_bindings = json.loads(
                _inspect_value(
                    container_id, "{{json .HostConfig.PortBindings}}"
                ),
                object_pairs_hook=_strict_object,
            )
        except json.JSONDecodeError as exc:
            raise AdoptionError(
                "unrecorded container host configuration is unreadable"
            ) from exc
        if (
            _inspect_value(container_id, "{{.State.Status}}")
            not in {"created", "running", "exited"}
            or _inspect_value(container_id, "{{.HostConfig.NetworkMode}}") != "none"
            or _inspect_value(container_id, "{{.HostConfig.ReadonlyRootfs}}")
            != "true"
            or _inspect_value(container_id, "{{.HostConfig.LogConfig.Type}}")
            != "none"
            or _inspect_value(container_id, "{{.HostConfig.RestartPolicy.Name}}")
            != "no"
            or _inspect_value(container_id, "{{.HostConfig.Memory}}")
            != expected_memory
            or _inspect_value(container_id, "{{.HostConfig.NanoCpus}}")
            != expected_cpus
            or _inspect_value(container_id, "{{.HostConfig.PidsLimit}}")
            != expected_pids
            or _inspect_value(container_id, "{{.HostConfig.Privileged}}")
            != "false"
            or binds not in (None, [])
            or (tmpfs or {}) != expected_tmpfs
            or (cap_drop or []) != expected_cap_drop
            or cap_add not in (None, [])
            or devices not in (None, [])
            or device_requests not in (None, [])
            or security_opt
            not in (
                ["no-new-privileges"],
                ["no-new-privileges:true"],
            )
            or port_bindings not in (None, {})
            or _inspect_value(container_id, "{{.HostConfig.PidMode}}")
            != ""
            or _inspect_value(container_id, "{{.HostConfig.IpcMode}}")
            not in {"", "private"}
        ):
            raise AdoptionError(
                "unrecorded container isolation boundaries differ; refusing cleanup"
            )
        try:
            mounts = json.loads(
                _inspect_value(container_id, "{{json .Mounts}}"),
                object_pairs_hook=_strict_object,
            )
        except json.JSONDecodeError as exc:
            raise AdoptionError(
                "unrecorded container mount inventory is unreadable"
            ) from exc
        if not isinstance(mounts, list) or any(
            not isinstance(row, dict) for row in mounts
        ):
            raise AdoptionError(
                "unrecorded container mount inventory is malformed"
            )
        volume_mounts = [row for row in mounts if row.get("Type") == "volume"]
        tmpfs_mounts = [row for row in mounts if row.get("Type") == "tmpfs"]
        expected_tmpfs_destinations = set(expected_tmpfs)
        if (
            any(
                row.get("Type") not in {"volume", "tmpfs"}
                for row in mounts
            )
            or
            len(volume_mounts) != 1
            or volume_mounts[0].get("Name") != constraints["mount_volume"]
            or volume_mounts[0].get("Destination")
            != constraints["mount_destination"]
            or volume_mounts[0].get("RW")
            is not (not constraints["mount_readonly"])
            or {
                str(row.get("Destination")) for row in tmpfs_mounts
            }
            not in (set(), expected_tmpfs_destinations)
            or any(row.get("RW") is not True for row in tmpfs_mounts)
        ):
            raise AdoptionError(
                "unrecorded container mount boundaries differ; refusing cleanup"
            )
        try:
            raw_entrypoint = json.loads(
                _inspect_value(container_id, "{{json .Config.Entrypoint}}")
            )
            raw_command = json.loads(
                _inspect_value(container_id, "{{json .Config.Cmd}}")
            )
            observed_entrypoint = _command_vector(
                raw_entrypoint, label="unrecorded container entrypoint"
            )
            observed_command = _command_vector(
                raw_command, label="unrecorded container command"
            )
        except (json.JSONDecodeError, AdoptionError) as exc:
            raise AdoptionError(
                "unrecorded container command identity is unreadable"
            ) from exc
        if (
            observed_entrypoint != constraints["entrypoint"]
            or observed_command != constraints["cmd"]
        ):
            raise AdoptionError(
                "unrecorded container command differs; refusing cleanup"
            )
        if purpose == "restore":
            try:
                environment = json.loads(
                    _inspect_value(container_id, "{{json .Config.Env}}")
                )
            except json.JSONDecodeError as exc:
                raise AdoptionError(
                    "unrecorded restore environment is unreadable"
                ) from exc
            required_environment = {
                "POSTGRES_USER=restore",
                "POSTGRES_DB=restore",
                "POSTGRES_HOST_AUTH_METHOD=trust",
            }
            if (
                not isinstance(environment, list)
                or not required_environment.issubset(set(environment))
                or any(
                    sum(item.startswith(f"{key}=") for item in environment)
                    != 1
                    for key in (
                        "POSTGRES_USER",
                        "POSTGRES_DB",
                        "POSTGRES_HOST_AUTH_METHOD",
                    )
                )
            ):
                raise AdoptionError(
                    "unrecorded restore environment differs; refusing cleanup"
                )
        self.container_ids[name] = container_id
        self.created_container_ids[name] = container_id
        return container_id

    def reserve_volume(self, *, recovery: bool = False) -> str:
        name = f"{self.prefix}-pgdata"
        if self.active_volume is not None or (
            self._volume_exists(name) and not recovery
        ):
            raise AdoptionError("scratch volume creation budget is unavailable")
        self.active_volume = name
        self.active_volume_recorded = False
        return name

    def record_volume(self, name: str, creation_output: str) -> None:
        if (
            name != self.active_volume
            or creation_output != name
        ):
            raise AdoptionError("scratch volume ownership recording failed")
        self._verify_volume_ownership(name)
        self.active_volume_recorded = True

    def _verify_volume_ownership(self, name: str) -> None:
        if (
            name != self.active_volume
            or not self._volume_exists(name)
            or _run([DOCKER, "volume", "inspect", "--format", "{{.Name}}", name])
            != name
            or _run(
                [
                    DOCKER,
                    "volume",
                    "inspect",
                    "--format",
                    f'{{{{index .Labels "{TEMP_LABEL_KEY}"}}}}',
                    name,
                ]
            )
            != self.operation_id
            or _run([DOCKER, "volume", "inspect", "--format", "{{.Driver}}", name])
            != "local"
            or _run([DOCKER, "volume", "inspect", "--format", "{{.Scope}}", name])
            != "local"
        ):
            raise AdoptionError("scratch volume ownership/isolation revalidation failed")

    def _adopt_unrecorded_volume(self, name: str) -> None:
        self._verify_volume_ownership(name)
        self.active_volume_recorded = True

    def remove_container(self, name: str) -> None:
        if name not in self.container_reservations:
            raise AdoptionError("refusing to remove an untracked container")
        if name not in self.container_ids:
            if self._container_exists(name):
                self._adopt_unrecorded_container(name)
            else:
                self.container_reservations.pop(name)
                self.container_constraints.pop(name, None)
                return
        if not self._container_exists(name):
            container_id = self.container_ids.get(name)
            if container_id is not None and self._container_exists(container_id):
                raise AdoptionError("tracked container was renamed; refusing cleanup")
            self.container_reservations.pop(name)
            self.container_constraints.pop(name, None)
            self.container_ids.pop(name, None)
            return
        container_id = self.container_ids.get(name) or _inspect_value(name, "{{.Id}}")
        self._verify_container_ownership(name, container_id)
        result = _probe(
            [DOCKER, "container", "rm", "-f", "-v", container_id], timeout=60
        )
        if (
            result.returncode != 0
            or self._container_exists(container_id)
            or self._container_exists(name)
        ):
            raise AdoptionError("tracked temporary container cleanup failed")
        self.container_reservations.pop(name)
        self.container_constraints.pop(name, None)
        self.container_ids.pop(name, None)

    def remove_volume(self, name: str) -> None:
        if name != self.active_volume:
            raise AdoptionError("refusing to remove an untracked volume")
        if not self.active_volume_recorded:
            if self._volume_exists(name):
                self._adopt_unrecorded_volume(name)
            else:
                self.active_volume = None
                return
        if not self._volume_exists(name):
            self.active_volume = None
            self.active_volume_recorded = False
            return
        self._verify_volume_ownership(name)
        result = _probe([DOCKER, "volume", "rm", name], timeout=60)
        if result.returncode != 0 or self._volume_exists(name):
            raise AdoptionError("tracked scratch volume cleanup failed")
        self.active_volume = None
        self.active_volume_recorded = False

    def cleanup(self) -> None:
        failures: list[str] = []
        for name in sorted(self.container_reservations):
            try:
                self.remove_container(name)
            except Exception:
                failures.append(name)
        if self.active_volume is not None:
            name = self.active_volume
            try:
                self.remove_volume(name)
            except Exception:
                failures.append(name)
        if failures:
            raise AdoptionError("strict temporary cleanup failed")

    def assert_zero_residue(self) -> None:
        if (
            self.container_reservations
            or self.container_constraints
            or self.container_ids
            or self.active_volume is not None
            or self.active_volume_recorded
        ):
            raise AdoptionError("temporary resource tracker is not empty")
        labeled_containers = _run(
            [
                DOCKER,
                "container",
                "ls",
                "-aq",
                "--filter",
                f"label={TEMP_LABEL_KEY}={self.operation_id}",
            ]
        )
        labeled_volumes = _run(
            [
                DOCKER,
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label={TEMP_LABEL_KEY}={self.operation_id}",
            ]
        )
        if labeled_containers or labeled_volumes:
            raise AdoptionError("temporary labeled resources remain after cleanup")
        if len(self.created_containers) > 3:
            raise AdoptionError("temporary container budget was exceeded")


def _create_archive(
    *,
    tracker: TemporaryResources,
    purpose: str,
    target: Path,
    source_volume: str,
    app_image_id: str,
    authorize_effect: Callable[[], None],
) -> int:
    name = tracker.reserve_container(
        purpose,
        expected_image_id=app_image_id,
        expected_mount_volume=source_volume,
        expected_mount_destination="/source",
        expected_mount_readonly=True,
        expected_entrypoint=["tar"],
        expected_cmd=["-C", "/source", "-czf", "-", "."],
    )
    try:
        authorize_effect()
        created_id = _run(
            archive_container_create_command(
                name=name,
                operation_id=tracker.operation_id,
                source_volume=source_volume,
                app_image_id=app_image_id,
            )
        )
        if not created_id:
            raise AdoptionError("archive worker creation returned no container identity")
        full_id = tracker.record_container(name, created_id)
        _verify_archive_worker(
            container_id=full_id,
            operation_id=tracker.operation_id,
            source_volume=source_volume,
            app_image_id=app_image_id,
        )
        authorize_effect()
        _stream_command_to_exclusive_file(
            [DOCKER, "container", "start", "-a", full_id],
            target,
            timeout=900,
        )
        if _inspect_value(full_id, "{{.State.ExitCode}}") != "0":
            target.unlink(missing_ok=True)
            raise AdoptionError("archive worker exited unsuccessfully")
    finally:
        if name in tracker.active_containers:
            tracker.remove_container(name)
    return _verify_tar_artifact(target)


def _scratch_psql(container: str, sql: str) -> str:
    return _run(
        [
            DOCKER,
            "exec",
            container,
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "restore",
            "-d",
            "restore",
            "-Atqc",
            sql,
        ],
        timeout=120,
    )


def _wait_for_scratch(container: str, *, attempts: int = 60) -> None:
    for _attempt in range(attempts):
        ready = _probe(
            [DOCKER, "exec", container, "pg_isready", "-U", "restore", "-d", "restore"],
            timeout=5,
        )
        if ready.returncode == 0:
            query = _probe(
                [
                    DOCKER,
                    "exec",
                    container,
                    "psql",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-U",
                    "restore",
                    "-d",
                    "restore",
                    "-Atqc",
                    "SELECT 1",
                ],
                timeout=5,
            )
            if query.returncode == 0 and query.stdout.strip() == "1":
                return
        time.sleep(1)
    raise AdoptionError("scratch PostgreSQL database did not become ready")


def _verify_scratch_worker(
    *,
    container_id: str,
    operation_id: str,
    volume: str,
    image_id: str,
) -> None:
    try:
        entrypoint = _command_vector(
            json.loads(
                _inspect_value(container_id, "{{json .Config.Entrypoint}}")
            ),
            label="scratch restore entrypoint",
        )
        command = _command_vector(
            json.loads(_inspect_value(container_id, "{{json .Config.Cmd}}")),
            label="scratch restore command",
        )
    except (json.JSONDecodeError, AdoptionError) as exc:
        raise AdoptionError(
            "scratch restore command identity is unreadable"
        ) from exc
    if (
        _inspect_value(container_id, "{{.Image}}") != image_id
        or entrypoint != SCRATCH_POSTGRES_ENTRYPOINT
        or command != SCRATCH_POSTGRES_CMD
        or _inspect_value(container_id, "{{.HostConfig.NetworkMode}}") != "none"
        or _inspect_value(container_id, "{{.HostConfig.ReadonlyRootfs}}") != "true"
        or _inspect_value(container_id, "{{.HostConfig.LogConfig.Type}}") != "none"
        or _inspect_value(container_id, "{{.HostConfig.RestartPolicy.Name}}") != "no"
        or _inspect_value(container_id, "{{.HostConfig.Memory}}") != "1073741824"
        or _inspect_value(container_id, "{{.HostConfig.NanoCpus}}") != "2000000000"
        or _inspect_value(container_id, "{{.HostConfig.PidsLimit}}") != "256"
        or _inspect_value(
            container_id, f'{{{{index .Config.Labels "{TEMP_LABEL_KEY}"}}}}'
        )
        != operation_id
        or _inspect_value(container_id, "{{json .HostConfig.PortBindings}}")
        not in {"null", "{}"}
    ):
        raise AdoptionError("scratch restore isolation/image verification failed")
    try:
        mounts = json.loads(
            _inspect_value(container_id, "{{json .Mounts}}"),
            object_pairs_hook=_strict_object,
        )
    except json.JSONDecodeError as exc:
        raise AdoptionError("scratch restore mount inventory is unreadable") from exc
    matching = [
        row
        for row in mounts
        if isinstance(row, dict)
        and row.get("Destination") == "/var/lib/postgresql/data"
    ]
    if (
        len(
            [
                row
                for row in mounts
                if isinstance(row, dict) and row.get("Type") == "volume"
            ]
        )
        != 1
        or len(matching) != 1
        or matching[0].get("Name") != volume
        or matching[0].get("RW") is not True
    ):
        raise AdoptionError("scratch restore does not use the one tracked named volume")


def _restore_drill(
    *,
    tracker: TemporaryResources,
    dump_path: Path,
    scratch_image_id: str,
    authorize_effect: Callable[[], None],
) -> dict[str, Any]:
    volume = tracker.reserve_volume()
    container = tracker.reserve_container(
        "restore",
        expected_image_id=scratch_image_id,
        expected_mount_volume=volume,
        expected_mount_destination="/var/lib/postgresql/data",
        expected_mount_readonly=False,
        expected_entrypoint=(
            list(SCRATCH_POSTGRES_ENTRYPOINT)
            if SCRATCH_POSTGRES_ENTRYPOINT is not None
            else None
        ),
        expected_cmd=(
            list(SCRATCH_POSTGRES_CMD)
            if SCRATCH_POSTGRES_CMD is not None
            else None
        ),
    )
    try:
        authorize_effect()
        created_volume = _run(
            [
                DOCKER,
                "volume",
                "create",
                "--driver",
                "local",
                "--label",
                f"{TEMP_LABEL_KEY}={tracker.operation_id}",
                volume,
            ]
        )
        if created_volume != volume:
            raise AdoptionError("scratch volume creation returned an unexpected identity")
        tracker.record_volume(volume, created_volume)
        authorize_effect()
        created_container = _run(
            scratch_container_create_command(
                name=container,
                operation_id=tracker.operation_id,
                volume=volume,
                image_id=scratch_image_id,
            )
        )
        if not created_container:
            raise AdoptionError("scratch container creation returned no identity")
        full_id = tracker.record_container(container, created_container)
        _verify_scratch_worker(
            container_id=full_id,
            operation_id=tracker.operation_id,
            volume=volume,
            image_id=scratch_image_id,
        )
        authorize_effect()
        _run([DOCKER, "container", "start", full_id], timeout=60)
        _wait_for_scratch(full_id)
        version_num = _scratch_psql(full_id, "SHOW server_version_num")
        if not re.fullmatch(r"15[0-9]{4}", version_num):
            raise AdoptionError("scratch image did not start PostgreSQL major version 15")
        authorize_effect()
        _run_file_input(
            [
                DOCKER,
                "exec",
                "-i",
                full_id,
                "pg_restore",
                "-U",
                "restore",
                "-d",
                "restore",
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
            ],
            dump_path,
            timeout=900,
        )
        revision = _scratch_psql(full_id, "SELECT version_num FROM alembic_version")
        system_id = _scratch_psql(
            full_id, "SELECT system_identifier FROM pg_control_system()"
        )
        fingerprint, row_count, table_count = _database_fingerprint(
            lambda sql: _scratch_psql(full_id, sql)
        )
        return {
            "status": "passed",
            "postgres_major": 15,
            "scratch_image_id": scratch_image_id,
            "restored_alembic_revision": revision,
            "scratch_postgres_system_id": system_id,
            "database_fingerprint_sha256": fingerprint,
            "database_row_count": row_count,
            "public_table_count": table_count,
            "network_mode": "none",
            "published_ports": False,
            "named_scratch_volume": True,
        }
    finally:
        cleanup_error: Exception | None = None
        if container in tracker.active_containers:
            try:
                tracker.remove_container(container)
            except Exception as exc:  # Preserve strict cleanup as the final failure.
                cleanup_error = exc
        if volume == tracker.active_volume:
            try:
                tracker.remove_volume(volume)
            except Exception as exc:
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            raise AdoptionError("restore drill temporary cleanup failed") from cleanup_error


def _verify_archive_worker(
    *,
    container_id: str,
    operation_id: str,
    source_volume: str,
    app_image_id: str,
) -> None:
    if (
        _inspect_value(container_id, "{{.Image}}") != app_image_id
        or _inspect_value(container_id, "{{.HostConfig.NetworkMode}}") != "none"
        or _inspect_value(container_id, "{{.HostConfig.ReadonlyRootfs}}") != "true"
        or _inspect_value(container_id, "{{.HostConfig.LogConfig.Type}}") != "none"
        or _inspect_value(container_id, "{{.HostConfig.RestartPolicy.Name}}") != "no"
        or _inspect_value(container_id, "{{.HostConfig.Memory}}") != "268435456"
        or _inspect_value(container_id, "{{.HostConfig.NanoCpus}}") != "1000000000"
        or _inspect_value(container_id, "{{.HostConfig.PidsLimit}}") != "128"
        or _inspect_value(
            container_id, f'{{{{index .Config.Labels "{TEMP_LABEL_KEY}"}}}}'
        )
        != operation_id
    ):
        raise AdoptionError("archive worker isolation/image verification failed")
    try:
        mounts = json.loads(
            _inspect_value(container_id, "{{json .Mounts}}"),
            object_pairs_hook=_strict_object,
        )
    except json.JSONDecodeError as exc:
        raise AdoptionError("archive worker mount inventory is unreadable") from exc
    if (
        not isinstance(mounts, list)
        or len(mounts) != 1
        or not isinstance(mounts[0], dict)
        or mounts[0].get("Destination") != "/source"
        or mounts[0].get("Type") != "volume"
        or mounts[0].get("Name") != source_volume
        or mounts[0].get("RW") is not False
    ):
        raise AdoptionError("archive source mount is not uniquely read-only")


def confirmation_phrase(role: str, historical_freeze_sha256: str) -> str:
    if role not in CONTRACTS or SHA256_RE.fullmatch(historical_freeze_sha256) is None:
        raise AdoptionError("confirmation binding is invalid")
    return (
        f"adopt-frozen-backup:{CURRENT_CAMPAIGN_ID}:{role}:"
        f"{historical_freeze_sha256}:{CURRENT_RELEASE_SHA}"
    )


def _emit_approval_subject(args: argparse.Namespace) -> dict[str, Any]:
    contract = CONTRACTS[args.source_role]
    if (
        args.inventory != CURRENT_INVENTORY_PATH
        or args.inventory_approval != contract.inventory_approval
        or args.approval_policy != CURRENT_APPROVAL_POLICY_PATH
        or args.approval_subject_output != contract.source_adoption_subject
        or args.output_dir != contract.output_dir
    ):
        raise AdoptionError("approval-subject inputs differ from the contract")
    _validate_new_output_path(contract.output_dir)
    _verify_exact_release()
    inventory_approval = _verify_current_approval(
        args, require_fresh=False
    )
    historical_freeze_raw = _secure_bytes(
        contract.freeze_evidence_path,
        maximum=4 * 1024 * 1024,
    )
    if _sha256(historical_freeze_raw) != contract.freeze_evidence_sha256:
        raise AdoptionError(
            "historical freeze bytes differ from the approval contract"
        )
    subject = source_adoption_approval_subject(args.source_role)
    raw = (json.dumps(subject, sort_keys=True, indent=2) + "\n").encode()
    _exclusive_write(contract.source_adoption_subject, raw)
    return {
        "status": "source-adoption-approval-subject-ready",
        "action": SOURCE_ADOPTION_ACTION,
        "environment": "staging",
        "approval_subject_path": str(contract.source_adoption_subject),
        "approval_subject_sha256": _canonical_hash(subject),
        "approval_subject_raw_sha256": _sha256(raw),
        "adoption_contract_sha256": ADOPTION_CONTRACT_SHA256,
        "inventory_sha256": inventory_approval["inventory_sha256"],
        "inventory_approval_token_sha256": inventory_approval[
            "approval_token_sha256"
        ],
        "max_ttl_seconds": 3600,
        "temporary_resource_budget": {
            "containers": MAX_TEMPORARY_CONTAINERS,
            "named_volumes": MAX_SCRATCH_VOLUMES,
            "network": "none",
        },
        "output_dir": str(contract.output_dir),
        "run_id": contract.run_id,
        "operation_id": _operation_id(contract.role, contract.run_id),
        "docker_access": False,
    }


def _preflight(
    args: argparse.Namespace, *, recovery: bool = False
) -> dict[str, Any]:
    contract = CONTRACTS[args.source_role]
    if (
        args.inventory != CURRENT_INVENTORY_PATH
        or args.inventory_approval != contract.inventory_approval
        or args.source_adoption_approval != contract.source_adoption_approval
        or args.output_dir != contract.output_dir
        or args.approval_policy != CURRENT_APPROVAL_POLICY_PATH
        or args.historical_restore_evidence != contract.restore_evidence_path
        or args.historical_adopted_freeze_evidence != contract.adopted_freeze_path
        or args.historical_freeze_evidence != contract.freeze_evidence_path
        or args.historical_restore_bundle != contract.restore_bundle_path
        or args.env_file != contract.env_file
        or args.scratch_postgres_image_id != SCRATCH_POSTGRES_IMAGE_ID
    ):
        raise AdoptionError("runtime paths/images differ from the adoption contract")
    if recovery:
        _validate_existing_output_directory(args.output_dir)
    else:
        _validate_new_output_path(args.output_dir)
    _verify_exact_release()
    recovery_header: dict[str, Any] | None = None
    if recovery:
        recovery_header = _read_recovery_journal_header(
            output_dir=args.output_dir,
            source_role=args.source_role,
        )
        inventory_approval = _verify_current_approval(
            args, require_fresh=False
        )
        source_adoption_approval = _verify_source_adoption_approval(
            args, require_fresh=False
        )
        if {
            field: inventory_approval.get(field)
            for field in INVENTORY_APPROVAL_BINDING_FIELDS
        } != recovery_header["inventory_approval"]:
            raise AdoptionError(
                "historical recovery inventory approval differs from the journal"
            )
        if source_adoption_approval != recovery_header[
            "source_adoption_approval"
        ]:
            raise AdoptionError(
                "historical source-adoption approval differs from the journal"
            )
    else:
        inventory_approval = _verify_current_approval(
            args, require_fresh=False
        )
        source_adoption_approval = _verify_source_adoption_approval(
            args, require_fresh=True
        )
        expiry = _utc(
            source_adoption_approval["approval_expires_at"],
            label="source-adoption approval",
        )
        remaining = (expiry - datetime.now(timezone.utc)).total_seconds()
        if remaining < MIN_APPROVAL_REMAINING_SECONDS:
            raise AdoptionError(
                "source-adoption approval has less than 20 minutes remaining"
            )
    if recovery:
        recovery_preflight = {
            "contract": contract,
            "inventory_approval": inventory_approval,
            "source_adoption_approval": source_adoption_approval,
        }
        tracker, recovery_journal = _load_recovery_tracker(
            output_dir=args.output_dir,
            preflight=recovery_preflight,
            scratch_image_id=args.scratch_postgres_image_id,
        )
        if recovery_journal["raw_sha256"] != recovery_header["raw_sha256"]:
            raise AdoptionError("recovery journal changed during secure preflight")
        return {
            **recovery_preflight,
            "tracker": tracker,
            "recovery_journal": recovery_journal,
            "protected_identities": recovery_journal["payload"][
                "protected_source_identities"
            ],
            "required_confirmation": recovery_confirmation_phrase(tracker),
        }
    historical = _validate_historical_chain(
        contract=contract,
        adopted_freeze_path=args.historical_adopted_freeze_evidence,
        restore_evidence_path=args.historical_restore_evidence,
        freeze_evidence_path=args.historical_freeze_evidence,
        restore_bundle_path=args.historical_restore_bundle,
    )
    for service, image_id in sorted(historical["service_images"].items()):
        _verify_local_image(image_id, label=f"historical {service}")
    if IMAGE_ID_RE.fullmatch(args.scratch_postgres_image_id) is None:
        raise AdoptionError("scratch PostgreSQL image must be one exact sha256 image ID")
    _verify_scratch_image_identity(args.scratch_postgres_image_id)

    snapshot = _project_snapshot(contract, historical["service_images"])
    app_stop_chronology = _validate_app_stop_chronology(
        contract=contract,
        snapshot=snapshot,
        historical=historical,
    )
    env = _secure_env(
        args.env_file, expected_sha256=contract.env_sha256
    )
    measurement = _measure_source(snapshot=snapshot, env=env)
    _validate_source_against_official_freeze(measurement, historical)
    partial = {
        "contract": contract,
        "inventory_approval": inventory_approval,
        "source_adoption_approval": source_adoption_approval,
        "historical": historical,
        "snapshot": snapshot,
        "env": env,
        "measurement": measurement,
        "app_stop_chronology": app_stop_chronology,
    }
    tracker = TemporaryResources(
        role=args.source_role, run_id=contract.run_id
    )
    protected_identities = _validate_protected_source_identities(
        contract=contract,
        boundaries=inventory_approval["_production_boundaries"],
        snapshot=snapshot,
        measurement=measurement,
        scratch_volume_name=f"{tracker.prefix}-pgdata",
    )
    tracker.assert_initially_absent()
    result = {
        **partial,
        "tracker": tracker,
        "protected_identities": protected_identities,
        "required_confirmation": confirmation_phrase(
            args.source_role, historical["freeze_raw_sha256"]
        ),
    }
    return result


def _write_rollback_and_freeze(
    *,
    output_dir: Path,
    preflight: dict[str, Any],
) -> dict[str, Any]:
    contract: HistoricalContract = preflight["contract"]
    historical = preflight["historical"]
    measured = preflight["measurement"]

    historical_copy = (
        output_dir / f"historical-compose-{historical['compose_sha256']}.yaml"
    )
    _exclusive_write(historical_copy, historical["compose_raw"])
    pinned_raw = _derive_pinned_compose(
        historical,
        role=contract.role,
        raw_copy_path=historical_copy,
    )
    pinned_hash = _sha256(pinned_raw)
    pinned_path = output_dir / f"legacy-compose-pinned-{pinned_hash}.yaml"
    _exclusive_write(pinned_path, pinned_raw)

    captured_at = datetime.now(timezone.utc).isoformat()
    rollback = {
        "schema": "three-site-staging-legacy-restore-bundle-v1",
        "campaign_id": CURRENT_CAMPAIGN_ID,
        "target_release_sha": CURRENT_RELEASE_SHA,
        "project_name": contract.project,
        "captured_at": captured_at,
        "source_releases": {contract.role: SOURCE_RELEASE_SHA},
        "previously_running_services": historical["previously_running_services"],
        "compose": {
            "path": str(pinned_path),
            "sha256": pinned_hash,
            "size": len(pinned_raw),
        },
        "service_images": dict(sorted(historical["service_images"].items())),
    }
    rollback_raw = (json.dumps(rollback, sort_keys=True, indent=2) + "\n").encode()
    rollback_hash = _sha256(rollback_raw)
    rollback_path = output_dir / f"legacy-restore-bundle-{rollback_hash}.json"
    _exclusive_write(rollback_path, rollback_raw)

    freeze = {
        "schema": "three-site-staging-source-freeze-v1",
        "campaign_id": CURRENT_CAMPAIGN_ID,
        "target_release_sha": CURRENT_RELEASE_SHA,
        "project_name": contract.project,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source_roles": [
            {
                "source_role": contract.role,
                "app_service": contract.app_service,
                "source_release_sha": SOURCE_RELEASE_SHA,
            }
        ],
        "previously_running_services": historical["previously_running_services"],
        # These are inherited from and cryptographically bound to the verified
        # restore->official-freeze chain; this helper performs no stop request.
        "stopped_services": sorted(historical["freeze"]["stopped_services"]),
        "running_services": ["db", "redis"],
        "postgres": measured["postgres"],
        "redis_observation": measured["redis_observation"],
        "legacy_restore_bundle": {
            "schema": "three-site-staging-legacy-restore-bundle-reference-v1",
            "path": str(rollback_path),
            "sha256": rollback_hash,
            "size": len(rollback_raw),
        },
    }
    freeze_raw = (json.dumps(freeze, sort_keys=True, indent=2) + "\n").encode()
    freeze_path = output_dir / f"source-freeze-{contract.role.replace('_', '-')}.json"
    _exclusive_write(freeze_path, freeze_raw)

    restore_verifier = _import_exact_release_module(
        "scripts.restore_three_site_staging_sources",
        "scripts/restore_three_site_staging_sources.py",
    )
    backup_verifier = _import_exact_release_module(
        "scripts.run_three_site_staging_source_backup",
        "scripts/run_three_site_staging_source_backup.py",
    )
    restore_verifier.verify_restore_input(
        freeze,
        campaign_id=CURRENT_CAMPAIGN_ID,
        release_sha=CURRENT_RELEASE_SHA,
        project_name=contract.project,
    )
    restore_verifier._load_legacy_restore_bundle(
        freeze["legacy_restore_bundle"], evidence=freeze
    )
    backup_verifier._load_freeze_evidence(
        freeze_path,
        campaign_id=CURRENT_CAMPAIGN_ID,
        target_release_sha=CURRENT_RELEASE_SHA,
        source_role=contract.role,
        expected_source_release_sha=SOURCE_RELEASE_SHA,
        project_name=contract.project,
    )
    return {
        "historical_compose_copy": historical_copy,
        "pinned_compose": pinned_path,
        "pinned_compose_sha256": pinned_hash,
        "rollback": rollback,
        "rollback_path": rollback_path,
        "rollback_sha256": rollback_hash,
        "freeze": freeze,
        "freeze_path": freeze_path,
        "freeze_sha256": _canonical_hash(freeze),
    }


def _create_source_backups(
    *,
    args: argparse.Namespace,
    output_dir: Path,
    preflight: dict[str, Any],
    freeze_outputs: dict[str, Any],
    authorize_effect: Callable[[], None],
) -> dict[str, Any]:
    contract: HistoricalContract = preflight["contract"]
    snapshot = preflight["snapshot"]
    measured = preflight["measurement"]
    tracker: TemporaryResources = preflight["tracker"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{contract.role}-{CURRENT_CAMPAIGN_ID}-{stamp}"
    dump_path = output_dir / f"{base}.postgres.custom"
    uploads_path = output_dir / f"{base}.uploads.tar.gz"
    audit_path = output_dir / f"{base}.audit.tar.gz"
    db_id = str(snapshot["services"]["db"]["container_id"])
    app_image = str(snapshot["services"][contract.app_service]["image_id"])

    authorize_effect()
    _stream_command_to_exclusive_file(
        postgres_dump_command(
            container_id=db_id,
            user=measured["postgres_user"],
            database=measured["postgres_database"],
        ),
        dump_path,
        timeout=900,
    )
    upload_members = _create_archive(
        tracker=tracker,
        purpose="uploads",
        target=uploads_path,
        source_volume=snapshot["app_source_volumes"]["/app/uploads"],
        app_image_id=app_image,
        authorize_effect=authorize_effect,
    )
    audit_members = _create_archive(
        tracker=tracker,
        purpose="audit",
        target=audit_path,
        source_volume=snapshot["app_source_volumes"]["/app/audit_trail"],
        app_image_id=app_image,
        authorize_effect=authorize_effect,
    )
    restore = _restore_drill(
        tracker=tracker,
        dump_path=dump_path,
        scratch_image_id=args.scratch_postgres_image_id,
        authorize_effect=authorize_effect,
    )
    if (
        restore["restored_alembic_revision"]
        != measured["postgres"]["alembic_revision"]
        or restore["scratch_postgres_system_id"]
        == measured["postgres"]["system_id"]
        or restore["database_fingerprint_sha256"]
        != measured["postgres"]["database_fingerprint_sha256"]
        or restore["database_row_count"]
        != measured["postgres"]["database_row_count"]
        or restore["public_table_count"]
        != measured["postgres"]["public_table_count"]
    ):
        raise AdoptionError("restored backup differs from the freshly measured source")

    manifest_restore = {
        key: restore[key]
        for key in (
            "status",
            "restored_alembic_revision",
            "scratch_postgres_system_id",
            "database_fingerprint_sha256",
            "database_row_count",
            "public_table_count",
        )
    }
    manifest = {
        "schema": "three-site-staging-source-backup-v2",
        "campaign_id": CURRENT_CAMPAIGN_ID,
        "source_role": contract.role,
        "source_release_sha": SOURCE_RELEASE_SHA,
        "target_release_sha": CURRENT_RELEASE_SHA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_postgres_system_id": measured["postgres"]["system_id"],
        "source_alembic_revision": measured["postgres"]["alembic_revision"],
        "source_freeze_evidence_sha256": freeze_outputs["freeze_sha256"],
        "redis_observation": measured["redis_observation"],
        "artifacts": {
            "postgres": _file_reference(dump_path),
            "uploads": {
                **_file_reference(uploads_path),
                "safe_member_count": upload_members,
            },
            "audit": {
                **_file_reference(audit_path),
                "safe_member_count": audit_members,
            },
        },
        "restore_drill": manifest_restore,
        "redis_restore": False,
        "application_mutation": False,
    }
    manifest_raw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    manifest_path = output_dir / f"{base}.manifest.json"
    _exclusive_write(manifest_path, manifest_raw)
    backup_verifier = _import_exact_release_module(
        "scripts.run_three_site_staging_source_backup",
        "scripts/run_three_site_staging_source_backup.py",
    )
    backup_verifier.verify_backup_manifest(
        manifest,
        campaign_id=CURRENT_CAMPAIGN_ID,
        source_role=contract.role,
        source_release_sha=SOURCE_RELEASE_SHA,
        target_release_sha=CURRENT_RELEASE_SHA,
        verify_files=True,
    )
    return {
        "manifest": manifest,
        "manifest_path": manifest_path,
        "manifest_sha256": _canonical_hash(manifest),
        "restore": restore,
    }


def _write_cleanup_signal(
    *,
    output_dir: Path,
    tracker: TemporaryResources,
    reasons: list[BaseException],
) -> None:
    """Persist a nonsecret, no-overwrite signal for operator cleanup review."""
    signal_path = output_dir / "cleanup-required.json"
    if signal_path.exists() or signal_path.is_symlink():
        return
    payload = {
        "schema": "three-site-staging-temporary-cleanup-required-v1",
        "status": "manual-review-required",
        "campaign_id": CURRENT_CAMPAIGN_ID,
        "source_role": tracker.role,
        "operation_label": {
            "key": TEMP_LABEL_KEY,
            "value": tracker.operation_id,
        },
        "tracked_containers": [
            {
                "name": name,
                "full_id": tracker.container_ids.get(name),
                "expected_image_id": tracker.container_reservations[name],
            }
            for name in sorted(tracker.container_reservations)
        ],
        "tracked_volume": tracker.active_volume,
        "reason_classes": sorted({type(reason).__name__ for reason in reasons}),
        "automatic_destructive_retry": False,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    _exclusive_write(
        signal_path,
        (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode(),
    )


def _write_resource_journal(
    *,
    output_dir: Path,
    tracker: TemporaryResources,
    preflight: dict[str, Any],
    scratch_image_id: str,
) -> dict[str, Any]:
    contract: HistoricalContract = preflight["contract"]
    app_image_id = preflight["snapshot"]["services"][contract.app_service]["image_id"]
    source_volumes = preflight["snapshot"]["app_source_volumes"]
    scratch_volume = f"{tracker.prefix}-pgdata"
    payload = {
        "schema": "three-site-staging-temporary-resource-journal-v3",
        "status": "cleanup-required-until-completion-marker",
        "campaign_id": CURRENT_CAMPAIGN_ID,
        "release_sha": CURRENT_RELEASE_SHA,
        "deployment_id": CURRENT_DEPLOYMENT_ID,
        "host_safety_mode": CURRENT_HOST_SAFETY_MODE,
        "source_role": tracker.role,
        "run_id": tracker.run_id,
        "resource_prefix": tracker.prefix,
        "adoption_contract": {
            "path": str(ADOPTION_CONTRACT_PATH),
            "sha256": ADOPTION_CONTRACT_SHA256,
        },
        "inventory_approval": {
            field: preflight["inventory_approval"][field]
            for field in (
                "approval_id",
                "approval_token_sha256",
                "approval_expires_at",
                "inventory_sha256",
                "approval_policy_sha256",
            )
        },
        "source_adoption_approval": {
            field: preflight["source_adoption_approval"][field]
            for field in sorted(SOURCE_ADOPTION_APPROVAL_BINDING_FIELDS)
        },
        "historical_freeze_sha256": preflight["historical"]["freeze_raw_sha256"],
        "source_snapshot_sha256": _canonical_hash(preflight["snapshot"]),
        "source_measurement_sha256": _canonical_hash(preflight["measurement"]),
        "protected_source_identities": preflight["protected_identities"],
        "operation_label": {
            "key": TEMP_LABEL_KEY,
            "value": tracker.operation_id,
        },
        "allowed_containers": [
            {
                "name": f"{tracker.prefix}-uploads",
                "purpose": "uploads",
                "expected_image_id": app_image_id,
                "mount_volume": source_volumes["/app/uploads"],
                "mount_destination": "/source",
                "mount_readonly": True,
                "expected_entrypoint": ["tar"],
                "expected_cmd": ["-C", "/source", "-czf", "-", "."],
            },
            {
                "name": f"{tracker.prefix}-audit",
                "purpose": "audit",
                "expected_image_id": app_image_id,
                "mount_volume": source_volumes["/app/audit_trail"],
                "mount_destination": "/source",
                "mount_readonly": True,
                "expected_entrypoint": ["tar"],
                "expected_cmd": ["-C", "/source", "-czf", "-", "."],
            },
            {
                "name": f"{tracker.prefix}-restore",
                "purpose": "restore",
                "expected_image_id": scratch_image_id,
                "mount_volume": scratch_volume,
                "mount_destination": "/var/lib/postgresql/data",
                "mount_readonly": False,
                "expected_entrypoint": (
                    list(SCRATCH_POSTGRES_ENTRYPOINT)
                    if SCRATCH_POSTGRES_ENTRYPOINT is not None
                    else None
                ),
                "expected_cmd": (
                    list(SCRATCH_POSTGRES_CMD)
                    if SCRATCH_POSTGRES_CMD is not None
                    else None
                ),
            },
        ],
        "allowed_named_volume": {
            "name": scratch_volume,
            "driver": "local",
            "scope": "local",
        },
        "container_limit": MAX_TEMPORARY_CONTAINERS,
        "named_volume_limit": MAX_SCRATCH_VOLUMES,
        "intent_persisted_before_docker_side_effect": True,
        "automatic_cleanup_scope": "exact-full-id-after-label-name-image-revalidation",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    raw = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    path = output_dir / "temporary-resource-journal.json"
    _exclusive_write(path, raw)
    return {
        "path": path,
        "raw_sha256": _sha256(raw),
        "payload": payload,
    }


def _validate_existing_output_directory(path: Path) -> None:
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path.parent != CURRENT_CAMPAIGN_ROOT
    ):
        raise AdoptionError("recovery output must be a direct campaign-root child")
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise AdoptionError("recovery output directory is not root-owned mode-0700")


def _validated_journal_inventory_approval(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != INVENTORY_APPROVAL_BINDING_FIELDS
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            str(value.get("approval_id", "")),
        )
        is None
        or any(
            SHA256_RE.fullmatch(str(value.get(field, ""))) is None
            for field in (
                "approval_token_sha256",
                "inventory_sha256",
                "approval_policy_sha256",
            )
        )
    ):
        raise AdoptionError("resource journal approval binding is invalid")
    _utc(value["approval_expires_at"], label="journal approval")
    return value


def _validated_journal_source_adoption_approval(
    value: Any,
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != SOURCE_ADOPTION_APPROVAL_BINDING_FIELDS
        or value.get("action") != SOURCE_ADOPTION_ACTION
        or value.get("environment") != "staging"
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            str(value.get("approval_id", "")),
        )
        is None
        or any(
            SHA256_RE.fullmatch(str(value.get(field, ""))) is None
            for field in (
                "approval_token_sha256",
                "approval_token_raw_sha256",
                "approval_policy_sha256",
                "approval_subject_sha256",
                "adoption_contract_sha256",
            )
        )
    ):
        raise AdoptionError(
            "resource journal source-adoption approval binding is invalid"
        )
    issued = _utc(value["approval_issued_at"], label="journal action approval")
    expires = _utc(value["approval_expires_at"], label="journal action approval")
    if expires <= issued or expires - issued > timedelta(hours=1):
        raise AdoptionError("journal source-adoption approval lifetime is invalid")
    return value


def _read_recovery_journal_header(
    *, output_dir: Path, source_role: str
) -> dict[str, Any]:
    journal_path = output_dir / "temporary-resource-journal.json"
    journal, raw = _secure_json(journal_path)
    expected_contract = {
        "path": str(ADOPTION_CONTRACT_PATH),
        "sha256": ADOPTION_CONTRACT_SHA256,
    }
    if (
        set(journal) != RESOURCE_JOURNAL_FIELDS
        or journal.get("schema")
        != "three-site-staging-temporary-resource-journal-v3"
        or journal.get("status") != "cleanup-required-until-completion-marker"
        or journal.get("campaign_id") != CURRENT_CAMPAIGN_ID
        or journal.get("release_sha") != CURRENT_RELEASE_SHA
        or journal.get("deployment_id") != CURRENT_DEPLOYMENT_ID
        or journal.get("host_safety_mode") != CURRENT_HOST_SAFETY_MODE
        or journal.get("source_role") != source_role
        or journal.get("adoption_contract") != expected_contract
    ):
        raise AdoptionError("recovery journal header is not contract-bound")
    inventory_approval = _validated_journal_inventory_approval(
        journal.get("inventory_approval")
    )
    source_adoption_approval = (
        _validated_journal_source_adoption_approval(
            journal.get("source_adoption_approval")
        )
    )
    expected_action_binding = {
        "approval_path": str(CONTRACTS[source_role].source_adoption_approval),
        "approval_policy_sha256": CURRENT_APPROVAL_POLICY_SHA256,
        "approval_subject_sha256": _canonical_hash(
            source_adoption_approval_subject(source_role)
        ),
        "adoption_contract_sha256": ADOPTION_CONTRACT_SHA256,
    }
    if any(
        source_adoption_approval.get(field) != expected
        for field, expected in expected_action_binding.items()
    ):
        raise AdoptionError(
            "recovery source-adoption approval is not contract-bound"
        )
    return {
        "path": journal_path,
        "raw_sha256": _sha256(raw),
        "inventory_approval": inventory_approval,
        "source_adoption_approval": source_adoption_approval,
    }


def _load_recovery_tracker(
    *,
    output_dir: Path,
    preflight: dict[str, Any],
    scratch_image_id: str,
) -> tuple[TemporaryResources, dict[str, Any]]:
    journal_path = output_dir / "temporary-resource-journal.json"
    journal, journal_raw = _secure_json(journal_path)
    contract: HistoricalContract = preflight["contract"]
    run_id = str(journal.get("run_id", ""))
    if run_id != contract.run_id:
        raise AdoptionError("recovery journal run identity differs from the contract")
    tracker = TemporaryResources(role=contract.role, run_id=run_id)
    expected_contract = {
        "path": str(ADOPTION_CONTRACT_PATH),
        "sha256": ADOPTION_CONTRACT_SHA256,
    }
    expected_label = {"key": TEMP_LABEL_KEY, "value": tracker.operation_id}
    source_volumes = {
        destination: identity[0]
        for destination, identity in EXPECTED_SOURCE_VOLUMES[contract.role].items()
    }
    app_image_id = dict(contract.service_images)[contract.app_service]
    scratch_volume = f"{tracker.prefix}-pgdata"
    expected_containers = [
        {
            "name": f"{tracker.prefix}-uploads",
            "purpose": "uploads",
            "expected_image_id": app_image_id,
            "mount_volume": source_volumes["/app/uploads"],
            "mount_destination": "/source",
            "mount_readonly": True,
            "expected_entrypoint": ["tar"],
            "expected_cmd": ["-C", "/source", "-czf", "-", "."],
        },
        {
            "name": f"{tracker.prefix}-audit",
            "purpose": "audit",
            "expected_image_id": app_image_id,
            "mount_volume": source_volumes["/app/audit_trail"],
            "mount_destination": "/source",
            "mount_readonly": True,
            "expected_entrypoint": ["tar"],
            "expected_cmd": ["-C", "/source", "-czf", "-", "."],
        },
        {
            "name": f"{tracker.prefix}-restore",
            "purpose": "restore",
            "expected_image_id": scratch_image_id,
            "mount_volume": scratch_volume,
            "mount_destination": "/var/lib/postgresql/data",
            "mount_readonly": False,
            "expected_entrypoint": (
                list(SCRATCH_POSTGRES_ENTRYPOINT)
                if SCRATCH_POSTGRES_ENTRYPOINT is not None
                else None
            ),
            "expected_cmd": (
                list(SCRATCH_POSTGRES_CMD)
                if SCRATCH_POSTGRES_CMD is not None
                else None
            ),
        },
    ]
    expected_volume = {"name": scratch_volume, "driver": "local", "scope": "local"}
    journal_inventory_approval = _validated_journal_inventory_approval(
        journal.get("inventory_approval")
    )
    journal_source_adoption_approval = (
        _validated_journal_source_adoption_approval(
            journal.get("source_adoption_approval")
        )
    )
    protected = journal.get("protected_source_identities")
    boundaries = preflight["inventory_approval"]["_production_boundaries"]
    boundary_postgres = boundaries.get("postgres_system_ids")
    boundary_volumes = boundaries.get("volume_ids")
    boundary_audit = boundaries.get("audit_root_ids")
    if (
        set(journal) != RESOURCE_JOURNAL_FIELDS
        or journal.get("schema")
        != "three-site-staging-temporary-resource-journal-v3"
        or journal.get("status") != "cleanup-required-until-completion-marker"
        or journal.get("campaign_id") != CURRENT_CAMPAIGN_ID
        or journal.get("release_sha") != CURRENT_RELEASE_SHA
        or journal.get("deployment_id") != CURRENT_DEPLOYMENT_ID
        or journal.get("host_safety_mode") != CURRENT_HOST_SAFETY_MODE
        or journal.get("source_role") != contract.role
        or journal.get("resource_prefix") != tracker.prefix
        or journal.get("adoption_contract") != expected_contract
        or journal_inventory_approval
        != {
            field: preflight["inventory_approval"].get(field)
            for field in INVENTORY_APPROVAL_BINDING_FIELDS
        }
        or journal_source_adoption_approval
        != preflight["source_adoption_approval"]
        or journal.get("historical_freeze_sha256")
        != contract.freeze_evidence_sha256
        or SHA256_RE.fullmatch(
            str(journal.get("source_snapshot_sha256", ""))
        )
        is None
        or SHA256_RE.fullmatch(
            str(journal.get("source_measurement_sha256", ""))
        )
        is None
        or not isinstance(protected, dict)
        or set(protected)
        != {
            "postgres_system_id",
            "uploads_volume_id",
            "audit_volume_id",
        }
        or not isinstance(boundary_postgres, list)
        or not isinstance(boundary_volumes, list)
        or not isinstance(boundary_audit, list)
        or protected.get("postgres_system_id") not in boundary_postgres
        or protected.get("uploads_volume_id")
        != source_volumes["/app/uploads"]
        or protected.get("uploads_volume_id") not in boundary_volumes
        or protected.get("audit_volume_id")
        != source_volumes["/app/audit_trail"]
        or protected.get("audit_volume_id") not in boundary_volumes
        or protected.get("audit_volume_id") not in boundary_audit
        or scratch_volume in set(boundary_volumes) | set(boundary_audit)
        or journal.get("operation_label") != expected_label
        or journal.get("allowed_containers") != expected_containers
        or journal.get("allowed_named_volume") != expected_volume
        or journal.get("container_limit") != MAX_TEMPORARY_CONTAINERS
        or journal.get("named_volume_limit") != MAX_SCRATCH_VOLUMES
        or journal.get("intent_persisted_before_docker_side_effect") is not True
        or journal.get("automatic_cleanup_scope")
        != "exact-full-id-after-label-name-image-revalidation"
    ):
        raise AdoptionError("recovery journal identity or constraints are invalid")
    _utc(journal["created_at"], label="resource journal")
    for row in expected_containers:
        tracker.reserve_container(
            row["purpose"],
            expected_image_id=row["expected_image_id"],
            expected_mount_volume=row["mount_volume"],
            expected_mount_destination=row["mount_destination"],
            expected_mount_readonly=row["mount_readonly"],
            expected_entrypoint=row["expected_entrypoint"],
            expected_cmd=row["expected_cmd"],
            recovery=True,
        )
    if tracker.reserve_volume(recovery=True) != scratch_volume:
        raise AdoptionError("recovery scratch volume identity is inconsistent")
    return tracker, {
        "path": journal_path,
        "raw_sha256": _sha256(journal_raw),
        "payload": journal,
    }


def recovery_confirmation_phrase(tracker: TemporaryResources) -> str:
    return (
        f"recover-frozen-backup:{CURRENT_CAMPAIGN_ID}:{tracker.role}:"
        f"{tracker.operation_id}:{CURRENT_RELEASE_SHA}"
    )


def _write_cleanup_completion(
    *,
    output_dir: Path,
    tracker: TemporaryResources,
    journal_sha256: str,
    status: str,
) -> dict[str, Any]:
    if status not in {"completed", "cleanup-after-failure-verified"}:
        raise AdoptionError("cleanup completion status is invalid")
    payload = {
        "schema": "three-site-staging-temporary-cleanup-completion-v2",
        "status": status,
        "campaign_id": CURRENT_CAMPAIGN_ID,
        "release_sha": CURRENT_RELEASE_SHA,
        "deployment_id": CURRENT_DEPLOYMENT_ID,
        "host_safety_mode": CURRENT_HOST_SAFETY_MODE,
        "source_role": tracker.role,
        "operation_id": tracker.operation_id,
        "adoption_contract_sha256": ADOPTION_CONTRACT_SHA256,
        "journal_sha256": journal_sha256,
        "created_containers": [
            {"name": name, "full_id": container_id}
            for name, container_id in sorted(tracker.created_container_ids.items())
        ],
        "created_named_volume": (
            f"{tracker.prefix}-pgdata"
            if any(name.endswith("-restore") for name in tracker.created_container_ids)
            else None
        ),
        "zero_residue_verified": True,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    raw = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    path = output_dir / "temporary-resource-cleanup-complete.json"
    if path.exists() or path.is_symlink():
        existing = _secure_bytes(path, maximum=1024 * 1024)
        if existing != raw:
            # Timestamps make independently generated bytes differ.  Existing
            # completion is accepted only after strict structural/hash binding.
            existing_payload, _ = _secure_json(path)
            if (
                set(existing_payload) != set(payload)
                or existing_payload.get("schema") != payload["schema"]
                or existing_payload.get("status") not in {
                    "completed",
                    "cleanup-after-failure-verified",
                }
                or existing_payload.get("campaign_id") != CURRENT_CAMPAIGN_ID
                or existing_payload.get("release_sha") != CURRENT_RELEASE_SHA
                or existing_payload.get("deployment_id") != CURRENT_DEPLOYMENT_ID
                or existing_payload.get("host_safety_mode")
                != CURRENT_HOST_SAFETY_MODE
                or existing_payload.get("source_role") != tracker.role
                or existing_payload.get("operation_id") != tracker.operation_id
                or existing_payload.get("adoption_contract_sha256")
                != ADOPTION_CONTRACT_SHA256
                or existing_payload.get("journal_sha256") != journal_sha256
                or existing_payload.get("zero_residue_verified") is not True
            ):
                raise AdoptionError("existing cleanup completion marker is invalid")
            return {
                "path": path,
                "raw_sha256": _sha256(existing),
                "payload": existing_payload,
            }
    else:
        _exclusive_write(path, raw)
    return {"path": path, "raw_sha256": _sha256(raw), "payload": payload}


def _execute_locked(
    args: argparse.Namespace, preflight: dict[str, Any]
) -> dict[str, Any]:
    output_dir = _prepare_output_directory(args.output_dir)
    tracker: TemporaryResources = preflight["tracker"]
    baseline_snapshot = preflight["snapshot"]
    primary_error: BaseException | None = None
    pending_attestation: dict[str, Any] | None = None
    resource_journal: dict[str, Any] | None = None
    cleanup_completion: dict[str, Any] | None = None
    try:
        resource_journal = _write_resource_journal(
            output_dir=output_dir,
            tracker=tracker,
            preflight=preflight,
            scratch_image_id=args.scratch_postgres_image_id,
        )
        freeze_outputs = _write_rollback_and_freeze(
            output_dir=output_dir,
            preflight=preflight,
        )
        backup_outputs = _create_source_backups(
            args=args,
            output_dir=output_dir,
            preflight=preflight,
            freeze_outputs=freeze_outputs,
            authorize_effect=lambda: _authorize_next_apply_effect(
                args, preflight
            ),
        )
        post_snapshot = _project_snapshot(
            preflight["contract"], preflight["historical"]["service_images"]
        )
        if post_snapshot != baseline_snapshot:
            raise AdoptionError("legacy project container state changed during backup")
        post_measurement = _measure_source(snapshot=post_snapshot, env=preflight["env"])
        if post_measurement["postgres"] != preflight["measurement"]["postgres"]:
            raise AdoptionError("legacy PostgreSQL content changed during backup")
        before_redis = preflight["measurement"]["redis_observation"]
        after_redis = post_measurement["redis_observation"]
        if (
            after_redis["appendonly"] is not True
            or after_redis["restore"] is not False
            or after_redis["dbsize"] > before_redis["dbsize"]
            or after_redis["lastsave_unix"] < before_redis["lastsave_unix"]
        ):
            raise AdoptionError("legacy Redis changed incompatibly during backup")
        tracker.assert_zero_residue()
        cleanup_completion = _write_cleanup_completion(
            output_dir=output_dir,
            tracker=tracker,
            journal_sha256=resource_journal["raw_sha256"],
            status="completed",
        )

        artifacts = backup_outputs["manifest"]["artifacts"]
        attestation = {
            "schema": "three-site-staging-adopted-frozen-source-backup-attestation-v1",
            "status": "backup-and-restore-verified",
            "campaign_id": CURRENT_CAMPAIGN_ID,
            "release_sha": CURRENT_RELEASE_SHA,
            "deployment_id": CURRENT_DEPLOYMENT_ID,
            "host_safety_mode": CURRENT_HOST_SAFETY_MODE,
            "source_role": preflight["contract"].role,
            "project_name": preflight["contract"].project,
            "source_release_sha": SOURCE_RELEASE_SHA,
            "adoption_contract": {
                "path": str(ADOPTION_CONTRACT_PATH),
                "sha256": ADOPTION_CONTRACT_SHA256,
            },
            "inventory_approval": {
                "approval_id": preflight["inventory_approval"]["approval_id"],
                "approval_token_sha256": preflight["inventory_approval"][
                    "approval_token_sha256"
                ],
                "approval_expires_at": preflight["inventory_approval"][
                    "approval_expires_at"
                ],
                "inventory_sha256": preflight["inventory_approval"][
                    "inventory_sha256"
                ],
            },
            "source_adoption_approval": {
                field: preflight["source_adoption_approval"][field]
                for field in sorted(SOURCE_ADOPTION_APPROVAL_BINDING_FIELDS)
            },
            "historical_restore_to_freeze_chain": {
                "campaign_id": HISTORICAL_CAMPAIGN_ID,
                "target_release_sha": HISTORICAL_TARGET_RELEASE_SHA,
                "adopted_freeze_canonical_sha256": preflight["historical"][
                    "adopted_restore_chain"
                ]["canonical_sha256"],
                "adopted_restore_bundle_sha256": preflight["historical"][
                    "adopted_restore_chain"
                ]["bundle_sha256"],
                "restore_evidence_sha256": preflight["historical"][
                    "restore_raw_sha256"
                ],
                "freeze_evidence_sha256": preflight["historical"]["freeze_raw_sha256"],
                "restore_bundle_sha256": preflight["historical"]["bundle_raw_sha256"],
                "compose_sha256": preflight["historical"]["compose_sha256"],
                "chronology_verified": True,
            },
            "fresh_source_observation": {
                "postgres": preflight["measurement"]["postgres"],
                "redis_before": before_redis,
                "redis_after": after_redis,
                "running_services": ["db", "redis"],
                "application_status": "exited",
                "application_stop_chronology": preflight[
                    "app_stop_chronology"
                ],
                "protected_inventory_identity_binding": True,
            },
            "outputs": {
                "historical_compose_copy": {
                    "name": freeze_outputs["historical_compose_copy"].name,
                    "sha256": preflight["historical"]["compose_sha256"],
                },
                "pinned_compose": {
                    "name": freeze_outputs["pinned_compose"].name,
                    "sha256": freeze_outputs["pinned_compose_sha256"],
                },
                "rollback_manifest": {
                    "name": freeze_outputs["rollback_path"].name,
                    "sha256": freeze_outputs["rollback_sha256"],
                },
                "freeze_evidence": {
                    "name": freeze_outputs["freeze_path"].name,
                    "canonical_sha256": freeze_outputs["freeze_sha256"],
                },
                "backup_manifest": {
                    "name": backup_outputs["manifest_path"].name,
                    "canonical_sha256": backup_outputs["manifest_sha256"],
                },
                "backup_artifact_sha256": {
                    kind: value["sha256"] for kind, value in sorted(artifacts.items())
                },
                "temporary_resource_journal": {
                    "name": resource_journal["path"].name,
                    "sha256": resource_journal["raw_sha256"],
                },
                "temporary_cleanup_completion": {
                    "name": cleanup_completion["path"].name,
                    "sha256": cleanup_completion["raw_sha256"],
                },
            },
            "restore_drill": backup_outputs["restore"],
            "temporary_resources": {
                "container_count": len(tracker.created_containers),
                "maximum_containers": 3,
                "named_volume_count": 1,
                "zero_residue_verified": True,
            },
            "safety": {
                "source_container_state_unchanged": True,
                "source_postgres_content_unchanged": True,
                "application_started_or_stopped": False,
                "compose_service_recreated": False,
                "image_pull": False,
                "image_build": False,
                "redis_restore": False,
                "temporary_network_mode": "none",
                "resource_intent_persisted_before_docker_side_effect": True,
                "output_overwrite": False,
            },
        }
        pending_attestation = attestation
    except BaseException as exc:
        primary_error = exc
    finally:
        final_errors: list[BaseException] = []
        try:
            tracker.cleanup()
            tracker.assert_zero_residue()
        except BaseException as exc:
            final_errors.append(exc)
        if (
            not final_errors
            and resource_journal is not None
            and cleanup_completion is None
        ):
            try:
                cleanup_completion = _write_cleanup_completion(
                    output_dir=output_dir,
                    tracker=tracker,
                    journal_sha256=resource_journal["raw_sha256"],
                    status="cleanup-after-failure-verified",
                )
            except BaseException as exc:
                final_errors.append(exc)
        try:
            final_snapshot = _project_snapshot(
                preflight["contract"], preflight["historical"]["service_images"]
            )
            if final_snapshot != baseline_snapshot:
                final_errors.append(
                    AdoptionError("legacy project state differs after final cleanup")
                )
        except BaseException as exc:
            final_errors.append(exc)
        if final_errors:
            try:
                _write_cleanup_signal(
                    output_dir=output_dir,
                    tracker=tracker,
                    reasons=final_errors,
                )
            except Exception as signal_error:
                final_errors.append(signal_error)
            raise AdoptionError("final cleanup/state proof failed") from final_errors[0]
    if primary_error is not None:
        raise primary_error
    if pending_attestation is None:
        raise AdoptionError("apply completed without a final attestation")
    attestation_raw = (
        json.dumps(pending_attestation, sort_keys=True, indent=2) + "\n"
    ).encode()
    attestation_path = output_dir / "attestation.json"
    _exclusive_write(attestation_path, attestation_raw)
    return {
        **pending_attestation,
        "attestation_sha256": _canonical_hash(pending_attestation),
        "attestation_name": attestation_path.name,
    }


def _refresh_authority(
    args: argparse.Namespace, preflight: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    _verify_adoption_contract_unchanged()
    _verify_exact_release()
    refreshed_inventory = _verify_current_approval(
        args, require_fresh=False
    )
    if {
        field: refreshed_inventory.get(field)
        for field in INVENTORY_APPROVAL_BINDING_FIELDS
    } != {
        field: preflight["inventory_approval"].get(field)
        for field in INVENTORY_APPROVAL_BINDING_FIELDS
    }:
        raise AdoptionError(
            "inventory provenance approval changed between plan and apply"
        )
    refreshed_action = _verify_source_adoption_approval(
        args, require_fresh=True
    )
    if refreshed_action != preflight["source_adoption_approval"]:
        raise AdoptionError(
            "source-adoption approval changed between plan and apply"
        )
    remaining = (
        _utc(
            refreshed_action["approval_expires_at"],
            label="refreshed source-adoption approval",
        )
        - datetime.now(timezone.utc)
    ).total_seconds()
    if remaining < MIN_APPROVAL_REMAINING_SECONDS:
        raise AdoptionError(
            "refreshed source-adoption approval has less than 20 minutes remaining"
        )
    return refreshed_inventory, refreshed_action


def _refresh_recovery_authority(
    args: argparse.Namespace, preflight: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    _verify_adoption_contract_unchanged()
    _verify_exact_release()
    refreshed_inventory = _verify_current_approval(
        args, require_fresh=False
    )
    refreshed_action = _verify_source_adoption_approval(
        args, require_fresh=False
    )
    journal_inventory = preflight["recovery_journal"]["payload"][
        "inventory_approval"
    ]
    if {
        field: refreshed_inventory.get(field)
        for field in INVENTORY_APPROVAL_BINDING_FIELDS
    } != journal_inventory:
        raise AdoptionError(
            "historical recovery inventory approval differs from the journal"
        )
    journal_action = preflight["recovery_journal"]["payload"][
        "source_adoption_approval"
    ]
    if refreshed_action != journal_action:
        raise AdoptionError(
            "historical recovery source-adoption approval differs from the journal"
        )
    return refreshed_inventory, refreshed_action


def _refresh_action_before_first_effect(
    args: argparse.Namespace, preflight: dict[str, Any]
) -> dict[str, Any]:
    _verify_adoption_contract_unchanged()
    _verify_exact_release()
    refreshed = _verify_source_adoption_approval(
        args, require_fresh=True
    )
    if refreshed != preflight["source_adoption_approval"]:
        raise AdoptionError(
            "source-adoption approval changed before the first effect"
        )
    remaining = (
        _utc(
            refreshed["approval_expires_at"],
            label="source-adoption approval before first effect",
        )
        - datetime.now(timezone.utc)
    ).total_seconds()
    if remaining < MIN_APPROVAL_REMAINING_SECONDS:
        raise AdoptionError(
            "source-adoption approval has less than 20 minutes remaining "
            "before the first effect"
        )
    _secure_env(
        preflight["contract"].env_file,
        expected_sha256=preflight["contract"].env_sha256,
    )
    _verify_scratch_image_identity(args.scratch_postgres_image_id)
    return refreshed


def _authorize_next_apply_effect(
    args: argparse.Namespace, preflight: dict[str, Any]
) -> None:
    _verify_adoption_contract_unchanged()
    _verify_exact_release()
    refreshed = _verify_source_adoption_approval(
        args, require_fresh=True
    )
    if refreshed != preflight["source_adoption_approval"]:
        raise AdoptionError(
            "source-adoption approval changed before a later effect"
        )
    refreshed_env = _secure_env(
        preflight["contract"].env_file,
        expected_sha256=preflight["contract"].env_sha256,
    )
    if refreshed_env != preflight["env"]:
        raise AdoptionError(
            "legacy environment semantics changed before a later effect"
        )
    _verify_scratch_image_identity(args.scratch_postgres_image_id)


def _revalidate_apply_state(
    args: argparse.Namespace, preflight: dict[str, Any]
) -> None:
    _verify_exact_release()
    for service, image_id in sorted(
        preflight["historical"]["service_images"].items()
    ):
        _verify_local_image(image_id, label=f"refreshed historical {service}")
    _verify_scratch_image_identity(args.scratch_postgres_image_id)
    snapshot = _project_snapshot(
        preflight["contract"], preflight["historical"]["service_images"]
    )
    if snapshot != preflight["snapshot"]:
        raise AdoptionError("legacy project state changed before first mutation")
    refreshed_env = _secure_env(
        args.env_file,
        expected_sha256=preflight["contract"].env_sha256,
    )
    if refreshed_env != preflight["env"]:
        raise AdoptionError(
            "legacy environment semantics changed before first effect"
        )
    measurement = _measure_source(snapshot=snapshot, env=refreshed_env)
    if measurement != preflight["measurement"]:
        raise AdoptionError("legacy PostgreSQL/Redis changed before first mutation")
    _validate_source_against_official_freeze(measurement, preflight["historical"])


def _execute(args: argparse.Namespace, preflight: dict[str, Any]) -> dict[str, Any]:
    if args.confirm != preflight["required_confirmation"]:
        raise AdoptionError("apply confirmation phrase is missing or stale")
    refreshed_inventory, refreshed_action = _refresh_authority(args, preflight)
    preflight = {
        **preflight,
        "inventory_approval": refreshed_inventory,
        "source_adoption_approval": refreshed_action,
    }
    with RoleApplyLock(args.source_role):
        _verify_adoption_contract_unchanged()
        _revalidate_apply_state(args, preflight)
        preflight = {
            **preflight,
            "source_adoption_approval": (
                _refresh_action_before_first_effect(args, preflight)
            ),
        }
        return _execute_locked(args, preflight)


def _recover_locked(
    args: argparse.Namespace, preflight: dict[str, Any]
) -> dict[str, Any]:
    tracker: TemporaryResources = preflight["tracker"]
    journal = preflight["recovery_journal"]
    errors: list[BaseException] = []
    try:
        tracker.cleanup()
        tracker.assert_zero_residue()
    except BaseException as exc:
        errors.append(exc)
    if errors:
        try:
            _write_cleanup_signal(
                output_dir=args.output_dir,
                tracker=tracker,
                reasons=errors,
            )
        except BaseException as exc:
            errors.append(exc)
        raise AdoptionError("SIGKILL recovery failed closed") from errors[0]
    completion = _write_cleanup_completion(
        output_dir=args.output_dir,
        tracker=tracker,
        journal_sha256=journal["raw_sha256"],
        status="cleanup-after-failure-verified",
    )
    return {
        "schema": "three-site-staging-frozen-source-backup-recovery-v1",
        "status": "recovered-zero-residue-restart-with-new-output",
        "campaign_id": CURRENT_CAMPAIGN_ID,
        "release_sha": CURRENT_RELEASE_SHA,
        "deployment_id": CURRENT_DEPLOYMENT_ID,
        "host_safety_mode": CURRENT_HOST_SAFETY_MODE,
        "source_role": args.source_role,
        "operation_id": tracker.operation_id,
        "journal_sha256": journal["raw_sha256"],
        "cleanup_completion_sha256": completion["raw_sha256"],
        "source_state_revalidation_required_before_restart": True,
        "zero_residue_verified": True,
    }


def _recover(args: argparse.Namespace, preflight: dict[str, Any]) -> dict[str, Any]:
    if args.confirm != preflight["required_confirmation"]:
        raise AdoptionError(
            "recovery confirmation phrase is missing or stale: "
            f"{preflight['required_confirmation']}"
        )
    refreshed_inventory, refreshed_action = _refresh_recovery_authority(
        args, preflight
    )
    preflight = {
        **preflight,
        "inventory_approval": refreshed_inventory,
        "source_adoption_approval": refreshed_action,
    }
    with RoleApplyLock(args.source_role):
        _verify_adoption_contract_unchanged()
        _verify_exact_release()
        return _recover_locked(args, preflight)


def _plan_result(preflight: dict[str, Any]) -> dict[str, Any]:
    contract: HistoricalContract = preflight["contract"]
    return {
        "status": "planned-read-only",
        "campaign_id": CURRENT_CAMPAIGN_ID,
        "release_sha": CURRENT_RELEASE_SHA,
        "host_safety_mode": CURRENT_HOST_SAFETY_MODE,
        "source_role": contract.role,
        "project_name": contract.project,
        "source_release_sha": SOURCE_RELEASE_SHA,
        "adoption_contract": {
            "path": str(ADOPTION_CONTRACT_PATH),
            "sha256": ADOPTION_CONTRACT_SHA256,
        },
        "historical_chain": {
            "campaign_id": HISTORICAL_CAMPAIGN_ID,
            "target_release_sha": HISTORICAL_TARGET_RELEASE_SHA,
            "restore_evidence_sha256": preflight["historical"]["restore_raw_sha256"],
            "freeze_evidence_sha256": preflight["historical"]["freeze_raw_sha256"],
            "restore_bundle_sha256": preflight["historical"]["bundle_raw_sha256"],
            "compose_sha256": preflight["historical"]["compose_sha256"],
        },
        "fresh_postgres": preflight["measurement"]["postgres"],
        "fresh_redis": preflight["measurement"]["redis_observation"],
        "running_services": ["db", "redis"],
        "application_status": "exited",
        "outputs_create_exclusively": [
            "historical Compose copy",
            "image-ID-pinned Compose",
            "provenance-bound rollback manifest",
            "fresh freeze evidence",
            "PostgreSQL custom dump",
            "uploads archive",
            "audit archive",
            "backup manifest",
            "nonsecret attestation",
        ],
        "temporary_resource_budget": {
            "containers": 3,
            "named_volumes": 1,
            "network": "none",
            "zero_residue_required": True,
        },
        "image_pull": False,
        "image_build": False,
        "redis_restore": False,
        "required_confirmation": preflight["required_confirmation"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adoption-contract", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=("approval-subject", "plan", "apply", "recover"),
        default="plan",
    )
    parser.add_argument("--source-role", choices=sorted(CONTRACTS), required=True)
    parser.add_argument(
        "--inventory",
        type=Path,
        default=CURRENT_INVENTORY_PATH,
    )
    parser.add_argument(
        "--inventory-approval",
        type=Path,
    )
    parser.add_argument("--source-adoption-approval", type=Path)
    parser.add_argument("--approval-subject-output", type=Path)
    parser.add_argument(
        "--approval-policy",
        type=Path,
        default=CURRENT_APPROVAL_POLICY_PATH,
    )
    parser.add_argument("--historical-restore-evidence", type=Path)
    parser.add_argument(
        "--historical-adopted-freeze-evidence", type=Path
    )
    parser.add_argument("--historical-freeze-evidence", type=Path)
    parser.add_argument("--historical-restore-bundle", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--scratch-postgres-image-id")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--confirm")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        contract_parser = argparse.ArgumentParser(add_help=False)
        contract_parser.add_argument("--adoption-contract", type=Path, required=True)
        contract_args, _remaining = contract_parser.parse_known_args(argv)
        _load_adoption_contract(contract_args.adoption_contract)
        args = _parser().parse_args(argv)
        if args.adoption_contract != ADOPTION_CONTRACT_PATH:
            raise AdoptionError("adoption contract path changed during argument parsing")
        role_contract = CONTRACTS[args.source_role]
        defaults = {
            "inventory_approval": role_contract.inventory_approval,
            "source_adoption_approval": (
                role_contract.source_adoption_approval
            ),
            "approval_subject_output": role_contract.source_adoption_subject,
            "historical_restore_evidence": role_contract.restore_evidence_path,
            "historical_adopted_freeze_evidence": role_contract.adopted_freeze_path,
            "historical_freeze_evidence": role_contract.freeze_evidence_path,
            "historical_restore_bundle": role_contract.restore_bundle_path,
            "env_file": role_contract.env_file,
            "scratch_postgres_image_id": SCRATCH_POSTGRES_IMAGE_ID,
            "output_dir": role_contract.output_dir,
        }
        for name, value in defaults.items():
            if getattr(args, name) is None:
                setattr(args, name, value)
        if args.mode == "approval-subject":
            result = _emit_approval_subject(args)
        else:
            preflight = _preflight(args, recovery=args.mode == "recover")
        if args.mode == "apply":
            with ApplySignalGuard():
                result = _execute(args, preflight)
        elif args.mode == "recover":
            with ApplySignalGuard():
                result = _recover(args, preflight)
        elif args.mode == "plan":
            result = _plan_result(preflight)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc)[:300],
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
