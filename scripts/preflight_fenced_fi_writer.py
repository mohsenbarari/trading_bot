#!/usr/bin/env python3
"""Read-only admission gate for the separate fenced WebApp-FI writer.

This is intentionally independent from the legacy FI preflight: it admits
only the fixed ``fenced_fi_writer`` app+bot Compose project.  It never starts,
stops, recreates, pulls, builds, removes, or changes a Docker resource, and
never contacts a peer, Object Storage, or the Writer Witness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.production_writer_lease import load_production_writer_lease
from scripts import production_writer_lease_agent as lease_agent
from scripts import verify_fenced_fi_release_identity as release_identity_verifier


PREFLIGHT_SCHEMA = "fenced-fi-writer-preflight-v1"
MAX_FILE_BYTES = 256 * 1024
MAX_COMPOSE_BYTES = 4 * 1024 * 1024
MAX_CONTAINER_BYTES = 512 * 1024
PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
IMAGE_REF_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}(?::[A-Za-z0-9][A-Za-z0-9._-]{0,127})?$"
)
IMAGE_REPO_DIGEST_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,511}@sha256:[0-9a-f]{64}$"
)
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
FENCED_SERVICES = ("app", "bot")
FENCED_CONTAINER_NAMES = {
    "app": "trading_bot_wa_fi_writer_2c08_app",
    "bot": "trading_bot_wa_fi_writer_2c08_bot",
}
TERM_PATH = "/run/trading-bot-writer-term"
TERM_LEASE_NAME = "writer-lease.json"
TERM_SAFETY_MARGIN_SECONDS = 15
TERM_MAX_LEASE_DURATION_SECONDS = 60
TERM_RENEW_INTERVAL_SECONDS = 10
FENCED_UNIT_NAME = "trading-bot-production-writer-fi-fenced-lease-guard.service"
APPROVED_FENCED_UNIT_FILE = Path("/etc/systemd/system") / FENCED_UNIT_NAME
FENCED_UNIT_TEMPLATE_RELATIVE = Path(
    "deploy/systemd/trading-bot-production-writer-fi-fenced-lease-guard.service.template"
)
FENCED_TERM_PARENT_DIRECTORY = Path("/var/lib/trading-bot-three-site/writer-terms")
FENCED_COMPOSE_RELATIVE_PATH = Path(
    "deploy/production/docker-compose.webapp-fi-writer-2c08.yml"
)


class FencedFiWriterPreflightError(RuntimeError):
    """The host has not proved the new fenced FI runtime safe to guard."""


@dataclass(frozen=True)
class StaticServiceExpectation:
    name: str
    container_name: str
    image_ref: str
    image_repo_digest: str
    image_id: str


@dataclass(frozen=True)
class RuntimeServiceExpectation:
    """One post-start identity attested by the root-only runtime receipt.

    Container IDs do not exist during ``cutover-pre``.  They are therefore
    deliberately absent from the static preflight configuration and can only
    enter the guard-start path through the receipt written after both
    containers are healthy.
    """

    name: str
    container_name: str
    container_id: str
    image_ref: str
    image_id: str
    labels_sha256: str


@dataclass(frozen=True)
class FencedFiReleaseIdentityInputs:
    descriptor_path: Path
    authority_path: Path
    expected_identity_sha256: str


@dataclass(frozen=True)
class FencedFiRuntimeResources:
    """Root-reviewed physical Docker resources for the isolated FI scope.

    These are deliberately installation inputs rather than release-identity
    fields: their names differ by host.  The preflight pins them independently
    so a mutable runtime environment cannot redirect the fenced app to a
    different network or writable volume after the release has been reviewed.
    """

    network_name: str
    uploads_volume: str
    audit_volume: str


@dataclass(frozen=True)
class FencedFiWriterPreflightConfig:
    control_release_root: Path
    application_release_root: Path
    agent_config: Path
    preflight_config: Path
    unit_file: Path
    lease_file: Path
    runtime_env_file: Path
    runtime_env_sha256: str
    term_parent_directory: Path
    app_local_port: int
    release_identity: FencedFiReleaseIdentityInputs
    runtime_resources: FencedFiRuntimeResources
    compose_file: Path
    compose_project: str
    services: tuple[StaticServiceExpectation, ...]


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FencedFiWriterPreflightError("preflight JSON contains duplicate keys")
        result[key] = value
    return result


def _absolute(value: Any, *, label: str) -> Path:
    text = str(value or "")
    path = Path(text)
    if not PATH_RE.fullmatch(text) or ".." in path.parts:
        raise FencedFiWriterPreflightError(f"{label} must be an absolute closed path")
    return path


def _secure_read(
    path: Path,
    *,
    label: str,
    private: bool,
    max_size: int = MAX_FILE_BYTES,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise FencedFiWriterPreflightError(f"cannot securely open {label}") from exc
    try:
        before = os.fstat(descriptor)
        unsafe_permissions = 0o077 if private else 0o022
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & unsafe_permissions
            or before.st_nlink != 1
            or before.st_size < 0
            or before.st_size > max_size
        ):
            raise FencedFiWriterPreflightError(
                f"{label} is not an owner-controlled regular file"
            )
        chunks: list[bytes] = []
        remaining = max_size + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > max_size:
            raise FencedFiWriterPreflightError(f"{label} is oversized")
        after = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, name) != getattr(after, name) for name in identity):
            raise FencedFiWriterPreflightError(f"{label} changed while being read")
        return payload
    finally:
        os.close(descriptor)


def _secure_text(path: Path, *, label: str, private: bool, max_size: int = MAX_FILE_BYTES) -> str:
    try:
        return _secure_read(path, label=label, private=private, max_size=max_size).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FencedFiWriterPreflightError(f"{label} is not UTF-8") from exc


def _check_owner_directory(path: Path, *, label: str) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise FencedFiWriterPreflightError(f"cannot inspect {label}") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise FencedFiWriterPreflightError(f"{label} is not an owner-controlled directory")


def _require_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise FencedFiWriterPreflightError(f"{label} is invalid")
    return value


def _load_config(path: Path) -> FencedFiWriterPreflightConfig:
    if os.geteuid() != 0:
        raise FencedFiWriterPreflightError("fenced FI preflight must run as root")
    safe_path = _absolute(str(path), label="preflight config")
    try:
        raw = json.loads(
            _secure_text(safe_path, label="preflight config", private=True),
            object_pairs_hook=_strict_object,
        )
    except FencedFiWriterPreflightError:
        raise
    except Exception as exc:
        raise FencedFiWriterPreflightError("preflight config is invalid JSON") from exc
    expected_fields = {
        "schema",
        "control_release_root",
        "application_release_root",
        "agent_config",
        "preflight_config",
        "unit_file",
        "lease_file",
        "runtime_env_file",
        "runtime_env_sha256",
        "term_parent_directory",
        "app_local_port",
        "release_identity",
        "runtime_resources",
        "runtime",
    }
    if not isinstance(raw, dict) or set(raw) != expected_fields or raw.get("schema") != PREFLIGHT_SCHEMA:
        raise FencedFiWriterPreflightError("preflight config schema is invalid")
    control_release_root = _absolute(raw.get("control_release_root"), label="control release root")
    application_release_root = _absolute(
        raw.get("application_release_root"), label="application release root"
    )
    if application_release_root.name != lease_agent.WA_IR_APPLICATION_RELEASE_SHA:
        raise FencedFiWriterPreflightError(
            "application release root is not the fixed legacy 2c08 release"
        )
    agent_config = _absolute(raw.get("agent_config"), label="fenced writer agent config")
    preflight_config = _absolute(raw.get("preflight_config"), label="preflight config path")
    if preflight_config != safe_path:
        raise FencedFiWriterPreflightError("preflight config path does not bind this input file")
    unit_file = _absolute(raw.get("unit_file"), label="fenced FI systemd unit file")
    if unit_file != APPROVED_FENCED_UNIT_FILE:
        raise FencedFiWriterPreflightError(
            "fenced FI systemd unit file is not the approved guard unit"
        )
    lease_file = _absolute(raw.get("lease_file"), label="writer lease file")
    if lease_file.name != TERM_LEASE_NAME:
        raise FencedFiWriterPreflightError("writer lease file has an unexpected name")
    runtime_env_file = _absolute(raw.get("runtime_env_file"), label="runtime env file")
    runtime_env_sha256 = _require_text(
        raw.get("runtime_env_sha256"), label="runtime env SHA-256"
    ).lower()
    if SHA256_RE.fullmatch(runtime_env_sha256) is None:
        raise FencedFiWriterPreflightError("runtime env SHA-256 is invalid")
    term_parent_directory = _absolute(
        raw.get("term_parent_directory"), label="writer term parent directory"
    )
    if lease_file.parent != term_parent_directory:
        raise FencedFiWriterPreflightError(
            "writer lease file is not inside the configured term parent directory"
        )
    if term_parent_directory != FENCED_TERM_PARENT_DIRECTORY:
        raise FencedFiWriterPreflightError(
            "writer term parent directory is not the fenced FI systemd write scope"
        )
    app_local_port = raw.get("app_local_port")
    if (
        type(app_local_port) is not int
        or app_local_port != lease_agent.WA_FI_FENCED_APP_LOCAL_PORT
    ):
        raise FencedFiWriterPreflightError(
            "fenced app loopback port must remain the isolated staged listener"
        )

    identity_raw = raw.get("release_identity")
    if not isinstance(identity_raw, dict) or set(identity_raw) != {
        "descriptor",
        "authority_public_key",
        "expected_identity_sha256",
    }:
        raise FencedFiWriterPreflightError("fenced FI release identity config is invalid")
    identity_descriptor = _absolute(
        identity_raw.get("descriptor"), label="fenced FI release identity descriptor"
    )
    identity_authority = _absolute(
        identity_raw.get("authority_public_key"),
        label="fenced FI release identity authority",
    )
    expected_identity_sha256 = _require_text(
        identity_raw.get("expected_identity_sha256"),
        label="fenced FI expected release identity hash",
    ).lower()
    if SHA256_RE.fullmatch(expected_identity_sha256) is None:
        raise FencedFiWriterPreflightError("fenced FI expected release identity hash is invalid")

    resources_raw = raw.get("runtime_resources")
    if not isinstance(resources_raw, dict) or set(resources_raw) != {
        "network_name",
        "uploads_volume",
        "audit_volume",
    }:
        raise FencedFiWriterPreflightError("fenced FI runtime resources are invalid")
    resources: dict[str, str] = {}
    for field, label in (
        ("network_name", "fenced FI runtime network"),
        ("uploads_volume", "fenced FI uploads volume"),
        ("audit_volume", "fenced FI audit volume"),
    ):
        value = _require_text(resources_raw.get(field), label=label)
        if lease_agent.DOCKER_RESOURCE_RE.fullmatch(value) is None:
            raise FencedFiWriterPreflightError(f"{label} is invalid")
        resources[field] = value

    runtime = raw.get("runtime")
    if not isinstance(runtime, dict) or set(runtime) != {
        "compose_file",
        "compose_project",
        "services",
    }:
        raise FencedFiWriterPreflightError("preflight runtime config is invalid")
    compose_file = _absolute(runtime.get("compose_file"), label="runtime compose file")
    expected_compose = (
        control_release_root / FENCED_COMPOSE_RELATIVE_PATH
    )
    if compose_file != expected_compose:
        raise FencedFiWriterPreflightError("runtime compose file is not the fixed fenced FI compose path")
    compose_project = _require_text(runtime.get("compose_project"), label="runtime compose project")
    if (
        not PROJECT_RE.fullmatch(compose_project)
        or compose_project != lease_agent.WA_FI_FENCED_WRITER_PROJECT_NAME
    ):
        raise FencedFiWriterPreflightError("runtime compose project is not the fixed fenced FI project")
    services_raw = runtime.get("services")
    if not isinstance(services_raw, list) or len(services_raw) != len(FENCED_SERVICES):
        raise FencedFiWriterPreflightError("preflight managed services are invalid")
    # The pre-start configuration binds only stable inputs.  Docker assigns
    # container IDs when cutover starts the project, so accepting IDs here
    # would make a legitimate cutover-pre impossible to satisfy.
    services: list[StaticServiceExpectation] = []
    for expected_name, item in zip(FENCED_SERVICES, services_raw, strict=True):
        if not isinstance(item, dict) or set(item) != {
            "name",
            "container_name",
            "image_ref",
            "image_repo_digest",
            "image_id",
        }:
            raise FencedFiWriterPreflightError("preflight service expectation is invalid")
        name = _require_text(item.get("name"), label="managed service name")
        if name != expected_name:
            raise FencedFiWriterPreflightError("preflight service scope is not exactly the fenced app and bot")
        container_name = _require_text(item.get("container_name"), label=f"{name} container name")
        if (
            not NAME_RE.fullmatch(container_name)
            or container_name != FENCED_CONTAINER_NAMES[name]
        ):
            raise FencedFiWriterPreflightError(f"{name} container name is not the fixed fenced name")
        image_ref = _require_text(item.get("image_ref"), label=f"{name} image reference")
        if not IMAGE_REF_RE.fullmatch(image_ref):
            raise FencedFiWriterPreflightError(f"{name} image reference is invalid")
        image_repo_digest = _require_text(
            item.get("image_repo_digest"), label=f"{name} immutable image repository digest"
        )
        if IMAGE_REPO_DIGEST_RE.fullmatch(image_repo_digest) is None:
            raise FencedFiWriterPreflightError(
                f"{name} immutable image repository digest is invalid"
            )
        image_id = str(item.get("image_id") or "").lower()
        if not IMAGE_ID_RE.fullmatch(image_id):
            raise FencedFiWriterPreflightError(f"{name} image ID is invalid")
        services.append(
            StaticServiceExpectation(
                name=name,
                container_name=container_name,
                image_ref=image_ref,
                image_repo_digest=image_repo_digest,
                image_id=image_id,
            )
        )
    return FencedFiWriterPreflightConfig(
        control_release_root=control_release_root,
        application_release_root=application_release_root,
        agent_config=agent_config,
        preflight_config=preflight_config,
        unit_file=unit_file,
        lease_file=lease_file,
        runtime_env_file=runtime_env_file,
        runtime_env_sha256=runtime_env_sha256,
        term_parent_directory=term_parent_directory,
        app_local_port=app_local_port,
        release_identity=FencedFiReleaseIdentityInputs(
            descriptor_path=identity_descriptor,
            authority_path=identity_authority,
            expected_identity_sha256=expected_identity_sha256,
        ),
        runtime_resources=FencedFiRuntimeResources(
            network_name=resources["network_name"],
            uploads_volume=resources["uploads_volume"],
            audit_volume=resources["audit_volume"],
        ),
        compose_file=compose_file,
        compose_project=compose_project,
        services=tuple(services),
    )


def _validate_release_layout(config: FencedFiWriterPreflightConfig) -> None:
    _check_owner_directory(config.control_release_root, label="control release root")
    _check_owner_directory(config.application_release_root, label="application release root")
    _check_owner_directory(config.term_parent_directory, label="writer term parent directory")
    for relative, label in (
        (Path("scripts/production_writer_lease_agent.py"), "writer lease agent"),
        (Path("scripts/preflight_fenced_fi_writer.py"), "fenced FI preflight helper"),
        (FENCED_UNIT_TEMPLATE_RELATIVE, "fenced FI systemd unit template"),
        (
            FENCED_COMPOSE_RELATIVE_PATH,
            "fenced FI compose file",
        ),
    ):
        _secure_read(
            config.control_release_root / relative,
            label=label,
            private=False,
            max_size=MAX_FILE_BYTES,
        )
    _secure_read(config.runtime_env_file, label="runtime env file", private=True)
    _secure_read(config.agent_config, label="fenced writer agent config", private=True)


def _validate_release_identity(config: FencedFiWriterPreflightConfig) -> Any:
    """Bind local fixed inputs to one independently signed Release-0 identity.

    A fixed 2c08 path or a local Docker tag alone is not an immutable release
    identity.  The descriptor is signed by a root-pinned authority and then
    compared to the exact control/application roots, compose bytes, image
    repository digests, and image IDs which this preflight will admit.
    """

    try:
        identity = release_identity_verifier.load_verified_fenced_fi_release_identity(
            descriptor_path=config.release_identity.descriptor_path,
            authority_path=config.release_identity.authority_path,
            expected_identity_sha256=config.release_identity.expected_identity_sha256,
        )
    except release_identity_verifier.VerifyFencedFiReleaseIdentityError as exc:
        raise FencedFiWriterPreflightError(
            "fenced FI signed release identity is not ready"
        ) from exc
    if (
        identity.release_sha != lease_agent.WA_IR_APPLICATION_RELEASE_SHA
        or identity.application_release_root != str(config.application_release_root)
        or identity.control_release_root != str(config.control_release_root)
        or identity.compose_relative_path != str(FENCED_COMPOSE_RELATIVE_PATH)
    ):
        raise FencedFiWriterPreflightError(
            "fenced FI signed release identity does not bind the fixed 2c08 runtime"
        )
    compose_bytes = _secure_read(
        config.compose_file,
        label="fenced FI compose file for signed identity",
        private=False,
        max_size=MAX_COMPOSE_BYTES,
    )
    if hashlib.sha256(compose_bytes).hexdigest() != identity.compose_sha256:
        raise FencedFiWriterPreflightError(
            "fenced FI signed release identity does not match the local Compose bytes"
        )
    identity_services = {
        "app": (identity.app_image_repo_digest, identity.app_image_id),
        "bot": (identity.bot_image_repo_digest, identity.bot_image_id),
    }
    for service in config.services:
        if (service.image_repo_digest, service.image_id) != identity_services[service.name]:
            raise FencedFiWriterPreflightError(
                f"{service.name} static image binding does not match the signed release identity"
            )
    return identity


def _validate_checked_out_release_tree(
    root: Path,
    *,
    expected_sha: str,
    expected_tree_sha: str,
    label: str,
) -> None:
    """Require a clean immutable Git checkout for each signed source claim.

    The descriptor's commit/tree fields are meaningful only if the local
    release roots prove them.  An archive-only installation has no equivalent
    proof here and deliberately fails closed rather than treating a directory
    name or a compose hash as an entire source-tree identity.
    """

    commit = _run_read_only(
        ["/usr/bin/git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
        label=f"{label} Git commit",
        cwd=root,
        max_output=1024,
    ).strip().lower()
    tree = _run_read_only(
        ["/usr/bin/git", "-C", str(root), "rev-parse", "--verify", "HEAD^{tree}"],
        label=f"{label} Git tree",
        cwd=root,
        max_output=1024,
    ).strip().lower()
    if commit != expected_sha or tree != expected_tree_sha:
        raise FencedFiWriterPreflightError(
            f"{label} Git identity does not match the signed release identity"
        )
    dirty = _run_read_only(
        ["/usr/bin/git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
        label=f"{label} Git worktree",
        cwd=root,
        max_output=MAX_COMPOSE_BYTES,
    )
    if dirty:
        raise FencedFiWriterPreflightError(
            f"{label} Git worktree is not immutable and clean"
        )


def _validate_release_trees(config: FencedFiWriterPreflightConfig, identity: Any) -> None:
    _validate_checked_out_release_tree(
        config.application_release_root,
        expected_sha=identity.release_sha,
        expected_tree_sha=identity.release_tree_sha,
        label="fenced FI application release",
    )
    _validate_checked_out_release_tree(
        config.control_release_root,
        expected_sha=identity.control_release_sha,
        expected_tree_sha=identity.control_release_tree_sha,
        label="fenced FI control release",
    )


def _reject_unfenced_legacy_application_release(identity: Any) -> None:
    """Fail closed for the known historical image/source release.

    The signed release identity makes the current application claim explicit:
    it is the clean 2c08 tree, not an unspecified rebuilt image.  Audit of
    that tree establishes that its app and bot do not consume the mounted
    Writer Witness term or the schema-bootstrap switch before startup side
    effects.  A successful host-only preflight must therefore never make that
    release look eligible for ``compose up``.  This is deliberately a hard
    gate rather than a documentation convention.

    A replacement requires a new immutable application SHA, source/image
    provenance, signed identity, and a dedicated real-image test proving the
    early term/schema gate.  It cannot be a patched image carrying the old
    2c08 source claim.
    """

    if getattr(identity, "release_sha", None) == lease_agent.WA_IR_APPLICATION_RELEASE_SHA:
        raise FencedFiWriterPreflightError(
            "fixed 2c08 application release is not term-fenced or schema-safe for fenced FI startup; "
            "a new immutable application release and signed identity are required"
        )


def _render_expected_unit(config: FencedFiWriterPreflightConfig) -> bytes:
    """Render the exact systemd unit from root-owned non-secret paths.

    `WorkingDirectory` accepts systemd specifiers but not EnvironmentFile
    expansion.  Keep all executable paths literal after rendering so a unit
    cannot silently start in a directory named by an unexpanded variable.
    """

    template_path = config.control_release_root / FENCED_UNIT_TEMPLATE_RELATIVE
    template = _secure_text(
        template_path,
        label="fenced FI systemd unit template",
        private=False,
    )
    replacements = {
        "__WA_FI_CONTROL_RELEASE_ROOT__": (str(config.control_release_root), 2),
        "__WA_FI_FENCED_AGENT_CONFIG__": (str(config.agent_config), 1),
    }
    for placeholder, (replacement, occurrences) in replacements.items():
        if template.count(placeholder) != occurrences:
            raise FencedFiWriterPreflightError(
                "fenced FI systemd unit template placeholders are invalid"
            )
        template = template.replace(placeholder, replacement)
    if "__" in template or "${" in template or "$" in template:
        raise FencedFiWriterPreflightError(
            "fenced FI systemd unit contains an unresolved environment expansion"
        )
    expected_lines = {
        "WorkingDirectory=/",
        "UMask=0077",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectSystem=full",
        "ProtectHome=read-only",
        "ReadWritePaths=/var/lib/trading-bot-three-site",
        "Conflicts=trading-bot-production-writer-fi-lease-guard.service",
        "Requires=docker.service",
    }
    lines = set(template.splitlines())
    if not expected_lines.issubset(lines) or any(
        line.startswith("EnvironmentFile=") for line in lines
    ):
        raise FencedFiWriterPreflightError(
            "fenced FI systemd unit does not meet the fixed hardening contract"
        )
    return template.encode("utf-8")


def _validate_installed_unit(config: FencedFiWriterPreflightConfig) -> None:
    actual = _secure_read(
        config.unit_file,
        label="installed fenced FI systemd unit",
        private=False,
    )
    if actual != _render_expected_unit(config):
        raise FencedFiWriterPreflightError(
            "installed fenced FI systemd unit does not match the pinned rendered template"
        )


def _validate_agent_config(config: FencedFiWriterPreflightConfig) -> lease_agent.AgentConfig:
    try:
        agent_config = lease_agent._load_config(config.agent_config)
    except lease_agent.ProductionWriterLeaseAgentError as exc:
        raise FencedFiWriterPreflightError("fenced writer agent config is not ready") from exc
    if (
        not lease_agent._is_fenced_fi_writer(agent_config)
        or agent_config.lease_file != config.lease_file
        or agent_config.runtime.compose_file != config.compose_file
        or agent_config.runtime.env_file != config.runtime_env_file
        or agent_config.runtime.selection_env_file is not None
        or agent_config.fenced_preflight_config != config.preflight_config
        or agent_config.runtime.services != FENCED_SERVICES
        or agent_config.witness.lease_duration_seconds != TERM_MAX_LEASE_DURATION_SECONDS
        or agent_config.witness.safety_margin_seconds != TERM_SAFETY_MARGIN_SECONDS
        or agent_config.witness.renew_interval_seconds != TERM_RENEW_INTERVAL_SECONDS
    ):
        raise FencedFiWriterPreflightError(
            "fenced writer agent config does not bind the fixed runtime and 60/15/10 Witness term"
        )
    return agent_config


def _validate_runtime_environment_binding(
    config: FencedFiWriterPreflightConfig,
    agent_config: lease_agent.AgentConfig,
) -> None:
    """Bind every Compose-affecting FI environment input before Docker reads it.

    The runtime file contains private database/application configuration, so
    the root-only preflight config records only its reviewed SHA-256.  The
    small set of values that selects a release root, image, term, network, or
    writable volume is additionally compared field-for-field.  This prevents
    a same-basename 2c08 directory or arbitrary Docker resource from being
    substituted through the environment file.
    """

    environment_hash = hashlib.sha256(
        _secure_read(
            config.runtime_env_file,
            label="fenced FI runtime environment",
            private=True,
        )
    ).hexdigest()
    if environment_hash != config.runtime_env_sha256:
        raise FencedFiWriterPreflightError(
            "fenced FI runtime environment does not match its reviewed SHA-256"
        )
    try:
        values = lease_agent._verify_fenced_fi_runtime_environment(agent_config)
    except lease_agent.ProductionWriterLeaseAgentError as exc:
        raise FencedFiWriterPreflightError(
            "fenced FI runtime environment is not ready"
        ) from exc
    static_by_name = {service.name: service for service in config.services}
    expected = {
        "RELEASE_SHA": lease_agent.WA_IR_APPLICATION_RELEASE_SHA,
        "WA_FI_WRITER_APP_IMAGE": static_by_name["app"].image_ref,
        "WA_FI_WRITER_BOT_IMAGE": static_by_name["bot"].image_ref,
        "WA_FI_WRITER_RUNTIME_ENV_FILE": str(config.runtime_env_file),
        "WA_FI_WRITER_APPLICATION_RELEASE_ROOT": str(config.application_release_root),
        "WA_FI_WRITER_TERM_PARENT_DIRECTORY": str(config.term_parent_directory),
        "WA_FI_WRITER_RUNTIME_NETWORK_NAME": config.runtime_resources.network_name,
        "WA_FI_WRITER_UPLOADS_VOLUME": config.runtime_resources.uploads_volume,
        "WA_FI_WRITER_AUDIT_VOLUME": config.runtime_resources.audit_volume,
        "WA_FI_WRITER_APP_LOCAL_PORT": str(config.app_local_port),
        "APPLICATION_WRITER_TERM_SAFETY_MARGIN_SECONDS": str(
            TERM_SAFETY_MARGIN_SECONDS
        ),
        "APPLICATION_WRITER_TERM_MAX_LEASE_DURATION_SECONDS": str(
            TERM_MAX_LEASE_DURATION_SECONDS
        ),
    }
    if any(values.get(field) != value for field, value in expected.items()):
        raise FencedFiWriterPreflightError(
            "fenced FI runtime environment does not bind the reviewed release resources"
        )


def _runtime_environment() -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


def _run_read_only(command: Sequence[str], *, label: str, cwd: Path, max_output: int) -> str:
    try:
        result = subprocess.run(
            list(command),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=str(cwd),
            env=_runtime_environment(),
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FencedFiWriterPreflightError(f"{label} could not be inspected") from exc
    if result.returncode != 0 or not isinstance(result.stdout, str):
        raise FencedFiWriterPreflightError(f"{label} inspection was rejected")
    if len(result.stdout.encode("utf-8")) > max_output:
        raise FencedFiWriterPreflightError(f"{label} inspection output is oversized")
    return result.stdout


def _compose_config(config: FencedFiWriterPreflightConfig) -> Mapping[str, Any]:
    raw = _run_read_only(
        [
            "/usr/bin/docker",
            "compose",
            "--project-directory",
            str(config.control_release_root),
            "--project-name",
            lease_agent.WA_FI_FENCED_WRITER_PROJECT_NAME,
            "--profile",
            lease_agent.WA_FI_FENCED_WRITER_PROFILE,
            "--env-file",
            str(config.runtime_env_file),
            "-f",
            str(config.compose_file),
            "config",
            "--format",
            "json",
        ],
        label="fenced FI Compose config",
        cwd=config.control_release_root,
        max_output=MAX_COMPOSE_BYTES,
    )
    try:
        payload = json.loads(raw, object_pairs_hook=_strict_object)
    except Exception as exc:
        raise FencedFiWriterPreflightError("fenced FI Compose config is not valid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("services"), dict):
        raise FencedFiWriterPreflightError("fenced FI Compose config has no services")
    return payload


def _environment_map(value: Any, *, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise FencedFiWriterPreflightError(f"{label} environment is invalid")
    return dict(value)


def _term_mount_is_valid(value: Any, config: FencedFiWriterPreflightConfig, *, label: str) -> bool:
    if not isinstance(value, list):
        raise FencedFiWriterPreflightError(f"{label} mounts are invalid")
    matching = [
        item
        for item in value
        if isinstance(item, Mapping)
        and item.get("Type", item.get("type")) == "bind"
        and item.get("Source", item.get("source")) == str(config.term_parent_directory)
        and item.get("Destination", item.get("target")) == TERM_PATH
    ]
    if len(matching) != 1:
        return False
    mount = matching[0]
    read_only = mount.get("RW") is False if "RW" in mount else mount.get("read_only") is True
    bind = mount.get("Bind", mount.get("bind"))
    if not read_only or not isinstance(bind, Mapping):
        return False
    create_host_path = bind.get("CreateHostPath", bind.get("create_host_path"))
    return create_host_path is False


def _application_settings_mount_is_valid(
    value: Any,
    config: FencedFiWriterPreflightConfig,
    *,
    label: str,
) -> bool:
    if not isinstance(value, list):
        raise FencedFiWriterPreflightError(f"{label} mounts are invalid")
    matching = [
        item
        for item in value
        if isinstance(item, Mapping)
        and item.get("Type", item.get("type")) == "bind"
        and item.get("Source", item.get("source"))
        == str(config.application_release_root / "trading_settings.json")
        and item.get("Destination", item.get("target")) == "/app/trading_settings.json"
    ]
    if len(matching) != 1:
        return False
    mount = matching[0]
    return mount.get("RW") is False if "RW" in mount else mount.get("read_only") is True


def _named_volume_mount_is_valid(
    value: Any,
    *,
    source: str,
    target: str,
    label: str,
) -> bool:
    if not isinstance(value, list):
        raise FencedFiWriterPreflightError(f"{label} mounts are invalid")
    matching = [
        item
        for item in value
        if isinstance(item, Mapping)
        and item.get("Type", item.get("type")) == "volume"
        and item.get("Source", item.get("source")) == source
        and item.get("Destination", item.get("target")) == target
    ]
    if len(matching) != 1:
        return False
    mount = matching[0]
    return mount.get("RW") is not False and mount.get("read_only") is not True


def _service_uses_only_runtime_network(rendered: Mapping[str, Any], *, label: str) -> bool:
    networks = rendered.get("networks")
    return isinstance(networks, Mapping) and set(networks) == {"runtime"}


def _expected_service_environment(service: str) -> dict[str, str]:
    expected = {
        "SERVER_MODE": "foreign",
        "RELEASE_SHA": lease_agent.WA_IR_APPLICATION_RELEASE_SHA,
        "SINGLE_WRITER_RUNTIME_ENABLED": "true",
        "APPLICATION_WRITER_TERM_ENFORCED": "true",
        "APPLICATION_WRITER_TERM_LOCAL_SITE": "webapp_fi",
        "APPLICATION_WRITER_TERM_LEASE_FILE": f"{TERM_PATH}/{TERM_LEASE_NAME}",
        "APPLICATION_WRITER_TERM_SAFETY_MARGIN_SECONDS": str(TERM_SAFETY_MARGIN_SECONDS),
        "APPLICATION_WRITER_TERM_MAX_LEASE_DURATION_SECONDS": str(TERM_MAX_LEASE_DURATION_SECONDS),
        "DATABASE_SCHEMA_BOOTSTRAP_ENABLED": "false",
        "TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH": "1",
        "TRADING_BOT_SERVICE": "api" if service == "app" else "bot",
    }
    if service == "app":
        expected["BACKGROUND_JOBS_ENABLED"] = "false"
    return expected


def _validate_rendered_service(
    rendered: Any,
    expectation: StaticServiceExpectation,
    config: FencedFiWriterPreflightConfig,
) -> None:
    if not isinstance(rendered, Mapping):
        raise FencedFiWriterPreflightError(
            f"{expectation.name} is missing from the fenced Compose runtime"
        )
    if (
        rendered.get("image") != expectation.image_ref
        or rendered.get("container_name") != expectation.container_name
        or rendered.get("restart") != "no"
        or rendered.get("pull_policy") != "never"
        or rendered.get("profiles") != [lease_agent.WA_FI_FENCED_WRITER_PROFILE]
        or "build" in rendered
    ):
        raise FencedFiWriterPreflightError(
            f"{expectation.name} rendered Compose identity is not the fixed fenced runtime"
        )
    environment = _environment_map(rendered.get("environment"), label=expectation.name)
    if any(environment.get(key) != value for key, value in _expected_service_environment(expectation.name).items()):
        raise FencedFiWriterPreflightError(
            f"{expectation.name} rendered Compose term environment is not pinned"
        )
    if not _term_mount_is_valid(rendered.get("volumes"), config, label=expectation.name):
        raise FencedFiWriterPreflightError(
            f"{expectation.name} rendered Compose term parent mount is not pinned read-only"
        )
    if not _application_settings_mount_is_valid(
        rendered.get("volumes"), config, label=expectation.name
    ):
        raise FencedFiWriterPreflightError(
            f"{expectation.name} rendered Compose settings mount is not pinned to the signed application root"
        )
    if not _service_uses_only_runtime_network(rendered, label=expectation.name):
        raise FencedFiWriterPreflightError(
            f"{expectation.name} rendered Compose network scope is not pinned"
        )
    if expectation.name == "app":
        if not _named_volume_mount_is_valid(
            rendered.get("volumes"),
            source="writer_uploads_data",
            target="/app/uploads",
            label=expectation.name,
        ) or not _named_volume_mount_is_valid(
            rendered.get("volumes"),
            source="writer_audit_data",
            target="/app/audit_trail",
            label=expectation.name,
        ):
            raise FencedFiWriterPreflightError(
                "app rendered Compose writable volumes are not pinned"
            )
        ports = rendered.get("ports")
        expected_port = {
            "host_ip": "127.0.0.1",
            "target": 8000,
            "published": str(config.app_local_port),
            "protocol": "tcp",
        }
        if (
            not isinstance(ports, list)
            or len(ports) != 1
            or not isinstance(ports[0], Mapping)
            or any(ports[0].get(key) != value for key, value in expected_port.items())
            or not isinstance(rendered.get("healthcheck"), Mapping)
        ):
            raise FencedFiWriterPreflightError(
                "app rendered Compose loopback port or healthcheck is not pinned"
            )
    elif "ports" in rendered:
        raise FencedFiWriterPreflightError("bot rendered Compose runtime must not expose a port")


def _validate_rendered_runtime(config: FencedFiWriterPreflightConfig) -> None:
    payload = _compose_config(config)
    services = payload["services"]
    if set(services) != set(FENCED_SERVICES):
        raise FencedFiWriterPreflightError("rendered Compose service scope is not exactly fenced app and bot")
    networks = payload.get("networks")
    if (
        not isinstance(networks, Mapping)
        or set(networks) != {"runtime"}
        or not isinstance(networks.get("runtime"), Mapping)
        or networks["runtime"].get("external") is not True
        or networks["runtime"].get("name") != config.runtime_resources.network_name
    ):
        raise FencedFiWriterPreflightError(
            "rendered Compose runtime network is not the reviewed external network"
        )
    volumes = payload.get("volumes")
    expected_volumes = {
        "writer_uploads_data": config.runtime_resources.uploads_volume,
        "writer_audit_data": config.runtime_resources.audit_volume,
    }
    if not isinstance(volumes, Mapping) or set(volumes) != set(expected_volumes):
        raise FencedFiWriterPreflightError(
            "rendered Compose writable volume scope is not pinned"
        )
    for logical_name, physical_name in expected_volumes.items():
        value = volumes.get(logical_name)
        if (
            not isinstance(value, Mapping)
            or value.get("external") is not True
            or value.get("name") != physical_name
        ):
            raise FencedFiWriterPreflightError(
                "rendered Compose writable volume does not match the reviewed resource"
            )
    for expectation in config.services:
        _validate_rendered_service(services.get(expectation.name), expectation, config)


def _inspect_image(config: FencedFiWriterPreflightConfig, expectation: StaticServiceExpectation) -> None:
    image_id = _run_read_only(
        ["/usr/bin/docker", "image", "inspect", "--format", "{{.Id}}", expectation.image_ref],
        label=f"{expectation.name} image",
        cwd=config.control_release_root,
        max_output=1024,
    ).strip().lower()
    if image_id != expectation.image_id:
        raise FencedFiWriterPreflightError(
            f"{expectation.name} image ID does not match its pinned identity"
        )
    raw_repo_digests = _run_read_only(
        [
            "/usr/bin/docker",
            "image",
            "inspect",
            "--format",
            "{{json .RepoDigests}}",
            expectation.image_ref,
        ],
        label=f"{expectation.name} immutable image repository digest",
        cwd=config.control_release_root,
        max_output=64 * 1024,
    )
    try:
        repo_digests = json.loads(raw_repo_digests, object_pairs_hook=_strict_object)
    except Exception as exc:
        raise FencedFiWriterPreflightError(
            f"{expectation.name} immutable image repository digest is invalid"
        ) from exc
    if (
        not isinstance(repo_digests, list)
        or not all(isinstance(item, str) for item in repo_digests)
        or expectation.image_repo_digest not in repo_digests
    ):
        raise FencedFiWriterPreflightError(
            f"{expectation.name} immutable image repository digest does not match its pinned identity"
        )


def _container_environment(value: Any, *, label: str) -> dict[str, str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and "=" in item for item in value):
        raise FencedFiWriterPreflightError(f"{label} container environment is invalid")
    result: dict[str, str] = {}
    for item in value:
        key, raw_value = item.split("=", 1)
        if not key or key in result:
            raise FencedFiWriterPreflightError(f"{label} container environment is invalid")
        result[key] = raw_value
    return result


def _inspect_container(config: FencedFiWriterPreflightConfig, expectation: RuntimeServiceExpectation) -> None:
    raw = _run_read_only(
        [
            "/usr/bin/docker",
            "container",
            "inspect",
            "--format",
            "{{json .}}",
            expectation.container_name,
        ],
        label=f"{expectation.name} container",
        cwd=config.control_release_root,
        max_output=MAX_CONTAINER_BYTES,
    )
    try:
        payload = json.loads(raw, object_pairs_hook=_strict_object)
    except Exception as exc:
        raise FencedFiWriterPreflightError(f"{expectation.name} container inspection is invalid") from exc
    if not isinstance(payload, Mapping):
        raise FencedFiWriterPreflightError(f"{expectation.name} container inspection is invalid")
    config_payload = payload.get("Config")
    state = payload.get("State")
    host_config = payload.get("HostConfig")
    mounts = payload.get("Mounts")
    if not isinstance(config_payload, Mapping) or not isinstance(state, Mapping) or not isinstance(host_config, Mapping):
        raise FencedFiWriterPreflightError(f"{expectation.name} container inspection is incomplete")
    labels = config_payload.get("Labels")
    restart = host_config.get("RestartPolicy")
    health = state.get("Health")
    health_status = health.get("Status") if isinstance(health, Mapping) else "none"
    if (
        payload.get("Id", "").lower() != expectation.container_id
        or str(payload.get("Image", "")).lower() != expectation.image_id
        or config_payload.get("Image") != expectation.image_ref
        or state.get("Running") is not True
        or state.get("Status") != "running"
        or not isinstance(restart, Mapping)
        or restart.get("Name") not in {"", "no"}
        # Both fenced services have an explicit healthcheck.  In particular,
        # the bot check proves a fresh, term-bound GetUpdates response rather
        # than merely a running process, so treating its health status as
        # ``none`` would make every valid guarded cutover fail closed.
        or health_status != "healthy"
        or not isinstance(labels, Mapping)
        or not all(isinstance(key, str) and isinstance(item, str) for key, item in labels.items())
        or labels.get("com.docker.compose.service") != expectation.name
        or labels.get("com.docker.compose.project") != config.compose_project
    ):
        raise FencedFiWriterPreflightError(
            f"{expectation.name} container does not match the pinned fenced runtime"
        )
    if lease_agent._runtime_binding_hash(dict(labels)) != expectation.labels_sha256:
        raise FencedFiWriterPreflightError(
            f"{expectation.name} container labels do not match the root-only runtime receipt"
        )
    environment = _container_environment(config_payload.get("Env"), label=expectation.name)
    if any(environment.get(key) != value for key, value in _expected_service_environment(expectation.name).items()):
        raise FencedFiWriterPreflightError(
            f"{expectation.name} container term environment does not match the pinned runtime"
        )
    if not _term_mount_is_valid(mounts, config, label=expectation.name):
        raise FencedFiWriterPreflightError(
            f"{expectation.name} container term parent mount does not match the pinned runtime"
        )
    port_bindings = host_config.get("PortBindings")
    if expectation.name == "app":
        expected_binding = [{"HostIp": "127.0.0.1", "HostPort": str(config.app_local_port)}]
        if port_bindings != {"8000/tcp": expected_binding}:
            raise FencedFiWriterPreflightError(
                "app container loopback binding does not match the pinned runtime"
            )
    elif port_bindings not in ({}, None):
        raise FencedFiWriterPreflightError("bot container must not expose a port")


def _validate_static_image_bindings(config: FencedFiWriterPreflightConfig) -> None:
    _validate_rendered_runtime(config)
    for expectation in config.services:
        _inspect_image(config, expectation)


def _validate_runtime_identity(
    config: FencedFiWriterPreflightConfig,
    runtime_services: Sequence[RuntimeServiceExpectation],
) -> None:
    if tuple(service.name for service in runtime_services) != FENCED_SERVICES:
        raise FencedFiWriterPreflightError(
            "fenced FI runtime receipt service scope is not exactly app and bot"
        )
    for expectation in runtime_services:
        _inspect_container(config, expectation)


def _validate_legacy_scope_is_disabled() -> None:
    try:
        lease_agent._assert_fenced_fi_legacy_scope_is_stopped()
    except lease_agent.ProductionWriterLeaseAgentError as exc:
        raise FencedFiWriterPreflightError(
            "legacy FI scope is not stopped with restart disabled"
        ) from exc


def _validate_fenced_runtime_scope_is_absent() -> None:
    """Require a clean create-only fenced scope before term acquisition."""

    try:
        lease_agent._assert_fenced_fi_runtime_scope_is_absent()
    except lease_agent.ProductionWriterLeaseAgentError as exc:
        raise FencedFiWriterPreflightError(
            "fenced FI runtime scope is not clean for a new cutover"
        ) from exc


def _validate_live_local_lease(
    config: FencedFiWriterPreflightConfig,
    agent_config: lease_agent.AgentConfig,
) -> None:
    try:
        lease = load_production_writer_lease(config.lease_file)
    except Exception as exc:
        raise FencedFiWriterPreflightError("local Writer Witness lease is not ready") from exc
    remaining = (lease.expires_at - datetime.now(timezone.utc)).total_seconds()
    if lease.holder_site != "webapp_fi" or remaining <= agent_config.witness.safety_margin_seconds:
        raise FencedFiWriterPreflightError("local Writer Witness lease is stale or belongs to another site")


def _validate_runtime_receipt(
    config: FencedFiWriterPreflightConfig,
) -> tuple[RuntimeServiceExpectation, ...]:
    """Require the post-health receipt before the guard is allowed to renew.

    The receipt is created by the fenced cutover agent only after both the app
    and bot have passed their health gates.  Guard-start therefore cannot be
    satisfied by a stale compose project or by a lease alone.
    """
    path = config.term_parent_directory / lease_agent.WA_FI_FENCED_RUNTIME_RECEIPT_NAME
    try:
        raw = _secure_read(path, label="fenced FI runtime receipt", private=True)
        value = json.loads(raw, object_pairs_hook=_strict_object)
    except FencedFiWriterPreflightError:
        raise
    except Exception as exc:
        raise FencedFiWriterPreflightError("fenced FI runtime receipt is invalid JSON") from exc
    if not isinstance(value, Mapping) or set(value) != lease_agent.WA_FI_FENCED_RUNTIME_RECEIPT_FIELDS:
        raise FencedFiWriterPreflightError("fenced FI runtime receipt schema is invalid")
    if (
        value.get("schema") != lease_agent.WA_FI_FENCED_RUNTIME_RECEIPT_SCHEMA
        or value.get("release_sha") != lease_agent.WA_IR_APPLICATION_RELEASE_SHA
        or value.get("compose_project") != config.compose_project
        or value.get("profile") != lease_agent.WA_FI_FENCED_WRITER_PROFILE
    ):
        raise FencedFiWriterPreflightError("fenced FI runtime receipt is not bound to this runtime")
    if (
        type(value.get("writer_epoch")) is not int
        or value["writer_epoch"] < 1
        or not isinstance(value.get("lease_id"), str)
        or not value["lease_id"]
        or value["lease_id"] != value["lease_id"].strip()
        or len(value["lease_id"]) > 128
        or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("witness_proof_sha256", "")).lower())
    ):
        raise FencedFiWriterPreflightError("fenced FI runtime receipt term binding is invalid")
    unsigned = {key: item for key, item in value.items() if key != "runtime_receipt_sha256"}
    expected_hash = lease_agent._runtime_binding_hash(unsigned)
    if value.get("runtime_receipt_sha256") != expected_hash:
        raise FencedFiWriterPreflightError("fenced FI runtime receipt hash is invalid")
    containers = value.get("containers")
    if not isinstance(containers, Mapping) or set(containers) != set(FENCED_SERVICES):
        raise FencedFiWriterPreflightError("fenced FI runtime receipt container scope is invalid")
    static_by_service = {service.name: service for service in config.services}
    runtime_services: list[RuntimeServiceExpectation] = []
    for service in FENCED_SERVICES:
        item = containers.get(service)
        if not isinstance(item, Mapping) or set(item) != lease_agent.WA_FI_FENCED_RUNTIME_CONTAINER_FIELDS:
            raise FencedFiWriterPreflightError(f"{service} fenced runtime receipt is invalid")
        static = static_by_service.get(service)
        if static is None:  # Defensive: _load_config pins this exact service set.
            raise FencedFiWriterPreflightError("fenced FI static service scope is invalid")
        container_id = str(item.get("container_id", "")).lower()
        image_id = str(item.get("image_id", "")).lower()
        labels_sha256 = str(item.get("labels_sha256", "")).lower()
        if (
            item.get("container_name") != static.container_name
            or item.get("image") != static.image_ref
            or image_id != static.image_id
            or item.get("restart_policy") != "no"
            or not CONTAINER_ID_RE.fullmatch(container_id)
            or not IMAGE_ID_RE.fullmatch(image_id)
            or not re.fullmatch(r"[0-9a-f]{64}", labels_sha256)
        ):
            raise FencedFiWriterPreflightError(
                f"{service} fenced runtime receipt does not match the static image binding"
            )
        runtime_services.append(
            RuntimeServiceExpectation(
                name=service,
                container_name=static.container_name,
                container_id=container_id,
                image_ref=static.image_ref,
                image_id=static.image_id,
                labels_sha256=labels_sha256,
            )
        )
    try:
        lease = load_production_writer_lease(config.lease_file)
    except Exception as exc:
        raise FencedFiWriterPreflightError("local Writer Witness lease is not ready") from exc
    if lease.writer_epoch != value.get("writer_epoch") or lease.lease_id != value.get("lease_id"):
        raise FencedFiWriterPreflightError("fenced FI runtime receipt does not match the local lease")
    return tuple(runtime_services)


def run(*, config_path: Path, phase: str) -> dict[str, Any]:
    config = _load_config(config_path)
    _validate_release_layout(config)
    identity = _validate_release_identity(config)
    _validate_release_trees(config, identity)
    _reject_unfenced_legacy_application_release(identity)
    _validate_installed_unit(config)
    agent_config = _validate_agent_config(config)
    _validate_runtime_environment_binding(config, agent_config)
    _validate_static_image_bindings(config)
    _validate_legacy_scope_is_disabled()
    if phase == "cutover-pre":
        _validate_fenced_runtime_scope_is_absent()
    # cutover-pre is intentionally static: it proves the exact compose and
    # image inputs before the controlled start, without requiring containers
    # that do not exist yet.  guard-start is post-health and requires both the
    # live container identities and the cutover receipt.
    if phase == "guard-start":
        _validate_live_local_lease(config, agent_config)
        runtime_services = _validate_runtime_receipt(config)
        _validate_runtime_identity(config, runtime_services)
        # Docker inspection can take long enough to consume a short term.
        # Re-read the same root-only lease as the final guard-start operation;
        # otherwise a term that was safe before inspection could be handed to
        # the renewal loop after it is already within its fencing margin.
        _validate_live_local_lease(config, agent_config)
    return {
        "status": "ready",
        "schema": PREFLIGHT_SCHEMA,
        "phase": phase,
        "services": [service.name for service in config.services],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--phase", choices=("cutover-pre", "stage", "guard-start"), default="stage")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(config_path=args.config, phase=args.phase)
    except FencedFiWriterPreflightError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_class": type(exc).__name__,
                    "reason": str(exc),
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
