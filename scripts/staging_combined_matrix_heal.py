#!/usr/bin/env python3
"""Remove synthetic rows owned by one combined-matrix run.

This cleanup is deliberately prefix-scoped.  It never marks unrelated
``change_log`` rows as synced and never deletes the shared ``sync:outbound`` or
``sync:retry`` Redis queues.  If unrelated staging drift exists, preflight must
surface it instead of this helper hiding it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text

class DriverRefusal(RuntimeError):
    pass


_RUN_PREFIX_RE = re.compile(
    r"(?:CMB_[A-Za-z0-9_-]{4,116}|OTACC_[0-9]{14})\Z"
)
_PREFIX_BOUNDARY_SQL = "('_', ':', '-', ' ')"


def _starts_with_run_prefix(column: str) -> str:
    value = f"coalesce({column}, '')"
    return (
        f"left({value}, length(:prefix)) = :prefix AND "
        f"(length({value}) = length(:prefix) OR "
        f"substring({value} from length(:prefix) + 1 for 1) "
        f"IN {_PREFIX_BOUNDARY_SQL})"
    )


def _contains_run_prefix(column: str) -> str:
    value = f"coalesce({column}, '')"
    position = f"strpos({value}, :prefix)"
    return (
        f"{position} > 0 AND "
        f"(length({value}) = {position} + length(:prefix) - 1 OR "
        f"substring({value} from {position} + length(:prefix) for 1) "
        f"IN {_PREFIX_BOUNDARY_SQL})"
    )


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


async def _try_delete(session, sql: str, params: dict, label: str) -> int:
    try:
        async with session.begin_nested():
            result = await session.execute(text(sql), params)
            return int(result.rowcount or 0)
    except Exception:  # noqa: BLE001
        return -1


async def _select_ids(session, sql: str, params: dict) -> list[int]:
    return sorted(
        {
            int(row[0])
            for row in (await session.execute(text(sql), params)).fetchall()
            if row[0] is not None
        }
    )


def _change_log_delete_plan(
    prefix: str,
    record_ids_by_table: dict[str, list[int]],
) -> tuple[list[str], dict[str, object]]:
    predicates = [f"({_contains_run_prefix('data::text')})"]
    params: dict[str, object] = {"prefix": prefix}
    for index, (table_name, record_ids) in enumerate(
        sorted(record_ids_by_table.items())
    ):
        if not record_ids:
            continue
        table_key = f"change_table_{index}"
        ids_key = f"change_ids_{index}"
        predicates.append(
            f"(table_name = :{table_key} AND record_id = ANY(:{ids_key}))"
        )
        params[table_key] = table_name
        params[ids_key] = sorted(set(int(value) for value in record_ids))
    return predicates, params


def _validate_run_prefix(value: str) -> str:
    prefix = (value or "").strip()
    if _RUN_PREFIX_RE.fullmatch(prefix) is None:
        raise DriverRefusal(
            "run prefix must match CMB_[A-Za-z0-9_-]{4,116} or "
            "an exact OTACC_YYYYMMDDhhmmss execution stamp"
        )
    return prefix


async def _run(run_prefix: str) -> dict[str, object]:
    from core.config import settings
    from core.db import AsyncSessionLocal

    prefix = _validate_run_prefix(run_prefix)
    environment = (getattr(settings, "environment", "") or "").strip().lower()
    if environment != "staging":
        raise DriverRefusal(f"refuses non-staging environment={environment!r}")

    async with AsyncSessionLocal() as session:
        ids = [
            int(row[0])
            for row in (
                await session.execute(
                    text(
                        "SELECT id FROM users "
                        f"WHERE {_starts_with_run_prefix('account_name')}"
                    ),
                    {"prefix": prefix},
                )
            ).fetchall()
        ]
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
        prefix_offers = [
            int(row[0])
            for row in (
                await session.execute(
                    text(
                        "SELECT id FROM offers "
                        f"WHERE {_starts_with_run_prefix('notes')}"
                    ),
                    {"prefix": prefix},
                )
            ).fetchall()
        ]
        offer_ids = sorted(set(offer_ids) | set(prefix_offers))
        offer_public_ids: list[str] = []
        if offer_ids:
            offer_public_ids = [
                str(row[0])
                for row in (
                    await session.execute(
                        text(
                            "SELECT offer_public_id FROM offers "
                            "WHERE id = ANY(:ids) AND offer_public_id IS NOT NULL"
                        ),
                        {"ids": offer_ids},
                    )
                ).fetchall()
            ]
        request_ids = [
            int(row[0])
            for row in (
                await session.execute(
                    text(
                        "SELECT id FROM offer_requests "
                        f"WHERE ({_starts_with_run_prefix('idempotency_key')}) "
                        f"   OR ({_starts_with_run_prefix('request_public_id')})"
                    ),
                    {"prefix": prefix},
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
        if offer_ids:
            request_ids.extend(
                int(row[0])
                for row in (
                    await session.execute(
                        text(
                            "SELECT id FROM offer_requests "
                            "WHERE local_offer_id = ANY(:offer_ids)"
                        ),
                        {"offer_ids": offer_ids},
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
        if offer_ids:
            trade_ids.extend(
                int(row[0])
                for row in (
                    await session.execute(
                        text("SELECT id FROM trades WHERE offer_id = ANY(:offer_ids)"),
                        {"offer_ids": offer_ids},
                    )
                ).fetchall()
            )
        trade_ids = sorted(set(trade_ids))
        receipt_ids: list[int] = []
        private_trade_job_ids: list[int] = []
        if trade_ids or offer_ids:
            from core.telegram_delivery_trade_result_binding import (
                trade_result_queue_job_id_from_receipt,
            )

            receipt_conditions: list[str] = []
            receipt_params: dict[str, object] = {}
            if trade_ids:
                receipt_conditions.append("trade_id = ANY(:trade_ids)")
                receipt_params["trade_ids"] = trade_ids
            if offer_ids:
                receipt_conditions.append("offer_id = ANY(:offer_ids)")
                receipt_params["offer_ids"] = offer_ids
            receipt_rows = (
                await session.execute(
                    text(
                        "SELECT id, worker_id FROM trade_delivery_receipts "
                        "WHERE " + " OR ".join(receipt_conditions)
                    ),
                    receipt_params,
                )
            ).fetchall()
            receipt_ids = sorted({int(row[0]) for row in receipt_rows})
            for row in receipt_rows:
                job_id = trade_result_queue_job_id_from_receipt(
                    SimpleNamespace(worker_id=row[1])
                )
                if job_id is not None:
                    private_trade_job_ids.append(job_id)
            private_trade_job_ids = sorted(set(private_trade_job_ids))
        deleted = {
            "run_prefix": prefix,
            "users_selected": len(ids),
            "offers_selected": len(offer_ids),
            "offer_requests_selected": len(request_ids),
            "trades_selected": len(trade_ids),
            "trade_receipts_selected": len(receipt_ids),
            "private_trade_jobs_selected": len(private_trade_job_ids),
        }
        job_predicates = [
            f"({_contains_run_prefix('source_natural_id')})",
            f"({_contains_run_prefix('dedupe_key')})",
            f"({_contains_run_prefix('run_id')})",
            f"({_contains_run_prefix('payload::text')})",
        ]
        job_params: dict[str, object] = {"prefix": prefix}
        if offer_public_ids:
            job_predicates.append("source_natural_id = ANY(:public_ids)")
            job_params["public_ids"] = offer_public_ids
        if private_trade_job_ids:
            job_predicates.append("id = ANY(:private_job_ids)")
            job_params["private_job_ids"] = private_trade_job_ids
        synthetic_job_ids = [
            int(row[0])
            for row in (
                await session.execute(
                    text(
                        "SELECT id FROM telegram_delivery_jobs WHERE "
                        + " OR ".join(job_predicates)
                    ),
                    job_params,
                )
            ).fetchall()
        ]
        synthetic_job_ids = sorted(set(synthetic_job_ids))
        deleted["telegram_delivery_jobs_selected"] = len(synthetic_job_ids)

        outbox_predicates = [
            f"({_contains_run_prefix('dedupe_key')})",
            f"({_contains_run_prefix('text')})",
            f"({_contains_run_prefix('source_id')})",
        ]
        outbox_params: dict[str, object] = {"prefix": prefix}
        if offer_public_ids:
            outbox_predicates.append("source_id = ANY(:public_ids)")
            outbox_params["public_ids"] = offer_public_ids
        if synthetic_job_ids:
            outbox_predicates.append("queue_job_id = ANY(:job_ids)")
            outbox_params["job_ids"] = synthetic_job_ids
        if ids:
            outbox_predicates.append("recipient_user_id = ANY(:user_ids)")
            outbox_params["user_ids"] = ids

        publication_state_ids = (
            await _select_ids(
                session,
                "SELECT id FROM offer_publication_states "
                "WHERE offer_id = ANY(:offer_ids)",
                {"offer_ids": offer_ids},
            )
            if offer_ids
            else []
        )
        chat_member_ids = (
            await _select_ids(
                session,
                "SELECT id FROM chat_members WHERE user_id = ANY(:user_ids)",
                {"user_ids": ids},
            )
            if ids
            else []
        )
        notification_ids = (
            await _select_ids(
                session,
                "SELECT id FROM notifications WHERE user_id = ANY(:user_ids)",
                {"user_ids": ids},
            )
            if ids
            else []
        )
        notification_outbox_ids = await _select_ids(
            session,
            "SELECT id FROM telegram_notification_outbox WHERE "
            + " OR ".join(outbox_predicates),
            outbox_params,
        )

        # Remove only change-log rows whose source record was selected by this
        # exact synthetic namespace. Without this, hard-deleting the matrix
        # rows leaves deferred child updates behind and poisons the next
        # preflight even though no business row remains.
        change_log_record_ids = {
            "users": ids,
            "offers": offer_ids,
            "offer_requests": request_ids,
            "trades": trade_ids,
            "trade_delivery_receipts": receipt_ids,
            "offer_publication_states": publication_state_ids,
            "chat_members": chat_member_ids,
            "notifications": notification_ids,
            "telegram_notification_outbox": notification_outbox_ids,
        }
        change_log_predicates, change_log_params = _change_log_delete_plan(
            prefix,
            change_log_record_ids,
        )
        deleted["change_log"] = await _try_delete(
            session,
            "DELETE FROM change_log WHERE " + " OR ".join(change_log_predicates),
            change_log_params,
            "change_log",
        )

        # RESTRICT children must be removed before their synthetic queue jobs.
        # Every delete remains bound to job ids selected by this exact CMB_
        # prefix; unrelated staging queue data is never touched.
        if synthetic_job_ids:
            for table, predicate in (
                (
                    "market_channel_notice_receipts",
                    "queue_job_id = ANY(:job_ids)",
                ),
                (
                    "telegram_admin_broadcast_receipts",
                    "queue_job_id = ANY(:job_ids)",
                ),
                (
                    "telegram_scheduled_operations",
                    "queue_job_id = ANY(:job_ids)",
                ),
                (
                    "telegram_channel_membership_sagas",
                    "ban_job_id = ANY(:job_ids) OR unban_job_id = ANY(:job_ids)",
                ),
                (
                    "telegram_notification_outbox_by_queue_job",
                    "queue_job_id = ANY(:job_ids)",
                ),
            ):
                sql_table = (
                    "telegram_notification_outbox"
                    if table == "telegram_notification_outbox_by_queue_job"
                    else table
                )
                deleted[table] = await _try_delete(
                    session,
                    f"DELETE FROM {sql_table} WHERE {predicate}",
                    {"job_ids": synthetic_job_ids},
                    table,
                )

        deleted["telegram_outbox_by_offer"] = await _try_delete(
            session,
            "DELETE FROM telegram_notification_outbox WHERE "
            + " OR ".join(outbox_predicates),
            outbox_params,
            "telegram_outbox_by_offer",
        )
        deleted["telegram_delivery_jobs"] = await _try_delete(
            session,
            "DELETE FROM telegram_delivery_jobs WHERE "
            + " OR ".join(job_predicates),
            job_params,
            "telegram_delivery_jobs",
        )
        if request_ids:
            deleted["offer_requests"] = await _try_delete(
                session,
                "DELETE FROM offer_requests WHERE id = ANY(:rids)",
                {"rids": request_ids},
                "offer_requests",
            )
        if receipt_ids:
            deleted["trade_delivery_receipts"] = await _try_delete(
                session,
                "DELETE FROM trade_delivery_receipts WHERE id = ANY(:receipt_ids)",
                {"receipt_ids": receipt_ids},
                "trade_delivery_receipts",
            )
        if trade_ids:
            deleted["trades"] = await _try_delete(
                session,
                "DELETE FROM trades WHERE id = ANY(:tids)",
                {"tids": trade_ids},
                "trades",
            )
        if offer_ids:
            deleted["market_outbox"] = await _try_delete(
                session,
                """
                DELETE FROM coin_intelligence_market_outbox
                 WHERE subject_kind = 'OFFER' AND subject_id = ANY(:oids)
                """,
                {"oids": offer_ids},
                "market_outbox",
            )
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
                f"({_starts_with_run_prefix('account_name')})",
                {"ids": ids, "prefix": prefix},
                "users",
            )
        await session.commit()
        left_users = int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM users "
                        f"WHERE {_starts_with_run_prefix('account_name')}"
                    ),
                    {"prefix": prefix},
                )
            ).scalar_one()
        )
        left_requests = int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM offer_requests "
                        f"WHERE ({_starts_with_run_prefix('idempotency_key')}) "
                        f"   OR ({_starts_with_run_prefix('request_public_id')})"
                    ),
                    {"prefix": prefix},
                )
            ).scalar_one()
        )
        left_offers = int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM offers "
                        f"WHERE {_starts_with_run_prefix('notes')}"
                    ),
                    {"prefix": prefix},
                )
            ).scalar_one()
        )
        left_jobs = int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM telegram_delivery_jobs WHERE "
                        + " OR ".join(job_predicates)
                    ),
                    job_params,
                )
            ).scalar_one()
        )
        global_unsynced = (
            await session.execute(
                text(
                    "SELECT table_name, count(*) FROM change_log "
                    "WHERE synced IS NOT TRUE GROUP BY 1"
                )
            )
        ).all()

    # Only a dedicated test namespace may be deleted. Shared sync queues are
    # intentionally untouched so genuine staging failures remain visible.
    test_namespace_cleanup: dict[str, object] = {
        "pattern": f"staging-combined:{prefix}:*",
        "deleted_keys": 0,
    }
    try:
        from core.redis import close_redis, init_redis

        redis_client = await init_redis()
        keys = [
            key
            async for key in redis_client.scan_iter(
                match=f"staging-combined:{prefix}:*", count=100
            )
        ]
        if keys:
            test_namespace_cleanup["deleted_keys"] = int(await redis_client.delete(*keys))
        await close_redis()
    except Exception as exc:  # noqa: BLE001
        test_namespace_cleanup["error"] = str(exc)

    failed_deletes = sorted(
        key for key, value in deleted.items() if isinstance(value, int) and value < 0
    )
    return {
        "ok": not failed_deletes
        and left_users == 0
        and left_requests == 0
        and left_offers == 0
        and left_jobs == 0,
        "at_utc": _utc(),
        "server_mode": getattr(settings, "server_mode", None),
        "run_prefix": prefix,
        "deleted": deleted,
        "failed_deletes": failed_deletes,
        "left_synthetic_users": left_users,
        "left_synthetic_offer_requests": left_requests,
        "left_synthetic_offers": left_offers,
        "left_synthetic_delivery_jobs": left_jobs,
        "global_unsynced_observed_only": [
            (str(row[0]), int(row[1])) for row in global_unsynced
        ],
        "test_namespace_cleanup": test_namespace_cleanup,
        "shared_sync_queues_untouched": ["sync:outbound", "sync:retry"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-prefix", required=True)
    args = parser.parse_args(argv)
    try:
        payload = asyncio.run(_run(args.run_prefix))
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
