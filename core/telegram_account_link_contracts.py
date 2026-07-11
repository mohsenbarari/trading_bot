"""Strict command contract for Iran-authoritative Web-to-Telegram account linking."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from core.registration_identity import normalize_mobile_number
from core.registration_contracts import REGISTRATION_ADDRESS_MIN_LENGTH, REGISTRATION_ADDRESS_MIN_LENGTH_MESSAGE


ExactOptionalAddress = Annotated[str, StringConstraints(strip_whitespace=False)]


class TelegramAccountLinkCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    command_id: UUID
    idempotency_key: str = Field(
        min_length=32,
        max_length=96,
        pattern=r"^telegram-account-link:[a-f0-9]{40}$",
    )
    mode: Literal["link_token", "existing_linked_user"]
    link_token: str | None = Field(default=None, min_length=32, max_length=192)
    mobile_number: str
    telegram_id: int = Field(gt=0)
    telegram_username: str | None = Field(default=None, max_length=255)
    telegram_full_name: str | None = Field(default=None, max_length=255)
    address: ExactOptionalAddress | None = None
    contact_verified_at: datetime | None = None

    @field_validator("mobile_number")
    @classmethod
    def validate_mobile_number(cls, value: str) -> str:
        normalized = normalize_mobile_number(value)
        if len(normalized) != 11 or not normalized.startswith("09") or not normalized.isdigit():
            raise ValueError("شماره موبایل نامعتبر است")
        return normalized

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str | None) -> str | None:
        if value is not None and len(value) < REGISTRATION_ADDRESS_MIN_LENGTH:
            raise ValueError(REGISTRATION_ADDRESS_MIN_LENGTH_MESSAGE)
        return value

    @field_validator("contact_verified_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("account-link proof timestamp must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_mode(self):
        if self.mode == "link_token":
            if self.link_token is None or self.contact_verified_at is None:
                raise ValueError("link-token mode requires token and contact proof")
            return self
        if self.link_token is not None or self.contact_verified_at is not None or self.address is None:
            raise ValueError("existing-linked-user mode requires only an address update")
        return self


def _account_link_identity_material(
    *,
    mode: str,
    link_token: str | None,
    mobile_number: str,
    telegram_id: int,
) -> str:
    protected_token = hashlib.sha256(str(link_token or "").encode("utf-8")).hexdigest()
    return ":".join(
        (
            str(mode),
            protected_token,
            normalize_mobile_number(mobile_number),
            str(int(telegram_id)),
        )
    )


def build_telegram_account_link_command(
    *,
    mode: Literal["link_token", "existing_linked_user"],
    link_token: str | None,
    mobile_number: str,
    telegram_id: int,
    telegram_username: str | None,
    telegram_full_name: str | None,
    address: str | None,
    contact_verified_at: datetime | None,
) -> TelegramAccountLinkCommand:
    material = _account_link_identity_material(
        mode=mode,
        link_token=link_token,
        mobile_number=mobile_number,
        telegram_id=telegram_id,
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return TelegramAccountLinkCommand(
        command_id=uuid5(NAMESPACE_URL, f"trading-bot:{material}"),
        idempotency_key=f"telegram-account-link:{digest[:40]}",
        mode=mode,
        link_token=link_token,
        mobile_number=mobile_number,
        telegram_id=telegram_id,
        telegram_username=telegram_username,
        telegram_full_name=telegram_full_name,
        address=address,
        contact_verified_at=contact_verified_at,
    )


def canonical_account_link_command_bytes(command: TelegramAccountLinkCommand) -> bytes:
    payload = command.model_dump(mode="json")
    # These are first-request profile/proof snapshots, not authoritative replay
    # identity. Business fields (mode, credential, mobile, Telegram id and
    # exact address) remain covered by the receipt hash.
    for volatile_field in (
        "telegram_username",
        "telegram_full_name",
        "contact_verified_at",
    ):
        payload.pop(volatile_field, None)
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def account_link_command_hash(command: TelegramAccountLinkCommand) -> str:
    return hashlib.sha256(canonical_account_link_command_bytes(command)).hexdigest()


def account_link_credential_hash(command: TelegramAccountLinkCommand) -> str:
    if command.link_token:
        material = f"link-token:{command.link_token}"
    else:
        material = f"linked-user:{command.mobile_number}:{command.telegram_id}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
