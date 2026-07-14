from sqlalchemy import Column, Integer, String, DateTime, Boolean, JSON, Index, CheckConstraint, func, text
from .database import Base

class ChangeLog(Base):
    __tablename__ = "change_log"

    id = Column(Integer, primary_key=True, index=True)
    operation = Column(String(10), nullable=False)  # INSERT, UPDATE, DELETE
    table_name = Column(String(50), nullable=False)
    record_id = Column(Integer, nullable=False)
    data = Column(JSON, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    hash = Column(String(64), nullable=True)
    synced = Column(Boolean, default=False)
    verified = Column(Boolean, default=False)
    delivery_attempt_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    last_delivery_error = Column(String(120), nullable=True)
    last_delivery_attempt_at = Column(DateTime(timezone=True), nullable=True)
    next_delivery_attempt_at = Column(DateTime(timezone=True), nullable=True)
    quarantined_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "delivery_attempt_count >= 0",
            name="ck_change_log_delivery_attempt_count_nonnegative",
        ),
        Index("idx_sync", "synced", "created_at"),
        Index(
            "idx_change_log_delivery_ready",
            "synced",
            "quarantined_at",
            "next_delivery_attempt_at",
            "id",
        ),
        Index("idx_table", "table_name", "record_id"),
    )
