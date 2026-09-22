"""Two-site preparation contract foundation for AE01-40.

Provides immutable startup identity, canonical fingerprinting,
drift detection, and source-free bounded preparation telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re

__all__ = [
    "PreparationConfigError",
    "PreparationStartup",
    "assert_same_startup",
    "preparation_telemetry",
]

_EXPECTED_STARTUP_KEYS = frozenset({
    "schema_version",
    "route",
    "cycle_id",
    "source_head",
    "assignment_sha256",
})

_VALID_ROUTES = ("one_shot", "legacy")
_VALID_OUTCOMES = frozenset({"TASK_READY", "TASK_BLOCKED", "ERROR", "UNKNOWN"})

_CYCLE_ID_PATTERN = re.compile(r"^cycle-[0-9]{4}-[0-9]{8}T[0-9]{6}Z-[0-9]+\Z")
_HEX40_PATTERN = re.compile(r"^[0-9a-f]{40}\Z")
_HEX64_PATTERN = re.compile(r"^[0-9a-f]{64}\Z")

_MAX_SAFE_INTEGER = 1 << 53
_MAX_COUNT = (1 << 63) - 1


class PreparationConfigError(ValueError):
    """Raised when preparation startup identity or configuration is invalid."""


@dataclass(frozen=True, slots=True)
class PreparationStartup:
    """Immutable transport identity for preparation startup."""

    schema_version: int
    route: str
    cycle_id: str
    source_head: str
    assignment_sha256: str

    def __post_init__(self) -> None:
        if type(self) is not PreparationStartup:
            raise PreparationConfigError("Subclasses of PreparationStartup are not permitted")

        if type(self.schema_version) is not int or self.schema_version != 1:
            raise PreparationConfigError("Invalid schema_version: must be exact int 1")

        if type(self.route) is not str or self.route not in _VALID_ROUTES:
            raise PreparationConfigError("Invalid route: must be 'one_shot' or 'legacy'")

        if (
            type(self.cycle_id) is not str
            or len(self.cycle_id) > 80
            or not _CYCLE_ID_PATTERN.fullmatch(self.cycle_id)
        ):
            raise PreparationConfigError("Invalid cycle_id: must match canonical format within 80 chars")

        if type(self.source_head) is not str or not _HEX40_PATTERN.fullmatch(self.source_head):
            raise PreparationConfigError("Invalid source_head: must be exact lowercase 40 hex")

        if type(self.assignment_sha256) is not str or not _HEX64_PATTERN.fullmatch(self.assignment_sha256):
            raise PreparationConfigError("Invalid assignment_sha256: must be exact lowercase 64 hex")

    @classmethod
    def from_mapping(cls, raw: object) -> PreparationStartup:
        if type(raw) is not dict:
            raise PreparationConfigError("raw mapping must be an exact builtin dict")

        if len(raw) != 5:
            raise PreparationConfigError("raw mapping must contain exactly 5 keys")

        for key in raw:
            if type(key) is not str:
                raise PreparationConfigError("raw mapping keys must be exact str")

        if frozenset(raw.keys()) != _EXPECTED_STARTUP_KEYS:
            raise PreparationConfigError("raw mapping keys do not match expected schema")

        return cls(
            schema_version=raw["schema_version"],
            route=raw["route"],
            cycle_id=raw["cycle_id"],
            source_head=raw["source_head"],
            assignment_sha256=raw["assignment_sha256"],
        )

    def to_mapping(self) -> dict[str, int | str]:
        return {
            "schema_version": self.schema_version,
            "route": self.route,
            "cycle_id": self.cycle_id,
            "source_head": self.source_head,
            "assignment_sha256": self.assignment_sha256,
        }

    @property
    def fingerprint(self) -> str:
        canonical_bytes = json.dumps(
            self.to_mapping(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        return hashlib.sha256(canonical_bytes).hexdigest()


def assert_same_startup(expected: object, observed: object) -> None:
    """Assert identity equality between two PreparationStartup instances."""
    if type(expected) is not PreparationStartup or type(observed) is not PreparationStartup:
        raise PreparationConfigError("Both expected and observed must be exact PreparationStartup instances")

    if (
        expected.schema_version != observed.schema_version
        or expected.route != observed.route
        or expected.cycle_id != observed.cycle_id
        or expected.source_head != observed.source_head
        or expected.assignment_sha256 != observed.assignment_sha256
    ):
        raise PreparationConfigError("Startup identity drift detected")

    return None


def preparation_telemetry(
    startup: PreparationStartup,
    *,
    outcome: object,
    elapsed_seconds: object = None,
    source_file_count: object = None,
    source_bytes: object = None,
    task_bytes: object = None,
    issue_count: object = None,
) -> dict[str, object]:
    """Emit bounded, source-free preparation telemetry."""
    if type(startup) is not PreparationStartup:
        raise PreparationConfigError("startup must be an exact PreparationStartup instance")

    if type(outcome) is str and outcome in _VALID_OUTCOMES:
        validated_outcome = outcome
    else:
        validated_outcome = "UNKNOWN"

    validated_elapsed = None
    t_elapsed = type(elapsed_seconds)
    if t_elapsed is int:
        if 0 <= elapsed_seconds <= _MAX_SAFE_INTEGER:
            validated_elapsed = elapsed_seconds
    elif t_elapsed is float:
        if math.isfinite(elapsed_seconds) and 0.0 <= elapsed_seconds <= float(_MAX_SAFE_INTEGER):
            validated_elapsed = elapsed_seconds

    def _validate_count(val: object) -> int | None:
        if type(val) is int:
            if 0 <= val <= _MAX_COUNT:
                return val
        return None

    validated_source_file_count = _validate_count(source_file_count)
    validated_source_bytes = _validate_count(source_bytes)
    validated_task_bytes = _validate_count(task_bytes)
    validated_issue_count = _validate_count(issue_count)

    measurements = (
        validated_elapsed,
        validated_source_file_count,
        validated_source_bytes,
        validated_task_bytes,
        validated_issue_count,
    )
    valid_count = sum(1 for m in measurements if m is not None)
    if valid_count == 5:
        measurement_state = "COMPLETE"
    elif valid_count > 0:
        measurement_state = "PARTIAL"
    else:
        measurement_state = "UNAVAILABLE"

    return {
        "schema_version": 1,
        "startup": startup.to_mapping(),
        "startup_sha256": startup.fingerprint,
        "outcome": validated_outcome,
        "elapsed_seconds": validated_elapsed,
        "source_file_count": validated_source_file_count,
        "source_bytes": validated_source_bytes,
        "task_bytes": validated_task_bytes,
        "issue_count": validated_issue_count,
        "measurement_state": measurement_state,
        "efficiency": "UNKNOWN",
    }
