"""Authoritative offer lifetime projection.

Every caller that needs a deadline, phase, or interaction flag must go through
this module. The normal lifetime still comes from the current admin setting
(dynamic for still-active offers). Overtime comes only from the offer's
immutable ``overtime_minutes_snapshot``.

Phase classification for a trade request uses the trusted first-server receipt
time alone. Transit delay and home-server processing time never move a request
between phases. Exact normal and exact final boundaries reject new requests;
automatic trades are only strictly before the normal deadline; approval is only
strictly inside overtime.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from sqlalchemy import func


class OfferLifecyclePhase(str, Enum):
    NORMAL = "normal"
    OVERTIME = "overtime"
    FINAL_TAIL = "final_tail"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"


class OfferRequestIntakePhase(str, Enum):
    """How a newly received trade request must be handled."""

    AUTOMATIC = "automatic"
    APPROVAL = "approval"
    REJECTED = "rejected"


@dataclass(frozen=True)
class OfferLifecycleProjection:
    normal_lifetime_minutes: int
    overtime_minutes_snapshot: int
    normal_deadline_at: datetime | None
    final_deadline_at: datetime | None
    normal_deadline_ts: int | None
    final_deadline_ts: int | None
    #: Backward-compatible display end: the final public lifetime deadline.
    expires_at_ts: int | None
    #: Authoritative timer denominator for the active phase bar, in seconds.
    timer_total_seconds: int | None
    phase: OfferLifecyclePhase
    accepts_automatic_trade: bool
    accepts_overtime_request: bool
    accepts_new_public_interaction: bool
    #: Worker may transition the offer to terminal time-limit expiry.
    terminal_expiry_due: bool


def _as_naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _timestamp(value: datetime | None) -> int | None:
    if value is None:
        return None
    return int(value.replace(tzinfo=timezone.utc).timestamp())


def read_overtime_minutes_snapshot(offer: Any) -> int:
    raw = getattr(offer, "overtime_minutes_snapshot", None)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0
    return max(0, min(10, raw))


def read_normal_lifetime_minutes(settings_or_minutes: Any) -> int:
    if isinstance(settings_or_minutes, bool):
        return 0
    if isinstance(settings_or_minutes, int):
        return max(0, settings_or_minutes)
    raw = getattr(settings_or_minutes, "offer_expiry_minutes", 0)
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def compute_lifecycle_deadlines(
    created_at: datetime | None,
    *,
    normal_lifetime_minutes: int,
    overtime_minutes_snapshot: int = 0,
) -> tuple[datetime | None, datetime | None]:
    created = _as_naive_utc(created_at)
    if created is None or normal_lifetime_minutes <= 0:
        return None, None
    overtime = max(0, int(overtime_minutes_snapshot or 0))
    normal_deadline = created + timedelta(minutes=normal_lifetime_minutes)
    final_deadline = normal_deadline + timedelta(minutes=overtime)
    return normal_deadline, final_deadline


def classify_request_intake_phase(
    *,
    receipt_at: datetime | None,
    normal_deadline_at: datetime | None,
    final_deadline_at: datetime | None,
    overtime_minutes_snapshot: int,
) -> OfferRequestIntakePhase:
    """Classify a request from its trusted first-server receipt time only."""
    receipt = _as_naive_utc(receipt_at)
    normal = _as_naive_utc(normal_deadline_at)
    final = _as_naive_utc(final_deadline_at)
    if receipt is None or normal is None or final is None:
        # No usable lifetime: keep the historical "no expiry configured" path.
        return OfferRequestIntakePhase.AUTOMATIC
    if receipt < normal:
        return OfferRequestIntakePhase.AUTOMATIC
    if overtime_minutes_snapshot > 0 and receipt > normal and receipt < final:
        return OfferRequestIntakePhase.APPROVAL
    return OfferRequestIntakePhase.REJECTED


def project_offer_lifecycle(
    offer: Any,
    *,
    normal_lifetime_minutes: int,
    as_of: datetime | None = None,
    has_final_tail_request: bool = False,
) -> OfferLifecycleProjection:
    """Project deadlines and display/enforcement flags for one offer.

    ``as_of`` is wall-clock for display phase and terminal-expiry eligibility.
    Request intake must call ``classify_request_intake_phase`` with the trusted
    receipt time instead of using this display phase.
    """
    normal_minutes = read_normal_lifetime_minutes(normal_lifetime_minutes)
    overtime = read_overtime_minutes_snapshot(offer)
    normal_deadline, final_deadline = compute_lifecycle_deadlines(
        getattr(offer, "created_at", None),
        normal_lifetime_minutes=normal_minutes,
        overtime_minutes_snapshot=overtime,
    )
    now = _as_naive_utc(as_of) or datetime.utcnow()
    status = getattr(offer, "status", None)
    status_value = getattr(status, "value", status)
    if status is None:
        # Untyped test doubles without a status are treated as active.
        is_active = True
    else:
        is_active = str(status_value) == "active"

    if normal_minutes <= 0 or normal_deadline is None or final_deadline is None:
        return OfferLifecycleProjection(
            normal_lifetime_minutes=normal_minutes,
            overtime_minutes_snapshot=overtime,
            normal_deadline_at=None,
            final_deadline_at=None,
            normal_deadline_ts=None,
            final_deadline_ts=None,
            expires_at_ts=None,
            timer_total_seconds=None,
            phase=OfferLifecyclePhase.UNAVAILABLE,
            accepts_automatic_trade=True,
            accepts_overtime_request=False,
            accepts_new_public_interaction=is_active,
            terminal_expiry_due=False,
        )

    normal_ts = _timestamp(normal_deadline)
    final_ts = _timestamp(final_deadline)

    if not is_active:
        phase = OfferLifecyclePhase.EXPIRED
        accepts_automatic = False
        accepts_overtime = False
        accepts_public = False
        terminal_due = False
        timer_total = None
    elif now < normal_deadline:
        phase = OfferLifecyclePhase.NORMAL
        accepts_automatic = True
        accepts_overtime = False
        accepts_public = True
        terminal_due = False
        timer_total = normal_minutes * 60
    elif overtime > 0 and now < final_deadline:
        phase = OfferLifecyclePhase.OVERTIME
        accepts_automatic = False
        accepts_overtime = True
        accepts_public = True
        terminal_due = False
        timer_total = overtime * 60
    elif has_final_tail_request and now >= final_deadline:
        phase = OfferLifecyclePhase.FINAL_TAIL
        accepts_automatic = False
        accepts_overtime = False
        accepts_public = False
        terminal_due = False
        timer_total = 0
    else:
        phase = OfferLifecyclePhase.EXPIRED
        accepts_automatic = False
        accepts_overtime = False
        accepts_public = False
        terminal_due = is_active and now >= final_deadline and not has_final_tail_request
        timer_total = 0

    # Exact normal boundary: display may already show overtime when snapshot > 0,
    # but new automatic intake is closed. Keep accepts_* aligned with intake rules
    # evaluated at `now` for local surfaces that have no separate receipt stamp.
    intake = classify_request_intake_phase(
        receipt_at=now,
        normal_deadline_at=normal_deadline,
        final_deadline_at=final_deadline,
        overtime_minutes_snapshot=overtime,
    )
    if is_active and phase not in {OfferLifecyclePhase.FINAL_TAIL, OfferLifecyclePhase.EXPIRED}:
        accepts_automatic = intake == OfferRequestIntakePhase.AUTOMATIC
        accepts_overtime = intake == OfferRequestIntakePhase.APPROVAL
        accepts_public = accepts_automatic or accepts_overtime

    return OfferLifecycleProjection(
        normal_lifetime_minutes=normal_minutes,
        overtime_minutes_snapshot=overtime,
        normal_deadline_at=normal_deadline,
        final_deadline_at=final_deadline,
        normal_deadline_ts=normal_ts,
        final_deadline_ts=final_ts,
        expires_at_ts=final_ts,
        timer_total_seconds=timer_total,
        phase=phase,
        accepts_automatic_trade=accepts_automatic,
        accepts_overtime_request=accepts_overtime,
        accepts_new_public_interaction=accepts_public,
        terminal_expiry_due=terminal_due,
    )


def stale_final_cutoff_time(
    *,
    now: datetime,
    normal_lifetime_minutes: int,
    overtime_minutes_snapshot: int = 0,
) -> datetime | None:
    """Return the created_at cutoff for a single known snapshot value."""
    normal_minutes = read_normal_lifetime_minutes(normal_lifetime_minutes)
    if normal_minutes <= 0:
        return None
    total = normal_minutes + max(0, int(overtime_minutes_snapshot or 0))
    return _as_naive_utc(now) - timedelta(minutes=total)


def offer_lifetime_end_epoch_sql(created_at_column, overtime_column, normal_lifetime_minutes: int):
    """SQL: epoch seconds when the offer's final public lifetime ends."""
    total_seconds = (
        int(normal_lifetime_minutes) + func.coalesce(overtime_column, 0)
    ) * 60
    return func.extract("epoch", created_at_column) + total_seconds


def publication_freshness_deadline_at(
    created_at: datetime | None,
    *,
    normal_lifetime_minutes: int,
    overtime_minutes_snapshot: int,
    safety_seconds: float,
) -> datetime:
    normal_deadline, final_deadline = compute_lifecycle_deadlines(
        created_at,
        normal_lifetime_minutes=normal_lifetime_minutes,
        overtime_minutes_snapshot=overtime_minutes_snapshot,
    )
    if final_deadline is None:
        raise ValueError("offer_lifecycle_deadline_unavailable")
    deadline = final_deadline - timedelta(seconds=float(safety_seconds))
    # Match the queue helper's timezone convention so comparisons stay valid.
    if deadline.tzinfo is None:
        return deadline.replace(tzinfo=timezone.utc)
    return deadline
