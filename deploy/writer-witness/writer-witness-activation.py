#!/usr/bin/env python3
"""Crash-safe Writer Witness release activation and rollback.

The provisioner stages immutable release/runtime trees separately.  This helper
owns the small mutable commit surface: the active generation pointer, legacy
compatibility pointers, and the host-global files consumed by systemd/Nginx.
An unfinished journal always means "roll back".  The boot recovery unit runs
this helper before either public daemon can start.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import uuid


class ActivationError(RuntimeError):
    """A fail-closed activation invariant was not satisfied."""


@dataclass(frozen=True)
class ManagedFile:
    candidate: str
    destination: str
    mode: int


MANAGED_FILES = (
    ManagedFile("runtime.env", "/etc/trading-bot-witness/runtime.env", 0o600),
    ManagedFile(
        "witness-ca.crt",
        "/root/writer-witness-client-material/witness-ca.crt",
        0o644,
    ),
    ManagedFile(
        "webapp-fi.env",
        "/root/writer-witness-client-material/webapp-fi.env",
        0o600,
    ),
    ManagedFile(
        "webapp-ir.env",
        "/root/writer-witness-client-material/webapp-ir.env",
        0o600,
    ),
    ManagedFile(
        "nginx-writer-witness",
        "/etc/nginx/sites-available/writer-witness",
        0o644,
    ),
    ManagedFile(
        "writer-witness.service",
        "/etc/systemd/system/writer-witness.service",
        0o644,
    ),
    ManagedFile(
        "writer-witness-backup.service",
        "/etc/systemd/system/writer-witness-backup.service",
        0o644,
    ),
    ManagedFile(
        "writer-witness-backup.timer",
        "/etc/systemd/system/writer-witness-backup.timer",
        0o644,
    ),
    ManagedFile(
        "writer-witness-offsite-backup.service",
        "/etc/systemd/system/writer-witness-offsite-backup.service",
        0o644,
    ),
    ManagedFile(
        "writer-witness-offsite-backup.timer",
        "/etc/systemd/system/writer-witness-offsite-backup.timer",
        0o644,
    ),
    ManagedFile(
        "writer-witness-backup",
        "/usr/local/sbin/writer-witness-backup",
        0o755,
    ),
    ManagedFile(
        "writer-witness-offsite-backup",
        "/usr/local/sbin/writer-witness-offsite-backup",
        0o755,
    ),
    ManagedFile(
        "writer-witness-s3-put",
        "/usr/local/sbin/writer-witness-s3-put",
        0o755,
    ),
    ManagedFile(
        "writer-witness-rotate-hmac",
        "/usr/local/sbin/writer-witness-rotate-hmac",
        0o755,
    ),
    ManagedFile(
        "writer-witness-live-restore",
        "/usr/local/sbin/writer-witness-live-restore",
        0o755,
    ),
    ManagedFile(
        "writer-witness-matrix-campaign",
        "/usr/local/sbin/writer-witness-matrix-campaign",
        0o755,
    ),
    ManagedFile(
        "writer-witness-matrix-host-faults",
        "/usr/local/sbin/writer-witness-matrix-host-faults",
        0o755,
    ),
    ManagedFile(
        "writer-witness-matrix-host-fault-state",
        "/usr/local/sbin/writer-witness-matrix-host-fault-state",
        0o755,
    ),
    ManagedFile(
        "writer-witness-state-manifest",
        "/usr/local/sbin/writer-witness-state-manifest",
        0o755,
    ),
    ManagedFile(
        "writer-witness-restore-drill",
        "/usr/local/sbin/writer-witness-restore-drill",
        0o755,
    ),
    ManagedFile(
        "writer-witness-smoke-client",
        "/usr/local/sbin/writer-witness-smoke-client",
        0o755,
    ),
)

# These files are mutated only by the credential finalization phase.  They are
# snapshotted with the activation journal, but are never populated from the
# candidates directory.  Keeping their pre-transaction bytes in the same
# durable operation makes "finalize, then commit" crash safe: an interruption
# before commit restores the bootstrap HMAC material and initialization marker
# together with the previous code/runtime generation.
ROLLBACK_ONLY_FILES = {
    "bootstrap-secrets": "/etc/trading-bot-witness/bootstrap-secrets.env",
    "credential-state": (
        "/var/lib/trading-bot-witness/activation-state/credential-state.json"
    ),
}

SPECIAL_PATHS = {
    "nginx_enabled": "/etc/nginx/sites-enabled/writer-witness",
    "nginx_default": "/etc/nginx/sites-enabled/default",
}

RELEASE_ID_RE = re.compile(r"[A-Za-z0-9._-]+")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
RECOVERY_HOST_TOOLCHAIN_VERIFIER = "recovery-host-toolchain-verifier.py"
RECOVERY_PACKAGE_LOCK_HOLDER = "recovery-package-lock-holder.py"
PACKAGE_MANAGER_LOCK_PATHS = (
    Path("/var/lib/dpkg/lock-frontend"),
    Path("/var/lib/dpkg/lock"),
    Path("/var/lib/apt/lists/lock"),
    Path("/var/cache/apt/archives/lock"),
)
SCHEMA = "writer_witness_activation_v3"
MANAGED_UNITS = (
    "nginx",
    "writer-witness.service",
    "writer-witness-backup.service",
    "writer-witness-backup.timer",
    "writer-witness-offsite-backup.service",
    "writer-witness-offsite-backup.timer",
)
UNIT_STATE_VALUE_RE = re.compile(r"[A-Za-z0-9._-]+")
UNIT_LOAD_STATES = frozenset({"loaded", "masked", "not-found"})
# A rollback intent is a durable desired state, not an observation of a
# transition.  Recording activating/reloading/deactivating/failed would make
# replay ambiguous and can duplicate a completed oneshot side effect.
UNIT_ACTIVE_STATES = frozenset({"active", "inactive"})
UNIT_FILE_STATES = frozenset(
    {
        "enabled",
        "enabled-runtime",
        "disabled",
        "masked",
        "masked-runtime",
        "static",
        "indirect",
        "generated",
        "alias",
        "linked",
        "linked-runtime",
        "transient",
        "not-found",
    }
)
DANGEROUS_RUNTIME_ENV = frozenset(
    {
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONINSPECT",
        "PYTHONUSERBASE",
        "BASH_ENV",
        "ENV",
        "SHELLOPTS",
        "BASHOPTS",
        "CDPATH",
        "GLOBIGNORE",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "LD_DEBUG",
        "LD_DEBUG_OUTPUT",
        "LD_PROFILE",
        "LD_SHOW_AUXV",
        "LD_BIND_NOW",
        "LD_BIND_NOT",
        "LD_ORIGIN_PATH",
        "LD_DYNAMIC_WEAK",
        "LD_HWCAP_MASK",
        "GLIBC_TUNABLES",
    }
)


def _assert_isolated_runtime(*, test_mode: bool) -> None:
    expected = Path("/usr/bin/python3.12")
    observed = Path(sys.executable).resolve(strict=True)
    if not test_mode and observed != expected:
        raise ActivationError("activation helper is not using the pinned system Python")
    if (
        not sys.flags.isolated
        or not sys.flags.no_site
        or not sys.flags.ignore_environment
        or not sys.flags.dont_write_bytecode
        or not sys.flags.utf8_mode
        or sys.pycache_prefix != "/dev/null"
    ):
        raise ActivationError("activation helper startup is not isolated")
    present = sorted(name for name in DANGEROUS_RUNTIME_ENV if os.environ.get(name))
    if present:
        raise ActivationError(
            "activation helper inherited forbidden runtime environment: "
            + ",".join(present)
        )


def _mapped(root: Path, absolute: str | Path) -> Path:
    value = Path(absolute)
    if not value.is_absolute():
        raise ActivationError(f"activation path is not absolute: {value}")
    return root / value.relative_to("/")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_directory(path: Path, mode: int = 0o700) -> None:
    created = False
    try:
        path.mkdir(mode=mode)
        created = True
    except FileExistsError:
        pass
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ActivationError(f"activation directory is unsafe: {path}")
    if metadata.st_uid != os.geteuid() or metadata.st_gid != os.getegid():
        raise ActivationError(f"activation directory ownership is unsafe: {path}")
    if stat.S_IMODE(metadata.st_mode) != mode:
        raise ActivationError(f"activation directory mode is unsafe: {path}")
    if created:
        _fsync_directory(path)
        _fsync_directory(path.parent)


def _read_regular(path: Path, maximum: int = 32 * 1024 * 1024) -> tuple[bytes, os.stat_result]:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > maximum
        ):
            raise ActivationError(f"activation file is unsafe: {path}")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise ActivationError(f"short activation file read: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_nlink,
            value.st_uid,
            value.st_gid,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
        if identity(before) != identity(after):
            raise ActivationError(f"activation file changed during read: {path}")
        return b"".join(chunks), before
    finally:
        os.close(descriptor)


def _atomic_write(
    destination: Path,
    payload: bytes,
    *,
    mode: int,
    uid: int,
    gid: int,
    token: str | None = None,
) -> None:
    _ensure_directory(destination.parent, stat.S_IMODE(destination.parent.lstat().st_mode))
    token_part = f"{token}-" if token is not None else ""
    temporary = destination.parent / (
        f".{destination.name}.activation-{token_part}{uuid.uuid4().hex}"
    )
    descriptor = os.open(
        temporary,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count < 1:
                raise ActivationError(f"short activation file write: {destination}")
            written += count
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, uid, gid)
        os.fsync(descriptor)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(descriptor)
    os.replace(temporary, destination)
    _fsync_directory(destination.parent)


def _atomic_json(path: Path, payload: dict[str, object], mode: int = 0o600) -> None:
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    _atomic_write(path, encoded, mode=mode, uid=os.geteuid(), gid=os.getegid())


def _atomic_symlink(destination: Path, target: str, *, token: str | None = None) -> None:
    token_part = f"{token}-" if token is not None else ""
    temporary = destination.parent / (
        f".{destination.name}.activation-{token_part}{uuid.uuid4().hex}"
    )
    os.symlink(target, temporary)
    os.replace(temporary, destination)
    _fsync_directory(destination.parent)


def _remove_path_entry(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
        raise ActivationError(f"refusing to unlink directory path entry: {path}")
    path.unlink()
    _fsync_directory(path.parent)


def _snapshot(path: Path, snapshot_path: Path) -> dict[str, object]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {"kind": "absent"}
    if stat.S_ISLNK(metadata.st_mode):
        return {"kind": "symlink", "target": os.readlink(path)}
    if not stat.S_ISREG(metadata.st_mode):
        raise ActivationError(f"managed activation path is not a regular file: {path}")
    payload, stable = _read_regular(path)
    _atomic_write(
        snapshot_path,
        payload,
        mode=0o600,
        uid=os.geteuid(),
        gid=os.getegid(),
    )
    return {
        "kind": "file",
        "mode": stat.S_IMODE(stable.st_mode),
        "uid": stable.st_uid,
        "gid": stable.st_gid,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "snapshot": snapshot_path.name,
    }


def _restore_snapshot(
    path: Path,
    snapshot: dict[str, object],
    snapshots: Path,
    *,
    token: str | None = None,
) -> None:
    kind = snapshot.get("kind")
    if kind == "absent":
        _remove_path_entry(path)
        return
    if kind == "symlink":
        target = snapshot.get("target")
        if not isinstance(target, str) or not target:
            raise ActivationError(f"invalid symlink snapshot for {path}")
        _atomic_symlink(path, target, token=token)
        return
    if kind != "file":
        raise ActivationError(f"invalid activation snapshot kind for {path}")
    snapshot_name = snapshot.get("snapshot")
    expected_sha = snapshot.get("sha256")
    if not isinstance(snapshot_name, str) or not isinstance(expected_sha, str):
        raise ActivationError(f"invalid file snapshot for {path}")
    payload, _ = _read_regular(snapshots / snapshot_name)
    if hashlib.sha256(payload).hexdigest() != expected_sha:
        raise ActivationError(f"activation snapshot hash mismatch for {path}")
    _atomic_write(
        path,
        payload,
        mode=int(snapshot["mode"]),
        uid=int(snapshot["uid"]),
        gid=int(snapshot["gid"]),
        token=token,
    )


def _path_descriptor(path: Path, *, allow_directory: bool = False) -> dict[str, object]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {"kind": "absent"}
    if stat.S_ISLNK(metadata.st_mode):
        resolved = path.resolve(strict=True)
        return {"kind": "symlink", "target": os.readlink(path), "resolved": str(resolved)}
    if allow_directory and stat.S_ISDIR(metadata.st_mode):
        return {
            "kind": "directory",
            "resolved": str(path.resolve(strict=True)),
            "dev": metadata.st_dev,
            "ino": metadata.st_ino,
        }
    raise ActivationError(f"activation pointer has unsafe type: {path}")


def _kill_after(label: str) -> None:
    if os.environ.get("WRITER_WITNESS_ACTIVATION_KILL_AFTER") != label:
        return
    if os.environ.get("WRITER_WITNESS_ACTIVATION_ALLOW_FAILPOINTS") != "1":
        raise ActivationError("activation failpoint requested without explicit test authorization")
    os.kill(os.getpid(), signal.SIGKILL)


class ActivationStore:
    def __init__(self, root: Path):
        self.root = root
        self.state = _mapped(root, "/var/lib/trading-bot-witness/activation-state")
        self.operations = self.state / "operations"
        self.history = self.state / "history"
        self.journal = self.state / "active.json"
        self.lock_path = self.state / ".activation.lock"

    def initialize(self) -> None:
        for path, mode in (
            (self.state, 0o700),
            (self.operations, 0o700),
            (self.history, 0o700),
        ):
            if not path.parent.exists():
                path.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
            _ensure_directory(path, mode)
        if not self.lock_path.exists():
            descriptor = os.open(
                self.lock_path,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            try:
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            _fsync_directory(self.state)
        metadata = self.lock_path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or metadata.st_gid != os.getegid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ActivationError("activation lock metadata is unsafe")

    def locked(self):
        self.initialize()
        descriptor = os.open(
            self.lock_path,
            os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )

        class Lock:
            def __enter__(inner_self):
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                return descriptor

            def __exit__(inner_self, exc_type, exc, traceback):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

        return Lock()

    def read_journal(self) -> dict[str, object] | None:
        try:
            payload, _ = _read_regular(self.journal, 4 * 1024 * 1024)
        except FileNotFoundError:
            return None
        parsed = json.loads(payload)
        if not isinstance(parsed, dict) or parsed.get("schema_version") != SCHEMA:
            raise ActivationError("activation journal is invalid")
        return parsed

    def write_journal(self, journal: dict[str, object]) -> None:
        _atomic_json(self.journal, journal)


def _operation_paths(store: ActivationStore, journal: dict[str, object]) -> tuple[Path, Path, Path]:
    operation_id = journal.get("operation_id")
    if not isinstance(operation_id, str) or not re.fullmatch(r"[0-9a-f]{32}", operation_id):
        raise ActivationError("activation operation id is invalid")
    operation = store.operations / operation_id
    return operation, operation / "candidates", operation / "snapshots"


def _cleanup_orphan_operations(store: ActivationStore) -> None:
    """Remove only helper-owned private operations when no journal owns one."""

    for path in store.operations.iterdir():
        metadata = path.lstat()
        if (
            not re.fullmatch(r"[0-9a-f]{32}", path.name)
            or not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_gid != os.getegid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise ActivationError(f"unowned activation operation entry: {path}")
        shutil.rmtree(path)
    _fsync_directory(store.operations)
    for path in store.state.glob(".active.json.activation-*"):
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_gid != os.getegid()
            or metadata.st_nlink != 1
        ):
            raise ActivationError(f"unowned activation journal temporary: {path}")
        path.unlink()
    _fsync_directory(store.state)


def _cleanup_owned_publication_temps(
    store: ActivationStore, journal: dict[str, object]
) -> None:
    operation_id = str(journal["operation_id"])
    destinations = [
        _mapped(store.root, item.destination) for item in MANAGED_FILES
    ] + [
        _mapped(store.root, value) for value in SPECIAL_PATHS.values()
    ] + [
        _mapped(store.root, "/opt/trading-bot-witness/active"),
        _mapped(store.root, "/srv/trading-bot-witness/current"),
        _mapped(store.root, "/opt/trading-bot-witness/venv"),
    ]
    touched_parents: set[Path] = set()
    for destination in destinations:
        prefix = f".{destination.name}.activation-{operation_id}-"
        for path in destination.parent.glob(f"{prefix}*"):
            metadata = path.lstat()
            if (
                not re.fullmatch(re.escape(prefix) + r"[0-9a-f]{32}", path.name)
                or metadata.st_uid != os.geteuid()
                or metadata.st_gid != os.getegid()
                or metadata.st_nlink != 1
                or not (
                    stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)
                )
            ):
                raise ActivationError(f"unsafe owned activation temporary: {path}")
            path.unlink()
            touched_parents.add(path.parent)
    for parent in sorted(touched_parents):
        _fsync_directory(parent)


def _validate_release_id(value: str) -> None:
    if not RELEASE_ID_RE.fullmatch(value):
        raise ActivationError("activation release id is invalid")


def _parse_unit_states(values: list[str] | None, *, required: bool) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for raw in values or ():
        parts = raw.split(":")
        if len(parts) != 4:
            raise ActivationError("activation unit state must contain four fields")
        unit, load_state, active_state, unit_file_state = parts
        if unit not in MANAGED_UNITS or unit in parsed:
            raise ActivationError(f"activation unit state is invalid or duplicated: {unit}")
        if any(UNIT_STATE_VALUE_RE.fullmatch(value) is None for value in parts):
            raise ActivationError(f"activation unit state contains an unsafe value: {unit}")
        if (
            load_state not in UNIT_LOAD_STATES
            or active_state not in UNIT_ACTIVE_STATES
            or unit_file_state not in UNIT_FILE_STATES
            or (load_state == "not-found" and unit_file_state != "not-found")
            or (load_state != "not-found" and unit_file_state == "not-found")
            or (
                load_state == "masked"
                and unit_file_state not in {"masked", "masked-runtime"}
            )
            or (load_state == "masked" and active_state != "inactive")
            or (
                unit
                in {
                    "writer-witness-backup.service",
                    "writer-witness-offsite-backup.service",
                }
                and active_state != "inactive"
            )
        ):
            raise ActivationError(f"activation unit state is unsupported: {unit}")
        parsed[unit] = {
            "load_state": load_state,
            "active_state": active_state,
            "unit_file_state": unit_file_state,
        }
    if required and set(parsed) != set(MANAGED_UNITS):
        missing = sorted(set(MANAGED_UNITS) - set(parsed))
        raise ActivationError(
            "activation requires a complete pre-publication unit-state snapshot: "
            + ",".join(missing)
        )
    if parsed and set(parsed) != set(MANAGED_UNITS):
        raise ActivationError("activation unit-state snapshot is incomplete")
    return parsed


def _canonical_expected_path(root: Path, raw: str, parent: str, release_id: str) -> Path:
    expected = _mapped(root, parent) / release_id
    value = _mapped(root, raw)
    if value != expected:
        raise ActivationError(f"activation path does not match release id: {raw}")
    return value


def _archive_and_remove(store: ActivationStore, journal: dict[str, object]) -> None:
    """Durably terminalize one activation without depending on its snapshots.

    History is published before the active journal is removed, and operation
    garbage collection happens only after the journal unlink is durable.  A
    recovery that observes a terminal journal can therefore repeat this
    function even when an earlier process already published history or removed
    the operation directory.  Conversely, a crash after the durable journal
    unlink leaves only an orphan operation, which the no-journal recovery path
    already owns and removes.
    """

    operation, _, _ = _operation_paths(store, journal)
    history_name = f"{journal['release_id']}-{journal['operation_id']}-{journal['phase']}.json"
    history_path = store.history / history_name
    expected_history = (
        json.dumps(journal, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    try:
        observed_history, _ = _read_regular(history_path, 4 * 1024 * 1024)
    except FileNotFoundError:
        _atomic_json(history_path, journal, mode=0o600)
    else:
        if observed_history != expected_history:
            raise ActivationError("activation terminal history conflicts with its journal")
    _kill_after("archive_history_published")
    _kill_after("archive_before_journal_unlink")
    store.journal.unlink()
    _kill_after("archive_journal_unlinked")
    _fsync_directory(store.state)
    _kill_after("archive_journal_fsynced")
    if operation.exists():
        if operation.is_symlink() or not operation.is_dir():
            raise ActivationError("activation operation directory is unsafe")
        shutil.rmtree(operation)
        _kill_after("archive_operation_removed")
        _fsync_directory(store.operations)
    _kill_after("archive_operation_fsynced")


def begin(store: ActivationStore, args: argparse.Namespace) -> None:
    _validate_release_id(args.release_id)
    if SHA256_RE.fullmatch(args.host_toolchain_inventory_sha256) is None:
        raise ActivationError("activation host toolchain binding is invalid")
    with store.locked():
        existing = store.read_journal()
        if existing is not None:
            if existing.get("phase") in {
                "committed",
                "rolled_back_pending_service_completion",
            }:
                raise ActivationError(
                    "activation requires service completion before a new begin"
                )
            else:
                raise ActivationError("unfinished activation exists; recover it before beginning")
        else:
            _cleanup_orphan_operations(store)

        release_dir = _canonical_expected_path(
            store.root, args.release_dir, "/srv/trading-bot-witness/releases", args.release_id
        )
        venv_dir = _canonical_expected_path(
            store.root, args.venv_dir, "/opt/trading-bot-witness/venvs", args.release_id
        )
        activation_dir = _canonical_expected_path(
            store.root,
            args.activation_dir,
            "/opt/trading-bot-witness/activations",
            args.release_id,
        )
        for planned in (release_dir, venv_dir, activation_dir):
            try:
                planned.lstat()
            except FileNotFoundError:
                continue
            raise ActivationError(f"release-owned path predates activation intent: {planned}")
        operation_id = uuid.uuid4().hex
        operation = store.operations / operation_id
        operation.mkdir(mode=0o700)
        candidates = operation / "candidates"
        snapshots = operation / "snapshots"
        candidates.mkdir(mode=0o700)
        snapshots.mkdir(mode=0o700)
        _fsync_directory(candidates)
        _fsync_directory(snapshots)
        _fsync_directory(operation)
        _fsync_directory(store.operations)

        recovery_helpers: dict[str, str] = {}
        for name, raw_source in (
            (RECOVERY_HOST_TOOLCHAIN_VERIFIER, args.host_toolchain_verifier),
            (RECOVERY_PACKAGE_LOCK_HOLDER, args.package_lock_helper),
        ):
            source = Path(raw_source)
            if not source.is_absolute():
                raise ActivationError("activation recovery helper path must be absolute")
            payload, metadata = _read_regular(source, 4 * 1024 * 1024)
            if (
                metadata.st_uid != os.geteuid()
                or metadata.st_gid != os.getegid()
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                raise ActivationError("activation recovery helper metadata is unsafe")
            _atomic_write(
                candidates / name,
                payload,
                mode=0o700,
                uid=os.geteuid(),
                gid=os.getegid(),
                token=operation_id,
            )
            recovery_helpers[name] = hashlib.sha256(payload).hexdigest()
        _fsync_directory(candidates)

        managed: dict[str, object] = {}
        for item in MANAGED_FILES:
            destination = _mapped(store.root, item.destination)
            managed[item.candidate] = _snapshot(
                destination, snapshots / f"{item.candidate}.snapshot"
            )
        special = {
            name: _snapshot(_mapped(store.root, path), snapshots / f"{name}.snapshot")
            for name, path in SPECIAL_PATHS.items()
        }
        rollback_only = {
            name: _snapshot(
                _mapped(store.root, destination),
                snapshots / f"rollback-only-{name}.snapshot",
            )
            for name, destination in ROLLBACK_ONLY_FILES.items()
        }

        active = _mapped(store.root, "/opt/trading-bot-witness/active")
        current = _mapped(store.root, "/srv/trading-bot-witness/current")
        compatibility_venv = _mapped(store.root, "/opt/trading-bot-witness/venv")
        active_descriptor = _path_descriptor(active)
        current_descriptor = _path_descriptor(current)
        venv_descriptor = _path_descriptor(compatibility_venv, allow_directory=True)
        active_absent = active_descriptor["kind"] == "absent"
        fresh_install = (
            active_absent
            and current_descriptor["kind"] == "absent"
            and venv_descriptor["kind"] == "absent"
        )
        initial_migration = active_absent and not fresh_install
        if initial_migration:
            if current_descriptor["kind"] != "symlink":
                raise ActivationError("initial migration requires the legacy current symlink")
            if venv_descriptor["kind"] not in {"symlink", "directory"}:
                raise ActivationError("initial migration requires the legacy venv")
            legacy_activation = _mapped(
                store.root,
                f"/opt/trading-bot-witness/activations/legacy-before-{args.release_id}",
            )
            legacy_paths = [legacy_activation]
            if venv_descriptor["kind"] == "directory":
                legacy_paths.append(
                    _mapped(
                        store.root,
                        f"/opt/trading-bot-witness/venvs/legacy-before-{args.release_id}",
                    )
                )
            for planned in legacy_paths:
                try:
                    planned.lstat()
                except FileNotFoundError:
                    continue
                raise ActivationError(
                    f"legacy migration path predates activation intent: {planned}"
                )
        elif not fresh_install:
            resolved = Path(str(active_descriptor.get("resolved", "")))
            expected_parent = _mapped(store.root, "/opt/trading-bot-witness/activations")
            if resolved.parent != expected_parent:
                raise ActivationError("previous active target is outside the activation root")
            expected_current = (resolved / "release").resolve(strict=True)
            expected_venv = (resolved / "venv").resolve(strict=True)
            if (
                current_descriptor.get("kind") != "symlink"
                or Path(str(current_descriptor.get("resolved", ""))) != expected_current
            ):
                raise ActivationError("current compatibility pointer is not the active release")
            if (
                venv_descriptor.get("kind") != "symlink"
                or Path(str(venv_descriptor.get("resolved", ""))) != expected_venv
            ):
                raise ActivationError("venv compatibility pointer is not the active runtime")

        journal: dict[str, object] = {
            "schema_version": SCHEMA,
            "operation_id": operation_id,
            "release_id": args.release_id,
            "phase": "prepared",
            "initial_migration": initial_migration,
            "fresh_install": fresh_install,
            "initial_normalization_started": False,
            "release_dir": str(release_dir.relative_to(store.root)),
            "venv_dir": str(venv_dir.relative_to(store.root)),
            "activation_dir": str(activation_dir.relative_to(store.root)),
            "previous_active": active_descriptor,
            "previous_current": current_descriptor,
            "previous_venv": venv_descriptor,
            "managed": managed,
            "special": special,
            "rollback_only": rollback_only,
            # Unit intent is finalized only immediately before the first
            # systemd mutation.  The long release/runtime staging interval is
            # intentionally outside that service-state snapshot.
            "unit_intent_finalized": False,
            "unit_states": {},
            "host_toolchain_inventory_sha256": (
                args.host_toolchain_inventory_sha256
            ),
            "recovery_helpers": recovery_helpers,
        }
        store.write_journal(journal)
        _kill_after("begin_journal")
        print(str(candidates))


def record_unit_intent(store: ActivationStore, args: argparse.Namespace) -> None:
    """Durably bind stable service intent immediately before quiescence."""

    with store.locked():
        journal = store.read_journal()
        if journal is None or journal.get("release_id") != args.release_id:
            raise ActivationError("matching prepared activation journal is missing")
        if journal.get("phase") != "prepared":
            raise ActivationError("activation is no longer eligible for unit intent")
        if journal.get("unit_intent_finalized") is not False:
            raise ActivationError("activation unit intent is already finalized")
        _require_host_toolchain_binding(journal, args.host_toolchain_inventory_sha256)
        journal["unit_states"] = _parse_unit_states(args.unit_state, required=True)
        journal["unit_intent_finalized"] = True
        store.write_journal(journal)
        _kill_after("unit_intent_recorded")
        print("activation_unit_intent_recorded=yes")


def _journal_absolute(store: ActivationStore, journal: dict[str, object], key: str) -> Path:
    raw = journal.get(key)
    if not isinstance(raw, str) or raw.startswith("/") or ".." in Path(raw).parts:
        raise ActivationError(f"activation journal path is invalid: {key}")
    return store.root / raw


def _require_host_toolchain_binding(
    journal: dict[str, object], observed_sha256: str
) -> None:
    if (
        SHA256_RE.fullmatch(observed_sha256) is None
        or journal.get("host_toolchain_inventory_sha256") != observed_sha256
    ):
        raise ActivationError("activation host toolchain differs from its journal binding")


def _ensure_symlink_pair(directory: Path, release: Path, venv: Path) -> None:
    if directory.exists():
        if directory.is_symlink() or not directory.is_dir():
            raise ActivationError(f"legacy activation path is unsafe: {directory}")
    else:
        directory.mkdir(mode=0o755)
    for name, target in (("release", release), ("venv", venv)):
        path = directory / name
        if path.is_symlink():
            if path.resolve(strict=True) != target.resolve(strict=True):
                raise ActivationError(f"legacy activation target drifted: {path}")
        elif path.exists():
            raise ActivationError(f"legacy activation member is unsafe: {path}")
        else:
            os.symlink(target, path)
    _fsync_directory(directory)
    _fsync_directory(directory.parent)


def _normalize_initial(store: ActivationStore, journal: dict[str, object]) -> Path:
    journal["initial_normalization_started"] = True
    release_id = str(journal["release_id"])
    legacy_activation = _mapped(
        store.root, f"/opt/trading-bot-witness/activations/legacy-before-{release_id}"
    )
    old_release = Path(str(journal["previous_current"]["resolved"]))
    old_venv_descriptor = journal["previous_venv"]
    compatibility_venv = _mapped(store.root, "/opt/trading-bot-witness/venv")
    if old_venv_descriptor["kind"] == "directory":
        legacy_venv = _mapped(
            store.root, f"/opt/trading-bot-witness/venvs/legacy-before-{release_id}"
        )
        journal["legacy_venv"] = str(legacy_venv.relative_to(store.root))
    else:
        legacy_venv = Path(str(old_venv_descriptor["resolved"]))
        journal["legacy_venv"] = None
    journal["legacy_activation"] = str(legacy_activation.relative_to(store.root))
    store.write_journal(journal)
    operation_id = str(journal["operation_id"])

    if old_venv_descriptor["kind"] == "directory":
        if legacy_venv.exists():
            if compatibility_venv.exists() and not compatibility_venv.is_symlink():
                raise ActivationError("both legacy venv locations exist")
        else:
            if compatibility_venv.is_symlink() or not compatibility_venv.is_dir():
                raise ActivationError("legacy venv disappeared before normalization")
            os.rename(compatibility_venv, legacy_venv)
            _fsync_directory(compatibility_venv.parent)
            _fsync_directory(legacy_venv.parent)
        _kill_after("legacy_venv_moved")

    _ensure_symlink_pair(legacy_activation, old_release, legacy_venv)
    _kill_after("legacy_activation_fsynced")
    _atomic_symlink(
        _mapped(store.root, "/opt/trading-bot-witness/active"),
        str(legacy_activation),
        token=operation_id,
    )
    _atomic_symlink(
        _mapped(store.root, "/srv/trading-bot-witness/current"),
        str(_mapped(store.root, "/opt/trading-bot-witness/active/release")),
        token=operation_id,
    )
    _atomic_symlink(
        compatibility_venv,
        str(_mapped(store.root, "/opt/trading-bot-witness/active/venv")),
        token=operation_id,
    )
    _kill_after("legacy_active_switched")
    journal["previous_activation"] = str(legacy_activation.relative_to(store.root))
    store.write_journal(journal)
    return legacy_activation


def _candidate_bindings(candidates: Path) -> dict[str, dict[str, object]]:
    bindings: dict[str, dict[str, object]] = {}
    for item in MANAGED_FILES:
        payload, metadata = _read_regular(candidates / item.candidate)
        if (
            metadata.st_uid != os.geteuid()
            or metadata.st_gid != os.getegid()
            or stat.S_IMODE(metadata.st_mode) != item.mode
        ):
            raise ActivationError(f"candidate metadata is unsafe: {item.candidate}")
        bindings[item.candidate] = {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
            "mode": item.mode,
        }
    return bindings


def _install_candidates(store: ActivationStore, journal: dict[str, object]) -> None:
    _, candidates, _ = _operation_paths(store, journal)
    operation_id = str(journal["operation_id"])
    bindings = journal.get("candidate_bindings")
    if not isinstance(bindings, dict) or set(bindings) != {
        item.candidate for item in MANAGED_FILES
    }:
        raise ActivationError("activation candidate bindings are incomplete")
    for item in MANAGED_FILES:
        payload, metadata = _read_regular(candidates / item.candidate)
        expected = bindings.get(item.candidate)
        if (
            metadata.st_uid != os.geteuid()
            or metadata.st_gid != os.getegid()
            or stat.S_IMODE(metadata.st_mode) != item.mode
            or not isinstance(expected, dict)
            or expected.get("sha256") != hashlib.sha256(payload).hexdigest()
            or expected.get("size") != len(payload)
            or expected.get("mode") != item.mode
        ):
            raise ActivationError(f"candidate ownership is unsafe: {item.candidate}")
        _atomic_write(
            _mapped(store.root, item.destination),
            payload,
            mode=item.mode,
            uid=os.geteuid(),
            gid=os.getegid(),
            token=operation_id,
        )
        _kill_after(f"candidate_published_{item.candidate}")
    _atomic_symlink(
        _mapped(store.root, SPECIAL_PATHS["nginx_enabled"]),
        str(_mapped(store.root, "/etc/nginx/sites-available/writer-witness")),
        token=operation_id,
    )
    _kill_after("nginx_enabled_published")
    _remove_path_entry(_mapped(store.root, SPECIAL_PATHS["nginx_default"]))
    _kill_after("nginx_default_removed")


def publish(store: ActivationStore, args: argparse.Namespace) -> None:
    with store.locked():
        journal = store.read_journal()
        if journal is None or journal.get("release_id") != args.release_id:
            raise ActivationError("matching prepared activation journal is missing")
        if journal.get("phase") != "prepared":
            raise ActivationError("activation journal is not prepared")
        if journal.get("unit_intent_finalized") is not True:
            raise ActivationError("activation unit intent is not finalized")
        _parse_unit_states(
            [
                f"{unit}:{state['load_state']}:{state['active_state']}:{state['unit_file_state']}"
                for unit, state in dict(journal.get("unit_states", {})).items()
                if isinstance(state, dict)
            ],
            required=True,
        )
        release_dir = _journal_absolute(store, journal, "release_dir")
        venv_dir = _journal_absolute(store, journal, "venv_dir")
        activation_dir = _journal_absolute(store, journal, "activation_dir")
        if not release_dir.is_dir() or release_dir.is_symlink():
            raise ActivationError("prepared release directory is missing")
        if not venv_dir.is_dir() or venv_dir.is_symlink():
            raise ActivationError("prepared venv directory is missing")
        if not activation_dir.is_dir() or activation_dir.is_symlink():
            raise ActivationError("prepared activation directory is missing")
        if (activation_dir / "release").resolve(strict=True) != release_dir.resolve(strict=True):
            raise ActivationError("prepared activation release target drifted")
        if (activation_dir / "venv").resolve(strict=True) != venv_dir.resolve(strict=True):
            raise ActivationError("prepared activation venv target drifted")
        _, candidates, _ = _operation_paths(store, journal)
        journal["candidate_bindings"] = _candidate_bindings(candidates)
        journal["phase"] = "publishing"
        store.write_journal(journal)
        _kill_after("candidates_bound")
        if bool(journal["initial_migration"]):
            previous = _normalize_initial(store, journal)
        elif bool(journal.get("fresh_install")):
            previous = None
        else:
            previous = Path(str(journal["previous_active"]["resolved"]))
            active = _mapped(store.root, "/opt/trading-bot-witness/active")
            if not active.is_symlink() or active.resolve(strict=True) != previous.resolve(strict=True):
                raise ActivationError("active generation changed after activation preparation")
            journal["previous_activation"] = str(previous.relative_to(store.root))
            store.write_journal(journal)

        _install_candidates(store, journal)
        _kill_after("candidates_published")
        _atomic_symlink(
            _mapped(store.root, "/opt/trading-bot-witness/active"),
            str(activation_dir),
            token=str(journal["operation_id"]),
        )
        _kill_after("new_active_switched")
        _atomic_symlink(
            _mapped(store.root, "/srv/trading-bot-witness/current"),
            str(_mapped(store.root, "/opt/trading-bot-witness/active/release")),
            token=str(journal["operation_id"]),
        )
        _atomic_symlink(
            _mapped(store.root, "/opt/trading-bot-witness/venv"),
            str(_mapped(store.root, "/opt/trading-bot-witness/active/venv")),
            token=str(journal["operation_id"]),
        )
        journal["phase"] = "activated"
        journal["previous_activation"] = (
            str(previous.relative_to(store.root)) if previous is not None else None
        )
        store.write_journal(journal)
        _kill_after("activation_published")
        print("activation_published=yes")


def _restore_managed(store: ActivationStore, journal: dict[str, object]) -> None:
    _, _, snapshots = _operation_paths(store, journal)
    operation_id = str(journal["operation_id"])
    managed = journal.get("managed")
    special = journal.get("special")
    if not isinstance(managed, dict) or not isinstance(special, dict):
        raise ActivationError("activation snapshots are invalid")
    for item in MANAGED_FILES:
        snapshot = managed.get(item.candidate)
        if not isinstance(snapshot, dict):
            raise ActivationError(f"activation snapshot is missing: {item.candidate}")
        _restore_snapshot(
            _mapped(store.root, item.destination),
            snapshot,
            snapshots,
            token=operation_id,
        )
    for name, destination in SPECIAL_PATHS.items():
        snapshot = special.get(name)
        if not isinstance(snapshot, dict):
            raise ActivationError(f"activation special snapshot is missing: {name}")
        _restore_snapshot(
            _mapped(store.root, destination),
            snapshot,
            snapshots,
            token=operation_id,
        )


def _restore_rollback_only(store: ActivationStore, journal: dict[str, object]) -> None:
    _, _, snapshots = _operation_paths(store, journal)
    operation_id = str(journal["operation_id"])
    rollback_only = journal.get("rollback_only")
    if not isinstance(rollback_only, dict):
        raise ActivationError("activation rollback-only snapshots are invalid")
    for name, destination in ROLLBACK_ONLY_FILES.items():
        snapshot = rollback_only.get(name)
        if not isinstance(snapshot, dict):
            raise ActivationError(f"activation rollback-only snapshot is missing: {name}")
        _restore_snapshot(
            _mapped(store.root, destination),
            snapshot,
            snapshots,
            token=operation_id,
        )


def _safe_remove_tree(path: Path, expected_parent: Path, active: Path) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    if path.parent != expected_parent or path.is_symlink() or not path.is_dir():
        raise ActivationError(f"refusing unsafe activation cleanup: {path}")
    if active.is_symlink() and active.resolve(strict=True) == path.resolve(strict=True):
        raise ActivationError(f"refusing to remove the active generation: {path}")
    shutil.rmtree(path)
    _fsync_directory(expected_parent)


def _rollback(store: ActivationStore, journal: dict[str, object]) -> None:
    phase = journal.get("phase")
    if phase == "rolled_back_without_service_changes":
        # This terminal phase no longer needs snapshots.  An earlier process
        # may have been killed at any archive substep, including after history
        # publication or operation garbage collection.
        _archive_and_remove(store, journal)
        return
    if phase == "rolled_back_pending_service_completion":
        _cleanup_owned_publication_temps(store, journal)
        active = _mapped(store.root, "/opt/trading-bot-witness/active")
        _safe_remove_tree(
            _journal_absolute(store, journal, "activation_dir"),
            _mapped(store.root, "/opt/trading-bot-witness/activations"),
            active,
        )
        _safe_remove_tree(
            _journal_absolute(store, journal, "venv_dir"),
            _mapped(store.root, "/opt/trading-bot-witness/venvs"),
            active,
        )
        _safe_remove_tree(
            _journal_absolute(store, journal, "release_dir"),
            _mapped(store.root, "/srv/trading-bot-witness/releases"),
            active,
        )
        if journal.get("unit_intent_finalized") is not True:
            journal["phase"] = "rolled_back_without_service_changes"
            store.write_journal(journal)
            _kill_after("rollback_without_service_terminal_recorded")
            _archive_and_remove(store, journal)
        return
    if phase == "committed":
        raise ActivationError("committed activation cannot be rolled back automatically")
    _cleanup_owned_publication_temps(store, journal)
    operation_id = str(journal["operation_id"])
    active = _mapped(store.root, "/opt/trading-bot-witness/active")
    normalization_started = bool(journal.get("initial_normalization_started"))
    if bool(journal.get("fresh_install")):
        for destination in (
            "/opt/trading-bot-witness/active",
            "/srv/trading-bot-witness/current",
            "/opt/trading-bot-witness/venv",
        ):
            _remove_path_entry(_mapped(store.root, destination))
    elif bool(journal.get("initial_migration")) and normalization_started:
        previous_raw = journal.get("previous_activation") or journal.get("legacy_activation")
        if not isinstance(previous_raw, str):
            raise ActivationError("legacy activation recovery target is missing")
        previous = store.root / previous_raw
        old_release = Path(str(journal["previous_current"]["resolved"]))
        old_venv_descriptor = journal["previous_venv"]
        if old_venv_descriptor["kind"] == "directory":
            legacy_raw = journal.get("legacy_venv")
            if not isinstance(legacy_raw, str):
                raise ActivationError("legacy venv recovery target is missing")
            old_venv = store.root / legacy_raw
            original = _mapped(store.root, "/opt/trading-bot-witness/venv")
            if not old_venv.exists():
                if original.is_dir() and not original.is_symlink():
                    os.rename(original, old_venv)
                    _fsync_directory(original.parent)
                    _fsync_directory(old_venv.parent)
                else:
                    raise ActivationError("legacy venv cannot be recovered")
        else:
            old_venv = Path(str(old_venv_descriptor["resolved"]))
        _ensure_symlink_pair(previous, old_release, old_venv)
        _atomic_symlink(active, str(previous), token=operation_id)
        _atomic_symlink(
            _mapped(store.root, "/srv/trading-bot-witness/current"),
            str(_mapped(store.root, "/opt/trading-bot-witness/active/release")),
            token=operation_id,
        )
        _atomic_symlink(
            _mapped(store.root, "/opt/trading-bot-witness/venv"),
            str(_mapped(store.root, "/opt/trading-bot-witness/active/venv")),
            token=operation_id,
        )
    elif bool(journal.get("initial_migration")):
        _restore_snapshot(
            active,
            journal["previous_active"],
            _operation_paths(store, journal)[2],
            token=operation_id,
        )
    else:
        previous = Path(str(journal["previous_active"]["resolved"]))
        _atomic_symlink(active, str(previous), token=operation_id)
        _atomic_symlink(
            _mapped(store.root, "/srv/trading-bot-witness/current"),
            str(_mapped(store.root, "/opt/trading-bot-witness/active/release")),
            token=operation_id,
        )
        _atomic_symlink(
            _mapped(store.root, "/opt/trading-bot-witness/venv"),
            str(_mapped(store.root, "/opt/trading-bot-witness/active/venv")),
            token=operation_id,
        )
    _kill_after("rollback_active_switched")
    _restore_managed(store, journal)
    _restore_rollback_only(store, journal)
    _cleanup_owned_publication_temps(store, journal)
    journal["phase"] = "rolled_back_pending_service_completion"
    store.write_journal(journal)
    _kill_after("rollback_restored")

    release_dir = _journal_absolute(store, journal, "release_dir")
    venv_dir = _journal_absolute(store, journal, "venv_dir")
    activation_dir = _journal_absolute(store, journal, "activation_dir")
    _safe_remove_tree(
        activation_dir,
        _mapped(store.root, "/opt/trading-bot-witness/activations"),
        active,
    )
    _safe_remove_tree(
        venv_dir, _mapped(store.root, "/opt/trading-bot-witness/venvs"), active
    )
    _safe_remove_tree(
        release_dir, _mapped(store.root, "/srv/trading-bot-witness/releases"), active
    )
    if journal.get("unit_intent_finalized") is not True:
        journal["phase"] = "rolled_back_without_service_changes"
        store.write_journal(journal)
        _kill_after("rollback_without_service_terminal_recorded")
        _archive_and_remove(store, journal)


def recover(store: ActivationStore, _args: argparse.Namespace) -> None:
    with store.locked():
        journal = store.read_journal()
        if journal is None:
            _cleanup_orphan_operations(store)
            print("activation_recovered=no")
            return
        if journal.get("phase") == "committed":
            # The generation and credentials are durable, but the surrounding
            # service supervisor may have died before restarting public
            # daemons.  Keep the journal until the watchdog (or provisioner)
            # confirms service completion explicitly.
            print("activation_recovered=committed-pending-service-completion")
            return
        _rollback(store, journal)
        journal = store.read_journal()
        if journal is None:
            print("activation_recovered=rolled-back-without-service-changes")
            return
        if (
            journal is None
            or journal.get("phase") != "rolled_back_pending_service_completion"
        ):
            raise ActivationError("activation rollback did not reach its durable terminal phase")
        print("activation_recovered=rolled-back-pending-service-completion")


def _validated_recovery_binding(
    store: ActivationStore, journal: dict[str, object]
) -> tuple[str, str, Path]:
    release_id = journal.get("release_id")
    digest = journal.get("host_toolchain_inventory_sha256")
    if not isinstance(release_id, str) or not isinstance(digest, str):
        raise ActivationError("activation recovery binding is invalid")
    _validate_release_id(release_id)
    if SHA256_RE.fullmatch(digest) is None:
        raise ActivationError("activation recovery toolchain digest is invalid")
    _, candidates, _ = _operation_paths(store, journal)
    helper_hashes = journal.get("recovery_helpers")
    if not isinstance(helper_hashes, dict) or set(helper_hashes) != {
        RECOVERY_HOST_TOOLCHAIN_VERIFIER,
        RECOVERY_PACKAGE_LOCK_HOLDER,
    }:
        raise ActivationError("activation recovery helper binding is incomplete")
    for name, expected in helper_hashes.items():
        if not isinstance(expected, str) or SHA256_RE.fullmatch(expected) is None:
            raise ActivationError("activation recovery helper digest is invalid")
        payload, metadata = _read_regular(candidates / name, 4 * 1024 * 1024)
        if (
            metadata.st_uid != os.geteuid()
            or metadata.st_gid != os.getegid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or hashlib.sha256(payload).hexdigest() != expected
        ):
            raise ActivationError("activation recovery helper differs from its journal")
    return release_id, digest, candidates


def _attest_boot_recovery_toolchain(
    store: ActivationStore, journal: dict[str, object]
) -> list[int]:
    """Exclude apt/dpkg and attest before boot-time rollback mutation."""

    descriptors: list[int] = []
    try:
        for path in PACKAGE_MANAGER_LOCK_PATHS:
            descriptor = os.open(
                path,
                os.O_RDWR
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != 0
                or metadata.st_gid != 0
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                os.close(descriptor)
                raise ActivationError("boot recovery package lock metadata is unsafe")
            try:
                fcntl.lockf(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                os.close(descriptor)
                raise
            descriptors.append(descriptor)
        _, digest, candidates = _validated_recovery_binding(store, journal)
        completed = subprocess.run(
            [
                "/usr/bin/python3.12",
                "-I",
                "-S",
                "-B",
                "-X",
                "utf8",
                "-X",
                "pycache_prefix=/dev/null",
                str(candidates / RECOVERY_HOST_TOOLCHAIN_VERIFIER),
                "--expected-inventory-sha256",
                digest,
            ],
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode != 0 or completed.stdout.strip() != "host_toolchain_attested=yes":
            raise ActivationError("boot recovery host toolchain attestation failed")
        return descriptors
    except BaseException:
        for descriptor in reversed(descriptors):
            try:
                fcntl.lockf(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        raise


def recover_boot(store: ActivationStore, args: argparse.Namespace) -> None:
    """Recover at boot while excluding both provision and HMAC rotation."""

    store.initialize()
    provision_lock = store.state / ".provision.lock"
    rotation_lock = _mapped(
        store.root,
        "/var/lib/trading-bot-witness/hmac-rotation/.runtime.lock",
    )
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    provision_descriptor = os.open(provision_lock, flags, 0o600)
    rotation_descriptor = -1
    package_descriptors: list[int] = []
    try:
        metadata = os.fstat(provision_descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_gid != os.getegid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise ActivationError("activation provision lock metadata is unsafe")
        try:
            fcntl.flock(provision_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            print("activation_recovered=deferred-live-provision")
            return
        rotation_descriptor = os.open(
            rotation_lock,
            os.O_RDWR
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        rotation_metadata = os.fstat(rotation_descriptor)
        if (
            not stat.S_ISREG(rotation_metadata.st_mode)
            or rotation_metadata.st_uid != os.geteuid()
            or rotation_metadata.st_gid != os.getegid()
            or stat.S_IMODE(rotation_metadata.st_mode) != 0o600
            or rotation_metadata.st_nlink != 1
        ):
            raise ActivationError("HMAC rotation lock metadata is unsafe")
        try:
            fcntl.flock(rotation_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            print("activation_recovered=deferred-live-rotation")
            return
        journal = store.read_journal()
        if (
            journal is not None
            and journal.get("phase") != "rolled_back_without_service_changes"
            and os.environ.get("WRITER_WITNESS_ACTIVATION_TEST_MODE") != "1"
        ):
            package_descriptors = _attest_boot_recovery_toolchain(store, journal)
        recover(store, args)
        journal = store.read_journal()
        if (
            journal is not None
            and journal.get("phase") == "rolled_back_pending_service_completion"
        ):
            raise ActivationError(
                "rolled-back activation is pending exact service restoration"
            )
    finally:
        for descriptor in reversed(package_descriptors):
            try:
                fcntl.lockf(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)
        if rotation_descriptor >= 0:
            try:
                fcntl.flock(rotation_descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(rotation_descriptor)
        try:
            fcntl.flock(provision_descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(provision_descriptor)


def commit(store: ActivationStore, args: argparse.Namespace) -> None:
    with store.locked():
        journal = store.read_journal()
        if journal is None or journal.get("release_id") != args.release_id:
            raise ActivationError("matching activated journal is missing")
        if journal.get("phase") != "activated":
            raise ActivationError("activation is not ready to commit")
        _require_host_toolchain_binding(journal, args.host_toolchain_inventory_sha256)
        activation_dir = _journal_absolute(store, journal, "activation_dir")
        active = _mapped(store.root, "/opt/trading-bot-witness/active")
        if not active.is_symlink() or active.resolve(strict=True) != activation_dir.resolve(strict=True):
            raise ActivationError("active generation changed before commit")
        _cleanup_owned_publication_temps(store, journal)
        journal["phase"] = "committed"
        store.write_journal(journal)
        _kill_after("commit_recorded")
        print("activation_committed=pending-service-completion")


def complete(store: ActivationStore, args: argparse.Namespace) -> None:
    """Archive a committed journal only after public services are healthy."""

    with store.locked():
        journal = store.read_journal()
        if journal is None or journal.get("release_id") != args.release_id:
            raise ActivationError("matching committed activation journal is missing")
        if journal.get("phase") != "committed":
            raise ActivationError("activation is not committed")
        _require_host_toolchain_binding(journal, args.host_toolchain_inventory_sha256)
        activation_dir = _journal_absolute(store, journal, "activation_dir")
        active = _mapped(store.root, "/opt/trading-bot-witness/active")
        if not active.is_symlink() or active.resolve(strict=True) != activation_dir.resolve(strict=True):
            raise ActivationError("active generation changed before completion")
        _archive_and_remove(store, journal)
        print("activation_completed=yes")


def rollback_unit_intent(store: ActivationStore, args: argparse.Namespace) -> None:
    """Return one fixed unit's exact pre-publication service state."""

    if args.unit not in MANAGED_UNITS:
        raise ActivationError("rollback unit is outside the managed unit set")
    with store.locked():
        journal = store.read_journal()
        if (
            journal is None
            or journal.get("phase") != "rolled_back_pending_service_completion"
            or journal.get("unit_intent_finalized") is not True
        ):
            raise ActivationError("rolled-back activation journal is missing")
        unit_states = journal.get("unit_states")
        if not isinstance(unit_states, dict):
            raise ActivationError("activation unit-state snapshot is invalid")
        state = unit_states.get(args.unit)
        if not isinstance(state, dict) or set(state) != {
            "load_state",
            "active_state",
            "unit_file_state",
        }:
            raise ActivationError(f"activation unit-state snapshot is missing: {args.unit}")
        values = [
            state[field]
            for field in ("load_state", "active_state", "unit_file_state")
        ]
        if any(
            not isinstance(value, str)
            or UNIT_STATE_VALUE_RE.fullmatch(value) is None
            for value in values
        ):
            raise ActivationError(f"activation unit-state snapshot is unsafe: {args.unit}")
        print(":".join(values))


def complete_rollback(store: ActivationStore, args: argparse.Namespace) -> None:
    """Archive rollback intent only after exact unit-state restoration."""

    with store.locked():
        journal = store.read_journal()
        if journal is None or journal.get("release_id") != args.release_id:
            raise ActivationError("matching rolled-back activation journal is missing")
        if journal.get("phase") != "rolled_back_pending_service_completion":
            raise ActivationError("activation rollback is not pending service completion")
        _require_host_toolchain_binding(journal, args.host_toolchain_inventory_sha256)
        unit_states = journal.get("unit_states")
        if not isinstance(unit_states, dict) or set(unit_states) != set(MANAGED_UNITS):
            raise ActivationError("activation unit-state snapshot is incomplete")
        observed = _parse_unit_states(args.unit_state, required=True)
        if observed != unit_states:
            raise ActivationError("activation rollback unit state does not match intent")
        _archive_and_remove(store, journal)
        print("activation_rollback_completed=yes")


def pending_release_id(store: ActivationStore, args: argparse.Namespace) -> None:
    with store.locked():
        journal = store.read_journal()
        if journal is None or journal.get("phase") != args.phase:
            raise ActivationError("matching pending activation journal is missing")
        release_id = journal.get("release_id")
        if not isinstance(release_id, str):
            raise ActivationError("activation release id is invalid")
        _validate_release_id(release_id)
        print(release_id)


def pending_toolchain_binding(store: ActivationStore, _args: argparse.Namespace) -> None:
    """Return one validated recovery capability without exposing mutable JSON."""

    with store.locked():
        journal = store.read_journal()
        if journal is None:
            print("none")
            return
        if journal.get("phase") == "rolled_back_without_service_changes":
            print("terminal")
            return
        release_id, digest, candidates = _validated_recovery_binding(store, journal)
        print(f"{release_id}|{digest}|{candidates}")


def candidate_dir(store: ActivationStore, args: argparse.Namespace) -> None:
    with store.locked():
        journal = store.read_journal()
        if journal is None or journal.get("release_id") != args.release_id:
            raise ActivationError("matching activation journal is missing")
        _, candidates, _ = _operation_paths(store, journal)
        print(candidates)


def active_release_id(store: ActivationStore, _args: argparse.Namespace) -> None:
    with store.locked():
        journal = store.read_journal()
        if journal is None or journal.get("phase") != "committed":
            raise ActivationError("committed activation journal is missing")
        release_id = journal.get("release_id")
        if not isinstance(release_id, str):
            raise ActivationError("activation release id is invalid")
        _validate_release_id(release_id)
        print(release_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="/")
    subparsers = parser.add_subparsers(dest="command", required=True)
    begin_parser = subparsers.add_parser("begin")
    begin_parser.add_argument("--release-id", required=True)
    begin_parser.add_argument("--release-dir", required=True)
    begin_parser.add_argument("--venv-dir", required=True)
    begin_parser.add_argument("--activation-dir", required=True)
    begin_parser.add_argument("--host-toolchain-inventory-sha256", required=True)
    begin_parser.add_argument("--host-toolchain-verifier", required=True)
    begin_parser.add_argument("--package-lock-helper", required=True)
    intent_parser = subparsers.add_parser("record-unit-intent")
    intent_parser.add_argument("--release-id", required=True)
    intent_parser.add_argument("--unit-state", action="append", required=True)
    intent_parser.add_argument("--host-toolchain-inventory-sha256", required=True)
    for name in ("publish", "commit", "complete", "candidate-dir"):
        child = subparsers.add_parser(name)
        child.add_argument("--release-id", required=True)
        if name in {"commit", "complete"}:
            child.add_argument("--host-toolchain-inventory-sha256", required=True)
    complete_rollback_parser = subparsers.add_parser("complete-rollback")
    complete_rollback_parser.add_argument("--release-id", required=True)
    complete_rollback_parser.add_argument("--unit-state", action="append", required=True)
    complete_rollback_parser.add_argument(
        "--host-toolchain-inventory-sha256", required=True
    )
    rollback_intent_parser = subparsers.add_parser("rollback-unit-intent")
    rollback_intent_parser.add_argument("--unit", required=True, choices=MANAGED_UNITS)
    pending_parser = subparsers.add_parser("pending-release-id")
    pending_parser.add_argument(
        "--phase",
        required=True,
        choices=("committed", "rolled_back_pending_service_completion"),
    )
    subparsers.add_parser("recover")
    subparsers.add_parser("recover-boot")
    subparsers.add_parser("active-release-id")
    subparsers.add_parser("pending-toolchain-binding")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(args.root).resolve(strict=True)
    test_mode = os.environ.get("WRITER_WITNESS_ACTIVATION_TEST_MODE") == "1"
    _assert_isolated_runtime(test_mode=test_mode)
    if root != Path("/") and not test_mode:
        raise ActivationError("non-root activation trees require explicit test mode")
    store = ActivationStore(root)
    commands = {
        "begin": begin,
        "record-unit-intent": record_unit_intent,
        "publish": publish,
        "recover": recover,
        "recover-boot": recover_boot,
        "commit": commit,
        "complete": complete,
        "complete-rollback": complete_rollback,
        "rollback-unit-intent": rollback_unit_intent,
        "pending-release-id": pending_release_id,
        "candidate-dir": candidate_dir,
        "active-release-id": active_release_id,
        "pending-toolchain-binding": pending_toolchain_binding,
    }
    commands[args.command](store, args)


if __name__ == "__main__":
    try:
        main()
    except (ActivationError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Writer Witness activation failed: {exc}") from exc
