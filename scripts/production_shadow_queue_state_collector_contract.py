#!/usr/bin/env python3
"""Pure contract for a future container-safe queue-state collector.

The existing convergence collector deliberately proves database parity and
blob state only.  It does not observe application mutators, due Redis work,
effect execution, leases, or provider-attempt deltas.  This module defines
the bounded, redacted input/output contract a later exact-release collector
must satisfy.  It has no filesystem, network, Docker, database, Redis, or
subprocess operation and cannot make a source set ready.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Mapping

from scripts import production_shadow_queue_state_observation as QUEUE


PLAN_SCHEMA = "production-shadow-convergence-queue-runtime-collector-plan-v1"
OUTPUT_SCHEMA = "production-shadow-convergence-queue-runtime-collector-output-v1"
ROLE_SNAPSHOT_SCHEMA = QUEUE.ROLE_SNAPSHOT_SCHEMA
COLLECTOR_ENTRYPOINT = "scripts/collect_production_shadow_queue_state_snapshot.py"
RUNTIME_INPUTS = ("application_database", "application_redis")
SOURCE_PROOF_FIELDS = frozenset({"source", "read_only", "snapshot_sha256"})
PLAN_FIELDS = frozenset(
    {
        "schema",
        "status",
        *QUEUE.IDENTITY_FIELDS,
        "phase_started_at",
        "role",
        "runtime_target_binding_sha256",
        "app_image_id",
        "collector_entrypoint",
        "collector_source_manifest_sha256",
        "runtime_inputs",
        "mutation_forbidden",
        "queue_collector_plan_sha256",
    }
)
OUTPUT_FIELDS = frozenset(
    {
        "schema",
        "status",
        *QUEUE.IDENTITY_FIELDS,
        "role",
        "queue_collector_plan_sha256",
        "captured_at",
        "observed_at",
        "queue_counters",
        "source_proofs",
        "collector_output_sha256",
    }
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_SOURCE_AGE = timedelta(minutes=15)
MAX_SOURCE_SKEW = timedelta(minutes=2)
MAX_FUTURE_SKEW = timedelta(seconds=5)


class QueueStateCollectorContractError(ValueError):
    """A planned or redacted queue collection is not safely bound."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise QueueStateCollectorContractError("queue collector JSON has duplicate fields")
        document[key] = value
    return document


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
        raise QueueStateCollectorContractError("queue collector value is not canonical JSON") from exc


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _hash(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        raise QueueStateCollectorContractError(f"{label} is invalid")
    return value


def _timestamp(value: Any, *, label: str) -> datetime:
    try:
        return QUEUE._timestamp(value, label=label)  # noqa: SLF001
    except QUEUE.QueueStateObservationError as exc:
        raise QueueStateCollectorContractError(f"{label} is invalid") from exc


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _identity_from_plan(value: Mapping[str, Any]) -> QUEUE.QueueStateIdentity:
    try:
        return QUEUE.validate_identity(
            {key: value[key] for key in (*QUEUE.IDENTITY_FIELDS, "phase_started_at")}
        )
    except (KeyError, QUEUE.QueueStateObservationError) as exc:
        raise QueueStateCollectorContractError("queue collector plan identity is invalid") from exc


def _plan_digest(document: Mapping[str, Any]) -> str:
    return _sha256(
        {key: value for key, value in document.items() if key != "queue_collector_plan_sha256"}
    )


def _output_digest(document: Mapping[str, Any]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "collector_output_sha256"})


def build_plan(
    *,
    identity: Mapping[str, Any],
    role: str,
    runtime_target_binding_sha256: str,
    app_image_id: str,
    collector_source_manifest_sha256: str,
) -> dict[str, Any]:
    """Create one static plan; this does not launch or install anything."""

    checked_identity = QUEUE.validate_identity(identity)
    if role not in QUEUE.RUNTIME_ROLES:
        raise QueueStateCollectorContractError("queue collector role is invalid")
    if not isinstance(app_image_id, str) or IMAGE_ID_RE.fullmatch(app_image_id) is None:
        raise QueueStateCollectorContractError("queue collector app image is invalid")
    document: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "status": "planned-only",
        **checked_identity.fields(),
        "phase_started_at": _timestamp_text(checked_identity.phase_started_at),
        "role": role,
        "runtime_target_binding_sha256": _hash(
            runtime_target_binding_sha256, label="queue collector runtime binding"
        ),
        "app_image_id": app_image_id,
        "collector_entrypoint": COLLECTOR_ENTRYPOINT,
        "collector_source_manifest_sha256": _hash(
            collector_source_manifest_sha256, label="queue collector source manifest"
        ),
        "runtime_inputs": list(RUNTIME_INPUTS),
        "mutation_forbidden": True,
        "queue_collector_plan_sha256": "0" * 64,
    }
    document["queue_collector_plan_sha256"] = _plan_digest(document)
    return document


def validate_plan(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != PLAN_FIELDS:
        raise QueueStateCollectorContractError("queue collector plan fields differ")
    document = dict(value)
    identity = _identity_from_plan(document)
    if (
        document.get("schema") != PLAN_SCHEMA
        or document.get("status") != "planned-only"
        or document.get("role") not in QUEUE.RUNTIME_ROLES
        or document.get("collector_entrypoint") != COLLECTOR_ENTRYPOINT
        or document.get("runtime_inputs") != list(RUNTIME_INPUTS)
        or document.get("mutation_forbidden") is not True
        or not isinstance(document.get("app_image_id"), str)
        or IMAGE_ID_RE.fullmatch(document["app_image_id"]) is None
    ):
        raise QueueStateCollectorContractError("queue collector plan differs")
    del identity
    _hash(document.get("runtime_target_binding_sha256"), label="queue collector runtime binding")
    _hash(document.get("collector_source_manifest_sha256"), label="queue collector source manifest")
    if (
        _hash(
            document.get("queue_collector_plan_sha256"), label="queue collector plan"
        )
        != _plan_digest(document)
    ):
        raise QueueStateCollectorContractError("queue collector plan digest differs")
    return document


def parse_collector_output(payload: bytes) -> dict[str, Any]:
    """Strictly parse a bounded redacted collector result without opening paths."""

    if not isinstance(payload, bytes) or not payload or len(payload) > 256 * 1024:
        raise QueueStateCollectorContractError("queue collector output size is invalid")
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QueueStateCollectorContractError("queue collector output is not strict ASCII JSON") from exc
    if not isinstance(document, dict) or _canonical_json(document) != payload:
        raise QueueStateCollectorContractError("queue collector output is not canonical JSON")
    return document


def _validate_source_proofs(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != set(RUNTIME_INPUTS):
        raise QueueStateCollectorContractError("queue collector source proof coverage differs")
    result: dict[str, dict[str, Any]] = {}
    for source in RUNTIME_INPUTS:
        proof = value[source]
        if (
            not isinstance(proof, Mapping)
            or set(proof) != SOURCE_PROOF_FIELDS
            or proof.get("source") != source
            or proof.get("read_only") is not True
        ):
            raise QueueStateCollectorContractError("queue collector source proof differs")
        result[source] = {
            "source": source,
            "read_only": True,
            "snapshot_sha256": _hash(
                proof.get("snapshot_sha256"), label=f"queue collector {source} snapshot"
            ),
        }
    return result


def validate_collector_output(
    value: Any, *, plan: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    """Validate a future role-local collector result against one static plan."""

    checked_plan = validate_plan(plan)
    if now.tzinfo is None or now.utcoffset() is None:
        raise QueueStateCollectorContractError("queue collector clock lacks a timezone")
    current = now.astimezone(timezone.utc)
    if not isinstance(value, Mapping) or set(value) != OUTPUT_FIELDS:
        raise QueueStateCollectorContractError("queue collector output fields differ")
    document = dict(value)
    if (
        document.get("schema") != OUTPUT_SCHEMA
        or document.get("status") != "observed-redacted"
        or document.get("role") != checked_plan["role"]
        or document.get("queue_collector_plan_sha256")
        != checked_plan["queue_collector_plan_sha256"]
        or any(document.get(key) != checked_plan[key] for key in QUEUE.IDENTITY_FIELDS)
    ):
        raise QueueStateCollectorContractError("queue collector output identity differs")
    captured = _timestamp(document.get("captured_at"), label="queue collector captured_at")
    observed = _timestamp(document.get("observed_at"), label="queue collector observed_at")
    phase_started = _timestamp(checked_plan["phase_started_at"], label="queue collector phase_started_at")
    if (
        captured < phase_started
        or observed < captured
        or captured > current + MAX_FUTURE_SKEW
        or observed > current + MAX_FUTURE_SKEW
        or current - captured > MAX_SOURCE_AGE
        or current - observed > MAX_SOURCE_AGE
        or observed - captured > MAX_SOURCE_SKEW
    ):
        raise QueueStateCollectorContractError("queue collector output freshness differs")
    counters = document.get("queue_counters")
    if not isinstance(counters, Mapping) or set(counters) != set(QUEUE.QUEUE_COUNTERS):
        raise QueueStateCollectorContractError("queue collector counters differ")
    normalized_counters: dict[str, int] = {}
    for counter in QUEUE.QUEUE_COUNTERS:
        try:
            normalized_counters[counter] = QUEUE._nonnegative(counters[counter], label=counter)  # noqa: SLF001
        except QUEUE.QueueStateObservationError as exc:
            raise QueueStateCollectorContractError("queue collector counter is invalid") from exc
    proofs = _validate_source_proofs(document.get("source_proofs"))
    if _hash(document.get("collector_output_sha256"), label="queue collector output") != _output_digest(document):
        raise QueueStateCollectorContractError("queue collector output digest differs")
    return {
        **{key: checked_plan[key] for key in QUEUE.IDENTITY_FIELDS},
        "role": checked_plan["role"],
        "queue_collector_plan_sha256": checked_plan["queue_collector_plan_sha256"],
        "captured_at": _timestamp_text(captured),
        "observed_at": _timestamp_text(observed),
        "queue_counters": normalized_counters,
        "source_proofs": proofs,
        "collector_output_sha256": document["collector_output_sha256"],
    }


def reduce_to_role_snapshot(
    value: Any, *, plan: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    """Produce the existing reducer's input; callers must retain raw receipt too.

    The reduction is intentionally not a publication or readiness operation.
    A future source-set integration must bind both this result and the raw
    validated collector receipt before it can treat queue evidence as usable.
    """

    checked = validate_collector_output(value, plan=plan, now=now)
    role_snapshot: dict[str, Any] = {
        "schema": ROLE_SNAPSHOT_SCHEMA,
        "status": "observed-redacted",
        **{key: checked[key] for key in QUEUE.IDENTITY_FIELDS},
        "role": checked["role"],
        "captured_at": checked["captured_at"],
        "observed_at": checked["observed_at"],
        "queue_counters": checked["queue_counters"],
        "queue_state_sha256": "0" * 64,
    }
    role_snapshot["queue_state_sha256"] = QUEUE._role_snapshot_digest(role_snapshot)  # noqa: SLF001
    return {
        "role_snapshot": role_snapshot,
        "collector_output_sha256": checked["collector_output_sha256"],
        "queue_collector_plan_sha256": checked["queue_collector_plan_sha256"],
    }
