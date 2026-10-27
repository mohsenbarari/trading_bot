"""Authenticated private-network transport primitives for market facts.

The provider-private network is a routing boundary, not an authentication
boundary.  Every data request therefore uses mutual TLS and a short-lived HMAC
envelope over the exact transmitted bytes.  Bodies and secrets are never
included in exceptions or logs emitted by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
import hmac
import http.client
import json
from pathlib import Path
import re
import secrets
import sqlite3
import ssl
import time
from typing import Mapping
import zlib


FACT_PATH = "/v1/market-facts"
SNAPSHOT_PATH = "/v1/estimator-snapshots"
SNAPSHOT_CURRENT_PATH = "/v1/estimator-snapshots/current"
HEALTH_PATH = "/healthz"
MAX_WIRE_BYTES = 1_048_576
MAX_DOCUMENT_BYTES = 2_097_152
DEFAULT_CLOCK_SKEW_SECONDS = 30
DEFAULT_REPLAY_WINDOW_SECONDS = 120
KEY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
NONCE_RE = re.compile(r"^[a-f0-9]{32}$")
SIGNATURE_RE = re.compile(r"^[a-f0-9]{64}$")


class MarketTransportError(RuntimeError):
    """A payload-free, operator-safe transport failure."""


class MarketAuthenticationError(MarketTransportError):
    def __init__(self, status: int, reason_code: str):
        super().__init__(reason_code)
        self.status = status
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class EncodedDocument:
    body: bytes
    content_encoding: str


def canonical_request(
    method: str,
    path: str,
    key_id: str,
    timestamp: str,
    nonce: str,
    content_encoding: str,
    body: bytes,
) -> bytes:
    return "\n".join(
        (
            method.upper(),
            path,
            key_id,
            timestamp,
            nonce,
            content_encoding,
            hashlib.sha256(body).hexdigest(),
        )
    ).encode("ascii")


def sign_request(
    key: bytes,
    method: str,
    path: str,
    key_id: str,
    timestamp: str,
    nonce: str,
    content_encoding: str,
    body: bytes,
) -> str:
    return hmac.new(
        key,
        canonical_request(
            method, path, key_id, timestamp, nonce, content_encoding, body
        ),
        hashlib.sha256,
    ).hexdigest()


def encode_document(
    document: Mapping[str, object],
    *,
    compress_threshold_bytes: int = 32_768,
) -> EncodedDocument:
    raw = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise MarketTransportError("market_transport_document_too_large")
    if len(raw) >= compress_threshold_bytes:
        body = gzip.compress(raw, compresslevel=6, mtime=0)
        encoding = "gzip"
    else:
        body = raw
        encoding = "identity"
    if len(body) > MAX_WIRE_BYTES:
        raise MarketTransportError("market_transport_wire_body_too_large")
    return EncodedDocument(body=body, content_encoding=encoding)


def decode_document(body: bytes, content_encoding: str) -> dict[str, object]:
    if not body or len(body) > MAX_WIRE_BYTES:
        raise MarketTransportError("market_transport_wire_body_invalid")
    if content_encoding == "identity":
        raw = body
    elif content_encoding == "gzip":
        inflater = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
        raw = inflater.decompress(body, MAX_DOCUMENT_BYTES + 1)
        if (
            len(raw) > MAX_DOCUMENT_BYTES
            or inflater.unconsumed_tail
            or not inflater.eof
        ):
            raise MarketTransportError("market_transport_document_too_large")
        raw += inflater.flush()
    else:
        raise MarketTransportError("market_transport_encoding_unsupported")
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise MarketTransportError("market_transport_document_too_large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketTransportError("market_transport_json_invalid") from exc
    if not isinstance(value, dict):
        raise MarketTransportError("market_transport_object_required")
    return value


def signed_headers(
    *,
    key_id: str,
    key: bytes,
    body: bytes,
    content_encoding: str,
    timestamp: int | None = None,
    nonce: str | None = None,
    path: str = FACT_PATH,
) -> dict[str, str]:
    if not KEY_ID_RE.fullmatch(key_id) or len(key) < 32:
        raise MarketTransportError("market_transport_key_invalid")
    timestamp_text = str(int(time.time()) if timestamp is None else int(timestamp))
    nonce_text = nonce or secrets.token_hex(16)
    signature = sign_request(
        key,
        "POST",
        path,
        key_id,
        timestamp_text,
        nonce_text,
        content_encoding,
        body,
    )
    return {
        "Content-Type": "application/json",
        "Content-Encoding": content_encoding,
        "Content-Length": str(len(body)),
        "X-Market-Key-ID": key_id,
        "X-Market-Timestamp": timestamp_text,
        "X-Market-Nonce": nonce_text,
        "X-Market-Signature": signature,
    }


def initialize_replay_store(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS transport_nonces (
            key_id TEXT NOT NULL,
            nonce TEXT NOT NULL,
            accepted_at_epoch INTEGER NOT NULL,
            expires_at_epoch INTEGER NOT NULL,
            PRIMARY KEY(key_id, nonce)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS transport_nonces_expiry_idx "
        "ON transport_nonces(expires_at_epoch)"
    )


def authenticate_request(
    connection: sqlite3.Connection,
    *,
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: bytes,
    keys: Mapping[str, bytes],
    now_epoch: int | None = None,
    max_clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS,
    replay_window_seconds: int = DEFAULT_REPLAY_WINDOW_SECONDS,
) -> str:
    key_id = headers.get("X-Market-Key-ID", "")
    timestamp = headers.get("X-Market-Timestamp", "")
    nonce = headers.get("X-Market-Nonce", "")
    signature = headers.get("X-Market-Signature", "")
    encoding = headers.get("Content-Encoding", "identity").lower()
    if not KEY_ID_RE.fullmatch(key_id):
        raise MarketAuthenticationError(401, "AUTH_KEY_ID_INVALID")
    if not NONCE_RE.fullmatch(nonce):
        raise MarketAuthenticationError(401, "AUTH_NONCE_INVALID")
    if not SIGNATURE_RE.fullmatch(signature):
        raise MarketAuthenticationError(401, "AUTH_SIGNATURE_INVALID")
    try:
        timestamp_number = int(timestamp)
    except ValueError as exc:
        raise MarketAuthenticationError(401, "AUTH_TIMESTAMP_INVALID") from exc
    now = int(time.time()) if now_epoch is None else int(now_epoch)
    if abs(now - timestamp_number) > max_clock_skew_seconds:
        raise MarketAuthenticationError(401, "AUTH_CLOCK_SKEW")
    key = keys.get(key_id)
    if key is None or len(key) < 32:
        raise MarketAuthenticationError(401, "AUTH_KEY_UNKNOWN")
    expected = sign_request(
        key, method, path, key_id, timestamp, nonce, encoding, body
    )
    if not hmac.compare_digest(signature, expected):
        raise MarketAuthenticationError(401, "AUTH_SIGNATURE_INVALID")
    initialize_replay_store(connection)
    connection.execute(
        "DELETE FROM transport_nonces WHERE expires_at_epoch < ?", (now,)
    )
    try:
        connection.execute(
            "INSERT INTO transport_nonces(key_id,nonce,accepted_at_epoch,expires_at_epoch) "
            "VALUES(?,?,?,?)",
            (key_id, nonce, now, now + replay_window_seconds),
        )
    except sqlite3.IntegrityError as exc:
        raise MarketAuthenticationError(409, "AUTH_REPLAY_DETECTED") from exc
    return encoding


def read_key(path: str | Path) -> bytes:
    value = Path(path).read_bytes()
    if len(value) < 32:
        raise MarketTransportError("market_transport_key_invalid")
    return value


def client_tls_context(*, ca: str | Path, cert: str | Path, key: str | Path) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(ca))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    return context


def server_tls_context(*, ca: str | Path, cert: str | Path, key: str | Path) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cafile=str(ca))
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    return context


def post_document(
    *,
    host: str,
    port: int,
    path: str,
    document: Mapping[str, object],
    key_id: str,
    hmac_key: bytes,
    tls_context: ssl.SSLContext,
    timeout_seconds: float,
) -> tuple[int, dict[str, object]]:
    encoded = encode_document(document)
    headers = signed_headers(
        key_id=key_id,
        key=hmac_key,
        body=encoded.body,
        content_encoding=encoded.content_encoding,
        path=path,
    )
    connection = http.client.HTTPSConnection(
        host, port=port, context=tls_context, timeout=timeout_seconds
    )
    try:
        connection.request("POST", path, body=encoded.body, headers=headers)
        response = connection.getresponse()
        payload = response.read(MAX_WIRE_BYTES + 1)
        status = int(response.status)
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        raise MarketTransportError("market_transport_request_failed") from exc
    finally:
        connection.close()
    if len(payload) > MAX_WIRE_BYTES:
        raise MarketTransportError("market_transport_response_too_large")
    try:
        document_response = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketTransportError("market_transport_response_invalid") from exc
    if not isinstance(document_response, dict):
        raise MarketTransportError("market_transport_response_invalid")
    return status, document_response
