#!/usr/bin/env python3
"""Pure local contract for redacted two-sided DR TLS handshake evidence.

The contract performs no peer connection, filesystem access, or publication.
It accepts canonical redacted proofs from both sides of every directed runtime
peer handshake and reduces them to the gate-compatible ``dr_tls`` observation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Mapping, Sequence
from uuid import UUID


PROOF_SCHEMA = "production-shadow-convergence-dr-tls-peer-proof-v1"
OBSERVATION_SCHEMA = "production-shadow-convergence-dr-tls-observation-v1"
ROLES = ("bot_fi", "webapp_fi", "webapp_ir")
PAIRS = tuple((origin, destination) for origin in ROLES for destination in ROLES if origin != destination)
MAX_PROOF_BYTES = 64 * 1024
MAX_PROOF_AGE = timedelta(minutes=15)
MAX_PROOF_SKEW = timedelta(minutes=2)
MAX_FUTURE_SKEW = timedelta(seconds=5)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
ZERO_SHA256 = "0" * 64

IDENTITY_FIELDS = frozenset({"campaign_id", "operation_id", "release_sha", "release_tree_sha", "manifest_sha256", "plan_sha256", "approval_sha256"})
PROOF_FIELDS = frozenset({"schema", "status", *IDENTITY_FIELDS, "role", "origin_role", "destination_role", "observed_at", "protocol", "status_code", "certificate_sha256", "peer_handshake_sha256", "ca_bundle_sha256", "proof_sha256"})
PEER_FIELDS = frozenset({"origin_role", "destination_role", "protocol", "status_code", "certificate_sha256", "peer_handshake_sha256", "ca_bundle_sha256"})
OBSERVATION_FIELDS = frozenset({"schema", "status", *IDENTITY_FIELDS, "observed_at", "peers", "peer_set_sha256"})


class DrTlsContractError(ValueError):
    """A redacted two-sided DR TLS proof is not exact or fresh."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DrTlsContractError("JSON document has duplicate fields")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise DrTlsContractError("DR TLS value is not canonical JSON") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == ZERO_SHA256:
        raise DrTlsContractError(f"{label} is not a nonzero SHA-256")
    return value


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise DrTlsContractError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise DrTlsContractError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise DrTlsContractError(f"{label} is invalid")
    normalized = parsed.astimezone(timezone.utc)
    if normalized.isoformat().replace("+00:00", "Z") != value:
        raise DrTlsContractError(f"{label} is invalid")
    return normalized


def _timestamp_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DrTlsContractError("timestamp is invalid")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _identity_from_values(**value: Any) -> dict[str, str]:
    try:
        campaign = UUID(value["campaign_id"])
        operation = UUID(value["operation_id"])
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise DrTlsContractError("campaign or operation identity is invalid") from exc
    if str(campaign) != value["campaign_id"] or str(operation) != value["operation_id"] or campaign.int == 0 or operation.int == 0 or campaign == operation:
        raise DrTlsContractError("campaign or operation identity is invalid")
    for field in ("release_sha", "release_tree_sha"):
        if not isinstance(value.get(field), str) or SHA40_RE.fullmatch(value[field]) is None:
            raise DrTlsContractError(f"{field} is invalid")
    return {
        "campaign_id": value["campaign_id"], "operation_id": value["operation_id"],
        "release_sha": value["release_sha"], "release_tree_sha": value["release_tree_sha"],
        "manifest_sha256": _nonzero_sha256(value.get("manifest_sha256"), label="manifest_sha256"),
        "plan_sha256": _nonzero_sha256(value.get("plan_sha256"), label="plan_sha256"),
        "approval_sha256": _nonzero_sha256(value.get("approval_sha256"), label="approval_sha256"),
    }


def _identity(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != IDENTITY_FIELDS:
        raise DrTlsContractError("DR TLS identity fields differ")
    return _identity_from_values(**dict(value))


def _proof_digest(document: Mapping[str, Any]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "proof_sha256"})


def parse_proof_payload(payload: bytes) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_PROOF_BYTES:
        raise DrTlsContractError("DR TLS proof payload is invalid")
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DrTlsContractError("DR TLS proof payload is invalid") from exc
    if not isinstance(document, dict) or payload != _canonical_json(document) + b"\n":
        raise DrTlsContractError("DR TLS proof payload is not canonical")
    return document


def validate_proof(value: Any, *, identity: Mapping[str, Any], origin_role: str, destination_role: str, role: str, now: datetime) -> tuple[dict[str, Any], datetime]:
    expected_identity = _identity(identity)
    if (origin_role, destination_role) not in PAIRS or role not in {origin_role, destination_role}:
        raise DrTlsContractError("DR TLS proof peer identity is invalid")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise DrTlsContractError("validation time is invalid")
    current = now.astimezone(timezone.utc)
    if not isinstance(value, Mapping) or set(value) != PROOF_FIELDS:
        raise DrTlsContractError("DR TLS proof fields differ")
    document = dict(value)
    expected = {"schema": PROOF_SCHEMA, "status": "observed", **expected_identity, "role": role, "origin_role": origin_role, "destination_role": destination_role, "protocol": document.get("protocol"), "status_code": 200}
    if document.get("protocol") not in {"TLSv1.2", "TLSv1.3"} or any(document.get(key) != item for key, item in expected.items()):
        raise DrTlsContractError("DR TLS proof binding differs")
    observed_at = _timestamp(document.get("observed_at"), label="DR TLS proof observed_at")
    if observed_at > current + MAX_FUTURE_SKEW or current - observed_at > MAX_PROOF_AGE:
        raise DrTlsContractError("DR TLS proof is not fresh")
    for field in ("certificate_sha256", "peer_handshake_sha256", "ca_bundle_sha256", "proof_sha256"):
        _nonzero_sha256(document.get(field), label=f"DR TLS proof {field}")
    if document["proof_sha256"] != _proof_digest(document):
        raise DrTlsContractError("DR TLS proof digest differs")
    return document, observed_at


def build_observation(*, campaign_id: str, operation_id: str, release_sha: str, release_tree_sha: str, manifest_sha256: str, plan_sha256: str, approval_sha256: str, peer_proofs: Sequence[Mapping[str, Any]], now: datetime) -> dict[str, Any]:
    identity = _identity_from_values(campaign_id=campaign_id, operation_id=operation_id, release_sha=release_sha, release_tree_sha=release_tree_sha, manifest_sha256=manifest_sha256, plan_sha256=plan_sha256, approval_sha256=approval_sha256)
    if not isinstance(peer_proofs, Sequence) or isinstance(peer_proofs, (str, bytes)):
        raise DrTlsContractError("DR TLS proof collection is invalid")
    expected_keys = {(origin, destination, role) for origin, destination in PAIRS for role in (origin, destination)}
    if len(peer_proofs) != len(expected_keys):
        raise DrTlsContractError("DR TLS proof coverage is incomplete")
    proofs: dict[tuple[str, str, str], tuple[dict[str, Any], datetime]] = {}
    for value in peer_proofs:
        if not isinstance(value, Mapping):
            raise DrTlsContractError("DR TLS proof entry is invalid")
        origin, destination, role = value.get("origin_role"), value.get("destination_role"), value.get("role")
        if not all(isinstance(item, str) for item in (origin, destination, role)):
            raise DrTlsContractError("DR TLS proof peer identity is invalid")
        key = (origin, destination, role)
        if key not in expected_keys or key in proofs:
            raise DrTlsContractError("DR TLS proof coverage differs")
        proofs[key] = validate_proof(value, identity=identity, origin_role=origin, destination_role=destination, role=role, now=now)
    if set(proofs) != expected_keys:
        raise DrTlsContractError("DR TLS proof coverage differs")
    times = [item[1] for item in proofs.values()]
    if max(times) - min(times) > MAX_PROOF_SKEW:
        raise DrTlsContractError("DR TLS proof time skew is too large")
    peers: list[dict[str, Any]] = []
    for origin, destination in PAIRS:
        origin_proof, destination_proof = proofs[(origin, destination, origin)][0], proofs[(origin, destination, destination)][0]
        if any(origin_proof[field] != destination_proof[field] for field in ("protocol", "status_code", "certificate_sha256", "peer_handshake_sha256", "ca_bundle_sha256")):
            raise DrTlsContractError("DR TLS two-sided handshake differs")
        peers.append({field: origin_proof[field] for field in PEER_FIELDS})
    document: dict[str, Any] = {"schema": OBSERVATION_SCHEMA, "status": "observed", **identity, "observed_at": _timestamp_text(max(times)), "peers": peers, "peer_set_sha256": _sha256(peers)}
    return validate_observation(document, identity=identity, now=now)


def validate_observation(value: Any, *, identity: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    expected_identity = _identity(identity)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise DrTlsContractError("validation time is invalid")
    current = now.astimezone(timezone.utc)
    if not isinstance(value, Mapping) or set(value) != OBSERVATION_FIELDS:
        raise DrTlsContractError("DR TLS observation fields differ")
    document = dict(value)
    expected = {"schema": OBSERVATION_SCHEMA, "status": "observed", **expected_identity}
    if any(document.get(key) != item for key, item in expected.items()):
        raise DrTlsContractError("DR TLS observation binding differs")
    observed_at = _timestamp(document.get("observed_at"), label="DR TLS observation observed_at")
    if observed_at > current + MAX_FUTURE_SKEW or current - observed_at > MAX_PROOF_AGE:
        raise DrTlsContractError("DR TLS observation is not fresh")
    peers = document.get("peers")
    if not isinstance(peers, list) or len(peers) != len(PAIRS):
        raise DrTlsContractError("DR TLS peer coverage differs")
    seen: set[tuple[str, str]] = set()
    for peer in peers:
        if not isinstance(peer, Mapping) or set(peer) != PEER_FIELDS:
            raise DrTlsContractError("DR TLS peer fields differ")
        key = (peer.get("origin_role"), peer.get("destination_role"))
        if key not in PAIRS or key in seen or peer.get("protocol") not in {"TLSv1.2", "TLSv1.3"} or peer.get("status_code") != 200:
            raise DrTlsContractError("DR TLS peer identity differs")
        seen.add(key)
        for field in ("certificate_sha256", "peer_handshake_sha256", "ca_bundle_sha256"):
            _nonzero_sha256(peer.get(field), label=f"DR TLS peer {field}")
    if seen != set(PAIRS) or document.get("peer_set_sha256") != _sha256(peers):
        raise DrTlsContractError("DR TLS peer-set digest differs")
    return document
