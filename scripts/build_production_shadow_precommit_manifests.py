#!/usr/bin/env python3
"""Bind prepared production-shadow artifacts into two precommit manifests.

The builder is controller-local.  Its default mode only validates and plans.
Apply mode publishes one root-only, create-only manifest for Bot-FI and one for
WebApp-FI.  It never contacts a host, Docker, Object Storage, or a provider.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
from typing import Any, Mapping, Sequence
from uuid import UUID

from core.secure_file_io import (
    SecureFileError,
    read_secure_bytes,
    sha256_secure_file,
    write_secure_new_bytes,
)
from scripts.production_shadow_cutover_controller import (
    CutoverContractError,
    read_root_only_manifest,
)
from scripts.production_shadow_precommit_worker import (
    ARTIFACT_KINDS,
    IMAGE_FIELDS,
    MANIFEST_FIELDS,
    MANIFEST_SCHEMA,
    operation_paths,
)
from scripts.produce_production_shadow_source_snapshot import (
    MANIFEST_FIELDS as SOURCE_SNAPSHOT_FIELDS,
)
from scripts.produce_production_shadow_prepare_material import (
    FINAL_PREPARE_FIELDS,
)


SET_SCHEMA = "production-shadow-precommit-manifest-set-v1"
CLOSURE_SCHEMA = "production-shadow-release-artifact-closure-v2"
PREPARE_SET_SCHEMA = "production-shadow-prepare-material-set-v1"
SOURCE_SNAPSHOT_SCHEMA = "production-shadow-source-snapshot-v1"
FINAL_PREPARE_SCHEMA = "production-shadow-final-prepare-material-v1"
ROLES = ("bot_fi", "webapp_fi")
IMAGE_KINDS = ("app", "postgres", "redis", "nginx")
IMAGE_FILENAMES = {
    "app": "app-image.tar",
    "postgres": "postgres-image.tar",
    "redis": "redis-image.tar",
    "nginx": "nginx-image.tar",
}
OUTPUT_FILENAMES = {
    "bot_fi": "precommit-operation-bot-fi.json",
    "webapp_fi": "precommit-operation-webapp-fi.json",
}
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024 * 1024
MAX_TAR_MEMBERS = 64
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
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-z_]{1,64}$")


class PrecommitManifestBuildError(RuntimeError):
    """The precommit manifest set is incomplete or not exactly bound."""


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PrecommitManifestBuildError(
            "precommit manifest contains non-canonical JSON data"
        ) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PrecommitManifestBuildError(
                f"duplicate JSON field is forbidden: {key}"
            )
        result[key] = value
    return result


def _canonical_operation_id(value: Any) -> str:
    if not isinstance(value, str):
        raise PrecommitManifestBuildError("operation id is invalid")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise PrecommitManifestBuildError("operation id is invalid") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise PrecommitManifestBuildError(
            "operation id must be a canonical UUIDv4"
        )
    return value


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == "0" * 64
    ):
        raise PrecommitManifestBuildError(f"{label} is not a nonzero SHA-256")
    return value


def _bounded_bytes(value: Any, *, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_ARTIFACT_BYTES
    ):
        raise PrecommitManifestBuildError(f"{label} size is invalid")
    return value


def _read_json(
    path: Path,
    *,
    label: str,
    require_canonical: bool,
) -> tuple[dict[str, Any], bytes]:
    if not path.is_absolute():
        raise PrecommitManifestBuildError(f"{label} path must be absolute")
    try:
        raw = read_secure_bytes(
            path,
            label=label,
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise PrecommitManifestBuildError(f"{label} is unsafe") from exc
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except PrecommitManifestBuildError:
        raise
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise PrecommitManifestBuildError(f"{label} is invalid JSON") from exc
    if not isinstance(document, dict):
        raise PrecommitManifestBuildError(f"{label} must be an object")
    if require_canonical and raw != _canonical_json(document):
        raise PrecommitManifestBuildError(f"{label} is not canonical JSON")
    return document, raw


def _assert_private_directory(path: Path, *, label: str) -> None:
    if not path.is_absolute():
        raise PrecommitManifestBuildError(f"{label} must be absolute")
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise PrecommitManifestBuildError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise PrecommitManifestBuildError(
            f"{label} must be root-owned mode 0700"
        )


def _hash_artifact(
    path: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    label: str,
) -> None:
    if not path.is_absolute():
        raise PrecommitManifestBuildError(f"{label} path must be absolute")
    try:
        observed = sha256_secure_file(
            path,
            label=label,
            owner_uid=0,
            max_size=MAX_ARTIFACT_BYTES,
        )
    except SecureFileError as exc:
        raise PrecommitManifestBuildError(f"{label} is unsafe") from exc
    if observed != (expected_sha256, expected_bytes):
        raise PrecommitManifestBuildError(f"{label} identity differs")


def _load_controller(path: Path) -> tuple[dict[str, Any], str]:
    try:
        manifest, digest = read_root_only_manifest(
            path,
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
        raw = read_secure_bytes(
            path,
            label="production cutover manifest",
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
    except (CutoverContractError, SecureFileError) as exc:
        raise PrecommitManifestBuildError(
            "production cutover manifest is invalid"
        ) from exc
    if raw != _canonical_json(manifest):
        raise PrecommitManifestBuildError(
            "production cutover manifest is not canonical JSON"
        )
    return manifest, digest


def _validate_release_closure(
    path: Path,
    *,
    controller: Mapping[str, Any],
) -> dict[str, Any]:
    document, _raw = _read_json(
        path,
        label="release artifact closure",
        require_canonical=False,
    )
    expected_fields = {
        "schema",
        "operation_id",
        "release",
        "images",
        "source_engine_observations",
        "verified_image_contracts",
        "constraints",
    }
    release = document.get("release")
    if (
        set(document) != expected_fields
        or document.get("schema") != CLOSURE_SCHEMA
        or document.get("operation_id") != controller["operation_id"]
        or not isinstance(release, dict)
        or set(release) != {"commit_sha", "tree_sha", "bundle"}
        or release.get("commit_sha") != controller["release_sha"]
        or release.get("tree_sha") != controller["release_tree_sha"]
        or not isinstance(release.get("bundle"), dict)
        or set(release["bundle"]) != {"filename", "sha256", "bytes"}
        or release["bundle"].get("filename") != "release.bundle"
        or document.get("images") != controller["artifacts"]["image_artifacts"]
        or set(document.get("source_engine_observations", {}))
        != set(IMAGE_KINDS)
        or set(document.get("verified_image_contracts", {}))
        != set(IMAGE_KINDS)
        or document.get("constraints")
        != {
            "source_backup_included": False,
            "role_material_included": False,
            "secrets_included": False,
            "network_transfer_performed": False,
            "container_runtime_changed": False,
        }
    ):
        raise PrecommitManifestBuildError(
            "release artifact closure differs from the controller manifest"
        )
    bundle = release["bundle"]
    bundle_sha256 = _nonzero_sha256(
        bundle["sha256"],
        label="release bundle",
    )
    bundle_bytes = _bounded_bytes(
        bundle["bytes"],
        label="release bundle",
    )
    if (
        bundle_sha256
        != controller["artifacts"]["release_bundle_sha256"]
        or bundle_bytes
        != controller["artifacts"]["release_bundle_bytes"]
    ):
        raise PrecommitManifestBuildError(
            "release bundle differs from the controller manifest"
        )
    _hash_artifact(
        path.parent / "release.bundle",
        expected_sha256=bundle_sha256,
        expected_bytes=bundle_bytes,
        label="release bundle",
    )
    for kind in IMAGE_KINDS:
        image = document["images"][kind]
        _hash_artifact(
            path.parent / IMAGE_FILENAMES[kind],
            expected_sha256=_nonzero_sha256(
                image["archive_sha256"],
                label=f"{kind} image archive",
            ),
            expected_bytes=_bounded_bytes(
                image["archive_bytes"],
                label=f"{kind} image archive",
            ),
            label=f"{kind} image archive",
        )
    return document


def _read_role_archive(
    path: Path,
    *,
    role: str,
    operation_id: str,
    release_sha: str,
    runtime_image_ids: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise PrecommitManifestBuildError(
            f"{role} role material is unavailable"
        ) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or not 1 <= metadata.st_size <= MAX_ARTIFACT_BYTES
    ):
        raise PrecommitManifestBuildError(
            f"{role} role material is unsafe"
        )
    expected_names = {
        "final-prepare-manifest.json",
        "role-compose.yml",
        "runtime.env.role",
        "ca.crt",
    }
    payloads: dict[str, bytes] = {}
    try:
        with tarfile.open(path, mode="r:") as archive:
            members = archive.getmembers()
            if (
                not 1 <= len(members) <= MAX_TAR_MEMBERS
                or len({member.name for member in members}) != len(members)
                or {member.name for member in members} != expected_names
            ):
                raise PrecommitManifestBuildError(
                    f"{role} role material member closure differs"
                )
            for member in members:
                pure = PurePosixPath(member.name)
                if (
                    not member.isreg()
                    or pure.is_absolute()
                    or ".." in pure.parts
                    or member.uid != 0
                    or member.gid != 0
                    or stat.S_IMODE(member.mode) != 0o600
                    or member.mtime != 0
                    or not 0 <= member.size <= MAX_JSON_BYTES
                ):
                    raise PrecommitManifestBuildError(
                        f"{role} role material contains an unsafe member"
                    )
                stream = archive.extractfile(member)
                if stream is None:
                    raise PrecommitManifestBuildError(
                        f"{role} role material member is unreadable"
                    )
                payload = stream.read(MAX_JSON_BYTES + 1)
                if len(payload) != member.size or len(payload) > MAX_JSON_BYTES:
                    raise PrecommitManifestBuildError(
                        f"{role} role material member size differs"
                    )
                payloads[member.name] = payload
    except PrecommitManifestBuildError:
        raise
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise PrecommitManifestBuildError(
            f"{role} role material is invalid"
        ) from exc
    try:
        internal = json.loads(
            payloads["final-prepare-manifest.json"].decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise PrecommitManifestBuildError(
            f"{role} role manifest is invalid JSON"
        ) from exc
    if (
        not isinstance(internal, dict)
        or set(internal) != FINAL_PREPARE_FIELDS
        or internal.get("schema") != FINAL_PREPARE_SCHEMA
        or internal.get("operation_id") != operation_id
        or internal.get("release_sha") != release_sha
        or internal.get("role") != role
        or internal.get("runtime_image_ids") != runtime_image_ids
        or payloads["final-prepare-manifest.json"] != _canonical_json(internal)
    ):
        raise PrecommitManifestBuildError(
            f"{role} role manifest binding differs"
        )
    entries = internal.get("entries")
    if not isinstance(entries, list) or len(entries) != 3:
        raise PrecommitManifestBuildError(
            f"{role} role manifest entries differ"
        )
    by_name = {
        entry.get("archive_path"): entry
        for entry in entries
        if isinstance(entry, dict)
    }
    if set(by_name) != {"role-compose.yml", "runtime.env.role", "ca.crt"}:
        raise PrecommitManifestBuildError(
            f"{role} role manifest entry closure differs"
        )
    for name, entry in by_name.items():
        if (
            set(entry) != {
                "archive_path",
                "destination",
                "sha256",
                "bytes",
                "mode",
            }
            or entry["mode"] != "0600"
            or entry["sha256"]
            != hashlib.sha256(payloads[name]).hexdigest()
            or entry["bytes"] != len(payloads[name])
        ):
            raise PrecommitManifestBuildError(
                f"{role} role manifest entry differs"
            )
    return internal, payloads


def _validate_prepare_set(
    path: Path,
    *,
    controller: Mapping[str, Any],
    role_material_directory: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    document, _raw = _read_json(
        path,
        label="production prepare material set",
        require_canonical=True,
    )
    expected_fields = {
        "schema",
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
    if (
        set(document) != expected_fields
        or document.get("schema") != PREPARE_SET_SCHEMA
        or document.get("operation_id") != controller["operation_id"]
        or document.get("release_sha") != controller["release_sha"]
        or document.get("canonical_compose_sha256")
        != controller["artifacts"]["shadow_compose_sha256"]
        or document.get("activation_secrets_included") is not False
        or document.get("precommit_manifest_bound") is not False
        or document.get("controller_bindings")
        != {
            "role_materials": controller["artifacts"]["role_materials"],
            "role_runtime_image_ids": controller["artifacts"][
                "role_runtime_image_ids"
            ],
        }
        or not isinstance(document.get("roles"), dict)
        or set(document["roles"]) != set(controller["topology"])
    ):
        raise PrecommitManifestBuildError(
            "prepare material set differs from the controller manifest"
        )
    if any(
        not isinstance(document["roles"][role], dict)
        or set(document["roles"][role]) != PREPARE_ROLE_FIELDS
        for role in controller["topology"]
    ):
        raise PrecommitManifestBuildError(
            "prepare material role fields differ"
        )
    details: dict[str, dict[str, str]] = {}
    for role in ROLES:
        row = document["roles"][role]
        material = controller["artifacts"]["role_materials"][role]
        if (
            not isinstance(row, dict)
            or set(row) != PREPARE_ROLE_FIELDS
            or row.get("sha256") != material["sha256"]
            or row.get("bytes") != material["bytes"]
            or row.get("format") != material["format"]
            or row.get("transport") != material["transport"]
            or row.get("filename") != f"role-material-{role.replace('_', '-')}.tar"
        ):
            raise PrecommitManifestBuildError(
                f"{role} prepare material identity differs"
            )
        archive = role_material_directory / row["filename"]
        _hash_artifact(
            archive,
            expected_sha256=row["sha256"],
            expected_bytes=row["bytes"],
            label=f"{role} role material",
        )
        internal, payloads = _read_role_archive(
            archive,
            role=role,
            operation_id=controller["operation_id"],
            release_sha=controller["release_sha"],
            runtime_image_ids=controller["artifacts"][
                "role_runtime_image_ids"
            ][role],
        )
        internal_sha256 = hashlib.sha256(
            payloads["final-prepare-manifest.json"]
        ).hexdigest()
        if (
            row.get("internal_manifest_sha256") != internal_sha256
            or row.get("stage_operation_manifest_sha256")
            != internal.get("operation_manifest_sha256")
            or row.get("stage_attestation_sha256")
            != internal.get("stage_attestation_sha256")
        ):
            raise PrecommitManifestBuildError(
                f"{role} internal prepare material binding differs"
            )
        details[role] = {
            "role_compose_sha256": hashlib.sha256(
                payloads["role-compose.yml"]
            ).hexdigest(),
            "environment_sha256": hashlib.sha256(
                payloads["runtime.env.role"]
            ).hexdigest(),
        }
    return document, details


def _validate_source_snapshot(
    path: Path,
    *,
    role: str,
    controller: Mapping[str, Any],
    controller_sha256: str,
) -> dict[str, Any]:
    document, _raw = _read_json(
        path,
        label=f"{role} source snapshot",
        require_canonical=True,
    )
    if set(document) != SOURCE_SNAPSHOT_FIELDS:
        raise PrecommitManifestBuildError(
            f"{role} source snapshot fields differ"
        )
    expected = {
        "schema": SOURCE_SNAPSHOT_SCHEMA,
        "status": "source-snapshot-created",
        "operation_id": controller["operation_id"],
        "role": role,
        "mode": "live-baseline",
        "release_sha": controller["release_sha"],
        "legacy_release_sha": controller["legacy_release_sha"],
        "controller_manifest_sha256": controller_sha256,
        "approval_sha256": controller["artifacts"]["cutover_approval_sha256"],
        "freeze_evidence_sha256": None,
        "source_mutated": False,
        "current_mutated": False,
        "source_stopped_or_restarted": False,
        "redis_restored": False,
    }
    if any(document.get(key) != value for key, value in expected.items()):
        raise PrecommitManifestBuildError(
            f"{role} source snapshot binding differs"
        )
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {
        "database-backup",
        "uploads-archive",
        "audit-archive",
    }:
        raise PrecommitManifestBuildError(
            f"{role} source snapshot artifact closure differs"
        )
    filenames = {
        "database-backup": "database.dump",
        "uploads-archive": "uploads.tar.gz",
        "audit-archive": "audit.tar.gz",
    }
    for kind, filename in filenames.items():
        row = artifacts[kind]
        if (
            not isinstance(row, dict)
            or set(row) != {"sha256", "bytes", "restored_tree_sha256"}
        ):
            raise PrecommitManifestBuildError(
                f"{role} {kind} binding differs"
            )
        digest = _nonzero_sha256(row["sha256"], label=f"{role} {kind}")
        size = _bounded_bytes(row["bytes"], label=f"{role} {kind}")
        tree = row["restored_tree_sha256"]
        if kind == "database-backup":
            if tree is not None:
                raise PrecommitManifestBuildError(
                    f"{role} database snapshot tree hash must be null"
                )
        else:
            _nonzero_sha256(tree, label=f"{role} {kind} tree")
        _hash_artifact(
            path.parent / filename,
            expected_sha256=digest,
            expected_bytes=size,
            label=f"{role} {kind}",
        )
    source_database = document.get("source_database")
    if (
        not isinstance(source_database, dict)
        or set(source_database)
        != {
            "alembic_revision",
            "fingerprint_algorithm",
            "database_fingerprint_sha256",
            "row_count",
            "table_count",
        }
        or not isinstance(source_database["alembic_revision"], str)
        or REVISION_RE.fullmatch(source_database["alembic_revision"]) is None
        or source_database["fingerprint_algorithm"]
        != "pg-copy-jsonl-sha256-canonical-session-v1"
        or not isinstance(source_database["row_count"], int)
        or isinstance(source_database["row_count"], bool)
        or source_database["row_count"] < 0
        or not isinstance(source_database["table_count"], int)
        or isinstance(source_database["table_count"], bool)
        or not 1 <= source_database["table_count"] <= 100_000
    ):
        raise PrecommitManifestBuildError(
            f"{role} source database binding differs"
        )
    _nonzero_sha256(
        source_database["database_fingerprint_sha256"],
        label=f"{role} source database fingerprint",
    )
    restore = document.get("restore_drill")
    redis = document.get("redis_rollback_only")
    if (
        not isinstance(restore, dict)
        or restore.get("status") != "passed"
        or restore.get("network_mode") != "none"
        or restore.get("pull_policy") != "never"
        or restore.get("source_or_current_mounted") is not False
        or restore.get("scratch_resources_removed") is not True
        or restore.get("zero_residue") is not True
        or not isinstance(redis, dict)
        or redis.get("archive_created") is not False
        or redis.get("restore") is not False
        or redis.get("policy") != "sealed-rollback-evidence-only"
    ):
        raise PrecommitManifestBuildError(
            f"{role} source restore/Redis evidence differs"
        )
    return document


def _migration_assignment(module: ast.Module, name: str) -> Any:
    values: list[Any] = []
    for statement in module.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            target = statement.target
            value = statement.value
        if isinstance(target, ast.Name) and target.id == name and value is not None:
            try:
                values.append(ast.literal_eval(value))
            except (TypeError, ValueError) as exc:
                raise PrecommitManifestBuildError(
                    f"migration {name} is not literal"
                ) from exc
    if len(values) != 1:
        raise PrecommitManifestBuildError(
            f"migration must define exactly one {name}"
        )
    return values[0]


def _target_migration_revision(release_root: Path) -> str:
    if not release_root.is_absolute():
        raise PrecommitManifestBuildError("release root must be absolute")
    versions = release_root / "migrations" / "versions"
    try:
        root = release_root.stat(follow_symlinks=False)
        metadata = versions.stat(follow_symlinks=False)
        candidates = sorted(versions.iterdir())
    except OSError as exc:
        raise PrecommitManifestBuildError(
            "release migration directory is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(root.st_mode)
        or root.st_uid != 0
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or versions.is_symlink()
        or not candidates
        or len(candidates) > 10_000
    ):
        raise PrecommitManifestBuildError(
            "release migration directory is unsafe"
        )
    parents: dict[str, tuple[str, ...]] = {}
    for path in candidates:
        try:
            item = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise PrecommitManifestBuildError(
                "migration source is unavailable"
            ) from exc
        if path.name == "__init__.py":
            if (
                not stat.S_ISREG(item.st_mode)
                or item.st_uid != 0
                or item.st_nlink != 1
                or stat.S_IMODE(item.st_mode) & 0o022
                or item.st_size > 1024 * 1024
            ):
                raise PrecommitManifestBuildError(
                    "migration package initializer is unsafe"
                )
            continue
        if (
            path.suffix != ".py"
            or not stat.S_ISREG(item.st_mode)
            or item.st_uid != 0
            or item.st_nlink != 1
            or stat.S_IMODE(item.st_mode) & 0o022
            or not 1 <= item.st_size <= 1024 * 1024
        ):
            raise PrecommitManifestBuildError(
                "release migration directory contains an unsafe entry"
            )
        try:
            module = ast.parse(path.read_bytes(), filename=path.name)
        except (OSError, SyntaxError, ValueError) as exc:
            raise PrecommitManifestBuildError(
                "migration source is invalid"
            ) from exc
        revision = _migration_assignment(module, "revision")
        down = _migration_assignment(module, "down_revision")
        if not isinstance(revision, str) or REVISION_RE.fullmatch(revision) is None:
            raise PrecommitManifestBuildError("migration revision is invalid")
        if down is None:
            revision_parents: tuple[str, ...] = ()
        elif isinstance(down, str) and REVISION_RE.fullmatch(down):
            revision_parents = (down,)
        elif (
            isinstance(down, (tuple, list))
            and down
            and all(
                isinstance(value, str) and REVISION_RE.fullmatch(value)
                for value in down
            )
        ):
            revision_parents = tuple(down)
        else:
            raise PrecommitManifestBuildError("migration ancestry is invalid")
        if revision in parents or len(set(revision_parents)) != len(
            revision_parents
        ):
            raise PrecommitManifestBuildError(
                "migration graph contains a duplicate"
            )
        parents[revision] = revision_parents
    unknown = {
        parent
        for values in parents.values()
        for parent in values
        if parent not in parents
    }
    children = {parent for values in parents.values() for parent in values}
    heads = set(parents) - children
    if unknown or len(heads) != 1:
        raise PrecommitManifestBuildError(
            "release migration graph must have one closed head"
        )
    return next(iter(heads))


def _hash_release_code(release_root: Path, relative: str) -> str:
    path = release_root / relative
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
            or stat.S_IMODE(before.st_mode) not in {0o644, 0o755}
            or not 1 <= before.st_size <= MAX_JSON_BYTES
        ):
            raise PrecommitManifestBuildError(
                f"release code {relative} is unsafe"
            )
        digest = hashlib.sha256()
        consumed = 0
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            consumed += len(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
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
            consumed != before.st_size
            or any(
                getattr(before, field) != getattr(after, field)
                for field in stable
            )
        ):
            raise PrecommitManifestBuildError(
                f"release code {relative} changed while being read"
            )
        return digest.hexdigest()
    except PrecommitManifestBuildError:
        raise
    except OSError as exc:
        raise PrecommitManifestBuildError(
            f"release code {relative} is unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def build_manifests(
    *,
    controller_manifest: Path,
    release_closure: Path,
    prepare_metadata: Path,
    role_material_directory: Path,
    bot_fi_source_snapshot: Path,
    webapp_fi_source_snapshot: Path,
    release_root: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    paths = (
        controller_manifest,
        release_closure,
        prepare_metadata,
        role_material_directory,
        bot_fi_source_snapshot,
        webapp_fi_source_snapshot,
        release_root,
    )
    if len({str(path) for path in paths}) != len(paths):
        raise PrecommitManifestBuildError("input paths must be distinct")
    controller, controller_sha256 = _load_controller(controller_manifest)
    operation_id = _canonical_operation_id(controller["operation_id"])
    closure = _validate_release_closure(
        release_closure,
        controller=controller,
    )
    _assert_private_directory(
        role_material_directory,
        label="role material directory",
    )
    prepare, role_details = _validate_prepare_set(
        prepare_metadata,
        controller=controller,
        role_material_directory=role_material_directory,
    )
    sources = {
        "bot_fi": _validate_source_snapshot(
            bot_fi_source_snapshot,
            role="bot_fi",
            controller=controller,
            controller_sha256=controller_sha256,
        ),
        "webapp_fi": _validate_source_snapshot(
            webapp_fi_source_snapshot,
            role="webapp_fi",
            controller=controller,
            controller_sha256=controller_sha256,
        ),
    }
    target_revision = _target_migration_revision(release_root)
    worker_sha256 = _hash_release_code(
        release_root,
        "scripts/production_shadow_precommit_worker.py",
    )
    acceptance_sha256 = _hash_release_code(
        release_root,
        "scripts/produce_production_shadow_readonly_acceptance.py",
    )
    release_bundle = closure["release"]["bundle"]
    manifests: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        material = controller["artifacts"]["role_materials"][role]
        source = sources[role]
        artifacts: dict[str, dict[str, Any]] = {
            "release-bundle": {
                "sha256": release_bundle["sha256"],
                "bytes": release_bundle["bytes"],
                "restored_tree_sha256": None,
            },
            "role-material": {
                "sha256": material["sha256"],
                "bytes": material["bytes"],
                "restored_tree_sha256": None,
            },
        }
        for kind in IMAGE_KINDS:
            image = closure["images"][kind]
            artifacts[f"{kind}-image-archive"] = {
                "sha256": image["archive_sha256"],
                "bytes": image["archive_bytes"],
                "restored_tree_sha256": None,
            }
        artifacts.update(
            {
                kind: dict(source["artifacts"][kind])
                for kind in (
                    "database-backup",
                    "uploads-archive",
                    "audit-archive",
                )
            }
        )
        document = {
            "schema": MANIFEST_SCHEMA,
            "operation_id": operation_id,
            "role": role,
            "release_sha": controller["release_sha"],
            "release_tree_sha": controller["release_tree_sha"],
            "controller_manifest_sha256": controller_sha256,
            "approval_sha256": controller["artifacts"][
                "cutover_approval_sha256"
            ],
            "role_material_sha256": material["sha256"],
            "canonical_compose_sha256": prepare[
                "canonical_compose_sha256"
            ],
            "role_compose_sha256": role_details[role][
                "role_compose_sha256"
            ],
            "environment_sha256": role_details[role][
                "environment_sha256"
            ],
            "worker_sha256": worker_sha256,
            "acceptance_producer_sha256": acceptance_sha256,
            "image_artifacts": {
                kind: dict(closure["images"][kind])
                for kind in IMAGE_KINDS
            },
            "runtime_image_ids": dict(
                controller["artifacts"]["role_runtime_image_ids"][role]
            ),
            "artifacts": artifacts,
            "source_database": dict(source["source_database"]),
            "target_migration_revision": target_revision,
            "postgres_runtime_uid": controller["artifacts"][
                "postgres_runtime_uid"
            ],
            "postgres_runtime_gid": controller["artifacts"][
                "postgres_runtime_gid"
            ],
        }
        if (
            set(document) != MANIFEST_FIELDS
            or set(document["artifacts"]) != set(ARTIFACT_KINDS)
            or set(document["image_artifacts"]) != set(IMAGE_FIELDS)
            or set(document["runtime_image_ids"]) != set(IMAGE_FIELDS)
        ):
            raise PrecommitManifestBuildError(
                f"{role} precommit manifest fields differ"
            )
        manifests[role] = document
    summary = {
        "schema": SET_SCHEMA,
        "status": "validated",
        "operation_id": operation_id,
        "release_sha": controller["release_sha"],
        "controller_manifest_sha256": controller_sha256,
        "approval_sha256": controller["artifacts"]["cutover_approval_sha256"],
        "target_migration_revision": target_revision,
        "outputs": {
            role: {
                "filename": OUTPUT_FILENAMES[role],
                "sha256": hashlib.sha256(
                    _canonical_json(manifests[role]) + b"\n"
                ).hexdigest(),
                "install_path": str(
                    operation_paths(
                        operation_id,
                        controller["release_sha"],
                        role,
                    ).manifest
                ),
            }
            for role in ROLES
        },
        "network_io": False,
        "production_mutated": False,
    }
    return manifests, summary


def _preflight_output(path: Path, payload: bytes, *, label: str) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise PrecommitManifestBuildError(
            f"cannot inspect {label}"
        ) from exc
    try:
        existing = read_secure_bytes(
            path,
            label=label,
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise PrecommitManifestBuildError(f"{label} is unsafe") from exc
    if existing != payload:
        raise PrecommitManifestBuildError(
            f"{label} already exists with different bytes"
        )
    return True


def publish_manifests(
    manifests: Mapping[str, Mapping[str, Any]],
    *,
    output_directory: Path,
) -> Mapping[str, str]:
    _assert_private_directory(
        output_directory,
        label="precommit manifest output directory",
    )
    outputs = {
        role: (
            output_directory / OUTPUT_FILENAMES[role],
            _canonical_json(manifests[role]) + b"\n",
        )
        for role in ROLES
    }
    existing = {
        role: _preflight_output(
            path,
            payload,
            label=f"{role} precommit manifest",
        )
        for role, (path, payload) in outputs.items()
    }
    for role, (path, payload) in outputs.items():
        if existing[role]:
            continue
        try:
            write_secure_new_bytes(
                path,
                payload,
                label=f"{role} precommit manifest",
                mode=0o600,
                max_size=MAX_JSON_BYTES,
            )
        except SecureFileError as exc:
            raise PrecommitManifestBuildError(
                f"{role} precommit manifest could not be published"
            ) from exc
    for role, (path, payload) in outputs.items():
        try:
            observed = read_secure_bytes(
                path,
                label=f"{role} precommit manifest",
                owner_uid=0,
                max_size=MAX_JSON_BYTES,
            )
        except SecureFileError as exc:
            raise PrecommitManifestBuildError(
                f"{role} precommit manifest could not be verified"
            ) from exc
        if observed != payload:
            raise PrecommitManifestBuildError(
                f"{role} precommit manifest differs after publication"
            )
    return {
        role: "reused" if existing[role] else "created"
        for role in ROLES
    }


def confirmation_phrase(operation_id: str, release_sha: str) -> str:
    return (
        "build-production-shadow-precommit-manifests:"
        f"{operation_id}:{release_sha}"
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-manifest", type=Path, required=True)
    parser.add_argument("--release-closure", type=Path, required=True)
    parser.add_argument("--prepare-metadata", type=Path, required=True)
    parser.add_argument("--role-material-directory", type=Path, required=True)
    parser.add_argument("--bot-fi-source-snapshot", type=Path, required=True)
    parser.add_argument("--webapp-fi-source-snapshot", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise PrecommitManifestBuildError(
                "precommit manifest builder must run as root"
            )
        manifests, summary = build_manifests(
            controller_manifest=args.controller_manifest,
            release_closure=args.release_closure,
            prepare_metadata=args.prepare_metadata,
            role_material_directory=args.role_material_directory,
            bot_fi_source_snapshot=args.bot_fi_source_snapshot,
            webapp_fi_source_snapshot=args.webapp_fi_source_snapshot,
            release_root=args.release_root,
        )
        required = confirmation_phrase(
            summary["operation_id"],
            summary["release_sha"],
        )
        if not args.apply:
            if args.confirm is not None:
                raise PrecommitManifestBuildError(
                    "--confirm is valid only with --apply"
                )
            result = {
                **summary,
                "status": "planned",
                "required_confirmation": required,
                "outputs_mutated": False,
            }
        else:
            if args.confirm != required:
                raise PrecommitManifestBuildError(
                    f"apply requires --confirm {required}"
                )
            publications = publish_manifests(
                manifests,
                output_directory=args.output_directory,
            )
            result = {
                **summary,
                "status": "published",
                "required_confirmation": required,
                "outputs_mutated": True,
                "publications": publications,
            }
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except PrecommitManifestBuildError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                    "production_mutated": False,
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
                    "error": "precommit manifest build failed closed",
                    "error_class": "PrecommitManifestBuildError",
                    "production_mutated": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
