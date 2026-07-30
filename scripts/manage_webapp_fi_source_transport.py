#!/usr/bin/env python3
"""Publish one immutable, encrypted WebApp-FI source-phase object.

This is deliberately a narrow Object Storage primitive.  It creates exactly
one private, versioned, create-only object from one root-only plaintext file,
immediately reads back that exact VersionId, and records a canonical receipt
without a URL.  It does not download a remote object, run Docker, use SSH,
stage a release, or change a service.

There are only two delivery shapes:

``static``
    One ciphertext is encrypted for exactly the configured controller and
    WebApp-IR age recipients.  This is the only allowed dual-recipient
    source-phase object.

``single``
    One ciphertext is encrypted for exactly the configured recipient of its
    destination site.  The destination must be one of controller, WebApp-FI,
    or WebApp-IR.

The public recipients are configuration pins, not caller-selected routing.
That makes an accidental third recipient, duplicate recipient, or omitted
recipient fail before ``age`` or S3 is invoked.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import importlib.util
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence


def _require_root_controlled_directory_chain(
    path: Path,
    *,
    field: str,
    error_type: Callable[[str], Exception],
) -> None:
    """Require a stable root-owned lookup chain before opening trusted code.

    A root-owned sticky directory is allowed even if it is group/world
    writable.  Sticky semantics prevent an unprivileged account from
    replacing an existing root-owned child, while each subsequent component
    still has to be root-owned and non-writable.
    """

    if not path.is_absolute():
        raise error_type(f"{field} parent must be absolute")
    current = Path(path.anchor)
    components = (current,)
    for component in path.parts[1:]:
        current = current / component
        components += (current,)
    for current in components:
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise error_type(f"cannot inspect {field} parent") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        writable_by_group_or_other = bool(mode & 0o022)
        root_owned_sticky_directory = bool(metadata.st_mode & stat.S_ISVTX)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or (writable_by_group_or_other and not root_owned_sticky_directory)
        ):
            raise error_type(f"{field} parent is not root-controlled")


def _load_exact_sibling(filename: str, module_name: str) -> Any:
    """Load one named, root-controlled sibling without consulting ``sys.path``.

    The transport is invoked as root, so executing a sibling is a trust
    boundary of its own.  Validate the complete lookup path before asking
    importlib to open it.  A root-owned sticky ancestor such as ``/tmp`` is
    safe for an already-root-owned path component: an unprivileged account
    cannot replace that component there.
    """

    if (
        not isinstance(filename, str)
        or not filename
        or Path(filename).name != filename
        or filename in {".", ".."}
    ):
        raise RuntimeError("required sibling filename is not a safe leaf name")
    path = Path(__file__).resolve(strict=True).with_name(filename)
    _require_root_controlled_directory_chain(
        path.parent,
        field=f"required sibling {filename}",
        error_type=RuntimeError,
    )
    try:
        state = path.lstat()
    except OSError as exc:  # pragma: no cover - repository layout invariant.
        raise RuntimeError(f"cannot inspect required sibling {filename}") from exc
    unsafe_bits = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX
    if (
        stat.S_ISLNK(state.st_mode)
        or not stat.S_ISREG(state.st_mode)
        or state.st_uid != 0
        or stat.S_IMODE(state.st_mode) & 0o022
        or state.st_mode & unsafe_bits
    ):  # pragma: no cover - deployment invariant.
        raise RuntimeError(f"required sibling {filename} is not a root-owned non-writable regular non-symlink file")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError(f"cannot load required sibling {filename}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
        raise
    loaded_path = getattr(module, "__file__", None)
    if not isinstance(loaded_path, str) or Path(loaded_path).resolve(strict=True) != path:
        raise RuntimeError(f"required sibling {filename} did not load from its exact path")
    return module


snapshot = _load_exact_sibling("manage_webapp_ir_snapshot.py", "_webapp_fi_source_transport_snapshot")
contract = _load_exact_sibling("webapp_fi_source_transport_contract.py", "_webapp_fi_source_transport_contract")
campaign_binding = _load_exact_sibling(
    "webapp_fi_source_campaign_binding.py",
    "_webapp_fi_source_campaign_binding",
)


# The controller implementation re-exports the pure contract names for
# existing callers, but it deliberately owns no second copy of routing,
# recipient, receipt, descriptor, or presigned-URL validation semantics.
TRANSPORT_SCHEMA = contract.TRANSPORT_SCHEMA
CONFIG_SCHEMA = contract.CONFIG_SCHEMA
OBJECT_ENCRYPTION = contract.OBJECT_ENCRYPTION
OBJECT_LAYOUT_VERSION = contract.OBJECT_LAYOUT_VERSION
STATIC_MODE = contract.STATIC_MODE
SINGLE_MODE = contract.SINGLE_MODE
STATIC_DESTINATION_SITE = contract.STATIC_DESTINATION_SITE
BOOTSTRAP_OBJECT_KIND = contract.BOOTSTRAP_OBJECT_KIND
STATIC_OBJECT_KIND = contract.STATIC_OBJECT_KIND
STATIC_PROVENANCE_OBJECT_KIND = contract.STATIC_PROVENANCE_OBJECT_KIND
RAW_APP_IMAGE_OBJECT_KIND = contract.RAW_APP_IMAGE_OBJECT_KIND
SOURCE_EVIDENCE_OBJECT_KIND = contract.SOURCE_EVIDENCE_OBJECT_KIND
MAXIMUM_PLAINTEXT_BYTES = contract.MAXIMUM_PLAINTEXT_BYTES
MAXIMUM_CIPHERTEXT_OVERHEAD_BYTES = contract.MAXIMUM_CIPHERTEXT_OVERHEAD_BYTES
OBJECT_ID_RE = contract.OBJECT_ID_RE


SourceTransportError = contract.SourceTransportError
SourceTransportPolicy = contract.SourceTransportPolicy
SourceTransportConfig = contract.SourceTransportConfig
SourceObjectRequest = contract.SourceObjectRequest
SourceObjectExpectation = contract.SourceObjectExpectation
CampaignBinding = campaign_binding.CampaignBinding
CampaignBindingError = campaign_binding.CampaignBindingError


@dataclasses.dataclass(frozen=True)
class ControllerS3Config:
    """Controller-only credential reference and short-lived presign policy.

    This object must never be copied to WebApp-FI.  FI needs only
    :class:`SourceTransportPolicy`, one transient presigned PUT URL, and the
    public recipient pins already validated by the controller.
    """

    policy: SourceTransportPolicy
    credentials_file: Path
    presign_expires_seconds: int = 300


@dataclasses.dataclass(frozen=True)
class ImmutablePlaintextSnapshot:
    """A root-only FD-backed snapshot passed to a single age invocation."""

    path: Path
    sha256: str
    bytes: int


@dataclasses.dataclass(frozen=True)
class PresignedUploadPlan:
    """Transient controller-to-FI control message, never a persistent receipt."""

    object_key: str
    upload_url: str
    required_headers: Mapping[str, str]
    expectation: SourceObjectExpectation
    recipient_mode: str
    recipients: tuple[str, ...]


canonical_json_bytes = contract.canonical_json_bytes
sha256_bytes = contract.sha256_bytes


def sha256_file(path: Path) -> tuple[str, int]:
    return snapshot.sha256_file(path)


def _require_sha256(value: object, *, field: str) -> str:
    return contract._require_sha256(value, field=field)


def _validate_expectation(
    expectation: SourceObjectExpectation,
    *,
    maximum_plaintext_bytes: int,
) -> SourceObjectExpectation:
    return contract.validate_expectation(
        expectation,
        maximum_plaintext_bytes=maximum_plaintext_bytes,
    )


def _snapshot_error(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except snapshot.SnapshotTransportError as exc:
        raise SourceTransportError(str(exc)) from exc


def _require_root_controlled_ancestors(path: Path, *, field: str) -> None:
    """Reject unsafe lookup paths before consuming local transport input."""

    _require_root_controlled_directory_chain(path, field=field, error_type=SourceTransportError)


def _require_root_controlled_regular_file(
    path: Path,
    *,
    field: str,
    private: bool,
    executable: bool = False,
) -> Path:
    """Validate a configuration, credential, or executable immediately before use."""

    path = _require_absolute_path(path, field=field)
    _require_root_controlled_ancestors(path.parent, field=field)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SourceTransportError(f"cannot inspect {field}") from exc
    unsafe_bits = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX
    forbidden_mode = 0o077 if private else 0o022
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & forbidden_mode
        or metadata.st_mode & unsafe_bits
        or (executable and not stat.S_IMODE(metadata.st_mode) & 0o100)
    ):
        qualifier = "root-only" if private else "root-owned and not group/world writable"
        raise SourceTransportError(f"{field} must be a {qualifier} regular non-symlink file")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SourceTransportError(f"cannot resolve {field}") from exc
    if resolved != path:
        raise SourceTransportError(f"{field} must not resolve through a symlink")
    return path


def _require_string(value: object, *, field: str) -> str:
    return contract._require_string(value, field=field)


def _require_id(value: object, *, field: str, pattern: re.Pattern[str]) -> str:
    return contract._require_id(value, field=field, pattern=pattern)


def _require_positive_int(value: object, *, field: str, maximum: int | None = None) -> int:
    return contract._require_positive_int(value, field=field, maximum=maximum)


def _require_absolute_path(value: object, *, field: str) -> Path:
    return contract._require_absolute_path(value, field=field)


def _validate_prefix(value: object) -> str:
    return contract._validate_prefix(value)


def _require_age_recipient(value: object, *, field: str) -> str:
    return contract._require_age_recipient(value, field=field)


def _validate_policy(config: SourceTransportPolicy) -> SourceTransportPolicy:
    return contract.validate_policy(config)


def _validate_controller_config(config: ControllerS3Config) -> ControllerS3Config:
    if not isinstance(config, ControllerS3Config):
        raise SourceTransportError("controller S3 config has an unsupported type")
    policy = _validate_policy(config.policy)
    credentials_file = _require_absolute_path(config.credentials_file, field="credentials_file")
    expires = _require_positive_int(config.presign_expires_seconds, field="presign_expires_seconds", maximum=900)
    if expires < 60:
        raise SourceTransportError("presign_expires_seconds must be at least 60")
    return ControllerS3Config(policy=policy, credentials_file=credentials_file, presign_expires_seconds=expires)


def load_controller_config(path: Path) -> ControllerS3Config:
    """Load controller-only S3 configuration; do not distribute it to FI."""

    config_path = _require_root_controlled_regular_file(
        Path(path),
        field="source transport publisher config",
        private=True,
    )
    raw = _snapshot_error(lambda: snapshot.load_root_only_json(config_path, field="source transport publisher config"))
    allowed = {
        "schema",
        "endpoint",
        "region",
        "bucket",
        "prefix",
        "credentials_file",
        "age_binary",
        "workspace",
        "controller_age_recipient",
        "webapp_fi_age_recipient",
        "webapp_ir_age_recipient",
        "maximum_plaintext_bytes",
        "presign_expires_seconds",
    }
    if set(raw) - allowed:
        raise SourceTransportError("source transport publisher config has unsupported fields")
    if raw.get("schema") != CONFIG_SCHEMA:
        raise SourceTransportError("source transport publisher config schema is unsupported")
    return _validate_controller_config(
        ControllerS3Config(
            policy=SourceTransportPolicy(
            endpoint=raw.get("endpoint"),
            region=raw.get("region"),
            bucket=raw.get("bucket"),
            prefix=raw.get("prefix"),
            age_binary=raw.get("age_binary", "/usr/bin/age"),
            workspace=Path(_require_string(raw.get("workspace"), field="workspace")),
            controller_age_recipient=raw.get("controller_age_recipient"),
            webapp_fi_age_recipient=raw.get("webapp_fi_age_recipient"),
            webapp_ir_age_recipient=raw.get("webapp_ir_age_recipient"),
            maximum_plaintext_bytes=raw.get("maximum_plaintext_bytes", MAXIMUM_PLAINTEXT_BYTES),
            ),
            credentials_file=Path(_require_string(raw.get("credentials_file"), field="credentials_file")),
            presign_expires_seconds=raw.get("presign_expires_seconds", 300),
        )
    )


def create_s3_client(config: ControllerS3Config) -> Any:
    """Create a path-style client only after a caller elects to publish."""

    if snapshot.boto3 is None:  # pragma: no cover - deployment image invariant.
        raise SourceTransportError("boto3 is unavailable")
    config = _validate_controller_config(config)
    credentials_file = _require_root_controlled_regular_file(
        config.credentials_file,
        field="controller source transport credentials",
        private=True,
    )
    credentials = _snapshot_error(lambda: snapshot.load_credentials(credentials_file))
    try:
        from botocore.config import Config as BotocoreConfig

        session = snapshot.boto3.session.Session(
            aws_access_key_id=credentials["access_key"],
            aws_secret_access_key=credentials["secret_key"],
            aws_session_token=credentials.get("session_token"),
            region_name=config.policy.region,
        )
        return session.client(
            "s3",
            endpoint_url=config.policy.endpoint,
            config=BotocoreConfig(s3={"addressing_style": "path"}),
        )
    except Exception as exc:  # pragma: no cover - executed only on a real publisher host.
        raise SourceTransportError("cannot create the Object Storage client") from exc


def resolve_recipients(config: SourceTransportPolicy, request: SourceObjectRequest) -> tuple[str, ...]:
    """Resolve strict recipient pins before touching age or Object Storage."""

    return contract.resolve_recipients(config, request)


def validate_request(config: SourceTransportPolicy, request: SourceObjectRequest) -> tuple[str, ...]:
    """Validate a typed direction and return its canonical, pinned recipients."""

    return contract.validate_request(config, request)


def request_from_campaign_binding(
    *,
    config: SourceTransportPolicy,
    campaign_binding_path: Path,
    source_site: str,
    destination_site: str,
    object_kind: str,
    object_id: str,
) -> SourceObjectRequest:
    """Derive one CLI-publishable request from the immutable campaign binding.

    The generic Python transport primitive intentionally remains typed and
    composable for local tests and controller-internal orchestration.  The
    real command-line publisher must use this helper instead of accepting
    mutable campaign/release/control values directly from an operator.
    """

    policy = _validate_policy(config)
    try:
        binding = campaign_binding.load_campaign_binding(Path(campaign_binding_path))
    except CampaignBindingError as exc:
        raise SourceTransportError("campaign binding is invalid") from exc
    if not all(isinstance(item, str) for item in (source_site, destination_site, object_kind, object_id)):
        raise SourceTransportError("source transport route identifiers must be strings")
    if source_site not in {"controller", "bot_fi"}:
        raise SourceTransportError(
            "authenticated controller publication is allowed only for controller-local source objects"
        )
    mode = contract.ALLOWED_DIRECTIONS.get((source_site, destination_site, object_kind))
    if mode is None:
        raise SourceTransportError("source transport direction, object kind, or recipient mode is unsupported")
    if mode == STATIC_MODE:
        recipients = (policy.controller_age_recipient, policy.webapp_ir_age_recipient)
    else:
        recipient_by_destination = {
            "controller": policy.controller_age_recipient,
            "webapp_fi": policy.webapp_fi_age_recipient,
            "webapp_ir": policy.webapp_ir_age_recipient,
        }
        try:
            recipients = (recipient_by_destination[destination_site],)
        except KeyError as exc:  # pragma: no cover - the allowlist is the authority.
            raise SourceTransportError("single-recipient destination_site is unsupported") from exc
    request = SourceObjectRequest(
        campaign_id=binding.campaign_id,
        release_sha=binding.application_release_sha,
        control_commit=binding.control_commit,
        control_tree=binding.control_tree,
        source_site=source_site,
        destination_site=destination_site,
        object_kind=object_kind,
        object_id=object_id,
        mode=mode,
        recipients=recipients,
    )
    validate_request(policy, request)
    return request


def source_object_key(config: SourceTransportPolicy, request: SourceObjectRequest) -> str:
    """Return a deterministic, safe, unique immutable object key namespace."""

    return contract.source_object_key(config, request)


def _required_upload_headers(*, expectation: SourceObjectExpectation, mode: str) -> dict[str, str]:
    """Return the exact no-SSE headers that an FI sender must attach to PUT."""

    return contract.required_upload_headers(expectation=expectation, mode=mode)


def require_create_only_presigned_put_url(
    value: object,
    *,
    policy: SourceTransportPolicy,
    object_key: str,
) -> str:
    """Validate a transient PUT URL without allowing it into persistent state."""

    return contract.require_create_only_presigned_put_url(value, policy=policy, object_key=object_key)


def require_version_bound_presigned_get_url(
    value: object,
    *,
    policy: SourceTransportPolicy,
    object_key: str,
    version_id: str,
) -> str:
    """Validate a transient GET URL bound to one exact immutable VersionId."""

    return contract.require_version_bound_presigned_get_url(
        value,
        policy=policy,
        object_key=object_key,
        version_id=version_id,
    )


def create_version_bound_presigned_get(
    client: Any,
    *,
    controller_config: ControllerS3Config,
    request: SourceObjectRequest,
    version_id: str,
    receipt_payload: bytes,
) -> str:
    """Controller-only transient GET URL for a verified immutable receipt.

    The caller may pass this value as a one-shot SSH control argument.  It is
    intentionally returned as a string rather than placed in a receipt or
    manifest; callers must not persist it.  The receipt is mandatory so a
    later GET cannot be minted merely from a caller-supplied key and VersionId.
    """

    _require_root_execution()
    controller_config = _validate_controller_config(controller_config)
    policy = controller_config.policy
    recipients = validate_request(policy, request)
    key = source_object_key(policy, request)
    exact_version = _require_version_id(version_id, field="VersionId")
    verified_receipt = contract.verify_publish_receipt(config=policy, payload=receipt_payload)
    expected_binding = {
        "campaign_id": request.campaign_id,
        "release_sha": request.release_sha,
        "control_commit": request.control_commit,
        "control_tree": request.control_tree,
        "source_site": request.source_site,
        "destination_site": request.destination_site,
        "object_kind": request.object_kind,
        "object_id": request.object_id,
        "recipient_mode": request.mode,
    }
    if any(verified_receipt.get(name) != value for name, value in expected_binding.items()):
        raise SourceTransportError("presigned download receipt is not bound to the requested source object")
    if tuple(verified_receipt.get("recipients", ())) != recipients:
        raise SourceTransportError("presigned download receipt recipients are not bound to the requested source object")
    descriptor = verified_receipt["object"]
    if descriptor["object_key"] != key or descriptor["version_id"] != exact_version:
        raise SourceTransportError("presigned download receipt is not bound to the exact immutable VersionId")
    _snapshot_error(lambda: snapshot.assert_private_versioned_bucket(client, policy.bucket))
    _snapshot_error(
        lambda: snapshot.require_singleton_immutable_object_version(
            client,
            bucket=policy.bucket,
            key=key,
            expected_version_id=exact_version,
        )
    )
    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": policy.bucket, "Key": key, "VersionId": exact_version},
            ExpiresIn=controller_config.presign_expires_seconds,
            HttpMethod="GET",
        )
    except Exception as exc:
        raise SourceTransportError("cannot create a version-bound presigned Object Storage GET URL") from exc
    return require_version_bound_presigned_get_url(
        url,
        policy=policy,
        object_key=key,
        version_id=exact_version,
    )


def prepare_presigned_upload(
    client: Any,
    *,
    controller_config: ControllerS3Config,
    request: SourceObjectRequest,
    expectation: SourceObjectExpectation,
) -> PresignedUploadPlan:
    """Controller-only prepare step for an FI direct PUT without S3 credentials.

    The returned URL is an in-memory/transient control value.  The controller
    must transmit it only to the intended FI sender and must later call
    :func:`finalize_presigned_upload` with the VersionId returned by that PUT.
    Neither this plan nor its URL belongs in a receipt, evidence, or Object
    Storage payload.
    """

    _require_root_execution()
    controller_config = _validate_controller_config(controller_config)
    policy = controller_config.policy
    recipients = validate_request(policy, request)
    expected = _validate_expectation(expectation, maximum_plaintext_bytes=policy.maximum_plaintext_bytes)
    key = source_object_key(policy, request)
    _snapshot_error(lambda: snapshot.assert_private_versioned_bucket(client, policy.bucket))
    _snapshot_error(lambda: snapshot.assert_object_absent(client, bucket=policy.bucket, key=key))
    metadata = _ciphertext_metadata(expected.ciphertext_sha256, request.mode)
    try:
        url = client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": policy.bucket,
                "Key": key,
                "ContentType": "application/octet-stream",
                "Metadata": metadata,
                "IfNoneMatch": "*",
            },
            ExpiresIn=controller_config.presign_expires_seconds,
            HttpMethod="PUT",
        )
    except Exception as exc:
        raise SourceTransportError("cannot create a create-only presigned Object Storage PUT URL") from exc
    return PresignedUploadPlan(
        object_key=key,
        upload_url=require_create_only_presigned_put_url(url, policy=policy, object_key=key),
        required_headers=_required_upload_headers(expectation=expected, mode=request.mode),
        expectation=expected,
        recipient_mode=request.mode,
        recipients=recipients,
    )


def finalize_presigned_upload(
    client: Any,
    *,
    policy: SourceTransportPolicy,
    request: SourceObjectRequest,
    expectation: SourceObjectExpectation,
    version_id: str,
    receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Controller-only exact read-back and URL-free receipt after FI direct PUT."""

    _require_root_execution()
    policy = _validate_policy(policy)
    validate_request(policy, request)
    expected = _validate_expectation(expectation, maximum_plaintext_bytes=policy.maximum_plaintext_bytes)
    exact_version = _require_version_id(version_id, field="presigned upload VersionId")
    key = source_object_key(policy, request)
    if receipt_path is not None:
        if not receipt_path.is_absolute():
            raise SourceTransportError("receipt_path must be absolute")
        if receipt_path.exists() or receipt_path.is_symlink():
            raise SourceTransportError("refusing to overwrite a source transport receipt")
    _snapshot_error(lambda: snapshot.assert_private_versioned_bucket(client, policy.bucket))
    _snapshot_error(
        lambda: snapshot.require_singleton_immutable_object_version(
            client,
            bucket=policy.bucket,
            key=key,
            expected_version_id=exact_version,
        )
    )
    with locked_workspace(policy.workspace, lock_name=request.campaign_id + "-" + request.object_id) as workspace:
        _verify_exact_object_readback(
            client,
            bucket=policy.bucket,
            key=key,
            version_id=exact_version,
            expected_sha256=expected.ciphertext_sha256,
            expected_bytes=expected.ciphertext_bytes,
            mode=request.mode,
            workspace=workspace,
        )
    descriptor = _object_descriptor(
        {
            "object_key": key,
            "version_id": exact_version,
            "ciphertext_sha256": expected.ciphertext_sha256,
            "ciphertext_bytes": expected.ciphertext_bytes,
            "plaintext_sha256": expected.plaintext_sha256,
            "plaintext_bytes": expected.plaintext_bytes,
        },
        maximum_plaintext_bytes=policy.maximum_plaintext_bytes,
    )
    receipt = build_publish_receipt(config=policy, request=request, descriptor=descriptor)
    if receipt_path is not None:
        write_create_only_receipt(receipt_path, receipt, config=policy)
    return receipt


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise SourceTransportError("source transport publication must run as root")


def _require_root_only_input(path: Path, *, field: str, maximum_bytes: int) -> Path:
    return _snapshot_error(
        lambda: snapshot.require_secure_input_file(path, field=field, maximum_bytes=maximum_bytes)
    )


def _require_private_directory(path: Path, *, field: str) -> Path:
    return _snapshot_error(lambda: snapshot.ensure_root_only_directory(path, field=field))


def _require_private_file(path: Path, *, field: str, maximum_bytes: int | None = None) -> Path:
    result = _snapshot_error(lambda: snapshot.require_root_only_file(path, field=field))
    if maximum_bytes is not None:
        state = result.lstat()
        if not 1 <= state.st_size <= maximum_bytes:
            raise SourceTransportError(f"{field} exceeds its configured size bound")
    return result


def create_immutable_plaintext_snapshot(
    *,
    source_path: Path,
    workspace: Path,
    snapshot_name: str,
    maximum_bytes: int,
) -> ImmutablePlaintextSnapshot:
    """Copy one checked source FD to a new root-only file before passing it to age.

    ``age`` reopens an input pathname.  The caller therefore never hands age
    the original source path: this function verifies the opened descriptor,
    copies it to a create-only private workspace file, and hashes that exact
    stable copy instead.
    """

    source_path = _require_root_only_input(source_path, field="source plaintext", maximum_bytes=maximum_bytes)
    workspace = _require_private_directory(workspace, field="source transport workspace")
    safe_name = _require_id(snapshot_name, field="plaintext snapshot name", pattern=OBJECT_ID_RE)
    destination = workspace / ("plaintext-snapshot-" + safe_name)
    if destination.exists() or destination.is_symlink():
        raise SourceTransportError("refusing to overwrite an immutable plaintext workspace snapshot")
    try:
        before = source_path.lstat()
    except OSError as exc:
        raise SourceTransportError("cannot inspect source plaintext before snapshot") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or stat.S_IMODE(before.st_mode) & 0o077
        or before.st_nlink != 1
        or not 1 <= before.st_size <= maximum_bytes
    ):
        raise SourceTransportError("source plaintext is not a private immutable snapshot input")

    source_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    destination_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    source_descriptor: int | None = None
    destination_descriptor: int | None = None
    digest = hashlib.sha256()
    total = 0
    try:
        source_descriptor = os.open(source_path, source_flags)
        opened = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) & 0o077
            or opened.st_nlink != 1
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
        ):
            raise SourceTransportError("source plaintext changed while being opened for snapshot")
        destination_descriptor = os.open(destination, destination_flags, 0o600)
        os.fchmod(destination_descriptor, 0o600)
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise SourceTransportError("source plaintext exceeds its configured size bound while being snapshotted")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                if written <= 0:  # pragma: no cover - os.write does not normally return zero.
                    raise OSError("short plaintext snapshot write")
                view = view[written:]
        if total != opened.st_size:
            raise SourceTransportError("source plaintext changed while being copied into an immutable snapshot")
        os.fsync(destination_descriptor)
        after = os.fstat(source_descriptor)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
            or after.st_nlink != opened.st_nlink
        ):
            raise SourceTransportError("source plaintext changed while being copied into an immutable snapshot")
    except SourceTransportError:
        raise
    except OSError as exc:
        raise SourceTransportError("cannot create immutable plaintext workspace snapshot") from exc
    finally:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
        if source_descriptor is not None:
            os.close(source_descriptor)

    _require_private_file(destination, field="immutable plaintext workspace snapshot", maximum_bytes=maximum_bytes)
    observed_sha256, observed_bytes = sha256_file(destination)
    if (observed_sha256, observed_bytes) != (digest.hexdigest(), total):
        raise SourceTransportError("immutable plaintext workspace snapshot changed while being verified")
    return ImmutablePlaintextSnapshot(path=destination, sha256=observed_sha256, bytes=observed_bytes)


@contextlib.contextmanager
def locked_workspace(workspace: Path, *, lock_name: str) -> Iterator[Path]:
    """Create one root-only temporary workspace under an exclusive local lock."""

    _require_private_directory(workspace, field="source transport workspace")
    safe_lock_name = _require_id(lock_name, field="source transport workspace lock name", pattern=OBJECT_ID_RE)
    try:
        with snapshot.exclusive_workspace_lock(workspace, name="webapp-fi-source-transport-" + safe_lock_name):
            with tempfile.TemporaryDirectory(prefix="webapp-fi-source-transport-", dir=str(workspace)) as temporary:
                temporary_path = Path(temporary)
                temporary_path.chmod(0o700)
                _require_private_directory(temporary_path, field="source transport temporary workspace")
                yield temporary_path
    except snapshot.SnapshotTransportError as exc:
        raise SourceTransportError(str(exc)) from exc


def run_age_encrypt_many(age_binary: str, recipients: Sequence[str], input_path: Path, output_path: Path) -> None:
    """Encrypt one input once for the exact ordered public-recipient set."""

    if output_path.exists() or output_path.is_symlink():
        raise SourceTransportError("refusing to overwrite an encrypted workspace artifact")
    safe_age_binary = _require_root_controlled_regular_file(
        Path(age_binary),
        field="source transport age binary",
        private=False,
        executable=True,
    )
    normalized = tuple(_require_age_recipient(value, field="age recipient") for value in recipients)
    if not normalized or len(set(normalized)) != len(normalized):
        raise SourceTransportError("age recipient set is invalid")
    command = [str(safe_age_binary)]
    for recipient in normalized:
        command.extend(("-r", recipient))
    command.extend(("-o", str(output_path), str(input_path)))
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
            preexec_fn=lambda: os.umask(0o077),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceTransportError("age encryption command failed to start") from exc
    if completed.returncode != 0:
        raise SourceTransportError("age encryption failed")
    _require_private_file(output_path, field="encrypted source transport artifact")


def _ciphertext_metadata(ciphertext_sha256: str, mode: str) -> dict[str, str]:
    return {
        "transport-schema": TRANSPORT_SCHEMA,
        "encryption": OBJECT_ENCRYPTION,
        "ciphertext-sha256": ciphertext_sha256,
        "recipient-mode": mode,
    }


def _write_stream_to_new_file(stream: Any, output_path: Path, *, maximum_bytes: int) -> tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(output_path), flags, 0o600)
    except FileExistsError as exc:
        raise SourceTransportError("refusing to overwrite a local read-back artifact") from exc
    except OSError as exc:
        raise SourceTransportError("cannot safely create a local read-back artifact") from exc
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(descriptor, "wb") as handle:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise SourceTransportError("Object Storage read-back returned non-bytes content")
                if len(chunk) > maximum_bytes - total:
                    raise SourceTransportError("Object Storage read-back ciphertext exceeds its declared size")
                handle.write(chunk)
                digest.update(chunk)
                total += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    return digest.hexdigest(), total


def _require_version_id(value: object, *, field: str) -> str:
    return contract._require_version_id(value, field=field)


def _reject_provider_side_encryption(response: Mapping[str, Any]) -> None:
    """Reject every S3 response field that can assert provider-side encryption."""

    if any(
        response.get(name) is not None
        for name in (
            "ServerSideEncryption",
            "SSECustomerAlgorithm",
            "SSECustomerKeyMD5",
            "SSEKMSKeyId",
        )
    ):
        raise SourceTransportError("provider-side Object Storage encryption is not permitted")


def _verify_exact_object_readback(
    client: Any,
    *,
    bucket: str,
    key: str,
    version_id: str,
    expected_sha256: str,
    expected_bytes: int,
    mode: str,
    workspace: Path,
) -> None:
    target = workspace / ("readback-" + secrets.token_hex(12) + ".age")
    try:
        try:
            response = client.get_object(Bucket=bucket, Key=key, VersionId=version_id)
        except Exception as exc:
            raise SourceTransportError("cannot read back the exact immutable Object Storage version") from exc
        if not isinstance(response, Mapping):
            raise SourceTransportError("Object Storage read-back response is malformed")
        if _require_version_id(response.get("VersionId"), field="Object Storage read-back VersionId") != version_id:
            raise SourceTransportError("Object Storage read-back returned a different VersionId")
        _reject_provider_side_encryption(response)
        metadata = response.get("Metadata", {})
        if not isinstance(metadata, Mapping):
            raise SourceTransportError("Object Storage read-back metadata is malformed")
        if metadata != _ciphertext_metadata(expected_sha256, mode):
            raise SourceTransportError("Object Storage read-back metadata does not match the encrypted artifact")
        body = response.get("Body")
        if body is None or not hasattr(body, "read"):
            raise SourceTransportError("Object Storage read-back has no readable body")
        observed_sha256, observed_bytes = _write_stream_to_new_file(
            body,
            target,
            maximum_bytes=expected_bytes,
        )
        if observed_sha256 != expected_sha256 or observed_bytes != expected_bytes:
            raise SourceTransportError("Object Storage read-back ciphertext does not match the uploaded artifact")
    finally:
        target.unlink(missing_ok=True)


def upload_immutable_encrypted_object(
    client: Any,
    *,
    bucket: str,
    key: str,
    ciphertext_path: Path,
    mode: str,
    workspace: Path,
) -> dict[str, Any]:
    """Create and exact-read-back one no-SSE versioned object."""

    _snapshot_error(lambda: snapshot.assert_object_absent(client, bucket=bucket, key=key))
    ciphertext_sha256, ciphertext_bytes = sha256_file(ciphertext_path)
    with ciphertext_path.open("rb") as handle:
        try:
            response = client.put_object(
                Bucket=bucket,
                Key=key,
                Body=handle,
                ContentType="application/octet-stream",
                Metadata=_ciphertext_metadata(ciphertext_sha256, mode),
                IfNoneMatch="*",
            )
        except Exception as exc:
            raise SourceTransportError("conditional immutable Object Storage upload failed") from exc
    if not isinstance(response, Mapping):
        raise SourceTransportError("Object Storage upload returned a malformed response")
    _reject_provider_side_encryption(response)
    version_id = _require_version_id(response.get("VersionId"), field="Object Storage upload VersionId")
    _snapshot_error(
        lambda: snapshot.require_singleton_immutable_object_version(
            client,
            bucket=bucket,
            key=key,
            expected_version_id=version_id,
        )
    )
    _verify_exact_object_readback(
        client,
        bucket=bucket,
        key=key,
        version_id=version_id,
        expected_sha256=ciphertext_sha256,
        expected_bytes=ciphertext_bytes,
        mode=mode,
        workspace=workspace,
    )
    return {
        "object_key": key,
        "version_id": version_id,
        "ciphertext_sha256": ciphertext_sha256,
        "ciphertext_bytes": ciphertext_bytes,
    }


def _object_descriptor(value: Mapping[str, Any], *, maximum_plaintext_bytes: int) -> dict[str, Any]:
    """Normalize with the one portable source transport descriptor contract."""

    return contract.validate_object_descriptor(
        value,
        maximum_plaintext_bytes=maximum_plaintext_bytes,
    )


def build_publish_receipt(
    *,
    config: SourceTransportPolicy,
    request: SourceObjectRequest,
    descriptor: Mapping[str, Any],
) -> dict[str, Any]:
    return contract.build_publish_receipt(config=config, request=request, descriptor=descriptor)


def verify_publish_receipt(
    *,
    config: SourceTransportPolicy,
    payload: bytes,
) -> dict[str, Any]:
    """Verify a canonical URL-free local receipt before a later phase consumes it."""

    return contract.verify_publish_receipt(config=config, payload=payload)


def write_create_only_receipt(
    path: Path,
    receipt: Mapping[str, Any],
    *,
    config: SourceTransportPolicy,
) -> None:
    """Persist one verified receipt with a single final-path ``O_EXCL`` create.

    The final filename is never replaced.  A concurrent creator wins the race
    and blocks this call, leaving both the pre-existing receipt and any local
    evidence untouched.  Validation happens before the final path is opened.
    """

    if not isinstance(path, Path) or not path.is_absolute():
        raise SourceTransportError("receipt_path must be absolute")
    _require_private_directory(path.parent, field="source transport receipt parent")
    encoded = canonical_json_bytes(receipt) + b"\n"
    verified = contract.verify_publish_receipt(config=config, payload=encoded)
    if verified != receipt:
        raise SourceTransportError("source transport receipt is not a canonical verified receipt")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise SourceTransportError("refusing to overwrite a source transport receipt") from exc
    except OSError as exc:
        raise SourceTransportError("cannot safely create a source transport receipt") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            view = memoryview(encoded)
            while view:
                written = handle.write(view)
                if written is None:
                    written = len(view)
                if written <= 0:  # pragma: no cover - regular file writes do not normally return zero.
                    raise OSError("short source transport receipt write")
                view = view[written:]
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as exc:
        # Preserve a failed final-path artifact as fail-closed evidence.  It is
        # never unlinked here because another creator must never be removed.
        raise SourceTransportError("cannot durably create a source transport receipt") from exc
    _require_private_file(path, field="source transport receipt", maximum_bytes=1024 * 1024)


def publish_controller_source_object(
    client: Any,
    *,
    config: SourceTransportPolicy,
    request: SourceObjectRequest,
    plaintext_path: Path,
    receipt_path: Path | None = None,
    encryptor: Callable[[str, Sequence[str], Path, Path], None] = run_age_encrypt_many,
) -> dict[str, Any]:
    """Controller-only direct publication for a controller-local plaintext.

    WebApp-FI must not use this function because it takes an authenticated S3
    client.  FI-originated payloads use a separate self-contained sender with
    the controller's transient presigned PUT plan, then this controller module
    finalizes the exact read-back.
    """

    _require_root_execution()
    config = _validate_policy(config)
    recipients = validate_request(config, request)
    if request.source_site not in {"controller", "bot_fi"}:
        raise SourceTransportError(
            "authenticated controller publication is allowed only for controller-local source objects"
        )
    key = source_object_key(config, request)
    _require_root_only_input(plaintext_path, field="source plaintext", maximum_bytes=config.maximum_plaintext_bytes)
    if receipt_path is not None:
        if not receipt_path.is_absolute():
            raise SourceTransportError("receipt_path must be absolute")
        if receipt_path.exists() or receipt_path.is_symlink():
            raise SourceTransportError("refusing to overwrite a source transport receipt")

    # Recipient validation above is intentionally before both the bucket check and age.
    _snapshot_error(lambda: snapshot.assert_private_versioned_bucket(client, config.bucket))
    lock_name = request.campaign_id + "-" + request.object_id
    with locked_workspace(config.workspace, lock_name=lock_name) as workspace:
        plaintext = create_immutable_plaintext_snapshot(
            source_path=plaintext_path,
            workspace=workspace,
            snapshot_name=request.object_id,
            maximum_bytes=config.maximum_plaintext_bytes,
        )
        ciphertext = workspace / "source-object.age"
        try:
            encryptor(config.age_binary, recipients, plaintext.path, ciphertext)
        except SourceTransportError:
            raise
        except Exception as exc:
            raise SourceTransportError("age encryption failed") from exc
        _require_private_file(
            ciphertext,
            field="encrypted source transport artifact",
            maximum_bytes=config.maximum_plaintext_bytes + MAXIMUM_CIPHERTEXT_OVERHEAD_BYTES,
        )
        # A hostile or broken encryptor cannot alter the signed plaintext binding.
        if sha256_file(plaintext.path) != (plaintext.sha256, plaintext.bytes):
            raise SourceTransportError("immutable plaintext snapshot changed while being encrypted")
        remote = upload_immutable_encrypted_object(
            client,
            bucket=config.bucket,
            key=key,
            ciphertext_path=ciphertext,
            mode=request.mode,
            workspace=workspace,
        )

    descriptor = _object_descriptor(
        {
            **remote,
            "plaintext_sha256": plaintext.sha256,
            "plaintext_bytes": plaintext.bytes,
        },
        maximum_plaintext_bytes=config.maximum_plaintext_bytes,
    )
    receipt = build_publish_receipt(config=config, request=request, descriptor=descriptor)
    if receipt_path is not None:
        write_create_only_receipt(receipt_path, receipt, config=config)
    return receipt


# Controller-local source objects may use this direct authenticated path.  Keep
# the generic alias explicitly controller-scoped to avoid accidental FI use.
publish = publish_controller_source_object


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    publish_parser = subparsers.add_parser("publish", help="publish one immutable URL-free source-phase receipt")
    publish_parser.add_argument("--config", required=True, type=Path)
    publish_parser.add_argument("--campaign-binding", required=True, type=Path)
    publish_parser.add_argument("--source-site", required=True)
    publish_parser.add_argument("--destination-site", required=True)
    publish_parser.add_argument("--object-kind", required=True)
    publish_parser.add_argument("--object-id", required=True)
    publish_parser.add_argument("--plaintext", required=True, type=Path)
    publish_parser.add_argument("--receipt", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_arguments(argv)
        if args.command != "publish":  # pragma: no cover - argparse dispatch invariant.
            raise SourceTransportError("unsupported command")
        controller_config = load_controller_config(args.config)
        request = request_from_campaign_binding(
            config=controller_config.policy,
            campaign_binding_path=args.campaign_binding,
            source_site=args.source_site,
            destination_site=args.destination_site,
            object_kind=args.object_kind,
            object_id=args.object_id,
        )
        client = create_s3_client(controller_config)
        receipt = publish_controller_source_object(
            client,
            config=controller_config.policy,
            request=request,
            plaintext_path=args.plaintext,
            receipt_path=args.receipt,
        )
        print(canonical_json_bytes(receipt).decode("utf-8"))
        return 0
    except SourceTransportError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - CLI wrapper.
    raise SystemExit(main())
