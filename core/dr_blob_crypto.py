"""Client-side authenticated encryption for the DR blob transport.

The Object Storage provider receives only ciphertext.  Plaintext identity is
bound as AES-GCM additional authenticated data, but is not exposed in object
keys or provider metadata.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from core.dr_event_protocol import canonical_json_bytes
from core.secure_file_io import read_secure_text


ENCRYPTION_SCHEMA = "trading-bot-dr-blob-keyring-v1"
ENCRYPTION_ALGORITHM = "AES-256-GCM-v1"
ENCRYPTION_MAGIC = b"TBDRB001"
NONCE_BYTES = 12
TAG_BYTES = 16
FORMAT_OVERHEAD = len(ENCRYPTION_MAGIC) + NONCE_BYTES + TAG_BYTES
KEY_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CHUNK_BYTES = 1024 * 1024


class DrBlobCryptoError(RuntimeError):
    pass


@dataclass(frozen=True)
class DrBlobKeyring:
    active_key_id: str
    keys: dict[str, bytes]

    def key(self, key_id: str) -> bytes:
        try:
            return self.keys[key_id]
        except KeyError as exc:
            raise DrBlobCryptoError("blob encryption key id is unavailable") from exc

    def discovery_order(self) -> tuple[str, ...]:
        return (self.active_key_id, *sorted(set(self.keys) - {self.active_key_id}))


@dataclass(frozen=True)
class CiphertextIdentity:
    object_key: str
    key_id: str
    algorithm: str
    ciphertext_hash: str
    ciphertext_size: int


def load_blob_keyring(path: str | Path | None) -> DrBlobKeyring:
    if not path:
        raise DrBlobCryptoError("DR blob encryption keyring file is missing")
    try:
        payload = json.loads(
            read_secure_text(path, label="DR blob encryption keyring", max_size=64 * 1024)
        )
    except Exception as exc:
        raise DrBlobCryptoError("DR blob encryption keyring is invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema", "active_key_id", "keys"}:
        raise DrBlobCryptoError("DR blob encryption keyring fields are invalid")
    if payload["schema"] != ENCRYPTION_SCHEMA or not isinstance(payload["keys"], dict):
        raise DrBlobCryptoError("DR blob encryption keyring schema is invalid")
    active_key_id = str(payload["active_key_id"])
    keys: dict[str, bytes] = {}
    for raw_key_id, encoded in payload["keys"].items():
        key_id = str(raw_key_id)
        if not KEY_ID_RE.fullmatch(key_id) or not isinstance(encoded, str):
            raise DrBlobCryptoError("DR blob encryption key identity is malformed")
        try:
            key = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise DrBlobCryptoError("DR blob encryption key encoding is invalid") from exc
        if len(key) != 32:
            raise DrBlobCryptoError("DR blob encryption keys must be exactly 256 bits")
        if key in keys.values():
            raise DrBlobCryptoError("DR blob encryption key material is duplicated across key ids")
        keys[key_id] = key
    if active_key_id not in keys:
        raise DrBlobCryptoError("active DR blob encryption key is unavailable")
    return DrBlobKeyring(active_key_id=active_key_id, keys=keys)


def encrypted_object_key(
    content_hash: str,
    *,
    prefix: str,
    keyring: DrBlobKeyring,
    key_id: str,
) -> str:
    if not SHA256_RE.fullmatch(content_hash):
        raise DrBlobCryptoError("blob content hash is malformed")
    normalized_prefix = str(prefix or "").strip("/")
    if not normalized_prefix or ".." in normalized_prefix.split("/"):
        raise DrBlobCryptoError("blob object prefix is unsafe")
    key = keyring.key(key_id)
    opaque = hmac.new(
        key,
        b"trading-bot-dr-object-key-v1\x00" + bytes.fromhex(content_hash),
        hashlib.sha256,
    ).hexdigest()
    return f"{normalized_prefix}/{key_id}/{opaque[:2]}/{opaque[2:4]}/{opaque}"


def authenticated_context(
    *,
    object_key: str,
    content_hash: str,
    size_bytes: int,
    mime_type: str,
) -> bytes:
    if not SHA256_RE.fullmatch(content_hash) or size_bytes < 0 or not mime_type:
        raise DrBlobCryptoError("blob authenticated context is invalid")
    return canonical_json_bytes(
        {
            "algorithm": ENCRYPTION_ALGORITHM,
            "object_key": object_key,
            "plaintext_sha256": content_hash,
            "plaintext_size": int(size_bytes),
            "mime_type": mime_type,
        }
    )


def _read_exact(source: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = source.read(remaining)
        if not chunk:
            raise DrBlobCryptoError("encrypted blob is truncated")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def encrypt_local_blob(
    *,
    local_path: str,
    content_hash: str,
    size_bytes: int,
    mime_type: str,
    object_key: str,
    key_id: str,
    keyring: DrBlobKeyring,
) -> tuple[BinaryIO, CiphertextIdentity, os.stat_result]:
    """Stream plaintext into an unlinked temporary ciphertext file."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(local_path, flags)
    before = os.fstat(fd)
    output: BinaryIO | None = None
    try:
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise DrBlobCryptoError("local blob is not a stable single-link regular file")
        if before.st_size != size_bytes or size_bytes < 0:
            raise DrBlobCryptoError("local blob size conflicts with the manifest")
        output = tempfile.TemporaryFile(mode="w+b")
        nonce = os.urandom(NONCE_BYTES)
        prefix = ENCRYPTION_MAGIC + nonce
        output.write(prefix)
        ciphertext_digest = hashlib.sha256(prefix)
        plaintext_digest = hashlib.sha256()
        encryptor = Cipher(algorithms.AES(keyring.key(key_id)), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(
            authenticated_context(
                object_key=object_key,
                content_hash=content_hash,
                size_bytes=size_bytes,
                mime_type=mime_type,
            )
        )
        plaintext_size = 0
        while True:
            chunk = os.read(fd, CHUNK_BYTES)
            if not chunk:
                break
            plaintext_digest.update(chunk)
            plaintext_size += len(chunk)
            encrypted = encryptor.update(chunk)
            output.write(encrypted)
            ciphertext_digest.update(encrypted)
        tail = encryptor.finalize()
        if tail:
            output.write(tail)
            ciphertext_digest.update(tail)
        output.write(encryptor.tag)
        ciphertext_digest.update(encryptor.tag)
        if plaintext_size != size_bytes or plaintext_digest.hexdigest() != content_hash:
            raise DrBlobCryptoError("local blob failed plaintext hash/size validation")
        after = os.fstat(fd)
        if any(
            getattr(after, field) != getattr(before, field)
            for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        ):
            raise DrBlobCryptoError("local blob changed during client-side encryption")
        output.flush()
        output.seek(0)
        identity = CiphertextIdentity(
            object_key=object_key,
            key_id=key_id,
            algorithm=ENCRYPTION_ALGORITHM,
            ciphertext_hash=ciphertext_digest.hexdigest(),
            ciphertext_size=plaintext_size + FORMAT_OVERHEAD,
        )
        return output, identity, before
    except Exception:
        if output is not None:
            output.close()
        raise
    finally:
        os.close(fd)


def decrypt_blob_stream(
    source: BinaryIO,
    *,
    ciphertext_size: int,
    expected_ciphertext_hash: str,
    content_hash: str,
    size_bytes: int,
    mime_type: str,
    object_key: str,
    key_id: str,
    keyring: DrBlobKeyring,
    plaintext_sink: BinaryIO,
) -> None:
    """Stream, authenticate, and decrypt one Object Storage response."""

    if ciphertext_size != size_bytes + FORMAT_OVERHEAD or not SHA256_RE.fullmatch(
        expected_ciphertext_hash
    ):
        raise DrBlobCryptoError("encrypted blob ciphertext identity is invalid")
    prefix = _read_exact(source, len(ENCRYPTION_MAGIC) + NONCE_BYTES)
    if prefix[: len(ENCRYPTION_MAGIC)] != ENCRYPTION_MAGIC:
        raise DrBlobCryptoError("encrypted blob format marker is invalid")
    nonce = prefix[len(ENCRYPTION_MAGIC) :]
    ciphertext_digest = hashlib.sha256(prefix)
    plaintext_digest = hashlib.sha256()
    decryptor = Cipher(algorithms.AES(keyring.key(key_id)), modes.GCM(nonce)).decryptor()
    decryptor.authenticate_additional_data(
        authenticated_context(
            object_key=object_key,
            content_hash=content_hash,
            size_bytes=size_bytes,
            mime_type=mime_type,
        )
    )
    remaining = size_bytes
    plaintext_size = 0
    while remaining:
        chunk = _read_exact(source, min(CHUNK_BYTES, remaining))
        remaining -= len(chunk)
        ciphertext_digest.update(chunk)
        plaintext = decryptor.update(chunk)
        plaintext_sink.write(plaintext)
        plaintext_digest.update(plaintext)
        plaintext_size += len(plaintext)
    tag = _read_exact(source, TAG_BYTES)
    ciphertext_digest.update(tag)
    if source.read(1):
        raise DrBlobCryptoError("encrypted blob contains trailing bytes")
    try:
        tail = decryptor.finalize_with_tag(tag)
    except (InvalidTag, ValueError) as exc:
        raise DrBlobCryptoError("encrypted blob authentication failed") from exc
    if tail:
        plaintext_sink.write(tail)
        plaintext_digest.update(tail)
        plaintext_size += len(tail)
    if ciphertext_digest.hexdigest() != expected_ciphertext_hash:
        raise DrBlobCryptoError("encrypted blob ciphertext hash mismatch")
    if plaintext_size != size_bytes or plaintext_digest.hexdigest() != content_hash:
        raise DrBlobCryptoError("decrypted blob plaintext hash/size mismatch")


def metadata_for_ciphertext(identity: CiphertextIdentity) -> dict[str, str]:
    return {
        "ciphertext-sha256": identity.ciphertext_hash,
        "encryption-key-id": identity.key_id,
        "encryption-format": identity.algorithm,
    }


def ciphertext_identity_from_provider(
    *,
    object_key: str,
    content_length: Any,
    metadata: Any,
) -> CiphertextIdentity:
    if not isinstance(metadata, dict):
        raise DrBlobCryptoError("encrypted object metadata is invalid")
    if set(metadata) != {"ciphertext-sha256", "encryption-key-id", "encryption-format"}:
        raise DrBlobCryptoError("encrypted object metadata fields are invalid")
    identity = CiphertextIdentity(
        object_key=object_key,
        key_id=str(metadata["encryption-key-id"]),
        algorithm=str(metadata["encryption-format"]),
        ciphertext_hash=str(metadata["ciphertext-sha256"]),
        ciphertext_size=int(content_length),
    )
    if (
        identity.algorithm != ENCRYPTION_ALGORITHM
        or not KEY_ID_RE.fullmatch(identity.key_id)
        or not SHA256_RE.fullmatch(identity.ciphertext_hash)
        or identity.ciphertext_size < FORMAT_OVERHEAD
    ):
        raise DrBlobCryptoError("encrypted object metadata values are invalid")
    return identity
