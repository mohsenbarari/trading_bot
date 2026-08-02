#!/usr/bin/env python3
"""Read-only collision gate for Emergency IR Standalone on WA-IR.

This control is intentionally narrower than activation: it neither creates a
receipt nor invokes Docker Compose.  It only reads the local Docker inventory
and listening TCP sockets, then fails closed if the isolated Emergency project
could overlap the permanent WA-IR stack by project, container, port, bind
path, named volume, network name, or network subnet.

The command list is fixed below and contains only Docker list/inspect calls
against the local Unix socket plus ``ss -H -ltn``.  There is no ``compose``,
``up``, ``start``, ``pull``, ``load``, ``rm``, ``down``, ``restart``, or write
path in this module.  It is therefore safe to run before the database stage
and must pass before activation is allowed to create any Docker resource.
"""

from __future__ import annotations

# This guard intentionally precedes every non-builtin import and every host
# action.  The preflight later loads a sealed sibling from an explicit path;
# isolation prevents ambient import and environment state from influencing the
# control before that boundary is established.
import sys

if __name__ == "__main__" and (
    not sys.flags.isolated or not sys.flags.dont_write_bytecode
):
    raise SystemExit(
        "Emergency host-isolation preflight must be launched with python3 -I -B"
    )

import argparse
import dataclasses
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from types import ModuleType
from typing import Any, Callable, Mapping, Sequence


DOCKER_BINARY = "/usr/bin/docker"
SS_BINARY = "/usr/bin/ss"
LOCAL_DOCKER_SOCKET = "unix:///var/run/docker.sock"
PROJECT_NAME = "trading-bot-emergency-ir"
EMERGENCY_APP_PORT = 18000
EMERGENCY_RUNTIME_ENV_FILE = "/etc/trading-bot-emergency/standalone/runtime.env"
EMERGENCY_TRADING_SETTINGS_FILE = "/srv/trading-bot-emergency/current/trading_settings.json"
PROTECTED_HOST_ROOTS = (
    "/srv/trading-bot-emergency",
    "/etc/trading-bot-emergency",
)
BASE_SERVICES = ("db", "redis", "migration", "api")
SMS_SERVICES = ("sms-egress",)
BASE_VOLUMES = (
    "trading-bot-emergency-ir-postgres",
    "trading-bot-emergency-ir-redis",
    "trading-bot-emergency-ir-uploads",
    "trading-bot-emergency-ir-audit",
)
BASE_NETWORKS = ("trading-bot-emergency-ir-net",)
SMS_NETWORKS = (
    "trading-bot-emergency-ir-sms-relay",
    "trading-bot-emergency-ir-sms-egress",
)
BASE_SUBNETS = ("172.29.250.0/28",)
SMS_SUBNETS = ("172.29.251.0/29", "172.29.252.0/29")

MAX_RUNTIME_ENV_BYTES = 256 * 1024
COMPOSE_CONTRACT_VALIDATOR_NAME = "validate_emergency_ir_compose_contract.py"
MAX_COMPOSE_CONTRACT_VALIDATOR_BYTES = 4 * 1024 * 1024
MAX_DOCKER_OBJECTS = 1024
MAX_DOCKER_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_SS_OUTPUT_BYTES = 2 * 1024 * 1024
DOCKER_ID_RE = re.compile(r"^[0-9a-f]{12,64}$", re.ASCII)
DOCKER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$", re.ASCII)
PORT_RE = re.compile(r"^[0-9]{1,5}$", re.ASCII)

READ_ONLY_ACTIONS = (
    "docker.ps",
    "docker.inspect",
    "docker.volume.ls",
    "docker.volume.inspect",
    "docker.network.ls",
    "docker.network.inspect",
    "ss.listen-tcp",
)
MUTATING_ACTIONS: tuple[str, ...] = ()
IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class EmergencyHostIsolationError(RuntimeError):
    """The host cannot prove a collision-free Emergency Docker namespace."""


@dataclasses.dataclass(frozen=True)
class DockerContainer:
    name: str
    project: str | None
    bind_sources: tuple[str, ...]
    volume_names: tuple[str, ...]
    network_names: tuple[str, ...]
    host_ports: tuple[int, ...]


@dataclasses.dataclass(frozen=True)
class DockerVolume:
    name: str
    project: str | None


@dataclasses.dataclass(frozen=True)
class DockerNetwork:
    name: str
    project: str | None
    subnets: tuple[IpNetwork, ...]


@dataclasses.dataclass(frozen=True)
class HostInventory:
    containers: tuple[DockerContainer, ...]
    volumes: tuple[DockerVolume, ...]
    networks: tuple[DockerNetwork, ...]
    listening_ports: frozenset[int]


def _fail(message: str) -> None:
    raise EmergencyHostIsolationError(message)


def _require_root() -> None:
    if os.geteuid() != 0:
        _fail("Emergency host-isolation preflight must run as root")


def _canonical_absolute(path: Path | str, *, label: str) -> Path:
    raw = str(path)
    candidate = Path(raw)
    if (
        not raw
        or "\x00" in raw
        or not candidate.is_absolute()
        or raw.startswith("//")
        or raw != os.path.normpath(raw)
    ):
        _fail(f"{label} path is invalid")
    return candidate


def _safe_directory_chain(path: Path, *, label: str) -> None:
    path = _canonical_absolute(path, label=label)
    current = Path("/")
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise EmergencyHostIsolationError(f"{label} parent cannot be inspected") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        sticky_tmp = (
            current == Path("/tmp")
            and stat.S_ISDIR(metadata.st_mode)
            and metadata.st_uid == 0
            and bool(mode & stat.S_ISVTX)
        )
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or ((mode & 0o022) and not sticky_tmp)
        ):
            _fail(f"{label} parent is not root-controlled")


def _read_root_regular(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
    private: bool,
) -> bytes:
    path = _canonical_absolute(path, label=label)
    _safe_directory_chain(path.parent, label=label)
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if type(no_follow) is not int:
        _fail("host preflight requires O_NOFOLLOW support")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | no_follow,
        )
    except OSError as exc:
        raise EmergencyHostIsolationError(f"{label} cannot be read") from exc
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        prohibited = 0o077 if private else 0o022
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or mode & prohibited
            or not 1 <= before.st_size <= maximum_bytes
        ):
            _fail(f"{label} must be a bounded root-controlled regular file")
        data = bytearray()
        while len(data) <= maximum_bytes:
            chunk = os.read(descriptor, min(65536, maximum_bytes + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
        identity = (
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
            len(data) != before.st_size
            or len(data) > maximum_bytes
            or any(getattr(before, item) != getattr(after, item) for item in identity)
        ):
            _fail(f"{label} changed while being read")
        return bytes(data)
    finally:
        os.close(descriptor)


def _require_trusted_executable(path: Path, *, label: str) -> None:
    """Require the fixed external inspector to be root-controlled.

    The preflight never resolves a command through ``PATH``.  This additional
    check makes that property explicit: a writable/symlinked replacement for
    one of the two fixed inspection binaries is rejected before it can run.
    Root is the trusted host administrator for this control, so a regular
    root-owned executable below root-controlled directories is sufficient.
    """

    path = _canonical_absolute(path, label=label)
    _safe_directory_chain(path.parent, label=label)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise EmergencyHostIsolationError(f"{label} cannot be inspected") from exc
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or mode & 0o022
        or not mode & 0o111
    ):
        _fail(f"{label} is not a root-controlled executable")


def _parse_runtime_env(path: Path) -> Mapping[str, str]:
    raw = _read_root_regular(
        path,
        label="Emergency runtime environment",
        maximum_bytes=MAX_RUNTIME_ENV_BYTES,
        private=True,
    )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EmergencyHostIsolationError("Emergency runtime environment is not UTF-8") from exc
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or key in values or not DOCKER_NAME_RE.fullmatch(key):
            _fail(f"Emergency runtime environment line {number} is malformed")
        values[key] = value
    return values


def _require_runtime_contract(path: Path) -> None:
    values = _parse_runtime_env(path)
    expected = {
        "COMPOSE_PROJECT_NAME": PROJECT_NAME,
        "EMERGENCY_RUNTIME_ENV_FILE": EMERGENCY_RUNTIME_ENV_FILE,
        "EMERGENCY_APP_PORT": str(EMERGENCY_APP_PORT),
        "EMERGENCY_TRADING_SETTINGS_FILE": EMERGENCY_TRADING_SETTINGS_FILE,
    }
    if any(values.get(key) != required for key, required in expected.items()):
        _fail("Emergency runtime environment does not retain the fixed isolation namespace")


def _fixed_compose_contract_validator_path() -> Path:
    """Resolve only the sealed validator beside this preflight, never via PATH."""

    preflight = _canonical_absolute(Path(__file__), label="Emergency host-isolation preflight")
    _safe_directory_chain(preflight.parent, label="Emergency host-isolation preflight")
    return preflight.parent / COMPOSE_CONTRACT_VALIDATOR_NAME


def _load_compose_contract_validator() -> ModuleType:
    """Load the checked sibling from bytes without an ambient Python import.

    The release package independently pins both this preflight and the
    validator.  This local check additionally makes direct use fail closed:
    only a bounded, root-controlled, non-link sibling can be executed.  The
    explicit ``compile``/``exec`` route keeps the same behavior under
    ``python3 -I -B`` and never consults ``sys.path`` or ``PYTHONPATH``.
    """

    path = _fixed_compose_contract_validator_path()
    payload = _read_root_regular(
        path,
        label="Emergency Compose contract validator",
        maximum_bytes=MAX_COMPOSE_CONTRACT_VALIDATOR_BYTES,
        private=False,
    )
    validator = ModuleType("_emergency_ir_compose_contract_validator")
    validator.__file__ = str(path)
    validator.__package__ = ""
    try:
        code = compile(payload, str(path), "exec")
        exec(code, validator.__dict__, validator.__dict__)
    except Exception as exc:
        raise EmergencyHostIsolationError(
            "Emergency Compose contract validator cannot be loaded"
        ) from exc
    if not callable(getattr(validator, "validate_contract", None)):
        _fail("Emergency Compose contract validator has no validation entrypoint")
    return validator


def _require_compose_contract(
    path: Path,
    *,
    profile: str,
    sms_compose: Path | None,
) -> None:
    """Require the sibling's immutable deep-exact JSON Compose contract."""

    try:
        validator = _load_compose_contract_validator()
        evidence = validator.validate_contract(
            base=path,
            profile=profile,
            sms=sms_compose,
        )
    except EmergencyHostIsolationError:
        raise
    except Exception as exc:
        raise EmergencyHostIsolationError(
            "Emergency Compose configuration does not retain the fixed isolation contract"
        ) from exc
    if (
        not isinstance(evidence, dict)
        or evidence.get("schema") != getattr(validator, "SCHEMA", None)
        or evidence.get("status") != "verified-local-only"
        or evidence.get("profile") != profile
        or evidence.get("docker_or_service_changed") is not False
        or evidence.get("network_action") is not False
    ):
        _fail("Emergency Compose contract validator returned invalid local-only evidence")


def _docker_command(*arguments: str) -> tuple[str, ...]:
    return (DOCKER_BINARY, "--host", LOCAL_DOCKER_SOCKET, *arguments)


def _is_permitted_read_only_command(command: Sequence[str]) -> bool:
    value = tuple(command)
    if value == _docker_command("ps", "-a", "--no-trunc", "--format", "{{.ID}}"):
        return True
    if value == _docker_command("volume", "ls", "-q"):
        return True
    if value == _docker_command("network", "ls", "-q"):
        return True
    if value == (SS_BINARY, "-H", "-ltn"):
        return True
    prefix = _docker_command("inspect")
    if value[: len(prefix)] == prefix and value[len(prefix) :] and all(
        DOCKER_ID_RE.fullmatch(item) for item in value[len(prefix) :]
    ):
        return True
    for resource in ("volume", "network"):
        prefix = _docker_command(resource, "inspect")
        if value[: len(prefix)] == prefix and value[len(prefix) :] and all(
            DOCKER_NAME_RE.fullmatch(item) for item in value[len(prefix) :]
        ):
            return True
    return False


def _run_read_only(
    command: Sequence[str],
    *,
    label: str,
    maximum_bytes: int,
    runner: Callable[..., Any] = subprocess.run,
) -> str:
    if not _is_permitted_read_only_command(command):
        _fail("host preflight attempted a command outside its read-only allowlist")
    _require_trusted_executable(Path(command[0]), label="host preflight inspection binary")
    try:
        result = runner(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencyHostIsolationError(f"{label} cannot be inspected") from exc
    if getattr(result, "returncode", 1) != 0:
        _fail(f"{label} cannot be inspected")
    raw = getattr(result, "stdout", "")
    if isinstance(raw, bytes):
        try:
            output = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EmergencyHostIsolationError(f"{label} output is invalid") from exc
    elif isinstance(raw, str):
        output = raw
    else:
        _fail(f"{label} output is invalid")
    if len(output.encode("utf-8")) > maximum_bytes:
        _fail(f"{label} output exceeds its fixed bound")
    return output


def _bounded_identifiers(text: str, *, label: str, pattern: re.Pattern[str]) -> tuple[str, ...]:
    values = tuple(item.strip() for item in text.splitlines() if item.strip())
    if len(values) > MAX_DOCKER_OBJECTS or len(set(values)) != len(values) or any(
        pattern.fullmatch(item) is None for item in values
    ):
        _fail(f"{label} inventory is invalid")
    return values


def _inspect_objects(
    command_prefix: Sequence[str],
    identifiers: Sequence[str],
    *,
    label: str,
    runner: Callable[..., Any],
) -> list[Mapping[str, Any]]:
    values: list[Mapping[str, Any]] = []
    for offset in range(0, len(identifiers), 64):
        output = _run_read_only(
            [*command_prefix, *identifiers[offset : offset + 64]],
            label=label,
            maximum_bytes=MAX_DOCKER_OUTPUT_BYTES,
            runner=runner,
        )
        try:
            payload = json.loads(output)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EmergencyHostIsolationError(f"{label} output is invalid") from exc
        if not isinstance(payload, list) or not payload or any(not isinstance(item, dict) for item in payload):
            _fail(f"{label} output is invalid")
        values.extend(payload)
        if len(values) > MAX_DOCKER_OBJECTS:
            _fail(f"{label} inventory exceeds its fixed bound")
    return values


def _labels(payload: Mapping[str, Any]) -> Mapping[str, str]:
    config = payload.get("Config")
    source = config.get("Labels") if isinstance(config, Mapping) else None
    if source is None:
        source = payload.get("Labels")
    if source is None:
        return {}
    if not isinstance(source, Mapping) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in source.items()):
        _fail("Docker object labels are invalid")
    return {key: value for key, value in source.items()}


def _container_from_payload(payload: Mapping[str, Any]) -> DockerContainer:
    raw_name = payload.get("Name")
    if not isinstance(raw_name, str) or not raw_name.startswith("/"):
        _fail("Docker container inventory is invalid")
    name = raw_name[1:]
    if DOCKER_NAME_RE.fullmatch(name) is None:
        _fail("Docker container inventory is invalid")
    mounts = payload.get("Mounts", [])
    if not isinstance(mounts, list):
        _fail("Docker container inventory is invalid")
    bind_sources: list[str] = []
    volume_names: list[str] = []
    for mount in mounts:
        if not isinstance(mount, Mapping):
            _fail("Docker container inventory is invalid")
        mount_type = mount.get("Type")
        source = mount.get("Source")
        if mount_type == "bind":
            if not isinstance(source, str):
                _fail("Docker bind mount inventory is invalid")
            bind_sources.append(source)
        elif mount_type == "volume":
            volume_name = mount.get("Name")
            if not isinstance(volume_name, str) or DOCKER_NAME_RE.fullmatch(volume_name) is None:
                _fail("Docker volume mount inventory is invalid")
            volume_names.append(volume_name)
    network_settings = payload.get("NetworkSettings", {})
    networks = network_settings.get("Networks", {}) if isinstance(network_settings, Mapping) else None
    if not isinstance(networks, Mapping) or any(not isinstance(item, str) for item in networks):
        _fail("Docker container network inventory is invalid")
    host_config = payload.get("HostConfig", {})
    port_bindings = host_config.get("PortBindings", {}) if isinstance(host_config, Mapping) else None
    if port_bindings is not None and not isinstance(port_bindings, Mapping):
        _fail("Docker container port inventory is invalid")
    network_ports = network_settings.get("Ports", {}) if isinstance(network_settings, Mapping) else {}
    if network_ports is None:
        network_ports = {}
    ports: set[int] = set()
    for bindings in (port_bindings or {}, network_ports):
        if not isinstance(bindings, Mapping):
            _fail("Docker container port inventory is invalid")
        for raw_bindings in bindings.values():
            if raw_bindings is None:
                continue
            if not isinstance(raw_bindings, list):
                _fail("Docker container port inventory is invalid")
            for binding in raw_bindings:
                if not isinstance(binding, Mapping):
                    _fail("Docker container port inventory is invalid")
                raw_port = binding.get("HostPort")
                if not isinstance(raw_port, str) or PORT_RE.fullmatch(raw_port) is None:
                    _fail("Docker container port inventory is invalid")
                port = int(raw_port)
                if not 1 <= port <= 65535:
                    _fail("Docker container port inventory is invalid")
                ports.add(port)
    return DockerContainer(
        name=name,
        project=_labels(payload).get("com.docker.compose.project"),
        bind_sources=tuple(sorted(set(bind_sources))),
        volume_names=tuple(sorted(set(volume_names))),
        network_names=tuple(sorted(networks)),
        host_ports=tuple(sorted(ports)),
    )


def _volume_from_payload(payload: Mapping[str, Any]) -> DockerVolume:
    name = payload.get("Name")
    if not isinstance(name, str) or DOCKER_NAME_RE.fullmatch(name) is None:
        _fail("Docker volume inventory is invalid")
    return DockerVolume(name=name, project=_labels(payload).get("com.docker.compose.project"))


def _network_from_payload(payload: Mapping[str, Any]) -> DockerNetwork:
    name = payload.get("Name")
    if not isinstance(name, str) or DOCKER_NAME_RE.fullmatch(name) is None:
        _fail("Docker network inventory is invalid")
    ipam = payload.get("IPAM", {})
    config = ipam.get("Config", []) if isinstance(ipam, Mapping) else None
    if not isinstance(config, list):
        _fail("Docker network inventory is invalid")
    subnets: list[IpNetwork] = []
    for entry in config:
        if not isinstance(entry, Mapping):
            _fail("Docker network inventory is invalid")
        raw_subnet = entry.get("Subnet")
        if raw_subnet is None:
            continue
        if not isinstance(raw_subnet, str):
            _fail("Docker network inventory is invalid")
        try:
            subnets.append(ipaddress.ip_network(raw_subnet, strict=False))
        except ValueError as exc:
            raise EmergencyHostIsolationError("Docker network inventory is invalid") from exc
    return DockerNetwork(
        name=name,
        project=_labels(payload).get("com.docker.compose.project"),
        subnets=tuple(subnets),
    )


def _listener_ports(text: str) -> frozenset[int]:
    ports: set[int] = set()
    for raw in text.splitlines():
        fields = raw.split()
        if len(fields) < 5 or fields[0] != "LISTEN":
            _fail("local TCP listener inventory is invalid")
        endpoint = fields[-2]
        if endpoint.startswith("["):
            _host, separator, raw_port = endpoint.rpartition("]:")
        else:
            _host, separator, raw_port = endpoint.rpartition(":")
        if not separator or PORT_RE.fullmatch(raw_port) is None:
            _fail("local TCP listener inventory is invalid")
        port = int(raw_port)
        if not 1 <= port <= 65535:
            _fail("local TCP listener inventory is invalid")
        ports.add(port)
    return frozenset(ports)


def collect_host_inventory(*, runner: Callable[..., Any] = subprocess.run) -> HostInventory:
    """Read a bounded local inventory through the fixed no-mutation allowlist."""

    container_ids = _bounded_identifiers(
        _run_read_only(
            _docker_command("ps", "-a", "--no-trunc", "--format", "{{.ID}}"),
            label="Docker container",
            maximum_bytes=MAX_DOCKER_OUTPUT_BYTES,
            runner=runner,
        ),
        label="Docker container",
        pattern=DOCKER_ID_RE,
    )
    volume_names = _bounded_identifiers(
        _run_read_only(
            _docker_command("volume", "ls", "-q"),
            label="Docker volume",
            maximum_bytes=MAX_DOCKER_OUTPUT_BYTES,
            runner=runner,
        ),
        label="Docker volume",
        pattern=DOCKER_NAME_RE,
    )
    network_ids = _bounded_identifiers(
        _run_read_only(
            _docker_command("network", "ls", "-q"),
            label="Docker network",
            maximum_bytes=MAX_DOCKER_OUTPUT_BYTES,
            runner=runner,
        ),
        label="Docker network",
        pattern=DOCKER_ID_RE,
    )
    listeners = _listener_ports(
        _run_read_only(
            (SS_BINARY, "-H", "-ltn"),
            label="local TCP listener",
            maximum_bytes=MAX_SS_OUTPUT_BYTES,
            runner=runner,
        )
    )
    containers = tuple(
        _container_from_payload(item)
        for item in _inspect_objects(
            _docker_command("inspect"), container_ids, label="Docker container", runner=runner
        )
    )
    volumes = tuple(
        _volume_from_payload(item)
        for item in _inspect_objects(
            _docker_command("volume", "inspect"), volume_names, label="Docker volume", runner=runner
        )
    )
    networks = tuple(
        _network_from_payload(item)
        for item in _inspect_objects(
            _docker_command("network", "inspect"), network_ids, label="Docker network", runner=runner
        )
    )
    return HostInventory(
        containers=containers,
        volumes=volumes,
        networks=networks,
        listening_ports=listeners,
    )


def _overlaps_protected_root(value: str) -> bool:
    if not value or "\x00" in value or not os.path.isabs(value):
        return True
    normalized = os.path.normpath(value)
    try:
        resolved = os.path.realpath(normalized, strict=False)
        return any(
            os.path.commonpath((candidate, root)) == root
            for candidate in (normalized, resolved)
            for root in PROTECTED_HOST_ROOTS
        )
    except (OSError, ValueError):
        return True


def _planned_resources(profile: str) -> tuple[set[str], set[str], set[str], tuple[IpNetwork, ...]]:
    if profile not in {"telegram-only", "sms-otp"}:
        _fail("Emergency host-isolation profile is invalid")
    services = (*BASE_SERVICES, *(SMS_SERVICES if profile == "sms-otp" else ()))
    container_names = {
        f"{PROJECT_NAME}-{service}-1" for service in services
    } | {
        f"{PROJECT_NAME}_{service}_1" for service in services
    }
    volumes = set(BASE_VOLUMES)
    networks = set(BASE_NETWORKS)
    subnets = (*BASE_SUBNETS, *(SMS_SUBNETS if profile == "sms-otp" else ()))
    if profile == "sms-otp":
        networks.update(SMS_NETWORKS)
    return (
        container_names,
        volumes,
        networks,
        tuple(ipaddress.ip_network(item) for item in subnets),
    )


def evaluate_host_isolation(
    *,
    runtime_env: Path,
    compose: Path,
    profile: str,
    sms_compose: Path | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, object]:
    """Return a non-mutating collision report; it never writes a receipt."""

    _require_root()
    _require_runtime_contract(runtime_env)
    if profile == "sms-otp":
        if sms_compose is None:
            _fail("SMS host isolation requires the sealed SMS Compose configuration")
    elif sms_compose is not None:
        _fail("telegram-only host isolation refuses an SMS Compose input")
    _require_compose_contract(
        compose,
        profile=profile,
        sms_compose=sms_compose,
    )
    inventory = collect_host_inventory(runner=runner)
    expected_containers, expected_volumes, expected_networks, expected_subnets = _planned_resources(profile)
    collisions: set[str] = set()
    if any(container.project == PROJECT_NAME for container in inventory.containers):
        collisions.add("docker-compose-project")
    if any(volume.project == PROJECT_NAME for volume in inventory.volumes) or any(
        network.project == PROJECT_NAME for network in inventory.networks
    ):
        collisions.add("docker-compose-project")
    if any(container.name in expected_containers for container in inventory.containers):
        collisions.add("docker-container-name")
    if expected_volumes & {volume.name for volume in inventory.volumes} or any(
        expected_volumes & set(container.volume_names) for container in inventory.containers
    ):
        collisions.add("docker-volume-name")
    if expected_networks & {network.name for network in inventory.networks} or any(
        expected_networks & set(container.network_names) for container in inventory.containers
    ):
        collisions.add("docker-network-name")
    if any(
        expected.overlaps(existing)
        for expected in expected_subnets
        for network in inventory.networks
        for existing in network.subnets
        if expected.version == existing.version
    ):
        collisions.add("docker-network-subnet")
    if EMERGENCY_APP_PORT in inventory.listening_ports:
        collisions.add("host-listening-port")
    if any(EMERGENCY_APP_PORT in container.host_ports for container in inventory.containers):
        collisions.add("docker-host-port")
    if any(
        _overlaps_protected_root(source)
        for container in inventory.containers
        for source in container.bind_sources
    ):
        collisions.add("docker-bind-path")
    return {
        "schema": "gold-trade-emergency-ir-host-isolation-preflight-v1",
        "status": "ready" if not collisions else "blocked",
        "profile": profile,
        "collisions": sorted(collisions),
        "checked": {
            "compose_project": PROJECT_NAME,
            "planned_host_port": EMERGENCY_APP_PORT,
            "container_count": len(inventory.containers),
            "volume_count": len(inventory.volumes),
            "network_count": len(inventory.networks),
            "protected_host_roots": len(PROTECTED_HOST_ROOTS),
        },
        "read_only_actions": list(READ_ONLY_ACTIONS),
        "mutating_actions": list(MUTATING_ACTIONS),
        "docker_or_service_changed": False,
        "authorizes_activation": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-env", required=True, type=Path)
    parser.add_argument("--compose", required=True, type=Path)
    parser.add_argument("--profile", choices=("telegram-only", "sms-otp"), default="telegram-only")
    parser.add_argument("--sms-compose", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = evaluate_host_isolation(
            runtime_env=arguments.runtime_env,
            compose=arguments.compose,
            profile=arguments.profile,
            sms_compose=arguments.sms_compose,
        )
    except EmergencyHostIsolationError as exc:
        print(json.dumps({"status": "blocked", "error_class": type(exc).__name__, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
