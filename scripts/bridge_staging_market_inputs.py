#!/usr/bin/env python3
"""Project existing staging inputs into the canonical Market Store.

The Telegram collectors already own network access.  This command is a local,
read-only bridge for the stores they produce and for the quality-gated group
conversation database.  It is deliberately idempotent and fail-closed:

* source databases are opened read-only;
* only normalized economic fields cross the boundary;
* historical imports receive the bridge execution time as ``available_at``;
  therefore a backfill cannot claim that a fact was known before the import;
* the Market Store's opaque event key provides duplicate protection;
* no raw text, sender, URL, Telegram id, or source payload is stored.

This is a staging bridge.  It does not call a provider and it never writes a
project PostgreSQL database.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Iterable, Iterator, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.market_contracts import (
    MarketObservation,
    MarketStoreContractError,
    derive_event_key,
    normalize_utc,
)
from core.market_intelligence.market_store import (
    MARKET_STORE_HOT_RETENTION_HOURS,
    _storage_values,
    archive_observations_older_than,
    connect_market_store,
    initialize_market_store,
)
from core.market_intelligence.price_magnitude_policy import (
    PriceUnitPolicyError,
    canonicalize_external_price,
    canonicalize_legacy_public_price,
)


BRIDGE_VERSION = "staging-market-input-bridge-v5"
STATE_VERSION = 1
BATCH_SIZE = 2000
GROUP_HOT_RETENTION_HOURS = MARKET_STORE_HOT_RETENTION_HOURS
_CONDITIONAL = re.compile(r"فیش|شرط|مهلت|واریز|تسویه|چک|شرکت|کدs*ملی|یکجا|فوری")
_ALLOWED_LEGACY_SOURCES = frozenset({"MELTED_AGGREGATE", "MELTED_FLOW", "USD_HERAT", "XAUUSD"})
_ALLOWED_EXTERNAL_INSTRUMENTS = frozenset({"USDT_IRT", "IME_GOLD_BAR", "IME_GOLD_COIN_IMAM"})
_ALLOWED_EXTERNAL_QUOTES = frozenset({"MID", "LAST", "CLOSE", "BID", "ASK"})
# price_events.instrument is authoritative; source_code only identifies the feed.
_LEGACY_SOURCE_INSTRUMENTS = {
    "MELTED_AGGREGATE": "MELTED_GOLD_AGGREGATE",
    "MELTED_FLOW": "MELTED_GOLD_FLOW",
    "USD_HERAT": "USD_HERAT",
    "XAUUSD": "XAUUSD",
}
_LEGACY_EVENT_INSTRUMENTS = {
    "MELTED_AGGREGATE": "MELTED_GOLD_AGGREGATE",
    "MELTED_GOLD": "MELTED_GOLD_AGGREGATE",
    "MELTED_GOLD_AGGREGATE": "MELTED_GOLD_AGGREGATE",
    "MELTED_GOLD_FLOW": "MELTED_GOLD_FLOW",
    "MELTED_FLOW": "MELTED_GOLD_FLOW",
    "MELTED_GOLD_UNION": "MELTED_GOLD_UNION",
    "GOLD_UNION_QUOTE": "MELTED_GOLD_UNION",
    "GOLD_COIN": "COIN_PUBLIC_CHANNEL",
    "USD_HERAT": "USD_HERAT",
    "XAUUSD": "XAUUSD",
}
_LEGACY_SKIP_ERRORS = frozenset(
    {
        "legacy_price_unit_unsupported",
        "legacy_price_must_be_positive",
        "legacy_instrument_unsupported",
        "instrument_price_unit_mismatch",
    }
)

_UPSERT_SQL = """
INSERT INTO market_observations(
    event_key, source_code, source_family, event_time_utc,
    available_at_utc, tehran_datetime, tehran_date, tehran_minute,
    tehran_weekday, instrument, market_label, settlement_term,
    trade_form, event_type, side, price_value, price_num, price_unit,
    currency, quantity_value, quantity_num, quantity_unit,
    parse_confidence, parser_version, quality_state,
    quality_policy_version, is_conditional, attributes_json,
    inserted_at_utc
) VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
ON CONFLICT(event_key) DO UPDATE SET
    source_code = excluded.source_code,
    source_family = excluded.source_family,
    event_time_utc = excluded.event_time_utc,
    available_at_utc = excluded.available_at_utc,
    tehran_datetime = excluded.tehran_datetime,
    tehran_date = excluded.tehran_date,
    tehran_minute = excluded.tehran_minute,
    tehran_weekday = excluded.tehran_weekday,
    instrument = excluded.instrument,
    market_label = excluded.market_label,
    settlement_term = excluded.settlement_term,
    trade_form = excluded.trade_form,
    event_type = excluded.event_type,
    side = excluded.side,
    price_value = excluded.price_value,
    price_num = excluded.price_num,
    price_unit = excluded.price_unit,
    currency = excluded.currency,
    quantity_value = excluded.quantity_value,
    quantity_num = excluded.quantity_num,
    quantity_unit = excluded.quantity_unit,
    parse_confidence = excluded.parse_confidence,
    parser_version = excluded.parser_version,
    quality_state = excluded.quality_state,
    quality_policy_version = excluded.quality_policy_version,
    is_conditional = excluded.is_conditional,
    attributes_json = excluded.attributes_json,
    inserted_at_utc = excluded.inserted_at_utc
"""


class BridgeError(RuntimeError):
    """A safe, operator-facing bridge failure."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc(value: object, *, field: str) -> str:
    return normalize_utc(str(value), field_name=field)


def _decimal(value: object, *, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BridgeError(f"{field}_invalid") from exc
    if not number.is_finite() or number <= 0:
        raise BridgeError(f"{field}_must_be_positive")
    return number


def _read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise BridgeError(f"source_database_unavailable:{path}")
    try:
        connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=60)
    except sqlite3.Error as exc:
        raise BridgeError("source_database_open_failed") from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _safe_quantity(value: object, unit: object | None) -> tuple[Decimal | None, str | None]:
    if value is None or unit is None:
        return None, None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None, None
    if not number.is_finite() or number <= 0:
        return None, None
    candidate = str(unit).strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", candidate):
        return None, None
    return number, candidate


def _trade_form(value: object) -> str:
    normalized = str(value or "UNKNOWN").strip().upper()
    return {"PAPER": "PAPER_NORMAL"}.get(normalized, normalized)


def _quality_state(row: Mapping[str, Any] | None, *, trade: bool = False) -> str:
    if row is None:
        return "PENDING_REVIEW"
    eligible = int(row["realtime_eligible"] or 0)
    if eligible == 1:
        return "ELIGIBLE"
    # Keep a durable fact for audit, but never let it reach a model-facing
    # signal.  Snapshot and estimators accept only ELIGIBLE rows.
    return "IGNORED"


def _commodity_code(value: object) -> str:
    normalized = " ".join(str(value or "").split())
    return {
        "امام": "IMAM",
        "امامی": "IMAM",
        "بهار": "BAHAR",
        "ربع بهار": "QUARTER_BAHAR",
        "نیم بهار": "HALF_BAHAR",
        "ربع تاریخ پایین": "QUARTER_LOW_DATE",
        "نیم تاریخ پایین": "HALF_LOW_DATE",
        "یک گرمی": "ONE_GRAM",
    }.get(normalized, "UNRESOLVED")


def _group_source(source_html: object, import_id: int) -> tuple[str, int]:
    value = str(source_html or "")
    if value == "group_1":
        return "GROUP_1", 1
    if value == "group_2":
        return "GROUP_2", 2
    # The first export predates the separate live feeds and cannot be
    # attributed to group 1 or 2 without guessing.  Preserve it distinctly.
    return "GROUP_HISTORICAL", 0


def _safe_attributes(**values: object) -> dict[str, object]:
    return {key: value for key, value in values.items() if value is not None}


def _legacy_instrument(row: sqlite3.Row) -> tuple[str, str]:
    """Return (feed source_code, canonical instrument).

    Public Telegram feeds can publish several instruments on one channel.  The
    parsed ``price_events.instrument`` is therefore preferred over the feed
    code; the feed code remains provenance and the fallback for older rows.
    """

    source = str(row["source_code"]).upper()
    if source not in _LEGACY_SOURCE_INSTRUMENTS:
        raise BridgeError("legacy_instrument_unsupported")
    try:
        event_instrument = str(row["instrument"] or "").strip().upper()
    except (KeyError, IndexError):
        event_instrument = ""
    if event_instrument:
        instrument = _LEGACY_EVENT_INSTRUMENTS.get(event_instrument)
        if instrument is None:
            raise BridgeError("legacy_instrument_unsupported")
        return source, instrument
    return source, _LEGACY_SOURCE_INSTRUMENTS[source]


def _legacy_observation(row: sqlite3.Row, *, available_at: str) -> MarketObservation:
    source, instrument = _legacy_instrument(row)
    event_type = str(row["event_type"] or "QUOTE").upper()
    side = str(row["side"] or "UNKNOWN").upper()
    if instrument == "XAUUSD":
        # The old parser represented a spot quote as UNKNOWN.  The canonical
        # contract uses MID for a reference quote, preserving its neutrality.
        side = "MID"
        event_type = "QUOTE"
    price = _decimal(row["price_num"], field="legacy_price")
    price_unit = str(row["price_unit"] or "").upper()
    currency = str(row["currency"] or "IRT").upper()
    try:
        price, price_unit, currency, conversion_attrs = canonicalize_legacy_public_price(
            price=price,
            price_unit=price_unit,
            currency=currency,
        )
    except PriceUnitPolicyError as exc:
        raise BridgeError(str(exc)) from exc
    if price_unit not in {
        "TOMAN_PER_MESGHAL_750",
        "TOMAN_PER_USD",
        "USD_PER_TROY_OUNCE",
        "TOMAN_PER_COIN",
    }:
        raise BridgeError("legacy_price_unit_unsupported")
    quantity, quantity_unit = _safe_quantity(row["quantity_num"], row["quantity_unit"])
    event_time = _utc(row["event_time_utc"], field="legacy_event_time_utc")
    available = max(event_time, available_at)
    event_index = int(row["event_index"])
    message_id = int(row["message_id"])
    if source == "XAUUSD":
        event_key = derive_event_key("public-telegram-compact-v1", source, event_time[:16], event_index)
    else:
        event_key = derive_event_key("public-telegram-message-v1", source, message_id, event_index)
    return MarketObservation(
        event_key=event_key,
        source_code=source,
        source_family="TELEGRAM_PUBLIC",
        event_time_utc=event_time,
        available_at_utc=available,
        instrument=instrument,
        market_label=f"PUBLIC_{instrument}",
        settlement_term=str(row["settlement_term"] or "UNKNOWN").upper(),
        trade_form=_trade_form(row["trade_form"]),
        event_type=event_type,
        side=side,
        price=price,
        price_unit=price_unit,
        currency=currency,
        quantity=quantity,
        quantity_unit=quantity_unit,
        parse_confidence=float(row["parse_confidence"] or 0.0),
        parser_version=BRIDGE_VERSION,
        quality_state="ELIGIBLE",
        quality_policy_version="legacy-public-replay-conservative-v1",
        is_conditional=False,
        attributes=_safe_attributes(
            bridge=BRIDGE_VERSION,
            historical_source=True,
            legacy_source=source,
            legacy_event_instrument=str(row["instrument"] or "").strip().upper() or None,
            **conversion_attrs,
        ),
    )


def _external_observation(row: sqlite3.Row, *, available_at: str) -> MarketObservation:
    instrument = str(row["instrument_code"]).upper()
    quote_kind = str(row["quote_kind"]).upper()
    if instrument not in _ALLOWED_EXTERNAL_INSTRUMENTS or quote_kind not in _ALLOWED_EXTERNAL_QUOTES:
        raise BridgeError("external_quote_not_selected")
    source = "WALLEX_PUBLIC_API" if instrument == "USDT_IRT" else "IME_REALTIME_BOARD"
    side = {"BID": "BUY", "ASK": "SELL"}.get(quote_kind, "MID")
    event_time = _utc(row["observed_at_utc"], field="external_event_time_utc")
    price_unit = {
        "USDT_IRT": "TOMAN_PER_USDT",
        "IME_GOLD_BAR": "TOMAN_PER_MESGHAL_750",
        "IME_GOLD_COIN_IMAM": "TOMAN_PER_COIN",
    }[instrument]
    price = _decimal(row["normalized_price_num"], field="external_price")
    try:
        price, price_unit, conversion_attrs = canonicalize_external_price(
            instrument=instrument,
            price=price,
            price_unit=price_unit,
        )
    except PriceUnitPolicyError as exc:
        raise BridgeError(str(exc)) from exc
    return MarketObservation(
        event_key=derive_event_key("external-market-v1", source, int(row["id"]), instrument, quote_kind),
        source_code=source,
        source_family="EXTERNAL_MARKET",
        event_time_utc=event_time,
        available_at_utc=max(event_time, available_at),
        instrument=instrument,
        market_label="EXTERNAL_REFERENCE",
        settlement_term="SPOT",
        trade_form="NOT_APPLICABLE",
        event_type="REFERENCE",
        side=side,
        price=price,
        price_unit=price_unit,
        currency="TOMAN",
        parse_confidence=1.0,
        parser_version=BRIDGE_VERSION,
        quality_state="ELIGIBLE",
        quality_policy_version="external-replay-conservative-v1",
        is_conditional=False,
        attributes=_safe_attributes(
            bridge=BRIDGE_VERSION,
            historical_source=True,
            quote_kind=quote_kind,
            external_provider=source,
            **conversion_attrs,
        ),
    )


def _group_source_fingerprint(connection: sqlite3.Connection) -> str:
    offers = connection.execute(
        "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM offers"
    ).fetchone()
    trades = connection.execute(
        "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM confirmed_trades"
    ).fetchone()
    return f"{int(offers[0])}:{int(offers[1])}:{int(trades[0])}:{int(trades[1])}"


def _group_cutoff_utc() -> str:
    return (
        datetime.now(timezone.utc) - timedelta(hours=GROUP_HOT_RETENTION_HOURS)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _group_observations(
    connection: sqlite3.Connection,
    *,
    event_cutoff_utc: str,
) -> Iterator[MarketObservation]:
    offers = connection.execute(
        """
        SELECT o.*, m.source_html_file, m.event_time_utc AS message_event_time_utc, i.imported_at_utc,
               q.realtime_eligible, q.training_eligible, q.training_eligible AS q_training,
               q.training_eligible AS quality_training, q.market_regime, q.regime_confidence,
               q.exclusion_reason
        FROM offers o
        JOIN messages m ON m.import_id = o.import_id AND m.message_id = o.message_id
        JOIN imports i ON i.id = o.import_id
        LEFT JOIN offer_market_quality q ON q.offer_id = o.id
        WHERE m.event_time_utc >= ?
        ORDER BY o.import_id, o.id
        """,
        (event_cutoff_utc,),
    )
    for row in offers:
        source, group_number = _group_source(row["source_html_file"], int(row["import_id"]))
        event_time = _utc(row["message_event_time_utc"], field="group_offer_event_time_utc")
        available = max(event_time, _utc(row["imported_at_utc"], field="group_imported_at_utc"))
        code = _commodity_code(row["commodity"])
        conditional = bool(_CONDITIONAL.search(str(row["source_text"] or "")))
        quantity, quantity_unit = _safe_quantity(row["quantity"], "COIN_COUNT")
        # Reuse the historical parser's exact key shape for the live feeds;
        # this keeps corrections idempotent across bridge runs.
        key_prefix = "coin-group-offer-v1" if source in {"GROUP_1", "GROUP_2"} else "coin-group-history-offer-v1"
        yield MarketObservation(
            event_key=derive_event_key(key_prefix, group_number, int(row["message_id"]), int(row["offer_index"]), int(row["import_id"])),
            source_code=source,
            source_family="GROUP",
            event_time_utc=event_time,
            available_at_utc=available,
            instrument="COIN_" + code,
            market_label="GROUP_COIN_" + code,
            settlement_term=str(row["settlement"] or "UNKNOWN").upper(),
            trade_form=_trade_form(row["trade_form"]),
            event_type="OFFER",
            side=str(row["side"] or "UNKNOWN").upper(),
            price=_decimal(row["price"], field="group_offer_price"),
            price_unit="PROJECT_THOUSAND_TOMAN",
            currency="TOMAN",
            quantity=quantity,
            quantity_unit=quantity_unit,
            parse_confidence=float(row["confidence"] or 0.0),
            parser_version=BRIDGE_VERSION,
            quality_state=_quality_state(row, trade=False),
            quality_policy_version="group-quality-gate-v1",
            is_conditional=conditional,
            attributes=_safe_attributes(
                bridge=BRIDGE_VERSION,
                group_number=group_number,
                historical_source=source == "GROUP_HISTORICAL",
                training_eligible=int(row["training_eligible"] or 0),
                market_regime=row["market_regime"],
                regime_confidence=row["regime_confidence"],
            ),
        )

    trades = connection.execute(
        """
        SELECT t.*, m.source_html_file, i.imported_at_utc,
               q.realtime_eligible, q.training_eligible AS q_training,
               q.market_regime, q.regime_confidence, q.exclusion_reason,
               o.source_text AS offer_source_text
        FROM confirmed_trades t
        JOIN imports i ON i.id = t.import_id
        LEFT JOIN messages m ON m.import_id=t.import_id AND m.message_id=t.confirmation_message_id
        LEFT JOIN trade_market_quality q ON q.trade_id=t.id
        LEFT JOIN offers o ON o.import_id=t.import_id AND o.message_id=t.offer_message_id
        WHERE t.event_time_utc >= ?
        ORDER BY t.import_id, t.id
        """,
        (event_cutoff_utc,),
    )
    for row in trades:
        source, group_number = _group_source(row["source_html_file"], int(row["import_id"]))
        event_time = _utc(row["event_time_utc"], field="group_trade_event_time_utc")
        available = max(event_time, _utc(row["imported_at_utc"], field="group_imported_at_utc"))
        code = _commodity_code(row["commodity"])
        quantity, quantity_unit = _safe_quantity(row["quantity"], "COIN_COUNT")
        key_prefix = "coin-group-trade-v1" if source in {"GROUP_1", "GROUP_2"} else "coin-group-history-trade-v1"
        yield MarketObservation(
            event_key=derive_event_key(key_prefix, group_number, int(row["offer_message_id"] or 0), int(row["confirmation_message_id"]), int(row["id"]), int(row["import_id"])),
            source_code=source,
            source_family="GROUP",
            event_time_utc=event_time,
            available_at_utc=available,
            instrument="COIN_" + code,
            market_label="GROUP_COIN_" + code,
            settlement_term=str(row["settlement"] or "UNKNOWN").upper(),
            trade_form=_trade_form(row["trade_form"]),
            event_type="TRADE",
            side=str(row["side"] or "UNKNOWN").upper(),
            price=_decimal(row["price"], field="group_trade_price"),
            price_unit="PROJECT_THOUSAND_TOMAN",
            currency="TOMAN",
            quantity=quantity,
            quantity_unit=quantity_unit,
            parse_confidence=float(row["confidence"] or 0.0),
            parser_version=BRIDGE_VERSION,
            quality_state=_quality_state(row, trade=True),
            quality_policy_version="group-quality-gate-v1",
            is_conditional=bool(_CONDITIONAL.search(str(row["offer_source_text"] or ""))),
            attributes=_safe_attributes(
                bridge=BRIDGE_VERSION,
                group_number=group_number,
                historical_source=source == "GROUP_HISTORICAL",
                confirmation_type=row["confirmation_type"],
                training_eligible=int(row["training_eligible"] or 0),
                is_aggregate=int(row["is_aggregate"] or 0),
                market_regime=row["market_regime"],
                regime_confidence=row["regime_confidence"],
            ),
        )


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": STATE_VERSION, "legacy_price_event_id": 0, "external_observation_id": 0}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BridgeError("bridge_state_invalid") from exc
    if int(state.get("schema_version") or 0) != STATE_VERSION:
        raise BridgeError("bridge_state_version_unsupported")
    return state


def _save_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(state), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _write_batch(destination: sqlite3.Connection, observations: Iterable[MarketObservation]) -> int:
    values = [_storage_values(observation.normalized()) for observation in observations]
    if not values:
        return 0
    destination.executemany(_UPSERT_SQL, values)
    destination.commit()
    return len(values)


def _write_source_rows(
    destination: sqlite3.Connection,
    rows: Iterable[sqlite3.Row],
    convert: Any,
    *,
    available_at: str,
    skip_errors: frozenset[str] = frozenset(),
) -> tuple[int, int]:
    """Write a source in bounded transactions and return count/max-id."""

    values: list[tuple[object, ...]] = []
    latest = 0
    for row in rows:
        latest = int(row["id"])
        try:
            observation = convert(row, available_at=available_at)
            values.append(_storage_values(observation.normalized()))
        except BridgeError as exc:
            if str(exc) in skip_errors:
                continue
            raise
        except MarketStoreContractError as exc:
            if str(exc) in skip_errors:
                continue
            raise BridgeError(str(exc)) from exc
    if values:
        destination.executemany(_UPSERT_SQL, values)
        destination.commit()
    count = len(values)
    return count, latest


def _legacy_rows(source: sqlite3.Connection, after_id: int) -> Iterator[sqlite3.Row]:
    query = """
        SELECT pe.*, rp.source_code, rp.message_id
        FROM price_events pe JOIN raw_posts rp ON rp.id=pe.raw_post_id
        WHERE pe.id > ? AND rp.source_code IN (?,?,?,?)
        ORDER BY pe.id
        LIMIT ?
    """
    yield from source.execute(query, (after_id, *_ALLOWED_LEGACY_SOURCES, BATCH_SIZE))


def _external_rows(source: sqlite3.Connection, after_id: int) -> Iterator[sqlite3.Row]:
    query = """
        SELECT id,instrument_code,observed_at_utc,quote_kind,normalized_price_num
        FROM external_market_observations
        WHERE id > ? AND instrument_code IN (?,?,?) AND quote_kind IN (?,?,?,?,?)
        ORDER BY id
        LIMIT ?
    """
    yield from source.execute(query, (after_id, *_ALLOWED_EXTERNAL_INSTRUMENTS, *_ALLOWED_EXTERNAL_QUOTES, BATCH_SIZE))


def _path_inside(root: Path, value: str, *, field: str) -> Path:
    supplied = Path(value).expanduser()
    path = (root / supplied).resolve() if not supplied.is_absolute() else supplied.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise BridgeError(f"{field}_outside_runtime_root") from exc
    if path == root:
        raise BridgeError(f"{field}_must_be_file")
    return path


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BridgeError("bridge_already_running") from exc
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def run(args: argparse.Namespace) -> dict[str, Any]:
    runtime_root = Path(args.runtime_root).expanduser().resolve()
    if not runtime_root.is_dir():
        raise BridgeError("runtime_root_unavailable")
    market_path = _path_inside(runtime_root, args.market_store, field="market_store")
    state_path = _path_inside(runtime_root, args.state_file, field="state_file")
    lock_path = _path_inside(runtime_root, args.lock_file, field="lock_file")
    state = _load_state(state_path)
    available_at = _now()
    counts = {"legacy_public": 0, "external": 0, "group": 0, "archived": 0}
    last_legacy = int(state.get("legacy_price_event_id") or 0)
    last_external = int(state.get("external_observation_id") or 0)
    group_skipped = False
    with _lock(lock_path):
        destination = connect_market_store(market_path)
        try:
            initialize_market_store(destination)
            # The public/gold collectors are intentionally kept online.  A
            # bounded bridge write waits for their short WAL transaction
            # rather than dropping the whole backfill on a transient lock.
            destination.execute("PRAGMA busy_timeout=300000")
            if args.legacy_market_db:
                while True:
                    # Close the source read transaction before touching the
                    # destination.  The legacy collector may be writing the
                    # same SQLite file at this moment.
                    with _read_only(Path(args.legacy_market_db)) as source:
                        batch = list(_legacy_rows(source, last_legacy))
                    if not batch:
                        break
                    imported, latest = _write_source_rows(
                        destination,
                        batch,
                        _legacy_observation,
                        available_at=available_at,
                        skip_errors=_LEGACY_SKIP_ERRORS,
                    )
                    counts["legacy_public"] += imported
                    last_legacy = max(last_legacy, latest)
                    state["legacy_price_event_id"] = last_legacy
                    _save_state(state_path, state)
            if args.external_market_db:
                while True:
                    with _read_only(Path(args.external_market_db)) as source:
                        batch = list(_external_rows(source, last_external))
                    if not batch:
                        break
                    imported, latest = _write_source_rows(
                        destination,
                        batch,
                        _external_observation,
                        available_at=available_at,
                        skip_errors=frozenset({"external_quote_not_selected"}),
                    )
                    counts["external"] += imported
                    last_external = max(last_external, latest)
                    state["external_observation_id"] = last_external
                    _save_state(state_path, state)
            if args.conversation_db:
                with _read_only(Path(args.conversation_db)) as source:
                    fingerprint = _group_source_fingerprint(source)
                    if fingerprint == str(state.get("group_source_fingerprint") or ""):
                        group_skipped = True
                        counts["group"] = 0
                    else:
                        counts["group"] = _write_batch(
                            destination,
                            _group_observations(source, event_cutoff_utc=_group_cutoff_utc()),
                        )
                        state["group_source_fingerprint"] = fingerprint
            archive_report = archive_observations_older_than(
                destination,
                retention_hours=GROUP_HOT_RETENTION_HOURS,
            )
            counts["archived"] = int(archive_report.get("archived_rows") or 0)
            state["hot_store"] = archive_report
        finally:
            destination.close()
        state.update({
            "schema_version": STATE_VERSION,
            "bridge_version": BRIDGE_VERSION,
            "updated_at_utc": _now(),
            "legacy_price_event_id": last_legacy,
            "external_observation_id": last_external,
            "last_group_refresh_at_utc": _now(),
            "group_refresh_skipped": group_skipped,
        })
        _save_state(state_path, state)
    return {
        "status": "BRIDGED",
        "bridge_version": BRIDGE_VERSION,
        "available_at_utc": available_at,
        "counts": counts,
        "group_refresh_skipped": group_skipped,
        "state": state,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--market-store", required=True)
    parser.add_argument("--state-file", default="staging/market-input-bridge.state.json")
    parser.add_argument("--lock-file", default="staging/.market-input-bridge.lock")
    parser.add_argument("--legacy-market-db")
    parser.add_argument("--external-market-db")
    parser.add_argument("--conversation-db")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        print(json.dumps(run(build_parser().parse_args(argv)), sort_keys=True, ensure_ascii=False), flush=True)
        return 0
    except (BridgeError, OSError, sqlite3.Error, ValueError) as exc:
        reason = str(exc)
        if reason == "bridge_already_running":
            print(json.dumps({"status": "SKIPPED", "reason": reason}, ensure_ascii=False), flush=True)
            return 0
        print(json.dumps({"status": "FAILED", "reason": reason}, ensure_ascii=False), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
