#!/usr/bin/env python3
"""Publish and relay the live coin-inference Snapshot for production.

The canonical Market Store is opened read-only by the existing Snapshot
publisher.  This command writes only into a dedicated production runtime
directory, validates freshness and content before every promotion, and uses an
atomic rename for both the local and remote consumer artifact.  It never starts
a collector, trains a model, or writes a product database.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import signal
import stat
import subprocess
import sys
import time
from typing import Iterator, Mapping, Sequence
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.market_contracts import normalize_utc
from core.market_intelligence.market_snapshot import (
    AtomicMarketSnapshotProvider,
    MarketSnapshotError,
    MarketSnapshotUnavailable,
    publish_market_snapshot_atomically,
)
from core.market_intelligence.snapshot_publisher import (
    MarketSnapshotPublisherError,
    publish_rate_ready_snapshot,
)
from scripts.check_production_coin_inference_readiness import (
    SAFE_NO_DATA_CONTEXT_KEY,
    SAFE_NO_DATA_CONTEXT_VERSION,
    safe_no_data_snapshot_assessment,
    safe_no_data_source_assessment,
    _source_probe,
    ProductionInferenceReadinessError,
)


PRODUCTION_CONFIRMATION = "publish-production-coin-inference-snapshot"
PRODUCTION_MAXIMUM_AGE_SECONDS = 120
REMOTE_TRANSPORT_TIMEOUT_SECONDS = 30
REMOTE_CLEANUP_TIMEOUT_SECONDS = 10
REMOTE_PROCESS_TERMINATION_GRACE_SECONDS = 2
REMOTE_PROCESS_GROUP_POLL_INTERVAL_SECONDS = 0.05
_REMOTE_HOST_PATTERN = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_-]*@)?[A-Za-z0-9][A-Za-z0-9.-]*$"
)
_REMOTE_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9._/-]+$")
_LOCAL_TRANSACTION_MARKER_NAME = ".production-snapshot-relay-transaction.json"


class ProductionSnapshotRelayError(RuntimeError):
    """A production Snapshot safety, freshness, or transport gate failed."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _emit(*, status: str, **payload: object) -> None:
    # Paths, hosts, command lines, and exception details are deliberately not
    # emitted.  The result is safe to retain as an operational health record.
    print(
        json.dumps(
            {"status": status, **payload},
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )


def _confirm(value: str) -> None:
    if value != PRODUCTION_CONFIRMATION:
        raise ProductionSnapshotRelayError("production_confirmation_required")


def _has_production_scope(path: Path) -> bool:
    return any("production" in part.lower() for part in path.parts)


def _root(value: str, *, field: str, must_exist: bool = True) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_absolute() or not _has_production_scope(root):
        raise ProductionSnapshotRelayError(f"{field}_root_not_production")
    if any("staging" in part.lower() for part in root.parts):
        raise ProductionSnapshotRelayError(f"{field}_root_contains_staging")
    if must_exist and not root.is_dir():
        raise ProductionSnapshotRelayError(f"{field}_root_unavailable")
    return root


def _file_inside(root: Path, value: str, *, field: str) -> Path:
    supplied = Path(value).expanduser()
    candidate = supplied.resolve() if supplied.is_absolute() else (root / supplied).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ProductionSnapshotRelayError(f"{field}_outside_root") from exc
    if candidate == root:
        raise ProductionSnapshotRelayError(f"{field}_must_be_file")
    if any("staging" in part.lower() for part in candidate.parts):
        raise ProductionSnapshotRelayError(f"{field}_contains_staging")
    return candidate


def _digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _terminate_process_group(
    process: subprocess.Popen[object],
    *,
    process_group_id: int | None = None,
) -> None:
    """Stop the whole SSH/SCP process tree before rollback may begin.

    ``Popen.poll()`` only describes the group leader.  A descendant can keep a
    captured pipe open after that leader exits, so timeout cleanup must always
    target the process group ID captured at spawn time and must itself remain
    bounded.
    """

    group_id = int(process_group_id or process.pid)
    try:
        os.killpg(group_id, signal.SIGTERM)
    except ProcessLookupError:
        pass
    communicate_complete = False
    try:
        process.communicate(timeout=REMOTE_PROCESS_TERMINATION_GRACE_SECONDS)
        communicate_complete = True
    except subprocess.TimeoutExpired:
        pass

    # A completed leader/pipe does not prove that every descendant exited.
    # Probe the captured group itself before deciding whether SIGKILL is needed.
    if _process_group_has_live_members(group_id):
        try:
            os.killpg(group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if not communicate_complete:
        try:
            process.communicate(timeout=REMOTE_PROCESS_TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired as exc:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            raise ProductionSnapshotRelayError(
                "remote_process_group_not_stopped"
            ) from exc
    if process.poll() is None or not _wait_for_process_group_exit(
        group_id,
        timeout=REMOTE_PROCESS_TERMINATION_GRACE_SECONDS,
    ):
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        raise ProductionSnapshotRelayError("remote_process_group_not_stopped")


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(int(process_group_id), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_group_has_live_members(process_group_id: int) -> bool:
    """Return whether the group contains a non-zombie process."""

    group_id = int(process_group_id)
    if not _process_group_exists(group_id):
        return False
    try:
        entries = tuple(Path("/proc").iterdir())
    except OSError:
        return True
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat_text = (entry / "stat").read_text(encoding="utf-8")
            suffix = stat_text[stat_text.rindex(") ") + 2 :].split()
            state = suffix[0]
            member_group = int(suffix[2])
        except (OSError, IndexError, ValueError):
            continue
        if member_group == group_id and state != "Z":
            return True
    return False


def _wait_for_process_group_exit(process_group_id: int, *, timeout: float) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout))
    while _process_group_has_live_members(process_group_id):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(REMOTE_PROCESS_GROUP_POLL_INTERVAL_SECONDS, remaining))
    return True


def _run_bounded(
    command: Sequence[str],
    *,
    timeout: int,
    check: bool,
    text: bool = False,
    capture_output: bool = False,
    stdout: int | None = None,
    stderr: int | None = None,
) -> subprocess.CompletedProcess[object]:
    """Run one transport command with descendant containment and a hard bound."""

    if capture_output:
        if stdout is not None or stderr is not None:
            raise ValueError("capture_output_conflicts_with_streams")
        stdout = subprocess.PIPE
        stderr = subprocess.PIPE
    process = subprocess.Popen(
        list(command),
        text=text,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )
    process_group_id = process.pid
    try:
        output, error = process.communicate(timeout=max(1, int(timeout)))
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(process, process_group_id=process_group_id)
        raise ProductionSnapshotRelayError("remote_transport_timeout") from exc
    except BaseException:
        _terminate_process_group(process, process_group_id=process_group_id)
        raise
    if _process_group_has_live_members(process_group_id):
        _terminate_process_group(process, process_group_id=process_group_id)
        raise ProductionSnapshotRelayError(
            "remote_process_group_survived_normal_exit"
        )
    completed = subprocess.CompletedProcess(
        list(command),
        int(process.returncode or 0),
        output,
        error,
    )
    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            completed.args,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed


def _validated_snapshot(
    path: Path,
    *,
    maximum_age_seconds: int,
    expected_digest: str | None = None,
) -> Mapping[str, object]:
    if maximum_age_seconds != PRODUCTION_MAXIMUM_AGE_SECONDS:
        raise ProductionSnapshotRelayError("maximum_age_seconds_invalid")
    if expected_digest is not None:
        normalized_digest = expected_digest.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized_digest):
            raise ProductionSnapshotRelayError("expected_digest_invalid")
        if _digest(path) != normalized_digest:
            raise ProductionSnapshotRelayError("snapshot_digest_mismatch")
    try:
        snapshot = AtomicMarketSnapshotProvider(path).load()
    except MarketSnapshotUnavailable as exc:
        raise ProductionSnapshotRelayError("snapshot_unavailable") from exc
    generated = datetime.fromisoformat(
        normalize_utc(
            str(snapshot.get("generated_at_utc") or ""),
            field_name="production_snapshot_generated_at_utc",
        ).replace("Z", "+00:00")
    )
    age = (_utc_now() - generated).total_seconds()
    if age < 0 or age > maximum_age_seconds:
        raise ProductionSnapshotRelayError("snapshot_stale_or_future")
    rates = snapshot.get("rates")
    if not isinstance(rates, Mapping):
        raise ProductionSnapshotRelayError("snapshot_rates_unavailable")
    estimated_count = int(rates.get("estimated_count") or 0)
    if estimated_count <= 0 and not safe_no_data_snapshot_assessment(snapshot):
        raise ProductionSnapshotRelayError("snapshot_has_no_estimated_rates")
    if expected_digest is not None and _digest(path) != expected_digest.lower():
        raise ProductionSnapshotRelayError("snapshot_digest_mismatch")
    return snapshot


def _bind_safe_no_data_context(
    path: Path,
    *,
    source_assessment: Mapping[str, object],
) -> str:
    """Bind a no-price Snapshot to the exact bounded production source gate."""

    if not safe_no_data_source_assessment(source_assessment):
        raise ProductionSnapshotRelayError("safe_no_data_source_not_eligible")
    try:
        snapshot = dict(AtomicMarketSnapshotProvider(path).load())
    except MarketSnapshotUnavailable as exc:
        raise ProductionSnapshotRelayError("snapshot_unavailable") from exc
    rates = snapshot.get("rates")
    if (
        snapshot.get("snapshot_status") != "NO_DATA_COIN_RATE_STATE"
        or not isinstance(rates, Mapping)
        or int(rates.get("estimated_count") or 0) != 0
        or int(rates.get("no_data_count") or 0) <= 0
    ):
        raise ProductionSnapshotRelayError("safe_no_data_snapshot_invalid")
    snapshot[SAFE_NO_DATA_CONTEXT_KEY] = {
        "contract_version": SAFE_NO_DATA_CONTEXT_VERSION,
        "source_status": "DEGRADED_GUARD_FAIL_OPEN",
        "source_reason": source_assessment["degradation_reason"],
        "group_inputs_within_hot_retention": True,
        "private_input_within_hot_retention": True,
        "collector_checkpoint_count": 3,
        "price_authority": False,
    }
    try:
        digest = publish_market_snapshot_atomically(path, snapshot)
    except MarketSnapshotError as exc:
        raise ProductionSnapshotRelayError("safe_no_data_snapshot_invalid") from exc
    if not safe_no_data_snapshot_assessment(snapshot):
        raise ProductionSnapshotRelayError("safe_no_data_context_invalid")
    return digest


@contextmanager
def _single_writer_lock(snapshot_path: Path) -> Iterator[None]:
    lock_path = snapshot_path.parent / ".production-snapshot-relay.lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ProductionSnapshotRelayError("snapshot_lock_unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid not in {0, os.geteuid()}
            or metadata.st_nlink != 1
        ):
            raise ProductionSnapshotRelayError("snapshot_lock_invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProductionSnapshotRelayError("snapshot_relay_busy") from exc
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _atomic_promote(candidate: Path, destination: Path) -> None:
    if candidate.parent != destination.parent:
        raise ProductionSnapshotRelayError("candidate_not_beside_destination")
    expected_prefix = f".{destination.name}.relay-"
    if not candidate.name.startswith(expected_prefix) or not candidate.name.endswith(".tmp"):
        raise ProductionSnapshotRelayError("candidate_name_invalid")
    descriptor = os.open(candidate, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(candidate, 0o644)
    os.replace(candidate, destination)
    directory_descriptor = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _exclusive_copy(source: Path, destination: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    output = os.open(destination, flags, 0o600)
    try:
        with source.open("rb") as reader, os.fdopen(output, "wb", closefd=False) as writer:
            for block in iter(lambda: reader.read(128 * 1024), b""):
                writer.write(block)
            writer.flush()
            os.fsync(writer.fileno())
    finally:
        os.close(output)
    _fsync_directory(destination.parent)


def _write_local_transaction_marker(
    marker: Path,
    *,
    phase: str,
    prior_digest: str | None,
    new_digest: str,
    backup_name: str | None,
) -> None:
    if phase not in {"prepared", "local_committed", "remote_committed"}:
        raise ProductionSnapshotRelayError("local_transaction_phase_invalid")
    payload = {
        "schema_version": 1,
        "status": "relay_incomplete",
        "phase": phase,
        "prior_digest": prior_digest,
        "new_digest": new_digest,
        "backup_name": backup_name,
        "recovery_action": "restore_exact_prior_local_snapshot_then_rerun_relay",
        "secrets_disclosed": False,
    }
    candidate = marker.with_name(f".{marker.name}.{uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(candidate, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    os.replace(candidate, marker)
    _fsync_directory(marker.parent)


def _read_local_transaction_marker(marker: Path) -> Mapping[str, object]:
    metadata = marker.lstat()
    if (
        marker.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid not in {0, os.geteuid()}
        or metadata.st_nlink != 1
    ):
        raise ProductionSnapshotRelayError("local_transaction_marker_invalid")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProductionSnapshotRelayError("local_transaction_marker_invalid") from exc
    required = {
        "schema_version", "status", "phase", "prior_digest", "new_digest",
        "backup_name", "recovery_action", "secrets_disclosed",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != required
        or payload["schema_version"] != 1
        or payload["status"] != "relay_incomplete"
        or payload["phase"] not in {"prepared", "local_committed", "remote_committed"}
        or payload["recovery_action"] != "restore_exact_prior_local_snapshot_then_rerun_relay"
        or payload["secrets_disclosed"] is not False
        or not re.fullmatch(r"[0-9a-f]{64}", str(payload["new_digest"]))
        or (
            payload["prior_digest"] is not None
            and not re.fullmatch(r"[0-9a-f]{64}", str(payload["prior_digest"]))
        )
    ):
        raise ProductionSnapshotRelayError("local_transaction_marker_invalid")
    backup_name = payload["backup_name"]
    if payload["prior_digest"] is None:
        if backup_name is not None:
            raise ProductionSnapshotRelayError("local_transaction_marker_invalid")
    elif (
        not isinstance(backup_name, str)
        or not re.fullmatch(r"\.[A-Za-z0-9._-]+\.rollback-[0-9a-f]{32}\.bak", backup_name)
    ):
        raise ProductionSnapshotRelayError("local_transaction_marker_invalid")
    return payload


def _clear_local_transaction(marker: Path, backup: Path | None) -> None:
    if backup is not None:
        backup.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
    _fsync_directory(marker.parent)


def _restore_local_transaction(
    snapshot_path: Path,
    marker: Path,
    *,
    remote_digest: str | None | object = Ellipsis,
) -> None:
    payload = _read_local_transaction_marker(marker)
    prior_digest = payload["prior_digest"]
    new_digest = str(payload["new_digest"])
    backup_name = payload["backup_name"]
    backup = snapshot_path.parent / str(backup_name) if backup_name is not None else None
    if backup is not None and not backup.name.startswith(f".{snapshot_path.name}.rollback-"):
        raise ProductionSnapshotRelayError("local_transaction_marker_invalid")
    current_digest = _digest(snapshot_path) if snapshot_path.is_file() else None
    if current_digest not in {prior_digest, new_digest}:
        raise ProductionSnapshotRelayError("local_transaction_snapshot_drift")
    if payload["phase"] == "remote_committed":
        if current_digest != new_digest:
            raise ProductionSnapshotRelayError("local_transaction_commit_drift")
        _clear_local_transaction(marker, backup)
        return
    if payload["phase"] == "local_committed":
        if remote_digest is Ellipsis:
            raise ProductionSnapshotRelayError("remote_reconcile_digest_required")
        if remote_digest == new_digest:
            if current_digest != new_digest:
                raise ProductionSnapshotRelayError("local_transaction_commit_drift")
            _clear_local_transaction(marker, backup)
            return
        if remote_digest != prior_digest:
            raise ProductionSnapshotRelayError("remote_transaction_snapshot_drift")
    if prior_digest is None:
        snapshot_path.unlink(missing_ok=True)
    elif current_digest != prior_digest:
        if backup is None or not backup.is_file() or backup.is_symlink() or _digest(backup) != prior_digest:
            raise ProductionSnapshotRelayError("local_transaction_backup_invalid")
        os.replace(backup, snapshot_path)
        os.chmod(snapshot_path, 0o644)
    if prior_digest is not None and (
        not snapshot_path.is_file() or _digest(snapshot_path) != prior_digest
    ):
        raise ProductionSnapshotRelayError("local_transaction_rollback_failed")
    _clear_local_transaction(marker, backup)


@contextmanager
def _defer_termination_during_transaction() -> Iterator[None]:
    pending: list[int] = []
    previous: dict[int, object] = {}

    def defer(signum: int, _frame: object) -> None:
        pending.append(signum)

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, defer)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    if pending:
        raise ProductionSnapshotRelayError("termination_deferred_until_relay_transaction_safe")


def _validate_remote_absolute_path(
    value: str,
    *,
    field: str,
    require_production: bool = False,
) -> PurePosixPath:
    if (
        not value
        or len(value) > 4096
        or not _REMOTE_PATH_PATTERN.fullmatch(value)
        or "%" in value
        or "//" in value
    ):
        raise ProductionSnapshotRelayError(f"{field}_invalid")
    raw_parts = value.split("/")[1:]
    if not raw_parts or any(part in {"", ".", ".."} for part in raw_parts):
        raise ProductionSnapshotRelayError(f"{field}_invalid")
    path = PurePosixPath(value)
    if str(path) != value or any("staging" in part.lower() for part in path.parts):
        raise ProductionSnapshotRelayError(f"{field}_invalid")
    if require_production and not any("production" in part.lower() for part in path.parts):
        raise ProductionSnapshotRelayError(f"{field}_invalid")
    return path


def _validate_remote_transport(host: str, port: int, project_dir: str) -> PurePosixPath:
    if (
        not host
        or len(host) > 320
        or host.startswith("-")
        or not _REMOTE_HOST_PATTERN.fullmatch(host)
        or not 1 <= int(port) <= 65535
    ):
        raise ProductionSnapshotRelayError("remote_transport_invalid")
    hostname = host.rsplit("@", 1)[-1]
    if len(hostname) > 253 or any(
        not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
        for label in hostname.split(".")
    ):
        raise ProductionSnapshotRelayError("remote_transport_invalid")
    return _validate_remote_absolute_path(project_dir, field="remote_project_dir")


def _validate_remote_snapshot_contract(
    remote_runtime_root: str,
    remote_snapshot: str,
) -> tuple[PurePosixPath, PurePosixPath]:
    remote_root = _validate_remote_absolute_path(
        remote_runtime_root,
        field="remote_runtime_root",
        require_production=True,
    )
    remote_path = _validate_remote_absolute_path(
        remote_snapshot,
        field="remote_snapshot",
        require_production=True,
    )
    if remote_path.parent != remote_root:
        raise ProductionSnapshotRelayError("remote_snapshot_parent_invalid")
    return remote_root, remote_path


def _validate_remote_identity_file(value: str | None) -> Path:
    if not value:
        raise ProductionSnapshotRelayError("remote_identity_file_required")
    supplied = Path(value)
    resolved = supplied.resolve()
    metadata = supplied.lstat()
    if (
        not supplied.is_absolute()
        or supplied != resolved
        or supplied.is_symlink()
        or not supplied.is_file()
        or stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}
        or metadata.st_uid not in {0, os.geteuid()}
        or metadata.st_nlink != 1
    ):
        raise ProductionSnapshotRelayError("remote_identity_file_invalid")
    return supplied


def _remote_snapshot_digest(
    *,
    remote_host: str,
    remote_port: int,
    remote_runtime_root: str,
    remote_snapshot: str,
    remote_project_dir: str,
    remote_identity_file: str | None,
) -> str | None:
    _validate_remote_transport(remote_host, remote_port, remote_project_dir)
    identity_file = _validate_remote_identity_file(remote_identity_file)
    _remote_root, remote_path = _validate_remote_snapshot_contract(
        remote_runtime_root, remote_snapshot
    )
    ssh = [
        "ssh", "-p", str(remote_port),
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "ConnectTimeout=10",
        "-i", str(identity_file), remote_host,
    ]
    path = shlex.quote(str(remote_path))
    result = _run_bounded(
        [
            *ssh,
            f"if test -f {path} && test ! -L {path}; then sha256sum {path} | awk '{{print $1}}'; "
            f"elif test ! -e {path}; then printf 'absent\\n'; else exit 42; fi",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=REMOTE_TRANSPORT_TIMEOUT_SECONDS,
    )
    rendered = (result.stdout or "").strip()
    if rendered == "absent":
        return None
    if not re.fullmatch(r"[0-9a-f]{64}", rendered):
        raise ProductionSnapshotRelayError("remote_snapshot_digest_invalid")
    return rendered


def _relay_remote(
    local_snapshot: Path,
    *,
    remote_host: str,
    remote_port: int,
    remote_runtime_root: str,
    remote_snapshot: str,
    remote_project_dir: str,
    maximum_age_seconds: int,
    digest: str,
    remote_identity_file: str | None = None,
) -> None:
    project = _validate_remote_transport(remote_host, remote_port, remote_project_dir)
    identity_file = _validate_remote_identity_file(remote_identity_file)
    remote_root, remote_path = _validate_remote_snapshot_contract(
        remote_runtime_root,
        remote_snapshot,
    )
    candidate = remote_path.with_name(f".{remote_path.name}.relay-{uuid4().hex}.tmp")
    ssh = [
        "ssh",
        "-p",
        str(remote_port),
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "ConnectTimeout=10",
    ]
    ssh.extend(("-i", str(identity_file)))
    ssh.append(remote_host)
    _run_bounded(
        [*ssh, f"install -d -m 0755 -- {shlex.quote(str(remote_root))}"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=REMOTE_TRANSPORT_TIMEOUT_SECONDS,
    )
    try:
        _run_bounded(
            [
                "scp",
                "-P",
                str(remote_port),
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "PasswordAuthentication=no",
                "-o",
                "KbdInteractiveAuthentication=no",
                "-o",
                "ConnectTimeout=10",
                "-i",
                str(identity_file),
                str(local_snapshot),
                f"{remote_host}:{candidate}",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=REMOTE_TRANSPORT_TIMEOUT_SECONDS,
        )
        remote_command = " ".join(
            [
                "python3",
                shlex.quote(str(project / "scripts/relay_production_coin_inference_snapshot.py")),
                "install-relayed",
                "--environment",
                "production",
                "--production-confirmation",
                shlex.quote(PRODUCTION_CONFIRMATION),
                "--runtime-root",
                shlex.quote(str(remote_root)),
                "--candidate",
                shlex.quote(str(candidate)),
                "--snapshot",
                shlex.quote(str(remote_path)),
                "--expected-sha256",
                digest,
                "--maximum-age-seconds",
                str(maximum_age_seconds),
            ]
        )
        _run_bounded(
            [*ssh, remote_command],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=REMOTE_TRANSPORT_TIMEOUT_SECONDS,
        )
    except BaseException:
        try:
            _run_bounded(
                [*ssh, f"rm -f -- {shlex.quote(str(candidate))}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=REMOTE_CLEANUP_TIMEOUT_SECONDS,
            )
        except BaseException:
            # The random candidate is never promoted without the guarded
            # remote installer.  Preserve the primary failure and let the next
            # deterministic run reconcile the committed digest.
            pass
        raise


def _publish_and_relay(args: argparse.Namespace) -> int:
    source_root = _root(args.source_root, field="source")
    store = _file_inside(source_root, args.market_store, field="market_store")
    runtime_root = _root(args.runtime_root, field="runtime")
    snapshot_path = _file_inside(runtime_root, args.snapshot, field="snapshot")
    if source_root == runtime_root or source_root in runtime_root.parents or runtime_root in source_root.parents:
        raise ProductionSnapshotRelayError("source_and_runtime_roots_not_separate")
    if not store.is_file():
        raise ProductionSnapshotRelayError("market_store_unavailable")
    if snapshot_path.parent != runtime_root or not snapshot_path.parent.is_dir():
        raise ProductionSnapshotRelayError("snapshot_parent_invalid")
    remote_values = (
        args.remote_host,
        args.remote_runtime_root,
        args.remote_snapshot,
        args.remote_project_dir,
    )
    if any(remote_values) and not all(remote_values):
        raise ProductionSnapshotRelayError("remote_arguments_incomplete")
    if args.remote_identity_file and not all(remote_values):
        raise ProductionSnapshotRelayError("remote_arguments_incomplete")
    if all(remote_values):
        _validate_remote_transport(args.remote_host, args.remote_port, args.remote_project_dir)
        _validate_remote_snapshot_contract(args.remote_runtime_root, args.remote_snapshot)
        _validate_remote_identity_file(args.remote_identity_file)

    with _single_writer_lock(snapshot_path):
        marker = snapshot_path.parent / _LOCAL_TRANSACTION_MARKER_NAME
        if marker.exists() or marker.is_symlink():
            pending = _read_local_transaction_marker(marker)
            if pending["phase"] == "local_committed":
                if not all(remote_values):
                    raise ProductionSnapshotRelayError("remote_reconcile_arguments_required")
                remote_digest = _remote_snapshot_digest(
                    remote_host=args.remote_host,
                    remote_port=args.remote_port,
                    remote_runtime_root=args.remote_runtime_root,
                    remote_snapshot=args.remote_snapshot,
                    remote_project_dir=args.remote_project_dir,
                    remote_identity_file=args.remote_identity_file,
                )
                _restore_local_transaction(
                    snapshot_path, marker, remote_digest=remote_digest
                )
            else:
                _restore_local_transaction(snapshot_path, marker)
        candidate = snapshot_path.with_name(
            f".{snapshot_path.name}.relay-{uuid4().hex}.tmp"
        )
        watermark = candidate.with_name(f".{candidate.name}.input-watermark.json")
        try:
            result = publish_rate_ready_snapshot(
                market_store_path=store,
                snapshot_path=candidate,
                watermark_path=watermark,
            )
            bound_no_data_digest: str | None = None
            if result.status not in {"PUBLISHED", "UNCHANGED"}:
                try:
                    source_assessment = _source_probe(store, now=_utc_now())
                except ProductionInferenceReadinessError as exc:
                    raise ProductionSnapshotRelayError(
                        "snapshot_not_rate_ready"
                    ) from exc
                if not safe_no_data_source_assessment(source_assessment):
                    raise ProductionSnapshotRelayError("snapshot_not_rate_ready")
                result = publish_rate_ready_snapshot(
                    market_store_path=store,
                    snapshot_path=candidate,
                    watermark_path=watermark,
                    publish_no_data_snapshot=True,
                )
                if result.status != "PUBLISHED_NO_DATA":
                    raise ProductionSnapshotRelayError("snapshot_not_rate_ready")
                bound_no_data_digest = _bind_safe_no_data_context(
                    candidate,
                    source_assessment=source_assessment,
                )
            snapshot = _validated_snapshot(
                candidate,
                maximum_age_seconds=args.maximum_age_seconds,
            )
            os.chmod(candidate, 0o644)
            digest = _digest(candidate)
            if bound_no_data_digest is not None and bound_no_data_digest != digest:
                raise ProductionSnapshotRelayError("published_snapshot_digest_mismatch")
            if (
                bound_no_data_digest is None
                and result.snapshot_digest
                and result.snapshot_digest != digest
            ):
                raise ProductionSnapshotRelayError("published_snapshot_digest_mismatch")

            if all(remote_values):
                prior_digest = _digest(snapshot_path) if snapshot_path.is_file() else None
                remote_prior_digest = _remote_snapshot_digest(
                    remote_host=args.remote_host,
                    remote_port=args.remote_port,
                    remote_runtime_root=args.remote_runtime_root,
                    remote_snapshot=args.remote_snapshot,
                    remote_project_dir=args.remote_project_dir,
                    remote_identity_file=args.remote_identity_file,
                )
                if remote_prior_digest != prior_digest:
                    raise ProductionSnapshotRelayError("preexisting_snapshot_parity_mismatch")
                backup = None
                if prior_digest is not None:
                    backup = snapshot_path.with_name(
                        f".{snapshot_path.name}.rollback-{uuid4().hex}.bak"
                    )
                    _exclusive_copy(snapshot_path, backup)
                    if _digest(backup) != prior_digest:
                        raise ProductionSnapshotRelayError("local_transaction_backup_invalid")
                _write_local_transaction_marker(
                    marker,
                    phase="prepared",
                    prior_digest=prior_digest,
                    new_digest=digest,
                    backup_name=backup.name if backup is not None else None,
                )
                try:
                    with _defer_termination_during_transaction():
                        _atomic_promote(candidate, snapshot_path)
                        _write_local_transaction_marker(
                            marker,
                            phase="local_committed",
                            prior_digest=prior_digest,
                            new_digest=digest,
                            backup_name=backup.name if backup is not None else None,
                        )
                        _relay_remote(
                            snapshot_path,
                            remote_host=args.remote_host,
                            remote_port=args.remote_port,
                            remote_runtime_root=args.remote_runtime_root,
                            remote_snapshot=args.remote_snapshot,
                            remote_project_dir=args.remote_project_dir,
                            maximum_age_seconds=args.maximum_age_seconds,
                            digest=digest,
                            remote_identity_file=args.remote_identity_file,
                        )
                        _write_local_transaction_marker(
                            marker,
                            phase="remote_committed",
                            prior_digest=prior_digest,
                            new_digest=digest,
                            backup_name=backup.name if backup is not None else None,
                        )
                        _clear_local_transaction(marker, backup)
                except BaseException:
                    if marker.exists() or marker.is_symlink():
                        payload = _read_local_transaction_marker(marker)
                        if payload["phase"] == "local_committed":
                            try:
                                remote_digest = _remote_snapshot_digest(
                                    remote_host=args.remote_host,
                                    remote_port=args.remote_port,
                                    remote_runtime_root=args.remote_runtime_root,
                                    remote_snapshot=args.remote_snapshot,
                                    remote_project_dir=args.remote_project_dir,
                                    remote_identity_file=args.remote_identity_file,
                                )
                                _restore_local_transaction(
                                    snapshot_path,
                                    marker,
                                    remote_digest=remote_digest,
                                )
                            except BaseException as recovery_error:
                                raise ProductionSnapshotRelayError(
                                    "relay_transaction_recovery_required"
                                ) from recovery_error
                        elif payload["phase"] != "remote_committed":
                            _restore_local_transaction(snapshot_path, marker)
                    raise
            else:
                _atomic_promote(candidate, snapshot_path)
            _validated_snapshot(
                snapshot_path,
                maximum_age_seconds=args.maximum_age_seconds,
                expected_digest=digest,
            )
        finally:
            candidate.unlink(missing_ok=True)
            watermark.unlink(missing_ok=True)
    rates = snapshot["rates"]
    safe_no_data = safe_no_data_snapshot_assessment(snapshot)
    _emit(
        status="PUBLISHED",
        snapshot_sha256=digest,
        generated_at_utc=snapshot.get("generated_at_utc"),
        estimated_rate_count=int(rates["estimated_count"]),
        snapshot_mode="SAFE_NO_DATA" if safe_no_data else "RATE_READY",
        price_authority=not safe_no_data,
        remote_relayed=bool(all(remote_values)),
    )
    return 0


def _install_relayed(args: argparse.Namespace) -> int:
    runtime_root = _root(args.runtime_root, field="runtime")
    candidate = _file_inside(runtime_root, args.candidate, field="candidate")
    snapshot_path = _file_inside(runtime_root, args.snapshot, field="snapshot")
    if snapshot_path.parent != runtime_root or candidate.parent != runtime_root:
        raise ProductionSnapshotRelayError("remote_snapshot_parent_invalid")
    with _single_writer_lock(snapshot_path):
        snapshot = _validated_snapshot(
            candidate,
            maximum_age_seconds=args.maximum_age_seconds,
            expected_digest=args.expected_sha256,
        )
        _atomic_promote(candidate, snapshot_path)
        _validated_snapshot(
            snapshot_path,
            maximum_age_seconds=args.maximum_age_seconds,
            expected_digest=args.expected_sha256,
        )
    _emit(
        status="INSTALLED",
        snapshot_sha256=args.expected_sha256.lower(),
        generated_at_utc=snapshot.get("generated_at_utc"),
        snapshot_mode=(
            "SAFE_NO_DATA"
            if safe_no_data_snapshot_assessment(snapshot)
            else "RATE_READY"
        ),
    )
    return 0


def _check(args: argparse.Namespace) -> int:
    runtime_root = _root(args.runtime_root, field="runtime")
    snapshot_path = _file_inside(runtime_root, args.snapshot, field="snapshot")
    snapshot = _validated_snapshot(
        snapshot_path,
        maximum_age_seconds=args.maximum_age_seconds,
        expected_digest=args.expected_sha256,
    )
    digest = _digest(snapshot_path)
    _emit(
        status="FRESH",
        snapshot_sha256=digest,
        generated_at_utc=snapshot.get("generated_at_utc"),
        estimated_rate_count=int(snapshot["rates"]["estimated_count"]),
        snapshot_mode=(
            "SAFE_NO_DATA"
            if safe_no_data_snapshot_assessment(snapshot)
            else "RATE_READY"
        ),
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--environment", required=True, choices=("production",))
        command.add_argument("--production-confirmation", required=True)
        command.add_argument("--maximum-age-seconds", type=int, default=120)

    publish = commands.add_parser("publish-relay")
    common(publish)
    publish.add_argument("--source-root", required=True)
    publish.add_argument("--market-store", required=True)
    publish.add_argument("--runtime-root", required=True)
    publish.add_argument("--snapshot", required=True)
    publish.add_argument("--remote-host")
    publish.add_argument("--remote-port", type=int, default=22)
    publish.add_argument("--remote-runtime-root")
    publish.add_argument("--remote-snapshot")
    publish.add_argument("--remote-project-dir")
    publish.add_argument("--remote-identity-file")
    publish.set_defaults(handler=_publish_and_relay)

    install = commands.add_parser("install-relayed")
    common(install)
    install.add_argument("--runtime-root", required=True)
    install.add_argument("--candidate", required=True)
    install.add_argument("--snapshot", required=True)
    install.add_argument("--expected-sha256", required=True)
    install.set_defaults(handler=_install_relayed)

    check = commands.add_parser("check")
    common(check)
    check.add_argument("--runtime-root", required=True)
    check.add_argument("--snapshot", required=True)
    check.add_argument("--expected-sha256")
    check.set_defaults(handler=_check)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _confirm(args.production_confirmation)
        if args.maximum_age_seconds != PRODUCTION_MAXIMUM_AGE_SECONDS:
            raise ProductionSnapshotRelayError("maximum_age_seconds_invalid")
        return int(args.handler(args))
    except (
        MarketSnapshotPublisherError,
        OSError,
        ProductionSnapshotRelayError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        # The reason is a bounded internal token, never the raw exception or a
        # command/path that might contain deployment details.
        reason = str(exc)
        if not isinstance(exc, ProductionSnapshotRelayError):
            reason = type(exc).__name__
        _emit(status="FAILED", reason=reason)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
