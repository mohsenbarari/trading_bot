"""Locked channel cadence after the low-risk and owner-approved latency stages.

Production destination spacing is not guessed downward. The shared
``destination_next`` key stays cross-bot. Method is not added to the digest
until a live 429 series proves Telegram applies two budgets on one chat.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from core.config import Settings


CADENCE_SCHEMA_VERSION = "telegram_dispatch_latency_cadence_v1"
CADENCE_TRIAL_INTERVALS_SECONDS = (
    0.25,
    0.30,
    0.35,
    0.40,
    0.50,
    0.65,
    0.80,
    1.00,
    1.05,
)
SHARED_FLEET_MINIMUM_INTERVAL_SECONDS = 1.05


@dataclass(frozen=True, slots=True)
class TelegramDispatchLatencyCadenceLock:
    schema_version: str
    evidence_kind: str
    production_destination_interval_seconds: float
    shared_fleet_minimum_interval_seconds: float
    shared_destination_gate: bool
    method_dimension_enabled: bool
    live_429_series_collected: bool
    trial_intervals_seconds: tuple[float, ...]
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["trial_intervals_seconds"] = list(self.trial_intervals_seconds)
        payload["notes"] = list(self.notes)
        return payload


def _default(name: str) -> Any:
    return Settings.model_fields[name].default


def locked_telegram_dispatch_cadence() -> TelegramDispatchLatencyCadenceLock:
    """Return the production cadence lock implied by Settings and missing live evidence."""
    destination = float(
        _default("telegram_delivery_queue_destination_min_interval_seconds")
    )
    return TelegramDispatchLatencyCadenceLock(
        schema_version=CADENCE_SCHEMA_VERSION,
        evidence_kind="code_derived_cadence_lock",
        production_destination_interval_seconds=destination,
        shared_fleet_minimum_interval_seconds=SHARED_FLEET_MINIMUM_INTERVAL_SECONDS,
        shared_destination_gate=True,
        method_dimension_enabled=False,
        live_429_series_collected=False,
        trial_intervals_seconds=CADENCE_TRIAL_INTERVALS_SECONDS,
        notes=(
            "Production interval stays at the Settings default; it was not lowered.",
            "TOPQ-R28 trial ladder is recorded for a later guarded staging run.",
            "destination_next remains shared across publishers.",
            "Observed staging 429 series were not collected in this worktree.",
        ),
    )
