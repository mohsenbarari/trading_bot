#!/usr/bin/env python3
"""Build and verify a sealed Emergency WA-IR Object Storage receive manifest.

This module is deliberately a *control-plane* helper.  It does not create a
bucket, use S3 credentials, upload or download an object, decrypt an artifact,
write an artifact to a target path, load an image, or start a container.  A
separately reviewed publisher must first upload already-age-encrypted objects
to a private, versioned Arvan bucket and collect their immutable VersionIds.

The resulting manifest binds exactly four encrypted artifacts for one
Emergency IR campaign: a Docker image bundle, a release package tar, a
database snapshot, and a settings tar.  It is canonical JSON signed by the
publisher's Ed25519 key.  The receiver verifies it with a pinned public key
and emits only a non-authorizing download plan.  A future downloader must use
that plan's exact endpoint, bucket, object key, VersionId, hashes, and
allowlisted destination path; it must not treat this helper as deployment
approval.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import dataclasses
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Mapping

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
except ImportError:  # pragma: no cover - production requirements include cryptography.
    InvalidSignature = None  # type: ignore[assignment,misc]
    serialization = None  # type: ignore[assignment,misc]
    Ed25519PrivateKey = None  # type: ignore[assignment,misc]
    Ed25519PublicKey = None  # type: ignore[assignment,misc]


MANIFEST_SCHEMA = "gold-trade-emergency-ir-object-storage-manifest-v1"
SIGNATURE_DOMAIN = b"gold-trade-emergency-ir-object-storage-manifest-v1\x00"
SIGNATURE_ALGORITHM = "ed25519"

# This is an endpoint allowlist, not an S3 configuration default.  The helper
# intentionally has no bucket or credential built in, so an Emergency transfer
# cannot accidentally reuse a three-site bucket or an unreviewed endpoint.
APPROVED_ARVAN_ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
APPROVED_ARVAN_REGION = "ir-thr-at1"
SOURCE_SITE = "webapp_fi"
DESTINATION_SITE = "webapp_ir_emergency"

MAX_MANIFEST_BYTES = 128 * 1024
MAX_ARTIFACT_BYTES = 100 * 1024 * 1024 * 1024
MAX_CIPHERTEXT_BYTES = MAX_ARTIFACT_BYTES + 2 * 1024 * 1024
MAX_KEY_FILE_BYTES = 1024

CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII)
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$", re.ASCII)
PREFIX_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,127}$", re.ASCII)
SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
RECIPIENT_KEY_ID_RE = re.compile(r"^age-recipient-sha256:[a-f0-9]{64}$", re.ASCII)
SIGNER_KEY_ID_RE = re.compile(r"^ed25519-sha256:[a-f0-9]{64}$", re.ASCII)


class EmergencyManifestError(ValueError):
    """The sealed Emergency IR transfer contract is invalid or unsafe."""


@dataclasses.dataclass(frozen=True)
class VerifiedEmergencyManifest:
    """Non-authorizing receiver plan derived from a verified manifest."""

    manifest_sha256: str
    campaign_id: str
    endpoint: str
    region: str
    bucket: str
    prefix: str
    destination_age_recipient_key_id: str
    artifacts: tuple[dict[str, Any], ...]

    def as_receive_plan(self) -> dict[str, Any]:
        """Return the bounded data a later downloader may inspect.

        This deliberately omits the signature and performs no I/O.  It does
        not authorize an artifact download, decrypt, restore, image load, or
        application activation.
        """

        return {
            "schema": "gold-trade-emergency-ir-object-storage-receive-plan-v1",
            "status": "verified-non-authorizing",
            "manifest_sha256": self.manifest_sha256,
            "campaign_id": self.campaign_id,
            "source_site": SOURCE_SITE,
            "destination_site": DESTINATION_SITE,
            "endpoint": self.endpoint,
            "region": self.region,
            "bucket": self.bucket,
            "prefix": self.prefix,
            "destination_age_recipient_key_id": self.destination_age_recipient_key_id,
            "artifacts": [dict(item) for item in self.artifacts],
        }


# The fixed artifact names make target paths independently derivable.  A
# malicious manifest cannot select an arbitrary file beneath /srv, nor replace
# a three-site release directory.  The campaign component keeps every receive
# generation detached until an explicit later staging operation reviews it.
ARTIFACT_CONTRACTS: dict[str, dict[str, str]] = {
    "image_bundle": {
        "format": "docker-image-tar",
        "filename": "images.tar.age",
        "target_root": "/srv/trading-bot-emergency/inbox/images",
    },
    "package_tar": {
        "format": "release-package-tar",
        "filename": "package.tar.age",
        "target_root": "/srv/trading-bot-emergency/inbox/package",
    },
    "snapshot": {
        "format": "postgresql-custom-dump",
        "filename": "snapshot.dump.age",
        "target_root": "/srv/trading-bot-emergency/inbox/snapshot",
    },
    "settings": {
        "format": "settings-tar",
        "filename": "settings.tar.age",
        "target_root": "/srv/trading-bot-emergency/inbox/settings",
    },
}
ARTIFACT_ORDER = tuple(ARTIFACT_CONTRACTS)

UNSIGNED_FIELDS = frozenset(
    {
        "schema",
        "campaign_id",
        "source_site",
        "destination_site",
        "endpoint",
        "region",
        "bucket",
        "prefix",
        "created_at",
        "destination_age_recipient_key_id",
        "artifacts",
    }
)
MANIFEST_FIELDS = UNSIGNED_FIELDS | {
    "signature_algorithm",
    "signer_key_id",
    "signature_base64",
}
ARTIFACT_FIELDS = frozenset(
    {
        "kind",
        "format",
        "object_key",
        "version_id",
        "plaintext_sha256",
        "plaintext_bytes",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "encryption",
        "target_path",
    }
)
ENCRYPTION_FIELDS = frozenset({"algorithm", "recipient_key_id"})


def _fail(message: str) -> None:
    raise EmergencyManifestError(message)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("JSON object contains a duplicate field")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("JSON constants are not allowed")


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Return the exact encoding covered by the Ed25519 signature."""

    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, RecursionError) as exc:
        raise EmergencyManifestError("manifest cannot be canonically encoded") from exc


def load_strict_json_bytes(payload: bytes, *, require_canonical: bool) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_MANIFEST_BYTES:
        _fail("manifest bytes are empty or exceed the size bound")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except EmergencyManifestError:
        raise
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise EmergencyManifestError("manifest is not strict JSON") from exc
    if not isinstance(value, dict):
        _fail("manifest must be a JSON object")
    if require_canonical and canonical_json_bytes(value) != payload:
        _fail("signed manifest must use canonical JSON encoding")
    return value


def _require_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(f"{field} must be a non-empty trimmed string")
    return value


def _require_pattern(value: object, *, field: str, pattern: re.Pattern[str]) -> str:
    text = _require_text(value, field=field)
    if pattern.fullmatch(text) is None:
        _fail(f"{field} has an unsafe format")
    return text


def _require_positive_bytes(value: object, *, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        _fail(f"{field} must be a positive bounded integer")
    return value


def _require_version_id(value: object) -> str:
    version_id = _require_text(value, field="artifact version_id")
    if version_id == "null" or len(version_id) > 1024:
        _fail("artifact version_id has an unsafe format")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in version_id):
        _fail("artifact version_id has an unsafe format")
    return version_id


def _parse_utc_timestamp(value: object, *, field: str) -> str:
    text = _require_text(value, field=field)
    if not text.endswith("Z"):
        _fail(f"{field} must be an RFC3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EmergencyManifestError(f"{field} is not a valid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        _fail(f"{field} must use UTC")
    canonical = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if canonical != text:
        _fail(f"{field} must use canonical RFC3339 UTC encoding")
    return text


def _validate_prefix(value: object) -> str:
    prefix = _require_text(value, field="prefix")
    if prefix.startswith("/") or prefix.endswith("/"):
        _fail("prefix must not begin or end with a slash")
    parts = prefix.split("/")
    if not parts or any(PREFIX_COMPONENT_RE.fullmatch(part) is None for part in parts):
        _fail("prefix must consist of safe object-key components")
    return prefix


def expected_object_key(*, prefix: str, campaign_id: str, kind: str) -> str:
    contract = ARTIFACT_CONTRACTS[kind]
    return "/".join((prefix, campaign_id, kind, contract["filename"]))


def expected_target_path(*, campaign_id: str, kind: str) -> str:
    contract = ARTIFACT_CONTRACTS[kind]
    return str(PurePosixPath(contract["target_root"]) / campaign_id / contract["filename"])


def _validate_artifact(
    value: object,
    *,
    prefix: str,
    campaign_id: str,
    recipient_key_id: str,
    expected_kind: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != ARTIFACT_FIELDS:
        _fail("manifest artifact fields are unsupported")
    artifact = dict(value)
    kind = _require_text(artifact.get("kind"), field="artifact kind")
    if kind != expected_kind:
        _fail("manifest artifacts must be complete, unique, and in fixed order")
    contract = ARTIFACT_CONTRACTS[kind]
    if artifact.get("format") != contract["format"]:
        _fail("artifact format does not match its Emergency IR contract")
    if artifact.get("object_key") != expected_object_key(
        prefix=prefix, campaign_id=campaign_id, kind=kind
    ):
        _fail("artifact object_key is outside the immutable campaign location")
    _require_version_id(artifact.get("version_id"))
    _require_pattern(
        artifact.get("plaintext_sha256"), field="artifact plaintext_sha256", pattern=SHA256_RE
    )
    _require_positive_bytes(
        artifact.get("plaintext_bytes"), field="artifact plaintext_bytes", maximum=MAX_ARTIFACT_BYTES
    )
    _require_pattern(
        artifact.get("ciphertext_sha256"), field="artifact ciphertext_sha256", pattern=SHA256_RE
    )
    _require_positive_bytes(
        artifact.get("ciphertext_bytes"),
        field="artifact ciphertext_bytes",
        maximum=MAX_CIPHERTEXT_BYTES,
    )
    if artifact["ciphertext_bytes"] <= artifact["plaintext_bytes"]:
        _fail("artifact ciphertext must be larger than its plaintext age input")
    if artifact["ciphertext_sha256"] == artifact["plaintext_sha256"]:
        _fail("artifact ciphertext hash must not equal its plaintext hash")
    encryption = artifact.get("encryption")
    if not isinstance(encryption, Mapping) or set(encryption) != ENCRYPTION_FIELDS:
        _fail("artifact encryption fields are unsupported")
    if encryption.get("algorithm") != "age-v1":
        _fail("artifact must use age-v1 encryption")
    if encryption.get("recipient_key_id") != recipient_key_id:
        _fail("artifact encryption recipient does not match the Emergency IR receiver")
    expected_target = expected_target_path(campaign_id=campaign_id, kind=kind)
    if artifact.get("target_path") != expected_target:
        _fail("artifact target_path is not an allowlisted Emergency IR inbox path")
    return artifact


def validate_unsigned_manifest(value: object) -> dict[str, Any]:
    """Validate one unsigned manifest specification without any I/O."""

    if not isinstance(value, Mapping) or set(value) != UNSIGNED_FIELDS:
        _fail("unsigned manifest fields are unsupported")
    manifest = dict(value)
    if manifest.get("schema") != MANIFEST_SCHEMA:
        _fail("manifest schema is unsupported")
    campaign_id = _require_pattern(
        manifest.get("campaign_id"), field="campaign_id", pattern=CAMPAIGN_ID_RE
    )
    if manifest.get("source_site") != SOURCE_SITE or manifest.get("destination_site") != DESTINATION_SITE:
        _fail("manifest source/destination site binding is invalid")
    if manifest.get("endpoint") != APPROVED_ARVAN_ENDPOINT or manifest.get("region") != APPROVED_ARVAN_REGION:
        _fail("manifest endpoint is not the approved Arvan Emergency IR endpoint")
    _require_pattern(manifest.get("bucket"), field="bucket", pattern=BUCKET_RE)
    prefix = _validate_prefix(manifest.get("prefix"))
    _parse_utc_timestamp(manifest.get("created_at"), field="created_at")
    recipient_key_id = _require_pattern(
        manifest.get("destination_age_recipient_key_id"),
        field="destination_age_recipient_key_id",
        pattern=RECIPIENT_KEY_ID_RE,
    )
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(ARTIFACT_ORDER):
        _fail("manifest must contain the complete Emergency IR artifact set")
    normalized_artifacts = [
        _validate_artifact(
            item,
            prefix=prefix,
            campaign_id=campaign_id,
            recipient_key_id=recipient_key_id,
            expected_kind=kind,
        )
        for item, kind in zip(artifacts, ARTIFACT_ORDER, strict=True)
    ]
    manifest["artifacts"] = normalized_artifacts
    return manifest


def _public_key_bytes(public_key: Ed25519PublicKey) -> bytes:
    if Ed25519PublicKey is None or serialization is None or not isinstance(public_key, Ed25519PublicKey):
        _fail("Ed25519 public key is invalid")
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def signer_key_id(public_key: Ed25519PublicKey) -> str:
    return "ed25519-sha256:" + hashlib.sha256(_public_key_bytes(public_key)).hexdigest()


def _decode_exact_base64(value: object, *, field: str, expected_bytes: int) -> bytes:
    encoded = _require_text(value, field=field)
    try:
        decoded = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise EmergencyManifestError(f"{field} must be strict base64") from exc
    if len(decoded) != expected_bytes:
        _fail(f"{field} must decode to exactly {expected_bytes} bytes")
    return decoded


def signing_payload(unsigned_manifest: Mapping[str, Any]) -> bytes:
    return SIGNATURE_DOMAIN + canonical_json_bytes(dict(unsigned_manifest))


def sign_manifest(
    unsigned_manifest: Mapping[str, Any], *, private_key: Ed25519PrivateKey
) -> dict[str, Any]:
    """Validate and sign a manifest specification; no Object Storage I/O occurs."""

    if Ed25519PrivateKey is None or not isinstance(private_key, Ed25519PrivateKey):
        _fail("Ed25519 private key is invalid")
    normalized = validate_unsigned_manifest(unsigned_manifest)
    public_key = private_key.public_key()
    result = dict(normalized)
    result["signature_algorithm"] = SIGNATURE_ALGORITHM
    result["signer_key_id"] = signer_key_id(public_key)
    result["signature_base64"] = base64.b64encode(
        private_key.sign(signing_payload(normalized))
    ).decode("ascii")
    # Keep the builder and receiver on one exact contract.
    verify_manifest(result, public_key=public_key)
    return result


def verify_manifest(
    value: object, *, public_key: Ed25519PublicKey
) -> VerifiedEmergencyManifest:
    """Verify an already parsed manifest and return a bounded receiver plan."""

    if Ed25519PublicKey is None or not isinstance(public_key, Ed25519PublicKey):
        _fail("Ed25519 public key is invalid")
    if not isinstance(value, Mapping) or set(value) != MANIFEST_FIELDS:
        _fail("signed manifest fields are unsupported")
    manifest = dict(value)
    unsigned = {field: manifest[field] for field in UNSIGNED_FIELDS}
    normalized = validate_unsigned_manifest(unsigned)
    if manifest.get("signature_algorithm") != SIGNATURE_ALGORITHM:
        _fail("manifest signature algorithm is unsupported")
    provided_key_id = _require_pattern(
        manifest.get("signer_key_id"), field="signer_key_id", pattern=SIGNER_KEY_ID_RE
    )
    if provided_key_id != signer_key_id(public_key):
        _fail("manifest signer key does not match the pinned public key")
    signature = _decode_exact_base64(
        manifest.get("signature_base64"), field="signature_base64", expected_bytes=64
    )
    try:
        public_key.verify(signature, signing_payload(normalized))
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise EmergencyManifestError("manifest Ed25519 signature is invalid") from exc
    canonical = canonical_json_bytes(manifest)
    return VerifiedEmergencyManifest(
        manifest_sha256=hashlib.sha256(canonical).hexdigest(),
        campaign_id=normalized["campaign_id"],
        endpoint=normalized["endpoint"],
        region=normalized["region"],
        bucket=normalized["bucket"],
        prefix=normalized["prefix"],
        destination_age_recipient_key_id=normalized["destination_age_recipient_key_id"],
        artifacts=tuple(dict(item) for item in normalized["artifacts"]),
    )


def verify_manifest_bytes(payload: bytes, *, public_key: Ed25519PublicKey) -> VerifiedEmergencyManifest:
    """Parse canonical strict JSON and verify the signature it carries."""

    return verify_manifest(
        load_strict_json_bytes(payload, require_canonical=True), public_key=public_key
    )


def _read_stable_regular_file(
    path: Path,
    *,
    field: str,
    maximum_bytes: int,
    owner_uid: int | None = None,
    private: bool = False,
) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise EmergencyManifestError(f"{field} cannot be inspected") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        _fail(f"{field} must be a regular non-symlink file")
    if not 1 <= metadata.st_size <= maximum_bytes:
        _fail(f"{field} size is invalid")
    if owner_uid is not None:
        if metadata.st_uid != owner_uid or metadata.st_mode & 0o022:
            _fail(f"{field} must be owned by the caller and not writable by group/other")
        if private and metadata.st_mode & 0o077:
            _fail(f"{field} must not be readable by group/other")
        if metadata.st_nlink != 1:
            _fail(f"{field} must not have additional hard links")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(no_follow, int):
        _fail(f"{field} requires O_NOFOLLOW support")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | no_follow,
        )
        opened = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(metadata, name) != getattr(opened, name) for name in fields):
            _fail(f"{field} changed while being opened")
        payload = bytearray()
        while len(payload) <= maximum_bytes:
            chunk = os.read(descriptor, min(65536, maximum_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) != opened.st_size or len(payload) > maximum_bytes:
            _fail(f"{field} changed while being read")
        after = os.fstat(descriptor)
        if any(getattr(opened, name) != getattr(after, name) for name in fields):
            _fail(f"{field} changed while being read")
        return bytes(payload)
    except OSError as exc:
        raise EmergencyManifestError(f"{field} cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_key_file(path: Path, *, private: bool) -> bytes:
    payload = _read_stable_regular_file(
        path,
        field="key file",
        maximum_bytes=MAX_KEY_FILE_BYTES,
        owner_uid=os.geteuid(),
        private=private,
    )
    try:
        encoded = payload.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise EmergencyManifestError("key file must be ASCII base64") from exc
    return _decode_exact_base64(encoded, field="key file", expected_bytes=32)


def load_private_key(path: Path) -> Ed25519PrivateKey:
    if Ed25519PrivateKey is None:
        _fail("cryptography Ed25519 support is unavailable")
    try:
        return Ed25519PrivateKey.from_private_bytes(_read_key_file(path, private=True))
    except ValueError as exc:
        raise EmergencyManifestError("private key file is invalid") from exc


def load_public_key(path: Path) -> Ed25519PublicKey:
    if Ed25519PublicKey is None:
        _fail("cryptography Ed25519 support is unavailable")
    try:
        return Ed25519PublicKey.from_public_bytes(_read_key_file(path, private=False))
    except ValueError as exc:
        raise EmergencyManifestError("public key file is invalid") from exc


def generate_keypair(*, private_path: Path, public_path: Path) -> str:
    """Create one local raw Ed25519 keypair without printing secret material.

    The caller chooses two existing root-/owner-controlled parent directories.
    Both outputs are create-only, so this helper cannot replace a controller
    trust anchor accidentally.  The public file is intentionally also 0600:
    receiver provisioning may make a separate root-owned copy through the
    sealed Object Storage bootstrap rather than widening access here.
    """

    if private_path == public_path:
        _fail("private and public key output paths must differ")
    if private_path.exists() or public_path.exists():
        _fail("refusing to overwrite an existing signing key output")
    if Ed25519PrivateKey is None or serialization is None:
        _fail("cryptography Ed25519 support is unavailable")
    private_key = Ed25519PrivateKey.generate()
    private_payload = base64.b64encode(
        private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ) + b"\n"
    public_payload = base64.b64encode(_public_key_bytes(private_key.public_key())) + b"\n"
    _write_create_only(private_path, private_payload)
    try:
        _write_create_only(public_path, public_payload)
    except Exception:
        # Keep the first create-only output for forensic inspection rather than
        # deleting a key material file after an interrupted bootstrap.
        raise
    return signer_key_id(private_key.public_key())


def _read_json_file(path: Path, *, require_canonical: bool) -> dict[str, Any]:
    payload = _read_stable_regular_file(
        path,
        field="manifest file",
        maximum_bytes=MAX_MANIFEST_BYTES,
    )
    return load_strict_json_bytes(payload, require_canonical=require_canonical)


def _write_create_only(path: Path, payload: bytes) -> None:
    if not path.is_absolute():
        _fail("output path must be absolute")
    parent = path.parent
    try:
        parent_state = parent.lstat()
    except OSError as exc:
        raise EmergencyManifestError("output directory cannot be inspected") from exc
    if stat.S_ISLNK(parent_state.st_mode) or not stat.S_ISDIR(parent_state.st_mode):
        _fail("output directory must be an existing non-symlink directory")
    if parent_state.st_uid != os.geteuid() or parent_state.st_mode & 0o022:
        _fail("output directory must be owned by the caller and not writable by group/other")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(no_follow, int):
        _fail("output path requires O_NOFOLLOW support")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | no_follow,
            0o600,
        )
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:  # pragma: no cover - os.write does not normally return zero.
                raise OSError("short output write")
            remaining = remaining[written:]
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise EmergencyManifestError("refusing to overwrite an existing output file") from exc
    except OSError as exc:
        raise EmergencyManifestError("cannot create output file") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="validate and sign an unsigned local manifest specification")
    build.add_argument("--spec", required=True, type=Path)
    build.add_argument("--signing-private-key", required=True, type=Path)
    build.add_argument("--output", required=True, type=Path)

    keypair = subparsers.add_parser("generate-keypair", help="create a local Ed25519 manifest signing keypair")
    keypair.add_argument("--private-key", required=True, type=Path)
    keypair.add_argument("--public-key", required=True, type=Path)

    verify = subparsers.add_parser("verify", help="verify a sealed manifest and print a receive plan")
    verify.add_argument("--manifest", required=True, type=Path)
    verify.add_argument("--signing-public-key", required=True, type=Path)

    parsed = parser.parse_args(arguments)
    try:
        if parsed.command == "generate-keypair":
            signer_id = generate_keypair(
                private_path=parsed.private_key,
                public_path=parsed.public_key,
            )
            result = {
                "status": "keypair-created-local-only",
                "private_key": str(parsed.private_key),
                "public_key": str(parsed.public_key),
                "signer_key_id": signer_id,
            }
        elif parsed.command == "build":
            signed = sign_manifest(
                _read_json_file(parsed.spec, require_canonical=False),
                private_key=load_private_key(parsed.signing_private_key),
            )
            _write_create_only(parsed.output, canonical_json_bytes(signed))
            result: dict[str, Any] = {
                "status": "built-non-authorizing",
                "manifest_sha256": hashlib.sha256(canonical_json_bytes(signed)).hexdigest(),
                "campaign_id": signed["campaign_id"],
                "output": str(parsed.output),
            }
        else:
            verified = verify_manifest(
                _read_json_file(parsed.manifest, require_canonical=True),
                public_key=load_public_key(parsed.signing_public_key),
            )
            result = verified.as_receive_plan()
    except EmergencyManifestError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
