#!/usr/bin/env python3
"""Render Writer Witness runtime/client files without reviving old HMAC keys."""

from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime
import fcntl
import functools
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
import uuid
from urllib.parse import urlsplit


SCHEMA_VERSION = "writer_witness_credential_state_v1"
BOOTSTRAP_HMAC_KEYS = frozenset(
    {
        "WITNESS_FI_KEY_ID",
        "WITNESS_FI_HMAC_SECRET",
        "WITNESS_IR_KEY_ID",
        "WITNESS_IR_HMAC_SECRET",
    }
)
BOOTSTRAP_DATABASE_KEYS = frozenset(
    {"WITNESS_DB_MIGRATOR_PASSWORD", "WITNESS_DB_RUNTIME_PASSWORD"}
)
RUNTIME_SITE_KEYS = {
    "webapp_fi": (
        "WRITER_WITNESS_SERVICE_WEBAPP_FI_KEY_ID",
        "WRITER_WITNESS_SERVICE_WEBAPP_FI_SECRET",
        "WRITER_WITNESS_SERVICE_WEBAPP_FI_PREVIOUS_KEY_ID",
        "WRITER_WITNESS_SERVICE_WEBAPP_FI_PREVIOUS_SECRET",
        "WRITER_WITNESS_SERVICE_WEBAPP_FI_NOT_AFTER",
        "WRITER_WITNESS_SERVICE_WEBAPP_FI_PREVIOUS_NOT_AFTER",
    ),
    "webapp_ir": (
        "WRITER_WITNESS_SERVICE_WEBAPP_IR_KEY_ID",
        "WRITER_WITNESS_SERVICE_WEBAPP_IR_SECRET",
        "WRITER_WITNESS_SERVICE_WEBAPP_IR_PREVIOUS_KEY_ID",
        "WRITER_WITNESS_SERVICE_WEBAPP_IR_PREVIOUS_SECRET",
        "WRITER_WITNESS_SERVICE_WEBAPP_IR_NOT_AFTER",
        "WRITER_WITNESS_SERVICE_WEBAPP_IR_PREVIOUS_NOT_AFTER",
    ),
}
CLIENT_FILES = {
    "webapp_fi": "webapp-fi.env",
    "webapp_ir": "webapp-ir.env",
}
KEY_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")
SECRET_PATTERN = re.compile(r"[0-9a-f]{64}")
TRUSTED_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"


class CredentialRenderError(RuntimeError):
    """A credential-state invariant was not satisfied."""


def _require_isolated_runtime() -> None:
    if not (
        sys.flags.isolated
        and sys.flags.no_site
        and sys.flags.ignore_environment
        and sys.flags.dont_write_bytecode
        and getattr(sys.flags, "safe_path", False)
        and sys.flags.utf8_mode == 1
        and sys.pycache_prefix == "/dev/null"
    ):
        raise CredentialRenderError("credential renderer Python startup is not isolated")
    executable = Path(sys.executable)
    prefix = Path(sys.prefix).resolve(strict=True)
    system_runtime = (
        executable.resolve(strict=True) == Path("/usr/bin/python3.12")
        and prefix == Path("/usr")
    )
    release_runtime = (
        prefix.parent == Path("/opt/trading-bot-witness/venvs")
        and prefix.name
        and not prefix.is_symlink()
    )
    if not system_runtime and not release_runtime:
        raise CredentialRenderError("credential renderer is outside an approved runtime")
    allowed = {"PATH": TRUSTED_PATH}
    if os.environ.get("LC_CTYPE") == "C.UTF-8":
        allowed["LC_CTYPE"] = "C.UTF-8"
    if dict(os.environ) != allowed:
        raise CredentialRenderError("credential renderer environment is not clean")


def _reclaim_bootstrap_initializers(path: Path, *, expected_uid: int) -> None:
    prefix = f".{path.name}.initialize-"
    changed = False
    for candidate in path.parent.iterdir():
        if not re.fullmatch(re.escape(prefix) + r"[0-9a-f]{32}", candidate.name):
            continue
        metadata = candidate.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or metadata.st_size > 1_048_576
        ):
            raise CredentialRenderError("unsafe bootstrap initialization residue")
        candidate.unlink()
        changed = True
    if changed:
        _fsync_directory(path.parent)


def initialize_bootstrap(
    path: Path,
    *,
    expected_uid: int,
    expected_gid: int,
) -> dict[str, object]:
    """Create bootstrap material exactly once, or attest the existing schema."""
    _validate_directory(
        path.parent,
        expected_uid=expected_uid,
        expected_gid=None,
        private=False,
    )
    created = False
    if not path.exists() and not path.is_symlink():
        _reclaim_bootstrap_initializers(path, expected_uid=expected_uid)
        payload = (
            "\n".join(
                (
                    f"WITNESS_DB_MIGRATOR_PASSWORD={secrets.token_hex(32)}",
                    f"WITNESS_DB_RUNTIME_PASSWORD={secrets.token_hex(32)}",
                    "WITNESS_FI_KEY_ID=webapp-fi-v1",
                    f"WITNESS_FI_HMAC_SECRET={secrets.token_hex(32)}",
                    "WITNESS_IR_KEY_ID=webapp-ir-v1",
                    f"WITNESS_IR_HMAC_SECRET={secrets.token_hex(32)}",
                    "",
                )
            )
        ).encode("utf-8")
        temporary = path.parent / f".{path.name}.initialize-{uuid.uuid4().hex}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(temporary, flags, 0o600)
        except OSError as exc:
            raise CredentialRenderError("cannot create bootstrap credential file") from exc
        try:
            os.fchmod(descriptor, 0o600)
            os.fchown(descriptor, expected_uid, expected_gid)
            if os.write(descriptor, payload) != len(payload):
                raise CredentialRenderError("short bootstrap credential write")
            os.fsync(descriptor)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            os.close(descriptor)
        _fsync_directory(path.parent)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
        created = True
    else:
        _reclaim_bootstrap_initializers(path, expected_uid=expected_uid)
    _, values = _read_env(
        path,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    if BOOTSTRAP_DATABASE_KEYS.difference(values):
        raise CredentialRenderError("bootstrap database credentials are incomplete")
    for key in BOOTSTRAP_DATABASE_KEYS:
        if SECRET_PATTERN.fullmatch(values[key]) is None:
            raise CredentialRenderError("bootstrap database credential is invalid")
    present_hmac = BOOTSTRAP_HMAC_KEYS.intersection(values)
    if present_hmac and present_hmac != BOOTSTRAP_HMAC_KEYS:
        raise CredentialRenderError("bootstrap HMAC credentials are incomplete")
    if present_hmac:
        _credentials_from_bootstrap(values)
    allowed = BOOTSTRAP_DATABASE_KEYS | BOOTSTRAP_HMAC_KEYS
    if set(values).difference(allowed):
        raise CredentialRenderError("bootstrap credential schema contains an unknown key")
    return {"bootstrap_created": created, "hmac_material_present": bool(present_hmac)}


def _validate_directory(
    path: Path,
    *,
    expected_uid: int,
    expected_gid: int | None,
    private: bool = False,
) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CredentialRenderError(f"required directory is missing: {path}") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or (expected_gid is not None and metadata.st_gid != expected_gid)
        or metadata.st_mode & 0o022
        or (private and metadata.st_mode & 0o077)
    ):
        raise CredentialRenderError(f"unsafe credential directory: {path}")


def _existing_secure_file(
    path: Path,
    *,
    expected_uid: int,
    expected_gid: int,
    expected_mode: int = 0o600,
) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise CredentialRenderError(f"cannot inspect credential file: {path}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or metadata.st_gid != expected_gid
        or stat.S_IMODE(metadata.st_mode) != expected_mode
        or metadata.st_nlink != 1
    ):
        raise CredentialRenderError(f"unsafe credential file: {path}")
    return True


def _read_secure_bytes(
    path: Path,
    *,
    expected_uid: int,
    expected_gid: int,
    maximum: int = 1_048_576,
) -> bytes:
    if not _existing_secure_file(
        path,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    ):
        raise CredentialRenderError(f"required credential file is missing: {path}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CredentialRenderError(f"cannot securely open credential file: {path}") from exc
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
        before = os.fstat(descriptor)
        if before.st_size < 1 or before.st_size > maximum:
            raise CredentialRenderError(f"credential file has unsafe size: {path}")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                raise CredentialRenderError(f"credential file changed during read: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if any(getattr(after, field) != getattr(before, field) for field in stable_fields):
            raise CredentialRenderError(f"credential file changed during read: {path}")
    finally:
        os.close(descriptor)
    final = path.lstat()
    if final.st_dev != before.st_dev or final.st_ino != before.st_ino:
        raise CredentialRenderError(f"credential path changed during read: {path}")
    return b"".join(chunks)


def _parse_env(raw: bytes, *, source: Path) -> tuple[list[str], dict[str, str]]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise CredentialRenderError(f"environment file is not UTF-8: {source}") from exc
    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if (
            not separator
            or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
            or key in values
            or "\x00" in value
            or "\r" in value
        ):
            raise CredentialRenderError(f"invalid or duplicate setting in {source}")
        values[key] = value.strip()
    return lines, values


def _read_env(
    path: Path,
    *,
    expected_uid: int,
    expected_gid: int,
) -> tuple[list[str], dict[str, str]]:
    return _parse_env(
        _read_secure_bytes(
            path,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        ),
        source=path,
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(
    path: Path,
    payload: bytes,
    *,
    expected_uid: int,
    expected_gid: int,
    mode: int = 0o600,
    enforce_parent_gid: bool = True,
) -> None:
    _validate_directory(
        path.parent,
        expected_uid=expected_uid,
        expected_gid=expected_gid if enforce_parent_gid else None,
    )
    if path.exists() or path.is_symlink():
        _existing_secure_file(
            path,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            expected_mode=mode,
        )
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.render-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, expected_uid, expected_gid)
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _credential_pair(key_id: str | None, secret: str | None, *, label: str) -> tuple[str, str]:
    if key_id is None or KEY_ID_PATTERN.fullmatch(key_id) is None:
        raise CredentialRenderError(f"invalid {label} credential key id")
    if secret is None or SECRET_PATTERN.fullmatch(secret) is None:
        raise CredentialRenderError(f"invalid {label} credential secret")
    return key_id, secret


def _credentials_from_runtime(
    values: dict[str, str],
) -> dict[str, tuple[str, str, str | None]]:
    result: dict[str, tuple[str, str, str | None]] = {}
    for site, (
        key_name,
        secret_name,
        previous_key,
        previous_secret,
        not_after_key,
        previous_not_after_key,
    ) in RUNTIME_SITE_KEYS.items():
        if any(
            key in values
            for key in (previous_key, previous_secret, previous_not_after_key)
        ):
            raise CredentialRenderError("cannot provision while an HMAC overlap is active")
        key_id, secret = _credential_pair(
            values.get(key_name),
            values.get(secret_name),
            label=site,
        )
        not_after = values.get(not_after_key)
        expected_campaign_key = re.fullmatch(
            rf"matrix-wwm_[0-9a-f]{{12}}-{site.removeprefix('webapp_')}",
            key_id,
        ) is not None
        if expected_campaign_key != bool(not_after):
            raise CredentialRenderError("campaign credential expiry is missing or unexpected")
        if not_after:
            try:
                parsed = datetime.fromisoformat(not_after.replace("Z", "+00:00"))
            except ValueError as exc:
                raise CredentialRenderError("campaign credential expiry is invalid") from exc
            if not not_after.endswith("Z") or parsed.tzinfo is None:
                raise CredentialRenderError("campaign credential expiry is invalid")
        result[site] = (key_id, secret, not_after)
    return result


def _credentials_from_bootstrap(
    values: dict[str, str],
) -> dict[str, tuple[str, str, str | None]]:
    return {
        "webapp_fi": (*_credential_pair(
            values.get("WITNESS_FI_KEY_ID"),
            values.get("WITNESS_FI_HMAC_SECRET"),
            label="bootstrap webapp_fi",
        ), None),
        "webapp_ir": (*_credential_pair(
            values.get("WITNESS_IR_KEY_ID"),
            values.get("WITNESS_IR_HMAC_SECRET"),
            label="bootstrap webapp_ir",
        ), None),
    }


def _validate_client(
    values: dict[str, str],
    expected: tuple[str, str, str | None],
    *,
    site: str,
) -> None:
    actual = _credential_pair(
        values.get("WRITER_WITNESS_CLIENT_KEY_ID"),
        values.get("WRITER_WITNESS_CLIENT_SECRET"),
        label=f"{site} client",
    )
    if actual != expected[:2]:
        raise CredentialRenderError(f"{site} client credential does not match runtime")


def _acquire_rotation_lock(
    state_root: Path,
    *,
    expected_uid: int,
    expected_gid: int,
) -> int:
    _validate_directory(
        state_root,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        private=True,
    )
    lock_path = state_root / ".runtime.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or metadata.st_gid != expected_gid
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise CredentialRenderError("unsafe HMAC rotation lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CredentialRenderError("cannot provision while HMAC rotation is active") from exc
        if {entry.name for entry in state_root.iterdir()} != {".runtime.lock"}:
            raise CredentialRenderError("cannot provision while HMAC rotation state is not empty")
        return descriptor
    except BaseException:
        if "descriptor" in locals():
            os.close(descriptor)
        raise


def _attest_inherited_rotation_lock(
    state_root: Path,
    descriptor: int,
    *,
    expected_uid: int,
    expected_gid: int,
) -> int:
    if descriptor < 3:
        raise CredentialRenderError("inherited HMAC rotation lock descriptor is invalid")
    _validate_directory(
        state_root,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        private=True,
    )
    lock_path = state_root / ".runtime.lock"
    path_metadata = lock_path.lstat()
    duplicate = os.dup(descriptor)
    try:
        metadata = os.fstat(duplicate)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(path_metadata.st_mode)
            or metadata.st_dev != path_metadata.st_dev
            or metadata.st_ino != path_metadata.st_ino
            or metadata.st_uid != expected_uid
            or metadata.st_gid != expected_gid
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise CredentialRenderError("inherited HMAC rotation lock is unsafe")
        # Reacquiring on the inherited open-file description succeeds only
        # while that same description owns (or can acquire) the exclusive lock.
        fcntl.flock(duplicate, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if {entry.name for entry in state_root.iterdir()} != {".runtime.lock"}:
            raise CredentialRenderError("cannot provision while HMAC rotation state is not empty")
        return duplicate
    except BaseException:
        os.close(duplicate)
        raise


def _holds_rotation_lock(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        inherited = kwargs.get("rotation_lock_fd")
        if inherited is None:
            descriptor = _acquire_rotation_lock(
                kwargs["hmac_state_root"],
                expected_uid=kwargs["expected_uid"],
                expected_gid=kwargs["expected_gid"],
            )
        else:
            descriptor = _attest_inherited_rotation_lock(
                kwargs["hmac_state_root"],
                int(inherited),
                expected_uid=kwargs["expected_uid"],
                expected_gid=kwargs["expected_gid"],
            )
        try:
            return function(*args, **kwargs)
        finally:
            if inherited is None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    return wrapped


def _read_marker(
    marker: Path,
    *,
    expected_uid: int,
    expected_gid: int,
) -> bool:
    if not _existing_secure_file(
        marker,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    ):
        return False
    try:
        payload = json.loads(
            _read_secure_bytes(
                marker,
                expected_uid=expected_uid,
                expected_gid=expected_gid,
            ).decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CredentialRenderError("credential initialization marker is invalid") from exc
    if payload != {"initialized": True, "schema_version": SCHEMA_VERSION}:
        raise CredentialRenderError("credential initialization marker is invalid")
    return True


def _scrub_bootstrap(
    path: Path,
    lines: list[str],
    *,
    expected_uid: int,
    expected_gid: int,
) -> None:
    rendered: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        key = line.partition("=")[0].strip() if line and not line.startswith("#") else ""
        if key not in BOOTSTRAP_HMAC_KEYS:
            rendered.append(raw_line)
    payload = ("\n".join(rendered).rstrip() + "\n").encode("utf-8")
    _atomic_write(
        path,
        payload,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        enforce_parent_gid=False,
    )


def _validate_public_inputs(
    *,
    internal_url: str,
    public_key: str,
    private_key_file: str,
) -> None:
    parsed = urlsplit(internal_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or any(character in internal_url for character in "\r\n\x00")
    ):
        raise CredentialRenderError("Writer Witness internal URL is invalid")
    try:
        decoded_key = base64.b64decode(public_key, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CredentialRenderError("Writer Witness public key is invalid") from exc
    if len(decoded_key) != 32 or any(character in public_key for character in "\r\n\x00"):
        raise CredentialRenderError("Writer Witness public key is invalid")
    private_path = Path(private_key_file)
    if (
        not private_path.is_absolute()
        or ".." in private_path.parts
        or any(character in private_key_file for character in "\r\n\x00")
    ):
        raise CredentialRenderError("Writer Witness private key path is invalid")


@_holds_rotation_lock
def render_credentials(
    *,
    runtime_env: Path,
    client_dir: Path,
    current_runtime_env: Path | None = None,
    current_client_dir: Path | None = None,
    bootstrap_secrets: Path,
    marker: Path,
    hmac_state_root: Path,
    internal_url: str,
    public_key: str,
    private_key_file: str,
    expected_uid: int,
    expected_gid: int,
    finalize: bool = True,
    rotation_lock_fd: int | None = None,
) -> dict[str, object]:
    _validate_public_inputs(
        internal_url=internal_url,
        public_key=public_key,
        private_key_file=private_key_file,
    )
    _validate_directory(
        runtime_env.parent,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    _validate_directory(
        client_dir,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        private=True,
    )
    _validate_directory(
        marker.parent,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        private=True,
    )
    marker_exists = _read_marker(
        marker,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    bootstrap_lines, bootstrap = _read_env(
        bootstrap_secrets,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    missing_database = BOOTSTRAP_DATABASE_KEYS.difference(bootstrap)
    if missing_database:
        raise CredentialRenderError("bootstrap database credentials are incomplete")
    for key in BOOTSTRAP_DATABASE_KEYS:
        if SECRET_PATTERN.fullmatch(bootstrap[key]) is None:
            raise CredentialRenderError("bootstrap database credential is invalid")

    output_paths = {
        "runtime": runtime_env,
        "webapp_fi": client_dir / CLIENT_FILES["webapp_fi"],
        "webapp_ir": client_dir / CLIENT_FILES["webapp_ir"],
    }
    source_runtime = current_runtime_env or runtime_env
    source_clients = current_client_dir or client_dir
    source_paths = {
        "runtime": source_runtime,
        "webapp_fi": source_clients / CLIENT_FILES["webapp_fi"],
        "webapp_ir": source_clients / CLIENT_FILES["webapp_ir"],
    }
    source_existing = {
        name: _existing_secure_file(
            path,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        for name, path in source_paths.items()
    }
    source_count = sum(source_existing.values())

    if source_count == 3:
        _, runtime_values = _read_env(
            source_paths["runtime"],
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        credentials = _credentials_from_runtime(runtime_values)
        for site in ("webapp_fi", "webapp_ir"):
            _, client_values = _read_env(
                source_paths[site],
                expected_uid=expected_uid,
                expected_gid=expected_gid,
            )
            _validate_client(client_values, credentials[site], site=site)
        source = "preserved"
    else:
        if source_count and source_paths != output_paths:
            raise CredentialRenderError("current credential files are incomplete")
        if marker_exists:
            raise CredentialRenderError("initialized credential files are incomplete")
        credentials = _credentials_from_bootstrap(bootstrap)
        output_existing = {
            name: _existing_secure_file(
                path,
                expected_uid=expected_uid,
                expected_gid=expected_gid,
            )
            for name, path in output_paths.items()
        }
        output_count = sum(output_existing.values())
        if output_count:
            if output_existing["runtime"]:
                _, runtime_values = _read_env(
                    output_paths["runtime"],
                    expected_uid=expected_uid,
                    expected_gid=expected_gid,
                )
                if _credentials_from_runtime(runtime_values) != credentials:
                    raise CredentialRenderError("partial runtime does not match initial credentials")
            for site in ("webapp_fi", "webapp_ir"):
                if output_existing[site]:
                    _, client_values = _read_env(
                        output_paths[site],
                        expected_uid=expected_uid,
                        expected_gid=expected_gid,
                    )
                    _validate_client(client_values, credentials[site], site=site)
            source = "initial-resume"
        else:
            source = "initial"

    fi_key, fi_secret, fi_not_after = credentials["webapp_fi"]
    ir_key, ir_secret, ir_not_after = credentials["webapp_ir"]
    runtime_payload = (
        "\n".join(
            (
                "LOGICAL_AUTHORITY=webapp",
                "PHYSICAL_SITE=webapp_ir",
                "WRITER_WITNESS_SERVICE_ENABLED=true",
                "WRITER_WITNESS_DATABASE_URL=postgresql+asyncpg://writer_witness_runtime:"
                f"{bootstrap['WITNESS_DB_RUNTIME_PASSWORD']}@127.0.0.1:5432/writer_witness",
                "WRITER_WITNESS_PRODUCT_DATABASE_USER=trading_bot_product",
                "WRITER_WITNESS_REQUIRE_DISTINCT_DATABASE_IDENTITY=true",
                f"WRITER_WITNESS_PRIVATE_KEY_FILE={private_key_file}",
                f"WRITER_WITNESS_PUBLIC_KEY={public_key}",
                f"WRITER_WITNESS_SERVICE_WEBAPP_FI_KEY_ID={fi_key}",
                f"WRITER_WITNESS_SERVICE_WEBAPP_FI_SECRET={fi_secret}",
                *((f"WRITER_WITNESS_SERVICE_WEBAPP_FI_NOT_AFTER={fi_not_after}",) if fi_not_after else ()),
                f"WRITER_WITNESS_SERVICE_WEBAPP_IR_KEY_ID={ir_key}",
                f"WRITER_WITNESS_SERVICE_WEBAPP_IR_SECRET={ir_secret}",
                *((f"WRITER_WITNESS_SERVICE_WEBAPP_IR_NOT_AFTER={ir_not_after}",) if ir_not_after else ()),
                "WRITER_WITNESS_LEASE_DURATION_SECONDS=180",
                "WRITER_WITNESS_RENEW_INTERVAL_SECONDS=30",
                "WRITER_WITNESS_SAFETY_MARGIN_SECONDS=15",
                "WRITER_WITNESS_MAX_CLOCK_SKEW_SECONDS=5",
                "WRITER_WITNESS_AUTH_MAX_AGE_SECONDS=15",
                "WRITER_WITNESS_AUTHORITATIVE_SITE=webapp_ir",
                "",
            )
        )
    ).encode("utf-8")
    client_payloads = {
        "webapp_fi": (
            "\n".join(
                (
                    f"WRITER_WITNESS_INTERNAL_URL={internal_url}",
                    f"WRITER_WITNESS_CLIENT_KEY_ID={fi_key}",
                    f"WRITER_WITNESS_CLIENT_SECRET={fi_secret}",
                    f"WRITER_WITNESS_PUBLIC_KEY={public_key}",
                    "WRITER_WITNESS_VERIFY_TLS=true",
                    "WRITER_WITNESS_CA_BUNDLE=/run/secrets/witness-ca.pem",
                    "",
                )
            )
        ).encode("utf-8"),
        "webapp_ir": (
            "\n".join(
                (
                    f"WRITER_WITNESS_INTERNAL_URL={internal_url}",
                    f"WRITER_WITNESS_CLIENT_KEY_ID={ir_key}",
                    f"WRITER_WITNESS_CLIENT_SECRET={ir_secret}",
                    f"WRITER_WITNESS_PUBLIC_KEY={public_key}",
                    "WRITER_WITNESS_VERIFY_TLS=true",
                    "WRITER_WITNESS_CA_BUNDLE=/run/secrets/witness-ca.pem",
                    "",
                )
            )
        ).encode("utf-8"),
    }

    _atomic_write(
        runtime_env,
        runtime_payload,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    for site in ("webapp_fi", "webapp_ir"):
        _atomic_write(
            output_paths[site],
            client_payloads[site],
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
    if finalize:
        _atomic_write(
            marker,
            (json.dumps({"initialized": True, "schema_version": SCHEMA_VERSION}, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        _scrub_bootstrap(
            bootstrap_secrets,
            bootstrap_lines,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
    return {
        "credential_source": source,
        "hmac_bootstrap_scrubbed": finalize,
        "marker_attested": finalize,
    }


@_holds_rotation_lock
def finalize_credentials(
    *,
    current_runtime_env: Path,
    current_client_dir: Path,
    bootstrap_secrets: Path,
    marker: Path,
    hmac_state_root: Path,
    expected_uid: int,
    expected_gid: int,
    rotation_lock_fd: int | None = None,
) -> dict[str, object]:
    """Commit credential initialization only after activation publication commits."""
    _validate_directory(
        marker.parent,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        private=True,
    )
    bootstrap_lines, bootstrap = _read_env(
        bootstrap_secrets,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    if BOOTSTRAP_DATABASE_KEYS.difference(bootstrap):
        raise CredentialRenderError("bootstrap database credentials are incomplete")
    for key in BOOTSTRAP_DATABASE_KEYS:
        if SECRET_PATTERN.fullmatch(bootstrap[key]) is None:
            raise CredentialRenderError("bootstrap database credential is invalid")
    _, runtime_values = _read_env(
        current_runtime_env,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    credentials = _credentials_from_runtime(runtime_values)
    for site in ("webapp_fi", "webapp_ir"):
        _, client_values = _read_env(
            current_client_dir / CLIENT_FILES[site],
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        _validate_client(client_values, credentials[site], site=site)
    _atomic_write(
        marker,
        (json.dumps({"initialized": True, "schema_version": SCHEMA_VERSION}, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    _scrub_bootstrap(
        bootstrap_secrets,
        bootstrap_lines,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    return {"hmac_bootstrap_scrubbed": True, "marker_attested": True}


def write_database_env(
    *,
    bootstrap_secrets: Path,
    output: Path,
    expected_uid: int,
    expected_gid: int,
) -> dict[str, object]:
    """Copy only validated fixed-format database secrets to a transient env file."""
    _, values = _read_env(
        bootstrap_secrets,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    if BOOTSTRAP_DATABASE_KEYS.difference(values):
        raise CredentialRenderError("bootstrap database credentials are incomplete")
    for key in BOOTSTRAP_DATABASE_KEYS:
        if SECRET_PATTERN.fullmatch(values[key]) is None:
            raise CredentialRenderError("bootstrap database credential is invalid")
    payload = (
        f"WITNESS_DB_MIGRATOR_PASSWORD={values['WITNESS_DB_MIGRATOR_PASSWORD']}\n"
        f"WITNESS_DB_RUNTIME_PASSWORD={values['WITNESS_DB_RUNTIME_PASSWORD']}\n"
    ).encode("utf-8")
    _atomic_write(
        output,
        payload,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    return {"database_env_written": True}


def main() -> int:
    _require_isolated_runtime()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("initialize-bootstrap", "database-env", "prepare", "finalize"),
        default="prepare",
    )
    parser.add_argument("--runtime-env", type=Path)
    parser.add_argument("--client-dir", type=Path)
    parser.add_argument("--current-runtime-env", type=Path)
    parser.add_argument("--current-client-dir", type=Path)
    parser.add_argument("--bootstrap-secrets", type=Path, required=True)
    parser.add_argument("--marker", type=Path)
    parser.add_argument("--hmac-state-root", type=Path)
    parser.add_argument("--database-env-output", type=Path)
    parser.add_argument("--internal-url")
    parser.add_argument("--public-key")
    parser.add_argument("--private-key-file")
    parser.add_argument("--expected-uid", type=int, default=0)
    parser.add_argument("--expected-gid", type=int, default=0)
    parser.add_argument("--rotation-lock-fd", type=int)
    args = parser.parse_args()
    if args.mode in {"prepare", "finalize"} and args.rotation_lock_fd is None:
        parser.error(
            "prepare/finalize require a caller-owned --rotation-lock-fd acquired before credential rendering"
        )
    if args.mode == "initialize-bootstrap":
        result = initialize_bootstrap(
            args.bootstrap_secrets,
            expected_uid=args.expected_uid,
            expected_gid=args.expected_gid,
        )
    elif args.mode == "database-env":
        if not args.database_env_output:
            parser.error("database-env mode requires --database-env-output")
        result = write_database_env(
            bootstrap_secrets=args.bootstrap_secrets,
            output=args.database_env_output,
            expected_uid=args.expected_uid,
            expected_gid=args.expected_gid,
        )
    elif args.mode == "prepare":
        if not all(
            (
                args.runtime_env,
                args.client_dir,
                args.marker,
                args.hmac_state_root,
                args.internal_url,
                args.public_key,
                args.private_key_file,
            )
        ):
            parser.error("prepare mode requires output paths and public runtime inputs")
        result = render_credentials(
            runtime_env=args.runtime_env,
            client_dir=args.client_dir,
            current_runtime_env=args.current_runtime_env,
            current_client_dir=args.current_client_dir,
            bootstrap_secrets=args.bootstrap_secrets,
            marker=args.marker,
            hmac_state_root=args.hmac_state_root,
            internal_url=args.internal_url,
            public_key=args.public_key,
            private_key_file=args.private_key_file,
            expected_uid=args.expected_uid,
            expected_gid=args.expected_gid,
            finalize=False,
            rotation_lock_fd=args.rotation_lock_fd,
        )
    else:
        if not all(
            (
                args.current_runtime_env,
                args.current_client_dir,
                args.marker,
                args.hmac_state_root,
            )
        ):
            parser.error("finalize mode requires the committed current credential paths")
        result = finalize_credentials(
            current_runtime_env=args.current_runtime_env,
            current_client_dir=args.current_client_dir,
            bootstrap_secrets=args.bootstrap_secrets,
            marker=args.marker,
            hmac_state_root=args.hmac_state_root,
            expected_uid=args.expected_uid,
            expected_gid=args.expected_gid,
            rotation_lock_fd=args.rotation_lock_fd,
        )
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
