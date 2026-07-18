#!/usr/bin/env python3
"""Safely stage, revoke, roll back, and finish one Witness HMAC rotation."""

from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time
from urllib.request import urlopen


RUNTIME_ENV = Path("/etc/trading-bot-witness/runtime.env")
CLIENT_DIR = Path("/root/writer-witness-client-material")
STATE_ROOT = Path("/var/lib/trading-bot-witness/hmac-rotation")
SITE_SETTINGS = {
    "webapp_fi": ("FI", "webapp-fi.env"),
    "webapp_ir": ("IR", "webapp-ir.env"),
}


class RotationError(RuntimeError):
    """A fail-closed rotation precondition or operation failed."""


def _validate_owned_directory(path: Path, *, private: bool = False) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RotationError(f"required credential directory is missing: {path}") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o022
        or (private and metadata.st_mode & 0o077)
    ):
        raise RotationError(f"credential directory is not safely owned: {path}")


def _ensure_private_directory(path: Path) -> None:
    """Create one direct private directory without chmod-following a symlink."""

    _validate_owned_directory(path.parent)
    created = False
    try:
        path.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        pass
    except OSError as exc:
        raise RotationError(f"cannot create private rotation directory: {path}") from exc
    _validate_owned_directory(path, private=True)
    if created:
        _fsync_directory(path)
        _fsync_directory(path.parent)


def _read_owner_file_bytes(path: Path, *, max_bytes: int = 1_048_576) -> bytes:
    _validate_owned_directory(path.parent)
    try:
        before = path.lstat()
    except OSError as exc:
        raise RotationError(f"required owner-only file is missing: {path}") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) != 0o600
        or before.st_nlink != 1
        or before.st_size < 1
        or before.st_size > max_bytes
    ):
        raise RotationError(f"file must be one owner-only regular inode: {path}")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RotationError(f"cannot securely open owner-only file: {path}") from exc
    stable_fields = (
        "st_dev",
        "st_ino",
        "st_uid",
        "st_gid",
        "st_mode",
        "st_nlink",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    try:
        opened = os.fstat(descriptor)
        if any(getattr(opened, field) != getattr(before, field) for field in stable_fields):
            raise RotationError(f"owner-only file changed before immutable read: {path}")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                raise RotationError(f"owner-only file changed during immutable read: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if any(getattr(after, field) != getattr(opened, field) for field in stable_fields):
            raise RotationError(f"owner-only file changed during immutable read: {path}")
    finally:
        os.close(descriptor)
    try:
        final_path = path.lstat()
    except OSError as exc:
        raise RotationError(f"owner-only file path changed after immutable read: {path}") from exc
    if final_path.st_dev != before.st_dev or final_path.st_ino != before.st_ino:
        raise RotationError(f"owner-only file path changed during immutable read: {path}")
    return b"".join(chunks)


def _read_env(path: Path) -> tuple[list[str], dict[str, str]]:
    try:
        lines = _read_owner_file_bytes(path).decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise RotationError(f"environment file is not UTF-8: {path}") from exc
    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key or key in values:
            raise RotationError(f"invalid or duplicate setting in {path}")
        values[key] = value.strip()
    return lines, values


def _atomic_update_env(
    path: Path,
    *,
    changes: dict[str, str],
    removals: set[str] | None = None,
    operation_token: str | None = None,
) -> None:
    lines, _ = _read_env(path)
    pending = dict(changes)
    remove = removals or set()
    rendered: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            rendered.append(raw_line)
            continue
        key = line.partition("=")[0].strip()
        if key in remove:
            continue
        if key in pending:
            rendered.append(f"{key}={pending.pop(key)}")
        else:
            rendered.append(raw_line)
    if pending:
        if rendered and rendered[-1]:
            rendered.append("")
        rendered.extend(f"{key}={value}" for key, value in pending.items())
    payload = ("\n".join(rendered).rstrip() + "\n").encode("utf-8")
    token_part = f"{operation_token}-" if operation_token is not None else ""
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.rotate-{token_part}",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_secret(
    source: Path,
    destination: Path,
    *,
    operation_token: str | None = None,
) -> None:
    _validate_owned_directory(destination.parent, private=True)
    payload = _read_owner_file_bytes(source)
    token_part = f"{operation_token}-" if operation_token is not None else ""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.copy-{token_part}",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as target_handle:
            descriptor = -1
            target_handle.write(payload)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        os.replace(temporary, destination)
        directory_fd = os.open(destination.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _env_line_key(raw_line: str) -> str | None:
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    return line.partition("=")[0].strip()


def _write_private_bytes(
    path: Path,
    payload: bytes,
    *,
    operation_token: str | None = None,
) -> None:
    _validate_owned_directory(path.parent)
    token_part = f"{operation_token}-" if operation_token is not None else ""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.write-{token_part}",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _snapshot_runtime_scope(
    runtime_path: Path,
    destination: Path,
    owned_keys: set[str],
    required_keys: set[str],
) -> None:
    """Persist only one site's credential slots, never the shared runtime file."""

    lines, values = _read_env(runtime_path)
    missing = required_keys.difference(values)
    if missing:
        raise RotationError("runtime credential scope is incomplete")
    selected = [
        raw_line
        for raw_line in lines
        if _env_line_key(raw_line) in owned_keys
    ]
    selected_keys = {
        key
        for raw_line in selected
        if (key := _env_line_key(raw_line)) is not None
    }
    if selected_keys != owned_keys.intersection(values):
        raise RotationError("runtime credential scope cannot be snapshotted safely")
    _write_private_bytes(
        destination,
        ("\n".join(selected) + "\n").encode("utf-8"),
    )


def _restore_runtime_scope(
    runtime_path: Path,
    snapshot_path: Path,
    owned_keys: set[str],
    required_keys: set[str],
    *,
    operation_token: str | None = None,
) -> None:
    """Atomically restore one site's exact lines while preserving the other site."""

    snapshot_lines, snapshot_values = _read_env(snapshot_path)
    if set(snapshot_values).difference(owned_keys):
        raise RotationError("runtime credential snapshot escapes its site scope")
    if required_keys.difference(snapshot_values):
        raise RotationError("runtime credential snapshot is incomplete")

    snapshot_raw: dict[str, str] = {}
    snapshot_order: list[str] = []
    for raw_line in snapshot_lines:
        key = _env_line_key(raw_line)
        if key is None:
            continue
        snapshot_raw[key] = raw_line
        snapshot_order.append(key)

    current_lines, _ = _read_env(runtime_path)
    rendered: list[str] = []
    restored: set[str] = set()
    for raw_line in current_lines:
        key = _env_line_key(raw_line)
        if key not in owned_keys:
            rendered.append(raw_line)
        elif key in snapshot_raw:
            rendered.append(snapshot_raw[key])
            restored.add(key)
        # Site-owned keys absent from the snapshot are deliberately removed.

    missing_from_current = [key for key in snapshot_order if key not in restored]
    if missing_from_current:
        if rendered and rendered[-1]:
            rendered.append("")
        rendered.extend(snapshot_raw[key] for key in missing_from_current)

    payload = ("\n".join(rendered).rstrip() + "\n").encode("utf-8")
    _write_private_bytes(
        runtime_path,
        payload,
        operation_token=operation_token,
    )


def _write_metadata(path: Path, metadata: dict[str, object]) -> None:
    _validate_owned_directory(path.parent, private=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(json.dumps(metadata, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _rename_directory_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish prepared state without replacing any existing state."""

    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except AttributeError as exc:
        raise RotationError("renameat2 is required for no-replace state publication") from exc
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,  # AT_FDCWD
        os.fsencode(source),
        -100,
        os.fsencode(destination),
        1,  # RENAME_NOREPLACE
    )
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
            raise RotationError("unfinished rotation state already exists")
        raise OSError(error_number, os.strerror(error_number), str(destination))


def _require_operation_token(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise RotationError("rotation operation token is missing or invalid")
    return value


def _private_owned_regular_file(path: Path) -> bool:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        return False
    return (
        stat.S_ISREG(file_stat.st_mode)
        and file_stat.st_uid == os.geteuid()
        and not file_stat.st_mode & 0o077
        and file_stat.st_nlink == 1
    )


def _cleanup_and_attest_operation_temps(
    *,
    operation_token: str,
    runtime_path: Path,
    client_path: Path,
) -> int:
    """Under the caller's global lock, remove only token-owned primitive temps."""

    operation_token = _require_operation_token(operation_token)
    specs = (
        (runtime_path, "rotate"),
        (runtime_path, "write"),
        (client_path, "rotate"),
        (client_path, "copy"),
    )
    removed = 0
    for target, primitive in specs:
        prefix = f".{target.name}.{primitive}-{operation_token}-"
        tombstone = target.parent / (
            f".{target.name}.{primitive}-{operation_token}.owned-tombstone"
        )
        if tombstone.exists() or tombstone.is_symlink():
            if not _private_owned_regular_file(tombstone):
                raise RotationError("operation temp tombstone is not safely owned")
            tombstone.unlink()
            _fsync_directory(target.parent)
            removed += 1

        try:
            candidates = tuple(target.parent.iterdir())
        except FileNotFoundError:
            candidates = ()
        for candidate in candidates:
            if not candidate.name.startswith(prefix):
                continue
            suffix = candidate.name.removeprefix(prefix)
            if not re.fullmatch(r"[A-Za-z0-9_]{6,64}", suffix):
                raise RotationError("operation temp name is outside the strict allowlist")
            if not _private_owned_regular_file(candidate):
                raise RotationError("operation temp is not a private owned regular file")
            _rename_directory_noreplace(candidate, tombstone)
            _fsync_directory(target.parent)
            tombstone.unlink()
            _fsync_directory(target.parent)
            removed += 1

        leftovers = tuple(
            candidate
            for candidate in target.parent.iterdir()
            if candidate.name.startswith(prefix)
        )
        if leftovers or tombstone.exists() or tombstone.is_symlink():
            raise RotationError("operation temp cleanup post-attestation failed")
    return removed


def _state_child_name_is_allowed(name: str) -> bool:
    if name in {
        "metadata.json",
        "runtime-site.env.before",
        "runtime-site.env.overlap",
        "client.env.before",
    }:
        return True
    for prefix in (
        ".metadata.json.",
        ".runtime-site.env.before.write-",
        ".runtime-site.env.overlap.write-",
        ".client.env.before.copy-",
    ):
        if name.startswith(prefix):
            return bool(
                re.fullmatch(
                    r"[A-Za-z0-9_]{6,128}",
                    name.removeprefix(prefix),
                )
            )
    return False


def _owned_staging_directory(
    path: Path,
    *,
    site: str,
    campaign_tag: str | None,
) -> bool:
    """Return true only for a strictly bounded staging directory we can remove."""

    campaign_label = campaign_tag if campaign_tag is not None else "manual"
    name_match = re.fullmatch(
        rf"\.{re.escape(site)}\.prepare-{re.escape(campaign_label)}-"
        r"(?P<token>[0-9a-f]{32})-[A-Za-z0-9_]{6,64}",
        path.name,
    )
    if name_match is None:
        return False
    operation_token = name_match.group("token")
    try:
        directory_stat = path.lstat()
    except FileNotFoundError:
        return False
    if (
        not stat.S_ISDIR(directory_stat.st_mode)
        or directory_stat.st_uid != os.geteuid()
        or directory_stat.st_mode & 0o077
    ):
        return False
    metadata_path = path / "metadata.json"
    try:
        metadata_stat = metadata_path.lstat()
    except FileNotFoundError:
        # The first metadata write contains no secret. A hard kill inside that
        # primitive may leave only its private temp. The campaign and operation
        # ownership are encoded in the unguessable staging name, and any unknown
        # child makes the directory foreign and therefore untouchable.
        try:
            bootstrap_children = tuple(path.iterdir())
        except OSError:
            return False
        if len(bootstrap_children) > 1:
            return False
        return all(
            child.name.startswith(".metadata.json.")
            and re.fullmatch(
                r"[A-Za-z0-9_]{6,64}",
                child.name.removeprefix(".metadata.json."),
            )
            and _private_owned_regular_file(child)
            for child in bootstrap_children
        )
    if (
        not stat.S_ISREG(metadata_stat.st_mode)
        or metadata_stat.st_uid != os.geteuid()
        or metadata_stat.st_mode & 0o077
    ):
        return False
    try:
        metadata = _load_metadata(metadata_path)
    except RotationError:
        return False
    if (
        metadata.get("site") != site
        or metadata.get("campaign_tag") != campaign_tag
        or metadata.get("phase") not in {"staging", "preparing", "deleting"}
        or metadata.get("staging_name") != path.name
        or metadata.get("operation_token") != operation_token
    ):
        return False

    try:
        children = list(path.iterdir())
    except OSError:
        return False
    for child in children:
        if not _state_child_name_is_allowed(child.name):
            return False
        if not _private_owned_regular_file(child):
            return False
    return True


def _cleanup_owned_staging_directories(
    state_root: Path,
    *,
    site: str,
    campaign_tag: str | None,
) -> int:
    """Remove only staging state carrying an exact same-campaign ownership claim."""

    removed = 0
    prefix = f".{site}.prepare-"
    try:
        candidates = tuple(state_root.iterdir())
    except FileNotFoundError:
        return 0
    for candidate in candidates:
        if not candidate.name.startswith(prefix):
            continue
        if not _owned_staging_directory(
            candidate,
            site=site,
            campaign_tag=campaign_tag,
        ):
            continue
        _delete_owned_state_directory(
            candidate,
            state_root=state_root,
            site=site,
            campaign_tag=campaign_tag,
        )
        removed += 1
    if removed:
        _fsync_directory(state_root)
    return removed


def _load_metadata(path: Path) -> dict[str, object]:
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RotationError("rotation metadata is missing or invalid") from exc
    if not isinstance(metadata, dict):
        raise RotationError("rotation metadata is invalid")
    return metadata


def _state_children_are_deletable(path: Path) -> bool:
    try:
        children = tuple(path.iterdir())
    except OSError:
        return False
    for child in children:
        if not _state_child_name_is_allowed(child.name):
            return False
        if not _private_owned_regular_file(child):
            return False
    return True


def _deletion_paths(state_root: Path, site: str, operation_token: str) -> tuple[Path, Path]:
    operation_token = _require_operation_token(operation_token)
    base = f".rotation-delete-{site}-{operation_token}"
    return state_root / base, state_root / f"{base}.claim"


def _load_owned_deletion_metadata(
    path: Path,
    *,
    site: str,
    campaign_tag: str | None,
    operation_token: str,
    tombstone_name: str,
) -> dict[str, object]:
    if not _private_owned_regular_file(path):
        raise RotationError("deletion ownership claim is not a private regular file")
    metadata = _load_metadata(path)
    if (
        metadata.get("site") != site
        or metadata.get("campaign_tag") != campaign_tag
        or metadata.get("operation_token") != operation_token
        or metadata.get("phase") != "deleting"
        or metadata.get("tombstone_name") != tombstone_name
    ):
        raise RotationError("deletion ownership claim does not match this campaign")
    return metadata


def _resume_owned_deletion(
    *,
    state_root: Path,
    site: str,
    campaign_tag: str | None,
    operation_token: str,
) -> bool:
    tombstone, claim = _deletion_paths(state_root, site, operation_token)
    tombstone_present = tombstone.exists() or tombstone.is_symlink()
    claim_present = claim.exists() or claim.is_symlink()
    if not tombstone_present and not claim_present:
        return False

    if claim_present:
        _load_owned_deletion_metadata(
            claim,
            site=site,
            campaign_tag=campaign_tag,
            operation_token=operation_token,
            tombstone_name=tombstone.name,
        )
        if tombstone_present:
            tombstone_stat = tombstone.lstat()
            if (
                not stat.S_ISDIR(tombstone_stat.st_mode)
                or tombstone_stat.st_uid != os.geteuid()
                or tombstone_stat.st_mode & 0o077
                or any(tombstone.iterdir())
            ):
                raise RotationError("claimed deletion tombstone is not an empty owned directory")
            tombstone.rmdir()
            _fsync_directory(state_root)
        claim.unlink()
        _fsync_directory(state_root)
        return True

    tombstone_stat = tombstone.lstat()
    if (
        not stat.S_ISDIR(tombstone_stat.st_mode)
        or tombstone_stat.st_uid != os.geteuid()
        or tombstone_stat.st_mode & 0o077
    ):
        raise RotationError("deletion tombstone is not a private owned directory")
    metadata_path = tombstone / "metadata.json"
    _load_owned_deletion_metadata(
        metadata_path,
        site=site,
        campaign_tag=campaign_tag,
        operation_token=operation_token,
        tombstone_name=tombstone.name,
    )
    if not _state_children_are_deletable(tombstone):
        raise RotationError("deletion tombstone contains unrecognized state")

    for child in tuple(tombstone.iterdir()):
        if child.name == "metadata.json":
            continue
        child.unlink()
        _fsync_directory(tombstone)
    _rename_directory_noreplace(metadata_path, claim)
    _fsync_directory(state_root)
    _fsync_directory(tombstone)
    tombstone.rmdir()
    _fsync_directory(state_root)
    claim.unlink()
    _fsync_directory(state_root)
    return True


def _resume_owned_deletions(
    *,
    state_root: Path,
    site: str,
    campaign_tag: str | None,
) -> int:
    prefix = f".rotation-delete-{site}-"
    tokens: set[str] = set()
    try:
        candidates = tuple(state_root.iterdir())
    except FileNotFoundError:
        return 0
    for candidate in candidates:
        name = candidate.name
        if name.endswith(".claim"):
            name = name.removesuffix(".claim")
        if not name.startswith(prefix):
            continue
        token = name.removeprefix(prefix)
        if re.fullmatch(r"[0-9a-f]{32}", token):
            tokens.add(token)

    resumed = 0
    for token in sorted(tokens):
        tombstone, claim = _deletion_paths(state_root, site, token)
        metadata_path = claim if claim.exists() or claim.is_symlink() else tombstone / "metadata.json"
        try:
            metadata = _load_owned_deletion_metadata(
                metadata_path,
                site=site,
                campaign_tag=campaign_tag,
                operation_token=token,
                tombstone_name=tombstone.name,
            )
        except RotationError:
            # Foreign or malformed tombstones are deliberately preserved.
            continue
        if metadata.get("campaign_tag") != campaign_tag:
            continue
        if _resume_owned_deletion(
            state_root=state_root,
            site=site,
            campaign_tag=campaign_tag,
            operation_token=token,
        ):
            resumed += 1
    return resumed


def _delete_owned_state_directory(
    path: Path,
    *,
    state_root: Path,
    site: str,
    campaign_tag: str | None,
) -> None:
    """Atomically detach owned state, then finish deletion idempotently."""

    if not _owned_staging_directory(
        path,
        site=site,
        campaign_tag=campaign_tag,
    ) and path.name != site:
        raise RotationError("state directory is not an owned staging directory")
    directory_stat = path.lstat()
    if (
        not stat.S_ISDIR(directory_stat.st_mode)
        or directory_stat.st_uid != os.geteuid()
        or directory_stat.st_mode & 0o077
    ):
        raise RotationError("state directory is not a private owned directory")

    metadata_path = path / "metadata.json"
    if metadata_path.exists() or metadata_path.is_symlink():
        if not _private_owned_regular_file(metadata_path):
            raise RotationError("state metadata is not a private owned regular file")
        metadata = _load_metadata(metadata_path)
        if metadata.get("site") != site or metadata.get("campaign_tag") != campaign_tag:
            raise RotationError("state directory belongs to a different campaign")
        if metadata.get("phase") not in {
            "staging",
            "preparing",
            "prepared",
            "revoked",
            "rolled_back",
            "recovered",
            "deleting",
        }:
            raise RotationError("state directory phase is not eligible for deletion")
        operation_token = _require_operation_token(metadata.get("operation_token"))
    else:
        campaign_label = campaign_tag if campaign_tag is not None else "manual"
        match = re.fullmatch(
            rf"\.{re.escape(site)}\.prepare-{re.escape(campaign_label)}-"
            r"(?P<token>[0-9a-f]{32})-[A-Za-z0-9_]{6,64}",
            path.name,
        )
        if match is None or not _owned_staging_directory(
            path,
            site=site,
            campaign_tag=campaign_tag,
        ):
            raise RotationError("metadata-less staging directory is not safely owned")
        operation_token = match.group("token")
        metadata = {
            "site": site,
            "campaign_tag": campaign_tag,
            "operation_token": operation_token,
            "staging_name": path.name,
        }

    if not _state_children_are_deletable(path):
        raise RotationError("state directory contains unrecognized files")
    tombstone, _ = _deletion_paths(state_root, site, operation_token)
    metadata["phase"] = "deleting"
    metadata["deletion_source_name"] = path.name
    metadata["tombstone_name"] = tombstone.name
    _write_metadata(metadata_path, metadata)
    if not _state_children_are_deletable(path):
        raise RotationError("state directory contains unrecognized files")
    _rename_directory_noreplace(path, tombstone)
    _fsync_directory(state_root)
    _resume_owned_deletion(
        state_root=state_root,
        site=site,
        campaign_tag=campaign_tag,
        operation_token=operation_token,
    )


def _next_key_id(current: str) -> str:
    match = re.fullmatch(r"([a-z0-9-]+)-v([1-9][0-9]*)", current)
    if not match:
        raise RotationError("current key id must end in a positive -vN generation")
    candidate = f"{match.group(1)}-v{int(match.group(2)) + 1}"
    if len(candidate) > 64:
        raise RotationError("next key id exceeds the service limit")
    return candidate


def _site_keys(site: str) -> tuple[str, str, str, str, str]:
    suffix, client_name = SITE_SETTINGS[site]
    prefix = f"WRITER_WITNESS_SERVICE_WEBAPP_{suffix}"
    return (
        f"{prefix}_KEY_ID",
        f"{prefix}_SECRET",
        f"{prefix}_PREVIOUS_KEY_ID",
        f"{prefix}_PREVIOUS_SECRET",
        client_name,
    )


def _require_root() -> None:
    if os.geteuid() != 0:
        raise RotationError("rotation must run as root")


def _require_dark_state(expected_epoch: int) -> None:
    result = subprocess.run(
        [
            "runuser",
            "-u",
            "postgres",
            "--",
            "psql",
            "-XAt",
            "-F",
            "|",
            "-d",
            "writer_witness",
            "-c",
            (
                "SELECT authority, writer_epoch, lease_status, "
                "COALESCE(holder_site, '') FROM webapp_writer_witness_state "
                "WHERE authority='webapp';"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip() != f"webapp|{expected_epoch}|vacant|":
        raise RotationError("witness is not in the explicitly expected vacant state")


def _restart_and_verify() -> None:
    subprocess.run(["systemctl", "restart", "writer-witness.service"], check=True)
    subprocess.run(
        ["systemctl", "is-active", "--quiet", "writer-witness.service"], check=True
    )
    last_error: Exception | None = None
    for _ in range(20):
        try:
            with urlopen("http://127.0.0.1:8011/health/ready", timeout=1.0) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # readiness is retried and reported without secrets
            last_error = exc
        time.sleep(0.25)
    raise RotationError("writer witness did not become ready after restart") from last_error


def _require_service_stopped() -> None:
    result = subprocess.run(
        ["systemctl", "is-active", "writer-witness.service"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip() not in {"inactive", "failed"}:
        raise RotationError(
            "--leave-service-stopped requires writer-witness.service to already be stopped"
        )


def _validate_pair(
    runtime: dict[str, str],
    client: dict[str, str],
    key_name: str,
    secret_name: str,
) -> None:
    if (
        not runtime.get(key_name)
        or len(runtime.get(secret_name, "").encode("utf-8")) < 32
        or client.get("WRITER_WITNESS_CLIENT_KEY_ID") != runtime[key_name]
        or client.get("WRITER_WITNESS_CLIENT_SECRET") != runtime[secret_name]
    ):
        raise RotationError("client material does not match the current service credential")


def _quarantine_unclaimed_rotation_directory(
    *,
    site: str,
    campaign_tag: str | None,
    runtime_path: Path,
    client_dir: Path,
    state_root: Path,
) -> str:
    """Reclaim a metadata-less owned claim after proving no mutation occurred."""

    key_name, secret_name, previous_key_name, previous_secret_name, client_name = _site_keys(site)
    rotation_dir = state_root / site
    directory_stat = rotation_dir.lstat()
    if (
        not stat.S_ISDIR(directory_stat.st_mode)
        or directory_stat.st_uid != os.geteuid()
        or directory_stat.st_mode & 0o077
    ):
        raise RotationError("unclaimed rotation state is not a private owned directory")

    _, runtime = _read_env(runtime_path)
    _, client = _read_env(client_dir / client_name)
    _validate_pair(runtime, client, key_name, secret_name)
    if runtime.get(previous_key_name) or runtime.get(previous_secret_name):
        raise RotationError("unclaimed rotation state may have mutated credentials")
    if campaign_tag is not None:
        scenario_key_id = f"matrix-{campaign_tag}-{site.removeprefix('webapp_')}"
        if runtime.get(key_name) == scenario_key_id:
            raise RotationError("unclaimed rotation state may have activated the campaign key")
    if not _state_children_are_deletable(rotation_dir):
        raise RotationError("unclaimed rotation state contains foreign or unsafe entries")

    quarantine = state_root / (
        f".unclaimed-{site}-{int(time.time())}-{secrets.token_hex(6)}"
    )
    _rename_directory_noreplace(rotation_dir, quarantine)
    _fsync_directory(state_root)
    shutil.rmtree(quarantine)
    _fsync_directory(state_root)
    return quarantine.name


def prepare(
    site: str,
    expected_epoch: int,
    runtime_path: Path = RUNTIME_ENV,
    client_dir: Path = CLIENT_DIR,
    state_root: Path = STATE_ROOT,
    campaign_tag: str | None = None,
) -> dict[str, object]:
    _require_root()
    _require_dark_state(expected_epoch)
    if campaign_tag is not None and not re.fullmatch(r"wwm_[0-9a-f]{12}", campaign_tag):
        raise RotationError("matrix campaign tag is invalid")
    key_name, secret_name, previous_key_name, previous_secret_name, client_name = _site_keys(site)
    owned_runtime_keys = {
        key_name,
        secret_name,
        previous_key_name,
        previous_secret_name,
    }
    required_runtime_keys = {key_name, secret_name}
    rotation_dir = state_root / site
    _ensure_private_directory(state_root)
    _resume_owned_deletions(
        state_root=state_root,
        site=site,
        campaign_tag=campaign_tag,
    )
    _cleanup_owned_staging_directories(
        state_root,
        site=site,
        campaign_tag=campaign_tag,
    )
    if rotation_dir.exists() or rotation_dir.is_symlink():
        raise RotationError("unfinished rotation state already exists")
    _, runtime = _read_env(runtime_path)
    _, client = _read_env(client_dir / client_name)
    if any(
        runtime.get(name)
        for name in (previous_key_name, previous_secret_name)
    ):
        raise RotationError("the selected site already has an overlap credential")
    _validate_pair(runtime, client, key_name, secret_name)
    old_key_id = runtime[key_name]
    new_key_id = (
        f"matrix-{campaign_tag}-{site.removeprefix('webapp_')}"
        if campaign_tag is not None
        else _next_key_id(old_key_id)
    )
    if len(new_key_id) > 64:
        raise RotationError("scenario key id exceeds the service limit")
    if new_key_id in runtime.values():
        raise RotationError("next key id collides with an existing setting")
    operation_token = _require_operation_token(secrets.token_hex(16))
    new_secret = secrets.token_hex(32)

    campaign_label = campaign_tag if campaign_tag is not None else "manual"
    staging_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{site}.prepare-{campaign_label}-{operation_token}-",
            dir=state_root,
        )
    )
    os.chmod(staging_dir, 0o700)
    metadata: dict[str, object] = {
        "site": site,
        "expected_epoch": expected_epoch,
        "old_key_id": old_key_id,
        "new_key_id": new_key_id,
        "phase": "staging",
        "campaign_tag": campaign_tag,
        "staging_name": staging_dir.name,
        "operation_token": operation_token,
    }
    published = False
    try:
        # Metadata contains no secret and is written before secret snapshots.
        # The staging name already binds site, campaign, and operation token, so
        # a hard kill inside this first primitive remains safely attributable.
        _write_metadata(staging_dir / "metadata.json", metadata)
        _snapshot_runtime_scope(
            runtime_path,
            staging_dir / "runtime-site.env.before",
            owned_runtime_keys,
            required_runtime_keys,
        )
        _copy_secret(client_dir / client_name, staging_dir / "client.env.before")
        metadata["phase"] = "preparing"
        _write_metadata(staging_dir / "metadata.json", metadata)
        _fsync_directory(staging_dir)
        _rename_directory_noreplace(staging_dir, rotation_dir)
        published = True
        _fsync_directory(state_root)
        _cleanup_and_attest_operation_temps(
            operation_token=operation_token,
            runtime_path=runtime_path,
            client_path=client_dir / client_name,
        )

        _atomic_update_env(
            runtime_path,
            changes={
                key_name: new_key_id,
                secret_name: new_secret,
                previous_key_name: old_key_id,
                previous_secret_name: runtime[secret_name],
            },
            operation_token=operation_token,
        )
        _atomic_update_env(
            client_dir / client_name,
            changes={
                "WRITER_WITNESS_CLIENT_KEY_ID": new_key_id,
                "WRITER_WITNESS_CLIENT_SECRET": new_secret,
            },
            operation_token=operation_token,
        )
        _restart_and_verify()
        metadata["phase"] = "prepared"
        _write_metadata(rotation_dir / "metadata.json", metadata)
        _cleanup_and_attest_operation_temps(
            operation_token=operation_token,
            runtime_path=runtime_path,
            client_path=client_dir / client_name,
        )
    except Exception:
        if published:
            # If rollback itself fails, the complete published journal is left
            # intact so a subsequent explicit recover can retry safely.
            _restore_runtime_scope(
                runtime_path,
                rotation_dir / "runtime-site.env.before",
                owned_runtime_keys,
                required_runtime_keys,
                operation_token=operation_token,
            )
            _copy_secret(
                rotation_dir / "client.env.before",
                client_dir / client_name,
                operation_token=operation_token,
            )
            _restart_and_verify()
            _cleanup_and_attest_operation_temps(
                operation_token=operation_token,
                runtime_path=runtime_path,
                client_path=client_dir / client_name,
            )
            metadata["phase"] = "recovered"
            _write_metadata(rotation_dir / "metadata.json", metadata)
            _delete_owned_state_directory(
                rotation_dir,
                state_root=state_root,
                site=site,
                campaign_tag=campaign_tag,
            )
        elif staging_dir.exists() and not staging_dir.is_symlink():
            _delete_owned_state_directory(
                staging_dir,
                state_root=state_root,
                site=site,
                campaign_tag=campaign_tag,
            )
        _fsync_directory(state_root)
        raise
    return metadata


def revoke(
    site: str,
    expected_epoch: int,
    runtime_path: Path = RUNTIME_ENV,
    client_dir: Path = CLIENT_DIR,
    state_root: Path = STATE_ROOT,
) -> dict[str, object]:
    _require_root()
    _require_dark_state(expected_epoch)
    _ensure_private_directory(state_root)
    key_name, secret_name, previous_key_name, previous_secret_name, client_name = _site_keys(site)
    owned_runtime_keys = {
        key_name,
        secret_name,
        previous_key_name,
        previous_secret_name,
    }
    required_runtime_keys = {key_name, secret_name}
    rotation_dir = state_root / site
    metadata = _load_metadata(rotation_dir / "metadata.json")
    if metadata.get("site") != site or metadata.get("phase") != "prepared":
        raise RotationError("rotation is not ready for revocation")
    operation_token = _require_operation_token(metadata.get("operation_token"))
    _cleanup_and_attest_operation_temps(
        operation_token=operation_token,
        runtime_path=runtime_path,
        client_path=client_dir / client_name,
    )
    _, runtime = _read_env(runtime_path)
    _, client = _read_env(client_dir / client_name)
    _, old_client = _read_env(rotation_dir / "client.env.before")
    if (
        runtime.get(key_name) != metadata.get("new_key_id")
        or runtime.get(previous_key_name) != metadata.get("old_key_id")
        or runtime.get(previous_secret_name) != old_client.get("WRITER_WITNESS_CLIENT_SECRET")
    ):
        raise RotationError("overlap state does not match the prepared rotation")
    _validate_pair(runtime, client, key_name, secret_name)
    _snapshot_runtime_scope(
        runtime_path,
        rotation_dir / "runtime-site.env.overlap",
        owned_runtime_keys,
        required_runtime_keys,
    )
    try:
        _atomic_update_env(
            runtime_path,
            changes={},
            removals={previous_key_name, previous_secret_name},
            operation_token=operation_token,
        )
        _restart_and_verify()
    except Exception:
        _restore_runtime_scope(
            runtime_path,
            rotation_dir / "runtime-site.env.overlap",
            owned_runtime_keys,
            required_runtime_keys,
            operation_token=operation_token,
        )
        _restart_and_verify()
        raise
    metadata["phase"] = "revoked"
    _write_metadata(rotation_dir / "metadata.json", metadata)
    _cleanup_and_attest_operation_temps(
        operation_token=operation_token,
        runtime_path=runtime_path,
        client_path=client_dir / client_name,
    )
    return metadata


def rollback(
    site: str,
    expected_epoch: int,
    runtime_path: Path = RUNTIME_ENV,
    client_dir: Path = CLIENT_DIR,
    state_root: Path = STATE_ROOT,
) -> dict[str, object]:
    _require_root()
    _require_dark_state(expected_epoch)
    _ensure_private_directory(state_root)
    key_name, secret_name, previous_key_name, previous_secret_name, client_name = _site_keys(site)
    owned_runtime_keys = {
        key_name,
        secret_name,
        previous_key_name,
        previous_secret_name,
    }
    required_runtime_keys = {key_name, secret_name}
    rotation_dir = state_root / site
    metadata = _load_metadata(rotation_dir / "metadata.json")
    if metadata.get("site") != site or metadata.get("phase") not in {
        "prepared",
        "revoked",
    }:
        raise RotationError("rotation is not eligible for rollback")
    operation_token = _require_operation_token(metadata.get("operation_token"))
    _cleanup_and_attest_operation_temps(
        operation_token=operation_token,
        runtime_path=runtime_path,
        client_path=client_dir / client_name,
    )
    _restore_runtime_scope(
        runtime_path,
        rotation_dir / "runtime-site.env.before",
        owned_runtime_keys,
        required_runtime_keys,
        operation_token=operation_token,
    )
    _copy_secret(
        rotation_dir / "client.env.before",
        client_dir / client_name,
        operation_token=operation_token,
    )
    _restart_and_verify()
    metadata["phase"] = "rolled_back"
    _write_metadata(rotation_dir / "metadata.json", metadata)
    _cleanup_and_attest_operation_temps(
        operation_token=operation_token,
        runtime_path=runtime_path,
        client_path=client_dir / client_name,
    )
    return metadata


def finish(site: str, state_root: Path = STATE_ROOT) -> dict[str, object]:
    _require_root()
    _ensure_private_directory(state_root)
    rotation_dir = state_root / site
    metadata = _load_metadata(rotation_dir / "metadata.json")
    if metadata.get("site") != site or metadata.get("phase") not in {
        "revoked",
        "rolled_back",
        "recovered",
    }:
        raise RotationError("rotation cannot finish before revocation or rollback")
    result = {
        "site": site,
        "old_key_id": metadata.get("old_key_id"),
        "new_key_id": metadata.get("new_key_id"),
        "phase": "finished",
    }
    _require_operation_token(metadata.get("operation_token"))
    metadata_campaign = metadata.get("campaign_tag")
    if metadata_campaign is not None and not isinstance(metadata_campaign, str):
        raise RotationError("rotation campaign metadata is invalid")
    _delete_owned_state_directory(
        rotation_dir,
        state_root=state_root,
        site=site,
        campaign_tag=metadata_campaign,
    )
    return result


def recover(
    site: str,
    expected_epoch: int,
    campaign_tag: str | None = None,
    runtime_path: Path = RUNTIME_ENV,
    client_dir: Path = CLIENT_DIR,
    state_root: Path = STATE_ROOT,
    *,
    restart_service: bool = True,
) -> dict[str, object]:
    """Idempotently restore the pre-rotation credential after an ambiguous result."""

    _require_root()
    _ensure_private_directory(state_root)
    if not restart_service:
        _require_service_stopped()
    if campaign_tag is not None:
        if not re.fullmatch(r"wwm_[0-9a-f]{12}", campaign_tag):
            raise RotationError("matrix campaign tag is invalid")
    else:
        _require_dark_state(expected_epoch)
    key_name, secret_name, previous_key_name, previous_secret_name, client_name = _site_keys(site)
    owned_runtime_keys = {
        key_name,
        secret_name,
        previous_key_name,
        previous_secret_name,
    }
    required_runtime_keys = {key_name, secret_name}
    rotation_dir = state_root / site
    resumed_deletions = _resume_owned_deletions(
        state_root=state_root,
        site=site,
        campaign_tag=campaign_tag,
    )
    cleaned_staging = _cleanup_owned_staging_directories(
        state_root,
        site=site,
        campaign_tag=campaign_tag,
    )
    try:
        rotation_stat = rotation_dir.lstat()
    except FileNotFoundError:
        return {
            "site": site,
            "phase": "already_clean",
            "campaign_tag": campaign_tag,
            "cleaned_staging": cleaned_staging,
            "resumed_deletions": resumed_deletions,
        }
    if (
        not stat.S_ISDIR(rotation_stat.st_mode)
        or rotation_stat.st_uid != os.geteuid()
        or rotation_stat.st_mode & 0o077
    ):
        raise RotationError("rotation state is not a private owned directory")

    metadata_path = rotation_dir / "metadata.json"
    try:
        metadata_stat = metadata_path.lstat()
    except FileNotFoundError:
        quarantine_name = _quarantine_unclaimed_rotation_directory(
            site=site,
            campaign_tag=campaign_tag,
            runtime_path=runtime_path,
            client_dir=client_dir,
            state_root=state_root,
        )
        return {
            "site": site,
            "phase": "reclaimed_unclaimed",
            "campaign_tag": campaign_tag,
            "quarantine_name": quarantine_name,
            "cleaned_staging": cleaned_staging,
            "resumed_deletions": resumed_deletions,
        }
    if (
        not stat.S_ISREG(metadata_stat.st_mode)
        or metadata_stat.st_uid != os.geteuid()
        or metadata_stat.st_mode & 0o077
    ):
        raise RotationError("rotation metadata is not a private owned regular file")
    metadata = _load_metadata(metadata_path)
    metadata_campaign = metadata.get("campaign_tag")
    if metadata_campaign is not None and not isinstance(metadata_campaign, str):
        raise RotationError("rotation campaign metadata is invalid")
    effective_campaign = campaign_tag if campaign_tag is not None else metadata_campaign
    if metadata.get("phase") == "deleting":
        if campaign_tag is not None and metadata_campaign != campaign_tag:
            raise RotationError("rotation belongs to a different matrix campaign")
        _delete_owned_state_directory(
            rotation_dir,
            state_root=state_root,
            site=site,
            campaign_tag=effective_campaign,
        )
        return {
            "site": site,
            "phase": "already_clean",
            "campaign_tag": campaign_tag,
            "resumed_deletions": resumed_deletions + 1,
        }
    if metadata.get("site") != site or metadata.get("phase") not in {
        "preparing",
        "prepared",
        "revoked",
        "rolled_back",
        "recovered",
    }:
        raise RotationError("rotation state cannot be reconciled safely")
    if campaign_tag is not None and metadata.get("campaign_tag") != campaign_tag:
        raise RotationError("rotation belongs to a different matrix campaign")
    operation_token = _require_operation_token(metadata.get("operation_token"))
    _cleanup_and_attest_operation_temps(
        operation_token=operation_token,
        runtime_path=runtime_path,
        client_path=client_dir / client_name,
    )
    before_runtime = rotation_dir / "runtime-site.env.before"
    before_client = rotation_dir / "client.env.before"
    if not _private_owned_regular_file(before_runtime) or not _private_owned_regular_file(
        before_client
    ):
        raise RotationError("rotation rollback material is incomplete")
    _restore_runtime_scope(
        runtime_path,
        before_runtime,
        owned_runtime_keys,
        required_runtime_keys,
        operation_token=operation_token,
    )
    _copy_secret(
        before_client,
        client_dir / client_name,
        operation_token=operation_token,
    )
    if restart_service:
        _restart_and_verify()
    _cleanup_and_attest_operation_temps(
        operation_token=operation_token,
        runtime_path=runtime_path,
        client_path=client_dir / client_name,
    )
    metadata["phase"] = "recovered"
    _write_metadata(rotation_dir / "metadata.json", metadata)
    _delete_owned_state_directory(
        rotation_dir,
        state_root=state_root,
        site=site,
        campaign_tag=effective_campaign,
    )
    return {
        "site": site,
        "phase": "recovered",
        "campaign_tag": campaign_tag,
        "service_restarted": restart_service,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "revoke", "rollback", "finish", "recover"))
    parser.add_argument("--site", choices=tuple(SITE_SETTINGS), required=True)
    parser.add_argument("--expected-epoch", type=int, required=True)
    parser.add_argument("--campaign-tag")
    parser.add_argument(
        "--leave-service-stopped",
        action="store_true",
        help="recover credentials without restarting writer-witness.service",
    )
    args = parser.parse_args()
    if args.leave_service_stopped and args.action != "recover":
        parser.error("--leave-service-stopped is valid only with recover")
    _ensure_private_directory(STATE_ROOT)
    # FI and IR have independent journals but mutate one shared runtime.env.
    # Serialize both sites so concurrent controllers cannot lose each other's
    # atomic read/modify/write update.
    lock_path = STATE_ROOT / ".runtime.lock"
    lock_flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        lock_flags |= os.O_NOFOLLOW
    lock_descriptor = os.open(lock_path, lock_flags, 0o600)
    try:
        lock_stat = os.fstat(lock_descriptor)
        if (
            not stat.S_ISREG(lock_stat.st_mode)
            or lock_stat.st_uid != os.geteuid()
            or stat.S_IMODE(lock_stat.st_mode) != 0o600
            or lock_stat.st_nlink != 1
        ):
            raise RotationError("rotation lock is not one owner-only regular file")
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        if args.action == "prepare":
            result = prepare(args.site, args.expected_epoch, campaign_tag=args.campaign_tag)
        elif args.action == "revoke":
            result = revoke(args.site, args.expected_epoch)
        elif args.action == "rollback":
            result = rollback(args.site, args.expected_epoch)
        elif args.action == "recover":
            result = recover(
                args.site,
                args.expected_epoch,
                args.campaign_tag,
                restart_service=not args.leave_service_stopped,
            )
        else:
            result = finish(args.site)
    except (OSError, subprocess.SubprocessError, RotationError) as exc:
        raise SystemExit(f"writer witness HMAC rotation failed: {exc}") from exc
    finally:
        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
