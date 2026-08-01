#!/usr/bin/env python3
"""Render the bounded, immutable WebApp-FI source-bootstrap receiver.

This controller-local renderer never opens SSH or Object Storage.  It verifies
the generic source-transport receipt, the detached source-adoption package and
the controller-signed delivery envelope, then makes one SSH control command
available only through its reviewed in-process API.  The presigned GET URL is
a transient final argv item; it is deliberately absent from every receipt,
envelope and receiver configuration, and direct CLI rendering is disabled so
the URL cannot be serialized to terminal output.

The embedded receiver is intentionally small and self-contained.  It performs
only one version-bound Object Storage GET, age decryption, creation of a new
root-only receive candidate, construction of the installer-compatible delivery
receipt, and the existing helper's no-write validation followed by its explicit
install.  It never obtains S3 credentials, sends payloads over SSH, or touches
Docker, services, ``current``, application data or volumes.
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


REMOTE_HOST = "root@65.109.220.59"

RECEIVER_CONFIG_SCHEMA = "gold-trade-webapp-fi-source-bootstrap-receive-config-v2"
PACKAGE_ARCHIVE_NAME = "webapp-fi-source-adoption.tar"
PREPARATION_RECEIPT_NAME = "source-adoption-preparation-receipt.json"
DEFAULT_RECEIVER_ROOT = "/srv/trading-bot-three-site-staging-data/webapp-fi-source-bootstrap"
FI_CAMPAIGN_IDENTITY_ROOT = "/etc/trading-bot-three-site/campaigns"
FI_BOOTSTRAP_IDENTITY_SUFFIX = "webapp-fi/bootstrap.agekey"

MAX_CONTROL_FILE_BYTES = 2 * 1024 * 1024
MAX_URL_BYTES = 8192
MAX_ARCHIVE_BYTES = 24 * 1024 * 1024
MAX_CIPHERTEXT_BYTES = MAX_ARCHIVE_BYTES + 1024 * 1024
MAX_PRESIGNED_LIFETIME_SECONDS = 300

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
ALEMBIC_RE = re.compile(r"^[0-9a-f]{12}$")
PACKAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
AGE_RECIPIENT_RE = re.compile(r"^age1[ac-hj-np-z02-9]{20,128}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_ID_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{1,1024}$")

# The SSH control document deliberately contains facts, not deliverable
# material.  Keep this allowlist adjacent to the renderer so a future field
# addition cannot quietly put a credential or a payload in argv.
REMOTE_CONFIG_FIELDS = frozenset(
    {
        "schema",
        "source_site",
        "destination_site",
        "receiver_root",
        "age_identity_file",
        "object_storage",
        "campaign_id",
        "package_id",
        "application",
        "tooling",
        "canonical_release_tree_sha256",
        "fi_bootstrap_recipient",
        "transport_receipt",
        "preparation_receipt",
        "delivery_envelope",
        "pinned_controller_public_key_base64",
    }
)
REMOTE_CONFIG_STORAGE_FIELDS = frozenset({"endpoint", "region", "bucket", "prefix"})
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
        ("delivery_envelope", "controller_public_key_base64"),
        ("delivery_envelope", "controller_signature", "signature_base64"),
    }
)


class SourceBootstrapReceiveRenderError(RuntimeError):
    """The controller cannot safely render a source-bootstrap receive."""


def _reject_direct_url_render() -> None:
    """Fence the transient GET credential from terminal and audit paths."""

    raise SourceBootstrapReceiveRenderError(
        "direct CLI rendering of the URL-bearing WebApp-FI bootstrap receive control is disabled"
    )


def _require_root_controlled_directory_chain(path: Path, *, field: str) -> None:
    """Require the complete code lookup path to be root-controlled.

    The renderer can run as root, so importing a sibling is an executable
    trust boundary.  A root-owned sticky ancestor remains safe for an
    existing root-owned child, while every other writable ancestor is
    rejected.
    """

    if not path.is_absolute():
        raise RuntimeError(f"{field} parent must be absolute")
    current = Path(path.anchor)
    components = (current,)
    for component in path.parts[1:]:
        current = current / component
        components += (current,)
    for current in components:
        try:
            metadata = current.lstat()
        except OSError as exc:  # pragma: no cover - deployment invariant.
            raise RuntimeError(f"cannot inspect {field} parent") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        writable_by_group_or_other = bool(mode & 0o022)
        root_owned_sticky_directory = bool(metadata.st_mode & stat.S_ISVTX)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or (writable_by_group_or_other and not root_owned_sticky_directory)
        ):
            raise RuntimeError(f"{field} parent is not root-controlled")


def _require_root_controlled_code_file(path: Path, *, field: str) -> Path:
    """Return one exact root-owned, non-writable code file without symlinks."""

    if not path.is_absolute():
        raise RuntimeError(f"{field} must be absolute")
    _require_root_controlled_directory_chain(path.parent, field=field)
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        opened = resolved.lstat()
    except OSError as exc:  # pragma: no cover - deployment invariant.
        raise RuntimeError(f"cannot inspect {field}") from exc
    unsafe_bits = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or stat.S_ISLNK(opened.st_mode)
        or not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != 0
        or opened.st_nlink != 1
        or stat.S_IMODE(opened.st_mode) & 0o022
        or opened.st_mode & unsafe_bits
    ):
        raise RuntimeError(f"{field} is not a root-owned non-writable regular non-symlink file")
    return path


def _load_exact_sibling(filename: str, module_name: str) -> Any:
    """Load one root-controlled sibling without consulting ``sys.path``."""

    if (
        not isinstance(filename, str)
        or not filename
        or Path(filename).name != filename
        or filename in {".", ".."}
    ):
        raise RuntimeError("required sibling filename is not a safe leaf name")
    source = _require_root_controlled_code_file(
        Path(__file__),
        field="WebApp-FI source-bootstrap renderer source",
    )
    path = _require_root_controlled_code_file(
        source.with_name(filename),
        field=f"required sibling {filename}",
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError(f"cannot load required sibling verifier: {filename}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        loaded_path = getattr(module, "__file__", None)
        if not isinstance(loaded_path, str) or Path(loaded_path).resolve(strict=True) != path:
            raise RuntimeError(f"required sibling verifier did not load from its exact path: {filename}")
    except BaseException:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
        raise
    return module


transport = _load_exact_sibling("manage_webapp_fi_source_transport.py", "_webapp_fi_source_transport_receiver")
preparer = _load_exact_sibling("prepare_webapp_fi_source_adoption.py", "_webapp_fi_source_preparer_receiver")
provenance = _load_exact_sibling("verify_webapp_fi_source_provenance.py", "_webapp_fi_source_provenance_receiver")
initial = _load_exact_sibling(
    "render_webapp_fi_initial_static_upload.py", "_webapp_fi_source_bootstrap_initial"
)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceBootstrapReceiveRenderError("JSON input contains duplicate keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise SourceBootstrapReceiveRenderError(f"JSON input contains unsupported constant: {value}")


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise SourceBootstrapReceiveRenderError("source bootstrap renderer must run as root")


def _require_absolute(path: Path, *, field: str) -> Path:
    if not path.is_absolute():
        raise SourceBootstrapReceiveRenderError(f"{field} must be an absolute path")
    return path


def _require_safe_ancestors(path: Path, *, field: str) -> None:
    path = _require_absolute(path, field=field)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            state = current.lstat()
        except OSError as exc:
            raise SourceBootstrapReceiveRenderError(f"{field} ancestor does not exist") from exc
        if (
            stat.S_ISLNK(state.st_mode)
            or not stat.S_ISDIR(state.st_mode)
            or state.st_uid != 0
            or stat.S_IMODE(state.st_mode) & 0o022
        ):
            raise SourceBootstrapReceiveRenderError(f"{field} has an unsafe ancestor")


def _read_root_only_file(path: Path, *, field: str, maximum_bytes: int = MAX_CONTROL_FILE_BYTES) -> bytes:
    path = _require_absolute(path, field=field)
    _require_safe_ancestors(path.parent, field=field)
    try:
        state = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SourceBootstrapReceiveRenderError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(state.st_mode)
        or not stat.S_ISREG(state.st_mode)
        or state.st_uid != 0
        or stat.S_IMODE(state.st_mode) & 0o077
        or not 1 <= state.st_size <= maximum_bytes
    ):
        raise SourceBootstrapReceiveRenderError(f"{field} has unsafe ownership, mode, or size")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SourceBootstrapReceiveRenderError(f"cannot securely open {field}") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) & 0o077
            or opened.st_dev != state.st_dev
            or opened.st_ino != state.st_ino
            or opened.st_size != state.st_size
        ):
            raise SourceBootstrapReceiveRenderError(f"{field} changed while being opened")
        payload = b""
        while len(payload) <= maximum_bytes:
            block = os.read(descriptor, min(65536, maximum_bytes + 1 - len(payload)))
            if not block:
                break
            payload += block
        after = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(payload) != opened.st_size or any(getattr(opened, name) != getattr(after, name) for name in identity):
            raise SourceBootstrapReceiveRenderError(f"{field} changed while being read")
        return payload
    finally:
        os.close(descriptor)


def _parse_canonical_json(payload: bytes, *, field: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceBootstrapReceiveRenderError(f"{field} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise SourceBootstrapReceiveRenderError(f"{field} must use canonical JSON")
    return value


def _reject_persisted_url(payload: bytes, *, field: str) -> None:
    lowered = payload.lower()
    if b"://" in lowered or b"presigned" in lowered or b'"url"' in lowered:
        raise SourceBootstrapReceiveRenderError(f"{field} must not persist a URL")


def _require_text(value: object, *, field: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise SourceBootstrapReceiveRenderError(f"{field} is invalid")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise SourceBootstrapReceiveRenderError(f"{field} contains control characters")
    return value


def _require_fixed_receiver_root(value: str) -> str:
    if value != DEFAULT_RECEIVER_ROOT:
        raise SourceBootstrapReceiveRenderError("WebApp-FI source bootstrap receiver root is fixed")
    path = PurePosixPath(value)
    if not path.is_absolute() or path.as_posix() != value or any(part in {"", ".", ".."} for part in path.parts):
        raise SourceBootstrapReceiveRenderError("WebApp-FI source bootstrap receiver root is invalid")
    return value


def webapp_fi_bootstrap_identity_file(campaign_id: object) -> str:
    """Return the one fresh, campaign-bound identity path accepted by FI."""

    if not isinstance(campaign_id, str) or not CAMPAIGN_ID_RE.fullmatch(campaign_id):
        raise SourceBootstrapReceiveRenderError("campaign ID is invalid for the WebApp-FI bootstrap identity")
    path = PurePosixPath(FI_CAMPAIGN_IDENTITY_ROOT) / campaign_id / FI_BOOTSTRAP_IDENTITY_SUFFIX
    value = path.as_posix()
    if not path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SourceBootstrapReceiveRenderError("WebApp-FI bootstrap identity path is invalid")
    return value


def _require_current_presigned_url(value: str, *, now: dt.datetime) -> None:
    """Add a current-time check after the shared strict SigV4 contract.

    ``require_version_bound_presigned_get_url`` rejects unknown/duplicate
    query fields and validates the exact endpoint, object, VersionId,
    credential scope, and signed-header set.  This renderer additionally
    rejects a syntactically valid URL that has already expired.
    """

    try:
        query = parse_qs(urlparse(value).query, keep_blank_values=True, strict_parsing=True)
        issued = dt.datetime.strptime(query["X-Amz-Date"][0], "%Y%m%dT%H%M%SZ").replace(
            tzinfo=dt.timezone.utc
        )
        expires = int(query["X-Amz-Expires"][0], 10)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise SourceBootstrapReceiveRenderError("source bootstrap presigned URL has an invalid current-time envelope") from exc
    now = now.astimezone(dt.timezone.utc)
    if not 1 <= expires <= MAX_PRESIGNED_LIFETIME_SECONDS or now < issued or now > issued + dt.timedelta(
        seconds=expires
    ):
        raise SourceBootstrapReceiveRenderError("source bootstrap presigned URL is not current and short-lived")


def _validate_presigned_url(
    value: object,
    *,
    policy: Any,
    object_value: Mapping[str, Any],
    now: dt.datetime | None = None,
) -> str:
    try:
        url = transport.require_version_bound_presigned_get_url(
            value,
            policy=policy,
            object_key=object_value["object_key"],
            version_id=object_value["version_id"],
        )
    except ValueError as exc:
        raise SourceBootstrapReceiveRenderError("source bootstrap presigned URL is invalid") from exc
    except Exception as exc:
        raise SourceBootstrapReceiveRenderError(
            "source bootstrap presigned URL is not bound to the exact immutable object"
        ) from exc
    _require_current_presigned_url(url, now=now or _utc_now())
    return url


def _load_transport_config(path: Path) -> Any:
    _require_root_execution()
    try:
        return transport.load_controller_config(path)
    except Exception as exc:
        raise SourceBootstrapReceiveRenderError("source transport controller configuration is unsafe") from exc


def _load_transport_policy(path: Path) -> Any:
    """Return the policy for URL-free planning callers only.

    The receive renderer itself retains the complete config long enough to
    bind its path-derived campaign identity to the verified receipt.
    """

    return _load_transport_config(path).policy


def _verify_prepared_package(*, package_directory: Path, preparation_receipt: Path) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    _require_root_execution()
    try:
        receipt_raw = _read_root_only_file(preparation_receipt, field="source-adoption preparation receipt")
        receipt_value = _parse_canonical_json(receipt_raw, field="source-adoption preparation receipt")
        verified = preparer.verify_prepared_source_adoption_package(
            package_directory=package_directory,
            preparation_receipt=preparation_receipt,
            expected_control_commit=receipt_value["tooling"]["control_commit"],
            expected_application_release_sha=receipt_value["application"]["release_sha"],
        )
    except (KeyError, TypeError, preparer.SourceAdoptionPreparationError) as exc:  # type: ignore[attr-defined]
        raise SourceBootstrapReceiveRenderError("source-adoption package preparation inputs are unsafe") from exc
    if verified.get("status") != "verified" or verified.get("archive_path") != str(package_directory / PACKAGE_ARCHIVE_NAME):
        raise SourceBootstrapReceiveRenderError("source-adoption package preparation verification is inconsistent")
    return verified, receipt_value, receipt_raw


def _verify_generic_transport_receipt(*, payload: bytes, policy: Any, prepared: Mapping[str, Any]) -> dict[str, Any]:
    _reject_persisted_url(payload, field="source transport publish receipt")
    try:
        published = transport.verify_publish_receipt(config=policy, payload=payload)
    except Exception as exc:
        raise SourceBootstrapReceiveRenderError("generic source transport publish receipt is unsafe") from exc
    if (
        published.get("source_site") != "bot_fi"
        or published.get("destination_site") != "webapp_fi"
        or published.get("object_kind") != transport.BOOTSTRAP_OBJECT_KIND
        or published.get("recipient_mode") != transport.SINGLE_MODE
        or published.get("object_id") != prepared["package_id"]
        or published.get("release_sha") != prepared["application"]["release_sha"]
        or published.get("control_commit") != prepared["tooling"]["control_commit"]
        or published.get("control_tree") != prepared["tooling"]["control_tree"]
        or published.get("recipients") != [policy.webapp_fi_age_recipient]
        or published["object"].get("plaintext_sha256") != prepared["archive_sha256"]
        or published["object"].get("plaintext_bytes") != prepared["archive_bytes"]
    ):
        raise SourceBootstrapReceiveRenderError("generic source transport receipt does not bind the exact bootstrap package")
    return published


def _verify_delivery_envelope(
    *,
    payload: bytes,
    pinned_controller_public_key_base64: str,
    prepared: Mapping[str, Any],
    published: Mapping[str, Any],
    policy: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _reject_persisted_url(payload, field="controller-signed delivery envelope")
    envelope_value = _parse_canonical_json(payload, field="controller-signed delivery envelope")
    try:
        verified = provenance._controller_delivery_envelope(  # type: ignore[attr-defined]
            payload=payload,
            pinned_controller_public_key_base64=pinned_controller_public_key_base64,
            expected_campaign_id=published["campaign_id"],
            expected_application=prepared["application"],
            expected_tooling=prepared["tooling"],
            expected_canonical_release_tree_sha256=prepared["canonical_release_tree_sha256"],
        )
    except Exception as exc:
        raise SourceBootstrapReceiveRenderError("controller-signed delivery envelope is unsafe") from exc
    if (
        verified.get("package_id") != prepared["package_id"]
        or verified.get("fi_bootstrap_recipient") != policy.webapp_fi_age_recipient
        or verified.get("object") != published["object"]
    ):
        raise SourceBootstrapReceiveRenderError("controller-signed delivery envelope does not bind the generic receipt")
    return verified, envelope_value


def _build_remote_config(
    *,
    prepared: Mapping[str, Any],
    preparation_receipt: Mapping[str, Any],
    published: Mapping[str, Any],
    envelope: Mapping[str, Any],
    pinned_controller_public_key_base64: str,
    policy: Any,
) -> dict[str, Any]:
    return {
        "schema": RECEIVER_CONFIG_SCHEMA,
        "source_site": "bot_fi",
        "destination_site": "webapp_fi",
        "receiver_root": DEFAULT_RECEIVER_ROOT,
        "age_identity_file": webapp_fi_bootstrap_identity_file(published["campaign_id"]),
        "object_storage": {
            "endpoint": policy.endpoint,
            "region": policy.region,
            "bucket": policy.bucket,
            "prefix": policy.prefix,
        },
        "campaign_id": published["campaign_id"],
        "package_id": prepared["package_id"],
        "application": prepared["application"],
        "tooling": prepared["tooling"],
        "canonical_release_tree_sha256": prepared["canonical_release_tree_sha256"],
        "fi_bootstrap_recipient": policy.webapp_fi_age_recipient,
        "transport_receipt": published,
        "preparation_receipt": dict(preparation_receipt),
        "delivery_envelope": dict(envelope),
        "pinned_controller_public_key_base64": pinned_controller_public_key_base64,
    }


def _assert_control_only_remote_config(value: Mapping[str, Any]) -> None:
    """Refuse an SSH config that carries anything beyond bounded control facts.

    The concrete receipt/envelope verifiers already constrain their complete
    schemas.  This additional boundary check makes the command-channel rule
    explicit: it may contain static receiver pins, generic immutable-object
    facts, preparation facts, a public verification key, and the signed
    envelope.  It cannot contain credentials, binary payloads, arbitrary
    base64 blobs, or a durable presigned URL.
    """

    if set(value) != REMOTE_CONFIG_FIELDS:
        raise SourceBootstrapReceiveRenderError("remote receiver control configuration has unexpected fields")
    storage = value.get("object_storage")
    if not isinstance(storage, Mapping) or set(storage) != REMOTE_CONFIG_STORAGE_FIELDS:
        raise SourceBootstrapReceiveRenderError("remote receiver control configuration storage fields are unexpected")

    def visit(item: object, path: tuple[str, ...]) -> None:
        if isinstance(item, (bytes, bytearray, memoryview)):
            raise SourceBootstrapReceiveRenderError("remote receiver control configuration contains binary payload material")
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise SourceBootstrapReceiveRenderError("remote receiver control configuration has a non-text field")
                lowered_key = key.lower()
                if any(fragment in lowered_key for fragment in FORBIDDEN_CONTROL_KEY_FRAGMENTS):
                    raise SourceBootstrapReceiveRenderError("remote receiver control configuration contains credential or payload fields")
                child_path = (*path, key)
                if lowered_key.endswith("base64") and child_path not in ALLOWED_CONTROL_BASE64_PATHS:
                    raise SourceBootstrapReceiveRenderError("remote receiver control configuration contains an unexpected base64 field")
                visit(child, child_path)
            return
        if isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                visit(child, (*path, str(index)))
            return
        if isinstance(item, str):
            lowered = item.lower()
            if "presigned" in lowered or "x-amz-" in lowered or "age-secret-key-" in lowered:
                raise SourceBootstrapReceiveRenderError("remote receiver control configuration contains transient or secret material")
            if "://" in item and path != ("object_storage", "endpoint"):
                raise SourceBootstrapReceiveRenderError("remote receiver control configuration contains a durable URL")

    visit(value, ())
    encoded = canonical_json_bytes(value)
    if len(encoded) > MAX_CONTROL_FILE_BYTES:
        raise SourceBootstrapReceiveRenderError("remote receiver control configuration exceeds the fixed metadata bound")


# The receiver has no repository imports.  Its compact Ed25519 verifier checks
# the controller envelope before it ever extracts or executes the downloaded
# helper.  It uses only the Python standard library plus fixed curl/age/Python
# executables, which makes it suitable for the first FI bootstrap.
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
import tarfile
from urllib.parse import parse_qs, quote, urlparse

CONFIG_SCHEMA = "gold-trade-webapp-fi-source-bootstrap-receive-config-v2"
TRANSPORT_SCHEMA = "gold-trade-webapp-fi-source-transport-v1"
OBJECT_ENCRYPTION = "age-v1"
DELIVERY_ENVELOPE_SCHEMA = "gold-trade-webapp-fi-source-adoption-delivery-envelope-v1"
DELIVERY_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-adoption-delivery-v1"
INSTALL_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-adoption-install-receipt-v1"
INSTALL_RECEIPT_NAME = "source-adoption-install-receipt.json"
PACKAGE_ARCHIVE_NAME = "webapp-fi-source-adoption.tar"
FIXED_RECEIVER_ROOT = "/srv/trading-bot-three-site-staging-data/webapp-fi-source-bootstrap"
FIXED_CAMPAIGN_IDENTITY_ROOT = "/etc/trading-bot-three-site/campaigns"
FIXED_AGE_IDENTITY_SUFFIX = "webapp-fi/bootstrap.agekey"
CURL_BINARY = "/usr/bin/curl"
AGE_BINARY = "/usr/bin/age"
PYTHON_BINARY = "/usr/bin/python3"
INSTALLER_LAUNCHER = r"""import importlib.util
import sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("_webapp_fi_downloaded_source_adoption", sys.argv[1])
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load downloaded source-adoption installer")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
module.install_source_adoption(
    archive=Path(sys.argv[2]),
    preparation_receipt=Path(sys.argv[3]),
    delivery_receipt=Path(sys.argv[4]),
    delivery_envelope=Path(sys.argv[5]),
    pinned_controller_public_key_base64=sys.argv[6],
    expected_campaign_id=sys.argv[7],
    expected_fi_bootstrap_recipient=sys.argv[8],
    staging_root=Path(sys.argv[9]),
    expected_control_commit=sys.argv[10],
    expected_application_release_sha=sys.argv[11],
    apply=sys.argv[12] == "1",
)
"""
MAX_ARCHIVE_BYTES = 24 * 1024 * 1024
MAX_CIPHERTEXT_BYTES = MAX_ARCHIVE_BYTES + 1024 * 1024
MAX_INSTALL_RECEIPT_BYTES = 1024 * 1024
MINIMUM_RECEIVE_CAPACITY_RESERVE_BYTES = 128 * 1024 * 1024
MAX_URL_BYTES = 8192
MAX_PRESIGNED_LIFETIME_SECONDS = 300
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
ALEMBIC_RE = re.compile(r"^[0-9a-f]{12}$")
PACKAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
AGE_RECIPIENT_RE = re.compile(r"^age1[ac-hj-np-z02-9]{20,128}$")
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

def campaign_identity_file(campaign_id):
    if not isinstance(campaign_id, str) or not CAMPAIGN_ID_RE.fullmatch(campaign_id):
        raise ReceiveError("campaign bootstrap identity binding")
    path = PurePosixPath(FIXED_CAMPAIGN_IDENTITY_ROOT) / campaign_id / FIXED_AGE_IDENTITY_SUFFIX
    text = path.as_posix()
    if not path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ReceiveError("campaign bootstrap identity binding")
    return text

def reject_transient_control_url(value):
    encoded = canonical_json_bytes(value).lower()
    # ``object_storage.endpoint`` is a static endpoint binding, not a signed
    # control URL.  Reject every shape that could retain the transient GET URL.
    if b"presigned" in encoded or b'"url"' in encoded or b"x-amz-" in encoded:
        raise ReceiveError("persistent receiver state contains a transient URL")

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

def require_root_private_campaign_identity(value):
    path = require_root_private_file(value)
    root = Path(FIXED_CAMPAIGN_IDENTITY_ROOT)
    try:
        relative_parent = path.parent.relative_to(root)
    except ValueError as exc:
        raise ReceiveError("campaign bootstrap identity is outside the fixed root") from exc
    current = root
    for part in (None, *relative_parent.parts):
        if part is not None:
            current = current / part
        try:
            before = current.lstat()
            resolved = current.resolve(strict=True)
            after = resolved.lstat()
        except OSError as exc:
            raise ReceiveError("campaign bootstrap identity parent is unavailable") from exc
        if (
            resolved != current
            or stat.S_ISLNK(before.st_mode)
            or not stat.S_ISDIR(after.st_mode)
            or after.st_uid != 0
            or stat.S_IMODE(after.st_mode) & 0o077
        ):
            raise ReceiveError("campaign bootstrap identity parent is unsafe")
    return path

def require_root_private_directory(value):
    path = Path(require_absolute_path(value))
    # The receiver creates candidates below this fixed directory.  Checking the
    # leaf alone is insufficient: a writable non-sticky ancestor could replace
    # it after validation and redirect a root write.  Root-owned sticky
    # directories (for example /tmp) are safe for an existing root-owned
    # child, matching the controller-side code-loader rule.
    current = Path(path.anchor)
    parents = (current,)
    for component in path.parts[1:-1]:
        current = current / component
        parents += (current,)
    for parent in parents:
        try:
            before_parent = parent.lstat()
            resolved_parent = parent.resolve(strict=True)
            after_parent = resolved_parent.lstat()
        except OSError as exc:
            raise ReceiveError("fixed staging root parent is unavailable") from exc
        mode = stat.S_IMODE(after_parent.st_mode)
        if (
            resolved_parent != parent
            or stat.S_ISLNK(before_parent.st_mode)
            or not stat.S_ISDIR(after_parent.st_mode)
            or after_parent.st_uid != 0
            or (mode & 0o022 and not after_parent.st_mode & stat.S_ISVTX)
        ):
            raise ReceiveError("fixed staging root parent is unsafe")
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        after = resolved.lstat()
    except OSError as exc:
        raise ReceiveError("fixed staging root is unavailable") from exc
    if resolved != path or stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(after.st_mode):
        raise ReceiveError("fixed staging root is unsafe")
    if after.st_uid != 0 or stat.S_IMODE(after.st_mode) & 0o077:
        raise ReceiveError("fixed staging root is unsafe")
    return path

def require_receive_capacity(root, object_value):
    """Fail before network I/O unless the fixed staging filesystem is usable.

    The exact immutable descriptor accounts for the ciphertext and decrypted
    archive.  The reserve covers the installed helper candidate and normal
    filesystem overhead without inferring capacity from a maximum payload.
    """
    try:
        state = os.statvfs(str(root))
        available_blocks = state.f_bavail
        fragment_size = state.f_frsize
        flags = state.f_flag
    except (AttributeError, OSError) as exc:
        raise ReceiveError("cannot inspect source bootstrap staging capacity") from exc
    if (
        isinstance(available_blocks, bool)
        or not isinstance(available_blocks, int)
        or available_blocks < 0
        or isinstance(fragment_size, bool)
        or not isinstance(fragment_size, int)
        or fragment_size <= 0
        or isinstance(flags, bool)
        or not isinstance(flags, int)
        or flags < 0
    ):
        raise ReceiveError("source bootstrap staging capacity is invalid")
    if flags & getattr(os, "ST_RDONLY", 1):
        raise ReceiveError("source bootstrap staging filesystem is read-only")
    ciphertext_bytes = require_size(object_value.get("ciphertext_bytes"), MAX_CIPHERTEXT_BYTES)
    plaintext_bytes = require_size(object_value.get("plaintext_bytes"), MAX_ARCHIVE_BYTES)
    required_bytes = ciphertext_bytes + plaintext_bytes + MINIMUM_RECEIVE_CAPACITY_RESERVE_BYTES
    available_bytes = available_blocks * fragment_size
    if available_bytes < required_bytes:
        raise ReceiveError("insufficient free space for source bootstrap staging")

def require_trusted_executable(value):
    path = Path(value)
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        after = resolved.lstat()
    except OSError as exc:
        raise ReceiveError("required executable is unavailable") from exc
    # Distribution-managed fixed executable paths may be root-owned symlinks
    # (notably ``/usr/bin/python3``); the resolved executable is authoritative.
    if before.st_uid != 0 or after.st_uid != 0 or not stat.S_ISREG(after.st_mode):
        raise ReceiveError("required executable is unsafe")
    if stat.S_IMODE(after.st_mode) & 0o022 or not stat.S_IMODE(after.st_mode) & 0o111:
        raise ReceiveError("required executable is unsafe")
    return str(path)

def write_new_private_file(path, payload, mode=0o600):
    if path.exists() or path.is_symlink():
        raise ReceiveError("refusing to overwrite a receiver artifact")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, mode)
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise ReceiveError("cannot create a receiver artifact") from exc
    state = path.lstat()
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISREG(state.st_mode) or state.st_uid != 0 or stat.S_IMODE(state.st_mode) != mode:
        raise ReceiveError("receiver artifact mode is unsafe")

def write_new_private_json(path, value):
    reject_transient_control_url(value)
    write_new_private_file(path, canonical_json_bytes(value) + b"\n")

def validate_url(value, config):
    url = require_text(value, MAX_URL_BYTES)
    if any(character.isspace() for character in url):
        raise ReceiveError("invalid URL")
    parsed = urlparse(url)
    endpoint = urlparse(config["object_storage"]["endpoint"])
    try:
        has_port = parsed.port is not None
    except ValueError as exc:
        raise ReceiveError("invalid URL") from exc
    object_value = config["transport_receipt"]["object"]
    expected_path = "/" + quote(config["object_storage"]["bucket"], safe="") + "/" + quote(object_value["object_key"], safe="/")
    if parsed.scheme != "https" or parsed.hostname != endpoint.hostname or has_port or parsed.username or parsed.password or parsed.fragment or parsed.path != expected_path:
        raise ReceiveError("URL endpoint binding")
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise ReceiveError("invalid URL query") from exc
    if query.get("versionId") != [object_value["version_id"]]:
        raise ReceiveError("URL version binding")
    sigv4_names = ("X-Amz-Algorithm", "X-Amz-Credential", "X-Amz-Signature", "X-Amz-Date", "X-Amz-Expires", "X-Amz-SignedHeaders")
    sigv2_names = ("AWSAccessKeyId", "Signature", "Expires")
    sigv4 = all(len(query.get(name, [])) == 1 and bool(query[name][0]) for name in sigv4_names)
    sigv2 = all(len(query.get(name, [])) == 1 and bool(query[name][0]) for name in sigv2_names)
    if sigv4 == sigv2:
        raise ReceiveError("URL signature envelope")
    now = dt.datetime.now(dt.timezone.utc)
    if sigv4:
        if query["X-Amz-Algorithm"] != ["AWS4-HMAC-SHA256"] or query["X-Amz-SignedHeaders"] != ["host"]:
            raise ReceiveError("URL SigV4 algorithm or signed headers")
        try:
            issued = dt.datetime.strptime(query["X-Amz-Date"][0], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
            expires = int(query["X-Amz-Expires"][0], 10)
        except (TypeError, ValueError) as exc:
            raise ReceiveError("URL SigV4 expiry") from exc
        if not 1 <= expires <= MAX_PRESIGNED_LIFETIME_SECONDS or now < issued or now > issued + dt.timedelta(seconds=expires):
            raise ReceiveError("URL SigV4 expiry")
    else:
        expires_text = query["Expires"][0]
        if not re.fullmatch(r"[0-9]{1,16}", expires_text):
            raise ReceiveError("URL SigV2 expiry")
        expires = int(expires_text, 10)
        now_seconds = int(now.timestamp())
        if expires < now_seconds or expires - now_seconds > MAX_PRESIGNED_LIFETIME_SECONDS:
            raise ReceiveError("URL SigV2 expiry")
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

def validate_headers(raw, config):
    headers = parse_header_blocks(raw)
    object_value = config["transport_receipt"]["object"]
    expected = {
        "x-amz-version-id": object_value["version_id"],
        "x-amz-meta-transport-schema": TRANSPORT_SCHEMA,
        "x-amz-meta-encryption": OBJECT_ENCRYPTION,
        "x-amz-meta-ciphertext-sha256": object_value["ciphertext_sha256"],
        "x-amz-meta-recipient-mode": "single",
    }
    if any(name.startswith("x-amz-server-side-encryption") for name in headers):
        raise ReceiveError("provider-side encryption is disallowed")
    for name, expected_value in expected.items():
        if headers.get(name) != [expected_value]:
            raise ReceiveError("response metadata mismatch")
    content_length = headers.get("content-length")
    if content_length is None or len(content_length) != 1 or not re.fullmatch(r"[0-9]+", content_length[0]) or int(content_length[0]) != object_value["ciphertext_bytes"]:
        raise ReceiveError("response length mismatch")

# Minimal RFC 8032 Ed25519 verification implemented with Python integers.  This
# prevents executing a downloaded installer until the controller envelope has
# been verified against a separately pinned public key.
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
    return (((x1 * y2 + x2 * y1) * denominator_x) % Q, ((y1 * y2 + x1 * x2) * denominator_y) % Q)

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
        raise ReceiveError("controller envelope signature verification failed")

def require_application(value):
    if not isinstance(value, dict) or set(value) != {"release_sha", "expected_alembic_revision"}:
        raise ReceiveError("invalid application binding")
    if not isinstance(value["release_sha"], str) or not RELEASE_RE.fullmatch(value["release_sha"]):
        raise ReceiveError("invalid application release")
    if not isinstance(value["expected_alembic_revision"], str) or not ALEMBIC_RE.fullmatch(value["expected_alembic_revision"]):
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
        "plaintext_bytes": require_size(value["plaintext_bytes"], MAX_ARCHIVE_BYTES),
    }

def validate_storage(value):
    expected = {"endpoint", "region", "bucket", "prefix"}
    if not isinstance(value, dict) or set(value) != expected:
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
    if parsed.scheme != "https" or parsed.hostname != "s3." + region + ".arvanstorage.ir" or has_port or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ReceiveError("invalid Object Storage endpoint")
    if not BUCKET_RE.fullmatch(bucket) or prefix.strip("/") != prefix or not prefix or any(not PREFIX_COMPONENT_RE.fullmatch(part) for part in prefix.split("/")):
        raise ReceiveError("invalid Object Storage namespace")
    return {"endpoint": endpoint, "region": region, "bucket": bucket, "prefix": prefix}

def validate_transport_receipt(value, config):
    expected = {
        "schema", "status", "campaign_id", "release_sha", "control_commit", "control_tree", "source_site", "destination_site",
        "object_kind", "object_id", "recipient_mode", "recipients", "transport", "object", "receipt_sha256",
    }
    if not isinstance(value, dict) or set(value) != expected or value.get("schema") != TRANSPORT_SCHEMA or value.get("status") != "published":
        raise ReceiveError("generic source transport receipt is invalid")
    if value.get("receipt_sha256") != sha256_bytes(canonical_json_bytes({key: item for key, item in value.items() if key != "receipt_sha256"})):
        raise ReceiveError("generic source transport receipt checksum")
    if value.get("campaign_id") != config["campaign_id"] or value.get("release_sha") != config["application"]["release_sha"] or value.get("control_commit") != config["tooling"]["control_commit"] or value.get("control_tree") != config["tooling"]["control_tree"]:
        raise ReceiveError("generic source transport release binding")
    if value.get("source_site") != "bot_fi" or value.get("destination_site") != "webapp_fi" or value.get("object_kind") != "bootstrap_package" or value.get("object_id") != config["package_id"] or value.get("recipient_mode") != "single" or value.get("recipients") != [config["fi_bootstrap_recipient"]]:
        raise ReceiveError("generic source transport route binding")
    if value.get("transport") != {"encryption": OBJECT_ENCRYPTION, "create_only": True, "private_bucket": True, "provider_side_sse": False, "read_back_same_version_id": True}:
        raise ReceiveError("generic source transport policy")
    object_value = require_object(value.get("object"))
    expected_key = "/".join((
        config["object_storage"]["prefix"], "webapp-fi-source-transport", "v1", config["campaign_id"],
        config["application"]["release_sha"], config["tooling"]["control_commit"], config["tooling"]["control_tree"],
        "bot_fi", "webapp_fi", "bootstrap_package", config["package_id"] + ".age",
    ))
    if object_value["object_key"] != expected_key:
        raise ReceiveError("generic source transport object key")
    return object_value

def validate_preparation_receipt(value, config, object_value):
    expected = {"schema", "status", "package_id", "package_directory", "source_site", "destination_site", "application", "tooling", "archive", "package_manifest", "receipt_sha256"}
    if not isinstance(value, dict) or set(value) != expected or value.get("schema") != "gold-trade-webapp-fi-source-adoption-preparation-v1" or value.get("status") != "prepared":
        raise ReceiveError("preparation receipt is invalid")
    if value.get("receipt_sha256") != sha256_bytes(canonical_json_bytes({key: item for key, item in value.items() if key != "receipt_sha256"})):
        raise ReceiveError("preparation receipt checksum")
    if value.get("package_id") != config["package_id"] or value.get("source_site") != "bot_fi" or value.get("destination_site") != "webapp_fi" or value.get("application") != config["application"] or value.get("tooling") != config["tooling"]:
        raise ReceiveError("preparation receipt binding")
    archive = value.get("archive")
    manifest = value.get("package_manifest")
    if not isinstance(archive, dict) or set(archive) != {"name", "sha256", "bytes"} or archive.get("name") != PACKAGE_ARCHIVE_NAME or not isinstance(manifest, dict) or set(manifest) != {"name", "sha256"} or manifest.get("name") != "source-adoption-package.json":
        raise ReceiveError("preparation receipt archive")
    if archive.get("sha256") != object_value["plaintext_sha256"] or archive.get("bytes") != object_value["plaintext_bytes"] or require_sha256(manifest.get("sha256")) is None:
        raise ReceiveError("preparation receipt archive binding")
    require_absolute_path(value.get("package_directory"))

def validate_envelope(value, config, object_value):
    expected = {
        "schema", "status", "campaign_id", "source_site", "destination_site", "package_id", "application", "tooling",
        "canonical_release_tree_sha256", "fi_bootstrap_recipient", "object", "controller_public_key_base64", "controller_signature",
    }
    if not isinstance(value, dict) or set(value) != expected or value.get("schema") != DELIVERY_ENVELOPE_SCHEMA or value.get("status") != "issued":
        raise ReceiveError("controller envelope is invalid")
    if value.get("campaign_id") != config["campaign_id"] or value.get("source_site") != "bot_fi" or value.get("destination_site") != "webapp_fi" or value.get("package_id") != config["package_id"] or value.get("application") != config["application"] or value.get("tooling") != config["tooling"] or value.get("canonical_release_tree_sha256") != config["canonical_release_tree_sha256"] or value.get("fi_bootstrap_recipient") != config["fi_bootstrap_recipient"]:
        raise ReceiveError("controller envelope binding")
    if require_object(value.get("object")) != object_value or value.get("controller_public_key_base64") != config["pinned_controller_public_key_base64"]:
        raise ReceiveError("controller envelope object/key binding")
    signature = value.get("controller_signature")
    if not isinstance(signature, dict) or set(signature) != {"algorithm", "signature_base64"} or signature.get("algorithm") != "ed25519":
        raise ReceiveError("controller envelope signature")
    verify_ed25519(
        config["pinned_controller_public_key_base64"], signature.get("signature_base64"),
        b"gold-trade-webapp-fi-source-adoption-delivery-envelope-v1\x00" + canonical_json_bytes({key: item for key, item in value.items() if key != "controller_signature"}),
    )

def build_delivery_receipt(config, object_value):
    unsigned = {
        "schema": DELIVERY_RECEIPT_SCHEMA,
        "status": "received",
        "source_site": "bot_fi",
        "destination_site": "webapp_fi",
        "control_commit": config["tooling"]["control_commit"],
        "package_id": config["package_id"],
        "object": object_value,
        "archive": {"sha256": object_value["plaintext_sha256"], "bytes": object_value["plaintext_bytes"]},
    }
    return {**unsigned, "receipt_sha256": sha256_bytes(canonical_json_bytes(unsigned))}

def load_config(encoded):
    try:
        raw = base64.b64decode(encoded, validate=True)
        config = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object, parse_constant=reject_constant)
    except Exception as exc:
        raise ReceiveError("receiver configuration is invalid") from exc
    expected = {
        "schema", "source_site", "destination_site", "receiver_root", "age_identity_file", "object_storage", "campaign_id", "package_id",
        "application", "tooling", "canonical_release_tree_sha256", "fi_bootstrap_recipient", "transport_receipt", "preparation_receipt",
        "delivery_envelope", "pinned_controller_public_key_base64",
    }
    if not isinstance(config, dict) or raw != canonical_json_bytes(config) or set(config) != expected or config.get("schema") != CONFIG_SCHEMA:
        raise ReceiveError("receiver configuration is invalid")
    reject_transient_control_url(config)
    if config.get("source_site") != "bot_fi" or config.get("destination_site") != "webapp_fi" or config.get("receiver_root") != FIXED_RECEIVER_ROOT:
        raise ReceiveError("receiver configuration pinning")
    if not isinstance(config.get("campaign_id"), str) or not CAMPAIGN_ID_RE.fullmatch(config["campaign_id"]):
        raise ReceiveError("campaign binding")
    if config.get("age_identity_file") != campaign_identity_file(config["campaign_id"]):
        raise ReceiveError("campaign bootstrap identity binding")
    if not isinstance(config.get("package_id"), str) or not PACKAGE_ID_RE.fullmatch(config["package_id"]):
        raise ReceiveError("package binding")
    config["application"] = require_application(config.get("application"))
    config["tooling"] = require_tooling(config.get("tooling"))
    config["canonical_release_tree_sha256"] = require_sha256(config.get("canonical_release_tree_sha256"))
    if not isinstance(config.get("fi_bootstrap_recipient"), str) or not AGE_RECIPIENT_RE.fullmatch(config["fi_bootstrap_recipient"]):
        raise ReceiveError("recipient binding")
    config["pinned_controller_public_key_base64"] = require_text(config.get("pinned_controller_public_key_base64"), 128)
    config["object_storage"] = validate_storage(config.get("object_storage"))
    object_value = validate_transport_receipt(config.get("transport_receipt"), config)
    validate_preparation_receipt(config.get("preparation_receipt"), config, object_value)
    validate_envelope(config.get("delivery_envelope"), config, object_value)
    return config

def extract_installer(archive_path, destination):
    target_name = "scripts/install_webapp_fi_source_adoption.py"
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            members = archive.getmembers()
            target = next((member for member in members if member.name == target_name), None)
            if target is None or not target.isreg() or target.issym() or target.islnk() or target.linkname or target.size < 1 or target.size > 8 * 1024 * 1024:
                raise ReceiveError("downloaded package installer is unsafe")
            handle = archive.extractfile(target)
            if handle is None:
                raise ReceiveError("downloaded package installer is unreadable")
            payload = handle.read(target.size + 1)
            if len(payload) != target.size:
                raise ReceiveError("downloaded package installer changed")
    except (OSError, tarfile.TarError) as exc:
        raise ReceiveError("downloaded package cannot be inspected") from exc
    write_new_private_file(destination, payload, mode=0o700)

def run_installer(config, candidate, archive, installer, apply):
    control = candidate / "control"
    preparation = control / "preparation.json"
    delivery = control / "delivery.json"
    envelope = control / "delivery-envelope.json"
    command = [
        PYTHON_BINARY, "-I", "-B", "-c", INSTALLER_LAUNCHER, str(installer), str(archive), str(preparation), str(delivery), str(envelope),
        config["pinned_controller_public_key_base64"], config["campaign_id"], config["fi_bootstrap_recipient"],
        config["receiver_root"], config["tooling"]["control_commit"], config["application"]["release_sha"], "1" if apply else "0",
    ]
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReceiveError("installer could not start") from exc
    if result.returncode != 0:
        raise ReceiveError("installer verification or install failed")

def load_nonsecret_install_receipt(config, candidate):
    """Return the exact URL-free installation receipt permitted over SSH.

    The actual installer has already performed complete package verification.
    This narrow second check prevents its control result from becoming a
    generic file-exfiltration channel: only the fixed, public receipt schema
    may leave WebApp-FI on stdout.
    """
    receipt_path = candidate / INSTALL_RECEIPT_NAME
    require_root_private_file(str(receipt_path))
    try:
        payload = receipt_path.read_bytes()
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=strict_object, parse_constant=reject_constant)
    except Exception as exc:
        raise ReceiveError("installed source-adoption receipt is unreadable") from exc
    if not 1 <= len(payload) <= MAX_INSTALL_RECEIPT_BYTES or not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise ReceiveError("installed source-adoption receipt is invalid")
    reject_transient_control_url(value)
    expected = {
        "schema", "status", "installed_at", "candidate_directory", "source_site", "destination_site", "campaign_id", "package_id",
        "application", "tooling", "files", "canonical_release_tree_sha256", "package", "receipt_sha256",
    }
    if set(value) != expected or value.get("schema") != INSTALL_RECEIPT_SCHEMA or value.get("status") != "installed":
        raise ReceiveError("installed source-adoption receipt is unsupported")
    if value.get("candidate_directory") != str(candidate) or value.get("campaign_id") != config["campaign_id"] or value.get("package_id") != config["package_id"]:
        raise ReceiveError("installed source-adoption receipt binding")
    if value.get("source_site") != config["source_site"] or value.get("destination_site") != config["destination_site"]:
        raise ReceiveError("installed source-adoption receipt site binding")
    if require_application(value.get("application")) != config["application"] or require_tooling(value.get("tooling")) != config["tooling"]:
        raise ReceiveError("installed source-adoption receipt release binding")
    if require_sha256(value.get("canonical_release_tree_sha256")) != config["canonical_release_tree_sha256"]:
        raise ReceiveError("installed source-adoption receipt descriptor binding")
    receipt_sha256 = require_sha256(value.get("receipt_sha256"))
    if sha256_bytes(canonical_json_bytes({key: item for key, item in value.items() if key != "receipt_sha256"})) != receipt_sha256:
        raise ReceiveError("installed source-adoption receipt checksum")
    files = value.get("files")
    if not isinstance(files, dict) or not files or any(not isinstance(name, str) or not name or require_sha256(digest) != digest for name, digest in files.items()):
        raise ReceiveError("installed source-adoption receipt files")
    package = value.get("package")
    package_fields = {
        "archive_sha256", "archive_bytes", "preparation_receipt_sha256", "delivery_receipt_sha256", "delivery_envelope_sha256",
        "controller_public_key_base64", "fi_bootstrap_recipient", "object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes",
    }
    if not isinstance(package, dict) or set(package) != package_fields:
        raise ReceiveError("installed source-adoption receipt package")
    for field in ("archive_sha256", "preparation_receipt_sha256", "delivery_receipt_sha256", "delivery_envelope_sha256", "ciphertext_sha256"):
        require_sha256(package.get(field))
    if package.get("archive_bytes") != config["transport_receipt"]["object"]["plaintext_bytes"] or package.get("ciphertext_bytes") != config["transport_receipt"]["object"]["ciphertext_bytes"]:
        raise ReceiveError("installed source-adoption receipt archive binding")
    if package.get("object_key") != config["transport_receipt"]["object"]["object_key"] or package.get("version_id") != config["transport_receipt"]["object"]["version_id"]:
        raise ReceiveError("installed source-adoption receipt object binding")
    if package.get("fi_bootstrap_recipient") != config["fi_bootstrap_recipient"] or package.get("controller_public_key_base64") != config["pinned_controller_public_key_base64"]:
        raise ReceiveError("installed source-adoption receipt key binding")
    if not isinstance(value.get("installed_at"), str) or not value["installed_at"].endswith("Z"):
        raise ReceiveError("installed source-adoption receipt timestamp")
    return value

def receive(config, url):
    url = validate_url(url, config)
    for executable in (CURL_BINARY, AGE_BINARY, PYTHON_BINARY):
        require_trusted_executable(executable)
    identity = require_root_private_campaign_identity(config["age_identity_file"])
    root = require_root_private_directory(config["receiver_root"])
    object_value = config["transport_receipt"]["object"]
    # This happens before candidate creation, curl, or age.  A failure leaves
    # no partial receiver evidence that could be mistaken for a delivery.
    require_receive_capacity(root, object_value)
    version_tag = sha256_bytes(object_value["version_id"].encode("ascii"))[:16]
    candidate = root / ("receive-" + config["tooling"]["control_commit"] + "-" + config["package_id"] + "-" + version_tag)
    if candidate.parent != root or candidate.exists() or candidate.is_symlink():
        raise ReceiveError("receive candidate already exists")
    os.umask(0o077)
    try:
        os.mkdir(candidate, 0o700)
        state = candidate.lstat()
        if not stat.S_ISDIR(state.st_mode) or state.st_uid != 0 or stat.S_IMODE(state.st_mode) != 0o700:
            raise ReceiveError("receive candidate creation")
        ciphertext = candidate / ".ciphertext.age"
        archive = candidate / ".plaintext.tar"
        result = subprocess.run(
            [CURL_BINARY, "--disable", "--silent", "--show-error", "--fail", "--globoff", "--noproxy", "*", "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "0", "--connect-timeout", "20", "--max-time", "120", "--dump-header", "-", "--output", str(ciphertext), "--", url],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        )
        if result.returncode != 0:
            raise ReceiveError("direct Object Storage download failed")
        validate_headers(result.stdout, config)
        if sha256_file(ciphertext, MAX_CIPHERTEXT_BYTES) != (object_value["ciphertext_sha256"], object_value["ciphertext_bytes"]):
            raise ReceiveError("ciphertext binding")
        result = subprocess.run(
            [AGE_BINARY, "--decrypt", "--identity", str(identity), "--output", str(archive), str(ciphertext)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if result.returncode != 0:
            raise ReceiveError("age decryption failed")
        if sha256_file(archive, MAX_ARCHIVE_BYTES) != (object_value["plaintext_sha256"], object_value["plaintext_bytes"]):
            raise ReceiveError("plaintext binding")
        control = candidate / "control"
        os.mkdir(control, 0o700)
        write_new_private_json(control / "preparation.json", config["preparation_receipt"])
        write_new_private_json(control / "delivery.json", build_delivery_receipt(config, object_value))
        write_new_private_json(control / "delivery-envelope.json", config["delivery_envelope"])
        installer = candidate / "install_webapp_fi_source_adoption.py"
        extract_installer(archive, installer)
        # The no-write call repeats signature/package validation before any candidate install.
        run_installer(config, candidate, archive, installer, apply=False)
        run_installer(config, candidate, archive, installer, apply=True)
        installed_candidate = root / ("installed-" + config["tooling"]["control_commit"] + "-" + config["package_id"])
        install_receipt = load_nonsecret_install_receipt(config, installed_candidate)
        print(json.dumps({"status": "installed", "install_receipt": install_receipt}, sort_keys=True))
    except Exception:
        # A failed fresh candidate is root-only evidence.  It is never retried or
        # deleted automatically, and no URL has been written into it.
        raise

def main():
    try:
        if os.geteuid() != 0:
            raise ReceiveError("receiver must run as root")
        if len(sys.argv) != 5 or sys.argv[3] != "--":
            raise ReceiveError("invalid receive arguments")
        config = load_config(sys.argv[2])
        receive(config, sys.argv[4])
    except Exception:
        print(json.dumps({"status": "blocked", "error": "source bootstrap receive verification failed"}, sort_keys=True))
        return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
'''

REMOTE_LAUNCHER = (
    "import base64,sys;exec(compile(base64.b64decode(sys.argv[1]),'<webapp-fi-source-bootstrap-receive>','exec'))"
)


def render_receive_command(
    *,
    transport_publish_receipt: Path,
    source_transport_config: Path,
    source_adoption_package_directory: Path,
    preparation_receipt: Path,
    delivery_envelope: Path,
    pinned_controller_public_key_base64: str,
    fi_known_hosts: Path,
    presigned_url: str,
    receiver_root: str = DEFAULT_RECEIVER_ROOT,
) -> str:
    """Return one transient SSH control command after complete local checks."""

    _require_fixed_receiver_root(receiver_root)
    controller_config = _load_transport_config(source_transport_config)
    policy = controller_config.policy
    prepared, preparation_value, _preparation_raw = _verify_prepared_package(
        package_directory=source_adoption_package_directory,
        preparation_receipt=preparation_receipt,
    )
    publish_raw = _read_root_only_file(transport_publish_receipt, field="generic source transport publish receipt")
    published = _verify_generic_transport_receipt(payload=publish_raw, policy=policy, prepared=prepared)
    try:
        controller_config = transport.require_controller_config_for_campaign(
            controller_config=controller_config,
            campaign_id=published["campaign_id"],
        )
    except Exception as exc:
        raise SourceBootstrapReceiveRenderError(
            "source transport controller configuration does not bind the published campaign"
        ) from exc
    policy = controller_config.policy
    envelope_raw = _read_root_only_file(delivery_envelope, field="controller-signed delivery envelope")
    _envelope_verified, envelope_value = _verify_delivery_envelope(
        payload=envelope_raw,
        pinned_controller_public_key_base64=_require_text(
            pinned_controller_public_key_base64,
            field="pinned controller public key",
            maximum=128,
        ),
        prepared=prepared,
        published=published,
        policy=policy,
    )
    url = _validate_presigned_url(presigned_url, policy=policy, object_value=published["object"])
    remote_config = _build_remote_config(
        prepared=prepared,
        preparation_receipt=preparation_value,
        published=published,
        envelope=envelope_value,
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
        policy=policy,
    )
    _assert_control_only_remote_config(remote_config)
    program_b64 = base64.b64encode(REMOTE_RECEIVER_SOURCE.encode("utf-8")).decode("ascii")
    config_b64 = base64.b64encode(canonical_json_bytes(remote_config)).decode("ascii")
    try:
        if initial.REMOTE_HOST != REMOTE_HOST:
            raise SourceBootstrapReceiveRenderError("pinned FI SSH target differs between bootstrap controls")
        return initial._render_pinned_ssh(
            known_hosts=Path(fi_known_hosts),
            remote_arguments=[
                "/usr/bin/python3",
                "-I",
                "-B",
                "-c",
                REMOTE_LAUNCHER,
                program_b64,
                config_b64,
                "--",
                url,
            ],
        )
    except SourceBootstrapReceiveRenderError:
        raise
    except Exception as exc:
        raise SourceBootstrapReceiveRenderError("pinned FI SSH bootstrap control cannot be rendered") from exc


def _read_presigned_url_stdin() -> str:
    try:
        payload = sys.stdin.buffer.read(MAX_URL_BYTES + 1)
    except OSError as exc:
        raise SourceBootstrapReceiveRenderError("cannot read source bootstrap presigned URL from stdin") from exc
    if not payload or len(payload) > MAX_URL_BYTES:
        raise SourceBootstrapReceiveRenderError("source bootstrap presigned URL stdin exceeds the fixed size bound")
    try:
        return payload.decode("utf-8").rstrip("\n")
    except UnicodeDecodeError as exc:
        raise SourceBootstrapReceiveRenderError("source bootstrap presigned URL stdin is not UTF-8") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport-publish-receipt", required=True, type=Path)
    parser.add_argument("--source-transport-config", required=True, type=Path)
    parser.add_argument("--source-adoption-package-directory", required=True, type=Path)
    parser.add_argument("--preparation-receipt", required=True, type=Path)
    parser.add_argument("--delivery-envelope", required=True, type=Path)
    parser.add_argument("--pinned-controller-public-key-base64", required=True)
    parser.add_argument("--fi-known-hosts", required=True, type=Path)
    parser.add_argument("--presigned-url-stdin", action="store_true", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        _reject_direct_url_render()
    except SourceBootstrapReceiveRenderError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
