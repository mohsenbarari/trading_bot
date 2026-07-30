#!/usr/bin/env python3
"""Render and verify one campaign-bound WebApp-FI source role config.

The source role config is a controller-side control artifact that is later
embedded in the controller-signed static-provenance packet.  It deliberately
does not inspect Docker, Compose, environment files, a release checkout, or
any host runtime.  The only operational values it accepts are the two
explicitly named containers.  Campaign, application, and control identity are
derived from the immutable controller campaign binding.

By default the artifact has one fixed create-only location::

    /etc/trading-bot-three-site/campaigns/<campaign>/webapp-fi-source/
        source-role-config.json

``--campaigns-root`` is available only for an already-created, root-only
deterministic campaign root, primarily for isolated controller workspaces and
tests.  It retains the exact same ``<campaign>/webapp-fi-source`` layout.
This helper has no network, Object Storage, SSH, Docker, service, or runtime
capability.  Without ``--apply`` it is a read-only preflight.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Callable, Mapping, Sequence


CAMPAIGNS_ROOT = Path("/etc/trading-bot-three-site/campaigns")
SOURCE_PHASE_DIRECTORY = "webapp-fi-source"
CAMPAIGN_BINDING_FILENAME = "campaign-binding.json"
SOURCE_ROLE_CONFIG_FILENAME = "source-role-config.json"

SOURCE_ROLE_CONFIG_SCHEMA = "gold-trade-webapp-fi-source-role-config-v3"
SOURCE_SITE = "webapp_fi"
DESTINATION_SITE = "webapp_ir"
FI_SOURCE_SIGNER_CAMPAIGN_ROOT = PurePosixPath("/etc/trading-bot-three-site/campaigns")
FI_SOURCE_SIGNER_DIRECTORY = "webapp-fi"
FI_SOURCE_SIGNER_KEY_NAME = "source-signing-ed25519.raw"

MAX_ROLE_CONFIG_BYTES = 16 * 1024
CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
URL_MARKERS = (b"://", b'"url"', b"presigned")


class SourceRoleConfigError(RuntimeError):
    """A WebApp-FI source role config is unsafe or inconsistent."""


@dataclasses.dataclass(frozen=True)
class SourceRoleConfigLayout:
    """The one deterministic controller path for a bound role config."""

    campaign_id: str
    campaign_binding_sha256: str
    campaign_directory: Path
    source_phase_directory: Path
    role_config_path: Path


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Encode one control artifact in the repository's canonical form."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceRoleConfigError("WebApp-FI source role config JSON contains duplicate keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise SourceRoleConfigError("WebApp-FI source role config JSON contains an unsupported constant")


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise SourceRoleConfigError("WebApp-FI source role config operations must run as root")


def _require_root_controlled_code_file(path: Path, *, field: str) -> Path:
    if not path.is_absolute():
        raise RuntimeError(f"{field} must be absolute")
    current = Path(path.anchor)
    for component in path.parts[1:-1]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:  # pragma: no cover - repository layout invariant.
            raise RuntimeError(f"cannot inspect {field} parent") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or ((mode & 0o022) and not (metadata.st_mode & stat.S_ISVTX))
        ):
            raise RuntimeError(f"{field} parent is not root-controlled")
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        target = resolved.lstat()
    except OSError as exc:  # pragma: no cover - repository layout invariant.
        raise RuntimeError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or stat.S_ISLNK(target.st_mode)
        or not stat.S_ISREG(target.st_mode)
        or target.st_uid != 0
        or target.st_nlink != 1
        or stat.S_IMODE(target.st_mode) & 0o022
        or target.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
    ):
        raise RuntimeError(f"{field} is not a root-owned non-writable regular non-symlink file")
    return path


def _load_exact_sibling(filename: str, module_name: str) -> Any:
    if not isinstance(filename, str) or not filename or Path(filename).name != filename or filename in {".", ".."}:
        raise RuntimeError("required sibling filename is invalid")
    source = _require_root_controlled_code_file(Path(__file__).absolute(), field="source role config renderer")
    path = _require_root_controlled_code_file(source.with_name(filename), field=f"required sibling {filename}")
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


binding = _load_exact_sibling(
    "webapp_fi_source_campaign_binding.py",
    "_webapp_fi_source_role_config_binding",
)


def _raise_binding_error(action: Callable[[], Any], *, message: str) -> Any:
    try:
        return action()
    except binding.CampaignBindingError as exc:
        raise SourceRoleConfigError(message) from exc


def _require_private_directory(path: Path, *, field: str) -> Path:
    return _raise_binding_error(
        lambda: binding._require_root_private_directory(Path(path), field=field),
        message=f"{field} is unsafe",
    )


def _read_private_file(path: Path, *, field: str) -> bytes:
    return _raise_binding_error(
        lambda: binding._read_root_private_file(Path(path), field=field),
        message=f"{field} is unsafe",
    )


def _fsync_private_directory(path: Path, *, field: str) -> None:
    _raise_binding_error(
        lambda: binding._fsync_root_private_directory(Path(path), field=field),
        message=f"cannot durably sync {field}",
    )


def _normalize_binding(campaign_binding: Any) -> Any:
    """Require the exact immutable binding type, not an operator projection."""

    if not isinstance(campaign_binding, binding.CampaignBinding):
        raise SourceRoleConfigError("canonical campaign binding is invalid")
    # The dataclass originates from a root-only canonical binding reader.  Run
    # each field through the binding builder as a cheap structural recheck,
    # without accepting a caller-provided tree or identity projection.
    value = _raise_binding_error(
        lambda: binding.build_campaign_binding(
            campaign_id=campaign_binding.campaign_id,
            application_release_sha=campaign_binding.application_release_sha,
            application_release_tree=campaign_binding.application_release_tree,
            expected_alembic_revision=campaign_binding.expected_alembic_revision,
            control_commit=campaign_binding.control_commit,
            control_tree=campaign_binding.control_tree,
        ),
        message="canonical campaign binding is invalid",
    )
    if value["binding_sha256"] != campaign_binding.binding_sha256:
        raise SourceRoleConfigError("canonical campaign binding checksum is invalid")
    return campaign_binding


def expected_source_signing_key_path(campaign_id: str) -> str:
    """Return the only campaign-derived FI private-key reference allowed."""

    campaign = _raise_binding_error(
        lambda: binding._require_id(campaign_id, field="campaign_id", pattern=binding.CAMPAIGN_ID_RE),
        message="campaign_id is invalid",
    )
    return str(
        FI_SOURCE_SIGNER_CAMPAIGN_ROOT
        / campaign
        / FI_SOURCE_SIGNER_DIRECTORY
        / FI_SOURCE_SIGNER_KEY_NAME
    )


def _require_container_name(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not CONTAINER_NAME_RE.fullmatch(value):
        raise SourceRoleConfigError(f"{field} is invalid")
    return value


def _parse_canonical_payload(payload: bytes) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_ROLE_CONFIG_BYTES:
        raise SourceRoleConfigError("WebApp-FI source role config has an unsafe size")
    lowered = payload.lower()
    if any(marker in lowered for marker in URL_MARKERS):
        raise SourceRoleConfigError("WebApp-FI source role config persists a forbidden URL")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceRoleConfigError("WebApp-FI source role config is not strict JSON") from exc
    if not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise SourceRoleConfigError("WebApp-FI source role config is not canonical JSON")
    return value


def build_source_role_config(
    *,
    campaign_binding: Any,
    application_container: str,
    sync_worker_container: str,
) -> dict[str, Any]:
    """Derive the complete config from one binding and explicit runtime names."""

    campaign = _normalize_binding(campaign_binding)
    application_name = _require_container_name(application_container, field="application_container")
    sync_name = _require_container_name(sync_worker_container, field="sync_worker_container")
    if application_name == sync_name:
        raise SourceRoleConfigError("application_container and sync_worker_container must be distinct")
    return {
        "schema": SOURCE_ROLE_CONFIG_SCHEMA,
        "campaign_id": campaign.campaign_id,
        "campaign_binding_sha256": campaign.binding_sha256,
        "source_site": SOURCE_SITE,
        "destination_site": DESTINATION_SITE,
        "application": {
            "release_sha": campaign.application_release_sha,
            "release_tree": campaign.application_release_tree,
            "expected_alembic_revision": campaign.expected_alembic_revision,
        },
        "tooling": {
            "control_commit": campaign.control_commit,
            "control_tree": campaign.control_tree,
        },
        "application_container": application_name,
        "sync_worker_container": sync_name,
        "source_signing_private_key_file": expected_source_signing_key_path(campaign.campaign_id),
    }


def validate_source_role_config_payload(*, payload: bytes, campaign_binding: Any) -> dict[str, Any]:
    """Verify canonical v3 config bytes against one immutable binding.

    The return value is normalized but deliberately retains no filesystem
    metadata.  Callers that need a path should use ``load_source_role_config``
    after establishing their root-only file boundary.
    """

    campaign = _normalize_binding(campaign_binding)
    value = _parse_canonical_payload(payload)
    expected = {
        "schema",
        "campaign_id",
        "campaign_binding_sha256",
        "source_site",
        "destination_site",
        "application",
        "tooling",
        "application_container",
        "sync_worker_container",
        "source_signing_private_key_file",
    }
    if set(value) != expected or value.get("schema") != SOURCE_ROLE_CONFIG_SCHEMA:
        raise SourceRoleConfigError("WebApp-FI source role config is unsupported")
    if value.get("campaign_id") != campaign.campaign_id:
        raise SourceRoleConfigError("WebApp-FI source role config campaign does not match binding")
    if value.get("campaign_binding_sha256") != campaign.binding_sha256:
        raise SourceRoleConfigError("WebApp-FI source role config binding does not match campaign")
    if value.get("source_site") != SOURCE_SITE or value.get("destination_site") != DESTINATION_SITE:
        raise SourceRoleConfigError("WebApp-FI source role config site binding is invalid")
    expected_application = {
        "release_sha": campaign.application_release_sha,
        "release_tree": campaign.application_release_tree,
        "expected_alembic_revision": campaign.expected_alembic_revision,
    }
    if value.get("application") != expected_application:
        raise SourceRoleConfigError("WebApp-FI source role config application does not match binding")
    expected_tooling = {"control_commit": campaign.control_commit, "control_tree": campaign.control_tree}
    if value.get("tooling") != expected_tooling:
        raise SourceRoleConfigError("WebApp-FI source role config tooling does not match binding")
    application_name = _require_container_name(value.get("application_container"), field="application_container")
    sync_name = _require_container_name(value.get("sync_worker_container"), field="sync_worker_container")
    if application_name == sync_name:
        raise SourceRoleConfigError("application_container and sync_worker_container must be distinct")
    if value.get("source_signing_private_key_file") != expected_source_signing_key_path(campaign.campaign_id):
        raise SourceRoleConfigError("WebApp-FI source role config signing key path is not campaign-derived")
    return {
        "schema": SOURCE_ROLE_CONFIG_SCHEMA,
        "campaign_id": campaign.campaign_id,
        "campaign_binding_sha256": campaign.binding_sha256,
        "source_site": SOURCE_SITE,
        "destination_site": DESTINATION_SITE,
        "application": expected_application,
        "tooling": expected_tooling,
        "application_container": application_name,
        "sync_worker_container": sync_name,
        "source_signing_private_key_file": expected_source_signing_key_path(campaign.campaign_id),
    }


def _load_exact_campaign_binding_path(
    campaign_binding_path: Path,
    *,
    campaigns_root: Path,
) -> tuple[Any, SourceRoleConfigLayout]:
    campaign = _raise_binding_error(
        lambda: binding.load_campaign_binding(Path(campaign_binding_path)),
        message="canonical campaign binding is invalid",
    )
    campaign = _normalize_binding(campaign)
    root = _require_private_directory(Path(campaigns_root), field="controller campaigns root")
    campaign_directory = _require_private_directory(root / campaign.campaign_id, field="controller campaign directory")
    source_phase = _require_private_directory(
        campaign_directory / SOURCE_PHASE_DIRECTORY,
        field="controller source-phase directory",
    )
    expected_binding = source_phase / CAMPAIGN_BINDING_FILENAME
    if Path(campaign_binding_path) != expected_binding:
        raise SourceRoleConfigError("campaign binding is not installed at its fixed controller campaign path")
    # Re-open through the fixed path after layout admission so the output is
    # never selected from an equivalent binding elsewhere.
    loaded = _raise_binding_error(
        lambda: binding.load_campaign_binding(expected_binding),
        message="canonical campaign binding is invalid",
    )
    loaded = _normalize_binding(loaded)
    if loaded != campaign:
        raise SourceRoleConfigError("canonical campaign binding changed while validating its layout")
    return loaded, SourceRoleConfigLayout(
        campaign_id=loaded.campaign_id,
        campaign_binding_sha256=loaded.binding_sha256,
        campaign_directory=campaign_directory,
        source_phase_directory=source_phase,
        role_config_path=source_phase / SOURCE_ROLE_CONFIG_FILENAME,
    )


def role_config_layout_for_campaign_binding(
    campaign_binding_path: Path,
    *,
    campaigns_root: Path = CAMPAIGNS_ROOT,
) -> SourceRoleConfigLayout:
    """Return the fixed config layout after revalidating the binding path."""

    _require_root_execution()
    _campaign, layout = _load_exact_campaign_binding_path(
        Path(campaign_binding_path),
        campaigns_root=Path(campaigns_root),
    )
    return layout


def _require_absent(path: Path, *, field: str) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise SourceRoleConfigError(f"cannot inspect {field}") from exc
    raise SourceRoleConfigError(f"{field} already exists and will not be reused")


def _safe_private_file(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == 0
        and metadata.st_nlink == 1
        and not (metadata.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX))
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and 1 <= metadata.st_size <= MAX_ROLE_CONFIG_BYTES
    )


def _write_new_private_file(path: Path, payload: bytes) -> None:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_ROLE_CONFIG_BYTES:
        raise SourceRoleConfigError("WebApp-FI source role config payload is invalid")
    _require_private_directory(path.parent, field="WebApp-FI source role config parent")
    _require_absent(path, field="WebApp-FI source role config")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:  # pragma: no cover - Linux deployment invariant.
        raise SourceRoleConfigError("secure no-follow file creation is unavailable")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | no_follow
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise SourceRoleConfigError("WebApp-FI source role config already exists and will not be reused") from exc
    except OSError as exc:
        raise SourceRoleConfigError("cannot create WebApp-FI source role config") from exc
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:  # pragma: no cover - regular-file writes do not return zero.
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
        if not _safe_private_file(os.fstat(descriptor)):
            raise SourceRoleConfigError("new WebApp-FI source role config is unsafe")
    except SourceRoleConfigError:
        raise
    except OSError as exc:
        raise SourceRoleConfigError("cannot durably create WebApp-FI source role config") from exc
    finally:
        os.close(descriptor)
    _fsync_private_directory(path.parent, field="WebApp-FI source role config parent")


def load_source_role_config(*, path: Path, campaign_binding: Any) -> dict[str, Any]:
    """Read one root-only config and prove it matches the supplied binding."""

    _require_root_execution()
    campaign = _normalize_binding(campaign_binding)
    payload = _read_private_file(Path(path), field="WebApp-FI source role config")
    return validate_source_role_config_payload(payload=payload, campaign_binding=campaign)


def render_source_role_config(
    *,
    campaign_binding_path: Path,
    application_container: str,
    sync_worker_container: str,
    campaigns_root: Path = CAMPAIGNS_ROOT,
    apply: bool = False,
) -> dict[str, Any]:
    """Plan or create exactly one campaign-derived role config file."""

    _require_root_execution()
    if not isinstance(apply, bool):
        raise SourceRoleConfigError("apply flag is invalid")
    campaign, layout = _load_exact_campaign_binding_path(
        Path(campaign_binding_path),
        campaigns_root=Path(campaigns_root),
    )
    value = build_source_role_config(
        campaign_binding=campaign,
        application_container=application_container,
        sync_worker_container=sync_worker_container,
    )
    payload = canonical_json_bytes(value) + b"\n"
    validated = validate_source_role_config_payload(payload=payload, campaign_binding=campaign)
    _require_absent(layout.role_config_path, field="WebApp-FI source role config")
    result = {
        "status": "rendered" if apply else "planned",
        "campaign_id": layout.campaign_id,
        "campaign_binding_sha256": layout.campaign_binding_sha256,
        "role_config_path": str(layout.role_config_path),
        "role_config_sha256": sha256_bytes(payload),
        "application": dict(validated["application"]),
        "tooling": dict(validated["tooling"]),
        "runtime_containers": {
            "application": validated["application_container"],
            "sync_worker": validated["sync_worker_container"],
        },
    }
    if not apply:
        return result
    _write_new_private_file(layout.role_config_path, payload)
    created = _read_private_file(layout.role_config_path, field="created WebApp-FI source role config")
    if created != payload:
        raise SourceRoleConfigError("created WebApp-FI source role config changed while being verified")
    if load_source_role_config(path=layout.role_config_path, campaign_binding=campaign) != validated:
        raise SourceRoleConfigError("created WebApp-FI source role config cannot be verified")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    render = actions.add_parser("render", help="plan or create one campaign-bound source role config")
    render.add_argument("--campaign-binding", required=True, type=Path)
    render.add_argument("--application-container", required=True)
    render.add_argument("--sync-worker-container", required=True)
    render.add_argument("--campaigns-root", type=Path, default=CAMPAIGNS_ROOT)
    render.add_argument("--apply", action="store_true")
    verify = actions.add_parser("verify", help="read and validate one existing source role config")
    verify.add_argument("--campaign-binding", required=True, type=Path)
    verify.add_argument("--source-role-config", required=True, type=Path)
    verify.add_argument("--campaigns-root", type=Path, default=CAMPAIGNS_ROOT)
    return parser


def _print(value: Mapping[str, Any], *, stream: Any = sys.stdout) -> None:
    stream.buffer.write(canonical_json_bytes(value) + b"\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        campaign, _layout = _load_exact_campaign_binding_path(
            Path(args.campaign_binding),
            campaigns_root=Path(args.campaigns_root),
        )
        if args.action == "render":
            result = render_source_role_config(
                campaign_binding_path=args.campaign_binding,
                application_container=args.application_container,
                sync_worker_container=args.sync_worker_container,
                campaigns_root=args.campaigns_root,
                apply=args.apply,
            )
        else:
            loaded = load_source_role_config(path=args.source_role_config, campaign_binding=campaign)
            result = {
                "status": "verified",
                "campaign_id": loaded["campaign_id"],
                "campaign_binding_sha256": loaded["campaign_binding_sha256"],
                "application": dict(loaded["application"]),
                "tooling": dict(loaded["tooling"]),
                "runtime_containers": {
                    "application": loaded["application_container"],
                    "sync_worker": loaded["sync_worker_container"],
                },
            }
    except SourceRoleConfigError as exc:
        _print({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, stream=sys.stderr)
        return 2
    _print(result)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI wrapper.
    raise SystemExit(main())
