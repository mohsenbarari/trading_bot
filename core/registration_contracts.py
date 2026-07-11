"""Strict contracts shared by future Web and Telegram registration adapters."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from core.registration_identity import strip_canonical_identity_whitespace
from core.utils import normalize_persian_numerals


REGISTRATION_ADDRESS_MIN_LENGTH = 10
REGISTRATION_ADDRESS_MIN_LENGTH_MESSAGE = "آدرس باید حداقل ۱۰ کاراکتر باشد."
ExactRegistrationAddress = Annotated[str, StringConstraints(strip_whitespace=False)]


def normalize_registration_mobile_number(value: object) -> str:
    """Normalize Telegram/Iran representations into the canonical 09 form."""

    normalized = normalize_persian_numerals(
        strip_canonical_identity_whitespace(value)
    )
    if normalized.startswith("+98"):
        normalized = "0" + normalized[3:]
    elif normalized.startswith("0098"):
        normalized = "0" + normalized[4:]
    elif normalized.startswith("98") and len(normalized) == 12:
        normalized = "0" + normalized[2:]
    elif normalized.startswith("9") and len(normalized) == 10:
        normalized = "0" + normalized
    if len(normalized) != 11 or not normalized.startswith("09") or not normalized.isdigit():
        raise ValueError("شماره موبایل نامعتبر است")
    return normalized


class RegistrationSourceSurface(str, Enum):
    WEBAPP = "webapp"
    TELEGRAM_BOT = "telegram_bot"


class RegistrationIdentityProofType(str, Enum):
    WEB_OTP = "web_otp"
    TELEGRAM_CONTACT = "telegram_contact"


class TelegramRegistrationOutcome(str, Enum):
    CREATED = "created"
    LINKED_EXISTING = "linked_existing"
    ALREADY_LINKED = "already_linked"
    FEATURE_DISABLED = "feature_disabled"
    INVALID_COMMAND = "invalid_command"
    CHANGED_PAYLOAD_REPLAY = "changed_payload_replay"
    INVITATION_NOT_FOUND = "invitation_not_found"
    INVITATION_REVOKED = "invitation_revoked"
    INVITATION_EXPIRED = "invitation_expired"
    INVITATION_ALREADY_USED = "invitation_already_used"
    INVALID_IDENTITY_PROOF = "invalid_identity_proof"
    CONTACT_NOT_OWNED = "contact_not_owned"
    CONTACT_MOBILE_MISMATCH = "contact_mobile_mismatch"
    IDENTITY_CONFLICT = "identity_conflict"
    INVALID_RELATION = "invalid_relation"
    TELEGRAM_ACCOUNT_CONFLICT = "telegram_account_conflict"
    TELEGRAM_ID_ALREADY_USED = "telegram_id_already_used"
    MOBILE_CONFLICT = "mobile_conflict"
    ACCOUNT_NAME_CONFLICT = "account_name_conflict"
    LEGACY_STATE_AMBIGUOUS = "legacy_state_ambiguous"
    AUTHORITATIVE_USER_MISSING = "authoritative_user_missing"
    ACCOUNT_INACTIVE = "account_inactive"
    ACCOUNT_DELETED = "account_deleted"
    LINK_TOKEN_NOT_FOUND = "link_token_not_found"
    LINK_TOKEN_EXPIRED = "link_token_expired"
    LINK_TOKEN_REVOKED = "link_token_revoked"
    LINK_TOKEN_ALREADY_USED = "link_token_already_used"


class InvitationDerivedState(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    REVOKED = "revoked"
    EXPIRED = "expired"


class InvitationSMSStatus(str, Enum):
    DISABLED = "disabled"
    PENDING = "pending"
    ACCEPTED = "accepted"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class OTPRequestStatus(str, Enum):
    PENDING = "pending"
    CONSUMED = "consumed"
    EXPIRED = "expired"


class OTPDeliveryStatus(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    PENDING = "pending"
    ACCEPTED = "accepted"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"
    CANCELLED = "cancelled"


class TelegramRegistrationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    command_id: UUID
    idempotency_key: str = Field(min_length=16, max_length=192, pattern=r"^[A-Za-z0-9._:-]+$")
    invitation_token: str = Field(min_length=12, max_length=192)
    source_surface: Literal[RegistrationSourceSurface.TELEGRAM_BOT] = (
        RegistrationSourceSurface.TELEGRAM_BOT
    )
    identity_proof_type: Literal[RegistrationIdentityProofType.TELEGRAM_CONTACT] = (
        RegistrationIdentityProofType.TELEGRAM_CONTACT
    )
    mobile_number: str
    telegram_id: int = Field(gt=0)
    telegram_username: str | None = Field(default=None, max_length=255)
    telegram_full_name: str | None = Field(default=None, max_length=255)
    address: ExactRegistrationAddress
    contact_verified_at: datetime
    local_completed_at: datetime
    invitation_expires_at_snapshot: datetime

    @field_validator("mobile_number")
    @classmethod
    def validate_mobile_number(cls, value: str) -> str:
        return normalize_registration_mobile_number(value)

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        address = str(value or "")
        if len(address) < REGISTRATION_ADDRESS_MIN_LENGTH:
            raise ValueError(REGISTRATION_ADDRESS_MIN_LENGTH_MESSAGE)
        return address

    @field_validator("contact_verified_at", "local_completed_at", "invitation_expires_at_snapshot")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("registration command timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_proof_timeline(self):
        if self.local_completed_at < self.contact_verified_at:
            raise ValueError("registration completion cannot precede contact verification")
        if self.local_completed_at > self.invitation_expires_at_snapshot:
            raise ValueError("registration proof must complete before invitation expiry")
        return self


class TelegramRegistrationCommandResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: UUID
    outcome: TelegramRegistrationOutcome
    authoritative_user_id: int | None = None
    terminal: bool = True


class InvitationContractV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str
    bot_link: str | None
    web_link: str
    web_short_link: str | None
    bot_available: bool
    web_available: bool
    state: InvitationDerivedState
    kind: str
    expires_at: datetime
    sms_status: InvitationSMSStatus
    link: str | None = None
    short_link: str | None = None


class OTPDeliveryStateContract(BaseModel):
    """Structured state referencing, but never duplicating, `otp:{mobile}`."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    otp_request_id: UUID
    purpose: str = Field(default="web_login", pattern=r"^web_login$")
    mobile_number: str
    code_key: str
    telegram_id: int | None = Field(default=None, gt=0)
    status: OTPRequestStatus = OTPRequestStatus.PENDING
    created_at: datetime
    expires_at: datetime
    telegram_delivery_status: OTPDeliveryStatus = OTPDeliveryStatus.NOT_ATTEMPTED
    telegram_sent_at: datetime | None = None
    sms_fallback_at: datetime | None = None
    sms_delivery_status: OTPDeliveryStatus = OTPDeliveryStatus.NOT_ATTEMPTED
    sms_sent_at: datetime | None = None

    @field_validator("mobile_number")
    @classmethod
    def normalize_mobile_number(cls, value: str) -> str:
        normalized = normalize_persian_numerals(str(value or "")).strip()
        if len(normalized) != 11 or not normalized.startswith("09") or not normalized.isdigit():
            raise ValueError("شماره موبایل نامعتبر است")
        return normalized

    @field_validator("created_at", "expires_at", "telegram_sent_at", "sms_fallback_at", "sms_sent_at")
    @classmethod
    def validate_optional_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("OTP state timestamps must include a timezone")
        return value

    @field_validator("code_key")
    @classmethod
    def validate_code_key_shape(cls, value: str) -> str:
        if not value.startswith("otp:") or len(value) <= 4:
            raise ValueError("OTP state must reference the canonical otp:{mobile} key")
        return value

    @model_validator(mode="after")
    def validate_code_key_mobile(self):
        if self.code_key != f"otp:{self.mobile_number}":
            raise ValueError("OTP state must reference the matching canonical code key")
        if self.expires_at <= self.created_at:
            raise ValueError("OTP expiry must be after creation")
        return self


def canonical_registration_command_bytes(command: TelegramRegistrationCommand | dict[str, Any]) -> bytes:
    if isinstance(command, TelegramRegistrationCommand):
        payload = command.model_dump(mode="json")
    else:
        payload = TelegramRegistrationCommand.model_validate(command).model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def registration_command_hash(command: TelegramRegistrationCommand | dict[str, Any]) -> str:
    return hashlib.sha256(canonical_registration_command_bytes(command)).hexdigest()


def invitation_token_hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()
