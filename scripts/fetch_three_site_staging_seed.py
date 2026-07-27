#!/usr/bin/env python3
"""Fetch and decrypt exact-version staging seed objects for one target role."""

from __future__ import annotations

import argparse
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
from typing import Any, Iterator

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import read_secure_bytes, sha256_secure_file
from scripts.publish_three_site_staging_seed_campaign import (
    AGE,
    MAX_ARTIFACT_BYTES,
    MAX_CIPHERTEXT_BYTES,
    SAFE_ENV,
    _credentials,
    _assert_directory_binding,
    _immutable_release_binding,
    _new_client,
    _no_sse,
    _open_private_directory,
    _require_owner_only_acl,
    _require_private_versioned_bucket,
)
from scripts.run_three_site_staging_source_backup import verify_tar_artifact
from scripts.verify_three_site_staging_migration_plan import TARGET_SEED_MAP, verify_migration_plan


TARGET_ROLES = tuple(TARGET_SEED_MAP)
ARTIFACT_FILENAME = {
    "postgres": "postgres.custom",
    "uploads": "uploads.tar.gz",
    "audit": "audit.tar.gz",
}
AGE_KEYGEN = "/usr/bin/age-keygen"
OUTPUT_LOCK_NAME = ".fetch.lock"
TEMPORARY_NAME_RE = re.compile(
    r"^\.(postgres\.custom|uploads\.tar\.gz|audit\.tar\.gz|target-seed\.json)"
    r"\.[0-9a-f]{32}\.(ciphertext|decrypting|writing)$"
)


class SeedFetchError(RuntimeError):
    pass


def _manifest_recipient_fingerprint(
    manifest: dict[str, Any],
    *,
    target_role: str,
) -> str:
    if manifest.get("schema") != "three-site-staging-seed-manifest-v2":
        raise SeedFetchError("only the sealed v2 target seed manifest is supported")
    fingerprints = manifest.get("recipient_fingerprints")
    if not isinstance(fingerprints, dict) or target_role not in fingerprints:
        raise SeedFetchError(
            "target role is not an authorized recipient in the seed manifest"
        )
    value = fingerprints[target_role]
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise SeedFetchError("target seed recipient fingerprint is malformed")
    return value


def _verify_exact_release(expected_release_sha: str) -> None:
    if (
        Path(__file__).resolve()
        != (REPO_ROOT / "scripts/fetch_three_site_staging_seed.py").resolve()
    ):
        raise SeedFetchError("target fetch is not executing from its fixed release root")
    try:
        _immutable_release_binding(REPO_ROOT.resolve(), expected_release_sha)
    except Exception as exc:
        raise SeedFetchError(
            "target fetch requires the exact fully clean immutable Git release"
        ) from exc


def _assert_output_binding(path: Path, descriptor: int) -> None:
    try:
        _assert_directory_binding(path, descriptor)
    except Exception as exc:
        raise SeedFetchError("target seed output directory path changed") from exc


def _open_output_directory(path: Path, *, require_empty: bool = True) -> int:
    if not path.is_absolute():
        raise SeedFetchError("target seed output path must be absolute")
    resolved = path.resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError:
        pass
    else:
        raise SeedFetchError("target seed output must be outside the Git release")
    descriptor = -1
    try:
        descriptor = _open_private_directory(path)
        _assert_output_binding(path, descriptor)
        if require_empty and os.listdir(descriptor):
            raise SeedFetchError(
                "target seed output must be an empty root-owned mode-0700 directory"
            )
        return descriptor
    except SeedFetchError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except Exception as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise SeedFetchError(
            "target seed output must be an empty root-owned mode-0700 directory"
        ) from exc


def _prepare_output(path: Path) -> None:
    descriptor = _open_output_directory(path)
    os.close(descriptor)


@contextmanager
def _exclusive_output_lock(path: Path, directory_descriptor: int) -> Iterator[None]:
    descriptor = -1
    try:
        descriptor = os.open(
            OUTPUT_LOCK_NAME,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_descriptor,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise SeedFetchError("target seed fetch lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SeedFetchError("target seed fetch is already running") from exc
        _assert_output_binding(path, directory_descriptor)
        yield
        _assert_output_binding(path, directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _purge_stale_temporary(
    directory_descriptor: int,
    name: str,
    *,
    canonical_name: str,
) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDWR
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_descriptor,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise SeedFetchError("stale target seed temporary is unsafe")
        if metadata.st_nlink == 1:
            os.ftruncate(descriptor, 0)
            os.fsync(descriptor)
        elif metadata.st_nlink == 2:
            canonical = os.stat(
                canonical_name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(canonical.st_mode)
                or canonical.st_uid != 0
                or canonical.st_nlink != 2
                or stat.S_IMODE(canonical.st_mode) != 0o600
                or canonical.st_dev != metadata.st_dev
                or canonical.st_ino != metadata.st_ino
            ):
                raise SeedFetchError(
                    "stale target seed temporary hard link is not canonical"
                )
        else:
            raise SeedFetchError("stale target seed temporary link count is unsafe")
    except OSError as exc:
        raise SeedFetchError("cannot reconcile stale target seed temporary") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    os.unlink(name, dir_fd=directory_descriptor)
    os.fsync(directory_descriptor)


def _reconcile_output_directory(
    path: Path,
    directory_descriptor: int,
    *,
    allowed_final_names: set[str],
) -> None:
    _assert_output_binding(path, directory_descriptor)
    for name in os.listdir(directory_descriptor):
        if name == OUTPUT_LOCK_NAME or name in allowed_final_names:
            continue
        match = TEMPORARY_NAME_RE.fullmatch(name)
        if match is None or match.group(1) not in allowed_final_names:
            raise SeedFetchError(
                f"target seed output contains an unowned or foreign path: {name}"
            )
        _purge_stale_temporary(
            directory_descriptor,
            name,
            canonical_name=match.group(1),
        )


def _assert_identity_binding(
    identity_path: Path,
    *,
    descriptor: int,
    metadata: os.stat_result,
    raw: bytes,
) -> None:
    try:
        after = os.fstat(descriptor)
        current = os.stat(identity_path, follow_symlinks=False)
        os.lseek(descriptor, 0, os.SEEK_SET)
        observed = os.read(descriptor, 4097)
    except OSError as exc:
        raise SeedFetchError("target age identity changed while pinned") from exc
    stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
    if (
        observed != raw
        or any(getattr(metadata, field) != getattr(after, field) for field in stable)
        or not stat.S_ISREG(current.st_mode)
        or current.st_dev != metadata.st_dev
        or current.st_ino != metadata.st_ino
    ):
        raise SeedFetchError("target age identity changed while pinned")


def _open_identity(
    identity_path: Path,
) -> tuple[int, os.stat_result, bytes]:
    descriptor = -1
    try:
        descriptor = os.open(
            identity_path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        raw = os.read(descriptor, 4097)
        if len(raw) > 4096:
            raise SeedFetchError("target age identity exceeds its safety bound")
        identity = raw.decode("ascii")
    except SeedFetchError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except (OSError, UnicodeError) as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise SeedFetchError("target age identity is unavailable") from exc
    secret_lines = [
        line.strip()
        for line in identity.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or len(secret_lines) != 1
        or re.fullmatch(r"AGE-SECRET-KEY-[A-Z0-9]+", secret_lines[0]) is None
    ):
        os.close(descriptor)
        raise SeedFetchError(
            "target identity must contain exactly one root-only age private key"
        )
    return descriptor, metadata, raw


def _derive_identity_recipient(
    identity_path: Path,
    *,
    descriptor: int,
    metadata: os.stat_result,
    raw: bytes,
    derive: Any = None,
) -> tuple[str, str]:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        if derive is None:
            if not Path(AGE_KEYGEN).is_file():
                raise SeedFetchError("age-keygen is unavailable at its fixed path")
            result = subprocess.run(
                [AGE_KEYGEN, "-y", f"/proc/self/fd/{descriptor}"],
                stdin=subprocess.DEVNULL,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
                env=SAFE_ENV,
                pass_fds=(descriptor,),
            )
            if result.returncode != 0:
                raise SeedFetchError("cannot derive the target age recipient")
            recipient = result.stdout.strip()
        else:
            recipient = str(derive(identity_path)).strip()
        _assert_identity_binding(
            identity_path,
            descriptor=descriptor,
            metadata=metadata,
            raw=raw,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SeedFetchError("cannot derive the target age recipient") from exc
    if re.fullmatch(r"age1[0-9a-z]+", recipient) is None:
        raise SeedFetchError("derived target age recipient is malformed")
    return recipient, hashlib.sha256((recipient + "\n").encode()).hexdigest()


def _identity_recipient(
    identity_path: Path,
    *,
    derive: Any = None,
) -> tuple[str, str]:
    descriptor, metadata, raw = _open_identity(identity_path)
    try:
        return _derive_identity_recipient(
            identity_path,
            descriptor=descriptor,
            metadata=metadata,
            raw=raw,
            derive=derive,
        )
    finally:
        os.close(descriptor)


def _download_exclusive(stream: Any, target: Path) -> tuple[str, int]:
    descriptor = os.open(
        target,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    digest = hashlib.sha256()
    size = 0
    try:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_CIPHERTEXT_BYTES:
                raise SeedFetchError("target seed ciphertext exceeds its safety bound")
            digest.update(chunk)
            view = memoryview(chunk)
            written = 0
            while written < len(view):
                count = os.write(descriptor, view[written:])
                if count <= 0:
                    raise SeedFetchError("target seed download made no progress")
                written += count
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    return digest.hexdigest(), size


def _decrypt_to_exclusive(
    *,
    identity_descriptor: int,
    encrypted: Path,
    temporary: Path,
) -> None:
    if not Path(AGE).is_file():
        raise SeedFetchError("age executable is unavailable at its fixed path")
    old_umask = os.umask(0o077)
    descriptor = -1
    encrypted_descriptor = -1
    try:
        encrypted_descriptor = os.open(
            encrypted,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        identity_metadata = os.fstat(identity_descriptor)
        encrypted_metadata = os.fstat(encrypted_descriptor)
        if (
            not stat.S_ISREG(identity_metadata.st_mode)
            or identity_metadata.st_uid != 0
            or identity_metadata.st_nlink != 1
            or stat.S_IMODE(identity_metadata.st_mode) != 0o600
            or not stat.S_ISREG(encrypted_metadata.st_mode)
            or encrypted_metadata.st_uid != 0
            or encrypted_metadata.st_nlink != 1
            or stat.S_IMODE(encrypted_metadata.st_mode) != 0o600
        ):
            raise SeedFetchError("age decryption inputs are unsafe")
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.lseek(identity_descriptor, 0, os.SEEK_SET)
        result = subprocess.run(
            [
                AGE,
                "--decrypt",
                "--identity",
                f"/proc/self/fd/{identity_descriptor}",
                f"/proc/self/fd/{encrypted_descriptor}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=descriptor,
            stderr=subprocess.PIPE,
            check=False,
            timeout=1800,
            env=SAFE_ENV,
            pass_fds=(identity_descriptor, encrypted_descriptor),
        )
        if result.returncode != 0:
            raise SeedFetchError("age decryption failed closed")
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SeedFetchError("age decryption failed closed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if encrypted_descriptor >= 0:
            os.close(encrypted_descriptor)
        os.umask(old_umask)
    metadata = temporary.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise SeedFetchError("decrypted temporary artifact is unsafe")


def _publish_exclusive(temporary: Path, output: Path) -> None:
    try:
        os.link(temporary, output, follow_symlinks=False)
    except FileExistsError as exc:
        raise SeedFetchError("target seed output raced with another writer") from exc
    directory = os.open(output.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _write_exclusive(path: Path, payload: bytes) -> None:
    temporary = path.parent / f".{path.name}.{secrets.token_hex(16)}.writing"
    descriptor = os.open(
        temporary,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        view = memoryview(payload)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise SeedFetchError("target seed evidence write made no progress")
            written += count
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)
    try:
        os.link(temporary, path, follow_symlinks=False)
        directory = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileExistsError as exc:
        raise SeedFetchError("target seed evidence raced with another writer") from exc
    finally:
        temporary.unlink(missing_ok=True)


def confirmation_phrase(campaign_id: str, target_role: str, plan_hash: str) -> str:
    return f"fetch-seed:{campaign_id}:{target_role}:{plan_hash}"


def _object_evidence(item: dict[str, Any], output: Path) -> dict[str, Any]:
    digest, size = sha256_secure_file(
        output,
        label=f"{item['kind']} target seed",
        owner_uid=0,
        max_size=MAX_ARTIFACT_BYTES,
    )
    if digest != item["plaintext_sha256"] or size != item["plaintext_bytes"]:
        raise SeedFetchError("existing target seed differs from signed manifest")
    if item["kind"] in {"uploads", "audit"}:
        verify_tar_artifact(output)
    return {
        "kind": item["kind"],
        "object_key": item["object_key"],
        "version_id": item["version_id"],
        "ciphertext_sha256": item["ciphertext_sha256"],
        "ciphertext_bytes": item["ciphertext_bytes"],
        "plaintext_sha256": digest,
        "plaintext_bytes": size,
        "publication_intent": item["publication_intent"],
        "path": str(output),
    }


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SeedFetchError("target seed evidence contains a duplicate JSON key")
        value[key] = item
    return value


def _load_secure_json(path: Path, *, label: str) -> dict[str, Any]:
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
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size <= 0
            or before.st_size > 4 * 1024 * 1024
        ):
            raise SeedFetchError(f"{label} must be one root-owned mode-0600 file")
        chunks: list[bytes] = []
        remaining = 4 * 1024 * 1024 + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
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
            len(raw) > 4 * 1024 * 1024
            or any(getattr(before, field) != getattr(after, field) for field in stable)
        ):
            raise SeedFetchError(f"{label} changed while it was read")
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except SeedFetchError:
        raise
    except Exception as exc:
        raise SeedFetchError(f"{label} is unavailable or invalid") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(value, dict):
        raise SeedFetchError(f"{label} must contain one JSON object")
    return value


def _existing_evidence(
    path: Path,
    *,
    verified_plan: dict[str, Any],
    target_role: str,
    source_role: str | None,
    mode: str,
    manifest_sha256: str | None,
    objects: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        raw = read_secure_bytes(
            path,
            label="target seed evidence",
            owner_uid=0,
            max_size=2 * 1024 * 1024,
        )
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except SeedFetchError:
        raise
    except Exception as exc:
        raise SeedFetchError("existing target seed evidence is unsafe or invalid") from exc
    if not isinstance(value, dict):
        raise SeedFetchError("existing target seed evidence must be one JSON object")
    fields = {
        "schema",
        "campaign_id",
        "release_sha",
        "target_role",
        "source_role",
        "seed_manifest_sha256",
        "mode",
        "verified_at",
        "objects",
    }
    try:
        verified_at = datetime.fromisoformat(
            str(value["verified_at"]).replace("Z", "+00:00")
        )
    except (KeyError, ValueError) as exc:
        raise SeedFetchError("existing target seed evidence timestamp is invalid") from exc
    if (
        set(value) != fields
        or value["schema"] != "three-site-staging-target-seed-v2"
        or value["campaign_id"] != verified_plan["campaign_id"]
        or value["release_sha"] != verified_plan["release_sha"]
        or value["target_role"] != target_role
        or value["source_role"] != source_role
        or value["seed_manifest_sha256"] != manifest_sha256
        or value["mode"] != mode
        or verified_at.tzinfo is None
        or value["objects"] != objects
        or raw != (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    ):
        raise SeedFetchError(
            "existing target seed evidence differs from the exact signed campaign"
        )
    return value


def _result_from_evidence(
    evidence: dict[str, Any],
    *,
    evidence_path: Path,
) -> dict[str, Any]:
    return {
        "status": "target-seed-verified",
        "campaign_id": evidence["campaign_id"],
        "target_role": evidence["target_role"],
        "source_role": evidence["source_role"],
        "evidence": str(evidence_path),
        "evidence_sha256": hashlib.sha256(
            json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "object_count": len(evidence["objects"]),
    }


def _fetch_one(
    client,
    *,
    bucket: str,
    item: dict[str, Any],
    identity_path: Path | None,
    identity_descriptor: int | None = None,
    identity_metadata: os.stat_result | None = None,
    identity_raw: bytes | None = None,
    output: Path,
    bucket_owner_id: str,
    decrypt: Any = _decrypt_to_exclusive,
) -> dict[str, Any]:
    nonce = secrets.token_hex(16)
    encrypted = output.parent / f".{output.name}.{nonce}.ciphertext"
    temporary = output.parent / f".{output.name}.{nonce}.decrypting"
    response = client.get_object(
        Bucket=bucket,
        Key=item["object_key"],
        VersionId=item["version_id"],
    )
    expected_metadata = {
        "plaintext-sha256": item["plaintext_sha256"],
        "ciphertext-sha256": item["ciphertext_sha256"],
        "artifact-kind": item["kind"],
    }
    if "publication_intent" in item:
        expected_metadata["publication-intent"] = item["publication_intent"]
    if (
        str(response.get("VersionId") or "") != item["version_id"]
        or item["version_id"] == "null"
        or int(response.get("ContentLength") or -1) != item["ciphertext_bytes"]
        or response.get("Metadata") != expected_metadata
        or not _no_sse(response)
    ):
        close = getattr(response.get("Body"), "close", None)
        if callable(close):
            close()
        raise SeedFetchError("target seed provider identity/metadata differs from manifest")
    try:
        _require_owner_only_acl(
            client.get_object_acl(
                Bucket=bucket,
                Key=item["object_key"],
                VersionId=item["version_id"],
            ),
            label="target seed object",
            expected_owner_id=bucket_owner_id,
        )
    except Exception:
        close = getattr(response.get("Body"), "close", None)
        if callable(close):
            close()
        raise
    ciphertext_hash, ciphertext_size = _download_exclusive(
        response["Body"],
        encrypted,
    )
    try:
        if (
            ciphertext_hash != item["ciphertext_sha256"]
            or ciphertext_size != item["ciphertext_bytes"]
        ):
            raise SeedFetchError("target seed ciphertext differs from signed manifest")
        if identity_descriptor is None:
            if decrypt is _decrypt_to_exclusive:
                raise SeedFetchError("pinned target age identity is unavailable")
        elif (
            identity_path is None
            or identity_metadata is None
            or identity_raw is None
        ):
            raise SeedFetchError("pinned target age identity guard is incomplete")
        else:
            _assert_identity_binding(
                identity_path,
                descriptor=identity_descriptor,
                metadata=identity_metadata,
                raw=identity_raw,
            )
        decrypt(
            identity_descriptor=identity_descriptor,
            encrypted=encrypted,
            temporary=temporary,
        )
        if identity_descriptor is not None:
            _assert_identity_binding(
                identity_path,
                descriptor=identity_descriptor,
                metadata=identity_metadata,
                raw=identity_raw,
            )
        plaintext_hash, plaintext_size = sha256_secure_file(
            temporary,
            label=f"{item['kind']} target seed",
            owner_uid=0,
            max_size=MAX_ARTIFACT_BYTES,
        )
        if (
            plaintext_hash != item["plaintext_sha256"]
            or plaintext_size != item["plaintext_bytes"]
        ):
            raise SeedFetchError("decrypted target seed differs from signed manifest")
        if item["kind"] in {"uploads", "audit"}:
            verify_tar_artifact(temporary)
        _publish_exclusive(temporary, output)
        return {
            "kind": item["kind"],
            "object_key": item["object_key"],
            "version_id": item["version_id"],
            "ciphertext_sha256": ciphertext_hash,
            "ciphertext_bytes": ciphertext_size,
            "plaintext_sha256": plaintext_hash,
            "plaintext_bytes": plaintext_size,
            "publication_intent": item["publication_intent"],
            "path": str(output),
        }
    finally:
        encrypted.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)


def build_plan(
    *, campaign_id: str, target_role: str, plan_hash: str, source_role: str | None
) -> dict[str, Any]:
    return {
        "status": "planned",
        "campaign_id": campaign_id,
        "target_role": target_role,
        "source_role": source_role,
        "object_count": 0 if source_role is None else 3,
        "required_confirmation": confirmation_phrase(campaign_id, target_role, plan_hash),
    }


def _execute_with_prepared_output(
    args: argparse.Namespace,
    *,
    verified_plan: dict[str, Any],
    inventory: dict[str, Any],
    seed_manifests: dict[str, dict[str, Any]],
    output_descriptor: int,
    derive_recipient: Any = None,
    client_factory: Any = _new_client,
    decrypt: Any = _decrypt_to_exclusive,
) -> dict[str, Any]:
    _verify_exact_release(verified_plan["release_sha"])
    expected_confirmation = confirmation_phrase(
        verified_plan["campaign_id"], args.target_role, verified_plan["plan_sha256"]
    )
    if args.confirm != expected_confirmation:
        raise SeedFetchError("target seed fetch confirmation mismatch")
    source_role, mode = TARGET_SEED_MAP[args.target_role]
    _assert_output_binding(args.output_dir, output_descriptor)
    evidence_path = args.output_dir / "target-seed.json"
    allowed_final_names = {"target-seed.json"}
    if source_role is not None:
        allowed_final_names.update(ARTIFACT_FILENAME.values())
    _reconcile_output_directory(
        args.output_dir,
        output_descriptor,
        allowed_final_names=allowed_final_names,
    )
    manifest_sha256: str | None = None
    if source_role is None:
        objects: list[dict[str, Any]] = []
        if evidence_path.exists() or evidence_path.is_symlink():
            evidence = _existing_evidence(
                evidence_path,
                verified_plan=verified_plan,
                target_role=args.target_role,
                source_role=None,
                mode=mode,
                manifest_sha256=None,
                objects=objects,
            )
            return _result_from_evidence(evidence, evidence_path=evidence_path)
        evidence = {
            "schema": "three-site-staging-target-seed-v2",
            "campaign_id": verified_plan["campaign_id"],
            "release_sha": verified_plan["release_sha"],
            "target_role": args.target_role,
            "source_role": None,
            "seed_manifest_sha256": None,
            "mode": mode,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "objects": objects,
        }
    else:
        manifest = seed_manifests[source_role]
        manifest_sha256 = hashlib.sha256(
            json.dumps(
                manifest,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        identity_descriptor, identity_metadata, identity_raw = _open_identity(
            args.identity
        )
        try:
            _recipient, recipient_fingerprint = _derive_identity_recipient(
                args.identity,
                descriptor=identity_descriptor,
                metadata=identity_metadata,
                raw=identity_raw,
                derive=derive_recipient,
            )
            expected_fingerprint = _manifest_recipient_fingerprint(
                manifest,
                target_role=args.target_role,
            )
            if recipient_fingerprint != expected_fingerprint:
                raise SeedFetchError(
                    "target age recipient differs from its role-bound manifest recipient"
                )
            objects = []
            missing: list[tuple[dict[str, Any], Path]] = []
            for item in sorted(
                manifest["objects"], key=lambda value: value["kind"]
            ):
                output = args.output_dir / ARTIFACT_FILENAME[item["kind"]]
                if output.exists() or output.is_symlink():
                    objects.append(_object_evidence(item, output))
                else:
                    missing.append((item, output))
            if evidence_path.exists() or evidence_path.is_symlink():
                if missing:
                    raise SeedFetchError(
                        "target seed evidence exists with an incomplete artifact set"
                    )
                evidence = _existing_evidence(
                    evidence_path,
                    verified_plan=verified_plan,
                    target_role=args.target_role,
                    source_role=source_role,
                    mode=mode,
                    manifest_sha256=manifest_sha256,
                    objects=objects,
                )
                return _result_from_evidence(evidence, evidence_path=evidence_path)
            if missing:
                access_key, secret_key = _credentials(
                    args.credentials,
                    expected_credential_id=inventory["object_storage"]["credential_id"],
                )
                client = client_factory(access_key, secret_key)
                bucket_owner_id = _require_private_versioned_bucket(
                    client,
                    bucket=manifest["bucket"],
                )
                if manifest.get("bucket_owner_id") != bucket_owner_id:
                    raise SeedFetchError(
                        "target seed bucket owner differs from the signed manifest"
                    )
                for item, output in missing:
                    _assert_output_binding(args.output_dir, output_descriptor)
                    _assert_identity_binding(
                        args.identity,
                        descriptor=identity_descriptor,
                        metadata=identity_metadata,
                        raw=identity_raw,
                    )
                    objects.append(
                        _fetch_one(
                            client,
                            bucket=manifest["bucket"],
                            item=item,
                            identity_path=args.identity,
                            identity_descriptor=identity_descriptor,
                            identity_metadata=identity_metadata,
                            identity_raw=identity_raw,
                            output=output,
                            bucket_owner_id=bucket_owner_id,
                            decrypt=decrypt,
                        )
                    )
                objects.sort(key=lambda value: value["kind"])
        finally:
            os.close(identity_descriptor)
        evidence = {
            "schema": "three-site-staging-target-seed-v2",
            "campaign_id": verified_plan["campaign_id"],
            "release_sha": verified_plan["release_sha"],
            "target_role": args.target_role,
            "source_role": source_role,
            "seed_manifest_sha256": manifest_sha256,
            "mode": mode,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "objects": objects,
        }
    encoded = (json.dumps(evidence, sort_keys=True, indent=2) + "\n").encode()
    _assert_output_binding(args.output_dir, output_descriptor)
    _write_exclusive(evidence_path, encoded)
    return _result_from_evidence(evidence, evidence_path=evidence_path)


def execute(
    args: argparse.Namespace,
    *,
    verified_plan: dict[str, Any],
    inventory: dict[str, Any],
    seed_manifests: dict[str, dict[str, Any]],
    derive_recipient: Any = None,
    client_factory: Any = _new_client,
    decrypt: Any = _decrypt_to_exclusive,
) -> dict[str, Any]:
    _verify_exact_release(verified_plan["release_sha"])
    expected_confirmation = confirmation_phrase(
        verified_plan["campaign_id"], args.target_role, verified_plan["plan_sha256"]
    )
    if args.confirm != expected_confirmation:
        raise SeedFetchError("target seed fetch confirmation mismatch")
    output_descriptor = _open_output_directory(
        args.output_dir,
        require_empty=False,
    )
    try:
        with _exclusive_output_lock(args.output_dir, output_descriptor):
            result = _execute_with_prepared_output(
                args,
                verified_plan=verified_plan,
                inventory=inventory,
                seed_manifests=seed_manifests,
                output_descriptor=output_descriptor,
                derive_recipient=derive_recipient,
                client_factory=client_factory,
                decrypt=decrypt,
            )
        _assert_output_binding(args.output_dir, output_descriptor)
        return result
    finally:
        os.close(output_descriptor)


def _mapping(values: list[str], *, roles: tuple[str, ...], label: str):  # noqa: ANN001
    result = {}
    for value in values:
        role, separator, raw_path = value.partition("=")
        if not separator or role not in roles or role in result or not raw_path:
            raise SeedFetchError(f"{label} must use one unique role=/path mapping")
        result[role] = _load_secure_json(
            Path(raw_path),
            label=f"{label} {role}",
        )
    if set(result) != set(roles):
        raise SeedFetchError(f"{label} role set is incomplete")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-role", choices=TARGET_ROLES, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-approval", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--freeze-evidence", action="append", type=Path, required=True)
    parser.add_argument("--image-inventory", action="append", required=True)
    parser.add_argument("--backup-manifest", action="append", required=True)
    parser.add_argument("--seed-manifest", action="append", required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        inventory = _load_secure_json(args.inventory, label="staging inventory")
        backups = _mapping(
            args.backup_manifest, roles=("bot_fi", "webapp_fi"), label="--backup-manifest"
        )
        seeds = _mapping(
            args.seed_manifest, roles=("bot_fi", "webapp_fi"), label="--seed-manifest"
        )
        verified = verify_migration_plan(
            _load_secure_json(args.plan, label="migration plan"),
            approval=_load_secure_json(
                args.plan_approval,
                label="migration plan approval",
            ),
            inventory=inventory,
            inventory_approval=_load_secure_json(
                args.inventory_approval,
                label="inventory approval",
            ),
            approval_policy=_load_secure_json(
                args.approval_policy,
                label="approval policy",
            ),
            freeze_evidence=[
                _load_secure_json(path, label="freeze evidence")
                for path in args.freeze_evidence
            ],
            image_inventories=_mapping(
                args.image_inventory,
                roles=("bot_fi", "webapp_fi", "webapp_ir", "witness"),
                label="--image-inventory",
            ),
            backup_manifests=backups,
            seed_manifests=seeds,
        )
        _verify_exact_release(verified["release_sha"])
        source_role, _mode = TARGET_SEED_MAP[args.target_role]
        result = build_plan(
            campaign_id=verified["campaign_id"],
            target_role=args.target_role,
            plan_hash=verified["plan_sha256"],
            source_role=source_role,
        )
        if args.apply:
            result = execute(
                args,
                verified_plan=verified,
                inventory=inventory,
                seed_manifests=seeds,
            )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
