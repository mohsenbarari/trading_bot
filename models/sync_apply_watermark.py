from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, UniqueConstraint, func

from .database import Base


class SyncApplyWatermark(Base):
    __tablename__ = "sync_apply_watermarks"
    __table_args__ = (
        UniqueConstraint(
            "source_server",
            "aggregate_table",
            "aggregate_key",
            name="ux_sync_apply_watermarks_source_aggregate",
        ),
        Index(
            "ix_sync_apply_watermarks_source_table_sequence",
            "source_server",
            "aggregate_table",
            "last_source_sequence",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    source_server = Column(String(16), nullable=False)
    aggregate_table = Column(String(64), nullable=False)
    aggregate_key = Column(String(255), nullable=False)
    last_source_sequence = Column(BigInteger, nullable=False)
    last_payload_hash = Column(String(64), nullable=False)
    last_operation = Column(String(10), nullable=False)
    last_record_id = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
