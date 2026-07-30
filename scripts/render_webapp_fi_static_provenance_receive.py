#!/usr/bin/env python3
"""Render one bounded controller-to-WebApp-FI static-provenance control.

The controller publishes a signed static-provenance packet through the one
``controller -> webapp_fi/static-provenance`` immutable Object Storage route.
This module proves all local controller inputs before it renders the only SSH
control that WebApp-FI may receive:

* the canonical campaign binding, fixed controller signing authority and
  fixed packet path;
* the URL-free, exact controller transport receipt for that packet;
* the opaque source-adoption installation receipt returned from WebApp-FI;
  and
* one root-owned ``known_hosts`` pin for the fixed FI host.

It neither executes SSH nor creates an Object Storage client.  The transient
VersionId-bound GET URL is read from stdin only for ``render`` and is present
only as the last remote argv item.  The remote wrapper writes public,
URL-free packet-policy and receipt facts only to a temporary root-only
directory, asks the already-installed FI helpers to receive and install the
packet, and returns a small URL-free install receipt.  A separate local
``verify-install`` operation validates that returned receipt; it does not
open an FI path or execute a command.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit


MAX_CONTROL_BYTES = 2 * 1024 * 1024
MAX_URL_BYTES = 8192
MAX_INSTALL_OUTPUT_BYTES = 2 * 1024 * 1024

REMOTE_RECEIVE_DIRECTORY_PREFIX = "static-provenance-"
SOURCE_ADOPTION_INSTALL_RECEIPT_NAME = "source-adoption-install-receipt.json"
EXCHANGE_SCRIPT_RELATIVE = "scripts/manage_webapp_fi_source_exchange.py"
PACKET_INSTALLER_RELATIVE = "scripts/install_webapp_fi_static_provenance_control_packet.py"
PACKET_INSTALL_DIRECTORY = "controller-static-provenance"
PACKET_INSTALL_RECEIPT_NAME = "static-provenance-install-receipt.json"
FI_CAMPAIGN_IDENTITY_ROOT = PurePosixPath("/etc/trading-bot-three-site/campaigns")
FI_BOOTSTRAP_IDENTITY_SUFFIX = PurePosixPath("webapp-fi/bootstrap.agekey")

CONTROL_SCHEMA = "gold-trade-webapp-fi-static-provenance-receive-control-v1"
INSTALL_OUTPUT_SCHEMA = "gold-trade-webapp-fi-static-provenance-install-output-v1"

UTC_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_CONTROL_KEY_FRAGMENTS = (
    "credential",
    "access_key",
    "secret",
    "private_key",
    "session_token",
    "password",
    "payload",
    "base64",
    "url",
)
FORBIDDEN_CONTROL_VALUE_MARKERS = ("://", "x-amz-", "age-secret-key-")


class StaticProvenanceReceiveRenderError(RuntimeError):
    """The controller cannot safely render or verify this FI control."""


def _require_root_controlled_directory_chain(path: Path, *, field: str) -> None:
    """Require a root-controlled source path before importing siblings."""

    import stat

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
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or (mode & 0o022 and not metadata.st_mode & stat.S_ISVTX)
        ):
            raise RuntimeError(f"{field} parent is not root-controlled")


def _require_root_controlled_code_file(path: Path, *, field: str) -> Path:
    """Return one exact root-owned, non-writable sibling source file."""

    import stat

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
    """Load a reviewed sibling from its exact source path only."""

    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise RuntimeError("required sibling filename is invalid")
    source = _require_root_controlled_code_file(
        Path(__file__), field="static-provenance receive renderer source"
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


initial = _load_exact_sibling(
    "render_webapp_fi_initial_static_upload.py", "_static_provenance_receive_initial"
)
transport = _load_exact_sibling(
    "manage_webapp_fi_source_transport.py", "_static_provenance_receive_transport"
)
packet = _load_exact_sibling(
    "webapp_fi_static_provenance_control_packet.py", "_static_provenance_receive_packet"
)
packet_builder = _load_exact_sibling(
    "build_webapp_fi_static_provenance_control_packet.py", "_static_provenance_receive_packet_builder"
)
role_config = _load_exact_sibling(
    "render_webapp_fi_source_role_config.py", "_static_provenance_receive_role_config"
)
campaign_key = _load_exact_sibling(
    "manage_controller_campaign_signing_key.py", "_static_provenance_receive_campaign_key"
)


@dataclasses.dataclass(frozen=True)
class StaticProvenanceReceiveControl:
    """All controller-verified facts for one FI receive/install operation."""

    controller_config: Any
    campaign_binding: Any
    authority: Any
    initial_control: Any
    packet_id: str
    packet_path: Path
    packet_payload: bytes
    verified_packet: Mapping[str, Any]
    transport_receipt: Mapping[str, Any]
    candidate_directory: Path
    received_directory: Path
    fi_install_receipt_sha256: str


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _assert_url_free_nonsecret_value(value: object, *, field: str) -> None:
    """Keep any controller-to-FI control and FI-to-controller result printable."""

    def visit(item: object) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if (
                    not isinstance(key, str)
                    or any(fragment in key.lower() for fragment in FORBIDDEN_CONTROL_KEY_FRAGMENTS)
                ):
                    raise StaticProvenanceReceiveRenderError(
                        f"{field} contains transient or secret material"
                    )
                visit(child)
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                visit(child)
            return
        if isinstance(item, str):
            lowered = item.lower()
            if any(marker in lowered for marker in FORBIDDEN_CONTROL_VALUE_MARKERS):
                raise StaticProvenanceReceiveRenderError(
                    f"{field} contains transient or secret material"
                )
            return
        if item is None or isinstance(item, (bool, int, float)):
            return
        raise StaticProvenanceReceiveRenderError(f"{field} contains unsupported binary material")

    visit(value)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StaticProvenanceReceiveRenderError("JSON input contains duplicate keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise StaticProvenanceReceiveRenderError(f"JSON input contains unsupported constant: {value}")


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise StaticProvenanceReceiveRenderError("static-provenance receive controls must run as root")


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise StaticProvenanceReceiveRenderError(f"{field} is invalid")
    return value


def _require_timestamp(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not UTC_TIMESTAMP_RE.fullmatch(value):
        raise StaticProvenanceReceiveRenderError(f"{field} is invalid")
    try:
        dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise StaticProvenanceReceiveRenderError(f"{field} is invalid") from exc
    return value


def _require_absolute_canonical(path: Path, *, field: str) -> Path:
    candidate = Path(path)
    if (
        "\x00" in str(candidate)
        or not candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts[1:])
        or str(candidate) != os.path.normpath(str(candidate))
    ):
        raise StaticProvenanceReceiveRenderError(f"{field} must be one canonical absolute path")
    return candidate


def webapp_fi_bootstrap_identity_file(campaign_id: str) -> str:
    """Return the sole campaign-derived FI bootstrap identity pathname."""

    try:
        campaign = packet._require_identifier(campaign_id, field="campaign ID", campaign=True)
    except Exception as exc:
        raise StaticProvenanceReceiveRenderError("campaign ID is invalid for the FI bootstrap identity") from exc
    path = FI_CAMPAIGN_IDENTITY_ROOT / campaign / FI_BOOTSTRAP_IDENTITY_SUFFIX
    value = path.as_posix()
    if not path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise StaticProvenanceReceiveRenderError("FI bootstrap identity path is invalid")
    return value


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


def _load_canonical_controller_binding(campaign_binding_path: Path) -> tuple[Any, Any, dict[str, Any]]:
    """Use the current role-config layout API to bind all controller paths."""

    _require_root_execution()
    try:
        layout = role_config.role_config_layout_for_campaign_binding(Path(campaign_binding_path))
        binding = role_config.binding.load_campaign_binding(Path(campaign_binding_path))
        normalized_role = role_config.load_source_role_config(
            path=layout.role_config_path, campaign_binding=binding
        )
    except Exception as exc:
        raise StaticProvenanceReceiveRenderError(
            "canonical campaign binding or fixed controller source role config is invalid"
        ) from exc
    if (
        getattr(binding, "campaign_id", None) != layout.campaign_id
        or getattr(binding, "binding_sha256", None) != layout.campaign_binding_sha256
    ):
        raise StaticProvenanceReceiveRenderError("canonical campaign binding changed while loading role config")
    return binding, layout, normalized_role


def _load_campaign_authority(campaign_binding_path: Path, *, binding: Any) -> Any:
    try:
        authority = campaign_key.load_verified_campaign_signer(
            campaign_binding_path=Path(campaign_binding_path)
        )
    except Exception as exc:
        raise StaticProvenanceReceiveRenderError(
            "fixed controller campaign signing authority is invalid"
        ) from exc
    if not _same_binding(getattr(authority, "campaign_binding", None), binding):
        raise StaticProvenanceReceiveRenderError(
            "fixed controller campaign signing authority is not bound to the canonical campaign"
        )
    signing_key = getattr(authority, "signing_key", None)
    public = getattr(signing_key, "public_key_base64", None)
    if not isinstance(public, str) or not public:
        raise StaticProvenanceReceiveRenderError("fixed controller campaign signing authority is incomplete")
    return authority


def _read_private_control(path: Path, *, field: str, maximum_bytes: int = MAX_CONTROL_BYTES) -> bytes:
    try:
        return initial._read_root_controlled_file(
            Path(path), field=field, maximum_bytes=maximum_bytes, private=True
        )
    except Exception as exc:
        raise StaticProvenanceReceiveRenderError(f"{field} is unsafe") from exc


def _packet_path(*, campaign_id: str, packet_id: str) -> Path:
    try:
        path = packet_builder.control_packet_path(campaign_id=campaign_id, packet_id=packet_id)
    except Exception as exc:
        raise StaticProvenanceReceiveRenderError("fixed controller static-provenance packet path is invalid") from exc
    return _require_absolute_canonical(Path(path), field="fixed controller static-provenance packet")


def _packet_policy_matches_controller(*, policy: Mapping[str, Any], controller_policy: Any) -> None:
    """Allow only the packet-bound FI workspace to differ from controller state."""

    try:
        parsed = urlsplit(controller_policy.endpoint)
        expected = {
            "endpoint_host": parsed.hostname,
            "region": controller_policy.region,
            "bucket": controller_policy.bucket,
            "prefix": controller_policy.prefix,
            "age_binary": controller_policy.age_binary,
            "controller_age_recipient": controller_policy.controller_age_recipient,
            "webapp_fi_age_recipient": controller_policy.webapp_fi_age_recipient,
            "webapp_ir_age_recipient": controller_policy.webapp_ir_age_recipient,
            "maximum_plaintext_bytes": controller_policy.maximum_plaintext_bytes,
        }
    except (AttributeError, TypeError) as exc:
        raise StaticProvenanceReceiveRenderError("controller source transport policy is incomplete") from exc
    if parsed.scheme != "https" or not parsed.hostname or parsed.port is not None:
        raise StaticProvenanceReceiveRenderError("controller source transport endpoint is invalid")
    if any(policy.get(field) != value for field, value in expected.items()):
        raise StaticProvenanceReceiveRenderError(
            "static-provenance packet transport policy differs from the controller-pinned route"
        )
    workspace = policy.get("workspace")
    if not isinstance(workspace, str) or not Path(workspace).is_absolute():
        raise StaticProvenanceReceiveRenderError("static-provenance packet FI workspace is invalid")


def _packet_policy_matches_bootstrap(*, policy: Mapping[str, Any], initial_policy: Any) -> None:
    """Retain one recipient/bucket policy across bootstrap and normal packet use."""

    try:
        parsed = urlsplit(initial_policy.endpoint)
        expected = {
            "endpoint_host": parsed.hostname,
            "region": initial_policy.region,
            "bucket": initial_policy.bucket,
            "prefix": initial_policy.prefix,
            "age_binary": initial_policy.age_binary,
            "controller_age_recipient": initial_policy.controller_age_recipient,
            "webapp_fi_age_recipient": initial_policy.webapp_fi_age_recipient,
            "webapp_ir_age_recipient": initial_policy.webapp_ir_age_recipient,
            "maximum_plaintext_bytes": initial_policy.maximum_plaintext_bytes,
        }
    except (AttributeError, TypeError) as exc:
        raise StaticProvenanceReceiveRenderError("opaque FI source-adoption transport policy is incomplete") from exc
    if parsed.scheme != "https" or not parsed.hostname or parsed.port is not None:
        raise StaticProvenanceReceiveRenderError("opaque FI source-adoption endpoint is invalid")
    if any(policy.get(field) != value for field, value in expected.items()):
        raise StaticProvenanceReceiveRenderError(
            "static-provenance packet transport policy is not bound to the FI bootstrap route"
        )


def _expected_request(*, binding: Any, packet_id: str, policy: Any) -> Any:
    try:
        request = transport.SourceObjectRequest(
            campaign_id=binding.campaign_id,
            release_sha=binding.application_release_sha,
            control_commit=binding.control_commit,
            control_tree=binding.control_tree,
            source_site="controller",
            destination_site="webapp_fi",
            object_kind=transport.STATIC_PROVENANCE_OBJECT_KIND,
            object_id=packet_id,
            mode=transport.SINGLE_MODE,
            recipients=(policy.webapp_fi_age_recipient,),
        )
        transport.validate_request(policy, request)
    except Exception as exc:
        raise StaticProvenanceReceiveRenderError("static-provenance transport request is invalid") from exc
    return request


def _verify_exact_transport_receipt(
    *,
    receipt_path: Path,
    controller_config: Any,
    binding: Any,
    packet_id: str,
    packet_payload: bytes,
) -> dict[str, Any]:
    payload = _read_private_control(
        Path(receipt_path), field="controller static-provenance transport receipt"
    )
    if b"://" in payload or b"presigned" in payload.lower() or b'"url"' in payload.lower():
        raise StaticProvenanceReceiveRenderError("controller static-provenance transport receipt persists a URL")
    try:
        published = transport.verify_publish_receipt(
            config=controller_config.policy, payload=payload
        )
        request = _expected_request(
            binding=binding, packet_id=packet_id, policy=controller_config.policy
        )
        expected = {
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
    except Exception as exc:
        raise StaticProvenanceReceiveRenderError(
            "controller static-provenance transport receipt is invalid"
        ) from exc
    if any(published.get(field) != value for field, value in expected.items()):
        raise StaticProvenanceReceiveRenderError(
            "controller static-provenance transport receipt is not bound to the fixed packet route"
        )
    descriptor = published.get("object")
    if (
        not isinstance(descriptor, Mapping)
        or descriptor.get("plaintext_sha256") != sha256_bytes(packet_payload)
        or descriptor.get("plaintext_bytes") != len(packet_payload)
    ):
        raise StaticProvenanceReceiveRenderError(
            "controller static-provenance transport receipt plaintext does not match the fixed packet"
        )
    return dict(published)


def _verify_packet(
    *,
    packet_path: Path,
    authority: Any,
    binding: Any,
    local_role_config: Mapping[str, Any],
) -> tuple[bytes, dict[str, Any]]:
    payload = _read_private_control(
        packet_path,
        field="fixed controller static-provenance packet",
        maximum_bytes=packet.MAX_PACKET_BYTES,
    )
    try:
        verified = packet.verify_control_packet_payload(
            payload=payload,
            pinned_controller_public_key_base64=authority.signing_key.public_key_base64,
            expected_campaign_binding_identity={
                "campaign_id": binding.campaign_id,
                "application": {
                    "release_sha": binding.application_release_sha,
                    "release_tree": binding.application_release_tree,
                    "expected_alembic_revision": binding.expected_alembic_revision,
                },
                "tooling": {"control_commit": binding.control_commit, "control_tree": binding.control_tree},
                "binding_sha256": binding.binding_sha256,
            },
        )
    except Exception as exc:
        raise StaticProvenanceReceiveRenderError(
            "fixed controller static-provenance packet is invalid"
        ) from exc
    try:
        local_role_payload = role_config.canonical_json_bytes(local_role_config) + b"\n"
    except Exception as exc:  # pragma: no cover - normalized local role invariant.
        raise StaticProvenanceReceiveRenderError("fixed controller source role config is invalid") from exc
    if verified.get("source_role_config_payload") != local_role_payload:
        raise StaticProvenanceReceiveRenderError(
            "fixed controller static-provenance packet does not contain the canonical source role config"
        )
    return payload, dict(verified)


def _validate_packet_against_fi_adoption_descriptor(
    *, verified_packet: Mapping[str, Any], initial_control: Any
) -> None:
    """Check the certificate's canonical-tree claim after package validation."""

    try:
        certificate = packet.parse_canonical_json(
            verified_packet["signer_enrollment_certificate_payload"],
            field="static-provenance signer enrollment certificate",
            maximum_bytes=packet.MAX_ARTIFACT_BYTES,
        )
    except Exception as exc:
        raise StaticProvenanceReceiveRenderError(
            "static-provenance packet signer enrollment certificate is invalid"
        ) from exc
    descriptor = getattr(initial_control, "fi_install_receipt_sha256", None)
    # ``build_initial_static_control`` proves the opaque receipt, but its
    # public result deliberately retains only its receipt hash.  The packet
    # verifier already checked certificate schema/signature; compare the
    # complete binding fields available on both sides here.
    if not isinstance(descriptor, str) or not SHA256_RE.fullmatch(descriptor):
        raise StaticProvenanceReceiveRenderError("opaque FI adoption receipt checksum is invalid")
    if (
        certificate.get("campaign_id") != initial_control.campaign_binding.campaign_id
        or certificate.get("package_id") != initial_control.package_id
        or certificate.get("source_adoption_install_receipt_sha256") != descriptor
    ):
        raise StaticProvenanceReceiveRenderError(
            "static-provenance packet signer enrollment is not bound to the opaque FI adoption receipt"
        )


def build_static_provenance_receive_control(
    *,
    source_transport_config: Path,
    campaign_binding: Path,
    source_adoption_package_directory: Path,
    preparation_receipt: Path,
    fi_install_receipt: Path,
    packet_id: str,
    transport_publish_receipt: Path,
) -> StaticProvenanceReceiveControl:
    """Validate every controller fact needed for one fixed FI remote control."""

    _require_root_execution()
    binding, _layout, local_role = _load_canonical_controller_binding(Path(campaign_binding))
    try:
        controller_config = transport.load_controller_config(Path(source_transport_config))
    except Exception as exc:
        raise StaticProvenanceReceiveRenderError("controller source transport configuration is invalid") from exc
    try:
        initial_control = initial.build_initial_static_control(
            source_transport_config=Path(source_transport_config),
            campaign_binding=Path(campaign_binding),
            source_adoption_package_directory=Path(source_adoption_package_directory),
            preparation_receipt=Path(preparation_receipt),
            fi_install_receipt=Path(fi_install_receipt),
        )
    except Exception as exc:
        raise StaticProvenanceReceiveRenderError(
            "opaque FI source-adoption install receipt or package is invalid"
        ) from exc
    if not _same_binding(initial_control.campaign_binding, binding):
        raise StaticProvenanceReceiveRenderError(
            "opaque FI source-adoption install receipt is not bound to the canonical campaign"
        )
    try:
        normalized_packet_id = packet._require_identifier(packet_id, field="control packet ID")
    except Exception as exc:
        raise StaticProvenanceReceiveRenderError("control packet ID is invalid") from exc
    authority = _load_campaign_authority(Path(campaign_binding), binding=binding)
    packet_path = _packet_path(campaign_id=binding.campaign_id, packet_id=normalized_packet_id)
    packet_payload, verified_packet = _verify_packet(
        packet_path=packet_path,
        authority=authority,
        binding=binding,
        local_role_config=local_role,
    )
    if verified_packet.get("packet_id") != normalized_packet_id:
        raise StaticProvenanceReceiveRenderError("fixed controller static-provenance packet ID is invalid")
    packet_policy = verified_packet.get("source_transport_policy")
    if not isinstance(packet_policy, Mapping):
        raise StaticProvenanceReceiveRenderError("fixed controller static-provenance packet policy is invalid")
    _packet_policy_matches_controller(policy=packet_policy, controller_policy=controller_config.policy)
    _packet_policy_matches_bootstrap(policy=packet_policy, initial_policy=initial_control.policy)
    _validate_packet_against_fi_adoption_descriptor(
        verified_packet=verified_packet, initial_control=initial_control
    )
    receipt = _verify_exact_transport_receipt(
        receipt_path=Path(transport_publish_receipt),
        controller_config=controller_config,
        binding=binding,
        packet_id=normalized_packet_id,
        packet_payload=packet_payload,
    )
    candidate = _require_absolute_canonical(
        Path(initial_control.candidate_directory), field="opaque FI source-adoption candidate"
    )
    workspace = _require_absolute_canonical(
        Path(packet_policy["workspace"]), field="packet-bound FI exchange workspace"
    )
    received = workspace / (REMOTE_RECEIVE_DIRECTORY_PREFIX + normalized_packet_id)
    if received.parent != workspace:
        raise StaticProvenanceReceiveRenderError("packet-bound FI receive directory is invalid")
    return StaticProvenanceReceiveControl(
        controller_config=controller_config,
        campaign_binding=binding,
        authority=authority,
        initial_control=initial_control,
        packet_id=normalized_packet_id,
        packet_path=packet_path,
        packet_payload=packet_payload,
        verified_packet=verified_packet,
        transport_receipt=receipt,
        candidate_directory=candidate,
        received_directory=received,
        fi_install_receipt_sha256=initial_control.fi_install_receipt_sha256,
    )


def _remote_config(control: StaticProvenanceReceiveControl) -> dict[str, Any]:
    policy = control.verified_packet.get("source_transport_policy")
    if not isinstance(policy, Mapping):  # pragma: no cover - constructor invariant.
        raise StaticProvenanceReceiveRenderError("static-provenance packet policy is invalid")
    return {
        "schema": CONTROL_SCHEMA,
        "candidate_directory": str(control.candidate_directory),
        "source_adoption_install_receipt": str(
            control.candidate_directory / SOURCE_ADOPTION_INSTALL_RECEIPT_NAME
        ),
        "exchange_script": str(control.candidate_directory / EXCHANGE_SCRIPT_RELATIVE),
        "packet_installer_script": str(control.candidate_directory / PACKET_INSTALLER_RELATIVE),
        "age_identity_file": webapp_fi_bootstrap_identity_file(control.campaign_binding.campaign_id),
        "campaign_id": control.campaign_binding.campaign_id,
        "packet_id": control.packet_id,
        "received_directory": str(control.received_directory),
        "packet_install_receipt": str(
            control.candidate_directory
            / PACKET_INSTALL_DIRECTORY
            / control.packet_id
            / PACKET_INSTALL_RECEIPT_NAME
        ),
        "source_transport_policy": dict(policy),
        "controller_publish_receipt": dict(control.transport_receipt),
    }


def _assert_remote_config(value: Mapping[str, Any]) -> None:
    expected = {
        "schema",
        "candidate_directory",
        "source_adoption_install_receipt",
        "exchange_script",
        "packet_installer_script",
        "age_identity_file",
        "campaign_id",
        "packet_id",
        "received_directory",
        "packet_install_receipt",
        "source_transport_policy",
        "controller_publish_receipt",
    }
    if set(value) != expected or value.get("schema") != CONTROL_SCHEMA:
        raise StaticProvenanceReceiveRenderError("remote static-provenance control has unexpected fields")
    _assert_url_free_nonsecret_value(value, field="remote static-provenance control")
    encoded = canonical_json_bytes(value)
    lowered = encoded.lower()
    if (
        len(encoded) > MAX_CONTROL_BYTES
        or b"://" in lowered
        or b"presigned" in lowered
        or b'"url"' in lowered
        or b"credential" in lowered
        or b"access_key" in lowered
        or b"secret" in lowered
        or b"private_key" in lowered
        or b"session_token" in lowered
        or b"password" in lowered
        or b"payload" in lowered
        or b"base64" in lowered
    ):
        raise StaticProvenanceReceiveRenderError(
            "remote static-provenance control contains transient or secret material"
        )


# The wrapper is deliberately a fixed program, not an operator-selected remote
# shell fragment.  It creates only ephemeral public receipt/policy files under
# the packet-bound FI workspace.  It never receives S3 credentials or a
# payload over SSH; the final argv item is the one transient GET URL.
REMOTE_RECEIVER_SOURCE = r'''
import base64
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile

CONTROL_SCHEMA = "gold-trade-webapp-fi-static-provenance-receive-control-v1"
INSTALL_RECEIPT_SCHEMA = "gold-trade-webapp-fi-static-provenance-install-receipt-v1"
INSTALL_RECEIPT_FIELDS = {"schema", "status", "installed_at", "candidate_directory", "campaign_id", "packet_id", "control_packet_sha256", "campaign_binding_sha256", "signer_enrollment_certificate_sha256", "source_role_config_sha256", "static_assets_provenance_sha256", "source_transport_policy_sha256", "exchange_receive_receipt_sha256", "exchange_object", "receipt_sha256"}
FORBIDDEN_OUTPUT_KEY_FRAGMENTS = ("credential", "access_key", "secret", "private_key", "session_token", "password", "payload", "base64", "url")

class ControlError(RuntimeError):
    pass

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")

def strict(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ControlError("duplicate key")
        result[key] = value
    return result

def require_path(value):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ControlError("path")
    path = PurePosixPath(value)
    if not path.is_absolute() or path.as_posix() != value or any(part in ("", ".", "..") for part in path.parts):
        raise ControlError("path")
    return Path(value)

def require_private_directory(path):
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        after = resolved.lstat()
    except OSError as exc:
        raise ControlError("directory") from exc
    if resolved != path or stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(after.st_mode):
        raise ControlError("directory")
    if after.st_uid != 0 or stat.S_IMODE(after.st_mode) & 0o077:
        raise ControlError("directory")
    return path

def write_new(path, payload):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except OSError as exc:
        raise ControlError("temporary control file") from exc
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
    except OSError as exc:
        raise ControlError("temporary control file") from exc
    finally:
        os.close(descriptor)

def read_private(path):
    try:
        before = path.lstat()
    except OSError as exc:
        raise ControlError("install receipt") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or stat.S_IMODE(before.st_mode) & 0o077:
        raise ControlError("install receipt")
    descriptor = os.open(str(path), os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if opened.st_dev != before.st_dev or opened.st_ino != before.st_ino or opened.st_size != before.st_size:
            raise ControlError("install receipt")
        payload = os.read(descriptor, 2 * 1024 * 1024 + 1)
        if len(payload) != opened.st_size or len(payload) > 2 * 1024 * 1024:
            raise ControlError("install receipt")
        return payload
    finally:
        os.close(descriptor)

def reject_nonsecret_output(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or any(fragment in key.lower() for fragment in FORBIDDEN_OUTPUT_KEY_FRAGMENTS):
                raise ControlError("install receipt")
            reject_nonsecret_output(child)
        return
    if isinstance(value, list):
        for child in value:
            reject_nonsecret_output(child)
        return
    if isinstance(value, str) and ("://" in value.lower() or "x-amz-" in value.lower() or "age-secret-key-" in value.lower()):
        raise ControlError("install receipt")
    if value is None or isinstance(value, (bool, int, float, str)):
        return
    raise ControlError("install receipt")

def load_nonsecret_install_receipt(path):
    payload = read_private(path)
    lowered = payload.lower()
    if b"://" in lowered or b"presigned" in lowered or b'"url"' in lowered or b"credential" in lowered or b"access_key" in lowered or b"secret" in lowered or b"private_key" in lowered or b"session_token" in lowered or b"password" in lowered or b"payload" in lowered or b"base64" in lowered:
        raise ControlError("install receipt")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=strict)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlError("install receipt") from exc
    if not isinstance(value, dict) or payload != canonical(value) + b"\n":
        raise ControlError("install receipt")
    if set(value) != INSTALL_RECEIPT_FIELDS or value.get("schema") != INSTALL_RECEIPT_SCHEMA or value.get("status") != "installed":
        raise ControlError("install receipt")
    reject_nonsecret_output(value)
    return value

def main():
    try:
        if os.geteuid() != 0 or len(sys.argv) != 5 or sys.argv[3] != "--":
            raise ControlError("arguments")
        program = json.loads(base64.b64decode(sys.argv[2]), object_pairs_hook=strict)
        expected = {"schema", "candidate_directory", "source_adoption_install_receipt", "exchange_script", "packet_installer_script", "age_identity_file", "campaign_id", "packet_id", "received_directory", "packet_install_receipt", "source_transport_policy", "controller_publish_receipt"}
        if not isinstance(program, dict) or set(program) != expected or program.get("schema") != CONTROL_SCHEMA:
            raise ControlError("configuration")
        encoded = canonical(program).lower()
        if b"://" in encoded or b"presigned" in encoded or b'"url"' in encoded or b"credential" in encoded or b"access_key" in encoded or b"secret" in encoded or b"private_key" in encoded or b"session_token" in encoded or b"password" in encoded or b"payload" in encoded or b"base64" in encoded:
            raise ControlError("configuration")
        candidate = require_private_directory(require_path(program["candidate_directory"]))
        source_install = require_path(program["source_adoption_install_receipt"])
        exchange_script = require_path(program["exchange_script"])
        installer_script = require_path(program["packet_installer_script"])
        age_identity = require_path(program["age_identity_file"])
        received = require_path(program["received_directory"])
        installed_receipt = require_path(program["packet_install_receipt"])
        if source_install != candidate / "source-adoption-install-receipt.json" or exchange_script != candidate / "scripts/manage_webapp_fi_source_exchange.py" or installer_script != candidate / "scripts/install_webapp_fi_static_provenance_control_packet.py":
            raise ControlError("candidate paths")
        packet_id = program["packet_id"]
        campaign_id = program["campaign_id"]
        expected_identity = Path("/etc/trading-bot-three-site/campaigns") / campaign_id / "webapp-fi/bootstrap.agekey"
        if not isinstance(campaign_id, str) or not campaign_id or age_identity != expected_identity:
            raise ControlError("campaign identity")
        if not isinstance(packet_id, str) or not packet_id or received.name != "static-provenance-" + packet_id:
            raise ControlError("packet id")
        policy = program["source_transport_policy"]
        receipt = program["controller_publish_receipt"]
        if not isinstance(policy, dict) or not isinstance(receipt, dict):
            raise ControlError("metadata")
        workspace = require_private_directory(require_path(policy.get("workspace")))
        if received.parent != workspace:
            raise ControlError("receive directory")
        expected_install = candidate / "controller-static-provenance" / packet_id / "static-provenance-install-receipt.json"
        if installed_receipt != expected_install:
            raise ControlError("install receipt path")
        url = sys.argv[4]
        if not isinstance(url, str) or not url.startswith("https://") or len(url) > 8192 or any(character.isspace() for character in url):
            raise ControlError("URL")
        with tempfile.TemporaryDirectory(prefix=".static-provenance-control-", dir=str(workspace)) as temporary_text:
            temporary = Path(temporary_text)
            policy_path = temporary / "policy.json"
            receipt_path = temporary / "publish-receipt.json"
            write_new(policy_path, canonical(policy) + b"\n")
            write_new(receipt_path, canonical(receipt) + b"\n")
            subprocess.run([
                "/usr/bin/python3", "-I", "-B", str(exchange_script), "receive-static-provenance",
                "--policy", str(policy_path), "--controller-publish-receipt", str(receipt_path),
                "--download-url", url, "--age-identity-file", str(age_identity),
                "--destination-dir", str(received),
            ], check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        subprocess.run([
            "/usr/bin/python3", "-I", "-B", str(installer_script), "--install-receipt", str(source_install),
            "--received-directory", str(received), "--apply",
        ], check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        install_value = load_nonsecret_install_receipt(installed_receipt)
        print(json.dumps({"schema": "gold-trade-webapp-fi-static-provenance-install-output-v1", "status": "installed", "install_receipt": install_value}, sort_keys=True, separators=(",", ":")))
    except Exception:
        print(json.dumps({"status": "blocked", "error": "static-provenance FI receive/install failed"}, sort_keys=True))
        return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
'''

REMOTE_LAUNCHER = (
    "import base64,sys;exec(compile(base64.b64decode(sys.argv[1]),'<webapp-fi-static-provenance-receive>','exec'))"
)


def render_receive_install_command(
    *,
    control: StaticProvenanceReceiveControl,
    fi_known_hosts: Path,
    presigned_download_url: str,
) -> str:
    """Render, but never execute, the one pinned FI receive/install command."""

    if not isinstance(control, StaticProvenanceReceiveControl):
        raise StaticProvenanceReceiveRenderError("static-provenance receive control is unsupported")
    try:
        request = _expected_request(
            binding=control.campaign_binding,
            packet_id=control.packet_id,
            policy=control.controller_config.policy,
        )
        expected_key = transport.source_object_key(control.controller_config.policy, request)
        descriptor = control.transport_receipt["object"]
        url = transport.require_version_bound_presigned_get_url(
            presigned_download_url,
            policy=control.controller_config.policy,
            object_key=expected_key,
            version_id=descriptor["version_id"],
        )
    except Exception as exc:
        raise StaticProvenanceReceiveRenderError(
            "static-provenance presigned download URL is invalid"
        ) from exc
    config = _remote_config(control)
    _assert_remote_config(config)
    program_b64 = base64.b64encode(REMOTE_RECEIVER_SOURCE.encode("utf-8")).decode("ascii")
    config_b64 = base64.b64encode(canonical_json_bytes(config)).decode("ascii")
    remote = shlex.join(
        ["/usr/bin/python3", "-I", "-B", "-c", REMOTE_LAUNCHER, program_b64, config_b64, "--", url]
    )
    try:
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
    except Exception as exc:
        raise StaticProvenanceReceiveRenderError("pinned FI SSH control cannot be rendered") from exc


def _parse_canonical_install_output(payload: bytes) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_INSTALL_OUTPUT_BYTES:
        raise StaticProvenanceReceiveRenderError("FI static-provenance install output has an unsafe size")
    lowered = payload.lower()
    if b"://" in lowered or b"presigned" in lowered or b'"url"' in lowered:
        raise StaticProvenanceReceiveRenderError("FI static-provenance install output persists a URL")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StaticProvenanceReceiveRenderError("FI static-provenance install output is not strict JSON") from exc
    if not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise StaticProvenanceReceiveRenderError("FI static-provenance install output is not canonical JSON")
    if set(value) != {"schema", "status", "install_receipt"} or value.get("schema") != INSTALL_OUTPUT_SCHEMA or value.get("status") != "installed":
        raise StaticProvenanceReceiveRenderError("FI static-provenance install output is unsupported")
    _assert_url_free_nonsecret_value(value, field="FI static-provenance install output")
    receipt = value.get("install_receipt")
    if not isinstance(receipt, Mapping):
        raise StaticProvenanceReceiveRenderError("FI static-provenance install receipt is invalid")
    return dict(receipt)


def validate_fi_static_provenance_install_receipt(
    *, control: StaticProvenanceReceiveControl, install_output: Path
) -> dict[str, Any]:
    """Validate a URL-free FI result captured after the rendered SSH command."""

    if not isinstance(control, StaticProvenanceReceiveControl):
        raise StaticProvenanceReceiveRenderError("static-provenance receive control is unsupported")
    payload = _read_private_control(
        Path(install_output),
        field="FI static-provenance install output",
        maximum_bytes=MAX_INSTALL_OUTPUT_BYTES,
    )
    receipt = _parse_canonical_install_output(payload)
    expected = {
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
    if set(receipt) != expected or receipt.get("schema") != "gold-trade-webapp-fi-static-provenance-install-receipt-v1" or receipt.get("status") != "installed":
        raise StaticProvenanceReceiveRenderError("FI static-provenance install receipt is unsupported")
    _require_timestamp(receipt.get("installed_at"), field="FI static-provenance install receipt timestamp")
    packet_fields = {
        "campaign_id": control.campaign_binding.campaign_id,
        "packet_id": control.packet_id,
        "candidate_directory": str(control.candidate_directory),
        "control_packet_sha256": sha256_bytes(control.packet_payload),
        "campaign_binding_sha256": control.verified_packet["campaign_binding_sha256"],
        "signer_enrollment_certificate_sha256": control.verified_packet["signer_enrollment_certificate_sha256"],
        "source_role_config_sha256": control.verified_packet["source_role_config_sha256"],
        "static_assets_provenance_sha256": control.verified_packet["static_assets_provenance_sha256"],
        "source_transport_policy_sha256": control.verified_packet["source_transport_policy_sha256"],
        "exchange_object": dict(control.transport_receipt["object"]),
    }
    if any(receipt.get(field) != value for field, value in packet_fields.items()):
        raise StaticProvenanceReceiveRenderError(
            "FI static-provenance install receipt is not bound to the rendered packet"
        )
    _require_sha256(
        receipt.get("exchange_receive_receipt_sha256"),
        field="FI static-provenance exchange receive receipt checksum",
    )
    checksum = _require_sha256(
        receipt.get("receipt_sha256"), field="FI static-provenance install receipt checksum"
    )
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    if checksum != sha256_bytes(canonical_json_bytes(unsigned)):
        raise StaticProvenanceReceiveRenderError("FI static-provenance install receipt checksum is invalid")
    return {
        "status": "verified",
        "campaign_id": control.campaign_binding.campaign_id,
        "packet_id": control.packet_id,
        "exchange_object": dict(control.transport_receipt["object"]),
        "fi_install_receipt_sha256": checksum,
    }


def _read_presigned_url_stdin() -> str:
    try:
        payload = sys.stdin.buffer.read(MAX_URL_BYTES + 1)
    except OSError as exc:
        raise StaticProvenanceReceiveRenderError("cannot read static-provenance download URL from stdin") from exc
    if not payload or len(payload) > MAX_URL_BYTES:
        raise StaticProvenanceReceiveRenderError("static-provenance download URL stdin exceeds the fixed size bound")
    if payload.endswith(b"\n"):
        payload = payload[:-1]
    if not payload or b"\n" in payload or b"\r" in payload:
        raise StaticProvenanceReceiveRenderError("static-provenance download URL stdin is malformed")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StaticProvenanceReceiveRenderError("static-provenance download URL stdin is not UTF-8") from exc


def _base_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-transport-config", required=True, type=Path)
    parser.add_argument("--campaign-binding", required=True, type=Path)
    parser.add_argument("--source-adoption-package-directory", required=True, type=Path)
    parser.add_argument("--preparation-receipt", required=True, type=Path)
    parser.add_argument("--fi-install-receipt", required=True, type=Path)
    parser.add_argument("--packet-id", required=True)
    parser.add_argument("--transport-publish-receipt", required=True, type=Path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    render = actions.add_parser("render", help="render one pinned FI receive/install SSH command")
    _base_arguments(render)
    render.add_argument("--fi-known-hosts", required=True, type=Path)
    render.add_argument("--presigned-download-url-stdin", required=True, action="store_true")
    verify = actions.add_parser("verify-install", help="verify one URL-free FI install result")
    _base_arguments(verify)
    verify.add_argument("--install-output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        control = build_static_provenance_receive_control(
            source_transport_config=args.source_transport_config,
            campaign_binding=args.campaign_binding,
            source_adoption_package_directory=args.source_adoption_package_directory,
            preparation_receipt=args.preparation_receipt,
            fi_install_receipt=args.fi_install_receipt,
            packet_id=args.packet_id,
            transport_publish_receipt=args.transport_publish_receipt,
        )
        if args.action == "render":
            print(
                render_receive_install_command(
                    control=control,
                    fi_known_hosts=args.fi_known_hosts,
                    presigned_download_url=_read_presigned_url_stdin(),
                )
            )
        elif args.action == "verify-install":
            print(json.dumps(validate_fi_static_provenance_install_receipt(control=control, install_output=args.install_output), sort_keys=True))
        else:  # pragma: no cover - argparse dispatch invariant.
            raise StaticProvenanceReceiveRenderError("unsupported static-provenance receive action")
        return 0
    except StaticProvenanceReceiveRenderError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - CLI entry point.
    raise SystemExit(main())
