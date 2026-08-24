#!/usr/bin/env python3
"""Ingest new local capture JSONL spools into one shadow/live Market Store.

The command is one-shot and network-free.  It advances durable byte cursors in
the same protected three-day staging database as the raw current state, then
idempotently projects privacy-minimized facts.  Output is deliberately limited
to counters and contract error classes.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Iterator, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.capture_event_adapter import (  # noqa: E402
    CAPTURE_ADAPTER_VERSION,
    CaptureEventContractError,
    decode_capture_event,
    initialize_capture_adapter,
    project_capture_changes,
    record_capture_rejection,
    stage_capture_event,
)
from core.market_intelligence.coin_group_staging import connect_coin_group_staging  # noqa: E402
from core.market_intelligence.market_contracts import normalize_utc  # noqa: E402
from core.market_intelligence.market_store import connect_market_store, initialize_market_store  # noqa: E402


COMMAND_VERSION = "capture-spool-ingest-v1"
MARKET_LIVE_BACKLOG_WINDOW_SECONDS = 30 * 60
_MAX_RECORD_BYTES = 256 * 1024
_SPOOL_NAME = re.compile(r"^events-\d{4}-\d{2}-\d{2}\.jsonl$")


class CaptureSpoolCommandError(RuntimeError):
    """An operational precondition prevents safe spool ingestion."""


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CaptureSpoolCommandError("capture_spool_ingest_in_progress") from exc
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _emit(**payload: object) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)


def _runtime_root(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if root.is_symlink() or not root.is_dir():
        raise CaptureSpoolCommandError("runtime_root_unavailable")
    try:
        root.relative_to(REPO_ROOT.resolve())
    except ValueError:
        return root
    raise CaptureSpoolCommandError("runtime_root_inside_repository")


def _inside(root: Path, value: str, *, field: str) -> Path:
    supplied = Path(value).expanduser()
    path = supplied.resolve() if supplied.is_absolute() else (root / supplied).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CaptureSpoolCommandError(f"{field}_outside_runtime_root") from exc
    if path == root or path.is_symlink():
        raise CaptureSpoolCommandError(f"{field}_invalid")
    return path


def _spool_dir(value: str, *, field: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_symlink() or not path.is_dir():
        raise CaptureSpoolCommandError(f"{field}_unavailable")
    return path


def _spool_files(directory: Path) -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(directory.iterdir())
        if _SPOOL_NAME.fullmatch(path.name) and path.is_file() and not path.is_symlink()
    )


def _cursor(
    connection: sqlite3.Connection,
    *,
    stream: str,
    path: Path,
    device: int,
    inode: int,
    size: int,
) -> int:
    row = connection.execute(
        "SELECT device,inode,byte_offset FROM capture_file_cursors WHERE stream=? AND file_path=?",
        (stream, str(path)),
    ).fetchone()
    if row is None:
        return 0
    offset = int(row["byte_offset"])
    if int(row["device"]) != device or int(row["inode"]) != inode or offset > size:
        return 0
    return offset


def _save_cursor(
    connection: sqlite3.Connection,
    *,
    stream: str,
    path: Path,
    device: int,
    inode: int,
    offset: int,
    updated_at_utc: str,
) -> None:
    connection.execute(
        """
        INSERT INTO capture_file_cursors(stream,file_path,device,inode,byte_offset,updated_at_utc)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(stream,file_path) DO UPDATE SET
          device=excluded.device,inode=excluded.inode,
          byte_offset=excluded.byte_offset,updated_at_utc=excluded.updated_at_utc
        """,
        (stream, str(path), device, inode, offset, updated_at_utc),
    )


def _ingest_file(
    connection: sqlite3.Connection,
    *,
    stream: str,
    path: Path,
    now_utc: str,
    remaining_records: int,
    market_minimum_available_at_utc: str | None = None,
) -> dict[str, int]:
    stat = path.stat()
    offset = _cursor(
        connection,
        stream=stream,
        path=path,
        device=int(stat.st_dev),
        inode=int(stat.st_ino),
        size=int(stat.st_size),
    )
    counters = {
        "records": 0,
        "accepted": 0,
        "duplicates": 0,
        "changes": 0,
        "tombstones": 0,
        "rejected": 0,
        "stale_market_skipped": 0,
    }
    with path.open("rb") as handle:
        handle.seek(offset)
        while counters["records"] < remaining_records:
            start = handle.tell()
            if start >= int(stat.st_size):
                break
            read_limit = min(_MAX_RECORD_BYTES + 2, int(stat.st_size) - start)
            raw = handle.readline(read_limit)
            if not raw:
                break
            if not raw.endswith(b"\n"):
                # A complete over-sized line can fill the bounded read too.
                # Quarantine it only after consuming through its newline in
                # the file-size snapshot taken at cycle start.  Never chase a
                # concurrently growing producer indefinitely; a partial tail
                # remains at its prior cursor for the next cycle.
                if len(raw) > _MAX_RECORD_BYTES:
                    complete = False
                    while handle.tell() < int(stat.st_size):
                        remaining = int(stat.st_size) - handle.tell()
                        tail = handle.readline(min(_MAX_RECORD_BYTES + 2, remaining))
                        if tail.endswith(b"\n"):
                            complete = True
                            break
                    if complete:
                        record_capture_rejection(
                            connection,
                            stream=stream,
                            record_bytes=b"record-too-large",
                            reason="capture_record_too_large",
                            seen_at_utc=now_utc,
                        )
                        counters["records"] += 1
                        counters["rejected"] += 1
                        offset = handle.tell()
                        continue
                handle.seek(start)
                break
            counters["records"] += 1
            offset = handle.tell()
            try:
                decoded = json.loads(raw.decode("utf-8"))
                event = decode_capture_event(decoded, stream=stream)
                if (
                    stream == "market"
                    and event.event_type != "message_deleted"
                    and market_minimum_available_at_utc is not None
                    and event.available_at_utc < market_minimum_available_at_utc
                ):
                    counters["stale_market_skipped"] += 1
                    continue
                report = stage_capture_event(connection, event)
            except (UnicodeDecodeError, json.JSONDecodeError, CaptureEventContractError) as exc:
                record_capture_rejection(
                    connection,
                    stream=stream,
                    record_bytes=raw,
                    reason=str(exc) or type(exc).__name__,
                    seen_at_utc=now_utc,
                )
                counters["rejected"] += 1
                continue
            counters["accepted"] += int(report.accepted)
            counters["duplicates"] += int(report.duplicate)
            counters["changes"] += int(report.staged_change)
            counters["tombstones"] += int(report.tombstone_applied)
    _save_cursor(
        connection,
        stream=stream,
        path=path,
        device=int(stat.st_dev),
        inode=int(stat.st_ino),
        offset=offset,
        updated_at_utc=now_utc,
    )
    return counters


def _run(args: argparse.Namespace) -> int:
    root = _runtime_root(args.runtime_root)
    market_path = _inside(root, args.market_store, field="market_store")
    staging_path = _inside(root, args.staging_store, field="staging_store")
    lock_path = _inside(root, args.lock_file, field="lock_file")
    market_spool = _spool_dir(args.market_spool_dir, field="market_spool_dir")
    coin_spool = _spool_dir(args.coin_spool_dir, field="coin_spool_dir")
    for path in (market_path.parent, staging_path.parent, lock_path.parent):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
    now = normalize_utc(datetime.now(timezone.utc), field_name="capture_spool_now_utc")
    maximum = int(args.maximum_records)
    if maximum <= 0:
        raise CaptureSpoolCommandError("maximum_records_invalid")
    totals = {
        "records": 0,
        "accepted": 0,
        "duplicates": 0,
        "changes": 0,
        "tombstones": 0,
        "rejected": 0,
        "stale_market_skipped": 0,
    }
    market_minimum_available = (
        datetime.fromisoformat(now.replace("Z", "+00:00"))
        - timedelta(seconds=MARKET_LIVE_BACKLOG_WINDOW_SECONDS)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with _lock(lock_path):
        staging = connect_coin_group_staging(staging_path, repository_root=REPO_ROOT)
        market = connect_market_store(market_path)
        try:
            initialize_capture_adapter(staging)
            initialize_market_store(market)
            for stream, directory in (("market", market_spool), ("coin", coin_spool)):
                for path in _spool_files(directory):
                    remaining = maximum - totals["records"]
                    if remaining <= 0:
                        raise CaptureSpoolCommandError("capture_record_limit_exceeded")
                    counts = _ingest_file(
                        staging,
                        stream=stream,
                        path=path,
                        now_utc=now,
                        remaining_records=remaining,
                        market_minimum_available_at_utc=market_minimum_available,
                    )
                    for key, value in counts.items():
                        totals[key] += value
            # Raw current state and cursors become durable before projection.
            # A projection failure is therefore safely retried without reread.
            staging.commit()
            try:
                projection = project_capture_changes(staging, market, as_of_utc=now)
                market.commit()
                staging.commit()
            except BaseException:
                market.rollback()
                staging.rollback()
                raise
        finally:
            market.close()
            staging.close()
    group = projection.group_pipeline
    _emit(
        command="ingest",
        version=COMMAND_VERSION,
        adapter_version=CAPTURE_ADAPTER_VERSION,
        status="INGESTED",
        **totals,
        market_messages_reprojected=projection.market_messages_reprojected,
        market_facts_upserted=projection.market_facts_upserted,
        market_facts_retracted=projection.market_facts_retracted,
        private_paper_minutes_refreshed=projection.private_paper_minutes_refreshed,
        group_messages_seen=(group.staged_messages_seen if group else 0),
        group_eligible_offers=(group.eligible_offers if group else 0),
        group_eligible_trades=(group.eligible_trades if group else 0),
        group_pending_or_rejected=(
            group.pending_or_rejected_offers + group.pending_or_rejected_trades if group else 0
        ),
        raw_rows_purged=projection.raw_rows_purged,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--market-store", default="market/market.sqlite3")
    parser.add_argument("--staging-store", default="staging/capture.sqlite3")
    parser.add_argument("--lock-file", default="run/capture-spool.lock")
    parser.add_argument("--market-spool-dir", required=True)
    parser.add_argument("--coin-spool-dir", required=True)
    parser.add_argument("--maximum-records", type=int, default=200_000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    previous_umask = os.umask(0o077)
    try:
        return _run(args)
    except (CaptureSpoolCommandError, CaptureEventContractError, OSError, sqlite3.Error) as exc:
        _emit(command="ingest", version=COMMAND_VERSION, status="FAILED", reason=str(exc))
        return 2
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    raise SystemExit(main())
