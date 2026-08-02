#!/usr/bin/env python3
"""Build, seal, publish, and receive one exact Emergency source checkout.

This is a deliberately narrow bridge for the *source* prerequisite of the
Emergency/three-site work.  It does not deploy an application.  In
particular, it never starts Docker, changes a service, changes ``current``,
touches a database, mounts a volume, or opens SSH.

The controller side turns one clean Git checkout into a self-contained Git
bundle, encrypts it once to the pinned WebApp-FI age recipient, and can put it
only into a private, versioned Arvan Object Storage bucket.  The publication
descriptor is Ed25519 signed after the provider returns an immutable
``VersionId``.  The WebApp-FI side accepts no S3 credentials: after explicit
confirmation it validates one version-bound presigned HTTPS GET, downloads
directly from Object Storage, decrypts locally, and atomically creates a
fresh Git checkout only after its complete Git identity is verified.

Every persistent write and every Object Storage request requires ``--apply``.
Source-campaign network actions also require ``--confirm`` equal to the
campaign ID; first-stage control artifacts use their separately printed,
artifact-hash/VersionId-bound confirmation phrase.  Without those switches
commands produce a non-authorizing plan only.

The two checkout inputs are deliberately different trust objects.  ``prepare
--repository`` must be a clean source-only checkout at exactly
``e1a309725154ab6b67655ebdfe22c73d831aa72e`` / tree
``d158aa5f520fd625537f927fb079196aa24fa302``; it is the only tree put into
the encrypted bundle.  ``--controller-repository`` must be a separate later,
clean control checkout containing this committed tool.  Passing the control
checkout as ``--repository`` is rejected because its HEAD is not the pinned
Emergency source commit.

Failure recovery is intentionally non-destructive.  A root-only durable
receipt outside ``/run`` binds each WA-FI campaign ID to exactly one verified
descriptor.  An exact-descriptor retry is safe after a reboot only when the
volatile candidate is gone; a different descriptor or an existing candidate
requires a new campaign ID and is never deleted by this tool.  A failed source
publish likewise requires a new campaign/identity as applicable.  This avoids
a retry silently consuming a changed URL, descriptor, or key.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import dataclasses
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import tarfile
from typing import Any, Iterator, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
except ImportError:  # pragma: no cover - deployment requirements provide cryptography.
    InvalidSignature = None  # type: ignore[assignment,misc]
    serialization = None  # type: ignore[assignment,misc]
    Ed25519PrivateKey = None  # type: ignore[assignment,misc]
    Ed25519PublicKey = None  # type: ignore[assignment,misc]


TOOL_SOURCE_PATH = "scripts/manage_webapp_fi_emergency_source_delivery.py"

PREPARED_SCHEMA = "gold-trade-webapp-fi-emergency-source-prepared-v1"
DESCRIPTOR_SCHEMA = "gold-trade-webapp-fi-emergency-source-descriptor-v1"
SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-emergency-source-descriptor-v1\x00"
SIGNATURE_ALGORITHM = "ed25519"
ENCRYPTION_ALGORITHM = "age-v1"
SOURCE_SITE = "controller"
DESTINATION_SITE = "webapp_fi"
OBJECT_LAYOUT = "webapp-fi-emergency-source/v1"
BOOTSTRAP_OBJECT_LAYOUT = "webapp-fi-emergency-source-bootstrap/v1"
ANCHOR_OBJECT_LAYOUT = "webapp-fi-emergency-source-anchor/v1"
INSTALLER_OBJECT_LAYOUT = "webapp-fi-emergency-source-installer/v1"
ANCHOR_APPROVAL_SCHEMA = "gold-trade-webapp-fi-emergency-source-anchor-approval-v2"
INSTALLER_APPROVAL_SCHEMA = "gold-trade-webapp-fi-emergency-source-installer-approval-v2"
INSTALLER_PLACEMENT_SCOPE = "webapp-fi-emergency-source-first-installer"
SIGNER_APPROVAL_SCHEMA = "gold-trade-webapp-fi-emergency-source-signer-approval-v1"
SIGNER_APPROVAL_SCOPE = "webapp-fi-emergency-source-signing-key"
ANCHOR_RELATIVE_PATH = "scripts/run_webapp_fi_emergency_source_receive.py"
INSTALLER_RELATIVE_PATH = "scripts/install_webapp_fi_emergency_source_anchor.py"
PINNED_ANCHOR_PATH = "/usr/local/lib/trading-bot-three-site/run_webapp_fi_emergency_source_receive.py"
PINNED_INSTALLER_PATH = "/usr/local/lib/trading-bot-three-site/install_webapp_fi_emergency_source_anchor.py"
FI_CAMPAIGN_IDENTITY_ROOT = Path("/etc/trading-bot-three-site/campaigns")
FI_EMERGENCY_SOURCE_IDENTITY_LEAF = "webapp-fi/emergency-source.agekey"

# The one unavoidable root-console ceremony runs before the first verified
# Python artifact exists.  Its commands must therefore not inherit a caller's
# PATH, proxy, CA, curl configuration, or Python environment.  This prefix is
# deliberately literal and is used only in rendered operator commands.
ROOT_CONSOLE_CLEAN_ENV = (
    "/usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent "
    "XDG_CONFIG_HOME=/nonexistent CURL_HOME=/nonexistent LC_ALL=C LANG=C"
)

# These are the exact identities approved for this immediate Emergency source
# transfer.  The functions below accept identities explicitly for local tests,
# while the CLI never silently substitutes a moving branch or release.
SOURCE_RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
SOURCE_RELEASE_TREE = "b4da321f72b84c075bd267bda1211f0ff68b91d6"
# This is the final WA-IR source commit approved for the isolated delivered
# source checkout.  It is intentionally distinct from the later controller
# revision that runs this manager; the bundle must name only this commit.
EMERGENCY_PATCH_SHA = "e1a309725154ab6b67655ebdfe22c73d831aa72e"
EMERGENCY_PATCH_TREE = "d158aa5f520fd625537f927fb079196aa24fa302"

MAX_DESCRIPTOR_BYTES = 128 * 1024
MAX_CREDENTIAL_BYTES = 16 * 1024
MAX_KEY_BYTES = 1024
MAX_GIT_BUNDLE_BYTES = 512 * 1024 * 1024
MAX_BOOTSTRAP_BYTES = 4 * 1024 * 1024
MAX_CIPHERTEXT_OVERHEAD_BYTES = 2 * 1024 * 1024
MAX_URL_BYTES = 16 * 1024
MIN_PRESIGNED_TTL_SECONDS = 60
MAX_PRESIGNED_TTL_SECONDS = 900
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
DISK_HEADROOM_BYTES = 128 * 1024 * 1024

SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
GIT_SHA_RE = re.compile(r"^[a-f0-9]{40}$", re.ASCII)
VERSION_ID_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{1,1024}$", re.ASCII)
CAMPAIGN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII)
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$", re.ASCII)
PREFIX_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,127}$", re.ASCII)
AGE_RECIPIENT_RE = re.compile(r"^age1[ac-hj-np-z02-9]{20,128}$", re.ASCII)
RECIPIENT_KEY_ID_RE = re.compile(r"^age-recipient-sha256:[a-f0-9]{64}$", re.ASCII)
SIGNER_KEY_ID_RE = re.compile(r"^ed25519-sha256:[a-f0-9]{64}$", re.ASCII)
REGION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$", re.ASCII)

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
_S3_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NoSuchVersion", "NotFound"})
# Arvan's S3-compatible service may not expose the AWS Public Access Block
# capability at all.  This exact capability-absence response is the sole
# exception to requiring all four AWS PAB flags.  Bucket policy/ACL and every
# object ACL remain independently fail-closed privacy boundaries.
_ARVAN_NO_PUBLIC_ACCESS_BLOCK_CODE = "NoSuchPublicAccessBlockConfiguration"
_ARVAN_NO_PUBLIC_ACCESS_BLOCK_ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
_ARVAN_NO_PUBLIC_ACCESS_BLOCK_REGION = "ir-thr-at1"


class EmergencySourceDeliveryError(RuntimeError):
    """The bounded Emergency source delivery contract was violated."""


@dataclasses.dataclass(frozen=True)
class SourceIdentity:
    base_sha: str
    base_tree: str
    emergency_patch_sha: str
    emergency_patch_tree: str

    def as_descriptor(self, *, bundle_sha256: str, bundle_bytes: int) -> dict[str, Any]:
        return {
            "base_sha": self.base_sha,
            "base_tree": self.base_tree,
            "emergency_patch_sha": self.emergency_patch_sha,
            "emergency_patch_tree": self.emergency_patch_tree,
            "git_bundle_sha256": bundle_sha256,
            "git_bundle_bytes": bundle_bytes,
        }


@dataclasses.dataclass(frozen=True)
class ControllerToolIdentity:
    revision: str
    tree: str
    sha256: str
    bytes: int

    def as_descriptor(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "tree": self.tree,
            "sha256": self.sha256,
            "bytes": self.bytes,
        }


@dataclasses.dataclass(frozen=True)
class VerifiedDescriptor:
    manifest_sha256: str
    campaign_id: str
    endpoint: str
    region: str
    bucket: str
    prefix: str
    object_key: str
    version_id: str
    recipient_key_id: str
    identity: SourceIdentity
    bundle_sha256: str
    bundle_bytes: int
    ciphertext_sha256: str
    ciphertext_bytes: int
    controller_tool: ControllerToolIdentity
    receiver_bootstrap_sha256: str
    receiver_bootstrap_bytes: int
    bootstrap_object_key: str
    bootstrap_version_id: str
    bootstrap_sha256: str
    bootstrap_bytes: int
    signer_key_id: str

    def as_receive_plan(self) -> dict[str, Any]:
        return {
            "schema": "gold-trade-webapp-fi-emergency-source-receive-plan-v1",
            "status": "verified-non-authorizing",
            "campaign_id": self.campaign_id,
            "source_site": SOURCE_SITE,
            "destination_site": DESTINATION_SITE,
            "descriptor_sha256": self.manifest_sha256,
            "endpoint": self.endpoint,
            "region": self.region,
            "bucket": self.bucket,
            "prefix": self.prefix,
            "object_key": self.object_key,
            "version_id": self.version_id,
            "recipient_key_id": self.recipient_key_id,
            "source": self.identity.as_descriptor(
                bundle_sha256=self.bundle_sha256, bundle_bytes=self.bundle_bytes
            ),
            "ciphertext": {"sha256": self.ciphertext_sha256, "bytes": self.ciphertext_bytes},
            "controller_tool": self.controller_tool.as_descriptor(),
            "receiver_bootstrap": {
                "schema": "gold-trade-webapp-fi-emergency-source-receiver-bootstrap-v1",
                "sha256": self.receiver_bootstrap_sha256,
                "bytes": self.receiver_bootstrap_bytes,
            },
            "bootstrap": {
                "object_key": self.bootstrap_object_key,
                "version_id": self.bootstrap_version_id,
                "sha256": self.bootstrap_sha256,
                "bytes": self.bootstrap_bytes,
            },
            "signer_key_id": self.signer_key_id,
            "s3_credentials": "not-accepted-on-webapp-fi",
            "network_action": False,
            "checkout_action": False,
        }


def _fail(message: str) -> None:
    raise EmergencySourceDeliveryError(message)


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        _fail("Emergency source delivery operations must run as root")


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    except (TypeError, ValueError, RecursionError) as exc:
        raise EmergencySourceDeliveryError("value cannot be canonically encoded") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("JSON contains a duplicate field")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("JSON constants are unsupported")


def _parse_canonical_json(payload: bytes, *, label: str, maximum_bytes: int) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= maximum_bytes:
        _fail(f"{label} bytes are empty or exceed the fixed bound")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except EmergencySourceDeliveryError:
        raise
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise EmergencySourceDeliveryError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) + b"\n" != payload:
        _fail(f"{label} is not canonical JSON")
    return value


def _require_text(value: object, *, field: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        _fail(f"{field} must be a non-empty trimmed string")
    if "\x00" in value or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        _fail(f"{field} contains a control character")
    return value


def _require_pattern(value: object, *, field: str, pattern: re.Pattern[str]) -> str:
    text = _require_text(value, field=field)
    if pattern.fullmatch(text) is None:
        _fail(f"{field} has an unsafe format")
    return text


def _require_positive_int(value: object, *, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        _fail(f"{field} is outside its fixed positive bound")
    return value


def _require_absolute(path: Path | str, *, field: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        _fail(f"{field} must be absolute")
    return candidate


def _validate_endpoint(endpoint: object, region: object) -> tuple[str, str]:
    endpoint_text = _require_text(endpoint, field="endpoint")
    region_text = _require_pattern(region, field="region", pattern=REGION_RE)
    try:
        parsed = urlsplit(endpoint_text)
        port = parsed.port
    except ValueError as exc:
        raise EmergencySourceDeliveryError("endpoint is malformed") from exc
    expected_host = f"s3.{region_text}.arvanstorage.ir"
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        _fail("endpoint must be the exact HTTPS Arvan S3 endpoint for the declared region")
    return endpoint_text.rstrip("/"), region_text


def _validate_prefix(value: object) -> str:
    prefix = _require_text(value, field="prefix").strip("/")
    if not prefix or any(PREFIX_COMPONENT_RE.fullmatch(item) is None for item in prefix.split("/")):
        _fail("prefix has an unsafe Object Storage layout")
    return prefix


def recipient_key_id(recipient: str) -> str:
    normalized = _require_pattern(recipient, field="age_recipient", pattern=AGE_RECIPIENT_RE)
    return "age-recipient-sha256:" + hashlib.sha256(normalized.encode("ascii")).hexdigest()


def campaign_identity_path(campaign_id: str) -> Path:
    campaign_id = _require_pattern(campaign_id, field="campaign_id", pattern=CAMPAIGN_RE)
    path = FI_CAMPAIGN_IDENTITY_ROOT / campaign_id / FI_EMERGENCY_SOURCE_IDENTITY_LEAF
    if not path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts[1:]):
        _fail("campaign-local WebApp-FI age identity path is invalid")
    return path


def _require_safe_directory(path: Path, *, label: str, private: bool) -> Path:
    path = _require_absolute(path, field=label)
    current = Path(path.anchor)
    final_state: os.stat_result | None = None
    for component in path.parts[1:]:
        current /= component
        try:
            state = current.lstat()
        except OSError as exc:
            raise EmergencySourceDeliveryError(f"{label} cannot be inspected") from exc
        mode = stat.S_IMODE(state.st_mode)
        # Ancestors such as /etc and /run are normally root-owned 0755.  A
        # private leaf must not incorrectly reject those readable ancestors;
        # every chain member only needs to be non-writable by group/other.
        writable = bool(mode & 0o022)
        root_owned_sticky = state.st_uid == 0 and bool(state.st_mode & stat.S_ISVTX)
        if (
            stat.S_ISLNK(state.st_mode)
            or not stat.S_ISDIR(state.st_mode)
            or state.st_uid != os.geteuid()
            or (writable and not root_owned_sticky)
        ):
            _fail(f"{label} is not an owner-controlled directory chain")
        final_state = state
    if final_state is None:
        _fail(f"{label} directory chain is invalid")
    if private and stat.S_IMODE(final_state.st_mode) != 0o700:
        _fail(f"{label} final directory is not root-only 0700")
    return path


def _require_safe_regular(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
    private: bool,
    executable: bool = False,
) -> Path:
    path = _require_absolute(path, field=label)
    _require_safe_directory(path.parent, label=f"{label} parent", private=False)
    try:
        state = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise EmergencySourceDeliveryError(f"{label} cannot be inspected") from exc
    mode = stat.S_IMODE(state.st_mode)
    if (
        resolved != path
        or stat.S_ISLNK(state.st_mode)
        or not stat.S_ISREG(state.st_mode)
        or state.st_uid != os.geteuid()
        or state.st_nlink != 1
        or (mode != 0o600 if private else bool(mode & 0o022))
        or not 1 <= state.st_size <= maximum_bytes
        or (executable and not mode & 0o100)
    ):
        _fail(f"{label} is not one bounded owner-controlled regular file")
    return path


def _read_stable_regular(path: Path, *, label: str, maximum_bytes: int, private: bool) -> bytes:
    path = _require_safe_regular(path, label=label, maximum_bytes=maximum_bytes, private=private)
    descriptor: int | None = None
    try:
        before = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(opened, field) for field in fields):
            _fail(f"{label} changed while being opened")
        payload = bytearray()
        while len(payload) <= maximum_bytes:
            chunk = os.read(descriptor, min(65536, maximum_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if len(payload) != opened.st_size or len(payload) > maximum_bytes or any(
            getattr(opened, field) != getattr(after, field) for field in fields
        ):
            _fail(f"{label} changed while being read")
        return bytes(payload)
    except OSError as exc:
        raise EmergencySourceDeliveryError(f"{label} cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _hash_regular(path: Path, *, label: str, maximum_bytes: int, private: bool) -> tuple[str, int]:
    payload = _read_stable_regular(path, label=label, maximum_bytes=maximum_bytes, private=private)
    return hashlib.sha256(payload).hexdigest(), len(payload)


def _write_create_only(path: Path, payload: bytes, *, label: str) -> None:
    path = _require_absolute(path, field=label)
    _require_safe_directory(path.parent, label=f"{label} parent", private=True)
    if path.exists() or path.is_symlink():
        _fail(f"refusing to overwrite {label}")
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
        view = memoryview(payload)
        while view:
            count = os.write(descriptor, view)
            if count <= 0:
                raise OSError("short output write")
            view = view[count:]
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise EmergencySourceDeliveryError(f"refusing to overwrite {label}") from exc
    except OSError as exc:
        raise EmergencySourceDeliveryError(f"{label} cannot be created") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    _fsync_directory(path.parent, label=f"{label} parent")


def _fsync_directory(path: Path, *, label: str) -> None:
    """Persist a directory entry after a create-only file/directory write."""

    path = _require_safe_directory(path, label=label, private=False)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or opened.st_uid != os.geteuid():
            _fail(f"{label} changed while being fsynced")
        os.fsync(descriptor)
    except EmergencySourceDeliveryError:
        raise
    except OSError as exc:
        raise EmergencySourceDeliveryError(f"{label} cannot be fsynced") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _new_private_directory(path: Path, *, label: str) -> Path:
    path = _require_absolute(path, field=label)
    _require_safe_directory(path.parent, label=f"{label} parent", private=True)
    if path.exists() or path.is_symlink():
        _fail(f"refusing to reuse {label}")
    try:
        path.mkdir(mode=0o700)
        os.chmod(path, 0o700)
        state = path.lstat()
    except OSError as exc:
        raise EmergencySourceDeliveryError(f"{label} cannot be created") from exc
    if not stat.S_ISDIR(state.st_mode) or state.st_uid != os.geteuid() or stat.S_IMODE(state.st_mode) != 0o700:
        _fail(f"{label} is not one fresh private directory")
    _fsync_directory(path.parent, label=f"{label} parent")
    _fsync_directory(path, label=label)
    return path


def _ensure_private_child(parent: Path, name: str, *, label: str) -> Path:
    if not name or name in {".", ".."} or "/" in name:
        _fail(f"{label} child name is unsafe")
    parent = _require_safe_directory(parent, label=f"{label} parent", private=True)
    child = parent / name
    try:
        state = child.lstat()
    except FileNotFoundError:
        try:
            child.mkdir(mode=0o700)
            os.chmod(child, 0o700)
            state = child.lstat()
            _fsync_directory(parent, label=f"{label} parent")
            _fsync_directory(child, label=label)
        except OSError as exc:
            raise EmergencySourceDeliveryError(f"{label} cannot be created") from exc
    except OSError as exc:
        raise EmergencySourceDeliveryError(f"{label} cannot be inspected") from exc
    if (
        stat.S_ISLNK(state.st_mode)
        or not stat.S_ISDIR(state.st_mode)
        or state.st_uid != os.geteuid()
        or stat.S_IMODE(state.st_mode) != 0o700
    ):
        _fail(f"{label} is not a root-only directory")
    return child


def _ensure_fixed_private_root(path: Path, *, label: str) -> Path:
    """Create the fixed campaign root durably, never a caller-selected path.

    The controller-side identity helper is kept behaviorally aligned with the
    installed WA-FI anchor.  Root-owned 0755 ancestors are acceptable; the
    final persistent leaf is always exactly 0700 and every created directory
    is fsynced before the function succeeds.
    """

    path = _require_absolute(path, field=label)
    current = Path(path.anchor)
    parts = path.parts[1:]
    if not parts:
        _fail(f"{label} cannot be the filesystem root")
    for index, component in enumerate(parts):
        candidate = current / component
        expected_mode = 0o700 if index == len(parts) - 1 else 0o755
        try:
            state = candidate.lstat()
        except FileNotFoundError:
            if current != Path(current.anchor):
                _require_safe_directory(current, label=f"{label} parent", private=False)
            try:
                candidate.mkdir(mode=expected_mode)
                os.chmod(candidate, expected_mode)
                state = candidate.lstat()
            except OSError as exc:
                raise EmergencySourceDeliveryError(f"{label} cannot be created") from exc
            if (
                stat.S_ISLNK(state.st_mode)
                or not stat.S_ISDIR(state.st_mode)
                or state.st_uid != os.geteuid()
                or stat.S_IMODE(state.st_mode) != expected_mode
            ):
                _fail(f"{label} was not created with its fixed root-only mode")
            _fsync_directory(current, label=f"{label} parent")
            _fsync_directory(candidate, label=label)
        except OSError as exc:
            raise EmergencySourceDeliveryError(f"{label} cannot be inspected") from exc
        else:
            if (
                stat.S_ISLNK(state.st_mode)
                or not stat.S_ISDIR(state.st_mode)
                or state.st_uid != os.geteuid()
                or stat.S_IMODE(state.st_mode) & 0o022
            ):
                _fail(f"{label} is not rooted in owner-controlled directories")
            if index == len(parts) - 1 and stat.S_IMODE(state.st_mode) != 0o700:
                _fail(f"{label} final directory mode differs from the fixed 0700 contract")
        current = candidate
    return _require_safe_directory(path, label=label, private=True)


def _git_environment() -> dict[str, str]:
    """Return the entire environment allowed into root-owned helpers.

    In particular this does not inherit ``LD_*``, proxy, Python, Git, or
    shell helper variables from the invoking terminal.
    """

    return {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ALLOW_PROTOCOL": "file",
        "LC_ALL": "C",
        "LANG": "C",
        "PATH": os.defpath,
    }


def _run_git(arguments: Sequence[str], *, cwd: Path | None = None, purpose: str) -> subprocess.CompletedProcess[str]:
    git = _require_safe_regular(Path("/usr/bin/git"), label="git binary", maximum_bytes=128 * 1024 * 1024, private=False, executable=True)
    try:
        completed = subprocess.run(
            [str(git), "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", *arguments],
            cwd=None if cwd is None else str(cwd),
            env=_git_environment(),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencySourceDeliveryError(f"{purpose} could not start") from exc
    if completed.returncode != 0:
        _fail(f"{purpose} failed")
    return completed


def _git_bytes(arguments: Sequence[str], *, cwd: Path, purpose: str) -> bytes:
    """Read an immutable Git blob without text decoding/newline conversion."""

    git = _require_safe_regular(Path("/usr/bin/git"), label="git binary", maximum_bytes=128 * 1024 * 1024, private=False, executable=True)
    try:
        completed = subprocess.run(
            [str(git), "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", *arguments],
            cwd=str(cwd),
            env=_git_environment(),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencySourceDeliveryError(f"{purpose} could not start") from exc
    if completed.returncode != 0:
        _fail(f"{purpose} failed")
    return bytes(completed.stdout)


def _git_output(arguments: Sequence[str], *, cwd: Path, purpose: str) -> str:
    return _run_git(arguments, cwd=cwd, purpose=purpose).stdout.strip()


def _validate_identity(value: SourceIdentity) -> SourceIdentity:
    if not isinstance(value, SourceIdentity):
        _fail("source identity has an unsupported type")
    return SourceIdentity(
        base_sha=_require_pattern(value.base_sha, field="base_sha", pattern=GIT_SHA_RE),
        base_tree=_require_pattern(value.base_tree, field="base_tree", pattern=GIT_SHA_RE),
        emergency_patch_sha=_require_pattern(value.emergency_patch_sha, field="emergency_patch_sha", pattern=GIT_SHA_RE),
        emergency_patch_tree=_require_pattern(value.emergency_patch_tree, field="emergency_patch_tree", pattern=GIT_SHA_RE),
    )


def _validate_controller_tool(value: ControllerToolIdentity) -> ControllerToolIdentity:
    if not isinstance(value, ControllerToolIdentity):
        _fail("controller tool identity has an unsupported type")
    return ControllerToolIdentity(
        revision=_require_pattern(value.revision, field="controller_tool.revision", pattern=GIT_SHA_RE),
        tree=_require_pattern(value.tree, field="controller_tool.tree", pattern=GIT_SHA_RE),
        sha256=_require_pattern(value.sha256, field="controller_tool.sha256", pattern=SHA256_RE),
        bytes=_require_positive_int(value.bytes, field="controller_tool.bytes", maximum=2 * 1024 * 1024),
    )


def _inspect_committed_control_file(*, repository: Path, relative_path: str, label: str) -> ControllerToolIdentity:
    """Bind one fixed control artifact to a clean later controller checkout."""

    repository = _require_safe_directory(repository, label="controller tool repository", private=False)
    top = Path(_git_output(["rev-parse", "--show-toplevel"], cwd=repository, purpose="controller tool repository discovery"))
    if top.resolve() != repository.resolve():
        _fail("controller tool repository must be its checkout root")
    if _git_output(
        ["status", "--porcelain=v1", "--untracked-files=all", "--"],
        cwd=repository,
        purpose="controller tool repository cleanliness check",
    ):
        _fail("controller tool repository must be clean")
    revision = _git_output(["rev-parse", "--verify", "HEAD^{commit}"], cwd=repository, purpose="controller tool revision verification")
    tree = _git_output(["rev-parse", revision + "^{tree}"], cwd=repository, purpose="controller tool tree verification")
    if not isinstance(relative_path, str) or not relative_path or relative_path.startswith("/") or ".." in PurePosixPath(relative_path).parts:
        _fail("controller artifact path is unsafe")
    actual_path = repository / relative_path
    actual = _read_stable_regular(actual_path, label=label, maximum_bytes=2 * 1024 * 1024, private=False)
    expected = _git_bytes(
        ["show", f"{revision}:{relative_path}"],
        cwd=repository,
        purpose=f"{label} committed-byte verification",
    )
    if not hashlib.sha256(actual).digest() == hashlib.sha256(expected).digest():
        _fail(f"{label} bytes differ from its committed control revision")
    return _validate_controller_tool(
        ControllerToolIdentity(revision=revision, tree=tree, sha256=hashlib.sha256(actual).hexdigest(), bytes=len(actual))
    )


def inspect_controller_tool(*, repository: Path) -> ControllerToolIdentity:
    """Bind the running manager from the later clean controller checkout."""

    actual_path = Path(__file__).resolve()
    try:
        relative = actual_path.relative_to(repository.resolve()).as_posix()
    except ValueError as exc:
        raise EmergencySourceDeliveryError("controller manager is not located in the declared control checkout") from exc
    if relative != TOOL_SOURCE_PATH:
        _fail("controller manager path differs from the fixed control source path")
    return _inspect_committed_control_file(
        repository=repository,
        relative_path=TOOL_SOURCE_PATH,
        label="controller manager source",
    )


def inspect_controller_artifact(*, repository: Path) -> ControllerToolIdentity:
    """Inspect the exact committed manager bytes that receiver bootstrap may execute.

    Control-artifact publication deliberately does not assume that this process
    itself was launched from ``repository``.  It still binds the approved
    manager path, revision, tree, bytes, and hash from that one clean checkout
    into both first-stage approval records.
    """

    return _inspect_committed_control_file(
        repository=repository,
        relative_path=TOOL_SOURCE_PATH,
        label="controller manager artifact",
    )


def inspect_anchor_artifact(*, repository: Path) -> ControllerToolIdentity:
    return _inspect_committed_control_file(
        repository=repository,
        relative_path=ANCHOR_RELATIVE_PATH,
        label="WebApp-FI source anchor artifact",
    )


def inspect_installer_artifact(*, repository: Path) -> ControllerToolIdentity:
    return _inspect_committed_control_file(
        repository=repository,
        relative_path=INSTALLER_RELATIVE_PATH,
        label="WebApp-FI source anchor first-installer artifact",
    )


def inspect_clean_checkout(*, repository: Path, base_sha: str, emergency_patch_sha: str) -> SourceIdentity:
    """Prove the exact source is clean and rooted at the supplied checkout."""

    repository = _require_safe_directory(repository, label="Emergency source repository", private=False)
    base_sha = _require_pattern(base_sha, field="base_sha", pattern=GIT_SHA_RE)
    emergency_patch_sha = _require_pattern(emergency_patch_sha, field="emergency_patch_sha", pattern=GIT_SHA_RE)
    top = Path(_git_output(["rev-parse", "--show-toplevel"], cwd=repository, purpose="repository discovery"))
    if top.resolve() != repository.resolve():
        _fail("Emergency source repository must be the checkout root")
    if _git_output(
        ["status", "--porcelain=v1", "--untracked-files=all", "--"],
        cwd=repository,
        purpose="repository cleanliness check",
    ):
        _fail("Emergency source checkout is dirty")
    head = _git_output(["rev-parse", "--verify", "HEAD^{commit}"], cwd=repository, purpose="HEAD verification")
    if head != emergency_patch_sha:
        _fail("Emergency source checkout HEAD does not match the approved patch identity")
    _git_output(["rev-parse", "--verify", base_sha + "^{commit}"], cwd=repository, purpose="base revision verification")
    ancestor = _run_git(
        ["merge-base", "--is-ancestor", base_sha, emergency_patch_sha],
        cwd=repository,
        purpose="base ancestry verification",
    )
    if ancestor.returncode != 0:  # defensive; _run_git already makes this fail closed.
        _fail("Emergency source patch is not descended from the approved base")
    base_tree = _git_output(["rev-parse", base_sha + "^{tree}"], cwd=repository, purpose="base tree verification")
    patch_tree = _git_output(
        ["rev-parse", emergency_patch_sha + "^{tree}"], cwd=repository, purpose="patch tree verification"
    )
    return _validate_identity(
        SourceIdentity(
            base_sha=base_sha,
            base_tree=base_tree,
            emergency_patch_sha=emergency_patch_sha,
            emergency_patch_tree=patch_tree,
        )
    )


def inspect_fixed_emergency_checkout(*, repository: Path) -> SourceIdentity:
    """Inspect only the approved 2c08 base and exact e1a30972 WA-IR source."""

    identity = inspect_clean_checkout(
        repository=repository,
        base_sha=SOURCE_RELEASE_SHA,
        emergency_patch_sha=EMERGENCY_PATCH_SHA,
    )
    if (identity.base_tree, identity.emergency_patch_tree) != (SOURCE_RELEASE_TREE, EMERGENCY_PATCH_TREE):
        _fail("Emergency source checkout tree identities do not match the fixed approved release")
    return identity


def _branch_for_head(*, repository: Path, head: str) -> str:
    branch = _git_output(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=repository, purpose="source branch discovery")
    if not branch or branch.startswith("-") or ".." in PurePosixPath(branch).parts:
        _fail("Emergency source checkout must be on one safe named branch")
    ref = "refs/heads/" + branch
    if _git_output(["rev-parse", "--verify", ref], cwd=repository, purpose="source branch verification") != head:
        _fail("Emergency source branch moved away from the approved HEAD")
    return branch


def _verify_checkout_filesystem(*, checkout: Path) -> None:
    """Reject post-clone aliases before the checkout is made visible.

    The committed Emergency source has only ordinary files and directories.
    A root checkout containing a link, hard link, set-id bit, wrong owner, or
    group/world writable application path is never an acceptable candidate,
    even if Git's object graph itself is intact.
    """

    checkout = _require_absolute(checkout, field="received source checkout")
    try:
        root_state = checkout.lstat()
    except OSError as exc:
        raise EmergencySourceDeliveryError("received source checkout cannot be inspected") from exc
    if (
        stat.S_ISLNK(root_state.st_mode)
        or not stat.S_ISDIR(root_state.st_mode)
        or root_state.st_uid != os.geteuid()
        or stat.S_IMODE(root_state.st_mode) & 0o022
    ):
        _fail("received source checkout root is unsafe")
    for current, directories, files in os.walk(checkout, topdown=True, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(directories)
        for name in [*directories, *files]:
            path = current_path / name
            try:
                state = path.lstat()
            except OSError as exc:
                raise EmergencySourceDeliveryError("received source checkout entry cannot be inspected") from exc
            mode = stat.S_IMODE(state.st_mode)
            if (
                stat.S_ISLNK(state.st_mode)
                or state.st_uid != os.geteuid()
                or mode & 0o022
                or state.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
            ):
                _fail("received source checkout contains an unsafe filesystem entry")
            if stat.S_ISDIR(state.st_mode):
                continue
            if not stat.S_ISREG(state.st_mode) or state.st_nlink != 1:
                _fail("received source checkout contains a non-regular application file")
    # The walk includes `.git`, rather than treating it as an opaque Git
    # implementation detail.  A received checkout must not hide a link or a
    # hard-linked regular file in its repository metadata either: this tree
    # will be used by later root-owned build steps.


def _verify_checkout_identity(*, checkout: Path, identity: SourceIdentity) -> None:
    identity = _validate_identity(identity)
    _verify_checkout_filesystem(checkout=checkout)
    if _git_output(["rev-parse", "--verify", "HEAD^{commit}"], cwd=checkout, purpose="received HEAD verification") != identity.emergency_patch_sha:
        _fail("received source checkout HEAD differs from the signed Emergency patch")
    if _git_output(
        ["rev-parse", "--verify", identity.base_sha + "^{commit}"], cwd=checkout, purpose="received base verification"
    ) != identity.base_sha:
        _fail("received source checkout lacks the signed base commit")
    _run_git(
        ["merge-base", "--is-ancestor", identity.base_sha, identity.emergency_patch_sha],
        cwd=checkout,
        purpose="received base ancestry verification",
    )
    if _git_output(["rev-parse", identity.base_sha + "^{tree}"], cwd=checkout, purpose="received base tree verification") != identity.base_tree:
        _fail("received base tree differs from the signed source identity")
    if _git_output(
        ["rev-parse", identity.emergency_patch_sha + "^{tree}"], cwd=checkout, purpose="received patch tree verification"
    ) != identity.emergency_patch_tree:
        _fail("received Emergency patch tree differs from the signed source identity")
    if _git_output(
        ["status", "--porcelain=v1", "--untracked-files=all", "--"],
        cwd=checkout,
        purpose="received checkout cleanliness check",
    ):
        _fail("received source checkout is not clean")
    _run_git(["diff", "--check"], cwd=checkout, purpose="received checkout whitespace verification")
    _run_git(["fsck", "--no-dangling"], cwd=checkout, purpose="received Git object verification")


def build_self_contained_git_bundle(*, repository: Path, identity: SourceIdentity, output: Path) -> tuple[str, int]:
    """Create a self-contained bundle and prove it can reconstruct the exact checkout.

    A temporary local bare clone gives the bundle a single named reference
    without mutating the source checkout or relying on a checkout's shared
    ``.git`` directory.  The source bytes are only Git objects, never mutable
    worktree paths.
    """

    _require_root_execution()
    identity = _validate_identity(identity)
    repository = _require_safe_directory(repository, label="Emergency source repository", private=False)
    output = _require_absolute(output, field="Git bundle output")
    _require_safe_directory(output.parent, label="Git bundle output parent", private=True)
    if output.exists() or output.is_symlink():
        _fail("refusing to overwrite Git bundle output")
    actual = inspect_clean_checkout(
        repository=repository, base_sha=identity.base_sha, emergency_patch_sha=identity.emergency_patch_sha
    )
    if actual != identity:
        _fail("Emergency source identity changed before Git bundle construction")
    branch = _branch_for_head(repository=repository, head=identity.emergency_patch_sha)
    with tempfile.TemporaryDirectory(prefix=".emergency-source-bundle-", dir=str(output.parent)) as temporary:
        temporary_root = Path(temporary)
        os.chmod(temporary_root, 0o700)
        bare = temporary_root / "source.git"
        _run_git(
            [
                "clone",
                "--bare",
                "--no-local",
                "--single-branch",
                "--branch",
                branch,
                repository.resolve().as_uri(),
                str(bare),
            ],
            cwd=temporary_root,
            purpose="self-contained source bundle clone",
        )
        ref = "refs/heads/" + branch
        if _git_output(["rev-parse", "--verify", ref], cwd=bare, purpose="temporary source branch verification") != identity.emergency_patch_sha:
            _fail("self-contained source clone does not retain the approved patch")
        _run_git(["bundle", "create", str(output), ref], cwd=bare, purpose="Git bundle construction")
        try:
            os.chmod(output, 0o600)
        except OSError as exc:
            raise EmergencySourceDeliveryError("Git bundle output mode cannot be restricted") from exc
        _hash_regular(output, label="Git bundle output", maximum_bytes=MAX_GIT_BUNDLE_BYTES, private=True)
        verification = temporary_root / "bundle-verification"
        _run_git(
            ["clone", "--no-checkout", "--no-local", str(output), str(verification)],
            cwd=temporary_root,
            purpose="self-contained Git bundle verification clone",
        )
        _run_git(
            ["checkout", "--detach", identity.emergency_patch_sha],
            cwd=verification,
            purpose="self-contained Git bundle verification checkout",
        )
        _verify_checkout_identity(checkout=verification, identity=identity)
    return _hash_regular(output, label="Git bundle output", maximum_bytes=MAX_GIT_BUNDLE_BYTES, private=True)


def _require_age_binary(path: Path, *, label: str) -> Path:
    return _require_safe_regular(path, label=label, maximum_bytes=128 * 1024 * 1024, private=False, executable=True)


def encrypt_git_bundle(*, bundle: Path, recipient: str, age_binary: Path, output: Path) -> tuple[str, int]:
    _require_root_execution()
    bundle = _require_safe_regular(bundle, label="Git bundle plaintext", maximum_bytes=MAX_GIT_BUNDLE_BYTES, private=True)
    recipient = _require_pattern(recipient, field="age_recipient", pattern=AGE_RECIPIENT_RE)
    age_binary = _require_age_binary(age_binary, label="age binary")
    output = _require_absolute(output, field="encrypted Git bundle output")
    _require_safe_directory(output.parent, label="encrypted Git bundle output parent", private=True)
    if output.exists() or output.is_symlink():
        _fail("refusing to overwrite encrypted Git bundle output")
    try:
        completed = subprocess.run(
            [str(age_binary), "-r", recipient, "-o", str(output), str(bundle)],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=900,
            env=_git_environment(),
            preexec_fn=lambda: os.umask(0o077),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencySourceDeliveryError("age encryption could not start") from exc
    if completed.returncode != 0:
        _fail("age encryption failed")
    return _hash_regular(
        output,
        label="encrypted Git bundle output",
        maximum_bytes=MAX_GIT_BUNDLE_BYTES + MAX_CIPHERTEXT_OVERHEAD_BYTES,
        private=True,
    )


def _source_object_key(*, prefix: str, campaign_id: str, identity: SourceIdentity, bundle_sha256: str) -> str:
    campaign_id = _require_pattern(campaign_id, field="campaign_id", pattern=CAMPAIGN_RE)
    identity = _validate_identity(identity)
    bundle_sha256 = _require_pattern(bundle_sha256, field="git_bundle_sha256", pattern=SHA256_RE)
    prefix = _validate_prefix(prefix)
    return "/".join(
        (
            prefix,
            OBJECT_LAYOUT,
            campaign_id,
            identity.base_sha,
            identity.emergency_patch_sha,
            bundle_sha256 + ".bundle.age",
        )
    )


def _bootstrap_object_key(*, prefix: str, campaign_id: str, controller_tool: ControllerToolIdentity) -> str:
    prefix = _validate_prefix(prefix)
    campaign_id = _require_pattern(campaign_id, field="campaign_id", pattern=CAMPAIGN_RE)
    controller_tool = _validate_controller_tool(controller_tool)
    return "/".join(
        (
            prefix,
            BOOTSTRAP_OBJECT_LAYOUT,
            campaign_id,
            controller_tool.sha256 + ".tar.gz",
        )
    )


def build_receiver_bootstrap(*, controller_tool: ControllerToolIdentity, output: Path) -> tuple[str, int]:
    """Build the small independently verified receiver payload, never application source."""

    _require_root_execution()
    controller_tool = _validate_controller_tool(controller_tool)
    receiver = Path(__file__).resolve()
    payload = _read_stable_regular(receiver, label="controller receiver source", maximum_bytes=2 * 1024 * 1024, private=False)
    if (hashlib.sha256(payload).hexdigest(), len(payload)) != (controller_tool.sha256, controller_tool.bytes):
        _fail("receiver bootstrap source differs from the separately attested controller tool")
    receiver_manifest = canonical_json_bytes(
        {
            "schema": "gold-trade-webapp-fi-emergency-source-receiver-bootstrap-v1",
            "receiver_sha256": controller_tool.sha256,
            "receiver_bytes": controller_tool.bytes,
        }
    ) + b"\n"
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for name, value in (("RECEIVER.json", receiver_manifest), ("receiver.py", payload)):
                info = tarfile.TarInfo(name)
                info.size = len(value)
                info.mode = 0o600
                info.uid = 0
                info.gid = 0
                info.uname = "root"
                info.gname = "root"
                info.mtime = 0
                archive.addfile(info, io.BytesIO(value))
    artifact = raw.getvalue()
    if not 1 <= len(artifact) <= MAX_BOOTSTRAP_BYTES:
        _fail("receiver bootstrap exceeds its fixed bound")
    _write_create_only(output, artifact, label="receiver bootstrap output")
    return _hash_regular(output, label="receiver bootstrap output", maximum_bytes=MAX_BOOTSTRAP_BYTES, private=True)


def verify_receiver_bootstrap(*, artifact: Path, controller_tool: ControllerToolIdentity) -> tuple[str, int]:
    """Verify a bootstrap tar without executing its receiver program."""

    controller_tool = _validate_controller_tool(controller_tool)
    payload = _read_stable_regular(artifact, label="receiver bootstrap artifact", maximum_bytes=MAX_BOOTSTRAP_BYTES, private=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            members = archive.getmembers()
            if {member.name for member in members} != {"RECEIVER.json", "receiver.py"} or len(members) != 2:
                _fail("receiver bootstrap member layout is invalid")
            values: dict[str, bytes] = {}
            for member in members:
                if not member.isreg() or member.issym() or member.islnk() or not 1 <= member.size <= 2 * 1024 * 1024:
                    _fail("receiver bootstrap member is unsafe")
                handle = archive.extractfile(member)
                if handle is None:
                    _fail("receiver bootstrap member cannot be read")
                values[member.name] = handle.read()
    except EmergencySourceDeliveryError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise EmergencySourceDeliveryError("receiver bootstrap is not a valid bounded gzip tar") from exc
    receiver = values.get("receiver.py", b"")
    if (hashlib.sha256(receiver).hexdigest(), len(receiver)) != (controller_tool.sha256, controller_tool.bytes):
        _fail("receiver bootstrap receiver bytes differ from controller provenance")
    manifest = _parse_canonical_json(values.get("RECEIVER.json", b""), label="receiver bootstrap manifest", maximum_bytes=64 * 1024)
    if manifest != {
        "schema": "gold-trade-webapp-fi-emergency-source-receiver-bootstrap-v1",
        "receiver_sha256": controller_tool.sha256,
        "receiver_bytes": controller_tool.bytes,
    }:
        _fail("receiver bootstrap manifest differs from controller provenance")
    return hashlib.sha256(payload).hexdigest(), len(payload)


def build_prepared_plan(
    *,
    campaign_id: str,
    endpoint: str,
    region: str,
    bucket: str,
    prefix: str,
    recipient: str,
    identity: SourceIdentity,
    controller_tool: ControllerToolIdentity,
    bundle: Path,
    bootstrap: Path,
    ciphertext: Path,
) -> dict[str, Any]:
    """Describe already-local sealed bytes; this function has no network I/O."""

    endpoint, region = _validate_endpoint(endpoint, region)
    bucket = _require_pattern(bucket, field="bucket", pattern=BUCKET_RE)
    prefix = _validate_prefix(prefix)
    campaign_id = _require_pattern(campaign_id, field="campaign_id", pattern=CAMPAIGN_RE)
    identity = _validate_identity(identity)
    controller_tool = _validate_controller_tool(controller_tool)
    bootstrap_sha256, bootstrap_bytes = verify_receiver_bootstrap(
        artifact=bootstrap, controller_tool=controller_tool
    )
    bundle_sha256, bundle_bytes = _hash_regular(
        bundle, label="Git bundle plaintext", maximum_bytes=MAX_GIT_BUNDLE_BYTES, private=True
    )
    ciphertext_sha256, ciphertext_bytes = _hash_regular(
        ciphertext,
        label="encrypted Git bundle",
        maximum_bytes=MAX_GIT_BUNDLE_BYTES + MAX_CIPHERTEXT_OVERHEAD_BYTES,
        private=True,
    )
    result = {
        "schema": PREPARED_SCHEMA,
        "campaign_id": campaign_id,
        "source_site": SOURCE_SITE,
        "destination_site": DESTINATION_SITE,
        "endpoint": endpoint,
        "region": region,
        "bucket": bucket,
        "prefix": prefix,
        "object_key": _source_object_key(
            prefix=prefix, campaign_id=campaign_id, identity=identity, bundle_sha256=bundle_sha256
        ),
        "recipient_key_id": recipient_key_id(recipient),
        "source": identity.as_descriptor(bundle_sha256=bundle_sha256, bundle_bytes=bundle_bytes),
        "controller_tool": controller_tool.as_descriptor(),
        "receiver_bootstrap": {
            "schema": "gold-trade-webapp-fi-emergency-source-receiver-bootstrap-v1",
            "sha256": controller_tool.sha256,
            "bytes": controller_tool.bytes,
        },
        "bootstrap": {
            "object_key": _bootstrap_object_key(
                prefix=prefix, campaign_id=campaign_id, controller_tool=controller_tool
            ),
            "path": str(_require_absolute(bootstrap, field="receiver bootstrap artifact")),
            "sha256": bootstrap_sha256,
            "bytes": bootstrap_bytes,
        },
        "ciphertext": {
            "path": str(_require_absolute(ciphertext, field="encrypted Git bundle")),
            "sha256": ciphertext_sha256,
            "bytes": ciphertext_bytes,
        },
    }
    validate_prepared_plan(result)
    return result


def _source_from_value(value: object) -> tuple[SourceIdentity, str, int]:
    if not isinstance(value, Mapping) or set(value) != {
        "base_sha",
        "base_tree",
        "emergency_patch_sha",
        "emergency_patch_tree",
        "git_bundle_sha256",
        "git_bundle_bytes",
    }:
        _fail("source identity fields are unsupported")
    identity = _validate_identity(
        SourceIdentity(
            base_sha=str(value.get("base_sha")),
            base_tree=str(value.get("base_tree")),
            emergency_patch_sha=str(value.get("emergency_patch_sha")),
            emergency_patch_tree=str(value.get("emergency_patch_tree")),
        )
    )
    bundle_sha256 = _require_pattern(value.get("git_bundle_sha256"), field="git_bundle_sha256", pattern=SHA256_RE)
    bundle_bytes = _require_positive_int(value.get("git_bundle_bytes"), field="git_bundle_bytes", maximum=MAX_GIT_BUNDLE_BYTES)
    return identity, bundle_sha256, bundle_bytes


def _controller_tool_from_value(value: object) -> ControllerToolIdentity:
    if not isinstance(value, Mapping) or set(value) != {"revision", "tree", "sha256", "bytes"}:
        _fail("controller tool descriptor fields are unsupported")
    return _validate_controller_tool(
        ControllerToolIdentity(
            revision=str(value.get("revision")),
            tree=str(value.get("tree")),
            sha256=str(value.get("sha256")),
            bytes=value.get("bytes"),
        )
    )


def _receiver_bootstrap_from_value(value: object, *, controller_tool: ControllerToolIdentity) -> tuple[str, int]:
    if not isinstance(value, Mapping) or set(value) != {"schema", "sha256", "bytes"}:
        _fail("receiver bootstrap descriptor fields are unsupported")
    if value.get("schema") != "gold-trade-webapp-fi-emergency-source-receiver-bootstrap-v1":
        _fail("receiver bootstrap descriptor schema is unsupported")
    sha256 = _require_pattern(value.get("sha256"), field="receiver_bootstrap.sha256", pattern=SHA256_RE)
    bytes_value = _require_positive_int(value.get("bytes"), field="receiver_bootstrap.bytes", maximum=2 * 1024 * 1024)
    if (sha256, bytes_value) != (controller_tool.sha256, controller_tool.bytes):
        _fail("receiver bootstrap does not match the separately attested controller tool")
    return sha256, bytes_value


def _bootstrap_from_value(
    value: object, *, prefix: str, campaign_id: str, controller_tool: ControllerToolIdentity, include_version: bool
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("receiver bootstrap object descriptor is invalid")
    fields = {"object_key", "sha256", "bytes"}
    fields.add("version_id" if include_version else "path")
    if set(value) != fields:
        _fail("receiver bootstrap object descriptor fields are unsupported")
    expected_key = _bootstrap_object_key(prefix=prefix, campaign_id=campaign_id, controller_tool=controller_tool)
    key = _require_text(value.get("object_key"), field="bootstrap.object_key", maximum=2048)
    if key != expected_key:
        _fail("receiver bootstrap object key is not deterministic")
    result: dict[str, Any] = {
        "object_key": key,
        "sha256": _require_pattern(value.get("sha256"), field="bootstrap.sha256", pattern=SHA256_RE),
        "bytes": _require_positive_int(value.get("bytes"), field="bootstrap.bytes", maximum=MAX_BOOTSTRAP_BYTES),
    }
    if include_version:
        result["version_id"] = _require_pattern(value.get("version_id"), field="bootstrap.version_id", pattern=VERSION_ID_RE)
    else:
        result["path"] = str(
            _require_absolute(Path(_require_text(value.get("path"), field="bootstrap.path")), field="bootstrap.path")
        )
    return result


def _validate_common(value: Mapping[str, Any], *, include_version: bool) -> tuple[dict[str, Any], SourceIdentity, str, int, str, int]:
    required = {
        "schema",
        "campaign_id",
        "source_site",
        "destination_site",
        "endpoint",
        "region",
        "bucket",
        "prefix",
        "object_key",
        "recipient_key_id",
        "source",
        "controller_tool",
        "receiver_bootstrap",
        "bootstrap",
        "ciphertext",
    }
    if include_version:
        required.add("version_id")
    if set(value) != required:
        _fail("source delivery fields are unsupported")
    if value.get("source_site") != SOURCE_SITE or value.get("destination_site") != DESTINATION_SITE:
        _fail("source delivery route is not controller to WebApp-FI")
    endpoint, region = _validate_endpoint(value.get("endpoint"), value.get("region"))
    bucket = _require_pattern(value.get("bucket"), field="bucket", pattern=BUCKET_RE)
    prefix = _validate_prefix(value.get("prefix"))
    campaign_id = _require_pattern(value.get("campaign_id"), field="campaign_id", pattern=CAMPAIGN_RE)
    recipient_id = _require_pattern(value.get("recipient_key_id"), field="recipient_key_id", pattern=RECIPIENT_KEY_ID_RE)
    identity, bundle_sha256, bundle_bytes = _source_from_value(value.get("source"))
    controller_tool = _controller_tool_from_value(value.get("controller_tool"))
    receiver_sha256, receiver_bytes = _receiver_bootstrap_from_value(
        value.get("receiver_bootstrap"), controller_tool=controller_tool
    )
    bootstrap = _bootstrap_from_value(
        value.get("bootstrap"),
        prefix=prefix,
        campaign_id=campaign_id,
        controller_tool=controller_tool,
        include_version=include_version,
    )
    expected_key = _source_object_key(
        prefix=prefix, campaign_id=campaign_id, identity=identity, bundle_sha256=bundle_sha256
    )
    object_key = _require_text(value.get("object_key"), field="object_key", maximum=2048)
    if object_key != expected_key:
        _fail("source delivery object key is not the deterministic signed key")
    cipher = value.get("ciphertext")
    if not isinstance(cipher, Mapping):
        _fail("ciphertext descriptor is invalid")
    expected_cipher_fields = {"sha256", "bytes"}
    if not include_version:
        expected_cipher_fields.add("path")
    if set(cipher) != expected_cipher_fields:
        _fail("ciphertext descriptor fields are unsupported")
    ciphertext_sha256 = _require_pattern(cipher.get("sha256"), field="ciphertext_sha256", pattern=SHA256_RE)
    ciphertext_bytes = _require_positive_int(
        cipher.get("bytes"), field="ciphertext_bytes", maximum=MAX_GIT_BUNDLE_BYTES + MAX_CIPHERTEXT_OVERHEAD_BYTES
    )
    normalized: dict[str, Any] = {
        "schema": value.get("schema"),
        "campaign_id": campaign_id,
        "source_site": SOURCE_SITE,
        "destination_site": DESTINATION_SITE,
        "endpoint": endpoint,
        "region": region,
        "bucket": bucket,
        "prefix": prefix,
        "object_key": object_key,
        "recipient_key_id": recipient_id,
        "source": identity.as_descriptor(bundle_sha256=bundle_sha256, bundle_bytes=bundle_bytes),
        "controller_tool": controller_tool.as_descriptor(),
        "receiver_bootstrap": {
            "schema": "gold-trade-webapp-fi-emergency-source-receiver-bootstrap-v1",
            "sha256": receiver_sha256,
            "bytes": receiver_bytes,
        },
        "bootstrap": bootstrap,
        "ciphertext": {"sha256": ciphertext_sha256, "bytes": ciphertext_bytes},
    }
    if include_version:
        normalized["version_id"] = _require_pattern(value.get("version_id"), field="version_id", pattern=VERSION_ID_RE)
    else:
        path = _require_absolute(Path(_require_text(cipher.get("path"), field="ciphertext.path")), field="ciphertext.path")
        normalized["ciphertext"] = {"path": str(path), "sha256": ciphertext_sha256, "bytes": ciphertext_bytes}
    return normalized, identity, bundle_sha256, bundle_bytes, ciphertext_sha256, ciphertext_bytes


def validate_prepared_plan(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("prepared source delivery plan must be an object")
    normalized, _identity, _bundle_hash, _bundle_bytes, _cipher_hash, _cipher_bytes = _validate_common(
        dict(value), include_version=False
    )
    if normalized["schema"] != PREPARED_SCHEMA:
        _fail("prepared source delivery schema is unsupported")
    return normalized


def _require_crypto() -> None:
    if Ed25519PrivateKey is None or Ed25519PublicKey is None or serialization is None or InvalidSignature is None:
        _fail("cryptography Ed25519 support is unavailable")


def _decode_key_file(path: Path, *, private: bool) -> bytes:
    payload = _read_stable_regular(path, label="Ed25519 key file", maximum_bytes=MAX_KEY_BYTES, private=private)
    try:
        encoded = payload.decode("ascii").strip()
        decoded = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeDecodeError, UnicodeEncodeError, binascii.Error) as exc:
        raise EmergencySourceDeliveryError("Ed25519 key file must be strict base64") from exc
    if len(decoded) != 32:
        _fail("Ed25519 key file must decode to exactly 32 bytes")
    return decoded


def _public_key_bytes(key: Any) -> bytes:
    _require_crypto()
    if not isinstance(key, Ed25519PublicKey):
        _fail("Ed25519 public key is invalid")
    return key.public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


def signer_key_id(public_key: Any) -> str:
    return "ed25519-sha256:" + hashlib.sha256(_public_key_bytes(public_key)).hexdigest()


def load_private_key(path: Path) -> Any:
    _require_crypto()
    try:
        return Ed25519PrivateKey.from_private_bytes(_decode_key_file(path, private=True))
    except ValueError as exc:
        raise EmergencySourceDeliveryError("Ed25519 private key is invalid") from exc


def load_public_key(path: Path) -> Any:
    _require_crypto()
    try:
        return Ed25519PublicKey.from_public_bytes(_decode_key_file(path, private=False))
    except ValueError as exc:
        raise EmergencySourceDeliveryError("Ed25519 public key is invalid") from exc


def _unsigned_descriptor_from_prepared(
    plan: Mapping[str, Any], *, source_version_id: str, bootstrap_version_id: str
) -> dict[str, Any]:
    plan = validate_prepared_plan(plan)
    source_version_id = _require_pattern(source_version_id, field="source_version_id", pattern=VERSION_ID_RE)
    bootstrap_version_id = _require_pattern(bootstrap_version_id, field="bootstrap_version_id", pattern=VERSION_ID_RE)
    return {
        "schema": DESCRIPTOR_SCHEMA,
        "campaign_id": plan["campaign_id"],
        "source_site": SOURCE_SITE,
        "destination_site": DESTINATION_SITE,
        "endpoint": plan["endpoint"],
        "region": plan["region"],
        "bucket": plan["bucket"],
        "prefix": plan["prefix"],
        "object_key": plan["object_key"],
        "version_id": source_version_id,
        "recipient_key_id": plan["recipient_key_id"],
        "source": dict(plan["source"]),
        "controller_tool": dict(plan["controller_tool"]),
        "receiver_bootstrap": dict(plan["receiver_bootstrap"]),
        "bootstrap": {
            "object_key": plan["bootstrap"]["object_key"],
            "version_id": bootstrap_version_id,
            "sha256": plan["bootstrap"]["sha256"],
            "bytes": plan["bootstrap"]["bytes"],
        },
        "ciphertext": {
            "sha256": plan["ciphertext"]["sha256"],
            "bytes": plan["ciphertext"]["bytes"],
        },
    }


def _validate_unsigned_descriptor(value: object) -> tuple[dict[str, Any], SourceIdentity, str, int, str, int]:
    if not isinstance(value, Mapping):
        _fail("unsigned source descriptor must be an object")
    normalized, identity, bundle_hash, bundle_bytes, cipher_hash, cipher_bytes = _validate_common(
        dict(value), include_version=True
    )
    if normalized["schema"] != DESCRIPTOR_SCHEMA:
        _fail("source descriptor schema is unsupported")
    return normalized, identity, bundle_hash, bundle_bytes, cipher_hash, cipher_bytes


def _descriptor_signing_payload(unsigned: Mapping[str, Any]) -> bytes:
    return SIGNATURE_DOMAIN + canonical_json_bytes(dict(unsigned))


def sign_descriptor(unsigned: Mapping[str, Any], *, private_key: Any) -> dict[str, Any]:
    _require_crypto()
    if not isinstance(private_key, Ed25519PrivateKey):
        _fail("Ed25519 private key is invalid")
    normalized, _identity, _bundle_hash, _bundle_bytes, _cipher_hash, _cipher_bytes = _validate_unsigned_descriptor(unsigned)
    public_key = private_key.public_key()
    signed = {
        **normalized,
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "signer_key_id": signer_key_id(public_key),
        "signature_base64": base64.b64encode(private_key.sign(_descriptor_signing_payload(normalized))).decode("ascii"),
    }
    verify_descriptor(signed, public_key=public_key)
    return signed


def verify_descriptor(value: object, *, public_key: Any) -> VerifiedDescriptor:
    _require_crypto()
    if not isinstance(public_key, Ed25519PublicKey):
        _fail("Ed25519 public key is invalid")
    fields = {
        "schema",
        "campaign_id",
        "source_site",
        "destination_site",
        "endpoint",
        "region",
        "bucket",
        "prefix",
        "object_key",
        "version_id",
        "recipient_key_id",
        "source",
        "controller_tool",
        "receiver_bootstrap",
        "bootstrap",
        "ciphertext",
        "signature_algorithm",
        "signer_key_id",
        "signature_base64",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail("signed source descriptor fields are unsupported")
    descriptor = dict(value)
    unsigned = {field: descriptor[field] for field in fields if field not in {"signature_algorithm", "signer_key_id", "signature_base64"}}
    normalized, identity, bundle_hash, bundle_bytes, cipher_hash, cipher_bytes = _validate_unsigned_descriptor(unsigned)
    controller_tool = _controller_tool_from_value(normalized["controller_tool"])
    receiver_sha256, receiver_bytes = _receiver_bootstrap_from_value(
        normalized["receiver_bootstrap"], controller_tool=controller_tool
    )
    bootstrap = _bootstrap_from_value(
        normalized["bootstrap"],
        prefix=str(normalized["prefix"]),
        campaign_id=str(normalized["campaign_id"]),
        controller_tool=controller_tool,
        include_version=True,
    )
    if descriptor.get("signature_algorithm") != SIGNATURE_ALGORITHM:
        _fail("source descriptor signature algorithm is unsupported")
    key_id = _require_pattern(descriptor.get("signer_key_id"), field="signer_key_id", pattern=SIGNER_KEY_ID_RE)
    if key_id != signer_key_id(public_key):
        _fail("source descriptor signer does not match the pinned public key")
    try:
        signature = base64.b64decode(_require_text(descriptor.get("signature_base64"), field="signature_base64").encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise EmergencySourceDeliveryError("source descriptor signature is not strict base64") from exc
    if len(signature) != 64:
        _fail("source descriptor signature length is invalid")
    try:
        public_key.verify(signature, _descriptor_signing_payload(normalized))
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise EmergencySourceDeliveryError("source descriptor Ed25519 signature is invalid") from exc
    canonical = canonical_json_bytes(descriptor)
    return VerifiedDescriptor(
        manifest_sha256=hashlib.sha256(canonical).hexdigest(),
        campaign_id=str(normalized["campaign_id"]),
        endpoint=str(normalized["endpoint"]),
        region=str(normalized["region"]),
        bucket=str(normalized["bucket"]),
        prefix=str(normalized["prefix"]),
        object_key=str(normalized["object_key"]),
        version_id=str(normalized["version_id"]),
        recipient_key_id=str(normalized["recipient_key_id"]),
        identity=identity,
        bundle_sha256=bundle_hash,
        bundle_bytes=bundle_bytes,
        ciphertext_sha256=cipher_hash,
        ciphertext_bytes=cipher_bytes,
        controller_tool=controller_tool,
        receiver_bootstrap_sha256=receiver_sha256,
        receiver_bootstrap_bytes=receiver_bytes,
        bootstrap_object_key=str(bootstrap["object_key"]),
        bootstrap_version_id=str(bootstrap["version_id"]),
        bootstrap_sha256=str(bootstrap["sha256"]),
        bootstrap_bytes=int(bootstrap["bytes"]),
        signer_key_id=key_id,
    )


def load_verified_descriptor(path: Path, *, public_key: Any) -> VerifiedDescriptor:
    payload = _read_stable_regular(path, label="signed source descriptor", maximum_bytes=MAX_DESCRIPTOR_BYTES, private=False)
    return verify_descriptor(_parse_canonical_json(payload, label="signed source descriptor", maximum_bytes=MAX_DESCRIPTOR_BYTES), public_key=public_key)


def _control_artifact_key(*, prefix: str, layout: str, artifact: ControllerToolIdentity) -> str:
    prefix = _validate_prefix(prefix)
    artifact = _validate_controller_tool(artifact)
    if layout not in {ANCHOR_OBJECT_LAYOUT, INSTALLER_OBJECT_LAYOUT}:
        _fail("control artifact layout is unsupported")
    return "/".join((prefix, layout, artifact.revision, artifact.sha256 + ".py"))


def _build_anchor_approval(
    *,
    endpoint: str,
    region: str,
    bucket: str,
    prefix: str,
    artifact: ControllerToolIdentity,
    controller_tool: ControllerToolIdentity,
    version_id: str,
) -> dict[str, Any]:
    endpoint, region = _validate_endpoint(endpoint, region)
    bucket = _require_pattern(bucket, field="bucket", pattern=BUCKET_RE)
    prefix = _validate_prefix(prefix)
    artifact = _validate_controller_tool(artifact)
    controller_tool = _validate_controller_tool(controller_tool)
    if (artifact.revision, artifact.tree) != (controller_tool.revision, controller_tool.tree):
        _fail("anchor and receiver controller artifacts do not share one control revision")
    return {
        "schema": ANCHOR_APPROVAL_SCHEMA,
        "anchor_path": PINNED_ANCHOR_PATH,
        "endpoint": endpoint,
        "region": region,
        "bucket": bucket,
        "prefix": prefix,
        "object_key": _control_artifact_key(prefix=prefix, layout=ANCHOR_OBJECT_LAYOUT, artifact=artifact),
        "artifact_version_id": _require_pattern(version_id, field="anchor artifact_version_id", pattern=VERSION_ID_RE),
        "anchor_sha256": artifact.sha256,
        "anchor_bytes": artifact.bytes,
        "controller_revision": artifact.revision,
        "controller_tree": artifact.tree,
        "controller_tool_sha256": controller_tool.sha256,
        "controller_tool_bytes": controller_tool.bytes,
    }


def _build_installer_approval(
    *,
    endpoint: str,
    region: str,
    bucket: str,
    prefix: str,
    artifact: ControllerToolIdentity,
    controller_tool: ControllerToolIdentity,
    version_id: str,
) -> dict[str, Any]:
    endpoint, region = _validate_endpoint(endpoint, region)
    bucket = _require_pattern(bucket, field="bucket", pattern=BUCKET_RE)
    prefix = _validate_prefix(prefix)
    artifact = _validate_controller_tool(artifact)
    controller_tool = _validate_controller_tool(controller_tool)
    if (artifact.revision, artifact.tree) != (controller_tool.revision, controller_tool.tree):
        _fail("first installer and receiver controller artifacts do not share one control revision")
    return {
        "schema": INSTALLER_APPROVAL_SCHEMA,
        "installer_path": PINNED_INSTALLER_PATH,
        "endpoint": endpoint,
        "region": region,
        "bucket": bucket,
        "prefix": prefix,
        "object_key": _control_artifact_key(prefix=prefix, layout=INSTALLER_OBJECT_LAYOUT, artifact=artifact),
        "artifact_version_id": _require_pattern(version_id, field="installer artifact_version_id", pattern=VERSION_ID_RE),
        "installer_sha256": artifact.sha256,
        "installer_bytes": artifact.bytes,
        "controller_revision": artifact.revision,
        "controller_tree": artifact.tree,
        "controller_tool_sha256": controller_tool.sha256,
        "controller_tool_bytes": controller_tool.bytes,
        "placement_scope": INSTALLER_PLACEMENT_SCOPE,
    }


def _validate_control_approval(value: object, *, kind: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("control artifact approval must be an object")
    if kind == "anchor":
        required = {
            "schema", "anchor_path", "endpoint", "region", "bucket", "prefix", "object_key", "artifact_version_id",
            "anchor_sha256", "anchor_bytes", "controller_revision", "controller_tree",
            "controller_tool_sha256", "controller_tool_bytes",
        }
        if set(value) != required or value.get("schema") != ANCHOR_APPROVAL_SCHEMA:
            _fail("anchor approval fields are unsupported")
        path_field, expected_path = "anchor_path", PINNED_ANCHOR_PATH
        hash_field, bytes_field, layout = "anchor_sha256", "anchor_bytes", ANCHOR_OBJECT_LAYOUT
    elif kind == "installer":
        required = {
            "schema", "installer_path", "endpoint", "region", "bucket", "prefix", "object_key", "artifact_version_id",
            "installer_sha256", "installer_bytes", "controller_revision", "controller_tree",
            "controller_tool_sha256", "controller_tool_bytes", "placement_scope",
        }
        if set(value) != required or value.get("schema") != INSTALLER_APPROVAL_SCHEMA:
            _fail("first-installer approval fields are unsupported")
        if value.get("placement_scope") != INSTALLER_PLACEMENT_SCOPE:
            _fail("first-installer approval scope is unsupported")
        path_field, expected_path = "installer_path", PINNED_INSTALLER_PATH
        hash_field, bytes_field, layout = "installer_sha256", "installer_bytes", INSTALLER_OBJECT_LAYOUT
    else:
        _fail("control artifact approval kind is unsupported")
    if _require_text(value.get(path_field), field=path_field, maximum=2048) != expected_path:
        _fail("control artifact approval fixed target path differs")
    endpoint, region = _validate_endpoint(value.get("endpoint"), value.get("region"))
    bucket = _require_pattern(value.get("bucket"), field="bucket", pattern=BUCKET_RE)
    prefix = _validate_prefix(value.get("prefix"))
    artifact = _validate_controller_tool(
        ControllerToolIdentity(
            revision=str(value.get("controller_revision")),
            tree=str(value.get("controller_tree")),
            sha256=str(value.get(hash_field)),
            bytes=value.get(bytes_field),
        )
    )
    controller_tool = _validate_controller_tool(
        ControllerToolIdentity(
            revision=artifact.revision,
            tree=artifact.tree,
            sha256=str(value.get("controller_tool_sha256")),
            bytes=value.get("controller_tool_bytes"),
        )
    )
    key = _require_text(value.get("object_key"), field="object_key", maximum=2048)
    if key != _control_artifact_key(prefix=prefix, layout=layout, artifact=artifact):
        _fail("control artifact approval object key is not deterministic")
    return {
        **({"schema": ANCHOR_APPROVAL_SCHEMA, "anchor_path": PINNED_ANCHOR_PATH} if kind == "anchor" else {
            "schema": INSTALLER_APPROVAL_SCHEMA,
            "installer_path": PINNED_INSTALLER_PATH,
            "placement_scope": INSTALLER_PLACEMENT_SCOPE,
        }),
        "endpoint": endpoint,
        "region": region,
        "bucket": bucket,
        "prefix": prefix,
        "object_key": key,
        "artifact_version_id": _require_pattern(value.get("artifact_version_id"), field="artifact_version_id", pattern=VERSION_ID_RE),
        hash_field: artifact.sha256,
        bytes_field: artifact.bytes,
        "controller_revision": artifact.revision,
        "controller_tree": artifact.tree,
        "controller_tool_sha256": controller_tool.sha256,
        "controller_tool_bytes": controller_tool.bytes,
    }


def build_pinned_signer_approval(*, anchor_approval: Mapping[str, Any], signing_public_key: Any) -> dict[str, Any]:
    """Create the non-secret receipt that independently binds signer to anchor."""

    anchor = _validate_control_approval(anchor_approval, kind="anchor")
    key_id = signer_key_id(signing_public_key)
    return {
        "schema": SIGNER_APPROVAL_SCHEMA,
        "anchor_sha256": anchor["anchor_sha256"],
        "signer_key_id": key_id,
        "approval_scope": SIGNER_APPROVAL_SCOPE,
    }


def _validate_pinned_signer_approval(value: object, *, anchor_approval: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"schema", "anchor_sha256", "signer_key_id", "approval_scope"}:
        _fail("pinned signer approval fields are unsupported")
    anchor = _validate_control_approval(anchor_approval, kind="anchor")
    if value.get("schema") != SIGNER_APPROVAL_SCHEMA or value.get("approval_scope") != SIGNER_APPROVAL_SCOPE:
        _fail("pinned signer approval schema/scope is unsupported")
    anchor_sha256 = _require_pattern(value.get("anchor_sha256"), field="anchor_sha256", pattern=SHA256_RE)
    if anchor_sha256 != anchor["anchor_sha256"]:
        _fail("pinned signer approval is not bound to the independent anchor receipt")
    return {
        "schema": SIGNER_APPROVAL_SCHEMA,
        "anchor_sha256": anchor_sha256,
        "signer_key_id": _require_pattern(value.get("signer_key_id"), field="signer_key_id", pattern=SIGNER_KEY_ID_RE),
        "approval_scope": SIGNER_APPROVAL_SCOPE,
    }


def _load_pinned_signer_approval(path: Path, *, anchor_approval: Mapping[str, Any]) -> dict[str, str]:
    payload = _read_stable_regular(
        path,
        label="pinned signer independent approval",
        maximum_bytes=MAX_DESCRIPTOR_BYTES,
        private=True,
    )
    return _validate_pinned_signer_approval(
        _parse_canonical_json(payload, label="pinned signer independent approval", maximum_bytes=MAX_DESCRIPTOR_BYTES),
        anchor_approval=anchor_approval,
    )


def _load_prepared(path: Path) -> dict[str, Any]:
    payload = _read_stable_regular(path, label="prepared source delivery plan", maximum_bytes=MAX_DESCRIPTOR_BYTES, private=True)
    return validate_prepared_plan(_parse_canonical_json(payload, label="prepared source delivery plan", maximum_bytes=MAX_DESCRIPTOR_BYTES))


def _metadata(*, kind: str, sha256: str, recipient_id: str | None = None) -> dict[str, str]:
    result = {
        "delivery-schema": DESCRIPTOR_SCHEMA,
        "artifact-kind": kind,
        "sha256": sha256,
    }
    if recipient_id is not None:
        result.update({"encryption": ENCRYPTION_ALGORITHM, "recipient-key-id": recipient_id})
    return result


def _reject_provider_side_encryption(response: Mapping[str, Any]) -> None:
    if any(
        response.get(name) is not None
        for name in ("ServerSideEncryption", "SSECustomerAlgorithm", "SSECustomerKeyMD5", "SSEKMSKeyId")
    ):
        _fail("provider-side encryption is not permitted for the source object")


def _load_credentials(path: Path) -> dict[str, str]:
    _require_direct_object_storage_environment()
    payload = _read_stable_regular(path, label="Object Storage credentials", maximum_bytes=MAX_CREDENTIAL_BYTES, private=True)
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object, parse_constant=_reject_json_constant)
    except EmergencySourceDeliveryError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise EmergencySourceDeliveryError("Object Storage credentials are not strict JSON") from exc
    if not isinstance(value, Mapping) or set(value) not in ({"access_key_id", "secret_access_key"}, {"access_key_id", "secret_access_key", "session_token"}):
        _fail("Object Storage credential fields are unsupported")
    result = {
        "access_key_id": _require_text(value.get("access_key_id"), field="access_key_id", maximum=2048),
        "secret_access_key": _require_text(value.get("secret_access_key"), field="secret_access_key", maximum=4096),
    }
    if value.get("session_token") is not None:
        result["session_token"] = _require_text(value.get("session_token"), field="session_token", maximum=8192)
    return result


def _create_s3_client(plan: Mapping[str, Any], credentials: Mapping[str, str]) -> Any:
    _require_direct_object_storage_environment()
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover - deployment has boto3.
        raise EmergencySourceDeliveryError("boto3 is unavailable") from exc
    try:
        session = boto3.session.Session(
            aws_access_key_id=credentials["access_key_id"],
            aws_secret_access_key=credentials["secret_access_key"],
            aws_session_token=credentials.get("session_token"),
            region_name=str(plan["region"]),
        )
        return session.client(
            "s3",
            endpoint_url=str(plan["endpoint"]),
            verify=True,
            # Object Storage traffic is never permitted through a terminal
            # proxy inherited from the controller environment.
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, proxies={}),
        )
    except Exception as exc:
        raise EmergencySourceDeliveryError("Object Storage client cannot be created") from exc


def _require_direct_object_storage_environment() -> None:
    """Reject every ambient proxy/AWS/TLS override before reading credentials.

    The dedicated credentials file and fixed endpoint are the only intended
    controller inputs.  Botocore, requests, curl, and OpenSSL otherwise
    consult a broad set of environment variables, which could silently
    redirect a signed request or replace the platform trust store.  Python's
    ``-I`` does *not* prevent OpenSSL from honoring ``SSL_CERT_FILE`` or
    ``SSL_CERT_DIR``, so those are an explicit fail-closed boundary too.
    """

    forbidden: list[str] = []
    for key in os.environ:
        lowered = key.lower()
        if (
            lowered.startswith("aws_")
            or lowered.startswith("boto_")
            or lowered in {
                "all_proxy", "http_proxy", "https_proxy", "no_proxy",
                "requests_ca_bundle", "curl_ca_bundle",
                "ssl_cert_file", "ssl_cert_dir",
                "openssl_conf", "openssl_modules", "sslkeylogfile",
                "pythonhttpsverify",
            }
        ):
            forbidden.append(key)
    if forbidden:
        _fail("ambient Object Storage proxy/AWS/TLS environment is forbidden")


def _s3_error_code(exc: BaseException) -> str | None:
    response = getattr(exc, "response", None)
    if not isinstance(response, Mapping):
        return None
    error = response.get("Error")
    if not isinstance(error, Mapping):
        return None
    code = error.get("Code")
    return str(code) if code is not None else None


def _require_owner_only_acl(value: object, *, label: str, expected_owner_id: str | None = None) -> str:
    if not isinstance(value, Mapping):
        _fail(f"Object Storage {label} ACL response is malformed")
    owner = value.get("Owner")
    owner_id = owner.get("ID") if isinstance(owner, Mapping) else None
    grants = value.get("Grants")
    if not isinstance(owner_id, str) or not owner_id or not isinstance(grants, list) or not grants:
        _fail(f"Object Storage {label} ACL response is malformed")
    if expected_owner_id is not None and owner_id != expected_owner_id:
        _fail(f"Object Storage {label} ACL owner differs from the private bucket owner")
    for grant in grants:
        if not isinstance(grant, Mapping):
            _fail(f"Object Storage {label} ACL response is malformed")
        grantee = grant.get("Grantee")
        if (
            not isinstance(grantee, Mapping)
            or grantee.get("Type") != "CanonicalUser"
            or grantee.get("ID") != owner_id
            or grantee.get("URI") is not None
            or grantee.get("EmailAddress") is not None
            or grant.get("Permission") != "FULL_CONTROL"
        ):
            _fail(f"Object Storage {label} ACL grants access outside its canonical owner")
    return owner_id


def _assert_private_versioned_bucket(client: Any, *, endpoint: str, region: str, bucket: str) -> str:
    """Prove the narrow Object Storage privacy contract before any use.

    The Public Access Block fallback is intentionally restricted to the one
    audited Arvan endpoint/region.  Every caller supplies the endpoint/region
    that it has already decoded from a validated control object; validating
    again here makes the provider-specific exception non-transferrable.
    """

    endpoint, region = _validate_endpoint(endpoint, region)
    bucket = _require_pattern(bucket, field="bucket", pattern=BUCKET_RE)
    try:
        versioning = client.get_bucket_versioning(Bucket=bucket)
    except Exception as exc:
        raise EmergencySourceDeliveryError("Object Storage bucket versioning cannot be verified") from exc
    if not isinstance(versioning, Mapping) or versioning.get("Status") != "Enabled":
        _fail("Object Storage bucket versioning must be Enabled")
    try:
        try:
            public_access = client.get_public_access_block(Bucket=bucket)
        except Exception as exc:
            if not (
                endpoint == _ARVAN_NO_PUBLIC_ACCESS_BLOCK_ENDPOINT
                and region == _ARVAN_NO_PUBLIC_ACCESS_BLOCK_REGION
                and _s3_error_code(exc) == _ARVAN_NO_PUBLIC_ACCESS_BLOCK_CODE
            ):
                raise
            # An explicit absence at the audited Arvan capability endpoint is
            # not proof of privacy by itself.  Continue into the independent
            # no-policy and owner-only ACL checks below.
        else:
            configuration = public_access.get("PublicAccessBlockConfiguration") if isinstance(public_access, Mapping) else None
            required = {"BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"}
            if not isinstance(configuration, Mapping) or any(configuration.get(item) is not True for item in required):
                _fail("Object Storage bucket public-access block is not fully enabled")
        try:
            client.get_bucket_policy(Bucket=bucket)
        except Exception as exc:
            if _s3_error_code(exc) != "NoSuchBucketPolicy":
                raise
        else:
            _fail("Object Storage private bucket must not have a bucket policy")
        return _require_owner_only_acl(client.get_bucket_acl(Bucket=bucket), label="bucket")
    except EmergencySourceDeliveryError:
        raise
    except Exception as exc:
        raise EmergencySourceDeliveryError("Object Storage bucket privacy cannot be verified") from exc


def _assert_private_object_acl(
    client: Any, *, bucket: str, key: str, version_id: str, label: str, expected_owner_id: str
) -> None:
    """Prove a received object did not acquire a public/object ACL grant."""

    try:
        acl = client.get_object_acl(Bucket=bucket, Key=key, VersionId=version_id)
    except Exception as exc:
        raise EmergencySourceDeliveryError(f"Object Storage {label} ACL cannot be verified") from exc
    _require_owner_only_acl(acl, label=label, expected_owner_id=expected_owner_id)


def _require_version_id(value: object, *, field: str) -> str:
    return _require_pattern(value, field=field, pattern=VERSION_ID_RE)


def _assert_key_unused(client: Any, *, bucket: str, key: str) -> None:
    """Reject all historic versions/delete-markers before a create-only PUT."""

    try:
        listing = client.list_object_versions(Bucket=bucket, Prefix=key)
        if not isinstance(listing, Mapping) or listing.get("IsTruncated") is True:
            _fail("Object Storage version history cannot be completely verified")
        versions = listing.get("Versions") or []
        delete_markers = listing.get("DeleteMarkers") or []
        if not isinstance(versions, list) or not isinstance(delete_markers, list):
            _fail("Object Storage version history cannot be verified")
        for entry in [*versions, *delete_markers]:
            if not isinstance(entry, Mapping):
                _fail("Object Storage version history cannot be verified")
            if entry.get("Key") == key:
                _fail("refusing to publish over an existing Object Storage object version")
        try:
            client.head_object(Bucket=bucket, Key=key)
        except Exception as exc:
            if _s3_error_code(exc) not in _S3_NOT_FOUND_CODES:
                raise
        else:
            _fail("refusing to publish over an existing Object Storage object")
    except EmergencySourceDeliveryError:
        raise
    except Exception as exc:
        raise EmergencySourceDeliveryError("Object Storage key availability cannot be verified") from exc


def _assert_immutable_object_available(
    client: Any,
    *,
    bucket: str,
    key: str,
    version_id: str,
    expected_bytes: int,
    expected_owner_id: str,
    label: str,
) -> None:
    """Recheck one immutable object before issuing a presigned GET."""

    try:
        head = client.head_object(Bucket=bucket, Key=key, VersionId=version_id)
    except Exception as exc:
        raise EmergencySourceDeliveryError(f"Object Storage {label} availability cannot be verified") from exc
    if (
        not isinstance(head, Mapping)
        or head.get("ContentLength") != expected_bytes
        or _require_version_id(head.get("VersionId"), field=f"Object Storage {label} head VersionId") != version_id
    ):
        _fail(f"Object Storage {label} immutable head differs from the sealed descriptor")
    _reject_provider_side_encryption(head)
    _assert_private_object_acl(
        client,
        bucket=bucket,
        key=key,
        version_id=version_id,
        label=label,
        expected_owner_id=expected_owner_id,
    )


def _readback_exact_object(
    client: Any,
    *,
    bucket: str,
    key: str,
    version_id: str,
    expected_sha256: str,
    expected_bytes: int,
    expected_metadata: Mapping[str, str],
    label: str,
    expected_owner_id: str,
) -> None:
    try:
        head = client.head_object(Bucket=bucket, Key=key, VersionId=version_id)
    except Exception as exc:
        raise EmergencySourceDeliveryError("Object Storage exact-version head readback failed") from exc
    if (
        not isinstance(head, Mapping)
        or head.get("ContentLength") != expected_bytes
        or _require_version_id(head.get("VersionId"), field="Object Storage readback head VersionId") != version_id
        or head.get("Metadata") != dict(expected_metadata)
    ):
        _fail(f"Object Storage readback head differs from the sealed {label}")
    _reject_provider_side_encryption(head)
    try:
        response = client.get_object(Bucket=bucket, Key=key, VersionId=version_id)
    except Exception as exc:
        raise EmergencySourceDeliveryError("Object Storage exact-version readback failed") from exc
    if not isinstance(response, Mapping):
        _fail("Object Storage readback response is malformed")
    if _require_version_id(response.get("VersionId"), field="Object Storage readback VersionId") != version_id:
        _fail("Object Storage readback selected a different version")
    _reject_provider_side_encryption(response)
    if response.get("Metadata") != dict(expected_metadata):
        _fail(f"Object Storage readback metadata differs from the sealed {label}")
    if response.get("ContentLength") != expected_bytes:
        _fail(f"Object Storage readback {label} content length differs from the sealed descriptor")
    body = response.get("Body")
    if body is None or not hasattr(body, "read"):
        _fail("Object Storage readback has no readable body")
    digest = hashlib.sha256()
    observed = 0
    try:
        while True:
            chunk = body.read(DOWNLOAD_CHUNK_BYTES)
            if not chunk:
                break
            if not isinstance(chunk, bytes) or observed + len(chunk) > expected_bytes:
                _fail(f"Object Storage readback {label} exceeds its sealed bound")
            observed += len(chunk)
            digest.update(chunk)
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()
    if observed != expected_bytes or digest.hexdigest() != expected_sha256:
        _fail(f"Object Storage readback {label} differs from its sealed descriptor")
    _assert_private_object_acl(
        client,
        bucket=bucket,
        key=key,
        version_id=version_id,
        label=label,
        expected_owner_id=expected_owner_id,
    )


def _upload_create_only_object(
    client: Any,
    *,
    bucket: str,
    key: str,
    payload: bytes,
    expected_sha256: str,
    expected_bytes: int,
    metadata: Mapping[str, str],
    label: str,
) -> str:
    if (hashlib.sha256(payload).hexdigest(), len(payload)) != (expected_sha256, expected_bytes):
        _fail(f"local sealed {label} changed before Object Storage upload")
    try:
        response = client.put_object(
            Bucket=bucket,
            Key=key,
            Body=payload,
            ContentType="application/octet-stream",
            IfNoneMatch="*",
            ACL="private",
            Metadata=dict(metadata),
        )
    except Exception as exc:
        raise EmergencySourceDeliveryError(f"create-only Object Storage {label} upload failed") from exc
    if not isinstance(response, Mapping):
        _fail(f"Object Storage {label} upload response is malformed")
    _reject_provider_side_encryption(response)
    return _require_version_id(response.get("VersionId"), field=f"Object Storage {label} upload VersionId")


def _control_artifact_payload(
    *, repository: Path, kind: str
) -> tuple[ControllerToolIdentity, bytes, str, str]:
    if kind == "anchor":
        artifact = inspect_anchor_artifact(repository=repository)
        relative_path, layout = ANCHOR_RELATIVE_PATH, ANCHOR_OBJECT_LAYOUT
    elif kind == "installer":
        artifact = inspect_installer_artifact(repository=repository)
        relative_path, layout = INSTALLER_RELATIVE_PATH, INSTALLER_OBJECT_LAYOUT
    else:
        _fail("control artifact kind is unsupported")
    payload = _read_stable_regular(
        _require_safe_directory(repository, label="controller tool repository", private=False) / relative_path,
        label=f"{kind} control artifact",
        maximum_bytes=2 * 1024 * 1024,
        private=False,
    )
    if (hashlib.sha256(payload).hexdigest(), len(payload)) != (artifact.sha256, artifact.bytes):
        _fail("committed control artifact changed after provenance inspection")
    return artifact, payload, relative_path, layout


def publish_control_artifact(
    *,
    repository: Path,
    kind: str,
    endpoint: str,
    region: str,
    bucket: str,
    prefix: str,
    credentials: Mapping[str, str],
    approval_output: Path,
    client: Any | None = None,
) -> dict[str, Any]:
    """Create-only publish a first-stage installer/anchor and its receipt.

    The generated receipt is *not* an authorization to write remote trust
    state.  A human/root ceremony must independently review and create-only
    place it on WA-FI.  This publisher never emits artifact bytes over SSH.
    """

    _require_root_execution()
    _require_direct_object_storage_environment()
    endpoint, region = _validate_endpoint(endpoint, region)
    bucket = _require_pattern(bucket, field="bucket", pattern=BUCKET_RE)
    prefix = _validate_prefix(prefix)
    approval_output = _require_absolute(approval_output, field="control artifact approval output")
    _require_safe_directory(approval_output.parent, label="control artifact approval output parent", private=True)
    if approval_output.exists() or approval_output.is_symlink():
        _fail("refusing to overwrite control artifact approval output")
    artifact, payload, _relative_path, layout = _control_artifact_payload(repository=repository, kind=kind)
    controller_tool = inspect_controller_artifact(repository=repository)
    if (artifact.revision, artifact.tree) != (controller_tool.revision, controller_tool.tree):
        _fail("control artifact and receiver controller do not share one control revision")
    key = _control_artifact_key(prefix=prefix, layout=layout, artifact=artifact)
    if client is None:
        client = _create_s3_client({"endpoint": endpoint, "region": region}, credentials)
    owner_id = _assert_private_versioned_bucket(client, endpoint=endpoint, region=region, bucket=bucket)
    _assert_key_unused(client, bucket=bucket, key=key)
    metadata = _metadata(kind=f"webapp-fi-emergency-source-{kind}", sha256=artifact.sha256)
    version_id = _upload_create_only_object(
        client,
        bucket=bucket,
        key=key,
        payload=payload,
        expected_sha256=artifact.sha256,
        expected_bytes=artifact.bytes,
        metadata=metadata,
        label=f"WebApp-FI source {kind}",
    )
    if kind == "anchor":
        approval = _build_anchor_approval(
            endpoint=endpoint,
            region=region,
            bucket=bucket,
            prefix=prefix,
            artifact=artifact,
            controller_tool=controller_tool,
            version_id=version_id,
        )
    else:
        approval = _build_installer_approval(
            endpoint=endpoint,
            region=region,
            bucket=bucket,
            prefix=prefix,
            artifact=artifact,
            controller_tool=controller_tool,
            version_id=version_id,
        )
    normalized = _validate_control_approval(approval, kind=kind)
    _readback_exact_object(
        client,
        bucket=bucket,
        key=key,
        version_id=version_id,
        expected_sha256=artifact.sha256,
        expected_bytes=artifact.bytes,
        expected_metadata=metadata,
        label=f"WebApp-FI source {kind}",
        expected_owner_id=owner_id,
    )
    _write_create_only(
        approval_output,
        canonical_json_bytes(normalized) + b"\n",
        label="control artifact independent approval output",
    )
    return normalized


def _load_control_approval(path: Path, *, kind: str) -> dict[str, Any]:
    payload = _read_stable_regular(
        path,
        label="control artifact independent approval",
        maximum_bytes=MAX_DESCRIPTOR_BYTES,
        private=True,
    )
    return _validate_control_approval(
        _parse_canonical_json(payload, label="control artifact independent approval", maximum_bytes=MAX_DESCRIPTOR_BYTES),
        kind=kind,
    )


def first_installer_placement_contract(*, approval: Mapping[str, Any]) -> dict[str, Any]:
    """Render the bounded non-executing root-console ceremony for installer #1.

    No remote program is asked to bless its own first execution.  The
    operator obtains the URL through ``issue-control-artifact-get``, uses only
    standard host tools to download *without execution*, compares the exact
    immutable approval hash/size, then hard-links each staged file into its
    final path create-only.  The final self-check runs only after both files
    are already pinned by this independent receipt.
    """

    normalized = _validate_control_approval(approval, kind="installer")
    receipt = canonical_json_bytes(normalized) + b"\n"
    receipt_sha256 = hashlib.sha256(receipt).hexdigest()
    installer_path = str(normalized["installer_path"])
    installer_parent = str(Path(installer_path).parent)
    trust_root = "/etc/trading-bot-three-site/trust"
    receipt_path = trust_root + "/webapp-fi-emergency-source-installer-approval.json"
    stage = installer_parent + "/.install_webapp_fi_emergency_source_anchor.py.download"
    receipt_stage = trust_root + "/.webapp-fi-emergency-source-installer-approval.json.control"
    return {
        "schema": "gold-trade-webapp-fi-emergency-source-first-installer-placement-contract-v1",
        "status": "planned-non-authorizing",
        "installer": {
            "path": installer_path,
            "sha256": normalized["installer_sha256"],
            "bytes": normalized["installer_bytes"],
            "object_key": normalized["object_key"],
            "artifact_version_id": normalized["artifact_version_id"],
            "controller_revision": normalized["controller_revision"],
            "controller_tree": normalized["controller_tree"],
            "controller_tool_sha256": normalized["controller_tool_sha256"],
            "controller_tool_bytes": normalized["controller_tool_bytes"],
        },
        "receipt": {
            "path": receipt_path,
            "sha256": receipt_sha256,
            "bytes": len(receipt),
            "transport": "trusted-control-fields-only-not-artifact-bytes",
        },
        "url": {
            "source": "issue-control-artifact-get installer --apply",
            "required_binding": "https fixed Arvan endpoint + exact object path + exact VersionId",
            "persistence": "forbidden",
        },
        "root_console_transport": {
            "environment": "env -i with fixed PATH, no HOME/XDG/CURL config",
            "clears": "proxy, CA override, AWS/Boto, curl, Python, and shell helper environment",
            "curl": "absolute /usr/bin/curl, -q, --noproxy '*', direct HTTPS only, redirect failure",
            "interpreter": "absolute /usr/bin/python3 -I -B under the same clean environment",
        },
        "recovery": "if either final path already exists, stop; do not replace/delete it—publish a new controller artifact or use a new source campaign as applicable",
        "commands": [
            "/usr/bin/test -x /usr/bin/env && /usr/bin/test -x /usr/bin/curl && /usr/bin/test -x /usr/bin/python3 && /usr/bin/test -x /usr/bin/install && /usr/bin/test -x /usr/bin/sha256sum && /usr/bin/test -x /usr/bin/wc && /usr/bin/test -x /usr/bin/printf && /usr/bin/test -x /usr/bin/ln && /usr/bin/test -x /usr/bin/rm && /usr/bin/test -x /usr/bin/sync",
            "/usr/bin/install -d -o root -g root -m 0700 /etc/trading-bot-three-site/trust " + installer_parent,
            "/usr/bin/sync -f /etc/trading-bot-three-site/trust " + installer_parent,
            "umask 077; /usr/bin/test ! -e " + installer_path + " && /usr/bin/test ! -L " + installer_path + " && /usr/bin/test ! -e " + receipt_path + " && /usr/bin/test ! -L " + receipt_path,
            ROOT_CONSOLE_CLEAN_ENV + " /usr/bin/curl -q --noproxy '*' --fail --silent --show-error --request GET --proto '=https' --proto-redir '=https' --location --max-redirs 0 --connect-timeout 20 --max-time 180 --output " + stage + " \"$VERSION_BOUND_URL\"",
            "/usr/bin/test \"$(/usr/bin/wc -c < " + stage + ")\" -eq " + str(normalized["installer_bytes"]) + " && /usr/bin/printf '%s  %s\\n' '" + str(normalized["installer_sha256"]) + "' " + stage + " | /usr/bin/sha256sum -c -",
            "# create " + receipt_stage + " only from the canonical controller approval control file; verify SHA-256 " + receipt_sha256 + " and byte length " + str(len(receipt)),
            "/usr/bin/ln " + stage + " " + installer_path + " && /usr/bin/rm " + stage + " && /usr/bin/ln " + receipt_stage + " " + receipt_path + " && /usr/bin/rm " + receipt_stage,
            "/usr/bin/sync -f " + installer_path + " " + receipt_path + " " + installer_parent + " /etc/trading-bot-three-site/trust",
            ROOT_CONSOLE_CLEAN_ENV + " /usr/bin/python3 -I -B " + installer_path + " --verify-installed-installer",
        ],
        "artifact_transport": "private-versioned-arvan-direct-get-only",
        "ssh_artifact_bytes": "forbidden",
        "execution_before_hash_verification": "forbidden",
    }


def pinned_anchor_installation_contract(*, approval: Mapping[str, Any]) -> dict[str, Any]:
    """Render the bounded control-record + direct-GET anchor installation plan."""

    normalized = _validate_control_approval(approval, kind="anchor")
    receipt = canonical_json_bytes(normalized) + b"\n"
    receipt_sha256 = hashlib.sha256(receipt).hexdigest()
    trust_root = "/etc/trading-bot-three-site/trust"
    receipt_path = trust_root + "/webapp-fi-emergency-source-anchor-approval.json"
    stage = trust_root + "/.webapp-fi-emergency-source-anchor-approval.json.control"
    phrase = "install-webapp-fi-emergency-source-anchor:" + str(normalized["anchor_sha256"]) + ":" + str(normalized["artifact_version_id"])
    return {
        "schema": "gold-trade-webapp-fi-emergency-source-pinned-anchor-installation-contract-v1",
        "status": "planned-non-authorizing",
        "anchor": {
            "path": normalized["anchor_path"],
            "sha256": normalized["anchor_sha256"],
            "bytes": normalized["anchor_bytes"],
            "object_key": normalized["object_key"],
            "artifact_version_id": normalized["artifact_version_id"],
            "controller_revision": normalized["controller_revision"],
            "controller_tree": normalized["controller_tree"],
            "controller_tool_sha256": normalized["controller_tool_sha256"],
            "controller_tool_bytes": normalized["controller_tool_bytes"],
        },
        "receipt": {
            "path": receipt_path,
            "sha256": receipt_sha256,
            "bytes": len(receipt),
            "transport": "trusted-control-fields-only-not-artifact-bytes",
        },
        "url": {
            "source": "issue-control-artifact-get anchor --apply",
            "required_binding": "https fixed Arvan endpoint + exact object path + exact VersionId",
            "persistence": "forbidden",
        },
        "installer_confirmation": phrase,
        "commands": [
            "# create " + stage + " only from the canonical controller approval control file; verify SHA-256 " + receipt_sha256 + " and byte length " + str(len(receipt)),
            "/usr/bin/test ! -e " + receipt_path + " && /usr/bin/test ! -L " + receipt_path + " && /usr/bin/ln " + stage + " " + receipt_path + " && /usr/bin/rm " + stage + " && /usr/bin/sync -f " + receipt_path + " " + trust_root,
            "/usr/bin/printf '%s\\n' \"$VERSION_BOUND_URL\" | " + ROOT_CONSOLE_CLEAN_ENV + " /usr/bin/python3 -I -B " + PINNED_INSTALLER_PATH + " --apply --confirm '" + phrase + "'",
        ],
        "artifact_transport": "private-versioned-arvan-direct-get-only",
        "ssh_artifact_bytes": "forbidden",
        "anchor_execution_during_install": "forbidden",
    }


def pinned_signer_provisioning_contract(
    *,
    anchor_approval: Mapping[str, Any],
    signer_approval: Mapping[str, Any],
    signing_public_key: Any,
) -> dict[str, Any]:
    """Render the separate local trust ceremony for the descriptor signer."""

    anchor = _validate_control_approval(anchor_approval, kind="anchor")
    signer = _validate_pinned_signer_approval(signer_approval, anchor_approval=anchor)
    if signer_key_id(signing_public_key) != signer["signer_key_id"]:
        _fail("signing public key does not match the independently approved signer fingerprint")
    public_payload = base64.b64encode(_public_key_bytes(signing_public_key)) + b"\n"
    signer_payload = canonical_json_bytes(signer) + b"\n"
    trust_root = "/etc/trading-bot-three-site/trust"
    candidate = trust_root + "/webapp-fi-emergency-source-signing-public.candidate"
    receipt = trust_root + "/webapp-fi-emergency-source-signer-approval.json"
    candidate_stage = trust_root + "/.webapp-fi-emergency-source-signing-public.candidate.control"
    receipt_stage = trust_root + "/.webapp-fi-emergency-source-signer-approval.json.control"
    phrase = "pin-webapp-fi-emergency-source-signer:" + signer["signer_key_id"]
    return {
        "schema": "gold-trade-webapp-fi-emergency-source-pinned-signer-provisioning-contract-v1",
        "status": "planned-non-authorizing",
        "anchor_sha256": anchor["anchor_sha256"],
        "signer_key_id": signer["signer_key_id"],
        "candidate": {
            "path": candidate,
            "sha256": hashlib.sha256(public_payload).hexdigest(),
            "bytes": len(public_payload),
            "transport": "trusted-control-fields-only",
        },
        "receipt": {
            "path": receipt,
            "sha256": hashlib.sha256(signer_payload).hexdigest(),
            "bytes": len(signer_payload),
            "transport": "trusted-control-fields-only",
        },
        "confirmation": phrase,
        "commands": [
            "# create " + candidate_stage + " only from the approved public-key control file; verify SHA-256 " + hashlib.sha256(public_payload).hexdigest() + " and byte length " + str(len(public_payload)),
            "# create " + receipt_stage + " only from the canonical signer-approval control file; verify SHA-256 " + hashlib.sha256(signer_payload).hexdigest() + " and byte length " + str(len(signer_payload)),
            "/usr/bin/test ! -e " + candidate + " && /usr/bin/test ! -L " + candidate + " && /usr/bin/test ! -e " + receipt + " && /usr/bin/test ! -L " + receipt + " && /usr/bin/ln " + candidate_stage + " " + candidate + " && /usr/bin/rm " + candidate_stage + " && /usr/bin/ln " + receipt_stage + " " + receipt + " && /usr/bin/rm " + receipt_stage + " && /usr/bin/sync -f " + candidate + " " + receipt + " " + trust_root,
            ROOT_CONSOLE_CLEAN_ENV + " /usr/bin/python3 -I -B " + PINNED_ANCHOR_PATH + " provision-pinned-signer --apply --confirm '" + phrase + "'",
        ],
        "artifact_transport": "not-applicable-public-key-and-approval-control-only",
        "ssh_artifact_bytes": "forbidden",
        "signer_private_key_material": "not-transferred",
    }


def issue_control_artifact_get(*, approval: Mapping[str, Any], kind: str, client: Any, ttl_seconds: int) -> str:
    _require_direct_object_storage_environment()
    normalized = _validate_control_approval(approval, kind=kind)
    owner_id = _assert_private_versioned_bucket(
        client,
        endpoint=str(normalized["endpoint"]),
        region=str(normalized["region"]),
        bucket=str(normalized["bucket"]),
    )
    bytes_field = "anchor_bytes" if kind == "anchor" else "installer_bytes"
    _assert_immutable_object_available(
        client,
        bucket=str(normalized["bucket"]),
        key=str(normalized["object_key"]),
        version_id=str(normalized["artifact_version_id"]),
        expected_bytes=int(normalized[bytes_field]),
        expected_owner_id=owner_id,
        label=f"WebApp-FI source {kind}",
    )
    return _issue_version_bound_get(
        endpoint=str(normalized["endpoint"]),
        bucket=str(normalized["bucket"]),
        object_key=str(normalized["object_key"]),
        version_id=str(normalized["artifact_version_id"]),
        client=client,
        ttl_seconds=ttl_seconds,
    )


def publish_prepared_plan(
    *, plan: Mapping[str, Any], private_key: Any, credentials: Mapping[str, str], descriptor_output: Path, client: Any | None = None
) -> VerifiedDescriptor:
    """Perform the sole controller S3 PUT/readback and write one signed descriptor."""

    _require_root_execution()
    _require_direct_object_storage_environment()
    normalized = validate_prepared_plan(plan)
    descriptor_output = _require_absolute(descriptor_output, field="signed source descriptor output")
    _require_safe_directory(descriptor_output.parent, label="signed source descriptor output parent", private=True)
    if descriptor_output.exists() or descriptor_output.is_symlink():
        _fail("refusing to overwrite signed source descriptor output")
    ciphertext_path = Path(str(normalized["ciphertext"]["path"]))
    bootstrap_path = Path(str(normalized["bootstrap"]["path"]))
    actual_hash, actual_bytes = _hash_regular(
        ciphertext_path,
        label="encrypted Git bundle",
        maximum_bytes=MAX_GIT_BUNDLE_BYTES + MAX_CIPHERTEXT_OVERHEAD_BYTES,
        private=True,
    )
    if (actual_hash, actual_bytes) != (normalized["ciphertext"]["sha256"], normalized["ciphertext"]["bytes"]):
        _fail("encrypted Git bundle changed after preparation")
    bootstrap_hash, bootstrap_bytes = _hash_regular(
        bootstrap_path, label="receiver bootstrap artifact", maximum_bytes=MAX_BOOTSTRAP_BYTES, private=True
    )
    if (bootstrap_hash, bootstrap_bytes) != (normalized["bootstrap"]["sha256"], normalized["bootstrap"]["bytes"]):
        _fail("receiver bootstrap artifact changed after preparation")
    verify_receiver_bootstrap(
        artifact=bootstrap_path, controller_tool=_controller_tool_from_value(normalized["controller_tool"])
    )
    if client is None:
        client = _create_s3_client(normalized, credentials)
    owner_id = _assert_private_versioned_bucket(
        client,
        endpoint=str(normalized["endpoint"]),
        region=str(normalized["region"]),
        bucket=str(normalized["bucket"]),
    )
    ciphertext = _read_stable_regular(
        ciphertext_path,
        label="encrypted Git bundle",
        maximum_bytes=MAX_GIT_BUNDLE_BYTES + MAX_CIPHERTEXT_OVERHEAD_BYTES,
        private=True,
    )
    bootstrap_payload = _read_stable_regular(
        bootstrap_path, label="receiver bootstrap artifact", maximum_bytes=MAX_BOOTSTRAP_BYTES, private=True
    )
    bootstrap_metadata = _metadata(
        kind="receiver-bootstrap", sha256=str(normalized["bootstrap"]["sha256"])
    )
    _assert_key_unused(client, bucket=str(normalized["bucket"]), key=str(normalized["bootstrap"]["object_key"]))
    bootstrap_version_id = _upload_create_only_object(
        client,
        bucket=str(normalized["bucket"]),
        key=str(normalized["bootstrap"]["object_key"]),
        payload=bootstrap_payload,
        expected_sha256=str(normalized["bootstrap"]["sha256"]),
        expected_bytes=int(normalized["bootstrap"]["bytes"]),
        metadata=bootstrap_metadata,
        label="receiver bootstrap",
    )
    source_metadata = _metadata(
        kind="encrypted-source-git-bundle",
        sha256=str(normalized["ciphertext"]["sha256"]),
        recipient_id=str(normalized["recipient_key_id"]),
    )
    _assert_key_unused(client, bucket=str(normalized["bucket"]), key=str(normalized["object_key"]))
    source_version_id = _upload_create_only_object(
        client,
        bucket=str(normalized["bucket"]),
        key=str(normalized["object_key"]),
        payload=ciphertext,
        expected_sha256=str(normalized["ciphertext"]["sha256"]),
        expected_bytes=int(normalized["ciphertext"]["bytes"]),
        metadata=source_metadata,
        label="encrypted source Git bundle",
    )
    unsigned = _unsigned_descriptor_from_prepared(
        normalized, source_version_id=source_version_id, bootstrap_version_id=bootstrap_version_id
    )
    signed = sign_descriptor(unsigned, private_key=private_key)
    _readback_exact_object(
        client,
        bucket=signed["bucket"],
        key=signed["bootstrap"]["object_key"],
        version_id=signed["bootstrap"]["version_id"],
        expected_sha256=signed["bootstrap"]["sha256"],
        expected_bytes=signed["bootstrap"]["bytes"],
        expected_metadata=bootstrap_metadata,
        label="receiver bootstrap",
        expected_owner_id=owner_id,
    )
    _readback_exact_object(
        client,
        bucket=signed["bucket"],
        key=signed["object_key"],
        version_id=signed["version_id"],
        expected_sha256=signed["ciphertext"]["sha256"],
        expected_bytes=signed["ciphertext"]["bytes"],
        expected_metadata=source_metadata,
        label="encrypted source Git bundle",
        expected_owner_id=owner_id,
    )
    _write_create_only(
        descriptor_output, canonical_json_bytes(signed) + b"\n", label="signed source descriptor output"
    )
    return verify_descriptor(signed, public_key=private_key.public_key())


def validate_version_bound_get(
    *, url: str, endpoint: str, bucket: str, object_key: str, version_id: str
) -> str:
    if not isinstance(url, str) or not url or len(url.encode("utf-8")) > MAX_URL_BYTES:
        _fail("version-bound Object Storage URL is invalid")
    try:
        parsed = urlsplit(url)
        parsed_endpoint = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise EmergencySourceDeliveryError("version-bound Object Storage URL is malformed") from exc
    endpoint_host = parsed_endpoint.hostname
    allowed_hosts = {endpoint_host, f"{bucket}.{endpoint_host}"}
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        _fail("version-bound Object Storage URL endpoint is not allowlisted")
    object_path = quote(object_key, safe="/")
    expected_path = "/" + object_path if parsed.hostname == f"{bucket}.{endpoint_host}" else "/" + quote(bucket, safe="") + "/" + object_path
    if parsed.path != expected_path:
        _fail("version-bound Object Storage URL selects a different object")
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise EmergencySourceDeliveryError("version-bound Object Storage URL query is malformed") from exc
    if set(query) - (_PRESIGNED_REQUIRED | _PRESIGNED_OPTIONAL) or not _PRESIGNED_REQUIRED.issubset(query) or any(
        len(items) != 1 for items in query.values()
    ):
        _fail("version-bound Object Storage URL query fields are unsupported")
    if query["X-Amz-Algorithm"][0] != "AWS4-HMAC-SHA256" or query["X-Amz-SignedHeaders"][0] != "host":
        _fail("version-bound Object Storage URL signature contract is unsupported")
    if query["versionId"][0] != version_id:
        _fail("version-bound Object Storage URL VersionId differs from the signed descriptor")
    try:
        ttl = int(query["X-Amz-Expires"][0], 10)
    except ValueError as exc:
        raise EmergencySourceDeliveryError("version-bound Object Storage URL expiry is invalid") from exc
    if not MIN_PRESIGNED_TTL_SECONDS <= ttl <= MAX_PRESIGNED_TTL_SECONDS:
        _fail("version-bound Object Storage URL expiry is outside the fixed bound")
    return url


def _validate_presigned_get(*, url: str, descriptor: VerifiedDescriptor) -> str:
    return validate_version_bound_get(
        url=url,
        endpoint=descriptor.endpoint,
        bucket=descriptor.bucket,
        object_key=descriptor.object_key,
        version_id=descriptor.version_id,
    )


def _issue_version_bound_get(
    *, endpoint: str, bucket: str, object_key: str, version_id: str, client: Any, ttl_seconds: int
) -> str:
    ttl_seconds = _require_positive_int(ttl_seconds, field="ttl_seconds", maximum=MAX_PRESIGNED_TTL_SECONDS)
    if ttl_seconds < MIN_PRESIGNED_TTL_SECONDS:
        _fail("ttl_seconds is below the fixed version-bound GET minimum")
    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": object_key, "VersionId": version_id},
            ExpiresIn=ttl_seconds,
            HttpMethod="GET",
        )
    except Exception as exc:
        raise EmergencySourceDeliveryError("version-bound Object Storage GET cannot be issued") from exc
    return validate_version_bound_get(
        url=str(url), endpoint=endpoint, bucket=bucket, object_key=object_key, version_id=version_id
    )


def issue_version_bound_gets(*, descriptor: VerifiedDescriptor, client: Any, ttl_seconds: int) -> dict[str, str]:
    """Issue the only two transient URLs; callers must not persist this map."""

    _require_direct_object_storage_environment()
    owner_id = _assert_private_versioned_bucket(
        client,
        endpoint=descriptor.endpoint,
        region=descriptor.region,
        bucket=descriptor.bucket,
    )
    _assert_immutable_object_available(
        client,
        bucket=descriptor.bucket,
        key=descriptor.bootstrap_object_key,
        version_id=descriptor.bootstrap_version_id,
        expected_bytes=descriptor.bootstrap_bytes,
        expected_owner_id=owner_id,
        label="receiver bootstrap",
    )
    _assert_immutable_object_available(
        client,
        bucket=descriptor.bucket,
        key=descriptor.object_key,
        version_id=descriptor.version_id,
        expected_bytes=descriptor.ciphertext_bytes,
        expected_owner_id=owner_id,
        label="encrypted source Git bundle",
    )

    return {
        "bootstrap_url": _issue_version_bound_get(
            endpoint=descriptor.endpoint,
            bucket=descriptor.bucket,
            object_key=descriptor.bootstrap_object_key,
            version_id=descriptor.bootstrap_version_id,
            client=client,
            ttl_seconds=ttl_seconds,
        ),
        "source_url": _issue_version_bound_get(
            endpoint=descriptor.endpoint,
            bucket=descriptor.bucket,
            object_key=descriptor.object_key,
            version_id=descriptor.version_id,
            client=client,
            ttl_seconds=ttl_seconds,
        ),
    }


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        raise EmergencySourceDeliveryError("Object Storage source download was unexpectedly redirected")


def _download_exact_ciphertext(*, url: str, descriptor: VerifiedDescriptor, output: Path) -> tuple[str, int]:
    output = _require_absolute(output, field="downloaded source ciphertext")
    _require_safe_directory(output.parent, label="downloaded source ciphertext parent", private=True)
    if output.exists() or output.is_symlink():
        _fail("refusing to overwrite downloaded source ciphertext")
    free = shutil.disk_usage(output.parent).free
    if free < descriptor.ciphertext_bytes + descriptor.bundle_bytes + DISK_HEADROOM_BYTES:
        _fail("insufficient disk space for the sealed source transfer")
    descriptor_fd: int | None = None
    try:
        descriptor_fd = os.open(
            output,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        opener = build_opener(ProxyHandler({}), _RejectRedirects(), HTTPSHandler(context=ssl.create_default_context()))
        request = Request(url, headers={"User-Agent": "gold-trade-webapp-fi-emergency-source/1"}, method="GET")
        digest = hashlib.sha256()
        size = 0
        try:
            with opener.open(request, timeout=180) as response:
                if getattr(response, "status", 200) != 200 or response.geturl() != url:
                    _fail("Object Storage source response differs from its signed request")
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes) or size + len(chunk) > descriptor.ciphertext_bytes:
                        _fail("Object Storage source ciphertext exceeds its signed bound")
                    view = memoryview(chunk)
                    while view:
                        written = os.write(descriptor_fd, view)
                        if written <= 0:
                            raise OSError("short source ciphertext write")
                        view = view[written:]
                    size += len(chunk)
                    digest.update(chunk)
        except (HTTPError, URLError, OSError, ssl.SSLError) as exc:
            raise EmergencySourceDeliveryError("Object Storage source download failed") from exc
        os.fsync(descriptor_fd)
        observed = digest.hexdigest()
        if size != descriptor.ciphertext_bytes or observed != descriptor.ciphertext_sha256:
            _fail("Object Storage source ciphertext differs from the signed descriptor")
        return observed, size
    except Exception:
        # This is a fresh private staging file only; do not publish partial
        # ciphertext as a candidate checkout.
        with contextlib.suppress(OSError):
            output.unlink()
        raise
    finally:
        if descriptor_fd is not None:
            os.close(descriptor_fd)


def _recipient_from_identity(*, identity_file: Path, age_keygen_binary: Path) -> str:
    identity_file = _require_safe_regular(identity_file, label="WebApp-FI age identity", maximum_bytes=256 * 1024, private=True)
    age_keygen_binary = _require_age_binary(age_keygen_binary, label="age-keygen binary")
    try:
        completed = subprocess.run(
            [str(age_keygen_binary), "-y", str(identity_file)],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            env=_git_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencySourceDeliveryError("age identity public recipient check could not start") from exc
    if completed.returncode != 0:
        _fail("age identity public recipient check failed")
    return _require_pattern(completed.stdout.strip(), field="derived age recipient", pattern=AGE_RECIPIENT_RE)


def bootstrap_campaign_age_identity(*, campaign_id: str, age_keygen_binary: Path) -> dict[str, str]:
    """Create one fresh campaign-local FI identity without exposing its private key."""

    _require_root_execution()
    campaign_id = _require_pattern(campaign_id, field="campaign_id", pattern=CAMPAIGN_RE)
    age_keygen_binary = _require_age_binary(age_keygen_binary, label="age-keygen binary")
    root = _require_safe_directory(FI_CAMPAIGN_IDENTITY_ROOT, label="WebApp-FI campaign identity root", private=True)
    campaign = _ensure_private_child(root, campaign_id, label="WebApp-FI campaign identity directory")
    webapp_fi = _ensure_private_child(campaign, "webapp-fi", label="WebApp-FI campaign identity subtree")
    identity = webapp_fi / "emergency-source.agekey"
    if identity.exists() or identity.is_symlink():
        _fail("campaign-local WebApp-FI Emergency source identity already exists; recover only with a new campaign ID")
    try:
        completed = subprocess.run(
            [str(age_keygen_binary), "-o", str(identity)],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            env=_git_environment(),
            preexec_fn=lambda: os.umask(0o077),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencySourceDeliveryError("campaign-local age identity generation could not start") from exc
    if completed.returncode != 0:
        _fail("campaign-local age identity generation failed")
    try:
        os.chmod(identity, 0o600)
    except OSError as exc:
        raise EmergencySourceDeliveryError("campaign-local age identity mode cannot be restricted") from exc
    recipient = _recipient_from_identity(identity_file=identity, age_keygen_binary=age_keygen_binary)
    return {
        "status": "bootstrapped-local-only",
        "campaign_id": campaign_id,
        "identity_path": str(identity),
        "age_recipient": recipient,
        "recipient_key_id": recipient_key_id(recipient),
        "object_storage_action": "not-performed",
        "private_key_material": "not-emitted",
    }


def _decrypt_bundle(*, ciphertext: Path, identity_file: Path, age_binary: Path, output: Path) -> tuple[str, int]:
    ciphertext = _require_safe_regular(
        ciphertext,
        label="downloaded source ciphertext",
        maximum_bytes=MAX_GIT_BUNDLE_BYTES + MAX_CIPHERTEXT_OVERHEAD_BYTES,
        private=True,
    )
    identity_file = _require_safe_regular(identity_file, label="WebApp-FI age identity", maximum_bytes=256 * 1024, private=True)
    age_binary = _require_age_binary(age_binary, label="age binary")
    output = _require_absolute(output, field="decrypted Git bundle output")
    _require_safe_directory(output.parent, label="decrypted Git bundle output parent", private=True)
    if output.exists() or output.is_symlink():
        _fail("refusing to overwrite decrypted Git bundle output")
    try:
        completed = subprocess.run(
            [str(age_binary), "--decrypt", "-i", str(identity_file), "-o", str(output), str(ciphertext)],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=900,
            env=_git_environment(),
            preexec_fn=lambda: os.umask(0o077),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencySourceDeliveryError("age decryption could not start") from exc
    if completed.returncode != 0:
        _fail("age decryption failed")
    return _hash_regular(output, label="decrypted Git bundle output", maximum_bytes=MAX_GIT_BUNDLE_BYTES, private=True)


def materialize_checkout_from_bundle(*, bundle: Path, identity: SourceIdentity, destination: Path) -> dict[str, Any]:
    """Build one fresh checkout and atomically expose it only after verification."""

    _require_root_execution()
    bundle = _require_safe_regular(bundle, label="decrypted Git bundle", maximum_bytes=MAX_GIT_BUNDLE_BYTES, private=True)
    identity = _validate_identity(identity)
    destination = _require_absolute(destination, field="WebApp-FI Emergency source destination")
    _require_safe_directory(destination.parent, label="WebApp-FI Emergency source destination parent", private=True)
    if destination.exists() or destination.is_symlink():
        _fail("WebApp-FI Emergency source destination must be fresh; recover only with a new campaign ID")
    stage = destination.parent / ("." + destination.name + "." + secrets.token_hex(12) + ".staging")
    _new_private_directory(stage, label="fresh WebApp-FI source checkout staging directory")
    try:
        _run_git(
            ["clone", "--no-checkout", "--no-local", str(bundle), str(stage)],
            cwd=destination.parent,
            purpose="fresh Git bundle checkout clone",
        )
        _run_git(
            ["checkout", "--detach", identity.emergency_patch_sha],
            cwd=stage,
            purpose="fresh Git bundle checkout",
        )
        _run_git(["remote", "remove", "origin"], cwd=stage, purpose="local source checkout remote removal")
        _verify_checkout_identity(checkout=stage, identity=identity)
        if destination.exists() or destination.is_symlink():
            _fail("WebApp-FI Emergency source destination appeared during staging")
        os.rename(stage, destination)
        # The candidate is now externally visible.  Persist the parent entry
        # as well as the verified checkout tree before reporting success.
        _fsync_directory(destination.parent, label="WebApp-FI Emergency source destination parent")
        _fsync_directory(destination, label="received WebApp-FI source checkout")
    except Exception:
        # Preserve the private staging directory for inspection on failure;
        # its name cannot collide with a future candidate.
        raise
    return {
        "status": "received-local-only",
        "destination": str(destination),
        "source": identity.as_descriptor(
            bundle_sha256=_hash_regular(bundle, label="decrypted Git bundle", maximum_bytes=MAX_GIT_BUNDLE_BYTES, private=True)[0],
            bundle_bytes=_hash_regular(bundle, label="decrypted Git bundle", maximum_bytes=MAX_GIT_BUNDLE_BYTES, private=True)[1],
        ),
    }


def receive_to_fresh_checkout(
    *,
    descriptor: VerifiedDescriptor,
    url: str,
    identity_file: Path,
    age_binary: Path,
    age_keygen_binary: Path,
    destination: Path,
) -> dict[str, Any]:
    """Perform the receiver's only network action, then materialize locally."""

    _require_root_execution()
    destination = _require_absolute(destination, field="WebApp-FI Emergency source destination")
    _require_safe_directory(destination.parent, label="WebApp-FI Emergency source destination parent", private=True)
    if destination.exists() or destination.is_symlink():
        _fail("WebApp-FI Emergency source destination must be fresh")
    local_recipient = _recipient_from_identity(identity_file=identity_file, age_keygen_binary=age_keygen_binary)
    if recipient_key_id(local_recipient) != descriptor.recipient_key_id:
        _fail("WebApp-FI age identity does not match the signed recipient pin")
    _validate_presigned_get(url=url, descriptor=descriptor)
    stage = destination.parent / ("." + destination.name + "." + secrets.token_hex(12) + ".receive")
    _new_private_directory(stage, label="fresh WebApp-FI source receive directory")
    ciphertext = stage / "source.bundle.age"
    bundle = stage / "source.bundle"
    _download_exact_ciphertext(url=url, descriptor=descriptor, output=ciphertext)
    bundle_hash, bundle_bytes = _decrypt_bundle(
        ciphertext=ciphertext, identity_file=identity_file, age_binary=age_binary, output=bundle
    )
    if (bundle_hash, bundle_bytes) != (descriptor.bundle_sha256, descriptor.bundle_bytes):
        _fail("decrypted Git bundle differs from the signed source descriptor")
    # The checkout helper uses a sibling staging directory so the source
    # bundle remains available as local forensic evidence if Git rejects it.
    result = materialize_checkout_from_bundle(bundle=bundle, identity=descriptor.identity, destination=destination)
    result.update(
        {
            "campaign_id": descriptor.campaign_id,
            "descriptor_sha256": descriptor.manifest_sha256,
            "payload_transport": "private-versioned-arvan-object-storage-direct-get",
            "s3_credentials": "not-accepted-on-webapp-fi",
        }
    )
    return result


def _write_prepared(path: Path, plan: Mapping[str, Any]) -> None:
    _write_create_only(path, canonical_json_bytes(validate_prepared_plan(plan)) + b"\n", label="prepared source delivery plan")


def _prepare_apply(args: argparse.Namespace) -> dict[str, Any]:
    _require_root_execution()
    identity = inspect_fixed_emergency_checkout(repository=args.repository)
    controller_tool = inspect_controller_tool(repository=args.controller_repository)
    output = _new_private_directory(args.output_directory, label="prepared source delivery directory")
    bootstrap = output / "receiver-bootstrap.tar.gz"
    build_receiver_bootstrap(controller_tool=controller_tool, output=bootstrap)
    bundle = output / "source.bundle"
    build_self_contained_git_bundle(repository=args.repository, identity=identity, output=bundle)
    ciphertext = output / "source.bundle.age"
    encrypt_git_bundle(bundle=bundle, recipient=args.age_recipient, age_binary=args.age_binary, output=ciphertext)
    plan = build_prepared_plan(
        campaign_id=args.campaign_id,
        endpoint=args.endpoint,
        region=args.region,
        bucket=args.bucket,
        prefix=args.prefix,
        recipient=args.age_recipient,
        identity=identity,
        controller_tool=controller_tool,
        bundle=bundle,
        bootstrap=bootstrap,
        ciphertext=ciphertext,
    )
    _write_prepared(output / "prepared.json", plan)
    return {
        "status": "prepared-local-only",
        "campaign_id": plan["campaign_id"],
        "prepared_plan": str(output / "prepared.json"),
        "source": plan["source"],
        "controller_tool": plan["controller_tool"],
        "receiver_bootstrap": plan["receiver_bootstrap"],
        "bootstrap": {
            "sha256": plan["bootstrap"]["sha256"],
            "bytes": plan["bootstrap"]["bytes"],
            "object_key": plan["bootstrap"]["object_key"],
        },
        "ciphertext": {"sha256": plan["ciphertext"]["sha256"], "bytes": plan["ciphertext"]["bytes"]},
        "object_key": plan["object_key"],
        "object_storage_action": False,
        "service_action": False,
        "docker_action": False,
    }


def _plan_prepare(args: argparse.Namespace) -> dict[str, Any]:
    identity = inspect_fixed_emergency_checkout(repository=args.repository)
    controller_tool = inspect_controller_tool(repository=args.controller_repository)
    _validate_endpoint(args.endpoint, args.region)
    _validate_prefix(args.prefix)
    _require_pattern(args.bucket, field="bucket", pattern=BUCKET_RE)
    recipient_key_id(args.age_recipient)
    return {
        "status": "planned-non-authorizing",
        "campaign_id": _require_pattern(args.campaign_id, field="campaign_id", pattern=CAMPAIGN_RE),
        "source": identity.as_descriptor(bundle_sha256="0" * 64, bundle_bytes=1),
        "controller_tool": controller_tool.as_descriptor(),
        "receiver_bootstrap": {
            "schema": "gold-trade-webapp-fi-emergency-source-receiver-bootstrap-v1",
            "sha256": controller_tool.sha256,
            "bytes": controller_tool.bytes,
        },
        "output_directory": str(_require_absolute(args.output_directory, field="output_directory")),
        "local_write_action": False,
        "object_storage_action": False,
        "service_action": False,
        "docker_action": False,
    }


def _confirm(value: object, campaign_id: str) -> None:
    if value != campaign_id:
        _fail("--confirm must exactly equal the signed campaign ID")


def _cli_prepare(args: argparse.Namespace) -> dict[str, Any]:
    return _prepare_apply(args) if args.apply else _plan_prepare(args)


def _cli_bootstrap_identity(args: argparse.Namespace) -> dict[str, Any]:
    campaign_id = _require_pattern(args.campaign_id, field="campaign_id", pattern=CAMPAIGN_RE)
    identity = campaign_identity_path(campaign_id)
    if not args.apply:
        return {
            "status": "planned-non-authorizing",
            "campaign_id": campaign_id,
            "identity_path": str(identity),
            "local_write_action": False,
            "object_storage_action": False,
            "private_key_material": "not-emitted",
        }
    _confirm(args.confirm, campaign_id)
    return bootstrap_campaign_age_identity(campaign_id=campaign_id, age_keygen_binary=args.age_keygen_binary)


def _cli_publish(args: argparse.Namespace) -> dict[str, Any]:
    plan = _load_prepared(args.prepared_plan)
    if not args.apply:
        return {
            "status": "planned-non-authorizing",
            "campaign_id": plan["campaign_id"],
            "object_key": plan["object_key"],
            "object_storage_action": False,
            "descriptor_output": str(_require_absolute(args.descriptor_output, field="descriptor_output")),
        }
    _confirm(args.confirm, str(plan["campaign_id"]))
    descriptor = publish_prepared_plan(
        plan=plan,
        private_key=load_private_key(args.signing_private_key),
        credentials=_load_credentials(args.credentials),
        descriptor_output=args.descriptor_output,
    )
    return {
        "status": "published",
        "campaign_id": descriptor.campaign_id,
        "descriptor": str(args.descriptor_output),
        "descriptor_sha256": descriptor.manifest_sha256,
        "object_key": descriptor.object_key,
        "version_id": descriptor.version_id,
        "payload_transport": "private-versioned-arvan-object-storage-only",
    }


def _control_confirmation(*, action: str, kind: str, approval: Mapping[str, Any] | None = None, artifact: ControllerToolIdentity | None = None) -> str:
    if kind not in {"anchor", "installer"}:
        _fail("control artifact kind is unsupported")
    if action == "publish":
        if artifact is None:
            _fail("control artifact publish confirmation requires artifact provenance")
        artifact = _validate_controller_tool(artifact)
        return f"publish-webapp-fi-emergency-source-{kind}:{artifact.revision}:{artifact.sha256}"
    if action == "issue-get":
        if approval is None:
            _fail("control artifact GET confirmation requires approval")
        normalized = _validate_control_approval(approval, kind=kind)
        hash_field = "anchor_sha256" if kind == "anchor" else "installer_sha256"
        return (
            f"issue-webapp-fi-emergency-source-{kind}-get:"
            f"{normalized[hash_field]}:{normalized['artifact_version_id']}"
        )
    _fail("control artifact confirmation action is unsupported")


def _cli_publish_control_artifact(args: argparse.Namespace) -> dict[str, Any]:
    kind = _require_pattern(args.kind, field="control artifact kind", pattern=re.compile(r"^(?:anchor|installer)$", re.ASCII))
    endpoint, region = _validate_endpoint(args.endpoint, args.region)
    bucket = _require_pattern(args.bucket, field="bucket", pattern=BUCKET_RE)
    prefix = _validate_prefix(args.prefix)
    if kind == "anchor":
        artifact = inspect_anchor_artifact(repository=args.controller_repository)
        layout = ANCHOR_OBJECT_LAYOUT
    else:
        artifact = inspect_installer_artifact(repository=args.controller_repository)
        layout = INSTALLER_OBJECT_LAYOUT
    phrase = _control_confirmation(action="publish", kind=kind, artifact=artifact)
    if not args.apply:
        return {
            "status": "planned-non-authorizing",
            "kind": kind,
            "controller_artifact": artifact.as_descriptor(),
            "object_key": _control_artifact_key(prefix=prefix, layout=layout, artifact=artifact),
            "approval_output": str(_require_absolute(args.approval_output, field="approval_output")),
            "confirmation": phrase,
            "object_storage_action": False,
            "ssh_artifact_bytes": "forbidden",
        }
    if args.confirm != phrase:
        _fail("--confirm must exactly equal the control artifact publication confirmation")
    approval = publish_control_artifact(
        repository=args.controller_repository,
        kind=kind,
        endpoint=endpoint,
        region=region,
        bucket=bucket,
        prefix=prefix,
        credentials=_load_credentials(args.credentials),
        approval_output=args.approval_output,
    )
    return {
        "status": "published-control-artifact",
        "kind": kind,
        "approval_output": str(args.approval_output),
        "object_key": approval["object_key"],
        "artifact_version_id": approval["artifact_version_id"],
        "payload_transport": "private-versioned-arvan-object-storage-only",
        "ssh_artifact_bytes": "forbidden",
    }


def _cli_issue_control_artifact_get(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, str] | None]:
    kind = _require_pattern(args.kind, field="control artifact kind", pattern=re.compile(r"^(?:anchor|installer)$", re.ASCII))
    approval = _load_control_approval(args.approval, kind=kind)
    phrase = _control_confirmation(action="issue-get", kind=kind, approval=approval)
    if not args.apply:
        hash_field = "anchor_sha256" if kind == "anchor" else "installer_sha256"
        return {
            "status": "planned-non-authorizing",
            "kind": kind,
            "object_key": approval["object_key"],
            "artifact_version_id": approval["artifact_version_id"],
            "sha256": approval[hash_field],
            "confirmation": phrase,
            "object_storage_action": False,
        }, None
    if args.confirm != phrase:
        _fail("--confirm must exactly equal the control artifact GET confirmation")
    client = _create_s3_client(approval, _load_credentials(args.credentials))
    _assert_private_versioned_bucket(
        client,
        endpoint=str(approval["endpoint"]),
        region=str(approval["region"]),
        bucket=str(approval["bucket"]),
    )
    url = issue_control_artifact_get(approval=approval, kind=kind, client=client, ttl_seconds=args.ttl_seconds)
    return {
        "status": "issued-transient-control-artifact-get",
        "kind": kind,
        "artifact_version_id": approval["artifact_version_id"],
    }, {"schema": "gold-trade-webapp-fi-emergency-source-control-artifact-url-v1", "kind": kind, "url": url}


def _cli_prepare_pinned_signer_approval(args: argparse.Namespace) -> dict[str, Any]:
    anchor = _load_control_approval(args.anchor_approval, kind="anchor")
    public_key = load_public_key(args.signing_public_key)
    approval = build_pinned_signer_approval(anchor_approval=anchor, signing_public_key=public_key)
    normalized = _validate_pinned_signer_approval(approval, anchor_approval=anchor)
    phrase = "prepare-webapp-fi-emergency-source-signer-approval:" + normalized["anchor_sha256"] + ":" + normalized["signer_key_id"]
    if not args.apply:
        return {
            "status": "planned-non-authorizing",
            "signer_approval": normalized,
            "output": str(_require_absolute(args.output, field="signer approval output")),
            "confirmation": phrase,
            "network_action": False,
            "object_storage_action": False,
        }
    if args.confirm != phrase:
        _fail("--confirm must exactly equal the pinned signer approval confirmation")
    _write_create_only(
        args.output,
        canonical_json_bytes(normalized) + b"\n",
        label="pinned signer independent approval output",
    )
    return {
        "status": "prepared-local-only",
        "output": str(args.output),
        "signer_key_id": normalized["signer_key_id"],
        "anchor_sha256": normalized["anchor_sha256"],
        "network_action": False,
        "object_storage_action": False,
    }


def _cli_pinned_signer_provisioning_contract(args: argparse.Namespace) -> dict[str, Any]:
    anchor = _load_control_approval(args.anchor_approval, kind="anchor")
    signer = _load_pinned_signer_approval(args.signer_approval, anchor_approval=anchor)
    return pinned_signer_provisioning_contract(
        anchor_approval=anchor,
        signer_approval=signer,
        signing_public_key=load_public_key(args.signing_public_key),
    )


def _cli_issue_get(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, str] | None]:
    descriptor = load_verified_descriptor(args.descriptor, public_key=load_public_key(args.signing_public_key))
    if not args.apply:
        return descriptor.as_receive_plan(), None
    _confirm(args.confirm, descriptor.campaign_id)
    credentials = _load_credentials(args.credentials)
    client = _create_s3_client(
        {
            "endpoint": descriptor.endpoint,
            "region": descriptor.region,
        },
        credentials,
    )
    _assert_private_versioned_bucket(
        client,
        endpoint=descriptor.endpoint,
        region=descriptor.region,
        bucket=descriptor.bucket,
    )
    urls = issue_version_bound_gets(descriptor=descriptor, client=client, ttl_seconds=args.ttl_seconds)
    return (
        {"status": "issued-transient-version-bound-gets", "campaign_id": descriptor.campaign_id},
        {
            "schema": "gold-trade-webapp-fi-emergency-source-url-map-v1",
            "descriptor_sha256": descriptor.manifest_sha256,
            **urls,
        },
    )


def _cli_receive(args: argparse.Namespace) -> dict[str, Any]:
    descriptor = load_verified_descriptor(args.descriptor, public_key=load_public_key(args.signing_public_key))
    if (
        descriptor.identity.base_sha != SOURCE_RELEASE_SHA
        or descriptor.identity.emergency_patch_sha != EMERGENCY_PATCH_SHA
    ):
        _fail("signed descriptor source SHA identities do not match the required Emergency release")
    receiver_sha256, receiver_bytes = _hash_regular(
        Path(__file__).resolve(), label="installed WebApp-FI receiver", maximum_bytes=2 * 1024 * 1024, private=False
    )
    if (receiver_sha256, receiver_bytes) != (
        descriptor.receiver_bootstrap_sha256,
        descriptor.receiver_bootstrap_bytes,
    ):
        _fail("installed WebApp-FI receiver differs from the signed bootstrap provenance")
    if not args.apply:
        plan = descriptor.as_receive_plan()
        plan["destination"] = str(_require_absolute(args.destination, field="destination"))
        return plan
    _confirm(args.confirm, descriptor.campaign_id)
    url = sys.stdin.read(MAX_URL_BYTES + 1).strip()
    if len(url.encode("utf-8")) > MAX_URL_BYTES:
        _fail("version-bound Object Storage URL exceeds the fixed bound")
    return receive_to_fresh_checkout(
        descriptor=descriptor,
        url=url,
        identity_file=args.age_identity,
        age_binary=args.age_binary,
        age_keygen_binary=args.age_keygen_binary,
        destination=args.destination,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)

    prepare = actions.add_parser("prepare", help="locally build and age-seal a self-contained Git bundle")
    prepare.add_argument(
        "--repository",
        type=Path,
        required=True,
        help="separate clean delivered source checkout at exact e1a30972 WA-IR source commit",
    )
    prepare.add_argument(
        "--controller-repository",
        type=Path,
        required=True,
        help="separate later clean control checkout containing this committed manager",
    )
    prepare.add_argument("--campaign-id", required=True)
    prepare.add_argument("--endpoint", required=True)
    prepare.add_argument("--region", required=True)
    prepare.add_argument("--bucket", required=True)
    prepare.add_argument("--prefix", required=True)
    prepare.add_argument("--age-recipient", required=True)
    prepare.add_argument("--age-binary", type=Path, default=Path("/usr/bin/age"))
    prepare.add_argument("--output-directory", type=Path, required=True)
    prepare.add_argument("--apply", action="store_true")

    bootstrap = actions.add_parser(
        "bootstrap-identity", help="create one fresh campaign-local FI age identity; emits public recipient only"
    )
    bootstrap.add_argument("--campaign-id", required=True)
    bootstrap.add_argument("--age-keygen-binary", type=Path, default=Path("/usr/bin/age-keygen"))
    bootstrap.add_argument("--apply", action="store_true")
    bootstrap.add_argument("--confirm")

    publish = actions.add_parser("publish", help="create-only upload and sign the immutable VersionId descriptor")
    publish.add_argument("--prepared-plan", type=Path, required=True)
    publish.add_argument("--credentials", type=Path, required=True)
    publish.add_argument("--signing-private-key", type=Path, required=True)
    publish.add_argument("--descriptor-output", type=Path, required=True)
    publish.add_argument("--apply", action="store_true")
    publish.add_argument("--confirm")

    publish_control = actions.add_parser(
        "publish-control-artifact",
        help="create-only publish the first installer or pinned anchor and write its canonical approval receipt",
    )
    publish_control.add_argument("--kind", choices=("installer", "anchor"), required=True)
    publish_control.add_argument("--controller-repository", type=Path, required=True)
    publish_control.add_argument("--endpoint", required=True)
    publish_control.add_argument("--region", required=True)
    publish_control.add_argument("--bucket", required=True)
    publish_control.add_argument("--prefix", required=True)
    publish_control.add_argument("--credentials", type=Path, required=True)
    publish_control.add_argument("--approval-output", type=Path, required=True)
    publish_control.add_argument("--apply", action="store_true")
    publish_control.add_argument("--confirm")

    issue = actions.add_parser("issue-get", help="emit one transient VersionId-bound GET URL only after confirmation")
    issue.add_argument("--descriptor", type=Path, required=True)
    issue.add_argument("--signing-public-key", type=Path, required=True)
    issue.add_argument("--credentials", type=Path, required=True)
    issue.add_argument("--ttl-seconds", type=int, default=300)
    issue.add_argument("--apply", action="store_true")
    issue.add_argument("--confirm")

    issue_control = actions.add_parser(
        "issue-control-artifact-get",
        help="emit one transient VersionId-bound direct GET for the first installer or pinned anchor",
    )
    issue_control.add_argument("--kind", choices=("installer", "anchor"), required=True)
    issue_control.add_argument("--approval", type=Path, required=True)
    issue_control.add_argument("--credentials", type=Path, required=True)
    issue_control.add_argument("--ttl-seconds", type=int, default=300)
    issue_control.add_argument("--apply", action="store_true")
    issue_control.add_argument("--confirm")

    first_placement = actions.add_parser(
        "first-installer-placement-contract",
        help="render the bounded root-console direct-Object-Storage first-installer ceremony",
    )
    first_placement.add_argument("--approval", type=Path, required=True)

    anchor_installation = actions.add_parser(
        "pinned-anchor-installation-contract",
        help="render the bounded root-console receipt + direct-GET anchor installation ceremony",
    )
    anchor_installation.add_argument("--approval", type=Path, required=True)

    signer_approval = actions.add_parser(
        "prepare-pinned-signer-approval",
        help="prepare a canonical local signer-approval receipt bound to one anchor approval",
    )
    signer_approval.add_argument("--anchor-approval", type=Path, required=True)
    signer_approval.add_argument("--signing-public-key", type=Path, required=True)
    signer_approval.add_argument("--output", type=Path, required=True)
    signer_approval.add_argument("--apply", action="store_true")
    signer_approval.add_argument("--confirm")

    signer_contract = actions.add_parser(
        "pinned-signer-provisioning-contract",
        help="render the bounded local candidate+receipt signer provisioning ceremony",
    )
    signer_contract.add_argument("--anchor-approval", type=Path, required=True)
    signer_contract.add_argument("--signer-approval", type=Path, required=True)
    signer_contract.add_argument("--signing-public-key", type=Path, required=True)

    receive = actions.add_parser("receive", help="WA-FI direct GET/decrypt/fresh checkout; accepts no S3 credentials")
    receive.add_argument("--descriptor", type=Path, required=True)
    receive.add_argument("--signing-public-key", type=Path, required=True)
    receive.add_argument("--age-identity", type=Path, required=True)
    receive.add_argument("--age-binary", type=Path, default=Path("/usr/bin/age"))
    receive.add_argument("--age-keygen-binary", type=Path, default=Path("/usr/bin/age-keygen"))
    receive.add_argument("--destination", type=Path, required=True)
    receive.add_argument("--apply", action="store_true")
    receive.add_argument("--confirm")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        if not sys.flags.isolated or not sys.flags.dont_write_bytecode:
            _fail("source delivery tool must be launched with python3 -I -B")
        args = _parser().parse_args(argv)
        transient_urls: dict[str, str] | None = None
        if args.action == "prepare":
            result = _cli_prepare(args)
        elif args.action == "bootstrap-identity":
            result = _cli_bootstrap_identity(args)
        elif args.action == "publish":
            result = _cli_publish(args)
        elif args.action == "publish-control-artifact":
            result = _cli_publish_control_artifact(args)
        elif args.action == "issue-get":
            result, transient_urls = _cli_issue_get(args)
        elif args.action == "issue-control-artifact-get":
            result, transient_urls = _cli_issue_control_artifact_get(args)
        elif args.action == "first-installer-placement-contract":
            result = first_installer_placement_contract(
                approval=_load_control_approval(args.approval, kind="installer")
            )
        elif args.action == "prepare-pinned-signer-approval":
            result = _cli_prepare_pinned_signer_approval(args)
        elif args.action == "pinned-anchor-installation-contract":
            result = pinned_anchor_installation_contract(
                approval=_load_control_approval(args.approval, kind="anchor")
            )
        elif args.action == "pinned-signer-provisioning-contract":
            result = _cli_pinned_signer_provisioning_contract(args)
        elif args.action == "receive":
            result = _cli_receive(args)
        else:  # pragma: no cover - argparse dispatch invariant.
            _fail("unsupported source delivery action")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        if transient_urls is not None:
            # This control map is intentionally emitted only on an explicit
            # live action and is never written into a receipt or descriptor.
            print(json.dumps(transient_urls, ensure_ascii=True, sort_keys=True))
        return 0
    except EmergencySourceDeliveryError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
