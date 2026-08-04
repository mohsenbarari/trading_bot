#!/usr/bin/env python3
"""Explicitly ingest local private-gold event spools without a network client.

The command is intentionally one-shot.  It reads newline-delimited outer event
envelopes from already-protected local files, never removes or rewrites those
files, and emits only redacted counters.  Telegram/Telethon, worker scheduling,
and application startup integration are deliberately absent.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3
from typing import Iterator, Sequence

from core.market_intelligence.market_contracts import MarketStoreContractError, normalize_utc
from core.market_intelligence.market_store import connect_market_store, initialize_market_store
from core.market_intelligence.private_gold_payloads import (
    PrivateGoldPayloadEnvelope,
    PrivateGoldPayloadStageReport,
    stage_private_gold_payload,
)
from core.market_intelligence.private_gold_pipeline import process_private_gold_payloads
from core.market_intelligence.private_gold_staging import (
    connect_private_gold_staging,
    initialize_private_gold_staging,
)


PRIVATE_GOLD_SPOOL_COMMAND_VERSION = "private-gold-spool-v1"
_MAX_RECORD_BYTES = 2 * 1024 * 1024
_MAX_RECORDS = 100_000


class PrivateGoldSpoolCommandError(RuntimeError):
    """A local spool operation is not safe to perform."""


class PrivateGoldSpoolBusyError(PrivateGoldSpoolCommandError):
    """Another local process owns the same bounded staging store."""


@dataclass(frozen=True, slots=True)
class _SpoolCounters:
    records_read: int = 0
    records_rejected: int = 0
    decoded_offers: int = 0
    decoded_trade_updates: int = 0
    staged_offer_changes: int = 0
    staged_trade_changes: int = 0
    invalid_payload_items: int = 0
    conflicting_payload_items: int = 0
    rejected_by_staging: int = 0

    def plus(self, report: PrivateGoldPayloadStageReport | None = None, *, rejected: bool = False) -> "_SpoolCounters":
        if report is None:
            return _SpoolCounters(
                records_read=self.records_read,
                records_rejected=self.records_rejected + int(rejected),
                decoded_offers=self.decoded_offers,
                decoded_trade_updates=self.decoded_trade_updates,
                staged_offer_changes=self.staged_offer_changes,
                staged_trade_changes=self.staged_trade_changes,
                invalid_payload_items=self.invalid_payload_items,
                conflicting_payload_items=self.conflicting_payload_items,
                rejected_by_staging=self.rejected_by_staging,
            )
        return _SpoolCounters(
            records_read=self.records_read + 1,
            records_rejected=self.records_rejected,
            decoded_offers=self.decoded_offers + report.decoded_offers,
            decoded_trade_updates=self.decoded_trade_updates + report.decoded_trade_updates,
            staged_offer_changes=self.staged_offer_changes + report.inserted_or_updated_offers,
            staged_trade_changes=self.staged_trade_changes + report.inserted_or_updated_trade_updates,
            invalid_payload_items=self.invalid_payload_items + report.invalid_items,
            conflicting_payload_items=self.conflicting_payload_items + report.conflicting_items,
            rejected_by_staging=self.rejected_by_staging + report.staging_rejected_items,
        )


def _emit(**payload: object) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _runtime_root(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise PrivateGoldSpoolCommandError("runtime_root_unavailable")
    repository = _repository_root()
    try:
        root.relative_to(repository)
    except ValueError:
        return root
    raise PrivateGoldSpoolCommandError("runtime_root_inside_repository")


def _path_inside_root(root: Path, value: str, *, field_name: str) -> Path:
    supplied = Path(value).expanduser()
    candidate = supplied.resolve() if supplied.is_absolute() else (root / supplied).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PrivateGoldSpoolCommandError(f"{field_name}_outside_runtime_root") from exc
    if candidate == root:
        raise PrivateGoldSpoolCommandError(f"{field_name}_must_be_file")
    return candidate


def _existing_regular_file(path: Path, *, field_name: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise PrivateGoldSpoolCommandError(f"{field_name}_unavailable")
    return path


@contextmanager
def _lock(staging_path: Path) -> Iterator[None]:
    lock_path = staging_path.with_name(f".{staging_path.name}.lock")
    try:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise PrivateGoldSpoolCommandError("staging_lock_unavailable") from exc
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PrivateGoldSpoolBusyError("private_gold_spool_ingest_in_progress") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _as_of(value: str | None) -> str:
    candidate: datetime | str = value if value else datetime.now(timezone.utc)
    try:
        return normalize_utc(candidate, field_name="private_gold_spool_as_of_utc")
    except MarketStoreContractError as exc:
        raise PrivateGoldSpoolCommandError(str(exc)) from exc


def _envelope_from_record(record: object, *, stream: str, as_of_utc: str) -> PrivateGoldPayloadEnvelope | None:
    if not isinstance(record, dict):
        return None
    payload_text = record.get("payload_text")
    published_at = record.get("published_at_utc")
    if not isinstance(payload_text, str) or not payload_text.strip() or published_at is None:
        return None
    try:
        available_at = normalize_utc(published_at, field_name="private_gold_spool_published_at_utc")
    except MarketStoreContractError:
        return None
    if available_at > as_of_utc:
        return None
    return PrivateGoldPayloadEnvelope(
        payload_text=payload_text,
        available_at_utc=available_at,
        stream=stream,
    )


def _stage_spool(
    connection: sqlite3.Connection,
    *,
    path: Path,
    stream: str,
    as_of_utc: str,
    maximum_records: int,
) -> _SpoolCounters:
    counters = _SpoolCounters()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if counters.records_read + counters.records_rejected >= maximum_records:
                raise PrivateGoldSpoolCommandError("spool_record_limit_exceeded")
            if len(line.encode("utf-8")) > _MAX_RECORD_BYTES:
                counters = counters.plus(rejected=True)
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                counters = counters.plus(rejected=True)
                continue
            envelope = _envelope_from_record(record, stream=stream, as_of_utc=as_of_utc)
            if envelope is None:
                counters = counters.plus(rejected=True)
                continue
            counters = counters.plus(
                stage_private_gold_payload(connection, envelope, staged_at_utc=as_of_utc)
            )
    return counters


def _sum(left: _SpoolCounters, right: _SpoolCounters) -> _SpoolCounters:
    return _SpoolCounters(
        **{
            name: getattr(left, name) + getattr(right, name)
            for name in _SpoolCounters.__dataclass_fields__
        }
    )


def _run(args: argparse.Namespace) -> int:
    root = _runtime_root(args.runtime_root)
    market_path = _existing_regular_file(
        _path_inside_root(root, args.market_store, field_name="market_store"),
        field_name="market_store",
    )
    staging_path = _path_inside_root(root, args.staging_store, field_name="staging_store")
    if staging_path.is_symlink() or not staging_path.parent.is_dir() or staging_path.parent.is_symlink():
        raise PrivateGoldSpoolCommandError("staging_store_parent_unavailable")
    spools: list[tuple[Path, str]] = []
    for value in args.offer_spool:
        spools.append((_existing_regular_file(_path_inside_root(root, value, field_name="offer_spool"), field_name="offer_spool"), "OFFER"))
    for value in args.trade_spool:
        spools.append((_existing_regular_file(_path_inside_root(root, value, field_name="trade_spool"), field_name="trade_spool"), "TRADE"))
    if not spools:
        raise PrivateGoldSpoolCommandError("private_gold_spool_required")
    as_of = _as_of(args.as_of_utc)
    staging = market = None
    try:
        with _lock(staging_path):
            staging = connect_private_gold_staging(staging_path, repository_root=_repository_root())
            market = connect_market_store(market_path)
            initialize_private_gold_staging(staging)
            initialize_market_store(market)
            counters = _SpoolCounters()
            for path, stream in spools:
                counters = _sum(
                    counters,
                    _stage_spool(
                        staging,
                        path=path,
                        stream=stream,
                        as_of_utc=as_of,
                        maximum_records=int(args.maximum_records),
                    ),
                )
            # Durable raw reconciliation precedes Market Store mutation.  A
            # later failure is safely retried from this three-day staging DB.
            staging.commit()
            try:
                pipeline = process_private_gold_payloads(staging, market, (), as_of_utc=as_of)
                market.commit()
                staging.commit()
            except BaseException:
                market.rollback()
                staging.rollback()
                raise
    finally:
        if market is not None:
            market.close()
        if staging is not None:
            staging.close()
    _emit(
        command="ingest",
        version=PRIVATE_GOLD_SPOOL_COMMAND_VERSION,
        status="INGESTED",
        records_read=counters.records_read,
        records_rejected=counters.records_rejected,
        decoded_offers=counters.decoded_offers,
        decoded_trade_updates=counters.decoded_trade_updates,
        staged_offer_changes=counters.staged_offer_changes,
        staged_trade_changes=counters.staged_trade_changes,
        invalid_payload_items=counters.invalid_payload_items,
        conflicting_payload_items=counters.conflicting_payload_items,
        rejected_by_staging=counters.rejected_by_staging,
        promoted_offer_facts=pipeline.promotion.offer_facts_upserted,
        promoted_trade_facts=pipeline.promotion.trade_facts_upserted,
        refreshed_paper_minutes=pipeline.refreshed_paper_minutes,
        expired_staging_rows_purged=pipeline.expired_staging_rows_purged,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--market-store", required=True)
    parser.add_argument("--staging-store", required=True)
    parser.add_argument("--offer-spool", action="append", default=[])
    parser.add_argument("--trade-spool", action="append", default=[])
    parser.add_argument("--as-of-utc", default=None)
    parser.add_argument("--maximum-records", type=int, default=10_000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if int(args.maximum_records) <= 0 or int(args.maximum_records) > _MAX_RECORDS:
        _emit(command="ingest", status="FAILED", reason="spool_record_limit_invalid")
        return 2
    try:
        return _run(args)
    except PrivateGoldSpoolBusyError as exc:
        _emit(command="ingest", status="BUSY", reason=str(exc))
        return 75
    except (PrivateGoldSpoolCommandError, ValueError, sqlite3.Error) as exc:
        _emit(command="ingest", status="FAILED", reason=str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
