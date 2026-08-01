#!/usr/bin/env python3
"""Render a bounded WA-IR receiver for the source-phase static archive.

The controller side verifies the generic source-transport receipt and the
controller-signed static proof before its reviewed in-process API renders one
SSH control command.  The embedded receiver then downloads the exact immutable
Object Storage version directly, decrypts it with the pre-existing campaign
bootstrap identity, and writes a URL-free receipt compatible with
``install_webapp_ir_static_assets.py``.

This helper deliberately does not install the static tree, stage a release,
load an image, invoke Docker, change ``current``, start a service, or read S3
credentials.  The presigned URL is a transient final argv item and is never
placed in a receipt, candidate file, or rendered configuration.  Direct CLI
rendering is disabled so it cannot expose the URL in a process list or terminal
output.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import sys
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, quote, urlparse


REMOTE_HOST = "root@95.38.164.29"
SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes")

RECEIVER_CONFIG_SCHEMA = "gold-trade-wa-ir-static-source-receive-config-v1"
STATIC_RECEIVE_RECEIPT_SCHEMA = "gold-trade-wa-ir-static-assets-receive-v1"
DEFAULT_RECEIVER_ROOT = "/srv/trading-bot-three-site-staging-data/webapp-ir-static-source"
WA_IR_CAMPAIGN_IDENTITY_ROOT = "/etc/trading-bot-three-site/campaigns"
WA_IR_BOOTSTRAP_IDENTITY_SUFFIX = "webapp-ir/bootstrap.agekey"
STATIC_ARCHIVE_NAME = "static-assets.tar"
STATIC_RECEIPT_NAME = "static-receive-receipt.json"

MAX_CONTROL_FILE_BYTES = 2 * 1024 * 1024
# The remote configuration is base64-expanded into one SSH command argument.
# Keep it well below conventional exec argument ceilings rather than treating
# a larger signed static file manifest as a viable control-channel payload.
MAX_REMOTE_CONFIG_BYTES = 512 * 1024
MAX_STATIC_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_CIPHERTEXT_BYTES = MAX_STATIC_ARCHIVE_BYTES + 1024 * 1024
STATIC_RECEIVE_RECEIPT_RESERVE_BYTES = 1024 * 1024
STATIC_RECEIVE_CAPACITY_MARGIN_BYTES = 64 * 1024 * 1024
MAX_URL_BYTES = 8192
MAX_PRESIGNED_LIFETIME_SECONDS = 900

CAMPAIGN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REVISION_RE = re.compile(r"^[0-9a-f]{12}$")
OBJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
AGE_RECIPIENT_RE = re.compile(r"^age1[ac-hj-np-z02-9]{20,128}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_ID_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{1,1024}$")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$")
PREFIX_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,127}$")

REMOTE_CONFIG_FIELDS = frozenset(
    {
        "schema",
        "receiver_root",
        "age_identity_file",
        "object_storage",
        "campaign_id",
        "application",
        "tooling",
        "controller_age_recipient",
        "wa_ir_age_recipient",
        "transport_receipt",
        "static_assets_provenance",
        "pinned_controller_public_key_base64",
    }
)
REMOTE_STORAGE_FIELDS = frozenset({"endpoint", "region", "bucket", "prefix"})
FORBIDDEN_CONTROL_KEY_FRAGMENTS = (
    "credential",
    "access_key",
    "secret",
    "session_token",
    "password",
    "private_key",
    "payload",
    "archive_base64",
    "image_base64",
    "data_base64",
)
ALLOWED_CONTROL_BASE64_PATHS = frozenset(
    {
        ("pinned_controller_public_key_base64",),
        ("static_assets_provenance", "controller_public_key_base64"),
        ("static_assets_provenance", "controller_signature", "signature_base64"),
    }
)


class StaticReceiveRenderError(RuntimeError):
    """The controller cannot safely render the WA-IR static receiver."""


def _reject_direct_url_render() -> None:
    """Fence the transient GET credential from CLI and shell-history paths."""

    raise StaticReceiveRenderError(
        "direct CLI rendering of the URL-bearing WA-IR static receive control is disabled"
    )


def _require_root_controlled_code_file(path: Path, *, field: str) -> Path:
    """Refuse sibling imports from a checkout root could have modified."""

    if not path.is_absolute():
        raise RuntimeError(f"{field} path must be absolute")
    current = Path(path.anchor)
    for component in path.parent.parts[1:]:
        current /= component
        try:
            state = current.lstat()
            resolved = current.resolve(strict=True)
        except OSError as exc:  # pragma: no cover - deployment invariant.
            raise RuntimeError(f"cannot inspect {field} ancestor") from exc
        sticky_root_directory = state.st_uid == 0 and bool(stat.S_IMODE(state.st_mode) & stat.S_ISVTX)
        if (
            resolved != current
            or stat.S_ISLNK(state.st_mode)
            or not stat.S_ISDIR(state.st_mode)
            or state.st_uid != 0
            or (stat.S_IMODE(state.st_mode) & 0o022 and not sticky_root_directory)
        ):
            raise RuntimeError(f"{field} ancestor is not root-controlled")
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        opened = resolved.lstat()
    except OSError as exc:  # pragma: no cover - deployment invariant.
        raise RuntimeError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or stat.S_ISLNK(opened.st_mode)
        or not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != 0
        or opened.st_nlink != 1
        or stat.S_IMODE(opened.st_mode) & (0o022 | stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
    ):
        raise RuntimeError(f"{field} is not a root-controlled regular file")
    return path


def _load_exact_sibling(filename: str, module_name: str) -> Any:
    """Load a named sibling without accepting an ambient ``sys.path`` copy."""

    source = _require_root_controlled_code_file(Path(__file__), field="WA-IR static receiver renderer source")
    path = _require_root_controlled_code_file(source.with_name(filename), field=f"required sibling {filename}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError(f"cannot load required sibling {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    loaded = getattr(module, "__file__", None)
    if not isinstance(loaded, str) or Path(loaded).resolve(strict=True) != path:
        raise RuntimeError(f"required sibling {filename} did not load from its exact path")
    return module


transport = _load_exact_sibling("manage_webapp_fi_source_transport.py", "_wa_ir_static_transport")
provenance = _load_exact_sibling("verify_webapp_fi_source_provenance.py", "_wa_ir_static_provenance")


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StaticReceiveRenderError("JSON input contains duplicate keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise StaticReceiveRenderError(f"JSON input contains unsupported constant: {value}")


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise StaticReceiveRenderError("WA-IR static receiver renderer must run as root")


def _require_absolute(path: Path, *, field: str) -> Path:
    if not path.is_absolute():
        raise StaticReceiveRenderError(f"{field} must be absolute")
    return path


def _require_safe_ancestors(path: Path, *, field: str) -> None:
    path = _require_absolute(path, field=field)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            state = current.lstat()
        except OSError as exc:
            raise StaticReceiveRenderError(f"{field} ancestor does not exist") from exc
        sticky_root_directory = state.st_uid == 0 and bool(stat.S_IMODE(state.st_mode) & stat.S_ISVTX)
        if (
            stat.S_ISLNK(state.st_mode)
            or not stat.S_ISDIR(state.st_mode)
            or state.st_uid != 0
            or (stat.S_IMODE(state.st_mode) & 0o022 and not sticky_root_directory)
        ):
            raise StaticReceiveRenderError(f"{field} has an unsafe ancestor")


def _read_root_only_file(path: Path, *, field: str, maximum_bytes: int = MAX_CONTROL_FILE_BYTES) -> bytes:
    path = _require_absolute(path, field=field)
    _require_safe_ancestors(path.parent, field=field)
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise StaticReceiveRenderError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or stat.S_IMODE(before.st_mode) & 0o077
        or before.st_nlink != 1
        or not 1 <= before.st_size <= maximum_bytes
    ):
        raise StaticReceiveRenderError(f"{field} has unsafe ownership, mode, or size")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StaticReceiveRenderError(f"cannot securely open {field}") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) & 0o077
            or opened.st_nlink != 1
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
        ):
            raise StaticReceiveRenderError(f"{field} changed while being opened")
        chunks: list[bytes] = []
        total = 0
        while True:
            block = os.read(descriptor, min(65536, maximum_bytes + 1 - total))
            if not block:
                break
            total += len(block)
            if total > maximum_bytes:
                raise StaticReceiveRenderError(f"{field} exceeds its fixed size bound")
            chunks.append(block)
        after = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        if total != opened.st_size or any(getattr(opened, name) != getattr(after, name) for name in identity):
            raise StaticReceiveRenderError(f"{field} changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _parse_canonical_json(payload: bytes, *, field: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StaticReceiveRenderError(f"{field} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise StaticReceiveRenderError(f"{field} must use canonical JSON")
    return value


def _reject_persisted_url(payload: bytes, *, field: str) -> None:
    """Reject an actual URL without rejecting opaque contract identifiers.

    The receipt and provenance validators that follow each require an exact
    schema, so they already reject a URL/header field, a presign envelope, or
    any unknown extension.  Terms such as ``x-amz-`` and ``presigned`` may,
    however, legally occur inside an opaque campaign/object identifier or the
    derived object key.  Treating those terms alone as a URL would permit the
    controller to create an immutable generic receipt that this renderer can
    never consume.
    """

    lowered = payload.lower()
    if b"://" in lowered:
        raise StaticReceiveRenderError(f"{field} must not persist a transient URL")


def _require_text(value: object, *, field: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise StaticReceiveRenderError(f"{field} is invalid")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise StaticReceiveRenderError(f"{field} contains control characters")
    return value


def _require_fixed_receiver_root(value: str) -> str:
    if value != DEFAULT_RECEIVER_ROOT:
        raise StaticReceiveRenderError("WA-IR static receiver root is fixed")
    path = PurePosixPath(value)
    if not path.is_absolute() or path.as_posix() != value or any(part in {"", ".", ".."} for part in path.parts):
        raise StaticReceiveRenderError("WA-IR static receiver root is invalid")
    return value


def wa_ir_bootstrap_identity_file(campaign_id: object) -> str:
    """Return the sole accepted existing identity path for one fresh campaign."""

    if not isinstance(campaign_id, str) or not CAMPAIGN_RE.fullmatch(campaign_id):
        raise StaticReceiveRenderError("campaign ID is invalid for the WA-IR bootstrap identity")
    path = PurePosixPath(WA_IR_CAMPAIGN_IDENTITY_ROOT) / campaign_id / WA_IR_BOOTSTRAP_IDENTITY_SUFFIX
    value = path.as_posix()
    if not path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise StaticReceiveRenderError("WA-IR bootstrap identity path is invalid")
    return value


def _require_current_presigned_url(value: str, *, now: dt.datetime) -> None:
    """Supplement the pure contract with current-time validity, not just TTL bounds."""

    try:
        query = parse_qs(urlparse(value).query, keep_blank_values=True, strict_parsing=True)
        issued = dt.datetime.strptime(query["X-Amz-Date"][0], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
        expires = int(query["X-Amz-Expires"][0], 10)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise StaticReceiveRenderError("presigned download URL has an invalid current-time envelope") from exc
    now = now.astimezone(dt.timezone.utc)
    if not 1 <= expires <= MAX_PRESIGNED_LIFETIME_SECONDS or now < issued or now > issued + dt.timedelta(seconds=expires):
        raise StaticReceiveRenderError("presigned download URL is not current and short-lived")


def _load_transport_config(path: Path) -> Any:
    _require_root_execution()
    try:
        return transport.load_controller_config(path)
    except Exception as exc:
        raise StaticReceiveRenderError("source transport controller configuration is unsafe") from exc


def _load_transport_policy(path: Path) -> Any:
    """Keep the URL-free policy accessor for non-render planning callers."""

    return _load_transport_config(path).policy


def _verify_generic_static_receipt(*, payload: bytes, policy: Any) -> dict[str, Any]:
    _reject_persisted_url(payload, field="generic static source transport receipt")
    try:
        published = transport.verify_publish_receipt(config=policy, payload=payload)
    except Exception as exc:
        raise StaticReceiveRenderError("generic static source transport receipt is unsafe") from exc
    if (
        published.get("source_site") != "webapp_fi"
        or published.get("destination_site") != transport.STATIC_DESTINATION_SITE
        or published.get("object_kind") != transport.STATIC_OBJECT_KIND
        or published.get("recipient_mode") != transport.STATIC_MODE
        or published.get("recipients") != [policy.controller_age_recipient, policy.webapp_ir_age_recipient]
    ):
        raise StaticReceiveRenderError("generic static source transport receipt route or recipients are invalid")
    object_value = published.get("object")
    if not isinstance(object_value, Mapping):  # pragma: no cover - contract already checks this.
        raise StaticReceiveRenderError("generic static source transport object is invalid")
    if (
        object_value.get("plaintext_bytes", 0) > MAX_STATIC_ARCHIVE_BYTES
        or object_value.get("ciphertext_bytes", 0) > MAX_CIPHERTEXT_BYTES
    ):
        raise StaticReceiveRenderError("generic static source transport object exceeds the static archive bounds")
    return published


def _verify_static_proof(
    *,
    payload: bytes,
    published: Mapping[str, Any],
    pinned_controller_public_key_base64: str,
) -> dict[str, Any]:
    _reject_persisted_url(payload, field="controller-signed static asset provenance")
    value = _parse_canonical_json(payload, field="controller-signed static asset provenance")
    application_value = value.get("application")
    try:
        application = provenance._application(application_value, field="static proof application")
        provenance._key(pinned_controller_public_key_base64, field="pinned controller public key")
        verified = provenance._static_assets_provenance(
            payload=payload,
            pinned_controller_public_key_base64=pinned_controller_public_key_base64,
            expected_campaign_id=published["campaign_id"],
            expected_application=application,
        )
    except Exception as exc:
        raise StaticReceiveRenderError("controller-signed static asset provenance is unsafe") from exc
    if application["release_sha"] != published.get("release_sha"):
        raise StaticReceiveRenderError("controller-signed static asset provenance release does not match the transport receipt")
    if verified.get("artifact") != published.get("object"):
        raise StaticReceiveRenderError("controller-signed static asset provenance object does not match the transport receipt")
    files = value.get("files")
    if not isinstance(files, list) or not any(isinstance(item, Mapping) and item.get("path") == "index.html" for item in files):
        raise StaticReceiveRenderError("controller-signed static asset provenance lacks index.html")
    return {"application": application, "proof": value}


def _validate_presigned_url(*, value: str, policy: Any, published: Mapping[str, Any]) -> str:
    try:
        validated = transport.require_version_bound_presigned_get_url(
            value,
            policy=policy,
            object_key=published["object"]["object_key"],
            version_id=published["object"]["version_id"],
        )
    except Exception as exc:
        raise StaticReceiveRenderError("presigned download URL is not bound to the exact immutable static object") from exc
    _require_current_presigned_url(validated, now=_utc_now())
    return validated


def _build_remote_config(
    *,
    policy: Any,
    published: Mapping[str, Any],
    static_proof: Mapping[str, Any],
    application: Mapping[str, str],
    pinned_controller_public_key_base64: str,
) -> dict[str, Any]:
    return {
        "schema": RECEIVER_CONFIG_SCHEMA,
        "receiver_root": DEFAULT_RECEIVER_ROOT,
        "age_identity_file": wa_ir_bootstrap_identity_file(published["campaign_id"]),
        "object_storage": {
            "endpoint": policy.endpoint,
            "region": policy.region,
            "bucket": policy.bucket,
            "prefix": policy.prefix,
        },
        "campaign_id": published["campaign_id"],
        "application": dict(application),
        "tooling": {
            "control_commit": published["control_commit"],
            "control_tree": published["control_tree"],
        },
        "controller_age_recipient": policy.controller_age_recipient,
        "wa_ir_age_recipient": policy.webapp_ir_age_recipient,
        "transport_receipt": dict(published),
        "static_assets_provenance": dict(static_proof),
        "pinned_controller_public_key_base64": pinned_controller_public_key_base64,
    }


def _assert_control_only_remote_config(value: Mapping[str, Any]) -> None:
    """Keep SSH control data metadata-only and reject hidden delivery inputs."""

    if set(value) != REMOTE_CONFIG_FIELDS:
        raise StaticReceiveRenderError("remote static receiver control configuration has unexpected fields")
    storage = value.get("object_storage")
    if not isinstance(storage, Mapping) or set(storage) != REMOTE_STORAGE_FIELDS:
        raise StaticReceiveRenderError("remote static receiver Object Storage fields are unexpected")

    def visit(item: object, path: tuple[str, ...]) -> None:
        if isinstance(item, (bytes, bytearray, memoryview)):
            raise StaticReceiveRenderError("remote static receiver control configuration contains binary payload material")
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if not isinstance(key, str):
                    raise StaticReceiveRenderError("remote static receiver control configuration key is invalid")
                lowered = key.lower()
                if any(fragment in lowered for fragment in FORBIDDEN_CONTROL_KEY_FRAGMENTS):
                    raise StaticReceiveRenderError("remote static receiver control configuration contains credential or payload material")
                visit(nested, (*path, key))
            return
        if isinstance(item, list):
            for index, nested in enumerate(item):
                visit(nested, (*path, str(index)))
            return
        if isinstance(item, str):
            if item.startswith("AGE-SECRET-KEY-"):
                raise StaticReceiveRenderError("remote static receiver control configuration contains an age identity")
            if path not in ALLOWED_CONTROL_BASE64_PATHS and path[-1:] == ("signature_base64",):
                raise StaticReceiveRenderError("remote static receiver control configuration contains unsupported base64 material")
            if "://" in item and path != ("object_storage", "endpoint"):
                raise StaticReceiveRenderError("remote static receiver control configuration contains a durable URL")

    visit(value, ())
    encoded = canonical_json_bytes(value)
    if len(encoded) > MAX_REMOTE_CONFIG_BYTES:
        raise StaticReceiveRenderError("remote static receiver control configuration exceeds the fixed metadata bound")


# This receiver has no repository imports.  It keeps the first WA-IR static
# hand-off independent of a staged release while still verifying the
# controller's Ed25519 static proof before it writes a usable receive receipt.
REMOTE_RECEIVER_SOURCE = r'''
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from urllib.parse import parse_qs, quote, urlparse

CONFIG_SCHEMA = "gold-trade-wa-ir-static-source-receive-config-v1"
TRANSPORT_SCHEMA = "gold-trade-webapp-fi-source-transport-v1"
STATIC_RECEIVE_RECEIPT_SCHEMA = "gold-trade-wa-ir-static-assets-receive-v1"
OBJECT_ENCRYPTION = "age-v1"
STATIC_PROOF_SCHEMA = "gold-trade-webapp-fi-static-asset-provenance-v1"
STATIC_PROOF_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-static-asset-provenance-v1\x00"
FIXED_RECEIVER_ROOT = "/srv/trading-bot-three-site-staging-data/webapp-ir-static-source"
FIXED_CAMPAIGN_IDENTITY_ROOT = "/etc/trading-bot-three-site/campaigns"
FIXED_AGE_IDENTITY_SUFFIX = "webapp-ir/bootstrap.agekey"
STATIC_ARCHIVE_NAME = "static-assets.tar"
STATIC_RECEIPT_NAME = "static-receive-receipt.json"
CURL_BINARY = "/usr/bin/curl"
AGE_BINARY = "/usr/bin/age"
MAX_CONTROL_FILE_BYTES = 2 * 1024 * 1024
MAX_REMOTE_CONFIG_BYTES = 512 * 1024
MAX_STATIC_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_CIPHERTEXT_BYTES = MAX_STATIC_ARCHIVE_BYTES + 1024 * 1024
# Literal values keep this self-contained remote source pinned to the
# renderer's admission reserves without accepting controller code at WA-IR.
STATIC_RECEIVE_RECEIPT_RESERVE_BYTES = 1048576  # 1 MiB
STATIC_RECEIVE_CAPACITY_MARGIN_BYTES = 67108864  # 64 MiB
MAX_URL_BYTES = 8192
MAX_PRESIGNED_LIFETIME_SECONDS = 900
CAMPAIGN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REVISION_RE = re.compile(r"^[0-9a-f]{12}$")
OBJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
AGE_RECIPIENT_RE = re.compile(r"^age1[ac-hj-np-z02-9]{20,128}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_ID_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{1,1024}$")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$")
PREFIX_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,127}$")

class ReceiveError(RuntimeError):
    pass

def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReceiveError("duplicate JSON key")
        result[key] = value
    return result

def reject_constant(value):
    raise ReceiveError("unsupported JSON constant")

def canonical_json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")

def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()

def sha256_file(path, maximum):
    digest = hashlib.sha256()
    total = 0
    with open(path, "rb") as handle:
        while True:
            block = handle.read(65536)
            if not block:
                break
            total += len(block)
            if total > maximum:
                raise ReceiveError("file exceeds the fixed size bound")
            digest.update(block)
    return digest.hexdigest(), total

def require_text(value, maximum=4096):
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise ReceiveError("invalid text")
    if any(ord(character) < 0x20 or ord(character) == 0x7f for character in value):
        raise ReceiveError("text contains a control character")
    return value

def require_sha256(value):
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ReceiveError("invalid SHA-256")
    return value

def require_size(value, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ReceiveError("invalid size")
    return value

def require_absolute_path(value):
    text = require_text(value, 1024)
    pure = PurePosixPath(text)
    if not pure.is_absolute() or pure.as_posix() != text or len(pure.parts) < 2 or any(part in ("", ".", "..") for part in pure.parts):
        raise ReceiveError("invalid absolute path")
    return text

def require_root_private_file(value):
    path = Path(require_absolute_path(value))
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        after = resolved.lstat()
    except OSError as exc:
        raise ReceiveError("required root-only file is unavailable") from exc
    if resolved != path or stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(after.st_mode):
        raise ReceiveError("required root-only file is unsafe")
    if after.st_uid != 0 or after.st_nlink != 1 or stat.S_IMODE(after.st_mode) & 0o077 or after.st_size < 1:
        raise ReceiveError("required root-only file is unsafe")
    return path

def require_root_private_directory(value):
    path = Path(require_absolute_path(value))
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        try:
            before = current.lstat()
            resolved = current.resolve(strict=True)
            after = resolved.lstat()
        except OSError as exc:
            raise ReceiveError("root-only directory is unavailable") from exc
        sticky_root_directory = after.st_uid == 0 and bool(stat.S_IMODE(after.st_mode) & stat.S_ISVTX)
        if (resolved != current or stat.S_ISLNK(before.st_mode) or stat.S_ISLNK(after.st_mode)
                or not stat.S_ISDIR(after.st_mode) or after.st_uid != 0
                or (stat.S_IMODE(after.st_mode) & 0o022 and not sticky_root_directory)):
            raise ReceiveError("root-only directory is unsafe")
    if stat.S_IMODE(path.lstat().st_mode) & 0o077:
        raise ReceiveError("root-only directory is unsafe")
    return path

def require_writable_receive_staging_volume(root):
    """Return a usable statvfs state or fail before any receiver write."""
    readonly_flag = getattr(os, "ST_RDONLY", None)
    if (isinstance(readonly_flag, bool) or not isinstance(readonly_flag, int)
            or readonly_flag <= 0):
        raise ReceiveError("cannot determine static receive staging-volume read-only status")
    try:
        state = os.statvfs(root)
        mount_flags = state.f_flag
    except (AttributeError, OSError, OverflowError, TypeError, ValueError) as exc:
        raise ReceiveError("cannot inspect static receive staging-volume mount flags") from exc
    if (isinstance(mount_flags, bool) or not isinstance(mount_flags, int)
            or mount_flags < 0):
        raise ReceiveError("static receive staging-volume mount flags are invalid")
    if mount_flags & readonly_flag:
        raise ReceiveError("static receive fixed staging volume is mounted read-only")
    return state

def require_receive_capacity(receipt, mount_state):
    """Reserve ciphertext plus plaintext before creating an exact candidate."""
    try:
        available = mount_state.f_bavail * mount_state.f_frsize
    except (AttributeError, OverflowError, TypeError, ValueError) as exc:
        raise ReceiveError("static receive capacity is unavailable") from exc
    if (isinstance(available, bool) or not isinstance(available, int) or available < 0):
        raise ReceiveError("static receive capacity is invalid")
    object_value = receipt["object"]
    required = (
        object_value["ciphertext_bytes"]
        + object_value["plaintext_bytes"]
        + STATIC_RECEIVE_RECEIPT_RESERVE_BYTES
        + STATIC_RECEIVE_CAPACITY_MARGIN_BYTES
    )
    if available < required:
        raise ReceiveError("insufficient capacity for the static receive candidate")

def campaign_identity_file(campaign_id):
    if not isinstance(campaign_id, str) or not CAMPAIGN_RE.fullmatch(campaign_id):
        raise ReceiveError("campaign bootstrap identity binding")
    path = PurePosixPath(FIXED_CAMPAIGN_IDENTITY_ROOT) / campaign_id / FIXED_AGE_IDENTITY_SUFFIX
    text = path.as_posix()
    if not path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ReceiveError("campaign bootstrap identity binding")
    return text

def require_root_private_campaign_identity(value):
    path = require_root_private_file(value)
    root = Path(FIXED_CAMPAIGN_IDENTITY_ROOT)
    try:
        relative_parent = path.parent.relative_to(root)
    except ValueError as exc:
        raise ReceiveError("campaign bootstrap identity is outside the fixed root") from exc
    current = root
    for component in (None, *relative_parent.parts):
        if component is not None:
            current = current / component
        try:
            before = current.lstat()
            resolved = current.resolve(strict=True)
            after = resolved.lstat()
        except OSError as exc:
            raise ReceiveError("campaign bootstrap identity parent is unavailable") from exc
        if (resolved != current or stat.S_ISLNK(before.st_mode) or stat.S_ISLNK(after.st_mode)
                or not stat.S_ISDIR(after.st_mode) or after.st_uid != 0 or stat.S_IMODE(after.st_mode) & 0o077):
            raise ReceiveError("campaign bootstrap identity parent is unsafe")
    return path

def require_trusted_executable(value):
    path = Path(value)
    if not path.is_absolute():
        raise ReceiveError("trusted executable path is invalid")
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        after = resolved.lstat()
    except OSError as exc:
        raise ReceiveError("trusted executable is unavailable") from exc
    if (resolved != path or stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(after.st_mode)
            or after.st_uid != 0 or stat.S_IMODE(after.st_mode) & 0o022
            or not stat.S_IMODE(after.st_mode) & 0o100):
        raise ReceiveError("trusted executable is unsafe")
    return path

def write_new_private_json(path, value):
    payload = canonical_json_bytes(value) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ReceiveError("cannot create receive receipt") from exc
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short receipt write")
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except OSError as exc:
        raise ReceiveError("cannot write receive receipt") from exc
    finally:
        os.close(descriptor)
    require_root_private_file(str(path))

def reject_transient_control_url(value):
    encoded = canonical_json_bytes(value).lower()
    if b"presigned" in encoded or b'"url"' in encoded or b"x-amz-" in encoded:
        raise ReceiveError("persistent receiver state contains a transient URL")

def validate_storage(value):
    if not isinstance(value, dict) or set(value) != {"endpoint", "region", "bucket", "prefix"}:
        raise ReceiveError("invalid Object Storage binding")
    endpoint = require_text(value["endpoint"], 512)
    region = require_text(value["region"], 128)
    bucket = require_text(value["bucket"], 63)
    prefix = require_text(value["prefix"], 512)
    parsed = urlparse(endpoint)
    try:
        has_port = parsed.port is not None
    except ValueError as exc:
        raise ReceiveError("invalid Object Storage endpoint") from exc
    if (parsed.scheme != "https" or parsed.hostname != "s3." + region + ".arvanstorage.ir" or has_port
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise ReceiveError("invalid Object Storage endpoint")
    if not BUCKET_RE.fullmatch(bucket) or prefix.strip("/") != prefix or not prefix or any(not PREFIX_COMPONENT_RE.fullmatch(part) for part in prefix.split("/")):
        raise ReceiveError("invalid Object Storage namespace")
    return {"endpoint": endpoint, "region": region, "bucket": bucket, "prefix": prefix}

def require_application(value):
    if not isinstance(value, dict) or set(value) != {"release_sha", "expected_alembic_revision"}:
        raise ReceiveError("invalid application binding")
    if not isinstance(value["release_sha"], str) or not COMMIT_RE.fullmatch(value["release_sha"]):
        raise ReceiveError("invalid application release")
    if not isinstance(value["expected_alembic_revision"], str) or not REVISION_RE.fullmatch(value["expected_alembic_revision"]):
        raise ReceiveError("invalid application revision")
    return dict(value)

def require_tooling(value):
    if not isinstance(value, dict) or set(value) != {"control_commit", "control_tree"}:
        raise ReceiveError("invalid tooling binding")
    if not isinstance(value["control_commit"], str) or not COMMIT_RE.fullmatch(value["control_commit"]):
        raise ReceiveError("invalid tooling commit")
    if not isinstance(value["control_tree"], str) or not COMMIT_RE.fullmatch(value["control_tree"]):
        raise ReceiveError("invalid tooling tree")
    return dict(value)

def require_recipient(value):
    if not isinstance(value, str) or not AGE_RECIPIENT_RE.fullmatch(value):
        raise ReceiveError("invalid age recipient")
    return value

def require_object(value):
    expected = {"object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes", "plaintext_sha256", "plaintext_bytes"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ReceiveError("invalid immutable object")
    key = require_text(value["object_key"], 1024)
    version = require_text(value["version_id"], 1024)
    if version.lower() == "null" or not VERSION_ID_RE.fullmatch(version):
        raise ReceiveError("invalid immutable object version")
    return {
        "object_key": key,
        "version_id": version,
        "ciphertext_sha256": require_sha256(value["ciphertext_sha256"]),
        "ciphertext_bytes": require_size(value["ciphertext_bytes"], MAX_CIPHERTEXT_BYTES),
        "plaintext_sha256": require_sha256(value["plaintext_sha256"]),
        "plaintext_bytes": require_size(value["plaintext_bytes"], MAX_STATIC_ARCHIVE_BYTES),
    }

def source_object_key(storage, receipt):
    return "/".join((
        storage["prefix"], "webapp-fi-source-transport", "v1", receipt["campaign_id"], receipt["release_sha"],
        receipt["control_commit"], receipt["control_tree"], "webapp_fi", "controller_webapp_ir", "static",
        receipt["object_id"] + ".age",
    ))

def validate_transport_receipt(value, config):
    expected = {
        "schema", "status", "campaign_id", "release_sha", "control_commit", "control_tree", "source_site", "destination_site",
        "object_kind", "object_id", "recipient_mode", "recipients", "transport", "object", "receipt_sha256",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ReceiveError("invalid generic source transport receipt")
    if value.get("schema") != TRANSPORT_SCHEMA or value.get("status") != "published":
        raise ReceiveError("invalid generic source transport receipt")
    if value.get("campaign_id") != config["campaign_id"] or not CAMPAIGN_RE.fullmatch(value["campaign_id"]):
        raise ReceiveError("transport receipt campaign binding")
    if value.get("release_sha") != config["application"]["release_sha"] or not COMMIT_RE.fullmatch(value["release_sha"]):
        raise ReceiveError("transport receipt release binding")
    if value.get("control_commit") != config["tooling"]["control_commit"] or not COMMIT_RE.fullmatch(value["control_commit"]):
        raise ReceiveError("transport receipt control binding")
    if value.get("control_tree") != config["tooling"]["control_tree"] or not COMMIT_RE.fullmatch(value["control_tree"]):
        raise ReceiveError("transport receipt control binding")
    if (value.get("source_site") != "webapp_fi" or value.get("destination_site") != "controller_webapp_ir"
            or value.get("object_kind") != "static" or value.get("recipient_mode") != "static"):
        raise ReceiveError("transport receipt route binding")
    if not isinstance(value.get("object_id"), str) or not OBJECT_ID_RE.fullmatch(value["object_id"]):
        raise ReceiveError("transport receipt object ID")
    if value.get("recipients") != [config["controller_age_recipient"], config["wa_ir_age_recipient"]]:
        raise ReceiveError("transport receipt recipient binding")
    expected_transport = {"encryption": "age-v1", "create_only": True, "private_bucket": True, "provider_side_sse": False, "read_back_same_version_id": True}
    if value.get("transport") != expected_transport:
        raise ReceiveError("transport receipt policy binding")
    object_value = require_object(value.get("object"))
    if object_value["object_key"] != source_object_key(config["object_storage"], value):
        raise ReceiveError("transport receipt object key binding")
    unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if value.get("receipt_sha256") != sha256_bytes(canonical_json_bytes(unsigned)):
        raise ReceiveError("transport receipt checksum")
    return {**unsigned, "object": object_value, "receipt_sha256": value["receipt_sha256"]}

# Minimal RFC 8032 Ed25519 verification.  The receiver must verify the
# controller proof before accepting an archive even though SSH conveys only
# control metadata.
Q = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
D = (-121665 * pow(121666, Q - 2, Q)) % Q
I = pow(2, (Q - 1) // 4, Q)
B_Y = (4 * pow(5, Q - 2, Q)) % Q

def edwards_add(left, right):
    x1, y1 = left
    x2, y2 = right
    denominator_x = pow((1 + D * x1 * x2 * y1 * y2) % Q, Q - 2, Q)
    denominator_y = pow((1 - D * x1 * x2 * y1 * y2) % Q, Q - 2, Q)
    return (((x1 * y2 + y1 * x2) * denominator_x) % Q, ((y1 * y2 + x1 * x2) * denominator_y) % Q)

def scalar_mult(point, scalar):
    result = (0, 1)
    current = point
    while scalar:
        if scalar & 1:
            result = edwards_add(result, current)
        current = edwards_add(current, current)
        scalar >>= 1
    return result

def x_recover(y):
    xx = ((y * y - 1) * pow((D * y * y + 1) % Q, Q - 2, Q)) % Q
    x = pow(xx, (Q + 3) // 8, Q)
    if x * x % Q != xx:
        x = (x * I) % Q
    if x * x % Q != xx:
        raise ReceiveError("invalid Ed25519 point")
    if x & 1:
        x = Q - x
    return x

B = (x_recover(B_Y), B_Y)

def decode_point(encoded):
    if not isinstance(encoded, bytes) or len(encoded) != 32:
        raise ReceiveError("invalid Ed25519 point")
    sign = encoded[31] >> 7
    y = int.from_bytes(encoded, "little") & ((1 << 255) - 1)
    if y >= Q:
        raise ReceiveError("invalid Ed25519 point")
    x = x_recover(y)
    if (x & 1) != sign:
        x = Q - x
    point = (x, y)
    if scalar_mult(point, L) != (0, 1) or scalar_mult(point, 8) == (0, 1):
        raise ReceiveError("invalid Ed25519 subgroup")
    return point

def verify_ed25519(public_key_base64, signature_base64, message):
    try:
        public_key = base64.b64decode(public_key_base64.encode("ascii"), validate=True)
        signature = base64.b64decode(signature_base64.encode("ascii"), validate=True)
    except Exception as exc:
        raise ReceiveError("invalid Ed25519 encoding") from exc
    if len(signature) != 64:
        raise ReceiveError("invalid Ed25519 signature")
    encoded_r, encoded_s = signature[:32], signature[32:]
    scalar_s = int.from_bytes(encoded_s, "little")
    if scalar_s >= L:
        raise ReceiveError("invalid Ed25519 signature")
    public_point = decode_point(public_key)
    r_point = decode_point(encoded_r)
    challenge = int.from_bytes(hashlib.sha512(encoded_r + public_key + message).digest(), "little") % L
    if scalar_mult(B, scalar_s) != edwards_add(r_point, scalar_mult(public_point, challenge)):
        raise ReceiveError("static proof signature verification failed")

def validate_static_proof(value, config, receipt):
    expected = {"schema", "status", "campaign_id", "application", "source_kind", "artifact", "files", "files_sha256", "controller_public_key_base64", "controller_signature"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ReceiveError("invalid controller static proof")
    if value.get("schema") != STATIC_PROOF_SCHEMA or value.get("status") != "verified":
        raise ReceiveError("invalid controller static proof")
    if value.get("campaign_id") != config["campaign_id"] or value.get("application") != config["application"]:
        raise ReceiveError("static proof identity binding")
    if value.get("source_kind") != "deterministic_2c08_dist_manifest":
        raise ReceiveError("static proof source binding")
    if require_object(value.get("artifact")) != receipt["object"]:
        raise ReceiveError("static proof immutable object binding")
    files = value.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= 100000:
        raise ReceiveError("static proof file list")
    normalized = []
    previous = ""
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "bytes"}:
            raise ReceiveError("static proof file entry")
        path = item.get("path")
        if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path or "\x00" in path:
            raise ReceiveError("static proof file path")
        pure = PurePosixPath(path)
        if pure.as_posix() != path or any(part in ("", ".", "..") for part in pure.parts) or previous and path <= previous:
            raise ReceiveError("static proof file path")
        previous = path
        if isinstance(item.get("bytes"), bool) or not isinstance(item.get("bytes"), int) or not 0 <= item["bytes"] <= MAX_STATIC_ARCHIVE_BYTES:
            raise ReceiveError("static proof file size")
        normalized.append({"path": path, "sha256": require_sha256(item.get("sha256")), "bytes": item["bytes"]})
    if not any(item["path"] == "index.html" for item in normalized):
        raise ReceiveError("static proof lacks index.html")
    if value.get("files_sha256") != sha256_bytes(canonical_json_bytes(normalized)):
        raise ReceiveError("static proof file hash")
    if value.get("controller_public_key_base64") != config["pinned_controller_public_key_base64"]:
        raise ReceiveError("static proof controller key binding")
    signature = value.get("controller_signature")
    if not isinstance(signature, dict) or set(signature) != {"algorithm", "signature_base64"} or signature.get("algorithm") != "ed25519":
        raise ReceiveError("static proof signature envelope")
    unsigned = {key: item for key, item in value.items() if key != "controller_signature"}
    verify_ed25519(config["pinned_controller_public_key_base64"], signature.get("signature_base64"), STATIC_PROOF_SIGNATURE_DOMAIN + canonical_json_bytes(unsigned))

def validate_url(value, config, receipt):
    url = require_text(value, MAX_URL_BYTES)
    if any(character.isspace() for character in url):
        raise ReceiveError("invalid download URL")
    parsed = urlparse(url)
    endpoint = urlparse(config["object_storage"]["endpoint"])
    try:
        has_port = parsed.port is not None
    except ValueError as exc:
        raise ReceiveError("invalid download URL") from exc
    expected_path = "/" + quote(config["object_storage"]["bucket"], safe="") + "/" + quote(receipt["object"]["object_key"], safe="/")
    if (parsed.scheme != "https" or parsed.hostname != endpoint.hostname or has_port or parsed.username or parsed.password
            or parsed.fragment or parsed.path != expected_path):
        raise ReceiveError("download URL binding")
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise ReceiveError("invalid download URL query") from exc
    required = {"versionId", "X-Amz-Algorithm", "X-Amz-Credential", "X-Amz-Date", "X-Amz-Expires", "X-Amz-SignedHeaders", "X-Amz-Signature"}
    optional = {"X-Amz-Security-Token"}
    if set(query) - required - optional or not required.issubset(query):
        raise ReceiveError("download URL signature envelope")
    if any(len(query[name]) != 1 or not query[name][0] for name in required):
        raise ReceiveError("download URL signature envelope")
    if "X-Amz-Security-Token" in query and (len(query["X-Amz-Security-Token"]) != 1 or not query["X-Amz-Security-Token"][0]):
        raise ReceiveError("download URL signature envelope")
    if query.get("versionId") != [receipt["object"]["version_id"]]:
        raise ReceiveError("download URL version binding")
    if query["X-Amz-Algorithm"] != ["AWS4-HMAC-SHA256"] or query["X-Amz-SignedHeaders"] != ["host"]:
        raise ReceiveError("download URL SigV4 binding")
    signing_time = query["X-Amz-Date"][0]
    expires_text = query["X-Amz-Expires"][0]
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", signing_time) or not expires_text.isdecimal():
        raise ReceiveError("download URL expiry")
    try:
        issued = dt.datetime.strptime(signing_time, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
        expires = int(expires_text, 10)
    except (TypeError, ValueError) as exc:
        raise ReceiveError("download URL expiry") from exc
    credential = query["X-Amz-Credential"][0].split("/")
    if (len(credential) != 5 or not credential[0] or any(not item or any(ord(character) < 0x21 or ord(character) == 0x7f for character in item) for item in credential)
            or credential[1] != signing_time[:8]
            or credential[2] != config["object_storage"]["region"] or credential[3] != "s3" or credential[4] != "aws4_request"):
        raise ReceiveError("download URL credential scope")
    if not re.fullmatch(r"[0-9a-f]{64}", query["X-Amz-Signature"][0]):
        raise ReceiveError("download URL signature")
    now = dt.datetime.now(dt.timezone.utc)
    if not 1 <= expires <= MAX_PRESIGNED_LIFETIME_SECONDS or now < issued or now > issued + dt.timedelta(seconds=expires):
        raise ReceiveError("download URL expiry")
    return url

def parse_header_blocks(raw):
    try:
        text = raw.decode("iso-8859-1")
    except UnicodeDecodeError as exc:
        raise ReceiveError("invalid response headers") from exc
    blocks = []
    for block in re.split(r"\r?\n\r?\n", text):
        if not block:
            continue
        lines = block.splitlines()
        if not lines or not re.fullmatch(r"HTTP/\d(?:\.\d)?\s+\d{3}(?:\s+.*)?", lines[0]):
            raise ReceiveError("invalid response headers")
        status = int(lines[0].split()[1])
        headers = {}
        for line in lines[1:]:
            if not line or line[0] in " \t" or ":" not in line:
                raise ReceiveError("invalid response headers")
            name, header_value = line.split(":", 1)
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
                raise ReceiveError("invalid response headers")
            headers.setdefault(name.lower(), []).append(header_value.strip())
        if 300 <= status < 400 or "location" in headers:
            raise ReceiveError("redirect response")
        blocks.append((status, headers))
    if not blocks or blocks[-1][0] != 200:
        raise ReceiveError("download response is not HTTP 200")
    return blocks[-1][1]

def validate_headers(raw, receipt):
    headers = parse_header_blocks(raw)
    object_value = receipt["object"]
    expected = {
        "x-amz-version-id": object_value["version_id"],
        "x-amz-meta-transport-schema": TRANSPORT_SCHEMA,
        "x-amz-meta-encryption": OBJECT_ENCRYPTION,
        "x-amz-meta-ciphertext-sha256": object_value["ciphertext_sha256"],
        "x-amz-meta-recipient-mode": "static",
    }
    if any(name.startswith("x-amz-server-side-encryption") for name in headers):
        raise ReceiveError("provider-side encryption is disallowed")
    for name, expected_value in expected.items():
        if headers.get(name) != [expected_value]:
            raise ReceiveError("response metadata mismatch")
    length = headers.get("content-length")
    if length is None or len(length) != 1 or not re.fullmatch(r"[0-9]+", length[0]) or int(length[0]) != object_value["ciphertext_bytes"]:
        raise ReceiveError("response length mismatch")

def load_config(encoded):
    try:
        raw = base64.b64decode(encoded, validate=True)
        if not 1 <= len(raw) <= MAX_REMOTE_CONFIG_BYTES:
            raise ValueError("size")
        config = json.loads(raw.decode("ascii"), object_pairs_hook=strict_object, parse_constant=reject_constant)
    except Exception as exc:
        raise ReceiveError("receiver configuration invalid") from exc
    if not isinstance(config, dict) or raw != canonical_json_bytes(config):
        raise ReceiveError("receiver configuration invalid")
    expected = {"schema", "receiver_root", "age_identity_file", "object_storage", "campaign_id", "application", "tooling", "controller_age_recipient", "wa_ir_age_recipient", "transport_receipt", "static_assets_provenance", "pinned_controller_public_key_base64"}
    if set(config) != expected or config.get("schema") != CONFIG_SCHEMA:
        raise ReceiveError("receiver configuration invalid")
    reject_transient_control_url(config)
    if config.get("receiver_root") != FIXED_RECEIVER_ROOT:
        raise ReceiveError("receiver root binding")
    if not isinstance(config.get("campaign_id"), str) or not CAMPAIGN_RE.fullmatch(config["campaign_id"]):
        raise ReceiveError("campaign binding")
    if config.get("age_identity_file") != campaign_identity_file(config["campaign_id"]):
        raise ReceiveError("campaign bootstrap identity binding")
    storage = validate_storage(config.get("object_storage"))
    application = require_application(config.get("application"))
    tooling = require_tooling(config.get("tooling"))
    controller_recipient = require_recipient(config.get("controller_age_recipient"))
    wa_ir_recipient = require_recipient(config.get("wa_ir_age_recipient"))
    if controller_recipient == wa_ir_recipient:
        raise ReceiveError("static recipient binding")
    try:
        key = base64.b64decode(config.get("pinned_controller_public_key_base64", "").encode("ascii"), validate=True)
    except Exception as exc:
        raise ReceiveError("pinned controller key") from exc
    if len(key) != 32:
        raise ReceiveError("pinned controller key")
    normalized = {
        "schema": CONFIG_SCHEMA,
        "receiver_root": FIXED_RECEIVER_ROOT,
        "age_identity_file": config["age_identity_file"],
        "object_storage": storage,
        "campaign_id": config["campaign_id"],
        "application": application,
        "tooling": tooling,
        "controller_age_recipient": controller_recipient,
        "wa_ir_age_recipient": wa_ir_recipient,
        "transport_receipt": config["transport_receipt"],
        "static_assets_provenance": config["static_assets_provenance"],
        "pinned_controller_public_key_base64": config["pinned_controller_public_key_base64"],
    }
    receipt = validate_transport_receipt(normalized["transport_receipt"], normalized)
    validate_static_proof(normalized["static_assets_provenance"], normalized, receipt)
    normalized["transport_receipt"] = receipt
    return normalized

def build_receive_receipt(config, receipt):
    object_value = receipt["object"]
    return {
        "schema": STATIC_RECEIVE_RECEIPT_SCHEMA,
        "status": "read_back",
        "campaign_id": config["campaign_id"],
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "object": object_value,
        "transport": {"transport": "private_versioned_age_only", "create_only": True, "read_back_same_version_id": True, "provider_side_sse": False},
        "age_decryption": {"algorithm": "age-v1", "wa_ir_identity_scope": "root_only", "ciphertext_sha256_verified_before_decrypt": True, "plaintext_sha256_verified_after_decrypt": True},
    }

def receive(config, url):
    receipt = config["transport_receipt"]
    url = validate_url(url, config, receipt)
    for executable in (CURL_BINARY, AGE_BINARY):
        require_trusted_executable(executable)
    identity = require_root_private_campaign_identity(config["age_identity_file"])
    root = require_root_private_directory(config["receiver_root"])
    # Inspect the fixed staging mount before capacity, candidate creation, or
    # network I/O.  The second checks below cover a remount after admission.
    require_receive_capacity(receipt, require_writable_receive_staging_volume(root))
    version_tag = sha256_bytes(receipt["object"]["version_id"].encode("ascii"))[:16]
    candidate = root / ("static-" + config["application"]["release_sha"] + "-" + config["tooling"]["control_commit"] + "-" + receipt["object_id"] + "-" + version_tag)
    if candidate.parent != root:
        raise ReceiveError("static receive candidate path")
    old_umask = os.umask(0o077)
    try:
        try:
            require_writable_receive_staging_volume(root)
            os.mkdir(candidate, 0o700)
        except FileExistsError as exc:
            raise ReceiveError("static receive candidate already exists") from exc
        state = candidate.lstat()
        if not stat.S_ISDIR(state.st_mode) or state.st_uid != 0 or stat.S_IMODE(state.st_mode) != 0o700:
            raise ReceiveError("static receive candidate creation")
        ciphertext = candidate / ".ciphertext.age"
        archive = candidate / STATIC_ARCHIVE_NAME
        require_writable_receive_staging_volume(root)
        result = subprocess.run(
            [CURL_BINARY, "--disable", "--silent", "--show-error", "--fail", "--globoff", "--noproxy", "*", "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "0", "--connect-timeout", "20", "--max-time", "180", "--dump-header", "-", "--output", str(ciphertext), "--", url],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        )
        if result.returncode != 0:
            raise ReceiveError("direct Object Storage download failed")
        validate_headers(result.stdout, receipt)
        require_root_private_file(str(ciphertext))
        if sha256_file(ciphertext, MAX_CIPHERTEXT_BYTES) != (receipt["object"]["ciphertext_sha256"], receipt["object"]["ciphertext_bytes"]):
            raise ReceiveError("ciphertext binding")
        result = subprocess.run(
            [AGE_BINARY, "--decrypt", "--identity", str(identity), "--output", str(archive), str(ciphertext)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if result.returncode != 0:
            raise ReceiveError("age decryption failed")
        require_root_private_file(str(archive))
        if sha256_file(archive, MAX_STATIC_ARCHIVE_BYTES) != (receipt["object"]["plaintext_sha256"], receipt["object"]["plaintext_bytes"]):
            raise ReceiveError("plaintext binding")
        os.unlink(ciphertext)
        receive_receipt = build_receive_receipt(config, receipt)
        write_new_private_json(candidate / STATIC_RECEIPT_NAME, receive_receipt)
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        print(json.dumps({"status": "received", "candidate_directory": str(candidate), "static_archive": str(archive), "receipt": str(candidate / STATIC_RECEIPT_NAME)}, sort_keys=True))
    finally:
        os.umask(old_umask)

def main():
    try:
        if os.geteuid() != 0:
            raise ReceiveError("receiver must run as root")
        if len(sys.argv) != 5 or sys.argv[3] != "--":
            raise ReceiveError("invalid receive arguments")
        config = load_config(sys.argv[2])
        receive(config, sys.argv[4])
    except Exception:
        print(json.dumps({"status": "blocked", "error": "static receive verification failed"}, sort_keys=True))
        return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
'''


REMOTE_LAUNCHER = "import base64,sys;exec(compile(base64.b64decode(sys.argv[1]),'<wa-ir-static-receive>','exec'))"


def render_receive_command(
    *,
    transport_publish_receipt: Path,
    source_transport_config: Path,
    static_assets_provenance: Path,
    pinned_controller_public_key_base64: str,
    presigned_url: str,
    receiver_root: str = DEFAULT_RECEIVER_ROOT,
) -> str:
    """Return one SSH control command after all controller-local checks pass."""

    _require_fixed_receiver_root(receiver_root)
    controller_config = _load_transport_config(source_transport_config)
    policy = controller_config.policy
    transport_payload = _read_root_only_file(
        transport_publish_receipt,
        field="generic static source transport receipt",
    )
    published = _verify_generic_static_receipt(payload=transport_payload, policy=policy)
    try:
        controller_config = transport.require_controller_config_for_campaign(
            controller_config=controller_config,
            campaign_id=published["campaign_id"],
        )
    except Exception as exc:
        raise StaticReceiveRenderError(
            "source transport controller configuration does not bind the published campaign"
        ) from exc
    policy = controller_config.policy
    pinned_key = _require_text(
        pinned_controller_public_key_base64,
        field="pinned controller public key",
        maximum=128,
    )
    proof_payload = _read_root_only_file(
        static_assets_provenance,
        field="controller-signed static asset provenance",
    )
    verified_proof = _verify_static_proof(
        payload=proof_payload,
        published=published,
        pinned_controller_public_key_base64=pinned_key,
    )
    url = _validate_presigned_url(value=presigned_url, policy=policy, published=published)
    remote_config = _build_remote_config(
        policy=policy,
        published=published,
        static_proof=verified_proof["proof"],
        application=verified_proof["application"],
        pinned_controller_public_key_base64=pinned_key,
    )
    _assert_control_only_remote_config(remote_config)
    program_b64 = base64.b64encode(REMOTE_RECEIVER_SOURCE.encode("utf-8")).decode("ascii")
    config_b64 = base64.b64encode(canonical_json_bytes(remote_config)).decode("ascii")
    remote = shlex.join(["/usr/bin/python3", "-I", "-B", "-c", REMOTE_LAUNCHER, program_b64, config_b64, "--", url])
    return shlex.join(["ssh", *SSH_OPTIONS, REMOTE_HOST, remote])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Direct rendering is disabled; transient receive controls require an in-process executor.",
    )
    parser.add_argument("--transport-publish-receipt", type=Path, required=True)
    parser.add_argument("--source-transport-config", type=Path, required=True)
    parser.add_argument("--static-assets-provenance", type=Path, required=True)
    parser.add_argument("--pinned-controller-public-key-base64", required=True)
    parser.add_argument("--presigned-url-stdin", action="store_true", required=True)
    parser.add_argument("--receiver-root", default=DEFAULT_RECEIVER_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _reject_direct_url_render()
    except StaticReceiveRenderError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
