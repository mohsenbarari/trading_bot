#!/usr/bin/env python3
"""Dry-run-first sync parity repair and current-state replay tool."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings
from core.db import AsyncSessionLocal
from core.server_routing import default_peer_server_url, peer_server_url_for
from core.sync_repair import (
    build_current_state_replay_item,
    build_repair_plan,
    build_signed_headers,
    build_watermark_repair_payload,
    load_row_by_identity,
    summarize_replay_item,
)


def _read_json_file(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _parse_identity(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("--identity must be a JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError("--identity must be a JSON object")
    return payload


def _target_url(args: argparse.Namespace) -> str:
    target = args.target_url or (
        peer_server_url_for(args.target_server) if getattr(args, "target_server", None) else default_peer_server_url()
    )
    if not target:
        raise ValueError("target URL is required for apply; pass --target-url or configure peer URL")
    return str(target).rstrip("/")


def _sync_api_key(args: argparse.Namespace) -> str:
    api_key = args.sync_api_key or os.getenv("SYNC_API_KEY") or getattr(settings, "sync_api_key", None)
    if not api_key:
        raise ValueError("SYNC_API_KEY is required for apply")
    return api_key


def _send_items(target_url: str, api_key: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    body = json.dumps(items, sort_keys=True, default=str, separators=(",", ":"))
    request = urllib.request.Request(
        f"{target_url}/api/sync/receive",
        data=body.encode("utf-8"),
        headers=build_signed_headers(api_key, body),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response_body = response.read()
            status_code = response.getcode()
    except urllib.error.HTTPError as exc:
        response_body = exc.read()
        status_code = exc.code

    try:
        payload = json.loads(response_body.decode("utf-8"))
    except Exception:
        payload = {"status": "invalid-json", "response_size_bytes": len(response_body)}
    if status_code != 200:
        raise RuntimeError(f"peer returned HTTP {status_code}: {json.dumps(payload, sort_keys=True, default=str)}")
    return payload


def plan_command(args: argparse.Namespace) -> int:
    local_snapshot = _read_json_file(args.local_snapshot)
    peer_snapshot = _read_json_file(args.peer_snapshot)
    payload = build_repair_plan(
        local_snapshot,
        peer_snapshot,
        direction=args.direction,
        sample_limit=args.sample_limit,
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


async def replay_row_command(args: argparse.Namespace) -> int:
    identity = _parse_identity(args.identity)
    source_sequence = args.source_sequence
    if args.apply and not args.confirm_write:
        raise ValueError("--apply requires --confirm-write")
    if args.apply and source_sequence is None:
        raise ValueError("--apply requires --source-sequence so receiver watermarks remain auditable")

    async with AsyncSessionLocal() as db:
        row = await load_row_by_identity(db, args.table, identity)

    item = build_current_state_replay_item(
        table_name=args.table,
        row=row,
        operation=args.operation,
        source_server=args.source_server,
        source_sequence=source_sequence,
    )
    summary = summarize_replay_item(item)
    summary["identity"] = {
        "fields": sorted(identity),
        "hash": summary["record_parity"]["identity_hash"] if summary.get("record_parity") else None,
    }

    if not args.apply:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0

    response = _send_items(_target_url(args), _sync_api_key(args), [item])
    output = {
        **summary,
        "dry_run": False,
        "target_url_hash": __import__("hashlib").sha256(_target_url(args).encode()).hexdigest()[:16],
        "peer_response": response,
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0 if response.get("status") in {"success", "ok"} and int(response.get("errors") or 0) == 0 else 2


def watermark_command(args: argparse.Namespace) -> int:
    payload = build_watermark_repair_payload(
        source_server=args.source_server,
        aggregate_table=args.aggregate_table,
        aggregate_key=args.aggregate_key,
        source_sequence=args.source_sequence,
        payload_hash=args.payload_hash,
        operation=args.operation,
        record_id=args.record_id,
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dry-run-first sync parity repair and replay tool.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="build a dry-run repair plan from two parity snapshots")
    plan.add_argument("--local-snapshot", required=True)
    plan.add_argument("--peer-snapshot", required=True)
    plan.add_argument("--direction", choices=("local-to-peer", "peer-to-local"), default="local-to-peer")
    plan.add_argument("--sample-limit", type=int, default=5)

    replay = subparsers.add_parser("replay-row", help="dry-run or apply current-state replay for one row")
    replay.add_argument("--table", required=True)
    replay.add_argument("--identity", required=True, help='JSON object, e.g. {"offer_public_id":"ofr_1"}')
    replay.add_argument("--operation", choices=("INSERT", "UPDATE"), default="UPDATE")
    replay.add_argument("--source-server", choices=("foreign", "iran"))
    replay.add_argument("--source-sequence", type=int)
    replay.add_argument("--target-server", choices=("foreign", "iran"))
    replay.add_argument("--target-url")
    replay.add_argument("--sync-api-key")
    replay.add_argument("--apply", action="store_true")
    replay.add_argument("--confirm-write", action="store_true")

    watermark = subparsers.add_parser("watermark", help="emit dry-run SQL for repairing sync apply watermark")
    watermark.add_argument("--source-server", required=True, choices=("foreign", "iran"))
    watermark.add_argument("--aggregate-table", required=True)
    watermark.add_argument("--aggregate-key", required=True)
    watermark.add_argument("--source-sequence", required=True, type=int)
    watermark.add_argument("--payload-hash", required=True)
    watermark.add_argument("--operation", required=True)
    watermark.add_argument("--record-id")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "plan":
        return plan_command(args)
    if args.command == "replay-row":
        return asyncio.run(replay_row_command(args))
    if args.command == "watermark":
        return watermark_command(args)
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
