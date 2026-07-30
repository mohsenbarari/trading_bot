#!/usr/bin/env python3
"""Locally adopt one exact WebApp-FI image archive for a WA-IR stage.

This is deliberately a controller-local file transformation.  It never
contacts Object Storage, invokes ``age``, calls Docker, starts a process, or
changes a service.  Its raw input must already be the plaintext obtained from
one private, versioned, create-only Object Storage object after the controller
has verified the exact ciphertext version and decrypted it with its root-only
age identity.  The accompanying root-only read-back record makes that trust
boundary explicit; this helper verifies the resulting plaintext bytes again.

The FI export is the application image only.  A caller must therefore provide
one already-verified, controller-local Docker archive and manifest containing
the two explicitly pinned supplemental runtime images (currently PostgreSQL
and Redis), and repeat their trusted local ``REF=IMAGE_ID`` inventory values.
The helper structurally merges those isolated supplemental images with a
rewritten FI application archive.  It never silently emits an
application-only bundle that could not support the standby runtime.

All outputs are fresh root-only files below a new root-only candidate
directory.  They are suitable as the ``image-bundle`` and ``image-manifest``
inputs to the later five-artifact stage, plus the signed v2 controller image
adoption receipt and the local source-provenance input consumed by the control
artifact builder.
"""

from __future__ import annotations

import argparse
import base64
import copy
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
from typing import Any, Mapping, Sequence


def _load_sibling_module(name: str) -> Any:
    """Load a co-shipped helper by path, never from an ambient PYTHONPATH."""

    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location("_controller_image_adoption_" + name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


portable = _load_sibling_module("verify_webapp_fi_source_provenance")
preparer = _load_sibling_module("prepare_webapp_ir_artifact_bundle")


SOURCE_IMAGE_READBACK_SCHEMA = "gold-trade-webapp-fi-source-image-readback-v1"
SOURCE_PROVENANCE_INPUT_SCHEMA = "gold-trade-wa-ir-webapp-fi-source-provenance-input-v1"
IMAGE_BUNDLE_NAME = "images.tar"
IMAGE_MANIFEST_NAME = "image-manifest.json"
ADOPTION_RECEIPT_NAME = "controller-image-adoption-receipt.json"
SOURCE_PROVENANCE_INPUT_NAME = "webapp-fi-source-provenance-input.json"

MAXIMUM_ARTIFACT_BYTES = 100 * 1024 * 1024 * 1024
MAXIMUM_PROOF_BYTES = 8 * 1024 * 1024
REQUIRED_SUPPLEMENTAL_IMAGE_COUNT = 2

SOURCE_IMAGE_TRANSPORT = {
    "transport": "private_versioned_age_only",
    "create_only": True,
    "read_back_same_version_id": True,
    "provider_side_sse": False,
}
SOURCE_IMAGE_AGE_DECRYPTION = {
    "algorithm": "age-v1",
    "controller_identity_scope": "root_only",
    "ciphertext_sha256_verified_before_decrypt": True,
    "plaintext_sha256_verified_after_decrypt": True,
}
ARCHIVE_CONTRACT = {
    "raw_source_archive_loadability_claimed": False,
    "raw_source_archive_semantics": "exact_bytes_only_unparsed",
    "controller_output_tags_isolated": True,
    "controller_docker_load_invoked": False,
}
PROOF_NAMES = (
    "source_role_attestation",
    "image_export_receipt",
    "controller_delivery_envelope",
    "signer_enrollment_certificate",
    "static_assets_provenance",
)


class ControllerImageAdoptionError(RuntimeError):
    """The local FI image adoption boundary could not be proven safe."""


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> tuple[str, int]:
    return preparer.sha256_file(path)


def utc_iso(value: dt.datetime | None = None) -> str:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        raise ControllerImageAdoptionError("adoption timestamp must be timezone-aware")
    return current.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise ControllerImageAdoptionError("controller image adoption must run as root")


def _require_safe_directory_ancestors(path: Path, *, field: str) -> None:
    """Reject path traversal through an attacker-replaceable directory.

    A root-owned sticky directory such as ``/tmp`` is safe for an already
    root-owned descendant: an unprivileged account cannot replace or remove
    that descendant.  Any non-sticky group/other writable ancestor is not an
    acceptable controller workspace boundary.
    """

    if not path.is_absolute():
        raise ControllerImageAdoptionError(f"{field} must be an absolute path")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise ControllerImageAdoptionError(f"cannot inspect {field} ancestor") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
        ):
            raise ControllerImageAdoptionError(f"{field} has an unsafe ancestor")
        mode = stat.S_IMODE(metadata.st_mode)
        if current == path:
            if mode & 0o077:
                raise ControllerImageAdoptionError(f"{field} must be root-only")
        elif mode & 0o022 and not metadata.st_mode & stat.S_ISVTX:
            raise ControllerImageAdoptionError(f"{field} has a writable non-sticky ancestor")


def _require_private_directory(path: Path, *, field: str) -> Path:
    if not path.is_absolute():
        raise ControllerImageAdoptionError(f"{field} must be an absolute path")
    _require_safe_directory_ancestors(path, field=field)
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
        resolved_metadata = resolved.lstat()
    except OSError as exc:
        raise ControllerImageAdoptionError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_ISLNK(resolved_metadata.st_mode)
        or not stat.S_ISDIR(resolved_metadata.st_mode)
        or resolved_metadata.st_uid != 0
        or stat.S_IMODE(resolved_metadata.st_mode) & 0o077
    ):
        raise ControllerImageAdoptionError(f"{field} must be one root-only non-symlink directory")
    return resolved


def _require_private_file(path: Path, *, field: str, maximum_bytes: int) -> Path:
    if not path.is_absolute():
        raise ControllerImageAdoptionError(f"{field} must be an absolute path")
    _require_private_directory(path.parent, field=f"{field} parent")
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
        resolved_metadata = resolved.lstat()
    except OSError as exc:
        raise ControllerImageAdoptionError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_ISLNK(resolved_metadata.st_mode)
        or not stat.S_ISREG(resolved_metadata.st_mode)
        or resolved_metadata.st_uid != 0
        or stat.S_IMODE(resolved_metadata.st_mode) & 0o077
        or resolved_metadata.st_nlink != 1
        or not 1 <= resolved_metadata.st_size <= maximum_bytes
    ):
        raise ControllerImageAdoptionError(f"{field} must be a bounded root-only non-symlink file")
    return resolved


def _read_canonical_private_json(path: Path, *, field: str, maximum_bytes: int = MAXIMUM_PROOF_BYTES) -> tuple[dict[str, Any], bytes]:
    path = _require_private_file(path, field=field, maximum_bytes=maximum_bytes)
    try:
        payload = path.read_bytes()
        value = portable._parse(payload, field=field)
    except Exception as exc:
        raise ControllerImageAdoptionError(f"{field} is invalid") from exc
    return value, payload


def _require_source_image_object(value: object, *, field: str) -> dict[str, Any]:
    try:
        return portable._object_descriptor(
            value,
            field=field,
            maximum_plaintext_bytes=MAXIMUM_ARTIFACT_BYTES,
        )
    except Exception as exc:
        raise ControllerImageAdoptionError(f"{field} is invalid") from exc


def _load_source_image_readback(
    *,
    path: Path,
    campaign_id: str,
) -> dict[str, Any]:
    """Load the local assertion made after exact S3 read-back and age decrypt.

    This helper intentionally cannot itself prove an S3 read-back or invoke
    age.  It only accepts this narrow, URL-free root-only hand-off record, and
    then verifies the plaintext archive bytes against both this object record
    and the FI-signed export receipt.
    """

    value, _payload = _read_canonical_private_json(path, field="source image read-back record")
    expected = {
        "schema",
        "status",
        "campaign_id",
        "source_site",
        "consumer_site",
        "object",
        "transport",
        "age_decryption",
    }
    if (
        set(value) != expected
        or value.get("schema") != SOURCE_IMAGE_READBACK_SCHEMA
        or value.get("status") != "read_back"
        or value.get("campaign_id") != campaign_id
        or value.get("source_site") != "webapp_fi"
        or value.get("consumer_site") != "controller"
        or value.get("transport") != SOURCE_IMAGE_TRANSPORT
        or value.get("age_decryption") != SOURCE_IMAGE_AGE_DECRYPTION
    ):
        raise ControllerImageAdoptionError("source image read-back record is unsupported")
    return _require_source_image_object(value.get("object"), field="source image read-back object")


def _load_controller_signer(path: Path) -> tuple[Any, str]:
    path = _require_private_file(path, field="controller image-adoption signing private key", maximum_bytes=32)
    raw = path.read_bytes()
    if len(raw) != 32:
        raise ControllerImageAdoptionError("controller image-adoption signing private key must contain exactly 32 bytes")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        signer = Ed25519PrivateKey.from_private_bytes(raw)
        public = signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    except (ImportError, ValueError) as exc:
        raise ControllerImageAdoptionError("controller image-adoption signing key is invalid") from exc
    return signer, base64.b64encode(public).decode("ascii")


def _validate_authority(
    *,
    proof_payloads: Mapping[str, bytes],
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
    try:
        authority = portable.verify_webapp_fi_source_authority_payloads(
            source_role_attestation_payload=proof_payloads["source_role_attestation"],
            image_export_receipt_payload=proof_payloads["image_export_receipt"],
            controller_delivery_envelope_payload=proof_payloads["controller_delivery_envelope"],
            signer_enrollment_certificate_payload=proof_payloads["signer_enrollment_certificate"],
            static_assets_provenance_payload=proof_payloads["static_assets_provenance"],
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
        if not isinstance(authority, Mapping):
            raise TypeError
        image_export = authority.get("image_export")
        if not isinstance(image_export, Mapping):
            raise TypeError
        image_claim = image_export.get("image_claim")
        required_image_fields = {
            "image_id",
            "image_reference",
            "docker_save_archive_sha256",
            "docker_save_archive_bytes",
        }
        if not isinstance(image_claim, Mapping) or not required_image_fields.issubset(image_claim):
            raise TypeError
        portable._sha(image_claim.get("docker_save_archive_sha256"), field="source image archive SHA-256")
        portable._size(
            image_claim.get("docker_save_archive_bytes"),
            field="source image archive bytes",
            maximum=MAXIMUM_ARTIFACT_BYTES,
        )
        portable._timestamp(image_export.get("exported_at"), field="source image export timestamp")
        portable._timestamp(verification_time, field="controller adoption timestamp")
        if portable._timestamp(verification_time, field="controller adoption timestamp") < portable._timestamp(
            image_export.get("exported_at"), field="source image export timestamp"
        ):
            raise ValueError
        proof_sha256 = authority.get("proof_sha256")
        if not isinstance(proof_sha256, Mapping) or set(proof_sha256) != set(PROOF_NAMES):
            raise TypeError
        for name in PROOF_NAMES:
            portable._sha(proof_sha256.get(name), field=f"authority proof {name}")
    except Exception as exc:
        raise ControllerImageAdoptionError("WebApp-FI source authority proofs are invalid") from exc
    return dict(authority)


def _inspect_raw_source_image(
    *,
    raw_path: Path,
    source_image: Mapping[str, Any],
    source_object: Mapping[str, Any],
    campaign_id: str,
    release_sha: str,
) -> tuple[Path, preparer.PreparedImage, preparer.PreparedImage]:
    """Bind raw bytes, then prepare separate rewrite and output descriptors.

    Raw ``RepoTags`` have no authorization meaning in this flow.  They are
    parsed only so the existing structural rewriter can safely strip them.  A
    source export made by Docker image ID may legitimately have no tags.
    """

    try:
        expected_sha256 = portable._sha(
            source_image.get("docker_save_archive_sha256"), field="source image archive SHA-256"
        )
        expected_bytes = portable._size(
            source_image.get("docker_save_archive_bytes"),
            field="source image archive bytes",
            maximum=MAXIMUM_ARTIFACT_BYTES,
        )
        image_id = preparer.require_image_id(source_image.get("image_id"), field="source image ID")
        source_ref = preparer.require_image_reference(
            source_image.get("image_reference"), field="source image reference"
        )
    except Exception as exc:
        raise ControllerImageAdoptionError("source image claim is invalid") from exc
    if (
        source_object.get("plaintext_sha256") != expected_sha256
        or source_object.get("plaintext_bytes") != expected_bytes
    ):
        raise ControllerImageAdoptionError("source image read-back object does not bind the signed raw archive")
    raw_path = _require_private_file(raw_path, field="age-decrypted source image archive", maximum_bytes=MAXIMUM_ARTIFACT_BYTES)
    if sha256_file(raw_path) != (expected_sha256, expected_bytes):
        raise ControllerImageAdoptionError("age-decrypted source image archive differs from the signed raw archive")
    try:
        inspection = preparer.inspect_docker_image_archive(path=raw_path)
    except Exception as exc:
        raise ControllerImageAdoptionError("age-decrypted source image archive is structurally unsafe") from exc
    if len(inspection.entries) != 1 or inspection.entries[0].image_id != image_id:
        raise ControllerImageAdoptionError("source image archive does not contain exactly the signed application image")
    try:
        archive_tag = preparer.image_contract.canonical_archive_tag(
            campaign_id=campaign_id,
            release_sha=release_sha,
            image_id=image_id,
        )
    except Exception as exc:  # pragma: no cover - caller injects validated values.
        raise ControllerImageAdoptionError("source image archive cannot receive an isolated tag") from exc
    raw_tags = inspection.entries[0].repo_tags
    # ``rewrite_docker_image_archive_tags`` normally proves a local Docker
    # source reference.  Here the FI signature and exact raw hash are that
    # authority.  Give the rewriter the observed safe tags solely to strip
    # them, or explicitly permit a tagless image-ID export.
    rewrite_image = preparer.PreparedImage(
        source_ref=raw_tags[0] if raw_tags else source_ref,
        image_id=image_id,
        repo_digests=(),
        repo_tags=tuple(raw_tags),
        size_bytes=expected_bytes,
        archive_tag=archive_tag,
    )
    manifest_image = preparer.PreparedImage(
        source_ref=source_ref,
        image_id=image_id,
        repo_digests=(source_ref,) if "@" in source_ref else (),
        repo_tags=(),
        size_bytes=expected_bytes,
        archive_tag=archive_tag,
    )
    return raw_path, rewrite_image, manifest_image


def _image_value(value: object, *, campaign_id: str, release_sha: str, field: str) -> preparer.PreparedImage:
    expected = {"archive_tag", "image_id", "repo_digests", "repo_tags", "size_bytes", "source_ref"}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ControllerImageAdoptionError(f"{field} is invalid")
    try:
        image_id = preparer.require_image_id(value.get("image_id"), field=f"{field} image ID")
        source_ref = preparer.require_image_reference(value.get("source_ref"), field=f"{field} source reference")
        repo_digests_value = value.get("repo_digests")
        repo_tags_value = value.get("repo_tags")
        if (
            not isinstance(repo_digests_value, list)
            or not isinstance(repo_tags_value, list)
            or not all(isinstance(item, str) for item in repo_digests_value + repo_tags_value)
            or repo_digests_value != sorted(repo_digests_value)
            or repo_tags_value != sorted(repo_tags_value)
            or len(set(repo_digests_value)) != len(repo_digests_value)
            or len(set(repo_tags_value)) != len(repo_tags_value)
        ):
            raise ValueError
        repo_digests = tuple(
            preparer.require_image_reference(item, field=f"{field} repo digest") for item in repo_digests_value
        )
        repo_tags = tuple(preparer.require_image_reference(item, field=f"{field} repo tag") for item in repo_tags_value)
        if any("@sha256:" not in item for item in repo_digests) or any("@" in item for item in repo_tags):
            raise ValueError
        size_bytes = preparer.require_nonnegative_int(
            value.get("size_bytes"), field=f"{field} size", maximum=MAXIMUM_ARTIFACT_BYTES
        )
        archive_tag = preparer.image_contract.require_canonical_archive_tag(
            value.get("archive_tag"),
            campaign_id=campaign_id,
            release_sha=release_sha,
            image_id=image_id,
            field=f"{field} archive tag",
        )
    except Exception as exc:
        raise ControllerImageAdoptionError(f"{field} is invalid") from exc
    return preparer.PreparedImage(
        source_ref=source_ref,
        image_id=image_id,
        repo_digests=repo_digests,
        repo_tags=repo_tags,
        size_bytes=size_bytes,
        archive_tag=archive_tag,
    )


def _load_supplemental_archive(
    *,
    bundle_path: Path,
    manifest_path: Path,
    campaign_id: str,
    release_sha: str,
    expected_images: Sequence[preparer.ImageSpecification],
) -> tuple[Path, tuple[preparer.PreparedImage, ...], tuple[str, int]]:
    """Verify the controller-local PostgreSQL/Redis archive before merging."""

    manifest_value, _payload = _read_canonical_private_json(
        manifest_path,
        field="supplemental image manifest",
    )
    expected = {"schema", "status", "campaign_id", "release_sha", "archive", "image_set_sha256", "images"}
    if (
        set(manifest_value) != expected
        or manifest_value.get("schema") != preparer.IMAGE_MANIFEST_SCHEMA
        or manifest_value.get("status") != "prepared"
        or manifest_value.get("campaign_id") != campaign_id
        or manifest_value.get("release_sha") != release_sha
    ):
        raise ControllerImageAdoptionError("supplemental image manifest is unsupported")
    raw_images = manifest_value.get("images")
    if not isinstance(raw_images, list) or len(raw_images) != REQUIRED_SUPPLEMENTAL_IMAGE_COUNT:
        raise ControllerImageAdoptionError("supplemental image manifest must contain exactly the required runtime images")
    images = tuple(
        _image_value(item, campaign_id=campaign_id, release_sha=release_sha, field="supplemental image manifest")
        for item in raw_images
    )
    if [
        (image.source_ref, image.image_id)
        for image in images
    ] != [
        (image.reference, image.expected_id)
        for image in expected_images
    ]:
        raise ControllerImageAdoptionError(
            "supplemental image manifest does not match the explicitly pinned controller inventory"
        )
    image_values = [image.as_manifest_value() for image in images]
    if image_values != raw_images or image_values != sorted(image_values, key=lambda item: item["source_ref"]):
        raise ControllerImageAdoptionError("supplemental image manifest images are not canonical")
    if manifest_value.get("image_set_sha256") != sha256_bytes(canonical_json_bytes(image_values)):
        raise ControllerImageAdoptionError("supplemental image manifest image set hash is invalid")
    archive = manifest_value.get("archive")
    if not isinstance(archive, Mapping) or set(archive) != {"bytes", "sha256", "image_ids", "repo_tags"}:
        raise ControllerImageAdoptionError("supplemental image manifest archive is invalid")
    bundle_path = _require_private_file(
        bundle_path,
        field="supplemental image bundle",
        maximum_bytes=MAXIMUM_ARTIFACT_BYTES,
    )
    actual_sha256, actual_bytes = sha256_file(bundle_path)
    if archive.get("sha256") != actual_sha256 or archive.get("bytes") != actual_bytes:
        raise ControllerImageAdoptionError("supplemental image bundle does not match its manifest")
    expected_ids = sorted(image.image_id for image in images)
    expected_tags = sorted(str(image.archive_tag) for image in images)
    if archive.get("image_ids") != expected_ids or archive.get("repo_tags") != expected_tags:
        raise ControllerImageAdoptionError("supplemental image bundle manifest does not bind isolated images")
    try:
        preparer.verify_docker_image_archive(
            path=bundle_path,
            images=images,
            require_isolated_tags=True,
        )
    except Exception as exc:
        raise ControllerImageAdoptionError("supplemental image bundle is structurally unsafe") from exc
    return bundle_path, images, (actual_sha256, actual_bytes)


def _copy_archive_members(
    *,
    source_path: Path,
    inspection: Any,
    destination: tarfile.TarFile,
    copied_names: set[str],
) -> None:
    """Copy one inspected archive without its mutable tag control files."""

    seen_names: set[str] = set()
    try:
        with tarfile.open(source_path, "r|", tarinfo=preparer._DockerArchiveTarInfo) as source:
            while (member := source.next()) is not None:
                preparer._validate_docker_archive_member(member, member_names=seen_names)
                if member.name not in inspection.member_names:
                    raise ControllerImageAdoptionError("input image archive changed while being merged")
                if member.name in {"manifest.json", "repositories"}:
                    continue
                if member.name in copied_names:
                    raise ControllerImageAdoptionError("input image archives reuse a member path")
                copied_names.add(member.name)
                copied = copy.copy(member)
                copied.pax_headers = {}
                copied.uid = 0
                copied.gid = 0
                copied.uname = ""
                copied.gname = ""
                copied.mtime = 0
                if copied.isdir():
                    destination.addfile(copied)
                    continue
                payload = source.extractfile(member)
                if payload is None:
                    raise ControllerImageAdoptionError("input image archive member cannot be read")
                try:
                    destination.addfile(copied, payload)
                finally:
                    payload.close()
    except (OSError, tarfile.TarError) as exc:
        raise ControllerImageAdoptionError("input image archive cannot be merged safely") from exc
    if seen_names != set(inspection.member_names):
        raise ControllerImageAdoptionError("input image archive changed while being merged")


def _merge_isolated_archives(
    *,
    application_archive: Path,
    supplemental_archive: Path,
    output_path: Path,
    application_image: preparer.PreparedImage,
    supplemental_images: Sequence[preparer.PreparedImage],
    supplemental_snapshot: tuple[str, int],
    final_images: Sequence[preparer.PreparedImage],
) -> dict[str, Any]:
    """Merge two pre-validated archives without a Docker daemon or tag reuse."""

    if sha256_file(supplemental_archive) != supplemental_snapshot:
        raise ControllerImageAdoptionError("supplemental image bundle changed from its verified snapshot")
    try:
        application_inspection = preparer.inspect_docker_image_archive(path=application_archive)
        supplemental_inspection = preparer.inspect_docker_image_archive(path=supplemental_archive)
        preparer.verify_docker_image_archive(
            path=application_archive,
            images=[application_image],
            require_isolated_tags=True,
        )
        preparer.verify_docker_image_archive(
            path=supplemental_archive,
            images=supplemental_images,
            require_isolated_tags=True,
        )
    except Exception as exc:
        raise ControllerImageAdoptionError("isolated image archives cannot be merged") from exc
    output_path = output_path.resolve(strict=False)
    if output_path.parent != output_path.parent.resolve(strict=True):
        raise ControllerImageAdoptionError("merged image bundle parent is not canonical")
    copied_names: set[str] = set()
    try:
        with preparer._open_new_private_binary(output_path, field="merged Docker image archive") as output:
            with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as destination:
                _copy_archive_members(
                    source_path=application_archive,
                    inspection=application_inspection,
                    destination=destination,
                    copied_names=copied_names,
                )
                if sha256_file(supplemental_archive) != supplemental_snapshot:
                    raise ControllerImageAdoptionError("supplemental image bundle changed while being merged")
                _copy_archive_members(
                    source_path=supplemental_archive,
                    inspection=supplemental_inspection,
                    destination=destination,
                    copied_names=copied_names,
                )
                entries_by_id = {
                    entry.image_id: entry
                    for entry in (*application_inspection.entries, *supplemental_inspection.entries)
                }
                if len(entries_by_id) != len(final_images):
                    raise ControllerImageAdoptionError("isolated image archives have duplicate image identities")
                manifest = [
                    {
                        "Config": entries_by_id[image.image_id].config_name,
                        "Layers": list(entries_by_id[image.image_id].layers),
                        "RepoTags": [image.archive_tag],
                    }
                    for image in final_images
                ]
                encoded = canonical_json_bytes(manifest)
                member = tarfile.TarInfo("manifest.json")
                member.mode = 0o600
                member.uid = 0
                member.gid = 0
                member.mtime = 0
                member.size = len(encoded)
                destination.addfile(member, io.BytesIO(encoded))
            output.flush()
            os.fsync(output.fileno())
    except (OSError, tarfile.TarError) as exc:
        raise ControllerImageAdoptionError("cannot create the merged image bundle") from exc
    try:
        verified = preparer.verify_docker_image_archive(
            path=output_path,
            images=final_images,
            require_isolated_tags=True,
        )
    except Exception as exc:
        raise ControllerImageAdoptionError("merged image bundle failed isolated-tag verification") from exc
    digest, size = sha256_file(output_path)
    if sha256_file(output_path) != (digest, size):
        raise ControllerImageAdoptionError("merged image bundle changed while being verified")
    return {"sha256": digest, "bytes": size, **verified}


def _write_new_json(path: Path, value: Mapping[str, Any], *, field: str) -> bytes:
    try:
        preparer.write_new_private_json(path, value)
        return canonical_json_bytes(value) + b"\n"
    except Exception as exc:
        raise ControllerImageAdoptionError(f"cannot create {field}") from exc


def _proof_inputs(paths: Mapping[str, Path]) -> tuple[dict[str, bytes], dict[str, dict[str, Any]]]:
    payloads: dict[str, bytes] = {}
    values: dict[str, dict[str, Any]] = {}
    for name in PROOF_NAMES:
        value, payload = _read_canonical_private_json(paths[name], field=name)
        payloads[name] = payload
        values[name] = value
    return payloads, values


def _expected_supplemental_images(
    *,
    postgres_image: preparer.ImageSpecification,
    redis_image: preparer.ImageSpecification,
) -> tuple[preparer.ImageSpecification, ...]:
    """Normalize the two runtime images from an explicit trusted inventory."""

    try:
        rendered: list[str] = []
        for value in (postgres_image, redis_image):
            if not isinstance(value, preparer.ImageSpecification):
                raise TypeError
            rendered.append(value.reference + "=" + value.expected_id)
        normalized = tuple(preparer.parse_image_specifications(rendered))
    except Exception as exc:
        raise ControllerImageAdoptionError("supplemental image inventory is invalid") from exc
    if len(normalized) != REQUIRED_SUPPLEMENTAL_IMAGE_COUNT:
        raise ControllerImageAdoptionError(
            "controller must explicitly pin exactly the PostgreSQL and Redis supplemental images"
        )
    roles = {
        image.reference.rsplit("/", 1)[-1].split("@", 1)[0].split(":", 1)[0]
        for image in normalized
    }
    if roles != {"postgres", "redis"}:
        raise ControllerImageAdoptionError(
            "controller supplemental inventory must explicitly name one PostgreSQL and one Redis image"
        )
    return normalized


def adopt_webapp_fi_image(
    *,
    source_role_attestation: Path,
    image_export_receipt: Path,
    controller_delivery_envelope: Path,
    signer_enrollment_certificate: Path,
    static_assets_provenance: Path,
    source_image_readback: Path,
    raw_image_archive: Path,
    supplemental_image_bundle: Path,
    supplemental_image_manifest: Path,
    expected_postgres_image: preparer.ImageSpecification,
    expected_redis_image: preparer.ImageSpecification,
    pinned_source_signing_public_key_base64: str,
    pinned_controller_public_key_base64: str,
    expected_campaign_id: str,
    expected_application: Mapping[str, str],
    expected_control_commit: str,
    expected_control_tree: str,
    expected_canonical_release_tree_sha256: str,
    expected_app_image_id: str,
    expected_app_image_reference: str,
    controller_signing_private_key: Path,
    output_directory: Path,
    verification_time: str,
    apply: bool,
) -> dict[str, Any]:
    """Make a new signed, operational three-image controller candidate.

    ``source_image_readback`` is a local hand-off contract, not an S3 client:
    the controller transport implementation must produce it only after an
    exact-VersionId read-back and age decryption.  This function has no S3,
    SSH, age, Docker, or service side effect in either plan or apply mode.
    """

    _require_root_execution()
    proof_paths = {
        "source_role_attestation": source_role_attestation,
        "image_export_receipt": image_export_receipt,
        "controller_delivery_envelope": controller_delivery_envelope,
        "signer_enrollment_certificate": signer_enrollment_certificate,
        "static_assets_provenance": static_assets_provenance,
    }
    payloads, proof_values = _proof_inputs(proof_paths)
    authority = _validate_authority(
        proof_payloads=payloads,
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
    try:
        campaign_id = portable._campaign(expected_campaign_id, field="expected campaign")
        application = portable._application(expected_application, field="expected application")
        portable._timestamp(verification_time, field="controller adoption timestamp")
        source_image = authority["image_export"]["image_claim"]
    except Exception as exc:
        raise ControllerImageAdoptionError("controller adoption identity is invalid") from exc
    source_object = _load_source_image_readback(path=source_image_readback, campaign_id=campaign_id)
    raw_path, rewrite_image, manifest_image = _inspect_raw_source_image(
        raw_path=raw_image_archive,
        source_image=source_image,
        source_object=source_object,
        campaign_id=campaign_id,
        release_sha=application["release_sha"],
    )
    supplemental_specs = _expected_supplemental_images(
        postgres_image=expected_postgres_image,
        redis_image=expected_redis_image,
    )
    supplemental_path, supplemental_images, supplemental_snapshot = _load_supplemental_archive(
        bundle_path=supplemental_image_bundle,
        manifest_path=supplemental_image_manifest,
        campaign_id=campaign_id,
        release_sha=application["release_sha"],
        expected_images=supplemental_specs,
    )
    if any(image.image_id == manifest_image.image_id for image in supplemental_images):
        raise ControllerImageAdoptionError("supplemental image bundle duplicates the WebApp-FI application image")
    images = tuple(sorted((manifest_image, *supplemental_images), key=lambda image: image.source_ref))
    if len({image.image_id for image in images}) != len(images):
        raise ControllerImageAdoptionError("final image bundle has duplicate image identities")
    signer, controller_public_key_base64 = _load_controller_signer(controller_signing_private_key)
    if controller_public_key_base64 != pinned_controller_public_key_base64:
        raise ControllerImageAdoptionError("controller image-adoption signer does not match the pinned controller key")
    output_directory = Path(output_directory)
    if not output_directory.is_absolute():
        raise ControllerImageAdoptionError("output_directory must be an absolute path")
    parent = _require_private_directory(output_directory.parent, field="output_directory parent")
    if output_directory.parent != parent or output_directory.exists() or output_directory.is_symlink():
        raise ControllerImageAdoptionError("output_directory must be a new child of a root-only directory")
    plan = {
        "status": "planned" if not apply else "adopting",
        "output_directory": str(output_directory),
        "campaign_id": campaign_id,
        "application": application,
        "source_image_object": {"object_key": source_object["object_key"], "version_id": source_object["version_id"]},
        "image_count": len(images),
        "supplemental_image_count": len(supplemental_images),
        "object_storage_action": False,
        "age_action": False,
        "docker_command_invoked": False,
        "docker_load_invoked": False,
        "service_changed": False,
    }
    if not apply:
        return plan
    try:
        output_directory.mkdir(mode=0o700)
    except OSError as exc:
        raise ControllerImageAdoptionError("cannot create output_directory") from exc
    _require_private_directory(output_directory, field="output_directory")
    # A temporary rewritten app archive is intentionally kept only during this
    # local merge.  It contains already-isolated tags and no raw source tags.
    with tempfile.TemporaryDirectory(prefix=".fi-app-isolated-", dir=output_directory) as temporary:
        temporary_path = Path(temporary)
        temporary_path.chmod(0o700)
        isolated_app = temporary_path / "app-isolated.tar"
        try:
            preparer.rewrite_docker_image_archive_tags(
                raw_path=raw_path,
                output_path=isolated_app,
                images=[rewrite_image],
                expected_raw_sha256=source_image["docker_save_archive_sha256"],
                expected_raw_bytes=source_image["docker_save_archive_bytes"],
                require_source_tags=bool(rewrite_image.repo_tags),
            )
        except Exception as exc:
            raise ControllerImageAdoptionError("cannot rewrite the signed source image archive") from exc
        image_bundle_path = output_directory / IMAGE_BUNDLE_NAME
        image_archive = _merge_isolated_archives(
            application_archive=isolated_app,
            supplemental_archive=supplemental_path,
            output_path=image_bundle_path,
            application_image=manifest_image,
            supplemental_images=supplemental_images,
            supplemental_snapshot=supplemental_snapshot,
            final_images=images,
        )
    # Re-read all authority inputs and raw bytes before signing a receipt, so
    # a candidate cannot bind an input that changed while its output was made.
    final_payloads, final_proof_values = _proof_inputs(proof_paths)
    final_authority = _validate_authority(
        proof_payloads=final_payloads,
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
    final_source_object = _load_source_image_readback(path=source_image_readback, campaign_id=campaign_id)
    if (
        final_authority.get("proof_sha256") != authority.get("proof_sha256")
        or final_source_object != source_object
        or sha256_file(raw_path)
        != (source_image["docker_save_archive_sha256"], source_image["docker_save_archive_bytes"])
    ):
        raise ControllerImageAdoptionError("source input changed while the controller image candidate was created")
    image_values = [image.as_manifest_value() for image in images]
    image_set_sha256 = sha256_bytes(canonical_json_bytes(image_values))
    image_ids_sha256 = sha256_bytes(canonical_json_bytes([image.image_id for image in images]))
    image_manifest_path = output_directory / IMAGE_MANIFEST_NAME
    image_manifest = {
        "schema": preparer.IMAGE_MANIFEST_SCHEMA,
        "status": "prepared",
        "campaign_id": campaign_id,
        "release_sha": application["release_sha"],
        "archive": image_archive,
        "image_set_sha256": image_set_sha256,
        "images": image_values,
    }
    image_manifest_payload = _write_new_json(image_manifest_path, image_manifest, field="controller image manifest")
    image_manifest_sha256 = sha256_bytes(image_manifest_payload)
    image_manifest_bytes = len(image_manifest_payload)
    app_archive_tag = next(image.archive_tag for image in images if image.image_id == manifest_image.image_id)
    artifacts = {
        "image_bundle_sha256": image_archive["sha256"],
        "image_bundle_bytes": image_archive["bytes"],
        "image_manifest_sha256": image_manifest_sha256,
        "image_manifest_bytes": image_manifest_bytes,
        "image_set_sha256": image_set_sha256,
        "image_ids_sha256": image_ids_sha256,
        "app_image_id": manifest_image.image_id,
        "app_image_archive_tag": app_archive_tag,
    }
    source_claim = {
        "image_id": source_image["image_id"],
        "image_reference": source_image["image_reference"],
        "docker_save_archive_sha256": source_image["docker_save_archive_sha256"],
        "docker_save_archive_bytes": source_image["docker_save_archive_bytes"],
    }
    unsigned_receipt = {
        "schema": portable.IMAGE_ADOPTION_RECEIPT_SCHEMA,
        "status": "adopted",
        "adopted_at": verification_time,
        "campaign_id": campaign_id,
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "application": application,
        "tooling": {"control_commit": expected_control_commit, "control_tree": expected_control_tree},
        "canonical_release_tree_sha256": expected_canonical_release_tree_sha256,
        "proof_sha256": dict(final_authority["proof_sha256"]),
        "source_image": source_claim,
        "source_image_object": source_object,
        "source_image_transport": SOURCE_IMAGE_TRANSPORT,
        "controller_image_artifacts": artifacts,
        "archive_contract": ARCHIVE_CONTRACT,
        "controller_public_key_base64": controller_public_key_base64,
        "controller_key_id": portable.public_key_id(controller_public_key_base64),
    }
    receipt = {
        **unsigned_receipt,
        "controller_signature": {
            "algorithm": "ed25519",
            "signature_base64": base64.b64encode(
                signer.sign(portable.IMAGE_ADOPTION_SIGNATURE_DOMAIN + canonical_json_bytes(unsigned_receipt))
            ).decode("ascii"),
        },
    }
    receipt_path = output_directory / ADOPTION_RECEIPT_NAME
    receipt_payload = _write_new_json(receipt_path, receipt, field="controller image adoption receipt")
    source_provenance_input = {
        "schema": SOURCE_PROVENANCE_INPUT_SCHEMA,
        "campaign_id": campaign_id,
        "proofs": {
            **final_proof_values,
            "controller_image_adoption_receipt": portable._parse(
                receipt_payload,
                field="controller image adoption receipt",
            ),
        },
    }
    input_path = output_directory / SOURCE_PROVENANCE_INPUT_NAME
    _write_new_json(input_path, source_provenance_input, field="controller source provenance input")
    return {
        "status": "adopted",
        "output_directory": str(output_directory),
        "image_bundle_path": str(image_bundle_path),
        "image_manifest_path": str(image_manifest_path),
        "image_adoption_receipt_path": str(receipt_path),
        "source_provenance_input_path": str(input_path),
        "image_bundle_sha256": image_archive["sha256"],
        "image_bundle_bytes": image_archive["bytes"],
        "image_manifest_sha256": image_manifest_sha256,
        "image_manifest_bytes": image_manifest_bytes,
        "image_count": len(images),
        "source_image_object": {"object_key": source_object["object_key"], "version_id": source_object["version_id"]},
        "object_storage_action": False,
        "age_action": False,
        "docker_command_invoked": False,
        "docker_load_invoked": False,
        "service_changed": False,
    }


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-role-attestation", type=Path, required=True)
    parser.add_argument("--image-export-receipt", type=Path, required=True)
    parser.add_argument("--controller-delivery-envelope", type=Path, required=True)
    parser.add_argument("--signer-enrollment-certificate", type=Path, required=True)
    parser.add_argument("--static-assets-provenance", type=Path, required=True)
    parser.add_argument("--source-image-readback", type=Path, required=True)
    parser.add_argument("--raw-image-archive", type=Path, required=True)
    parser.add_argument("--supplemental-image-bundle", type=Path, required=True)
    parser.add_argument("--supplemental-image-manifest", type=Path, required=True)
    parser.add_argument("--postgres-image", required=True, metavar="REF=IMAGE_ID")
    parser.add_argument("--redis-image", required=True, metavar="REF=IMAGE_ID")
    parser.add_argument("--pinned-source-signing-public-key-base64", required=True)
    parser.add_argument("--pinned-controller-public-key-base64", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--expected-alembic-revision", required=True)
    parser.add_argument("--control-commit", required=True)
    parser.add_argument("--control-tree", required=True)
    parser.add_argument("--canonical-release-tree-sha256", required=True)
    parser.add_argument("--app-image-id", required=True)
    parser.add_argument("--app-image-reference", required=True)
    parser.add_argument("--controller-signing-private-key", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--verification-time", default=None)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        result = adopt_webapp_fi_image(
            source_role_attestation=args.source_role_attestation,
            image_export_receipt=args.image_export_receipt,
            controller_delivery_envelope=args.controller_delivery_envelope,
            signer_enrollment_certificate=args.signer_enrollment_certificate,
            static_assets_provenance=args.static_assets_provenance,
            source_image_readback=args.source_image_readback,
            raw_image_archive=args.raw_image_archive,
            supplemental_image_bundle=args.supplemental_image_bundle,
            supplemental_image_manifest=args.supplemental_image_manifest,
            expected_postgres_image=preparer.parse_image_specifications([args.postgres_image])[0],
            expected_redis_image=preparer.parse_image_specifications([args.redis_image])[0],
            pinned_source_signing_public_key_base64=args.pinned_source_signing_public_key_base64,
            pinned_controller_public_key_base64=args.pinned_controller_public_key_base64,
            expected_campaign_id=args.campaign_id,
            expected_application={
                "release_sha": args.release_sha,
                "expected_alembic_revision": args.expected_alembic_revision,
            },
            expected_control_commit=args.control_commit,
            expected_control_tree=args.control_tree,
            expected_canonical_release_tree_sha256=args.canonical_release_tree_sha256,
            expected_app_image_id=args.app_image_id,
            expected_app_image_reference=args.app_image_reference,
            controller_signing_private_key=args.controller_signing_private_key,
            output_directory=args.output_directory,
            verification_time=args.verification_time or utc_iso(),
            apply=args.apply,
        )
    except ControllerImageAdoptionError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": exc.__class__.__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI dispatch.
    raise SystemExit(main())
