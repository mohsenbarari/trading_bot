"""Market schedule override model for per-day open/close exceptions."""

import enum

from sqlalchemy import Column, Date, DateTime, Enum, ForeignKey, Index, Integer, String, Time
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base


def _market_schedule_override_type_values(enum_cls):
    return [item.value for item in enum_cls]


class MarketScheduleOverrideType(str, enum.Enum):
    CLOSED_ALL_DAY = "closed_all_day"
    OPEN_ALL_DAY = "open_all_day"
    CUSTOM_HOURS = "custom_hours"


class MarketScheduleOverride(Base):
    """One override row per local calendar day."""

    __tablename__ = "market_schedule_overrides"
    __table_args__ = (
        Index("ux_market_schedule_overrides_date", "date", unique=True),
        Index("ix_market_schedule_overrides_override_type", "override_type"),
        Index("ix_market_schedule_overrides_created_by_user_id", "created_by_user_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False)
    override_type = Column(
        Enum(
            MarketScheduleOverrideType,
            name="marketscheduleoverridetype",
            values_callable=_market_schedule_override_type_values,
        ),
        nullable=False,
        index=True,
    )
    open_time_local = Column(Time(timezone=False), nullable=True)
    close_time_local = Column(Time(timezone=False), nullable=True)
    note = Column(String(255), nullable=True)

    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    created_by_user = relationship("User", foreign_keys=[created_by_user_id])