"""Redaction helpers for logs and future observability sinks."""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

REDACTED = "[REDACTED]"
REDACTED_JWT = "[REDACTED_JWT]"
REDACTED_CARD = "[REDACTED_CARD]"
REDACTED_EMAIL = "[REDACTED_EMAIL]"
REDACTED_FILENAME = "[REDACTED_FILENAME]"
REDACTED_MOBILE = "[REDACTED_MOBILE]"
REDACTED_NATIONAL_ID = "[REDACTED_NATIONAL_ID]"
REDACTED_OBJECT = "[REDACTED_OBJECT]"
REDACTED_SHEBA = "[REDACTED_SHEBA]"
REDACTED_SIGNED_URL_VALUE = "[REDACTED_SIGNED_URL_VALUE]"

SAFE_CODE_KEYS = {
    "status-code",
    "reason-code",
    "error-code",
    "http-code",
    "status",
}

SENSITIVE_EXACT_KEYS = {
    "access-token",
    "access_token",
    "authorization",
    "cookie",
    "code",
    "id-token",
    "id_token",
    "otp",
    "password",
    "passwd",
    "refresh-token",
    "refresh_token",
    "secret",
    "session-id",
    "signature",
    "sid",
    "signed-url",
    "signed_url",
}

SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "x-api-key",
    "x-dev-api-key",
    "otp",
    "refresh",
    "session_id",
    "session-id",
    "sid",
    "upload_session",
    "upload-session",
    "signed_url",
    "signed-url",
    "x-amz",
    "file_name",
    "file-name",
    "filename",
    "original_file_name",
    "original-file-name",
    "s3_key",
    "s3-key",
    "jwt",
    "signature",
)

_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_SIGNED_URL_QUERY_RE = re.compile(
    r"(?i)([?&](?:"
    r"X-Amz-(?:Algorithm|Credential|Date|Expires|Security-Token|Signature|SignedHeaders)|"
    r"AWSAccessKeyId|Expires|Signature|token|access_token|download_token"
    r")=)([^&#\s]+)"
)
_KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|x-api-key|x-dev-api-key|authorization|token|secret|password|otp|code)"
    r"(\s*[:=]\s*)"
    r"([^\s,;]+)"
)
_KEY_VALUE_FILENAME_RE = re.compile(
    r"(?i)\b(file[_-]?name|filename|original[_-]?file[_-]?name|s3[_-]?key)"
    r"(\s*[:=]\s*)"
    r"([^\s,;]+)"
)
_OTP_RE = re.compile(r"(?i)\b(otp|code)(\s*[:=]\s*)\d{4,8}\b")
_MOBILE_RE = re.compile(r"(?<!\d)(09\d{2})\d{4}(\d{3})(?!\d)")
_IRAN_MOBILE_VARIANT_RE = re.compile(
    r"(?<![\d۰-۹٠-٩])(?:\+?98|0098|0)?[\s\-._()]*9(?:[\s\-._()]*[\d۰-۹٠-٩]){9}(?![\d۰-۹٠-٩])"
)
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_SHEBA_RE = re.compile(r"(?i)\bIR[\s-]*[\d۰-۹٠-٩](?:[\s-]*[\d۰-۹٠-٩]){23}\b")
_CARD_RE = re.compile(r"(?<![\d۰-۹٠-٩])[\d۰-۹٠-٩](?:[\s-]?[\d۰-۹٠-٩]){15}(?![\d۰-۹٠-٩])")
_NATIONAL_ID_RE = re.compile(r"(?<![\d۰-۹٠-٩])[\d۰-۹٠-٩]{10}(?![\d۰-۹٠-٩])")

_SAFE_SCALAR_TYPES = (str, int, float, bool)


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("_", "-")
    if normalized in SAFE_CODE_KEYS:
        return False
    if normalized in SENSITIVE_EXACT_KEYS:
        return True
    if normalized.endswith("-code") and any(
        prefix in normalized for prefix in ("otp", "recovery", "verification", "password", "login")
    ):
        return True
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def mask_mobile(value: str) -> str:
    masked = _MOBILE_RE.sub(r"\1****\2", value)
    return _IRAN_MOBILE_VARIANT_RE.sub(REDACTED_MOBILE, masked)


def redact_string(value: str) -> str:
    sanitized = _BEARER_RE.sub(f"Bearer {REDACTED}", value)
    sanitized = _JWT_RE.sub(REDACTED_JWT, sanitized)
    sanitized = _SIGNED_URL_QUERY_RE.sub(rf"\1{REDACTED_SIGNED_URL_VALUE}", sanitized)
    sanitized = _OTP_RE.sub(rf"\1\2{REDACTED}", sanitized)
    sanitized = _KEY_VALUE_SECRET_RE.sub(rf"\1\2{REDACTED}", sanitized)
    sanitized = _KEY_VALUE_FILENAME_RE.sub(rf"\1\2{REDACTED_FILENAME}", sanitized)
    sanitized = _EMAIL_RE.sub(REDACTED_EMAIL, sanitized)
    sanitized = _SHEBA_RE.sub(REDACTED_SHEBA, sanitized)
    sanitized = _CARD_RE.sub(REDACTED_CARD, sanitized)
    sanitized = mask_mobile(sanitized)
    return _NATIONAL_ID_RE.sub(REDACTED_NATIONAL_ID, sanitized)


def safe_object_metadata(value: Any) -> dict[str, str]:
    return {
        "redacted": REDACTED_OBJECT,
        "object_type": f"{type(value).__module__}.{type(value).__qualname__}",
    }


def redact(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, nested in value.items():
            key_str = str(key)
            redacted[key_str] = REDACTED if is_sensitive_key(key_str) else redact(nested)
        return redacted
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item) for item in value]
    if isinstance(value, bytes):
        return redact_string(value.decode("utf-8", errors="replace"))
    if isinstance(value, str):
        return redact_string(value)
    if isinstance(value, _SAFE_SCALAR_TYPES):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return redact(value.value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return safe_object_metadata(value)
