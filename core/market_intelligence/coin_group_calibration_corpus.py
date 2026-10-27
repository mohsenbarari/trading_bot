"""Append-only, privacy-minimized calibration corpus for coin-group reviews."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import sqlite3
from typing import Iterable

from .coin_group_feedback import (
    AMBIGUOUS_FIELDS,
    COIN_GROUP_FEEDBACK_VERSION,
    CoinGroupParserFeedback,
)
from .market_contracts import normalize_utc


CALIBRATION_CORPUS_VERSION = "coin-group-calibration-corpus-v1"
CALIBRATION_CORPUS_SCHEMA_VERSION = 1


class CoinGroupCalibrationCorpusError(RuntimeError):
    """The correction history cannot be appended without losing integrity."""


@dataclass(frozen=True, slots=True)
class CalibrationCorpusReport:
    reviews_seen: int
    revisions_appended: int
    idempotent_replays: int


_SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS coin_group_calibration_corpus_state(
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    schema_version INTEGER NOT NULL,
    initialized_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coin_group_calibration_corpus(
    event_key BLOB NOT NULL CHECK(length(event_key) BETWEEN 16 AND 64),
    review_revision INTEGER NOT NULL CHECK(review_revision>0),
    reviewed_at_utc TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('OFFER','TRADE')),
    group_number INTEGER NOT NULL CHECK(group_number IN (1,2)),
    ambiguous_fields_json TEXT NOT NULL,
    event_confirmed INTEGER NOT NULL CHECK(event_confirmed IN (0,1)),
    commodity_code TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
    price_project_thousand_toman INTEGER NOT NULL CHECK(price_project_thousand_toman>0),
    quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 100),
    settlement_term TEXT NOT NULL,
    trade_form TEXT NOT NULL,
    is_conditional INTEGER NOT NULL CHECK(is_conditional IN (0,1)),
    parser_version_before TEXT NOT NULL,
    feedback_version TEXT NOT NULL,
    revision_digest BLOB NOT NULL CHECK(length(revision_digest)=32),
    appended_at_utc TEXT NOT NULL,
    PRIMARY KEY(event_key,review_revision)
);

CREATE INDEX IF NOT EXISTS idx_coin_group_calibration_corpus_time
ON coin_group_calibration_corpus(reviewed_at_utc,event_type,group_number);
"""


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def initialize_coin_group_calibration_corpus(
    connection: sqlite3.Connection,
) -> None:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='coin_group_calibration_corpus_state'"
    ).fetchone()
    if row is None:
        connection.executescript(_SCHEMA)
        connection.execute(
            "INSERT INTO coin_group_calibration_corpus_state VALUES(1,?,?)",
            (CALIBRATION_CORPUS_SCHEMA_VERSION, _now()),
        )
        connection.commit()
        return
    state = connection.execute(
        "SELECT schema_version FROM coin_group_calibration_corpus_state WHERE singleton=1"
    ).fetchone()
    if state is None or int(state["schema_version"]) != CALIBRATION_CORPUS_SCHEMA_VERSION:
        raise CoinGroupCalibrationCorpusError(
            "coin_group_calibration_corpus_schema_upgrade_required"
        )


def _revision_payload(
    feedback: CoinGroupParserFeedback,
    *,
    parser_version_before: str,
) -> dict[str, object]:
    fields = tuple(sorted(feedback.ambiguous_fields))
    if not fields or not set(fields).issubset(AMBIGUOUS_FIELDS):
        raise CoinGroupCalibrationCorpusError(
            "coin_group_calibration_corpus_fields_invalid"
        )
    reviewed = normalize_utc(
        feedback.reviewed_at_utc,
        field_name="coin_group_calibration_reviewed_at_utc",
    )
    source_time = normalize_utc(
        feedback.source_event_time_utc,
        field_name="coin_group_calibration_source_event_time_utc",
    )
    if reviewed < source_time:
        raise CoinGroupCalibrationCorpusError(
            "coin_group_calibration_review_before_event"
        )
    parser_version = str(parser_version_before or "").strip()
    if not parser_version or len(parser_version) > 128:
        raise CoinGroupCalibrationCorpusError(
            "coin_group_calibration_parser_version_invalid"
        )
    return {
        "event_key": feedback.event_key.hex(),
        "review_revision": int(feedback.review_revision),
        "reviewed_at_utc": reviewed,
        "event_type": feedback.event_type,
        "group_number": int(feedback.group_number),
        "ambiguous_fields": fields,
        "event_confirmed": bool(feedback.event_confirmed),
        "commodity_code": feedback.commodity_code,
        "side": feedback.side,
        "price_project_thousand_toman": int(
            feedback.price_project_thousand_toman
        ),
        "quantity": int(feedback.quantity),
        "settlement_term": feedback.settlement_term,
        "trade_form": feedback.trade_form,
        "is_conditional": bool(feedback.is_conditional),
        "parser_version_before": parser_version,
        "feedback_version": COIN_GROUP_FEEDBACK_VERSION,
    }


def _digest(payload: dict[str, object]) -> bytes:
    return sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).digest()


def append_coin_group_feedback_revisions(
    connection: sqlite3.Connection,
    feedback: Iterable[CoinGroupParserFeedback],
    *,
    parser_version_before: str,
    appended_at_utc: str | None = None,
) -> CalibrationCorpusReport:
    """Append unseen review revisions; an existing revision is immutable."""

    initialize_coin_group_calibration_corpus(connection)
    appended_at = normalize_utc(
        appended_at_utc or _now(),
        field_name="coin_group_calibration_appended_at_utc",
    )
    seen = appended = replayed = 0
    for item in feedback:
        seen += 1
        payload = _revision_payload(
            item,
            parser_version_before=parser_version_before,
        )
        digest = _digest(payload)
        existing = connection.execute(
            """
            SELECT revision_digest,parser_version_before
            FROM coin_group_calibration_corpus
            WHERE event_key=? AND review_revision=?
            """,
            (item.event_key, int(item.review_revision)),
        ).fetchone()
        if existing is not None:
            if bytes(existing["revision_digest"]) != digest:
                replay_payload = _revision_payload(
                    item,
                    parser_version_before=str(existing["parser_version_before"]),
                )
                if bytes(existing["revision_digest"]) != _digest(replay_payload):
                    raise CoinGroupCalibrationCorpusError(
                        "coin_group_calibration_revision_conflict"
                    )
            replayed += 1
            continue
        connection.execute(
            """
            INSERT INTO coin_group_calibration_corpus(
                event_key,review_revision,reviewed_at_utc,event_type,group_number,
                ambiguous_fields_json,event_confirmed,commodity_code,side,
                price_project_thousand_toman,quantity,settlement_term,trade_form,
                is_conditional,parser_version_before,feedback_version,
                revision_digest,appended_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                item.event_key,
                int(item.review_revision),
                payload["reviewed_at_utc"],
                item.event_type,
                int(item.group_number),
                json.dumps(payload["ambiguous_fields"], separators=(",", ":")),
                int(item.event_confirmed),
                item.commodity_code,
                item.side,
                int(item.price_project_thousand_toman),
                int(item.quantity),
                item.settlement_term,
                item.trade_form,
                int(item.is_conditional),
                payload["parser_version_before"],
                COIN_GROUP_FEEDBACK_VERSION,
                digest,
                appended_at,
            ),
        )
        appended += 1
    return CalibrationCorpusReport(
        reviews_seen=seen,
        revisions_appended=appended,
        idempotent_replays=replayed,
    )


__all__ = [
    "CALIBRATION_CORPUS_SCHEMA_VERSION",
    "CALIBRATION_CORPUS_VERSION",
    "CalibrationCorpusReport",
    "CoinGroupCalibrationCorpusError",
    "append_coin_group_feedback_revisions",
    "initialize_coin_group_calibration_corpus",
]
