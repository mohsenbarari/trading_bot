"""Append-only, privacy-minimized audit records for product coin inference."""

from sqlalchemy import CheckConstraint, Column, DateTime, Index, Integer, String, text
from sqlalchemy.sql import func

from .database import Base


class CoinIntelligenceInferenceAudit(Base):
    """One idempotent inference decision, without offer text or user identity."""

    __tablename__ = "coin_intelligence_inference_audits"
    __table_args__ = (
        CheckConstraint(
            "source_surface IN ('WEBAPP', 'TELEGRAM_BOT', 'INTERNAL')",
            name="ck_coin_intelligence_inference_audit_source_surface",
        ),
        CheckConstraint(
            "decision_status IN ('AUTO_SELECT', 'CONFIRM', 'ABSTAIN')",
            name="ck_coin_intelligence_inference_audit_decision_status",
        ),
        CheckConstraint(
            "settlement_term IN ('CASH', 'TOMORROW')",
            name="ck_coin_intelligence_inference_audit_settlement_term",
        ),
        CheckConstraint(
            "candidate_scope IN ('ALL', 'LOW_DATE_ONLY')",
            name="ck_coin_intelligence_inference_audit_candidate_scope",
        ),
        CheckConstraint(
            "market_regime IN ('NORMAL', 'UP', 'DOWN', 'VOLATILE', 'UNKNOWN')",
            name="ck_coin_infer_audit_market_regime",
        ),
        CheckConstraint(
            "submitted_project_price > 0",
            name="ck_coin_intelligence_inference_audit_price_positive",
        ),
        CheckConstraint(
            "candidate_count >= 0",
            name="ck_coin_intelligence_inference_audit_candidate_count",
        ),
        CheckConstraint(
            "selected_commodity_id IS NULL OR selected_commodity_id > 0",
            name="ck_coin_infer_audit_selected_commodity_positive",
        ),
        CheckConstraint(
            "(decision_status = 'AUTO_SELECT' AND candidate_count = 1 "
            "AND selected_commodity_id IS NOT NULL "
            "AND selected_commodity_code IS NOT NULL "
            "AND selected_commodity_name IS NOT NULL) "
            "OR (decision_status = 'CONFIRM' AND candidate_count >= 1 "
            "AND selected_commodity_id IS NULL "
            "AND selected_commodity_code IS NULL "
            "AND selected_commodity_name IS NULL) "
            "OR (decision_status = 'ABSTAIN' AND candidate_count = 0 "
            "AND selected_commodity_id IS NULL "
            "AND selected_commodity_code IS NULL "
            "AND selected_commodity_name IS NULL)",
            name="ck_coin_intelligence_inference_audit_decision_shape",
        ),
        Index(
            "ix_coin_intelligence_inference_audit_created_status",
            "created_at",
            "decision_status",
        ),
    )

    id = Column(Integer, primary_key=True)
    decision_key = Column(String(64), nullable=False, unique=True, index=True)
    source_surface = Column(String(16), nullable=False)
    decision_status = Column(String(16), nullable=False)
    reason_code = Column(String(96), nullable=True)
    settlement_term = Column(String(16), nullable=False)
    candidate_scope = Column(String(16), nullable=False, default="ALL")
    submitted_project_price = Column(Integer, nullable=False)
    candidate_count = Column(Integer, nullable=False)
    selected_commodity_id = Column(Integer, nullable=True)
    selected_commodity_code = Column(String(32), nullable=True)
    selected_commodity_name = Column(String(96), nullable=True)
    inference_version = Column(String(64), nullable=False)
    catalog_resolution_version = Column(String(64), nullable=False)
    snapshot_receipt = Column(String(64), nullable=True)
    snapshot_generated_at_utc = Column(DateTime(timezone=True), nullable=True)
    dominant_underlying_source = Column(String(64), nullable=True)
    market_regime = Column(
        String(16), nullable=False, default="UNKNOWN", server_default=text("'UNKNOWN'")
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
