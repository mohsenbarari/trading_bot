#!/usr/bin/env python3
"""First, bounded installer for the independently attested WA-FI source anchor.

This program is intentionally separate from the anchor it installs.  It has
no path, key, credential, service, Docker, or SSH-payload option.  A trusted
root setup places this tiny installer on WA-FI once.  Thereafter the installer
accepts only a short-lived version-bound HTTPS URL on stdin, reads the fixed
root-only anchor approval receipt, downloads the anchor directly from private
Arvan Object Storage, verifies its exact hash/size, and publishes it with a
create-only hard link at the one fixed anchor path.  It never executes the
downloaded bytes.

The initial placement of *this* installer is necessarily an independent root
trust ceremony; it cannot be bootstrapped by executing an unverified remote
artifact.  That ceremony has a machine-checkable contract: before Python is
allowed to run this file, an operator retrieves its bytes directly from the
approved private versioned Object Storage object (never SSH), verifies the
independently approved SHA-256/size, create-only installs it at
``INSTALLER_PATH`` as root:root 0600 below a root:root 0700 parent, and
create-only writes ``INSTALLER_APPROVAL_PATH``.  Every later invocation
verifies that local receipt against the running bytes before it can inspect
an anchor URL or contact Object Storage.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import stat
import sys
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener


APPROVAL_SCHEMA = "gold-trade-webapp-fi-emergency-source-anchor-approval-v2"
INSTALLER_APPROVAL_SCHEMA = "gold-trade-webapp-fi-emergency-source-installer-approval-v2"
INSTALLER_PLACEMENT_SCOPE = "webapp-fi-emergency-source-first-installer"
ANCHOR_OBJECT_LAYOUT = "webapp-fi-emergency-source-anchor/v1"
INSTALLER_OBJECT_LAYOUT = "webapp-fi-emergency-source-installer/v1"
TRUST_ROOT = Path("/etc/trading-bot-three-site/trust")
APPROVAL_PATH = TRUST_ROOT / "webapp-fi-emergency-source-anchor-approval.json"
INSTALLER_APPROVAL_PATH = TRUST_ROOT / "webapp-fi-emergency-source-installer-approval.json"
INSTALLER_PATH = Path("/usr/local/lib/trading-bot-three-site/install_webapp_fi_emergency_source_anchor.py")
ANCHOR_PATH = Path("/usr/local/lib/trading-bot-three-site/run_webapp_fi_emergency_source_receive.py")

MAX_APPROVAL_BYTES = 4096
MAX_URL_BYTES = 16 * 1024
MAX_ANCHOR_BYTES = 2 * 1024 * 1024
MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 900
CHUNK_BYTES = 64 * 1024

SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
GIT_SHA_RE = re.compile(r"^[a-f0-9]{40}$", re.ASCII)
VERSION_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{1,1024}$", re.ASCII)
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$", re.ASCII)
PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,127}$", re.ASCII)
REGION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$", re.ASCII)
_REQUIRED_QUERY = frozenset(
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
_OPTIONAL_QUERY = frozenset({"X-Amz-Security-Token"})
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


class AnchorInstallerError(RuntimeError):
    pass


def _fail(message: str) -> None:
    raise AnchorInstallerError(message)


def _require_root() -> None:
    if os.geteuid() != 0:
        _fail("anchor installer must run as root")


def _require_direct_object_storage_environment() -> None:
    """Reject ambient routing and TLS trust overrides before a direct GET.

    ``ProxyHandler({})`` is deliberately retained as a second line of
    defense, but OpenSSL still honors ``SSL_CERT_FILE`` and ``SSL_CERT_DIR``
    even under ``python3 -I``.  This first-stage installer has no legitimate
    need for any proxy or custom CA input, so every such inherited setting is
    a fail-closed condition before it creates a target directory or opens a
    socket.
    """

    forbidden = sorted(key for key in os.environ if key.lower() in _DIRECT_TRANSPORT_ENVIRONMENT_NAMES)
    if forbidden:
        _fail("direct Object Storage transport forbids proxy and TLS override environment variables")


def _canonical(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    except (TypeError, ValueError, RecursionError) as exc:
        raise AnchorInstallerError("approval value cannot be canonicalized") from exc


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("approval JSON has duplicate fields")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    _fail("approval JSON constants are unsupported")


def _text(value: object, *, field: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        _fail(f"{field} is invalid")
    if "\x00" in value or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        _fail(f"{field} has a control character")
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


def _safe_dir(path: Path, *, label: str, private: bool) -> Path:
    if not path.is_absolute():
        _fail(f"{label} must be absolute")
    current = Path(path.anchor)
    final: os.stat_result | None = None
    for component in path.parts[1:]:
        current /= component
        try:
            state = current.lstat()
        except OSError as exc:
            raise AnchorInstallerError(f"{label} cannot be inspected") from exc
        mode = stat.S_IMODE(state.st_mode)
        sticky_root = state.st_uid == 0 and bool(state.st_mode & stat.S_ISVTX)
        if (
            stat.S_ISLNK(state.st_mode)
            or not stat.S_ISDIR(state.st_mode)
            or state.st_uid != 0
            or (mode & 0o022 and not sticky_root)
        ):
            _fail(f"{label} is not root-controlled")
        final = state
    if final is None or (private and stat.S_IMODE(final.st_mode) != 0o700):
        _fail(f"{label} is not an exact root-only 0700 directory")
    return path


def _safe_file(path: Path, *, label: str, maximum: int, private: bool) -> Path:
    if not path.is_absolute():
        _fail(f"{label} must be absolute")
    _safe_dir(path.parent, label=f"{label} parent", private=False)
    try:
        state = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AnchorInstallerError(f"{label} cannot be inspected") from exc
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
            chunk = os.read(fd, min(CHUNK_BYTES, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(fd)
        if len(payload) != opened.st_size or len(payload) > maximum or any(
            getattr(opened, field) != getattr(after, field) for field in fields
        ):
            _fail(f"{label} changed while reading")
        return bytes(payload)
    except OSError as exc:
        raise AnchorInstallerError(f"{label} cannot be read") from exc
    finally:
        if fd is not None:
            os.close(fd)


def _fsync_dir(path: Path, *, label: str) -> None:
    _safe_dir(path, label=label, private=False)
    fd: int | None = None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        state = os.fstat(fd)
        if not stat.S_ISDIR(state.st_mode) or state.st_uid != 0:
            _fail(f"{label} changed while fsyncing")
        os.fsync(fd)
    except OSError as exc:
        raise AnchorInstallerError(f"{label} cannot be fsynced") from exc
    finally:
        if fd is not None:
            os.close(fd)


def _endpoint(value: object, region: object) -> tuple[str, str]:
    endpoint = _text(value, field="endpoint")
    region = _pattern(region, field="region", pattern=REGION_RE)
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise AnchorInstallerError("endpoint is malformed") from exc
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
        _fail("endpoint is not the fixed Arvan S3 HTTPS endpoint")
    return endpoint.rstrip("/"), region


def _prefix(value: object) -> str:
    prefix = _text(value, field="prefix").strip("/")
    if not prefix or any(PREFIX_RE.fullmatch(part) is None for part in prefix.split("/")):
        _fail("prefix is unsafe")
    return prefix


def _anchor_key(*, prefix: str, revision: str, sha256: str) -> str:
    return "/".join((prefix, ANCHOR_OBJECT_LAYOUT, revision, sha256 + ".py"))


def _installer_key(*, prefix: str, revision: str, sha256: str) -> str:
    return "/".join((prefix, INSTALLER_OBJECT_LAYOUT, revision, sha256 + ".py"))


def _require_trust_root() -> Path:
    """Require—not silently create—the first-placement trust root.

    The receipt directory is itself part of the trust ceremony.  If it is
    missing or empty, this program must fail closed rather than creating a
    directory and then accepting a controller-provided replacement record.
    A root console pre-provisions it as root:root 0700 before the installer
    receipt is written.
    """

    return _safe_dir(TRUST_ROOT, label="first-placement trust root", private=True)


def _current_installer_payload() -> bytes:
    """Read the fixed installer that is actually executing this code.

    This is deliberately not a command-line path.  The one unavoidable
    first-placement ceremony is checked on every later invocation, so a
    copied script elsewhere cannot use its local receipt as a downloader.
    """

    current = Path(__file__).resolve()
    if current != INSTALLER_PATH:
        _fail("anchor installer is not running from its fixed first-placement path")
    _safe_dir(INSTALLER_PATH.parent, label="fixed first-installer directory", private=True)
    return _read_file(
        current,
        label="installed first WebApp-FI Emergency source anchor installer",
        maximum=MAX_ANCHOR_BYTES,
        private=True,
    )


def load_installer_approval() -> dict[str, Any]:
    """Validate the independent, create-only root trust for this installer.

    There is intentionally no command that creates this receipt: accepting a
    receipt from the same program that it authorizes would collapse the first
    trust boundary.  Its creation is the documented direct-Object-Storage,
    pre-execution root-console ceremony.
    """

    _require_trust_root()
    payload = _read_file(
        INSTALLER_APPROVAL_PATH,
        label="independent first installer approval receipt",
        maximum=MAX_APPROVAL_BYTES,
        private=True,
    )
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_constant)
    except AnchorInstallerError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise AnchorInstallerError("independent first installer approval receipt is not strict JSON") from exc
    if not isinstance(value, Mapping) or _canonical(dict(value)) + b"\n" != payload:
        _fail("independent first installer approval receipt is not canonical JSON")
    fields = {
        "schema",
        "installer_path",
        "endpoint",
        "region",
        "bucket",
        "prefix",
        "object_key",
        "artifact_version_id",
        "installer_sha256",
        "installer_bytes",
        "controller_revision",
        "controller_tree",
        "controller_tool_sha256",
        "controller_tool_bytes",
        "placement_scope",
    }
    if set(value) != fields or value.get("schema") != INSTALLER_APPROVAL_SCHEMA:
        _fail("independent first installer approval receipt fields are unsupported")
    if _text(value.get("installer_path"), field="approved installer path", maximum=2048) != str(INSTALLER_PATH):
        _fail("independent first installer approval receipt targets a different path")
    if value.get("placement_scope") != INSTALLER_PLACEMENT_SCOPE:
        _fail("independent first installer approval receipt scope is unsupported")
    endpoint, region = _endpoint(value.get("endpoint"), value.get("region"))
    bucket = _pattern(value.get("bucket"), field="approved installer bucket", pattern=BUCKET_RE)
    prefix = _prefix(value.get("prefix"))
    revision = _pattern(value.get("controller_revision"), field="approved installer controller revision", pattern=GIT_SHA_RE)
    sha256 = _pattern(value.get("installer_sha256"), field="approved installer SHA-256", pattern=SHA256_RE)
    object_key = _text(value.get("object_key"), field="approved installer object key", maximum=2048)
    if object_key != _installer_key(prefix=prefix, revision=revision, sha256=sha256):
        _fail("independent first installer approval receipt object key is not deterministic")
    approved = {
        "endpoint": endpoint,
        "region": region,
        "bucket": bucket,
        "prefix": prefix,
        "object_key": object_key,
        "artifact_version_id": _pattern(value.get("artifact_version_id"), field="approved installer artifact VersionId", pattern=VERSION_RE),
        "installer_sha256": sha256,
        "installer_bytes": _positive(value.get("installer_bytes"), field="approved installer bytes", maximum=MAX_ANCHOR_BYTES),
        "controller_revision": revision,
        "controller_tree": _pattern(value.get("controller_tree"), field="approved installer controller tree", pattern=GIT_SHA_RE),
        "controller_tool_sha256": _pattern(value.get("controller_tool_sha256"), field="approved installer controller tool SHA-256", pattern=SHA256_RE),
        "controller_tool_bytes": _positive(value.get("controller_tool_bytes"), field="approved installer controller tool bytes", maximum=MAX_ANCHOR_BYTES),
    }
    installed = _current_installer_payload()
    if (hashlib.sha256(installed).hexdigest(), len(installed)) != (
        approved["installer_sha256"],
        approved["installer_bytes"],
    ):
        _fail("installed first anchor installer differs from its independent approval receipt")
    return approved


def first_placement_contract() -> dict[str, object]:
    """Return the non-authorizing, immutable first-placement contract.

    This is intentionally data rather than an installer command: before the
    receipt exists it is only an operator checklist, never authority for the
    untrusted bytes to create their own receipt.
    """

    return {
        "schema": "gold-trade-webapp-fi-emergency-source-first-placement-contract-v1",
        "installer_path": str(INSTALLER_PATH),
        "installer_mode": "0600",
        "installer_parent_mode": "0700",
        "trust_root": str(TRUST_ROOT),
        "trust_root_mode": "0700",
        "installer_approval_path": str(INSTALLER_APPROVAL_PATH),
        "installer_approval_schema": INSTALLER_APPROVAL_SCHEMA,
        "installer_approval_mode": "0600",
        "payload_transport": "private-versioned-arvan-object-storage-direct-get-only",
        "ssh_artifact_bytes": "forbidden",
        "pre_execution_requirement": "verify independent sha256 and byte length before python execution",
        "write_policy": "create-only-root-console-first-placement",
    }


def load_approval() -> dict[str, Any]:
    # This always happens before parsing or using a URL.  It is safe for
    # callers of `load_approval()` to rely on the installer trust check too.
    installer_approval = load_installer_approval()
    payload = _read_file(APPROVAL_PATH, label="independent anchor approval receipt", maximum=MAX_APPROVAL_BYTES, private=True)
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_constant)
    except AnchorInstallerError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise AnchorInstallerError("independent anchor approval receipt is not strict JSON") from exc
    if not isinstance(value, Mapping) or _canonical(dict(value)) + b"\n" != payload:
        _fail("independent anchor approval receipt is not canonical JSON")
    fields = {
        "schema", "anchor_path", "endpoint", "region", "bucket", "prefix", "object_key", "artifact_version_id",
        "anchor_sha256", "anchor_bytes", "controller_revision", "controller_tree",
        "controller_tool_sha256", "controller_tool_bytes",
    }
    if set(value) != fields or value.get("schema") != APPROVAL_SCHEMA:
        _fail("independent anchor approval receipt fields are unsupported")
    if _text(value.get("anchor_path"), field="anchor path", maximum=2048) != str(ANCHOR_PATH):
        _fail("independent anchor approval receipt targets a different anchor path")
    endpoint, region = _endpoint(value.get("endpoint"), value.get("region"))
    bucket = _pattern(value.get("bucket"), field="bucket", pattern=BUCKET_RE)
    prefix = _prefix(value.get("prefix"))
    revision = _pattern(value.get("controller_revision"), field="controller revision", pattern=GIT_SHA_RE)
    sha256 = _pattern(value.get("anchor_sha256"), field="anchor SHA-256", pattern=SHA256_RE)
    key = _text(value.get("object_key"), field="anchor object key", maximum=2048)
    if key != _anchor_key(prefix=prefix, revision=revision, sha256=sha256):
        _fail("independent anchor approval receipt object key is not deterministic")
    approval = {
        "endpoint": endpoint,
        "region": region,
        "bucket": bucket,
        "prefix": prefix,
        "object_key": key,
        "artifact_version_id": _pattern(value.get("artifact_version_id"), field="anchor artifact VersionId", pattern=VERSION_RE),
        "anchor_sha256": sha256,
        "anchor_bytes": _positive(value.get("anchor_bytes"), field="anchor bytes", maximum=MAX_ANCHOR_BYTES),
        "controller_revision": revision,
        "controller_tree": _pattern(value.get("controller_tree"), field="controller tree", pattern=GIT_SHA_RE),
        "controller_tool_sha256": _pattern(value.get("controller_tool_sha256"), field="approved controller tool SHA-256", pattern=SHA256_RE),
        "controller_tool_bytes": _positive(value.get("controller_tool_bytes"), field="approved controller tool bytes", maximum=MAX_ANCHOR_BYTES),
    }
    _require_matching_control_context(installer_approval=installer_approval, anchor_approval=approval)
    return approval


def _require_matching_control_context(
    *, installer_approval: Mapping[str, Any], anchor_approval: Mapping[str, Any]
) -> None:
    """Keep the two immutable first-stage receipts in one control release.

    The first installer is intentionally byte-identical across some control
    revisions.  Its self-hash alone therefore cannot prove that a separately
    supplied anchor receipt belongs to the same frozen controller/source
    release.  Reject a mixed receipt set before accepting a URL or opening
    any network transport.
    """

    for field in (
        "endpoint",
        "region",
        "bucket",
        "prefix",
        "controller_revision",
        "controller_tree",
        "controller_tool_sha256",
        "controller_tool_bytes",
    ):
        if installer_approval.get(field) != anchor_approval.get(field):
            _fail("anchor approval control context does not match the installed first-installer")


def _validate_url(*, url: str, approval: Mapping[str, Any]) -> str:
    if not isinstance(url, str) or not url or len(url.encode("utf-8")) > MAX_URL_BYTES:
        _fail("anchor VersionId-bound URL is invalid")
    try:
        parsed = urlsplit(url)
        endpoint = urlsplit(str(approval["endpoint"]))
        port = parsed.port
    except ValueError as exc:
        raise AnchorInstallerError("anchor VersionId-bound URL is malformed") from exc
    host = endpoint.hostname
    bucket = str(approval["bucket"])
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {host, f"{bucket}.{host}"}
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        _fail("anchor VersionId-bound URL endpoint is not allowlisted")
    key = str(approval["object_key"])
    expected_path = "/" + quote(key, safe="/") if parsed.hostname == f"{bucket}.{host}" else "/" + quote(bucket, safe="") + "/" + quote(key, safe="/")
    if parsed.path != expected_path:
        _fail("anchor VersionId-bound URL selects a different object")
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise AnchorInstallerError("anchor VersionId-bound URL query is malformed") from exc
    if set(query) - (_REQUIRED_QUERY | _OPTIONAL_QUERY) or not _REQUIRED_QUERY.issubset(query) or any(len(items) != 1 for items in query.values()):
        _fail("anchor VersionId-bound URL query is unsupported")
    if (
        query["X-Amz-Algorithm"][0] != "AWS4-HMAC-SHA256"
        or query["X-Amz-SignedHeaders"][0] != "host"
        or query["versionId"][0] != approval["artifact_version_id"]
    ):
        _fail("anchor VersionId-bound URL is not bound to the approved artifact")
    try:
        ttl = int(query["X-Amz-Expires"][0], 10)
    except ValueError as exc:
        raise AnchorInstallerError("anchor VersionId-bound URL expiry is invalid") from exc
    if not MIN_TTL_SECONDS <= ttl <= MAX_TTL_SECONDS:
        _fail("anchor VersionId-bound URL expiry is outside its bound")
    return url


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        raise AnchorInstallerError("anchor Object Storage download was redirected")


def _ensure_target_parent() -> Path:
    if ANCHOR_PATH.parent != Path("/usr/local/lib/trading-bot-three-site"):
        _fail("fixed anchor path parent is unsupported")
    parent = ANCHOR_PATH.parent
    base = _safe_dir(parent.parent, label="anchor library parent", private=False)
    try:
        state = parent.lstat()
    except FileNotFoundError:
        try:
            parent.mkdir(mode=0o700)
            os.chmod(parent, 0o700)
            state = parent.lstat()
            _fsync_dir(base, label="anchor library parent")
            _fsync_dir(parent, label="fixed anchor directory")
        except OSError as exc:
            raise AnchorInstallerError("fixed anchor directory cannot be created") from exc
    except OSError as exc:
        raise AnchorInstallerError("fixed anchor directory cannot be inspected") from exc
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode) or state.st_uid != 0 or stat.S_IMODE(state.st_mode) != 0o700:
        _fail("fixed anchor directory is not root-only 0700")
    return _safe_dir(parent, label="fixed anchor directory", private=True)


def install_from_url(*, approval: Mapping[str, Any], url: str) -> dict[str, Any]:
    _require_root()
    _require_direct_object_storage_environment()
    url = _validate_url(url=url, approval=approval)
    parent = _ensure_target_parent()
    if ANCHOR_PATH.exists() or ANCHOR_PATH.is_symlink():
        _fail("fixed WebApp-FI source anchor already exists; replacement is forbidden")
    temporary = parent / ("." + ANCHOR_PATH.name + "." + secrets.token_hex(16) + ".download")
    fd: int | None = None
    created = False
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
        created = True
        opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
        request = Request(url, headers={"User-Agent": "gold-trade-webapp-fi-anchor-installer/1"}, method="GET")
        digest = hashlib.sha256()
        total = 0
        try:
            with opener.open(request, timeout=180) as response:
                if getattr(response, "status", 200) != 200 or response.geturl() != url:
                    _fail("anchor Object Storage response differs from the approved request")
                while True:
                    chunk = response.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes) or total + len(chunk) > int(approval["anchor_bytes"]):
                        _fail("downloaded anchor exceeds its approved bound")
                    view = memoryview(chunk)
                    while view:
                        written = os.write(fd, view)
                        if written <= 0:
                            raise OSError("short anchor write")
                        view = view[written:]
                    total += len(chunk)
                    digest.update(chunk)
        except (HTTPError, URLError, OSError, ssl.SSLError) as exc:
            raise AnchorInstallerError("anchor Object Storage download failed") from exc
        os.fsync(fd)
        if (digest.hexdigest(), total) != (approval["anchor_sha256"], approval["anchor_bytes"]):
            _fail("downloaded anchor differs from independent approved artifact provenance")
    except Exception:
        if created:
            with contextlib.suppress(OSError):
                temporary.unlink()
        raise
    finally:
        if fd is not None:
            os.close(fd)
    try:
        os.link(temporary, ANCHOR_PATH, follow_symlinks=False)
        temporary.unlink()
        _fsync_dir(parent, label="fixed anchor directory")
    except FileExistsError as exc:
        raise AnchorInstallerError("fixed WebApp-FI source anchor already exists; replacement is forbidden") from exc
    except OSError as exc:
        raise AnchorInstallerError("fixed WebApp-FI source anchor cannot be published create-only") from exc
    _safe_file(ANCHOR_PATH, label="installed WebApp-FI source anchor", maximum=MAX_ANCHOR_BYTES, private=True)
    return {
        "status": "installed-local-only",
        "anchor_path": str(ANCHOR_PATH),
        "anchor_sha256": approval["anchor_sha256"],
        "anchor_bytes": approval["anchor_bytes"],
        "artifact_version_id": approval["artifact_version_id"],
        "controller_revision": approval["controller_revision"],
        "controller_tree": approval["controller_tree"],
        "object_storage_transport": "private-versioned-arvan-direct-get",
        "anchor_execution": "not-performed",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-installed-installer",
        action="store_true",
        help="verify only the independently approved fixed first-installer placement",
    )
    parser.add_argument(
        "--print-first-placement-contract",
        action="store_true",
        help="print the non-authorizing root-console first-placement checklist",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        if not sys.flags.isolated or not sys.flags.dont_write_bytecode:
            _fail("anchor installer must be launched with python3 -I -B")
        _require_root()
        args = _parser().parse_args(argv)
        if args.print_first_placement_contract:
            if args.apply or args.confirm is not None or args.verify_installed_installer:
                _fail("first-placement contract cannot be combined with another action")
            print(json.dumps(first_placement_contract(), sort_keys=True))
            return 0
        installer_approval = load_installer_approval()
        if args.verify_installed_installer:
            if args.apply or args.confirm is not None:
                _fail("installer verification cannot request an apply action")
            print(json.dumps({
                "status": "verified-local-only",
                "installer_path": str(INSTALLER_PATH),
                **installer_approval,
                "network_action": False,
                "object_storage_action": False,
            }, sort_keys=True))
            return 0
        approval = load_approval()
        phrase = "install-webapp-fi-emergency-source-anchor:" + str(approval["anchor_sha256"]) + ":" + str(approval["artifact_version_id"])
        if not args.apply:
            print(json.dumps({
                "status": "planned-non-authorizing",
                "anchor_path": str(ANCHOR_PATH),
                "object_key": approval["object_key"],
                "artifact_version_id": approval["artifact_version_id"],
                "anchor_sha256": approval["anchor_sha256"],
                "anchor_bytes": approval["anchor_bytes"],
                "confirmation": phrase,
                "object_storage_action": False,
                "anchor_execution": False,
            }, sort_keys=True))
            return 0
        if args.confirm != phrase:
            _fail("--confirm must exactly equal the approved anchor installation confirmation")
        raw_url = sys.stdin.read(MAX_URL_BYTES + 1).strip()
        if len(raw_url.encode("utf-8")) > MAX_URL_BYTES:
            _fail("anchor VersionId-bound URL exceeds its bound")
        print(json.dumps(install_from_url(approval=approval, url=raw_url), sort_keys=True))
        return 0
    except AnchorInstallerError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
