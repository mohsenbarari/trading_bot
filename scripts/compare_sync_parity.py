#!/usr/bin/env python3
"""Build or compare redacted cross-server sync parity snapshots."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.sync_parity import build_database_parity_snapshot, compare_parity_snapshots
from core.sync_parity_observability import infer_parity_comparison_mode, summarize_parity_comparison


def _read_json(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _fetch_json(url: str | None, api_key: str | None) -> dict[str, Any] | None:
    if not url:
        return None
    headers = {}
    if api_key:
        headers["X-Observability-Api-Key"] = api_key
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{url} returned a non-object JSON payload")
    return payload


def _post_json(url: str, payload: dict[str, Any], api_key: str | None) -> None:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-Observability-Api-Key"] = api_key
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _table_count(snapshot: dict[str, Any]) -> int | None:
    tables = snapshot.get("tables")
    return len(tables) if isinstance(tables, dict) else None


def _snapshot_timestamp(snapshot: dict[str, Any]) -> str | None:
    for key in ("snapshot_at", "generated_at", "created_at", "captured_at", "observed_at"):
        value = snapshot.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _optional_arg(args: argparse.Namespace, name: str) -> Any:
    return getattr(args, name, None)


def _build_artifact_metadata(
    args: argparse.Namespace,
    *,
    local_snapshot: dict[str, Any],
    peer_snapshot: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}

    string_values = {
        "local_server_mode": _optional_arg(args, "local_server_mode") or local_snapshot.get("server_mode"),
        "peer_server_mode": _optional_arg(args, "peer_server_mode") or peer_snapshot.get("server_mode"),
        "local_release_sha": _optional_arg(args, "local_release_sha") or local_snapshot.get("release_sha"),
        "peer_release_sha": _optional_arg(args, "peer_release_sha") or peer_snapshot.get("release_sha"),
        "snapshot_mode": _optional_arg(args, "snapshot_mode") or mode,
        "local_snapshot_at": _optional_arg(args, "local_snapshot_at") or _snapshot_timestamp(local_snapshot),
        "peer_snapshot_at": _optional_arg(args, "peer_snapshot_at") or _snapshot_timestamp(peer_snapshot),
        "comparison_artifact_hash": _optional_arg(args, "comparison_artifact_hash"),
        "artifact_reference": _optional_arg(args, "artifact_reference") or _optional_arg(args, "artifact_path"),
    }
    for key, value in string_values.items():
        if value not in (None, ""):
            metadata[key] = str(value)

    local_table_count = _optional_arg(args, "local_table_count") or _table_count(local_snapshot)
    peer_table_count = _optional_arg(args, "peer_table_count") or _table_count(peer_snapshot)
    if local_table_count is not None:
        metadata["local_table_count"] = int(local_table_count)
    if peer_table_count is not None:
        metadata["peer_table_count"] = int(peer_table_count)

    return metadata


async def _snapshot(args: argparse.Namespace) -> int:
    # Database configuration is required only for a live capture.  Keeping
    # this import local allows an offline controller to compare redacted JSON
    # snapshots without inheriting any site database credentials.
    from core.db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        payload = await build_database_parity_snapshot(
            db,
            mode=args.mode,
            max_rows_per_table=args.max_rows_per_table,
        )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def _load_snapshot(*, path: str | None, url: str | None, api_key: str | None) -> dict[str, Any]:
    payload = _read_json(path)
    if payload is not None:
        return payload
    payload = _fetch_json(url, api_key)
    if payload is not None:
        return payload
    raise ValueError("snapshot source requires either a file path or URL")


def _compare(args: argparse.Namespace) -> int:
    local_snapshot = _load_snapshot(
        path=args.local_snapshot,
        url=args.local_url,
        api_key=args.local_observability_key or os.getenv("LOCAL_OBSERVABILITY_API_KEY"),
    )
    peer_snapshot = _load_snapshot(
        path=args.peer_snapshot,
        url=args.peer_url,
        api_key=args.peer_observability_key or os.getenv("PEER_OBSERVABILITY_API_KEY"),
    )
    payload = compare_parity_snapshots(local_snapshot, peer_snapshot, sample_limit=args.sample_limit)
    payload["mode"] = infer_parity_comparison_mode(local_snapshot, peer_snapshot)
    payload["compared_at"] = _utc_now_iso()
    artifact_metadata = _build_artifact_metadata(
        args,
        local_snapshot=local_snapshot,
        peer_snapshot=peer_snapshot,
        mode=payload["mode"],
    )
    if artifact_metadata:
        payload["artifact_metadata"] = artifact_metadata
    payload["summary"] = summarize_parity_comparison(payload, mode=payload["mode"], observed_at=payload["compared_at"])
    for record_url in args.record_url or []:
        _post_json(
            record_url,
            payload,
            args.record_observability_key or os.getenv("RECORD_OBSERVABILITY_API_KEY"),
        )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["status"] in {"ok", "non_business_difference"} else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build or compare redacted cross-server sync parity snapshots.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser("snapshot", help="build a local DB parity snapshot")
    snapshot.add_argument("--mode", choices=("quick", "deep"), default="quick")
    snapshot.add_argument("--max-rows-per-table", type=int, default=5000)

    compare = subparsers.add_parser("compare", help="compare two parity snapshots")
    compare.add_argument("--local-snapshot")
    compare.add_argument("--peer-snapshot")
    compare.add_argument("--local-url")
    compare.add_argument("--peer-url")
    compare.add_argument("--local-observability-key")
    compare.add_argument("--peer-observability-key")
    compare.add_argument("--sample-limit", type=int, default=5)
    compare.add_argument("--record-url", action="append", help="POST the comparison result to a /api/sync/parity/status endpoint.")
    compare.add_argument("--record-observability-key")
    compare.add_argument("--local-server-mode")
    compare.add_argument("--peer-server-mode")
    compare.add_argument("--local-release-sha")
    compare.add_argument("--peer-release-sha")
    compare.add_argument("--snapshot-mode")
    compare.add_argument("--local-table-count", type=int)
    compare.add_argument("--peer-table-count", type=int)
    compare.add_argument("--local-snapshot-at")
    compare.add_argument("--peer-snapshot-at")
    compare.add_argument("--comparison-artifact-hash")
    compare.add_argument("--artifact-reference")
    compare.add_argument("--artifact-path", help="Alias for --artifact-reference when the retained artifact is a local path.")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "snapshot":
        return asyncio.run(_snapshot(args))
    if args.command == "compare":
        return _compare(args)
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
