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
import statistics
import sys
from typing import Sequence
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.input_health import update_probe_state


PROJECTION_VERSION = "canonical-group-estimator-projection-v5-cross-book-price-guard"
PROJECTION_IMPORT_ID = -9_000_000_000_000_000_001
MAXIMUM_MODEL_PROJECTION_DELAY_SECONDS = 5 * 60
LIVE_BOOK_PRICE_WINDOW_SECONDS = 5 * 60
LIVE_BOOK_PRICE_MINIMUM_OFFERS = 3
LIVE_BOOK_MAXIMUM_RELATIVE_DEVIATION = 0.05
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
    code = instrument.removeprefix("COIN_")
    if code == "UNRESOLVED":
        return "نامشخص"
    try:
        return _COMMODITY[code]
    except KeyError as exc:
        raise ProjectionError("canonical_group_commodity_unsupported") from exc


def _attributes(row: sqlite3.Row) -> dict[str, object]:
    try:
        value = json.loads(str(row["attributes_json"] or "{}"))
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _root_offer_event_key(attributes: dict[str, object]) -> bytes | None:
    value = str(attributes.get("root_offer_event_key") or "").strip()
    if not value:
        return None
    try:
        decoded = bytes.fromhex(value)
    except ValueError:
        return None
    return decoded if 16 <= len(decoded) <= 64 else None


def _rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    connection.row_factory = sqlite3.Row
    return list(
        connection.execute(
            """
            SELECT event_key,source_code,event_time_utc,available_at_utc,
                   instrument,settlement_term,trade_form,event_type,side,
                   price_num,quantity_num,parse_confidence,quality_state,
                   parser_version,is_conditional,attributes_json
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


def _model_exclusion_reason(row: sqlite3.Row) -> str | None:
    if str(row["quality_state"] or "").upper() != "ELIGIBLE":
        return "CANONICAL_QUALITY_NOT_ELIGIBLE"
    if bool(row["is_conditional"]):
        return "CONDITIONAL_GROUP_FACT"
    event_time = datetime.fromisoformat(
        str(row["event_time_utc"]).replace("Z", "+00:00")
    )
    available_at = datetime.fromisoformat(
        str(row["available_at_utc"]).replace("Z", "+00:00")
    )
    delay = (available_at - event_time).total_seconds()
    if delay < 0:
        return "CANONICAL_AVAILABILITY_PRECEDES_EVENT"
    if delay > MAXIMUM_MODEL_PROJECTION_DELAY_SECONDS:
        return "CANONICAL_FACT_ARRIVED_TOO_LATE"
    return None


def _causal_trade_exclusion_reason(
    row: sqlite3.Row,
    *,
    rows_by_key: dict[bytes, sqlite3.Row],
    exclusions: dict[bytes, str | None],
) -> str | None:
    if str(row["event_type"]).upper() != "TRADE":
        return None
    root_key = _root_offer_event_key(_attributes(row))
    if root_key is None:
        return "CAUSAL_TRADE_ROOT_KEY_MISSING"
    root = rows_by_key.get(root_key)
    if root is None or str(root["event_type"]).upper() != "OFFER":
        return "CAUSAL_TRADE_ROOT_OFFER_UNAVAILABLE"
    for column, reason in (
        ("source_code", "CAUSAL_TRADE_ROOT_SOURCE_MISMATCH"),
        ("instrument", "CAUSAL_TRADE_ROOT_INSTRUMENT_MISMATCH"),
        ("settlement_term", "CAUSAL_TRADE_ROOT_SETTLEMENT_MISMATCH"),
        ("trade_form", "CAUSAL_TRADE_ROOT_FORM_MISMATCH"),
        ("side", "CAUSAL_TRADE_ROOT_SIDE_MISMATCH"),
    ):
        if str(row[column]).upper() != str(root[column]).upper():
            return reason
    if exclusions.get(root_key) is not None:
        return "CAUSAL_TRADE_ROOT_NOT_MODEL_ELIGIBLE"
    return None


def _live_book_price_exclusion_reason(
    row: sqlite3.Row,
    *,
    eligible_offers: Sequence[sqlite3.Row],
) -> str | None:
    """Reject a glaring deviation from a causal, multi-offer instrument book.

    Prefer the exact settlement book.  When it is too thin, the other physical
    settlement of the same instrument is still a useful safety reference: its
    normal basis is much smaller than a five-percent price-family error.  The
    fallback never labels the candidate or changes its price; it only keeps an
    implausible fact out of realtime estimation and training.
    """

    event_time = datetime.fromisoformat(
        str(row["event_time_utc"]).replace("Z", "+00:00")
    )
    available_at = datetime.fromisoformat(
        str(row["available_at_utc"]).replace("Z", "+00:00")
    )
    same_book_prices: list[float] = []
    instrument_prices: list[float] = []
    for offer in eligible_offers:
        if bytes(offer["event_key"]) == bytes(row["event_key"]):
            continue
        if any(
            str(offer[column]).upper() != str(row[column]).upper()
            for column in ("instrument", "trade_form")
        ):
            continue
        offer_event_time = datetime.fromisoformat(
            str(offer["event_time_utc"]).replace("Z", "+00:00")
        )
        offer_available_at = datetime.fromisoformat(
            str(offer["available_at_utc"]).replace("Z", "+00:00")
        )
        # The estimator can use every offer already known when this fact
        # becomes available.  Telegram batches may deliver a slightly later
        # economic event in the same envelope, so ordering by source time here
        # would discard causal evidence that is already present at decision
        # time.
        age = (available_at - offer_event_time).total_seconds()
        if (
            age < 0
            or age > LIVE_BOOK_PRICE_WINDOW_SECONDS
            or offer_available_at > available_at
        ):
            continue
        price = float(offer["price_num"])
        if price > 0:
            instrument_prices.append(price)
            if str(offer["settlement_term"]).upper() == str(
                row["settlement_term"]
            ).upper():
                same_book_prices.append(price)
    prices = (
        same_book_prices
        if len(same_book_prices) >= LIVE_BOOK_PRICE_MINIMUM_OFFERS
        else instrument_prices
    )
    if len(prices) < LIVE_BOOK_PRICE_MINIMUM_OFFERS:
        return None
    center = float(statistics.median(prices))
    relative_mad = float(
        statistics.median(abs(price - center) for price in prices)
    ) / max(1.0, center)
    tolerance = max(
        LIVE_BOOK_MAXIMUM_RELATIVE_DEVIATION,
        6.0 * relative_mad,
    )
    deviation = abs(float(row["price_num"]) - center) / max(1.0, center)
    return "LIVE_BOOK_PRICE_OUTLIER" if deviation > tolerance else None


def _model_exclusion_reasons(
    rows: Sequence[sqlite3.Row],
) -> dict[bytes, str | None]:
    """Build causal model gates once so projection and observability agree."""

    rows_by_key = {bytes(row["event_key"]): row for row in rows}
    base_exclusions = {
        bytes(row["event_key"]): _model_exclusion_reason(row) for row in rows
    }
    eligible_offers = [
        row
        for row in rows
        if str(row["event_type"]).upper() == "OFFER"
        and base_exclusions[bytes(row["event_key"])] is None
    ]
    exclusions: dict[bytes, str | None] = {}
    for row in rows:
        event_key = bytes(row["event_key"])
        reason = base_exclusions[event_key]
        if reason is None:
            reason = _causal_trade_exclusion_reason(
                row,
                rows_by_key=rows_by_key,
                exclusions=exclusions,
            )
        if reason is None:
            reason = _live_book_price_exclusion_reason(
                row,
                eligible_offers=eligible_offers,
            )
        exclusions[event_key] = reason
    return exclusions


def _audit_projectable(row: sqlite3.Row) -> bool:
    """Keep detected facts for reporting without weakening model gates."""

    return str(row["quality_state"] or "").upper() in {
        "ELIGIBLE",
        "PENDING_REVIEW",
    }


def _delete_projected(destination: sqlite3.Connection, prior: sqlite3.Row) -> None:
    if str(prior["event_type"]) == "OFFER":
        destination.execute(
            "DELETE FROM offer_market_quality WHERE offer_id=?",
            (int(prior["row_id"]),),
        )
        destination.execute("DELETE FROM offers WHERE id=?", (int(prior["row_id"]),))
    else:
        destination.execute(
            "DELETE FROM trade_market_quality WHERE trade_id=?",
            (int(prior["row_id"]),),
        )
        destination.execute(
            "DELETE FROM confirmed_trades WHERE id=?",
            (int(prior["row_id"]),),
        )
    destination.execute(
        "DELETE FROM messages WHERE import_id=? AND message_id=?",
        (PROJECTION_IMPORT_ID, int(prior["message_id"])),
    )
    destination.execute(
        "DELETE FROM canonical_group_projection WHERE event_key=?",
        (bytes(prior["event_key"]),),
    )


def _group_observability(
    rows: Sequence[sqlite3.Row],
    *,
    exclusion_reasons: dict[bytes, str | None],
) -> dict[str, int | str | None]:
    """Summarize intake separately from model eligibility without private data."""

    result: dict[str, int | str | None] = {}
    for group_number in (1, 2):
        prefix = f"group_{group_number}"
        result[f"{prefix}_latest_canonical_event_utc"] = None
        result[f"{prefix}_latest_eligible_event_utc"] = None
        result[f"{prefix}_pending_review_total"] = 0
        result[f"{prefix}_rejected_total"] = 0
    for row in rows:
        source_code = str(row["source_code"] or "").upper()
        if source_code not in {"GROUP_1", "GROUP_2"}:
            continue
        prefix = source_code.lower()
        event_time = str(row["event_time_utc"] or "") or None
        canonical_key = f"{prefix}_latest_canonical_event_utc"
        if event_time and (
            result[canonical_key] is None
            or event_time > str(result[canonical_key])
        ):
            result[canonical_key] = event_time
        quality = str(row["quality_state"] or "").upper()
        if exclusion_reasons.get(bytes(row["event_key"])) is None:
            eligible_key = f"{prefix}_latest_eligible_event_utc"
            if event_time and (
                result[eligible_key] is None
                or event_time > str(result[eligible_key])
            ):
                result[eligible_key] = event_time
        elif quality == "PENDING_REVIEW":
            key = f"{prefix}_pending_review_total"
            result[key] = int(result[key] or 0) + 1
        elif quality in {"REJECTED", "IGNORED"}:
            key = f"{prefix}_rejected_total"
            result[key] = int(result[key] or 0) + 1
    return result


def project(
    market_store: Path, conversation_db: Path
) -> dict[str, int | str | None]:
    if not market_store.is_file():
        raise ProjectionError("market_store_unavailable")
    if not conversation_db.is_file():
        raise ProjectionError("estimator_conversation_database_unavailable")
    source = sqlite3.connect(f"file:{market_store.resolve()}?mode=ro", uri=True, timeout=30)
    source.row_factory = sqlite3.Row
    destination = sqlite3.connect(conversation_db, timeout=30)
    destination.row_factory = sqlite3.Row
    destination.execute("PRAGMA busy_timeout=30000")
    counts = {
        "eligible_offers": 0,
        "eligible_trades": 0,
        "audit_offers": 0,
        "audit_trades": 0,
        "audit_only_offers": 0,
        "audit_only_trades": 0,
        "live_book_price_outliers": 0,
        "causal_trade_mismatches": 0,
        "ineligible_removed": 0,
    }
    try:
        _require_schema(destination)
        rows = _rows(source)
        exclusion_reasons = _model_exclusion_reasons(rows)
        counts.update(
            _group_observability(rows, exclusion_reasons=exclusion_reasons)
        )
        counts["live_book_price_outliers"] = sum(
            reason == "LIVE_BOOK_PRICE_OUTLIER"
            for reason in exclusion_reasons.values()
        )
        counts["causal_trade_mismatches"] = sum(
            bool(reason and reason.startswith("CAUSAL_TRADE_ROOT_"))
            for reason in exclusion_reasons.values()
        )
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
        source_event_keys = {bytes(row["event_key"]) for row in rows}
        for prior in destination.execute(
            "SELECT event_key,event_type,row_id,message_id FROM canonical_group_projection"
        ).fetchall():
            if bytes(prior["event_key"]) in source_event_keys:
                continue
            _delete_projected(destination, prior)
            counts["ineligible_removed"] += 1
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
            attributes = _attributes(row)
            row_id = _opaque_id(event_key, event_type.encode("ascii"))
            message_id = _opaque_id(event_key, b"MESSAGE")
            prior = destination.execute(
                "SELECT event_key,event_type,row_id,message_id FROM canonical_group_projection WHERE event_key=?",
                (event_key,),
            ).fetchone()
            if not _audit_projectable(row):
                if prior is not None:
                    _delete_projected(destination, prior)
                    counts["ineligible_removed"] += 1
                continue
            exclusion_reason = exclusion_reasons[event_key]
            model_eligible = exclusion_reason is None
            source_file, _group_number = _source(row)
            commodity = _commodity(row)
            settlement = _settlement(str(row["settlement_term"]))
            trade_form = _trade_form(str(row["trade_form"]))
            price = float(row["price_num"])
            if price <= 0 or not price.is_integer():
                raise ProjectionError("canonical_group_price_invalid")
            quantity = _quantity(row)
            source_event_time = str(row["event_time_utc"])
            # Compatibility-store time means "known by".  The economic event
            # time remains in opaque metadata for audit without future leakage.
            event_time = str(row["available_at_utc"])
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
                    source_html_file=excluded.source_html_file,
                    relevance_json=excluded.relevance_json
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
                    json.dumps(
                        {
                            "source": "CANONICAL_MARKET_STORE",
                            "source_event_time_utc": source_event_time,
                            "available_at_utc": event_time,
                            "canonical_settlement_term": str(row["settlement_term"]),
                            "canonical_trade_form": str(row["trade_form"]),
                            "canonical_quality_state": str(row["quality_state"]),
                            "canonical_is_conditional": bool(row["is_conditional"]),
                            "canonical_resolution_reason": attributes.get(
                                "resolution_reason"
                            ),
                            "human_feedback_revision": attributes.get(
                                "human_feedback_revision"
                            ),
                            "parser_version": str(row["parser_version"]),
                        },
                        separators=(",", ":"),
                    ),
                ),
            )
            opaque_context = "canonical:" + event_key.hex()
            if event_type == "OFFER":
                lifecycle = (
                    "CANONICAL_ELIGIBLE"
                    if model_eligible
                    else "CANONICAL_AUDIT_ONLY"
                )
                live_weight = 1.0 if model_eligible else 0.0
                training_weight = (1.0 / 3.0) if model_eligible else 0.0
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
                        realtime_eligible=excluded.realtime_eligible,
                        training_eligible=excluded.training_eligible,
                        cross_state=excluded.cross_state,
                        exclusion_reason=excluded.exclusion_reason
                    """,
                    (
                        row_id,
                        event_time,
                        lifecycle,
                        live_weight,
                        live_weight,
                        training_weight,
                        int(model_eligible),
                        int(model_eligible),
                        lifecycle,
                        None,
                        "UNKNOWN",
                        None,
                        0.0,
                        None,
                        exclusion_reason,
                    ),
                )
                counts["audit_offers"] += 1
                if model_eligible:
                    counts["eligible_offers"] += 1
                else:
                    counts["audit_only_offers"] += 1
            else:
                root_event_key = _root_offer_event_key(attributes)
                linked_offer = (
                    destination.execute(
                        """
                        SELECT row_id,message_id
                        FROM canonical_group_projection
                        WHERE event_key=? AND event_type='OFFER'
                        """,
                        (root_event_key,),
                    ).fetchone()
                    if root_event_key is not None
                    else None
                )
                linked_offer_id = (
                    int(linked_offer["row_id"]) if linked_offer is not None else None
                )
                linked_offer_message_id = (
                    int(linked_offer["message_id"])
                    if linked_offer is not None
                    else None
                )
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
                        confidence=excluded.confidence,
                        offer_message_id=excluded.offer_message_id,
                        is_aggregate=excluded.is_aggregate,
                        training_eligible=excluded.training_eligible,
                        confirmation_type=excluded.confirmation_type,
                        evidence_json=excluded.evidence_json,
                        context_json=excluded.context_json
                    """,
                    (
                        row_id,
                        PROJECTION_IMPORT_ID,
                        message_id,
                        linked_offer_message_id,
                        None,
                        event_time,
                        commodity,
                        int(price),
                        None,
                        "CANONICAL_MARKET_STORE",
                        quantity,
                        "CANONICAL_MARKET_STORE",
                        quantity,
                        int(bool(attributes.get("is_aggregate"))),
                        int(model_eligible),
                        str(row["side"]).upper(),
                        settlement,
                        trade_form,
                        confidence,
                        str(
                            attributes.get("confirmation_kind")
                            or "CANONICAL_REPLY_CHAIN"
                        ),
                        json.dumps(
                            {
                                "root_offer_event_key": (
                                    root_event_key.hex()
                                    if root_event_key is not None
                                    else None
                                )
                            },
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            {
                                "opaque_event": opaque_context,
                                "source_event_time_utc": source_event_time,
                                "available_at_utc": event_time,
                            },
                            separators=(",", ":"),
                        ),
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
                        linked_offer_id=excluded.linked_offer_id,
                        training_eligible=excluded.training_eligible,
                        realtime_eligible=excluded.realtime_eligible,
                        training_weight=excluded.training_weight,
                        cross_state=excluded.cross_state,
                        exclusion_reason=excluded.exclusion_reason
                    """,
                    (
                        row_id,
                        linked_offer_id,
                        int(model_eligible),
                        int(model_eligible),
                        1.5 if model_eligible else 0.0,
                        "UNKNOWN",
                        None,
                        0.0,
                        (
                            "CANONICAL_ELIGIBLE"
                            if model_eligible
                            else "CANONICAL_AUDIT_ONLY"
                        ),
                        exclusion_reason,
                    ),
                )
                counts["audit_trades"] += 1
                if model_eligible:
                    counts["eligible_trades"] += 1
                else:
                    counts["audit_only_trades"] += 1
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
    parser.add_argument(
        "--health-state",
        type=Path,
        help="Heartbeat JSON; defaults beside the estimator conversation database.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    health_state = args.health_state or args.conversation_db.parent / "group-event-health.json"
    try:
        update_probe_state(
            health_state,
            source="COIN_GROUP_PROJECTION",
            status="RUNNING",
            successful=None,
        )
        result = project(args.market_store, args.conversation_db)
        update_probe_state(
            health_state,
            source="COIN_GROUP_PROJECTION",
            status="HEALTHY",
            successful=True,
            details={
                str(key): value
                for key, value in result.items()
                if key != "status"
            },
        )
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0
    except (OSError, ProjectionError, sqlite3.Error, ValueError) as exc:
        try:
            update_probe_state(
                health_state,
                source="COIN_GROUP_PROJECTION",
                status="FAILED",
                successful=False,
                error_code=f"GROUP_PROJECTION_{type(exc).__name__.upper()}",
            )
        except OSError:
            pass
        print(json.dumps({"status": "FAILED", "reason": str(exc)}, sort_keys=True), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
