#!/usr/bin/env python3
"""Run the Stage 11 history importer against an isolated PostgreSQL DSN."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.market_fact_archive import stable_fact_id
from core.market_intelligence.market_history_backfill import (
    build_bundle,
    export_bot_seed,
    import_history_bundle,
)


class Stage11RehearsalError(RuntimeError):
    pass


BASE_TIME = datetime(2026, 8, 20, 5, 0, tzinfo=timezone.utc)


def _digest(*parts: object) -> str:
    material = "\0".join(str(item) for item in parts).encode("utf-8")
    return sha256(material).hexdigest()


def _encrypted(label: str) -> str:
    return base64.b64encode(b"ciphertext:v1:" + bytes.fromhex(_digest(label))).decode()


def _record(
    *,
    source_code: str,
    identity: str,
    position: int,
    payload: dict[str, Any],
    revision: int = 1,
    encrypted_raw_text: bool = False,
    participant: bool = False,
) -> dict[str, Any]:
    event_key = _digest("event", source_code, identity)
    occurred = BASE_TIME + timedelta(seconds=position)
    document: dict[str, Any] = {
        "contract": "market_history_fact/1.0",
        "lineage": {
            "source_record_id_hash": _digest("source-record", source_code, identity),
            "source_revision": revision,
        },
        "event_key": event_key,
        "origin_event_key": event_key,
        "source_code": source_code,
        "occurred_at_utc": occurred.isoformat(),
        "available_at_utc": (occurred + timedelta(milliseconds=25)).isoformat(),
        "parser_version": "stage11-history-v1",
        "quality_state": "ELIGIBLE",
        "quality_reason_codes": [],
        "payload": payload,
    }
    if encrypted_raw_text:
        document["encrypted_raw_text"] = {
            "ciphertext_b64": _encrypted(f"raw:{source_code}:{identity}"),
            "plaintext_hash": _digest("plaintext", source_code, identity),
            "encryption_key_id": "history-rehearsal:v1",
        }
    if participant:
        document["encrypted_participants"] = [
            {
                "actor_role": "OFFERER",
                "telegram_id_ciphertext_b64": _encrypted(
                    f"telegram-id:{source_code}:{identity}"
                ),
                "telegram_id_lookup_hmac": _digest(
                    "lookup", source_code, identity
                ),
                "display_name_ciphertext_b64": _encrypted(
                    f"display-name:{source_code}:{identity}"
                ),
                "encryption_key_id": "history-rehearsal:v1",
            }
        ]
    return document


def _coin_records(group_code: int, count: int) -> list[dict[str, Any]]:
    source = f"GROUP_{group_code}"
    offer_count = count - 30
    records: list[dict[str, Any]] = []
    for index in range(offer_count):
        identity = f"offer-{index}"
        records.append(
            _record(
                source_code=source,
                identity=identity,
                position=(group_code - 1) * 1000 + index,
                payload={
                    "kind": "COIN_OFFER",
                    "group_code": group_code,
                    "instrument": "COIN_IMAM" if index % 3 else "COIN_QUARTER_BAHAR",
                    "side": "SELL" if index % 2 else "BUY",
                    "settlement": "TOMORROW" if index % 4 == 0 else "CASH",
                    "trade_form": "PHYSICAL",
                    "offered_price_value": str(188000 + group_code * 100 + index),
                    "price_unit": "PROJECT_THOUSAND_TOMAN",
                    "quantity_value": str((index % 20) + 1),
                    "quantity_unit": "COIN",
                },
                encrypted_raw_text=index == 0 and group_code == 1,
                participant=index == 0 and group_code == 1,
            )
        )
    for index in range(30):
        offer_identity = f"offer-{index}"
        offer_key = _digest("event", source, offer_identity)
        offer_fact_id = stable_fact_id(
            source_code=source,
            event_key=offer_key,
            fact_kind="COIN_OFFER",
        )
        records.append(
            _record(
                source_code=source,
                identity=f"trade-{index}",
                position=(group_code - 1) * 1000 + offer_count + index,
                payload={
                    "kind": "COIN_TRADE",
                    "offer_fact_id": offer_fact_id,
                    "outcome": "CONFIRMED_PARTIAL" if index % 5 == 0 else "CONFIRMED_FULL",
                    "agreed_price_value": str(188050 + group_code * 100 + index),
                    "price_unit": "PROJECT_THOUSAND_TOMAN",
                    "agreed_quantity_value": str((index % 10) + 1),
                    "quantity_unit": "COIN",
                },
            )
        )
    return records


def _private_gold_records() -> list[dict[str, Any]]:
    source = "PRIVATE_GOLD_CHANNEL"
    records: list[dict[str, Any]] = []
    for index in range(150):
        records.append(
            _record(
                source_code=source,
                identity=f"offer-{index}",
                position=2000 + index,
                payload={
                    "kind": "PRIVATE_GOLD_OFFER",
                    "instrument": "MELTED_GOLD_PRIVATE",
                    "side": "BUY" if index % 2 else "SELL",
                    "settlement": "CASH",
                    "trade_form": "PHYSICAL",
                    "offered_price_value": str(52_000_000 + index * 1000),
                    "price_unit": "TOMAN_PER_MESGHAL_750",
                    "quantity_value": str((index % 25) + 1),
                    "quantity_unit": "MESGHAL",
                    "lifetime_seconds": 120,
                },
                encrypted_raw_text=index == 0,
            )
        )
    for index in range(50):
        offer_key = _digest("event", source, f"offer-{index}")
        offer_fact_id = stable_fact_id(
            source_code=source,
            event_key=offer_key,
            fact_kind="PRIVATE_GOLD_OFFER",
        )
        records.append(
            _record(
                source_code=source,
                identity=f"outcome-{index}",
                position=2150 + index,
                payload={
                    "kind": "PRIVATE_GOLD_OUTCOME",
                    "offer_fact_id": offer_fact_id,
                    "outcome": "FULL",
                    "executed_quantity_value": str((index % 25) + 1),
                    "remaining_quantity_value": None,
                    "quantity_unit": "MESGHAL",
                },
            )
        )
    return records


def _herat_records() -> list[dict[str, Any]]:
    return [
        _record(
            source_code="USD_HERAT",
            identity=f"quote-{index}",
            position=3000 + index,
            payload={
                "kind": "OBSERVATION",
                "instrument": "USD_HERAT",
                "event_type": "QUOTE",
                "side": "MID",
                "settlement": "SPOT",
                "trade_form": "NOT_APPLICABLE",
                "price_value": str(95_000 + index),
                "price_unit": "TOMAN_PER_USD",
                "currency": "TOMAN",
                "quantity_value": None,
                "quantity_unit": None,
            },
        )
        for index in range(150)
    ]


def _xau_records() -> list[dict[str, Any]]:
    records = [
        _record(
            source_code="XAUUSD",
            identity=f"quote-{index}",
            position=4000 + index,
            payload={
                "kind": "EXTERNAL_QUOTE",
                "instrument": "XAUUSD",
                "quote_kind": "MID",
                "price_value": str(3400 + index / 100),
                "price_unit": "USD_PER_TROY_OUNCE",
                "currency": "USD",
            },
        )
        for index in range(125)
    ]
    for index in range(5):
        records.append(
            _record(
                source_code="XAUUSD",
                identity=f"quote-{index}",
                position=4000 + index,
                revision=2,
                payload={
                    "kind": "EXTERNAL_QUOTE",
                    "instrument": "XAUUSD",
                    "quote_kind": "MID",
                    "price_value": str(3400.5 + index / 100),
                    "price_unit": "USD_PER_TROY_OUNCE",
                    "currency": "USD",
                },
            )
        )
    return records


def _usdt_records() -> list[dict[str, Any]]:
    return [
        _record(
            source_code="WALLEX_PUBLIC_API",
            identity=f"quote-{index}",
            position=5000 + index,
            payload={
                "kind": "EXTERNAL_QUOTE",
                "instrument": "USDT_IRT",
                "quote_kind": "LAST",
                "price_value": str(96_000 + index),
                "price_unit": "TOMAN_PER_USDT",
                "currency": "TOMAN",
            },
        )
        for index in range(120)
    ]


def _bundle_records() -> dict[str, list[dict[str, Any]]]:
    sources = {
        "GROUP_1": _coin_records(1, 200),
        "GROUP_2": _coin_records(2, 200),
        "PRIVATE_GOLD_CHANNEL": _private_gold_records(),
        "USD_HERAT": _herat_records(),
        "XAUUSD": _xau_records(),
        "WALLEX_PUBLIC_API": _usdt_records(),
    }
    # One intentionally incompatible row per source proves row quarantine does
    # not expose or stop the rest of that source batch.
    for source_code, records in sources.items():
        malformed = dict(records[0])
        malformed["lineage"] = dict(malformed["lineage"])
        malformed["lineage"]["source_record_id_hash"] = _digest(
            "malformed", source_code
        )
        malformed["event_key"] = _digest("malformed-event", source_code)
        malformed["origin_event_key"] = malformed["event_key"]
        malformed["message_link"] = "https://forbidden.invalid/message"
        records.append(malformed)
    return sources


def run(dsn: str, output_root: Path) -> dict[str, Any]:
    import psycopg2

    output_root.mkdir(parents=True, exist_ok=True)
    os.chmod(output_root, 0o700)
    connection = psycopg2.connect(dsn, application_name="market-stage11-rehearsal")
    reports: list[dict[str, Any]] = []
    no_op_reports: list[dict[str, Any]] = []
    try:
        sources = _bundle_records()
        for source_code, records in sources.items():
            bundle = build_bundle(
                source_code=source_code,
                source_system="LEGACY_MARKET_STORE",
                records=records,
            ).model_dump(mode="json")
            reports.append(import_history_bundle(connection, bundle))
            no_op_reports.append(import_history_bundle(connection, bundle))
        seed = export_bot_seed(connection, output_root / "bot-seed.jsonl")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*),COUNT(DISTINCT fact_id),
                       (SELECT COUNT(*) FROM market_data.market_fact_revisions),
                       (SELECT COUNT(*) FROM market_data.market_fact_outbox),
                       (SELECT COUNT(*) FROM market_data.history_import_items),
                       (SELECT COUNT(*) FROM market_data.history_import_quarantine),
                       (SELECT COUNT(*) FROM market_data.curated_raw_texts),
                       (SELECT COUNT(*) FROM market_data.market_actor_identities)
                FROM market_data.market_facts
                """
            )
            counts = tuple(int(item) for item in cursor.fetchone())
            cursor.execute(
                """
                SELECT COUNT(*) FROM market_data.history_import_batches
                WHERE status='RECONCILED'
                  AND source_reconciliation_hash=archive_reconciliation_hash
                """
            )
            reconciled_batches = int(cursor.fetchone()[0])
            cursor.execute(
                """
                SELECT COUNT(*) FROM market_data.history_import_quarantine
                WHERE reason_code='FORBIDDEN_FIELD'
                  AND logical_identity_hash IS NULL
                """
            )
            safe_quarantine_count = int(cursor.fetchone()[0])
        (
            fact_count,
            distinct_fact_count,
            revision_count,
            outbox_count,
            import_item_count,
            quarantine_count,
            raw_count,
            participant_count,
        ) = counts
        if fact_count != 995 or distinct_fact_count != fact_count:
            raise Stage11RehearsalError("logical_fact_count_mismatch")
        if revision_count != 1000 or outbox_count != 1000 or import_item_count != 1000:
            raise Stage11RehearsalError("revision_count_mismatch")
        if quarantine_count != 6 or safe_quarantine_count != 6:
            raise Stage11RehearsalError("quarantine_count_mismatch")
        if raw_count != 2 or participant_count != 1:
            raise Stage11RehearsalError("sensitive_archive_projection_mismatch")
        if reconciled_batches != 6 or not all(item["no_op"] for item in no_op_reports):
            raise Stage11RehearsalError("idempotency_or_reconciliation_failed")
        if seed["fact_count"] != fact_count:
            raise Stage11RehearsalError("bot_seed_count_mismatch")
        seed_text = (output_root / "bot-seed.jsonl").read_text(encoding="utf-8")
        for forbidden in (
            "ciphertext_b64",
            "telegram_id",
            "display_name",
            "message_link",
            "https://",
        ):
            if forbidden in seed_text:
                raise Stage11RehearsalError("bot_seed_private_material_detected")
        return {
            "status": "pass",
            "source_count": len(sources),
            "source_record_count": sum(len(items) for items in sources.values()),
            "fact_count": fact_count,
            "duplicate_logical_fact_count": fact_count - distinct_fact_count,
            "revision_count": revision_count,
            "quarantine_count": quarantine_count,
            "reconciled_batch_count": reconciled_batches,
            "second_import_no_op": True,
            "bot_seed": seed,
            "raw_history_transferred_to_bot": False,
            "source_reports": reports,
        }
    finally:
        connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = run(args.dsn, args.output_root)
    except Exception as exc:
        print(json.dumps({"status": "fail", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
