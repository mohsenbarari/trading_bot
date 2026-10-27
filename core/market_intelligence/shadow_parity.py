"""Redacted Stage 12 parity evidence for legacy and private market feeds."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import hmac
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Literal, Mapping, Sequence

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from .coin_rate_engine import COIN_SPECS
from .market_snapshot import build_market_snapshot
from .private_pipeline_contracts import Code, Hex64, content_hash, load_source_registry


MIN_LIVE_PARITY_WINDOW_SECONDS = 7_200
MIN_SNAPSHOT_TIMELINE_SAMPLES = 10
MAX_LIVE_SNAPSHOT_GAP_SECONDS = 15
# The repository currently has no independently trusted verifier for the
# operator-supplied live schedule/drill/transport receipts.  Keep the gate
# structurally incapable of recommending promotion until that verifier exists.
TRUSTED_LIVE_ATTESTATION_AVAILABLE = False
EVENTFUL_CAPTURE_SOURCES = frozenset(
    {
        "GROUP_1",
        "GROUP_2",
        "PRIVATE_GOLD_CHANNEL",
        "USD_HERAT",
        "XAUUSD",
        "WALLEX_PUBLIC_API",
    }
)
FALLBACK_CAPTURE_SOURCES = frozenset(
    {"BINANCE_PAXG_PUBLIC_API", "MELTED_AGGREGATE", "MELTED_FLOW"}
)
CAPTURE_SOURCE_INVENTORY = EVENTFUL_CAPTURE_SOURCES | FALLBACK_CAPTURE_SOURCES
EXPECTED_RATE_GRID = frozenset(
    (f"COIN_{instrument}", settlement)
    for instrument in COIN_SPECS
    for settlement in ("CASH", "TOMORROW")
)
EXPECTED_EXTERNAL_FEATURE_GRID = frozenset({"XAUUSD", "USDT_IRT"})
FAILURE_DRILLS = frozenset(
    {
        "RECEIVER_RESTART",
        "ROUTE_PARTITION",
        "LOST_ACK",
        "ROLLBACK",
        "DISK_FAILURE",
    }
)


class ShadowParityError(RuntimeError):
    """A content-free parity gate failure."""


class _Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        protected_namespaces=(),
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone_required")
    return value.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("decimal_invalid") from exc
    if not parsed.is_finite():
        raise ValueError("decimal_invalid")
    return parsed


def _decimal_text(value: object | None) -> str | None:
    if value is None:
        return None
    parsed = Decimal(str(value))
    rendered = format(parsed, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


class CaptureEvidenceV1(_Contract):
    event_key: Hex64
    source_code: Code
    occurred_at_utc: AwareDatetime
    available_at_utc: AwareDatetime

    @field_validator("occurred_at_utc", "available_at_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_time_order(self) -> "CaptureEvidenceV1":
        if self.available_at_utc < self.occurred_at_utc:
            raise ValueError("capture_availability_before_occurrence")
        return self


class FactDimensionsV1(_Contract):
    instrument: Code
    event_type: Literal["OFFER", "TRADE", "QUOTE", "REFERENCE"]
    side: Literal["BUY", "SELL", "MID", "UNKNOWN"]
    settlement: Literal["CASH", "TODAY", "TOMORROW", "SPOT", "UNKNOWN"]
    trade_form: Code
    price_value: str
    price_unit: Code
    quantity_value: str | None = None
    quantity_unit: Code | None = None

    @field_validator("price_value")
    @classmethod
    def validate_price(cls, value: str) -> str:
        parsed = _decimal(value)
        if parsed is None or parsed <= 0:
            raise ValueError("price_must_be_positive")
        return value

    @field_validator("quantity_value")
    @classmethod
    def validate_quantity(cls, value: str | None) -> str | None:
        parsed = _decimal(value)
        if parsed is not None and parsed <= 0:
            raise ValueError("quantity_must_be_positive")
        return value

    @model_validator(mode="after")
    def validate_quantity_pair(self) -> "FactDimensionsV1":
        if (self.quantity_value is None) != (self.quantity_unit is None):
            raise ValueError("quantity_value_and_unit_must_pair")
        return self


class FactEvidenceV1(_Contract):
    event_key: Hex64
    source_code: Code
    eligible: bool
    dimensions: FactDimensionsV1
    parser_fingerprint: Hex64
    lifecycle_state: Code
    occurred_at_utc: AwareDatetime
    available_at_utc: AwareDatetime
    parsed_at_utc: AwareDatetime
    transferred_at_utc: AwareDatetime
    next_snapshot_at_utc: AwareDatetime | None = None

    @field_validator(
        "occurred_at_utc",
        "available_at_utc",
        "parsed_at_utc",
        "transferred_at_utc",
        "next_snapshot_at_utc",
    )
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_trace(self) -> "FactEvidenceV1":
        if self.parser_fingerprint != content_hash(self.dimensions):
            raise ValueError("parser_fingerprint_mismatch")
        if not (
            self.occurred_at_utc
            <= self.available_at_utc
            <= self.parsed_at_utc
            <= self.transferred_at_utc
        ):
            raise ValueError("fact_trace_time_order_invalid")
        if (
            self.next_snapshot_at_utc is not None
            and self.next_snapshot_at_utc < self.transferred_at_utc
        ):
            raise ValueError("snapshot_before_transfer")
        return self


class FeatureEvidenceV1(_Contract):
    evaluation_at_utc: AwareDatetime
    component: Code
    point_value: str | None
    mean_value: str | None
    unit: Code
    sample_count: int = Field(ge=0)
    source_event_key: Hex64 | None = None
    freshness: Literal["FRESH", "STALE", "MISSING", "REJECTED"]

    @field_validator("evaluation_at_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_values(self) -> "FeatureEvidenceV1":
        _decimal(self.point_value)
        _decimal(self.mean_value)
        if self.freshness == "MISSING":
            if self.point_value is not None or self.mean_value is not None:
                raise ValueError("missing_feature_cannot_have_value")
        elif self.point_value is None:
            raise ValueError("observed_feature_requires_point")
        return self


def feature_evidence_snapshot_hash(
    values: Sequence[FeatureEvidenceV1 | Mapping[str, Any]],
) -> str:
    """Return the canonical hash for one estimator input-feature snapshot."""

    features = [
        item if isinstance(item, FeatureEvidenceV1) else FeatureEvidenceV1.model_validate(item)
        for item in values
    ]
    return content_hash(
        [
            item.model_dump(mode="json")
            for item in sorted(features, key=lambda item: item.component)
        ]
    )


class CaptureSourceInventoryEvidenceV1(_Contract):
    source_code: Literal[
        "GROUP_1",
        "GROUP_2",
        "PRIVATE_GOLD_CHANNEL",
        "USD_HERAT",
        "XAUUSD",
        "WALLEX_PUBLIC_API",
        "BINANCE_PAXG_PUBLIC_API",
        "MELTED_AGGREGATE",
        "MELTED_FLOW",
    ]
    captured_event_count: int = Field(ge=0)
    healthy: bool
    observed_at_utc: AwareDatetime
    zero_event_reason: Literal[
        "FALLBACK_NOT_SELECTED",
        "NO_ELIGIBLE_UPSTREAM_EVENT",
    ] | None = None

    @field_validator("observed_at_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_zero_event_reason(self) -> "CaptureSourceInventoryEvidenceV1":
        if self.captured_event_count:
            if self.zero_event_reason is not None:
                raise ValueError("eventful_capture_source_has_zero_event_reason")
        elif self.source_code in EVENTFUL_CAPTURE_SOURCES:
            if self.zero_event_reason is not None:
                raise ValueError("required_eventful_source_cannot_explain_zero_events")
        elif self.zero_event_reason is None:
            raise ValueError("fallback_zero_event_reason_required")
        return self


class SnapshotVersionEvidenceV1(_Contract):
    snapshot_version: int = Field(gt=0)
    evaluation_at_utc: AwareDatetime

    @field_validator("evaluation_at_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)


class ImmutableCapturePrefixEvidenceV1(_Contract):
    """Receipt for one new-capture prefix fanned out to both projections."""

    contract: Literal["market_immutable_capture_prefix/1.0"]
    capture_authority: Literal["NEW_SINGLE_OWNER_CAPTURE"]
    session_id: Code
    pinned_at_utc: AwareDatetime
    sealed_at_utc: AwareDatetime
    byte_range_start: int = Field(ge=0)
    byte_range_end: int = Field(gt=0)
    prefix_event_count: int = Field(gt=0)
    ordered_manifest_hash: Hex64
    seed_receipt_hash: Hex64
    sealed_byte_range_hash: Hex64
    single_owner_receipt_hash: Hex64
    sequence_health_receipt_hash: Hex64
    reconciliation_receipt_hash: Hex64
    unresolved_sequence_gap_count: int = Field(ge=0)
    unresolved_reconciliation_gap_count: int = Field(ge=0)

    @field_validator("pinned_at_utc", "sealed_at_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_seal(self) -> "ImmutableCapturePrefixEvidenceV1":
        if self.sealed_at_utc <= self.pinned_at_utc:
            raise ValueError("capture_prefix_seal_window_invalid")
        if self.byte_range_end <= self.byte_range_start:
            raise ValueError("capture_prefix_byte_range_invalid")
        return self


class EstimateEvidenceV1(_Contract):
    evaluation_at_utc: AwareDatetime
    model_artifact_hash: Hex64
    input_snapshot_hash: Hex64
    instrument: Code
    settlement: Literal["CASH", "TOMORROW"]
    status: Literal["ESTIMATED", "NO_DATA"] = "ESTIMATED"
    value: str | None = None
    lower_bound: str | None = None
    upper_bound: str | None = None
    reason_code: Code | None = None
    unit: Literal["PROJECT_THOUSAND_TOMAN"]
    confidence: Code
    method: Code
    underlying_source: Code | None = None
    underlying_age_seconds: float | None = Field(default=None, ge=0)
    anchor_age_seconds: float | None = Field(default=None, ge=0)
    market_regime: Literal["NORMAL", "UP", "DOWN", "VOLATILE", "UNKNOWN"]

    @field_validator("evaluation_at_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_range(self) -> "EstimateEvidenceV1":
        if self.status == "NO_DATA":
            if any(
                value is not None
                for value in (self.value, self.lower_bound, self.upper_bound)
            ):
                raise ValueError("no_data_estimate_cannot_have_values")
            if self.reason_code is None:
                raise ValueError("no_data_estimate_requires_reason")
            if self.confidence != "NONE":
                raise ValueError("no_data_estimate_confidence_invalid")
            if any(
                value is not None
                for value in (
                    self.underlying_source,
                    self.underlying_age_seconds,
                    self.anchor_age_seconds,
                )
            ):
                raise ValueError("no_data_estimate_source_metadata_forbidden")
            return self
        if self.reason_code is not None:
            raise ValueError("estimated_rate_cannot_have_no_data_reason")
        lower = _decimal(self.lower_bound)
        value = _decimal(self.value)
        upper = _decimal(self.upper_bound)
        if lower is None or value is None or upper is None or not lower <= value <= upper:
            raise ValueError("estimate_range_invalid")
        if self.confidence == "NONE":
            raise ValueError("estimated_confidence_invalid")
        if self.underlying_source is None or self.underlying_age_seconds is None:
            raise ValueError("estimated_underlying_metadata_required")
        if any(
            value is not None and not math.isfinite(value)
            for value in (self.underlying_age_seconds, self.anchor_age_seconds)
        ):
            raise ValueError("estimate_age_not_finite")
        return self


class TransportEvidenceV1(_Contract):
    unresolved_sequence_gap_count: int = Field(ge=0)
    duplicate_eligible_fact_count: int | None = Field(default=None, ge=0)
    duplicate_evidence: Literal["DELIVERY_LEDGER", "NOT_APPLICABLE", "UNKNOWN"] = (
        "UNKNOWN"
    )
    duplicate_evidence_receipt_hash: Hex64 | None = None
    duplicate_evidence_row_count: int | None = Field(default=None, ge=0)
    rejected_delivery_count: int = Field(ge=0)
    receiver_checkpoint_count: int = Field(ge=0)


class ShadowLaneEvidenceV1(_Contract):
    contract: Literal["market_shadow_lane/1.0"]
    lane: Literal["LEGACY", "REFERENCE_PROJECTION", "PRIVATE_SHADOW"]
    session_id: Code = "UNBOUND"
    window_start_utc: AwareDatetime
    window_end_utc: AwareDatetime
    capture_manifest_complete: bool
    model_artifact_hash: Hex64
    captures: tuple[CaptureEvidenceV1, ...]
    capture_prefix: ImmutableCapturePrefixEvidenceV1 | None = None
    capture_inventory: tuple[CaptureSourceInventoryEvidenceV1, ...] = ()
    facts: tuple[FactEvidenceV1, ...]
    features: tuple[FeatureEvidenceV1, ...]
    snapshot_versions: tuple[SnapshotVersionEvidenceV1, ...] = ()
    estimates: tuple[EstimateEvidenceV1, ...]
    transport: TransportEvidenceV1

    @field_validator("window_start_utc", "window_end_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_lane(self) -> "ShadowLaneEvidenceV1":
        if self.window_end_utc <= self.window_start_utc:
            raise ValueError("parity_window_invalid")
        for collection in (self.captures, self.facts):
            keys = [item.event_key for item in collection]
            if len(keys) != len(set(keys)):
                raise ValueError("duplicate_event_evidence")
        if any(item.model_artifact_hash != self.model_artifact_hash for item in self.estimates):
            raise ValueError("estimate_model_artifact_mismatch")
        inventory_sources = [item.source_code for item in self.capture_inventory]
        if len(inventory_sources) != len(set(inventory_sources)):
            raise ValueError("duplicate_capture_inventory_source")
        snapshot_versions = [item.snapshot_version for item in self.snapshot_versions]
        snapshot_times = [item.evaluation_at_utc for item in self.snapshot_versions]
        if len(snapshot_versions) != len(set(snapshot_versions)):
            raise ValueError("duplicate_snapshot_version")
        if len(snapshot_times) != len(set(snapshot_times)):
            raise ValueError("duplicate_snapshot_evaluation_time")
        estimate_keys = [
            (item.evaluation_at_utc, item.instrument, item.settlement)
            for item in self.estimates
        ]
        if len(estimate_keys) != len(set(estimate_keys)):
            raise ValueError("duplicate_estimate_evidence")
        feature_keys = [
            (item.evaluation_at_utc, item.component) for item in self.features
        ]
        if len(feature_keys) != len(set(feature_keys)):
            raise ValueError("duplicate_feature_evidence")
        for timestamp in (
            *(item.occurred_at_utc for item in self.captures),
            *(item.available_at_utc for item in self.captures),
            *(item.observed_at_utc for item in self.capture_inventory),
            *(item.occurred_at_utc for item in self.facts),
            *(item.available_at_utc for item in self.facts),
            *(item.evaluation_at_utc for item in self.features),
            *(item.evaluation_at_utc for item in self.snapshot_versions),
            *(item.evaluation_at_utc for item in self.estimates),
        ):
            if not self.window_start_utc <= timestamp <= self.window_end_utc:
                raise ValueError("lane_evidence_outside_window")
        return self


class HumanParityLabelV1(_Contract):
    event_key: Hex64
    resolution: Literal["PRIVATE_CORRECT", "LEGACY_CORRECT", "EQUIVALENT"]
    label_id_hash: Hex64
    approved_at_utc: AwareDatetime

    @field_validator("approved_at_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)


class MarketSessionScheduleEvidenceV1(_Contract):
    contract: Literal["market_session_schedule/1.0"]
    session_id: Code
    schedule_id: Code
    schedule_version: Code
    timezone_name: Literal["Asia/Tehran"]
    official_open_at_utc: AwareDatetime
    official_close_at_utc: AwareDatetime
    schedule_receipt_hash: Hex64

    @field_validator("official_open_at_utc", "official_close_at_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_window(self) -> "MarketSessionScheduleEvidenceV1":
        if self.official_close_at_utc <= self.official_open_at_utc:
            raise ValueError("market_schedule_window_invalid")
        return self


class FailureSoakEvidenceV1(_Contract):
    contract: Literal["market_failure_soak/1.0"]
    session_id: Code = "UNBOUND"
    evidence_mode: Literal["HISTORICAL_REPLAY", "LIVE_OPEN_MARKET"]
    started_at_utc: AwareDatetime
    completed_at_utc: AwareDatetime
    full_market_session: bool
    receiver_restart_passed: bool
    route_partition_passed: bool
    lost_ack_passed: bool
    rollback_passed: bool
    disk_failure_passed: bool
    market_schedule: MarketSessionScheduleEvidenceV1 | None = None
    drill_receipts: tuple["FailureDrillReceiptV1", ...] = ()

    @field_validator("started_at_utc", "completed_at_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_window(self) -> "FailureSoakEvidenceV1":
        if self.completed_at_utc <= self.started_at_utc:
            raise ValueError("failure_soak_window_invalid")
        drills = [item.drill for item in self.drill_receipts]
        if len(drills) != len(set(drills)):
            raise ValueError("duplicate_failure_drill_receipt")
        for receipt in self.drill_receipts:
            if receipt.session_id != self.session_id:
                raise ValueError("failure_drill_session_mismatch")
            if not (
                self.started_at_utc
                <= receipt.started_at_utc
                < receipt.completed_at_utc
                <= self.completed_at_utc
            ):
                raise ValueError("failure_drill_outside_soak_window")
        if (
            self.market_schedule is not None
            and self.market_schedule.session_id != self.session_id
        ):
            raise ValueError("market_schedule_session_mismatch")
        return self


class FailureDrillReceiptV1(_Contract):
    drill: Literal[
        "RECEIVER_RESTART",
        "ROUTE_PARTITION",
        "LOST_ACK",
        "ROLLBACK",
        "DISK_FAILURE",
    ]
    session_id: Code
    receipt_hash: Hex64
    started_at_utc: AwareDatetime
    completed_at_utc: AwareDatetime
    passed: bool

    @field_validator("started_at_utc", "completed_at_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_window(self) -> "FailureDrillReceiptV1":
        if self.completed_at_utc <= self.started_at_utc:
            raise ValueError("failure_drill_window_invalid")
        return self


FailureSoakEvidenceV1.model_rebuild()


def _issue(
    category: str,
    code: str,
    severity: int,
    *,
    event_key: str | None = None,
    component: str | None = None,
    accepted_by_label: bool = False,
) -> dict[str, Any]:
    return {
        "category": category,
        "code": code,
        "severity": severity,
        "event_key": event_key,
        "component": component,
        "accepted_by_label": accepted_by_label,
    }


def _percentile(values: Sequence[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * ratio) - 1)
    return ordered[index]


def _label_map(labels: Sequence[HumanParityLabelV1]) -> dict[str, HumanParityLabelV1]:
    output: dict[str, HumanParityLabelV1] = {}
    for label in labels:
        if label.event_key in output:
            raise ShadowParityError("duplicate_human_parity_label")
        output[label.event_key] = label
    return output


def capture_manifest_hash(
    captures: Sequence[CaptureEvidenceV1 | Mapping[str, Any]],
) -> str:
    normalized = [
        item
        if isinstance(item, CaptureEvidenceV1)
        else CaptureEvidenceV1.model_validate(item)
        for item in captures
    ]
    return content_hash([item.model_dump(mode="json") for item in normalized])


def _capture_prefix_findings(
    lane: ShadowLaneEvidenceV1,
) -> tuple[list[dict[str, Any]], bool]:
    prefix = lane.capture_prefix
    if prefix is None:
        return [_issue("CAPTURE", "IMMUTABLE_CAPTURE_PREFIX_RECEIPT_MISSING", 1)], False
    issues: list[dict[str, Any]] = []
    calculated_hash = capture_manifest_hash(lane.captures)
    if prefix.session_id != lane.session_id:
        issues.append(_issue("CAPTURE", "CAPTURE_PREFIX_SESSION_MISMATCH", 1))
    if prefix.prefix_event_count != len(lane.captures):
        issues.append(_issue("CAPTURE", "CAPTURE_PREFIX_COUNT_MISMATCH", 1))
    if prefix.ordered_manifest_hash != calculated_hash:
        issues.append(_issue("CAPTURE", "CAPTURE_PREFIX_HASH_MISMATCH", 1))
    if prefix.unresolved_sequence_gap_count:
        issues.append(_issue("CAPTURE", "CAPTURE_PREFIX_SEQUENCE_GAP", 1))
    if prefix.unresolved_reconciliation_gap_count:
        issues.append(_issue("CAPTURE", "CAPTURE_PREFIX_RECONCILIATION_GAP", 1))
    return issues, not issues


def _capture_inventory_findings(
    lane: ShadowLaneEvidenceV1,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    issues: list[dict[str, Any]] = []
    registered_capture_sources = frozenset(
        item.source_code for item in load_source_registry().sources if item.capture_enabled
    )
    if registered_capture_sources != CAPTURE_SOURCE_INVENTORY:
        issues.append(_issue("CAPTURE", "CAPTURE_SOURCE_REGISTRY_MISMATCH", 1))
    inventory = {item.source_code: item for item in lane.capture_inventory}
    capture_counts = Counter(item.source_code for item in lane.captures)
    if set(inventory) != CAPTURE_SOURCE_INVENTORY:
        issues.append(_issue("CAPTURE", "CAPTURE_SOURCE_INVENTORY_INCOMPLETE", 1))
    unknown_capture_sources = set(capture_counts) - CAPTURE_SOURCE_INVENTORY
    if unknown_capture_sources:
        issues.append(_issue("CAPTURE", "CAPTURE_SOURCE_NOT_IN_INVENTORY", 1))
    for source_code in sorted(CAPTURE_SOURCE_INVENTORY):
        item = inventory.get(source_code)
        if item is None:
            continue
        if not item.healthy:
            issues.append(
                _issue(
                    "CAPTURE",
                    "CAPTURE_SOURCE_UNHEALTHY",
                    1,
                    component=source_code,
                )
            )
        if (lane.window_end_utc - item.observed_at_utc).total_seconds() > 60:
            issues.append(
                _issue(
                    "CAPTURE",
                    "CAPTURE_SOURCE_HEALTH_STALE_AT_SEAL",
                    1,
                    component=source_code,
                )
            )
        if item.captured_event_count != capture_counts.get(source_code, 0):
            issues.append(
                _issue(
                    "CAPTURE",
                    "CAPTURE_SOURCE_COUNT_MISMATCH",
                    1,
                    component=source_code,
                )
            )
        if source_code in EVENTFUL_CAPTURE_SOURCES and item.captured_event_count == 0:
            issues.append(
                _issue(
                    "CAPTURE",
                    "REQUIRED_CAPTURE_SOURCE_NOT_EVENTFUL",
                    1,
                    component=source_code,
                )
            )
    return issues, {
        key: capture_counts.get(key, 0) for key in sorted(CAPTURE_SOURCE_INVENTORY)
    }


def _snapshot_timeline_findings(
    lane: ShadowLaneEvidenceV1,
    *,
    require_live_cadence: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    snapshots = sorted(lane.snapshot_versions, key=lambda item: item.snapshot_version)
    snapshot_times = {item.evaluation_at_utc for item in snapshots}
    versions = [item.snapshot_version for item in snapshots]
    version_gap_count = sum(
        max(0, following - prior - 1)
        for prior, following in zip(versions, versions[1:])
    )
    if len(snapshots) < MIN_SNAPSHOT_TIMELINE_SAMPLES:
        issues.append(_issue("TIMING", "SNAPSHOT_TIMELINE_TOO_SPARSE", 1))
    if version_gap_count:
        issues.append(_issue("TIMING", "SNAPSHOT_VERSION_GAP", 1))
    if any(
        following.evaluation_at_utc <= prior.evaluation_at_utc
        for prior, following in zip(snapshots, snapshots[1:])
    ):
        issues.append(_issue("TIMING", "SNAPSHOT_TIMELINE_NOT_MONOTONIC", 1))
    maximum_gap = max(
        (
            (following.evaluation_at_utc - prior.evaluation_at_utc).total_seconds()
            for prior, following in zip(snapshots, snapshots[1:])
        ),
        default=0.0,
    )
    if require_live_cadence and maximum_gap > MAX_LIVE_SNAPSHOT_GAP_SECONDS:
        issues.append(_issue("TIMING", "LIVE_SNAPSHOT_CADENCE_GAP", 1))
    window_covered = bool(snapshots) and (
        (snapshots[0].evaluation_at_utc - lane.window_start_utc).total_seconds() <= 7
        and (lane.window_end_utc - snapshots[-1].evaluation_at_utc).total_seconds() <= 7
    )
    if not window_covered:
        issues.append(_issue("TIMING", "SNAPSHOT_TIMELINE_WINDOW_UNCOVERED", 1))
    linked_times = {
        item.next_snapshot_at_utc
        for item in lane.facts
        if item.eligible and item.next_snapshot_at_utc is not None
    }
    if linked_times - snapshot_times:
        issues.append(_issue("TIMING", "SNAPSHOT_TRACE_NOT_IN_TIMELINE", 1))
    feature_times = {item.evaluation_at_utc for item in lane.features}
    if feature_times - snapshot_times:
        issues.append(_issue("TIMING", "FEATURE_NOT_BOUND_TO_SNAPSHOT", 1))
    feature_components_by_time: dict[datetime, set[str]] = {}
    feature_values_by_time: dict[datetime, dict[str, FeatureEvidenceV1]] = {}
    for feature in lane.features:
        feature_components_by_time.setdefault(feature.evaluation_at_utc, set()).add(
            feature.component
        )
        feature_values_by_time.setdefault(feature.evaluation_at_utc, {})[
            feature.component
        ] = feature
    incomplete_external_feature_grids = sum(
        not EXPECTED_EXTERNAL_FEATURE_GRID.issubset(
            feature_components_by_time.get(timestamp, set())
        )
        for timestamp in snapshot_times
    )
    if incomplete_external_feature_grids:
        issues.append(_issue("TIMING", "EXTERNAL_FEATURE_GRID_INCOMPLETE", 1))
    unusable_external_features = sum(
        any(
            (feature := feature_values_by_time.get(timestamp, {}).get(component))
            is None
            or feature.freshness != "FRESH"
            or feature.sample_count <= 0
            or feature.point_value is None
            for component in EXPECTED_EXTERNAL_FEATURE_GRID
        )
        for timestamp in snapshot_times
    )
    if unusable_external_features:
        issues.append(_issue("TIMING", "EXTERNAL_FEATURE_NOT_FRESH", 1))
    estimate_keys_by_time: dict[datetime, set[tuple[str, str]]] = {}
    for estimate in lane.estimates:
        estimate_keys_by_time.setdefault(estimate.evaluation_at_utc, set()).add(
            (estimate.instrument, estimate.settlement)
        )
    if set(estimate_keys_by_time) != snapshot_times:
        issues.append(_issue("ESTIMATOR", "RATE_TIMELINE_SNAPSHOT_MISMATCH", 1))
    incomplete_grids = sum(
        estimate_keys_by_time.get(timestamp, set()) != EXPECTED_RATE_GRID
        for timestamp in snapshot_times
    )
    if incomplete_grids:
        issues.append(_issue("ESTIMATOR", "RATE_GRID_INCOMPLETE", 1))
    feature_hash_mismatches = 0
    for timestamp in snapshot_times:
        expected_input_hash = feature_evidence_snapshot_hash(
            [
                feature
                for feature in lane.features
                if feature.evaluation_at_utc == timestamp
            ]
        )
        feature_hash_mismatches += sum(
            item.input_snapshot_hash != expected_input_hash
            for item in lane.estimates
            if item.evaluation_at_utc == timestamp
        )
    if feature_hash_mismatches:
        issues.append(_issue("ESTIMATOR", "INPUT_SNAPSHOT_HASH_INVALID", 1))
    return issues, {
        "snapshot_count": len(snapshots),
        "snapshot_version_gap_count": version_gap_count,
        "snapshot_window_covered": window_covered,
        "maximum_snapshot_gap_seconds": maximum_gap,
        "incomplete_rate_grid_count": incomplete_grids,
        "incomplete_external_feature_grid_count": incomplete_external_feature_grids,
        "unusable_external_feature_count": unusable_external_features,
        "input_snapshot_hash_mismatch_count": feature_hash_mismatches,
    }


def compare_shadow_lanes(
    legacy_value: Mapping[str, Any],
    private_value: Mapping[str, Any],
    *,
    soak_value: Mapping[str, Any],
    labels_value: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    legacy = ShadowLaneEvidenceV1.model_validate(legacy_value)
    private = ShadowLaneEvidenceV1.model_validate(private_value)
    soak = FailureSoakEvidenceV1.model_validate(soak_value)
    labels = _label_map(
        [HumanParityLabelV1.model_validate(item) for item in labels_value]
    )
    if legacy.lane not in {"LEGACY", "REFERENCE_PROJECTION"} or private.lane != "PRIVATE_SHADOW":
        raise ShadowParityError("parity_lane_role_invalid")
    if (legacy.window_start_utc, legacy.window_end_utc) != (
        private.window_start_utc,
        private.window_end_utc,
    ):
        raise ShadowParityError("parity_window_mismatch")

    issues: list[dict[str, Any]] = []
    reference_projection_isolated = legacy.lane == "REFERENCE_PROJECTION"
    if soak.evidence_mode == "LIVE_OPEN_MARKET" and not reference_projection_isolated:
        issues.append(
            _issue("CAPTURE", "OLD_LIVE_COLLECTOR_REFERENCE_FORBIDDEN", 1)
        )
    window_seconds = (legacy.window_end_utc - legacy.window_start_utc).total_seconds()
    session_bound = (
        legacy.session_id != "UNBOUND"
        and legacy.session_id == private.session_id == soak.session_id
        and soak.started_at_utc == legacy.window_start_utc
        and soak.completed_at_utc == legacy.window_end_utc
    )
    schedule = soak.market_schedule
    official_schedule_bound = (
        schedule is not None
        and schedule.session_id == soak.session_id
        and schedule.official_open_at_utc == legacy.window_start_utc
        and schedule.official_close_at_utc == legacy.window_end_utc
    )
    if not session_bound:
        issues.append(_issue("TIMING", "SESSION_OR_WINDOW_BINDING_INVALID", 1))
    if soak.evidence_mode == "LIVE_OPEN_MARKET" and not official_schedule_bound:
        issues.append(
            _issue("TIMING", "OFFICIAL_MARKET_SCHEDULE_NOT_BOUND", 1)
        )
    if soak.evidence_mode == "LIVE_OPEN_MARKET" and (
        window_seconds < MIN_LIVE_PARITY_WINDOW_SECONDS
    ):
        issues.append(_issue("TIMING", "LIVE_PARITY_WINDOW_TOO_SHORT", 1))
    if not legacy.capture_manifest_complete or not private.capture_manifest_complete:
        issues.append(_issue("CAPTURE", "CAPTURE_MANIFEST_INCOMPLETE", 1))
    if legacy.model_artifact_hash != private.model_artifact_hash:
        issues.append(_issue("ESTIMATOR", "MODEL_ARTIFACT_MISMATCH", 1))

    legacy_prefix_issues, legacy_prefix_valid = _capture_prefix_findings(legacy)
    private_prefix_issues, private_prefix_valid = _capture_prefix_findings(private)
    issues.extend(legacy_prefix_issues)
    issues.extend(private_prefix_issues)
    common_capture_prefix = (
        legacy_prefix_valid
        and private_prefix_valid
        and legacy.capture_prefix == private.capture_prefix
        and legacy.captures == private.captures
    )
    if not common_capture_prefix:
        issues.append(_issue("CAPTURE", "COMMON_CAPTURE_PREFIX_DIVERGED", 1))
    capture_session_sealed = (
        common_capture_prefix
        and schedule is not None
        and legacy.capture_prefix is not None
        and legacy.capture_prefix.pinned_at_utc <= schedule.official_open_at_utc
        and legacy.capture_prefix.sealed_at_utc >= schedule.official_close_at_utc
    )
    if soak.evidence_mode == "LIVE_OPEN_MARKET" and not capture_session_sealed:
        issues.append(
            _issue("CAPTURE", "CAPTURE_SESSION_NOT_PREOPEN_PINNED_AND_SEALED", 1)
        )
    legacy_inventory_issues, legacy_source_event_counts = _capture_inventory_findings(
        legacy
    )
    private_inventory_issues, private_source_event_counts = (
        _capture_inventory_findings(private)
    )
    issues.extend(legacy_inventory_issues)
    issues.extend(private_inventory_issues)
    if legacy.capture_inventory != private.capture_inventory:
        issues.append(_issue("CAPTURE", "COMMON_CAPTURE_INVENTORY_DIVERGED", 1))
    live_evidence = soak.evidence_mode == "LIVE_OPEN_MARKET"
    legacy_timeline_issues, legacy_timeline = _snapshot_timeline_findings(
        legacy,
        require_live_cadence=live_evidence,
    )
    private_timeline_issues, private_timeline = _snapshot_timeline_findings(
        private,
        require_live_cadence=live_evidence,
    )
    issues.extend(legacy_timeline_issues)
    issues.extend(private_timeline_issues)
    legacy_snapshot_identity = {
        (item.snapshot_version, item.evaluation_at_utc) for item in legacy.snapshot_versions
    }
    private_snapshot_identity = {
        (item.snapshot_version, item.evaluation_at_utc) for item in private.snapshot_versions
    }
    if legacy_snapshot_identity != private_snapshot_identity:
        issues.append(_issue("TIMING", "LANE_SNAPSHOT_TIMELINE_MISMATCH", 1))

    legacy_capture = {item.event_key: item for item in legacy.captures}
    private_capture = {item.event_key: item for item in private.captures}
    for key in sorted(set(legacy_capture) - set(private_capture)):
        issues.append(_issue("CAPTURE", "PRIVATE_CAPTURE_MISSING", 1, event_key=key))

    legacy_facts = {item.event_key: item for item in legacy.facts}
    private_facts = {item.event_key: item for item in private.facts}
    for lane_name, facts, captures_by_key in (
        ("LEGACY", legacy.facts, legacy_capture),
        ("PRIVATE", private.facts, private_capture),
    ):
        for fact in facts:
            capture = captures_by_key.get(fact.event_key)
            if capture is None or capture.source_code != fact.source_code:
                issues.append(
                    _issue(
                        "CAPTURE",
                        f"{lane_name}_FACT_CAPTURE_LINEAGE_INVALID",
                        1,
                        event_key=fact.event_key,
                    )
                )
    for key in sorted(set(legacy_facts) | set(private_facts)):
        left = legacy_facts.get(key)
        right = private_facts.get(key)
        label = labels.get(key)
        accepted = label is not None and label.resolution in {"PRIVATE_CORRECT", "EQUIVALENT"}
        if left is not None and left.eligible and right is None:
            issues.append(
                _issue(
                    "PARSER",
                    "PRIVATE_FACT_MISSING",
                    0 if accepted else 2,
                    event_key=key,
                    accepted_by_label=accepted,
                )
            )
            continue
        if left is None and right is not None:
            issues.append(
                _issue(
                    "PARSER",
                    "PRIVATE_FACT_ADDED",
                    0 if accepted else 2,
                    event_key=key,
                    accepted_by_label=accepted,
                )
            )
            continue
        if left is None or right is None:
            continue
        if (
            left.eligible != right.eligible
            or left.parser_fingerprint != right.parser_fingerprint
            or left.lifecycle_state != right.lifecycle_state
        ):
            if left.dimensions.price_unit != right.dimensions.price_unit:
                category, code, severity = "UNIT", "FACT_UNIT_MISMATCH", 1
            elif left.lifecycle_state != right.lifecycle_state:
                category, code, severity = "LIFECYCLE", "LIFECYCLE_MISMATCH", 2
            else:
                category, code, severity = "PARSER", "FACT_DIMENSION_MISMATCH", 2
            issues.append(
                _issue(
                    category,
                    code,
                    0 if accepted and severity == 2 else severity,
                    event_key=key,
                    accepted_by_label=accepted and severity == 2,
                )
            )

    transport = private.transport
    for value, code in (
        (transport.unresolved_sequence_gap_count, "UNRESOLVED_SEQUENCE_GAP"),
        (transport.rejected_delivery_count, "REJECTED_PRIVATE_DELIVERY"),
    ):
        if value:
            issues.append(_issue("TRANSPORT", code, 1))
    duplicate_ledger_known = (
        transport.duplicate_evidence == "DELIVERY_LEDGER"
        and transport.duplicate_eligible_fact_count is not None
        and transport.duplicate_evidence_receipt_hash is not None
        and transport.duplicate_evidence_row_count is not None
        and transport.duplicate_evidence_row_count > 0
    )
    if not duplicate_ledger_known:
        issues.append(_issue("TRANSPORT", "DUPLICATE_LEDGER_EVIDENCE_UNKNOWN", 1))
    elif transport.duplicate_eligible_fact_count:
        issues.append(_issue("TRANSPORT", "DUPLICATE_ELIGIBLE_FACT", 1))
    if transport.receiver_checkpoint_count <= 0:
        issues.append(_issue("TRANSPORT", "RECEIVER_CHECKPOINTS_MISSING", 1))

    legacy_features = {
        (item.evaluation_at_utc, item.component): item for item in legacy.features
    }
    private_features = {
        (item.evaluation_at_utc, item.component): item for item in private.features
    }
    all_features = sorted(set(legacy_features) | set(private_features))
    external_components = {"XAU", "XAUUSD", "USDT", "USDT_IRT"}
    consumed_external_mismatch = 0
    for key in all_features:
        component = key[1]
        left = legacy_features.get(key)
        right = private_features.get(key)
        if left is None or right is None:
            severity = 1 if component in external_components else 2
            if component in external_components:
                consumed_external_mismatch += 1
            issues.append(
                _issue("TIMING", "CONSUMED_FEATURE_MISSING", severity, component=component)
            )
            continue
        if left.unit != right.unit:
            issues.append(_issue("UNIT", "FEATURE_UNIT_MISMATCH", 1, component=component))
            continue
        if (
            _decimal(left.point_value) != _decimal(right.point_value)
            or _decimal(left.mean_value) != _decimal(right.mean_value)
            or left.sample_count != right.sample_count
            or left.freshness != right.freshness
        ):
            severity = 1 if component in external_components else 2
            if component in external_components:
                consumed_external_mismatch += 1
            issues.append(
                _issue("PARSER", "CONSUMED_FEATURE_MISMATCH", severity, component=component)
            )

    legacy_estimates = {
        (item.evaluation_at_utc, item.instrument, item.settlement): item
        for item in legacy.estimates
    }
    private_estimates = {
        (item.evaluation_at_utc, item.instrument, item.settlement): item
        for item in private.estimates
    }
    estimator_mismatches = 0
    for key in sorted(set(legacy_estimates) | set(private_estimates)):
        left = legacy_estimates.get(key)
        right = private_estimates.get(key)
        if left is None or right is None:
            estimator_mismatches += 1
            issues.append(_issue("ESTIMATOR", "ESTIMATE_MISSING", 1))
            continue
        if left.input_snapshot_hash != right.input_snapshot_hash:
            estimator_mismatches += 1
            issues.append(_issue("ESTIMATOR", "INPUT_SNAPSHOT_HASH_MISMATCH", 1))
            continue
        if left.status != right.status:
            estimator_mismatches += 1
            issues.append(_issue("ESTIMATOR", "RATE_STATUS_MISMATCH", 1))
            continue
        if left.status == "NO_DATA":
            if left.reason_code != right.reason_code:
                estimator_mismatches += 1
                issues.append(_issue("ESTIMATOR", "NO_DATA_REASON_MISMATCH", 1))
            continue
        if (
            _decimal(left.value),
            _decimal(left.lower_bound),
            _decimal(left.upper_bound),
        ) != (
            _decimal(right.value),
            _decimal(right.lower_bound),
            _decimal(right.upper_bound),
        ):
            estimator_mismatches += 1
            issues.append(_issue("ESTIMATOR", "SAME_INPUT_OUTPUT_MISMATCH", 1))
            continue
        if (
            left.unit,
            left.confidence,
            left.method,
            left.underlying_source,
            left.underlying_age_seconds,
            left.anchor_age_seconds,
            left.market_regime,
        ) != (
            right.unit,
            right.confidence,
            right.method,
            right.underlying_source,
            right.underlying_age_seconds,
            right.anchor_age_seconds,
            right.market_regime,
        ):
            estimator_mismatches += 1
            issues.append(_issue("ESTIMATOR", "ESTIMATE_METADATA_MISMATCH", 1))

    latencies = [
        (item.next_snapshot_at_utc - item.occurred_at_utc).total_seconds()
        for item in private.facts
        if item.eligible and item.next_snapshot_at_utc is not None
    ]
    missing_snapshot_links = sum(
        1 for item in private.facts if item.eligible and item.next_snapshot_at_utc is None
    )
    p95 = _percentile(latencies, 0.95)
    if missing_snapshot_links:
        issues.append(_issue("TIMING", "SNAPSHOT_TRACE_MISSING", 1))
    if p95 is None or p95 > 7:
        issues.append(_issue("TIMING", "SOURCE_TO_SNAPSHOT_P95_EXCEEDED", 1))

    asserted_failure_drills_passed = all(
        (
            soak.receiver_restart_passed,
            soak.route_partition_passed,
            soak.lost_ack_passed,
            soak.rollback_passed,
            soak.disk_failure_passed,
        )
    )
    drill_receipts = {item.drill: item for item in soak.drill_receipts}
    receipt_failure_drills_passed = (
        set(drill_receipts) == FAILURE_DRILLS
        and all(item.passed for item in drill_receipts.values())
    )
    failure_drills_passed = (
        asserted_failure_drills_passed and receipt_failure_drills_passed
    )
    if not asserted_failure_drills_passed:
        issues.append(_issue("TRANSPORT", "FAILURE_SOAK_INCOMPLETE", 1))
    if not receipt_failure_drills_passed:
        issues.append(_issue("TRANSPORT", "FAILURE_SOAK_RECEIPTS_INCOMPLETE", 1))
    if live_evidence and not TRUSTED_LIVE_ATTESTATION_AVAILABLE:
        issues.append(
            _issue("GOVERNANCE", "TRUSTED_LIVE_ATTESTATION_UNAVAILABLE", 1)
        )
    if live_evidence and not any(
        item.status == "ESTIMATED" for item in private.estimates
    ):
        issues.append(_issue("ESTIMATOR", "LIVE_SESSION_HAS_NO_ESTIMATED_RATE", 1))
    window_long_enough = window_seconds >= MIN_LIVE_PARITY_WINDOW_SECONDS
    live_open_market_passed = (
        soak.evidence_mode == "LIVE_OPEN_MARKET"
        and soak.full_market_session
        and failure_drills_passed
        and session_bound
        and official_schedule_bound
        and capture_session_sealed
        and window_long_enough
        and TRUSTED_LIVE_ATTESTATION_AVAILABLE
    )
    blocking = [item for item in issues if item["severity"] in {1, 2}]
    recommendation = (
        "READY_FOR_EXPLICIT_PROMOTION_APPROVAL"
        if not blocking and live_open_market_passed
        else "HOLD_LIVE_OPEN_MARKET_REQUIRED"
        if not blocking
        else "HOLD_BLOCKING_PARITY_FINDINGS"
    )
    required_next_actions = [
        {
            "code": code,
            "required": True,
        }
        for code in sorted({item["code"] for item in blocking})
    ]
    if not live_open_market_passed and not blocking:
        required_next_actions.append(
            {"code": "RUN_BOUND_FULL_OPEN_MARKET_SESSION", "required": True}
        )
    return {
        "contract": "market_shadow_parity_report/1.0",
        "reference_lane": legacy.lane,
        "reference_lane_semantics": (
            "ISOLATED_REFERENCE_PROJECTION_FROM_NEW_SINGLE_OWNER_CAPTURE"
            if reference_projection_isolated
            else "HISTORICAL_COMPATIBILITY_ONLY_NOT_A_LIVE_COLLECTOR_AUTHORITY"
        ),
        "live_capture_authority": "NEW_SINGLE_OWNER_CAPTURE",
        "common_immutable_capture_prefix": common_capture_prefix,
        "capture_session_preopen_pinned_and_sealed": capture_session_sealed,
        "window_start_utc": legacy.window_start_utc.isoformat(),
        "window_end_utc": legacy.window_end_utc.isoformat(),
        "legacy_capture_count": len(legacy.captures),
        "private_capture_count": len(private.captures),
        "legacy_fact_count": len(legacy.facts),
        "private_fact_count": len(private.facts),
        "private_only_capture_count": len(set(private_capture) - set(legacy_capture)),
        "private_capture_loss_count": len(set(legacy_capture) - set(private_capture)),
        "duplicate_eligible_fact_count": transport.duplicate_eligible_fact_count,
        "duplicate_evidence": transport.duplicate_evidence,
        "duplicate_evidence_receipt_hash": transport.duplicate_evidence_receipt_hash,
        "duplicate_evidence_row_count": transport.duplicate_evidence_row_count,
        "unresolved_sequence_gap_count": transport.unresolved_sequence_gap_count,
        "consumed_external_mismatch_count": consumed_external_mismatch,
        "same_input_estimator_mismatch_count": estimator_mismatches,
        "source_to_snapshot_p95_seconds": p95,
        "missing_snapshot_trace_count": missing_snapshot_links,
        "severity_1_count": sum(item["severity"] == 1 for item in issues),
        "severity_2_count": sum(item["severity"] == 2 for item in issues),
        "accepted_labeled_difference_count": sum(
            bool(item["accepted_by_label"]) for item in issues
        ),
        "live_open_market_passed": live_open_market_passed,
        "failure_soak_passed": failure_drills_passed,
        "session_bound": session_bound,
        "official_market_schedule_bound": official_schedule_bound,
        "window_duration_seconds": window_seconds,
        "minimum_live_window_seconds": MIN_LIVE_PARITY_WINDOW_SECONDS,
        "legacy_capture_source_event_counts": legacy_source_event_counts,
        "private_capture_source_event_counts": private_source_event_counts,
        "legacy_snapshot_timeline": legacy_timeline,
        "private_snapshot_timeline": private_timeline,
        "required_next_actions": required_next_actions,
        "cutover_performed": False,
        "issues": issues,
        "promotion_recommendation": recommendation,
    }


def sign_parity_report(
    report: Mapping[str, Any], *, key: bytes, key_id: str
) -> dict[str, Any]:
    if len(key) < 32:
        raise ShadowParityError("parity_signing_key_too_short")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,95}", key_id) is None:
        raise ShadowParityError("parity_signing_key_id_invalid")
    body = dict(report)
    body.pop("report_hash", None)
    body.pop("signature", None)
    body.pop("signature_key_id", None)
    report_hash = content_hash(body)
    signature = hmac.new(key, bytes.fromhex(report_hash), sha256).hexdigest()
    return {
        **body,
        "report_hash": report_hash,
        "signature_key_id": key_id,
        "signature": signature,
    }


def verify_parity_report(document: Mapping[str, Any], *, key: bytes) -> bool:
    try:
        signature = str(document["signature"])
        expected_hash = str(document["report_hash"])
        body = dict(document)
        body.pop("signature", None)
        body.pop("signature_key_id", None)
        body.pop("report_hash", None)
        if content_hash(body) != expected_hash:
            return False
        expected = hmac.new(key, bytes.fromhex(expected_hash), sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
    except (KeyError, TypeError, ValueError):
        return False


def _market_store_rows(
    connection: sqlite3.Connection, *, start: str, end: str
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT event_key,source_code,event_time_utc,available_at_utc,
               inserted_at_utc,instrument,event_type,side,settlement_term,
               trade_form,price_value,price_unit,quantity_value,quantity_unit,
               quality_state
        FROM market_observations
        WHERE available_at_utc>=? AND available_at_utc<=?
        ORDER BY available_at_utc,id
        """,
        (start, end),
    ).fetchall()


def build_lane_evidence_from_market_store(
    *,
    market_store_path: Path,
    lane: Literal["LEGACY", "REFERENCE_PROJECTION", "PRIVATE_SHADOW"],
    window_start_utc: datetime,
    window_end_utc: datetime,
    model_artifact_hash: str,
    capture_manifest: Sequence[Mapping[str, Any]] | None = None,
    capture_prefix: Mapping[str, Any] | None = None,
    capture_inventory: Sequence[Mapping[str, Any]] | None = None,
    snapshot_times: Mapping[str, datetime] | None = None,
    snapshot_versions: Sequence[Mapping[str, Any]] | None = None,
    session_id: str = "UNBOUND",
) -> ShadowLaneEvidenceV1:
    """Build redacted evidence from a read-only Market Store snapshot."""

    start = _utc(window_start_utc)
    end = _utc(window_end_utc)
    connection = sqlite3.connect(
        f"file:{market_store_path.resolve()}?mode=ro", uri=True, timeout=10
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = _market_store_rows(connection, start=_stamp(start), end=_stamp(end))
        projections: dict[str, sqlite3.Row] = {}
        if lane == "PRIVATE_SHADOW":
            try:
                projection_rows = connection.execute(
                    """
                    SELECT hex(event_key) AS event_key,parsed_at_utc,transferred_at_utc
                    FROM private_fact_adapter_projections
                    """
                ).fetchall()
                projections = {str(row["event_key"]).lower(): row for row in projection_rows}
            except sqlite3.OperationalError:
                projections = {}
        facts: list[FactEvidenceV1] = []
        for row in rows:
            event_key = bytes(row["event_key"]).hex()
            dimensions = FactDimensionsV1(
                instrument=str(row["instrument"]),
                event_type=str(row["event_type"]),
                side=str(row["side"]),
                settlement=str(row["settlement_term"]),
                trade_form=str(row["trade_form"]),
                price_value=str(row["price_value"]),
                price_unit=str(row["price_unit"]),
                quantity_value=(
                    str(row["quantity_value"])
                    if row["quantity_value"] is not None
                    else None
                ),
                quantity_unit=(
                    str(row["quantity_unit"])
                    if row["quantity_unit"] is not None
                    else None
                ),
            )
            projection = projections.get(event_key)
            parsed_at = (
                str(projection["parsed_at_utc"])
                if projection is not None
                else str(row["inserted_at_utc"])
            )
            transferred_at = (
                str(projection["transferred_at_utc"])
                if projection is not None
                else parsed_at
            )
            snapshot_at = (snapshot_times or {}).get(event_key)
            facts.append(
                FactEvidenceV1(
                    event_key=event_key,
                    source_code=str(row["source_code"]),
                    eligible=str(row["quality_state"]) == "ELIGIBLE",
                    dimensions=dimensions,
                    parser_fingerprint=content_hash(dimensions),
                    lifecycle_state=(
                        "TRADE_CONFIRMED"
                        if str(row["event_type"]) == "TRADE"
                        else "ACTIVE"
                        if str(row["event_type"]) == "OFFER"
                        else "OBSERVED"
                    ),
                    occurred_at_utc=str(row["event_time_utc"]),
                    available_at_utc=str(row["available_at_utc"]),
                    parsed_at_utc=parsed_at,
                    transferred_at_utc=transferred_at,
                    next_snapshot_at_utc=snapshot_at,
                )
            )
        captures = (
            tuple(CaptureEvidenceV1.model_validate(item) for item in capture_manifest)
            if capture_manifest is not None
            else tuple(
                CaptureEvidenceV1(
                    event_key=item.event_key,
                    source_code=item.source_code,
                    occurred_at_utc=item.occurred_at_utc,
                    available_at_utc=item.available_at_utc,
                )
                for item in facts
            )
        )
        resolved_snapshots = (
            tuple(
                SnapshotVersionEvidenceV1.model_validate(item)
                for item in snapshot_versions
            )
            if snapshot_versions is not None
            else (
                SnapshotVersionEvidenceV1(
                    snapshot_version=1,
                    evaluation_at_utc=end,
                ),
            )
        )
        features: list[FeatureEvidenceV1] = []
        estimates: list[EstimateEvidenceV1] = []
        for snapshot in resolved_snapshots:
            market = build_market_snapshot(
                connection, as_of_utc=snapshot.evaluation_at_utc
            )
            snapshot_features: list[FeatureEvidenceV1] = []
            for component, signal in sorted(market["signals"].items()):
                source_codes = tuple(
                    str(item) for item in signal.get("source_codes") or ()
                )
                event_key: str | None = None
                if source_codes and signal.get("last_event_utc"):
                    placeholders = ",".join("?" for _ in source_codes)
                    selected = connection.execute(
                        "SELECT event_key FROM market_observations "
                        f"WHERE source_code IN ({placeholders}) "
                        "AND event_time_utc=? ORDER BY id DESC LIMIT 1",
                        (*source_codes, str(signal["last_event_utc"])),
                    ).fetchone()
                    if selected is not None:
                        event_key = bytes(selected[0]).hex()
                status = str(signal.get("status") or "MISSING").upper()
                snapshot_features.append(
                    FeatureEvidenceV1(
                        evaluation_at_utc=snapshot.evaluation_at_utc,
                        component=component,
                        point_value=_decimal_text(signal.get("latest_price")),
                        mean_value=_decimal_text(signal.get("mean_price")),
                        unit=str(signal.get("price_unit") or "UNKNOWN_UNIT"),
                        sample_count=int(signal.get("observation_count") or 0),
                        source_event_key=event_key,
                        freshness=(
                            status
                            if status in {"FRESH", "STALE", "MISSING", "REJECTED"}
                            else "REJECTED"
                        ),
                    )
                )
            features.extend(snapshot_features)
            input_hash = feature_evidence_snapshot_hash(snapshot_features)
            estimates.extend(
                EstimateEvidenceV1(
                    evaluation_at_utc=snapshot.evaluation_at_utc,
                    model_artifact_hash=model_artifact_hash,
                    input_snapshot_hash=input_hash,
                    instrument="COIN_" + str(item["commodity_code"]),
                    settlement=str(item["settlement_term"]),
                    status=str(item["status"]),
                    value=(
                        str(item["estimated_project_price"])
                        if item["estimated_project_price"] is not None
                        else None
                    ),
                    lower_bound=(
                        str(item["lower_project_price"])
                        if item["lower_project_price"] is not None
                        else None
                    ),
                    upper_bound=(
                        str(item["upper_project_price"])
                        if item["upper_project_price"] is not None
                        else None
                    ),
                    reason_code=(
                        str(item.get("reason") or "NO_DATA_UNSPECIFIED")
                        if item["status"] == "NO_DATA"
                        else None
                    ),
                    unit="PROJECT_THOUSAND_TOMAN",
                    confidence=str(item.get("confidence") or "NONE"),
                    method=str(item.get("method") or "UNKNOWN_METHOD"),
                    underlying_source=(
                        str(item["underlying_source"])
                        if item.get("underlying_source") is not None
                        else None
                    ),
                    underlying_age_seconds=item.get("underlying_age_seconds"),
                    anchor_age_seconds=item.get("anchor_age_seconds"),
                    market_regime=str(item.get("market_regime") or "UNKNOWN"),
                )
                for item in market["rates"]["items"]
            )
        unresolved_gaps = 0
        rejected = 0
        checkpoints = 0
        duplicate_count: int | None = None
        duplicate_evidence = "NOT_APPLICABLE" if lane == "LEGACY" else "UNKNOWN"
        duplicate_receipt_hash: str | None = None
        duplicate_evidence_row_count: int | None = None
        if lane == "PRIVATE_SHADOW":
            try:
                streams = connection.execute(
                    """
                    SELECT stream_id,MIN(delivery_sequence),MAX(delivery_sequence),
                           COUNT(DISTINCT delivery_sequence),
                           SUM(CASE WHEN status='REJECTED' THEN 1 ELSE 0 END)
                    FROM private_fact_adapter_deliveries
                    WHERE applied_at_utc>=? AND applied_at_utc<=?
                    GROUP BY stream_id
                    """,
                    (_stamp(start), _stamp(end)),
                ).fetchall()
                for stream in streams:
                    unresolved_gaps += int(stream[2]) - int(stream[1]) + 1 - int(stream[3])
                    rejected += int(stream[4] or 0)
                checkpoints = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM private_fact_adapter_checkpoints"
                    ).fetchone()[0]
                )
                duplicate_count = int(
                    connection.execute(
                        """
                        SELECT COALESCE(SUM(delivery_count-1),0)
                        FROM (
                          SELECT COUNT(*) AS delivery_count
                          FROM private_fact_adapter_deliveries
                          WHERE status='APPLIED'
                            AND applied_at_utc>=? AND applied_at_utc<=?
                          GROUP BY fact_id,fact_revision
                          HAVING COUNT(*)>1
                        )
                        """,
                        (_stamp(start), _stamp(end)),
                    ).fetchone()[0]
                )
                duplicate_evidence_row_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM private_fact_adapter_deliveries
                        WHERE applied_at_utc>=? AND applied_at_utc<=?
                        """,
                        (_stamp(start), _stamp(end)),
                    ).fetchone()[0]
                )
                duplicate_evidence = "DELIVERY_LEDGER"
                duplicate_receipt_hash = content_hash(
                    {
                        "window_start_utc": _stamp(start),
                        "window_end_utc": _stamp(end),
                        "ledger_row_count": duplicate_evidence_row_count,
                        "duplicate_eligible_fact_count": duplicate_count,
                        "unresolved_sequence_gap_count": unresolved_gaps,
                        "rejected_delivery_count": rejected,
                    }
                )
            except sqlite3.OperationalError:
                unresolved_gaps = 1
                duplicate_count = None
                duplicate_evidence = "UNKNOWN"
                duplicate_receipt_hash = None
                duplicate_evidence_row_count = None
        return ShadowLaneEvidenceV1(
            contract="market_shadow_lane/1.0",
            lane=lane,
            session_id=session_id,
            window_start_utc=start,
            window_end_utc=end,
            capture_manifest_complete=capture_manifest is not None,
            model_artifact_hash=model_artifact_hash,
            captures=captures,
            capture_prefix=(
                ImmutableCapturePrefixEvidenceV1.model_validate(capture_prefix)
                if capture_prefix is not None
                else None
            ),
            capture_inventory=(
                tuple(
                    CaptureSourceInventoryEvidenceV1.model_validate(item)
                    for item in capture_inventory
                )
                if capture_inventory is not None
                else ()
            ),
            facts=tuple(facts),
            features=tuple(features),
            snapshot_versions=resolved_snapshots,
            estimates=tuple(estimates),
            transport=TransportEvidenceV1(
                unresolved_sequence_gap_count=unresolved_gaps,
                duplicate_eligible_fact_count=duplicate_count,
                duplicate_evidence=duplicate_evidence,
                duplicate_evidence_receipt_hash=duplicate_receipt_hash,
                duplicate_evidence_row_count=duplicate_evidence_row_count,
                rejected_delivery_count=rejected,
                receiver_checkpoint_count=checkpoints,
            ),
        )
    finally:
        connection.close()


def write_private_json(path: Path, document: Mapping[str, Any]) -> None:
    if path.exists():
        raise ShadowParityError("parity_output_exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)
