"""Stable contracts for the Shadow coin-intelligence boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


SHADOW_BUNDLE_STATUS = "SHADOW_NOT_PROMOTED"
SUPPORTED_SHADOW_OUTCOMES = frozenset(
    {
        "INFERRED",
        "AMBIGUOUS",
        "NO_MARKET_DATA",
        "INVALID_PRICE",
        "STALE_INPUT",
        "UNSUPPORTED_MARKET_DIMENSION",
        "BUNDLE_UNAVAILABLE",
        "SNAPSHOT_UNAVAILABLE",
        "RUNTIME_ERROR",
    }
)


class SnapshotUnavailableError(RuntimeError):
    """Raised when no safe, complete range snapshot can be loaded."""


class RangeSnapshotProvider(Protocol):
    """Loads one immutable estimator-state snapshot for local inference."""

    def load(self) -> Mapping[str, Any]:
        """Return one JSON-compatible state object or raise unavailable."""


@dataclass(frozen=True, slots=True)
class ShadowObservation:
    """Non-mutating result emitted beside the existing parser decision."""

    status: str
    settlement: str
    current_commodity: str
    inferred_commodity: str | None
    agrees_with_current: bool | None
    requires_user_confirmation: bool
    decision_reason: str
    bundle_version: str | None
    snapshot_version: str | None
