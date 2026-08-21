#!/usr/bin/env python3
"""Run one guarded, local coin-intelligence Snapshot publish or freshness check.

This command intentionally does not schedule itself and does not ingest data.
An operational owner must invoke it with paths inside one protected runtime
root.  It is safe to use from a staging job because it neither creates a
Market Store nor falls back to another source when the Store is unavailable.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sys
from typing import Iterator, Sequence

# Direct execution (``python scripts/...``) places ``scripts/`` on sys.path.
# The documented operational command must resolve the local package without an
# ambient PYTHONPATH, while imports in unit tests remain unchanged.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.market_contracts import normalize_utc
from core.market_intelligence.market_snapshot import AtomicMarketSnapshotProvider, MarketSnapshotUnavailable
from core.market_intelligence.snapshot_publisher import (
    SNAPSHOT_PUBLISHER_VERSION,
    MarketSnapshotPublisherError,
    publish_rate_ready_snapshot,
)


class SnapshotPublisherCommandError(RuntimeError):
    """An operational precondition was not satisfied."""


class SnapshotPublisherBusyError(SnapshotPublisherCommandError):
    """Another local publisher currently owns this artifact."""


STAGING_NO_DATA_CONFIRMATION = "publish-staging-no-data-snapshot"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _emit(**payload: object) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)


def _runtime_root(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise SnapshotPublisherCommandError("runtime_root_unavailable")
    return root


def _path_inside_root(root: Path, value: str, *, field_name: str) -> Path:
    supplied = Path(value).expanduser()
    candidate = supplied.resolve() if supplied.is_absolute() else (root / supplied).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise SnapshotPublisherCommandError(f"{field_name}_outside_runtime_root") from exc
    if candidate == root:
        raise SnapshotPublisherCommandError(f"{field_name}_must_be_file")
    return candidate


@contextmanager
def _single_writer_lock(snapshot_path: Path) -> Iterator[None]:
    """Hold a non-blocking lock without ever removing a live lock inode."""

    lock_path = snapshot_path.with_name(f".{snapshot_path.name}.lock")
    try:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise SnapshotPublisherCommandError("snapshot_lock_unavailable") from exc
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SnapshotPublisherBusyError("snapshot_publish_in_progress") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _publish(args: argparse.Namespace) -> int:
    root = _runtime_root(args.runtime_root)
    store_path = _path_inside_root(root, args.market_store, field_name="market_store")
    snapshot_path = _path_inside_root(root, args.snapshot, field_name="snapshot")
    if not store_path.is_file():
        raise SnapshotPublisherCommandError("market_store_unavailable")
    if not snapshot_path.parent.is_dir():
        raise SnapshotPublisherCommandError("snapshot_parent_unavailable")
    publish_no_data = bool(
        getattr(args, "publish_staging_no_data_snapshot", False)
    )
    if publish_no_data:
        if (
            str(getattr(args, "environment", "") or "").strip().lower()
            != "staging"
            or str(getattr(args, "confirm", "") or "")
            != STAGING_NO_DATA_CONFIRMATION
            or not any(
                "staging" in part.lower()
                for part in snapshot_path.relative_to(root).parts
            )
        ):
            raise SnapshotPublisherCommandError(
                "staging_no_data_publish_authority_invalid"
            )
    as_of = args.as_of_utc
    if as_of:
        normalize_utc(as_of, field_name="snapshot_publish_as_of_utc")
    with _single_writer_lock(snapshot_path):
        result = publish_rate_ready_snapshot(
            market_store_path=store_path,
            snapshot_path=snapshot_path,
            as_of_utc=as_of,
            force=bool(getattr(args, "force", False)),
            publish_no_data_snapshot=publish_no_data,
        )
    _emit(
        command="publish",
        publisher_version=SNAPSHOT_PUBLISHER_VERSION,
        status=result.status,
        reason=result.reason,
        snapshot_digest=result.snapshot_digest,
        generated_at_utc=result.generated_at_utc,
        estimated_rate_count=result.estimated_rate_count,
        no_data_rate_count=result.no_data_rate_count,
        input_watermark=result.input_watermark,
    )
    if result.status in {"PUBLISHED", "PUBLISHED_NO_DATA", "UNCHANGED"}:
        return 0
    return 3


def _check(args: argparse.Namespace) -> int:
    root = _runtime_root(args.runtime_root)
    snapshot_path = _path_inside_root(root, args.snapshot, field_name="snapshot")
    try:
        snapshot = AtomicMarketSnapshotProvider(snapshot_path).load()
    except MarketSnapshotUnavailable as exc:
        _emit(
            command="check",
            publisher_version=SNAPSHOT_PUBLISHER_VERSION,
            status="UNAVAILABLE",
            reason=str(exc),
        )
        return 3
    generated_at = datetime.fromisoformat(
        normalize_utc(
            str(snapshot.get("generated_at_utc") or ""),
            field_name="snapshot_generated_at_utc",
        ).replace("Z", "+00:00")
    )
    age_seconds = (_utc_now() - generated_at).total_seconds()
    if age_seconds < 0 or age_seconds > int(args.maximum_age_seconds):
        _emit(
            command="check",
            publisher_version=SNAPSHOT_PUBLISHER_VERSION,
            status="STALE",
            reason="SNAPSHOT_STALE_OR_FUTURE",
            generated_at_utc=generated_at.isoformat().replace("+00:00", "Z"),
            age_seconds=round(age_seconds, 3),
        )
        return 3
    rates = snapshot.get("rates")
    if not isinstance(rates, dict):
        estimated_rate_count = 0
        no_data_rate_count = 0
    else:
        try:
            estimated_rate_count = int(rates.get("estimated_count", 0))
            no_data_rate_count = int(rates.get("no_data_count", 0))
        except (TypeError, ValueError):
            estimated_rate_count = 0
            no_data_rate_count = 0
    if estimated_rate_count <= 0:
        if (
            no_data_rate_count <= 0
            or snapshot.get("snapshot_status") != "NO_DATA_COIN_RATE_STATE"
        ):
            _emit(
                command="check",
                publisher_version=SNAPSHOT_PUBLISHER_VERSION,
                status="UNAVAILABLE",
                reason="SNAPSHOT_NO_DATA_STATE_INVALID",
            )
            return 3
        _emit(
            command="check",
            publisher_version=SNAPSHOT_PUBLISHER_VERSION,
            status="FRESH_NO_DATA",
            reason="NO_ESTIMATED_COIN_RATES",
            generated_at_utc=generated_at.isoformat().replace("+00:00", "Z"),
            age_seconds=round(age_seconds, 3),
            estimated_rate_count=estimated_rate_count,
            no_data_rate_count=no_data_rate_count,
        )
        return 0
    if snapshot.get("snapshot_status") != "PARTIAL_COIN_RATE_STATE":
        _emit(
            command="check",
            publisher_version=SNAPSHOT_PUBLISHER_VERSION,
            status="UNAVAILABLE",
            reason="SNAPSHOT_RATE_READY_STATE_INVALID",
        )
        return 3
    _emit(
        command="check",
        publisher_version=SNAPSHOT_PUBLISHER_VERSION,
        status="FRESH",
        reason=None,
        generated_at_utc=generated_at.isoformat().replace("+00:00", "Z"),
        age_seconds=round(age_seconds, 3),
        estimated_rate_count=estimated_rate_count,
        no_data_rate_count=no_data_rate_count,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    publish = commands.add_parser("publish", help="publish one atomic, rate-ready Snapshot")
    publish.add_argument("--runtime-root", required=True)
    publish.add_argument("--market-store", required=True)
    publish.add_argument("--snapshot", required=True)
    publish.add_argument("--as-of-utc", default=None)
    publish.add_argument(
        "--force",
        action="store_true",
        help="compatibility option; snapshots are always rebuilt because freshness is time-dependent",
    )
    publish.add_argument(
        "--publish-staging-no-data-snapshot",
        action="store_true",
        help=(
            "staging-only opt-in: atomically publish a fresh validated NO_DATA "
            "Snapshot when no estimated rate is available"
        ),
    )
    publish.add_argument("--environment", choices=("staging",))
    publish.add_argument(
        "--confirm",
        default="",
        help="exact confirmation required for the staging NO_DATA exception",
    )
    publish.set_defaults(handler=_publish)

    check = commands.add_parser("check", help="validate a published Snapshot without writing")
    check.add_argument("--runtime-root", required=True)
    check.add_argument("--snapshot", required=True)
    check.add_argument("--maximum-age-seconds", type=int, default=120)
    check.set_defaults(handler=_check)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "maximum_age_seconds", 1) <= 0:
        _emit(command=args.command, status="FAILED", reason="maximum_age_seconds_invalid")
        return 2
    try:
        return int(args.handler(args))
    except SnapshotPublisherBusyError as exc:
        _emit(command=args.command, status="BUSY", reason=str(exc))
        return 75
    except (SnapshotPublisherCommandError, MarketSnapshotPublisherError, ValueError) as exc:
        _emit(command=args.command, status="FAILED", reason=str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
