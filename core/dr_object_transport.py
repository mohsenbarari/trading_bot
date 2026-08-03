"""Encrypted, version-bound Object Storage transport for the FI↔IR DR hop.

The provider stores only ciphertext.  A sender publishes an immutable signed
event record, Iran pulls and validates that record, then publishes a signed
receipt bound to the exact source Object Storage VersionId.  No FIN→IR peer
HTTP request is involved in this protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import secrets
from typing import Any

from botocore.exceptions import ClientError

from core.config import settings
from core.dr_blob_crypto import (
    FORMAT_OVERHEAD,
    CiphertextIdentity,
    DrBlobKeyring,
    ciphertext_identity_from_provider,
    decrypt_bytes,
    encrypt_bytes,
    metadata_for_ciphertext,
)
from core.dr_event_protocol import canonical_json_bytes
from core.dr_object_storage import S3Config, object_not_found, object_storage_client
from core.dr_sync_auth import PairwiseDrKey, acknowledgement_signature_is_valid, sign_acknowledgement


OBJECT_TRANSPORT_SCHEMA = "trading-bot-dr-object-transport-v1"
EVENT_RECORD_SCHEMA = "trading-bot-dr-object-event-v1"
EVENT_RECEIPT_SCHEMA = "trading-bot-dr-object-event-receipt-v1"
BLOB_RECEIPT_RECORD_SCHEMA = "trading-bot-dr-object-blob-receipt-v1"
BLOB_RECEIPT_ACK_SCHEMA = "trading-bot-dr-object-blob-receipt-ack-v1"
CONTROL_RECORD_MIME = "application/vnd.trading-bot.dr-object-transport+json"
MAX_CONTROL_RECORD_BYTES = 8 * 1024 * 1024
HASH_ALPHABET = frozenset("0123456789abcdef")
OBJECT_STORAGE_EVENT_HOPS = frozenset({
    ("webapp_fi", "webapp_ir"),
    ("webapp_ir", "webapp_fi"),
})


class DrObjectTransportError(RuntimeError):
    """Raised when Object Storage evidence is malformed, stale, or conflicted."""


class DrObjectTransportMissing(DrObjectTransportError):
    """Raised only when an expected immutable provider object is absent."""


@dataclass(frozen=True)
class StoredControlObject:
    object_key: str
    version_id: str
    identity: CiphertextIdentity
    plaintext: bytes


@dataclass(frozen=True)
class ObjectStorageEventRecord:
    source_site: str
    destination_site: str
    key_id: str
    request_hash: str
    record_hash: str
    body: bytes
    events: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ObjectStorageEventReceipt:
    event_record_hash: str
    event_request_hash: str
    event_object_key: str
    event_object_version_id: str
    event_ciphertext_hash: str
    event_ciphertext_size: int
    receipt_object_key: str
    receipt_object_version_id: str
    receipt_ciphertext_hash: str
    receipt_ciphertext_size: int
    acknowledgement: dict[str, Any]
    receipt_hash: str


@dataclass(frozen=True)
class ObjectStorageBlobReceiptRecord:
    source_site: str
    destination_site: str
    key_id: str
    request_hash: str
    record_hash: str
    body: dict[str, Any]


@dataclass(frozen=True)
class ObjectStorageBlobReceiptAck:
    record_hash: str
    request_hash: str
    object_key: str
    object_version_id: str
    ciphertext_hash: str
    ciphertext_size: int
    acknowledgement: dict[str, Any]
    receipt_hash: str


def uses_object_storage_event_transport(*, source_site: str, destination_site: str) -> bool:
    return (source_site, destination_site) in OBJECT_STORAGE_EVENT_HOPS


def uses_object_storage_blob_receipt_transport(*, source_site: str, destination_site: str) -> bool:
    return {source_site, destination_site} == {"webapp_fi", "webapp_ir"}


def _strict_json(raw: bytes) -> Any:
    def reject_duplicates(pairs):  # noqa: ANN001
        result = {}
        for key, value in pairs:
            if key in result:
                raise DrObjectTransportError(f"duplicate Object Storage record field: {key}")
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, DrObjectTransportError) as exc:
        raise DrObjectTransportError("Object Storage record is not strict JSON") from exc


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HASH_ALPHABET


def _prefix() -> str:
    value = str(settings.dr_object_transport_prefix or "").strip("/")
    if not value or ".." in value.split("/"):
        raise DrObjectTransportError("Object Storage transport prefix is unsafe")
    return value


def _opaque_object_key(*, kind: str, key: PairwiseDrKey, binding: str) -> str:
    if not _is_hash(binding):
        raise DrObjectTransportError("Object Storage transport binding hash is invalid")
    opaque = hmac.new(
        key.secret.encode("utf-8"),
        f"trading-bot-dr-object-transport-v1\x00{kind}\x00{binding}".encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{_prefix()}/{kind}/{key.source_site}/{key.destination_site}/{opaque[:2]}/{opaque}"


def _control_metadata(identity: CiphertextIdentity, *, plaintext_hash: str) -> dict[str, str]:
    if not _is_hash(plaintext_hash):
        raise DrObjectTransportError("Object Storage record plaintext hash is invalid")
    return {**metadata_for_ciphertext(identity), "plaintext-sha256": plaintext_hash}


def _control_identity(*, object_key: str, content_length: Any, metadata: Any) -> tuple[CiphertextIdentity, str]:
    if not isinstance(metadata, dict) or set(metadata) != {
        "ciphertext-sha256", "encryption-key-id", "encryption-format", "plaintext-sha256",
    }:
        raise DrObjectTransportError("Object Storage control record metadata fields are invalid")
    identity = ciphertext_identity_from_provider(
        object_key=object_key,
        content_length=content_length,
        metadata={name: metadata[name] for name in metadata if name != "plaintext-sha256"},
    )
    plaintext_hash = metadata["plaintext-sha256"]
    if not _is_hash(plaintext_hash) or identity.ciphertext_size > MAX_CONTROL_RECORD_BYTES + FORMAT_OVERHEAD:
        raise DrObjectTransportError("Object Storage control record identity is invalid")
    return identity, plaintext_hash


def _read_control_object_with_keyring(
    client, config: S3Config, *, object_key: str, keyring: DrBlobKeyring
) -> StoredControlObject:  # noqa: ANN001
    # Keep the public helper small while making the decryption key explicit at
    # every call site; no process-global secret is retained in this module.
    head = client.head_object(Bucket=config.bucket, Key=object_key)
    identity, plaintext_hash = _control_identity(
        object_key=object_key,
        content_length=head.get("ContentLength"),
        metadata=head.get("Metadata"),
    )
    version_id = str(head.get("VersionId") or "")
    if settings.dr_blob_require_versioning and not version_id:
        raise DrObjectTransportError("Object Storage control record lacks an exact VersionId")
    result = client.get_object(
        Bucket=config.bucket,
        Key=object_key,
        **({"VersionId": version_id} if version_id else {}),
    )
    observed_version = str(result.get("VersionId") or version_id)
    if version_id and observed_version != version_id:
        raise DrObjectTransportError("Object Storage control record VersionId changed during read-back")
    plaintext = decrypt_bytes(
        result["Body"],
        ciphertext_size=identity.ciphertext_size,
        expected_ciphertext_hash=identity.ciphertext_hash,
        content_hash=plaintext_hash,
        object_key=object_key,
        key_id=identity.key_id,
        keyring=keyring,
        mime_type=CONTROL_RECORD_MIME,
    )
    if _hash(plaintext) != plaintext_hash:
        raise DrObjectTransportError("Object Storage control record plaintext hash changed after read-back")
    return StoredControlObject(object_key, observed_version, identity, plaintext)


def store_control_record(
    config: S3Config,
    *,
    object_key: str,
    plaintext: bytes,
    keyring: DrBlobKeyring,
    immutable: bool = True,
) -> StoredControlObject:
    """Persist then decrypt the exact current VersionId.

    Event records are immutable.  Receipts are the deliberately narrow
    exception: they may advance from ``received`` to ``applied`` under the
    same opaque key, with Object Storage versioning retaining both proofs.
    """

    if len(plaintext) > MAX_CONTROL_RECORD_BYTES:
        raise DrObjectTransportError("Object Storage control record exceeds the size limit")
    client = object_storage_client(config)
    try:
        existing = _read_control_object_with_keyring(
            client, config, object_key=object_key, keyring=keyring
        )
    except ClientError as exc:
        if not object_not_found(exc):
            raise DrObjectTransportError("Object Storage control record lookup failed") from exc
    else:
        if not secrets.compare_digest(existing.plaintext, plaintext):
            if immutable:
                raise DrObjectTransportError("Object Storage control record key conflicts with immutable content")
        else:
            return existing

    plaintext_hash = _hash(plaintext)
    encrypted, identity = encrypt_bytes(
        plaintext,
        content_hash=plaintext_hash,
        object_key=object_key,
        key_id=keyring.active_key_id,
        keyring=keyring,
        mime_type=CONTROL_RECORD_MIME,
    )
    try:
        client.put_object(
            Bucket=config.bucket,
            Key=object_key,
            Body=encrypted,
            ContentLength=identity.ciphertext_size,
            ContentType="application/octet-stream",
            Metadata=_control_metadata(identity, plaintext_hash=plaintext_hash),
        )
    finally:
        encrypted.close()
    stored = _read_control_object_with_keyring(client, config, object_key=object_key, keyring=keyring)
    if not secrets.compare_digest(stored.plaintext, plaintext):
        raise DrObjectTransportError("Object Storage control record failed exact read-back")
    return stored


def load_control_record(
    config: S3Config, *, object_key: str, keyring: DrBlobKeyring
) -> StoredControlObject:
    try:
        return _read_control_object_with_keyring(
            object_storage_client(config), config, object_key=object_key, keyring=keyring
        )
    except ClientError as exc:
        if object_not_found(exc):
            raise DrObjectTransportMissing("Object Storage control record is not available") from exc
        raise DrObjectTransportError("Object Storage control record read failed") from exc


def list_control_record_keys(
    config: S3Config, *, source_site: str, destination_site: str, key: PairwiseDrKey
) -> tuple[str, ...]:
    prefix = f"{_prefix()}/event/{source_site}/{destination_site}/"
    if key.source_site != source_site or key.destination_site != destination_site:
        raise DrObjectTransportError("Object Storage event list key identity is invalid")
    client = object_storage_client(config)
    keys: list[str] = []
    continuation: str | None = None
    while True:
        params: dict[str, Any] = {"Bucket": config.bucket, "Prefix": prefix, "MaxKeys": 1000}
        if continuation:
            params["ContinuationToken"] = continuation
        result = client.list_objects_v2(**params)
        for item in result.get("Contents", []):
            if isinstance(item, dict) and isinstance(item.get("Key"), str):
                keys.append(item["Key"])
        if not result.get("IsTruncated"):
            break
        continuation = str(result.get("NextContinuationToken") or "")
        if not continuation:
            raise DrObjectTransportError("Object Storage event listing continuation is missing")
    return tuple(sorted(set(keys)))


def build_event_record(
    *, body: bytes, source_site: str, destination_site: str, key: PairwiseDrKey
) -> tuple[dict[str, Any], bytes]:
    if not uses_object_storage_event_transport(
        source_site=source_site, destination_site=destination_site
    ):
        raise DrObjectTransportError("Object Storage event transport is not authorized for this hop")
    if key.source_site != source_site or key.destination_site != destination_site:
        raise DrObjectTransportError("Object Storage event key direction is invalid")
    payload = _strict_json(body)
    if not isinstance(payload, dict) or set(payload) != {"events"} or not isinstance(payload["events"], list):
        raise DrObjectTransportError("Object Storage event body is invalid")
    if canonical_json_bytes(payload) != body:
        raise DrObjectTransportError("Object Storage event body is not canonical")
    body_hash = _hash(body)
    request_hash = _hash(
        canonical_json_bytes(
            {
                "schema": OBJECT_TRANSPORT_SCHEMA,
                "kind": "event-request",
                "source_site": source_site,
                "destination_site": destination_site,
                "key_id": key.key_id,
                "body_hash": body_hash,
            }
        )
    )
    unsigned = {
        "schema": EVENT_RECORD_SCHEMA,
        "source_site": source_site,
        "destination_site": destination_site,
        "key_id": key.key_id,
        "request_hash": request_hash,
        "body_hash": body_hash,
        "body": payload,
    }
    record_hash = _hash(canonical_json_bytes(unsigned))
    signed = {**unsigned, "record_hash": record_hash}
    record = {**signed, "record_mac": sign_acknowledgement(payload=signed, secret=key.secret)}
    return record, canonical_json_bytes(record)


def parse_event_record(
    plaintext: bytes, *, expected_source_site: str, expected_destination_site: str, key: PairwiseDrKey
) -> ObjectStorageEventRecord:
    record = _strict_json(plaintext)
    required = {
        "schema", "source_site", "destination_site", "key_id", "request_hash", "body_hash", "body",
        "record_hash", "record_mac",
    }
    if not isinstance(record, dict) or set(record) != required:
        raise DrObjectTransportError("Object Storage event record fields are invalid")
    if (
        record["schema"] != EVENT_RECORD_SCHEMA
        or record["source_site"] != expected_source_site
        or record["destination_site"] != expected_destination_site
        or record["key_id"] != key.key_id
        or key.source_site != expected_source_site
        or key.destination_site != expected_destination_site
        or not _is_hash(record["request_hash"])
        or not _is_hash(record["body_hash"])
        or not _is_hash(record["record_hash"])
        or not isinstance(record["body"], dict)
        or set(record["body"]) != {"events"}
        or not isinstance(record["body"]["events"], list)
        or not all(isinstance(item, dict) for item in record["body"]["events"])
    ):
        raise DrObjectTransportError("Object Storage event record identity is invalid")
    body = canonical_json_bytes(record["body"])
    if _hash(body) != record["body_hash"]:
        raise DrObjectTransportError("Object Storage event body hash is invalid")
    unsigned = {name: record[name] for name in required - {"record_hash", "record_mac"}}
    expected_record_hash = _hash(canonical_json_bytes(unsigned))
    if not secrets.compare_digest(record["record_hash"], expected_record_hash):
        raise DrObjectTransportError("Object Storage event record hash is invalid")
    signed = {**unsigned, "record_hash": record["record_hash"]}
    if not acknowledgement_signature_is_valid(
        payload=signed, signature=str(record["record_mac"]), secret=key.secret
    ):
        raise DrObjectTransportError("Object Storage event record signature is invalid")
    expected_request_hash = _hash(
        canonical_json_bytes(
            {
                "schema": OBJECT_TRANSPORT_SCHEMA,
                "kind": "event-request",
                "source_site": expected_source_site,
                "destination_site": expected_destination_site,
                "key_id": key.key_id,
                "body_hash": record["body_hash"],
            }
        )
    )
    if not secrets.compare_digest(record["request_hash"], expected_request_hash):
        raise DrObjectTransportError("Object Storage event request hash is invalid")
    return ObjectStorageEventRecord(
        source_site=expected_source_site,
        destination_site=expected_destination_site,
        key_id=key.key_id,
        request_hash=record["request_hash"],
        record_hash=record["record_hash"],
        body=body,
        events=tuple(record["body"]["events"]),
    )


def publish_event_record(
    config: S3Config,
    *,
    body: bytes,
    source_site: str,
    destination_site: str,
    key: PairwiseDrKey,
    keyring: DrBlobKeyring,
) -> tuple[ObjectStorageEventRecord, StoredControlObject]:
    record, plaintext = build_event_record(
        body=body, source_site=source_site, destination_site=destination_site, key=key
    )
    object_key = _opaque_object_key(kind="event", key=key, binding=str(record["record_hash"]))
    stored = store_control_record(config, object_key=object_key, plaintext=plaintext, keyring=keyring)
    return parse_event_record(
        stored.plaintext,
        expected_source_site=source_site,
        expected_destination_site=destination_site,
        key=key,
    ), stored


def load_event_record(
    config: S3Config,
    *,
    object_key: str,
    source_site: str,
    destination_site: str,
    key: PairwiseDrKey,
    keyring: DrBlobKeyring,
) -> tuple[ObjectStorageEventRecord, StoredControlObject]:
    stored = load_control_record(config, object_key=object_key, keyring=keyring)
    return (
        parse_event_record(
            stored.plaintext,
            expected_source_site=source_site,
            expected_destination_site=destination_site,
            key=key,
        ),
        stored,
    )


def _receipt_object_key(*, key: PairwiseDrKey, event: ObjectStorageEventRecord, stored: StoredControlObject) -> str:
    binding = _hash(
        canonical_json_bytes(
            {
                "event_record_hash": event.record_hash,
                "event_object_key": stored.object_key,
                "event_object_version_id": stored.version_id,
                "event_ciphertext_hash": stored.identity.ciphertext_hash,
            }
        )
    )
    return _opaque_object_key(kind="event-receipt", key=key, binding=binding)


def _build_event_receipt(
    *, event: ObjectStorageEventRecord, stored: StoredControlObject, acknowledgement: dict[str, Any], key: PairwiseDrKey
) -> bytes:
    unsigned = {
        "schema": EVENT_RECEIPT_SCHEMA,
        "source_site": event.source_site,
        "destination_site": event.destination_site,
        "key_id": key.key_id,
        "event_record_hash": event.record_hash,
        "event_request_hash": event.request_hash,
        "event_object_key": stored.object_key,
        "event_object_version_id": stored.version_id,
        "event_ciphertext_hash": stored.identity.ciphertext_hash,
        "event_ciphertext_size": stored.identity.ciphertext_size,
        "acknowledgement": acknowledgement,
    }
    receipt_hash = _hash(canonical_json_bytes(unsigned))
    signed = {**unsigned, "receipt_hash": receipt_hash}
    return canonical_json_bytes(
        {**signed, "receipt_mac": sign_acknowledgement(payload=signed, secret=key.secret)}
    )


def parse_event_receipt(
    plaintext: bytes,
    *,
    event: ObjectStorageEventRecord,
    stored: StoredControlObject,
    receipt_stored: StoredControlObject,
    key: PairwiseDrKey,
) -> ObjectStorageEventReceipt:
    receipt = _strict_json(plaintext)
    required = {
        "schema", "source_site", "destination_site", "key_id", "event_record_hash", "event_request_hash",
        "event_object_key", "event_object_version_id", "event_ciphertext_hash", "event_ciphertext_size",
        "acknowledgement", "receipt_hash", "receipt_mac",
    }
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise DrObjectTransportError("Object Storage event receipt fields are invalid")
    expected = {
        "schema": EVENT_RECEIPT_SCHEMA,
        "source_site": event.source_site,
        "destination_site": event.destination_site,
        "key_id": key.key_id,
        "event_record_hash": event.record_hash,
        "event_request_hash": event.request_hash,
        "event_object_key": stored.object_key,
        "event_object_version_id": stored.version_id,
        "event_ciphertext_hash": stored.identity.ciphertext_hash,
        "event_ciphertext_size": stored.identity.ciphertext_size,
    }
    if any(receipt.get(name) != value for name, value in expected.items()):
        raise DrObjectTransportError("Object Storage event receipt is not bound to the exact event object")
    if not isinstance(receipt["acknowledgement"], dict) or not _is_hash(receipt["receipt_hash"]):
        raise DrObjectTransportError("Object Storage event receipt payload is invalid")
    unsigned = {name: receipt[name] for name in required - {"receipt_hash", "receipt_mac"}}
    expected_hash = _hash(canonical_json_bytes(unsigned))
    if not secrets.compare_digest(receipt["receipt_hash"], expected_hash):
        raise DrObjectTransportError("Object Storage event receipt hash is invalid")
    signed = {**unsigned, "receipt_hash": receipt["receipt_hash"]}
    if not acknowledgement_signature_is_valid(
        payload=signed, signature=str(receipt["receipt_mac"]), secret=key.secret
    ):
        raise DrObjectTransportError("Object Storage event receipt signature is invalid")
    return ObjectStorageEventReceipt(
        event_record_hash=event.record_hash,
        event_request_hash=event.request_hash,
        event_object_key=stored.object_key,
        event_object_version_id=stored.version_id,
        event_ciphertext_hash=stored.identity.ciphertext_hash,
        event_ciphertext_size=stored.identity.ciphertext_size,
        receipt_object_key=receipt_stored.object_key,
        receipt_object_version_id=receipt_stored.version_id,
        receipt_ciphertext_hash=receipt_stored.identity.ciphertext_hash,
        receipt_ciphertext_size=receipt_stored.identity.ciphertext_size,
        acknowledgement=receipt["acknowledgement"],
        receipt_hash=receipt["receipt_hash"],
    )


def publish_event_receipt(
    config: S3Config,
    *,
    event: ObjectStorageEventRecord,
    stored: StoredControlObject,
    acknowledgement: dict[str, Any],
    key: PairwiseDrKey,
    keyring: DrBlobKeyring,
) -> ObjectStorageEventReceipt:
    plaintext = _build_event_receipt(
        event=event, stored=stored, acknowledgement=acknowledgement, key=key
    )
    receipt_key = _receipt_object_key(key=key, event=event, stored=stored)
    receipt = store_control_record(
        config,
        object_key=receipt_key,
        plaintext=plaintext,
        keyring=keyring,
        immutable=False,
    )
    return parse_event_receipt(
        receipt.plaintext,
        event=event,
        stored=stored,
        receipt_stored=receipt,
        key=key,
    )


def load_event_receipt(
    config: S3Config,
    *,
    event: ObjectStorageEventRecord,
    stored: StoredControlObject,
    key: PairwiseDrKey,
    keyring: DrBlobKeyring,
) -> ObjectStorageEventReceipt:
    receipt_key = _receipt_object_key(key=key, event=event, stored=stored)
    receipt = load_control_record(config, object_key=receipt_key, keyring=keyring)
    return parse_event_receipt(
        receipt.plaintext,
        event=event,
        stored=stored,
        receipt_stored=receipt,
        key=key,
    )


def _blob_receipt_request_hash(
    *, body_hash: str, source_site: str, destination_site: str, key_id: str
) -> str:
    return _hash(
        canonical_json_bytes(
            {
                "schema": OBJECT_TRANSPORT_SCHEMA,
                "kind": "blob-receipt-request",
                "source_site": source_site,
                "destination_site": destination_site,
                "key_id": key_id,
                "body_hash": body_hash,
            }
        )
    )


def build_blob_receipt_record(
    *, body: bytes, source_site: str, destination_site: str, key: PairwiseDrKey
) -> tuple[dict[str, Any], bytes]:
    if not uses_object_storage_blob_receipt_transport(
        source_site=source_site, destination_site=destination_site
    ):
        raise DrObjectTransportError("Object Storage blob receipt transport is not authorized for this hop")
    if key.source_site != source_site or key.destination_site != destination_site:
        raise DrObjectTransportError("Object Storage blob receipt key direction is invalid")
    payload = _strict_json(body)
    if not isinstance(payload, dict) or canonical_json_bytes(payload) != body:
        raise DrObjectTransportError("Object Storage blob receipt body is invalid or non-canonical")
    body_hash = _hash(body)
    request_hash = _blob_receipt_request_hash(
        body_hash=body_hash,
        source_site=source_site,
        destination_site=destination_site,
        key_id=key.key_id,
    )
    unsigned = {
        "schema": BLOB_RECEIPT_RECORD_SCHEMA,
        "source_site": source_site,
        "destination_site": destination_site,
        "key_id": key.key_id,
        "request_hash": request_hash,
        "body_hash": body_hash,
        "body": payload,
    }
    record_hash = _hash(canonical_json_bytes(unsigned))
    signed = {**unsigned, "record_hash": record_hash}
    return (
        {**signed, "record_mac": sign_acknowledgement(payload=signed, secret=key.secret)},
        canonical_json_bytes({**signed, "record_mac": sign_acknowledgement(payload=signed, secret=key.secret)}),
    )


def parse_blob_receipt_record(
    plaintext: bytes,
    *,
    expected_source_site: str,
    expected_destination_site: str,
    key: PairwiseDrKey,
) -> ObjectStorageBlobReceiptRecord:
    record = _strict_json(plaintext)
    required = {
        "schema", "source_site", "destination_site", "key_id", "request_hash", "body_hash", "body",
        "record_hash", "record_mac",
    }
    if not isinstance(record, dict) or set(record) != required:
        raise DrObjectTransportError("Object Storage blob receipt record fields are invalid")
    if (
        record["schema"] != BLOB_RECEIPT_RECORD_SCHEMA
        or record["source_site"] != expected_source_site
        or record["destination_site"] != expected_destination_site
        or record["key_id"] != key.key_id
        or key.source_site != expected_source_site
        or key.destination_site != expected_destination_site
        or not _is_hash(record["request_hash"])
        or not _is_hash(record["body_hash"])
        or not _is_hash(record["record_hash"])
        or not isinstance(record["body"], dict)
    ):
        raise DrObjectTransportError("Object Storage blob receipt record identity is invalid")
    body = canonical_json_bytes(record["body"])
    if _hash(body) != record["body_hash"]:
        raise DrObjectTransportError("Object Storage blob receipt body hash is invalid")
    unsigned = {name: record[name] for name in required - {"record_hash", "record_mac"}}
    expected_record_hash = _hash(canonical_json_bytes(unsigned))
    if not secrets.compare_digest(record["record_hash"], expected_record_hash):
        raise DrObjectTransportError("Object Storage blob receipt record hash is invalid")
    signed = {**unsigned, "record_hash": record["record_hash"]}
    if not acknowledgement_signature_is_valid(
        payload=signed, signature=str(record["record_mac"]), secret=key.secret
    ):
        raise DrObjectTransportError("Object Storage blob receipt record signature is invalid")
    expected_request_hash = _blob_receipt_request_hash(
        body_hash=record["body_hash"],
        source_site=expected_source_site,
        destination_site=expected_destination_site,
        key_id=key.key_id,
    )
    if not secrets.compare_digest(record["request_hash"], expected_request_hash):
        raise DrObjectTransportError("Object Storage blob receipt request hash is invalid")
    return ObjectStorageBlobReceiptRecord(
        source_site=expected_source_site,
        destination_site=expected_destination_site,
        key_id=key.key_id,
        request_hash=record["request_hash"],
        record_hash=record["record_hash"],
        body=record["body"],
    )


def publish_blob_receipt_record(
    config: S3Config,
    *,
    body: bytes,
    source_site: str,
    destination_site: str,
    key: PairwiseDrKey,
    keyring: DrBlobKeyring,
) -> tuple[ObjectStorageBlobReceiptRecord, StoredControlObject]:
    record, plaintext = build_blob_receipt_record(
        body=body, source_site=source_site, destination_site=destination_site, key=key
    )
    object_key = _opaque_object_key(
        kind="blob-receipt", key=key, binding=str(record["record_hash"])
    )
    stored = store_control_record(config, object_key=object_key, plaintext=plaintext, keyring=keyring)
    return parse_blob_receipt_record(
        stored.plaintext,
        expected_source_site=source_site,
        expected_destination_site=destination_site,
        key=key,
    ), stored


def load_blob_receipt_record(
    config: S3Config,
    *,
    object_key: str,
    source_site: str,
    destination_site: str,
    key: PairwiseDrKey,
    keyring: DrBlobKeyring,
) -> tuple[ObjectStorageBlobReceiptRecord, StoredControlObject]:
    stored = load_control_record(config, object_key=object_key, keyring=keyring)
    return (
        parse_blob_receipt_record(
            stored.plaintext,
            expected_source_site=source_site,
            expected_destination_site=destination_site,
            key=key,
        ),
        stored,
    )


def list_blob_receipt_record_keys(
    config: S3Config, *, source_site: str, destination_site: str, key: PairwiseDrKey
) -> tuple[str, ...]:
    prefix = f"{_prefix()}/blob-receipt/{source_site}/{destination_site}/"
    if key.source_site != source_site or key.destination_site != destination_site:
        raise DrObjectTransportError("Object Storage blob receipt list key identity is invalid")
    client = object_storage_client(config)
    keys: list[str] = []
    continuation: str | None = None
    while True:
        params: dict[str, Any] = {"Bucket": config.bucket, "Prefix": prefix, "MaxKeys": 1000}
        if continuation:
            params["ContinuationToken"] = continuation
        result = client.list_objects_v2(**params)
        for item in result.get("Contents", []):
            if isinstance(item, dict) and isinstance(item.get("Key"), str):
                keys.append(item["Key"])
        if not result.get("IsTruncated"):
            break
        continuation = str(result.get("NextContinuationToken") or "")
        if not continuation:
            raise DrObjectTransportError("Object Storage blob receipt listing continuation is missing")
    return tuple(sorted(set(keys)))


def _blob_receipt_ack_object_key(
    *, key: PairwiseDrKey, record: ObjectStorageBlobReceiptRecord, stored: StoredControlObject
) -> str:
    binding = _hash(
        canonical_json_bytes(
            {
                "record_hash": record.record_hash,
                "object_key": stored.object_key,
                "object_version_id": stored.version_id,
                "ciphertext_hash": stored.identity.ciphertext_hash,
            }
        )
    )
    return _opaque_object_key(kind="blob-receipt-ack", key=key, binding=binding)


def _build_blob_receipt_ack(
    *,
    record: ObjectStorageBlobReceiptRecord,
    stored: StoredControlObject,
    acknowledgement: dict[str, Any],
    key: PairwiseDrKey,
) -> bytes:
    unsigned = {
        "schema": BLOB_RECEIPT_ACK_SCHEMA,
        "source_site": record.source_site,
        "destination_site": record.destination_site,
        "key_id": key.key_id,
        "record_hash": record.record_hash,
        "request_hash": record.request_hash,
        "object_key": stored.object_key,
        "object_version_id": stored.version_id,
        "ciphertext_hash": stored.identity.ciphertext_hash,
        "ciphertext_size": stored.identity.ciphertext_size,
        "acknowledgement": acknowledgement,
    }
    receipt_hash = _hash(canonical_json_bytes(unsigned))
    signed = {**unsigned, "receipt_hash": receipt_hash}
    return canonical_json_bytes(
        {**signed, "receipt_mac": sign_acknowledgement(payload=signed, secret=key.secret)}
    )


def parse_blob_receipt_ack(
    plaintext: bytes,
    *,
    record: ObjectStorageBlobReceiptRecord,
    stored: StoredControlObject,
    key: PairwiseDrKey,
) -> ObjectStorageBlobReceiptAck:
    receipt = _strict_json(plaintext)
    required = {
        "schema", "source_site", "destination_site", "key_id", "record_hash", "request_hash",
        "object_key", "object_version_id", "ciphertext_hash", "ciphertext_size", "acknowledgement",
        "receipt_hash", "receipt_mac",
    }
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise DrObjectTransportError("Object Storage blob receipt acknowledgement fields are invalid")
    expected = {
        "schema": BLOB_RECEIPT_ACK_SCHEMA,
        "source_site": record.source_site,
        "destination_site": record.destination_site,
        "key_id": key.key_id,
        "record_hash": record.record_hash,
        "request_hash": record.request_hash,
        "object_key": stored.object_key,
        "object_version_id": stored.version_id,
        "ciphertext_hash": stored.identity.ciphertext_hash,
        "ciphertext_size": stored.identity.ciphertext_size,
    }
    if any(receipt.get(name) != value for name, value in expected.items()):
        raise DrObjectTransportError("Object Storage blob acknowledgement is not bound to the exact request")
    if not isinstance(receipt["acknowledgement"], dict) or not _is_hash(receipt["receipt_hash"]):
        raise DrObjectTransportError("Object Storage blob acknowledgement payload is invalid")
    unsigned = {name: receipt[name] for name in required - {"receipt_hash", "receipt_mac"}}
    expected_hash = _hash(canonical_json_bytes(unsigned))
    if not secrets.compare_digest(receipt["receipt_hash"], expected_hash):
        raise DrObjectTransportError("Object Storage blob acknowledgement hash is invalid")
    signed = {**unsigned, "receipt_hash": receipt["receipt_hash"]}
    if not acknowledgement_signature_is_valid(
        payload=signed, signature=str(receipt["receipt_mac"]), secret=key.secret
    ):
        raise DrObjectTransportError("Object Storage blob acknowledgement signature is invalid")
    return ObjectStorageBlobReceiptAck(
        record_hash=record.record_hash,
        request_hash=record.request_hash,
        object_key=stored.object_key,
        object_version_id=stored.version_id,
        ciphertext_hash=stored.identity.ciphertext_hash,
        ciphertext_size=stored.identity.ciphertext_size,
        acknowledgement=receipt["acknowledgement"],
        receipt_hash=receipt["receipt_hash"],
    )


def publish_blob_receipt_ack(
    config: S3Config,
    *,
    record: ObjectStorageBlobReceiptRecord,
    stored: StoredControlObject,
    acknowledgement: dict[str, Any],
    key: PairwiseDrKey,
    keyring: DrBlobKeyring,
) -> ObjectStorageBlobReceiptAck:
    plaintext = _build_blob_receipt_ack(
        record=record, stored=stored, acknowledgement=acknowledgement, key=key
    )
    object_key = _blob_receipt_ack_object_key(key=key, record=record, stored=stored)
    stored_ack = store_control_record(
        config,
        object_key=object_key,
        plaintext=plaintext,
        keyring=keyring,
        immutable=True,
    )
    return parse_blob_receipt_ack(stored_ack.plaintext, record=record, stored=stored, key=key)


def load_blob_receipt_ack(
    config: S3Config,
    *,
    record: ObjectStorageBlobReceiptRecord,
    stored: StoredControlObject,
    key: PairwiseDrKey,
    keyring: DrBlobKeyring,
) -> ObjectStorageBlobReceiptAck:
    object_key = _blob_receipt_ack_object_key(key=key, record=record, stored=stored)
    stored_ack = load_control_record(config, object_key=object_key, keyring=keyring)
    return parse_blob_receipt_ack(stored_ack.plaintext, record=record, stored=stored, key=key)
