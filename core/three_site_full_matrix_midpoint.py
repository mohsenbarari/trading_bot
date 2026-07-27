"""Cryptographic checkpoint for the Full Matrix iteration-1 session refresh."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
from typing import Any

from core.canonical_json import canonical_json_bytes
from core.human_approval import (
    RELAY_RECEIPT_SCHEMA,
    approval_subject,
    staging_session_scope_sha256,
    verify_human_approval,
)
from core.secure_file_io import read_secure_text


MIDPOINT_BUNDLE_SCHEMA = "three-site-full-matrix-midpoint-refresh-bundle-v1"
MIDPOINT_PROBE_SCHEMA = "three-site-full-matrix-midpoint-refresh-probe-v1"
MIDPOINT_ARTIFACT_TYPE = "three-site-full-matrix-midpoint-refresh-probe-v1"
MIDPOINT_ACTIONS = ("start_full_matrix", "promote_ir", "failback_fi")
MIDPOINT_SESSION_ACTIONS = tuple(sorted(MIDPOINT_ACTIONS))
MIDPOINT_COMPLETED_ITERATION = 1
MIDPOINT_NEXT_ITERATION = 2
MIDPOINT_PAUSE_REASON = "witness_session_refresh_required"
MIDPOINT_RESUME_REASON = "witness_session_refreshed"
INITIAL_SESSION_MIN_RUNWAY = timedelta(hours=47)
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class FullMatrixMidpointError(RuntimeError):
    """Raised when a midpoint session-refresh proof is not exact."""


def full_matrix_session_scope_sha256(release_sha: str) -> str:
    try:
        return staging_session_scope_sha256(
            release_sha=release_sha,
            allowed_actions=MIDPOINT_SESSION_ACTIONS,
        )
    except Exception as exc:
        raise FullMatrixMidpointError(
            "Full Matrix Witness session scope is invalid"
        ) from exc


def verify_initial_session_runway(
    receipt: dict[str, Any],
    *,
    release_sha: str,
    now: datetime | None,
) -> str:
    """Require a near-fresh session before the first live journal mutation.

    Iteration 1 contains a real 24-hour endurance window and two independent
    one-hour backlog cycles.  The fixed 47-hour admission threshold preserves
    a further 21-hour controller/operator buffer without increasing the
    protocol's 48-hour maximum session lifetime.
    """

    if now is not None and now.tzinfo is None:
        raise FullMatrixMidpointError(
            "initial session runway time must include a timezone"
        )
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expiry = _utc(receipt.get("expires_at"), label="initial session expiry")
    session_hash = str(receipt.get("session_token_sha256") or "")
    if (
        receipt.get("schema") != RELAY_RECEIPT_SCHEMA
        or receipt.get("session_scope_sha256")
        != full_matrix_session_scope_sha256(release_sha)
        or SHA256.fullmatch(session_hash) is None
        or expiry - current < INITIAL_SESSION_MIN_RUNWAY
    ):
        raise FullMatrixMidpointError(
            "initial Full Matrix Witness session lacks the required runway"
        )
    return session_hash


_JOURNAL_BASE_FIELDS = {
    "schema",
    "timestamp",
    "event",
    "campaign_id",
    "campaign_hash",
    "release_sha",
    "activation_sha",
    "previous_hash",
    "event_hash",
}


def validate_midpoint_journal(
    records: list[dict[str, Any]],
    *,
    campaign: dict[str, Any],
    campaign_hash: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Validate the durable iteration-1/2 boundary without trusting the runner."""

    if SHA256.fullmatch(campaign_hash) is None:
        raise FullMatrixMidpointError("midpoint campaign hash is invalid")
    pauses = [
        (index, record)
        for index, record in enumerate(records)
        if record.get("event") == "campaign_paused"
    ]
    resumes = [
        (index, record)
        for index, record in enumerate(records)
        if record.get("event") == "campaign_resumed"
    ]
    if len(pauses) > 1 or len(resumes) > 1 or (resumes and not pauses):
        raise FullMatrixMidpointError("midpoint pause/resume event count is invalid")

    def is_iteration_two_intent(record: dict[str, Any]) -> bool:
        context = record.get("operation_context")
        return (
            record.get("iteration") == MIDPOINT_NEXT_ITERATION
            or (
                isinstance(context, dict)
                and context.get("iteration") == MIDPOINT_NEXT_ITERATION
            )
            or record.get("event") in {"campaign_finalized", "campaign_completed"}
        )

    if not pauses:
        if any(is_iteration_two_intent(record) for record in records):
            raise FullMatrixMidpointError(
                "iteration 2/finalization intent exists without a midpoint pause"
            )
        return None, None

    pause_index, pause = pauses[0]
    phases = campaign.get("required_phases")
    required = campaign.get("required_scenarios")
    if (
        not isinstance(phases, list)
        or not phases
        or not isinstance(required, dict)
        or pause_index == 0
    ):
        raise FullMatrixMidpointError("midpoint campaign catalog is invalid")
    final_phase = phases[-1]
    previous = records[pause_index - 1]
    pre_pause_head = str(previous.get("event_hash") or "")
    pause_fields = _JOURNAL_BASE_FIELDS | {
        "reason",
        "phase",
        "completed_iteration",
        "next_iteration",
        "pre_pause_journal_head",
        "cleanup_evidence_hash",
    }
    cleanup = previous.get("cleanup_result")
    if (
        set(pause) != pause_fields
        or pause.get("campaign_hash") != campaign_hash
        or pause.get("reason") != MIDPOINT_PAUSE_REASON
        or pause.get("phase") != final_phase
        or pause.get("completed_iteration") != MIDPOINT_COMPLETED_ITERATION
        or pause.get("next_iteration") != MIDPOINT_NEXT_ITERATION
        or SHA256.fullmatch(pre_pause_head) is None
        or pause.get("pre_pause_journal_head") != pre_pause_head
        or pause.get("previous_hash") != pre_pause_head
        or previous.get("event") != "phase_passed"
        or previous.get("phase") != final_phase
        or previous.get("iteration") != MIDPOINT_COMPLETED_ITERATION
        or not isinstance(cleanup, dict)
        or cleanup.get("residue_count") != 0
        or cleanup.get("production_touched") is not False
        or pause.get("cleanup_evidence_hash")
        != previous.get("cleanup_evidence_hash")
        or SHA256.fullmatch(str(pause.get("cleanup_evidence_hash") or "")) is None
    ):
        raise FullMatrixMidpointError("midpoint pause is not at a zero-residue boundary")

    expected_scenarios = {
        (MIDPOINT_COMPLETED_ITERATION, phase, scenario)
        for phase in phases
        for scenario in required.get(phase, ())
    }
    observed_scenarios = {
        (
            record.get("iteration"),
            record.get("phase"),
            record.get("scenario_id"),
        )
        for record in records[:pause_index]
        if record.get("event") == "scenario_passed"
    }
    expected_phases = {
        (MIDPOINT_COMPLETED_ITERATION, phase)
        for phase in phases
    }
    observed_phases = {
        (record.get("iteration"), record.get("phase"))
        for record in records[:pause_index]
        if record.get("event") == "phase_passed"
    }
    starts = {
        str(record.get("operation_id") or "")
        for record in records[:pause_index]
        if record.get("event") in {"campaign_started", "operation_started"}
    }
    passes = {
        str(record.get("operation_id") or "")
        for record in records[:pause_index]
        if record.get("event") == "operation_passed"
    }
    open_scenarios: set[tuple[Any, Any, Any]] = set()
    for record in records[:pause_index]:
        if record.get("event") == "scenario_started":
            open_scenarios.add(
                (
                    record.get("iteration"),
                    record.get("phase"),
                    record.get("scenario_id"),
                )
            )
        elif record.get("event") in {"scenario_passed", "scenario_recovered"}:
            open_scenarios.discard(
                (
                    record.get("iteration"),
                    record.get("phase"),
                    record.get("scenario_id"),
                )
            )
    if (
        observed_scenarios != expected_scenarios
        or observed_phases != expected_phases
        or starts != passes
        or open_scenarios
        or any(is_iteration_two_intent(record) for record in records[:pause_index])
    ):
        raise FullMatrixMidpointError(
            "midpoint pause does not follow a complete iteration 1"
        )

    if not resumes:
        if pause_index != len(records) - 1:
            raise FullMatrixMidpointError("unresumed midpoint pause is not the journal tail")
        return pause, None

    resume_index, resume = resumes[0]
    resume_fields = _JOURNAL_BASE_FIELDS | {
        "reason",
        "phase",
        "completed_iteration",
        "next_iteration",
        "pre_pause_journal_head",
        "pause_event_hash",
        "refresh_bundle",
        "refresh_summary",
    }
    if (
        resume_index != pause_index + 1
        or set(resume) != resume_fields
        or resume.get("campaign_hash") != campaign_hash
        or resume.get("reason") != MIDPOINT_RESUME_REASON
        or resume.get("phase") != final_phase
        or resume.get("completed_iteration") != MIDPOINT_COMPLETED_ITERATION
        or resume.get("next_iteration") != MIDPOINT_NEXT_ITERATION
        or resume.get("pre_pause_journal_head") != pre_pause_head
        or resume.get("pause_event_hash") != pause.get("event_hash")
        or resume.get("previous_hash") != pause.get("event_hash")
        or not isinstance(resume.get("refresh_bundle"), dict)
        or not isinstance(resume.get("refresh_summary"), dict)
        or any(
            is_iteration_two_intent(record)
            for record in records[:resume_index]
            if record is not pause
        )
    ):
        raise FullMatrixMidpointError("midpoint resume ordering/identity is invalid")
    return pause, resume


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FullMatrixMidpointError("midpoint JSON contains a duplicate field")
        result[key] = value
    return result


def _utc(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise FullMatrixMidpointError(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FullMatrixMidpointError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise FullMatrixMidpointError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def load_bound_witness_public_key(
    path: Path,
    *,
    failover_control_path: Path,
    campaign_id: str,
    gate_group_id: str,
    execution_class: str,
    release_sha: str,
    expected_sha256: str | None = None,
    expected_control_sha256: str | None = None,
) -> str:
    """Read and cross-check both campaign-bound Witness trust references."""

    try:
        encoded = read_secure_text(
            path,
            label="campaign-bound failover backend config",
            max_size=512 * 1024,
        ).encode("utf-8")
        if (
            expected_sha256 is not None
            and (
                SHA256.fullmatch(expected_sha256) is None
                or hashlib.sha256(encoded).hexdigest() != expected_sha256
            )
        ):
            raise FullMatrixMidpointError(
                "campaign-bound failover backend snapshot hash differs"
            )
        payload = json.loads(
            encoded,
            object_pairs_hook=_strict_object,
        )
    except Exception as exc:
        raise FullMatrixMidpointError(
            "campaign-bound failover backend config is invalid"
        ) from exc
    witness = payload.get("witness") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "three-site-staging-failover-backend-v1"
        or payload.get("campaign_id") != campaign_id
        or payload.get("release_sha") != release_sha
        or not isinstance(witness, dict)
        or not isinstance(witness.get("public_key"), str)
    ):
        raise FullMatrixMidpointError(
            "campaign-bound failover backend identity is invalid"
        )
    public_key = witness["public_key"]
    try:
        decoded = base64.b64decode(public_key, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise FullMatrixMidpointError(
            "campaign-bound Witness public key is invalid"
        ) from exc
    if len(decoded) != 32:
        raise FullMatrixMidpointError(
            "campaign-bound Witness public key is not Ed25519"
        )
    try:
        control_encoded = read_secure_text(
            failover_control_path,
            label="campaign-bound failover control config",
            max_size=64 * 1024,
        ).encode("utf-8")
        if (
            expected_control_sha256 is not None
            and (
                SHA256.fullmatch(expected_control_sha256) is None
                or hashlib.sha256(control_encoded).hexdigest()
                != expected_control_sha256
            )
        ):
            raise FullMatrixMidpointError(
                "campaign-bound failover control snapshot hash differs"
            )
        control = json.loads(control_encoded, object_pairs_hook=_strict_object)
        fields = {
            "schema",
            "campaign_id",
            "gate_group_id",
            "execution_class",
            "release_sha",
            "backend_config",
            "relay_credentials",
            "witness_relay_public_key_file",
            "journal_root",
        }
        if not isinstance(control, dict):
            raise FullMatrixMidpointError(
                "campaign-bound failover control must be an object"
            )
        witness_key_path = Path(
            str(control.get("witness_relay_public_key_file") or "")
        )
        backend_pointer = Path(str(control.get("backend_config") or ""))
        if (
            set(control) != fields
            or control.get("schema")
            != "three-site-full-matrix-failover-control-v1"
            or control.get("campaign_id") != campaign_id
            or control.get("gate_group_id") != gate_group_id
            or control.get("execution_class") != execution_class
            or control.get("release_sha") != release_sha
            or not backend_pointer.is_absolute()
            or backend_pointer != path.resolve()
            or not witness_key_path.is_absolute()
        ):
            raise FullMatrixMidpointError(
                "campaign-bound failover control identity is invalid"
            )
        control_public_key = read_secure_text(
            witness_key_path,
            label="campaign-bound Witness relay public key",
            max_size=16 * 1024,
        ).strip()
    except Exception as exc:
        raise FullMatrixMidpointError(
            "campaign-bound failover control trust reference is invalid"
        ) from exc
    if not hmac.compare_digest(public_key, control_public_key):
        raise FullMatrixMidpointError(
            "campaign-bound Witness trust references differ"
        )
    return public_key


def _probe_descriptor(
    *,
    action: str,
    campaign: dict[str, Any],
    campaign_hash: str,
    pre_pause_journal_head: str,
) -> dict[str, Any]:
    if (
        action not in MIDPOINT_ACTIONS
        or SHA256.fullmatch(campaign_hash) is None
        or SHA256.fullmatch(pre_pause_journal_head) is None
        or SHA40.fullmatch(str(campaign.get("release_sha") or "")) is None
    ):
        raise FullMatrixMidpointError("midpoint probe identity is invalid")
    return {
        "schema": MIDPOINT_PROBE_SCHEMA,
        "action": action,
        "campaign_id": campaign["campaign_id"],
        "gate_group_id": campaign["gate_group_id"],
        "execution_class": campaign["execution_class"],
        "campaign_hash": campaign_hash,
        "release_sha": campaign["release_sha"],
        "pre_pause_journal_head": pre_pause_journal_head,
        "completed_iteration": MIDPOINT_COMPLETED_ITERATION,
        "next_iteration": MIDPOINT_NEXT_ITERATION,
    }


def midpoint_subject(
    *,
    action: str,
    campaign: dict[str, Any],
    campaign_hash: str,
    pre_pause_journal_head: str,
) -> dict[str, Any]:
    descriptor = _probe_descriptor(
        action=action,
        campaign=campaign,
        campaign_hash=campaign_hash,
        pre_pause_journal_head=pre_pause_journal_head,
    )
    return approval_subject(
        artifact_type=MIDPOINT_ARTIFACT_TYPE,
        artifact_sha256=hashlib.sha256(canonical_json_bytes(descriptor)).hexdigest(),
        release_sha=campaign["release_sha"],
        bindings={
            "action": action,
            "campaign_id": campaign["campaign_id"],
            "gate_group_id": campaign["gate_group_id"],
            "execution_class": campaign["execution_class"],
            "campaign_hash": campaign_hash,
            "pre_pause_journal_head": pre_pause_journal_head,
            "completed_iteration": MIDPOINT_COMPLETED_ITERATION,
            "next_iteration": MIDPOINT_NEXT_ITERATION,
        },
    )


def midpoint_subjects(
    *,
    campaign: dict[str, Any],
    campaign_hash: str,
    pre_pause_journal_head: str,
) -> dict[str, dict[str, Any]]:
    return {
        action: midpoint_subject(
            action=action,
            campaign=campaign,
            campaign_hash=campaign_hash,
            pre_pause_journal_head=pre_pause_journal_head,
        )
        for action in MIDPOINT_ACTIONS
    }


def assemble_midpoint_bundle(
    *,
    campaign: dict[str, Any],
    campaign_hash: str,
    pre_pause_journal_head: str,
    receipts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if set(receipts) != set(MIDPOINT_ACTIONS):
        raise FullMatrixMidpointError("midpoint receipt set is incomplete")
    subjects = midpoint_subjects(
        campaign=campaign,
        campaign_hash=campaign_hash,
        pre_pause_journal_head=pre_pause_journal_head,
    )
    return {
        "schema": MIDPOINT_BUNDLE_SCHEMA,
        "campaign_id": campaign["campaign_id"],
        "gate_group_id": campaign["gate_group_id"],
        "execution_class": campaign["execution_class"],
        "campaign_hash": campaign_hash,
        "release_sha": campaign["release_sha"],
        "pre_pause_journal_head": pre_pause_journal_head,
        "completed_iteration": MIDPOINT_COMPLETED_ITERATION,
        "next_iteration": MIDPOINT_NEXT_ITERATION,
        "probes": [
            {
                "action": action,
                "subject": subjects[action],
                "receipt": receipts[action],
            }
            for action in MIDPOINT_ACTIONS
        ],
    }


def verify_midpoint_bundle(
    bundle: Any,
    *,
    campaign: dict[str, Any],
    campaign_hash: str,
    pre_pause_journal_head: str,
    pause_timestamp: str,
    policy_payload: dict[str, Any],
    witness_public_key: str,
    prior_session_token_sha256: str | None,
    now: datetime | None,
    require_fresh: bool,
) -> dict[str, Any]:
    """Verify three action probes from one post-pause Witness session."""

    if type(require_fresh) is not bool:
        raise FullMatrixMidpointError("midpoint freshness setting is invalid")
    fields = {
        "schema",
        "campaign_id",
        "gate_group_id",
        "execution_class",
        "campaign_hash",
        "release_sha",
        "pre_pause_journal_head",
        "completed_iteration",
        "next_iteration",
        "probes",
    }
    if (
        not isinstance(bundle, dict)
        or set(bundle) != fields
        or bundle.get("schema") != MIDPOINT_BUNDLE_SCHEMA
        or bundle.get("campaign_id") != campaign.get("campaign_id")
        or bundle.get("gate_group_id") != campaign.get("gate_group_id")
        or bundle.get("execution_class") != campaign.get("execution_class")
        or bundle.get("campaign_hash") != campaign_hash
        or bundle.get("release_sha") != campaign.get("release_sha")
        or bundle.get("pre_pause_journal_head") != pre_pause_journal_head
        or bundle.get("completed_iteration") != MIDPOINT_COMPLETED_ITERATION
        or bundle.get("next_iteration") != MIDPOINT_NEXT_ITERATION
    ):
        raise FullMatrixMidpointError("midpoint bundle identity/schema is invalid")
    probes = bundle.get("probes")
    if not isinstance(probes, list) or len(probes) != len(MIDPOINT_ACTIONS):
        raise FullMatrixMidpointError("midpoint bundle probe set is invalid")
    expected_subjects = midpoint_subjects(
        campaign=campaign,
        campaign_hash=campaign_hash,
        pre_pause_journal_head=pre_pause_journal_head,
    )
    pause_time = _utc(pause_timestamp, label="midpoint pause timestamp")
    campaign_expiry = _utc(campaign.get("expires_at"), label="campaign expiry")
    expected_session_scope = full_matrix_session_scope_sha256(
        str(campaign.get("release_sha") or "")
    )
    session_hash: str | None = None
    approval_id: str | None = None
    expiry: datetime | None = None
    receipt_hashes: dict[str, str] = {}
    request_ids: set[str] = set()
    receipt_ids: set[str] = set()
    issued_values: list[datetime] = []
    for index, action in enumerate(MIDPOINT_ACTIONS):
        probe = probes[index]
        if (
            not isinstance(probe, dict)
            or set(probe) != {"action", "subject", "receipt"}
            or probe.get("action") != action
            or probe.get("subject") != expected_subjects[action]
        ):
            raise FullMatrixMidpointError("midpoint probe order/subject is invalid")
        receipt = probe.get("receipt")
        if (
            not isinstance(receipt, dict)
            or receipt.get("schema") != RELAY_RECEIPT_SCHEMA
            or receipt.get("subject") != expected_subjects[action]
            or receipt.get("session_scope_sha256") != expected_session_scope
        ):
            raise FullMatrixMidpointError("midpoint probe receipt is not relay-bound")
        try:
            verified = verify_human_approval(
                receipt,
                policy_payload=policy_payload,
                expected_action=action,
                expected_environment="staging",
                expected_subject=expected_subjects[action],
                now=now,
                require_fresh=require_fresh,
                witness_relay_public_key=witness_public_key,
            )
        except Exception as exc:
            raise FullMatrixMidpointError(
                "midpoint relay receipt verification failed"
            ) from exc
        if verified.issued_at <= pause_time:
            raise FullMatrixMidpointError(
                "midpoint relay receipt predates the durable pause"
            )
        if verified.expires_at < campaign_expiry:
            raise FullMatrixMidpointError(
                "midpoint Witness session expires before the campaign"
            )
        current_session_hash = str(receipt.get("session_token_sha256") or "")
        current_request_id = str(receipt.get("request_id") or "")
        current_receipt_id = str(receipt.get("receipt_id") or "")
        if (
            SHA256.fullmatch(current_session_hash) is None
            or current_request_id in request_ids
            or current_receipt_id in receipt_ids
        ):
            raise FullMatrixMidpointError("midpoint relay receipt identity is unsafe")
        if session_hash is None:
            session_hash = current_session_hash
            approval_id = verified.approval_id
            expiry = verified.expires_at
        elif (
            current_session_hash != session_hash
            or verified.approval_id != approval_id
            or verified.expires_at != expiry
        ):
            raise FullMatrixMidpointError(
                "midpoint probes were not issued from one Witness session"
            )
        request_ids.add(current_request_id)
        receipt_ids.add(current_receipt_id)
        issued_values.append(verified.issued_at)
        receipt_hashes[action] = verified.token_hash
    if session_hash is None or approval_id is None or expiry is None:
        raise FullMatrixMidpointError("midpoint session proof is incomplete")
    if (
        prior_session_token_sha256 is not None
        and (
            SHA256.fullmatch(prior_session_token_sha256) is None
            or hmac.compare_digest(session_hash, prior_session_token_sha256)
        )
    ):
        raise FullMatrixMidpointError(
            "midpoint receipts did not rotate the initial Witness session"
        )
    bundle_hash = hashlib.sha256(canonical_json_bytes(bundle)).hexdigest()
    return {
        "bundle_sha256": bundle_hash,
        "session_token_sha256": session_hash,
        "session_scope_sha256": expected_session_scope,
        "approval_id": approval_id,
        "issued_after": min(issued_values).isoformat(),
        "expires_at": expiry.isoformat(),
        "receipt_sha256": receipt_hashes,
    }
