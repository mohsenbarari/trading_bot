"""Host-boot monotonic timing evidence for signed Writer leases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import time


class WriterLeaseClockError(RuntimeError):
    """Raised when a lease cannot be bound to the current host boot clock."""


@dataclass(frozen=True)
class LeaseClockEvidence:
    boot_id: str
    observed_wall_at: datetime
    observed_boottime: float
    boottime_deadline: float
    witness_issue_offset_ms: int


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def current_boot_id(path: str = "/proc/sys/kernel/random/boot_id") -> str:
    try:
        value = Path(path).read_text(encoding="ascii").strip().lower()
    except OSError as exc:
        raise WriterLeaseClockError("cannot read the host boot identity") from exc
    if len(value) != 36 or value.count("-") != 4:
        raise WriterLeaseClockError("host boot identity has an unexpected format")
    return value


def boottime_seconds() -> float:
    clock = getattr(time, "CLOCK_BOOTTIME", None)
    if clock is None:
        # Platforms without CLOCK_BOOTTIME cannot safely cover suspend.  Linux
        # production has it; this fallback exists only for portable unit tests.
        return time.monotonic()
    return time.clock_gettime(clock)


def build_lease_clock_evidence(
    *,
    issued_at: datetime,
    expires_at: datetime,
    observed_wall_at: datetime,
    safety_margin_seconds: int,
    boot_id: str | None = None,
    observed_boottime: float | None = None,
    boot_id_file: str = "/proc/sys/kernel/random/boot_id",
) -> LeaseClockEvidence:
    observed_wall = _utc(observed_wall_at)
    issued = _utc(issued_at)
    expires = _utc(expires_at)
    remaining = (expires - observed_wall).total_seconds() - max(0, int(safety_margin_seconds))
    if remaining <= 0:
        raise WriterLeaseClockError("witness lease has no safe monotonic lifetime remaining")
    local_boottime = boottime_seconds() if observed_boottime is None else float(observed_boottime)
    if not (local_boottime >= 0):
        raise WriterLeaseClockError("host boot clock is invalid")
    local_boot_id = boot_id or current_boot_id(boot_id_file)
    return LeaseClockEvidence(
        boot_id=local_boot_id,
        observed_wall_at=observed_wall,
        observed_boottime=local_boottime,
        boottime_deadline=local_boottime + remaining,
        witness_issue_offset_ms=int(round((observed_wall - issued).total_seconds() * 1000)),
    )


def lease_clock_reasons(
    *,
    stored_boot_id: str | None,
    stored_boottime_deadline: float | None,
    current_boot: str | None = None,
    current_boottime: float | None = None,
    boot_id_file: str = "/proc/sys/kernel/random/boot_id",
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not stored_boot_id or stored_boottime_deadline is None:
        return ("writer_witness_monotonic_evidence_missing",)
    try:
        boot = current_boot or current_boot_id(boot_id_file)
        now = boottime_seconds() if current_boottime is None else float(current_boottime)
    except WriterLeaseClockError:
        return ("writer_witness_boot_clock_unavailable",)
    if boot != stored_boot_id:
        reasons.append("writer_witness_host_rebooted")
    if now < 0:
        reasons.append("writer_witness_boottime_invalid")
    elif now >= float(stored_boottime_deadline):
        reasons.append("writer_witness_monotonic_deadline_expired")
    return tuple(reasons)
