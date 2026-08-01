#!/usr/bin/env python3
"""Verify or explicitly install a reviewed staged preflight receipt endpoint.

The renderer deliberately produces only a fresh review tree.  This companion
installer closes that deployment boundary without making import or default CLI
execution mutate a host:

* default invocation securely verifies the fixed staged tree and prints a
  non-authorizing result;
* ``--apply --confirm-staged-install`` is the only local mutation path;
* apply re-renders and byte-compares every staged asset, syntax-checks staged
  sshd/sudoers files, atomically replaces each fixed destination with rollback
  on local failure, validates the installed syntax, and writes a root-local
  installation attestation; and
* it never reloads sshd, creates a Unix account, opens a network connection,
  runs a receipt collector, or starts Full Matrix.

The two unprivileged accounts remain an intentional explicit host-admin step:
their exact rendered account policies must be reconciled before sshd reload.
Keeping account creation/reload out of this file avoids a successful asset
install unexpectedly altering login availability during a recovery window.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
from typing import Any, Sequence


sys.dont_write_bytecode = True

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from core import dedicated_host_preflight_receipt_agent_boundary as boundary  # noqa: E402
from core import dedicated_host_preflight_receipt_agent_installation as installation  # noqa: E402
from scripts import render_dedicated_host_preflight_receipt_agent as renderer  # noqa: E402


DEFAULT_RENDER_ROOT = renderer.DEFAULT_RENDER_ROOT
LIVE_ROOT = Path("/")
FIXED_INSTALLATION_STATE_ROOT = Path(
    "/var/lib/trading-bot/dedicated-host-preflight/receipt-agent-installation"
)
FIXED_INSTALLATION_ATTESTATION = FIXED_INSTALLATION_STATE_ROOT / "installation-attestation.json"
FIXED_INSTALLATION_LOCK = FIXED_INSTALLATION_STATE_ROOT / "installation.lock"
FIXED_SSHD_BINARY = Path("/usr/sbin/sshd")
FIXED_VISUDO_BINARY = Path("/usr/sbin/visudo")

_MAX_FILE_BYTES = 64 * 1024
_ALLOWED_MODES = frozenset({0o440, 0o600, 0o644, 0o755})
_TMP_PREFIX = ".receipt-agent-install-"


class ReceiptAgentInstallerError(RuntimeError):
    """Fixed-code local install refusal; errors never expose asset contents."""


@dataclass(frozen=True)
class _LiveBackup:
    destination: Path
    existed: bool
    content: bytes = b""
    mode: int = 0


def _fail(code: str) -> None:
    raise ReceiptAgentInstallerError(code)


def _require_root() -> None:
    try:
        root = os.getuid() == 0 and os.geteuid() == 0
    except OSError:
        root = False
    if not root:
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_ROOT_REQUIRED")


def _absolute_relative(path: Path) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_PATH_INVALID")
    try:
        relative = path.relative_to("/")
    except ValueError:
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_PATH_INVALID")
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_PATH_INVALID")
    return relative


def _live_path(destination: Path) -> Path:
    return LIVE_ROOT / _absolute_relative(destination)


def _directory_flags() -> int:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_PLATFORM_UNSUPPORTED")
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _file_flags() -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_PLATFORM_UNSUPPORTED")
    return os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _require_root_directory(path: Path, *, exact_mode: int | None, code: str) -> None:
    if not path.is_absolute() or ".." in path.parts:
        _fail(code)
    try:
        before = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail(code)
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISDIR(before.st_mode)
        or before.st_uid != 0
        or (exact_mode is not None and stat.S_IMODE(before.st_mode) != exact_mode)
        or (exact_mode is None and stat.S_IMODE(before.st_mode) & 0o022)
    ):
        _fail(code)


def _open_stage_root() -> int:
    _require_root_directory(
        DEFAULT_RENDER_ROOT,
        exact_mode=0o700,
        code="PREFLIGHT_RECEIPT_AGENT_STAGE_ROOT_UNSAFE",
    )
    try:
        descriptor = os.open(DEFAULT_RENDER_ROOT, _directory_flags())
    except OSError:
        _fail("PREFLIGHT_RECEIPT_AGENT_STAGE_ROOT_UNSAFE")
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        os.close(descriptor)
        _fail("PREFLIGHT_RECEIPT_AGENT_STAGE_ROOT_UNSAFE")
    return descriptor


def _read_stage_file(root_fd: int, destination: Path, *, expected_mode: int) -> bytes:
    relative = _absolute_relative(destination)
    descriptor = os.dup(root_fd)
    file_descriptor = -1
    try:
        for component in relative.parts[:-1]:
            next_descriptor = os.open(component, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                _fail("PREFLIGHT_RECEIPT_AGENT_STAGE_FILE_UNSAFE")
        before = os.stat(relative.name, dir_fd=descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != expected_mode
            or not 1 <= before.st_size <= _MAX_FILE_BYTES
        ):
            _fail("PREFLIGHT_RECEIPT_AGENT_STAGE_FILE_UNSAFE")
        file_descriptor = os.open(relative.name, _file_flags(), dir_fd=descriptor)
        opened = os.fstat(file_descriptor)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
        )
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mode,
            opened.st_uid,
            opened.st_nlink,
        ) != identity:
            _fail("PREFLIGHT_RECEIPT_AGENT_STAGE_FILE_UNSAFE")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(file_descriptor, min(65536, remaining))
            if not chunk:
                _fail("PREFLIGHT_RECEIPT_AGENT_STAGE_FILE_UNSAFE")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(file_descriptor, 1):
            _fail("PREFLIGHT_RECEIPT_AGENT_STAGE_FILE_UNSAFE")
        after = os.fstat(file_descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mode,
            after.st_uid,
            after.st_nlink,
        ) != identity:
            _fail("PREFLIGHT_RECEIPT_AGENT_STAGE_FILE_UNSAFE")
        return b"".join(chunks)
    except ReceiptAgentInstallerError:
        raise
    except OSError:
        _fail("PREFLIGHT_RECEIPT_AGENT_STAGE_FILE_UNSAFE")
    finally:
        if file_descriptor >= 0:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        try:
            os.close(descriptor)
        except OSError:
            pass


def _parse_stage_config(root_fd: int) -> boundary.ReceiptAgentInstallationConfig:
    runtime_raw = _read_stage_file(
        root_fd,
        boundary.FIXED_PREFLIGHT_ROOT_COLLECTOR_CONFIG,
        expected_mode=0o600,
    )
    authorized_raw = _read_stage_file(
        root_fd,
        boundary.FIXED_PREFLIGHT_AUTHORIZED_KEYS,
        expected_mode=0o644,
    )
    try:
        runtime_value = json.loads(runtime_raw.decode("ascii", "strict"))
        runtime_config = boundary.parse_receipt_agent_runtime_config(runtime_value)
        controller_key = boundary.parse_receipt_agent_authorized_key_bytes(authorized_raw)
        return boundary.ReceiptAgentInstallationConfig(
            enabled=runtime_config.enabled,
            site_role=runtime_config.site_role,
            agent_release_sha=runtime_config.agent_release_sha,
            controller_public_key=controller_key,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, boundary.ReceiptAgentBoundaryError):
        _fail("PREFLIGHT_RECEIPT_AGENT_STAGE_INVALID")


def _load_verified_stage() -> installation.VerifiedReceiptAgentInstallationStage:
    root_fd = _open_stage_root()
    try:
        config = _parse_stage_config(root_fd)
        expected = boundary.render_receipt_agent_assets(config)
        stage_files = {
            item.destination: (
                _read_stage_file(root_fd, item.destination, expected_mode=item.mode),
                item.mode,
            )
            for item in expected.files
        }
    finally:
        os.close(root_fd)
    try:
        return installation.verify_staged_receipt_agent_assets(stage_files)
    except installation.DedicatedHostPreflightReceiptAgentInstallationError:
        _fail("PREFLIGHT_RECEIPT_AGENT_STAGE_MISMATCH")


def _binary(path: Path, *, code: str) -> Path:
    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail(code)
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not metadata.st_mode & stat.S_IXUSR
    ):
        _fail(code)
    return path


def _validate_command(arguments: tuple[str, ...], *, code: str) -> None:
    try:
        completed = subprocess.run(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env={"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
            close_fds=True,
            check=False,
            shell=False,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail(code)
    if completed.returncode != 0:
        _fail(code)


def _validate_assets_at(root: Path, stage: installation.VerifiedReceiptAgentInstallationStage) -> None:
    sshd = _binary(FIXED_SSHD_BINARY, code="PREFLIGHT_RECEIPT_AGENT_SSHD_VALIDATOR_UNAVAILABLE")
    visudo = _binary(FIXED_VISUDO_BINARY, code="PREFLIGHT_RECEIPT_AGENT_SUDOERS_VALIDATOR_UNAVAILABLE")
    for item in stage.files:
        live = root / _absolute_relative(item.destination)
        if item.destination.suffix == ".conf" and "sshd_config.d" in item.destination.parts:
            _validate_command(
                (str(sshd), "-t", "-f", str(live)),
                code="PREFLIGHT_RECEIPT_AGENT_SSHD_SYNTAX_INVALID",
            )
        if "sudoers.d" in item.destination.parts:
            _validate_command(
                (str(visudo), "-cf", str(live)),
                code="PREFLIGHT_RECEIPT_AGENT_SUDOERS_SYNTAX_INVALID",
            )


def _ensure_live_directory(path: Path, *, mode: int) -> None:
    if not path.is_absolute() or ".." in path.parts:
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_PATH_INVALID")
    try:
        path.mkdir(mode=mode, parents=True, exist_ok=True)
        os.chmod(path, mode)
    except OSError:
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_PARENT_UNSAFE")
    _require_root_directory(
        path,
        exact_mode=mode,
        code="PREFLIGHT_RECEIPT_AGENT_INSTALL_PARENT_UNSAFE",
    )


def _prepare_live_parents(stage: installation.VerifiedReceiptAgentInstallationStage) -> None:
    # Only these two leaf roots may be created by this installer.  Standard
    # sshd/sudoers directories must already exist and be root-controlled.
    _ensure_live_directory(
        _live_path(Path("/etc/trading-bot/security/dedicated-host-preflight")),
        mode=0o700,
    )
    _ensure_live_directory(
        _live_path(Path("/usr/local/libexec/trading-bot/dedicated-host-preflight")),
        mode=0o755,
    )
    for item in stage.files:
        parent = _live_path(item.destination).parent
        if not parent.exists():
            _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_PARENT_UNSAFE")
        _require_root_directory(
            parent,
            exact_mode=None,
            code="PREFLIGHT_RECEIPT_AGENT_INSTALL_PARENT_UNSAFE",
        )


def _read_existing_live(destination: Path) -> _LiveBackup:
    path = _live_path(destination)
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return _LiveBackup(destination=destination, existed=False)
    except OSError:
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_TARGET_UNSAFE")
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) not in _ALLOWED_MODES
        or before.st_size > _MAX_FILE_BYTES
    ):
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_TARGET_UNSAFE")
    descriptor = -1
    try:
        descriptor = os.open(path, _file_flags())
        opened = os.fstat(descriptor)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
        )
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mode,
            opened.st_uid,
            opened.st_nlink,
        ) != identity:
            _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_TARGET_UNSAFE")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_TARGET_UNSAFE")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_TARGET_UNSAFE")
        after = os.fstat(descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mode,
            after.st_uid,
            after.st_nlink,
        ) != identity:
            _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_TARGET_UNSAFE")
        return _LiveBackup(
            destination=destination,
            existed=True,
            content=b"".join(chunks),
            mode=stat.S_IMODE(opened.st_mode),
        )
    except ReceiptAgentInstallerError:
        raise
    except OSError:
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_TARGET_UNSAFE")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _atomic_replace(path: Path, *, content: bytes, mode: int) -> None:
    if not 1 <= len(content) <= _MAX_FILE_BYTES or mode not in _ALLOWED_MODES:
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_WRITE_FAILED")
    parent = path.parent
    _require_root_directory(parent, exact_mode=None, code="PREFLIGHT_RECEIPT_AGENT_INSTALL_PARENT_UNSAFE")
    directory_fd = -1
    descriptor = -1
    temporary_name = ""
    try:
        directory_fd = os.open(parent, _directory_flags())
        temporary_name = _TMP_PREFIX + secrets.token_hex(16)
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            mode,
            dir_fd=directory_fd,
        )
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_WRITE_FAILED")
            offset += written
        os.fchmod(descriptor, mode)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != mode
            or opened.st_size != len(content)
        ):
            _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_WRITE_FAILED")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.rename(temporary_name, path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
        temporary_name = ""
    except ReceiptAgentInstallerError:
        raise
    except OSError:
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_WRITE_FAILED")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if directory_fd >= 0:
            if temporary_name:
                try:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                except OSError:
                    pass
            try:
                os.close(directory_fd)
            except OSError:
                pass


def _restore(backups: list[_LiveBackup]) -> None:
    for backup in reversed(backups):
        path = _live_path(backup.destination)
        try:
            if backup.existed:
                _atomic_replace(path, content=backup.content, mode=backup.mode)
            else:
                try:
                    metadata = os.lstat(path)
                except FileNotFoundError:
                    continue
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != 0
                    or metadata.st_nlink != 1
                ):
                    _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_ROLLBACK_FAILED")
                os.unlink(path)
                parent_fd = os.open(path.parent, _directory_flags())
                try:
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
        except ReceiptAgentInstallerError:
            raise
        except OSError:
            _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_ROLLBACK_FAILED")


def _ensure_state_root() -> None:
    _ensure_live_directory(_live_path(FIXED_INSTALLATION_STATE_ROOT), mode=0o700)


def _locked_state() -> tuple[int, int]:
    _ensure_state_root()
    root = _live_path(FIXED_INSTALLATION_STATE_ROOT)
    root_fd = -1
    lock_fd = -1
    try:
        root_fd = os.open(root, _directory_flags())
        lock_fd = os.open(
            _live_path(FIXED_INSTALLATION_LOCK),
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        metadata = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_STATE_UNSAFE")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        return root_fd, lock_fd
    except ReceiptAgentInstallerError:
        raise
    except OSError:
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_STATE_UNSAFE")
    finally:
        if lock_fd < 0 and root_fd >= 0:
            try:
                os.close(root_fd)
            except OSError:
                pass


def _write_installation_attestation(stage: installation.VerifiedReceiptAgentInstallationStage) -> None:
    raw = installation.canonical_installation_attestation_bytes(
        stage=stage,
        installed_at=datetime.now(timezone.utc),
    )
    _atomic_replace(_live_path(FIXED_INSTALLATION_ATTESTATION), content=raw, mode=0o600)


def _apply(stage: installation.VerifiedReceiptAgentInstallationStage) -> None:
    # Staged validators run first, so no current host file changes when the
    # rendered sshd/sudoers syntax itself is invalid.
    _validate_assets_at(DEFAULT_RENDER_ROOT, stage)
    root_fd, lock_fd = _locked_state()
    try:
        _prepare_live_parents(stage)
        backups: list[_LiveBackup] = []
        try:
            for item in stage.files:
                backups.append(_read_existing_live(item.destination))
                _atomic_replace(_live_path(item.destination), content=item.content, mode=item.mode)
            _validate_assets_at(LIVE_ROOT, stage)
            backups.append(_read_existing_live(FIXED_INSTALLATION_ATTESTATION))
            _write_installation_attestation(stage)
        except Exception:
            try:
                _restore(backups)
            except Exception:
                _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_ROLLBACK_FAILED")
            raise
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(lock_fd)
        except OSError:
            pass
        try:
            os.close(root_fd)
        except OSError:
            pass


def _result(
    stage: installation.VerifiedReceiptAgentInstallationStage,
    *,
    applied: bool,
) -> bytes:
    return boundary.canonical_json_document(
        {
            "schema": installation.DEDICATED_HOST_PREFLIGHT_RECEIPT_AGENT_INSTALLATION_ATTESTATION_SCHEMA,
            "status": "installed-not-activated" if applied else "staged-verified-not-installed",
            "site_role": stage.config.site_role,
            "agent_release_sha": stage.config.agent_release_sha,
            "enabled": stage.config.enabled,
            "installation_sha256": stage.installation_sha256,
            "file_count": len(stage.files),
            "host_change_applied": applied,
            "service_reloaded": False,
            "writer_authorized": False,
            "promotion_authorized": False,
            "execution_authorized": False,
        },
        code="PREFLIGHT_RECEIPT_AGENT_INSTALL_RESULT_INVALID",
    ) + b"\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="atomically install only the fixed, already verified staged assets",
    )
    parser.add_argument(
        "--confirm-staged-install",
        action="store_true",
        help="required with --apply; default remains verification-only",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _require_root()
        if args.confirm_staged_install and not args.apply:
            _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_CONFIRMATION_INVALID")
        stage = _load_verified_stage()
        if args.apply:
            if not args.confirm_staged_install:
                _fail("PREFLIGHT_RECEIPT_AGENT_INSTALL_CONFIRMATION_REQUIRED")
            _apply(stage)
        output = _result(stage, applied=args.apply)
    except (
        ReceiptAgentInstallerError,
        boundary.ReceiptAgentBoundaryError,
        installation.DedicatedHostPreflightReceiptAgentInstallationError,
        OSError,
    ):
        return 2
    sys.stdout.buffer.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
