#!/usr/bin/env python3
"""Produce one non-secret, locally TLS-verified Writer Witness attestation.

This helper is intentionally read-only: it loads one site's root-only lease
agent configuration, performs the HMAC-authenticated HTTPS request from that
same site, and prints a receipt that contains no URL or secret.  It neither
starts containers nor acquires, renews, drains, or writes a writer lease.

The companion pair verifier consumes the FI and IR receipts only after each
site has performed its own CA-pinned TLS handshake.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import ssl
import stat
import sys
from typing import Any, Mapping, Sequence
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlsplit
from uuid import uuid4


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts import prepare_writer_witness_immutable_release as control  # noqa: E402


CLIENT_ATTESTATION_SCHEMA = "gold-trade-writer-witness-client-live-attestation-v1"
WITNESS_CONFIG_ATTESTATION_PATH = "/v1/writer-witness/config-attestation"
WITNESS_AUTH_VERSION = 1
MAX_FILE_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 128 * 1024
SECRET_RE = re.compile(r"^[0-9a-f]{64}$")
KEY_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class WriterWitnessClientAttestationError(RuntimeError):
    """The site cannot prove its configured TLS/profile contract."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WriterWitnessClientAttestationError("JSON input contains duplicate keys")
        result[key] = value
    return result


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _read_file(
    path: Path,
    *,
    field: str,
    private: bool,
    maximum_bytes: int = MAX_FILE_BYTES,
) -> bytes:
    if not path.is_absolute():
        raise WriterWitnessClientAttestationError(f"{field} must be absolute")
    try:
        resolved = path.resolve(strict=True)
        path_metadata = path.lstat()
    except OSError as exc:
        raise WriterWitnessClientAttestationError(f"cannot inspect {field}") from exc
    if resolved != path or stat.S_ISLNK(path_metadata.st_mode):
        raise WriterWitnessClientAttestationError(f"{field} must be one canonical non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise WriterWitnessClientAttestationError(f"cannot securely open {field}") from exc
    identity_fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_uid",
        "st_gid",
        "st_nlink",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > maximum_bytes
            or stat.S_IMODE(before.st_mode) & (0o077 if private else 0o022)
        ):
            raise WriterWitnessClientAttestationError(f"{field} has unsafe ownership or mode")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                raise WriterWitnessClientAttestationError(f"{field} changed during read")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if any(getattr(before, name) != getattr(after, name) for name in identity_fields):
            raise WriterWitnessClientAttestationError(f"{field} changed during read")
    finally:
        os.close(descriptor)
    try:
        final = path.lstat()
    except OSError as exc:
        raise WriterWitnessClientAttestationError(f"cannot re-check {field}") from exc
    if final.st_dev != before.st_dev or final.st_ino != before.st_ino:
        raise WriterWitnessClientAttestationError(f"{field} changed during read")
    return b"".join(chunks)


def _parse_time(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise WriterWitnessClientAttestationError(f"{field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WriterWitnessClientAttestationError(f"{field} is invalid") from exc
    if parsed.tzinfo is None:
        raise WriterWitnessClientAttestationError(f"{field} lacks a timezone")
    return parsed.astimezone(timezone.utc)


def _decode_public_key(value: object, *, field: str) -> bytes:
    if not isinstance(value, str):
        raise WriterWitnessClientAttestationError(f"{field} is invalid")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise WriterWitnessClientAttestationError(f"{field} is not valid base64") from exc
    if len(decoded) != 32:
        raise WriterWitnessClientAttestationError(f"{field} has an invalid length")
    return decoded


def _decode_signature(value: object) -> bytes:
    if not isinstance(value, str):
        raise WriterWitnessClientAttestationError("Witness attestation signature is invalid")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise WriterWitnessClientAttestationError(
            "Witness attestation signature is not valid base64"
        ) from exc
    if len(decoded) != 64:
        raise WriterWitnessClientAttestationError("Witness attestation signature has an invalid length")
    return decoded


def _validate_url(value: object) -> str:
    if not isinstance(value, str) or any(character in value for character in "\r\n\x00"):
        raise WriterWitnessClientAttestationError("Witness URL is invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise WriterWitnessClientAttestationError("Witness URL is invalid")
    try:
        port = parsed.port
    except ValueError as exc:
        raise WriterWitnessClientAttestationError("Witness URL is invalid") from exc
    if port is not None and not 1 <= port <= 65535:
        raise WriterWitnessClientAttestationError("Witness URL is invalid")
    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise WriterWitnessClientAttestationError("Witness URL is invalid") from exc
    # ``urlsplit`` returns IPv6 hosts without brackets.  Build a canonical
    # URL here so both sites hash the same endpoint binding and so the value
    # later passed to urllib has no user-controlled path/query component.
    rendered_host = f"[{host}]" if ":" in host else host
    rendered_port = f":{port}" if port is not None else ""
    return f"https://{rendered_host}{rendered_port}"


def _request_headers(
    *,
    key_id: str,
    secret: str,
    site: str,
    request_id: str,
    now: datetime,
) -> dict[str, str]:
    if not KEY_ID_RE.fullmatch(key_id) or not SECRET_RE.fullmatch(secret):
        raise WriterWitnessClientAttestationError("Witness client credential is invalid")
    if site not in {"webapp_fi", "webapp_ir"} or not REQUEST_ID_RE.fullmatch(request_id):
        raise WriterWitnessClientAttestationError("Witness attestation nonce is invalid")
    timestamp = int(now.timestamp())
    canonical = "\n".join(
        (
            f"writer-witness-auth-v{WITNESS_AUTH_VERSION}",
            "GET",
            WITNESS_CONFIG_ATTESTATION_PATH,
            str(timestamp),
            request_id,
            site,
            hashlib.sha256(b"").hexdigest(),
        )
    ).encode("utf-8")
    return {
        "Accept": "application/json",
        "X-Writer-Witness-Key-Id": key_id,
        "X-Writer-Witness-Site": site,
        "X-Writer-Witness-Timestamp": str(timestamp),
        "X-Writer-Witness-Request-Id": request_id,
        "X-Writer-Witness-Signature": hmac.new(
            secret.encode("ascii"), canonical, hashlib.sha256
        ).hexdigest(),
    }


class _NoRedirect(urlrequest.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return None


def _open_https(request: urlrequest.Request, *, context: ssl.SSLContext, timeout: float):
    # Do not inherit HTTP(S)_PROXY/NO_PROXY from the process environment.
    # Witness attestations are a direct site-to-Witness proof; a proxy would
    # weaken both the endpoint binding and the local TLS observation.
    opener = urlrequest.build_opener(
        urlrequest.ProxyHandler({}),
        _NoRedirect(),
        urlrequest.HTTPSHandler(context=context),
    )
    return opener.open(request, timeout=timeout)


def _request_attestation(
    *,
    base_url: str,
    headers: Mapping[str, str],
    ca_bundle: bytes,
    timeout_seconds: float,
) -> dict[str, Any]:
    # Use the already securely-read CA bytes.  Supplying a file path here
    # would reopen it after our ownership/link checks and make the receipt's
    # CA hash a different object from the trust store used for TLS.
    try:
        ca_data = ca_bundle.decode("ascii")
    except UnicodeDecodeError as exc:
        raise WriterWitnessClientAttestationError("Witness CA bundle is not ASCII PEM") from exc
    try:
        context = ssl.create_default_context(cadata=ca_data)
    except ssl.SSLError as exc:
        raise WriterWitnessClientAttestationError("Witness CA bundle is invalid") from exc
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    request = urlrequest.Request(
        base_url + WITNESS_CONFIG_ATTESTATION_PATH,
        headers=dict(headers),
        method="GET",
    )
    try:
        with _open_https(request, context=context, timeout=timeout_seconds) as response:
            if response.status != 200:
                raise WriterWitnessClientAttestationError("Witness config attestation returned a non-success status")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urlerror.HTTPError as exc:
        raise WriterWitnessClientAttestationError(
            "Witness config attestation returned an HTTP error"
        ) from exc
    except (urlerror.URLError, ssl.SSLError, TimeoutError, OSError) as exc:
        raise WriterWitnessClientAttestationError(
            "Witness config attestation TLS request failed"
        ) from exc
    if len(raw) < 1 or len(raw) > MAX_RESPONSE_BYTES:
        raise WriterWitnessClientAttestationError("Witness config attestation response size is invalid")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WriterWitnessClientAttestationError("Witness config attestation response is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise WriterWitnessClientAttestationError("Witness config attestation response is not an object")
    return payload


def _validate_witness_attestation(
    *,
    payload: Mapping[str, Any],
    expected_profile: Mapping[str, Any],
    expected_public_key: str,
    expected_site: str,
    expected_key_id_sha256: str | None,
    request_id: str,
    now: datetime,
    timeout_seconds: float,
) -> dict[str, Any]:
    expected_fields = {
        "contract_version",
        "request_id",
        "caller_site",
        "caller_key_id_sha256",
        "witness_time",
        "runtime_profile_sha256",
        "release_manifest_sha256",
        "witness_public_key",
        "profile",
        "witness_signature",
    }
    if set(payload) != expected_fields or payload.get("contract_version") != 2:
        raise WriterWitnessClientAttestationError("Witness config attestation schema is invalid")
    if payload.get("request_id") != request_id:
        raise WriterWitnessClientAttestationError("Witness config attestation nonce does not match")
    if payload.get("caller_site") != expected_site:
        raise WriterWitnessClientAttestationError(
            "Witness config attestation caller site does not match"
        )
    caller_key_id_sha256 = payload.get("caller_key_id_sha256")
    if not isinstance(caller_key_id_sha256, str) or not SHA256_RE.fullmatch(caller_key_id_sha256):
        raise WriterWitnessClientAttestationError(
            "Witness config attestation caller credential identity is invalid"
        )
    if (
        expected_key_id_sha256 is not None
        and not hmac.compare_digest(caller_key_id_sha256, expected_key_id_sha256)
    ):
        raise WriterWitnessClientAttestationError(
            "Witness config attestation caller credential identity does not match"
        )
    witness_time = _parse_time(payload.get("witness_time"), field="Witness attestation time")
    maximum_difference = expected_profile["witness"]["max_clock_skew_seconds"] + math.ceil(timeout_seconds) + 5
    if abs(now - witness_time) > timedelta(seconds=maximum_difference):
        raise WriterWitnessClientAttestationError("Witness config attestation time is not fresh")
    if (
        payload.get("runtime_profile_sha256")
        != expected_profile["source_runtime_profile_sha256"]
        or payload.get("release_manifest_sha256")
        != expected_profile["source_release_manifest_sha256"]
    ):
        raise WriterWitnessClientAttestationError(
            "Witness config attestation profile or release manifest is unexpected"
        )
    response_public_key = payload.get("witness_public_key")
    if not isinstance(response_public_key, str) or response_public_key != expected_public_key:
        raise WriterWitnessClientAttestationError(
            "Witness config attestation public key differs from the client-pinned key"
        )
    public_key = _decode_public_key(response_public_key, field="Witness attestation public key")
    profile = payload.get("profile")
    if not isinstance(profile, dict) or profile != expected_profile["witness"]:
        raise WriterWitnessClientAttestationError("Witness config attestation profile is unexpected")
    unsigned = {key: payload[key] for key in expected_fields if key != "witness_signature"}
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(public_key).verify(
            _decode_signature(payload.get("witness_signature")),
            _canonical_json_bytes(unsigned),
        )
    except ImportError as exc:
        raise WriterWitnessClientAttestationError(
            "cryptography is required for Witness attestation verification"
        ) from exc
    except InvalidSignature as exc:
        raise WriterWitnessClientAttestationError(
            "Witness config attestation signature is invalid"
        ) from exc
    return dict(payload)


def attest_client(
    *,
    agent_config_path: Path,
    profile_path: Path = control.DEFAULT_PROFILE_PATH,
    request_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Perform the local CA-pinned handshake and return a non-secret receipt."""

    if os.geteuid() != 0:
        raise WriterWitnessClientAttestationError("Writer Witness client attestation must run as root")
    profile = control._load_profile(profile_path)
    raw_config = control._load_json_bytes(
        control._read_controlled_file(
            agent_config_path,
            field="WebApp writer lease-agent config",
            root_only=True,
        ),
        field="WebApp writer lease-agent config",
    )
    site = raw_config.get("site")
    client_name = {
        "webapp_fi": "webapp_fi_client",
        "webapp_ir": "webapp_ir_client",
    }.get(site)
    if client_name is None:
        raise WriterWitnessClientAttestationError("WebApp writer lease-agent site is invalid")
    static = control._verify_webapp_client_timing(
        agent_config_path=agent_config_path,
        profile=profile,
        client_name=client_name,
    )
    witness = raw_config["witness"]
    if not isinstance(witness, dict):  # Kept explicit even after static validation.
        raise WriterWitnessClientAttestationError("WebApp Witness configuration is invalid")
    base_url = _validate_url(witness.get("url"))
    key_id = witness.get("key_id")
    if not isinstance(key_id, str):
        raise WriterWitnessClientAttestationError("Witness client key id is invalid")
    secret_path = Path(str(witness.get("secret_file") or ""))
    public_key_path = Path(str(witness.get("public_key_file") or ""))
    ca_bundle_path = Path(str(witness.get("ca_bundle") or ""))
    try:
        secret = _read_file(secret_path, field="Witness client secret", private=True, maximum_bytes=16 * 1024).decode("ascii").strip()
        pinned_public_key = _read_file(
            public_key_path,
            field="Witness client pinned public key",
            private=False,
            maximum_bytes=16 * 1024,
        ).decode("ascii").strip()
        ca_bundle = _read_file(
            ca_bundle_path,
            field="Witness client CA bundle",
            private=False,
        )
    except UnicodeDecodeError as exc:
        raise WriterWitnessClientAttestationError("Witness client credential material is not ASCII") from exc
    _decode_public_key(pinned_public_key, field="Witness client pinned public key")
    timeout = witness.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise WriterWitnessClientAttestationError("Witness client timeout is invalid")
    timeout_seconds = float(timeout)
    if not 0.1 <= timeout_seconds <= 10:
        raise WriterWitnessClientAttestationError("Witness client timeout is invalid")
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    nonce = str(uuid4()) if request_id is None else request_id
    payload = _request_attestation(
        base_url=base_url,
        headers=_request_headers(
            key_id=key_id,
            secret=secret,
            site=site,
            request_id=nonce,
            now=observed_at,
        ),
        ca_bundle=ca_bundle,
        timeout_seconds=timeout_seconds,
    )
    witness_attestation = _validate_witness_attestation(
        payload=payload,
        expected_profile=profile,
        expected_public_key=pinned_public_key,
        expected_site=site,
        expected_key_id_sha256=hashlib.sha256(key_id.encode("utf-8")).hexdigest(),
        request_id=nonce,
        now=observed_at,
        timeout_seconds=timeout_seconds,
    )
    return {
        "schema": CLIENT_ATTESTATION_SCHEMA,
        "status": "attested",
        "site": site,
        "mode": static["mode"],
        "observed_at": observed_at.isoformat(),
        "request_id": nonce,
        "tls_verified": True,
        "witness_endpoint_sha256": hashlib.sha256(base_url.encode("utf-8")).hexdigest(),
        "ca_bundle_sha256": hashlib.sha256(ca_bundle).hexdigest(),
        "pinned_witness_public_key": pinned_public_key,
        "runtime_profile_sha256": witness_attestation["runtime_profile_sha256"],
        "release_manifest_sha256": witness_attestation["release_manifest_sha256"],
        "profile": witness_attestation["profile"],
        "witness_attestation": witness_attestation,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-config", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=control.DEFAULT_PROFILE_PATH)
    parser.add_argument("--request-id")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        receipt = attest_client(
            agent_config_path=arguments.agent_config,
            profile_path=arguments.profile,
            request_id=arguments.request_id,
        )
        if arguments.output is not None:
            control._write_optional_attestation(arguments.output, receipt)
        print(_canonical_json_bytes(receipt).decode("utf-8"))
        return 0
    except (WriterWitnessClientAttestationError, control.WitnessReleasePreparationError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
