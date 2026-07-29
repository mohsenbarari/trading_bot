#!/usr/bin/env python3
"""Acquire, renew, and read back one production-shadow Writer lease.

The worker is intentionally limited to the two prepared WebApp databases.
WebApp-FI may acquire/import the initial epoch-1 lease or renew that same
lease.  WebApp-IR is read-only and must remain fenced without a local lease.
The Witness is contacted only by the isolated WebApp-FI writer-control
one-off; no API, effect worker, Bot, Redis, or public service is started.

Every remote transition request ID and all parameters that participate in the
Witness request hash are persisted before the transition.  A retry therefore
replays the exact Witness receipt after an ambiguous response or controller
EOF instead of acquiring a second term.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Protocol, Sequence
from uuid import UUID, uuid5


sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    read_secure_bytes,
    write_secure_atomic_bytes,
    write_secure_new_bytes,
)
from core.writer_witness_contract import (  # noqa: E402
    PROOF_FIELDS,
    WitnessProofError,
    validate_witness_lease_proof,
    witness_public_key_is_valid,
)
from scripts import production_shadow_startup_normalization_worker as CONTROL  # noqa: E402


REQUEST_SCHEMA = "production-shadow-witness-lease-worker-request-v1"
PLAN_SCHEMA = "production-shadow-witness-lease-worker-plan-v1"
RESULT_SCHEMA = "production-shadow-witness-lease-worker-result-v1"
JOURNAL_SCHEMA = "production-shadow-witness-lease-worker-journal-v1"
ERROR_SCHEMA = "production-shadow-witness-lease-worker-error-v1"
FINAL_SCHEMA = "production-shadow-witness-lease-worker-final-v1"

PHASE = "witness_lease"
OPERATION = "acquire-shadow-writer-witness-lease"
ROLES = ("webapp_fi", "webapp_ir")
ACTIONS = ("acquire", "renew", "readback")
MUTATING_ACTIONS = frozenset({"acquire", "renew"})
WORKER_RELATIVE = Path(
    "scripts/production_shadow_witness_lease_worker.py"
)
BOOTSTRAP_RELATIVE = Path(
    "scripts/bootstrap_three_site_staging_writer_lease.py"
)
STATUS_RELATIVE = Path(
    "scripts/inspect_three_site_staging_writer_witness.py"
)
CLIENT_RELATIVE = Path("core/writer_witness_client.py")
CONTRACT_RELATIVE = Path("core/writer_witness_contract.py")
CONTROL_RELATIVE = Path(
    "scripts/production_shadow_startup_normalization_worker.py"
)

SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ZERO_SHA256 = "0" * 64
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_RELEASE_FILE_BYTES = 16 * 1024 * 1024
MIN_REQUEST_LIFETIME_SECONDS = 30
MAX_REQUEST_LIFETIME_SECONDS = 30 * 60
MAX_FUTURE_SKEW_SECONDS = 5
MIN_LEASE_DURATION_SECONDS = 30
MAX_LEASE_DURATION_SECONDS = 3600
MAX_CLOCK_SKEW_SECONDS = 30

EXPECTED_CONSTRAINTS = {
    "exact_shadow_database_only": True,
    "webapp_fi_epoch_one_only": True,
    "webapp_ir_fenced_non_holder_required": True,
    "single_acquire_request_only": True,
    "exact_request_replay_only": True,
    "same_lease_renewal_only": True,
    "signed_proof_required": True,
    "fresh_authenticated_status_required": True,
    "minimum_remaining_lifetime_required": True,
    "business_write_forbidden": True,
    "api_start_forbidden": True,
    "effect_start_forbidden": True,
    "bot_start_forbidden": True,
    "public_service_start_forbidden": True,
    "redis_start_forbidden": True,
    "legacy_campaign_lease_forbidden": True,
    "current_mutation_forbidden": True,
    "volume_mutation_forbidden": True,
    "object_storage_forbidden": True,
}

POLICY_FIELDS = frozenset(
    {
        "lease_duration_seconds",
        "minimum_remaining_seconds",
        "safety_margin_seconds",
        "max_clock_skew_seconds",
        "witness_reason",
        "local_operator",
    }
)
REQUEST_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "phase",
        "operation",
        "role",
        "action",
        "renewal_sequence",
        "release_sha",
        "release_tree_sha",
        "expected_host",
        "controller_manifest_sha256",
        "controller_plan_sha256",
        "approval_sha256",
        "role_manifest_path",
        "role_manifest_sha256",
        "worker_path",
        "worker_sha256",
        "bootstrap_sha256",
        "status_sha256",
        "client_sha256",
        "contract_sha256",
        "control_protocol_sha256",
        "witness_public_key",
        "witness_public_key_sha256",
        "transition_request_id",
        "status_request_id",
        "issued_at",
        "expires_at",
        "output_root",
        "lease_policy",
        "constraints",
        "request_sha256",
    }
)
WRITER_STATE_FIELDS = frozenset(
    {
        "role",
        "singleton_count",
        "control_state",
        "active_site",
        "writer_epoch",
        "transition_id",
        "witness_lease_id",
        "witness_lease_issued_at",
        "witness_lease_expires_at",
        "witness_proof_hash",
        "witness_transition_id",
        "business_state_sha256",
        "business_row_count",
        "database_identity_sha256",
        "state_sha256",
    }
)
STATUS_FIELDS = frozenset(
    {
        "status",
        "request_id",
        "observer_site",
        "holder_site",
        "writer_epoch",
        "lease_id",
        "lease_status",
        "expires_at",
        "lease_live",
        "witness_receipt_hash",
    }
)
SURFACE_FIELDS = frozenset(
    {
        "database_running",
        "operation_oneoff_count",
        "api_running_count",
        "effect_running_count",
        "bot_running_count",
        "public_service_running_count",
        "redis_running_count",
        "other_running_count",
        "surface_sha256",
    }
)
RESULT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "phase",
        "operation",
        "role",
        "action",
        "renewal_sequence",
        "release_sha",
        "release_tree_sha",
        "expected_host",
        "controller_manifest_sha256",
        "controller_plan_sha256",
        "approval_sha256",
        "role_manifest_sha256",
        "worker_sha256",
        "request_sha256",
        "transition_request_id",
        "status_request_id",
        "lease_policy",
        "captured_at",
        "completed_at",
        "before_state",
        "after_state",
        "before_surface",
        "after_surface",
        "signed_proof",
        "signed_proof_sha256",
        "witness_status",
        "lease_readback_sha256",
        "remaining_lifetime_seconds",
        "witness_signature_verified",
        "singleton_live_lease_count",
        "lease_epoch",
        "business_write_count",
        "app_service_started",
        "current_mutated",
        "volume_mutated",
        "object_storage_used",
        "journal_event_count",
        "journal_tail_sha256",
        "response_sha256",
    }
)
JOURNAL_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "role",
        "action",
        "renewal_sequence",
        "request_sha256",
        "transition_request_id",
        "status_request_id",
        "lease_policy",
        "events",
        "tail_sha256",
        "result_path",
        "result_sha256",
    }
)
EVENT_FIELDS = frozenset(
    {
        "index",
        "kind",
        "checkpoint",
        "request_sha256",
        "semantic_sha256",
        "previous_event_sha256",
        "event_sha256",
    }
)


class WitnessLeaseWorkerError(RuntimeError):
    """The bounded lease operation cannot be proven safe."""


class WitnessLeaseCancellation(WitnessLeaseWorkerError):
    """The controller stopped authorizing a mutation/readback."""


@dataclass(frozen=True)
class WriterState:
    role: str
    singleton_count: int
    control_state: str
    active_site: str | None
    writer_epoch: int
    transition_id: str
    witness_lease_id: str | None
    witness_lease_issued_at: str | None
    witness_lease_expires_at: str | None
    witness_proof_hash: str | None
    witness_transition_id: str | None
    business_state_sha256: str
    business_row_count: int
    database_identity_sha256: str

    def document(self) -> dict[str, Any]:
        document = {
            "role": self.role,
            "singleton_count": self.singleton_count,
            "control_state": self.control_state,
            "active_site": self.active_site,
            "writer_epoch": self.writer_epoch,
            "transition_id": self.transition_id,
            "witness_lease_id": self.witness_lease_id,
            "witness_lease_issued_at": self.witness_lease_issued_at,
            "witness_lease_expires_at": self.witness_lease_expires_at,
            "witness_proof_hash": self.witness_proof_hash,
            "witness_transition_id": self.witness_transition_id,
            "business_state_sha256": self.business_state_sha256,
            "business_row_count": self.business_row_count,
            "database_identity_sha256": self.database_identity_sha256,
        }
        document["state_sha256"] = _sha256(_canonical_json(document))
        return _validate_writer_state(document, role=self.role)


@dataclass(frozen=True)
class RuntimeSurface:
    database_running: bool
    operation_oneoff_count: int
    api_running_count: int
    effect_running_count: int
    bot_running_count: int
    public_service_running_count: int
    redis_running_count: int
    other_running_count: int

    def document(self) -> dict[str, Any]:
        document = {
            "database_running": self.database_running,
            "operation_oneoff_count": self.operation_oneoff_count,
            "api_running_count": self.api_running_count,
            "effect_running_count": self.effect_running_count,
            "bot_running_count": self.bot_running_count,
            "public_service_running_count": (
                self.public_service_running_count
            ),
            "redis_running_count": self.redis_running_count,
            "other_running_count": self.other_running_count,
        }
        document["surface_sha256"] = _sha256(_canonical_json(document))
        return _validate_surface(document)


class LeaseBackend(Protocol):
    """Host implementation; secret material never crosses this interface."""

    def reconcile_authorized_oneoff(
        self,
        *,
        request_id: str,
        authority: Callable[[str], bool],
    ) -> None: ...

    def witness_public_key(self) -> str: ...

    def writer_state(self) -> WriterState: ...

    def runtime_surface(self) -> RuntimeSurface: ...

    def acquire(
        self,
        *,
        campaign_id: str,
        request_id: str,
        release_sha: str,
        duration_seconds: int,
    ) -> Mapping[str, Any]: ...

    def renew(
        self,
        *,
        request_id: str,
        release_sha: str,
        duration_seconds: int,
    ) -> Mapping[str, Any]: ...

    def witness_status(
        self,
        *,
        request_id: str,
        release_sha: str,
    ) -> Mapping[str, Any]: ...


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
        raise WitnessLeaseWorkerError(
            "value is not canonical JSON"
        ) from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WitnessLeaseWorkerError(
                f"duplicate JSON field: {key}"
            )
        result[key] = value
    return result


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise WitnessLeaseWorkerError(
            f"{label} is not a nonzero SHA-256"
        )
    return value


def _canonical_uuid4(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise WitnessLeaseWorkerError(f"{label} is invalid")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise WitnessLeaseWorkerError(f"{label} is invalid") from exc
    if str(parsed) != value or parsed.version != 4:
        raise WitnessLeaseWorkerError(
            f"{label} is not canonical UUID4"
        )
    return value


def _parse_timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise WitnessLeaseWorkerError(
            f"{label} is not canonical UTC"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise WitnessLeaseWorkerError(
            f"{label} is not canonical UTC"
        ) from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timedelta(0)
        or parsed.isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
        != value
    ):
        raise WitnessLeaseWorkerError(
            f"{label} is not canonical UTC"
        )
    return parsed


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _absolute_path(value: Any, *, label: str) -> Path:
    try:
        path = Path(value)
    except TypeError as exc:
        raise WitnessLeaseWorkerError(f"{label} is invalid") from exc
    if not path.is_absolute() or ".." in path.parts:
        raise WitnessLeaseWorkerError(f"{label} is not absolute")
    return path


def _bounded_int(
    value: Any,
    *,
    minimum: int,
    maximum: int,
    label: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise WitnessLeaseWorkerError(f"{label} is outside its bound")
    return value


def deterministic_request_id(
    *,
    operation_id: str,
    action: str,
    renewal_sequence: int,
    purpose: str,
) -> str:
    operation = UUID(_canonical_uuid4(operation_id, label="operation ID"))
    if (
        action not in ACTIONS
        or purpose not in {"transition", "status", "replay-status"}
        or isinstance(renewal_sequence, bool)
        or not 0 <= renewal_sequence <= 1_000_000
    ):
        raise WitnessLeaseWorkerError(
            "deterministic request identity input is invalid"
        )
    return str(
        uuid5(
            operation,
            f"production-shadow:{PHASE}:{action}:"
            f"{renewal_sequence}:{purpose}",
        )
    )


def _request_digest(value: Mapping[str, Any]) -> str:
    unsigned = {
        key: item
        for key, item in value.items()
        if key != "request_sha256"
    }
    return _sha256(_canonical_json(unsigned))


def _validate_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != POLICY_FIELDS:
        raise WitnessLeaseWorkerError(
            "lease policy fields are not exact"
        )
    document = dict(value)
    duration = _bounded_int(
        document["lease_duration_seconds"],
        minimum=MIN_LEASE_DURATION_SECONDS,
        maximum=MAX_LEASE_DURATION_SECONDS,
        label="lease duration",
    )
    minimum_remaining = _bounded_int(
        document["minimum_remaining_seconds"],
        minimum=1,
        maximum=MAX_LEASE_DURATION_SECONDS - 1,
        label="minimum remaining lifetime",
    )
    safety = _bounded_int(
        document["safety_margin_seconds"],
        minimum=1,
        maximum=MAX_LEASE_DURATION_SECONDS - 1,
        label="lease safety margin",
    )
    skew = _bounded_int(
        document["max_clock_skew_seconds"],
        minimum=0,
        maximum=MAX_CLOCK_SKEW_SECONDS,
        label="maximum clock skew",
    )
    if (
        safety <= skew
        or minimum_remaining < safety + skew
        or minimum_remaining >= duration
    ):
        raise WitnessLeaseWorkerError(
            "lease timing policy is unsafe"
        )
    for field in ("witness_reason", "local_operator"):
        item = document[field]
        if (
            not isinstance(item, str)
            or not item
            or item != item.strip()
            or len(item) > 128
        ):
            raise WitnessLeaseWorkerError(
                f"lease policy {field} is invalid"
            )
    return document


def build_request(
    *,
    campaign_id: str,
    operation_id: str,
    role: str,
    action: str,
    renewal_sequence: int,
    release_sha: str,
    release_tree_sha: str,
    expected_host: str,
    controller_manifest_sha256: str,
    controller_plan_sha256: str,
    approval_sha256: str,
    role_manifest_path: Path,
    role_manifest_sha256: str,
    worker_sha256: str,
    bootstrap_sha256: str,
    status_sha256: str,
    client_sha256: str,
    contract_sha256: str,
    control_protocol_sha256: str,
    witness_public_key: str,
    witness_public_key_sha256: str,
    issued_at: datetime,
    expires_at: datetime,
    output_root: Path,
    lease_policy: Mapping[str, Any],
) -> dict[str, Any]:
    release_root = (
        Path("/srv/trading-bot-three-site-production-shadow")
        / operation_id
        / "releases"
        / release_sha
    )
    document: dict[str, Any] = {
        "schema": REQUEST_SCHEMA,
        "status": "authorized-request",
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "phase": PHASE,
        "operation": OPERATION,
        "role": role,
        "action": action,
        "renewal_sequence": renewal_sequence,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "expected_host": expected_host,
        "controller_manifest_sha256": controller_manifest_sha256,
        "controller_plan_sha256": controller_plan_sha256,
        "approval_sha256": approval_sha256,
        "role_manifest_path": os.fspath(role_manifest_path),
        "role_manifest_sha256": role_manifest_sha256,
        "worker_path": os.fspath(release_root / WORKER_RELATIVE),
        "worker_sha256": worker_sha256,
        "bootstrap_sha256": bootstrap_sha256,
        "status_sha256": status_sha256,
        "client_sha256": client_sha256,
        "contract_sha256": contract_sha256,
        "control_protocol_sha256": control_protocol_sha256,
        "witness_public_key": witness_public_key,
        "witness_public_key_sha256": witness_public_key_sha256,
        "transition_request_id": deterministic_request_id(
            operation_id=operation_id,
            action=action,
            renewal_sequence=renewal_sequence,
            purpose="transition",
        ),
        "status_request_id": deterministic_request_id(
            operation_id=operation_id,
            action=action,
            renewal_sequence=renewal_sequence,
            purpose="status",
        ),
        "issued_at": _timestamp(issued_at),
        "expires_at": _timestamp(expires_at),
        "output_root": os.fspath(output_root),
        "lease_policy": dict(lease_policy),
        "constraints": dict(EXPECTED_CONSTRAINTS),
        "request_sha256": ZERO_SHA256,
    }
    document["request_sha256"] = _request_digest(document)
    return validate_request(document, now=issued_at)


def validate_request(
    value: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != REQUEST_FIELDS
        or value.get("schema") != REQUEST_SCHEMA
        or value.get("status") != "authorized-request"
        or value.get("phase") != PHASE
        or value.get("operation") != OPERATION
        or value.get("role") not in ROLES
        or value.get("action") not in ACTIONS
    ):
        raise WitnessLeaseWorkerError(
            "Witness lease request fields are not exact"
        )
    try:
        document = json.loads(
            _canonical_json(dict(value)).decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise WitnessLeaseWorkerError(
            "Witness lease request is not strict JSON"
        ) from exc
    if len(_canonical_json(document)) > MAX_JSON_BYTES:
        raise WitnessLeaseWorkerError(
            "Witness lease request is oversized"
        )
    campaign_id = _canonical_uuid4(
        document["campaign_id"], label="campaign ID"
    )
    operation_id = _canonical_uuid4(
        document["operation_id"], label="operation ID"
    )
    if campaign_id == operation_id:
        raise WitnessLeaseWorkerError(
            "campaign and operation IDs must differ"
        )
    role = document["role"]
    action = document["action"]
    sequence = _bounded_int(
        document["renewal_sequence"],
        minimum=0,
        maximum=1_000_000,
        label="renewal sequence",
    )
    if (
        (action == "acquire" and (role != "webapp_fi" or sequence != 0))
        or (action == "renew" and (role != "webapp_fi" or sequence < 1))
        or (
            action == "readback"
            and (role != "webapp_ir" or sequence != 0)
        )
    ):
        raise WitnessLeaseWorkerError(
            "Witness lease action/role/sequence is invalid"
        )
    if (
        not isinstance(document["release_sha"], str)
        or SHA40_RE.fullmatch(document["release_sha"]) is None
        or not isinstance(document["release_tree_sha"], str)
        or SHA40_RE.fullmatch(document["release_tree_sha"]) is None
        or document["expected_host"] != CONTROL.ROLE_HOSTS[role]
        or document["constraints"] != EXPECTED_CONSTRAINTS
    ):
        raise WitnessLeaseWorkerError(
            "Witness lease release, host, or constraints differ"
        )
    for field in (
        "controller_manifest_sha256",
        "controller_plan_sha256",
        "approval_sha256",
        "role_manifest_sha256",
        "worker_sha256",
        "bootstrap_sha256",
        "status_sha256",
        "client_sha256",
        "contract_sha256",
        "control_protocol_sha256",
        "witness_public_key_sha256",
        "request_sha256",
    ):
        _nonzero_sha256(document[field], label=field)
    public_key = document["witness_public_key"]
    if (
        not isinstance(public_key, str)
        or not witness_public_key_is_valid(public_key)
        or _sha256(public_key.encode("ascii"))
        != document["witness_public_key_sha256"]
    ):
        raise WitnessLeaseWorkerError(
            "Witness public key binding differs"
        )
    issued_at = _parse_timestamp(
        document["issued_at"], label="issued_at"
    )
    expires_at = _parse_timestamp(
        document["expires_at"], label="expires_at"
    )
    observed_now = (
        datetime.now(timezone.utc)
        if now is None
        else now.astimezone(timezone.utc)
    )
    lifetime = (expires_at - issued_at).total_seconds()
    if (
        not MIN_REQUEST_LIFETIME_SECONDS
        <= lifetime
        <= MAX_REQUEST_LIFETIME_SECONDS
        or issued_at
        > observed_now
        + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS)
        or observed_now > expires_at
    ):
        raise WitnessLeaseWorkerError(
            "Witness lease request is stale or outside its time bound"
        )
    release_root = (
        Path("/srv/trading-bot-three-site-production-shadow")
        / operation_id
        / "releases"
        / document["release_sha"]
    )
    if (
        _absolute_path(document["worker_path"], label="worker path")
        != release_root / WORKER_RELATIVE
        or _absolute_path(
            document["role_manifest_path"], label="role manifest"
        ).name
        != "restore-role-manifest.json"
    ):
        raise WitnessLeaseWorkerError(
            "Witness lease installed path binding differs"
        )
    output_root = _absolute_path(
        document["output_root"], label="output root"
    )
    if (
        output_root
        != Path(document["role_manifest_path"]).parent
        / "witness-lease"
    ):
        raise WitnessLeaseWorkerError(
            "Witness lease output root is not role-generation derived"
        )
    if (
        document["transition_request_id"]
        != deterministic_request_id(
            operation_id=operation_id,
            action=action,
            renewal_sequence=sequence,
            purpose="transition",
        )
        or document["status_request_id"]
        != deterministic_request_id(
            operation_id=operation_id,
            action=action,
            renewal_sequence=sequence,
            purpose="status",
        )
    ):
        raise WitnessLeaseWorkerError(
            "Witness lease request IDs are not deterministic"
        )
    policy = _validate_policy(document["lease_policy"])
    expected_reason = (
        f"initial three-site staging migration campaign {campaign_id}"
        if action == "acquire"
        else "automatic active-writer lease renewal"
        if action == "renew"
        else "read-only Witness lease status"
    )
    expected_operator = (
        f"staging-migration:{campaign_id}"
        if action == "acquire"
        else "witness-renewer:webapp_fi"
        if action == "renew"
        else "controller-readback"
    )
    if (
        policy["witness_reason"] != expected_reason
        or policy["local_operator"] != expected_operator
    ):
        raise WitnessLeaseWorkerError(
            "Witness lease reason/operator policy differs"
        )
    if document["request_sha256"] != _request_digest(document):
        raise WitnessLeaseWorkerError(
            "Witness lease request digest differs"
        )
    return document


def confirmation_phrase(request: Mapping[str, Any]) -> str:
    return (
        "production-shadow-witness-lease:"
        f"{request['operation_id']}:{request['role']}:"
        f"{request['action']}:{request['renewal_sequence']}:"
        f"{request['request_sha256']}"
    )


def plan(
    request_value: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    request = validate_request(request_value, now=now)
    return {
        "schema": PLAN_SCHEMA,
        "status": "planned",
        "campaign_id": request["campaign_id"],
        "operation_id": request["operation_id"],
        "phase": PHASE,
        "operation": OPERATION,
        "role": request["role"],
        "action": request["action"],
        "renewal_sequence": request["renewal_sequence"],
        "release_sha": request["release_sha"],
        "request_sha256": request["request_sha256"],
        "transition_request_id": request["transition_request_id"],
        "status_request_id": request["status_request_id"],
        "lease_policy": request["lease_policy"],
        "mutates_production": request["action"] in MUTATING_ACTIONS,
        "business_write_allowed": False,
        "required_confirmation": confirmation_phrase(request),
        "production_contacted": False,
    }


def _validate_writer_state(
    value: Any,
    *,
    role: str,
) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != WRITER_STATE_FIELDS
        or value.get("role") != role
        or role not in ROLES
    ):
        raise WitnessLeaseWorkerError(
            f"{role} writer state fields are not exact"
        )
    document = dict(value)
    if (
        document["singleton_count"] != 1
        or document["control_state"] not in {"active", "fenced"}
        or (
            document["active_site"] is not None
            and document["active_site"] not in ROLES
        )
        or isinstance(document["writer_epoch"], bool)
        or not isinstance(document["writer_epoch"], int)
        or document["writer_epoch"] < 1
        or not isinstance(document["transition_id"], str)
        or not document["transition_id"]
        or isinstance(document["business_row_count"], bool)
        or not isinstance(document["business_row_count"], int)
        or document["business_row_count"] < 0
    ):
        raise WitnessLeaseWorkerError(
            f"{role} writer state is invalid"
        )
    for field in ("business_state_sha256", "database_identity_sha256"):
        _nonzero_sha256(document[field], label=f"{role} {field}")
    optional_hash = document["witness_proof_hash"]
    if optional_hash is not None:
        _nonzero_sha256(
            optional_hash, label=f"{role} witness proof hash"
        )
    optional_identifiers = (
        "witness_lease_id",
        "witness_lease_issued_at",
        "witness_lease_expires_at",
        "witness_proof_hash",
        "witness_transition_id",
    )
    null_count = sum(document[field] is None for field in optional_identifiers)
    if null_count not in {0, len(optional_identifiers)}:
        raise WitnessLeaseWorkerError(
            f"{role} writer lease fields are partial"
        )
    if null_count == 0:
        for field in (
            "witness_lease_id",
            "witness_transition_id",
        ):
            if (
                not isinstance(document[field], str)
                or not document[field]
                or len(document[field]) > 64
            ):
                raise WitnessLeaseWorkerError(
                    f"{role} {field} is invalid"
                )
        _parse_any_timestamp(
            document["witness_lease_issued_at"],
            label=f"{role} local lease issued_at",
        )
        _parse_any_timestamp(
            document["witness_lease_expires_at"],
            label=f"{role} local lease expires_at",
        )
    unsigned = {
        key: item
        for key, item in document.items()
        if key != "state_sha256"
    }
    if (
        _nonzero_sha256(
            document["state_sha256"], label=f"{role} state"
        )
        != _sha256(_canonical_json(unsigned))
    ):
        raise WitnessLeaseWorkerError(
            f"{role} writer state digest differs"
        )
    return document


def _validate_surface(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != SURFACE_FIELDS:
        raise WitnessLeaseWorkerError(
            "runtime surface fields are not exact"
        )
    document = dict(value)
    if type(document["database_running"]) is not bool:
        raise WitnessLeaseWorkerError(
            "database running surface is invalid"
        )
    for field in (
        "operation_oneoff_count",
        "api_running_count",
        "effect_running_count",
        "bot_running_count",
        "public_service_running_count",
        "redis_running_count",
        "other_running_count",
    ):
        _bounded_int(
            document[field],
            minimum=0,
            maximum=100_000,
            label=field,
        )
    unsigned = {
        key: item
        for key, item in document.items()
        if key != "surface_sha256"
    }
    if (
        _nonzero_sha256(
            document["surface_sha256"], label="runtime surface"
        )
        != _sha256(_canonical_json(unsigned))
    ):
        raise WitnessLeaseWorkerError(
            "runtime surface digest differs"
        )
    return document


def _parse_any_timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise WitnessLeaseWorkerError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WitnessLeaseWorkerError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise WitnessLeaseWorkerError(
            f"{label} lacks a timezone"
        )
    return parsed.astimezone(timezone.utc)


def _validate_status(
    value: Any,
    *,
    request: Mapping[str, Any],
    proof: Mapping[str, Any],
    now: datetime,
    expected_request_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != STATUS_FIELDS:
        raise WitnessLeaseWorkerError(
            "Witness status fields are not exact"
        )
    status = dict(value)
    status_request_id = (
        request["status_request_id"]
        if expected_request_id is None
        else expected_request_id
    )
    if (
        status["status"] != "ok"
        or status["request_id"] != status_request_id
        or status["observer_site"] != "webapp_fi"
        or status["holder_site"] != "webapp_fi"
        or status["writer_epoch"] != proof["writer_epoch"]
        or status["lease_id"] != proof["lease_id"]
        or status["lease_status"] != "leased"
        or status["expires_at"] != proof["expires_at"]
        or status["lease_live"] is not True
    ):
        raise WitnessLeaseWorkerError(
            "Witness status does not exactly read back the signed proof"
        )
    _nonzero_sha256(
        status["witness_receipt_hash"],
        label="Witness status receipt",
    )
    expires_at = _parse_any_timestamp(
        status["expires_at"], label="Witness status expires_at"
    )
    if (
        expires_at - now
    ).total_seconds() < request["lease_policy"][
        "minimum_remaining_seconds"
    ]:
        raise WitnessLeaseWorkerError(
            "Witness status lacks the required remaining lifetime"
        )
    return status


def _validate_zero_surface(surface: Mapping[str, Any]) -> None:
    if (
        surface["database_running"] is not True
        or any(
            surface[field] != 0
            for field in (
                "operation_oneoff_count",
                "api_running_count",
                "effect_running_count",
                "bot_running_count",
                "public_service_running_count",
                "redis_running_count",
                "other_running_count",
            )
        )
    ):
        raise WitnessLeaseWorkerError(
            "runtime surface is not database-only and quiescent"
        )


def _require_fi_pre_state(
    state: Mapping[str, Any],
    *,
    action: str,
    journal_started: bool,
) -> None:
    if (
        state["control_state"] != "active"
        or state["active_site"] != "webapp_fi"
        or state["writer_epoch"] != 1
    ):
        raise WitnessLeaseWorkerError(
            "WebApp-FI is not the exact active epoch-1 shadow database"
        )
    has_lease = state["witness_lease_id"] is not None
    if action == "renew" and not has_lease:
        raise WitnessLeaseWorkerError(
            "WebApp-FI has no lease to renew"
        )
    if action == "acquire" and has_lease and not journal_started:
        raise WitnessLeaseWorkerError(
            "WebApp-FI already has an unbound or foreign lease"
        )


def _require_ir_state(state: Mapping[str, Any]) -> None:
    if (
        state["control_state"] != "fenced"
        or state["active_site"] is not None
        or state["writer_epoch"] != 1
        or any(
            state[field] is not None
            for field in (
                "witness_lease_id",
                "witness_lease_issued_at",
                "witness_lease_expires_at",
                "witness_proof_hash",
                "witness_transition_id",
            )
        )
    ):
        raise WitnessLeaseWorkerError(
            "WebApp-IR is not the exact fenced epoch-1 non-holder"
        )


def _require_fi_state_matches_proof(
    state: Mapping[str, Any],
    proof: Mapping[str, Any],
) -> None:
    proof_hash = _proof_hash(proof)
    if (
        state["control_state"] != "active"
        or state["active_site"] != "webapp_fi"
        or state["writer_epoch"] != 1
        or state["witness_lease_id"] != proof["lease_id"]
        or state["witness_lease_issued_at"] != proof["issued_at"]
        or state["witness_lease_expires_at"] != proof["expires_at"]
        or state["witness_proof_hash"] != proof_hash
        or state["witness_transition_id"]
        != proof["witness_transition_id"]
    ):
        raise WitnessLeaseWorkerError(
            "WebApp-FI local import differs from signed proof"
        )


def _replay_status_request_id(request: Mapping[str, Any]) -> str:
    return deterministic_request_id(
        operation_id=request["operation_id"],
        action=request["action"],
        renewal_sequence=request["renewal_sequence"],
        purpose="replay-status",
    )


def _fresh_replay_time(
    request: Mapping[str, Any],
    *,
    clock: Callable[[], datetime],
) -> datetime:
    observed = clock()
    if (
        not isinstance(observed, datetime)
        or observed.tzinfo is None
        or observed.utcoffset() is None
    ):
        raise WitnessLeaseWorkerError(
            "fresh replay clock is not timezone-aware"
        )
    observed = observed.astimezone(timezone.utc)
    # A completed result cannot outlive its live authorization window.
    validate_request(request, now=observed)
    return observed


def _require_replay_snapshot(
    state: Mapping[str, Any],
    surface: Mapping[str, Any],
    *,
    result: Mapping[str, Any],
) -> None:
    if (
        state != result["after_state"]
        or surface != result["after_surface"]
    ):
        raise WitnessLeaseWorkerError(
            "fresh replay local readback differs from the verified result"
        )


def _fresh_replay_readback(
    request: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    backend: LeaseBackend,
    authority: Callable[[str], bool],
    clock: Callable[[], datetime],
) -> None:
    """Require live local/Witness readback before reusing a result receipt."""

    pre_reconciliation_now = _fresh_replay_time(request, clock=clock)
    if request["role"] == "webapp_fi":
        public_key = backend.witness_public_key()
        if (
            not witness_public_key_is_valid(public_key)
            or public_key != request["witness_public_key"]
            or _sha256(public_key.encode("ascii"))
            != request["witness_public_key_sha256"]
        ):
            raise WitnessLeaseWorkerError(
                "Witness public key binding differs"
            )
        proof = _validate_proof(
            result["signed_proof"],
            request=request,
            public_key=public_key,
            now=pre_reconciliation_now,
        )
    _authority(authority, "before-replay-stale-oneoff-reconciliation")
    backend.reconcile_authorized_oneoff(
        request_id=request["transition_request_id"],
        authority=authority,
    )
    _authority(authority, "before-replay-initial-local-readback")
    initial_now = _fresh_replay_time(request, clock=clock)
    initial_state = backend.writer_state().document()
    initial_surface = backend.runtime_surface().document()
    _validate_zero_surface(initial_surface)

    if request["role"] == "webapp_ir":
        _require_ir_state(initial_state)
        _require_replay_snapshot(
            initial_state,
            initial_surface,
            result=result,
        )
    else:
        proof = _validate_proof(
            proof,
            request=request,
            public_key=public_key,
            now=initial_now,
        )
        _require_fi_state_matches_proof(initial_state, proof)
        _require_replay_snapshot(
            initial_state,
            initial_surface,
            result=result,
        )
        _authority(authority, "before-replay-fresh-witness-status")
        replay_status_id = _replay_status_request_id(request)
        status = backend.witness_status(
            request_id=replay_status_id,
            release_sha=request["release_sha"],
        )
        status_now = _fresh_replay_time(request, clock=clock)
        proof = _validate_proof(
            proof,
            request=request,
            public_key=public_key,
            now=status_now,
        )
        _validate_status(
            status,
            request=request,
            proof=proof,
            now=status_now,
            expected_request_id=replay_status_id,
        )

    _authority(authority, "before-replay-final-local-readback")
    final_now = _fresh_replay_time(request, clock=clock)
    final_state = backend.writer_state().document()
    final_surface = backend.runtime_surface().document()
    _validate_zero_surface(final_surface)
    if request["role"] == "webapp_ir":
        _require_ir_state(final_state)
    else:
        _require_fi_state_matches_proof(final_state, proof)
        _validate_proof(
            proof,
            request=request,
            public_key=public_key,
            now=final_now,
        )
    _require_replay_snapshot(
        final_state,
        final_surface,
        result=result,
    )
    _authority(authority, "before-replay-result-return")
    return_now = _fresh_replay_time(request, clock=clock)
    if request["role"] == "webapp_fi":
        _validate_proof(
            proof,
            request=request,
            public_key=public_key,
            now=return_now,
        )


def _authority(
    authority: Callable[[str], bool],
    checkpoint: str,
) -> None:
    try:
        permitted = authority(checkpoint)
    except BaseException as exc:
        raise WitnessLeaseCancellation(
            f"controller authority failed at {checkpoint}"
        ) from exc
    if permitted is not True:
        raise WitnessLeaseCancellation(
            f"controller authority denied at {checkpoint}"
        )


def _ensure_private_directory(path: Path) -> None:
    if not path.exists():
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=False)
        except FileExistsError:
            pass
        except OSError as exc:
            raise WitnessLeaseWorkerError(
                "lease output directory cannot be created"
            ) from exc
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise WitnessLeaseWorkerError(
            "lease output directory is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise WitnessLeaseWorkerError(
            "lease output directory is not owner-only"
        )


def _read_json_file(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = read_secure_bytes(
            path,
            label=label,
            owner_uid=os.geteuid(),
            max_size=MAX_JSON_BYTES,
        )
        value = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except (
        SecureFileError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise WitnessLeaseWorkerError(
            f"{label} is not secure strict JSON"
        ) from exc
    if not isinstance(value, dict):
        raise WitnessLeaseWorkerError(f"{label} is not an object")
    return value


def _event(
    *,
    journal: Mapping[str, Any],
    kind: str,
    checkpoint: str,
    semantic: Mapping[str, Any],
) -> dict[str, Any]:
    previous = (
        journal["events"][-1]["event_sha256"]
        if journal["events"]
        else ZERO_SHA256
    )
    document = {
        "index": len(journal["events"]) + 1,
        "kind": kind,
        "checkpoint": checkpoint,
        "request_sha256": journal["request_sha256"],
        "semantic_sha256": _sha256(_canonical_json(semantic)),
        "previous_event_sha256": previous,
        "event_sha256": ZERO_SHA256,
    }
    document["event_sha256"] = _sha256(
        _canonical_json(
            {
                key: item
                for key, item in document.items()
                if key != "event_sha256"
            }
        )
    )
    return document


def _validate_journal(
    value: Any,
    *,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != JOURNAL_FIELDS
        or value.get("schema") != JOURNAL_SCHEMA
        or value.get("status") not in {"started", "completed"}
    ):
        raise WitnessLeaseWorkerError(
            "lease worker journal fields are not exact"
        )
    journal = dict(value)
    expected = {
        "campaign_id": request["campaign_id"],
        "operation_id": request["operation_id"],
        "release_sha": request["release_sha"],
        "role": request["role"],
        "action": request["action"],
        "renewal_sequence": request["renewal_sequence"],
        "request_sha256": request["request_sha256"],
        "transition_request_id": request["transition_request_id"],
        "status_request_id": request["status_request_id"],
        "lease_policy": request["lease_policy"],
    }
    if any(journal[field] != item for field, item in expected.items()):
        raise WitnessLeaseWorkerError(
            "lease worker journal binding differs"
        )
    events = journal["events"]
    if not isinstance(events, list) or not 1 <= len(events) <= 32:
        raise WitnessLeaseWorkerError(
            "lease worker journal event count is invalid"
        )
    previous = ZERO_SHA256
    for index, event in enumerate(events, start=1):
        if (
            not isinstance(event, dict)
            or set(event) != EVENT_FIELDS
            or event["index"] != index
            or event["request_sha256"] != request["request_sha256"]
            or event["previous_event_sha256"] != previous
        ):
            raise WitnessLeaseWorkerError(
                "lease worker journal event chain differs"
            )
        _nonzero_sha256(
            event["semantic_sha256"], label="journal semantic"
        )
        unsigned = {
            key: item
            for key, item in event.items()
            if key != "event_sha256"
        }
        if event["event_sha256"] != _sha256(
            _canonical_json(unsigned)
        ):
            raise WitnessLeaseWorkerError(
                "lease worker journal event digest differs"
            )
        previous = event["event_sha256"]
    if journal["tail_sha256"] != previous:
        raise WitnessLeaseWorkerError(
            "lease worker journal tail differs"
        )
    if journal["status"] == "completed":
        if not isinstance(journal["result_path"], str):
            raise WitnessLeaseWorkerError(
                "completed journal lacks result path"
            )
        _nonzero_sha256(
            journal["result_sha256"], label="journal result"
        )
    elif (
        journal["result_path"] is not None
        or journal["result_sha256"] is not None
    ):
        raise WitnessLeaseWorkerError(
            "started journal contains a result"
        )
    return journal


def _journal_paths(
    request: Mapping[str, Any],
) -> tuple[Path, Path]:
    root = Path(request["output_root"])
    suffix = (
        f"{request['action']}-{request['renewal_sequence']}-"
        f"{request['request_sha256']}"
    )
    return (
        root / f"journal-{suffix}.json",
        root / f"result-{suffix}.json",
    )


def _write_new_or_same(
    path: Path,
    payload: bytes,
    *,
    label: str,
) -> None:
    try:
        write_secure_new_bytes(
            path,
            payload,
            label=label,
            mode=0o600,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError:
        try:
            existing = read_secure_bytes(
                path,
                label=f"existing {label}",
                owner_uid=os.geteuid(),
                max_size=MAX_JSON_BYTES,
            )
        except SecureFileError as exc:
            raise WitnessLeaseWorkerError(
                f"{label} cannot be read back"
            ) from exc
        if existing != payload:
            raise WitnessLeaseWorkerError(
                f"existing {label} differs"
            )


def _persist_journal(
    path: Path,
    journal: Mapping[str, Any],
    *,
    create_only: bool = False,
) -> None:
    payload = _canonical_json(journal) + b"\n"
    try:
        if create_only:
            write_secure_new_bytes(
                path,
                payload,
                label="lease worker journal",
                mode=0o600,
                max_size=MAX_JSON_BYTES,
            )
        else:
            write_secure_atomic_bytes(
                path,
                payload,
                label="lease worker journal",
                mode=0o600,
                max_size=MAX_JSON_BYTES,
            )
    except SecureFileError as exc:
        raise WitnessLeaseWorkerError(
            "lease worker journal cannot be persisted"
        ) from exc
    observed = _read_json_file(path, label="lease worker journal")
    if observed != journal:
        raise WitnessLeaseWorkerError(
            "lease worker journal readback differs"
        )


def _load_or_start_journal(
    request: Mapping[str, Any],
) -> tuple[dict[str, Any], Path, Path, bool]:
    root = Path(request["output_root"])
    _ensure_private_directory(root)
    journal_path, result_path = _journal_paths(request)
    existed = journal_path.exists() or journal_path.is_symlink()
    if existed:
        journal = _validate_journal(
            _read_json_file(
                journal_path, label="lease worker journal"
            ),
            request=request,
        )
        return journal, journal_path, result_path, True
    journal: dict[str, Any] = {
        "schema": JOURNAL_SCHEMA,
        "status": "started",
        "campaign_id": request["campaign_id"],
        "operation_id": request["operation_id"],
        "release_sha": request["release_sha"],
        "role": request["role"],
        "action": request["action"],
        "renewal_sequence": request["renewal_sequence"],
        "request_sha256": request["request_sha256"],
        "transition_request_id": request[
            "transition_request_id"
        ],
        "status_request_id": request["status_request_id"],
        "lease_policy": request["lease_policy"],
        "events": [],
        "tail_sha256": ZERO_SHA256,
        "result_path": None,
        "result_sha256": None,
    }
    first = _event(
        journal=journal,
        kind="intent-persisted",
        checkpoint="before-host-observation",
        semantic={
            "action": request["action"],
            "transition_request_id": request[
                "transition_request_id"
            ],
            "status_request_id": request["status_request_id"],
            "lease_policy": request["lease_policy"],
        },
    )
    journal["events"].append(first)
    journal["tail_sha256"] = first["event_sha256"]
    _persist_journal(journal_path, journal, create_only=True)
    return journal, journal_path, result_path, False


def _append_journal_event(
    journal: dict[str, Any],
    path: Path,
    *,
    expected_index: int,
    kind: str,
    checkpoint: str,
    semantic: Mapping[str, Any],
) -> None:
    if (
        isinstance(expected_index, bool)
        or not isinstance(expected_index, int)
        or expected_index < 1
    ):
        raise WitnessLeaseWorkerError(
            "lease journal event position is invalid"
        )
    if len(journal["events"]) > expected_index:
        existing = journal["events"][expected_index]
        if (
            existing["index"] != expected_index + 1
            or existing["kind"] != kind
            or existing["checkpoint"] != checkpoint
            or existing["semantic_sha256"]
            != _sha256(_canonical_json(semantic))
        ):
            raise WitnessLeaseWorkerError(
                "existing lease journal event differs"
            )
        return
    if len(journal["events"]) != expected_index:
        raise WitnessLeaseWorkerError(
            "lease journal event order differs"
        )
    row = _event(
        journal=journal,
        kind=kind,
        checkpoint=checkpoint,
        semantic=semantic,
    )
    journal["events"].append(row)
    journal["tail_sha256"] = row["event_sha256"]
    _persist_journal(path, journal)


def _validated_completed_result(
    journal: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    result_path: Path,
) -> dict[str, Any]:
    if (
        journal["status"] != "completed"
        or journal["result_path"] != os.fspath(result_path)
    ):
        raise WitnessLeaseWorkerError(
            "completed lease journal result path differs"
        )
    try:
        payload = read_secure_bytes(
            result_path,
            label="lease worker result",
            owner_uid=os.geteuid(),
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise WitnessLeaseWorkerError(
            "completed lease result is unavailable"
        ) from exc
    if _sha256(payload) != journal["result_sha256"]:
        raise WitnessLeaseWorkerError(
            "completed lease result digest differs"
        )
    try:
        result = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise WitnessLeaseWorkerError(
            "completed lease result is invalid"
        ) from exc
    validated = validate_result(result, request=request)
    _validate_result_journal_binding(
        journal,
        request=request,
        result=validated,
    )
    return validated


def _validate_result_journal_binding(
    journal: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    result: Mapping[str, Any],
) -> None:
    """Rebuild every journal semantic from the validated public result."""

    intent = {
        "action": request["action"],
        "transition_request_id": request["transition_request_id"],
        "status_request_id": request["status_request_id"],
        "lease_policy": request["lease_policy"],
    }
    expected = [
        (
            "intent-persisted",
            "before-host-observation",
            intent,
        )
    ]
    if request["role"] == "webapp_fi":
        proof = result["signed_proof"]
        status = result["witness_status"]
        transition = {
            "holder_site": proof["holder_site"],
            "writer_epoch": proof["writer_epoch"],
            "lease_id": proof["lease_id"],
            "witness_transition_id": proof["witness_transition_id"],
            "proof_hash": _proof_hash(proof),
            "issued_at": proof["issued_at"],
            "expires_at": proof["expires_at"],
        }
        closure = {
            "role": request["role"],
            "signed_proof_sha256": result["signed_proof_sha256"],
            "witness_status_receipt_sha256": status[
                "witness_receipt_hash"
            ],
            "local_writer_state_sha256": result["after_state"][
                "state_sha256"
            ],
            "lease_id": proof["lease_id"],
            "writer_epoch": proof["writer_epoch"],
            "expires_at": proof["expires_at"],
        }
        expected.append(
            (
                "transition-readback",
                f"after-{request['action']}-transition",
                transition,
            )
        )
    else:
        closure = {
            "role": request["role"],
            "writer_state_sha256": result["after_state"][
                "state_sha256"
            ],
            "surface_sha256": result["after_surface"][
                "surface_sha256"
            ],
            "fenced_non_holder": True,
        }
    expected.append(
        (
            "closure-readback",
            "before-result-publication",
            closure,
        )
    )
    events = journal["events"]
    if (
        len(events) != len(expected)
        or result["journal_event_count"] != len(expected)
        or result["journal_tail_sha256"] != journal["tail_sha256"]
        or result["lease_readback_sha256"]
        != _sha256(_canonical_json(closure))
    ):
        raise WitnessLeaseWorkerError(
            "lease result journal closure differs"
        )
    for event, (kind, checkpoint, semantic) in zip(
        events,
        expected,
        strict=True,
    ):
        if (
            event["kind"] != kind
            or event["checkpoint"] != checkpoint
            or event["semantic_sha256"]
            != _sha256(_canonical_json(semantic))
        ):
            raise WitnessLeaseWorkerError(
                "lease result journal semantics differ"
            )


def _recover_published_result(
    journal: dict[str, Any],
    *,
    request: Mapping[str, Any],
    journal_path: Path,
    result_path: Path,
    authority: Callable[[str], bool],
    backend: LeaseBackend | None,
    clock: Callable[[], datetime],
) -> dict[str, Any] | None:
    """Finish only the local journal after a lost post-publication response."""

    if not (result_path.exists() or result_path.is_symlink()):
        return None
    if journal["status"] != "started":
        raise WitnessLeaseWorkerError(
            "published result has an incompatible journal state"
        )
    try:
        payload = read_secure_bytes(
            result_path,
            label="recoverable lease worker result",
            owner_uid=os.geteuid(),
            max_size=MAX_JSON_BYTES,
        )
        value = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except (
        SecureFileError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise WitnessLeaseWorkerError(
            "recoverable lease result is invalid"
        ) from exc
    if not isinstance(value, dict):
        raise WitnessLeaseWorkerError(
            "recoverable lease result is not an object"
        )
    result = validate_result(value, request=request)
    _validate_result_journal_binding(
        journal,
        request=request,
        result=result,
    )
    active_backend = (
        ExactReleaseBackend(request)
        if backend is None
        else backend
    )
    _fresh_replay_readback(
        request,
        result,
        backend=active_backend,
        authority=authority,
        clock=clock,
    )
    _authority(authority, "before-recovered-journal-completion")
    journal["status"] = "completed"
    journal["result_path"] = os.fspath(result_path)
    journal["result_sha256"] = _sha256(payload)
    _persist_journal(journal_path, journal)
    return result


def _validate_proof(
    value: Any,
    *,
    request: Mapping[str, Any],
    public_key: str,
    now: datetime,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != PROOF_FIELDS:
        raise WitnessLeaseWorkerError(
            "signed Witness proof fields are not exact"
        )
    try:
        proof = validate_witness_lease_proof(
            value,
            public_key_base64=public_key,
            expected_site="webapp_fi",
            expected_epoch=1,
            now=now,
            safety_margin_seconds=request["lease_policy"][
                "safety_margin_seconds"
            ],
            max_clock_skew_seconds=request["lease_policy"][
                "max_clock_skew_seconds"
            ],
            max_lifetime_seconds=request["lease_policy"][
                "lease_duration_seconds"
            ],
        )
    except WitnessProofError as exc:
        raise WitnessLeaseWorkerError(
            "signed Witness proof is invalid"
        ) from exc
    remaining = (proof.expires_at - now).total_seconds()
    if (
        remaining
        < request["lease_policy"]["minimum_remaining_seconds"]
    ):
        raise WitnessLeaseWorkerError(
            "signed Witness proof lacks required remaining lifetime"
        )
    return proof.canonical_payload


def _proof_hash(value: Mapping[str, Any]) -> str:
    return _sha256(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def _transition_proof(
    response: Mapping[str, Any],
) -> Mapping[str, Any]:
    if set(response) != {"proof"} or not isinstance(
        response["proof"], Mapping
    ):
        raise WitnessLeaseWorkerError(
            "lease transition response fields are not exact"
        )
    return response["proof"]


def execute(
    request_value: Mapping[str, Any],
    *,
    apply: bool = False,
    confirm: str | None = None,
    authority: Callable[[str], bool] | None = None,
    backend: LeaseBackend | None = None,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    observed_now = (
        datetime.now(timezone.utc)
        if now is None
        else now.astimezone(timezone.utc)
    )
    request = validate_request(request_value, now=observed_now)
    if not apply:
        return plan(request, now=observed_now)
    if (
        confirm != confirmation_phrase(request)
        or authority is None
        or not callable(authority)
    ):
        raise WitnessLeaseWorkerError(
            "apply requires exact confirmation and live authority"
        )
    current_time = (
        (lambda: datetime.now(timezone.utc))
        if clock is None
        else clock
    )
    journal, journal_path, result_path, journal_existed = (
        _load_or_start_journal(request)
    )
    if journal["status"] == "completed":
        completed = _validated_completed_result(
            journal,
            request=request,
            result_path=result_path,
        )
        active_backend = (
            ExactReleaseBackend(request)
            if backend is None
            else backend
        )
        _fresh_replay_readback(
            request,
            completed,
            backend=active_backend,
            authority=authority,
            clock=current_time,
        )
        return completed
    recovered = _recover_published_result(
        journal,
        request=request,
        journal_path=journal_path,
        result_path=result_path,
        authority=authority,
        backend=backend,
        clock=current_time,
    )
    if recovered is not None:
        return recovered
    active_backend = (
        ExactReleaseBackend(request)
        if backend is None
        else backend
    )

    _authority(authority, "before-stale-oneoff-reconciliation")
    active_backend.reconcile_authorized_oneoff(
        request_id=request["transition_request_id"],
        authority=authority,
    )
    _authority(authority, "before-initial-readback")
    captured_at = current_time().astimezone(timezone.utc)
    before = active_backend.writer_state().document()
    before_surface = active_backend.runtime_surface().document()
    _validate_zero_surface(before_surface)
    role = request["role"]
    action = request["action"]
    if role == "webapp_ir":
        _require_ir_state(before)
        proof_document = None
        status = None
        after = before
        after_surface = before_surface
    else:
        _require_fi_pre_state(
            before,
            action=action,
            journal_started=journal_existed,
        )
        public_key = active_backend.witness_public_key()
        if (
            not witness_public_key_is_valid(public_key)
            or public_key != request["witness_public_key"]
            or _sha256(public_key.encode("ascii"))
            != request["witness_public_key_sha256"]
        ):
            raise WitnessLeaseWorkerError(
                "Witness public key binding differs"
            )
        _authority(authority, f"before-{action}-transition")
        if action == "acquire":
            transition = active_backend.acquire(
                campaign_id=request["campaign_id"],
                request_id=request["transition_request_id"],
                release_sha=request["release_sha"],
                duration_seconds=request["lease_policy"][
                    "lease_duration_seconds"
                ],
            )
        elif action == "renew":
            transition = active_backend.renew(
                request_id=request["transition_request_id"],
                release_sha=request["release_sha"],
                duration_seconds=request["lease_policy"][
                    "lease_duration_seconds"
                ],
            )
        else:
            raise WitnessLeaseWorkerError(
                "WebApp-FI readback-only action is unsupported"
            )
        proof_now = current_time().astimezone(timezone.utc)
        proof_document = _validate_proof(
            dict(_transition_proof(transition)),
            request=request,
            public_key=public_key,
            now=proof_now,
        )
        _append_journal_event(
            journal,
            journal_path,
            expected_index=1,
            kind="transition-readback",
            checkpoint=f"after-{action}-transition",
            semantic={
                "holder_site": proof_document["holder_site"],
                "writer_epoch": proof_document["writer_epoch"],
                "lease_id": proof_document["lease_id"],
                "witness_transition_id": proof_document[
                    "witness_transition_id"
                ],
                "proof_hash": _proof_hash(proof_document),
                "issued_at": proof_document["issued_at"],
                "expires_at": proof_document["expires_at"],
            },
        )
        _authority(authority, "before-fresh-witness-status")
        status_now = current_time().astimezone(timezone.utc)
        status = _validate_status(
            active_backend.witness_status(
                request_id=request["status_request_id"],
                release_sha=request["release_sha"],
            ),
            request=request,
            proof=proof_document,
            now=status_now,
        )
        _authority(authority, "before-final-local-readback")
        after = active_backend.writer_state().document()
        after_surface = active_backend.runtime_surface().document()
        _validate_zero_surface(after_surface)
        _require_fi_state_matches_proof(after, proof_document)
        if action == "renew":
            if (
                before["witness_lease_id"]
                != after["witness_lease_id"]
                or before["writer_epoch"] != after["writer_epoch"]
                or before["active_site"] != after["active_site"]
                or (
                    _parse_any_timestamp(
                        after["witness_lease_issued_at"],
                        label="renewed local issued_at",
                    )
                    <= _parse_any_timestamp(
                        before["witness_lease_issued_at"],
                        label="prior local issued_at",
                    )
                )
                or (
                    _parse_any_timestamp(
                        after["witness_lease_expires_at"],
                        label="renewed local expires_at",
                    )
                    <= _parse_any_timestamp(
                        before["witness_lease_expires_at"],
                        label="prior local expires_at",
                    )
                )
                or after["witness_transition_id"]
                == before["witness_transition_id"]
            ):
                raise WitnessLeaseWorkerError(
                    "renewal did not advance the same lease proof"
                )
        if (
            before["business_state_sha256"]
            != after["business_state_sha256"]
            or before["business_row_count"]
            != after["business_row_count"]
            or before["database_identity_sha256"]
            != after["database_identity_sha256"]
        ):
            raise WitnessLeaseWorkerError(
                "lease operation changed business database state"
            )

    completed_at = current_time().astimezone(timezone.utc)
    if (
        completed_at < captured_at
        or completed_at
        > _parse_timestamp(
            request["expires_at"], label="request expires_at"
        )
    ):
        raise WitnessLeaseWorkerError(
            "lease operation completed outside its authorization"
        )
    if role == "webapp_ir":
        lease_readback = {
            "role": role,
            "writer_state_sha256": after["state_sha256"],
            "surface_sha256": after_surface["surface_sha256"],
            "fenced_non_holder": True,
        }
        signed_proof_sha256 = None
        remaining = None
        signature_verified = False
        live_count = 0
        epoch = after["writer_epoch"]
    else:
        assert proof_document is not None
        assert status is not None
        signed_proof_sha256 = _proof_hash(proof_document)
        remaining = int(
            (
                _parse_any_timestamp(
                    proof_document["expires_at"],
                    label="proof expiry",
                )
                - completed_at
            ).total_seconds()
        )
        if (
            remaining
            < request["lease_policy"]["minimum_remaining_seconds"]
        ):
            raise WitnessLeaseWorkerError(
                "final readback lacks the required lifetime margin"
            )
        lease_readback = {
            "role": role,
            "signed_proof_sha256": signed_proof_sha256,
            "witness_status_receipt_sha256": status[
                "witness_receipt_hash"
            ],
            "local_writer_state_sha256": after["state_sha256"],
            "lease_id": proof_document["lease_id"],
            "writer_epoch": proof_document["writer_epoch"],
            "expires_at": proof_document["expires_at"],
        }
        signature_verified = True
        live_count = 1
        epoch = proof_document["writer_epoch"]
    _append_journal_event(
        journal,
        journal_path,
        expected_index=(1 if role == "webapp_ir" else 2),
        kind="closure-readback",
        checkpoint="before-result-publication",
        semantic=lease_readback,
    )
    result: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "status": "verified",
        **{
            field: request[field]
            for field in (
                "campaign_id",
                "operation_id",
                "phase",
                "operation",
                "role",
                "action",
                "renewal_sequence",
                "release_sha",
                "release_tree_sha",
                "expected_host",
                "controller_manifest_sha256",
                "controller_plan_sha256",
                "approval_sha256",
                "role_manifest_sha256",
                "worker_sha256",
                "request_sha256",
                "transition_request_id",
                "status_request_id",
                "lease_policy",
            )
        },
        "captured_at": _timestamp(captured_at),
        "completed_at": _timestamp(completed_at),
        "before_state": before,
        "after_state": after,
        "before_surface": before_surface,
        "after_surface": after_surface,
        "signed_proof": proof_document,
        "signed_proof_sha256": signed_proof_sha256,
        "witness_status": status,
        "lease_readback_sha256": _sha256(
            _canonical_json(lease_readback)
        ),
        "remaining_lifetime_seconds": remaining,
        "witness_signature_verified": signature_verified,
        "singleton_live_lease_count": live_count,
        "lease_epoch": epoch,
        "business_write_count": 0,
        "app_service_started": False,
        "current_mutated": False,
        "volume_mutated": False,
        "object_storage_used": False,
        "journal_event_count": len(journal["events"]),
        "journal_tail_sha256": journal["tail_sha256"],
        "response_sha256": ZERO_SHA256,
    }
    result["response_sha256"] = _sha256(
        _canonical_json(
            {
                key: item
                for key, item in result.items()
                if key != "response_sha256"
            }
        )
    )
    validated = validate_result(
        result,
        request=request,
        now=completed_at,
    )
    _validate_result_journal_binding(
        journal,
        request=request,
        result=validated,
    )
    payload = _canonical_json(validated) + b"\n"
    _write_new_or_same(
        result_path,
        payload,
        label="lease worker result",
    )
    _authority(authority, "before-journal-completion")
    journal["status"] = "completed"
    journal["result_path"] = os.fspath(result_path)
    journal["result_sha256"] = _sha256(payload)
    _persist_journal(journal_path, journal)
    return validated


def validate_result(
    value: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    bound = validate_request(
        request,
        now=_parse_timestamp(
            request["issued_at"], label="request issued_at"
        ),
    )
    if not isinstance(value, Mapping) or set(value) != RESULT_FIELDS:
        raise WitnessLeaseWorkerError(
            "lease worker result fields are not exact"
        )
    document = json.loads(_canonical_json(dict(value)).decode("ascii"))
    identity_fields = (
        "campaign_id",
        "operation_id",
        "phase",
        "operation",
        "role",
        "action",
        "renewal_sequence",
        "release_sha",
        "release_tree_sha",
        "expected_host",
        "controller_manifest_sha256",
        "controller_plan_sha256",
        "approval_sha256",
        "role_manifest_sha256",
        "worker_sha256",
        "request_sha256",
        "transition_request_id",
        "status_request_id",
        "lease_policy",
    )
    if (
        document["schema"] != RESULT_SCHEMA
        or document["status"] != "verified"
        or any(document[field] != bound[field] for field in identity_fields)
    ):
        raise WitnessLeaseWorkerError(
            "lease worker result identity differs"
        )
    before = _validate_writer_state(
        document["before_state"], role=bound["role"]
    )
    after = _validate_writer_state(
        document["after_state"], role=bound["role"]
    )
    before_surface = _validate_surface(document["before_surface"])
    after_surface = _validate_surface(document["after_surface"])
    _validate_zero_surface(before_surface)
    _validate_zero_surface(after_surface)
    captured_at = _parse_timestamp(
        document["captured_at"], label="captured_at"
    )
    completed_at = _parse_timestamp(
        document["completed_at"], label="completed_at"
    )
    observed_now = (
        completed_at
        if now is None
        else now.astimezone(timezone.utc)
    )
    if (
        captured_at
        < _parse_timestamp(bound["issued_at"], label="issued_at")
        or completed_at < captured_at
        or completed_at
        > _parse_timestamp(bound["expires_at"], label="expires_at")
        or completed_at
        > observed_now + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS)
    ):
        raise WitnessLeaseWorkerError(
            "lease worker result chronology differs"
        )
    _nonzero_sha256(
        document["lease_readback_sha256"],
        label="lease readback",
    )
    if (
        document["business_write_count"] != 0
        or document["app_service_started"] is not False
        or document["current_mutated"] is not False
        or document["volume_mutated"] is not False
        or document["object_storage_used"] is not False
        or isinstance(document["journal_event_count"], bool)
        or not 2 <= document["journal_event_count"] <= 32
    ):
        raise WitnessLeaseWorkerError(
            "lease worker safety closure differs"
        )
    _nonzero_sha256(
        document["journal_tail_sha256"], label="journal tail"
    )
    if bound["role"] == "webapp_ir":
        _require_ir_state(before)
        _require_ir_state(after)
        if (
            before != after
            or document["signed_proof"] is not None
            or document["signed_proof_sha256"] is not None
            or document["witness_status"] is not None
            or document["remaining_lifetime_seconds"] is not None
            or document["witness_signature_verified"] is not False
            or document["singleton_live_lease_count"] != 0
            or document["lease_epoch"] != 1
        ):
            raise WitnessLeaseWorkerError(
                "WebApp-IR result is not read-only fenced evidence"
            )
    else:
        proof = document["signed_proof"]
        if not isinstance(proof, dict) or set(proof) != PROOF_FIELDS:
            raise WitnessLeaseWorkerError(
                "WebApp-FI signed proof is unavailable"
            )
        verified_proof = _validate_proof(
            proof,
            request=bound,
            public_key=bound["witness_public_key"],
            # A result is a historical completion receipt.  execute() must
            # revalidate liveness before it reuses this receipt; the phase
            # bridge also binds a fresh cross-role observation.
            now=completed_at,
        )
        proof_sha256 = _proof_hash(proof)
        if (
            verified_proof != proof
            or document["signed_proof_sha256"] != proof_sha256
            or document["witness_signature_verified"] is not True
            or document["singleton_live_lease_count"] != 1
            or document["lease_epoch"] != 1
            or isinstance(document["remaining_lifetime_seconds"], bool)
            or not isinstance(
                document["remaining_lifetime_seconds"], int
            )
            or document["remaining_lifetime_seconds"]
            < bound["lease_policy"]["minimum_remaining_seconds"]
            or after["witness_lease_id"] != proof["lease_id"]
            or after["witness_proof_hash"] != proof_sha256
            or after["witness_transition_id"]
            != proof["witness_transition_id"]
        ):
            raise WitnessLeaseWorkerError(
                "WebApp-FI lease closure differs"
            )
        _validate_status(
            document["witness_status"],
            request=bound,
            proof=proof,
            now=completed_at,
        )
        if (
            before["business_state_sha256"]
            != after["business_state_sha256"]
            or before["business_row_count"]
            != after["business_row_count"]
            or before["database_identity_sha256"]
            != after["database_identity_sha256"]
        ):
            raise WitnessLeaseWorkerError(
                "WebApp-FI result contains business drift"
            )
    unsigned = {
        key: item
        for key, item in document.items()
        if key != "response_sha256"
    }
    if (
        _nonzero_sha256(
            document["response_sha256"], label="worker response"
        )
        != _sha256(_canonical_json(unsigned))
    ):
        raise WitnessLeaseWorkerError(
            "lease worker response digest differs"
        )
    return document


class ExactReleaseBackend:
    """Use the immutable role generation and bounded local Docker socket."""

    def __init__(self, request: Mapping[str, Any]) -> None:
        # Imported lazily so pure validation and hostile tests never touch
        # Docker, environment material, or host paths.
        from scripts import (
            production_shadow_frozen_final_restore_worker as RESTORE,
        )
        from scripts import wa_ir_production_operation as WA
        from scripts.render_three_site_production_shadow_role_compose import (
            parse_env_values,
        )

        self.RESTORE = RESTORE
        self.WA = WA
        self.request = dict(request)
        self.role = str(request["role"])
        manifest_path = Path(request["role_manifest_path"])
        try:
            self.manifest = RESTORE.load_role_manifest(manifest_path)
        except Exception as exc:
            raise WitnessLeaseWorkerError(
                "installed role manifest is invalid"
            ) from exc
        if (
            self.manifest.canonical_sha256
            != request["role_manifest_sha256"]
            or self.manifest.operation_id != request["operation_id"]
            or self.manifest.role != self.role
            or self.manifest.release_sha != request["release_sha"]
            or self.manifest.release_tree_sha
            != request["release_tree_sha"]
            or self.manifest.controller_manifest_sha256
            != request["controller_manifest_sha256"]
        ):
            raise WitnessLeaseWorkerError(
                "installed role manifest binding differs"
            )
        try:
            payload = read_secure_bytes(
                self.manifest.environment_path,
                label="installed role environment",
                owner_uid=0,
                max_size=MAX_JSON_BYTES,
            )
            self.environment = parse_env_values(
                payload.decode("ascii")
            )
        except Exception as exc:
            raise WitnessLeaseWorkerError(
                "installed role environment is invalid"
            ) from exc
        prefix = self.role.upper()
        self.database_user = self.environment.get(
            f"{prefix}_POSTGRES_USER", ""
        )
        self.database_name = self.environment.get(
            f"{prefix}_POSTGRES_DB", ""
        )
        if (
            not isinstance(self.database_user, str)
            or re.fullmatch(
                rf"{re.escape(self.role)}(?:_[a-z]+)?",
                self.database_user,
            )
            is None
            or not isinstance(self.database_name, str)
            or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", self.database_name)
            is None
        ):
            raise WitnessLeaseWorkerError(
                "installed database identity is invalid"
            )
        command_env, overrides = RESTORE._compose_environment(  # noqa: SLF001
            self.manifest
        )
        controller = self.manifest.document
        runtime_ids = self._controller_manifest()[
            "artifacts"
        ]["role_runtime_image_ids"][self.role]
        self.runtime_ids = dict(runtime_ids)
        self.command_env = {
            **command_env,
            **overrides,
            "PRODUCTION_SHADOW_APP_IMAGE_ID": runtime_ids["app"],
            "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID": runtime_ids[
                "postgres"
            ],
            "PRODUCTION_SHADOW_REDIS_IMAGE_ID": runtime_ids["redis"],
            "PRODUCTION_SHADOW_NGINX_IMAGE_ID": runtime_ids["nginx"],
            "PRODUCTION_SHADOW_RELEASE_SHA": self.manifest.release_sha,
        }
        # Keep secrets in the Compose env-file.  They are never copied into
        # the host process environment or command line.
        if any(
            key in self.command_env
            for key in (
                "WRITER_WITNESS_CLIENT_SECRET",
                "WRITER_WITNESS_PRIVATE_KEY",
                "BOT_TOKEN",
                "SMSIR_API_KEY",
            )
        ):
            raise WitnessLeaseWorkerError(
                "host Docker environment contains a secret"
            )
        self.compose = [
            *RESTORE.DOCKER_BASE,
            "compose",
            "--project-name",
            self.manifest.paths.project_name,
            "--env-file",
            str(self.manifest.environment_path),
            "--file",
            str(self.manifest.canonical_compose_path),
            "--profile",
            (
                "webapp-fi-private"
                if self.role == "webapp_fi"
                else "webapp-ir-observe"
            ),
        ]
        self._verify_release_dependencies()
        if self.role == "webapp_fi":
            self._verify_writer_control_compose()
        self.database_id: str | None = None

    def _controller_manifest(self) -> Mapping[str, Any]:
        try:
            return self.RESTORE._load_controller_manifest(  # noqa: SLF001
                self.manifest.controller_manifest_path,
                self.manifest.controller_manifest_sha256,
            )
        except Exception as exc:
            raise WitnessLeaseWorkerError(
                "controller manifest cannot be loaded"
            ) from exc

    def _run(
        self,
        arguments: Sequence[str],
        *,
        timeout: int = 120,
        stdout_limit: int = MAX_JSON_BYTES,
        stderr_limit: int = 128 * 1024,
    ) -> bytes:
        try:
            result = self.RESTORE._bounded_command(  # noqa: SLF001
                list(arguments),
                timeout=timeout,
                env=self.command_env,
                stdin=subprocess.DEVNULL,
                stdout_limit=stdout_limit,
                stderr_limit=stderr_limit,
            )
        except Exception as exc:
            raise WitnessLeaseWorkerError(
                "bounded exact-host command failed"
            ) from exc
        if result.returncode != 0 or result.stderr:
            raise WitnessLeaseWorkerError(
                "bounded exact-host command was not clean"
            )
        return result.stdout

    def _json_output(
        self,
        arguments: Sequence[str],
        *,
        label: str,
        timeout: int = 120,
    ) -> Any:
        raw = self._run(arguments, timeout=timeout)
        try:
            return json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_strict_object,
            )
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise WitnessLeaseWorkerError(
                f"{label} returned invalid JSON"
            ) from exc

    def _verify_release_dependencies(self) -> None:
        release_root = self.manifest.paths.release_root
        expected = {
            WORKER_RELATIVE: self.request["worker_sha256"],
            BOOTSTRAP_RELATIVE: self.request["bootstrap_sha256"],
            STATUS_RELATIVE: self.request["status_sha256"],
            CLIENT_RELATIVE: self.request["client_sha256"],
            CONTRACT_RELATIVE: self.request["contract_sha256"],
            CONTROL_RELATIVE: self.request[
                "control_protocol_sha256"
            ],
        }
        for relative, digest in expected.items():
            try:
                payload = read_secure_bytes(
                    release_root / relative,
                    label=f"exact-release {relative}",
                    owner_uid=0,
                    max_size=MAX_RELEASE_FILE_BYTES,
                )
            except SecureFileError as exc:
                raise WitnessLeaseWorkerError(
                    "exact-release dependency is unavailable"
                ) from exc
            if _sha256(payload) != digest:
                raise WitnessLeaseWorkerError(
                    f"exact-release dependency changed: {relative}"
                )
            tracked = self._run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(release_root),
                    "ls-files",
                    "--stage",
                    "--",
                    relative.as_posix(),
                ],
                timeout=30,
            ).decode("utf-8").strip()
            if (
                re.fullmatch(
                    rf"100(?:644|755) [0-9a-f]{{40}} 0\t"
                    rf"{re.escape(relative.as_posix())}",
                    tracked,
                )
                is None
            ):
                raise WitnessLeaseWorkerError(
                    f"exact-release dependency is untracked: {relative}"
                )
        commands = (
            (("rev-parse", "HEAD^{commit}"), self.manifest.release_sha),
            (("rev-parse", "HEAD^{tree}"), self.manifest.release_tree_sha),
            (("branch", "--show-current"), ""),
            (
                (
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                    "--ignore-submodules=none",
                ),
                "",
            ),
        )
        for tail, expected_output in commands:
            output = self._run(
                ["/usr/bin/git", "-C", str(release_root), *tail],
                timeout=30,
            ).decode("utf-8").strip()
            if output != expected_output:
                raise WitnessLeaseWorkerError(
                    "exact release is not clean and detached"
                )

    def _verify_writer_control_compose(self) -> None:
        config = self._json_output(
            [*self.compose, "config", "--format", "json"],
            label="writer-control Compose",
        )
        services = config.get("services") if isinstance(config, dict) else None
        service = (
            services.get("webapp_fi_writer_control")
            if isinstance(services, dict)
            else None
        )
        networks = (
            service.get("networks")
            if isinstance(service, dict)
            else None
        )
        environment = (
            service.get("environment")
            if isinstance(service, dict)
            else None
        )
        if (
            not isinstance(service, dict)
            or service.get("image") != self.manifest.app_image_id
            or "build" in service
            or service.get("ports") not in (None, [])
            or service.get("network_mode") is not None
            or not isinstance(networks, (dict, list))
            or set(networks)
            != {"webapp_fi", "webapp_fi_witness_egress"}
            or not isinstance(environment, dict)
            or environment.get("PHYSICAL_SITE") != "webapp_fi"
            or environment.get("BACKGROUND_JOBS_ENABLED") != "false"
            or environment.get("WRITER_WITNESS_REQUIRED") != "true"
            or not environment.get("WRITER_WITNESS_CLIENT_KEY_ID")
            or not environment.get("WRITER_WITNESS_CLIENT_SECRET")
            or not environment.get("WRITER_WITNESS_PUBLIC_KEY")
            or environment.get("BOT_TOKEN")
            or any(
                name in service
                for name in ("ports", "devices", "privileged")
                if service.get(name) not in (None, False, [], {})
            )
        ):
            raise WitnessLeaseWorkerError(
                "writer-control one-off Compose closure differs"
            )

    def _database_container_id(self) -> str:
        try:
            row = self.RESTORE._database_container(  # noqa: SLF001
                self.manifest,
                self.RESTORE.SubprocessDockerRunner(),
            )
        except Exception as exc:
            raise WitnessLeaseWorkerError(
                "exact shadow database inventory is invalid"
            ) from exc
        state = row.get("State") if isinstance(row, Mapping) else None
        identifier = row.get("Id") if isinstance(row, Mapping) else None
        if (
            not isinstance(identifier, str)
            or re.fullmatch(r"[0-9a-f]{64}", identifier) is None
            or not isinstance(state, Mapping)
            or state.get("Status") != "running"
            or not isinstance(state.get("Health"), Mapping)
            or state["Health"].get("Status") != "healthy"
        ):
            raise WitnessLeaseWorkerError(
                "exact shadow database is not healthy"
            )
        return identifier

    def _psql(self, sql: str, *, timeout: int = 120) -> str:
        raw = self._run(
            [
                *self.RESTORE.DOCKER_BASE,
                "exec",
                self.database_id,
                "psql",
                "-U",
                self.database_user,
                "-d",
                self.database_name,
                "-v",
                "ON_ERROR_STOP=1",
                "--no-psqlrc",
                "-Atqc",
                sql,
            ],
            timeout=timeout,
            stdout_limit=16 * 1024 * 1024,
        )
        try:
            return raw.decode("utf-8").strip()
        except UnicodeError as exc:
            raise WitnessLeaseWorkerError(
                "database readback is not UTF-8"
            ) from exc

    def _business_fingerprint(self) -> tuple[str, int]:
        excluded = {
            "webapp_writer_state",
            "webapp_writer_transitions",
            "webapp_writer_activation_operations",
            "webapp_writer_witness_state",
            "webapp_writer_witness_receipts",
        }
        tables = [
            row
            for row in self._psql(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname='public' ORDER BY tablename"
            ).splitlines()
            if row and row not in excluded
        ]

        def stream(sql: str):
            try:
                return self.RESTORE._bounded_streaming_sha256(  # noqa: SLF001
                    [
                        *self.RESTORE.DOCKER_BASE,
                        "exec",
                        "--env",
                        (
                            "PGOPTIONS="
                            f"{self.WA.DATABASE_FINGERPRINT_PGOPTIONS}"
                        ),
                        "--env",
                        (
                            "PGCLIENTENCODING="
                            f"{self.WA.DATABASE_FINGERPRINT_CLIENT_ENCODING}"
                        ),
                        self.database_id,
                        "psql",
                        "-U",
                        self.database_user,
                        "-d",
                        self.database_name,
                        "-v",
                        "ON_ERROR_STOP=1",
                        "--no-psqlrc",
                        "--quiet",
                        "--command",
                        sql,
                    ],
                    timeout=900,
                    env=self.command_env,
                )
            except Exception as exc:
                raise WitnessLeaseWorkerError(
                    "business database fingerprint failed"
                ) from exc

        try:
            digest, rows, _tables = self.WA._fingerprint_from_streams(  # noqa: SLF001
                tables,
                stream,
            )
        except Exception as exc:
            raise WitnessLeaseWorkerError(
                "business database fingerprint differs"
            ) from exc
        return digest, rows

    def _database_identity_sha256(self) -> str:
        row = self._json_output(
            [*self.RESTORE.DOCKER_BASE, "inspect", self.database_id],
            label="database identity",
        )
        if not isinstance(row, list) or len(row) != 1:
            raise WitnessLeaseWorkerError(
                "database identity inspection differs"
            )
        document = row[0]
        config = document.get("Config")
        labels = config.get("Labels") if isinstance(config, dict) else None
        mounts = document.get("Mounts")
        networks = document.get("NetworkSettings", {}).get("Networks")
        identity = {
            "id": document.get("Id"),
            "image": document.get("Image"),
            "project": (
                labels.get("com.docker.compose.project")
                if isinstance(labels, dict)
                else None
            ),
            "service": (
                labels.get("com.docker.compose.service")
                if isinstance(labels, dict)
                else None
            ),
            "operation_id": (
                labels.get("trading-bot.production.operation-id")
                if isinstance(labels, dict)
                else None
            ),
            "mounts": mounts,
            "network_names": (
                sorted(networks)
                if isinstance(networks, dict)
                else None
            ),
        }
        if (
            identity["id"] != self.database_id
            or identity["image"] != self.manifest.postgres_image_id
            or identity["project"] != self.manifest.paths.project_name
            or identity["service"] != f"{self.role}_db"
            or identity["operation_id"] != self.manifest.operation_id
            or not isinstance(identity["mounts"], list)
            or identity["network_names"]
            != [f"{self.manifest.paths.project_name}_{self.role}"]
        ):
            raise WitnessLeaseWorkerError(
                "database identity escaped the bound operation"
            )
        return _sha256(_canonical_json(identity))

    def _oneoff_name(self, request_id: str) -> str:
        return (
            f"{self.manifest.paths.project_name}-witness-"
            f"{request_id.replace('-', '')[:20]}"
        )[:120]

    def _oneoff_container_id(self, name: str) -> str | None:
        raw = self._run(
            [
                *self.RESTORE.DOCKER_BASE,
                "ps",
                "--all",
                "--quiet",
                "--no-trunc",
                "--filter",
                f"name=^{name}$",
            ],
            timeout=30,
        ).decode("ascii").strip()
        if not raw:
            return None
        if re.fullmatch(r"[0-9a-f]{64}", raw) is None:
            raise WitnessLeaseWorkerError(
                "lease one-off identity is invalid"
            )
        return raw

    @staticmethod
    def _created_oneoff_is_unstarted(state: Mapping[str, Any]) -> bool:
        def zero_timestamp(value: Any) -> bool:
            return (
                value == ""
                or (
                    isinstance(value, str)
                    and re.fullmatch(
                        r"0001-01-01T00:00:00(?:\\.0+)?Z",
                        value,
                    )
                    is not None
                )
            )

        return (
            state.get("Status") == "created"
            and type(state.get("Pid")) is int
            and state.get("Pid") == 0
            and state.get("Running") is False
            and zero_timestamp(state.get("StartedAt"))
            and zero_timestamp(state.get("FinishedAt"))
        )

    def _cleanup_oneoff(
        self,
        name: str,
        *,
        allow_live: bool = False,
        allow_unstarted: bool = False,
        authority: Callable[[str], bool] | None = None,
    ) -> bool:
        raw = self._oneoff_container_id(name)
        if raw is None:
            return False
        row = self._json_output(
            [*self.RESTORE.DOCKER_BASE, "inspect", raw],
            label="lease one-off",
        )
        item = row[0] if isinstance(row, list) and len(row) == 1 else None
        config = item.get("Config") if isinstance(item, dict) else None
        labels = config.get("Labels") if isinstance(config, dict) else None
        mounts = item.get("Mounts") if isinstance(item, dict) else None
        state = item.get("State") if isinstance(item, dict) else None
        status = state.get("Status") if isinstance(state, dict) else None
        if (
            not isinstance(item, dict)
            or item.get("Id") != raw
            or item.get("Name") != f"/{name}"
            or not isinstance(labels, dict)
            or labels.get("com.docker.compose.project")
            != self.manifest.paths.project_name
            or labels.get("com.docker.compose.service")
            != "webapp_fi_writer_control"
            or labels.get("com.docker.compose.oneoff") != "True"
            or labels.get("trading-bot.production.operation-id")
            != self.manifest.operation_id
            or item.get("Image") != self.manifest.app_image_id
            or not isinstance(mounts, list)
            or any(
                not isinstance(mount, Mapping)
                or mount.get("Type") == "volume"
                for mount in mounts
            )
            or status
            not in {
                "created",
                "running",
                "paused",
                "restarting",
                "removing",
                "exited",
                "dead",
            }
        ):
            raise WitnessLeaseWorkerError(
                "refusing to clean a foreign lease one-off"
            )
        if allow_unstarted and authority is None:
            raise WitnessLeaseWorkerError(
                "created lease one-off cleanup requires live authority"
            )
        if status == "created" and allow_unstarted:
            if not self._created_oneoff_is_unstarted(state):
                raise WitnessLeaseWorkerError(
                    "created lease one-off is not provably unstarted"
                )
        if (
            not allow_live
            and status not in (
                {"exited", "dead"}
                | ({"created"} if allow_unstarted else set())
            )
        ):
            raise WitnessLeaseWorkerError(
                "authorized lease one-off is still live"
            )
        if authority is not None:
            _authority(authority, "before-stale-oneoff-removal")
        remove = [
            *self.RESTORE.DOCKER_BASE,
            "rm",
        ]
        if allow_live:
            remove.append("--force")
        remove.extend(("--volumes", raw))
        self._run(
            remove,
            timeout=60,
        )
        if self._oneoff_container_id(name) is not None:
            raise WitnessLeaseWorkerError(
                "lease one-off remains after cleanup"
            )
        return True

    def reconcile_authorized_oneoff(
        self,
        *,
        request_id: str,
        authority: Callable[[str], bool],
    ) -> None:
        if request_id != self.request["transition_request_id"]:
            raise WitnessLeaseWorkerError(
                "lease one-off reconciliation request differs"
            )
        if not callable(authority):
            raise WitnessLeaseWorkerError(
                "lease one-off reconciliation authority is unavailable"
            )
        if self.database_id is not None:
            return
        removed = False
        if self.role == "webapp_fi":
            removed = self._cleanup_oneoff(
                self._oneoff_name(request_id),
                allow_unstarted=True,
                authority=authority,
            )
        self.database_id = self._database_container_id()
        if removed:
            _validate_zero_surface(self.runtime_surface().document())

    def _writer_oneoff(
        self,
        command: Sequence[str],
        *,
        request_id: str,
        duration_seconds: int,
        timeout: int = 120,
    ) -> Mapping[str, Any]:
        name = self._oneoff_name(request_id)
        self._cleanup_oneoff(name)
        arguments = [
            *self.compose,
            "run",
            "--rm",
            "--no-deps",
            "--pull",
            "never",
            "--name",
            name,
            "--label",
            (
                "trading-bot.production.operation-id="
                f"{self.manifest.operation_id}"
            ),
            "-T",
            "--env",
            (
                "WRITER_WITNESS_LEASE_DURATION_SECONDS="
                f"{duration_seconds}"
            ),
            "webapp_fi_writer_control",
            *command,
        ]
        try:
            raw = self._run(
                arguments,
                timeout=timeout,
                stdout_limit=MAX_JSON_BYTES,
            )
        finally:
            self._cleanup_oneoff(name, allow_live=True)
        try:
            value = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_strict_object,
            )
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise WitnessLeaseWorkerError(
                "writer-control one-off returned invalid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise WitnessLeaseWorkerError(
                "writer-control one-off response is not an object"
            )
        return value

    def witness_public_key(self) -> str:
        value = self.environment.get("WRITER_WITNESS_PUBLIC_KEY", "")
        if not witness_public_key_is_valid(value):
            raise WitnessLeaseWorkerError(
                "installed Witness public key is invalid"
            )
        return value

    def writer_state(self) -> WriterState:
        raw = self._psql(
            "SELECT json_build_object("
            "'role', current_setting('trading_bot.physical_site', true),"
            "'singleton_count', count(*) OVER (),"
            "'control_state', control_state,"
            "'active_site', active_site,"
            "'writer_epoch', writer_epoch,"
            "'transition_id', transition_id,"
            "'witness_lease_id', witness_lease_id,"
            "'witness_lease_issued_at', witness_lease_issued_at,"
            "'witness_lease_expires_at', witness_lease_expires_at,"
            "'witness_proof_hash', witness_proof_hash,"
            "'witness_transition_id', witness_transition_id"
            ")::text FROM public.webapp_writer_state "
            "WHERE authority='webapp'"
        )
        try:
            row = json.loads(raw, object_pairs_hook=_strict_object)
        except (ValueError, json.JSONDecodeError) as exc:
            raise WitnessLeaseWorkerError(
                "writer state query returned invalid JSON"
            ) from exc
        if not isinstance(row, dict):
            raise WitnessLeaseWorkerError(
                "writer state query returned no singleton"
            )
        # The PostgreSQL session does not inherit application settings.  The
        # role is therefore bound by the immutable role manifest instead of a
        # mutable GUC.
        row["role"] = self.role
        for field in (
            "witness_lease_issued_at",
            "witness_lease_expires_at",
        ):
            if row[field] is not None:
                row[field] = _parse_any_timestamp(
                    row[field], label=field
                ).isoformat()
        business_digest, business_rows = self._business_fingerprint()
        return WriterState(
            role=self.role,
            singleton_count=row["singleton_count"],
            control_state=row["control_state"],
            active_site=row["active_site"],
            writer_epoch=row["writer_epoch"],
            transition_id=row["transition_id"],
            witness_lease_id=row["witness_lease_id"],
            witness_lease_issued_at=row[
                "witness_lease_issued_at"
            ],
            witness_lease_expires_at=row[
                "witness_lease_expires_at"
            ],
            witness_proof_hash=row["witness_proof_hash"],
            witness_transition_id=row["witness_transition_id"],
            business_state_sha256=business_digest,
            business_row_count=business_rows,
            database_identity_sha256=(
                self._database_identity_sha256()
            ),
        )

    def runtime_surface(self) -> RuntimeSurface:
        runner = self.RESTORE.SubprocessDockerRunner()
        try:
            identifiers = self.RESTORE._project_container_ids(  # noqa: SLF001
                self.manifest,
                runner,
            )
            rows = [
                self.RESTORE._inspect_container(  # noqa: SLF001
                    identifier,
                    self.manifest,
                    runner,
                )
                for identifier in identifiers
            ]
        except Exception as exc:
            raise WitnessLeaseWorkerError(
                "operation runtime inventory failed"
            ) from exc
        return self._runtime_surface_from_rows(
            rows,
            role=self.role,
            project_name=self.manifest.paths.project_name,
            operation_id=self.manifest.operation_id,
            database_id=self.database_id,
            runtime_ids=self.runtime_ids,
        )

    @staticmethod
    def _runtime_surface_from_rows(
        rows: Sequence[Mapping[str, Any]],
        *,
        role: str,
        project_name: str,
        operation_id: str,
        database_id: str,
        runtime_ids: Mapping[str, str],
    ) -> RuntimeSurface:
        counts = {
            "operation_oneoff_count": 0,
            "api_running_count": 0,
            "effect_running_count": 0,
            "bot_running_count": 0,
            "public_service_running_count": 0,
            "redis_running_count": 0,
            "other_running_count": 0,
        }
        database_running = False
        database_seen = 0
        for row in rows:
            config = row.get("Config")
            labels = (
                config.get("Labels")
                if isinstance(config, Mapping)
                else None
            )
            state = row.get("State")
            identifier = row.get("Id")
            image = row.get("Image")
            if (
                not isinstance(identifier, str)
                or re.fullmatch(r"[0-9a-f]{64}", identifier) is None
                or not isinstance(labels, Mapping)
                or labels.get("com.docker.compose.project")
                != project_name
                or labels.get("trading-bot.production.operation-id")
                != operation_id
                or not isinstance(state, Mapping)
                or state.get("Status")
                not in {
                    "created",
                    "running",
                    "paused",
                    "restarting",
                    "removing",
                    "exited",
                    "dead",
                }
            ):
                raise WitnessLeaseWorkerError(
                    "operation container inventory binding differs"
                )
            service = labels.get("com.docker.compose.service")
            oneoff = labels.get("com.docker.compose.oneoff") == "True"
            if not isinstance(service, str) or not service:
                raise WitnessLeaseWorkerError(
                    "operation container service identity is invalid"
                )
            if oneoff:
                counts["operation_oneoff_count"] += 1
            running = state.get("Status") == "running"
            if service == f"{role}_db":
                database_seen += 1
                if (
                    identifier != database_id
                    or oneoff
                    or image != runtime_ids.get("postgres")
                ):
                    raise WitnessLeaseWorkerError(
                        "operation database container identity differs"
                    )
                database_running = running
                continue
            expected_kind = (
                "redis"
                if service == f"{role}_redis"
                else "nginx"
                if service.endswith(("_nginx", "_dr_tls"))
                else "app"
            )
            if image != runtime_ids.get(expected_kind):
                raise WitnessLeaseWorkerError(
                    "operation service image identity differs"
                )
            if not running:
                continue
            categorized = False
            if service in {
                f"{role}_api",
                f"{role}_api_acceptance",
            }:
                counts["api_running_count"] += 1
                counts["public_service_running_count"] += 1
                categorized = True
            if "effect" in service:
                counts["effect_running_count"] += 1
                categorized = True
            if "bot" in service:
                counts["bot_running_count"] += 1
                categorized = True
            if service == f"{role}_redis":
                counts["redis_running_count"] += 1
                categorized = True
            if service.endswith(("_nginx", "_dr_tls")):
                counts["public_service_running_count"] += 1
                categorized = True
            if not categorized:
                counts["other_running_count"] += 1
        if database_seen != 1:
            raise WitnessLeaseWorkerError(
                "operation database singleton count differs"
            )
        return RuntimeSurface(
            database_running=database_running,
            **counts,
        )

    def acquire(
        self,
        *,
        campaign_id: str,
        request_id: str,
        release_sha: str,
        duration_seconds: int,
    ) -> Mapping[str, Any]:
        bootstrap = self._writer_oneoff(
            [
                "python",
                BOOTSTRAP_RELATIVE.as_posix(),
                "--campaign-id",
                campaign_id,
                "--request-id",
                request_id,
                "--expected-release-sha",
                release_sha,
                "--apply",
                "--confirm",
                (
                    f"bootstrap-writer:{campaign_id}:"
                    f"{request_id}:{release_sha}"
                ),
            ],
            request_id=request_id,
            duration_seconds=duration_seconds,
        )
        if (
            bootstrap.get("status") != "initialized"
            or bootstrap.get("campaign_id") != campaign_id
            or bootstrap.get("request_id") != request_id
            or bootstrap.get("release_sha") != release_sha
            or bootstrap.get("holder_site") != "webapp_fi"
            or bootstrap.get("writer_epoch") != 1
            or not bootstrap.get("lease_id")
            or not bootstrap.get("proof_hash")
        ):
            raise WitnessLeaseWorkerError(
                "bootstrap lease result differs"
            )
        # The bootstrap CLI intentionally emits only the proof digest.  This
        # exact replay reaches the durable Witness receipt and returns the
        # original signed public proof; it cannot create another term.
        replay = self._writer_oneoff(
            [
                "python",
                WORKER_RELATIVE.as_posix(),
                "--container-action",
                "acquire-replay",
                "--request-id",
                request_id,
                "--campaign-id",
                campaign_id,
                "--expected-release-sha",
                release_sha,
                "--lease-duration-seconds",
                str(duration_seconds),
                "--apply",
            ],
            request_id=request_id,
            duration_seconds=duration_seconds,
        )
        proof = replay.get("result", replay).get("proof")
        if (
            not isinstance(proof, dict)
            or _proof_hash(proof) != bootstrap["proof_hash"]
            or proof.get("lease_id") != bootstrap["lease_id"]
        ):
            raise WitnessLeaseWorkerError(
                "bootstrap signed-proof replay differs"
            )
        return {"proof": proof}

    def renew(
        self,
        *,
        request_id: str,
        release_sha: str,
        duration_seconds: int,
    ) -> Mapping[str, Any]:
        result = self._writer_oneoff(
            [
                "python",
                WORKER_RELATIVE.as_posix(),
                "--container-action",
                "renew",
                "--request-id",
                request_id,
                "--campaign-id",
                self.request["campaign_id"],
                "--expected-release-sha",
                release_sha,
                "--lease-duration-seconds",
                str(duration_seconds),
                "--apply",
            ],
            request_id=request_id,
            duration_seconds=duration_seconds,
        )
        proof = result.get("result", result).get("proof")
        if not isinstance(proof, dict):
            raise WitnessLeaseWorkerError(
                "renewal signed proof is unavailable"
            )
        return {"proof": proof}

    def witness_status(
        self,
        *,
        request_id: str,
        release_sha: str,
    ) -> Mapping[str, Any]:
        result = self._writer_oneoff(
            [
                "python",
                STATUS_RELATIVE.as_posix(),
                "--request-id",
                request_id,
                "--expected-release-sha",
                release_sha,
            ],
            request_id=request_id,
            duration_seconds=self.request["lease_policy"][
                "lease_duration_seconds"
            ],
        )
        return result.get("result", result)


async def _container_lease_action(args: argparse.Namespace) -> dict[str, Any]:
    """Run inside the exact writer-control image; never used by host mode."""

    from core.config import settings
    from core.runtime_identity import resolve_runtime_identity
    from core.writer_witness_client import (
        initialize_local_writer_lease_once,
        renew_local_writer_lease_once,
        writer_witness_client_from_settings,
    )

    try:
        request_id = str(UUID(args.request_id))
        campaign_id = str(UUID(args.campaign_id))
    except ValueError as exc:
        raise WitnessLeaseWorkerError(
            "container action identity is invalid"
        ) from exc
    duration = _bounded_int(
        args.lease_duration_seconds,
        minimum=MIN_LEASE_DURATION_SECONDS,
        maximum=MAX_LEASE_DURATION_SECONDS,
        label="container lease duration",
    )
    configured_release = str(settings.release_sha or "").lower()
    configured_duration = int(
        settings.writer_witness_lease_duration_seconds
    )
    if (
        not isinstance(args.expected_release_sha, str)
        or SHA40_RE.fullmatch(args.expected_release_sha) is None
        or configured_release != args.expected_release_sha
        or configured_duration != duration
    ):
        raise WitnessLeaseWorkerError(
            "container runtime release or lease policy differs"
        )
    identity = resolve_runtime_identity(settings)
    if identity.physical_site != "webapp_fi":
        raise WitnessLeaseWorkerError(
            "container lease action is restricted to WebApp-FI"
        )
    # Client construction can read credential material, so every public
    # release/site/duration gate above must pass before this point.
    client = writer_witness_client_from_settings(identity)
    if args.container_action == "acquire-replay":
        proof = await initialize_local_writer_lease_once(
            client=client,
            request_id=request_id,
            campaign_id=campaign_id,
            identity=identity,
            lease_duration_seconds=duration,
        )
    elif args.container_action == "renew":
        proof = await renew_local_writer_lease_once(
            client=client,
            request_id=request_id,
            identity=identity,
            lease_duration_seconds=duration,
        )
    else:
        raise WitnessLeaseWorkerError(
            "container lease action is invalid"
        )
    return {"proof": proof.canonical_payload}


def _read_initial_request() -> dict[str, Any]:
    raw = sys.stdin.buffer.readline(MAX_JSON_BYTES + 2)
    if (
        not raw
        or len(raw) > MAX_JSON_BYTES + 1
        or not raw.endswith(b"\n")
    ):
        raise WitnessLeaseWorkerError(
            "host request is missing or oversized"
        )
    try:
        value = json.loads(
            raw[:-1].decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise WitnessLeaseWorkerError(
            "host request is not strict JSON"
        ) from exc
    if not isinstance(value, dict):
        raise WitnessLeaseWorkerError(
            "host request is not an object"
        )
    return value


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-stdio", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument(
        "--container-action",
        choices=("acquire-replay", "renew"),
    )
    parser.add_argument("--request-id")
    parser.add_argument("--campaign-id")
    parser.add_argument("--expected-release-sha")
    parser.add_argument("--lease-duration-seconds", type=int)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.container_action is not None:
            if (
                args.host_stdio
                or args.confirm is not None
                or not args.apply
                or not args.request_id
                or not args.campaign_id
                or not args.expected_release_sha
                or args.lease_duration_seconds is None
            ):
                raise WitnessLeaseWorkerError(
                    "container action arguments are not exact"
                )
            result = asyncio.run(_container_lease_action(args))
        else:
            if not args.host_stdio:
                raise WitnessLeaseWorkerError(
                    "exact --host-stdio mode is required"
                )
            request = _read_initial_request()
            if args.apply:
                validated = validate_request(request)
                with CONTROL.StdioAuthority(
                    validated["request_sha256"]
                ) as authority:
                    result = execute(
                        validated,
                        apply=True,
                        confirm=args.confirm,
                        authority=authority,
                    )
            else:
                if args.confirm is not None:
                    raise WitnessLeaseWorkerError(
                        "plan mode does not accept confirmation"
                    )
                result = plan(request)
        payload = _canonical_json(
            {"schema": FINAL_SCHEMA, "result": result}
        )
        status = 0
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        payload = _canonical_json(
            {
                "schema": ERROR_SCHEMA,
                "status": "blocked",
                "error": "production-shadow Witness lease failed closed",
                "error_class": "WitnessLeaseWorkerError",
            }
        )
        status = 1
    if len(payload) > MAX_JSON_BYTES:
        payload = _canonical_json(
            {
                "schema": ERROR_SCHEMA,
                "status": "blocked",
                "error": "Witness lease response exceeded its bound",
                "error_class": "WitnessLeaseWorkerError",
            }
        )
        status = 1
    sys.stdout.buffer.write(payload + b"\n")
    sys.stdout.buffer.flush()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
