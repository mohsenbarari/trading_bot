"""Strict signed-command contracts for canonical standard invitations."""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.registration_identity import normalize_account_name, normalize_mobile_number
from models.user import UserRole


class InternalInvitationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    requester_user_id: int = Field(gt=0)
    account_name: str = Field(min_length=3, max_length=32)
    mobile_number: str
    role: UserRole = UserRole.WATCH
    source_server: str = Field(pattern=r"^foreign$")
    idempotency_key: str = Field(
        min_length=32,
        max_length=96,
        pattern=r"^standard-invitation:[a-f0-9]{40}$",
    )

    @field_validator("account_name")
    @classmethod
    def normalize_account(cls, value: str) -> str:
        normalized = normalize_account_name(value)
        if len(normalized) < 3:
            raise ValueError("نام کاربری نامعتبر است")
        return normalized

    @field_validator("mobile_number")
    @classmethod
    def normalize_mobile(cls, value: str) -> str:
        normalized = normalize_mobile_number(value)
        if len(normalized) != 11 or not normalized.startswith("09") or not normalized.isdigit():
            raise ValueError("شماره موبایل نامعتبر است")
        return normalized


def build_standard_invitation_idempotency_key(
    *,
    requester_user_id: int,
    account_name: str,
    mobile_number: str,
    role: UserRole | str,
) -> str:
    role_value = str(getattr(role, "value", role) or "")
    material = ":".join(
        (
            str(int(requester_user_id)),
            normalize_account_name(account_name),
            normalize_mobile_number(mobile_number),
            role_value,
        )
    )
    return f"standard-invitation:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:40]}"
