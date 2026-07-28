#!/usr/bin/env python3
"""Install one frozen-final restore input closure without running a restore.

The command is plan-only by default.  It performs no Docker, SSH, network, or
Object Storage operation.  Apply is available only through the Python API with
an execution envelope, a dedicated fresh live-lease claim, and an on-demand
controller liveness verifier.  The CLI deliberately cannot supply that live
callback and therefore fails closed in apply mode.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tarfile
from typing import Any, Callable, Iterator, Mapping, Sequence

# The installer is executed from an immutable release.  Disable bytecode before
# importing any release module so validation cannot create ignored residue.
sys.dont_write_bytecode = True

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import (  # noqa: E402
    build_production_shadow_frozen_final_restore_set as RESTORE_SET,
)
from scripts import (  # noqa: E402
    build_production_shadow_cutover_manifest_template as TEMPLATE,
)
from scripts import (  # noqa: E402
    orchestrate_production_shadow_nginx_generations as NGINX,
)
from scripts import produce_production_shadow_prepare_material as PREPARE  # noqa: E402
from scripts import production_shadow_cutover_controller as CONTROLLER  # noqa: E402
from scripts import (  # noqa: E402
    production_shadow_frozen_final_restore_worker as WORKER,
)
from scripts.render_three_site_production_shadow_role_compose import (  # noqa: E402
    ProductionShadowRoleError,
    canonical_role_compose_bytes,
    parse_env_values,
    referenced_environment_names,
    render_role_compose,
    required_environment_names,
)


ROOT_UID = 0
ROOT_GID = 0
FILE_MODE = 0o600
PRIVATE_DIRECTORY_MODE = 0o700
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_ROLE_MATERIAL_BYTES = 64 * 1024 * 1024
MAX_RELEASE_FILE_BYTES = 16 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024 * 1024
ARTIFACT_FILENAMES = {
    "database-backup": "database.dump",
    "uploads-archive": "uploads.tar.gz",
    "audit-archive": "audit.tar.gz",
}
ROLE_MATERIAL_MEMBERS = frozenset(
    {
        PREPARE.FINAL_PREPARE_MANIFEST_NAME,
        "role-compose.yml",
        "runtime.env.role",
        "ca.crt",
    }
)
EXECUTION_ENVELOPE_SCHEMA = (
    "production-shadow-frozen-final-install-authority-v1"
)
EXECUTION_ENVELOPE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "owner_action",
        "intended_outcome",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "restore_set_sha256",
        "restore_generation_sha256",
        "target_roles",
        "legacy_frozen_receipt_path",
        "legacy_frozen_receipt_sha256",
        "historical_claim_sha256",
        "fresh_claim_path",
        "fresh_claim_sha256",
        "fresh_claim_epoch",
        "controller_live_verifier_required",
        "network_io_authorized",
        "object_storage_mutation_authorized",
    }
)
AUTHORITY_EVENT_SCHEMA = (
    "production-shadow-frozen-final-install-authority-event-v1"
)
INSTALLATION_ATTESTATION_SCHEMA = (
    "production-shadow-frozen-final-installation-attestation-v1"
)


class FrozenFinalRestoreInputInstallError(RuntimeError):
    """Raised when a restore-input installation cannot be proven safe."""


@dataclass(frozen=True)
class FileIdentity:
    path: Path
    sha256: str
    bytes: int
    device: int
    inode: int


@dataclass(frozen=True)
class ArtifactSource:
    kind: str
    identity: FileIdentity
    restored_tree_sha256: str | None


@dataclass(frozen=True)
class OutputSpec:
    kind: str
    path: Path
    sha256: str
    bytes: int
    payload: bytes | None = None
    source: Path | None = None


@dataclass(frozen=True)
class InstallationPlan:
    controller: Mapping[str, Any]
    controller_sha256: str
    restore_set: Mapping[str, Any]
    restore_set_sha256: str
    role: str
    source_role: str
    paths: WORKER.RuntimePaths
    role_manifest: Mapping[str, Any]
    role_manifest_payload: bytes
    role_manifest_sha256: str
    installer_receipt: Mapping[str, Any]
    installer_receipt_payload: bytes
    installer_receipt_sha256: str
    role_material_identity: FileIdentity
    artifact_sources: Mapping[str, ArtifactSource]
    outputs: tuple[OutputSpec, ...]
    transport_summary: Mapping[str, Any]
    expected_role: str | None


@dataclass(frozen=True)
class AuthorityBinding:
    envelope: Mapping[str, Any]
    claim: Mapping[str, Any]
    claim_sha256: str
    claim_epoch: int
    claim_nonce: str
    receipt: Mapping[str, Any]
    receipt_sha256: str


LiveAuthorityVerifier = Callable[
    [Mapping[str, Any], str], Mapping[str, Any]
]


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
        raise FrozenFinalRestoreInputInstallError(
            "document contains non-canonical JSON data"
        ) from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FrozenFinalRestoreInputInstallError(
                "JSON contains a duplicate field"
            )
        result[key] = value
    return result


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or WORKER.SHA256_RE.fullmatch(value) is None
        or value == WORKER.ZERO_SHA256
    ):
        raise FrozenFinalRestoreInputInstallError(
            f"{label} is not a nonzero SHA-256"
        )
    return value


def _bounded_int(
    value: Any,
    *,
    minimum: int,
    maximum: int,
    label: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise FrozenFinalRestoreInputInstallError(
            f"{label} is outside its bound"
        )
    return value


def _canonical_path(path: Path, *, label: str) -> Path:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path != Path(os.path.abspath(os.fspath(path)))
        or path.name in {"", ".", ".."}
        or ".." in path.parts
    ):
        raise FrozenFinalRestoreInputInstallError(
            f"{label} must be an absolute canonical path"
        )
    return path


def _stable_metadata(metadata: os.stat_result) -> tuple[int, ...]:
    return tuple(
        getattr(metadata, field)
        for field in (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
    )


def _directory_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise FrozenFinalRestoreInputInstallError(
            "secure no-follow directory traversal is unavailable"
        )
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | os.O_NOFOLLOW
    )


def _assert_root_directory(
    descriptor: int,
    *,
    label: str,
    exact_mode: int | None = None,
) -> os.stat_result:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != ROOT_UID
        or metadata.st_gid != ROOT_GID
        or (
            exact_mode is not None
            and stat.S_IMODE(metadata.st_mode) != exact_mode
        )
    ):
        raise FrozenFinalRestoreInputInstallError(
            f"{label} directory is unsafe"
        )
    return metadata


def _open_parent(
    path: Path,
    *,
    label: str,
    missing_ok: bool = False,
) -> tuple[int, str] | None:
    path = _canonical_path(path, label=label)
    descriptor = -1
    try:
        descriptor = os.open("/", _directory_flags())
        _assert_root_directory(descriptor, label=f"{label} root ancestor")
        for component in path.parts[1:-1]:
            try:
                child = os.open(
                    component,
                    _directory_flags(),
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                if missing_ok:
                    os.close(descriptor)
                    return None
                raise
            _assert_root_directory(
                child,
                label=f"{label} ancestor {component}",
            )
            os.close(descriptor)
            descriptor = child
        return descriptor, path.name
    except FrozenFinalRestoreInputInstallError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise FrozenFinalRestoreInputInstallError(
            f"{label} ancestor traversal is unsafe"
        ) from exc


def _open_root_directory(
    path: Path,
    *,
    label: str,
    exact_mode: int | None = None,
) -> int:
    opened = _open_parent(path, label=label)
    if opened is None:
        raise FrozenFinalRestoreInputInstallError(
            f"{label} directory is unavailable"
        )
    parent_fd, name = opened
    descriptor = -1
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent_fd)
        _assert_root_directory(
            descriptor,
            label=label,
            exact_mode=exact_mode,
        )
        return descriptor
    except FrozenFinalRestoreInputInstallError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise FrozenFinalRestoreInputInstallError(
            f"{label} directory is unavailable or unsafe"
        ) from exc
    finally:
        os.close(parent_fd)


def _read_secure_file(
    path: Path,
    *,
    label: str,
    maximum: int,
    allowed_modes: frozenset[int] = frozenset({FILE_MODE}),
) -> tuple[bytes, FileIdentity]:
    path = _canonical_path(path, label=label)
    opened = _open_parent(path, label=label)
    if opened is None:
        raise FrozenFinalRestoreInputInstallError(f"{label} is unavailable")
    directory_fd, name = opened
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != ROOT_UID
            or before.st_gid != ROOT_GID
            or stat.S_IMODE(before.st_mode) not in allowed_modes
            or before.st_nlink != 1
            or not 1 <= before.st_size <= maximum
        ):
            raise FrozenFinalRestoreInputInstallError(
                f"{label} is not an exact root-owned regular file"
            )
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        visible = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            len(payload) != before.st_size
            or len(payload) > maximum
            or _stable_metadata(before) != _stable_metadata(after)
            or _stable_metadata(after) != _stable_metadata(visible)
        ):
            raise FrozenFinalRestoreInputInstallError(
                f"{label} changed while being read"
            )
        return payload, FileIdentity(
            path=path,
            sha256=_sha256(payload),
            bytes=len(payload),
            device=after.st_dev,
            inode=after.st_ino,
        )
    except FrozenFinalRestoreInputInstallError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreInputInstallError(
            f"{label} is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


def _hash_secure_file(
    path: Path,
    *,
    label: str,
    maximum: int,
    expected_sha256: str | None = None,
    expected_bytes: int | None = None,
) -> FileIdentity:
    path = _canonical_path(path, label=label)
    opened = _open_parent(path, label=label)
    if opened is None:
        raise FrozenFinalRestoreInputInstallError(f"{label} is unavailable")
    directory_fd, name = opened
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != ROOT_UID
            or before.st_gid != ROOT_GID
            or stat.S_IMODE(before.st_mode) != FILE_MODE
            or before.st_nlink != 1
            or not 1 <= before.st_size <= maximum
            or (
                expected_bytes is not None
                and before.st_size != expected_bytes
            )
        ):
            raise FrozenFinalRestoreInputInstallError(
                f"{label} is not an exact root-only 0600 file"
            )
        digest = hashlib.sha256()
        observed = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            observed += len(chunk)
            if observed > maximum:
                raise FrozenFinalRestoreInputInstallError(
                    f"{label} exceeds its size bound"
                )
            digest.update(chunk)
        after = os.fstat(descriptor)
        visible = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        observed_sha256 = digest.hexdigest()
        if (
            observed != before.st_size
            or _stable_metadata(before) != _stable_metadata(after)
            or _stable_metadata(after) != _stable_metadata(visible)
            or (
                expected_sha256 is not None
                and observed_sha256 != expected_sha256
            )
            or (
                expected_bytes is not None
                and observed != expected_bytes
            )
        ):
            raise FrozenFinalRestoreInputInstallError(
                f"{label} identity differs"
            )
        return FileIdentity(
            path=path,
            sha256=observed_sha256,
            bytes=observed,
            device=after.st_dev,
            inode=after.st_ino,
        )
    except FrozenFinalRestoreInputInstallError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreInputInstallError(
            f"{label} is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


def _parse_canonical_json(
    payload: bytes,
    *,
    label: str,
) -> Mapping[str, Any]:
    try:
        document = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except FrozenFinalRestoreInputInstallError:
        raise
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise FrozenFinalRestoreInputInstallError(
            f"{label} is not strict JSON"
        ) from exc
    if not isinstance(document, dict) or payload != _canonical_json(document):
        raise FrozenFinalRestoreInputInstallError(
            f"{label} is not canonical JSON"
        )
    return document


def _read_canonical_json(
    path: Path,
    *,
    label: str,
) -> tuple[Mapping[str, Any], bytes, FileIdentity]:
    payload, identity = _read_secure_file(
        path,
        label=label,
        maximum=MAX_JSON_BYTES,
    )
    return _parse_canonical_json(payload, label=label), payload, identity


def _load_controller(
    path: Path,
) -> tuple[Mapping[str, Any], bytes, FileIdentity]:
    payload, identity = _read_secure_file(
        path,
        label="production cutover controller manifest",
        maximum=MAX_JSON_BYTES,
    )
    try:
        document, digest = CONTROLLER.read_root_only_manifest(
            path,
            owner_uid=ROOT_UID,
            max_size=MAX_JSON_BYTES,
        )
    except CONTROLLER.CutoverContractError as exc:
        raise FrozenFinalRestoreInputInstallError(
            "production cutover controller manifest is invalid"
        ) from exc
    if digest != identity.sha256 or payload != _canonical_json(document):
        raise FrozenFinalRestoreInputInstallError(
            "controller manifest canonical identity differs"
        )
    return document, payload, identity


def _validate_restore_set_closure(
    document: Mapping[str, Any],
    *,
    controller: Mapping[str, Any],
    controller_sha256: str,
) -> None:
    identity = {
        "campaign_id": controller["campaign_id"],
        "operation_id": controller["operation_id"],
        "release_sha": controller["release_sha"],
        "release_tree_sha": controller["release_tree_sha"],
        "legacy_release_sha": controller["legacy_release_sha"],
        "controller_manifest_sha256": controller_sha256,
        "approval_sha256": controller["artifacts"][
            "cutover_approval_sha256"
        ],
        "approval_policy_sha256": controller["artifacts"][
            "human_approval_policy_sha256"
        ],
    }
    if any(document.get(key) != value for key, value in identity.items()):
        raise FrozenFinalRestoreInputInstallError(
            "restore set and controller identities differ"
        )
    freeze = document.get("nginx_freeze")
    if (
        not isinstance(freeze, dict)
        or set(freeze) != RESTORE_SET.NGINX_FREEZE_FIELDS
        or freeze["state"] != "legacy-frozen"
        or freeze["global_generation_sha256"]
        != controller["artifacts"]["nginx_freeze_generation_sha256"]
        or set(freeze["role_generation_sha256"])
        != set(NGINX.ROLE_ORDER)
        or set(freeze["role_bindings"]) != set(NGINX.ROLE_ORDER)
        or type(freeze["journal_sequence"]) is not int
        or freeze["journal_sequence"] < 1
    ):
        raise FrozenFinalRestoreInputInstallError(
            "restore set legacy-frozen binding differs"
        )
    for field in (
        "aggregate_sha256",
        "state_receipt_sha256",
        "global_generation_sha256",
        "journal_sha256",
        "journal_tail_sha256",
        "external_readback_sha256",
    ):
        _nonzero_sha256(freeze[field], label=f"restore set {field}")
    for role, value in freeze["role_generation_sha256"].items():
        _nonzero_sha256(value, label=f"{role} Nginx generation")

    historical = document.get("snapshot_authorization_claim")
    if (
        not isinstance(historical, dict)
        or set(historical)
        != RESTORE_SET.SNAPSHOT_AUTHORIZATION_CLAIM_OUTPUT_FIELDS
        or historical["owner_action"] != "capture-frozen-final-snapshots"
        or historical["claim_document_status"] != "active"
        or historical["legacy_frozen_receipt_sha256"]
        != freeze["state_receipt_sha256"]
        or historical["receipt_journal_sha256"]
        != freeze["journal_sha256"]
        or historical["receipt_journal_sequence"]
        != freeze["journal_sequence"]
        or historical["receipt_journal_tail_sha256"]
        != freeze["journal_tail_sha256"]
        or historical["copied_material_authoritative"] is not False
        or historical["claim_liveness_asserted"] is not False
        or historical["future_install_or_restore_authority_implied"]
        is not False
        or historical[
            "fresh_live_authority_required_before_install_or_restore"
        ]
        is not True
    ):
        raise FrozenFinalRestoreInputInstallError(
            "historical snapshot claim is not provenance-only"
        )
    _nonzero_sha256(
        historical["claim_sha256"],
        label="historical snapshot claim",
    )
    if (
        type(historical["claim_epoch"]) is not int
        or historical["claim_epoch"] < 1
    ):
        raise FrozenFinalRestoreInputInstallError(
            "historical snapshot claim epoch is invalid"
        )

    constraints = document.get("constraints")
    if (
        not isinstance(constraints, dict)
        or set(constraints) != RESTORE_SET.CONSTRAINT_FIELDS
        or constraints["plan_only_default"] is not True
        or constraints["network_io_performed"] is not False
        or constraints["object_storage_contacted"] is not False
        or constraints["production_contacted"] is not False
        or constraints["installer_executed"] is not False
        or constraints["restore_worker_executed"] is not False
        or constraints["service_mutated"] is not False
        or constraints["current_mutated"] is not False
        or constraints["container_mutated"] is not False
        or constraints["volume_mutated"] is not False
        or constraints["data_mutated"] is not False
        or constraints["legacy_redis_restore_included"] is not False
        or constraints[
            "snapshot_authorization_claim_copy_is_not_live_authority"
        ]
        is not True
        or constraints[
            "snapshot_authorization_claim_liveness_asserted"
        ]
        is not False
        or constraints["future_install_or_restore_authority_implied"]
        is not False
        or constraints[
            "fresh_live_authority_required_before_install_or_restore"
        ]
        is not True
    ):
        raise FrozenFinalRestoreInputInstallError(
            "restore set fail-closed constraints differ"
        )


def _load_restore_set(
    path: Path,
    *,
    controller: Mapping[str, Any],
    controller_sha256: str,
) -> tuple[Mapping[str, Any], bytes, FileIdentity]:
    payload, identity = _read_secure_file(
        path,
        label="frozen-final restore set",
        maximum=MAX_JSON_BYTES,
    )
    if payload != _canonical_json(
        _parse_canonical_json(payload, label="frozen-final restore set")
    ):
        raise FrozenFinalRestoreInputInstallError(
            "restore set is not canonical"
        )
    try:
        document, digest = WORKER.load_restore_set(path)
    except WORKER.FrozenFinalRestoreWorkerError as exc:
        raise FrozenFinalRestoreInputInstallError(
            "frozen-final restore set is invalid"
        ) from exc
    if digest != identity.sha256:
        raise FrozenFinalRestoreInputInstallError(
            "restore set file digest and namespace differ"
        )
    _validate_restore_set_closure(
        document,
        controller=controller,
        controller_sha256=controller_sha256,
    )
    return document, payload, identity


def _safe_role_material_member(name: str) -> PurePosixPath:
    candidate = PurePosixPath(name)
    if (
        not isinstance(name, str)
        or not name
        or candidate.is_absolute()
        or candidate.as_posix() != name
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise FrozenFinalRestoreInputInstallError(
            "role material member path is unsafe"
        )
    return candidate


def _read_role_material_members(payload: bytes) -> Mapping[str, bytes]:
    members: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            rows = archive.getmembers()
            if (
                len(rows) != len(ROLE_MATERIAL_MEMBERS)
                or {row.name for row in rows} != ROLE_MATERIAL_MEMBERS
                or len({row.name for row in rows}) != len(rows)
            ):
                raise FrozenFinalRestoreInputInstallError(
                    "role material member closure is not exact"
                )
            for row in rows:
                _safe_role_material_member(row.name)
                if (
                    not row.isreg()
                    or row.uid != ROOT_UID
                    or row.gid != ROOT_GID
                    or stat.S_IMODE(row.mode) != FILE_MODE
                    or row.mtime != 0
                    or row.uname not in {"", None}
                    or row.gname not in {"", None}
                    or row.pax_headers
                    or not 1 <= row.size <= MAX_JSON_BYTES
                ):
                    raise FrozenFinalRestoreInputInstallError(
                        "role material member metadata is unsafe"
                    )
                stream = archive.extractfile(row)
                if stream is None:
                    raise FrozenFinalRestoreInputInstallError(
                        "role material member is unreadable"
                    )
                content = stream.read(MAX_JSON_BYTES + 1)
                if len(content) != row.size:
                    raise FrozenFinalRestoreInputInstallError(
                        "role material member size differs"
                    )
                members[row.name] = content
    except FrozenFinalRestoreInputInstallError:
        raise
    except (EOFError, OSError, tarfile.TarError) as exc:
        raise FrozenFinalRestoreInputInstallError(
            "role material archive is invalid"
        ) from exc
    return members


def _derive_restore_compose(
    canonical_payload: bytes,
    *,
    role: str,
) -> tuple[Mapping[str, Any], bytes]:
    try:
        canonical = yaml.safe_load(canonical_payload.decode("utf-8"))
        if not isinstance(canonical, dict):
            raise ProductionShadowRoleError(
                "canonical production Compose is not an object"
            )
        rendered = render_role_compose(
            canonical,
            role=WORKER.ROLE_PATHS[role],
            scope="prepare",
        )
    except (UnicodeError, yaml.YAMLError, ProductionShadowRoleError) as exc:
        raise FrozenFinalRestoreInputInstallError(
            "canonical restore Compose cannot be derived"
        ) from exc
    exact_services = {
        f"{role}_db",
        f"{role}_restore_tool",
    }
    rendered["services"] = {
        name: value
        for name, value in rendered["services"].items()
        if name in exact_services
    }
    role_network = role
    networks = rendered.get("networks")
    if (
        set(rendered["services"]) != exact_services
        or not isinstance(networks, dict)
        or role_network not in networks
    ):
        raise FrozenFinalRestoreInputInstallError(
            "canonical restore-only service closure differs"
        )
    rendered["networks"] = {role_network: networks[role_network]}
    rendered.pop("volumes", None)
    return rendered, canonical_role_compose_bytes(rendered)


def _expected_prepare_compose(
    canonical_payload: bytes,
    *,
    role: str,
) -> bytes:
    try:
        canonical = yaml.safe_load(canonical_payload.decode("utf-8"))
        if not isinstance(canonical, dict):
            raise ProductionShadowRoleError(
                "canonical production Compose is not an object"
            )
        rendered = render_role_compose(
            canonical,
            role=WORKER.ROLE_PATHS[role],
            scope="prepare",
        )
    except (UnicodeError, yaml.YAMLError, ProductionShadowRoleError) as exc:
        raise FrozenFinalRestoreInputInstallError(
            "prepare role Compose cannot be derived"
        ) from exc
    rendered["x-production-shadow-runtime-image-ids"] = dict(
        PREPARE.RUNTIME_IMAGE_COMPOSE_EXTENSION
    )
    return canonical_role_compose_bytes(rendered)


def _validate_role_environment(
    payload: bytes,
    *,
    internal: Mapping[str, Any],
    controller: Mapping[str, Any],
    role: str,
) -> None:
    try:
        values = parse_env_values(payload.decode("ascii"))
    except (UnicodeError, ProductionShadowRoleError) as exc:
        raise FrozenFinalRestoreInputInstallError(
            "role environment is invalid"
        ) from exc
    required_keys = internal.get("required_env_keys")
    if (
        not isinstance(required_keys, list)
        or required_keys != sorted(set(required_keys))
        or required_keys != sorted(values)
    ):
        raise FrozenFinalRestoreInputInstallError(
            "role environment key closure differs"
        )
    if any(
        fragment in name
        for name in values
        for fragment in PREPARE.FORBIDDEN_PREPARE_ENV_FRAGMENTS
    ):
        raise FrozenFinalRestoreInputInstallError(
            "role environment contains activation or provider material"
        )
    expected = PREPARE._operation_values(  # noqa: SLF001
        controller["operation_id"],
        controller["release_sha"],
    )
    expected.update(
        {
            PREPARE.IMAGE_ENV_BY_KIND[kind]: controller["artifacts"][
                "role_runtime_image_ids"
            ][role][kind]
            for kind in PREPARE.IMAGE_KINDS
        }
    )
    if any(values.get(key) != value for key, value in expected.items()):
        raise FrozenFinalRestoreInputInstallError(
            "role environment differs from immutable operation identity"
        )


def _load_role_material(
    path: Path,
    *,
    controller: Mapping[str, Any],
    canonical_compose_payload: bytes,
    expected_role: str | None,
) -> tuple[str, Mapping[str, bytes], FileIdentity]:
    payload, identity = _read_secure_file(
        path,
        label="production role material",
        maximum=MAX_ROLE_MATERIAL_BYTES,
    )
    candidates = [
        role
        for role in WORKER.ROLE_NAMES
        if controller["artifacts"]["role_materials"][role]["sha256"]
        == identity.sha256
        and controller["artifacts"]["role_materials"][role]["bytes"]
        == identity.bytes
    ]
    if len(candidates) != 1:
        raise FrozenFinalRestoreInputInstallError(
            "role material does not identify exactly one Docker role"
        )
    role = candidates[0]
    if expected_role is not None and expected_role != role:
        raise FrozenFinalRestoreInputInstallError(
            "caller role assertion differs from role material identity"
        )
    binding = controller["artifacts"]["role_materials"][role]
    if (
        binding["format"] != "production-shadow-role-material-tar"
        or binding["transport"]
        != controller["topology"][role]["transport"]
    ):
        raise FrozenFinalRestoreInputInstallError(
            "role material format or transport differs"
        )
    members = _read_role_material_members(payload)
    internal = _parse_canonical_json(
        members[PREPARE.FINAL_PREPARE_MANIFEST_NAME],
        label="role material internal manifest",
    )
    expected_schema = (
        PREPARE.WA_IR_FINAL_PREPARE_SCHEMA
        if role == "webapp_ir"
        else PREPARE.FI_FINAL_PREPARE_SCHEMA
    )
    if (
        set(internal) != PREPARE.FINAL_PREPARE_FIELDS
        or internal["schema"] != expected_schema
        or internal["operation_id"] != controller["operation_id"]
        or internal["release_sha"] != controller["release_sha"]
        or internal["role"] != role
        or internal["runtime_image_ids"]
        != controller["artifacts"]["role_runtime_image_ids"][role]
    ):
        raise FrozenFinalRestoreInputInstallError(
            "role material internal identity differs"
        )
    _nonzero_sha256(
        internal["operation_manifest_sha256"],
        label="role material operation manifest",
    )
    _nonzero_sha256(
        internal["stage_attestation_sha256"],
        label="role material stage attestation",
    )
    entries = internal.get("entries")
    if not isinstance(entries, list) or len(entries) != 3:
        raise FrozenFinalRestoreInputInstallError(
            "role material entry closure differs"
        )
    expected_destinations = {
        "role-compose.yml": (
            f"rendered/{WORKER.ROLE_PATHS[role]}/docker-compose.yml"
        ),
        "runtime.env.role": (
            f"secrets/{WORKER.ROLE_PATHS[role]}/runtime.env.role"
        ),
        "ca.crt": "secrets/tls/ca.crt",
    }
    rows: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != PREPARE.FINAL_PREPARE_ENTRY_FIELDS
            or not isinstance(entry.get("archive_path"), str)
            or entry["archive_path"] in rows
        ):
            raise FrozenFinalRestoreInputInstallError(
                "role material entry fields are not exact"
            )
        rows[entry["archive_path"]] = entry
    if set(rows) != set(expected_destinations):
        raise FrozenFinalRestoreInputInstallError(
            "role material entry names differ"
        )
    for name, destination in expected_destinations.items():
        member = members[name]
        row = rows[name]
        if (
            row["destination"] != destination
            or row["mode"] != "0600"
            or row["sha256"] != _sha256(member)
            or row["bytes"] != len(member)
        ):
            raise FrozenFinalRestoreInputInstallError(
                f"role material {name} binding differs"
            )
    if (
        members["role-compose.yml"]
        != _expected_prepare_compose(
            canonical_compose_payload,
            role=role,
        )
    ):
        raise FrozenFinalRestoreInputInstallError(
            "role material Compose differs from canonical release"
        )
    _validate_role_environment(
        members["runtime.env.role"],
        internal=internal,
        controller=controller,
        role=role,
    )
    ca = members["ca.crt"]
    if (
        ca.count(b"-----BEGIN CERTIFICATE-----") != 1
        or ca.count(b"-----END CERTIFICATE-----") != 1
        or b"PRIVATE KEY" in ca
    ):
        raise FrozenFinalRestoreInputInstallError(
            "role material CA payload is invalid"
        )
    return role, members, identity


def _validate_transport(
    *,
    role: str,
    restore_set: Mapping[str, Any],
    controller: Mapping[str, Any],
    controller_sha256: str,
    webapp_ir_transport_manifest: Path | None,
    webapp_ir_readback_receipt: Path | None,
) -> Mapping[str, Any]:
    mapping = restore_set["target_map"][role]
    topology = controller["topology"][role]
    if (
        mapping["transport"] != WORKER.ROLE_TRANSPORTS[role]
        or mapping["source_role"] not in RESTORE_SET.SOURCE_ROLES
    ):
        raise FrozenFinalRestoreInputInstallError(
            "target transport or source mapping differs"
        )
    if role == "bot_fi":
        if (
            webapp_ir_transport_manifest is not None
            or webapp_ir_readback_receipt is not None
            or topology["transport"] != "local-controller"
            or topology["ssh_user"] is not None
            or topology["ssh_port"] is not None
            or mapping["transport"] != "host-local-create-only"
        ):
            raise FrozenFinalRestoreInputInstallError(
                "Bot-FI transport metadata differs"
            )
        return {
            "mode": "host-local-create-only",
            "controller_transport": "local-controller",
            "host": topology["host"],
            "network_io_performed": False,
        }
    if role == "webapp_fi":
        if (
            webapp_ir_transport_manifest is not None
            or webapp_ir_readback_receipt is not None
            or topology["transport"] != "ssh-control"
            or topology["host"] != "65.109.220.59"
            or topology["ssh_user"] != "root"
            or topology["ssh_port"] != 37067
            or mapping["transport"] != "ssh-control"
        ):
            raise FrozenFinalRestoreInputInstallError(
                "WebApp-FI pinned SSH metadata differs"
            )
        return {
            "mode": "ssh-control",
            "host": "65.109.220.59",
            "ssh_user": "root",
            "ssh_port": 37067,
            "network_io_performed": False,
        }
    if (
        webapp_ir_transport_manifest is None
        or webapp_ir_readback_receipt is None
        or topology["transport"]
        != "ssh-control-object-storage-payload-only"
        or mapping["transport"] != "arvan-private-versioned-age"
    ):
        raise FrozenFinalRestoreInputInstallError(
            "WebApp-IR local transport evidence is incomplete"
        )
    try:
        observed = RESTORE_SET._load_ir_transport(  # noqa: SLF001
            webapp_ir_transport_manifest,
            webapp_ir_readback_receipt,
            controller=controller,
            controller_sha256=controller_sha256,
            webapp_fi_restore_input_sha256=restore_set["sources"][
                "webapp_fi"
            ]["restore_input_sha256"],
        )
    except RESTORE_SET.FrozenFinalRestoreSetError as exc:
        raise FrozenFinalRestoreInputInstallError(
            "WebApp-IR exact-version transport evidence is invalid"
        ) from exc
    expected = restore_set["webapp_ir_transport"]
    if (
        observed != expected
        or set(expected) != RESTORE_SET.IR_TRANSPORT_OUTPUT_FIELDS
        or expected["provider"] != "arvan-s3"
        or expected["private"] is not True
        or expected["versioned"] is not True
        or expected["encryption"] != "age"
        or expected["exact_version_readback_verified"] is not True
        or not isinstance(expected["version_id"], str)
        or not expected["version_id"]
        or expected["plaintext_restore_input_set_sha256"]
        != restore_set["sources"]["webapp_fi"]["restore_input_sha256"]
    ):
        raise FrozenFinalRestoreInputInstallError(
            "WebApp-IR VersionId or private/versioned/age binding differs"
        )
    return {
        "mode": "arvan-private-versioned-age",
        "provider": expected["provider"],
        "bucket": expected["bucket"],
        "object_key": expected["object_key"],
        "version_id": expected["version_id"],
        "recipient": expected["recipient"],
        "ciphertext_sha256": expected["ciphertext_sha256"],
        "readback_receipt_sha256": expected[
            "readback_receipt_sha256"
        ],
        "network_io_performed": False,
    }


def _redis_member_name(name: PurePosixPath) -> bool:
    for component in name.parts:
        lowered = component.lower()
        if (
            lowered == "redis"
            or lowered.startswith("redis-")
            or lowered.startswith("redis_")
            or lowered in {"dump.rdb", "appendonly.aof"}
            or lowered.endswith((".rdb", ".aof"))
        ):
            return True
    return False


def _validate_snapshot_archive(
    path: Path,
    *,
    identity: FileIdentity,
    label: str,
) -> None:
    opened = _open_parent(path, label=label)
    if opened is None:
        raise FrozenFinalRestoreInputInstallError(f"{label} is unavailable")
    directory_fd, name = opened
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != ROOT_UID
            or before.st_gid != ROOT_GID
            or stat.S_IMODE(before.st_mode) != FILE_MODE
            or before.st_nlink != 1
            or before.st_size != identity.bytes
            or (before.st_dev, before.st_ino)
            != (identity.device, identity.inode)
        ):
            raise FrozenFinalRestoreInputInstallError(
                f"{label} archive identity changed"
            )
        names: set[str] = set()
        directories: set[str] = set()
        member_count = 0
        expanded_bytes = 0
        digest = hashlib.sha256()
        with os.fdopen(os.dup(descriptor), "rb") as hash_stream:
            while True:
                chunk = hash_stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        os.lseek(descriptor, 0, os.SEEK_SET)
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            with tarfile.open(fileobj=stream, mode="r:gz") as archive:
                for member in archive:
                    member_count += 1
                    expanded_bytes += member.size
                    try:
                        normalized, parts = WORKER._safe_member_path(  # noqa: SLF001
                            member.name
                        )
                    except WORKER.FrozenFinalRestoreWorkerError as exc:
                        raise FrozenFinalRestoreInputInstallError(
                            f"{label} contains an unsafe member path"
                        ) from exc
                    candidate = PurePosixPath(*parts)
                    parent = PurePosixPath(*parts[:-1]).as_posix()
                    if parent == ".":
                        parent = ""
                    mode = stat.S_IMODE(member.mode)
                    if (
                        member_count > WORKER.MAX_TAR_MEMBERS
                        or expanded_bytes > MAX_ARTIFACT_BYTES
                        or normalized in names
                        or (parent and parent not in directories)
                        or _redis_member_name(candidate)
                        or member.issym()
                        or member.islnk()
                        or member.isdev()
                        or member.isfifo()
                        or not (member.isdir() or member.isfile())
                        or member.uid != ROOT_UID
                        or member.gid != ROOT_GID
                        or member.mtime != 0
                        or member.uname not in {"", None}
                        or member.gname not in {"", None}
                        or member.pax_headers
                        or bool(mode & 0o6022)
                        or (member.isdir() and member.size != 0)
                    ):
                        raise FrozenFinalRestoreInputInstallError(
                            f"{label} contains an unsafe or Redis member"
                        )
                    names.add(normalized)
                    if member.isdir():
                        directories.add(normalized)
        after = os.fstat(descriptor)
        visible = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            not names
            or digest.hexdigest() != identity.sha256
            or _stable_metadata(before) != _stable_metadata(after)
            or _stable_metadata(after) != _stable_metadata(visible)
        ):
            raise FrozenFinalRestoreInputInstallError(
                f"{label} changed during archive validation"
            )
    except FrozenFinalRestoreInputInstallError:
        raise
    except (EOFError, OSError, tarfile.TarError) as exc:
        raise FrozenFinalRestoreInputInstallError(
            f"{label} archive is invalid"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_input_paths(
    paths: Sequence[Path],
    *,
    operation_id: str,
    outputs: Sequence[Path],
) -> None:
    if len(set(paths)) != len(paths):
        raise FrozenFinalRestoreInputInstallError(
            "installer input paths must be distinct"
        )
    prohibited_components = {"current", "staging", ".staging"}
    for path in paths:
        if prohibited_components & set(path.parts):
            raise FrozenFinalRestoreInputInstallError(
                "installer input overlaps current or staging"
            )
    if len(set(outputs)) != len(outputs):
        raise FrozenFinalRestoreInputInstallError(
            "installer output paths must be distinct"
        )
    if any(path in set(paths) for path in outputs):
        raise FrozenFinalRestoreInputInstallError(
            "installer output aliases an input path"
        )
    rehearsal_root = WORKER.DATA_ROOT_PREFIX / operation_id
    artifact_inputs = paths[-3:]
    if any(_is_relative_to(path, rehearsal_root) for path in artifact_inputs):
        raise FrozenFinalRestoreInputInstallError(
            "frozen-final inputs overlap a rehearsal data namespace"
        )
    for output in outputs:
        if (
            "current" in output.parts
            or "staging" in output.parts
            or not (
                _is_relative_to(output, WORKER.DATA_ROOT_PREFIX)
                or _is_relative_to(output, WORKER.SECRET_ROOT_PREFIX)
            )
        ):
            raise FrozenFinalRestoreInputInstallError(
                "installer output escapes the isolated generation"
            )


def _load_artifacts(
    *,
    restore_set: Mapping[str, Any],
    role: str,
    database_backup: Path,
    uploads_archive: Path,
    audit_archive: Path,
) -> Mapping[str, ArtifactSource]:
    source_role = restore_set["target_map"][role]["source_role"]
    source = restore_set["sources"][source_role]
    supplied = {
        "database-backup": database_backup,
        "uploads-archive": uploads_archive,
        "audit-archive": audit_archive,
    }
    result: dict[str, ArtifactSource] = {}
    for kind in WORKER.ARTIFACT_KINDS:
        path = _canonical_path(
            supplied[kind],
            label=f"{kind} input",
        )
        if path.name != ARTIFACT_FILENAMES[kind]:
            raise FrozenFinalRestoreInputInstallError(
                f"{kind} filename differs"
            )
        row = source["artifacts"][kind]
        identity = _hash_secure_file(
            path,
            label=f"{kind} input",
            maximum=MAX_ARTIFACT_BYTES,
            expected_sha256=row["sha256"],
            expected_bytes=row["bytes"],
        )
        if kind != "database-backup":
            _validate_snapshot_archive(
                path,
                identity=identity,
                label=f"{kind} input",
            )
        result[kind] = ArtifactSource(
            kind=kind,
            identity=identity,
            restored_tree_sha256=row["restored_tree_sha256"],
        )
    physical = {
        (artifact.identity.device, artifact.identity.inode)
        for artifact in result.values()
    }
    if len(physical) != len(result):
        raise FrozenFinalRestoreInputInstallError(
            "restore artifacts must be physically distinct"
        )
    return result


def _assert_existing_root_directory(
    path: Path,
    *,
    label: str,
    exact_mode: int | None = None,
) -> None:
    descriptor = _open_root_directory(
        path,
        label=label,
        exact_mode=exact_mode,
    )
    os.close(descriptor)


def _assert_root_contract(
    *,
    controller: Mapping[str, Any],
    role: str,
    paths: WORKER.RuntimePaths,
    canonical_compose: Path,
    role_material: Path,
    worker: Path,
) -> None:
    prefixes = (
        WORKER.PROJECT_ROOT_PREFIX,
        WORKER.DATA_ROOT_PREFIX,
        WORKER.SECRET_ROOT_PREFIX,
    )
    for index, prefix in enumerate(prefixes):
        _canonical_path(prefix, label=f"runtime prefix {index + 1}")
        _assert_existing_root_directory(
            prefix,
            label=f"runtime prefix {index + 1}",
        )
    for index, first in enumerate(prefixes):
        for second in prefixes[index + 1 :]:
            if first == second or first in second.parents or second in first.parents:
                raise FrozenFinalRestoreInputInstallError(
                    "runtime root prefixes must be disjoint"
                )
    expected_project_root = (
        WORKER.PROJECT_ROOT_PREFIX / controller["operation_id"]
    )
    if (
        paths.project_root != expected_project_root
        or paths.release_root
        != expected_project_root
        / "releases"
        / controller["release_sha"]
        or canonical_compose
        != paths.release_root / TEMPLATE.CANONICAL_COMPOSE_RELATIVE_PATH
        or worker
        != paths.release_root
        / "scripts"
        / "production_shadow_frozen_final_restore_worker.py"
        or role_material
        != paths.project_root
        / "incoming"
        / PREPARE.ROLE_ARCHIVE_NAMES[role]
    ):
        raise FrozenFinalRestoreInputInstallError(
            "release, Compose, worker, or role material path is not immutable"
        )
    for directory, label in (
        (paths.project_root, "operation project root"),
        (paths.project_root / "incoming", "operation incoming root"),
        (paths.release_root, "immutable release root"),
    ):
        _assert_existing_root_directory(directory, label=label)
    if (
        paths.data_generation_root
        != WORKER.DATA_ROOT_PREFIX
        / controller["operation_id"]
        / "frozen-final-generations"
        / paths.data_generation_root.name
        or paths.secret_generation_root
        != WORKER.SECRET_ROOT_PREFIX
        / controller["operation_id"]
        / "frozen-final-generations"
        / paths.data_generation_root.name
        / WORKER.ROLE_PATHS[role]
        or paths.prepare_compose
        != paths.secret_generation_root / "docker-compose.prepare.yml"
        or paths.ca
        != paths.secret_generation_root.parent / "tls" / "ca.crt"
    ):
        raise FrozenFinalRestoreInputInstallError(
            "generation roots are not digest-derived"
        )


def _run_release_probe(
    arguments: Sequence[str],
    *,
    cwd: Path,
    label: str,
) -> str:
    try:
        result = subprocess.run(
            list(arguments),
            cwd=cwd,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/root",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FrozenFinalRestoreInputInstallError(
            f"{label} probe is unavailable"
        ) from exc
    if (
        result.returncode != 0
        or len(result.stdout) > MAX_JSON_BYTES
        or len(result.stderr) > MAX_JSON_BYTES
    ):
        raise FrozenFinalRestoreInputInstallError(
            f"{label} probe failed closed"
        )
    try:
        return result.stdout.decode("utf-8").strip()
    except UnicodeError as exc:
        raise FrozenFinalRestoreInputInstallError(
            f"{label} probe returned invalid output"
        ) from exc


def _verify_immutable_release(
    *,
    paths: WORKER.RuntimePaths,
    controller: Mapping[str, Any],
    canonical_compose: Path,
    worker: Path,
) -> None:
    git = "/usr/bin/git"
    head = _run_release_probe(
        (git, "rev-parse", "--verify", "HEAD"),
        cwd=paths.release_root,
        label="release HEAD",
    )
    tree = _run_release_probe(
        (git, "rev-parse", "--verify", "HEAD^{tree}"),
        cwd=paths.release_root,
        label="release tree",
    )
    status = _run_release_probe(
        (
            git,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ),
        cwd=paths.release_root,
        label="release cleanliness",
    )
    branch = _run_release_probe(
        (git, "branch", "--show-current"),
        cwd=paths.release_root,
        label="release detached HEAD",
    )
    ignored = _run_release_probe(
        (
            git,
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
        ),
        cwd=paths.release_root,
        label="release ignored residue",
    )
    relative_compose = canonical_compose.relative_to(paths.release_root)
    relative_worker = worker.relative_to(paths.release_root)
    tracked_rows = _run_release_probe(
        (
            git,
            "ls-files",
            "--stage",
            "--",
            relative_compose.as_posix(),
            relative_worker.as_posix(),
        ),
        cwd=paths.release_root,
        label="release tracked-file",
    ).splitlines()
    tracked: dict[str, str] = {}
    for row in tracked_rows:
        metadata, separator, name = row.partition("\t")
        fields = metadata.split()
        if (
            not separator
            or len(fields) != 3
            or fields[2] != "0"
            or name in tracked
        ):
            raise FrozenFinalRestoreInputInstallError(
                "release tracked-file index entry is invalid"
            )
        tracked[name] = fields[0]
    if (
        head != controller["release_sha"]
        or tree != controller["release_tree_sha"]
        or status
        or branch
        or ignored
        or tracked.get(relative_compose.as_posix()) != "100644"
        or tracked.get(relative_worker.as_posix())
        not in {"100644", "100755"}
        or set(tracked)
        != {relative_compose.as_posix(), relative_worker.as_posix()}
    ):
        raise FrozenFinalRestoreInputInstallError(
            "release HEAD, tree, cleanliness, or tracked files differ"
        )
    help_output = _run_release_probe(
        (sys.executable, "-I", os.fspath(worker), "--help"),
        cwd=paths.release_root,
        label="release worker import",
    )
    if "frozen-final" not in help_output.lower():
        raise FrozenFinalRestoreInputInstallError(
            "release worker import proof is not recognizable"
        )


def _build_documents(
    *,
    controller: Mapping[str, Any],
    controller_sha256: str,
    controller_payload: bytes,
    restore_set: Mapping[str, Any],
    restore_set_sha256: str,
    restore_set_payload: bytes,
    role: str,
    paths: WORKER.RuntimePaths,
    canonical_compose_payload: bytes,
    role_compose_payload: bytes,
    prepare_compose_payload: bytes,
    ca_payload: bytes,
    environment_payload: bytes,
    worker_payload: bytes,
    target_migration_revision: str,
    artifacts: Mapping[str, ArtifactSource],
) -> tuple[
    Mapping[str, Any],
    bytes,
    str,
    Mapping[str, Any],
    bytes,
    str,
    tuple[OutputSpec, ...],
]:
    controller_destination = (
        paths.secret_generation_root / "controller-manifest.json"
    )
    restore_set_destination = (
        paths.secret_generation_root / "frozen-final-restore-set.json"
    )
    canonical_compose_destination = (
        paths.secret_generation_root / "canonical-compose.yml"
    )
    role_compose_destination = (
        paths.secret_generation_root / "docker-compose.restore.yml"
    )
    prepare_compose_destination = paths.prepare_compose
    ca_destination = paths.ca
    environment_destination = (
        paths.secret_generation_root / "runtime.env.role"
    )
    worker_destination = (
        paths.release_root
        / "scripts"
        / "production_shadow_frozen_final_restore_worker.py"
    )
    receipt_destination = (
        paths.secret_generation_root / "installer-receipt.json"
    )
    manifest_destination = (
        paths.secret_generation_root / "restore-role-manifest.json"
    )
    artifact_rows: dict[str, Mapping[str, Any]] = {}
    artifact_specs: list[OutputSpec] = []
    for kind in WORKER.ARTIFACT_KINDS:
        artifact = artifacts[kind]
        destination = (
            paths.restore_input_root / ARTIFACT_FILENAMES[kind]
        )
        artifact_rows[kind] = {
            "path": os.fspath(destination),
            "sha256": artifact.identity.sha256,
            "bytes": artifact.identity.bytes,
            "restored_tree_sha256": artifact.restored_tree_sha256,
        }
        artifact_specs.append(
            OutputSpec(
                kind=kind,
                path=destination,
                sha256=artifact.identity.sha256,
                bytes=artifact.identity.bytes,
                source=artifact.identity.path,
            )
        )

    ordinary_specs = [
        OutputSpec(
            kind="controller-manifest",
            path=controller_destination,
            sha256=controller_sha256,
            bytes=len(controller_payload),
            payload=controller_payload,
        ),
        OutputSpec(
            kind="restore-set",
            path=restore_set_destination,
            sha256=restore_set_sha256,
            bytes=len(restore_set_payload),
            payload=restore_set_payload,
        ),
        OutputSpec(
            kind="canonical-compose",
            path=canonical_compose_destination,
            sha256=_sha256(canonical_compose_payload),
            bytes=len(canonical_compose_payload),
            payload=canonical_compose_payload,
        ),
        OutputSpec(
            kind="role-compose",
            path=role_compose_destination,
            sha256=_sha256(role_compose_payload),
            bytes=len(role_compose_payload),
            payload=role_compose_payload,
        ),
        OutputSpec(
            kind="prepare-compose",
            path=prepare_compose_destination,
            sha256=_sha256(prepare_compose_payload),
            bytes=len(prepare_compose_payload),
            payload=prepare_compose_payload,
        ),
        OutputSpec(
            kind="ca",
            path=ca_destination,
            sha256=_sha256(ca_payload),
            bytes=len(ca_payload),
            payload=ca_payload,
        ),
        OutputSpec(
            kind="environment",
            path=environment_destination,
            sha256=_sha256(environment_payload),
            bytes=len(environment_payload),
            payload=environment_payload,
        ),
        *artifact_specs,
    ]
    installed_files = {
        spec.kind: {
            "path": os.fspath(spec.path),
            "sha256": spec.sha256,
            "bytes": spec.bytes,
        }
        for spec in ordinary_specs
    }
    # The worker remains inside the immutable release so its relative imports
    # resolve against the complete release tree.  The receipt attests that
    # release file; it does not claim the installer copied it.
    installed_files["worker"] = {
        "path": os.fspath(worker_destination),
        "sha256": _sha256(worker_payload),
        "bytes": len(worker_payload),
    }
    if set(installed_files) != WORKER.INSTALLER_FILE_NAMES:
        raise FrozenFinalRestoreInputInstallError(
            "installer file closure differs from worker contract"
        )
    source_role = restore_set["target_map"][role]["source_role"]
    common = {
        "campaign_id": controller["campaign_id"],
        "operation_id": controller["operation_id"],
        "role": role,
        "release_sha": controller["release_sha"],
        "release_tree_sha": controller["release_tree_sha"],
        "controller_manifest_sha256": controller_sha256,
        "restore_set_sha256": restore_set_sha256,
        "restore_generation_sha256": restore_set[
            "restore_generation_sha256"
        ],
        "source_role": source_role,
        "target_transport": restore_set["target_map"][role]["transport"],
        "app_image_id": controller["artifacts"][
            "role_runtime_image_ids"
        ][role]["app"],
        "app_image_content_identity": controller["artifacts"][
            "image_artifacts"
        ]["app"]["content_identity"],
        "target_migration_revision": target_migration_revision,
        "data_generation_root": os.fspath(paths.data_generation_root),
        "secret_generation_root": os.fspath(paths.secret_generation_root),
    }
    receipt = {
        "schema": WORKER.INSTALLER_RECEIPT_SCHEMA,
        "status": "installed",
        **common,
        "installed_files": installed_files,
        "redis_restore_bytes": 0,
        "current_mutated": False,
        "legacy_mutated": False,
        "object_storage_mutated": False,
    }
    if set(receipt) != WORKER.INSTALLER_RECEIPT_FIELDS:
        raise FrozenFinalRestoreInputInstallError(
            "installer receipt fields differ from worker contract"
        )
    receipt_payload = _canonical_json(receipt)
    receipt_sha256 = _sha256(receipt_payload)
    constraints = {
        name: True for name in WORKER.CONSTRAINT_FIELDS
    }
    role_manifest = {
        "schema": WORKER.ROLE_MANIFEST_SCHEMA,
        "status": "installed",
        **{
            key: common[key]
            for key in (
                "campaign_id",
                "operation_id",
                "role",
                "release_sha",
                "release_tree_sha",
                "controller_manifest_sha256",
                "restore_set_sha256",
                "restore_generation_sha256",
                "source_role",
                "target_transport",
            )
        },
        "controller_manifest_path": os.fspath(controller_destination),
        "restore_set_path": os.fspath(restore_set_destination),
        "legacy_frozen_receipt_sha256": restore_set["nginx_freeze"][
            "state_receipt_sha256"
        ],
        "snapshot_authorization_claim_sha256": restore_set[
            "snapshot_authorization_claim"
        ]["claim_sha256"],
        "installer_receipt_path": os.fspath(receipt_destination),
        "installer_receipt_sha256": receipt_sha256,
        "canonical_compose_path": os.fspath(
            canonical_compose_destination
        ),
        "canonical_compose_sha256": _sha256(
            canonical_compose_payload
        ),
        "role_compose_path": os.fspath(role_compose_destination),
        "role_compose_sha256": _sha256(role_compose_payload),
        "prepare_compose_path": os.fspath(
            prepare_compose_destination
        ),
        "prepare_compose_sha256": _sha256(
            prepare_compose_payload
        ),
        "ca_path": os.fspath(ca_destination),
        "ca_sha256": _sha256(ca_payload),
        "environment_path": os.fspath(environment_destination),
        "environment_sha256": _sha256(environment_payload),
        "worker_path": os.fspath(worker_destination),
        "worker_sha256": _sha256(worker_payload),
        "release_root": os.fspath(paths.release_root),
        "project_base": paths.project_base,
        "project_name": paths.project_name,
        "data_generation_root": os.fspath(paths.data_generation_root),
        "secret_generation_root": os.fspath(
            paths.secret_generation_root
        ),
        "postgres_image_id": controller["artifacts"][
            "role_runtime_image_ids"
        ][role]["postgres"],
        "postgres_image_content_identity": controller["artifacts"][
            "image_artifacts"
        ]["postgres"]["content_identity"],
        "app_image_id": common["app_image_id"],
        "app_image_content_identity": common[
            "app_image_content_identity"
        ],
        "target_migration_revision": target_migration_revision,
        "postgres_runtime_uid": controller["artifacts"][
            "postgres_runtime_uid"
        ],
        "postgres_runtime_gid": controller["artifacts"][
            "postgres_runtime_gid"
        ],
        "artifacts": artifact_rows,
        "source_database": dict(
            restore_set["sources"][source_role]["source_database"]
        ),
        "constraints": constraints,
    }
    if set(role_manifest) != WORKER.ROLE_MANIFEST_FIELDS:
        raise FrozenFinalRestoreInputInstallError(
            "role manifest fields differ from worker contract"
        )
    role_manifest_payload = _canonical_json(role_manifest)
    role_manifest_sha256 = _sha256(role_manifest_payload)
    outputs = (
        *ordinary_specs,
        OutputSpec(
            kind="installer-receipt",
            path=receipt_destination,
            sha256=receipt_sha256,
            bytes=len(receipt_payload),
            payload=receipt_payload,
        ),
        OutputSpec(
            kind="role-manifest",
            path=manifest_destination,
            sha256=role_manifest_sha256,
            bytes=len(role_manifest_payload),
            payload=role_manifest_payload,
        ),
    )
    return (
        role_manifest,
        role_manifest_payload,
        role_manifest_sha256,
        receipt,
        receipt_payload,
        receipt_sha256,
        outputs,
    )


def preflight_installation(
    *,
    controller_manifest: Path,
    restore_set: Path,
    role_material: Path,
    database_backup: Path,
    uploads_archive: Path,
    audit_archive: Path,
    canonical_compose: Path,
    worker: Path,
    expected_role: str | None = None,
    webapp_ir_transport_manifest: Path | None = None,
    webapp_ir_readback_receipt: Path | None = None,
) -> InstallationPlan:
    if os.geteuid() != ROOT_UID or os.getegid() != ROOT_GID:
        raise FrozenFinalRestoreInputInstallError(
            "frozen-final input installer must run as root:root"
        )
    if expected_role is not None and expected_role not in WORKER.ROLE_NAMES:
        raise FrozenFinalRestoreInputInstallError(
            "caller role assertion is invalid"
        )
    controller_manifest = _canonical_path(
        controller_manifest,
        label="controller manifest",
    )
    restore_set = _canonical_path(
        restore_set,
        label="restore set",
    )
    role_material = _canonical_path(
        role_material,
        label="role material",
    )
    canonical_compose = _canonical_path(
        canonical_compose,
        label="canonical Compose",
    )
    worker = _canonical_path(worker, label="restore worker")
    (
        controller,
        controller_payload,
        controller_identity,
    ) = _load_controller(controller_manifest)
    (
        restore_document,
        restore_payload,
        restore_identity,
    ) = _load_restore_set(
        restore_set,
        controller=controller,
        controller_sha256=controller_identity.sha256,
    )
    canonical_payload, canonical_identity = _read_secure_file(
        canonical_compose,
        label="canonical production shadow Compose",
        maximum=MAX_RELEASE_FILE_BYTES,
        allowed_modes=frozenset({0o600, 0o644}),
    )
    if (
        canonical_identity.sha256
        != controller["artifacts"]["shadow_compose_sha256"]
    ):
        raise FrozenFinalRestoreInputInstallError(
            "canonical Compose digest differs from controller"
        )
    try:
        PREPARE._validate_canonical_compose(  # noqa: SLF001
            canonical_payload,
            expected_sha256=canonical_identity.sha256,
        )
    except PREPARE.PrepareMaterialError as exc:
        raise FrozenFinalRestoreInputInstallError(
            "canonical production Compose contract is invalid"
        ) from exc
    role, role_members, role_material_identity = _load_role_material(
        role_material,
        controller=controller,
        canonical_compose_payload=canonical_payload,
        expected_role=expected_role,
    )
    paths = WORKER.runtime_paths(
        controller["operation_id"],
        controller["release_sha"],
        restore_document["restore_generation_sha256"],
        role,
    )
    _assert_root_contract(
        controller=controller,
        role=role,
        paths=paths,
        canonical_compose=canonical_compose,
        role_material=role_material,
        worker=worker,
    )
    _verify_immutable_release(
        paths=paths,
        controller=controller,
        canonical_compose=canonical_compose,
        worker=worker,
    )
    try:
        target_migration_revision = WORKER.target_migration_revision(
            paths.release_root
        )
    except WORKER.FrozenFinalRestoreWorkerError as exc:
        raise FrozenFinalRestoreInputInstallError(
            "immutable release target migration revision is invalid"
        ) from exc
    worker_payload, worker_identity = _read_secure_file(
        worker,
        label="immutable frozen-final restore worker",
        maximum=MAX_RELEASE_FILE_BYTES,
        allowed_modes=frozenset({0o644, 0o755}),
    )
    transport_summary = _validate_transport(
        role=role,
        restore_set=restore_document,
        controller=controller,
        controller_sha256=controller_identity.sha256,
        webapp_ir_transport_manifest=webapp_ir_transport_manifest,
        webapp_ir_readback_receipt=webapp_ir_readback_receipt,
    )
    artifact_sources = _load_artifacts(
        restore_set=restore_document,
        role=role,
        database_backup=database_backup,
        uploads_archive=uploads_archive,
        audit_archive=audit_archive,
    )
    _restore_document, role_compose_payload = _derive_restore_compose(
        canonical_payload,
        role=role,
    )
    (
        role_manifest_document,
        role_manifest_payload,
        role_manifest_sha256,
        installer_receipt,
        installer_receipt_payload,
        installer_receipt_sha256,
        outputs,
    ) = _build_documents(
        controller=controller,
        controller_sha256=controller_identity.sha256,
        controller_payload=controller_payload,
        restore_set=restore_document,
        restore_set_sha256=restore_identity.sha256,
        restore_set_payload=restore_payload,
        role=role,
        paths=paths,
        canonical_compose_payload=canonical_payload,
        role_compose_payload=role_compose_payload,
        prepare_compose_payload=role_members["role-compose.yml"],
        ca_payload=role_members["ca.crt"],
        environment_payload=role_members["runtime.env.role"],
        worker_payload=worker_payload,
        target_migration_revision=target_migration_revision,
        artifacts=artifact_sources,
    )
    input_paths = [
        controller_manifest,
        restore_set,
        canonical_compose,
        role_material,
        worker,
    ]
    if webapp_ir_transport_manifest is not None:
        input_paths.append(
            _canonical_path(
                webapp_ir_transport_manifest,
                label="WebApp-IR transport manifest",
            )
        )
    if webapp_ir_readback_receipt is not None:
        input_paths.append(
            _canonical_path(
                webapp_ir_readback_receipt,
                label="WebApp-IR readback receipt",
            )
        )
    input_paths.extend(
        (
            _canonical_path(database_backup, label="database backup"),
            _canonical_path(uploads_archive, label="uploads archive"),
            _canonical_path(audit_archive, label="audit archive"),
        )
    )
    _validate_input_paths(
        input_paths,
        operation_id=controller["operation_id"],
        outputs=[spec.path for spec in outputs],
    )
    core_identities = (
        controller_identity,
        restore_identity,
        canonical_identity,
        role_material_identity,
        worker_identity,
        *(artifact.identity for artifact in artifact_sources.values()),
    )
    physical = {
        (identity.device, identity.inode) for identity in core_identities
    }
    if len(physical) != len(core_identities):
        raise FrozenFinalRestoreInputInstallError(
            "installer inputs must be physically distinct"
        )
    _inspect_generation_residue(
        paths=paths,
        outputs=outputs,
        source_identities=frozenset(physical),
    )
    return InstallationPlan(
        controller=controller,
        controller_sha256=controller_identity.sha256,
        restore_set=restore_document,
        restore_set_sha256=restore_identity.sha256,
        role=role,
        source_role=restore_document["target_map"][role]["source_role"],
        paths=paths,
        role_manifest=role_manifest_document,
        role_manifest_payload=role_manifest_payload,
        role_manifest_sha256=role_manifest_sha256,
        installer_receipt=installer_receipt,
        installer_receipt_payload=installer_receipt_payload,
        installer_receipt_sha256=installer_receipt_sha256,
        role_material_identity=role_material_identity,
        artifact_sources=artifact_sources,
        outputs=outputs,
        transport_summary=transport_summary,
        expected_role=expected_role,
    )


def _partial_name(spec: OutputSpec) -> str:
    basis = (
        spec.kind.encode("ascii")
        + b"\0"
        + os.fsencode(spec.path.name)
        + b"\0"
        + spec.sha256.encode("ascii")
        + b"\0"
        + str(spec.bytes).encode("ascii")
    )
    return (
        ".frozen-final-install-"
        + hashlib.sha256(basis).hexdigest()[:40]
        + ".partial"
    )


def _authority_boundary_token(value: str) -> str:
    try:
        encoded = value.encode("ascii")
    except UnicodeError as exc:
        raise FrozenFinalRestoreInputInstallError(
            "authority boundary label is not ASCII"
        ) from exc
    slug = "".join(
        character
        if character in "abcdefghijklmnopqrstuvwxyz0123456789._-"
        else "-"
        for character in value.lower()
    )
    slug = "-".join(part for part in slug.split("-") if part)
    if len(slug) > 180:
        slug = slug[:180].rstrip("-")
    if not slug:
        slug = "boundary"
    return f"{slug}-{hashlib.sha256(encoded).hexdigest()[:16]}"


def _leaf_exists(directory_fd: int, name: str, *, label: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise FrozenFinalRestoreInputInstallError(
            f"{label} cannot be inspected"
        ) from exc
    return True


def _leaf_identity(
    directory_fd: int,
    name: str,
    *,
    path: Path,
    label: str,
    expected_sha256: str,
    expected_bytes: int,
    allowed_links: frozenset[int],
) -> FileIdentity:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != ROOT_UID
            or before.st_gid != ROOT_GID
            or stat.S_IMODE(before.st_mode) != FILE_MODE
            or before.st_nlink not in allowed_links
            or before.st_size != expected_bytes
        ):
            raise FrozenFinalRestoreInputInstallError(
                f"{label} is unsafe"
            )
        digest = hashlib.sha256()
        observed = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            observed += len(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
        visible = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            observed != expected_bytes
            or digest.hexdigest() != expected_sha256
            or _stable_metadata(before) != _stable_metadata(after)
            or _stable_metadata(after) != _stable_metadata(visible)
        ):
            raise FrozenFinalRestoreInputInstallError(
                f"{label} identity differs"
            )
        return FileIdentity(
            path=path,
            sha256=expected_sha256,
            bytes=expected_bytes,
            device=after.st_dev,
            inode=after.st_ino,
        )
    except FrozenFinalRestoreInputInstallError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreInputInstallError(
            f"{label} is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _partial_prefix_identity(
    directory_fd: int,
    name: str,
    *,
    spec: OutputSpec,
) -> FileIdentity:
    descriptor = -1
    source_fd = -1
    source_directory_fd = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != ROOT_UID
            or before.st_gid != ROOT_GID
            or stat.S_IMODE(before.st_mode) != FILE_MODE
            or before.st_nlink != 1
            or not 0 <= before.st_size < spec.bytes
        ):
            raise FrozenFinalRestoreInputInstallError(
                f"{spec.kind} incomplete partial is unsafe"
            )
        if (spec.payload is None) == (spec.source is None):
            raise FrozenFinalRestoreInputInstallError(
                f"{spec.kind} installation source is ambiguous"
            )
        if spec.payload is not None and (
            len(spec.payload) != spec.bytes
            or _sha256(spec.payload) != spec.sha256
        ):
            raise FrozenFinalRestoreInputInstallError(
                f"{spec.kind} payload binding differs"
            )
        source_before: os.stat_result | None = None
        source_name: str | None = None
        if spec.source is not None:
            opened = _open_parent(
                spec.source,
                label=f"{spec.kind} partial source",
            )
            if opened is None:
                raise FrozenFinalRestoreInputInstallError(
                    f"{spec.kind} partial source is unavailable"
                )
            source_directory_fd, source_name = opened
            source_fd = os.open(
                source_name,
                os.O_RDONLY
                | os.O_NONBLOCK
                | getattr(os, "O_CLOEXEC", 0)
                | os.O_NOFOLLOW,
                dir_fd=source_directory_fd,
            )
            source_before = os.fstat(source_fd)
            if (
                not stat.S_ISREG(source_before.st_mode)
                or source_before.st_uid != ROOT_UID
                or source_before.st_gid != ROOT_GID
                or stat.S_IMODE(source_before.st_mode) != FILE_MODE
                or source_before.st_nlink != 1
                or source_before.st_size != spec.bytes
                or (
                    source_before.st_dev == before.st_dev
                    and source_before.st_ino == before.st_ino
                )
            ):
                raise FrozenFinalRestoreInputInstallError(
                    f"{spec.kind} partial source is unsafe"
                )
        digest = hashlib.sha256()
        observed = 0
        while observed < before.st_size:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, before.st_size - observed),
            )
            if not chunk:
                raise FrozenFinalRestoreInputInstallError(
                    f"{spec.kind} incomplete partial is truncated"
                )
            if spec.payload is not None:
                expected = spec.payload[observed : observed + len(chunk)]
            else:
                expected_chunks: list[bytes] = []
                remaining = len(chunk)
                while remaining:
                    source_chunk = os.read(source_fd, remaining)
                    if not source_chunk:
                        raise FrozenFinalRestoreInputInstallError(
                            f"{spec.kind} partial source is truncated"
                        )
                    expected_chunks.append(source_chunk)
                    remaining -= len(source_chunk)
                expected = b"".join(expected_chunks)
            if chunk != expected:
                raise FrozenFinalRestoreInputInstallError(
                    f"{spec.kind} incomplete partial is not a bound prefix"
                )
            digest.update(chunk)
            observed += len(chunk)
        after = os.fstat(descriptor)
        visible = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            observed != before.st_size
            or _stable_metadata(before) != _stable_metadata(after)
            or _stable_metadata(after) != _stable_metadata(visible)
        ):
            raise FrozenFinalRestoreInputInstallError(
                f"{spec.kind} incomplete partial changed"
            )
        if source_before is not None and source_name is not None:
            source_after = os.fstat(source_fd)
            source_visible = os.stat(
                source_name,
                dir_fd=source_directory_fd,
                follow_symlinks=False,
            )
            if (
                _stable_metadata(source_before)
                != _stable_metadata(source_after)
                or _stable_metadata(source_after)
                != _stable_metadata(source_visible)
            ):
                raise FrozenFinalRestoreInputInstallError(
                    f"{spec.kind} partial source changed"
                )
        return FileIdentity(
            path=spec.path.with_name(name),
            sha256=digest.hexdigest(),
            bytes=observed,
            device=after.st_dev,
            inode=after.st_ino,
        )
    except FrozenFinalRestoreInputInstallError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreInputInstallError(
            f"{spec.kind} incomplete partial is unavailable or unsafe"
        ) from exc
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if source_directory_fd >= 0:
            os.close(source_directory_fd)
        if descriptor >= 0:
            os.close(descriptor)


def _inspect_spec(
    spec: OutputSpec,
    *,
    source_identities: frozenset[tuple[int, int]],
) -> tuple[str, str]:
    opened = _open_parent(
        spec.path,
        label=f"{spec.kind} output",
        missing_ok=True,
    )
    if opened is None:
        return "absent", "absent"
    directory_fd, destination_name = opened
    partial_name = _partial_name(spec)
    try:
        _assert_root_directory(
            directory_fd,
            label=f"{spec.kind} output parent",
            exact_mode=PRIVATE_DIRECTORY_MODE,
        )
        destination: FileIdentity | None = None
        partial: FileIdentity | None = None
        if _leaf_exists(
            directory_fd,
            destination_name,
            label=f"{spec.kind} output",
        ):
            destination = _leaf_identity(
                directory_fd,
                destination_name,
                path=spec.path,
                label=f"existing {spec.kind} output",
                expected_sha256=spec.sha256,
                expected_bytes=spec.bytes,
                allowed_links=frozenset({1, 2}),
            )
        if _leaf_exists(
            directory_fd,
            partial_name,
            label=f"{spec.kind} partial",
        ):
            partial_metadata = os.stat(
                partial_name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            if partial_metadata.st_size == spec.bytes:
                partial = _leaf_identity(
                    directory_fd,
                    partial_name,
                    path=spec.path.with_name(partial_name),
                    label=f"{spec.kind} partial",
                    expected_sha256=spec.sha256,
                    expected_bytes=spec.bytes,
                    allowed_links=frozenset({1, 2}),
                )
            else:
                partial = _partial_prefix_identity(
                    directory_fd,
                    partial_name,
                    spec=spec,
                )
        for identity in (destination, partial):
            if (
                identity is not None
                and (identity.device, identity.inode) in source_identities
            ):
                raise FrozenFinalRestoreInputInstallError(
                    f"{spec.kind} output aliases an installer input"
                )
        if destination is not None and partial is not None:
            same = (
                destination.device == partial.device
                and destination.inode == partial.inode
            )
            if same:
                visible = os.stat(
                    destination_name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                if visible.st_nlink != 2:
                    raise FrozenFinalRestoreInputInstallError(
                        f"{spec.kind} create-link residue is unsafe"
                    )
            elif (
                os.stat(
                    destination_name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                ).st_nlink
                != 1
                or os.stat(
                    partial_name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                ).st_nlink
                != 1
            ):
                raise FrozenFinalRestoreInputInstallError(
                    f"{spec.kind} residue has a foreign hard link"
                )
        elif destination is not None:
            visible = os.stat(
                destination_name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            if visible.st_nlink != 1:
                raise FrozenFinalRestoreInputInstallError(
                    f"{spec.kind} output has a foreign hard link"
                )
        elif partial is not None:
            visible = os.stat(
                partial_name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            if visible.st_nlink != 1:
                raise FrozenFinalRestoreInputInstallError(
                    f"{spec.kind} partial has a foreign hard link"
                )
        return (
            "identical" if destination is not None else "absent",
            "recoverable" if partial is not None else "absent",
        )
    finally:
        os.close(directory_fd)


def _list_directory(
    path: Path,
    *,
    label: str,
    missing_ok: bool,
) -> set[str] | None:
    try:
        descriptor = _open_root_directory(
            path,
            label=label,
            exact_mode=PRIVATE_DIRECTORY_MODE,
        )
    except FrozenFinalRestoreInputInstallError:
        if missing_ok:
            opened = _open_parent(
                path,
                label=label,
                missing_ok=True,
            )
            if opened is None:
                return None
            parent_fd, name = opened
            try:
                if not _leaf_exists(parent_fd, name, label=label):
                    return None
            finally:
                os.close(parent_fd)
        raise
    try:
        return set(os.listdir(descriptor))
    except OSError as exc:
        raise FrozenFinalRestoreInputInstallError(
            f"{label} cannot be enumerated"
        ) from exc
    finally:
        os.close(descriptor)


def _assert_directory_inventory(
    path: Path,
    *,
    label: str,
    allowed: frozenset[str],
) -> None:
    names = _list_directory(path, label=label, missing_ok=True)
    if names is not None and names - allowed:
        raise FrozenFinalRestoreInputInstallError(
            f"{label} contains foreign residue"
        )


def _inspect_generation_residue(
    *,
    paths: WORKER.RuntimePaths,
    outputs: Sequence[OutputSpec],
    source_identities: frozenset[tuple[int, int]],
) -> None:
    allowed_roles = frozenset(WORKER.ROLE_PATHS.values())
    _assert_directory_inventory(
        paths.data_generation_root,
        label="data generation root",
        allowed=allowed_roles | {"restore-input"},
    )
    _assert_directory_inventory(
        paths.role_data_root,
        label="role data root",
        allowed=frozenset({"postgres", "redis", "uploads", "audit"}),
    )
    for name, directory in (
        ("PostgreSQL store", paths.postgres),
        ("Redis store", paths.redis),
        ("uploads store", paths.uploads),
        ("audit store", paths.audit),
    ):
        _assert_directory_inventory(
            directory,
            label=name,
            allowed=frozenset(),
        )
    _assert_directory_inventory(
        paths.data_generation_root / "restore-input",
        label="restore-input generation root",
        allowed=allowed_roles,
    )
    restore_specs = [
        spec for spec in outputs if spec.path.parent == paths.restore_input_root
    ]
    secret_specs = [
        spec
        for spec in outputs
        if spec.path.parent == paths.secret_generation_root
    ]
    tls_specs = [
        spec for spec in outputs if spec.path.parent == paths.ca.parent
    ]
    restore_allowed = {
        spec.path.name for spec in restore_specs
    } | {_partial_name(spec) for spec in restore_specs}
    secret_allowed = {
        spec.path.name for spec in secret_specs
    } | {_partial_name(spec) for spec in secret_specs}
    tls_allowed = {
        spec.path.name for spec in tls_specs
    } | {_partial_name(spec) for spec in tls_specs}
    _assert_directory_inventory(
        paths.restore_input_root,
        label="role restore-input root",
        allowed=frozenset(restore_allowed),
    )
    _assert_directory_inventory(
        paths.secret_generation_root.parent,
        label="secret generation base",
        allowed=frozenset(allowed_roles | {"tls"}),
    )
    _assert_directory_inventory(
        paths.secret_generation_root,
        label="role secret generation root",
        allowed=frozenset(secret_allowed),
    )
    _assert_directory_inventory(
        paths.ca.parent,
        label="secret generation TLS root",
        allowed=frozenset(tls_allowed),
    )
    states = {
        spec.kind: _inspect_spec(
            spec,
            source_identities=source_identities,
        )
        for spec in outputs
    }
    manifest_state = states["role-manifest"]
    closure_states = {
        key: value
        for key, value in states.items()
        if key != "role-manifest"
    }
    if manifest_state[0] == "identical" and any(
        state[0] != "identical" for state in closure_states.values()
    ):
        raise FrozenFinalRestoreInputInstallError(
            "installed role manifest exists without its complete closure"
        )
    if manifest_state[1] == "recoverable" and any(
        state[0] != "identical" for state in closure_states.values()
    ):
        raise FrozenFinalRestoreInputInstallError(
            "role manifest partial precedes its complete closure"
        )


class _AuthorityGate:
    def __init__(
        self,
        authority: AuthorityBinding,
        verifier: LiveAuthorityVerifier,
    ) -> None:
        self.authority = authority
        self.verifier = verifier
        self.sequence = 0
        self.nonces: set[str] = set()
        self.transcript: list[Mapping[str, Any]] = []
        self.tail_sha256 = WORKER.ZERO_SHA256

    def verify(self, boundary: str) -> Mapping[str, Any]:
        try:
            result = self.verifier(self.authority.claim, boundary)
        except Exception as exc:
            raise FrozenFinalRestoreInputInstallError(
                "controller liveness callback failed closed"
            ) from exc
        if (
            not isinstance(result, Mapping)
            or set(result) != WORKER.LIVE_AUTHORITY_FIELDS
            or result["schema"] != WORKER.LIVE_AUTHORITY_SCHEMA
            or result["status"] != "verified-live"
            or result["boundary"] != boundary
            or result["claim_sha256"] != self.authority.claim_sha256
            or result["claim_epoch"] != self.authority.claim_epoch
            or result["claim_nonce"] != self.authority.claim_nonce
            or result["legacy_frozen_receipt_sha256"]
            != self.authority.receipt_sha256
            or result["controller_lock_held"] is not True
            or result["controller_authoritative"] is not True
            or type(result["verification_sequence"]) is not int
            or result["verification_sequence"] <= self.sequence
        ):
            raise FrozenFinalRestoreInputInstallError(
                "controller liveness callback is not live and exact"
            )
        nonce = _nonzero_sha256(
            result["verification_nonce"],
            label="controller liveness nonce",
        )
        if nonce in self.nonces:
            raise FrozenFinalRestoreInputInstallError(
                "controller liveness callback replayed a verification"
            )
        self.sequence = result["verification_sequence"]
        self.nonces.add(nonce)
        verification = json.loads(_canonical_json(dict(result)))
        body = {
            "schema": AUTHORITY_EVENT_SCHEMA,
            "index": len(self.transcript) + 1,
            "boundary": boundary,
            "verification": verification,
            "previous_event_sha256": self.tail_sha256,
        }
        event = {
            **body,
            "event_sha256": _sha256(_canonical_json(body)),
        }
        self.transcript.append(event)
        self.tail_sha256 = event["event_sha256"]
        return verification


def _load_authority(
    plan: InstallationPlan,
    *,
    execution_envelope: Path,
    fresh_live_lease_claim: Path,
    legacy_frozen_receipt: Path,
) -> AuthorityBinding:
    (
        envelope,
        _envelope_payload,
        _envelope_identity,
    ) = _read_canonical_json(
        execution_envelope,
        label="frozen-final installation execution envelope",
    )
    claim, claim_payload, claim_identity = _read_canonical_json(
        fresh_live_lease_claim,
        label="fresh frozen-final live-lease claim",
    )
    receipt_path = _canonical_path(
        legacy_frozen_receipt,
        label="legacy-frozen receipt",
    )
    expected_control_root = (
        WORKER.SECRET_ROOT_PREFIX
        / plan.controller["operation_id"]
        / "nginx-coordinator"
    )
    expected_claim_path = (
        expected_control_root
        / "live-leases"
        / "claims"
        / f"{claim_identity.sha256}.json"
    )
    expected_receipt_sha256 = plan.restore_set["nginx_freeze"][
        "state_receipt_sha256"
    ]
    expected_receipt_path = (
        expected_control_root
        / "receipts"
        / f"legacy-frozen-{expected_receipt_sha256}.json"
    )
    if (
        fresh_live_lease_claim != expected_claim_path
        or receipt_path != expected_receipt_path
    ):
        raise FrozenFinalRestoreInputInstallError(
            "fresh claim or legacy-frozen receipt path is not canonical"
        )
    try:
        receipt, receipt_sha256 = NGINX.load_state_receipt(
            receipt_path,
            "legacy-frozen",
            plan.controller["operation_id"],
            plan.controller["release_sha"],
            plan.controller["release_tree_sha"],
            plan.restore_set["nginx_freeze"]["aggregate_sha256"],
        )
    except NGINX.NginxCoordinatorError as exc:
        raise FrozenFinalRestoreInputInstallError(
            "legacy-frozen receipt is invalid"
        ) from exc
    freeze = plan.restore_set["nginx_freeze"]
    historical = plan.restore_set["snapshot_authorization_claim"]
    role_generation = {
        role: receipt["readbacks"][role]["generation_sha256"]
        for role in NGINX.ROLE_ORDER
    }
    epoch = claim.get("claim_epoch")
    if (
        receipt_sha256 != expected_receipt_sha256
        or receipt["global_generation_sha256"]
        != freeze["global_generation_sha256"]
        or receipt["journal_sha256"] != freeze["journal_sha256"]
        or receipt["evidence_count"] != freeze["journal_sequence"]
        or receipt["evidence_tail_sha256"]
        != freeze["journal_tail_sha256"]
        or receipt["role_bindings"] != freeze["role_bindings"]
        or set(claim) != RESTORE_SET.LIVE_LEASE_FIELDS
        or claim_payload != _canonical_json(claim)
        or claim["schema"] != WORKER.LIVE_LEASE_CLAIM_SCHEMA
        or claim["status"] != "active"
        or claim["owner_action"] != WORKER.LIVE_LEASE_OWNER_ACTION
        or claim["operation_id"] != plan.controller["operation_id"]
        or claim["release_sha"] != plan.controller["release_sha"]
        or claim["release_tree_sha"]
        != plan.controller["release_tree_sha"]
        or claim["aggregate_sha256"] != freeze["aggregate_sha256"]
        or type(epoch) is not int
        or epoch <= historical["claim_epoch"]
        or claim_identity.sha256 == historical["claim_sha256"]
        or claim["controller_lock_path"]
        != os.fspath(expected_control_root / "coordinator.lock")
        or claim["controller_authoritative"] is not True
        or claim["remote_copy_authoritative"] is not False
        or claim["automatic_expiry_allowed"] is not False
        or claim["reconciliation_required_after_crash"] is not True
        or claim["legacy_frozen_receipt_path"]
        != os.fspath(expected_receipt_path)
        or claim["legacy_frozen_receipt_sha256"] != receipt_sha256
        or claim["receipt_journal_sha256"] != receipt["journal_sha256"]
        or claim["receipt_journal_sequence"] != receipt["evidence_count"]
        or claim["receipt_journal_tail_sha256"]
        != receipt["evidence_tail_sha256"]
        or type(claim["controller_journal_event_count"]) is not int
        or claim["controller_journal_event_count"]
        < receipt["evidence_count"]
        or claim["receipt_state"] != "legacy-frozen"
        or claim["receipt_global_generation_sha256"]
        != receipt["global_generation_sha256"]
        or claim["receipt_role_generation_sha256"] != role_generation
        or claim["receipt_role_bindings"] != receipt["role_bindings"]
        or claim["receipt_readbacks"] != receipt["readbacks"]
    ):
        raise FrozenFinalRestoreInputInstallError(
            "fresh restore claim differs from legacy-frozen authority"
        )
    claim_nonce = _nonzero_sha256(
        claim["nonce"],
        label="fresh live-lease nonce",
    )
    previous = claim["previous_claim_sha256"]
    if (
        not isinstance(previous, str)
        or WORKER.SHA256_RE.fullmatch(previous) is None
        or previous == WORKER.ZERO_SHA256
    ):
        raise FrozenFinalRestoreInputInstallError(
            "fresh live-lease predecessor is invalid"
        )
    expected_envelope = {
        "schema": EXECUTION_ENVELOPE_SCHEMA,
        "status": "authorized",
        "owner_action": WORKER.LIVE_LEASE_OWNER_ACTION,
        "intended_outcome": WORKER.LIVE_LEASE_SUCCESS_OUTCOME,
        "campaign_id": plan.controller["campaign_id"],
        "operation_id": plan.controller["operation_id"],
        "release_sha": plan.controller["release_sha"],
        "release_tree_sha": plan.controller["release_tree_sha"],
        "controller_manifest_sha256": plan.controller_sha256,
        "restore_set_sha256": plan.restore_set_sha256,
        "restore_generation_sha256": plan.restore_set[
            "restore_generation_sha256"
        ],
        "target_roles": list(WORKER.ROLE_NAMES),
        "legacy_frozen_receipt_path": os.fspath(expected_receipt_path),
        "legacy_frozen_receipt_sha256": receipt_sha256,
        "historical_claim_sha256": historical["claim_sha256"],
        "fresh_claim_path": os.fspath(expected_claim_path),
        "fresh_claim_sha256": claim_identity.sha256,
        "fresh_claim_epoch": epoch,
        "controller_live_verifier_required": True,
        "network_io_authorized": False,
        "object_storage_mutation_authorized": False,
    }
    if (
        set(envelope) != EXECUTION_ENVELOPE_FIELDS
        or envelope != expected_envelope
    ):
        raise FrozenFinalRestoreInputInstallError(
            "frozen-final execution envelope differs"
        )
    return AuthorityBinding(
        envelope=envelope,
        claim=claim,
        claim_sha256=claim_identity.sha256,
        claim_epoch=epoch,
        claim_nonce=claim_nonce,
        receipt=receipt,
        receipt_sha256=receipt_sha256,
    )


def confirmation_phrase(plan: InstallationPlan) -> str:
    return (
        "install-production-shadow-frozen-final-inputs:"
        f"{plan.controller['operation_id']}:{plan.role}:"
        f"{plan.restore_set['restore_generation_sha256']}"
    )


def _mkdir_open_at(
    parent_fd: int,
    name: str,
    *,
    label: str,
    gate: _AuthorityGate,
) -> int:
    try:
        child = os.open(name, _directory_flags(), dir_fd=parent_fd)
    except FileNotFoundError:
        gate.verify(
            f"before-mkdir:{_authority_boundary_token(label)}"
        )
        try:
            os.mkdir(
                name,
                mode=PRIVATE_DIRECTORY_MODE,
                dir_fd=parent_fd,
            )
            os.fsync(parent_fd)
        except OSError as exc:
            raise FrozenFinalRestoreInputInstallError(
                f"{label} directory could not be created"
            ) from exc
        try:
            child = os.open(name, _directory_flags(), dir_fd=parent_fd)
        except OSError as exc:
            raise FrozenFinalRestoreInputInstallError(
                f"{label} directory is unsafe"
            ) from exc
    except OSError as exc:
        raise FrozenFinalRestoreInputInstallError(
            f"{label} directory is unsafe"
        ) from exc
    try:
        _assert_root_directory(
            child,
            label=label,
            exact_mode=PRIVATE_DIRECTORY_MODE,
        )
        return child
    except Exception:
        os.close(child)
        raise


def _create_chain(
    base: Path,
    parts: Sequence[str],
    *,
    label: str,
    gate: _AuthorityGate,
) -> None:
    descriptor = _open_root_directory(
        base,
        label=f"{label} base",
    )
    try:
        for component in parts:
            if (
                not component
                or component in {".", ".."}
                or "/" in component
                or "\0" in component
            ):
                raise FrozenFinalRestoreInputInstallError(
                    f"{label} directory component is invalid"
                )
            child = _mkdir_open_at(
                descriptor,
                component,
                label=f"{label}/{component}",
                gate=gate,
            )
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


def _create_generation_directories(
    plan: InstallationPlan,
    *,
    gate: _AuthorityGate,
) -> None:
    operation_id = plan.controller["operation_id"]
    generation = plan.restore_set["restore_generation_sha256"]
    role_path = WORKER.ROLE_PATHS[plan.role]
    data_prefix = (
        operation_id,
        "frozen-final-generations",
        generation,
    )
    for suffix, label in (
        ((role_path,), "role data root"),
        ((role_path, "postgres"), "PostgreSQL store"),
        ((role_path, "redis"), "Redis store"),
        ((role_path, "uploads"), "uploads store"),
        ((role_path, "audit"), "audit store"),
        (("restore-input",), "restore-input root"),
        (("restore-input", role_path), "role restore-input root"),
    ):
        _create_chain(
            WORKER.DATA_ROOT_PREFIX,
            (*data_prefix, *suffix),
            label=label,
            gate=gate,
        )
    _create_chain(
        WORKER.SECRET_ROOT_PREFIX,
        (
            operation_id,
            "frozen-final-generations",
            generation,
            role_path,
        ),
        label="role secret generation root",
        gate=gate,
    )
    _create_chain(
        WORKER.SECRET_ROOT_PREFIX,
        (
            operation_id,
            "frozen-final-generations",
            generation,
            "tls",
        ),
        label="secret generation TLS root",
        gate=gate,
    )


def _write_all(descriptor: int, payload: bytes, *, label: str) -> None:
    view = memoryview(payload)
    written = 0
    while written < len(view):
        try:
            count = os.write(descriptor, view[written:])
        except OSError as exc:
            raise FrozenFinalRestoreInputInstallError(
                f"{label} write failed"
            ) from exc
        if count <= 0:
            raise FrozenFinalRestoreInputInstallError(
                f"{label} write made no progress"
            )
        written += count


def _copy_held_source(
    source: Path,
    destination_fd: int,
    *,
    expected_sha256: str,
    expected_bytes: int,
    label: str,
) -> None:
    opened = _open_parent(source, label=f"{label} source")
    if opened is None:
        raise FrozenFinalRestoreInputInstallError(
            f"{label} source is unavailable"
        )
    directory_fd, name = opened
    source_fd = -1
    try:
        source_fd = os.open(
            name,
            os.O_RDONLY
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        before = os.fstat(source_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != ROOT_UID
            or before.st_gid != ROOT_GID
            or stat.S_IMODE(before.st_mode) != FILE_MODE
            or before.st_nlink != 1
            or before.st_size != expected_bytes
        ):
            raise FrozenFinalRestoreInputInstallError(
                f"{label} source is unsafe"
            )
        digest = hashlib.sha256()
        observed = 0
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            observed += len(chunk)
            if observed > expected_bytes:
                raise FrozenFinalRestoreInputInstallError(
                    f"{label} source exceeds its binding"
                )
            digest.update(chunk)
            _write_all(destination_fd, chunk, label=label)
        after = os.fstat(source_fd)
        visible = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            observed != expected_bytes
            or digest.hexdigest() != expected_sha256
            or _stable_metadata(before) != _stable_metadata(after)
            or _stable_metadata(after) != _stable_metadata(visible)
        ):
            raise FrozenFinalRestoreInputInstallError(
                f"{label} source changed while copied"
            )
    except FrozenFinalRestoreInputInstallError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreInputInstallError(
            f"{label} source is unavailable or unsafe"
        ) from exc
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        os.close(directory_fd)


def _write_partial(
    directory_fd: int,
    partial_name: str,
    *,
    spec: OutputSpec,
    gate: _AuthorityGate,
) -> None:
    if _leaf_exists(
        directory_fd,
        partial_name,
        label=f"{spec.kind} partial",
    ):
        metadata = os.stat(
            partial_name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if metadata.st_size == spec.bytes:
            _leaf_identity(
                directory_fd,
                partial_name,
                path=spec.path.with_name(partial_name),
                label=f"{spec.kind} partial",
                expected_sha256=spec.sha256,
                expected_bytes=spec.bytes,
                allowed_links=frozenset({1}),
            )
            return
        _discard_incomplete_partial(
            directory_fd,
            partial_name,
            spec=spec,
            gate=gate,
        )
    gate.verify(f"before-create-partial:{spec.kind}")
    descriptor = -1
    created = False
    try:
        descriptor = os.open(
            partial_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW,
            FILE_MODE,
            dir_fd=directory_fd,
        )
        created = True
        os.fchmod(descriptor, FILE_MODE)
        if spec.payload is not None and spec.source is None:
            gate.verify(f"before-write-payload:{spec.kind}")
            _write_all(descriptor, spec.payload, label=spec.kind)
        elif spec.source is not None and spec.payload is None:
            gate.verify(f"before-copy-source:{spec.kind}")
            _copy_held_source(
                spec.source,
                descriptor,
                expected_sha256=spec.sha256,
                expected_bytes=spec.bytes,
                label=spec.kind,
            )
        else:
            raise FrozenFinalRestoreInputInstallError(
                f"{spec.kind} installation source is ambiguous"
            )
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != ROOT_UID
            or metadata.st_gid != ROOT_GID
            or stat.S_IMODE(metadata.st_mode) != FILE_MODE
            or metadata.st_nlink != 1
            or metadata.st_size != spec.bytes
        ):
            raise FrozenFinalRestoreInputInstallError(
                f"{spec.kind} partial identity differs"
            )
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
        if created:
            try:
                gate.verify(f"before-remove-incomplete:{spec.kind}")
                os.unlink(partial_name, dir_fd=directory_fd)
                os.fsync(directory_fd)
            except Exception:
                pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    os.fsync(directory_fd)
    _leaf_identity(
        directory_fd,
        partial_name,
        path=spec.path.with_name(partial_name),
        label=f"{spec.kind} completed partial",
        expected_sha256=spec.sha256,
        expected_bytes=spec.bytes,
        allowed_links=frozenset({1}),
    )


def _discard_incomplete_partial(
    directory_fd: int,
    partial_name: str,
    *,
    spec: OutputSpec,
    gate: _AuthorityGate,
) -> None:
    identity = _partial_prefix_identity(
        directory_fd,
        partial_name,
        spec=spec,
    )
    gate.verify(f"before-discard-incomplete:{spec.kind}")
    try:
        visible = os.stat(
            partial_name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            visible.st_dev != identity.device
            or visible.st_ino != identity.inode
            or visible.st_size != identity.bytes
            or visible.st_nlink != 1
            or not stat.S_ISREG(visible.st_mode)
            or visible.st_uid != ROOT_UID
            or visible.st_gid != ROOT_GID
            or stat.S_IMODE(visible.st_mode) != FILE_MODE
        ):
            raise FrozenFinalRestoreInputInstallError(
                f"{spec.kind} incomplete partial changed before recovery"
            )
        os.unlink(partial_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    except FrozenFinalRestoreInputInstallError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreInputInstallError(
            f"{spec.kind} incomplete partial could not be recovered"
        ) from exc


def _unlink_partial(
    directory_fd: int,
    partial_name: str,
    *,
    spec: OutputSpec,
    gate: _AuthorityGate,
    allowed_links: frozenset[int],
) -> None:
    if not _leaf_exists(
        directory_fd,
        partial_name,
        label=f"{spec.kind} partial",
    ):
        return
    _leaf_identity(
        directory_fd,
        partial_name,
        path=spec.path.with_name(partial_name),
        label=f"{spec.kind} partial",
        expected_sha256=spec.sha256,
        expected_bytes=spec.bytes,
        allowed_links=allowed_links,
    )
    gate.verify(f"before-unlink-partial:{spec.kind}")
    try:
        os.unlink(partial_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    except OSError as exc:
        raise FrozenFinalRestoreInputInstallError(
            f"{spec.kind} partial could not be removed"
        ) from exc


def _publish_spec(
    spec: OutputSpec,
    *,
    gate: _AuthorityGate,
) -> str:
    opened = _open_parent(
        spec.path,
        label=f"{spec.kind} output",
    )
    if opened is None:
        raise FrozenFinalRestoreInputInstallError(
            f"{spec.kind} output parent is unavailable"
        )
    directory_fd, destination_name = opened
    partial_name = _partial_name(spec)
    try:
        _assert_root_directory(
            directory_fd,
            label=f"{spec.kind} output parent",
            exact_mode=PRIVATE_DIRECTORY_MODE,
        )
        destination_exists = _leaf_exists(
            directory_fd,
            destination_name,
            label=f"{spec.kind} output",
        )
        partial_exists = _leaf_exists(
            directory_fd,
            partial_name,
            label=f"{spec.kind} partial",
        )
        if partial_exists:
            partial_metadata = os.stat(
                partial_name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            if partial_metadata.st_size != spec.bytes:
                _discard_incomplete_partial(
                    directory_fd,
                    partial_name,
                    spec=spec,
                    gate=gate,
                )
                partial_exists = False
        if destination_exists:
            destination = _leaf_identity(
                directory_fd,
                destination_name,
                path=spec.path,
                label=f"existing {spec.kind} output",
                expected_sha256=spec.sha256,
                expected_bytes=spec.bytes,
                allowed_links=frozenset({1, 2}),
            )
            if partial_exists:
                partial = _leaf_identity(
                    directory_fd,
                    partial_name,
                    path=spec.path.with_name(partial_name),
                    label=f"{spec.kind} partial",
                    expected_sha256=spec.sha256,
                    expected_bytes=spec.bytes,
                    allowed_links=frozenset({1, 2}),
                )
                same = (
                    destination.device == partial.device
                    and destination.inode == partial.inode
                )
                _unlink_partial(
                    directory_fd,
                    partial_name,
                    spec=spec,
                    gate=gate,
                    allowed_links=(
                        frozenset({2}) if same else frozenset({1})
                    ),
                )
            _leaf_identity(
                directory_fd,
                destination_name,
                path=spec.path,
                label=f"reused {spec.kind} output",
                expected_sha256=spec.sha256,
                expected_bytes=spec.bytes,
                allowed_links=frozenset({1}),
            )
            return "reused"
        _write_partial(
            directory_fd,
            partial_name,
            spec=spec,
            gate=gate,
        )
        gate.verify(f"before-create-link:{spec.kind}")
        try:
            os.link(
                partial_name,
                destination_name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            pass
        except OSError as exc:
            raise FrozenFinalRestoreInputInstallError(
                f"{spec.kind} create-only publication failed"
            ) from exc
        os.fsync(directory_fd)
        destination = _leaf_identity(
            directory_fd,
            destination_name,
            path=spec.path,
            label=f"published {spec.kind} output",
            expected_sha256=spec.sha256,
            expected_bytes=spec.bytes,
            allowed_links=frozenset({2}),
        )
        partial = _leaf_identity(
            directory_fd,
            partial_name,
            path=spec.path.with_name(partial_name),
            label=f"linked {spec.kind} partial",
            expected_sha256=spec.sha256,
            expected_bytes=spec.bytes,
            allowed_links=frozenset({2}),
        )
        if (
            destination.device != partial.device
            or destination.inode != partial.inode
        ):
            raise FrozenFinalRestoreInputInstallError(
                f"{spec.kind} create-link publication raced"
            )
        _unlink_partial(
            directory_fd,
            partial_name,
            spec=spec,
            gate=gate,
            allowed_links=frozenset({2}),
        )
        _leaf_identity(
            directory_fd,
            destination_name,
            path=spec.path,
            label=f"final {spec.kind} output",
            expected_sha256=spec.sha256,
            expected_bytes=spec.bytes,
            allowed_links=frozenset({1}),
        )
        return "created"
    finally:
        os.close(directory_fd)


@contextmanager
def _installation_lock() -> Iterator[None]:
    descriptor = _open_root_directory(
        WORKER.SECRET_ROOT_PREFIX,
        label="frozen-final secret root prefix",
    )
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FrozenFinalRestoreInputInstallError(
                "another frozen-final installation is active"
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _verify_installed(plan: InstallationPlan) -> None:
    for spec in plan.outputs:
        identity = _hash_secure_file(
            spec.path,
            label=f"installed {spec.kind}",
            maximum=MAX_ARTIFACT_BYTES,
            expected_sha256=spec.sha256,
            expected_bytes=spec.bytes,
        )
        if identity.bytes != spec.bytes:
            raise FrozenFinalRestoreInputInstallError(
                f"installed {spec.kind} byte count differs"
            )
    worker_row = plan.installer_receipt["installed_files"]["worker"]
    _worker_payload, worker_identity = _read_secure_file(
        Path(worker_row["path"]),
        label="attested immutable release worker",
        maximum=MAX_RELEASE_FILE_BYTES,
        allowed_modes=frozenset({0o644, 0o755}),
    )
    if (
        worker_identity.sha256 != worker_row["sha256"]
        or worker_identity.bytes != worker_row["bytes"]
    ):
        raise FrozenFinalRestoreInputInstallError(
            "attested immutable release worker identity differs"
        )
    manifest = _parse_canonical_json(
        plan.role_manifest_payload,
        label="installed role manifest",
    )
    receipt = _parse_canonical_json(
        plan.installer_receipt_payload,
        label="installed installer receipt",
    )
    if manifest != plan.role_manifest or receipt != plan.installer_receipt:
        raise FrozenFinalRestoreInputInstallError(
            "installed canonical documents changed"
        )
    names = _list_directory(
        plan.paths.redis,
        label="installed pristine Redis store",
        missing_ok=False,
    )
    if names:
        raise FrozenFinalRestoreInputInstallError(
            "installed Redis store is not pristine"
        )


def _plan_summary(plan: InstallationPlan) -> Mapping[str, Any]:
    return {
        "schema": WORKER.ROLE_MANIFEST_SCHEMA,
        "status": "planned",
        "campaign_id": plan.controller["campaign_id"],
        "operation_id": plan.controller["operation_id"],
        "role": plan.role,
        "source_role": plan.source_role,
        "release_sha": plan.controller["release_sha"],
        "release_tree_sha": plan.controller["release_tree_sha"],
        "controller_manifest_sha256": plan.controller_sha256,
        "restore_set_sha256": plan.restore_set_sha256,
        "restore_generation_sha256": plan.restore_set[
            "restore_generation_sha256"
        ],
        "role_manifest_sha256": plan.role_manifest_sha256,
        "installer_receipt_sha256": plan.installer_receipt_sha256,
        "prepare_compose_sha256": plan.role_manifest[
            "prepare_compose_sha256"
        ],
        "ca_sha256": plan.role_manifest["ca_sha256"],
        "app_image_id": plan.role_manifest["app_image_id"],
        "app_image_content_identity": plan.role_manifest[
            "app_image_content_identity"
        ],
        "target_migration_revision": plan.role_manifest[
            "target_migration_revision"
        ],
        "data_generation_root": os.fspath(
            plan.paths.data_generation_root
        ),
        "secret_generation_root": os.fspath(
            plan.paths.secret_generation_root
        ),
        "transport": {
            "mode": plan.transport_summary["mode"],
            "network_io_performed": False,
        },
        "required_confirmation": confirmation_phrase(plan),
        "worker_copied": False,
        "network_io_performed": False,
        "docker_invoked": False,
        "object_storage_contacted": False,
        "service_mutated": False,
        "current_mutated": False,
        "legacy_mutated": False,
        "redis_restore_bytes": 0,
    }


def _installation_attestation(
    plan: InstallationPlan,
    *,
    status: str,
    publications: Mapping[str, str],
    authority: AuthorityBinding,
    gate: _AuthorityGate,
) -> Mapping[str, Any]:
    transcript = list(gate.transcript)
    transcript_sha256 = _sha256(_canonical_json(transcript))
    document = {
        "schema": INSTALLATION_ATTESTATION_SCHEMA,
        "status": status,
        "campaign_id": plan.controller["campaign_id"],
        "operation_id": plan.controller["operation_id"],
        "role": plan.role,
        "source_role": plan.source_role,
        "release_sha": plan.controller["release_sha"],
        "release_tree_sha": plan.controller["release_tree_sha"],
        "controller_manifest_sha256": plan.controller_sha256,
        "restore_set_sha256": plan.restore_set_sha256,
        "restore_generation_sha256": plan.restore_set[
            "restore_generation_sha256"
        ],
        "role_manifest_sha256": plan.role_manifest_sha256,
        "installer_receipt_sha256": plan.installer_receipt_sha256,
        "fresh_claim_sha256": authority.claim_sha256,
        "fresh_claim_epoch": authority.claim_epoch,
        "fresh_claim_nonce": authority.claim_nonce,
        "legacy_frozen_receipt_sha256": authority.receipt_sha256,
        "owner_action": WORKER.LIVE_LEASE_OWNER_ACTION,
        "intended_outcome": WORKER.LIVE_LEASE_SUCCESS_OUTCOME,
        "authority_verifications": transcript,
        "authority_verification_count": len(transcript),
        "authority_verification_tail_sha256": gate.tail_sha256,
        "authority_transcript_sha256": transcript_sha256,
        "publications": dict(sorted(publications.items())),
        "worker_copied": False,
        "redis_restore_bytes": 0,
        "network_io_performed": False,
        "docker_invoked": False,
        "object_storage_contacted": False,
        "service_mutated": False,
        "current_mutated": False,
        "legacy_mutated": False,
    }
    return {
        **document,
        "attestation_sha256": _sha256(_canonical_json(document)),
    }


def execute_installation(
    *,
    controller_manifest: Path,
    restore_set: Path,
    role_material: Path,
    database_backup: Path,
    uploads_archive: Path,
    audit_archive: Path,
    canonical_compose: Path,
    worker: Path,
    expected_role: str | None = None,
    webapp_ir_transport_manifest: Path | None = None,
    webapp_ir_readback_receipt: Path | None = None,
    apply: bool = False,
    confirm: str | None = None,
    execution_envelope: Path | None = None,
    fresh_live_lease_claim: Path | None = None,
    legacy_frozen_receipt: Path | None = None,
    live_authority_verifier: LiveAuthorityVerifier | None = None,
) -> Mapping[str, Any]:
    inputs = {
        "controller_manifest": controller_manifest,
        "restore_set": restore_set,
        "role_material": role_material,
        "database_backup": database_backup,
        "uploads_archive": uploads_archive,
        "audit_archive": audit_archive,
        "canonical_compose": canonical_compose,
        "worker": worker,
        "expected_role": expected_role,
        "webapp_ir_transport_manifest": webapp_ir_transport_manifest,
        "webapp_ir_readback_receipt": webapp_ir_readback_receipt,
    }
    plan = preflight_installation(**inputs)
    authority_values = (
        execution_envelope,
        fresh_live_lease_claim,
        legacy_frozen_receipt,
        live_authority_verifier,
    )
    if not apply:
        if confirm is not None or any(
            value is not None for value in authority_values
        ):
            raise FrozenFinalRestoreInputInstallError(
                "confirmation and live authority are valid only in apply mode"
            )
        return _plan_summary(plan)
    required = confirmation_phrase(plan)
    if confirm != required:
        raise FrozenFinalRestoreInputInstallError(
            f"apply requires --confirm {required}"
        )
    if (
        execution_envelope is None
        or fresh_live_lease_claim is None
        or legacy_frozen_receipt is None
        or live_authority_verifier is None
    ):
        raise FrozenFinalRestoreInputInstallError(
            "apply requires envelope, fresh claim, receipt, and live callback"
        )
    with _installation_lock():
        current = preflight_installation(**inputs)
        if (
            current.role_manifest_sha256 != plan.role_manifest_sha256
            or current.installer_receipt_sha256
            != plan.installer_receipt_sha256
        ):
            raise FrozenFinalRestoreInputInstallError(
                "installation inputs changed before apply"
            )
        authority = _load_authority(
            current,
            execution_envelope=_canonical_path(
                execution_envelope,
                label="execution envelope",
            ),
            fresh_live_lease_claim=_canonical_path(
                fresh_live_lease_claim,
                label="fresh live-lease claim",
            ),
            legacy_frozen_receipt=_canonical_path(
                legacy_frozen_receipt,
                label="legacy-frozen receipt",
            ),
        )
        gate = _AuthorityGate(authority, live_authority_verifier)
        gate.verify("before-installation")
        _create_generation_directories(current, gate=gate)
        publications: dict[str, str] = {}
        for spec in current.outputs:
            publications[spec.kind] = _publish_spec(spec, gate=gate)
        _verify_installed(current)
        gate.verify("after-installation-readback")
        status = (
            "already-installed"
            if all(value == "reused" for value in publications.values())
            else "installed"
        )
        return _installation_attestation(
            current,
            status=status,
            publications=publications,
            authority=authority,
            gate=gate,
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--role",
        dest="expected_role",
        choices=WORKER.ROLE_NAMES,
        help="Optional assertion; the role is derived from material identity.",
    )
    parser.add_argument(
        "--controller-manifest",
        required=True,
        type=Path,
    )
    parser.add_argument("--restore-set", required=True, type=Path)
    parser.add_argument("--role-material", required=True, type=Path)
    parser.add_argument("--database-backup", required=True, type=Path)
    parser.add_argument("--uploads-archive", required=True, type=Path)
    parser.add_argument("--audit-archive", required=True, type=Path)
    parser.add_argument("--canonical-compose", required=True, type=Path)
    parser.add_argument("--worker", required=True, type=Path)
    parser.add_argument(
        "--webapp-ir-transport-manifest",
        type=Path,
    )
    parser.add_argument(
        "--webapp-ir-readback-receipt",
        type=Path,
    )
    parser.add_argument("--execution-envelope", type=Path)
    parser.add_argument("--fresh-live-lease-claim", type=Path)
    parser.add_argument("--legacy-frozen-receipt", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    inputs = {
        "controller_manifest": args.controller_manifest,
        "restore_set": args.restore_set,
        "role_material": args.role_material,
        "database_backup": args.database_backup,
        "uploads_archive": args.uploads_archive,
        "audit_archive": args.audit_archive,
        "canonical_compose": args.canonical_compose,
        "worker": args.worker,
        "expected_role": args.expected_role,
        "webapp_ir_transport_manifest": (
            args.webapp_ir_transport_manifest
        ),
        "webapp_ir_readback_receipt": (
            args.webapp_ir_readback_receipt
        ),
    }
    try:
        if args.apply:
            # A CLI invocation cannot provide the synchronous, controller-owned
            # callback required at every filesystem mutation boundary.
            preflight_installation(**inputs)
            raise FrozenFinalRestoreInputInstallError(
                "CLI apply is unavailable without an on-demand controller "
                "liveness callback; use the controller Python API"
            )
        if any(
            value is not None
            for value in (
                args.confirm,
                args.execution_envelope,
                args.fresh_live_lease_claim,
                args.legacy_frozen_receipt,
            )
        ):
            raise FrozenFinalRestoreInputInstallError(
                "authority arguments are valid only for controller API apply"
            )
        result = execute_installation(**inputs)
    except FrozenFinalRestoreInputInstallError as exc:
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
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
