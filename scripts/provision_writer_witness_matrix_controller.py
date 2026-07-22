#!/usr/bin/env python3
"""Provision the root-owned controller state and SSH signer trust policy.

This utility deliberately accepts public keys only.  The corresponding private
keys must remain on independent operator devices and are never copied to the
Matrix controller.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import tempfile
from typing import Sequence


PRODUCTION_CONFIG_ROOT = Path("/etc/trading-bot-witness-matrix")
PRODUCTION_CONTROLLER_ROOT = Path("/var/lib/trading-bot-witness-matrix")
PRODUCTION_RUNTIME_ROOT = Path("/run/writer-witness-matrix-controller")
IDENTITY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@+-]{0,127}\Z")
MAX_PUBLIC_KEY_BYTES = 16_384
SUPPORTED_KEY_TYPES = {
    "ssh-ed25519",
    "sk-ssh-ed25519@openssh.com",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ecdsa-sha2-nistp256@openssh.com",
    "ssh-rsa",
}


class ProvisionError(RuntimeError):
    """A fail-closed provisioning validation error."""


@dataclass(frozen=True)
class PublicKey:
    key_type: str
    encoded_blob: str
    blob: bytes

    @property
    def fingerprint(self) -> str:
        digest = base64.b64encode(hashlib.sha256(self.blob).digest()).decode("ascii")
        return "SHA256:" + digest.rstrip("=")


@dataclass(frozen=True)
class ProvisionConfig:
    observer_identity: str
    observer_public_key_file: Path
    incident_commander_identity: str
    incident_commander_public_key_file: Path
    config_root: Path = PRODUCTION_CONFIG_ROOT
    controller_root: Path = PRODUCTION_CONTROLLER_ROOT
    runtime_root: Path = PRODUCTION_RUNTIME_ROOT
    owner_uid: int = 0
    owner_gid: int = 0
    test_mode: bool = False


def _write_all(descriptor: int, raw: bytes) -> None:
    offset = 0
    while offset < len(raw):
        written = os.write(descriptor, raw[offset:])
        if written <= 0:
            raise ProvisionError("short write while publishing controller policy")
        offset += written


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_identity(value: str, *, role: str) -> str:
    if value != value.strip() or not IDENTITY_PATTERN.fullmatch(value):
        raise ProvisionError(
            f"{role} identity must contain only safe OpenSSH principal characters"
        )
    return value


def _validate_root_path(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise ProvisionError(f"{label} must be an absolute path")
    normalized = Path(os.path.normpath(os.fspath(path)))
    if normalized != path:
        raise ProvisionError(f"{label} must not contain traversal components")
    try:
        resolved = path.resolve(strict=False)
    except OSError as exc:
        raise ProvisionError(f"cannot resolve {label}") from exc
    if resolved != path:
        raise ProvisionError(f"{label} must not traverse a symbolic link")
    return path


def _assert_existing_target_safe(path: Path, *, uid: int, gid: int) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ProvisionError(f"cannot inspect existing trust policy: {path}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise ProvisionError(
            "existing allowed_signers must be one owner-controlled mode-0600 regular file"
        )


def _ensure_directory(path: Path, *, uid: int, gid: int) -> None:
    """Create or validate one real directory, then force its exact safe mode."""

    try:
        path.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        created = False
    except OSError as exc:
        raise ProvisionError(f"cannot create controller directory: {path}") from exc

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProvisionError(f"controller path is not a real directory: {path}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise ProvisionError(f"controller path is not a directory: {path}")
        if not created and (metadata.st_uid != uid or metadata.st_gid != gid):
            raise ProvisionError(f"controller directory has an unexpected owner: {path}")
        if created:
            os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, 0o700)
        os.fsync(descriptor)
        verified = os.fstat(descriptor)
        if (
            verified.st_uid != uid
            or verified.st_gid != gid
            or stat.S_IMODE(verified.st_mode) != 0o700
        ):
            raise ProvisionError(f"controller directory hardening failed: {path}")
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _read_owner_safe_file(path: Path, *, uid: int, gid: int, role: str) -> bytes:
    """Read an immutable snapshot without following or blocking on special files."""

    try:
        before = path.lstat()
    except OSError as exc:
        raise ProvisionError(f"cannot inspect {role} public key file") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != uid
        or before.st_gid != gid
        or stat.S_IMODE(before.st_mode) != 0o600
        or before.st_nlink != 1
        or before.st_size < 1
        or before.st_size > MAX_PUBLIC_KEY_BYTES
    ):
        raise ProvisionError(
            f"{role} public key must be one owner-controlled mode-0600 regular file"
        )

    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProvisionError(f"cannot securely open {role} public key file") from exc
    try:
        opened = os.fstat(descriptor)
        stable_fields = (
            "st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink",
            "st_size", "st_mtime_ns", "st_ctime_ns",
        )
        if any(getattr(opened, item) != getattr(before, item) for item in stable_fields):
            raise ProvisionError(f"{role} public key changed before its immutable read")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                raise ProvisionError(f"{role} public key changed during its immutable read")
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if any(getattr(after, item) != getattr(opened, item) for item in stable_fields):
            raise ProvisionError(f"{role} public key changed during its immutable read")
    finally:
        os.close(descriptor)

    try:
        final_path = path.lstat()
    except OSError as exc:
        raise ProvisionError(f"cannot revalidate {role} public key file") from exc
    if final_path.st_dev != before.st_dev or final_path.st_ino != before.st_ino:
        raise ProvisionError(f"{role} public key path changed during its immutable read")
    return raw


def _decode_ssh_fields(blob: bytes) -> list[bytes]:
    fields: list[bytes] = []
    offset = 0
    while offset < len(blob):
        if len(blob) - offset < 4:
            raise ProvisionError("OpenSSH public key blob is truncated")
        length = struct.unpack(">I", blob[offset : offset + 4])[0]
        offset += 4
        if length > len(blob) - offset:
            raise ProvisionError("OpenSSH public key blob contains an invalid field length")
        fields.append(blob[offset : offset + length])
        offset += length
    return fields


def _parse_public_key(raw: bytes, *, role: str) -> PublicKey:
    try:
        rendered = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ProvisionError(f"{role} public key is not ASCII OpenSSH text") from exc
    lines = [line for line in rendered.splitlines() if line]
    if len(lines) != 1 or lines[0] != lines[0].strip():
        raise ProvisionError(f"{role} public key file must contain exactly one key line")
    fields = lines[0].split()
    if len(fields) < 2:
        raise ProvisionError(f"{role} public key line is incomplete")
    key_type, encoded_blob = fields[:2]
    if key_type not in SUPPORTED_KEY_TYPES:
        raise ProvisionError(f"{role} public key uses an unsupported key type")
    try:
        blob = base64.b64decode(encoded_blob, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ProvisionError(f"{role} public key contains invalid base64") from exc
    ssh_fields = _decode_ssh_fields(blob)
    try:
        embedded_type = ssh_fields[0].decode("ascii")
    except (IndexError, UnicodeDecodeError) as exc:
        raise ProvisionError(f"{role} public key blob lacks its key type") from exc
    if embedded_type != key_type:
        raise ProvisionError(f"{role} public key type does not match its encoded blob")

    expected_counts = {
        "ssh-ed25519": 2,
        "sk-ssh-ed25519@openssh.com": 3,
        "ecdsa-sha2-nistp256": 3,
        "ecdsa-sha2-nistp384": 3,
        "ecdsa-sha2-nistp521": 3,
        "sk-ecdsa-sha2-nistp256@openssh.com": 4,
        "ssh-rsa": 3,
    }
    if len(ssh_fields) != expected_counts[key_type] or any(not item for item in ssh_fields[1:]):
        raise ProvisionError(f"{role} public key blob has an invalid structure")
    if key_type in {"ssh-ed25519", "sk-ssh-ed25519@openssh.com"} and len(ssh_fields[1]) != 32:
        raise ProvisionError(f"{role} Ed25519 public key has an invalid length")
    if key_type.startswith("ecdsa-sha2-"):
        expected_curve = key_type.removeprefix("ecdsa-sha2-").encode("ascii")
        if ssh_fields[1] != expected_curve:
            raise ProvisionError(f"{role} ECDSA key has a mismatched curve")
    if key_type == "sk-ecdsa-sha2-nistp256@openssh.com" and ssh_fields[1] != b"nistp256":
        raise ProvisionError(f"{role} security-key ECDSA key has a mismatched curve")
    return PublicKey(key_type=key_type, encoded_blob=encoded_blob, blob=blob)


def _atomic_install(path: Path, raw: bytes, *, uid: int, gid: int) -> None:
    if not raw:
        raise ProvisionError("refusing to publish an empty trusted signer policy")
    _assert_existing_target_safe(path, uid=uid, gid=gid)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.install-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    published = False
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, uid, gid)
        _write_all(descriptor, raw)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != uid
            or metadata.st_gid != gid
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or metadata.st_size != len(raw)
        ):
            raise ProvisionError("temporary trusted signer policy failed attestation")
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        published = True
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()
            _fsync_directory(path.parent)

    if not published:
        raise ProvisionError("trusted signer policy was not published")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        installed = b""
        while len(installed) < len(raw):
            chunk = os.read(descriptor, len(raw) - len(installed))
            if not chunk:
                break
            installed += chunk
        if (
            installed != raw
            or metadata.st_uid != uid
            or metadata.st_gid != gid
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise ProvisionError("installed trusted signer policy failed read-back attestation")
    finally:
        os.close(descriptor)


def _validate_execution_context(config: ProvisionConfig) -> None:
    roots = (
        _validate_root_path(config.config_root, label="config root"),
        _validate_root_path(config.controller_root, label="controller root"),
        _validate_root_path(config.runtime_root, label="runtime root"),
    )
    if len(set(roots)) != 3:
        raise ProvisionError("controller roots must be three distinct paths")
    if config.owner_uid < 0 or config.owner_gid < 0:
        raise ProvisionError("owner uid and gid must be non-negative")
    if config.test_mode:
        production_roots = {
            PRODUCTION_CONFIG_ROOT, PRODUCTION_CONTROLLER_ROOT, PRODUCTION_RUNTIME_ROOT,
        }
        if any(path in production_roots for path in roots):
            raise ProvisionError("test mode must not target a production controller root")
        return
    if os.geteuid() != 0:
        raise ProvisionError("production controller provisioning must run as root")
    if roots != (
        PRODUCTION_CONFIG_ROOT, PRODUCTION_CONTROLLER_ROOT, PRODUCTION_RUNTIME_ROOT,
    ) or config.owner_uid != 0 or config.owner_gid != 0:
        raise ProvisionError("production roots and ownership cannot be overridden")


def provision(config: ProvisionConfig) -> dict[str, object]:
    _validate_execution_context(config)
    observer_identity = _validate_identity(config.observer_identity, role="observer")
    commander_identity = _validate_identity(
        config.incident_commander_identity, role="incident commander"
    )
    if observer_identity.casefold() == commander_identity.casefold():
        raise ProvisionError("observer and incident commander identities must be distinct")

    observer = _parse_public_key(
        _read_owner_safe_file(
            config.observer_public_key_file,
            uid=config.owner_uid,
            gid=config.owner_gid,
            role="observer",
        ),
        role="observer",
    )
    commander = _parse_public_key(
        _read_owner_safe_file(
            config.incident_commander_public_key_file,
            uid=config.owner_uid,
            gid=config.owner_gid,
            role="incident commander",
        ),
        role="incident commander",
    )
    if observer.blob == commander.blob:
        raise ProvisionError("observer and incident commander must use different public keys")

    for path in (config.config_root, config.controller_root, config.runtime_root):
        if not path.parent.is_dir():
            raise ProvisionError(f"controller root parent does not exist: {path.parent}")
        _ensure_directory(path, uid=config.owner_uid, gid=config.owner_gid)
    for path in (
        config.controller_root / "campaigns",
        config.controller_root / "campaigns" / "consumed-approvals",
        config.controller_root / "campaigns" / "consumed-preflights",
        config.controller_root / "runs",
    ):
        _ensure_directory(path, uid=config.owner_uid, gid=config.owner_gid)

    policy = (
        f"{observer_identity} {observer.key_type} {observer.encoded_blob}\n"
        f"{commander_identity} {commander.key_type} {commander.encoded_blob}\n"
    ).encode("ascii")
    policy_path = config.config_root / "allowed_signers"
    _atomic_install(policy_path, policy, uid=config.owner_uid, gid=config.owner_gid)

    return {
        "schema_version": "writer_witness_matrix_controller_provision_v1",
        "allowed_signers": str(policy_path),
        "allowed_signers_sha256": hashlib.sha256(policy).hexdigest(),
        "observer_identity": observer_identity,
        "observer_key_fingerprint": observer.fingerprint,
        "incident_commander_identity": commander_identity,
        "incident_commander_key_fingerprint": commander.fingerprint,
        "private_keys_copied": False,
        "directories": [
            str(config.controller_root),
            str(config.controller_root / "campaigns"),
            str(config.controller_root / "campaigns" / "consumed-approvals"),
            str(config.controller_root / "campaigns" / "consumed-preflights"),
            str(config.controller_root / "runs"),
            str(config.runtime_root),
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observer-identity", required=True)
    parser.add_argument("--observer-public-key-file", required=True, type=Path)
    parser.add_argument("--incident-commander-identity", required=True)
    parser.add_argument("--incident-commander-public-key-file", required=True, type=Path)
    parser.add_argument("--test-mode", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--config-root", type=Path, default=PRODUCTION_CONFIG_ROOT, help=argparse.SUPPRESS)
    parser.add_argument(
        "--controller-root", type=Path, default=PRODUCTION_CONTROLLER_ROOT, help=argparse.SUPPRESS
    )
    parser.add_argument("--runtime-root", type=Path, default=PRODUCTION_RUNTIME_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--owner-uid", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--owner-gid", type=int, default=0, help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = provision(
            ProvisionConfig(
                observer_identity=args.observer_identity,
                observer_public_key_file=args.observer_public_key_file,
                incident_commander_identity=args.incident_commander_identity,
                incident_commander_public_key_file=args.incident_commander_public_key_file,
                config_root=args.config_root,
                controller_root=args.controller_root,
                runtime_root=args.runtime_root,
                owner_uid=args.owner_uid,
                owner_gid=args.owner_gid,
                test_mode=args.test_mode,
            )
        )
    except (OSError, ProvisionError) as exc:
        print(f"controller provisioning refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
