#!/usr/bin/env python3
"""Dry-run-first sync parity repair and current-state replay tool."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
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
    REPAIR_TOOL_SCHEMA_VERSION,
    REPLAY_IDENTITY_FIELDS_BY_TABLE,
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


def _target_url_hash(target_url: str) -> str:
    import hashlib

    return hashlib.sha256(target_url.encode()).hexdigest()[:16]


def _sync_api_key(args: argparse.Namespace) -> str:
    api_key = args.sync_api_key or os.getenv("SYNC_API_KEY") or getattr(settings, "sync_api_key", None)
    if not api_key:
        raise ValueError("SYNC_API_KEY is required for apply")
    return api_key


def _normalize_environment(value: Any) -> str:
    return str(value or "").strip().lower()


def _environment(args: argparse.Namespace) -> str:
    runtime_environment = _normalize_environment(getattr(settings, "environment", ""))
    cli_environment = _normalize_environment(getattr(args, "environment", None))
    if getattr(args, "apply", False) and not runtime_environment:
        raise ValueError("Runtime environment is required for repair apply")
    if runtime_environment and cli_environment and runtime_environment != cli_environment:
        raise ValueError("CLI environment does not match runtime settings; refusing repair apply")
    return runtime_environment or cli_environment


def _is_production_environment(args: argparse.Namespace) -> bool:
    return _environment(args) == "production"


def _current_git_ref() -> dict[str, str | None]:
    try:
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return {"branch": None, "commit": None}
    return {"branch": branch or None, "commit": commit or None}


def _identity_uses_raw_local_id(table_name: str, identity_fields: list[str]) -> bool:
    configured = REPLAY_IDENTITY_FIELDS_BY_TABLE.get(table_name, set())
    return identity_fields == ["id"] and bool(configured - {"id"})


def _manifest_required_keys() -> tuple[str, ...]:
    return (
        "schema_version",
        "type",
        "source_server",
        "target",
        "table",
        "operation",
        "identity_fields",
        "identity_hash",
        "expected_source_row_count",
        "expected_target_row_count_impact",
        "source_sequence",
        "environment",
        "before_parity_artifact_hash",
        "after_parity_command",
        "backup_artifact",
        "git_branch",
        "git_commit",
        "operator_approval_phrase",
    )


def _validate_replay_apply_manifest(
    args: argparse.Namespace,
    *,
    manifest: dict[str, Any],
    summary: dict[str, Any],
    target_url: str,
) -> None:
    missing = [key for key in _manifest_required_keys() if manifest.get(key) in (None, "", [])]
    if missing:
        raise ValueError(f"repair manifest is missing required evidence fields: {', '.join(missing)}")
    if manifest.get("schema_version") != REPAIR_TOOL_SCHEMA_VERSION:
        raise ValueError("repair manifest schema_version is not supported")
    if manifest.get("type") != "sync_repair_apply_manifest":
        raise ValueError("repair manifest type must be sync_repair_apply_manifest")
    if manifest.get("source_server") != getattr(args, "source_server", None):
        raise ValueError("repair manifest source_server does not match CLI")
    target = manifest.get("target")
    if isinstance(target, dict):
        manifest_target_hash = target.get("target_url_hash")
        manifest_target_server = target.get("target_server")
    else:
        manifest_target_hash = manifest.get("target_url_hash")
        manifest_target_server = None
    if not manifest_target_hash:
        raise ValueError("repair manifest target.target_url_hash is required")
    if manifest_target_server and manifest_target_server != getattr(args, "target_server", None):
        raise ValueError("repair manifest target_server does not match CLI")
    if manifest_target_hash and manifest_target_hash != _target_url_hash(target_url):
        raise ValueError("repair manifest target_url_hash does not match CLI target")
    if manifest.get("table") != summary.get("table"):
        raise ValueError("repair manifest table does not match replay item")
    if manifest.get("operation") != summary.get("operation"):
        raise ValueError("repair manifest operation does not match replay item")
    identity = summary.get("identity") if isinstance(summary.get("identity"), dict) else {}
    identity_fields = list(identity.get("fields") or [])
    if list(manifest.get("identity_fields") or []) != identity_fields:
        raise ValueError("repair manifest identity_fields do not match replay identity")
    if manifest.get("identity_hash") != identity.get("hash"):
        raise ValueError("repair manifest identity_hash does not match replay identity")
    if int(manifest.get("expected_source_row_count")) != 1:
        raise ValueError("repair manifest expected_source_row_count must be 1 for replay-row")
    if int(manifest.get("expected_target_row_count_impact")) != 1:
        raise ValueError("repair manifest expected_target_row_count_impact must be 1 for replay-row")
    if int(manifest.get("source_sequence")) != int(getattr(args, "source_sequence", 0)):
        raise ValueError("repair manifest source_sequence does not match CLI")
    environment = _environment(args)
    if _normalize_environment(manifest.get("environment")) != environment:
        raise ValueError("repair manifest environment does not match runtime settings")

    production = _is_production_environment(args)
    raw_local_id = _identity_uses_raw_local_id(str(summary.get("table") or ""), identity_fields)
    if raw_local_id and production:
        raise ValueError("production repair apply refuses raw local id identity")
    if raw_local_id and not getattr(args, "allow_local_id_identity", False):
        raise ValueError("raw local id identity requires --allow-local-id-identity outside production")

    if getattr(args, "operator_approval", None) != manifest.get("operator_approval_phrase"):
        raise ValueError("operator approval phrase does not match repair manifest")

    if production:
        if manifest.get("git_branch") != "main":
            raise ValueError("production repair apply requires a main-branch manifest")
        git_ref = _current_git_ref()
        if git_ref["branch"] != manifest.get("git_branch") or git_ref["commit"] != manifest.get("git_commit"):
            raise ValueError("production repair apply requires current git branch/commit to match manifest")


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
    payload["apply_requires_manifest"] = True
    payload["repair_apply_manifest_template"] = {
        "schema_version": REPAIR_TOOL_SCHEMA_VERSION,
        "type": "sync_repair_apply_manifest",
        "source_server": "<foreign|iran>",
        "target": {"target_server": "<foreign|iran>", "target_url_hash": "<sha256-16>"},
        "table": "<sync table>",
        "operation": "<INSERT|UPDATE>",
        "identity_fields": ["<natural identity field>"],
        "identity_hash": "<from replay-row dry-run summary.identity.hash>",
        "expected_source_row_count": 1,
        "expected_target_row_count_impact": 1,
        "source_sequence": "<auditable source sequence>",
        "environment": str(getattr(settings, "environment", "") or "<runtime environment>"),
        "before_parity_artifact_hash": "<sha256 of before parity artifact>",
        "after_parity_command": "<exact command to collect after parity evidence>",
        "backup_artifact": "<backup manifest/path/hash>",
        "git_branch": "<main for production>",
        "git_commit": "<release commit sha>",
        "operator_approval_phrase": "apply-sync-repair:<identity_hash>",
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


async def replay_row_command(args: argparse.Namespace) -> int:
    identity = _parse_identity(args.identity)
    source_sequence = args.source_sequence
    if args.apply and not args.confirm_write:
        raise ValueError("--apply requires --confirm-write")
    if args.apply and source_sequence is None:
        raise ValueError("--apply requires --source-sequence so receiver watermarks remain auditable")
    if args.apply and not getattr(args, "manifest", None):
        raise ValueError("--apply requires --manifest with repair evidence")
    if args.apply and not getattr(args, "operator_approval", None):
        raise ValueError("--apply requires --operator-approval matching the repair manifest")

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

    target_url = _target_url(args)
    manifest = _read_json_file(args.manifest)
    _validate_replay_apply_manifest(
        args,
        manifest=manifest,
        summary=summary,
        target_url=target_url,
    )
    response = _send_items(target_url, _sync_api_key(args), [item])
    output = {
        **summary,
        "dry_run": False,
        "target_url_hash": _target_url_hash(target_url),
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
    replay.add_argument("--environment", default=None, help="Execution environment; production enables stricter apply gates")
    replay.add_argument("--manifest", help="Repair apply manifest generated from reviewed dry-run evidence")
    replay.add_argument("--operator-approval", help="Approval phrase that must match the repair manifest")
    replay.add_argument(
        "--allow-local-id-identity",
        action="store_true",
        help="Allow raw local id identity outside production when no natural key can be used",
    )
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
