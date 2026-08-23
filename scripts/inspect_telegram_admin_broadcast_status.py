#!/usr/bin/env python3
"""Read-only status counts for one Telegram admin broadcast.

This command never creates, queues, or sends a broadcast. It prints only
privacy-safe receipt totals. No chat id, file_id, or personal identity.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.db import AsyncSessionLocal
from core.services.telegram_admin_broadcast_service import (
    TelegramAdminBroadcastValidationError,
    inspect_telegram_admin_broadcast_status,
)


async def _run(broadcast_id: int) -> int:
    async with AsyncSessionLocal() as db:
        try:
            counts = await inspect_telegram_admin_broadcast_status(
                db,
                broadcast_id=broadcast_id,
            )
        except TelegramAdminBroadcastValidationError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    print(
        json.dumps(
            {
                "broadcast_id": counts.broadcast_id,
                "broadcast_status": counts.broadcast_status,
                "content_kind": counts.content_kind,
                "pending": counts.pending,
                "sending": counts.sending,
                "retryable_failed": counts.retryable_failed,
                "sent": counts.sent,
                "skipped": counts.skipped,
                "terminal_failed": counts.terminal_failed,
            },
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect Telegram admin broadcast receipt counts."
    )
    parser.add_argument("broadcast_id", type=int)
    args = parser.parse_args()
    return asyncio.run(_run(args.broadcast_id))


if __name__ == "__main__":
    raise SystemExit(main())
