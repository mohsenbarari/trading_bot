#!/usr/bin/env python3
"""Receive one FI-produced source object into a controller-only candidate.

This is the controller half of the WebApp-FI source exchange.  It accepts
only a root-only FI upload report, revalidates it through the exact exchange
and transport contracts, reads back that report's sole immutable Object
Storage version, and age-decrypts it into a fresh campaign-derived candidate.
Candidate payloads live only below the fixed root-only controller staging-data
root, never beside the campaign binding or identity under ``/etc``.

Only ``static`` and ``raw-app-image`` are supported.  The command never
starts a container or service, invokes Docker or SSH, changes ``current``, or
changes a volume or application data.  It creates no new persistent schema:
the sole JSON output in a successful candidate is the existing readback record
consumed by the corresponding controller adopter.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence


def _require_root_controlled_directory_chain(path: Path, *, field: str) -> None:
    """Require a stable root-owned code lookup path before importing a sibling."""

    if not path.is_absolute():
        raise RuntimeError(f"{field} parent must be absolute")
    current = Path(path.anchor)
    components = (current,)
    for component in path.parts[1:]:
        current = current / component
        components += (current,)
    for current in components:
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
    """Return one exact root-owned, non-writable code file without symlinks."""

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
    """Load one root-controlled sibling without consulting ``sys.path``."""

    if (
        not isinstance(filename, str)
        or not filename
        or Path(filename).name != filename
        or filename in {".", ".."}
    ):
        raise RuntimeError("required sibling filename is not a safe leaf name")
    source = _require_root_controlled_code_file(
        Path(__file__),
        field="WebApp-FI source receiver source",
    )
    path = _require_root_controlled_code_file(
        source.with_name(filename),
        field=f"required sibling {filename}",
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


transport = _load_exact_sibling("manage_webapp_fi_source_transport.py", "_webapp_fi_source_receiver_transport")
exchange = _load_exact_sibling("manage_webapp_fi_source_exchange.py", "_webapp_fi_source_receiver_exchange")
identity_bootstrap = _load_exact_sibling(
    "manage_controller_source_receive_identity.py",
    "_webapp_fi_source_receiver_identity",
)
binding = transport.campaign_binding
contract = transport.contract


STATIC_READBACK_SCHEMA = "gold-trade-webapp-fi-static-assets-readback-v1"
SOURCE_IMAGE_READBACK_SCHEMA = "gold-trade-webapp-fi-source-image-readback-v1"
CANDIDATE_DIRECTORY_NAME = "received"
CONTROLLER_SOURCE_RECEIVE_ROOT = Path(
    "/srv/trading-bot-three-site-staging-data/controller/webapp-fi-source-receive"
)
READBACK_RECORD_NAME = "readback.json"
STATIC_PAYLOAD_NAME = "static-assets.tar"
SOURCE_IMAGE_PAYLOAD_NAME = "raw-app-image.tar"
CIPHERTEXT_NAME = "payload.age"
MAXIMUM_AGE_IDENTITY_BYTES = 256 * 1024
MAXIMUM_STATIC_ARCHIVE_BYTES = 100 * 1024 * 1024
MAXIMUM_SOURCE_IMAGE_BYTES = 100 * 1024 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
READBACK_RECORD_RESERVE_BYTES = 1024 * 1024
CAPACITY_MARGIN_BYTES = 64 * 1024 * 1024

READBACK_TRANSPORT = {
    "transport": "private_versioned_age_only",
    "create_only": True,
    "read_back_same_version_id": True,
    "provider_side_sse": False,
}
READBACK_AGE_DECRYPTION = {
    "algorithm": "age-v1",
    "controller_identity_scope": "root_only",
    "ciphertext_sha256_verified_before_decrypt": True,
    "plaintext_sha256_verified_after_decrypt": True,
}


class SourceObjectReceiveError(RuntimeError):
    """A FI source object could not be proven safe for controller receipt."""


@dataclasses.dataclass(frozen=True)
class _ReceiveKind:
    object_kind: str
    destination_site: str
    recipient_mode: str
    readback_schema: str
    plaintext_name: str
    maximum_plaintext_bytes: int


@dataclasses.dataclass(frozen=True)
class ReceivePlan:
    """Fully local, validated input for one later exact-S3 receive action."""

    controller_config: Any
    campaign_binding_path: Path
    upload_report_path: Path
    campaign_binding: Any
    policy_sha256: str
    request: Any
    descriptor: Mapping[str, Any]
    kind: _ReceiveKind
    data_root: Path
    candidate_root: Path
    candidate_directory: Path
    age_identity_file: Path
    age_identity_recipient: str
    age_identity_key_id: str
    age_identity_receipt_sha256: str


Decryptor = Callable[[ReceivePlan, Path, Path], None]


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise SourceObjectReceiveError("controller FI source receive operations must run as root")


def _raise_transport_error(action: Callable[[], Any], *, message: str) -> Any:
    try:
        return action()
    except transport.SourceTransportError as exc:
        raise SourceObjectReceiveError(message) from exc


def _raise_exchange_error(action: Callable[[], Any], *, message: str) -> Any:
    try:
        return action()
    except exchange.SourceExchangeError as exc:
        raise SourceObjectReceiveError(message) from exc


def _raise_identity_error(action: Callable[[], Any], *, message: str) -> Any:
    try:
        return action()
    except identity_bootstrap.ControllerSourceReceiveIdentityError as exc:
        raise SourceObjectReceiveError(message) from exc


def _policy_for_exchange(policy: Any) -> Any:
    """Project controller policy into the separately loaded FI exchange type."""

    try:
        return exchange.contract.SourceTransportPolicy(
            endpoint=policy.endpoint,
            region=policy.region,
            bucket=policy.bucket,
            prefix=policy.prefix,
            age_binary=policy.age_binary,
            workspace=policy.workspace,
            controller_age_recipient=policy.controller_age_recipient,
            webapp_fi_age_recipient=policy.webapp_fi_age_recipient,
            webapp_ir_age_recipient=policy.webapp_ir_age_recipient,
            maximum_plaintext_bytes=policy.maximum_plaintext_bytes,
        )
    except (AttributeError, TypeError, ValueError) as exc:  # pragma: no cover - controller config is validated first.
        raise SourceObjectReceiveError("controller source transport policy cannot be projected for FI report verification") from exc


def policy_binding_sha256(policy: Any) -> str:
    """Fingerprint the canonical transport fields that bind an FI report."""

    policy = _raise_transport_error(
        lambda: contract.validate_policy(policy),
        message="controller source transport policy is invalid",
    )
    # These are exactly the policy fields used by report route, recipient,
    # object-key, descriptor-size, and Object Storage validation.  The local
    # age executable and workspace are execution details, not FI report pins.
    value = {
        "schema": contract.CONFIG_SCHEMA,
        "endpoint": policy.endpoint,
        "region": policy.region,
        "bucket": policy.bucket,
        "prefix": policy.prefix,
        "controller_age_recipient": policy.controller_age_recipient,
        "webapp_fi_age_recipient": policy.webapp_fi_age_recipient,
        "webapp_ir_age_recipient": policy.webapp_ir_age_recipient,
        "maximum_plaintext_bytes": policy.maximum_plaintext_bytes,
    }
    return sha256_bytes(canonical_json_bytes(value))


def _request_from_verified_report(value: Mapping[str, Any], *, policy: Any) -> Any:
    request_value = value.get("request")
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
    if not isinstance(request_value, Mapping) or set(request_value) != expected:
        raise SourceObjectReceiveError("verified FI upload report request is unsupported")
    recipients = request_value.get("recipients")
    if not isinstance(recipients, list):
        raise SourceObjectReceiveError("verified FI upload report recipients are unsupported")
    try:
        request = contract.SourceObjectRequest(
            campaign_id=request_value.get("campaign_id"),
            release_sha=request_value.get("release_sha"),
            control_commit=request_value.get("control_commit"),
            control_tree=request_value.get("control_tree"),
            source_site=request_value.get("source_site"),
            destination_site=request_value.get("destination_site"),
            object_kind=request_value.get("object_kind"),
            object_id=request_value.get("object_id"),
            mode=request_value.get("recipient_mode"),
            recipients=tuple(recipients),
        )
        contract.validate_request(policy, request)
    except (TypeError, ValueError, contract.SourceTransportError) as exc:
        raise SourceObjectReceiveError("verified FI upload report request violates the controller transport contract") from exc
    return request


def _kind_for_request(request: Any, *, policy: Any) -> _ReceiveKind:
    if (
        request.source_site == "webapp_fi"
        and request.destination_site == contract.STATIC_DESTINATION_SITE
        and request.object_kind == contract.STATIC_OBJECT_KIND
        and request.mode == contract.STATIC_MODE
        and tuple(request.recipients) == (policy.controller_age_recipient, policy.webapp_ir_age_recipient)
    ):
        return _ReceiveKind(
            object_kind=contract.STATIC_OBJECT_KIND,
            destination_site=contract.STATIC_DESTINATION_SITE,
            recipient_mode=contract.STATIC_MODE,
            readback_schema=STATIC_READBACK_SCHEMA,
            plaintext_name=STATIC_PAYLOAD_NAME,
            maximum_plaintext_bytes=MAXIMUM_STATIC_ARCHIVE_BYTES,
        )
    if (
        request.source_site == "webapp_fi"
        and request.destination_site == "controller"
        and request.object_kind == contract.RAW_APP_IMAGE_OBJECT_KIND
        and request.mode == contract.SINGLE_MODE
        and tuple(request.recipients) == (policy.controller_age_recipient,)
    ):
        return _ReceiveKind(
            object_kind=contract.RAW_APP_IMAGE_OBJECT_KIND,
            destination_site="controller",
            recipient_mode=contract.SINGLE_MODE,
            readback_schema=SOURCE_IMAGE_READBACK_SCHEMA,
            plaintext_name=SOURCE_IMAGE_PAYLOAD_NAME,
            maximum_plaintext_bytes=MAXIMUM_SOURCE_IMAGE_BYTES,
        )
    raise SourceObjectReceiveError("FI source receiver supports only the exact static or raw-app-image route and recipients")


def _candidate_path(
    *,
    data_root: Path,
    campaign_binding_path: Path,
    campaign_binding: Any,
    policy_sha256: str,
    request: Any,
    descriptor: Mapping[str, Any],
) -> tuple[Path, Path]:
    if (
        not isinstance(campaign_binding_path, Path)
        or not campaign_binding_path.is_absolute()
        or ".." in campaign_binding_path.parts
        or campaign_binding_path.name != binding.CAMPAIGN_BINDING_FILENAME
        or campaign_binding_path.parent.name != binding.SOURCE_PHASE_DIRECTORY
    ):
        raise SourceObjectReceiveError("campaign binding path is not the canonical source-phase binding path")
    candidate_root = data_root / campaign_binding.campaign_id / CANDIDATE_DIRECTORY_NAME
    version_tag = sha256_bytes(descriptor["version_id"].encode("ascii"))[:16]
    candidate_name = "-".join(
        (
            request.object_kind,
            request.object_id,
            "b" + campaign_binding.binding_sha256[:16],
            "p" + policy_sha256[:16],
            "v" + version_tag,
        )
    )
    return candidate_root, candidate_root / candidate_name


def _require_controller_receive_data_root() -> Path:
    """Return the one dedicated root-only controller staging-volume root."""

    return _raise_exchange_error(
        lambda: exchange._require_root_private_directory(
            CONTROLLER_SOURCE_RECEIVE_ROOT,
            field="controller FI source receive data root",
        ),
        message="controller FI source receive data root is unsafe",
    )


def _require_writable_controller_receive_staging_volume(
    data_root: Path,
    *,
    statvfs_reader: Callable[[Path], Any] | None = None,
) -> None:
    """Fail closed before a new candidate can write to a read-only mount."""

    fixed_root = _require_controller_receive_data_root()
    if data_root != fixed_root:
        raise SourceObjectReceiveError("FI source receive data root changed before staging-volume admission")
    readonly_flag = getattr(os, "ST_RDONLY", None)
    if isinstance(readonly_flag, bool) or not isinstance(readonly_flag, int) or readonly_flag <= 0:
        raise SourceObjectReceiveError("cannot determine FI source receive staging-volume read-only status")
    reader = os.statvfs if statvfs_reader is None else statvfs_reader
    try:
        mount_flags = reader(fixed_root).f_flag
    except (AttributeError, OSError, OverflowError, TypeError, ValueError) as exc:
        raise SourceObjectReceiveError("cannot inspect FI source receive staging-volume mount flags") from exc
    if isinstance(mount_flags, bool) or not isinstance(mount_flags, int) or mount_flags < 0:
        raise SourceObjectReceiveError("FI source receive staging-volume mount flags are invalid")
    if mount_flags & readonly_flag:
        raise SourceObjectReceiveError("FI source receive fixed staging volume is mounted read-only")


def _require_if_present_root_private_directory(path: Path, *, field: str) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise SourceObjectReceiveError(f"cannot inspect {field}") from exc
    _raise_exchange_error(
        lambda: exchange._require_root_private_directory(path, field=field),
        message=f"{field} is unsafe",
    )


def _capacity_preflight(
    *,
    plan: ReceivePlan,
    candidate: Path,
    disk_usage: Callable[[Path], Any] | None = None,
    stat_path: Callable[[Path], os.stat_result] | None = None,
) -> dict[str, int | bool]:
    """Reserve the retained ciphertext and plaintext before any S3 GET.

    A successful candidate deliberately retains both exact read-back ciphertext
    and decrypted payload.  The fixed data root and candidate must therefore
    remain on one filesystem, and that filesystem must have room for both
    descriptors plus the readback record and a conservative margin.
    """

    if not isinstance(plan, ReceivePlan):  # pragma: no cover - internal caller invariant.
        raise SourceObjectReceiveError("FI source receive capacity plan is unsupported")
    data_root = _require_controller_receive_data_root()
    if data_root != plan.data_root or candidate != plan.candidate_directory:
        raise SourceObjectReceiveError("FI source receive data root changed after preflight")
    _raise_exchange_error(
        lambda: exchange._require_root_private_directory(candidate, field="FI source receive candidate"),
        message="FI source receive candidate is unsafe",
    )
    usage_reader = shutil.disk_usage if disk_usage is None else disk_usage
    device_reader = os.stat if stat_path is None else stat_path
    try:
        data_device = device_reader(data_root).st_dev
        candidate_device = device_reader(candidate).st_dev
        available_bytes = usage_reader(candidate).free
    except (AttributeError, OSError, OverflowError, TypeError, ValueError) as exc:
        raise SourceObjectReceiveError("cannot inspect FI source receive staging capacity") from exc
    if data_device != candidate_device:
        raise SourceObjectReceiveError("FI source receive candidate is not on the fixed staging-volume filesystem")
    if isinstance(available_bytes, bool) or not isinstance(available_bytes, int) or available_bytes < 0:
        raise SourceObjectReceiveError("FI source receive staging capacity is invalid")
    required_new_bytes = (
        plan.descriptor["ciphertext_bytes"]
        + plan.descriptor["plaintext_bytes"]
        + READBACK_RECORD_RESERVE_BYTES
        + CAPACITY_MARGIN_BYTES
    )
    if available_bytes < required_new_bytes:
        raise SourceObjectReceiveError(
            "insufficient free space for FI source receive candidate on the fixed staging volume"
        )
    return {
        "ciphertext_bytes": plan.descriptor["ciphertext_bytes"],
        "plaintext_bytes": plan.descriptor["plaintext_bytes"],
        "readback_record_reserve_bytes": READBACK_RECORD_RESERVE_BYTES,
        "margin_bytes": CAPACITY_MARGIN_BYTES,
        "required_new_bytes": required_new_bytes,
        "available_bytes": available_bytes,
        "same_filesystem": True,
    }


def prepare_receive(
    *,
    controller_config: Any,
    campaign_binding_path: Path,
    upload_report_path: Path,
    candidate_state: str = "new",
) -> ReceivePlan:
    """Validate every local input before a client is created or S3 is read.

    ``candidate_state`` is deliberately limited to the two lifecycle points
    that share the same immutable report-to-candidate derivation.  Normal S3
    receive callers may use only the default ``new`` state.  A later local
    consumer can request ``existing`` solely to rederive and validate the
    fixed candidate after this module has already completed its exact
    read-back; ``execute_receive`` always refreshes with ``new`` and can
    therefore never reuse a candidate.
    """

    _require_root_execution()
    if not isinstance(candidate_state, str) or candidate_state not in {"new", "existing"}:
        raise SourceObjectReceiveError("FI source receive candidate state is unsupported")
    controller_config = _raise_transport_error(
        lambda: transport._validate_controller_config(controller_config),
        message="controller source transport configuration is invalid",
    )
    try:
        campaign = binding.load_campaign_binding(Path(campaign_binding_path))
    except binding.CampaignBindingError as exc:
        raise SourceObjectReceiveError("canonical campaign binding is invalid") from exc
    controller_config = _raise_transport_error(
        lambda: transport.require_controller_config_for_campaign(
            controller_config=controller_config,
            campaign_id=campaign.campaign_id,
        ),
        message="controller source transport config does not bind the canonical campaign",
    )
    data_root = _require_controller_receive_data_root()
    if candidate_state == "new":
        _require_writable_controller_receive_staging_volume(data_root)
    policy = controller_config.policy
    policy_sha256 = policy_binding_sha256(policy)
    verified_identity = _raise_identity_error(
        lambda: identity_bootstrap.load_verified_identity(campaign_binding_path=Path(campaign_binding_path)),
        message="controller source receive identity or receipt is invalid",
    )
    if verified_identity.recipient != policy.controller_age_recipient:
        raise SourceObjectReceiveError("controller source receive identity recipient does not match the pinned controller policy recipient")
    report_payload = _raise_exchange_error(
        lambda: exchange._read_private_file(
            Path(upload_report_path),
            field="FI source upload report",
            maximum_bytes=exchange.MAX_JSON_BYTES,
        ),
        message="FI source upload report is not a root-only canonical input",
    )
    exchange_policy = _policy_for_exchange(policy)
    report = _raise_exchange_error(
        lambda: exchange.verify_upload_report(policy=exchange_policy, payload=report_payload),
        message="FI source upload report is invalid",
    )
    request = _request_from_verified_report(report, policy=policy)
    if (
        request.campaign_id != campaign.campaign_id
        or request.release_sha != campaign.application_release_sha
        or request.control_commit != campaign.control_commit
        or request.control_tree != campaign.control_tree
    ):
        raise SourceObjectReceiveError("FI source upload report is not bound to the canonical campaign release and control pins")
    kind = _kind_for_request(request, policy=policy)
    maximum_plaintext_bytes = min(policy.maximum_plaintext_bytes, kind.maximum_plaintext_bytes)
    try:
        descriptor = contract.validate_object_descriptor(
            report.get("object"),
            maximum_plaintext_bytes=maximum_plaintext_bytes,
        )
    except contract.SourceTransportError as exc:
        raise SourceObjectReceiveError("FI source upload report object exceeds its supported receiver bounds") from exc
    if descriptor["object_key"] != contract.source_object_key(policy, request):
        raise SourceObjectReceiveError("FI source upload report object key is not bound to the canonical request")
    candidate_root, candidate_directory = _candidate_path(
        data_root=data_root,
        campaign_binding_path=Path(campaign_binding_path),
        campaign_binding=campaign,
        policy_sha256=policy_sha256,
        request=request,
        descriptor=descriptor,
    )
    _require_if_present_root_private_directory(
        data_root / campaign.campaign_id,
        field="FI source receive campaign data directory",
    )
    _require_if_present_root_private_directory(
        candidate_root,
        field="FI source receive candidate root",
    )
    if candidate_state == "new":
        if candidate_directory.exists() or candidate_directory.is_symlink():
            raise SourceObjectReceiveError("FI source receive candidate already exists and will not be reused")
    else:
        _raise_exchange_error(
            lambda: exchange._require_root_private_directory(
                candidate_directory,
                field="existing FI source receive candidate",
            ),
            message="existing FI source receive candidate is unsafe",
        )
    return ReceivePlan(
        controller_config=controller_config,
        campaign_binding_path=Path(campaign_binding_path),
        upload_report_path=Path(upload_report_path),
        campaign_binding=campaign,
        policy_sha256=policy_sha256,
        request=request,
        descriptor=descriptor,
        kind=kind,
        data_root=data_root,
        candidate_root=candidate_root,
        candidate_directory=candidate_directory,
        age_identity_file=verified_identity.layout.identity_path,
        age_identity_recipient=verified_identity.recipient,
        age_identity_key_id=verified_identity.key_id,
        age_identity_receipt_sha256=verified_identity.receipt_sha256,
    )


def _fsync_directory(path: Path, *, field: str) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0))
    except OSError as exc:
        raise SourceObjectReceiveError(f"cannot open {field}") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise SourceObjectReceiveError(f"cannot durably sync {field}") from exc
    finally:
        os.close(descriptor)


def _create_or_require_root_private_child(parent: Path, name: str, *, field: str) -> Path:
    if not isinstance(name, str) or not name or Path(name).name != name or name in {".", ".."}:
        raise SourceObjectReceiveError(f"{field} name is invalid")
    _raise_exchange_error(
        lambda: exchange._require_root_private_directory(parent, field=field + " parent"),
        message=f"{field} parent is unsafe",
    )
    child = parent / name
    try:
        os.mkdir(child, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise SourceObjectReceiveError(f"cannot create {field}") from exc
    _raise_exchange_error(
        lambda: exchange._require_root_private_directory(child, field=field),
        message=f"{field} is unsafe",
    )
    _fsync_directory(parent, field=field + " parent")
    return child


def _create_candidate(plan: ReceivePlan) -> Path:
    """Create the deterministic candidate once and retain it on all failures."""

    data_root = _require_controller_receive_data_root()
    if data_root != plan.data_root or plan.candidate_root.parent.parent != data_root:
        raise SourceObjectReceiveError("FI source receive data root changed after preflight")
    # Recheck immediately before the first mkdir to cover a remount after the
    # fully local plan was prepared.  This function is also reused by the
    # source-evidence receiver, which must receive the same admission gate.
    _require_writable_controller_receive_staging_volume(data_root)
    campaign_root = _create_or_require_root_private_child(
        data_root,
        plan.campaign_binding.campaign_id,
        field="FI source receive campaign data directory",
    )
    root = _create_or_require_root_private_child(
        campaign_root,
        CANDIDATE_DIRECTORY_NAME,
        field="FI source receive candidate root",
    )
    if root != plan.candidate_root:
        raise SourceObjectReceiveError("FI source receive candidate root changed after preflight")
    candidate = plan.candidate_directory
    if candidate.parent != root or candidate.exists() or candidate.is_symlink():
        raise SourceObjectReceiveError("FI source receive candidate already exists and will not be reused")
    try:
        os.mkdir(candidate, 0o700)
        os.chmod(candidate, 0o700)
    except FileExistsError as exc:
        raise SourceObjectReceiveError("FI source receive candidate already exists and will not be reused") from exc
    except OSError as exc:
        raise SourceObjectReceiveError("cannot create FI source receive candidate") from exc
    _raise_exchange_error(
        lambda: exchange._require_root_private_directory(candidate, field="FI source receive candidate"),
        message="FI source receive candidate is unsafe",
    )
    _fsync_directory(root, field="FI source receive candidate root")
    return candidate


def _write_stream_to_new_file_preserving_failure(
    stream: Any,
    *,
    output_path: Path,
    maximum_bytes: int,
) -> tuple[str, int]:
    """Write one response body exactly once without deleting failed evidence."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(output_path, flags, 0o600)
    except FileExistsError as exc:
        raise SourceObjectReceiveError("refusing to overwrite FI source receive ciphertext") from exc
    except OSError as exc:
        raise SourceObjectReceiveError("cannot create FI source receive ciphertext") from exc
    digest = hashlib.sha256()
    total = 0
    try:
        os.fchmod(descriptor, 0o600)
        while True:
            chunk = stream.read(CHUNK_BYTES)
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                raise SourceObjectReceiveError("Object Storage read-back returned non-bytes ciphertext")
            if len(chunk) > maximum_bytes - total:
                raise SourceObjectReceiveError("Object Storage read-back ciphertext exceeds its exact declared size")
            view = memoryview(chunk)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:  # pragma: no cover - regular-file writes do not normally return zero.
                    raise OSError("short ciphertext write")
                view = view[written:]
            digest.update(chunk)
            total += len(chunk)
        os.fsync(descriptor)
    except SourceObjectReceiveError:
        raise
    except OSError as exc:
        raise SourceObjectReceiveError("cannot durably write FI source receive ciphertext") from exc
    finally:
        os.close(descriptor)
    return digest.hexdigest(), total


def _download_exact_ciphertext(client: Any, *, plan: ReceivePlan, candidate: Path) -> Path:
    """Verify and retain one exact VersionId ciphertext in the candidate."""

    policy = plan.controller_config.policy
    descriptor = plan.descriptor
    _raise_transport_error(
        lambda: transport._snapshot_error(lambda: transport.snapshot.assert_private_versioned_bucket(client, policy.bucket)),
        message="Object Storage bucket is not private and versioned",
    )
    _raise_transport_error(
        lambda: transport._snapshot_error(
            lambda: transport.snapshot.require_singleton_immutable_object_version(
                client,
                bucket=policy.bucket,
                key=descriptor["object_key"],
                expected_version_id=descriptor["version_id"],
            )
        ),
        message="FI source object is not one exact immutable Object Storage version",
    )
    try:
        response = client.get_object(
            Bucket=policy.bucket,
            Key=descriptor["object_key"],
            VersionId=descriptor["version_id"],
        )
    except Exception as exc:
        raise SourceObjectReceiveError("cannot read back the exact FI source Object Storage version") from exc
    if not isinstance(response, Mapping):
        raise SourceObjectReceiveError("FI source Object Storage read-back response is malformed")
    try:
        response_version = contract._require_version_id(
            response.get("VersionId"),
            field="FI source Object Storage read-back VersionId",
        )
    except contract.SourceTransportError as exc:
        raise SourceObjectReceiveError("FI source Object Storage read-back VersionId is invalid") from exc
    if response_version != descriptor["version_id"]:
        raise SourceObjectReceiveError("FI source Object Storage read-back returned a different VersionId")
    if any(response.get(name) is not None for name in (
        "ServerSideEncryption",
        "SSECustomerAlgorithm",
        "SSECustomerKeyMD5",
        "SSEKMSKeyId",
    )):
        raise SourceObjectReceiveError("FI source Object Storage read-back enabled forbidden provider-side encryption")
    content_length = response.get("ContentLength")
    if isinstance(content_length, bool) or not isinstance(content_length, int) or content_length != descriptor["ciphertext_bytes"]:
        raise SourceObjectReceiveError("FI source Object Storage read-back content length is not exact")
    metadata = response.get("Metadata")
    if not isinstance(metadata, Mapping) or dict(metadata) != transport._ciphertext_metadata(
        descriptor["ciphertext_sha256"], plan.request.mode
    ):
        raise SourceObjectReceiveError("FI source Object Storage read-back metadata is not bound to the report")
    body = response.get("Body")
    if body is None or not hasattr(body, "read"):
        raise SourceObjectReceiveError("FI source Object Storage read-back has no readable body")
    ciphertext = candidate / CIPHERTEXT_NAME
    try:
        observed_sha256, observed_bytes = _write_stream_to_new_file_preserving_failure(
            body,
            output_path=ciphertext,
            maximum_bytes=descriptor["ciphertext_bytes"],
        )
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
    if (
        observed_sha256 != descriptor["ciphertext_sha256"]
        or observed_bytes != descriptor["ciphertext_bytes"]
    ):
        raise SourceObjectReceiveError("FI source Object Storage ciphertext does not match its exact upload report")
    _raise_exchange_error(
        lambda: exchange._require_root_private_file(
            ciphertext,
            field="FI source read-back ciphertext",
            maximum_bytes=descriptor["ciphertext_bytes"],
        ),
        message="FI source read-back ciphertext is unsafe",
    )
    return ciphertext


def _run_age_decrypt(plan: ReceivePlan, ciphertext: Path, plaintext: Path) -> None:
    """Decrypt via the identity already fixed and revalidated in ``plan``."""

    if not isinstance(plan, ReceivePlan):  # pragma: no cover - execute_receive supplies the exact type.
        raise SourceObjectReceiveError("controller FI source decrypt plan is unsupported")
    if plan.age_identity_recipient != plan.controller_config.policy.controller_age_recipient:
        raise SourceObjectReceiveError("controller FI source decrypt identity is not bound to the controller policy")
    age_binary = plan.controller_config.policy.age_binary
    identity_file = plan.age_identity_file

    safe_age = _raise_transport_error(
        lambda: transport._require_root_controlled_regular_file(
            Path(age_binary),
            field="controller FI source age binary",
            private=False,
            executable=True,
        ),
        message="controller FI source age binary is unsafe",
    )
    _raise_exchange_error(
        lambda: exchange._require_root_private_file(
            identity_file,
            field="controller FI source age identity",
            maximum_bytes=MAXIMUM_AGE_IDENTITY_BYTES,
        ),
        message="controller FI source age identity is unsafe",
    )
    _raise_exchange_error(
        lambda: exchange._require_root_private_file(
            ciphertext,
            field="FI source read-back ciphertext",
            maximum_bytes=MAXIMUM_SOURCE_IMAGE_BYTES + contract.MAXIMUM_CIPHERTEXT_OVERHEAD_BYTES,
        ),
        message="FI source read-back ciphertext is unsafe",
    )
    if plaintext.exists() or plaintext.is_symlink():
        raise SourceObjectReceiveError("refusing to overwrite FI source decrypted candidate payload")
    try:
        result = subprocess.run(
            [str(safe_age), "--decrypt", "-i", str(identity_file), "-o", str(plaintext), str(ciphertext)],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=600,
            preexec_fn=lambda: os.umask(0o077),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceObjectReceiveError("age decryption of FI source ciphertext could not start") from exc
    if result.returncode != 0:
        raise SourceObjectReceiveError("age decryption of FI source ciphertext failed")


def _build_readback_record(plan: ReceivePlan) -> dict[str, Any]:
    return {
        "schema": plan.kind.readback_schema,
        "status": "read_back",
        "campaign_id": plan.campaign_binding.campaign_id,
        "source_site": "webapp_fi",
        "consumer_site": "controller",
        "object": dict(plan.descriptor),
        "transport": dict(READBACK_TRANSPORT),
        "age_decryption": dict(READBACK_AGE_DECRYPTION),
    }


def _write_new_readback_record(path: Path, value: Mapping[str, Any]) -> None:
    """Durably create the only JSON receipt in a successful candidate."""

    payload = canonical_json_bytes(value) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise SourceObjectReceiveError("refusing to overwrite FI source readback record") from exc
    except OSError as exc:
        raise SourceObjectReceiveError("cannot create FI source readback record") from exc
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:  # pragma: no cover - regular-file writes do not normally return zero.
                raise OSError("short readback record write")
            view = view[written:]
        os.fsync(descriptor)
    except OSError as exc:
        raise SourceObjectReceiveError("cannot durably write FI source readback record") from exc
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent, field="FI source receive candidate")


def execute_receive(client: Any, *, plan: ReceivePlan, decryptor: Decryptor = _run_age_decrypt) -> dict[str, Any]:
    """Read back, decrypt, and record one locally prevalidated FI object."""

    _require_root_execution()
    if not isinstance(plan, ReceivePlan):
        raise SourceObjectReceiveError("FI source receive plan is unsupported")
    if not callable(decryptor):
        raise SourceObjectReceiveError("FI source decryptor is unsupported")
    refreshed = prepare_receive(
        controller_config=plan.controller_config,
        campaign_binding_path=plan.campaign_binding_path,
        upload_report_path=plan.upload_report_path,
    )
    if refreshed != plan:
        raise SourceObjectReceiveError("FI source receive local inputs changed after preflight")
    plan = refreshed
    candidate = _create_candidate(plan)
    capacity = _capacity_preflight(plan=plan, candidate=candidate)
    ciphertext = _download_exact_ciphertext(client, plan=plan, candidate=candidate)
    plaintext = candidate / plan.kind.plaintext_name
    try:
        decryptor(plan, ciphertext, plaintext)
    except SourceObjectReceiveError:
        raise
    except Exception as exc:
        raise SourceObjectReceiveError("age decryption of FI source ciphertext failed") from exc
    _raise_exchange_error(
        lambda: exchange._require_root_private_file(
            plaintext,
            field="age-decrypted FI source payload",
            maximum_bytes=min(plan.controller_config.policy.maximum_plaintext_bytes, plan.kind.maximum_plaintext_bytes),
        ),
        message="age-decrypted FI source payload is unsafe",
    )
    observed_sha256, observed_bytes = _raise_exchange_error(
        lambda: exchange._secure_hash_file(
            plaintext,
            field="age-decrypted FI source payload",
            maximum_bytes=min(plan.controller_config.policy.maximum_plaintext_bytes, plan.kind.maximum_plaintext_bytes),
        ),
        message="age-decrypted FI source payload cannot be verified",
    )
    if (
        observed_sha256 != plan.descriptor["plaintext_sha256"]
        or observed_bytes != plan.descriptor["plaintext_bytes"]
    ):
        raise SourceObjectReceiveError("age-decrypted FI source payload does not match its exact upload report")
    record_path = candidate / READBACK_RECORD_NAME
    record = _build_readback_record(plan)
    _write_new_readback_record(record_path, record)
    return {
        "status": "received",
        "campaign_id": plan.campaign_binding.campaign_id,
        "object_kind": plan.request.object_kind,
        "object_id": plan.request.object_id,
        "policy_sha256": plan.policy_sha256,
        "candidate_directory": str(candidate),
        "ciphertext": str(ciphertext),
        "plaintext": str(plaintext),
        "readback_record": str(record_path),
        "object": dict(plan.descriptor),
        "capacity_preflight": capacity,
    }


def receive_fi_source_object(
    client: Any,
    *,
    controller_config: Any,
    campaign_binding_path: Path,
    upload_report_path: Path,
    decryptor: Decryptor = _run_age_decrypt,
) -> dict[str, Any]:
    """Convenience composition for callers that already own an S3 client."""

    plan = prepare_receive(
        controller_config=controller_config,
        campaign_binding_path=campaign_binding_path,
        upload_report_path=upload_report_path,
    )
    return execute_receive(client, plan=plan, decryptor=decryptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    receive = subparsers.add_parser("receive", help="receive one exact FI upload-report object")
    receive.add_argument("--config", required=True, type=Path)
    receive.add_argument("--campaign-binding", required=True, type=Path)
    receive.add_argument("--upload-report", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command != "receive":  # pragma: no cover - argparse dispatch invariant.
            raise SourceObjectReceiveError("unsupported command")
        controller_config = _raise_transport_error(
            lambda: transport.load_controller_config(args.config),
            message="controller source transport configuration is invalid",
        )
        # Complete every local report/binding/identity check before loading a
        # credential or constructing an authenticated Object Storage client.
        plan = prepare_receive(
            controller_config=controller_config,
            campaign_binding_path=args.campaign_binding,
            upload_report_path=args.upload_report,
        )
        client = _raise_transport_error(
            lambda: transport.create_s3_client(controller_config),
            message="cannot create the controller Object Storage client",
        )
        result = execute_receive(client, plan=plan)
    except SourceObjectReceiveError as exc:
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
