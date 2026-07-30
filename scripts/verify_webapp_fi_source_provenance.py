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
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Mapping, Sequence


ATTESTATION_SCHEMA = "gold-trade-webapp-fi-source-role-attestation-v1"
IMAGE_EXPORT_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-image-export-receipt-v1"
ATTESTATION_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-source-role-attestation-v1\x00"
IMAGE_EXPORT_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-source-image-export-v1\x00"

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
ALEMBIC_RE = re.compile(r"^[0-9a-f]{12}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:-]{0,511}$")
OBJECT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/=-]{2,1023}$")
CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
PACKAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
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


def _parse(payload: bytes, *, field: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
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


def _timestamp(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z") or "T" not in value:
        raise SourceProvenanceVerificationError(f"{field} is invalid")
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
    expected_app_image_id: str,
    expected_app_image_reference: str,
) -> dict[str, Any]:
    """Verify a complete FI runtime proof without accessing FI state."""

    value = _parse(payload, field="WebApp-FI source role attestation")
    expected = {"schema", "status", "attested_at", "campaign_id", "source_site", "destination_site", "package_id", "application", "application_release_tree", "tooling", "source_adoption_install_receipt_sha256", "source_adoption_delivery", "canonical_release_tree_sha256", "source_signer_enrollment", "runtime_projection", "static_assets_proof", "containers", "active_application_image", "schema_observation", "race_check", "snapshot_transport", "source_signing_public_key_base64", "source_signing_key_id", "source_signature"}
    if set(value) != expected or value.get("schema") != ATTESTATION_SCHEMA or value.get("status") != "attested":
        raise SourceProvenanceVerificationError("WebApp-FI source role attestation is unsupported")
    _timestamp(value.get("attested_at"), field="attestation timestamp")
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
    enrollment = value.get("source_signer_enrollment")
    if not isinstance(enrollment, Mapping) or set(enrollment) != {"receipt_sha256", "certificate_sha256", "fi_ssh_host_public_key_sha256", "controller_public_key_base64", "source_signing_public_key_base64"}:
        raise SourceProvenanceVerificationError("source signer enrollment is invalid")
    for name in ("receipt_sha256", "certificate_sha256", "fi_ssh_host_public_key_sha256"):
        _sha(enrollment.get(name), field=f"source signer enrollment {name}")
    _key(enrollment.get("controller_public_key_base64"), field="source signer controller key")
    if enrollment.get("source_signing_public_key_base64") != pinned_source_signing_public_key_base64 or value.get("source_signing_public_key_base64") != pinned_source_signing_public_key_base64 or value.get("source_signing_key_id") != public_key_id(pinned_source_signing_public_key_base64):
        raise SourceProvenanceVerificationError("source signing key is not pinned")
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
    if not isinstance(containers, Mapping) or set(containers) != {"database", "application", "sync_worker"}:
        raise SourceProvenanceVerificationError("attestation containers are invalid")
    database, app, sync = _container(containers["database"]), _container(containers["application"]), _container(containers["sync_worker"])
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
    if value.get("schema_observation") != {"observed_alembic_revision": application["expected_alembic_revision"], "capture_role_verified_read_only": True}:
        raise SourceProvenanceVerificationError("schema observation is invalid")
    if value.get("race_check") != {"runtime_projection_unchanged": True, "static_assets_unchanged": True, "application_container_unchanged": True, "sync_worker_container_unchanged": True, "database_container_unchanged": True, "active_image_unchanged": True, "schema_unchanged": True}:
        raise SourceProvenanceVerificationError("race check is invalid")
    if value.get("snapshot_transport") != {"payload_path": "private_versioned_object_storage_age_only", "one_off_publication_only": True, "direct_webapp_fi_to_webapp_ir_transfer": False, "automatic_deletion": False}:
        raise SourceProvenanceVerificationError("snapshot transport is invalid")
    _verify_signature(unsigned={key: item for key, item in value.items() if key != "source_signature"}, signature=value["source_signature"], public_key_base64=pinned_source_signing_public_key_base64, domain=ATTESTATION_SIGNATURE_DOMAIN)
    return {"status": "verified", "attestation_sha256": sha256_bytes(payload), "campaign_id": expected_campaign_id, "application": application, "application_release_tree": value["application_release_tree"], "tooling": tooling, "source_adoption_delivery": delivery, "canonical_release_tree_sha256": descriptor_sha, "runtime_image": {"image_id": expected_app_image_id, "image_reference": expected_app_image_reference}, "source_signing_key_id": value["source_signing_key_id"], "source_signing_public_key_base64": pinned_source_signing_public_key_base64, "containers": {"database": database, "application": app, "sync_worker": sync}}


def verify_image_export_receipt_payload(
    *,
    payload: bytes,
    pinned_source_signing_public_key_base64: str,
    expected_campaign_id: str,
    expected_application: Mapping[str, str],
    expected_control_commit: str,
    expected_application_release_tree: str,
    expected_attestation_sha256: str,
    expected_app_image_id: str,
    expected_app_image_reference: str,
) -> dict[str, Any]:
    """Verify FI's local image-export receipt without Docker loading it."""

    value = _parse(payload, field="WebApp-FI image export receipt")
    expected = {"schema", "status", "exported_at", "export_id", "campaign_id", "source_site", "destination_site", "application", "application_release_tree", "tooling", "canonical_release_tree_sha256", "source_role_attestation_sha256", "image", "pre_export_runtime", "post_export_runtime", "image_archive_does_not_prove_bind_mounted_runtime", "archive_consumption", "object_storage_export_required", "source_signing_public_key_base64", "source_signing_key_id", "source_signature"}
    if set(value) != expected or value.get("schema") != IMAGE_EXPORT_RECEIPT_SCHEMA or value.get("status") != "exported":
        raise SourceProvenanceVerificationError("WebApp-FI image export receipt is unsupported")
    _timestamp(value.get("exported_at"), field="image export timestamp")
    if not isinstance(value.get("export_id"), str) or not PACKAGE_ID_RE.fullmatch(value["export_id"]) or value.get("campaign_id") != _campaign(expected_campaign_id, field="expected campaign") or value.get("source_site") != "webapp_fi" or value.get("destination_site") != "webapp_ir" or _application(value.get("application"), field="image export application") != _application(expected_application, field="expected application"):
        raise SourceProvenanceVerificationError("image export binding is invalid")
    tooling = _tooling(value.get("tooling"), field="image export tooling")
    if tooling["control_commit"] != expected_control_commit or not COMMIT_RE.fullmatch(expected_application_release_tree) or value.get("application_release_tree") != expected_application_release_tree or _sha(value.get("source_role_attestation_sha256"), field="image export attestation sha256") != _sha(expected_attestation_sha256, field="expected attestation sha256"):
        raise SourceProvenanceVerificationError("image export provenance binding is invalid")
    _sha(value.get("canonical_release_tree_sha256"), field="image export canonical descriptor sha256")
    image = value.get("image")
    image_fields = {"image_id", "image_reference", "archive_sha256", "archive_bytes", "docker_manifest_sha256", "docker_config_sha256", "layer_count", "repo_tags"}
    if not isinstance(image, Mapping) or set(image) != image_fields or image.get("image_id") != expected_app_image_id or image.get("image_reference") != expected_app_image_reference:
        raise SourceProvenanceVerificationError("image export image binding is invalid")
    for name in ("archive_sha256", "docker_manifest_sha256", "docker_config_sha256"):
        _sha(image.get(name), field=f"image export {name}")
    _size(image.get("archive_bytes"), field="image export archive bytes", maximum=100 * 1024 * 1024 * 1024)
    _size(image.get("layer_count"), field="image export layer count", maximum=100_000)
    if not isinstance(image.get("repo_tags"), list) or not all(isinstance(tag, str) and IMAGE_REFERENCE_RE.fullmatch(tag) for tag in image["repo_tags"]):
        raise SourceProvenanceVerificationError("image export repo tags are invalid")
    for runtime_name in ("pre_export_runtime", "post_export_runtime"):
        runtime = value.get(runtime_name)
        if not isinstance(runtime, Mapping) or set(runtime) != {"application", "sync_worker", "active_image"}:
            raise SourceProvenanceVerificationError("image export runtime proof is invalid")
        app, sync = _container(runtime["application"]), _container(runtime["sync_worker"])
        if app["image_id"] != expected_app_image_id or app["image_reference"] != expected_app_image_reference or sync["image_id"] != expected_app_image_id or sync["image_reference"] != expected_app_image_reference:
            raise SourceProvenanceVerificationError("image export runtime image is invalid")
    if value["pre_export_runtime"] != value["post_export_runtime"] or value.get("image_archive_does_not_prove_bind_mounted_runtime") is not True:
        raise SourceProvenanceVerificationError("image export runtime race proof is invalid")
    if value.get("archive_consumption") != {"docker_load_prohibited": True, "fi_local_archive_verification_before_age_encryption": True, "controller_read_back_verification_after_age_encryption": True, "raw_repo_tags_are_not_authorization": True} or value.get("object_storage_export_required") != {"transport": "private_versioned_age_only", "create_only": True, "read_back_same_version_id": True, "direct_webapp_fi_to_webapp_ir_transfer": False}:
        raise SourceProvenanceVerificationError("image export transport policy is invalid")
    if value.get("source_signing_public_key_base64") != pinned_source_signing_public_key_base64 or value.get("source_signing_key_id") != public_key_id(pinned_source_signing_public_key_base64):
        raise SourceProvenanceVerificationError("image export signing key is not pinned")
    _verify_signature(unsigned={key: item for key, item in value.items() if key != "source_signature"}, signature=value["source_signature"], public_key_base64=pinned_source_signing_public_key_base64, domain=IMAGE_EXPORT_SIGNATURE_DOMAIN)
    return {"status": "verified", "image_export_receipt_sha256": sha256_bytes(payload), "campaign_id": expected_campaign_id, "application": _application(value["application"], field="image export application"), "tooling": tooling, "image": dict(image), "source_role_attestation_sha256": value["source_role_attestation_sha256"], "source_signing_key_id": value["source_signing_key_id"]}
