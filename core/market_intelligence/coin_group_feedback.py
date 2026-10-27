"""Privacy-safe operator feedback for the private coin-group parser.

The feedback store never contains raw group text, Telegram message IDs, sender
identities, or display names.  One opaque canonical event key is paired with a
complete reviewed economic fact.  The live pipeline applies that fact only
after ``reviewed_at_utc`` and may use it as a causal, time-bounded resolver
anchor for later messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import blake2b
import json
from pathlib import Path
import sqlite3
from typing import Iterable, Mapping

from .coin_groups import _PRICE_BOUNDS
from .market_contracts import MarketStoreContractError, normalize_utc


COIN_GROUP_FEEDBACK_VERSION = "coin-group-human-feedback-v1"
AMBIGUOUS_FIELDS = frozenset(
    {
        "event_validity",
        "commodity",
        "side",
        "price",
        "quantity",
        "settlement",
        "trade_form",
        "conditional",
    }
)
_SIDES = frozenset({"BUY", "SELL"})
_SETTLEMENTS = frozenset({"CASH", "TODAY", "TOMORROW"})
_TRADE_FORMS = frozenset(
    {"PHYSICAL", "PAPER_NORMAL", "PAPER_REVERSE", "PAPER_SWIM"}
)

_SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS coin_group_parser_feedback_state(
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    schema_version INTEGER NOT NULL,
    calibration_revision INTEGER NOT NULL CHECK(calibration_revision>=0),
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coin_group_parser_feedback(
    event_key BLOB PRIMARY KEY CHECK(length(event_key) BETWEEN 16 AND 64),
    event_type TEXT NOT NULL CHECK(event_type IN ('OFFER','TRADE')),
    group_number INTEGER NOT NULL CHECK(group_number IN (1,2)),
    source_event_time_utc TEXT NOT NULL,
    ambiguous_fields_json TEXT NOT NULL,
    event_confirmed INTEGER NOT NULL CHECK(event_confirmed IN (0,1)),
    commodity_code TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
    price_project_thousand_toman INTEGER NOT NULL CHECK(price_project_thousand_toman>0),
    quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 100),
    settlement_term TEXT NOT NULL CHECK(settlement_term IN ('CASH','TODAY','TOMORROW')),
    trade_form TEXT NOT NULL CHECK(
        trade_form IN ('PHYSICAL','PAPER_NORMAL','PAPER_REVERSE','PAPER_SWIM')
    ),
    is_conditional INTEGER NOT NULL CHECK(is_conditional IN (0,1)),
    reviewer_digest BLOB NOT NULL CHECK(length(reviewer_digest)=32),
    review_revision INTEGER NOT NULL CHECK(review_revision>0),
    reviewed_at_utc TEXT NOT NULL,
    applied_revision INTEGER NOT NULL DEFAULT 0 CHECK(applied_revision>=0),
    applied_at_utc TEXT,
    application_count INTEGER NOT NULL DEFAULT 0 CHECK(application_count>=0)
);

CREATE INDEX IF NOT EXISTS idx_coin_group_parser_feedback_reviewed
ON coin_group_parser_feedback(reviewed_at_utc,event_type,group_number);
"""


@dataclass(frozen=True, slots=True)
class CoinGroupParserFeedback:
    event_key: bytes
    event_type: str
    group_number: int
    source_event_time_utc: str
    ambiguous_fields: frozenset[str]
    event_confirmed: bool
    commodity_code: str
    side: str
    price_project_thousand_toman: int
    quantity: int
    settlement_term: str
    trade_form: str
    is_conditional: bool
    review_revision: int
    reviewed_at_utc: str
    applied_revision: int
    applied_at_utc: str | None
    application_count: int


class CoinGroupFeedbackError(ValueError):
    """A redacted validation or sidecar-contract error."""


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _event_key(value: bytes | str) -> bytes:
    if isinstance(value, bytes):
        decoded = value
    else:
        text = str(value or "").strip()
        try:
            decoded = bytes.fromhex(text)
        except ValueError as exc:
            raise CoinGroupFeedbackError("parser_feedback_event_key_invalid") from exc
    if not 16 <= len(decoded) <= 64:
        raise CoinGroupFeedbackError("parser_feedback_event_key_invalid")
    return decoded


def _reviewer_digest(value: str) -> bytes:
    normalized = str(value or "").strip()
    if not normalized:
        raise CoinGroupFeedbackError("parser_feedback_reviewer_required")
    return blake2b(
        normalized.encode("utf-8"),
        digest_size=32,
        person=b"coin-grp-review1",
    ).digest()


def _connection(
    path: Path | str,
    *,
    read_only: bool,
    immutable: bool = False,
) -> sqlite3.Connection:
    database = Path(path).expanduser().resolve()
    if read_only:
        if not database.is_file():
            raise CoinGroupFeedbackError("parser_feedback_store_unavailable")
        immutable_query = "&immutable=1" if immutable else ""
        connection = sqlite3.connect(
            f"file:{database}?mode=ro{immutable_query}", uri=True
        )
    else:
        database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    if not read_only:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
    return connection


def ensure_coin_group_feedback_store(path: Path | str) -> None:
    connection = _connection(path, read_only=False)
    try:
        connection.executescript(_SCHEMA)
        row = connection.execute(
            "SELECT schema_version FROM coin_group_parser_feedback_state WHERE singleton=1"
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO coin_group_parser_feedback_state VALUES(1,1,0,?)",
                (_utc_now(),),
            )
        elif int(row["schema_version"]) != 1:
            raise CoinGroupFeedbackError("parser_feedback_schema_upgrade_required")
        connection.commit()
    finally:
        connection.close()


def _validated_values(
    *,
    event_type: str,
    group_number: int,
    source_event_time_utc: str,
    ambiguous_fields: Iterable[str],
    event_confirmed: bool,
    commodity_code: str,
    side: str,
    price_project_thousand_toman: int,
    quantity: int,
    settlement_term: str,
    trade_form: str,
    is_conditional: bool,
    reviewed_at_utc: str,
) -> tuple[str, int, str, tuple[str, ...], bool, str, str, int, int, str, str, bool, str]:
    normalized_type = str(event_type or "").strip().upper()
    if normalized_type not in {"OFFER", "TRADE"}:
        raise CoinGroupFeedbackError("parser_feedback_event_type_invalid")
    try:
        normalized_group = int(group_number)
    except (TypeError, ValueError) as exc:
        raise CoinGroupFeedbackError("parser_feedback_group_invalid") from exc
    if normalized_group not in {1, 2}:
        raise CoinGroupFeedbackError("parser_feedback_group_invalid")
    fields = tuple(sorted({str(item).strip() for item in ambiguous_fields}))
    if not fields or not set(fields).issubset(AMBIGUOUS_FIELDS):
        raise CoinGroupFeedbackError("parser_feedback_ambiguous_fields_invalid")
    if not isinstance(event_confirmed, bool) or not isinstance(is_conditional, bool):
        raise CoinGroupFeedbackError("parser_feedback_boolean_field_invalid")
    code = str(commodity_code or "").strip().upper()
    side_value = str(side or "").strip().upper()
    settlement = str(settlement_term or "").strip().upper()
    form = str(trade_form or "").strip().upper()
    if code not in _PRICE_BOUNDS and not (
        not event_confirmed and code == "UNRESOLVED"
    ):
        raise CoinGroupFeedbackError("parser_feedback_commodity_invalid")
    if side_value not in _SIDES:
        raise CoinGroupFeedbackError("parser_feedback_side_invalid")
    if settlement not in _SETTLEMENTS:
        raise CoinGroupFeedbackError("parser_feedback_settlement_invalid")
    if form not in _TRADE_FORMS:
        raise CoinGroupFeedbackError("parser_feedback_trade_form_invalid")
    try:
        price = int(price_project_thousand_toman)
        count = int(quantity)
    except (TypeError, ValueError) as exc:
        raise CoinGroupFeedbackError("parser_feedback_numeric_field_invalid") from exc
    if event_confirmed:
        low, high = _PRICE_BOUNDS[code]
        if not low <= price <= high:
            raise CoinGroupFeedbackError("parser_feedback_price_outside_commodity_band")
    elif price <= 0:
        raise CoinGroupFeedbackError("parser_feedback_price_invalid")
    if not 1 <= count <= 100:
        raise CoinGroupFeedbackError("parser_feedback_quantity_invalid")
    try:
        source_time = normalize_utc(
            source_event_time_utc, field_name="parser_feedback_source_event_time_utc"
        )
        reviewed_at = normalize_utc(
            reviewed_at_utc, field_name="parser_feedback_reviewed_at_utc"
        )
    except MarketStoreContractError as exc:
        raise CoinGroupFeedbackError(str(exc)) from exc
    if reviewed_at < source_time:
        raise CoinGroupFeedbackError("parser_feedback_review_before_event")
    return (
        normalized_type,
        normalized_group,
        source_time,
        fields,
        event_confirmed,
        code,
        side_value,
        price,
        count,
        settlement,
        form,
        is_conditional,
        reviewed_at,
    )


def record_coin_group_parser_feedback(
    path: Path | str,
    *,
    event_key: bytes | str,
    event_type: str,
    group_number: int,
    source_event_time_utc: str,
    ambiguous_fields: Iterable[str],
    event_confirmed: bool,
    commodity_code: str,
    side: str,
    price_project_thousand_toman: int,
    quantity: int,
    settlement_term: str,
    trade_form: str,
    is_conditional: bool,
    reviewer: str,
    reviewed_at_utc: str | None = None,
) -> CoinGroupParserFeedback:
    """Upsert one complete review and increment the online calibration revision."""

    opaque_key = _event_key(event_key)
    values = _validated_values(
        event_type=event_type,
        group_number=group_number,
        source_event_time_utc=source_event_time_utc,
        ambiguous_fields=ambiguous_fields,
        event_confirmed=event_confirmed,
        commodity_code=commodity_code,
        side=side,
        price_project_thousand_toman=price_project_thousand_toman,
        quantity=quantity,
        settlement_term=settlement_term,
        trade_form=trade_form,
        is_conditional=is_conditional,
        reviewed_at_utc=reviewed_at_utc or _utc_now(),
    )
    (
        normalized_type,
        normalized_group,
        source_time,
        fields,
        confirmed,
        code,
        side_value,
        price,
        count,
        settlement,
        form,
        conditional,
        reviewed_at,
    ) = values
    ensure_coin_group_feedback_store(path)
    connection = _connection(path, read_only=False)
    try:
        connection.execute("BEGIN IMMEDIATE")
        prior = connection.execute(
            "SELECT review_revision FROM coin_group_parser_feedback WHERE event_key=?",
            (opaque_key,),
        ).fetchone()
        review_revision = int(prior["review_revision"]) + 1 if prior else 1
        state = connection.execute(
            "SELECT calibration_revision FROM coin_group_parser_feedback_state WHERE singleton=1"
        ).fetchone()
        calibration_revision = int(state["calibration_revision"]) + 1
        connection.execute(
            """
            INSERT INTO coin_group_parser_feedback(
                event_key,event_type,group_number,source_event_time_utc,
                ambiguous_fields_json,event_confirmed,commodity_code,side,
                price_project_thousand_toman,quantity,settlement_term,trade_form,
                is_conditional,reviewer_digest,review_revision,reviewed_at_utc,
                applied_revision,applied_at_utc,application_count
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,NULL,0)
            ON CONFLICT(event_key) DO UPDATE SET
                event_type=excluded.event_type,
                group_number=excluded.group_number,
                source_event_time_utc=excluded.source_event_time_utc,
                ambiguous_fields_json=excluded.ambiguous_fields_json,
                event_confirmed=excluded.event_confirmed,
                commodity_code=excluded.commodity_code,
                side=excluded.side,
                price_project_thousand_toman=excluded.price_project_thousand_toman,
                quantity=excluded.quantity,
                settlement_term=excluded.settlement_term,
                trade_form=excluded.trade_form,
                is_conditional=excluded.is_conditional,
                reviewer_digest=excluded.reviewer_digest,
                review_revision=excluded.review_revision,
                reviewed_at_utc=excluded.reviewed_at_utc,
                applied_revision=0,applied_at_utc=NULL
            """,
            (
                opaque_key,
                normalized_type,
                normalized_group,
                source_time,
                json.dumps(fields, separators=(",", ":")),
                int(confirmed),
                code,
                side_value,
                price,
                count,
                settlement,
                form,
                int(conditional),
                _reviewer_digest(reviewer),
                review_revision,
                reviewed_at,
            ),
        )
        connection.execute(
            "UPDATE coin_group_parser_feedback_state SET calibration_revision=?,updated_at_utc=? WHERE singleton=1",
            (calibration_revision, reviewed_at),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return load_coin_group_parser_feedback(path)[opaque_key]


def record_coin_group_parser_feedback_batch(
    path: Path | str,
    decisions: Iterable[Mapping[str, object]],
    *,
    reviewer: str,
    reviewed_at_utc: str | None = None,
) -> dict[str, int]:
    """Atomically record complete, privacy-safe review decisions.

    The batch is validated in full before the feedback store is opened for
    writing.  Replaying an identical decision is a no-op, while a changed
    decision receives the next per-event revision.  This keeps supervised
    historical remediation all-or-nothing and safely repeatable.
    """

    reviewed_at = reviewed_at_utc or _utc_now()
    reviewer_digest = _reviewer_digest(reviewer)
    normalized: list[
        tuple[
            bytes,
            str,
            int,
            str,
            tuple[str, ...],
            bool,
            str,
            str,
            int,
            int,
            str,
            str,
            bool,
            str,
        ]
    ] = []
    seen: set[bytes] = set()
    for decision in decisions:
        key = _event_key(decision.get("event_key", b""))
        if key in seen:
            raise CoinGroupFeedbackError("parser_feedback_batch_duplicate_event")
        seen.add(key)
        values = _validated_values(
            event_type=str(decision.get("event_type") or ""),
            group_number=decision.get("group_number"),  # type: ignore[arg-type]
            source_event_time_utc=str(
                decision.get("source_event_time_utc") or ""
            ),
            ambiguous_fields=decision.get("ambiguous_fields") or (),
            event_confirmed=decision.get("event_confirmed"),  # type: ignore[arg-type]
            commodity_code=str(decision.get("commodity_code") or ""),
            side=str(decision.get("side") or ""),
            price_project_thousand_toman=decision.get(
                "price_project_thousand_toman"
            ),  # type: ignore[arg-type]
            quantity=decision.get("quantity"),  # type: ignore[arg-type]
            settlement_term=str(decision.get("settlement_term") or ""),
            trade_form=str(decision.get("trade_form") or ""),
            is_conditional=decision.get("is_conditional"),  # type: ignore[arg-type]
            reviewed_at_utc=reviewed_at,
        )
        normalized.append((key, *values))
    if not normalized:
        return {"submitted": 0, "recorded": 0, "unchanged": 0}

    ensure_coin_group_feedback_store(path)
    connection = _connection(path, read_only=False)
    recorded = unchanged = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        state = connection.execute(
            "SELECT calibration_revision FROM coin_group_parser_feedback_state WHERE singleton=1"
        ).fetchone()
        calibration_revision = int(state["calibration_revision"])
        for item in normalized:
            (
                opaque_key,
                normalized_type,
                normalized_group,
                source_time,
                fields,
                confirmed,
                code,
                side_value,
                price,
                count,
                settlement,
                form,
                conditional,
                normalized_reviewed_at,
            ) = item
            fields_json = json.dumps(fields, separators=(",", ":"))
            prior = connection.execute(
                "SELECT * FROM coin_group_parser_feedback WHERE event_key=?",
                (opaque_key,),
            ).fetchone()
            comparable = (
                normalized_type,
                normalized_group,
                source_time,
                fields_json,
                int(confirmed),
                code,
                side_value,
                price,
                count,
                settlement,
                form,
                int(conditional),
            )
            if prior is not None and comparable == (
                str(prior["event_type"]),
                int(prior["group_number"]),
                str(prior["source_event_time_utc"]),
                str(prior["ambiguous_fields_json"]),
                int(prior["event_confirmed"]),
                str(prior["commodity_code"]),
                str(prior["side"]),
                int(prior["price_project_thousand_toman"]),
                int(prior["quantity"]),
                str(prior["settlement_term"]),
                str(prior["trade_form"]),
                int(prior["is_conditional"]),
            ):
                unchanged += 1
                continue
            review_revision = (
                int(prior["review_revision"]) + 1 if prior is not None else 1
            )
            connection.execute(
                """
                INSERT INTO coin_group_parser_feedback(
                    event_key,event_type,group_number,source_event_time_utc,
                    ambiguous_fields_json,event_confirmed,commodity_code,side,
                    price_project_thousand_toman,quantity,settlement_term,trade_form,
                    is_conditional,reviewer_digest,review_revision,reviewed_at_utc,
                    applied_revision,applied_at_utc,application_count
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,NULL,0)
                ON CONFLICT(event_key) DO UPDATE SET
                    event_type=excluded.event_type,
                    group_number=excluded.group_number,
                    source_event_time_utc=excluded.source_event_time_utc,
                    ambiguous_fields_json=excluded.ambiguous_fields_json,
                    event_confirmed=excluded.event_confirmed,
                    commodity_code=excluded.commodity_code,
                    side=excluded.side,
                    price_project_thousand_toman=excluded.price_project_thousand_toman,
                    quantity=excluded.quantity,
                    settlement_term=excluded.settlement_term,
                    trade_form=excluded.trade_form,
                    is_conditional=excluded.is_conditional,
                    reviewer_digest=excluded.reviewer_digest,
                    review_revision=excluded.review_revision,
                    reviewed_at_utc=excluded.reviewed_at_utc,
                    applied_revision=0,applied_at_utc=NULL
                """,
                (
                    opaque_key,
                    normalized_type,
                    normalized_group,
                    source_time,
                    fields_json,
                    int(confirmed),
                    code,
                    side_value,
                    price,
                    count,
                    settlement,
                    form,
                    int(conditional),
                    reviewer_digest,
                    review_revision,
                    normalized_reviewed_at,
                ),
            )
            recorded += 1
        if recorded:
            connection.execute(
                "UPDATE coin_group_parser_feedback_state "
                "SET calibration_revision=?,updated_at_utc=? WHERE singleton=1",
                (calibration_revision + recorded, reviewed_at),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "submitted": len(normalized),
        "recorded": recorded,
        "unchanged": unchanged,
    }


def _feedback_from_row(row: sqlite3.Row) -> CoinGroupParserFeedback:
    try:
        fields = frozenset(json.loads(str(row["ambiguous_fields_json"])))
    except (TypeError, ValueError):
        fields = frozenset()
    return CoinGroupParserFeedback(
        event_key=bytes(row["event_key"]),
        event_type=str(row["event_type"]),
        group_number=int(row["group_number"]),
        source_event_time_utc=str(row["source_event_time_utc"]),
        ambiguous_fields=fields,
        event_confirmed=bool(row["event_confirmed"]),
        commodity_code=str(row["commodity_code"]),
        side=str(row["side"]),
        price_project_thousand_toman=int(row["price_project_thousand_toman"]),
        quantity=int(row["quantity"]),
        settlement_term=str(row["settlement_term"]),
        trade_form=str(row["trade_form"]),
        is_conditional=bool(row["is_conditional"]),
        review_revision=int(row["review_revision"]),
        reviewed_at_utc=str(row["reviewed_at_utc"]),
        applied_revision=int(row["applied_revision"]),
        applied_at_utc=str(row["applied_at_utc"]) if row["applied_at_utc"] else None,
        application_count=int(row["application_count"]),
    )


def load_coin_group_parser_feedback(
    path: Path | str,
    *,
    immutable: bool = False,
) -> dict[bytes, CoinGroupParserFeedback]:
    try:
        connection = _connection(path, read_only=True, immutable=immutable)
    except CoinGroupFeedbackError:
        return {}
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='coin_group_parser_feedback'"
        ).fetchone()
        if table is None:
            return {}
        rows = connection.execute(
            "SELECT * FROM coin_group_parser_feedback ORDER BY reviewed_at_utc,event_key"
        ).fetchall()
        return {bytes(row["event_key"]): _feedback_from_row(row) for row in rows}
    finally:
        connection.close()


def mark_coin_group_parser_feedback_applied(
    path: Path | str,
    event_keys: Iterable[bytes],
    *,
    applied_at_utc: str | None = None,
) -> int:
    keys = tuple(dict.fromkeys(_event_key(item) for item in event_keys))
    if not keys:
        return 0
    applied_at = normalize_utc(
        applied_at_utc or _utc_now(), field_name="parser_feedback_applied_at_utc"
    )
    ensure_coin_group_feedback_store(path)
    connection = _connection(path, read_only=False)
    changed = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        for key in keys:
            cursor = connection.execute(
                """
                UPDATE coin_group_parser_feedback
                SET applied_revision=review_revision,applied_at_utc=?,
                    application_count=application_count+1
                WHERE event_key=? AND applied_revision<review_revision
                """,
                (applied_at, key),
            )
            changed += int(cursor.rowcount)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return changed
