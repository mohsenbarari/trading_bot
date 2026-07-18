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
SCHEMA = "writer_witness_activation_v1"


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


def _canonical_expected_path(root: Path, raw: str, parent: str, release_id: str) -> Path:
    expected = _mapped(root, parent) / release_id
    value = _mapped(root, raw)
    if value != expected:
        raise ActivationError(f"activation path does not match release id: {raw}")
    return value


def _archive_and_remove(store: ActivationStore, journal: dict[str, object]) -> None:
    operation, _, _ = _operation_paths(store, journal)
    if operation.exists():
        if operation.is_symlink() or not operation.is_dir():
            raise ActivationError("activation operation directory is unsafe")
        shutil.rmtree(operation)
        _fsync_directory(store.operations)
    history_name = f"{journal['release_id']}-{journal['operation_id']}-{journal['phase']}.json"
    _atomic_json(store.history / history_name, journal, mode=0o600)
    store.journal.unlink()
    _fsync_directory(store.state)


def begin(store: ActivationStore, args: argparse.Namespace) -> None:
    _validate_release_id(args.release_id)
    with store.locked():
        existing = store.read_journal()
        if existing is not None:
            if existing.get("phase") in {"rolled_back", "committed"}:
                _archive_and_remove(store, existing)
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
        }
        store.write_journal(journal)
        _kill_after("begin_journal")
        print(str(candidates))


def _journal_absolute(store: ActivationStore, journal: dict[str, object], key: str) -> Path:
    raw = journal.get(key)
    if not isinstance(raw, str) or raw.startswith("/") or ".." in Path(raw).parts:
        raise ActivationError(f"activation journal path is invalid: {key}")
    return store.root / raw


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


def _install_candidates(store: ActivationStore, journal: dict[str, object]) -> None:
    _, candidates, _ = _operation_paths(store, journal)
    operation_id = str(journal["operation_id"])
    for item in MANAGED_FILES:
        payload, metadata = _read_regular(candidates / item.candidate)
        if metadata.st_uid != os.geteuid() or metadata.st_gid != os.getegid():
            raise ActivationError(f"candidate ownership is unsafe: {item.candidate}")
        _atomic_write(
            _mapped(store.root, item.destination),
            payload,
            mode=item.mode,
            uid=os.geteuid(),
            gid=os.getegid(),
            token=operation_id,
        )
    _atomic_symlink(
        _mapped(store.root, SPECIAL_PATHS["nginx_enabled"]),
        str(_mapped(store.root, "/etc/nginx/sites-available/writer-witness")),
        token=operation_id,
    )
    _remove_path_entry(_mapped(store.root, SPECIAL_PATHS["nginx_default"]))


def publish(store: ActivationStore, args: argparse.Namespace) -> None:
    with store.locked():
        journal = store.read_journal()
        if journal is None or journal.get("release_id") != args.release_id:
            raise ActivationError("matching prepared activation journal is missing")
        if journal.get("phase") != "prepared":
            raise ActivationError("activation journal is not prepared")
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
        for item in MANAGED_FILES:
            _read_regular(candidates / item.candidate)

        journal["phase"] = "publishing"
        store.write_journal(journal)
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
    if phase == "rolled_back":
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
    journal["phase"] = "rolled_back"
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
        if journal is None or journal.get("phase") != "rolled_back":
            raise ActivationError("activation rollback did not reach its durable terminal phase")
        _archive_and_remove(store, journal)
        print("activation_recovered=yes")


def recover_boot(store: ActivationStore, args: argparse.Namespace) -> None:
    """Recover at service start unless a live provisioner owns the host lock."""

    store.initialize()
    provision_lock = store.state / ".provision.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(provision_lock, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_gid != os.getegid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise ActivationError("activation provision lock metadata is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            print("activation_recovered=deferred-live-provision")
            return
        recover(store, args)
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(descriptor)


def commit(store: ActivationStore, args: argparse.Namespace) -> None:
    with store.locked():
        journal = store.read_journal()
        if journal is None or journal.get("release_id") != args.release_id:
            raise ActivationError("matching activated journal is missing")
        if journal.get("phase") != "activated":
            raise ActivationError("activation is not ready to commit")
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
        activation_dir = _journal_absolute(store, journal, "activation_dir")
        active = _mapped(store.root, "/opt/trading-bot-witness/active")
        if not active.is_symlink() or active.resolve(strict=True) != activation_dir.resolve(strict=True):
            raise ActivationError("active generation changed before completion")
        _archive_and_remove(store, journal)
        print("activation_completed=yes")


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
    for name in ("publish", "commit", "complete", "candidate-dir"):
        child = subparsers.add_parser(name)
        child.add_argument("--release-id", required=True)
    subparsers.add_parser("recover")
    subparsers.add_parser("recover-boot")
    subparsers.add_parser("active-release-id")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(args.root).resolve(strict=True)
    if root != Path("/") and os.environ.get("WRITER_WITNESS_ACTIVATION_TEST_MODE") != "1":
        raise ActivationError("non-root activation trees require explicit test mode")
    store = ActivationStore(root)
    commands = {
        "begin": begin,
        "publish": publish,
        "recover": recover,
        "recover-boot": recover_boot,
        "commit": commit,
        "complete": complete,
        "candidate-dir": candidate_dir,
        "active-release-id": active_release_id,
    }
    commands[args.command](store, args)


if __name__ == "__main__":
    try:
        main()
    except (ActivationError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Writer Witness activation failed: {exc}") from exc
