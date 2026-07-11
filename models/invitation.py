import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base
from .user import UserRole


def _enum_values(enum_cls):
    return [item.value for item in enum_cls]


class InvitationKind(str, enum.Enum):
    STANDARD = "standard"
    ACCOUNTANT = "accountant"
    CUSTOMER = "customer"
    LEGACY_UNKNOWN = "legacy_unknown"


class InvitationCompletionSurface(str, enum.Enum):
    WEB = "web"
    TELEGRAM = "telegram"


class Invitation(Base):
    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint(
            "sync_version >= 1",
            name="ck_invitations_sync_version_positive",
        ),
        CheckConstraint(
            "((registered_user_id IS NULL AND completed_at IS NULL AND completed_via IS NULL) "
            "OR (registered_user_id IS NOT NULL AND completed_at IS NOT NULL "
            "AND completed_via IS NOT NULL AND is_used = true))",
            name="ck_invitations_completion_metadata_atomic",
        ),
        CheckConstraint(
            "NOT (revoked_at IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_invitations_not_completed_and_revoked",
        ),
        Index("ix_invitations_kind", "kind"),
        Index("ix_invitations_registered_user_id", "registered_user_id"),
        Index("ix_invitations_revoked_at", "revoked_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    account_name = Column(String, index=True, nullable=False)
    mobile_number = Column(String, index=True, nullable=False)
    
    token = Column(String, unique=True, index=True, nullable=False)
    
    # New short code for SMS-friendly links
    short_code = Column(String(8), unique=True, index=True, nullable=True)
    
    role = Column(Enum(UserRole), nullable=False, default=UserRole.WATCH)

    kind = Column(
        Enum(
            InvitationKind,
            name="invitationkind",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=InvitationKind.LEGACY_UNKNOWN,
        server_default=text("'legacy_unknown'"),
    )
    
    created_by_id = Column(Integer, ForeignKey("users.id"))
    created_by = relationship("User", foreign_keys=[created_by_id])
    
    is_used = Column(Boolean, default=False)
    expires_at = Column(DateTime, nullable=False)

    registered_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    registered_user = relationship("User", foreign_keys=[registered_user_id])
    completed_at = Column(DateTime(timezone=True), nullable=True)
    completed_via = Column(
        Enum(
            InvitationCompletionSurface,
            name="invitationcompletionsurface",
            values_callable=_enum_values,
        ),
        nullable=True,
    )
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    sync_version = Column(BigInteger, nullable=False, default=1, server_default=text("1"))
    __mapper_args__ = {
        "version_id_col": sync_version,
        "version_id_generator": False,
    }

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())
