#!/usr/bin/env python3
"""Publish one sealed Emergency WA-IR artifact campaign to Arvan Object Storage.

This is deliberately the publisher counterpart to the bounded receiver
bootstrap.  It is not a deployment tool: it never contacts WA-IR, decrypts
an artifact, restores a database, loads an image, starts a container, changes
Nginx, or changes a firewall.

The input plan names exactly four *already age-encrypted* local files.  Their
plaintext and ciphertext hashes/sizes are checked locally, the ciphertexts
are uploaded to a private, versioned Arvan bucket and read back by their
immutable VersionIds, and the existing Emergency manifest helper signs the
result.  A small pinned-key receiver bundle, sealed manifest and transient
presigned URL map are then published.  The only URL-bearing output is a
root-only descriptor consumed by ``run_emergency_ir_object_storage_receive``.

No network operation is reachable without both ``--apply`` and the exact
``--confirm`` phrase printed by the prior dry run.  Standard output never
contains presigned URLs or credentials.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, BinaryIO, Callable, Mapping
from urllib.parse import parse_qs, quote, urlsplit

from scripts import build_emergency_ir_receiver_bundle as receiver_bundle
from scripts import emergency_ir_object_storage_manifest as manifest
from scripts import emergency_ir_object_storage_receiver as receiver
from scripts import run_emergency_ir_object_storage_receive as receiver_bootstrap


REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLISH_PLAN_SCHEMA = "gold-trade-emergency-ir-object-storage-publish-plan-v1"
PUBLISH_PLAN_FIELDS = frozenset(
    {
        "schema",
        "campaign_id",
        "bucket",
        "prefix",
        "created_at",
        "destination_age_recipient_key_id",
        "artifacts",
    }
)
ARTIFACT_DESCRIPTOR_FIELDS = frozenset(
    {
        "kind",
        "ciphertext_path",
        "plaintext_sha256",
        "plaintext_bytes",
        "ciphertext_sha256",
        "ciphertext_bytes",
    }
)
MAX_PUBLISH_PLAN_BYTES = 128 * 1024
MAX_CREDENTIALS_BYTES = 8 * 1024
MAX_CONTROL_ARTIFACT_BYTES = 4 * 1024 * 1024
HASH_CHUNK_BYTES = 1024 * 1024
AGE_HEADER = b"age-encryption.org/v1\n"
SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
CONTROL_FILENAMES = {
    "receiver_bundle": "receiver-bootstrap.tar.gz",
    "manifest": "sealed-manifest.json",
    "url_map": "presigned-urls.json",
}
PUBLIC_GRANTEE_URIS = frozenset(
    {
        "http://acs.amazonaws.com/groups/global/AllUsers",
        "http://acs.amazonaws.com/groups/global/AuthenticatedUsers",
    }
)
PRESIGNED_QUERY_FIELDS = frozenset(
    {
        "X-Amz-Algorithm",
        "X-Amz-Credential",
        "X-Amz-Date",
        "X-Amz-Expires",
        "X-Amz-SignedHeaders",
        "X-Amz-Signature",
        "versionId",
    }
)
_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NoSuchVersion", "NotFound"})


class EmergencyPublisherError(RuntimeError):
    """A publish precondition or immutable transfer verification failed."""


@dataclasses.dataclass(frozen=True)
class FileStamp:
    """Stable identity for a locally inspected regular file."""

    dev: int
    ino: int
    mode: int
    uid: int
    gid: int
    nlink: int
    size: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> "FileStamp":
        return cls(
            dev=value.st_dev,
            ino=value.st_ino,
            mode=value.st_mode,
            uid=value.st_uid,
            gid=value.st_gid,
            nlink=value.st_nlink,
            size=value.st_size,
        )


@dataclasses.dataclass(frozen=True)
class ArtifactDescriptor:
    kind: str
    ciphertext_path: Path
    plaintext_sha256: str
    plaintext_bytes: int
    ciphertext_sha256: str
    ciphertext_bytes: int


@dataclasses.dataclass(frozen=True)
class PublishPlan:
    campaign_id: str
    bucket: str
    prefix: str
    created_at: str
    destination_age_recipient_key_id: str
    artifacts: tuple[ArtifactDescriptor, ...]


@dataclasses.dataclass(frozen=True)
class VerifiedLocalArtifact:
    descriptor: ArtifactDescriptor
    stamp: FileStamp


@dataclasses.dataclass(frozen=True)
class UploadedObject:
    key: str
    version_id: str
    sha256: str
    bytes: int


@dataclasses.dataclass(frozen=True)
class PublishOutputs:
    receiver_bundle: Path
    sealed_manifest: Path
    url_map: Path
    descriptor: Path


def _fail(message: str) -> None:
    raise EmergencyPublisherError(message)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("publish input contains a duplicate field")
        result[key] = value
    return result


def _read_owner_regular(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
    require_private: bool,
) -> bytes:
    """Read a stable local input without following links.

    Artifact descriptors identify files that may contain encrypted credentials
    or database data, so the publisher accepts only caller-owned regular files
    that are not writable by another account.  Secret-bearing files additionally
    need 0600-like permissions.
    """

    try:
        before = path.lstat()
    except OSError as exc:
        raise EmergencyPublisherError(f"{label} cannot be inspected") from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or (require_private and stat.S_IMODE(before.st_mode) & 0o077)
        or not 1 <= before.st_size <= maximum_bytes
    ):
        _fail(f"{label} must be one bounded owner-controlled regular file")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if FileStamp.from_stat(opened) != FileStamp.from_stat(before):
            _fail(f"{label} changed while being opened")
        payload = bytearray()
        while len(payload) <= maximum_bytes:
            chunk = os.read(descriptor, min(65536, maximum_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(payload) != opened.st_size
            or len(payload) > maximum_bytes
            or FileStamp.from_stat(after) != FileStamp.from_stat(opened)
        ):
            _fail(f"{label} changed while being read")
        return bytes(payload)
    except OSError as exc:
        raise EmergencyPublisherError(f"{label} cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _load_json(path: Path, *, label: str, maximum_bytes: int, private: bool) -> dict[str, Any]:
    payload = _read_owner_regular(
        path, label=label, maximum_bytes=maximum_bytes, require_private=private
    )
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except EmergencyPublisherError:
        raise
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EmergencyPublisherError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        _fail(f"{label} must be a JSON object")
    return value


def _require_sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        _fail(f"{field} must be a lowercase SHA-256")
    return value


def _require_positive(value: object, *, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        _fail(f"{field} must be a positive bounded integer")
    return value


def _parse_descriptor(value: object, *, expected_kind: str) -> ArtifactDescriptor:
    if not isinstance(value, Mapping) or set(value) != ARTIFACT_DESCRIPTOR_FIELDS:
        _fail("artifact descriptor fields are unsupported")
    if value.get("kind") != expected_kind:
        _fail("artifact descriptors must be complete, unique, and in fixed order")
    path_value = value.get("ciphertext_path")
    if not isinstance(path_value, str) or not path_value or "\x00" in path_value:
        _fail("artifact ciphertext_path is invalid")
    ciphertext_path = Path(path_value)
    if not ciphertext_path.is_absolute():
        _fail("artifact ciphertext_path must be absolute")
    plaintext_sha256 = _require_sha(value.get("plaintext_sha256"), field="artifact plaintext_sha256")
    plaintext_bytes = _require_positive(
        value.get("plaintext_bytes"),
        field="artifact plaintext_bytes",
        maximum=manifest.MAX_ARTIFACT_BYTES,
    )
    ciphertext_sha256 = _require_sha(value.get("ciphertext_sha256"), field="artifact ciphertext_sha256")
    ciphertext_bytes = _require_positive(
        value.get("ciphertext_bytes"),
        field="artifact ciphertext_bytes",
        maximum=manifest.MAX_CIPHERTEXT_BYTES,
    )
    if ciphertext_bytes <= plaintext_bytes or ciphertext_sha256 == plaintext_sha256:
        _fail("artifact ciphertext descriptor is not a plausible age ciphertext")
    return ArtifactDescriptor(
        kind=expected_kind,
        ciphertext_path=ciphertext_path,
        plaintext_sha256=plaintext_sha256,
        plaintext_bytes=plaintext_bytes,
        ciphertext_sha256=ciphertext_sha256,
        ciphertext_bytes=ciphertext_bytes,
    )


def _unsigned_manifest(plan: PublishPlan, *, version_ids: Mapping[str, str]) -> dict[str, Any]:
    """Build the exact unsigned v1 manifest from a validated publish plan."""

    if set(version_ids) != set(manifest.ARTIFACT_ORDER):
        _fail("immutable VersionIds must cover exactly the fixed artifact set")
    artifacts: list[dict[str, Any]] = []
    for descriptor in plan.artifacts:
        artifacts.append(
            {
                "kind": descriptor.kind,
                "format": manifest.ARTIFACT_CONTRACTS[descriptor.kind]["format"],
                "object_key": manifest.expected_object_key(
                    prefix=plan.prefix, campaign_id=plan.campaign_id, kind=descriptor.kind
                ),
                "version_id": version_ids[descriptor.kind],
                "plaintext_sha256": descriptor.plaintext_sha256,
                "plaintext_bytes": descriptor.plaintext_bytes,
                "ciphertext_sha256": descriptor.ciphertext_sha256,
                "ciphertext_bytes": descriptor.ciphertext_bytes,
                "encryption": {
                    "algorithm": "age-v1",
                    "recipient_key_id": plan.destination_age_recipient_key_id,
                },
                "target_path": manifest.expected_target_path(
                    campaign_id=plan.campaign_id, kind=descriptor.kind
                ),
            }
        )
    return {
        "schema": manifest.MANIFEST_SCHEMA,
        "campaign_id": plan.campaign_id,
        "source_site": manifest.SOURCE_SITE,
        "destination_site": manifest.DESTINATION_SITE,
        "endpoint": manifest.APPROVED_ARVAN_ENDPOINT,
        "region": manifest.APPROVED_ARVAN_REGION,
        "bucket": plan.bucket,
        "prefix": plan.prefix,
        "created_at": plan.created_at,
        "destination_age_recipient_key_id": plan.destination_age_recipient_key_id,
        "artifacts": artifacts,
    }


def load_publish_plan(path: Path) -> PublishPlan:
    """Load a local-only, strict four-artifact Emergency publish plan."""

    raw = _load_json(path, label="Emergency publish plan", maximum_bytes=MAX_PUBLISH_PLAN_BYTES, private=False)
    if set(raw) != PUBLISH_PLAN_FIELDS or raw.get("schema") != PUBLISH_PLAN_SCHEMA:
        _fail("Emergency publish plan fields or schema are unsupported")
    raw_artifacts = raw.get("artifacts")
    if not isinstance(raw_artifacts, list) or len(raw_artifacts) != len(manifest.ARTIFACT_ORDER):
        _fail("Emergency publish plan must contain exactly four artifacts")
    descriptors = tuple(
        _parse_descriptor(value, expected_kind=kind)
        for value, kind in zip(raw_artifacts, manifest.ARTIFACT_ORDER, strict=True)
    )
    if len({str(item.ciphertext_path) for item in descriptors}) != len(descriptors):
        _fail("each Emergency artifact must use a distinct ciphertext path")
    provisional = PublishPlan(
        campaign_id=str(raw.get("campaign_id")),
        bucket=str(raw.get("bucket")),
        prefix=str(raw.get("prefix")),
        created_at=str(raw.get("created_at")),
        destination_age_recipient_key_id=str(raw.get("destination_age_recipient_key_id")),
        artifacts=descriptors,
    )
    # Reuse the sealed receiver's exact structural contract before any S3
    # client is constructed.  Placeholder VersionIds are valid only locally;
    # real VersionIds are required again before signing.
    try:
        normalized = manifest.validate_unsigned_manifest(
            _unsigned_manifest(
                provisional,
                version_ids={kind: f"planned-version-{index + 1}" for index, kind in enumerate(manifest.ARTIFACT_ORDER)},
            )
        )
    except manifest.EmergencyManifestError as exc:
        raise EmergencyPublisherError("Emergency publish plan does not satisfy the sealed manifest contract") from exc
    return PublishPlan(
        campaign_id=str(normalized["campaign_id"]),
        bucket=str(normalized["bucket"]),
        prefix=str(normalized["prefix"]),
        created_at=str(normalized["created_at"]),
        destination_age_recipient_key_id=str(normalized["destination_age_recipient_key_id"]),
        artifacts=descriptors,
    )


def _plan_identity(plan: PublishPlan, *, signer_key_id: str, ttl_seconds: int) -> str:
    """Return a non-secret digest binding the required human confirmation."""

    value = {
        "schema": PUBLISH_PLAN_SCHEMA,
        "campaign_id": plan.campaign_id,
        "bucket": plan.bucket,
        "prefix": plan.prefix,
        "created_at": plan.created_at,
        "destination_age_recipient_key_id": plan.destination_age_recipient_key_id,
        "signer_key_id": signer_key_id,
        "presigned_ttl_seconds": ttl_seconds,
        "artifacts": [
            {
                "kind": item.kind,
                "plaintext_sha256": item.plaintext_sha256,
                "plaintext_bytes": item.plaintext_bytes,
                "ciphertext_sha256": item.ciphertext_sha256,
                "ciphertext_bytes": item.ciphertext_bytes,
            }
            for item in plan.artifacts
        ],
    }
    return hashlib.sha256(manifest.canonical_json_bytes(value)).hexdigest()


def confirmation_phrase(plan: PublishPlan, *, signer_key_id: str, ttl_seconds: int) -> str:
    return f"publish-emergency-ir:{plan.campaign_id}:{_plan_identity(plan, signer_key_id=signer_key_id, ttl_seconds=ttl_seconds)}"


def _verify_local_ciphertext(descriptor: ArtifactDescriptor) -> VerifiedLocalArtifact:
    """Check hash, exact size and age header before any external request."""

    path = descriptor.ciphertext_path
    try:
        before = path.lstat()
    except OSError as exc:
        raise EmergencyPublisherError(f"{descriptor.kind} ciphertext cannot be inspected") from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o077
        or before.st_size != descriptor.ciphertext_bytes
    ):
        _fail(f"{descriptor.kind} ciphertext must be a root-only sealed regular file")
    descriptor_fd: int | None = None
    try:
        descriptor_fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor_fd)
        if FileStamp.from_stat(opened) != FileStamp.from_stat(before):
            _fail(f"{descriptor.kind} ciphertext changed while being opened")
        digest = hashlib.sha256()
        observed = 0
        header = bytearray()
        while True:
            chunk = os.read(descriptor_fd, HASH_CHUNK_BYTES)
            if not chunk:
                break
            observed += len(chunk)
            if observed > descriptor.ciphertext_bytes:
                _fail(f"{descriptor.kind} ciphertext exceeds its declared size")
            if len(header) < len(AGE_HEADER):
                header.extend(chunk[: len(AGE_HEADER) - len(header)])
            digest.update(chunk)
        after = os.fstat(descriptor_fd)
        if (
            observed != descriptor.ciphertext_bytes
            or digest.hexdigest() != descriptor.ciphertext_sha256
            or not bytes(header).startswith(AGE_HEADER)
            or FileStamp.from_stat(after) != FileStamp.from_stat(opened)
        ):
            _fail(f"{descriptor.kind} ciphertext does not match its sealed age descriptor")
        return VerifiedLocalArtifact(descriptor=descriptor, stamp=FileStamp.from_stat(opened))
    except OSError as exc:
        raise EmergencyPublisherError(f"{descriptor.kind} ciphertext cannot be read") from exc
    finally:
        if descriptor_fd is not None:
            os.close(descriptor_fd)


def _ensure_safe_output(path: Path, *, label: str) -> None:
    if not path.is_absolute() or not path.name or path.name in {".", ".."}:
        _fail(f"{label} output path must be absolute")
    try:
        parent = path.parent.lstat()
    except OSError as exc:
        raise EmergencyPublisherError(f"{label} output directory cannot be inspected") from exc
    if (
        stat.S_ISLNK(parent.st_mode)
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        _fail(f"{label} output directory is not owner-controlled")
    try:
        state = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise EmergencyPublisherError(f"{label} output path cannot be inspected") from exc
    if state:
        _fail(f"refusing to overwrite existing {label} output")


def _write_create_only(path: Path, payload: bytes, *, label: str) -> None:
    _ensure_safe_output(path, label=label)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        remainder = memoryview(payload)
        while remainder:
            written = os.write(descriptor, remainder)
            if written <= 0:  # pragma: no cover - os.write does not normally return zero.
                raise OSError("short output write")
            remainder = remainder[written:]
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise EmergencyPublisherError(f"refusing to overwrite existing {label} output") from exc
    except OSError as exc:
        raise EmergencyPublisherError(f"{label} output cannot be created") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _inspect_created_file(path: Path, *, label: str, maximum_bytes: int) -> tuple[FileStamp, str, int]:
    payload = _read_owner_regular(path, label=label, maximum_bytes=maximum_bytes, require_private=True)
    state = path.lstat()
    return FileStamp.from_stat(state), hashlib.sha256(payload).hexdigest(), len(payload)


def _open_verified_source(path: Path, *, expected: FileStamp, label: str) -> BinaryIO:
    """Open the same private file whose hash was just preflighted."""

    try:
        before = path.lstat()
    except OSError as exc:
        raise EmergencyPublisherError(f"{label} cannot be re-opened") from exc
    if FileStamp.from_stat(before) != expected:
        _fail(f"{label} changed after local verification")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise EmergencyPublisherError(f"{label} cannot be opened for upload") from exc
    opened = os.fstat(descriptor)
    if FileStamp.from_stat(opened) != expected:
        os.close(descriptor)
        _fail(f"{label} changed while being opened for upload")
    return os.fdopen(descriptor, "rb", closefd=True)


def _s3_error_code(exc: BaseException) -> str | None:
    response = getattr(exc, "response", None)
    if not isinstance(response, Mapping):
        return None
    error = response.get("Error")
    if not isinstance(error, Mapping):
        return None
    code = error.get("Code")
    return str(code) if code is not None else None


def _require_immutable_version(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or value == "null" or len(value) > 1024:
        _fail(f"{label} did not return an immutable Object Storage VersionId")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        _fail(f"{label} returned an unsafe Object Storage VersionId")
    return value


def _require_private_versioned_bucket(client: Any, *, bucket: str) -> None:
    """Fail closed unless all bucket privacy and versioning controls are visible."""

    try:
        versioning = client.get_bucket_versioning(Bucket=bucket)
        if not isinstance(versioning, Mapping) or versioning.get("Status") != "Enabled":
            _fail("Arvan bucket versioning must be Enabled")
        public_access = client.get_public_access_block(Bucket=bucket)
        configuration = public_access.get("PublicAccessBlockConfiguration") if isinstance(public_access, Mapping) else None
        required = {
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        }
        if not isinstance(configuration, Mapping) or any(
            configuration.get(key) is not expected for key, expected in required.items()
        ):
            _fail("Arvan bucket public-access block must be fully enabled")
        try:
            policy_status = client.get_bucket_policy_status(Bucket=bucket)
        except Exception as exc:  # no policy is safe; every other unknown response is not.
            if _s3_error_code(exc) != "NoSuchBucketPolicy":
                raise
        else:
            status = policy_status.get("PolicyStatus") if isinstance(policy_status, Mapping) else None
            if not isinstance(status, Mapping) or status.get("IsPublic") is not False:
                _fail("Arvan bucket policy must not be public")
        acl = client.get_bucket_acl(Bucket=bucket)
        grants = acl.get("Grants") if isinstance(acl, Mapping) else None
        if not isinstance(grants, list):
            _fail("Arvan bucket ACL cannot be verified")
        for grant in grants:
            grantee = grant.get("Grantee") if isinstance(grant, Mapping) else None
            uri = grantee.get("URI") if isinstance(grantee, Mapping) else None
            if uri in PUBLIC_GRANTEE_URIS:
                _fail("Arvan bucket ACL must not grant public access")
    except EmergencyPublisherError:
        raise
    except Exception as exc:
        raise EmergencyPublisherError("Arvan bucket privacy/versioning cannot be verified") from exc


def _assert_key_unused(client: Any, *, bucket: str, key: str) -> None:
    try:
        client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if _s3_error_code(exc) in _NOT_FOUND_CODES:
            return
        raise EmergencyPublisherError("Object Storage key availability cannot be verified") from exc
    _fail("refusing to publish over an existing Emergency campaign object")


def _readback_object(
    client: Any,
    *,
    bucket: str,
    key: str,
    version_id: str,
    expected_sha256: str,
    expected_bytes: int,
) -> None:
    """Verify the object stream selected by the returned immutable VersionId."""

    try:
        head = client.head_object(Bucket=bucket, Key=key, VersionId=version_id)
        if (
            not isinstance(head, Mapping)
            or head.get("ContentLength") != expected_bytes
            or head.get("VersionId") != version_id
        ):
            _fail("Object Storage immutable head readback differs from the uploaded object")
        response = client.get_object(Bucket=bucket, Key=key, VersionId=version_id)
        if not isinstance(response, Mapping) or response.get("ContentLength") != expected_bytes:
            _fail("Object Storage immutable GET readback differs from the uploaded object")
        body = response.get("Body")
        if body is None or not callable(getattr(body, "read", None)):
            _fail("Object Storage immutable GET body is unavailable")
        digest = hashlib.sha256()
        observed = 0
        try:
            while True:
                chunk = body.read(HASH_CHUNK_BYTES)
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    _fail("Object Storage immutable GET returned a non-bytes body")
                observed += len(chunk)
                if observed > expected_bytes:
                    _fail("Object Storage immutable GET exceeds its expected size")
                digest.update(chunk)
        finally:
            closer = getattr(body, "close", None)
            if callable(closer):
                closer()
        if observed != expected_bytes or digest.hexdigest() != expected_sha256:
            _fail("Object Storage immutable GET hash/size differs from the uploaded object")
    except EmergencyPublisherError:
        raise
    except Exception as exc:
        raise EmergencyPublisherError("Object Storage immutable readback failed") from exc


def _upload_and_readback(
    client: Any,
    *,
    bucket: str,
    key: str,
    source_path: Path,
    source_stamp: FileStamp,
    expected_sha256: str,
    expected_bytes: int,
    label: str,
) -> UploadedObject:
    """Create one fresh object version then verify exactly that version's body."""

    _assert_key_unused(client, bucket=bucket, key=key)
    source = _open_verified_source(source_path, expected=source_stamp, label=label)
    try:
        response = client.put_object(
            Bucket=bucket,
            Key=key,
            Body=source,
            ContentType="application/octet-stream",
            Metadata={"sha256": expected_sha256, "emergency-artifact": label},
        )
        version_id = _require_immutable_version(
            response.get("VersionId") if isinstance(response, Mapping) else None,
            label=label,
        )
        uploaded_state = os.fstat(source.fileno())
        if FileStamp.from_stat(uploaded_state) != source_stamp:
            _fail(f"{label} changed while being uploaded")
    except EmergencyPublisherError:
        raise
    except Exception as exc:
        raise EmergencyPublisherError("Object Storage upload failed") from exc
    finally:
        source.close()
    _readback_object(
        client,
        bucket=bucket,
        key=key,
        version_id=version_id,
        expected_sha256=expected_sha256,
        expected_bytes=expected_bytes,
    )
    return UploadedObject(key=key, version_id=version_id, sha256=expected_sha256, bytes=expected_bytes)


def _control_key(plan: PublishPlan, kind: str) -> str:
    try:
        filename = CONTROL_FILENAMES[kind]
    except KeyError as exc:  # pragma: no cover - internal callers use constants.
        raise EmergencyPublisherError("unknown Emergency control artifact") from exc
    return "/".join((plan.prefix, plan.campaign_id, "control", filename))


def _generate_presigned_get(
    client: Any,
    *,
    bucket: str,
    key: str,
    version_id: str,
    ttl_seconds: int,
) -> str:
    try:
        value = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key, "VersionId": version_id},
            ExpiresIn=ttl_seconds,
            HttpMethod="GET",
        )
    except Exception as exc:
        raise EmergencyPublisherError("Object Storage presigned URL generation failed") from exc
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > receiver.MAX_URL_BYTES:
        _fail("Object Storage presigned URL generation returned an invalid URL")
    _validate_presigned_get(
        value,
        bucket=bucket,
        key=key,
        version_id=version_id,
        ttl_seconds=ttl_seconds,
    )
    return value


def _validate_presigned_get(
    url: str,
    *,
    bucket: str,
    key: str,
    version_id: str,
    ttl_seconds: int,
) -> None:
    """Ensure generated URLs satisfy the receiver's fixed S3 receive shape."""

    try:
        parsed = urlsplit(url)
        endpoint = urlsplit(manifest.APPROVED_ARVAN_ENDPOINT)
    except ValueError as exc:
        raise EmergencyPublisherError("generated Object Storage URL is malformed") from exc
    approved_hosts = {endpoint.hostname, f"{bucket}.{endpoint.hostname}"}
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname not in approved_hosts
        or parsed.port is not None
        or parsed.fragment
    ):
        _fail("generated Object Storage URL endpoint is outside the approved Arvan endpoint")
    encoded_key = quote(key, safe="/")
    expected_path = (
        "/" + quote(bucket, safe="") + "/" + encoded_key
        if parsed.hostname == endpoint.hostname
        else "/" + encoded_key
    )
    if parsed.path != expected_path:
        _fail("generated Object Storage URL does not bind the expected object key")
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise EmergencyPublisherError("generated Object Storage URL query is malformed") from exc
    if not PRESIGNED_QUERY_FIELDS.issubset(query) or any(len(values) != 1 for values in query.values()):
        _fail("generated Object Storage URL is not one strict presigned request")
    if query["X-Amz-Algorithm"][0] != "AWS4-HMAC-SHA256" or query["versionId"][0] != version_id:
        _fail("generated Object Storage URL does not bind its immutable VersionId")
    try:
        observed_ttl = int(query["X-Amz-Expires"][0], 10)
    except ValueError as exc:
        raise EmergencyPublisherError("generated Object Storage URL expiry is invalid") from exc
    if observed_ttl != ttl_seconds or not receiver.MIN_PRESIGNED_TTL_SECONDS <= observed_ttl <= receiver.MAX_PRESIGNED_TTL_SECONDS:
        _fail("generated Object Storage URL expiry is outside the Emergency bound")


def _build_url_map(
    *,
    client: Any,
    plan: PublishPlan,
    signed_manifest: bytes,
    public_key: Any,
    uploaded_artifacts: Mapping[str, UploadedObject],
    ttl_seconds: int,
) -> bytes:
    """Build the root-only map consumed by the existing sealed receiver."""

    verified = manifest.verify_manifest_bytes(signed_manifest, public_key=public_key)
    receive_plan = verified.as_receive_plan()
    entries: list[dict[str, str]] = []
    for artifact in receive_plan["artifacts"]:
        kind = str(artifact["kind"])
        uploaded = uploaded_artifacts.get(kind)
        if uploaded is None or uploaded.key != artifact["object_key"] or uploaded.version_id != artifact["version_id"]:
            _fail("uploaded artifact versions do not match the sealed manifest")
        url = _generate_presigned_get(
            client,
            bucket=plan.bucket,
            key=uploaded.key,
            version_id=uploaded.version_id,
            ttl_seconds=ttl_seconds,
        )
        # Use the receiver's own validator before emitting any URL-bearing
        # output; publication cannot create a map the receiver would reject.
        receiver._validate_presigned_url(url=url, plan=receive_plan, artifact=artifact)
        entries.append({"kind": kind, "url": url})
    value = {
        "schema": receiver.URL_MAP_SCHEMA,
        "manifest_sha256": receive_plan["manifest_sha256"],
        "artifacts": entries,
    }
    payload = manifest.canonical_json_bytes(value)
    receiver._parse_url_map(payload, manifest_sha256=receive_plan["manifest_sha256"])
    return payload


def _build_bootstrap_descriptor(
    *,
    plan: PublishPlan,
    ttl_seconds: int,
    client: Any,
    receiver_bundle_object: UploadedObject,
    manifest_object: UploadedObject,
    url_map_object: UploadedObject,
) -> bytes:
    """Return the only local file that contains short-lived presigned URLs."""

    control = {
        "receiver_bundle": receiver_bundle_object,
        "manifest": manifest_object,
        "url_map": url_map_object,
    }
    entries: dict[str, dict[str, Any]] = {}
    for kind, uploaded in control.items():
        entries[kind] = {
            "url": _generate_presigned_get(
                client,
                bucket=plan.bucket,
                key=uploaded.key,
                version_id=uploaded.version_id,
                ttl_seconds=ttl_seconds,
            ),
            "sha256": uploaded.sha256,
            "bytes": uploaded.bytes,
        }
    payload = {
        "schema": receiver_bootstrap.SCHEMA,
        "campaign_id": plan.campaign_id,
        "expires_in_seconds": ttl_seconds,
        **entries,
    }
    return manifest.canonical_json_bytes(payload)


def _check_outputs(outputs: PublishOutputs) -> None:
    paths = {
        "receiver bundle": outputs.receiver_bundle,
        "sealed manifest": outputs.sealed_manifest,
        "presigned URL map": outputs.url_map,
        "bootstrap descriptor": outputs.descriptor,
    }
    if len({str(path) for path in paths.values()}) != len(paths):
        _fail("Emergency publisher outputs must use distinct paths")
    for label, path in paths.items():
        _ensure_safe_output(path, label=label)


def publish(
    *,
    client: Any,
    plan: PublishPlan,
    signing_private_key_path: Path,
    signing_public_key_path: Path,
    repo: Path,
    outputs: PublishOutputs,
    ttl_seconds: int,
) -> dict[str, Any]:
    """Perform the guarded publish flow after CLI confirmation has succeeded."""

    if not receiver.MIN_PRESIGNED_TTL_SECONDS <= ttl_seconds <= receiver.MAX_PRESIGNED_TTL_SECONDS:
        _fail("presigned URL TTL is outside the Emergency receiver bound")
    _check_outputs(outputs)
    try:
        private_key = manifest.load_private_key(signing_private_key_path)
        public_key = manifest.load_public_key(signing_public_key_path)
    except manifest.EmergencyManifestError as exc:
        raise EmergencyPublisherError("Emergency signing keys are unavailable or unsafe") from exc
    if manifest.signer_key_id(private_key.public_key()) != manifest.signer_key_id(public_key):
        _fail("Emergency signing private/public keys do not form the pinned keypair")
    verified_artifacts = tuple(_verify_local_ciphertext(item) for item in plan.artifacts)
    _require_private_versioned_bucket(client, bucket=plan.bucket)

    # Verify all campaign locations are unused before writing a local control
    # artifact or uploading the first byte.  Each later result is still bound
    # to its exact returned VersionId to protect against races and later writes.
    keys = [
        *(manifest.expected_object_key(prefix=plan.prefix, campaign_id=plan.campaign_id, kind=item.kind) for item in plan.artifacts),
        *(_control_key(plan, kind) for kind in CONTROL_FILENAMES),
    ]
    for key in keys:
        _assert_key_unused(client, bucket=plan.bucket, key=key)

    try:
        receiver_bundle.build_bundle(
            repo=repo.resolve(), signing_public_key=signing_public_key_path, output=outputs.receiver_bundle
        )
    except receiver_bundle.ReceiverBundleError as exc:
        raise EmergencyPublisherError("pinned-key receiver bootstrap bundle cannot be built") from exc
    bundle_stamp, bundle_hash, bundle_bytes = _inspect_created_file(
        outputs.receiver_bundle,
        label="receiver bootstrap bundle",
        maximum_bytes=MAX_CONTROL_ARTIFACT_BYTES,
    )
    bundle_object = _upload_and_readback(
        client,
        bucket=plan.bucket,
        key=_control_key(plan, "receiver_bundle"),
        source_path=outputs.receiver_bundle,
        source_stamp=bundle_stamp,
        expected_sha256=bundle_hash,
        expected_bytes=bundle_bytes,
        label="receiver bootstrap bundle",
    )

    uploaded_artifacts: dict[str, UploadedObject] = {}
    for item in verified_artifacts:
        descriptor = item.descriptor
        uploaded_artifacts[descriptor.kind] = _upload_and_readback(
            client,
            bucket=plan.bucket,
            key=manifest.expected_object_key(
                prefix=plan.prefix, campaign_id=plan.campaign_id, kind=descriptor.kind
            ),
            source_path=descriptor.ciphertext_path,
            source_stamp=item.stamp,
            expected_sha256=descriptor.ciphertext_sha256,
            expected_bytes=descriptor.ciphertext_bytes,
            label=f"Emergency artifact {descriptor.kind}",
        )

    unsigned = _unsigned_manifest(
        plan, version_ids={kind: uploaded_artifacts[kind].version_id for kind in manifest.ARTIFACT_ORDER}
    )
    try:
        signed = manifest.sign_manifest(unsigned, private_key=private_key)
    except manifest.EmergencyManifestError as exc:
        raise EmergencyPublisherError("sealed Emergency manifest cannot be signed") from exc
    sealed_manifest = manifest.canonical_json_bytes(signed)
    _write_create_only(outputs.sealed_manifest, sealed_manifest, label="sealed manifest")
    manifest_stamp, manifest_hash, manifest_bytes = _inspect_created_file(
        outputs.sealed_manifest,
        label="sealed manifest",
        maximum_bytes=manifest.MAX_MANIFEST_BYTES,
    )
    manifest_object = _upload_and_readback(
        client,
        bucket=plan.bucket,
        key=_control_key(plan, "manifest"),
        source_path=outputs.sealed_manifest,
        source_stamp=manifest_stamp,
        expected_sha256=manifest_hash,
        expected_bytes=manifest_bytes,
        label="sealed manifest",
    )

    url_map_payload = _build_url_map(
        client=client,
        plan=plan,
        signed_manifest=sealed_manifest,
        public_key=public_key,
        uploaded_artifacts=uploaded_artifacts,
        ttl_seconds=ttl_seconds,
    )
    _write_create_only(outputs.url_map, url_map_payload, label="presigned URL map")
    map_stamp, map_hash, map_bytes = _inspect_created_file(
        outputs.url_map,
        label="presigned URL map",
        maximum_bytes=receiver.MAX_URL_MAP_BYTES,
    )
    url_map_object = _upload_and_readback(
        client,
        bucket=plan.bucket,
        key=_control_key(plan, "url_map"),
        source_path=outputs.url_map,
        source_stamp=map_stamp,
        expected_sha256=map_hash,
        expected_bytes=map_bytes,
        label="presigned URL map",
    )

    descriptor_payload = _build_bootstrap_descriptor(
        plan=plan,
        ttl_seconds=ttl_seconds,
        client=client,
        receiver_bundle_object=bundle_object,
        manifest_object=manifest_object,
        url_map_object=url_map_object,
    )
    _write_create_only(outputs.descriptor, descriptor_payload, label="bootstrap descriptor")
    # This deliberately validates the exact on-disk 0600 descriptor using the
    # existing controller-side bootstrap contract without invoking SSH.
    receiver_bootstrap.load_descriptor(outputs.descriptor)
    return {
        "status": "published-sealed",
        "campaign_id": plan.campaign_id,
        "manifest_sha256": manifest_hash,
        "signer_key_id": manifest.signer_key_id(public_key),
        "artifact_count": len(uploaded_artifacts),
        "payload_transport": "private-arvan-object-storage-only",
        "descriptor": str(outputs.descriptor),
        "receiver_bundle": str(outputs.receiver_bundle),
        "sealed_manifest": str(outputs.sealed_manifest),
        "url_map": str(outputs.url_map),
    }


def _load_credentials(path: Path) -> dict[str, str]:
    """Read local S3 credentials only after the apply confirmation succeeds."""

    raw = _load_json(
        path,
        label="Arvan Object Storage credentials",
        maximum_bytes=MAX_CREDENTIALS_BYTES,
        private=True,
    )
    if set(raw) not in ({"access_key_id", "secret_access_key"}, {"access_key_id", "secret_access_key", "session_token"}):
        _fail("Arvan Object Storage credential fields are unsupported")
    result: dict[str, str] = {}
    for field in ("access_key_id", "secret_access_key", "session_token"):
        value = raw.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value or len(value) > 4096 or "\x00" in value:
            _fail("Arvan Object Storage credentials are invalid")
        result[field] = value
    if set(result) < {"access_key_id", "secret_access_key"}:
        _fail("Arvan Object Storage credentials are incomplete")
    return result


def make_s3_client(credentials_path: Path) -> Any:
    """Create the one fixed-endpoint S3 client lazily, after confirmation."""

    credentials = _load_credentials(credentials_path)
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise EmergencyPublisherError("boto3 with botocore is required for the guarded publish step") from exc
    try:
        return boto3.session.Session(
            aws_access_key_id=credentials["access_key_id"],
            aws_secret_access_key=credentials["secret_access_key"],
            aws_session_token=credentials.get("session_token"),
            region_name=manifest.APPROVED_ARVAN_REGION,
        ).client(
            "s3",
            endpoint_url=manifest.APPROVED_ARVAN_ENDPOINT,
            region_name=manifest.APPROVED_ARVAN_REGION,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
    except Exception as exc:
        raise EmergencyPublisherError("Arvan Object Storage client cannot be initialized") from exc


def _load_public_key_id(path: Path) -> str:
    try:
        return manifest.signer_key_id(manifest.load_public_key(path))
    except manifest.EmergencyManifestError as exc:
        raise EmergencyPublisherError("pinned Emergency signing public key is unavailable or unsafe") from exc


def execute(
    args: argparse.Namespace,
    *,
    client_factory: Callable[[Path], Any] = make_s3_client,
) -> dict[str, Any]:
    plan = load_publish_plan(args.plan)
    if not receiver.MIN_PRESIGNED_TTL_SECONDS <= args.ttl_seconds <= receiver.MAX_PRESIGNED_TTL_SECONDS:
        _fail("presigned URL TTL is outside the Emergency receiver bound")
    signer_key_id = _load_public_key_id(args.signing_public_key)
    expected_confirmation = confirmation_phrase(
        plan, signer_key_id=signer_key_id, ttl_seconds=args.ttl_seconds
    )
    if not args.apply:
        return {
            "status": "planned-no-network",
            "campaign_id": plan.campaign_id,
            "artifact_count": len(plan.artifacts),
            "payload_transport": "private-arvan-object-storage-only",
            "required_confirmation": expected_confirmation,
        }
    if args.confirm != expected_confirmation:
        _fail("Emergency Object Storage publish confirmation mismatch")
    if args.credentials is None:
        _fail("Arvan Object Storage credentials are required with --apply")
    outputs = PublishOutputs(
        receiver_bundle=args.receiver_bundle_output,
        sealed_manifest=args.sealed_manifest_output,
        url_map=args.url_map_output,
        descriptor=args.descriptor_output,
    )
    client = client_factory(args.credentials)
    return publish(
        client=client,
        plan=plan,
        signing_private_key_path=args.signing_private_key,
        signing_public_key_path=args.signing_public_key,
        repo=args.repo,
        outputs=outputs,
        ttl_seconds=args.ttl_seconds,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--signing-private-key", type=Path, required=True)
    parser.add_argument("--signing-public-key", type=Path, required=True)
    parser.add_argument("--credentials", type=Path)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--receiver-bundle-output", type=Path, required=True)
    parser.add_argument("--sealed-manifest-output", type=Path, required=True)
    parser.add_argument("--url-map-output", type=Path, required=True)
    parser.add_argument("--descriptor-output", type=Path, required=True)
    parser.add_argument("--ttl-seconds", type=int, default=300)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = execute(args)
    except EmergencyPublisherError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    except Exception:
        # Do not leak provider URLs, credentials, or provider exception text.
        print(json.dumps({"status": "blocked", "error": "Emergency Object Storage publish failed", "error_class": "UnexpectedError"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
