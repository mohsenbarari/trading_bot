"""Local, non-authoritative persistence for coin-intelligence Shadow runs.

These rows are evaluation evidence only.  They are deliberately disconnected
from product decisions and from the cross-site business event stream.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from .database import Base


class CoinIntelligenceShadowRun(Base):
    __tablename__ = "coin_intelligence_shadow_runs"
    __table_args__ = (
        CheckConstraint(
            "mode = 'shadow'",
            name="ck_coin_intelligence_shadow_runs_mode",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_coin_intelligence_shadow_runs_idempotency_key",
        ),
        Index(
            "ix_coin_intelligence_shadow_runs_subject",
            "subject_kind",
            "subject_fingerprint",
            "as_of_utc",
        ),
        Index(
            "ix_coin_intelligence_shadow_runs_component_time",
            "component",
            "as_of_utc",
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_coin_intelligence_shadow_runs_latency",
        ),
    )

    id = Column(String(36), primary_key=True)
    idempotency_key = Column(String(64), nullable=False)
    mode = Column(
        String(16),
        nullable=False,
        default="shadow",
        server_default="shadow",
    )
    component = Column(String(64), nullable=False)
    status = Column(String(40), nullable=False)
    source_surface = Column(String(32), nullable=False)
    subject_kind = Column(String(24), nullable=True)
    # Opaque SHA-256 only.  Raw offer/trade/Telegram identifiers are forbidden.
    subject_fingerprint = Column(String(64), nullable=True)
    as_of_utc = Column(DateTime(timezone=True), nullable=False)
    physical_site = Column(String(32), nullable=True)
    primary_version = Column(String(160), nullable=True)
    candidate_version = Column(String(160), nullable=True)
    feature_schema_version = Column(String(160), nullable=True)
    snapshot_version = Column(String(255), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    error_code = Column(String(96), nullable=True)
    # Project offers are test-like until an explicit review promotes a label.
    training_eligible = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class CoinIntelligenceShadowFeatureSnapshot(Base):
    __tablename__ = "coin_intelligence_shadow_feature_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            name="uq_coin_intelligence_shadow_feature_snapshots_run_id",
        ),
    )

    id = Column(Integer, primary_key=True)
    run_id = Column(
        String(36),
        ForeignKey("coin_intelligence_shadow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    schema_version = Column(String(160), nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    # Compact, normalized features only; no raw text or personal identifiers.
    features = Column(JSON, nullable=False)
    source_ages = Column(JSON, nullable=False, default=dict)
    missing_fields = Column(JSON, nullable=False, default=list)
    source_vintages = Column(JSON, nullable=False, default=dict)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class CoinIntelligenceShadowPrediction(Base):
    __tablename__ = "coin_intelligence_shadow_predictions"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "model_role",
            "candidate_name",
            name="uq_coin_intelligence_shadow_predictions_role",
        ),
        CheckConstraint(
            "lower_project_price IS NULL OR center_project_price IS NULL "
            "OR lower_project_price <= center_project_price",
            name="ck_coin_intelligence_shadow_predictions_lower",
        ),
        CheckConstraint(
            "upper_project_price IS NULL OR center_project_price IS NULL "
            "OR upper_project_price >= center_project_price",
            name="ck_coin_intelligence_shadow_predictions_upper",
        ),
        CheckConstraint(
            "center_project_price IS NULL OR center_project_price > 0",
            name="ck_coin_intelligence_shadow_predictions_center_positive",
        ),
        CheckConstraint(
            "lower_project_price IS NULL OR lower_project_price > 0",
            name="ck_coin_intelligence_shadow_predictions_lower_positive",
        ),
        CheckConstraint(
            "upper_project_price IS NULL OR upper_project_price > 0",
            name="ck_coin_intelligence_shadow_predictions_upper_positive",
        ),
        CheckConstraint(
            "is_authoritative = false",
            name="ck_coin_intelligence_shadow_predictions_non_authoritative",
        ),
        Index(
            "ix_coin_intelligence_shadow_predictions_market",
            "canonical_commodity",
            "settlement",
            "trade_form",
        ),
    )

    id = Column(Integer, primary_key=True)
    run_id = Column(
        String(36),
        ForeignKey("coin_intelligence_shadow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    model_role = Column(String(24), nullable=False)
    candidate_name = Column(String(64), nullable=False)
    commodity_id = Column(
        Integer,
        ForeignKey("commodities.id", ondelete="SET NULL"),
        nullable=True,
    )
    canonical_commodity = Column(String(80), nullable=True)
    settlement = Column(String(16), nullable=False)
    trade_form = Column(String(16), nullable=False)
    center_project_price = Column(BigInteger, nullable=True)
    lower_project_price = Column(BigInteger, nullable=True)
    upper_project_price = Column(BigInteger, nullable=True)
    confidence = Column(Float, nullable=True)
    confidence_label = Column(String(24), nullable=True)
    method = Column(String(160), nullable=True)
    gate_reason = Column(String(160), nullable=True)
    anchor_kind = Column(String(64), nullable=True)
    anchor_age_seconds = Column(Integer, nullable=True)
    is_authoritative = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    diagnostics = Column(JSON, nullable=False, default=dict)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class CoinIntelligenceShadowParserResult(Base):
    __tablename__ = "coin_intelligence_shadow_parser_results"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            name="uq_coin_intelligence_shadow_parser_results_run_id",
        ),
    )

    id = Column(Integer, primary_key=True)
    run_id = Column(
        String(36),
        ForeignKey("coin_intelligence_shadow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    current_commodity = Column(String(80), nullable=False)
    candidate_commodity = Column(String(80), nullable=True)
    agrees_with_current = Column(Boolean, nullable=True)
    requires_user_confirmation = Column(Boolean, nullable=False)
    decision_reason = Column(String(160), nullable=False)
    validator_status = Column(String(40), nullable=False)
    disagreement_fields = Column(JSON, nullable=False, default=list)
    candidate_payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class CoinIntelligenceShadowOutcome(Base):
    __tablename__ = "coin_intelligence_shadow_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "prediction_id",
            "outcome_version",
            name="uq_coin_intelligence_shadow_outcomes_prediction_version",
        ),
        Index(
            "ix_coin_intelligence_shadow_outcomes_market_time",
            "actual_commodity_id",
            "settlement",
            "trade_form",
            "occurred_at_utc",
        ),
        CheckConstraint(
            "outcome_version > 0",
            name="ck_coin_intelligence_shadow_outcomes_version_positive",
        ),
        CheckConstraint(
            "actual_project_price > 0",
            name="ck_coin_intelligence_shadow_outcomes_price_positive",
        ),
    )

    id = Column(Integer, primary_key=True)
    prediction_id = Column(
        Integer,
        ForeignKey(
            "coin_intelligence_shadow_predictions.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    outcome_version = Column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    subject_fingerprint = Column(String(64), nullable=False, index=True)
    actual_commodity_id = Column(
        Integer,
        ForeignKey("commodities.id", ondelete="SET NULL"),
        nullable=True,
    )
    settlement = Column(String(16), nullable=False)
    trade_form = Column(String(16), nullable=False)
    actual_project_price = Column(BigInteger, nullable=False)
    occurred_at_utc = Column(DateTime(timezone=True), nullable=False)
    label_status = Column(String(32), nullable=False)
    review_reason = Column(String(160), nullable=True)
    absolute_percent_error = Column(Float, nullable=True)
    signed_percent_error = Column(Float, nullable=True)
    interval_covered = Column(Boolean, nullable=True)
    scoring_policy_version = Column(String(80), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
