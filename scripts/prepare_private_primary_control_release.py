#!/usr/bin/env python3
"""Independent PRIVATE_PRIMARY control-release preparation.

This tool installs immutable artifacts and host foundation only.  It never
starts or stops services, mutates a live database, changes Queue or capture
ownership, or writes Product authority.  Historical PRIVATE_SHADOW evidence
and preflight flags must stay off.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
from typing import Any, Mapping, Sequence

if __package__:
    from scripts.inventory_private_primary_active_runtime import (
        ALLOWED_ADOPTED_DATA_ROOTS,
        COMBINED_SCHEMA,
        INVENTORY_SCHEMA,
        validate_inventory,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.inventory_private_primary_active_runtime import (
        ALLOWED_ADOPTED_DATA_ROOTS,
        COMBINED_SCHEMA,
        INVENTORY_SCHEMA,
        validate_inventory,
    )


CONFIRMATION = "prepare-production-private-primary-control-release"
INSTALL_SCHEMA = "private_primary_control_release_install/1.0"
FOUNDATION_SCHEMA = "private_primary_foundation/1.0"
PREPARE_SCHEMA = "private_primary_control_release_prepare/1.0"
KEY_CONFIRMATION = "generate-production-market-pipeline-backup-key"
SELECT_IMAGE_CONFIRMATION = "select-market-pipeline-host-image"
MAXIMUM_RECEIPT_AGE_SECONDS = 3600
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
HISTORICAL_FLAGS = (
    "PRODUCTION_MARKET_PIPELINE_RELEASE_EVIDENCE_ENABLED",
    "PRODUCTION_MARKET_PIPELINE_HOST_PREFLIGHT_ENABLED",
    "PRODUCTION_MARKET_PIPELINE_MIGRATION_ENABLED",
    "PRODUCTION_MARKET_PIPELINE_SHADOW_ROLLOUT_ENABLED",
    "PRODUCTION_MARKET_PIPELINE_CAPTURE_CUTOVER_ENABLED",
)
CANONICAL_BOT_DATA_ROOT = "/srv/trading-bot/production-data/market-pipeline"
CANONICAL_WEB_DATA_ROOT = "/srv/trading-bot/market-data-production"
CANONICAL_WEB_BACKUP_ROOT = "/srv/trading-bot/market-data-production/backups"
CANONICAL_OFFHOST_ROOT = (
    "/root/secure-envs/trading-bot/release-control/offhost-backups"
)
CANONICAL_BACKUP_KEY = "/root/secure-envs/trading-bot/market-pipeline-backup.key"
REQUIRED_INSTALL_NAMES = (
    "control-payload.sha256",
    "bot.release.env",
    "web.release.env",
    "market-pipeline-image-prebuild-receipt.json",
    "market-pipeline-release-pair-receipt.json",
)
FORBIDDEN_PATH_MARKERS = ("staging", "/tmp/", "/var/tmp/")


class PrepareError(RuntimeError):
    """Stable, secret-free refusal."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _digest_bytes(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _digest_path(path: Path) -> str:
    return _digest_bytes(path.read_bytes())


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    _secure_parent(path, create=True)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise PrepareError("receipt_output_invalid")
    candidate = path.parent / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(candidate, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(candidate, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        candidate.unlink(missing_ok=True)


def _secure_parent(path: Path, *, create: bool) -> None:
    parent = path.parent
    if not parent.is_absolute() or parent in {
        Path("/"),
        Path("/root"),
        Path("/srv"),
        Path("/tmp"),
        Path("/var/tmp"),
    }:
        raise PrepareError("parent_scope_invalid")
    if create:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(parent, 0o700)
    try:
        info = parent.lstat()
    except OSError as exc:
        raise PrepareError("parent_unavailable") from exc
    if (
        parent.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise PrepareError("parent_owner_mode_invalid")


def validate_lexical_path(
    raw: str,
    *,
    label: str,
    repository_root: Path,
    allow_exact: set[str] | frozenset[str] | None = None,
) -> Path:
    if not raw or not raw.startswith("/") or ".." in Path(raw).parts:
        raise PrepareError(f"{label}_path_invalid")
    lowered = raw.lower()
    allowed = set(allow_exact or ())
    if any(marker in lowered for marker in FORBIDDEN_PATH_MARKERS):
        staging_exception = (
            raw in allowed
            and "staging" in lowered
            and "/tmp/" not in lowered
            and "/var/tmp/" not in lowered
        )
        if not staging_exception:
            raise PrepareError(f"{label}_path_forbidden")
    path = Path(raw)
    try:
        resolved = path.resolve(strict=False)
    except OSError as exc:
        raise PrepareError(f"{label}_path_invalid") from exc
    if str(resolved) != raw:
        raise PrepareError(f"{label}_path_not_canonical")
    repo = repository_root.resolve(strict=False)
    if raw == str(repo) or raw.startswith(str(repo) + "/"):
        raise PrepareError(f"{label}_path_inside_repository")
    return path


def validate_historical_flags(values: Mapping[str, str]) -> None:
    for key in HISTORICAL_FLAGS:
        if str(values.get(key, "0") or "0") != "0":
            raise PrepareError("historical_private_shadow_flag_enabled")


def load_continuity_receipt(path: Path, *, role: str) -> dict[str, Any]:
    validate_lexical_path(str(path), label="continuity_receipt", repository_root=Path("/nonexistent-repo"))
    try:
        info = path.lstat()
    except OSError as exc:
        raise PrepareError("continuity_receipt_unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise PrepareError("continuity_receipt_invalid")
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") == COMBINED_SCHEMA:
        host = (document.get("hosts") or {}).get(role)
        if not isinstance(host, Mapping):
            raise PrepareError("continuity_host_missing")
        adopted = str(host.get("adopted_data_root") or "")
        if adopted != ALLOWED_ADOPTED_DATA_ROOTS[role]:
            raise PrepareError("continuity_data_root_unapproved")
        if document.get("decision") != "adopt_live_roots" or document.get("status") != "PASS":
            raise PrepareError("continuity_decision_invalid")
        return {
            "adopted_data_root": adopted,
            "adopted_snapshot_root": str(host.get("adopted_snapshot_root") or f"{adopted}/snapshots"),
            "container_ids": list(host.get("container_ids") or []),
            "mount_identity_sha256": str(host.get("mount_identity_sha256") or ""),
            "project_name": str(document.get("project_name") or ""),
        }
    if document.get("schema") == INVENTORY_SCHEMA:
        validated = validate_inventory(document, role=role)
        return {
            "adopted_data_root": validated["adopted_data_root"],
            "adopted_snapshot_root": validated["adopted_snapshot_root"],
            "container_ids": list(validated["container_ids"]),
            "mount_identity_sha256": validated["mount_identity_sha256"],
            "project_name": validated["project_name"],
        }
    raise PrepareError("continuity_schema_invalid")


def validate_topology_source(
    path: Path,
    *,
    role: str,
    repository_root: Path,
    continuity_receipt: Path | None = None,
) -> dict[str, str]:
    validate_lexical_path(str(path), label=f"{role}_source", repository_root=repository_root)
    try:
        info = path.lstat()
    except OSError as exc:
        raise PrepareError(f"{role}_source_unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) & 0o022
        or info.st_uid not in {0, os.geteuid()}
    ):
        raise PrepareError(f"{role}_source_security_invalid")
    text = path.read_text(encoding="utf-8")
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    root_key = "MARKET_WEB_DATA_ROOT" if role == "web" else "MARKET_BOT_DATA_ROOT"
    allowed_exact: set[str] = set()
    if continuity_receipt is not None:
        adopted = load_continuity_receipt(continuity_receipt, role=role)
        expected_root = adopted["adopted_data_root"]
        expected_snapshot = adopted["adopted_snapshot_root"]
        allowed_exact = {expected_root, expected_snapshot}
        if not adopted.get("container_ids") or not adopted.get("mount_identity_sha256"):
            raise PrepareError("continuity_identity_incomplete")
    else:
        expected_root = CANONICAL_WEB_DATA_ROOT if role == "web" else CANONICAL_BOT_DATA_ROOT
        expected_snapshot = f"{expected_root}/snapshots"
    if values.get(root_key) != expected_root:
        raise PrepareError(f"{role}_data_root_mismatch")
    snapshot = values.get("MARKET_PRODUCT_SNAPSHOT_ROOT", "")
    if snapshot != expected_snapshot:
        raise PrepareError(f"{role}_snapshot_root_mismatch")
    for key, value in values.items():
        if value.startswith("/tmp/") or "/var/tmp/" in value:
            raise PrepareError(f"{role}_source_staging_or_tmp")
        if "staging" in value.lower() and value not in allowed_exact:
            raise PrepareError(f"{role}_source_staging_or_tmp")
        if key.endswith("_FILE") and "staging" in value.lower():
            raise PrepareError(f"{role}_secret_path_not_canonical")
        if key.endswith(("_TOKEN", "_PASSWORD", "_SECRET")) and value:
            raise PrepareError(f"{role}_source_plaintext_secret")
    return values


def generate_or_reuse_backup_key(path: Path) -> dict[str, Any]:
    validate_lexical_path(str(path), label="backup_key", repository_root=Path("/nonexistent-repo"))
    _secure_parent(path, create=True)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise PrepareError("backup_key_invalid_existing")
        try:
            text = path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise PrepareError("backup_key_invalid_existing") from exc
        if not HEX64.fullmatch(text):
            raise PrepareError("backup_key_invalid_existing")
        return {
            "path": str(path),
            "reused": True,
            "created": False,
            "mode": "0600",
            "secrets_disclosed": False,
        }
    payload = os.urandom(32).hex() + "\n"
    candidate = path.parent / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(candidate, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(candidate, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        candidate.unlink(missing_ok=True)
    os.chmod(path, 0o600)
    return {
        "path": str(path),
        "reused": False,
        "created": True,
        "mode": "0600",
        "secrets_disclosed": False,
    }


def prepare_directory(
    path: Path,
    *,
    label: str,
    allow_exact: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    validate_lexical_path(
        str(path),
        label=label,
        repository_root=Path("/nonexistent-repo"),
        allow_exact=allow_exact,
    )
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise PrepareError(f"{label}_exists_not_directory")
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise PrepareError(f"{label}_owner_mode_invalid")
        return {"path": str(path), "created": False, "mode": "0700"}
    path.mkdir(mode=0o700, parents=True)
    os.chmod(path, 0o700)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return {"path": str(path), "created": True, "mode": "0700"}


def prepare_foundation(
    *,
    bot_data_root: Path,
    web_data_root: Path,
    web_backup_root: Path,
    offhost_root: Path,
    backup_key: Path,
    receipt: Path,
    release_sha: str,
    release_tree: str,
    continuity_receipt: Path | None = None,
) -> dict[str, Any]:
    if not HEX40.fullmatch(release_sha) or not HEX40.fullmatch(release_tree):
        raise PrepareError("release_identity_invalid")
    allow_exact: set[str] = set()
    adopted = False
    if continuity_receipt is not None:
        bot = load_continuity_receipt(continuity_receipt, role="bot")
        web = load_continuity_receipt(continuity_receipt, role="web")
        if str(bot_data_root) != bot["adopted_data_root"]:
            raise PrepareError("foundation_bot_root_not_adopted")
        if str(web_data_root) != web["adopted_data_root"]:
            raise PrepareError("foundation_web_root_not_adopted")
        allow_exact = {
            bot["adopted_data_root"],
            web["adopted_data_root"],
            bot["adopted_snapshot_root"],
            web["adopted_snapshot_root"],
        }
        adopted = True
    directories = {
        "bot_data_root": prepare_directory(
            bot_data_root, label="bot_data_root", allow_exact=allow_exact
        ),
        "web_data_root": prepare_directory(
            web_data_root, label="web_data_root", allow_exact=allow_exact
        ),
        "web_backup_root": prepare_directory(web_backup_root, label="web_backup_root"),
        "offhost_root": prepare_directory(offhost_root, label="offhost_root"),
    }
    key = generate_or_reuse_backup_key(backup_key)
    payload = {
        "schema": FOUNDATION_SCHEMA,
        "environment": "production",
        "release_sha": release_sha,
        "release_tree": release_tree,
        "directories": {
            name: {"path": row["path"], "created": row["created"], "mode": row["mode"]}
            for name, row in directories.items()
        },
        "backup_key": {
            "path": key["path"],
            "reused": key["reused"],
            "created": key["created"],
            "mode": key["mode"],
        },
        "adopted_live_roots": adopted,
        "state_copied": False,
        "relocation_required": False,
        "services_started": False,
        "database_mutated": False,
        "authority_changed": False,
        "capture_owner_changed": False,
        "secrets_disclosed": False,
        "created_at": _now(),
    }
    _atomic_json(receipt, payload)
    return payload


def assert_fresh_receipt(path: Path, *, now: datetime | None = None) -> None:
    _regular_file(path, label="receipt")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PrepareError("receipt_invalid") from exc
    created = str(document.get("created_at") or "")
    try:
        stamp = datetime.strptime(created, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise PrepareError("receipt_timestamp_invalid") from exc
    age = ((now or datetime.now(timezone.utc)) - stamp).total_seconds()
    if age < 0 or age > MAXIMUM_RECEIPT_AGE_SECONDS:
        raise PrepareError("receipt_stale")


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PrepareError(f"{label}_invalid") from exc
    if not isinstance(document, dict):
        raise PrepareError(f"{label}_invalid")
    return document


def _pair_host_image_id(document: Mapping[str, Any], *, host_role: str) -> str | None:
    if "image_ids" in document:
        values = document.get("image_ids")
        if not isinstance(values, Mapping) or host_role not in values:
            raise PrepareError("image_mismatch")
        return str(values[host_role])
    value = document.get("image_id")
    return None if value is None else str(value)


def _load_inspect_documents(raw: bytes) -> list[Mapping[str, Any]]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrepareError("host_image_inspect_invalid") from exc
    if isinstance(payload, Mapping):
        return [payload]
    if not isinstance(payload, list) or not payload or not all(
        isinstance(item, Mapping) for item in payload
    ):
        raise PrepareError("host_image_inspect_invalid")
    return payload


def select_host_image_id(
    documents: Sequence[Mapping[str, Any]],
    *,
    release_sha: str,
    release_tree: str,
    input_signature: str,
) -> str:
    if not HEX40.fullmatch(release_sha) or not HEX40.fullmatch(release_tree):
        raise PrepareError("release_identity_invalid")
    if not HEX64.fullmatch(input_signature):
        raise PrepareError("image_identity_invalid")
    matches: list[str] = []
    for document in documents:
        config = document.get("Config") if isinstance(document, Mapping) else None
        labels = config.get("Labels") if isinstance(config, Mapping) else None
        if not isinstance(config, Mapping) or not isinstance(labels, Mapping):
            continue
        image_id = str(document.get("Id") or "")
        platform = f"{document.get('Os')}/{document.get('Architecture')}"
        if (
            platform == "linux/amd64"
            and str(config.get("User") or "") == "10001:10001"
            and str(labels.get("org.opencontainers.image.revision") or "") == release_sha
            and str(labels.get("io.gold-trade.release.tree") or "") == release_tree
            and str(labels.get("io.gold-trade.release.input-signature") or "")
            == input_signature
            and IMAGE_ID.fullmatch(image_id)
            and image_id not in matches
        ):
            matches.append(image_id)
    if not matches:
        raise PrepareError("host_image_identity_missing")
    if len(matches) > 1:
        raise PrepareError("host_image_identity_ambiguous")
    return matches[0]


def _validate_bound_identity(
    extras: Mapping[str, Path],
    *,
    release_sha: str,
    release_tree: str,
    host_role: str,
    image_id: str,
    image_input_signature: str,
) -> None:
    pair = extras.get("market-pipeline-release-pair-receipt.json")
    image = extras.get("market-pipeline-image-prebuild-receipt.json")
    if pair is not None:
        document = _load_json(pair, label="pair_receipt")
        if document.get("release_sha") not in {None, release_sha}:
            raise PrepareError("release_sha_mismatch")
        if document.get("release_tree") not in {None, release_tree}:
            raise PrepareError("release_tree_mismatch")
        bound = _pair_host_image_id(document, host_role=host_role)
        if bound not in {None, image_id}:
            raise PrepareError("image_mismatch")
        if document.get("feed_mode") not in {None, "PRIVATE_PRIMARY"}:
            raise PrepareError("feed_mode_mismatch")
    if image is not None:
        document = _load_json(image, label="image_receipt")
        if document.get("release_sha") not in {None, release_sha}:
            raise PrepareError("release_sha_mismatch")
        if document.get("release_tree") not in {None, release_tree}:
            raise PrepareError("release_tree_mismatch")
        receipt_image = document.get("image_id")
        if host_role == "bot" and receipt_image not in {None, image_id}:
            raise PrepareError("image_mismatch")
        if receipt_image not in {None} and not IMAGE_ID.fullmatch(str(receipt_image)):
            raise PrepareError("image_mismatch")
        if document.get("input_signature") not in {None, image_input_signature}:
            raise PrepareError("image_signature_mismatch")
    role_name = "bot.release.env" if host_role == "bot" else "web.release.env"
    role_env = extras.get(role_name)
    if role_env is None:
        raise PrepareError("host_role_mismatch")
    text = role_env.read_text(encoding="utf-8")
    required_root = "MARKET_BOT_DATA_ROOT=" if host_role == "bot" else "MARKET_WEB_DATA_ROOT="
    if required_root not in text:
        raise PrepareError("host_role_mismatch")
    if "MARKET_PIPELINE_RELEASE_SHA=" in text and f"MARKET_PIPELINE_RELEASE_SHA={release_sha}" not in text:
        raise PrepareError("env_release_sha_mismatch")
    if "MARKET_PIPELINE_IMAGE=" in text and f"MARKET_PIPELINE_IMAGE={image_id}" not in text:
        raise PrepareError("env_image_mismatch")
    if "MARKET_PIPELINE_FEED_MODE=" in text and "MARKET_PIPELINE_FEED_MODE=PRIVATE_PRIMARY" not in text:
        raise PrepareError("env_feed_mode_mismatch")


def _regular_file(path: Path, *, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise PrepareError(f"{label}_unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise PrepareError(f"{label}_file_invalid")


def _payload_matches(release_dir: Path, payload_dir: Path, extras: Mapping[str, Path]) -> bool:
    manifest = release_dir / "control-payload.sha256"
    if not manifest.is_file():
        return False
    try:
        expected = {
            name: digest
            for digest, name in (
                line.split("  ./", 1) for line in manifest.read_text(encoding="utf-8").splitlines() if line
            )
        }
    except ValueError:
        return False
    for relative, digest in expected.items():
        candidate = release_dir / relative
        if not candidate.is_file() or _digest_path(candidate) != digest:
            return False
        source = payload_dir / relative
        if not source.is_file() or _digest_path(source) != digest:
            return False
    for name, source in extras.items():
        installed = release_dir / name
        if not installed.is_file() or _digest_path(installed) != _digest_path(source):
            return False
    return True


def install_control_release(
    *,
    base_dir: Path,
    release_sha: str,
    release_tree: str,
    host_role: str,
    payload_dir: Path,
    extras: Mapping[str, Path],
    image_id: str,
    image_input_signature: str,
    receipt: Path,
) -> dict[str, Any]:
    if not HEX40.fullmatch(release_sha) or not HEX40.fullmatch(release_tree):
        raise PrepareError("release_identity_invalid")
    if host_role not in {"bot", "web"}:
        raise PrepareError("host_role_invalid")
    if not IMAGE_ID.fullmatch(image_id) or not HEX64.fullmatch(image_input_signature):
        raise PrepareError("image_identity_invalid")
    if payload_dir.is_symlink() or any(child.is_symlink() for child in payload_dir.rglob("*")):
        raise PrepareError("payload_symlink_forbidden")
    validate_lexical_path(str(base_dir), label="release_base", repository_root=Path("/nonexistent-repo"))
    if base_dir.exists() or base_dir.is_symlink():
        info = base_dir.lstat()
        if base_dir.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise PrepareError("release_base_invalid")
        if info.st_uid != os.geteuid():
            raise PrepareError("release_base_owner_mode_invalid")
        mode = stat.S_IMODE(info.st_mode)
        if mode != 0o700:
            if mode not in {0o755, 0o750, 0o711, 0o710, 0o701}:
                raise PrepareError("release_base_owner_mode_invalid")
            os.chmod(base_dir, 0o700)
            info = base_dir.lstat()
            if stat.S_IMODE(info.st_mode) != 0o700:
                raise PrepareError("release_base_owner_mode_invalid")
    else:
        base_dir.mkdir(mode=0o700, parents=True)
        os.chmod(base_dir, 0o700)
    release_dir = base_dir / release_sha
    incoming = base_dir / f".{release_sha}.incoming"
    if incoming.exists() or incoming.is_symlink():
        raise PrepareError("incoming_transaction_present")
    extras = dict(extras)
    missing = [name for name in REQUIRED_INSTALL_NAMES if name not in extras and name != "control-payload.sha256"]
    if "control-payload.sha256" not in extras:
        extras["control-payload.sha256"] = payload_dir / "control-payload.sha256"
    for name in REQUIRED_INSTALL_NAMES:
        source = extras[name] if name in extras else payload_dir / name
        if name == "control-payload.sha256":
            source = extras["control-payload.sha256"]
        _regular_file(source, label=name.replace(".", "_"))
    _validate_bound_identity(
        extras,
        release_sha=release_sha,
        release_tree=release_tree,
        host_role=host_role,
        image_id=image_id,
        image_input_signature=image_input_signature,
    )
    if release_dir.exists() or release_dir.is_symlink():
        if release_dir.is_symlink() or not release_dir.is_dir():
            raise PrepareError("existing_release_not_directory")
        if _payload_matches(release_dir, payload_dir, extras):
            payload = _install_receipt(
                release_sha=release_sha,
                release_tree=release_tree,
                host_role=host_role,
                release_dir=release_dir,
                extras=extras,
                image_id=image_id,
                image_input_signature=image_input_signature,
                reused=True,
            )
            _atomic_json(receipt, payload)
            return payload
        raise PrepareError("existing_release_digest_mismatch")
    incoming.mkdir(mode=0o700)
    try:
        shutil.copytree(payload_dir, incoming, dirs_exist_ok=True, symlinks=False)
        for name, source in extras.items():
            destination = incoming / name
            shutil.copy2(source, destination)
            os.chmod(destination, 0o600)
        if incoming.is_symlink() or any(child.is_symlink() for child in incoming.rglob("*")):
            raise PrepareError("incoming_symlink_forbidden")
        manifest = incoming / "control-payload.sha256"
        _regular_file(manifest, label="control_manifest")
        completed = []
        for line in manifest.read_text(encoding="utf-8").splitlines():
            digest, separator, relative = line.partition("  ./")
            if not separator or not HEX64.fullmatch(digest):
                raise PrepareError("control_payload_manifest_invalid")
            observed = _digest_path(incoming / relative)
            if observed != digest:
                raise PrepareError("control_payload_drift")
            completed.append(relative)
        if not completed:
            raise PrepareError("control_payload_manifest_empty")
        for name, source in extras.items():
            if _digest_path(incoming / name) != _digest_path(source):
                raise PrepareError("control_release_extra_digest_mismatch")
            os.fsync(os.open(incoming / name, os.O_RDONLY))
        os.replace(incoming, release_dir)
        directory = os.open(base_dir, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        if incoming.exists() and incoming.name.endswith(".incoming"):
            shutil.rmtree(incoming, ignore_errors=True)
        raise
    payload = _install_receipt(
        release_sha=release_sha,
        release_tree=release_tree,
        host_role=host_role,
        release_dir=release_dir,
        extras=extras,
        image_id=image_id,
        image_input_signature=image_input_signature,
        reused=False,
    )
    _atomic_json(receipt, payload)
    return payload


def _install_receipt(
    *,
    release_sha: str,
    release_tree: str,
    host_role: str,
    release_dir: Path,
    extras: Mapping[str, Path],
    image_id: str,
    image_input_signature: str,
    reused: bool,
) -> dict[str, Any]:
    manifest = release_dir / "control-payload.sha256"
    role_env = "bot.release.env" if host_role == "bot" else "web.release.env"
    return {
        "schema": INSTALL_SCHEMA,
        "environment": "production",
        "release_sha": release_sha,
        "release_tree": release_tree,
        "host_role": host_role,
        "control_manifest_sha256": _digest_path(manifest),
        "image_id": image_id,
        "image_input_signature": image_input_signature,
        "role_env_sha256": _digest_path(release_dir / role_env),
        "installation_status": "PASS",
        "idempotent_reuse": reused,
        "fresh_install": not reused,
        "services_started": False,
        "database_mutated": False,
        "authority_changed": False,
        "capture_owner_changed": False,
        "secrets_disclosed": False,
        "created_at": _now(),
        "release_dir": str(release_dir),
    }


def write_prepare_receipt(
    *,
    receipt: Path,
    release_sha: str,
    release_tree: str,
    foundation: Mapping[str, Any],
    local_install: Mapping[str, Any],
    remote_install: Mapping[str, Any],
    preflight_sha256: str,
    control_manifest_sha256: str,
    image_id: str,
    historical_flags: Mapping[str, str],
) -> dict[str, Any]:
    validate_historical_flags(historical_flags)
    if not HEX64.fullmatch(preflight_sha256) or not HEX64.fullmatch(control_manifest_sha256):
        raise PrepareError("receipt_digest_invalid")
    payload = {
        "schema": PREPARE_SCHEMA,
        "environment": "production",
        "status": "PASS",
        "release_sha": release_sha,
        "release_tree": release_tree,
        "control_manifest_sha256": control_manifest_sha256,
        "image_id": image_id,
        "preflight_sha256": preflight_sha256,
        "foundation_schema": foundation.get("schema"),
        "local_install_status": local_install.get("installation_status"),
        "remote_install_status": remote_install.get("installation_status"),
        "local_idempotent_reuse": local_install.get("idempotent_reuse"),
        "remote_idempotent_reuse": remote_install.get("idempotent_reuse"),
        "historical_flags": {key: "0" for key in HISTORICAL_FLAGS},
        "services_started": False,
        "database_mutated": False,
        "authority_changed": False,
        "capture_owner_changed": False,
        "queue_owner_changed": False,
        "secrets_disclosed": False,
        "created_at": _now(),
    }
    _atomic_json(receipt, payload)
    return payload


def _add_confirm(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--confirm", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    flags = commands.add_parser("reject-historical-flags")
    _add_confirm(flags)
    for key in HISTORICAL_FLAGS:
        flags.add_argument(f"--{key.lower().replace('_', '-')}", default="0")
    source = commands.add_parser("validate-topology-source")
    _add_confirm(source)
    source.add_argument("--role", choices=("web", "bot"), required=True)
    source.add_argument("--source", type=Path, required=True)
    source.add_argument("--repository-root", type=Path, required=True)
    source.add_argument("--continuity-receipt", type=Path)
    foundation = commands.add_parser("prepare-foundation")
    _add_confirm(foundation)
    foundation.add_argument("--bot-data-root", type=Path, default=Path(CANONICAL_BOT_DATA_ROOT))
    foundation.add_argument("--web-data-root", type=Path, default=Path(CANONICAL_WEB_DATA_ROOT))
    foundation.add_argument("--web-backup-root", type=Path, default=Path(CANONICAL_WEB_BACKUP_ROOT))
    foundation.add_argument("--offhost-root", type=Path, default=Path(CANONICAL_OFFHOST_ROOT))
    foundation.add_argument("--backup-key", type=Path, default=Path(CANONICAL_BACKUP_KEY))
    foundation.add_argument("--receipt", type=Path, required=True)
    foundation.add_argument("--release-sha", required=True)
    foundation.add_argument("--release-tree", required=True)
    foundation.add_argument("--continuity-receipt", type=Path)
    key = commands.add_parser("generate-backup-key")
    _add_confirm(key)
    key.add_argument("--key-file", type=Path, required=True)
    install = commands.add_parser("install-control-release")
    _add_confirm(install)
    install.add_argument("--base-dir", type=Path, required=True)
    install.add_argument("--release-sha", required=True)
    install.add_argument("--release-tree", required=True)
    install.add_argument("--host-role", choices=("web", "bot"), required=True)
    install.add_argument("--payload-dir", type=Path, required=True)
    install.add_argument("--control-manifest", type=Path)
    install.add_argument("--bot-env", type=Path, required=True)
    install.add_argument("--web-env", type=Path, required=True)
    install.add_argument("--image-receipt", type=Path, required=True)
    install.add_argument("--pair-receipt", type=Path, required=True)
    install.add_argument("--image-id", required=True)
    install.add_argument("--image-input-signature", required=True)
    install.add_argument("--receipt", type=Path, required=True)
    select_image = commands.add_parser("select-host-image")
    _add_confirm(select_image)
    select_image.add_argument("--release-sha", required=True)
    select_image.add_argument("--release-tree", required=True)
    select_image.add_argument("--input-signature", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.confirm != CONFIRMATION and not (
            args.command == "generate-backup-key" and args.confirm == KEY_CONFIRMATION
        ) and not (
            args.command == "select-host-image"
            and args.confirm == SELECT_IMAGE_CONFIRMATION
        ):
            raise PrepareError("confirmation_invalid")
        if args.command == "reject-historical-flags":
            values = {
                key: getattr(args, key.lower())
                for key in HISTORICAL_FLAGS
            }
            validate_historical_flags(values)
            result = {"status": "PASS", "historical_flags": {key: "0" for key in HISTORICAL_FLAGS}, "secrets_disclosed": False}
        elif args.command == "validate-topology-source":
            validate_topology_source(
                args.source,
                role=args.role,
                repository_root=args.repository_root,
                continuity_receipt=args.continuity_receipt,
            )
            result = {
                "status": "PASS",
                "role": args.role,
                "source_sha256": _digest_path(args.source),
                "secrets_disclosed": False,
            }
        elif args.command == "generate-backup-key":
            result = {
                "status": "PASS",
                **generate_or_reuse_backup_key(args.key_file),
            }
        elif args.command == "prepare-foundation":
            foundation = prepare_foundation(
                bot_data_root=args.bot_data_root,
                web_data_root=args.web_data_root,
                web_backup_root=args.web_backup_root,
                offhost_root=args.offhost_root,
                backup_key=args.backup_key,
                receipt=args.receipt,
                release_sha=args.release_sha,
                release_tree=args.release_tree,
                continuity_receipt=args.continuity_receipt,
            )
            result = {
                "status": "PASS",
                "schema": foundation["schema"],
                "release_sha": foundation["release_sha"],
                "release_tree": foundation["release_tree"],
                "backup_key_reused": foundation["backup_key"]["reused"],
                "services_started": False,
                "database_mutated": False,
                "authority_changed": False,
                "capture_owner_changed": False,
                "secrets_disclosed": False,
            }
        elif args.command == "select-host-image":
            result = {
                "status": "PASS",
                "image_id": select_host_image_id(
                    _load_inspect_documents(sys.stdin.buffer.read()),
                    release_sha=args.release_sha,
                    release_tree=args.release_tree,
                    input_signature=args.input_signature,
                ),
                "secrets_disclosed": False,
            }
        elif args.command == "install-control-release":
            extras = {
                "bot.release.env": args.bot_env,
                "web.release.env": args.web_env,
                "market-pipeline-image-prebuild-receipt.json": args.image_receipt,
                "market-pipeline-release-pair-receipt.json": args.pair_receipt,
                "control-payload.sha256": (
                    args.control_manifest
                    if args.control_manifest is not None
                    else args.payload_dir / "control-payload.sha256"
                ),
            }
            result = {
                "status": "PASS",
                **install_control_release(
                    base_dir=args.base_dir,
                    release_sha=args.release_sha,
                    release_tree=args.release_tree,
                    host_role=args.host_role,
                    payload_dir=args.payload_dir,
                    extras=extras,
                    image_id=args.image_id,
                    image_input_signature=args.image_input_signature,
                    receipt=args.receipt,
                ),
            }
        else:
            raise PrepareError("command_invalid")
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, ValueError, PrepareError) as exc:
        print(
            json.dumps(
                {"status": "BLOCKED", "reason": str(exc), "secrets_disclosed": False},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=os.sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
