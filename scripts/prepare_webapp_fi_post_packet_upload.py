#!/usr/bin/env python3
"""Prepare one tightly-bound WebApp-FI post-packet source upload.

This FI-local entrypoint is deliberately smaller than the generic source
exchange.  It derives the only two post-packet upload requests from the
already installed static-provenance packet:

* ``raw-app-image`` from the fixed image-export output; and
* ``source-evidence`` from the fixed evidence-envelope output.

The operator may select only that strict enum and one safe identifier.  The
campaign, release, control revision, route, recipient, packet policy,
plaintext path, and prepared directory are all re-derived locally.  No
caller-supplied route, recipient, policy, or data path is accepted.

The command has no SSH, Object Storage client, Docker, service, container,
volume, current, migration, or data-plane capability.  It delegates only the
local age-encryption preparation to the co-shipped FI exchange.  A later
controller renderer supplies a transient presigned PUT URL to the existing
exchange's separate one-shot upload command.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Mapping, Sequence


THIS_SCRIPT_RELATIVE = "scripts/prepare_webapp_fi_post_packet_upload.py"
STATIC_PACKET_READER_MEMBER = "scripts/install_webapp_fi_static_provenance_control_packet.py"
EXCHANGE_MEMBER = "scripts/manage_webapp_fi_source_exchange.py"
INSTALL_RECEIPT_NAME = "source-adoption-install-receipt.json"
CONTROL_PACKET_DIRECTORY = "controller-static-provenance"
CONTROL_PACKET_NAME = "control-packet.json"
SOURCE_TRANSPORT_POLICY_NAME = "source-transport-policy.json"
STATIC_PACKET_RECEIPT_NAME = "static-provenance-install-receipt.json"

RAW_APP_IMAGE = "raw-app-image"
SOURCE_EVIDENCE = "source-evidence"
ARTIFACT_KINDS = frozenset((RAW_APP_IMAGE, SOURCE_EVIDENCE))

FI_SOURCE_EXPORT_ROOT = Path("/srv/trading-bot-three-site-staging-data/webapp-fi-source-exports")
FI_SOURCE_EVIDENCE_ROOT = Path("/srv/trading-bot-three-site-staging-data/webapp-fi-source-evidence")
RAW_APP_IMAGE_FILENAME = "webapp-fi-active-app-image.tar"
SOURCE_EVIDENCE_FILENAME = "source-evidence-envelope.json"

MAX_RECEIPT_BYTES = 1024 * 1024
FORBIDDEN_MARKERS = (
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


class PostPacketUploadError(RuntimeError):
    """One FI post-packet upload control is unsafe or unbound."""


@dataclasses.dataclass(frozen=True)
class PostPacketUploadControl:
    """The complete fixed local input set for one FI source upload."""

    candidate_directory: Path
    packet_directory: Path
    policy_path: Path
    policy: Any
    exchange: Any
    request: Any
    artifact_kind: str
    artifact_id: str
    plaintext_path: Path
    prepared_directory: Path
    static_packet_receipt_sha256: str


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_exact_sibling(filename: str, module_name: str) -> Any:
    if not isinstance(filename, str) or Path(filename).name != filename or filename in {"", ".", ".."}:
        raise RuntimeError("required sibling filename is invalid")
    source = Path(__file__).absolute()
    path = source.with_name(filename)
    if not source.is_absolute() or not path.is_absolute():  # pragma: no cover - Python invariant.
        raise RuntimeError("FI post-packet helper source is not absolute")
    current = Path(source.anchor)
    for component in path.parts[1:-1]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:  # pragma: no cover - deployment layout invariant.
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
    except OSError as exc:  # pragma: no cover - deployment layout invariant.
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


reader = _load_exact_sibling(
    "install_webapp_fi_static_provenance_control_packet.py",
    "_webapp_fi_post_packet_static_reader",
)


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise PostPacketUploadError("FI post-packet upload preparation must run as root")


def _candidate_directory() -> Path:
    """Return only the installed candidate containing this exact helper."""

    try:
        source = reader._require_absolute_canonical_path(
            Path(__file__).absolute(), field="FI post-packet upload helper"
        )
        candidate = reader._require_root_private_directory(
            source.parent.parent, field="installed source-adoption candidate"
        )
    except Exception as exc:
        raise PostPacketUploadError("FI post-packet helper is not in a root-only installed candidate") from exc
    if source != candidate / THIS_SCRIPT_RELATIVE:
        raise PostPacketUploadError("FI post-packet helper must run from its verified installed candidate")
    return candidate


def _read_member(reader_module: Any, *, candidate: Path, installed: Mapping[str, Any], relative: str) -> bytes:
    files = installed.get("files")
    if not isinstance(files, Mapping):
        raise PostPacketUploadError("installed source-adoption hashes are unavailable")
    expected = files.get(relative)
    if not isinstance(expected, str) or len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise PostPacketUploadError(f"installed {relative} hash is invalid")
    try:
        payload = reader_module._read_root_private_file(
            candidate / relative,
            field=f"installed {relative}",
            maximum_bytes=reader_module.MAX_INSTALLED_SOURCE_BYTES,
        )
    except Exception as exc:
        raise PostPacketUploadError(f"installed {relative} cannot be safely read") from exc
    if reader_module.sha256_bytes(payload) != expected:
        raise PostPacketUploadError(f"installed {relative} hash changed")
    return payload


def _load_verified_candidate() -> tuple[Any, Any, Any, dict[str, Any], Path]:
    candidate = _candidate_directory()
    try:
        installer, packet_control, transport, installed = reader._load_verified_installed_adoption(
            candidate / INSTALL_RECEIPT_NAME
        )
    except Exception as exc:
        raise PostPacketUploadError("installed source-adoption candidate cannot be verified") from exc
    if not isinstance(installed, Mapping) or installed.get("candidate") != candidate:
        raise PostPacketUploadError("installed source-adoption candidate changed while being verified")
    installed_value = dict(installed)
    helper_payload = _read_member(
        reader,
        candidate=candidate,
        installed=installed_value,
        relative=THIS_SCRIPT_RELATIVE,
    )
    try:
        own_payload = reader._read_root_private_file(
            Path(__file__).absolute(),
            field="FI post-packet upload helper",
            maximum_bytes=reader.MAX_INSTALLED_SOURCE_BYTES,
        )
    except Exception as exc:
        raise PostPacketUploadError("FI post-packet upload helper cannot be safely read") from exc
    if own_payload != helper_payload:
        raise PostPacketUploadError("FI post-packet upload helper changed while being verified")
    return installer, packet_control, transport, installed_value, candidate


def _load_candidate_exchange(*, candidate: Path, installed: Mapping[str, Any]) -> Any:
    payload = _read_member(reader, candidate=candidate, installed=installed, relative=EXCHANGE_MEMBER)
    try:
        exchange = reader._execute_verified_module(
            "_verified_webapp_fi_post_packet_exchange",
            candidate / EXCHANGE_MEMBER,
            payload,
        )
    except Exception as exc:
        raise PostPacketUploadError("verified FI source exchange cannot be loaded") from exc
    required = (
        "load_policy",
        "prepare_upload",
        "_require_root_private_file",
        "contract",
        "PREPARED_RECEIPT_NAME",
    )
    if any(not hasattr(exchange, item) for item in required):
        raise PostPacketUploadError("verified FI source exchange contract is incompatible")
    return exchange


def _require_artifact_kind(value: object) -> str:
    if not isinstance(value, str) or value not in ARTIFACT_KINDS:
        raise PostPacketUploadError("artifact_kind must be raw-app-image or source-evidence")
    return value


def _require_artifact_id(packet_control: Any, value: object) -> str:
    try:
        return packet_control._require_identifier(value, field="post-packet artifact ID")
    except Exception as exc:
        raise PostPacketUploadError("artifact_id is invalid") from exc


def _parse_static_receipt(
    *,
    packet_control: Any,
    transport: Any,
    candidate: Path,
    packet_directory: Path,
    packet_id: str,
    packet_payload: bytes,
    verified_packet: Mapping[str, Any],
    payload: bytes,
) -> str:
    """Validate the FI-installed packet receipt against the local packet bytes."""

    if any(marker in payload.lower() for marker in FORBIDDEN_MARKERS):
        raise PostPacketUploadError("FI static-packet install receipt is not URL-free and nonsecret")
    try:
        value = reader._parse_canonical_json(
            payload,
            field="FI static-packet install receipt",
            maximum_bytes=MAX_RECEIPT_BYTES,
        )
    except Exception as exc:
        raise PostPacketUploadError("FI static-packet install receipt is invalid") from exc
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
        or value.get("schema") != reader.READ_RECEIPT_SCHEMA
        or value.get("status") != "installed"
    ):
        raise PostPacketUploadError("FI static-packet install receipt is unsupported")
    unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if value.get("receipt_sha256") != reader.sha256_bytes(reader.canonical_json_bytes(unsigned)):
        raise PostPacketUploadError("FI static-packet install receipt checksum is invalid")
    try:
        packet_control._require_timestamp(value.get("installed_at"), field="FI static-packet install timestamp")
    except Exception as exc:
        raise PostPacketUploadError("FI static-packet install receipt timestamp is invalid") from exc
    binding = verified_packet.get("campaign_binding")
    policy = verified_packet.get("source_transport_policy")
    if not isinstance(binding, Mapping) or not isinstance(policy, Mapping):
        raise PostPacketUploadError("verified static packet is incomplete")
    expected_key = reader._expected_object_key(
        transport=transport,
        campaign_binding=binding,
        packet_id=packet_id,
        policy=policy,
    )
    try:
        descriptor = transport.validate_object_descriptor(
            value.get("exchange_object"), maximum_plaintext_bytes=policy["maximum_plaintext_bytes"]
        )
    except Exception as exc:
        raise PostPacketUploadError("FI static-packet install receipt object is invalid") from exc
    if (
        value.get("candidate_directory") != str(candidate)
        or value.get("campaign_id") != binding.get("campaign_id")
        or value.get("packet_id") != packet_id
        or value.get("control_packet_sha256") != reader.sha256_bytes(packet_payload)
        or value.get("campaign_binding_sha256") != verified_packet.get("campaign_binding_sha256")
        or value.get("signer_enrollment_certificate_sha256")
        != verified_packet.get("signer_enrollment_certificate_sha256")
        or value.get("source_role_config_sha256") != verified_packet.get("source_role_config_sha256")
        or value.get("static_assets_provenance_sha256")
        != verified_packet.get("static_assets_provenance_sha256")
        or value.get("source_transport_policy_sha256")
        != verified_packet.get("source_transport_policy_sha256")
        or descriptor != value.get("exchange_object")
        or descriptor.get("object_key") != expected_key
        or packet_directory.parent != candidate / CONTROL_PACKET_DIRECTORY
    ):
        raise PostPacketUploadError("FI static-packet install receipt is not bound to the installed packet")
    return sha256_bytes(payload)


def _load_static_packet_state(
    *,
    packet_id: object,
) -> tuple[Any, Any, Any, Mapping[str, Any], Path, Path, Path, str]:
    """Verify the installed packet and return no caller-selected policy state."""

    installer, packet_control, transport, installed, candidate = _load_verified_candidate()
    del installer
    packet = _require_artifact_id(packet_control, packet_id)
    try:
        packet_directory = reader._require_root_private_directory(
            candidate / CONTROL_PACKET_DIRECTORY / packet,
            field="installed static-provenance packet directory",
        )
        packet_payload = reader._read_root_private_file(
            packet_directory / CONTROL_PACKET_NAME,
            field="installed static-provenance control packet",
            maximum_bytes=packet_control.MAX_PACKET_BYTES,
        )
        policy_path = packet_directory / SOURCE_TRANSPORT_POLICY_NAME
        policy_payload = reader._read_root_private_file(
            policy_path,
            field="installed static-provenance policy",
            maximum_bytes=packet_control.MAX_POLICY_BYTES,
        )
        receipt_payload = reader._read_root_private_file(
            packet_directory / STATIC_PACKET_RECEIPT_NAME,
            field="installed static-provenance receipt",
            maximum_bytes=MAX_RECEIPT_BYTES,
        )
    except PostPacketUploadError:
        raise
    except Exception as exc:
        raise PostPacketUploadError("installed static-provenance packet files cannot be safely read") from exc
    package = installed.get("package")
    if not isinstance(package, Mapping) or not isinstance(package.get("controller_public_key_base64"), str):
        raise PostPacketUploadError("installed source-adoption controller key is invalid")
    try:
        verified_packet = packet_control.verify_control_packet_payload(
            payload=packet_payload,
            pinned_controller_public_key_base64=package["controller_public_key_base64"],
        )
        campaign_id = verified_packet["campaign_binding"]["campaign_id"]
        campaign = packet_control._require_identifier(campaign_id, field="campaign ID", campaign=True)
        campaigns_root = reader._require_root_private_directory(
            reader.CAMPAIGN_ROOT, field="FI campaign root"
        )
        campaign_directory = reader._require_root_private_directory(
            campaigns_root / campaign, field="FI campaign directory"
        )
        source_phase = reader._require_root_private_directory(
            campaign_directory / reader.SOURCE_PHASE_DIRECTORY,
            field="FI campaign source-phase directory",
        )
        binding_path = source_phase / reader.CAMPAIGN_BINDING_FILENAME
        binding_payload = reader._read_root_private_file(
            binding_path,
            field="installed FI campaign binding",
            maximum_bytes=packet_control.MAX_ARTIFACT_BYTES,
        )
        binding_identity = packet_control.binding_identity_from_payload(binding_payload)
    except Exception as exc:
        raise PostPacketUploadError("installed static-provenance packet cannot be verified") from exc
    if (
        verified_packet.get("packet_id") != packet
        or binding_payload != verified_packet.get("campaign_binding_payload")
        or dict(binding_identity) != dict(verified_packet.get("campaign_binding", {}))
        or policy_payload != verified_packet.get("source_transport_policy_payload")
    ):
        raise PostPacketUploadError("installed static-provenance packet is not bound to the FI campaign authority")
    receipt_sha = _parse_static_receipt(
        packet_control=packet_control,
        transport=transport,
        candidate=candidate,
        packet_directory=packet_directory,
        packet_id=packet,
        packet_payload=packet_payload,
        verified_packet=verified_packet,
        payload=receipt_payload,
    )
    return packet_control, transport, installed, verified_packet, candidate, packet_directory, policy_path, receipt_sha


def _validate_exchange_policy(*, exchange: Any, policy_path: Path, packet_policy: Mapping[str, Any]) -> Any:
    try:
        policy = exchange.load_policy(policy_path)
    except Exception as exc:
        raise PostPacketUploadError("installed static-packet transport policy cannot be loaded") from exc
    expected = {
        "endpoint": "https://" + packet_policy.get("endpoint_host", ""),
        "region": packet_policy.get("region"),
        "bucket": packet_policy.get("bucket"),
        "prefix": packet_policy.get("prefix"),
        "age_binary": packet_policy.get("age_binary"),
        "workspace": str(packet_policy.get("workspace")),
        "controller_age_recipient": packet_policy.get("controller_age_recipient"),
        "webapp_fi_age_recipient": packet_policy.get("webapp_fi_age_recipient"),
        "webapp_ir_age_recipient": packet_policy.get("webapp_ir_age_recipient"),
        "maximum_plaintext_bytes": packet_policy.get("maximum_plaintext_bytes"),
    }
    actual = {
        "endpoint": getattr(policy, "endpoint", None),
        "region": getattr(policy, "region", None),
        "bucket": getattr(policy, "bucket", None),
        "prefix": getattr(policy, "prefix", None),
        "age_binary": getattr(policy, "age_binary", None),
        "workspace": str(getattr(policy, "workspace", "")),
        "controller_age_recipient": getattr(policy, "controller_age_recipient", None),
        "webapp_fi_age_recipient": getattr(policy, "webapp_fi_age_recipient", None),
        "webapp_ir_age_recipient": getattr(policy, "webapp_ir_age_recipient", None),
        "maximum_plaintext_bytes": getattr(policy, "maximum_plaintext_bytes", None),
    }
    if actual != expected:
        raise PostPacketUploadError("installed static-packet transport policy changed while being loaded")
    return policy


def _plaintext_path(*, artifact_kind: str, campaign_id: str, artifact_id: str) -> Path:
    if artifact_kind == RAW_APP_IMAGE:
        return FI_SOURCE_EXPORT_ROOT / campaign_id / artifact_id / RAW_APP_IMAGE_FILENAME
    if artifact_kind == SOURCE_EVIDENCE:
        return FI_SOURCE_EVIDENCE_ROOT / campaign_id / artifact_id / SOURCE_EVIDENCE_FILENAME
    raise PostPacketUploadError("artifact_kind must be raw-app-image or source-evidence")


def derive_post_packet_upload(
    *,
    packet_id: object,
    artifact_kind: object,
    artifact_id: object,
) -> PostPacketUploadControl:
    """Return one fully-derived FI upload control without writing anything."""

    _require_root_execution()
    kind = _require_artifact_kind(artifact_kind)
    (
        packet_control,
        _transport,
        installed,
        verified_packet,
        candidate,
        packet_directory,
        policy_path,
        receipt_sha,
    ) = _load_static_packet_state(packet_id=packet_id)
    identifier = _require_artifact_id(packet_control, artifact_id)
    exchange = _load_candidate_exchange(candidate=candidate, installed=installed)
    packet_policy = verified_packet.get("source_transport_policy")
    binding = verified_packet.get("campaign_binding")
    if not isinstance(packet_policy, Mapping) or not isinstance(binding, Mapping):
        raise PostPacketUploadError("verified static packet is incomplete")
    policy = _validate_exchange_policy(exchange=exchange, policy_path=policy_path, packet_policy=packet_policy)
    try:
        request = exchange.contract.SourceObjectRequest(
            campaign_id=binding.get("campaign_id"),
            release_sha=binding.get("application", {}).get("release_sha") if isinstance(binding.get("application"), Mapping) else None,
            control_commit=binding.get("tooling", {}).get("control_commit") if isinstance(binding.get("tooling"), Mapping) else None,
            control_tree=binding.get("tooling", {}).get("control_tree") if isinstance(binding.get("tooling"), Mapping) else None,
            source_site="webapp_fi",
            destination_site="controller",
            object_kind=kind,
            object_id=identifier,
            mode=exchange.contract.SINGLE_MODE,
            recipients=(policy.controller_age_recipient,),
        )
        recipients = exchange.contract.validate_request(policy, request)
    except Exception as exc:
        raise PostPacketUploadError("post-packet FI upload route is invalid") from exc
    if tuple(recipients) != (policy.controller_age_recipient,) or tuple(request.recipients) != recipients:
        raise PostPacketUploadError("post-packet FI upload is not controller-recipient-only")
    plaintext = _plaintext_path(
        artifact_kind=kind,
        campaign_id=request.campaign_id,
        artifact_id=identifier,
    )
    prepared = Path(policy.workspace) / ("post-packet-" + kind + "-" + identifier)
    if prepared.parent != Path(policy.workspace) or prepared.name != "post-packet-" + kind + "-" + identifier:
        raise PostPacketUploadError("post-packet FI prepared directory is invalid")
    return PostPacketUploadControl(
        candidate_directory=candidate,
        packet_directory=packet_directory,
        policy_path=policy_path,
        policy=policy,
        exchange=exchange,
        request=request,
        artifact_kind=kind,
        artifact_id=identifier,
        plaintext_path=plaintext,
        prepared_directory=prepared,
        static_packet_receipt_sha256=receipt_sha,
    )


def prepare_post_packet_upload(
    *,
    packet_id: object,
    artifact_kind: object,
    artifact_id: object,
) -> dict[str, Any]:
    """Encrypt exactly one derived local source artifact; never upload it."""

    control = derive_post_packet_upload(
        packet_id=packet_id,
        artifact_kind=artifact_kind,
        artifact_id=artifact_id,
    )
    exchange = control.exchange
    # Validate the plaintext before the exchange creates its immutable
    # prepared directory, so a missing/unsafe input leaves no new residue.
    try:
        exchange._require_root_private_file(
            control.plaintext_path,
            field="derived FI post-packet plaintext",
            maximum_bytes=control.policy.maximum_plaintext_bytes,
        )
    except Exception as exc:
        raise PostPacketUploadError("derived FI post-packet plaintext is unsafe") from exc
    if control.prepared_directory.exists() or control.prepared_directory.is_symlink():
        raise PostPacketUploadError("refusing to reuse or overwrite a post-packet FI prepared directory")
    try:
        receipt = exchange.prepare_upload(
            policy=control.policy,
            request=control.request,
            plaintext_path=control.plaintext_path,
            prepared_dir=control.prepared_directory,
        )
    except Exception as exc:
        raise PostPacketUploadError("FI post-packet upload encryption preparation failed") from exc
    return {
        "status": "prepared",
        "campaign_id": control.request.campaign_id,
        "packet_id": str(packet_id),
        "artifact_kind": control.artifact_kind,
        "artifact_id": control.artifact_id,
        "object_key": exchange.contract.source_object_key(control.policy, control.request),
        "prepared_directory": str(control.prepared_directory),
        "prepared_receipt_sha256": receipt.get("prepared_sha256"),
        "static_packet_receipt_sha256": control.static_packet_receipt_sha256,
    }


def upload_post_packet_prepared(
    *,
    packet_id: object,
    artifact_kind: object,
    artifact_id: object,
    upload_url: str,
) -> dict[str, Any]:
    """PUT one already-prepared, packet-derived FI source artifact.

    The only capability supplied by the caller is the transient presigned
    URL.  The packet, object kind, identifier, policy, recipient, and local
    prepared directory are independently re-derived before the exchange
    primitive is allowed to contact Object Storage.
    """

    control = derive_post_packet_upload(
        packet_id=packet_id,
        artifact_kind=artifact_kind,
        artifact_id=artifact_id,
    )
    try:
        report = control.exchange.upload_prepared(
            policy=control.policy,
            prepared_dir=control.prepared_directory,
            upload_url=upload_url,
        )
        payload = canonical_json_bytes(report) + b"\n"
        verified = control.exchange.verify_upload_report(policy=control.policy, payload=payload)
        request = control.exchange._request_from_value(
            verified.get("request"),
            policy=control.policy,
            field="FI post-packet upload report request",
        )
    except Exception as exc:
        raise PostPacketUploadError("FI post-packet upload failed") from exc
    if (
        request != control.request
        or verified.get("object", {}).get("object_key")
        != control.exchange.contract.source_object_key(control.policy, control.request)
    ):
        raise PostPacketUploadError("FI post-packet upload report is not bound to the derived request")
    return dict(verified)


def _print_result(value: Mapping[str, Any]) -> None:
    sys.stdout.buffer.write(canonical_json_bytes(value) + b"\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    prepare = actions.add_parser("prepare-upload", help="encrypt one derived FI post-packet object")
    prepare.add_argument("--packet-id", required=True)
    prepare.add_argument("--artifact-kind", required=True, choices=sorted(ARTIFACT_KINDS))
    prepare.add_argument("--artifact-id", required=True)
    upload = actions.add_parser("upload-prepared", help="PUT one derived FI post-packet object")
    upload.add_argument("--packet-id", required=True)
    upload.add_argument("--artifact-kind", required=True, choices=sorted(ARTIFACT_KINDS))
    upload.add_argument("--artifact-id", required=True)
    upload.add_argument("--upload-url", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "prepare-upload":
            result = prepare_post_packet_upload(
                packet_id=args.packet_id,
                artifact_kind=args.artifact_kind,
                artifact_id=args.artifact_id,
            )
        elif args.action == "upload-prepared":
            result = upload_post_packet_prepared(
                packet_id=args.packet_id,
                artifact_kind=args.artifact_kind,
                artifact_id=args.artifact_id,
                upload_url=args.upload_url,
            )
        else:  # pragma: no cover - argparse makes this unreachable.
            raise PostPacketUploadError("unsupported FI post-packet upload action")
        _print_result(result)
        return 0
    except PostPacketUploadError as exc:
        _print_result({"status": "blocked", "error": str(exc), "error_class": exc.__class__.__name__})
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
