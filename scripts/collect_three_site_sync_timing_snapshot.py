#!/usr/bin/env python3
"""Collect one read-only, role-local DR timing snapshot for Gate D.

Run this command inside the exact Bot-FI, WebApp-FI or WebApp-IR application
image.  It never creates a probe and never changes delivery state; the Full
Matrix driver supplies a unique idempotency/correlation prefix created by its
separate doer.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import func, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings
from core.db import AsyncSessionLocal
from core.runtime_identity import resolve_runtime_identity
from core.three_site_full_matrix_campaign import secure_json
from models.dr_event import DrEvent, DrEventDelivery, DrEventReceipt
from scripts.measure_three_site_host_clock import SCHEMA as CLOCK_SCHEMA


SCHEMA = "three-site-staging-sync-site-snapshot-v1"


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate clock evidence field: {key}")
        result[key] = value
    return result


def _clock_from_base64(value: str) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(value + "===")
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except Exception as exc:
        raise ValueError("encoded host clock evidence is invalid") from exc
    if not isinstance(result, dict) or len(raw) > 256 * 1024:
        raise ValueError("encoded host clock evidence is invalid")
    return result


def _utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat()


def _like_prefix(value: str) -> str:
    """Treat a correlation prefix literally in PostgreSQL LIKE."""

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _receipt_hash(row: DrEventReceipt) -> str:
    payload = {
        "event_id": row.event_id,
        "destination_site": row.destination_site,
        "origin_physical_site": row.origin_physical_site,
        "producer_epoch": int(row.producer_epoch),
        "producer_sequence": int(row.producer_sequence),
        "envelope_hash": row.envelope_hash,
        "received_from_site": row.received_from_site,
        "relay_site": row.relay_site,
        "status": row.status,
        "received_at": _utc(row.received_at),
        "applied_at": _utc(row.applied_at),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def collect(prefix: str, *, clock: dict[str, Any]) -> dict[str, Any]:
    identity = resolve_runtime_identity()
    if (
        set(clock) != {
            "schema", "site", "release_sha", "synchronized", "offset_ms",
            "observed_at", "measurement_source", "measurement_raw_sha256",
            "measurement_raw",
        }
        or clock.get("schema") != CLOCK_SCHEMA
        or clock.get("site") != identity.physical_site
        or clock.get("release_sha") != str(settings.release_sha or "")
        or clock.get("synchronized") is not True
        or hashlib.sha256(str(clock.get("measurement_raw")).encode("utf-8")).hexdigest()
        != clock.get("measurement_raw_sha256")
    ):
        raise RuntimeError("host clock evidence does not match this runtime")
    pattern = _like_prefix(prefix)
    async with AsyncSessionLocal() as session:
        events = (
            await session.execute(
                select(DrEvent)
                .where(DrEvent.idempotency_key.like(pattern, escape="\\"))
                .order_by(DrEvent.created_at, DrEvent.event_id)
            )
        ).scalars().all()
        event_ids = [row.event_id for row in events]
        deliveries = []
        receipts = []
        if event_ids:
            deliveries = (
                await session.execute(
                    select(DrEventDelivery)
                    .where(DrEventDelivery.event_id.in_(event_ids))
                    .order_by(DrEventDelivery.event_id, DrEventDelivery.destination_site)
                )
            ).scalars().all()
            receipts = (
                await session.execute(
                    select(DrEventReceipt)
                    .where(DrEventReceipt.event_id.in_(event_ids))
                    .order_by(DrEventReceipt.event_id, DrEventReceipt.destination_site)
                )
            ).scalars().all()
        pending_count, oldest_pending = (
            await session.execute(
                select(func.count(DrEventDelivery.event_id), func.min(DrEvent.created_at))
                .join(DrEvent, DrEvent.event_id == DrEventDelivery.event_id)
                .where(
                    DrEvent.idempotency_key.like(pattern, escape="\\"),
                    DrEventDelivery.status != "acknowledged",
                )
            )
        ).one()
        await session.rollback()

    captured = datetime.now(timezone.utc)
    return {
        "schema": SCHEMA,
        "site": identity.physical_site,
        "logical_authority": identity.logical_authority,
        "release_sha": str(settings.release_sha or ""),
        "correlation_prefix": prefix,
        "captured_at": captured.isoformat(),
        "clock": {
            "synchronized": True,
            "offset_ms": float(clock["offset_ms"]),
            "observed_at": clock["observed_at"],
            "measurement_source": clock["measurement_source"],
            "measurement_raw_sha256": clock["measurement_raw_sha256"],
            "measurement_raw": clock["measurement_raw"],
        },
        "events": [
            {
                "event_id": row.event_id,
                "correlation_id": row.idempotency_key,
                "origin_physical_site": row.origin_physical_site,
                "producer_epoch": int(row.producer_epoch),
                "producer_sequence": int(row.producer_sequence),
                "envelope_hash": row.envelope_hash,
                "created_at": _utc(row.created_at),
                "payload_bytes": len(
                    json.dumps(
                        row.canonical_payload,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                ),
            }
            for row in events
        ],
        "deliveries": [
            {
                "event_id": row.event_id,
                "destination_site": row.destination_site,
                "status": row.status,
                "attempt_count": int(row.attempt_count or 0),
                # updated_at has no automatic on-update clause on this immutable
                # delivery identity; it records when the destination row was
                # enqueued, including relay enqueue after prior-hop apply.
                "enqueued_at": _utc(row.updated_at),
                "first_attempt_at": _utc(row.first_attempt_at),
                "last_attempt_at": _utc(row.last_attempt_at),
                "acknowledged_at": _utc(row.acknowledged_at),
                "acknowledgement_hash": row.acknowledgement_hash,
                "relay_site": row.relay_site,
                "last_error_code": row.last_error_code,
            }
            for row in deliveries
        ],
        "receipts": [
            {
                "event_id": row.event_id,
                "destination_site": row.destination_site,
                "origin_physical_site": row.origin_physical_site,
                "received_from_site": row.received_from_site,
                "relay_site": row.relay_site,
                "status": row.status,
                "received_at": _utc(row.received_at),
                "applied_at": _utc(row.applied_at),
                "envelope_hash": row.envelope_hash,
                "receipt_hash": _receipt_hash(row),
            }
            for row in receipts
        ],
        "backlog": {
            "pending_events": int(pending_count or 0),
            "oldest_pending_at": _utc(oldest_pending),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--correlation-prefix", required=True)
    clock_group = parser.add_mutually_exclusive_group(required=True)
    clock_group.add_argument("--clock-evidence", type=Path)
    clock_group.add_argument("--clock-evidence-base64")
    args = parser.parse_args(argv)
    prefix = str(args.correlation_prefix).strip()
    if len(prefix) < 16 or len(prefix) > 128:
        raise SystemExit("causation prefix length is invalid")
    result = asyncio.run(
        collect(
            prefix,
            clock=(
                secure_json(
                    args.clock_evidence,
                    label="three-site host clock evidence",
                    max_size=256 * 1024,
                )
                if args.clock_evidence is not None
                else _clock_from_base64(args.clock_evidence_base64)
            ),
        )
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
