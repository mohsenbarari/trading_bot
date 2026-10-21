from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
from typing import Any

try:
    from scripts.proposal_runner import unique_json
except ImportError:
    try:
        from proposal_runner import unique_json  # type: ignore
    except ImportError:
        try:
            from scripts.two_site_proposal_runner import unique_json  # type: ignore
        except ImportError:
            from two_site_proposal_runner import unique_json  # type: ignore

try:
    from scripts.two_site_provider_broker import (
        BrokerError,
        _open_existing_cycle_run_directory,
    )
except ImportError:
    from two_site_provider_broker import (  # type: ignore
        BrokerError,
        _open_existing_cycle_run_directory,
    )

try:
    from scripts.two_site_quota_wait_record import (
        WaitRecordError,
        _EXPECTED_KEYS,
        validate_initial_wait_record,
    )
except ImportError:
    from two_site_quota_wait_record import (  # type: ignore
        WaitRecordError,
        _EXPECTED_KEYS,
        validate_initial_wait_record,
    )

QUOTA_WAIT_FILENAME: str = "quota-wait.json"
MAX_WAIT_RECORD_BYTES: int = 16384
_READ_LIMIT: int = 16385


class WaitStorageError(Exception):
    """Raised when quota wait storage reading or validation fails."""


def _stat_fingerprint(info: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _validate_stat(info: os.stat_result, label: str) -> None:
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise WaitStorageError(f"{label} is not a regular file")
    if info.st_uid != os.geteuid():
        raise WaitStorageError(f"{label} owner mismatch")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise WaitStorageError(f"{label} mode must be 0600")
    if info.st_nlink != 1:
        raise WaitStorageError(f"{label} has invalid link count")
    if info.st_size < 0 or info.st_size > MAX_WAIT_RECORD_BYTES:
        raise WaitStorageError(f"{label} exceeds byte limit")


def _check_record_name(filename: str) -> None:
    if filename not in {QUOTA_WAIT_FILENAME, "quota-history-retirement.json",
                         "quota-history-retirement-complete.json"} and filename not in {
            f"quota-unknown-health-{ordinal}.json" for ordinal in range(1, 6)}:
        raise WaitStorageError("unsupported cycle record name")


def read_initial_wait_record_bytes(root: Path, controller_run_id: str) -> bytes:
    return _read_cycle_record_bytes(root, controller_run_id, QUOTA_WAIT_FILENAME)


def publish_initial_wait_record_bytes(root: Path, controller_run_id: str, data: bytes) -> bytes:
    return _publish_cycle_record_bytes(root, controller_run_id, data, QUOTA_WAIT_FILENAME)


def _read_cycle_record_bytes(root: Path, controller_run_id: str, filename: str) -> bytes:
    _check_record_name(filename)
    if not isinstance(root, Path):
        raise WaitStorageError("root must be a Path")
    if not isinstance(controller_run_id, str) or not controller_run_id:
        raise WaitStorageError("controller_run_id must be a non-empty string")

    try:
        with _open_existing_cycle_run_directory(root, controller_run_id) as (run_path, run_fd, verify_identity):
            try:
                verify_identity()
            except BrokerError as exc:
                raise WaitStorageError(f"directory identity verification failed before read: {exc}") from exc

            file_path = run_path / filename

            try:
                lstat_before = os.lstat(file_path)
            except OSError as exc:
                raise WaitStorageError(f"cannot lstat {filename}: {exc}") from exc
            _validate_stat(lstat_before, f"{filename} path before open")
            initial_fingerprint = _stat_fingerprint(lstat_before)

            try:
                dirfd_stat_before = os.stat(filename, dir_fd=run_fd, follow_symlinks=False)
            except OSError as exc:
                raise WaitStorageError(f"cannot stat {filename} via dir_fd before open: {exc}") from exc
            _validate_stat(dirfd_stat_before, f"{filename} via dir_fd before open")
            if _stat_fingerprint(dirfd_stat_before) != initial_fingerprint:
                raise WaitStorageError(f"{filename} dir_fd stat mismatch before open")

            flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            try:
                file_fd = os.open(filename, flags, dir_fd=run_fd)
            except OSError as exc:
                raise WaitStorageError(f"cannot safely open {filename}: {exc}") from exc

            try:
                try:
                    fstat_before = os.fstat(file_fd)
                except OSError as exc:
                    raise WaitStorageError(f"cannot fstat {filename} before read: {exc}") from exc
                _validate_stat(fstat_before, f"{filename} descriptor before read")
                if _stat_fingerprint(fstat_before) != initial_fingerprint:
                    raise WaitStorageError(f"{filename} descriptor metadata mismatch before read")

                expected_size = initial_fingerprint[5]
                chunks: list[bytes] = []
                total_read = 0
                while total_read < _READ_LIMIT:
                    try:
                        chunk = os.read(file_fd, _READ_LIMIT - total_read)
                    except OSError as exc:
                        raise WaitStorageError(f"cannot read {filename}: {exc}") from exc
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total_read += len(chunk)

                data = b"".join(chunks)
                if len(data) > MAX_WAIT_RECORD_BYTES:
                    raise WaitStorageError(f"{filename} exceeds byte limit")
                if len(data) != expected_size:
                    raise WaitStorageError(f"{filename} read size mismatch")

                try:
                    fstat_after = os.fstat(file_fd)
                except OSError as exc:
                    raise WaitStorageError(f"cannot fstat {filename} after read: {exc}") from exc
                _validate_stat(fstat_after, f"{filename} descriptor after read")
                if _stat_fingerprint(fstat_after) != initial_fingerprint:
                    raise WaitStorageError(f"{filename} descriptor metadata changed after read")

                try:
                    dirfd_stat_after = os.stat(filename, dir_fd=run_fd, follow_symlinks=False)
                except OSError as exc:
                    raise WaitStorageError(f"cannot stat {filename} via dir_fd after read: {exc}") from exc
                _validate_stat(dirfd_stat_after, f"{filename} via dir_fd after read")
                if _stat_fingerprint(dirfd_stat_after) != initial_fingerprint:
                    raise WaitStorageError(f"{filename} dir_fd metadata changed after read")

                try:
                    lstat_after = os.lstat(file_path)
                except OSError as exc:
                    raise WaitStorageError(f"cannot lstat {filename} after read: {exc}") from exc
                _validate_stat(lstat_after, f"{filename} path after read")
                if _stat_fingerprint(lstat_after) != initial_fingerprint:
                    raise WaitStorageError(f"{filename} path metadata changed after read")

                try:
                    verify_identity()
                except BrokerError as exc:
                    raise WaitStorageError(f"directory identity verification failed after read: {exc}") from exc

                try:
                    decoded = data.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise WaitStorageError(f"{filename} is not valid UTF-8: {exc}") from exc

                try:
                    payload = unique_json(decoded)
                except (json.JSONDecodeError, ValueError) as exc:
                    raise WaitStorageError(f"{filename} is not valid JSON: {exc}") from exc

                if not isinstance(payload, dict) or type(payload) is not dict:
                    raise WaitStorageError(f"{filename} must be a JSON object")

                try:
                    canonical_text = json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    canonical_bytes = canonical_text.encode("utf-8")
                except (ValueError, TypeError) as exc:
                    raise WaitStorageError(f"{filename} cannot be serialized to canonical JSON: {exc}") from exc

                if data != canonical_bytes:
                    raise WaitStorageError(f"{filename} is not canonical JSON bytes")

                return data
            finally:
                try:
                    os.close(file_fd)
                except OSError as exc:
                    if sys.exc_info()[1] is None:
                        raise WaitStorageError(f"cannot safely close {filename} descriptor: {exc}") from exc
    except BrokerError as exc:
        raise WaitStorageError(f"cannot safely open cycle run directory: {exc}") from exc


def _publish_cycle_record_bytes(
    root: Path,
    controller_run_id: str,
    data: bytes,
    filename: str,
) -> bytes:
    _check_record_name(filename)
    unknown_health = filename in {f"quota-unknown-health-{n}.json" for n in range(1, 6)}
    if not isinstance(root, Path):
        raise WaitStorageError("root must be a Path")
    if not isinstance(controller_run_id, str) or not controller_run_id:
        raise WaitStorageError("controller_run_id must be a non-empty string")
    if not isinstance(data, bytes) or type(data) is not bytes:
        raise WaitStorageError("data must be bytes")
    if len(data) > MAX_WAIT_RECORD_BYTES:
        raise WaitStorageError("data exceeds byte limit")

    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WaitStorageError(f"data is not valid UTF-8: {exc}") from exc

    try:
        payload = unique_json(decoded)
    except (json.JSONDecodeError, ValueError) as exc:
        raise WaitStorageError(f"data is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict) or type(payload) is not dict:
        raise WaitStorageError("data must be a JSON object")

    try:
        canonical_text = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        canonical_bytes = canonical_text.encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise WaitStorageError(f"data cannot be serialized to canonical JSON: {exc}") from exc

    if data != canonical_bytes:
        raise WaitStorageError("data is not canonical JSON bytes")

    temp_name = f".{filename}.tmp.{os.getpid()}"
    target_name = filename
    temp_created = False
    linked = False

    try:
        with _open_existing_cycle_run_directory(root, controller_run_id) as (run_path, run_fd, verify_identity):
            try:
                verify_identity()
            except BrokerError as exc:
                raise WaitStorageError(f"directory identity verification failed before write: {exc}") from exc

            file_path = run_path / target_name
            target_exists = False
            try:
                os.stat(target_name, dir_fd=run_fd, follow_symlinks=False)
                target_exists = True
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise WaitStorageError(f"cannot stat {target_name} via dir_fd: {exc}") from exc

            if not target_exists:
                try:
                    os.lstat(file_path)
                    target_exists = True
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise WaitStorageError(f"cannot lstat {target_name}: {exc}") from exc

            if target_exists:
                raise WaitStorageError(f"{target_name} already exists")

            file_fd: int | None = None
            try:
                flags = (
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                )
                try:
                    file_fd = os.open(temp_name, flags, 0o600, dir_fd=run_fd)
                except OSError as exc:
                    raise WaitStorageError(f"cannot safely create temp file {temp_name}: {exc}") from exc
                temp_created = True

                try:
                    total_written = 0
                    data_len = len(data)
                    while total_written < data_len:
                        try:
                            written = os.write(file_fd, data[total_written:])
                        except OSError as exc:
                            raise WaitStorageError(f"cannot write {temp_name}: {exc}") from exc
                        if written <= 0:
                            raise WaitStorageError(f"write stalled while writing {temp_name}")
                        total_written += written

                    try:
                        os.fchmod(file_fd, 0o600)
                    except OSError as exc:
                        raise WaitStorageError(f"cannot fchmod {temp_name}: {exc}") from exc

                    try:
                        os.fsync(file_fd)
                    except OSError as exc:
                        raise WaitStorageError(f"cannot fsync {temp_name}: {exc}") from exc
                finally:
                    try:
                        os.close(file_fd)
                    except OSError as exc:
                        file_fd = None
                        raise WaitStorageError(f"cannot safely close temp file {temp_name}: {exc}") from exc
                    file_fd = None

                try:
                    os.link(
                        temp_name,
                        target_name,
                        src_dir_fd=run_fd,
                        dst_dir_fd=run_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError as exc:
                    raise WaitStorageError(f"cannot link {target_name}: target already exists") from exc
                except OSError as exc:
                    raise WaitStorageError(f"cannot link {target_name}: {exc}") from exc
                linked = True

                if unknown_health:
                    # The alias is the existing codec's ambiguity evidence.
                    # It may disappear only AFTER target durability is established.
                    try:
                        os.fsync(run_fd)
                    except OSError as exc:
                        raise UnknownHealthPublicationPending("UNKNOWN required target directory fsync failed") from exc

                try:
                    os.unlink(temp_name, dir_fd=run_fd)
                except OSError as exc:
                    raise WaitStorageError(f"ambiguous post-link publication failure: cannot unlink {temp_name}: {exc}") from exc

                try:
                    os.fsync(run_fd)
                except OSError as exc:
                    if unknown_health:
                        raise UnknownHealthDurableCleanupError("UNKNOWN durable target cleanup fsync failed") from exc
                    raise WaitStorageError(f"ambiguous post-link publication failure: cannot fsync directory: {exc}") from exc

                try:
                    verify_identity()
                except BrokerError as exc:
                    raise WaitStorageError(f"ambiguous post-link publication failure: directory identity verification failed after write: {exc}") from exc
            finally:
                if file_fd is not None:
                    try:
                        os.close(file_fd)
                    except OSError:
                        pass
                if temp_created and not linked and not (unknown_health and os.path.lexists(file_path)):
                    try:
                        os.unlink(temp_name, dir_fd=run_fd)
                    except OSError:
                        pass
    except BrokerError as exc:
        if linked:
            raise WaitStorageError(f"ambiguous post-link publication failure: cycle run directory error: {exc}") from exc
        raise WaitStorageError(f"cannot safely open cycle run directory: {exc}") from exc
    except OSError as exc:
        if linked:
            raise WaitStorageError(f"ambiguous post-link publication failure: {exc}") from exc
        raise WaitStorageError(f"cannot safely access cycle run directory: {exc}") from exc

    try:
        readback_bytes = (read_initial_wait_record_bytes(root, controller_run_id)
                          if filename == QUOTA_WAIT_FILENAME else
                          _read_cycle_record_bytes(root, controller_run_id, filename))
    except WaitStorageError as exc:
        raise WaitStorageError(f"ambiguous post-link publication failure: readback failed: {exc}") from exc
    except Exception as exc:
        raise WaitStorageError(f"ambiguous post-link publication failure: readback unexpected error: {exc}") from exc

    if readback_bytes != data:
        raise WaitStorageError("ambiguous post-link publication failure: readback data mismatch")

    return readback_bytes


def read_initial_wait_record(
    root: Path,
    controller_run_id: str,
    expected: object,
) -> dict:
    """Read and validate initial quota wait record against independent expected identity.

    This function performs READ+VALIDATE composition only and NEVER grants wake
    or replay execution authority.

    The caller MUST independently obtain expected from validated reservation,
    terminal receipt, repository and rescope intent in a future unit, and hold
    the Supervisor lock while invoking this function.

    No field from the record is used to construct or fill expected.
    """
    if not isinstance(root, Path):
        raise WaitStorageError("root must be a Path")
    if type(controller_run_id) is not str:
        raise WaitStorageError(
            f"controller_run_id must be exact str, got {type(controller_run_id).__name__}"
        )
    if not controller_run_id:
        raise WaitStorageError("controller_run_id must be a non-empty string")
    if type(expected) is not dict:
        raise WaitStorageError(
            f"expected must be exact dict, got {type(expected).__name__}"
        )
    if set(expected.keys()) != _EXPECTED_KEYS:
        raise WaitStorageError(
            f"expected keys must exactly match {sorted(_EXPECTED_KEYS)!r}"
        )
    expected_ctrl_id = expected["controller_run_id"]
    if type(expected_ctrl_id) is not type(controller_run_id) or expected_ctrl_id != controller_run_id:
        raise WaitStorageError(
            f"expected controller_run_id {expected_ctrl_id!r} does not match {controller_run_id!r}"
        )

    raw_bytes = read_initial_wait_record_bytes(root, controller_run_id)

    try:
        decoded = raw_bytes.decode("utf-8")
    except (KeyboardInterrupt, SystemExit):
        raise
    except UnicodeDecodeError as exc:
        raise WaitStorageError(f"{QUOTA_WAIT_FILENAME} is not valid UTF-8: {exc}") from exc

    try:
        record = unique_json(decoded)
    except (KeyboardInterrupt, SystemExit):
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise WaitStorageError(f"{QUOTA_WAIT_FILENAME} is not valid JSON: {exc}") from exc
    except Exception as exc:
        raise WaitStorageError(f"{QUOTA_WAIT_FILENAME} is not valid JSON: {exc}") from exc

    if type(record) is not dict:
        raise WaitStorageError(
            f"{QUOTA_WAIT_FILENAME} must be a JSON object, got {type(record).__name__}"
        )

    try:
        return validate_initial_wait_record(record, expected)
    except (KeyboardInterrupt, SystemExit):
        raise
    except WaitRecordError as exc:
        raise WaitStorageError(f"initial wait record validation failed: {exc}") from exc
    except Exception as exc:
        raise WaitStorageError(
            f"unexpected error validating initial wait record: {exc}"
        ) from exc



def _health_canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _read_unknown_health_checks_unframed(root: Path, controller_run_id: str, expected: object) -> list[dict]:
    return _read_unknown_health_state(root, controller_run_id, expected)[0]


def read_unknown_health_checks(root: Path, controller_run_id: str, expected: object) -> list[dict]:
    from scripts.two_site_quota_retention import _Pins, _run_relative
    try:
        with _Pins() as pins:
            pins.read(root, _run_relative(controller_run_id, QUOTA_WAIT_FILENAME), MAX_WAIT_RECORD_BYTES)
            return _read_unknown_health_checks_unframed(root, controller_run_id, expected)
    except (KeyboardInterrupt, SystemExit, WaitStorageError):
        raise
    except Exception as exc:
        raise WaitStorageError("invalid UNKNOWN health custody or record") from exc


def publish_unknown_health_check(root: Path, controller_run_id: str,
                                 expected: object, checked_at_utc: str) -> dict:
    """Supervisor-owned one-observation append or SAME-ordinal reconciliation.

    Read-only Monitor/inspection never calls the reconciliation helper.
    """
    import hashlib
    from scripts.two_site_quota_wait_due import evaluate_initial_wait_check
    _check_unknown_stop(root)
    wait = read_initial_wait_record(root, controller_run_id, expected)
    checks, pending = _read_unknown_health_state(root, controller_run_id, expected)
    if pending is not None:
        from scripts.two_site_quota_wait_schedule import _parse_utc_iso
        try:
            actual_now = _parse_utc_iso(checked_at_utc)
            pending_at = _parse_utc_iso(pending["record"]["checked_at_utc"])
        except ValueError as exc:
            raise WaitStorageError("invalid UNKNOWN reconciliation clock") from exc
        if actual_now < pending_at:
            raise WaitStorageError("regressed UNKNOWN reconciliation clock")
        try:
            return _reconcile_unknown_health_pending(root, controller_run_id, expected, pending)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            observed, unresolved = _read_unknown_health_state(root, controller_run_id, expected)
            if isinstance(exc, UnknownHealthPublicationPending) and unresolved is not None:
                raise UnknownHealthPublicationPending("same UNKNOWN ordinal awaits durability") from exc
            if isinstance(exc, UnknownHealthDurableCleanupError) and observed == checks + [pending["record"]]:
                _check_unknown_stop(root)
                return pending["record"]
            raise
    due = evaluate_initial_wait_check(wait, expected, checked_at_utc, health_checks=checks)
    if due["state"] != "UNKNOWN_BACKOFF_REVIEW_DUE" or len(checks) >= 5:
        raise WaitStorageError("UNKNOWN health check is not due or is exhausted")
    raw_wait = read_initial_wait_record_bytes(root, controller_run_id)
    ordinal = len(checks) + 1
    previous = _health_canonical(checks[-1]) if checks else raw_wait
    record = {
        "schema_version": "1.0", "record_kind": "QUOTA_UNKNOWN_HEALTH_CHECK",
        "controller_run_id": controller_run_id,
        "wait_sha256": hashlib.sha256(raw_wait).hexdigest(), "ordinal": ordinal,
        "previous_sha256": hashlib.sha256(previous).hexdigest(),
        "checked_at_utc": checked_at_utc, "observation": "LOCAL_BINDINGS_UNCHANGED",
    }
    _check_unknown_stop(root)
    try:
        _publish_cycle_record_bytes(root, controller_run_id, _health_canonical(record),
                                    f"quota-unknown-health-{ordinal}.json")
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        observed, pending = _read_unknown_health_state(root, controller_run_id, expected)
        if isinstance(exc, UnknownHealthPublicationPending) and pending is not None:
            raise UnknownHealthPublicationPending("same UNKNOWN ordinal awaits durability") from exc
        # A single-link target is reachable only after the UNKNOWN-only FIRST
        # directory fsync. Cleanup/readback failure can be independently read.
        if not isinstance(exc, UnknownHealthDurableCleanupError) or observed != checks + [record]:
            raise
    _check_unknown_stop(root)
    if read_unknown_health_checks(root, controller_run_id, expected) != checks + [record]:
        raise WaitStorageError("ambiguous UNKNOWN health publication readback")
    return record


def _check_unknown_stop(root: Path) -> None:
    if os.path.lexists(root / "STOP") or os.path.lexists(root / ".local/two-site-refactor/autopilot/STOP"):
        raise WaitStorageError("STOP blocks UNKNOWN health publication")


class UnknownHealthPublicationPending(WaitStorageError):
    """One validated existing ordinal needs Supervisor-owned durability work."""


class UnknownHealthDurableCleanupError(WaitStorageError):
    """Only explicit post-target-durability cleanup fsync may be revalidated."""


def _health_leaf_stat(info, links):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != links
            or not 0 <= info.st_size <= 4096):
        raise WaitStorageError("unsafe UNKNOWN publication inode")


def _validate_health_row(raw, controller, ordinal, wait_sha, previous, prior_time):
    from scripts.two_site_quota_wait_schedule import _parse_utc_iso
    from datetime import timedelta
    value = unique_json(raw.decode("utf-8"))
    keys = {"schema_version", "record_kind", "controller_run_id", "wait_sha256",
            "ordinal", "previous_sha256", "checked_at_utc", "observation"}
    if (type(value) is not dict or set(value) != keys or _health_canonical(value) != raw
            or type(value["ordinal"]) is not int or value["ordinal"] != ordinal
            or value["schema_version"] != "1.0" or value["record_kind"] != "QUOTA_UNKNOWN_HEALTH_CHECK"
            or value["controller_run_id"] != controller or value["wait_sha256"] != wait_sha
            or value["previous_sha256"] != previous or value["observation"] != "LOCAL_BINDINGS_UNCHANGED"):
        raise WaitStorageError("invalid UNKNOWN health observation binding")
    checked = _parse_utc_iso(value["checked_at_utc"])
    if checked < prior_time + timedelta(minutes=(15, 30, 60, 120, 240)[ordinal - 1]):
        raise WaitStorageError("UNKNOWN observation predates its sequential due time")
    return value, checked


def _read_unknown_health_state(root, controller, expected):
    """Read only: a validated codec alias never contributes to the durable count."""
    import hashlib
    import re
    from scripts.two_site_quota_wait_schedule import _parse_utc_iso
    from scripts.two_site_quota_retention import _bounded_names
    wait = read_initial_wait_record(root, controller, expected)
    raw_wait = read_initial_wait_record_bytes(root, controller)
    checks = []
    previous = hashlib.sha256(raw_wait).hexdigest()
    prior_time = _parse_utc_iso(wait["schedule"]["created_at_utc"])
    with _open_existing_cycle_run_directory(root, controller) as (directory, parent_fd, verify):
        parent_before = _stat_fingerprint(os.fstat(parent_fd))
        names = _bounded_names(directory, 128, "UNKNOWN run")
        aliases = {}
        for name in names:
            if name.startswith("quota-unknown-health-") and not re.fullmatch(r"quota-unknown-health-[1-5]\.json", name):
                raise WaitStorageError("invalid UNKNOWN ordinal filename")
            if not name.startswith(".quota-unknown-health-"):
                continue
            match = re.fullmatch(r"\.quota-unknown-health-([1-5])\.json\.tmp\.([1-9][0-9]{0,19})", name)
            if match is None or int(match[1]) in aliases:
                raise WaitStorageError("duplicate or malformed UNKNOWN codec alias")
            aliases[int(match[1])] = name
        opened = []
        watched = []
        primary = None
        def leaf(name, links):
            before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            _health_leaf_stat(before, links)
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=parent_fd)
            opened.append(fd)
            watched.append((name, fd, _stat_fingerprint(before)))
            actual = os.fstat(fd)
            _health_leaf_stat(actual, links)
            if _stat_fingerprint(actual) != _stat_fingerprint(before):
                raise WaitStorageError("UNKNOWN descriptor identity changed")
            raw = _health_fd_bytes(fd)
            if len(raw) != before.st_size or len(raw) > 4096:
                raise WaitStorageError("UNKNOWN payload changed or exceeded bound")
            return raw, _stat_fingerprint(before), fd
        try:
            gap = False
            for ordinal in range(1, 6):
                target = f"quota-unknown-health-{ordinal}.json"
                present = target in names
                alias = aliases.get(ordinal)
                if not present and alias is None:
                    gap = True
                    continue
                if gap or wait["schedule"]["reset_kind"] != "QUOTA_RESET_UNKNOWN":
                    raise WaitStorageError("unexpected or noncontiguous UNKNOWN observation")
                if alias is not None:
                    links = 2 if present else 1
                    raw, fingerprint, fd = leaf(alias, links)
                    if present:
                        target_info = os.stat(target, dir_fd=parent_fd, follow_symlinks=False)
                        _health_leaf_stat(target_info, links)
                        if _stat_fingerprint(target_info) != fingerprint:
                            raise WaitStorageError("UNKNOWN alias and target are not the SAME inode")
                        watched.append((target, fd, fingerprint))
                    value, _ = _validate_health_row(raw, controller, ordinal,
                        hashlib.sha256(raw_wait).hexdigest(), previous, prior_time)
                    if any(f"quota-unknown-health-{n}.json" in names or n in aliases
                           for n in range(ordinal + 1, 6)):
                        raise WaitStorageError("later UNKNOWN record follows pending ordinal")
                    return checks, {"alias": alias, "target": target, "raw": raw,
                                    "fingerprint": fingerprint, "target_present": present,
                                    "record": value}
                raw, _, _ = leaf(target, 1)
                value, prior_time = _validate_health_row(raw, controller, ordinal,
                    hashlib.sha256(raw_wait).hexdigest(), previous, prior_time)
                checks.append(value)
                previous = hashlib.sha256(raw).hexdigest()
            return checks, None
        except BaseException as exc:
            primary = exc
            raise
        finally:
            failure = None
            try:
                for name, fd, fingerprint in watched:
                    if (_stat_fingerprint(os.fstat(fd)) != fingerprint
                            or _stat_fingerprint(os.stat(name, dir_fd=parent_fd,
                                                        follow_symlinks=False)) != fingerprint):
                        raise WaitStorageError("UNKNOWN publication custody changed")
                if (_stat_fingerprint(os.fstat(parent_fd)) != parent_before
                        or read_initial_wait_record_bytes(root, controller) != raw_wait):
                    raise WaitStorageError("UNKNOWN run or wait custody changed")
                verify()
            except BaseException as exc:
                failure = exc
            for fd in opened:
                try: os.close(fd)
                except BaseException as exc: failure = failure or exc
            if failure is not None:
                if primary is not None: raise failure from primary
                raise failure


def _reconcile_unknown_health_pending(root, controller, expected, pending):
    """Caller is the existing Supervisor owner. Never called by observation."""
    _check_unknown_stop(root)
    prefix, current = _read_unknown_health_state(root, controller, expected)
    if current is None or current != pending:
        raise WaitStorageError("pending UNKNOWN ordinal changed before reconciliation")
    with _open_existing_cycle_run_directory(root, controller) as (_, parent_fd, verify):
        alias, target = current["alias"], current["target"]
        fd = os.open(alias, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=parent_fd)
        primary = None
        try:
            before = _stat_fingerprint(os.fstat(fd))
            if before != current["fingerprint"]:
                raise WaitStorageError("pending UNKNOWN alias descriptor drift")
            if _stat_fingerprint(os.stat(alias, dir_fd=parent_fd, follow_symlinks=False)) != before:
                raise WaitStorageError("pending UNKNOWN alias path drift")
            _check_unknown_stop(root)
            if not current["target_present"]:
                os.link(alias, target, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
            expected_links = 2
            for name in (alias, target):
                info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                _health_leaf_stat(info, expected_links)
                if (info.st_dev, info.st_ino) != before[:2]:
                    raise WaitStorageError("pending UNKNOWN link identity drift")
            linked = _stat_fingerprint(os.fstat(fd))
            def verify_publication():
                _health_leaf_stat(os.fstat(fd), 2)
                if _stat_fingerprint(os.fstat(fd)) != linked:
                    raise WaitStorageError("pending UNKNOWN descriptor custody changed")
                for name in (alias, target):
                    if _stat_fingerprint(os.stat(name, dir_fd=parent_fd, follow_symlinks=False)) != linked:
                        raise WaitStorageError("pending UNKNOWN path custody changed")
                os.lseek(fd, 0, os.SEEK_SET)
                if _health_fd_bytes(fd) != pending["raw"] or _stat_fingerprint(os.fstat(fd)) != linked:
                    raise WaitStorageError("pending UNKNOWN bytes changed")
                verify()
            verify_publication()
            os.fsync(fd)
            verify_publication()
            _check_unknown_stop(root)
            try:
                os.fsync(parent_fd)  # Required target durability; alias MUST still exist.
            except OSError as exc:
                raise UnknownHealthPublicationPending("UNKNOWN required target directory fsync failed") from exc
            verify_publication()
            _check_unknown_stop(root)
            os.unlink(alias, dir_fd=parent_fd)
            try:
                os.fsync(parent_fd)  # Alias cleanup only; target was already durable.
            except OSError as exc:
                raise UnknownHealthDurableCleanupError("UNKNOWN durable target cleanup fsync failed") from exc
            verify()
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try: os.close(fd)
            except BaseException as exc:
                if primary is not None: raise exc from primary
                raise
    _check_unknown_stop(root)
    observed, remainder = _read_unknown_health_state(root, controller, expected)
    if remainder is not None or observed != prefix + [pending["record"]]:
        raise WaitStorageError("UNKNOWN same-ordinal reconciliation readback mismatch")
    return pending["record"]


def _health_fd_bytes(fd):
    chunks = []
    total = 0
    while total <= 4096:
        data = os.read(fd, 4097 - total)
        if not data:
            break
        chunks.append(data)
        total += len(data)
    return b"".join(chunks)
