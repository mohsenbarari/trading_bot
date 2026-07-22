#!/usr/bin/env python3
"""Export identity-free offer shapes through a forced read-only transaction.

The output is a Stage 4 fixture, not a production snapshot: it contains active
commodity labels plus quantity/price/lot shape examples, and deliberately
selects no offer, user, Telegram, message, notes, or idempotency identifiers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


class Stage4SamplerSafetyError(ValueError):
    """Raised before export when read-only/identity guards are not satisfied."""


_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")


def database_fingerprint(database_url: str) -> str:
    parsed = urlparse(str(database_url or ""))
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.path.strip("/"):
        raise Stage4SamplerSafetyError("stage4_sampler_database_url_invalid")
    identity = f"{parsed.hostname.lower()}:{parsed.port or 5432}/{parsed.path.strip('/').lower()}"
    return "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _safe_lots(value: Any, *, quantity: int) -> list[int] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) not in {2, 3}:
        return None
    if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in value):
        return None
    if sum(value) != quantity:
        return None
    return list(value)


def build_sanitized_fixture(rows: Sequence[Sequence[Any]]) -> dict[str, Any]:
    commodities: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if len(row) != 7:
            raise Stage4SamplerSafetyError("stage4_sampler_row_shape_invalid")
        commodity, offer_type, settlement, quantity, price, wholesale, lots = row
        key = str(commodity or "").strip()
        if not key or len(key) > 80 or any(ord(char) < 32 for char in key):
            raise Stage4SamplerSafetyError("stage4_sampler_commodity_invalid")
        offer_type = str(offer_type or "").lower()
        settlement = str(settlement or "").lower()
        quantity = int(quantity)
        price = int(price)
        if offer_type not in {"buy", "sell"} or settlement not in {"cash", "tomorrow"}:
            raise Stage4SamplerSafetyError("stage4_sampler_enum_invalid")
        if quantity <= 0 or price <= 0:
            raise Stage4SamplerSafetyError("stage4_sampler_numeric_invalid")
        lot_sizes = _safe_lots(lots, quantity=quantity)
        lot_shape = "none" if bool(wholesale) or lot_sizes is None else str(len(lot_sizes))
        commodities.setdefault(key, []).append(
            {
                "offer_type": offer_type,
                "settlement_type": settlement,
                "quantity": quantity,
                "price": price,
                "lot_shape": lot_shape,
                "lot_sizes": lot_sizes,
            }
        )
    if not commodities:
        raise Stage4SamplerSafetyError("stage4_sampler_no_offer_shapes")
    return {
        "schema_version": 1,
        "source": "production_read_only_sanitized_offer_shapes",
        "commodities": [
            {"key": key, "active": True, "templates": templates}
            for key, templates in sorted(commodities.items())
        ],
        "privacy_contract": {
            "contains_offer_ids": False,
            "contains_user_ids": False,
            "contains_telegram_ids": False,
            "contains_message_ids": False,
            "contains_names_or_notes": False,
        },
    }


def sample_read_only(connection: Any, *, seed: str, limit: int) -> dict[str, Any]:
    if not 1 <= int(limit) <= 5000:
        raise Stage4SamplerSafetyError("stage4_sampler_limit_invalid")
    cursor = connection.cursor()
    try:
        cursor.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cursor.execute("SET LOCAL statement_timeout = '15s'")
        cursor.execute("SHOW transaction_read_only")
        state = cursor.fetchone()
        if not state or str(state[0]).lower() != "on":
            raise Stage4SamplerSafetyError("stage4_sampler_transaction_not_read_only")
        cursor.execute(
            """
            SELECT c.name,
                   lower(o.offer_type::text),
                   lower(o.settlement_type::text),
                   o.quantity,
                   o.price,
                   o.is_wholesale,
                   o.lot_sizes
              FROM offers AS o
              JOIN commodities AS c ON c.id = o.commodity_id
             WHERE o.quantity > 0
               AND o.price > 0
               AND o.created_at >= clock_timestamp() - interval '365 days'
             ORDER BY md5(o.id::text || %s)
             LIMIT %s
            """,
            (str(seed), int(limit)),
        )
        rows = cursor.fetchall()
        fixture = build_sanitized_fixture(rows)
        connection.rollback()
        return fixture
    except BaseException:
        connection.rollback()
        raise
    finally:
        cursor.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", required=True)
    parser.add_argument("--ack-production-read-only", action="store_true")
    parser.add_argument("--expected-database-fingerprint", required=True)
    parser.add_argument("--database-url-env", default="STAGE4_PRODUCTION_DATABASE_URL")
    parser.add_argument("--seed", required=True)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.environment != "production" or not args.ack_production_read_only:
            raise Stage4SamplerSafetyError("stage4_sampler_production_read_only_ack_required")
        expected = str(args.expected_database_fingerprint)
        if _FINGERPRINT.fullmatch(expected) is None:
            raise Stage4SamplerSafetyError("stage4_sampler_expected_fingerprint_invalid")
        database_url = os.environ.get(args.database_url_env, "")
        if database_fingerprint(database_url) != expected:
            raise Stage4SamplerSafetyError("stage4_sampler_database_fingerprint_mismatch")
        if args.output.exists() or args.output.is_symlink():
            raise Stage4SamplerSafetyError("stage4_sampler_output_must_not_exist")
        import psycopg2

        connection = psycopg2.connect(
            database_url,
            connect_timeout=5,
            application_name="telegram-stage4-read-only-sampler",
        )
        try:
            connection.autocommit = False
            fixture = sample_read_only(connection, seed=args.seed, limit=args.limit)
        finally:
            connection.close()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(fixture, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"status": "ok", "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
