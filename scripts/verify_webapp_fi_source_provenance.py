#!/usr/bin/env python3
"""Portable verifier for signed WebApp-FI source proof artifacts.

This module intentionally performs no filesystem, Docker, SSH, Object
Storage, service, or privilege operation.  Controller and WA-IR consumers use
it only after the FI public signing key has been enrolled and pinned by a
separate controller-authenticated certificate flow.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any, Mapping, Sequence


ATTESTATION_SCHEMA = "gold-trade-webapp-fi-source-role-attestation-v2"
IMAGE_EXPORT_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-image-export-receipt-v2"
DELIVERY_ENVELOPE_SCHEMA = "gold-trade-webapp-fi-source-adoption-delivery-envelope-v1"
SIGNER_ENROLLMENT_CERTIFICATE_SCHEMA = "gold-trade-webapp-fi-source-signer-enrollment-certificate-v2"
STATIC_ASSET_PROVENANCE_SCHEMA = "gold-trade-webapp-fi-static-asset-provenance-v1"
IMAGE_ADOPTION_RECEIPT_SCHEMA = "gold-trade-webapp-fi-controller-image-adoption-receipt-v2"
ATTESTATION_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-source-role-attestation-v2\x00"
IMAGE_EXPORT_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-source-image-export-v2\x00"
DELIVERY_ENVELOPE_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-source-adoption-delivery-envelope-v1\x00"
SIGNER_ENROLLMENT_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-source-signer-enrollment-v2\x00"
STATIC_ASSET_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-static-asset-provenance-v1\x00"
IMAGE_ADOPTION_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-controller-image-adoption-receipt-v2\x00"

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
ALEMBIC_RE = re.compile(r"^[0-9a-f]{12}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:-]{0,511}$")
OBJECT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/=-]{2,1023}$")
CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
PACKAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
UTC_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
MAX_OBSERVATION_AGE_SECONDS = 15 * 60
MAX_ENROLLMENT_CERTIFICATE_LIFETIME_SECONDS = 60 * 60
MAX_VERSION_ID_BYTES = 1024
VERSION_ID_RE = re.compile(rf"^[A-Za-z0-9._~+/=-]{{1,{MAX_VERSION_ID_BYTES}}}$")

CODE_PROJECTIONS = (
    "api",
    "bot",
    "core",
    "src",
    "models",
    "migrations",
    "scripts",
    "main.py",
    "schemas.py",
    "trading_settings.json",
)
RUNTIME_DATA_MOUNT_TARGETS = frozenset({"/app/uploads", "/app/audit_trail"})
RUNTIME_EXTERNAL_NON_PAYLOAD_MOUNT_TARGET = "/app/certs"


def _load_image_archive_contract() -> Any:
    """Load the co-shipped canonical WA-IR image-tag contract by exact path."""

    path = Path(__file__).with_name("webapp_ir_image_archive_contract.py")
    spec = importlib.util.spec_from_file_location("_webapp_fi_source_image_archive_contract", path)
    if spec is None or spec.loader is None:  # pragma: no cover - bootstrap invariant.
        raise RuntimeError("cannot load WA-IR image archive contract")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


image_contract = _load_image_archive_contract()


class SourceProvenanceVerificationError(RuntimeError):
    """A portable FI source proof is malformed, unpinned, or untrusted."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceProvenanceVerificationError("JSON input contains duplicate keys")
        result[key] = value
    return result


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _reject_json_constant(value: str) -> None:
    raise SourceProvenanceVerificationError(f"JSON input contains unsupported constant: {value}")


def _parse(payload: bytes, *, field: str) -> dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceProvenanceVerificationError(f"{field} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise SourceProvenanceVerificationError(f"{field} is not canonical JSON")
    _reject_persisted_url(payload, field=field)
    return value


def _reject_persisted_url(payload: bytes, *, field: str) -> None:
    """Portable proofs must never retain an Object Storage control URL."""

    lowered = payload.lower()
    if b"://" in lowered or b"presigned" in lowered or b'"url"' in lowered:
        raise SourceProvenanceVerificationError(f"{field} persists a forbidden URL")


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise SourceProvenanceVerificationError(f"{field} is invalid")
    return value


def _version_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or value.lower() == "null" or not VERSION_ID_RE.fullmatch(value):
        raise SourceProvenanceVerificationError(f"{field} is invalid")
    return value


def _canonical_mount_path(value: object, *, field: str, allow_root: bool) -> str:
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
        raise SourceProvenanceVerificationError(f"{field} is invalid")
    pure = PurePosixPath(value)
    if (
        pure.anchor != "/"
        or pure.as_posix() != value
        or any(part in {".", ".."} for part in pure.parts)
        or (not allow_root and value == "/")
    ):
        raise SourceProvenanceVerificationError(f"{field} is invalid")
    return value


def _paths_overlap(left: str, right: str) -> bool:
    def contains(parent: str, child: str) -> bool:
        return parent == "/" or parent == child or child.startswith(parent + "/")

    return contains(left, right) or contains(right, left)


def _size(value: object, *, field: str, maximum: int, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise SourceProvenanceVerificationError(f"{field} is invalid")
    return value


def _application(value: object, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"release_sha", "expected_alembic_revision"}:
        raise SourceProvenanceVerificationError(f"{field} is invalid")
    release = value.get("release_sha")
    revision = value.get("expected_alembic_revision")
    if not isinstance(release, str) or not RELEASE_RE.fullmatch(release) or not isinstance(revision, str) or not ALEMBIC_RE.fullmatch(revision):
        raise SourceProvenanceVerificationError(f"{field} is invalid")
    return {"release_sha": release, "expected_alembic_revision": revision}


def _tooling(value: object, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"control_commit", "control_tree"}:
        raise SourceProvenanceVerificationError(f"{field} is invalid")
    commit = value.get("control_commit")
    tree = value.get("control_tree")
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit) or not isinstance(tree, str) or not COMMIT_RE.fullmatch(tree):
        raise SourceProvenanceVerificationError(f"{field} is invalid")
    return {"control_commit": commit, "control_tree": tree}


def _key(value: object, *, field: str) -> bytes:
    if not isinstance(value, str):
        raise SourceProvenanceVerificationError(f"{field} is invalid")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise SourceProvenanceVerificationError(f"{field} is invalid") from exc
    if len(raw) != 32:
        raise SourceProvenanceVerificationError(f"{field} is invalid")
    return raw


def public_key_id(public_key_base64: str) -> str:
    return "ed25519-sha256:" + sha256_bytes(_key(public_key_base64, field="public key"))


def _verify_signature(*, unsigned: Mapping[str, Any], signature: object, public_key_base64: str, domain: bytes) -> None:
    if not isinstance(signature, Mapping) or set(signature) != {"algorithm", "signature_base64"} or signature.get("algorithm") != "ed25519" or not isinstance(signature.get("signature_base64"), str):
        raise SourceProvenanceVerificationError("signature envelope is invalid")
    try:
        raw_signature = base64.b64decode(signature["signature_base64"].encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise SourceProvenanceVerificationError("signature envelope is invalid") from exc
    if len(raw_signature) != 64:
        raise SourceProvenanceVerificationError("signature envelope is invalid")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise SourceProvenanceVerificationError("cryptography Ed25519 support is unavailable") from exc
    try:
        Ed25519PublicKey.from_public_bytes(_key(public_key_base64, field="pinned source signing public key")).verify(raw_signature, domain + canonical_json_bytes(unsigned))
    except InvalidSignature as exc:
        raise SourceProvenanceVerificationError("signature verification failed") from exc


def _campaign(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not CAMPAIGN_ID_RE.fullmatch(value):
        raise SourceProvenanceVerificationError(f"{field} is invalid")
    return value


def _timestamp(value: object, *, field: str) -> dt.datetime:
    if not isinstance(value, str) or not UTC_TIMESTAMP_RE.fullmatch(value):
        raise SourceProvenanceVerificationError(f"{field} is invalid")
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise SourceProvenanceVerificationError(f"{field} is invalid") from exc


def _fresh_timestamp(
    value: object,
    *,
    field: str,
    verification_time: str,
    maximum_age_seconds: int,
) -> str:
    observed = _timestamp(value, field=field)
    now = _timestamp(verification_time, field="verification time")
    if isinstance(maximum_age_seconds, bool) or not isinstance(maximum_age_seconds, int) or not 1 <= maximum_age_seconds <= MAX_OBSERVATION_AGE_SECONDS:
        raise SourceProvenanceVerificationError("maximum observation age is invalid")
    if observed > now or (now - observed).total_seconds() > maximum_age_seconds:
        raise SourceProvenanceVerificationError(f"{field} is stale or from the future")
    return value


def _delivery(value: object) -> dict[str, Any]:
    expected = {"object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes", "plaintext_sha256", "plaintext_bytes", "delivery_envelope_sha256", "controller_public_key_base64"}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise SourceProvenanceVerificationError("source adoption delivery is invalid")
    key = value.get("object_key")
    version = value.get("version_id")
    if not isinstance(key, str) or not OBJECT_KEY_RE.fullmatch(key):
        raise SourceProvenanceVerificationError("source adoption delivery is invalid")
    result = {"object_key": key, "version_id": _version_id(version, field="source adoption delivery version ID")}
    for name in ("ciphertext_sha256", "plaintext_sha256", "delivery_envelope_sha256"):
        result[name] = _sha(value.get(name), field=f"source adoption delivery {name}")
    result["ciphertext_bytes"] = _size(value.get("ciphertext_bytes"), field="source adoption delivery ciphertext bytes", maximum=25 * 1024 * 1024)
    result["plaintext_bytes"] = _size(value.get("plaintext_bytes"), field="source adoption delivery plaintext bytes", maximum=24 * 1024 * 1024)
    controller = value.get("controller_public_key_base64")
    _key(controller, field="source adoption delivery controller public key")
    result["controller_public_key_base64"] = controller
    return result


def _projection(value: object, *, application: Mapping[str, str]) -> dict[str, Any]:
    expected = {"runtime_source_root", "release_sha", "git_tree", "descriptor_sha256", "projections", "projection_sha256"}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise SourceProvenanceVerificationError("runtime projection is invalid")
    root = value.get("runtime_source_root")
    tree = value.get("git_tree")
    projections = value.get("projections")
    if value.get("release_sha") != application["release_sha"] or not isinstance(tree, str) or not COMMIT_RE.fullmatch(tree) or not isinstance(projections, Mapping) or set(projections) != set(CODE_PROJECTIONS):
        raise SourceProvenanceVerificationError("runtime projection is invalid")
    root = _canonical_mount_path(root, field="runtime projection root", allow_root=False)
    normalized: dict[str, list[dict[str, Any]]] = {}
    for relative in CODE_PROJECTIONS:
        entries = projections.get(relative)
        if not isinstance(entries, list) or not entries:
            raise SourceProvenanceVerificationError("runtime projection entries are invalid")
        prior = ""
        normalized_entries: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, Mapping) or set(entry) != {"path", "sha256", "bytes", "mode"}:
                raise SourceProvenanceVerificationError("runtime projection entry is invalid")
            path = entry.get("path")
            if not isinstance(path, str) or (path != relative and not path.startswith(relative + "/")) or (prior and path <= prior) or entry.get("mode") not in {"100644", "100755"}:
                raise SourceProvenanceVerificationError("runtime projection entry is invalid")
            prior = path
            normalized_entries.append({"path": path, "sha256": _sha(entry.get("sha256"), field="runtime projection hash"), "bytes": _size(entry.get("bytes"), field="runtime projection bytes", maximum=100 * 1024 * 1024, minimum=0), "mode": entry["mode"]})
        normalized[relative] = normalized_entries
    projection_hash = _sha(value.get("projection_sha256"), field="runtime projection sha256")
    if projection_hash != sha256_bytes(canonical_json_bytes(normalized)):
        raise SourceProvenanceVerificationError("runtime projection hash is invalid")
    return {"runtime_source_root": root, "release_sha": application["release_sha"], "git_tree": tree, "descriptor_sha256": _sha(value.get("descriptor_sha256"), field="runtime descriptor sha256"), "projections": normalized, "projection_sha256": projection_hash}


def _static(value: object) -> dict[str, Any]:
    expected = {"descriptor_sha256", "artifact", "files_sha256", "file_count", "source_kind"}
    if not isinstance(value, Mapping) or set(value) != expected or value.get("source_kind") != "deterministic_2c08_dist_manifest":
        raise SourceProvenanceVerificationError("static asset proof is invalid")
    artifact = value.get("artifact")
    fields = {"object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes", "plaintext_sha256", "plaintext_bytes"}
    if not isinstance(artifact, Mapping) or set(artifact) != fields:
        raise SourceProvenanceVerificationError("static asset proof artifact is invalid")
    if not isinstance(artifact.get("object_key"), str) or not OBJECT_KEY_RE.fullmatch(artifact["object_key"]):
        raise SourceProvenanceVerificationError("static asset proof artifact is invalid")
    _version_id(artifact.get("version_id"), field="static asset proof artifact version ID")
    for name in ("ciphertext_sha256", "plaintext_sha256"):
        _sha(artifact.get(name), field=f"static artifact {name}")
    for name in ("ciphertext_bytes", "plaintext_bytes"):
        _size(artifact.get(name), field=f"static artifact {name}", maximum=100 * 1024 * 1024)
    return {"descriptor_sha256": _sha(value.get("descriptor_sha256"), field="static descriptor sha256"), "artifact": dict(artifact), "files_sha256": _sha(value.get("files_sha256"), field="static files sha256"), "file_count": _size(value.get("file_count"), field="static file count", maximum=100_000, minimum=0), "source_kind": value["source_kind"]}


def _mounts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise SourceProvenanceVerificationError("container mounts are invalid")
    output: list[dict[str, Any]] = []
    destinations: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"type", "source", "destination", "read_only"}:
            raise SourceProvenanceVerificationError("container mount is invalid")
        mount_type, source, destination, read_only = item.get("type"), item.get("source"), item.get("destination"), item.get("read_only")
        if mount_type not in {"bind", "volume", "tmpfs"}:
            raise SourceProvenanceVerificationError("container mount is invalid")
        destination = _canonical_mount_path(destination, field="container mount destination", allow_root=True)
        if destination in destinations or not isinstance(read_only, bool):
            raise SourceProvenanceVerificationError("container mount is invalid")
        if mount_type == "bind" and (not isinstance(source, str) or not source.startswith("/")):
            raise SourceProvenanceVerificationError("container bind source is invalid")
        if mount_type == "bind":
            source = _canonical_mount_path(source, field="container bind source", allow_root=False)
        if mount_type != "bind" and source is not None and not isinstance(source, str):
            raise SourceProvenanceVerificationError("container mount source is invalid")
        destinations.add(destination)
        output.append({"type": mount_type, "source": source, "destination": destination, "read_only": read_only})
    if output != sorted(output, key=lambda item: (item["destination"], item["type"], str(item["source"]))):
        raise SourceProvenanceVerificationError("container mounts are not normalized")
    return output


def _container(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"name", "container_id", "image_id", "image_reference", "mounts"}:
        raise SourceProvenanceVerificationError("container is invalid")
    if not isinstance(value.get("name"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value["name"]) or not isinstance(value.get("container_id"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["container_id"]) or not isinstance(value.get("image_id"), str) or not IMAGE_ID_RE.fullmatch(value["image_id"]) or not isinstance(value.get("image_reference"), str) or not IMAGE_REFERENCE_RE.fullmatch(value["image_reference"]):
        raise SourceProvenanceVerificationError("container is invalid")
    return {"name": value["name"], "container_id": value["container_id"], "image_id": value["image_id"], "image_reference": value["image_reference"], "mounts": _mounts(value["mounts"])}


def _assert_app_projection_mounts(*, container: Mapping[str, Any], root: str, static: bool) -> None:
    required = {"/app/" + relative: root + "/" + relative for relative in CODE_PROJECTIONS}
    if static:
        required["/app/mini_app_dist"] = root + "/mini_app_dist"
    observed: dict[str, Mapping[str, Any]] = {}
    external_certs: Mapping[str, Any] | None = None
    for mount in container["mounts"]:
        destination = mount["destination"]
        if destination in required:
            if mount["type"] != "bind" or mount["source"] != required[destination]:
                raise SourceProvenanceVerificationError("runtime projection mount is invalid")
            observed[destination] = mount
        elif any(_paths_overlap(destination, expected) for expected in required):
            raise SourceProvenanceVerificationError("container mount overlaps a reviewed runtime projection")
        elif destination == RUNTIME_EXTERNAL_NON_PAYLOAD_MOUNT_TARGET:
            if not static or mount["type"] != "bind" or not isinstance(mount["source"], str):
                raise SourceProvenanceVerificationError("external non-payload mount is invalid")
            if _paths_overlap(mount["source"], root):
                raise SourceProvenanceVerificationError("external non-payload mount source overlaps runtime root")
            if external_certs is not None:
                raise SourceProvenanceVerificationError("external non-payload mount is duplicated")
            external_certs = mount
        elif destination in RUNTIME_DATA_MOUNT_TARGETS:
            continue
        elif destination == "/app" or destination.startswith("/app/"):
            raise SourceProvenanceVerificationError("unexpected mount exists below /app")
    if set(observed) != set(required):
        raise SourceProvenanceVerificationError("runtime projection mount is incomplete")
    if static and external_certs is None:
        raise SourceProvenanceVerificationError("application certificate mount is incomplete")


def verify_source_role_attestation_payload(
    *,
    payload: bytes,
    pinned_source_signing_public_key_base64: str,
    expected_campaign_id: str,
    expected_application: Mapping[str, str],
    expected_control_commit: str,
    expected_canonical_release_tree_sha256: str,
    expected_app_image_id: str,
    expected_app_image_reference: str,
    verification_time: str,
    maximum_observation_age_seconds: int = MAX_OBSERVATION_AGE_SECONDS,
) -> dict[str, Any]:
    """Verify only FI's fresh, point-in-time source-key signed claims.

    This pure verifier intentionally does not assert controller authorization.
    A source signing key can authenticate these claims, but enrollment
    certificate authority must be verified separately against a controller
    key and the exact local consumption state.
    """

    value = _parse(payload, field="WebApp-FI source role attestation")
    expected = {
        "schema", "status", "attested_at", "campaign_id", "source_site", "destination_site", "package_id",
        "application", "application_release_tree", "tooling", "source_adoption_install_receipt_sha256",
        "source_adoption_delivery", "canonical_release_tree_sha256", "source_signer_enrollment",
        "observation_scope", "runtime_projection", "static_assets_proof", "containers",
        "active_application_image", "race_check", "source_signing_public_key_base64", "source_signing_key_id",
        "source_signature",
    }
    if set(value) != expected or value.get("schema") != ATTESTATION_SCHEMA or value.get("status") != "attested":
        raise SourceProvenanceVerificationError("WebApp-FI source role attestation is unsupported")
    attested_at = _timestamp(value.get("attested_at"), field="attestation timestamp")
    _fresh_timestamp(
        value.get("attested_at"),
        field="attestation timestamp",
        verification_time=verification_time,
        maximum_age_seconds=maximum_observation_age_seconds,
    )
    expected_campaign_id = _campaign(expected_campaign_id, field="expected campaign")
    application = _application(value.get("application"), field="attestation application")
    if value.get("campaign_id") != expected_campaign_id or value.get("source_site") != "webapp_fi" or value.get("destination_site") != "webapp_ir" or not isinstance(value.get("package_id"), str) or not PACKAGE_ID_RE.fullmatch(value["package_id"]) or application != _application(expected_application, field="expected application"):
        raise SourceProvenanceVerificationError("attestation binding is invalid")
    tooling = _tooling(value.get("tooling"), field="attestation tooling")
    if tooling["control_commit"] != expected_control_commit or not isinstance(value.get("application_release_tree"), str) or not COMMIT_RE.fullmatch(value["application_release_tree"]):
        raise SourceProvenanceVerificationError("attestation release tree is invalid")
    _sha(value.get("source_adoption_install_receipt_sha256"), field="install receipt sha256")
    delivery = _delivery(value.get("source_adoption_delivery"))
    descriptor_sha = _sha(value.get("canonical_release_tree_sha256"), field="canonical descriptor sha256")
    if descriptor_sha != _sha(expected_canonical_release_tree_sha256, field="expected canonical descriptor sha256"):
        raise SourceProvenanceVerificationError("attestation canonical descriptor is unexpected")
    enrollment = value.get("source_signer_enrollment")
    if not isinstance(enrollment, Mapping) or set(enrollment) != {
        "receipt_sha256", "certificate_sha256", "certificate_id", "operation_id", "certificate_consumption_sha256",
        "not_after", "fi_ssh_host_public_key_sha256", "controller_key_id", "source_signing_public_key_base64",
        "source_signing_key_id",
    }:
        raise SourceProvenanceVerificationError("source signer enrollment is invalid")
    for name in ("receipt_sha256", "certificate_sha256", "certificate_consumption_sha256", "fi_ssh_host_public_key_sha256"):
        _sha(enrollment.get(name), field=f"source signer enrollment {name}")
    if not isinstance(enrollment.get("certificate_id"), str) or not PACKAGE_ID_RE.fullmatch(enrollment["certificate_id"]) or not isinstance(enrollment.get("operation_id"), str) or not PACKAGE_ID_RE.fullmatch(enrollment["operation_id"]) or not isinstance(enrollment.get("controller_key_id"), str) or not re.fullmatch(r"ed25519-sha256:[0-9a-f]{64}", enrollment["controller_key_id"]):
        raise SourceProvenanceVerificationError("source signer enrollment is invalid")
    enrollment_not_after = _timestamp(enrollment.get("not_after"), field="source signer enrollment not_after")
    if attested_at > enrollment_not_after:
        raise SourceProvenanceVerificationError("attestation was made after source signer enrollment expiry")
    source_key_id = public_key_id(pinned_source_signing_public_key_base64)
    if enrollment.get("source_signing_public_key_base64") != pinned_source_signing_public_key_base64 or value.get("source_signing_public_key_base64") != pinned_source_signing_public_key_base64 or value.get("source_signing_key_id") != source_key_id or enrollment.get("source_signing_key_id") != value.get("source_signing_key_id"):
        raise SourceProvenanceVerificationError("source signing key is not pinned")
    if (
        delivery["controller_public_key_base64"] == pinned_source_signing_public_key_base64
        or enrollment.get("controller_key_id") == source_key_id
    ):
        raise SourceProvenanceVerificationError("source signing key reuses the controller key")
    if value.get("observation_scope") != {
        "point_in_time_only": True,
        "data_capture_performed": False,
        "schema_capture_performed": False,
        "promotion_ready": False,
        "later_snapshot_requires_separate_authorization": True,
    }:
        raise SourceProvenanceVerificationError("attestation observation scope is invalid")
    projection = value.get("runtime_projection")
    if not isinstance(projection, Mapping) or set(projection) != {"before", "after"}:
        raise SourceProvenanceVerificationError("runtime projection race proof is invalid")
    before = _projection(projection["before"], application=application)
    after = _projection(projection["after"], application=application)
    if before != after or before["descriptor_sha256"] != descriptor_sha or before["git_tree"] != value["application_release_tree"]:
        raise SourceProvenanceVerificationError("runtime projection race proof is invalid")
    static = value.get("static_assets_proof")
    if not isinstance(static, Mapping) or set(static) != {"before", "after", "proof_is_not_static_payload", "promotion_requires_verified_immutable_age_object"} or static.get("proof_is_not_static_payload") is not True or static.get("promotion_requires_verified_immutable_age_object") is not True:
        raise SourceProvenanceVerificationError("static asset proof policy is invalid")
    if _static(static["before"]) != _static(static["after"]):
        raise SourceProvenanceVerificationError("static asset proof race check is invalid")
    containers = value.get("containers")
    if not isinstance(containers, Mapping) or set(containers) != {"application", "sync_worker"}:
        raise SourceProvenanceVerificationError("attestation containers are invalid")
    app, sync = _container(containers["application"]), _container(containers["sync_worker"])
    _assert_app_projection_mounts(container=app, root=before["runtime_source_root"], static=True)
    _assert_app_projection_mounts(container=sync, root=before["runtime_source_root"], static=False)
    if app["image_id"] != expected_app_image_id or app["image_reference"] != expected_app_image_reference or sync["image_id"] != expected_app_image_id or sync["image_reference"] != expected_app_image_reference:
        raise SourceProvenanceVerificationError("runtime image binding is invalid")
    active = value.get("active_application_image")
    if not isinstance(active, Mapping) or set(active) != {"image_id", "image_reference", "repo_tags", "repo_digests"}:
        raise SourceProvenanceVerificationError("active image is invalid")
    repo_tags = active.get("repo_tags")
    repo_digests = active.get("repo_digests")
    if not isinstance(repo_tags, list) or not isinstance(repo_digests, list) or not all(isinstance(item, str) and IMAGE_REFERENCE_RE.fullmatch(item) for item in repo_tags + repo_digests):
        raise SourceProvenanceVerificationError("active image is invalid")
    if active.get("image_id") != expected_app_image_id or active.get("image_reference") != expected_app_image_reference or expected_app_image_reference not in set(repo_tags) | set(repo_digests):
        raise SourceProvenanceVerificationError("active image is invalid")
    if value.get("race_check") != {
        "runtime_projection_unchanged": True,
        "static_assets_unchanged": True,
        "application_container_unchanged": True,
        "sync_worker_container_unchanged": True,
        "active_image_unchanged": True,
    }:
        raise SourceProvenanceVerificationError("race check is invalid")
    _verify_signature(unsigned={key: item for key, item in value.items() if key != "source_signature"}, signature=value["source_signature"], public_key_base64=pinned_source_signing_public_key_base64, domain=ATTESTATION_SIGNATURE_DOMAIN)
    return {
        "status": "verified",
        "attestation_sha256": sha256_bytes(payload),
        "attested_at": value["attested_at"],
        "package_id": value["package_id"],
        "source_adoption_install_receipt_sha256": value["source_adoption_install_receipt_sha256"],
        "campaign_id": expected_campaign_id,
        "application": application,
        "tooling": tooling,
        "descriptor_claim": {
            "canonical_release_tree_sha256": descriptor_sha,
            "application_release_tree": value["application_release_tree"],
            "application": application,
        },
        "runtime_claim": {
            "projection": before,
            "static_assets": _static(static["before"]),
            "containers": {"application": app, "sync_worker": sync},
        },
        "image_claim": {"image_id": expected_app_image_id, "image_reference": expected_app_image_reference, "active_application_image": dict(active)},
        "source_adoption_delivery_claim": delivery,
        "source_signer_enrollment_claim": dict(enrollment),
        "source_signing_key_id": value["source_signing_key_id"],
        "source_signing_public_key_base64": pinned_source_signing_public_key_base64,
        "point_in_time_observation_only": True,
    }


def _export_runtime_claim(
    value: object,
    *,
    application: Mapping[str, str],
    expected_descriptor_sha256: str,
    expected_application_release_tree: str,
    expected_app_image_id: str,
    expected_app_image_reference: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"projection", "static_assets", "containers", "active_application_image"}:
        raise SourceProvenanceVerificationError("image export runtime proof is invalid")
    projection = _projection(value["projection"], application=application)
    if projection["descriptor_sha256"] != expected_descriptor_sha256 or projection["git_tree"] != expected_application_release_tree:
        raise SourceProvenanceVerificationError("image export runtime projection is invalid")
    static_assets = _static(value["static_assets"])
    containers_value = value["containers"]
    if not isinstance(containers_value, Mapping) or set(containers_value) != {"application", "sync_worker"}:
        raise SourceProvenanceVerificationError("image export runtime containers are invalid")
    application_container = _container(containers_value["application"])
    sync_worker = _container(containers_value["sync_worker"])
    _assert_app_projection_mounts(container=application_container, root=projection["runtime_source_root"], static=True)
    _assert_app_projection_mounts(container=sync_worker, root=projection["runtime_source_root"], static=False)
    if application_container["image_id"] != expected_app_image_id or application_container["image_reference"] != expected_app_image_reference or sync_worker["image_id"] != expected_app_image_id or sync_worker["image_reference"] != expected_app_image_reference:
        raise SourceProvenanceVerificationError("image export runtime image is invalid")
    active = value["active_application_image"]
    if not isinstance(active, Mapping) or set(active) != {"image_id", "image_reference", "repo_tags", "repo_digests"}:
        raise SourceProvenanceVerificationError("image export active image is invalid")
    repo_tags = active.get("repo_tags")
    repo_digests = active.get("repo_digests")
    if not isinstance(repo_tags, list) or not isinstance(repo_digests, list) or not all(isinstance(item, str) and IMAGE_REFERENCE_RE.fullmatch(item) for item in repo_tags + repo_digests):
        raise SourceProvenanceVerificationError("image export active image is invalid")
    if active.get("image_id") != expected_app_image_id or active.get("image_reference") != expected_app_image_reference or expected_app_image_reference not in set(repo_tags) | set(repo_digests):
        raise SourceProvenanceVerificationError("image export active image is invalid")
    return {
        "projection": projection,
        "static_assets": static_assets,
        "containers": {"application": application_container, "sync_worker": sync_worker},
        "active_application_image": dict(active),
    }


def verify_image_export_receipt_payload(
    *,
    payload: bytes,
    pinned_source_signing_public_key_base64: str,
    expected_campaign_id: str,
    expected_application: Mapping[str, str],
    expected_control_commit: str,
    expected_application_release_tree: str,
    expected_canonical_release_tree_sha256: str,
    expected_attestation_sha256: str,
    expected_app_image_id: str,
    expected_app_image_reference: str,
    verification_time: str,
    maximum_observation_age_seconds: int = MAX_OBSERVATION_AGE_SECONDS,
) -> dict[str, Any]:
    """Verify a fresh source-key signed exact-byte image export receipt.

    The receipt makes no claim that a Docker archive is loadable.  It only
    binds bytes emitted by the trusted FI ``docker save`` invocation to the
    same before/after runtime observation.
    """

    value = _parse(payload, field="WebApp-FI image export receipt")
    expected = {
        "schema", "status", "exported_at", "export_id", "campaign_id", "source_site", "destination_site",
        "application", "application_release_tree", "tooling", "canonical_release_tree_sha256",
        "source_role_attestation_sha256", "source_signer_enrollment", "observation_scope", "image", "pre_export_runtime",
        "post_export_runtime", "exact_byte_export", "archive_consumption", "object_storage_export_required",
        "source_signing_public_key_base64", "source_signing_key_id", "source_signature",
    }
    if set(value) != expected or value.get("schema") != IMAGE_EXPORT_RECEIPT_SCHEMA or value.get("status") != "exported":
        raise SourceProvenanceVerificationError("WebApp-FI image export receipt is unsupported")
    exported_at = _timestamp(value.get("exported_at"), field="image export timestamp")
    _fresh_timestamp(
        value.get("exported_at"),
        field="image export timestamp",
        verification_time=verification_time,
        maximum_age_seconds=maximum_observation_age_seconds,
    )
    if not isinstance(value.get("export_id"), str) or not PACKAGE_ID_RE.fullmatch(value["export_id"]) or value.get("campaign_id") != _campaign(expected_campaign_id, field="expected campaign") or value.get("source_site") != "webapp_fi" or value.get("destination_site") != "webapp_ir" or _application(value.get("application"), field="image export application") != _application(expected_application, field="expected application"):
        raise SourceProvenanceVerificationError("image export binding is invalid")
    tooling = _tooling(value.get("tooling"), field="image export tooling")
    expected_descriptor = _sha(expected_canonical_release_tree_sha256, field="expected image export canonical descriptor sha256")
    if tooling["control_commit"] != expected_control_commit or not COMMIT_RE.fullmatch(expected_application_release_tree) or value.get("application_release_tree") != expected_application_release_tree or _sha(value.get("source_role_attestation_sha256"), field="image export attestation sha256") != _sha(expected_attestation_sha256, field="expected attestation sha256"):
        raise SourceProvenanceVerificationError("image export provenance binding is invalid")
    if _sha(value.get("canonical_release_tree_sha256"), field="image export canonical descriptor sha256") != expected_descriptor:
        raise SourceProvenanceVerificationError("image export canonical descriptor is unexpected")
    enrollment = value.get("source_signer_enrollment")
    enrollment_expected = {
        "receipt_sha256", "certificate_sha256", "certificate_id", "operation_id", "certificate_consumption_sha256",
        "not_after", "fi_ssh_host_public_key_sha256", "controller_key_id", "source_signing_public_key_base64",
        "source_signing_key_id",
    }
    if not isinstance(enrollment, Mapping) or set(enrollment) != enrollment_expected:
        raise SourceProvenanceVerificationError("image export signer enrollment is invalid")
    for name in ("receipt_sha256", "certificate_sha256", "certificate_consumption_sha256", "fi_ssh_host_public_key_sha256"):
        _sha(enrollment.get(name), field=f"image export signer enrollment {name}")
    if not isinstance(enrollment.get("certificate_id"), str) or not PACKAGE_ID_RE.fullmatch(enrollment["certificate_id"]) or not isinstance(enrollment.get("operation_id"), str) or not PACKAGE_ID_RE.fullmatch(enrollment["operation_id"]) or not isinstance(enrollment.get("controller_key_id"), str) or not re.fullmatch(r"ed25519-sha256:[0-9a-f]{64}", enrollment["controller_key_id"]):
        raise SourceProvenanceVerificationError("image export signer enrollment is invalid")
    enrollment_not_after = _timestamp(enrollment.get("not_after"), field="image export signer enrollment not_after")
    if exported_at > enrollment_not_after:
        raise SourceProvenanceVerificationError("image export was made after source signer enrollment expiry")
    source_key_id = public_key_id(pinned_source_signing_public_key_base64)
    if enrollment.get("source_signing_public_key_base64") != pinned_source_signing_public_key_base64 or enrollment.get("source_signing_key_id") != source_key_id:
        raise SourceProvenanceVerificationError("image export signer enrollment is not pinned")
    if enrollment.get("controller_key_id") == source_key_id:
        raise SourceProvenanceVerificationError("image export source signing key reuses the controller key")
    if value.get("observation_scope") != {
        "point_in_time_only": True,
        "data_capture_performed": False,
        "schema_capture_performed": False,
        "promotion_ready": False,
        "later_snapshot_requires_separate_authorization": True,
    }:
        raise SourceProvenanceVerificationError("image export observation scope is invalid")
    image = value.get("image")
    image_fields = {"image_id", "image_reference", "docker_save_archive_sha256", "docker_save_archive_bytes", "docker_save"}
    if not isinstance(image, Mapping) or set(image) != image_fields or image.get("image_id") != expected_app_image_id or image.get("image_reference") != expected_app_image_reference:
        raise SourceProvenanceVerificationError("image export image binding is invalid")
    _sha(image.get("docker_save_archive_sha256"), field="image export raw docker-save archive sha256")
    _size(image.get("docker_save_archive_bytes"), field="image export raw docker-save archive bytes", maximum=100 * 1024 * 1024 * 1024)
    docker_save = image.get("docker_save")
    if not isinstance(docker_save, Mapping) or set(docker_save) != {"command", "docker_executable_sha256", "docker_executable_bytes", "archive_semantics", "archive_layout", "manifest_semantics_attested", "docker_load_invoked", "loadability_claimed"}:
        raise SourceProvenanceVerificationError("image export docker save binding is invalid")
    if docker_save.get("command") != ["docker", "save", "--output", "webapp-fi-active-app-image.tar", expected_app_image_id] or _sha(docker_save.get("docker_executable_sha256"), field="image export docker executable sha256") is None or _size(docker_save.get("docker_executable_bytes"), field="image export docker executable bytes", maximum=10 * 1024 * 1024 * 1024) < 1 or docker_save.get("archive_semantics") != "exact_bytes_only_unparsed" or docker_save.get("archive_layout") != "not_inspected" or docker_save.get("manifest_semantics_attested") is not False or docker_save.get("docker_load_invoked") is not False or docker_save.get("loadability_claimed") is not False:
        raise SourceProvenanceVerificationError("image export docker save binding is invalid")
    before = _export_runtime_claim(
        value.get("pre_export_runtime"),
        application=_application(value["application"], field="image export application"),
        expected_descriptor_sha256=expected_descriptor,
        expected_application_release_tree=expected_application_release_tree,
        expected_app_image_id=expected_app_image_id,
        expected_app_image_reference=expected_app_image_reference,
    )
    after = _export_runtime_claim(
        value.get("post_export_runtime"),
        application=_application(value["application"], field="image export application"),
        expected_descriptor_sha256=expected_descriptor,
        expected_application_release_tree=expected_application_release_tree,
        expected_app_image_id=expected_app_image_id,
        expected_app_image_reference=expected_app_image_reference,
    )
    if before != after:
        raise SourceProvenanceVerificationError("image export runtime race proof is invalid")
    if value.get("exact_byte_export") != {"archive_is_unparsed_exact_bytes": True, "docker_load_invoked": False, "loadability_claimed": False, "bind_mounted_runtime_revalidated_before_and_after": True}:
        raise SourceProvenanceVerificationError("image export exact-byte policy is invalid")
    if value.get("archive_consumption") != {"docker_load_prohibited": True, "fi_local_exact_byte_hash_before_age_encryption": True, "controller_read_back_exact_byte_hash_after_age_encryption": True, "raw_repo_tags_are_not_authorization": True} or value.get("object_storage_export_required") != {"transport": "private_versioned_age_only", "create_only": True, "read_back_same_version_id": True, "direct_webapp_fi_to_webapp_ir_transfer": False}:
        raise SourceProvenanceVerificationError("image export transport policy is invalid")
    if value.get("source_signing_public_key_base64") != pinned_source_signing_public_key_base64 or value.get("source_signing_key_id") != public_key_id(pinned_source_signing_public_key_base64):
        raise SourceProvenanceVerificationError("image export signing key is not pinned")
    _verify_signature(unsigned={key: item for key, item in value.items() if key != "source_signature"}, signature=value["source_signature"], public_key_base64=pinned_source_signing_public_key_base64, domain=IMAGE_EXPORT_SIGNATURE_DOMAIN)
    return {
        "status": "verified",
        "image_export_receipt_sha256": sha256_bytes(payload),
        "exported_at": value["exported_at"],
        "campaign_id": expected_campaign_id,
        "application": _application(value["application"], field="image export application"),
        "tooling": tooling,
        "descriptor_claim": {
            "canonical_release_tree_sha256": expected_descriptor,
            "application_release_tree": expected_application_release_tree,
            "application": _application(value["application"], field="image export application"),
        },
        "runtime_claim": before,
        "image_claim": dict(image),
        "source_role_attestation_sha256": value["source_role_attestation_sha256"],
        "source_signer_enrollment_claim": dict(enrollment),
        "source_signing_key_id": value["source_signing_key_id"],
        "source_signing_public_key_base64": pinned_source_signing_public_key_base64,
        "point_in_time_observation_only": True,
    }


def _controller_key_id(value: object, *, field: str, pinned_controller_public_key_base64: str) -> str:
    if value != public_key_id(pinned_controller_public_key_base64):
        raise SourceProvenanceVerificationError(f"{field} is not pinned")
    return str(value)


def _object_descriptor(value: object, *, field: str, maximum_plaintext_bytes: int) -> dict[str, Any]:
    expected = {"object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes", "plaintext_sha256", "plaintext_bytes"}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise SourceProvenanceVerificationError(f"{field} is invalid")
    key = value.get("object_key")
    if not isinstance(key, str) or not OBJECT_KEY_RE.fullmatch(key):
        raise SourceProvenanceVerificationError(f"{field} is invalid")
    return {
        "object_key": key,
        "version_id": _version_id(value.get("version_id"), field=f"{field} version ID"),
        "ciphertext_sha256": _sha(value.get("ciphertext_sha256"), field=f"{field} ciphertext SHA-256"),
        "ciphertext_bytes": _size(
            value.get("ciphertext_bytes"),
            field=f"{field} ciphertext bytes",
            maximum=maximum_plaintext_bytes + 1024 * 1024,
        ),
        "plaintext_sha256": _sha(value.get("plaintext_sha256"), field=f"{field} plaintext SHA-256"),
        "plaintext_bytes": _size(
            value.get("plaintext_bytes"),
            field=f"{field} plaintext bytes",
            maximum=maximum_plaintext_bytes,
        ),
    }


def _verify_controller_signature(
    *,
    value: Mapping[str, Any],
    signature_field: str,
    public_key_field: str,
    pinned_controller_public_key_base64: str,
    domain: bytes,
    field: str,
) -> None:
    if value.get(public_key_field) != pinned_controller_public_key_base64:
        raise SourceProvenanceVerificationError(f"{field} controller key is not pinned")
    _verify_signature(
        unsigned={key: item for key, item in value.items() if key != signature_field},
        signature=value.get(signature_field),
        public_key_base64=pinned_controller_public_key_base64,
        domain=domain,
    )


def _controller_delivery_envelope(
    *,
    payload: bytes,
    pinned_controller_public_key_base64: str,
    expected_campaign_id: str,
    expected_application: Mapping[str, str],
    expected_tooling: Mapping[str, str],
    expected_canonical_release_tree_sha256: str,
) -> dict[str, Any]:
    value = _parse(payload, field="controller source-adoption delivery envelope")
    expected = {
        "schema", "status", "campaign_id", "source_site", "destination_site", "package_id", "application", "tooling",
        "canonical_release_tree_sha256", "fi_bootstrap_recipient", "object", "controller_public_key_base64", "controller_signature",
    }
    if set(value) != expected or value.get("schema") != DELIVERY_ENVELOPE_SCHEMA or value.get("status") != "issued":
        raise SourceProvenanceVerificationError("controller source-adoption delivery envelope is unsupported")
    if value.get("campaign_id") != _campaign(expected_campaign_id, field="expected campaign") or value.get("source_site") != "bot_fi" or value.get("destination_site") != "webapp_fi":
        raise SourceProvenanceVerificationError("controller source-adoption delivery envelope binding is invalid")
    package_id = value.get("package_id")
    if not isinstance(package_id, str) or not PACKAGE_ID_RE.fullmatch(package_id):
        raise SourceProvenanceVerificationError("controller source-adoption delivery envelope package is invalid")
    if _application(value.get("application"), field="controller source-adoption delivery application") != _application(expected_application, field="expected application") or _tooling(value.get("tooling"), field="controller source-adoption delivery tooling") != _tooling(expected_tooling, field="expected tooling") or _sha(value.get("canonical_release_tree_sha256"), field="controller source-adoption delivery descriptor SHA-256") != _sha(expected_canonical_release_tree_sha256, field="expected canonical descriptor SHA-256"):
        raise SourceProvenanceVerificationError("controller source-adoption delivery envelope release binding is invalid")
    recipient = value.get("fi_bootstrap_recipient")
    if not isinstance(recipient, str) or not re.fullmatch(r"age1[ac-hj-np-z02-9]{20,128}", recipient):
        raise SourceProvenanceVerificationError("controller source-adoption delivery envelope recipient is invalid")
    _verify_controller_signature(
        value=value,
        signature_field="controller_signature",
        public_key_field="controller_public_key_base64",
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
        domain=DELIVERY_ENVELOPE_SIGNATURE_DOMAIN,
        field="controller source-adoption delivery envelope",
    )
    return {
        "sha256": sha256_bytes(payload),
        "campaign_id": expected_campaign_id,
        "package_id": package_id,
        "application": _application(expected_application, field="expected application"),
        "tooling": _tooling(expected_tooling, field="expected tooling"),
        "canonical_release_tree_sha256": _sha(expected_canonical_release_tree_sha256, field="expected canonical descriptor SHA-256"),
        "fi_bootstrap_recipient": recipient,
        "object": _object_descriptor(
            value.get("object"),
            field="controller source-adoption delivery object",
            maximum_plaintext_bytes=24 * 1024 * 1024,
        ),
        "controller_public_key_base64": pinned_controller_public_key_base64,
    }


def _signer_enrollment_certificate(
    *,
    payload: bytes,
    pinned_controller_public_key_base64: str,
    expected_campaign_id: str,
    expected_application: Mapping[str, str],
    expected_tooling: Mapping[str, str],
    expected_canonical_release_tree_sha256: str,
    expected_delivery: Mapping[str, Any],
    expected_source_signing_public_key_base64: str,
    verification_time: str,
) -> dict[str, Any]:
    value = _parse(payload, field="controller signer enrollment certificate")
    expected = {
        "schema", "status", "certificate_id", "operation_id", "issued_at", "not_before", "not_after",
        "campaign_id", "source_site", "destination_site", "package_id", "application", "tooling",
        "canonical_release_tree_sha256", "source_adoption_install_receipt_sha256", "delivery_envelope_sha256",
        "source_adoption_object", "fi_bootstrap_recipient", "fi_ssh_host_public_key_sha256",
        "source_signing_public_key_base64", "source_signing_key_id", "controller_public_key_base64",
        "controller_key_id", "controller_signature",
    }
    if set(value) != expected or value.get("schema") != SIGNER_ENROLLMENT_CERTIFICATE_SCHEMA or value.get("status") != "issued":
        raise SourceProvenanceVerificationError("controller signer enrollment certificate is unsupported")
    certificate_id = value.get("certificate_id")
    operation_id = value.get("operation_id")
    if not isinstance(certificate_id, str) or not PACKAGE_ID_RE.fullmatch(certificate_id) or not isinstance(operation_id, str) or not PACKAGE_ID_RE.fullmatch(operation_id):
        raise SourceProvenanceVerificationError("controller signer enrollment certificate ID is invalid")
    issued_at = _timestamp(value.get("issued_at"), field="controller signer enrollment certificate issued_at")
    not_before = _timestamp(value.get("not_before"), field="controller signer enrollment certificate not_before")
    not_after = _timestamp(value.get("not_after"), field="controller signer enrollment certificate not_after")
    now = _timestamp(verification_time, field="verification time")
    if issued_at > not_before or not_before > not_after or (not_after - issued_at).total_seconds() > MAX_ENROLLMENT_CERTIFICATE_LIFETIME_SECONDS or now < not_before or now > not_after:
        raise SourceProvenanceVerificationError("controller signer enrollment certificate validity window is invalid")
    if value.get("campaign_id") != _campaign(expected_campaign_id, field="expected campaign") or value.get("source_site") != "webapp_fi" or value.get("destination_site") != "webapp_ir" or value.get("package_id") != expected_delivery["package_id"]:
        raise SourceProvenanceVerificationError("controller signer enrollment certificate binding is invalid")
    if _application(value.get("application"), field="controller signer enrollment certificate application") != _application(expected_application, field="expected application") or _tooling(value.get("tooling"), field="controller signer enrollment certificate tooling") != _tooling(expected_tooling, field="expected tooling") or _sha(value.get("canonical_release_tree_sha256"), field="controller signer enrollment certificate descriptor SHA-256") != _sha(expected_canonical_release_tree_sha256, field="expected canonical descriptor SHA-256"):
        raise SourceProvenanceVerificationError("controller signer enrollment certificate release binding is invalid")
    if _sha(value.get("delivery_envelope_sha256"), field="controller signer enrollment certificate delivery SHA-256") != expected_delivery["sha256"] or _object_descriptor(value.get("source_adoption_object"), field="controller signer enrollment certificate source object", maximum_plaintext_bytes=24 * 1024 * 1024) != expected_delivery["object"] or value.get("fi_bootstrap_recipient") != expected_delivery["fi_bootstrap_recipient"]:
        raise SourceProvenanceVerificationError("controller signer enrollment certificate delivery binding is invalid")
    _sha(value.get("source_adoption_install_receipt_sha256"), field="controller signer enrollment certificate installation receipt SHA-256")
    _sha(value.get("fi_ssh_host_public_key_sha256"), field="controller signer enrollment certificate SSH host key SHA-256")
    source_key = value.get("source_signing_public_key_base64")
    if source_key != expected_source_signing_public_key_base64 or value.get("source_signing_key_id") != public_key_id(expected_source_signing_public_key_base64) or source_key == pinned_controller_public_key_base64:
        raise SourceProvenanceVerificationError("controller signer enrollment certificate source key is invalid")
    _verify_controller_signature(
        value=value,
        signature_field="controller_signature",
        public_key_field="controller_public_key_base64",
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
        domain=SIGNER_ENROLLMENT_SIGNATURE_DOMAIN,
        field="controller signer enrollment certificate",
    )
    _controller_key_id(
        value.get("controller_key_id"),
        field="controller signer enrollment certificate controller key ID",
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
    )
    return {
        "sha256": sha256_bytes(payload),
        "certificate_id": certificate_id,
        "operation_id": operation_id,
        "issued_at": value["issued_at"],
        "not_before": value["not_before"],
        "not_after": value["not_after"],
        "source_adoption_install_receipt_sha256": value["source_adoption_install_receipt_sha256"],
        "fi_ssh_host_public_key_sha256": value["fi_ssh_host_public_key_sha256"],
        "source_signing_public_key_base64": expected_source_signing_public_key_base64,
        "source_signing_key_id": public_key_id(expected_source_signing_public_key_base64),
        "controller_key_id": public_key_id(pinned_controller_public_key_base64),
    }


def _relative_static_path(value: object) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\x00" in value:
        raise SourceProvenanceVerificationError("static asset path is invalid")
    pure = PurePosixPath(value)
    if pure.as_posix() != value or any(part in {".", ".."} for part in pure.parts):
        raise SourceProvenanceVerificationError("static asset path is invalid")
    return value


def _static_assets_provenance(
    *,
    payload: bytes,
    pinned_controller_public_key_base64: str,
    expected_campaign_id: str,
    expected_application: Mapping[str, str],
) -> dict[str, Any]:
    value = _parse(payload, field="controller static assets provenance")
    expected = {
        "schema", "status", "campaign_id", "application", "source_kind", "artifact", "files", "files_sha256",
        "controller_public_key_base64", "controller_signature",
    }
    if set(value) != expected or value.get("schema") != STATIC_ASSET_PROVENANCE_SCHEMA or value.get("status") != "verified":
        raise SourceProvenanceVerificationError("controller static assets provenance is unsupported")
    if value.get("campaign_id") != _campaign(expected_campaign_id, field="expected campaign") or _application(value.get("application"), field="controller static assets application") != _application(expected_application, field="expected application") or value.get("source_kind") != "deterministic_2c08_dist_manifest":
        raise SourceProvenanceVerificationError("controller static assets provenance binding is invalid")
    raw_files = value.get("files")
    if not isinstance(raw_files, list) or len(raw_files) > 100_000:
        raise SourceProvenanceVerificationError("controller static assets files are invalid")
    files: list[dict[str, Any]] = []
    previous = ""
    for item in raw_files:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256", "bytes"}:
            raise SourceProvenanceVerificationError("controller static assets file is invalid")
        path = _relative_static_path(item.get("path"))
        if previous and path <= previous:
            raise SourceProvenanceVerificationError("controller static assets paths are not normalized")
        previous = path
        files.append(
            {
                "path": path,
                "sha256": _sha(item.get("sha256"), field="controller static asset SHA-256"),
                "bytes": _size(item.get("bytes"), field="controller static asset bytes", maximum=100 * 1024 * 1024, minimum=0),
            }
        )
    if value.get("files_sha256") != sha256_bytes(canonical_json_bytes(files)):
        raise SourceProvenanceVerificationError("controller static assets file hash is invalid")
    _verify_controller_signature(
        value=value,
        signature_field="controller_signature",
        public_key_field="controller_public_key_base64",
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
        domain=STATIC_ASSET_SIGNATURE_DOMAIN,
        field="controller static assets provenance",
    )
    return {
        "sha256": sha256_bytes(payload),
        "artifact": _object_descriptor(
            value.get("artifact"),
            field="controller static assets artifact",
            maximum_plaintext_bytes=100 * 1024 * 1024,
        ),
        "files_sha256": value["files_sha256"],
        "file_count": len(files),
        "source_kind": value["source_kind"],
    }


def verify_webapp_fi_source_authority_payloads(
    *,
    source_role_attestation_payload: bytes,
    image_export_receipt_payload: bytes,
    controller_delivery_envelope_payload: bytes,
    signer_enrollment_certificate_payload: bytes,
    static_assets_provenance_payload: bytes,
    pinned_source_signing_public_key_base64: str,
    pinned_controller_public_key_base64: str,
    expected_campaign_id: str,
    expected_application: Mapping[str, str],
    expected_control_commit: str,
    expected_control_tree: str,
    expected_canonical_release_tree_sha256: str,
    expected_app_image_id: str,
    expected_app_image_reference: str,
    verification_time: str,
) -> dict[str, Any]:
    """Verify all source and controller authority proofs before archive adoption.

    The function is intentionally pure.  It accepts raw canonical proof bytes,
    checks both independently pinned public keys, and returns only normalized
    claims for a later controller archive-adoption receipt.
    """

    expected_tooling = {
        "control_commit": expected_control_commit,
        "control_tree": expected_control_tree,
    }
    expected_tooling = _tooling(expected_tooling, field="expected tooling")
    delivery = _controller_delivery_envelope(
        payload=controller_delivery_envelope_payload,
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
        expected_campaign_id=expected_campaign_id,
        expected_application=expected_application,
        expected_tooling=expected_tooling,
        expected_canonical_release_tree_sha256=expected_canonical_release_tree_sha256,
    )
    attestation = verify_source_role_attestation_payload(
        payload=source_role_attestation_payload,
        pinned_source_signing_public_key_base64=pinned_source_signing_public_key_base64,
        expected_campaign_id=expected_campaign_id,
        expected_application=expected_application,
        expected_control_commit=expected_control_commit,
        expected_canonical_release_tree_sha256=expected_canonical_release_tree_sha256,
        expected_app_image_id=expected_app_image_id,
        expected_app_image_reference=expected_app_image_reference,
        verification_time=verification_time,
    )
    if attestation["tooling"] != expected_tooling or attestation["source_adoption_delivery_claim"] != {
        **delivery["object"],
        "delivery_envelope_sha256": delivery["sha256"],
        "controller_public_key_base64": pinned_controller_public_key_base64,
    }:
        raise SourceProvenanceVerificationError("source role attestation does not bind the controller delivery")
    certificate = _signer_enrollment_certificate(
        payload=signer_enrollment_certificate_payload,
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
        expected_campaign_id=expected_campaign_id,
        expected_application=expected_application,
        expected_tooling=expected_tooling,
        expected_canonical_release_tree_sha256=expected_canonical_release_tree_sha256,
        expected_delivery=delivery,
        expected_source_signing_public_key_base64=pinned_source_signing_public_key_base64,
        verification_time=verification_time,
    )
    enrollment = attestation["source_signer_enrollment_claim"]
    if (
        attestation["package_id"] != delivery["package_id"]
        or attestation["source_adoption_install_receipt_sha256"]
        != certificate["source_adoption_install_receipt_sha256"]
    ):
        raise SourceProvenanceVerificationError(
            "source role attestation is not bound to the controller-authorized source installation"
        )
    expected_enrollment = {
        "receipt_sha256": enrollment.get("receipt_sha256"),
        "certificate_sha256": certificate["sha256"],
        "certificate_id": certificate["certificate_id"],
        "operation_id": certificate["operation_id"],
        "certificate_consumption_sha256": enrollment.get("certificate_consumption_sha256"),
        "not_after": certificate["not_after"],
        "fi_ssh_host_public_key_sha256": certificate["fi_ssh_host_public_key_sha256"],
        "controller_key_id": certificate["controller_key_id"],
        "source_signing_public_key_base64": certificate["source_signing_public_key_base64"],
        "source_signing_key_id": certificate["source_signing_key_id"],
    }
    if enrollment != expected_enrollment:
        raise SourceProvenanceVerificationError("source role attestation signer enrollment is not controller-authorized")
    attested_at = _timestamp(attestation["attested_at"], field="source role attestation timestamp")
    if (
        _timestamp(certificate["issued_at"], field="certificate issued_at") > attested_at
        or attested_at < _timestamp(certificate["not_before"], field="certificate not_before")
        or attested_at > _timestamp(certificate["not_after"], field="certificate not_after")
    ):
        raise SourceProvenanceVerificationError("source role attestation is outside the controller certificate window")
    static = _static_assets_provenance(
        payload=static_assets_provenance_payload,
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
        expected_campaign_id=expected_campaign_id,
        expected_application=expected_application,
    )
    if attestation["runtime_claim"]["static_assets"] != {
        "descriptor_sha256": static["sha256"],
        "artifact": static["artifact"],
        "files_sha256": static["files_sha256"],
        "file_count": static["file_count"],
        "source_kind": static["source_kind"],
    }:
        raise SourceProvenanceVerificationError("source role attestation static assets are not controller-authorized")
    image_export = verify_image_export_receipt_payload(
        payload=image_export_receipt_payload,
        pinned_source_signing_public_key_base64=pinned_source_signing_public_key_base64,
        expected_campaign_id=expected_campaign_id,
        expected_application=expected_application,
        expected_control_commit=expected_control_commit,
        expected_application_release_tree=attestation["descriptor_claim"]["application_release_tree"],
        expected_canonical_release_tree_sha256=expected_canonical_release_tree_sha256,
        expected_attestation_sha256=attestation["attestation_sha256"],
        expected_app_image_id=expected_app_image_id,
        expected_app_image_reference=expected_app_image_reference,
        verification_time=verification_time,
    )
    if image_export["tooling"] != expected_tooling or image_export["source_signer_enrollment_claim"] != enrollment or image_export["runtime_claim"]["static_assets"] != attestation["runtime_claim"]["static_assets"]:
        raise SourceProvenanceVerificationError("image export receipt does not bind the full authorized source state")
    exported_at = _timestamp(image_export["exported_at"], field="image export timestamp")
    if (
        exported_at < attested_at
        or exported_at < _timestamp(certificate["not_before"], field="certificate not_before")
        or exported_at > _timestamp(certificate["not_after"], field="certificate not_after")
    ):
        raise SourceProvenanceVerificationError("image export is outside the controller certificate window")
    return {
        "status": "verified",
        "campaign_id": expected_campaign_id,
        "application": _application(expected_application, field="expected application"),
        "tooling": expected_tooling,
        "canonical_release_tree_sha256": _sha(expected_canonical_release_tree_sha256, field="expected canonical descriptor SHA-256"),
        "attestation": attestation,
        "image_export": image_export,
        "delivery": delivery,
        "certificate": certificate,
        "static_assets": static,
        "proof_sha256": {
            "source_role_attestation": attestation["attestation_sha256"],
            "image_export_receipt": image_export["image_export_receipt_sha256"],
            "controller_delivery_envelope": delivery["sha256"],
            "signer_enrollment_certificate": certificate["sha256"],
            "static_assets_provenance": static["sha256"],
        },
    }


def verify_controller_image_adoption_receipt_payload(
    *,
    payload: bytes,
    authority: Mapping[str, Any],
    pinned_controller_public_key_base64: str,
    expected_image_bundle_sha256: str,
    expected_image_bundle_bytes: int,
    expected_image_manifest_sha256: str,
    expected_image_manifest_bytes: int,
    verification_time: str,
) -> dict[str, Any]:
    """Verify the controller-signed hand-off from raw FI bytes to isolated tags."""

    value = _parse(payload, field="controller image adoption receipt")
    expected = {
        "schema", "status", "adopted_at", "campaign_id", "source_site", "destination_site", "application", "tooling",
        "canonical_release_tree_sha256", "proof_sha256", "source_image", "source_image_object", "source_image_transport", "controller_image_artifacts",
        "archive_contract", "controller_public_key_base64", "controller_key_id", "controller_signature",
    }
    if set(value) != expected or value.get("schema") != IMAGE_ADOPTION_RECEIPT_SCHEMA or value.get("status") != "adopted":
        raise SourceProvenanceVerificationError("controller image adoption receipt is unsupported")
    adopted_at = _fresh_timestamp(
        value.get("adopted_at"),
        field="controller image adoption timestamp",
        verification_time=verification_time,
        maximum_age_seconds=MAX_OBSERVATION_AGE_SECONDS,
    )
    if value.get("campaign_id") != authority.get("campaign_id") or value.get("source_site") != "webapp_fi" or value.get("destination_site") != "webapp_ir" or _application(value.get("application"), field="controller image adoption application") != authority.get("application") or _tooling(value.get("tooling"), field="controller image adoption tooling") != authority.get("tooling") or _sha(value.get("canonical_release_tree_sha256"), field="controller image adoption descriptor SHA-256") != authority.get("canonical_release_tree_sha256"):
        raise SourceProvenanceVerificationError("controller image adoption receipt binding is invalid")
    image_export = authority.get("image_export")
    if not isinstance(image_export, Mapping) or _timestamp(adopted_at, field="controller image adoption timestamp") < _timestamp(
        image_export.get("exported_at"), field="source image export timestamp"
    ):
        raise SourceProvenanceVerificationError("controller image adoption receipt predates the source image export")
    proof_hashes = value.get("proof_sha256")
    if not isinstance(proof_hashes, Mapping) or dict(proof_hashes) != authority.get("proof_sha256"):
        raise SourceProvenanceVerificationError("controller image adoption receipt proof hashes are invalid")
    source_image = value.get("source_image")
    expected_source_image = image_export.get("image_claim")
    if not isinstance(source_image, Mapping) or set(source_image) != {"image_id", "image_reference", "docker_save_archive_sha256", "docker_save_archive_bytes"} or not isinstance(expected_source_image, Mapping) or source_image != {
        "image_id": expected_source_image.get("image_id"),
        "image_reference": expected_source_image.get("image_reference"),
        "docker_save_archive_sha256": expected_source_image.get("docker_save_archive_sha256"),
        "docker_save_archive_bytes": expected_source_image.get("docker_save_archive_bytes"),
    }:
        raise SourceProvenanceVerificationError("controller image adoption receipt raw source image is invalid")
    source_image_object = _object_descriptor(
        value.get("source_image_object"),
        field="controller image adoption raw source object",
        maximum_plaintext_bytes=100 * 1024 * 1024 * 1024,
    )
    if (
        source_image_object["plaintext_sha256"] != source_image["docker_save_archive_sha256"]
        or source_image_object["plaintext_bytes"] != source_image["docker_save_archive_bytes"]
        or value.get("source_image_transport")
        != {
            "transport": "private_versioned_age_only",
            "create_only": True,
            "read_back_same_version_id": True,
            "provider_side_sse": False,
        }
    ):
        raise SourceProvenanceVerificationError("controller image adoption raw source transport is invalid")
    artifacts = value.get("controller_image_artifacts")
    expected_artifact_fields = {
        "image_bundle_sha256", "image_bundle_bytes", "image_manifest_sha256", "image_manifest_bytes",
        "image_set_sha256", "image_ids_sha256", "app_image_id", "app_image_archive_tag",
    }
    if not isinstance(artifacts, Mapping) or set(artifacts) != expected_artifact_fields or _sha(artifacts.get("image_bundle_sha256"), field="controller image bundle SHA-256") != _sha(expected_image_bundle_sha256, field="expected image bundle SHA-256") or _size(artifacts.get("image_bundle_bytes"), field="controller image bundle bytes", maximum=100 * 1024 * 1024 * 1024) != _size(expected_image_bundle_bytes, field="expected image bundle bytes", maximum=100 * 1024 * 1024 * 1024) or _sha(artifacts.get("image_manifest_sha256"), field="controller image manifest SHA-256") != _sha(expected_image_manifest_sha256, field="expected image manifest SHA-256") or _size(artifacts.get("image_manifest_bytes"), field="controller image manifest bytes", maximum=1024 * 1024) != _size(expected_image_manifest_bytes, field="expected image manifest bytes", maximum=1024 * 1024):
        raise SourceProvenanceVerificationError("controller image adoption receipt artifact binding is invalid")
    try:
        expected_archive_tag = image_contract.require_canonical_archive_tag(
            artifacts.get("app_image_archive_tag"),
            campaign_id=str(authority["campaign_id"]),
            release_sha=str(authority["application"]["release_sha"]),
            image_id=str(source_image["image_id"]),
        )
    except (KeyError, TypeError, image_contract.ImageArchiveContractError) as exc:
        raise SourceProvenanceVerificationError(
            "controller image adoption receipt application image is invalid"
        ) from exc
    if artifacts.get("app_image_id") != source_image["image_id"] or artifacts.get("app_image_archive_tag") != expected_archive_tag:
        raise SourceProvenanceVerificationError("controller image adoption receipt application image is invalid")
    _sha(artifacts.get("image_set_sha256"), field="controller image set SHA-256")
    _sha(artifacts.get("image_ids_sha256"), field="controller image IDs SHA-256")
    if value.get("archive_contract") != {
        "raw_source_archive_loadability_claimed": False,
        "raw_source_archive_semantics": "exact_bytes_only_unparsed",
        "controller_output_tags_isolated": True,
        "controller_docker_load_invoked": False,
    }:
        raise SourceProvenanceVerificationError("controller image adoption receipt archive contract is invalid")
    _verify_controller_signature(
        value=value,
        signature_field="controller_signature",
        public_key_field="controller_public_key_base64",
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
        domain=IMAGE_ADOPTION_SIGNATURE_DOMAIN,
        field="controller image adoption receipt",
    )
    _controller_key_id(
        value.get("controller_key_id"),
        field="controller image adoption receipt controller key ID",
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
    )
    return {
        "status": "verified",
        "image_adoption_receipt_sha256": sha256_bytes(payload),
        "adopted_at": adopted_at,
        "source_image_object": source_image_object,
        "controller_image_artifacts": dict(artifacts),
    }


def verify_composite_webapp_fi_source_provenance(
    *,
    source_role_attestation_payload: bytes,
    image_export_receipt_payload: bytes,
    controller_delivery_envelope_payload: bytes,
    signer_enrollment_certificate_payload: bytes,
    static_assets_provenance_payload: bytes,
    controller_image_adoption_receipt_payload: bytes,
    pinned_source_signing_public_key_base64: str,
    pinned_controller_public_key_base64: str,
    expected_campaign_id: str,
    expected_application: Mapping[str, str],
    expected_control_commit: str,
    expected_control_tree: str,
    expected_canonical_release_tree_sha256: str,
    expected_app_image_id: str,
    expected_app_image_reference: str,
    expected_image_bundle_sha256: str,
    expected_image_bundle_bytes: int,
    expected_image_manifest_sha256: str,
    expected_image_manifest_bytes: int,
    verification_time: str,
) -> dict[str, Any]:
    """Verify the complete six-proof controller-to-WA-IR adoption chain."""

    authority = verify_webapp_fi_source_authority_payloads(
        source_role_attestation_payload=source_role_attestation_payload,
        image_export_receipt_payload=image_export_receipt_payload,
        controller_delivery_envelope_payload=controller_delivery_envelope_payload,
        signer_enrollment_certificate_payload=signer_enrollment_certificate_payload,
        static_assets_provenance_payload=static_assets_provenance_payload,
        pinned_source_signing_public_key_base64=pinned_source_signing_public_key_base64,
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
        expected_campaign_id=expected_campaign_id,
        expected_application=expected_application,
        expected_control_commit=expected_control_commit,
        expected_control_tree=expected_control_tree,
        expected_canonical_release_tree_sha256=expected_canonical_release_tree_sha256,
        expected_app_image_id=expected_app_image_id,
        expected_app_image_reference=expected_app_image_reference,
        verification_time=verification_time,
    )
    adoption = verify_controller_image_adoption_receipt_payload(
        payload=controller_image_adoption_receipt_payload,
        authority=authority,
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
        expected_image_bundle_sha256=expected_image_bundle_sha256,
        expected_image_bundle_bytes=expected_image_bundle_bytes,
        expected_image_manifest_sha256=expected_image_manifest_sha256,
        expected_image_manifest_bytes=expected_image_manifest_bytes,
        verification_time=verification_time,
    )
    return {
        "status": "verified",
        "authority": authority,
        "image_adoption": adoption,
        "proof_sha256": {
            **authority["proof_sha256"],
            "controller_image_adoption_receipt": adoption["image_adoption_receipt_sha256"],
        },
    }
