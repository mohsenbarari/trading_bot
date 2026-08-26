"""Exact Decimal materialization and idempotent estimator-input ledger.

The ledger stores the values actually consumable by inference, not artificial
per-second market rows.  A snapshot identity changes only when its selected
sample set, value, method or provenance changes; repeated five-second inference
cycles can therefore reference the same immutable snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import sqlite3
from typing import Any, Iterable, Mapping

from .market_contracts import normalize_utc


INPUT_LEDGER_VERSION = "market-input-ledger-v1"
POINT_WINDOW_SECONDS = 90
USDT_TREND_WINDOW_SECONDS = 180
REGIME_WINDOW_SECONDS = 600
PAXG_RECENT_DIRECT_SECONDS = 15 * 60
PAXG_MAX_DIRECT_GAP = Decimal("0.02")

INPUT_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS input_snapshots (
  input_snapshot_hash BLOB PRIMARY KEY CHECK(length(input_snapshot_hash)=32),
  window_end_utc TEXT NOT NULL,
  created_at_utc TEXT NOT NULL,
  selection_method TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS input_snapshot_components (
  input_snapshot_hash BLOB NOT NULL
    REFERENCES input_snapshots(input_snapshot_hash) ON DELETE CASCADE,
  feature_role TEXT NOT NULL,
  source_event_key BLOB,
  consumed_value TEXT,
  consumed_unit TEXT,
  window_start_utc TEXT NOT NULL,
  window_end_utc TEXT NOT NULL,
  sample_count INTEGER NOT NULL CHECK(sample_count>=0),
  selection_method TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  PRIMARY KEY(input_snapshot_hash,feature_role)
);
CREATE TABLE IF NOT EXISTS inference_input_uses (
  inference_id BLOB PRIMARY KEY CHECK(length(inference_id)=32),
  input_snapshot_hash BLOB NOT NULL
    REFERENCES input_snapshots(input_snapshot_hash),
  model_version TEXT NOT NULL,
  settlement TEXT NOT NULL CHECK(settlement IN ('CASH','TOMORROW')),
  inferred_at_utc TEXT NOT NULL
);
"""


class MarketInputMaterializerError(RuntimeError):
    """Raised when source evidence cannot produce a trustworthy ledger row."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _utc(value: datetime | str, *, field: str) -> datetime:
    normalized = normalize_utc(value, field_name=field)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _decimal(value: object) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MarketInputMaterializerError("input_value_invalid") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise MarketInputMaterializerError("input_value_invalid")
    return parsed


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


@dataclass(frozen=True, slots=True)
class Sample:
    event_key: bytes
    event_time_utc: str
    available_at_utc: str
    value: Decimal
    unit: str
    source_code: str


@dataclass(frozen=True, slots=True)
class InputComponent:
    feature_role: str
    source_event_key: bytes | None
    consumed_value: str | None
    consumed_unit: str | None
    window_start_utc: str
    window_end_utc: str
    sample_count: int
    selection_method: str
    provenance: Mapping[str, object]
    sample_digest: str

    def identity(self) -> dict[str, object]:
        return {
            "feature_role": self.feature_role,
            "source_event_key": (
                self.source_event_key.hex() if self.source_event_key is not None else None
            ),
            "consumed_value": self.consumed_value,
            "consumed_unit": self.consumed_unit,
            "sample_count": self.sample_count,
            "selection_method": self.selection_method,
            "provenance": dict(self.provenance),
            "sample_digest": self.sample_digest,
        }


@dataclass(frozen=True, slots=True)
class MaterializedInputSnapshot:
    input_snapshot_hash: bytes
    components: tuple[InputComponent, ...]
    inserted: bool

    @property
    def hash_hex(self) -> str:
        return self.input_snapshot_hash.hex()


def initialize_input_ledger(connection: sqlite3.Connection) -> None:
    connection.executescript(INPUT_LEDGER_SCHEMA)


def _samples(
    connection: sqlite3.Connection,
    *,
    instrument: str,
    source_codes: Iterable[str],
    start: datetime,
    end: datetime,
) -> tuple[Sample, ...]:
    sources = tuple(sorted(set(source_codes)))
    if not sources:
        return ()
    placeholders = ",".join("?" for _ in sources)
    rows = connection.execute(
        f"""
        SELECT event_key,event_time_utc,available_at_utc,price_value,
               price_unit,source_code,side
        FROM market_observations
        WHERE quality_state='ELIGIBLE'
          AND instrument=?
          AND source_code IN ({placeholders})
          AND event_time_utc>? AND event_time_utc<=?
          AND (instrument NOT IN ('USDT_IRT','PAXG_USD_PROXY') OR side='MID')
        ORDER BY event_time_utc,id
        """,
        (instrument, *sources, _stamp(start), _stamp(end)),
    ).fetchall()
    samples: list[Sample] = []
    unit: str | None = None
    for row in rows:
        event_key = row["event_key"]
        if not isinstance(event_key, bytes):
            raise MarketInputMaterializerError("input_event_key_invalid")
        row_unit = str(row["price_unit"])
        if unit is not None and row_unit != unit:
            raise MarketInputMaterializerError("input_window_unit_mismatch")
        unit = row_unit
        samples.append(
            Sample(
                event_key=event_key,
                event_time_utc=str(row["event_time_utc"]),
                available_at_utc=str(row["available_at_utc"]),
                value=_decimal(row["price_value"]),
                unit=row_unit,
                source_code=str(row["source_code"]),
            )
        )
    return tuple(samples)


def _sample_digest(samples: tuple[Sample, ...]) -> str:
    return sha256(
        _canonical(
            [
                {
                    "event_key": sample.event_key.hex(),
                    "event_time_utc": sample.event_time_utc,
                    "value": _decimal_text(sample.value),
                }
                for sample in samples
            ]
        )
    ).hexdigest()


def _component_pair(
    *,
    prefix: str,
    samples: tuple[Sample, ...],
    start: datetime,
    end: datetime,
    selection_method: str,
    provenance: Mapping[str, object],
) -> tuple[InputComponent, InputComponent]:
    window_start = _stamp(start)
    window_end = _stamp(end)
    digest = _sample_digest(samples)
    if not samples:
        common = {
            "source_event_key": None,
            "consumed_value": None,
            "consumed_unit": None,
            "window_start_utc": window_start,
            "window_end_utc": window_end,
            "sample_count": 0,
            "selection_method": "NO_DATA",
            "provenance": {**dict(provenance), "status": "NO_DATA"},
            "sample_digest": digest,
        }
        return (
            InputComponent(feature_role=f"{prefix}_POINT", **common),
            InputComponent(feature_role=f"{prefix}_MEAN", **common),
        )
    latest = samples[-1]
    mean = sum((sample.value for sample in samples), Decimal("0")) / Decimal(
        len(samples)
    )
    common = {
        "source_event_key": latest.event_key,
        "consumed_unit": latest.unit,
        "window_start_utc": window_start,
        "window_end_utc": window_end,
        "sample_count": len(samples),
        "selection_method": selection_method,
        "provenance": {
            **dict(provenance),
            "status": "OBSERVED" if "PROXY" not in selection_method else "ESTIMATED",
            "latest_event_time_utc": latest.event_time_utc,
            "latest_available_at_utc": latest.available_at_utc,
        },
        "sample_digest": digest,
    }
    return (
        InputComponent(
            feature_role=f"{prefix}_POINT",
            consumed_value=_decimal_text(latest.value),
            **common,
        ),
        InputComponent(
            feature_role=f"{prefix}_MEAN",
            consumed_value=_decimal_text(mean),
            **common,
        ),
    )


def _xau_samples(
    connection: sqlite3.Connection,
    *,
    start: datetime,
    end: datetime,
) -> tuple[tuple[Sample, ...], str, Mapping[str, object]]:
    direct = _samples(
        connection,
        instrument="XAUUSD",
        source_codes=("XAUUSD",),
        start=start,
        end=end,
    )
    if direct:
        return direct, "TELEGRAM_DIRECT_XAUUSD", {"is_proxy": False}
    proxy = _samples(
        connection,
        instrument="PAXG_USD_PROXY",
        source_codes=("BINANCE_PAXG_PUBLIC_API",),
        start=start,
        end=end,
    )
    if not proxy:
        return (), "NO_DATA", {"is_proxy": False, "fallback": "PAXG_NO_DATA"}
    recent_direct = _samples(
        connection,
        instrument="XAUUSD",
        source_codes=("XAUUSD",),
        start=end - timedelta(seconds=PAXG_RECENT_DIRECT_SECONDS),
        end=end,
    )
    if recent_direct:
        gap = abs(proxy[-1].value / recent_direct[-1].value - Decimal("1"))
        if gap > PAXG_MAX_DIRECT_GAP:
            return (), "NO_DATA", {
                "is_proxy": False,
                "fallback": "PAXG_PROXY_OUTSIDE_RECENT_XAU_BAND",
                "relative_gap": _decimal_text(gap),
            }
    return proxy, "BINANCE_PAXG_STABLECOIN_CORROBORATED_PROXY", {
        "is_proxy": True,
        "safety_policy": "TWO_BOOK_CORROBORATION_AND_RECENT_XAU_BAND",
    }


def _mean_component(
    *,
    role: str,
    samples: tuple[Sample, ...],
    start: datetime,
    end: datetime,
    selection_method: str,
    provenance: Mapping[str, object],
) -> InputComponent:
    pair = _component_pair(
        prefix="TEMP",
        samples=samples,
        start=start,
        end=end,
        selection_method=selection_method,
        provenance=provenance,
    )
    mean = pair[1]
    return InputComponent(
        feature_role=role,
        source_event_key=mean.source_event_key,
        consumed_value=mean.consumed_value,
        consumed_unit=mean.consumed_unit,
        window_start_utc=mean.window_start_utc,
        window_end_utc=mean.window_end_utc,
        sample_count=mean.sample_count,
        selection_method=mean.selection_method,
        provenance=mean.provenance,
        sample_digest=mean.sample_digest,
    )


def build_input_components(
    connection: sqlite3.Connection,
    *,
    as_of_utc: datetime | str,
    include_usdt_trend: bool = False,
    include_regime: bool = False,
) -> tuple[InputComponent, ...]:
    end = _utc(as_of_utc, field="input_snapshot_as_of_utc")
    point_start = end - timedelta(seconds=POINT_WINDOW_SECONDS)
    usdt = _samples(
        connection,
        instrument="USDT_IRT",
        source_codes=("WALLEX_PUBLIC_API",),
        start=point_start,
        end=end,
    )
    xau, xau_method, xau_provenance = _xau_samples(
        connection, start=point_start, end=end
    )
    components = [
        *_component_pair(
            prefix="USDT_IRT_90S",
            samples=usdt,
            start=point_start,
            end=end,
            selection_method="WALLEX_USDT_IRT" if usdt else "NO_DATA",
            provenance={"is_proxy": False},
        ),
        *_component_pair(
            prefix="XAUUSD_90S",
            samples=xau,
            start=point_start,
            end=end,
            selection_method=xau_method,
            provenance=xau_provenance,
        ),
    ]
    if include_usdt_trend:
        current_start = end - timedelta(seconds=USDT_TREND_WINDOW_SECONDS)
        previous_start = current_start - timedelta(seconds=USDT_TREND_WINDOW_SECONDS)
        current = _samples(
            connection,
            instrument="USDT_IRT",
            source_codes=("WALLEX_PUBLIC_API",),
            start=current_start,
            end=end,
        )
        previous = _samples(
            connection,
            instrument="USDT_IRT",
            source_codes=("WALLEX_PUBLIC_API",),
            start=previous_start,
            end=current_start,
        )
        components.extend(
            (
                _mean_component(
                    role="USDT_IRT_TREND_PREVIOUS_180S",
                    samples=previous,
                    start=previous_start,
                    end=current_start,
                    selection_method="WALLEX_USDT_TREND" if previous else "NO_DATA",
                    provenance={"period": "PREVIOUS"},
                ),
                _mean_component(
                    role="USDT_IRT_TREND_CURRENT_180S",
                    samples=current,
                    start=current_start,
                    end=end,
                    selection_method="WALLEX_USDT_TREND" if current else "NO_DATA",
                    provenance={"period": "CURRENT"},
                ),
            )
        )
    if include_regime:
        regime_start = end - timedelta(seconds=REGIME_WINDOW_SECONDS)
        usdt_regime = _samples(
            connection,
            instrument="USDT_IRT",
            source_codes=("WALLEX_PUBLIC_API",),
            start=regime_start,
            end=end,
        )
        xau_regime, regime_method, regime_provenance = _xau_samples(
            connection, start=regime_start, end=end
        )
        components.extend(
            (
                _mean_component(
                    role="USDT_IRT_REGIME_600S_MEAN",
                    samples=usdt_regime,
                    start=regime_start,
                    end=end,
                    selection_method="WALLEX_USDT_REGIME" if usdt_regime else "NO_DATA",
                    provenance={"window_seconds": REGIME_WINDOW_SECONDS},
                ),
                _mean_component(
                    role="XAUUSD_REGIME_600S_MEAN",
                    samples=xau_regime,
                    start=regime_start,
                    end=end,
                    selection_method=regime_method,
                    provenance={**dict(regime_provenance), "window_seconds": REGIME_WINDOW_SECONDS},
                ),
            )
        )
    return tuple(sorted(components, key=lambda item: item.feature_role))


def materialize_input_snapshot(
    connection: sqlite3.Connection,
    *,
    as_of_utc: datetime | str,
    include_usdt_trend: bool = False,
    include_regime: bool = False,
) -> MaterializedInputSnapshot:
    initialize_input_ledger(connection)
    end = _utc(as_of_utc, field="input_snapshot_as_of_utc")
    components = build_input_components(
        connection,
        as_of_utc=end,
        include_usdt_trend=include_usdt_trend,
        include_regime=include_regime,
    )
    identity = {
        "ledger_version": INPUT_LEDGER_VERSION,
        "components": [component.identity() for component in components],
    }
    digest = sha256(_canonical(identity)).digest()
    exists = connection.execute(
        "SELECT 1 FROM input_snapshots WHERE input_snapshot_hash=?", (digest,)
    ).fetchone()
    inserted = exists is None
    if inserted:
        created = _stamp(datetime.now(timezone.utc))
        connection.execute(
            "INSERT INTO input_snapshots VALUES(?,?,?,?,?)",
            (
                digest,
                _stamp(end),
                created,
                INPUT_LEDGER_VERSION,
                _canonical(identity).decode("ascii"),
            ),
        )
        connection.executemany(
            """
            INSERT INTO input_snapshot_components(
              input_snapshot_hash,feature_role,source_event_key,consumed_value,
              consumed_unit,window_start_utc,window_end_utc,sample_count,
              selection_method,provenance_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    digest,
                    component.feature_role,
                    component.source_event_key,
                    component.consumed_value,
                    component.consumed_unit,
                    component.window_start_utc,
                    component.window_end_utc,
                    component.sample_count,
                    component.selection_method,
                    _canonical(component.provenance).decode("ascii"),
                )
                for component in components
            ],
        )
    return MaterializedInputSnapshot(digest, components, inserted)


def record_inference_use(
    connection: sqlite3.Connection,
    *,
    inference_id: bytes,
    input_snapshot_hash: bytes,
    model_version: str,
    settlement: str,
    inferred_at_utc: datetime | str,
) -> bool:
    if len(inference_id) != 32 or len(input_snapshot_hash) != 32:
        raise MarketInputMaterializerError("inference_identity_invalid")
    if settlement not in {"CASH", "TOMORROW"} or not model_version.strip():
        raise MarketInputMaterializerError("inference_metadata_invalid")
    if connection.execute(
        "SELECT 1 FROM input_snapshots WHERE input_snapshot_hash=?",
        (input_snapshot_hash,),
    ).fetchone() is None:
        raise MarketInputMaterializerError("inference_snapshot_unknown")
    result = connection.execute(
        "INSERT OR IGNORE INTO inference_input_uses VALUES(?,?,?,?,?)",
        (
            inference_id,
            input_snapshot_hash,
            model_version.strip(),
            settlement,
            normalize_utc(inferred_at_utc, field_name="inferred_at_utc"),
        ),
    )
    return bool(result.rowcount)


__all__ = [
    "INPUT_LEDGER_VERSION",
    "InputComponent",
    "MarketInputMaterializerError",
    "MaterializedInputSnapshot",
    "build_input_components",
    "initialize_input_ledger",
    "materialize_input_snapshot",
    "record_inference_use",
]
