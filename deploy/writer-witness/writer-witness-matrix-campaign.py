#!/usr/bin/env python3
"""Durable, fenced ownership for one dark Writer-Witness Matrix campaign.

The helper deliberately keeps all state on the replacement Witness.  Every
publication is a complete owner-only regular file, every mutation is serialized
by one descriptor-held lock, and campaign release is an atomic rename to an
append-only tombstone.  A lost SSH response can therefore be reconciled by
repeating the same exact command; no empty ``active`` directory is ever used.
"""

from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timedelta, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Iterable, Sequence
import uuid


SCHEMA = "writer_witness_matrix_campaign_v1"
DEFAULT_STATE_ROOT = Path("/var/lib/trading-bot-witness/matrix-campaign")
ACTIVE_NAME = "active.json"
LOCK_NAME = ".campaign.lock"
MANAGED_DIRECTORIES = (
    "releases",
    "authorization-intents",
    "authorizations",
    "consumed-approvals",
    "consumed-preflights",
)
TAG_PATTERN = re.compile(r"wwm_[0-9a-f]{12}\Z")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
SCENARIO_PATTERN = re.compile(r"RH-(?:00[1-9]|01[0-2])\Z")
NONCE_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
MAX_CAMPAIGN_SECONDS = 900
TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z\Z"
)
TEMP_PATTERN = re.compile(r"\.campaign-write\.[0-9]+\.[0-9a-f]{32}\.tmp\Z")
FORBIDDEN_RUNTIME_ENV = frozenset(
    {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT", "PYTHONUSERBASE", "LD_PRELOAD", "LD_LIBRARY_PATH"}
)


class CampaignError(RuntimeError):
    """A campaign ownership or durability condition could not be proven."""


def _assert_isolated_runtime(*, test_mode: bool) -> None:
    if not test_mode and Path(sys.executable).resolve(strict=True) != Path("/usr/bin/python3.12"):
        raise CampaignError("campaign helper is not using the pinned system Python")
    if (
        not sys.flags.isolated
        or not sys.flags.no_site
        or not sys.flags.ignore_environment
        or not sys.flags.dont_write_bytecode
        or not sys.flags.utf8_mode
        or sys.pycache_prefix != "/dev/null"
    ):
        raise CampaignError("campaign helper startup is not isolated")
    if any(os.environ.get(name) for name in FORBIDDEN_RUNTIME_ENV):
        raise CampaignError("campaign helper inherited a forbidden runtime environment")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_identity(tag: str, expected_commit: str, scenario: str) -> None:
    if not TAG_PATTERN.fullmatch(tag):
        raise CampaignError("invalid Matrix campaign tag")
    if not COMMIT_PATTERN.fullmatch(expected_commit):
        raise CampaignError("invalid exact Matrix campaign commit")
    if not SCENARIO_PATTERN.fullmatch(scenario):
        raise CampaignError("invalid Matrix campaign scenario")


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_directory(path: Path, *, uid: int, gid: int) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CampaignError(f"campaign state directory is unavailable: {path}") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise CampaignError(f"campaign state directory is not owner-safe: {path}")


def _path_lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CampaignError(f"campaign state path is unavailable: {path}") from exc


def _create_one_directory(path: Path, *, uid: int, gid: int) -> None:
    """Create one owner-safe directory and durably anchor its name.

    The parent is deliberately synced only after ownership/mode validation.  A
    successful return therefore never describes a directory name that exists
    merely in the VFS cache and could disappear after a host crash.
    """

    created = False
    try:
        os.mkdir(path, 0o700)
        created = True
    except FileExistsError:
        # A concurrent first opener may have won the mkdir.  Validation below
        # still makes that race fail closed unless it produced the exact node.
        pass
    except OSError as exc:
        raise CampaignError(f"cannot create campaign state directory: {path}") from exc
    if created and os.geteuid() == 0:
        try:
            os.chown(path, uid, gid, follow_symlinks=False)
        except OSError as exc:
            raise CampaignError(f"cannot own campaign state directory: {path}") from exc
    if created:
        try:
            os.chmod(path, 0o700)
        except OSError as exc:
            raise CampaignError(f"cannot protect campaign state directory: {path}") from exc
    _validate_directory(path, uid=uid, gid=gid)
    _fsync_directory(path)
    # Sync even after a benign mkdir race: this process must independently
    # prove the observed name is anchored before it relies on the directory.
    _fsync_directory(path.parent)


def _ensure_directory(path: Path, *, uid: int, gid: int, parents: bool = False) -> None:
    metadata = _path_lstat(path)
    if metadata is not None:
        _validate_directory(path, uid=uid, gid=gid)
        return

    if not parents:
        _create_one_directory(path, uid=uid, gid=gid)
        return

    # Path.mkdir(parents=True) does not durably anchor each newly created name
    # and gives intermediate directories umask-dependent permissions.  Build
    # the missing suffix explicitly from the nearest real directory instead.
    missing: list[Path] = []
    cursor = path
    while _path_lstat(cursor) is None:
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            raise CampaignError("campaign state root has no existing directory ancestor")
        cursor = parent
    ancestor = cursor.lstat()
    if cursor.is_symlink() or not stat.S_ISDIR(ancestor.st_mode):
        raise CampaignError("campaign state root ancestor is not a real directory")
    for directory in reversed(missing):
        _create_one_directory(directory, uid=uid, gid=gid)


def _rename_noreplace(source: Path, destination: Path) -> None:
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except AttributeError as exc:
        raise CampaignError("renameat2 is required for atomic campaign publication") from exc
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
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(error_number, os.strerror(error_number), destination)
    raise OSError(error_number, os.strerror(error_number), destination)


def _json_object(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CampaignError("campaign state contains duplicate JSON keys")
        result[key] = value
    return result


def _read_record(path: Path, *, uid: int, gid: int, label: str) -> dict[str, Any]:
    # O_NONBLOCK prevents a corrupt FIFO/device entry from turning a fail-closed
    # ownership check into an indefinite wait before fstat can reject it.
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CampaignError(f"cannot securely open {label}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != uid
            or before.st_gid != gid
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size < 2
            or before.st_size > 65_536
        ):
            raise CampaignError(f"{label} is not one owner-safe regular file")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            value = os.read(descriptor, min(remaining, 16_384))
            if not value:
                raise CampaignError(f"{label} changed during read")
            chunks.append(value)
            remaining -= len(value)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise CampaignError(f"{label} changed during read")
        try:
            payload = json.loads(b"".join(chunks), object_pairs_hook=_json_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CampaignError(f"{label} is not valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise CampaignError(f"{label} must be a JSON object")
        return payload
    finally:
        os.close(descriptor)


def _kill_at(requested: str | None, point: str, status: int) -> None:
    if requested == point:
        os._exit(status)


def _publish_record(
    path: Path,
    payload: dict[str, Any],
    *,
    uid: int,
    gid: int,
    failpoint: str | None = None,
    before_point: str | None = None,
    after_point: str | None = None,
) -> None:
    raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    temporary = path.parent / f".campaign-write.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        if os.geteuid() == 0:
            os.fchown(descriptor, uid, gid)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise CampaignError("short campaign state write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _kill_at(failpoint, before_point or "", 91)
    try:
        _rename_noreplace(temporary, path)
    except FileExistsError:
        temporary.unlink(missing_ok=True)
        _fsync_directory(path.parent)
        raise
    _fsync_directory(path.parent)
    # Every "after publish" failpoint means the target name has already been
    # anchored in its parent directory, not merely atomically renamed in RAM.
    _kill_at(failpoint, after_point or "", 92)


def _parse_timestamp(value: str, *, label: str) -> datetime:
    if not TIMESTAMP_PATTERN.fullmatch(value):
        raise CampaignError(f"{label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CampaignError(f"{label} timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise CampaignError(f"{label} timestamp is invalid")
    return parsed.astimezone(timezone.utc)


def _identity_payload(
    tag: str,
    expected_commit: str,
    scenario: str,
    not_after: str,
) -> dict[str, str]:
    _require_identity(tag, expected_commit, scenario)
    _parse_timestamp(not_after, label="campaign expiry")
    return {
        "tag": tag,
        "expected_commit": expected_commit,
        "scenario": scenario,
        "not_after": not_after,
    }


def _require_unexpired(identity: dict[str, str]) -> None:
    if datetime.now(timezone.utc) >= _parse_timestamp(
        identity["not_after"], label="campaign expiry"
    ):
        raise CampaignError("Matrix campaign authorization has expired")


def _validate_record(
    payload: dict[str, Any],
    *,
    record_type: str,
    identity: dict[str, str],
    extra: dict[str, str] | None = None,
) -> None:
    required = {
        "schema": SCHEMA,
        "record_type": record_type,
        **identity,
        **(extra or {}),
    }
    expected_keys = set(required) | {"recorded_at"}
    if set(payload) != expected_keys or any(payload.get(key) != value for key, value in required.items()):
        raise CampaignError(f"{record_type} is owned by a different campaign identity")
    if not TIMESTAMP_PATTERN.fullmatch(str(payload.get("recorded_at") or "")):
        raise CampaignError(f"{record_type} timestamp is invalid")


def _new_record(
    record_type: str,
    identity: dict[str, str],
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    return {
        "schema": SCHEMA,
        "record_type": record_type,
        **identity,
        **(extra or {}),
        "recorded_at": _utc_now(),
    }


def _validated_identity(
    payload: dict[str, Any],
    *,
    record_type: str,
    binding_required: bool = False,
) -> tuple[dict[str, str], dict[str, str]]:
    """Validate a record without trusting caller-supplied identity text.

    This is used by structured inspection and the global reservation scan.  It
    validates the complete schema before returning any values that can affect
    an ownership decision.
    """

    identity = _identity_payload(
        str(payload.get("tag") or ""),
        str(payload.get("expected_commit") or ""),
        str(payload.get("scenario") or ""),
        str(payload.get("not_after") or ""),
    )
    binding: dict[str, str] = {}
    if binding_required:
        nonce = str(payload.get("authorization_nonce") or "")
        preflight = str(payload.get("preflight_sha256") or "")
        if not NONCE_PATTERN.fullmatch(nonce):
            raise CampaignError(f"{record_type} authorization nonce is invalid")
        if not SHA256_PATTERN.fullmatch(preflight):
            raise CampaignError(f"{record_type} preflight SHA-256 is invalid")
        binding = {
            "authorization_nonce": nonce,
            "preflight_sha256": preflight,
        }
    _validate_record(
        payload,
        record_type=record_type,
        identity=identity,
        extra=binding,
    )
    return identity, binding


class CampaignStore:
    """Descriptor-locked access to the complete replacement-host campaign state."""

    def __init__(
        self,
        root: Path = DEFAULT_STATE_ROOT,
        *,
        owner_uid: int | None = None,
        owner_gid: int | None = None,
        read_only: bool = False,
    ) -> None:
        self.root = root
        self.owner_uid = os.geteuid() if owner_uid is None else owner_uid
        self.owner_gid = os.getegid() if owner_gid is None else owner_gid
        self.read_only = read_only
        self._lock_descriptor: int | None = None

    def __enter__(self) -> "CampaignStore":
        if self.read_only:
            _validate_directory(
                self.root,
                uid=self.owner_uid,
                gid=self.owner_gid,
            )
        else:
            _ensure_directory(
                self.root,
                uid=self.owner_uid,
                gid=self.owner_gid,
                parents=True,
            )
        lock_path = self.root / LOCK_NAME
        # The lock must also be inspectable without blocking if hostile or
        # corrupted state placed a non-regular node at the fixed lock path.
        flags = (os.O_RDONLY if self.read_only else os.O_RDWR | os.O_CREAT) | os.O_NONBLOCK
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise CampaignError("campaign lock cannot be securely opened") from exc
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != self.owner_uid
                or metadata.st_gid != self.owner_gid
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
            ):
                raise CampaignError("campaign lock is not one owner-safe regular file")
            fcntl.flock(
                descriptor,
                fcntl.LOCK_SH if self.read_only else fcntl.LOCK_EX,
            )
        except BaseException:
            os.close(descriptor)
            raise
        self._lock_descriptor = descriptor
        try:
            for name in MANAGED_DIRECTORIES:
                if self.read_only:
                    _validate_directory(
                        self.root / name,
                        uid=self.owner_uid,
                        gid=self.owner_gid,
                    )
                else:
                    _ensure_directory(
                        self.root / name,
                        uid=self.owner_uid,
                        gid=self.owner_gid,
                    )
            legacy_active = self.root / "active"
            if legacy_active.exists() or legacy_active.is_symlink():
                raise CampaignError(
                    "legacy active campaign state requires explicit reconciliation"
                )
            if self.read_only:
                self._assert_no_abandoned_temporary_files()
            else:
                self._remove_abandoned_temporary_files()
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        if self._lock_descriptor is not None:
            try:
                fcntl.flock(self._lock_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(self._lock_descriptor)
                self._lock_descriptor = None

    def _require_lock(self) -> None:
        if self._lock_descriptor is None:
            raise CampaignError("campaign store must be used while its lock is held")

    def _remove_abandoned_temporary_files(self) -> None:
        self._require_lock()
        for directory in (self.root, *(self.root / name for name in MANAGED_DIRECTORIES)):
            for path in directory.iterdir():
                if not TEMP_PATTERN.fullmatch(path.name):
                    continue
                metadata = path.lstat()
                if (
                    path.is_symlink()
                    or not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != self.owner_uid
                    or metadata.st_gid != self.owner_gid
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                    or metadata.st_nlink != 1
                ):
                    raise CampaignError("abandoned campaign temporary file is not owner-safe")
                path.unlink()
            _fsync_directory(directory)

    def _assert_no_abandoned_temporary_files(self) -> None:
        """Reject crash residue without repairing or deleting any filesystem state."""

        self._require_lock()
        for directory in (self.root, *(self.root / name for name in MANAGED_DIRECTORIES)):
            for path in directory.iterdir():
                if TEMP_PATTERN.fullmatch(path.name):
                    raise CampaignError("abandoned campaign temporary file requires recovery")

    @property
    def active_path(self) -> Path:
        return self.root / ACTIVE_NAME

    def release_path(self, tag: str) -> Path:
        return self.root / "releases" / f"{tag}.json"

    def _read_and_validate(
        self,
        path: Path,
        *,
        record_type: str,
        identity: dict[str, str],
        extra: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload = _read_record(
            path,
            uid=self.owner_uid,
            gid=self.owner_gid,
            label=record_type,
        )
        _validate_record(payload, record_type=record_type, identity=identity, extra=extra)
        return payload

    def _read_claim_identity(self, path: Path, *, label: str) -> dict[str, str]:
        payload = _read_record(
            path,
            uid=self.owner_uid,
            gid=self.owner_gid,
            label=label,
        )
        identity, _binding = _validated_identity(
            payload,
            record_type="campaign_claim",
        )
        return identity

    def _reserve_global_binding(
        self,
        *,
        identity: dict[str, str],
        binding: dict[str, str],
    ) -> None:
        """Publish or resume the one record that reserves both global values.

        All intent records are immutable and this scan runs while the single
        campaign lock is held.  Consequently the first durable intent reserves
        *both* its nonce and preflight digest even if SIGKILL occurs before
        either convenience index in ``consumed-*`` is published.
        """

        intent_directory = self.root / "authorization-intents"
        for candidate in sorted(intent_directory.iterdir(), key=lambda item: item.name):
            if not candidate.name.endswith(".json"):
                raise CampaignError(
                    "authorization intent reservation space contains an unknown entry"
                )
            filename_tag = candidate.name.removesuffix(".json")
            if not TAG_PATTERN.fullmatch(filename_tag):
                raise CampaignError("authorization intent filename is invalid")
            payload = _read_record(
                candidate,
                uid=self.owner_uid,
                gid=self.owner_gid,
                label="authorization_intent",
            )
            observed_identity, observed_binding = _validated_identity(
                payload,
                record_type="authorization_intent",
                binding_required=True,
            )
            if observed_identity["tag"] != filename_tag:
                raise CampaignError("authorization intent filename does not match its owner")

            nonce_matches = (
                observed_binding["authorization_nonce"]
                == binding["authorization_nonce"]
            )
            preflight_matches = (
                observed_binding["preflight_sha256"]
                == binding["preflight_sha256"]
            )
            exact = observed_identity == identity and observed_binding == binding
            if (nonce_matches or preflight_matches) and not exact:
                conflicts: list[str] = []
                if nonce_matches:
                    conflicts.append("authorization nonce")
                if preflight_matches:
                    conflicts.append("preflight SHA-256")
                raise CampaignError(
                    " and ".join(conflicts)
                    + " is already reserved by a different campaign identity or binding"
                )

        intent = intent_directory / f"{identity['tag']}.json"
        self._publish_or_validate(
            intent,
            record_type="authorization_intent",
            identity=identity,
            extra=binding,
        )

    def _publish_or_validate(
        self,
        path: Path,
        *,
        record_type: str,
        identity: dict[str, str],
        extra: dict[str, str] | None = None,
        failpoint: str | None = None,
        before_point: str | None = None,
        after_point: str | None = None,
    ) -> bool:
        if path.exists() or path.is_symlink():
            self._read_and_validate(
                path,
                record_type=record_type,
                identity=identity,
                extra=extra,
            )
            _fsync_directory(path.parent)
            return False
        try:
            _publish_record(
                path,
                _new_record(record_type, identity, extra),
                uid=self.owner_uid,
                gid=self.owner_gid,
                failpoint=failpoint,
                before_point=before_point,
                after_point=after_point,
            )
        except FileExistsError:
            self._read_and_validate(
                path,
                record_type=record_type,
                identity=identity,
                extra=extra,
            )
            return False
        return True

    def claim(
        self,
        *,
        tag: str,
        expected_commit: str,
        scenario: str,
        not_after: str,
        failpoint: str | None = None,
    ) -> dict[str, Any]:
        self._require_lock()
        identity = _identity_payload(tag, expected_commit, scenario, not_after)
        expiry = _parse_timestamp(not_after, label="campaign expiry")
        now = datetime.now(timezone.utc)
        if expiry <= now or expiry > now + timedelta(seconds=MAX_CAMPAIGN_SECONDS):
            raise CampaignError(
                f"campaign expiry must be within the next {MAX_CAMPAIGN_SECONDS} seconds"
            )
        release = self.release_path(tag)
        if release.exists() or release.is_symlink():
            self._read_and_validate(
                release,
                record_type="campaign_claim",
                identity=identity,
            )
            raise CampaignError("released campaign identity cannot be claimed again")
        created = self._publish_or_validate(
            self.active_path,
            record_type="campaign_claim",
            identity=identity,
            failpoint=failpoint,
            before_point="claim_before_publish",
            after_point="claim_after_publish",
        )
        return {
            "status": "claimed" if created else "already_claimed",
            **identity,
            "active_state": "regular_file",
        }

    def inspect(
        self,
        *,
        tag: str,
        expected_commit: str,
        scenario: str,
        not_after: str,
    ) -> dict[str, Any]:
        """Return a validated, machine-readable ownership classification."""

        self._require_lock()
        identity = _identity_payload(tag, expected_commit, scenario, not_after)
        active_identity: dict[str, str] | None = None
        released_identity: dict[str, str] | None = None

        if self.active_path.exists() or self.active_path.is_symlink():
            active_identity = self._read_claim_identity(
                self.active_path,
                label="campaign_claim",
            )
        release = self.release_path(tag)
        if release.exists() or release.is_symlink():
            released_identity = self._read_claim_identity(
                release,
                label="campaign_release",
            )
            if released_identity["tag"] != tag:
                raise CampaignError("campaign release filename does not match its owner")

        active_relation = (
            "absent"
            if active_identity is None
            else "exact"
            if active_identity == identity
            else "foreign"
        )
        released_relation = (
            "absent"
            if released_identity is None
            else "exact"
            if released_identity == identity
            else "foreign"
        )
        if active_relation == "exact" and released_relation != "absent":
            raise CampaignError("campaign has conflicting active and released records")

        if active_relation == "exact":
            state = "active_exact"
        elif released_relation == "exact":
            # A later, foreign campaign may legitimately be active after this
            # exact campaign was released; report both relations explicitly.
            state = "released_exact"
        elif active_relation == "foreign":
            state = "active_foreign"
        elif released_relation == "foreign":
            state = "released_foreign"
        else:
            state = "absent"

        result: dict[str, Any] = {
            "status": "inspected",
            "state": state,
            "active_relation": active_relation,
            "release_relation": released_relation,
            **identity,
        }
        result["expired"] = datetime.now(timezone.utc) >= _parse_timestamp(
            identity["not_after"], label="campaign expiry"
        )
        if active_relation == "foreign":
            result["active_identity"] = active_identity
        if released_relation == "foreign":
            result["release_identity"] = released_identity
        return result

    def assert_state(
        self,
        *,
        tag: str,
        expected_commit: str,
        scenario: str,
        not_after: str,
        expect: str,
    ) -> dict[str, Any]:
        self._require_lock()
        identity = _identity_payload(tag, expected_commit, scenario, not_after)
        if expect in {"active", "active-cleanup"}:
            if expect == "active":
                _require_unexpired(identity)
            if self.release_path(tag).exists() or self.release_path(tag).is_symlink():
                raise CampaignError("campaign has conflicting active and released records")
            if not self.active_path.exists() or self.active_path.is_symlink():
                raise CampaignError("exact Matrix campaign is not active")
            self._read_and_validate(
                self.active_path,
                record_type="campaign_claim",
                identity=identity,
            )
        elif expect == "released":
            if self.active_path.exists() or self.active_path.is_symlink():
                raise CampaignError("a Matrix campaign remains active")
            release = self.release_path(tag)
            if not release.exists() or release.is_symlink():
                raise CampaignError("exact Matrix campaign release tombstone is missing")
            self._read_and_validate(
                release,
                record_type="campaign_claim",
                identity=identity,
            )
        elif expect == "absent":
            inspected = self.inspect(**identity)
            if inspected["state"] != "absent":
                raise CampaignError(
                    "Matrix campaign state is not absent: " + str(inspected["state"])
                )
        else:
            raise CampaignError("unsupported campaign assertion")
        return {"status": "asserted", "expected_state": expect, **identity}

    def consume(
        self,
        *,
        tag: str,
        expected_commit: str,
        scenario: str,
        not_after: str,
        authorization_nonce: str,
        preflight_sha256: str,
        failpoint: str | None = None,
    ) -> dict[str, Any]:
        self._require_lock()
        identity = _identity_payload(tag, expected_commit, scenario, not_after)
        _require_unexpired(identity)
        if not NONCE_PATTERN.fullmatch(authorization_nonce):
            raise CampaignError("invalid one-time authorization nonce")
        if not SHA256_PATTERN.fullmatch(preflight_sha256):
            raise CampaignError("invalid one-time preflight SHA-256")
        if not self.active_path.exists() or self.active_path.is_symlink():
            raise CampaignError("authorization cannot be consumed without exact active ownership")
        if self.release_path(tag).exists() or self.release_path(tag).is_symlink():
            raise CampaignError("authorization found conflicting released campaign state")
        self._read_and_validate(
            self.active_path,
            record_type="campaign_claim",
            identity=identity,
        )
        binding = {
            "authorization_nonce": authorization_nonce,
            "preflight_sha256": preflight_sha256,
        }
        approval = self.root / "consumed-approvals" / f"{authorization_nonce}.json"
        preflight = self.root / "consumed-preflights" / f"{preflight_sha256}.json"
        completion = self.root / "authorizations" / f"{tag}.json"

        # A deployment upgraded from the earlier directory-based protocol must
        # never make an old global one-time record reusable merely because the
        # new append-only representation uses a .json suffix.
        for legacy in (
            self.root / "consumed-approvals" / authorization_nonce,
            self.root / "consumed-preflights" / preflight_sha256,
        ):
            if legacy.exists() or legacy.is_symlink():
                raise CampaignError(
                    "legacy global authorization consumption requires reconciliation"
                )

        # Reconcile append-only indexes left by an older helper before creating
        # a fresh intent.  This prevents a conflicting old nonce from causing a
        # new preflight value (or vice versa) to become reserved accidentally.
        for path, record_type in (
            (approval, "approval_consumption"),
            (preflight, "preflight_consumption"),
            (completion, "authorization_consumed"),
        ):
            if path.exists() or path.is_symlink():
                self._read_and_validate(
                    path,
                    record_type=record_type,
                    identity=identity,
                    extra=binding,
                )

        self._reserve_global_binding(identity=identity, binding=binding)
        _kill_at(failpoint, "consume_after_intent", 93)
        self._publish_or_validate(
            approval,
            record_type="approval_consumption",
            identity=identity,
            extra=binding,
        )
        _kill_at(failpoint, "consume_after_approval", 94)
        self._publish_or_validate(
            preflight,
            record_type="preflight_consumption",
            identity=identity,
            extra=binding,
        )
        _kill_at(failpoint, "consume_after_preflight", 95)
        created = self._publish_or_validate(
            completion,
            record_type="authorization_consumed",
            identity=identity,
            extra=binding,
        )
        return {
            "status": "consumed" if created else "already_consumed",
            **identity,
            "authorization_nonce_sha256": hashlib.sha256(authorization_nonce.encode()).hexdigest(),
            "preflight_sha256": preflight_sha256,
        }

    def release(
        self,
        *,
        tag: str,
        expected_commit: str,
        scenario: str,
        not_after: str,
        failpoint: str | None = None,
    ) -> dict[str, Any]:
        self._require_lock()
        identity = _identity_payload(tag, expected_commit, scenario, not_after)
        release = self.release_path(tag)
        active_exists = self.active_path.exists() or self.active_path.is_symlink()
        release_exists = release.exists() or release.is_symlink()
        if active_exists and release_exists:
            raise CampaignError("campaign has conflicting active and released records")
        if release_exists:
            self._read_and_validate(
                release,
                record_type="campaign_claim",
                identity=identity,
            )
            _fsync_directory(release.parent)
            _fsync_directory(self.root)
            return {"status": "already_released", **identity}
        if not active_exists:
            raise CampaignError("campaign release cannot prove prior active ownership")
        self._read_and_validate(
            self.active_path,
            record_type="campaign_claim",
            identity=identity,
        )
        try:
            _rename_noreplace(self.active_path, release)
        except FileExistsError:
            if self.active_path.exists() or self.active_path.is_symlink():
                raise CampaignError("campaign release raced with conflicting state")
            self._read_and_validate(
                release,
                record_type="campaign_claim",
                identity=identity,
            )
            _fsync_directory(release.parent)
            _fsync_directory(self.root)
            return {"status": "already_released", **identity}
        # A cross-directory rename needs both the target insertion and source
        # removal anchored before an "after rename" crash point is durable.
        _fsync_directory(release.parent)
        _fsync_directory(self.root)
        _kill_at(failpoint, "release_after_rename", 96)
        return {"status": "released", **identity}


def _add_identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tag", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--not-after", required=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-mode", action="store_true")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument(
        "--test-failpoint",
        choices=(
            "claim_before_publish",
            "claim_after_publish",
            "consume_after_intent",
            "consume_after_approval",
            "consume_after_preflight",
            "release_after_rename",
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    claim_parser = subparsers.add_parser("claim")
    _add_identity_arguments(claim_parser)
    inspect_parser = subparsers.add_parser("inspect")
    _add_identity_arguments(inspect_parser)
    assert_parser = subparsers.add_parser("assert")
    _add_identity_arguments(assert_parser)
    assert_parser.add_argument(
        "--expect",
        choices=("active", "active-cleanup", "released", "absent"),
        required=True,
    )
    consume_parser = subparsers.add_parser("consume")
    _add_identity_arguments(consume_parser)
    consume_parser.add_argument("--authorization-nonce", required=True)
    consume_parser.add_argument("--preflight-sha256", required=True)
    release_parser = subparsers.add_parser("release")
    _add_identity_arguments(release_parser)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        _assert_isolated_runtime(test_mode=args.test_mode)
        if args.test_mode:
            if args.state_root is None:
                raise CampaignError("test mode requires an explicit state root")
            state_root = args.state_root.resolve()
            owner_uid = os.geteuid()
            owner_gid = os.getegid()
        else:
            if args.state_root is not None or args.test_failpoint is not None:
                raise CampaignError("production campaign state and failpoints cannot be overridden")
            if os.geteuid() != 0:
                raise CampaignError("production Matrix campaign helper must run as root")
            state_root = DEFAULT_STATE_ROOT
            owner_uid = 0
            owner_gid = 0
        common = {
            "tag": args.tag,
            "expected_commit": args.expected_commit,
            "scenario": args.scenario,
            "not_after": args.not_after,
        }
        with CampaignStore(
            state_root,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
            read_only=args.command in {"inspect", "assert"},
        ) as store:
            if args.command == "claim":
                result = store.claim(**common, failpoint=args.test_failpoint)
            elif args.command == "inspect":
                result = store.inspect(**common)
            elif args.command == "assert":
                result = store.assert_state(**common, expect=args.expect)
            elif args.command == "consume":
                result = store.consume(
                    **common,
                    authorization_nonce=args.authorization_nonce,
                    preflight_sha256=args.preflight_sha256,
                    failpoint=args.test_failpoint,
                )
            else:
                result = store.release(**common, failpoint=args.test_failpoint)
    except (CampaignError, OSError) as exc:
        print(f"writer witness Matrix campaign failed closed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
