"""Durable, parser-free capture for Wallex USDT and corroborated PAXG quotes.

The public HTTP payload is intentionally reduced at the network boundary.  The
spool contains only the normalized quote, exact observation/receipt times and
non-sensitive provenance required by the model.  A SQLite FULL outbox is
committed before JSONL append/fsync, so restart can replay without loss.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .market_contracts import MarketObservation, derive_event_key, normalize_utc
from .private_pipeline_foundation import atomic_json_write, utc_text


EXTERNAL_CAPTURE_SCHEMA = "external_quote_capture/1.0"
EXTERNAL_QUOTE_SCHEMA = "external_quote_event"
EXTERNAL_QUOTE_SCHEMA_VERSION = "1.0"
EXTERNAL_CAPTURE_VERSION = "external-quote-capture-v1"
WALLEX_DEPTH_URL = "https://api.wallex.ir/v1/depth"
WALLEX_SYMBOL = "USDTTMN"
BINANCE_BOOK_TICKER_URL = (
    "https://data-api.binance.vision/api/v3/ticker/bookTicker"
)
BINANCE_PAXG_SYMBOLS = ("PAXGUSDC", "PAXGUSDT")
RAW_RETENTION = timedelta(days=3)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

SUPPORTED_SOURCES = frozenset(
    {"WALLEX_PUBLIC_API", "BINANCE_PAXG_PUBLIC_API"}
)


class ExternalQuoteCaptureError(RuntimeError):
    """Operator-safe external capture failure."""


def _decimal(value: object, *, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ExternalQuoteCaptureError(f"{field}_invalid")
    cleaned = str(value).strip().replace(",", "").replace("٬", "")
    cleaned = cleaned.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    try:
        parsed = Decimal(cleaned)
    except (InvalidOperation, ValueError) as exc:
        raise ExternalQuoteCaptureError(f"{field}_invalid") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ExternalQuoteCaptureError(f"{field}_invalid")
    return parsed


def decimal_text(value: Decimal) -> str:
    normalized = format(value, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _http_json(url: str, *, params: Mapping[str, str], timeout: float) -> object:
    request = Request(
        f"{url}?{urlencode(params)}",
        headers={
            "Accept": "application/json",
            "User-Agent": "trading-bot-market-pipeline/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ExternalQuoteCaptureError("external_quote_http_failure") from exc
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ExternalQuoteCaptureError("external_quote_response_too_large")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExternalQuoteCaptureError("external_quote_response_invalid") from exc


@dataclass(frozen=True, slots=True)
class Quote:
    source_code: str
    instrument: str
    quote_kind: str
    price_value: str
    price_unit: str
    currency: str
    observed_at_utc: str
    available_at_utc: str
    provenance: Mapping[str, object]


def fetch_wallex_quotes(
    *, timeout: float = 8.0, observed_at: datetime | None = None
) -> tuple[Quote, ...]:
    """Fetch one real depth snapshot and emit BID/ASK/MID in toman."""

    payload = _http_json(
        WALLEX_DEPTH_URL,
        params={"symbol": WALLEX_SYMBOL},
        timeout=timeout,
    )
    if not isinstance(payload, Mapping) or not isinstance(
        payload.get("result"), Mapping
    ):
        raise ExternalQuoteCaptureError("wallex_depth_malformed")
    result = payload["result"]
    assert isinstance(result, Mapping)
    asks = result.get("ask")
    bids = result.get("bid")
    if not isinstance(asks, list) or not isinstance(bids, list) or not asks or not bids:
        raise ExternalQuoteCaptureError("wallex_depth_empty")
    try:
        best_ask = min(
            _decimal(row.get("price"), field="wallex_ask")
            for row in asks
            if isinstance(row, Mapping)
        )
        best_bid = max(
            _decimal(row.get("price"), field="wallex_bid")
            for row in bids
            if isinstance(row, Mapping)
        )
    except ValueError as exc:
        raise ExternalQuoteCaptureError("wallex_depth_empty") from exc
    if best_ask < best_bid:
        raise ExternalQuoteCaptureError("wallex_depth_crossed")
    observed = observed_at or datetime.now(timezone.utc)
    stamp = normalize_utc(observed, field_name="wallex_observed_at_utc")
    available = normalize_utc(
        datetime.now(timezone.utc), field_name="wallex_available_at_utc"
    )
    if available < stamp:
        available = stamp
    common = {
        "source_code": "WALLEX_PUBLIC_API",
        "instrument": "USDT_IRT",
        "price_unit": "TOMAN_PER_USDT",
        "currency": "TOMAN",
        "observed_at_utc": stamp,
        "available_at_utc": available,
        "provenance": {"symbol": WALLEX_SYMBOL, "method": "ORDER_BOOK_TOP"},
    }
    return tuple(
        Quote(
            quote_kind=kind,
            price_value=decimal_text(price),
            **common,
        )
        for kind, price in (
            ("BID", best_bid),
            ("ASK", best_ask),
            ("MID", (best_bid + best_ask) / Decimal("2")),
        )
    )


def _binance_mid(payload: object, *, symbol: str) -> Decimal:
    if not isinstance(payload, Mapping) or str(payload.get("symbol")) != symbol:
        raise ExternalQuoteCaptureError("binance_book_malformed")
    bid = _decimal(payload.get("bidPrice"), field="binance_bid")
    ask = _decimal(payload.get("askPrice"), field="binance_ask")
    if ask < bid:
        raise ExternalQuoteCaptureError("binance_book_crossed")
    midpoint = (bid + ask) / Decimal("2")
    if (ask - bid) / midpoint > Decimal("0.005"):
        raise ExternalQuoteCaptureError("binance_book_spread_wide")
    return midpoint


def fetch_paxg_quote(
    *, timeout: float = 8.0, observed_at: datetime | None = None
) -> tuple[Quote, ...]:
    """Return one two-book corroborated proxy; it is never labelled direct XAU."""

    midpoints = {
        symbol: _binance_mid(
            _http_json(
                BINANCE_BOOK_TICKER_URL,
                params={"symbol": symbol},
                timeout=timeout,
            ),
            symbol=symbol,
        )
        for symbol in BINANCE_PAXG_SYMBOLS
    }
    low = min(midpoints.values())
    high = max(midpoints.values())
    center = sum(midpoints.values(), Decimal("0")) / Decimal(len(midpoints))
    if (high - low) / center > Decimal("0.005"):
        raise ExternalQuoteCaptureError("binance_paxg_books_diverged")
    observed = observed_at or datetime.now(timezone.utc)
    stamp = normalize_utc(observed, field_name="paxg_observed_at_utc")
    available = normalize_utc(
        datetime.now(timezone.utc), field_name="paxg_available_at_utc"
    )
    if available < stamp:
        available = stamp
    return (
        Quote(
            source_code="BINANCE_PAXG_PUBLIC_API",
            instrument="PAXG_USD_PROXY",
            quote_kind="MID",
            price_value=decimal_text(center),
            price_unit="USD_PER_TROY_OUNCE",
            currency="USD",
            observed_at_utc=stamp,
            available_at_utc=available,
            provenance={
                "symbols": list(BINANCE_PAXG_SYMBOLS),
                "method": "TWO_BOOK_MIDPOINT_CORROBORATION",
                "maximum_spread_ratio": "0.005",
                "maximum_book_divergence_ratio": "0.005",
            },
        ),
    )


def quote_event(quote: Quote) -> dict[str, Any]:
    if quote.source_code not in SUPPORTED_SOURCES:
        raise ExternalQuoteCaptureError("external_quote_source_unsupported")
    identity = "|".join(
        (
            quote.source_code,
            quote.instrument,
            quote.quote_kind,
            quote.observed_at_utc,
            quote.price_value,
        )
    )
    event_id = sha256(identity.encode("utf-8")).hexdigest()
    return {
        "schema": EXTERNAL_QUOTE_SCHEMA,
        "schema_version": EXTERNAL_QUOTE_SCHEMA_VERSION,
        "event_id": event_id,
        "event_type": "quote_observed",
        "source": {"source_id": quote.source_code},
        "quote": {
            "instrument": quote.instrument,
            "quote_kind": quote.quote_kind,
            "price_value": quote.price_value,
            "price_unit": quote.price_unit,
            "currency": quote.currency,
            "observed_at_utc": quote.observed_at_utc,
            "provenance": dict(quote.provenance),
        },
        "producer": {"available_at_utc": quote.available_at_utc},
    }


def decode_quote_event(document: object) -> tuple[str, MarketObservation]:
    """Validate one minimized spool event and map it to the canonical Store."""

    if not isinstance(document, Mapping):
        raise ExternalQuoteCaptureError("external_quote_event_object_required")
    if (
        document.get("schema") != EXTERNAL_QUOTE_SCHEMA
        or document.get("schema_version") != EXTERNAL_QUOTE_SCHEMA_VERSION
        or document.get("event_type") != "quote_observed"
    ):
        raise ExternalQuoteCaptureError("external_quote_contract_invalid")
    event_id = str(document.get("event_id") or "")
    if len(event_id) != 64 or any(ch not in "0123456789abcdef" for ch in event_id):
        raise ExternalQuoteCaptureError("external_quote_event_id_invalid")
    source = document.get("source")
    quote = document.get("quote")
    producer = document.get("producer")
    if not all(isinstance(value, Mapping) for value in (source, quote, producer)):
        raise ExternalQuoteCaptureError("external_quote_contract_invalid")
    assert isinstance(source, Mapping)
    assert isinstance(quote, Mapping)
    assert isinstance(producer, Mapping)
    source_code = str(source.get("source_id") or "")
    instrument = str(quote.get("instrument") or "")
    quote_kind = str(quote.get("quote_kind") or "")
    expected = {
        "WALLEX_PUBLIC_API": (
            "USDT_IRT",
            {"BID", "ASK", "MID"},
            "TOMAN_PER_USDT",
            "TOMAN",
        ),
        "BINANCE_PAXG_PUBLIC_API": (
            "PAXG_USD_PROXY",
            {"MID"},
            "USD_PER_TROY_OUNCE",
            "USD",
        ),
    }.get(source_code)
    if expected is None or (
        instrument,
        quote.get("price_unit"),
        quote.get("currency"),
    ) != (expected[0], expected[2], expected[3]) or quote_kind not in expected[1]:
        raise ExternalQuoteCaptureError("external_quote_dimensions_invalid")
    observed = normalize_utc(
        str(quote.get("observed_at_utc") or ""),
        field_name="external_quote_observed_at_utc",
    )
    available = normalize_utc(
        str(producer.get("available_at_utc") or ""),
        field_name="external_quote_available_at_utc",
    )
    if available < observed:
        raise ExternalQuoteCaptureError("external_quote_availability_invalid")
    price = decimal_text(_decimal(quote.get("price_value"), field="external_price"))
    provenance = quote.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ExternalQuoteCaptureError("external_quote_provenance_invalid")
    observation = MarketObservation(
        event_key=derive_event_key("external-quote-v1", event_id),
        source_code=source_code,
        source_family="EXTERNAL_MARKET",
        event_time_utc=observed,
        available_at_utc=available,
        instrument=instrument,
        market_label="EXTERNAL_REFERENCE",
        settlement_term="SPOT",
        trade_form="NOT_APPLICABLE",
        event_type="REFERENCE",
        side={"BID": "BUY", "ASK": "SELL"}.get(quote_kind, "MID"),
        price=price,
        price_unit=str(quote["price_unit"]),
        currency=str(quote["currency"]),
        parser_version=EXTERNAL_CAPTURE_VERSION,
        quality_state="ELIGIBLE",
        quality_policy_version="external-quote-v1",
        attributes={"quote_kind": quote_kind, **dict(provenance)},
    )
    return event_id, observation


class DurableExternalQuoteSpool:
    """Single-writer FULL outbox and fsynced JSONL spool."""

    def __init__(self, state_path: Path, spool_directory: Path) -> None:
        state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        spool_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.spool_directory = spool_directory
        self.connection = sqlite3.connect(state_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=DELETE")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS external_capture_metadata(
              singleton INTEGER PRIMARY KEY CHECK(singleton=1),
              schema_version INTEGER NOT NULL,
              sequence INTEGER NOT NULL CHECK(sequence>=0)
            );
            INSERT OR IGNORE INTO external_capture_metadata VALUES(1,1,0);
            CREATE TABLE IF NOT EXISTS external_capture_outbox(
              event_id TEXT PRIMARY KEY CHECK(length(event_id)=64),
              sequence INTEGER NOT NULL UNIQUE CHECK(sequence>0),
              source_code TEXT NOT NULL,
              available_at_utc TEXT NOT NULL,
              payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS external_capture_seen(
              event_id TEXT PRIMARY KEY CHECK(length(event_id)=64),
              sequence INTEGER NOT NULL UNIQUE CHECK(sequence>0),
              expires_at_utc TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS external_capture_seen_expiry
              ON external_capture_seen(expires_at_utc);
            """
        )
        self.connection.commit()
        os.chmod(state_path, 0o600)

    def close(self) -> None:
        self.connection.close()

    def stage(self, document: Mapping[str, Any]) -> str:
        event_id, _ = decode_quote_event(document)
        if self.connection.execute(
            "SELECT 1 FROM external_capture_seen WHERE event_id=?", (event_id,)
        ).fetchone():
            return "duplicate"
        if self.connection.execute(
            "SELECT 1 FROM external_capture_outbox WHERE event_id=?", (event_id,)
        ).fetchone():
            return "pending"
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            sequence = int(
                self.connection.execute(
                    "SELECT sequence FROM external_capture_metadata WHERE singleton=1"
                ).fetchone()[0]
            ) + 1
            payload = json.loads(json.dumps(document, sort_keys=True))
            payload["producer"]["capture_sequence"] = sequence
            self.connection.execute(
                "INSERT INTO external_capture_outbox VALUES(?,?,?,?,?)",
                (
                    event_id,
                    sequence,
                    str(payload["source"]["source_id"]),
                    str(payload["producer"]["available_at_utc"]),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                ),
            )
            self.connection.execute(
                "UPDATE external_capture_metadata SET sequence=? WHERE singleton=1",
                (sequence,),
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return "staged"

    def drain(self) -> int:
        rows = self.connection.execute(
            "SELECT * FROM external_capture_outbox ORDER BY sequence"
        ).fetchall()
        delivered = 0
        for row in rows:
            available = datetime.fromisoformat(
                str(row["available_at_utc"]).replace("Z", "+00:00")
            )
            target = self.spool_directory / f"events-{available.date().isoformat()}.jsonl"
            descriptor = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                payload = (str(row["payload_json"]) + "\n").encode("utf-8")
                view = memoryview(payload)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("external_spool_short_write")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self.connection.execute(
                    "INSERT OR IGNORE INTO external_capture_seen VALUES(?,?,?)",
                    (
                        str(row["event_id"]),
                        int(row["sequence"]),
                        normalize_utc(
                            available + RAW_RETENTION,
                            field_name="external_quote_retention_utc",
                        ),
                    ),
                )
                self.connection.execute(
                    "DELETE FROM external_capture_outbox WHERE event_id=?",
                    (str(row["event_id"]),),
                )
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise
            delivered += 1
        if rows:
            directory = os.open(self.spool_directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return delivered

    def purge(self, *, now: datetime | None = None) -> int:
        cutoff = normalize_utc(
            now or datetime.now(timezone.utc), field_name="external_purge_utc"
        )
        result = self.connection.execute(
            "DELETE FROM external_capture_seen WHERE expires_at_utc<=?", (cutoff,)
        )
        self.connection.commit()
        return max(0, int(result.rowcount or 0))


def run_external_capture_service(
    *,
    role: str,
    mode: str,
    release_sha: str,
    state_directory: Path,
    stop: Any,
    wallex_loader: Callable[[], tuple[Quote, ...]] = fetch_wallex_quotes,
    paxg_loader: Callable[[], tuple[Quote, ...]] = fetch_paxg_quote,
) -> int:
    if role != "market-capture-external" or mode not in {"fixture", "live"}:
        raise ExternalQuoteCaptureError("external_capture_role_or_mode_invalid")
    try:
        interval = float(os.environ.get("MARKET_EXTERNAL_POLL_SECONDS", "10"))
    except ValueError as exc:
        raise ExternalQuoteCaptureError("external_capture_interval_invalid") from exc
    if not 5 <= interval <= 60:
        raise ExternalQuoteCaptureError("external_capture_interval_invalid")
    spool = Path(
        os.environ.get(
            "MARKET_EXTERNAL_CAPTURE_ROOT", "/var/lib/market-data/capture/external"
        )
    ).resolve()
    store = DurableExternalQuoteSpool(
        state_directory / "external-capture.sqlite3", spool
    )
    started = utc_text()
    counters = {source: {"success": 0, "failure": 0, "last_available_at_utc": None} for source in SUPPORTED_SOURCES}

    def write_health(status: str) -> None:
        atomic_json_write(
            state_directory / "health.json",
            {
                "schema": EXTERNAL_CAPTURE_SCHEMA,
                "version": EXTERNAL_CAPTURE_VERSION,
                "role": role,
                "mode": mode,
                "release_sha": release_sha,
                "pid": os.getpid(),
                "started_at_utc": started,
                "updated_at_utc": utc_text(),
                "status": status,
                "poll_interval_seconds": interval,
                "sources": counters,
                "outbox_depth": int(
                    store.connection.execute(
                        "SELECT COUNT(*) FROM external_capture_outbox"
                    ).fetchone()[0]
                ),
            },
        )

    def poll_once() -> None:
        for source, loader in (
            ("WALLEX_PUBLIC_API", wallex_loader),
            ("BINANCE_PAXG_PUBLIC_API", paxg_loader),
        ):
            try:
                quotes = loader()
                if not quotes:
                    raise ExternalQuoteCaptureError("external_quote_empty")
                for quote in quotes:
                    store.stage(quote_event(quote))
                store.drain()
                counters[source]["success"] = int(counters[source]["success"]) + 1
                counters[source]["last_available_at_utc"] = max(
                    quote.available_at_utc for quote in quotes
                )
            except (ExternalQuoteCaptureError, OSError, sqlite3.Error):
                counters[source]["failure"] = int(counters[source]["failure"]) + 1
        store.purge()
        write_health(f"{mode}-ready")

    try:
        store.drain()
        if mode == "fixture":
            write_health("fixture-ready")
            if os.environ.get("MARKET_EXTERNAL_CAPTURE_FIXTURE_POLL", "").lower() in {
                "1", "true", "yes", "on"
            }:
                poll_once()
            if os.environ.get("MARKET_EXTERNAL_CAPTURE_ONESHOT", "").lower() in {
                "1", "true", "yes", "on"
            }:
                return 0
            while not stop.wait(1.0):
                write_health("fixture-ready")
            write_health("fixture-stopped")
            return 0
        while not stop.is_set():
            started_poll = time.monotonic()
            poll_once()
            delay = max(0.1, interval - (time.monotonic() - started_poll))
            if stop.wait(delay):
                break
        write_health("live-stopped")
        return 0
    finally:
        store.close()


__all__ = [
    "DurableExternalQuoteSpool",
    "EXTERNAL_CAPTURE_SCHEMA",
    "EXTERNAL_CAPTURE_VERSION",
    "ExternalQuoteCaptureError",
    "Quote",
    "decode_quote_event",
    "fetch_paxg_quote",
    "fetch_wallex_quotes",
    "quote_event",
    "run_external_capture_service",
]
