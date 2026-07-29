#!/usr/bin/env python3
"""Pure contract for a future exact-release DR TLS peer collector.

This module deliberately does not open a socket, read a path, invoke Docker,
or publish evidence.  It binds one redacted local peer-handshake receipt to a
static exact-release plan, then reduces it to the existing two-sided DR TLS
proof contract.  A later collector must keep endpoint, SNI, CA material,
credentials, and raw certificate/handshake data outside this interface.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Mapping

from scripts import production_shadow_convergence_dr_tls as DR_TLS


PLAN_SCHEMA = "production-shadow-convergence-dr-tls-collector-plan-v1"
OUTPUT_SCHEMA = "production-shadow-convergence-dr-tls-collector-output-v1"
COLLECTOR_ENTRYPOINT = "scripts/collect_production_shadow_convergence_dr_tls_peer.py"
MAX_OUTPUT_BYTES = DR_TLS.MAX_PROOF_BYTES
MAX_SOURCE_AGE = DR_TLS.MAX_PROOF_AGE
MAX_FUTURE_SKEW = DR_TLS.MAX_FUTURE_SKEW
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

PLAN_FIELDS = frozenset(
    {
        "schema", "status", *DR_TLS.IDENTITY_FIELDS, "phase_started_at",
        "role", "origin_role", "destination_role",
        "runtime_target_binding_sha256", "app_image_id", "collector_entrypoint",
        "collector_source_manifest_sha256", "network_policy_sha256",
        "collector_plan_sha256",
    }
)
OUTPUT_FIELDS = frozenset(
    {
        "schema", "status", *DR_TLS.IDENTITY_FIELDS, "role", "origin_role",
        "destination_role", "collector_plan_sha256", "captured_at", "observed_at",
        "protocol", "status_code", "certificate_sha256", "peer_handshake_sha256",
        "ca_bundle_sha256", "collector_output_sha256",
    }
)


class DrTlsCollectorContractError(ValueError):
    """A future DR TLS collector plan or redacted output is unsafe."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DrTlsCollectorContractError("DR TLS collector JSON has duplicate fields")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise DrTlsCollectorContractError("DR TLS collector value is not canonical JSON") from exc


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == DR_TLS.ZERO_SHA256:
        raise DrTlsCollectorContractError(f"{label} is invalid")
    return value


def _timestamp(value: Any, *, label: str) -> datetime:
    try:
        return DR_TLS._timestamp(value, label=label)  # noqa: SLF001
    except DR_TLS.DrTlsContractError as exc:
        raise DrTlsCollectorContractError(f"{label} is invalid") from exc


def _timestamp_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DrTlsCollectorContractError("collector timestamp is invalid")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _identity(value: Mapping[str, Any]) -> dict[str, str]:
    try:
        return DR_TLS._identity_from_values(**dict(value))  # noqa: SLF001
    except (DR_TLS.DrTlsContractError, TypeError) as exc:
        raise DrTlsCollectorContractError("DR TLS collector identity differs") from exc


def _plan_digest(document: Mapping[str, Any]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "collector_plan_sha256"})


def _output_digest(document: Mapping[str, Any]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "collector_output_sha256"})


def build_plan(
    *,
    identity: Mapping[str, Any],
    phase_started_at: datetime,
    role: str,
    origin_role: str,
    destination_role: str,
    runtime_target_binding_sha256: str,
    app_image_id: str,
    collector_source_manifest_sha256: str,
    network_policy_sha256: str,
) -> dict[str, Any]:
    """Build a static plan without installing or executing a collector."""

    checked_identity = _identity(identity)
    if (origin_role, destination_role) not in DR_TLS.PAIRS or role not in {origin_role, destination_role}:
        raise DrTlsCollectorContractError("DR TLS collector peer identity is invalid")
    if not isinstance(app_image_id, str) or IMAGE_ID_RE.fullmatch(app_image_id) is None:
        raise DrTlsCollectorContractError("DR TLS collector app image is invalid")
    phase_started = _timestamp_text(phase_started_at)
    document: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "status": "planned-only",
        **checked_identity,
        "phase_started_at": phase_started,
        "role": role,
        "origin_role": origin_role,
        "destination_role": destination_role,
        "runtime_target_binding_sha256": _digest(runtime_target_binding_sha256, label="DR TLS collector runtime binding"),
        "app_image_id": app_image_id,
        "collector_entrypoint": COLLECTOR_ENTRYPOINT,
        "collector_source_manifest_sha256": _digest(collector_source_manifest_sha256, label="DR TLS collector source manifest"),
        "network_policy_sha256": _digest(network_policy_sha256, label="DR TLS collector network policy"),
        "collector_plan_sha256": DR_TLS.ZERO_SHA256,
    }
    document["collector_plan_sha256"] = _plan_digest(document)
    return document


def validate_plan(value: Any, *, identity: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != PLAN_FIELDS:
        raise DrTlsCollectorContractError("DR TLS collector plan fields differ")
    document = dict(value)
    expected_identity = _identity(identity)
    actual_identity = _identity({key: document.get(key) for key in DR_TLS.IDENTITY_FIELDS})
    if actual_identity != expected_identity:
        raise DrTlsCollectorContractError("DR TLS collector plan identity differs")
    origin, destination, role = document.get("origin_role"), document.get("destination_role"), document.get("role")
    if (
        document.get("schema") != PLAN_SCHEMA
        or document.get("status") != "planned-only"
        or (origin, destination) not in DR_TLS.PAIRS
        or role not in {origin, destination}
        or document.get("collector_entrypoint") != COLLECTOR_ENTRYPOINT
        or not isinstance(document.get("app_image_id"), str)
        or IMAGE_ID_RE.fullmatch(document["app_image_id"]) is None
    ):
        raise DrTlsCollectorContractError("DR TLS collector plan differs")
    _timestamp(document.get("phase_started_at"), label="DR TLS collector phase_started_at")
    for field in ("runtime_target_binding_sha256", "collector_source_manifest_sha256", "network_policy_sha256"):
        _digest(document.get(field), label=f"DR TLS collector {field}")
    if _digest(document.get("collector_plan_sha256"), label="DR TLS collector plan") != _plan_digest(document):
        raise DrTlsCollectorContractError("DR TLS collector plan digest differs")
    return document


def parse_collector_output(payload: bytes) -> dict[str, Any]:
    """Parse one bounded canonical redacted output, without any I/O."""

    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_OUTPUT_BYTES:
        raise DrTlsCollectorContractError("DR TLS collector output payload is invalid")
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DrTlsCollectorContractError("DR TLS collector output payload is invalid") from exc
    if not isinstance(document, dict) or payload != _canonical_json(document) + b"\n":
        raise DrTlsCollectorContractError("DR TLS collector output payload is not canonical")
    return document


def validate_collector_output(value: Any, *, plan: Mapping[str, Any], identity: Mapping[str, Any], now: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate redacted output and return its gate-compatible peer proof."""

    checked_plan = validate_plan(plan, identity=identity)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise DrTlsCollectorContractError("DR TLS collector validation time is invalid")
    current = now.astimezone(timezone.utc)
    if not isinstance(value, Mapping) or set(value) != OUTPUT_FIELDS:
        raise DrTlsCollectorContractError("DR TLS collector output fields differ")
    document = dict(value)
    required = {key: checked_plan[key] for key in DR_TLS.IDENTITY_FIELDS}
    required.update({key: checked_plan[key] for key in ("role", "origin_role", "destination_role", "collector_plan_sha256")})
    if document.get("schema") != OUTPUT_SCHEMA or document.get("status") != "observed-redacted" or any(document.get(key) != item for key, item in required.items()):
        raise DrTlsCollectorContractError("DR TLS collector output binding differs")
    captured = _timestamp(document.get("captured_at"), label="DR TLS collector captured_at")
    observed = _timestamp(document.get("observed_at"), label="DR TLS collector observed_at")
    phase_started = _timestamp(checked_plan["phase_started_at"], label="DR TLS collector phase_started_at")
    if (
        captured < phase_started or observed < captured or captured > current + MAX_FUTURE_SKEW
        or observed > current + MAX_FUTURE_SKEW or current - captured > MAX_SOURCE_AGE
        or current - observed > MAX_SOURCE_AGE
    ):
        raise DrTlsCollectorContractError("DR TLS collector output freshness differs")
    if document.get("protocol") not in {"TLSv1.2", "TLSv1.3"} or document.get("status_code") != 200:
        raise DrTlsCollectorContractError("DR TLS collector handshake differs")
    for field in ("certificate_sha256", "peer_handshake_sha256", "ca_bundle_sha256"):
        _digest(document.get(field), label=f"DR TLS collector {field}")
    if _digest(document.get("collector_output_sha256"), label="DR TLS collector output") != _output_digest(document):
        raise DrTlsCollectorContractError("DR TLS collector output digest differs")
    proof: dict[str, Any] = {
        "schema": DR_TLS.PROOF_SCHEMA,
        "status": "observed",
        **required,
        "observed_at": document["observed_at"],
        "protocol": document["protocol"],
        "status_code": 200,
        "certificate_sha256": document["certificate_sha256"],
        "peer_handshake_sha256": document["peer_handshake_sha256"],
        "ca_bundle_sha256": document["ca_bundle_sha256"],
        "proof_sha256": DR_TLS.ZERO_SHA256,
    }
    proof.pop("collector_plan_sha256")
    proof["proof_sha256"] = DR_TLS._proof_digest(proof)  # noqa: SLF001
    try:
        DR_TLS.validate_proof(
            proof,
            identity={key: checked_plan[key] for key in DR_TLS.IDENTITY_FIELDS},
            origin_role=checked_plan["origin_role"],
            destination_role=checked_plan["destination_role"],
            role=checked_plan["role"],
            now=current,
        )
    except DR_TLS.DrTlsContractError as exc:
        raise DrTlsCollectorContractError("DR TLS collector output is not gate-compatible") from exc
    return document, proof


def reduce_to_peer_proof(value: Any, *, plan: Mapping[str, Any], identity: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """Reduce a checked receipt; this never publishes or enables readiness."""

    _document, proof = validate_collector_output(value, plan=plan, identity=identity, now=now)
    return proof
