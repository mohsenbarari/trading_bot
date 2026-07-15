#!/usr/bin/env python3
"""Inspect or safely release sync rows quarantined by a rolling registry change."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg2

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.sync_protocol import current_sync_registry_fingerprint
from scripts.align_trade_number_sequence import normalize_database_url


REJECTION_REASON = "registry_fingerprint_mismatch"
REPAIR_CONFIRMATION = "RELEASE_REGISTRY_FINGERPRINT_QUARANTINE"


def validate_runtime_compatibility(
    *,
    expected_release_sha: str,
    expected_registry_fingerprint: str,
    actual_release_sha: str,
    actual_registry_fingerprint: str,
) -> None:
    if not expected_release_sha or actual_release_sha != expected_release_sha:
        raise RuntimeError("runtime release SHA does not match the orchestrator release")
    if (
        not expected_registry_fingerprint
        or actual_registry_fingerprint != expected_registry_fingerprint
    ):
        raise RuntimeError("runtime sync-registry fingerprint does not match the peer-verified fingerprint")


def inspect_or_repair(
    database_url: str,
    *,
    repair: bool,
) -> dict[str, int | str]:
    with psycopg2.connect(normalize_database_url(database_url), connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM change_log
                WHERE synced = false
                  AND quarantined_at IS NOT NULL
                  AND last_delivery_error = %s
                """,
                (REJECTION_REASON,),
            )
            matched = int(cursor.fetchone()[0] or 0)
            released = 0
            if repair and matched:
                cursor.execute(
                    """
                    UPDATE change_log
                    SET delivery_attempt_count = 0,
                        last_delivery_error = NULL,
                        last_delivery_attempt_at = NULL,
                        next_delivery_attempt_at = NULL,
                        quarantined_at = NULL
                    WHERE synced = false
                      AND quarantined_at IS NOT NULL
                      AND last_delivery_error = %s
                    """,
                    (REJECTION_REASON,),
                )
                released = int(cursor.rowcount or 0)

    return {
        "status": "repaired" if repair else "inspected",
        "matched": matched,
        "released": released,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "repair"))
    parser.add_argument("--expected-release-sha", required=True)
    parser.add_argument("--expected-registry-fingerprint", required=True)
    parser.add_argument("--confirm", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repair = args.action == "repair"
    if repair and args.confirm != REPAIR_CONFIRMATION:
        raise RuntimeError(f"repair requires --confirm {REPAIR_CONFIRMATION}")

    database_url = (os.environ.get("SYNC_DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError("SYNC_DATABASE_URL is required")
    actual_release_sha = (os.environ.get("RELEASE_SHA") or "").strip()
    actual_registry_fingerprint = current_sync_registry_fingerprint()
    validate_runtime_compatibility(
        expected_release_sha=args.expected_release_sha,
        expected_registry_fingerprint=args.expected_registry_fingerprint,
        actual_release_sha=actual_release_sha,
        actual_registry_fingerprint=actual_registry_fingerprint,
    )
    result = inspect_or_repair(database_url, repair=repair)
    result.update(
        {
            "release_sha": actual_release_sha,
            "registry_fingerprint": actual_registry_fingerprint,
        }
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
