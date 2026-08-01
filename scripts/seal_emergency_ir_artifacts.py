#!/usr/bin/env python3
"""Seal four already-validated Emergency IR artifacts without network I/O.

This build-host helper deliberately has no S3, SSH, Docker, DNS, or deployment
surface.  It reads a fixed set of local plaintext artifacts through stable,
owner-controlled descriptors, encrypts each one to the fixed WA-IR age
recipient, and creates the exact four-entry publish plan consumed by
``publish_emergency_ir_object_storage.py``.  Every output is create-only;
failed encryption leaves its ``.part`` file in the otherwise empty output
directory for forensic inspection rather than overwriting anything.
"""

from __future__ import annotations

import argparse
import dataclasses
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
from typing import Any, BinaryIO, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT_TEXT = str(REPO_ROOT)
sys.path[:] = [entry for entry in sys.path if entry != _REPO_ROOT_TEXT]
sys.path.insert(0, _REPO_ROOT_TEXT)

from scripts import emergency_ir_object_storage_manifest as manifest


SHA_RE = re.compile(r"^[a-f0-9]{40}$", re.ASCII)
AGE_RECIPIENT_RE = re.compile(r"^age1[ac-hj-np-z02-9]{10,200}$", re.ASCII)
AGE_HEADER = b"age-encryption.org/v1\n"
HASH_CHUNK_BYTES = 1024 * 1024
DEFAULT_AGE_BINARY = Path("/usr/bin/age")
PUBLISH_PLAN_SCHEMA = "gold-trade-emergency-ir-object-storage-publish-plan-v2"
PUBLISH_PLAN_FIELDS = frozenset(
    {
        "schema",
        "campaign_id",
        "bucket",
        "prefix",
        "created_at",
        "emergency_patch_sha",
        "destination_age_recipient_key_id",
        "artifacts",
    }
)
PUBLISH_PLAN_ARTIFACT_FIELDS = frozenset(
    {
        "kind",
        "ciphertext_path",
        "plaintext_sha256",
        "plaintext_bytes",
        "ciphertext_sha256",
        "ciphertext_bytes",
    }
)
SOURCE_RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
PACKAGE_ROOT_NAME = "emergency-ir-standalone"
PACKAGE_RELEASE_SCHEMA = "gold-trade-emergency-ir-release-package-v1"
MAX_PACKAGE_RELEASE_BYTES = 1024 * 1024
MAX_PACKAGE_MEMBERS = 512
MAX_PLAINTEXT_BYTES = {
    "image_bundle": manifest.MAX_ARTIFACT_BYTES,
    "package_tar": 32 * 1024 * 1024,
    "snapshot": manifest.MAX_ARTIFACT_BYTES,
    "settings": 4 * 1024 * 1024,
}
OUTPUT_FILENAMES = {
    "image_bundle": "images.tar.age",
    "package_tar": "package.tar.age",
    "snapshot": "snapshot.dump.age",
    "settings": "settings.tar.age",
}


class EmergencyArtifactSealError(RuntimeError):
    """A local, fail-closed artifact sealing precondition failed."""


@dataclasses.dataclass(frozen=True)
class FileDigest:
    path: Path
    sha256: str
    bytes: int


def _fail(message: str) -> None:
    raise EmergencyArtifactSealError(message)


def _canonical_created_at(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail("created_at must be canonical RFC3339 UTC")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EmergencyArtifactSealError("created_at must be canonical RFC3339 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        _fail("created_at must be canonical RFC3339 UTC")
    canonical = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if canonical != value:
        _fail("created_at must be canonical RFC3339 UTC")
    return value


def _secure_directory(path: Path, *, label: str, empty: bool | None = None) -> None:
    if not path.is_absolute():
        _fail(f"{label} must be an owner-controlled absolute directory")
    current = Path("/")
    for component in path.parts[1:]:
        current /= component
        try:
            state = current.lstat()
        except OSError as exc:
            raise EmergencyArtifactSealError(f"{label} cannot be inspected") from exc
        sticky_tmp = (
            current == Path("/tmp")
            and state.st_uid == 0
            and bool(stat.S_IMODE(state.st_mode) & stat.S_ISVTX)
        )
        if (
            stat.S_ISLNK(state.st_mode)
            or not stat.S_ISDIR(state.st_mode)
            or (state.st_uid not in {0, os.geteuid()})
            or (stat.S_IMODE(state.st_mode) & 0o022 and (current == path or not sticky_tmp))
        ):
            _fail(f"{label} must be an owner-controlled absolute directory")
    try:
        state = path.lstat()
    except OSError as exc:
        raise EmergencyArtifactSealError(f"{label} cannot be inspected") from exc
    if state.st_uid != os.geteuid():
        _fail(f"{label} must be an owner-controlled absolute directory")
    if empty is True:
        try:
            if any(path.iterdir()):
                _fail(f"{label} must be empty for create-only artifact sealing")
        except OSError as exc:
            raise EmergencyArtifactSealError(f"{label} cannot be inspected") from exc


def _open_stable_regular(
    path: Path,
    *,
    label: str,
    private: bool = False,
) -> tuple[BinaryIO, os.stat_result]:
    if not path.is_absolute():
        _fail(f"{label} path must be absolute")
    try:
        before = path.lstat()
    except OSError as exc:
        raise EmergencyArtifactSealError(f"{label} cannot be inspected") from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or (private and stat.S_IMODE(before.st_mode) & 0o077)
        or not 1 <= before.st_size <= manifest.MAX_ARTIFACT_BYTES
    ):
        _fail(f"{label} must be one bounded owner-controlled regular file")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise EmergencyArtifactSealError(f"{label} cannot be opened") from exc
    opened = os.fstat(fd)
    fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
    if any(getattr(before, field) != getattr(opened, field) for field in fields):
        os.close(fd)
        _fail(f"{label} changed while being opened")
    return os.fdopen(fd, "rb", closefd=True), opened


def _digest_open_file(
    source: BinaryIO,
    opened: os.stat_result,
    *,
    path: Path,
    label: str,
) -> FileDigest:
    observed = hashlib.sha256()
    total = 0
    while True:
        chunk = source.read(HASH_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > manifest.MAX_ARTIFACT_BYTES:
            _fail(f"{label} exceeds the Emergency artifact size bound")
        observed.update(chunk)
    after = os.fstat(source.fileno())
    fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
    if total != opened.st_size or any(getattr(opened, field) != getattr(after, field) for field in fields):
        _fail(f"{label} changed while being read")
    try:
        source.seek(0)
    except OSError as exc:
        raise EmergencyArtifactSealError(f"{label} cannot be rewound") from exc
    return FileDigest(path=path, sha256=observed.hexdigest(), bytes=total)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("artifact metadata contains a duplicate field")
        result[key] = value
    return result


def _strict_json_object(payload: bytes, *, label: str, maximum_bytes: int) -> dict[str, Any]:
    if not 1 <= len(payload) <= maximum_bytes:
        _fail(f"{label} is empty or exceeds its size bound")
    try:
        parsed = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _value: _fail(f"{label} contains an invalid JSON constant"),
        )
    except EmergencyArtifactSealError:
        raise
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EmergencyArtifactSealError(f"{label} is not strict JSON") from exc
    if not isinstance(parsed, dict):
        _fail(f"{label} must be a JSON object")
    return parsed


def _package_patch_sha(path: Path) -> str:
    """Read only the self-attested package identity before encrypting it.

    This does not extract a package or trust package code.  It just binds the
    local publish plan to the release identity that activation will validate
    again after decryption on WA-IR.
    """

    source, opened = _open_stable_regular(path, label="package_tar plaintext")
    try:
        if opened.st_size > MAX_PLAINTEXT_BYTES["package_tar"]:
            _fail("package_tar plaintext exceeds the Emergency package size bound")
        release_name = f"{PACKAGE_ROOT_NAME}/RELEASE.json"
        release_member: tarfile.TarInfo | None = None
        members_by_name: dict[str, tarfile.TarInfo] = {}
        try:
            with tarfile.open(fileobj=source, mode="r:gz") as archive:
                member_count = 0
                for member in archive:
                    member_count += 1
                    if member_count > MAX_PACKAGE_MEMBERS:
                        _fail("package tar has too many members")
                    member_path = PurePosixPath(member.name)
                    if (
                        not member.name
                        or "\\" in member.name
                        or member_path.is_absolute()
                        or any(part in {"", ".", ".."} for part in member_path.parts)
                        or not member.isreg()
                        or member.issym()
                        or member.islnk()
                        or not 1 <= member.size <= 4 * 1024 * 1024
                        or member.name in members_by_name
                    ):
                        _fail("package tar member layout is unsafe")
                    members_by_name[member.name] = member
                    if member.name == release_name:
                        if release_member is not None:
                            _fail("package tar has duplicate RELEASE.json members")
                        release_member = member
                if (
                    release_member is None
                    or not release_member.isreg()
                    or release_member.issym()
                    or release_member.islnk()
                    or not 1 <= release_member.size <= MAX_PACKAGE_RELEASE_BYTES
                ):
                    _fail("package tar RELEASE.json is missing or unsafe")
                release_file = archive.extractfile(release_member)
                if release_file is None:
                    _fail("package tar RELEASE.json cannot be read")
                release_payload = release_file.read(MAX_PACKAGE_RELEASE_BYTES + 1)
                if len(release_payload) != release_member.size:
                    _fail("package tar RELEASE.json changed while being read")
        except EmergencyArtifactSealError:
            raise
        except (OSError, tarfile.TarError) as exc:
            raise EmergencyArtifactSealError("package tar is invalid") from exc
        after = os.fstat(source.fileno())
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(opened, field) != getattr(after, field) for field in fields):
            _fail("package_tar plaintext changed while being inspected")
    finally:
        source.close()

    release = _strict_json_object(
        release_payload,
        label="package tar RELEASE.json",
        maximum_bytes=MAX_PACKAGE_RELEASE_BYTES,
    )
    canonical = (
        json.dumps(release, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")
    if canonical != release_payload:
        _fail("package tar RELEASE.json is not canonical")
    if set(release) != {"schema", "source_release_sha", "emergency_patch_sha", "files"}:
        _fail("package tar RELEASE.json fields are unsupported")
    if release.get("schema") != PACKAGE_RELEASE_SCHEMA or release.get("source_release_sha") != SOURCE_RELEASE_SHA:
        _fail("package tar release identities do not match the Emergency base contract")
    patch_sha = release.get("emergency_patch_sha")
    if not isinstance(patch_sha, str) or SHA_RE.fullmatch(patch_sha) is None:
        _fail("package tar Emergency patch SHA is invalid")
    files = release.get("files")
    if not isinstance(files, list) or not files:
        _fail("package tar RELEASE.json file list is invalid")
    expected_members = {release_name}
    required = {
        "deploy/emergency-ir/docker-compose.standalone.yml",
        "deploy/emergency-ir/nginx.standalone.conf.template",
        "deploy/emergency-ir/reset-emergency-sessions.sql",
        "scripts/render_emergency_ir_standalone_env.py",
        "scripts/verify_emergency_ir_standalone.py",
        "scripts/verify_emergency_ir_image_provenance.py",
        "scripts/emergency_ir_standalone_activate.py",
    }
    seen_files: set[str] = set()
    for entry in files:
        if not isinstance(entry, Mapping) or set(entry) != {"path", "sha256", "bytes"}:
            _fail("package tar RELEASE.json file entry is invalid")
        relative = entry.get("path")
        digest = entry.get("sha256")
        size = entry.get("bytes")
        relative_path = PurePosixPath(relative) if isinstance(relative, str) else None
        if (
            not isinstance(relative, str)
            or not (relative.startswith("deploy/emergency-ir/") or relative.startswith("scripts/"))
            or "\\" in relative
            or relative_path is None
            or relative_path.is_absolute()
            or any(part in {"", ".", ".."} for part in relative_path.parts)
            or relative in seen_files
            or not isinstance(digest, str)
            or manifest.SHA256_RE.fullmatch(digest) is None
            or isinstance(size, bool)
            or not isinstance(size, int)
            or not 1 <= size <= 4 * 1024 * 1024
        ):
            _fail("package tar RELEASE.json file identity is invalid")
        seen_files.add(relative)
        expected_members.add(f"{PACKAGE_ROOT_NAME}/{relative}")
    if not required.issubset(seen_files) or set(members_by_name) != expected_members:
        _fail("package tar member set does not match its release identity")
    source, reopened = _open_stable_regular(path, label="package_tar plaintext")
    try:
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(opened, field) != getattr(reopened, field) for field in fields):
            _fail("package_tar plaintext changed before its release identity was verified")
        with tarfile.open(fileobj=source, mode="r:gz") as archive:
            for entry in files:
                relative = str(entry["path"])
                member = archive.getmember(f"{PACKAGE_ROOT_NAME}/{relative}")
                payload_file = archive.extractfile(member)
                if payload_file is None:
                    _fail("package tar file cannot be read")
                payload = payload_file.read(int(entry["bytes"]) + 1)
                if len(payload) != entry["bytes"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                    _fail("package tar file differs from its release identity")
        after = os.fstat(source.fileno())
        if any(getattr(reopened, field) != getattr(after, field) for field in fields):
            _fail("package_tar plaintext changed while its release identity was verified")
    except EmergencyArtifactSealError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise EmergencyArtifactSealError("package tar is invalid") from exc
    finally:
        source.close()
    return patch_sha


def _validate_plaintext_inputs(plaintext_paths: Mapping[str, Path]) -> str:
    """Reject aliasing or obviously wrong local artifact kinds before output.

    The heavy package/image/settings checks remain in the pinned WA-IR
    activator.  Here the goal is to stop a malformed input from reaching even
    the local ciphertext stage and to derive the one patch identity carried
    into the publisher confirmation.
    """

    identities: set[tuple[int, int]] = set()
    for kind in manifest.ARTIFACT_ORDER:
        source, opened = _open_stable_regular(
            plaintext_paths[kind],
            label=f"{kind} plaintext",
            private=kind in {"snapshot", "settings"},
        )
        try:
            if opened.st_size > MAX_PLAINTEXT_BYTES[kind]:
                _fail(f"{kind} plaintext exceeds its fixed size bound")
            identity = (opened.st_dev, opened.st_ino)
            if identity in identities:
                _fail("each Emergency artifact must use a distinct plaintext file")
            identities.add(identity)
            if kind == "snapshot":
                if source.read(5) != b"PGDMP":
                    _fail("snapshot plaintext is not a PostgreSQL custom dump")
                after = os.fstat(source.fileno())
                fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
                if any(getattr(opened, field) != getattr(after, field) for field in fields):
                    _fail("snapshot plaintext changed while being inspected")
        finally:
            source.close()
    return _package_patch_sha(plaintext_paths["package_tar"])


def _write_plan_create_only(path: Path, payload: bytes) -> None:
    if not path.is_absolute():
        _fail("publish plan output must be absolute")
    _secure_directory(path.parent, label="publish plan output directory")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        offset = 0
        view = memoryview(payload)
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                raise OSError("short output write")
            offset += written
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise EmergencyArtifactSealError("refusing to overwrite an existing Emergency publish plan") from exc
    except OSError as exc:
        raise EmergencyArtifactSealError("Emergency publish plan cannot be written") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _digest_path(path: Path, *, label: str) -> FileDigest:
    source, opened = _open_stable_regular(path, label=label, private=True)
    with source:
        return _digest_open_file(source, opened, path=path, label=label)


def _seal_one(
    *,
    source_path: Path,
    output_path: Path,
    recipient: str,
    age_binary: Path,
    label: str,
) -> tuple[FileDigest, FileDigest]:
    """Encrypt one stable descriptor to a create-only output through stdin/stdout."""

    if output_path.exists() or output_path.is_symlink():
        _fail(f"refusing to overwrite existing {label} ciphertext")
    temporary = output_path.with_name(f".{output_path.name}.{os.getpid()}.part")
    if temporary.exists() or temporary.is_symlink():
        _fail(f"refusing to overwrite existing partial {label} ciphertext")
    source, opened = _open_stable_regular(
        source_path,
        label=f"{label} plaintext",
        private=label in {"snapshot", "settings"},
    )
    try:
        plaintext = _digest_open_file(
            source,
            opened,
            path=source_path,
            label=f"{label} plaintext",
        )
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                try:
                    completed = subprocess.run(
                        [str(age_binary), "-r", recipient],
                        stdin=source,
                        stdout=output,
                        stderr=subprocess.PIPE,
                        check=False,
                        timeout=7200,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    raise EmergencyArtifactSealError(f"{label} age encryption failed") from exc
                output.flush()
                os.fsync(output.fileno())
        finally:
            descriptor = -1
        if completed.returncode != 0:
            _fail(f"{label} age encryption failed")
        after = os.fstat(source.fileno())
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(opened, field) != getattr(after, field) for field in fields):
            _fail(f"{label} plaintext changed during encryption")
    finally:
        source.close()
    ciphertext = _digest_path(temporary, label=f"{label} ciphertext")
    if ciphertext.bytes <= plaintext.bytes or ciphertext.sha256 == plaintext.sha256:
        _fail(f"{label} ciphertext does not satisfy the age artifact contract")
    with temporary.open("rb") as handle:
        if handle.read(len(AGE_HEADER)) != AGE_HEADER:
            _fail(f"{label} ciphertext is not an age-v1 stream")
    try:
        os.link(temporary, output_path, follow_symlinks=False)
    except FileExistsError as exc:
        raise EmergencyArtifactSealError(f"refusing to overwrite existing {label} ciphertext") from exc
    except OSError as exc:
        raise EmergencyArtifactSealError(f"{label} ciphertext cannot be finalized") from exc
    try:
        temporary.unlink()
    except OSError as exc:
        raise EmergencyArtifactSealError(f"{label} ciphertext temporary cannot be finalized") from exc
    return plaintext, FileDigest(path=output_path, sha256=ciphertext.sha256, bytes=ciphertext.bytes)


def _recipient_key_id(recipient: str) -> str:
    if AGE_RECIPIENT_RE.fullmatch(recipient) is None:
        _fail("destination age recipient is invalid")
    return "age-recipient-sha256:" + hashlib.sha256(recipient.encode("ascii")).hexdigest()


def _validate_age_binary(path: Path) -> None:
    if not path.is_absolute():
        _fail("age binary must be one executable absolute file")
    try:
        state = path.lstat()
    except OSError as exc:
        raise EmergencyArtifactSealError("age binary cannot be inspected") from exc
    if (
        stat.S_ISLNK(state.st_mode)
        or not stat.S_ISREG(state.st_mode)
        or state.st_uid != 0
        or stat.S_IMODE(state.st_mode) & 0o022
        or not os.access(path, os.X_OK)
    ):
        _fail("age binary must be one root-controlled executable absolute file")


def _placeholder_bootstrap_provenance() -> dict[str, Any]:
    return {
        "schema": manifest.BOOTSTRAP_PROVENANCE_SCHEMA,
        "publisher_source_revision": "0" * 40,
        "receiver_bundle_sha256": "0" * 64,
        "receiver_bundle_bytes": 1,
        "signer_key_id": "ed25519-sha256:" + "0" * 64,
    }


def _validate_publish_plan(value: object) -> dict[str, Any]:
    """Validate the pure v2 plan contract without importing the publisher.

    The publisher intentionally checks its clean checkout *before* exposing
    its import surface.  Importing it from a local builder would turn an
    uncommitted-but-testable artifact worktree into a false provenance error.
    This validator retains the same fixed plan schema and reuses the sealed
    manifest structural validator, while the publisher independently parses
    this exact file again before any client can be constructed.
    """

    if not isinstance(value, Mapping) or set(value) != PUBLISH_PLAN_FIELDS:
        _fail("Emergency publish plan fields or schema are unsupported")
    plan = dict(value)
    if plan.get("schema") != PUBLISH_PLAN_SCHEMA:
        _fail("Emergency publish plan fields or schema are unsupported")
    patch_sha = plan.get("emergency_patch_sha")
    if not isinstance(patch_sha, str) or SHA_RE.fullmatch(patch_sha) is None:
        _fail("Emergency publish plan emergency_patch_sha is invalid")
    artifacts = plan.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(manifest.ARTIFACT_ORDER):
        _fail("Emergency publish plan must contain exactly four artifacts")
    unsigned_artifacts: list[dict[str, Any]] = []
    for index, (artifact, kind) in enumerate(zip(artifacts, manifest.ARTIFACT_ORDER, strict=True)):
        if not isinstance(artifact, Mapping) or set(artifact) != PUBLISH_PLAN_ARTIFACT_FIELDS:
            _fail("artifact descriptor fields are unsupported")
        descriptor = dict(artifact)
        if descriptor.get("kind") != kind:
            _fail("artifact descriptors must be complete, unique, and in fixed order")
        ciphertext_path = descriptor.get("ciphertext_path")
        if not isinstance(ciphertext_path, str) or not Path(ciphertext_path).is_absolute() or "\x00" in ciphertext_path:
            _fail("artifact ciphertext_path must be absolute")
        for field, maximum in (
            ("plaintext_bytes", manifest.MAX_ARTIFACT_BYTES),
            ("ciphertext_bytes", manifest.MAX_CIPHERTEXT_BYTES),
        ):
            number = descriptor.get(field)
            if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= maximum:
                _fail(f"artifact {field} must be a positive bounded integer")
        for field in ("plaintext_sha256", "ciphertext_sha256"):
            value_hash = descriptor.get(field)
            if not isinstance(value_hash, str) or manifest.SHA256_RE.fullmatch(value_hash) is None:
                _fail(f"artifact {field} must be a lowercase SHA-256")
        if (
            descriptor["ciphertext_bytes"] <= descriptor["plaintext_bytes"]
            or descriptor["ciphertext_sha256"] == descriptor["plaintext_sha256"]
        ):
            _fail("artifact ciphertext descriptor is not a plausible age ciphertext")
        unsigned_artifacts.append(
            {
                "kind": kind,
                "format": manifest.ARTIFACT_CONTRACTS[kind]["format"],
                "object_key": manifest.expected_object_key(
                    prefix=str(plan.get("prefix")), campaign_id=str(plan.get("campaign_id")), kind=kind
                ),
                "version_id": f"planned-version-{index + 1}",
                "plaintext_sha256": descriptor["plaintext_sha256"],
                "plaintext_bytes": descriptor["plaintext_bytes"],
                "ciphertext_sha256": descriptor["ciphertext_sha256"],
                "ciphertext_bytes": descriptor["ciphertext_bytes"],
                "encryption": {
                    "algorithm": "age-v1",
                    "recipient_key_id": plan.get("destination_age_recipient_key_id"),
                },
                "target_path": manifest.expected_target_path(campaign_id=str(plan.get("campaign_id")), kind=kind),
            }
        )
    try:
        normalized = manifest.validate_unsigned_manifest(
            {
                "schema": manifest.MANIFEST_SCHEMA,
                "campaign_id": plan.get("campaign_id"),
                "source_site": manifest.SOURCE_SITE,
                "destination_site": manifest.DESTINATION_SITE,
                "endpoint": manifest.APPROVED_ARVAN_ENDPOINT,
                "region": manifest.APPROVED_ARVAN_REGION,
                "bucket": plan.get("bucket"),
                "prefix": plan.get("prefix"),
                "created_at": plan.get("created_at"),
                "destination_age_recipient_key_id": plan.get("destination_age_recipient_key_id"),
                "bootstrap_provenance": _placeholder_bootstrap_provenance(),
                "artifacts": unsigned_artifacts,
            }
        )
    except manifest.EmergencyManifestError as exc:
        raise EmergencyArtifactSealError(
            "Emergency publish plan does not satisfy the sealed manifest contract"
        ) from exc
    return {
        "schema": PUBLISH_PLAN_SCHEMA,
        "campaign_id": str(normalized["campaign_id"]),
        "bucket": str(normalized["bucket"]),
        "prefix": str(normalized["prefix"]),
        "created_at": str(normalized["created_at"]),
        "emergency_patch_sha": patch_sha,
        "destination_age_recipient_key_id": str(normalized["destination_age_recipient_key_id"]),
        "artifacts": [dict(item) for item in artifacts],
    }


def build_publish_plan(
    *,
    campaign_id: str,
    bucket: str,
    prefix: str,
    created_at: str,
    emergency_patch_sha: str,
    recipient_key_id: str,
    artifacts: Mapping[str, tuple[FileDigest, FileDigest]],
) -> dict[str, Any]:
    """Return the strict, local-only v2 input for the guarded publisher."""

    if set(artifacts) != set(manifest.ARTIFACT_ORDER):
        _fail("sealed artifacts must cover exactly the fixed Emergency set")
    values: list[dict[str, Any]] = []
    for kind in manifest.ARTIFACT_ORDER:
        plaintext, ciphertext = artifacts[kind]
        values.append(
            {
                "kind": kind,
                "ciphertext_path": str(ciphertext.path),
                "plaintext_sha256": plaintext.sha256,
                "plaintext_bytes": plaintext.bytes,
                "ciphertext_sha256": ciphertext.sha256,
                "ciphertext_bytes": ciphertext.bytes,
            }
        )
    plan = {
        "schema": PUBLISH_PLAN_SCHEMA,
        "campaign_id": campaign_id,
        "bucket": bucket,
        "prefix": prefix,
        "created_at": created_at,
        "emergency_patch_sha": emergency_patch_sha,
        "destination_age_recipient_key_id": recipient_key_id,
        "artifacts": values,
    }
    return _validate_publish_plan(plan)


def seal_artifacts(
    *,
    campaign_id: str,
    bucket: str,
    prefix: str,
    created_at: str | None,
    recipient: str,
    plaintext_paths: Mapping[str, Path],
    output_directory: Path,
    age_binary: Path = DEFAULT_AGE_BINARY,
) -> dict[str, Any]:
    """Seal exactly four ready plaintexts and emit a create-only publish plan."""

    if set(plaintext_paths) != set(manifest.ARTIFACT_ORDER):
        _fail("plaintext inputs must cover exactly the fixed Emergency set")
    _secure_directory(output_directory, label="Emergency artifact output directory", empty=True)
    _validate_age_binary(age_binary)
    recipient_key_id = _recipient_key_id(recipient)
    timestamp = _canonical_created_at(created_at)
    emergency_patch_sha = _validate_plaintext_inputs(plaintext_paths)
    sealed: dict[str, tuple[FileDigest, FileDigest]] = {}
    for kind in manifest.ARTIFACT_ORDER:
        sealed[kind] = _seal_one(
            source_path=plaintext_paths[kind],
            output_path=output_directory / OUTPUT_FILENAMES[kind],
            recipient=recipient,
            age_binary=age_binary,
            label=kind,
        )
    plan = build_publish_plan(
        campaign_id=campaign_id,
        bucket=bucket,
        prefix=prefix,
        created_at=timestamp,
        emergency_patch_sha=emergency_patch_sha,
        recipient_key_id=recipient_key_id,
        artifacts=sealed,
    )
    plan_path = output_directory / "publish-plan.json"
    _write_plan_create_only(plan_path, manifest.canonical_json_bytes(plan))
    # Do not import the publisher here: its pre-import provenance guard must
    # run only from a clean, direct publisher invocation.  Parse the exact
    # output locally and let the publisher independently parse it again before
    # any external client is even constructed.
    try:
        source, opened = _open_stable_regular(
            plan_path,
            label="written Emergency publish plan",
            private=True,
        )
        with source:
            payload = source.read(manifest.MAX_MANIFEST_BYTES + 1)
            after = os.fstat(source.fileno())
            fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
            if len(payload) != opened.st_size or any(getattr(opened, field) != getattr(after, field) for field in fields):
                _fail("written Emergency publish plan changed while being read")
        written = _strict_json_object(
            payload,
            label="written Emergency publish plan",
            maximum_bytes=manifest.MAX_MANIFEST_BYTES,
        )
        if manifest.canonical_json_bytes(written) != payload:
            _fail("written Emergency publish plan is not canonical")
        _validate_publish_plan(written)
    except Exception as exc:
        if isinstance(exc, EmergencyArtifactSealError):
            raise
        raise EmergencyArtifactSealError("written Emergency publish plan failed local verification") from exc
    return {
        "status": "sealed-local-only",
        "campaign_id": campaign_id,
        "emergency_patch_sha": emergency_patch_sha,
        "destination_age_recipient_key_id": recipient_key_id,
        "publish_plan": str(plan_path),
        "artifacts": [
            {
                "kind": kind,
                "plaintext_sha256": sealed[kind][0].sha256,
                "plaintext_bytes": sealed[kind][0].bytes,
                "ciphertext_sha256": sealed[kind][1].sha256,
                "ciphertext_bytes": sealed[kind][1].bytes,
            }
            for kind in manifest.ARTIFACT_ORDER
        ],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--created-at")
    parser.add_argument("--destination-age-recipient", required=True)
    parser.add_argument("--image-bundle", type=Path, required=True)
    parser.add_argument("--package-tar", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--age-binary", type=Path, default=DEFAULT_AGE_BINARY)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        if not sys.flags.isolated:
            _fail("Emergency artifact sealer must be launched with python3 -I -B")
        args = parse_args(argv)
        result = seal_artifacts(
            campaign_id=args.campaign_id,
            bucket=args.bucket,
            prefix=args.prefix,
            created_at=args.created_at,
            recipient=args.destination_age_recipient,
            plaintext_paths={
                "image_bundle": args.image_bundle,
                "package_tar": args.package_tar,
                "snapshot": args.snapshot,
                "settings": args.settings,
            },
            output_directory=args.output_directory,
            age_binary=args.age_binary,
        )
    except EmergencyArtifactSealError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
