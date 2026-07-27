#!/usr/bin/env python3
"""Execute one target role's fail-closed staging migration state machine locally."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import time
from typing import Any
from uuid import UUID

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

from core.human_approval import approval_subject, verify_human_approval
from core.secure_file_io import write_secure_new_bytes
from scripts.publish_three_site_staging_seed_campaign import (
    _assert_directory_binding,
    _immutable_release_binding,
    _open_private_directory,
)
from scripts.run_three_site_staging_source_backup import DOCKER
from scripts.three_site_staging_migration_journal import (
    MigrationJournal,
    ROLE_PHASES,
    _validate as validate_migration_journal,
)
from scripts.verify_three_site_staging_host_identity import (
    verify_host_snapshot,
)
from scripts.verify_three_site_staging_image_inventory import image_content_descriptor
from scripts.verify_three_site_staging_migration_plan import (
    TARGET_SEED_MAP,
    verify_migration_plan,
)
from scripts.verify_three_site_staging_role_bundle import verify_role_bundle


SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
CANONICAL_COMPOSE = REPO_ROOT / "deploy/staging/docker-compose.three-site.yml"
MAX_INPUT_BYTES = 4 * 1024 * 1024
VARIABLE_RE = re.compile(
    r"\$\{([A-Z_][A-Z0-9_]*)(?:(:-|:\?)([^}]*))?\}"
)
EXPECTED_HEAD = "b986c7d8e0f1"
ROLE_DB = {
    "bot_fi": ("bot_fi_db", "BOT_FI_POSTGRES_USER", "BOT_FI_POSTGRES_DB"),
    "webapp_fi": ("webapp_fi_db", "WEBAPP_FI_POSTGRES_USER", "WEBAPP_FI_POSTGRES_DB"),
    "webapp_ir": ("webapp_ir_db", "WEBAPP_IR_POSTGRES_USER", "WEBAPP_IR_POSTGRES_DB"),
    "witness": ("witness_db", "WITNESS_POSTGRES_USER", "WITNESS_POSTGRES_DB"),
}
ROLE_VOLUME_SERVICE = {
    "bot_fi": "bot_fi_api",
    "webapp_fi": "webapp_fi_api",
    "webapp_ir": "webapp_ir_api",
}
ROLE_PRIVATE = {
    "bot_fi": ("bot_fi_dr_receiver", "bot_fi_dr_projection", "bot_fi_dr_tls"),
    "webapp_fi": ("webapp_fi_dr_receiver", "webapp_fi_dr_projection", "webapp_fi_dr_tls"),
    "webapp_ir": ("webapp_ir_dr_receiver", "webapp_ir_dr_projection", "webapp_ir_dr_tls"),
    "witness": ("witness_api", "witness_dr_tls"),
}
ROLE_WORKERS = {
    "bot_fi": ("bot_fi_dr_delivery",),
    "webapp_fi": ("webapp_fi_dr_delivery", "webapp_fi_blobs"),
    "webapp_ir": ("webapp_ir_writer_control", "webapp_ir_dr_delivery", "webapp_ir_blobs"),
}
ROLE_PUBLIC = {
    "bot_fi": ("bot_fi_redis", "bot_fi_api", "bot_fi_bot"),
    "webapp_fi": ("webapp_fi_redis", "webapp_fi_api", "webapp_fi_effects"),
    "webapp_ir": ("webapp_ir_redis", "webapp_ir_api", "webapp_ir_effects"),
}


class RoleMigrationError(RuntimeError):
    pass


def _reject_duplicate_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RoleMigrationError("security-sensitive JSON contains a duplicate key")
        result[key] = value
    return result


def _verify_exact_release(expected_release_sha: str) -> None:
    if (
        Path(__file__).resolve()
        != (REPO_ROOT / "scripts/run_three_site_staging_role_migration.py").resolve()
    ):
        raise RoleMigrationError(
            "role migration is not executing from its fixed release root"
        )
    try:
        _immutable_release_binding(REPO_ROOT.resolve(), expected_release_sha)
    except Exception as exc:
        raise RoleMigrationError(
            "role migration requires the exact fully clean immutable Git release"
        ) from exc


def _read_root_file(
    path: Path,
    *,
    label: str,
    expected_mode: int,
    maximum: int = MAX_INPUT_BYTES,
) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != expected_mode
            or before.st_size <= 0
            or before.st_size > maximum
        ):
            raise RoleMigrationError(
                f"{label} must be one root-owned mode-{expected_mode:04o} file"
            )
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
        )
        if (
            len(payload) > maximum
            or any(getattr(before, field) != getattr(after, field) for field in stable)
        ):
            raise RoleMigrationError(f"{label} changed while it was read")
        return payload
    except RoleMigrationError:
        raise
    except OSError as exc:
        raise RoleMigrationError(f"{label} is unavailable or unsafe") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _proc_descriptors(arguments: list[str]) -> tuple[int, ...]:
    return tuple(
        sorted(
            {
                int(match.group(1))
                for argument in arguments
                if (
                    match := re.fullmatch(
                        r"/proc/self/fd/([0-9]+)",
                        argument,
                    )
                )
            }
        )
    )


def _run(arguments: list[str], *, timeout: int = 120) -> str:
    try:
        result = subprocess.run(
            arguments,
            text=True,
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env=SAFE_ENV,
            pass_fds=_proc_descriptors(arguments),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RoleMigrationError(f"role migration command unavailable: {arguments[0]}") from exc
    if result.returncode != 0:
        raise RoleMigrationError(f"role migration command failed closed: {Path(arguments[0]).name}")
    return result.stdout.strip()


def _mapping(values: list[str], roles: set[str], *, label: str) -> dict[str, dict[str, Any]]:
    result = {}
    for value in values:
        role, separator, raw_path = value.partition("=")
        if not separator or role not in roles or role in result or not raw_path:
            raise RoleMigrationError(f"{label} must use unique role=/path mappings")
        result[role] = _secure_json(Path(raw_path), label=f"{label} {role}")
    if set(result) != roles:
        raise RoleMigrationError(f"{label} role set is incomplete")
    return result


def _secure_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            _read_root_file(
                path,
                label=label,
                expected_mode=0o600,
            ).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_pairs,
        )
    except RoleMigrationError:
        raise
    except Exception as exc:
        raise RoleMigrationError(f"{label} is unreadable or unsafe") from exc
    if not isinstance(value, dict):
        raise RoleMigrationError(f"{label} must contain one JSON object")
    return value


def _target_seed(
    document: dict[str, Any],
    *,
    role: str,
    campaign_id: str,
    release_sha: str,
    seed_manifest: dict[str, Any] | None,
    signed_manifest_sha256: str | None,
) -> dict[str, Any]:
    fields = {
        "schema", "campaign_id", "release_sha", "target_role", "source_role",
        "seed_manifest_sha256", "mode", "verified_at", "objects",
    }
    expected_source, expected_mode = TARGET_SEED_MAP[role]
    actual_manifest_sha256 = (
        hashlib.sha256(
            json.dumps(
                seed_manifest,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if seed_manifest is not None
        else None
    )
    if (
        set(document) != fields
        or document["schema"] != "three-site-staging-target-seed-v2"
        or document["campaign_id"] != campaign_id
        or document["release_sha"] != release_sha
        or document["target_role"] != role
        or document["source_role"] != expected_source
        or document["seed_manifest_sha256"] != actual_manifest_sha256
        or actual_manifest_sha256 != signed_manifest_sha256
        or document["mode"] != expected_mode
        or not isinstance(document["objects"], list)
        or len(document["objects"]) != (0 if role == "witness" else 3)
    ):
        raise RoleMigrationError("target seed evidence identity is invalid")
    manifest_by_kind = (
        {
            str(item["kind"]): item
            for item in seed_manifest["objects"]
        }
        if seed_manifest is not None
        else {}
    )
    if len(manifest_by_kind) != (0 if role == "witness" else 3):
        raise RoleMigrationError("signed target seed manifest object set is invalid")
    kinds: set[str] = set()
    for item in document["objects"]:
        if not isinstance(item, dict) or set(item) != {
            "kind", "object_key", "version_id", "ciphertext_sha256",
            "ciphertext_bytes", "plaintext_sha256", "plaintext_bytes",
            "publication_intent", "path",
        }:
            raise RoleMigrationError("target seed object evidence fields are invalid")
        kind = str(item["kind"])
        signed = manifest_by_kind.get(kind)
        if kind not in {"postgres", "uploads", "audit"} or kind in kinds:
            raise RoleMigrationError("target seed object kind is invalid or duplicate")
        if (
            not isinstance(signed, dict)
            or any(
                item[field] != signed[field]
                for field in (
                    "kind",
                    "object_key",
                    "version_id",
                    "ciphertext_sha256",
                    "ciphertext_bytes",
                    "plaintext_sha256",
                    "plaintext_bytes",
                    "publication_intent",
                )
            )
            or not str(item["version_id"])
            or item["version_id"] == "null"
        ):
            raise RoleMigrationError(
                "target seed object differs from the signed manifest"
            )
        descriptor, _metadata = _open_seed_artifact(item)
        os.close(descriptor)
        kinds.add(kind)
    if kinds != (set() if role == "witness" else {"postgres", "uploads", "audit"}):
        raise RoleMigrationError("target seed artifact set is incomplete")
    return document


def verify_inputs(
    args: argparse.Namespace,
    *,
    allow_expired_plan: bool = False,
) -> dict[str, Any]:
    inventory = _secure_json(args.inventory, label="provisioned inventory")
    migration_plan = _secure_json(args.plan, label="migration plan")
    backups = _mapping(args.backup_manifest, {"bot_fi", "webapp_fi"}, label="--backup-manifest")
    seeds = _mapping(args.seed_manifest, {"bot_fi", "webapp_fi"}, label="--seed-manifest")
    images = _mapping(
        args.image_inventory,
        {"bot_fi", "webapp_fi", "webapp_ir", "witness"},
        label="--image-inventory",
    )
    verified = verify_migration_plan(
        migration_plan,
        approval=_secure_json(args.plan_approval, label="migration plan approval"),
        inventory=inventory,
        inventory_approval=_secure_json(
            args.inventory_approval,
            label="inventory approval",
        ),
        approval_policy=_secure_json(args.approval_policy, label="approval policy"),
        freeze_evidence=[
            _secure_json(path, label="source freeze evidence")
            for path in args.freeze_evidence
        ],
        image_inventories=images,
        backup_manifests=backups,
        seed_manifests=seeds,
        require_fresh_approval=not allow_expired_plan,
        allow_expired_plan=allow_expired_plan,
    )
    _verify_exact_release(verified["release_sha"])
    role_cli = args.role.replace("_", "-")
    role_compose_bytes = _read_root_file(
        args.role_compose,
        label="role Compose bundle",
        expected_mode=0o640,
    )
    env_bytes = _read_root_file(
        args.env_file,
        label="role environment bundle",
        expected_mode=0o600,
    )
    if args.canonical_compose.resolve() != CANONICAL_COMPOSE.resolve():
        raise RoleMigrationError("canonical Compose must use the fixed release path")
    canonical_compose_bytes = _read_root_file(
        CANONICAL_COMPOSE,
        label="canonical release Compose",
        expected_mode=0o644,
    )
    bundle = verify_role_bundle(
        role=role_cli,
        canonical_compose=yaml.safe_load(canonical_compose_bytes.decode("utf-8")),
        role_compose_bytes=role_compose_bytes,
        env_bytes=env_bytes,
        inventory=inventory,
        approval=_secure_json(args.inventory_approval, label="inventory approval"),
        approval_policy=_secure_json(args.approval_policy, label="approval policy"),
        verify_files=True,
        required_inventory_stage="provisioned",
    )
    role_inventory = next(row for row in inventory["roles"] if row["role"] == args.role)
    host = verify_host_snapshot(
        _secure_json(args.host_snapshot, label="host snapshot"),
        role=role_cli,
        role_inventory=role_inventory,
        release_sha=verified["release_sha"],
        stage="provisioned",
    )
    target_seed = _target_seed(
        _secure_json(args.target_seed, label="target seed evidence"),
        role=args.role,
        campaign_id=verified["campaign_id"],
        release_sha=verified["release_sha"],
        seed_manifest=(
            None
            if TARGET_SEED_MAP[args.role][0] is None
            else seeds[TARGET_SEED_MAP[args.role][0]]
        ),
        signed_manifest_sha256=(
            None
            if TARGET_SEED_MAP[args.role][0] is None
            else next(
                row["manifest_sha256"]
                for row in migration_plan["seed_bundles"]
                if row["source_role"] == TARGET_SEED_MAP[args.role][0]
            )
        ),
    )
    return {
        "verified_plan": verified,
        "inventory": inventory,
        "role_inventory": role_inventory,
        "bundle": bundle,
        "host": host,
        "target_seed": target_seed,
        "role_compose_bytes": role_compose_bytes,
        "env_bytes": env_bytes,
        "image_inventory": images[args.role],
        "approval_policy": _secure_json(args.approval_policy, label="approval policy"),
        "env": {
            line.partition("=")[0]: line.partition("=")[2]
            for line in env_bytes.decode().splitlines()
            if line and not line.startswith("#") and "=" in line
        },
    }


def _expand_image_reference(reference: str, env: dict[str, str]) -> str:
    def replacement(match: re.Match[str]) -> str:
        name, operator, operand = match.groups()
        value = env.get(name, "")
        if operator == ":-":
            return value or str(operand)
        if operator == ":?":
            if not value:
                raise RoleMigrationError(
                    f"role Compose image requires environment value: {name}"
                )
            return value
        return value

    expanded = VARIABLE_RE.sub(replacement, reference)
    if not expanded or "$" in expanded:
        raise RoleMigrationError("role Compose image reference did not resolve exactly")
    return expanded


def _sanitized_runtime_compose(
    role_compose_bytes: bytes,
    *,
    env: dict[str, str],
    image_inventory: dict[str, Any],
) -> bytes:
    try:
        document = yaml.safe_load(role_compose_bytes.decode("utf-8"))
    except Exception as exc:
        raise RoleMigrationError("role Compose bundle is not valid YAML") from exc
    services = document.get("services") if isinstance(document, dict) else None
    inventory_rows = image_inventory.get("images")
    if not isinstance(services, dict) or not isinstance(inventory_rows, list):
        raise RoleMigrationError("role runtime Compose/image inventory is incomplete")
    image_ids = {
        str(row.get("reference")): str(row.get("image_id"))
        for row in inventory_rows
        if isinstance(row, dict)
    }
    if len(image_ids) != len(inventory_rows):
        raise RoleMigrationError("role image inventory references are ambiguous")
    used_references: set[str] = set()
    for service_name, service in services.items():
        if not isinstance(service, dict) or not isinstance(service.get("image"), str):
            raise RoleMigrationError(
                f"role runtime service lacks a signed image: {service_name}"
            )
        reference = _expand_image_reference(service["image"], env)
        image_id = image_ids.get(reference)
        if re.fullmatch(r"sha256:[0-9a-f]{64}", str(image_id or "")) is None:
            raise RoleMigrationError(
                f"role runtime image is absent from signed inventory: {reference}"
            )
        service["image"] = image_id
        service.pop("build", None)
        service["pull_policy"] = "never"
        used_references.add(reference)
    if used_references != set(image_ids):
        raise RoleMigrationError(
            "signed image inventory differs from runtime Compose image references"
        )
    return yaml.safe_dump(document, sort_keys=False).encode("utf-8")


def _sealed_memfd(name: str, payload: bytes, *, mode: int) -> int:
    required = (
        "memfd_create",
        "MFD_CLOEXEC",
        "MFD_ALLOW_SEALING",
    )
    if any(not hasattr(os, attribute) for attribute in required):
        raise RoleMigrationError("sealed in-memory runtime bundle is unsupported")
    descriptor = os.memfd_create(
        name,
        flags=os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
    )
    try:
        view = memoryview(payload)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise RoleMigrationError("runtime bundle write made no progress")
            written += count
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        seals = (
            fcntl.F_SEAL_SEAL
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_WRITE
        )
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, seals)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _hash_descriptor(
    descriptor: int,
    *,
    metadata: os.stat_result,
    maximum: int,
    label: str,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                raise RoleMigrationError(f"{label} exceeds its safety bound")
            digest.update(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise RoleMigrationError(f"{label} changed while pinned") from exc
    stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
    if any(getattr(metadata, field) != getattr(after, field) for field in stable):
        raise RoleMigrationError(f"{label} changed while pinned")
    return digest.hexdigest(), size


def _verify_tar_descriptor(descriptor: int, *, label: str) -> None:
    duplicate = os.dup(descriptor)
    try:
        os.lseek(duplicate, 0, os.SEEK_SET)
        with os.fdopen(duplicate, "rb", closefd=True) as source:
            duplicate = -1
            with tarfile.open(fileobj=source, mode="r:gz") as archive:
                for member in archive:
                    normalized = PurePosixPath(member.name)
                    if (
                        normalized.is_absolute()
                        or ".." in normalized.parts
                        or member.issym()
                        or member.islnk()
                        or member.isdev()
                    ):
                        raise RoleMigrationError(
                            f"{label} contains an unsafe archive member"
                        )
    except (OSError, tarfile.TarError) as exc:
        raise RoleMigrationError(f"{label} archive is invalid") from exc
    finally:
        if duplicate >= 0:
            os.close(duplicate)


def _open_seed_artifact(item: dict[str, Any]) -> tuple[int, os.stat_result]:
    path = Path(str(item["path"]))
    if not path.is_absolute() or path.parent == Path("/"):
        raise RoleMigrationError("target seed artifact path is not private")
    directory = _open_private_directory(path.parent)
    descriptor = -1
    try:
        _assert_directory_binding(path.parent, directory)
        descriptor = os.open(
            path.name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise RoleMigrationError("target seed artifact is not root-only")
        observed = _hash_descriptor(
            descriptor,
            metadata=metadata,
            maximum=4 * 1024 * 1024 * 1024,
            label=f"{item['kind']} target seed",
        )
        if observed != (item["plaintext_sha256"], item["plaintext_bytes"]):
            raise RoleMigrationError(
                "target seed artifact differs from its signed manifest"
            )
        if item["kind"] in {"uploads", "audit"}:
            _verify_tar_descriptor(
                descriptor,
                label=f"{item['kind']} target seed",
            )
        return descriptor, metadata
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    finally:
        os.close(directory)


class LocalRoleBackend:
    def __init__(self, args: argparse.Namespace, context: dict[str, Any]):
        self.args = args
        self.context = context
        self.role = args.role
        self._runtime_descriptors: list[int] = []
        self._seed_handles: dict[str, tuple[int, os.stat_result, dict[str, Any]]] = {}
        try:
            role_compose_bytes = context["role_compose_bytes"]
            if "image_inventory" in context:
                role_compose_bytes = _sanitized_runtime_compose(
                    role_compose_bytes,
                    env=context["env"],
                    image_inventory=context["image_inventory"],
                )
            runtime_document = yaml.safe_load(
                role_compose_bytes.decode("utf-8")
            )
            runtime_services = (
                runtime_document.get("services")
                if isinstance(runtime_document, dict)
                else None
            )
            self.project_name = (
                str(runtime_document.get("name"))
                if isinstance(runtime_document, dict)
                else ""
            )
            if (
                re.fullmatch(
                    r"[a-z0-9][a-z0-9_-]{2,120}",
                    self.project_name,
                )
                is None
                or not isinstance(runtime_services, dict)
            ):
                raise RoleMigrationError(
                    "runtime Compose project identity is invalid"
                )
            self._runtime_service_images = {
                str(name): str(service.get("image") or "")
                for name, service in runtime_services.items()
                if isinstance(service, dict)
            }
            writer_service = runtime_services.get(
                "webapp_fi_writer_control",
                {},
            )
            writer_networks = (
                writer_service.get("networks", [])
                if isinstance(writer_service, dict)
                else []
            )
            if isinstance(writer_networks, dict):
                writer_network_names = set(map(str, writer_networks))
            elif isinstance(writer_networks, list):
                writer_network_names = set(map(str, writer_networks))
            else:
                writer_network_names = set()
            writer_command = (
                writer_service.get("command")
                if isinstance(writer_service, dict)
                else None
            )
            self._writer_runtime_command = (
                tuple(map(str, writer_command))
                if isinstance(writer_command, list)
                and writer_command
                and all(isinstance(value, str) for value in writer_command)
                else ()
            )
            network_definitions = runtime_document.get("networks", {})
            self._writer_bootstrap_networks = {
                str(
                    network_definitions.get(name, {}).get("name")
                    or f"{self.project_name}_{name}"
                )
                for name in writer_network_names
                if isinstance(network_definitions, dict)
                and isinstance(network_definitions.get(name, {}), dict)
            }
            compose_descriptor = _sealed_memfd(
                f"{self.role}-compose",
                role_compose_bytes,
                mode=0o600,
            )
            self._runtime_descriptors.append(compose_descriptor)
            env_descriptor = _sealed_memfd(
                f"{self.role}-env",
                context["env_bytes"],
                mode=0o600,
            )
            self._runtime_descriptors.append(env_descriptor)
            self.prefix = [
                DOCKER,
                "compose",
                "-f",
                f"/proc/self/fd/{compose_descriptor}",
                "--env-file",
                f"/proc/self/fd/{env_descriptor}",
            ]
            for item in context.get("target_seed", {}).get("objects", []):
                descriptor, metadata = _open_seed_artifact(item)
                self._seed_handles[str(item["kind"])] = (
                    descriptor,
                    metadata,
                    item,
                )
            self.db_service, self.user_key, self.database_key = ROLE_DB[self.role]
            self.user = context["env"][self.user_key]
            self.database = context["env"][self.database_key]
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        for descriptor, _metadata, _item in self._seed_handles.values():
            os.close(descriptor)
        self._seed_handles.clear()
        for descriptor in self._runtime_descriptors:
            os.close(descriptor)
        self._runtime_descriptors.clear()

    def _attest_images(self) -> None:
        document = self.context.get("image_inventory")
        rows = document.get("images") if isinstance(document, dict) else None
        if not isinstance(rows, list):
            raise RoleMigrationError("signed role image inventory is unavailable")
        for item in rows:
            image_id = str(item["image_id"])
            try:
                inspected = json.loads(
                    _run([DOCKER, "image", "inspect", image_id])
                )
            except json.JSONDecodeError as exc:
                raise RoleMigrationError(
                    "live Docker image inspection is invalid"
                ) from exc
            if (
                not isinstance(inspected, list)
                or len(inspected) != 1
                or not isinstance(inspected[0], dict)
            ):
                raise RoleMigrationError("live Docker image inspection is ambiguous")
            raw = inspected[0]
            config = raw.get("Config") if isinstance(raw.get("Config"), dict) else {}
            labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
            descriptor, content_identity = image_content_descriptor(raw)
            if (
                str(raw.get("Id") or "") != image_id
                or sorted(str(value) for value in (raw.get("RepoDigests") or []))
                != item["repo_digests"]
                or labels.get("org.opencontainers.image.revision")
                != item["release_label"]
                or descriptor != item["content_descriptor"]
                or content_identity != item["content_identity"]
            ):
                raise RoleMigrationError(
                    f"live Docker image differs from signed inventory: {item['reference']}"
                )

    def _mutation_boundary(self, *, images: bool = True) -> None:
        _verify_exact_release(self.context["verified_plan"]["release_sha"])
        if images:
            self._attest_images()

    def _seed_source(self, kind: str):
        try:
            descriptor, metadata, item = self._seed_handles[kind]
        except KeyError as exc:
            raise RoleMigrationError(f"pinned target seed is unavailable: {kind}") from exc
        observed = _hash_descriptor(
            descriptor,
            metadata=metadata,
            maximum=4 * 1024 * 1024 * 1024,
            label=f"{kind} target seed",
        )
        if observed != (item["plaintext_sha256"], item["plaintext_bytes"]):
            raise RoleMigrationError(f"pinned target seed changed: {kind}")
        if kind in {"uploads", "audit"}:
            _verify_tar_descriptor(descriptor, label=f"{kind} target seed")
        os.lseek(descriptor, 0, os.SEEK_SET)
        return os.fdopen(os.dup(descriptor), "rb", closefd=True)

    def _psql(self, sql: str, *, database: str | None = None) -> str:
        return _run(
            [
                *self.prefix, "exec", "-T", self.db_service,
                "psql", "-v", "ON_ERROR_STOP=1", "-U", self.user,
                "-d", database or self.database, "-Atqc", sql,
            ]
        )

    def _wait_db(self) -> None:
        for _attempt in range(30):
            result = subprocess.run(
                [
                    *self.prefix, "exec", "-T", self.db_service,
                    "pg_isready", "-U", self.user, "-d", self.database,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
                env=SAFE_ENV,
                pass_fds=_proc_descriptors(self.prefix),
            )
            if result.returncode == 0:
                return
            time.sleep(1)
        raise RoleMigrationError("target PostgreSQL did not become ready")

    def _compose_run(self, service: str) -> None:
        self._mutation_boundary()
        _run(
            [
                *self.prefix,
                "run",
                "--rm",
                "--no-deps",
                "--pull",
                "never",
                "-T",
                service,
            ],
            timeout=900,
        )

    def _assert_database_migration_quiescent(self) -> None:
        """Prove that no old application writer can overlap Alembic preflight."""

        services = tuple(
            item.strip()
            for item in _run([*self.prefix, "config", "--services"]).splitlines()
            if item.strip()
        )
        if not services or self.db_service not in services:
            raise RoleMigrationError("role Compose service inventory is incomplete")
        for service in services:
            if service == self.db_service or service.endswith("_redis"):
                continue
            if _run([*self.prefix, "ps", "-q", service]):
                raise RoleMigrationError(
                    f"database migration requires the application service to be stopped: {service}"
                )
        # A just-finished restore can leave a short-lived PostgreSQL client
        # connection visible for a moment.  Do not mistake that harmless tail
        # for an application writer: require three consecutive quiescent
        # samples, while still failing closed if the target never becomes
        # quiescent within this bounded window.
        consecutive_quiescent_samples = 0
        for _sample in range(30):
            active_clients = self._psql(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname=current_database() AND pid<>pg_backend_pid() "
                "AND backend_type='client backend'"
            )
            if active_clients == "0":
                consecutive_quiescent_samples += 1
                if consecutive_quiescent_samples == 3:
                    return
            else:
                consecutive_quiescent_samples = 0
            time.sleep(0.5)
        raise RoleMigrationError(
            "database migration requires three consecutive zero-client samples"
        )

    def _wait_services_ready(
        self,
        services: tuple[str, ...],
        *,
        stable_seconds: int = 5,
    ) -> None:
        deadline = time.monotonic() + 90.0
        stable_since: float | None = None
        while time.monotonic() < deadline:
            all_ready = True
            for service in services:
                container = _run([*self.prefix, "ps", "-q", service])
                if not container:
                    all_ready = False
                    break
                state_raw = _run(
                    [
                        DOCKER,
                        "inspect",
                        "--format",
                        "{{json .State}}",
                        container,
                    ]
                )
                try:
                    state = json.loads(state_raw)
                except json.JSONDecodeError as exc:
                    raise RoleMigrationError(
                        f"service state is unreadable: {service}"
                    ) from exc
                health = (state.get("Health") or {}).get("Status")
                if state.get("Running") is not True or health == "unhealthy":
                    all_ready = False
                    break
                if health is not None and health != "healthy":
                    all_ready = False
                    break
                environment_raw = _run(
                    [
                        DOCKER,
                        "inspect",
                        "--format",
                        "{{json .Config.Env}}",
                        container,
                    ]
                )
                try:
                    environment_rows = json.loads(environment_raw)
                except json.JSONDecodeError as exc:
                    raise RoleMigrationError(
                        f"service environment is unreadable: {service}"
                    ) from exc
                if not isinstance(environment_rows, list) or any(
                    not isinstance(row, str) or "=" not in row
                    for row in environment_rows
                ):
                    raise RoleMigrationError(
                        f"service environment is malformed: {service}"
                    )
                environment: dict[str, str] = {}
                for row in environment_rows:
                    key, _separator, value = row.partition("=")
                    if not key or key in environment:
                        raise RoleMigrationError(
                            f"service environment is ambiguous: {service}"
                        )
                    environment[key] = value
                release_key = (
                    "WRITER_WITNESS_RELEASE_SHA"
                    if self.role == "witness" and service == "witness_api"
                    else "RELEASE_SHA"
                )
                observed_release = environment.get(release_key, "")
                if observed_release != self.context["verified_plan"]["release_sha"]:
                    raise RoleMigrationError(
                        f"service release identity mismatch: {service}"
                    )
            if all_ready:
                stable_since = stable_since or time.monotonic()
                if time.monotonic() - stable_since >= stable_seconds:
                    return
            else:
                stable_since = None
            time.sleep(1)
        raise RoleMigrationError(
            "services did not retain exact running/healthy release identity: "
            + ",".join(services)
        )

    def _wait_infrastructure_ready(
        self,
        services: tuple[str, ...],
        *,
        stable_seconds: int = 5,
    ) -> None:
        deadline = time.monotonic() + 90.0
        stable_since: float | None = None
        while time.monotonic() < deadline:
            all_ready = True
            for service in services:
                container = _run([*self.prefix, "ps", "-q", service])
                if not container:
                    all_ready = False
                    break
                state_raw = _run(
                    [
                        DOCKER,
                        "inspect",
                        "--format",
                        "{{json .State}}",
                        container,
                    ]
                )
                try:
                    state = json.loads(state_raw)
                except json.JSONDecodeError as exc:
                    raise RoleMigrationError(
                        f"infrastructure state is unreadable: {service}"
                    ) from exc
                health = (state.get("Health") or {}).get("Status")
                if (
                    state.get("Running") is not True
                    or health == "unhealthy"
                    or (health is not None and health != "healthy")
                ):
                    all_ready = False
                    break
            if all_ready:
                stable_since = stable_since or time.monotonic()
                if time.monotonic() - stable_since >= stable_seconds:
                    return
            else:
                stable_since = None
            time.sleep(1)
        raise RoleMigrationError(
            "infrastructure services did not retain running/healthy state: "
            + ",".join(services)
        )

    def _start_services(self, services: tuple[str, ...]) -> None:
        """Start each service with a small bounded retry for Compose races.

        Docker Compose can transiently reject a create/start request while a
        just-stopped container or its network is being reconciled.  A retry is
        safe here because every request is scoped to this exact role Compose
        project and the subsequent readiness checks remain mandatory.  A
        persistent failure still propagates unchanged and rolls the journal
        back.
        """
        for service in services:
            for attempt in range(3):
                try:
                    self._mutation_boundary()
                    _run(
                        [
                            *self.prefix,
                            "up",
                            "-d",
                            "--no-deps",
                            "--no-build",
                            "--pull",
                            "never",
                            service,
                        ],
                        timeout=180,
                    )
                    break
                except RoleMigrationError:
                    if attempt == 2:
                        raise
                    time.sleep(2)

    def restore_seed(self) -> None:
        self._mutation_boundary()
        _run(
            [
                *self.prefix,
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                "--pull",
                "never",
                self.db_service,
            ],
            timeout=180,
        )
        self._wait_db()
        system_id = self._psql("SELECT system_identifier FROM pg_control_system()")
        if system_id != str(self.context["role_inventory"]["postgres_system_id"]):
            raise RoleMigrationError("target PostgreSQL system identity drifted before restore")
        if self.role == "witness":
            if self._psql("SELECT count(*) FROM pg_tables WHERE schemaname='public'") != "0":
                raise RoleMigrationError("Witness seed target is not empty")
            return
        if self._psql("SELECT count(*) FROM pg_tables WHERE schemaname='public'") != "0":
            raise RoleMigrationError("product seed target database is not empty")
        self._mutation_boundary()
        with self._seed_source("postgres") as source:
            result = subprocess.run(
                [
                    *self.prefix, "exec", "-T", self.db_service,
                    "pg_restore", "-U", self.user, "-d", self.database,
                    "--exit-on-error", "--no-owner", "--no-acl",
                ],
                stdin=source,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
                timeout=1800,
                env=SAFE_ENV,
                pass_fds=_proc_descriptors(self.prefix),
            )
        if result.returncode != 0:
            raise RoleMigrationError("target PostgreSQL seed restore failed")
        service = ROLE_VOLUME_SERVICE[self.role]
        for kind, destination in (("uploads", "/app/uploads"), ("audit", "/app/audit_trail")):
            self._mutation_boundary()
            if _run(
                [
                    *self.prefix, "run", "--rm", "--no-deps",
                    "--pull", "never", "-T",
                    "--entrypoint", "find", service,
                    destination, "-mindepth", "1", "-print", "-quit",
                ]
            ):
                raise RoleMigrationError(f"target {kind} volume is not empty")
            self._mutation_boundary()
            with self._seed_source(kind) as source:
                result = subprocess.run(
                    [
                        *self.prefix, "run", "--rm", "--no-deps",
                        "--pull", "never", "-T",
                        "--entrypoint", "tar", service,
                        "-C", destination, "-xzf", "-",
                    ],
                    stdin=source,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=900,
                    env=SAFE_ENV,
                    pass_fds=_proc_descriptors(self.prefix),
                )
            if result.returncode != 0:
                raise RoleMigrationError(f"target {kind} seed restore failed")

    def configure_database(self) -> None:
        self._assert_database_migration_quiescent()
        if self.role == "bot_fi":
            self._compose_run("bot_fi_migration")
            self._compose_run("bot_fi_db_roles")
        elif self.role in {"webapp_fi", "webapp_ir"}:
            prefix = self.role
            self._compose_run(f"{prefix}_db_roles")
            self._compose_run(f"{prefix}_migration")
            self._compose_run(f"{prefix}_db_roles")
            self._compose_run(f"{prefix}_db_fencing")
            if self.role == "webapp_ir":
                campaign_id = self.context["verified_plan"]["campaign_id"]
                self._mutation_boundary()
                _run(
                    [
                        *self.prefix, "run", "--rm", "--no-deps",
                        "--pull", "never", "-T",
                        "webapp_ir_writer_control", "python",
                        "scripts/manage_webapp_writer.py", "fence",
                        "--expected-epoch", "1",
                        "--expected-active-site", "webapp_fi",
                        "--operator", f"staging-migration:{campaign_id}",
                        "--reason", "initialize WebApp-IR as a locally fenced standby",
                        "--apply", "--confirm", "writer:fence:webapp_ir:1:1",
                    ],
                    timeout=300,
                )
        else:
            self._compose_run("witness_role_bootstrap")
            self._compose_run("witness_migration")
        if self.role == "witness":
            if self._psql("SELECT version_num FROM writer_witness_schema_version") != "003":
                raise RoleMigrationError("Witness schema did not reach version 003")
        elif self._psql("SELECT version_num FROM alembic_version") != EXPECTED_HEAD:
            raise RoleMigrationError("product database did not reach the integration migration head")
        if self._psql("SELECT system_identifier FROM pg_control_system()") != str(
            self.context["role_inventory"]["postgres_system_id"]
        ):
            raise RoleMigrationError("PostgreSQL cluster identity changed during configuration")

    def start_private(self) -> None:
        self._start_services(ROLE_PRIVATE[self.role])
        app_services = ROLE_PRIVATE[self.role][:-1]
        infrastructure_services = ROLE_PRIVATE[self.role][-1:]
        self._wait_services_ready(app_services)
        self._wait_infrastructure_ready(infrastructure_services)

    def start_workers(self) -> None:
        if self.role == "witness":
            raise RoleMigrationError("Witness has no product worker phase")
        self._start_services(ROLE_WORKERS[self.role])
        self._wait_services_ready(ROLE_WORKERS[self.role])

    def _writer_state_snapshot(self) -> dict[str, Any]:
        query = (
            "SELECT json_build_object("
            "'active_site', active_site, 'writer_epoch', writer_epoch, "
            "'control_state', control_state, 'witness_lease_id', witness_lease_id, "
            "'witness_proof_hash', witness_proof_hash, "
            "'witness_lease_expires_at', witness_lease_expires_at, "
            "'lease_seconds_remaining', CASE WHEN witness_lease_expires_at IS NULL THEN NULL "
            "ELSE floor(extract(epoch FROM (witness_lease_expires_at - clock_timestamp())))::bigint END"
            ")::text FROM webapp_writer_state WHERE authority='webapp'"
        )
        try:
            value = json.loads(
                self._psql(query),
                object_pairs_hook=_reject_duplicate_json_pairs,
            )
        except Exception as exc:
            raise RoleMigrationError("local Writer state is unreadable") from exc
        if not isinstance(value, dict):
            raise RoleMigrationError("local Writer state is missing")
        return value

    def attest_writer_state(self) -> dict[str, Any]:
        if self.role not in {"webapp_fi", "webapp_ir"}:
            raise RoleMigrationError("writer-state attestation is valid only on a WebApp role")
        state = self._writer_state_snapshot()
        if self.role == "webapp_ir":
            if (
                state.get("active_site") is not None
                or state.get("writer_epoch") != 1
                or state.get("control_state") != "fenced"
                or state.get("witness_lease_id") is not None
            ):
                raise RoleMigrationError("WebApp-IR is not a locally fenced epoch-1 standby")
            return state

        if (
            state.get("active_site") != "webapp_fi"
            or state.get("writer_epoch") != 1
            or state.get("control_state") != "active"
        ):
            raise RoleMigrationError("initial Writer authority is not WebApp-FI epoch 1")

        # The compatibility migration creates FI/epoch-1 without a Witness
        # lease. The guarded bootstrap starts the isolated renewal loop, imports
        # the initial signed proof, and proves at least one later renewal before
        # this read-only attestation is allowed.
        remaining = state.get("lease_seconds_remaining")
        if (
            not isinstance(state.get("witness_lease_id"), str)
            or not state["witness_lease_id"]
            or type(remaining) is not int
            or remaining < 30
        ):
            raise RoleMigrationError(
                "WebApp-FI epoch 1 lacks a live imported Witness lease"
            )
        container = _run([*self.prefix, "ps", "-q", "webapp_fi_writer_control"])
        if (
            not container
            or _run(
                [
                    DOCKER,
                    "inspect",
                    "--format",
                    "{{.State.Running}}",
                    container,
                ]
            )
            != "true"
        ):
            raise RoleMigrationError(
                "WebApp-FI Writer control agent is not running"
            )
        self._wait_services_ready(
            ("webapp_fi_writer_control",),
            stable_seconds=0,
        )
        return state

    def _writer_service_config_hash(self) -> str:
        raw = _run(
            [
                *self.prefix,
                "config",
                "--hash",
                "webapp_fi_writer_control",
            ]
        )
        rows = [line.split() for line in raw.splitlines() if line.strip()]
        if (
            len(rows) != 1
            or len(rows[0]) != 2
            or rows[0][0] != "webapp_fi_writer_control"
            or re.fullmatch(r"[0-9a-f]{64}", rows[0][1]) is None
        ):
            raise RoleMigrationError(
                "WebApp-FI Writer control Compose config hash is invalid"
            )
        return rows[0][1]

    def _writer_service_container_ids(self) -> list[str]:
        return [
            value
            for value in _run(
                [
                    *self.prefix,
                    "ps",
                    "-a",
                    "-q",
                    "webapp_fi_writer_control",
                ]
            ).splitlines()
            if value
        ]

    def _attest_writer_service_container(
        self,
        container_id: str,
        *,
        expected_config_hash: str,
        require_running: bool,
    ) -> dict[str, Any]:
        try:
            inspected = json.loads(
                _run(
                    [
                        DOCKER,
                        "container",
                        "inspect",
                        container_id,
                    ]
                )
            )
        except json.JSONDecodeError as exc:
            raise RoleMigrationError(
                "WebApp-FI Writer control container is unreadable"
            ) from exc
        if (
            not isinstance(inspected, list)
            or len(inspected) != 1
            or not isinstance(inspected[0], dict)
        ):
            raise RoleMigrationError(
                "WebApp-FI Writer control container is ambiguous"
            )
        raw = inspected[0]
        config = raw.get("Config") if isinstance(raw.get("Config"), dict) else {}
        labels = (
            config.get("Labels")
            if isinstance(config.get("Labels"), dict)
            else {}
        )
        host_config = (
            raw.get("HostConfig")
            if isinstance(raw.get("HostConfig"), dict)
            else {}
        )
        state = raw.get("State") if isinstance(raw.get("State"), dict) else {}
        network_settings = (
            raw.get("NetworkSettings")
            if isinstance(raw.get("NetworkSettings"), dict)
            else {}
        )
        networks = (
            network_settings.get("Networks")
            if isinstance(network_settings.get("Networks"), dict)
            else {}
        )
        expected_image = self._runtime_service_images.get(
            "webapp_fi_writer_control"
        )
        command = config.get("Cmd")
        status = str(state.get("Status") or "")
        if (
            re.fullmatch(r"[0-9a-f]{64}", container_id) is None
            or raw.get("Id") != container_id
            or re.fullmatch(r"sha256:[0-9a-f]{64}", str(expected_image or ""))
            is None
            or raw.get("Image") != expected_image
            or config.get("Image") != expected_image
            or not self._writer_runtime_command
            or not isinstance(command, list)
            or tuple(command) != self._writer_runtime_command
            or labels.get("com.docker.compose.project") != self.project_name
            or labels.get("com.docker.compose.service")
            != "webapp_fi_writer_control"
            or labels.get("com.docker.compose.oneoff") != "False"
            or labels.get("com.docker.compose.config-hash")
            != expected_config_hash
            or host_config.get("AutoRemove") is not False
            or host_config.get("Privileged") is not False
            or host_config.get("PortBindings") not in (None, {})
            or set(map(str, networks)) != self._writer_bootstrap_networks
            or host_config.get("NetworkMode")
            not in self._writer_bootstrap_networks
            or status not in {"created", "running", "exited"}
            or (require_running and state.get("Running") is not True)
        ):
            raise RoleMigrationError(
                "WebApp-FI Writer control container differs from signed Compose"
            )
        return raw

    def _start_writer_renewal_agent(self) -> str:
        expected_config_hash = self._writer_service_config_hash()
        existing = self._writer_service_container_ids()
        if len(existing) > 1:
            raise RoleMigrationError(
                "WebApp-FI Writer control container identity is ambiguous"
            )
        if existing:
            self._attest_writer_service_container(
                existing[0],
                expected_config_hash=expected_config_hash,
                require_running=False,
            )
        self._mutation_boundary()
        _run(
            [
                *self.prefix,
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                "--no-recreate",
                "--pull",
                "never",
                "webapp_fi_writer_control",
            ],
            timeout=180,
        )
        observed = self._writer_service_container_ids()
        if len(observed) != 1:
            raise RoleMigrationError(
                "WebApp-FI Writer control agent did not start exactly once"
            )
        container = observed[0]
        self._attest_writer_service_container(
            container,
            expected_config_hash=expected_config_hash,
            require_running=True,
        )
        self._wait_services_ready(
            ("webapp_fi_writer_control",),
            stable_seconds=0,
        )
        self._attest_writer_service_container(
            container,
            expected_config_hash=expected_config_hash,
            require_running=True,
        )
        return container

    def _prove_writer_renewal(
        self,
        *,
        initial_proof_hash: str,
        container: str,
    ) -> dict[str, Any]:
        for _attempt in range(45):
            if (
                not container
                or _run(
                    [DOCKER, "inspect", "--format", "{{.State.Running}}", container]
                ) != "true"
            ):
                raise RoleMigrationError("WebApp-FI Writer control agent is not running")
            renewed = self._writer_state_snapshot()
            if (
                isinstance(renewed.get("witness_proof_hash"), str)
                and renewed["witness_proof_hash"]
                and renewed["witness_proof_hash"] != initial_proof_hash
                and type(renewed.get("lease_seconds_remaining")) is int
                and renewed["lease_seconds_remaining"] >= 30
            ):
                return renewed
            time.sleep(1)
        raise RoleMigrationError(
            "WebApp-FI Writer control agent did not prove a live Witness renewal"
        )

    def bootstrap_writer_lease(
        self,
        *,
        request_id: str,
        retrying: bool = False,
    ) -> dict[str, Any]:
        if self.role != "webapp_fi":
            raise RoleMigrationError(
                "initial Writer lease bootstrap is WebApp-FI-only"
            )
        campaign_id = self.context["verified_plan"]["campaign_id"]
        release_sha = self.context["verified_plan"]["release_sha"]
        required = (
            f"bootstrap-writer:{campaign_id}:{request_id}:{release_sha}"
        )
        bootstrap_command = [
            "python",
            "scripts/bootstrap_three_site_staging_writer_lease.py",
            "--campaign-id",
            campaign_id,
            "--request-id",
            request_id,
            "--expected-release-sha",
            release_sha,
            "--apply",
            "--confirm",
            required,
        ]
        container_name = (
            "ts-writer-bootstrap-"
            + hashlib.sha256(
                f"{campaign_id}:{request_id}".encode("ascii")
            ).hexdigest()[:24]
        )
        if (
            re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", container_name)
            is None
        ):
            raise RoleMigrationError(
                "Writer lease bootstrap container identity is invalid"
            )

        def listed_container_ids() -> list[str]:
            return [
                value
                for value in _run(
                    [
                        DOCKER,
                        "ps",
                        "-a",
                        "--no-trunc",
                        "--filter",
                        f"name=^/{container_name}$",
                        "--format",
                        "{{.ID}}",
                    ]
                ).splitlines()
                if value
            ]

        def inspected_container(container_id: str) -> dict[str, Any]:
            try:
                inspected = json.loads(
                    _run(
                        [
                            DOCKER,
                            "container",
                            "inspect",
                            container_id,
                        ]
                    )
                )
            except json.JSONDecodeError as exc:
                raise RoleMigrationError(
                    "Writer lease bootstrap container residue is unreadable"
                ) from exc
            if (
                not isinstance(inspected, list)
                or len(inspected) != 1
                or not isinstance(inspected[0], dict)
            ):
                raise RoleMigrationError(
                    "Writer lease bootstrap container residue is ambiguous"
                )
            return inspected[0]

        def verified_owned_container(
            raw_container: dict[str, Any],
            *,
            container_id: str,
        ) -> str:
            config = (
                raw_container.get("Config")
                if isinstance(raw_container.get("Config"), dict)
                else {}
            )
            labels = (
                config.get("Labels")
                if isinstance(config.get("Labels"), dict)
                else {}
            )
            host_config = (
                raw_container.get("HostConfig")
                if isinstance(raw_container.get("HostConfig"), dict)
                else {}
            )
            state = (
                raw_container.get("State")
                if isinstance(raw_container.get("State"), dict)
                else {}
            )
            network_settings = (
                raw_container.get("NetworkSettings")
                if isinstance(raw_container.get("NetworkSettings"), dict)
                else {}
            )
            networks = (
                network_settings.get("Networks")
                if isinstance(network_settings.get("Networks"), dict)
                else {}
            )
            observed_networks = set(map(str, networks))
            expected_image = self._runtime_service_images.get(
                "webapp_fi_writer_control"
            )
            status = str(state.get("Status") or "")
            if (
                re.fullmatch(r"[0-9a-f]{64}", container_id) is None
                or raw_container.get("Id") != container_id
                or raw_container.get("Name") != f"/{container_name}"
                or raw_container.get("Image") != expected_image
                or config.get("Image") != expected_image
                or config.get("Cmd") != bootstrap_command
                or labels.get("com.docker.compose.project")
                != self.project_name
                or labels.get("com.docker.compose.service")
                != "webapp_fi_writer_control"
                or labels.get("com.docker.compose.oneoff") != "True"
                or host_config.get("AutoRemove") is not True
                or host_config.get("Privileged") is not False
                or host_config.get("PortBindings") not in (None, {})
                or host_config.get("NetworkMode")
                not in self._writer_bootstrap_networks
                or not observed_networks
                or not observed_networks.issubset(
                    self._writer_bootstrap_networks
                )
                or status
                not in {
                    "created",
                    "running",
                    "exited",
                    "dead",
                }
            ):
                raise RoleMigrationError(
                    "foreign container occupies Writer bootstrap identity"
                )
            return status

        self._mutation_boundary()
        existing = listed_container_ids()
        if existing:
            if len(existing) != 1:
                raise RoleMigrationError(
                    "Writer lease bootstrap container identity is ambiguous"
                )
            status = verified_owned_container(
                inspected_container(existing[0]),
                container_id=existing[0],
            )
            if status == "running":
                for _attempt in range(30):
                    time.sleep(1)
                    observed = listed_container_ids()
                    if not observed:
                        break
                    if observed != existing:
                        raise RoleMigrationError(
                            "Writer bootstrap container identity changed "
                            "while waiting"
                        )
                else:
                    raise RoleMigrationError(
                        "verified Writer bootstrap container remains running; "
                        "wait for its bounded operation before retry"
                    )
            else:
                self._mutation_boundary()
                confirmed = inspected_container(existing[0])
                confirmed_status = verified_owned_container(
                    confirmed,
                    container_id=existing[0],
                )
                if confirmed_status == "running":
                    raise RoleMigrationError(
                        "Writer bootstrap residue started during reconciliation"
                    )
                _run(
                    [
                        DOCKER,
                        "rm",
                        "-f",
                        "-v",
                        existing[0],
                    ]
                )
                if listed_container_ids():
                    raise RoleMigrationError(
                        "Writer bootstrap residue removal was not exact"
                    )
        # Start the independently supervised renewal loop before acquiring the
        # first lease. It safely waits while no local lease exists, and closes
        # the crash window between the one-off import and durable phase commit.
        renewal_container = self._start_writer_renewal_agent()
        if retrying:
            current = self._writer_state_snapshot()
            current_hash = current.get("witness_proof_hash")
            remaining = current.get("lease_seconds_remaining")
            lease_id = current.get("witness_lease_id")
            if lease_id is not None:
                if (
                    current.get("active_site") != "webapp_fi"
                    or current.get("writer_epoch") != 1
                    or current.get("control_state") != "active"
                    or not isinstance(lease_id, str)
                    or not lease_id
                    or not isinstance(current_hash, str)
                    or re.fullmatch(r"[0-9a-f]{64}", current_hash) is None
                    or type(remaining) is not int
                    or remaining < 30
                ):
                    raise RoleMigrationError(
                        "interrupted Writer bootstrap has an unusable local lease"
                    )
                return self._prove_writer_renewal(
                    initial_proof_hash=current_hash,
                    container=renewal_container,
                )
            if (
                current.get("active_site") != "webapp_fi"
                or current.get("writer_epoch") != 1
                or current.get("control_state") != "active"
                or current_hash is not None
                or current.get("witness_lease_expires_at") is not None
            ):
                raise RoleMigrationError(
                    "interrupted Writer bootstrap has partial local lease state"
                )
        raw = _run(
            [
                *self.prefix,
                "run",
                "--rm",
                "--no-deps",
                "--pull",
                "never",
                "--name",
                container_name,
                "-T",
                "webapp_fi_writer_control",
                *bootstrap_command,
            ],
            timeout=300,
        )
        try:
            result = json.loads(
                raw,
                object_pairs_hook=_reject_duplicate_json_pairs,
            )
            expires_at = datetime.fromisoformat(
                str(result["expires_at"]).replace("Z", "+00:00")
            )
        except Exception as exc:
            raise RoleMigrationError(
                "initial Writer lease bootstrap receipt is invalid"
            ) from exc
        if (
            not isinstance(result, dict)
            or set(result)
            != {
                "status",
                "campaign_id",
                "request_id",
                "release_sha",
                "holder_site",
                "writer_epoch",
                "lease_id",
                "witness_transition_id",
                "proof_hash",
                "expires_at",
            }
            or result["status"] != "initialized"
            or result["campaign_id"] != campaign_id
            or result["request_id"] != request_id
            or result["release_sha"] != release_sha
            or result["holder_site"] != "webapp_fi"
            or result["writer_epoch"] != 1
            or not isinstance(result["lease_id"], str)
            or not result["lease_id"]
            or not isinstance(result["witness_transition_id"], str)
            or not result["witness_transition_id"]
            or re.fullmatch(r"[0-9a-f]{64}", str(result["proof_hash"])) is None
            or expires_at.tzinfo is None
            or expires_at.astimezone(timezone.utc)
            <= datetime.now(timezone.utc) + timedelta(seconds=15)
        ):
            raise RoleMigrationError(
                "initial Writer lease bootstrap receipt is invalid"
            )
        return self._prove_writer_renewal(
            initial_proof_hash=result["proof_hash"],
            container=renewal_container,
        )

    def start_public(self) -> None:
        if self.role == "witness":
            raise RoleMigrationError("Witness has no public application phase")
        self._start_services(ROLE_PUBLIC[self.role])
        # Redis is deliberately consumed from the upstream immutable image rather
        # than from the application release image, so it cannot carry the exact
        # RELEASE_SHA that _wait_services_ready verifies.  Keep the release
        # identity check on all public application services, while checking the
        # Redis infrastructure service only for running/healthy state.  This is
        # the same split already used by start_private().
        app_services = ROLE_PUBLIC[self.role][1:]
        infrastructure_services = ROLE_PUBLIC[self.role][:1]
        self._wait_services_ready(app_services)
        self._wait_infrastructure_ready(infrastructure_services)

    def rollback_stop(self) -> None:
        # Preserve every target byte for forensics; rollback of user access is
        # performed by restoring the independently frozen legacy source.
        self._mutation_boundary(images=False)
        _run([*self.prefix, "stop", "--timeout", "30"], timeout=300)


def _evidence(
    path: Path,
    *,
    schema: str,
    context: dict[str, Any],
    role: str,
    journal_state_sha256: str,
) -> tuple[dict, str]:
    value = _secure_json(path, label=schema)
    schema_extra = {
        "three-site-staging-private-barrier-v1": {"campaign_journals_sha256"},
        "three-site-staging-routing-hold-v1": {
            "campaign_journals_sha256", "routing_observation_sha256",
        },
        "three-site-staging-role-acceptance-v1": {
            "campaign_journals_sha256", "acceptance_observation_sha256",
        },
    }
    common = {
        "schema", "status", "campaign_id", "release_sha", "plan_sha256",
        "role", "issued_at", "role_journal_state_sha256",
    }
    try:
        issued_at = datetime.fromisoformat(str(value["issued_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise RoleMigrationError(f"{schema} issued_at is invalid") from exc
    if (
        schema not in schema_extra
        or set(value) != common | schema_extra[schema]
        or value.get("schema") != schema
        or value.get("status") != "passed"
        or value.get("campaign_id") != context["verified_plan"]["campaign_id"]
        or value.get("release_sha") != context["verified_plan"]["release_sha"]
        or value.get("plan_sha256") != context["verified_plan"]["plan_sha256"]
        or value.get("role") != role
        or value.get("role_journal_state_sha256") != journal_state_sha256
        or re.fullmatch(r"[0-9a-f]{64}", str(value.get("campaign_journals_sha256", ""))) is None
        or any(
            re.fullmatch(r"[0-9a-f]{64}", str(value.get(field, ""))) is None
            for field in schema_extra[schema] - {"campaign_journals_sha256"}
        )
        or issued_at.tzinfo is None
        or datetime.now(timezone.utc) - issued_at.astimezone(timezone.utc) > timedelta(minutes=15)
        or issued_at.astimezone(timezone.utc) > datetime.now(timezone.utc) + timedelta(minutes=2)
    ):
        raise RoleMigrationError(f"{schema} identity/status is invalid")
    return value, hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _phase_for_action(role: str, action: str) -> str:
    fixed = {
        "restore-seed": "empty_seed_verified" if role == "witness" else "seed_restored",
        "configure-database": "database_configured",
        "start-private": "private_ready",
        "bootstrap-writer-lease": "writer_lease_bootstrapped",
        "start-workers": "workers_ready",
        "start-public": "public_ready",
        "accept": "accepted",
    }
    if action == "attest-writer-state":
        return "writer_initialized" if role == "webapp_fi" else "standby_fenced"
    try:
        return fixed[action]
    except KeyError as exc:
        raise RoleMigrationError("action has no migration phase") from exc


def apply_action(
    *,
    action: str,
    journal: MigrationJournal,
    backend: LocalRoleBackend,
    context: dict[str, Any],
    evidence_path: Path | None,
    expected_checkpoint_sha256: str | None = None,
    writer_lease_request_id: str | None = None,
) -> dict[str, Any]:
    arguments = {
        "action": action,
        "journal": journal,
        "backend": backend,
        "context": context,
        "evidence_path": evidence_path,
        "expected_checkpoint_sha256": expected_checkpoint_sha256,
        "writer_lease_request_id": writer_lease_request_id,
    }
    if action == "bootstrap-writer-lease":
        with journal.writer_lease_execution_lock():
            return _apply_action_locked(**arguments)
    return _apply_action_locked(**arguments)


def _apply_action_locked(
    *,
    action: str,
    journal: MigrationJournal,
    backend: LocalRoleBackend,
    context: dict[str, Any],
    evidence_path: Path | None,
    expected_checkpoint_sha256: str | None = None,
    writer_lease_request_id: str | None = None,
) -> dict[str, Any]:
    role = backend.role
    phase = _phase_for_action(role, action)
    if phase not in ROLE_PHASES[role]:
        raise RoleMigrationError(f"action {action} is not valid for role {role}")
    if action == "bootstrap-writer-lease":
        writer_lease_request_id = _canonical_writer_lease_request_id(
            writer_lease_request_id
        )
        if role != "webapp_fi":
            raise RoleMigrationError(
                "initial Writer lease bootstrap is WebApp-FI-only"
            )
    elif writer_lease_request_id is not None:
        raise RoleMigrationError(
            "Writer lease request id is valid only for bootstrap-writer-lease"
        )
    journal_state = journal.load()
    retrying_writer_bootstrap = (
        action == "bootstrap-writer-lease"
        and journal_state.get("status") == "rollback_required"
        and journal_state.get("started_phase")
        == "writer_lease_bootstrapped"
    )
    if (
        expected_checkpoint_sha256 is not None
        and journal_state["state_sha256"] != expected_checkpoint_sha256
    ):
        raise RoleMigrationError(
            "role journal changed after migration resume approval"
        )
    controller_evidence_hash = None
    if action in {"start-workers", "start-public", "accept"}:
        if evidence_path is None:
            raise RoleMigrationError(f"{action} requires campaign-controller evidence")
        schema = {
            "start-workers": "three-site-staging-private-barrier-v1",
            "start-public": "three-site-staging-routing-hold-v1",
            "accept": "three-site-staging-role-acceptance-v1",
        }[action]
        _value, controller_evidence_hash = _evidence(
            evidence_path,
            schema=schema,
            context=context,
            role=role,
            journal_state_sha256=journal_state["state_sha256"],
        )
    release_sha = context["verified_plan"]["release_sha"]
    _verify_exact_release(release_sha)
    if action == "bootstrap-writer-lease":
        journal.begin_writer_lease_bootstrap(
            request_id=str(writer_lease_request_id),
        )
    else:
        journal.begin_phase(phase)
    try:
        _verify_exact_release(release_sha)
        evidence_hash = None
        if action == "restore-seed":
            backend.restore_seed()
        elif action == "configure-database":
            backend.configure_database()
        elif action == "start-private":
            backend.start_private()
        elif action == "bootstrap-writer-lease":
            backend.bootstrap_writer_lease(
                request_id=str(writer_lease_request_id),
                retrying=retrying_writer_bootstrap,
            )
        elif action == "start-workers":
            backend.start_workers()
        elif action == "start-public":
            backend.start_public()
        elif action == "attest-writer-state":
            evidence = backend.attest_writer_state()
            expected = "active" if role == "webapp_fi" else "standby"
            observed = (
                "active"
                if evidence.get("active_site") == role
                and evidence.get("control_state") == "active"
                else "standby"
            )
            expected_site = "webapp_fi" if role == "webapp_fi" else None
            if observed != expected or evidence.get("active_site") != expected_site:
                raise RoleMigrationError("initial WebApp Writer/standby state is incorrect")
        elif action == "accept":
            evidence_hash = controller_evidence_hash
        _verify_exact_release(release_sha)
        journal.complete_phase(phase)
        if action == "accept":
            _verify_exact_release(release_sha)
            return journal.commit(acceptance_evidence_sha256=str(evidence_hash))
        return journal.load()
    except Exception as exc:
        try:
            _verify_exact_release(release_sha)
            journal.require_rollback(type(exc).__name__)
        except Exception:
            pass
        raise


def _canonical_writer_lease_request_id(value: str | None) -> str:
    try:
        normalized = str(UUID(str(value)))
    except ValueError as exc:
        raise RoleMigrationError(
            "bootstrap-writer-lease requires one canonical request UUID"
        ) from exc
    if value != normalized:
        raise RoleMigrationError(
            "bootstrap-writer-lease request UUID is not canonical"
        )
    return normalized


def migration_resume_subject(
    state: dict[str, Any],
    *,
    action: str,
    writer_lease_request_id: str | None = None,
) -> dict[str, Any]:
    try:
        validate_migration_journal(state)
        role = str(state["role"])
        next_index = len(state["completed_phases"])
        exact_next_phase = ROLE_PHASES[role][next_index]
        requested_phase = _phase_for_action(role, action)
    except Exception as exc:
        raise RoleMigrationError(
            "migration resume checkpoint/action is invalid"
        ) from exc
    retrying_writer_bootstrap = (
        action == "bootstrap-writer-lease"
        and state.get("status") == "rollback_required"
        and state.get("started_phase") == "writer_lease_bootstrapped"
    )
    if action == "bootstrap-writer-lease":
        writer_lease_request_id = _canonical_writer_lease_request_id(
            writer_lease_request_id
        )
        persisted_request_id = state.get("writer_lease_request_id")
        if (
            persisted_request_id is not None
            and persisted_request_id != writer_lease_request_id
        ):
            raise RoleMigrationError(
                "migration resume Writer lease request id differs from journal"
            )
    elif writer_lease_request_id is not None:
        raise RoleMigrationError(
            "migration resume Writer lease request id is action-mismatched"
        )
    if (
        not (
            (
                state.get("status") == "active"
                and state.get("started_phase") is None
            )
            or retrying_writer_bootstrap
        )
        or requested_phase != exact_next_phase
        or action in {"plan", "begin", "status", "finish", "rollback"}
        or action not in {
            "restore-seed",
            "configure-database",
            "start-private",
            "bootstrap-writer-lease",
            "attest-writer-state",
            "start-workers",
            "start-public",
            "accept",
        }
        or re.fullmatch(r"[0-9a-f]{64}", str(state.get("plan_sha256", ""))) is None
    ):
        raise RoleMigrationError("migration resume checkpoint/action is invalid")
    try:
        bindings = {
            "campaign_id": state["campaign_id"],
            "plan_sha256": state["plan_sha256"],
            "role": state["role"],
            "next_action": action,
            "checkpoint_state_sha256": state["state_sha256"],
        }
        if writer_lease_request_id is not None:
            bindings["writer_lease_request_id"] = writer_lease_request_id
        return approval_subject(
            artifact_type="three-site-staging-migration-resume-v1",
            artifact_sha256=str(state["state_sha256"]),
            release_sha=str(state["release_sha"]),
            bindings=bindings,
        )
    except Exception as exc:
        raise RoleMigrationError(
            "migration resume checkpoint cannot form an approval subject"
        ) from exc


def _verify_resume_approval(
    token: dict[str, Any],
    *,
    approval_policy: dict[str, Any],
    state: dict[str, Any],
    action: str,
    writer_lease_request_id: str | None = None,
) -> dict[str, Any]:
    subject = migration_resume_subject(
        state,
        action=action,
        writer_lease_request_id=writer_lease_request_id,
    )
    try:
        verified = verify_human_approval(
            token,
            policy_payload=approval_policy,
            expected_action="approve_migration_resume",
            expected_environment="staging",
            expected_subject=subject,
            require_fresh=True,
            allow_session=False,
        )
    except Exception as exc:
        raise RoleMigrationError(
            "fresh direct migration resume approval is invalid"
        ) from exc
    return {
        "approval_id": verified.approval_id,
        "operator": verified.operator,
        "expires_at": verified.expires_at.isoformat(),
        "checkpoint_state_sha256": state["state_sha256"],
        "next_action": action,
    }


def confirmation_phrase(
    campaign_id: str,
    role: str,
    action: str,
    plan_hash: str,
    *,
    writer_lease_request_id: str | None = None,
) -> str:
    base = f"migrate-role:{campaign_id}:{role}:{action}:{plan_hash}"
    if writer_lease_request_id is not None:
        return f"{base}:{writer_lease_request_id}"
    return base


def _require_forward_inputs(args: argparse.Namespace) -> None:
    required = {
        "canonical-compose": args.canonical_compose,
        "role-compose": args.role_compose,
        "env-file": args.env_file,
        "host-snapshot": args.host_snapshot,
        "target-seed": args.target_seed,
        "inventory": args.inventory,
        "inventory-approval": args.inventory_approval,
        "approval-policy": args.approval_policy,
        "plan": args.plan,
        "plan-approval": args.plan_approval,
        "freeze-evidence": args.freeze_evidence,
        "backup-manifest": args.backup_manifest,
        "seed-manifest": args.seed_manifest,
        "image-inventory": args.image_inventory,
    }
    missing = sorted(name for name, value in required.items() if not value)
    if missing:
        raise RoleMigrationError(
            "forward role migration is missing required inputs: " + ", ".join(missing)
        )


def _require_rollback_inputs(args: argparse.Namespace) -> None:
    missing = sorted(
        name
        for name, value in {
            "role-compose": args.role_compose,
            "env-file": args.env_file,
        }.items()
        if value is None
    )
    if missing:
        raise RoleMigrationError(
            "role rollback is missing required inputs: " + ", ".join(missing)
        )


def _verify_global_commit(path: Path, *, role: str, state: dict[str, Any]) -> dict[str, Any]:
    value = _secure_json(path, label="global migration commit evidence")
    fields = {
        "schema", "status", "campaign_id", "release_sha", "plan_sha256",
        "issued_at", "campaign_journals_sha256", "role_journals",
        "committed_role_states", "all_roles_committed",
    }
    role_journals = value.get("role_journals")
    if (
        set(value) != fields
        or value.get("schema") != "three-site-staging-global-commit-v2"
        or value.get("status") != "passed"
        or value.get("campaign_id") != state["campaign_id"]
        or value.get("release_sha") != state["release_sha"]
        or value.get("plan_sha256") != state["plan_sha256"]
        or value.get("all_roles_committed") is not True
        or not isinstance(role_journals, dict)
        or set(role_journals) != set(ROLE_PHASES)
        or role_journals.get(role) != state["state_sha256"]
        or not isinstance(value.get("committed_role_states"), dict)
        or set(value["committed_role_states"]) != set(ROLE_PHASES)
        or value["committed_role_states"].get(role) != state
    ):
        raise RoleMigrationError("global migration commit evidence is invalid")
    expected_campaign_hash = hashlib.sha256(
        json.dumps(role_journals, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if value["campaign_journals_sha256"] != expected_campaign_hash:
        raise RoleMigrationError("global migration commit journal hash is invalid")
    for state_role, committed_state in value["committed_role_states"].items():
        try:
            validate_migration_journal(committed_state)
        except Exception as exc:
            raise RoleMigrationError("global commit embeds an invalid journal state") from exc
        if (
            committed_state.get("role") != state_role
            or committed_state.get("campaign_id") != state["campaign_id"]
            or committed_state.get("release_sha") != state["release_sha"]
            or committed_state.get("plan_sha256") != state["plan_sha256"]
            or committed_state.get("status") != "committed"
            or committed_state.get("completed_phases")
            != list(ROLE_PHASES[state_role])
            or re.fullmatch(
                r"[0-9a-f]{64}",
                str(committed_state.get("acceptance_evidence_sha256") or ""),
            )
            is None
            or committed_state.get("state_sha256") != role_journals[state_role]
        ):
            raise RoleMigrationError("global commit journal state/hash is invalid")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=(
        "plan", "begin", "status", "resume-subject",
        "restore-seed", "configure-database",
        "start-private", "bootstrap-writer-lease", "attest-writer-state",
        "start-workers", "start-public",
        "accept", "finish", "rollback",
    ))
    parser.add_argument("--role", choices=tuple(ROLE_PHASES), required=True)
    parser.add_argument("--canonical-compose", type=Path)
    parser.add_argument("--role-compose", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--host-snapshot", type=Path)
    parser.add_argument("--target-seed", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--inventory-approval", type=Path)
    parser.add_argument("--approval-policy", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-approval", type=Path)
    parser.add_argument("--resume-approval", type=Path)
    parser.add_argument(
        "--next-action",
        choices=(
            "restore-seed",
            "configure-database",
            "start-private",
            "bootstrap-writer-lease",
            "attest-writer-state",
            "start-workers",
            "start-public",
            "accept",
        ),
    )
    parser.add_argument("--subject-output", type=Path)
    parser.add_argument("--writer-lease-request-id")
    parser.add_argument("--freeze-evidence", action="append", type=Path, default=[])
    parser.add_argument("--backup-manifest", action="append", default=[])
    parser.add_argument("--seed-manifest", action="append", default=[])
    parser.add_argument("--image-inventory", action="append", default=[])
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    backend: LocalRoleBackend | None = None
    try:
        journal = MigrationJournal(args.journal)
        if args.action == "status":
            result = journal.load()
        elif args.action == "resume-subject":
            state = journal.load()
            if state["role"] != args.role:
                raise RoleMigrationError(
                    "resume-subject role differs from durable journal"
                )
            if args.next_action is None or args.subject_output is None:
                raise RoleMigrationError(
                    "resume-subject requires --next-action and --subject-output"
                )
            if (
                args.next_action != "bootstrap-writer-lease"
                and args.writer_lease_request_id is not None
            ):
                raise RoleMigrationError(
                    "--writer-lease-request-id is bootstrap-only"
                )
            _verify_exact_release(state["release_sha"])
            subject = migration_resume_subject(
                state,
                action=args.next_action,
                writer_lease_request_id=(
                    _canonical_writer_lease_request_id(
                        args.writer_lease_request_id
                    )
                    if args.next_action == "bootstrap-writer-lease"
                    else None
                ),
            )
            write_secure_new_bytes(
                args.subject_output,
                (json.dumps(subject, sort_keys=True, indent=2) + "\n").encode(),
                label="migration resume approval subject",
                mode=0o600,
                max_size=1024 * 1024,
            )
            result = {
                "status": "subject-ready",
                "action": "approve_migration_resume",
                "role": args.role,
                "next_action": args.next_action,
                "checkpoint_state_sha256": state["state_sha256"],
                **(
                    {
                        "writer_lease_request_id":
                        args.writer_lease_request_id
                    }
                    if args.next_action == "bootstrap-writer-lease"
                    else {}
                ),
                "output": str(args.subject_output),
            }
        elif args.action == "finish":
            state = journal.load()
            if state["role"] != args.role:
                raise RoleMigrationError("finish role differs from durable journal")
            _verify_exact_release(state["release_sha"])
            if args.evidence is None:
                raise RoleMigrationError("finish requires global commit evidence")
            _verify_global_commit(args.evidence, role=args.role, state=state)
            _verify_exact_release(state["release_sha"])
            result = journal.finish()
        elif args.action == "rollback":
            _require_rollback_inputs(args)
            # Rollback remains possible after plan expiry. The exact Compose
            # bytes are still checked and all target data is retained.
            state = journal.load()
            if state["role"] != args.role:
                raise RoleMigrationError("rollback role differs from durable journal")
            _verify_exact_release(state["release_sha"])
            role_compose_bytes = _read_root_file(
                args.role_compose,
                label="rollback role Compose bundle",
                expected_mode=0o640,
            )
            env_bytes = _read_root_file(
                args.env_file,
                label="rollback role environment bundle",
                expected_mode=0o600,
            )
            if (
                hashlib.sha256(role_compose_bytes).hexdigest()
                != state["role_compose_sha256"]
                or hashlib.sha256(env_bytes).hexdigest() != state["role_env_sha256"]
            ):
                raise RoleMigrationError("rollback role bundle differs from the durable journal")
            required = confirmation_phrase(state["campaign_id"], args.role, args.action, state["plan_sha256"])
            if not args.apply:
                result = {"status": "planned", "required_confirmation": required}
            else:
                if args.confirm != required:
                    raise RoleMigrationError("role rollback confirmation mismatch")
                backend = LocalRoleBackend(
                    args,
                    {
                        "env": {
                            line.partition("=")[0]: line.partition("=")[2]
                            for line in env_bytes.decode("utf-8").splitlines()
                            if line and not line.startswith("#") and "=" in line
                        },
                        "env_bytes": env_bytes,
                        "role_compose_bytes": role_compose_bytes,
                        "role_inventory": {},
                        "verified_plan": {
                            "campaign_id": state["campaign_id"],
                            "release_sha": state["release_sha"],
                            "plan_sha256": state["plan_sha256"],
                        },
                    },
                )
                backend.rollback_stop()
                _verify_exact_release(state["release_sha"])
                result = journal.complete_rollback()
        else:
            _require_forward_inputs(args)
            writer_lease_request_id = (
                _canonical_writer_lease_request_id(
                    args.writer_lease_request_id
                )
                if args.action == "bootstrap-writer-lease"
                else None
            )
            if (
                args.action != "bootstrap-writer-lease"
                and args.writer_lease_request_id is not None
            ):
                raise RoleMigrationError(
                    "--writer-lease-request-id is bootstrap-only"
                )
            resume_state: dict[str, Any] | None = None
            if args.resume_approval is not None:
                if args.action in {"plan", "begin"}:
                    raise RoleMigrationError(
                        "migration resume approval cannot authorize a new migration"
                    )
                resume_state = journal.load()
            context = verify_inputs(
                args,
                allow_expired_plan=resume_state is not None,
            )
            verified = context["verified_plan"]
            if args.action not in {"plan", "begin"}:
                state = resume_state or journal.load()
                if (
                    state["campaign_id"] != verified["campaign_id"]
                    or state["release_sha"] != verified["release_sha"]
                    or state["plan_sha256"] != verified["plan_sha256"]
                    or state["role"] != args.role
                ):
                    raise RoleMigrationError(
                        "role journal differs from current signed campaign"
                    )
                if verified["plan_expired"] and resume_state is None:
                    raise RoleMigrationError(
                        "expired migration plan requires checkpoint approval"
                    )
                if resume_state is not None:
                    _verify_resume_approval(
                        _secure_json(
                            args.resume_approval,
                            label="migration resume approval",
                        ),
                        approval_policy=context["approval_policy"],
                        state=resume_state,
                        action=args.action,
                        writer_lease_request_id=writer_lease_request_id,
                    )
            required = confirmation_phrase(
                verified["campaign_id"],
                args.role,
                args.action,
                verified["plan_sha256"],
                writer_lease_request_id=writer_lease_request_id,
            )
            if args.action == "plan" or not args.apply:
                result = {
                    "status": "planned",
                    "action": args.action,
                    "role": args.role,
                    "next_phase": (
                        ROLE_PHASES[args.role][0]
                        if args.action in {"plan", "begin"}
                        else _phase_for_action(args.role, args.action)
                    ),
                    "required_confirmation": required,
                }
            else:
                if args.confirm != required:
                    raise RoleMigrationError("role migration confirmation mismatch")
                if args.action == "begin":
                    _verify_exact_release(verified["release_sha"])
                    result = journal.create(
                        campaign_id=verified["campaign_id"],
                        release_sha=verified["release_sha"],
                        plan_sha256=verified["plan_sha256"],
                        role=args.role,
                        role_compose_sha256=context["bundle"]["compose_sha256"],
                        role_env_sha256=context["bundle"]["environment_sha256"],
                        image_inventory_sha256=verified["image_inventory_sha256"][args.role],
                    )
                else:
                    backend = LocalRoleBackend(args, context)
                    result = apply_action(
                        action=args.action,
                        journal=journal,
                        backend=backend,
                        context=context,
                        evidence_path=args.evidence,
                        expected_checkpoint_sha256=(
                            None
                            if resume_state is None
                            else resume_state["state_sha256"]
                        ),
                        writer_lease_request_id=writer_lease_request_id,
                    )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    finally:
        if backend is not None:
            backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
