"""Fail-safe orchestration for one local Shadow intelligence cycle."""

from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import errno
import fcntl
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterator, Mapping, Sequence

from core.market_intelligence.bundle import LoadedRuntimeBundle
from core.market_intelligence.producer import (
    CoinSnapshotProducer,
    connect_market_db,
    iso_utc,
    latest_complete_minute,
    validate_market_database,
    write_snapshot_atomic,
)


PIPELINE_VERSION = "COIN_SHADOW_PIPELINE_V1"
MAXIMUM_INTERVAL_SPAN_RELATIVE = 0.25
WARNING_CENTER_CHANGE_RELATIVE = 0.08
REJECT_CENTER_CHANGE_RELATIVE = 0.30
MAXIMUM_HARD_COMPARISON_GAP_SECONDS = 6 * 60 * 60
MAXIMUM_PREVIOUS_SNAPSHOT_BYTES = 2 * 1024 * 1024


class ShadowPipelineError(RuntimeError):
    """A stable, data-minimized pipeline failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ShadowCycleAlreadyRunning(ShadowPipelineError):
    """Raised when another process owns the cycle lock."""


@dataclass(frozen=True, slots=True)
class SignalRule:
    name: str
    table: str
    selector_column: str
    selector_value: str
    timestamp_column: str
    fresh_seconds: int
    reference_seconds: int
    current_anchor: bool


SIGNAL_RULES: tuple[SignalRule, ...] = (
    SignalRule(
        "MELTED_GOLD",
        "price_events",
        "instrument",
        "MELTED_GOLD",
        "event_time_utc",
        900,
        345_600,
        True,
    ),
    SignalRule(
        "MELTED_FLOW",
        "price_events",
        "instrument",
        "MELTED_GOLD_FLOW",
        "event_time_utc",
        900,
        3_600,
        False,
    ),
    SignalRule(
        "USD_HERAT",
        "price_events",
        "instrument",
        "USD_HERAT",
        "event_time_utc",
        900,
        86_400,
        True,
    ),
    SignalRule(
        "XAUUSD",
        "price_events",
        "instrument",
        "XAUUSD",
        "event_time_utc",
        3_600,
        259_200,
        False,
    ),
    SignalRule(
        "GOLD_COIN",
        "price_events",
        "instrument",
        "GOLD_COIN",
        "event_time_utc",
        900,
        86_400,
        True,
    ),
    SignalRule(
        "USDT_IRT",
        "external_market_observations",
        "instrument_code",
        "USDT_IRT",
        "observed_at_utc",
        900,
        3_600,
        True,
    ),
    SignalRule(
        "IME_GOLD_BAR",
        "external_market_observations",
        "instrument_code",
        "IME_GOLD_BAR",
        "observed_at_utc",
        1_800,
        259_200,
        False,
    ),
    SignalRule(
        "IME_GOLD_COIN_IMAM",
        "external_market_observations",
        "instrument_code",
        "IME_GOLD_COIN_IMAM",
        "observed_at_utc",
        1_800,
        259_200,
        False,
    ),
)


@dataclass(frozen=True, slots=True)
class ShadowCycleResult:
    snapshot: Mapping[str, Any]
    health: Mapping[str, Any]


def _parse_utc(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            str(value).strip().replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ShadowPipelineError("pipeline_timestamp_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ShadowPipelineError(
            "pipeline_timestamp_timezone_required"
        )
    return parsed.astimezone(timezone.utc)


def _table_exists(
    connection: sqlite3.Connection,
    table: str,
) -> bool:
    return (
        connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (table,),
        ).fetchone()
        is not None
    )


def inspect_source_freshness(
    connection: sqlite3.Connection,
    *,
    as_of: datetime,
    rules: Sequence[SignalRule] = SIGNAL_RULES,
) -> dict[str, Any]:
    """Describe source age without exposing prices or raw observations."""
    cutoff = as_of.astimezone(timezone.utc)
    signals: list[dict[str, Any]] = []
    for rule in rules:
        if not _table_exists(connection, rule.table):
            signals.append(
                {
                    "name": rule.name,
                    "status": "MISSING",
                    "last_observed_utc": None,
                    "age_seconds": None,
                    "recent_sample_count": 0,
                    "current_anchor": rule.current_anchor,
                }
            )
            continue
        row = connection.execute(
            f"""
            SELECT MAX({rule.timestamp_column}) AS latest,
                   SUM(
                       CASE
                           WHEN {rule.timestamp_column} > ?
                            AND {rule.timestamp_column} <= ?
                           THEN 1 ELSE 0
                       END
                   ) AS recent_count
            FROM {rule.table}
            WHERE {rule.selector_column} = ?
              AND {rule.timestamp_column} <= ?
            """,
            (
                iso_utc(cutoff.replace(microsecond=0))
                if rule.fresh_seconds == 0
                else iso_utc(
                    cutoff.replace(microsecond=0)
                    - timedelta(seconds=rule.fresh_seconds)
                ),
                iso_utc(cutoff),
                rule.selector_value,
                iso_utc(cutoff),
            ),
        ).fetchone()
        if row is None or row["latest"] is None:
            status = "MISSING"
            latest = None
            age_seconds = None
        else:
            observed = _parse_utc(row["latest"])
            age_seconds = max(
                0.0,
                (cutoff - observed).total_seconds(),
            )
            latest = iso_utc(observed)
            if age_seconds <= rule.fresh_seconds:
                status = "FRESH"
            elif age_seconds <= rule.reference_seconds:
                status = "REFERENCE_ONLY"
            else:
                status = "STALE"
        signals.append(
            {
                "name": rule.name,
                "status": status,
                "last_observed_utc": latest,
                "age_seconds": age_seconds,
                "recent_sample_count": int(row["recent_count"] or 0),
                "current_anchor": rule.current_anchor,
            }
        )

    fresh_current = [
        row["name"]
        for row in signals
        if row["current_anchor"] and row["status"] == "FRESH"
    ]
    fresh_supporting = [
        row["name"]
        for row in signals
        if not row["current_anchor"] and row["status"] == "FRESH"
    ]
    reference_only = [
        row["name"]
        for row in signals
        if row["status"] == "REFERENCE_ONLY"
    ]
    coin_signal = next(
        (row for row in signals if row["name"] == "GOLD_COIN"), None
    )
    if not fresh_current:
        quality = "INSUFFICIENT"
        eligible = False
        reason = "NO_FRESH_CURRENT_PRICE_ANCHOR"
    elif (
        len(fresh_current) >= 2
        and fresh_supporting
        and coin_signal is not None
        and coin_signal["status"] == "FRESH"
    ):
        quality = "HEALTHY"
        eligible = True
        reason = "MULTI_SIGNAL_CURRENT_INPUT"
    elif coin_signal is not None and coin_signal["status"] != "FRESH":
        quality = "DEGRADED"
        eligible = True
        reason = "NO_FRESH_COIN_ANCHOR"
    else:
        quality = "DEGRADED"
        eligible = True
        reason = "LIMITED_CURRENT_INPUT"
    return {
        "status": quality,
        "eligible_for_snapshot": eligible,
        "reason": reason,
        "as_of_utc": iso_utc(cutoff),
        "fresh_current_anchors": fresh_current,
        "fresh_supporting_signals": fresh_supporting,
        "reference_only_signals": reference_only,
        "signals": signals,
    }


def _rate_map(snapshot: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    settlements = snapshot.get("settlements")
    if not isinstance(settlements, Mapping):
        raise ShadowPipelineError("snapshot_settlements_invalid")
    for settlement in ("CASH", "TOMORROW"):
        payload = settlements.get(settlement)
        if not isinstance(payload, Mapping):
            raise ShadowPipelineError(
                f"snapshot_settlement_missing:{settlement}"
            )
        rates = payload.get("rates")
        if not isinstance(rates, list):
            raise ShadowPipelineError(
                f"snapshot_rates_invalid:{settlement}"
            )
        for rate in rates:
            if not isinstance(rate, Mapping):
                raise ShadowPipelineError(
                    f"snapshot_rate_invalid:{settlement}"
                )
            name = str(rate.get("commodity_name") or "")
            if not name or (settlement, name) in result:
                raise ShadowPipelineError(
                    "snapshot_rate_identity_invalid"
                )
            result[(settlement, name)] = rate
    return result


def inspect_candidate_snapshot(
    candidate: Mapping[str, Any],
    *,
    previous: Mapping[str, Any] | None,
    expected_commodities: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Reject malformed ranges and implausible short-horizon jumps."""
    candidate_rates = _rate_map(candidate)
    warnings: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if expected_commodities is not None:
        expected_identities = {
            (settlement, commodity)
            for settlement in ("CASH", "TOMORROW")
            for commodity in expected_commodities
        }
        actual_identities = set(candidate_rates)
        for settlement, commodity in sorted(
            expected_identities - actual_identities
        ):
            rejected.append(
                {
                    "kind": "RATE_MISSING",
                    "settlement": settlement,
                    "commodity": commodity,
                    "relative_value": None,
                }
            )
        for settlement, commodity in sorted(
            actual_identities - expected_identities
        ):
            rejected.append(
                {
                    "kind": "RATE_UNEXPECTED",
                    "settlement": settlement,
                    "commodity": commodity,
                    "relative_value": None,
                }
            )
    for (settlement, commodity), rate in candidate_rates.items():
        if rate.get("status") != "ESTIMATED":
            warnings.append(
                {
                    "kind": "RATE_ABSTAINED",
                    "settlement": settlement,
                    "commodity": commodity,
                    "relative_value": None,
                }
            )
            continue
        try:
            center = float(rate["estimated_project_price"])
            tolerance = rate["tolerance"]
            lower = float(tolerance["lower_project_price"])
            upper = float(tolerance["upper_project_price"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ShadowPipelineError(
                "snapshot_rate_numeric_contract_invalid"
            ) from exc
        if center <= 0 or lower <= 0 or upper <= 0:
            raise ShadowPipelineError("snapshot_rate_non_positive")
        if not lower <= center <= upper:
            raise ShadowPipelineError("snapshot_interval_order_invalid")
        span = (upper - lower) / center
        if span > MAXIMUM_INTERVAL_SPAN_RELATIVE:
            rejected.append(
                {
                    "kind": "INTERVAL_TOO_WIDE",
                    "settlement": settlement,
                    "commodity": commodity,
                    "relative_value": span,
                }
            )

    if previous is not None:
        previous_rates = _rate_map(previous)
        candidate_time = _parse_utc(candidate.get("generated_at_utc"))
        previous_time = _parse_utc(previous.get("generated_at_utc"))
        gap_seconds = (candidate_time - previous_time).total_seconds()
        same_snapshot = (
            candidate.get("snapshot_version")
            == previous.get("snapshot_version")
        )
        if same_snapshot and candidate != previous:
            rejected.append(
                {
                    "kind": "SNAPSHOT_VERSION_COLLISION",
                    "relative_value": None,
                }
            )
        if gap_seconds < 0 or (gap_seconds == 0 and not same_snapshot):
            rejected.append(
                {
                    "kind": "SNAPSHOT_TIME_REGRESSION",
                    "relative_value": None,
                }
            )
        comparable = (
            0 < gap_seconds <= MAXIMUM_HARD_COMPARISON_GAP_SECONDS
        )
        same_bundle = (
            candidate.get("bundle_version")
            == previous.get("bundle_version")
        )
        for identity, rate in candidate_rates.items():
            if rate.get("status") != "ESTIMATED":
                continue
            previous_rate = previous_rates.get(identity)
            if previous_rate is None:
                continue
            old_center = float(
                previous_rate.get("estimated_project_price") or 0
            )
            new_center = float(
                rate.get("estimated_project_price") or 0
            )
            if old_center <= 0:
                continue
            change = abs(new_center / old_center - 1.0)
            settlement, commodity = identity
            row = {
                "kind": "CENTER_CHANGE",
                "settlement": settlement,
                "commodity": commodity,
                "relative_value": change,
            }
            if (
                comparable
                and same_bundle
                and change > REJECT_CENTER_CHANGE_RELATIVE
            ):
                rejected.append(row)
            elif change > WARNING_CENTER_CHANGE_RELATIVE:
                warnings.append(row)
    return {
        "status": "REJECTED" if rejected else "PASSED",
        "warnings": warnings,
        "rejections": rejected,
        "rate_count": len(candidate_rates),
    }


def _load_previous_snapshot(path: Path) -> Mapping[str, Any] | None:
    if not path.exists():
        return None
    if path.is_symlink():
        raise ShadowPipelineError(
            "previous_snapshot_symlink_forbidden"
        )
    try:
        if path.stat().st_size > MAXIMUM_PREVIOUS_SNAPSHOT_BYTES:
            raise ShadowPipelineError(
                "previous_snapshot_too_large"
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ShadowPipelineError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShadowPipelineError(
            "previous_snapshot_invalid"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ShadowPipelineError(
            "previous_snapshot_not_object"
        )
    return payload


def _validate_cycle_paths(
    *,
    market_db: Path,
    snapshot_path: Path,
    health_path: Path,
    lock_path: Path,
    conversation_db: Path | None,
) -> None:
    named_paths = {
        "market_db": market_db.resolve(),
        "snapshot": snapshot_path.resolve(),
        "health": health_path.resolve(),
        "lock": lock_path.resolve(),
    }
    if conversation_db is not None:
        named_paths["conversation_db"] = conversation_db.resolve()
    if len(set(named_paths.values())) != len(named_paths):
        raise ShadowPipelineError("pipeline_paths_overlap")


def _collection_counts(value: Mapping[str, Any]) -> dict[str, int]:
    """Reduce collector output to bounded counts before persistence."""
    totals = {
        "source_count": 0,
        "messages": 0,
        "events": 0,
        "ignored": 0,
    }
    for row in value.values():
        if not isinstance(row, Mapping):
            continue
        totals["source_count"] += 1
        for key in ("messages", "events", "ignored"):
            try:
                count = int(row.get(key) or 0)
            except (TypeError, ValueError):
                count = 0
            totals[key] += max(0, count)
    return totals


@contextmanager
def exclusive_cycle_lock(path: Path) -> Iterator[None]:
    """Hold a stable non-blocking process lock for the complete cycle."""
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.is_symlink():
        raise ShadowPipelineError("pipeline_lock_symlink_forbidden")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o640)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ShadowPipelineError(
                "pipeline_lock_symlink_forbidden"
            ) from exc
        raise ShadowPipelineError("pipeline_lock_unavailable") from exc
    try:
        try:
            fcntl.flock(
                descriptor,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as exc:
            raise ShadowCycleAlreadyRunning(
                "pipeline_cycle_already_running"
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


class CoinIntelligenceShadowPipeline:
    """Compose optional collection, validation and atomic publication."""

    def __init__(
        self,
        bundle: LoadedRuntimeBundle,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.bundle = bundle
        self.producer = CoinSnapshotProducer(bundle)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ShadowPipelineError(
                "pipeline_clock_timezone_required"
            )
        return value.astimezone(timezone.utc).replace(microsecond=0)

    def run(
        self,
        *,
        market_db: Path,
        snapshot_path: Path,
        health_path: Path,
        lock_path: Path,
        conversation_db: Path | None = None,
        as_of: datetime | None = None,
        collection_step: Callable[[], Mapping[str, Any]] | None = None,
    ) -> ShadowCycleResult:
        _validate_cycle_paths(
            market_db=market_db,
            snapshot_path=snapshot_path,
            health_path=health_path,
            lock_path=lock_path,
            conversation_db=conversation_db,
        )
        with exclusive_cycle_lock(lock_path):
            return self._run_locked(
                market_db=market_db,
                snapshot_path=snapshot_path,
                health_path=health_path,
                conversation_db=conversation_db,
                as_of=as_of,
                collection_step=collection_step,
            )

    def _run_locked(
        self,
        *,
        market_db: Path,
        snapshot_path: Path,
        health_path: Path,
        conversation_db: Path | None,
        as_of: datetime | None,
        collection_step: Callable[[], Mapping[str, Any]] | None,
    ) -> ShadowCycleResult:
        started = self._now()
        stages: list[dict[str, Any]] = []
        health: dict[str, Any] = {
            "pipeline_version": PIPELINE_VERSION,
            "bundle_version": self.bundle.bundle_version,
            "status": "RUNNING",
            "started_at_utc": iso_utc(started),
            "finished_at_utc": None,
            "cutoff_utc": None,
            "snapshot_version": None,
            "input_quality": None,
            "snapshot_quality": None,
            "collection_summary": None,
            "failure_reason": None,
            "stages": stages,
        }
        try:
            if collection_step is not None:
                collection_summary = _collection_counts(
                    collection_step()
                )
                health["collection_summary"] = collection_summary
                stages.append(
                    {
                        "name": "COLLECT",
                        "status": "PASSED",
                    }
                )
            else:
                stages.append(
                    {
                        "name": "COLLECT",
                        "status": "SKIPPED",
                    }
                )

            try:
                validate_market_database(market_db)
            except Exception as exc:
                raise ShadowPipelineError(
                    "pipeline_input_schema_invalid"
                ) from exc
            stages.append(
                {
                    "name": "VALIDATE_INPUT_SCHEMA",
                    "status": "PASSED",
                }
            )
            if as_of is None:
                with closing(connect_market_db(market_db)) as connection:
                    cutoff = latest_complete_minute(connection)
            else:
                if as_of.tzinfo is None or as_of.utcoffset() is None:
                    raise ShadowPipelineError(
                        "pipeline_as_of_timezone_required"
                    )
                cutoff = as_of.astimezone(timezone.utc).replace(
                    microsecond=0
                )
            health["cutoff_utc"] = iso_utc(cutoff)

            with closing(connect_market_db(market_db)) as connection:
                input_quality = inspect_source_freshness(
                    connection,
                    as_of=cutoff,
                )
            health["input_quality"] = input_quality
            if not input_quality["eligible_for_snapshot"]:
                raise ShadowPipelineError(
                    f"pipeline_input_insufficient:{input_quality['reason']}"
                )
            stages.append(
                {
                    "name": "VALIDATE_INPUT_FRESHNESS",
                    "status": "PASSED",
                    "quality": input_quality["status"],
                }
            )

            try:
                candidate = self.producer.build(
                    market_db=market_db,
                    conversation_db=conversation_db,
                    as_of=cutoff,
                )
            except Exception as exc:
                raise ShadowPipelineError(
                    "pipeline_snapshot_build_failed"
                ) from exc
            stages.append(
                {
                    "name": "BUILD_SNAPSHOT",
                    "status": "PASSED",
                }
            )
            previous = _load_previous_snapshot(snapshot_path)
            snapshot_quality = inspect_candidate_snapshot(
                candidate,
                previous=previous,
                expected_commodities=(
                    self.bundle.canonical_commodity_names
                ),
            )
            health["snapshot_quality"] = snapshot_quality
            if snapshot_quality["status"] != "PASSED":
                raise ShadowPipelineError(
                    "pipeline_snapshot_quality_rejected"
                )
            stages.append(
                {
                    "name": "VALIDATE_SNAPSHOT",
                    "status": "PASSED",
                    "warning_count": len(
                        snapshot_quality["warnings"]
                    ),
                }
            )

            write_snapshot_atomic(snapshot_path, candidate)
            stages.append(
                {
                    "name": "PUBLISH_SNAPSHOT",
                    "status": "PASSED",
                }
            )
            finished = self._now()
            health.update(
                {
                    "status": "COMPLETED",
                    "finished_at_utc": iso_utc(finished),
                    "snapshot_version": candidate["snapshot_version"],
                }
            )
            write_snapshot_atomic(health_path, health)
            return ShadowCycleResult(
                snapshot=candidate,
                health=health,
            )
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, ShadowPipelineError)
                else f"pipeline_unexpected:{type(exc).__name__}"
            )
            stages.append(
                {
                    "name": "CYCLE",
                    "status": "FAILED",
                    "reason": code,
                }
            )
            health.update(
                {
                    "status": "FAILED",
                    "finished_at_utc": iso_utc(self._now()),
                    "failure_reason": code,
                }
            )
            write_snapshot_atomic(health_path, health)
            if isinstance(exc, ShadowPipelineError):
                raise
            raise ShadowPipelineError(code) from exc
