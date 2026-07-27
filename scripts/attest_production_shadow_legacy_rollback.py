#!/usr/bin/env python3
"""Attest one sealed legacy rollback set without modifying production."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping, Sequence
from uuid import UUID

from core.secure_file_io import (
    SecureFileError,
    read_secure_bytes,
    write_secure_new_bytes,
)


ATTESTATION_SCHEMA = "production-shadow-legacy-rollback-attestation-v1"
CLOSURE_SCHEMA = "production-shadow-legacy-rollback-closure-v1"
ROLES = ("bot_fi", "webapp_fi")
ROLE_PREFIXES = {"bot_fi": "foreign", "webapp_fi": "iran"}
ROLE_COMPOSE = {
    "bot_fi": "docker-compose.yml",
    "webapp_fi": "docker-compose.iran.yml",
}
ROLLBACK_ROOTS = {
    "bot_fi": Path("/root/secure-envs/trading-bot/rollback"),
    "webapp_fi": Path("/srv/trading-bot/rollbacks"),
}
BACKUP_ROOT = Path("/srv/trading-bot/backups")
MAX_FILE_BYTES = 64 * 1024 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}\n$")
STAMP_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$")
COMMON_SEALED_FILES = {
    "source.tar.gz",
    "image.tar.gz",
    "compose.resolved.yml",
    "containers.inspect.json",
    "release-sha.txt",
    "image-tag.txt",
    "image-id.txt",
    "etc-hosts",
}
ROLE_SEALED_FILES = {
    "bot_fi": COMMON_SEALED_FILES | {"git-status.txt"},
    "webapp_fi": COMMON_SEALED_FILES
    | {
        "nginx.conf",
        "nginx.stderr.log",
        "nginx-etc.tar.gz",
        "nginx-sites-enabled.txt",
    },
}
BACKUP_KINDS = ("db", "redis", "uploads", "audit")
BACKUP_SUFFIXES = {
    "db": ".sql.gz",
    "redis": ".tar.gz",
    "uploads": ".tar.gz",
    "audit": ".tar.gz",
}


class LegacyRollbackAttestationError(RuntimeError):
    """The sealed rollback set could not be proven exact."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _operation_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise LegacyRollbackAttestationError(
            "operation id must be a canonical UUIDv4"
        ) from exc
    if parsed.version != 4 or str(parsed) != value:
        raise LegacyRollbackAttestationError(
            "operation id must be a canonical UUIDv4"
        )
    return value


def _release(value: str, *, label: str) -> str:
    if not isinstance(value, str) or SHA40_RE.fullmatch(value) is None:
        raise LegacyRollbackAttestationError(f"{label} is invalid")
    if value == "0" * 40:
        raise LegacyRollbackAttestationError(f"{label} is zero")
    return value


def _sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == "0" * 64
    ):
        raise LegacyRollbackAttestationError(
            f"{label} is not a nonzero SHA-256"
        )
    return value


def _open_directory(path: Path, *, require_mode: int | None) -> int:
    if not path.is_absolute() or path == Path("/"):
        raise LegacyRollbackAttestationError(
            "rollback directory path is invalid"
        )
    descriptor = os.open(
        "/",
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(
                component,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or (
                require_mode is not None
                and stat.S_IMODE(metadata.st_mode) != require_mode
            )
        ):
            raise LegacyRollbackAttestationError(
                "rollback directory ownership or mode is unsafe"
            )
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _read_relative(
    directory_fd: int,
    name: str,
    *,
    label: str,
    maximum: int,
    required_mode: int | None,
) -> bytes:
    if SAFE_NAME_RE.fullmatch(name) is None:
        raise LegacyRollbackAttestationError(f"{label} name is unsafe")
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or before.st_nlink != 1
            or mode & 0o022
            or (required_mode is not None and mode != required_mode)
            or not 1 <= before.st_size <= maximum
        ):
            raise LegacyRollbackAttestationError(
                f"{label} is unavailable or unsafe"
            )
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
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
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if (
            len(payload) != before.st_size
            or len(payload) > maximum
            or any(
                getattr(before, field) != getattr(after, field)
                for field in stable
            )
        ):
            raise LegacyRollbackAttestationError(
                f"{label} changed while being read"
            )
        return payload
    except LegacyRollbackAttestationError:
        raise
    except OSError as exc:
        raise LegacyRollbackAttestationError(
            f"{label} is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _hash_relative(
    directory_fd: int,
    name: str,
    *,
    label: str,
    expected_sha256: str | None = None,
    expected_bytes: int | None = None,
    required_mode: int | None,
) -> tuple[str, int]:
    if SAFE_NAME_RE.fullmatch(name) is None:
        raise LegacyRollbackAttestationError(f"{label} name is unsafe")
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or before.st_nlink != 1
            or mode & 0o022
            or (required_mode is not None and mode != required_mode)
            or not 1 <= before.st_size <= MAX_FILE_BYTES
        ):
            raise LegacyRollbackAttestationError(
                f"{label} is unavailable or unsafe"
            )
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise LegacyRollbackAttestationError(
                    f"{label} is oversized"
                )
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
        if size != before.st_size or any(
            getattr(before, field) != getattr(after, field)
            for field in stable
        ):
            raise LegacyRollbackAttestationError(
                f"{label} changed while being hashed"
            )
        observed = digest.hexdigest(), size
    except LegacyRollbackAttestationError:
        raise
    except OSError as exc:
        raise LegacyRollbackAttestationError(
            f"{label} is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if expected_sha256 is not None and observed[0] != expected_sha256:
        raise LegacyRollbackAttestationError(f"{label} digest differs")
    if expected_bytes is not None and observed[1] != expected_bytes:
        raise LegacyRollbackAttestationError(f"{label} size differs")
    return observed


def _parse_sha256sums(raw: bytes) -> dict[str, str]:
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise LegacyRollbackAttestationError(
            "SHA256SUMS is not ASCII"
        ) from exc
    if not text.endswith("\n") or "\r" in text:
        raise LegacyRollbackAttestationError(
            "SHA256SUMS line encoding is invalid"
        )
    result: dict[str, str] = {}
    for line in text.splitlines():
        if len(line) < 67 or line[64:66] != "  ":
            raise LegacyRollbackAttestationError(
                "SHA256SUMS record is invalid"
            )
        digest, name = line[:64], line[66:]
        if (
            SHA256_RE.fullmatch(digest) is None
            or digest == "0" * 64
            or SAFE_NAME_RE.fullmatch(name) is None
            or name == "SHA256SUMS"
            or name in result
        ):
            raise LegacyRollbackAttestationError(
                "SHA256SUMS record is unsafe"
            )
        result[name] = digest
    if not result:
        raise LegacyRollbackAttestationError("SHA256SUMS is empty")
    return result


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LegacyRollbackAttestationError(
                f"duplicate backup manifest field: {key}"
            )
        result[key] = value
    return result


def _parse_backup_manifest(
    raw: bytes,
    *,
    role: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        LegacyRollbackAttestationError,
    ) as exc:
        raise LegacyRollbackAttestationError(
            "backup manifest is invalid JSON"
        ) from exc
    fields = {
        "backup_dir",
        "compose_file",
        "created_at",
        "files",
        "hostname",
        "notes",
        "restore_smoke",
        "role",
        "stamp",
        "status",
    }
    prefix = ROLE_PREFIXES[role]
    if (
        not isinstance(document, dict)
        or set(document) != fields
        or document["backup_dir"] != str(BACKUP_ROOT)
        or document["compose_file"] != ROLE_COMPOSE[role]
        or document["role"] != prefix
        or document["status"] != "ok"
        or not isinstance(document["hostname"], str)
        or not document["hostname"]
        or not isinstance(document["notes"], list)
        or any(not isinstance(item, str) for item in document["notes"])
        or not isinstance(document["created_at"], str)
    ):
        raise LegacyRollbackAttestationError(
            "backup manifest identity is invalid"
        )
    try:
        created_at = datetime.fromisoformat(
            document["created_at"].replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise LegacyRollbackAttestationError(
            "backup manifest timestamp is invalid"
        ) from exc
    if created_at.tzinfo is None or STAMP_RE.fullmatch(document["stamp"]) is None:
        raise LegacyRollbackAttestationError(
            "backup manifest timestamp is invalid"
        )
    restore = document["restore_smoke"]
    if (
        not isinstance(restore, dict)
        or set(restore) != {"error", "status", "table_count"}
        or restore["error"] is not None
        or restore["status"] != "passed"
        or isinstance(restore["table_count"], bool)
        or not isinstance(restore["table_count"], int)
        or not 1 <= restore["table_count"] <= 100_000
    ):
        raise LegacyRollbackAttestationError(
            "backup restore smoke did not pass"
        )
    files = document["files"]
    if not isinstance(files, list) or len(files) != len(BACKUP_KINDS):
        raise LegacyRollbackAttestationError(
            "backup artifact set is incomplete"
        )
    rows: dict[str, dict[str, Any]] = {}
    for row in files:
        if (
            not isinstance(row, dict)
            or set(row) != {"bytes", "kind", "path", "sha256"}
            or row["kind"] not in BACKUP_KINDS
            or row["kind"] in rows
            or isinstance(row["bytes"], bool)
            or not isinstance(row["bytes"], int)
            or not 1 <= row["bytes"] <= MAX_FILE_BYTES
        ):
            raise LegacyRollbackAttestationError(
                "backup artifact record is invalid"
            )
        kind = row["kind"]
        expected_name = (
            f"{prefix}-{kind}-{document['stamp']}{BACKUP_SUFFIXES[kind]}"
        )
        expected_path = BACKUP_ROOT / expected_name
        if (
            not isinstance(row["path"], str)
            or PurePosixPath(row["path"]) != PurePosixPath(expected_path)
        ):
            raise LegacyRollbackAttestationError(
                f"backup {kind} path is not canonical"
            )
        rows[kind] = {
            "kind": kind,
            "filename": expected_name,
            "sha256": _sha256(row["sha256"], label=f"backup {kind}"),
            "bytes": row["bytes"],
        }
    if set(rows) != set(BACKUP_KINDS):
        raise LegacyRollbackAttestationError(
            "backup artifact kinds are not exact"
        )
    return document, [rows[kind] for kind in BACKUP_KINDS]


def expected_rollback_directory(role: str, legacy_release_sha: str) -> Path:
    return (
        ROLLBACK_ROOTS[role]
        / legacy_release_sha
        / role.replace("_", "-")
    )


def inspect_rollback(
    *,
    operation_id: str,
    release_sha: str,
    legacy_release_sha: str,
    role: str,
) -> dict[str, Any]:
    operation_id = _operation_id(operation_id)
    release_sha = _release(release_sha, label="release SHA")
    legacy_release_sha = _release(
        legacy_release_sha,
        label="legacy release SHA",
    )
    if release_sha == legacy_release_sha or role not in ROLES:
        raise LegacyRollbackAttestationError(
            "release identity or role is invalid"
        )
    rollback_path = expected_rollback_directory(role, legacy_release_sha)
    directory_fd = _open_directory(rollback_path, require_mode=0o700)
    try:
        entries = sorted(os.listdir(directory_fd))
        sha_raw = _read_relative(
            directory_fd,
            "SHA256SUMS",
            label="sealed SHA256SUMS",
            maximum=MAX_JSON_BYTES,
            required_mode=0o600,
        )
        sealed = _parse_sha256sums(sha_raw)
        prefix = ROLE_PREFIXES[role]
        manifests = [
            name
            for name in sealed
            if name.startswith(f"{prefix}-backup-")
            and name.endswith(".json")
        ]
        expected_names = ROLE_SEALED_FILES[role] | set(manifests)
        if (
            len(manifests) != 1
            or set(sealed) != expected_names
            or set(entries) != expected_names | {"SHA256SUMS"}
        ):
            raise LegacyRollbackAttestationError(
                "sealed rollback file closure is not exact"
            )
        release_raw = _read_relative(
            directory_fd,
            "release-sha.txt",
            label="sealed legacy release",
            maximum=128,
            required_mode=0o600,
        )
        if release_raw != f"{legacy_release_sha}\n".encode("ascii"):
            raise LegacyRollbackAttestationError(
                "sealed legacy release differs"
            )
        image_id = _read_relative(
            directory_fd,
            "image-id.txt",
            label="sealed image id",
            maximum=128,
            required_mode=0o600,
        )
        try:
            image_id_text = image_id.decode("ascii")
        except UnicodeDecodeError as exc:
            raise LegacyRollbackAttestationError(
                "sealed image id is invalid"
            ) from exc
        if IMAGE_ID_RE.fullmatch(image_id_text) is None:
            raise LegacyRollbackAttestationError(
                "sealed image id is invalid"
            )
        sealed_rows: list[dict[str, Any]] = []
        for name in sorted(sealed):
            digest, size = _hash_relative(
                directory_fd,
                name,
                label=f"sealed rollback file {name}",
                expected_sha256=sealed[name],
                required_mode=0o600,
            )
            sealed_rows.append(
                {"filename": name, "sha256": digest, "bytes": size}
            )
        backup_name = manifests[0]
        backup_raw = _read_relative(
            directory_fd,
            backup_name,
            label="sealed backup manifest",
            maximum=MAX_JSON_BYTES,
            required_mode=0o600,
        )
    finally:
        os.close(directory_fd)

    backup, backup_rows = _parse_backup_manifest(backup_raw, role=role)
    backup_directory_fd = _open_directory(BACKUP_ROOT, require_mode=None)
    try:
        for row in backup_rows:
            _hash_relative(
                backup_directory_fd,
                row["filename"],
                label=f"backup artifact {row['kind']}",
                expected_sha256=row["sha256"],
                expected_bytes=row["bytes"],
                required_mode=None,
            )
    finally:
        os.close(backup_directory_fd)

    sha256sums_sha256 = hashlib.sha256(sha_raw).hexdigest()
    backup_manifest_sha256 = hashlib.sha256(backup_raw).hexdigest()
    backup_artifact_set_sha256 = hashlib.sha256(
        _canonical_json(backup_rows)
    ).hexdigest()
    closure = {
        "schema": CLOSURE_SCHEMA,
        "role": role,
        "legacy_release_sha": legacy_release_sha,
        "sha256sums_sha256": sha256sums_sha256,
        "sealed_files": sealed_rows,
        "backup_manifest_sha256": backup_manifest_sha256,
        "backup_artifacts": backup_rows,
        "database_restore_smoke": {
            "status": "passed",
            "table_count": backup["restore_smoke"]["table_count"],
        },
    }
    return {
        "schema": ATTESTATION_SCHEMA,
        "status": "verified",
        "operation_id": operation_id,
        "release_sha": release_sha,
        "legacy_release_sha": legacy_release_sha,
        "role": role,
        "rollback_closure_sha256": hashlib.sha256(
            _canonical_json(closure)
        ).hexdigest(),
        "legacy_redis_rollback_sha256": next(
            row["sha256"] for row in backup_rows if row["kind"] == "redis"
        ),
        "sha256sums_sha256": sha256sums_sha256,
        "backup_manifest_sha256": backup_manifest_sha256,
        "backup_artifact_set_sha256": backup_artifact_set_sha256,
        "backup_stamp": backup["stamp"],
        "database_restore_smoke_passed": True,
        "database_restore_smoke_table_count": backup[
            "restore_smoke"
        ]["table_count"],
        "sealed_file_count": len(sealed_rows),
        "backup_artifact_count": len(backup_rows),
        "source_mutated": False,
        "production_contacted": True,
    }


def _assert_output_directory(path: Path) -> None:
    descriptor = _open_directory(path, require_mode=0o700)
    os.close(descriptor)


def _publish(path: Path, document: Mapping[str, Any]) -> str:
    payload = _canonical_json(document)
    if path.exists() or path.is_symlink():
        try:
            existing = read_secure_bytes(
                path,
                label="legacy rollback attestation",
                owner_uid=0,
                max_size=MAX_JSON_BYTES,
            )
        except SecureFileError as exc:
            raise LegacyRollbackAttestationError(
                "existing legacy rollback attestation is unsafe"
            ) from exc
        if existing != payload:
            raise LegacyRollbackAttestationError(
                "refusing to overwrite a different rollback attestation"
            )
        return "reused"
    try:
        write_secure_new_bytes(
            path,
            payload,
            label="legacy rollback attestation",
            mode=0o600,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise LegacyRollbackAttestationError(
            "rollback attestation publication failed closed"
        ) from exc
    return "created"


def confirmation_phrase(
    operation_id: str,
    role: str,
    release_sha: str,
) -> str:
    return (
        "attest-production-shadow-legacy-rollback:"
        f"{operation_id}:{role}:{release_sha}"
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--legacy-release-sha", required=True)
    parser.add_argument("--role", choices=ROLES, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise LegacyRollbackAttestationError(
                "legacy rollback attestation must run as root"
            )
        _assert_output_directory(args.output_directory)
        document = inspect_rollback(
            operation_id=args.operation_id,
            release_sha=args.release_sha,
            legacy_release_sha=args.legacy_release_sha,
            role=args.role,
        )
        required = confirmation_phrase(
            document["operation_id"],
            document["role"],
            document["release_sha"],
        )
        output = (
            args.output_directory
            / f"legacy-rollback-{args.role.replace('_', '-')}.json"
        )
        result: dict[str, Any] = {
            **document,
            "required_confirmation": required,
            "output": str(output),
            "network_io": False,
            "production_mutated": False,
        }
        if not args.apply:
            if args.confirm is not None:
                raise LegacyRollbackAttestationError(
                    "--confirm is valid only with --apply"
                )
            result.update(status="planned", output_mutated=False)
        else:
            if args.confirm != required:
                raise LegacyRollbackAttestationError(
                    f"apply requires --confirm {required}"
                )
            result.update(
                status="published",
                output_mutated=True,
                publication=_publish(output, document),
            )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except LegacyRollbackAttestationError as exc:
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
                    "error": "legacy rollback attestation failed closed",
                    "error_class": "LegacyRollbackAttestationError",
                    "production_mutated": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
