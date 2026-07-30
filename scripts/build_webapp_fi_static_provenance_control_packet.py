#!/usr/bin/env python3
"""Seal one controller-to-WebApp-FI static-provenance control packet.

This controller-local helper turns four already-existing, root-only control
inputs into one canonical packet for the existing
``controller -> webapp_fi/static-provenance`` Object Storage route.  It has no
Object Storage, SSH, Docker, service, container, volume, current, migration,
or data-plane capability.  Publication remains a separate explicit action.

The packet output is fixed below the controller staging root by campaign and
packet ID, is create-only, and deliberately contains no credential, private
key, delivery-Object VersionId, or presigned URL.  The two already-signed
artifacts retain their schema-required historical object descriptors.  A
normal exchange policy with a public endpoint is projected to the packet's
URL-free policy form before it is sealed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Mapping, Sequence


CONTROLLER_CAMPAIGN_ROOT = Path("/etc/trading-bot-three-site/campaigns")
CONTROL_PACKET_ROOT = Path(
    "/srv/trading-bot-three-site-staging-data/controller/webapp-fi-static-provenance-control-packets"
)
SOURCE_PHASE_DIRECTORY = "webapp-fi-source"
CAMPAIGN_BINDING_FILENAME = "campaign-binding.json"
CONTROL_PACKET_FILENAME = "static-provenance.json"

MAX_CONTROL_INPUT_BYTES = 8 * 1024 * 1024
MAX_POLICY_INPUT_BYTES = 1024 * 1024


class StaticProvenanceControlPacketBuildError(RuntimeError):
    """A controller static-provenance control packet cannot be safely sealed."""


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise StaticProvenanceControlPacketBuildError("controller static-provenance packet operations must run as root")


def _require_absolute_canonical_path(path: Path, *, field: str) -> Path:
    candidate = Path(path)
    if (
        "\x00" in str(candidate)
        or not candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts[1:])
        or str(candidate) != os.path.normpath(str(candidate))
    ):
        raise StaticProvenanceControlPacketBuildError(f"{field} must be one canonical absolute path")
    return candidate


def _require_root_controlled_ancestors(path: Path, *, field: str) -> None:
    path = _require_absolute_canonical_path(path, field=field)
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise StaticProvenanceControlPacketBuildError(f"cannot inspect {field} parent") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or ((mode & 0o022) and not (metadata.st_mode & stat.S_ISVTX))
        ):
            raise StaticProvenanceControlPacketBuildError(f"{field} parent is unsafe")


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
        raise StaticProvenanceControlPacketBuildError(f"cannot inspect {field}") from exc
    if (
        resolved != directory
        or stat.S_ISLNK(before.st_mode)
        or stat.S_ISLNK(target.st_mode)
        or not stat.S_ISDIR(target.st_mode)
        or target.st_uid != 0
        or stat.S_IMODE(target.st_mode) != 0o700
    ):
        raise StaticProvenanceControlPacketBuildError(f"{field} must be one root-only mode 0700 non-symlink directory")
    return resolved


def _read_root_private_file(path: Path, *, field: str, maximum_bytes: int) -> bytes:
    source = _require_absolute_canonical_path(Path(path), field=field)
    _require_root_controlled_ancestors(source.parent, field=field)
    try:
        before = source.lstat()
    except OSError as exc:
        raise StaticProvenanceControlPacketBuildError(f"cannot inspect {field}") from exc
    if not _safe_private_file(before, maximum_bytes=maximum_bytes):
        raise StaticProvenanceControlPacketBuildError(f"{field} is unsafe")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:  # pragma: no cover - Linux deployment invariant.
        raise StaticProvenanceControlPacketBuildError("secure no-follow file access is unavailable")
    try:
        descriptor = os.open(str(source), os.O_RDONLY | os.O_CLOEXEC | no_follow)
    except OSError as exc:
        raise StaticProvenanceControlPacketBuildError(f"cannot securely open {field}") from exc
    try:
        opened = os.fstat(descriptor)
        if not _same_file_metadata(before, opened) or not _safe_private_file(opened, maximum_bytes=maximum_bytes):
            raise StaticProvenanceControlPacketBuildError(f"{field} changed while opening")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise StaticProvenanceControlPacketBuildError(f"{field} is too large")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        payload = b"".join(chunks)
        if len(payload) != opened.st_size or not _same_file_metadata(opened, after):
            raise StaticProvenanceControlPacketBuildError(f"{field} changed while reading")
        return payload
    except OSError as exc:
        raise StaticProvenanceControlPacketBuildError(f"cannot read {field}") from exc
    finally:
        os.close(descriptor)


def _create_or_require_root_private_directory(parent: Path, name: str, *, field: str) -> Path:
    parent = _require_root_private_directory(parent, field=field + " parent")
    if not isinstance(name, str) or not control.IDENTIFIER_RE.fullmatch(name):
        raise StaticProvenanceControlPacketBuildError(f"{field} name is invalid")
    child = parent / name
    try:
        os.mkdir(child, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise StaticProvenanceControlPacketBuildError(f"cannot create {field}") from exc
    try:
        os.chmod(child, 0o700)
    except OSError as exc:
        raise StaticProvenanceControlPacketBuildError(f"cannot protect {field}") from exc
    return _require_root_private_directory(child, field=field)


def _create_new_root_private_directory(parent: Path, name: str, *, field: str) -> Path:
    parent = _require_root_private_directory(parent, field=field + " parent")
    if not isinstance(name, str) or not control.IDENTIFIER_RE.fullmatch(name):
        raise StaticProvenanceControlPacketBuildError(f"{field} name is invalid")
    child = parent / name
    try:
        child.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise StaticProvenanceControlPacketBuildError(f"cannot inspect {field}") from exc
    else:
        raise StaticProvenanceControlPacketBuildError(f"refusing to reuse or overwrite existing {field}")
    try:
        os.mkdir(child, 0o700)
        os.chmod(child, 0o700)
    except OSError as exc:
        raise StaticProvenanceControlPacketBuildError(f"cannot create {field}") from exc
    return _require_root_private_directory(child, field=field)


def _write_new_root_private_file(path: Path, payload: bytes, *, field: str) -> None:
    destination = _require_absolute_canonical_path(Path(path), field=field)
    _require_root_private_directory(destination.parent, field=field + " parent")
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= control.MAX_PACKET_BYTES:
        raise StaticProvenanceControlPacketBuildError(f"{field} payload is invalid")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:  # pragma: no cover - Linux deployment invariant.
        raise StaticProvenanceControlPacketBuildError("secure no-follow file creation is unavailable")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | no_follow
    try:
        descriptor = os.open(str(destination), flags, 0o600)
    except FileExistsError as exc:
        raise StaticProvenanceControlPacketBuildError(f"refusing to reuse or overwrite existing {field}") from exc
    except OSError as exc:
        raise StaticProvenanceControlPacketBuildError(f"cannot create {field}") from exc
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:  # pragma: no cover - regular file writes do not return zero.
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if not _safe_private_file(metadata, maximum_bytes=control.MAX_PACKET_BYTES) or metadata.st_size != len(payload):
            raise StaticProvenanceControlPacketBuildError(f"new {field} is unsafe")
    except OSError as exc:
        raise StaticProvenanceControlPacketBuildError(f"cannot durably create {field}") from exc
    finally:
        os.close(descriptor)


def _load_exact_sibling(filename: str, module_name: str) -> Any:
    if not isinstance(filename, str) or Path(filename).name != filename or filename in {"", ".", ".."}:
        raise StaticProvenanceControlPacketBuildError("required sibling filename is invalid")
    source = _require_absolute_canonical_path(Path(__file__).absolute(), field="packet builder script")
    path = source.with_name(filename)
    _require_root_controlled_ancestors(path.parent, field=f"required sibling {filename}")
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise StaticProvenanceControlPacketBuildError(f"cannot inspect required sibling {filename}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or metadata.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
    ):
        raise StaticProvenanceControlPacketBuildError(f"required sibling {filename} is unsafe")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise StaticProvenanceControlPacketBuildError(f"cannot load required sibling {filename}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        loaded = getattr(module, "__file__", None)
        if not isinstance(loaded, str) or Path(loaded).resolve(strict=True) != path:
            raise StaticProvenanceControlPacketBuildError(f"required sibling {filename} did not load from its exact path")
    except BaseException:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
        raise
    return module


control = _load_exact_sibling(
    "webapp_fi_static_provenance_control_packet.py",
    "_webapp_fi_static_provenance_control_packet_builder",
)


def _load_campaign_bound_controller_signer(campaign_binding: Path) -> Any:
    """Load the sole controller signer selected by the canonical binding."""

    helper = _load_exact_sibling(
        "manage_controller_campaign_signing_key.py",
        "_static_packet_campaign_signing_key",
    )
    try:
        return helper.load_verified_campaign_signer(campaign_binding_path=Path(campaign_binding))
    except Exception as exc:
        raise StaticProvenanceControlPacketBuildError(
            "controller static-provenance signing authority is not bound to the canonical campaign"
        ) from exc


def _campaign_signing_authority_identity(authority: Any) -> tuple[str, str, str, str]:
    try:
        return (
            authority.campaign_binding.campaign_id,
            authority.campaign_binding.binding_sha256,
            authority.signing_key.public_key_base64,
            authority.signing_key.receipt_sha256,
        )
    except (AttributeError, TypeError) as exc:
        raise StaticProvenanceControlPacketBuildError(
            "controller static-provenance signing authority is incomplete"
        ) from exc


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def campaign_binding_path(campaign_id: str) -> Path:
    campaign = control._require_identifier(campaign_id, field="campaign ID", campaign=True)
    root = _require_root_private_directory(CONTROLLER_CAMPAIGN_ROOT, field="controller campaign root")
    campaign_directory = _require_root_private_directory(root / campaign, field="controller campaign directory")
    source_phase = _require_root_private_directory(
        campaign_directory / SOURCE_PHASE_DIRECTORY,
        field="controller source-phase directory",
    )
    return source_phase / CAMPAIGN_BINDING_FILENAME


def control_packet_path(*, campaign_id: str, packet_id: str) -> Path:
    campaign = control._require_identifier(campaign_id, field="campaign ID", campaign=True)
    packet = control._require_identifier(packet_id, field="control packet ID")
    root = _require_root_private_directory(CONTROL_PACKET_ROOT, field="controller control-packet root")
    return root / campaign / packet / CONTROL_PACKET_FILENAME


def _load_inputs(
    *,
    campaign_id: str,
    signer_enrollment_certificate: Path,
    source_role_config: Path,
    static_assets_provenance: Path,
    source_transport_policy: Path,
) -> tuple[bytes, bytes, bytes, bytes, bytes]:
    return (
        _read_root_private_file(campaign_binding_path(campaign_id), field="campaign binding", maximum_bytes=MAX_CONTROL_INPUT_BYTES),
        _read_root_private_file(
            signer_enrollment_certificate,
            field="signer enrollment certificate",
            maximum_bytes=MAX_CONTROL_INPUT_BYTES,
        ),
        _read_root_private_file(source_role_config, field="FI source role config", maximum_bytes=MAX_CONTROL_INPUT_BYTES),
        _read_root_private_file(
            static_assets_provenance,
            field="static assets provenance",
            maximum_bytes=MAX_CONTROL_INPUT_BYTES,
        ),
        _read_root_private_file(
            source_transport_policy,
            field="FI source transport policy",
            maximum_bytes=MAX_POLICY_INPUT_BYTES,
        ),
    )


def _seal_packet(
    *,
    inputs: tuple[bytes, bytes, bytes, bytes, bytes],
    campaign: str,
    packet: str,
    timestamp: str,
    authority: Any,
) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    """Validate all immutable inputs and seal one packet without writing it."""

    try:
        binding = control.binding_identity_from_payload(inputs[0])
        campaign_binding = authority.campaign_binding
        if (
            binding["campaign_id"] != campaign
            or binding["campaign_id"] != campaign_binding.campaign_id
            or binding["binding_sha256"] != campaign_binding.binding_sha256
            or binding["application"]
            != {
                "release_sha": campaign_binding.application_release_sha,
                "release_tree": campaign_binding.application_release_tree,
                "expected_alembic_revision": campaign_binding.expected_alembic_revision,
            }
            or binding["tooling"]
            != {
                "control_commit": campaign_binding.control_commit,
                "control_tree": campaign_binding.control_tree,
            }
        ):
            raise StaticProvenanceControlPacketBuildError(
                "controller campaign binding does not match the fixed signing authority"
            )
        payload = control.build_control_packet_payload_with_signer(
            created_at=timestamp,
            campaign_binding_payload=inputs[0],
            signer_enrollment_certificate_payload=inputs[1],
            source_role_config_payload=inputs[2],
            static_assets_provenance_payload=inputs[3],
            source_transport_policy_payload=inputs[4],
            packet_id=packet,
            controller_signer=authority.signer,
            controller_public_key_base64=authority.signing_key.public_key_base64,
        )
        verified = control.verify_control_packet_payload(
            payload=payload,
            pinned_controller_public_key_base64=authority.signing_key.public_key_base64,
            expected_campaign_binding_identity=binding,
        )
    except control.StaticProvenanceControlPacketError as exc:
        raise StaticProvenanceControlPacketBuildError(str(exc)) from exc
    return binding, payload, verified


def build_static_provenance_control_packet(
    *,
    campaign_id: str,
    packet_id: str,
    signer_enrollment_certificate: Path,
    source_role_config: Path,
    static_assets_provenance: Path,
    source_transport_policy: Path,
    apply: bool,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Validate inputs and optionally create exactly one fixed packet file."""

    _require_root_execution()
    campaign = control._require_identifier(campaign_id, field="campaign ID", campaign=True)
    packet = control._require_identifier(packet_id, field="control packet ID")
    output = control_packet_path(campaign_id=campaign, packet_id=packet)
    binding_path = campaign_binding_path(campaign)
    authority = _load_campaign_bound_controller_signer(binding_path)
    authority_identity = _campaign_signing_authority_identity(authority)
    inputs = _load_inputs(
        campaign_id=campaign,
        signer_enrollment_certificate=Path(signer_enrollment_certificate),
        source_role_config=Path(source_role_config),
        static_assets_provenance=Path(static_assets_provenance),
        source_transport_policy=Path(source_transport_policy),
    )
    timestamp = created_at or utc_now()
    binding, payload, verified = _seal_packet(
        inputs=inputs,
        campaign=campaign,
        packet=packet,
        timestamp=timestamp,
        authority=authority,
    )
    if output.exists() or output.is_symlink() or output.parent.exists() or output.parent.is_symlink():
        raise StaticProvenanceControlPacketBuildError("refusing to reuse or overwrite a control packet candidate")
    result = {
        "status": "sealed" if apply else "planned",
        "campaign_id": campaign,
        "packet_id": packet,
        "output_path": str(output),
        "sha256": control.sha256_bytes(payload),
        "bytes": len(payload),
        "controller_public_key_base64": verified["controller_public_key_base64"],
        "source_transport_policy_sha256": verified["source_transport_policy_sha256"],
    }
    if not apply:
        return result
    final_authority = _load_campaign_bound_controller_signer(binding_path)
    if _campaign_signing_authority_identity(final_authority) != authority_identity:
        raise StaticProvenanceControlPacketBuildError(
            "canonical campaign signing authority changed before control-packet write"
        )
    final_inputs = _load_inputs(
        campaign_id=campaign,
        signer_enrollment_certificate=Path(signer_enrollment_certificate),
        source_role_config=Path(source_role_config),
        static_assets_provenance=Path(static_assets_provenance),
        source_transport_policy=Path(source_transport_policy),
    )
    final_binding, final_payload, final_verified = _seal_packet(
        inputs=final_inputs,
        campaign=campaign,
        packet=packet,
        timestamp=timestamp,
        authority=final_authority,
    )
    if final_binding != binding or final_payload != payload or final_verified != verified:
        raise StaticProvenanceControlPacketBuildError(
            "static-provenance inputs changed before control-packet write"
        )
    root = _require_root_private_directory(CONTROL_PACKET_ROOT, field="controller control-packet root")
    campaign_directory = _create_or_require_root_private_directory(
        root,
        campaign,
        field="controller control-packet campaign directory",
    )
    packet_directory = _create_new_root_private_directory(
        campaign_directory,
        packet,
        field="controller control-packet candidate",
    )
    _write_new_root_private_file(packet_directory / CONTROL_PACKET_FILENAME, payload, field="control packet")
    created = _read_root_private_file(
        packet_directory / CONTROL_PACKET_FILENAME,
        field="created control packet",
        maximum_bytes=control.MAX_PACKET_BYTES,
    )
    if created != payload:
        raise StaticProvenanceControlPacketBuildError("created control packet changed before verification")
    try:
        control.verify_control_packet_payload(
            payload=created,
            pinned_controller_public_key_base64=verified["controller_public_key_base64"],
            expected_campaign_binding_identity=binding,
        )
    except control.StaticProvenanceControlPacketError as exc:  # pragma: no cover - already checked above.
        raise StaticProvenanceControlPacketBuildError("created control packet cannot be verified") from exc
    return result


def _print_result(value: Mapping[str, Any]) -> None:
    sys.stdout.buffer.write(control.canonical_json_bytes(value) + b"\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--packet-id", required=True)
    parser.add_argument("--signer-enrollment-certificate", required=True, type=Path)
    parser.add_argument("--source-role-config", required=True, type=Path)
    parser.add_argument("--static-assets-provenance", required=True, type=Path)
    parser.add_argument("--source-transport-policy", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = build_static_provenance_control_packet(
            campaign_id=args.campaign_id,
            packet_id=args.packet_id,
            signer_enrollment_certificate=args.signer_enrollment_certificate,
            source_role_config=args.source_role_config,
            static_assets_provenance=args.static_assets_provenance,
            source_transport_policy=args.source_transport_policy,
            apply=args.apply,
        )
        _print_result(result)
        return 0
    except StaticProvenanceControlPacketBuildError as exc:
        _print_result({"status": "blocked", "error": str(exc), "error_class": exc.__class__.__name__})
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
