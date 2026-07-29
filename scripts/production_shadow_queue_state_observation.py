#!/usr/bin/env python3
"""Pure reducer for redacted three-runtime-role queue-state snapshots.

This module has no filesystem, database, Redis, Docker, subprocess, or
network operation. A role-local collector may later supply redacted snapshots,
but this reducer accepts only their bounded counters and creates the exact
queue observation shape consumed by the convergence gate. It neither publishes
that observation nor integrates it into a source set.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Mapping, Sequence
from uuid import UUID


ROLE_SNAPSHOT_SCHEMA = "production-shadow-convergence-queue-role-snapshot-v1"
QUEUE_OBSERVATION_SCHEMA = "production-shadow-convergence-queue-observation-v1"
QUEUE_STATE_SCHEMA = "production-shadow-convergence-queue-state-v1"
RUNTIME_ROLES = ("bot_fi", "webapp_fi", "webapp_ir")
QUEUE_COUNTERS = (
    "running_business_mutator_count",
    "due_otp_job_count",
    "inflight_effect_count",
    "telegram_lease_count",
    "provider_attempt_delta_count",
)
IDENTITY_FIELDS = (
    "campaign_id",
    "operation_id",
    "release_sha",
    "release_tree_sha",
    "manifest_sha256",
    "plan_sha256",
    "approval_sha256",
)
CONTEXT_FIELDS = frozenset({*IDENTITY_FIELDS, "phase_started_at"})
ROLE_SNAPSHOT_FIELDS = frozenset(
    {
        "schema",
        "status",
        *IDENTITY_FIELDS,
        "role",
        "captured_at",
        "observed_at",
        "queue_counters",
        "queue_state_sha256",
    }
)
OBSERVATION_FIELDS = frozenset(
    {
        "schema",
        "status",
        *IDENTITY_FIELDS,
        "observed_at",
        *QUEUE_COUNTERS,
        "queue_state_sha256",
    }
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
ZERO_SHA256 = "0" * 64
MAX_SOURCE_AGE = timedelta(minutes=15)
MAX_SOURCE_SKEW = timedelta(minutes=2)
MAX_FUTURE_SKEW = timedelta(seconds=5)


class QueueStateObservationError(ValueError):
    """The redacted queue-state closure cannot prove a safe observation."""


@dataclass(frozen=True)
class QueueStateIdentity:
    campaign_id: str
    operation_id: str
    release_sha: str
    release_tree_sha: str
    manifest_sha256: str
    plan_sha256: str
    approval_sha256: str
    phase_started_at: datetime

    def fields(self) -> dict[str, str]:
        return {
            "campaign_id": self.campaign_id,
            "operation_id": self.operation_id,
            "release_sha": self.release_sha,
            "release_tree_sha": self.release_tree_sha,
            "manifest_sha256": self.manifest_sha256,
            "plan_sha256": self.plan_sha256,
            "approval_sha256": self.approval_sha256,
        }


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise QueueStateObservationError("JSON document has duplicate fields")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise QueueStateObservationError("queue-state value is not canonical JSON") from exc


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise QueueStateObservationError(f"{label} is invalid")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise QueueStateObservationError(f"{label} is invalid") from exc
    if str(parsed) != value:
        raise QueueStateObservationError(f"{label} is invalid")
    return value


def _sha40(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA40_RE.fullmatch(value) is None:
        raise QueueStateObservationError(f"{label} is invalid")
    return value


def _sha256_nonzero(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == ZERO_SHA256:
        raise QueueStateObservationError(f"{label} is invalid")
    return value


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise QueueStateObservationError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise QueueStateObservationError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise QueueStateObservationError(f"{label} lacks a timezone")
    return parsed.astimezone(timezone.utc)


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _nonnegative(value: Any, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise QueueStateObservationError(f"{label} is invalid")
    return value


def validate_identity(value: Mapping[str, Any]) -> QueueStateIdentity:
    """Validate the exact controller bindings needed by every role snapshot."""

    if not isinstance(value, Mapping) or set(value) != CONTEXT_FIELDS:
        raise QueueStateObservationError("queue-state identity fields differ")
    campaign = _uuid(value["campaign_id"], label="campaign_id")
    operation = _uuid(value["operation_id"], label="operation_id")
    if campaign == operation:
        raise QueueStateObservationError("campaign and operation differ")
    return QueueStateIdentity(
        campaign_id=campaign,
        operation_id=operation,
        release_sha=_sha40(value["release_sha"], label="release_sha"),
        release_tree_sha=_sha40(value["release_tree_sha"], label="release_tree_sha"),
        manifest_sha256=_sha256_nonzero(value["manifest_sha256"], label="manifest_sha256"),
        plan_sha256=_sha256_nonzero(value["plan_sha256"], label="plan_sha256"),
        approval_sha256=_sha256_nonzero(value["approval_sha256"], label="approval_sha256"),
        phase_started_at=_timestamp(value["phase_started_at"], label="phase_started_at"),
    )


def _role_snapshot_digest(document: Mapping[str, Any]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "queue_state_sha256"})


def _validate_role_snapshot(
    value: Any,
    *,
    role: str,
    identity: QueueStateIdentity,
    now: datetime,
) -> tuple[dict[str, Any], datetime]:
    if role not in RUNTIME_ROLES:
        raise QueueStateObservationError("queue-state role is invalid")
    if not isinstance(value, Mapping) or set(value) != ROLE_SNAPSHOT_FIELDS:
        raise QueueStateObservationError(f"{role} queue snapshot fields differ")
    document = dict(value)
    if (
        document.get("schema") != ROLE_SNAPSHOT_SCHEMA
        or document.get("status") != "observed-redacted"
        or document.get("role") != role
        or any(document.get(key) != item for key, item in identity.fields().items())
    ):
        raise QueueStateObservationError(f"{role} queue snapshot identity differs")
    counters = document.get("queue_counters")
    if not isinstance(counters, Mapping) or set(counters) != set(QUEUE_COUNTERS):
        raise QueueStateObservationError(f"{role} queue counters differ")
    for counter in QUEUE_COUNTERS:
        _nonnegative(counters[counter], label=f"{role} {counter}")
    captured = _timestamp(document.get("captured_at"), label=f"{role} captured_at")
    observed = _timestamp(document.get("observed_at"), label=f"{role} observed_at")
    if (
        captured < identity.phase_started_at
        or observed < captured
        or captured > now + MAX_FUTURE_SKEW
        or observed > now + MAX_FUTURE_SKEW
        or now - captured > MAX_SOURCE_AGE
        or now - observed > MAX_SOURCE_AGE
        or observed - captured > MAX_SOURCE_SKEW
    ):
        raise QueueStateObservationError(f"{role} queue snapshot freshness differs")
    if (
        _sha256_nonzero(document.get("queue_state_sha256"), label=f"{role} queue state")
        != _role_snapshot_digest(document)
    ):
        raise QueueStateObservationError(f"{role} queue snapshot digest differs")
    return document, observed


def _observation_digest(
    *,
    identity: QueueStateIdentity,
    observed_at: datetime,
    counters: Mapping[str, int],
    snapshots: Mapping[str, Mapping[str, Any]],
) -> str:
    return _sha256(
        {
            "schema": QUEUE_STATE_SCHEMA,
            **identity.fields(),
            "observed_at": _timestamp_text(observed_at),
            "queue_counters": dict(counters),
            "role_snapshots": {
                role: {
                    "captured_at": snapshots[role]["captured_at"],
                    "observed_at": snapshots[role]["observed_at"],
                    "queue_state_sha256": snapshots[role]["queue_state_sha256"],
                }
                for role in RUNTIME_ROLES
            },
        }
    )


def _validated_snapshots(
    role_snapshots: Mapping[str, Mapping[str, Any]],
    *,
    identity: QueueStateIdentity,
    now: datetime,
) -> tuple[dict[str, dict[str, Any]], list[datetime]]:
    if not isinstance(role_snapshots, Mapping) or set(role_snapshots) != set(RUNTIME_ROLES):
        raise QueueStateObservationError("queue-state role snapshot set differs")
    snapshots: dict[str, dict[str, Any]] = {}
    observed_times: list[datetime] = []
    for role in RUNTIME_ROLES:
        snapshot, observed = _validate_role_snapshot(
            role_snapshots[role],
            role=role,
            identity=identity,
            now=now,
        )
        snapshots[role] = snapshot
        observed_times.append(observed)
    if max(observed_times) - min(observed_times) > MAX_SOURCE_SKEW:
        raise QueueStateObservationError("queue-state role snapshot skew differs")
    return snapshots, observed_times


def build_queue_observation(
    *,
    identity: Mapping[str, Any],
    role_snapshots: Mapping[str, Mapping[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Reduce three redacted role snapshots to the gate queue observation.

    Counts are summed only after each role proves a non-negative value. A zero
    aggregate therefore proves every one of the five counters is zero on every
    runtime role; no cancellation or caller-provided boolean is accepted.
    """

    bound = validate_identity(identity)
    if now.tzinfo is None or now.utcoffset() is None:
        raise QueueStateObservationError("queue-state clock lacks a timezone")
    current = now.astimezone(timezone.utc)
    snapshots, observed_times = _validated_snapshots(
        role_snapshots,
        identity=bound,
        now=current,
    )
    counters = {
        counter: sum(int(snapshots[role]["queue_counters"][counter]) for role in RUNTIME_ROLES)
        for counter in QUEUE_COUNTERS
    }
    if any(value != 0 for value in counters.values()):
        raise QueueStateObservationError("queue-state reports live or due work")
    observed_at = max(observed_times)
    document: dict[str, Any] = {
        "schema": QUEUE_OBSERVATION_SCHEMA,
        "status": "observed",
        **bound.fields(),
        "observed_at": _timestamp_text(observed_at),
        **counters,
        "queue_state_sha256": _observation_digest(
            identity=bound,
            observed_at=observed_at,
            counters=counters,
            snapshots=snapshots,
        ),
    }
    return validate_queue_observation(
        document,
        identity=bound,
        role_snapshots=snapshots,
        now=current,
    )


def validate_queue_observation(
    value: Any,
    *,
    identity: QueueStateIdentity | Mapping[str, Any],
    role_snapshots: Mapping[str, Mapping[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Validate the pure reducer output before a separate publication layer."""

    bound = identity if isinstance(identity, QueueStateIdentity) else validate_identity(identity)
    if not isinstance(value, Mapping) or set(value) != OBSERVATION_FIELDS:
        raise QueueStateObservationError("queue observation fields differ")
    document = dict(value)
    if (
        document.get("schema") != QUEUE_OBSERVATION_SCHEMA
        or document.get("status") != "observed"
        or any(document.get(key) != item for key, item in bound.fields().items())
    ):
        raise QueueStateObservationError("queue observation identity differs")
    if now.tzinfo is None or now.utcoffset() is None:
        raise QueueStateObservationError("queue-state clock lacks a timezone")
    observed = _timestamp(document.get("observed_at"), label="queue observation observed_at")
    current = now.astimezone(timezone.utc)
    if (
        observed < bound.phase_started_at
        or observed > current + MAX_FUTURE_SKEW
        or current - observed > MAX_SOURCE_AGE
    ):
        raise QueueStateObservationError("queue observation freshness differs")
    for counter in QUEUE_COUNTERS:
        if _nonnegative(document.get(counter), label=f"queue {counter}") != 0:
            raise QueueStateObservationError("queue observation reports live or due work")
    snapshots, observed_times = _validated_snapshots(
        role_snapshots,
        identity=bound,
        now=current,
    )
    counters = {
        counter: sum(int(snapshots[role]["queue_counters"][counter]) for role in RUNTIME_ROLES)
        for counter in QUEUE_COUNTERS
    }
    if any(value != 0 for value in counters.values()):
        raise QueueStateObservationError("queue observation reports live or due work")
    if observed != max(observed_times) or any(document[counter] != counters[counter] for counter in QUEUE_COUNTERS):
        raise QueueStateObservationError("queue observation snapshot reduction differs")
    if (
        _sha256_nonzero(document.get("queue_state_sha256"), label="queue observation state")
        != _observation_digest(
            identity=bound,
            observed_at=observed,
            counters=counters,
            snapshots=snapshots,
        )
    ):
        raise QueueStateObservationError("queue observation digest differs")
    return document


def validate_published_queue_observation(
    value: Any,
    *,
    identity: QueueStateIdentity | Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Validate a redacted reduced record after its input closure is archived.

    The source-set layer receives only the already-reduced observation, not
    the three role snapshots. It can still enforce the exact public schema,
    controller identity, freshness, all-zero counters, and a nonzero closure
    digest; full reduction verification remains available above.
    """

    bound = identity if isinstance(identity, QueueStateIdentity) else validate_identity(identity)
    if now.tzinfo is None or now.utcoffset() is None:
        raise QueueStateObservationError("queue-state clock lacks a timezone")
    if not isinstance(value, Mapping) or set(value) != OBSERVATION_FIELDS:
        raise QueueStateObservationError("queue observation fields differ")
    document = dict(value)
    if (
        document.get("schema") != QUEUE_OBSERVATION_SCHEMA
        or document.get("status") != "observed"
        or any(document.get(key) != item for key, item in bound.fields().items())
    ):
        raise QueueStateObservationError("queue observation identity differs")
    observed = _timestamp(document.get("observed_at"), label="queue observation observed_at")
    current = now.astimezone(timezone.utc)
    if (
        observed < bound.phase_started_at
        or observed > current + MAX_FUTURE_SKEW
        or current - observed > MAX_SOURCE_AGE
    ):
        raise QueueStateObservationError("queue observation freshness differs")
    for counter in QUEUE_COUNTERS:
        if _nonnegative(document.get(counter), label=f"queue {counter}") != 0:
            raise QueueStateObservationError("queue observation reports live or due work")
    _sha256_nonzero(document.get("queue_state_sha256"), label="queue observation state")
    return document
