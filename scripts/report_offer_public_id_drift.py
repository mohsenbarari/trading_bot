#!/usr/bin/env python3
"""Dry-run reporting for cross-server offer_public_id drift.

The script is intentionally read-only. It can produce a local snapshot from the
current database, then compare two snapshots captured from the Iran/foreign
servers without rewriting historical public ids.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.db import AsyncSessionLocal


TERMINAL_OFFER_STATUSES = {"completed", "cancelled", "expired"}
ACTIVE_OFFER_STATUS = "active"

SNAPSHOT_QUERY = text(
    """
    SELECT
        o.id,
        o.offer_public_id,
        o.home_server,
        CAST(o.status AS TEXT) AS status,
        o.version_id,
        o.created_at,
        o.updated_at,
        o.expired_at,
        COALESCE(orq.offer_requests_count, 0) AS offer_requests_count,
        COALESCE(ops.publication_states_count, 0) AS publication_states_count,
        COALESCE(tr.trades_count, 0) AS trades_count
    FROM offers o
    LEFT JOIN (
        SELECT offer_public_id, COUNT(*) AS offer_requests_count
        FROM offer_requests
        GROUP BY offer_public_id
    ) orq ON orq.offer_public_id = o.offer_public_id
    LEFT JOIN (
        SELECT offer_public_id, COUNT(*) AS publication_states_count
        FROM offer_publication_states
        GROUP BY offer_public_id
    ) ops ON ops.offer_public_id = o.offer_public_id
    LEFT JOIN (
        SELECT offer_id, COUNT(*) AS trades_count
        FROM trades
        WHERE offer_id IS NOT NULL
        GROUP BY offer_id
    ) tr ON tr.offer_id = o.id
    ORDER BY o.id
    """
)


def json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def normalize_status(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        value = value.value
    return str(value).strip().lower() or None


def dependency_count(row: Mapping[str, Any]) -> int:
    return (
        int(row.get("offer_requests_count") or 0)
        + int(row.get("publication_states_count") or 0)
        + int(row.get("trades_count") or 0)
    )


def classify_mismatch(local: Mapping[str, Any], peer: Mapping[str, Any]) -> str:
    local_status = normalize_status(local.get("status"))
    peer_status = normalize_status(peer.get("status"))
    local_dependencies = dependency_count(local)
    peer_dependencies = dependency_count(peer)

    if ACTIVE_OFFER_STATUS in {local_status, peer_status}:
        return "active_public_id_repair_blocked"
    if local_dependencies or peer_dependencies:
        return "dependent_public_id_repair_blocked"
    if local_status in TERMINAL_OFFER_STATUSES and peer_status in TERMINAL_OFFER_STATUSES:
        return "inactive_historical_exemption_candidate"
    return "manual_review_required"


def normalize_snapshot_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        normalized.append(
            {
                "id": int(row["id"]),
                "offer_public_id": str(row.get("offer_public_id") or ""),
                "home_server": row.get("home_server"),
                "status": normalize_status(row.get("status")),
                "version_id": int(row.get("version_id") or 0),
                "created_at": json_safe(row.get("created_at")),
                "updated_at": json_safe(row.get("updated_at")),
                "expired_at": json_safe(row.get("expired_at")),
                "offer_requests_count": int(row.get("offer_requests_count") or 0),
                "publication_states_count": int(row.get("publication_states_count") or 0),
                "trades_count": int(row.get("trades_count") or 0),
            }
        )
    return normalized


def snapshot_rows(payload: Mapping[str, Any] | list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return normalize_snapshot_rows(payload)
    rows = payload.get("offers", [])
    if not isinstance(rows, list):
        raise ValueError("snapshot payload must contain an offers list")
    return normalize_snapshot_rows(rows)


def compare_snapshots(local_payload: Mapping[str, Any], peer_payload: Mapping[str, Any]) -> dict[str, Any]:
    local_by_id = {row["id"]: row for row in snapshot_rows(local_payload)}
    peer_by_id = {row["id"]: row for row in snapshot_rows(peer_payload)}
    common_ids = sorted(set(local_by_id) & set(peer_by_id))

    mismatches = []
    for offer_id in common_ids:
        local = local_by_id[offer_id]
        peer = peer_by_id[offer_id]
        if local.get("offer_public_id") == peer.get("offer_public_id"):
            continue
        reason = classify_mismatch(local, peer)
        mismatches.append(
            {
                "id": offer_id,
                "classification": reason,
                "local_offer_public_id": local.get("offer_public_id"),
                "peer_offer_public_id": peer.get("offer_public_id"),
                "local_status": local.get("status"),
                "peer_status": peer.get("status"),
                "local_dependency_count": dependency_count(local),
                "peer_dependency_count": dependency_count(peer),
            }
        )

    by_classification: dict[str, int] = {}
    for mismatch in mismatches:
        key = str(mismatch["classification"])
        by_classification[key] = by_classification.get(key, 0) + 1

    blocking_classifications = {
        "active_public_id_repair_blocked",
        "dependent_public_id_repair_blocked",
        "manual_review_required",
    }
    blocking_count = sum(
        1 for mismatch in mismatches if mismatch["classification"] in blocking_classifications
    )

    return {
        "status": "ok",
        "dry_run": True,
        "writes_performed": False,
        "total_local_offers": len(local_by_id),
        "total_peer_offers": len(peer_by_id),
        "total_compared_by_legacy_id": len(common_ids),
        "missing_on_peer": sorted(set(local_by_id) - set(peer_by_id)),
        "missing_on_local": sorted(set(peer_by_id) - set(local_by_id)),
        "offer_public_id_mismatch_count": len(mismatches),
        "blocking_mismatch_count": blocking_count,
        "by_classification": by_classification,
        "mismatches": mismatches,
        "policy": {
            "active_public_id_repair_blocked": "Do not rewrite active offer public ids automatically.",
            "dependent_public_id_repair_blocked": "Do not rewrite public ids while dependent request/publication/trade rows reference the offer.",
            "inactive_historical_exemption_candidate": "Terminal offers without dependent references may be treated as historical parity exemptions.",
            "manual_review_required": "No automatic repair policy is defined for this row shape.",
        },
    }


async def build_snapshot(server_name: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(SNAPSHOT_QUERY)
        rows = [dict(row._mapping) for row in result.fetchall()]
    return {
        "status": "ok",
        "server": server_name,
        "dry_run": True,
        "writes_performed": False,
        "offers": normalize_snapshot_rows(rows),
    }


def load_json(path: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report offer_public_id drift without writing data.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser("snapshot", help="capture a read-only local DB snapshot")
    snapshot.add_argument("--server-name", default="local")

    compare = subparsers.add_parser("compare", help="compare two snapshot JSON files")
    compare.add_argument("--local-snapshot", required=True)
    compare.add_argument("--peer-snapshot", required=True)

    return parser.parse_args()


async def main_async() -> int:
    args = parse_args()
    if args.command == "snapshot":
        payload = await build_snapshot(args.server_name)
    else:
        payload = compare_snapshots(load_json(args.local_snapshot), load_json(args.peer_snapshot))
    print(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
