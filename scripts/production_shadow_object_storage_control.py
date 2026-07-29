#!/usr/bin/env python3
"""Private, versioned Object Storage control-artifact primitives.

This module deliberately has a narrower trust boundary than the existing
WA-IR release/data artifact publisher.  It transports only root-owned control
request documents and encrypted result documents for a single operation.  It
does not run a command, install a release, or open SSH.  The remote receiver
has a separate, fixed request-type allowlist.

The controller publishes requests with the already hardened age/private/
versioned publisher.  Results use a fresh, controller-chosen create-only key:
the remote cannot know the ciphertext digest before it encrypts the result, so
the result key is bound to a UUID upload id rather than to a ciphertext hash.
The controller must read back the returned exact VersionId before accepting a
result.  URLs are intentionally never included in durable evidence objects.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
from typing import Any, Mapping
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import UUID, uuid4


from scripts.wa_ir_production_object_storage_transport import (
    EphemeralPresignedGet,
    PublishedObject,
    presign_exact_get,
    publish_age_encrypted,
    recover_create_only_exact_version,
)
from scripts.wa_ir_production_transport_contract import (
    ARVAN_HOST,
    ARVAN_REGION,
    PRODUCTION_BUCKET,
    ProductionTransportError,
    SHA256_RE,
    validate_operation_id,
    validate_prefix,
)
from core.secure_file_io import (
    SecureFileError,
    read_secure_bytes,
    write_secure_new_bytes,
)
from scripts.receive_wa_ir_production_artifact import (
    ProductionReceiveError,
    parse_descriptor,
)


REQUEST_ENVELOPE_SCHEMA = "production-shadow-object-storage-control-request-v1"
RESULT_UPLOAD_SCHEMA = "production-shadow-object-storage-control-result-upload-v1"
RESULT_METADATA_SCHEMA = "production-shadow-object-storage-control-result-age-v1"
RESULT_RECOVERY_INTENT_SCHEMA = (
    "production-shadow-object-storage-control-result-recovery-intent-v1"
)
RESULT_RECOVERY_RECEIPT_SCHEMA = (
    "production-shadow-object-storage-control-result-recovery-receipt-v1"
)
REQUEST_ARTIFACT_KIND = "control-request"
RESULT_ARTIFACT_KIND = "control-result"

REQUEST_ENVELOPE_FIELDS = frozenset(
    {
        "schema",
        "request_type",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "worker_request",
        "worker_request_sha256",
        "request_sha256",
    }
)
RESULT_GRANT_FIELDS = frozenset(
    {
        "schema",
        "bucket",
        "object_key",
        "upload_id",
        "operation_id",
        "role",
        "request_sha256",
        "ttl_seconds",
    }
)
RESULT_RECOVERY_INTENT_FIELDS = frozenset(
    {
        "schema",
        "prefix",
        "bucket",
        "object_key",
        "upload_id",
        "operation_id",
        "role",
        "request_sha256",
        "ttl_seconds",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "metadata",
    }
)
RESULT_RECOVERY_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "recovery_intent_sha256",
        "bucket",
        "object_key",
        "upload_id",
        "operation_id",
        "role",
        "request_sha256",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "metadata",
        "version_id",
    }
)
REQUEST_TYPE_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}-v[1-9][0-9]*$")
ROLE_RE = re.compile(r"^(?:webapp_fi|webapp_ir|witness)$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
MIN_URL_TTL_SECONDS = 60
MAX_URL_TTL_SECONDS = 900
MAX_CONTROL_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESULT_CIPHERTEXT_BYTES = 8 * 1024 * 1024
MAX_URL_BYTES = 16 * 1024
MAX_URL_CONTROL_ARGUMENT_BYTES = ((MAX_URL_BYTES + 2) // 3) * 4
MAX_RESULT_RECOVERY_JOURNAL_BYTES = 64 * 1024
CLOCK_SKEW_SECONDS = 300


class ControlTransportError(RuntimeError):
    """Raised when an Object Storage control binding cannot be proven."""


@dataclass(frozen=True)
class RequestPublication:
    """Durable request evidence plus an ephemeral exact-version GET URL."""

    published: PublishedObject
    presigned: EphemeralPresignedGet
    request_sha256: str
    request_bytes: int
    request_type: str
    role: str

    def evidence(self) -> dict[str, Any]:
        return {
            "bucket": self.published.bucket,
            "object_key": self.published.object_key,
            "version_id": self.published.version_id,
            "plaintext_sha256": self.published.plaintext_sha256,
            "plaintext_bytes": self.published.plaintext_bytes,
            "ciphertext_sha256": self.published.ciphertext_sha256,
            "ciphertext_bytes": self.published.ciphertext_bytes,
            "metadata": dict(self.published.metadata),
            "request_sha256": self.request_sha256,
            "request_bytes": self.request_bytes,
            "request_type": self.request_type,
            "role": self.role,
            "presigned_url_persisted": False,
        }


@dataclass(frozen=True)
class ResultUploadGrant:
    """One ephemeral exact-key PUT authority, with no URL in durable state."""

    bucket: str
    object_key: str
    upload_id: str
    operation_id: str
    role: str
    request_sha256: str
    ttl_seconds: int

    def document(self) -> dict[str, Any]:
        return {
            "schema": RESULT_UPLOAD_SCHEMA,
            "bucket": self.bucket,
            "object_key": self.object_key,
            "upload_id": self.upload_id,
            "operation_id": self.operation_id,
            "role": self.role,
            "request_sha256": self.request_sha256,
            "ttl_seconds": self.ttl_seconds,
        }

    def metadata(self) -> dict[str, str]:
        return {
            "transport-schema": RESULT_METADATA_SCHEMA,
            "operation-id": self.operation_id,
            "role": self.role,
            "request-sha256": self.request_sha256,
            "upload-id": self.upload_id,
            "artifact-kind": RESULT_ARTIFACT_KIND,
        }


@dataclass(frozen=True)
class ResultObject:
    """Non-secret exact-version result receipt returned by a receiver."""

    bucket: str
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    metadata: Mapping[str, str]

    def evidence(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "object_key": self.object_key,
            "version_id": self.version_id,
            "ciphertext_sha256": self.ciphertext_sha256,
            "ciphertext_bytes": self.ciphertext_bytes,
            "metadata": dict(self.metadata),
            "presigned_url_persisted": False,
        }


@dataclass(frozen=True)
class ResultUploadRecoveryIntent:
    """Non-secret durable binding for a lost create-only result PUT reply.

    This is the only record a target or controller needs to retain before a
    future read-only recovery.  It intentionally excludes the presigned URL,
    ciphertext payload and every private key.  The controller can recover only
    the already-bound current key and only after exact-version readback.
    """

    prefix: str
    bucket: str
    object_key: str
    upload_id: str
    operation_id: str
    role: str
    request_sha256: str
    ttl_seconds: int
    ciphertext_sha256: str
    ciphertext_bytes: int
    metadata: Mapping[str, str]

    def document(self) -> dict[str, Any]:
        if not isinstance(self.metadata, Mapping):
            raise ControlTransportError("control result recovery metadata is invalid")
        try:
            metadata = dict(self.metadata)
        except (TypeError, ValueError) as exc:
            raise ControlTransportError("control result recovery metadata is invalid") from exc
        return {
            "schema": RESULT_RECOVERY_INTENT_SCHEMA,
            "prefix": self.prefix,
            "bucket": self.bucket,
            "object_key": self.object_key,
            "upload_id": self.upload_id,
            "operation_id": self.operation_id,
            "role": self.role,
            "request_sha256": self.request_sha256,
            "ttl_seconds": self.ttl_seconds,
            "ciphertext_sha256": self.ciphertext_sha256,
            "ciphertext_bytes": self.ciphertext_bytes,
            "metadata": metadata,
        }

    def sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.document())).hexdigest()


@dataclass(frozen=True)
class ReceiverControlCommand:
    """Fixed remote receiver argv; request/result payload bytes are absent."""

    argv: tuple[str, ...]
    application_payload_bytes_over_ssh: int = 0

    def remote_command(self) -> str:
        """Return exactly one shell-safe remote command argument for SSH.

        A pinned SSH caller must pass this value as its sole remote command
        argument.  ``shlex.join`` preserves the fixed argv boundary and does
        not create a generic shell execution surface.
        """

        rendered = shlex.join(self.argv)
        if not rendered or any(character in rendered for character in "\r\n\x00"):
            raise ControlTransportError("receiver remote command is unsafe")
        return rendered

    def evidence(self) -> dict[str, Any]:
        return {
            "application_payload_bytes_over_ssh": self.application_payload_bytes_over_ssh,
            "presigned_url_persisted": False,
            "presigned_urls_base64url_encoded": True,
            "remote_command_single_argv": True,
            "generic_shell_execution_used": False,
        }


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ControlTransportError("control document is not canonical JSON") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ControlTransportError("control document has duplicate JSON fields")
        document[key] = value
    return document


def encode_control_url_argument(url: str, *, label: str) -> str:
    """Encode one short-lived URL for a bounded remote argv slot.

    The URL remains ephemeral and is never suitable for a durable record.  A
    canonical unpadded base64url representation avoids query delimiters in the
    command string while keeping the receiver's typed decoder deterministic.
    """

    if (
        not isinstance(url, str)
        or not 1 <= len(url.encode("ascii")) <= MAX_URL_BYTES
        or not url.isascii()
        or any(character in url for character in "\r\n\x00")
    ):
        raise ControlTransportError(f"{label} URL is malformed")
    encoded = base64.urlsafe_b64encode(url.encode("ascii")).decode("ascii").rstrip("=")
    if (
        not encoded
        or len(encoded) > MAX_URL_CONTROL_ARGUMENT_BYTES
        or BASE64URL_RE.fullmatch(encoded) is None
    ):
        raise ControlTransportError(f"{label} URL cannot be encoded safely")
    return encoded


def decode_control_url_argument(value: Any, *, label: str) -> str:
    """Strictly decode the canonical base64url form used by remote control.

    Callers must immediately pass the returned URL to their exact presigned
    URL validator.  This function deliberately does no I/O and rejects any
    alternate padding or non-canonical spelling before a URL reaches a parser.
    """

    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_URL_CONTROL_ARGUMENT_BYTES
        or BASE64URL_RE.fullmatch(value) is None
    ):
        raise ControlTransportError(f"{label} URL encoding is invalid")
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.b64decode(
            (value + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        url = raw.decode("ascii")
    except (UnicodeError, ValueError, base64.binascii.Error) as exc:
        raise ControlTransportError(f"{label} URL encoding is invalid") from exc
    if encode_control_url_argument(url, label=label) != value:
        raise ControlTransportError(f"{label} URL encoding is not canonical")
    return url


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == "0" * 64
    ):
        raise ControlTransportError(f"{label} SHA-256 is invalid")
    return value


def _canonical_uuid(value: Any, *, label: str) -> str:
    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ControlTransportError(f"{label} must be a canonical UUID") from exc
    canonical = str(parsed)
    if canonical != value or parsed.int == 0:
        raise ControlTransportError(f"{label} must be a nonzero canonical UUID")
    return canonical


def _validate_role(value: Any) -> str:
    if not isinstance(value, str) or ROLE_RE.fullmatch(value) is None:
        raise ControlTransportError("control role is invalid")
    return value


def _validate_request_type(value: Any) -> str:
    if not isinstance(value, str) or REQUEST_TYPE_RE.fullmatch(value) is None:
        raise ControlTransportError("control request type is invalid")
    return value


def validate_request_envelope(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a typed payload before encryption or after exact readback."""

    if not isinstance(value, Mapping) or set(value) != REQUEST_ENVELOPE_FIELDS:
        raise ControlTransportError("control request envelope fields are not exact")
    try:
        document = json.loads(
            _canonical_json(dict(value)).decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ControlTransportError("control request envelope is invalid JSON") from exc
    if document["schema"] != REQUEST_ENVELOPE_SCHEMA:
        raise ControlTransportError("control request envelope schema differs")
    _validate_request_type(document["request_type"])
    _canonical_uuid(document["campaign_id"], label="campaign id")
    _canonical_uuid(document["operation_id"], label="operation id")
    if document["campaign_id"] == document["operation_id"]:
        raise ControlTransportError("campaign and operation ids must differ")
    if (
        not isinstance(document["release_sha"], str)
        or SHA40_RE.fullmatch(document["release_sha"]) is None
        or not isinstance(document["release_tree_sha"], str)
        or SHA40_RE.fullmatch(document["release_tree_sha"]) is None
    ):
        raise ControlTransportError("control request release binding is invalid")
    _validate_role(document["role"])
    if not isinstance(document["worker_request"], dict):
        raise ControlTransportError("control worker request must be an object")
    worker_bytes = _canonical_json(document["worker_request"])
    if len(worker_bytes) > MAX_CONTROL_REQUEST_BYTES:
        raise ControlTransportError("control worker request exceeds its bound")
    if _nonzero_sha256(
        document["worker_request_sha256"], label="worker request"
    ) != hashlib.sha256(worker_bytes).hexdigest():
        raise ControlTransportError("control worker request digest differs")
    unsigned = {key: item for key, item in document.items() if key != "request_sha256"}
    if _nonzero_sha256(document["request_sha256"], label="control request") != hashlib.sha256(
        _canonical_json(unsigned)
    ).hexdigest():
        raise ControlTransportError("control request digest differs")
    return document


def request_envelope_payload(value: Mapping[str, Any]) -> bytes:
    document = validate_request_envelope(value)
    payload = _canonical_json(document) + b"\n"
    if not 1 <= len(payload) <= MAX_CONTROL_REQUEST_BYTES:
        raise ControlTransportError("control request payload exceeds its bound")
    return payload


def request_destination_name(request_sha256: str) -> str:
    digest = _nonzero_sha256(request_sha256, label="control request")
    return f"{digest}.json"


def build_result_upload_grant(
    *,
    prefix: str,
    operation_id: str,
    role: str,
    request_sha256: str,
    ttl_seconds: int,
    upload_id: str | None = None,
) -> ResultUploadGrant:
    try:
        prefix = validate_prefix(prefix)
        operation_id = validate_operation_id(operation_id)
    except ProductionTransportError as exc:
        raise ControlTransportError("control result object namespace is invalid") from exc
    role = _validate_role(role)
    request_sha256 = _nonzero_sha256(request_sha256, label="control request")
    if not MIN_URL_TTL_SECONDS <= ttl_seconds <= MAX_URL_TTL_SECONDS:
        raise ControlTransportError("control result URL lifetime is invalid")
    raw_upload_id = str(uuid4()) if upload_id is None else upload_id
    upload_id = _canonical_uuid(raw_upload_id, label="result upload id")
    object_key = (
        f"{prefix}/{operation_id}/{RESULT_ARTIFACT_KIND}/"
        f"{role}/{request_sha256}/{upload_id}.age"
    )
    return ResultUploadGrant(
        bucket=PRODUCTION_BUCKET,
        object_key=object_key,
        upload_id=upload_id,
        operation_id=operation_id,
        role=role,
        request_sha256=request_sha256,
        ttl_seconds=ttl_seconds,
    )


def validate_result_upload_grant(value: Mapping[str, Any], *, prefix: str) -> ResultUploadGrant:
    if not isinstance(value, Mapping) or set(value) != RESULT_GRANT_FIELDS:
        raise ControlTransportError("control result grant fields are not exact")
    if value.get("schema") != RESULT_UPLOAD_SCHEMA:
        raise ControlTransportError("control result grant schema differs")
    try:
        prefix = validate_prefix(prefix)
        operation_id = validate_operation_id(str(value["operation_id"]))
    except ProductionTransportError as exc:
        raise ControlTransportError("control result grant identity is invalid") from exc
    role = _validate_role(value["role"])
    request_sha256 = _nonzero_sha256(value["request_sha256"], label="control request")
    upload_id = _canonical_uuid(value["upload_id"], label="result upload id")
    ttl_seconds = value["ttl_seconds"]
    if (
        value["bucket"] != PRODUCTION_BUCKET
        or isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, int)
        or not MIN_URL_TTL_SECONDS <= ttl_seconds <= MAX_URL_TTL_SECONDS
    ):
        raise ControlTransportError("control result grant storage binding differs")
    expected_key = (
        f"{prefix}/{operation_id}/{RESULT_ARTIFACT_KIND}/"
        f"{role}/{request_sha256}/{upload_id}.age"
    )
    if value["object_key"] != expected_key:
        raise ControlTransportError("control result grant object key differs")
    return ResultUploadGrant(
        bucket=PRODUCTION_BUCKET,
        object_key=expected_key,
        upload_id=upload_id,
        operation_id=operation_id,
        role=role,
        request_sha256=request_sha256,
        ttl_seconds=ttl_seconds,
    )


def presign_result_upload(client: Any, grant: ResultUploadGrant) -> str:
    """Mint one PUT URL with headers binding it to one create-only result key."""

    if not isinstance(grant, ResultUploadGrant):
        raise ControlTransportError("control result upload grant is invalid")
    metadata = grant.metadata()
    try:
        url = client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": grant.bucket,
                "Key": grant.object_key,
                "ContentType": "application/octet-stream",
                "IfNoneMatch": "*",
                "Metadata": metadata,
            },
            ExpiresIn=grant.ttl_seconds,
        )
    except Exception as exc:
        raise ControlTransportError("control result URL generation failed") from exc
    validate_result_upload_url(str(url), grant=grant)
    return str(url)


def validate_result_upload_url(
    url: str,
    *,
    grant: ResultUploadGrant,
    now: datetime | None = None,
) -> None:
    """Prove that a result PUT URL can only create this exact object key.

    This deliberately validates the URL both when the controller mints it and
    again on the target before a worker can run.  A relay therefore cannot
    substitute a broader or stale URL after the controller-side check.
    """

    if not isinstance(url, str) or not 1 <= len(url.encode("utf-8")) <= MAX_URL_BYTES:
        raise ControlTransportError("control result URL is malformed")
    try:
        parsed = urlsplit(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ControlTransportError("control result URL is malformed") from exc
    required = {
        "X-Amz-Algorithm",
        "X-Amz-Credential",
        "X-Amz-Date",
        "X-Amz-Expires",
        "X-Amz-Signature",
        "X-Amz-SignedHeaders",
    }
    optional = {"X-Amz-Security-Token"}
    if (
        parsed.scheme != "https"
        or parsed.hostname != ARVAN_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or unquote(parsed.path) != f"/{grant.bucket}/{grant.object_key}"
        or parsed.fragment
        or required - set(query)
        or set(query) - required - optional
        or any(len(values) != 1 or not values[0] for values in query.values())
        or query.get("X-Amz-Algorithm") != ["AWS4-HMAC-SHA256"]
        or query.get("X-Amz-Expires") != [str(grant.ttl_seconds)]
        or not re.fullmatch(r"[0-9a-fA-F]{64}", query["X-Amz-Signature"][0])
    ):
        raise ControlTransportError("control result URL is not exact-key HTTPS")
    signed = set(query["X-Amz-SignedHeaders"][0].split(";"))
    required_headers = {
        "host",
        "content-type",
        "if-none-match",
        *{f"x-amz-meta-{key}" for key in grant.metadata()},
    }
    if not required_headers <= signed:
        raise ControlTransportError("control result URL lacks signed create-only headers")
    try:
        issued_at = datetime.strptime(
            query["X-Amz-Date"][0], "%Y%m%dT%H%M%SZ"
        ).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ControlTransportError("control result URL time binding is invalid") from exc
    credential = query["X-Amz-Credential"][0].split("/")
    if (
        len(credential) != 5
        or not credential[0]
        or credential[1] != issued_at.strftime("%Y%m%d")
        or credential[2] != ARVAN_REGION
        or credential[3:] != ["s3", "aws4_request"]
    ):
        raise ControlTransportError("control result URL credential scope differs")
    observed_at = datetime.now(timezone.utc) if now is None else now
    if observed_at.tzinfo is None:
        raise ControlTransportError("control result URL clock is invalid")
    observed_at = observed_at.astimezone(timezone.utc)
    if (
        observed_at < issued_at - timedelta(seconds=CLOCK_SKEW_SECONDS)
        or observed_at > issued_at + timedelta(seconds=grant.ttl_seconds)
    ):
        raise ControlTransportError("control result URL is expired or outside its time bound")


def publish_request(
    source: Path,
    *,
    recipient_file: Path,
    prefix: str,
    client: Any,
    journal_path: Path,
    ttl_seconds: int,
) -> RequestPublication:
    """Encrypt, create, and read back one typed control request version."""

    source = _absolute_path(source, label="control request source")
    try:
        raw = read_secure_bytes(
            source,
            label="control request source",
            owner_uid=0,
            max_size=MAX_CONTROL_REQUEST_BYTES,
        )
        metadata = source.stat(follow_symlinks=False)
    except (SecureFileError, OSError) as exc:
        raise ControlTransportError("control request source is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or metadata.st_nlink != 1
        or metadata.st_mode & 0o022
    ):
        raise ControlTransportError("control request source is not root-owned and private")
    if not raw.endswith(b"\n"):
        raise ControlTransportError("control request source must end in one newline")
    try:
        request = json.loads(raw[:-1].decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ControlTransportError("control request source is not strict JSON") from exc
    if not isinstance(request, dict) or request_envelope_payload(request) != raw:
        raise ControlTransportError("control request source is not canonical")
    try:
        published = publish_age_encrypted(
            source,
            recipient_file=recipient_file,
            bucket=PRODUCTION_BUCKET,
            prefix=prefix,
            operation_id=request["operation_id"],
            artifact_kind=REQUEST_ARTIFACT_KIND,
            client=client,
            journal_path=journal_path,
            metadata={
                "request-schema": REQUEST_ENVELOPE_SCHEMA,
                "request-type": request["request_type"],
                "role": request["role"],
                "request-sha256": request["request_sha256"],
            },
            max_bytes=MAX_CONTROL_REQUEST_BYTES,
        )
        presigned = presign_exact_get(client, published, ttl_seconds=ttl_seconds)
    except ProductionTransportError as exc:
        raise ControlTransportError("control request publication failed closed") from exc
    if (
        published.metadata.get("request-schema") != REQUEST_ENVELOPE_SCHEMA
        or published.metadata.get("request-type") != request["request_type"]
        or published.metadata.get("role") != request["role"]
        or published.metadata.get("request-sha256") != request["request_sha256"]
    ):
        raise ControlTransportError("control request publication metadata differs")
    return RequestPublication(
        published=published,
        presigned=presigned,
        request_sha256=request["request_sha256"],
        request_bytes=len(raw),
        request_type=request["request_type"],
        role=request["role"],
    )


def build_request_descriptor(publication: RequestPublication) -> dict[str, Any]:
    """Build an in-memory receiver descriptor.  It must never be persisted."""

    if not isinstance(publication, RequestPublication):
        raise ControlTransportError("control request publication is invalid")
    published = publication.published
    if publication.presigned.object_key != published.object_key or publication.presigned.version_id != published.version_id:
        raise ControlTransportError("control request URL is not bound to its object")
    return {
        "schema": "wa-ir-production-artifact-receive-v1",
        "operation_id": published.metadata["operation-id"],
        "artifact_kind": REQUEST_ARTIFACT_KIND,
        "destination_name": request_destination_name(publication.request_sha256),
        "bucket": published.bucket,
        "object_key": published.object_key,
        "version_id": published.version_id,
        "url": publication.presigned.reveal_for_control_channel(),
        "ciphertext_sha256": published.ciphertext_sha256,
        "ciphertext_bytes": published.ciphertext_bytes,
        "plaintext_sha256": published.plaintext_sha256,
        "plaintext_bytes": published.plaintext_bytes,
    }


def _absolute_path(value: Path | str, *, label: str) -> Path:
    path = Path(value)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path != Path(path.resolve(strict=False))
    ):
        raise ControlTransportError(f"{label} must be an absolute normalized path")
    return path


def build_receiver_control_command(
    *,
    receiver_path: Path,
    policy_path: Path,
    publication: RequestPublication,
    result_grant: ResultUploadGrant,
    result_url: str,
    confirmation: str,
) -> ReceiverControlCommand:
    """Build the fixed target command without placing request bytes on SSH.

    A pinned SSH wrapper must receive ``ReceiverControlCommand.remote_command``
    as one remote-command argument.  The typed request and result URLs are
    base64url command arguments rather than raw query strings; the receiver
    decodes and immediately validates each against its exact object binding.
    No arbitrary executable, shell fragment, source path, plaintext request or
    plaintext result is represented here.
    """

    receiver_path = _absolute_path(receiver_path, label="receiver path")
    policy_path = _absolute_path(policy_path, label="receiver policy path")
    if not isinstance(publication, RequestPublication):
        raise ControlTransportError("control request publication is invalid")
    if not isinstance(result_grant, ResultUploadGrant):
        raise ControlTransportError("control result grant is invalid")
    if not isinstance(confirmation, str) or not confirmation or any(
        character in confirmation for character in "\r\n\x00"
    ):
        raise ControlTransportError("receiver confirmation is invalid")
    descriptor = build_request_descriptor(publication)
    if (
        publication.role != result_grant.role
        or publication.request_sha256 != result_grant.request_sha256
        or descriptor["operation_id"] != result_grant.operation_id
    ):
        raise ControlTransportError("control request and result grant differ")
    try:
        parse_descriptor(_canonical_json(descriptor))
    except ProductionReceiveError as exc:
        raise ControlTransportError("control request URL is not exact-version scoped") from exc
    validate_result_upload_url(result_url, grant=result_grant)
    request_url_b64 = encode_control_url_argument(
        str(descriptor["url"]), label="control request"
    )
    result_url_b64 = encode_control_url_argument(result_url, label="control result")
    values = (
        os.fspath(receiver_path),
        os.fspath(policy_path),
        confirmation,
        publication.request_sha256,
        request_url_b64,
        str(descriptor["operation_id"]),
        str(descriptor["object_key"]),
        str(descriptor["version_id"]),
        str(descriptor["destination_name"]),
        str(descriptor["ciphertext_sha256"]),
        str(descriptor["ciphertext_bytes"]),
        str(descriptor["plaintext_sha256"]),
        str(descriptor["plaintext_bytes"]),
        result_url_b64,
        result_grant.object_key,
        result_grant.upload_id,
        str(result_grant.ttl_seconds),
    )
    if any(not value or any(character in value for character in "\r\n\x00") for value in values):
        raise ControlTransportError("receiver control argument is unsafe")
    argv = (
        "/usr/bin/env",
        "-i",
        "PATH=/usr/bin:/bin",
        "HOME=/nonexistent",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
        "PYTHONDONTWRITEBYTECODE=1",
        "/usr/bin/python3",
        "-I",
        "-B",
        os.fspath(receiver_path),
        "--policy",
        os.fspath(policy_path),
        "--apply",
        "--confirm",
        confirmation,
        "--request-sha256",
        publication.request_sha256,
        "--request-url-b64",
        request_url_b64,
        "--operation-id",
        str(descriptor["operation_id"]),
        "--request-object-key",
        str(descriptor["object_key"]),
        "--request-version-id",
        str(descriptor["version_id"]),
        "--request-destination-name",
        str(descriptor["destination_name"]),
        "--request-ciphertext-sha256",
        str(descriptor["ciphertext_sha256"]),
        "--request-ciphertext-bytes",
        str(descriptor["ciphertext_bytes"]),
        "--request-plaintext-sha256",
        str(descriptor["plaintext_sha256"]),
        "--request-plaintext-bytes",
        str(descriptor["plaintext_bytes"]),
        "--result-url-b64",
        result_url_b64,
        "--result-grant-schema",
        RESULT_UPLOAD_SCHEMA,
        "--result-object-key",
        result_grant.object_key,
        "--result-upload-id",
        result_grant.upload_id,
        "--result-ttl-seconds",
        str(result_grant.ttl_seconds),
    )
    if any(item in {"/bin/sh", "sh", "-c", "bash"} for item in argv):
        raise ControlTransportError("receiver control command must not contain a shell")
    command = ReceiverControlCommand(argv=argv)
    command.remote_command()
    return command


def validate_result_object(
    value: Mapping[str, Any],
    *,
    grant: ResultUploadGrant,
) -> ResultObject:
    if not isinstance(value, Mapping):
        raise ControlTransportError("control result object is invalid")
    bucket = value.get("bucket")
    object_key = value.get("object_key")
    version_id = value.get("version_id")
    ciphertext_sha256 = value.get("ciphertext_sha256")
    ciphertext_bytes = value.get("ciphertext_bytes")
    metadata = value.get("metadata")
    if (
        bucket != grant.bucket
        or object_key != grant.object_key
        or not isinstance(version_id, str)
        or not 1 <= len(version_id) <= 1024
        or not version_id.isprintable()
        or any(character.isspace() for character in version_id)
        or version_id.lower() == "null"
        or _nonzero_sha256(ciphertext_sha256, label="result ciphertext") != ciphertext_sha256
        or isinstance(ciphertext_bytes, bool)
        or not isinstance(ciphertext_bytes, int)
        or not 1 <= ciphertext_bytes <= MAX_RESULT_CIPHERTEXT_BYTES
        or not isinstance(metadata, Mapping)
        or {str(key): str(item) for key, item in metadata.items()} != grant.metadata()
    ):
        raise ControlTransportError("control result object binding differs")
    return ResultObject(
        bucket=bucket,
        object_key=object_key,
        version_id=version_id,
        ciphertext_sha256=ciphertext_sha256,
        ciphertext_bytes=ciphertext_bytes,
        metadata={str(key): str(item) for key, item in metadata.items()},
    )


def _grant_from_recovery_intent(intent: ResultUploadRecoveryIntent) -> ResultUploadGrant:
    return validate_result_upload_grant(
        {
            "schema": RESULT_UPLOAD_SCHEMA,
            "bucket": intent.bucket,
            "object_key": intent.object_key,
            "upload_id": intent.upload_id,
            "operation_id": intent.operation_id,
            "role": intent.role,
            "request_sha256": intent.request_sha256,
            "ttl_seconds": intent.ttl_seconds,
        },
        prefix=intent.prefix,
    )


def build_result_upload_recovery_intent(
    *,
    prefix: str,
    grant: ResultUploadGrant,
    ciphertext_sha256: str,
    ciphertext_bytes: int,
) -> ResultUploadRecoveryIntent:
    """Bind a target's encrypted result before an ephemeral PUT is attempted.

    The returned document is suitable for a root-only local journal on either
    side of the bounded control channel.  It has no pre-signed URL or
    ciphertext payload, so it remains valid after the short-lived PUT URL has
    expired and can be used only for read-only exact-key recovery.
    """

    if not isinstance(prefix, str) or not isinstance(grant, ResultUploadGrant):
        raise ControlTransportError("control result recovery grant is invalid")
    try:
        grant = _grant_from_recovery_intent(
            ResultUploadRecoveryIntent(
                prefix=prefix,
                bucket=grant.bucket,
                object_key=grant.object_key,
                upload_id=grant.upload_id,
                operation_id=grant.operation_id,
                role=grant.role,
                request_sha256=grant.request_sha256,
                ttl_seconds=grant.ttl_seconds,
                ciphertext_sha256=ciphertext_sha256,
                ciphertext_bytes=ciphertext_bytes,
                metadata=grant.metadata(),
            )
        )
    except (ControlTransportError, TypeError) as exc:
        raise ControlTransportError("control result recovery grant is invalid") from exc
    intent = ResultUploadRecoveryIntent(
        prefix=prefix,
        bucket=grant.bucket,
        object_key=grant.object_key,
        upload_id=grant.upload_id,
        operation_id=grant.operation_id,
        role=grant.role,
        request_sha256=grant.request_sha256,
        ttl_seconds=grant.ttl_seconds,
        ciphertext_sha256=_nonzero_sha256(
            ciphertext_sha256, label="result ciphertext"
        ),
        ciphertext_bytes=ciphertext_bytes,
        metadata=grant.metadata(),
    )
    if (
        isinstance(ciphertext_bytes, bool)
        or not isinstance(ciphertext_bytes, int)
        or not 1 <= ciphertext_bytes <= MAX_RESULT_CIPHERTEXT_BYTES
    ):
        raise ControlTransportError("control result recovery ciphertext size is invalid")
    return validate_result_upload_recovery_intent(intent.document())


def validate_result_upload_recovery_intent(
    value: Mapping[str, Any],
) -> ResultUploadRecoveryIntent:
    """Validate the immutable, non-secret lost-PUT recovery binding."""

    if not isinstance(value, Mapping) or set(value) != RESULT_RECOVERY_INTENT_FIELDS:
        raise ControlTransportError("control result recovery intent fields are not exact")
    if value.get("schema") != RESULT_RECOVERY_INTENT_SCHEMA:
        raise ControlTransportError("control result recovery intent schema differs")
    prefix = value.get("prefix")
    if not isinstance(prefix, str):
        raise ControlTransportError("control result recovery prefix is invalid")
    provisional = ResultUploadRecoveryIntent(
        prefix=prefix,
        bucket=value.get("bucket"),
        object_key=value.get("object_key"),
        upload_id=value.get("upload_id"),
        operation_id=value.get("operation_id"),
        role=value.get("role"),
        request_sha256=value.get("request_sha256"),
        ttl_seconds=value.get("ttl_seconds"),
        ciphertext_sha256=value.get("ciphertext_sha256"),
        ciphertext_bytes=value.get("ciphertext_bytes"),
        metadata=value.get("metadata"),
    )
    try:
        grant = _grant_from_recovery_intent(provisional)
    except (ControlTransportError, TypeError) as exc:
        raise ControlTransportError("control result recovery grant binding differs") from exc
    ciphertext_sha256 = _nonzero_sha256(
        provisional.ciphertext_sha256, label="result ciphertext"
    )
    ciphertext_bytes = provisional.ciphertext_bytes
    if (
        isinstance(ciphertext_bytes, bool)
        or not isinstance(ciphertext_bytes, int)
        or not 1 <= ciphertext_bytes <= MAX_RESULT_CIPHERTEXT_BYTES
        or not isinstance(provisional.metadata, Mapping)
        or {str(key): str(item) for key, item in provisional.metadata.items()}
        != grant.metadata()
    ):
        raise ControlTransportError("control result recovery ciphertext binding differs")
    return ResultUploadRecoveryIntent(
        prefix=provisional.prefix,
        bucket=grant.bucket,
        object_key=grant.object_key,
        upload_id=grant.upload_id,
        operation_id=grant.operation_id,
        role=grant.role,
        request_sha256=grant.request_sha256,
        ttl_seconds=grant.ttl_seconds,
        ciphertext_sha256=ciphertext_sha256,
        ciphertext_bytes=ciphertext_bytes,
        metadata=grant.metadata(),
    )


def _result_object_document(result: ResultObject) -> dict[str, Any]:
    if not isinstance(result, ResultObject) or not isinstance(result.metadata, Mapping):
        raise ControlTransportError("control result object is invalid")
    try:
        metadata = dict(result.metadata)
    except (TypeError, ValueError) as exc:
        raise ControlTransportError("control result object is invalid") from exc
    return {
        "bucket": result.bucket,
        "object_key": result.object_key,
        "version_id": result.version_id,
        "ciphertext_sha256": result.ciphertext_sha256,
        "ciphertext_bytes": result.ciphertext_bytes,
        "metadata": metadata,
    }


def recover_result_upload_from_intent(
    client: Any,
    intent: ResultUploadRecoveryIntent,
) -> ResultObject | None:
    """Recover one accepted result PUT without issuing a replacement write."""

    if not isinstance(intent, ResultUploadRecoveryIntent):
        raise ControlTransportError("control result recovery intent is invalid")
    intent = validate_result_upload_recovery_intent(intent.document())
    try:
        version_id = recover_create_only_exact_version(
            client,
            bucket=intent.bucket,
            object_key=intent.object_key,
            expected_sha256=intent.ciphertext_sha256,
            expected_bytes=intent.ciphertext_bytes,
            expected_metadata=intent.metadata,
        )
    except ProductionTransportError as exc:
        raise ControlTransportError("control result lost-PUT recovery failed closed") from exc
    if version_id is None:
        return None
    result = ResultObject(
        bucket=intent.bucket,
        object_key=intent.object_key,
        version_id=version_id,
        ciphertext_sha256=intent.ciphertext_sha256,
        ciphertext_bytes=intent.ciphertext_bytes,
        metadata=intent.metadata,
    )
    return validate_result_object(_result_object_document(result), grant=_grant_from_recovery_intent(intent))


def _recovery_journal_payload(document: Mapping[str, Any], *, label: str) -> bytes:
    payload = _canonical_json(dict(document)) + b"\n"
    if (
        not 1 <= len(payload) <= MAX_RESULT_RECOVERY_JOURNAL_BYTES
        or b"://" in payload
        or b"X-Amz-" in payload
    ):
        raise ControlTransportError(f"{label} contains forbidden transport material")
    return payload


def _read_root_only_recovery_journal(path: Path, *, label: str) -> bytes:
    path = _absolute_path(path, label=label)
    try:
        payload = read_secure_bytes(
            path,
            label=label,
            owner_uid=0,
            max_size=MAX_RESULT_RECOVERY_JOURNAL_BYTES,
        )
        metadata = path.stat(follow_symlinks=False)
    except (SecureFileError, OSError) as exc:
        raise ControlTransportError(f"{label} is unavailable or unsafe") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or not payload.endswith(b"\n")
        or b"://" in payload
        or b"X-Amz-" in payload
    ):
        raise ControlTransportError(f"{label} is unavailable or unsafe")
    return payload


def _persist_root_only_recovery_journal(
    path: Path,
    payload: bytes,
    *,
    label: str,
) -> bytes:
    path = _absolute_path(path, label=label)
    if os.geteuid() != 0 or os.getegid() != 0:
        raise ControlTransportError(f"{label} must be persisted by root:root")
    try:
        write_secure_new_bytes(
            path,
            payload,
            label=label,
            mode=0o600,
            max_size=MAX_RESULT_RECOVERY_JOURNAL_BYTES,
        )
    except SecureFileError:
        pass
    observed = _read_root_only_recovery_journal(path, label=label)
    if observed != payload:
        raise ControlTransportError(f"{label} already exists with different binding")
    return observed


def persist_result_upload_recovery_intent(
    path: Path,
    intent: ResultUploadRecoveryIntent,
) -> ResultUploadRecoveryIntent:
    """Create-or-match the root-only local lost-PUT recovery journal."""

    if not isinstance(intent, ResultUploadRecoveryIntent):
        raise ControlTransportError("control result recovery intent is invalid")
    intent = validate_result_upload_recovery_intent(intent.document())
    payload = _recovery_journal_payload(intent.document(), label="control result recovery intent")
    observed = _persist_root_only_recovery_journal(
        path,
        payload,
        label="control result recovery intent",
    )
    return load_result_upload_recovery_intent_payload(observed)


def load_result_upload_recovery_intent(path: Path) -> ResultUploadRecoveryIntent:
    return load_result_upload_recovery_intent_payload(
        _read_root_only_recovery_journal(path, label="control result recovery intent")
    )


def load_result_upload_recovery_intent_payload(payload: bytes) -> ResultUploadRecoveryIntent:
    try:
        document = json.loads(
            payload[:-1].decode("ascii"), object_pairs_hook=_strict_object
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ControlTransportError("control result recovery intent is invalid") from exc
    if not isinstance(document, dict) or _canonical_json(document) + b"\n" != payload:
        raise ControlTransportError("control result recovery intent is not canonical")
    return validate_result_upload_recovery_intent(document)


def _recovery_receipt_document(
    intent: ResultUploadRecoveryIntent,
    result: ResultObject,
) -> dict[str, Any]:
    intent = validate_result_upload_recovery_intent(intent.document())
    result = validate_result_object(
        _result_object_document(result), grant=_grant_from_recovery_intent(intent)
    )
    if (
        result.ciphertext_sha256 != intent.ciphertext_sha256
        or result.ciphertext_bytes != intent.ciphertext_bytes
    ):
        raise ControlTransportError("control result recovery receipt ciphertext differs")
    return {
        "schema": RESULT_RECOVERY_RECEIPT_SCHEMA,
        "recovery_intent_sha256": intent.sha256(),
        "bucket": intent.bucket,
        "object_key": intent.object_key,
        "upload_id": intent.upload_id,
        "operation_id": intent.operation_id,
        "role": intent.role,
        "request_sha256": intent.request_sha256,
        "ciphertext_sha256": intent.ciphertext_sha256,
        "ciphertext_bytes": intent.ciphertext_bytes,
        "metadata": dict(intent.metadata),
        "version_id": result.version_id,
    }


def validate_result_upload_recovery_receipt(
    value: Mapping[str, Any],
    *,
    intent: ResultUploadRecoveryIntent,
) -> ResultObject:
    if not isinstance(value, Mapping) or set(value) != RESULT_RECOVERY_RECEIPT_FIELDS:
        raise ControlTransportError("control result recovery receipt fields are not exact")
    if value.get("schema") != RESULT_RECOVERY_RECEIPT_SCHEMA:
        raise ControlTransportError("control result recovery receipt schema differs")
    intent = validate_result_upload_recovery_intent(intent.document())
    expected = _recovery_receipt_document(
        intent,
        ResultObject(
            bucket=value.get("bucket"),
            object_key=value.get("object_key"),
            version_id=value.get("version_id"),
            ciphertext_sha256=value.get("ciphertext_sha256"),
            ciphertext_bytes=value.get("ciphertext_bytes"),
            metadata=value.get("metadata"),
        ),
    )
    if value != expected:
        raise ControlTransportError("control result recovery receipt binding differs")
    return validate_result_object(
        {
            "bucket": value["bucket"],
            "object_key": value["object_key"],
            "version_id": value["version_id"],
            "ciphertext_sha256": value["ciphertext_sha256"],
            "ciphertext_bytes": value["ciphertext_bytes"],
            "metadata": value["metadata"],
        },
        grant=_grant_from_recovery_intent(intent),
    )


def persist_result_upload_recovery_receipt(
    path: Path,
    *,
    intent: ResultUploadRecoveryIntent,
    result: ResultObject,
) -> ResultObject:
    """Create-or-match a recovered exact-VersionId receipt without URLs."""

    document = _recovery_receipt_document(intent, result)
    payload = _recovery_journal_payload(document, label="control result recovery receipt")
    observed = _persist_root_only_recovery_journal(
        path,
        payload,
        label="control result recovery receipt",
    )
    try:
        parsed = json.loads(observed[:-1].decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ControlTransportError("control result recovery receipt is invalid") from exc
    if not isinstance(parsed, dict) or _canonical_json(parsed) + b"\n" != observed:
        raise ControlTransportError("control result recovery receipt is not canonical")
    return validate_result_upload_recovery_receipt(parsed, intent=intent)


def recover_result_upload_from_journal(
    client: Any,
    *,
    intent_path: Path,
    receipt_path: Path,
) -> ResultObject | None:
    """Resume only a durable exact-key recovery, never a replacement PUT."""

    intent_path = _absolute_path(intent_path, label="control result recovery intent")
    receipt_path = _absolute_path(receipt_path, label="control result recovery receipt")
    if intent_path == receipt_path:
        raise ControlTransportError("control result recovery journal paths must differ")
    intent = load_result_upload_recovery_intent(intent_path)
    if receipt_path.exists() or receipt_path.is_symlink():
        payload = _read_root_only_recovery_journal(
            receipt_path, label="control result recovery receipt"
        )
        try:
            document = json.loads(
                payload[:-1].decode("ascii"), object_pairs_hook=_strict_object
            )
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise ControlTransportError("control result recovery receipt is invalid") from exc
        if not isinstance(document, dict) or _canonical_json(document) + b"\n" != payload:
            raise ControlTransportError("control result recovery receipt is not canonical")
        return validate_result_upload_recovery_receipt(document, intent=intent)
    result = recover_result_upload_from_intent(client, intent)
    if result is None:
        return None
    return persist_result_upload_recovery_receipt(
        receipt_path,
        intent=intent,
        result=result,
    )


def readback_result_exact(client: Any, result: ResultObject) -> bytes:
    """Read one returned exact VersionId and prove its stored ciphertext identity."""

    if not isinstance(result, ResultObject):
        raise ControlTransportError("control result readback input is invalid")
    response: Any | None = None
    stream: Any | None = None
    try:
        head = client.head_object(
            Bucket=result.bucket,
            Key=result.object_key,
            VersionId=result.version_id,
        )
        response = client.get_object(
            Bucket=result.bucket,
            Key=result.object_key,
            VersionId=result.version_id,
        )
        stream = response["Body"]
        body = stream.read(MAX_RESULT_CIPHERTEXT_BYTES + 1)
    except Exception as exc:
        raise ControlTransportError("control result exact-version readback failed") from exc
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    observed_metadata = head.get("Metadata") if isinstance(head, Mapping) else None
    observed_version = head.get("VersionId") if isinstance(head, Mapping) else None
    observed_head_bytes = head.get("ContentLength") if isinstance(head, Mapping) else None
    observed_get_version = response.get("VersionId") if isinstance(response, Mapping) else None
    observed_get_metadata = response.get("Metadata") if isinstance(response, Mapping) else None
    observed_get_bytes = response.get("ContentLength") if isinstance(response, Mapping) else None
    if (
        observed_version != result.version_id
        or observed_get_version != result.version_id
        or not isinstance(observed_metadata, Mapping)
        or {str(key): str(item) for key, item in observed_metadata.items()}
        != dict(result.metadata)
        or not isinstance(observed_get_metadata, Mapping)
        or {str(key): str(item) for key, item in observed_get_metadata.items()}
        != dict(result.metadata)
        or observed_head_bytes != result.ciphertext_bytes
        or observed_get_bytes != result.ciphertext_bytes
        or not isinstance(body, bytes)
        or len(body) != result.ciphertext_bytes
        or hashlib.sha256(body).hexdigest() != result.ciphertext_sha256
    ):
        raise ControlTransportError("control result exact-version readback differs")
    return body
