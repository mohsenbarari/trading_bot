#!/usr/bin/env python3
"""Read one verified controller static-provenance packet into an FI candidate.

The existing FI exchange has already performed the only Object Storage GET,
age decryption, and VersionId-bound read.  This helper performs no network,
Object Storage, SSH, Docker, service, container, volume, current, migration,
or data-plane action.  It verifies the retained receive receipt and the
controller-signed packet, then creates exactly one root-only, create-only
subdirectory below the already verified source-adoption candidate.

The result gives later local signer-enrollment and role-attestation commands
their fixed certificate, role-config, static-provenance, and transport-policy
paths.  It never invokes those later commands itself.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import types
from typing import Any, Mapping, Sequence


INSTALL_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-adoption-install-receipt-v1"
INSTALL_RECEIPT_NAME = "source-adoption-install-receipt.json"
THIS_SCRIPT_RELATIVE = "scripts/install_webapp_fi_static_provenance_control_packet.py"
INSTALL_SCRIPT_RELATIVE = "scripts/install_webapp_fi_source_adoption.py"
CONTROL_PACKET_SCRIPT_RELATIVE = "scripts/webapp_fi_static_provenance_control_packet.py"
TRANSPORT_CONTRACT_SCRIPT_RELATIVE = "scripts/webapp_fi_source_transport_contract.py"

CAMPAIGN_ROOT = Path("/etc/trading-bot-three-site/campaigns")
SOURCE_PHASE_DIRECTORY = "webapp-fi-source"
CAMPAIGN_BINDING_FILENAME = "campaign-binding.json"
CONTROLLER_STATIC_PROVENANCE_DIRECTORY = "controller-static-provenance"
RECEIVED_PACKET_NAME = "static-provenance.json"
EXCHANGE_RECEIPT_NAME = "receive-receipt.json"
CONTROL_PACKET_FILENAME = "control-packet.json"
SIGNER_ENROLLMENT_CERTIFICATE_FILENAME = "signer-enrollment-certificate.json"
SOURCE_ROLE_CONFIG_FILENAME = "source-role-config.json"
STATIC_ASSETS_PROVENANCE_FILENAME = "static-assets-provenance.json"
SOURCE_TRANSPORT_POLICY_FILENAME = "source-transport-policy.json"
READ_RECEIPT_FILENAME = "static-provenance-install-receipt.json"
READ_RECEIPT_SCHEMA = "gold-trade-webapp-fi-static-provenance-install-receipt-v1"

MAX_INSTALL_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_INSTALLED_SOURCE_BYTES = 8 * 1024 * 1024
MAX_RECEIVE_RECEIPT_BYTES = 1024 * 1024


class StaticProvenanceControlPacketInstallError(RuntimeError):
    """An FI static-provenance packet cannot be safely installed."""


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise StaticProvenanceControlPacketInstallError("WebApp-FI static-provenance packet operations must run as root")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StaticProvenanceControlPacketInstallError("JSON input contains duplicate keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise StaticProvenanceControlPacketInstallError(f"JSON input contains unsupported constant: {value}")


def _parse_canonical_json(payload: bytes, *, field: str, maximum_bytes: int) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= maximum_bytes:
        raise StaticProvenanceControlPacketInstallError(f"{field} has an unsafe size")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StaticProvenanceControlPacketInstallError(f"{field} is not strict canonical JSON") from exc
    if not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise StaticProvenanceControlPacketInstallError(f"{field} is not canonical JSON")
    return value


def _require_absolute_canonical_path(path: Path, *, field: str) -> Path:
    candidate = Path(path)
    if (
        "\x00" in str(candidate)
        or not candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts[1:])
        or str(candidate) != os.path.normpath(str(candidate))
    ):
        raise StaticProvenanceControlPacketInstallError(f"{field} must be one canonical absolute path")
    return candidate


def _require_root_controlled_ancestors(path: Path, *, field: str) -> None:
    path = _require_absolute_canonical_path(path, field=field)
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise StaticProvenanceControlPacketInstallError(f"cannot inspect {field} parent") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or ((mode & 0o022) and not (metadata.st_mode & stat.S_ISVTX))
        ):
            raise StaticProvenanceControlPacketInstallError(f"{field} parent is unsafe")


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


def _safe_private_file(metadata: os.stat_result, *, maximum_bytes: int) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == 0
        and metadata.st_nlink == 1
        and not (metadata.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX))
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and 1 <= metadata.st_size <= maximum_bytes
    )


def _require_root_private_directory(path: Path, *, field: str) -> Path:
    directory = _require_absolute_canonical_path(Path(path), field=field)
    _require_root_controlled_ancestors(directory.parent, field=field)
    try:
        before = directory.lstat()
        resolved = directory.resolve(strict=True)
        target = resolved.lstat()
    except OSError as exc:
        raise StaticProvenanceControlPacketInstallError(f"cannot inspect {field}") from exc
    if (
        resolved != directory
        or stat.S_ISLNK(before.st_mode)
        or stat.S_ISLNK(target.st_mode)
        or not stat.S_ISDIR(target.st_mode)
        or target.st_uid != 0
        or stat.S_IMODE(target.st_mode) != 0o700
    ):
        raise StaticProvenanceControlPacketInstallError(f"{field} must be one root-only mode 0700 non-symlink directory")
    return resolved


def _read_root_private_file(path: Path, *, field: str, maximum_bytes: int) -> bytes:
    source = _require_absolute_canonical_path(Path(path), field=field)
    _require_root_controlled_ancestors(source.parent, field=field)
    try:
        before = source.lstat()
    except OSError as exc:
        raise StaticProvenanceControlPacketInstallError(f"cannot inspect {field}") from exc
    if not _safe_private_file(before, maximum_bytes=maximum_bytes):
        raise StaticProvenanceControlPacketInstallError(f"{field} is unsafe")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:  # pragma: no cover - Linux deployment invariant.
        raise StaticProvenanceControlPacketInstallError("secure no-follow file access is unavailable")
    try:
        descriptor = os.open(str(source), os.O_RDONLY | os.O_CLOEXEC | no_follow)
    except OSError as exc:
        raise StaticProvenanceControlPacketInstallError(f"cannot securely open {field}") from exc
    try:
        opened = os.fstat(descriptor)
        if not _same_file_metadata(before, opened) or not _safe_private_file(opened, maximum_bytes=maximum_bytes):
            raise StaticProvenanceControlPacketInstallError(f"{field} changed while opening")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise StaticProvenanceControlPacketInstallError(f"{field} is too large")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        payload = b"".join(chunks)
        if len(payload) != opened.st_size or not _same_file_metadata(opened, after):
            raise StaticProvenanceControlPacketInstallError(f"{field} changed while reading")
        return payload
    except OSError as exc:
        raise StaticProvenanceControlPacketInstallError(f"cannot read {field}") from exc
    finally:
        os.close(descriptor)


def _create_or_require_root_private_directory(parent: Path, name: str, *, field: str) -> Path:
    parent = _require_root_private_directory(parent, field=field + " parent")
    if not isinstance(name, str) or not control.IDENTIFIER_RE.fullmatch(name):
        raise StaticProvenanceControlPacketInstallError(f"{field} name is invalid")
    child = parent / name
    try:
        os.mkdir(child, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise StaticProvenanceControlPacketInstallError(f"cannot create {field}") from exc
    try:
        os.chmod(child, 0o700)
    except OSError as exc:
        raise StaticProvenanceControlPacketInstallError(f"cannot protect {field}") from exc
    return _require_root_private_directory(child, field=field)


def _create_new_root_private_directory(parent: Path, name: str, *, field: str) -> Path:
    parent = _require_root_private_directory(parent, field=field + " parent")
    if not isinstance(name, str) or not control.IDENTIFIER_RE.fullmatch(name):
        raise StaticProvenanceControlPacketInstallError(f"{field} name is invalid")
    child = parent / name
    try:
        child.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise StaticProvenanceControlPacketInstallError(f"cannot inspect {field}") from exc
    else:
        raise StaticProvenanceControlPacketInstallError(f"refusing to reuse or overwrite existing {field}")
    try:
        os.mkdir(child, 0o700)
        os.chmod(child, 0o700)
    except OSError as exc:
        raise StaticProvenanceControlPacketInstallError(f"cannot create {field}") from exc
    return _require_root_private_directory(child, field=field)


def _write_new_root_private_file(path: Path, payload: bytes, *, field: str) -> None:
    destination = _require_absolute_canonical_path(Path(path), field=field)
    _require_root_private_directory(destination.parent, field=field + " parent")
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= control.MAX_PACKET_BYTES:
        raise StaticProvenanceControlPacketInstallError(f"{field} payload is invalid")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:  # pragma: no cover - Linux deployment invariant.
        raise StaticProvenanceControlPacketInstallError("secure no-follow file creation is unavailable")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | no_follow
    try:
        descriptor = os.open(str(destination), flags, 0o600)
    except FileExistsError as exc:
        raise StaticProvenanceControlPacketInstallError(f"refusing to reuse or overwrite existing {field}") from exc
    except OSError as exc:
        raise StaticProvenanceControlPacketInstallError(f"cannot create {field}") from exc
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:  # pragma: no cover - regular-file writes do not return zero.
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if not _safe_private_file(metadata, maximum_bytes=control.MAX_PACKET_BYTES) or metadata.st_size != len(payload):
            raise StaticProvenanceControlPacketInstallError(f"new {field} is unsafe")
    except OSError as exc:
        raise StaticProvenanceControlPacketInstallError(f"cannot durably create {field}") from exc
    finally:
        os.close(descriptor)


def _execute_verified_module(module_name: str, path: Path, source: bytes) -> Any:
    previous = sys.modules.get(module_name)
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = ""
    sys.modules[module_name] = module
    try:
        exec(compile(source, str(path), "exec"), module.__dict__)
    except BaseException as exc:
        raise StaticProvenanceControlPacketInstallError(f"cannot load verified {path.name}") from exc
    finally:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
    return module


def _parse_install_receipt(payload: bytes) -> dict[str, Any]:
    value = _parse_canonical_json(payload, field="source-adoption install receipt", maximum_bytes=MAX_INSTALL_RECEIPT_BYTES)
    expected = {
        "schema", "status", "installed_at", "candidate_directory", "source_site", "destination_site",
        "campaign_id", "package_id", "application", "tooling", "files", "canonical_release_tree_sha256",
        "package", "receipt_sha256",
    }
    if set(value) != expected or value.get("schema") != INSTALL_RECEIPT_SCHEMA or value.get("status") != "installed":
        raise StaticProvenanceControlPacketInstallError("source-adoption install receipt is unsupported")
    checksum = value.get("receipt_sha256")
    if not isinstance(checksum, str) or len(checksum) != 64 or any(item not in "0123456789abcdef" for item in checksum):
        raise StaticProvenanceControlPacketInstallError("source-adoption install receipt checksum is invalid")
    if checksum != sha256_bytes(canonical_json_bytes({key: item for key, item in value.items() if key != "receipt_sha256"})):
        raise StaticProvenanceControlPacketInstallError("source-adoption install receipt checksum is invalid")
    return value


def _load_verified_installed_adoption(install_receipt: Path) -> tuple[Any, Any, Any, dict[str, Any]]:
    """Verify the current helper candidate before executing any candidate code."""

    receipt_path = _require_absolute_canonical_path(Path(install_receipt), field="install receipt")
    receipt_payload = _read_root_private_file(
        receipt_path,
        field="install receipt",
        maximum_bytes=MAX_INSTALL_RECEIPT_BYTES,
    )
    receipt = _parse_install_receipt(receipt_payload)
    candidate_text = receipt.get("candidate_directory")
    if not isinstance(candidate_text, str):
        raise StaticProvenanceControlPacketInstallError("source-adoption install receipt candidate is invalid")
    candidate = _require_root_private_directory(Path(candidate_text), field="installed source-adoption candidate")
    if receipt_path != candidate / INSTALL_RECEIPT_NAME:
        raise StaticProvenanceControlPacketInstallError("source-adoption install receipt is not candidate-bound")
    files = receipt.get("files")
    if not isinstance(files, Mapping):
        raise StaticProvenanceControlPacketInstallError("installed source-adoption helper hashes are unavailable")
    required_relatives = (
        INSTALL_SCRIPT_RELATIVE,
        THIS_SCRIPT_RELATIVE,
        CONTROL_PACKET_SCRIPT_RELATIVE,
        TRANSPORT_CONTRACT_SCRIPT_RELATIVE,
    )
    trusted_bytes: dict[str, bytes] = {}
    for relative in required_relatives:
        expected = files.get(relative)
        if not isinstance(expected, str) or len(expected) != 64 or any(item not in "0123456789abcdef" for item in expected):
            raise StaticProvenanceControlPacketInstallError(f"installed {relative} hash is invalid")
        source = _read_root_private_file(
            candidate / relative,
            field=f"installed {relative}",
            maximum_bytes=MAX_INSTALLED_SOURCE_BYTES,
        )
        if sha256_bytes(source) != expected:
            raise StaticProvenanceControlPacketInstallError(f"installed {relative} hash changed")
        trusted_bytes[relative] = source
    current_script = _require_absolute_canonical_path(
        Path(__file__).absolute(),
        field="static-provenance packet reader script",
    )
    if current_script != candidate / THIS_SCRIPT_RELATIVE:
        raise StaticProvenanceControlPacketInstallError("static-provenance packet reader must run from the verified installed candidate")
    installer = _execute_verified_module(
        "_verified_webapp_fi_source_adoption_installer_for_static_provenance",
        candidate / INSTALL_SCRIPT_RELATIVE,
        trusted_bytes[INSTALL_SCRIPT_RELATIVE],
    )
    if (
        getattr(installer, "INSTALL_RECEIPT_SCHEMA", None) != INSTALL_RECEIPT_SCHEMA
        or not callable(getattr(installer, "verify_installed_source_adoption", None))
    ):
        raise StaticProvenanceControlPacketInstallError("verified source-adoption installer contract is incompatible")
    try:
        installed = installer.verify_installed_source_adoption(receipt_path)
    except Exception as exc:
        raise StaticProvenanceControlPacketInstallError("installed source-adoption receipt cannot be verified") from exc
    if not isinstance(installed, Mapping) or installed.get("candidate") != candidate:
        raise StaticProvenanceControlPacketInstallError("installed source-adoption receipt changed while being verified")
    packet_control = _execute_verified_module(
        "_verified_webapp_fi_static_provenance_control_packet",
        candidate / CONTROL_PACKET_SCRIPT_RELATIVE,
        trusted_bytes[CONTROL_PACKET_SCRIPT_RELATIVE],
    )
    if (
        getattr(packet_control, "CONTROL_PACKET_SCHEMA", None)
        != "gold-trade-webapp-fi-static-provenance-control-packet-v1"
        or not callable(getattr(packet_control, "verify_control_packet_payload", None))
        or not callable(getattr(packet_control, "verify_exchange_receive_receipt", None))
        or not callable(getattr(packet_control, "binding_identity_from_payload", None))
    ):
        raise StaticProvenanceControlPacketInstallError("verified static-provenance packet contract is incompatible")
    transport = _execute_verified_module(
        "_verified_webapp_fi_source_transport_contract_for_static_provenance",
        candidate / TRANSPORT_CONTRACT_SCRIPT_RELATIVE,
        trusted_bytes[TRANSPORT_CONTRACT_SCRIPT_RELATIVE],
    )
    if (
        getattr(transport, "STATIC_PROVENANCE_OBJECT_KIND", None) != "static-provenance"
        or not callable(getattr(transport, "validate_policy", None))
        or not callable(getattr(transport, "source_object_key", None))
    ):
        raise StaticProvenanceControlPacketInstallError("verified FI source transport contract is incompatible")
    return installer, packet_control, transport, dict(installed)


def _campaign_binding_target(campaign_id: str) -> Path:
    """Return the one FI binding target without requiring it to exist.

    This is the first consumer of the binding on WebApp-FI.  A bootstrap
    identity has already established the root and campaign directory, but the
    source-phase directory and the binding itself must be created only after
    the controller-signed packet and installed source package agree.
    """

    campaign = control._require_identifier(campaign_id, field="campaign ID", campaign=True)
    root = _require_root_private_directory(CAMPAIGN_ROOT, field="campaign root")
    campaign_directory = _require_root_private_directory(root / campaign, field="campaign directory")
    source_phase = campaign_directory / SOURCE_PHASE_DIRECTORY
    try:
        source_phase.lstat()
    except FileNotFoundError:
        return source_phase / CAMPAIGN_BINDING_FILENAME
    except OSError as exc:
        raise StaticProvenanceControlPacketInstallError("cannot inspect campaign source-phase directory") from exc
    _require_root_private_directory(source_phase, field="campaign source-phase directory")
    return source_phase / CAMPAIGN_BINDING_FILENAME


def _create_campaign_binding_target(campaign_id: str) -> Path:
    """Create only the fixed root-private parent needed for one binding."""

    target = _campaign_binding_target(campaign_id)
    source_phase = _create_or_require_root_private_directory(
        target.parent.parent,
        SOURCE_PHASE_DIRECTORY,
        field="campaign source-phase directory",
    )
    if source_phase / CAMPAIGN_BINDING_FILENAME != target:
        raise StaticProvenanceControlPacketInstallError("campaign binding target changed before creation")
    return target


def _require_absent(path: Path, *, field: str) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise StaticProvenanceControlPacketInstallError(f"cannot inspect {field}") from exc
    raise StaticProvenanceControlPacketInstallError(f"refusing to reuse or overwrite existing {field}")


def _fsync_root_private_directory(path: Path, *, field: str) -> None:
    directory = _require_root_private_directory(path, field=field)
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(str(directory), flags)
    except OSError as exc:
        raise StaticProvenanceControlPacketInstallError(f"cannot open {field}") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise StaticProvenanceControlPacketInstallError(f"{field} changed while opening")
        os.fsync(descriptor)
    except StaticProvenanceControlPacketInstallError:
        raise
    except OSError as exc:
        raise StaticProvenanceControlPacketInstallError(f"cannot durably sync {field}") from exc
    finally:
        os.close(descriptor)


def _load_received_packet(received_directory: Path, *, policy: Mapping[str, Any]) -> tuple[bytes, bytes]:
    directory = _require_root_private_directory(received_directory, field="received static-provenance directory")
    workspace = _require_root_private_directory(Path(policy["workspace"]), field="FI source exchange workspace")
    if directory.parent != workspace or not control.IDENTIFIER_RE.fullmatch(directory.name):
        raise StaticProvenanceControlPacketInstallError("received static-provenance directory is not one exchange workspace child")
    try:
        names = {item.name for item in directory.iterdir()}
    except OSError as exc:
        raise StaticProvenanceControlPacketInstallError("cannot inspect received static-provenance directory") from exc
    if names != {RECEIVED_PACKET_NAME, EXCHANGE_RECEIPT_NAME}:
        raise StaticProvenanceControlPacketInstallError("received static-provenance directory has an unexpected layout")
    return (
        _read_root_private_file(
            directory / RECEIVED_PACKET_NAME,
            field="received static-provenance packet",
            maximum_bytes=control.MAX_PACKET_BYTES,
        ),
        _read_root_private_file(
            directory / EXCHANGE_RECEIPT_NAME,
            field="received static-provenance receipt",
            maximum_bytes=MAX_RECEIVE_RECEIPT_BYTES,
        ),
    )


def _expected_object_key(
    *,
    transport: Any,
    campaign_binding: Mapping[str, Any],
    packet_id: str,
    policy: Mapping[str, Any],
) -> str:
    """Build the exact existing controller-to-FI object key in memory only."""

    try:
        exchange_policy = transport.SourceTransportPolicy(
            endpoint="https" + ":" + "//" + policy["endpoint_host"],
            region=policy["region"],
            bucket=policy["bucket"],
            prefix=policy["prefix"],
            age_binary=policy["age_binary"],
            workspace=Path(policy["workspace"]),
            controller_age_recipient=policy["controller_age_recipient"],
            webapp_fi_age_recipient=policy["webapp_fi_age_recipient"],
            webapp_ir_age_recipient=policy["webapp_ir_age_recipient"],
            maximum_plaintext_bytes=policy["maximum_plaintext_bytes"],
        )
        exchange_policy = transport.validate_policy(exchange_policy)
        binding = campaign_binding
        request = transport.SourceObjectRequest(
            campaign_id=binding["campaign_id"],
            release_sha=binding["application"]["release_sha"],
            control_commit=binding["tooling"]["control_commit"],
            control_tree=binding["tooling"]["control_tree"],
            source_site="controller",
            destination_site="webapp_fi",
            object_kind=transport.STATIC_PROVENANCE_OBJECT_KIND,
            object_id=packet_id,
            mode=transport.SINGLE_MODE,
            recipients=(exchange_policy.webapp_fi_age_recipient,),
        )
        return transport.source_object_key(exchange_policy, request)
    except Exception as exc:
        raise StaticProvenanceControlPacketInstallError("static-provenance packet transport policy is incompatible") from exc


def _validate_packet_against_installed_candidate(
    *,
    packet_control: Any,
    verified_packet: Mapping[str, Any],
    installed: Mapping[str, Any],
) -> None:
    """Bind the shipped enrollment certificate to this exact FI candidate.

    The later enrollment command rechecks the local signer key, SSH host key,
    and certificate clock window.  Those observations do not exist at this
    read-only packet step.  All candidate-held controller facts do exist now,
    though, and must agree before an immutable candidate subdirectory is made.
    """

    try:
        certificate = packet_control.parse_canonical_json(
            verified_packet["signer_enrollment_certificate_payload"],
            field="signer enrollment certificate",
            maximum_bytes=packet_control.MAX_ARTIFACT_BYTES,
        )
    except Exception as exc:
        raise StaticProvenanceControlPacketInstallError("signer enrollment certificate cannot be re-read") from exc
    package = installed.get("package")
    if not isinstance(package, Mapping):
        raise StaticProvenanceControlPacketInstallError("installed source-adoption package is invalid")
    expected_object = {
        "object_key": package.get("object_key"),
        "version_id": package.get("version_id"),
        "ciphertext_sha256": package.get("ciphertext_sha256"),
        "ciphertext_bytes": package.get("ciphertext_bytes"),
        "plaintext_sha256": package.get("archive_sha256"),
        "plaintext_bytes": package.get("archive_bytes"),
    }
    try:
        not_before = dt.datetime.strptime(certificate["not_before"], "%Y-%m-%dT%H:%M:%SZ")
        not_after = dt.datetime.strptime(certificate["not_after"], "%Y-%m-%dT%H:%M:%SZ")
        now = dt.datetime.strptime(utc_now(), "%Y-%m-%dT%H:%M:%SZ")
    except (KeyError, TypeError, ValueError) as exc:
        raise StaticProvenanceControlPacketInstallError("signer enrollment certificate lifetime is invalid") from exc
    if now < not_before or now > not_after:
        raise StaticProvenanceControlPacketInstallError("signer enrollment certificate is not currently valid")
    if (
        certificate.get("package_id") != installed.get("package_id")
        or certificate.get("canonical_release_tree_sha256") != installed.get("canonical_release_tree_sha256")
        or certificate.get("source_adoption_install_receipt_sha256") != installed.get("receipt_sha256")
        or certificate.get("delivery_envelope_sha256") != package.get("delivery_envelope_sha256")
        or certificate.get("source_adoption_object") != expected_object
        or certificate.get("fi_bootstrap_recipient") != package.get("fi_bootstrap_recipient")
        or certificate.get("controller_public_key_base64") != package.get("controller_public_key_base64")
    ):
        raise StaticProvenanceControlPacketInstallError(
            "signer enrollment certificate is not bound to this installed source-adoption candidate"
        )


def _validate_binding_against_installed_candidate(
    *,
    installer: Any,
    packet_control: Any,
    verified_packet: Mapping[str, Any],
    installed: Mapping[str, Any],
) -> None:
    """Bind packet-held full binding to the verified FI source package.

    No FI-side binding is consulted here.  The only local authority is the
    already verified source-adoption candidate: its contract pins release,
    revision and control tree, and its canonical descriptor pins the release
    Git tree.
    """

    binding = verified_packet.get("campaign_binding")
    if not isinstance(binding, Mapping):
        raise StaticProvenanceControlPacketInstallError("verified packet campaign binding is invalid")
    application = binding.get("application")
    tooling = binding.get("tooling")
    installed_application = installed.get("application")
    installed_tooling = installed.get("tooling")
    if not isinstance(application, Mapping) or not isinstance(tooling, Mapping):
        raise StaticProvenanceControlPacketInstallError("verified packet campaign binding is invalid")
    if not isinstance(installed_application, Mapping) or not isinstance(installed_tooling, Mapping):
        raise StaticProvenanceControlPacketInstallError("installed source-adoption package contract is invalid")
    expected_application = {
        "release_sha": installed_application.get("release_sha"),
        "release_tree": application.get("release_tree"),
        "expected_alembic_revision": installed_application.get("expected_alembic_revision"),
    }
    if (
        binding.get("campaign_id") != installed.get("campaign_id")
        or application != expected_application
        or tooling != installed_tooling
    ):
        raise StaticProvenanceControlPacketInstallError(
            "controller campaign binding does not match the installed source-adoption package contract"
        )
    descriptor_member = getattr(installer, "CANONICAL_RELEASE_TREE_MEMBER", None)
    descriptor_validator = getattr(installer, "_validate_canonical_release_tree_descriptor", None)
    if descriptor_member != "config/canonical-release-tree.json" or not callable(descriptor_validator):
        raise StaticProvenanceControlPacketInstallError("verified source-adoption descriptor contract is incompatible")
    candidate = installed.get("candidate")
    expected_descriptor_sha256 = installed.get("canonical_release_tree_sha256")
    if not isinstance(candidate, Path) or not isinstance(expected_descriptor_sha256, str):
        raise StaticProvenanceControlPacketInstallError("installed source-adoption descriptor is invalid")
    descriptor_payload = _read_root_private_file(
        candidate / descriptor_member,
        field="installed canonical release descriptor",
        maximum_bytes=MAX_INSTALLED_SOURCE_BYTES,
    )
    if sha256_bytes(descriptor_payload) != expected_descriptor_sha256:
        raise StaticProvenanceControlPacketInstallError("installed canonical release descriptor changed")
    try:
        descriptor = descriptor_validator(descriptor_payload)
    except Exception as exc:
        raise StaticProvenanceControlPacketInstallError("installed canonical release descriptor cannot be verified") from exc
    if (
        not isinstance(descriptor, Mapping)
        or descriptor.get("application")
        != {
            "release_sha": application["release_sha"],
            "git_tree": application["release_tree"],
        }
    ):
        raise StaticProvenanceControlPacketInstallError(
            "controller campaign binding release tree does not match the installed source-adoption package"
        )
    payload = verified_packet.get("campaign_binding_payload")
    if not isinstance(payload, bytes):
        raise StaticProvenanceControlPacketInstallError("verified packet campaign binding payload is invalid")
    try:
        identity = packet_control.binding_identity_from_payload(payload)
    except Exception as exc:
        raise StaticProvenanceControlPacketInstallError("verified packet campaign binding payload cannot be re-read") from exc
    if dict(identity) != dict(binding):
        raise StaticProvenanceControlPacketInstallError("verified packet campaign binding changed while being checked")


def control_packet_output_directory(*, candidate: Path, packet_id: str) -> Path:
    candidate = _require_root_private_directory(candidate, field="installed source-adoption candidate")
    packet = control._require_identifier(packet_id, field="control packet ID")
    return candidate / CONTROLLER_STATIC_PROVENANCE_DIRECTORY / packet


def _read_receipt_value(
    *,
    installed: Mapping[str, Any],
    packet_id: str,
    packet_payload: bytes,
    verified_packet: Mapping[str, Any],
    exchange_receipt_payload: bytes,
    exchange_result: Mapping[str, Any],
) -> dict[str, Any]:
    unsigned = {
        "schema": READ_RECEIPT_SCHEMA,
        "status": "installed",
        "installed_at": utc_now(),
        "candidate_directory": str(installed["candidate"]),
        "campaign_id": installed["campaign_id"],
        "packet_id": packet_id,
        "control_packet_sha256": sha256_bytes(packet_payload),
        "campaign_binding_sha256": verified_packet["campaign_binding_sha256"],
        "signer_enrollment_certificate_sha256": verified_packet["signer_enrollment_certificate_sha256"],
        "source_role_config_sha256": verified_packet["source_role_config_sha256"],
        "static_assets_provenance_sha256": verified_packet["static_assets_provenance_sha256"],
        "source_transport_policy_sha256": verified_packet["source_transport_policy_sha256"],
        "exchange_receive_receipt_sha256": sha256_bytes(exchange_receipt_payload),
        "exchange_object": dict(exchange_result["object"]),
    }
    return {**unsigned, "receipt_sha256": sha256_bytes(canonical_json_bytes(unsigned))}


def _validate_read_receipt(payload: bytes, *, expected: Mapping[str, Any]) -> None:
    value = _parse_canonical_json(payload, field="static-provenance install receipt", maximum_bytes=MAX_RECEIVE_RECEIPT_BYTES)
    if set(value) != set(expected) or value != dict(expected):
        raise StaticProvenanceControlPacketInstallError("created static-provenance install receipt is invalid")
    if value["receipt_sha256"] != sha256_bytes(canonical_json_bytes({key: item for key, item in value.items() if key != "receipt_sha256"})):
        raise StaticProvenanceControlPacketInstallError("created static-provenance install receipt checksum is invalid")


def install_static_provenance_control_packet(
    *,
    install_receipt: Path,
    received_directory: Path,
    apply: bool,
) -> dict[str, Any]:
    """Verify one exchange candidate and optionally install its fixed inputs."""

    _require_root_execution()
    installer, packet_control, transport, installed = _load_verified_installed_adoption(Path(install_receipt))
    global control
    control = packet_control
    campaign_id = installed.get("campaign_id")
    if not isinstance(campaign_id, str):
        raise StaticProvenanceControlPacketInstallError("installed source-adoption campaign is invalid")
    binding_target = _campaign_binding_target(campaign_id)
    _require_absent(binding_target, field="campaign binding")
    package = installed.get("package")
    if not isinstance(package, Mapping) or not isinstance(package.get("controller_public_key_base64"), str):
        raise StaticProvenanceControlPacketInstallError("installed source-adoption controller key is invalid")
    # Read the packet first using only its exchange-independent shape.  Its
    # embedded policy then tells us the one allowed received-workspace parent.
    received = _require_root_private_directory(Path(received_directory), field="received static-provenance directory")
    packet_path = received / RECEIVED_PACKET_NAME
    receipt_path = received / EXCHANGE_RECEIPT_NAME
    packet_payload = _read_root_private_file(
        packet_path,
        field="received static-provenance packet",
        maximum_bytes=packet_control.MAX_PACKET_BYTES,
    )
    try:
        verified_packet = packet_control.verify_control_packet_payload(
            payload=packet_payload,
            pinned_controller_public_key_base64=package["controller_public_key_base64"],
        )
        raw_packet = packet_control.parse_canonical_json(
            packet_payload,
            field="received static-provenance packet",
            maximum_bytes=packet_control.MAX_PACKET_BYTES,
        )
    except Exception as exc:
        raise StaticProvenanceControlPacketInstallError("received static-provenance packet cannot be verified") from exc
    _validate_packet_against_installed_candidate(
        packet_control=packet_control,
        verified_packet=verified_packet,
        installed=installed,
    )
    _validate_binding_against_installed_candidate(
        installer=installer,
        packet_control=packet_control,
        verified_packet=verified_packet,
        installed=installed,
    )
    # Enforce the receiver's original direct-workspace layout after the signed
    # URL-free policy is available.  This also rejects extra retained files.
    received_packet_payload, exchange_receipt_payload = _load_received_packet(
        received,
        policy=verified_packet["source_transport_policy"],
    )
    if received_packet_payload != packet_payload:
        raise StaticProvenanceControlPacketInstallError("received static-provenance packet changed while being verified")
    try:
        expected_key = _expected_object_key(
            transport=transport,
            campaign_binding=verified_packet["campaign_binding"],
            packet_id=verified_packet["packet_id"],
            policy=verified_packet["source_transport_policy"],
        )
        exchange_result = packet_control.verify_exchange_receive_receipt(
            payload=exchange_receipt_payload,
            control_packet_payload=packet_payload,
            packet=raw_packet,
            expected_object_key=expected_key,
        )
    except Exception as exc:
        raise StaticProvenanceControlPacketInstallError("received static-provenance receipt cannot be verified") from exc
    packet_id = verified_packet["packet_id"]
    output = control_packet_output_directory(candidate=installed["candidate"], packet_id=packet_id)
    if output.exists() or output.is_symlink():
        raise StaticProvenanceControlPacketInstallError("refusing to reuse or overwrite a static-provenance candidate")
    receipt_value = _read_receipt_value(
        installed=installed,
        packet_id=packet_id,
        packet_payload=packet_payload,
        verified_packet=verified_packet,
        exchange_receipt_payload=exchange_receipt_payload,
        exchange_result=exchange_result,
    )
    outputs = {
        CONTROL_PACKET_FILENAME: packet_payload,
        SIGNER_ENROLLMENT_CERTIFICATE_FILENAME: verified_packet["signer_enrollment_certificate_payload"],
        SOURCE_ROLE_CONFIG_FILENAME: verified_packet["source_role_config_payload"],
        STATIC_ASSETS_PROVENANCE_FILENAME: verified_packet["static_assets_provenance_payload"],
        SOURCE_TRANSPORT_POLICY_FILENAME: verified_packet["source_transport_policy_payload"],
        READ_RECEIPT_FILENAME: canonical_json_bytes(receipt_value) + b"\n",
    }
    result = {
        "status": "installed" if apply else "planned",
        "campaign_id": campaign_id,
        "packet_id": packet_id,
        "candidate_directory": str(installed["candidate"]),
        "output_directory": str(output),
        "campaign_binding_path": str(binding_target),
        "campaign_binding_sha256": verified_packet["campaign_binding_sha256"],
        "control_packet_sha256": sha256_bytes(packet_payload),
        "exchange_object_key": exchange_result["object"]["object_key"],
        "exchange_object_version_id": exchange_result["object"]["version_id"],
        "files": {name: sha256_bytes(payload) for name, payload in sorted(outputs.items())},
    }
    if not apply:
        return result
    created_binding_target = _create_campaign_binding_target(campaign_id)
    if created_binding_target != binding_target:
        raise StaticProvenanceControlPacketInstallError("campaign binding target changed before creation")
    _require_absent(created_binding_target, field="campaign binding")
    binding_payload = verified_packet["campaign_binding_payload"]
    _write_new_root_private_file(created_binding_target, binding_payload, field="campaign binding")
    observed_binding = _read_root_private_file(
        created_binding_target,
        field="created campaign binding",
        maximum_bytes=packet_control.MAX_ARTIFACT_BYTES,
    )
    if observed_binding != binding_payload:
        raise StaticProvenanceControlPacketInstallError("created campaign binding changed before verification")
    try:
        if packet_control.binding_identity_from_payload(observed_binding) != verified_packet["campaign_binding"]:
            raise StaticProvenanceControlPacketInstallError("created campaign binding cannot be verified")
    except StaticProvenanceControlPacketInstallError:
        raise
    except Exception as exc:
        raise StaticProvenanceControlPacketInstallError("created campaign binding cannot be verified") from exc
    _fsync_root_private_directory(created_binding_target.parent, field="campaign source-phase directory")
    base = _create_or_require_root_private_directory(
        installed["candidate"],
        CONTROLLER_STATIC_PROVENANCE_DIRECTORY,
        field="candidate controller static-provenance directory",
    )
    output = _create_new_root_private_directory(base, packet_id, field="candidate static-provenance packet directory")
    for name, payload in outputs.items():
        _write_new_root_private_file(output / name, payload, field=f"candidate {name}")
    for name, payload in outputs.items():
        observed = _read_root_private_file(output / name, field=f"created candidate {name}", maximum_bytes=packet_control.MAX_PACKET_BYTES)
        if observed != payload:
            raise StaticProvenanceControlPacketInstallError(f"created candidate {name} changed before verification")
    _validate_read_receipt(outputs[READ_RECEIPT_FILENAME], expected=receipt_value)
    try:
        installer.verify_installed_source_adoption(Path(install_receipt))
    except Exception as exc:
        raise StaticProvenanceControlPacketInstallError("installed source-adoption candidate layout cannot be verified") from exc
    return result


def _print_result(value: Mapping[str, Any]) -> None:
    sys.stdout.buffer.write(canonical_json_bytes(value) + b"\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-receipt", required=True, type=Path)
    parser.add_argument("--received-directory", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = install_static_provenance_control_packet(
            install_receipt=args.install_receipt,
            received_directory=args.received_directory,
            apply=args.apply,
        )
        _print_result(result)
        return 0
    except StaticProvenanceControlPacketInstallError as exc:
        _print_result({"status": "blocked", "error": str(exc), "error_class": exc.__class__.__name__})
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
