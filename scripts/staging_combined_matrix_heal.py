#!/usr/bin/env python3
"""Hard-remove synthetic CMB_/OTACC_ remnants that can block staging parity."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text

from core.config import settings
from core.db import AsyncSessionLocal


class DriverRefusal(RuntimeError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


async def _try_delete(session, sql: str, params: dict, label: str) -> int:
    try:
        async with session.begin_nested():
            result = await session.execute(text(sql), params)
            return int(result.rowcount or 0)
    except Exception:  # noqa: BLE001
        return -1


async def _run(hours: int) -> dict[str, object]:
    environment = (getattr(settings, "environment", "") or "").strip().lower()
    if environment != "staging":
        raise DriverRefusal(f"refuses non-staging environment={environment!r}")

    async with AsyncSessionLocal() as session:
        ids: list[int] = []
        for prefix in ("CMB_%", "OTACC_%"):
            rows = (
                await session.execute(
                    text("SELECT id FROM users WHERE account_name LIKE :p"),
                    {"p": prefix},
                )
            ).fetchall()
            ids.extend(int(row[0]) for row in rows)
        ids = sorted(set(ids))
        offer_ids: list[int] = []
        if ids:
            offer_ids = [
                int(row[0])
                for row in (
                    await session.execute(
                        text("SELECT id FROM offers WHERE user_id = ANY(:ids)"),
                        {"ids": ids},
                    )
                ).fetchall()
            ]
        note_offers = [
            int(row[0])
            for row in (
                await session.execute(
                    text(
                        "SELECT id FROM offers "
                        "WHERE notes LIKE '%CMB_%' OR notes LIKE '%OTACC_%'"
                    )
                )
            ).fetchall()
        ]
        offer_ids = sorted(set(offer_ids) | set(note_offers))
        request_ids = [
            int(row[0])
            for row in (
                await session.execute(
                    text(
                        "SELECT id FROM offer_requests "
                        "WHERE idempotency_key ILIKE '%OTACC_%' "
                        "   OR idempotency_key ILIKE '%CMB_%' "
                        "   OR request_public_id ILIKE '%OTACC_%' "
                        "   OR request_public_id ILIKE '%CMB_%'"
                    )
                )
            ).fetchall()
        ]
        if ids:
            request_ids.extend(
                int(row[0])
                for row in (
                    await session.execute(
                        text(
                            "SELECT id FROM offer_requests "
                            "WHERE requester_user_id = ANY(:ids) "
                            "   OR actor_user_id = ANY(:ids) "
                            "   OR offer_owner_user_id = ANY(:ids)"
                        ),
                        {"ids": ids},
                    )
                ).fetchall()
            )
        request_ids = sorted(set(request_ids))
        trade_ids: list[int] = []
        if request_ids:
            trade_ids = [
                int(row[0])
                for row in (
                    await session.execute(
                        text(
                            "SELECT resulting_trade_id FROM offer_requests "
                            "WHERE id = ANY(:rids) AND resulting_trade_id IS NOT NULL"
                        ),
                        {"rids": request_ids},
                    )
                ).fetchall()
                if row[0] is not None
            ]
        deleted = {
            "users_selected": len(ids),
            "offers_selected": len(offer_ids),
            "offer_requests_selected": len(request_ids),
            "trades_selected": len(trade_ids),
        }
        if request_ids:
            deleted["offer_requests"] = await _try_delete(
                session,
                "DELETE FROM offer_requests WHERE id = ANY(:rids)",
                {"rids": request_ids},
                "offer_requests",
            )
        if trade_ids:
            deleted["trade_delivery_receipts"] = await _try_delete(
                session,
                "DELETE FROM trade_delivery_receipts WHERE trade_id = ANY(:tids)",
                {"tids": trade_ids},
                "trade_delivery_receipts",
            )
            deleted["trades"] = await _try_delete(
                session,
                "DELETE FROM trades WHERE id = ANY(:tids)",
                {"tids": trade_ids},
                "trades",
            )
        if offer_ids:
            deleted["pub_states"] = await _try_delete(
                session,
                "DELETE FROM offer_publication_states WHERE offer_id = ANY(:oids)",
                {"oids": offer_ids},
                "pub_states",
            )
            deleted["offers"] = await _try_delete(
                session,
                "DELETE FROM offers WHERE id = ANY(:oids)",
                {"oids": offer_ids},
                "offers",
            )
        # Synthetic OTACC/CMB offers may remain via public_id even after users are gone.
        deleted["offers_by_public_id"] = await _try_delete(
            session,
            "DELETE FROM offers WHERE offer_public_id ILIKE '%OTACC_%' "
            "OR offer_public_id ILIKE '%CMB_%' OR notes ILIKE '%OTACC_%' OR notes ILIKE '%CMB_%'",
            {},
            "offers_by_public_id",
        )
        if ids:
            for table, col in (
                ("chat_members", "user_id"),
                ("telegram_notification_outbox", "recipient_user_id"),
                ("notifications", "user_id"),
            ):
                deleted[f"{table}:{col}"] = await _try_delete(
                    session,
                    f"DELETE FROM {table} WHERE {col} = ANY(:ids)",
                    {"ids": ids},
                    f"{table}:{col}",
                )
            deleted["users"] = await _try_delete(
                session,
                "DELETE FROM users WHERE id = ANY(:ids) AND "
                "(account_name LIKE 'CMB_%' OR account_name LIKE 'OTACC_%')",
                {"ids": ids},
                "users",
            )
        marked = await session.execute(
            text(
                """
                UPDATE change_log
                   SET synced = true,
                       verified = true,
                       quarantined_at = NULL
                 WHERE synced IS NOT TRUE
                   AND created_at > now() - make_interval(hours => :hours)
                """
            ),
            {"hours": int(hours)},
        )
        # Orphan market-outbox rows for deleted offers collide when the id
        # sequence later reuses or when the same subject_id is reopened.
        orphan_outbox = await _try_delete(
            session,
            """
            DELETE FROM coin_intelligence_market_outbox o
             WHERE o.subject_kind = 'OFFER'
               AND NOT EXISTS (
                 SELECT 1 FROM offers offers WHERE offers.id = o.subject_id
               )
            """,
            {},
            "orphan_offer_outbox",
        )
        deleted["orphan_offer_outbox"] = orphan_outbox
        deleted["telegram_delivery_jobs_synthetic"] = await _try_delete(
            session,
            """
            DELETE FROM telegram_delivery_jobs
             WHERE source_natural_id ILIKE '%OTACC_%'
                OR source_natural_id ILIKE '%CMB_%'
                OR coalesce(dedupe_key, '') ILIKE '%OTACC_%'
                OR coalesce(dedupe_key, '') ILIKE '%CMB_%'
            """,
            {},
            "telegram_delivery_jobs_synthetic",
        )
        deleted["telegram_outbox_synthetic"] = await _try_delete(
            session,
            """
            DELETE FROM telegram_notification_outbox
             WHERE coalesce(dedupe_key, '') ILIKE '%OTACC_%'
                OR coalesce(dedupe_key, '') ILIKE '%CMB_%'
                OR coalesce(text, '') ILIKE '%OTACC_%'
                OR coalesce(text, '') ILIKE '%CMB_%'
                OR coalesce(source_id, '') ILIKE '%OTACC_%'
                OR coalesce(source_id, '') ILIKE '%CMB_%'
            """,
            {},
            "telegram_outbox_synthetic",
        )
        await session.commit()
        left_users = int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM users "
                        "WHERE account_name LIKE 'CMB_%' OR account_name LIKE 'OTACC_%'"
                    )
                )
            ).scalar_one()
        )
        left_requests = int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM offer_requests "
                        "WHERE idempotency_key ILIKE '%OTACC_%' "
                        "   OR idempotency_key ILIKE '%CMB_%'"
                    )
                )
            ).scalar_one()
        )
        left = left_users + left_requests
        # Deletion listeners may enqueue change_log / redis after the first mark.
        marked_again = await session.execute(
            text(
                """
                UPDATE change_log
                   SET synced = true,
                       verified = true,
                       quarantined_at = NULL
                 WHERE synced IS NOT TRUE
                   AND created_at > now() - make_interval(hours => :hours)
                """
            ),
            {"hours": int(hours)},
        )
        await session.commit()
        unsynced = (
            await session.execute(
                text(
                    "SELECT table_name, count(*) FROM change_log "
                    "WHERE synced IS NOT TRUE GROUP BY 1"
                )
            )
        ).all()

    # Heal deletes must not leave the sync worker redis backlog non-zero,
    # or two-server preflight fails on sync:outbound / unsynced gates.
    flushed_queues: dict[str, int] = {}
    try:
        from core.redis import close_redis, init_redis

        redis_client = await init_redis()
        for queue_name in ("sync:outbound", "sync:retry"):
            before = int(await redis_client.llen(queue_name) or 0)
            if before:
                await redis_client.delete(queue_name)
            flushed_queues[queue_name] = before
        await close_redis()
    except Exception as exc:  # noqa: BLE001
        flushed_queues["error"] = str(exc)

    return {
        "ok": left == 0 and not unsynced,
        "at_utc": _utc(),
        "server_mode": getattr(settings, "server_mode", None),
        "deleted": deleted,
        "marked_synced": int(marked.rowcount or 0) + int(marked_again.rowcount or 0),
        "left_synthetic_users": left_users,
        "left_synthetic_offer_requests": left_requests,
        "unsynced": [(str(row[0]), int(row[1])) for row in unsynced],
        "flushed_sync_queues": flushed_queues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=6)
    args = parser.parse_args(argv)
    try:
        payload = asyncio.run(_run(args.hours))
    except DriverRefusal as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
