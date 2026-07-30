#!/usr/bin/env python3
"""Render and verify fixed WebApp-FI post-packet source uploads.

This controller-local program has no SSH, Object Storage, Docker, service,
container, volume, current, migration, or data-plane execution path.  It
proves that an FI static-provenance packet was locally sealed by the canonical
campaign authority and later installed on FI, then renders only pinned SSH
commands for the two permitted post-packet source objects:

* ``raw-app-image``; and
* ``source-evidence``.

The strict enum and one identifier are the only upload-specific inputs.  The
FI helper derives the route, controller-only recipient, policy, plaintext
path, and prepared directory again from the installed static packet.  A PUT
URL is accepted only from stdin for the rendering invocation and is emitted
only as the final transient FI command argument.  Receipts and reports must
be canonical, root-only, URL-free, and nonsecret.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit


FI_POST_PACKET_HELPER_MEMBER = "scripts/prepare_webapp_fi_post_packet_upload.py"
EXCHANGE_MEMBER = "scripts/manage_webapp_fi_source_exchange.py"
CONTROL_PACKET_DIRECTORY = "controller-static-provenance"
SOURCE_TRANSPORT_POLICY_NAME = "source-transport-policy.json"

RAW_APP_IMAGE = "raw-app-image"
SOURCE_EVIDENCE = "source-evidence"
ARTIFACT_KINDS = frozenset((RAW_APP_IMAGE, SOURCE_EVIDENCE))

STATIC_PACKET_INSTALL_RECEIPT_SCHEMA = "gold-trade-webapp-fi-static-provenance-install-receipt-v1"
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_REPORT_MARKERS = (
    b"://",
    b'"url"',
    b"presigned",
    b"credential",
    b"access_key",
    b"secret",
    b"private_key",
    b"session_token",
    b"password",
)


class PostPacketUploadControlError(RuntimeError):
    """A controller post-packet FI upload control is unsafe or unbound."""


@dataclasses.dataclass(frozen=True)
class PostPacketUploadControl:
    """All controller-verified facts for one fixed FI post-packet upload."""

    controller_config: Any
    policy: Any
    request: Any
    campaign_binding: Any
    packet_id: str
    artifact_kind: str
    artifact_id: str
    fi_candidate_directory: Path
    fi_packet_directory: Path
    prepared_directory: Path
    control_packet_sha256: str
    static_packet_receipt_sha256: str
    source_role_config_sha256: str


def _load_exact_sibling(filename: str, module_name: str) -> Any:
    if not isinstance(filename, str) or not filename or Path(filename).name != filename or filename in {".", ".."}:
        raise RuntimeError("required sibling filename is invalid")
    source = Path(__file__).absolute()
    path = source.with_name(filename)
    if not source.is_absolute() or not path.is_absolute():  # pragma: no cover - Python invariant.
        raise RuntimeError("controller post-packet renderer source is not absolute")
    current = Path(source.anchor)
    for component in path.parts[1:-1]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:  # pragma: no cover - repository layout invariant.
            raise RuntimeError("cannot inspect required sibling parent") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or ((mode & 0o022) and not (metadata.st_mode & stat.S_ISVTX))
        ):
            raise RuntimeError("required sibling parent is not root-controlled")
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        opened = resolved.lstat()
    except OSError as exc:  # pragma: no cover - repository layout invariant.
        raise RuntimeError("cannot inspect required sibling") from exc
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or stat.S_ISLNK(opened.st_mode)
        or not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != 0
        or opened.st_nlink != 1
        or stat.S_IMODE(opened.st_mode) & 0o022
        or opened.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
    ):
        raise RuntimeError("required sibling is not a root-owned non-writable regular non-symlink file")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError("cannot load required sibling")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        loaded = getattr(module, "__file__", None)
        if not isinstance(loaded, str) or Path(loaded).resolve(strict=True) != path:
            raise RuntimeError("required sibling did not load from its exact path")
    except BaseException:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
        raise
    return module


initial = _load_exact_sibling(
    "render_webapp_fi_initial_static_upload.py",
    "_webapp_fi_post_packet_initial_renderer",
)
role_config = _load_exact_sibling(
    "render_webapp_fi_source_role_config.py",
    "_webapp_fi_post_packet_role_config",
)
packet_control = _load_exact_sibling(
    "webapp_fi_static_provenance_control_packet.py",
    "_webapp_fi_post_packet_contract",
)
packet_builder = _load_exact_sibling(
    "build_webapp_fi_static_provenance_control_packet.py",
    "_webapp_fi_post_packet_builder",
)


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise PostPacketUploadControlError("controller post-packet FI upload controls must run as root")


def _same_binding(left: Any, right: Any) -> bool:
    return all(
        getattr(left, field, None) == getattr(right, field, None)
        for field in (
            "campaign_id",
            "application_release_sha",
            "application_release_tree",
            "expected_alembic_revision",
            "control_commit",
            "control_tree",
            "binding_sha256",
        )
    )


def _require_artifact_kind(value: object) -> str:
    if not isinstance(value, str) or value not in ARTIFACT_KINDS:
        raise PostPacketUploadControlError("artifact_kind must be raw-app-image or source-evidence")
    return value


def _require_artifact_id(value: object) -> str:
    try:
        return packet_control._require_identifier(value, field="post-packet artifact ID")
    except Exception as exc:
        raise PostPacketUploadControlError("artifact_id is invalid") from exc


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise PostPacketUploadControlError(f"{field} is invalid")
    return value


def _load_local_binding(path: Path) -> tuple[Any, bytes, Mapping[str, Any]]:
    """Load only the canonical controller binding selected by its own root."""

    try:
        binding = initial.transport.campaign_binding.load_campaign_binding(Path(path))
        expected = packet_builder.campaign_binding_path(binding.campaign_id)
        raw = initial._read_root_controlled_file(
            Path(path),
            field="canonical controller campaign binding",
            maximum_bytes=initial.MAX_CONTROL_BYTES,
            private=True,
        )
        identity = packet_control.binding_identity_from_payload(raw)
    except Exception as exc:
        raise PostPacketUploadControlError("canonical controller campaign binding is invalid") from exc
    if Path(path) != expected:
        raise PostPacketUploadControlError("campaign binding is not at the fixed controller campaign path")
    if (
        identity.get("campaign_id") != binding.campaign_id
        or identity.get("binding_sha256") != binding.binding_sha256
        or identity.get("application")
        != {
            "release_sha": binding.application_release_sha,
            "release_tree": binding.application_release_tree,
            "expected_alembic_revision": binding.expected_alembic_revision,
        }
        or identity.get("tooling")
        != {"control_commit": binding.control_commit, "control_tree": binding.control_tree}
    ):
        raise PostPacketUploadControlError("canonical controller campaign binding changed while being read")
    return binding, raw, identity


def _load_role_config(*, binding_path: Path, expected_binding: Any, role_path: Path) -> tuple[bytes, str]:
    try:
        if Path(role_path) != Path(binding_path).with_name("source-role-config.json"):
            raise PostPacketUploadControlError("FI source role config is not at the campaign-bound fixed path")
        role_binding = role_config.binding.load_campaign_binding(Path(binding_path))
        if not _same_binding(role_binding, expected_binding):
            raise PostPacketUploadControlError("FI source role config binding differs from controller authority")
        normalized = role_config.load_source_role_config(path=Path(role_path), campaign_binding=role_binding)
        raw = role_config._read_private_file(Path(role_path), field="FI source role config")
    except PostPacketUploadControlError:
        raise
    except Exception as exc:
        raise PostPacketUploadControlError("FI source role config is invalid") from exc
    expected = role_config.canonical_json_bytes(normalized) + b"\n"
    if raw != expected:
        raise PostPacketUploadControlError("FI source role config changed while being verified")
    return raw, sha256_bytes(raw)


def _load_controller_authority(*, binding_path: Path, expected_binding: Any) -> str:
    try:
        authority = packet_builder._load_campaign_bound_controller_signer(Path(binding_path))
        authority_binding = authority.campaign_binding
        public = authority.signing_key.public_key_base64
        packet_control.public_key_id(public)
    except Exception as exc:
        raise PostPacketUploadControlError("controller static-packet signing authority is invalid") from exc
    if not _same_binding(authority_binding, expected_binding):
        raise PostPacketUploadControlError("controller static-packet signing authority is not campaign-bound")
    return public


def _validate_packet_policy(*, controller_policy: Any, packet_policy: Mapping[str, Any]) -> None:
    try:
        endpoint = urlsplit(controller_policy.endpoint)
        expected_host = endpoint.hostname
    except Exception as exc:  # pragma: no cover - controller config validates this first.
        raise PostPacketUploadControlError("controller source transport endpoint is invalid") from exc
    expected = {
        "schema": packet_control.SOURCE_TRANSPORT_POLICY_SCHEMA,
        "endpoint_host": expected_host,
        "region": controller_policy.region,
        "bucket": controller_policy.bucket,
        "prefix": controller_policy.prefix,
        "age_binary": controller_policy.age_binary,
        "workspace": str(controller_policy.workspace),
        "controller_age_recipient": controller_policy.controller_age_recipient,
        "webapp_fi_age_recipient": controller_policy.webapp_fi_age_recipient,
        "webapp_ir_age_recipient": controller_policy.webapp_ir_age_recipient,
        "maximum_plaintext_bytes": controller_policy.maximum_plaintext_bytes,
    }
    if dict(packet_policy) != expected:
        raise PostPacketUploadControlError("static-packet transport policy differs from the controller policy")


def _validate_static_packet_receipt(
    *,
    path: Path,
    binding: Any,
    policy: Any,
    packet_id: str,
    packet_payload: bytes,
    verified_packet: Mapping[str, Any],
) -> tuple[Path, str]:
    """Validate compact FI evidence without accessing an FI filesystem."""

    try:
        payload = initial._read_root_controlled_file(
            Path(path),
            field="FI static-packet install receipt",
            maximum_bytes=MAX_RECEIPT_BYTES,
            private=True,
        )
    except Exception as exc:
        raise PostPacketUploadControlError("FI static-packet install receipt is unsafe") from exc
    if any(marker in payload.lower() for marker in FORBIDDEN_REPORT_MARKERS):
        raise PostPacketUploadControlError("FI static-packet install receipt is not URL-free and nonsecret")
    try:
        value = initial._parse_canonical_json(payload, field="FI static-packet install receipt")
    except Exception as exc:
        raise PostPacketUploadControlError("FI static-packet install receipt is invalid") from exc
    expected_fields = {
        "schema",
        "status",
        "installed_at",
        "candidate_directory",
        "campaign_id",
        "packet_id",
        "control_packet_sha256",
        "campaign_binding_sha256",
        "signer_enrollment_certificate_sha256",
        "source_role_config_sha256",
        "static_assets_provenance_sha256",
        "source_transport_policy_sha256",
        "exchange_receive_receipt_sha256",
        "exchange_object",
        "receipt_sha256",
    }
    if (
        set(value) != expected_fields
        or value.get("schema") != STATIC_PACKET_INSTALL_RECEIPT_SCHEMA
        or value.get("status") != "installed"
    ):
        raise PostPacketUploadControlError("FI static-packet install receipt is unsupported")
    unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if value.get("receipt_sha256") != sha256_bytes(canonical_json_bytes(unsigned)):
        raise PostPacketUploadControlError("FI static-packet install receipt checksum is invalid")
    try:
        initial._require_timestamp(value.get("installed_at"), field="FI static-packet install timestamp")
        candidate = initial._require_absolute_canonical(
            Path(value.get("candidate_directory")), field="FI static-packet candidate"
        )
    except Exception as exc:
        raise PostPacketUploadControlError("FI static-packet install receipt candidate is invalid") from exc
    prefix = "installed-" + binding.control_commit + "-"
    package_id = candidate.name[len(prefix) :] if candidate.name.startswith(prefix) else ""
    try:
        packet_control._require_identifier(package_id, field="FI source package ID")
    except Exception as exc:
        raise PostPacketUploadControlError("FI static-packet candidate is not a fixed source-adoption candidate") from exc
    if candidate.parent != initial.FI_BOOTSTRAP_ROOT:
        raise PostPacketUploadControlError("FI static-packet candidate is outside the fixed bootstrap root")
    try:
        static_request = initial.transport.SourceObjectRequest(
            campaign_id=binding.campaign_id,
            release_sha=binding.application_release_sha,
            control_commit=binding.control_commit,
            control_tree=binding.control_tree,
            source_site="controller",
            destination_site="webapp_fi",
            object_kind=initial.transport.STATIC_PROVENANCE_OBJECT_KIND,
            object_id=packet_id,
            mode=initial.transport.SINGLE_MODE,
            recipients=(policy.webapp_fi_age_recipient,),
        )
        initial.transport.validate_request(policy, static_request)
        object_key = initial.transport.source_object_key(policy, static_request)
        descriptor = initial.transport.contract.validate_object_descriptor(
            value.get("exchange_object"), maximum_plaintext_bytes=policy.maximum_plaintext_bytes
        )
    except Exception as exc:
        raise PostPacketUploadControlError("FI static-packet install receipt object is invalid") from exc
    if (
        value.get("campaign_id") != binding.campaign_id
        or value.get("packet_id") != packet_id
        or value.get("control_packet_sha256") != sha256_bytes(packet_payload)
        or value.get("campaign_binding_sha256") != verified_packet.get("campaign_binding_sha256")
        or value.get("signer_enrollment_certificate_sha256")
        != verified_packet.get("signer_enrollment_certificate_sha256")
        or value.get("source_role_config_sha256") != verified_packet.get("source_role_config_sha256")
        or value.get("static_assets_provenance_sha256")
        != verified_packet.get("static_assets_provenance_sha256")
        or value.get("source_transport_policy_sha256")
        != verified_packet.get("source_transport_policy_sha256")
        or descriptor != value.get("exchange_object")
        or descriptor.get("object_key") != object_key
    ):
        raise PostPacketUploadControlError("FI static-packet install receipt is not bound to the sealed packet")
    return candidate, sha256_bytes(payload)


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


def build_post_packet_upload_control(
    *,
    source_transport_config: Path,
    campaign_binding: Path,
    source_role_config: Path,
    fi_static_packet_install_receipt: Path,
    packet_id: object,
    artifact_kind: object,
    artifact_id: object,
) -> PostPacketUploadControl:
    """Verify all local authority and render-only inputs for one FI upload."""

    _require_root_execution()
    kind = _require_artifact_kind(artifact_kind)
    identifier = _require_artifact_id(artifact_id)
    try:
        controller_config = initial.transport.load_controller_config(Path(source_transport_config))
    except Exception as exc:
        raise PostPacketUploadControlError("controller source transport config is invalid") from exc
    binding, binding_payload, binding_identity = _load_local_binding(Path(campaign_binding))
    role_payload, role_sha = _load_role_config(
        binding_path=Path(campaign_binding), expected_binding=binding, role_path=Path(source_role_config)
    )
    public = _load_controller_authority(binding_path=Path(campaign_binding), expected_binding=binding)
    try:
        packet = packet_control._require_identifier(packet_id, field="static packet ID")
        packet_path = packet_builder.control_packet_path(campaign_id=binding.campaign_id, packet_id=packet)
        packet_payload = initial._read_root_controlled_file(
            packet_path,
            field="controller static-provenance control packet",
            maximum_bytes=packet_control.MAX_PACKET_BYTES,
            private=True,
        )
        verified = packet_control.verify_control_packet_payload(
            payload=packet_payload,
            pinned_controller_public_key_base64=public,
            expected_campaign_binding_identity=binding_identity,
        )
    except Exception as exc:
        raise PostPacketUploadControlError("controller static-provenance control packet is invalid") from exc
    if (
        verified.get("packet_id") != packet
        or verified.get("campaign_binding_payload") != binding_payload
        or verified.get("source_role_config_payload") != role_payload
        or verified.get("source_role_config_sha256") != role_sha
    ):
        raise PostPacketUploadControlError("controller static-provenance packet is not bound to the current campaign role config")
    packet_policy = verified.get("source_transport_policy")
    if not isinstance(packet_policy, Mapping):
        raise PostPacketUploadControlError("controller static-provenance packet policy is invalid")
    _validate_packet_policy(controller_policy=controller_config.policy, packet_policy=packet_policy)
    candidate, receipt_sha = _validate_static_packet_receipt(
        path=Path(fi_static_packet_install_receipt),
        binding=binding,
        policy=controller_config.policy,
        packet_id=packet,
        packet_payload=packet_payload,
        verified_packet=verified,
    )
    try:
        request = initial.transport.SourceObjectRequest(
            campaign_id=binding.campaign_id,
            release_sha=binding.application_release_sha,
            control_commit=binding.control_commit,
            control_tree=binding.control_tree,
            source_site="webapp_fi",
            destination_site="controller",
            object_kind=kind,
            object_id=identifier,
            mode=initial.transport.SINGLE_MODE,
            recipients=(controller_config.policy.controller_age_recipient,),
        )
        recipients = initial.transport.validate_request(controller_config.policy, request)
    except Exception as exc:
        raise PostPacketUploadControlError("post-packet FI upload route is invalid") from exc
    if tuple(recipients) != (controller_config.policy.controller_age_recipient,):
        raise PostPacketUploadControlError("post-packet FI upload is not controller-recipient-only")
    packet_directory = candidate / CONTROL_PACKET_DIRECTORY / packet
    prepared_directory = controller_config.policy.workspace / ("post-packet-" + kind + "-" + identifier)
    if prepared_directory.parent != controller_config.policy.workspace:
        raise PostPacketUploadControlError("post-packet FI prepared directory is invalid")
    return PostPacketUploadControl(
        controller_config=controller_config,
        policy=controller_config.policy,
        request=request,
        campaign_binding=binding,
        packet_id=packet,
        artifact_kind=kind,
        artifact_id=identifier,
        fi_candidate_directory=candidate,
        fi_packet_directory=packet_directory,
        prepared_directory=prepared_directory,
        control_packet_sha256=sha256_bytes(packet_payload),
        static_packet_receipt_sha256=receipt_sha,
        source_role_config_sha256=role_sha,
    )


def render_prepare_command(*, control: PostPacketUploadControl, fi_known_hosts: Path) -> str:
    """Render one pinned FI command that derives and prepares one object."""

    if not isinstance(control, PostPacketUploadControl):
        raise PostPacketUploadControlError("post-packet FI upload control is unsupported")
    remote = [
        "/usr/bin/python3",
        "-I",
        "-B",
        str(control.fi_candidate_directory / FI_POST_PACKET_HELPER_MEMBER),
        "prepare-upload",
        "--packet-id",
        control.packet_id,
        "--artifact-kind",
        control.artifact_kind,
        "--artifact-id",
        control.artifact_id,
    ]
    try:
        return initial._render_pinned_ssh(known_hosts=Path(fi_known_hosts), remote_arguments=remote)
    except Exception as exc:
        raise PostPacketUploadControlError("pinned FI SSH prepare control cannot be rendered") from exc


def _read_prepared_receipt(path: Path, *, control: PostPacketUploadControl) -> dict[str, Any]:
    try:
        payload = initial._read_root_controlled_file(
            Path(path),
            field="FI post-packet prepared receipt",
            maximum_bytes=MAX_RECEIPT_BYTES,
            private=True,
        )
        request, recipients, plaintext, ciphertext, prepared_sha = initial.exchange._verify_prepared_receipt(
            policy=initial._exchange_policy_from_control(control.policy), payload=payload
        )
        value = initial._parse_canonical_json(payload, field="FI post-packet prepared receipt")
    except Exception as exc:
        raise PostPacketUploadControlError("FI post-packet prepared receipt is invalid") from exc
    if any(marker in payload.lower() for marker in FORBIDDEN_REPORT_MARKERS):
        raise PostPacketUploadControlError("FI post-packet prepared receipt is not URL-free and nonsecret")
    if (
        value.get("schema") != initial.SOURCE_EXCHANGE_PREPARED_SCHEMA
        or value.get("status") != "prepared"
        or _request_value(request) != _request_value(control.request)
        or tuple(recipients) != (control.policy.controller_age_recipient,)
    ):
        raise PostPacketUploadControlError("FI post-packet prepared receipt is not bound to the fixed controller-only request")
    return {
        "plaintext": dict(plaintext),
        "ciphertext": dict(ciphertext),
        "prepared_sha256": prepared_sha,
        "receipt_sha256": sha256_bytes(payload),
    }


def validate_prepared_receipt(*, control: PostPacketUploadControl, prepared_receipt: Path) -> dict[str, Any]:
    """Return the only URL-free expectation eligible for a controller PUT URL."""

    prepared = _read_prepared_receipt(Path(prepared_receipt), control=control)
    return {
        "status": "verified",
        "campaign_id": control.request.campaign_id,
        "packet_id": control.packet_id,
        "artifact_kind": control.artifact_kind,
        "artifact_id": control.artifact_id,
        "object_key": initial.transport.source_object_key(control.policy, control.request),
        "recipient_mode": control.request.mode,
        "recipients": list(control.request.recipients),
        "plaintext": prepared["plaintext"],
        "ciphertext": prepared["ciphertext"],
        "prepared_receipt_sha256": prepared["receipt_sha256"],
    }


def render_upload_command(
    *,
    control: PostPacketUploadControl,
    fi_known_hosts: Path,
    prepared_receipt: Path,
    presigned_upload_url: str,
) -> str:
    """Render one pinned FI PUT command using one transient URL only."""

    _read_prepared_receipt(Path(prepared_receipt), control=control)
    try:
        upload_url = initial.transport.require_create_only_presigned_put_url(
            presigned_upload_url,
            policy=control.policy,
            object_key=initial.transport.source_object_key(control.policy, control.request),
        )
    except Exception as exc:
        raise PostPacketUploadControlError("post-packet FI presigned upload URL is invalid") from exc
    remote = [
        "/usr/bin/python3",
        "-I",
        "-B",
        str(control.fi_candidate_directory / FI_POST_PACKET_HELPER_MEMBER),
        "upload-prepared",
        "--packet-id",
        control.packet_id,
        "--artifact-kind",
        control.artifact_kind,
        "--artifact-id",
        control.artifact_id,
        "--upload-url",
        upload_url,
    ]
    try:
        return initial._render_pinned_ssh(known_hosts=Path(fi_known_hosts), remote_arguments=remote)
    except Exception as exc:
        raise PostPacketUploadControlError("pinned FI SSH upload control cannot be rendered") from exc


def validate_upload_report(
    *,
    control: PostPacketUploadControl,
    prepared_receipt: Path,
    upload_report: Path,
) -> dict[str, Any]:
    """Verify an FI URL-free report before controller exact-VersionId read-back."""

    prepared = _read_prepared_receipt(Path(prepared_receipt), control=control)
    try:
        payload = initial._read_root_controlled_file(
            Path(upload_report),
            field="FI post-packet upload report",
            maximum_bytes=MAX_RECEIPT_BYTES,
            private=True,
        )
        report = initial.exchange.verify_upload_report(
            policy=initial._exchange_policy_from_control(control.policy), payload=payload
        )
        value = initial._parse_canonical_json(payload, field="FI post-packet upload report")
    except Exception as exc:
        raise PostPacketUploadControlError("FI post-packet upload report is invalid") from exc
    if any(marker in payload.lower() for marker in FORBIDDEN_REPORT_MARKERS):
        raise PostPacketUploadControlError("FI post-packet upload report is not URL-free and nonsecret")
    try:
        request = initial.exchange._request_from_value(
            report.get("request"),
            policy=initial._exchange_policy_from_control(control.policy),
            field="FI post-packet upload report request",
        )
    except Exception as exc:
        raise PostPacketUploadControlError("FI post-packet upload report request is invalid") from exc
    descriptor = report.get("object")
    if (
        value.get("schema") != initial.SOURCE_EXCHANGE_UPLOAD_REPORT_SCHEMA
        or value.get("status") != "uploaded-awaiting-controller-readback"
        or _request_value(request) != _request_value(control.request)
        or not isinstance(descriptor, Mapping)
        or descriptor.get("object_key") != initial.transport.source_object_key(control.policy, control.request)
        or descriptor.get("plaintext_sha256") != prepared["plaintext"]["sha256"]
        or descriptor.get("plaintext_bytes") != prepared["plaintext"]["bytes"]
        or descriptor.get("ciphertext_sha256") != prepared["ciphertext"]["sha256"]
        or descriptor.get("ciphertext_bytes") != prepared["ciphertext"]["bytes"]
    ):
        raise PostPacketUploadControlError("FI post-packet upload report differs from the prepared expectation")
    return {
        "status": "verified",
        "campaign_id": control.request.campaign_id,
        "packet_id": control.packet_id,
        "artifact_kind": control.artifact_kind,
        "artifact_id": control.artifact_id,
        "object": dict(descriptor),
        "upload_report_sha256": sha256_bytes(payload),
        "controller_readback_required": True,
    }


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-transport-config", type=Path, required=True)
    parser.add_argument("--campaign-binding", type=Path, required=True)
    parser.add_argument("--source-role-config", type=Path, required=True)
    parser.add_argument("--fi-static-packet-install-receipt", type=Path, required=True)
    parser.add_argument("--packet-id", required=True)
    parser.add_argument("--artifact-kind", choices=sorted(ARTIFACT_KINDS), required=True)
    parser.add_argument("--artifact-id", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    prepare = actions.add_parser("render-prepare", help="render the fixed FI post-packet prepare command")
    _common_arguments(prepare)
    prepare.add_argument("--fi-known-hosts", type=Path, required=True)
    verify_prepared = actions.add_parser("verify-prepared", help="verify one FI prepared receipt")
    _common_arguments(verify_prepared)
    verify_prepared.add_argument("--prepared-receipt", type=Path, required=True)
    upload = actions.add_parser("render-upload", help="render the fixed FI post-packet upload command")
    _common_arguments(upload)
    upload.add_argument("--fi-known-hosts", type=Path, required=True)
    upload.add_argument("--prepared-receipt", type=Path, required=True)
    verify_upload = actions.add_parser("verify-upload", help="verify one FI upload report")
    _common_arguments(verify_upload)
    verify_upload.add_argument("--prepared-receipt", type=Path, required=True)
    verify_upload.add_argument("--upload-report", type=Path, required=True)
    return parser


def _control_from_args(args: argparse.Namespace) -> PostPacketUploadControl:
    return build_post_packet_upload_control(
        source_transport_config=args.source_transport_config,
        campaign_binding=args.campaign_binding,
        source_role_config=args.source_role_config,
        fi_static_packet_install_receipt=args.fi_static_packet_install_receipt,
        packet_id=args.packet_id,
        artifact_kind=args.artifact_kind,
        artifact_id=args.artifact_id,
    )


def _print_result(value: Mapping[str, Any]) -> None:
    sys.stdout.buffer.write(canonical_json_bytes(value) + b"\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        control = _control_from_args(args)
        if args.action == "render-prepare":
            result: Mapping[str, Any] = {"command": render_prepare_command(control=control, fi_known_hosts=args.fi_known_hosts)}
        elif args.action == "verify-prepared":
            result = validate_prepared_receipt(control=control, prepared_receipt=args.prepared_receipt)
        elif args.action == "render-upload":
            url = initial._read_presigned_url_stdin()
            result = {
                "command": render_upload_command(
                    control=control,
                    fi_known_hosts=args.fi_known_hosts,
                    prepared_receipt=args.prepared_receipt,
                    presigned_upload_url=url,
                )
            }
        elif args.action == "verify-upload":
            result = validate_upload_report(
                control=control,
                prepared_receipt=args.prepared_receipt,
                upload_report=args.upload_report,
            )
        else:  # pragma: no cover - argparse makes this unreachable.
            raise PostPacketUploadControlError("unsupported post-packet upload action")
        _print_result(result)
        return 0
    except PostPacketUploadControlError as exc:
        _print_result({"status": "blocked", "error": str(exc), "error_class": exc.__class__.__name__})
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
