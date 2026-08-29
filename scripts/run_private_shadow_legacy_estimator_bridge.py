#!/usr/bin/env python3
"""Orchestrate the temporary Shadow → Legacy estimator compatibility bridge.

Order: preflight → quick_check → private/group Market Store projection under
the existing Market Store writer lock → group conversation projection under
the existing conversation writer lock → atomic heartbeat.  The two locks are
never held together.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.shadow_legacy_bridge import (
    AUTHORIZED_CUTOFF_UTC,
    GROUP_SOURCES,
    MARKET_BRIDGE_SOURCES,
    PRIVATE_SOURCES,
    BridgeError,
    empty_health,
    lag_seconds,
    project_shadow_to_legacy_market,
    require_product_mode_legacy,
    source_watermarks,
    sqlite_quick_check,
    utc_now,
    write_health,
)
from core.market_intelligence.market_store import (
    MarketStoreError,
    connect_market_store_read_only,
)
from core.market_intelligence.market_contracts import MarketStoreContractError
from scripts.project_group_market_to_estimator import (
    ProjectionError,
    project as project_groups,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadow-market-store", type=Path, required=True)
    parser.add_argument("--legacy-market-store", type=Path, required=True)
    parser.add_argument("--conversation-db", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--heartbeat", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--cutoff-utc", default=AUTHORIZED_CUTOFF_UTC)
    parser.add_argument("--market-lock", type=Path, required=True)
    parser.add_argument("--conversation-lock", type=Path, required=True)
    parser.add_argument("--lock-timeout-seconds", type=int, default=300)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-quick-check", action="store_true")
    return parser


def _read_release_sha(expected: str) -> str:
    digest = expected.strip().lower()
    if len(digest) != 40 or any(char not in "0123456789abcdef" for char in digest):
        raise BridgeError("release_sha_invalid")
    pinned = REPO_ROOT / "RELEASE_SHA"
    if pinned.is_file():
        recorded = pinned.read_text(encoding="utf-8").strip().lower()
        if recorded != digest:
            raise BridgeError("release_sha_mismatch")
    return digest


def _exclusive_lock(path: Path, timeout_seconds: int):
    import fcntl
    import time

    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
        os.chmod(path, 0o600)
    handle = open(path, "a+b")
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except BlockingIOError:
            if time.monotonic() >= deadline:
                handle.close()
                raise BridgeError("writer_lock_timeout")
            time.sleep(0.05)


def _watermarks(path: Path, sources: Sequence[str]) -> dict[str, str | None]:
    connection = connect_market_store_read_only(path)
    try:
        return source_watermarks(connection, sources)
    finally:
        connection.close()


def _reason(error: BaseException) -> str:
    if isinstance(error, BridgeError):
        token = str(error).strip() or type(error).__name__
        return token[:95]
    return type(error).__name__.upper()[:95]


def run(args: argparse.Namespace) -> dict[str, Any]:
    require_product_mode_legacy()
    release_sha = _read_release_sha(args.release_sha)
    started = utc_now()
    source_check = dest_market_check = dest_conversation_check = None
    if not args.skip_quick_check:
        source_check = sqlite_quick_check(args.shadow_market_store)
        dest_market_check = sqlite_quick_check(args.legacy_market_store)
        dest_conversation_check = sqlite_quick_check(args.conversation_db)
        if "OK" not in {source_check, dest_market_check, dest_conversation_check}:
            raise BridgeError("quick_check_failed")
        if source_check != "OK" or dest_market_check != "OK" or dest_conversation_check != "OK":
            raise BridgeError("quick_check_failed")
    market_lock = _exclusive_lock(args.market_lock, args.lock_timeout_seconds)
    try:
        market = project_shadow_to_legacy_market(
            source=args.shadow_market_store,
            destination=args.legacy_market_store,
            ledger=args.ledger,
            sources=MARKET_BRIDGE_SOURCES,
            cutoff_utc=args.cutoff_utc,
            dry_run=args.dry_run,
        )
    finally:
        market_lock.close()
    conversation_lock = _exclusive_lock(
        args.conversation_lock, args.lock_timeout_seconds
    )
    try:
        groups = (
            {
                "status": "DRY_RUN",
                "eligible_offers": 0,
                "eligible_trades": 0,
                "audit_offers": 0,
                "audit_trades": 0,
            }
            if args.dry_run
            else project_groups(args.shadow_market_store, args.conversation_db)
        )
    finally:
        conversation_lock.close()
    source_latest = _watermarks(args.shadow_market_store, sorted(MARKET_BRIDGE_SOURCES))
    dest_latest = _watermarks(args.legacy_market_store, sorted(MARKET_BRIDGE_SOURCES))
    completed = utc_now()
    health = {
        "schema": "private-shadow-legacy-bridge-health/1.0",
        "version": "private-shadow-legacy-bridge-v1",
        "release_sha": release_sha,
        "status": "OK",
        "started_at_utc": started,
        "completed_at_utc": completed,
        "source_latest_available_at_utc": source_latest,
        "destination_latest_available_at_utc": dest_latest,
        "projection_mode": market.get("mode"),
        "projected": int(market.get("projected") or 0),
        "updated": int(market.get("updated") or 0),
        "unchanged": int(market.get("unchanged") or 0),
        "removed": int(market.get("removed") or 0),
        "group_eligible_offers": int(groups.get("eligible_offers") or 0),
        "group_eligible_trades": int(groups.get("eligible_trades") or 0),
        "lag_seconds": lag_seconds(source_latest, dest_latest),
        "last_successful_run_at_utc": completed,
        "failure_reason_code": None,
        "source_quick_check": source_check,
        "destination_quick_check": {
            "market": dest_market_check,
            "conversation": dest_conversation_check,
        },
    }
    write_health(args.heartbeat, health)
    return {
        "status": "DRY_RUN" if args.dry_run else "OK",
        "market": {key: market[key] for key in market if key != "status"},
        "groups": {
            key: groups[key]
            for key in groups
            if key
            in {
                "status",
                "eligible_offers",
                "eligible_trades",
                "audit_offers",
                "audit_trades",
                "audit_only_offers",
                "audit_only_trades",
                "ineligible_removed",
            }
        },
        "lag_seconds": health["lag_seconds"],
        "release_sha": release_sha,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    heartbeat = args.heartbeat
    release_sha = str(args.release_sha or "").strip().lower()
    try:
        result = run(args)
    except (
        BridgeError,
        MarketStoreError,
        MarketStoreContractError,
        ProjectionError,
        OSError,
        sqlite3.Error,
        ValueError,
    ) as exc:
        failure = empty_health(
            release_sha=release_sha or "unknown",
            status="FAILED",
            reason=_reason(exc),
        )
        failure["started_at_utc"] = utc_now()
        failure["completed_at_utc"] = utc_now()
        try:
            write_health(heartbeat, failure)
        except OSError:
            pass
        print(json.dumps({"status": "FAILED", "reason": _reason(exc)}, sort_keys=True), flush=True)
        return 2
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
