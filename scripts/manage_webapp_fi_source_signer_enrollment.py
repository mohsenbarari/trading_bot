#!/usr/bin/env python3
"""Issue one controller-bound WebApp-FI source-signer enrollment certificate.

The controller validates its own root-only prepared package and receipt plus
opaque, non-secret FI control receipts captured through a pinned SSH session.
FI candidate paths are strings to compare, never filesystem paths to open.
This command has no SSH, Object Storage, Docker, service, current, volume, or
application-data action.
"""

from __future__ import annotations

import argparse
import base64
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


SIGNER_ENROLLMENT_CERTIFICATE_SCHEMA = "gold-trade-webapp-fi-source-signer-enrollment-certificate-v2"
SIGNER_ENROLLMENT_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-source-signer-enrollment-v2\x00"
MAX_ENROLLMENT_CERTIFICATE_LIFETIME_SECONDS = 60 * 60
MAX_ISSUANCE_CLOCK_SKEW_SECONDS = 60
MAX_BOOTSTRAP_SIGNER_RECEIPT_AGE_SECONDS = 15 * 60

_INSTALL_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-adoption-install-receipt-v1"
_BOOTSTRAP_SIGNER_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-signer-bootstrap-receipt-v1"
_CAMPAIGN_BINDING_SCHEMA = "gold-trade-webapp-fi-source-campaign-binding-v1"
_CAMPAIGN_BINDING_DIRECTORY = "webapp-fi-source"
_CAMPAIGN_BINDING_FILENAME = "campaign-binding.json"
_PREPARATION_RECEIPT_NAME = "source-adoption-preparation-receipt.json"
_INSTALL_SCRIPT_RELATIVE = "scripts/install_webapp_fi_source_adoption.py"
_FI_SOURCE_SIGNER_DIRECTORY = "webapp-fi"
_FI_SOURCE_SIGNER_KEY_NAME = "source-signing-ed25519.raw"
FI_SOURCE_SIGNER_CAMPAIGN_ROOT = Path("/etc/trading-bot-three-site/campaigns")

_MAX_CONTROL_BYTES = 8 * 1024 * 1024
_MAX_BOOTSTRAP_SIGNER_RECEIPT_BYTES = 64 * 1024
_MAX_CAMPAIGN_BINDING_BYTES = 16 * 1024
_MAX_SSH_HOST_PUBLIC_KEY_BYTES = 64 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ALEMBIC_RE = re.compile(r"^[0-9a-f]{12}$")
_CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
_UTC_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


class SourceSignerEnrollmentIssuerError(RuntimeError):
    """The controller lacks a safe, exact source binding."""


def _canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceSignerEnrollmentIssuerError("control input contains duplicate JSON keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise SourceSignerEnrollmentIssuerError("control input contains an unsupported JSON constant")


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise SourceSignerEnrollmentIssuerError("source signer enrollment issuance must run as root")


def _require_absolute_canonical_path(path: Path, *, field: str) -> Path:
    candidate = Path(path)
    if (
        "\x00" in str(candidate)
        or not candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts[1:])
        or str(candidate) != os.path.normpath(str(candidate))
    ):
        raise SourceSignerEnrollmentIssuerError(f"{field} must be one canonical absolute path")
    return candidate


def _require_root_controlled_ancestors(path: Path, *, field: str) -> None:
    path = _require_absolute_canonical_path(path, field=field)
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise SourceSignerEnrollmentIssuerError(f"cannot inspect {field} parent") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or ((mode & 0o022) and not (metadata.st_mode & stat.S_ISVTX))
        ):
            raise SourceSignerEnrollmentIssuerError(f"{field} parent is unsafe")


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


def _safe_file_metadata(metadata: os.stat_result, *, private: bool, maximum_bytes: int) -> bool:
    forbidden_mode = 0o077 if private else 0o022
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == 0
        and not (metadata.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX))
        and not (stat.S_IMODE(metadata.st_mode) & forbidden_mode)
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
    source = _require_absolute_canonical_path(Path(path), field=field)
    _require_root_controlled_ancestors(source.parent, field=field)
    try:
        before = source.lstat()
    except OSError as exc:
        raise SourceSignerEnrollmentIssuerError(f"cannot inspect {field}") from exc
    if not _safe_file_metadata(before, private=private, maximum_bytes=maximum_bytes) or (
        exact_mode is not None and stat.S_IMODE(before.st_mode) != exact_mode
    ):
        raise SourceSignerEnrollmentIssuerError(f"{field} is unsafe")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:  # pragma: no cover - Linux deployment invariant.
        raise SourceSignerEnrollmentIssuerError("secure no-follow file access is unavailable")
    try:
        descriptor = os.open(str(source), os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | no_follow)
    except OSError as exc:
        raise SourceSignerEnrollmentIssuerError(f"cannot securely open {field}") from exc
    try:
        opened = os.fstat(descriptor)
        if not _same_file_metadata(before, opened) or not _safe_file_metadata(
            opened, private=private, maximum_bytes=maximum_bytes
        ) or (exact_mode is not None and stat.S_IMODE(opened.st_mode) != exact_mode):
            raise SourceSignerEnrollmentIssuerError(f"{field} changed while opening")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise SourceSignerEnrollmentIssuerError(f"{field} is too large")
            chunks.append(chunk)
        after_opened = os.fstat(descriptor)
        payload = b"".join(chunks)
        if len(payload) != opened.st_size or not _same_file_metadata(opened, after_opened):
            raise SourceSignerEnrollmentIssuerError(f"{field} changed while reading")
    except OSError as exc:
        raise SourceSignerEnrollmentIssuerError(f"cannot read {field}") from exc
    finally:
        os.close(descriptor)
    try:
        after = source.lstat()
    except OSError as exc:
        raise SourceSignerEnrollmentIssuerError(f"cannot recheck {field}") from exc
    if not _same_file_metadata(before, after):
        raise SourceSignerEnrollmentIssuerError(f"{field} changed while reading")
    return payload


def _read_root_private_control_file(path: Path, *, field: str, maximum_bytes: int) -> tuple[Path, bytes]:
    source = _require_absolute_canonical_path(Path(path), field=field)
    payload = _read_root_controlled_file(
        source, field=field, maximum_bytes=maximum_bytes, private=True, exact_mode=0o600
    )
    try:
        metadata = source.lstat()
    except OSError as exc:  # pragma: no cover - FD-pinned read succeeded above.
        raise SourceSignerEnrollmentIssuerError(f"cannot recheck {field}") from exc
    if metadata.st_nlink != 1:
        raise SourceSignerEnrollmentIssuerError(f"{field} must be one root-only mode 0600 regular non-symlink file")
    return source, payload


def _require_root_only_directory(path: Path, *, field: str) -> Path:
    directory = _require_absolute_canonical_path(Path(path), field=field)
    _require_root_controlled_ancestors(directory.parent, field=field)
    try:
        metadata = directory.lstat()
        resolved = directory.resolve(strict=True)
        target = resolved.lstat()
    except OSError as exc:
        raise SourceSignerEnrollmentIssuerError(f"cannot inspect {field}") from exc
    if (
        resolved != directory
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_ISLNK(target.st_mode)
        or not stat.S_ISDIR(target.st_mode)
        or target.st_uid != 0
        or stat.S_IMODE(target.st_mode) & 0o077
    ):
        raise SourceSignerEnrollmentIssuerError(f"{field} is unsafe")
    return resolved


def _parse_canonical_control_json(payload: bytes, *, field: str, maximum_bytes: int) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= maximum_bytes:
        raise SourceSignerEnrollmentIssuerError(f"{field} has an unsafe size")
    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_strict_object, parse_constant=_reject_json_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceSignerEnrollmentIssuerError(f"{field} is not strict canonical JSON") from exc
    if not isinstance(value, dict) or payload != _canonical_json_bytes(value) + b"\n":
        raise SourceSignerEnrollmentIssuerError(f"{field} is not canonical JSON")
    lowered = payload.lower()
    if b"://" in lowered or b'"url"' in lowered or b"presigned" in lowered:
        raise SourceSignerEnrollmentIssuerError(f"{field} persists a forbidden transient URL")
    return value


def _raise_from_helper(exc: Exception) -> None:
    raise SourceSignerEnrollmentIssuerError(str(exc)) from exc


def _load_verified_local_helper(filename: str, module_name: str) -> tuple[Any, str]:
    """Execute one FD-pinned controller helper, never a helper from FI."""

    if Path(filename).name != filename:
        raise SourceSignerEnrollmentIssuerError("controller helper filename is invalid")
    issuer = _require_absolute_canonical_path(Path(__file__).absolute(), field="issuer script")
    path = issuer.with_name(filename)
    source = _read_root_controlled_file(
        path, field=f"controller helper {filename}", maximum_bytes=_MAX_CONTROL_BYTES, private=False
    )
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = ""
    try:
        exec(compile(source, str(path), "exec"), module.__dict__)
    except BaseException as exc:
        raise SourceSignerEnrollmentIssuerError(f"cannot load controller helper {filename}") from exc
    return module, hashlib.sha256(source).hexdigest()


def _require_timestamp(installer: Any, value: object, *, field: str) -> tuple[str, dt.datetime]:
    if not isinstance(value, str) or not _UTC_TIMESTAMP_RE.fullmatch(value):
        raise SourceSignerEnrollmentIssuerError(f"{field} is invalid")
    try:
        return value, installer._parse_utc_timestamp(value, field=field)
    except Exception as exc:
        _raise_from_helper(exc)
    raise AssertionError("unreachable")  # pragma: no cover


def _load_campaign_binding(path: Path) -> dict[str, Any]:
    binding_path, payload = _read_root_private_control_file(
        Path(path), field="controller source campaign binding", maximum_bytes=_MAX_CAMPAIGN_BINDING_BYTES
    )
    if binding_path.name != _CAMPAIGN_BINDING_FILENAME or binding_path.parent.name != _CAMPAIGN_BINDING_DIRECTORY:
        raise SourceSignerEnrollmentIssuerError("controller source campaign binding path is not canonical")
    value = _parse_canonical_control_json(
        payload, field="controller source campaign binding", maximum_bytes=_MAX_CAMPAIGN_BINDING_BYTES
    )
    expected = {"schema", "status", "campaign_id", "application", "tooling", "binding_sha256"}
    if set(value) != expected or value.get("schema") != _CAMPAIGN_BINDING_SCHEMA or value.get("status") != "bound":
        raise SourceSignerEnrollmentIssuerError("controller source campaign binding is unsupported")
    application = value.get("application")
    tooling = value.get("tooling")
    if (
        not isinstance(application, Mapping)
        or set(application) != {"release_sha", "release_tree", "expected_alembic_revision"}
        or not isinstance(tooling, Mapping)
        or set(tooling) != {"control_commit", "control_tree"}
    ):
        raise SourceSignerEnrollmentIssuerError("controller source campaign binding has an invalid release pin")
    unsigned = {
        "schema": _CAMPAIGN_BINDING_SCHEMA,
        "status": "bound",
        "campaign_id": value.get("campaign_id"),
        "application": {
            "release_sha": application.get("release_sha"),
            "release_tree": application.get("release_tree"),
            "expected_alembic_revision": application.get("expected_alembic_revision"),
        },
        "tooling": {"control_commit": tooling.get("control_commit"), "control_tree": tooling.get("control_tree")},
    }
    checksum = value.get("binding_sha256")
    if not isinstance(checksum, str) or not _SHA256_RE.fullmatch(checksum) or hashlib.sha256(
        _canonical_json_bytes(unsigned)
    ).hexdigest() != checksum:
        raise SourceSignerEnrollmentIssuerError("controller source campaign binding checksum is invalid")
    campaign = unsigned["campaign_id"]
    if not isinstance(campaign, str) or not _CAMPAIGN_ID_RE.fullmatch(campaign):
        raise SourceSignerEnrollmentIssuerError("campaign binding campaign_id is invalid")
    release = unsigned["application"]["release_sha"]
    revision = unsigned["application"]["expected_alembic_revision"]
    commit = unsigned["tooling"]["control_commit"]
    control_tree = unsigned["tooling"]["control_tree"]
    if not isinstance(release, str) or not _GIT_SHA_RE.fullmatch(release) or not isinstance(revision, str) or not _ALEMBIC_RE.fullmatch(revision):
        raise SourceSignerEnrollmentIssuerError("campaign binding application is invalid")
    if not isinstance(commit, str) or not _GIT_SHA_RE.fullmatch(commit) or not isinstance(control_tree, str) or not _GIT_SHA_RE.fullmatch(control_tree):
        raise SourceSignerEnrollmentIssuerError("campaign binding tooling is invalid")
    application_value = {"release_sha": release, "expected_alembic_revision": revision}
    tooling_value = {"control_commit": commit, "control_tree": control_tree}
    release_tree = unsigned["application"]["release_tree"]
    if not isinstance(release_tree, str) or not _GIT_SHA_RE.fullmatch(release_tree):
        raise SourceSignerEnrollmentIssuerError("controller source campaign binding release tree is invalid")
    if binding_path.parent.parent.name != campaign:
        raise SourceSignerEnrollmentIssuerError("controller source campaign binding path is not campaign-bound")
    return {
        "campaign_id": campaign,
        "application": {**application_value, "release_tree": release_tree},
        "tooling": tooling_value,
        "binding_sha256": checksum,
    }


def _load_verified_controller_package(
    *, package_directory: Path, preparation_receipt: Path, binding: Mapping[str, Any]
) -> tuple[Any, dict[str, Any], str]:
    """Verify controller-local bytes and bind the envelope verifier to them."""

    prepare, _prepare_sha = _load_verified_local_helper(
        "prepare_webapp_fi_source_adoption.py", "_verified_controller_source_adoption_prepare"
    )
    for name in ("verify_prepared_source_adoption_package", "_read_archive_members", "_validate_inner_manifest", "_validate_canonical_release_tree_descriptor"):
        if not callable(getattr(prepare, name, None)):
            raise SourceSignerEnrollmentIssuerError("controller source-adoption package verifier is incomplete")
    package = _require_root_only_directory(Path(package_directory), field="controller source-adoption package directory")
    receipt = _require_absolute_canonical_path(Path(preparation_receipt), field="controller source-adoption preparation receipt")
    if receipt != package / _PREPARATION_RECEIPT_NAME:
        raise SourceSignerEnrollmentIssuerError("controller source-adoption preparation receipt is not package-bound")
    try:
        verified = prepare.verify_prepared_source_adoption_package(
            package_directory=package,
            preparation_receipt=receipt,
            expected_control_commit=binding["tooling"]["control_commit"],
            expected_application_release_sha=binding["application"]["release_sha"],
        )
        members = prepare._read_archive_members(Path(verified["archive_path"]))
        manifest = prepare._validate_inner_manifest(members[prepare.PACKAGE_MANIFEST_MEMBER])
        descriptor = prepare._validate_canonical_release_tree_descriptor(members[prepare.CANONICAL_RELEASE_TREE_MEMBER])
    except Exception as exc:
        _raise_from_helper(exc)
    if not isinstance(verified, Mapping) or not isinstance(manifest, Mapping) or not isinstance(descriptor, Mapping):
        raise SourceSignerEnrollmentIssuerError("controller source-adoption package verification is incomplete")
    descriptor_application = descriptor.get("application")
    if verified.get("application") != {
        "release_sha": binding["application"]["release_sha"],
        "expected_alembic_revision": binding["application"]["expected_alembic_revision"],
    } or verified.get("tooling") != binding["tooling"]:
        raise SourceSignerEnrollmentIssuerError("controller source campaign binding is not bound to the controller-local prepared package")
    if not isinstance(descriptor_application, Mapping) or descriptor_application != {
        "release_sha": binding["application"]["release_sha"],
        "git_tree": binding["application"]["release_tree"],
    }:
        raise SourceSignerEnrollmentIssuerError("controller source campaign binding release tree is not bound to the local package")
    files = manifest.get("files")
    if not isinstance(files, Mapping) or not isinstance(files.get(_INSTALL_SCRIPT_RELATIVE), str):
        raise SourceSignerEnrollmentIssuerError("controller source-adoption package has no installer binding")
    installer, installer_sha = _load_verified_local_helper(
        "install_webapp_fi_source_adoption.py", "_verified_controller_source_adoption_installer"
    )
    if installer_sha != files[_INSTALL_SCRIPT_RELATIVE]:
        raise SourceSignerEnrollmentIssuerError("controller source-adoption installer hash does not match the prepared package")
    required = ("_require_campaign_id", "_require_package_id", "_require_application", "_require_tooling", "_require_sha256", "_parse_utc_timestamp", "_public_key_id", "_validate_signed_delivery_envelope")
    if any(not callable(getattr(installer, name, None)) for name in required):
        raise SourceSignerEnrollmentIssuerError("controller source-adoption installer contract is incomplete")
    return installer, {
        "package_directory": verified["package_directory"],
        "package_id": verified["package_id"],
        "application": dict(verified["application"]),
        "tooling": dict(verified["tooling"]),
        "archive_sha256": verified["archive_sha256"],
        "archive_bytes": verified["archive_bytes"],
        "preparation_receipt_sha256": verified["preparation_receipt_sha256"],
        "canonical_release_tree_sha256": verified["canonical_release_tree_sha256"],
        "files": dict(files),
        "descriptor_application": dict(descriptor_application),
    }, installer_sha


def _load_delivery_envelope(
    *, installer: Any, path: Path, controller_public_key_base64: str, binding: Mapping[str, Any], prepared: Mapping[str, Any]
) -> dict[str, Any]:
    try:
        delivery = installer._validate_signed_delivery_envelope(
            envelope=Path(path),
            pinned_controller_public_key_base64=controller_public_key_base64,
            expected_control_commit=prepared["tooling"]["control_commit"],
            expected_application=prepared["application"],
            expected_descriptor_sha256=prepared["canonical_release_tree_sha256"],
        )
    except Exception as exc:
        _raise_from_helper(exc)
    if (
        not isinstance(delivery, Mapping)
        or delivery.get("campaign_id") != binding["campaign_id"]
        or delivery.get("package_id") != prepared["package_id"]
        or delivery.get("application") != prepared["application"]
        or delivery.get("tooling") != prepared["tooling"]
        or delivery.get("controller_public_key_base64") != controller_public_key_base64
        or not isinstance(delivery.get("recipient"), str)
        or not isinstance(delivery.get("object"), Mapping)
    ):
        raise SourceSignerEnrollmentIssuerError("delivery envelope is not bound to the controller-local prepared package")
    object_value = dict(delivery["object"])
    if object_value.get("plaintext_sha256") != prepared["archive_sha256"] or object_value.get("plaintext_bytes") != prepared["archive_bytes"]:
        raise SourceSignerEnrollmentIssuerError("delivery envelope plaintext is not the prepared package archive")
    return {
        "campaign_id": delivery["campaign_id"],
        "package_id": delivery["package_id"],
        "application": dict(delivery["application"]),
        "tooling": dict(delivery["tooling"]),
        "fi_bootstrap_recipient": delivery["recipient"],
        "object": object_value,
        "sha256": delivery["sha256"],
    }


def _load_opaque_fi_install_control_receipt(
    *, installer: Any, path: Path, prepared: Mapping[str, Any], delivery: Mapping[str, Any], controller_public_key_base64: str, campaign_id: str
) -> dict[str, Any]:
    """Validate FI control output without opening its candidate directory."""

    _path, payload = _read_root_private_control_file(
        Path(path), field="opaque FI source-adoption install control receipt", maximum_bytes=_MAX_CONTROL_BYTES
    )
    value = _parse_canonical_control_json(
        payload, field="opaque FI source-adoption install control receipt", maximum_bytes=_MAX_CONTROL_BYTES
    )
    expected = {"schema", "status", "installed_at", "candidate_directory", "source_site", "destination_site", "campaign_id", "package_id", "application", "tooling", "files", "canonical_release_tree_sha256", "package", "receipt_sha256"}
    if set(value) != expected or value.get("schema") != _INSTALL_RECEIPT_SCHEMA or value.get("status") != "installed":
        raise SourceSignerEnrollmentIssuerError("opaque FI source-adoption install control receipt is unsupported")
    unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
    receipt_sha = value.get("receipt_sha256")
    if not isinstance(receipt_sha, str) or not _SHA256_RE.fullmatch(receipt_sha) or hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest() != receipt_sha:
        raise SourceSignerEnrollmentIssuerError("opaque FI source-adoption install control receipt checksum is invalid")
    installed_at, installed_time = _require_timestamp(installer, value.get("installed_at"), field="opaque FI install receipt installed_at")
    if installed_time > _controller_utc_now() + dt.timedelta(seconds=MAX_ISSUANCE_CLOCK_SKEW_SECONDS):
        raise SourceSignerEnrollmentIssuerError("opaque FI source-adoption install control receipt is from the future")
    candidate_text = value.get("candidate_directory")
    if not isinstance(candidate_text, str):
        raise SourceSignerEnrollmentIssuerError("opaque FI source-adoption install control receipt candidate is invalid")
    candidate = _require_absolute_canonical_path(Path(candidate_text), field="opaque FI install candidate claim")
    try:
        package_id = installer._require_package_id(value.get("package_id"), field="opaque FI install receipt package_id")
        application = installer._require_application(value.get("application"), field="opaque FI install receipt application")
        tooling = installer._require_tooling(value.get("tooling"), field="opaque FI install receipt tooling")
        descriptor_sha = installer._require_sha256(value.get("canonical_release_tree_sha256"), field="opaque FI install receipt descriptor sha256")
    except Exception as exc:
        _raise_from_helper(exc)
    if (
        value.get("source_site") != "bot_fi"
        or value.get("destination_site") != "webapp_fi"
        or value.get("campaign_id") != campaign_id
        or candidate.name != f"installed-{tooling['control_commit']}-{package_id}"
        or package_id != prepared["package_id"]
        or application != prepared["application"]
        or tooling != prepared["tooling"]
        or descriptor_sha != prepared["canonical_release_tree_sha256"]
    ):
        raise SourceSignerEnrollmentIssuerError("opaque FI source-adoption install control receipt is not bound to the local package")
    files = value.get("files")
    if not isinstance(files, Mapping) or dict(files) != prepared["files"]:
        raise SourceSignerEnrollmentIssuerError("opaque FI source-adoption install control receipt files do not match the local package")
    package = value.get("package")
    expected_package = {"archive_sha256", "archive_bytes", "preparation_receipt_sha256", "delivery_receipt_sha256", "delivery_envelope_sha256", "controller_public_key_base64", "fi_bootstrap_recipient", "object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes"}
    if not isinstance(package, Mapping) or set(package) != expected_package:
        raise SourceSignerEnrollmentIssuerError("opaque FI source-adoption install control receipt package is invalid")
    expected_object = delivery["object"]
    if (
        package.get("archive_sha256") != prepared["archive_sha256"]
        or package.get("archive_bytes") != prepared["archive_bytes"]
        or package.get("preparation_receipt_sha256") != prepared["preparation_receipt_sha256"]
        or package.get("delivery_envelope_sha256") != delivery["sha256"]
        or package.get("controller_public_key_base64") != controller_public_key_base64
        or package.get("fi_bootstrap_recipient") != delivery["fi_bootstrap_recipient"]
        or any(package.get(key) != expected_object[key] for key in ("object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes"))
    ):
        raise SourceSignerEnrollmentIssuerError("opaque FI source-adoption install control receipt delivery binding is invalid")
    try:
        installer._require_sha256(package.get("delivery_receipt_sha256"), field="opaque FI install receipt delivery receipt sha256")
    except Exception as exc:
        _raise_from_helper(exc)
    return {
        "campaign_id": campaign_id,
        "candidate_directory": str(candidate),
        "package_id": package_id,
        "application": application,
        "tooling": tooling,
        # Consumer certificates historically bind the hash of the exact FI
        # receipt file, not the receipt's internal checksum field.  Retain
        # both meanings explicitly so the opaque control copy remains
        # compatible without reading any FI path.
        "receipt_sha256": hashlib.sha256(payload).hexdigest(),
        "receipt_content_sha256": receipt_sha,
        "receipt_file_sha256": hashlib.sha256(payload).hexdigest(),
        "installed_at": installed_at,
    }


def _expected_fi_source_signer_key_path(campaign_id: str) -> str:
    return str(FI_SOURCE_SIGNER_CAMPAIGN_ROOT / campaign_id / _FI_SOURCE_SIGNER_DIRECTORY / _FI_SOURCE_SIGNER_KEY_NAME)


def _require_fresh_bootstrap_signer_receipt(created: dt.datetime) -> None:
    now = _controller_utc_now()
    if created > now + dt.timedelta(seconds=MAX_ISSUANCE_CLOCK_SKEW_SECONDS):
        raise SourceSignerEnrollmentIssuerError("FI source signer bootstrap receipt is from the future")
    if now - created > dt.timedelta(seconds=MAX_BOOTSTRAP_SIGNER_RECEIPT_AGE_SECONDS):
        raise SourceSignerEnrollmentIssuerError("FI source signer bootstrap receipt is stale")


def _load_bootstrap_signer_receipt(
    path: Path,
    *,
    installer: Any,
    binding: Mapping[str, Any],
    fi_install_receipt: Mapping[str, Any],
    prepared: Mapping[str, Any],
    delivery: Mapping[str, Any],
    pinned_fi_ssh_host_public_key_sha256: str,
) -> dict[str, Any]:
    _path, payload = _read_root_private_control_file(
        Path(path), field="FI source signer bootstrap receipt", maximum_bytes=_MAX_BOOTSTRAP_SIGNER_RECEIPT_BYTES
    )
    value = _parse_canonical_control_json(
        payload, field="FI source signer bootstrap receipt", maximum_bytes=_MAX_BOOTSTRAP_SIGNER_RECEIPT_BYTES
    )
    expected = {"schema", "status", "created_at", "campaign_id", "source_site", "destination_site", "source_adoption", "source_signer", "fi_ssh_host_public_key_sha256", "receipt_sha256"}
    if set(value) != expected or value.get("schema") != _BOOTSTRAP_SIGNER_RECEIPT_SCHEMA or value.get("status") != "created" or value.get("source_site") != "webapp_fi" or value.get("destination_site") != "webapp_ir":
        raise SourceSignerEnrollmentIssuerError("FI source signer bootstrap receipt is unsupported")
    unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
    receipt_sha = value.get("receipt_sha256")
    if not isinstance(receipt_sha, str) or not _SHA256_RE.fullmatch(receipt_sha) or hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest() != receipt_sha:
        raise SourceSignerEnrollmentIssuerError("FI source signer bootstrap receipt checksum is invalid")
    source_adoption = value.get("source_adoption")
    source_signer = value.get("source_signer")
    if not isinstance(source_adoption, Mapping) or not isinstance(source_signer, Mapping) or set(source_adoption) != {"candidate_directory", "package_id", "application", "tooling", "install_receipt_sha256", "delivery_envelope_sha256"} or set(source_signer) != {"private_key_file", "public_key_base64", "key_id"}:
        raise SourceSignerEnrollmentIssuerError("FI source signer bootstrap receipt has an invalid source binding")
    try:
        campaign_id = installer._require_campaign_id(value.get("campaign_id"), field="bootstrap signer campaign_id")
        created_at, created = _require_timestamp(installer, value.get("created_at"), field="bootstrap signer created_at")
        package_id = installer._require_package_id(source_adoption.get("package_id"), field="bootstrap signer package_id")
        application = installer._require_application(source_adoption.get("application"), field="bootstrap signer application")
        tooling = installer._require_tooling(source_adoption.get("tooling"), field="bootstrap signer tooling")
        install_sha = installer._require_sha256(source_adoption.get("install_receipt_sha256"), field="bootstrap signer install receipt sha256")
        envelope_sha = installer._require_sha256(source_adoption.get("delivery_envelope_sha256"), field="bootstrap signer delivery envelope sha256")
        ssh_sha = installer._require_sha256(value.get("fi_ssh_host_public_key_sha256"), field="bootstrap signer SSH host public key sha256")
        public_key = source_signer.get("public_key_base64")
        source_key_id = installer._public_key_id(public_key)
    except Exception as exc:
        _raise_from_helper(exc)
    candidate_text = source_adoption.get("candidate_directory")
    if not isinstance(candidate_text, str):
        raise SourceSignerEnrollmentIssuerError("FI source signer bootstrap receipt candidate is invalid")
    candidate = _require_absolute_canonical_path(Path(candidate_text), field="bootstrap signer FI candidate claim")
    if (
        campaign_id != binding["campaign_id"]
        or campaign_id != fi_install_receipt["campaign_id"]
        or str(candidate) != fi_install_receipt["candidate_directory"]
        or package_id != prepared["package_id"]
        or package_id != fi_install_receipt["package_id"]
        or application != prepared["application"]
        or application != fi_install_receipt["application"]
        or tooling != prepared["tooling"]
        or tooling != fi_install_receipt["tooling"]
        or install_sha != fi_install_receipt["receipt_file_sha256"]
        or envelope_sha != delivery["sha256"]
    ):
        raise SourceSignerEnrollmentIssuerError("FI source signer bootstrap receipt is not bound to the opaque FI install receipt and local package")
    if ssh_sha != pinned_fi_ssh_host_public_key_sha256:
        raise SourceSignerEnrollmentIssuerError("FI source signer bootstrap receipt SSH digest does not match the controller pin")
    if source_signer.get("private_key_file") != _expected_fi_source_signer_key_path(campaign_id):
        raise SourceSignerEnrollmentIssuerError("FI source signer bootstrap receipt key path is not campaign-derived")
    if source_signer.get("key_id") != source_key_id:
        raise SourceSignerEnrollmentIssuerError("FI source signer bootstrap receipt key ID is invalid")
    _require_fresh_bootstrap_signer_receipt(created)
    return {
        "source_signing_public_key_base64": public_key,
        "source_signing_key_id": source_key_id,
        "fi_ssh_host_public_key_sha256": ssh_sha,
        "receipt_sha256": receipt_sha,
    }


def _load_controller_signer(path: Path) -> tuple[Any, str]:
    raw = _read_root_controlled_file(
        Path(path), field="controller signer enrollment private key", maximum_bytes=32, private=True, exact_mode=0o600
    )
    if len(raw) != 32:
        raise SourceSignerEnrollmentIssuerError("controller signer enrollment private key must contain exactly 32 bytes")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        signer = Ed25519PrivateKey.from_private_bytes(raw)
        public = signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    except (ImportError, ValueError) as exc:
        raise SourceSignerEnrollmentIssuerError("controller signer enrollment key is invalid") from exc
    return signer, base64.b64encode(public).decode("ascii")


def _load_pinned_fi_ssh_host_public_key_digest(path: Path) -> str:
    _path, payload = _read_root_private_control_file(
        Path(path), field="pinned FI SSH host public key", maximum_bytes=_MAX_SSH_HOST_PUBLIC_KEY_BYTES
    )
    if b"\x00" in payload or not payload.rstrip(b"\r\n"):
        raise SourceSignerEnrollmentIssuerError("pinned FI SSH host public key is invalid")
    return hashlib.sha256(payload).hexdigest()


def _require_new_output(path: Path) -> Path:
    output = _require_absolute_canonical_path(Path(path), field="certificate output")
    _require_root_only_directory(output.parent, field="certificate output parent")
    try:
        output.lstat()
    except FileNotFoundError:
        return output
    except OSError as exc:
        raise SourceSignerEnrollmentIssuerError("cannot inspect certificate output") from exc
    raise SourceSignerEnrollmentIssuerError("refusing to overwrite source signer enrollment certificate")


def _write_new_private_certificate(path: Path, value: Mapping[str, Any]) -> bytes:
    payload = _canonical_json_bytes(value) + b"\n"
    if b"://" in payload.lower() or b"presigned" in payload.lower() or b'"url"' in payload.lower():
        raise SourceSignerEnrollmentIssuerError("source signer enrollment certificate persists a forbidden URL")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:  # pragma: no cover
        raise SourceSignerEnrollmentIssuerError("secure no-follow file access is unavailable")
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | no_follow, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise SourceSignerEnrollmentIssuerError("cannot create source signer enrollment certificate") from exc
    persisted = _read_root_controlled_file(
        path, field="new source signer enrollment certificate", maximum_bytes=_MAX_CONTROL_BYTES, private=True, exact_mode=0o600
    )
    if persisted != payload:
        raise SourceSignerEnrollmentIssuerError("source signer enrollment certificate changed while writing")
    return persisted


def _certificate_payload(
    *, installer: Any, prepared: Mapping[str, Any], delivery: Mapping[str, Any], fi_install: Mapping[str, Any], campaign_id: str, certificate_id: str, operation_id: str, issued_at: str, not_before: str, not_after: str, ssh_sha: str, source_public: str, controller_public: str
) -> dict[str, Any]:
    try:
        source_key_id = installer._public_key_id(source_public)
        controller_key_id = installer._public_key_id(controller_public)
    except Exception as exc:
        _raise_from_helper(exc)
    return {
        "schema": SIGNER_ENROLLMENT_CERTIFICATE_SCHEMA,
        "status": "issued",
        "certificate_id": certificate_id,
        "operation_id": operation_id,
        "issued_at": issued_at,
        "not_before": not_before,
        "not_after": not_after,
        "campaign_id": campaign_id,
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "package_id": prepared["package_id"],
        "application": dict(prepared["application"]),
        "tooling": dict(prepared["tooling"]),
        "canonical_release_tree_sha256": prepared["canonical_release_tree_sha256"],
        "source_adoption_install_receipt_sha256": fi_install["receipt_sha256"],
        "delivery_envelope_sha256": delivery["sha256"],
        "source_adoption_object": dict(delivery["object"]),
        "fi_bootstrap_recipient": delivery["fi_bootstrap_recipient"],
        "fi_ssh_host_public_key_sha256": ssh_sha,
        "source_signing_public_key_base64": source_public,
        "source_signing_key_id": source_key_id,
        "controller_public_key_base64": controller_public,
        "controller_key_id": controller_key_id,
    }


def _controller_utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _require_apply_time_admission(*, issued: dt.datetime, begins: dt.datetime, expires: dt.datetime) -> None:
    now = _controller_utc_now()
    skew = dt.timedelta(seconds=MAX_ISSUANCE_CLOCK_SKEW_SECONDS)
    if abs(now - issued) > skew:
        raise SourceSignerEnrollmentIssuerError("issued_at is outside the controller clock-skew window")
    if begins > now + skew:
        raise SourceSignerEnrollmentIssuerError("not_before is outside the controller clock-skew window")
    if expires <= now:
        raise SourceSignerEnrollmentIssuerError("not_after must be after the current controller time")


def _fingerprint(*, prepared: Mapping[str, Any], delivery: Mapping[str, Any], fi_install: Mapping[str, Any], binding: Mapping[str, Any], bootstrap: Mapping[str, Any], installer_sha: str) -> bytes:
    return _canonical_json_bytes({"prepared": dict(prepared), "delivery": dict(delivery), "fi_install": dict(fi_install), "binding": dict(binding), "bootstrap": dict(bootstrap), "installer_sha256": installer_sha})


def _read_all_inputs(
    *, package_directory: Path, preparation_receipt: Path, delivery_envelope: Path, campaign_binding: Path, fi_install_control_receipt: Path, bootstrap_signer_receipt: Path, pinned_fi_ssh_host_public_key_file: Path, controller_public: str
) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], str]:
    binding = _load_campaign_binding(Path(campaign_binding))
    installer, prepared, installer_sha = _load_verified_controller_package(
        package_directory=Path(package_directory), preparation_receipt=Path(preparation_receipt), binding=binding
    )
    delivery = _load_delivery_envelope(
        installer=installer, path=Path(delivery_envelope), controller_public_key_base64=controller_public, binding=binding, prepared=prepared
    )
    fi_install = _load_opaque_fi_install_control_receipt(
        installer=installer, path=Path(fi_install_control_receipt), prepared=prepared, delivery=delivery, controller_public_key_base64=controller_public, campaign_id=binding["campaign_id"]
    )
    ssh_sha = _load_pinned_fi_ssh_host_public_key_digest(Path(pinned_fi_ssh_host_public_key_file))
    bootstrap = _load_bootstrap_signer_receipt(
        Path(bootstrap_signer_receipt), installer=installer, binding=binding, fi_install_receipt=fi_install, prepared=prepared, delivery=delivery, pinned_fi_ssh_host_public_key_sha256=ssh_sha
    )
    return installer, prepared, delivery, fi_install, binding, bootstrap, installer_sha


def issue_source_signer_enrollment_certificate(
    *, package_directory: Path, preparation_receipt: Path, delivery_envelope: Path, campaign_binding: Path, fi_install_control_receipt: Path, bootstrap_signer_receipt: Path, pinned_fi_ssh_host_public_key_file: Path, certificate_id: str, operation_id: str, issued_at: str, not_before: str, not_after: str, controller_signing_private_key: Path, output: Path, apply: bool
) -> dict[str, Any]:
    _require_root_execution()
    # IDs are parsed by the package-bound installer after the initial read.
    output_path = _require_new_output(Path(output))
    _unused, controller_public = _load_controller_signer(Path(controller_signing_private_key))
    del _unused
    installer, prepared, delivery, fi_install, binding, bootstrap, installer_sha = _read_all_inputs(
        package_directory=Path(package_directory), preparation_receipt=Path(preparation_receipt), delivery_envelope=Path(delivery_envelope), campaign_binding=Path(campaign_binding), fi_install_control_receipt=Path(fi_install_control_receipt), bootstrap_signer_receipt=Path(bootstrap_signer_receipt), pinned_fi_ssh_host_public_key_file=Path(pinned_fi_ssh_host_public_key_file), controller_public=controller_public
    )
    try:
        certificate = installer._require_attestation_id(certificate_id)
        operation = installer._require_attestation_id(operation_id)
        issued_text, issued = _require_timestamp(installer, issued_at, field="issued_at")
        not_before_text, begins = _require_timestamp(installer, not_before, field="not_before")
        not_after_text, expires = _require_timestamp(installer, not_after, field="not_after")
    except Exception as exc:
        _raise_from_helper(exc)
    if issued > begins or begins > expires or (expires - issued).total_seconds() > MAX_ENROLLMENT_CERTIFICATE_LIFETIME_SECONDS:
        raise SourceSignerEnrollmentIssuerError("source signer enrollment certificate lifetime is invalid")
    source_public = bootstrap["source_signing_public_key_base64"]
    if source_public == controller_public:
        raise SourceSignerEnrollmentIssuerError("source signing public key must be distinct from the controller key")
    expected_fingerprint = _fingerprint(prepared=prepared, delivery=delivery, fi_install=fi_install, binding=binding, bootstrap=bootstrap, installer_sha=installer_sha)
    signer: Any | None = None
    if apply:
        signer, final_public = _load_controller_signer(Path(controller_signing_private_key))
        if final_public != controller_public:
            raise SourceSignerEnrollmentIssuerError("controller signer enrollment key changed before signing")
        installer, prepared, delivery, fi_install, binding, bootstrap, final_installer_sha = _read_all_inputs(
            package_directory=Path(package_directory), preparation_receipt=Path(preparation_receipt), delivery_envelope=Path(delivery_envelope), campaign_binding=Path(campaign_binding), fi_install_control_receipt=Path(fi_install_control_receipt), bootstrap_signer_receipt=Path(bootstrap_signer_receipt), pinned_fi_ssh_host_public_key_file=Path(pinned_fi_ssh_host_public_key_file), controller_public=controller_public
        )
        if _fingerprint(prepared=prepared, delivery=delivery, fi_install=fi_install, binding=binding, bootstrap=bootstrap, installer_sha=final_installer_sha) != expected_fingerprint:
            raise SourceSignerEnrollmentIssuerError("immutable source signer enrollment inputs changed before signing")
        source_public = bootstrap["source_signing_public_key_base64"]
        if source_public == controller_public:
            raise SourceSignerEnrollmentIssuerError("source signing public key must be distinct from the controller key")
        _require_apply_time_admission(issued=issued, begins=begins, expires=expires)
    unsigned = _certificate_payload(
        installer=installer, prepared=prepared, delivery=delivery, fi_install=fi_install, campaign_id=binding["campaign_id"], certificate_id=certificate, operation_id=operation, issued_at=issued_text, not_before=not_before_text, not_after=not_after_text, ssh_sha=bootstrap["fi_ssh_host_public_key_sha256"], source_public=source_public, controller_public=controller_public
    )
    result = {"status": "issued" if apply else "planned", "certificate_path": str(output_path), "campaign_id": binding["campaign_id"], "certificate_id": certificate, "operation_id": operation, "not_before": not_before_text, "not_after": not_after_text, "package_id": prepared["package_id"], "delivery_envelope_sha256": delivery["sha256"], "campaign_binding_sha256": binding["binding_sha256"], "fi_install_control_receipt_sha256": fi_install["receipt_sha256"], "bootstrap_signer_receipt_sha256": bootstrap["receipt_sha256"], "source_adoption_object": {"object_key": delivery["object"]["object_key"], "version_id": delivery["object"]["version_id"]}, "source_signing_key_id": unsigned["source_signing_key_id"], "controller_key_id": unsigned["controller_key_id"], "private_key_created": False, "object_storage_action": False, "ssh_action": False, "docker_action": False, "service_changed": False, "current_changed": False, "container_changed": False, "volume_changed": False, "application_data_changed": False}
    if not apply:
        return result
    if signer is None:  # pragma: no cover
        raise SourceSignerEnrollmentIssuerError("controller signer is unavailable")
    signature = signer.sign(SIGNER_ENROLLMENT_SIGNATURE_DOMAIN + _canonical_json_bytes(unsigned))
    value = {**unsigned, "controller_signature": {"algorithm": "ed25519", "signature_base64": base64.b64encode(signature).decode("ascii")}}
    written = _write_new_private_certificate(output_path, value)
    result["certificate_sha256"] = hashlib.sha256(written).hexdigest()
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-directory", type=Path, required=True)
    parser.add_argument("--preparation-receipt", type=Path, required=True)
    parser.add_argument("--delivery-envelope", type=Path, required=True)
    parser.add_argument("--campaign-binding", type=Path, required=True)
    parser.add_argument("--fi-install-control-receipt", type=Path, required=True)
    parser.add_argument("--bootstrap-signer-receipt", type=Path, required=True)
    parser.add_argument("--pinned-fi-ssh-host-public-key-file", type=Path, required=True)
    parser.add_argument("--certificate-id", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--issued-at", required=True)
    parser.add_argument("--not-before", required=True)
    parser.add_argument("--not-after", required=True)
    parser.add_argument("--controller-signing-private-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = issue_source_signer_enrollment_certificate(
            package_directory=args.package_directory,
            preparation_receipt=args.preparation_receipt,
            delivery_envelope=args.delivery_envelope,
            campaign_binding=args.campaign_binding,
            fi_install_control_receipt=args.fi_install_control_receipt,
            bootstrap_signer_receipt=args.bootstrap_signer_receipt,
            pinned_fi_ssh_host_public_key_file=args.pinned_fi_ssh_host_public_key_file,
            certificate_id=args.certificate_id,
            operation_id=args.operation_id,
            issued_at=args.issued_at,
            not_before=args.not_before,
            not_after=args.not_after,
            controller_signing_private_key=args.controller_signing_private_key,
            output=args.output,
            apply=args.apply,
        )
    except SourceSignerEnrollmentIssuerError as exc:
        print(_canonical_json_bytes({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}).decode("ascii"), file=sys.stderr)
        return 2
    print(_canonical_json_bytes(result).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
