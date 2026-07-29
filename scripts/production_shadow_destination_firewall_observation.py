#!/usr/bin/env python3
"""Pure reducer for redacted operation destination-allowlist proofs.

The module never contacts a firewall provider, host, Docker, or network. It
reduces exact role/provider proofs whose destination entries are SHA-256
tokens, not addresses, ports, host names, rule IDs, or provider resources.
Publication and convergence source-set integration deliberately remain outside
this contract.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping, Sequence

from scripts import production_shadow_queue_state_observation as IDENTITY


FIREWALL_PROOF_SCHEMA = "production-shadow-destination-firewall-proof-v1"
FIREWALL_OBSERVATION_SCHEMA = "production-shadow-convergence-firewall-observation-v1"
ALLOWLIST_PROOF_SCHEMA = "production-shadow-destination-firewall-allowlist-v1"
ROLES = ("bot_fi", "webapp_fi", "webapp_ir", "witness")
PROOF_FIELDS = frozenset(
    {
        "schema",
        "status",
        *IDENTITY.IDENTITY_FIELDS,
        "role",
        "captured_at",
        "observed_at",
        "expected_allowlist",
        "observed_allowlist",
        "operation_rule_count",
        "unexpected_destination_count",
        "missing_destination_count",
        "forbidden_egress_count",
        "readback_sha256",
    }
)
OBSERVATION_FIELDS = frozenset(
    {
        "schema",
        "status",
        *IDENTITY.IDENTITY_FIELDS,
        "observed_at",
        "roles",
        "allowlist_set_sha256",
    }
)
ROW_FIELDS = frozenset(
    {
        "expected_allowlist_sha256",
        "observed_allowlist_sha256",
        "operation_rule_count",
        "unexpected_destination_count",
        "missing_destination_count",
        "forbidden_egress_count",
        "readback_sha256",
    }
)


class DestinationFirewallObservationError(ValueError):
    """A redacted destination-allowlist proof is incomplete or unsafe."""


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
        raise DestinationFirewallObservationError("firewall value is not canonical JSON") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _hash(value: Any, *, label: str) -> str:
    try:
        return IDENTITY._sha256_nonzero(value, label=label)  # noqa: SLF001
    except IDENTITY.QueueStateObservationError as exc:
        raise DestinationFirewallObservationError(f"{label} is invalid") from exc


def _timestamp(value: Any, *, label: str) -> datetime:
    try:
        return IDENTITY._timestamp(value, label=label)  # noqa: SLF001
    except IDENTITY.QueueStateObservationError as exc:
        raise DestinationFirewallObservationError(f"{label} is invalid") from exc


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _nonnegative(value: Any, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise DestinationFirewallObservationError(f"{label} is invalid")
    return value


def _allowlist(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise DestinationFirewallObservationError(f"{label} is invalid")
    entries = [_hash(item, label=f"{label} entry") for item in value]
    if entries != sorted(entries) or len(set(entries)) != len(entries):
        raise DestinationFirewallObservationError(f"{label} is not canonical")
    return entries


def _allowlist_digest(entries: Sequence[str]) -> str:
    return _sha256({"schema": ALLOWLIST_PROOF_SCHEMA, "destinations": list(entries)})


def _proof_digest(document: Mapping[str, Any]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "readback_sha256"})


def _validate_expected_allowlists(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, Mapping) or set(value) != set(ROLES):
        raise DestinationFirewallObservationError("expected firewall allowlist roles differ")
    return {role: _allowlist(value[role], label=f"expected {role} allowlist") for role in ROLES}


def _validate_proof(
    value: Any,
    *,
    role: str,
    identity: IDENTITY.QueueStateIdentity,
    expected_allowlist: Sequence[str],
    now: datetime,
) -> tuple[dict[str, Any], datetime]:
    if not isinstance(value, Mapping) or set(value) != PROOF_FIELDS:
        raise DestinationFirewallObservationError(f"{role} firewall proof fields differ")
    document = dict(value)
    if (
        document.get("schema") != FIREWALL_PROOF_SCHEMA
        or document.get("status") != "observed-redacted"
        or document.get("role") != role
        or any(document.get(key) != item for key, item in identity.fields().items())
    ):
        raise DestinationFirewallObservationError(f"{role} firewall proof identity differs")
    expected = _allowlist(document.get("expected_allowlist"), label=f"{role} expected allowlist")
    observed = _allowlist(document.get("observed_allowlist"), label=f"{role} observed allowlist")
    if expected != list(expected_allowlist) or observed != expected:
        raise DestinationFirewallObservationError(f"{role} firewall allowlist differs")
    if _nonnegative(document.get("operation_rule_count"), label=f"{role} rule count") != len(expected):
        raise DestinationFirewallObservationError(f"{role} firewall rule count differs")
    if any(
        _nonnegative(document.get(field), label=f"{role} {field}") != 0
        for field in (
            "unexpected_destination_count",
            "missing_destination_count",
            "forbidden_egress_count",
        )
    ):
        raise DestinationFirewallObservationError(f"{role} firewall reports a violation")
    captured = _timestamp(document.get("captured_at"), label=f"{role} captured_at")
    observed_at = _timestamp(document.get("observed_at"), label=f"{role} observed_at")
    if (
        captured < identity.phase_started_at
        or observed_at < captured
        or captured > now + IDENTITY.MAX_FUTURE_SKEW
        or observed_at > now + IDENTITY.MAX_FUTURE_SKEW
        or now - captured > IDENTITY.MAX_SOURCE_AGE
        or now - observed_at > IDENTITY.MAX_SOURCE_AGE
        or observed_at - captured > IDENTITY.MAX_SOURCE_SKEW
    ):
        raise DestinationFirewallObservationError(f"{role} firewall proof freshness differs")
    if _hash(document.get("readback_sha256"), label=f"{role} readback") != _proof_digest(document):
        raise DestinationFirewallObservationError(f"{role} firewall proof digest differs")
    return document, observed_at


def _reduced_rows(
    proofs: Mapping[str, Mapping[str, Any]],
    *,
    identity: IDENTITY.QueueStateIdentity,
    expected_allowlists: Mapping[str, Sequence[str]],
    now: datetime,
) -> tuple[dict[str, dict[str, Any]], list[datetime]]:
    if not isinstance(proofs, Mapping) or set(proofs) != set(ROLES):
        raise DestinationFirewallObservationError("firewall proof role set differs")
    rows: dict[str, dict[str, Any]] = {}
    observed_times: list[datetime] = []
    for role in ROLES:
        proof, observed = _validate_proof(
            proofs[role],
            role=role,
            identity=identity,
            expected_allowlist=expected_allowlists[role],
            now=now,
        )
        expected_digest = _allowlist_digest(expected_allowlists[role])
        rows[role] = {
            "expected_allowlist_sha256": expected_digest,
            "observed_allowlist_sha256": expected_digest,
            "operation_rule_count": len(expected_allowlists[role]),
            "unexpected_destination_count": 0,
            "missing_destination_count": 0,
            "forbidden_egress_count": 0,
            "readback_sha256": proof["readback_sha256"],
        }
        observed_times.append(observed)
    if max(observed_times) - min(observed_times) > IDENTITY.MAX_SOURCE_SKEW:
        raise DestinationFirewallObservationError("firewall proof skew differs")
    return rows, observed_times


def build_destination_firewall_observation(
    *,
    identity: Mapping[str, Any],
    expected_allowlists: Mapping[str, Sequence[str]],
    role_provider_proofs: Mapping[str, Mapping[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Reduce exact redacted allowlist proofs to the gate observation schema."""

    try:
        bound = IDENTITY.validate_identity(identity)
    except IDENTITY.QueueStateObservationError as exc:
        raise DestinationFirewallObservationError("firewall identity differs") from exc
    if now.tzinfo is None or now.utcoffset() is None:
        raise DestinationFirewallObservationError("firewall clock lacks a timezone")
    current = now.astimezone(timezone.utc)
    expected = _validate_expected_allowlists(expected_allowlists)
    rows, observed_times = _reduced_rows(
        role_provider_proofs,
        identity=bound,
        expected_allowlists=expected,
        now=current,
    )
    document: dict[str, Any] = {
        "schema": FIREWALL_OBSERVATION_SCHEMA,
        "status": "observed",
        **bound.fields(),
        "observed_at": _timestamp_text(max(observed_times)),
        "roles": rows,
        "allowlist_set_sha256": _sha256(rows),
    }
    return validate_destination_firewall_observation(
        document,
        identity=bound,
        expected_allowlists=expected,
        role_provider_proofs=role_provider_proofs,
        now=current,
    )


def validate_destination_firewall_observation(
    value: Any,
    *,
    identity: IDENTITY.QueueStateIdentity | Mapping[str, Any],
    expected_allowlists: Mapping[str, Sequence[str]],
    role_provider_proofs: Mapping[str, Mapping[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Recompute the reduced proof closure without publishing or I/O."""

    try:
        bound = identity if isinstance(identity, IDENTITY.QueueStateIdentity) else IDENTITY.validate_identity(identity)
    except IDENTITY.QueueStateObservationError as exc:
        raise DestinationFirewallObservationError("firewall identity differs") from exc
    if now.tzinfo is None or now.utcoffset() is None:
        raise DestinationFirewallObservationError("firewall clock lacks a timezone")
    if not isinstance(value, Mapping) or set(value) != OBSERVATION_FIELDS:
        raise DestinationFirewallObservationError("destination firewall observation fields differ")
    document = dict(value)
    if (
        document.get("schema") != FIREWALL_OBSERVATION_SCHEMA
        or document.get("status") != "observed"
        or any(document.get(key) != item for key, item in bound.fields().items())
    ):
        raise DestinationFirewallObservationError("destination firewall observation identity differs")
    current = now.astimezone(timezone.utc)
    observed = _timestamp(document.get("observed_at"), label="firewall observed_at")
    expected = _validate_expected_allowlists(expected_allowlists)
    rows, observed_times = _reduced_rows(
        role_provider_proofs,
        identity=bound,
        expected_allowlists=expected,
        now=current,
    )
    if (
        observed != max(observed_times)
        or observed < bound.phase_started_at
        or observed > current + IDENTITY.MAX_FUTURE_SKEW
        or current - observed > IDENTITY.MAX_SOURCE_AGE
        or document.get("roles") != rows
        or document.get("allowlist_set_sha256") != _sha256(rows)
    ):
        raise DestinationFirewallObservationError("destination firewall observation binding differs")
    return document


def validate_published_destination_firewall_observation(
    value: Any,
    *,
    identity: IDENTITY.QueueStateIdentity | Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Validate the redacted reduced record available to the source-set layer."""

    try:
        bound = identity if isinstance(identity, IDENTITY.QueueStateIdentity) else IDENTITY.validate_identity(identity)
    except IDENTITY.QueueStateObservationError as exc:
        raise DestinationFirewallObservationError("firewall identity differs") from exc
    if now.tzinfo is None or now.utcoffset() is None:
        raise DestinationFirewallObservationError("firewall clock lacks a timezone")
    if not isinstance(value, Mapping) or set(value) != OBSERVATION_FIELDS:
        raise DestinationFirewallObservationError("destination firewall observation fields differ")
    document = dict(value)
    if (
        document.get("schema") != FIREWALL_OBSERVATION_SCHEMA
        or document.get("status") != "observed"
        or any(document.get(key) != item for key, item in bound.fields().items())
    ):
        raise DestinationFirewallObservationError("destination firewall observation identity differs")
    observed = _timestamp(document.get("observed_at"), label="firewall observed_at")
    current = now.astimezone(timezone.utc)
    if (
        observed < bound.phase_started_at
        or observed > current + IDENTITY.MAX_FUTURE_SKEW
        or current - observed > IDENTITY.MAX_SOURCE_AGE
        or not isinstance(document.get("roles"), Mapping)
        or set(document["roles"]) != set(ROLES)
    ):
        raise DestinationFirewallObservationError("destination firewall observation freshness or roles differ")
    rows: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        row = document["roles"][role]
        if not isinstance(row, Mapping) or set(row) != ROW_FIELDS:
            raise DestinationFirewallObservationError(f"destination firewall {role} fields differ")
        checked = dict(row)
        for field in ("expected_allowlist_sha256", "observed_allowlist_sha256", "readback_sha256"):
            _hash(checked.get(field), label=f"destination firewall {role} {field}")
        if (
            checked["expected_allowlist_sha256"] != checked["observed_allowlist_sha256"]
            or _nonnegative(checked.get("operation_rule_count"), label=f"destination firewall {role} rule count") < 1
            or any(
                _nonnegative(checked.get(field), label=f"destination firewall {role} {field}") != 0
                for field in (
                    "unexpected_destination_count",
                    "missing_destination_count",
                    "forbidden_egress_count",
                )
            )
        ):
            raise DestinationFirewallObservationError(f"destination firewall {role} differs")
        rows[role] = checked
    if document.get("allowlist_set_sha256") != _sha256(rows):
        raise DestinationFirewallObservationError("destination firewall set digest differs")
    return document
