"""Redacted Stage 12 parity evidence for legacy and private market feeds."""

from __future__ import annotations

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

from .market_snapshot import build_market_snapshot
from .private_pipeline_contracts import Code, Hex64, content_hash


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


class EstimateEvidenceV1(_Contract):
    evaluation_at_utc: AwareDatetime
    model_artifact_hash: Hex64
    input_snapshot_hash: Hex64
    instrument: Code
    settlement: Literal["CASH", "TOMORROW"]
    value: str
    lower_bound: str
    upper_bound: str

    @field_validator("evaluation_at_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_range(self) -> "EstimateEvidenceV1":
        lower = _decimal(self.lower_bound)
        value = _decimal(self.value)
        upper = _decimal(self.upper_bound)
        if lower is None or value is None or upper is None or not lower <= value <= upper:
            raise ValueError("estimate_range_invalid")
        return self


class TransportEvidenceV1(_Contract):
    unresolved_sequence_gap_count: int = Field(ge=0)
    duplicate_eligible_fact_count: int = Field(ge=0)
    rejected_delivery_count: int = Field(ge=0)
    receiver_checkpoint_count: int = Field(ge=0)


class ShadowLaneEvidenceV1(_Contract):
    contract: Literal["market_shadow_lane/1.0"]
    lane: Literal["LEGACY", "PRIVATE_SHADOW"]
    window_start_utc: AwareDatetime
    window_end_utc: AwareDatetime
    capture_manifest_complete: bool
    model_artifact_hash: Hex64
    captures: tuple[CaptureEvidenceV1, ...]
    facts: tuple[FactEvidenceV1, ...]
    features: tuple[FeatureEvidenceV1, ...]
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


class FailureSoakEvidenceV1(_Contract):
    contract: Literal["market_failure_soak/1.0"]
    evidence_mode: Literal["HISTORICAL_REPLAY", "LIVE_OPEN_MARKET"]
    started_at_utc: AwareDatetime
    completed_at_utc: AwareDatetime
    full_market_session: bool
    receiver_restart_passed: bool
    route_partition_passed: bool
    lost_ack_passed: bool
    rollback_passed: bool
    disk_failure_passed: bool

    @field_validator("started_at_utc", "completed_at_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_window(self) -> "FailureSoakEvidenceV1":
        if self.completed_at_utc <= self.started_at_utc:
            raise ValueError("failure_soak_window_invalid")
        return self


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
    if legacy.lane != "LEGACY" or private.lane != "PRIVATE_SHADOW":
        raise ShadowParityError("parity_lane_role_invalid")
    if (legacy.window_start_utc, legacy.window_end_utc) != (
        private.window_start_utc,
        private.window_end_utc,
    ):
        raise ShadowParityError("parity_window_mismatch")

    issues: list[dict[str, Any]] = []
    if not legacy.capture_manifest_complete or not private.capture_manifest_complete:
        issues.append(_issue("CAPTURE", "CAPTURE_MANIFEST_INCOMPLETE", 1))
    if legacy.model_artifact_hash != private.model_artifact_hash:
        issues.append(_issue("ESTIMATOR", "MODEL_ARTIFACT_MISMATCH", 1))

    legacy_capture = {item.event_key: item for item in legacy.captures}
    private_capture = {item.event_key: item for item in private.captures}
    for key in sorted(set(legacy_capture) - set(private_capture)):
        issues.append(_issue("CAPTURE", "PRIVATE_CAPTURE_MISSING", 1, event_key=key))

    legacy_facts = {item.event_key: item for item in legacy.facts}
    private_facts = {item.event_key: item for item in private.facts}
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
        (transport.duplicate_eligible_fact_count, "DUPLICATE_ELIGIBLE_FACT"),
        (transport.rejected_delivery_count, "REJECTED_PRIVATE_DELIVERY"),
    ):
        if value:
            issues.append(_issue("TRANSPORT", code, 1))

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
        if (
            left.model_artifact_hash == right.model_artifact_hash
            and left.input_snapshot_hash == right.input_snapshot_hash
            and (
                _decimal(left.value),
                _decimal(left.lower_bound),
                _decimal(left.upper_bound),
            )
            != (
                _decimal(right.value),
                _decimal(right.lower_bound),
                _decimal(right.upper_bound),
            )
        ):
            estimator_mismatches += 1
            issues.append(_issue("ESTIMATOR", "SAME_INPUT_OUTPUT_MISMATCH", 1))

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

    failure_drills_passed = all(
        (
            soak.receiver_restart_passed,
            soak.route_partition_passed,
            soak.lost_ack_passed,
            soak.rollback_passed,
            soak.disk_failure_passed,
        )
    )
    if not failure_drills_passed:
        issues.append(_issue("TRANSPORT", "FAILURE_SOAK_INCOMPLETE", 1))
    live_open_market_passed = (
        soak.evidence_mode == "LIVE_OPEN_MARKET"
        and soak.full_market_session
        and failure_drills_passed
    )
    blocking = [item for item in issues if item["severity"] in {1, 2}]
    recommendation = (
        "PROMOTE_PRIVATE_PRIMARY"
        if not blocking and live_open_market_passed
        else "HOLD_LIVE_OPEN_MARKET_REQUIRED"
        if not blocking
        else "HOLD_BLOCKING_PARITY_FINDINGS"
    )
    return {
        "contract": "market_shadow_parity_report/1.0",
        "window_start_utc": legacy.window_start_utc.isoformat(),
        "window_end_utc": legacy.window_end_utc.isoformat(),
        "legacy_capture_count": len(legacy.captures),
        "private_capture_count": len(private.captures),
        "legacy_fact_count": len(legacy.facts),
        "private_fact_count": len(private.facts),
        "private_only_capture_count": len(set(private_capture) - set(legacy_capture)),
        "private_capture_loss_count": len(set(legacy_capture) - set(private_capture)),
        "duplicate_eligible_fact_count": transport.duplicate_eligible_fact_count,
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
    lane: Literal["LEGACY", "PRIVATE_SHADOW"],
    window_start_utc: datetime,
    window_end_utc: datetime,
    model_artifact_hash: str,
    capture_manifest: Sequence[Mapping[str, Any]] | None = None,
    snapshot_times: Mapping[str, datetime] | None = None,
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
        market = build_market_snapshot(connection, as_of_utc=end)
        features: list[FeatureEvidenceV1] = []
        for component, signal in sorted(market["signals"].items()):
            source_codes = tuple(str(item) for item in signal.get("source_codes") or ())
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
            features.append(
                FeatureEvidenceV1(
                    evaluation_at_utc=end,
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
        input_hash = content_hash([item.model_dump(mode="json") for item in features])
        estimates = tuple(
            EstimateEvidenceV1(
                evaluation_at_utc=end,
                model_artifact_hash=model_artifact_hash,
                input_snapshot_hash=input_hash,
                instrument="COIN_" + str(item["commodity_code"]),
                settlement=str(item["settlement_term"]),
                value=str(item["estimated_project_price"]),
                lower_bound=str(item["lower_project_price"]),
                upper_bound=str(item["upper_project_price"]),
            )
            for item in market["rates"]["items"]
            if item["status"] == "ESTIMATED"
        )
        unresolved_gaps = 0
        rejected = 0
        checkpoints = 0
        if lane == "PRIVATE_SHADOW":
            try:
                streams = connection.execute(
                    """
                    SELECT stream_id,MIN(delivery_sequence),MAX(delivery_sequence),
                           COUNT(DISTINCT delivery_sequence),
                           SUM(CASE WHEN status='REJECTED' THEN 1 ELSE 0 END)
                    FROM private_fact_adapter_deliveries GROUP BY stream_id
                    """
                ).fetchall()
                for stream in streams:
                    unresolved_gaps += int(stream[2]) - int(stream[1]) + 1 - int(stream[3])
                    rejected += int(stream[4] or 0)
                checkpoints = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM private_fact_adapter_checkpoints"
                    ).fetchone()[0]
                )
            except sqlite3.OperationalError:
                unresolved_gaps = 1
        return ShadowLaneEvidenceV1(
            contract="market_shadow_lane/1.0",
            lane=lane,
            window_start_utc=start,
            window_end_utc=end,
            capture_manifest_complete=capture_manifest is not None,
            model_artifact_hash=model_artifact_hash,
            captures=captures,
            facts=tuple(facts),
            features=tuple(features),
            estimates=estimates,
            transport=TransportEvidenceV1(
                unresolved_sequence_gap_count=unresolved_gaps,
                duplicate_eligible_fact_count=0,
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
