#!/usr/bin/env python3
"""Build one fully bound, approval-pending production cutover manifest.

The builder is controller-local and offline.  Plan mode is the default and
performs the same input verification as apply mode without publishing output.
Apply mode creates or exactly reuses one root-only canonical manifest template.
No host, Docker daemon, provider, service, or production runtime is contacted.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
from typing import Any, Mapping, Sequence
from uuid import UUID


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.canonical_json import (  # noqa: E402
    CanonicalJSONError,
    canonical_json_bytes,
)
from core.human_approval import (  # noqa: E402
    HumanApprovalError,
    load_human_approval_policy,
)
from core.production_shadow_authorization import (  # noqa: E402
    AUTHORIZATION_ACTION,
    AUTHORIZATION_ENVIRONMENT,
    ProductionShadowAuthorizationError,
    authorization_basis_sha256,
)
from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    write_secure_new_bytes,
)
from scripts import (  # noqa: E402
    attest_production_shadow_legacy_rollback as rollback_attestation,
)
from scripts import (  # noqa: E402
    orchestrate_production_shadow_finland_artifacts as release_orchestrator,
)
from scripts import production_shadow_finland_stage as release_stage  # noqa: E402
from scripts import production_shadow_host_agent as host_agent  # noqa: E402
from scripts import production_shadow_nginx_generation as nginx_generation  # noqa: E402
from scripts import production_shadow_convergence_runtime_targets as runtime_targets  # noqa: E402
from scripts import production_shadow_remote_receiver_signing_policy as receiver_policy  # noqa: E402
from scripts import (  # noqa: E402
    produce_production_shadow_prepare_material as prepare_material,
)
from scripts import (  # noqa: E402
    render_three_site_production_shadow_role_compose as role_compose,
)
from scripts import (  # noqa: E402
    verify_production_shadow_phase_evidence as phase_verifier,
)
from scripts.production_shadow_cutover_controller import (  # noqa: E402
    EXPECTED_TOPOLOGY,
    HOST_AGENT_CONTRACT_SHA256,
    MANIFEST_SCHEMA,
    POLICY_FIELDS,
    POSTCOMMIT_JOURNAL_STATUS,
    POSTCOMMIT_SPECS,
    REMOTE_RECEIVER_POLICY_CONTRACT_FIELDS,
    REMOTE_RECEIVER_POLICY_ROLES,
    ZERO_SHA256,
    CutoverContractError,
    _secure_root,
    _shadow_project,
    _shadow_root,
    host_agent_contract_document,
    validate_manifest,
)


POSTCOMMIT_CONTRACT_SCHEMA = (
    "production-shadow-postcommit-executor-contract-v1"
)
POSTCOMMIT_CONTRACT_FIELDS = frozenset(
    {
        "schema",
        "release_sha",
        "executor_path",
        "executor_sha256",
        "required_journal_status",
        "rollback_allowed",
        "operations",
    }
)
POSTCOMMIT_OPERATION_FIELDS = frozenset(
    {
        "phase",
        "operation",
        "roles",
        "forward_only",
        "business_write_allowed",
        "required_journal_status",
        "nginx_generations",
    }
)
POSTCOMMIT_EXECUTOR_RELATIVE_PATH = Path(
    "scripts/production_shadow_postcommit_executor.py"
)
HOST_AGENT_RELATIVE_PATH = Path("scripts/production_shadow_host_agent.py")
PHASE_VERIFIER_RELATIVE_PATH = Path(
    "scripts/verify_production_shadow_phase_evidence.py"
)
CANONICAL_COMPOSE_RELATIVE_PATH = Path(
    "deploy/production/docker-compose.three-site-shadow.yml"
)
SHADOW_ROOT_BASE = Path("/srv/trading-bot-three-site-production-shadow")
OUTPUT_FILENAME = "production-shadow-cutover-manifest-template.json"
RUNTIME_TARGET_DERIVATION_RECEIPT_FILENAME = (
    runtime_targets.CONVERGENCE_RUNTIME_TARGET_DERIVATION_RECEIPT_FILENAME
)
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_ROLE_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024 * 1024
MAX_RELEASE_CODE_BYTES = 16 * 1024 * 1024
MAX_RUNTIME_TARGET_BYTES = runtime_targets.MAX_CONVERGENCE_RUNTIME_TARGET_BYTES
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ROLLBACK_FIELDS = frozenset(
    {
        "schema",
        "status",
        "operation_id",
        "release_sha",
        "legacy_release_sha",
        "role",
        "rollback_closure_sha256",
        "legacy_redis_rollback_sha256",
        "sha256sums_sha256",
        "backup_manifest_sha256",
        "backup_artifact_set_sha256",
        "backup_stamp",
        "database_restore_smoke_passed",
        "database_restore_smoke_table_count",
        "sealed_file_count",
        "backup_artifact_count",
        "source_mutated",
        "production_contacted",
    }
)
PREPARE_SET_FIELDS = frozenset(
    {
        "schema",
        "capabilities",
        "operation_id",
        "release_sha",
        "canonical_compose_sha256",
        "dr_ca_sha256",
        "dr_tls_attestation_sha256",
        "dr_tls_attested_at_epoch",
        "roles",
        "controller_bindings",
        "activation_secrets_included",
        "precommit_manifest_bound",
    }
)
PREPARE_ROLE_FIELDS = frozenset(
    {
        "filename",
        "sha256",
        "bytes",
        "format",
        "transport",
        "internal_manifest_sha256",
        "stage_operation_manifest_sha256",
        "stage_attestation_sha256",
    }
)
NGINX_AGGREGATE_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "shadow_release_root",
        "roles",
        "generation_sha256",
        "legacy_upstream_closure_sha256",
        "nginx_legacy_normal_generation_sha256",
        "nginx_rollback_generation_sha256",
        "nginx_freeze_generation_sha256",
        "nginx_shadow_readonly_generation_sha256",
        "nginx_shadow_writable_generation_sha256",
        "contains_tls_key_or_certificate_body",
        "production_contacted",
        "active_configuration_mutated",
    }
)
NGINX_AGGREGATE_ROLE_FIELDS = frozenset(
    {
        "expected_host",
        "manifest_sha256",
        "manifest_bytes",
        "archive_sha256",
        "archive_bytes",
        "legacy_upstream_closure_sha256",
        "generation_sha256",
    }
)


class CutoverManifestTemplateError(RuntimeError):
    """The final cutover manifest template cannot be proven exact."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CutoverManifestTemplateError(
                f"duplicate JSON field is forbidden: {key}"
            )
        result[key] = value
    return result


def _canonical_uuid4(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise CutoverManifestTemplateError(
            f"{label} must be a canonical UUIDv4"
        )
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise CutoverManifestTemplateError(
            f"{label} must be a canonical UUIDv4"
        ) from exc
    if parsed.version != 4 or str(parsed) != value:
        raise CutoverManifestTemplateError(
            f"{label} must be a canonical UUIDv4"
        )
    return value


def _release_sha(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA40_RE.fullmatch(value) is None
        or value == "0" * 40
    ):
        raise CutoverManifestTemplateError(
            f"{label} must be a nonzero lowercase Git SHA"
        )
    return value


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise CutoverManifestTemplateError(
            f"{label} must be a nonzero SHA-256"
        )
    return value


def _bounded_size(value: Any, *, label: str, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise CutoverManifestTemplateError(f"{label} size is invalid")
    return value


def _canonical_created_at(value: Any) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CutoverManifestTemplateError(
            "created_at must be a canonical UTC timestamp"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise CutoverManifestTemplateError(
            "created_at must be a canonical UTC timestamp"
        ) from exc
    if (
        parsed.microsecond != 0
        or parsed.isoformat(timespec="seconds").replace("+00:00", "Z")
        != value
    ):
        raise CutoverManifestTemplateError(
            "created_at must be a canonical UTC timestamp"
        )
    return value


def _canonical_path(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise CutoverManifestTemplateError(f"{label} path must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise CutoverManifestTemplateError(f"{label} is unavailable") from exc
    if resolved != path:
        raise CutoverManifestTemplateError(
            f"{label} path must not contain or traverse a symlink"
        )
    return path


def _assert_private_directory(
    path: Path,
    *,
    label: str,
    owner_uid: int,
) -> None:
    _canonical_path(path, label=label)
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise CutoverManifestTemplateError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != owner_uid
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise CutoverManifestTemplateError(
            f"{label} must be a real root-owned mode 0700 directory"
        )


def _open_stable_file(
    path: Path,
    *,
    label: str,
    owner_uid: int,
    allowed_modes: frozenset[int],
    maximum: int,
) -> tuple[int, os.stat_result]:
    _canonical_path(path, label=label)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CutoverManifestTemplateError(
            f"cannot securely open {label}: {path}"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner_uid
            or before.st_gid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) not in allowed_modes
            or not 1 <= before.st_size <= maximum
        ):
            raise CutoverManifestTemplateError(
                f"{label} ownership, mode, link, type, or size is unsafe"
            )
        return descriptor, before
    except Exception:
        os.close(descriptor)
        raise


def _verify_stable_identity(
    path: Path,
    *,
    descriptor: int,
    before: os.stat_result,
    label: str,
) -> None:
    after = os.fstat(descriptor)
    try:
        path_after = path.stat(follow_symlinks=False)
        resolved_after = path.resolve(strict=True)
    except OSError as exc:
        raise CutoverManifestTemplateError(
            f"{label} path changed while being read"
        ) from exc
    stable = (
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
    if (
        resolved_after != path
        or any(
            getattr(before, field) != getattr(after, field)
            for field in stable
        )
        or any(
            getattr(after, field) != getattr(path_after, field)
            for field in stable
        )
    ):
        raise CutoverManifestTemplateError(
            f"{label} changed while being read"
        )


def _read_stable_file(
    path: Path,
    *,
    label: str,
    owner_uid: int,
    allowed_modes: frozenset[int],
    maximum: int,
) -> bytes:
    descriptor, before = _open_stable_file(
        path,
        label=label,
        owner_uid=owner_uid,
        allowed_modes=allowed_modes,
        maximum=maximum,
    )
    try:
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) != before.st_size or len(payload) > maximum:
            raise CutoverManifestTemplateError(
                f"{label} size changed while being read"
            )
        _verify_stable_identity(
            path,
            descriptor=descriptor,
            before=before,
            label=label,
        )
        return payload
    finally:
        os.close(descriptor)


def _hash_stable_file(
    path: Path,
    *,
    label: str,
    owner_uid: int,
    allowed_modes: frozenset[int],
    maximum: int,
) -> tuple[str, int]:
    descriptor, before = _open_stable_file(
        path,
        label=label,
        owner_uid=owner_uid,
        allowed_modes=allowed_modes,
        maximum=maximum,
    )
    try:
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                raise CutoverManifestTemplateError(
                    f"{label} exceeds its size bound"
                )
            digest.update(chunk)
        if size != before.st_size:
            raise CutoverManifestTemplateError(
                f"{label} size changed while being hashed"
            )
        _verify_stable_identity(
            path,
            descriptor=descriptor,
            before=before,
            label=label,
        )
        return digest.hexdigest(), size
    finally:
        os.close(descriptor)


def _parse_canonical_object_bytes(
    raw: bytes,
    *,
    label: str,
) -> dict[str, Any]:
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except CutoverManifestTemplateError:
        raise
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise CutoverManifestTemplateError(
            f"{label} is not strict UTF-8 JSON"
        ) from exc
    try:
        canonical = canonical_json_bytes(document)
    except CanonicalJSONError as exc:
        raise CutoverManifestTemplateError(
            f"{label} is not canonical JSON data"
        ) from exc
    if not isinstance(document, dict) or raw != canonical:
        raise CutoverManifestTemplateError(
            f"{label} bytes are not one canonical JSON object"
        )
    return document


def _read_canonical_object(
    path: Path,
    *,
    label: str,
    owner_uid: int,
    maximum: int = MAX_JSON_BYTES,
) -> tuple[dict[str, Any], bytes]:
    raw = _read_stable_file(
        path,
        label=label,
        owner_uid=owner_uid,
        allowed_modes=frozenset({0o600}),
        maximum=maximum,
    )
    return _parse_canonical_object_bytes(raw, label=label), raw


def _expected_release_root(operation_id: str, release_sha: str) -> Path:
    return SHADOW_ROOT_BASE / operation_id / "releases" / release_sha


def _verify_release(
    *,
    closure_path: Path,
    operation_id: str,
    release_root: Path,
    owner_uid: int,
) -> dict[str, Any]:
    _assert_private_directory(
        closure_path.parent,
        label="release artifact directory",
        owner_uid=owner_uid,
    )
    closure_source = _read_stable_file(
        closure_path,
        label="release artifact closure",
        owner_uid=owner_uid,
        allowed_modes=frozenset({0o600}),
        maximum=MAX_JSON_BYTES,
    )
    closure_source_document = _parse_canonical_object_bytes(
        closure_source,
        label="release artifact closure",
    )
    release_source = closure_source_document.get("release")
    if not isinstance(release_source, dict):
        raise CutoverManifestTemplateError(
            "release artifact closure lacks release identity"
        )
    source_release_sha = _release_sha(
        release_source.get("commit_sha"),
        label="release closure commit",
    )
    source_tree_sha = _release_sha(
        release_source.get("tree_sha"),
        label="release closure tree",
    )
    try:
        closure, raw, _digest = release_orchestrator.load_release_closure(
            closure_path,
            operation_id=operation_id,
            release_sha=source_release_sha,
            release_tree_sha=source_tree_sha,
            required_uid=owner_uid,
        )
    except (
        release_orchestrator.FinlandArtifactOrchestratorError,
        release_stage.FinlandStageError,
    ) as exc:
        raise CutoverManifestTemplateError(
            "release artifact closure is invalid"
        ) from exc
    if raw != closure_source or raw != canonical_json_bytes(closure):
        raise CutoverManifestTemplateError(
            "release artifact closure is not canonical JSON"
        )
    release_sha = closure["release"]["commit_sha"]
    release_tree_sha = closure["release"]["tree_sha"]
    expected_root = _expected_release_root(operation_id, release_sha)
    if release_root != expected_root:
        raise CutoverManifestTemplateError(
            "release root is not the canonical operation release path"
        )
    _assert_private_directory(
        release_root,
        label="materialized release root",
        owner_uid=owner_uid,
    )
    try:
        sources = release_orchestrator._artifact_sources(
            closure_path,
            closure,
            required_uid=owner_uid,
        )
        bundle = sources["release-bundle"]
        release_stage._verify_release_bundle(
            bundle,
            release_sha=release_sha,
            expected_sha256=closure["release"]["bundle"]["sha256"],
            expected_bytes=closure["release"]["bundle"]["bytes"],
            required_uid=owner_uid,
            runner=subprocess.run,
        )
        release_stage._verify_materialized_release(
            release_root,
            bundle=bundle,
            release_sha=release_sha,
            release_tree_sha=release_tree_sha,
            required_uid=owner_uid,
            runner=subprocess.run,
        )
        for kind in release_stage.IMAGE_ROLES:
            observed = release_stage.verify_image_archive(
                sources[f"{kind}-image-archive"],
                image_role=kind,
                release_sha=release_sha,
                expected=closure["images"][kind],
                required_uid=owner_uid,
            )
            expected = closure["images"][kind]
            if observed != {
                "archive_sha256": expected["archive_sha256"],
                "archive_bytes": expected["archive_bytes"],
                "config_digest": expected["config_digest"],
                "content_descriptor": expected["content_descriptor"],
                "content_identity": expected["content_identity"],
            }:
                raise CutoverManifestTemplateError(
                    f"{kind} image archive verification differs"
                )
    except (
        release_orchestrator.FinlandArtifactOrchestratorError,
        release_stage.FinlandStageError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        raise CutoverManifestTemplateError(
            "release bundle or image archive verification failed"
        ) from exc
    return closure


def _read_prepare_archive(
    path: Path,
    *,
    role: str,
    owner_uid: int,
) -> tuple[bytes, dict[str, bytes]]:
    payload = _read_stable_file(
        path,
        label=f"{role} prepare role archive",
        owner_uid=owner_uid,
        allowed_modes=frozenset({0o600}),
        maximum=MAX_ROLE_ARCHIVE_BYTES,
    )
    expected_names = (
        [
            prepare_material.WITNESS_MANIFEST_NAME,
            prepare_material.WITNESS_ATTESTATION_NAME,
        ]
        if role == "witness"
        else [
            prepare_material.FINAL_PREPARE_MANIFEST_NAME,
            "role-compose.yml",
            "runtime.env.role",
            "ca.crt",
        ]
    )
    members: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            infos = archive.getmembers()
            if [item.name for item in infos] != expected_names:
                raise CutoverManifestTemplateError(
                    f"{role} prepare archive member order is not exact"
                )
            for item in infos:
                if (
                    not item.isreg()
                    or item.issym()
                    or item.islnk()
                    or item.uid != 0
                    or item.gid != 0
                    or stat.S_IMODE(item.mode) != 0o600
                    or item.mtime != 0
                    or item.uname not in {"", None}
                    or item.gname not in {"", None}
                    or item.pax_headers
                    or not 1
                    <= item.size
                    <= prepare_material.MAX_ARCHIVE_MEMBER_BYTES
                ):
                    raise CutoverManifestTemplateError(
                        f"{role} prepare archive member metadata is unsafe"
                    )
                source = archive.extractfile(item)
                if source is None:
                    raise CutoverManifestTemplateError(
                        f"{role} prepare archive member is unreadable"
                    )
                content = source.read(item.size + 1)
                if len(content) != item.size:
                    raise CutoverManifestTemplateError(
                        f"{role} prepare archive member size differs"
                    )
                members[item.name] = content
    except CutoverManifestTemplateError:
        raise
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise CutoverManifestTemplateError(
            f"{role} prepare archive is invalid"
        ) from exc
    try:
        prepare_material.validate_role_archive_bytes(
            payload,
            expected_files=members,
        )
        if prepare_material._tar_bytes(members) != payload:
            raise CutoverManifestTemplateError(
                f"{role} prepare archive encoding is not deterministic"
            )
    except prepare_material.PrepareMaterialError as exc:
        raise CutoverManifestTemplateError(
            f"{role} prepare archive failed its producer validator"
        ) from exc
    return payload, members


def _validate_prepare_internal_manifest(
    *,
    role: str,
    members: Mapping[str, bytes],
    operation_id: str,
    release_sha: str,
    metadata_row: Mapping[str, Any],
    runtime_image_ids: Mapping[str, str],
) -> tuple[bytes, dict[str, str] | None]:
    manifest_name = (
        prepare_material.WITNESS_MANIFEST_NAME
        if role == "witness"
        else prepare_material.FINAL_PREPARE_MANIFEST_NAME
    )
    manifest_raw = members[manifest_name]
    manifest = _parse_canonical_object_bytes(
        manifest_raw,
        label=f"{role} internal prepare manifest",
    )
    expected_schema = (
        prepare_material.WITNESS_PREPARE_SCHEMA
        if role == "witness"
        else (
            prepare_material.WA_IR_FINAL_PREPARE_SCHEMA
            if role == "webapp_ir"
            else prepare_material.FI_FINAL_PREPARE_SCHEMA
        )
    )
    expected_runtime = {} if role == "witness" else dict(runtime_image_ids)
    if (
        set(manifest) != prepare_material.FINAL_PREPARE_FIELDS
        or manifest["schema"] != expected_schema
        or manifest["operation_id"] != operation_id
        or manifest["release_sha"] != release_sha
        or manifest["role"] != role
        or manifest["operation_manifest_sha256"]
        != metadata_row["stage_operation_manifest_sha256"]
        or manifest["stage_attestation_sha256"]
        != metadata_row["stage_attestation_sha256"]
        or manifest["runtime_image_ids"] != expected_runtime
    ):
        raise CutoverManifestTemplateError(
            f"{role} internal prepare manifest identity differs"
        )
    payload_names = (
        [prepare_material.WITNESS_ATTESTATION_NAME]
        if role == "witness"
        else ["role-compose.yml", "runtime.env.role", "ca.crt"]
    )
    expected_destinations = (
        {
            prepare_material.WITNESS_ATTESTATION_NAME: (
                "attestations/witness-public-prepare.json"
            )
        }
        if role == "witness"
        else prepare_material._role_destinations(role)
    )
    entries = manifest["entries"]
    if (
        not isinstance(entries, list)
        or [entry.get("archive_path") for entry in entries] != payload_names
    ):
        raise CutoverManifestTemplateError(
            f"{role} internal prepare entries are not exact"
        )
    for entry, name in zip(entries, payload_names, strict=True):
        content = members[name]
        if (
            not isinstance(entry, dict)
            or set(entry) != prepare_material.FINAL_PREPARE_ENTRY_FIELDS
            or entry["archive_path"] != name
            or entry["destination"] != expected_destinations[name]
            or entry["sha256"] != hashlib.sha256(content).hexdigest()
            or entry["bytes"] != len(content)
            or entry["mode"] != "0600"
        ):
            raise CutoverManifestTemplateError(
                f"{role} internal prepare entry {name} differs"
            )
    if role == "witness":
        if manifest["required_env_keys"] != []:
            raise CutoverManifestTemplateError(
                "Witness prepare manifest must not require environment keys"
            )
        _parse_canonical_object_bytes(
            members[prepare_material.WITNESS_ATTESTATION_NAME],
            label="Witness public prepare attestation",
        )
    else:
        env_values: dict[str, str]
        keys = manifest["required_env_keys"]
        if (
            not isinstance(keys, list)
            or keys != sorted(set(keys))
            or any(
                not isinstance(name, str) or not name
                for name in keys
            )
            or not prepare_material.IMAGE_ENV_NAMES <= set(keys)
            or prepare_material._forbidden_prepare_environment(set(keys))
        ):
            raise CutoverManifestTemplateError(
                f"{role} required environment key closure differs"
            )
        try:
            compose_document = prepare_material.yaml.safe_load(
                members["role-compose.yml"].decode("utf-8")
            )
            if (
                not isinstance(compose_document, dict)
                or role_compose.canonical_role_compose_bytes(
                    compose_document
                )
                != members["role-compose.yml"]
            ):
                raise CutoverManifestTemplateError(
                    f"{role} role Compose is not canonical"
                )
            env_values = role_compose.parse_env_values(
                members["runtime.env.role"].decode("utf-8")
            )
            if (
                sorted(env_values) != keys
                or role_compose.canonical_role_env_bytes(
                    env_values,
                    required_names=frozenset(env_values),
                )
                != members["runtime.env.role"]
            ):
                raise CutoverManifestTemplateError(
                    f"{role} role environment is not canonical"
                )
        except (
            UnicodeError,
            prepare_material.yaml.YAMLError,
            role_compose.ProductionShadowRoleError,
        ) as exc:
            raise CutoverManifestTemplateError(
                f"{role} role material payload is invalid"
            ) from exc
        ca = members["ca.crt"]
        if (
            ca.count(b"-----BEGIN CERTIFICATE-----") != 1
            or ca.count(b"-----END CERTIFICATE-----") != 1
            or b"-----BEGIN PRIVATE KEY-----" in ca
        ):
            raise CutoverManifestTemplateError(
                f"{role} prepare CA payload is invalid"
            )
    return manifest_raw, (None if role == "witness" else env_values)


def _verify_prepare_materials(
    *,
    metadata_path: Path,
    canonical_compose: Path,
    closure: Mapping[str, Any],
    operation_id: str,
    release_root: Path,
    owner_uid: int,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, str]],
    str,
    dict[str, Any],
]:
    _assert_private_directory(
        metadata_path.parent,
        label="prepare material directory",
        owner_uid=owner_uid,
    )
    metadata, _raw = _read_canonical_object(
        metadata_path,
        label="prepare material metadata",
        owner_uid=owner_uid,
    )
    if runtime_targets.is_legacy_prepare_material_schema(metadata):
        raise CutoverManifestTemplateError(
            runtime_targets.PREPARE_V2_MIGRATION_MESSAGE
        )
    release_sha = closure["release"]["commit_sha"]
    expected_compose_path = release_root / CANONICAL_COMPOSE_RELATIVE_PATH
    if canonical_compose != expected_compose_path:
        raise CutoverManifestTemplateError(
            "canonical Compose path is not release-bound"
        )
    compose_raw = _read_stable_file(
        canonical_compose,
        label="canonical production shadow Compose",
        owner_uid=owner_uid,
        allowed_modes=frozenset({0o644}),
        maximum=MAX_JSON_BYTES,
    )
    compose_sha256 = hashlib.sha256(compose_raw).hexdigest()
    try:
        prepare_material._validate_canonical_compose(
            compose_raw,
            expected_sha256=compose_sha256,
        )
    except prepare_material.PrepareMaterialError as exc:
        raise CutoverManifestTemplateError(
            "canonical production shadow Compose is invalid"
        ) from exc
    if (
        set(metadata) != PREPARE_SET_FIELDS
        or metadata["schema"] != prepare_material.SET_SCHEMA
        or metadata["capabilities"]
        != list(runtime_targets.RUNTIME_TARGET_CAPABILITIES)
        or metadata["operation_id"] != operation_id
        or metadata["release_sha"] != release_sha
        or metadata["canonical_compose_sha256"] != compose_sha256
        or metadata["activation_secrets_included"] is not False
        or metadata["precommit_manifest_bound"] is not False
        or set(metadata.get("roles", {}))
        != set(prepare_material.ALL_ROLES)
        or set(metadata.get("controller_bindings", {}))
        != {
            "role_materials",
            "role_runtime_image_ids",
            "convergence_runtime_targets",
        }
    ):
        raise CutoverManifestTemplateError(
            "prepare material metadata identity differs"
        )
    _nonzero_sha256(metadata["dr_ca_sha256"], label="prepare DR CA")
    _nonzero_sha256(
        metadata["dr_tls_attestation_sha256"],
        label="prepare DR TLS attestation",
    )
    _bounded_size(
        metadata["dr_tls_attested_at_epoch"],
        label="prepare DR TLS attestation epoch",
        maximum=4_102_444_800,
    )
    runtime_inventory = metadata["controller_bindings"][
        "role_runtime_image_ids"
    ]
    expected_runtime = {
        kind: closure["images"][kind]["config_digest"]
        for kind in release_stage.IMAGE_ROLES
    }
    if (
        not isinstance(runtime_inventory, dict)
        or set(runtime_inventory) != set(prepare_material.DOCKER_ROLES)
        or any(
            runtime_inventory[role] != expected_runtime
            for role in prepare_material.DOCKER_ROLES
        )
    ):
        raise CutoverManifestTemplateError(
            "prepare runtime image inventory differs from verified archives"
        )
    runtime_target_descriptor_input = metadata["controller_bindings"][
        "convergence_runtime_targets"
    ]
    try:
        runtime_target_descriptor_input = (
            runtime_targets.validate_runtime_target_descriptor(
                runtime_target_descriptor_input,
                label="prepare convergence runtime target descriptor",
            )
        )
    except runtime_targets.ConvergenceRuntimeTargetDescriptorError as exc:
        raise CutoverManifestTemplateError(
            "prepare convergence runtime target descriptor differs"
        ) from exc
    runtime_target_sha256 = _nonzero_sha256(
        runtime_target_descriptor_input["sha256"],
        label="prepare convergence runtime target file",
    )
    runtime_target_set_sha256 = _nonzero_sha256(
        runtime_target_descriptor_input["target_set_sha256"],
        label="prepare convergence runtime target set",
    )
    runtime_target_bytes = _bounded_size(
        runtime_target_descriptor_input["bytes"],
        label="prepare convergence runtime target file",
        maximum=MAX_RUNTIME_TARGET_BYTES,
    )
    runtime_target_path = (
        metadata_path.parent
        / prepare_material.CONVERGENCE_RUNTIME_TARGETS_FILENAME
    )
    runtime_target_payload = _read_stable_file(
        runtime_target_path,
        label="prepare convergence runtime target set",
        owner_uid=owner_uid,
        allowed_modes=frozenset({0o600}),
        maximum=MAX_RUNTIME_TARGET_BYTES,
    )
    if (
        len(runtime_target_payload) != runtime_target_bytes
        or hashlib.sha256(runtime_target_payload).hexdigest()
        != runtime_target_sha256
    ):
        raise CutoverManifestTemplateError(
            "prepare convergence runtime target file identity differs"
        )
    runtime_target_document = _parse_canonical_object_bytes(
        runtime_target_payload,
        label="prepare convergence runtime target set",
    )
    try:
        prepare_material.validate_convergence_runtime_target_set(
            runtime_target_document,
            operation_id=operation_id,
            release_sha=release_sha,
            canonical_compose_raw=compose_raw,
        )
    except prepare_material.PrepareMaterialError as exc:
        raise CutoverManifestTemplateError(
            "prepare convergence runtime target set is invalid"
        ) from exc
    if (
        runtime_target_document["target_set_sha256"]
        != runtime_target_set_sha256
    ):
        raise CutoverManifestTemplateError(
            "prepare convergence runtime target descriptor hash differs"
        )
    runtime_target_descriptor = {
        "schema": prepare_material.CONVERGENCE_RUNTIME_TARGET_SET_SCHEMA,
        "filename": prepare_material.CONVERGENCE_RUNTIME_TARGETS_FILENAME,
        "sha256": runtime_target_sha256,
        "bytes": runtime_target_bytes,
        "target_set_sha256": runtime_target_set_sha256,
        "roles": list(prepare_material.DOCKER_ROLES),
    }
    try:
        runtime_target_descriptor = runtime_targets.validate_runtime_target_descriptor(
            runtime_target_descriptor,
            label="verified convergence runtime target descriptor",
        )
    except runtime_targets.ConvergenceRuntimeTargetDescriptorError as exc:
        raise CutoverManifestTemplateError(
            "prepare convergence runtime target descriptor differs"
        ) from exc
    if runtime_target_descriptor_input != runtime_target_descriptor:
        raise CutoverManifestTemplateError(
            "prepare convergence runtime target descriptor differs"
        )
    controller_materials = metadata["controller_bindings"][
        "role_materials"
    ]
    if (
        not isinstance(controller_materials, dict)
        or set(controller_materials) != set(prepare_material.ALL_ROLES)
    ):
        raise CutoverManifestTemplateError(
            "prepare controller role material closure differs"
        )
    role_materials: dict[str, dict[str, Any]] = {}
    verified_runtime_target_sources: dict[str, Mapping[str, str]] = {}
    for role in prepare_material.ALL_ROLES:
        row = metadata["roles"][role]
        expected_filename = prepare_material.ROLE_ARCHIVE_NAMES[role]
        if (
            not isinstance(row, dict)
            or set(row) != PREPARE_ROLE_FIELDS
            or row["filename"] != expected_filename
            or row["format"] != prepare_material.ROLE_FORMATS[role]
            or row["transport"] != prepare_material.ROLE_TRANSPORTS[role]
        ):
            raise CutoverManifestTemplateError(
                f"{role} prepare material metadata differs"
            )
        for field in (
            "sha256",
            "internal_manifest_sha256",
            "stage_operation_manifest_sha256",
            "stage_attestation_sha256",
        ):
            _nonzero_sha256(
                row[field],
                label=f"{role} prepare {field}",
            )
        archive_size = _bounded_size(
            row["bytes"],
            label=f"{role} prepare archive",
            maximum=MAX_ROLE_ARCHIVE_BYTES,
        )
        archive_path = metadata_path.parent / expected_filename
        archive_payload, members = _read_prepare_archive(
            archive_path,
            role=role,
            owner_uid=owner_uid,
        )
        if (
            len(archive_payload) != archive_size
            or hashlib.sha256(archive_payload).hexdigest() != row["sha256"]
        ):
            raise CutoverManifestTemplateError(
                f"{role} prepare archive identity differs"
            )
        internal_raw, runtime_target_source = _validate_prepare_internal_manifest(
            role=role,
            members=members,
            operation_id=operation_id,
            release_sha=release_sha,
            metadata_row=row,
            runtime_image_ids=(
                {} if role == "witness" else runtime_inventory[role]
            ),
        )
        if (
            hashlib.sha256(internal_raw).hexdigest()
            != row["internal_manifest_sha256"]
        ):
            raise CutoverManifestTemplateError(
                f"{role} internal prepare manifest digest differs"
            )
        if role in prepare_material.DOCKER_ROLES:
            if runtime_target_source is None:
                raise CutoverManifestTemplateError(
                    f"{role} runtime target environment is unavailable"
                )
            verified_runtime_target_sources[role] = runtime_target_source
        controller_row = {
            "sha256": row["sha256"],
            "bytes": row["bytes"],
            "transport": row["transport"],
            "format": row["format"],
        }
        if controller_materials[role] != controller_row:
            raise CutoverManifestTemplateError(
                f"{role} controller role material binding differs"
            )
        role_materials[role] = controller_row
    if set(verified_runtime_target_sources) != set(prepare_material.DOCKER_ROLES):
        raise CutoverManifestTemplateError(
            "prepare runtime target environment source coverage differs"
        )
    try:
        rederived_runtime_target_set = (
            prepare_material.build_convergence_runtime_target_set(
                operation_id=operation_id,
                release_sha=release_sha,
                canonical_compose_raw=compose_raw,
                role_source_values=verified_runtime_target_sources,
            )
        )
        rederived_runtime_target_payload = canonical_json_bytes(
            rederived_runtime_target_set
        )
        rederived_runtime_target_descriptor = (
            prepare_material.convergence_runtime_target_descriptor(
                rederived_runtime_target_set,
                canonical_compose_raw=compose_raw,
            )
        )
    except prepare_material.PrepareMaterialError as exc:
        raise CutoverManifestTemplateError(
            "prepare convergence runtime target semantic derivation failed"
        ) from exc
    if (
        runtime_target_payload != rederived_runtime_target_payload
        or runtime_target_document != rederived_runtime_target_set
        or runtime_target_descriptor != rederived_runtime_target_descriptor
    ):
        raise CutoverManifestTemplateError(
            "prepare convergence runtime target semantic derivation differs"
        )
    if len({row["sha256"] for row in role_materials.values()}) != len(
        role_materials
    ):
        raise CutoverManifestTemplateError(
            "prepare role material digests must be distinct"
        )
    return (
        role_materials,
        runtime_inventory,
        compose_sha256,
        runtime_target_descriptor,
    )


def _verify_rollback_attestation(
    path: Path,
    *,
    role: str,
    operation_id: str,
    release_sha: str,
    legacy_release_sha: str,
    owner_uid: int,
) -> tuple[str, str]:
    document, _raw = _read_canonical_object(
        path,
        label=f"{role} legacy rollback attestation",
        owner_uid=owner_uid,
    )
    expected_sealed_count = (
        len(rollback_attestation.ROLE_SEALED_FILES[role]) + 1
    )
    if (
        set(document) != ROLLBACK_FIELDS
        or document["schema"] != rollback_attestation.ATTESTATION_SCHEMA
        or document["status"] != "verified"
        or document["operation_id"] != operation_id
        or document["release_sha"] != release_sha
        or document["legacy_release_sha"] != legacy_release_sha
        or document["role"] != role
        or rollback_attestation.STAMP_RE.fullmatch(
            str(document["backup_stamp"])
        )
        is None
        or document["database_restore_smoke_passed"] is not True
        or isinstance(document["database_restore_smoke_table_count"], bool)
        or not isinstance(
            document["database_restore_smoke_table_count"], int
        )
        or not 1
        <= document["database_restore_smoke_table_count"]
        <= 100_000
        or document["sealed_file_count"] != expected_sealed_count
        or document["backup_artifact_count"]
        != len(rollback_attestation.BACKUP_KINDS)
        or document["source_mutated"] is not False
        or document["production_contacted"] is not True
    ):
        raise CutoverManifestTemplateError(
            f"{role} legacy rollback attestation differs"
        )
    for field in (
        "rollback_closure_sha256",
        "legacy_redis_rollback_sha256",
        "sha256sums_sha256",
        "backup_manifest_sha256",
        "backup_artifact_set_sha256",
    ):
        _nonzero_sha256(
            document[field],
            label=f"{role} rollback {field}",
        )
    return (
        document["rollback_closure_sha256"],
        document["legacy_redis_rollback_sha256"],
    )


def _verify_nginx_material(
    *,
    aggregate_path: Path,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    release_root: Path,
    owner_uid: int,
) -> dict[str, str]:
    _assert_private_directory(
        aggregate_path.parent,
        label="Nginx generation directory",
        owner_uid=owner_uid,
    )
    aggregate, _raw = _read_canonical_object(
        aggregate_path,
        label="Nginx generation aggregate",
        owner_uid=owner_uid,
    )
    if (
        set(aggregate) != NGINX_AGGREGATE_FIELDS
        or aggregate["schema"] != nginx_generation.PRODUCER_SCHEMA
        or aggregate["operation_id"] != operation_id
        or aggregate["release_sha"] != release_sha
        or aggregate["release_tree_sha"] != release_tree_sha
        or aggregate["shadow_release_root"] != str(release_root)
        or set(aggregate.get("roles", {}))
        != set(nginx_generation.ROLES)
        or aggregate["contains_tls_key_or_certificate_body"] is not False
        or aggregate["production_contacted"] is not False
        or aggregate["active_configuration_mutated"] is not False
    ):
        raise CutoverManifestTemplateError(
            "Nginx generation aggregate identity differs"
        )
    role_manifests: dict[str, dict[str, Any]] = {}
    for role in nginx_generation.ROLES:
        role_directory = aggregate_path.parent / role
        _assert_private_directory(
            role_directory,
            label=f"{role} Nginx generation directory",
            owner_uid=owner_uid,
        )
        row = aggregate["roles"][role]
        if (
            not isinstance(row, dict)
            or set(row) != NGINX_AGGREGATE_ROLE_FIELDS
            or row["expected_host"]
            != nginx_generation.ROLE_HOSTS[role]
            or not isinstance(row["generation_sha256"], dict)
            or set(row["generation_sha256"])
            != set(nginx_generation.GENERATION_STATES)
        ):
            raise CutoverManifestTemplateError(
                f"{role} Nginx aggregate row differs"
            )
        for field in (
            "manifest_sha256",
            "archive_sha256",
            "legacy_upstream_closure_sha256",
        ):
            _nonzero_sha256(
                row[field],
                label=f"{role} Nginx {field}",
            )
        _bounded_size(
            row["manifest_bytes"],
            label=f"{role} Nginx manifest",
            maximum=nginx_generation.MAX_JSON_BYTES,
        )
        _bounded_size(
            row["archive_bytes"],
            label=f"{role} Nginx archive",
            maximum=nginx_generation.MAX_ARCHIVE_BYTES,
        )
        try:
            manifest, manifest_raw, _members = (
                nginx_generation.load_role_material(
                    manifest_path=(
                        role_directory
                        / "nginx-generations-manifest.json"
                    ),
                    expected_manifest_sha256=row["manifest_sha256"],
                    archive_path=(
                        role_directory / "nginx-generations.tar"
                    ),
                    expected_role=role,
                    expected_host=row["expected_host"],
                    operation_id=operation_id,
                    release_sha=release_sha,
                    release_tree_sha=release_tree_sha,
                    owner_uid=owner_uid,
                )
            )
        except nginx_generation.NginxGenerationError as exc:
            raise CutoverManifestTemplateError(
                f"{role} Nginx role material is invalid"
            ) from exc
        if (
            len(manifest_raw) != row["manifest_bytes"]
            or manifest["archive"]["sha256"] != row["archive_sha256"]
            or manifest["archive"]["bytes"] != row["archive_bytes"]
            or manifest["legacy_upstream_closure_sha256"]
            != row["legacy_upstream_closure_sha256"]
            or manifest["generation_sha256"]
            != row["generation_sha256"]
        ):
            raise CutoverManifestTemplateError(
                f"{role} Nginx aggregate binding differs"
            )
        role_manifests[role] = manifest
    expected_global = {
        state: nginx_generation._generation_digest(
            {
                f"{role}:{vhost['destination']}": (
                    vhost["generation_sha256"][state]
                )
                for role in nginx_generation.ROLES
                for vhost in role_manifests[role]["vhosts"]
            }
        )
        for state in nginx_generation.GENERATION_STATES
    }
    expected_upstream_closure = hashlib.sha256(
        canonical_json_bytes(
            {
                role: role_manifests[role][
                    "legacy_upstream_closure_sha256"
                ]
                for role in nginx_generation.ROLES
            }
        )
    ).hexdigest()
    aliases = {
        "nginx_legacy_normal_generation_sha256": "legacy-normal",
        "nginx_rollback_generation_sha256": "legacy-normal",
        "nginx_freeze_generation_sha256": "legacy-frozen",
        "nginx_shadow_readonly_generation_sha256": "shadow-readonly",
        "nginx_shadow_writable_generation_sha256": "shadow-writable",
    }
    if (
        aggregate["generation_sha256"] != expected_global
        or aggregate["legacy_upstream_closure_sha256"]
        != expected_upstream_closure
        or any(
            aggregate[field] != expected_global[state]
            for field, state in aliases.items()
        )
        or len(set(expected_global.values()))
        != len(nginx_generation.GENERATION_STATES)
    ):
        raise CutoverManifestTemplateError(
            "Nginx global generation closure differs"
        )
    return {
        "nginx_rollback_generation_sha256": aggregate[
            "nginx_rollback_generation_sha256"
        ],
        "nginx_freeze_generation_sha256": aggregate[
            "nginx_freeze_generation_sha256"
        ],
        "nginx_shadow_readonly_generation_sha256": aggregate[
            "nginx_shadow_readonly_generation_sha256"
        ],
        "nginx_shadow_writable_generation_sha256": aggregate[
            "nginx_shadow_writable_generation_sha256"
        ],
    }


def _expected_postcommit_operations() -> list[dict[str, Any]]:
    return [
        {
            "phase": spec.phase,
            "operation": spec.operation,
            "roles": list(spec.roles),
            "forward_only": spec.forward_only,
            "business_write_allowed": spec.business_write_allowed,
            "required_journal_status": spec.required_journal_status,
            "nginx_generations": list(spec.nginx_generations),
        }
        for spec in POSTCOMMIT_SPECS
    ]


def _verify_release_contracts(
    *,
    release_root: Path,
    release_sha: str,
    postcommit_contract_path: Path,
    owner_uid: int,
) -> dict[str, str]:
    code_paths = {
        "host_agent_sha256": release_root / HOST_AGENT_RELATIVE_PATH,
        "phase_evidence_verifier_sha256": (
            release_root / PHASE_VERIFIER_RELATIVE_PATH
        ),
        "postcommit_executor_sha256": (
            release_root / POSTCOMMIT_EXECUTOR_RELATIVE_PATH
        ),
    }
    code_hashes = {
        field: _hash_stable_file(
            path,
            label=field.replace("_", " "),
            owner_uid=owner_uid,
            allowed_modes=frozenset({0o644}),
            maximum=MAX_RELEASE_CODE_BYTES,
        )[0]
        for field, path in code_paths.items()
    }
    try:
        contract_document = host_agent.validate_contract(
            host_agent_contract_document()
        )
        observed_host_contract = host_agent.contract_sha256(
            contract_document
        )
    except host_agent.HostAgentError as exc:
        raise CutoverManifestTemplateError(
            "controller and host-agent contract constants differ"
        ) from exc
    if observed_host_contract != HOST_AGENT_CONTRACT_SHA256:
        raise CutoverManifestTemplateError(
            "controller and host-agent contract hashes differ"
        )
    phase_contract_sha256 = hashlib.sha256(
        canonical_json_bytes(phase_verifier.PHASE_EVIDENCE_CONTRACT)
    ).hexdigest()
    if (
        phase_contract_sha256
        != phase_verifier.PHASE_EVIDENCE_CONTRACT_SHA256
        or phase_contract_sha256 == ZERO_SHA256
    ):
        raise CutoverManifestTemplateError(
            "phase evidence verifier contract constant differs"
        )
    postcommit, postcommit_raw = _read_canonical_object(
        postcommit_contract_path,
        label="postcommit executor contract",
        owner_uid=owner_uid,
    )
    expected_operations = _expected_postcommit_operations()
    if (
        set(postcommit) != POSTCOMMIT_CONTRACT_FIELDS
        or postcommit["schema"] != POSTCOMMIT_CONTRACT_SCHEMA
        or postcommit["release_sha"] != release_sha
        or postcommit["executor_path"]
        != POSTCOMMIT_EXECUTOR_RELATIVE_PATH.as_posix()
        or postcommit["executor_sha256"]
        != code_hashes["postcommit_executor_sha256"]
        or postcommit["required_journal_status"]
        != POSTCOMMIT_JOURNAL_STATUS
        or postcommit["rollback_allowed"] is not False
        or postcommit["operations"] != expected_operations
        or any(
            not isinstance(row, dict)
            or set(row) != POSTCOMMIT_OPERATION_FIELDS
            for row in postcommit.get("operations", [])
        )
    ):
        raise CutoverManifestTemplateError(
            "postcommit executor contract differs from POSTCOMMIT_SPECS"
        )
    return {
        "host_agent_sha256": code_hashes["host_agent_sha256"],
        "host_agent_contract_sha256": HOST_AGENT_CONTRACT_SHA256,
        "phase_evidence_verifier_sha256": code_hashes[
            "phase_evidence_verifier_sha256"
        ],
        "phase_evidence_schema_sha256": phase_contract_sha256,
        "postcommit_executor_contract_sha256": hashlib.sha256(
            postcommit_raw
        ).hexdigest(),
    }


def _verify_human_policy(
    path: Path,
    *,
    owner_uid: int,
) -> str:
    document, raw = _read_canonical_object(
        path,
        label="canonical human approval public policy",
        owner_uid=owner_uid,
    )
    try:
        policy = load_human_approval_policy(document)
    except HumanApprovalError as exc:
        raise CutoverManifestTemplateError(
            "human approval public policy is invalid"
        ) from exc
    action = policy.actions.get(AUTHORIZATION_ACTION)
    if (
        action is None
        or AUTHORIZATION_ENVIRONMENT not in action.environments
    ):
        raise CutoverManifestTemplateError(
            "human approval policy does not authorize production deployment"
        )
    digest = hashlib.sha256(raw).hexdigest()
    if policy.policy_hash != digest:
        raise CutoverManifestTemplateError(
            "human approval policy canonical hash differs"
        )
    return digest


def _verify_remote_receiver_signing_policies(
    *,
    paths: Mapping[str, Path],
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    owner_uid: int,
) -> dict[str, dict[str, str]]:
    """Read public, root-only remote signing policies into manifest anchors."""

    if set(paths) != set(REMOTE_RECEIVER_POLICY_ROLES):
        raise CutoverManifestTemplateError(
            "remote receiver signing-policy roles are not exact"
        )
    anchors: dict[str, dict[str, str]] = {}
    for role in REMOTE_RECEIVER_POLICY_ROLES:
        raw = _read_stable_file(
            paths[role],
            label=f"{role} remote receiver signing policy",
            owner_uid=owner_uid,
            allowed_modes=frozenset({0o600}),
            maximum=receiver_policy.MAX_POLICY_BYTES,
        )
        try:
            policy = receiver_policy.parse_policy_payload(raw)
        except receiver_policy.RemoteReceiverSigningPolicyError as exc:
            raise CutoverManifestTemplateError(
                f"{role} remote receiver signing policy is invalid"
            ) from exc
        expected = {
            "campaign_id": campaign_id,
            "operation_id": operation_id,
            "release_sha": release_sha,
            "release_tree_sha": release_tree_sha,
            "role": role,
        }
        actual = {
            "campaign_id": policy.campaign_id,
            "operation_id": policy.operation_id,
            "release_sha": policy.release_sha,
            "release_tree_sha": policy.release_tree_sha,
            "role": policy.role,
        }
        if actual != expected:
            raise CutoverManifestTemplateError(
                f"{role} remote receiver signing policy binding differs"
            )
        anchor = {
            "policy_file_sha256": hashlib.sha256(raw).hexdigest(),
            "policy_sha256": policy.policy_sha256,
            "key_id": policy.key_id,
            "public_key_sha256": hashlib.sha256(policy.public_key).hexdigest(),
            "receiver_sha256": policy.receiver_sha256,
            "worker_sha256": policy.worker_sha256,
        }
        if set(anchor) != REMOTE_RECEIVER_POLICY_CONTRACT_FIELDS:
            raise CutoverManifestTemplateError(
                f"{role} remote receiver signing-policy anchor differs"
            )
        anchors[role] = anchor
    return anchors


def _assert_only_pending_approval(manifest: Mapping[str, Any]) -> None:
    zero_paths: list[str] = []

    def visit(value: Any, path: str) -> None:
        if value == ZERO_SHA256:
            zero_paths.append(path)
        if isinstance(value, dict):
            for key, nested in value.items():
                visit(nested, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                visit(nested, f"{path}[{index}]")

    visit(manifest, "")
    if zero_paths != ["artifacts.cutover_approval_sha256"]:
        raise CutoverManifestTemplateError(
            "only cutover_approval_sha256 may remain zero"
        )


def _validate_template(document: dict[str, Any]) -> dict[str, Any]:
    try:
        normalized = json.loads(canonical_json_bytes(document))
    except (CanonicalJSONError, json.JSONDecodeError) as exc:
        raise CutoverManifestTemplateError(
            "cutover manifest template is not canonical JSON data"
        ) from exc
    _assert_only_pending_approval(normalized)
    provisional = json.loads(canonical_json_bytes(normalized))
    provisional["artifacts"]["cutover_approval_sha256"] = "1" * 64
    try:
        validate_manifest(provisional)
        authorization_basis_sha256(normalized)
    except (
        CutoverContractError,
        ProductionShadowAuthorizationError,
    ) as exc:
        raise CutoverManifestTemplateError(
            "cutover manifest template fails the controller contract"
        ) from exc
    return normalized


def build_template(
    *,
    campaign_id: str,
    operation_id: str,
    created_at: str,
    legacy_release_sha: str,
    release_closure: Path,
    release_root: Path,
    prepare_metadata: Path,
    canonical_compose: Path,
    bot_rollback_attestation: Path,
    webapp_rollback_attestation: Path,
    nginx_aggregate: Path,
    human_approval_policy: Path,
    webapp_ir_remote_receiver_signing_policy: Path,
    witness_remote_receiver_signing_policy: Path,
    postcommit_executor_contract: Path,
    owner_uid: int = 0,
) -> dict[str, Any]:
    campaign_id = _canonical_uuid4(campaign_id, label="campaign_id")
    operation_id = _canonical_uuid4(operation_id, label="operation_id")
    if campaign_id == operation_id:
        raise CutoverManifestTemplateError(
            "campaign_id and operation_id must differ"
        )
    created_at = _canonical_created_at(created_at)
    legacy_release_sha = _release_sha(
        legacy_release_sha,
        label="legacy_release_sha",
    )
    closure = _verify_release(
        closure_path=release_closure,
        operation_id=operation_id,
        release_root=release_root,
        owner_uid=owner_uid,
    )
    release_sha = closure["release"]["commit_sha"]
    release_tree_sha = closure["release"]["tree_sha"]
    if legacy_release_sha == release_sha:
        raise CutoverManifestTemplateError(
            "legacy and shadow release SHAs must differ"
        )
    (
        role_materials,
        runtime_inventory,
        compose_sha256,
        runtime_target_descriptor,
    ) = (
        _verify_prepare_materials(
            metadata_path=prepare_metadata,
            canonical_compose=canonical_compose,
            closure=closure,
            operation_id=operation_id,
            release_root=release_root,
            owner_uid=owner_uid,
        )
    )
    bot_rollback, bot_redis = _verify_rollback_attestation(
        bot_rollback_attestation,
        role="bot_fi",
        operation_id=operation_id,
        release_sha=release_sha,
        legacy_release_sha=legacy_release_sha,
        owner_uid=owner_uid,
    )
    webapp_rollback, webapp_redis = _verify_rollback_attestation(
        webapp_rollback_attestation,
        role="webapp_fi",
        operation_id=operation_id,
        release_sha=release_sha,
        legacy_release_sha=legacy_release_sha,
        owner_uid=owner_uid,
    )
    nginx_hashes = _verify_nginx_material(
        aggregate_path=nginx_aggregate,
        operation_id=operation_id,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        release_root=release_root,
        owner_uid=owner_uid,
    )
    policy_sha256 = _verify_human_policy(
        human_approval_policy,
        owner_uid=owner_uid,
    )
    remote_receiver_signing_policies = (
        _verify_remote_receiver_signing_policies(
            paths={
                "webapp_ir": webapp_ir_remote_receiver_signing_policy,
                "witness": witness_remote_receiver_signing_policy,
            },
            campaign_id=campaign_id,
            operation_id=operation_id,
            release_sha=release_sha,
            release_tree_sha=release_tree_sha,
            owner_uid=owner_uid,
        )
    )
    release_contracts = _verify_release_contracts(
        release_root=release_root,
        release_sha=release_sha,
        postcommit_contract_path=postcommit_executor_contract,
        owner_uid=owner_uid,
    )
    secure_root = _secure_root(campaign_id)
    artifacts: dict[str, Any] = {
        "release_bundle_sha256": closure["release"]["bundle"]["sha256"],
        "release_bundle_bytes": closure["release"]["bundle"]["bytes"],
        "role_materials": role_materials,
        "image_artifacts": closure["images"],
        "role_runtime_image_ids": runtime_inventory,
        "convergence_runtime_targets": runtime_target_descriptor,
        "remote_receiver_signing_policies": remote_receiver_signing_policies,
        "postgres_runtime_uid": 70,
        "postgres_runtime_gid": 70,
        "postgres_image_ref": (
            f"trading_bot_postgres_boottime:15-{release_sha}"
        ),
        "legacy_bot_rollback_sha256": bot_rollback,
        "legacy_webapp_rollback_sha256": webapp_rollback,
        "legacy_bot_redis_rollback_sha256": bot_redis,
        "legacy_webapp_redis_rollback_sha256": webapp_redis,
        "shadow_compose_sha256": compose_sha256,
        "cutover_approval_sha256": ZERO_SHA256,
        "human_approval_policy_sha256": policy_sha256,
        **nginx_hashes,
        **release_contracts,
    }
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "capabilities": list(runtime_targets.RUNTIME_TARGET_CAPABILITIES),
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "created_at": created_at,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "legacy_release_sha": legacy_release_sha,
        "topology": json.loads(canonical_json_bytes(EXPECTED_TOPOLOGY)),
        "deployment": {
            "production_hostname": "coin.gold-trade.ir",
            "legacy_compose_project": "trading_bot",
            "shadow_compose_project": _shadow_project(operation_id),
            "shadow_root": str(_shadow_root(operation_id)),
            "controller_journal_path": str(secure_root / "journal.json"),
            "controller_evidence_root": str(secure_root / "evidence"),
        },
        "artifacts": artifacts,
        "policy": {field: True for field in POLICY_FIELDS},
    }
    return _validate_template(manifest)


def build_runtime_target_derivation_receipt(
    template: Mapping[str, Any],
) -> dict[str, Any]:
    """Emit the nonsecret sidecar that proves template-side rederivation.

    This helper is invoked only after ``build_template`` has independently
    recomputed the target set from release Compose and verified role archive
    environments.  It deliberately binds the pending (zero approval) template
    bytes, rather than a final manifest, so the approval remains the sole
    allowed finalization delta.
    """

    normalized = _validate_template(dict(template))
    template_payload = canonical_json_bytes(normalized)
    try:
        return runtime_targets.build_runtime_target_derivation_receipt(
            campaign_id=normalized["campaign_id"],
            operation_id=normalized["operation_id"],
            release_sha=normalized["release_sha"],
            template_sha256=hashlib.sha256(template_payload).hexdigest(),
            authorization_basis_sha256=authorization_basis_sha256(normalized),
            canonical_compose_sha256=normalized["artifacts"][
                "shadow_compose_sha256"
            ],
            convergence_runtime_targets=normalized["artifacts"][
                "convergence_runtime_targets"
            ],
        )
    except (
        KeyError,
        runtime_targets.ConvergenceRuntimeTargetDescriptorError,
    ) as exc:
        raise CutoverManifestTemplateError(
            "runtime target derivation receipt cannot bind the template"
        ) from exc


def confirmation_phrase(
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    template_sha256: str,
) -> str:
    return (
        "build-production-shadow-cutover-manifest-template:"
        f"{campaign_id}:{operation_id}:{release_sha}:{template_sha256}"
    )


def _preflight_output(
    path: Path,
    payload: bytes,
    *,
    owner_uid: int,
    label: str,
) -> str | None:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CutoverManifestTemplateError(
            f"cannot inspect {label} output"
        ) from exc
    existing = _read_stable_file(
        path,
        label=f"existing {label}",
        owner_uid=owner_uid,
        allowed_modes=frozenset({0o600}),
        maximum=MAX_JSON_BYTES,
    )
    if existing != payload:
        raise CutoverManifestTemplateError(
            f"refusing to overwrite a different {label}"
        )
    return "reused"


def _publish_output(
    path: Path,
    payload: bytes,
    *,
    owner_uid: int,
    label: str,
) -> str:
    existing = _preflight_output(
        path,
        payload,
        owner_uid=owner_uid,
        label=label,
    )
    if existing is not None:
        return existing
    try:
        write_secure_new_bytes(
            path,
            payload,
            label=label,
            mode=0o600,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise CutoverManifestTemplateError(
            f"{label} publication failed closed"
        ) from exc
    observed = _read_stable_file(
        path,
        label=f"published {label}",
        owner_uid=owner_uid,
        allowed_modes=frozenset({0o600}),
        maximum=MAX_JSON_BYTES,
    )
    if observed != payload:
        raise CutoverManifestTemplateError(
            f"published {label} differs"
        )
    return "created"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--legacy-release-sha", required=True)
    parser.add_argument("--release-closure", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--prepare-metadata", type=Path, required=True)
    parser.add_argument("--canonical-compose", type=Path, required=True)
    parser.add_argument(
        "--bot-rollback-attestation",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--webapp-rollback-attestation",
        type=Path,
        required=True,
    )
    parser.add_argument("--nginx-aggregate", type=Path, required=True)
    parser.add_argument(
        "--human-approval-policy",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--webapp-ir-remote-receiver-signing-policy",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--witness-remote-receiver-signing-policy",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--postcommit-executor-contract",
        type=Path,
        required=True,
    )
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        if os.geteuid() != 0:
            raise CutoverManifestTemplateError(
                "cutover manifest template builder must run as root"
            )
        args = build_parser().parse_args(
            sys.argv[1:] if argv is None else argv
        )
        _assert_private_directory(
            args.output_directory,
            label="cutover manifest template output directory",
            owner_uid=0,
        )
        manifest = build_template(
            campaign_id=args.campaign_id,
            operation_id=args.operation_id,
            created_at=args.created_at,
            legacy_release_sha=args.legacy_release_sha,
            release_closure=args.release_closure,
            release_root=args.release_root,
            prepare_metadata=args.prepare_metadata,
            canonical_compose=args.canonical_compose,
            bot_rollback_attestation=args.bot_rollback_attestation,
            webapp_rollback_attestation=args.webapp_rollback_attestation,
            nginx_aggregate=args.nginx_aggregate,
            human_approval_policy=args.human_approval_policy,
            webapp_ir_remote_receiver_signing_policy=(
                args.webapp_ir_remote_receiver_signing_policy
            ),
            witness_remote_receiver_signing_policy=(
                args.witness_remote_receiver_signing_policy
            ),
            postcommit_executor_contract=(
                args.postcommit_executor_contract
            ),
            owner_uid=0,
        )
        payload = canonical_json_bytes(manifest)
        template_sha256 = hashlib.sha256(payload).hexdigest()
        runtime_target_derivation_receipt = (
            build_runtime_target_derivation_receipt(manifest)
        )
        runtime_target_derivation_receipt_payload = canonical_json_bytes(
            runtime_target_derivation_receipt
        )
        required_confirmation = confirmation_phrase(
            manifest["campaign_id"],
            manifest["operation_id"],
            manifest["release_sha"],
            template_sha256,
        )
        output = args.output_directory / OUTPUT_FILENAME
        receipt_output = (
            args.output_directory / RUNTIME_TARGET_DERIVATION_RECEIPT_FILENAME
        )
        _preflight_output(
            output,
            payload,
            owner_uid=0,
            label="production shadow cutover manifest template",
        )
        _preflight_output(
            receipt_output,
            runtime_target_derivation_receipt_payload,
            owner_uid=0,
            label="runtime target derivation receipt",
        )
        result: dict[str, Any] = {
            "schema": MANIFEST_SCHEMA,
            "campaign_id": manifest["campaign_id"],
            "operation_id": manifest["operation_id"],
            "release_sha": manifest["release_sha"],
            "release_tree_sha": manifest["release_tree_sha"],
            "legacy_release_sha": manifest["legacy_release_sha"],
            "template_sha256": template_sha256,
            "runtime_target_derivation_receipt_sha256": hashlib.sha256(
                runtime_target_derivation_receipt_payload
            ).hexdigest(),
            "authorization_basis_sha256": authorization_basis_sha256(
                manifest
            ),
            "cutover_approval_sha256": ZERO_SHA256,
            "output": str(output),
            "runtime_target_derivation_receipt_output": str(receipt_output),
            "required_confirmation": required_confirmation,
            "network_io": False,
            "docker_contacted": False,
            "production_contacted": False,
            "service_mutated": False,
        }
        if not args.apply:
            if args.confirm is not None:
                raise CutoverManifestTemplateError(
                    "--confirm is valid only with --apply"
                )
            result.update(status="planned", output_mutated=False)
        else:
            if args.confirm != required_confirmation:
                raise CutoverManifestTemplateError(
                    f"apply requires --confirm {required_confirmation}"
                )
            publication = _publish_output(
                output,
                payload,
                owner_uid=0,
                label="production shadow cutover manifest template",
            )
            receipt_publication = _publish_output(
                receipt_output,
                runtime_target_derivation_receipt_payload,
                owner_uid=0,
                label="runtime target derivation receipt",
            )
            result.update(
                status="published",
                publication=publication,
                runtime_target_derivation_receipt_publication=receipt_publication,
                output_mutated=(
                    publication == "created" or receipt_publication == "created"
                ),
            )
        print(
            json.dumps(
                result,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except CutoverManifestTemplateError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                    "network_io": False,
                    "docker_contacted": False,
                    "production_contacted": False,
                    "service_mutated": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    except Exception:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": (
                        "cutover manifest template build failed closed"
                    ),
                    "error_class": "CutoverManifestTemplateError",
                    "network_io": False,
                    "docker_contacted": False,
                    "production_contacted": False,
                    "service_mutated": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
