#!/usr/bin/env python3
"""Rotate one installed Witness approval session without touching runtime state."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

from core.canonical_json import canonical_json_bytes
from core.human_approval import (
    SESSION_TOKEN_SCHEMA,
    approval_subject,
    staging_session_scope_sha256,
    verify_human_approval,
)
from core.human_approval_issuer import DEFAULT_STAGING_SESSION_ACTIONS
from scripts.build_three_site_staging_witness_relay_material import (
    ACTIVE_DIRECTORY_NAME,
    ARCHIVE_DIRECTORY_NAME,
    JOURNAL_DIRECTORY_NAME,
    POLICY_NAME,
    SESSION_NAME,
    WitnessRelayMaterialError,
    _manifest_bytes,
    _sha256,
    _strict_json_bytes,
    assert_root_controlled_ancestors,
    read_exact_material_file,
)
from scripts.render_three_site_staging_role_compose import (
    canonical_role_env_bytes,
    parse_env_values,
    referenced_environment_names,
)


ROTATION_SCHEMA = "three-site-staging-witness-relay-rotation-v1"
RELAY_CONTAINER_DIRECTORY = "/run/human-approval"
RELAY_DIRECTORY_BIND = (
    "${STAGING_HUMAN_APPROVAL_RELAY_MATERIAL_DIR:-/dev/null}:"
    f"{RELAY_CONTAINER_DIRECTORY}:ro"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
REVISION_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{7,79}$")
OPERATION_RE = re.compile(r"^[0-9a-f]{32}$")
ARCHIVE_NAME_RE = re.compile(r"^session-[0-9a-f]{64}\.json$")
TEMPORARY_NAME_RE = re.compile(r"^\.session-[0-9a-f]{32}\.tmp$")
JOURNAL_NAME_RE = re.compile(r"^[0-9a-f]{32}\.json$")
JOURNAL_PHASES = ("prepared", "archived", "staged", "activated", "complete")
CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
CRASH_POINTS = frozenset(
    {
        "journal-prepared",
        "archive-written",
        "temp-written",
        "session-replaced",
        "journal-complete",
    }
)


class WitnessRelayRotationError(RuntimeError):
    """A local relay-session rotation cannot be proven safe."""


class InjectedRotationCrash(WitnessRelayRotationError):
    """A deterministic test crash occurred after a durable mutation."""


def _crash(crash_after: str | None, checkpoint: str) -> None:
    if crash_after == checkpoint:
        raise InjectedRotationCrash(f"injected crash after {checkpoint}")


def _inspect_runtime_container(
    *,
    container_id: str,
    material_directory: Path,
    values: dict[str, str],
    expected_project: str,
) -> dict[str, Any]:
    if CONTAINER_ID_RE.fullmatch(container_id) is None:
        raise WitnessRelayRotationError("Witness container identifier is invalid")
    docker = Path("/usr/bin/docker")
    try:
        metadata = docker.lstat()
    except OSError as exc:
        raise WitnessRelayRotationError(
            "fixed Docker inspection binary is unavailable"
        ) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(docker, os.X_OK)
    ):
        raise WitnessRelayRotationError(
            "fixed Docker inspection binary is not trusted"
        )
    try:
        completed = subprocess.run(
            [str(docker), "inspect", "--type", "container", container_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=15,
            check=False,
            cwd="/",
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            close_fds=True,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise WitnessRelayRotationError(
            "read-only Witness container inspection failed"
        ) from exc
    if (
        completed.returncode != 0
        or len(completed.stdout.encode("utf-8")) > 2 * 1024 * 1024
    ):
        raise WitnessRelayRotationError(
            "read-only Witness container inspection was not successful"
        )
    try:
        documents = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise WitnessRelayRotationError(
            "read-only Witness container inspection is invalid"
        ) from exc
    if (
        not isinstance(documents, list)
        or len(documents) != 1
        or not isinstance(documents[0], dict)
    ):
        raise WitnessRelayRotationError(
            "read-only Witness container inspection is not singular"
        )
    document = documents[0]
    state = document.get("State")
    config = document.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    environment = config.get("Env") if isinstance(config, dict) else None
    mounts = document.get("Mounts")
    if (
        document.get("Id") != container_id
        or not isinstance(state, dict)
        or state.get("Running") is not True
        or state.get("Restarting") is True
        or not isinstance(state.get("StartedAt"), str)
        or not state["StartedAt"]
        or type(document.get("RestartCount")) is not int
        or document["RestartCount"] < 0
        or not isinstance(labels, dict)
        or labels.get("com.docker.compose.service") != "witness_api"
        or labels.get("com.docker.compose.project") != expected_project
        or not isinstance(environment, list)
        or not isinstance(mounts, list)
    ):
        raise WitnessRelayRotationError(
            "running Witness container identity/state/labels are invalid"
        )
    parsed_environment: dict[str, str] = {}
    for item in environment:
        if not isinstance(item, str):
            raise WitnessRelayRotationError(
                "running Witness container environment is invalid"
            )
        name, separator, value = item.partition("=")
        if not separator or not name or name in parsed_environment:
            raise WitnessRelayRotationError(
                "running Witness container environment is invalid"
            )
        parsed_environment[name] = value
    expected_runtime_environment = {
        "WRITER_WITNESS_RELEASE_SHA": values["STAGING_RELEASE_SHA"],
        "HUMAN_APPROVAL_RELAY_SESSION_FILE": (
            f"{RELAY_CONTAINER_DIRECTORY}/{SESSION_NAME}"
        ),
        "HUMAN_APPROVAL_RELAY_POLICY_FILE": (
            f"{RELAY_CONTAINER_DIRECTORY}/{POLICY_NAME}"
        ),
        "HUMAN_APPROVAL_RELAY_ENABLED": "true",
        "HUMAN_APPROVAL_RELAY_ORCHESTRATOR_KEY_ID": values[
            "STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_KEY_ID"
        ],
        "HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET": values[
            "STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET"
        ],
    }
    if any(
        parsed_environment.get(name) != value
        for name, value in expected_runtime_environment.items()
    ):
        raise WitnessRelayRotationError(
            "running Witness container relay/release environment is invalid"
        )
    relay_mounts = [
        mount
        for mount in mounts
        if isinstance(mount, dict)
        and (
            mount.get("Destination") == RELAY_CONTAINER_DIRECTORY
            or str(mount.get("Destination", "")).startswith(
                f"{RELAY_CONTAINER_DIRECTORY}/"
            )
        )
    ]
    try:
        expected_source = material_directory.resolve(strict=True)
    except OSError as exc:
        raise WitnessRelayRotationError(
            "relay material directory cannot be resolved for runtime inspection"
        ) from exc
    if len(relay_mounts) != 1:
        raise WitnessRelayRotationError(
            "running Witness container has legacy/duplicate relay mounts"
        )
    relay_mount = relay_mounts[0]
    source = Path(str(relay_mount.get("Source", "")))
    try:
        resolved_source = source.resolve(strict=True)
    except OSError as exc:
        raise WitnessRelayRotationError(
            "running Witness relay mount source is unavailable"
        ) from exc
    if (
        relay_mount.get("Type") != "bind"
        or relay_mount.get("Destination") != RELAY_CONTAINER_DIRECTORY
        or relay_mount.get("RW") is not False
        or not source.is_absolute()
        or str(source) != os.path.normpath(str(source))
        or resolved_source != source
        or resolved_source != expected_source
    ):
        raise WitnessRelayRotationError(
            "running Witness container lacks the exact read-only relay directory bind"
        )
    return {
        "container_id": container_id,
        "started_at": state["StartedAt"],
        "restart_count": document["RestartCount"],
        "compose_project": labels["com.docker.compose.project"],
    }


def _assert_root_directory(path: Path, *, label: str) -> None:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise WitnessRelayRotationError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or resolved != path
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise WitnessRelayRotationError(
            f"{label} must be a root-owned non-symlink mode-0700 directory"
        )


def _assert_pinned_directory(path: Path, descriptor: int, *, label: str) -> None:
    _assert_root_directory(path, label=label)
    pathname = path.lstat()
    pinned = os.fstat(descriptor)
    if (pathname.st_dev, pathname.st_ino) != (pinned.st_dev, pinned.st_ino):
        raise WitnessRelayRotationError(f"{label} changed after it was pinned")


def _read_canonical_json(path: Path, *, label: str) -> tuple[bytes, dict[str, Any]]:
    payload = read_exact_material_file(
        path,
        expected_mode=0o600,
        label=label,
    )
    parsed = _strict_json_bytes(payload, label=label)
    if payload != _manifest_bytes(parsed):
        raise WitnessRelayRotationError(f"{label} bytes are not canonical")
    return payload, parsed


def _read_root_file_relaxed(
    path: Path,
    *,
    label: str,
    allowed_links: frozenset[int] = frozenset({1}),
    max_size: int = 1024 * 1024,
) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise WitnessRelayRotationError(f"cannot securely open {label}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink not in allowed_links
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size < 0
            or before.st_size > max_size
        ):
            raise WitnessRelayRotationError(f"{label} metadata is unsafe")
        chunks: list[bytes] = []
        remaining = max_size + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
        )
        if (
            len(payload) > max_size
            or any(getattr(before, field) != getattr(after, field) for field in stable)
        ):
            raise WitnessRelayRotationError(f"{label} changed while it was read")
        return payload, after
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unlink_recoverable_file(
    path: Path,
    *,
    label: str,
    allowed_links: frozenset[int] = frozenset({1, 2}),
) -> None:
    _read_root_file_relaxed(
        path,
        label=label,
        allowed_links=allowed_links,
    )
    path.unlink()
    _fsync_directory(path.parent)


def _write_direct_new(path: Path, payload: bytes, *, label: str) -> None:
    if not payload or len(payload) > 1024 * 1024:
        raise WitnessRelayRotationError(f"{label} payload is invalid")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags, 0o600)
        view = memoryview(payload)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise WitnessRelayRotationError(f"{label} write made no progress")
            written += count
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _fsync_directory(path.parent)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            _unlink_recoverable_file(path, label=f"incomplete {label}")
        except (FileNotFoundError, WitnessRelayRotationError):
            pass
        raise


def _recoverable_create_exclusive(
    path: Path,
    payload: bytes,
    *,
    label: str,
) -> None:
    """Create one durable target and reconcile its deterministic link sidecar."""

    _assert_root_directory(path.parent, label=f"{label} directory")
    sidecar = path.parent / f".{path.name}.creating"
    target_exists = path.exists() or path.is_symlink()
    sidecar_exists = sidecar.exists() or sidecar.is_symlink()
    if target_exists:
        target_payload, target_metadata = _read_root_file_relaxed(
            path,
            label=label,
            allowed_links=frozenset({1, 2}),
        )
        if target_payload != payload:
            raise WitnessRelayRotationError(f"{label} differs from durable intent")
        if sidecar_exists:
            sidecar_payload, sidecar_metadata = _read_root_file_relaxed(
                sidecar,
                label=f"{label} creation sidecar",
                allowed_links=frozenset({2}),
            )
            if (
                sidecar_payload != payload
                or sidecar_metadata.st_dev != target_metadata.st_dev
                or sidecar_metadata.st_ino != target_metadata.st_ino
            ):
                raise WitnessRelayRotationError(
                    f"{label} creation sidecar is inconsistent"
                )
            sidecar.unlink()
            _fsync_directory(path.parent)
        elif target_metadata.st_nlink != 1:
            raise WitnessRelayRotationError(f"{label} has an unexplained hard link")
        read_exact_material_file(path, expected_mode=0o600, label=label)
        return

    if sidecar_exists:
        sidecar_payload, sidecar_metadata = _read_root_file_relaxed(
            sidecar,
            label=f"{label} creation sidecar",
        )
        if sidecar_payload != payload:
            if sidecar_metadata.st_nlink != 1:
                raise WitnessRelayRotationError(
                    f"{label} partial creation sidecar has an unsafe link count"
                )
            sidecar.unlink()
            _fsync_directory(path.parent)
            sidecar_exists = False
    if not sidecar_exists:
        _write_direct_new(
            sidecar,
            payload,
            label=f"{label} creation sidecar",
        )
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(path.parent, directory_flags)
    try:
        try:
            os.link(
                sidecar.name,
                path.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            pass
        os.fsync(directory_fd)
        target_payload, target_metadata = _read_root_file_relaxed(
            path,
            label=label,
            allowed_links=frozenset({2}),
        )
        sidecar_payload, sidecar_metadata = _read_root_file_relaxed(
            sidecar,
            label=f"{label} creation sidecar",
            allowed_links=frozenset({2}),
        )
        if (
            target_payload != payload
            or sidecar_payload != payload
            or target_metadata.st_dev != sidecar_metadata.st_dev
            or target_metadata.st_ino != sidecar_metadata.st_ino
        ):
            raise WitnessRelayRotationError(
                f"{label} create-exclusive publication is inconsistent"
            )
        os.unlink(sidecar.name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    read_exact_material_file(path, expected_mode=0o600, label=label)


def _finish_published_create_sidecar(path: Path, *, label: str) -> None:
    sidecar = path.parent / f".{path.name}.creating"
    if (
        (path.exists() or path.is_symlink())
        and (sidecar.exists() or sidecar.is_symlink())
    ):
        payload, _metadata = _read_root_file_relaxed(
            path,
            label=label,
            allowed_links=frozenset({2}),
        )
        _recoverable_create_exclusive(path, payload, label=label)


def _recoverable_stage_file(
    path: Path,
    payload: bytes,
    *,
    durable_phase: str,
) -> None:
    if path.exists() or path.is_symlink():
        staged_payload, metadata = _read_root_file_relaxed(
            path,
            label="staged relay session",
        )
        if staged_payload == payload:
            return
        if durable_phase == "staged" or metadata.st_nlink != 1:
            raise WitnessRelayRotationError(
                "staged relay session differs after its durable phase"
            )
        path.unlink()
        _fsync_directory(path.parent)
    _write_direct_new(path, payload, label="staged relay session")


def _recoverable_atomic_replace(
    path: Path,
    payload: bytes,
    *,
    operation_id: str,
    label: str,
) -> None:
    _assert_root_directory(path.parent, label=f"{label} directory")
    sidecar = path.parent / f".{path.name}.{operation_id}.update"
    if sidecar.exists() or sidecar.is_symlink():
        sidecar_payload, metadata = _read_root_file_relaxed(
            sidecar,
            label=f"{label} update sidecar",
        )
        if sidecar_payload != payload:
            if metadata.st_nlink != 1:
                raise WitnessRelayRotationError(
                    f"{label} update sidecar has an unsafe link count"
                )
            sidecar.unlink()
            _fsync_directory(path.parent)
            _write_direct_new(
                sidecar,
                payload,
                label=f"{label} update sidecar",
            )
    else:
        _write_direct_new(
            sidecar,
            payload,
            label=f"{label} update sidecar",
        )
    if path.exists() or path.is_symlink():
        _read_root_file_relaxed(path, label=label)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(path.parent, directory_flags)
    try:
        os.replace(
            sidecar.name,
            path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    if read_exact_material_file(path, expected_mode=0o600, label=label) != payload:
        raise WitnessRelayRotationError(f"{label} failed its atomic read-back")


def _prove_directory_bind(
    compose_bytes: bytes,
    env_bytes: bytes,
) -> tuple[dict[str, str], Path, str]:
    try:
        compose = yaml.safe_load(compose_bytes.decode("utf-8"))
        values = parse_env_values(env_bytes.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError, ValueError) as exc:
        raise WitnessRelayRotationError("Witness Compose/environment is invalid") from exc
    services = compose.get("services") if isinstance(compose, dict) else None
    witness = services.get("witness_api") if isinstance(services, dict) else None
    if not isinstance(witness, dict):
        raise WitnessRelayRotationError("rendered Witness Compose lacks witness_api")
    environment = witness.get("environment")
    volumes = witness.get("volumes")
    if (
        not isinstance(environment, dict)
        or environment.get("HUMAN_APPROVAL_RELAY_SESSION_FILE")
        != f"{RELAY_CONTAINER_DIRECTORY}/{SESSION_NAME}"
        or environment.get("HUMAN_APPROVAL_RELAY_POLICY_FILE")
        != f"{RELAY_CONTAINER_DIRECTORY}/{POLICY_NAME}"
    ):
        raise WitnessRelayRotationError(
            "Witness Compose changed the fixed in-container relay paths"
        )
    if not isinstance(volumes, list):
        raise WitnessRelayRotationError("Witness Compose relay volumes are invalid")
    relay_mounts = [
        value
        for value in volumes
        if isinstance(value, str)
        and (
            f":{RELAY_CONTAINER_DIRECTORY}:" in value
            or f":{RELAY_CONTAINER_DIRECTORY}/" in value
        )
    ]
    serialized = json.dumps(witness, sort_keys=True)
    if (
        relay_mounts != [RELAY_DIRECTORY_BIND]
        or "STAGING_HUMAN_APPROVAL_RELAY_SESSION_FILE" in serialized
        or "STAGING_HUMAN_APPROVAL_RELAY_POLICY_FILE" in serialized
    ):
        raise WitnessRelayRotationError(
            "Witness Compose is not the exact read-only relay directory bind"
        )
    required_names = referenced_environment_names(compose)
    if (
        set(values) != set(required_names)
        or env_bytes != canonical_role_env_bytes(values, required_names=required_names)
    ):
        raise WitnessRelayRotationError(
            "Witness environment is not the exact canonical closed variable set"
        )
    expected_project = f"{values.get('STAGING_STORAGE_NAMESPACE', '')}-witness"
    if compose.get("name") != expected_project:
        raise WitnessRelayRotationError(
            "Witness Compose project name differs from its role namespace"
        )
    material_value = values.get("STAGING_HUMAN_APPROVAL_RELAY_MATERIAL_DIR", "")
    material = Path(material_value)
    if (
        values.get("STAGING_HUMAN_APPROVAL_RELAY_ENABLED") != "true"
        or not values.get("STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_KEY_ID")
        or len(
            values.get(
                "STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET", ""
            ).encode("utf-8")
        )
        < 32
        or not material.is_absolute()
        or ".." in material.parts
        or str(material) != os.path.normpath(str(material))
        or material.name != ACTIVE_DIRECTORY_NAME
        or REVISION_RE.fullmatch(material.parent.name) is None
        or material.parent.parent.name != "material-revisions"
    ):
        raise WitnessRelayRotationError(
            "Witness relay environment is not an enabled revision-bound directory"
        )
    release = values.get("STAGING_RELEASE_SHA", "").lower()
    source_root = values.get("STAGING_SOURCE_ROOT", "")
    if (
        RELEASE_RE.fullmatch(release) is None
        or source_root != f"/srv/trading-bot-three-site/releases/{release}"
    ):
        raise WitnessRelayRotationError(
            "Witness relay environment is not bound to the immutable release"
        )
    return values, material, expected_project


def _session_probe_subject(
    session: dict[str, Any],
    *,
    release_sha: str,
) -> dict[str, Any]:
    return approval_subject(
        artifact_type=SESSION_TOKEN_SCHEMA,
        artifact_sha256=_sha256(
            canonical_json_bytes(
                {
                    "release_sha": release_sha,
                    "allowed_actions": session.get("allowed_actions"),
                }
            )
        ),
        release_sha=release_sha,
        bindings={},
    )


def _verify_session(
    session: dict[str, Any],
    *,
    policy: dict[str, Any],
    release_sha: str,
    now: datetime | None,
    require_fresh: bool,
) -> dict[str, str]:
    actions = list(DEFAULT_STAGING_SESSION_ACTIONS)
    if (
        session.get("schema") != SESSION_TOKEN_SCHEMA
        or session.get("release_sha") != release_sha
        or session.get("allowed_actions") != actions
    ):
        raise WitnessRelayRotationError(
            "relay session is not the exact release-bound live matrix scope"
        )
    probe = _session_probe_subject(session, release_sha=release_sha)
    verified = []
    for action in actions:
        verified.append(
            verify_human_approval(
                session,
                policy_payload=policy,
                expected_action=action,
                expected_environment="staging",
                expected_subject=probe,
                now=now,
                require_fresh=require_fresh,
                allow_session=True,
            )
        )
    identities = {
        (item.approval_id, item.expires_at.isoformat(), item.token_hash)
        for item in verified
    }
    if len(identities) != 1:
        raise WitnessRelayRotationError(
            "relay session verification changed across required actions"
        )
    approval_id, expires_at, token_hash = next(iter(identities))
    return {
        "approval_id": approval_id,
        "issued_at": str(session["issued_at"]),
        "expires_at": expires_at,
        "session_token_sha256": token_hash,
        "session_scope_sha256": staging_session_scope_sha256(
            release_sha=release_sha,
            allowed_actions=actions,
        ),
    }


def _operation_id(revision_id: str, new_session_sha256: str) -> str:
    return hashlib.sha256(
        f"{revision_id}\0{new_session_sha256}".encode("utf-8")
    ).hexdigest()[:32]


def _assert_newer_session(
    *,
    old_result: dict[str, str],
    new_result: dict[str, str],
) -> None:
    if (
        old_result["approval_id"] == new_result["approval_id"]
        or old_result["session_token_sha256"]
        == new_result["session_token_sha256"]
    ):
        raise WitnessRelayRotationError(
            "replacement relay session reuses the archived session identity"
        )
    try:
        old_issued = datetime.fromisoformat(
            old_result["issued_at"].replace("Z", "+00:00")
        )
        new_issued = datetime.fromisoformat(
            new_result["issued_at"].replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise WitnessRelayRotationError(
            "relay session issuance ordering is invalid"
        ) from exc
    if (
        old_issued.tzinfo is None
        or new_issued.tzinfo is None
        or new_issued <= old_issued
    ):
        raise WitnessRelayRotationError(
            "replacement relay session is not newer than the archived session"
        )


def _assert_control_directory_entries(
    directory: Path,
    *,
    name_pattern: re.Pattern[str],
    label: str,
    allowed_names: frozenset[str] = frozenset(),
) -> None:
    for entry in directory.iterdir():
        try:
            metadata = entry.lstat()
        except OSError as exc:
            raise WitnessRelayRotationError(f"{label} entry is unavailable") from exc
        allowed_links = {1, 2} if entry.name in allowed_names else {1}
        if (
            (
                name_pattern.fullmatch(entry.name) is None
                and entry.name not in allowed_names
            )
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink not in allowed_links
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise WitnessRelayRotationError(
                f"{label} contains an unsafe or unexpected entry"
            )


def _journal_bytes(payload: dict[str, Any]) -> bytes:
    return _manifest_bytes(payload)


def _read_journal(path: Path) -> dict[str, Any]:
    _payload, journal = _read_canonical_json(path, label="relay rotation journal")
    return _validate_journal_payload(journal)


def _read_unpublished_journal_sidecar(path: Path) -> dict[str, Any] | None:
    """Return only a complete canonical admission; partial writes are residue."""

    payload, _metadata = _read_root_file_relaxed(
        path,
        label="relay rotation journal creation sidecar",
    )
    try:
        journal = _strict_json_bytes(
            payload,
            label="relay rotation journal creation sidecar",
        )
        if payload != _journal_bytes(journal):
            raise WitnessRelayRotationError(
                "relay rotation journal creation sidecar bytes are not canonical"
            )
        return _validate_journal_payload(journal)
    except (WitnessRelayMaterialError, WitnessRelayRotationError):
        return None


def _validate_journal_payload(journal: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "schema",
        "operation_id",
        "phase",
        "created_at",
        "revision_id",
        "release_sha",
        "policy_sha256",
        "session_scope_sha256",
        "old",
        "new",
        "archive_name",
        "temporary_name",
        "runtime",
    }
    if (
        set(journal) != expected
        or journal.get("schema") != ROTATION_SCHEMA
        or journal.get("phase") not in JOURNAL_PHASES
        or OPERATION_RE.fullmatch(str(journal.get("operation_id", ""))) is None
        or SHA256_RE.fullmatch(str(journal.get("policy_sha256", ""))) is None
        or SHA256_RE.fullmatch(
            str(journal.get("session_scope_sha256", ""))
        )
        is None
        or not isinstance(journal.get("old"), dict)
        or set(journal["old"]) != {"approval_id", "session_sha256"}
        or not isinstance(journal.get("new"), dict)
        or set(journal["new"]) != {"approval_id", "expires_at", "session_sha256"}
        or not isinstance(journal.get("runtime"), dict)
        or set(journal["runtime"])
        != {
            "container_id",
            "started_at",
            "restart_count",
            "compose_project",
        }
        or SHA256_RE.fullmatch(str(journal["old"].get("session_sha256", ""))) is None
        or SHA256_RE.fullmatch(str(journal["new"].get("session_sha256", ""))) is None
        or ARCHIVE_NAME_RE.fullmatch(str(journal.get("archive_name", ""))) is None
        or TEMPORARY_NAME_RE.fullmatch(
            str(journal.get("temporary_name", ""))
        )
        is None
        or CONTAINER_ID_RE.fullmatch(
            str(journal["runtime"].get("container_id", ""))
        )
        is None
        or not isinstance(journal["runtime"].get("started_at"), str)
        or not journal["runtime"]["started_at"]
        or type(journal["runtime"].get("restart_count")) is not int
        or journal["runtime"]["restart_count"] < 0
        or not isinstance(journal["runtime"].get("compose_project"), str)
        or not journal["runtime"]["compose_project"]
    ):
        raise WitnessRelayRotationError("relay rotation journal is invalid")
    try:
        UUID(str(journal["old"]["approval_id"]))
        UUID(str(journal["new"]["approval_id"]))
        created = datetime.fromisoformat(
            str(journal["created_at"]).replace("Z", "+00:00")
        )
        expires = datetime.fromisoformat(
            str(journal["new"]["expires_at"]).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise WitnessRelayRotationError(
            "relay rotation journal identity/time is invalid"
        ) from exc
    if (
        created.tzinfo is None
        or expires.tzinfo is None
        or RELEASE_RE.fullmatch(str(journal["release_sha"])) is None
        or REVISION_RE.fullmatch(str(journal["revision_id"])) is None
    ):
        raise WitnessRelayRotationError(
            "relay rotation journal release/time binding is invalid"
        )
    return journal


def _set_phase(path: Path, journal: dict[str, Any], phase: str) -> None:
    if phase not in JOURNAL_PHASES:
        raise WitnessRelayRotationError("relay rotation phase is invalid")
    journal["phase"] = phase
    _recoverable_atomic_replace(
        path,
        _journal_bytes(journal),
        operation_id=str(journal["operation_id"]),
        label="relay rotation journal",
    )


def _replace_active_session(
    *,
    active: Path,
    temporary_name: str,
) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(active, flags)
    try:
        metadata = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise WitnessRelayRotationError("relay active directory changed")
        os.replace(
            temporary_name,
            SESSION_NAME,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def rotate_witness_relay_session(
    *,
    role_compose_path: Path,
    env_file_path: Path,
    new_session_path: Path,
    expected_policy_sha256: str,
    container_id: str,
    now: datetime | None = None,
    crash_after: str | None = None,
) -> dict[str, Any]:
    """Rotate one session in place, or reconcile the same durable operation."""

    if os.geteuid() != 0:
        raise WitnessRelayRotationError("Witness relay rotation must run as root")
    if crash_after is not None and crash_after not in CRASH_POINTS:
        raise WitnessRelayRotationError("rotation crash checkpoint is invalid")
    if SHA256_RE.fullmatch(str(expected_policy_sha256)) is None:
        raise WitnessRelayRotationError("expected relay policy hash is invalid")
    if CONTAINER_ID_RE.fullmatch(str(container_id)) is None:
        raise WitnessRelayRotationError("Witness container identifier is invalid")
    assert_root_controlled_ancestors(
        role_compose_path.parent,
        label="installed Witness Compose parent",
    )
    assert_root_controlled_ancestors(
        env_file_path.parent,
        label="installed Witness environment parent",
    )
    compose_bytes = read_exact_material_file(
        role_compose_path,
        expected_mode=0o640,
        label="installed Witness Compose",
    )
    env_bytes = read_exact_material_file(
        env_file_path,
        expected_mode=0o600,
        label="installed Witness environment",
    )
    values, active, expected_project = _prove_directory_bind(
        compose_bytes,
        env_bytes,
    )
    revision_root = active.parent
    archive = revision_root / ARCHIVE_DIRECTORY_NAME
    journals = revision_root / JOURNAL_DIRECTORY_NAME
    for path, label in (
        (revision_root, "relay revision root"),
        (active, "relay active directory"),
        (archive, "relay archive directory"),
        (journals, "relay journal directory"),
    ):
        _assert_root_directory(path, label=label)
    assert_root_controlled_ancestors(
        revision_root,
        label="relay revision root",
    )
    try:
        new_resolved = new_session_path.resolve(strict=True)
    except OSError as exc:
        raise WitnessRelayRotationError("new relay session is unavailable") from exc
    if new_resolved != new_session_path or revision_root in new_session_path.parents:
        raise WitnessRelayRotationError(
            "new relay session source must be outside the installed revision"
        )
    assert_root_controlled_ancestors(
        new_session_path.parent,
        label="new relay session parent",
    )
    new_bytes, new_session = _read_canonical_json(
        new_session_path,
        label="new relay session",
    )
    new_hash = _sha256(new_bytes)
    operation_id = _operation_id(revision_root.name, new_hash)
    temporary_name = f".session-{operation_id}.tmp"
    allowed_active_entries = {
        SESSION_NAME,
        POLICY_NAME,
        temporary_name,
    }
    active_entries = {entry.name for entry in active.iterdir()}
    if (
        not {SESSION_NAME, POLICY_NAME}.issubset(active_entries)
        or not active_entries.issubset(allowed_active_entries)
    ):
        raise WitnessRelayRotationError(
            "relay active directory contains an unexpected entry"
        )
    policy_bytes, policy = _read_canonical_json(
        active / POLICY_NAME,
        label="installed relay policy",
    )
    policy_hash = _sha256(policy_bytes)
    if policy_hash != expected_policy_sha256:
        raise WitnessRelayRotationError(
            "installed relay policy differs from the explicitly pinned hash"
        )
    live_bytes, live_session = _read_canonical_json(
        active / SESSION_NAME,
        label="installed relay session",
    )
    live_hash = _sha256(live_bytes)
    release = values["STAGING_RELEASE_SHA"].lower()
    new_is_live = live_hash == new_hash
    new_result = _verify_session(
        new_session,
        policy=policy,
        release_sha=release,
        now=now,
        require_fresh=False,
    )
    scope_hash = new_result["session_scope_sha256"]

    lock_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    lock_fd = os.open(revision_root, lock_flags)
    try:
        _assert_pinned_directory(
            revision_root,
            lock_fd,
            label="relay revision root",
        )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WitnessRelayRotationError(
                "another relay rotation holds the revision lock"
            ) from exc
        for path, label in (
            (revision_root, "relay revision root"),
            (active, "relay active directory"),
            (archive, "relay archive directory"),
            (journals, "relay journal directory"),
        ):
            _assert_root_directory(path, label=label)
        _assert_pinned_directory(
            revision_root,
            lock_fd,
            label="relay revision root",
        )
        journal_path = journals / f"{operation_id}.json"
        journal_create_sidecar = f".{journal_path.name}.creating"
        journal_update_sidecar = (
            f".{journal_path.name}.{operation_id}.update"
        )
        _assert_control_directory_entries(
            journals,
            name_pattern=JOURNAL_NAME_RE,
            label="relay journal directory",
            allowed_names=frozenset(
                {
                    journal_path.name,
                    journal_create_sidecar,
                    journal_update_sidecar,
                }
            ),
        )
        for existing_path in journals.iterdir():
            if JOURNAL_NAME_RE.fullmatch(existing_path.name) is None:
                continue
            existing_journal = _read_journal(existing_path)
            if existing_journal["operation_id"] != existing_path.stem:
                raise WitnessRelayRotationError(
                    "relay rotation journal filename differs from its operation"
                )
            if (
                existing_path != journal_path
                and existing_journal["phase"] != "complete"
            ):
                raise WitnessRelayRotationError(
                    "another relay rotation journal is incomplete"
                )
        _finish_published_create_sidecar(
            journal_path,
            label="relay rotation journal",
        )
        journal_sidecar_path = journals / journal_create_sidecar
        if journal_path.exists() or journal_path.is_symlink():
            journal_source_path: Path | None = journal_path
            journal_candidate: dict[str, Any] | None = _read_journal(
                journal_source_path
            )
        elif journal_sidecar_path.exists() or journal_sidecar_path.is_symlink():
            journal_source_path = journal_sidecar_path
            journal_candidate = _read_unpublished_journal_sidecar(
                journal_source_path
            )
        else:
            journal_source_path = None
            journal_candidate = None
        active_entries = {entry.name for entry in active.iterdir()}
        if (
            not {SESSION_NAME, POLICY_NAME}.issubset(active_entries)
            or not active_entries.issubset(allowed_active_entries)
        ):
            raise WitnessRelayRotationError(
                "relay active directory changed before the rotation lock"
            )
        locked_policy_bytes, locked_policy = _read_canonical_json(
            active / POLICY_NAME,
            label="installed relay policy",
        )
        if (
            locked_policy_bytes != policy_bytes
            or locked_policy != policy
            or _sha256(locked_policy_bytes) != expected_policy_sha256
        ):
            raise WitnessRelayRotationError(
                "installed relay policy changed before the rotation lock"
            )
        live_bytes, live_session = _read_canonical_json(
            active / SESSION_NAME,
            label="installed relay session",
        )
        live_hash = _sha256(live_bytes)
        new_is_live = live_hash == new_hash
        new_result = _verify_session(
            new_session,
            policy=policy,
            release_sha=release,
            now=now,
            require_fresh=False,
        )
        scope_hash = new_result["session_scope_sha256"]
        runtime_before = _inspect_runtime_container(
            container_id=container_id,
            material_directory=active,
            values=values,
            expected_project=expected_project,
        )
        preflight_old_result = None
        if not new_is_live:
            preflight_old_result = _verify_session(
                live_session,
                policy=policy,
                release_sha=release,
                now=now,
                require_fresh=False,
            )
            _assert_newer_session(
                old_result=preflight_old_result,
                new_result=new_result,
            )
        expected_new = {
            "approval_id": new_result["approval_id"],
            "expires_at": new_result["expires_at"],
            "session_sha256": new_hash,
        }
        journal_matches_request = (
            journal_candidate is not None
            and journal_candidate["operation_id"] == operation_id
            and journal_candidate["revision_id"] == revision_root.name
            and journal_candidate["release_sha"] == release
            and journal_candidate["policy_sha256"] == policy_hash
            and journal_candidate["session_scope_sha256"] == scope_hash
            and journal_candidate["runtime"] == runtime_before
            and journal_candidate["new"] == expected_new
        )
        if journal_source_path == journal_path:
            if not journal_matches_request:
                raise WitnessRelayRotationError(
                    "published relay rotation journal differs from the requested operation"
                )
            has_durable_admission = True
        elif journal_source_path == journal_sidecar_path:
            has_durable_admission = bool(
                journal_matches_request
                and journal_candidate is not None
                and journal_candidate["phase"] == "prepared"
                and not new_is_live
                and preflight_old_result is not None
                and journal_candidate["old"]
                == {
                    "approval_id": preflight_old_result["approval_id"],
                    "session_sha256": live_hash,
                }
                and journal_candidate["archive_name"]
                == f"session-{live_hash}.json"
                and journal_candidate["temporary_name"] == temporary_name
            )
        else:
            has_durable_admission = False
        if not has_durable_admission:
            new_result = _verify_session(
                new_session,
                policy=policy,
                release_sha=release,
                now=now,
                require_fresh=True,
            )
            scope_hash = new_result["session_scope_sha256"]
            if new_is_live:
                raise WitnessRelayRotationError(
                    "new session is live without its rotation journal"
                )
            if journal_source_path == journal_sidecar_path:
                _assert_pinned_directory(
                    revision_root,
                    lock_fd,
                    label="relay revision root",
                )
                _assert_root_directory(
                    journals,
                    label="relay journal directory",
                )
                _unlink_recoverable_file(
                    journal_sidecar_path,
                    label="incomplete relay rotation journal creation sidecar",
                    allowed_links=frozenset({1}),
                )
                _assert_pinned_directory(
                    revision_root,
                    lock_fd,
                    label="relay revision root",
                )
                journal_source_path = None
                journal_candidate = None
        if has_durable_admission:
            if journal_source_path is None:
                raise WitnessRelayRotationError(
                    "relay rotation lost its durable admission"
                )
            journal = _read_journal(journal_source_path)
            if (
                journal["operation_id"] != operation_id
                or journal["revision_id"] != revision_root.name
                or journal["release_sha"] != release
                or journal["policy_sha256"] != policy_hash
                or journal["session_scope_sha256"] != scope_hash
                or journal["runtime"] != runtime_before
                or journal["new"] != expected_new
            ):
                raise WitnessRelayRotationError(
                    "relay rotation journal differs from the requested operation"
                )
            if journal_source_path == journal_sidecar_path:
                _recoverable_create_exclusive(
                    journal_path,
                    _journal_bytes(journal),
                    label="relay rotation journal",
                )
        else:
            if new_is_live:
                raise WitnessRelayRotationError(
                    "new session is live without its rotation journal"
                )
            if preflight_old_result is None:
                raise WitnessRelayRotationError(
                    "relay rotation lost its preflight session binding"
                )
            old_result = preflight_old_result
            timestamp = (now or datetime.now(timezone.utc)).astimezone(
                timezone.utc
            ).isoformat()
            archive_name = f"session-{live_hash}.json"
            journal = {
                "schema": ROTATION_SCHEMA,
                "operation_id": operation_id,
                "phase": "prepared",
                "created_at": timestamp,
                "revision_id": revision_root.name,
                "release_sha": release,
                "policy_sha256": policy_hash,
                "session_scope_sha256": scope_hash,
                "old": {
                    "approval_id": old_result["approval_id"],
                    "session_sha256": live_hash,
                },
                "new": {
                    "approval_id": new_result["approval_id"],
                    "expires_at": new_result["expires_at"],
                    "session_sha256": new_hash,
                },
                "archive_name": archive_name,
                "temporary_name": temporary_name,
                "runtime": runtime_before,
            }
            _recoverable_create_exclusive(
                journal_path,
                _journal_bytes(journal),
                label="relay rotation journal",
            )
            _crash(crash_after, "journal-prepared")

        old_hash = str(journal["old"]["session_sha256"])
        _assert_pinned_directory(
            revision_root,
            lock_fd,
            label="relay revision root",
        )
        archive_path = archive / str(journal["archive_name"])
        temporary_path = active / str(journal["temporary_name"])
        if (
            journal["archive_name"] != f"session-{old_hash}.json"
            or journal["temporary_name"] != temporary_name
        ):
            raise WitnessRelayRotationError("relay rotation path binding is invalid")

        archive_create_sidecar = f".{archive_path.name}.creating"
        _assert_control_directory_entries(
            archive,
            name_pattern=ARCHIVE_NAME_RE,
            label="relay archive directory",
            allowed_names=frozenset(
                {archive_path.name, archive_create_sidecar}
            ),
        )
        _finish_published_create_sidecar(
            archive_path,
            label="archived relay session",
        )
        if archive_path.exists() or archive_path.is_symlink():
            archived_bytes, archived_session = _read_canonical_json(
                archive_path,
                label="archived relay session",
            )
            if _sha256(archived_bytes) != old_hash:
                raise WitnessRelayRotationError(
                    "archived relay session differs from the journal"
                )
        else:
            if journal["phase"] != "prepared":
                raise WitnessRelayRotationError(
                    "relay rotation archive is missing after its durable phase"
                )
            if live_hash != old_hash:
                raise WitnessRelayRotationError(
                    "old relay session is unavailable for create-exclusive archive"
                )
            archived_bytes = live_bytes
            archived_session = live_session
            _recoverable_create_exclusive(
                archive_path,
                archived_bytes,
                label="archived relay session",
            )
            _crash(crash_after, "archive-written")
        old_result = _verify_session(
            archived_session,
            policy=policy,
            release_sha=release,
            now=now,
            require_fresh=False,
        )
        if (
            old_result["approval_id"] != journal["old"]["approval_id"]
            or old_result["session_scope_sha256"] != scope_hash
        ):
            raise WitnessRelayRotationError(
                "archived relay session differs from the rotation binding"
            )
        _assert_newer_session(
            old_result=old_result,
            new_result=new_result,
        )
        phase = str(journal["phase"])
        if phase == "prepared":
            _set_phase(journal_path, journal, "archived")

        live_bytes, live_session = _read_canonical_json(
            active / SESSION_NAME,
            label="installed relay session",
        )
        live_hash = _sha256(live_bytes)
        temp_exists = temporary_path.exists() or temporary_path.is_symlink()
        if live_hash == new_hash:
            if temp_exists or journal["phase"] not in {
                "staged",
                "activated",
                "complete",
            }:
                raise WitnessRelayRotationError(
                    "activated relay session has an inconsistent journal/temp state"
                )
        elif live_hash == old_hash:
            if journal["phase"] in {"activated", "complete"}:
                raise WitnessRelayRotationError(
                    "relay rotation journal is ahead of the live session"
                )
            if not temp_exists and journal["phase"] == "staged":
                raise WitnessRelayRotationError(
                    "staged relay session disappeared after its durable phase"
                )
            _recoverable_stage_file(
                temporary_path,
                new_bytes,
                durable_phase=str(journal["phase"]),
            )
            if not temp_exists:
                _crash(crash_after, "temp-written")
            if journal["phase"] in {"prepared", "archived"}:
                _set_phase(journal_path, journal, "staged")
            _assert_pinned_directory(
                revision_root,
                lock_fd,
                label="relay revision root",
            )
            _replace_active_session(
                active=active,
                temporary_name=temporary_name,
            )
            _crash(crash_after, "session-replaced")
            _set_phase(journal_path, journal, "activated")
        else:
            raise WitnessRelayRotationError(
                "live relay session differs from both journal endpoints"
            )

        readback_bytes, readback = _read_canonical_json(
            active / SESSION_NAME,
            label="rotated relay session",
        )
        if readback_bytes != new_bytes:
            raise WitnessRelayRotationError(
                "rotated relay session failed its atomic read-back"
            )
        readback_result = _verify_session(
            readback,
            policy=policy,
            release_sha=release,
            now=now,
            require_fresh=False,
        )
        if (
            readback_result["approval_id"] != journal["new"]["approval_id"]
            or readback_result["session_scope_sha256"] != scope_hash
        ):
            raise WitnessRelayRotationError(
                "rotated relay session failed its cryptographic read-back"
            )
        if journal["phase"] != "complete":
            _set_phase(journal_path, journal, "complete")
            _crash(crash_after, "journal-complete")
        if {entry.name for entry in active.iterdir()} != {SESSION_NAME, POLICY_NAME}:
            raise WitnessRelayRotationError(
                "relay active directory retained rotation residue"
            )
        _assert_pinned_directory(
            revision_root,
            lock_fd,
            label="relay revision root",
        )
        _assert_control_directory_entries(
            archive,
            name_pattern=ARCHIVE_NAME_RE,
            label="relay archive directory",
        )
        _assert_control_directory_entries(
            journals,
            name_pattern=JOURNAL_NAME_RE,
            label="relay journal directory",
        )
        runtime_after = _inspect_runtime_container(
            container_id=container_id,
            material_directory=active,
            values=values,
            expected_project=expected_project,
        )
        if runtime_after != runtime_before or runtime_after != journal["runtime"]:
            raise WitnessRelayRotationError(
                "Witness container changed during relay session rotation"
            )
        return {
            "status": "rotated-and-verified",
            "operation_id": operation_id,
            "revision_id": revision_root.name,
            "release_sha": release,
            "approval_id": new_result["approval_id"],
            "previous_approval_id": journal["old"]["approval_id"],
            "expires_at": new_result["expires_at"],
            "policy_sha256": policy_hash,
            "session_scope_sha256": scope_hash,
            "journal_phase": "complete",
            "idempotent": new_is_live,
            "active_file_count": 2,
            "service_changed": False,
            "container_changed": False,
            "current_changed": False,
        }
    finally:
        os.close(lock_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role-compose", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--new-session", type=Path, required=True)
    parser.add_argument("--expected-policy-sha256", required=True)
    parser.add_argument("--container-id", required=True)
    args = parser.parse_args(argv)
    try:
        result = rotate_witness_relay_session(
            role_compose_path=args.role_compose,
            env_file_path=args.env_file,
            new_session_path=args.new_session,
            expected_policy_sha256=args.expected_policy_sha256,
            container_id=args.container_id,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
