#!/usr/bin/env python3
"""Read-only admission gate for an adopted WebApp-FI Writer Witness guard.

This tool is deliberately an *admission* check.  It never starts, stops,
recreates, pulls, builds, or removes a Docker resource and it never contacts
WebApp-IR.  The systemd unit runs it before the long-lived lease guard so a
guard cannot attach to a mutable release, an unexpected container, or a
Docker restart policy that could resurrect a writer without a live lease.

The operator supplies an owner-only expectation file created from a reviewed
local inventory.  Dynamic values are never learned and accepted by this tool:
the running Compose config, image IDs, and container IDs must all equal the
pre-recorded expectation.
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


PREFLIGHT_SCHEMA = "production-writer-lease-guard-preflight-v1"
RELEASE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
IMAGE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}(?::[A-Za-z0-9][A-Za-z0-9._-]{0,127})?$")
MAX_FILE_BYTES = 256 * 1024
MAX_COMPOSE_BYTES = 4 * 1024 * 1024
MANAGED_SERVICES = ("app", "sync_worker")
UNIT_NAME = "trading-bot-production-writer-fi-lease-guard.service"
APPROVED_UNIT_FILE = Path("/etc/systemd/system") / UNIT_NAME
UNIT_TEMPLATE_RELATIVE = Path(
    "deploy/systemd/trading-bot-production-writer-fi-lease-guard.service.template"
)
AGENT_SCRIPT_RELATIVE = Path("scripts/production_writer_lease_agent.py")
PREFLIGHT_SCRIPT_RELATIVE = Path("scripts/preflight_production_writer_lease_guard.py")


class WriterGuardPreflightError(RuntimeError):
    """The host has not proved it is safe to admit the writer guard."""


@dataclass(frozen=True)
class RuntimeServiceExpectation:
    name: str
    container_name: str
    container_id: str
    image_ref: str
    image_id: str


@dataclass(frozen=True)
class ReleaseFileExpectation:
    relative_path: Path
    sha256: str


@dataclass(frozen=True)
class WitnessTimingExpectation:
    lease_duration_seconds: int
    safety_margin_seconds: int
    renew_interval_seconds: int


@dataclass(frozen=True)
class WriterGuardPreflightConfig:
    release_sha: str
    release_root: Path
    agent_config: Path
    preflight_config: Path
    unit_file: Path
    lease_file: Path
    runtime_env_file: Path
    compose_file: Path
    compose_project: str
    witness_timing: WitnessTimingExpectation
    release_files: tuple[ReleaseFileExpectation, ...]
    services: tuple[RuntimeServiceExpectation, ...]


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WriterGuardPreflightError("preflight JSON contains duplicate keys")
        result[key] = value
    return result


def _absolute(value: Any, *, label: str) -> Path:
    text = str(value or "")
    path = Path(text)
    if not PATH_RE.fullmatch(text) or ".." in path.parts:
        raise WriterGuardPreflightError(f"{label} must be an absolute closed path")
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
        raise WriterGuardPreflightError(f"cannot securely open {label}") from exc
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
            raise WriterGuardPreflightError(f"{label} is not an owner-controlled regular file")
        payload = bytearray()
        while len(payload) <= max_size:
            chunk = os.read(descriptor, min(65536, max_size + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > max_size:
            raise WriterGuardPreflightError(f"{label} is oversized")
        after = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            raise WriterGuardPreflightError(f"{label} changed while being read")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _secure_text(path: Path, *, label: str, private: bool, max_size: int = MAX_FILE_BYTES) -> str:
    try:
        return _secure_read(path, label=label, private=private, max_size=max_size).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WriterGuardPreflightError(f"{label} is not UTF-8") from exc


def _check_owner_directory(path: Path, *, label: str) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise WriterGuardPreflightError(f"cannot inspect {label}") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise WriterGuardPreflightError(f"{label} is not an owner-controlled directory")


def _check_owner_file(path: Path, *, label: str, private: bool) -> None:
    _secure_read(path, label=label, private=private, max_size=MAX_FILE_BYTES)


def _require_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise WriterGuardPreflightError(f"{label} is invalid")
    return value


def _load_config(path: Path) -> WriterGuardPreflightConfig:
    if os.geteuid() != 0:
        raise WriterGuardPreflightError("writer guard preflight must run as root")
    safe_path = _absolute(str(path), label="preflight config")
    try:
        raw = json.loads(
            _secure_text(safe_path, label="preflight config", private=True),
            object_pairs_hook=_strict_object,
        )
    except WriterGuardPreflightError:
        raise
    except Exception as exc:
        raise WriterGuardPreflightError("preflight config is invalid JSON") from exc
    expected_fields = {
        "schema",
        "release_sha",
        "release_root",
        "agent_config",
        "preflight_config",
        "unit_file",
        "lease_file",
        "runtime_env_file",
        "witness_timing",
        "release_files",
        "runtime",
    }
    if not isinstance(raw, dict) or set(raw) != expected_fields or raw.get("schema") != PREFLIGHT_SCHEMA:
        raise WriterGuardPreflightError("preflight config schema is invalid")
    release_sha = str(raw.get("release_sha") or "").lower()
    if not RELEASE_SHA_RE.fullmatch(release_sha):
        raise WriterGuardPreflightError("preflight release SHA is invalid")
    release_root = _absolute(raw.get("release_root"), label="release root")
    if release_root.name != release_sha or release_root.parent.name != "releases":
        raise WriterGuardPreflightError("release root is not the immutable release path for this SHA")
    agent_config = _absolute(raw.get("agent_config"), label="writer guard agent config")
    preflight_config = _absolute(raw.get("preflight_config"), label="preflight config path")
    if preflight_config != safe_path:
        raise WriterGuardPreflightError("preflight config path does not bind this input file")
    unit_file = _absolute(raw.get("unit_file"), label="systemd unit file")
    if unit_file != APPROVED_UNIT_FILE:
        raise WriterGuardPreflightError("systemd unit file is not the approved WebApp-FI guard unit")
    lease_file = _absolute(raw.get("lease_file"), label="writer lease file")
    runtime_env_file = _absolute(raw.get("runtime_env_file"), label="runtime env file")

    witness_timing_raw = raw.get("witness_timing")
    if not isinstance(witness_timing_raw, dict) or set(witness_timing_raw) != {
        "lease_duration_seconds",
        "safety_margin_seconds",
        "renew_interval_seconds",
    }:
        raise WriterGuardPreflightError("intended Witness lease timing is invalid")
    duration = witness_timing_raw.get("lease_duration_seconds")
    margin = witness_timing_raw.get("safety_margin_seconds")
    interval = witness_timing_raw.get("renew_interval_seconds")
    if (
        type(duration) is not int
        or type(margin) is not int
        or type(interval) is not int
        or duration < 30
        or margin < 5
        or interval < 1
        or interval + margin >= duration
    ):
        raise WriterGuardPreflightError("intended Witness lease timing is unsafe")
    witness_timing = WitnessTimingExpectation(
        lease_duration_seconds=duration,
        safety_margin_seconds=margin,
        renew_interval_seconds=interval,
    )

    release_files_raw = raw.get("release_files")
    if not isinstance(release_files_raw, list) or len(release_files_raw) != 2:
        raise WriterGuardPreflightError("release file fingerprints are invalid")
    expected_release_paths = (Path("main.py"), Path("core/background_job_authority.py"))
    release_files: list[ReleaseFileExpectation] = []
    for expected_relative, item in zip(expected_release_paths, release_files_raw, strict=True):
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise WriterGuardPreflightError("release file fingerprint is invalid")
        relative_text = _require_text(item.get("path"), label="release file fingerprint path")
        relative_path = Path(relative_text)
        if relative_path != expected_relative or relative_path.is_absolute() or ".." in relative_path.parts:
            raise WriterGuardPreflightError("release file fingerprint path is not approved")
        digest = str(item.get("sha256") or "").lower()
        if not SHA256_RE.fullmatch(digest):
            raise WriterGuardPreflightError("release file fingerprint digest is invalid")
        release_files.append(ReleaseFileExpectation(relative_path=relative_path, sha256=digest))

    runtime = raw.get("runtime")
    if not isinstance(runtime, dict) or set(runtime) != {"compose_file", "compose_project", "services"}:
        raise WriterGuardPreflightError("preflight runtime config is invalid")
    compose_file = _absolute(runtime.get("compose_file"), label="runtime compose file")
    try:
        compose_file.relative_to(release_root)
    except ValueError as exc:
        raise WriterGuardPreflightError("runtime compose file is outside the immutable release") from exc
    compose_project = _require_text(runtime.get("compose_project"), label="runtime compose project")
    if not PROJECT_RE.fullmatch(compose_project):
        raise WriterGuardPreflightError("runtime compose project is invalid")
    services_raw = runtime.get("services")
    if not isinstance(services_raw, list) or len(services_raw) != len(MANAGED_SERVICES):
        raise WriterGuardPreflightError("preflight managed services are invalid")
    services: list[RuntimeServiceExpectation] = []
    for expected_name, item in zip(MANAGED_SERVICES, services_raw, strict=True):
        if not isinstance(item, dict) or set(item) != {
            "name",
            "container_name",
            "container_id",
            "image_ref",
            "image_id",
        }:
            raise WriterGuardPreflightError("preflight service expectation is invalid")
        name = _require_text(item.get("name"), label="managed service name")
        if name != expected_name:
            raise WriterGuardPreflightError("preflight service scope is not exactly app and sync_worker")
        container_name = _require_text(item.get("container_name"), label=f"{name} container name")
        if not NAME_RE.fullmatch(container_name):
            raise WriterGuardPreflightError(f"{name} container name is invalid")
        container_id = str(item.get("container_id") or "").lower()
        if not CONTAINER_ID_RE.fullmatch(container_id):
            raise WriterGuardPreflightError(f"{name} container ID is invalid")
        image_ref = _require_text(item.get("image_ref"), label=f"{name} image reference")
        if not IMAGE_REF_RE.fullmatch(image_ref):
            raise WriterGuardPreflightError(f"{name} image reference is invalid")
        image_id = str(item.get("image_id") or "").lower()
        if not IMAGE_ID_RE.fullmatch(image_id):
            raise WriterGuardPreflightError(f"{name} image ID is invalid")
        services.append(
            RuntimeServiceExpectation(
                name=name,
                container_name=container_name,
                container_id=container_id,
                image_ref=image_ref,
                image_id=image_id,
            )
        )
    if len({service.container_name for service in services}) != len(services):
        raise WriterGuardPreflightError("managed container names must be unique")
    return WriterGuardPreflightConfig(
        release_sha=release_sha,
        release_root=release_root,
        agent_config=agent_config,
        preflight_config=preflight_config,
        unit_file=unit_file,
        lease_file=lease_file,
        runtime_env_file=runtime_env_file,
        compose_file=compose_file,
        compose_project=compose_project,
        witness_timing=witness_timing,
        release_files=tuple(release_files),
        services=tuple(services),
    )


def _validate_release_layout(config: WriterGuardPreflightConfig) -> None:
    _check_owner_directory(config.release_root, label="immutable release root")
    for relative, label in (
        (AGENT_SCRIPT_RELATIVE, "writer lease agent"),
        (PREFLIGHT_SCRIPT_RELATIVE, "writer guard preflight helper"),
        (UNIT_TEMPLATE_RELATIVE, "writer guard unit template"),
    ):
        _check_owner_file(config.release_root / relative, label=label, private=False)
    _check_owner_file(config.compose_file, label="runtime compose file", private=False)
    _check_owner_file(config.runtime_env_file, label="runtime env file", private=True)
    for expectation in config.release_files:
        path = config.release_root / expectation.relative_path
        payload = _secure_read(path, label=f"release fingerprint {expectation.relative_path}", private=False)
        if hashlib.sha256(payload).hexdigest() != expectation.sha256:
            raise WriterGuardPreflightError(
                f"release fingerprint {expectation.relative_path} does not match the pinned release"
            )


def _render_expected_unit(config: WriterGuardPreflightConfig) -> bytes:
    template_path = config.release_root / UNIT_TEMPLATE_RELATIVE
    template = _secure_text(template_path, label="writer guard unit template", private=False)
    replacements = {
        "__RELEASE_ROOT__": (str(config.release_root), 4),
        "__PREFLIGHT_CONFIG__": (str(config.preflight_config), 1),
        "__AGENT_CONFIG__": (str(config.agent_config), 1),
    }
    for placeholder, (replacement, occurrences) in replacements.items():
        if template.count(placeholder) != occurrences:
            raise WriterGuardPreflightError("writer guard unit template placeholders are invalid")
        template = template.replace(placeholder, replacement)
    if "__" in template:
        raise WriterGuardPreflightError("writer guard unit template has an unresolved placeholder")
    return template.encode("utf-8")


def _validate_installed_unit(config: WriterGuardPreflightConfig) -> None:
    actual = _secure_read(config.unit_file, label="installed writer guard unit", private=False)
    expected = _render_expected_unit(config)
    if actual != expected:
        raise WriterGuardPreflightError("installed writer guard unit does not match the pinned template")


def _validate_agent_config(config: WriterGuardPreflightConfig) -> lease_agent.AgentConfig:
    try:
        agent_config = lease_agent._load_config(config.agent_config)
    except lease_agent.ProductionWriterLeaseAgentError as exc:
        raise WriterGuardPreflightError("writer guard agent config is not ready") from exc
    if (
        agent_config.mode != "writer"
        or agent_config.site != "webapp_fi"
        or agent_config.lease_file != config.lease_file
        or agent_config.runtime.compose_file != config.compose_file
        or agent_config.runtime.env_file != config.runtime_env_file
        or agent_config.runtime.selection_env_file is not None
        or agent_config.runtime.services != MANAGED_SERVICES
        or agent_config.witness.lease_duration_seconds
        != config.witness_timing.lease_duration_seconds
        or agent_config.witness.safety_margin_seconds
        != config.witness_timing.safety_margin_seconds
        or agent_config.witness.renew_interval_seconds
        != config.witness_timing.renew_interval_seconds
    ):
        raise WriterGuardPreflightError(
            "writer guard agent config does not bind this guarded runtime and intended Witness timing"
        )
    return agent_config


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
        raise WriterGuardPreflightError(f"{label} could not be inspected") from exc
    if result.returncode != 0 or not isinstance(result.stdout, str):
        raise WriterGuardPreflightError(f"{label} inspection was rejected")
    if len(result.stdout.encode("utf-8")) > max_output:
        raise WriterGuardPreflightError(f"{label} inspection output is oversized")
    return result.stdout


def _compose_config(config: WriterGuardPreflightConfig) -> Mapping[str, Any]:
    raw = _run_read_only(
        [
            "/usr/bin/docker",
            "compose",
            "--project-directory",
            str(config.release_root),
            "--project-name",
            config.compose_project,
            "--env-file",
            str(config.runtime_env_file),
            "-f",
            str(config.compose_file),
            "config",
            "--format",
            "json",
        ],
        label="runtime Compose config",
        cwd=config.release_root,
        max_output=MAX_COMPOSE_BYTES,
    )
    try:
        payload = json.loads(raw, object_pairs_hook=_strict_object)
    except Exception as exc:
        raise WriterGuardPreflightError("runtime Compose config is not valid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("services"), dict):
        raise WriterGuardPreflightError("runtime Compose config has no services")
    return payload


def _validate_rendered_runtime(config: WriterGuardPreflightConfig) -> None:
    payload = _compose_config(config)
    services = payload["services"]
    for expectation in config.services:
        rendered = services.get(expectation.name)
        if not isinstance(rendered, dict):
            raise WriterGuardPreflightError(f"{expectation.name} is missing from the guarded Compose runtime")
        if rendered.get("image") != expectation.image_ref:
            raise WriterGuardPreflightError(f"{expectation.name} Compose image does not match its pinned image")
        if rendered.get("container_name") != expectation.container_name:
            raise WriterGuardPreflightError(f"{expectation.name} Compose container name is not pinned")
        if rendered.get("restart") != "no":
            raise WriterGuardPreflightError(
                f"{expectation.name} Compose restart policy can resurrect an unfenced writer"
            )
        if rendered.get("pull_policy") != "never":
            raise WriterGuardPreflightError(f"{expectation.name} Compose pull policy is not disabled")
        if "build" in rendered:
            raise WriterGuardPreflightError(f"{expectation.name} Compose build is not allowed")


def _inspect_image(config: WriterGuardPreflightConfig, expectation: RuntimeServiceExpectation) -> None:
    image_id = _run_read_only(
        ["/usr/bin/docker", "image", "inspect", "--format", "{{.Id}}", expectation.image_ref],
        label=f"{expectation.name} image",
        cwd=config.release_root,
        max_output=1024,
    ).strip().lower()
    if image_id != expectation.image_id:
        raise WriterGuardPreflightError(f"{expectation.name} image ID does not match its pinned identity")


def _inspect_container(config: WriterGuardPreflightConfig, expectation: RuntimeServiceExpectation) -> None:
    format_value = (
        "{{.Id}}\\n{{.Image}}\\n{{.Config.Image}}\\n{{.State.Running}}\\n{{.State.Status}}"
        "\\n{{.HostConfig.RestartPolicy.Name}}\\n"
        "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}\\n"
        '{{index .Config.Labels "com.docker.compose.service"}}\\n'
        '{{index .Config.Labels "com.docker.compose.project"}}'
    )
    output = _run_read_only(
        [
            "/usr/bin/docker",
            "container",
            "inspect",
            "--format",
            format_value,
            expectation.container_name,
        ],
        label=f"{expectation.name} container",
        cwd=config.release_root,
        max_output=4096,
    )
    values = output.strip().splitlines()
    if len(values) != 9:
        raise WriterGuardPreflightError(f"{expectation.name} container inspection is malformed")
    (
        container_id,
        image_id,
        image_ref,
        running,
        status,
        restart_policy,
        health,
        compose_service,
        compose_project,
    ) = values
    expected_health = "healthy" if expectation.name == "app" else "none"
    if (
        container_id.lower() != expectation.container_id
        or image_id.lower() != expectation.image_id
        or image_ref != expectation.image_ref
        or running != "true"
        or status != "running"
        or restart_policy not in {"", "no"}
        or health != expected_health
        or compose_service != expectation.name
        or compose_project != config.compose_project
    ):
        raise WriterGuardPreflightError(
            f"{expectation.name} container does not match the pinned guarded runtime"
        )


def _validate_runtime_identity(config: WriterGuardPreflightConfig) -> None:
    _validate_rendered_runtime(config)
    for expectation in config.services:
        _inspect_image(config, expectation)
        _inspect_container(config, expectation)


def _validate_live_local_lease(
    config: WriterGuardPreflightConfig,
    agent_config: lease_agent.AgentConfig,
) -> None:
    try:
        lease = load_production_writer_lease(config.lease_file)
    except Exception as exc:
        raise WriterGuardPreflightError("local Writer Witness lease is not ready") from exc
    remaining = (lease.expires_at - datetime.now(timezone.utc)).total_seconds()
    if lease.holder_site != "webapp_fi" or remaining <= agent_config.witness.safety_margin_seconds:
        raise WriterGuardPreflightError("local Writer Witness lease is stale or belongs to another site")


def run(*, config_path: Path, phase: str) -> dict[str, Any]:
    config = _load_config(config_path)
    _validate_release_layout(config)
    _validate_installed_unit(config)
    agent_config = _validate_agent_config(config)
    _validate_runtime_identity(config)
    if phase == "guard-start":
        _validate_live_local_lease(config, agent_config)
    return {
        "status": "ready",
        "schema": PREFLIGHT_SCHEMA,
        "phase": phase,
        "release_sha": config.release_sha,
        "services": [service.name for service in config.services],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--phase", choices=("stage", "guard-start"), default="stage")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(config_path=args.config, phase=args.phase)
    except WriterGuardPreflightError as exc:
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
