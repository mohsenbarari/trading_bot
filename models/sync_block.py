from sqlalchemy import Column, Integer, String, Date, Boolean, DateTime, func, UniqueConstraint
from .database import Base

class SyncBlock(Base):
    __tablename__ = "sync_blocks"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(20), nullable=False)  # 'iran' or 'foreign'
    date = Column(Date, nullable=False)
    hash = Column(String(64), nullable=False)
    record_count = Column(Integer, nullable=True)
    verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("source", "date", name="uq_source_date"),
    )
