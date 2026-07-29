from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys
import threading
import time
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from scripts import production_shadow_global_docker_inventory_agent as MODULE
from scripts import production_shadow_frozen_final_restore_worker as WORKER


CAMPAIGN_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2944"
OPERATION_ID = "8fb08095-7a9e-4a92-9fa9-3f9a301b2945"
RELEASE_SHA = "a" * 40
RELEASE_TREE_SHA = "b" * 40
GENERATION_SHA256 = "c" * 64
AGENT_SHA256 = "1" * 64
WORKER_SHA256 = "2" * 64
MANIFEST_SHA256 = "3" * 64
NON_OPERATION_CONTAINER_ID = "4" * 64
NON_OPERATION_NETWORK_ID = "5" * 64
OPERATION_CONTAINER_ID = "6" * 64
OPERATION_NETWORK_ID = "7" * 64
IMAGE_ID = f"sha256:{'8' * 64}"
ENDPOINT_ID = "9" * 64
COMPOSE_VERSION = "5.1.4"
DATABASE_CONFIG_HASH = "a" * 64
PREPARED_NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
PREPARED_CHALLENGE = "d" * 64
REDIS_IDENTITY_SHA256 = "e" * 64
REDIS_CHAIN_METADATA_SHA256 = "f" * 64
REDIS_METADATA_SHA256 = "c" * 64


def _state(*, running: bool = True) -> dict:
    return {
        "Status": "running" if running else "exited",
        "Running": running,
        "Paused": False,
        "Restarting": False,
        "OOMKilled": False,
        "Dead": False,
        "ExitCode": 0,
        "Health": {"Status": "healthy"},
    }


def _operation_cgroup_parent(request: dict) -> str:
    digest = hashlib.sha256(
        MODULE.canonical_json(
            {
                "operation_id": request["operation_id"],
                "restore_generation_sha256": request[
                    "restore_generation_sha256"
                ],
                "role": request["role"],
            }
        )
    ).hexdigest()
    return (
        "/trading-bot-production-shadow/frozen-final-"
        f"{digest[:32]}"
    )


def _config(identifier: str, image: str) -> dict:
    return {
        "Hostname": identifier[:12],
        "Domainname": "",
        "User": "",
        "AttachStdin": False,
        "AttachStdout": True,
        "AttachStderr": True,
        "ExposedPorts": {},
        "Tty": False,
        "OpenStdin": False,
        "StdinOnce": False,
        "Env": [],
        "Cmd": None,
        "Healthcheck": None,
        "ArgsEscaped": False,
        "Image": image,
        "Volumes": {},
        "WorkingDir": "",
        "Entrypoint": None,
        "NetworkDisabled": False,
        "OnBuild": None,
        "Labels": {},
        "StopSignal": "SIGTERM",
        "StopTimeout": 10,
        "Shell": None,
    }


def _host(network: str) -> dict:
    return {
        field: None for field in MODULE.HOST_CONFIG_FIELDS
    } | {
        "NetworkMode": network,
        "CgroupParent": "/legacy",
        "NanoCpus": 1_000_000_000,
        "Memory": 512 * 1024**2,
        "MemoryReservation": 0,
        "MemorySwap": 512 * 1024**2,
        "MemorySwappiness": None,
        "PidsLimit": 128,
        "CpuShares": 0,
        "CpuPeriod": 0,
        "CpuQuota": 0,
        "CpuRealtimePeriod": 0,
        "CpuRealtimeRuntime": 0,
        "CpusetCpus": "",
        "CpusetMems": "",
        "Privileged": False,
        "OomKillDisable": False,
        "Init": False,
        "ReadonlyRootfs": False,
        "AutoRemove": False,
        "PublishAllPorts": False,
        "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
        "Binds": [],
        "ContainerIDFile": "",
        "PortBindings": {},
        "CapAdd": None,
        "CapDrop": None,
        "SecurityOpt": None,
        "Devices": None,
        "DeviceRequests": None,
        "PidMode": "",
        "IpcMode": "private",
        "UTSMode": "",
        "UsernsMode": "",
        "Links": None,
        "ExtraHosts": None,
        "Dns": None,
        "DnsOptions": None,
        "DnsSearch": None,
        "GroupAdd": None,
        "Sysctls": {},
        "Tmpfs": {},
        "BlkioWeight": 0,
        "BlkioWeightDevice": [],
        "BlkioDeviceReadBps": [],
        "BlkioDeviceWriteBps": [],
        "BlkioDeviceReadIOps": [],
        "BlkioDeviceWriteIOps": [],
        "DeviceCgroupRules": None,
        "Ulimits": [],
        "CpuCount": 0,
        "CpuPercent": 0,
        "IOMaximumIOps": 0,
        "IOMaximumBandwidth": 0,
        "VolumeDriver": "",
        "VolumesFrom": None,
        "Mounts": None,
        "ConsoleSize": [0, 0],
        "Annotations": {},
        "CgroupnsMode": "private",
        "Cgroup": "",
        "OomScoreAdj": 0,
        "StorageOpt": {},
        "ShmSize": 64 * 1024 * 1024,
        "Runtime": "runc",
        "Isolation": "",
        "MaskedPaths": list(WORKER.BASE_MASKED_PATHS),
        "ReadonlyPaths": list(WORKER.READONLY_PATHS),
        "LogConfig": {
            "Type": "json-file",
            "Config": {"max-file": "5", "max-size": "20m"},
        },
    }


def _non_operation_container() -> dict:
    return {
        "Id": NON_OPERATION_CONTAINER_ID,
        "Name": "/legacy-api",
        "Image": IMAGE_ID,
        "RestartCount": 0,
        "Config": {
            **_config(NON_OPERATION_CONTAINER_ID, IMAGE_ID),
            "Image": IMAGE_ID,
            "Cmd": ["python", "app.py"],
            "Entrypoint": None,
            "Env": ["SECRET_VALUE=must-not-be-normalized"],
            "Labels": {"com.docker.compose.project": "legacy"},
            "User": "1000",
            "WorkingDir": "/app",
            "StopSignal": "SIGTERM",
        },
        "HostConfig": _host("legacy"),
        "State": _state(),
        "NetworkSettings": {
            "Networks": {
                "legacy": {
                    "NetworkID": NON_OPERATION_NETWORK_ID,
                    "EndpointID": ENDPOINT_ID,
                }
            }
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": "/srv/legacy/private",
                "Destination": "/app/data",
                "RW": True,
            }
        ],
    }


def _non_operation_network() -> dict:
    return {
        "Id": NON_OPERATION_NETWORK_ID,
        "Name": "legacy",
        "Driver": "bridge",
        "Scope": "local",
        "Internal": False,
        "Attachable": False,
        "Ingress": False,
        "ConfigOnly": False,
        "Labels": {"com.docker.compose.project": "legacy"},
        "Options": {},
        "IPAM": {
            "Driver": "default",
            "Options": {},
            "Config": [
                {
                    "Subnet": "172.31.240.0/24",
                    "Gateway": "172.31.240.1",
                }
            ],
        },
        "Containers": {
            NON_OPERATION_CONTAINER_ID: {"EndpointID": ENDPOINT_ID}
        },
    }


def _volume() -> dict:
    return {
        "Name": "legacy_data",
        "Driver": "local",
        "Scope": "local",
        "Labels": {"com.docker.compose.project": "legacy"},
        "Options": {},
        "Mountpoint": "/var/lib/docker/volumes/legacy_data/_data",
        "CreatedAt": "2026-07-28T00:00:00Z",
    }


def _image() -> dict:
    return {
        "Id": IMAGE_ID,
        "Parent": "",
        "RepoTags": ["legacy/app:current"],
        "RepoDigests": [f"legacy/app@{IMAGE_ID}"],
        "Created": "2026-07-27T00:00:00Z",
        "Size": 100,
        "VirtualSize": 100,
        "SharedSize": 0,
        "Architecture": "amd64",
        "Os": "linux",
        "Config": {
            "Env": ["IMAGE_SECRET=must-not-be-normalized"],
            "Cmd": ["postgres"],
            "Entrypoint": None,
            "ExposedPorts": {},
            "Volumes": {},
            "OnBuild": None,
            "Shell": None,
            "User": "70",
            "WorkingDir": "",
            "StopSignal": "SIGTERM",
            "Labels": {"org.opencontainers.image.title": "legacy"},
        },
        "RootFS": {"Type": "layers", "Layers": [f"sha256:{'f' * 64}"]},
    }


def _operation_container(request: dict, postgres: Path) -> dict:
    role = request["role"]
    project = request["project_name"]
    network = f"{project}_{role}"
    compose_path = Path("/fake/restore-compose.yml")
    environment_path = Path("/fake/runtime.env")
    host = _host(network)
    host.update(
        {
            "CgroupParent": _operation_cgroup_parent(request),
            "NanoCpus": 2_000_000_000,
            "Memory": 2 * 1024**3,
            "MemorySwap": 2 * 1024**3,
            "PidsLimit": 512,
            "Binds": [
                f"{postgres}:/var/lib/postgresql/data:rw"
            ],
        }
    )
    return {
        "Id": OPERATION_CONTAINER_ID,
        "Name": f"/{project}-{role}_db-1",
        "Image": IMAGE_ID,
        "RestartCount": 0,
        "Config": {
            **_config(OPERATION_CONTAINER_ID, IMAGE_ID),
            "Image": IMAGE_ID,
            "Cmd": ["postgres"],
            "Entrypoint": None,
            "Env": [
                "IMAGE_SECRET=must-not-be-normalized",
                "POSTGRES_PASSWORD=never-normalized",
            ],
            "Labels": {
                "org.opencontainers.image.title": "legacy",
                "com.docker.compose.config-hash": DATABASE_CONFIG_HASH,
                "com.docker.compose.container-number": "1",
                "com.docker.compose.depends_on": "",
                "com.docker.compose.image": IMAGE_ID,
                "com.docker.compose.oneoff": "False",
                "com.docker.compose.project": project,
                "com.docker.compose.project.config_files": str(
                    compose_path
                ),
                "com.docker.compose.project.environment_file": str(
                    environment_path
                ),
                "com.docker.compose.project.working_dir": str(
                    compose_path.parent
                ),
                "com.docker.compose.service": f"{role}_db",
                "com.docker.compose.version": COMPOSE_VERSION,
                "trading-bot.production.operation-id": OPERATION_ID,
            },
            "User": "70",
            "WorkingDir": "",
            "StopSignal": "SIGTERM",
        },
        "HostConfig": host,
        "State": _state(),
        "NetworkSettings": {
            "Networks": {
                network: {
                    "NetworkID": OPERATION_NETWORK_ID,
                    "EndpointID": "e" * 64,
                }
            }
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": str(postgres),
                "Destination": "/var/lib/postgresql/data",
                "RW": True,
            }
        ],
    }


def _operation_network(request: dict) -> dict:
    role = request["role"]
    project = request["project_name"]
    return {
        "Id": OPERATION_NETWORK_ID,
        "Name": f"{project}_{role}",
        "Driver": "bridge",
        "Scope": "local",
        "Internal": True,
        "Attachable": False,
        "Ingress": False,
        "ConfigOnly": False,
        "Labels": {
            "com.docker.compose.project": project,
            "com.docker.compose.network": role,
            "com.docker.compose.version": COMPOSE_VERSION,
            "trading-bot.production.operation-id": OPERATION_ID,
        },
        "Options": {},
        "IPAM": {
            "Driver": "default",
            "Options": {},
            "Config": [
                {
                    "Subnet": "172.31.241.0/24",
                    "Gateway": "172.31.241.1",
                }
            ],
        },
        "Containers": {
            OPERATION_CONTAINER_ID: {"EndpointID": "e" * 64}
        },
    }


def _manifest_path() -> Path:
    return (
        MODULE.SECRET_ROOT_PREFIX
        / OPERATION_ID
        / "frozen-final-generations"
        / GENERATION_SHA256
        / "bot-fi"
        / "restore-role-manifest.json"
    )


def _request(action: str) -> dict:
    after = action == "capture-after"
    arguments = {
        "action": action,
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": RELEASE_TREE_SHA,
        "restore_generation_sha256": GENERATION_SHA256,
        "role": "bot_fi",
        "agent_sha256": AGENT_SHA256,
        "worker_sha256": WORKER_SHA256,
        "expected_operation_container_id": (
            OPERATION_CONTAINER_ID if after else None
        ),
        "role_manifest_path": _manifest_path() if after else None,
        "role_manifest_sha256": MANIFEST_SHA256 if after else None,
    }
    if not after:
        return MODULE.build_request(**arguments)
    provisional = MODULE.build_request(
        **arguments,
        expected_operation_host_config_sha256="f" * 64,
    )
    postgres = (
        MODULE.PROJECT_ROOT_PREFIX
        / OPERATION_ID
        / "fake-postgres"
    )
    expected_host_config_sha256 = hashlib.sha256(
        MODULE.canonical_json(
            _operation_container(provisional, postgres)["HostConfig"]
        )
    ).hexdigest()
    return MODULE.build_request(
        **arguments,
        expected_operation_host_config_sha256=(
            expected_host_config_sha256
        ),
    )


def _snapshot(
    request: dict,
    *,
    after: bool,
    changed_non_operation: bool = False,
    extra_operation_volume: bool = False,
) -> dict:
    container = _non_operation_container()
    if changed_non_operation:
        container["RestartCount"] = 1
    containers = {NON_OPERATION_CONTAINER_ID: container}
    networks = {NON_OPERATION_NETWORK_ID: _non_operation_network()}
    volumes = {"legacy_data": _volume()}
    if after:
        postgres = (
            MODULE.PROJECT_ROOT_PREFIX
            / OPERATION_ID
            / "fake-postgres"
        )
        containers[OPERATION_CONTAINER_ID] = _operation_container(
            request,
            postgres,
        )
        networks[OPERATION_NETWORK_ID] = _operation_network(request)
    if extra_operation_volume:
        volumes[f"{request['project_name']}_extra"] = {
            **_volume(),
            "Name": f"{request['project_name']}_extra",
            "Labels": {
                "com.docker.compose.project": request["project_name"]
            },
        }
    return {
        "containers": containers,
        "networks": networks,
        "volumes": volumes,
        "images": {IMAGE_ID: _image()},
    }


def _prepared_request(
    *,
    challenge: str = PREPARED_CHALLENGE,
    issued_at: datetime = PREPARED_NOW,
    expires_at: datetime | None = None,
) -> dict:
    return MODULE.build_prepared_request(
        campaign_id=CAMPAIGN_ID,
        operation_id=OPERATION_ID,
        release_sha=RELEASE_SHA,
        release_tree_sha=RELEASE_TREE_SHA,
        role="bot_fi",
        agent_sha256=AGENT_SHA256,
        contract_worker_sha256=WORKER_SHA256,
        role_manifest_sha256=MANIFEST_SHA256,
        controller_challenge_sha256=challenge,
        issued_at=issued_at,
        expires_at=expires_at or issued_at + timedelta(seconds=60),
    )


def _prepared_snapshot(
    request: dict,
    *,
    foreign_restart_count: int = 0,
) -> dict:
    container = _non_operation_container()
    container["RestartCount"] = foreign_restart_count
    operation = _operation_container(
        {**request, "restore_generation_sha256": GENERATION_SHA256},
        MODULE.DATA_ROOT_PREFIX
        / OPERATION_ID
        / "bot-fi"
        / "postgres",
    )
    operation["HostConfig"]["CgroupParent"] = "/prepared"
    operation["Mounts"][0]["Propagation"] = "rprivate"
    operation["Config"]["Labels"][
        "trading-bot.production.operation-id"
    ] = OPERATION_ID
    return {
        "containers": {
            NON_OPERATION_CONTAINER_ID: container,
            OPERATION_CONTAINER_ID: operation,
        },
        "networks": {
            NON_OPERATION_NETWORK_ID: _non_operation_network(),
            OPERATION_NETWORK_ID: _operation_network(request),
        },
        "volumes": {"legacy_data": _volume()},
        "images": {IMAGE_ID: _image()},
    }


class FakeRunner:
    def __init__(
        self,
        snapshots: list[dict],
        *,
        duplicate_image: bool = False,
        compose_environment: object | None = None,
        compose_config_hash: str | None = None,
    ):
        self.snapshots = snapshots
        self.current = -1
        self.commands: list[tuple[str, ...]] = []
        self.duplicate_image = duplicate_image
        self.compose_environment = compose_environment
        self.compose_config_hash = compose_config_hash

    @property
    def snapshot(self) -> dict:
        return self.snapshots[self.current]

    def run(self, arguments, *, timeout=30):
        command = tuple(arguments)
        MODULE._validate_read_only_docker_command(command)
        self.commands.append(command)
        if "compose" in command and "config" in command:
            operation = self.snapshot["containers"][
                OPERATION_CONTAINER_ID
            ]
            labels = operation["Config"]["Labels"]
            service = labels["com.docker.compose.service"]
            if "--format" in command:
                return json.dumps(
                    {
                        "services": {
                            service: {
                                "environment": (
                                    operation["Config"]["Env"]
                                    if self.compose_environment is None
                                    else self.compose_environment
                                ),
                                "labels": {
                                    "trading-bot.production.operation-id": (
                                        OPERATION_ID
                                    )
                                },
                            }
                        }
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            if "--hash" in command:
                config_hash = (
                    labels["com.docker.compose.config-hash"]
                    if self.compose_config_hash is None
                    else self.compose_config_hash
                )
                return f"{service} {config_hash}"
            raise AssertionError("unexpected Compose config command")
        if command == (
            *MODULE.DOCKER_BASE,
            "ps",
            "--all",
            "--quiet",
            "--no-trunc",
        ):
            self.current += 1
            if self.current >= len(self.snapshots):
                raise AssertionError("unexpected third capture")
            return "\n".join(sorted(self.snapshot["containers"]))
        if command == (
            *MODULE.DOCKER_BASE,
            "network",
            "ls",
            "--quiet",
            "--no-trunc",
        ):
            return "\n".join(sorted(self.snapshot["networks"]))
        if command == (
            *MODULE.DOCKER_BASE,
            "volume",
            "ls",
            "--quiet",
        ):
            return "\n".join(sorted(self.snapshot["volumes"]))
        if command == (
            *MODULE.DOCKER_BASE,
            "image",
            "ls",
            "--all",
            "--quiet",
            "--no-trunc",
        ):
            values = sorted(self.snapshot["images"])
            if self.duplicate_image:
                values += values
            return "\n".join(values)
        prefixes = (
            ((*MODULE.DOCKER_BASE, "inspect"), "containers"),
            ((*MODULE.DOCKER_BASE, "network", "inspect"), "networks"),
            ((*MODULE.DOCKER_BASE, "volume", "inspect"), "volumes"),
            ((*MODULE.DOCKER_BASE, "image", "inspect"), "images"),
        )
        for prefix, kind in prefixes:
            if command[: len(prefix)] == prefix:
                rows = [
                    self.snapshot[kind][identity]
                    for identity in command[len(prefix) :]
                ]
                return json.dumps(rows, separators=(",", ":"), sort_keys=True)
        raise AssertionError(f"unexpected Docker command: {command!r}")


class FakeWorker:
    MAX_JSON_BYTES = 1024 * 1024
    DOCKER_API_VERSION = WORKER.DOCKER_API_VERSION
    HOST_CONFIG_FIELDS = WORKER.HOST_CONFIG_FIELDS
    OPTIONAL_HOST_CONFIG_FIELDS = WORKER.OPTIONAL_HOST_CONFIG_FIELDS
    CONTAINER_CONFIG_FIELDS = WORKER.CONTAINER_CONFIG_FIELDS
    _environment_map = staticmethod(WORKER._environment_map)
    _string_vector = staticmethod(WORKER._string_vector)
    _duration_seconds = staticmethod(WORKER._duration_seconds)
    _compose_healthcheck = staticmethod(WORKER._compose_healthcheck)
    _compose_dependencies_label = staticmethod(
        WORKER._compose_dependencies_label
    )
    _empty_object_map = staticmethod(WORKER._empty_object_map)
    _expected_compose_container_labels = staticmethod(
        WORKER._expected_compose_container_labels
    )
    _validate_exact_container_config = staticmethod(
        WORKER._validate_exact_container_config
    )
    _validate_exact_host_config = staticmethod(
        WORKER._validate_exact_host_config
    )

    def __init__(self, request: dict):
        self.request = request
        postgres = (
            MODULE.PROJECT_ROOT_PREFIX
            / OPERATION_ID
            / "fake-postgres"
        )
        self.paths = SimpleNamespace(
            project_base=request["project_base"],
            project_name=request["project_name"],
            release_root=Path(request["release_root"]),
            postgres=postgres,
        )
        self.manifest = SimpleNamespace(
            canonical_sha256=MANIFEST_SHA256,
            operation_id=OPERATION_ID,
            role="bot_fi",
            release_sha=RELEASE_SHA,
            release_tree_sha=RELEASE_TREE_SHA,
            restore_generation_sha256=GENERATION_SHA256,
            postgres_image_id=IMAGE_ID,
            paths=self.paths,
            role_compose_path=Path("/fake/restore-compose.yml"),
            environment_path=Path("/fake/runtime.env"),
            document={"environment_sha256": "f" * 64},
        )

    def runtime_paths(self, *_args):
        return self.paths

    def load_role_manifest(self, path):
        if path != _manifest_path():
            raise AssertionError("wrong manifest path")
        return self.manifest

    def _restore_compose_document(self, manifest):
        return {
            "services": {
                f"{manifest.role}_db": {
                    "command": ["postgres"],
                    "environment": {
                        "POSTGRES_PASSWORD": "never-normalized",
                    },
                    "cgroup_parent": (
                        "${PRODUCTION_SHADOW_CGROUP_PARENT:"
                        "?operation-bound cgroup is required}"
                    ),
                    "cpus": (
                        "${PRODUCTION_SHADOW_POSTGRES_CPU_LIMIT:-2.0}"
                    ),
                    "mem_limit": (
                        "${PRODUCTION_SHADOW_POSTGRES_MEMORY_LIMIT:-2g}"
                    ),
                    "pids_limit": (
                        "${PRODUCTION_SHADOW_POSTGRES_PIDS_LIMIT:-512}"
                    ),
                    "restart": "unless-stopped",
                    "labels": {
                        "trading-bot.production.operation-id": OPERATION_ID,
                    },
                    "logging": {
                        "driver": "json-file",
                        "options": {
                            "max-file": "5",
                            "max-size": "20m",
                        },
                    },
                }
            }
        }

    def _read_root_file(self, *_args, **_kwargs):
        return b""

    def parse_env_values(self, source):
        if source != "":
            raise AssertionError("unexpected fake role environment")
        return {}

    def _compose_environment(self, manifest):
        if manifest is not self.manifest:
            raise AssertionError("wrong manifest")
        return (
            {},
            {
                "PRODUCTION_SHADOW_CGROUP_PARENT": (
                    _operation_cgroup_parent(self.request)
                )
            },
        )

    def _nano_cpus(self, value, *, label):
        if label != "database CPU limit":
            raise AssertionError("wrong CPU label")
        return int(float(value) * 1_000_000_000)

    def _memory_bytes(self, value, *, label):
        if label != "database memory limit":
            raise AssertionError("wrong memory label")
        if value == "2g":
            return 2 * 1024**3
        raise AssertionError("unexpected memory value")


class StreamWrapper:
    def __init__(self, payload: bytes = b""):
        self.buffer = BytesIO(payload)


class GlobalDockerInventoryAgentTests(unittest.TestCase):
    def _execute(self, request: dict, runner: FakeRunner) -> dict:
        worker = FakeWorker(request)
        with mock.patch.object(
            MODULE,
            "_verify_execution_context",
            return_value=(worker, [request["expected_host"]]),
        ):
            return MODULE.execute_request(request, runner=runner)

    def _execute_prepared(
        self,
        request: dict,
        runner: FakeRunner,
        *,
        now: datetime | None = PREPARED_NOW,
        clock=None,
    ) -> dict:
        paths = SimpleNamespace(
            project_base=request["project_base"],
            project_name=request["project_name"],
            release_root=Path(request["release_root"]),
            data_root=MODULE.DATA_ROOT_PREFIX / OPERATION_ID,
            manifest=Path(request["role_manifest_path"]),
            environment=(
                MODULE.SECRET_ROOT_PREFIX
                / OPERATION_ID
                / "bot-fi"
                / "runtime.env.role"
            ),
            compose=(
                MODULE.PROJECT_ROOT_PREFIX
                / OPERATION_ID
                / "rendered"
                / "bot-fi"
                / "docker-compose.yml"
            ),
        )
        manifest = SimpleNamespace(
            canonical_sha256=MANIFEST_SHA256,
            operation_id=OPERATION_ID,
            role="bot_fi",
            release_sha=RELEASE_SHA,
            release_tree_sha=RELEASE_TREE_SHA,
            worker_sha256=WORKER_SHA256,
            runtime_image_ids={"postgres": IMAGE_ID},
        )
        worker = SimpleNamespace(
            ROLE_SERVICES={
                "bot_fi": {
                    "database": "bot_fi_db",
                    "network": "bot_fi",
                }
            }
        )
        with mock.patch.object(
            MODULE,
            "_verify_prepared_execution_context",
            return_value=(
                worker,
                manifest,
                paths,
                [request["expected_host"]],
            ),
        ), mock.patch.object(
            MODULE,
            "_attest_prepared_redis_target",
            return_value={
                "identity_sha256": REDIS_IDENTITY_SHA256,
                "chain_metadata_sha256": REDIS_CHAIN_METADATA_SHA256,
                "metadata_sha256": REDIS_METADATA_SHA256,
                "target_count": 1,
                "unsafe_path_count": 0,
                "entry_count": 0,
                "pristine": True,
            },
        ):
            return MODULE.execute_prepared_request(
                request,
                runner=runner,
                now=now,
                clock=clock,
            )

    def _assert_foreign_host_config_drift(
        self,
        field: str,
        replacement: object,
    ) -> None:
        before_request = _request("capture-before")
        before_snapshot = _snapshot(before_request, after=False)
        before = self._execute(
            before_request,
            FakeRunner([before_snapshot, copy.deepcopy(before_snapshot)]),
        )
        after_request = _request("capture-after")
        after_snapshot = _snapshot(after_request, after=True)
        after_snapshot["containers"][NON_OPERATION_CONTAINER_ID][
            "HostConfig"
        ][field] = replacement
        after = self._execute(
            after_request,
            FakeRunner([after_snapshot, copy.deepcopy(after_snapshot)]),
        )
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "non-operation Docker inventory changed",
        ):
            MODULE.compare_non_operation_inventories(
                before,
                after,
                before_request=before_request,
                after_request=after_request,
            )

    def test_request_round_trip_binds_release_host_paths_and_project(self):
        before = _request("capture-before")
        after = _request("capture-after")
        self.assertEqual(before, MODULE.validate_request(before))
        self.assertEqual(after, MODULE.validate_request(after))
        self.assertEqual(before["expected_host"], "65.109.216.187")
        self.assertTrue(before["agent_path"].endswith(str(MODULE.AGENT_RELATIVE)))
        self.assertTrue(
            before["worker_path"].endswith(str(MODULE.WORKER_RELATIVE))
        )
        self.assertTrue(before["project_name"].endswith("-bot-fi"))
        self.assertIsNone(before["expected_operation_container_id"])
        self.assertEqual(
            after["expected_operation_container_id"],
            OPERATION_CONTAINER_ID,
        )

    def test_request_rejects_tamper_duplicate_json_and_noncanonical_path(self):
        request = _request("capture-before")
        tampered = {**request, "expected_host": "127.0.0.1"}
        with self.assertRaises(MODULE.GlobalDockerInventoryError):
            MODULE.validate_request(tampered)
        tampered = {**request, "release_root": "/srv/../srv/wrong"}
        tampered["request_binding_sha256"] = MODULE._request_binding(tampered)
        with self.assertRaises(MODULE.GlobalDockerInventoryError):
            MODULE.validate_request(tampered)
        with self.assertRaises(MODULE.GlobalDockerInventoryError):
            MODULE.strict_json(b'{"a":1,"a":2}', label="duplicate")
        with self.assertRaises(MODULE.GlobalDockerInventoryError):
            MODULE._safe_string("\ud800", label="surrogate")

    def test_request_rejects_action_specific_field_cross_contamination(self):
        before = _request("capture-before")
        before["expected_operation_container_id"] = OPERATION_CONTAINER_ID
        before["request_binding_sha256"] = MODULE._request_binding(before)
        with self.assertRaises(MODULE.GlobalDockerInventoryError):
            MODULE.validate_request(before)
        after = _request("capture-after")
        after["role_manifest_sha256"] = None
        after["request_binding_sha256"] = MODULE._request_binding(after)
        with self.assertRaises(MODULE.GlobalDockerInventoryError):
            MODULE.validate_request(after)
        after = _request("capture-after")
        after["expected_operation_host_config_sha256"] = None
        after["request_binding_sha256"] = MODULE._request_binding(after)
        with self.assertRaises(MODULE.GlobalDockerInventoryError):
            MODULE.validate_request(after)

    def test_prepared_request_and_response_bind_fresh_exact_clone(self):
        request = _prepared_request()
        snapshot = _prepared_snapshot(request)
        response = self._execute_prepared(
            request,
            FakeRunner([snapshot, copy.deepcopy(snapshot)]),
        )
        self.assertEqual(
            request,
            MODULE.validate_prepared_request(
                request,
                now=PREPARED_NOW,
            ),
        )
        self.assertEqual(
            response,
            MODULE.validate_prepared_response(
                response,
                request=request,
                now=PREPARED_NOW,
            ),
        )
        self.assertEqual(
            response["operation_resource_counts"],
            {"container": 1, "network": 1, "volume": 0, "image": 0},
        )
        self.assertEqual(
            response["prepared_container_id"],
            OPERATION_CONTAINER_ID,
        )
        self.assertEqual(
            response["prepared_network_id"],
            OPERATION_NETWORK_ID,
        )
        self.assertTrue(response["prepared_database_running"])
        self.assertTrue(response["prepared_database_healthy"])
        self.assertEqual(response["prepared_redis_target_count"], 1)
        self.assertEqual(response["prepared_redis_unsafe_path_count"], 0)
        self.assertEqual(response["prepared_redis_entry_count"], 0)
        self.assertTrue(response["prepared_redis_pristine"])
        self.assertFalse(response["environment_values_returned"])
        self.assertFalse(response["path_descriptors_returned"])
        encoded = MODULE.canonical_json(response)
        self.assertNotIn(b"never-normalized", encoded)
        self.assertNotIn(b"postgres", encoded)
        self.assertNotIn(b"/srv/", encoded)

    def test_prepared_old_response_replay_and_touched_copy_are_rejected(self):
        first_request = _prepared_request(challenge="a" * 64)
        snapshot = _prepared_snapshot(first_request)
        first_response = self._execute_prepared(
            first_request,
            FakeRunner([snapshot, copy.deepcopy(snapshot)]),
        )
        second_request = _prepared_request(challenge="b" * 64)
        copied_and_touched = json.loads(
            MODULE.canonical_json(first_response).decode("ascii")
        )
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "identity or safety boundary differs",
        ):
            MODULE.validate_prepared_response(
                copied_and_touched,
                request=second_request,
                now=PREPARED_NOW,
            )

    def test_prepared_challenge_substitution_and_hash_tamper_are_rejected(self):
        request = _prepared_request()
        substituted = {
            **request,
            "controller_challenge_sha256": "e" * 64,
        }
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "request binding",
        ):
            MODULE.validate_prepared_request(
                substituted,
                now=PREPARED_NOW,
            )
        snapshot = _prepared_snapshot(request)
        response = self._execute_prepared(
            request,
            FakeRunner([snapshot, copy.deepcopy(snapshot)]),
        )
        response["prepared_host_config_sha256"] = "f" * 64
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "response SHA-256",
        ):
            MODULE.validate_prepared_response(
                response,
                request=request,
                now=PREPARED_NOW,
            )

    def test_prepared_request_rejects_expiry_future_and_wide_window(self):
        expired = _prepared_request(
            issued_at=PREPARED_NOW - timedelta(seconds=70),
            expires_at=PREPARED_NOW - timedelta(seconds=10),
        )
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "stale or outside",
        ):
            MODULE.validate_prepared_request(expired, now=PREPARED_NOW)
        future = _prepared_request(
            issued_at=PREPARED_NOW + timedelta(seconds=6),
            expires_at=PREPARED_NOW + timedelta(seconds=66),
        )
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "stale or outside",
        ):
            MODULE.validate_prepared_request(
                future,
                now=PREPARED_NOW,
            )
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "stale or outside",
        ):
            _prepared_request(
                issued_at=PREPARED_NOW,
                expires_at=PREPARED_NOW + timedelta(seconds=121),
            )

    def test_prepared_response_rejects_future_capture_and_expired_readback(self):
        request = _prepared_request()
        snapshot = _prepared_snapshot(request)
        response = self._execute_prepared(
            request,
            FakeRunner([snapshot, copy.deepcopy(snapshot)]),
        )
        future = dict(response)
        future["captured_at"] = MODULE.canonical_utc_timestamp(
            PREPARED_NOW + timedelta(seconds=6)
        )
        unsigned = {
            key: value
            for key, value in future.items()
            if key != "response_sha256"
        }
        future["response_sha256"] = MODULE._sha256(
            MODULE.canonical_json(unsigned)
        )
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "stale or from the future",
        ):
            MODULE.validate_prepared_response(
                future,
                request=request,
                now=PREPARED_NOW,
            )
        with self.assertRaises(
            MODULE.GlobalDockerInventoryError
        ):
            MODULE.validate_prepared_response(
                response,
                request=request,
                now=PREPARED_NOW + timedelta(seconds=61),
            )

    def test_prepared_rejects_wrong_project_clone_and_foreign_delta(self):
        request = _prepared_request()
        wrong = _prepared_snapshot(request)
        operation = wrong["containers"][OPERATION_CONTAINER_ID]
        operation["Name"] = "/foreign-bot_fi_db-1"
        operation["Config"]["Labels"]["com.docker.compose.project"] = (
            "foreign"
        )
        wrong["networks"][OPERATION_NETWORK_ID]["Name"] = "foreign_bot_fi"
        wrong["networks"][OPERATION_NETWORK_ID]["Labels"][
            "com.docker.compose.project"
        ] = "foreign"
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "container contract differs",
        ):
            self._execute_prepared(
                request,
                FakeRunner([wrong, copy.deepcopy(wrong)]),
            )

        first = _prepared_snapshot(request)
        second = _prepared_snapshot(request, foreign_restart_count=1)
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "stable consecutive roots",
        ):
            self._execute_prepared(
                request,
                FakeRunner([first, second]),
            )

    def test_prepared_rejects_wrong_mount_network_and_unhealthy_state(self):
        request = _prepared_request()
        mutations = []
        wrong_mount = _prepared_snapshot(request)
        wrong_mount["containers"][OPERATION_CONTAINER_ID]["Mounts"][0][
            "Source"
        ] = "/foreign"
        mutations.append(wrong_mount)
        foreign_endpoint = _prepared_snapshot(request)
        foreign_endpoint["networks"][OPERATION_NETWORK_ID]["Containers"][
            "a" * 64
        ] = {"EndpointID": "b" * 64}
        mutations.append(foreign_endpoint)
        unhealthy = _prepared_snapshot(request)
        unhealthy["containers"][OPERATION_CONTAINER_ID]["State"][
            "Health"
        ] = {"Status": "unhealthy"}
        mutations.append(unhealthy)
        for snapshot in mutations:
            with self.subTest(snapshot=snapshot):
                with self.assertRaises(
                    MODULE.GlobalDockerInventoryError
                ):
                    self._execute_prepared(
                        request,
                        FakeRunner(
                            [snapshot, copy.deepcopy(snapshot)]
                        ),
                    )

    def test_prepared_rejects_environment_and_compose_hash_mismatch(self):
        request = _prepared_request()
        snapshot = _prepared_snapshot(request)
        wrong_environment = FakeRunner(
            [snapshot, copy.deepcopy(snapshot)],
            compose_environment=[
                "IMAGE_SECRET=must-not-be-normalized",
                "POSTGRES_PASSWORD=different",
            ],
        )
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "container contract differs",
        ):
            self._execute_prepared(request, wrong_environment)

        snapshot = _prepared_snapshot(request)
        wrong_hash = FakeRunner(
            [snapshot, copy.deepcopy(snapshot)],
            compose_config_hash="f" * 64,
        )
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "container contract differs",
        ):
            self._execute_prepared(request, wrong_hash)

    def test_prepared_rejects_noncanonical_ipam_and_network_options(self):
        request = _prepared_request()
        null_options = _prepared_snapshot(request)
        null_options["networks"][OPERATION_NETWORK_ID]["Options"] = None
        null_options["networks"][OPERATION_NETWORK_ID]["IPAM"][
            "Options"
        ] = None
        response = self._execute_prepared(
            request,
            FakeRunner(
                [null_options, copy.deepcopy(null_options)]
            ),
        )
        self.assertEqual(
            response["prepared_network_id"],
            OPERATION_NETWORK_ID,
        )

        mutations = []
        wrong_options = _prepared_snapshot(request)
        wrong_options["networks"][OPERATION_NETWORK_ID]["Options"] = {
            "foreign": "value"
        }
        mutations.append(wrong_options)
        noncanonical_subnet = _prepared_snapshot(request)
        noncanonical_subnet["networks"][OPERATION_NETWORK_ID]["IPAM"][
            "Config"
        ][0]["Subnet"] = "172.31.250.4/24"
        mutations.append(noncanonical_subnet)
        extra_ipam = _prepared_snapshot(request)
        extra_ipam["networks"][OPERATION_NETWORK_ID]["IPAM"]["Extra"] = {}
        mutations.append(extra_ipam)
        for snapshot in mutations:
            with self.subTest(snapshot=snapshot):
                with self.assertRaises(MODULE.GlobalDockerInventoryError):
                    self._execute_prepared(
                        request,
                        FakeRunner(
                            [snapshot, copy.deepcopy(snapshot)]
                        ),
                    )

    def test_prepared_capture_time_is_sampled_after_second_capture(self):
        request = _prepared_request()
        snapshot = _prepared_snapshot(request)
        runner = FakeRunner([snapshot, copy.deepcopy(snapshot)])
        calls = []

        def clock():
            calls.append(runner.current)
            return PREPARED_NOW + timedelta(seconds=len(calls) - 1)

        response = self._execute_prepared(
            request,
            runner,
            now=None,
            clock=clock,
        )
        self.assertEqual(calls, [-1, 1])
        self.assertEqual(
            response["captured_at"],
            MODULE.canonical_utc_timestamp(
                PREPARED_NOW + timedelta(seconds=1)
            ),
        )

    def test_stopped_request_binds_running_baseline_and_exact_clone(self):
        running_request = _prepared_request()
        running_snapshot = _prepared_snapshot(running_request)
        running_response = self._execute_prepared(
            running_request,
            FakeRunner(
                [running_snapshot, copy.deepcopy(running_snapshot)]
            ),
        )
        stopped_now = PREPARED_NOW + timedelta(minutes=5)
        stopped_request = MODULE.build_stopped_request_from_prepared_response(
            prepared_request=running_request,
            prepared_response=running_response,
            controller_challenge_sha256="e" * 64,
            issued_at=stopped_now,
            expires_at=stopped_now + timedelta(seconds=60),
        )
        stopped_snapshot = _prepared_snapshot(stopped_request)
        stopped_snapshot["containers"][OPERATION_CONTAINER_ID]["State"] = (
            _state(running=False)
        )
        stopped_response = self._execute_prepared(
            stopped_request,
            FakeRunner(
                [stopped_snapshot, copy.deepcopy(stopped_snapshot)]
            ),
            now=stopped_now,
        )
        self.assertEqual(
            stopped_request["baseline_response_sha256"],
            running_response["response_sha256"],
        )
        self.assertEqual(
            stopped_response["prepared_container_id"],
            running_response["prepared_container_id"],
        )
        self.assertEqual(
            stopped_response["prepared_network_id"],
            running_response["prepared_network_id"],
        )
        self.assertFalse(stopped_response["prepared_database_running"])
        self.assertFalse(stopped_response["prepared_database_healthy"])
        self.assertEqual(
            stopped_response["prepared_redis_identity_sha256"],
            running_response["prepared_redis_identity_sha256"],
        )
        self.assertEqual(
            stopped_response["prepared_redis_metadata_sha256"],
            running_response["prepared_redis_metadata_sha256"],
        )
        self.assertEqual(
            stopped_response["prepared_redis_chain_metadata_sha256"],
            running_response["prepared_redis_chain_metadata_sha256"],
        )

    def test_prepared_redis_directory_attestation_is_empty_and_redacted(self):
        request = _prepared_request()
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "redis"
            target.mkdir(mode=0o700)
            result = MODULE._attest_pristine_redis_directory(
                target,
                request=request,
                operation_root=Path(raw),
            )
        self.assertEqual(
            set(result),
            {
                "identity_sha256",
                "chain_metadata_sha256",
                "metadata_sha256",
                "target_count",
                "unsafe_path_count",
                "entry_count",
                "pristine",
            },
        )
        self.assertEqual(result["target_count"], 1)
        self.assertEqual(result["unsafe_path_count"], 0)
        self.assertEqual(result["entry_count"], 0)
        self.assertTrue(result["pristine"])
        self.assertNotIn(
            str(target).encode("utf-8"),
            MODULE.canonical_json(result),
        )

    def test_prepared_redis_attestation_rejects_symlink_mode_and_hardlink(self):
        request = _prepared_request()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            real = root / "real"
            real.mkdir(mode=0o700)
            link = root / "redis-link"
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaises(MODULE.GlobalDockerInventoryError):
                MODULE._attest_pristine_redis_directory(
                    link,
                    request=request,
                    operation_root=root,
                )

            wrong_mode = root / "wrong-mode"
            wrong_mode.mkdir(mode=0o755)
            with self.assertRaisesRegex(
                MODULE.GlobalDockerInventoryError,
                "chain is unsafe",
            ):
                MODULE._attest_pristine_redis_directory(
                    wrong_mode,
                    request=request,
                    operation_root=root,
                )

            source = root / "source"
            source.write_bytes(b"x")
            hardlink_target = root / "hardlink-target"
            hardlink_target.mkdir(mode=0o700)
            os.link(source, hardlink_target / "linked")
            with self.assertRaisesRegex(
                MODULE.GlobalDockerInventoryError,
                "not pristine-empty",
            ):
                MODULE._attest_pristine_redis_directory(
                    hardlink_target,
                    request=request,
                    operation_root=root,
                )

    def test_prepared_redis_attestation_rejects_path_substitution_race(self):
        request = _prepared_request()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "redis"
            displaced = root / "redis-displaced"
            target.mkdir(mode=0o700)
            real_scandir = os.scandir
            calls = 0

            def substitute(path):
                nonlocal calls
                calls += 1
                if calls == 1:
                    target.rename(displaced)
                    target.mkdir(mode=0o700)
                return real_scandir(path)

            with (
                mock.patch.object(MODULE.os, "scandir", side_effect=substitute),
                self.assertRaisesRegex(
                    MODULE.GlobalDockerInventoryError,
                    "identity changed",
                ),
            ):
                MODULE._attest_pristine_redis_directory(
                    target,
                    request=request,
                    operation_root=root,
                )

    def test_prepared_redis_attestation_rejects_chain_metadata_race(self):
        request = _prepared_request()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "redis"
            target.mkdir(mode=0o700)
            real_scandir = os.scandir
            calls = 0

            def mutate(path):
                nonlocal calls
                calls += 1
                if calls == 1:
                    target.chmod(0o711)
                return real_scandir(path)

            with (
                mock.patch.object(MODULE.os, "scandir", side_effect=mutate),
                self.assertRaisesRegex(
                    MODULE.GlobalDockerInventoryError,
                    "identity changed",
                ),
            ):
                MODULE._attest_pristine_redis_directory(
                    target,
                    request=request,
                    operation_root=root,
                )

    def test_prepared_redis_primary_error_survives_close_failure(self):
        request = _prepared_request()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "redis"
            target.mkdir(mode=0o700)
            (target / "residue").write_bytes(b"x")
            real_close = os.close
            failed = False

            def fail_once(descriptor):
                nonlocal failed
                real_close(descriptor)
                if not failed:
                    failed = True
                    raise OSError("injected close failure")

            with (
                mock.patch.object(MODULE.os, "close", side_effect=fail_once),
                self.assertRaisesRegex(
                    MODULE.GlobalDockerInventoryError,
                    "not pristine-empty",
                ) as raised,
            ):
                MODULE._attest_pristine_redis_directory(
                    target,
                    request=request,
                    operation_root=root,
                )
        self.assertTrue(
            any(
                "descriptors could not be closed" in note
                for note in getattr(raised.exception, "__notes__", ())
            )
        )

    def test_prepared_redis_rejects_writable_root_metadata(self):
        request = _prepared_request()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "redis"
            target.mkdir(mode=0o700)
            real_fstat = os.fstat
            calls = 0

            def unsafe_root(descriptor):
                nonlocal calls
                metadata = real_fstat(descriptor)
                calls += 1
                if calls != 1:
                    return metadata
                values = list(metadata)
                values[0] |= stat.S_IWGRP
                return os.stat_result(values)

            with (
                mock.patch.object(
                    MODULE.os,
                    "fstat",
                    side_effect=unsafe_root,
                ),
                self.assertRaisesRegex(
                    MODULE.GlobalDockerInventoryError,
                    "directory root is unsafe",
                ),
            ):
                MODULE._attest_pristine_redis_directory(
                    target,
                    request=request,
                    operation_root=root,
                )

    def test_prepared_redis_cleanup_preserves_baseexception(self):
        request = _prepared_request()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "redis"
            target.mkdir(mode=0o700)
            real_close = os.close
            failed = False

            def interrupt_once(descriptor):
                nonlocal failed
                real_close(descriptor)
                if not failed:
                    failed = True
                    raise KeyboardInterrupt()

            with (
                mock.patch.object(
                    MODULE.os,
                    "close",
                    side_effect=interrupt_once,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                MODULE._attest_pristine_redis_directory(
                    target,
                    request=request,
                    operation_root=root,
                )

    def test_prepared_rejects_naive_controller_clock(self):
        request = _prepared_request()
        snapshot = _prepared_snapshot(request)
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "clock is invalid",
        ):
            self._execute_prepared(
                request,
                FakeRunner([snapshot, copy.deepcopy(snapshot)]),
                now=None,
                clock=lambda: datetime(2026, 7, 28, 12, 0),
            )

    def test_aggregate_capture_budget_is_shared_across_commands(self):
        runner = mock.Mock()
        runner.run.side_effect = ["123456", "abcdef"]
        budget = MODULE.CaptureBudget(runner=runner, started_at=0.0)
        with (
            mock.patch.object(
                MODULE,
                "MAX_CAPTURE_OUTPUT_BYTES",
                10,
            ),
            mock.patch.object(
                MODULE.time,
                "monotonic",
                side_effect=(0.0, 0.0, 0.0, 0.0),
            ),
        ):
            self.assertEqual(budget.run(["first"]), "123456")
            with self.assertRaisesRegex(
                MODULE.GlobalDockerInventoryError,
                "byte budget exhausted",
            ):
                budget.run(["second"])
        self.assertEqual(budget.bytes_consumed, 12)

    def test_aggregate_capture_duration_budget_is_monotonic(self):
        runner = mock.Mock()
        runner.run.return_value = ""
        budget = MODULE.CaptureBudget(runner=runner, started_at=10.0)
        with (
            mock.patch.object(
                MODULE,
                "MAX_CAPTURE_DURATION_SECONDS",
                5.0,
            ),
            mock.patch.object(
                MODULE.time,
                "monotonic",
                side_effect=(10.0, 16.0),
            ),
        ):
            with self.assertRaisesRegex(
                MODULE.GlobalDockerInventoryError,
                "duration budget exhausted",
            ):
                budget.run(["capture"])
        runner.run.assert_called_once()

    def test_aggregate_capture_budget_never_rounds_subsecond_remainder_up(self):
        runner = mock.Mock()
        runner.run.return_value = ""
        budget = MODULE.CaptureBudget(runner=runner, started_at=10.0)
        with (
            mock.patch.object(
                MODULE,
                "MAX_CAPTURE_DURATION_SECONDS",
                5.0,
            ),
            mock.patch.object(
                MODULE.time,
                "monotonic",
                side_effect=(14.75, 14.75),
            ),
        ):
            self.assertEqual(budget.run(["capture"], timeout=30), "")
        runner.run.assert_called_once_with(["capture"], timeout=0.25)

    def test_read_only_command_allowlist_has_no_shell_or_mutation_escape(self):
        self.assertEqual(
            MODULE.DOCKER_BASE,
            (
                "/usr/bin/docker",
                "--host=unix:///run/docker.sock",
            ),
        )
        MODULE._validate_read_only_docker_command(
            [*MODULE.DOCKER_BASE, "volume", "inspect", "run"]
        )
        for command in (
            [*MODULE.DOCKER_BASE, "run", "image"],
            [
                *MODULE.DOCKER_BASE,
                "inspect",
                "--format",
                "{{json .}}",
            ],
            ["/bin/sh", "-c", "docker ps"],
            [MODULE.DOCKER, "ps", "--all", "--quiet", "--no-trunc"],
            [*MODULE.DOCKER_BASE, "compose", "ps"],
            [*MODULE.DOCKER_BASE, "image", "inspect"],
        ):
            with self.subTest(command=command):
                with self.assertRaises(MODULE.GlobalDockerInventoryError):
                    MODULE._validate_read_only_docker_command(command)

    def test_docker_socket_must_remain_root_owned_socket_without_world_write(
        self,
    ):
        safe = SimpleNamespace(
            st_dev=1,
            st_ino=2,
            st_uid=0,
            st_gid=999,
            st_mode=stat.S_IFSOCK | 0o660,
        )
        unsafe = SimpleNamespace(**{**vars(safe), "st_mode": stat.S_IFSOCK | 0o662})
        replacement = SimpleNamespace(**{**vars(safe), "st_ino": 3})
        with mock.patch.object(MODULE.os, "lstat", return_value=safe):
            identity = MODULE._docker_socket_identity()
            MODULE._assert_docker_socket_identity(identity)
        with mock.patch.object(MODULE.os, "lstat", return_value=unsafe):
            with self.assertRaisesRegex(
                MODULE.GlobalDockerInventoryError,
                "unsafe",
            ):
                MODULE._docker_socket_identity()
        with mock.patch.object(MODULE.os, "lstat", return_value=replacement):
            with self.assertRaisesRegex(
                MODULE.GlobalDockerInventoryError,
                "changed",
            ):
                MODULE._assert_docker_socket_identity(identity)
        with (
            mock.patch.object(
                MODULE.os,
                "lstat",
                side_effect=(safe, safe, replacement),
            ),
            mock.patch.object(
                MODULE,
                "_bounded_command",
                return_value=MODULE.BoundedCommandResult(0, b"", b""),
            ),
        ):
            runner = MODULE.SubprocessDockerRunner()
            with self.assertRaisesRegex(
                MODULE.GlobalDockerInventoryError,
                "changed",
            ):
                runner.run(
                    [
                        *MODULE.DOCKER_BASE,
                        "ps",
                        "--all",
                        "--quiet",
                        "--no-trunc",
                    ]
                )

    def test_subprocess_runner_preserves_and_charges_raw_output_bytes(self):
        safe = SimpleNamespace(
            st_dev=1,
            st_ino=2,
            st_uid=0,
            st_gid=999,
            st_mode=stat.S_IFSOCK | 0o660,
        )
        command = [
            *MODULE.DOCKER_BASE,
            "ps",
            "--all",
            "--quiet",
            "--no-trunc",
        ]
        with (
            mock.patch.object(MODULE.os, "lstat", return_value=safe),
            mock.patch.object(
                MODULE,
                "_bounded_command",
                return_value=MODULE.BoundedCommandResult(
                    0,
                    b" \n",
                    b"",
                ),
            ),
        ):
            runner = MODULE.SubprocessDockerRunner()
            self.assertEqual(runner.run(command), " \n")
            budget = MODULE.CaptureBudget(runner=runner, started_at=0.0)
            with (
                mock.patch.object(
                    MODULE,
                    "MAX_CAPTURE_OUTPUT_BYTES",
                    1,
                ),
                mock.patch.object(
                    MODULE.time,
                    "monotonic",
                    return_value=0.0,
                ),
                self.assertRaisesRegex(
                    MODULE.GlobalDockerInventoryError,
                    "byte budget exhausted",
                ),
            ):
                budget.run(command)
            self.assertEqual(budget.bytes_consumed, 2)

    def test_subprocess_runner_rejects_successful_stderr(self):
        safe = SimpleNamespace(
            st_dev=1,
            st_ino=2,
            st_uid=0,
            st_gid=999,
            st_mode=stat.S_IFSOCK | 0o660,
        )
        with (
            mock.patch.object(MODULE.os, "lstat", return_value=safe),
            mock.patch.object(
                MODULE,
                "_bounded_command",
                return_value=MODULE.BoundedCommandResult(
                    0,
                    b"",
                    b"warning\n",
                ),
            ),
        ):
            runner = MODULE.SubprocessDockerRunner()
            with self.assertRaisesRegex(
                MODULE.GlobalDockerInventoryError,
                "failed closed",
            ):
                runner.run(
                    [
                        *MODULE.DOCKER_BASE,
                        "ps",
                        "--all",
                        "--quiet",
                        "--no-trunc",
                    ]
                )

    def test_bounded_command_stops_oversized_output_while_running(self):
        with self.assertRaisesRegex(
            MODULE.BoundedCommandError,
            "stdout exceeded",
        ):
            MODULE._bounded_command(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-c",
                    "import os; os.write(1, b'x' * 131072)",
                ],
                timeout=10,
                env={"PATH": "/usr/bin:/bin"},
                stdout_limit=1024,
                stderr_limit=1024,
            )

    def test_timeout_reaps_setsided_double_fork_without_proc_residue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            descendant_pid = root / "timeout-descendant-pid"
            sentinel = root / "timeout-descendant-survived"
            program = (
                "import os,signal,time\n"
                "if os.fork() == 0:\n"
                " os.setsid()\n"
                " if os.fork() != 0: time.sleep(60);os._exit(0)\n"
                " signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                f" open({str(descendant_pid)!r},'w').write(str(os.getpid()))\n"
                " time.sleep(0.7)\n"
                f" open({str(sentinel)!r},'wb').write(b'survived')\n"
                " os._exit(0)\n"
                f"while not os.path.exists({str(descendant_pid)!r}):"
                " time.sleep(0.005)\n"
                "time.sleep(60)\n"
            )
            with (
                mock.patch.object(
                    MODULE,
                    "PROCESS_TREE_TERM_SECONDS",
                    0.1,
                ),
                mock.patch.object(
                    MODULE,
                    "PROCESS_TREE_QUIESCENCE_SECONDS",
                    0.05,
                ),
                self.assertRaisesRegex(
                    MODULE.BoundedCommandError,
                    "timed out",
                ),
            ):
                MODULE._bounded_command(
                    [sys.executable, "-I", "-B", "-c", program],
                    timeout=0.15,
                    env={"PATH": "/usr/bin:/bin"},
                    stdout_limit=1024,
                    stderr_limit=1024,
                )
            time.sleep(0.8)
            self.assertFalse(sentinel.exists())
            self.assertTrue(descendant_pid.is_file())
            self.assertFalse(
                Path(
                    f"/proc/{descendant_pid.read_text(encoding='ascii')}"
                ).exists()
            )

    def test_timeout_after_both_output_streams_close_is_classified(self):
        with (
            mock.patch.object(
                MODULE,
                "PROCESS_TREE_TERM_SECONDS",
                0.1,
            ),
            mock.patch.object(
                MODULE,
                "PROCESS_TREE_QUIESCENCE_SECONDS",
                0.05,
            ),
            self.assertRaisesRegex(
                MODULE.BoundedCommandError,
                "timed out",
            ),
        ):
            MODULE._bounded_command(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    "import os,time;os.close(1);os.close(2);time.sleep(60)",
                ],
                timeout=0.1,
                env={"PATH": "/usr/bin:/bin"},
                stdout_limit=1024,
                stderr_limit=1024,
            )

    def test_output_flood_reaps_detached_descendant_and_zombie(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            descendant_pid = root / "flood-descendant-pid"
            sentinel = root / "flood-descendant-survived"
            program = (
                "import os,signal,time\n"
                "if os.fork() == 0:\n"
                " os.setsid()\n"
                " if os.fork() != 0: time.sleep(60);os._exit(0)\n"
                " signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                f" open({str(descendant_pid)!r},'w').write(str(os.getpid()))\n"
                " time.sleep(0.7)\n"
                f" open({str(sentinel)!r},'wb').write(b'survived')\n"
                " os._exit(0)\n"
                f"while not os.path.exists({str(descendant_pid)!r}):"
                " time.sleep(0.005)\n"
                "os.write(2,b'x'*131072)\n"
                "time.sleep(60)\n"
            )
            with (
                mock.patch.object(
                    MODULE,
                    "PROCESS_TREE_TERM_SECONDS",
                    0.1,
                ),
                mock.patch.object(
                    MODULE,
                    "PROCESS_TREE_QUIESCENCE_SECONDS",
                    0.05,
                ),
                self.assertRaisesRegex(
                    MODULE.BoundedCommandError,
                    "stderr exceeded",
                ),
            ):
                MODULE._bounded_command(
                    [sys.executable, "-I", "-B", "-c", program],
                    timeout=5,
                    env={"PATH": "/usr/bin:/bin"},
                    stdout_limit=1024,
                    stderr_limit=1024,
                )
            time.sleep(0.8)
            self.assertFalse(sentinel.exists())
            self.assertTrue(descendant_pid.is_file())
            self.assertFalse(
                Path(
                    f"/proc/{descendant_pid.read_text(encoding='ascii')}"
                ).exists()
            )

    def test_rapid_parent_exit_contains_reparented_double_fork(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            descendant_pid = root / "rapid-descendant-pid"
            sentinel = root / "rapid-descendant-survived"
            program = (
                "import os,signal,time\n"
                "if os.fork() == 0:\n"
                " os.setsid()\n"
                " if os.fork() != 0: time.sleep(60);os._exit(0)\n"
                " signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                f" open({str(descendant_pid)!r},'w').write(str(os.getpid()))\n"
                " time.sleep(0.7)\n"
                f" open({str(sentinel)!r},'wb').write(b'survived')\n"
                " os._exit(0)\n"
                f"while not os.path.exists({str(descendant_pid)!r}):"
                " time.sleep(0.005)\n"
                "os._exit(0)\n"
            )
            started = time.monotonic()
            with (
                mock.patch.object(
                    MODULE,
                    "PROCESS_TREE_TERM_SECONDS",
                    0.1,
                ),
                mock.patch.object(
                    MODULE,
                    "PROCESS_TREE_QUIESCENCE_SECONDS",
                    0.05,
                ),
            ):
                result = MODULE._bounded_command(
                    [sys.executable, "-I", "-B", "-c", program],
                    timeout=5,
                    env={"PATH": "/usr/bin:/bin"},
                    stdout_limit=1024,
                    stderr_limit=1024,
                )
            self.assertEqual(result.returncode, 0)
            self.assertLess(time.monotonic() - started, 2)
            time.sleep(0.8)
            self.assertFalse(sentinel.exists())
            self.assertTrue(descendant_pid.is_file())
            self.assertFalse(
                Path(
                    f"/proc/{descendant_pid.read_text(encoding='ascii')}"
                ).exists()
            )

    def test_keyboard_interrupt_cleanup_is_baseexception_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            descendant_pid = root / "interrupt-descendant-pid"
            sentinel = root / "interrupt-descendant-survived"
            program = (
                "import os,signal,time\n"
                "if os.fork() == 0:\n"
                " os.setsid()\n"
                " if os.fork() != 0: time.sleep(60);os._exit(0)\n"
                " signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                f" open({str(descendant_pid)!r},'w').write(str(os.getpid()))\n"
                " time.sleep(0.7)\n"
                f" open({str(sentinel)!r},'wb').write(b'survived')\n"
                " os._exit(0)\n"
                f"while not os.path.exists({str(descendant_pid)!r}):"
                " time.sleep(0.005)\n"
                "time.sleep(60)\n"
            )

            def interrupt_when_ready() -> None:
                deadline = time.monotonic() + 2
                while (
                    not descendant_pid.exists()
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                os.kill(os.getpid(), signal.SIGINT)

            interrupter = threading.Thread(
                target=interrupt_when_ready,
                daemon=True,
            )
            interrupter.start()
            with (
                mock.patch.object(
                    MODULE,
                    "PROCESS_TREE_TERM_SECONDS",
                    0.1,
                ),
                mock.patch.object(
                    MODULE,
                    "PROCESS_TREE_QUIESCENCE_SECONDS",
                    0.05,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                MODULE._bounded_command(
                    [sys.executable, "-I", "-B", "-c", program],
                    timeout=5,
                    env={"PATH": "/usr/bin:/bin"},
                    stdout_limit=1024,
                    stderr_limit=1024,
                )
            interrupter.join(timeout=2)
            time.sleep(0.8)
            self.assertFalse(sentinel.exists())
            self.assertTrue(descendant_pid.is_file())
            self.assertFalse(
                Path(
                    f"/proc/{descendant_pid.read_text(encoding='ascii')}"
                ).exists()
            )

    def test_identity_bound_signal_refuses_reused_pid(self):
        identity = MODULE.ProcessIdentity(
            process_id=4242,
            parent_id=os.getpid(),
            process_group=4242,
            session_id=4242,
            starttime=100,
            state="S",
        )
        with (
            mock.patch.object(
                MODULE,
                "_proc_identity",
                return_value=(
                    os.getpid(),
                    4242,
                    4242,
                    101,
                    "S",
                ),
            ),
            mock.patch.object(
                MODULE.os,
                "pidfd_open",
            ) as pidfd_open,
        ):
            MODULE._signal_process_identity(identity, signal.SIGKILL)
        pidfd_open.assert_not_called()

    def test_root_pidfd_contains_when_proc_identity_is_unavailable(self):
        opened: list[tuple[int, int]] = []
        real_pidfd_open = os.pidfd_open

        def capture_pidfd(process_id: int, flags: int = 0) -> int:
            descriptor = real_pidfd_open(process_id, flags)
            opened.append((process_id, descriptor))
            return descriptor

        with (
            mock.patch.object(
                MODULE,
                "_direct_child_baseline",
                return_value=frozenset(),
            ),
            mock.patch.object(
                MODULE,
                "_read_process_identity",
                side_effect=MODULE.BoundedCommandError(
                    "forced subprocess identity failure"
                ),
            ),
            mock.patch.object(
                MODULE.os,
                "pidfd_open",
                side_effect=capture_pidfd,
            ),
            self.assertRaisesRegex(
                MODULE.BoundedCommandError,
                "forced subprocess identity failure",
            ),
        ):
            MODULE._bounded_command(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    "import time;time.sleep(60)",
                ],
                timeout=5,
                env={"PATH": "/usr/bin:/bin"},
                stdout_limit=1024,
                stderr_limit=1024,
            )
        self.assertEqual(len(opened), 1)
        process_id, descriptor = opened[0]
        self.assertFalse(Path(f"/proc/{process_id}").exists())
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_owned_processes_refuses_reused_root_pid(self):
        root_identity = MODULE.ProcessIdentity(
            process_id=4242,
            parent_id=os.getpid(),
            process_group=4242,
            session_id=4242,
            starttime=100,
            state="S",
        )
        reused_root = MODULE.ProcessIdentity(
            process_id=4242,
            parent_id=os.getpid(),
            process_group=4242,
            session_id=4242,
            starttime=101,
            state="S",
        )
        with mock.patch.object(
            MODULE,
            "_process_snapshot",
            return_value={reused_root.process_id: reused_root},
        ):
            self.assertEqual(
                MODULE._owned_processes(
                    root_identity,
                    baseline_children=frozenset(),
                ),
                (),
            )

    def test_adopted_zombie_reaping_uses_exact_pid_and_wnohang(self):
        root_identity = MODULE.ProcessIdentity(
            process_id=9999,
            parent_id=os.getpid(),
            process_group=9999,
            session_id=9999,
            starttime=100,
            state="S",
        )
        zombie = MODULE.ProcessIdentity(
            process_id=4343,
            parent_id=os.getpid(),
            process_group=4343,
            session_id=4343,
            starttime=200,
            state="Z",
        )
        with (
            mock.patch.object(
                MODULE,
                "_owned_processes",
                side_effect=((zombie,), ()),
            ),
            mock.patch.object(
                MODULE.os,
                "waitpid",
                return_value=(zombie.process_id, 0),
            ) as waitpid,
        ):
            MODULE._reap_owned_zombies(
                root_identity,
                baseline_children=frozenset(),
                tracked={root_identity},
            )
        waitpid.assert_called_once_with(zombie.process_id, os.WNOHANG)

    def test_git_probe_neutralizes_local_config_and_rejects_index_flags(self):
        completed = MODULE.BoundedCommandResult(0, b"HEAD\n", b"")
        with mock.patch.object(
            MODULE,
            "_bounded_command",
            return_value=completed,
        ) as bounded:
            self.assertEqual(
                MODULE._run_git(
                    [MODULE.GIT, "-C", "/release", "rev-parse", "HEAD"]
                ),
                "HEAD",
            )
        command = bounded.call_args.args[0]
        self.assertEqual(command[0], MODULE.GIT)
        self.assertIn("core.fsmonitor=false", command)
        self.assertIn("core.hooksPath=/dev/null", command)
        self.assertIn("--git-dir=/release/.git", command)
        self.assertIn("--work-tree=/release", command)
        self.assertNotIn("-C", command)
        with mock.patch.object(
            MODULE,
            "_run_git",
            return_value="S scripts/hidden.py",
        ):
            with self.assertRaisesRegex(
                MODULE.GlobalDockerInventoryError,
                "hidden tracked state",
            ):
                MODULE._verify_git_index_visibility(Path("/release"))

    def test_registry_port_operation_image_tag_is_detected(self):
        image = _image()
        project_base = "tb3f-" + "a" * 48
        project_name = f"{project_base}-bot-fi"
        image["RepoTags"] = [
            f"registry.example:5000/private/{project_name}:release"
        ]
        descriptor = MODULE._image_descriptor(
            image,
            operation_id=OPERATION_ID,
            project_base=project_base,
            project_name=project_name,
        )
        self.assertTrue(descriptor.operation_match)

    def test_execution_context_rejects_non_root_and_missing_bytecode_guard(self):
        request = _request("capture-before")
        with mock.patch.object(MODULE.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(
                MODULE.GlobalDockerInventoryError,
                "requires root:root",
            ):
                MODULE._verify_execution_context(
                    request,
                    observed_host_addresses={request["expected_host"]},
                )
        for reserved in (
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_CONFIG",
            "COMPOSE_PROFILES",
        ):
            with (
                self.subTest(reserved=reserved),
                mock.patch.object(MODULE.os, "geteuid", return_value=0),
                mock.patch.object(MODULE.os, "getegid", return_value=0),
                mock.patch.object(
                    MODULE.sys,
                    "flags",
                    SimpleNamespace(isolated=1),
                ),
                mock.patch.dict(
                    MODULE.os.environ,
                    {
                        "PYTHONDONTWRITEBYTECODE": "1",
                        reserved: "hostile",
                    },
                    clear=True,
                ),
            ):
                with self.assertRaisesRegex(
                    MODULE.GlobalDockerInventoryError,
                    "reserved Docker or Compose",
                ):
                    MODULE._verify_execution_context(
                        request,
                        observed_host_addresses={request["expected_host"]},
                    )
        with (
            mock.patch.object(MODULE.os, "geteuid", return_value=0),
            mock.patch.object(MODULE.os, "getegid", return_value=0),
            mock.patch.dict(MODULE.os.environ, {}, clear=True),
        ):
            with self.assertRaisesRegex(
                MODULE.GlobalDockerInventoryError,
                "PYTHONDONTWRITEBYTECODE",
            ):
                MODULE._verify_execution_context(
                    request,
                    observed_host_addresses={request["expected_host"]},
                )

    def test_before_after_are_stable_and_compare_to_exact_zero_delta(self):
        before_request = _request("capture-before")
        before_snapshot = _snapshot(before_request, after=False)
        before = self._execute(
            before_request,
            FakeRunner([before_snapshot, copy.deepcopy(before_snapshot)]),
        )
        after_request = _request("capture-after")
        after_snapshot = _snapshot(after_request, after=True)
        after = self._execute(
            after_request,
            FakeRunner([after_snapshot, copy.deepcopy(after_snapshot)]),
        )
        comparison = MODULE.compare_non_operation_inventories(
            before,
            after,
            before_request=before_request,
            after_request=after_request,
        )
        self.assertEqual(comparison["status"], "verified-zero-delta")
        self.assertEqual(
            comparison["non_operation_resource_delta_count"],
            0,
        )
        self.assertEqual(
            before["operation_resource_counts"],
            {"container": 0, "network": 0, "volume": 0, "image": 0},
        )
        self.assertEqual(
            after["operation_resource_counts"],
            {"container": 1, "network": 1, "volume": 0, "image": 0},
        )
        self.assertFalse(after["descriptors_returned"])
        self.assertLess(
            len(MODULE.canonical_json(after)),
            MODULE.MAX_RESPONSE_BYTES,
        )

    def test_non_operation_state_delta_is_detected(self):
        before_request = _request("capture-before")
        before_snapshot = _snapshot(before_request, after=False)
        before = self._execute(
            before_request,
            FakeRunner([before_snapshot, copy.deepcopy(before_snapshot)]),
        )
        after_request = _request("capture-after")
        after_snapshot = _snapshot(
            after_request,
            after=True,
            changed_non_operation=True,
        )
        after = self._execute(
            after_request,
            FakeRunner([after_snapshot, copy.deepcopy(after_snapshot)]),
        )
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "non-operation Docker inventory changed",
        ):
            MODULE.compare_non_operation_inventories(
                before,
                after,
                before_request=before_request,
                after_request=after_request,
            )

    def test_foreign_container_memory_drift_is_detected(self):
        self._assert_foreign_host_config_drift(
            "Memory",
            768 * 1024**2,
        )

    def test_foreign_container_pids_limit_drift_is_detected(self):
        self._assert_foreign_host_config_drift("PidsLimit", 129)

    def test_capture_rejects_race_between_consecutive_roots(self):
        request = _request("capture-before")
        first = _snapshot(request, after=False)
        second = _snapshot(
            request,
            after=False,
            changed_non_operation=True,
        )
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "two stable consecutive roots",
        ):
            self._execute(request, FakeRunner([first, second]))

    def test_before_rejects_any_operation_prefix_or_label_residue(self):
        request = _request("capture-before")
        snapshot = _snapshot(
            request,
            after=False,
            extra_operation_volume=True,
        )
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "operation Docker resource closure differs",
        ):
            self._execute(
                request,
                FakeRunner([snapshot, copy.deepcopy(snapshot)]),
            )

    def test_after_rejects_extra_operation_volume(self):
        request = _request("capture-after")
        snapshot = _snapshot(
            request,
            after=True,
            extra_operation_volume=True,
        )
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "operation Docker resource closure differs",
        ):
            self._execute(
                request,
                FakeRunner([snapshot, copy.deepcopy(snapshot)]),
            )

    def test_after_rejects_wrong_database_container_contract(self):
        request = _request("capture-after")
        snapshot = _snapshot(request, after=True)
        snapshot["containers"][OPERATION_CONTAINER_ID]["HostConfig"][
            "Privileged"
        ] = True
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "database container contract differs",
        ):
            self._execute(
                request,
                FakeRunner([snapshot, copy.deepcopy(snapshot)]),
            )

    def test_after_requires_healthy_database_container(self):
        request = _request("capture-after")
        for label, health in (
            ("missing", None),
            ("starting", {"Status": "starting"}),
            ("unhealthy", {"Status": "unhealthy"}),
        ):
            with self.subTest(label=label):
                snapshot = _snapshot(request, after=True)
                state = snapshot["containers"][OPERATION_CONTAINER_ID][
                    "State"
                ]
                if health is None:
                    del state["Health"]
                else:
                    state["Health"] = health
                with self.assertRaisesRegex(
                    MODULE.GlobalDockerInventoryError,
                    "database container contract differs",
                ):
                    self._execute(
                        request,
                        FakeRunner(
                            [snapshot, copy.deepcopy(snapshot)]
                        ),
                    )

    def test_after_requires_exact_database_compose_label_closure(self):
        request = _request("capture-after")
        mutations = {
            "extra": (
                "com.docker.compose.future-owner",
                "foreign",
            ),
            "image": ("com.docker.compose.image", f"sha256:{'f' * 64}"),
            "depends": (
                "com.docker.compose.depends_on",
                "foreign:service_started:false",
            ),
            "config-files": (
                "com.docker.compose.project.config_files",
                "/foreign",
            ),
            "environment-file": (
                "com.docker.compose.project.environment_file",
                "/foreign",
            ),
            "working-dir": (
                "com.docker.compose.project.working_dir",
                "/foreign",
            ),
            "version": ("com.docker.compose.version", "5.1.3"),
        }
        for label, (field, value) in mutations.items():
            with self.subTest(label=label):
                snapshot = _snapshot(request, after=True)
                snapshot["containers"][OPERATION_CONTAINER_ID]["Config"][
                    "Labels"
                ][field] = value
                with self.assertRaisesRegex(
                    MODULE.GlobalDockerInventoryError,
                    "database container contract differs",
                ):
                    self._execute(
                        request,
                        FakeRunner(
                            [snapshot, copy.deepcopy(snapshot)]
                        ),
                    )

    def test_after_rejects_operation_container_memory_drift(self):
        request = _request("capture-after")
        snapshot = _snapshot(request, after=True)
        snapshot["containers"][OPERATION_CONTAINER_ID]["HostConfig"][
            "Memory"
        ] += 1
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "database container contract differs",
        ):
            self._execute(
                request,
                FakeRunner([snapshot, copy.deepcopy(snapshot)]),
            )

    def test_after_rejects_operation_container_pids_limit_drift(self):
        request = _request("capture-after")
        snapshot = _snapshot(request, after=True)
        snapshot["containers"][OPERATION_CONTAINER_ID]["HostConfig"][
            "PidsLimit"
        ] += 1
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "database container contract differs",
        ):
            self._execute(
                request,
                FakeRunner([snapshot, copy.deepcopy(snapshot)]),
            )

    def test_after_rejects_host_config_digest_tamper_and_drift(self):
        request = _request("capture-after")
        tampered = {
            **request,
            "expected_operation_host_config_sha256": "a" * 64,
        }
        tampered["request_binding_sha256"] = MODULE._request_binding(tampered)
        snapshot = _snapshot(tampered, after=True)
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "HostConfig digest differs",
        ):
            self._execute(
                tampered,
                FakeRunner([snapshot, copy.deepcopy(snapshot)]),
            )

        first = _snapshot(request, after=True)
        second = copy.deepcopy(first)
        second["containers"][OPERATION_CONTAINER_ID]["HostConfig"][
            "MemorySwap"
        ] += 1
        with self.assertRaises(MODULE.GlobalDockerInventoryError):
            self._execute(request, FakeRunner([first, second]))

    def test_response_rejects_missing_or_tampered_host_config_digest(self):
        request = _request("capture-after")
        snapshot = _snapshot(request, after=True)
        response = self._execute(
            request,
            FakeRunner([snapshot, copy.deepcopy(snapshot)]),
        )
        missing = dict(response)
        del missing["observed_operation_host_config_sha256"]
        with self.assertRaises(MODULE.GlobalDockerInventoryError):
            MODULE.validate_response(missing, request=request)
        tampered = {
            **response,
            "observed_operation_host_config_sha256": "a" * 64,
        }
        unsigned = {
            key: value
            for key, value in tampered.items()
            if key != "response_sha256"
        }
        tampered["response_sha256"] = MODULE._sha256(
            MODULE.canonical_json(unsigned)
        )
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "HostConfig digest differs",
        ):
            MODULE.validate_response(tampered, request=request)

    def test_container_config_api_v152_rejects_every_omission(self):
        request = _request("capture-after")
        for field in sorted(MODULE.CONTAINER_CONFIG_FIELDS):
            with self.subTest(field=field):
                snapshot = _snapshot(request, after=True)
                del snapshot["containers"][OPERATION_CONTAINER_ID][
                    "Config"
                ][field]
                with self.assertRaisesRegex(
                    MODULE.GlobalDockerInventoryError,
                    "container inspection shape is invalid",
                ):
                    self._execute(
                        request,
                        FakeRunner(
                            [snapshot, copy.deepcopy(snapshot)]
                        ),
                    )

    def test_after_rejects_wrong_network_attachment_closure(self):
        request = _request("capture-after")
        snapshot = _snapshot(request, after=True)
        snapshot["networks"][OPERATION_NETWORK_ID]["Containers"] = {}
        with self.assertRaisesRegex(
            MODULE.GlobalDockerInventoryError,
            "operation network contract differs",
        ):
            self._execute(
                request,
                FakeRunner([snapshot, copy.deepcopy(snapshot)]),
            )

    def test_env_values_and_raw_paths_are_not_descriptor_fields(self):
        request = _request("capture-before")
        first = _snapshot(request, after=False)
        second = copy.deepcopy(first)
        second["containers"][NON_OPERATION_CONTAINER_ID]["Config"]["Env"] = [
            "DIFFERENT_SECRET=still-not-normalized"
        ]
        first_capture = MODULE._capture_once(
            request,
            runner=FakeRunner([first]),
        )
        second_capture = MODULE._capture_once(
            request,
            runner=FakeRunner([second]),
        )
        self.assertTrue(MODULE._same_capture(first_capture, second_capture))
        encoded = MODULE.canonical_json(
            [
                descriptor.document()
                for descriptor in first_capture.descriptors
            ]
        )
        self.assertNotIn(b"SECRET", encoded)
        self.assertNotIn(b"/srv/", encoded)
        self.assertNotIn(b"/var/lib/", encoded)

    def test_duplicate_image_list_is_deduplicated_before_chunked_inspect(self):
        request = _request("capture-before")
        snapshot = _snapshot(request, after=False)
        response = self._execute(
            request,
            FakeRunner(
                [snapshot, copy.deepcopy(snapshot)],
                duplicate_image=True,
            ),
        )
        self.assertEqual(response["resource_counts"]["image"], 1)

    def test_stopped_container_may_have_empty_network_endpoint_identities(self):
        request = _request("capture-before")
        snapshot = _snapshot(request, after=False)
        container = snapshot["containers"][NON_OPERATION_CONTAINER_ID]
        container["State"] = _state(running=False)
        attachment = container["NetworkSettings"]["Networks"]["legacy"]
        attachment["NetworkID"] = ""
        attachment["EndpointID"] = ""
        response = self._execute(
            request,
            FakeRunner([snapshot, copy.deepcopy(snapshot)]),
        )
        self.assertEqual(response["resource_counts"]["container"], 1)

    def test_duplicate_resource_id_and_duplicate_inspect_key_fail_closed(self):
        with self.assertRaises(MODULE.GlobalDockerInventoryError):
            MODULE._parse_lines(
                f"{NON_OPERATION_CONTAINER_ID}\n"
                f"{NON_OPERATION_CONTAINER_ID}",
                pattern=MODULE.CONTAINER_ID_RE,
                label="duplicate",
            )

        class DuplicateInspectRunner:
            def run(self, _arguments, *, timeout=30):
                del timeout
                return (
                    '[{"Id":"'
                    + NON_OPERATION_CONTAINER_ID
                    + '","x":1,"x":2}]'
                )

        with self.assertRaises(MODULE.GlobalDockerInventoryError):
            MODULE._load_inspect_rows(
                DuplicateInspectRunner(),
                command_prefix=(*MODULE.DOCKER_BASE, "inspect"),
                identifiers=[NON_OPERATION_CONTAINER_ID],
                identity_field="Id",
                label="duplicate inspect",
            )

    def test_inspection_is_chunked_and_resource_count_is_hard_bounded(self):
        identifiers = [f"{index:064x}" for index in range(1, 19)]

        class ChunkRunner:
            def __init__(self):
                self.chunk_sizes = []

            def run(self, arguments, *, timeout=30):
                del timeout
                chunk = list(
                    arguments[len(MODULE.DOCKER_BASE) + 1 :]
                )
                self.chunk_sizes.append(len(chunk))
                return json.dumps(
                    [{"Id": identity} for identity in chunk],
                    separators=(",", ":"),
                )

        runner = ChunkRunner()
        rows = MODULE._load_inspect_rows(
            runner,
            command_prefix=(*MODULE.DOCKER_BASE, "inspect"),
            identifiers=identifiers,
            identity_field="Id",
            label="chunked inspection",
        )
        self.assertEqual(set(rows), set(identifiers))
        self.assertEqual(runner.chunk_sizes, [MODULE.MAX_INSPECT_CHUNK, 2])
        oversized = "\n".join(
            f"{index:064x}"
            for index in range(1, MODULE.MAX_RESOURCES_PER_KIND + 2)
        )
        with self.assertRaises(MODULE.GlobalDockerInventoryError):
            MODULE._parse_lines(
                oversized,
                pattern=MODULE.CONTAINER_ID_RE,
                label="oversized resource inventory",
            )

    def test_response_and_comparison_hash_tamper_fail_closed(self):
        before_request = _request("capture-before")
        before_snapshot = _snapshot(before_request, after=False)
        before = self._execute(
            before_request,
            FakeRunner([before_snapshot, copy.deepcopy(before_snapshot)]),
        )
        tampered = {**before, "stable_capture_count": 3}
        with self.assertRaises(MODULE.GlobalDockerInventoryError):
            MODULE.validate_response(tampered, request=before_request)

        after_request = _request("capture-after")
        after_snapshot = _snapshot(after_request, after=True)
        after = self._execute(
            after_request,
            FakeRunner([after_snapshot, copy.deepcopy(after_snapshot)]),
        )
        comparison = MODULE.compare_non_operation_inventories(
            before,
            after,
            before_request=before_request,
            after_request=after_request,
        )
        tampered_comparison = {
            **comparison,
            "non_operation_resource_delta_count": 1,
        }
        with self.assertRaises(MODULE.GlobalDockerInventoryError):
            MODULE.validate_comparison(
                tampered_comparison,
                before=before,
                after=after,
            )

    def test_secure_release_file_rejects_symlink_hardlink_and_mode(self):
        base = Path("/root/trading-bot/trading_bot/tmp")
        base.mkdir(mode=0o755, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temporary:
            root = Path(temporary)
            path = root / "agent.py"
            path.write_bytes(b"safe\n")
            path.chmod(0o644)
            digest = hashlib.sha256(b"safe\n").hexdigest()
            MODULE._secure_file_sha256(
                path,
                expected_sha256=digest,
                label="test file",
            )
            hardlink = root / "hardlink.py"
            os.link(path, hardlink)
            with self.assertRaises(MODULE.GlobalDockerInventoryError):
                MODULE._secure_file_sha256(
                    path,
                    expected_sha256=digest,
                    label="test file",
                )
            hardlink.unlink()
            symlink = root / "symlink.py"
            symlink.symlink_to(path)
            with self.assertRaises(MODULE.GlobalDockerInventoryError):
                MODULE._secure_file_sha256(
                    symlink,
                    expected_sha256=digest,
                    label="test symlink",
                )
            path.chmod(0o666)
            with self.assertRaises(MODULE.GlobalDockerInventoryError):
                MODULE._secure_file_sha256(
                    path,
                    expected_sha256=digest,
                    label="test mode",
                )

    def test_host_stdio_rejects_trailing_request_and_main_emits_one_line(self):
        request = _request("capture-before")
        payload = MODULE.canonical_json(request)
        with mock.patch.object(
            MODULE.sys,
            "stdin",
            StreamWrapper(payload + b"\ntrailing"),
        ):
            with self.assertRaises(MODULE.GlobalDockerInventoryError):
                MODULE._host_stdio()

        output = StreamWrapper()
        with mock.patch.object(MODULE.sys, "stdout", output):
            status = MODULE.main([])
        self.assertEqual(status, 1)
        emitted = output.buffer.getvalue()
        self.assertEqual(emitted.count(b"\n"), 1)
        self.assertEqual(
            json.loads(emitted)["error_class"],
            "GlobalDockerInventoryError",
        )

        unexpected_output = StreamWrapper()
        with (
            mock.patch.object(MODULE.sys, "stdout", unexpected_output),
            mock.patch.object(
                MODULE,
                "_host_stdio",
                side_effect=ValueError("dependency failure"),
            ),
        ):
            unexpected_status = MODULE.main(["--host-stdio"])
        self.assertEqual(unexpected_status, 1)
        self.assertEqual(
            unexpected_output.buffer.getvalue().count(b"\n"),
            1,
        )


if __name__ == "__main__":
    unittest.main()
