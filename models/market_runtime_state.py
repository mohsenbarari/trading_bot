"""Singleton-like runtime state for market open/close transitions."""

from sqlalchemy import Boolean, Column, DateTime, Integer
from sqlalchemy.sql import func

from .database import Base


class MarketRuntimeState(Base):
    """Stores the last known market transition and minimal web notice state."""

    __tablename__ = "market_runtime_state"

    id = Column(Integer, primary_key=True, index=True)
    is_open = Column(Boolean, nullable=False, default=False, server_default="false")
    active_web_notice_visible = Column(Boolean, nullable=False, default=False, server_default="false")
    offers_since_last_open = Column(Integer, nullable=False, default=0, server_default="0")
    last_transition_at = Column(DateTime(timezone=True), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)