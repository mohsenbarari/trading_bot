"""Pure signed baseline contract for an Object-delta stream.

The first incremental batch is meaningful only when the receiver has an
authenticated database baseline that ends immediately before that stream.  A
full snapshot manifest by itself does not carry that cutover fact, and a
stream cursor by itself cannot prove which database image it follows.

This module therefore defines a compact source-signed claim for a *fresh*
stream generation.  The source-side coordinator must create it only while a
shared write gate is held: it exports the PostgreSQL snapshot, enables the
new outbox generation, and releases the gate only after the stream begins at
sequence one.  This module validates and signs that claim, but deliberately
does not implement the gate, export a PostgreSQL snapshot, read a file,
contact Object Storage, use age, or start a worker.

The signed plaintext is intended to travel only inside the existing private,
versioned, age-encrypted Object Storage control plane.  A receiver must also
verify its locally pinned source public key and its installed release before
using the result to admit Object-delta batches.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any
from uuid import UUID

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    GENESIS_PRIOR_CHAIN_SHA256,
    LEASE_ID_RE,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    VERSION_ID_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
)
from core.append_only_sync_delta_payload import REGISTRY_FINGERPRINT_RE


OBJECT_DELTA_BASELINE_MANIFEST_SCHEMA = "gold-trade-object-delta-baseline-manifest-v1"
OBJECT_DELTA_BASELINE_MANIFEST_STATUS = "committed"
OBJECT_DELTA_BASELINE_MANIFEST_SIGNATURE_ALGORITHM = "ed25519"
OBJECT_DELTA_BASELINE_MANIFEST_SIGNATURE_DOMAIN = (
    b"gold-trade-object-delta-baseline-manifest-v1\x00"
)
OBJECT_DELTA_BASELINE_CUTOVER_MODE = "write_fenced_exported_snapshot_v1"
MAX_OBJECT_DELTA_BASELINE_MANIFEST_BYTES = 64 * 1024

_BASELINE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "stream_generation_id",
        "registry_fingerprint",
        "writer_term",
        "snapshot",
        "cutover",
        "source_signer",
        "source_signature",
    }
)
_WRITER_TERM_FIELDS = frozenset({"epoch", "lease_id"})
_SNAPSHOT_FIELDS = frozenset(
    {
        "source_generation",
        "snapshot_id",
        "release_sha",
        "alembic_revision",
        "manifest_object_key",
        "manifest_object_version_id",
        "manifest_ciphertext_sha256",
        "manifest_ciphertext_bytes",
        "database_sha256",
        "uploads_sha256",
    }
)
_CUTOVER_FIELDS = frozenset(
    {
        "mode",
        "write_gate_id",
        "first_sequence",
        "prior_chain_sha256",
    }
)
_SIGNER_FIELDS = frozenset({"algorithm", "public_key_base64", "key_id"})
_SIGNATURE_FIELDS = frozenset({"algorithm", "signature_base64"})

_SNAPSHOT_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{16,64}$")
_SOURCE_GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ALEMBIC_REVISION_RE = re.compile(r"^[0-9a-z]{8,64}$")
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$")


class ObjectDeltaBaselineManifestError(ValueError):
    """The signed baseline is malformed, unbound, or not authentic."""


@dataclass(frozen=True)
class VerifiedObjectDeltaBaselineManifest:
    """Source-authenticated baseline *claim* without database payload bytes.

    This verifies the source signature and receiver release bindings only. It
    is not evidence that the claimed writer gate was actually held, nor that
    this receiver restored the named snapshot. A future coordinator and local
    restore-attestation verifier must establish both facts before admission.
    """

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str
    registry_fingerprint: str
    writer_epoch: int
    writer_lease_id: str
    source_generation: str
    snapshot_id: str
    alembic_revision: str
    manifest_object_key: str
    manifest_object_version_id: str
    manifest_ciphertext_sha256: str
    manifest_ciphertext_bytes: int
    database_sha256: str
    uploads_sha256: str
    write_gate_id: str
    source_key_id: str
    manifest_sha256: str


@dataclass(frozen=True)
class ObjectDeltaReceiverRestoreAttestation:
    """Non-secret local evidence a future receiver must compare to a baseline.

    A root-only adapter must derive this value only after independently
    validating the existing snapshot restore receipt, active snapshot pointer,
    installed release provenance, and static-assets receipt. This pure value
    intentionally does not read any of those files itself.
    """

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str
    registry_fingerprint: str
    source_generation: str
    snapshot_id: str
    alembic_revision: str
    manifest_object_key: str
    manifest_object_version_id: str
    manifest_ciphertext_sha256: str
    manifest_ciphertext_bytes: int
    database_sha256: str
    uploads_sha256: str


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ObjectDeltaBaselineManifestError("baseline manifest contains duplicate JSON fields")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ObjectDeltaBaselineManifestError(
        f"baseline manifest JSON constant is forbidden: {value}"
    )


def parse_object_delta_baseline_manifest_json(raw: bytes | str) -> dict[str, Any]:
    """Parse exact baseline JSON without treating it as authenticated.

    The returned mapping is structurally normalized, but callers must still
    use :func:`verify_object_delta_baseline_manifest` with a locally pinned
    source key before using its contents. This split permits a future
    root-only Object Storage adapter to reject duplicate keys and malformed
    decrypted bytes before its signature verification step.
    """

    if isinstance(raw, bytes):
        if not raw or len(raw) > MAX_OBJECT_DELTA_BASELINE_MANIFEST_BYTES:
            raise ObjectDeltaBaselineManifestError("baseline manifest size is invalid")
        try:
            decoded = raw.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise ObjectDeltaBaselineManifestError("baseline manifest JSON is invalid") from exc
    elif isinstance(raw, str):
        try:
            encoded = raw.encode("utf-8", "strict")
        except UnicodeEncodeError as exc:
            raise ObjectDeltaBaselineManifestError("baseline manifest JSON is invalid") from exc
        if not encoded or len(encoded) > MAX_OBJECT_DELTA_BASELINE_MANIFEST_BYTES:
            raise ObjectDeltaBaselineManifestError("baseline manifest size is invalid")
        decoded = raw
    else:
        raise ObjectDeltaBaselineManifestError("baseline manifest JSON is invalid")
    try:
        value = json.loads(
            decoded,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except ObjectDeltaBaselineManifestError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ObjectDeltaBaselineManifestError("baseline manifest JSON is invalid") from exc
    normalized, _verified, _public_key, _signature = _validate_baseline_manifest(value)
    return normalized


def _exact_mapping(value: object, *, fields: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ObjectDeltaBaselineManifestError(f"{label} fields are invalid")
    return dict(value)


def _text(value: object, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ObjectDeltaBaselineManifestError(f"{label} is invalid")
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError as exc:
        raise ObjectDeltaBaselineManifestError(f"{label} is invalid") from exc
    return value


def _positive_int(value: object, *, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ObjectDeltaBaselineManifestError(f"{label} is invalid")
    return value


def _decode_base64(value: object, *, label: str, expected_bytes: int) -> bytes:
    if not isinstance(value, str):
        raise ObjectDeltaBaselineManifestError(f"{label} is invalid")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ObjectDeltaBaselineManifestError(f"{label} is invalid") from exc
    if len(decoded) != expected_bytes:
        raise ObjectDeltaBaselineManifestError(f"{label} is invalid")
    return decoded


def _public_key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _require_public_key(value: object, *, label: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        raise ObjectDeltaBaselineManifestError(f"{label} is invalid")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, ValueError) as exc:
        raise ObjectDeltaBaselineManifestError(f"{label} is invalid") from exc
    return value


def _public_key_from_signer(signer: object) -> bytes:
    try:
        from cryptography.hazmat.primitives import serialization

        public_key = signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except (AttributeError, ImportError, TypeError, ValueError) as exc:
        raise ObjectDeltaBaselineManifestError("source signer is invalid") from exc
    return _require_public_key(public_key, label="source signer public key")


def unsigned_object_delta_baseline_manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    """Return the exact domain-separated bytes signed by the source key."""

    if not isinstance(manifest, Mapping):
        raise ObjectDeltaBaselineManifestError("baseline manifest is invalid")
    unsigned = {key: value for key, value in manifest.items() if key != "source_signature"}
    return OBJECT_DELTA_BASELINE_MANIFEST_SIGNATURE_DOMAIN + canonical_json_bytes(unsigned)


def _parse_writer_term(value: object) -> tuple[int, str]:
    term = _exact_mapping(value, fields=_WRITER_TERM_FIELDS, label="baseline writer term")
    return (
        _positive_int(term["epoch"], label="baseline writer epoch"),
        _text(term["lease_id"], label="baseline writer lease", pattern=LEASE_ID_RE),
    )


def _parse_snapshot(value: object, *, release_sha: str) -> dict[str, Any]:
    snapshot = _exact_mapping(value, fields=_SNAPSHOT_FIELDS, label="baseline snapshot")
    parsed = {
        "source_generation": _text(
            snapshot["source_generation"],
            label="baseline snapshot source generation",
            pattern=_SOURCE_GENERATION_RE,
        ),
        "snapshot_id": _text(
            snapshot["snapshot_id"], label="baseline snapshot id", pattern=_SNAPSHOT_ID_RE
        ),
        "release_sha": _text(
            snapshot["release_sha"], label="baseline snapshot release", pattern=RELEASE_SHA_RE
        ),
        "alembic_revision": _text(
            snapshot["alembic_revision"],
            label="baseline snapshot alembic revision",
            pattern=_ALEMBIC_REVISION_RE,
        ),
        "manifest_object_key": _text(
            snapshot["manifest_object_key"],
            label="baseline snapshot manifest object key",
            pattern=OBJECT_KEY_RE,
        ),
        "manifest_object_version_id": _text(
            snapshot["manifest_object_version_id"],
            label="baseline snapshot manifest object version",
            pattern=VERSION_ID_RE,
        ),
        "manifest_ciphertext_sha256": _text(
            snapshot["manifest_ciphertext_sha256"],
            label="baseline snapshot manifest ciphertext hash",
            pattern=SHA256_RE,
        ),
        "manifest_ciphertext_bytes": _positive_int(
            snapshot["manifest_ciphertext_bytes"],
            label="baseline snapshot manifest ciphertext bytes",
        ),
        "database_sha256": _text(
            snapshot["database_sha256"], label="baseline snapshot database hash", pattern=SHA256_RE
        ),
        "uploads_sha256": _text(
            snapshot["uploads_sha256"], label="baseline snapshot uploads hash", pattern=SHA256_RE
        ),
    }
    if parsed["release_sha"] != release_sha:
        raise ObjectDeltaBaselineManifestError("baseline snapshot release does not match stream release")
    return parsed


def _parse_cutover(value: object) -> str:
    cutover = _exact_mapping(value, fields=_CUTOVER_FIELDS, label="baseline cutover")
    if cutover["mode"] != OBJECT_DELTA_BASELINE_CUTOVER_MODE:
        raise ObjectDeltaBaselineManifestError("baseline cutover mode is invalid")
    raw_gate_id = cutover["write_gate_id"]
    try:
        gate_id = str(UUID(str(raw_gate_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ObjectDeltaBaselineManifestError("baseline write gate id is invalid") from exc
    if gate_id != raw_gate_id:
        raise ObjectDeltaBaselineManifestError("baseline write gate id is invalid")
    if _positive_int(cutover["first_sequence"], label="baseline first stream sequence") != 1:
        raise ObjectDeltaBaselineManifestError("baseline stream must begin at sequence one")
    if cutover["prior_chain_sha256"] != GENESIS_PRIOR_CHAIN_SHA256:
        raise ObjectDeltaBaselineManifestError("baseline stream must begin at genesis")
    return gate_id


def _parse_signer(value: object) -> tuple[bytes, str]:
    signer = _exact_mapping(value, fields=_SIGNER_FIELDS, label="baseline source signer")
    if signer["algorithm"] != OBJECT_DELTA_BASELINE_MANIFEST_SIGNATURE_ALGORITHM:
        raise ObjectDeltaBaselineManifestError("baseline source signer algorithm is invalid")
    public_key = _require_public_key(
        _decode_base64(
            signer["public_key_base64"],
            label="baseline source signer public key",
            expected_bytes=32,
        ),
        label="baseline source signer public key",
    )
    key_id = _text(signer["key_id"], label="baseline source signer key id", pattern=_KEY_ID_RE)
    if key_id != _public_key_id(public_key):
        raise ObjectDeltaBaselineManifestError("baseline source signer key id does not match public key")
    return public_key, key_id


def _parse_signature(value: object) -> bytes:
    signature = _exact_mapping(value, fields=_SIGNATURE_FIELDS, label="baseline source signature")
    if signature["algorithm"] != OBJECT_DELTA_BASELINE_MANIFEST_SIGNATURE_ALGORITHM:
        raise ObjectDeltaBaselineManifestError("baseline source signature algorithm is invalid")
    return _decode_base64(
        signature["signature_base64"],
        label="baseline source signature",
        expected_bytes=64,
    )


def _validate_baseline_manifest(
    manifest: object,
) -> tuple[dict[str, Any], VerifiedObjectDeltaBaselineManifest, bytes, bytes]:
    value = _exact_mapping(manifest, fields=_BASELINE_FIELDS, label="baseline manifest")
    if (
        value["schema"] != OBJECT_DELTA_BASELINE_MANIFEST_SCHEMA
        or value["status"] != OBJECT_DELTA_BASELINE_MANIFEST_STATUS
    ):
        raise ObjectDeltaBaselineManifestError("baseline manifest schema or status is invalid")
    source_site = value["source_site"]
    destination_site = value["destination_site"]
    if not isinstance(source_site, str) or not isinstance(destination_site, str):
        raise ObjectDeltaBaselineManifestError("baseline stream sites are invalid")
    if source_site not in WEBAPP_SITES or destination_site not in WEBAPP_SITES or source_site == destination_site:
        raise ObjectDeltaBaselineManifestError("baseline stream sites are invalid")
    campaign_id = _text(value["campaign_id"], label="baseline campaign", pattern=CAMPAIGN_ID_RE)
    release_sha = _text(value["release_sha"], label="baseline release", pattern=RELEASE_SHA_RE)
    stream_generation_id = _text(
        value["stream_generation_id"],
        label="baseline stream generation",
        pattern=STREAM_GENERATION_ID_RE,
    )
    registry_fingerprint = _text(
        value["registry_fingerprint"],
        label="baseline registry fingerprint",
        pattern=REGISTRY_FINGERPRINT_RE,
    )
    writer_epoch, writer_lease_id = _parse_writer_term(value["writer_term"])
    snapshot = _parse_snapshot(value["snapshot"], release_sha=release_sha)
    write_gate_id = _parse_cutover(value["cutover"])
    public_key, key_id = _parse_signer(value["source_signer"])
    signature = _parse_signature(value["source_signature"])
    normalized = {
        "schema": OBJECT_DELTA_BASELINE_MANIFEST_SCHEMA,
        "status": OBJECT_DELTA_BASELINE_MANIFEST_STATUS,
        "source_site": source_site,
        "destination_site": destination_site,
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "stream_generation_id": stream_generation_id,
        "registry_fingerprint": registry_fingerprint,
        "writer_term": {"epoch": writer_epoch, "lease_id": writer_lease_id},
        "snapshot": snapshot,
        "cutover": {
            "mode": OBJECT_DELTA_BASELINE_CUTOVER_MODE,
            "write_gate_id": write_gate_id,
            "first_sequence": 1,
            "prior_chain_sha256": GENESIS_PRIOR_CHAIN_SHA256,
        },
        "source_signer": {
            "algorithm": OBJECT_DELTA_BASELINE_MANIFEST_SIGNATURE_ALGORITHM,
            "public_key_base64": base64.b64encode(public_key).decode("ascii"),
            "key_id": key_id,
        },
        "source_signature": {
            "algorithm": OBJECT_DELTA_BASELINE_MANIFEST_SIGNATURE_ALGORITHM,
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        },
    }
    verified = VerifiedObjectDeltaBaselineManifest(
        source_site=source_site,
        destination_site=destination_site,
        campaign_id=campaign_id,
        release_sha=release_sha,
        stream_generation_id=stream_generation_id,
        registry_fingerprint=registry_fingerprint,
        writer_epoch=writer_epoch,
        writer_lease_id=writer_lease_id,
        source_generation=snapshot["source_generation"],
        snapshot_id=snapshot["snapshot_id"],
        alembic_revision=snapshot["alembic_revision"],
        manifest_object_key=snapshot["manifest_object_key"],
        manifest_object_version_id=snapshot["manifest_object_version_id"],
        manifest_ciphertext_sha256=snapshot["manifest_ciphertext_sha256"],
        manifest_ciphertext_bytes=snapshot["manifest_ciphertext_bytes"],
        database_sha256=snapshot["database_sha256"],
        uploads_sha256=snapshot["uploads_sha256"],
        write_gate_id=write_gate_id,
        source_key_id=key_id,
        manifest_sha256=hashlib.sha256(canonical_json_bytes(normalized)).hexdigest(),
    )
    return normalized, verified, public_key, signature


def assert_object_delta_baseline_matches_receiver_restore(
    baseline: VerifiedObjectDeltaBaselineManifest,
    restore: ObjectDeltaReceiverRestoreAttestation,
) -> None:
    """Require a source-signed claim to match local verified restore evidence.

    This comparison intentionally grants no write capability. It is the
    receiver-side half of the baseline precondition: a future adapter must
    call it after verifying a root-only local restore attestation, before it
    installs a stream permit or enters the dedicated database apply scope.
    """

    if not isinstance(baseline, VerifiedObjectDeltaBaselineManifest):
        raise ObjectDeltaBaselineManifestError("verified object-delta baseline is required")
    if not isinstance(restore, ObjectDeltaReceiverRestoreAttestation):
        raise ObjectDeltaBaselineManifestError("receiver restore attestation is required")
    expected = (
        ("source site", baseline.source_site, restore.source_site),
        ("destination site", baseline.destination_site, restore.destination_site),
        ("campaign", baseline.campaign_id, restore.campaign_id),
        ("release", baseline.release_sha, restore.release_sha),
        ("stream generation", baseline.stream_generation_id, restore.stream_generation_id),
        ("registry fingerprint", baseline.registry_fingerprint, restore.registry_fingerprint),
        ("source generation", baseline.source_generation, restore.source_generation),
        ("snapshot id", baseline.snapshot_id, restore.snapshot_id),
        ("alembic revision", baseline.alembic_revision, restore.alembic_revision),
        ("manifest object key", baseline.manifest_object_key, restore.manifest_object_key),
        ("manifest object version", baseline.manifest_object_version_id, restore.manifest_object_version_id),
        (
            "manifest ciphertext hash",
            baseline.manifest_ciphertext_sha256,
            restore.manifest_ciphertext_sha256,
        ),
        (
            "manifest ciphertext bytes",
            baseline.manifest_ciphertext_bytes,
            restore.manifest_ciphertext_bytes,
        ),
        ("database hash", baseline.database_sha256, restore.database_sha256),
        ("uploads hash", baseline.uploads_sha256, restore.uploads_sha256),
    )
    for label, baseline_value, restore_value in expected:
        if baseline_value != restore_value:
            raise ObjectDeltaBaselineManifestError(
                f"baseline does not match receiver restore {label}"
            )


def build_object_delta_baseline_manifest(
    *,
    source_site: str,
    destination_site: str,
    campaign_id: str,
    release_sha: str,
    stream_generation_id: str,
    registry_fingerprint: str,
    writer_epoch: int,
    writer_lease_id: str,
    snapshot: Mapping[str, Any],
    write_gate_id: str,
    source_signer: object,
) -> dict[str, Any]:
    """Build one fresh-genesis baseline claim and sign it in memory only."""

    public_key = _public_key_from_signer(source_signer)
    unsigned: dict[str, Any] = {
        "schema": OBJECT_DELTA_BASELINE_MANIFEST_SCHEMA,
        "status": OBJECT_DELTA_BASELINE_MANIFEST_STATUS,
        "source_site": source_site,
        "destination_site": destination_site,
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "stream_generation_id": stream_generation_id,
        "registry_fingerprint": registry_fingerprint,
        "writer_term": {"epoch": writer_epoch, "lease_id": writer_lease_id},
        "snapshot": dict(snapshot),
        "cutover": {
            "mode": OBJECT_DELTA_BASELINE_CUTOVER_MODE,
            "write_gate_id": write_gate_id,
            "first_sequence": 1,
            "prior_chain_sha256": GENESIS_PRIOR_CHAIN_SHA256,
        },
        "source_signer": {
            "algorithm": OBJECT_DELTA_BASELINE_MANIFEST_SIGNATURE_ALGORITHM,
            "public_key_base64": base64.b64encode(public_key).decode("ascii"),
            "key_id": _public_key_id(public_key),
        },
        "source_signature": {
            "algorithm": OBJECT_DELTA_BASELINE_MANIFEST_SIGNATURE_ALGORITHM,
            "signature_base64": base64.b64encode(b"\x00" * 64).decode("ascii"),
        },
    }
    normalized, _verified, _parsed_key, _placeholder_signature = _validate_baseline_manifest(unsigned)
    try:
        signature = source_signer.sign(unsigned_object_delta_baseline_manifest_bytes(normalized))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ObjectDeltaBaselineManifestError("source signer cannot sign baseline manifest") from exc
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise ObjectDeltaBaselineManifestError("source signer produced an invalid baseline signature")
    normalized["source_signature"] = {
        "algorithm": OBJECT_DELTA_BASELINE_MANIFEST_SIGNATURE_ALGORITHM,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }
    # Parse once more so callers cannot receive a malformed signed structure.
    _validate_baseline_manifest(normalized)
    return normalized


def verify_object_delta_baseline_manifest(
    manifest: object,
    *,
    expected_source_public_key: bytes,
    expected_source_site: str,
    expected_destination_site: str,
    expected_campaign_id: str,
    expected_release_sha: str,
    expected_stream_generation_id: str,
    expected_registry_fingerprint: str,
) -> VerifiedObjectDeltaBaselineManifest:
    """Verify the exact source-signed baseline against receiver expectations."""

    expected_key = _require_public_key(expected_source_public_key, label="expected source public key")
    normalized, verified, actual_key, signature = _validate_baseline_manifest(manifest)
    if actual_key != expected_key:
        raise ObjectDeltaBaselineManifestError("baseline source public key does not match the receiver pin")
    expected_values = (
        (expected_source_site, verified.source_site, "source site"),
        (expected_destination_site, verified.destination_site, "destination site"),
        (expected_campaign_id, verified.campaign_id, "campaign"),
        (expected_release_sha, verified.release_sha, "release"),
        (expected_stream_generation_id, verified.stream_generation_id, "stream generation"),
        (expected_registry_fingerprint, verified.registry_fingerprint, "registry fingerprint"),
    )
    for expected, actual, label in expected_values:
        if not isinstance(expected, str) or expected != actual:
            raise ObjectDeltaBaselineManifestError(f"baseline {label} does not match receiver binding")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise ObjectDeltaBaselineManifestError(
            "baseline source signature verification is unavailable"
        ) from exc
    try:
        Ed25519PublicKey.from_public_bytes(actual_key).verify(
            signature,
            unsigned_object_delta_baseline_manifest_bytes(normalized),
        )
    except (ValueError, InvalidSignature) as exc:
        raise ObjectDeltaBaselineManifestError("baseline source signature verification failed") from exc
    return verified
