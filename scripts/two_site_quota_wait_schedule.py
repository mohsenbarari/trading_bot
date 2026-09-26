"""Pure schedule calculation for durable quota wait state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any

try:
    from scripts.two_site_quota_evidence import provider_for_model
except ImportError:
    from two_site_quota_evidence import provider_for_model

__all__ = ["QuotaWaitScheduleError", "initial_wait_schedule"]

_CANONICAL_UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)

_EVIDENCE_KEYS = frozenset(
    {"kind", "provider", "model", "conversation_id", "reset_at_utc"}
)


class QuotaWaitScheduleError(ValueError):
    """Raised when quota wait schedule input is invalid or schedule cannot be calculated."""


def _parse_utc_iso(ts: object) -> datetime:
    if type(ts) is not str:
        raise QuotaWaitScheduleError(f"Timestamp must be str, got {type(ts).__name__}")
    if not _CANONICAL_UTC_RE.fullmatch(ts):
        raise QuotaWaitScheduleError(f"Invalid canonical UTC timestamp: {ts!r}")
    try:
        dt = datetime.fromisoformat(ts[:-1] + "+00:00")
    except (ValueError, OverflowError) as exc:
        raise QuotaWaitScheduleError(f"Invalid date/time value: {ts!r}") from exc
    if dt.utcoffset() != timedelta(0):
        raise QuotaWaitScheduleError(f"Timestamp must be UTC: {ts!r}")
    return dt


def initial_wait_schedule(
    evidence: object,
    created_at_utc: object,
) -> dict[str, Any]:
    """Calculate the initial wait schedule for quota backoff."""
    if type(evidence) is not dict:
        raise QuotaWaitScheduleError(
            f"evidence must be dict, got {type(evidence).__name__}"
        )
    if set(evidence.keys()) != _EVIDENCE_KEYS:
        raise QuotaWaitScheduleError(
            f"evidence keys must be exactly {sorted(_EVIDENCE_KEYS)!r}"
        )

    kind = evidence["kind"]
    if type(kind) is not str:
        raise QuotaWaitScheduleError(f"kind must be str, got {type(kind).__name__}")
    if kind not in ("QUOTA_RESET_KNOWN", "QUOTA_RESET_UNKNOWN"):
        raise QuotaWaitScheduleError(f"Unsupported kind: {kind!r}")

    provider = evidence["provider"]
    if type(provider) is not str or not provider:
        raise QuotaWaitScheduleError(
            f"provider must be non-empty str, got {provider!r}"
        )

    model = evidence["model"]
    if (
        type(model) is not str
        or not model
        or len(model) > 128
        or not model.isprintable()
    ):
        raise QuotaWaitScheduleError("model must be nonempty printable str <= 128 chars")

    expected_provider = provider_for_model(model)
    if expected_provider is None or expected_provider != provider:
        raise QuotaWaitScheduleError(
            f"provider/model mismatch or unsupported model: provider={provider!r}, model={model!r}"
        )

    conv_id = evidence["conversation_id"]
    if (
        type(conv_id) is not str
        or not (1 <= len(conv_id) <= 128)
        or not all(33 <= ord(c) <= 126 for c in conv_id)
    ):
        raise QuotaWaitScheduleError(
            "conversation_id must be ASCII 33..126 of length 1..128"
        )

    created_dt = _parse_utc_iso(created_at_utc)

    reset_at = evidence["reset_at_utc"]
    try:
        if kind == "QUOTA_RESET_UNKNOWN":
            if reset_at is not None:
                raise QuotaWaitScheduleError(
                    "reset_at_utc must be None for QUOTA_RESET_UNKNOWN"
                )
            next_dt = created_dt + timedelta(minutes=15)
            remaining_check_budget = 5
        else:
            reset_dt = _parse_utc_iso(reset_at)
            next_dt = max(
                created_dt + timedelta(seconds=30),
                reset_dt + timedelta(seconds=30),
            )
            remaining_check_budget = 0
    except OverflowError as exc:
        raise QuotaWaitScheduleError("Timestamp arithmetic overflow") from exc

    timespec = "microseconds" if next_dt.microsecond else "seconds"
    next_check_at_utc = next_dt.isoformat(timespec=timespec).replace("+00:00", "Z")

    return {
        "reset_kind": kind,
        "reset_at_utc": reset_at,
        "created_at_utc": created_at_utc,
        "next_check_at_utc": next_check_at_utc,
        "remaining_check_budget": remaining_check_budget,
        "remaining_resume_budget": 1,
    }
