#!/usr/bin/env python3
"""Pure input contract for a future independent firewall readback collector.

The convergence reducer accepts already-redacted role/provider proofs, but it
cannot establish where those proofs came from.  This module defines the
smallest bounded receipt a later *independent* provider-control-plane
collector must emit before it is reduced to that existing proof.  The receipt
is bound to the exact release and collector source closure and commits to the
provider readback without carrying provider identifiers, destination data,
URLs, credentials, or rule IDs.

This is deliberately only parsing and validation.  It does not contact a
provider, host, container runtime, Object Storage, or network; it does not
publish evidence or affect source-set or convergence-gate readiness.  A
future executor must prove its independent trust boundary and retain the raw
provider receipt outside this redacted contract.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any, Mapping

from scripts import production_shadow_destination_firewall_observation as FIREWALL
from scripts import production_shadow_queue_state_observation as IDENTITY


COLLECTOR_INPUT_SCHEMA = "production-shadow-destination-firewall-collector-input-v1"
COLLECTOR_STATUS = "observed-redacted"
COLLECTOR_ORIGIN = "independent-provider-control-plane-readback"
COLLECTOR_SCOPE = "operation-destination-allowlist"
MAX_COLLECTOR_INPUT_BYTES = 256 * 1024

COLLECTOR_INPUT_FIELDS = frozenset(
    {
        "schema",
        "status",
        *IDENTITY.IDENTITY_FIELDS,
        "phase_started_at",
        "collector_release_sha",
        "collector_release_tree_sha",
        "collector_source_manifest_sha256",
        "collector_origin",
        "scope",
        "role",
        "captured_at",
        "observed_at",
        "expected_allowlist",
        "observed_allowlist",
        "operation_rule_count",
        "unexpected_destination_count",
        "missing_destination_count",
        "forbidden_egress_count",
        "provider_readback_sha256",
        "collector_input_sha256",
    }
)


class DestinationFirewallCollectorContractError(ValueError):
    """A redacted independent provider receipt is incomplete or unbound."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise DestinationFirewallCollectorContractError(
                "firewall collector JSON has duplicate fields"
            )
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
        raise DestinationFirewallCollectorContractError(
            "firewall collector value is not canonical JSON"
        ) from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _hash(value: Any, *, label: str) -> str:
    try:
        return FIREWALL._hash(value, label=label)  # noqa: SLF001
    except FIREWALL.DestinationFirewallObservationError as exc:
        raise DestinationFirewallCollectorContractError(f"{label} is invalid") from exc


def _identity(value: Mapping[str, Any]) -> IDENTITY.QueueStateIdentity:
    try:
        return IDENTITY.validate_identity(value)
    except IDENTITY.QueueStateObservationError as exc:
        raise DestinationFirewallCollectorContractError(
            "firewall collector identity differs"
        ) from exc


def _input_digest(document: Mapping[str, Any]) -> str:
    return _sha256(
        {key: value for key, value in document.items() if key != "collector_input_sha256"}
    )


def parse_collector_input_payload(payload: bytes) -> dict[str, Any]:
    """Parse one bounded canonical ASCII receipt without opening any path."""

    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_COLLECTOR_INPUT_BYTES:
        raise DestinationFirewallCollectorContractError("firewall collector input size is invalid")
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DestinationFirewallCollectorContractError(
            "firewall collector input is not strict ASCII JSON"
        ) from exc
    if not isinstance(document, dict) or payload != _canonical_json(document) + b"\n":
        raise DestinationFirewallCollectorContractError(
            "firewall collector input is not canonical"
        )
    return document


def validate_collector_input(
    value: Any,
    *,
    identity: Mapping[str, Any],
    expected_allowlist: list[str],
    role: str,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate one exact-release receipt and reduce it to the legacy proof.

    The returned raw receipt contains only commitments.  ``role_proof`` is
    compatible with :mod:`production_shadow_destination_firewall_observation`
    but callers must retain and bind the raw receipt before any future gate
    integration.
    """

    bound = _identity(identity)
    if role not in FIREWALL.ROLES:
        raise DestinationFirewallCollectorContractError("firewall collector role is invalid")
    if now.tzinfo is None or now.utcoffset() is None:
        raise DestinationFirewallCollectorContractError("firewall collector clock lacks a timezone")
    if not isinstance(value, Mapping) or set(value) != COLLECTOR_INPUT_FIELDS:
        raise DestinationFirewallCollectorContractError("firewall collector input fields differ")
    document = dict(value)
    if (
        document.get("schema") != COLLECTOR_INPUT_SCHEMA
        or document.get("status") != COLLECTOR_STATUS
        or document.get("collector_origin") != COLLECTOR_ORIGIN
        or document.get("scope") != COLLECTOR_SCOPE
        or document.get("role") != role
        or document.get("collector_release_sha") != bound.release_sha
        or document.get("collector_release_tree_sha") != bound.release_tree_sha
        or document.get("phase_started_at")
        != bound.phase_started_at.isoformat().replace("+00:00", "Z")
        or any(document.get(key) != item for key, item in bound.fields().items())
    ):
        raise DestinationFirewallCollectorContractError("firewall collector input binding differs")
    _hash(
        document.get("collector_source_manifest_sha256"),
        label="firewall collector source manifest",
    )
    _hash(document.get("provider_readback_sha256"), label="firewall collector provider readback")
    if document.get("collector_input_sha256") != _input_digest(document):
        raise DestinationFirewallCollectorContractError("firewall collector input digest differs")

    # Delegate exact allowlist, zero-violation, and freshness checks to the
    # existing authoritative reducer so the two contracts cannot drift.
    role_proof: dict[str, Any] = {
        "schema": FIREWALL.FIREWALL_PROOF_SCHEMA,
        "status": "observed-redacted",
        **bound.fields(),
        "role": role,
        "captured_at": document["captured_at"],
        "observed_at": document["observed_at"],
        "expected_allowlist": document["expected_allowlist"],
        "observed_allowlist": document["observed_allowlist"],
        "operation_rule_count": document["operation_rule_count"],
        "unexpected_destination_count": document["unexpected_destination_count"],
        "missing_destination_count": document["missing_destination_count"],
        "forbidden_egress_count": document["forbidden_egress_count"],
        "readback_sha256": "0" * 64,
    }
    role_proof["readback_sha256"] = FIREWALL._proof_digest(role_proof)  # noqa: SLF001
    try:
        FIREWALL._validate_proof(  # noqa: SLF001
            role_proof,
            role=role,
            identity=bound,
            expected_allowlist=FIREWALL._allowlist(  # noqa: SLF001
                expected_allowlist, label=f"expected {role} allowlist"
            ),
            now=now,
        )
    except FIREWALL.DestinationFirewallObservationError as exc:
        raise DestinationFirewallCollectorContractError(
            "firewall collector input is not fresh or reducer-compatible"
        ) from exc
    return document, role_proof


def build_role_provider_proof(
    value: Any,
    *,
    identity: Mapping[str, Any],
    expected_allowlist: list[str],
    role: str,
    now: datetime,
) -> dict[str, Any]:
    """Return only the existing redacted role proof from a validated receipt."""

    _receipt, role_proof = validate_collector_input(
        value,
        identity=identity,
        expected_allowlist=expected_allowlist,
        role=role,
        now=now,
    )
    return role_proof
