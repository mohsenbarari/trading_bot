"""Operational and model-input health for coin-rate estimation.

Collector liveness and market-data freshness are intentionally separate.  A
quiet Telegram source may produce no events while its collector is healthy;
conversely, a recent cached observation must not hide a dead collector.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping


HEALTH_SCHEMA_VERSION = 1
HEALTHY_PROBE_STATES = {"HEALTHY", "COLLECTED", "PROJECTED"}
TRANSIENT_PROBE_STATES = {"STARTING", "RUNNING"}
AVAILABLE_INPUT_STATES = {"OBSERVED", "ESTIMATED"}


@dataclass(frozen=True)
class InputHealthConfig:
    public_telegram_state: Path
    external_market_state: Path
    group_projection_state: Path
    public_telegram_max_age_seconds: int = 60
    wallex_max_age_seconds: int = 45
    binance_paxg_max_age_seconds: int = 45
    group_projection_max_age_seconds: int = 90


def utc_text(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_registry(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"schema_version": HEALTH_SCHEMA_VERSION, "sources": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), dict):
        return {"schema_version": HEALTH_SCHEMA_VERSION, "sources": {}}
    return payload


def _write_registry(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def update_probe_state(
    path: Path,
    *,
    source: str,
    status: str,
    successful: bool | None,
    now: datetime | None = None,
    error_code: str | None = None,
    details: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Atomically update one privacy-safe collector heartbeat."""

    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp_text = utc_text(stamp)
    normalized_source = source.strip().upper()
    normalized_status = status.strip().upper()
    registry = _read_registry(path)
    sources = dict(registry.get("sources") or {})
    previous = sources.get(normalized_source)
    previous = previous if isinstance(previous, dict) else {}
    entry: dict[str, Any] = {
        "status": normalized_status,
        "heartbeat_at_utc": stamp_text,
        "last_success_at_utc": (
            stamp_text if successful is True else previous.get("last_success_at_utc")
        ),
        "error_code": (
            None
            if successful is not False
            else (error_code or "UNSPECIFIED_FAILURE")
        ),
    }
    if details:
        entry["details"] = {
            str(key): value
            for key, value in details.items()
            if value is None or isinstance(value, (bool, int, float, str))
        }
    sources[normalized_source] = entry
    payload = {
        "schema_version": HEALTH_SCHEMA_VERSION,
        "updated_at_utc": stamp_text,
        "sources": sources,
    }
    _write_registry(path, payload)
    return entry


def _severity(critical: bool) -> str:
    return "CRITICAL" if critical else "DEGRADED"


def assess_probe(
    path: Path,
    *,
    source: str,
    as_of: datetime,
    max_age_seconds: int,
    critical: bool,
) -> dict[str, Any]:
    registry = _read_registry(path)
    raw = (registry.get("sources") or {}).get(source.strip().upper())
    if not isinstance(raw, dict):
        return {
            "status": _severity(critical),
            "reason_code": "COLLECTOR_HEARTBEAT_MISSING",
            "heartbeat_at_utc": None,
            "heartbeat_age_seconds": None,
            "last_success_at_utc": None,
            "max_age_seconds": int(max_age_seconds),
        }
    probe_status = str(raw.get("status") or "UNKNOWN").upper()
    heartbeat = parse_utc(raw.get("heartbeat_at_utc"))
    last_success = parse_utc(raw.get("last_success_at_utc"))
    raw_details = raw.get("details")
    raw_details = raw_details if isinstance(raw_details, Mapping) else {}
    age = None if heartbeat is None else (as_of - heartbeat).total_seconds()
    base = {
        "probe_status": probe_status,
        "heartbeat_at_utc": utc_text(heartbeat) if heartbeat else None,
        "heartbeat_age_seconds": None if age is None else round(max(0.0, age), 3),
        "last_success_at_utc": utc_text(last_success) if last_success else None,
        "max_age_seconds": int(max_age_seconds),
        "error_code": raw.get("error_code"),
        "details": {
            str(key): value
            for key, value in raw_details.items()
            if value is None or isinstance(value, (bool, int, float, str))
        },
    }
    if heartbeat is None:
        return {**base, "status": _severity(critical), "reason_code": "COLLECTOR_HEARTBEAT_INVALID"}
    if age is not None and age < -30:
        return {**base, "status": _severity(critical), "reason_code": "COLLECTOR_CLOCK_SKEW"}
    if age is not None and age > max_age_seconds:
        return {**base, "status": _severity(critical), "reason_code": "COLLECTOR_HEARTBEAT_STALE"}
    if probe_status == "DISABLED":
        return {**base, "status": "DISABLED", "reason_code": None}
    if probe_status in HEALTHY_PROBE_STATES:
        return {**base, "status": "HEALTHY", "reason_code": None}
    if probe_status in TRANSIENT_PROBE_STATES and last_success is not None:
        success_age = (as_of - last_success).total_seconds()
        if success_age <= max_age_seconds:
            return {**base, "status": "HEALTHY", "reason_code": None}
    return {**base, "status": _severity(critical), "reason_code": "COLLECTOR_REPORTED_FAILURE"}


def _input_time(payload: Mapping[str, Any]) -> datetime | None:
    candidates: list[datetime | None] = []

    def collect(value: object) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if key in {
                    "latest_event_utc",
                    "last_event_utc",
                    "anchor_event_utc",
                    "last_observed_utc",
                }:
                    candidates.append(parse_utc(nested))
                elif isinstance(nested, (Mapping, list, tuple)):
                    collect(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                collect(nested)

    collect(payload)
    present = [value for value in candidates if value is not None]
    return max(present) if present else None


def _logical_input_health(
    estimate: Mapping[str, Any], *, as_of: datetime
) -> tuple[dict[str, Any], list[str], bool]:
    policies = {
        "melted_gold": "CRITICAL",
        "xauusd": "CRITICAL",
        "usd": "CRITICAL",
        "usdt": "SUPPORTING",
        "generic_coin": "OPPORTUNISTIC",
        "order_flow": "OPPORTUNISTIC",
        "market_regime": "SUPPORTING",
    }
    settlements = estimate.get("settlements")
    settlements = settlements if isinstance(settlements, Mapping) else {}
    result: dict[str, Any] = {}
    reasons: list[str] = []
    critical_failure = False
    for name, importance in policies.items():
        per_settlement: dict[str, str] = {}
        freshest: datetime | None = None
        stale_observed = False
        proxy_active = False
        for settlement in ("CASH", "TOMORROW"):
            settlement_payload = settlements.get(settlement)
            settlement_payload = settlement_payload if isinstance(settlement_payload, Mapping) else {}
            inputs = settlement_payload.get("inputs")
            inputs = inputs if isinstance(inputs, Mapping) else {}
            payload = (
                settlement_payload.get("market_regime")
                if name == "market_regime"
                else inputs.get(name)
            )
            payload = payload if isinstance(payload, Mapping) else {}
            status = str(payload.get("status") or "NO_DATA").upper()
            if (
                name == "generic_coin"
                and status == "NO_DATA"
                and isinstance(payload.get("excluded_observations"), (list, tuple))
                and bool(payload.get("excluded_observations"))
            ):
                status = "EXCLUDED"
            per_settlement[settlement] = status
            if name == "xauusd" and payload.get("is_proxy") is True:
                proxy_active = True
            observed_at = _input_time(payload)
            if observed_at is not None and (freshest is None or observed_at > freshest):
                freshest = observed_at
            if status == "OBSERVED":
                freshness_window = payload.get("average_window_seconds", payload.get("window_seconds", 90))
                try:
                    maximum_age = max(90, int(freshness_window)) + 60
                except (TypeError, ValueError):
                    maximum_age = 150
                if observed_at is None or (as_of - observed_at) > timedelta(seconds=maximum_age):
                    stale_observed = True
        available = all(value in AVAILABLE_INPUT_STATES for value in per_settlement.values())
        excluded_by_contract = any(
            value == "EXCLUDED" for value in per_settlement.values()
        )
        if stale_observed:
            status = "STALE"
        elif available and proxy_active:
            status = "AVAILABLE_PROXY"
        elif available:
            status = "AVAILABLE"
        elif excluded_by_contract and all(
            value in {"EXCLUDED", "NO_DATA"}
            for value in per_settlement.values()
        ):
            status = "EXCLUDED_BY_CONTRACT"
        elif importance == "OPPORTUNISTIC":
            status = "QUIET_OR_NO_DATA"
        else:
            status = "NO_DATA"
        if status in {"NO_DATA", "STALE"}:
            reason = f"MODEL_INPUT_{name.upper()}_{status}"
            reasons.append(reason)
            if importance == "CRITICAL":
                critical_failure = True
        elif status == "AVAILABLE_PROXY":
            reasons.append("MODEL_INPUT_XAUUSD_PROXY_ACTIVE")
        result[name] = {
            "status": status,
            "importance": importance,
            "settlements": per_settlement,
            "latest_observation_utc": utc_text(freshest) if freshest else None,
            "latest_observation_age_seconds": (
                None if freshest is None else round(max(0.0, (as_of - freshest).total_seconds()), 3)
            ),
        }

    # Coin-group observations do not live in ``settlement.inputs``.  They are
    # selected per commodity after the common market inputs have been built,
    # and the exact selected live/historical anchors are therefore attached to
    # rate rows.  Keep this separate from ``generic_coin`` (the public generic
    # coin quote) so a healthy projection heartbeat cannot make an empty live
    # group book look populated, and a quiet group cannot be mistaken for a
    # collector failure.
    group_settlements: dict[str, str] = {}
    group_freshest: datetime | None = None
    live_commodity_count = 0
    historical_commodity_count = 0
    for settlement in ("CASH", "TOMORROW"):
        settlement_payload = settlements.get(settlement)
        settlement_payload = (
            settlement_payload if isinstance(settlement_payload, Mapping) else {}
        )
        rates = settlement_payload.get("rates")
        rates = rates if isinstance(rates, (list, tuple)) else ()
        live_found = False
        historical_found = False
        for rate in rates:
            if not isinstance(rate, Mapping):
                continue
            live_anchor = rate.get("group_offer_anchor")
            live_anchor = live_anchor if isinstance(live_anchor, Mapping) else {}
            if str(live_anchor.get("status") or "").upper() == "OBSERVED":
                live_found = True
                live_commodity_count += 1
                observed_at = parse_utc(live_anchor.get("latest_event_utc"))
                if observed_at is not None and (
                    group_freshest is None or observed_at > group_freshest
                ):
                    group_freshest = observed_at

            historical_anchor = rate.get("historical_group_anchor")
            historical_anchor = (
                historical_anchor
                if isinstance(historical_anchor, Mapping)
                else {}
            )
            if str(historical_anchor.get("status") or "").upper() == "OBSERVED":
                historical_found = True
                historical_commodity_count += 1
                observed_at = parse_utc(historical_anchor.get("event_time_utc"))
                if observed_at is not None and (
                    group_freshest is None or observed_at > group_freshest
                ):
                    group_freshest = observed_at
        group_settlements[settlement] = (
            "OBSERVED"
            if live_found
            else ("HISTORICAL" if historical_found else "NO_DATA")
        )

    if all(value == "OBSERVED" for value in group_settlements.values()):
        group_status = "AVAILABLE"
    elif any(value == "OBSERVED" for value in group_settlements.values()):
        group_status = "PARTIAL"
    elif any(value == "HISTORICAL" for value in group_settlements.values()):
        group_status = "HISTORICAL_ONLY"
    else:
        group_status = "QUIET_OR_NO_DATA"
    result["coin_groups"] = {
        "status": group_status,
        "importance": "OPPORTUNISTIC",
        "settlements": group_settlements,
        "latest_observation_utc": (
            utc_text(group_freshest) if group_freshest else None
        ),
        "latest_observation_age_seconds": (
            None
            if group_freshest is None
            else round(max(0.0, (as_of - group_freshest).total_seconds()), 3)
        ),
        "live_commodity_count": live_commodity_count,
        "historical_commodity_count": historical_commodity_count,
    }
    return result, reasons, critical_failure


def build_estimator_input_health(
    estimate: Mapping[str, Any],
    *,
    as_of: datetime,
    config: InputHealthConfig,
) -> dict[str, Any]:
    """Combine collector heartbeats with model-approved input availability."""

    effective_as_of = as_of.astimezone(timezone.utc)
    collectors = {
        "public_market_telegram": assess_probe(
            config.public_telegram_state,
            source="PUBLIC_MARKET_TELEGRAM",
            as_of=effective_as_of,
            max_age_seconds=config.public_telegram_max_age_seconds,
            critical=True,
        ),
        "wallex_public_api": assess_probe(
            config.external_market_state,
            source="WALLEX_PUBLIC_API",
            as_of=effective_as_of,
            max_age_seconds=config.wallex_max_age_seconds,
            critical=False,
        ),
        "binance_paxg_public_api": assess_probe(
            config.external_market_state,
            source="BINANCE_PAXG_PUBLIC_API",
            as_of=effective_as_of,
            max_age_seconds=config.binance_paxg_max_age_seconds,
            critical=False,
        ),
        "coin_group_projection": assess_probe(
            config.group_projection_state,
            source="COIN_GROUP_PROJECTION",
            as_of=effective_as_of,
            max_age_seconds=config.group_projection_max_age_seconds,
            critical=False,
        ),
    }
    model_inputs, input_reasons, input_critical = _logical_input_health(
        estimate, as_of=effective_as_of
    )
    reasons = [
        f"{name.upper()}_{payload['reason_code']}"
        for name, payload in collectors.items()
        if payload.get("reason_code")
    ]
    reasons.extend(input_reasons)
    collector_critical = any(
        payload.get("status") == "CRITICAL" for payload in collectors.values()
    )
    collector_degraded = any(
        payload.get("status") == "DEGRADED" for payload in collectors.values()
    )
    supporting_input_degraded = any(
        (
            payload.get("importance") == "SUPPORTING"
            and payload.get("status") in {"NO_DATA", "STALE"}
        )
        or payload.get("status") == "AVAILABLE_PROXY"
        for payload in model_inputs.values()
    )
    if input_critical or collector_critical:
        aggregate = "CRITICAL"
    elif collector_degraded or supporting_input_degraded:
        aggregate = "DEGRADED"
    else:
        aggregate = "HEALTHY"
    return {
        "schema_version": HEALTH_SCHEMA_VERSION,
        "status": aggregate,
        "evaluated_at_utc": utc_text(effective_as_of),
        "reason_codes": sorted(set(reasons)),
        "collectors": collectors,
        "model_inputs": model_inputs,
    }
