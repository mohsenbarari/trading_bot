#!/usr/bin/env python3
"""Replay the coin-group parser against production-shaped SQLite, read-only.

The command never copies or prints raw message text, sender digests, message
identifiers, or event keys.  It opens both production inputs query-only, clones
only privacy-minimized Market Store rows into memory, runs the current parser
and reply linker there, and emits aggregate parity counters plus reason codes.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Iterable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.coin_group_pipeline import (  # noqa: E402
    COIN_GROUP_PIPELINE_VERSION,
    process_coin_group_staging,
)
from core.market_intelligence.coin_prediction_anchors import (  # noqa: E402
    load_coin_prediction_anchors,
)
from core.market_intelligence.coin_group_feedback import (  # noqa: E402
    AMBIGUOUS_FIELDS,
    CoinGroupParserFeedback,
)
from core.market_intelligence.coin_group_staging import (  # noqa: E402
    COIN_GROUP_STAGING_SCHEMA_VERSION,
)
from core.market_intelligence.coin_group_trades import (  # noqa: E402
    COIN_GROUP_TRADE_LINKER_VERSION,
)
from core.market_intelligence.coin_groups import (  # noqa: E402
    COIN_GROUP_PARSER_VERSION,
    CoinGroupMessageInput,
    parse_coin_group_offers,
)
from core.market_intelligence.market_store import (  # noqa: E402
    initialize_market_store,
    verify_market_store_read_only,
)


AUDIT_VERSION = "coin-group-production-shape-audit-v1"
GROUP_SOURCES = ("GROUP_1", "GROUP_2")
_COPY_COLUMNS = (
    "event_key",
    "source_code",
    "source_family",
    "event_time_utc",
    "available_at_utc",
    "tehran_datetime",
    "tehran_date",
    "tehran_minute",
    "tehran_weekday",
    "instrument",
    "market_label",
    "settlement_term",
    "trade_form",
    "event_type",
    "side",
    "price_value",
    "price_num",
    "price_unit",
    "currency",
    "quantity_value",
    "quantity_num",
    "quantity_unit",
    "parse_confidence",
    "parser_version",
    "quality_state",
    "quality_policy_version",
    "is_conditional",
    "attributes_json",
    "inserted_at_utc",
)
_SEMANTIC_FIELDS = (
    "event_type",
    "instrument",
    "settlement_term",
    "trade_form",
    "side",
    "price_value",
    "quantity_value",
    "quality_state",
    "is_conditional",
    "parser_version",
)
_FIELD_REASON = {
    "event_type": "EVENT_TYPE_CHANGED",
    "instrument": "INSTRUMENT_CHANGED",
    "settlement_term": "SETTLEMENT_CHANGED",
    "trade_form": "TRADE_FORM_CHANGED",
    "side": "SIDE_CHANGED",
    "price_value": "PRICE_CHANGED",
    "quantity_value": "QUANTITY_CHANGED",
    "quality_state": "QUALITY_STATE_CHANGED",
    "is_conditional": "CONDITIONAL_STATE_CHANGED",
    "parser_version": "PARSER_VERSION_CHANGED",
}


class ProductionShapeAuditError(RuntimeError):
    """A safe, non-payload reason for refusing or failing the audit."""


def _external_database(value: str, *, field: str) -> Path:
    supplied = Path(value).expanduser()
    if supplied.is_symlink():
        raise ProductionShapeAuditError(f"{field}_unavailable")
    path = supplied.resolve()
    if not path.is_file():
        raise ProductionShapeAuditError(f"{field}_unavailable")
    try:
        path.relative_to(REPO_ROOT.resolve())
    except ValueError:
        return path
    raise ProductionShapeAuditError(f"{field}_inside_repository")


def _open_read_only(path: Path, *, immutable: bool) -> sqlite3.Connection:
    suffix = "?mode=ro&immutable=1" if immutable else "?mode=ro"
    connection = sqlite3.connect(path.resolve().as_uri() + suffix, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA query_only=ON")
    return connection


def _verify_staging(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT schema_version FROM coin_group_staging_metadata WHERE singleton=1"
    ).fetchone()
    if row is None or int(row["schema_version"]) != COIN_GROUP_STAGING_SCHEMA_VERSION:
        raise ProductionShapeAuditError("coin_group_staging_schema_mismatch")
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(coin_group_staged_messages)")
    }
    required = {
        "group_number",
        "message_id",
        "event_time_utc",
        "available_at_utc",
        "message_text",
        "reply_to_message_id",
        "sender_digest",
        "revision",
        "expires_at_utc",
    }
    if not required.issubset(columns):
        raise ProductionShapeAuditError("coin_group_staging_columns_mismatch")


def _as_utc(value: str) -> datetime:
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ProductionShapeAuditError("production_timestamp_timezone_missing")
    return moment.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _snapshot_bounds(staging: sqlite3.Connection) -> tuple[str, str]:
    row = staging.execute(
        """
        SELECT MIN(event_time_utc) AS minimum_event_time_utc,
               MAX(last_staged_at_utc) AS maximum_staged_at_utc
        FROM coin_group_staged_messages
        """
    ).fetchone()
    if row is None or row["minimum_event_time_utc"] is None or row["maximum_staged_at_utc"] is None:
        raise ProductionShapeAuditError("coin_group_staging_empty")
    minimum = _iso(_as_utc(str(row["minimum_event_time_utc"])))
    maximum = _iso(_as_utc(str(row["maximum_staged_at_utc"])))
    if maximum < minimum:
        raise ProductionShapeAuditError("coin_group_staging_time_order_invalid")
    return minimum, maximum


def _source_inventory(staging: sqlite3.Connection) -> list[dict[str, object]]:
    return [
        {
            "group_number": int(row["group_number"]),
            "messages": int(row["message_count"]),
            "replies": int(row["reply_count"]),
            "edited_messages": int(row["edited_count"]),
            "missing_sender_identity": int(row["missing_sender_count"]),
        }
        for row in staging.execute(
            """
            SELECT group_number, COUNT(*) AS message_count,
                   SUM(reply_to_message_id IS NOT NULL) AS reply_count,
                   SUM(edited_at_utc IS NOT NULL) AS edited_count,
                   SUM(sender_digest IS NULL) AS missing_sender_count
            FROM coin_group_staged_messages
            GROUP BY group_number ORDER BY group_number
            """
        )
    ]


def _first_pass_inventory(staging: sqlite3.Connection) -> dict[str, object]:
    messages = candidates = parsed_messages = explicit = review = failures = 0
    by_group: Counter[int] = Counter()
    settlement: Counter[str] = Counter()
    instrument: Counter[str] = Counter()
    for row in staging.execute(
        """
        SELECT group_number,message_id,event_time_utc,available_at_utc,message_text
        FROM coin_group_staged_messages
        ORDER BY event_time_utc,group_number,message_id
        """
    ):
        messages += 1
        try:
            parsed = parse_coin_group_offers(
                CoinGroupMessageInput(
                    group_number=int(row["group_number"]),
                    source_event_id=int(row["message_id"]),
                    published_at_utc=str(row["event_time_utc"]),
                    available_at_utc=str(row["available_at_utc"]),
                    text=str(row["message_text"]),
                )
            )
        except (TypeError, ValueError):
            failures += 1
            continue
        if parsed:
            parsed_messages += 1
        for item in parsed:
            candidates += 1
            by_group[int(row["group_number"])] += 1
            settlement[item.settlement_term] += 1
            instrument[item.commodity_code or "UNRESOLVED"] += 1
            explicit += int(item.commodity_code is not None)
            review += int(item.quality_state != "ELIGIBLE")
    return {
        "messages_seen": messages,
        "messages_with_offer_candidate": parsed_messages,
        "messages_without_offer_candidate": messages - parsed_messages,
        "offer_candidates": candidates,
        "explicit_instrument_candidates": explicit,
        "review_candidates": review,
        "parser_failures": failures,
        "candidates_by_group": {str(key): value for key, value in sorted(by_group.items())},
        "settlement_counts": dict(sorted(settlement.items())),
        "instrument_counts": dict(sorted(instrument.items())),
    }


def _load_feedback(path: Path) -> dict[bytes, CoinGroupParserFeedback]:
    connection = _open_read_only(path, immutable=True)
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='coin_group_parser_feedback'"
        ).fetchone()
        if table is None:
            raise ProductionShapeAuditError("parser_feedback_table_unavailable")
        values: dict[bytes, CoinGroupParserFeedback] = {}
        for row in connection.execute(
            "SELECT * FROM coin_group_parser_feedback ORDER BY reviewed_at_utc,event_key"
        ):
            try:
                fields = frozenset(json.loads(str(row["ambiguous_fields_json"])))
            except (TypeError, ValueError) as exc:
                raise ProductionShapeAuditError("parser_feedback_fields_invalid") from exc
            key = bytes(row["event_key"])
            if (
                not 16 <= len(key) <= 64
                or not fields
                or not fields.issubset(AMBIGUOUS_FIELDS)
            ):
                raise ProductionShapeAuditError("parser_feedback_row_invalid")
            values[key] = CoinGroupParserFeedback(
                event_key=key,
                event_type=str(row["event_type"]),
                group_number=int(row["group_number"]),
                source_event_time_utc=str(row["source_event_time_utc"]),
                ambiguous_fields=fields,
                event_confirmed=bool(row["event_confirmed"]),
                commodity_code=str(row["commodity_code"]),
                side=str(row["side"]),
                price_project_thousand_toman=int(
                    row["price_project_thousand_toman"]
                ),
                quantity=int(row["quantity"]),
                settlement_term=str(row["settlement_term"]),
                trade_form=str(row["trade_form"]),
                is_conditional=bool(row["is_conditional"]),
                review_revision=int(row["review_revision"]),
                reviewed_at_utc=str(row["reviewed_at_utc"]),
                applied_revision=int(row["applied_revision"]),
                applied_at_utc=(
                    str(row["applied_at_utc"])
                    if row["applied_at_utc"] is not None
                    else None
                ),
                application_count=int(row["application_count"]),
            )
        if connection.total_changes:
            raise ProductionShapeAuditError("feedback_connection_reported_changes")
        return values
    finally:
        connection.close()


def _copy_seed_rows(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    *,
    minimum_event_time_utc: str,
) -> int:
    lower = _iso(_as_utc(minimum_event_time_utc) - timedelta(hours=2))
    selected = source.execute(
        f"""
        SELECT {','.join(_COPY_COLUMNS)}
        FROM market_observations
        WHERE source_code IN ('GROUP_1','GROUP_2')
           OR (
                event_time_utc >= ?
                AND quality_state='ELIGIBLE'
                AND is_conditional=0
                AND event_type IN ('OFFER','TRADE')
                AND price_unit='PROJECT_THOUSAND_TOMAN'
                AND instrument LIKE 'COIN_%'
           )
        """,
        (lower,),
    )
    placeholders = ",".join("?" for _ in _COPY_COLUMNS)
    inserted = 0
    batch: list[tuple[object, ...]] = []
    for row in selected:
        batch.append(tuple(row[column] for column in _COPY_COLUMNS))
        if len(batch) >= 2_000:
            target.executemany(
                f"INSERT OR IGNORE INTO market_observations({','.join(_COPY_COLUMNS)}) VALUES({placeholders})",
                batch,
            )
            inserted += len(batch)
            batch.clear()
    if batch:
        target.executemany(
            f"INSERT OR IGNORE INTO market_observations({','.join(_COPY_COLUMNS)}) VALUES({placeholders})",
            batch,
        )
        inserted += len(batch)
    target.commit()
    return inserted


def _facts(
    connection: sqlite3.Connection,
    *,
    minimum_event_time_utc: str,
) -> dict[bytes, dict[str, object]]:
    return {
        bytes(row["event_key"]): {
            field: row[field] for field in _SEMANTIC_FIELDS
        }
        for row in connection.execute(
            f"""
            SELECT event_key,{','.join(_SEMANTIC_FIELDS)}
            FROM market_observations
            WHERE source_code IN ('GROUP_1','GROUP_2') AND event_time_utc>=?
            """,
            (minimum_event_time_utc,),
        )
    }


def _fact_inventory(values: Mapping[bytes, Mapping[str, object]]) -> dict[str, object]:
    counts: Counter[str] = Counter()
    for item in values.values():
        counts[f"{item['event_type']}|{item['quality_state']}"] += 1
    return {"facts": len(values), "by_type_and_quality": dict(sorted(counts.items()))}


def _parity(
    baseline: Mapping[bytes, Mapping[str, object]],
    candidate: Mapping[bytes, Mapping[str, object]],
    *,
    fields: Iterable[str] = _SEMANTIC_FIELDS,
) -> dict[str, object]:
    compared_fields = tuple(fields)
    reasons: Counter[str] = Counter()
    changed_events = 0
    for key in set(baseline) | set(candidate):
        before = baseline.get(key)
        after = candidate.get(key)
        if before is None:
            changed_events += 1
            reasons["CANDIDATE_EVENT_ADDED"] += 1
            continue
        if after is None:
            changed_events += 1
            reasons["BASELINE_EVENT_MISSING"] += 1
            continue
        event_changed = False
        for field in compared_fields:
            if before[field] != after[field]:
                event_changed = True
                reasons[_FIELD_REASON[field]] += 1
        changed_events += int(event_changed)
    return {
        "events_compared": len(set(baseline) | set(candidate)),
        "events_equal": len(set(baseline) | set(candidate)) - changed_events,
        "events_changed": changed_events,
        "reason_code_counts": dict(sorted(reasons.items())),
        "all_differences_reason_coded": changed_events == 0 or bool(reasons),
        "compared_fields": compared_fields,
    }


def _run(args: argparse.Namespace) -> int:
    staging_path = _external_database(args.staging_database, field="staging_database")
    market_path = _external_database(args.market_database, field="market_database")
    feedback_path = _external_database(args.feedback_database, field="feedback_database")
    prediction_path = _external_database(
        args.prediction_database, field="prediction_database"
    )
    staging_signature = (staging_path.stat().st_ino, staging_path.stat().st_size, staging_path.stat().st_mtime_ns)
    market_signature = (market_path.stat().st_ino, market_path.stat().st_size, market_path.stat().st_mtime_ns)
    feedback_signature = (feedback_path.stat().st_ino, feedback_path.stat().st_size, feedback_path.stat().st_mtime_ns)
    prediction_signature = (prediction_path.stat().st_ino, prediction_path.stat().st_size, prediction_path.stat().st_mtime_ns)
    staging = _open_read_only(staging_path, immutable=True)
    market = _open_read_only(market_path, immutable=False)
    # Hold one WAL snapshot while the live writer is free to continue.  A
    # changed file signature therefore means concurrent production activity,
    # not a write by this command; ``mode=ro``, ``query_only`` and
    # ``total_changes`` are the mutation proof.
    market.execute("BEGIN")
    candidate = sqlite3.connect(":memory:")
    candidate.row_factory = sqlite3.Row
    try:
        _verify_staging(staging)
        verify_market_store_read_only(market)
        minimum, as_of = _snapshot_bounds(staging)
        source_inventory = _source_inventory(staging)
        first_pass = _first_pass_inventory(staging)
        feedback = _load_feedback(feedback_path)
        prediction_load = load_coin_prediction_anchors(
            prediction_path,
            earliest_event_time_utc=minimum,
            as_of_utc=as_of,
        )
        initialize_market_store(candidate)
        seed_rows = _copy_seed_rows(
            market,
            candidate,
            minimum_event_time_utc=minimum,
        )
        baseline = _facts(candidate, minimum_event_time_utc=minimum)
        report = process_coin_group_staging(
            staging,
            candidate,
            as_of_utc=as_of,
            additional_anchors=prediction_load.anchors,
            parser_feedback=feedback,
            reconciliation_horizon_utc=minimum,
        )
        candidate.commit()
        replayed = _facts(candidate, minimum_event_time_utc=minimum)
        economic_parity = _parity(
            baseline,
            replayed,
            fields=tuple(
                field for field in _SEMANTIC_FIELDS if field != "parser_version"
            ),
        )
        provenance_parity = _parity(baseline, replayed)
        candidate_integrity = candidate.execute("PRAGMA integrity_check").fetchone()[0]
        if candidate_integrity != "ok":
            raise ProductionShapeAuditError("in_memory_candidate_integrity_failed")
        if staging.total_changes or market.total_changes:
            raise ProductionShapeAuditError("production_connection_reported_changes")
    finally:
        candidate.close()
        market.close()
        staging.close()
    signatures_unchanged = (
        staging_signature
        == (staging_path.stat().st_ino, staging_path.stat().st_size, staging_path.stat().st_mtime_ns)
        and market_signature
        == (market_path.stat().st_ino, market_path.stat().st_size, market_path.stat().st_mtime_ns)
        and feedback_signature
        == (feedback_path.stat().st_ino, feedback_path.stat().st_size, feedback_path.stat().st_mtime_ns)
        and prediction_signature
        == (prediction_path.stat().st_ino, prediction_path.stat().st_size, prediction_path.stat().st_mtime_ns)
    )
    payload = {
        "schema": "coin_group_production_shape_audit/1.0",
        "audit_version": AUDIT_VERSION,
        "status": "PASS",
        "read_only_verified": True,
        "source_file_signatures_stable": signatures_unchanged,
        "parser_version": COIN_GROUP_PARSER_VERSION,
        "trade_linker_version": COIN_GROUP_TRADE_LINKER_VERSION,
        "pipeline_version": COIN_GROUP_PIPELINE_VERSION,
        "source_inventory": source_inventory,
        "first_pass": first_pass,
        "baseline": _fact_inventory(baseline),
        "candidate": _fact_inventory(replayed),
        "pipeline": {
            "seed_rows": seed_rows,
            "feedback_rows": len(feedback),
            "prediction_anchor_rows_seen": prediction_load.rows_seen,
            "prediction_anchor_rows_rejected": prediction_load.rows_rejected,
            "prediction_anchors": len(prediction_load.anchors),
            "staged_messages_seen": report.staged_messages_seen,
            "eligible_offers": report.eligible_offers,
            "pending_or_rejected_offers": report.pending_or_rejected_offers,
            "eligible_trades": report.eligible_trades,
            "pending_or_rejected_trades": report.pending_or_rejected_trades,
            "root_messages_not_trade_linkable": report.root_messages_not_trade_linkable,
            "retracted_facts": report.retracted_facts,
            "feedback_reviews_seen": report.feedback_reviews_seen,
            "feedback_reviews_applied": report.feedback_reviews_applied,
        },
        "economic_parity": economic_parity,
        "provenance_parity": provenance_parity,
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-database", required=True)
    parser.add_argument("--market-database", required=True)
    parser.add_argument("--feedback-database", required=True)
    parser.add_argument("--prediction-database", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _run(build_parser().parse_args(argv))
    except (ProductionShapeAuditError, OSError, sqlite3.Error, ValueError):
        print(
            json.dumps(
                {
                    "schema": "coin_group_production_shape_audit/1.0",
                    "status": "FAIL",
                    "reason_code": "PRODUCTION_SHAPE_AUDIT_FAILED",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
