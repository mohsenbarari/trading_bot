from sqlalchemy import Column, Integer, String, DateTime, Boolean, JSON, Index, func
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
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_sync", "synced", "created_at"),
        Index("idx_table", "table_name", "record_id"),
    )
