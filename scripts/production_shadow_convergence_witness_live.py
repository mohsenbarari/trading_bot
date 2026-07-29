#!/usr/bin/env python3
"""Pure local contract for a fresh signed Witness live-lease observation.

The module accepts only one canonical, redacted input record.  It neither
contacts Witness nor reads an Object Storage receipt.  The signed lease proof
is verified with the supplied public key, then reduced to the exact
``witness_live`` observation schema expected by the convergence gate.  It does
not publish the result or integrate it with a source-set.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Mapping
from uuid import UUID

from core.writer_witness_contract import (
    WitnessProofError,
    validate_witness_lease_proof,
    witness_public_key_is_valid,
)


INPUT_SCHEMA = "production-shadow-convergence-witness-live-proof-input-v1"
OBSERVATION_SCHEMA = "production-shadow-convergence-witness-live-observation-v1"
EXPECTED_HOLDER_SITE = "webapp_fi"
EXPECTED_WRITER_EPOCH = 1
MAX_INPUT_BYTES = 128 * 1024
MAX_INPUT_AGE = timedelta(minutes=15)
MAX_FUTURE_SKEW = timedelta(seconds=5)
MIN_REMAINING_LEASE = timedelta(seconds=90)
MAX_LEASE_LIFETIME_SECONDS = 3600
MAX_LEASE_CLOCK_SKEW_SECONDS = 30
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
ZERO_SHA256 = "0" * 64

IDENTITY_FIELDS = frozenset(
    {
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "manifest_sha256",
        "plan_sha256",
        "approval_sha256",
    }
)
INPUT_FIELDS = frozenset(
    {
        "schema",
        "status",
        *IDENTITY_FIELDS,
        "journal_started_at",
        "observed_at",
        "witness_public_key",
        "witness_public_key_sha256",
        "signed_proof",
        "signed_proof_sha256",
        "witness_status_receipt_sha256",
        "input_sha256",
    }
)
OBSERVATION_FIELDS = frozenset(
    {
        "schema",
        "status",
        *IDENTITY_FIELDS,
        "observed_at",
        "witness_public_key",
        "witness_public_key_sha256",
        "signed_proof",
        "signed_proof_sha256",
        "witness_status_receipt_sha256",
        "lease_live_readback_sha256",
    }
)


class WitnessLiveContractError(ValueError):
    """A redacted Witness proof is malformed, stale, or not live enough."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WitnessLiveContractError("JSON document has duplicate fields")
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
        raise WitnessLiveContractError("Witness live value is not canonical JSON") from exc


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _proof_sha256(value: Mapping[str, Any]) -> str:
    try:
        payload = json.dumps(
            dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise WitnessLiveContractError("signed Witness proof is invalid") from exc
    return hashlib.sha256(payload).hexdigest()


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == ZERO_SHA256:
        raise WitnessLiveContractError(f"{label} is not a nonzero SHA-256")
    return value


def _uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise WitnessLiveContractError(f"{label} is invalid")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise WitnessLiveContractError(f"{label} is invalid") from exc
    if str(parsed) != value or parsed.int == 0:
        raise WitnessLiveContractError(f"{label} is invalid")
    return value


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise WitnessLiveContractError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise WitnessLiveContractError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise WitnessLiveContractError(f"{label} is invalid")
    normalized = parsed.astimezone(timezone.utc)
    if normalized.isoformat().replace("+00:00", "Z") != value:
        raise WitnessLiveContractError(f"{label} is invalid")
    return normalized


def _timestamp_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise WitnessLiveContractError("timestamp is invalid")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _identity(
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    manifest_sha256: str,
    plan_sha256: str,
    approval_sha256: str,
) -> dict[str, str]:
    campaign = _uuid(campaign_id, label="campaign_id")
    operation = _uuid(operation_id, label="operation_id")
    if campaign == operation:
        raise WitnessLiveContractError("campaign_id and operation_id must differ")
    if not isinstance(release_sha, str) or SHA40_RE.fullmatch(release_sha) is None:
        raise WitnessLiveContractError("release_sha is invalid")
    if not isinstance(release_tree_sha, str) or SHA40_RE.fullmatch(release_tree_sha) is None:
        raise WitnessLiveContractError("release_tree_sha is invalid")
    return {
        "campaign_id": campaign,
        "operation_id": operation,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "manifest_sha256": _nonzero_sha256(manifest_sha256, label="manifest_sha256"),
        "plan_sha256": _nonzero_sha256(plan_sha256, label="plan_sha256"),
        "approval_sha256": _nonzero_sha256(approval_sha256, label="approval_sha256"),
    }


def _identity_from_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != IDENTITY_FIELDS:
        raise WitnessLiveContractError("Witness live identity fields differ")
    return _identity(
        campaign_id=value["campaign_id"],
        operation_id=value["operation_id"],
        release_sha=value["release_sha"],
        release_tree_sha=value["release_tree_sha"],
        manifest_sha256=value["manifest_sha256"],
        plan_sha256=value["plan_sha256"],
        approval_sha256=value["approval_sha256"],
    )


def _input_digest(document: Mapping[str, Any]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "input_sha256"})


def _lease_readback_digest(*, proof_sha256: str, receipt_sha256: str, observed_at: str) -> str:
    return _sha256(
        {
            "proof_sha256": proof_sha256,
            "status_receipt_sha256": receipt_sha256,
            "observed_at": observed_at,
        }
    )


def parse_input_payload(payload: bytes) -> dict[str, Any]:
    """Decode one bounded canonical redacted Witness input without I/O."""

    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_INPUT_BYTES:
        raise WitnessLiveContractError("Witness live input payload is invalid")
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WitnessLiveContractError("Witness live input payload is invalid") from exc
    if not isinstance(document, dict) or payload != _canonical_json(document) + b"\n":
        raise WitnessLiveContractError("Witness live input payload is not canonical")
    return document


def validate_input(
    value: Any,
    *,
    identity: Mapping[str, Any],
    journal_started_at: datetime,
    now: datetime,
) -> tuple[dict[str, Any], datetime]:
    """Verify one redacted signed Witness proof against the exact journal."""

    expected_identity = _identity_from_mapping(identity)
    if not isinstance(journal_started_at, datetime) or journal_started_at.tzinfo is None:
        raise WitnessLiveContractError("journal start time is invalid")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise WitnessLiveContractError("validation time is invalid")
    journal_start = journal_started_at.astimezone(timezone.utc)
    current = now.astimezone(timezone.utc)
    if not isinstance(value, Mapping) or set(value) != INPUT_FIELDS:
        raise WitnessLiveContractError("Witness live input fields differ")
    document = dict(value)
    expected = {
        "schema": INPUT_SCHEMA,
        "status": "observed",
        **expected_identity,
        "journal_started_at": _timestamp_text(journal_start),
    }
    if any(document.get(key) != item for key, item in expected.items()):
        raise WitnessLiveContractError("Witness live input binding differs")
    observed_at = _timestamp(document.get("observed_at"), label="Witness live observed_at")
    if (
        observed_at < journal_start
        or observed_at > current + MAX_FUTURE_SKEW
        or current - observed_at > MAX_INPUT_AGE
    ):
        raise WitnessLiveContractError("Witness live input is not fresh for this journal")
    public_key = document.get("witness_public_key")
    if (
        not isinstance(public_key, str)
        or not witness_public_key_is_valid(public_key)
        or document.get("witness_public_key_sha256")
        != hashlib.sha256(public_key.encode("ascii")).hexdigest()
    ):
        raise WitnessLiveContractError("Witness live public key binding differs")
    signed_proof = document.get("signed_proof")
    if not isinstance(signed_proof, Mapping) or document.get("signed_proof_sha256") != _proof_sha256(signed_proof):
        raise WitnessLiveContractError("Witness live signed proof binding differs")
    _nonzero_sha256(
        document.get("witness_status_receipt_sha256"), label="Witness status receipt"
    )
    _nonzero_sha256(document.get("input_sha256"), label="Witness live input")
    if document["input_sha256"] != _input_digest(document):
        raise WitnessLiveContractError("Witness live input digest differs")
    try:
        proof = validate_witness_lease_proof(
            dict(signed_proof),
            public_key_base64=public_key,
            expected_site=EXPECTED_HOLDER_SITE,
            expected_epoch=EXPECTED_WRITER_EPOCH,
            now=current,
            safety_margin_seconds=0,
            max_clock_skew_seconds=MAX_LEASE_CLOCK_SKEW_SECONDS,
            max_lifetime_seconds=MAX_LEASE_LIFETIME_SECONDS,
        ).canonical_payload
    except WitnessProofError as exc:
        raise WitnessLiveContractError("Witness live signature or lifetime differs") from exc
    if proof != dict(signed_proof):
        raise WitnessLiveContractError("Witness live signed proof canonical form differs")
    expires_at = _timestamp(proof["expires_at"].replace("+00:00", "Z"), label="Witness lease expiry")
    if expires_at - current < MIN_REMAINING_LEASE:
        raise WitnessLiveContractError("Witness live lease lacks 90 seconds remaining")
    return document, observed_at


def build_observation(
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    manifest_sha256: str,
    plan_sha256: str,
    approval_sha256: str,
    journal_started_at: datetime,
    witness_input: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Build one gate-compatible live Witness observation without publication."""

    identity = _identity(
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan_sha256,
        approval_sha256=approval_sha256,
    )
    checked, observed_at = validate_input(
        witness_input,
        identity=identity,
        journal_started_at=journal_started_at,
        now=now,
    )
    document = {
        "schema": OBSERVATION_SCHEMA,
        "status": "observed",
        **identity,
        "observed_at": _timestamp_text(observed_at),
        "witness_public_key": checked["witness_public_key"],
        "witness_public_key_sha256": checked["witness_public_key_sha256"],
        "signed_proof": dict(checked["signed_proof"]),
        "signed_proof_sha256": checked["signed_proof_sha256"],
        "witness_status_receipt_sha256": checked["witness_status_receipt_sha256"],
        "lease_live_readback_sha256": _lease_readback_digest(
            proof_sha256=checked["signed_proof_sha256"],
            receipt_sha256=checked["witness_status_receipt_sha256"],
            observed_at=_timestamp_text(observed_at),
        ),
    }
    return validate_observation(
        document,
        identity=identity,
        journal_started_at=journal_started_at,
        now=now,
    )


def validate_observation(
    value: Any,
    *,
    identity: Mapping[str, Any],
    journal_started_at: datetime,
    now: datetime,
) -> dict[str, Any]:
    """Validate the future gate-compatible observation without publishing it."""

    expected_identity = _identity_from_mapping(identity)
    if not isinstance(journal_started_at, datetime) or journal_started_at.tzinfo is None:
        raise WitnessLiveContractError("journal start time is invalid")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise WitnessLiveContractError("validation time is invalid")
    journal_start = journal_started_at.astimezone(timezone.utc)
    current = now.astimezone(timezone.utc)
    if not isinstance(value, Mapping) or set(value) != OBSERVATION_FIELDS:
        raise WitnessLiveContractError("Witness live observation fields differ")
    document = dict(value)
    expected = {
        "schema": OBSERVATION_SCHEMA,
        "status": "observed",
        **expected_identity,
    }
    if any(document.get(key) != item for key, item in expected.items()):
        raise WitnessLiveContractError("Witness live observation binding differs")
    observed_at = _timestamp(document.get("observed_at"), label="Witness live observed_at")
    if (
        observed_at < journal_start
        or observed_at > current + MAX_FUTURE_SKEW
        or current - observed_at > MAX_INPUT_AGE
    ):
        raise WitnessLiveContractError("Witness live observation is not fresh for this journal")
    public_key = document.get("witness_public_key")
    if (
        not isinstance(public_key, str)
        or not witness_public_key_is_valid(public_key)
        or document.get("witness_public_key_sha256")
        != hashlib.sha256(public_key.encode("ascii")).hexdigest()
    ):
        raise WitnessLiveContractError("Witness live public key binding differs")
    proof = document.get("signed_proof")
    if not isinstance(proof, Mapping) or document.get("signed_proof_sha256") != _proof_sha256(proof):
        raise WitnessLiveContractError("Witness live signed proof binding differs")
    _nonzero_sha256(
        document.get("witness_status_receipt_sha256"), label="Witness status receipt"
    )
    try:
        validated = validate_witness_lease_proof(
            dict(proof),
            public_key_base64=public_key,
            expected_site=EXPECTED_HOLDER_SITE,
            expected_epoch=EXPECTED_WRITER_EPOCH,
            now=current,
            safety_margin_seconds=0,
            max_clock_skew_seconds=MAX_LEASE_CLOCK_SKEW_SECONDS,
            max_lifetime_seconds=MAX_LEASE_LIFETIME_SECONDS,
        ).canonical_payload
    except WitnessProofError as exc:
        raise WitnessLiveContractError("Witness live signature or lifetime differs") from exc
    if validated != dict(proof):
        raise WitnessLiveContractError("Witness live signed proof canonical form differs")
    expires_at = _timestamp(validated["expires_at"].replace("+00:00", "Z"), label="Witness lease expiry")
    if expires_at - current < MIN_REMAINING_LEASE:
        raise WitnessLiveContractError("Witness live lease lacks 90 seconds remaining")
    expected_readback = _lease_readback_digest(
        proof_sha256=document["signed_proof_sha256"],
        receipt_sha256=document["witness_status_receipt_sha256"],
        observed_at=document["observed_at"],
    )
    if document.get("lease_live_readback_sha256") != expected_readback:
        raise WitnessLiveContractError("Witness live readback digest differs")
    return document
