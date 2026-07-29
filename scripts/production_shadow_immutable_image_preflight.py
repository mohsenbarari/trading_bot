#!/usr/bin/env python3
"""Pure contract for a local-only immutable app-image dependency preflight.

This module intentionally does not open files, invoke Docker, or start a
container.  A later privileged executor may use the bounded argv and receipt
validators here, but must supply its own local image/container inspection
results.  The probe has no application mounts, environment, or network.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Callable, Mapping
from uuid import UUID

from core.docker_image_identity import DockerImageIdentityError, image_content_descriptor
from core.secure_file_io import SecureFileError, read_secure_bytes, write_secure_new_bytes
from scripts.production_shadow_convergence_runtime_targets import (
    ConvergenceRuntimeTargetBindingError,
    validate_observer_runtime_target_binding,
)


PLAN_SCHEMA = "production-shadow-immutable-image-dependency-preflight-plan-v1"
OUTPUT_SCHEMA = "production-shadow-immutable-image-dependency-preflight-output-v1"
RECEIPT_SCHEMA = "production-shadow-immutable-image-dependency-preflight-receipt-v1"
MATERIAL_SCHEMA = "production-shadow-immutable-image-dependency-preflight-material-v1"
RECEIPT_VERIFICATION_SCHEMA = (
    "production-shadow-immutable-image-dependency-preflight-receipt-verification-v1"
)
ROLES = ("bot_fi", "webapp_fi", "webapp_ir")
RUNTIME_IMAGE_KINDS = ("app", "postgres", "redis", "nginx")
DOCKER = "/usr/bin/docker"
PROBE_PURPOSE = "immutable-image-dependency-preflight"
PROBE_USER = "65534:65534"
PROBE_MEMORY_BYTES = 256 * 1024 * 1024
PROBE_NANO_CPUS = 250_000_000
PROBE_PIDS_LIMIT = 32
PROBE_TMPFS = "/tmp:rw,noexec,nosuid,size=16m"
TIMEOUT_SECONDS = 30
MAX_STDOUT_BYTES = 8 * 1024
MAX_STDERR_BYTES = 8 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
ZERO_SHA256 = "0" * 64
INSTALL_ROOT = Path("/root/secure-envs/trading-bot")
INSTALL_DIRECTORY = "immutable-image-preflight"
INSTALL_FILE_MODE = 0o600
INSTALL_DIRECTORY_MODE = 0o700
MAX_INSTALL_BYTES = 256 * 1024
MAX_INSPECT_BYTES = 256 * 1024
EXPECTED_DEPENDENCIES = {
    "asyncpg": "0.29.0",
    "pydantic_settings": "2.3.4",
    "sqlalchemy": "2.0.31",
}
TRUSTED_SYSTEM_PACKAGE_ROOTS = (
    "/usr/local/lib/python3.11/dist-packages",
    "/usr/local/lib/python3.11/site-packages",
    "/usr/lib/python3/dist-packages",
)

PLAN_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "manifest_sha256",
        "role",
        "runtime_target_binding_sha256",
        "app_image_id",
        "container_name",
        "labels",
        "image_inspect_argv",
        "create_argv",
        "container_inspect_argv",
        "start_argv",
        "remove_argv",
        "container_residue_argv",
        "volume_residue_argv",
        "network_residue_argv",
        "timeout_seconds",
        "max_stdout_bytes",
        "max_stderr_bytes",
        "network_forbidden",
        "mounts_forbidden",
        "production_mutation_forbidden",
        "object_storage_contact_forbidden",
        "plan_sha256",
    }
)
OUTPUT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "python_major",
        "python_minor",
        "isolated",
        "no_site",
        "safe_path",
        "dependency_versions",
        "installed_roots",
    }
)
CONTAINER_PROOF_FIELDS = frozenset(
    {
        "container_id_sha256",
        "image_id",
        "network_mode",
        "read_only",
        "unprivileged_user",
        "cap_drop_all",
        "no_new_privileges",
        "mount_count",
        "ports_published",
        "privileged",
        "restart_policy",
        "auto_remove",
        "tmpfs",
    }
)
RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "manifest_sha256",
        "role",
        "runtime_target_binding_sha256",
        "plan_sha256",
        "app_image_id",
        "image_content_identity",
        "container_proof",
        "dependency_versions",
        "probe_stdout_sha256",
        "probe_stdout_bytes",
        "stderr_bytes",
        "exit_code",
        "started_at",
        "finished_at",
        "duration_ms",
        "zero_residue",
        "network_forbidden",
        "mounts_forbidden",
        "production_mutated",
        "object_storage_contacted",
        "residue_checks_sha256",
        "receipt_sha256",
    }
)
MATERIAL_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "manifest_sha256",
        "role",
        "runtime_target_binding_sha256",
        "plan_sha256",
        "receipt_schema",
        "receipt_verification_schema",
        "material_sha256",
    }
)
RECEIPT_VERIFICATION_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "manifest_sha256",
        "role",
        "runtime_target_binding_sha256",
        "plan_sha256",
        "material_sha256",
        "receipt_sha256",
        "app_image_id",
        "image_content_identity",
        "dependency_versions",
        "zero_residue",
        "network_forbidden",
        "mounts_forbidden",
        "production_mutated",
        "object_storage_contacted",
        "verification_sha256",
    }
)


class ImmutableImagePreflightContractError(ValueError):
    """The local-only immutable-image preflight contract is not exact."""


@dataclass(frozen=True)
class ImmutableImagePreflightCommandResult:
    """One injected local executor result; no subprocess API is accepted here."""

    exit_code: int
    stdout: bytes
    stderr: bytes
    started_at: datetime
    finished_at: datetime


ImmutableImagePreflightRunner = Callable[
    [tuple[str, ...]], ImmutableImagePreflightCommandResult
]


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ImmutableImagePreflightContractError("JSON document has duplicate fields")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ImmutableImagePreflightContractError("contract value is not canonical JSON") from exc


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == ZERO_SHA256:
        raise ImmutableImagePreflightContractError(f"{label} is not a nonzero SHA-256")
    return value


def _uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise ImmutableImagePreflightContractError(f"{label} is invalid")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ImmutableImagePreflightContractError(f"{label} is invalid") from exc
    if str(parsed) != value:
        raise ImmutableImagePreflightContractError(f"{label} is invalid")
    return value


def _release_sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA40_RE.fullmatch(value) is None:
        raise ImmutableImagePreflightContractError(f"{label} is invalid")
    return value


def _image_id(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or IMAGE_ID_RE.fullmatch(value) is None:
        raise ImmutableImagePreflightContractError(f"{label} is invalid")
    return value


def _nonzero_image_identity(value: Any, *, label: str) -> str:
    result = _image_id(value, label=label)
    if result == "sha256:" + ZERO_SHA256:
        raise ImmutableImagePreflightContractError(f"{label} is invalid")
    return result


def _runtime_image_ids(value: Any, *, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(RUNTIME_IMAGE_KINDS):
        raise ImmutableImagePreflightContractError(f"{label} fields differ")
    result = {kind: _image_id(value[kind], label=f"{label}.{kind}") for kind in RUNTIME_IMAGE_KINDS}
    if len(set(result.values())) != len(result):
        raise ImmutableImagePreflightContractError(f"{label} values must be distinct")
    return result


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise ImmutableImagePreflightContractError(f"{label} is invalid")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ImmutableImagePreflightContractError(f"{label} is invalid") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ImmutableImagePreflightContractError(f"{label} is invalid")
    return result.astimezone(timezone.utc)


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


PROBE_SOURCE = "\n".join(
    (
        "import importlib.metadata as metadata",
        "import json",
        "import os",
        "from pathlib import Path",
        "import stat",
        "import sys",
        f"roots = {TRUSTED_SYSTEM_PACKAGE_ROOTS!r}",
        "if sys.version_info[:2] != (3, 11): raise RuntimeError('unexpected Python version')",
        "if not (sys.flags.isolated and sys.flags.no_site and sys.flags.safe_path): raise RuntimeError('isolated flags differ')",
        "installed = []",
        "for text in roots:",
        "    path = Path(text)",
        "    if not path.exists(): continue",
        "    if path.is_symlink(): raise RuntimeError('dependency root is symlinked')",
        "    current = path",
        "    while True:",
        "        data = current.stat(follow_symlinks=False)",
        "        if data.st_uid != 0 or not stat.S_ISDIR(data.st_mode) or stat.S_IMODE(data.st_mode) & 0o022: raise RuntimeError('dependency root is unsafe')",
        "        if current == current.parent: break",
        "        current = current.parent",
        "    sys.path.append(text)",
        "    installed.append(text)",
        "if '/usr/local/lib/python3.11/site-packages' not in installed: raise RuntimeError('pip dependency root is absent')",
        "import asyncpg",
        "import pydantic",
        "import pydantic_settings",
        "import sqlalchemy",
        "payload = {'schema': 'production-shadow-immutable-image-dependency-preflight-output-v1', 'status': 'passed', 'python_major': sys.version_info.major, 'python_minor': sys.version_info.minor, 'isolated': True, 'no_site': True, 'safe_path': True, 'dependency_versions': {'asyncpg': metadata.version('asyncpg'), 'pydantic_settings': metadata.version('pydantic-settings'), 'sqlalchemy': metadata.version('SQLAlchemy')}, 'installed_roots': installed}",
        "print(json.dumps(payload, allow_nan=False, ensure_ascii=True, separators=(',', ':'), sort_keys=True))",
    )
)
PROBE_RUNTIME_ARGV = ("python", "-B", "-I", "-S", "-c", PROBE_SOURCE)


def canonical_container_name(*, operation_id: str, role: str) -> str:
    operation = _uuid(operation_id, label="operation_id")
    if role not in ROLES:
        raise ImmutableImagePreflightContractError("role is invalid")
    return f"tb3p-image-preflight-{operation.replace('-', '')}-{role.replace('_', '-')}"


def _labels(*, operation_id: str, role: str) -> dict[str, str]:
    return {
        "trading-bot.production.operation-id": operation_id,
        "trading-bot.production.preflight-purpose": PROBE_PURPOSE,
        "trading-bot.production.role": role,
    }


def _create_argv(*, image_id: str, container_name: str, labels: Mapping[str, str]) -> list[str]:
    result = [
        DOCKER,
        "create",
        "--pull=never",
        "--name",
        container_name,
    ]
    for key in sorted(labels):
        result.extend(("--label", f"{key}={labels[key]}"))
    return [
        *result,
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--user",
        PROBE_USER,
        "--pids-limit",
        str(PROBE_PIDS_LIMIT),
        "--memory",
        str(PROBE_MEMORY_BYTES),
        "--cpus",
        "0.25",
        "--tmpfs",
        PROBE_TMPFS,
        "--workdir",
        "/",
        image_id,
        *PROBE_RUNTIME_ARGV,
    ]


def _residue_argv(*, resource: str, labels: Mapping[str, str]) -> list[str]:
    if resource == "container":
        result = [DOCKER, "ps", "--all", "--quiet"]
    elif resource == "volume":
        result = [DOCKER, "volume", "ls", "--quiet"]
    elif resource == "network":
        result = [DOCKER, "network", "ls", "--quiet"]
    else:
        raise ImmutableImagePreflightContractError("residue resource is invalid")
    for key in sorted(labels):
        result.extend(("--filter", f"label={key}={labels[key]}"))
    return result


def _plan_digest(document: Mapping[str, Any]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "plan_sha256"})


def _receipt_digest(document: Mapping[str, Any]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "receipt_sha256"})


def _material_digest(document: Mapping[str, Any]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "material_sha256"})


def _verification_digest(document: Mapping[str, Any]) -> str:
    return _sha256(
        {key: value for key, value in document.items() if key != "verification_sha256"}
    )


def _require_root() -> None:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise ImmutableImagePreflightContractError("immutable image preflight installation requires root:root")


def _absolute_path(value: Path | str, *, label: str) -> Path:
    try:
        text = os.fspath(value)
        pure = PurePosixPath(text)
    except TypeError as exc:
        raise ImmutableImagePreflightContractError(f"{label} is invalid") from exc
    if not pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ImmutableImagePreflightContractError(f"{label} is not canonical")
    return Path(text)


def _private_directory(path: Path, *, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ImmutableImagePreflightContractError(f"{label} is unavailable") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise ImmutableImagePreflightContractError(f"{label} is not root-only")


def _ensure_private_install_directory(path: Path, *, root: Path) -> None:
    """Create only the fixed private descendants below an existing secure root."""

    _private_directory(root, label="immutable image preflight install root")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ImmutableImagePreflightContractError("immutable image preflight install path escapes root") from exc
    current = root
    for component in relative.parts:
        current = current / component
        try:
            current.mkdir(mode=INSTALL_DIRECTORY_MODE)
        except FileExistsError:
            pass
        except OSError as exc:
            raise ImmutableImagePreflightContractError(
                "immutable image preflight install directory cannot be created"
            ) from exc
        _private_directory(current, label="immutable image preflight install directory")


def _require_private_install_directory(path: Path, *, root: Path) -> None:
    _private_directory(root, label="immutable image preflight install root")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ImmutableImagePreflightContractError("immutable image preflight install path escapes root") from exc
    current = root
    for component in relative.parts:
        current = current / component
        _private_directory(current, label="immutable image preflight install directory")


def canonical_install_paths(
    *, operation_id: str, role: str, install_root: Path | str = INSTALL_ROOT
) -> dict[str, Path]:
    """Return the sole local filesystem layout for one role preflight."""

    operation = _uuid(operation_id, label="operation_id")
    if role not in ROLES:
        raise ImmutableImagePreflightContractError("role is invalid")
    root = _absolute_path(install_root, label="immutable image preflight install root")
    directory = root / operation / INSTALL_DIRECTORY / role
    return {
        "directory": directory,
        "plan": directory / "plan.json",
        "material": directory / "material.json",
        "receipt": directory / "receipt.json",
    }


def _canonical_payload(document: Mapping[str, Any]) -> bytes:
    return _canonical_json(document) + b"\n"


def _read_canonical_installed_document(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        payload = read_secure_bytes(
            path,
            label=label,
            owner_uid=0,
            max_size=MAX_INSTALL_BYTES,
        )
    except SecureFileError as exc:
        raise ImmutableImagePreflightContractError(f"{label} is unavailable") from exc
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImmutableImagePreflightContractError(f"{label} is not strict canonical JSON") from exc
    if not isinstance(document, dict) or payload != _canonical_payload(document):
        raise ImmutableImagePreflightContractError(f"{label} is not canonical JSON")
    return document, payload


def _preflight_create_only_collision(path: Path, payload: bytes, *, label: str) -> None:
    if not os.path.lexists(path):
        return
    _existing, observed = _read_canonical_installed_document(path, label=label)
    if observed != payload:
        raise ImmutableImagePreflightContractError(f"{label} collision differs")


def _publish_create_only_and_readback(path: Path, payload: bytes, *, label: str) -> None:
    try:
        write_secure_new_bytes(
            path,
            payload,
            label=label,
            mode=INSTALL_FILE_MODE,
            max_size=MAX_INSTALL_BYTES,
        )
    except SecureFileError as exc:
        if "already exists" not in str(exc):
            raise ImmutableImagePreflightContractError(f"{label} cannot be published") from exc
    _existing, observed = _read_canonical_installed_document(path, label=label)
    if observed != payload:
        raise ImmutableImagePreflightContractError(f"{label} read-back differs")


def _residue_checks_digest(*, plan: Mapping[str, Any]) -> str:
    return _sha256(
        {
            "container_residue_argv": plan["container_residue_argv"],
            "volume_residue_argv": plan["volume_residue_argv"],
            "network_residue_argv": plan["network_residue_argv"],
            "container_output_sha256": hashlib.sha256(b"").hexdigest(),
            "volume_output_sha256": hashlib.sha256(b"").hexdigest(),
            "network_output_sha256": hashlib.sha256(b"").hexdigest(),
        }
    )


def build_plan(
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    manifest_sha256: str,
    role: str,
    runtime_target_binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the only local, no-network Docker argv permitted for this probe."""

    campaign = _uuid(campaign_id, label="campaign_id")
    operation = _uuid(operation_id, label="operation_id")
    if campaign == operation:
        raise ImmutableImagePreflightContractError("campaign and operation must differ")
    release = _release_sha(release_sha, label="release_sha")
    manifest = _nonzero_sha256(manifest_sha256, label="manifest_sha256")
    if role not in ROLES:
        raise ImmutableImagePreflightContractError("role is invalid")
    try:
        binding_document = validate_observer_runtime_target_binding(
            runtime_target_binding,
            campaign_id=campaign,
            operation_id=operation,
            release_sha=release,
            manifest_sha256=manifest,
            role=role,
            label="immutable image preflight runtime target binding",
        )
    except ConvergenceRuntimeTargetBindingError as exc:
        raise ImmutableImagePreflightContractError(
            "runtime target binding is invalid"
        ) from exc
    binding = binding_document["binding_sha256"]
    image_ids = _runtime_image_ids(
        binding_document["role_runtime_image_ids"], label="role_runtime_image_ids"
    )
    image = image_ids["app"]
    name = canonical_container_name(operation_id=operation, role=role)
    labels = _labels(operation_id=operation, role=role)
    document: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "status": "planned-not-executed",
        "campaign_id": campaign,
        "operation_id": operation,
        "release_sha": release,
        "manifest_sha256": manifest,
        "role": role,
        "runtime_target_binding_sha256": binding,
        "app_image_id": image,
        "container_name": name,
        "labels": labels,
        "image_inspect_argv": [DOCKER, "image", "inspect", image],
        "create_argv": _create_argv(image_id=image, container_name=name, labels=labels),
        "container_inspect_argv": [DOCKER, "container", "inspect", name],
        "start_argv": [DOCKER, "start", "--attach", name],
        "remove_argv": [DOCKER, "container", "rm", "--force", name],
        "container_residue_argv": _residue_argv(resource="container", labels=labels),
        "volume_residue_argv": _residue_argv(resource="volume", labels=labels),
        "network_residue_argv": _residue_argv(resource="network", labels=labels),
        "timeout_seconds": TIMEOUT_SECONDS,
        "max_stdout_bytes": MAX_STDOUT_BYTES,
        "max_stderr_bytes": MAX_STDERR_BYTES,
        "network_forbidden": True,
        "mounts_forbidden": True,
        "production_mutation_forbidden": True,
        "object_storage_contact_forbidden": True,
        "plan_sha256": ZERO_SHA256,
    }
    document["plan_sha256"] = _plan_digest(document)
    return validate_plan(document)


def validate_plan(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != PLAN_FIELDS:
        raise ImmutableImagePreflightContractError("plan fields differ")
    document = dict(value)
    campaign = _uuid(document.get("campaign_id"), label="plan campaign_id")
    operation = _uuid(document.get("operation_id"), label="plan operation_id")
    if campaign == operation:
        raise ImmutableImagePreflightContractError("plan campaign and operation must differ")
    release = _release_sha(document.get("release_sha"), label="plan release_sha")
    manifest = _nonzero_sha256(document.get("manifest_sha256"), label="plan manifest_sha256")
    role = document.get("role")
    if role not in ROLES:
        raise ImmutableImagePreflightContractError("plan role is invalid")
    binding = _nonzero_sha256(
        document.get("runtime_target_binding_sha256"), label="plan runtime target binding"
    )
    image = _image_id(document.get("app_image_id"), label="plan app image ID")
    name = canonical_container_name(operation_id=operation, role=role)
    labels = _labels(operation_id=operation, role=role)
    expected = {
        "schema": PLAN_SCHEMA,
        "status": "planned-not-executed",
        "campaign_id": campaign,
        "operation_id": operation,
        "release_sha": release,
        "manifest_sha256": manifest,
        "role": role,
        "runtime_target_binding_sha256": binding,
        "app_image_id": image,
        "container_name": name,
        "labels": labels,
        "image_inspect_argv": [DOCKER, "image", "inspect", image],
        "create_argv": _create_argv(image_id=image, container_name=name, labels=labels),
        "container_inspect_argv": [DOCKER, "container", "inspect", name],
        "start_argv": [DOCKER, "start", "--attach", name],
        "remove_argv": [DOCKER, "container", "rm", "--force", name],
        "container_residue_argv": _residue_argv(resource="container", labels=labels),
        "volume_residue_argv": _residue_argv(resource="volume", labels=labels),
        "network_residue_argv": _residue_argv(resource="network", labels=labels),
        "timeout_seconds": TIMEOUT_SECONDS,
        "max_stdout_bytes": MAX_STDOUT_BYTES,
        "max_stderr_bytes": MAX_STDERR_BYTES,
        "network_forbidden": True,
        "mounts_forbidden": True,
        "production_mutation_forbidden": True,
        "object_storage_contact_forbidden": True,
    }
    if any(document.get(key) != item for key, item in expected.items()):
        raise ImmutableImagePreflightContractError("plan differs from fixed local-only probe")
    if document.get("plan_sha256") != _plan_digest(document):
        raise ImmutableImagePreflightContractError("plan digest differs")
    return document


def build_material(*, plan: Mapping[str, Any]) -> dict[str, Any]:
    """Bind the later receipt verifier to one exact, installed plan."""

    checked = validate_plan(plan)
    document: dict[str, Any] = {
        "schema": MATERIAL_SCHEMA,
        "status": "installed-not-executed",
        "campaign_id": checked["campaign_id"],
        "operation_id": checked["operation_id"],
        "release_sha": checked["release_sha"],
        "manifest_sha256": checked["manifest_sha256"],
        "role": checked["role"],
        "runtime_target_binding_sha256": checked["runtime_target_binding_sha256"],
        "plan_sha256": checked["plan_sha256"],
        "receipt_schema": RECEIPT_SCHEMA,
        "receipt_verification_schema": RECEIPT_VERIFICATION_SCHEMA,
        "material_sha256": ZERO_SHA256,
    }
    document["material_sha256"] = _material_digest(document)
    return validate_material(document, plan=checked)


def validate_material(value: Any, *, plan: Mapping[str, Any]) -> dict[str, Any]:
    checked = validate_plan(plan)
    if not isinstance(value, Mapping) or set(value) != MATERIAL_FIELDS:
        raise ImmutableImagePreflightContractError("preflight material fields differ")
    document = dict(value)
    expected = {
        "schema": MATERIAL_SCHEMA,
        "status": "installed-not-executed",
        "campaign_id": checked["campaign_id"],
        "operation_id": checked["operation_id"],
        "release_sha": checked["release_sha"],
        "manifest_sha256": checked["manifest_sha256"],
        "role": checked["role"],
        "runtime_target_binding_sha256": checked["runtime_target_binding_sha256"],
        "plan_sha256": checked["plan_sha256"],
        "receipt_schema": RECEIPT_SCHEMA,
        "receipt_verification_schema": RECEIPT_VERIFICATION_SCHEMA,
    }
    if any(document.get(key) != item for key, item in expected.items()):
        raise ImmutableImagePreflightContractError("preflight material binding differs")
    if document.get("material_sha256") != _material_digest(document):
        raise ImmutableImagePreflightContractError("preflight material digest differs")
    return document


def load_installed_inputs(
    *, operation_id: str, role: str, install_root: Path | str = INSTALL_ROOT
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Path]]:
    """Reopen and validate the exact plan/material pair without execution."""

    _require_root()
    paths = canonical_install_paths(
        operation_id=operation_id,
        role=role,
        install_root=install_root,
    )
    root = _absolute_path(install_root, label="immutable image preflight install root")
    _require_private_install_directory(paths["directory"], root=root)
    plan, _plan_payload = _read_canonical_installed_document(
        paths["plan"], label="immutable image preflight plan"
    )
    material, _material_payload = _read_canonical_installed_document(
        paths["material"], label="immutable image preflight material"
    )
    checked_plan = validate_plan(plan)
    checked_material = validate_material(material, plan=checked_plan)
    if checked_plan["operation_id"] != operation_id or checked_plan["role"] != role:
        raise ImmutableImagePreflightContractError("installed preflight path identity differs")
    return checked_plan, checked_material, paths


def install_from_runtime_target_binding(
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    manifest_sha256: str,
    role: str,
    runtime_target_binding: Mapping[str, Any],
    install_root: Path | str = INSTALL_ROOT,
) -> dict[str, str]:
    """Install one root-only, create-only preflight input pair.

    This function validates the redacted runtime binding but never contacts
    Docker, a network peer, Object Storage, or a subprocess.
    """

    _require_root()
    plan = build_plan(
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
        manifest_sha256=manifest_sha256,
        role=role,
        runtime_target_binding=runtime_target_binding,
    )
    material = build_material(plan=plan)
    paths = canonical_install_paths(
        operation_id=plan["operation_id"],
        role=plan["role"],
        install_root=install_root,
    )
    root = _absolute_path(install_root, label="immutable image preflight install root")
    _ensure_private_install_directory(paths["directory"], root=root)
    plan_payload = _canonical_payload(plan)
    material_payload = _canonical_payload(material)
    # Check both collision states before the first write so a bad material can
    # never leave an otherwise valid plan as a misleading readiness marker.
    _preflight_create_only_collision(
        paths["plan"], plan_payload, label="immutable image preflight plan"
    )
    _preflight_create_only_collision(
        paths["material"], material_payload, label="immutable image preflight material"
    )
    # Material first; a plan exists only after the receipt verifier contract is
    # durably available beside it.
    _publish_create_only_and_readback(
        paths["material"], material_payload, label="immutable image preflight material"
    )
    _publish_create_only_and_readback(
        paths["plan"], plan_payload, label="immutable image preflight plan"
    )
    checked_plan, checked_material, checked_paths = load_installed_inputs(
        operation_id=plan["operation_id"],
        role=plan["role"],
        install_root=install_root,
    )
    if checked_plan != plan or checked_material != material or checked_paths != paths:
        raise ImmutableImagePreflightContractError("installed preflight read-back differs")
    return {
        "plan_path": os.fspath(paths["plan"]),
        "plan_sha256": plan["plan_sha256"],
        "material_path": os.fspath(paths["material"]),
        "material_sha256": material["material_sha256"],
        "receipt_path": os.fspath(paths["receipt"]),
        "receipt_schema": RECEIPT_SCHEMA,
        "receipt_verification_schema": RECEIPT_VERIFICATION_SCHEMA,
    }


def validate_probe_output(payload: bytes) -> dict[str, Any]:
    """Validate the bounded, nonsecret JSON emitted by the fixed probe argv."""

    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_STDOUT_BYTES:
        raise ImmutableImagePreflightContractError("probe stdout is invalid")
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImmutableImagePreflightContractError("probe stdout is not strict JSON") from exc
    if not isinstance(document, dict) or payload != _canonical_json(document) + b"\n":
        raise ImmutableImagePreflightContractError("probe stdout is not canonical")
    if set(document) != OUTPUT_FIELDS or document.get("schema") != OUTPUT_SCHEMA or document.get("status") != "passed":
        raise ImmutableImagePreflightContractError("probe output fields differ")
    if (
        document.get("python_major") != 3
        or document.get("python_minor") != 11
        or document.get("isolated") is not True
        or document.get("no_site") is not True
        or document.get("safe_path") is not True
        or document.get("dependency_versions") != EXPECTED_DEPENDENCIES
    ):
        raise ImmutableImagePreflightContractError("probe interpreter or dependency proof differs")
    roots = document.get("installed_roots")
    if (
        not isinstance(roots, list)
        or not roots
        or roots != [root for root in TRUSTED_SYSTEM_PACKAGE_ROOTS if root in roots]
        or "/usr/local/lib/python3.11/site-packages" not in roots
    ):
        raise ImmutableImagePreflightContractError("probe dependency roots differ")
    return document


def inspect_image(image: Any, *, plan: Mapping[str, Any]) -> dict[str, str]:
    """Bind one raw local inspect object to the exact planned app image."""

    checked = validate_plan(plan)
    if not isinstance(image, Mapping) or image.get("Id") != checked["app_image_id"]:
        raise ImmutableImagePreflightContractError("image inspection ID differs from the plan")
    config = image.get("Config")
    if not isinstance(config, Mapping) or config.get("Entrypoint") not in (None, []):
        raise ImmutableImagePreflightContractError("image inspection entrypoint differs")
    try:
        _descriptor, identity = image_content_descriptor(dict(image))
    except DockerImageIdentityError as exc:
        raise ImmutableImagePreflightContractError("image inspection descriptor is invalid") from exc
    return {"image_id": checked["app_image_id"], "image_content_identity": identity}


def inspect_container(container: Any, *, plan: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce one live inspect object to the no-network/no-mounts proof."""

    checked = validate_plan(plan)
    if not isinstance(container, Mapping):
        raise ImmutableImagePreflightContractError("container inspection is invalid")
    config = container.get("Config")
    host = container.get("HostConfig")
    restart = host.get("RestartPolicy") if isinstance(host, Mapping) else None
    labels = config.get("Labels") if isinstance(config, Mapping) else None
    mounts = container.get("Mounts")
    networks = container.get("NetworkSettings")
    attached = networks.get("Networks") if isinstance(networks, Mapping) else None
    identifier = container.get("Id")
    if (
        not isinstance(identifier, str)
        or CONTAINER_ID_RE.fullmatch(identifier) is None
        or container.get("Name") != f"/{checked['container_name']}"
        or container.get("Image") != checked["app_image_id"]
        or not isinstance(config, Mapping)
        or config.get("Image") != checked["app_image_id"]
        or config.get("User") != PROBE_USER
        or config.get("WorkingDir") != "/"
        or config.get("Cmd") != list(PROBE_RUNTIME_ARGV)
        or config.get("Entrypoint") not in (None, [])
        or labels != checked["labels"]
        or not isinstance(host, Mapping)
        or host.get("NetworkMode") != "none"
        or host.get("ReadonlyRootfs") is not True
        or host.get("CapDrop") != ["ALL"]
        or host.get("CapAdd") not in (None, [])
        or host.get("SecurityOpt") != ["no-new-privileges:true"]
        or host.get("PidsLimit") != PROBE_PIDS_LIMIT
        or host.get("Memory") != PROBE_MEMORY_BYTES
        or host.get("NanoCpus") != PROBE_NANO_CPUS
        or host.get("Privileged") is not False
        or host.get("PortBindings") not in (None, {})
        or host.get("Binds") not in (None, [])
        or host.get("VolumesFrom") not in (None, [])
        or host.get("Devices") not in (None, [])
        or host.get("DeviceRequests") not in (None, [])
        or host.get("PidMode") not in (None, "", "private")
        or host.get("IpcMode") not in (None, "", "private")
        or host.get("UTSMode") not in (None, "", "private")
        or host.get("UsernsMode") not in (None, "", "private")
        or host.get("CgroupnsMode") not in (None, "", "private")
        or host.get("Tmpfs") != {"/tmp": "rw,noexec,nosuid,size=16m"}
        or host.get("AutoRemove") is not False
        or not isinstance(restart, Mapping)
        or restart.get("Name") != "no"
        or not isinstance(mounts, list)
        or mounts
        or attached not in (None, {})
    ):
        raise ImmutableImagePreflightContractError("container inspection differs from the local-only probe")
    return {
        "container_id_sha256": hashlib.sha256(identifier.encode("ascii")).hexdigest(),
        "image_id": checked["app_image_id"],
        "network_mode": "none",
        "read_only": True,
        "unprivileged_user": PROBE_USER,
        "cap_drop_all": True,
        "no_new_privileges": True,
        "mount_count": 0,
        "ports_published": False,
        "privileged": False,
        "restart_policy": "no",
        "auto_remove": False,
        "tmpfs": "/tmp:rw,noexec,nosuid,size=16m",
    }


def _validate_container_proof(value: Any, *, image_id: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != CONTAINER_PROOF_FIELDS:
        raise ImmutableImagePreflightContractError("container proof fields differ")
    document = dict(value)
    expected = {
        "image_id": image_id,
        "network_mode": "none",
        "read_only": True,
        "unprivileged_user": PROBE_USER,
        "cap_drop_all": True,
        "no_new_privileges": True,
        "mount_count": 0,
        "ports_published": False,
        "privileged": False,
        "restart_policy": "no",
        "auto_remove": False,
        "tmpfs": "/tmp:rw,noexec,nosuid,size=16m",
    }
    if any(document.get(key) != item for key, item in expected.items()):
        raise ImmutableImagePreflightContractError("container proof differs")
    _nonzero_sha256(document.get("container_id_sha256"), label="container proof ID")
    return document


def build_receipt(
    *,
    plan: Mapping[str, Any],
    image_inspection: Mapping[str, Any],
    container_inspection: Mapping[str, Any],
    stdout: bytes,
    stderr_bytes: int,
    exit_code: int,
    started_at: datetime,
    finished_at: datetime,
    container_residue: bytes,
    volume_residue: bytes,
    network_residue: bytes,
) -> dict[str, Any]:
    """Build one redacted receipt after a later executor has cleaned up."""

    checked = validate_plan(plan)
    image = inspect_image(image_inspection, plan=checked)
    container = inspect_container(container_inspection, plan=checked)
    output = validate_probe_output(stdout)
    if (
        type(stderr_bytes) is not int
        or not 0 <= stderr_bytes <= checked["max_stderr_bytes"]
        or type(exit_code) is not int
        or exit_code != 0
        or any(
            not isinstance(value, bytes) or value.strip()
            for value in (container_residue, volume_residue, network_residue)
        )
        or started_at.tzinfo is None
        or finished_at.tzinfo is None
    ):
        raise ImmutableImagePreflightContractError("preflight outcome is invalid")
    started = started_at.astimezone(timezone.utc)
    finished = finished_at.astimezone(timezone.utc)
    duration_ms = int((finished - started).total_seconds() * 1000)
    if not 0 <= duration_ms <= checked["timeout_seconds"] * 1000:
        raise ImmutableImagePreflightContractError("preflight duration exceeds plan")
    document: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "status": "completed-local-only",
        "campaign_id": checked["campaign_id"],
        "operation_id": checked["operation_id"],
        "release_sha": checked["release_sha"],
        "manifest_sha256": checked["manifest_sha256"],
        "role": checked["role"],
        "runtime_target_binding_sha256": checked["runtime_target_binding_sha256"],
        "plan_sha256": checked["plan_sha256"],
        "app_image_id": checked["app_image_id"],
        "image_content_identity": image["image_content_identity"],
        "container_proof": container,
        "dependency_versions": output["dependency_versions"],
        "probe_stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "probe_stdout_bytes": len(stdout),
        "stderr_bytes": stderr_bytes,
        "exit_code": 0,
        "started_at": _timestamp_text(started),
        "finished_at": _timestamp_text(finished),
        "duration_ms": duration_ms,
        "zero_residue": True,
        "network_forbidden": True,
        "mounts_forbidden": True,
        "production_mutated": False,
        "object_storage_contacted": False,
        "residue_checks_sha256": _residue_checks_digest(plan=checked),
        "receipt_sha256": ZERO_SHA256,
    }
    document["receipt_sha256"] = _receipt_digest(document)
    return validate_receipt(document, plan=checked)


def validate_receipt(value: Any, *, plan: Mapping[str, Any]) -> dict[str, Any]:
    checked = validate_plan(plan)
    if not isinstance(value, Mapping) or set(value) != RECEIPT_FIELDS:
        raise ImmutableImagePreflightContractError("receipt fields differ")
    document = dict(value)
    expected = {
        "schema": RECEIPT_SCHEMA,
        "status": "completed-local-only",
        "campaign_id": checked["campaign_id"],
        "operation_id": checked["operation_id"],
        "release_sha": checked["release_sha"],
        "manifest_sha256": checked["manifest_sha256"],
        "role": checked["role"],
        "runtime_target_binding_sha256": checked["runtime_target_binding_sha256"],
        "plan_sha256": checked["plan_sha256"],
        "app_image_id": checked["app_image_id"],
        "dependency_versions": EXPECTED_DEPENDENCIES,
        "exit_code": 0,
        "zero_residue": True,
        "network_forbidden": True,
        "mounts_forbidden": True,
        "production_mutated": False,
        "object_storage_contacted": False,
    }
    if any(document.get(key) != item for key, item in expected.items()):
        raise ImmutableImagePreflightContractError("receipt binding differs")
    _nonzero_image_identity(
        document.get("image_content_identity"), label="receipt image content identity"
    )
    _validate_container_proof(document.get("container_proof"), image_id=checked["app_image_id"])
    for field in ("probe_stdout_sha256", "receipt_sha256", "residue_checks_sha256"):
        _nonzero_sha256(document.get(field), label=f"receipt {field}")
    if document["residue_checks_sha256"] != _residue_checks_digest(plan=checked):
        raise ImmutableImagePreflightContractError("receipt residue checks differ")
    if (
        type(document.get("probe_stdout_bytes")) is not int
        or not 1 <= document["probe_stdout_bytes"] <= checked["max_stdout_bytes"]
        or type(document.get("stderr_bytes")) is not int
        or not 0 <= document["stderr_bytes"] <= checked["max_stderr_bytes"]
        or type(document.get("duration_ms")) is not int
        or not 0 <= document["duration_ms"] <= checked["timeout_seconds"] * 1000
    ):
        raise ImmutableImagePreflightContractError("receipt outcome differs")
    started = _timestamp(document.get("started_at"), label="receipt started_at")
    finished = _timestamp(document.get("finished_at"), label="receipt finished_at")
    if finished < started or int((finished - started).total_seconds() * 1000) != document["duration_ms"]:
        raise ImmutableImagePreflightContractError("receipt timing differs")
    if document.get("receipt_sha256") != _receipt_digest(document):
        raise ImmutableImagePreflightContractError("receipt digest differs")
    return document


def build_receipt_verification(
    *, plan: Mapping[str, Any], material: Mapping[str, Any], receipt: Mapping[str, Any]
) -> dict[str, Any]:
    """Reduce a verified receipt to the nonsecret handoff schema.

    The verifier is deliberately pure: it does not publish a gate record and
    cannot start Docker or recover a container.  A later executor may persist
    this already-redacted result through its own separately reviewed path.
    """

    checked_plan = validate_plan(plan)
    checked_material = validate_material(material, plan=checked_plan)
    checked_receipt = validate_receipt(receipt, plan=checked_plan)
    document: dict[str, Any] = {
        "schema": RECEIPT_VERIFICATION_SCHEMA,
        "status": "verified-local-only",
        "campaign_id": checked_plan["campaign_id"],
        "operation_id": checked_plan["operation_id"],
        "release_sha": checked_plan["release_sha"],
        "manifest_sha256": checked_plan["manifest_sha256"],
        "role": checked_plan["role"],
        "runtime_target_binding_sha256": checked_plan["runtime_target_binding_sha256"],
        "plan_sha256": checked_plan["plan_sha256"],
        "material_sha256": checked_material["material_sha256"],
        "receipt_sha256": checked_receipt["receipt_sha256"],
        "app_image_id": checked_receipt["app_image_id"],
        "image_content_identity": checked_receipt["image_content_identity"],
        "dependency_versions": dict(checked_receipt["dependency_versions"]),
        "zero_residue": True,
        "network_forbidden": True,
        "mounts_forbidden": True,
        "production_mutated": False,
        "object_storage_contacted": False,
        "verification_sha256": ZERO_SHA256,
    }
    document["verification_sha256"] = _verification_digest(document)
    return validate_receipt_verification(document, plan=checked_plan, material=checked_material)


def validate_receipt_verification(
    value: Any, *, plan: Mapping[str, Any], material: Mapping[str, Any]
) -> dict[str, Any]:
    checked_plan = validate_plan(plan)
    checked_material = validate_material(material, plan=checked_plan)
    if not isinstance(value, Mapping) or set(value) != RECEIPT_VERIFICATION_FIELDS:
        raise ImmutableImagePreflightContractError("receipt verification fields differ")
    document = dict(value)
    expected = {
        "schema": RECEIPT_VERIFICATION_SCHEMA,
        "status": "verified-local-only",
        "campaign_id": checked_plan["campaign_id"],
        "operation_id": checked_plan["operation_id"],
        "release_sha": checked_plan["release_sha"],
        "manifest_sha256": checked_plan["manifest_sha256"],
        "role": checked_plan["role"],
        "runtime_target_binding_sha256": checked_plan["runtime_target_binding_sha256"],
        "plan_sha256": checked_plan["plan_sha256"],
        "material_sha256": checked_material["material_sha256"],
        "app_image_id": checked_plan["app_image_id"],
        "dependency_versions": EXPECTED_DEPENDENCIES,
        "zero_residue": True,
        "network_forbidden": True,
        "mounts_forbidden": True,
        "production_mutated": False,
        "object_storage_contacted": False,
    }
    if any(document.get(key) != item for key, item in expected.items()):
        raise ImmutableImagePreflightContractError("receipt verification binding differs")
    _nonzero_sha256(document.get("receipt_sha256"), label="receipt verification receipt")
    _nonzero_image_identity(
        document.get("app_image_id"), label="receipt verification app image"
    )
    _nonzero_image_identity(
        document.get("image_content_identity"), label="receipt verification image content"
    )
    if document.get("verification_sha256") != _verification_digest(document):
        raise ImmutableImagePreflightContractError("receipt verification digest differs")
    return document


def _run_injected_executor(
    runner: ImmutableImagePreflightRunner,
    argv: list[str],
    *,
    label: str,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
) -> ImmutableImagePreflightCommandResult:
    """Run one fixed argv through the injected seam and validate its envelope."""

    try:
        result = runner(tuple(argv))
    except Exception as exc:
        raise ImmutableImagePreflightContractError(f"{label} executor is unavailable") from exc
    if (
        not isinstance(result, ImmutableImagePreflightCommandResult)
        or type(result.exit_code) is not int
        or not isinstance(result.stdout, bytes)
        or not isinstance(result.stderr, bytes)
        or len(result.stdout) > max_stdout_bytes
        or len(result.stderr) > max_stderr_bytes
        or result.started_at.tzinfo is None
        or result.finished_at.tzinfo is None
        or result.finished_at < result.started_at
    ):
        raise ImmutableImagePreflightContractError(f"{label} executor result is invalid")
    return result


def _require_success(
    result: ImmutableImagePreflightCommandResult,
    *,
    label: str,
) -> None:
    if result.exit_code != 0:
        raise ImmutableImagePreflightContractError(f"{label} executor failed")


def _docker_inspect_document(payload: bytes, *, label: str) -> dict[str, Any]:
    if not 1 <= len(payload) <= MAX_INSPECT_BYTES:
        raise ImmutableImagePreflightContractError(f"{label} output is invalid")
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImmutableImagePreflightContractError(f"{label} output is not strict JSON") from exc
    if (
        not isinstance(document, list)
        or len(document) != 1
        or not isinstance(document[0], dict)
    ):
        raise ImmutableImagePreflightContractError(f"{label} output shape differs")
    return document[0]


def _created_container_id(payload: bytes) -> str:
    try:
        identifier = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ImmutableImagePreflightContractError("container create output is not ASCII") from exc
    if not identifier.endswith("\n") or identifier[:-1].endswith("\n"):
        raise ImmutableImagePreflightContractError("container create output is not canonical")
    identifier = identifier[:-1]
    if CONTAINER_ID_RE.fullmatch(identifier) is None:
        raise ImmutableImagePreflightContractError("container create ID is invalid")
    return identifier


def _require_zero_residue(
    runner: ImmutableImagePreflightRunner,
    *,
    plan: Mapping[str, Any],
) -> tuple[bytes, bytes, bytes]:
    outputs: list[bytes] = []
    for label, field in (
        ("container residue", "container_residue_argv"),
        ("volume residue", "volume_residue_argv"),
        ("network residue", "network_residue_argv"),
    ):
        result = _run_injected_executor(
            runner,
            plan[field],
            label=label,
            max_stdout_bytes=plan["max_stdout_bytes"],
            max_stderr_bytes=plan["max_stderr_bytes"],
        )
        _require_success(result, label=label)
        if result.stdout.strip() or result.stderr:
            raise ImmutableImagePreflightContractError(f"{label} is not empty")
        outputs.append(result.stdout)
    return tuple(outputs)  # type: ignore[return-value]


def _existing_installed_receipt(
    *,
    plan: Mapping[str, Any],
    material: Mapping[str, Any],
    paths: Mapping[str, Path],
) -> dict[str, Any] | None:
    """Return one verified create-only receipt, never re-run a completed probe."""

    if not os.path.lexists(paths["receipt"]):
        return None
    receipt, _payload = _read_canonical_installed_document(
        paths["receipt"], label="immutable image preflight receipt"
    )
    checked = validate_receipt(receipt, plan=plan)
    verification = build_receipt_verification(
        plan=plan,
        material=material,
        receipt=checked,
    )
    return {
        "status": "already-completed-local-only",
        "receipt_path": os.fspath(paths["receipt"]),
        "receipt_sha256": checked["receipt_sha256"],
        "verification": verification,
    }


def execute_installed_preflight(
    *,
    operation_id: str,
    role: str,
    runner: ImmutableImagePreflightRunner,
    install_root: Path | str = INSTALL_ROOT,
) -> dict[str, Any]:
    """Execute exactly one installed local image probe through an injected runner.

    This is deliberately not a Docker wrapper: all command execution is
    supplied by the caller through ``runner`` and every accepted command comes
    from the root-only installed plan.  The executor never receives caller
    argv, environment, image IDs, or container names.  A redacted receipt is
    published create-only only after inspect, probe, removal, and all three
    empty-residue checks succeed.  It does not contact a gate or a peer.
    """

    plan, material, paths = load_installed_inputs(
        operation_id=operation_id,
        role=role,
        install_root=install_root,
    )
    existing = _existing_installed_receipt(plan=plan, material=material, paths=paths)
    if existing is not None:
        return existing

    created = False
    primary_error: Exception | None = None
    receipt: dict[str, Any] | None = None
    try:
        image_result = _run_injected_executor(
            runner,
            plan["image_inspect_argv"],
            label="image inspect",
            max_stdout_bytes=MAX_INSPECT_BYTES,
            max_stderr_bytes=plan["max_stderr_bytes"],
        )
        _require_success(image_result, label="image inspect")
        image_document = _docker_inspect_document(image_result.stdout, label="image inspect")
        inspect_image(image_document, plan=plan)

        create_result = _run_injected_executor(
            runner,
            plan["create_argv"],
            label="container create",
            max_stdout_bytes=plan["max_stdout_bytes"],
            max_stderr_bytes=plan["max_stderr_bytes"],
        )
        _require_success(create_result, label="container create")
        # A successful Docker create may have allocated the planned name even
        # if its stdout is malformed. Always attempt the fixed-name cleanup.
        created = True
        created_identifier = _created_container_id(create_result.stdout)
        if create_result.stderr:
            raise ImmutableImagePreflightContractError("container create emitted stderr")

        container_result = _run_injected_executor(
            runner,
            plan["container_inspect_argv"],
            label="container inspect",
            max_stdout_bytes=MAX_INSPECT_BYTES,
            max_stderr_bytes=plan["max_stderr_bytes"],
        )
        _require_success(container_result, label="container inspect")
        container_document = _docker_inspect_document(
            container_result.stdout,
            label="container inspect",
        )
        if container_document.get("Id") != created_identifier:
            raise ImmutableImagePreflightContractError("container inspect ID differs from create")
        # Reject every isolation/mount/image drift before the probe can start.
        inspect_container(container_document, plan=plan)

        start_result = _run_injected_executor(
            runner,
            plan["start_argv"],
            label="container start",
            max_stdout_bytes=plan["max_stdout_bytes"],
            max_stderr_bytes=plan["max_stderr_bytes"],
        )
        _require_success(start_result, label="container start")

        # ``build_receipt`` repeats the image/container/probe validation after
        # cleanup has completed, so raw executor values never become evidence.
        receipt = build_receipt(
            plan=plan,
            image_inspection=image_document,
            container_inspection=container_document,
            stdout=start_result.stdout,
            stderr_bytes=len(start_result.stderr),
            exit_code=start_result.exit_code,
            started_at=start_result.started_at,
            finished_at=start_result.finished_at,
            container_residue=b"",
            volume_residue=b"",
            network_residue=b"",
        )
    except Exception as exc:
        primary_error = exc

    cleanup_error: Exception | None = None
    if created:
        try:
            remove_result = _run_injected_executor(
                runner,
                plan["remove_argv"],
                label="container remove",
                max_stdout_bytes=plan["max_stdout_bytes"],
                max_stderr_bytes=plan["max_stderr_bytes"],
            )
            _require_success(remove_result, label="container remove")
            if remove_result.stderr:
                raise ImmutableImagePreflightContractError("container remove emitted stderr")
        except Exception as exc:
            cleanup_error = exc
    try:
        residues = _require_zero_residue(runner, plan=plan)
    except Exception as exc:
        cleanup_error = cleanup_error or exc
        residues = None

    if cleanup_error is not None:
        raise ImmutableImagePreflightContractError(
            "immutable image preflight cleanup or zero-residue verification failed"
        ) from cleanup_error
    if primary_error is not None:
        raise primary_error
    if receipt is None or residues is None:
        raise ImmutableImagePreflightContractError("immutable image preflight result is unavailable")
    # Rebuild only after final residue validation so the receipt cannot claim
    # cleanup that occurred before a later failed cleanup action.
    receipt = build_receipt(
        plan=plan,
        image_inspection=image_document,
        container_inspection=container_document,
        stdout=start_result.stdout,
        stderr_bytes=len(start_result.stderr),
        exit_code=start_result.exit_code,
        started_at=start_result.started_at,
        finished_at=start_result.finished_at,
        container_residue=residues[0],
        volume_residue=residues[1],
        network_residue=residues[2],
    )
    payload = _canonical_payload(receipt)
    _preflight_create_only_collision(
        paths["receipt"],
        payload,
        label="immutable image preflight receipt",
    )
    _publish_create_only_and_readback(
        paths["receipt"],
        payload,
        label="immutable image preflight receipt",
    )
    verification = verify_installed_receipt(
        operation_id=operation_id,
        role=role,
        install_root=install_root,
    )
    if verification["receipt_sha256"] != receipt["receipt_sha256"]:
        raise ImmutableImagePreflightContractError("installed receipt verification differs")
    return {
        "status": "completed-local-only",
        "receipt_path": os.fspath(paths["receipt"]),
        "receipt_sha256": receipt["receipt_sha256"],
        "verification": verification,
    }


def verify_installed_receipt(
    *, operation_id: str, role: str, install_root: Path | str = INSTALL_ROOT
) -> dict[str, Any]:
    """Verify a future receipt against the installed pair without publishing it."""

    plan, material, paths = load_installed_inputs(
        operation_id=operation_id,
        role=role,
        install_root=install_root,
    )
    receipt, _receipt_payload = _read_canonical_installed_document(
        paths["receipt"], label="immutable image preflight receipt"
    )
    return build_receipt_verification(plan=plan, material=material, receipt=receipt)
