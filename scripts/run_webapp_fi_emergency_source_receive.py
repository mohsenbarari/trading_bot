#!/usr/bin/env python3
"""Immutable WA-FI bootstrap anchor for the Emergency source receive.

This program is installed separately from, and is never read from, the
downloaded application source bundle.  It verifies the controller-signed
control descriptor itself, then direct-downloads only the small receiver
bootstrap object from private versioned Object Storage.  It hash-verifies and
extracts that receiver before executing it.  The verified receiver performs
the second direct, version-bound GET for the age-encrypted Git bundle.

SSH/control transport may carry this program's bounded arguments and the two
short-lived URLs through stdin.  It never carries either artifact's bytes and
accepts no Object Storage credentials.

The descriptor verifier does not accept a caller-selected signing key.  Its
only trust root is the create-only, root-owned trust record at
``/etc/trading-bot-three-site/trust/webapp-fi-emergency-source-signer.json``.
That record must be pinned locally through a trusted console or an already
verified control channel *before* this program is used for a receive.  The
``provision-pinned-signer`` action implements only that local, create-only
write; it has no network, SSH, Object Storage, service, or Docker behavior.

Before a source GET, a durable root-only receipt binds one campaign ID to one
verified descriptor. A retry with that exact descriptor is safe after a
reboot only if the volatile candidate no longer exists; a descriptor swap or
an existing candidate always requires a new campaign ID and is never cleaned
up automatically.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import ssl
import stat
import subprocess
import sys
import tarfile
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:  # pragma: no cover - deployment requirements provide cryptography.
    InvalidSignature = None  # type: ignore[assignment,misc]
    serialization = None  # type: ignore[assignment,misc]
    Ed25519PublicKey = None  # type: ignore[assignment,misc]


DESCRIPTOR_SCHEMA = "gold-trade-webapp-fi-emergency-source-descriptor-v1"
SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-emergency-source-descriptor-v1\x00"
SIGNATURE_ALGORITHM = "ed25519"
SOURCE_SITE = "controller"
DESTINATION_SITE = "webapp_fi"
BOOTSTRAP_OBJECT_LAYOUT = "webapp-fi-emergency-source-bootstrap/v1"
URL_MAP_SCHEMA = "gold-trade-webapp-fi-emergency-source-url-map-v1"
RECEIVER_BOOTSTRAP_SCHEMA = "gold-trade-webapp-fi-emergency-source-receiver-bootstrap-v1"
DEFAULT_RECEIVER_ROOT = Path("/run/trading-bot-three-site-source-bootstrap")
TRUST_RECORD_SCHEMA = "gold-trade-webapp-fi-emergency-source-pinned-signer-v1"
ANCHOR_APPROVAL_SCHEMA = "gold-trade-webapp-fi-emergency-source-anchor-approval-v2"
SIGNER_APPROVAL_SCHEMA = "gold-trade-webapp-fi-emergency-source-signer-approval-v1"
CAMPAIGN_RECEIVE_LOCK_SCHEMA = "gold-trade-webapp-fi-emergency-source-receive-lock-v1"
TRUST_ROOT = Path("/etc/trading-bot-three-site/trust")
PINNED_SIGNER_RECORD = TRUST_ROOT / "webapp-fi-emergency-source-signer.json"
PINNED_ANCHOR_APPROVAL = TRUST_ROOT / "webapp-fi-emergency-source-anchor-approval.json"
PINNED_SIGNER_APPROVAL = TRUST_ROOT / "webapp-fi-emergency-source-signer-approval.json"
PINNED_SIGNER_CANDIDATE = TRUST_ROOT / "webapp-fi-emergency-source-signing-public.candidate"
PINNED_ANCHOR_PATH = Path("/usr/local/lib/trading-bot-three-site/run_webapp_fi_emergency_source_receive.py")
FI_CAMPAIGN_IDENTITY_ROOT = Path("/etc/trading-bot-three-site/campaigns")
FI_EMERGENCY_SOURCE_IDENTITY_LEAF = "webapp-fi/emergency-source.agekey"
FI_EMERGENCY_SOURCE_RECEIVE_LOCK_LEAF = "webapp-fi/emergency-source-receive-lock.json"
AGE_BINARY = Path("/usr/bin/age")
AGE_KEYGEN_BINARY = Path("/usr/bin/age-keygen")

# No caller can replace these source identities.  The anchor validates them
# before the downloaded receiver bootstrap exists or is executed.
SOURCE_RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
SOURCE_RELEASE_TREE = "b4da321f72b84c075bd267bda1211f0ff68b91d6"
# This must match the source-only e1a30972 checkout accepted by the controller,
# never the later controller checkout that publishes/receives it.
EMERGENCY_PATCH_SHA = "e1a309725154ab6b67655ebdfe22c73d831aa72e"
EMERGENCY_PATCH_TREE = "d158aa5f520fd625537f927fb079196aa24fa302"
OBJECT_LAYOUT = "webapp-fi-emergency-source/v1"
ANCHOR_OBJECT_LAYOUT = "webapp-fi-emergency-source-anchor/v1"

MAX_DESCRIPTOR_BYTES = 128 * 1024
MAX_KEY_BYTES = 1024
MAX_ANCHOR_APPROVAL_BYTES = 4096
MAX_BOOTSTRAP_BYTES = 4 * 1024 * 1024
MAX_URL_MAP_BYTES = 64 * 1024
MAX_URL_BYTES = 16 * 1024
MAX_CAMPAIGN_RECEIVE_LOCK_BYTES = 16 * 1024
MIN_PRESIGNED_TTL_SECONDS = 60
MAX_PRESIGNED_TTL_SECONDS = 900
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
DISK_HEADROOM_BYTES = 64 * 1024 * 1024
SOURCE_RECEIVE_DISK_HEADROOM_BYTES = 128 * 1024 * 1024

SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
GIT_SHA_RE = re.compile(r"^[a-f0-9]{40}$", re.ASCII)
VERSION_ID_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{1,1024}$", re.ASCII)
CAMPAIGN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII)
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$", re.ASCII)
PREFIX_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,127}$", re.ASCII)
REGION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$", re.ASCII)
RECIPIENT_KEY_ID_RE = re.compile(r"^age-recipient-sha256:[a-f0-9]{64}$", re.ASCII)
SIGNER_KEY_ID_RE = re.compile(r"^ed25519-sha256:[a-f0-9]{64}$", re.ASCII)
AGE_RECIPIENT_RE = re.compile(r"^age1[ac-hj-np-z02-9]{20,128}$", re.ASCII)

_PRESIGNED_REQUIRED = frozenset(
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
_PRESIGNED_OPTIONAL = frozenset({"X-Amz-Security-Token"})
_DIRECT_TRANSPORT_ENVIRONMENT_NAMES = frozenset(
    {
        "all_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "requests_ca_bundle",
        "curl_ca_bundle",
        "aws_ca_bundle",
        "ssl_cert_file",
        "ssl_cert_dir",
        "openssl_conf",
        "openssl_modules",
        "sslkeylogfile",
        "pythonhttpsverify",
    }
)


class EmergencySourceBootstrapError(RuntimeError):
    pass


def _fail(message: str) -> None:
    raise EmergencySourceBootstrapError(message)


def _require_root() -> None:
    if os.geteuid() != 0:
        _fail("WebApp-FI source bootstrap must run as root")


def _require_direct_object_storage_environment() -> None:
    """Refuse inherited routing or TLS trust inputs before direct HTTPS.

    A supplied ``ProxyHandler({})`` does not make OpenSSL ignore
    ``SSL_CERT_FILE`` or ``SSL_CERT_DIR``. They remain effective under
    ``python3 -I`` and could authorize a terminal TLS interceptor, so the
    anchor has no compatibility exception for proxy or CA override variables.
    """

    forbidden = sorted(key for key in os.environ if key.lower() in _DIRECT_TRANSPORT_ENVIRONMENT_NAMES)
    if forbidden:
        _fail("direct Object Storage transport forbids proxy and TLS override environment variables")


def _environment() -> dict[str, str]:
    return {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "LC_ALL": "C",
        "LANG": "C",
        "PATH": os.defpath,
    }


def _canonical(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    except (TypeError, ValueError, RecursionError) as exc:
        raise EmergencySourceBootstrapError("control value cannot be canonicalized") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("control JSON contains duplicate fields")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    _fail("control JSON constants are unsupported")


def _text(value: object, *, field: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        _fail(f"{field} is invalid")
    if "\x00" in value or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        _fail(f"{field} contains a control character")
    return value


def _pattern(value: object, *, field: str, pattern: re.Pattern[str]) -> str:
    text = _text(value, field=field)
    if pattern.fullmatch(text) is None:
        _fail(f"{field} has an unsafe format")
    return text


def _positive(value: object, *, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        _fail(f"{field} is outside its bound")
    return value


def _absolute(path: Path | str, *, field: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        _fail(f"{field} must be absolute")
    return candidate


def _safe_directory(path: Path, *, label: str, private: bool) -> Path:
    path = _absolute(path, field=label)
    current = Path(path.anchor)
    final_state: os.stat_result | None = None
    for component in path.parts[1:]:
        current /= component
        try:
            state = current.lstat()
        except OSError as exc:
            raise EmergencySourceBootstrapError(f"{label} cannot be inspected") from exc
        mode = stat.S_IMODE(state.st_mode)
        # `/etc` and `/run` are usually root-owned 0755 ancestors.  Private
        # means the final controlled directory is 0700; ancestors only need
        # to resist group/other writes (with the root sticky `/tmp` exception).
        writable = bool(mode & 0o022)
        sticky_root = state.st_uid == 0 and bool(state.st_mode & stat.S_ISVTX)
        if (
            stat.S_ISLNK(state.st_mode)
            or not stat.S_ISDIR(state.st_mode)
            or state.st_uid != 0
            or (writable and not sticky_root)
        ):
            _fail(f"{label} is not root-controlled")
        final_state = state
    if final_state is None:
        _fail(f"{label} directory chain is invalid")
    # These leaves hold either a trust decision or a private age identity.
    # Do not silently accept a merely non-world-readable directory such as
    # 0710: an exact 0700 leaf makes the operational contract auditable and
    # prevents a future group policy change from widening the trust boundary.
    if private and stat.S_IMODE(final_state.st_mode) != 0o700:
        _fail(f"{label} final directory is not root-only 0700")
    return path


def _safe_file(path: Path, *, label: str, maximum: int, private: bool) -> Path:
    path = _absolute(path, field=label)
    _safe_directory(path.parent, label=f"{label} parent", private=False)
    try:
        state = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise EmergencySourceBootstrapError(f"{label} cannot be inspected") from exc
    if (
        resolved != path
        or stat.S_ISLNK(state.st_mode)
        or not stat.S_ISREG(state.st_mode)
        or state.st_uid != 0
        or state.st_nlink != 1
        or (stat.S_IMODE(state.st_mode) != 0o600 if private else bool(stat.S_IMODE(state.st_mode) & 0o022))
        or not 1 <= state.st_size <= maximum
    ):
        _fail(f"{label} is unsafe")
    return path


def _ensure_fixed_directory(path: Path, *, label: str, final_mode: int) -> Path:
    """Create one fixed persistent/runtime root without trusting a caller path.

    Only constants in this module call this helper.  Existing ancestors such
    as ``/etc`` and ``/run`` may correctly be root-owned 0755; newly created
    intermediate directories are root-owned 0755 and the requested final
    leaf is exact-mode.  Every successful create is fsynced at both levels so
    an identity/trust root remains present after an abrupt reboot.
    """

    path = _absolute(path, field=label)
    if final_mode not in {0o700, 0o755}:
        _fail(f"{label} has an unsupported fixed mode")
    current = Path(path.anchor)
    parts = path.parts[1:]
    if not parts:
        _fail(f"{label} cannot be the filesystem root")
    for index, component in enumerate(parts):
        candidate = current / component
        is_final = index == len(parts) - 1
        expected_mode = final_mode if is_final else 0o755
        try:
            state = candidate.lstat()
        except FileNotFoundError:
            # `current` was checked during the preceding iteration (or is
            # `/`), and is never caller-controlled.
            if current != Path(current.anchor):
                _safe_directory(current, label=f"{label} parent", private=False)
            try:
                candidate.mkdir(mode=expected_mode)
                os.chmod(candidate, expected_mode)
                state = candidate.lstat()
            except OSError as exc:
                raise EmergencySourceBootstrapError(f"{label} cannot be created") from exc
            if (
                stat.S_ISLNK(state.st_mode)
                or not stat.S_ISDIR(state.st_mode)
                or state.st_uid != 0
                or stat.S_IMODE(state.st_mode) != expected_mode
            ):
                _fail(f"{label} was not created as the required root-owned directory")
            _fsync_directory(current, label=f"{label} parent")
            _fsync_directory(candidate, label=label)
        except OSError as exc:
            raise EmergencySourceBootstrapError(f"{label} cannot be inspected") from exc
        else:
            if (
                stat.S_ISLNK(state.st_mode)
                or not stat.S_ISDIR(state.st_mode)
                or state.st_uid != 0
                or stat.S_IMODE(state.st_mode) & 0o022
            ):
                _fail(f"{label} is not rooted in root-controlled directories")
            if is_final and stat.S_IMODE(state.st_mode) != expected_mode:
                _fail(f"{label} final directory mode differs from its fixed contract")
        current = candidate
    return _safe_directory(path, label=label, private=(final_mode == 0o700))


def _ensure_campaign_identity_root() -> Path:
    """Provision the fixed reboot-safe 0700 campaign identity leaf only."""

    return _ensure_fixed_directory(
        FI_CAMPAIGN_IDENTITY_ROOT,
        label="WebApp-FI campaign identity root",
        final_mode=0o700,
    )


def _fsync_directory(path: Path, *, label: str) -> None:
    """Durably publish a create-only entry after its file has been fsynced."""

    path = _safe_directory(path, label=label, private=False)
    fd: int | None = None
    try:
        fd = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(fd)
        if not stat.S_ISDIR(opened.st_mode) or opened.st_uid != 0:
            _fail(f"{label} changed while being fsynced")
        os.fsync(fd)
    except EmergencySourceBootstrapError:
        raise
    except OSError as exc:
        raise EmergencySourceBootstrapError(f"{label} cannot be fsynced") from exc
    finally:
        if fd is not None:
            os.close(fd)


def _read_file(path: Path, *, label: str, maximum: int, private: bool) -> bytes:
    path = _safe_file(path, label=label, maximum=maximum, private=private)
    fd: int | None = None
    try:
        before = path.lstat()
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(fd)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(opened, field) for field in fields):
            _fail(f"{label} changed while opening")
        payload = bytearray()
        while len(payload) <= maximum:
            block = os.read(fd, min(65536, maximum + 1 - len(payload)))
            if not block:
                break
            payload.extend(block)
        after = os.fstat(fd)
        if len(payload) != opened.st_size or len(payload) > maximum or any(
            getattr(opened, field) != getattr(after, field) for field in fields
        ):
            _fail(f"{label} changed while reading")
        return bytes(payload)
    except OSError as exc:
        raise EmergencySourceBootstrapError(f"{label} cannot be read") from exc
    finally:
        if fd is not None:
            os.close(fd)


def _parse_canonical(payload: bytes, *, label: str, maximum: int) -> dict[str, Any]:
    if not 1 <= len(payload) <= maximum:
        _fail(f"{label} size is invalid")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object, parse_constant=_reject_constant)
    except EmergencySourceBootstrapError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise EmergencySourceBootstrapError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict) or _canonical(value) + b"\n" != payload:
        _fail(f"{label} is not canonical JSON")
    return value


def _endpoint(value: object, region: object) -> tuple[str, str]:
    endpoint = _text(value, field="endpoint")
    region = _pattern(region, field="region", pattern=REGION_RE)
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise EmergencySourceBootstrapError("endpoint is malformed") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != f"s3.{region}.arvanstorage.ir"
        or port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        _fail("endpoint is not the exact Arvan S3 HTTPS endpoint")
    return endpoint.rstrip("/"), region


def _prefix(value: object) -> str:
    prefix = _text(value, field="prefix").strip("/")
    if not prefix or any(PREFIX_COMPONENT_RE.fullmatch(item) is None for item in prefix.split("/")):
        _fail("prefix is unsafe")
    return prefix


def _decode_public_key(payload: bytes, *, label: str) -> tuple[Any, bytes]:
    if Ed25519PublicKey is None or serialization is None:
        _fail("cryptography Ed25519 support is unavailable")
    try:
        raw = base64.b64decode(payload.decode("ascii").strip().encode("ascii"), validate=True)
    except (UnicodeDecodeError, UnicodeEncodeError, binascii.Error) as exc:
        raise EmergencySourceBootstrapError(f"{label} is not strict base64") from exc
    if len(raw) != 32:
        _fail(f"{label} has invalid length")
    try:
        return Ed25519PublicKey.from_public_bytes(raw), raw
    except ValueError as exc:
        raise EmergencySourceBootstrapError(f"{label} is invalid") from exc


def _signer_id(key: Any) -> str:
    if Ed25519PublicKey is None or serialization is None or not isinstance(key, Ed25519PublicKey):
        _fail("pinned signing public key is invalid")
    raw = key.public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    return "ed25519-sha256:" + hashlib.sha256(raw).hexdigest()


def _current_anchor_payload() -> tuple[Path, bytes]:
    """Read the root-owned anchor that is actually running, never a CLI path."""

    current = Path(__file__).resolve()
    if current != PINNED_ANCHOR_PATH:
        _fail("source bootstrap is not running from the fixed pinned anchor path")
    _safe_directory(PINNED_ANCHOR_PATH.parent, label="fixed source anchor directory", private=True)
    payload = _read_file(
        current,
        label="installed WebApp-FI Emergency source anchor",
        maximum=2 * 1024 * 1024,
        private=True,
    )
    return current, payload


def _require_trust_root() -> Path:
    return _safe_directory(TRUST_ROOT, label="WebApp-FI source trust root", private=True)


def _load_anchor_approval() -> dict[str, Any]:
    """Verify the independent, durable attestation for the installed anchor.

    This receipt is a root-only, canonical local trust input created by a
    human-approved preflight.  It is deliberately not supplied by the
    controller command, descriptor, URL map, or downloaded bootstrap.
    """

    _require_trust_root()
    payload = _read_file(
        PINNED_ANCHOR_APPROVAL,
        label="pinned WebApp-FI Emergency source anchor approval receipt",
        maximum=MAX_ANCHOR_APPROVAL_BYTES,
        private=True,
    )
    record = _parse_canonical(
        payload,
        label="pinned WebApp-FI Emergency source anchor approval receipt",
        maximum=MAX_ANCHOR_APPROVAL_BYTES,
    )
    fields = {
        "schema",
        "anchor_path",
        "endpoint",
        "region",
        "bucket",
        "prefix",
        "object_key",
        "artifact_version_id",
        "anchor_sha256",
        "anchor_bytes",
        "controller_revision",
        "controller_tree",
        "controller_tool_sha256",
        "controller_tool_bytes",
    }
    if set(record) != fields or record.get("schema") != ANCHOR_APPROVAL_SCHEMA:
        _fail("pinned WebApp-FI Emergency source anchor approval receipt is unsupported")
    anchor_path = _text(record.get("anchor_path"), field="approved anchor path", maximum=2048)
    if anchor_path != str(PINNED_ANCHOR_PATH):
        _fail("approved anchor path differs from the fixed anchor path")
    endpoint, region = _endpoint(record.get("endpoint"), record.get("region"))
    bucket = _pattern(record.get("bucket"), field="approved anchor bucket", pattern=BUCKET_RE)
    prefix = _prefix(record.get("prefix"))
    object_key = _text(record.get("object_key"), field="approved anchor object key", maximum=2048)
    approved = {
        "anchor_sha256": _pattern(record.get("anchor_sha256"), field="approved anchor SHA-256", pattern=SHA256_RE),
        "anchor_bytes": _positive(record.get("anchor_bytes"), field="approved anchor bytes", maximum=2 * 1024 * 1024),
        "artifact_version_id": _pattern(record.get("artifact_version_id"), field="approved anchor artifact VersionId", pattern=VERSION_ID_RE),
        "controller_revision": _pattern(record.get("controller_revision"), field="approved anchor controller revision", pattern=GIT_SHA_RE),
        "controller_tree": _pattern(record.get("controller_tree"), field="approved anchor controller tree", pattern=GIT_SHA_RE),
        "controller_tool_sha256": _pattern(record.get("controller_tool_sha256"), field="approved controller tool SHA-256", pattern=SHA256_RE),
        "controller_tool_bytes": _positive(record.get("controller_tool_bytes"), field="approved controller tool bytes", maximum=2 * 1024 * 1024),
        "endpoint": endpoint,
        "region": region,
        "bucket": bucket,
        "prefix": prefix,
        "object_key": object_key,
    }
    if object_key != _anchor_object_key(
        prefix=prefix,
        controller_revision=str(approved["controller_revision"]),
        anchor_sha256=str(approved["anchor_sha256"]),
    ):
        _fail("approved anchor object key is not deterministic")
    _current, installed = _current_anchor_payload()
    if (hashlib.sha256(installed).hexdigest(), len(installed)) != (
        approved["anchor_sha256"],
        approved["anchor_bytes"],
    ):
        _fail("installed WebApp-FI Emergency source anchor differs from its approved artifact provenance")
    return approved


def _load_signer_approval(*, anchor_approval: Mapping[str, Any]) -> str:
    payload = _read_file(
        PINNED_SIGNER_APPROVAL,
        label="pinned WebApp-FI Emergency source signer approval receipt",
        maximum=MAX_KEY_BYTES,
        private=True,
    )
    record = _parse_canonical(
        payload,
        label="pinned WebApp-FI Emergency source signer approval receipt",
        maximum=MAX_KEY_BYTES,
    )
    fields = {"schema", "anchor_sha256", "signer_key_id", "approval_scope"}
    if set(record) != fields or record.get("schema") != SIGNER_APPROVAL_SCHEMA:
        _fail("pinned WebApp-FI Emergency source signer approval receipt is unsupported")
    if record.get("approval_scope") != "webapp-fi-emergency-source-signing-key":
        _fail("pinned WebApp-FI Emergency source signer approval scope is unsupported")
    if _pattern(record.get("anchor_sha256"), field="approved signer anchor SHA-256", pattern=SHA256_RE) != anchor_approval["anchor_sha256"]:
        _fail("pinned signer approval is not bound to the approved anchor")
    return _pattern(record.get("signer_key_id"), field="approved signer key ID", pattern=SIGNER_KEY_ID_RE)


def _trust_record_payload(*, public_key: Any, raw_public_key: bytes, anchor_sha256: str) -> bytes:
    key_id = _signer_id(public_key)
    return _canonical(
        {
            "schema": TRUST_RECORD_SCHEMA,
            "anchor_sha256": anchor_sha256,
            "signer_key_id": key_id,
            "signing_public_key_base64": base64.b64encode(raw_public_key).decode("ascii"),
        }
    ) + b"\n"


def _load_pinned_signer(*, anchor_approval: Mapping[str, Any]) -> tuple[Any, str, bytes]:
    """Read the one fixed local signer record; normal receive has no key input."""

    payload = _read_file(
        PINNED_SIGNER_RECORD,
        label="pinned WebApp-FI Emergency source signer record",
        maximum=MAX_KEY_BYTES,
        private=True,
    )
    record = _parse_canonical(
        payload,
        label="pinned WebApp-FI Emergency source signer record",
        maximum=MAX_KEY_BYTES,
    )
    if set(record) != {"schema", "anchor_sha256", "signer_key_id", "signing_public_key_base64"}:
        _fail("pinned WebApp-FI Emergency source signer record fields are unsupported")
    if record.get("schema") != TRUST_RECORD_SCHEMA:
        _fail("pinned WebApp-FI Emergency source signer record schema is unsupported")
    encoded_key = _text(record.get("signing_public_key_base64"), field="pinned signing public key", maximum=MAX_KEY_BYTES)
    try:
        encoded_key_bytes = encoded_key.encode("ascii")
    except UnicodeEncodeError as exc:
        raise EmergencySourceBootstrapError("pinned signing public key is not ASCII") from exc
    public_key, raw_public_key = _decode_public_key(encoded_key_bytes, label="pinned signing public key")
    expected_key_id = _pattern(record.get("signer_key_id"), field="pinned signer key ID", pattern=SIGNER_KEY_ID_RE)
    if _pattern(record.get("anchor_sha256"), field="pinned signer anchor SHA-256", pattern=SHA256_RE) != anchor_approval["anchor_sha256"]:
        _fail("pinned signer record is not bound to the approved anchor")
    if _signer_id(public_key) != expected_key_id:
        _fail("pinned signing public key does not match its immutable signer key ID")
    return public_key, expected_key_id, base64.b64encode(raw_public_key) + b"\n"


def _provision_pinned_signer(*, anchor_approval: Mapping[str, Any]) -> dict[str, str]:
    """Create the fixed signer record exactly once from a local root-owned key.

    The candidate file is intentionally one fixed local-only path: a caller
    cannot redirect normal bootstrap/provisioning to a key of its choice.
    A human-approved preflight must put it there through a trusted console or
    previously verified root control path.  It is never fetched, relayed, or
    executed by this bootstrap.
    """

    _require_root()
    source_payload = _read_file(
        PINNED_SIGNER_CANDIDATE,
        label="local approved signer public key candidate",
        maximum=MAX_KEY_BYTES,
        private=False,
    )
    public_key, raw_public_key = _decode_public_key(source_payload, label="local signer public key to pin")
    approved_signer_id = _load_signer_approval(anchor_approval=anchor_approval)
    if _signer_id(public_key) != approved_signer_id:
        _fail("local signer public key does not match the independent approved signer fingerprint")
    trust_parent = _safe_directory(PINNED_SIGNER_RECORD.parent, label="pinned signer trust directory", private=True)
    if trust_parent != PINNED_SIGNER_RECORD.parent:
        _fail("pinned signer trust directory is invalid")
    if PINNED_SIGNER_RECORD.exists() or PINNED_SIGNER_RECORD.is_symlink():
        _fail("pinned WebApp-FI Emergency source signer record already exists")
    _write_new(
        PINNED_SIGNER_RECORD,
        _trust_record_payload(
            public_key=public_key,
            raw_public_key=raw_public_key,
            anchor_sha256=str(anchor_approval["anchor_sha256"]),
        ),
    )
    loaded, key_id, _encoded = _load_pinned_signer(anchor_approval=anchor_approval)
    if _signer_id(loaded) != key_id or key_id != _signer_id(public_key):
        _fail("pinned WebApp-FI Emergency source signer record could not be reverified")
    return {
        "status": "pinned-local-only",
        "trust_record": str(PINNED_SIGNER_RECORD),
        "signer_key_id": key_id,
        "object_storage_action": "not-performed",
        "network_action": "not-performed",
    }


def _bootstrap_key(*, prefix: str, campaign: str, controller_sha256: str) -> str:
    return "/".join((prefix, BOOTSTRAP_OBJECT_LAYOUT, campaign, controller_sha256 + ".tar.gz"))


def _anchor_object_key(*, prefix: str, controller_revision: str, anchor_sha256: str) -> str:
    return "/".join((prefix, ANCHOR_OBJECT_LAYOUT, controller_revision, anchor_sha256 + ".py"))


def _source_key(*, prefix: str, campaign: str, bundle_sha256: str) -> str:
    return "/".join(
        (
            prefix,
            OBJECT_LAYOUT,
            campaign,
            SOURCE_RELEASE_SHA,
            EMERGENCY_PATCH_SHA,
            bundle_sha256 + ".bundle.age",
        )
    )


def _validate_fixed_source(value: object, *, prefix: str, campaign: str, object_key: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "base_sha",
        "base_tree",
        "emergency_patch_sha",
        "emergency_patch_tree",
        "git_bundle_sha256",
        "git_bundle_bytes",
    }:
        _fail("signed source descriptor source fields are unsupported")
    source = {
        "base_sha": _pattern(value.get("base_sha"), field="source base SHA", pattern=GIT_SHA_RE),
        "base_tree": _pattern(value.get("base_tree"), field="source base tree", pattern=GIT_SHA_RE),
        "emergency_patch_sha": _pattern(value.get("emergency_patch_sha"), field="source Emergency patch SHA", pattern=GIT_SHA_RE),
        "emergency_patch_tree": _pattern(value.get("emergency_patch_tree"), field="source Emergency patch tree", pattern=GIT_SHA_RE),
        "git_bundle_sha256": _pattern(value.get("git_bundle_sha256"), field="source Git bundle SHA-256", pattern=SHA256_RE),
        "git_bundle_bytes": _positive(value.get("git_bundle_bytes"), field="source Git bundle bytes", maximum=512 * 1024 * 1024),
    }
    if (
        source["base_sha"],
        source["base_tree"],
        source["emergency_patch_sha"],
        source["emergency_patch_tree"],
    ) != (SOURCE_RELEASE_SHA, SOURCE_RELEASE_TREE, EMERGENCY_PATCH_SHA, EMERGENCY_PATCH_TREE):
        _fail("signed source descriptor does not identify the fixed approved Emergency source")
    if object_key != _source_key(prefix=prefix, campaign=campaign, bundle_sha256=str(source["git_bundle_sha256"])):
        _fail("signed source descriptor object key does not bind the fixed approved source")
    return source


def verify_descriptor(payload: bytes, *, public_key: Any) -> dict[str, Any]:
    descriptor = _parse_canonical(payload, label="signed source descriptor", maximum=MAX_DESCRIPTOR_BYTES)
    fields = {
        "schema", "campaign_id", "source_site", "destination_site", "endpoint", "region", "bucket", "prefix",
        "object_key", "version_id", "recipient_key_id", "source", "controller_tool", "receiver_bootstrap",
        "bootstrap", "ciphertext", "signature_algorithm", "signer_key_id", "signature_base64",
    }
    if set(descriptor) != fields or descriptor.get("schema") != DESCRIPTOR_SCHEMA:
        _fail("signed source descriptor fields are unsupported")
    if descriptor.get("source_site") != SOURCE_SITE or descriptor.get("destination_site") != DESTINATION_SITE:
        _fail("signed source descriptor route is unsupported")
    endpoint, region = _endpoint(descriptor.get("endpoint"), descriptor.get("region"))
    bucket = _pattern(descriptor.get("bucket"), field="bucket", pattern=BUCKET_RE)
    prefix = _prefix(descriptor.get("prefix"))
    campaign = _pattern(descriptor.get("campaign_id"), field="campaign_id", pattern=CAMPAIGN_RE)
    controller = descriptor.get("controller_tool")
    if not isinstance(controller, Mapping) or set(controller) != {"revision", "tree", "sha256", "bytes"}:
        _fail("controller tool provenance is invalid")
    controller_revision = _pattern(controller.get("revision"), field="controller revision", pattern=GIT_SHA_RE)
    controller_tree = _pattern(controller.get("tree"), field="controller tree", pattern=GIT_SHA_RE)
    controller_sha = _pattern(controller.get("sha256"), field="controller tool SHA-256", pattern=SHA256_RE)
    controller_bytes = _positive(controller.get("bytes"), field="controller tool bytes", maximum=2 * 1024 * 1024)
    receiver = descriptor.get("receiver_bootstrap")
    if not isinstance(receiver, Mapping) or set(receiver) != {"schema", "sha256", "bytes"}:
        _fail("receiver bootstrap provenance is invalid")
    if receiver.get("schema") != RECEIVER_BOOTSTRAP_SCHEMA or receiver.get("sha256") != controller_sha or receiver.get("bytes") != controller_bytes:
        _fail("receiver bootstrap provenance does not match controller tool provenance")
    bootstrap = descriptor.get("bootstrap")
    if not isinstance(bootstrap, Mapping) or set(bootstrap) != {"object_key", "version_id", "sha256", "bytes"}:
        _fail("bootstrap object descriptor is invalid")
    bootstrap_key = _text(bootstrap.get("object_key"), field="bootstrap object key", maximum=2048)
    if bootstrap_key != _bootstrap_key(prefix=prefix, campaign=campaign, controller_sha256=controller_sha):
        _fail("bootstrap object key is not deterministic")
    bootstrap_version = _pattern(bootstrap.get("version_id"), field="bootstrap VersionId", pattern=VERSION_ID_RE)
    bootstrap_sha = _pattern(bootstrap.get("sha256"), field="bootstrap SHA-256", pattern=SHA256_RE)
    bootstrap_bytes = _positive(bootstrap.get("bytes"), field="bootstrap bytes", maximum=MAX_BOOTSTRAP_BYTES)
    source_key = _text(descriptor.get("object_key"), field="source object key", maximum=2048)
    source_version = _pattern(descriptor.get("version_id"), field="source VersionId", pattern=VERSION_ID_RE)
    source_identity = _validate_fixed_source(
        descriptor.get("source"), prefix=prefix, campaign=campaign, object_key=source_key
    )
    recipient = _pattern(descriptor.get("recipient_key_id"), field="recipient key ID", pattern=RECIPIENT_KEY_ID_RE)
    ciphertext = descriptor.get("ciphertext")
    if not isinstance(ciphertext, Mapping) or set(ciphertext) != {"sha256", "bytes"}:
        _fail("source ciphertext descriptor is invalid")
    source_sha = _pattern(ciphertext.get("sha256"), field="source ciphertext SHA-256", pattern=SHA256_RE)
    source_bytes = _positive(ciphertext.get("bytes"), field="source ciphertext bytes", maximum=512 * 1024 * 1024 + 2 * 1024 * 1024)
    if descriptor.get("signature_algorithm") != SIGNATURE_ALGORITHM:
        _fail("signed source descriptor signature algorithm is unsupported")
    signer_id = _pattern(descriptor.get("signer_key_id"), field="signer key ID", pattern=SIGNER_KEY_ID_RE)
    if signer_id != _signer_id(public_key):
        _fail("signed source descriptor signer does not match the pinned public key")
    try:
        signature = base64.b64decode(_text(descriptor.get("signature_base64"), field="signature").encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise EmergencySourceBootstrapError("signed source descriptor signature is invalid") from exc
    if len(signature) != 64:
        _fail("signed source descriptor signature length is invalid")
    unsigned = {key: descriptor[key] for key in fields if key not in {"signature_algorithm", "signer_key_id", "signature_base64"}}
    try:
        public_key.verify(signature, SIGNATURE_DOMAIN + _canonical(unsigned))
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise EmergencySourceBootstrapError("signed source descriptor signature verification failed") from exc
    return {
        "descriptor_sha256": hashlib.sha256(_canonical(descriptor)).hexdigest(),
        "campaign_id": campaign,
        "endpoint": endpoint,
        "bucket": bucket,
        "bootstrap": {"object_key": bootstrap_key, "version_id": bootstrap_version, "sha256": bootstrap_sha, "bytes": bootstrap_bytes},
        "source": {
            "object_key": source_key,
            "version_id": source_version,
            "sha256": source_sha,
            "bytes": source_bytes,
            "identity": source_identity,
        },
        "receiver": {"sha256": controller_sha, "bytes": controller_bytes},
        "controller": {"revision": controller_revision, "tree": controller_tree},
        "recipient_key_id": recipient,
    }


def _require_descriptor_control_context(*, descriptor: Mapping[str, Any], anchor_approval: Mapping[str, Any]) -> None:
    """Reject a valid signature that names a foreign receiver/controller.

    The signing key alone authorizes neither arbitrary controller revisions nor
    arbitrary Python receiver bytes.  Both are frozen in the root-only anchor
    approval record that also attests the installed anchor itself.  This check
    intentionally runs before URL parsing, campaign mutation, bootstrap
    download, or receiver execution.
    """

    controller = descriptor.get("controller")
    receiver = descriptor.get("receiver")
    if not isinstance(controller, Mapping) or not isinstance(receiver, Mapping):
        _fail("verified source descriptor control provenance is malformed")
    observed = (
        _pattern(controller.get("revision"), field="descriptor controller revision", pattern=GIT_SHA_RE),
        _pattern(controller.get("tree"), field="descriptor controller tree", pattern=GIT_SHA_RE),
        _pattern(receiver.get("sha256"), field="descriptor receiver SHA-256", pattern=SHA256_RE),
        _positive(receiver.get("bytes"), field="descriptor receiver bytes", maximum=2 * 1024 * 1024),
    )
    expected = (
        _pattern(anchor_approval.get("controller_revision"), field="approved controller revision", pattern=GIT_SHA_RE),
        _pattern(anchor_approval.get("controller_tree"), field="approved controller tree", pattern=GIT_SHA_RE),
        _pattern(anchor_approval.get("controller_tool_sha256"), field="approved controller tool SHA-256", pattern=SHA256_RE),
        _positive(anchor_approval.get("controller_tool_bytes"), field="approved controller tool bytes", maximum=2 * 1024 * 1024),
    )
    if observed != expected:
        _fail("signed source descriptor controller/receiver provenance does not match the approved anchor control context")


def _validate_url(*, url: str, endpoint: str, bucket: str, object_key: str, version_id: str) -> str:
    if not isinstance(url, str) or not url or len(url.encode("utf-8")) > MAX_URL_BYTES:
        _fail("version-bound Object Storage URL is invalid")
    try:
        parsed = urlsplit(url)
        endpoint_parts = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise EmergencySourceBootstrapError("version-bound Object Storage URL is malformed") from exc
    host = endpoint_parts.hostname
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {host, f"{bucket}.{host}"}
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        _fail("version-bound Object Storage URL endpoint is not allowlisted")
    object_path = quote(object_key, safe="/")
    expected = "/" + object_path if parsed.hostname == f"{bucket}.{host}" else "/" + quote(bucket, safe="") + "/" + object_path
    if parsed.path != expected:
        _fail("version-bound Object Storage URL selects a different object")
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise EmergencySourceBootstrapError("version-bound Object Storage URL query is malformed") from exc
    if set(query) - (_PRESIGNED_REQUIRED | _PRESIGNED_OPTIONAL) or not _PRESIGNED_REQUIRED.issubset(query) or any(len(items) != 1 for items in query.values()):
        _fail("version-bound Object Storage URL query is unsupported")
    if query["X-Amz-Algorithm"][0] != "AWS4-HMAC-SHA256" or query["X-Amz-SignedHeaders"][0] != "host" or query["versionId"][0] != version_id:
        _fail("version-bound Object Storage URL signature binding is invalid")
    try:
        ttl = int(query["X-Amz-Expires"][0], 10)
    except ValueError as exc:
        raise EmergencySourceBootstrapError("version-bound Object Storage URL expiry is invalid") from exc
    if not MIN_PRESIGNED_TTL_SECONDS <= ttl <= MAX_PRESIGNED_TTL_SECONDS:
        _fail("version-bound Object Storage URL expiry is outside its bound")
    return url


def _load_url_map(payload: bytes, *, descriptor: Mapping[str, Any]) -> dict[str, str]:
    if not 1 <= len(payload) <= MAX_URL_MAP_BYTES:
        _fail("transient URL map size is invalid")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object, parse_constant=_reject_constant)
    except EmergencySourceBootstrapError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise EmergencySourceBootstrapError("transient URL map is not strict JSON") from exc
    if not isinstance(value, Mapping) or set(value) != {"schema", "descriptor_sha256", "bootstrap_url", "source_url"}:
        _fail("transient URL map fields are unsupported")
    if value.get("schema") != URL_MAP_SCHEMA or value.get("descriptor_sha256") != descriptor["descriptor_sha256"]:
        _fail("transient URL map is not bound to the signed descriptor")
    return {
        "bootstrap_url": _validate_url(
            url=_text(value.get("bootstrap_url"), field="bootstrap URL", maximum=MAX_URL_BYTES),
            endpoint=str(descriptor["endpoint"]),
            bucket=str(descriptor["bucket"]),
            object_key=str(descriptor["bootstrap"]["object_key"]),
            version_id=str(descriptor["bootstrap"]["version_id"]),
        ),
        "source_url": _validate_url(
            url=_text(value.get("source_url"), field="source URL", maximum=MAX_URL_BYTES),
            endpoint=str(descriptor["endpoint"]),
            bucket=str(descriptor["bucket"]),
            object_key=str(descriptor["source"]["object_key"]),
            version_id=str(descriptor["source"]["version_id"]),
        ),
    }


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        raise EmergencySourceBootstrapError("Object Storage bootstrap download was redirected")


def _fresh_private_directory(parent: Path, name: str) -> Path:
    parent = _safe_directory(parent, label="receiver bootstrap root", private=True)
    if not name or name in {".", ".."} or "/" in name:
        _fail("receiver bootstrap candidate name is invalid")
    path = parent / name
    if path.exists() or path.is_symlink():
        _fail(
            "receiver bootstrap candidate already exists; do not reuse or delete it—"
            "recover only with a new campaign ID and a fresh age identity"
        )
    try:
        path.mkdir(mode=0o700)
        os.chmod(path, 0o700)
    except OSError as exc:
        raise EmergencySourceBootstrapError("receiver bootstrap candidate cannot be created") from exc
    _safe_directory(path, label="receiver bootstrap candidate", private=True)
    _fsync_directory(parent, label="receiver bootstrap root")
    _fsync_directory(path, label="receiver bootstrap candidate")
    return path


def _ensure_fixed_receiver_root() -> Path:
    """Create only the fixed volatile `/run` child, never a caller path."""

    if DEFAULT_RECEIVER_ROOT.parent != Path("/run"):
        _fail("fixed receiver bootstrap root is outside /run")
    parent = _safe_directory(Path("/run"), label="runtime directory", private=False)
    root = DEFAULT_RECEIVER_ROOT
    try:
        state = root.lstat()
    except FileNotFoundError:
        try:
            root.mkdir(mode=0o700)
            os.chmod(root, 0o700)
            state = root.lstat()
            _fsync_directory(parent, label="runtime directory")
            _fsync_directory(root, label="receiver bootstrap root")
        except OSError as exc:
            raise EmergencySourceBootstrapError("fixed receiver bootstrap root cannot be created") from exc
    except OSError as exc:
        raise EmergencySourceBootstrapError("fixed receiver bootstrap root cannot be inspected") from exc
    if (
        stat.S_ISLNK(state.st_mode)
        or not stat.S_ISDIR(state.st_mode)
        or state.st_uid != 0
        or stat.S_IMODE(state.st_mode) != 0o700
    ):
        _fail("fixed receiver bootstrap root is not root-only 0700")
    return _safe_directory(root, label="receiver bootstrap root", private=True)


def _safe_executable(path: Path, *, label: str) -> Path:
    path = _safe_file(path, label=label, maximum=128 * 1024 * 1024, private=False)
    try:
        state = path.lstat()
    except OSError as exc:
        raise EmergencySourceBootstrapError(f"{label} cannot be inspected") from exc
    if not stat.S_IMODE(state.st_mode) & 0o100:
        _fail(f"{label} is not executable by root")
    return path


def _campaign_identity_path(campaign_id: str) -> Path:
    campaign = _pattern(campaign_id, field="campaign ID", pattern=CAMPAIGN_RE)
    path = FI_CAMPAIGN_IDENTITY_ROOT / campaign / FI_EMERGENCY_SOURCE_IDENTITY_LEAF
    if not path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts[1:]):
        _fail("fixed WebApp-FI campaign identity path is invalid")
    return path


def _campaign_receive_lock_path(campaign_id: str) -> Path:
    """Return the fixed durable descriptor-binding receipt for one campaign."""

    campaign = _pattern(campaign_id, field="campaign ID", pattern=CAMPAIGN_RE)
    identity = _campaign_identity_path(campaign)
    path = FI_CAMPAIGN_IDENTITY_ROOT / campaign / FI_EMERGENCY_SOURCE_RECEIVE_LOCK_LEAF
    if (
        not path.is_absolute()
        or path.parent != identity.parent
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        _fail("fixed WebApp-FI campaign receive lock path is invalid")
    return path


def _campaign_receive_lock_value(descriptor: Mapping[str, Any]) -> dict[str, object]:
    """Project a verified descriptor into the durable campaign binding."""

    if not isinstance(descriptor, Mapping):
        _fail("verified source descriptor is invalid for campaign binding")
    source = descriptor.get("source")
    bootstrap = descriptor.get("bootstrap")
    controller = descriptor.get("controller")
    receiver = descriptor.get("receiver")
    if not all(isinstance(value, Mapping) for value in (source, bootstrap, controller, receiver)):
        _fail("verified source descriptor cannot be bound to a campaign")
    assert isinstance(source, Mapping) and isinstance(bootstrap, Mapping)
    assert isinstance(controller, Mapping) and isinstance(receiver, Mapping)
    source_identity = source.get("identity")
    if not isinstance(source_identity, Mapping):
        _fail("verified source descriptor has no fixed source identity")
    campaign_id = _pattern(descriptor.get("campaign_id"), field="campaign lock campaign ID", pattern=CAMPAIGN_RE)
    return {
        "schema": CAMPAIGN_RECEIVE_LOCK_SCHEMA,
        "campaign_id": campaign_id,
        "descriptor_sha256": _pattern(descriptor.get("descriptor_sha256"), field="campaign lock descriptor SHA-256", pattern=SHA256_RE),
        "endpoint": _text(descriptor.get("endpoint"), field="campaign lock endpoint", maximum=2048),
        "bucket": _pattern(descriptor.get("bucket"), field="campaign lock bucket", pattern=BUCKET_RE),
        "recipient_key_id": _pattern(descriptor.get("recipient_key_id"), field="campaign lock recipient key ID", pattern=RECIPIENT_KEY_ID_RE),
        "source_object_key": _text(source.get("object_key"), field="campaign lock source object key", maximum=2048),
        "source_version_id": _pattern(source.get("version_id"), field="campaign lock source VersionId", pattern=VERSION_ID_RE),
        "source_ciphertext_sha256": _pattern(source.get("sha256"), field="campaign lock source ciphertext SHA-256", pattern=SHA256_RE),
        "source_ciphertext_bytes": _positive(source.get("bytes"), field="campaign lock source ciphertext bytes", maximum=512 * 1024 * 1024 + 2 * 1024 * 1024),
        "source_base_sha": _pattern(source_identity.get("base_sha"), field="campaign lock source base SHA", pattern=GIT_SHA_RE),
        "source_base_tree": _pattern(source_identity.get("base_tree"), field="campaign lock source base tree", pattern=GIT_SHA_RE),
        "source_emergency_patch_sha": _pattern(source_identity.get("emergency_patch_sha"), field="campaign lock source Emergency patch SHA", pattern=GIT_SHA_RE),
        "source_emergency_patch_tree": _pattern(source_identity.get("emergency_patch_tree"), field="campaign lock source Emergency patch tree", pattern=GIT_SHA_RE),
        "source_git_bundle_sha256": _pattern(source_identity.get("git_bundle_sha256"), field="campaign lock Git bundle SHA-256", pattern=SHA256_RE),
        "source_git_bundle_bytes": _positive(source_identity.get("git_bundle_bytes"), field="campaign lock Git bundle bytes", maximum=512 * 1024 * 1024),
        "bootstrap_object_key": _text(bootstrap.get("object_key"), field="campaign lock bootstrap object key", maximum=2048),
        "bootstrap_version_id": _pattern(bootstrap.get("version_id"), field="campaign lock bootstrap VersionId", pattern=VERSION_ID_RE),
        "bootstrap_sha256": _pattern(bootstrap.get("sha256"), field="campaign lock bootstrap SHA-256", pattern=SHA256_RE),
        "bootstrap_bytes": _positive(bootstrap.get("bytes"), field="campaign lock bootstrap bytes", maximum=MAX_BOOTSTRAP_BYTES),
        "controller_revision": _pattern(controller.get("revision"), field="campaign lock controller revision", pattern=GIT_SHA_RE),
        "controller_tree": _pattern(controller.get("tree"), field="campaign lock controller tree", pattern=GIT_SHA_RE),
        "receiver_sha256": _pattern(receiver.get("sha256"), field="campaign lock receiver SHA-256", pattern=SHA256_RE),
        "receiver_bytes": _positive(receiver.get("bytes"), field="campaign lock receiver bytes", maximum=2 * 1024 * 1024),
    }


def _write_campaign_receive_lock_create_only(path: Path, payload: bytes) -> bool:
    """Durably create the campaign lock, or report an existing entry.

    Unlike a rename-based update, this never replaces a receipt. The caller
    rereads either outcome and accepts only byte-for-byte canonical equality
    with its already verified descriptor.
    """

    path = _absolute(path, field="campaign receive lock")
    _safe_directory(path.parent, label="campaign receive lock parent", private=True)
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
    except FileExistsError:
        return False
    except OSError as exc:
        raise EmergencySourceBootstrapError("campaign receive lock cannot be created") from exc
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short campaign receive lock write")
            view = view[written:]
        os.fsync(descriptor)
    except OSError as exc:
        raise EmergencySourceBootstrapError("campaign receive lock cannot be durably written") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    _safe_file(
        path,
        label="durable WebApp-FI source campaign receive lock",
        maximum=MAX_CAMPAIGN_RECEIVE_LOCK_BYTES,
        private=True,
    )
    _fsync_directory(path.parent, label="campaign receive lock parent")
    return True


def _bind_persistent_campaign_descriptor(descriptor: Mapping[str, Any]) -> dict[str, str]:
    """Create or exactly reuse the one reboot-safe descriptor binding.

    The lock is intentionally outside ``/run``. Thus a reboot may make an
    exact-descriptor retry possible once the volatile candidate is gone, but
    it can never silently repurpose the campaign to a different descriptor.
    """

    expected = _campaign_receive_lock_value(descriptor)
    lock_path = _campaign_receive_lock_path(str(expected["campaign_id"]))
    _safe_directory(lock_path.parent, label="campaign receive lock parent", private=True)
    created = _write_campaign_receive_lock_create_only(lock_path, _canonical(expected) + b"\n")
    payload = _read_file(
        lock_path,
        label="durable WebApp-FI source campaign receive lock",
        maximum=MAX_CAMPAIGN_RECEIVE_LOCK_BYTES,
        private=True,
    )
    observed = _parse_canonical(
        payload,
        label="durable WebApp-FI source campaign receive lock",
        maximum=MAX_CAMPAIGN_RECEIVE_LOCK_BYTES,
    )
    if observed != expected:
        _fail("campaign is already locked to a different signed descriptor; recover only with a new campaign ID")
    return {
        "path": str(lock_path),
        "status": "created" if created else "reused-exact-descriptor",
    }


def _preflight_receive_destination(*, descriptor: Mapping[str, Any], destination: Path) -> Path:
    """Fail before locking/downloading when a receive target cannot succeed."""

    destination = _absolute(destination, field="WebApp-FI Emergency source destination")
    parent = _safe_directory(destination.parent, label="WebApp-FI Emergency source destination parent", private=True)
    if destination.exists() or destination.is_symlink():
        _fail("WebApp-FI Emergency source destination must be fresh; recover only with a new campaign ID")
    source = descriptor.get("source") if isinstance(descriptor, Mapping) else None
    identity = source.get("identity") if isinstance(source, Mapping) else None
    if not isinstance(source, Mapping) or not isinstance(identity, Mapping):
        _fail("verified source descriptor has no source storage budget")
    ciphertext_bytes = _positive(
        source.get("bytes"),
        field="source ciphertext bytes for destination preflight",
        maximum=512 * 1024 * 1024 + 2 * 1024 * 1024,
    )
    bundle_bytes = _positive(
        identity.get("git_bundle_bytes"),
        field="source Git bundle bytes for destination preflight",
        maximum=512 * 1024 * 1024,
    )
    _safe_executable(AGE_BINARY, label="age binary")
    if shutil.disk_usage(parent).free < ciphertext_bytes + bundle_bytes + SOURCE_RECEIVE_DISK_HEADROOM_BYTES:
        _fail("insufficient disk space for the sealed source transfer")
    return destination


def _ensure_private_child(parent: Path, name: str, *, label: str) -> Path:
    if not name or name in {".", ".."} or "/" in name:
        _fail(f"{label} child name is invalid")
    parent = _safe_directory(parent, label=f"{label} parent", private=True)
    child = parent / name
    created = False
    try:
        state = child.lstat()
    except FileNotFoundError:
        try:
            child.mkdir(mode=0o700)
            os.chmod(child, 0o700)
            state = child.lstat()
            created = True
        except OSError as exc:
            raise EmergencySourceBootstrapError(f"{label} cannot be created") from exc
    except OSError as exc:
        raise EmergencySourceBootstrapError(f"{label} cannot be inspected") from exc
    if (
        stat.S_ISLNK(state.st_mode)
        or not stat.S_ISDIR(state.st_mode)
        or state.st_uid != 0
        or stat.S_IMODE(state.st_mode) != 0o700
    ):
        _fail(f"{label} is not a root-only 0700 directory")
    if created:
        _fsync_directory(parent, label=f"{label} parent")
        _fsync_directory(child, label=label)
    return child


def _derive_age_recipient(*, identity: Path) -> str:
    identity = _safe_file(identity, label="WebApp-FI Emergency source age identity", maximum=256 * 1024, private=True)
    age_keygen = _safe_executable(AGE_KEYGEN_BINARY, label="age-keygen binary")
    try:
        completed = subprocess.run(
            [str(age_keygen), "-y", str(identity)],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            env=_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencySourceBootstrapError("campaign age public recipient derivation could not start") from exc
    if completed.returncode != 0:
        _fail("campaign age public recipient derivation failed")
    return _pattern(completed.stdout.strip(), field="derived campaign age recipient", pattern=AGE_RECIPIENT_RE)


def bootstrap_campaign_identity(*, campaign_id: str) -> dict[str, str]:
    """Create one campaign-local age identity before any receiver download.

    The campaign root is deliberately an explicit precondition provided by
    the root-only tmpfiles/installer contract.  This function may create only
    the campaign and WebApp-FI children below that root, and it never emits
    private key bytes.
    """

    _require_root()
    identity = _campaign_identity_path(campaign_id)
    root = _ensure_campaign_identity_root()
    campaign = _ensure_private_child(root, _pattern(campaign_id, field="campaign ID", pattern=CAMPAIGN_RE), label="campaign identity directory")
    webapp_fi = _ensure_private_child(campaign, "webapp-fi", label="campaign WebApp-FI identity directory")
    if identity.parent != webapp_fi:
        _fail("fixed WebApp-FI campaign identity parent is inconsistent")
    if identity.exists() or identity.is_symlink():
        _fail(
            "fixed WebApp-FI campaign source identity already exists; do not reuse or replace it—"
            "recover only with a new campaign ID"
        )
    age_keygen = _safe_executable(AGE_KEYGEN_BINARY, label="age-keygen binary")
    temporary = webapp_fi / ("." + identity.name + "." + secrets.token_hex(16) + ".new")
    try:
        completed = subprocess.run(
            [str(age_keygen), "-o", str(temporary)],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            env=_environment(),
            preexec_fn=lambda: os.umask(0o077),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencySourceBootstrapError("campaign age identity generation could not start") from exc
    if completed.returncode != 0:
        _fail("campaign age identity generation failed")
    try:
        os.chmod(temporary, 0o600)
        _safe_file(temporary, label="new WebApp-FI campaign source identity", maximum=256 * 1024, private=True)
        # `link` is create-only: unlike rename it cannot replace an identity
        # that appeared between the earlier check and publication.
        os.link(temporary, identity, follow_symlinks=False)
        temporary.unlink()
        _safe_file(identity, label="published WebApp-FI campaign source identity", maximum=256 * 1024, private=True)
        _fsync_directory(webapp_fi, label="campaign WebApp-FI identity directory")
    except FileExistsError as exc:
        raise EmergencySourceBootstrapError(
            "fixed WebApp-FI campaign source identity already exists; recover only with a new campaign ID"
        ) from exc
    except OSError as exc:
        raise EmergencySourceBootstrapError("campaign age identity cannot be published create-only") from exc
    recipient = _derive_age_recipient(identity=identity)
    return {
        "status": "bootstrapped-local-only",
        "campaign_id": _pattern(campaign_id, field="campaign ID", pattern=CAMPAIGN_RE),
        "identity_path": str(identity),
        "age_recipient": recipient,
        "recipient_key_id": "age-recipient-sha256:" + hashlib.sha256(recipient.encode("ascii")).hexdigest(),
        "object_storage_action": "not-performed",
        "network_action": "not-performed",
        "private_key_material": "not-emitted",
    }


def _download_bootstrap(*, url: str, expected_sha256: str, expected_bytes: int, output: Path) -> None:
    _require_direct_object_storage_environment()
    output = _absolute(output, field="receiver bootstrap download")
    _safe_directory(output.parent, label="receiver bootstrap download parent", private=True)
    if output.exists() or output.is_symlink():
        _fail("receiver bootstrap download target already exists")
    if shutil.disk_usage(output.parent).free < expected_bytes + DISK_HEADROOM_BYTES:
        _fail("insufficient disk space for receiver bootstrap")
    fd: int | None = None
    try:
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
        opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
        request = Request(url, headers={"User-Agent": "gold-trade-webapp-fi-source-bootstrap/1"}, method="GET")
        digest = hashlib.sha256()
        size = 0
        try:
            with opener.open(request, timeout=180) as response:
                if getattr(response, "status", 200) != 200 or response.geturl() != url:
                    _fail("Object Storage bootstrap response differs from request")
                while True:
                    block = response.read(DOWNLOAD_CHUNK_BYTES)
                    if not block:
                        break
                    if not isinstance(block, bytes) or size + len(block) > expected_bytes:
                        _fail("receiver bootstrap exceeds its descriptor bound")
                    view = memoryview(block)
                    while view:
                        written = os.write(fd, view)
                        if written <= 0:
                            raise OSError("short bootstrap write")
                        view = view[written:]
                    size += len(block)
                    digest.update(block)
        except (HTTPError, URLError, OSError, ssl.SSLError) as exc:
            raise EmergencySourceBootstrapError("Object Storage bootstrap download failed") from exc
        os.fsync(fd)
        if (digest.hexdigest(), size) != (expected_sha256, expected_bytes):
            _fail("receiver bootstrap differs from its signed descriptor")
    except Exception:
        with contextlib.suppress(OSError):
            output.unlink()
        raise
    finally:
        if fd is not None:
            os.close(fd)


def _write_new(path: Path, payload: bytes) -> None:
    path = _absolute(path, field="new bootstrap file")
    _safe_directory(path.parent, label="new bootstrap file parent", private=True)
    if path.exists() or path.is_symlink():
        _fail("receiver bootstrap extraction target already exists")
    fd: int | None = None
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short bootstrap extraction write")
            view = view[written:]
        os.fsync(fd)
    except (FileExistsError, OSError) as exc:
        raise EmergencySourceBootstrapError("receiver bootstrap extraction cannot create a new file") from exc
    finally:
        if fd is not None:
            os.close(fd)
    _safe_file(path, label="new bootstrap file", maximum=max(1, len(payload)), private=True)
    _fsync_directory(path.parent, label="new bootstrap file parent")


def _extract_verified_receiver(*, artifact: Path, candidate: Path, receiver_sha256: str, receiver_bytes: int) -> Path:
    payload = _read_file(artifact, label="downloaded receiver bootstrap", maximum=MAX_BOOTSTRAP_BYTES, private=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            members = archive.getmembers()
            if {member.name for member in members} != {"RECEIVER.json", "receiver.py"} or len(members) != 2:
                _fail("receiver bootstrap archive layout is invalid")
            values: dict[str, bytes] = {}
            for member in members:
                if not member.isreg() or member.issym() or member.islnk() or not 1 <= member.size <= 2 * 1024 * 1024:
                    _fail("receiver bootstrap archive member is unsafe")
                handle = archive.extractfile(member)
                if handle is None:
                    _fail("receiver bootstrap archive member cannot be read")
                values[member.name] = handle.read()
    except EmergencySourceBootstrapError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise EmergencySourceBootstrapError("receiver bootstrap archive is invalid") from exc
    receiver = values.get("receiver.py", b"")
    if (hashlib.sha256(receiver).hexdigest(), len(receiver)) != (receiver_sha256, receiver_bytes):
        _fail("downloaded receiver bytes differ from signed bootstrap provenance")
    manifest = _parse_canonical(values.get("RECEIVER.json", b""), label="receiver bootstrap manifest", maximum=64 * 1024)
    if manifest != {"schema": RECEIVER_BOOTSTRAP_SCHEMA, "receiver_sha256": receiver_sha256, "receiver_bytes": receiver_bytes}:
        _fail("receiver bootstrap manifest differs from signed provenance")
    receiver_path = candidate / "receiver.py"
    _write_new(receiver_path, receiver)
    try:
        os.chmod(receiver_path, 0o600)
    except OSError as exc:
        raise EmergencySourceBootstrapError("verified receiver mode cannot be restricted") from exc
    _safe_file(receiver_path, label="verified receiver", maximum=2 * 1024 * 1024, private=True)
    return receiver_path


def receive_from_transient_url_map(
    *,
    descriptor_payload: bytes,
    destination: Path,
    url_map_payload: bytes,
) -> dict[str, Any]:
    """Run the whole anchor receive boundary after CLI authorization.

    This deliberately keeps the verified descriptor in a new private
    candidate directory before the downloaded receiver ever starts.  The
    receiver gets only that sealed copy and a freshly materialized copy of
    the already-pinned public key, never the caller-selected descriptor/key
    pathname.  Keeping the boundary as a function also makes it possible to
    exercise the real order in adversarial tests without launching a service
    or contacting Object Storage.
    """

    _require_direct_object_storage_environment()
    anchor_approval = _load_anchor_approval()
    public_key, signer_key_id, encoded_public_key = _load_pinned_signer(anchor_approval=anchor_approval)
    if not isinstance(descriptor_payload, bytes):
        _fail("verified signed source descriptor bytes are invalid")
    descriptor = verify_descriptor(descriptor_payload, public_key=public_key)
    _require_descriptor_control_context(descriptor=descriptor, anchor_approval=anchor_approval)
    identity = _campaign_identity_path(str(descriptor["campaign_id"]))
    local_recipient = _derive_age_recipient(identity=identity)
    local_recipient_key_id = "age-recipient-sha256:" + hashlib.sha256(local_recipient.encode("ascii")).hexdigest()
    if local_recipient_key_id != descriptor["recipient_key_id"]:
        _fail("fixed WebApp-FI campaign identity does not match the signed recipient pin")
    destination = _preflight_receive_destination(descriptor=descriptor, destination=destination)
    urls = _load_url_map(url_map_payload, descriptor=descriptor)
    campaign_lock = _bind_persistent_campaign_descriptor(descriptor)
    root = _ensure_fixed_receiver_root()
    candidate = _fresh_private_directory(
        root,
        str(descriptor["campaign_id"])
        + "-"
        + hashlib.sha256(str(descriptor["descriptor_sha256"]).encode("ascii")).hexdigest()[:16],
    )
    sealed_descriptor = candidate / "sealed-descriptor.json"
    _write_new(sealed_descriptor, descriptor_payload)
    sealed_payload = _read_file(
        sealed_descriptor,
        label="copied signed source descriptor",
        maximum=MAX_DESCRIPTOR_BYTES,
        private=True,
    )
    sealed_value = _parse_canonical(
        sealed_payload,
        label="copied signed source descriptor",
        maximum=MAX_DESCRIPTOR_BYTES,
    )
    if hashlib.sha256(_canonical(sealed_value)).hexdigest() != descriptor["descriptor_sha256"]:
        _fail("copied signed source descriptor differs from the verified descriptor")
    pinned_public_key = candidate / "signing-public.key"
    _write_new(pinned_public_key, encoded_public_key)
    _safe_file(
        pinned_public_key,
        label="materialized pinned signing public key",
        maximum=MAX_KEY_BYTES,
        private=True,
    )
    artifact = candidate / "receiver-bootstrap.tar.gz"
    _download_bootstrap(
        url=urls["bootstrap_url"],
        expected_sha256=str(descriptor["bootstrap"]["sha256"]),
        expected_bytes=int(descriptor["bootstrap"]["bytes"]),
        output=artifact,
    )
    receiver = _extract_verified_receiver(
        artifact=artifact,
        candidate=candidate,
        receiver_sha256=str(descriptor["receiver"]["sha256"]),
        receiver_bytes=int(descriptor["receiver"]["bytes"]),
    )
    _run_receiver(
        receiver=receiver,
        signing_public_key=pinned_public_key,
        descriptor=sealed_descriptor,
        identity=identity,
        destination=destination,
        campaign_id=str(descriptor["campaign_id"]),
        source_url=urls["source_url"],
    )
    return {
        "status": "received-through-verified-anchor",
        "campaign_id": descriptor["campaign_id"],
        "descriptor_sha256": descriptor["descriptor_sha256"],
        "pinned_signer_key_id": signer_key_id,
        "campaign_lock": campaign_lock["status"],
        "receiver_execution": "verified-bootstrap-only",
        "s3_credentials": "not-accepted-on-webapp-fi",
    }


def _run_receiver(
    *,
    receiver: Path,
    signing_public_key: Path,
    descriptor: Path,
    identity: Path,
    destination: Path,
    campaign_id: str,
    source_url: str,
) -> int:
    try:
        completed = subprocess.run(
            [
                "/usr/bin/python3", "-I", "-B", str(receiver), "receive",
                "--descriptor", str(descriptor),
                "--signing-public-key", str(signing_public_key),
                "--age-identity", str(identity),
                "--age-binary", str(AGE_BINARY),
                "--age-keygen-binary", str(AGE_KEYGEN_BINARY),
                "--destination", str(destination),
                "--apply", "--confirm", campaign_id,
            ],
            input=source_url + "\n",
            text=True,
            stdin=subprocess.PIPE,
            stdout=None,
            stderr=None,
            check=False,
            timeout=3600,
            env=_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencySourceBootstrapError("verified receiver could not start") from exc
    if completed.returncode != 0:
        _fail("verified receiver rejected the Emergency source transfer")
    return completed.returncode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    receive = actions.add_parser("receive", help="verify with the fixed local trust record and receive one source checkout")
    receive.add_argument("--descriptor", type=Path, required=True)
    receive.add_argument("--destination", type=Path, required=True)
    receive.add_argument("--apply", action="store_true")
    receive.add_argument("--confirm")
    identity = actions.add_parser(
        "bootstrap-identity",
        help="create only the fixed campaign-local WA-FI age identity and emit its non-secret recipient",
    )
    identity.add_argument("--campaign-id", required=True)
    identity.add_argument("--apply", action="store_true")
    identity.add_argument("--confirm")
    provision = actions.add_parser(
        "provision-pinned-signer",
        help="create the fixed signer record from the independently approved fixed local candidate",
    )
    provision.add_argument("--apply", action="store_true")
    provision.add_argument("--confirm")
    actions.add_parser(
        "verify-installed-anchor",
        help="verify the fixed anchor against its independent root-only approval receipt",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        if not sys.flags.isolated or not sys.flags.dont_write_bytecode:
            _fail("source bootstrap must be launched with python3 -I -B")
        _require_root()
        args = _parser().parse_args(argv)
        anchor_approval = _load_anchor_approval()
        if args.action == "verify-installed-anchor":
            print(json.dumps({
                "status": "verified-local-only",
                "anchor_path": str(PINNED_ANCHOR_PATH),
                **anchor_approval,
                "network_action": False,
                "object_storage_action": False,
            }, sort_keys=True))
            return 0
        if args.action == "provision-pinned-signer":
            approved_signer_id = _load_signer_approval(anchor_approval=anchor_approval)
            if not args.apply:
                print(json.dumps({
                    "status": "planned-non-authorizing",
                    "trust_record": str(PINNED_SIGNER_RECORD),
                    "signer_key_id": approved_signer_id,
                    "candidate_path": str(PINNED_SIGNER_CANDIDATE),
                    "local_write_action": False,
                    "object_storage_action": False,
                    "network_action": False,
                }, sort_keys=True))
                return 0
            expected_confirmation = "pin-webapp-fi-emergency-source-signer:" + approved_signer_id
            if args.confirm != expected_confirmation:
                _fail("--confirm must exactly equal the derived pinned signer confirmation")
            print(json.dumps(_provision_pinned_signer(anchor_approval=anchor_approval), sort_keys=True))
            return 0
        if args.action == "bootstrap-identity":
            campaign_id = _pattern(args.campaign_id, field="campaign ID", pattern=CAMPAIGN_RE)
            if not args.apply:
                print(json.dumps({
                    "status": "planned-non-authorizing",
                    "campaign_id": campaign_id,
                    "identity_path": str(_campaign_identity_path(campaign_id)),
                    "local_write_action": False,
                    "object_storage_action": False,
                    "network_action": False,
                    "private_key_material": "not-emitted",
                }, sort_keys=True))
                return 0
            expected_confirmation = "bootstrap-webapp-fi-emergency-source-identity:" + campaign_id
            if args.confirm != expected_confirmation:
                _fail("--confirm must exactly equal the campaign identity bootstrap confirmation")
            print(json.dumps(bootstrap_campaign_identity(campaign_id=campaign_id), sort_keys=True))
            return 0
        if args.action != "receive":  # pragma: no cover - argparse action invariant.
            _fail("source bootstrap action is unsupported")
        public_key, signer_key_id, _encoded_public_key = _load_pinned_signer(anchor_approval=anchor_approval)
        descriptor_payload = _read_file(args.descriptor, label="signed source descriptor", maximum=MAX_DESCRIPTOR_BYTES, private=False)
        descriptor = verify_descriptor(descriptor_payload, public_key=public_key)
        _require_descriptor_control_context(descriptor=descriptor, anchor_approval=anchor_approval)
        identity = _campaign_identity_path(str(descriptor["campaign_id"]))
        local_recipient = _derive_age_recipient(identity=identity)
        local_recipient_key_id = "age-recipient-sha256:" + hashlib.sha256(local_recipient.encode("ascii")).hexdigest()
        if local_recipient_key_id != descriptor["recipient_key_id"]:
            _fail("fixed WebApp-FI campaign identity does not match the signed recipient pin")
        if not args.apply:
            print(json.dumps({
                "status": "verified-non-authorizing",
                "campaign_id": descriptor["campaign_id"],
                "descriptor_sha256": descriptor["descriptor_sha256"],
                "bootstrap": descriptor["bootstrap"],
                "pinned_signer_key_id": signer_key_id,
                "identity_path": str(identity),
                "recipient_key_id": local_recipient_key_id,
                "object_storage_action": False,
                "receiver_execution": False,
                "s3_credentials": "not-accepted-on-webapp-fi",
            }, sort_keys=True))
            return 0
        if args.confirm != descriptor["campaign_id"]:
            _fail("--confirm must exactly equal the signed campaign ID")
        raw_urls = sys.stdin.buffer.read(MAX_URL_MAP_BYTES + 1)
        if len(raw_urls) > MAX_URL_MAP_BYTES:
            _fail("transient URL map exceeds its fixed bound")
        result = receive_from_transient_url_map(
            # Do not reopen the caller pathname after confirmation.  The
            # bytes validated above remain immutable in-process and this
            # helper immediately makes its own create-only private copy.
            descriptor_payload=descriptor_payload,
            destination=args.destination,
            url_map_payload=raw_urls,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except EmergencySourceBootstrapError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
