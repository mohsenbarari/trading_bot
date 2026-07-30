#!/usr/bin/env python3
"""Bootstrap one fresh, campaign-bound WebApp-FI source signing key.

This FI-side helper runs only from an already verified source-adoption
candidate.  It creates a single raw Ed25519 private key below the campaign's
root-only FI directory and writes an URL-free, non-secret receipt containing
the corresponding public key, key ID, and SSH-host-key digest needed by the
controller-side enrollment issuer.

It deliberately has no Object Storage, SSH, Docker, service, current, volume,
or application-data capability.  It never prints or persists the private key
outside its one create-only key file.  A failed or interrupted create is kept
as evidence and must not be retried without separately deciding how to handle
the retained material.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import types
from typing import Any, Mapping, Sequence


SOURCE_SIGNER_BOOTSTRAP_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-signer-bootstrap-receipt-v1"
INSTALL_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-adoption-install-receipt-v1"
INSTALL_RECEIPT_NAME = "source-adoption-install-receipt.json"
INSTALL_SCRIPT_RELATIVE = "scripts/install_webapp_fi_source_adoption.py"
THIS_SCRIPT_RELATIVE = "scripts/bootstrap_webapp_fi_source_signer.py"

SOURCE_SITE = "webapp_fi"
DESTINATION_SITE = "webapp_ir"
CAMPAIGN_ROOT = Path("/etc/trading-bot-three-site/campaigns")
FI_SOURCE_SIGNER_DIRECTORY = "webapp-fi"
FI_SOURCE_SIGNER_KEY_NAME = "source-signing-ed25519.raw"
FI_SOURCE_SIGNER_RECEIPT_NAME = "source-signing-receipt.json"

MAX_INSTALL_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_INSTALLED_SOURCE_BYTES = 8 * 1024 * 1024
MAX_SSH_HOST_PUBLIC_KEY_BYTES = 64 * 1024
MAX_BOOTSTRAP_RECEIPT_BYTES = 64 * 1024

CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ALEMBIC_REVISION_RE = re.compile(r"^[0-9a-f]{12}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SourceSignerBootstrapError(RuntimeError):
    """The isolated FI signer bootstrap cannot be safely completed."""


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceSignerBootstrapError("JSON input contains duplicate keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise SourceSignerBootstrapError("JSON input contains an unsupported constant")


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise SourceSignerBootstrapError("WebApp-FI source signer bootstrap must run as root")


def _require_absolute_canonical_path(path: Path, *, field: str) -> Path:
    candidate = Path(path)
    if (
        "\x00" in str(candidate)
        or not candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts[1:])
        or str(candidate) != os.path.normpath(str(candidate))
    ):
        raise SourceSignerBootstrapError(f"{field} must be one canonical absolute path")
    return candidate


def _require_root_controlled_ancestors(path: Path, *, field: str) -> None:
    """Reject a path whose lookup can be redirected by another account."""

    path = _require_absolute_canonical_path(path, field=field)
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise SourceSignerBootstrapError(f"cannot inspect {field} parent") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise SourceSignerBootstrapError(f"{field} parent is unsafe")


def _same_file_metadata(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_mode == right.st_mode
        and left.st_uid == right.st_uid
        and left.st_size == right.st_size
        and left.st_nlink == right.st_nlink
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
    )


def _safe_file_metadata(
    metadata: os.stat_result,
    *,
    private: bool,
    maximum_bytes: int,
    exact_mode: int | None = None,
) -> bool:
    if exact_mode is not None:
        mode_is_safe = stat.S_IMODE(metadata.st_mode) == exact_mode
    else:
        mode_is_safe = not (stat.S_IMODE(metadata.st_mode) & (0o077 if private else 0o022))
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == 0
        and metadata.st_nlink == 1
        and not (metadata.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX))
        and mode_is_safe
        and 1 <= metadata.st_size <= maximum_bytes
    )


def _read_root_controlled_file(
    path: Path,
    *,
    field: str,
    maximum_bytes: int,
    private: bool,
    exact_mode: int | None = None,
) -> bytes:
    """Read one FD-pinned root-controlled regular file without following links."""

    source = _require_absolute_canonical_path(Path(path), field=field)
    _require_root_controlled_ancestors(source.parent, field=field)
    try:
        before = source.lstat()
    except OSError as exc:
        raise SourceSignerBootstrapError(f"cannot inspect {field}") from exc
    if not _safe_file_metadata(
        before,
        private=private,
        maximum_bytes=maximum_bytes,
        exact_mode=exact_mode,
    ):
        raise SourceSignerBootstrapError(f"{field} is unsafe")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:  # pragma: no cover - Linux deployment invariant.
        raise SourceSignerBootstrapError("secure no-follow file access is unavailable")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | no_follow
    try:
        descriptor = os.open(str(source), flags)
    except OSError as exc:
        raise SourceSignerBootstrapError(f"cannot securely open {field}") from exc
    try:
        opened = os.fstat(descriptor)
        if not _same_file_metadata(before, opened) or not _safe_file_metadata(
            opened,
            private=private,
            maximum_bytes=maximum_bytes,
            exact_mode=exact_mode,
        ):
            raise SourceSignerBootstrapError(f"{field} changed while opening")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise SourceSignerBootstrapError(f"{field} is too large")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if len(b"".join(chunks)) != opened.st_size or not _same_file_metadata(opened, after):
            raise SourceSignerBootstrapError(f"{field} changed while reading")
        return b"".join(chunks)
    except OSError as exc:
        raise SourceSignerBootstrapError(f"cannot read {field}") from exc
    finally:
        os.close(descriptor)


def _require_root_private_directory(path: Path, *, field: str) -> Path:
    directory = _require_absolute_canonical_path(Path(path), field=field)
    _require_root_controlled_ancestors(directory.parent, field=field)
    try:
        metadata = directory.lstat()
        resolved = directory.resolve(strict=True)
        target = resolved.lstat()
    except OSError as exc:
        raise SourceSignerBootstrapError(f"cannot inspect {field}") from exc
    if (
        resolved != directory
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_ISLNK(target.st_mode)
        or not stat.S_ISDIR(target.st_mode)
        or target.st_uid != 0
        or stat.S_IMODE(target.st_mode) != 0o700
    ):
        raise SourceSignerBootstrapError(f"{field} must be one root-only mode 0700 non-symlink directory")
    return resolved


def _create_or_require_root_private_directory(parent: Path, name: str, *, field: str) -> Path:
    parent = _require_root_private_directory(parent, field=field + " parent")
    if not name or "/" in name or name in {".", ".."}:
        raise SourceSignerBootstrapError(f"{field} name is unsafe")
    child = parent / name
    try:
        os.mkdir(child, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise SourceSignerBootstrapError(f"cannot create {field}") from exc
    return _require_root_private_directory(child, field=field)


def _parse_canonical_json(payload: bytes, *, field: str, maximum_bytes: int) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= maximum_bytes:
        raise SourceSignerBootstrapError(f"{field} has an unsafe size")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceSignerBootstrapError(f"{field} is not strict canonical JSON") from exc
    if not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise SourceSignerBootstrapError(f"{field} is not canonical JSON")
    return value


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise SourceSignerBootstrapError(f"{field} is invalid")
    return value


def _require_campaign_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not CAMPAIGN_ID_RE.fullmatch(value):
        raise SourceSignerBootstrapError(f"{field} is invalid")
    return value


def _parse_install_receipt(payload: bytes) -> dict[str, Any]:
    value = _parse_canonical_json(
        payload,
        field="source-adoption install receipt",
        maximum_bytes=MAX_INSTALL_RECEIPT_BYTES,
    )
    expected = {
        "schema", "status", "installed_at", "candidate_directory", "source_site", "destination_site",
        "campaign_id", "package_id", "application", "tooling", "files", "canonical_release_tree_sha256",
        "package", "receipt_sha256",
    }
    if set(value) != expected or value.get("schema") != INSTALL_RECEIPT_SCHEMA or value.get("status") != "installed":
        raise SourceSignerBootstrapError("source-adoption install receipt is unsupported")
    receipt_sha = _require_sha256(value.get("receipt_sha256"), field="source-adoption install receipt hash")
    unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if sha256_bytes(canonical_json_bytes(unsigned)) != receipt_sha:
        raise SourceSignerBootstrapError("source-adoption install receipt hash is invalid")
    return value


def _load_verified_installed_adoption(install_receipt: Path) -> tuple[Any, dict[str, Any]]:
    """Load the installed verifier only after the receipt pins both script hashes.

    The helper itself must be executed from the installed candidate.  This
    avoids treating a copy of this file in an arbitrary checkout as authority
    to create a campaign key.
    """

    receipt_path = _require_absolute_canonical_path(Path(install_receipt), field="install receipt")
    receipt_payload = _read_root_controlled_file(
        receipt_path,
        field="install receipt",
        maximum_bytes=MAX_INSTALL_RECEIPT_BYTES,
        private=True,
        exact_mode=0o600,
    )
    receipt = _parse_install_receipt(receipt_payload)
    candidate_text = receipt.get("candidate_directory")
    if not isinstance(candidate_text, str):
        raise SourceSignerBootstrapError("source-adoption install receipt candidate is invalid")
    candidate = _require_root_private_directory(Path(candidate_text), field="installed source-adoption candidate")
    if candidate_text != str(candidate) or receipt_path != candidate / INSTALL_RECEIPT_NAME:
        raise SourceSignerBootstrapError("source-adoption install receipt is not candidate-bound")
    files = receipt.get("files")
    if not isinstance(files, Mapping):
        raise SourceSignerBootstrapError("installed source-adoption helper hashes are unavailable")
    expected_installer_sha = _require_sha256(
        files.get(INSTALL_SCRIPT_RELATIVE),
        field="installed source-adoption verifier hash",
    )
    expected_bootstrap_sha = _require_sha256(
        files.get(THIS_SCRIPT_RELATIVE),
        field="installed source signer bootstrap hash",
    )
    current_script = _require_absolute_canonical_path(
        Path(__file__).absolute(),
        field="source signer bootstrap script",
    )
    installed_bootstrap = candidate / THIS_SCRIPT_RELATIVE
    if current_script != installed_bootstrap:
        raise SourceSignerBootstrapError("source signer bootstrap must run from the verified installed candidate")
    bootstrap_bytes = _read_root_controlled_file(
        installed_bootstrap,
        field="installed source signer bootstrap",
        maximum_bytes=MAX_INSTALLED_SOURCE_BYTES,
        private=True,
        exact_mode=0o600,
    )
    if sha256_bytes(bootstrap_bytes) != expected_bootstrap_sha:
        raise SourceSignerBootstrapError("installed source signer bootstrap hash changed")
    installer_path = candidate / INSTALL_SCRIPT_RELATIVE
    installer_bytes = _read_root_controlled_file(
        installer_path,
        field="installed source-adoption verifier",
        maximum_bytes=MAX_INSTALLED_SOURCE_BYTES,
        private=True,
        exact_mode=0o600,
    )
    if sha256_bytes(installer_bytes) != expected_installer_sha:
        raise SourceSignerBootstrapError("installed source-adoption verifier hash changed")
    module = types.ModuleType("_verified_webapp_fi_source_adoption_installer")
    module.__file__ = str(installer_path)
    module.__package__ = ""
    try:
        exec(compile(installer_bytes, str(installer_path), "exec"), module.__dict__)
    except BaseException as exc:
        raise SourceSignerBootstrapError("cannot load verified source-adoption installer") from exc
    if (
        getattr(module, "INSTALL_RECEIPT_SCHEMA", None) != INSTALL_RECEIPT_SCHEMA
        or getattr(module, "PACKAGE_DESTINATION_SITE", None) != SOURCE_SITE
        or getattr(module, "SNAPSHOT_DESTINATION_SITE", None) != DESTINATION_SITE
        or not callable(getattr(module, "verify_installed_source_adoption", None))
    ):
        raise SourceSignerBootstrapError("verified source-adoption installer contract is incompatible")
    try:
        installed = module.verify_installed_source_adoption(receipt_path)
    except Exception as exc:
        raise SourceSignerBootstrapError("installed source-adoption receipt cannot be verified") from exc
    if not isinstance(installed, Mapping) or installed.get("candidate") != candidate:
        raise SourceSignerBootstrapError("installed source-adoption receipt changed while being verified")
    return module, dict(installed)


def source_signer_paths(campaign_id: str) -> tuple[Path, Path, Path]:
    """Return the one FI key and receipt location permitted for a campaign."""

    campaign = _require_campaign_id(campaign_id, field="campaign_id")
    root = _require_absolute_canonical_path(CAMPAIGN_ROOT, field="campaign root")
    campaign_directory = root / campaign
    fi_directory = campaign_directory / FI_SOURCE_SIGNER_DIRECTORY
    key_path = fi_directory / FI_SOURCE_SIGNER_KEY_NAME
    receipt_path = fi_directory / FI_SOURCE_SIGNER_RECEIPT_NAME
    expected = PurePosixPath(root.as_posix()) / campaign / FI_SOURCE_SIGNER_DIRECTORY
    if PurePosixPath(fi_directory.as_posix()) != expected:
        raise SourceSignerBootstrapError("source signer path is not campaign-derived")
    return campaign_directory, key_path, receipt_path


def _require_absent(path: Path, *, field: str) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise SourceSignerBootstrapError(f"cannot inspect {field}") from exc
    raise SourceSignerBootstrapError(f"refusing to reuse or overwrite existing {field}")


def _load_ssh_host_public_key_digest(path: Path) -> str:
    # The later FI-side enrollment verifier reads this same public-key file as
    # root-only material.  Keep the bootstrap admission identical so the hash
    # returned here can be used without introducing a second, differently
    # protected copy of the host key.
    payload = _read_root_controlled_file(
        Path(path),
        field="FI SSH host public key",
        maximum_bytes=MAX_SSH_HOST_PUBLIC_KEY_BYTES,
        private=True,
        exact_mode=0o600,
    )
    if b"\x00" in payload or not payload.rstrip(b"\r\n"):
        raise SourceSignerBootstrapError("FI SSH host public key is invalid")
    return sha256_bytes(payload)


def _generate_ed25519_private_key() -> tuple[bytes, str]:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise SourceSignerBootstrapError("cryptography Ed25519 support is unavailable") from exc
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
    if len(raw) != 32 or len(public) != 32:  # pragma: no cover - cryptography invariant.
        raise SourceSignerBootstrapError("generated Ed25519 key has an unsafe length")
    return raw, base64.b64encode(public).decode("ascii")


def _write_new_private_file(path: Path, payload: bytes, *, field: str) -> None:
    path = _require_absolute_canonical_path(path, field=field)
    _require_root_private_directory(path.parent, field=field + " parent")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:  # pragma: no cover - Linux deployment invariant.
        raise SourceSignerBootstrapError("secure no-follow file creation is unavailable")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | no_follow
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise SourceSignerBootstrapError(f"refusing to reuse or overwrite existing {field}") from exc
    except OSError as exc:
        raise SourceSignerBootstrapError(f"cannot create {field}") from exc
    try:
        os.fchmod(descriptor, 0o600)
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:  # pragma: no cover - regular file writes do not return zero.
                raise OSError("short write")
            remaining = remaining[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if not _safe_file_metadata(
            metadata,
            private=True,
            maximum_bytes=max(len(payload), 1),
            exact_mode=0o600,
        ) or metadata.st_size != len(payload):
            raise SourceSignerBootstrapError(f"new {field} is unsafe")
    except SourceSignerBootstrapError:
        raise
    except OSError as exc:
        raise SourceSignerBootstrapError(f"cannot write {field}") from exc
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path, *, field: str) -> None:
    directory = _require_root_private_directory(path, field=field)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(str(directory), flags)
    except OSError as exc:
        raise SourceSignerBootstrapError(f"cannot open {field}") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) != 0o700
        ):
            raise SourceSignerBootstrapError(f"{field} changed while being opened")
        os.fsync(descriptor)
    except SourceSignerBootstrapError:
        raise
    except OSError as exc:
        raise SourceSignerBootstrapError(f"cannot durably sync {field}") from exc
    finally:
        os.close(descriptor)


def _public_key_id(public_key_base64: str) -> str:
    try:
        raw = base64.b64decode(public_key_base64.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise SourceSignerBootstrapError("source signing public key is invalid") from exc
    if len(raw) != 32 or public_key_base64 != base64.b64encode(raw).decode("ascii"):
        raise SourceSignerBootstrapError("source signing public key is invalid")
    return "ed25519-sha256:" + sha256_bytes(raw)


def _source_adoption_claim(installed: Mapping[str, Any]) -> dict[str, Any]:
    application = installed.get("application")
    tooling = installed.get("tooling")
    package = installed.get("package")
    if (
        not isinstance(application, Mapping)
        or not isinstance(tooling, Mapping)
        or not isinstance(package, Mapping)
        or not isinstance(installed.get("package_id"), str)
    ):
        raise SourceSignerBootstrapError("verified source-adoption receipt is incomplete")
    release = application.get("release_sha")
    revision = application.get("expected_alembic_revision")
    commit = tooling.get("control_commit")
    tree = tooling.get("control_tree")
    if (
        not isinstance(release, str)
        or not GIT_SHA_RE.fullmatch(release)
        or not isinstance(revision, str)
        or not ALEMBIC_REVISION_RE.fullmatch(revision)
        or not isinstance(commit, str)
        or not GIT_SHA_RE.fullmatch(commit)
        or not isinstance(tree, str)
        or not GIT_SHA_RE.fullmatch(tree)
    ):
        raise SourceSignerBootstrapError("verified source-adoption release binding is invalid")
    return {
        "candidate_directory": str(installed["candidate"]),
        "package_id": installed["package_id"],
        "application": {"release_sha": release, "expected_alembic_revision": revision},
        "tooling": {"control_commit": commit, "control_tree": tree},
        "install_receipt_sha256": _require_sha256(
            installed.get("receipt_sha256"),
            field="verified source-adoption install receipt hash",
        ),
        "delivery_envelope_sha256": _require_sha256(
            package.get("delivery_envelope_sha256"),
            field="verified source-adoption delivery envelope hash",
        ),
    }


def _bootstrap_receipt(
    *,
    campaign_id: str,
    source_adoption: Mapping[str, Any],
    key_path: Path,
    public_key_base64: str,
    ssh_host_public_key_sha256: str,
) -> dict[str, Any]:
    public_id = _public_key_id(public_key_base64)
    unsigned: dict[str, Any] = {
        "schema": SOURCE_SIGNER_BOOTSTRAP_RECEIPT_SCHEMA,
        "status": "created",
        "created_at": utc_now(),
        "campaign_id": campaign_id,
        "source_site": SOURCE_SITE,
        "destination_site": DESTINATION_SITE,
        "source_adoption": dict(source_adoption),
        "source_signer": {
            "private_key_file": str(key_path),
            "public_key_base64": public_key_base64,
            "key_id": public_id,
        },
        "fi_ssh_host_public_key_sha256": ssh_host_public_key_sha256,
    }
    return {**unsigned, "receipt_sha256": sha256_bytes(canonical_json_bytes(unsigned))}


def _validate_created_key(path: Path, *, expected_public_key_base64: str) -> None:
    raw = _read_root_controlled_file(
        path,
        field="new WebApp-FI source signing private key",
        maximum_bytes=32,
        private=True,
        exact_mode=0o600,
    )
    if len(raw) != 32:
        raise SourceSignerBootstrapError("new WebApp-FI source signing private key has an invalid length")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        public = Ed25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except (ImportError, ValueError) as exc:
        raise SourceSignerBootstrapError("new WebApp-FI source signing private key is invalid") from exc
    if base64.b64encode(public).decode("ascii") != expected_public_key_base64:
        raise SourceSignerBootstrapError("new WebApp-FI source signing private key changed after creation")


def bootstrap_source_signer(
    *,
    install_receipt: Path,
    ssh_host_public_key_file: Path,
    apply: bool,
) -> dict[str, Any]:
    """Plan or create one fresh FI source signer tied to the installed package."""

    _require_root_execution()
    _, installed = _load_verified_installed_adoption(Path(install_receipt))
    campaign_id = _require_campaign_id(installed.get("campaign_id"), field="installed source-adoption campaign_id")
    source_adoption = _source_adoption_claim(installed)
    ssh_digest = _load_ssh_host_public_key_digest(Path(ssh_host_public_key_file))
    campaign_directory, key_path, receipt_path = source_signer_paths(campaign_id)
    campaign_directory = _require_root_private_directory(campaign_directory, field="campaign directory")
    if campaign_directory.name != campaign_id:
        raise SourceSignerBootstrapError("campaign directory does not match the verified source-adoption campaign")
    fi_directory = campaign_directory / FI_SOURCE_SIGNER_DIRECTORY
    if not apply:
        if fi_directory.exists() or fi_directory.is_symlink():
            _require_root_private_directory(fi_directory, field="WebApp-FI campaign directory")
        _require_absent(key_path, field="WebApp-FI source signing private key")
        _require_absent(receipt_path, field="WebApp-FI source signer bootstrap receipt")
        return {
            "status": "planned",
            "campaign_id": campaign_id,
            "source_signing_private_key_file": str(key_path),
            "receipt_path": str(receipt_path),
            "fi_ssh_host_public_key_sha256": ssh_digest,
            "private_key_created": False,
            "object_storage_action": False,
            "ssh_action": False,
            "docker_action": False,
            "service_changed": False,
            "current_changed": False,
            "container_changed": False,
            "volume_changed": False,
            "application_data_changed": False,
        }

    # Check both outputs before the first irreversible write.  A retained key
    # or receipt is an ambiguity that must be investigated, never replaced.
    _require_absent(key_path, field="WebApp-FI source signing private key")
    _require_absent(receipt_path, field="WebApp-FI source signer bootstrap receipt")
    fi_directory = _create_or_require_root_private_directory(
        campaign_directory,
        FI_SOURCE_SIGNER_DIRECTORY,
        field="WebApp-FI campaign directory",
    )
    if key_path.parent != fi_directory or receipt_path.parent != fi_directory:
        raise SourceSignerBootstrapError("source signer output path changed before creation")
    _require_absent(key_path, field="WebApp-FI source signing private key")
    _require_absent(receipt_path, field="WebApp-FI source signer bootstrap receipt")

    # Rebind the candidate immediately before creating a live signing key.
    _, reverified = _load_verified_installed_adoption(Path(install_receipt))
    if (
        _require_campaign_id(reverified.get("campaign_id"), field="reverified campaign_id") != campaign_id
        or _source_adoption_claim(reverified) != source_adoption
    ):
        raise SourceSignerBootstrapError("verified source-adoption binding changed before key creation")

    private_raw, public_key_base64 = _generate_ed25519_private_key()
    _write_new_private_file(key_path, private_raw, field="WebApp-FI source signing private key")
    _validate_created_key(key_path, expected_public_key_base64=public_key_base64)
    receipt = _bootstrap_receipt(
        campaign_id=campaign_id,
        source_adoption=source_adoption,
        key_path=key_path,
        public_key_base64=public_key_base64,
        ssh_host_public_key_sha256=ssh_digest,
    )
    payload = canonical_json_bytes(receipt) + b"\n"
    _write_new_private_file(receipt_path, payload, field="WebApp-FI source signer bootstrap receipt")
    persisted = _read_root_controlled_file(
        receipt_path,
        field="new WebApp-FI source signer bootstrap receipt",
        maximum_bytes=MAX_BOOTSTRAP_RECEIPT_BYTES,
        private=True,
        exact_mode=0o600,
    )
    if persisted != payload:
        raise SourceSignerBootstrapError("source signer bootstrap receipt changed after creation")
    _fsync_directory(fi_directory, field="WebApp-FI campaign directory")
    return {
        "status": "created",
        "campaign_id": campaign_id,
        "source_signing_private_key_file": str(key_path),
        "receipt_path": str(receipt_path),
        # This is deliberately the complete public receipt rather than a
        # remote path-only reference.  A controller can persist this exact
        # URL-free control record from its pinned SSH session without ever
        # reading the FI private key.
        "source_signing_receipt": receipt,
        "source_signing_public_key_base64": public_key_base64,
        "source_signing_key_id": _public_key_id(public_key_base64),
        "fi_ssh_host_public_key_sha256": ssh_digest,
        "source_adoption_install_receipt_sha256": source_adoption["install_receipt_sha256"],
        "private_key_created": True,
        "object_storage_action": False,
        "ssh_action": False,
        "docker_action": False,
        "service_changed": False,
        "current_changed": False,
        "container_changed": False,
        "volume_changed": False,
        "application_data_changed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-receipt", type=Path, required=True)
    parser.add_argument("--ssh-host-public-key-file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = bootstrap_source_signer(
            install_receipt=args.install_receipt,
            ssh_host_public_key_file=args.ssh_host_public_key_file,
            apply=args.apply,
        )
    except SourceSignerBootstrapError as exc:
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
