"""Three-day private staging for reconciled melted-gold offer/trade events.

The crawler emits an offer and its later verifier result through separate
event streams.  A verifier payload is not a market quote by itself: it must
be merged with the original offer before the canonical private-gold adapter
can emit a trade.  Raw text remains only in this bounded SQLite staging DB;
the Market Store receives privacy-minimized facts exclusively.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import blake2b
from pathlib import Path
import sqlite3

from .market_contracts import MarketStoreContractError, normalize_utc
from .market_store import upsert_observation
from .private_gold import PrivateGoldOfferInput, private_gold_observations


PRIVATE_GOLD_STAGING_SCHEMA_VERSION = 1
PRIVATE_GOLD_STAGING_RETENTION = timedelta(days=3)
PRIVATE_GOLD_STAGING_VERSION = "private-gold-staging-v1"
_MAX_TEXT_BYTES = 32 * 1024
_TRADE_STATUSES = frozenset({"NONE", "FULL", "PARTIAL", "CHANGED_UNCLASSIFIED", "PENDING"})
_TRADE_STATUS_ALIASES = {"NO_TRADE": "NONE", "COMPLETED": "FULL", "TRADED": "FULL"}


class PrivateGoldStagingError(RuntimeError):
    """Private source data cannot safely enter short-lived staging."""


@dataclass(frozen=True, slots=True)
class PrivateGoldStagingOffer:
    source_message_id: str
    event_time_utc: datetime | str
    available_at_utc: datetime | str
    text: str
    edited_at_utc: datetime | str | None = None


@dataclass(frozen=True, slots=True)
class PrivateGoldStagingTradeUpdate:
    source_message_id: str
    available_at_utc: datetime | str
    trade_status: str | None = None
    traded_quantity: int | None = None
    trade_detected_at_utc: datetime | str | None = None
    telegram_edit_datetime: datetime | str | None = None


@dataclass(frozen=True, slots=True)
class StagedPrivateGoldOffer:
    source_message_id: str
    event_time_utc: str | None
    offer_available_at_utc: str | None
    text: str | None
    offer_edited_at_utc: str | None
    trade_status: str
    traded_quantity: int | None
    trade_detected_at_utc: str | None
    trade_edited_at_utc: str | None
    trade_available_at_utc: str | None
    revision: int
    expires_at_utc: str


@dataclass(frozen=True, slots=True)
class PrivateGoldPromotionReport:
    staged_rows_seen: int
    offer_facts_upserted: int
    trade_facts_upserted: int
    unparseable_or_incomplete_rows: int


_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS private_gold_staging_metadata (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    schema_version INTEGER NOT NULL,
    initialized_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS private_gold_staged_offers (
    source_message_id TEXT PRIMARY KEY,
    event_time_utc TEXT,
    offer_available_at_utc TEXT,
    offer_text TEXT,
    offer_edited_at_utc TEXT,
    offer_digest BLOB,
    trade_status TEXT NOT NULL DEFAULT 'PENDING',
    traded_quantity INTEGER,
    trade_detected_at_utc TEXT,
    trade_edited_at_utc TEXT,
    trade_available_at_utc TEXT,
    trade_digest BLOB,
    revision INTEGER NOT NULL CHECK(revision > 0),
    first_staged_at_utc TEXT NOT NULL,
    last_staged_at_utc TEXT NOT NULL,
    expires_at_utc TEXT NOT NULL,
    CHECK(offer_text IS NULL OR length(offer_text) > 0),
    CHECK(traded_quantity IS NULL OR traded_quantity > 0)
);

CREATE INDEX IF NOT EXISTS idx_private_gold_staged_offers_expiry
    ON private_gold_staged_offers(expires_at_utc);
CREATE INDEX IF NOT EXISTS idx_private_gold_staged_offers_pending
    ON private_gold_staged_offers(offer_text, trade_status, last_staged_at_utc);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def assert_private_gold_staging_path_outside_repository(
    path: Path | str,
    *,
    repository_root: Path | str,
) -> Path:
    target = Path(path).expanduser().resolve()
    root = Path(repository_root).expanduser().resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return target
    raise PrivateGoldStagingError("private_gold_staging_path_inside_repository")


def connect_private_gold_staging(
    path: Path | str,
    *,
    repository_root: Path | str | None = None,
) -> sqlite3.Connection:
    database = (
        assert_private_gold_staging_path_outside_repository(path, repository_root=repository_root)
        if repository_root is not None
        else Path(path).expanduser().resolve()
    )
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    return connection


def initialize_private_gold_staging(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name = 'private_gold_staging_metadata'"
    ).fetchone()
    if row is None:
        connection.executescript(_SCHEMA)
        connection.execute(
            "INSERT INTO private_gold_staging_metadata(singleton, schema_version, initialized_at_utc) "
            "VALUES (1, ?, ?)",
            (PRIVATE_GOLD_STAGING_SCHEMA_VERSION, _utc_now()),
        )
        connection.commit()
        return
    metadata = connection.execute(
        "SELECT schema_version FROM private_gold_staging_metadata WHERE singleton = 1"
    ).fetchone()
    if metadata is None or int(metadata["schema_version"]) != PRIVATE_GOLD_STAGING_SCHEMA_VERSION:
        raise PrivateGoldStagingError("private_gold_staging_schema_upgrade_required")


def _message_id(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 64 or not normalized.isascii() or not normalized.isdecimal():
        raise PrivateGoldStagingError("private_gold_staging_message_id_invalid")
    return normalized


def _utc(value: datetime | str | None, *, name: str, required: bool) -> str | None:
    if value is None:
        if required:
            raise PrivateGoldStagingError(f"{name}_required")
        return None
    try:
        return normalize_utc(value, field_name=name)
    except MarketStoreContractError as exc:
        raise PrivateGoldStagingError(str(exc)) from exc


def _positive_quantity(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise PrivateGoldStagingError("private_gold_staging_quantity_invalid")
    try:
        quantity = int(value)
    except (TypeError, ValueError) as exc:
        raise PrivateGoldStagingError("private_gold_staging_quantity_invalid") from exc
    if quantity <= 0:
        raise PrivateGoldStagingError("private_gold_staging_quantity_invalid")
    return quantity


def _status(value: object) -> str:
    normalized = str(value or "PENDING").strip().upper()
    normalized = _TRADE_STATUS_ALIASES.get(normalized, normalized)
    if normalized not in _TRADE_STATUSES:
        raise PrivateGoldStagingError("private_gold_staging_trade_status_invalid")
    return normalized


def _digest(*values: object) -> bytes:
    # BLAKE2 ``person`` is deliberately limited to sixteen bytes.
    digest = blake2b(digest_size=32, person=b"priv-gold-stg1")
    for value in values:
        encoded = str(value or "").encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.digest()


def _expiry(*times: str | None) -> str:
    available = [
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        for value in times
        if value is not None
    ]
    if not available:
        raise PrivateGoldStagingError("private_gold_staging_expiry_unavailable")
    return (max(available) + PRIVATE_GOLD_STAGING_RETENTION).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _row(connection: sqlite3.Connection, message_id: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM private_gold_staged_offers WHERE source_message_id = ?", (message_id,)
    ).fetchone()


def _write(
    connection: sqlite3.Connection,
    *,
    message_id: str,
    event_time: str | None,
    offer_available: str | None,
    text: str | None,
    offer_edited: str | None,
    offer_digest: bytes | None,
    trade_status: str,
    traded_quantity: int | None,
    trade_detected: str | None,
    trade_edited: str | None,
    trade_available: str | None,
    trade_digest: bytes | None,
    existing: sqlite3.Row | None,
    staged_at: str,
) -> None:
    revision = int(existing["revision"]) + 1 if existing is not None else 1
    connection.execute(
        """
        INSERT INTO private_gold_staged_offers(
            source_message_id, event_time_utc, offer_available_at_utc, offer_text,
            offer_edited_at_utc, offer_digest, trade_status, traded_quantity,
            trade_detected_at_utc, trade_edited_at_utc, trade_available_at_utc,
            trade_digest, revision, first_staged_at_utc, last_staged_at_utc,
            expires_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_message_id) DO UPDATE SET
            event_time_utc = excluded.event_time_utc,
            offer_available_at_utc = excluded.offer_available_at_utc,
            offer_text = excluded.offer_text,
            offer_edited_at_utc = excluded.offer_edited_at_utc,
            offer_digest = excluded.offer_digest,
            trade_status = excluded.trade_status,
            traded_quantity = excluded.traded_quantity,
            trade_detected_at_utc = excluded.trade_detected_at_utc,
            trade_edited_at_utc = excluded.trade_edited_at_utc,
            trade_available_at_utc = excluded.trade_available_at_utc,
            trade_digest = excluded.trade_digest,
            revision = excluded.revision,
            last_staged_at_utc = excluded.last_staged_at_utc,
            expires_at_utc = excluded.expires_at_utc
        """,
        (
            message_id, event_time, offer_available, text, offer_edited, offer_digest,
            trade_status, traded_quantity, trade_detected, trade_edited, trade_available,
            trade_digest, revision,
            str(existing["first_staged_at_utc"]) if existing is not None else staged_at,
            staged_at, _expiry(offer_available, trade_available),
        ),
    )


def stage_private_gold_offer(
    connection: sqlite3.Connection,
    offer: PrivateGoldStagingOffer,
    *,
    staged_at_utc: datetime | str | None = None,
) -> bool:
    """Store one current offer text, retaining an earlier/later verifier update."""

    message_id = _message_id(offer.source_message_id)
    event_time = _utc(offer.event_time_utc, name="private_gold_offer_event_time_utc", required=True)
    available = _utc(offer.available_at_utc, name="private_gold_offer_available_at_utc", required=True)
    edited = _utc(offer.edited_at_utc, name="private_gold_offer_edited_at_utc", required=False)
    assert event_time is not None and available is not None
    if available < event_time or (edited is not None and edited < event_time):
        raise PrivateGoldStagingError("private_gold_staging_timestamp_order_invalid")
    text = str(offer.text or "").strip()
    if not text or len(text.encode("utf-8")) > _MAX_TEXT_BYTES:
        raise PrivateGoldStagingError("private_gold_staging_text_invalid")
    offer_digest = _digest(event_time, available, edited, text)
    existing = _row(connection, message_id)
    if existing is not None and bytes(existing["offer_digest"] or b"") == offer_digest:
        return False
    if existing is not None and existing["offer_text"] is not None:
        previous_edit = existing["offer_edited_at_utc"]
        if edited is None or (previous_edit is not None and edited <= str(previous_edit)):
            return False
    staged_at = _utc(staged_at_utc or _utc_now(), name="private_gold_staged_at_utc", required=True)
    assert staged_at is not None
    existing_trade_available = (
        str(existing["trade_available_at_utc"])
        if existing is not None and existing["trade_available_at_utc"] is not None
        else None
    )
    # This source's documented convention treats an edit as completion
    # evidence.  A later verifier update still wins, but an edited offer must
    # not lose its trade simply because the verifier stream was delayed.
    inferred_trade_available = existing_trade_available or (available if edited is not None else None)
    inferred_trade_edited = (
        str(existing["trade_edited_at_utc"])
        if existing is not None and existing["trade_edited_at_utc"] is not None
        else edited
    )
    _write(
        connection,
        message_id=message_id,
        event_time=event_time,
        offer_available=available,
        text=text,
        offer_edited=edited,
        offer_digest=offer_digest,
        trade_status=str(existing["trade_status"]) if existing is not None else "PENDING",
        traded_quantity=(int(existing["traded_quantity"]) if existing is not None and existing["traded_quantity"] is not None else None),
        trade_detected=(str(existing["trade_detected_at_utc"]) if existing is not None and existing["trade_detected_at_utc"] is not None else None),
        trade_edited=inferred_trade_edited,
        trade_available=inferred_trade_available,
        trade_digest=(bytes(existing["trade_digest"]) if existing is not None and existing["trade_digest"] is not None else None),
        existing=existing,
        staged_at=staged_at,
    )
    return True


def stage_private_gold_trade_update(
    connection: sqlite3.Connection,
    update: PrivateGoldStagingTradeUpdate,
    *,
    staged_at_utc: datetime | str | None = None,
) -> bool:
    """Store a verifier update even when its textual offer has not arrived yet."""

    message_id = _message_id(update.source_message_id)
    available = _utc(update.available_at_utc, name="private_gold_trade_available_at_utc", required=True)
    detected = _utc(update.trade_detected_at_utc, name="private_gold_trade_detected_at_utc", required=False)
    edited = _utc(update.telegram_edit_datetime, name="private_gold_trade_edited_at_utc", required=False)
    assert available is not None
    if (detected is not None and available < detected) or (edited is not None and available < edited):
        raise PrivateGoldStagingError("private_gold_staging_timestamp_order_invalid")
    status = _status(update.trade_status)
    quantity = _positive_quantity(update.traded_quantity)
    digest = _digest(status, quantity, detected, edited, available)
    existing = _row(connection, message_id)
    if existing is not None and bytes(existing["trade_digest"] or b"") == digest:
        return False
    if existing is not None and existing["trade_available_at_utc"] is not None and available <= str(existing["trade_available_at_utc"]):
        return False
    staged_at = _utc(staged_at_utc or _utc_now(), name="private_gold_staged_at_utc", required=True)
    assert staged_at is not None
    _write(
        connection,
        message_id=message_id,
        event_time=(str(existing["event_time_utc"]) if existing is not None and existing["event_time_utc"] is not None else None),
        offer_available=(str(existing["offer_available_at_utc"]) if existing is not None and existing["offer_available_at_utc"] is not None else None),
        text=(str(existing["offer_text"]) if existing is not None and existing["offer_text"] is not None else None),
        offer_edited=(str(existing["offer_edited_at_utc"]) if existing is not None and existing["offer_edited_at_utc"] is not None else None),
        offer_digest=(bytes(existing["offer_digest"]) if existing is not None and existing["offer_digest"] is not None else None),
        trade_status=status,
        traded_quantity=quantity,
        trade_detected=detected,
        trade_edited=edited,
        trade_available=available,
        trade_digest=digest,
        existing=existing,
        staged_at=staged_at,
    )
    return True


def list_current_private_gold_staging(
    connection: sqlite3.Connection,
    *,
    as_of_utc: datetime | str,
) -> list[StagedPrivateGoldOffer]:
    as_of = _utc(as_of_utc, name="private_gold_staging_as_of_utc", required=True)
    assert as_of is not None
    rows = connection.execute(
        """
        SELECT * FROM private_gold_staged_offers
        WHERE expires_at_utc > ?
          AND (offer_available_at_utc IS NULL OR offer_available_at_utc <= ?)
        ORDER BY COALESCE(event_time_utc, trade_available_at_utc), source_message_id
        """,
        (as_of, as_of),
    ).fetchall()
    return [
        StagedPrivateGoldOffer(
            source_message_id=str(row["source_message_id"]),
            event_time_utc=(str(row["event_time_utc"]) if row["event_time_utc"] is not None else None),
            offer_available_at_utc=(str(row["offer_available_at_utc"]) if row["offer_available_at_utc"] is not None else None),
            text=(str(row["offer_text"]) if row["offer_text"] is not None else None),
            offer_edited_at_utc=(str(row["offer_edited_at_utc"]) if row["offer_edited_at_utc"] is not None else None),
            trade_status=str(row["trade_status"]),
            traded_quantity=(int(row["traded_quantity"]) if row["traded_quantity"] is not None else None),
            trade_detected_at_utc=(str(row["trade_detected_at_utc"]) if row["trade_detected_at_utc"] is not None else None),
            trade_edited_at_utc=(str(row["trade_edited_at_utc"]) if row["trade_edited_at_utc"] is not None else None),
            trade_available_at_utc=(str(row["trade_available_at_utc"]) if row["trade_available_at_utc"] is not None else None),
            revision=int(row["revision"]),
            expires_at_utc=str(row["expires_at_utc"]),
        )
        for row in rows
    ]


def _offer_input(row: StagedPrivateGoldOffer, *, include_trade: bool, available_at_utc: str) -> PrivateGoldOfferInput | None:
    if row.text is None or row.event_time_utc is None:
        return None
    is_no_trade = row.trade_status == "NONE"
    return PrivateGoldOfferInput(
        source_event_id=row.source_message_id,
        published_at_utc=row.event_time_utc,
        available_at_utc=available_at_utc,
        text=row.text,
        edited_at_utc=(row.trade_edited_at_utc if include_trade and not is_no_trade else None),
        trade_detected_at_utc=(row.trade_detected_at_utc if include_trade and not is_no_trade else None),
        trade_status=(row.trade_status if include_trade and not is_no_trade else None),
        traded_quantity=(row.traded_quantity if include_trade and not is_no_trade else None),
    )


def promote_private_gold_staging(
    staging_connection: sqlite3.Connection,
    market_connection: sqlite3.Connection,
    *,
    as_of_utc: datetime | str,
) -> PrivateGoldPromotionReport:
    """Upsert only reconstructed offer/trade facts; caller owns commit/rollback."""

    rows = list_current_private_gold_staging(staging_connection, as_of_utc=as_of_utc)
    offers = trades = incomplete = 0
    for row in rows:
        if row.offer_available_at_utc is None:
            incomplete += 1
            continue
        offer_input = _offer_input(row, include_trade=False, available_at_utc=row.offer_available_at_utc)
        if offer_input is None:
            incomplete += 1
            continue
        offer_observations = private_gold_observations(offer_input)
        offer_rows = [item for item in offer_observations if item.event_type == "OFFER"]
        if not offer_rows:
            incomplete += 1
            continue
        for item in offer_rows:
            upsert_observation(market_connection, item)
            offers += 1
        if row.trade_available_at_utc is None:
            continue
        trade_input = _offer_input(row, include_trade=True, available_at_utc=row.trade_available_at_utc)
        if trade_input is None:
            continue
        for item in private_gold_observations(trade_input):
            if item.event_type == "TRADE":
                upsert_observation(market_connection, item)
                trades += 1
    return PrivateGoldPromotionReport(
        staged_rows_seen=len(rows),
        offer_facts_upserted=offers,
        trade_facts_upserted=trades,
        unparseable_or_incomplete_rows=incomplete,
    )


def purge_expired_private_gold_staging(
    connection: sqlite3.Connection,
    *,
    as_of_utc: datetime | str,
) -> int:
    as_of = _utc(as_of_utc, name="private_gold_purge_as_of_utc", required=True)
    assert as_of is not None
    result = connection.execute(
        "DELETE FROM private_gold_staged_offers WHERE expires_at_utc <= ?", (as_of,)
    )
    return max(0, int(result.rowcount or 0))


__all__ = [
    "PRIVATE_GOLD_STAGING_RETENTION",
    "PRIVATE_GOLD_STAGING_SCHEMA_VERSION",
    "PRIVATE_GOLD_STAGING_VERSION",
    "PrivateGoldPromotionReport",
    "PrivateGoldStagingError",
    "PrivateGoldStagingOffer",
    "PrivateGoldStagingTradeUpdate",
    "StagedPrivateGoldOffer",
    "assert_private_gold_staging_path_outside_repository",
    "connect_private_gold_staging",
    "initialize_private_gold_staging",
    "list_current_private_gold_staging",
    "promote_private_gold_staging",
    "purge_expired_private_gold_staging",
    "stage_private_gold_offer",
    "stage_private_gold_trade_update",
]
