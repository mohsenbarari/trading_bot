#!/usr/bin/env python3
"""Project canonical group facts into the estimator compatibility database.

The estimator's historical training database remains useful, but its former
private pipeline is retired.  This projection appends only normalized,
quality-approved Market Store facts using opaque deterministic identifiers.
Raw Telegram text, sender identity, reply identifiers, and transport message
identifiers never cross this boundary.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Sequence
from zoneinfo import ZoneInfo


PROJECTION_VERSION = "canonical-group-estimator-projection-v1"
PROJECTION_IMPORT_ID = -9_000_000_000_000_000_001
_TEHRAN = ZoneInfo("Asia/Tehran")
_COMMODITY = {
    "IMAM": "امام",
    "BAHAR": "بهار",
    "QUARTER_BAHAR": "ربع بهار",
    "HALF_BAHAR": "نیم بهار",
    "QUARTER_LOW_DATE": "ربع تاریخ پایین",
    "HALF_LOW_DATE": "نیم تاریخ پایین",
    "ONE_GRAM": "یک گرمی",
}


class ProjectionError(RuntimeError):
    """Raised when the compatibility projection cannot be proven safe."""


def _opaque_id(event_key: bytes, label: bytes) -> int:
    value = int.from_bytes(hashlib.sha256(event_key + b":" + label).digest()[:7], "big") + 1
    return -value


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _require_schema(connection: sqlite3.Connection) -> None:
    required = {
        "imports",
        "messages",
        "offers",
        "confirmed_trades",
        "offer_market_quality",
        "trade_market_quality",
    }
    missing = sorted(required - _tables(connection))
    if missing:
        raise ProjectionError("estimator_conversation_schema_incomplete:" + ",".join(missing))


def _event_time_tehran(value: str) -> str:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(_TEHRAN).isoformat(timespec="seconds")


def _settlement(value: str) -> str:
    normalized = value.strip().upper()
    if normalized in {"CASH", "TODAY"}:
        return "CASH"
    if normalized == "TOMORROW":
        return "TOMORROW"
    raise ProjectionError("canonical_group_settlement_unsupported")


def _trade_form(value: str) -> str:
    normalized = value.strip().upper()
    if normalized == "PHYSICAL":
        return "PHYSICAL"
    if normalized.startswith("PAPER"):
        return "PAPER"
    raise ProjectionError("canonical_group_trade_form_unsupported")


def _quantity(row: sqlite3.Row) -> int | None:
    value = row["quantity_num"]
    if value is None:
        return None
    number = float(value)
    if number <= 0 or not number.is_integer():
        return None
    return int(number)


def _source(row: sqlite3.Row) -> tuple[str, int]:
    code = str(row["source_code"] or "").strip().upper()
    if code not in {"GROUP_1", "GROUP_2"}:
        raise ProjectionError("canonical_group_source_unsupported")
    number = int(code[-1])
    return f"group_{number}", number


def _commodity(row: sqlite3.Row) -> str:
    instrument = str(row["instrument"] or "").strip().upper()
    if not instrument.startswith("COIN_"):
        raise ProjectionError("canonical_group_instrument_unsupported")
    try:
        return _COMMODITY[instrument.removeprefix("COIN_")]
    except KeyError as exc:
        raise ProjectionError("canonical_group_commodity_unsupported") from exc


def _rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    connection.row_factory = sqlite3.Row
    return list(
        connection.execute(
            """
            SELECT event_key,source_code,event_time_utc,available_at_utc,
                   instrument,settlement_term,trade_form,event_type,side,
                   price_num,quantity_num,parse_confidence,quality_state,
                   is_conditional,attributes_json
            FROM market_observations
            WHERE source_code IN ('GROUP_1','GROUP_2')
              AND source_family='GROUP'
              AND event_type IN ('OFFER','TRADE')
              AND price_unit='PROJECT_THOUSAND_TOMAN'
              AND parser_version <> 'staging-market-input-bridge-v5'
            ORDER BY event_time_utc,id
            """
        )
    )


def project(market_store: Path, conversation_db: Path) -> dict[str, int | str]:
    if not market_store.is_file():
        raise ProjectionError("market_store_unavailable")
    if not conversation_db.is_file():
        raise ProjectionError("estimator_conversation_database_unavailable")
    source = sqlite3.connect(f"file:{market_store.resolve()}?mode=ro", uri=True, timeout=30)
    source.row_factory = sqlite3.Row
    destination = sqlite3.connect(conversation_db, timeout=30)
    destination.row_factory = sqlite3.Row
    destination.execute("PRAGMA busy_timeout=30000")
    counts = {"eligible_offers": 0, "eligible_trades": 0, "ineligible_removed": 0}
    try:
        _require_schema(destination)
        rows = _rows(source)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        destination.execute("BEGIN IMMEDIATE")
        destination.execute(
            """
            CREATE TABLE IF NOT EXISTS canonical_group_projection (
                event_key BLOB PRIMARY KEY CHECK(length(event_key) BETWEEN 16 AND 64),
                event_type TEXT NOT NULL CHECK(event_type IN ('OFFER','TRADE')),
                row_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                projected_at_utc TEXT NOT NULL,
                projection_version TEXT NOT NULL
            )
            """
        )
        destination.execute(
            """
            INSERT INTO imports(
                id,archive_path,archive_sha256,imported_at_utc,cutoff_utc,
                message_count,retained_message_count,dropped_message_count,extractor_version
            ) VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET imported_at_utc=excluded.imported_at_utc,
                extractor_version=excluded.extractor_version
            """,
            (
                PROJECTION_IMPORT_ID,
                "canonical-market-store",
                hashlib.sha256(b"canonical-market-store-group-projection-v1").hexdigest(),
                now,
                "1970-01-01T00:00:00Z",
                0,
                0,
                0,
                PROJECTION_VERSION,
            ),
        )
        for row in rows:
            event_key = bytes(row["event_key"])
            event_type = str(row["event_type"]).upper()
            row_id = _opaque_id(event_key, event_type.encode("ascii"))
            message_id = _opaque_id(event_key, b"MESSAGE")
            prior = destination.execute(
                "SELECT event_type,row_id,message_id FROM canonical_group_projection WHERE event_key=?",
                (event_key,),
            ).fetchone()
            if str(row["quality_state"]).upper() != "ELIGIBLE":
                if prior is not None:
                    if str(prior["event_type"]) == "OFFER":
                        destination.execute("DELETE FROM offer_market_quality WHERE offer_id=?", (int(prior["row_id"]),))
                        destination.execute("DELETE FROM offers WHERE id=?", (int(prior["row_id"]),))
                    else:
                        destination.execute("DELETE FROM trade_market_quality WHERE trade_id=?", (int(prior["row_id"]),))
                        destination.execute("DELETE FROM confirmed_trades WHERE id=?", (int(prior["row_id"]),))
                    destination.execute(
                        "DELETE FROM messages WHERE import_id=? AND message_id=?",
                        (PROJECTION_IMPORT_ID, int(prior["message_id"])),
                    )
                    destination.execute("DELETE FROM canonical_group_projection WHERE event_key=?", (event_key,))
                    counts["ineligible_removed"] += 1
                continue
            source_file, _group_number = _source(row)
            commodity = _commodity(row)
            settlement = _settlement(str(row["settlement_term"]))
            trade_form = _trade_form(str(row["trade_form"]))
            price = float(row["price_num"])
            if price <= 0 or not price.is_integer():
                raise ProjectionError("canonical_group_price_invalid")
            quantity = _quantity(row)
            event_time = str(row["event_time_utc"])
            confidence = float(row["parse_confidence"])
            destination.execute(
                """
                INSERT INTO messages(
                    import_id,message_id,event_time_utc,event_time_tehran,
                    sender_hash,text,reply_to_message_id,source_html_file,
                    roles_json,relevance_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(import_id,message_id) DO UPDATE SET
                    event_time_utc=excluded.event_time_utc,
                    event_time_tehran=excluded.event_time_tehran,
                    source_html_file=excluded.source_html_file
                """,
                (
                    PROJECTION_IMPORT_ID,
                    message_id,
                    event_time,
                    _event_time_tehran(event_time),
                    None,
                    "",
                    None,
                    source_file,
                    "[]",
                    json.dumps({"source": "CANONICAL_MARKET_STORE"}, separators=(",", ":")),
                ),
            )
            opaque_context = "canonical:" + event_key.hex()
            if event_type == "OFFER":
                destination.execute(
                    """
                    INSERT INTO offers(
                        id,import_id,message_id,offer_index,commodity,price,
                        quantity,side,settlement,trade_form,confidence,source_text,
                        price_raw,price_method,commodity_method,quantity_method
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                        commodity=excluded.commodity,price=excluded.price,
                        quantity=excluded.quantity,side=excluded.side,
                        settlement=excluded.settlement,trade_form=excluded.trade_form,
                        confidence=excluded.confidence,source_text=excluded.source_text
                    """,
                    (
                        row_id,
                        PROJECTION_IMPORT_ID,
                        message_id,
                        0,
                        commodity,
                        int(price),
                        quantity,
                        str(row["side"]).upper(),
                        settlement,
                        trade_form,
                        confidence,
                        opaque_context,
                        None,
                        "CANONICAL_MARKET_STORE",
                        "CANONICAL_MARKET_STORE",
                        "CANONICAL_MARKET_STORE",
                    ),
                )
                destination.execute(
                    """
                    INSERT INTO offer_market_quality(
                        offer_id,event_time_utc,lifecycle_phase,live_range_weight,
                        live_flow_weight,historical_training_weight,realtime_eligible,
                        training_eligible,cross_state,crossing_reference_price,
                        market_regime,regime_score,regime_confidence,
                        regime_volatility_percent,exclusion_reason
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(offer_id) DO UPDATE SET
                        event_time_utc=excluded.event_time_utc,
                        lifecycle_phase=excluded.lifecycle_phase,
                        live_range_weight=excluded.live_range_weight,
                        live_flow_weight=excluded.live_flow_weight,
                        historical_training_weight=excluded.historical_training_weight,
                        realtime_eligible=1,training_eligible=1,
                        cross_state=excluded.cross_state,exclusion_reason=NULL
                    """,
                    (
                        row_id,
                        event_time,
                        "CANONICAL_ELIGIBLE",
                        1.0,
                        1.0,
                        1.0 / 3.0,
                        1,
                        1,
                        "CANONICAL_ELIGIBLE",
                        None,
                        "UNKNOWN",
                        None,
                        0.0,
                        None,
                        None,
                    ),
                )
                counts["eligible_offers"] += 1
            else:
                destination.execute(
                    """
                    INSERT INTO confirmed_trades(
                        id,import_id,confirmation_message_id,offer_message_id,
                        request_message_id,event_time_utc,commodity,price,price_raw,
                        price_method,quantity,quantity_method,reported_quantity,
                        is_aggregate,training_eligible,side,settlement,trade_form,
                        confidence,confirmation_type,evidence_json,context_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                        event_time_utc=excluded.event_time_utc,
                        commodity=excluded.commodity,price=excluded.price,
                        quantity=excluded.quantity,side=excluded.side,
                        settlement=excluded.settlement,trade_form=excluded.trade_form,
                        confidence=excluded.confidence,context_json=excluded.context_json
                    """,
                    (
                        row_id,
                        PROJECTION_IMPORT_ID,
                        message_id,
                        None,
                        None,
                        event_time,
                        commodity,
                        int(price),
                        None,
                        "CANONICAL_MARKET_STORE",
                        quantity,
                        "CANONICAL_MARKET_STORE",
                        quantity,
                        0,
                        1,
                        str(row["side"]).upper(),
                        settlement,
                        trade_form,
                        confidence,
                        "CANONICAL_REPLY_CHAIN",
                        "{}",
                        json.dumps({"opaque_event": opaque_context}, separators=(",", ":")),
                    ),
                )
                destination.execute(
                    """
                    INSERT INTO trade_market_quality(
                        trade_id,linked_offer_id,training_eligible,realtime_eligible,
                        training_weight,market_regime,regime_score,
                        regime_confidence,cross_state,exclusion_reason
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(trade_id) DO UPDATE SET
                        training_eligible=1,realtime_eligible=1,
                        training_weight=excluded.training_weight,
                        cross_state=excluded.cross_state,exclusion_reason=NULL
                    """,
                    (row_id, None, 1, 1, 1.5, "UNKNOWN", None, 0.0, "CANONICAL_ELIGIBLE", None),
                )
                counts["eligible_trades"] += 1
            destination.execute(
                """
                INSERT INTO canonical_group_projection(
                    event_key,event_type,row_id,message_id,projected_at_utc,projection_version
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(event_key) DO UPDATE SET
                    event_type=excluded.event_type,row_id=excluded.row_id,
                    message_id=excluded.message_id,projected_at_utc=excluded.projected_at_utc,
                    projection_version=excluded.projection_version
                """,
                (event_key, event_type, row_id, message_id, now, PROJECTION_VERSION),
            )
        destination.execute(
            """
            UPDATE imports SET
                imported_at_utc=?,
                message_count=(SELECT COUNT(*) FROM canonical_group_projection),
                retained_message_count=(SELECT COUNT(*) FROM canonical_group_projection)
            WHERE id=?
            """,
            (now, PROJECTION_IMPORT_ID),
        )
        destination.commit()
        return {"status": "PROJECTED", **counts}
    except BaseException:
        destination.rollback()
        raise
    finally:
        source.close()
        destination.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-store", type=Path, required=True)
    parser.add_argument("--conversation-db", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print(json.dumps(project(args.market_store, args.conversation_db), sort_keys=True), flush=True)
        return 0
    except (OSError, ProjectionError, sqlite3.Error, ValueError) as exc:
        print(json.dumps({"status": "FAILED", "reason": str(exc)}, sort_keys=True), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
