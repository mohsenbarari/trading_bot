#!/usr/bin/env python3
"""Pure input contract for a future role-local blob roundtrip collector.

The collector which talks to Object Storage is intentionally not implemented
here.  It must reduce its private provider responses to this bounded,
redacted input before calling :func:`build_role_proof`.  Each entry commits to
one object identity and proves that the source-returned VersionId equals both
the target HEAD and target GET VersionId, while the payload digest agrees.
Object keys, bucket names, raw VersionIds, URLs, credentials, and payload
bytes are forbidden from this contract.

This module performs no filesystem, network, Object Storage, SSH, Docker, or
subprocess operation.  It cannot publish evidence or make a source set or
convergence gate ready.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any, Mapping, Sequence

from scripts import production_shadow_convergence_blob_roundtrip as BLOB


COLLECTOR_INPUT_SCHEMA = "production-shadow-convergence-blob-collector-input-v1"
COLLECTOR_STATUS = "observed-redacted"
TRANSPORT = "private-versioned-object-storage"
MAX_ENTRIES = 256

COLLECTOR_INPUT_FIELDS = frozenset(
    {
        "schema",
        "status",
        *BLOB.IDENTITY_FIELDS,
        "collector_release_sha",
        "collector_release_tree_sha",
        "role",
        "scope",
        "source_site",
        "target_site",
        "observed_at",
        "transport",
        "object_storage_private",
        "object_storage_versioned",
        "keyring_sha256",
        "entries",
        "collector_input_sha256",
    }
)
ENTRY_FIELDS = frozenset(
    {
        "object_commitment_sha256",
        "source_version_id_sha256",
        "target_head_version_id_sha256",
        "target_get_version_id_sha256",
        "source_payload_sha256",
        "target_payload_sha256",
    }
)


class BlobRoundtripCollectorContractError(ValueError):
    """A future collector input is incomplete, non-redacted, or unbound."""


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
        raise BlobRoundtripCollectorContractError("blob collector value is not canonical JSON") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BlobRoundtripCollectorContractError("blob collector JSON has duplicate fields")
        result[key] = value
    return result


def _digest(value: Any, *, label: str) -> str:
    try:
        return BLOB._nonzero_sha256(value, label=label)  # noqa: SLF001
    except BLOB.BlobRoundtripContractError as exc:
        raise BlobRoundtripCollectorContractError(f"{label} is invalid") from exc


def _identity(value: Mapping[str, Any]) -> dict[str, str]:
    try:
        return BLOB._identity_from_mapping(value)  # noqa: SLF001
    except BLOB.BlobRoundtripContractError as exc:
        raise BlobRoundtripCollectorContractError("blob collector identity differs") from exc


def _input_digest(document: Mapping[str, Any]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "collector_input_sha256"})


def parse_collector_input_payload(payload: bytes) -> dict[str, Any]:
    """Decode one bounded canonical redacted collector input without I/O."""

    if not isinstance(payload, bytes) or not 1 <= len(payload) <= BLOB.MAX_PROOF_BYTES:
        raise BlobRoundtripCollectorContractError("blob collector input payload is invalid")
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BlobRoundtripCollectorContractError("blob collector input payload is invalid") from exc
    if not isinstance(document, dict) or payload != _canonical_json(document) + b"\n":
        raise BlobRoundtripCollectorContractError("blob collector input payload is not canonical")
    return document


def _validated_entries(value: Any) -> tuple[list[dict[str, str]], str, str]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_ENTRIES:
        raise BlobRoundtripCollectorContractError("blob collector entries are incomplete")
    entries: list[dict[str, str]] = []
    previous = ""
    for item in value:
        if not isinstance(item, Mapping) or set(item) != ENTRY_FIELDS:
            raise BlobRoundtripCollectorContractError("blob collector entry fields differ")
        entry = {field: _digest(item.get(field), label=f"blob collector {field}") for field in ENTRY_FIELDS}
        commitment = entry["object_commitment_sha256"]
        if commitment <= previous:
            raise BlobRoundtripCollectorContractError("blob collector entries are not uniquely ordered")
        previous = commitment
        if (
            entry["source_version_id_sha256"] != entry["target_head_version_id_sha256"]
            or entry["source_version_id_sha256"] != entry["target_get_version_id_sha256"]
        ):
            raise BlobRoundtripCollectorContractError("blob collector exact VersionId readback differs")
        if entry["source_payload_sha256"] != entry["target_payload_sha256"]:
            raise BlobRoundtripCollectorContractError("blob collector payload readback differs")
        entries.append(entry)
    # Include the VersionId and payload commitments in both set digests.  A
    # future collector cannot substitute a matching object list with another
    # version or ciphertext while preserving either digest.
    object_set = _sha256(
        [
            {
                "object_commitment_sha256": entry["object_commitment_sha256"],
                "source_version_id_sha256": entry["source_version_id_sha256"],
                "source_payload_sha256": entry["source_payload_sha256"],
            }
            for entry in entries
        ]
    )
    readback_set = _sha256(
        [
            {
                "object_commitment_sha256": entry["object_commitment_sha256"],
                "target_head_version_id_sha256": entry["target_head_version_id_sha256"],
                "target_get_version_id_sha256": entry["target_get_version_id_sha256"],
                "target_payload_sha256": entry["target_payload_sha256"],
            }
            for entry in entries
        ]
    )
    return entries, object_set, readback_set


def validate_collector_input(
    value: Any,
    *,
    identity: Mapping[str, Any],
    source_site: str,
    target_site: str,
    role: str,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate redacted evidence from one exact-release role-local collector."""

    expected_identity = _identity(identity)
    if source_site not in BLOB.ROLES or target_site not in BLOB.ROLES or source_site == target_site:
        raise BlobRoundtripCollectorContractError("blob collector pair is invalid")
    if role not in {source_site, target_site}:
        raise BlobRoundtripCollectorContractError("blob collector role is invalid")
    if not isinstance(value, Mapping) or set(value) != COLLECTOR_INPUT_FIELDS:
        raise BlobRoundtripCollectorContractError("blob collector input fields differ")
    document = dict(value)
    expected = {
        "schema": COLLECTOR_INPUT_SCHEMA,
        "status": COLLECTOR_STATUS,
        **expected_identity,
        "collector_release_sha": expected_identity["release_sha"],
        "collector_release_tree_sha": expected_identity["release_tree_sha"],
        "role": role,
        "scope": BLOB.SCOPE,
        "source_site": source_site,
        "target_site": target_site,
        "transport": TRANSPORT,
        "object_storage_private": True,
        "object_storage_versioned": True,
    }
    if any(document.get(key) != item for key, item in expected.items()):
        raise BlobRoundtripCollectorContractError("blob collector input binding differs")
    try:
        BLOB._timestamp(document.get("observed_at"), label="blob collector observed_at")  # noqa: SLF001
    except BLOB.BlobRoundtripContractError as exc:
        raise BlobRoundtripCollectorContractError("blob collector observed_at is invalid") from exc
    # Delegate the authoritative freshness bound to the gate-compatible proof
    # validator below, so collector and reducer cannot drift.
    keyring = _digest(document.get("keyring_sha256"), label="blob collector keyring")
    entries, object_set, readback_set = _validated_entries(document.get("entries"))
    if document.get("collector_input_sha256") != _input_digest(document):
        raise BlobRoundtripCollectorContractError("blob collector input digest differs")
    proof = {
        "schema": BLOB.ROLE_PROOF_SCHEMA,
        "status": "observed",
        **expected_identity,
        "role": role,
        "scope": BLOB.SCOPE,
        "source_site": source_site,
        "target_site": target_site,
        "observed_at": document["observed_at"],
        "object_storage_private": True,
        "object_storage_versioned": True,
        "local_object_set_sha256": object_set,
        "local_object_count": len(entries),
        "local_keyring_sha256": keyring,
        "versioned_readback_set_sha256": readback_set,
        # Every committed object has HEAD and GET verification; sampling is
        # intentionally forbidden for this future real collector boundary.
        "readback_sample_count": len(entries),
        "missing_object_count": 0,
        "corrupt_object_count": 0,
        "proof_sha256": BLOB.ZERO_SHA256,
    }
    proof["proof_sha256"] = BLOB._proof_digest(proof)  # noqa: SLF001
    try:
        BLOB.validate_role_proof(
            proof,
            identity=expected_identity,
            source_site=source_site,
            target_site=target_site,
            role=role,
            now=now,
        )
    except BLOB.BlobRoundtripContractError as exc:
        raise BlobRoundtripCollectorContractError("blob collector input is not fresh or gate-compatible") from exc
    return document, proof


def build_role_proof(
    value: Any,
    *,
    identity: Mapping[str, Any],
    source_site: str,
    target_site: str,
    role: str,
    now: datetime,
) -> dict[str, Any]:
    """Reduce one validated redacted collector input to a role proof."""

    _document, proof = validate_collector_input(
        value,
        identity=identity,
        source_site=source_site,
        target_site=target_site,
        role=role,
        now=now,
    )
    return proof
