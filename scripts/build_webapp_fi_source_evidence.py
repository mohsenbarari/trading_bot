#!/usr/bin/env python3
"""Seal one strict WebApp-FI source-evidence envelope.

This FI-side helper is deliberately a local producer only.  It runs from an
already verified source-adoption candidate, consumes one immutable
campaign-derived binding plus one signed role attestation and one signed image
export receipt, and writes a new root-only evidence envelope.  The later
exchange helper can encrypt and publish that one file through the separately
authorized Object Storage route.

It has no Object Storage credential or client, no SSH, Docker, service,
container, current, volume, application-data, migration, seed, restore,
failover, or Full Matrix capability.  Paths for the binding, signer key,
proofs, and result are fixed by the installed candidate and campaign ID; no
caller-selected proof or output path is accepted.  Failed create-only output
is intentionally retained as evidence and is never retried or cleaned up by
this helper.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import types
from typing import Any, Mapping, Sequence


SOURCE_EVIDENCE_ENVELOPE_SCHEMA = "gold-trade-webapp-fi-source-evidence-envelope-v1"
SOURCE_EVIDENCE_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-source-evidence-envelope-v1\x00"
INSTALL_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-adoption-install-receipt-v1"
INSTALL_RECEIPT_NAME = "source-adoption-install-receipt.json"
INSTALL_SCRIPT_RELATIVE = "scripts/install_webapp_fi_source_adoption.py"
THIS_SCRIPT_RELATIVE = "scripts/build_webapp_fi_source_evidence.py"
CAMPAIGN_BINDING_SCRIPT_RELATIVE = "scripts/webapp_fi_source_campaign_binding.py"
PROVENANCE_VERIFIER_SCRIPT_RELATIVE = "scripts/verify_webapp_fi_source_provenance.py"
IMAGE_ARCHIVE_CONTRACT_SCRIPT_RELATIVE = "scripts/webapp_ir_image_archive_contract.py"
CANONICAL_RELEASE_TREE_RELATIVE = "config/canonical-release-tree.json"

CAMPAIGN_BINDING_SCHEMA = "gold-trade-webapp-fi-source-campaign-binding-v1"
ATTESTATION_SCHEMA = "gold-trade-webapp-fi-source-role-attestation-v2"
IMAGE_EXPORT_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-image-export-receipt-v2"

SOURCE_SITE = "webapp_fi"
DESTINATION_SITE = "controller"
OBJECT_KIND = "source-evidence"
RECIPIENT_MODE = "single"

CAMPAIGN_ROOT = Path("/etc/trading-bot-three-site/campaigns")
FI_SOURCE_SIGNER_DIRECTORY = "webapp-fi"
FI_SOURCE_SIGNER_KEY_NAME = "source-signing-ed25519.raw"
FI_SOURCE_EXPORT_ROOT = Path("/srv/trading-bot-three-site-staging-data/webapp-fi-source-exports")
FI_SOURCE_EVIDENCE_ROOT = Path("/srv/trading-bot-three-site-staging-data/webapp-fi-source-evidence")
SOURCE_PHASE_DIRECTORY = "webapp-fi-source"
CAMPAIGN_BINDING_FILENAME = "campaign-binding.json"
ATTESTATION_DIRECTORY = "attestations"
IMAGE_EXPORT_RECEIPT_NAME = "image-export-receipt.json"
SOURCE_EVIDENCE_FILENAME = "source-evidence-envelope.json"

MAX_INSTALL_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_INSTALLED_SOURCE_BYTES = 8 * 1024 * 1024
MAX_PROOF_BYTES = 8 * 1024 * 1024
MAX_ENVELOPE_BYTES = 20 * 1024 * 1024
MAX_BINDING_BYTES = 16 * 1024

CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
GIT_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
ALEMBIC_REVISION_RE = re.compile(r"^[0-9a-f]{12}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


class SourceEvidenceError(RuntimeError):
    """The source-evidence producer lacks one exact, safe input binding."""


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    """Encode persistent control assertions in one canonical ASCII form."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceEvidenceError("JSON input contains duplicate keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise SourceEvidenceError(f"JSON input contains unsupported constant: {value}")


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise SourceEvidenceError("WebApp-FI source-evidence operations must run as root")


def _require_absolute_canonical_path(path: Path, *, field: str) -> Path:
    candidate = Path(path)
    if (
        "\x00" in str(candidate)
        or not candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts[1:])
        or str(candidate) != os.path.normpath(str(candidate))
    ):
        raise SourceEvidenceError(f"{field} must be one canonical absolute path")
    return candidate


def _require_root_controlled_ancestors(path: Path, *, field: str) -> None:
    """Reject symlinked or replaceable lookups while permitting root sticky /tmp."""

    path = _require_absolute_canonical_path(path, field=field)
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise SourceEvidenceError(f"cannot inspect {field} parent") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or ((mode & 0o022) and not (metadata.st_mode & stat.S_ISVTX))
        ):
            raise SourceEvidenceError(f"{field} parent is unsafe")


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
    maximum_bytes: int,
    exact_mode: int,
) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == 0
        and metadata.st_nlink == 1
        and not (metadata.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX))
        and stat.S_IMODE(metadata.st_mode) == exact_mode
        and 1 <= metadata.st_size <= maximum_bytes
    )


def _read_root_private_file(
    path: Path,
    *,
    field: str,
    maximum_bytes: int,
) -> bytes:
    """Read one FD-pinned root-only file without following links."""

    source = _require_absolute_canonical_path(Path(path), field=field)
    _require_root_controlled_ancestors(source.parent, field=field)
    try:
        before = source.lstat()
    except OSError as exc:
        raise SourceEvidenceError(f"cannot inspect {field}") from exc
    if not _safe_file_metadata(before, maximum_bytes=maximum_bytes, exact_mode=0o600):
        raise SourceEvidenceError(f"{field} is unsafe")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:  # pragma: no cover - Linux deployment invariant.
        raise SourceEvidenceError("secure no-follow file access is unavailable")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | no_follow
    try:
        descriptor = os.open(str(source), flags)
    except OSError as exc:
        raise SourceEvidenceError(f"cannot securely open {field}") from exc
    try:
        opened = os.fstat(descriptor)
        if not _same_file_metadata(before, opened) or not _safe_file_metadata(
            opened,
            maximum_bytes=maximum_bytes,
            exact_mode=0o600,
        ):
            raise SourceEvidenceError(f"{field} changed while opening")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise SourceEvidenceError(f"{field} is too large")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        payload = b"".join(chunks)
        if len(payload) != opened.st_size or not _same_file_metadata(opened, after):
            raise SourceEvidenceError(f"{field} changed while reading")
        return payload
    except OSError as exc:
        raise SourceEvidenceError(f"cannot read {field}") from exc
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
        raise SourceEvidenceError(f"cannot inspect {field}") from exc
    if (
        resolved != directory
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_ISLNK(target.st_mode)
        or not stat.S_ISDIR(target.st_mode)
        or target.st_uid != 0
        or stat.S_IMODE(target.st_mode) != 0o700
    ):
        raise SourceEvidenceError(f"{field} must be one root-only mode 0700 non-symlink directory")
    return resolved


def _require_absent(path: Path, *, field: str) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise SourceEvidenceError(f"cannot inspect {field}") from exc
    raise SourceEvidenceError(f"refusing to reuse or overwrite existing {field}")


def _create_or_require_root_private_directory(parent: Path, name: str, *, field: str) -> Path:
    parent = _require_root_private_directory(parent, field=field + " parent")
    if not isinstance(name, str) or not SAFE_IDENTIFIER_RE.fullmatch(name):
        raise SourceEvidenceError(f"{field} name is invalid")
    child = parent / name
    try:
        os.mkdir(child, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise SourceEvidenceError(f"cannot create {field}") from exc
    try:
        os.chmod(child, 0o700)
    except OSError as exc:
        raise SourceEvidenceError(f"cannot protect {field}") from exc
    return _require_root_private_directory(child, field=field)


def _create_new_root_private_directory(parent: Path, name: str, *, field: str) -> Path:
    parent = _require_root_private_directory(parent, field=field + " parent")
    if not isinstance(name, str) or not SAFE_IDENTIFIER_RE.fullmatch(name):
        raise SourceEvidenceError(f"{field} name is invalid")
    child = parent / name
    _require_absent(child, field=field)
    try:
        os.mkdir(child, 0o700)
        os.chmod(child, 0o700)
    except OSError as exc:
        raise SourceEvidenceError(f"cannot create {field}") from exc
    return _require_root_private_directory(child, field=field)


def _fsync_root_private_directory(path: Path, *, field: str) -> None:
    directory = _require_root_private_directory(path, field=field)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(str(directory), flags)
    except OSError as exc:
        raise SourceEvidenceError(f"cannot open {field}") from exc
    try:
        state = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(state.st_mode)
            or state.st_uid != 0
            or stat.S_IMODE(state.st_mode) != 0o700
        ):
            raise SourceEvidenceError(f"{field} changed while opening")
        os.fsync(descriptor)
    except SourceEvidenceError:
        raise
    except OSError as exc:
        raise SourceEvidenceError(f"cannot durably sync {field}") from exc
    finally:
        os.close(descriptor)


def _write_new_root_private_file(path: Path, payload: bytes, *, field: str) -> None:
    destination = _require_absolute_canonical_path(path, field=field)
    _require_root_private_directory(destination.parent, field=field + " parent")
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_ENVELOPE_BYTES:
        raise SourceEvidenceError(f"{field} payload is invalid")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:  # pragma: no cover - Linux deployment invariant.
        raise SourceEvidenceError("secure no-follow file creation is unavailable")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | no_follow
    try:
        descriptor = os.open(str(destination), flags, 0o600)
    except FileExistsError as exc:
        raise SourceEvidenceError(f"refusing to reuse or overwrite existing {field}") from exc
    except OSError as exc:
        raise SourceEvidenceError(f"cannot create {field}") from exc
    try:
        os.fchmod(descriptor, 0o600)
        pending = memoryview(payload)
        while pending:
            written = os.write(descriptor, pending)
            if written <= 0:  # pragma: no cover - regular-file writes do not return zero.
                raise OSError("short write")
            pending = pending[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not _safe_file_metadata(metadata, maximum_bytes=MAX_ENVELOPE_BYTES, exact_mode=0o600)
            or metadata.st_size != len(payload)
        ):
            raise SourceEvidenceError(f"new {field} is unsafe")
    except SourceEvidenceError:
        raise
    except OSError as exc:
        raise SourceEvidenceError(f"cannot durably create {field}") from exc
    finally:
        os.close(descriptor)


def _parse_canonical_json(
    payload: bytes,
    *,
    field: str,
    maximum_bytes: int,
    reject_url: bool = True,
) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= maximum_bytes:
        raise SourceEvidenceError(f"{field} has an unsafe size")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceEvidenceError(f"{field} is not strict canonical JSON") from exc
    if not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise SourceEvidenceError(f"{field} is not canonical JSON")
    if reject_url:
        lowered = payload.lower()
        if b"://" in lowered or b"presigned" in lowered or b'"url"' in lowered:
            raise SourceEvidenceError(f"{field} persists a forbidden URL")
    return value


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise SourceEvidenceError(f"{field} is invalid")
    return value


def _require_campaign_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not CAMPAIGN_ID_RE.fullmatch(value):
        raise SourceEvidenceError(f"{field} is invalid")
    return value


def _require_identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SAFE_IDENTIFIER_RE.fullmatch(value):
        raise SourceEvidenceError(f"{field} is invalid")
    return value


def _require_application(value: object, *, include_tree: bool, field: str) -> dict[str, str]:
    expected = {"release_sha", "expected_alembic_revision"}
    if include_tree:
        expected.add("release_tree")
    if not isinstance(value, Mapping) or set(value) != expected:
        raise SourceEvidenceError(f"{field} is invalid")
    release = value.get("release_sha")
    revision = value.get("expected_alembic_revision")
    if not isinstance(release, str) or not GIT_SHA_RE.fullmatch(release):
        raise SourceEvidenceError(f"{field}.release_sha is invalid")
    if not isinstance(revision, str) or not ALEMBIC_REVISION_RE.fullmatch(revision):
        raise SourceEvidenceError(f"{field}.expected_alembic_revision is invalid")
    result = {"release_sha": release, "expected_alembic_revision": revision}
    if include_tree:
        tree = value.get("release_tree")
        if not isinstance(tree, str) or not GIT_SHA_RE.fullmatch(tree):
            raise SourceEvidenceError(f"{field}.release_tree is invalid")
        result["release_tree"] = tree
    return result


def _require_tooling(value: object, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"control_commit", "control_tree"}:
        raise SourceEvidenceError(f"{field} is invalid")
    commit = value.get("control_commit")
    tree = value.get("control_tree")
    if not isinstance(commit, str) or not GIT_SHA_RE.fullmatch(commit):
        raise SourceEvidenceError(f"{field}.control_commit is invalid")
    if not isinstance(tree, str) or not GIT_SHA_RE.fullmatch(tree):
        raise SourceEvidenceError(f"{field}.control_tree is invalid")
    return {"control_commit": commit, "control_tree": tree}


def _require_timestamp(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not UTC_TIMESTAMP_RE.fullmatch(value):
        raise SourceEvidenceError(f"{field} is invalid")
    try:
        dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise SourceEvidenceError(f"{field} is invalid") from exc
    return value


def _decode_public_key(value: object, *, field: str) -> tuple[str, bytes]:
    if not isinstance(value, str):
        raise SourceEvidenceError(f"{field} is invalid")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise SourceEvidenceError(f"{field} is invalid") from exc
    if len(raw) != 32:
        raise SourceEvidenceError(f"{field} is invalid")
    return value, raw


def public_key_id(public_key_base64: str) -> str:
    _, raw = _decode_public_key(public_key_base64, field="public key")
    return "ed25519-sha256:" + sha256_bytes(raw)


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
        raise SourceEvidenceError("source-adoption install receipt is unsupported")
    receipt_sha = _require_sha256(value.get("receipt_sha256"), field="source-adoption install receipt hash")
    unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if sha256_bytes(canonical_json_bytes(unsigned)) != receipt_sha:
        raise SourceEvidenceError("source-adoption install receipt hash is invalid")
    return value


def _execute_verified_module(module_name: str, path: Path, source: bytes) -> Any:
    """Execute one package member only after its exact receipt-pinned hash."""

    previous = sys.modules.get(module_name)
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = ""
    sys.modules[module_name] = module
    try:
        exec(compile(source, str(path), "exec"), module.__dict__)
    except BaseException as exc:
        raise SourceEvidenceError(f"cannot load verified {path.name}") from exc
    finally:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
    return module


def _load_verified_installed_adoption(install_receipt: Path) -> tuple[Any, Any, Any, dict[str, Any]]:
    """Verify the candidate and load its complete trusted proof-verifier closure."""

    receipt_path = _require_absolute_canonical_path(Path(install_receipt), field="install receipt")
    receipt_payload = _read_root_private_file(
        receipt_path,
        field="install receipt",
        maximum_bytes=MAX_INSTALL_RECEIPT_BYTES,
    )
    receipt = _parse_install_receipt(receipt_payload)
    candidate_text = receipt.get("candidate_directory")
    if not isinstance(candidate_text, str):
        raise SourceEvidenceError("source-adoption install receipt candidate is invalid")
    candidate = _require_root_private_directory(Path(candidate_text), field="installed source-adoption candidate")
    if candidate_text != str(candidate) or receipt_path != candidate / INSTALL_RECEIPT_NAME:
        raise SourceEvidenceError("source-adoption install receipt is not candidate-bound")
    files = receipt.get("files")
    if not isinstance(files, Mapping):
        raise SourceEvidenceError("installed source-adoption helper hashes are unavailable")
    required_relatives = (
        INSTALL_SCRIPT_RELATIVE,
        THIS_SCRIPT_RELATIVE,
        CAMPAIGN_BINDING_SCRIPT_RELATIVE,
        PROVENANCE_VERIFIER_SCRIPT_RELATIVE,
        IMAGE_ARCHIVE_CONTRACT_SCRIPT_RELATIVE,
        CANONICAL_RELEASE_TREE_RELATIVE,
    )
    expected_hashes: dict[str, str] = {}
    trusted_bytes: dict[str, bytes] = {}
    for relative in required_relatives:
        expected = _require_sha256(files.get(relative), field=f"installed {relative} hash")
        path = candidate / relative
        source = _read_root_private_file(
            path,
            field=f"installed {relative}",
            maximum_bytes=MAX_INSTALLED_SOURCE_BYTES,
        )
        if sha256_bytes(source) != expected:
            raise SourceEvidenceError(f"installed {relative} hash changed")
        expected_hashes[relative] = expected
        trusted_bytes[relative] = source
    current_script = _require_absolute_canonical_path(
        Path(__file__).absolute(),
        field="source-evidence builder script",
    )
    installed_builder = candidate / THIS_SCRIPT_RELATIVE
    if current_script != installed_builder:
        raise SourceEvidenceError("source-evidence builder must run from the verified installed candidate")
    installer = _execute_verified_module(
        "_verified_webapp_fi_source_adoption_installer_for_evidence",
        candidate / INSTALL_SCRIPT_RELATIVE,
        trusted_bytes[INSTALL_SCRIPT_RELATIVE],
    )
    if (
        getattr(installer, "INSTALL_RECEIPT_SCHEMA", None) != INSTALL_RECEIPT_SCHEMA
        or getattr(installer, "PACKAGE_DESTINATION_SITE", None) != SOURCE_SITE
        or not callable(getattr(installer, "verify_installed_source_adoption", None))
        or not callable(getattr(installer, "_validate_canonical_release_tree_descriptor", None))
    ):
        raise SourceEvidenceError("verified source-adoption installer contract is incompatible")
    try:
        installed = installer.verify_installed_source_adoption(receipt_path)
    except Exception as exc:
        raise SourceEvidenceError("installed source-adoption receipt cannot be verified") from exc
    if not isinstance(installed, Mapping) or installed.get("candidate") != candidate:
        raise SourceEvidenceError("installed source-adoption receipt changed while being verified")
    binding = _execute_verified_module(
        "_verified_webapp_fi_source_campaign_binding_for_evidence",
        candidate / CAMPAIGN_BINDING_SCRIPT_RELATIVE,
        trusted_bytes[CAMPAIGN_BINDING_SCRIPT_RELATIVE],
    )
    if (
        getattr(binding, "CAMPAIGN_BINDING_SCHEMA", None) != CAMPAIGN_BINDING_SCHEMA
        or not callable(getattr(binding, "load_campaign_binding", None))
        or not callable(getattr(binding, "build_campaign_binding", None))
    ):
        raise SourceEvidenceError("verified campaign binding helper contract is incompatible")
    provenance = _execute_verified_module(
        "_verified_webapp_fi_source_provenance_for_evidence",
        candidate / PROVENANCE_VERIFIER_SCRIPT_RELATIVE,
        trusted_bytes[PROVENANCE_VERIFIER_SCRIPT_RELATIVE],
    )
    if (
        getattr(provenance, "ATTESTATION_SCHEMA", None) != ATTESTATION_SCHEMA
        or getattr(provenance, "IMAGE_EXPORT_RECEIPT_SCHEMA", None) != IMAGE_EXPORT_RECEIPT_SCHEMA
        or not callable(getattr(provenance, "verify_source_role_attestation_payload", None))
        or not callable(getattr(provenance, "verify_image_export_receipt_payload", None))
    ):
        raise SourceEvidenceError("verified source provenance verifier contract is incompatible")
    image_contract = getattr(provenance, "image_contract", None)
    loaded_contract_text = getattr(image_contract, "__file__", None)
    expected_contract = candidate / IMAGE_ARCHIVE_CONTRACT_SCRIPT_RELATIVE
    if not isinstance(loaded_contract_text, str) or Path(loaded_contract_text).absolute() != expected_contract:
        raise SourceEvidenceError("verified source provenance verifier did not load its co-shipped image contract")
    loaded_contract = _read_root_private_file(
        expected_contract,
        field="verified provenance image archive contract",
        maximum_bytes=MAX_INSTALLED_SOURCE_BYTES,
    )
    if sha256_bytes(loaded_contract) != expected_hashes[IMAGE_ARCHIVE_CONTRACT_SCRIPT_RELATIVE]:
        raise SourceEvidenceError("verified provenance image archive contract hash changed")
    return installer, binding, provenance, dict(installed)


def _campaign_binding_path(campaign_id: str) -> Path:
    campaign = _require_campaign_id(campaign_id, field="campaign_id")
    root = _require_root_private_directory(CAMPAIGN_ROOT, field="campaign root")
    directory = _require_root_private_directory(root / campaign, field="campaign directory")
    source_phase = _require_root_private_directory(directory / SOURCE_PHASE_DIRECTORY, field="campaign source-phase directory")
    return source_phase / CAMPAIGN_BINDING_FILENAME


def _source_signer_key_path(campaign_id: str) -> Path:
    campaign = _require_campaign_id(campaign_id, field="campaign_id")
    root = _require_root_private_directory(CAMPAIGN_ROOT, field="campaign root")
    directory = _require_root_private_directory(root / campaign, field="campaign directory")
    signer_directory = _require_root_private_directory(
        directory / FI_SOURCE_SIGNER_DIRECTORY,
        field="WebApp-FI source signer directory",
    )
    return signer_directory / FI_SOURCE_SIGNER_KEY_NAME


def _attestation_path(*, candidate: Path, attestation_id: str) -> Path:
    identifier = _require_identifier(attestation_id, field="attestation_id")
    directory = _require_root_private_directory(candidate / ATTESTATION_DIRECTORY, field="installed source attestation directory")
    return directory / f"{identifier}.json"


def _image_export_receipt_path(*, campaign_id: str, export_id: str) -> Path:
    campaign = _require_campaign_id(campaign_id, field="campaign_id")
    identifier = _require_identifier(export_id, field="export_id")
    root = _require_root_private_directory(FI_SOURCE_EXPORT_ROOT, field="WebApp-FI source export root")
    campaign_directory = _require_root_private_directory(root / campaign, field="WebApp-FI source export campaign directory")
    export_directory = _require_root_private_directory(
        campaign_directory / identifier,
        field="WebApp-FI source export directory",
    )
    return export_directory / IMAGE_EXPORT_RECEIPT_NAME


def source_evidence_path(*, campaign_id: str, evidence_id: str) -> Path:
    """Return the only candidate output path for a campaign evidence ID."""

    campaign = _require_campaign_id(campaign_id, field="campaign_id")
    identifier = _require_identifier(evidence_id, field="evidence_id")
    root = _require_root_private_directory(FI_SOURCE_EVIDENCE_ROOT, field="WebApp-FI source evidence root")
    return root / campaign / identifier / SOURCE_EVIDENCE_FILENAME


def _load_source_signer(private_key_path: Path) -> tuple[Any, str, str]:
    raw = _read_root_private_file(
        private_key_path,
        field="WebApp-FI source signing private key",
        maximum_bytes=32,
    )
    if len(raw) != 32:
        raise SourceEvidenceError("WebApp-FI source signing private key has an unsafe length")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise SourceEvidenceError("cryptography Ed25519 support is unavailable") from exc
    try:
        signer = Ed25519PrivateKey.from_private_bytes(raw)
    except ValueError as exc:
        raise SourceEvidenceError("WebApp-FI source signing private key is invalid") from exc
    public = signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    public_base64 = base64.b64encode(public).decode("ascii")
    return signer, public_base64, public_key_id(public_base64)


def _load_binding(
    *,
    binding_module: Any,
    campaign_id: str,
) -> tuple[bytes, dict[str, Any], Any]:
    path = _campaign_binding_path(campaign_id)
    raw = _read_root_private_file(path, field="campaign binding", maximum_bytes=MAX_BINDING_BYTES)
    value = _parse_canonical_json(raw, field="campaign binding", maximum_bytes=MAX_BINDING_BYTES)
    try:
        loaded = binding_module.load_campaign_binding(path)
    except Exception as exc:
        raise SourceEvidenceError("campaign binding cannot be verified") from exc
    if getattr(loaded, "campaign_id", None) != campaign_id:
        raise SourceEvidenceError("campaign binding is not bound to the installed campaign")
    try:
        expected = binding_module.build_campaign_binding(
            campaign_id=loaded.campaign_id,
            application_release_sha=loaded.application_release_sha,
            application_release_tree=loaded.application_release_tree,
            expected_alembic_revision=loaded.expected_alembic_revision,
            control_commit=loaded.control_commit,
            control_tree=loaded.control_tree,
        )
    except Exception as exc:
        raise SourceEvidenceError("campaign binding semantic reconstruction failed") from exc
    if value != expected or raw != canonical_json_bytes(expected) + b"\n":
        raise SourceEvidenceError("campaign binding raw canonical payload and semantic value disagree")
    return raw, value, loaded


def _validate_installed_binding(
    *,
    installer: Any,
    installed: Mapping[str, Any],
    binding: Any,
) -> None:
    expected_application = {
        "release_sha": binding.application_release_sha,
        "expected_alembic_revision": binding.expected_alembic_revision,
    }
    expected_tooling = {
        "control_commit": binding.control_commit,
        "control_tree": binding.control_tree,
    }
    if (
        installed.get("campaign_id") != binding.campaign_id
        or installed.get("application") != expected_application
        or installed.get("tooling") != expected_tooling
    ):
        raise SourceEvidenceError("installed source-adoption candidate does not match the campaign binding")
    candidate = installed.get("candidate")
    if not isinstance(candidate, Path):
        raise SourceEvidenceError("installed source-adoption candidate is invalid")
    files = installed.get("files")
    if not isinstance(files, Mapping):
        raise SourceEvidenceError("installed source-adoption candidate file hashes are unavailable")
    expected_descriptor_sha = _require_sha256(
        files.get(CANONICAL_RELEASE_TREE_RELATIVE),
        field="installed canonical release descriptor hash",
    )
    descriptor_raw = _read_root_private_file(
        candidate / CANONICAL_RELEASE_TREE_RELATIVE,
        field="installed canonical release descriptor",
        maximum_bytes=MAX_INSTALLED_SOURCE_BYTES,
    )
    if sha256_bytes(descriptor_raw) != expected_descriptor_sha:
        raise SourceEvidenceError("installed canonical release descriptor hash changed")
    try:
        descriptor = installer._validate_canonical_release_tree_descriptor(descriptor_raw)
    except Exception as exc:
        raise SourceEvidenceError("installed canonical release descriptor cannot be verified") from exc
    if (
        installed.get("canonical_release_tree_sha256") != expected_descriptor_sha
        or descriptor.get("application") != {
            "release_sha": binding.application_release_sha,
            "git_tree": binding.application_release_tree,
        }
    ):
        raise SourceEvidenceError("installed canonical release descriptor does not match the campaign binding")


def _extract_attested_image(value: Mapping[str, Any]) -> tuple[str, str]:
    image = value.get("active_application_image")
    if not isinstance(image, Mapping) or set(image) != {"image_id", "image_reference", "repo_tags", "repo_digests"}:
        raise SourceEvidenceError("source role attestation active image is invalid")
    image_id = image.get("image_id")
    image_reference = image.get("image_reference")
    if not isinstance(image_id, str) or not isinstance(image_reference, str):
        raise SourceEvidenceError("source role attestation active image is invalid")
    return image_id, image_reference


def _verify_proofs(
    *,
    provenance: Any,
    installed: Mapping[str, Any],
    binding: Any,
    source_public_key_base64: str,
    source_key_id: str,
    attestation_raw: bytes,
    image_export_raw: bytes,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    attestation_value = _parse_canonical_json(
        attestation_raw,
        field="source role attestation",
        maximum_bytes=MAX_PROOF_BYTES,
    )
    image_id, image_reference = _extract_attested_image(attestation_value)
    verification_time = _require_timestamp(utc_now(), field="proof verification time")
    expected_application = {
        "release_sha": binding.application_release_sha,
        "expected_alembic_revision": binding.expected_alembic_revision,
    }
    try:
        attestation = provenance.verify_source_role_attestation_payload(
            payload=attestation_raw,
            pinned_source_signing_public_key_base64=source_public_key_base64,
            expected_campaign_id=binding.campaign_id,
            expected_application=expected_application,
            expected_control_commit=binding.control_commit,
            expected_canonical_release_tree_sha256=installed["canonical_release_tree_sha256"],
            expected_app_image_id=image_id,
            expected_app_image_reference=image_reference,
            verification_time=verification_time,
        )
    except Exception as exc:
        raise SourceEvidenceError("source role attestation cannot be verified") from exc
    expected_tooling = {"control_commit": binding.control_commit, "control_tree": binding.control_tree}
    if (
        attestation.get("application") != expected_application
        or attestation.get("tooling") != expected_tooling
        or attestation.get("descriptor_claim", {}).get("application_release_tree") != binding.application_release_tree
        or attestation.get("source_adoption_install_receipt_sha256") != installed.get("receipt_sha256")
        or attestation.get("source_signing_public_key_base64") != source_public_key_base64
        or attestation.get("source_signing_key_id") != source_key_id
    ):
        raise SourceEvidenceError("source role attestation does not share the installed campaign pins")
    image_export_value = _parse_canonical_json(
        image_export_raw,
        field="image export receipt",
        maximum_bytes=MAX_PROOF_BYTES,
    )
    try:
        image_export = provenance.verify_image_export_receipt_payload(
            payload=image_export_raw,
            pinned_source_signing_public_key_base64=source_public_key_base64,
            expected_campaign_id=binding.campaign_id,
            expected_application=expected_application,
            expected_control_commit=binding.control_commit,
            expected_application_release_tree=binding.application_release_tree,
            expected_canonical_release_tree_sha256=installed["canonical_release_tree_sha256"],
            expected_attestation_sha256=attestation["attestation_sha256"],
            expected_app_image_id=attestation["image_claim"]["image_id"],
            expected_app_image_reference=attestation["image_claim"]["image_reference"],
            verification_time=verification_time,
        )
    except Exception as exc:
        raise SourceEvidenceError("image export receipt cannot be verified") from exc
    if (
        image_export.get("application") != expected_application
        or image_export.get("tooling") != expected_tooling
        or image_export.get("source_role_attestation_sha256") != attestation["attestation_sha256"]
        or image_export.get("source_signing_public_key_base64") != source_public_key_base64
        or image_export.get("source_signing_key_id") != source_key_id
    ):
        raise SourceEvidenceError("image export receipt does not share the source role campaign pins")
    return attestation_value, image_export_value, attestation, image_export


def _proof_wrapper(value: Mapping[str, Any], raw: bytes) -> dict[str, Any]:
    canonical = canonical_json_bytes(value) + b"\n"
    if canonical != raw:
        raise SourceEvidenceError("proof raw payload is not canonical")
    return {"payload": dict(value), "payload_sha256": sha256_bytes(raw)}


def _sign_envelope(unsigned: Mapping[str, Any], signer: Any) -> dict[str, str]:
    signature = signer.sign(SOURCE_EVIDENCE_SIGNATURE_DOMAIN + canonical_json_bytes(unsigned))
    if len(signature) != 64:  # pragma: no cover - Ed25519 invariant.
        raise SourceEvidenceError("source evidence signature has an unsafe length")
    return {"algorithm": "ed25519", "signature_base64": base64.b64encode(signature).decode("ascii")}


def _embedded_payload(
    value: object,
    *,
    field: str,
) -> tuple[dict[str, Any], bytes, str]:
    if not isinstance(value, Mapping) or set(value) != {"payload", "payload_sha256"}:
        raise SourceEvidenceError(f"{field} wrapper is invalid")
    payload = value.get("payload")
    if not isinstance(payload, Mapping):
        raise SourceEvidenceError(f"{field} payload is invalid")
    raw = canonical_json_bytes(dict(payload)) + b"\n"
    digest = _require_sha256(value.get("payload_sha256"), field=f"{field} payload hash")
    if sha256_bytes(raw) != digest:
        raise SourceEvidenceError(f"{field} raw canonical payload checksum is invalid")
    if b"://" in raw.lower() or b"presigned" in raw.lower() or b'"url"' in raw.lower():
        raise SourceEvidenceError(f"{field} persists a forbidden URL")
    return dict(payload), raw, digest


def _validate_binding_value(value: Mapping[str, Any]) -> tuple[str, dict[str, str], dict[str, str]]:
    expected = {"schema", "status", "campaign_id", "application", "tooling", "binding_sha256"}
    if set(value) != expected or value.get("schema") != CAMPAIGN_BINDING_SCHEMA or value.get("status") != "bound":
        raise SourceEvidenceError("embedded campaign binding schema is unsupported")
    campaign = _require_campaign_id(value.get("campaign_id"), field="embedded campaign binding campaign_id")
    application = _require_application(value.get("application"), include_tree=True, field="embedded campaign binding application")
    tooling = _require_tooling(value.get("tooling"), field="embedded campaign binding tooling")
    unsigned = {
        "schema": CAMPAIGN_BINDING_SCHEMA,
        "status": "bound",
        "campaign_id": campaign,
        "application": application,
        "tooling": tooling,
    }
    if _require_sha256(value.get("binding_sha256"), field="embedded campaign binding checksum") != sha256_bytes(canonical_json_bytes(unsigned)):
        raise SourceEvidenceError("embedded campaign binding checksum is invalid")
    return campaign, application, tooling


def _verify_outer_signature(
    *,
    unsigned: Mapping[str, Any],
    signature: object,
    public_key_base64: str,
) -> None:
    if (
        not isinstance(signature, Mapping)
        or set(signature) != {"algorithm", "signature_base64"}
        or signature.get("algorithm") != "ed25519"
        or not isinstance(signature.get("signature_base64"), str)
    ):
        raise SourceEvidenceError("source evidence signature is invalid")
    try:
        raw_signature = base64.b64decode(signature["signature_base64"].encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise SourceEvidenceError("source evidence signature is invalid") from exc
    if len(raw_signature) != 64:
        raise SourceEvidenceError("source evidence signature is invalid")
    _, raw_public = _decode_public_key(public_key_base64, field="source evidence public key")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise SourceEvidenceError("cryptography Ed25519 support is unavailable") from exc
    try:
        Ed25519PublicKey.from_public_bytes(raw_public).verify(
            raw_signature,
            SOURCE_EVIDENCE_SIGNATURE_DOMAIN + canonical_json_bytes(unsigned),
        )
    except InvalidSignature as exc:
        raise SourceEvidenceError("source evidence signature verification failed") from exc


def verify_source_evidence_envelope_payload(
    *,
    payload: bytes,
    expected_campaign_binding_payload: bytes,
    pinned_source_signing_public_key_base64: str,
    verification_time: str | None = None,
) -> dict[str, Any]:
    """Verify the outer canonical evidence binding without touching the filesystem.

    A controller later additionally invokes the co-shipped portable proof
    verifier on the returned proof payloads.  This function verifies the
    strict outer schema, source signature, proof hashes, and both raw and
    semantic equality of the embedded binding with the canonical root-only
    binding supplied by its caller.
    """

    value = _parse_canonical_json(
        payload,
        field="source evidence envelope",
        maximum_bytes=MAX_ENVELOPE_BYTES,
    )
    expected = {
        "schema", "status", "created_at", "campaign_binding", "application", "tooling",
        "transport", "source_signer", "proofs", "source_signature",
    }
    if (
        set(value) != expected
        or value.get("schema") != SOURCE_EVIDENCE_ENVELOPE_SCHEMA
        or value.get("status") != "sealed"
    ):
        raise SourceEvidenceError("source evidence envelope schema is unsupported")
    created_at = _require_timestamp(value.get("created_at"), field="source evidence timestamp")
    if verification_time is not None:
        now_text = _require_timestamp(verification_time, field="source evidence verification time")
        observed = dt.datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        now = dt.datetime.strptime(now_text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        if observed > now:
            raise SourceEvidenceError("source evidence timestamp is from the future")
    expected_binding_value = _parse_canonical_json(
        expected_campaign_binding_payload,
        field="expected campaign binding",
        maximum_bytes=MAX_BINDING_BYTES,
    )
    expected_campaign, expected_application, expected_tooling = _validate_binding_value(expected_binding_value)
    embedded_binding, embedded_binding_raw, _ = _embedded_payload(
        value.get("campaign_binding"),
        field="source evidence campaign binding",
    )
    campaign, binding_application, binding_tooling = _validate_binding_value(embedded_binding)
    if (
        embedded_binding_raw != expected_campaign_binding_payload
        or embedded_binding != expected_binding_value
        or campaign != expected_campaign
        or binding_application != expected_application
        or binding_tooling != expected_tooling
    ):
        raise SourceEvidenceError("source evidence campaign binding does not exactly match the canonical root-only binding")
    application = _require_application(value.get("application"), include_tree=True, field="source evidence application")
    tooling = _require_tooling(value.get("tooling"), field="source evidence tooling")
    if application != expected_application or tooling != expected_tooling:
        raise SourceEvidenceError("source evidence release or tooling pins do not match the campaign binding")
    transport = value.get("transport")
    if not isinstance(transport, Mapping) or set(transport) != {
        "source_site", "destination_site", "object_kind", "recipient_mode", "evidence_id",
    }:
        raise SourceEvidenceError("source evidence transport is invalid")
    evidence_id = _require_identifier(transport.get("evidence_id"), field="source evidence ID")
    if (
        transport.get("source_site") != SOURCE_SITE
        or transport.get("destination_site") != DESTINATION_SITE
        or transport.get("object_kind") != OBJECT_KIND
        or transport.get("recipient_mode") != RECIPIENT_MODE
    ):
        raise SourceEvidenceError("source evidence transport is invalid")
    source_signer = value.get("source_signer")
    if not isinstance(source_signer, Mapping) or set(source_signer) != {"public_key_base64", "key_id"}:
        raise SourceEvidenceError("source evidence signer is invalid")
    public_key_base64 = source_signer.get("public_key_base64")
    _decode_public_key(public_key_base64, field="source evidence signer public key")
    if (
        public_key_base64 != pinned_source_signing_public_key_base64
        or source_signer.get("key_id") != public_key_id(pinned_source_signing_public_key_base64)
    ):
        raise SourceEvidenceError("source evidence signer is not pinned")
    proofs = value.get("proofs")
    if not isinstance(proofs, Mapping) or set(proofs) != {"role_attestation", "image_export_receipt"}:
        raise SourceEvidenceError("source evidence proofs are invalid")
    role_payload, role_raw, role_sha = _embedded_payload(
        proofs.get("role_attestation"),
        field="source role attestation",
    )
    image_payload, image_raw, image_sha = _embedded_payload(
        proofs.get("image_export_receipt"),
        field="image export receipt",
    )
    if (
        role_payload.get("schema") != ATTESTATION_SCHEMA
        or role_payload.get("status") != "attested"
        or role_payload.get("campaign_id") != expected_campaign
        or role_payload.get("application") != {
            "release_sha": expected_application["release_sha"],
            "expected_alembic_revision": expected_application["expected_alembic_revision"],
        }
        or role_payload.get("application_release_tree") != expected_application["release_tree"]
        or role_payload.get("tooling") != expected_tooling
        or role_payload.get("source_signing_public_key_base64") != public_key_base64
        or role_payload.get("source_signing_key_id") != source_signer["key_id"]
    ):
        raise SourceEvidenceError("source role attestation does not match the outer campaign pins")
    if (
        image_payload.get("schema") != IMAGE_EXPORT_RECEIPT_SCHEMA
        or image_payload.get("status") != "exported"
        or image_payload.get("campaign_id") != expected_campaign
        or image_payload.get("application") != {
            "release_sha": expected_application["release_sha"],
            "expected_alembic_revision": expected_application["expected_alembic_revision"],
        }
        or image_payload.get("application_release_tree") != expected_application["release_tree"]
        or image_payload.get("tooling") != expected_tooling
        or image_payload.get("source_role_attestation_sha256") != role_sha
        or image_payload.get("source_signing_public_key_base64") != public_key_base64
        or image_payload.get("source_signing_key_id") != source_signer["key_id"]
    ):
        raise SourceEvidenceError("image export receipt does not match the outer campaign pins")
    _verify_outer_signature(
        unsigned={key: item for key, item in value.items() if key != "source_signature"},
        signature=value.get("source_signature"),
        public_key_base64=pinned_source_signing_public_key_base64,
    )
    return {
        "status": "verified",
        "campaign_id": expected_campaign,
        "evidence_id": evidence_id,
        "application": application,
        "tooling": tooling,
        "source_signing_public_key_base64": public_key_base64,
        "source_signing_key_id": source_signer["key_id"],
        "role_attestation_sha256": role_sha,
        "image_export_receipt_sha256": image_sha,
        "role_attestation_payload": role_raw,
        "image_export_receipt_payload": image_raw,
    }


def build_source_evidence(
    *,
    install_receipt: Path,
    attestation_id: str,
    export_id: str,
    evidence_id: str,
    apply: bool,
) -> dict[str, Any]:
    """Plan or seal one campaign-derived FI source-evidence envelope."""

    _require_root_execution()
    attestation_id = _require_identifier(attestation_id, field="attestation_id")
    export_id = _require_identifier(export_id, field="export_id")
    evidence_id = _require_identifier(evidence_id, field="evidence_id")
    installer, binding_module, provenance, installed = _load_verified_installed_adoption(Path(install_receipt))
    campaign_id = _require_campaign_id(installed.get("campaign_id"), field="installed campaign_id")
    binding_raw, binding_value, binding = _load_binding(
        binding_module=binding_module,
        campaign_id=campaign_id,
    )
    _validate_installed_binding(installer=installer, installed=installed, binding=binding)
    signer, source_public, source_key_id = _load_source_signer(_source_signer_key_path(campaign_id))
    candidate = installed.get("candidate")
    if not isinstance(candidate, Path):
        raise SourceEvidenceError("installed source-adoption candidate is invalid")
    attestation_raw = _read_root_private_file(
        _attestation_path(candidate=candidate, attestation_id=attestation_id),
        field="source role attestation",
        maximum_bytes=MAX_PROOF_BYTES,
    )
    image_export_raw = _read_root_private_file(
        _image_export_receipt_path(campaign_id=campaign_id, export_id=export_id),
        field="image export receipt",
        maximum_bytes=MAX_PROOF_BYTES,
    )
    attestation_value, image_export_value, attestation, image_export = _verify_proofs(
        provenance=provenance,
        installed=installed,
        binding=binding,
        source_public_key_base64=source_public,
        source_key_id=source_key_id,
        attestation_raw=attestation_raw,
        image_export_raw=image_export_raw,
    )
    output = source_evidence_path(campaign_id=campaign_id, evidence_id=evidence_id)
    output_directory = output.parent
    _require_absent(output_directory, field="source evidence directory")
    plan = {
        "schema": SOURCE_EVIDENCE_ENVELOPE_SCHEMA,
        "status": "planned" if not apply else "sealing",
        "campaign_id": campaign_id,
        "evidence_id": evidence_id,
        "output_path": str(output),
        "source_signing_public_key_base64": source_public,
        "source_signing_key_id": source_key_id,
        "campaign_binding_sha256": sha256_bytes(binding_raw),
        "role_attestation_sha256": attestation["attestation_sha256"],
        "image_export_receipt_sha256": image_export["image_export_receipt_sha256"],
        "object_storage_changed": False,
        "ssh_changed": False,
        "docker_changed": False,
        "service_changed": False,
        "current_changed": False,
        "container_changed": False,
        "volume_changed": False,
        "application_data_changed": False,
    }
    if not apply:
        return plan
    root = _require_root_private_directory(FI_SOURCE_EVIDENCE_ROOT, field="WebApp-FI source evidence root")
    campaign_directory = _create_or_require_root_private_directory(
        root,
        campaign_id,
        field="WebApp-FI source evidence campaign directory",
    )
    output_directory = _create_new_root_private_directory(
        campaign_directory,
        evidence_id,
        field="WebApp-FI source evidence directory",
    )
    output = output_directory / SOURCE_EVIDENCE_FILENAME
    created_at = _require_timestamp(utc_now(), field="source evidence timestamp")
    unsigned: dict[str, Any] = {
        "schema": SOURCE_EVIDENCE_ENVELOPE_SCHEMA,
        "status": "sealed",
        "created_at": created_at,
        "campaign_binding": _proof_wrapper(binding_value, binding_raw),
        "application": {
            "release_sha": binding.application_release_sha,
            "release_tree": binding.application_release_tree,
            "expected_alembic_revision": binding.expected_alembic_revision,
        },
        "tooling": {
            "control_commit": binding.control_commit,
            "control_tree": binding.control_tree,
        },
        "transport": {
            "source_site": SOURCE_SITE,
            "destination_site": DESTINATION_SITE,
            "object_kind": OBJECT_KIND,
            "recipient_mode": RECIPIENT_MODE,
            "evidence_id": evidence_id,
        },
        "source_signer": {"public_key_base64": source_public, "key_id": source_key_id},
        "proofs": {
            "role_attestation": _proof_wrapper(attestation_value, attestation_raw),
            "image_export_receipt": _proof_wrapper(image_export_value, image_export_raw),
        },
    }
    envelope = {**unsigned, "source_signature": _sign_envelope(unsigned, signer)}
    encoded = canonical_json_bytes(envelope) + b"\n"
    verify_source_evidence_envelope_payload(
        payload=encoded,
        expected_campaign_binding_payload=binding_raw,
        pinned_source_signing_public_key_base64=source_public,
        verification_time=created_at,
    )
    _write_new_root_private_file(output, encoded, field="source evidence envelope")
    _fsync_root_private_directory(output_directory, field="WebApp-FI source evidence directory")
    read_back = _read_root_private_file(
        output,
        field="source evidence envelope",
        maximum_bytes=MAX_ENVELOPE_BYTES,
    )
    if read_back != encoded:
        raise SourceEvidenceError("source evidence envelope changed after creation")
    verify_source_evidence_envelope_payload(
        payload=read_back,
        expected_campaign_binding_payload=binding_raw,
        pinned_source_signing_public_key_base64=source_public,
        verification_time=created_at,
    )
    return {
        **plan,
        "status": "sealed",
        "output_path": str(output),
        "envelope_sha256": sha256_bytes(encoded),
        "envelope_bytes": len(encoded),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-receipt", required=True, type=Path)
    parser.add_argument("--attestation-id", required=True)
    parser.add_argument("--export-id", required=True)
    parser.add_argument("--evidence-id", required=True)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = build_source_evidence(
            install_receipt=args.install_receipt,
            attestation_id=args.attestation_id,
            export_id=args.export_id,
            evidence_id=args.evidence_id,
            apply=args.apply,
        )
    except SourceEvidenceError as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "error_class": exc.__class__.__name__},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
