#!/usr/bin/env python3
"""CLI dry-run / apply report for overtime request reconciliation.

Usage:
  python scripts/report_offer_overtime_reconciliation.py
  python scripts/report_offer_overtime_reconciliation.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _main(dry_run: bool, limit: int) -> int:
    from core.db import AsyncSessionLocal
    if not dry_run:
        # This CLI runs outside the API/bot startup path. Register the same
        # transactional listeners before an authoritative repair so its
        # OfferRequest update is durably emitted to the peer server.
        from core.events import setup_all_events

        setup_all_events()
    from core.services.offer_overtime_reconciliation_service import (
        reconcile_overtime_requests,
    )

    async with AsyncSessionLocal() as db:
        report = await reconcile_overtime_requests(
            db,
            dry_run=dry_run,
            limit=limit,
            flush=not dry_run,
        )
        if not dry_run:
            await db.commit()
    payload = {
        "dry_run": report.dry_run,
        "finding_counts": report.finding_counts,
        "status_counts": report.status_counts,
        "silent_owner_count": report.silent_owner_count,
        "repaired": [
            {
                "issue": item.issue,
                "request_public_id": item.request_public_id,
                "offer_public_id": item.offer_public_id,
                "offer_owner_user_id": item.offer_owner_user_id,
            }
            for item in report.repaired
        ],
        "findings": [
            {
                "issue": item.issue,
                "request_public_id": item.request_public_id,
                "offer_public_id": item.offer_public_id,
                "offer_owner_user_id": item.offer_owner_user_id,
                "detail": item.detail,
            }
            for item in report.findings
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply authoritative repairs (default is dry-run).",
    )
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(dry_run=not args.apply, limit=args.limit)))


if __name__ == "__main__":
    main()
