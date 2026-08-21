#!/usr/bin/env python3
"""Validate and atomically relay the live estimator Snapshot to staging.

The source is read-only.  The destination must be staging-scoped, and the
Snapshot is validated before both the local and optional remote atomic rename.
No collector, model, database, or production runtime is modified.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Iterator, Sequence
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.market_contracts import normalize_utc
from core.market_intelligence.market_snapshot import (
    AtomicMarketSnapshotProvider,
    MarketSnapshotUnavailable,
)


class StagingSnapshotRelayError(RuntimeError):
    """The relay failed a safety, freshness, or transport contract."""


def _inside(root_value: str, path_value: str, *, field: str) -> tuple[Path, Path]:
    root = Path(root_value).expanduser().resolve()
    path = Path(path_value).expanduser().resolve()
    if "staging" not in str(root).lower():
        raise StagingSnapshotRelayError(f"{field}_root_not_staging")
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise StagingSnapshotRelayError(f"{field}_outside_root") from exc
    if path == root:
        raise StagingSnapshotRelayError(f"{field}_must_be_file")
    return root, path


def _validated_snapshot(path: Path, *, maximum_age_seconds: int) -> dict[str, object]:
    try:
        snapshot = AtomicMarketSnapshotProvider(path).load()
    except MarketSnapshotUnavailable as exc:
        raise StagingSnapshotRelayError(f"snapshot_unavailable:{exc}") from exc
    generated = datetime.fromisoformat(
        normalize_utc(
            str(snapshot.get("generated_at_utc") or ""),
            field_name="staging_relay_snapshot_generated_at_utc",
        ).replace("Z", "+00:00")
    )
    age = (datetime.now(timezone.utc) - generated).total_seconds()
    if age < 0 or age > maximum_age_seconds:
        raise StagingSnapshotRelayError("snapshot_stale_or_future")
    rates = snapshot.get("rates")
    if not isinstance(rates, dict):
        raise StagingSnapshotRelayError("snapshot_rates_invalid")
    estimated_count = int(rates.get("estimated_count") or 0)
    no_data_count = int(rates.get("no_data_count") or 0)
    # A fresh, schema-valid NO_DATA artifact is an intentional staging state:
    # it keeps deployment and binding checks live outside market hours while
    # exposing no rate that inference or the price guard could use.  Empty or
    # malformed rate state remains a hard failure.
    if estimated_count < 0 or no_data_count < 0 or estimated_count + no_data_count <= 0:
        raise StagingSnapshotRelayError("snapshot_rate_state_empty")
    snapshot_status = str(snapshot.get("snapshot_status") or "")
    if estimated_count == 0:
        if snapshot_status != "NO_DATA_COIN_RATE_STATE":
            raise StagingSnapshotRelayError("snapshot_no_data_state_invalid")
    elif snapshot_status != "PARTIAL_COIN_RATE_STATE":
        raise StagingSnapshotRelayError("snapshot_rate_ready_state_invalid")
    return snapshot


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.relay-",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _relay_lock(destination: Path) -> Iterator[None]:
    destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    lock_path = destination.parent / ".snapshot-relay.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StagingSnapshotRelayError("snapshot_relay_busy") from exc
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _remote_relay(
    local_snapshot: Path,
    *,
    remote_host: str,
    remote_port: int,
    remote_runtime_root: str,
    remote_snapshot: str,
    remote_project_dir: str,
    maximum_age_seconds: int,
) -> None:
    if not remote_host or any(char.isspace() for char in remote_host):
        raise StagingSnapshotRelayError("remote_host_invalid")
    if not 1 <= remote_port <= 65535:
        raise StagingSnapshotRelayError("remote_port_invalid")
    remote_root = Path(remote_runtime_root)
    remote_path = Path(remote_snapshot)
    project = Path(remote_project_dir)
    if not remote_root.is_absolute() or "staging" not in str(remote_root).lower():
        raise StagingSnapshotRelayError("remote_root_not_staging")
    if not remote_path.is_absolute() or remote_root not in remote_path.parents:
        raise StagingSnapshotRelayError("remote_snapshot_outside_root")
    if not project.is_absolute():
        raise StagingSnapshotRelayError("remote_project_dir_invalid")

    temporary = remote_path.with_name(f".{remote_path.name}.relay-{uuid4().hex}.tmp")
    ssh_base = [
        "ssh",
        "-p",
        str(remote_port),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        remote_host,
    ]
    prepare = f"install -d -m 0755 -- {shlex.quote(str(remote_root))}"
    subprocess.run([*ssh_base, prepare], check=True)
    try:
        subprocess.run(
            [
                "scp",
                "-P",
                str(remote_port),
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                str(local_snapshot),
                f"{remote_host}:{temporary}",
            ],
            check=True,
        )
        check_command = " ".join(
            [
                "python3",
                shlex.quote(str(project / "scripts/publish_coin_intelligence_snapshot.py")),
                "check",
                "--runtime-root",
                shlex.quote(str(remote_root)),
                "--snapshot",
                shlex.quote(str(temporary)),
                "--maximum-age-seconds",
                str(maximum_age_seconds),
            ]
        )
        publish = (
            f"{check_command} >/dev/null && chmod 0644 -- {shlex.quote(str(temporary))} "
            f"&& mv -f -- {shlex.quote(str(temporary))} {shlex.quote(str(remote_path))}"
        )
        subprocess.run([*ssh_base, publish], check=True)
    except Exception:
        subprocess.run(
            [*ssh_base, f"rm -f -- {shlex.quote(str(temporary))}"],
            check=False,
        )
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True, choices=("staging",))
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--source-snapshot", required=True)
    parser.add_argument("--local-runtime-root", required=True)
    parser.add_argument("--local-snapshot", required=True)
    parser.add_argument("--maximum-age-seconds", type=int, default=120)
    parser.add_argument("--remote-host")
    parser.add_argument("--remote-port", type=int, default=22)
    parser.add_argument("--remote-runtime-root")
    parser.add_argument("--remote-snapshot")
    parser.add_argument("--remote-project-dir")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.maximum_age_seconds <= 0:
            raise StagingSnapshotRelayError("maximum_age_seconds_invalid")
        source_root, source = _inside(
            args.source_root,
            args.source_snapshot,
            field="source_snapshot",
        )
        _, local = _inside(
            args.local_runtime_root,
            args.local_snapshot,
            field="local_snapshot",
        )
        if not source_root.is_dir() or not source.is_file():
            raise StagingSnapshotRelayError("source_snapshot_missing")
        remote_values = (
            args.remote_host,
            args.remote_runtime_root,
            args.remote_snapshot,
            args.remote_project_dir,
        )
        if any(remote_values) and not all(remote_values):
            raise StagingSnapshotRelayError("remote_arguments_incomplete")

        source_snapshot = _validated_snapshot(
            source,
            maximum_age_seconds=args.maximum_age_seconds,
        )
        with _relay_lock(local):
            _atomic_copy(source, local)
            _validated_snapshot(local, maximum_age_seconds=args.maximum_age_seconds)
            source_digest = _digest(source)
            if _digest(local) != source_digest:
                raise StagingSnapshotRelayError("local_snapshot_digest_mismatch")
            if all(remote_values):
                _remote_relay(
                    local,
                    remote_host=args.remote_host,
                    remote_port=args.remote_port,
                    remote_runtime_root=args.remote_runtime_root,
                    remote_snapshot=args.remote_snapshot,
                    remote_project_dir=args.remote_project_dir,
                    maximum_age_seconds=args.maximum_age_seconds,
                )
        print(
            json.dumps(
                {
                    "status": "relayed",
                    "snapshot_state": (
                        "RATE_READY"
                        if int(source_snapshot["rates"]["estimated_count"]) > 0
                        else "SAFE_NO_DATA"
                    ),
                    "snapshot_sha256": source_digest,
                    "generated_at_utc": source_snapshot.get("generated_at_utc"),
                    "estimated_rate_count": int(source_snapshot["rates"]["estimated_count"]),
                    "no_data_rate_count": int(source_snapshot["rates"]["no_data_count"]),
                    "remote_relayed": bool(all(remote_values)),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, StagingSnapshotRelayError) as exc:
        print(
            json.dumps(
                {"status": "failed", "reason": type(exc).__name__},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
