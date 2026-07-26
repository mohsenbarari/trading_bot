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


class CoinIntelligenceShadowJob(Base):
    """Local durable work item; payloads contain normalized IDs only."""

    __tablename__ = "coin_intelligence_shadow_jobs"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_coin_intelligence_shadow_jobs_idempotency_key",
        ),
        CheckConstraint(
            "status IN ('PENDING','PROCESSING','COMPLETE','FAILED')",
            name="ck_coin_intelligence_shadow_jobs_status",
        ),
        CheckConstraint(
            "attempts >= 0 AND max_attempts > 0",
            name="ck_coin_intelligence_shadow_jobs_attempts",
        ),
        CheckConstraint(
            "job_kind IN ('PROJECT_OFFER','PROJECT_TRADE')",
            name="ck_coin_intelligence_shadow_jobs_kind",
        ),
        CheckConstraint(
            "local_id > 0",
            name="ck_coin_intelligence_shadow_jobs_local_id_positive",
        ),
        Index(
            "ix_coin_intelligence_shadow_jobs_claim",
            "status",
            "available_at",
            "lease_expires_at",
        ),
    )

    id = Column(String(36), primary_key=True)
    idempotency_key = Column(String(64), nullable=False)
    job_kind = Column(String(32), nullable=False)
    local_id = Column(Integer, nullable=False)
    # Normalized bounded fields only. Raw offer text is forbidden.
    payload = Column(JSON, nullable=False, default=dict)
    requested_at_utc = Column(DateTime(timezone=True), nullable=False)
    status = Column(
        String(16),
        nullable=False,
        default="PENDING",
        server_default="PENDING",
    )
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    max_attempts = Column(
        Integer,
        nullable=False,
        default=5,
        server_default="5",
    )
    available_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    worker_token = Column(String(64), nullable=True)
    error_code = Column(String(96), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class CoinIntelligenceShadowQualityDecision(Base):
    """Immutable quality decision made at the prediction cutoff."""

    __tablename__ = "coin_intelligence_shadow_quality_decisions"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            name="uq_coin_intelligence_shadow_quality_run_id",
        ),
        CheckConstraint(
            "decision IN "
            "('EXCLUDE','REVIEW_REQUIRED','INCLUDE_SHADOW')",
            name="ck_coin_intelligence_shadow_quality_decision",
        ),
        CheckConstraint(
            "realtime_weight >= 0 AND realtime_weight <= 1 "
            "AND training_weight >= 0 AND training_weight <= 1",
            name="ck_coin_intelligence_shadow_quality_weights",
        ),
    )

    id = Column(Integer, primary_key=True)
    run_id = Column(
        String(36),
        ForeignKey("coin_intelligence_shadow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    policy_version = Column(String(80), nullable=False)
    decision = Column(String(32), nullable=False)
    reason_codes = Column(JSON, nullable=False, default=list)
    realtime_weight = Column(Float, nullable=False)
    training_weight = Column(Float, nullable=False)
    review_required = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    context = Column(JSON, nullable=False, default=dict)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class CoinIntelligenceShadowReview(Base):
    """Append-only human decision over one immutable outcome."""

    __tablename__ = "coin_intelligence_shadow_reviews"
    __table_args__ = (
        UniqueConstraint(
            "outcome_id",
            "review_version",
            name="uq_coin_intelligence_shadow_reviews_version",
        ),
        CheckConstraint(
            "review_version > 0",
            name="ck_coin_intelligence_shadow_reviews_version_positive",
        ),
        CheckConstraint(
            "corrected_project_price IS NULL "
            "OR corrected_project_price > 0",
            name="ck_coin_intelligence_shadow_reviews_price_positive",
        ),
        CheckConstraint(
            "action IN "
            "('ACCEPT_ORIGINAL','ACCEPT_CORRECTION','REJECT_LABEL',"
            "'KEEP_UNREVIEWED','AMBIGUOUS')",
            name="ck_coin_intelligence_shadow_reviews_action",
        ),
        CheckConstraint(
            "(action = 'ACCEPT_CORRECTION' "
            "AND corrected_project_price IS NOT NULL) "
            "OR (action <> 'ACCEPT_CORRECTION' "
            "AND corrected_project_price IS NULL)",
            name="ck_coin_intelligence_shadow_reviews_correction_shape",
        ),
    )

    id = Column(Integer, primary_key=True)
    outcome_id = Column(
        Integer,
        ForeignKey("coin_intelligence_shadow_outcomes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    review_version = Column(Integer, nullable=False)
    action = Column(String(32), nullable=False)
    reviewer_fingerprint = Column(String(64), nullable=False)
    corrected_project_price = Column(BigInteger, nullable=True)
    reason_code = Column(String(96), nullable=False)
    # Coded/bounded metadata only; no raw offer text or free-form user data.
    note_code = Column(String(96), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
