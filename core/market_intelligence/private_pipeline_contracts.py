"""Versioned contracts for the private cross-server market-data pipeline.

These contracts are independent from the legacy product sync and its database.
Raw capture records stay on the web/data host for bounded retention.  Only
privacy-minimized ``MarketFactV1`` objects may enter a transport batch.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping, Sequence

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


CONTRACT_VERSION = "1.0"
DEFAULT_SOURCE_REGISTRY = (
    Path(__file__).resolve().parents[2] / "config" / "market_data_sources.v1.json"
)

Hex64 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Code = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")]
StreamId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){1,15}$"),
]
DecimalText = Annotated[
    str,
    StringConstraints(pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"),
]
UnitCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")]
ReasonCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{1,95}$")]

QualityState = Literal["ELIGIBLE", "REVIEW", "REJECTED", "AUDIT_ONLY"]
Side = Literal["BUY", "SELL", "MID", "UNKNOWN"]
Settlement = Literal["CASH", "TODAY", "TOMORROW", "SPOT", "UNKNOWN"]
TradeForm = Literal[
    "PHYSICAL",
    "PAPER_NORMAL",
    "PAPER_REVERSE",
    "PAPER_SWIM",
    "NOT_APPLICABLE",
    "UNKNOWN",
]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        protected_namespaces=(),
    )


def canonical_json(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def content_hash(value: Any) -> str:
    return sha256(canonical_json(value)).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone_required")
    return value.astimezone(timezone.utc)


class SourceDefinitionV1(ContractModel):
    source_code: Code
    capture_stream_id: StreamId | None
    fact_stream_id: StreamId
    source_family: Literal[
        "TELEGRAM_GROUP",
        "TELEGRAM_PUBLIC",
        "TELEGRAM_PRIVATE",
        "EXTERNAL_API",
        "DERIVED",
        "RESERVED",
    ]
    upstream_schema: str
    upstream_schema_version: str
    parser_profile: Code
    capture_enabled: bool
    permanent_archive: bool
    raw_retention_seconds: int = Field(ge=0, le=604_800)
    transfer_to_bot: bool
    pii_classification: Literal["NONE", "LOW", "MEDIUM", "HIGH"]
    allowed_fact_kinds: tuple[
        Literal[
            "OBSERVATION",
            "COIN_OFFER",
            "COIN_TRADE",
            "PRIVATE_GOLD_OFFER",
            "PRIVATE_GOLD_OUTCOME",
            "EXTERNAL_QUOTE",
        ],
        ...,
    ]

    @model_validator(mode="after")
    def validate_capture_contract(self) -> "SourceDefinitionV1":
        if self.capture_enabled and self.capture_stream_id is None:
            raise ValueError("enabled_source_requires_capture_stream")
        if self.source_family in {"DERIVED", "RESERVED"} and self.capture_stream_id:
            raise ValueError("derived_or_reserved_source_cannot_capture")
        if self.capture_stream_id and self.raw_retention_seconds != 259_200:
            raise ValueError("capture_raw_retention_must_be_three_days")
        return self


class SourceRegistryV1(ContractModel):
    contract: Literal["market_source_registry/1.0"]
    sources: tuple[SourceDefinitionV1, ...]

    @model_validator(mode="after")
    def validate_uniqueness(self) -> "SourceRegistryV1":
        codes = [item.source_code for item in self.sources]
        fact_streams = [item.fact_stream_id for item in self.sources]
        capture_streams = [
            item.capture_stream_id for item in self.sources if item.capture_stream_id
        ]
        if len(codes) != len(set(codes)):
            raise ValueError("duplicate_source_code")
        if len(fact_streams) != len(set(fact_streams)):
            raise ValueError("duplicate_fact_stream_id")
        if len(capture_streams) != len(set(capture_streams)):
            raise ValueError("duplicate_capture_stream_id")
        return self

    def by_code(self) -> dict[str, SourceDefinitionV1]:
        return {item.source_code: item for item in self.sources}


def load_source_registry(path: Path = DEFAULT_SOURCE_REGISTRY) -> SourceRegistryV1:
    return SourceRegistryV1.model_validate_json(path.read_text(encoding="utf-8"))


class MarketCaptureRecordV1(ContractModel):
    contract: Literal["market_capture_record/1.0"]
    upstream_schema: str
    upstream_schema_version: str
    event_key: Hex64
    upstream_event_id: str = Field(min_length=8, max_length=192)
    source_code: Code
    stream_id: StreamId
    source_sequence: int = Field(ge=1)
    event_type: Literal[
        "MESSAGE_CREATED",
        "MESSAGE_SNAPSHOT",
        "MESSAGE_EDITED",
        "MESSAGE_DELETED",
        "QUOTE_OBSERVED",
    ]
    occurred_at_utc: AwareDatetime
    available_at_utc: AwareDatetime
    persisted_at_utc: AwareDatetime
    retention_until_utc: AwareDatetime
    payload_hash: Hex64
    retention_class: Literal["RAW_3D"]
    contains_pii: bool
    raw_payload: dict[str, Any]

    @field_validator(
        "occurred_at_utc",
        "available_at_utc",
        "persisted_at_utc",
        "retention_until_utc",
    )
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_semantics(self) -> "MarketCaptureRecordV1":
        if self.available_at_utc < self.occurred_at_utc:
            raise ValueError("availability_before_occurrence")
        if self.persisted_at_utc < self.available_at_utc:
            raise ValueError("persistence_before_availability")
        lifetime = self.retention_until_utc - self.persisted_at_utc
        if not 259_199 <= lifetime.total_seconds() <= 259_201:
            raise ValueError("raw_retention_not_three_days")
        if self.payload_hash != content_hash(self.raw_payload):
            raise ValueError("capture_payload_hash_mismatch")
        source = load_source_registry().by_code().get(self.source_code)
        if source is None or not source.capture_enabled:
            raise ValueError("capture_source_not_enabled")
        if source.capture_stream_id != self.stream_id:
            raise ValueError("capture_stream_source_mismatch")
        if (source.upstream_schema, source.upstream_schema_version) != (
            self.upstream_schema,
            self.upstream_schema_version,
        ):
            raise ValueError("capture_upstream_contract_mismatch")
        return self


class QuantityFields(ContractModel):
    quantity_value: DecimalText | None = None
    quantity_unit: UnitCode | None = None

    @model_validator(mode="after")
    def validate_quantity_pair(self) -> "QuantityFields":
        if (self.quantity_value is None) != (self.quantity_unit is None):
            raise ValueError("quantity_value_and_unit_must_pair")
        return self


class ObservationPayload(QuantityFields):
    kind: Literal["OBSERVATION"]
    instrument: Code
    event_type: Literal["OFFER", "TRADE", "QUOTE", "REFERENCE"]
    side: Side
    settlement: Settlement
    trade_form: TradeForm
    price_value: DecimalText
    price_unit: UnitCode
    currency: Literal["TOMAN", "USD"]


class CoinOfferPayload(QuantityFields):
    kind: Literal["COIN_OFFER"]
    group_code: Literal[1, 2]
    instrument: Code
    side: Literal["BUY", "SELL"]
    settlement: Literal["CASH", "TOMORROW"]
    trade_form: TradeForm
    offered_price_value: DecimalText
    price_unit: Literal["PROJECT_THOUSAND_TOMAN", "TOMAN_PER_COIN"]


class CoinTradePayload(ContractModel):
    kind: Literal["COIN_TRADE"]
    offer_fact_id: Hex64
    outcome: Literal[
        "CONFIRMED_FULL",
        "CONFIRMED_PARTIAL",
        "REJECTED",
        "AMBIGUOUS",
    ]
    agreed_price_value: DecimalText | None = None
    price_unit: Literal["PROJECT_THOUSAND_TOMAN", "TOMAN_PER_COIN"] | None = None
    agreed_quantity_value: DecimalText | None = None
    quantity_unit: UnitCode | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> "CoinTradePayload":
        agreed = self.outcome in {"CONFIRMED_FULL", "CONFIRMED_PARTIAL"}
        values = (
            self.agreed_price_value,
            self.price_unit,
            self.agreed_quantity_value,
            self.quantity_unit,
        )
        if agreed and any(item is None for item in values):
            raise ValueError("confirmed_coin_trade_requires_agreed_price_and_quantity")
        if not agreed and any(item is not None for item in values):
            raise ValueError("unconfirmed_coin_trade_cannot_publish_economic_terms")
        return self


class PrivateGoldOfferPayload(QuantityFields):
    kind: Literal["PRIVATE_GOLD_OFFER"]
    instrument: Literal["MELTED_GOLD_PRIVATE"]
    side: Literal["BUY", "SELL"]
    settlement: Settlement
    trade_form: TradeForm
    offered_price_value: DecimalText
    price_unit: Literal["TOMAN_PER_MESGHAL_750"]
    lifetime_seconds: Literal[120]


class PrivateGoldOutcomePayload(ContractModel):
    kind: Literal["PRIVATE_GOLD_OUTCOME"]
    offer_fact_id: Hex64
    outcome: Literal["FULL", "PARTIAL", "NO_TRADE", "AMBIGUOUS"]
    executed_quantity_value: DecimalText | None = None
    remaining_quantity_value: DecimalText | None = None
    quantity_unit: UnitCode | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> "PrivateGoldOutcomePayload":
        if self.outcome == "FULL" and self.remaining_quantity_value not in {None, "0"}:
            raise ValueError("full_outcome_cannot_have_positive_remaining_quantity")
        if self.outcome == "PARTIAL" and (
            self.executed_quantity_value is None
            and self.remaining_quantity_value is None
        ):
            raise ValueError("partial_outcome_requires_quantity_evidence")
        if self.outcome in {"NO_TRADE", "AMBIGUOUS"} and any(
            value is not None
            for value in (
                self.executed_quantity_value,
                self.remaining_quantity_value,
                self.quantity_unit,
            )
        ):
            raise ValueError("non_trade_outcome_cannot_publish_quantity")
        if (
            self.executed_quantity_value is not None
            or self.remaining_quantity_value is not None
        ) and self.quantity_unit is None:
            raise ValueError("outcome_quantity_unit_required")
        return self


class ExternalQuotePayload(ContractModel):
    kind: Literal["EXTERNAL_QUOTE"]
    instrument: Literal["XAUUSD", "USD_HERAT", "USDT_IRT"]
    quote_kind: Literal["BID", "ASK", "MID", "LAST"]
    price_value: DecimalText
    price_unit: Literal[
        "USD_PER_TROY_OUNCE",
        "TOMAN_PER_USD",
        "TOMAN_PER_USDT",
    ]
    currency: Literal["TOMAN", "USD"]

    @model_validator(mode="after")
    def validate_instrument_unit(self) -> "ExternalQuotePayload":
        expected = {
            "XAUUSD": ("USD_PER_TROY_OUNCE", "USD"),
            "USD_HERAT": ("TOMAN_PER_USD", "TOMAN"),
            "USDT_IRT": ("TOMAN_PER_USDT", "TOMAN"),
        }[self.instrument]
        if (self.price_unit, self.currency) != expected:
            raise ValueError("external_quote_instrument_unit_mismatch")
        return self


FactPayload = Annotated[
    ObservationPayload
    | CoinOfferPayload
    | CoinTradePayload
    | PrivateGoldOfferPayload
    | PrivateGoldOutcomePayload
    | ExternalQuotePayload,
    Field(discriminator="kind"),
]


class MarketFactV1(ContractModel):
    contract: Literal["market_fact/1.0"]
    fact_id: Hex64
    event_key: Hex64
    origin_event_key: Hex64
    source_code: Code
    stream_id: StreamId
    source_sequence: int = Field(ge=1)
    occurred_at_utc: AwareDatetime
    available_at_utc: AwareDatetime
    persisted_at_utc: AwareDatetime
    schema_version: Literal["1.0"]
    parser_version: str = Field(min_length=1, max_length=96)
    fact_revision: int = Field(ge=1)
    quality_state: QualityState
    quality_reason_codes: tuple[ReasonCode, ...] = ()
    payload_hash: Hex64
    payload: FactPayload

    @field_validator("occurred_at_utc", "available_at_utc", "persisted_at_utc")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_semantics(self) -> "MarketFactV1":
        if self.available_at_utc < self.occurred_at_utc:
            raise ValueError("availability_before_occurrence")
        if self.persisted_at_utc < self.available_at_utc:
            raise ValueError("persistence_before_availability")
        if self.payload_hash != content_hash(self.payload):
            raise ValueError("fact_payload_hash_mismatch")
        source = load_source_registry().by_code().get(self.source_code)
        if source is None or not source.transfer_to_bot:
            raise ValueError("fact_source_not_transferable")
        if source.fact_stream_id != self.stream_id:
            raise ValueError("fact_stream_source_mismatch")
        if self.payload.kind not in source.allowed_fact_kinds:
            raise ValueError("fact_kind_not_allowed_for_source")
        return self


class MarketFactBatchV1(ContractModel):
    contract: Literal["market_fact_batch/1.0"]
    batch_id: Hex64
    schema_version: Literal["1.0"]
    stream_id: StreamId
    first_sequence: int = Field(ge=1)
    last_sequence: int = Field(ge=1)
    created_at_utc: AwareDatetime
    item_count: int = Field(ge=1, le=500)
    items_hash: Hex64
    sender_instance_id: Annotated[
        str, StringConstraints(pattern=r"^[a-z][a-z0-9._-]{2,63}$")
    ]
    items: tuple[MarketFactV1, ...]

    @field_validator("created_at_utc")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_batch(self) -> "MarketFactBatchV1":
        if self.item_count != len(self.items):
            raise ValueError("batch_item_count_mismatch")
        sequences = [item.source_sequence for item in self.items]
        if any(item.stream_id != self.stream_id for item in self.items):
            raise ValueError("batch_stream_mismatch")
        if sequences != list(range(self.first_sequence, self.last_sequence + 1)):
            raise ValueError("batch_sequence_not_contiguous")
        if self.items_hash != content_hash(
            [item.model_dump(mode="json") for item in self.items]
        ):
            raise ValueError("batch_items_hash_mismatch")
        return self


class MarketFactAckV1(ContractModel):
    contract: Literal["market_fact_ack/1.0"]
    batch_id: Hex64
    stream_id: StreamId
    status: Literal["ACK", "PARTIAL", "REJECTED"]
    highest_contiguous_sequence: int = Field(ge=0)
    received_count: int = Field(ge=0, le=500)
    accepted_count: int = Field(ge=0, le=500)
    duplicate_count: int = Field(ge=0, le=500)
    rejected_count: int = Field(ge=0, le=500)
    rejection_reason_codes: tuple[ReasonCode, ...] = ()
    receiver_timestamp_utc: AwareDatetime

    @field_validator("receiver_timestamp_utc")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_counts(self) -> "MarketFactAckV1":
        if self.accepted_count + self.duplicate_count + self.rejected_count != self.received_count:
            raise ValueError("ack_count_mismatch")
        if self.status == "ACK" and self.rejected_count:
            raise ValueError("ack_cannot_contain_rejection")
        return self


class EstimatorRateV1(ContractModel):
    instrument: Code
    settlement: Literal["CASH", "TOMORROW"]
    value: DecimalText
    unit: Literal["PROJECT_THOUSAND_TOMAN", "TOMAN_PER_COIN"]
    lower_bound: DecimalText
    upper_bound: DecimalText
    confidence: float = Field(ge=0, le=1)
    method: Code


class EstimatorInputHealthV1(ContractModel):
    component: Code
    status: Literal["FRESH", "STALE", "MISSING", "REJECTED"]
    latest_available_at_utc: AwareDatetime | None
    age_seconds: float | None = Field(default=None, ge=0)
    reason_codes: tuple[ReasonCode, ...] = ()

    @field_validator("latest_available_at_utc")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None


class EstimatorSnapshotV1(ContractModel):
    contract: Literal["estimator_snapshot/1.0"]
    snapshot_id: Hex64
    snapshot_version: int = Field(ge=1)
    generated_at_utc: AwareDatetime
    input_snapshot_hash: Hex64
    model_version: str = Field(min_length=1, max_length=128)
    status: Literal["OK", "SAFE_NO_DATA", "FAILURE"]
    rates: tuple[EstimatorRateV1, ...]
    health: tuple[EstimatorInputHealthV1, ...]
    reason_codes: tuple[ReasonCode, ...] = ()

    @field_validator("generated_at_utc")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def validate_status(self) -> "EstimatorSnapshotV1":
        if self.status == "OK" and not self.rates:
            raise ValueError("ok_snapshot_requires_rates")
        if self.status != "OK" and self.rates:
            raise ValueError("non_ok_snapshot_cannot_publish_rates")
        return self


SCHEMA_MODELS: Mapping[str, type[BaseModel]] = {
    "market_capture_record-1.0.schema.json": MarketCaptureRecordV1,
    "market_fact-1.0.schema.json": MarketFactV1,
    "market_fact_batch-1.0.schema.json": MarketFactBatchV1,
    "market_fact_ack-1.0.schema.json": MarketFactAckV1,
    "estimator_snapshot-1.0.schema.json": EstimatorSnapshotV1,
    "market_source_registry-1.0.schema.json": SourceRegistryV1,
}


def exported_schemas() -> dict[str, dict[str, Any]]:
    return {
        name: model.model_json_schema()
        for name, model in SCHEMA_MODELS.items()
    }


def batch_items_hash(items: Sequence[MarketFactV1]) -> str:
    return content_hash([item.model_dump(mode="json") for item in items])
