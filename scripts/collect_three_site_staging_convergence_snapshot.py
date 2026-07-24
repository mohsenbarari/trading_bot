#!/usr/bin/env python3
"""Collect one redacted, read-only convergence snapshot inside a site observer.

The command is intended for the ``*_sync_observer`` Compose service.  It does
not open a public listener, create a probe, mutate a row, or emit business
values/file bytes.  It reads one repeatable-read transaction and hashes the
local WebApp content-addressed files before returning a small typed snapshot.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings
from core.db import AsyncSessionLocal
from core.dr_blob_plane import _hash_file
from core.runtime_identity import resolve_runtime_identity
from core.sync_parity import build_database_parity_snapshot
from models.dr_event import (
    DrBlobManifest,
    DrConflictQuarantine,
    DrDestinationCursor,
    DrEvent,
    DrProducerCursor,
    DrStreamCheckpoint,
)


SITES = ("bot_fi", "webapp_fi", "webapp_ir")
WEBAPP_SITES = frozenset({"webapp_fi", "webapp_ir"})
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SCHEMA = "three-site-staging-convergence-site-snapshot-v1"


class ConvergenceSnapshotError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _zero_hash() -> str:
    return "0" * 64


def _hash_or_zero(value: Any, *, label: str, zero_allowed: bool) -> str:
    raw = str(value or "")
    if SHA256.fullmatch(raw) is None or (not zero_allowed and raw == _zero_hash()):
        raise ConvergenceSnapshotError(f"{label} hash is invalid")
    return raw


async def _stream_transaction_hash(
    db,
    *,
    origin_site: str,
    producer_epoch: int,
    destination_site: str,
    destination_sequence: int,
) -> str:
    if destination_sequence == 0:
        return _zero_hash()
    result = await db.execute(
        text(
            "SELECT destination_streams -> CAST(:destination AS text) ->> 'transaction_hash' "
            "FROM dr_events WHERE origin_physical_site=:origin "
            "AND producer_epoch=:epoch "
            "AND (destination_streams -> CAST(:destination AS text) ->> 'sequence')::bigint=:sequence"
        ),
        {
            "origin": origin_site,
            "epoch": producer_epoch,
            "destination": destination_site,
            "sequence": destination_sequence,
        },
    )
    values = list(result.scalars())
    if len(values) != 1:
        raise ConvergenceSnapshotError("stream transaction tail is missing or ambiguous")
    return _hash_or_zero(values[0], label="stream transaction", zero_allowed=False)


async def _source_streams(db, *, site: str, producer_epoch: int) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(DrDestinationCursor).where(
                DrDestinationCursor.origin_physical_site == site,
                DrDestinationCursor.producer_epoch == producer_epoch,
            )
        )
    ).scalars().all()
    by_destination: dict[str, DrDestinationCursor] = {}
    for row in rows:
        destination = str(row.destination_site)
        if destination not in SITES or destination == site or destination in by_destination:
            raise ConvergenceSnapshotError("source destination cursor is invalid")
        by_destination[destination] = row
    output = []
    for destination in SITES:
        if destination == site:
            continue
        cursor = by_destination.get(destination)
        sequence = int(cursor.last_sequence) if cursor is not None else 0
        transaction_hash = await _stream_transaction_hash(
            db,
            origin_site=site,
            producer_epoch=producer_epoch,
            destination_site=destination,
            destination_sequence=sequence,
        )
        output.append(
            {
                "destination_site": destination,
                "source_sequence": sequence,
                "source_transaction_hash": transaction_hash,
            }
        )
    return output


async def _destination_streams(db, *, site: str) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(DrStreamCheckpoint).where(DrStreamCheckpoint.destination_site == site)
        )
    ).scalars().all()
    output = []
    seen: set[tuple[str, int]] = set()
    for row in rows:
        origin = str(row.origin_physical_site)
        epoch = int(row.producer_epoch)
        received = int(row.contiguous_received_sequence)
        applied = int(row.contiguous_applied_sequence)
        if origin not in SITES or origin == site or epoch < 1 or received < applied or received < 0:
            raise ConvergenceSnapshotError("destination checkpoint is invalid")
        key = (origin, epoch)
        if key in seen:
            raise ConvergenceSnapshotError("destination checkpoint is duplicated")
        seen.add(key)
        output.append(
            {
                "origin_site": origin,
                "producer_epoch": epoch,
                "received_sequence": received,
                "applied_sequence": applied,
                "received_transaction_hash": await _stream_transaction_hash(
                    db,
                    origin_site=origin,
                    producer_epoch=epoch,
                    destination_site=site,
                    destination_sequence=received,
                ),
                "applied_transaction_hash": await _stream_transaction_hash(
                    db,
                    origin_site=origin,
                    producer_epoch=epoch,
                    destination_site=site,
                    destination_sequence=applied,
                ),
            }
        )
    return sorted(output, key=lambda item: (item["origin_site"], item["producer_epoch"]))


async def _assert_single_source_epoch(db, *, site: str, producer_epoch: int) -> None:
    cursor_rows = (
        await db.execute(
            select(DrProducerCursor.producer_epoch, DrProducerCursor.last_sequence).where(
                DrProducerCursor.origin_physical_site == site
            )
        )
    ).all()
    if any(int(epoch) != producer_epoch and int(sequence) > 0 for epoch, sequence in cursor_rows):
        raise ConvergenceSnapshotError("historic producer epoch requires a multi-epoch convergence gate")
    foreign_event_count = int(
        await db.scalar(
            select(func.count(DrEvent.event_id)).where(
                DrEvent.origin_physical_site == site,
                DrEvent.producer_epoch != producer_epoch,
            )
        )
        or 0
    )
    if foreign_event_count:
        raise ConvergenceSnapshotError("historic producer event requires a multi-epoch convergence gate")


async def _blob_records(db, *, site: str) -> list[dict[str, Any]]:
    if site not in WEBAPP_SITES:
        return []
    rows = (
        await db.execute(
            select(DrBlobManifest)
            .where(DrBlobManifest.state != "tombstoned")
            .order_by(DrBlobManifest.content_hash)
        )
    ).scalars().all()
    root = Path(settings.dr_blob_root).resolve()
    records: list[dict[str, Any]] = []
    for row in rows:
        if row.state != "uploaded":
            raise ConvergenceSnapshotError("Blob manifest is not uploaded")
        content_hash = _hash_or_zero(row.content_hash, label="Blob content", zero_allowed=False)
        try:
            path = Path(str(row.local_path)).resolve(strict=True)
            path.relative_to(root)
            local_hash, local_size = _hash_file(path)
        except Exception as exc:
            raise ConvergenceSnapshotError("Blob local read-back failed") from exc
        if local_hash != content_hash or local_size != int(row.size_bytes):
            raise ConvergenceSnapshotError("Blob local content identity differs")
        records.append(
            {
                "content_hash": content_hash,
                "size_bytes": int(row.size_bytes),
                "object_version_id": str(row.object_version_id or ""),
                "object_ciphertext_hash": _hash_or_zero(
                    row.object_ciphertext_hash, label="Blob ciphertext", zero_allowed=False
                ),
                "object_ciphertext_size": int(row.object_ciphertext_size or 0),
                "encryption_key_id": str(row.encryption_key_id or ""),
                "encryption_algorithm": str(row.encryption_algorithm or ""),
                "local_content_hash": local_hash,
                "local_size_bytes": local_size,
            }
        )
    return records


async def collect(*, campaign_id: str, release_sha: str, plan_sha256: str, max_rows_per_table: int) -> dict[str, Any]:
    identity = resolve_runtime_identity()
    site = identity.physical_site
    if site not in SITES or str(settings.release_sha or "") != release_sha:
        raise ConvergenceSnapshotError("runtime identity/release differs from the convergence campaign")
    if max_rows_per_table < 1:
        raise ConvergenceSnapshotError("max rows per table is invalid")
    async with AsyncSessionLocal() as db:
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        try:
            producer_epoch = int(settings.dr_producer_epoch)
            if producer_epoch < 1:
                raise ConvergenceSnapshotError("runtime producer epoch is invalid")
            await _assert_single_source_epoch(db, site=site, producer_epoch=producer_epoch)
            parity = await build_database_parity_snapshot(
                db, mode="deep", max_rows_per_table=max_rows_per_table
            )
            if any(bool(table.get("truncated")) for table in parity["tables"].values()):
                raise ConvergenceSnapshotError("database parity snapshot exceeded its approved row bound")
            source_streams = await _source_streams(db, site=site, producer_epoch=producer_epoch)
            destination_streams = await _destination_streams(db, site=site)
            conflict_count = int(
                await db.scalar(
                    select(func.count(DrConflictQuarantine.quarantine_id)).where(
                        DrConflictQuarantine.resolved_at.is_(None)
                    )
                )
                or 0
            )
            blobs = await _blob_records(db, site=site)
        finally:
            await db.rollback()
    return {
        "schema": SCHEMA,
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "plan_sha256": plan_sha256,
        "site": site,
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "producer_epoch": producer_epoch,
        "source_streams": source_streams,
        "destination_streams": destination_streams,
        "unresolved_conflict_count": conflict_count,
        "database_snapshot": parity,
        "blob_records": blobs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--max-rows-per-table", type=int, default=10000)
    args = parser.parse_args(argv)
    try:
        if str(UUID(args.campaign_id)) != args.campaign_id:
            raise ValueError
        if SHA40.fullmatch(args.release_sha) is None or SHA256.fullmatch(args.plan_sha256) is None:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise SystemExit("campaign identity is invalid") from exc
    try:
        print(_canonical(asyncio.run(collect(
            campaign_id=args.campaign_id,
            release_sha=args.release_sha,
            plan_sha256=args.plan_sha256,
            max_rows_per_table=args.max_rows_per_table,
        ))))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
