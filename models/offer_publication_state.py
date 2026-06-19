"""Surface publication state for offers."""
from __future__ import annotations

import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base


def _enum_values(enum_cls):
    return [item.value for item in enum_cls]


class OfferPublicationSurface(str, enum.Enum):
    TELEGRAM_CHANNEL = "telegram_channel"
    WEBAPP_MARKET = "webapp_market"


class OfferPublicationStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    VISIBLE = "visible"
    FAILED = "failed"
    DISABLED = "disabled"
    LAGGED = "lagged"


class OfferPublicationState(Base):
    __tablename__ = "offer_publication_states"
    __table_args__ = (
        UniqueConstraint("offer_public_id", "surface", name="ux_offer_publication_states_offer_surface"),
        UniqueConstraint("dedupe_key", name="ux_offer_publication_states_dedupe_key"),
        Index("ix_offer_publication_states_offer_status", "offer_public_id", "status"),
        Index("ix_offer_publication_states_surface_status", "surface", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    version_id = Column(Integer, nullable=False, default=1)

    offer_id = Column(Integer, ForeignKey("offers.id", ondelete="SET NULL"), nullable=True, index=True)
    offer = relationship("Offer", foreign_keys=[offer_id])
    offer_public_id = Column(String(40), nullable=False, index=True)
    offer_home_server = Column(String(16), nullable=False, index=True)

    surface = Column(
        Enum(
            OfferPublicationSurface,
            name="offerpublicationsurface",
            values_callable=_enum_values,
        ),
        nullable=False,
        index=True,
    )
    publication_owner_server = Column(String(16), nullable=False, index=True)
    status = Column(
        Enum(
            OfferPublicationStatus,
            name="offerpublicationstatus",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=OfferPublicationStatus.PENDING,
        index=True,
    )

    dedupe_key = Column(String(160), nullable=False)
    surface_resource_id = Column(String(160), nullable=True)
    telegram_chat_id = Column(BigInteger, nullable=True)
    telegram_message_id = Column(BigInteger, nullable=True)

    offer_version_id = Column(Integer, nullable=True)
    last_known_offer_status = Column(String(32), nullable=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    disabled_at = Column(DateTime(timezone=True), nullable=True)
    lagged_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String(96), nullable=True)
    error_message = Column(String(240), nullable=True)
    state_metadata = Column(JSON, nullable=True)

    archived = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    __mapper_args__ = {
        "version_id_col": version_id,
    }
