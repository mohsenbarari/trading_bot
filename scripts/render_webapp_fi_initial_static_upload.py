#!/usr/bin/env python3
"""Render and verify the first WebApp-FI static-object exchange controls.

The source-adoption package carries a URL-free, immutable policy and request
for its first static archive.  This controller-local helper verifies those
package members against the local campaign binding and controller transport
configuration, then renders exact pinned-SSH commands for the two existing
WebApp-FI exchange operations:

* ``prepare-upload`` encrypts the already-prepared deterministic static
  archive and prints its URL-free expectation receipt; and
* ``upload-prepared`` receives one transient create-only PUT URL and prints
  its URL-free VersionId report.

It intentionally never opens SSH, creates an Object Storage client, reads
credentials, creates a payload, or writes a receipt.  Its CLI is render and
verify only.  An operator must separately and explicitly execute a rendered
command after the applicable external authorization.  The transient URL is
accepted only from stdin for the render-upload operation and is never written
to stdout except as the final remote argument in that one shell command.
"""

from __future__ import annotations

import argparse
import dataclasses
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


REMOTE_HOST = "root@65.109.220.59"
REMOTE_HOSTNAME = "65.109.220.59"
SSH_OPTIONS = (
    "-o",
    "BatchMode=yes",
    "-o",
    "PasswordAuthentication=no",
    "-o",
    "KbdInteractiveAuthentication=no",
    "-o",
    "NumberOfPasswordPrompts=0",
    "-o",
    "StrictHostKeyChecking=yes",
)

SOURCE_ADOPTION_INSTALL_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-adoption-install-receipt-v1"
SOURCE_EXCHANGE_PREPARED_SCHEMA = "gold-trade-webapp-fi-source-exchange-prepared-upload-v1"
SOURCE_EXCHANGE_UPLOAD_REPORT_SCHEMA = "gold-trade-webapp-fi-source-exchange-upload-report-v1"

PACKAGE_ARCHIVE_NAME = "webapp-fi-source-adoption.tar"
PREPARATION_RECEIPT_NAME = "source-adoption-preparation-receipt.json"
INITIAL_STATIC_POLICY_MEMBER = "config/initial-static-transport-policy.json"
INITIAL_STATIC_REQUEST_MEMBER = "config/initial-static-upload-request.json"
EXCHANGE_SCRIPT_MEMBER = "scripts/manage_webapp_fi_source_exchange.py"
# This is the exact artifact name emitted by the package's
# ``prepare_webapp_fi_static_assets.py`` producer.  The receiver may later
# normalize its local filename, but the FI prepare step must name the source
# artifact exactly.
STATIC_ARCHIVE_NAME = "mini_app_dist.tar"

# The bootstrap receiver already creates this root-only directory.  The
# initial package policy is required to use it, so the renderer never takes an
# FI plaintext or output directory from an operator.
FI_BOOTSTRAP_ROOT = Path("/srv/trading-bot-three-site-staging-data/webapp-fi-source-bootstrap")

MAX_CONTROL_BYTES = 2 * 1024 * 1024
MAX_KNOWN_HOSTS_BYTES = 256 * 1024
MAX_URL_BYTES = 8192

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


class InitialStaticControlError(RuntimeError):
    """One initial static control input is not safely bound."""


def _require_root_controlled_directory_chain(path: Path, *, field: str) -> None:
    """Reject symlinked or non-root-controlled lookup paths."""

    if not path.is_absolute():
        raise RuntimeError(f"{field} parent must be absolute")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:  # pragma: no cover - deployment invariant.
            raise RuntimeError(f"cannot inspect {field} parent") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        sticky_root_directory = bool(metadata.st_mode & stat.S_ISVTX)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or (mode & 0o022 and not sticky_root_directory)
        ):
            raise RuntimeError(f"{field} parent is not root-controlled")


def _require_root_controlled_code_file(path: Path, *, field: str) -> Path:
    """Return one exact root-owned non-writable sibling source file."""

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
    """Load a reviewed local helper without consulting ``sys.path``."""

    if not isinstance(filename, str) or Path(filename).name != filename or filename in {"", ".", ".."}:
        raise RuntimeError("required sibling filename is unsafe")
    source = _require_root_controlled_code_file(
        Path(__file__), field="initial static control renderer source"
    )
    path = _require_root_controlled_code_file(source.with_name(filename), field=f"required sibling {filename}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError(f"cannot load required sibling {filename}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        loaded = getattr(module, "__file__", None)
        if not isinstance(loaded, str) or Path(loaded).resolve(strict=True) != path:
            raise RuntimeError(f"required sibling {filename} did not load from its exact path")
    except BaseException:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
        raise
    return module


transport = _load_exact_sibling("manage_webapp_fi_source_transport.py", "_initial_static_control_transport")
preparer = _load_exact_sibling("prepare_webapp_fi_source_adoption.py", "_initial_static_control_preparer")
packet_control = _load_exact_sibling(
    "webapp_fi_static_provenance_control_packet.py", "_initial_static_control_packet"
)
exchange = _load_exact_sibling("manage_webapp_fi_source_exchange.py", "_initial_static_control_exchange")


@dataclasses.dataclass(frozen=True)
class InitialStaticControl:
    """Fully verified public controls for one initial FI static object."""

    controller_config: Any
    policy: Any
    request: Any
    campaign_binding: Any
    package_id: str
    candidate_directory: Path
    fi_install_receipt_sha256: str
    prepared_directory: Path
    static_archive: Path


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InitialStaticControlError("JSON input contains duplicate keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise InitialStaticControlError(f"JSON input contains unsupported constant: {value}")


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise InitialStaticControlError("initial static control operations must run as root")


def _require_absolute_canonical(path: Path, *, field: str) -> Path:
    candidate = Path(path)
    if (
        "\x00" in str(candidate)
        or not candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts[1:])
        or str(candidate) != os.path.normpath(str(candidate))
    ):
        raise InitialStaticControlError(f"{field} must be one canonical absolute path")
    return candidate


def _require_root_controlled_ancestors(path: Path, *, field: str) -> None:
    path = _require_absolute_canonical(path, field=field)
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise InitialStaticControlError(f"cannot inspect {field} parent") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or (mode & 0o022 and not metadata.st_mode & stat.S_ISVTX)
        ):
            raise InitialStaticControlError(f"{field} parent is unsafe")


def _read_root_controlled_file(
    path: Path,
    *,
    field: str,
    maximum_bytes: int,
    private: bool,
) -> bytes:
    path = _require_absolute_canonical(path, field=field)
    _require_root_controlled_ancestors(path.parent, field=field)
    try:
        before = path.lstat()
    except OSError as exc:
        raise InitialStaticControlError(f"cannot inspect {field}") from exc
    mode = stat.S_IMODE(before.st_mode)
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or before.st_nlink != 1
        or (mode & (0o077 if private else 0o022))
        or not 1 <= before.st_size <= maximum_bytes
    ):
        raise InitialStaticControlError(f"{field} is unsafe")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise InitialStaticControlError(f"cannot securely open {field}") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_mode != before.st_mode
            or opened.st_uid != before.st_uid
            or opened.st_size != before.st_size
            or opened.st_nlink != before.st_nlink
        ):
            raise InitialStaticControlError(f"{field} changed while opening")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise InitialStaticControlError(f"{field} exceeds its size bound")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if total != opened.st_size or after.st_dev != opened.st_dev or after.st_ino != opened.st_ino:
            raise InitialStaticControlError(f"{field} changed while reading")
        return b"".join(chunks)
    except OSError as exc:
        raise InitialStaticControlError(f"cannot read {field}") from exc
    finally:
        os.close(descriptor)


def _parse_canonical_json(payload: bytes, *, field: str) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_CONTROL_BYTES:
        raise InitialStaticControlError(f"{field} has an unsafe size")
    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_strict_object, parse_constant=_reject_json_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InitialStaticControlError(f"{field} is not strict canonical JSON") from exc
    if not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise InitialStaticControlError(f"{field} is not canonical JSON")
    lowered = payload.lower()
    if b"://" in lowered or b'"url"' in lowered or b"presigned" in lowered or b"x-amz-signature" in lowered:
        raise InitialStaticControlError(f"{field} persists a forbidden transient URL")
    return value


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise InitialStaticControlError(f"{field} is invalid")
    return value


def _require_timestamp(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not UTC_TIMESTAMP_RE.fullmatch(value):
        raise InitialStaticControlError(f"{field} is invalid")
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:  # pragma: no cover - regex above is only a lexical gate.
        raise InitialStaticControlError(f"{field} is invalid") from exc
    if parsed.tzinfo is not None:  # pragma: no cover - strptime returns naive values here.
        raise InitialStaticControlError(f"{field} is invalid")
    return value


def _request_value(request: Any) -> dict[str, Any]:
    return {
        "campaign_id": request.campaign_id,
        "release_sha": request.release_sha,
        "control_commit": request.control_commit,
        "control_tree": request.control_tree,
        "source_site": request.source_site,
        "destination_site": request.destination_site,
        "object_kind": request.object_kind,
        "object_id": request.object_id,
        "recipient_mode": request.mode,
        "recipients": list(request.recipients),
    }


def _exchange_policy_from_control(policy: Any) -> Any:
    """Bridge public values between independently loaded pure contracts.

    The controller and FI exchange intentionally load their contract modules
    by exact path.  Their dataclass identities therefore differ even though
    their public fields are identical; reconstruct the FI-side value rather
    than weakening either module's ``isinstance`` validation.
    """

    return exchange.contract.SourceTransportPolicy(
        endpoint=policy.endpoint,
        region=policy.region,
        bucket=policy.bucket,
        prefix=policy.prefix,
        age_binary=policy.age_binary,
        workspace=Path(policy.workspace),
        controller_age_recipient=policy.controller_age_recipient,
        webapp_fi_age_recipient=policy.webapp_fi_age_recipient,
        webapp_ir_age_recipient=policy.webapp_ir_age_recipient,
        maximum_plaintext_bytes=policy.maximum_plaintext_bytes,
    )


def _request_from_value(value: object, *, policy: Any, field: str) -> Any:
    expected = {
        "campaign_id",
        "release_sha",
        "control_commit",
        "control_tree",
        "source_site",
        "destination_site",
        "object_kind",
        "object_id",
        "recipient_mode",
        "recipients",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise InitialStaticControlError(f"{field} has an unsupported schema")
    recipients = value.get("recipients")
    if not isinstance(recipients, list):
        raise InitialStaticControlError(f"{field} recipients are invalid")
    request = transport.SourceObjectRequest(
        campaign_id=value.get("campaign_id"),
        release_sha=value.get("release_sha"),
        control_commit=value.get("control_commit"),
        control_tree=value.get("control_tree"),
        source_site=value.get("source_site"),
        destination_site=value.get("destination_site"),
        object_kind=value.get("object_kind"),
        object_id=value.get("object_id"),
        mode=value.get("recipient_mode"),
        recipients=tuple(recipients),
    )
    try:
        transport.validate_request(policy, request)
    except Exception as exc:
        raise InitialStaticControlError(f"{field} violates the source transport contract") from exc
    return request


def _load_initial_package_members(
    *,
    source_transport_config: Path,
    campaign_binding_path: Path,
    source_adoption_package_directory: Path,
    preparation_receipt: Path,
) -> tuple[Any, Any, Any, dict[str, bytes], dict[str, Any]]:
    """Verify and parse package-only controls before considering FI output."""

    _require_root_execution()
    try:
        controller_config = transport.load_controller_config(Path(source_transport_config))
        campaign_binding = transport.campaign_binding.load_campaign_binding(Path(campaign_binding_path))
        prepared = preparer.verify_prepared_source_adoption_package(
            package_directory=Path(source_adoption_package_directory),
            preparation_receipt=Path(preparation_receipt),
            expected_control_commit=campaign_binding.control_commit,
            expected_application_release_sha=campaign_binding.application_release_sha,
        )
    except Exception as exc:
        raise InitialStaticControlError("controller package, transport, or campaign binding is invalid") from exc
    package_directory = Path(prepared["package_directory"])
    try:
        members = preparer._read_archive_members(package_directory / PACKAGE_ARCHIVE_NAME)
    except Exception as exc:
        raise InitialStaticControlError("source-adoption package members are invalid") from exc
    try:
        policy_raw = members[INITIAL_STATIC_POLICY_MEMBER]
        request_raw = members[INITIAL_STATIC_REQUEST_MEMBER]
        canonical_release_tree_raw = members[preparer.CANONICAL_RELEASE_TREE_MEMBER]
    except KeyError as exc:
        raise InitialStaticControlError("source-adoption package lacks initial static controls") from exc
    try:
        canonical_release_tree = preparer._validate_canonical_release_tree_descriptor(canonical_release_tree_raw)
    except Exception as exc:
        raise InitialStaticControlError("source-adoption package canonical release tree is invalid") from exc
    if (
        canonical_release_tree["application"]["release_sha"] != campaign_binding.application_release_sha
        or canonical_release_tree["application"]["git_tree"] != campaign_binding.application_release_tree
    ):
        raise InitialStaticControlError("source-adoption package canonical release tree is not bound to the campaign")
    try:
        policy_projection, projected_raw, _policy_sha = packet_control.source_transport_policy_from_payload(policy_raw)
    except Exception as exc:
        raise InitialStaticControlError("initial static package policy is invalid") from exc
    if projected_raw != policy_raw:
        raise InitialStaticControlError("initial static package policy is not the required URL-free projection")
    try:
        policy = transport.SourceTransportPolicy(
            endpoint="https://" + str(policy_projection["endpoint_host"]),
            region=policy_projection["region"],
            bucket=policy_projection["bucket"],
            prefix=policy_projection["prefix"],
            age_binary=policy_projection["age_binary"],
            workspace=Path(policy_projection["workspace"]),
            controller_age_recipient=policy_projection["controller_age_recipient"],
            webapp_fi_age_recipient=policy_projection["webapp_fi_age_recipient"],
            webapp_ir_age_recipient=policy_projection["webapp_ir_age_recipient"],
            maximum_plaintext_bytes=policy_projection["maximum_plaintext_bytes"],
        )
        policy = transport.contract.validate_policy(policy)
    except Exception as exc:
        raise InitialStaticControlError("initial static package policy cannot be used by the exchange") from exc
    request_value = _parse_canonical_json(request_raw, field="initial static package request")
    request = _request_from_value(request_value, policy=policy, field="initial static package request")
    if (
        policy.endpoint != controller_config.policy.endpoint
        or policy.region != controller_config.policy.region
        or policy.bucket != controller_config.policy.bucket
        or policy.prefix != controller_config.policy.prefix
        or policy.controller_age_recipient != controller_config.policy.controller_age_recipient
        or policy.webapp_fi_age_recipient != controller_config.policy.webapp_fi_age_recipient
        or policy.webapp_ir_age_recipient != controller_config.policy.webapp_ir_age_recipient
        or policy.maximum_plaintext_bytes != controller_config.policy.maximum_plaintext_bytes
        or policy.age_binary != "/usr/bin/age"
        or policy.workspace != FI_BOOTSTRAP_ROOT
    ):
        raise InitialStaticControlError("initial static package policy differs from controller-pinned transport")
    expected_request = transport.SourceObjectRequest(
        campaign_id=campaign_binding.campaign_id,
        release_sha=campaign_binding.application_release_sha,
        control_commit=campaign_binding.control_commit,
        control_tree=campaign_binding.control_tree,
        source_site="webapp_fi",
        destination_site=transport.STATIC_DESTINATION_SITE,
        object_kind=transport.STATIC_OBJECT_KIND,
        object_id=request.object_id,
        mode=transport.STATIC_MODE,
        recipients=(policy.controller_age_recipient, policy.webapp_ir_age_recipient),
    )
    try:
        transport.validate_request(policy, expected_request)
        expected_payloads = preparer._initial_static_bootstrap_payloads(
            source_transport_config=Path(source_transport_config),
            campaign_binding_path=Path(campaign_binding_path),
            initial_static_object_id=request.object_id,
            application_release_sha=campaign_binding.application_release_sha,
            application_release_tree=campaign_binding.application_release_tree,
            expected_alembic_revision=campaign_binding.expected_alembic_revision,
            control_commit=campaign_binding.control_commit,
            control_tree=campaign_binding.control_tree,
        )
    except Exception as exc:
        raise InitialStaticControlError("initial static package controls do not reproduce from controller inputs") from exc
    if (
        _request_value(request) != _request_value(expected_request)
        or expected_payloads.get(INITIAL_STATIC_POLICY_MEMBER) != policy_raw
        or expected_payloads.get(INITIAL_STATIC_REQUEST_MEMBER) != request_raw
    ):
        raise InitialStaticControlError("initial static package controls are not bound to the exact campaign")
    expected_files = {
        relative: preparer.sha256_bytes(members[relative]) for relative in preparer.PACKAGE_PAYLOAD_FILES
    }
    return controller_config, policy, campaign_binding, members, {
        "prepared": prepared,
        "request": request,
        "files": expected_files,
    }


def _validate_fi_install_receipt(
    *,
    receipt_path: Path,
    package: Mapping[str, Any],
    campaign_binding: Any,
) -> tuple[Path, str]:
    """Validate the URL-free FI control receipt without opening any FI path."""

    payload = _read_root_controlled_file(
        Path(receipt_path), field="opaque FI source-adoption install receipt", maximum_bytes=MAX_CONTROL_BYTES, private=True
    )
    value = _parse_canonical_json(payload, field="opaque FI source-adoption install receipt")
    expected = {
        "schema",
        "status",
        "installed_at",
        "candidate_directory",
        "source_site",
        "destination_site",
        "campaign_id",
        "package_id",
        "application",
        "tooling",
        "files",
        "canonical_release_tree_sha256",
        "package",
        "receipt_sha256",
    }
    if (
        set(value) != expected
        or value.get("schema") != SOURCE_ADOPTION_INSTALL_RECEIPT_SCHEMA
        or value.get("status") != "installed"
    ):
        raise InitialStaticControlError("opaque FI source-adoption install receipt is unsupported")
    receipt_sha = _require_sha256(value.get("receipt_sha256"), field="opaque FI install receipt checksum")
    unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if receipt_sha != sha256_bytes(canonical_json_bytes(unsigned)):
        raise InitialStaticControlError("opaque FI source-adoption install receipt checksum is invalid")
    _require_timestamp(value.get("installed_at"), field="opaque FI install receipt timestamp")
    candidate_text = value.get("candidate_directory")
    if not isinstance(candidate_text, str):
        raise InitialStaticControlError("opaque FI install candidate is invalid")
    candidate = _require_absolute_canonical(Path(candidate_text), field="opaque FI install candidate")
    expected_candidate_name = "installed-" + campaign_binding.control_commit + "-" + package["prepared"]["package_id"]
    if candidate.parent != FI_BOOTSTRAP_ROOT or candidate.name != expected_candidate_name:
        raise InitialStaticControlError("opaque FI install candidate is not the fixed bootstrap candidate")
    if (
        value.get("source_site") != "bot_fi"
        or value.get("destination_site") != "webapp_fi"
        or value.get("campaign_id") != campaign_binding.campaign_id
        or value.get("package_id") != package["prepared"]["package_id"]
        or value.get("application") != package["prepared"]["application"]
        or value.get("tooling") != package["prepared"]["tooling"]
        or value.get("canonical_release_tree_sha256") != package["prepared"]["canonical_release_tree_sha256"]
        or value.get("files") != package["files"]
    ):
        raise InitialStaticControlError("opaque FI install receipt is not bound to the controller package")
    package_value = value.get("package")
    package_expected = {
        "archive_sha256",
        "archive_bytes",
        "preparation_receipt_sha256",
        "delivery_receipt_sha256",
        "delivery_envelope_sha256",
        "controller_public_key_base64",
        "fi_bootstrap_recipient",
        "object_key",
        "version_id",
        "ciphertext_sha256",
        "ciphertext_bytes",
    }
    if not isinstance(package_value, Mapping) or set(package_value) != package_expected:
        raise InitialStaticControlError("opaque FI install receipt package binding is invalid")
    if (
        package_value.get("archive_sha256") != package["prepared"]["archive_sha256"]
        or package_value.get("archive_bytes") != package["prepared"]["archive_bytes"]
        or package_value.get("preparation_receipt_sha256") != package["prepared"]["preparation_receipt_sha256"]
    ):
        raise InitialStaticControlError("opaque FI install receipt package does not match local preparation")
    for field in (
        "delivery_receipt_sha256",
        "delivery_envelope_sha256",
        "ciphertext_sha256",
    ):
        _require_sha256(package_value.get(field), field=f"opaque FI install receipt package {field}")
    if (
        isinstance(package_value.get("archive_bytes"), bool)
        or not isinstance(package_value.get("archive_bytes"), int)
        or isinstance(package_value.get("ciphertext_bytes"), bool)
        or not isinstance(package_value.get("ciphertext_bytes"), int)
        or package_value["ciphertext_bytes"] < 1
        or not isinstance(package_value.get("object_key"), str)
        or not isinstance(package_value.get("version_id"), str)
        or not isinstance(package_value.get("controller_public_key_base64"), str)
        or package_value.get("fi_bootstrap_recipient") is None
    ):
        raise InitialStaticControlError("opaque FI install receipt package fields are invalid")
    return candidate, sha256_bytes(payload)


def build_initial_static_control(
    *,
    source_transport_config: Path,
    campaign_binding: Path,
    source_adoption_package_directory: Path,
    preparation_receipt: Path,
    fi_install_receipt: Path,
) -> InitialStaticControl:
    """Build all local-only bindings needed for the two FI exchange commands."""

    controller_config, policy, bound, _members, package = _load_initial_package_members(
        source_transport_config=Path(source_transport_config),
        campaign_binding_path=Path(campaign_binding),
        source_adoption_package_directory=Path(source_adoption_package_directory),
        preparation_receipt=Path(preparation_receipt),
    )
    candidate, install_sha = _validate_fi_install_receipt(
        receipt_path=Path(fi_install_receipt), package=package, campaign_binding=bound
    )
    request = package["request"]
    prepared_directory = policy.workspace / ("initial-static-upload-" + request.object_id)
    static_archive = policy.workspace / ("initial-static-assets-" + request.object_id) / STATIC_ARCHIVE_NAME
    return InitialStaticControl(
        controller_config=controller_config,
        policy=policy,
        request=request,
        campaign_binding=bound,
        package_id=package["prepared"]["package_id"],
        candidate_directory=candidate,
        fi_install_receipt_sha256=install_sha,
        prepared_directory=prepared_directory,
        static_archive=static_archive,
    )


def _require_pinned_known_hosts(path: Path) -> Path:
    """Require a root-owned known_hosts file that pins the exact FI host."""

    payload = _read_root_controlled_file(
        Path(path), field="pinned FI SSH known_hosts", maximum_bytes=MAX_KNOWN_HOSTS_BYTES, private=False
    )
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise InitialStaticControlError("pinned FI SSH known_hosts is not ASCII") from exc
    found = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) < 3 or fields[0].startswith("@"):
            continue
        hosts, key_type, key = fields[:3]
        if key_type.startswith("ssh-") or key_type.startswith("ecdsa-"):
            host_values = set(hosts.split(","))
            if REMOTE_HOSTNAME in host_values or f"[{REMOTE_HOSTNAME}]:22" in host_values:
                if re.fullmatch(r"[A-Za-z0-9+/=]{16,16384}", key):
                    found = True
    if not found:
        raise InitialStaticControlError("pinned FI SSH known_hosts lacks the exact FI host key")
    return _require_absolute_canonical(Path(path), field="pinned FI SSH known_hosts")


def _render_pinned_ssh(*, known_hosts: Path, remote_arguments: Sequence[str]) -> str:
    if not remote_arguments or any(not isinstance(item, str) or not item for item in remote_arguments):
        raise InitialStaticControlError("remote static control arguments are invalid")
    known_hosts = _require_pinned_known_hosts(known_hosts)
    remote = shlex.join(list(remote_arguments))
    return shlex.join(
        [
            "ssh",
            *SSH_OPTIONS,
            "-o",
            "UserKnownHostsFile=" + str(known_hosts),
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            REMOTE_HOST,
            remote,
        ]
    )


def render_prepare_command(*, control: InitialStaticControl, fi_known_hosts: Path) -> str:
    """Render, but never execute, the fixed FI encryption/prepare operation."""

    if not isinstance(control, InitialStaticControl):
        raise InitialStaticControlError("initial static control is unsupported")
    candidate = control.candidate_directory
    remote = [
        "/usr/bin/python3",
        "-I",
        "-B",
        str(candidate / EXCHANGE_SCRIPT_MEMBER),
        "prepare-upload",
        "--policy",
        str(candidate / INITIAL_STATIC_POLICY_MEMBER),
        "--request",
        str(candidate / INITIAL_STATIC_REQUEST_MEMBER),
        "--plaintext",
        str(control.static_archive),
        "--prepared-dir",
        str(control.prepared_directory),
    ]
    return _render_pinned_ssh(known_hosts=Path(fi_known_hosts), remote_arguments=remote)


def _read_prepared_receipt(path: Path, *, control: InitialStaticControl) -> dict[str, Any]:
    payload = _read_root_controlled_file(
        Path(path), field="FI initial static prepared receipt", maximum_bytes=MAX_CONTROL_BYTES, private=True
    )
    try:
        request, recipients, plaintext, ciphertext, prepared_sha = exchange._verify_prepared_receipt(
            policy=_exchange_policy_from_control(control.policy), payload=payload
        )
    except Exception as exc:
        raise InitialStaticControlError("FI initial static prepared receipt is invalid") from exc
    if (
        _request_value(request) != _request_value(control.request)
        or tuple(recipients)
        != (control.policy.controller_age_recipient, control.policy.webapp_ir_age_recipient)
    ):
        raise InitialStaticControlError("FI initial static prepared receipt is not bound to the package request")
    value = _parse_canonical_json(payload, field="FI initial static prepared receipt")
    if value.get("schema") != SOURCE_EXCHANGE_PREPARED_SCHEMA or value.get("status") != "prepared":
        raise InitialStaticControlError("FI initial static prepared receipt is unsupported")
    return {
        "request": request,
        "plaintext": plaintext,
        "ciphertext": ciphertext,
        "prepared_sha256": prepared_sha,
        "receipt_sha256": sha256_bytes(payload),
    }


def validate_prepared_receipt(*, control: InitialStaticControl, prepared_receipt: Path) -> dict[str, Any]:
    """Return the exact URL-free expectation a controller may later presign."""

    value = _read_prepared_receipt(Path(prepared_receipt), control=control)
    return {
        "status": "verified",
        "campaign_id": control.request.campaign_id,
        "object_key": transport.source_object_key(control.policy, control.request),
        "recipient_mode": control.request.mode,
        "recipients": list(control.request.recipients),
        "plaintext": dict(value["plaintext"]),
        "ciphertext": dict(value["ciphertext"]),
        "prepared_receipt_sha256": value["receipt_sha256"],
    }


def render_upload_command(
    *,
    control: InitialStaticControl,
    fi_known_hosts: Path,
    prepared_receipt: Path,
    presigned_upload_url: str,
) -> str:
    """Render, but never execute, one exact FI create-only static PUT."""

    _read_prepared_receipt(Path(prepared_receipt), control=control)
    try:
        upload_url = transport.require_create_only_presigned_put_url(
            presigned_upload_url,
            policy=control.policy,
            object_key=transport.source_object_key(control.policy, control.request),
        )
    except Exception as exc:
        raise InitialStaticControlError("initial static presigned upload URL is invalid") from exc
    candidate = control.candidate_directory
    remote = [
        "/usr/bin/python3",
        "-I",
        "-B",
        str(candidate / EXCHANGE_SCRIPT_MEMBER),
        "upload-prepared",
        "--policy",
        str(candidate / INITIAL_STATIC_POLICY_MEMBER),
        "--prepared-dir",
        str(control.prepared_directory),
        "--upload-url",
        upload_url,
    ]
    return _render_pinned_ssh(known_hosts=Path(fi_known_hosts), remote_arguments=remote)


def validate_upload_report(
    *,
    control: InitialStaticControl,
    prepared_receipt: Path,
    upload_report: Path,
) -> dict[str, Any]:
    """Verify an FI report before the controller performs exact S3 read-back."""

    prepared = _read_prepared_receipt(Path(prepared_receipt), control=control)
    payload = _read_root_controlled_file(
        Path(upload_report), field="FI initial static upload report", maximum_bytes=MAX_CONTROL_BYTES, private=True
    )
    try:
        report = exchange.verify_upload_report(policy=_exchange_policy_from_control(control.policy), payload=payload)
    except Exception as exc:
        raise InitialStaticControlError("FI initial static upload report is invalid") from exc
    report_request = _request_from_value(
        report["request"], policy=control.policy, field="FI initial static upload report request"
    )
    if _request_value(report_request) != _request_value(control.request):
        raise InitialStaticControlError("FI initial static upload report is not bound to the package request")
    descriptor = report["object"]
    if (
        descriptor.get("plaintext_sha256") != prepared["plaintext"]["sha256"]
        or descriptor.get("plaintext_bytes") != prepared["plaintext"]["bytes"]
        or descriptor.get("ciphertext_sha256") != prepared["ciphertext"]["sha256"]
        or descriptor.get("ciphertext_bytes") != prepared["ciphertext"]["bytes"]
        or descriptor.get("object_key") != transport.source_object_key(control.policy, control.request)
    ):
        raise InitialStaticControlError("FI initial static upload report differs from the prepared expectation")
    value = _parse_canonical_json(payload, field="FI initial static upload report")
    if value.get("schema") != SOURCE_EXCHANGE_UPLOAD_REPORT_SCHEMA or value.get("status") != "uploaded-awaiting-controller-readback":
        raise InitialStaticControlError("FI initial static upload report is unsupported")
    return {
        "status": "verified",
        "campaign_id": control.request.campaign_id,
        "object": dict(descriptor),
        "upload_report_sha256": sha256_bytes(payload),
        "controller_readback_required": True,
    }


def _read_presigned_url_stdin() -> str:
    try:
        payload = sys.stdin.buffer.read(MAX_URL_BYTES + 1)
    except OSError as exc:
        raise InitialStaticControlError("cannot read initial static presigned upload URL from stdin") from exc
    if not payload or len(payload) > MAX_URL_BYTES:
        raise InitialStaticControlError("initial static presigned upload URL stdin exceeds the fixed size bound")
    if payload.endswith(b"\n"):
        payload = payload[:-1]
    if not payload or b"\n" in payload or b"\r" in payload:
        raise InitialStaticControlError("initial static presigned upload URL stdin is malformed")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InitialStaticControlError("initial static presigned upload URL stdin is not UTF-8") from exc


def _base_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-transport-config", required=True, type=Path)
    parser.add_argument("--campaign-binding", required=True, type=Path)
    parser.add_argument("--source-adoption-package-directory", required=True, type=Path)
    parser.add_argument("--preparation-receipt", required=True, type=Path)
    parser.add_argument("--fi-install-receipt", required=True, type=Path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    prepare = actions.add_parser("render-prepare", help="render one pinned FI prepare-upload SSH command")
    _base_arguments(prepare)
    prepare.add_argument("--fi-known-hosts", required=True, type=Path)
    verify_prepared = actions.add_parser("verify-prepared", help="verify one URL-free FI prepared receipt")
    _base_arguments(verify_prepared)
    verify_prepared.add_argument("--prepared-receipt", required=True, type=Path)
    upload = actions.add_parser("render-upload", help="render one pinned FI upload-prepared SSH command")
    _base_arguments(upload)
    upload.add_argument("--fi-known-hosts", required=True, type=Path)
    upload.add_argument("--prepared-receipt", required=True, type=Path)
    upload.add_argument("--presigned-upload-url-stdin", action="store_true", required=True)
    verify_upload = actions.add_parser("verify-upload", help="verify one URL-free FI upload report")
    _base_arguments(verify_upload)
    verify_upload.add_argument("--prepared-receipt", required=True, type=Path)
    verify_upload.add_argument("--upload-report", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        control = build_initial_static_control(
            source_transport_config=args.source_transport_config,
            campaign_binding=args.campaign_binding,
            source_adoption_package_directory=args.source_adoption_package_directory,
            preparation_receipt=args.preparation_receipt,
            fi_install_receipt=args.fi_install_receipt,
        )
        if args.action == "render-prepare":
            print(render_prepare_command(control=control, fi_known_hosts=args.fi_known_hosts))
        elif args.action == "verify-prepared":
            print(json.dumps(validate_prepared_receipt(control=control, prepared_receipt=args.prepared_receipt), sort_keys=True))
        elif args.action == "render-upload":
            print(
                render_upload_command(
                    control=control,
                    fi_known_hosts=args.fi_known_hosts,
                    prepared_receipt=args.prepared_receipt,
                    presigned_upload_url=_read_presigned_url_stdin(),
                )
            )
        elif args.action == "verify-upload":
            print(
                json.dumps(
                    validate_upload_report(
                        control=control,
                        prepared_receipt=args.prepared_receipt,
                        upload_report=args.upload_report,
                    ),
                    sort_keys=True,
                )
            )
        else:  # pragma: no cover - argparse dispatch invariant.
            raise InitialStaticControlError("unsupported initial static control action")
        return 0
    except InitialStaticControlError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - CLI entry point.
    raise SystemExit(main())
