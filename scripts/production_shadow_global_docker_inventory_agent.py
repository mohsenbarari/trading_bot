#!/usr/bin/env python3
"""Attest a stable, host-wide Docker inventory without mutating Docker.

The agent is deliberately a bounded stdio protocol.  It returns only counts
and digest roots; normalized resource descriptors never cross the control
channel.  A before/after pair proves that the frozen-final restore changed
only its exact database container and operation network.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import selectors
import secrets
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
import types
from typing import Any, Callable, Mapping, Protocol, Sequence
from uuid import UUID


sys.dont_write_bytecode = True

REQUEST_SCHEMA = "production-shadow-global-docker-inventory-request-v2"
RESPONSE_SCHEMA = "production-shadow-global-docker-inventory-response-v2"
DESCRIPTOR_SCHEMA = "production-shadow-global-docker-resource-v1"
COMPARISON_SCHEMA = "production-shadow-global-docker-inventory-comparison-v1"
PREPARED_REQUEST_SCHEMA = (
    "production-shadow-prepared-clone-inventory-request-v1"
)
PREPARED_RESPONSE_SCHEMA = (
    "production-shadow-prepared-clone-inventory-response-v1"
)

ROLE_NAMES = ("bot_fi", "webapp_fi", "webapp_ir")
ROLE_PATHS = {
    "bot_fi": "bot-fi",
    "webapp_fi": "webapp-fi",
    "webapp_ir": "webapp-ir",
}
ROLE_HOSTS = {
    "bot_fi": "65.109.216.187",
    "webapp_fi": "65.109.220.59",
    "webapp_ir": "95.38.164.29",
}

PROJECT_ROOT_PREFIX = Path("/srv/trading-bot-three-site-production-shadow")
DATA_ROOT_PREFIX = Path(
    "/srv/trading-bot-three-site-production-shadow-data"
)
SECRET_ROOT_PREFIX = Path(
    "/root/secure-envs/trading-bot/three-site-production-shadow"
)
AGENT_RELATIVE = Path(
    "scripts/production_shadow_global_docker_inventory_agent.py"
)
WORKER_RELATIVE = Path(
    "scripts/production_shadow_frozen_final_restore_worker.py"
)
PREPARED_WORKER_RELATIVES = {
    "finland-precommit": Path(
        "scripts/production_shadow_precommit_worker.py"
    ),
    "wa-ir-operation": Path("scripts/wa_ir_production_operation.py"),
}

DOCKER = "/usr/bin/docker"
DOCKER_SOCKET_PATH = Path("/run/docker.sock")
DOCKER_HOST_ARGUMENT = "--host=unix:///run/docker.sock"
DOCKER_BASE = (DOCKER, DOCKER_HOST_ARGUMENT)
DOCKER_API_VERSION = "1.52"
GIT = "/usr/bin/git"
MAX_CONTROL_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_COMMAND_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_COMMAND_ERROR_BYTES = 1024 * 1024
MAX_RESOURCES_PER_KIND = 8192
MAX_INSPECT_CHUNK = 16
MAX_LABELS = 1024
MAX_LABEL_BYTES = 64 * 1024
MAX_MOUNTS = 1024
MAX_NETWORK_ATTACHMENTS = 1024
MAX_STRING_BYTES = 256 * 1024
MAX_CAPTURE_OUTPUT_BYTES = 256 * 1024 * 1024
MAX_CAPTURE_DURATION_SECONDS = 300.0
PREPARED_REQUEST_MIN_LIFETIME_SECONDS = 15.0
PREPARED_REQUEST_MAX_LIFETIME_SECONDS = 120.0
PREPARED_REQUEST_FUTURE_SKEW_SECONDS = 5.0
PROCESS_TREE_TERM_SECONDS = 1.0
PROCESS_TREE_QUIESCENCE_SECONDS = 0.25
MAX_PROCESS_SNAPSHOT_MEMBERS = 65536
MAX_PROCESS_TREE_MEMBERS = 8192
PR_SET_CHILD_SUBREAPER = 36
ZERO_SHA256 = "0" * 64
_BOUNDED_COMMAND_LOCK = threading.Lock()

SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
NETWORK_ID_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{7,127}$")
VOLUME_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
COMPOSE_VARIABLE_RE = re.compile(
    r"^\$\{([A-Z_][A-Z0-9_]*)(?:(:-|:\?)(.*))?\}$"
)
RESOURCE_KINDS = ("container", "network", "volume", "image")
HOST_CONFIG_FIELDS = frozenset(
    {
        "CpuShares",
        "Memory",
        "CgroupParent",
        "BlkioWeight",
        "BlkioWeightDevice",
        "BlkioDeviceReadBps",
        "BlkioDeviceWriteBps",
        "BlkioDeviceReadIOps",
        "BlkioDeviceWriteIOps",
        "CpuPeriod",
        "CpuQuota",
        "CpuRealtimePeriod",
        "CpuRealtimeRuntime",
        "CpusetCpus",
        "CpusetMems",
        "Devices",
        "DeviceCgroupRules",
        "DeviceRequests",
        "MemoryReservation",
        "MemorySwap",
        "MemorySwappiness",
        "NanoCpus",
        "OomKillDisable",
        "Init",
        "PidsLimit",
        "Ulimits",
        "CpuCount",
        "CpuPercent",
        "IOMaximumIOps",
        "IOMaximumBandwidth",
        "Binds",
        "ContainerIDFile",
        "LogConfig",
        "NetworkMode",
        "PortBindings",
        "RestartPolicy",
        "AutoRemove",
        "VolumeDriver",
        "VolumesFrom",
        "Mounts",
        "ConsoleSize",
        "Annotations",
        "CapAdd",
        "CapDrop",
        "CgroupnsMode",
        "Dns",
        "DnsOptions",
        "DnsSearch",
        "ExtraHosts",
        "GroupAdd",
        "IpcMode",
        "Cgroup",
        "Links",
        "OomScoreAdj",
        "PidMode",
        "Privileged",
        "PublishAllPorts",
        "ReadonlyRootfs",
        "SecurityOpt",
        "StorageOpt",
        "Tmpfs",
        "UTSMode",
        "UsernsMode",
        "ShmSize",
        "Sysctls",
        "Runtime",
        "Isolation",
        "MaskedPaths",
        "ReadonlyPaths",
    }
)
OPTIONAL_HOST_CONFIG_FIELDS = frozenset(
    {"Annotations", "Mounts", "StorageOpt", "Sysctls", "Tmpfs", "Init"}
)
CONTAINER_CONFIG_FIELDS = frozenset(
    {
        "Hostname",
        "Domainname",
        "User",
        "AttachStdin",
        "AttachStdout",
        "AttachStderr",
        "ExposedPorts",
        "Tty",
        "OpenStdin",
        "StdinOnce",
        "Env",
        "Cmd",
        "Healthcheck",
        "ArgsEscaped",
        "Image",
        "Volumes",
        "WorkingDir",
        "Entrypoint",
        "NetworkDisabled",
        "OnBuild",
        "Labels",
        "StopSignal",
        "StopTimeout",
        "Shell",
    }
)

SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "DOCKER_CONFIG": "/nonexistent",
    "DOCKER_API_VERSION": DOCKER_API_VERSION,
}
SAFE_GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
}
GIT_CONFIG_ARGUMENTS = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fileMode=true",
)
RESERVED_PROCESS_ENVIRONMENT = frozenset(
    {
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_CONFIG",
        "DOCKER_API_VERSION",
    }
)

REQUEST_FIELDS = frozenset(
    {
        "schema",
        "action",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "restore_generation_sha256",
        "role",
        "expected_host",
        "release_root",
        "agent_path",
        "agent_sha256",
        "worker_path",
        "worker_sha256",
        "project_base",
        "project_name",
        "expected_operation_container_id",
        "expected_operation_host_config_sha256",
        "role_manifest_path",
        "role_manifest_sha256",
        "request_binding_sha256",
    }
)
PREPARED_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "action",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "expected_host",
        "controller_challenge_sha256",
        "issued_at",
        "expires_at",
        "expected_database_state",
        "baseline_response_sha256",
        "expected_prepared_container_id",
        "expected_prepared_network_id",
        "expected_prepared_config_sha256",
        "expected_prepared_compose_config_sha256",
        "expected_prepared_host_config_sha256",
        "expected_prepared_mounts_sha256",
        "expected_prepared_network_identity_sha256",
        "expected_prepared_network_metadata_sha256",
        "expected_prepared_redis_identity_sha256",
        "expected_prepared_redis_chain_metadata_sha256",
        "expected_prepared_redis_metadata_sha256",
        "expected_non_operation_inventory_root_sha256",
        "expected_non_operation_identity_root_sha256",
        "expected_non_operation_state_root_sha256",
        "expected_non_operation_metadata_root_sha256",
        "expected_non_operation_resource_counts",
        "release_root",
        "agent_path",
        "agent_sha256",
        "contract_kind",
        "contract_worker_path",
        "contract_worker_sha256",
        "role_manifest_path",
        "role_manifest_sha256",
        "project_base",
        "project_name",
        "request_binding_sha256",
    }
)
COUNT_FIELDS = frozenset(RESOURCE_KINDS)
RESPONSE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "action",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "restore_generation_sha256",
        "role",
        "expected_host",
        "observed_host_ipv4",
        "project_base",
        "project_name",
        "request_binding_sha256",
        "expected_operation_container_id",
        "expected_operation_host_config_sha256",
        "observed_operation_host_config_sha256",
        "inventory_root_sha256",
        "inventory_identity_root_sha256",
        "inventory_state_root_sha256",
        "inventory_metadata_root_sha256",
        "resource_counts",
        "non_operation_inventory_root_sha256",
        "non_operation_identity_root_sha256",
        "non_operation_state_root_sha256",
        "non_operation_metadata_root_sha256",
        "non_operation_resource_counts",
        "operation_resource_root_sha256",
        "operation_resource_counts",
        "stable_capture_count",
        "descriptors_returned",
        "docker_read_only",
        "network_io_performed",
        "filesystem_mutated",
        "response_sha256",
    }
)
PREPARED_RESPONSE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "action",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "expected_host",
        "observed_host_ipv4",
        "controller_challenge_sha256",
        "issued_at",
        "expires_at",
        "captured_at",
        "expected_database_state",
        "baseline_response_sha256",
        "contract_kind",
        "project_base",
        "project_name",
        "request_binding_sha256",
        "role_manifest_sha256",
        "prepared_container_id",
        "prepared_network_id",
        "prepared_container_identity_sha256",
        "prepared_container_state_sha256",
        "prepared_container_metadata_sha256",
        "prepared_network_identity_sha256",
        "prepared_network_state_sha256",
        "prepared_network_metadata_sha256",
        "prepared_config_sha256",
        "prepared_environment_sha256",
        "prepared_environment_entry_count",
        "prepared_compose_config_sha256",
        "prepared_host_config_sha256",
        "prepared_mounts_sha256",
        "prepared_network_attachment_sha256",
        "prepared_redis_identity_sha256",
        "prepared_redis_chain_metadata_sha256",
        "prepared_redis_metadata_sha256",
        "prepared_redis_target_count",
        "prepared_redis_unsafe_path_count",
        "prepared_redis_entry_count",
        "prepared_redis_pristine",
        "inventory_root_sha256",
        "inventory_identity_root_sha256",
        "inventory_state_root_sha256",
        "inventory_metadata_root_sha256",
        "resource_counts",
        "non_operation_inventory_root_sha256",
        "non_operation_identity_root_sha256",
        "non_operation_state_root_sha256",
        "non_operation_metadata_root_sha256",
        "non_operation_resource_counts",
        "operation_resource_root_sha256",
        "operation_resource_counts",
        "stable_capture_count",
        "prepared_database_running",
        "prepared_database_healthy",
        "descriptors_returned",
        "environment_values_returned",
        "path_descriptors_returned",
        "docker_read_only",
        "network_io_performed",
        "filesystem_mutated",
        "response_sha256",
    }
)
COMPARISON_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "restore_generation_sha256",
        "role",
        "project_base",
        "project_name",
        "before_response_sha256",
        "after_response_sha256",
        "non_operation_inventory_root_sha256",
        "non_operation_identity_root_sha256",
        "non_operation_state_root_sha256",
        "non_operation_metadata_root_sha256",
        "non_operation_resource_counts",
        "non_operation_resource_delta_count",
        "comparison_sha256",
    }
)


class GlobalDockerInventoryError(RuntimeError):
    """A redacted, fail-closed inventory protocol error."""


class DockerRunner(Protocol):
    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout: float = 30,
    ) -> str:
        """Run one bounded, read-only Docker command."""


@dataclass(frozen=True)
class ResourceDescriptor:
    kind: str
    identity_sha256: str
    state_sha256: str
    metadata_sha256: str
    operation_match: bool

    def document(self) -> dict[str, Any]:
        return {
            "schema": DESCRIPTOR_SCHEMA,
            "kind": self.kind,
            "identity_sha256": self.identity_sha256,
            "state_sha256": self.state_sha256,
            "metadata_sha256": self.metadata_sha256,
            "operation_match": self.operation_match,
        }


@dataclass(frozen=True)
class CapturedInventory:
    descriptors: tuple[ResourceDescriptor, ...]
    raw_containers: Mapping[str, Mapping[str, Any]]
    raw_networks: Mapping[str, Mapping[str, Any]]
    raw_images: Mapping[str, Mapping[str, Any]]


@dataclass
class CaptureBudget:
    runner: DockerRunner
    started_at: float
    bytes_consumed: int = 0

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout: float = 30,
    ) -> str:
        remaining = (
            self.started_at
            + MAX_CAPTURE_DURATION_SECONDS
            - time.monotonic()
        )
        if remaining <= 0:
            raise GlobalDockerInventoryError(
                "aggregate Docker inventory duration budget exhausted"
            )
        bounded_timeout = min(float(timeout), remaining)
        raw = self.runner.run(arguments, timeout=bounded_timeout)
        if not isinstance(raw, str):
            raise GlobalDockerInventoryError(
                "Docker inventory output is not text"
            )
        try:
            observed_bytes = len(raw.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise GlobalDockerInventoryError(
                "Docker inventory output is invalid UTF-8"
            ) from exc
        self.bytes_consumed += observed_bytes
        if self.bytes_consumed > MAX_CAPTURE_OUTPUT_BYTES:
            raise GlobalDockerInventoryError(
                "aggregate Docker inventory byte budget exhausted"
            )
        if time.monotonic() > (
            self.started_at + MAX_CAPTURE_DURATION_SECONDS
        ):
            raise GlobalDockerInventoryError(
                "aggregate Docker inventory duration budget exhausted"
            )
        return raw


@dataclass(frozen=True)
class DockerSocketIdentity:
    device: int
    inode: int
    uid: int
    gid: int
    mode: int


@dataclass(frozen=True)
class BoundedCommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class BoundedCommandError(RuntimeError):
    """Raised when a subprocess exceeds its bounded execution contract."""


def _enable_child_subreaper() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise BoundedCommandError(
            f"subprocess child subreaper setup failed with errno {error}"
        )


@dataclass(frozen=True)
class ProcessIdentity:
    process_id: int
    parent_id: int
    process_group: int
    session_id: int
    starttime: int
    state: str

    @property
    def key(self) -> tuple[int, int]:
        return self.process_id, self.starttime


def _proc_identity(process_id: int) -> tuple[int, int, int, int, str]:
    try:
        payload = Path(f"/proc/{process_id}/stat").read_text(
            encoding="ascii"
        )
        fields = payload[payload.rindex(") ") + 2 :].split()
        if len(fields) < 20:
            raise ValueError("short process stat")
        state = fields[0]
        parent = int(fields[1], 10)
        group = int(fields[2], 10)
        session = int(fields[3], 10)
        starttime = int(fields[19], 10)
    except (OSError, UnicodeError, ValueError) as exc:
        raise BoundedCommandError(
            "subprocess identity is unavailable"
        ) from exc
    return parent, group, session, starttime, state


def _read_process_identity(process_id: int) -> ProcessIdentity:
    parent, group, session, starttime, state = _proc_identity(process_id)
    return ProcessIdentity(
        process_id=process_id,
        parent_id=parent,
        process_group=group,
        session_id=session,
        starttime=starttime,
        state=state,
    )


def _process_snapshot() -> dict[int, ProcessIdentity]:
    observed: dict[int, ProcessIdentity] = {}
    scanned = 0
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            scanned += 1
            if scanned > MAX_PROCESS_SNAPSHOT_MEMBERS:
                raise BoundedCommandError(
                    "subprocess closure exceeds its process bound"
                )
            process_id = int(entry.name, 10)
            try:
                identity = _read_process_identity(process_id)
            except BoundedCommandError:
                continue
            observed[process_id] = identity
    except BoundedCommandError:
        raise
    except OSError as exc:
        raise BoundedCommandError(
            "subprocess closure cannot be enumerated"
        ) from exc
    return observed


def _direct_child_baseline() -> frozenset[tuple[int, int]]:
    owner = os.getpid()
    return frozenset(
        identity.key
        for identity in _process_snapshot().values()
        if identity.parent_id == owner
    )


def _owned_processes(
    root_identity: ProcessIdentity,
    *,
    baseline_children: frozenset[tuple[int, int]],
    tracked: set[ProcessIdentity] | None = None,
    include_zombies: bool = False,
) -> tuple[ProcessIdentity, ...]:
    snapshot = _process_snapshot()
    observed_root = snapshot.get(root_identity.process_id)
    owned_ids: set[int] = set()
    if (
        observed_root is not None
        and observed_root.starttime == root_identity.starttime
    ):
        owned_ids.add(root_identity.process_id)
    if tracked is not None:
        for identity in tracked:
            current = snapshot.get(identity.process_id)
            if (
                current is not None
                and current.starttime == identity.starttime
            ):
                owned_ids.add(identity.process_id)
    owner = os.getpid()
    for identity in snapshot.values():
        if (
            identity.process_id != root_identity.process_id
            and identity.parent_id == owner
            and identity.key not in baseline_children
        ):
            owned_ids.add(identity.process_id)
    changed = True
    while changed:
        changed = False
        for identity in snapshot.values():
            if (
                identity.process_id not in owned_ids
                and identity.parent_id in owned_ids
            ):
                owned_ids.add(identity.process_id)
                changed = True
    owned = tuple(
        identity
        for process_id, identity in snapshot.items()
        if process_id in owned_ids
    )
    if tracked is not None:
        discovered = set(owned)
        if len(tracked | discovered) > MAX_PROCESS_TREE_MEMBERS:
            raise BoundedCommandError(
                "subprocess tree exceeds its process bound"
            )
        tracked.update(discovered)
    return tuple(
        identity
        for identity in owned
        if include_zombies or identity.state != "Z"
    )


def _signal_process_identity(
    identity: ProcessIdentity,
    signum: int,
) -> None:
    try:
        current = _proc_identity(identity.process_id)
    except BoundedCommandError:
        return
    if current[3] != identity.starttime:
        return
    try:
        descriptor = os.pidfd_open(identity.process_id, 0)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise BoundedCommandError(
            "identity-bound process handle cannot be opened"
        ) from exc
    try:
        refreshed = _proc_identity(identity.process_id)
        if refreshed[3] != identity.starttime:
            return
        _signal_process_handle(descriptor, signum)
    except ProcessLookupError:
        return
    finally:
        os.close(descriptor)


def _signal_process_handle(descriptor: int, signum: int) -> None:
    try:
        signal.pidfd_send_signal(descriptor, signum)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise BoundedCommandError(
            "identity-bound process signal failed"
        ) from exc


def _signal_owned_process(
    identity: ProcessIdentity,
    signum: int,
    *,
    root_identity: ProcessIdentity,
    root_descriptor: int | None,
) -> None:
    if identity.key == root_identity.key and root_descriptor is not None:
        _signal_process_handle(root_descriptor, signum)
        return
    _signal_process_identity(identity, signum)


def _reap_owned_zombies(
    root_identity: ProcessIdentity,
    *,
    baseline_children: frozenset[tuple[int, int]],
    tracked: set[ProcessIdentity],
) -> None:
    owner = os.getpid()
    while True:
        reaped = False
        for identity in _owned_processes(
            root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
            include_zombies=True,
        ):
            if (
                identity.key == root_identity.key
                or identity.parent_id != owner
                or identity.state != "Z"
            ):
                continue
            try:
                waited, _status = os.waitpid(
                    identity.process_id,
                    os.WNOHANG,
                )
            except (ChildProcessError, ProcessLookupError):
                continue
            except OSError as exc:
                raise BoundedCommandError(
                    "adopted subprocess child could not be reaped"
                ) from exc
            reaped |= waited == identity.process_id
        if not reaped:
            return


def _terminate_process_tree(
    process: subprocess.Popen[bytes],
    *,
    root_identity: ProcessIdentity,
    root_descriptor: int | None,
    baseline_children: frozenset[tuple[int, int]],
    tracked: set[ProcessIdentity],
) -> None:
    for identity in reversed(
        _owned_processes(
            root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
        )
    ):
        _signal_owned_process(
            identity,
            signal.SIGTERM,
            root_identity=root_identity,
            root_descriptor=root_descriptor,
        )
    deadline = time.monotonic() + PROCESS_TREE_TERM_SECONDS
    while time.monotonic() < deadline and (
        _owned_processes(
            root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
        )
    ):
        process.poll()
        _reap_owned_zombies(
            root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
        )
        time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
    for identity in reversed(
        _owned_processes(
            root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
        )
    ):
        _signal_owned_process(
            identity,
            signal.SIGKILL,
            root_identity=root_identity,
            root_descriptor=root_descriptor,
        )
    try:
        process.wait(timeout=PROCESS_TREE_TERM_SECONDS)
    except subprocess.TimeoutExpired:
        _signal_owned_process(
            root_identity,
            signal.SIGKILL,
            root_identity=root_identity,
            root_descriptor=root_descriptor,
        )
        try:
            process.wait(timeout=PROCESS_TREE_TERM_SECONDS)
        except subprocess.TimeoutExpired as second_exc:
            raise BoundedCommandError(
                "subprocess root survived forced cleanup"
            ) from second_exc
    absence_deadline = (
        time.monotonic()
        + PROCESS_TREE_TERM_SECONDS
        + PROCESS_TREE_QUIESCENCE_SECONDS
    )
    stable_since: float | None = None
    while time.monotonic() < absence_deadline:
        _reap_owned_zombies(
            root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
        )
        owned = _owned_processes(
            root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
            include_zombies=True,
        )
        if owned:
            stable_since = None
            for identity in reversed(owned):
                if identity.state != "Z":
                    _signal_owned_process(
                        identity,
                        signal.SIGKILL,
                        root_identity=root_identity,
                        root_descriptor=root_descriptor,
                    )
        else:
            if stable_since is None:
                stable_since = time.monotonic()
            elif (
                time.monotonic() - stable_since
                >= PROCESS_TREE_QUIESCENCE_SECONDS
            ):
                return
        time.sleep(0.01)
    _reap_owned_zombies(
        root_identity,
        baseline_children=baseline_children,
        tracked=tracked,
    )
    if _owned_processes(
        root_identity,
        baseline_children=baseline_children,
        tracked=tracked,
        include_zombies=True,
    ):
        raise BoundedCommandError(
            "subprocess descendants survived forced cleanup"
        )


def _docker_socket_identity() -> DockerSocketIdentity:
    try:
        metadata = os.lstat(DOCKER_SOCKET_PATH)
    except OSError as exc:
        raise GlobalDockerInventoryError(
            "local Docker socket is unavailable"
        ) from exc
    if (
        not stat.S_ISSOCK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o002
    ):
        raise GlobalDockerInventoryError(
            "local Docker socket identity is unsafe"
        )
    return DockerSocketIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        uid=metadata.st_uid,
        gid=metadata.st_gid,
        mode=metadata.st_mode,
    )


def _assert_docker_socket_identity(expected: DockerSocketIdentity) -> None:
    if _docker_socket_identity() != expected:
        raise GlobalDockerInventoryError(
            "local Docker socket identity changed"
        )


def _bounded_command_locked(
    arguments: Sequence[str],
    *,
    timeout: float,
    env: Mapping[str, str],
    stdout_limit: int,
    stderr_limit: int,
) -> BoundedCommandResult:
    if (
        not arguments
        or any(not isinstance(token, str) or not token for token in arguments)
        or type(timeout) not in {int, float}
        or not math.isfinite(timeout)
        or timeout <= 0
        or isinstance(stdout_limit, bool)
        or not isinstance(stdout_limit, int)
        or stdout_limit <= 0
        or isinstance(stderr_limit, bool)
        or not isinstance(stderr_limit, int)
        or stderr_limit <= 0
    ):
        raise BoundedCommandError(
            "subprocess execution contract is invalid"
        )
    _enable_child_subreaper()
    baseline_children = _direct_child_baseline()
    process: subprocess.Popen[bytes] | None = None
    root_identity: ProcessIdentity | None = None
    root_descriptor: int | None = None
    tracked: set[ProcessIdentity] = set()
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    group_cleaned = False
    try:
        process = subprocess.Popen(  # noqa: S603
            list(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
            close_fds=True,
            shell=False,
            start_new_session=True,
        )
        root_descriptor = os.pidfd_open(process.pid, 0)
        root_identity = _read_process_identity(process.pid)
        tracked.add(root_identity)
        if process.stdout is None or process.stderr is None:
            raise BoundedCommandError("subprocess pipes are unavailable")
        for label, stream in (
            ("stdout", process.stdout),
            ("stderr", process.stderr),
        ):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        deadline = time.monotonic() + timeout
        while selector.get_map():
            _reap_owned_zombies(
                root_identity,
                baseline_children=baseline_children,
                tracked=tracked,
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BoundedCommandError("subprocess timed out")
            events = selector.select(min(0.1, remaining))
            if not events:
                if process.poll() is not None and not group_cleaned:
                    _terminate_process_tree(
                        process,
                        root_identity=root_identity,
                        root_descriptor=root_descriptor,
                        baseline_children=baseline_children,
                        tracked=tracked,
                    )
                    group_cleaned = True
                continue
            for key, _ in events:
                try:
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                label = key.data
                buffer = buffers[label]
                limit = stdout_limit if label == "stdout" else stderr_limit
                if len(buffer) + len(chunk) > limit:
                    raise BoundedCommandError(
                        f"subprocess {label} exceeded its byte limit"
                    )
                buffer.extend(chunk)
            if process.poll() is not None and not group_cleaned:
                _terminate_process_tree(
                    process,
                    root_identity=root_identity,
                    root_descriptor=root_descriptor,
                    baseline_children=baseline_children,
                    tracked=tracked,
                )
                group_cleaned = True
        wait_timeout = deadline - time.monotonic()
        if wait_timeout <= 0:
            raise BoundedCommandError("subprocess timed out")
        try:
            returncode = process.wait(timeout=wait_timeout)
        except subprocess.TimeoutExpired as exc:
            raise BoundedCommandError("subprocess timed out") from exc
        return BoundedCommandResult(
            returncode=returncode,
            stdout=bytes(buffers["stdout"]),
            stderr=bytes(buffers["stderr"]),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BoundedCommandError("subprocess execution failed") from exc
    finally:
        original_error = sys.exception()
        cleanup_errors: list[BaseException] = []
        if process is not None:
            try:
                if not group_cleaned:
                    if root_identity is None:
                        if root_descriptor is None:
                            root_identity = _read_process_identity(
                                process.pid
                            )
                        else:
                            _signal_process_handle(
                                root_descriptor,
                                signal.SIGKILL,
                            )
                            try:
                                process.wait(
                                    timeout=PROCESS_TREE_TERM_SECONDS
                                )
                            except subprocess.TimeoutExpired as exc:
                                raise BoundedCommandError(
                                    "unidentified subprocess root "
                                    "survived forced cleanup"
                                ) from exc
                            root_identity = ProcessIdentity(
                                process_id=process.pid,
                                parent_id=os.getpid(),
                                process_group=process.pid,
                                session_id=process.pid,
                                starttime=-1,
                                state="?",
                            )
                    _terminate_process_tree(
                        process,
                        root_identity=root_identity,
                        root_descriptor=root_descriptor,
                        baseline_children=baseline_children,
                        tracked=tracked,
                    )
            except BaseException as exc:
                cleanup_errors.append(exc)
        try:
            selector.close()
        except BaseException as exc:
            cleanup_errors.append(exc)
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is None:
                    continue
                try:
                    stream.close()
                except BaseException as exc:
                    cleanup_errors.append(exc)
        if root_descriptor is not None:
            try:
                os.close(root_descriptor)
            except BaseException as exc:
                cleanup_errors.append(exc)
        if cleanup_errors:
            cleanup_error = cleanup_errors[0]
            if original_error is not None:
                raise original_error from cleanup_error
            raise cleanup_error


def _bounded_command(
    arguments: Sequence[str],
    *,
    timeout: float,
    env: Mapping[str, str],
    stdout_limit: int,
    stderr_limit: int,
) -> BoundedCommandResult:
    with _BOUNDED_COMMAND_LOCK:
        return _bounded_command_locked(
            arguments,
            timeout=timeout,
            env=env,
            stdout_limit=stdout_limit,
            stderr_limit=stderr_limit,
        )


class SubprocessDockerRunner:
    """Production runner with a strict read-only Docker command allowlist."""

    def __init__(self) -> None:
        self._socket_identity = _docker_socket_identity()

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout: float = 30,
    ) -> str:
        command = tuple(arguments)
        _validate_read_only_docker_command(command)
        _assert_docker_socket_identity(self._socket_identity)
        try:
            result = _bounded_command(
                command,
                timeout=timeout,
                env=SAFE_ENV,
                stdout_limit=MAX_COMMAND_OUTPUT_BYTES,
                stderr_limit=MAX_COMMAND_ERROR_BYTES,
            )
        except BoundedCommandError as exc:
            raise GlobalDockerInventoryError(
                "bounded Docker inspection is unavailable"
            ) from exc
        finally:
            _assert_docker_socket_identity(self._socket_identity)
        if result.returncode != 0 or result.stderr:
            raise GlobalDockerInventoryError(
                "bounded Docker inspection failed closed"
            )
        try:
            return result.stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GlobalDockerInventoryError(
                "Docker inspection returned non-UTF-8 output"
            ) from exc


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise GlobalDockerInventoryError(
            "document contains non-canonical JSON data"
        ) from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GlobalDockerInventoryError(
                "JSON contains a duplicate field"
            )
        result[key] = value
    return result


def strict_json(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        document = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"invalid constant {token}")
            ),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise GlobalDockerInventoryError(
            f"{label} is not strict JSON"
        ) from exc
    if not isinstance(document, dict) or canonical_json(document) != payload:
        raise GlobalDockerInventoryError(
            f"{label} is not canonical JSON"
        )
    return document


def _canonical_uuid4(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise GlobalDockerInventoryError(f"{label} is invalid")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise GlobalDockerInventoryError(f"{label} is invalid") from exc
    if str(parsed) != value or parsed.version != 4:
        raise GlobalDockerInventoryError(
            f"{label} is not a canonical UUIDv4"
        )
    return value


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise GlobalDockerInventoryError(
            f"{label} is not a nonzero SHA-256"
        )
    return value


def _canonical_path(value: Any, *, label: str) -> Path:
    if not isinstance(value, str) or "\x00" in value:
        raise GlobalDockerInventoryError(
            f"{label} is not an absolute canonical path"
        )
    path = Path(value)
    if (
        not path.is_absolute()
        or path != Path(os.path.abspath(value))
        or path.name in {"", ".", ".."}
        or ".." in path.parts
    ):
        raise GlobalDockerInventoryError(
            f"{label} is not an absolute canonical path"
        )
    return path


def _safe_string(
    value: Any,
    *,
    label: str,
    maximum: int = MAX_STRING_BYTES,
) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise GlobalDockerInventoryError(f"{label} is invalid")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError as exc:
        raise GlobalDockerInventoryError(f"{label} is invalid") from exc
    if len(encoded) > maximum:
        raise GlobalDockerInventoryError(f"{label} is invalid")
    return value


def _string_or_none(value: Any, *, label: str) -> str | None:
    if value is None:
        return None
    return _safe_string(value, label=label)


def _string_vector_hash(value: Any, *, label: str) -> str:
    if value is None:
        normalized: list[str] | None = None
    elif isinstance(value, list) and len(value) <= 4096:
        normalized = [
            _safe_string(item, label=label) for item in value
        ]
    else:
        raise GlobalDockerInventoryError(f"{label} is invalid")
    return _sha256(canonical_json(normalized))


def _labels(value: Any, *, label: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > MAX_LABELS:
        raise GlobalDockerInventoryError(f"{label} is invalid")
    result: dict[str, str] = {}
    total = 0
    for key, item in value.items():
        key = _safe_string(key, label=label, maximum=4096)
        item = _safe_string(item, label=label, maximum=MAX_LABEL_BYTES)
        total += len(key.encode("utf-8")) + len(item.encode("utf-8"))
        if total > MAX_LABEL_BYTES:
            raise GlobalDockerInventoryError(f"{label} exceeds its bound")
        result[key] = item
    return dict(sorted(result.items()))


def _request_binding(document: Mapping[str, Any]) -> str:
    unsigned = {
        key: value
        for key, value in document.items()
        if key != "request_binding_sha256"
    }
    return _sha256(canonical_json(unsigned))


def canonical_utc_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise GlobalDockerInventoryError(
            "inventory timestamp must be timezone-aware"
        )
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _parse_utc_timestamp(value: Any, *, label: str) -> datetime:
    if (
        not isinstance(value, str)
        or len(value) != 27
        or not value.endswith("Z")
    ):
        raise GlobalDockerInventoryError(f"{label} is not canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise GlobalDockerInventoryError(
            f"{label} is not canonical UTC"
        ) from exc
    if canonical_utc_timestamp(parsed) != value:
        raise GlobalDockerInventoryError(f"{label} is not canonical UTC")
    return parsed


def _aware_utc_datetime(value: Any, *, label: str) -> datetime:
    try:
        serialized = canonical_utc_timestamp(value)
    except GlobalDockerInventoryError as exc:
        raise GlobalDockerInventoryError(f"{label} is invalid") from exc
    return _parse_utc_timestamp(serialized, label=label)


def _prepared_project_identity(
    operation_id: str,
    role: str,
) -> tuple[str, str]:
    base = f"tb3p-{operation_id.replace('-', '')}"
    return base, f"{base}-{ROLE_PATHS[role]}"


def _prepared_manifest_path(
    *,
    operation_id: str,
    role: str,
    contract_kind: str,
) -> Path:
    if contract_kind == "finland-precommit":
        if role not in {"bot_fi", "webapp_fi"}:
            raise GlobalDockerInventoryError(
                "Finland prepared contract role differs"
            )
        return (
            SECRET_ROOT_PREFIX
            / operation_id
            / ROLE_PATHS[role]
            / "precommit-operation.json"
        )
    if contract_kind == "wa-ir-operation":
        if role != "webapp_ir":
            raise GlobalDockerInventoryError(
                "WA-IR prepared contract role differs"
            )
        return (
            PROJECT_ROOT_PREFIX
            / operation_id
            / "incoming"
            / "operation-manifest.json"
        )
    raise GlobalDockerInventoryError(
        "prepared contract kind is invalid"
    )


def new_controller_challenge() -> str:
    """Return controller-owned entropy; no CLI/caller value is accepted."""

    raw_nonce = secrets.token_bytes(32)
    return hashlib.sha256(raw_nonce).hexdigest()


def build_prepared_request(
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    role: str,
    agent_sha256: str,
    contract_worker_sha256: str,
    role_manifest_sha256: str,
    controller_challenge_sha256: str,
    issued_at: datetime,
    expires_at: datetime,
    expected_database_state: str = "running-healthy",
    baseline_bindings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    contract_kind = (
        "wa-ir-operation"
        if role == "webapp_ir"
        else "finland-precommit"
    )
    project_base, project_name = _prepared_project_identity(
        operation_id,
        role,
    )
    release_root = PROJECT_ROOT_PREFIX / operation_id / "releases" / release_sha
    worker_relative = PREPARED_WORKER_RELATIVES[contract_kind]
    baseline = dict(baseline_bindings or {})
    baseline_fields = {
        "baseline_response_sha256": None,
        "expected_prepared_container_id": None,
        "expected_prepared_network_id": None,
        "expected_prepared_config_sha256": None,
        "expected_prepared_compose_config_sha256": None,
        "expected_prepared_host_config_sha256": None,
        "expected_prepared_mounts_sha256": None,
        "expected_prepared_network_identity_sha256": None,
        "expected_prepared_network_metadata_sha256": None,
        "expected_prepared_redis_identity_sha256": None,
        "expected_prepared_redis_chain_metadata_sha256": None,
        "expected_prepared_redis_metadata_sha256": None,
        "expected_non_operation_inventory_root_sha256": None,
        "expected_non_operation_identity_root_sha256": None,
        "expected_non_operation_state_root_sha256": None,
        "expected_non_operation_metadata_root_sha256": None,
        "expected_non_operation_resource_counts": None,
    }
    if set(baseline) - set(baseline_fields):
        raise GlobalDockerInventoryError(
            "prepared baseline binding fields are not exact"
        )
    baseline_fields.update(baseline)
    document: dict[str, Any] = {
        "schema": PREPARED_REQUEST_SCHEMA,
        "action": "capture-prepared",
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "role": role,
        "expected_host": ROLE_HOSTS.get(role),
        "controller_challenge_sha256": controller_challenge_sha256,
        "issued_at": canonical_utc_timestamp(issued_at),
        "expires_at": canonical_utc_timestamp(expires_at),
        "expected_database_state": expected_database_state,
        **baseline_fields,
        "release_root": str(release_root),
        "agent_path": str(release_root / AGENT_RELATIVE),
        "agent_sha256": agent_sha256,
        "contract_kind": contract_kind,
        "contract_worker_path": str(release_root / worker_relative),
        "contract_worker_sha256": contract_worker_sha256,
        "role_manifest_path": str(
            _prepared_manifest_path(
                operation_id=operation_id,
                role=role,
                contract_kind=contract_kind,
            )
        ),
        "role_manifest_sha256": role_manifest_sha256,
        "project_base": project_base,
        "project_name": project_name,
        "request_binding_sha256": ZERO_SHA256,
    }
    document["request_binding_sha256"] = _request_binding(document)
    return validate_prepared_request(document, now=issued_at)


def build_stopped_request_from_prepared_response(
    *,
    prepared_request: Mapping[str, Any],
    prepared_response: Mapping[str, Any],
    controller_challenge_sha256: str,
    issued_at: datetime,
    expires_at: datetime,
) -> dict[str, Any]:
    if not isinstance(prepared_response, Mapping):
        raise GlobalDockerInventoryError(
            "stopped inventory baseline response is invalid"
        )
    baseline_captured_at = _parse_utc_timestamp(
        prepared_response.get("captured_at"),
        label="running prepared baseline captured_at",
    )
    prior_request = validate_prepared_request(
        prepared_request,
        now=baseline_captured_at,
    )
    prior_response = validate_prepared_response(
        prepared_response,
        request=prior_request,
        now=baseline_captured_at,
    )
    if prior_request["expected_database_state"] != "running-healthy":
        raise GlobalDockerInventoryError(
            "stopped inventory baseline must be a running prepared capture"
        )
    current_issued_at = _parse_utc_timestamp(
        canonical_utc_timestamp(issued_at),
        label="stopped prepared request issued_at",
    )
    if current_issued_at < baseline_captured_at:
        raise GlobalDockerInventoryError(
            "stopped inventory request predates its running baseline"
        )
    baseline = {
        "baseline_response_sha256": prior_response["response_sha256"],
        "expected_prepared_container_id": prior_response[
            "prepared_container_id"
        ],
        "expected_prepared_network_id": prior_response[
            "prepared_network_id"
        ],
        "expected_prepared_config_sha256": prior_response[
            "prepared_config_sha256"
        ],
        "expected_prepared_compose_config_sha256": prior_response[
            "prepared_compose_config_sha256"
        ],
        "expected_prepared_host_config_sha256": prior_response[
            "prepared_host_config_sha256"
        ],
        "expected_prepared_mounts_sha256": prior_response[
            "prepared_mounts_sha256"
        ],
        "expected_prepared_network_identity_sha256": prior_response[
            "prepared_network_identity_sha256"
        ],
        "expected_prepared_network_metadata_sha256": prior_response[
            "prepared_network_metadata_sha256"
        ],
        "expected_prepared_redis_identity_sha256": prior_response[
            "prepared_redis_identity_sha256"
        ],
        "expected_prepared_redis_chain_metadata_sha256": prior_response[
            "prepared_redis_chain_metadata_sha256"
        ],
        "expected_prepared_redis_metadata_sha256": prior_response[
            "prepared_redis_metadata_sha256"
        ],
        "expected_non_operation_inventory_root_sha256": prior_response[
            "non_operation_inventory_root_sha256"
        ],
        "expected_non_operation_identity_root_sha256": prior_response[
            "non_operation_identity_root_sha256"
        ],
        "expected_non_operation_state_root_sha256": prior_response[
            "non_operation_state_root_sha256"
        ],
        "expected_non_operation_metadata_root_sha256": prior_response[
            "non_operation_metadata_root_sha256"
        ],
        "expected_non_operation_resource_counts": prior_response[
            "non_operation_resource_counts"
        ],
    }
    return build_prepared_request(
        campaign_id=prior_request["campaign_id"],
        operation_id=prior_request["operation_id"],
        release_sha=prior_request["release_sha"],
        release_tree_sha=prior_request["release_tree_sha"],
        role=prior_request["role"],
        agent_sha256=prior_request["agent_sha256"],
        contract_worker_sha256=prior_request[
            "contract_worker_sha256"
        ],
        role_manifest_sha256=prior_request["role_manifest_sha256"],
        controller_challenge_sha256=controller_challenge_sha256,
        issued_at=issued_at,
        expires_at=expires_at,
        expected_database_state="stopped",
        baseline_bindings=baseline,
    )


def validate_prepared_request(
    value: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != PREPARED_REQUEST_FIELDS
        or value.get("schema") != PREPARED_REQUEST_SCHEMA
        or value.get("action") != "capture-prepared"
        or value.get("role") not in ROLE_NAMES
        or value.get("contract_kind") not in PREPARED_WORKER_RELATIVES
        or value.get("expected_database_state")
        not in {"running-healthy", "stopped"}
    ):
        raise GlobalDockerInventoryError(
            "prepared inventory request fields are not exact"
        )
    try:
        document = json.loads(
            canonical_json(dict(value)).decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise GlobalDockerInventoryError(
            "prepared inventory request is not JSON-compatible"
        ) from exc
    if len(canonical_json(document)) > MAX_CONTROL_BYTES:
        raise GlobalDockerInventoryError(
            "prepared inventory request exceeds the control bound"
        )
    campaign_id = _canonical_uuid4(
        document["campaign_id"],
        label="campaign ID",
    )
    operation_id = _canonical_uuid4(
        document["operation_id"],
        label="operation ID",
    )
    role = document["role"]
    contract_kind = document["contract_kind"]
    if campaign_id == operation_id:
        raise GlobalDockerInventoryError(
            "campaign and operation IDs must be distinct"
        )
    if (
        not isinstance(document["release_sha"], str)
        or SHA40_RE.fullmatch(document["release_sha"]) is None
        or not isinstance(document["release_tree_sha"], str)
        or SHA40_RE.fullmatch(document["release_tree_sha"]) is None
        or document["expected_host"] != ROLE_HOSTS[role]
        or (
            role == "webapp_ir"
            and contract_kind != "wa-ir-operation"
        )
        or (
            role != "webapp_ir"
            and contract_kind != "finland-precommit"
        )
    ):
        raise GlobalDockerInventoryError(
            "prepared inventory release, host, or contract differs"
        )
    for field in (
        "controller_challenge_sha256",
        "agent_sha256",
        "contract_worker_sha256",
        "role_manifest_sha256",
        "request_binding_sha256",
    ):
        _nonzero_sha256(document[field], label=field)
    baseline_fields = (
        "baseline_response_sha256",
        "expected_prepared_config_sha256",
        "expected_prepared_compose_config_sha256",
        "expected_prepared_host_config_sha256",
        "expected_prepared_mounts_sha256",
        "expected_prepared_network_identity_sha256",
        "expected_prepared_network_metadata_sha256",
        "expected_prepared_redis_identity_sha256",
        "expected_prepared_redis_chain_metadata_sha256",
        "expected_prepared_redis_metadata_sha256",
        "expected_non_operation_inventory_root_sha256",
        "expected_non_operation_identity_root_sha256",
        "expected_non_operation_state_root_sha256",
        "expected_non_operation_metadata_root_sha256",
    )
    if document["expected_database_state"] == "running-healthy":
        if any(
            document[field] is not None
            for field in (
                *baseline_fields,
                "expected_prepared_container_id",
                "expected_prepared_network_id",
                "expected_non_operation_resource_counts",
            )
        ):
            raise GlobalDockerInventoryError(
                "running prepared request unexpectedly has a baseline"
            )
    else:
        for field in baseline_fields:
            _nonzero_sha256(document[field], label=field)
        if (
            not isinstance(document["expected_prepared_container_id"], str)
            or CONTAINER_ID_RE.fullmatch(
                document["expected_prepared_container_id"]
            )
            is None
            or not isinstance(document["expected_prepared_network_id"], str)
            or NETWORK_ID_RE.fullmatch(
                document["expected_prepared_network_id"]
            )
            is None
        ):
            raise GlobalDockerInventoryError(
                "stopped prepared request resource baseline is invalid"
            )
        _validate_counts(
            document["expected_non_operation_resource_counts"],
            label="stopped prepared non-operation baseline counts",
        )
    issued_at = _parse_utc_timestamp(
        document["issued_at"],
        label="prepared request issued_at",
    )
    expires_at = _parse_utc_timestamp(
        document["expires_at"],
        label="prepared request expires_at",
    )
    lifetime = (expires_at - issued_at).total_seconds()
    observed_now = (
        datetime.now(timezone.utc)
        if now is None
        else _aware_utc_datetime(
            now,
            label="prepared request validation time",
        )
    )
    if (
        not PREPARED_REQUEST_MIN_LIFETIME_SECONDS
        <= lifetime
        <= PREPARED_REQUEST_MAX_LIFETIME_SECONDS
        or issued_at
        > observed_now
        + timedelta(seconds=PREPARED_REQUEST_FUTURE_SKEW_SECONDS)
        or observed_now > expires_at
    ):
        raise GlobalDockerInventoryError(
            "prepared inventory request is stale or outside its time bound"
        )
    release_root = (
        PROJECT_ROOT_PREFIX
        / operation_id
        / "releases"
        / document["release_sha"]
    )
    worker_relative = PREPARED_WORKER_RELATIVES[contract_kind]
    expected_paths = {
        "release_root": release_root,
        "agent_path": release_root / AGENT_RELATIVE,
        "contract_worker_path": release_root / worker_relative,
        "role_manifest_path": _prepared_manifest_path(
            operation_id=operation_id,
            role=role,
            contract_kind=contract_kind,
        ),
    }
    for field, expected in expected_paths.items():
        if _canonical_path(document[field], label=field) != expected:
            raise GlobalDockerInventoryError(
                f"prepared inventory {field} is not operation-derived"
            )
    project_base, project_name = _prepared_project_identity(
        operation_id,
        role,
    )
    if (
        document["project_base"] != project_base
        or document["project_name"] != project_name
        or PROJECT_RE.fullmatch(document["project_base"]) is None
        or PROJECT_RE.fullmatch(document["project_name"]) is None
    ):
        raise GlobalDockerInventoryError(
            "prepared inventory project identity differs"
        )
    if document["request_binding_sha256"] != _request_binding(document):
        raise GlobalDockerInventoryError(
            "prepared inventory request binding SHA-256 differs"
        )
    return document


def _project_identity(
    operation_id: str,
    restore_generation_sha256: str,
    role: str,
) -> tuple[str, str]:
    basis = {
        "schema": "production-shadow-frozen-final-project-v1",
        "operation_id": operation_id,
        "restore_generation_sha256": restore_generation_sha256,
        "role": role,
    }
    digest = _sha256(canonical_json(basis))
    base = f"tb3f-{digest[:48]}"
    return base, f"{base}-{ROLE_PATHS[role]}"


def build_request(
    *,
    action: str,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    restore_generation_sha256: str,
    role: str,
    agent_sha256: str,
    worker_sha256: str,
    expected_operation_container_id: str | None = None,
    expected_operation_host_config_sha256: str | None = None,
    role_manifest_path: Path | None = None,
    role_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    project_base, project_name = _project_identity(
        operation_id,
        restore_generation_sha256,
        role,
    )
    release_root = PROJECT_ROOT_PREFIX / operation_id / "releases" / release_sha
    document: dict[str, Any] = {
        "schema": REQUEST_SCHEMA,
        "action": action,
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "restore_generation_sha256": restore_generation_sha256,
        "role": role,
        "expected_host": ROLE_HOSTS.get(role),
        "release_root": str(release_root),
        "agent_path": str(release_root / AGENT_RELATIVE),
        "agent_sha256": agent_sha256,
        "worker_path": str(release_root / WORKER_RELATIVE),
        "worker_sha256": worker_sha256,
        "project_base": project_base,
        "project_name": project_name,
        "expected_operation_container_id": (
            expected_operation_container_id
        ),
        "expected_operation_host_config_sha256": (
            expected_operation_host_config_sha256
        ),
        "role_manifest_path": (
            str(role_manifest_path) if role_manifest_path is not None else None
        ),
        "role_manifest_sha256": role_manifest_sha256,
        "request_binding_sha256": ZERO_SHA256,
    }
    document["request_binding_sha256"] = _request_binding(document)
    return validate_request(document)


def validate_request(value: Mapping[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != REQUEST_FIELDS
        or value["schema"] != REQUEST_SCHEMA
        or value["action"] not in {"capture-before", "capture-after"}
        or value["role"] not in ROLE_NAMES
    ):
        raise GlobalDockerInventoryError(
            "inventory request fields are not exact"
        )
    try:
        document = json.loads(
            canonical_json(dict(value)).decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise GlobalDockerInventoryError(
            "inventory request is not JSON-compatible"
        ) from exc
    if len(canonical_json(document)) > MAX_CONTROL_BYTES:
        raise GlobalDockerInventoryError(
            "inventory request exceeds the control bound"
        )
    campaign_id = _canonical_uuid4(
        document["campaign_id"],
        label="campaign ID",
    )
    operation_id = _canonical_uuid4(
        document["operation_id"],
        label="operation ID",
    )
    if campaign_id == operation_id:
        raise GlobalDockerInventoryError(
            "campaign and operation IDs must be distinct"
        )
    role = document["role"]
    if (
        not isinstance(document["release_sha"], str)
        or SHA40_RE.fullmatch(document["release_sha"]) is None
        or not isinstance(document["release_tree_sha"], str)
        or SHA40_RE.fullmatch(document["release_tree_sha"]) is None
        or document["expected_host"] != ROLE_HOSTS[role]
    ):
        raise GlobalDockerInventoryError(
            "inventory request release or host identity differs"
        )
    for field in (
        "restore_generation_sha256",
        "agent_sha256",
        "worker_sha256",
        "request_binding_sha256",
    ):
        _nonzero_sha256(document[field], label=field)
    release_root = (
        PROJECT_ROOT_PREFIX
        / operation_id
        / "releases"
        / document["release_sha"]
    )
    expected_paths = {
        "release_root": release_root,
        "agent_path": release_root / AGENT_RELATIVE,
        "worker_path": release_root / WORKER_RELATIVE,
    }
    for field, expected in expected_paths.items():
        if _canonical_path(document[field], label=field) != expected:
            raise GlobalDockerInventoryError(
                f"inventory request {field} is not release-derived"
            )
    project_base, project_name = _project_identity(
        operation_id,
        document["restore_generation_sha256"],
        role,
    )
    if (
        document["project_base"] != project_base
        or document["project_name"] != project_name
        or PROJECT_RE.fullmatch(document["project_base"]) is None
        or PROJECT_RE.fullmatch(document["project_name"]) is None
    ):
        raise GlobalDockerInventoryError(
            "inventory request project identity differs"
        )
    expected_container = document["expected_operation_container_id"]
    expected_host_config_sha256 = document[
        "expected_operation_host_config_sha256"
    ]
    manifest_path = document["role_manifest_path"]
    manifest_sha256 = document["role_manifest_sha256"]
    if document["action"] == "capture-before":
        if any(
            item is not None
            for item in (
                expected_container,
                expected_host_config_sha256,
                manifest_path,
                manifest_sha256,
            )
        ):
            raise GlobalDockerInventoryError(
                "before inventory unexpectedly carries post-restore inputs"
            )
    else:
        if (
            not isinstance(expected_container, str)
            or CONTAINER_ID_RE.fullmatch(expected_container) is None
            or not isinstance(expected_host_config_sha256, str)
            or SHA256_RE.fullmatch(expected_host_config_sha256) is None
            or expected_host_config_sha256 == ZERO_SHA256
            or manifest_path is None
            or manifest_sha256 is None
        ):
            raise GlobalDockerInventoryError(
                "after inventory lacks exact restore resource bindings"
            )
        expected_manifest_path = (
            SECRET_ROOT_PREFIX
            / operation_id
            / "frozen-final-generations"
            / document["restore_generation_sha256"]
            / ROLE_PATHS[role]
            / "restore-role-manifest.json"
        )
        if (
            _canonical_path(
                manifest_path,
                label="role manifest path",
            )
            != expected_manifest_path
        ):
            raise GlobalDockerInventoryError(
                "role manifest path is not generation-derived"
            )
        _nonzero_sha256(manifest_sha256, label="role manifest SHA-256")
    if document["request_binding_sha256"] != _request_binding(document):
        raise GlobalDockerInventoryError(
            "inventory request binding SHA-256 differs"
        )
    return document


def _validate_counts(value: Any, *, label: str) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != COUNT_FIELDS:
        raise GlobalDockerInventoryError(f"{label} fields are not exact")
    result: dict[str, int] = {}
    for kind in RESOURCE_KINDS:
        count = value[kind]
        if type(count) is not int or not 0 <= count <= MAX_RESOURCES_PER_KIND:
            raise GlobalDockerInventoryError(f"{label} is invalid")
        result[kind] = count
    return result


def validate_response(
    value: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    request = validate_request(request)
    if (
        not isinstance(value, Mapping)
        or set(value) != RESPONSE_FIELDS
    ):
        raise GlobalDockerInventoryError(
            "inventory response fields are not exact"
        )
    document = json.loads(canonical_json(dict(value)).decode("ascii"))
    if len(canonical_json(document)) > MAX_RESPONSE_BYTES:
        raise GlobalDockerInventoryError(
            "inventory response exceeds its bound"
        )
    if (
        document["schema"] != RESPONSE_SCHEMA
        or document["status"] != "captured-stable"
        or any(
            document[field] != request[field]
            for field in (
                "action",
                "campaign_id",
                "operation_id",
                "release_sha",
                "release_tree_sha",
                "restore_generation_sha256",
                "role",
                "expected_host",
                "project_base",
                "project_name",
                "request_binding_sha256",
                "expected_operation_container_id",
                "expected_operation_host_config_sha256",
            )
        )
        or document["stable_capture_count"] != 2
        or document["descriptors_returned"] is not False
        or document["docker_read_only"] is not True
        or document["network_io_performed"] is not False
        or document["filesystem_mutated"] is not False
        or not isinstance(document["observed_host_ipv4"], list)
        or document["observed_host_ipv4"]
        != sorted(set(document["observed_host_ipv4"]))
        or request["expected_host"] not in document["observed_host_ipv4"]
    ):
        raise GlobalDockerInventoryError(
            "inventory response identity or safety boundary differs"
        )
    observed_host_config_sha256 = document[
        "observed_operation_host_config_sha256"
    ]
    if request["action"] == "capture-before":
        if observed_host_config_sha256 is not None:
            raise GlobalDockerInventoryError(
                "before inventory unexpectedly observed operation HostConfig"
            )
    elif (
        not isinstance(observed_host_config_sha256, str)
        or observed_host_config_sha256
        != request["expected_operation_host_config_sha256"]
    ):
        raise GlobalDockerInventoryError(
            "operation HostConfig digest differs"
        )
    else:
        _nonzero_sha256(
            observed_host_config_sha256,
            label="observed operation HostConfig",
        )
    for address in document["observed_host_ipv4"]:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise GlobalDockerInventoryError(
                "inventory response contains an invalid host address"
            ) from exc
        if parsed.version != 4 or str(parsed) != address:
            raise GlobalDockerInventoryError(
                "inventory response contains a non-canonical host address"
            )
    for field in (
        "inventory_root_sha256",
        "inventory_identity_root_sha256",
        "inventory_state_root_sha256",
        "inventory_metadata_root_sha256",
        "non_operation_inventory_root_sha256",
        "non_operation_identity_root_sha256",
        "non_operation_state_root_sha256",
        "non_operation_metadata_root_sha256",
        "operation_resource_root_sha256",
        "response_sha256",
    ):
        _nonzero_sha256(document[field], label=field)
    total = _validate_counts(
        document["resource_counts"],
        label="resource counts",
    )
    non_operation = _validate_counts(
        document["non_operation_resource_counts"],
        label="non-operation resource counts",
    )
    operation = _validate_counts(
        document["operation_resource_counts"],
        label="operation resource counts",
    )
    if any(
        total[kind] != non_operation[kind] + operation[kind]
        for kind in RESOURCE_KINDS
    ):
        raise GlobalDockerInventoryError(
            "inventory response resource partition differs"
        )
    expected_operation = (
        {kind: 0 for kind in RESOURCE_KINDS}
        if request["action"] == "capture-before"
        else {"container": 1, "network": 1, "volume": 0, "image": 0}
    )
    if operation != expected_operation:
        raise GlobalDockerInventoryError(
            "inventory response operation resource closure differs"
        )
    unsigned = {
        key: item
        for key, item in document.items()
        if key != "response_sha256"
    }
    if document["response_sha256"] != _sha256(canonical_json(unsigned)):
        raise GlobalDockerInventoryError(
            "inventory response SHA-256 differs"
        )
    return document


def _validate_read_only_docker_command(command: Sequence[str]) -> None:
    if tuple(command[: len(DOCKER_BASE)]) != DOCKER_BASE:
        raise GlobalDockerInventoryError(
            "inventory command is not exact Docker inspection"
        )
    exact_lists = {
        (*DOCKER_BASE, "ps", "--all", "--quiet", "--no-trunc"),
        (*DOCKER_BASE, "network", "ls", "--quiet", "--no-trunc"),
        (*DOCKER_BASE, "volume", "ls", "--quiet"),
        (
            *DOCKER_BASE,
            "image",
            "ls",
            "--all",
            "--quiet",
            "--no-trunc",
        ),
    }
    inspect_prefixes = {
        (*DOCKER_BASE, "inspect"),
        (*DOCKER_BASE, "network", "inspect"),
        (*DOCKER_BASE, "volume", "inspect"),
        (*DOCKER_BASE, "image", "inspect"),
    }
    if tuple(command) in exact_lists:
        return
    compose_prefix = (*DOCKER_BASE, "compose")
    if (
        tuple(command[: len(compose_prefix)]) == compose_prefix
        and len(command) == len(compose_prefix) + 9
        and command[len(compose_prefix)] == "--project-name"
        and isinstance(command[len(compose_prefix) + 1], str)
        and PROJECT_RE.fullmatch(command[len(compose_prefix) + 1])
        is not None
        and command[len(compose_prefix) + 2] == "--env-file"
        and command[len(compose_prefix) + 4] == "--file"
        and command[len(compose_prefix) + 6] == "config"
        and tuple(command[len(compose_prefix) + 7 :])
        in {
            ("--format", "json"),
            ("--hash", command[-1]),
        }
    ):
        environment = Path(command[len(compose_prefix) + 3])
        compose = Path(command[len(compose_prefix) + 5])
        terminal = tuple(command[len(compose_prefix) + 7 :])
        if (
            environment.is_absolute()
            and environment == Path(os.path.abspath(environment))
            and SECRET_ROOT_PREFIX in environment.parents
            and compose.is_absolute()
            and compose == Path(os.path.abspath(compose))
            and PROJECT_ROOT_PREFIX in compose.parents
            and (
                terminal == ("--format", "json")
                or (
                    terminal[0] == "--hash"
                    and isinstance(terminal[1], str)
                    and re.fullmatch(
                        r"[a-z_][a-z0-9_]{0,62}",
                        terminal[1],
                    )
                    is not None
                )
            )
        ):
            return
    for prefix in inspect_prefixes:
        if (
            tuple(command[: len(prefix)]) == prefix
            and 1 <= len(command) - len(prefix) <= MAX_INSPECT_CHUNK
            and all(
                isinstance(item, str)
                and item
                and not item.startswith("-")
                and "\x00" not in item
                for item in command[len(prefix) :]
            )
        ):
            return
    else:
        raise GlobalDockerInventoryError(
            "inventory attempted a non-read-only Docker command"
        )


def _run_git(arguments: Sequence[str]) -> str:
    if (
        len(arguments) < 4
        or arguments[0] != GIT
        or arguments[1] != "-C"
    ):
        raise GlobalDockerInventoryError(
            "immutable release verification command lacks an exact work tree"
        )
    work_tree = Path(arguments[2])
    tail = list(arguments[3:])
    if (
        not work_tree.is_absolute()
        or work_tree != Path(os.path.abspath(work_tree))
        or ".." in work_tree.parts
        or any(
            argument == "-C"
            or argument.startswith("--git-dir")
            or argument.startswith("--work-tree")
            for argument in tail
        )
    ):
        raise GlobalDockerInventoryError(
            "immutable release Git work tree is not canonical"
        )
    command = [
        GIT,
        *GIT_CONFIG_ARGUMENTS,
        f"--git-dir={work_tree / '.git'}",
        f"--work-tree={work_tree}",
        *tail,
    ]
    try:
        result = _bounded_command(
            command,
            timeout=30,
            env=SAFE_GIT_ENV,
            stdout_limit=MAX_COMMAND_OUTPUT_BYTES,
            stderr_limit=MAX_COMMAND_ERROR_BYTES,
        )
    except BoundedCommandError as exc:
        raise GlobalDockerInventoryError(
            "immutable release verification is unavailable"
        ) from exc
    if result.returncode != 0:
        raise GlobalDockerInventoryError(
            "immutable release verification failed closed"
        )
    try:
        return result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise GlobalDockerInventoryError(
            "immutable release verification returned invalid output"
        ) from exc


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _secure_file_sha256(
    path: Path,
    *,
    expected_sha256: str,
    label: str,
) -> None:
    directory_fd = -1
    descriptor = -1
    try:
        directory_fd = os.open("/", _directory_flags())
        for component in path.parts[1:-1]:
            child = os.open(
                component,
                _directory_flags(),
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = child
            metadata = os.fstat(directory_fd)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or metadata.st_gid != 0
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                raise GlobalDockerInventoryError(
                    f"{label} ancestry is unsafe"
                )
        descriptor = os.open(
            path.name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) not in {0o644, 0o755}
            or not 1 <= before.st_size <= 16 * 1024 * 1024
        ):
            raise GlobalDockerInventoryError(
                f"{label} is not an exact immutable release file"
            )
        digest = hashlib.sha256()
        observed = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            observed += len(chunk)
            if observed > 16 * 1024 * 1024:
                raise GlobalDockerInventoryError(
                    f"{label} exceeds its bound"
                )
            digest.update(chunk)
        after = os.fstat(descriptor)
        visible = os.stat(
            path.name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if (
            observed != before.st_size
            or digest.hexdigest() != expected_sha256
            or any(
                getattr(before, field) != getattr(after, field)
                or getattr(before, field) != getattr(visible, field)
                for field in stable_fields
            )
        ):
            raise GlobalDockerInventoryError(f"{label} identity differs")
    except GlobalDockerInventoryError:
        raise
    except OSError as exc:
        raise GlobalDockerInventoryError(
            f"{label} is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if directory_fd >= 0:
            os.close(directory_fd)


def observe_local_ipv4_addresses() -> set[str]:
    addresses: set[str] = set()
    try:
        interfaces = socket.if_nameindex()
        handle = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError as exc:
        raise GlobalDockerInventoryError(
            "cannot inspect local IPv4 identity"
        ) from exc
    try:
        for _, name in interfaces:
            try:
                packed = struct.pack("256s", name.encode("ascii")[:15])
                result = fcntl.ioctl(handle.fileno(), 0x8915, packed)
            except (OSError, UnicodeEncodeError):
                continue
            addresses.add(socket.inet_ntoa(result[20:24]))
    finally:
        handle.close()
    if not addresses:
        raise GlobalDockerInventoryError(
            "local host has no observable IPv4 identity"
        )
    return addresses


def _verify_execution_context(
    request: Mapping[str, Any],
    *,
    observed_host_addresses: set[str] | None,
) -> tuple[Any, list[str]]:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise GlobalDockerInventoryError(
            "global Docker inventory agent requires root:root"
        )
    if os.environ.get("PYTHONDONTWRITEBYTECODE") != "1":
        raise GlobalDockerInventoryError(
            "immutable release execution requires PYTHONDONTWRITEBYTECODE=1"
        )
    if not sys.flags.isolated:
        raise GlobalDockerInventoryError(
            "immutable release execution requires isolated Python"
        )
    reserved = sorted(
        key
        for key in os.environ
        if key in RESERVED_PROCESS_ENVIRONMENT or key.startswith("COMPOSE_")
    )
    if reserved:
        raise GlobalDockerInventoryError(
            "reserved Docker or Compose environment is present"
        )
    addresses = (
        observe_local_ipv4_addresses()
        if observed_host_addresses is None
        else set(observed_host_addresses)
    )
    canonical_addresses: list[str] = []
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise GlobalDockerInventoryError(
                "observed host address is invalid"
            ) from exc
        if parsed.version != 4 or str(parsed) != address:
            raise GlobalDockerInventoryError(
                "observed host address is not canonical IPv4"
            )
        canonical_addresses.append(address)
    if request["expected_host"] not in addresses:
        raise GlobalDockerInventoryError(
            "local IPv4 identity differs from the requested role host"
        )
    release_root = Path(request["release_root"])
    try:
        release_metadata = release_root.stat(follow_symlinks=False)
    except OSError as exc:
        raise GlobalDockerInventoryError(
            "immutable release root is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(release_metadata.st_mode)
        or release_metadata.st_uid != 0
        or release_metadata.st_gid != 0
        or stat.S_IMODE(release_metadata.st_mode) & 0o022
        or Path(__file__).resolve() != Path(request["agent_path"])
    ):
        raise GlobalDockerInventoryError(
            "inventory agent is not running from the immutable release"
        )
    if (
        _run_git([GIT, "-C", str(release_root), "rev-parse", "HEAD"])
        != request["release_sha"]
        or _run_git(
            [GIT, "-C", str(release_root), "rev-parse", "HEAD^{tree}"]
        )
        != request["release_tree_sha"]
        or _run_git(
            [GIT, "-C", str(release_root), "branch", "--show-current"]
        )
        != ""
        or _run_git(
            [
                GIT,
                "-C",
                str(release_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--ignore-submodules=none",
            ]
        )
        != ""
        or _run_git(
            [
                GIT,
                "-C",
                str(release_root),
                "ls-files",
                "--others",
                "--exclude-standard",
            ]
        )
        != ""
        or _run_git(
            [
                GIT,
                "-C",
                str(release_root),
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
            ]
        )
        != ""
    ):
        raise GlobalDockerInventoryError(
            "immutable release is not detached, exact, and clean"
        )
    _verify_git_index_visibility(release_root)
    for path_field, sha_field, relative, label in (
        ("agent_path", "agent_sha256", AGENT_RELATIVE, "inventory agent"),
        ("worker_path", "worker_sha256", WORKER_RELATIVE, "restore worker"),
    ):
        tracked = _run_git(
            [
                GIT,
                "-C",
                str(release_root),
                "ls-files",
                "--stage",
                "--",
                str(relative),
            ]
        )
        if re.fullmatch(
            rf"100(?:644|755) [0-9a-f]{{40}} 0\t{re.escape(str(relative))}",
            tracked,
        ) is None:
            raise GlobalDockerInventoryError(
                f"{label} is not an exact tracked release file"
            )
        _secure_file_sha256(
            Path(request[path_field]),
            expected_sha256=request[sha_field],
            label=label,
        )
    worker = _load_exact_release_worker(
        release_root=release_root,
        worker_path=Path(request["worker_path"]),
    )
    paths = worker.runtime_paths(
        request["operation_id"],
        request["release_sha"],
        request["restore_generation_sha256"],
        request["role"],
    )
    if (
        Path(worker.__file__).resolve() != Path(request["worker_path"])
        or paths.project_base != request["project_base"]
        or paths.project_name != request["project_name"]
        or paths.release_root != release_root
    ):
        raise GlobalDockerInventoryError(
            "restore worker runtime-path semantics differ"
        )
    return worker, sorted(canonical_addresses)


def _secure_root_manifest_sha256(
    path: Path,
    *,
    expected_sha256: str,
    label: str,
) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or not 1 <= before.st_size <= 16 * 1024 * 1024
        ):
            raise GlobalDockerInventoryError(
                f"{label} is unavailable or unsafe"
            )
        digest = hashlib.sha256()
        observed = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            observed += len(chunk)
            if observed > 16 * 1024 * 1024:
                raise GlobalDockerInventoryError(
                    f"{label} exceeds its bound"
                )
            digest.update(chunk)
        after = os.fstat(descriptor)
        visible = os.stat(path, follow_symlinks=False)
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if (
            observed != before.st_size
            or digest.hexdigest() != expected_sha256
            or any(
                getattr(before, field) != getattr(after, field)
                or getattr(before, field) != getattr(visible, field)
                for field in stable_fields
            )
        ):
            raise GlobalDockerInventoryError(f"{label} identity differs")
    except GlobalDockerInventoryError:
        raise
    except OSError as exc:
        raise GlobalDockerInventoryError(
            f"{label} is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _verify_prepared_execution_context(
    request: Mapping[str, Any],
    *,
    observed_host_addresses: set[str] | None,
) -> tuple[Any, Any, Any, list[str]]:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise GlobalDockerInventoryError(
            "prepared Docker inventory agent requires root:root"
        )
    if os.environ.get("PYTHONDONTWRITEBYTECODE") != "1":
        raise GlobalDockerInventoryError(
            "immutable release execution requires PYTHONDONTWRITEBYTECODE=1"
        )
    if not sys.flags.isolated:
        raise GlobalDockerInventoryError(
            "immutable release execution requires isolated Python"
        )
    reserved = sorted(
        key
        for key in os.environ
        if key in RESERVED_PROCESS_ENVIRONMENT or key.startswith("COMPOSE_")
    )
    if reserved:
        raise GlobalDockerInventoryError(
            "reserved Docker or Compose environment is present"
        )
    addresses = (
        observe_local_ipv4_addresses()
        if observed_host_addresses is None
        else set(observed_host_addresses)
    )
    canonical_addresses: list[str] = []
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise GlobalDockerInventoryError(
                "observed host address is invalid"
            ) from exc
        if parsed.version != 4 or str(parsed) != address:
            raise GlobalDockerInventoryError(
                "observed host address is not canonical IPv4"
            )
        canonical_addresses.append(address)
    if request["expected_host"] not in addresses:
        raise GlobalDockerInventoryError(
            "local IPv4 identity differs from the requested role host"
        )
    release_root = Path(request["release_root"])
    try:
        release_metadata = release_root.stat(follow_symlinks=False)
    except OSError as exc:
        raise GlobalDockerInventoryError(
            "immutable release root is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(release_metadata.st_mode)
        or release_metadata.st_uid != 0
        or release_metadata.st_gid != 0
        or stat.S_IMODE(release_metadata.st_mode) & 0o022
        or Path(__file__).resolve() != Path(request["agent_path"])
    ):
        raise GlobalDockerInventoryError(
            "prepared inventory agent is not running from the immutable release"
        )
    if (
        _run_git([GIT, "-C", str(release_root), "rev-parse", "HEAD"])
        != request["release_sha"]
        or _run_git(
            [GIT, "-C", str(release_root), "rev-parse", "HEAD^{tree}"]
        )
        != request["release_tree_sha"]
        or _run_git(
            [GIT, "-C", str(release_root), "branch", "--show-current"]
        )
        != ""
        or _run_git(
            [
                GIT,
                "-C",
                str(release_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--ignore-submodules=none",
            ]
        )
        != ""
        or _run_git(
            [
                GIT,
                "-C",
                str(release_root),
                "ls-files",
                "--others",
                "--exclude-standard",
            ]
        )
        != ""
        or _run_git(
            [
                GIT,
                "-C",
                str(release_root),
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
            ]
        )
        != ""
    ):
        raise GlobalDockerInventoryError(
            "immutable release is not detached, exact, and clean"
        )
    _verify_git_index_visibility(release_root)
    worker_relative = PREPARED_WORKER_RELATIVES[
        request["contract_kind"]
    ]
    for path_field, sha_field, relative, label in (
        ("agent_path", "agent_sha256", AGENT_RELATIVE, "inventory agent"),
        (
            "contract_worker_path",
            "contract_worker_sha256",
            worker_relative,
            "prepared contract worker",
        ),
    ):
        tracked = _run_git(
            [
                GIT,
                "-C",
                str(release_root),
                "ls-files",
                "--stage",
                "--",
                str(relative),
            ]
        )
        if re.fullmatch(
            rf"100(?:644|755) [0-9a-f]{{40}} 0\t{re.escape(str(relative))}",
            tracked,
        ) is None:
            raise GlobalDockerInventoryError(
                f"{label} is not an exact tracked release file"
            )
        _secure_file_sha256(
            Path(request[path_field]),
            expected_sha256=request[sha_field],
            label=label,
        )
    worker = _load_exact_release_contract_worker(
        release_root=release_root,
        worker_path=Path(request["contract_worker_path"]),
        contract_kind=request["contract_kind"],
    )
    manifest_path = Path(request["role_manifest_path"])
    _secure_root_manifest_sha256(
        manifest_path,
        expected_sha256=request["role_manifest_sha256"],
        label="prepared role manifest",
    )
    try:
        if request["contract_kind"] == "finland-precommit":
            manifest = worker.load_manifest(manifest_path)
            paths = worker.operation_paths(
                request["operation_id"],
                request["release_sha"],
                request["role"],
            )
            expected_worker_sha256 = manifest.worker_sha256
        else:
            manifest = worker.load_manifest(
                manifest_path,
                required_uid=0,
            )
            paths = worker._canonical_operation_paths(manifest)  # noqa: SLF001
            expected_worker_sha256 = manifest.bootstrap_sha256
    except Exception as exc:
        raise GlobalDockerInventoryError(
            "prepared role manifest cannot be validated by exact release code"
        ) from exc
    if (
        Path(worker.__file__).resolve()
        != Path(request["contract_worker_path"])
        or manifest.canonical_sha256 != request["role_manifest_sha256"]
        or manifest.operation_id != request["operation_id"]
        or manifest.release_sha != request["release_sha"]
        or manifest.release_tree_sha != request["release_tree_sha"]
        or expected_worker_sha256 != request["contract_worker_sha256"]
        or paths.project_name != request["project_name"]
        or paths.release_root != release_root
        or (
            request["contract_kind"] == "finland-precommit"
            and (
                manifest.role != request["role"]
                or paths.project_base != request["project_base"]
                or paths.manifest != manifest_path
            )
        )
        or (
            request["contract_kind"] == "wa-ir-operation"
            and (
                request["role"] != "webapp_ir"
                or request["project_base"]
                != f"tb3p-{request['operation_id'].replace('-', '')}"
            )
        )
    ):
        raise GlobalDockerInventoryError(
            "prepared release worker or manifest semantics differ"
        )
    return worker, manifest, paths, sorted(canonical_addresses)


def _verify_git_index_visibility(release_root: Path) -> None:
    index_rows = _run_git(
        [
            GIT,
            "-C",
            str(release_root),
            "ls-files",
            "-v",
            "--full-name",
        ]
    ).splitlines()
    if not index_rows or any(not row.startswith("H ") for row in index_rows):
        raise GlobalDockerInventoryError(
            "immutable release index contains hidden tracked state"
        )


def _load_exact_release_worker(
    *,
    release_root: Path,
    worker_path: Path,
) -> Any:
    scripts_root = release_root / "scripts"
    release_parent = str(release_root)
    if release_parent in sys.path:
        sys.path.remove(release_parent)
    sys.path.insert(0, release_parent)
    package = sys.modules.get("scripts")
    if package is not None:
        package_paths = getattr(package, "__path__", ())
        try:
            resolved_paths = {
                Path(item).resolve(strict=True) for item in package_paths
            }
        except (OSError, TypeError) as exc:
            raise GlobalDockerInventoryError(
                "loaded scripts package identity is invalid"
            ) from exc
        if resolved_paths != {scripts_root.resolve(strict=True)}:
            raise GlobalDockerInventoryError(
                "loaded scripts package is not release-owned"
            )
    else:
        package = types.ModuleType("scripts")
        package.__file__ = None
        package.__package__ = "scripts"
        package.__path__ = [str(scripts_root)]
        package.__spec__ = importlib.machinery.ModuleSpec(
            "scripts",
            loader=None,
            is_package=True,
        )
        package.__spec__.submodule_search_locations = [str(scripts_root)]
        sys.modules["scripts"] = package
    module_name = "scripts.production_shadow_frozen_final_restore_worker"
    loaded = sys.modules.get(module_name)
    if loaded is not None:
        try:
            loaded_path = Path(loaded.__file__).resolve(strict=True)
        except (AttributeError, OSError, TypeError) as exc:
            raise GlobalDockerInventoryError(
                "loaded restore worker identity is invalid"
            ) from exc
        if loaded_path != worker_path.resolve(strict=True):
            raise GlobalDockerInventoryError(
                "loaded restore worker is not release-owned"
            )
        return loaded
    spec = importlib.util.spec_from_file_location(module_name, worker_path)
    if spec is None or spec.loader is None:
        raise GlobalDockerInventoryError(
            "exact release restore worker cannot be imported"
        )
    worker = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = worker
    try:
        spec.loader.exec_module(worker)
    except (ImportError, OSError, RuntimeError, SyntaxError) as exc:
        sys.modules.pop(module_name, None)
        raise GlobalDockerInventoryError(
            "exact release restore worker cannot be imported"
        ) from exc
    return worker


def _load_exact_release_contract_worker(
    *,
    release_root: Path,
    worker_path: Path,
    contract_kind: str,
) -> Any:
    relative = PREPARED_WORKER_RELATIVES.get(contract_kind)
    if relative is None or worker_path != release_root / relative:
        raise GlobalDockerInventoryError(
            "prepared contract worker path differs"
        )
    scripts_root = release_root / "scripts"
    release_parent = str(release_root)
    while release_parent in sys.path:
        sys.path.remove(release_parent)
    sys.path.insert(0, release_parent)
    package = sys.modules.get("scripts")
    if package is not None:
        package_paths = getattr(package, "__path__", ())
        try:
            resolved_paths = {
                Path(item).resolve(strict=True) for item in package_paths
            }
        except (OSError, TypeError) as exc:
            raise GlobalDockerInventoryError(
                "loaded scripts package identity is invalid"
            ) from exc
        if resolved_paths != {scripts_root.resolve(strict=True)}:
            raise GlobalDockerInventoryError(
                "loaded scripts package is not release-owned"
            )
    else:
        package = types.ModuleType("scripts")
        package.__file__ = None
        package.__package__ = "scripts"
        package.__path__ = [str(scripts_root)]
        package.__spec__ = importlib.machinery.ModuleSpec(
            "scripts",
            loader=None,
            is_package=True,
        )
        package.__spec__.submodule_search_locations = [str(scripts_root)]
        sys.modules["scripts"] = package
    module_name = f"scripts.{relative.stem}"
    loaded = sys.modules.get(module_name)
    if loaded is not None:
        try:
            loaded_path = Path(loaded.__file__).resolve(strict=True)
        except (AttributeError, OSError, TypeError) as exc:
            raise GlobalDockerInventoryError(
                "loaded prepared worker identity is invalid"
            ) from exc
        if loaded_path != worker_path.resolve(strict=True):
            raise GlobalDockerInventoryError(
                "loaded prepared worker is not release-owned"
            )
        return loaded
    spec = importlib.util.spec_from_file_location(module_name, worker_path)
    if spec is None or spec.loader is None:
        raise GlobalDockerInventoryError(
            "exact prepared contract worker cannot be imported"
        )
    worker = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = worker
    try:
        spec.loader.exec_module(worker)
    except (ImportError, OSError, RuntimeError, SyntaxError) as exc:
        sys.modules.pop(module_name, None)
        raise GlobalDockerInventoryError(
            "exact prepared contract worker cannot be imported"
        ) from exc
    return worker


def _parse_lines(
    raw: str,
    *,
    pattern: re.Pattern[str],
    label: str,
    allow_duplicates: bool = False,
) -> list[str]:
    values = raw.splitlines() if raw else []
    if (
        len(values) > MAX_RESOURCES_PER_KIND
        or (not allow_duplicates and len(values) != len(set(values)))
        or any(pattern.fullmatch(item) is None for item in values)
    ):
        raise GlobalDockerInventoryError(f"{label} is invalid")
    return sorted(values)


def _load_inspect_rows(
    runner: DockerRunner,
    *,
    command_prefix: Sequence[str],
    identifiers: Sequence[str],
    identity_field: str,
    label: str,
) -> dict[str, Mapping[str, Any]]:
    rows: dict[str, Mapping[str, Any]] = {}
    for offset in range(0, len(identifiers), MAX_INSPECT_CHUNK):
        chunk = list(identifiers[offset : offset + MAX_INSPECT_CHUNK])
        raw = runner.run([*command_prefix, *chunk], timeout=30)
        try:
            document = json.loads(
                raw,
                object_pairs_hook=_strict_object,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    ValueError(f"invalid constant {token}")
                ),
            )
        except (ValueError, json.JSONDecodeError) as exc:
            raise GlobalDockerInventoryError(
                f"{label} is not strict JSON"
            ) from exc
        if not isinstance(document, list) or len(document) != len(chunk):
            raise GlobalDockerInventoryError(
                f"{label} response cardinality differs"
            )
        for row in document:
            if not isinstance(row, dict):
                raise GlobalDockerInventoryError(
                    f"{label} contains a non-object"
                )
            identity = row.get(identity_field)
            if identity not in chunk or identity in rows:
                raise GlobalDockerInventoryError(
                    f"{label} identity closure differs"
                )
            rows[identity] = row
    if set(rows) != set(identifiers):
        raise GlobalDockerInventoryError(
            f"{label} inspection set differs"
        )
    return rows


def _operation_label_match(
    labels: Mapping[str, str],
    *,
    operation_id: str,
    project_base: str,
    project_name: str,
) -> bool:
    project = labels.get("com.docker.compose.project")
    operation = labels.get("trading-bot.production.operation-id")
    return (
        operation == operation_id
        or project == project_name
        or (
            isinstance(project, str)
            and (
                project == project_base
                or project.startswith(f"{project_base}-")
                or project.startswith(f"{project_base}_")
            )
        )
    )


def _name_operation_match(
    name: str,
    *,
    project_base: str,
    project_name: str,
) -> bool:
    normalized = name[1:] if name.startswith("/") else name
    return any(
        normalized == prefix
        or normalized.startswith(f"{prefix}-")
        or normalized.startswith(f"{prefix}_")
        for prefix in (project_base, project_name)
    )


def _container_descriptor(
    row: Mapping[str, Any],
    *,
    operation_id: str,
    project_base: str,
    project_name: str,
) -> ResourceDescriptor:
    identifier = row.get("Id")
    name = row.get("Name")
    image = row.get("Image")
    config = row.get("Config")
    host = row.get("HostConfig")
    state = row.get("State")
    network_settings = row.get("NetworkSettings")
    mounts = row.get("Mounts")
    if (
        not isinstance(identifier, str)
        or CONTAINER_ID_RE.fullmatch(identifier) is None
        or not isinstance(name, str)
        or not 1 <= len(name) <= 256
        or not isinstance(image, str)
        or IMAGE_ID_RE.fullmatch(image) is None
        or not isinstance(config, dict)
        or not isinstance(host, dict)
        or set(config) != CONTAINER_CONFIG_FIELDS
        or set(host) - HOST_CONFIG_FIELDS
        or (HOST_CONFIG_FIELDS - OPTIONAL_HOST_CONFIG_FIELDS) - set(host)
        or not isinstance(state, dict)
        or not isinstance(network_settings, dict)
        or not isinstance(mounts, list)
        or len(mounts) > MAX_MOUNTS
    ):
        raise GlobalDockerInventoryError(
            "container inspection shape is invalid"
        )
    name = _safe_string(name, label="container name", maximum=256)
    labels = _labels(config.get("Labels"), label="container labels")
    status = state.get("Status")
    if status not in {
        "created",
        "running",
        "paused",
        "restarting",
        "removing",
        "exited",
        "dead",
    }:
        raise GlobalDockerInventoryError(
            "container state status is invalid"
        )
    state_flags: dict[str, Any] = {"status": status}
    for field in ("Running", "Paused", "Restarting", "OOMKilled", "Dead"):
        if not isinstance(state.get(field), bool):
            raise GlobalDockerInventoryError(
                "container state flags are invalid"
            )
        state_flags[field.lower()] = state[field]
    exit_code = state.get("ExitCode")
    restart_count = row.get("RestartCount", 0)
    if (
        type(exit_code) is not int
        or not -(2**31) <= exit_code < 2**31
        or type(restart_count) is not int
        or not 0 <= restart_count < 2**63
    ):
        raise GlobalDockerInventoryError(
            "container state counters are invalid"
        )
    state_flags["exit_code"] = exit_code
    state_flags["restart_count"] = restart_count
    health = state.get("Health")
    if health is not None:
        if (
            not isinstance(health, dict)
            or health.get("Status")
            not in {"none", "starting", "healthy", "unhealthy"}
        ):
            raise GlobalDockerInventoryError(
                "container health state is invalid"
            )
        state_flags["health_status"] = health["Status"]
    networks = network_settings.get("Networks")
    if (
        not isinstance(networks, dict)
        or len(networks) > MAX_NETWORK_ATTACHMENTS
    ):
        raise GlobalDockerInventoryError(
            "container network attachment set is invalid"
        )
    normalized_networks: list[dict[str, Any]] = []
    for network_name, attachment in sorted(networks.items()):
        network_name = _safe_string(
            network_name,
            label="container network name",
            maximum=256,
        )
        if not isinstance(attachment, dict):
            raise GlobalDockerInventoryError(
                "container network attachment is invalid"
            )
        network_id = attachment.get("NetworkID")
        endpoint_id = attachment.get("EndpointID")
        if (
            not isinstance(network_id, str)
            or (
                network_id != ""
                and NETWORK_ID_RE.fullmatch(network_id) is None
            )
            or not isinstance(endpoint_id, str)
            or (
                endpoint_id != ""
                and NETWORK_ID_RE.fullmatch(endpoint_id) is None
            )
        ):
            raise GlobalDockerInventoryError(
                "container network identity is invalid"
            )
        normalized_networks.append(
            {
                "name_sha256": _sha256(network_name.encode("utf-8")),
                "network_id": network_id,
                "endpoint_id": endpoint_id,
            }
        )
    normalized_mounts: list[dict[str, Any]] = []
    for mount in mounts:
        if not isinstance(mount, dict):
            raise GlobalDockerInventoryError(
                "container mount inspection is invalid"
            )
        mount_type = mount.get("Type")
        source = mount.get("Source")
        destination = mount.get("Destination")
        if (
            mount_type not in {"bind", "volume", "tmpfs", "npipe", "cluster"}
            or not isinstance(source, str)
            or not isinstance(destination, str)
            or not isinstance(mount.get("RW"), bool)
        ):
            raise GlobalDockerInventoryError(
                "container mount inspection is invalid"
            )
        source = _safe_string(source, label="container mount source")
        destination = _safe_string(
            destination,
            label="container mount destination",
        )
        normalized_mounts.append(
            {
                "type": mount_type,
                "source_sha256": _sha256(source.encode("utf-8")),
                "destination_sha256": _sha256(
                    destination.encode("utf-8")
                ),
                "rw": mount["RW"],
            }
        )
    restart_policy = host.get("RestartPolicy")
    binds = host.get("Binds")
    if binds is None:
        binds = []
    if (
        not isinstance(restart_policy, dict)
        or not isinstance(binds, list)
        or len(binds) > MAX_MOUNTS
        or any(not isinstance(item, str) for item in binds)
        or host.get("PortBindings") is not None
        and not isinstance(host.get("PortBindings"), dict)
    ):
        raise GlobalDockerInventoryError(
            "container host metadata is invalid"
        )
    config_metadata = {
        "image": _safe_string(config.get("Image"), label="container image"),
        "cmd_sha256": _string_vector_hash(
            config.get("Cmd"),
            label="container command",
        ),
        "entrypoint_sha256": _string_vector_hash(
            config.get("Entrypoint"),
            label="container entrypoint",
        ),
        # Config.Env is intentionally neither normalized nor hashed.
        "labels_sha256": _sha256(canonical_json(labels)),
        "user_sha256": _sha256(
            _safe_string(
                config.get("User", ""),
                label="container user",
            ).encode("utf-8")
        ),
        "working_dir_sha256": _sha256(
            _safe_string(
                config.get("WorkingDir", ""),
                label="container working directory",
            ).encode("utf-8")
        ),
        "stop_signal": _string_or_none(
            config.get("StopSignal"),
            label="container stop signal",
        ),
    }
    host_metadata = {
        "host_config_sha256": _sha256(canonical_json(host)),
        "network_mode": _safe_string(
            host.get("NetworkMode", ""),
            label="container network mode",
        ),
        "privileged": host.get("Privileged"),
        "readonly_rootfs": host.get("ReadonlyRootfs"),
        "auto_remove": host.get("AutoRemove"),
        "restart_policy_sha256": _sha256(
            canonical_json(restart_policy)
        ),
        "binds_sha256": _sha256(
            canonical_json(
                [
                    _sha256(
                        _safe_string(
                            item,
                            label="container bind",
                        ).encode("utf-8")
                    )
                    for item in binds
                ]
            )
        ),
        "port_bindings_sha256": _sha256(
            canonical_json(host.get("PortBindings"))
        ),
    }
    if any(
        value is not None and not isinstance(value, bool)
        for value in (
            host_metadata["privileged"],
            host_metadata["readonly_rootfs"],
            host_metadata["auto_remove"],
        )
    ):
        raise GlobalDockerInventoryError(
            "container host flags are invalid"
        )
    identity = {
        "id": identifier,
        "name_sha256": _sha256(name.encode("utf-8")),
        "image_id": image,
    }
    metadata = {
        "config_sha256": _sha256(canonical_json(config_metadata)),
        "host_sha256": _sha256(canonical_json(host_metadata)),
        "networks_sha256": _sha256(canonical_json(normalized_networks)),
        "mounts_sha256": _sha256(
            canonical_json(
                sorted(
                    normalized_mounts,
                    key=lambda item: canonical_json(item),
                )
            )
        ),
    }
    return ResourceDescriptor(
        kind="container",
        identity_sha256=_sha256(canonical_json(identity)),
        state_sha256=_sha256(canonical_json(state_flags)),
        metadata_sha256=_sha256(canonical_json(metadata)),
        operation_match=(
            _name_operation_match(
                name,
                project_base=project_base,
                project_name=project_name,
            )
            or _operation_label_match(
                labels,
                operation_id=operation_id,
                project_base=project_base,
                project_name=project_name,
            )
        ),
    )


def _network_descriptor(
    row: Mapping[str, Any],
    *,
    operation_id: str,
    project_base: str,
    project_name: str,
) -> ResourceDescriptor:
    identifier = row.get("Id")
    name = row.get("Name")
    labels = _labels(row.get("Labels"), label="network labels")
    containers = row.get("Containers")
    if containers is None:
        containers = {}
    if (
        not isinstance(identifier, str)
        or NETWORK_ID_RE.fullmatch(identifier) is None
        or not isinstance(name, str)
        or not 1 <= len(name) <= 256
        or not isinstance(containers, dict)
        or len(containers) > MAX_RESOURCES_PER_KIND
    ):
        raise GlobalDockerInventoryError(
            "network inspection shape is invalid"
        )
    name = _safe_string(name, label="network name", maximum=256)
    attached: list[dict[str, str]] = []
    for container_id, endpoint in sorted(containers.items()):
        if (
            not isinstance(container_id, str)
            or CONTAINER_ID_RE.fullmatch(container_id) is None
            or not isinstance(endpoint, dict)
        ):
            raise GlobalDockerInventoryError(
                "network attachment inventory is invalid"
            )
        endpoint_id = endpoint.get("EndpointID", "")
        if (
            not isinstance(endpoint_id, str)
            or (
                endpoint_id != ""
                and NETWORK_ID_RE.fullmatch(endpoint_id) is None
            )
        ):
            raise GlobalDockerInventoryError(
                "network endpoint identity is invalid"
            )
        attached.append(
            {"container_id": container_id, "endpoint_id": endpoint_id}
        )
    boolean_fields = {}
    for field in ("Internal", "Attachable", "Ingress", "ConfigOnly"):
        value = row.get(field, False)
        if not isinstance(value, bool):
            raise GlobalDockerInventoryError(
                "network state flags are invalid"
            )
        boolean_fields[field.lower()] = value
    metadata = {
        "driver": _safe_string(
            row.get("Driver", ""),
            label="network driver",
            maximum=256,
        ),
        "scope": _safe_string(
            row.get("Scope", ""),
            label="network scope",
            maximum=256,
        ),
        "labels_sha256": _sha256(canonical_json(labels)),
        "options_sha256": _sha256(canonical_json(row.get("Options") or {})),
        "ipam_sha256": _sha256(canonical_json(row.get("IPAM") or {})),
    }
    return ResourceDescriptor(
        kind="network",
        identity_sha256=_sha256(
            canonical_json(
                {
                    "id": identifier,
                    "name_sha256": _sha256(name.encode("utf-8")),
                }
            )
        ),
        state_sha256=_sha256(
            canonical_json(
                {
                    **boolean_fields,
                    "attachments": attached,
                }
            )
        ),
        metadata_sha256=_sha256(canonical_json(metadata)),
        operation_match=(
            _name_operation_match(
                name,
                project_base=project_base,
                project_name=project_name,
            )
            or _operation_label_match(
                labels,
                operation_id=operation_id,
                project_base=project_base,
                project_name=project_name,
            )
        ),
    )


def _volume_descriptor(
    row: Mapping[str, Any],
    *,
    operation_id: str,
    project_base: str,
    project_name: str,
) -> ResourceDescriptor:
    name = row.get("Name")
    labels = _labels(row.get("Labels"), label="volume labels")
    if (
        not isinstance(name, str)
        or VOLUME_NAME_RE.fullmatch(name) is None
    ):
        raise GlobalDockerInventoryError(
            "volume inspection shape is invalid"
        )
    metadata = {
        "driver": _safe_string(
            row.get("Driver", ""),
            label="volume driver",
            maximum=256,
        ),
        "scope": _safe_string(
            row.get("Scope", ""),
            label="volume scope",
            maximum=256,
        ),
        "labels_sha256": _sha256(canonical_json(labels)),
        "options_sha256": _sha256(canonical_json(row.get("Options") or {})),
        "mountpoint_sha256": _sha256(
            _safe_string(
                row.get("Mountpoint", ""),
                label="volume mountpoint",
            ).encode("utf-8")
        ),
    }
    return ResourceDescriptor(
        kind="volume",
        identity_sha256=_sha256(
            canonical_json(
                {"name_sha256": _sha256(name.encode("utf-8"))}
            )
        ),
        state_sha256=_sha256(
            canonical_json(
                {
                    "created_at": _string_or_none(
                        row.get("CreatedAt"),
                        label="volume creation time",
                    )
                }
            )
        ),
        metadata_sha256=_sha256(canonical_json(metadata)),
        operation_match=(
            _name_operation_match(
                name,
                project_base=project_base,
                project_name=project_name,
            )
            or _operation_label_match(
                labels,
                operation_id=operation_id,
                project_base=project_base,
                project_name=project_name,
            )
        ),
    )


def _image_descriptor(
    row: Mapping[str, Any],
    *,
    operation_id: str,
    project_base: str,
    project_name: str,
) -> ResourceDescriptor:
    identifier = row.get("Id")
    config = row.get("Config")
    tags = row.get("RepoTags")
    digests = row.get("RepoDigests")
    if (
        not isinstance(identifier, str)
        or IMAGE_ID_RE.fullmatch(identifier) is None
        or not isinstance(config, dict)
        or tags is not None
        and not isinstance(tags, list)
        or digests is not None
        and not isinstance(digests, list)
    ):
        raise GlobalDockerInventoryError(
            "image inspection shape is invalid"
        )
    normalized_tags = sorted(
        _safe_string(item, label="image tag")
        for item in (tags or [])
    )
    normalized_digests = sorted(
        _safe_string(item, label="image digest")
        for item in (digests or [])
    )
    if (
        len(normalized_tags) > 4096
        or len(normalized_digests) > 4096
        or len(normalized_tags) != len(set(normalized_tags))
        or len(normalized_digests) != len(set(normalized_digests))
    ):
        raise GlobalDockerInventoryError(
            "image reference inventory is invalid"
        )
    labels = _labels(config.get("Labels"), label="image labels")
    sizes: dict[str, int] = {}
    for field in ("Size", "VirtualSize", "SharedSize"):
        value = row.get(field, 0)
        if type(value) is not int or not 0 <= value < 2**63:
            raise GlobalDockerInventoryError(
                "image size metadata is invalid"
            )
        sizes[field.lower()] = value
    rootfs = row.get("RootFS") or {}
    if not isinstance(rootfs, dict):
        raise GlobalDockerInventoryError("image rootfs metadata is invalid")
    metadata = {
        "parent": _string_or_none(row.get("Parent"), label="image parent"),
        "architecture": _safe_string(
            row.get("Architecture", ""),
            label="image architecture",
            maximum=256,
        ),
        "os": _safe_string(
            row.get("Os", ""),
            label="image operating system",
            maximum=256,
        ),
        "labels_sha256": _sha256(canonical_json(labels)),
        # Config.Env and GraphDriver.Data are intentionally excluded.
        "cmd_sha256": _string_vector_hash(
            config.get("Cmd"),
            label="image command",
        ),
        "entrypoint_sha256": _string_vector_hash(
            config.get("Entrypoint"),
            label="image entrypoint",
        ),
        "rootfs_sha256": _sha256(canonical_json(rootfs)),
        "repo_tags_sha256": _sha256(canonical_json(normalized_tags)),
        "repo_digests_sha256": _sha256(
            canonical_json(normalized_digests)
        ),
    }
    operation_tag = False
    for tag in normalized_tags:
        repository = tag.rsplit(":", 1)[0]
        repository_name = repository.rsplit("/", 1)[-1]
        if any(
            _name_operation_match(
                candidate,
                project_base=project_base,
                project_name=project_name,
            )
            for candidate in (repository, repository_name)
        ):
            operation_tag = True
            break
    return ResourceDescriptor(
        kind="image",
        identity_sha256=_sha256(
            canonical_json({"id": identifier})
        ),
        state_sha256=_sha256(
            canonical_json(
                {
                    "created": _string_or_none(
                        row.get("Created"),
                        label="image creation time",
                    ),
                    **sizes,
                }
            )
        ),
        metadata_sha256=_sha256(canonical_json(metadata)),
        operation_match=(
            operation_tag
            or _operation_label_match(
                labels,
                operation_id=operation_id,
                project_base=project_base,
                project_name=project_name,
            )
        ),
    )


def _capture_once(
    request: Mapping[str, Any],
    *,
    runner: DockerRunner,
) -> CapturedInventory:
    container_ids = _parse_lines(
        runner.run(
            [*DOCKER_BASE, "ps", "--all", "--quiet", "--no-trunc"],
            timeout=30,
        ),
        pattern=CONTAINER_ID_RE,
        label="container ID inventory",
    )
    network_ids = _parse_lines(
        runner.run(
            [
                *DOCKER_BASE,
                "network",
                "ls",
                "--quiet",
                "--no-trunc",
            ],
            timeout=30,
        ),
        pattern=NETWORK_ID_RE,
        label="network ID inventory",
    )
    volume_names = _parse_lines(
        runner.run(
            [*DOCKER_BASE, "volume", "ls", "--quiet"],
            timeout=30,
        ),
        pattern=VOLUME_NAME_RE,
        label="volume name inventory",
    )
    image_ids = sorted(
        set(
            _parse_lines(
                runner.run(
                    [
                        *DOCKER_BASE,
                        "image",
                        "ls",
                        "--all",
                        "--quiet",
                        "--no-trunc",
                    ],
                    timeout=30,
                ),
                pattern=IMAGE_ID_RE,
                label="image ID inventory",
                allow_duplicates=True,
            )
        )
    )
    containers = _load_inspect_rows(
        runner,
        command_prefix=(*DOCKER_BASE, "inspect"),
        identifiers=container_ids,
        identity_field="Id",
        label="container inspection",
    )
    networks = _load_inspect_rows(
        runner,
        command_prefix=(*DOCKER_BASE, "network", "inspect"),
        identifiers=network_ids,
        identity_field="Id",
        label="network inspection",
    )
    volumes = _load_inspect_rows(
        runner,
        command_prefix=(*DOCKER_BASE, "volume", "inspect"),
        identifiers=volume_names,
        identity_field="Name",
        label="volume inspection",
    )
    images = _load_inspect_rows(
        runner,
        command_prefix=(*DOCKER_BASE, "image", "inspect"),
        identifiers=image_ids,
        identity_field="Id",
        label="image inspection",
    )
    kwargs = {
        "operation_id": request["operation_id"],
        "project_base": request["project_base"],
        "project_name": request["project_name"],
    }
    descriptors = [
        *(
            _container_descriptor(containers[item], **kwargs)
            for item in container_ids
        ),
        *(
            _network_descriptor(networks[item], **kwargs)
            for item in network_ids
        ),
        *(
            _volume_descriptor(volumes[item], **kwargs)
            for item in volume_names
        ),
        *(
            _image_descriptor(images[item], **kwargs)
            for item in image_ids
        ),
    ]
    descriptors.sort(
        key=lambda item: (
            RESOURCE_KINDS.index(item.kind),
            item.identity_sha256,
        )
    )
    if len({(item.kind, item.identity_sha256) for item in descriptors}) != len(
        descriptors
    ):
        raise GlobalDockerInventoryError(
            "normalized Docker resource identities collide"
        )
    return CapturedInventory(
        descriptors=tuple(descriptors),
        raw_containers=containers,
        raw_networks=networks,
        raw_images=images,
    )


def _descriptor_roots(
    descriptors: Sequence[ResourceDescriptor],
) -> dict[str, str]:
    documents = [item.document() for item in descriptors]
    return {
        "inventory": _sha256(canonical_json(documents)),
        "identity": _sha256(
            canonical_json(
                [
                    {
                        "kind": item.kind,
                        "identity_sha256": item.identity_sha256,
                    }
                    for item in descriptors
                ]
            )
        ),
        "state": _sha256(
            canonical_json(
                [
                    {
                        "kind": item.kind,
                        "identity_sha256": item.identity_sha256,
                        "state_sha256": item.state_sha256,
                    }
                    for item in descriptors
                ]
            )
        ),
        "metadata": _sha256(
            canonical_json(
                [
                    {
                        "kind": item.kind,
                        "identity_sha256": item.identity_sha256,
                        "metadata_sha256": item.metadata_sha256,
                    }
                    for item in descriptors
                ]
            )
        ),
    }


def _counts(
    descriptors: Sequence[ResourceDescriptor],
) -> dict[str, int]:
    return {
        kind: sum(item.kind == kind for item in descriptors)
        for kind in RESOURCE_KINDS
    }


def _resolve_compose_contract_value(
    value: Any,
    *,
    environment: Mapping[str, str],
    label: str,
) -> Any:
    if not isinstance(value, str):
        return value
    match = COMPOSE_VARIABLE_RE.fullmatch(value)
    if match is None:
        if "${" in value:
            raise GlobalDockerInventoryError(f"{label} is invalid")
        return value
    name, operator, fallback = match.groups()
    observed = environment.get(name)
    if observed:
        return observed
    if operator == ":-":
        if fallback == "":
            raise GlobalDockerInventoryError(f"{label} is invalid")
        return fallback
    raise GlobalDockerInventoryError(f"{label} is unavailable")


def _database_host_runtime_contract(
    manifest: Any,
    *,
    worker: Any,
    image_config: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        compose = worker._restore_compose_document(manifest)
        services = (
            compose.get("services") if isinstance(compose, dict) else None
        )
        service = (
            services.get(f"{manifest.role}_db")
            if isinstance(services, dict)
            else None
        )
        environment_payload = worker._read_root_file(
            manifest.environment_path,
            label="frozen-final role environment",
            maximum=worker.MAX_JSON_BYTES,
            expected_sha256=manifest.document["environment_sha256"],
        )
        environment = worker.parse_env_values(
            environment_payload.decode("ascii")
        )
        _command_environment, overrides = worker._compose_environment(
            manifest
        )
        environment.update(overrides)
        if not isinstance(service, dict):
            raise GlobalDockerInventoryError(
                "database runtime contract is unavailable"
            )
        cgroup_parent = _resolve_compose_contract_value(
            service.get("cgroup_parent"),
            environment=environment,
            label="database cgroup parent",
        )
        nano_cpus = worker._nano_cpus(
            _resolve_compose_contract_value(
                service.get("cpus"),
                environment=environment,
                label="database CPU limit",
            ),
            label="database CPU limit",
        )
        memory = worker._memory_bytes(
            _resolve_compose_contract_value(
                service.get("mem_limit"),
                environment=environment,
                label="database memory limit",
            ),
            label="database memory limit",
        )
        pids_limit = _resolve_compose_contract_value(
            service.get("pids_limit"),
            environment=environment,
            label="database PID limit",
        )
        if (
            isinstance(pids_limit, str)
            and re.fullmatch(r"[1-9][0-9]*", pids_limit) is not None
        ):
            pids_limit = int(pids_limit)
        logging = service.get("logging")
        options = logging.get("options") if isinstance(logging, dict) else None
        service_environment = service.get("environment") or {}
        service_labels = service.get("labels") or {}
        if not isinstance(service_environment, dict):
            raise GlobalDockerInventoryError(
                "database environment contract is invalid"
            )
        if not isinstance(service_labels, dict):
            raise GlobalDockerInventoryError(
                "database label contract is invalid"
            )
        resolved_service_environment = {
            key: _resolve_compose_contract_value(
                item,
                environment=environment,
                label=f"database environment {key}",
            )
            for key, item in service_environment.items()
        }
        image_environment = worker._environment_map(  # noqa: SLF001
            image_config.get("Env") or [],
            label="PostgreSQL image environment",
        )
        image_environment.update(
            worker._environment_map(  # noqa: SLF001
                resolved_service_environment,
                label="database service environment",
            )
        )
        image_labels = _labels(
            image_config.get("Labels"),
            label="PostgreSQL image labels",
        )
        resolved_service_labels = {
            key: _resolve_compose_contract_value(
                item,
                environment=environment,
                label=f"database label {key}",
            )
            for key, item in service_labels.items()
        }
        if any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in resolved_service_labels.items()
        ):
            raise GlobalDockerInventoryError(
                "database label contract is invalid"
            )
        command = (
            worker._string_vector(  # noqa: SLF001
                service.get("command"),
                label="database command",
            )
            if service.get("command") is not None
            else worker._string_vector(  # noqa: SLF001
                image_config.get("Cmd"),
                label="PostgreSQL image command",
            )
        )
        entrypoint = (
            worker._string_vector(  # noqa: SLF001
                service.get("entrypoint"),
                label="database entrypoint",
            )
            if service.get("entrypoint") is not None
            else worker._string_vector(  # noqa: SLF001
                image_config.get("Entrypoint"),
                label="PostgreSQL image entrypoint",
            )
        )
        user = service.get("user", image_config.get("User", ""))
        working_dir = service.get(
            "working_dir",
            image_config.get("WorkingDir", ""),
        )
        stop_signal = service.get(
            "stop_signal",
            image_config.get("StopSignal", ""),
        )
        if (
            not isinstance(cgroup_parent, str)
            or not cgroup_parent
            or isinstance(pids_limit, bool)
            or not isinstance(pids_limit, int)
            or pids_limit <= 0
            or service.get("restart") != "unless-stopped"
            or not isinstance(logging, dict)
            or not isinstance(logging.get("driver"), str)
            or not isinstance(options, dict)
            or any(
                not isinstance(key, str) or not isinstance(item, str)
                for key, item in options.items()
            )
            or any(
                not isinstance(value, str)
                for value in (user, working_dir, stop_signal)
            )
        ):
            raise GlobalDockerInventoryError(
                "database runtime contract is invalid"
            )
        return {
            "cgroup_parent": cgroup_parent,
            "nano_cpus": nano_cpus,
            "memory": memory,
            "pids_limit": pids_limit,
            "restart_policy": service["restart"],
            "log_config": {
                "Type": logging["driver"],
                "Config": dict(sorted(options.items())),
            },
            "image_id": manifest.postgres_image_id,
            "command": command,
            "entrypoint": entrypoint,
            "user": user,
            "working_dir": working_dir,
            "stop_signal": stop_signal,
            "stop_timeout": worker._duration_seconds(  # noqa: SLF001
                service.get("stop_grace_period", "10s"),
                label="database stop grace period",
            ),
            "environment": dict(sorted(image_environment.items())),
            "healthcheck": worker._compose_healthcheck(  # noqa: SLF001
                service.get("healthcheck")
            ),
            "exposed_ports": worker._empty_object_map(  # noqa: SLF001
                image_config.get("ExposedPorts"),
                label="PostgreSQL image exposed ports",
            ),
            "volumes": worker._empty_object_map(  # noqa: SLF001
                image_config.get("Volumes"),
                label="PostgreSQL image volumes",
            ),
            "on_build": worker._string_vector(  # noqa: SLF001
                image_config.get("OnBuild"),
                label="PostgreSQL image OnBuild",
            ),
            "shell": worker._string_vector(  # noqa: SLF001
                image_config.get("Shell"),
                label="PostgreSQL image shell",
            ),
            "service": f"{manifest.role}_db",
            "compose_dependencies": worker._compose_dependencies_label(  # noqa: SLF001
                service.get("depends_on")
            ),
            "labels": dict(
                sorted({**image_labels, **resolved_service_labels}.items())
            ),
        }
    except GlobalDockerInventoryError:
        raise
    except Exception as exc:
        raise GlobalDockerInventoryError(
            "database runtime contract is unavailable"
        ) from exc


def _validate_operation_closure(
    request: Mapping[str, Any],
    capture: CapturedInventory,
    *,
    worker: Any,
) -> str | None:
    operation_descriptors = [
        item for item in capture.descriptors if item.operation_match
    ]
    expected_counts = (
        {kind: 0 for kind in RESOURCE_KINDS}
        if request["action"] == "capture-before"
        else {"container": 1, "network": 1, "volume": 0, "image": 0}
    )
    if _counts(operation_descriptors) != expected_counts:
        raise GlobalDockerInventoryError(
            "operation Docker resource closure differs"
        )
    if request["action"] == "capture-before":
        return None
    manifest = worker.load_role_manifest(Path(request["role_manifest_path"]))
    paths = worker.runtime_paths(
        request["operation_id"],
        request["release_sha"],
        request["restore_generation_sha256"],
        request["role"],
    )
    if (
        manifest.canonical_sha256 != request["role_manifest_sha256"]
        or manifest.operation_id != request["operation_id"]
        or manifest.role != request["role"]
        or manifest.release_sha != request["release_sha"]
        or manifest.release_tree_sha != request["release_tree_sha"]
        or manifest.restore_generation_sha256
        != request["restore_generation_sha256"]
        or manifest.paths != paths
        or manifest.paths.project_base != request["project_base"]
        or manifest.paths.project_name != request["project_name"]
    ):
        raise GlobalDockerInventoryError(
            "installed role manifest differs from inventory request"
        )
    container_id = request["expected_operation_container_id"]
    container = capture.raw_containers.get(container_id)
    if container is None:
        raise GlobalDockerInventoryError(
            "expected restored database container is absent"
        )
    config = container.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    host = container.get("HostConfig")
    state = container.get("State")
    networks = (
        container.get("NetworkSettings", {}).get("Networks")
        if isinstance(container.get("NetworkSettings"), dict)
        else None
    )
    mounts = container.get("Mounts")
    expected_network_name = (
        f"{request['project_name']}_{request['role']}"
    )
    matching_networks = [
        row
        for row in capture.raw_networks.values()
        if row.get("Name") == expected_network_name
    ]
    if len(matching_networks) != 1:
        raise GlobalDockerInventoryError(
            "restored operation network identity differs"
        )
    network = matching_networks[0]
    network_labels = network.get("Labels")
    compose_version = (
        network_labels.get("com.docker.compose.version")
        if isinstance(network_labels, dict)
        else None
    )
    if (
        not isinstance(compose_version, str)
        or re.fullmatch(
            r"[0-9]+(?:\.[0-9]+){1,3}(?:[-+][0-9A-Za-z.-]+)?",
            compose_version,
        )
        is None
    ):
        raise GlobalDockerInventoryError(
            "restored operation network contract differs"
        )
    image = capture.raw_images.get(manifest.postgres_image_id)
    image_config = image.get("Config") if isinstance(image, dict) else None
    if not isinstance(image_config, dict):
        raise GlobalDockerInventoryError(
            "immutable PostgreSQL image config is unavailable"
        )
    try:
        if (
            worker.DOCKER_API_VERSION != DOCKER_API_VERSION
            or worker.HOST_CONFIG_FIELDS != HOST_CONFIG_FIELDS
            or worker.OPTIONAL_HOST_CONFIG_FIELDS
            != OPTIONAL_HOST_CONFIG_FIELDS
            or worker.CONTAINER_CONFIG_FIELDS != CONTAINER_CONFIG_FIELDS
        ):
            raise GlobalDockerInventoryError(
                "restore worker Docker API contract differs"
            )
        runtime = _database_host_runtime_contract(
            manifest,
            worker=worker,
            image_config=image_config,
        )
        contract = types.SimpleNamespace(
            image_id=runtime["image_id"],
            user=runtime["user"],
            working_dir=runtime["working_dir"],
            stop_signal=runtime["stop_signal"],
            stop_timeout=runtime["stop_timeout"],
            environment=runtime["environment"],
            command=runtime["command"],
            entrypoint=runtime["entrypoint"],
            healthcheck=runtime["healthcheck"],
            exposed_ports=runtime["exposed_ports"],
            volumes=runtime["volumes"],
            on_build=runtime["on_build"],
            shell=runtime["shell"],
            service=runtime["service"],
            config_hash=(
                labels.get("com.docker.compose.config-hash")
                if isinstance(labels, dict)
                else None
            ),
            compose_version=compose_version,
            compose_dependencies=runtime["compose_dependencies"],
        )
        worker._validate_exact_container_config(  # noqa: SLF001
            config,
            container_id=container_id,
            contract=contract,
            command=runtime["command"],
            environment=runtime["environment"],
        )
        observed_host_config_sha256 = (
            worker._validate_exact_host_config(  # noqa: SLF001
                host,
                binds=[
                    f"{manifest.paths.postgres}:"
                    "/var/lib/postgresql/data:rw"
                ],
                network_mode=expected_network_name,
                cgroup_parent=runtime["cgroup_parent"],
                nano_cpus=runtime["nano_cpus"],
                memory=runtime["memory"],
                pids_limit=runtime["pids_limit"],
                auto_remove=False,
                restart_policy=runtime["restart_policy"],
                log_config=runtime["log_config"],
            )
        )
    except GlobalDockerInventoryError:
        raise
    except Exception as exc:
        raise GlobalDockerInventoryError(
            "restored database container contract differs"
        ) from exc
    if (
        observed_host_config_sha256
        != request["expected_operation_host_config_sha256"]
    ):
        raise GlobalDockerInventoryError(
            "restored database HostConfig digest differs"
        )
    config_hash = contract.config_hash
    if (
        not isinstance(config_hash, str)
        or SHA256_RE.fullmatch(config_hash) is None
    ):
        raise GlobalDockerInventoryError(
            "restored database container contract differs"
        )
    try:
        expected_labels = {
            **runtime["labels"],
            **worker._expected_compose_container_labels(  # noqa: SLF001
                manifest,
                contract,
                oneoff=False,
                slug=None,
            ),
        }
    except Exception as exc:
        raise GlobalDockerInventoryError(
            "restored database container contract differs"
        ) from exc
    if (
        container.get("Id") != container_id
        or container.get("Name")
        != f"/{request['project_name']}-{request['role']}_db-1"
        or container.get("Image") != manifest.postgres_image_id
        or labels != expected_labels
        or labels.get("trading-bot.production.operation-id")
        != request["operation_id"]
        or not isinstance(state, dict)
        or state.get("Running") is not True
        or state.get("Paused") is not False
        or state.get("Restarting") is not False
        or state.get("Dead") is not False
        or state.get("Status") != "running"
        or not isinstance(state.get("Health"), dict)
        or state["Health"].get("Status") != "healthy"
        or not isinstance(networks, dict)
        or set(networks) != {expected_network_name}
        or not isinstance(mounts, list)
        or len(mounts) != 1
    ):
        raise GlobalDockerInventoryError(
            "restored database container contract differs"
        )
    mount = mounts[0]
    if (
        not isinstance(mount, dict)
        or mount.get("Type") != "bind"
        or mount.get("Source") != str(manifest.paths.postgres)
        or mount.get("Destination") != "/var/lib/postgresql/data"
        or mount.get("RW") is not True
    ):
        raise GlobalDockerInventoryError(
            "restored database mount escaped the generation root"
        )
    attached = network.get("Containers")
    database_attachment = networks.get(expected_network_name)
    if (
        network.get("Internal") is not True
        or not isinstance(network_labels, dict)
        or network_labels.get("com.docker.compose.project")
        != request["project_name"]
        or network_labels.get("com.docker.compose.network")
        != request["role"]
        or network_labels.get("trading-bot.production.operation-id")
        != request["operation_id"]
        or not isinstance(attached, dict)
        or set(attached) != {container_id}
        or not isinstance(database_attachment, dict)
        or database_attachment.get("NetworkID") != network.get("Id")
        or not isinstance(database_attachment.get("EndpointID"), str)
        or NETWORK_ID_RE.fullmatch(
            database_attachment["EndpointID"]
        )
        is None
    ):
        raise GlobalDockerInventoryError(
            "restored operation network contract differs"
        )
    return observed_host_config_sha256


def _prepared_operation_rows(
    request: Mapping[str, Any],
    capture: CapturedInventory,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    containers: list[Mapping[str, Any]] = []
    for row in capture.raw_containers.values():
        config = row.get("Config")
        labels = config.get("Labels") if isinstance(config, dict) else None
        if not isinstance(labels, dict):
            labels = {}
        if (
            _name_operation_match(
                str(row.get("Name", "")),
                project_base=request["project_base"],
                project_name=request["project_name"],
            )
            or _operation_label_match(
                labels,
                operation_id=request["operation_id"],
                project_base=request["project_base"],
                project_name=request["project_name"],
            )
        ):
            containers.append(row)
    networks: list[Mapping[str, Any]] = []
    for row in capture.raw_networks.values():
        labels = row.get("Labels")
        if not isinstance(labels, dict):
            labels = {}
        if (
            _name_operation_match(
                str(row.get("Name", "")),
                project_base=request["project_base"],
                project_name=request["project_name"],
            )
            or _operation_label_match(
                labels,
                operation_id=request["operation_id"],
                project_base=request["project_base"],
                project_name=request["project_name"],
            )
        ):
            networks.append(row)
    if len(containers) != 1 or len(networks) != 1:
        raise GlobalDockerInventoryError(
            "prepared operation container or network closure differs"
        )
    return containers[0], networks[0]


def _prepared_public_digests(
    container: Mapping[str, Any],
) -> dict[str, str]:
    config = container.get("Config")
    host = container.get("HostConfig")
    mounts = container.get("Mounts")
    network_settings = container.get("NetworkSettings")
    networks = (
        network_settings.get("Networks")
        if isinstance(network_settings, dict)
        else None
    )
    if (
        not isinstance(config, dict)
        or not isinstance(host, dict)
        or not isinstance(mounts, list)
        or not isinstance(networks, dict)
    ):
        raise GlobalDockerInventoryError(
            "prepared container digest inputs are invalid"
        )
    public_config = dict(config)
    public_config["Env"] = {
        "redacted": True,
        "entry_count": (
            len(config["Env"])
            if isinstance(config.get("Env"), list)
            else 0
        ),
    }
    normalized_mounts: list[dict[str, Any]] = []
    for mount in mounts:
        if (
            not isinstance(mount, dict)
            or not isinstance(mount.get("Source"), str)
            or not isinstance(mount.get("Destination"), str)
        ):
            raise GlobalDockerInventoryError(
                "prepared mount digest input is invalid"
            )
        normalized_mounts.append(
            {
                "type": mount.get("Type"),
                "source_sha256": _sha256(
                    mount["Source"].encode("utf-8")
                ),
                "destination_sha256": _sha256(
                    mount["Destination"].encode("utf-8")
                ),
                "rw": mount.get("RW"),
                "propagation": mount.get("Propagation"),
            }
        )
    normalized_networks: list[dict[str, Any]] = []
    for name, attachment in sorted(networks.items()):
        if not isinstance(attachment, dict):
            raise GlobalDockerInventoryError(
                "prepared network attachment digest input is invalid"
            )
        normalized_networks.append(
            {
                "name_sha256": _sha256(name.encode("utf-8")),
                "network_id": attachment.get("NetworkID"),
                "endpoint_id": attachment.get("EndpointID"),
            }
        )
    return {
        "config": _sha256(canonical_json(public_config)),
        "host_config": _sha256(canonical_json(host)),
        "mounts": _sha256(
            canonical_json(
                sorted(normalized_mounts, key=canonical_json)
            )
        ),
        "network_attachment": _sha256(
            canonical_json(normalized_networks)
        ),
    }


def _same_directory_metadata(
    expected: os.stat_result,
    *observed: os.stat_result,
) -> bool:
    stable_fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_uid",
        "st_gid",
        "st_nlink",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    return all(
        getattr(expected, field) == getattr(item, field)
        for item in observed
        for field in stable_fields
    )


def _attest_pristine_redis_directory(
    target: Path,
    *,
    request: Mapping[str, Any],
    operation_root: Path = DATA_ROOT_PREFIX,
) -> dict[str, Any]:
    target = Path(target)
    operation_root = Path(operation_root)
    if (
        not target.is_absolute()
        or target != Path(os.path.abspath(target))
        or ".." in target.parts
        or len(target.parts) < 3
        or not operation_root.is_absolute()
        or operation_root != Path(os.path.abspath(operation_root))
        or ".." in operation_root.parts
        or operation_root not in target.parents
    ):
        raise GlobalDockerInventoryError(
            "prepared Redis target path is not canonical"
        )
    components = target.parts[1:]
    operation_start_index = len(operation_root.parts) - 2
    descriptors: list[int] = []
    bindings: list[tuple[str, os.stat_result]] = []
    try:
        root_fd = os.open("/", _directory_flags())
        descriptors.append(root_fd)
        root_metadata = os.fstat(root_fd)
        if (
            not stat.S_ISDIR(root_metadata.st_mode)
            or root_metadata.st_uid != 0
            or root_metadata.st_gid != 0
            or bool(stat.S_IMODE(root_metadata.st_mode) & 0o022)
        ):
            raise GlobalDockerInventoryError(
                "prepared Redis directory root is unsafe"
            )
        for index, component in enumerate(components):
            if (
                not component
                or component in {".", ".."}
                or "/" in component
                or "\x00" in component
            ):
                raise GlobalDockerInventoryError(
                    "prepared Redis directory component is unsafe"
                )
            descriptor = os.open(
                component,
                _directory_flags(),
                dir_fd=descriptors[-1],
            )
            metadata = os.fstat(descriptor)
            descriptors.append(descriptor)
            operation_owned = index >= operation_start_index
            writable_system_ancestor_is_sticky = (
                not operation_owned
                and bool(stat.S_IMODE(metadata.st_mode) & 0o022)
                and bool(metadata.st_mode & stat.S_ISVTX)
            )
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or metadata.st_gid != 0
                or metadata.st_nlink < 1
                or (
                    operation_owned
                    and stat.S_IMODE(metadata.st_mode) != 0o700
                )
                or (
                    not operation_owned
                    and bool(stat.S_IMODE(metadata.st_mode) & 0o022)
                    and not writable_system_ancestor_is_sticky
                )
            ):
                raise GlobalDockerInventoryError(
                    "prepared Redis directory chain is unsafe"
                )
            bindings.append((component, metadata))
        entry_count = 0
        with os.scandir(descriptors[-1]) as entries:
            for _entry in entries:
                entry_count += 1
                break
        if entry_count != 0:
            raise GlobalDockerInventoryError(
                "prepared Redis target is not pristine-empty"
            )
        leaf_metadata = bindings[-1][1]
        alias_count = 0
        with os.scandir(descriptors[-2]) as siblings:
            for sibling in siblings:
                try:
                    sibling_metadata = sibling.stat(follow_symlinks=False)
                except OSError as exc:
                    raise GlobalDockerInventoryError(
                        "prepared Redis parent inventory changed"
                    ) from exc
                if (
                    sibling_metadata.st_dev == leaf_metadata.st_dev
                    and sibling_metadata.st_ino == leaf_metadata.st_ino
                ):
                    alias_count += 1
        if alias_count != 1:
            raise GlobalDockerInventoryError(
                "prepared Redis target has an unsafe directory alias"
            )
        chain_metadata: list[dict[str, Any]] = []
        for index, (component, before) in enumerate(bindings):
            after = os.fstat(descriptors[index + 1])
            visible = os.stat(
                component,
                dir_fd=descriptors[index],
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(visible.st_mode)
                or not _same_directory_metadata(before, after, visible)
            ):
                raise GlobalDockerInventoryError(
                    "prepared Redis directory identity changed during scan"
                )
            if index >= operation_start_index:
                chain_metadata.append(
                    {
                        "component_sha256": _sha256(
                            component.encode("utf-8")
                        ),
                        "device": after.st_dev,
                        "inode": after.st_ino,
                        "uid": after.st_uid,
                        "gid": after.st_gid,
                        "mode": stat.S_IMODE(after.st_mode),
                        "link_count": after.st_nlink,
                        "size": after.st_size,
                        "mtime_ns": after.st_mtime_ns,
                        "ctime_ns": after.st_ctime_ns,
                    }
                )
        leaf_after = os.fstat(descriptors[-1])
        chain_metadata_sha256 = _sha256(
            canonical_json(
                {
                    "schema": (
                        "production-shadow-prepared-redis-chain-metadata-v1"
                    ),
                    "components": chain_metadata,
                }
            )
        )
        identity_document = {
            "schema": "production-shadow-prepared-redis-identity-v1",
            "operation_id": request["operation_id"],
            "role": request["role"],
            "target_kind": "redis",
            "canonical_path_sha256": _sha256(
                str(target).encode("utf-8")
            ),
            "device": leaf_after.st_dev,
            "inode": leaf_after.st_ino,
            "chain_metadata_sha256": chain_metadata_sha256,
        }
        identity_sha256 = _sha256(canonical_json(identity_document))
        metadata_document = {
            "schema": "production-shadow-prepared-redis-metadata-v1",
            "identity_sha256": identity_sha256,
            "chain_metadata_sha256": chain_metadata_sha256,
            "uid": leaf_after.st_uid,
            "gid": leaf_after.st_gid,
            "mode": stat.S_IMODE(leaf_after.st_mode),
            "link_count": leaf_after.st_nlink,
            "size": leaf_after.st_size,
            "mtime_ns": leaf_after.st_mtime_ns,
            "ctime_ns": leaf_after.st_ctime_ns,
            "entry_count": entry_count,
        }
        return {
            "identity_sha256": identity_sha256,
            "chain_metadata_sha256": chain_metadata_sha256,
            "metadata_sha256": _sha256(
                canonical_json(metadata_document)
            ),
            "target_count": 1,
            "unsafe_path_count": 0,
            "entry_count": 0,
            "pristine": True,
        }
    except GlobalDockerInventoryError:
        raise
    except OSError as exc:
        raise GlobalDockerInventoryError(
            "prepared Redis target is unavailable or unsafe"
        ) from exc
    finally:
        primary_error = sys.exc_info()[1]
        cleanup_error: BaseException | None = None
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        if cleanup_error is not None:
            message = (
                "prepared Redis directory descriptors could not be closed"
            )
            if primary_error is not None:
                if hasattr(primary_error, "add_note"):
                    primary_error.add_note(message)
            elif not isinstance(cleanup_error, OSError):
                raise cleanup_error
            else:
                raise GlobalDockerInventoryError(
                    message
                ) from cleanup_error


def _attest_prepared_redis_target(
    request: Mapping[str, Any],
    *,
    worker: Any,
    manifest: Any,
    paths: Any,
) -> dict[str, Any]:
    expected_data_root = DATA_ROOT_PREFIX / request["operation_id"]
    if request["contract_kind"] == "finland-precommit":
        role_path = request["role"].replace("_", "-")
        target = paths.data_root / role_path / "redis"
        if (
            paths.data_root != expected_data_root
            or getattr(worker, "STORE_NAMES", None)
            != ("postgres", "redis", "uploads", "audit")
        ):
            raise GlobalDockerInventoryError(
                "prepared Finland Redis target binding differs"
            )
    else:
        try:
            canonical = worker._canonical_operation_paths(  # noqa: SLF001
                manifest
            )
        except Exception as exc:
            raise GlobalDockerInventoryError(
                "prepared WA-IR Redis target binding is unavailable"
            ) from exc
        target = paths.redis
        if (
            paths != canonical
            or paths.data_root != expected_data_root
            or target != expected_data_root / "webapp-ir" / "redis"
        ):
            raise GlobalDockerInventoryError(
                "prepared WA-IR Redis target binding differs"
            )
    return _attest_pristine_redis_directory(target, request=request)


def _environment_map(value: Any, *, label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if value is None:
        return result
    if isinstance(value, list):
        entries = value
    elif isinstance(value, dict):
        entries = [
            f"{key}={item}"
            for key, item in value.items()
            if item is not None
        ]
        if len(entries) != len(value):
            raise GlobalDockerInventoryError(f"{label} is unresolved")
    else:
        raise GlobalDockerInventoryError(f"{label} is invalid")
    for entry in entries:
        if (
            not isinstance(entry, str)
            or "=" not in entry
            or "\x00" in entry
        ):
            raise GlobalDockerInventoryError(f"{label} is invalid")
        key, item = entry.split("=", 1)
        if (
            re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", key) is None
            or key in result
        ):
            raise GlobalDockerInventoryError(f"{label} is invalid")
        result[key] = item
    return result


def _compose_label_map(value: Any, *, label: str) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, dict):
        if any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in value.items()
        ):
            raise GlobalDockerInventoryError(f"{label} is invalid")
        return dict(value)
    if isinstance(value, list):
        result: dict[str, str] = {}
        for entry in value:
            if not isinstance(entry, str) or "=" not in entry:
                raise GlobalDockerInventoryError(f"{label} is invalid")
            key, item = entry.split("=", 1)
            if not key or key in result:
                raise GlobalDockerInventoryError(f"{label} is invalid")
            result[key] = item
        return result
    raise GlobalDockerInventoryError(f"{label} is invalid")


def _prepared_compose_contract(
    request: Mapping[str, Any],
    capture: CapturedInventory,
    *,
    runner: DockerRunner,
    manifest: Any,
    paths: Any,
    service: str,
    expected_image: str,
) -> dict[str, Any]:
    if request["contract_kind"] == "finland-precommit":
        environment_path = paths.environment
        compose_path = paths.compose
    else:
        environment_path = paths.runtime_env
        compose_path = paths.compose
    prefix = [
        *DOCKER_BASE,
        "compose",
        "--project-name",
        request["project_name"],
        "--env-file",
        str(environment_path),
        "--file",
        str(compose_path),
        "config",
    ]
    rendered_raw = runner.run(
        [*prefix, "--format", "json"],
        timeout=30,
    )
    try:
        rendered = json.loads(
            rendered_raw,
            object_pairs_hook=_strict_object,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise GlobalDockerInventoryError(
            "prepared Compose contract is invalid JSON"
        ) from exc
    services = rendered.get("services") if isinstance(rendered, dict) else None
    service_document = (
        services.get(service) if isinstance(services, dict) else None
    )
    if not isinstance(service_document, dict):
        raise GlobalDockerInventoryError(
            "prepared Compose database service is unavailable"
        )
    image = capture.raw_images.get(expected_image)
    image_config = image.get("Config") if isinstance(image, dict) else None
    if not isinstance(image_config, dict):
        raise GlobalDockerInventoryError(
            "prepared PostgreSQL image config is unavailable"
        )
    environment = _environment_map(
        image_config.get("Env"),
        label="PostgreSQL image environment",
    )
    environment.update(
        _environment_map(
            service_document.get("environment"),
            label="prepared Compose service environment",
        )
    )
    labels = _compose_label_map(
        service_document.get("labels"),
        label="prepared Compose service labels",
    )
    config_hash_raw = runner.run(
        [*prefix, "--hash", service],
        timeout=30,
    ).strip()
    match = re.fullmatch(
        rf"(?:{re.escape(service)}(?:[ :]))?([0-9a-f]{{64}})",
        config_hash_raw,
    )
    if match is None:
        raise GlobalDockerInventoryError(
            "prepared Compose config hash is unavailable"
        )
    return {
        "environment": environment,
        "labels": labels,
        "config_hash": match.group(1),
    }


def _validate_prepared_operation_closure(
    request: Mapping[str, Any],
    capture: CapturedInventory,
    *,
    worker: Any,
    manifest: Any,
    paths: Any,
    runner: DockerRunner,
) -> dict[str, Any]:
    operation_descriptors = [
        item for item in capture.descriptors if item.operation_match
    ]
    if _counts(operation_descriptors) != {
        "container": 1,
        "network": 1,
        "volume": 0,
        "image": 0,
    }:
        raise GlobalDockerInventoryError(
            "prepared operation Docker resource closure differs"
        )
    container, network = _prepared_operation_rows(request, capture)
    container_descriptor = _container_descriptor(
        container,
        operation_id=request["operation_id"],
        project_base=request["project_base"],
        project_name=request["project_name"],
    )
    network_descriptor = _network_descriptor(
        network,
        operation_id=request["operation_id"],
        project_base=request["project_base"],
        project_name=request["project_name"],
    )
    if request["contract_kind"] == "finland-precommit":
        service = str(worker.ROLE_SERVICES[request["role"]]["database"])
        role_network = str(
            worker.ROLE_SERVICES[request["role"]]["network"]
        )
        expected_image = manifest.runtime_image_ids["postgres"]
        expected_mount_source = (
            paths.data_root
            / ROLE_PATHS[request["role"]]
            / "postgres"
        )
    else:
        service = str(manifest.services["database"])
        role_network = "webapp_ir"
        expected_image = manifest.image_artifacts[
            "postgres"
        ].config_digest
        expected_mount_source = paths.postgres
    expected_container_name = (
        f"/{request['project_name']}-{service}-1"
    )
    expected_network_name = (
        f"{request['project_name']}_{role_network}"
    )
    compose_contract = _prepared_compose_contract(
        request,
        capture,
        runner=runner,
        manifest=manifest,
        paths=paths,
        service=service,
        expected_image=expected_image,
    )
    config = container.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    host = container.get("HostConfig")
    state = container.get("State")
    mounts = container.get("Mounts")
    network_settings = container.get("NetworkSettings")
    attachments = (
        network_settings.get("Networks")
        if isinstance(network_settings, dict)
        else None
    )
    restart_policy = (
        host.get("RestartPolicy") if isinstance(host, dict) else None
    )
    health = state.get("Health") if isinstance(state, dict) else None
    image = capture.raw_images.get(expected_image)
    image_config = image.get("Config") if isinstance(image, dict) else None
    image_labels = (
        image_config.get("Labels")
        if isinstance(image_config, dict)
        else None
    )
    if image_labels is None:
        image_labels = {}
    if not isinstance(image_labels, dict):
        raise GlobalDockerInventoryError(
            "prepared PostgreSQL image label contract is invalid"
        )
    observed_environment = _environment_map(
        config.get("Env") if isinstance(config, dict) else None,
        label="prepared container environment",
    )
    config_hash = (
        labels.get("com.docker.compose.config-hash")
        if isinstance(labels, dict)
        else None
    )
    running_expected = (
        request["expected_database_state"] == "running-healthy"
    )
    state_is_exact = (
        isinstance(state, dict)
        and state.get("Paused") is False
        and state.get("Restarting") is False
        and state.get("Dead") is False
        and (
            (
                running_expected
                and state.get("Running") is True
                and state.get("Status") == "running"
                and isinstance(health, dict)
                and health.get("Status") == "healthy"
            )
            or (
                not running_expected
                and state.get("Running") is False
                and state.get("Status") == "exited"
                and state.get("ExitCode") == 0
            )
        )
    )
    if (
        container.get("Name") != expected_container_name
        or container.get("Image") != expected_image
        or not isinstance(config, dict)
        or config.get("Image") != expected_image
        or not isinstance(labels, dict)
        or any(
            labels.get(key) != value
            for key, value in {
                "com.docker.compose.project": request["project_name"],
                "com.docker.compose.service": service,
                "trading-bot.production.operation-id": request[
                    "operation_id"
                ],
            }.items()
        )
        or any(
            labels.get(key) != value
            for key, value in compose_contract["labels"].items()
        )
        or config_hash != compose_contract["config_hash"]
        or observed_environment != compose_contract["environment"]
        or labels.get("com.docker.compose.oneoff") not in {None, "False"}
        or any(
            key not in image_labels
            and key != "trading-bot.production.operation-id"
            and not key.startswith("com.docker.compose.")
            for key in labels
        )
        or not isinstance(host, dict)
        or host.get("Privileged") is not False
        or host.get("AutoRemove") is not False
        or host.get("ReadonlyRootfs") is not False
        or host.get("PortBindings") not in (None, {})
        or host.get("NetworkMode") != expected_network_name
        or not isinstance(restart_policy, dict)
        or restart_policy.get("Name") != "unless-stopped"
        or not state_is_exact
        or not isinstance(attachments, dict)
        or set(attachments) != {expected_network_name}
        or not isinstance(mounts, list)
        or len(mounts) != 1
    ):
        raise GlobalDockerInventoryError(
            "prepared database container contract differs"
        )
    mount = mounts[0]
    if (
        not isinstance(mount, dict)
        or mount.get("Type") != "bind"
        or mount.get("Source") != str(expected_mount_source)
        or mount.get("Destination") != "/var/lib/postgresql/data"
        or mount.get("RW") is not True
        or mount.get("Propagation") not in {None, "rprivate"}
    ):
        raise GlobalDockerInventoryError(
            "prepared database mount escaped its operation root"
        )
    network_id = network.get("Id")
    network_labels = network.get("Labels")
    network_containers = network.get("Containers")
    attachment = attachments[expected_network_name]
    attached_endpoint = (
        isinstance(attachment, dict)
        and attachment.get("NetworkID") == network_id
        and isinstance(attachment.get("EndpointID"), str)
        and NETWORK_ID_RE.fullmatch(attachment["EndpointID"]) is not None
        and isinstance(network_containers, dict)
        and set(network_containers) == {container["Id"]}
        and isinstance(network_containers[container["Id"]], dict)
        and network_containers[container["Id"]].get("EndpointID")
        == attachment["EndpointID"]
    )
    detached_endpoint = (
        not running_expected
        and isinstance(attachment, dict)
        and attachment.get("NetworkID") in {"", network_id}
        and attachment.get("EndpointID") == ""
        and isinstance(network_containers, dict)
        and not network_containers
    )
    options = network.get("Options")
    ipam = network.get("IPAM")
    ipam_config = ipam.get("Config") if isinstance(ipam, dict) else None
    if not isinstance(ipam_config, list) or len(ipam_config) != 1:
        raise GlobalDockerInventoryError(
            "prepared internal network IPAM closure differs"
        )
    ipam_entry = ipam_config[0]
    try:
        subnet = ipaddress.ip_network(
            ipam_entry.get("Subnet"),
            strict=True,
        )
        gateway = ipaddress.ip_address(ipam_entry.get("Gateway"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise GlobalDockerInventoryError(
            "prepared internal network IPAM is invalid"
        ) from exc
    if (
        not isinstance(network_id, str)
        or NETWORK_ID_RE.fullmatch(network_id) is None
        or network.get("Name") != expected_network_name
        or network.get("Driver") != "bridge"
        or network.get("Scope") != "local"
        or network.get("Internal") is not True
        or network.get("Attachable") is not False
        or network.get("Ingress") is not False
        or network.get("ConfigOnly") not in {None, False}
        or options not in (None, {})
        or not isinstance(network_labels, dict)
        or network_labels.get("com.docker.compose.project")
        != request["project_name"]
        or network_labels.get("com.docker.compose.network")
        != role_network
        or network_labels.get("trading-bot.production.operation-id")
        != request["operation_id"]
        or any(
            key != "trading-bot.production.operation-id"
            and not key.startswith("com.docker.compose.")
            for key in network_labels
        )
        or not isinstance(ipam, dict)
        or set(ipam) != {"Driver", "Options", "Config"}
        or ipam.get("Driver") != "default"
        or ipam.get("Options") not in (None, {})
        or not isinstance(ipam_entry, dict)
        or set(ipam_entry) != {"Subnet", "Gateway"}
        or subnet.version != 4
        or not subnet.is_private
        or str(subnet) != ipam_entry["Subnet"]
        or str(gateway) != ipam_entry["Gateway"]
        or gateway not in subnet
        or gateway in {subnet.network_address, subnet.broadcast_address}
        or not (
            attached_endpoint
            if running_expected
            else attached_endpoint or detached_endpoint
        )
    ):
        raise GlobalDockerInventoryError(
            "prepared internal network contract differs"
        )
    public = _prepared_public_digests(container)
    environment_sha256 = _sha256(
        canonical_json(
            {
                "schema": "production-shadow-prepared-env-binding-v1",
                "controller_challenge_sha256": request[
                    "controller_challenge_sha256"
                ],
                "environment": observed_environment,
            }
        )
    )
    if request["expected_database_state"] == "stopped":
        expected = {
            "container_id": request["expected_prepared_container_id"],
            "network_id": request["expected_prepared_network_id"],
            "config_sha256": request["expected_prepared_config_sha256"],
            "compose_config_sha256": request[
                "expected_prepared_compose_config_sha256"
            ],
            "host_config_sha256": request[
                "expected_prepared_host_config_sha256"
            ],
            "mounts_sha256": request["expected_prepared_mounts_sha256"],
            "network_identity_sha256": request[
                "expected_prepared_network_identity_sha256"
            ],
            "network_metadata_sha256": request[
                "expected_prepared_network_metadata_sha256"
            ],
        }
        observed = {
            "container_id": container["Id"],
            "network_id": network_id,
            "config_sha256": public["config"],
            "compose_config_sha256": config_hash,
            "host_config_sha256": public["host_config"],
            "mounts_sha256": public["mounts"],
            "network_identity_sha256": (
                network_descriptor.identity_sha256
            ),
            "network_metadata_sha256": (
                network_descriptor.metadata_sha256
            ),
        }
        if observed != expected:
            raise GlobalDockerInventoryError(
                "stopped prepared clone differs from its running baseline"
            )
    return {
        "container_id": container["Id"],
        "network_id": network_id,
        "container_descriptor": container_descriptor,
        "network_descriptor": network_descriptor,
        "config_sha256": public["config"],
        "environment_sha256": environment_sha256,
        "environment_entry_count": len(observed_environment),
        "compose_config_sha256": config_hash,
        "host_config_sha256": public["host_config"],
        "mounts_sha256": public["mounts"],
        "network_attachment_sha256": public[
            "network_attachment"
        ],
        "running": running_expected,
        "healthy": running_expected,
    }


def _same_capture(
    first: CapturedInventory,
    second: CapturedInventory,
) -> bool:
    return [item.document() for item in first.descriptors] == [
        item.document() for item in second.descriptors
    ]


def execute_request(
    request_value: Mapping[str, Any],
    *,
    runner: DockerRunner | None = None,
    observed_host_addresses: set[str] | None = None,
) -> dict[str, Any]:
    request = validate_request(request_value)
    worker, observed_ipv4 = _verify_execution_context(
        request,
        observed_host_addresses=observed_host_addresses,
    )
    active_runner = CaptureBudget(
        runner=runner or SubprocessDockerRunner(),
        started_at=time.monotonic(),
    )
    first = _capture_once(request, runner=active_runner)
    first_host_config_sha256 = _validate_operation_closure(
        request,
        first,
        worker=worker,
    )
    second = _capture_once(request, runner=active_runner)
    second_host_config_sha256 = _validate_operation_closure(
        request,
        second,
        worker=worker,
    )
    if not _same_capture(first, second):
        raise GlobalDockerInventoryError(
            "Docker inventory did not produce two stable consecutive roots"
        )
    if first_host_config_sha256 != second_host_config_sha256:
        raise GlobalDockerInventoryError(
            "operation HostConfig changed between stable captures"
        )
    all_descriptors = list(second.descriptors)
    non_operation = [
        item for item in all_descriptors if not item.operation_match
    ]
    operation = [
        item for item in all_descriptors if item.operation_match
    ]
    all_roots = _descriptor_roots(all_descriptors)
    non_operation_roots = _descriptor_roots(non_operation)
    operation_roots = _descriptor_roots(operation)
    result: dict[str, Any] = {
        "schema": RESPONSE_SCHEMA,
        "status": "captured-stable",
        "action": request["action"],
        "campaign_id": request["campaign_id"],
        "operation_id": request["operation_id"],
        "release_sha": request["release_sha"],
        "release_tree_sha": request["release_tree_sha"],
        "restore_generation_sha256": request[
            "restore_generation_sha256"
        ],
        "role": request["role"],
        "expected_host": request["expected_host"],
        "observed_host_ipv4": observed_ipv4,
        "project_base": request["project_base"],
        "project_name": request["project_name"],
        "request_binding_sha256": request["request_binding_sha256"],
        "expected_operation_container_id": request[
            "expected_operation_container_id"
        ],
        "expected_operation_host_config_sha256": request[
            "expected_operation_host_config_sha256"
        ],
        "observed_operation_host_config_sha256": (
            second_host_config_sha256
        ),
        "inventory_root_sha256": all_roots["inventory"],
        "inventory_identity_root_sha256": all_roots["identity"],
        "inventory_state_root_sha256": all_roots["state"],
        "inventory_metadata_root_sha256": all_roots["metadata"],
        "resource_counts": _counts(all_descriptors),
        "non_operation_inventory_root_sha256": (
            non_operation_roots["inventory"]
        ),
        "non_operation_identity_root_sha256": (
            non_operation_roots["identity"]
        ),
        "non_operation_state_root_sha256": non_operation_roots["state"],
        "non_operation_metadata_root_sha256": (
            non_operation_roots["metadata"]
        ),
        "non_operation_resource_counts": _counts(non_operation),
        "operation_resource_root_sha256": operation_roots["inventory"],
        "operation_resource_counts": _counts(operation),
        "stable_capture_count": 2,
        "descriptors_returned": False,
        "docker_read_only": True,
        "network_io_performed": False,
        "filesystem_mutated": False,
    }
    result["response_sha256"] = _sha256(canonical_json(result))
    return validate_response(result, request=request)


def validate_prepared_response(
    value: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    request = validate_prepared_request(request, now=now)
    if (
        not isinstance(value, Mapping)
        or set(value) != PREPARED_RESPONSE_FIELDS
    ):
        raise GlobalDockerInventoryError(
            "prepared inventory response fields are not exact"
        )
    document = json.loads(canonical_json(dict(value)).decode("ascii"))
    if len(canonical_json(document)) > MAX_RESPONSE_BYTES:
        raise GlobalDockerInventoryError(
            "prepared inventory response exceeds its bound"
        )
    identity_fields = (
        "action",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "expected_host",
        "controller_challenge_sha256",
        "issued_at",
        "expires_at",
        "expected_database_state",
        "baseline_response_sha256",
        "contract_kind",
        "project_base",
        "project_name",
        "request_binding_sha256",
        "role_manifest_sha256",
    )
    if (
        document["schema"] != PREPARED_RESPONSE_SCHEMA
        or document["status"] != "captured-prepared-stable"
        or any(document[field] != request[field] for field in identity_fields)
        or document["stable_capture_count"] != 2
        or document["prepared_database_running"]
        is not (
            request["expected_database_state"] == "running-healthy"
        )
        or document["prepared_database_healthy"]
        is not (
            request["expected_database_state"] == "running-healthy"
        )
        or document["descriptors_returned"] is not False
        or document["environment_values_returned"] is not False
        or document["path_descriptors_returned"] is not False
        or document["docker_read_only"] is not True
        or document["network_io_performed"] is not False
        or document["filesystem_mutated"] is not False
        or not isinstance(document["observed_host_ipv4"], list)
        or document["observed_host_ipv4"]
        != sorted(set(document["observed_host_ipv4"]))
        or request["expected_host"] not in document["observed_host_ipv4"]
    ):
        raise GlobalDockerInventoryError(
            "prepared inventory response identity or safety boundary differs"
        )
    observed_now = (
        datetime.now(timezone.utc)
        if now is None
        else _aware_utc_datetime(
            now,
            label="prepared response validation time",
        )
    )
    issued_at = _parse_utc_timestamp(
        request["issued_at"],
        label="prepared request issued_at",
    )
    expires_at = _parse_utc_timestamp(
        request["expires_at"],
        label="prepared request expires_at",
    )
    captured_at = _parse_utc_timestamp(
        document["captured_at"],
        label="prepared response captured_at",
    )
    if (
        captured_at
        < issued_at
        - timedelta(seconds=PREPARED_REQUEST_FUTURE_SKEW_SECONDS)
        or captured_at > expires_at
        or captured_at
        > observed_now
        + timedelta(seconds=PREPARED_REQUEST_FUTURE_SKEW_SECONDS)
        or observed_now > expires_at
    ):
        raise GlobalDockerInventoryError(
            "prepared inventory response is stale or from the future"
        )
    for address in document["observed_host_ipv4"]:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise GlobalDockerInventoryError(
                "prepared response contains an invalid host address"
            ) from exc
        if parsed.version != 4 or str(parsed) != address:
            raise GlobalDockerInventoryError(
                "prepared response host address is not canonical IPv4"
            )
    for field in (
        "prepared_container_identity_sha256",
        "prepared_container_state_sha256",
        "prepared_container_metadata_sha256",
        "prepared_network_identity_sha256",
        "prepared_network_state_sha256",
        "prepared_network_metadata_sha256",
        "prepared_config_sha256",
        "prepared_environment_sha256",
        "prepared_compose_config_sha256",
        "prepared_host_config_sha256",
        "prepared_mounts_sha256",
        "prepared_network_attachment_sha256",
        "prepared_redis_identity_sha256",
        "prepared_redis_chain_metadata_sha256",
        "prepared_redis_metadata_sha256",
        "inventory_root_sha256",
        "inventory_identity_root_sha256",
        "inventory_state_root_sha256",
        "inventory_metadata_root_sha256",
        "non_operation_inventory_root_sha256",
        "non_operation_identity_root_sha256",
        "non_operation_state_root_sha256",
        "non_operation_metadata_root_sha256",
        "operation_resource_root_sha256",
        "response_sha256",
    ):
        _nonzero_sha256(document[field], label=field)
    if (
        not isinstance(document["prepared_container_id"], str)
        or CONTAINER_ID_RE.fullmatch(
            document["prepared_container_id"]
        )
        is None
        or not isinstance(document["prepared_network_id"], str)
        or NETWORK_ID_RE.fullmatch(document["prepared_network_id"])
        is None
    ):
        raise GlobalDockerInventoryError(
            "prepared response resource identity is invalid"
        )
    if (
        type(document["prepared_environment_entry_count"]) is not int
        or not 1 <= document["prepared_environment_entry_count"] <= 4096
        or document["prepared_redis_target_count"] != 1
        or document["prepared_redis_unsafe_path_count"] != 0
        or document["prepared_redis_entry_count"] != 0
        or document["prepared_redis_pristine"] is not True
    ):
        raise GlobalDockerInventoryError(
            "prepared environment or Redis attestation is invalid"
        )
    total = _validate_counts(
        document["resource_counts"],
        label="prepared resource counts",
    )
    non_operation = _validate_counts(
        document["non_operation_resource_counts"],
        label="prepared non-operation resource counts",
    )
    operation = _validate_counts(
        document["operation_resource_counts"],
        label="prepared operation resource counts",
    )
    if (
        operation
        != {
            "container": 1,
            "network": 1,
            "volume": 0,
            "image": 0,
        }
        or any(
            total[kind] != non_operation[kind] + operation[kind]
            for kind in RESOURCE_KINDS
        )
    ):
        raise GlobalDockerInventoryError(
            "prepared response resource partition differs"
        )
    if request["expected_database_state"] == "stopped":
        expected_non_operation = {
            "non_operation_inventory_root_sha256": request[
                "expected_non_operation_inventory_root_sha256"
            ],
            "non_operation_identity_root_sha256": request[
                "expected_non_operation_identity_root_sha256"
            ],
            "non_operation_state_root_sha256": request[
                "expected_non_operation_state_root_sha256"
            ],
            "non_operation_metadata_root_sha256": request[
                "expected_non_operation_metadata_root_sha256"
            ],
            "non_operation_resource_counts": request[
                "expected_non_operation_resource_counts"
            ],
        }
        if any(
            document[field] != expected
            for field, expected in expected_non_operation.items()
        ):
            raise GlobalDockerInventoryError(
                "non-operation inventory changed during startup normalization"
            )
        if (
            document["prepared_redis_identity_sha256"]
            != request["expected_prepared_redis_identity_sha256"]
            or document["prepared_redis_chain_metadata_sha256"]
            != request["expected_prepared_redis_chain_metadata_sha256"]
            or document["prepared_redis_metadata_sha256"]
            != request["expected_prepared_redis_metadata_sha256"]
        ):
            raise GlobalDockerInventoryError(
                "prepared Redis target changed during startup normalization"
            )
    unsigned = {
        key: item
        for key, item in document.items()
        if key != "response_sha256"
    }
    if document["response_sha256"] != _sha256(canonical_json(unsigned)):
        raise GlobalDockerInventoryError(
            "prepared response SHA-256 differs"
        )
    return document


def execute_prepared_request(
    request_value: Mapping[str, Any],
    *,
    runner: DockerRunner | None = None,
    observed_host_addresses: set[str] | None = None,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    if now is not None and clock is not None:
        raise GlobalDockerInventoryError(
            "prepared inventory clock inputs are ambiguous"
        )
    active_clock = clock or (lambda: datetime.now(timezone.utc))
    request_now = (
        _aware_utc_datetime(
            active_clock(),
            label="prepared request clock",
        )
        if now is None
        else _aware_utc_datetime(
            now,
            label="prepared request time",
        )
    )
    request = validate_prepared_request(
        request_value,
        now=request_now,
    )
    worker, manifest, paths, observed_ipv4 = (
        _verify_prepared_execution_context(
            request,
            observed_host_addresses=observed_host_addresses,
        )
    )
    active_runner = CaptureBudget(
        runner=runner or SubprocessDockerRunner(),
        started_at=time.monotonic(),
    )
    first_redis = _attest_prepared_redis_target(
        request,
        worker=worker,
        manifest=manifest,
        paths=paths,
    )
    first = _capture_once(request, runner=active_runner)
    first_prepared = _validate_prepared_operation_closure(
        request,
        first,
        worker=worker,
        manifest=manifest,
        paths=paths,
        runner=active_runner,
    )
    second = _capture_once(request, runner=active_runner)
    second_prepared = _validate_prepared_operation_closure(
        request,
        second,
        worker=worker,
        manifest=manifest,
        paths=paths,
        runner=active_runner,
    )
    second_redis = _attest_prepared_redis_target(
        request,
        worker=worker,
        manifest=manifest,
        paths=paths,
    )
    if (
        not _same_capture(first, second)
        or first_redis != second_redis
        or {
            key: value
            for key, value in first_prepared.items()
            if not key.endswith("_descriptor")
        }
        != {
            key: value
            for key, value in second_prepared.items()
            if not key.endswith("_descriptor")
        }
    ):
        raise GlobalDockerInventoryError(
            "prepared Docker inventory did not produce stable consecutive roots"
        )
    if request["expected_database_state"] == "stopped" and (
        second_redis["identity_sha256"]
        != request["expected_prepared_redis_identity_sha256"]
        or second_redis["chain_metadata_sha256"]
        != request["expected_prepared_redis_chain_metadata_sha256"]
        or second_redis["metadata_sha256"]
        != request["expected_prepared_redis_metadata_sha256"]
    ):
        raise GlobalDockerInventoryError(
            "stopped prepared Redis target differs from its running baseline"
        )
    captured_now = (
        _aware_utc_datetime(
            active_clock(),
            label="prepared capture clock",
        )
        if now is None
        else _aware_utc_datetime(
            now,
            label="prepared capture time",
        )
    )
    validate_prepared_request(request, now=captured_now)
    all_descriptors = list(second.descriptors)
    non_operation = [
        item for item in all_descriptors if not item.operation_match
    ]
    operation = [
        item for item in all_descriptors if item.operation_match
    ]
    all_roots = _descriptor_roots(all_descriptors)
    non_operation_roots = _descriptor_roots(non_operation)
    operation_roots = _descriptor_roots(operation)
    container_descriptor = second_prepared["container_descriptor"]
    network_descriptor = second_prepared["network_descriptor"]
    result: dict[str, Any] = {
        "schema": PREPARED_RESPONSE_SCHEMA,
        "status": "captured-prepared-stable",
        **{
            field: request[field]
            for field in (
                "action",
                "campaign_id",
                "operation_id",
                "release_sha",
                "release_tree_sha",
                "role",
                "expected_host",
                "controller_challenge_sha256",
                "issued_at",
                "expires_at",
                "expected_database_state",
                "baseline_response_sha256",
                "contract_kind",
                "project_base",
                "project_name",
                "request_binding_sha256",
                "role_manifest_sha256",
            )
        },
        "observed_host_ipv4": observed_ipv4,
        "captured_at": canonical_utc_timestamp(captured_now),
        "prepared_container_id": second_prepared["container_id"],
        "prepared_network_id": second_prepared["network_id"],
        "prepared_container_identity_sha256": (
            container_descriptor.identity_sha256
        ),
        "prepared_container_state_sha256": (
            container_descriptor.state_sha256
        ),
        "prepared_container_metadata_sha256": (
            container_descriptor.metadata_sha256
        ),
        "prepared_network_identity_sha256": (
            network_descriptor.identity_sha256
        ),
        "prepared_network_state_sha256": (
            network_descriptor.state_sha256
        ),
        "prepared_network_metadata_sha256": (
            network_descriptor.metadata_sha256
        ),
        "prepared_config_sha256": second_prepared["config_sha256"],
        "prepared_environment_sha256": second_prepared[
            "environment_sha256"
        ],
        "prepared_environment_entry_count": second_prepared[
            "environment_entry_count"
        ],
        "prepared_compose_config_sha256": second_prepared[
            "compose_config_sha256"
        ],
        "prepared_host_config_sha256": second_prepared[
            "host_config_sha256"
        ],
        "prepared_mounts_sha256": second_prepared["mounts_sha256"],
        "prepared_network_attachment_sha256": second_prepared[
            "network_attachment_sha256"
        ],
        "prepared_redis_identity_sha256": second_redis[
            "identity_sha256"
        ],
        "prepared_redis_chain_metadata_sha256": second_redis[
            "chain_metadata_sha256"
        ],
        "prepared_redis_metadata_sha256": second_redis[
            "metadata_sha256"
        ],
        "prepared_redis_target_count": second_redis["target_count"],
        "prepared_redis_unsafe_path_count": second_redis[
            "unsafe_path_count"
        ],
        "prepared_redis_entry_count": second_redis["entry_count"],
        "prepared_redis_pristine": second_redis["pristine"],
        "inventory_root_sha256": all_roots["inventory"],
        "inventory_identity_root_sha256": all_roots["identity"],
        "inventory_state_root_sha256": all_roots["state"],
        "inventory_metadata_root_sha256": all_roots["metadata"],
        "resource_counts": _counts(all_descriptors),
        "non_operation_inventory_root_sha256": (
            non_operation_roots["inventory"]
        ),
        "non_operation_identity_root_sha256": (
            non_operation_roots["identity"]
        ),
        "non_operation_state_root_sha256": non_operation_roots["state"],
        "non_operation_metadata_root_sha256": (
            non_operation_roots["metadata"]
        ),
        "non_operation_resource_counts": _counts(non_operation),
        "operation_resource_root_sha256": operation_roots["inventory"],
        "operation_resource_counts": _counts(operation),
        "stable_capture_count": 2,
        "prepared_database_running": second_prepared["running"],
        "prepared_database_healthy": second_prepared["healthy"],
        "descriptors_returned": False,
        "environment_values_returned": False,
        "path_descriptors_returned": False,
        "docker_read_only": True,
        "network_io_performed": False,
        "filesystem_mutated": False,
    }
    result["response_sha256"] = _sha256(canonical_json(result))
    return validate_prepared_response(
        result,
        request=request,
        now=captured_now,
    )


def compare_non_operation_inventories(
    before_value: Mapping[str, Any],
    after_value: Mapping[str, Any],
    *,
    before_request: Mapping[str, Any],
    after_request: Mapping[str, Any],
) -> dict[str, Any]:
    before = validate_response(before_value, request=before_request)
    after = validate_response(after_value, request=after_request)
    if (
        before["action"] != "capture-before"
        or after["action"] != "capture-after"
    ):
        raise GlobalDockerInventoryError(
            "inventory comparison requires an ordered before/after pair"
        )
    identity_fields = (
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "restore_generation_sha256",
        "role",
        "expected_host",
        "project_base",
        "project_name",
    )
    if any(before[field] != after[field] for field in identity_fields):
        raise GlobalDockerInventoryError(
            "before and after inventory identities differ"
        )
    comparison_fields = (
        "non_operation_inventory_root_sha256",
        "non_operation_identity_root_sha256",
        "non_operation_state_root_sha256",
        "non_operation_metadata_root_sha256",
        "non_operation_resource_counts",
    )
    if any(before[field] != after[field] for field in comparison_fields):
        raise GlobalDockerInventoryError(
            "non-operation Docker inventory changed"
        )
    result: dict[str, Any] = {
        "schema": COMPARISON_SCHEMA,
        "status": "verified-zero-delta",
        **{
            field: before[field]
            for field in identity_fields
            if field != "expected_host"
        },
        "before_response_sha256": before["response_sha256"],
        "after_response_sha256": after["response_sha256"],
        **{field: before[field] for field in comparison_fields},
        "non_operation_resource_delta_count": 0,
    }
    result["comparison_sha256"] = _sha256(canonical_json(result))
    return validate_comparison(
        result,
        before=before,
        after=after,
    )


def validate_comparison(
    value: Mapping[str, Any],
    *,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != COMPARISON_FIELDS:
        raise GlobalDockerInventoryError(
            "inventory comparison fields are not exact"
        )
    document = json.loads(canonical_json(dict(value)).decode("ascii"))
    if (
        document["schema"] != COMPARISON_SCHEMA
        or document["status"] != "verified-zero-delta"
        or document["non_operation_resource_delta_count"] != 0
        or document["before_response_sha256"] != before["response_sha256"]
        or document["after_response_sha256"] != after["response_sha256"]
    ):
        raise GlobalDockerInventoryError(
            "inventory comparison identity differs"
        )
    for field in (
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "restore_generation_sha256",
        "role",
        "project_base",
        "project_name",
        "non_operation_inventory_root_sha256",
        "non_operation_identity_root_sha256",
        "non_operation_state_root_sha256",
        "non_operation_metadata_root_sha256",
        "non_operation_resource_counts",
    ):
        if document[field] != before[field] or document[field] != after[field]:
            raise GlobalDockerInventoryError(
                "inventory comparison does not bind both captures"
            )
    _validate_counts(
        document["non_operation_resource_counts"],
        label="comparison resource counts",
    )
    for field in (
        "before_response_sha256",
        "after_response_sha256",
        "non_operation_inventory_root_sha256",
        "non_operation_identity_root_sha256",
        "non_operation_state_root_sha256",
        "non_operation_metadata_root_sha256",
        "comparison_sha256",
    ):
        _nonzero_sha256(document[field], label=field)
    unsigned = {
        key: item
        for key, item in document.items()
        if key != "comparison_sha256"
    }
    if document["comparison_sha256"] != _sha256(canonical_json(unsigned)):
        raise GlobalDockerInventoryError(
            "inventory comparison SHA-256 differs"
        )
    return document


def _host_stdio() -> dict[str, Any]:
    raw = sys.stdin.buffer.readline(MAX_CONTROL_BYTES + 2)
    if (
        not raw
        or len(raw) > MAX_CONTROL_BYTES + 1
        or not raw.endswith(b"\n")
        or sys.stdin.buffer.read(1) != b""
    ):
        raise GlobalDockerInventoryError(
            "host control request is missing, oversized, or trailing"
        )
    document = strict_json(raw[:-1], label="host control request")
    if document.get("schema") == PREPARED_REQUEST_SCHEMA:
        request = validate_prepared_request(document)
        return execute_prepared_request(request)
    request = validate_request(document)
    return execute_request(request)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    try:
        if arguments != ("--host-stdio",):
            raise GlobalDockerInventoryError(
                "exact --host-stdio mode is required"
            )
        result: Mapping[str, Any] = _host_stdio()
        status = 0
    except GlobalDockerInventoryError:
        result = {
            "schema": RESPONSE_SCHEMA,
            "status": "blocked",
            "error": "global Docker inventory failed closed",
            "error_class": "GlobalDockerInventoryError",
        }
        status = 1
    except Exception:
        result = {
            "schema": RESPONSE_SCHEMA,
            "status": "blocked",
            "error": "global Docker inventory failed closed",
            "error_class": "GlobalDockerInventoryError",
        }
        status = 1
    payload = canonical_json(result)
    if len(payload) > MAX_RESPONSE_BYTES:
        payload = canonical_json(
            {
                "schema": RESPONSE_SCHEMA,
                "status": "blocked",
                "error": "global Docker inventory response exceeded its bound",
                "error_class": "GlobalDockerInventoryError",
            }
        )
        status = 1
    sys.stdout.buffer.write(payload + b"\n")
    sys.stdout.buffer.flush()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
