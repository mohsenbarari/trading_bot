import enum
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, Enum as SAEnum, JSON, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base


def _enum_values(enum_cls):
    return [member.value for member in enum_cls]


class UploadRoomKind(str, enum.Enum):
    DIRECT = "direct"
    GROUP = "group"


class UploadBatchMessageKind(str, enum.Enum):
    SINGLE = "single"
    ALBUM = "album"


class UploadBatchStatus(str, enum.Enum):
    COLLECTING = "collecting"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    COMMITTING = "committing"
    COMMITTED = "committed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class UploadCaptionPolicy(str, enum.Enum):
    NONE = "none"
    FIRST_ITEM_ONLY = "first_item_only"


class UploadSessionStatus(str, enum.Enum):
    CREATED = "created"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    FINALIZING = "finalizing"
    READY = "ready"
    COMMITTED = "committed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class UploadMediaType(str, enum.Enum):
    IMAGE = "image"
    VIDEO = "video"
    VOICE = "voice"
    DOCUMENT = "document"


class UploadBatch(Base):
    __tablename__ = "upload_batches"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    owner_user_id = Column(Integer, ForeignKey("users.id", name="fk_upload_batches_owner_user"), nullable=False, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id", name="fk_upload_batches_actor_user"), nullable=True, index=True)
    room_kind = Column(
        SAEnum(UploadRoomKind, values_callable=_enum_values, name="uploadroomkind"),
        nullable=False,
        index=True,
    )
    target_id = Column(Integer, nullable=False, index=True)
    message_kind = Column(
        SAEnum(UploadBatchMessageKind, values_callable=_enum_values, name="uploadbatchmessagekind"),
        nullable=False,
        default=UploadBatchMessageKind.SINGLE,
    )
    expected_items = Column(Integer, nullable=False, default=1)
    committed_items = Column(Integer, nullable=False, default=0)
    status = Column(
        SAEnum(UploadBatchStatus, values_callable=_enum_values, name="uploadbatchstatus"),
        nullable=False,
        default=UploadBatchStatus.COLLECTING,
        index=True,
    )
    caption_policy = Column(
        SAEnum(UploadCaptionPolicy, values_callable=_enum_values, name="uploadcaptionpolicy"),
        nullable=False,
        default=UploadCaptionPolicy.NONE,
    )
    idempotency_key = Column(String(128), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    last_activity_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    owner = relationship("User", foreign_keys=[owner_user_id])
    actor = relationship("User", foreign_keys=[actor_user_id])
    sessions = relationship("UploadSession", back_populates="batch", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_upload_batches_owner_status_activity", "owner_user_id", "status", "last_activity_at"),
        Index("ix_upload_batches_target_room_status", "room_kind", "target_id", "status"),
    )


class UploadSession(Base):
    __tablename__ = "upload_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    batch_id = Column(String(36), ForeignKey("upload_batches.id", name="fk_upload_sessions_batch"), nullable=True, index=True)
    owner_user_id = Column(Integer, ForeignKey("users.id", name="fk_upload_sessions_owner_user"), nullable=False, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id", name="fk_upload_sessions_actor_user"), nullable=True, index=True)
    room_kind = Column(
        SAEnum(UploadRoomKind, values_callable=_enum_values, name="uploadroomkind"),
        nullable=False,
        index=True,
    )
    target_id = Column(Integer, nullable=False, index=True)
    media_type = Column(
        SAEnum(UploadMediaType, values_callable=_enum_values, name="uploadmediatype"),
        nullable=False,
        index=True,
    )
    original_file_name = Column(String(255), nullable=False)
    mime_type = Column(String(100), nullable=False)
    total_bytes = Column(Integer, nullable=False)
    chunk_size = Column(Integer, nullable=False)
    received_bytes = Column(Integer, nullable=False, default=0)
    next_offset = Column(Integer, nullable=False, default=0)
    chunk_count = Column(Integer, nullable=False, default=0)
    sha256_full = Column(String(128), nullable=True)
    sha256_chunks = Column(JSON, nullable=True)
    status = Column(
        SAEnum(UploadSessionStatus, values_callable=_enum_values, name="uploadsessionstatus"),
        nullable=False,
        default=UploadSessionStatus.CREATED,
        index=True,
    )
    temp_storage_path = Column(String(512), nullable=False)
    final_chat_file_id = Column(String(36), ForeignKey("chat_files.id", name="fk_upload_sessions_chat_file"), nullable=True, index=True)
    preview_metadata = Column(JSON, nullable=True)
    resume_token = Column(String(128), nullable=False, unique=True, index=True)
    retry_count = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    last_activity_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    batch = relationship("UploadBatch", back_populates="sessions", foreign_keys=[batch_id])
    owner = relationship("User", foreign_keys=[owner_user_id])
    actor = relationship("User", foreign_keys=[actor_user_id])
    final_chat_file = relationship("ChatFile", foreign_keys=[final_chat_file_id])

    __table_args__ = (
        Index("ix_upload_sessions_owner_status_activity", "owner_user_id", "status", "last_activity_at"),
        Index("ix_upload_sessions_batch_status", "batch_id", "status"),
        Index("ix_upload_sessions_target_room_status", "room_kind", "target_id", "status"),
    )