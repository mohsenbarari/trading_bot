"""Point-in-time, atomic market-state snapshots for product inference.

Snapshots are deliberately source-separated.  They describe the market facts
available at one instant; they do not manufacture a missing Herat price from
USDT, merge paper and physical melted gold, or select a commodity.  Those
policy decisions belong to the later anchor/range producer.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from .coin_rate_engine import COIN_RATE_ENGINE_VERSION, COIN_SPECS, build_coin_rate_estimates
from .market_contracts import MARKET_STORE_CONTRACT_VERSION, normalize_utc


MARKET_SNAPSHOT_SCHEMA_VERSION = 1
MARKET_SNAPSHOT_BUILDER_VERSION = "market-snapshot-v1"
DEFAULT_MAXIMUM_SNAPSHOT_BYTES = 2 * 1024 * 1024


class MarketSnapshotError(RuntimeError):
    """Raised when a snapshot cannot be safely built, published, or read."""


class MarketSnapshotUnavailable(MarketSnapshotError):
    """Raised when no validated, atomically-read snapshot exists."""


def _utc(value: datetime | str) -> datetime:
    serialized = normalize_utc(value, field_name="snapshot_time_utc")
    return datetime.fromisoformat(serialized.replace("Z", "+00:00"))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    length = len(ordered)
    if not length:
        raise MarketSnapshotError("snapshot_median_empty")
    midpoint = length // 2
    if length % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _weighted_median(values: Iterable[tuple[float, int]]) -> float:
    normalized = sorted((float(price), max(1, int(weight))) for price, weight in values)
    total = sum(weight for _, weight in normalized)
    if total <= 0:
        raise MarketSnapshotError("snapshot_weighted_median_empty")
    threshold = (total + 1) / 2.0
    cumulative = 0
    for price, weight in normalized:
        cumulative += weight
        if cumulative >= threshold:
            return price
    return normalized[-1][0]


def _event_weight(event_type: str) -> int:
    # This is an evidence summary only.  The later rate policy owns final
    # weights, but completed trades must remain distinguishable from offers.
    return {"TRADE": 3, "OFFER": 1, "QUOTE": 1, "REFERENCE": 1}.get(
        str(event_type).upper(),
        1,
    )


def _finite_number(value: object) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketSnapshotError("snapshot_number_invalid") from exc
    if not math.isfinite(normalized):
        raise MarketSnapshotError("snapshot_number_non_finite")
    return normalized


def _no_private_keys(value: object) -> None:
    forbidden = (
        "raw",
        "text",
        "message",
        "telegram",
        "channel",
        "chat",
        "sender",
        "username",
        "phone",
        "link",
    )
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if any(token in normalized for token in forbidden):
                raise MarketSnapshotError("snapshot_private_field_forbidden")
            _no_private_keys(item)
    elif isinstance(value, list):
        for item in value:
            _no_private_keys(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise MarketSnapshotError("snapshot_number_non_finite")


def _read_fact_rows(
    connection: sqlite3.Connection,
    *,
    as_of: datetime,
    instrument: str,
    settlement_term: str | None = None,
    trade_form: str | None = None,
    market_label: str | None = None,
    source_codes: Sequence[str] | None = None,
    include_conditional: bool = False,
    limit: int = 250,
) -> list[sqlite3.Row]:
    """Read only data known no later than ``as_of`` (no future leakage)."""

    clauses = [
        "instrument = ?",
        "quality_state = 'ELIGIBLE'",
        "event_time_utc <= ?",
        "available_at_utc <= ?",
    ]
    parameters: list[object] = [instrument, _iso(as_of), _iso(as_of)]
    if settlement_term is not None:
        clauses.append("settlement_term = ?")
        parameters.append(settlement_term)
    if trade_form is not None:
        clauses.append("trade_form = ?")
        parameters.append(trade_form)
    if market_label is not None:
        clauses.append("market_label = ?")
        parameters.append(market_label)
    if not include_conditional:
        clauses.append("is_conditional = 0")
    if source_codes:
        placeholders = ", ".join("?" for _ in source_codes)
        clauses.append(f"source_code IN ({placeholders})")
        parameters.extend(source_codes)
    parameters.append(max(1, int(limit)))
    return list(
        connection.execute(
            f"""
            SELECT source_code, event_time_utc, available_at_utc, event_type,
                   side, price_num, price_unit, settlement_term, trade_form
            FROM market_observations
            WHERE {' AND '.join(clauses)}
            ORDER BY event_time_utc DESC, id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
    )


def _source_summary(
    rows: Sequence[sqlite3.Row],
    *,
    as_of: datetime,
    freshness_seconds: int,
    aggregation_seconds: int,
    expected_unit: str,
    method: str,
) -> dict[str, Any]:
    if not rows:
        return {
            "status": "MISSING",
            "price_unit": expected_unit,
            "last_event_utc": None,
            "age_seconds": None,
            "observation_count": 0,
            "source_codes": [],
            "method": method,
        }
    newest = _utc(str(rows[0]["event_time_utc"]))
    age_seconds = max(0.0, (as_of - newest).total_seconds())
    status = "FRESH" if age_seconds <= freshness_seconds else "STALE"
    window_start = newest - timedelta(seconds=max(1, aggregation_seconds))
    window = [
        row
        for row in rows
        if _utc(str(row["event_time_utc"])) >= window_start
        and str(row["price_unit"]) == expected_unit
    ]
    if not window:
        raise MarketSnapshotError("snapshot_source_unit_mismatch")
    prices = [_finite_number(row["price_num"]) for row in window]
    event_counts = Counter(str(row["event_type"]).upper() for row in window)
    side_counts = Counter(str(row["side"]).upper() for row in window)
    weighted = _weighted_median(
        (_finite_number(row["price_num"]), _event_weight(str(row["event_type"])))
        for row in window
    )
    return {
        "status": status,
        "price_unit": expected_unit,
        "last_event_utc": _iso(newest),
        "age_seconds": round(age_seconds, 3),
        "observation_count": len(window),
        "source_codes": sorted({str(row["source_code"]) for row in window}),
        "event_counts": dict(sorted(event_counts.items())),
        "side_counts": dict(sorted(side_counts.items())),
        "latest_price": _finite_number(rows[0]["price_num"]),
        "weighted_median_price": weighted,
        "median_price": _median(prices),
        "minimum_price": min(prices),
        "maximum_price": max(prices),
        "method": method,
    }


def _signal(
    connection: sqlite3.Connection,
    *,
    as_of: datetime,
    key: str,
    instrument: str,
    expected_unit: str,
    freshness_seconds: int,
    aggregation_seconds: int,
    settlement_term: str | None = None,
    trade_form: str | None = None,
    market_label: str | None = None,
    source_codes: Sequence[str] | None = None,
    method: str,
) -> tuple[str, dict[str, Any]]:
    return (
        key,
        _source_summary(
            _read_fact_rows(
                connection,
                as_of=as_of,
                instrument=instrument,
                settlement_term=settlement_term,
                trade_form=trade_form,
                market_label=market_label,
                source_codes=source_codes,
            ),
            as_of=as_of,
            freshness_seconds=freshness_seconds,
            aggregation_seconds=aggregation_seconds,
            expected_unit=expected_unit,
            method=method,
        ),
    )


def _regime_from_signals(signals: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """A conservative, non-authoritative underlying direction summary.

    It intentionally abstains until two independent fresh series agree.  This
    keeps a thin or stale source from turning into a false market regime.
    """

    usable: list[tuple[str, float]] = []
    for name in ("MELTED_PAPER_TOMORROW", "USD_HERAT_TOMORROW", "USDT_IRT"):
        signal = signals.get(name) or {}
        if signal.get("status") != "FRESH":
            continue
        latest = signal.get("latest_price")
        median = signal.get("median_price")
        if not isinstance(latest, (int, float)) or not isinstance(median, (int, float)):
            continue
        if median <= 0:
            continue
        usable.append((name, (float(latest) / float(median)) - 1.0))
    if len(usable) < 2:
        return {
            "status": "ABSTAIN",
            "reason": "INSUFFICIENT_INDEPENDENT_FRESH_SIGNALS",
            "inputs": [name for name, _ in usable],
        }
    positives = sum(1 for _, change in usable if change >= 0.001)
    negatives = sum(1 for _, change in usable if change <= -0.001)
    magnitude = _median([abs(change) for _, change in usable])
    if positives and negatives:
        label = "VOLATILE"
    elif positives >= 2:
        label = "UP"
    elif negatives >= 2:
        label = "DOWN"
    else:
        label = "NORMAL"
    return {
        "status": "OBSERVED",
        "label": label,
        "median_window_return": magnitude,
        "inputs": [name for name, _ in usable],
        "method": "independent_underlying_window_direction_v1",
    }


def build_market_snapshot(
    connection: sqlite3.Connection,
    *,
    as_of_utc: datetime | str,
) -> dict[str, Any]:
    """Build a privacy-minimized, point-in-time market snapshot in memory."""

    as_of = _utc(as_of_utc)
    signals = dict(
        (
            _signal(
                connection,
                as_of=as_of,
                # P2-A can prove PHYSICAL only from an explicit cash/formal
                # marker but it cannot infer TODAY versus TOMORROW when the
                # message does not say so.  Preserve UNKNOWN rather than
                # silently calling this a CASH observation.
                key="MELTED_PHYSICAL_UNSPECIFIED",
                instrument="MELTED_GOLD_AGGREGATE",
                settlement_term="UNKNOWN",
                trade_form="PHYSICAL",
                expected_unit="IRT_PER_MESGHAL_750",
                freshness_seconds=900,
                aggregation_seconds=60,
                method="physical_events_preserved_weighted_summary_v1",
            ),
            _signal(
                connection,
                as_of=as_of,
                key="MELTED_PAPER_TODAY",
                instrument="MELTED_GOLD_FLOW",
                settlement_term="TODAY",
                trade_form="PAPER_NORMAL",
                expected_unit="IRT_PER_MESGHAL_750",
                freshness_seconds=900,
                aggregation_seconds=60,
                method="paper_trade_weighted_minute_summary_v1",
            ),
            _signal(
                connection,
                as_of=as_of,
                key="MELTED_PAPER_TOMORROW",
                instrument="MELTED_GOLD_FLOW",
                settlement_term="TOMORROW",
                trade_form="PAPER_NORMAL",
                expected_unit="IRT_PER_MESGHAL_750",
                freshness_seconds=900,
                aggregation_seconds=60,
                method="paper_trade_weighted_minute_summary_v1",
            ),
            _signal(
                connection,
                as_of=as_of,
                key="PRIVATE_GOLD_PHYSICAL_TODAY",
                instrument="MELTED_GOLD_PRIVATE",
                settlement_term="TODAY",
                trade_form="PHYSICAL",
                source_codes=("PRIVATE_GOLD_CHANNEL",),
                expected_unit="IRT_PER_MESGHAL_750",
                freshness_seconds=900,
                aggregation_seconds=60,
                method="private_physical_individual_events_v1",
            ),
            _signal(
                connection,
                as_of=as_of,
                key="PRIVATE_GOLD_PHYSICAL_TOMORROW",
                instrument="MELTED_GOLD_PRIVATE",
                settlement_term="TOMORROW",
                trade_form="PHYSICAL",
                source_codes=("PRIVATE_GOLD_CHANNEL",),
                expected_unit="IRT_PER_MESGHAL_750",
                freshness_seconds=900,
                aggregation_seconds=60,
                method="private_physical_individual_events_v1",
            ),
            _signal(
                connection,
                as_of=as_of,
                key="PRIVATE_GOLD_PAPER_NORMAL_TODAY",
                instrument="MELTED_GOLD_PRIVATE",
                market_label="PRIVATE_GOLD_PAPER_NORMAL",
                settlement_term="TODAY",
                trade_form="PAPER_NORMAL",
                source_codes=("PRIVATE_GOLD_PAPER_MINUTE",),
                expected_unit="IRT_PER_MESGHAL_750",
                freshness_seconds=900,
                aggregation_seconds=60,
                method="private_paper_trade_weighted_minute_v1",
            ),
            _signal(
                connection,
                as_of=as_of,
                key="PRIVATE_GOLD_PAPER_NORMAL_TOMORROW",
                instrument="MELTED_GOLD_PRIVATE",
                market_label="PRIVATE_GOLD_PAPER_NORMAL",
                settlement_term="TOMORROW",
                trade_form="PAPER_NORMAL",
                source_codes=("PRIVATE_GOLD_PAPER_MINUTE",),
                expected_unit="IRT_PER_MESGHAL_750",
                freshness_seconds=900,
                aggregation_seconds=60,
                method="private_paper_trade_weighted_minute_v1",
            ),
            _signal(
                connection,
                as_of=as_of,
                key="PRIVATE_GOLD_PAPER_REVERSE_TODAY",
                instrument="MELTED_GOLD_PRIVATE",
                market_label="PRIVATE_GOLD_PAPER_REVERSE",
                settlement_term="TODAY",
                trade_form="PAPER_REVERSE",
                source_codes=("PRIVATE_GOLD_PAPER_MINUTE",),
                expected_unit="IRT_PER_MESGHAL_750",
                freshness_seconds=900,
                aggregation_seconds=60,
                method="private_paper_trade_weighted_minute_v1",
            ),
            _signal(
                connection,
                as_of=as_of,
                key="PRIVATE_GOLD_PAPER_REVERSE_TOMORROW",
                instrument="MELTED_GOLD_PRIVATE",
                market_label="PRIVATE_GOLD_PAPER_REVERSE",
                settlement_term="TOMORROW",
                trade_form="PAPER_REVERSE",
                source_codes=("PRIVATE_GOLD_PAPER_MINUTE",),
                expected_unit="IRT_PER_MESGHAL_750",
                freshness_seconds=900,
                aggregation_seconds=60,
                method="private_paper_trade_weighted_minute_v1",
            ),
            _signal(
                connection,
                as_of=as_of,
                key="PRIVATE_GOLD_PAPER_SWIM_TODAY",
                instrument="MELTED_GOLD_PRIVATE",
                market_label="PRIVATE_GOLD_PAPER_SWIM",
                settlement_term="TODAY",
                trade_form="PAPER_SWIM",
                source_codes=("PRIVATE_GOLD_PAPER_MINUTE",),
                expected_unit="IRT_PER_MESGHAL_750",
                freshness_seconds=900,
                aggregation_seconds=60,
                method="private_paper_trade_weighted_minute_v1",
            ),
            _signal(
                connection,
                as_of=as_of,
                key="PRIVATE_GOLD_PAPER_SWIM_TOMORROW",
                instrument="MELTED_GOLD_PRIVATE",
                market_label="PRIVATE_GOLD_PAPER_SWIM",
                settlement_term="TOMORROW",
                trade_form="PAPER_SWIM",
                source_codes=("PRIVATE_GOLD_PAPER_MINUTE",),
                expected_unit="IRT_PER_MESGHAL_750",
                freshness_seconds=900,
                aggregation_seconds=60,
                method="private_paper_trade_weighted_minute_v1",
            ),
            _signal(
                connection,
                as_of=as_of,
                key="USD_HERAT_CASH",
                instrument="USD_HERAT",
                settlement_term="UNKNOWN",
                trade_form="PHYSICAL",
                expected_unit="IRT_PER_USD",
                freshness_seconds=900,
                aggregation_seconds=60,
                method="source_separated_latest_window_v1",
            ),
            _signal(
                connection,
                as_of=as_of,
                key="USD_HERAT_TODAY",
                instrument="USD_HERAT",
                settlement_term="TODAY",
                trade_form="PAPER_NORMAL",
                expected_unit="IRT_PER_USD",
                freshness_seconds=900,
                aggregation_seconds=60,
                method="source_separated_latest_window_v1",
            ),
            _signal(
                connection,
                as_of=as_of,
                key="USD_HERAT_TOMORROW",
                instrument="USD_HERAT",
                settlement_term="TOMORROW",
                trade_form="PAPER_NORMAL",
                expected_unit="IRT_PER_USD",
                freshness_seconds=900,
                aggregation_seconds=60,
                method="source_separated_latest_window_v1",
            ),
            _signal(
                connection,
                as_of=as_of,
                key="USDT_IRT",
                instrument="USDT_IRT",
                expected_unit="IRT_PER_USDT",
                freshness_seconds=900,
                aggregation_seconds=60,
                method="external_reference_not_herat_substitution_v1",
            ),
            _signal(
                connection,
                as_of=as_of,
                key="IME_GOLD_BAR",
                instrument="IME_GOLD_BAR",
                settlement_term="SPOT",
                trade_form="NOT_APPLICABLE",
                expected_unit="IRT_PER_MESGHAL_750",
                freshness_seconds=1800,
                aggregation_seconds=60,
                method="official_ime_common_unit_reference_v1",
            ),
            _signal(
                connection,
                as_of=as_of,
                key="IME_GOLD_COIN_IMAM",
                instrument="IME_GOLD_COIN_IMAM",
                settlement_term="SPOT",
                trade_form="NOT_APPLICABLE",
                expected_unit="IRT_PER_COIN",
                freshness_seconds=1800,
                aggregation_seconds=60,
                method="official_ime_coin_reference_v1",
            ),
            _signal(
                connection,
                as_of=as_of,
                key="XAUUSD",
                instrument="XAUUSD",
                settlement_term="SPOT",
                trade_form="NOT_APPLICABLE",
                expected_unit="USD_PER_TROY_OUNCE",
                freshness_seconds=3600,
                aggregation_seconds=60,
                method="external_spot_latest_minute_v1",
            ),
        )
    )
    rate_items = [item.to_dict() for item in build_coin_rate_estimates(connection, as_of_utc=as_of)]
    snapshot = {
        "schema_version": MARKET_SNAPSHOT_SCHEMA_VERSION,
        "market_store_contract_version": MARKET_STORE_CONTRACT_VERSION,
        "builder_version": MARKET_SNAPSHOT_BUILDER_VERSION,
        "generated_at_utc": _iso(as_of),
        "signals": signals,
        "market_regime": _regime_from_signals(signals),
        # Range generation is deterministic and source-separated.  Product
        # commodity selection remains deliberately deferred to P5.
        "rates": {
            "engine_version": COIN_RATE_ENGINE_VERSION,
            "items": rate_items,
            "estimated_count": sum(item["status"] == "ESTIMATED" for item in rate_items),
            "no_data_count": sum(item["status"] == "NO_DATA" for item in rate_items),
        },
        "snapshot_status": "PARTIAL_COIN_RATE_STATE",
    }
    validate_market_snapshot(snapshot)
    return snapshot


def _canonical_snapshot_bytes(snapshot: Mapping[str, Any]) -> bytes:
    try:
        serialized = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise MarketSnapshotError("snapshot_json_serialization_failed") from exc
    encoded = serialized.encode("utf-8")
    if not encoded:
        raise MarketSnapshotError("snapshot_empty")
    return encoded


def validate_market_snapshot(snapshot: Mapping[str, Any]) -> None:
    """Fail closed for malformed, private, or non-point-in-time artifacts."""

    if not isinstance(snapshot, Mapping):
        raise MarketSnapshotError("snapshot_mapping_required")
    _no_private_keys(snapshot)
    if int(snapshot.get("schema_version") or 0) != MARKET_SNAPSHOT_SCHEMA_VERSION:
        raise MarketSnapshotError("snapshot_schema_unsupported")
    if int(snapshot.get("market_store_contract_version") or 0) != MARKET_STORE_CONTRACT_VERSION:
        raise MarketSnapshotError("snapshot_market_contract_mismatch")
    _utc(str(snapshot.get("generated_at_utc") or ""))
    signals = snapshot.get("signals")
    if not isinstance(signals, Mapping):
        raise MarketSnapshotError("snapshot_signals_required")
    rates = snapshot.get("rates")
    if not isinstance(rates, Mapping):
        raise MarketSnapshotError("snapshot_rates_mapping_required")
    if rates:
        _validate_coin_rates(rates)
    _canonical_snapshot_bytes(snapshot)


def _validate_coin_rates(rates: Mapping[str, Any]) -> None:
    """Reject malformed estimates before an atomic artifact can expose them."""

    if str(rates.get("engine_version") or "") != COIN_RATE_ENGINE_VERSION:
        raise MarketSnapshotError("snapshot_rate_engine_version_invalid")
    items = rates.get("items")
    if not isinstance(items, list) or len(items) != len(COIN_SPECS) * 2:
        raise MarketSnapshotError("snapshot_rate_items_invalid")
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, Mapping):
            raise MarketSnapshotError("snapshot_rate_item_invalid")
        code = str(item.get("commodity_code") or "")
        settlement = str(item.get("settlement_term") or "")
        status = str(item.get("status") or "")
        if code not in COIN_SPECS or settlement not in {"CASH", "TOMORROW"} or status not in {"ESTIMATED", "NO_DATA"}:
            raise MarketSnapshotError("snapshot_rate_item_invalid")
        if (code, settlement) in seen:
            raise MarketSnapshotError("snapshot_rate_item_duplicate")
        seen.add((code, settlement))
        values = (item.get("estimated_project_price"), item.get("lower_project_price"), item.get("upper_project_price"))
        if status == "NO_DATA":
            if any(value is not None for value in values):
                raise MarketSnapshotError("snapshot_no_data_rate_has_price")
            continue
        if not all(isinstance(value, int) and value > 0 for value in values):
            raise MarketSnapshotError("snapshot_estimated_rate_invalid")
        center, lower, upper = values
        if not lower <= center <= upper:
            raise MarketSnapshotError("snapshot_rate_interval_invalid")


def publish_market_snapshot_atomically(
    path: Path | str,
    snapshot: Mapping[str, Any],
) -> str:
    """Atomically replace an artifact only after complete validation.

    The old valid file stays untouched on serialization, fsync, or rename
    failure.  The returned digest is an audit-safe snapshot identity.
    """

    validate_market_snapshot(snapshot)
    target = Path(path)
    if target.exists() and not target.is_file():
        raise MarketSnapshotError("snapshot_target_not_file")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_snapshot_bytes(snapshot)
    if len(payload) > DEFAULT_MAXIMUM_SNAPSHOT_BYTES:
        raise MarketSnapshotError("snapshot_file_too_large")
    digest = sha256(payload).hexdigest()
    descriptor = -1
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
        temporary_name = None
        directory_descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as exc:
        raise MarketSnapshotError("snapshot_atomic_publish_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
    return digest


class AtomicMarketSnapshotProvider:
    """Load a snapshot only when one immutable read passes validation."""

    def __init__(
        self,
        path: Path | str,
        *,
        maximum_bytes: int = DEFAULT_MAXIMUM_SNAPSHOT_BYTES,
    ) -> None:
        self.path = Path(path)
        self.maximum_bytes = max(1, int(maximum_bytes))

    def load(self) -> Mapping[str, Any]:
        try:
            before = self.path.stat()
        except OSError as exc:
            raise MarketSnapshotUnavailable("snapshot_file_unavailable") from exc
        if not self.path.is_file() or before.st_size <= 0:
            raise MarketSnapshotUnavailable("snapshot_file_invalid")
        if before.st_size > self.maximum_bytes:
            raise MarketSnapshotUnavailable("snapshot_file_too_large")
        try:
            payload = self.path.read_bytes()
            after = self.path.stat()
        except OSError as exc:
            raise MarketSnapshotUnavailable("snapshot_read_failed") from exc
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise MarketSnapshotUnavailable("snapshot_changed_during_read")
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MarketSnapshotUnavailable("snapshot_json_invalid") from exc
        try:
            validate_market_snapshot(decoded)
        except (MarketSnapshotError, TypeError, ValueError) as exc:
            raise MarketSnapshotUnavailable("snapshot_validation_failed") from exc
        return decoded
