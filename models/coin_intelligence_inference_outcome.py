"""Privacy-minimized, append-only outcomes for price-based commodity inference."""

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from .database import Base


class CoinIntelligenceInferenceOutcome(Base):
    """One accepted inferred-commodity choice, with no offer or user linkage.

    The decision receipt is already opaque.  Keeping this outcome in a separate
    append-only table lets rollout measurement compare a proposed decision with
    the commodity ultimately accepted by the product without storing raw offer
    text, Telegram data, user identity, or an offer identifier.
    """

    __tablename__ = "coin_intelligence_inference_outcomes"
    __table_args__ = (
        CheckConstraint(
            "source_surface IN ('WEBAPP', 'TELEGRAM_BOT')",
            name="ck_coin_infer_outcome_source_surface",
        ),
        CheckConstraint(
            "outcome_kind = 'OFFER_ACCEPTED_SELECTION'",
            name="ck_coin_infer_outcome_kind",
        ),
        CheckConstraint(
            "selected_commodity_id > 0",
            name="ck_coin_infer_outcome_selected_positive",
        ),
        UniqueConstraint("outcome_key", name="uq_coin_infer_outcome_key"),
        Index(
            "ix_coin_infer_outcome_created_surface",
            "created_at",
            "source_surface",
        ),
    )

    id = Column(Integer, primary_key=True)
    outcome_key = Column(String(64), nullable=False, index=True)
    decision_key = Column(
        String(64),
        ForeignKey(
            "coin_intelligence_inference_audits.decision_key",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    source_surface = Column(String(16), nullable=False)
    outcome_kind = Column(String(32), nullable=False)
    selected_commodity_id = Column(Integer, nullable=False)
    selected_commodity_code = Column(String(32), nullable=False)
    selected_commodity_name = Column(String(96), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
