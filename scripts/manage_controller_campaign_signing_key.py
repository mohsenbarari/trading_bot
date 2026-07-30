#!/usr/bin/env python3
"""Enroll one fresh controller-only Ed25519 signing key for one campaign.

The source standby path needs a controller signer, but a key from an earlier
campaign must never silently become authority for a new campaign.  This
helper owns the one permitted key layout::

    /etc/trading-bot-three-site/campaigns/<campaign>/controller/
        campaign-signing-ed25519/private.raw
        campaign-signing-ed25519/receipt.json

``enroll`` is a dry plan unless ``--apply`` is supplied.  Apply creates one
new 32-byte raw Ed25519 private key and one URL-free public receipt with
``O_EXCL``.  Existing key-directory paths are retained as evidence and fail
closed; this command neither reuses nor overwrites them.  The private key
never appears in stdout, the receipt, or any caller-selectable location.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Callable, Mapping, Sequence


CAMPAIGNS_ROOT = Path("/etc/trading-bot-three-site/campaigns")
CONTROLLER_DIRECTORY_NAME = "controller"
SIGNING_DIRECTORY_NAME = "campaign-signing-ed25519"
PRIVATE_KEY_FILENAME = "private.raw"
RECEIPT_FILENAME = "receipt.json"
RECEIPT_SCHEMA = "gold-trade-controller-campaign-signing-key-receipt-v1"
MAX_RECEIPT_BYTES = 16 * 1024
ED25519_PRIVATE_KEY_BYTES = 32
ED25519_PUBLIC_KEY_BYTES = 32
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ControllerCampaignSigningKeyError(RuntimeError):
    """A campaign-scoped controller signing key cannot be safely proven."""


@dataclasses.dataclass(frozen=True)
class SigningKeyLayout:
    """All non-negotiable controller signing-key paths for one campaign."""

    campaign_id: str
    campaign_binding_sha256: str
    campaign_directory: Path
    controller_directory: Path
    signing_directory: Path
    private_key_path: Path
    receipt_path: Path


@dataclasses.dataclass(frozen=True)
class VerifiedSigningKey:
    """The public facts derived from one fixed private controller key."""

    layout: SigningKeyLayout
    public_key_base64: str
    key_id: str
    receipt_sha256: str
    receipt: Mapping[str, str]


@dataclasses.dataclass(frozen=True)
class VerifiedCampaignSigner:
    """One usable signer proved against the fixed canonical campaign binding.

    The raw private bytes are intentionally not retained in this value.  A
    caller receives only the cryptography signer object, the public receipt,
    and the immutable campaign binding that selected it.
    """

    signer: Any
    signing_key: VerifiedSigningKey
    campaign_binding: Any


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Serialize persisted public control records in exactly one form."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ControllerCampaignSigningKeyError("controller signing-key receipt contains duplicate JSON keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ControllerCampaignSigningKeyError("controller signing-key receipt contains an unsupported JSON constant")


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise ControllerCampaignSigningKeyError("controller campaign signing-key operations must run as root")


def _require_root_controlled_directory_chain(path: Path, *, field: str) -> None:
    """Require an immutable lookup path before importing a sibling helper."""

    if not path.is_absolute():
        raise RuntimeError(f"{field} parent must be absolute")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        try:
            metadata = current.lstat()
        except OSError as exc:  # pragma: no cover - deployment layout invariant.
            raise RuntimeError(f"cannot inspect {field} parent") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or (mode & 0o022 and not metadata.st_mode & stat.S_ISVTX)
        ):
            raise RuntimeError(f"{field} parent is not root-controlled")


def _require_root_controlled_code_file(path: Path, *, field: str) -> Path:
    """Return one exact root-owned, non-writable sibling source file."""

    if not path.is_absolute():
        raise RuntimeError(f"{field} must be absolute")
    _require_root_controlled_directory_chain(path.parent, field=field)
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        after = resolved.lstat()
    except OSError as exc:  # pragma: no cover - deployment layout invariant.
        raise RuntimeError(f"cannot inspect {field}") from exc
    unsafe_bits = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or stat.S_ISLNK(after.st_mode)
        or not stat.S_ISREG(after.st_mode)
        or after.st_uid != 0
        or after.st_nlink != 1
        or stat.S_IMODE(after.st_mode) & 0o022
        or after.st_mode & unsafe_bits
    ):
        raise RuntimeError(f"{field} is not a root-owned non-writable regular non-symlink file")
    return path


def _load_exact_sibling(filename: str, module_name: str) -> Any:
    """Load one named root-controlled sibling without consulting ``sys.path``."""

    if (
        not isinstance(filename, str)
        or not filename
        or Path(filename).name != filename
        or filename in {".", ".."}
    ):
        raise RuntimeError("required sibling filename is not a safe leaf name")
    source = _require_root_controlled_code_file(
        Path(__file__), field="controller campaign signing-key source"
    )
    path = _require_root_controlled_code_file(
        source.with_name(filename), field=f"required sibling {filename}"
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError(f"cannot load required sibling {filename}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        loaded_path = getattr(module, "__file__", None)
        if not isinstance(loaded_path, str) or Path(loaded_path).resolve(strict=True) != path:
            raise RuntimeError(f"required sibling {filename} did not load from its exact path")
    except BaseException:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
        raise
    return module


binding = _load_exact_sibling(
    "webapp_fi_source_campaign_binding.py",
    "_controller_campaign_signing_key_binding",
)


def _raise_binding_error(action: Callable[[], Any], *, message: str) -> Any:
    try:
        return action()
    except binding.CampaignBindingError as exc:
        raise ControllerCampaignSigningKeyError(message) from exc


def _require_private_directory(path: Path, *, field: str) -> Path:
    return _raise_binding_error(
        lambda: binding._require_root_private_directory(path, field=field),
        message=f"{field} is unsafe",
    )


def _same_file_metadata(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_mode == right.st_mode
        and left.st_uid == right.st_uid
        and left.st_nlink == right.st_nlink
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
    )


def _safe_private_file(metadata: os.stat_result, *, maximum_bytes: int) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == 0
        and metadata.st_nlink == 1
        and not (metadata.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX))
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and 1 <= metadata.st_size <= maximum_bytes
    )


def _read_private_file(path: Path, *, field: str, maximum_bytes: int) -> bytes:
    """Read one exact root-only regular file without following its path."""

    source = Path(path)
    _require_private_directory(source.parent, field=field + " parent")
    try:
        before = source.lstat()
    except OSError as exc:
        raise ControllerCampaignSigningKeyError(f"cannot inspect {field}") from exc
    if not _safe_private_file(before, maximum_bytes=maximum_bytes):
        raise ControllerCampaignSigningKeyError(f"{field} is unsafe")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:  # pragma: no cover - Linux deployment invariant.
        raise ControllerCampaignSigningKeyError("secure no-follow file access is unavailable")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | no_follow
    try:
        descriptor = os.open(str(source), flags)
    except OSError as exc:
        raise ControllerCampaignSigningKeyError(f"cannot securely open {field}") from exc
    try:
        opened = os.fstat(descriptor)
        if not _same_file_metadata(before, opened) or not _safe_private_file(
            opened, maximum_bytes=maximum_bytes
        ):
            raise ControllerCampaignSigningKeyError(f"{field} changed while opening")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise ControllerCampaignSigningKeyError(f"{field} is too large")
            chunks.append(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        if len(payload) != opened.st_size or not _same_file_metadata(opened, after):
            raise ControllerCampaignSigningKeyError(f"{field} changed while reading")
        return payload
    except OSError as exc:
        raise ControllerCampaignSigningKeyError(f"cannot read {field}") from exc
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path, *, field: str) -> None:
    directory = _require_private_directory(path, field=field)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(str(directory), flags)
    except OSError as exc:
        raise ControllerCampaignSigningKeyError(f"cannot open {field}") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) != 0o700
        ):
            raise ControllerCampaignSigningKeyError(f"{field} changed while being opened")
        os.fsync(descriptor)
    except ControllerCampaignSigningKeyError:
        raise
    except OSError as exc:
        raise ControllerCampaignSigningKeyError(f"cannot durably sync {field}") from exc
    finally:
        os.close(descriptor)


def _create_or_require_private_child(parent: Path, name: str, *, field: str) -> Path:
    parent = _require_private_directory(parent, field=field + " parent")
    if not isinstance(name, str) or not name or Path(name).name != name or name in {".", ".."}:
        raise ControllerCampaignSigningKeyError(f"{field} name is invalid")
    child = parent / name
    try:
        os.mkdir(child, 0o700)
        os.chmod(child, 0o700)
        _fsync_directory(parent, field=field + " parent")
    except FileExistsError:
        pass
    except OSError as exc:
        raise ControllerCampaignSigningKeyError(f"cannot create {field}") from exc
    return _require_private_directory(child, field=field)


def _create_new_private_child(parent: Path, name: str, *, field: str) -> Path:
    parent = _require_private_directory(parent, field=field + " parent")
    if not isinstance(name, str) or not name or Path(name).name != name or name in {".", ".."}:
        raise ControllerCampaignSigningKeyError(f"{field} name is invalid")
    child = parent / name
    try:
        child.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ControllerCampaignSigningKeyError(f"cannot inspect {field}") from exc
    else:
        raise ControllerCampaignSigningKeyError(f"{field} already exists and will not be reused")
    try:
        os.mkdir(child, 0o700)
        os.chmod(child, 0o700)
        _fsync_directory(parent, field=field + " parent")
    except OSError as exc:
        raise ControllerCampaignSigningKeyError(f"cannot create {field}") from exc
    return _require_private_directory(child, field=field)


def _require_absent(path: Path, *, field: str) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ControllerCampaignSigningKeyError(f"cannot inspect {field}") from exc
    raise ControllerCampaignSigningKeyError(f"{field} already exists and will not be reused")


def _require_if_present_private_directory(path: Path, *, field: str) -> None:
    """Validate an existing fixed directory without creating it during a plan."""

    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ControllerCampaignSigningKeyError(f"cannot inspect {field}") from exc
    _require_private_directory(path, field=field)


def _write_new_private_file(path: Path, payload: bytes, *, field: str, maximum_bytes: int) -> None:
    """Durably create one bounded root-only file without replacement."""

    if not isinstance(payload, bytes) or not 1 <= len(payload) <= maximum_bytes:
        raise ControllerCampaignSigningKeyError(f"{field} payload is invalid")
    _require_private_directory(path.parent, field=field + " parent")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:  # pragma: no cover - Linux deployment invariant.
        raise ControllerCampaignSigningKeyError("secure no-follow file creation is unavailable")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | no_follow
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise ControllerCampaignSigningKeyError(f"{field} already exists and will not be reused") from exc
    except OSError as exc:
        raise ControllerCampaignSigningKeyError(f"cannot create {field}") from exc
    try:
        os.fchmod(descriptor, 0o600)
        pending = memoryview(payload)
        while pending:
            written = os.write(descriptor, pending)
            if written <= 0:  # pragma: no cover - regular-file writes do not normally return zero.
                raise OSError("short write")
            pending = pending[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if not _safe_private_file(metadata, maximum_bytes=maximum_bytes) or metadata.st_size != len(payload):
            raise ControllerCampaignSigningKeyError(f"new {field} is unsafe")
    except ControllerCampaignSigningKeyError:
        raise
    except OSError as exc:
        raise ControllerCampaignSigningKeyError(f"cannot durably create {field}") from exc
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent, field=field + " parent")


def _require_exact_campaign_binding_path(path: Path) -> tuple[Any, Path]:
    campaign = _raise_binding_error(
        lambda: binding.load_campaign_binding(Path(path)),
        message="canonical campaign binding is invalid",
    )
    expected = (
        CAMPAIGNS_ROOT
        / campaign.campaign_id
        / binding.SOURCE_PHASE_DIRECTORY
        / binding.CAMPAIGN_BINDING_FILENAME
    )
    if Path(path) != expected:
        raise ControllerCampaignSigningKeyError("campaign binding is not installed at its fixed campaign path")
    return campaign, expected.parent.parent


def signing_key_layout_for_campaign_binding(campaign_binding_path: Path) -> SigningKeyLayout:
    """Derive the only signing-key layout from the canonical campaign binding."""

    campaign, campaign_directory = _require_exact_campaign_binding_path(Path(campaign_binding_path))
    controller_directory = campaign_directory / CONTROLLER_DIRECTORY_NAME
    signing_directory = controller_directory / SIGNING_DIRECTORY_NAME
    return SigningKeyLayout(
        campaign_id=campaign.campaign_id,
        campaign_binding_sha256=campaign.binding_sha256,
        campaign_directory=campaign_directory,
        controller_directory=controller_directory,
        signing_directory=signing_directory,
        private_key_path=signing_directory / PRIVATE_KEY_FILENAME,
        receipt_path=signing_directory / RECEIPT_FILENAME,
    )


def _generate_ed25519_private_key() -> tuple[bytes, str]:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise ControllerCampaignSigningKeyError("cryptography Ed25519 support is unavailable") from exc
    private = Ed25519PrivateKey.generate()
    raw = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    if len(raw) != ED25519_PRIVATE_KEY_BYTES or len(public) != ED25519_PUBLIC_KEY_BYTES:
        raise ControllerCampaignSigningKeyError("generated controller signing key has an unsafe length")
    return raw, base64.b64encode(public).decode("ascii")


def _decode_public_key(value: object, *, field: str) -> bytes:
    if not isinstance(value, str):
        raise ControllerCampaignSigningKeyError(f"{field} is invalid")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise ControllerCampaignSigningKeyError(f"{field} is invalid") from exc
    if len(raw) != ED25519_PUBLIC_KEY_BYTES or value != base64.b64encode(raw).decode("ascii"):
        raise ControllerCampaignSigningKeyError(f"{field} is invalid")
    return raw


def key_id_for_public_key(public_key_base64: str) -> str:
    return "ed25519-sha256:" + sha256_bytes(
        _decode_public_key(public_key_base64, field="controller signing public key")
    )


def _public_key_from_private_key(path: Path) -> str:
    raw = _read_private_file(
        path,
        field="controller campaign signing private key",
        maximum_bytes=ED25519_PRIVATE_KEY_BYTES,
    )
    if len(raw) != ED25519_PRIVATE_KEY_BYTES:
        raise ControllerCampaignSigningKeyError("controller campaign signing private key has an invalid length")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise ControllerCampaignSigningKeyError("cryptography Ed25519 support is unavailable") from exc
    try:
        public = Ed25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except ValueError as exc:  # pragma: no cover - raw 32-byte input is accepted by the backend.
        raise ControllerCampaignSigningKeyError("controller campaign signing private key is invalid") from exc
    if len(public) != ED25519_PUBLIC_KEY_BYTES:  # pragma: no cover - backend invariant.
        raise ControllerCampaignSigningKeyError("controller campaign signing public key has an invalid length")
    return base64.b64encode(public).decode("ascii")


def _receipt_value(*, layout: SigningKeyLayout, public_key_base64: str) -> dict[str, str]:
    return {
        "schema": RECEIPT_SCHEMA,
        "status": "bound",
        "campaign_id": layout.campaign_id,
        "campaign_binding_sha256": layout.campaign_binding_sha256,
        "algorithm": "ed25519",
        "public_key_base64": public_key_base64,
        "key_id": key_id_for_public_key(public_key_base64),
    }


def _parse_receipt(payload: bytes, *, layout: SigningKeyLayout) -> tuple[str, str, dict[str, str]]:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_RECEIPT_BYTES:
        raise ControllerCampaignSigningKeyError("controller signing-key receipt has an unsafe size")
    lowered = payload.lower()
    if b"://" in lowered or b'"url"' in lowered or b"presigned" in lowered:
        raise ControllerCampaignSigningKeyError("controller signing-key receipt persists a forbidden URL")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControllerCampaignSigningKeyError("controller signing-key receipt is not strict JSON") from exc
    if not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise ControllerCampaignSigningKeyError("controller signing-key receipt is not canonical JSON")
    expected = {
        "schema",
        "status",
        "campaign_id",
        "campaign_binding_sha256",
        "algorithm",
        "public_key_base64",
        "key_id",
    }
    if set(value) != expected or value.get("schema") != RECEIPT_SCHEMA or value.get("status") != "bound":
        raise ControllerCampaignSigningKeyError("controller signing-key receipt is unsupported")
    if value.get("campaign_id") != layout.campaign_id:
        raise ControllerCampaignSigningKeyError("controller signing-key receipt campaign does not match binding")
    if value.get("campaign_binding_sha256") != layout.campaign_binding_sha256:
        raise ControllerCampaignSigningKeyError("controller signing-key receipt binding does not match campaign")
    if value.get("algorithm") != "ed25519":
        raise ControllerCampaignSigningKeyError("controller signing-key receipt algorithm is invalid")
    public = value.get("public_key_base64")
    _decode_public_key(public, field="controller signing-key receipt public key")
    key_id = value.get("key_id")
    if not isinstance(key_id, str) or not SHA256_RE.fullmatch(key_id.removeprefix("ed25519-sha256:")):
        raise ControllerCampaignSigningKeyError("controller signing-key receipt key identifier is invalid")
    if key_id != key_id_for_public_key(public):
        raise ControllerCampaignSigningKeyError("controller signing-key receipt key identifier is invalid")
    return public, key_id, dict(value)


def _assert_signing_outputs_absent(layout: SigningKeyLayout) -> None:
    _require_if_present_private_directory(
        layout.controller_directory,
        field="controller campaign directory",
    )
    _require_absent(layout.signing_directory, field="controller campaign signing-key directory")
    # The signing directory is intentionally new on every enrollment, but keep
    # the leaf checks explicit in case the directory appears between admission
    # and creation on an incorrectly administered host.
    _require_absent(layout.private_key_path, field="controller campaign signing private key")
    _require_absent(layout.receipt_path, field="controller campaign signing-key receipt")


def enroll_campaign_signing_key(*, campaign_binding_path: Path, apply: bool = False) -> dict[str, Any]:
    """Plan or create exactly one fresh controller campaign signing key."""

    _require_root_execution()
    if not isinstance(apply, bool):
        raise ControllerCampaignSigningKeyError("controller signing-key apply flag is invalid")
    layout = signing_key_layout_for_campaign_binding(Path(campaign_binding_path))
    _require_private_directory(layout.campaign_directory, field="campaign directory")
    _assert_signing_outputs_absent(layout)
    if not apply:
        return {
            "status": "planned",
            "campaign_id": layout.campaign_id,
            "campaign_binding_sha256": layout.campaign_binding_sha256,
            "algorithm": "ed25519",
            "private_key_created": False,
        }

    controller_directory = _create_or_require_private_child(
        layout.campaign_directory,
        CONTROLLER_DIRECTORY_NAME,
        field="controller campaign directory",
    )
    if controller_directory != layout.controller_directory:  # pragma: no cover - fixed-child invariant.
        raise ControllerCampaignSigningKeyError("controller campaign directory changed while being created")
    _assert_signing_outputs_absent(layout)
    signing_directory = _create_new_private_child(
        controller_directory,
        SIGNING_DIRECTORY_NAME,
        field="controller campaign signing-key directory",
    )
    if signing_directory != layout.signing_directory:  # pragma: no cover - fixed-child invariant.
        raise ControllerCampaignSigningKeyError("controller signing-key directory changed while being created")
    _require_absent(layout.private_key_path, field="controller campaign signing private key")
    _require_absent(layout.receipt_path, field="controller campaign signing-key receipt")

    private_raw, public_key_base64 = _generate_ed25519_private_key()
    _write_new_private_file(
        layout.private_key_path,
        private_raw,
        field="controller campaign signing private key",
        maximum_bytes=ED25519_PRIVATE_KEY_BYTES,
    )
    if _public_key_from_private_key(layout.private_key_path) != public_key_base64:
        raise ControllerCampaignSigningKeyError("new controller campaign signing private key changed after creation")
    receipt = _receipt_value(layout=layout, public_key_base64=public_key_base64)
    receipt_payload = canonical_json_bytes(receipt) + b"\n"
    _write_new_private_file(
        layout.receipt_path,
        receipt_payload,
        field="controller campaign signing-key receipt",
        maximum_bytes=MAX_RECEIPT_BYTES,
    )
    verified = load_verified_campaign_signing_key(campaign_binding_path=campaign_binding_path)
    if verified.public_key_base64 != public_key_base64 or verified.receipt != receipt:
        raise ControllerCampaignSigningKeyError("created controller campaign signing key changed while being verified")
    return {
        "status": "created",
        "campaign_id": layout.campaign_id,
        "campaign_binding_sha256": layout.campaign_binding_sha256,
        "algorithm": "ed25519",
        "controller_signing_public_key_base64": verified.public_key_base64,
        "controller_signing_key_id": verified.key_id,
        "signing_key_receipt": dict(verified.receipt),
        "receipt_sha256": verified.receipt_sha256,
        "private_key_created": True,
    }


def load_verified_campaign_signing_key(*, campaign_binding_path: Path) -> VerifiedSigningKey:
    """Re-read the fixed receipt and prove it matches the private key locally."""

    _require_root_execution()
    layout = signing_key_layout_for_campaign_binding(Path(campaign_binding_path))
    _require_private_directory(layout.campaign_directory, field="campaign directory")
    _require_private_directory(layout.controller_directory, field="controller campaign directory")
    _require_private_directory(layout.signing_directory, field="controller campaign signing-key directory")
    public = _public_key_from_private_key(layout.private_key_path)
    payload = _read_private_file(
        layout.receipt_path,
        field="controller campaign signing-key receipt",
        maximum_bytes=MAX_RECEIPT_BYTES,
    )
    receipt_public, key_id, receipt = _parse_receipt(payload, layout=layout)
    if public != receipt_public:
        raise ControllerCampaignSigningKeyError(
            "controller campaign signing public key does not match its receipt"
        )
    return VerifiedSigningKey(
        layout=layout,
        public_key_base64=public,
        key_id=key_id,
        receipt_sha256=sha256_bytes(payload),
        receipt=receipt,
    )


def load_verified_campaign_signer(*, campaign_binding_path: Path) -> VerifiedCampaignSigner:
    """Load the only signing authority selected by a canonical campaign binding.

    Source-stage callers must use this instead of accepting a private-key
    pathname.  It revalidates the canonical binding, fixed key layout, receipt
    and public/private correspondence in one operation, while keeping the raw
    private bytes inside this helper.
    """

    _require_root_execution()
    verified = load_verified_campaign_signing_key(campaign_binding_path=campaign_binding_path)
    campaign, _campaign_directory = _require_exact_campaign_binding_path(
        Path(campaign_binding_path)
    )
    if (
        campaign.campaign_id != verified.layout.campaign_id
        or campaign.binding_sha256 != verified.layout.campaign_binding_sha256
    ):
        raise ControllerCampaignSigningKeyError(
            "canonical campaign binding changed while loading its signing key"
        )
    raw = _read_private_file(
        verified.layout.private_key_path,
        field="controller campaign signing private key",
        maximum_bytes=ED25519_PRIVATE_KEY_BYTES,
    )
    if len(raw) != ED25519_PRIVATE_KEY_BYTES:
        raise ControllerCampaignSigningKeyError(
            "controller campaign signing private key has an invalid length"
        )
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        signer = Ed25519PrivateKey.from_private_bytes(raw)
        public = signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except (ImportError, ValueError) as exc:
        raise ControllerCampaignSigningKeyError(
            "controller campaign signing private key is invalid"
        ) from exc
    public_key_base64 = base64.b64encode(public).decode("ascii")
    if public_key_base64 != verified.public_key_base64:
        raise ControllerCampaignSigningKeyError(
            "controller campaign signing private key changed while being loaded"
        )
    # Re-read the binding last.  A write caller can compare this immutable
    # authority record with a second invocation immediately before output.
    final_campaign, _final_directory = _require_exact_campaign_binding_path(
        Path(campaign_binding_path)
    )
    if (
        final_campaign.campaign_id != campaign.campaign_id
        or final_campaign.binding_sha256 != campaign.binding_sha256
        or final_campaign.binding_sha256 != verified.layout.campaign_binding_sha256
    ):
        raise ControllerCampaignSigningKeyError(
            "canonical campaign binding changed while loading its signing key"
        )
    return VerifiedCampaignSigner(
        signer=signer,
        signing_key=verified,
        campaign_binding=final_campaign,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    enroll = actions.add_parser("enroll", help="plan or explicitly create one fixed fresh controller signing key")
    enroll.add_argument("--campaign-binding", required=True, type=Path)
    enroll.add_argument("--apply", action="store_true", help="create the key only after the plan is approved")
    verify = actions.add_parser("verify", help="verify the fixed controller signing key and public receipt")
    verify.add_argument("--campaign-binding", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.action == "enroll":
            result = enroll_campaign_signing_key(
                campaign_binding_path=args.campaign_binding,
                apply=args.apply,
            )
        elif args.action == "verify":
            verified = load_verified_campaign_signing_key(campaign_binding_path=args.campaign_binding)
            result = {
                "status": "verified",
                "campaign_id": verified.layout.campaign_id,
                "campaign_binding_sha256": verified.layout.campaign_binding_sha256,
                "algorithm": "ed25519",
                "controller_signing_public_key_base64": verified.public_key_base64,
                "controller_signing_key_id": verified.key_id,
                "signing_key_receipt": dict(verified.receipt),
                "receipt_sha256": verified.receipt_sha256,
            }
        else:  # pragma: no cover - argparse dispatch invariant.
            raise ControllerCampaignSigningKeyError("unsupported action")
    except ControllerCampaignSigningKeyError as exc:
        print(
            canonical_json_bytes(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}
            ).decode("ascii"),
            file=sys.stderr,
        )
        return 2
    print(canonical_json_bytes(result).decode("ascii"))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI wrapper.
    raise SystemExit(main())
