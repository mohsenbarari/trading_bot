"""Pure, fail-closed admission check for the scoped dark IR snapshot runtime.

This module deliberately accepts only an already-collected observation.  It
does not inspect processes, Docker, systemd, sockets, or a network.  Its
successful result is an informational safety projection and is never a
promotion, writer, or execution authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Mapping


__all__ = (
    "MAX_WEBAPP_IR_DARK_SNAPSHOT_OBSERVATION_AGE_SECONDS",
    "WebappIrDarkSnapshotPreflightError",
    "WebappIrDarkSnapshotPreflightObservation",
    "WebappIrDarkSnapshotPreflightResult",
    "verify_webapp_ir_dark_snapshot_preflight",
)


MAX_WEBAPP_IR_DARK_SNAPSHOT_OBSERVATION_AGE_SECONDS = 30
_OBSERVATION_KEYS = frozenset(
    {
        "services",
        "network_mode",
        "published_ports",
        "promotion_state",
        "promotion_unit_state",
        "refresh_timer_enabled",
        "refresh_timer_state",
        "observed_at",
    }
)
_SERVICE_KEYS = frozenset({"state", "health"})
_REQUIRED_SERVICE = "snapshot_db"
_FORBIDDEN_SERVICES = frozenset({"app", "bot", "redis", "sync_worker", "migration"})


class WebappIrDarkSnapshotPreflightError(ValueError):
    """Raised when an observation cannot prove the dark-host boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class WebappIrDarkSnapshotPreflightObservation:
    """Redacted, already-collected state required by the pure verifier."""

    services: Mapping[str, Mapping[str, str]]
    network_mode: str
    published_ports: tuple[object, ...]
    promotion_state: str
    promotion_unit_state: str
    refresh_timer_enabled: bool
    refresh_timer_state: str
    observed_at: datetime

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "WebappIrDarkSnapshotPreflightObservation":
        """Parse one exact, closed observation mapping without probing a host."""

        if not isinstance(value, Mapping) or set(value) != _OBSERVATION_KEYS:
            _fail("IR_DARK_SNAPSHOT_OBSERVATION_INVALID")
        services = value["services"]
        ports = value["published_ports"]
        if not isinstance(services, Mapping) or type(ports) is not tuple:
            _fail("IR_DARK_SNAPSHOT_OBSERVATION_INVALID")
        copied_services: dict[str, Mapping[str, str]] = {}
        for name, service in services.items():
            if type(name) is not str or not isinstance(service, Mapping):
                _fail("IR_DARK_SNAPSHOT_OBSERVATION_INVALID")
            if set(service) != _SERVICE_KEYS:
                _fail("IR_DARK_SNAPSHOT_OBSERVATION_INVALID")
            state, health = service["state"], service["health"]
            if type(state) is not str or type(health) is not str:
                _fail("IR_DARK_SNAPSHOT_OBSERVATION_INVALID")
            copied_services[name] = MappingProxyType({"state": state, "health": health})
        if (
            type(value["network_mode"]) is not str
            or type(value["promotion_state"]) is not str
            or type(value["promotion_unit_state"]) is not str
            or type(value["refresh_timer_state"]) is not str
            or type(value["refresh_timer_enabled"]) is not bool
            or not isinstance(value["observed_at"], datetime)
        ):
            _fail("IR_DARK_SNAPSHOT_OBSERVATION_INVALID")
        return cls(
            services=MappingProxyType(copied_services),
            network_mode=value["network_mode"],  # type: ignore[arg-type]
            published_ports=ports,
            promotion_state=value["promotion_state"],  # type: ignore[arg-type]
            promotion_unit_state=value["promotion_unit_state"],  # type: ignore[arg-type]
            refresh_timer_enabled=value["refresh_timer_enabled"],  # type: ignore[arg-type]
            refresh_timer_state=value["refresh_timer_state"],  # type: ignore[arg-type]
            observed_at=value["observed_at"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class WebappIrDarkSnapshotPreflightResult:
    """A successful safety statement, explicitly not an authority grant."""

    observation: WebappIrDarkSnapshotPreflightObservation
    promotion: bool = False
    writer: bool = False
    execution: bool = False

    def __post_init__(self) -> None:
        if self.promotion is not False or self.writer is not False or self.execution is not False:
            _fail("IR_DARK_SNAPSHOT_AUTHORIZATION_FORBIDDEN")

    @property
    def output(self) -> Mapping[str, bool]:
        """The only emitted capability-like fields, all permanently false."""

        return MappingProxyType(
            {"promotion": self.promotion, "writer": self.writer, "execution": self.execution}
        )


def _fail(code: str) -> None:
    raise WebappIrDarkSnapshotPreflightError(code)


def _observation(value: object) -> WebappIrDarkSnapshotPreflightObservation:
    if type(value) is WebappIrDarkSnapshotPreflightObservation:
        return value
    if isinstance(value, Mapping):
        return WebappIrDarkSnapshotPreflightObservation.from_mapping(value)
    _fail("IR_DARK_SNAPSHOT_OBSERVATION_INVALID")


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail("IR_DARK_SNAPSHOT_OBSERVED_AT_INVALID")
    return value.astimezone(timezone.utc)


def verify_webapp_ir_dark_snapshot_preflight(
    observation: WebappIrDarkSnapshotPreflightObservation | Mapping[str, object],
    *,
    now: datetime,
) -> WebappIrDarkSnapshotPreflightResult:
    """Verify the fixed Docker/systemd snapshot-only posture without side effects.

    This is deliberately a scoped runtime statement, not evidence that every
    arbitrary host process outside the collected Docker/systemd boundary is
    absent.
    """

    candidate = _observation(observation)
    current = _utc(now)
    observed_at = _utc(candidate.observed_at)
    age = current - observed_at
    if age < timedelta(0) or age > timedelta(
        seconds=MAX_WEBAPP_IR_DARK_SNAPSHOT_OBSERVATION_AGE_SECONDS
    ):
        _fail("IR_DARK_SNAPSHOT_OBSERVATION_STALE")

    services = candidate.services
    if (
        type(candidate.network_mode) is not str
        or type(candidate.promotion_state) is not str
        or type(candidate.promotion_unit_state) is not str
        or type(candidate.refresh_timer_state) is not str
        or type(candidate.refresh_timer_enabled) is not bool
    ):
        _fail("IR_DARK_SNAPSHOT_OBSERVATION_INVALID")
    if not isinstance(services, Mapping) or set(services) != {_REQUIRED_SERVICE}:
        _fail("IR_DARK_SNAPSHOT_SERVICES_NOT_SNAPSHOT_DB_ONLY")
    # Keeping the forbidden names explicit makes this invariant clear even if a
    # caller supplies a custom Mapping implementation.
    if _FORBIDDEN_SERVICES.intersection(services):
        _fail("IR_DARK_SNAPSHOT_FORBIDDEN_SERVICE_PRESENT")
    snapshot_db = services.get(_REQUIRED_SERVICE)
    if not isinstance(snapshot_db, Mapping) or set(snapshot_db) != _SERVICE_KEYS:
        _fail("IR_DARK_SNAPSHOT_DB_STATE_INVALID")
    if snapshot_db.get("state") != "running" or snapshot_db.get("health") != "healthy":
        _fail("IR_DARK_SNAPSHOT_DB_NOT_HEALTHY_RUNNING")

    if candidate.network_mode != "none":
        _fail("IR_DARK_SNAPSHOT_NETWORK_NOT_NONE")
    if type(candidate.published_ports) is not tuple or candidate.published_ports:
        _fail("IR_DARK_SNAPSHOT_PORTS_PRESENT")
    if candidate.promotion_state != "inactive" or candidate.promotion_unit_state != "masked":
        _fail("IR_DARK_SNAPSHOT_PROMOTION_NOT_INACTIVE_MASKED")
    if type(candidate.refresh_timer_enabled) is not bool or not candidate.refresh_timer_enabled:
        _fail("IR_DARK_SNAPSHOT_REFRESH_TIMER_NOT_ENABLED")
    if candidate.refresh_timer_state != "active":
        _fail("IR_DARK_SNAPSHOT_REFRESH_TIMER_NOT_ACTIVE")

    return WebappIrDarkSnapshotPreflightResult(observation=candidate)
