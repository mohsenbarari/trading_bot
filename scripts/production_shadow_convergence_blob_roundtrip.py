#!/usr/bin/env python3
"""Pure local contract for redacted, bidirectional blob roundtrip evidence.

This module deliberately has no filesystem, Object Storage, SSH, Docker, or
network client.  A future role-local collector may supply canonical redacted
proof bytes from WebApp-FI and WebApp-IR; this contract only validates their
independent bindings and reduces the four local proofs to the observation
shape consumed by the convergence gate.  It does not publish that observation
or integrate it with a source set.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Mapping, Sequence
from uuid import UUID


ROLE_PROOF_SCHEMA = "production-shadow-convergence-blob-role-proof-v1"
OBSERVATION_SCHEMA = "production-shadow-convergence-blob-observation-v1"
ROLES = ("webapp_fi", "webapp_ir")
SCOPE = "webapp-authority"
PAIRS = (("webapp_fi", "webapp_ir"), ("webapp_ir", "webapp_fi"))
MAX_PROOF_BYTES = 64 * 1024
MAX_PROOF_AGE = timedelta(minutes=15)
MAX_PROOF_SKEW = timedelta(minutes=2)
MAX_FUTURE_SKEW = timedelta(seconds=5)
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
ROLE_PROOF_FIELDS = frozenset(
    {
        "schema",
        "status",
        *IDENTITY_FIELDS,
        "role",
        "scope",
        "source_site",
        "target_site",
        "observed_at",
        "object_storage_private",
        "object_storage_versioned",
        "local_object_set_sha256",
        "local_object_count",
        "local_keyring_sha256",
        "versioned_readback_set_sha256",
        "readback_sample_count",
        "missing_object_count",
        "corrupt_object_count",
        "proof_sha256",
    }
)
OBSERVATION_FIELDS = frozenset(
    {
        "schema",
        "status",
        *IDENTITY_FIELDS,
        "observed_at",
        "object_storage_versioning",
        "missing_object_count",
        "corrupt_object_count",
        "scopes",
        "blob_state_sha256",
    }
)
SCOPE_FIELDS = frozenset(
    {
        "scope",
        "source_site",
        "target_site",
        "source_set_sha256",
        "target_set_sha256",
        "source_object_count",
        "target_object_count",
        "readback_sample_count",
        "source_keyring_sha256",
        "target_keyring_sha256",
    }
)


class BlobRoundtripContractError(ValueError):
    """Independent blob evidence is absent, stale, or internally inconsistent."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BlobRoundtripContractError("JSON document has duplicate fields")
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
        raise BlobRoundtripContractError("blob evidence is not canonical JSON") from exc


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == ZERO_SHA256:
        raise BlobRoundtripContractError(f"{label} is not a nonzero SHA-256")
    return value


def _uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise BlobRoundtripContractError(f"{label} is invalid")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise BlobRoundtripContractError(f"{label} is invalid") from exc
    if str(parsed) != value or parsed.int == 0:
        raise BlobRoundtripContractError(f"{label} is invalid")
    return value


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise BlobRoundtripContractError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise BlobRoundtripContractError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise BlobRoundtripContractError(f"{label} is invalid")
    normalized = parsed.astimezone(timezone.utc)
    if normalized.isoformat().replace("+00:00", "Z") != value:
        raise BlobRoundtripContractError(f"{label} is invalid")
    return normalized


def _timestamp_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise BlobRoundtripContractError("timestamp is invalid")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _nonnegative(value: Any, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise BlobRoundtripContractError(f"{label} must be a non-negative integer")
    return value


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
        raise BlobRoundtripContractError("campaign_id and operation_id must differ")
    if not isinstance(release_sha, str) or SHA40_RE.fullmatch(release_sha) is None:
        raise BlobRoundtripContractError("release_sha is invalid")
    if not isinstance(release_tree_sha, str) or SHA40_RE.fullmatch(release_tree_sha) is None:
        raise BlobRoundtripContractError("release_tree_sha is invalid")
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
        raise BlobRoundtripContractError("blob evidence identity fields differ")
    return _identity(
        campaign_id=value["campaign_id"],
        operation_id=value["operation_id"],
        release_sha=value["release_sha"],
        release_tree_sha=value["release_tree_sha"],
        manifest_sha256=value["manifest_sha256"],
        plan_sha256=value["plan_sha256"],
        approval_sha256=value["approval_sha256"],
    )


def _proof_digest(document: Mapping[str, Any]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "proof_sha256"})


def _observation_digest(document: Mapping[str, Any]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "blob_state_sha256"})


def parse_role_proof_payload(payload: bytes) -> dict[str, Any]:
    """Decode one bounded canonical redacted proof without transport I/O."""

    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_PROOF_BYTES:
        raise BlobRoundtripContractError("blob role proof payload is invalid")
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BlobRoundtripContractError("blob role proof payload is invalid") from exc
    if not isinstance(document, dict) or payload != _canonical_json(document) + b"\n":
        raise BlobRoundtripContractError("blob role proof payload is not canonical")
    return document


def validate_role_proof(
    value: Any,
    *,
    identity: Mapping[str, Any],
    source_site: str,
    target_site: str,
    role: str,
    now: datetime,
) -> tuple[dict[str, Any], datetime]:
    """Validate one local proof for one side of a directed WebApp pair."""

    expected_identity = _identity_from_mapping(identity)
    if source_site not in ROLES or target_site not in ROLES or source_site == target_site:
        raise BlobRoundtripContractError("blob role proof pair is invalid")
    if role not in {source_site, target_site}:
        raise BlobRoundtripContractError("blob role proof role is invalid")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise BlobRoundtripContractError("validation time is invalid")
    current = now.astimezone(timezone.utc)
    if not isinstance(value, Mapping) or set(value) != ROLE_PROOF_FIELDS:
        raise BlobRoundtripContractError("blob role proof fields differ")
    document = dict(value)
    expected = {
        "schema": ROLE_PROOF_SCHEMA,
        "status": "observed",
        **expected_identity,
        "role": role,
        "scope": SCOPE,
        "source_site": source_site,
        "target_site": target_site,
        "object_storage_private": True,
        "object_storage_versioned": True,
        "missing_object_count": 0,
        "corrupt_object_count": 0,
    }
    if any(document.get(key) != item for key, item in expected.items()):
        raise BlobRoundtripContractError("blob role proof binding differs")
    observed_at = _timestamp(document.get("observed_at"), label="blob role proof observed_at")
    if observed_at > current + MAX_FUTURE_SKEW or current - observed_at > MAX_PROOF_AGE:
        raise BlobRoundtripContractError("blob role proof is not fresh")
    counts = {
        field: _nonnegative(document.get(field), label=f"blob role proof {field}")
        for field in ("local_object_count", "readback_sample_count")
    }
    if counts["readback_sample_count"] > counts["local_object_count"] or (
        counts["local_object_count"] > 0 and counts["readback_sample_count"] < 1
    ):
        raise BlobRoundtripContractError("blob role proof exact-version readback differs")
    for field in (
        "local_object_set_sha256",
        "local_keyring_sha256",
        "versioned_readback_set_sha256",
        "proof_sha256",
    ):
        _nonzero_sha256(document.get(field), label=f"blob role proof {field}")
    if document["proof_sha256"] != _proof_digest(document):
        raise BlobRoundtripContractError("blob role proof digest differs")
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
    role_proofs: Sequence[Mapping[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Cross-check four independent local proofs into one redacted observation."""

    identity = _identity(
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan_sha256,
        approval_sha256=approval_sha256,
    )
    if not isinstance(role_proofs, Sequence) or isinstance(role_proofs, (str, bytes)):
        raise BlobRoundtripContractError("blob role proof collection is invalid")
    expected_keys = {(source, target, role) for source, target in PAIRS for role in (source, target)}
    if len(role_proofs) != len(expected_keys):
        raise BlobRoundtripContractError("blob role proof coverage is incomplete")
    proofs: dict[tuple[str, str, str], tuple[dict[str, Any], datetime]] = {}
    for value in role_proofs:
        if not isinstance(value, Mapping):
            raise BlobRoundtripContractError("blob role proof entry is invalid")
        source = value.get("source_site")
        target = value.get("target_site")
        role = value.get("role")
        if not isinstance(source, str) or not isinstance(target, str) or not isinstance(role, str):
            raise BlobRoundtripContractError("blob role proof identity is invalid")
        key = (source, target, role)
        if key not in expected_keys or key in proofs:
            raise BlobRoundtripContractError("blob role proof coverage differs")
        proofs[key] = validate_role_proof(
            value,
            identity=identity,
            source_site=source,
            target_site=target,
            role=role,
            now=now,
        )
    if set(proofs) != expected_keys:
        raise BlobRoundtripContractError("blob role proof coverage differs")
    proof_times = [item[1] for item in proofs.values()]
    if max(proof_times) - min(proof_times) > MAX_PROOF_SKEW:
        raise BlobRoundtripContractError("blob role proof time skew is too large")

    scopes: list[dict[str, Any]] = []
    for source, target in PAIRS:
        source_proof = proofs[(source, target, source)][0]
        target_proof = proofs[(source, target, target)][0]
        equality_fields = (
            ("local_object_set_sha256", "local_object_set_sha256"),
            ("local_keyring_sha256", "local_keyring_sha256"),
            ("versioned_readback_set_sha256", "versioned_readback_set_sha256"),
            ("local_object_count", "local_object_count"),
            ("readback_sample_count", "readback_sample_count"),
        )
        if any(source_proof[left] != target_proof[right] for left, right in equality_fields):
            raise BlobRoundtripContractError("blob role proof readback or keyring differs")
        scopes.append(
            {
                "scope": SCOPE,
                "source_site": source,
                "target_site": target,
                "source_set_sha256": source_proof["local_object_set_sha256"],
                "target_set_sha256": target_proof["local_object_set_sha256"],
                "source_object_count": source_proof["local_object_count"],
                "target_object_count": target_proof["local_object_count"],
                "readback_sample_count": source_proof["readback_sample_count"],
                "source_keyring_sha256": source_proof["local_keyring_sha256"],
                "target_keyring_sha256": target_proof["local_keyring_sha256"],
            }
        )
    observed_at = max(proof_times)
    document: dict[str, Any] = {
        "schema": OBSERVATION_SCHEMA,
        "status": "observed",
        **identity,
        "observed_at": _timestamp_text(observed_at),
        "object_storage_versioning": True,
        "missing_object_count": 0,
        "corrupt_object_count": 0,
        "scopes": scopes,
        "blob_state_sha256": ZERO_SHA256,
    }
    document["blob_state_sha256"] = _observation_digest(document)
    return validate_observation(document, identity=identity, now=now)


def validate_observation(
    value: Any, *, identity: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    """Validate the future gate-compatible observation without publishing it."""

    expected_identity = _identity_from_mapping(identity)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise BlobRoundtripContractError("validation time is invalid")
    current = now.astimezone(timezone.utc)
    if not isinstance(value, Mapping) or set(value) != OBSERVATION_FIELDS:
        raise BlobRoundtripContractError("blob observation fields differ")
    document = dict(value)
    expected = {
        "schema": OBSERVATION_SCHEMA,
        "status": "observed",
        **expected_identity,
        "object_storage_versioning": True,
        "missing_object_count": 0,
        "corrupt_object_count": 0,
    }
    if any(document.get(key) != item for key, item in expected.items()):
        raise BlobRoundtripContractError("blob observation binding differs")
    observed_at = _timestamp(document.get("observed_at"), label="blob observation observed_at")
    if observed_at > current + MAX_FUTURE_SKEW or current - observed_at > MAX_PROOF_AGE:
        raise BlobRoundtripContractError("blob observation is not fresh")
    scopes = document.get("scopes")
    if not isinstance(scopes, list) or len(scopes) != len(PAIRS):
        raise BlobRoundtripContractError("blob observation scope coverage differs")
    seen: set[tuple[str, str, str]] = set()
    for scope in scopes:
        if not isinstance(scope, Mapping) or set(scope) != SCOPE_FIELDS:
            raise BlobRoundtripContractError("blob observation scope fields differ")
        key = (scope.get("scope"), scope.get("source_site"), scope.get("target_site"))
        if key not in {(SCOPE, source, target) for source, target in PAIRS} or key in seen:
            raise BlobRoundtripContractError("blob observation scope identity differs")
        seen.add(key)
        source_count = _nonnegative(scope.get("source_object_count"), label="blob source object count")
        target_count = _nonnegative(scope.get("target_object_count"), label="blob target object count")
        samples = _nonnegative(scope.get("readback_sample_count"), label="blob readback sample count")
        for field in (
            "source_set_sha256",
            "target_set_sha256",
            "source_keyring_sha256",
            "target_keyring_sha256",
        ):
            _nonzero_sha256(scope.get(field), label=f"blob scope {field}")
        if (
            scope["source_set_sha256"] != scope["target_set_sha256"]
            or scope["source_keyring_sha256"] != scope["target_keyring_sha256"]
            or source_count != target_count
            or samples > source_count
            or (source_count > 0 and samples < 1)
        ):
            raise BlobRoundtripContractError("blob observation versioned readback or keyring differs")
    if seen != {(SCOPE, source, target) for source, target in PAIRS}:
        raise BlobRoundtripContractError("blob observation scope coverage differs")
    if document.get("blob_state_sha256") != _observation_digest(document):
        raise BlobRoundtripContractError("blob observation digest differs")
    return document
