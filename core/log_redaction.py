"""Redaction helpers for logs and future observability sinks."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"
REDACTED_JWT = "[REDACTED_JWT]"

SAFE_CODE_KEYS = {
    "status-code",
    "reason-code",
    "error-code",
    "http-code",
    "status",
}

SENSITIVE_EXACT_KEYS = {
    "authorization",
    "cookie",
    "code",
    "otp",
    "password",
    "passwd",
    "secret",
    "session-id",
    "signature",
    "sid",
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
    "access",
    "jwt",
    "signature",
)

_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|x-api-key|x-dev-api-key|authorization|token|secret|password|otp|code)"
    r"(\s*[:=]\s*)"
    r"([^\s,;]+)"
)
_OTP_RE = re.compile(r"(?i)\b(otp|code)(\s*[:=]\s*)\d{4,8}\b")
_MOBILE_RE = re.compile(r"(?<!\d)(09\d{2})\d{4}(\d{3})(?!\d)")


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
    return _MOBILE_RE.sub(r"\1****\2", value)


def redact_string(value: str) -> str:
    sanitized = _BEARER_RE.sub(f"Bearer {REDACTED}", value)
    sanitized = _JWT_RE.sub(REDACTED_JWT, sanitized)
    sanitized = _OTP_RE.sub(rf"\1\2{REDACTED}", sanitized)
    sanitized = _KEY_VALUE_SECRET_RE.sub(rf"\1\2{REDACTED}", sanitized)
    return mask_mobile(sanitized)


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
    return value
