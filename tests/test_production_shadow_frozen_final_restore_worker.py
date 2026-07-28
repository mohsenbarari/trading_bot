from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import copy
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

import yaml

from scripts import production_shadow_frozen_final_restore_worker as MODULE


CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
OPERATION_ID = "22222222-2222-4222-8222-222222222222"
RELEASE_SHA = "a" * 40
RELEASE_TREE_SHA = "b" * 40
GENERATION = "c" * 64
RESTORE_SET_SHA256 = "d" * 64
CONTROLLER_SHA256 = "e" * 64
INSTALLER_SHA256 = "f" * 64
POSTGRES_IMAGE_ID = "sha256:" + "1" * 64
POSTGRES_CONTENT_ID = "sha256:" + "2" * 64
DATABASE_CONFIG_HASH = "3" * 64
RESTORE_TOOL_CONFIG_HASH = "4" * 64
NETWORK_ID = "5" * 64
ENDPOINT_ID = "6" * 64
COMPOSE_VERSION = "5.1.4"


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def write_root_file(path: Path, payload: bytes, mode: int = 0o600) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    for parent in [path.parent, *path.parents]:
        if parent == Path("/"):
            break
        try:
            parent.chmod(0o700)
        except OSError:
            break
    path.write_bytes(payload)
    path.chmod(mode)
    return hashlib.sha256(payload).hexdigest()


class FakeRunner:
    def __init__(self, callback=None, stream_callback=None) -> None:
        self.callback = callback
        self.stream_callback = stream_callback
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.stream_calls: list[list[str]] = []

    def run(self, arguments, *, timeout, env, stdin=MODULE.subprocess.DEVNULL):
        args = list(arguments)
        self.calls.append((args, dict(env)))
        if self.callback is None:
            return ""
        return self.callback(args, dict(env), stdin)

    def stream(self, arguments, *, timeout, env):
        args = list(arguments)
        self.stream_calls.append(args)
        if self.stream_callback is None:
            return MODULE.StreamDigest("3" * 64, 0, 0)
        return self.stream_callback(args, dict(env))


class Fixture:
    def __init__(self, root: Path, role: str = "bot_fi") -> None:
        self.root = root
        self.role = role
        self.patch = mock.patch.multiple(
            MODULE,
            PROJECT_ROOT_PREFIX=root / "project",
            DATA_ROOT_PREFIX=root / "data",
            SECRET_ROOT_PREFIX=root / "secret",
        )
        self.patch.start()
        self.paths = MODULE.runtime_paths(
            OPERATION_ID,
            RELEASE_SHA,
            GENERATION,
            role,
        )
        for path in (
            self.paths.project_root,
            self.paths.release_root,
            self.paths.data_generation_root,
            self.paths.restore_input_root,
            self.paths.secret_generation_root,
        ):
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(0o700)
        role_path = MODULE.ROLE_PATHS[role]
        canonical_source = (
            Path(__file__).resolve().parents[1]
            / "deploy"
            / "production"
            / "docker-compose.three-site-shadow.yml"
        )
        self.canonical_compose = (
            self.paths.secret_generation_root / "canonical-compose.yml"
        )
        shutil.copyfile(canonical_source, self.canonical_compose)
        self.canonical_compose.chmod(0o600)
        canonical_document = yaml.safe_load(
            self.canonical_compose.read_text(encoding="utf-8")
        )
        role_document = MODULE.render_role_compose(
            canonical_document,
            role=role_path,
            scope="prepare",
        )
        names = {f"{role}_db", f"{role}_restore_tool"}
        role_document["services"] = {
            name: value
            for name, value in role_document["services"].items()
            if name in names
        }
        role_document["networks"] = {
            role: role_document["networks"][role]
        }
        role_document.pop("volumes", None)
        self.role_compose = (
            self.paths.secret_generation_root
            / "docker-compose.restore.yml"
        )
        write_root_file(
            self.role_compose,
            yaml.safe_dump(
                role_document,
                allow_unicode=False,
                default_flow_style=False,
                sort_keys=True,
            ).encode("utf-8"),
        )
        prefix = MODULE.ROLE_PREFIXES[role]
        env_values = {
            name: "x"
            for name in MODULE.referenced_environment_names(role_document)
        }
        env_values.update(
            {
                f"{prefix}_POSTGRES_USER": "owner",
                f"{prefix}_POSTGRES_PASSWORD": "secret-value",
                f"{prefix}_POSTGRES_DB": "database",
                "PRODUCTION_SHADOW_PROJECT": "tb3p-rehearsal",
                "PRODUCTION_SHADOW_DATA_ROOT": str(root / "rehearsal-data"),
                "PRODUCTION_SHADOW_SECRET_ROOT": str(root / "rehearsal-secret"),
                "PRODUCTION_SHADOW_CGROUP_PARENT": "/rehearsal",
                "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID": POSTGRES_IMAGE_ID,
                "PRODUCTION_SHADOW_OPERATION_ID": OPERATION_ID,
                "PRODUCTION_SHADOW_PROJECT_ROOT": str(
                    self.paths.project_root
                ),
                "PRODUCTION_SHADOW_RELEASE_ROOT": str(
                    self.paths.release_root
                ),
            }
        )
        self.environment = (
            self.paths.secret_generation_root / "runtime.env.role"
        )
        write_root_file(
            self.environment,
            "".join(
                f"{key}={value}\n"
                for key, value in sorted(env_values.items())
            ).encode("ascii"),
        )
        self.worker = (
            self.paths.release_root
            / "scripts"
            / "production_shadow_frozen_final_restore_worker.py"
        )
        write_root_file(self.worker, b"worker")
        artifacts = {}
        for kind, filename, payload, tree in (
            ("database-backup", "database.dump", b"database", None),
            ("uploads-archive", "uploads.tar.gz", b"uploads", "4" * 64),
            ("audit-archive", "audit.tar.gz", b"audit", "5" * 64),
        ):
            path = self.paths.restore_input_root / filename
            digest = write_root_file(path, payload)
            artifacts[kind] = MODULE.ArtifactBinding(
                path=path,
                sha256=digest,
                bytes=len(payload),
                restored_tree_sha256=tree,
            )
        self.document = {
            "target_transport": MODULE.ROLE_TRANSPORTS[role],
            "environment_sha256": hashlib.sha256(
                self.environment.read_bytes()
            ).hexdigest(),
            "canonical_compose_sha256": hashlib.sha256(
                self.canonical_compose.read_bytes()
            ).hexdigest(),
            "role_compose_sha256": hashlib.sha256(
                self.role_compose.read_bytes()
            ).hexdigest(),
        }
        self.manifest = MODULE.RoleManifest(
            document=self.document,
            canonical_sha256="6" * 64,
            operation_id=OPERATION_ID,
            role=role,
            release_sha=RELEASE_SHA,
            release_tree_sha=RELEASE_TREE_SHA,
            restore_set_sha256=RESTORE_SET_SHA256,
            restore_generation_sha256=GENERATION,
            source_role=(
                "bot_fi" if role == "bot_fi" else "webapp_fi"
            ),
            controller_manifest_sha256=CONTROLLER_SHA256,
            installer_receipt_sha256=INSTALLER_SHA256,
            postgres_image_id=POSTGRES_IMAGE_ID,
            postgres_image_content_identity=POSTGRES_CONTENT_ID,
            artifacts=artifacts,
            source_database=MODULE.DatabaseExpectation(
                alembic_revision="source_1",
                fingerprint_algorithm=(
                    "pg-copy-jsonl-sha256-canonical-session-v1"
                ),
                database_fingerprint_sha256="7" * 64,
                row_count=12,
                table_count=3,
            ),
            paths=self.paths,
            controller_manifest_path=(
                self.paths.secret_generation_root
                / "controller-manifest.json"
            ),
            restore_set_path=(
                self.paths.secret_generation_root / "restore-set.json"
            ),
            canonical_compose_path=self.canonical_compose,
            role_compose_path=self.role_compose,
            environment_path=self.environment,
            worker_path=self.worker,
        )
        self.receipt_path = (
            self.paths.secret_generation_root
            / f"legacy-frozen-{'8' * 64}.json"
        )
        write_root_file(self.receipt_path, b"receipt")
        self.lease = MODULE.LeaseBinding(
            document={},
            path=(
                self.paths.secret_generation_root
                / "claims"
                / f"{'9' * 64}.json"
            ),
            sha256="9" * 64,
            epoch=2,
            nonce="a" * 64,
            receipt_path=self.receipt_path,
            receipt_sha256=hashlib.sha256(b"receipt").hexdigest(),
        )

    def close(self) -> None:
        self.patch.stop()

    def initialize_stores(self) -> None:
        MODULE._initialize_generation(self.manifest)


def rendered_config(fixture: Fixture) -> dict:
    manifest = fixture.manifest
    cgroup = MODULE._compose_environment(manifest)[1][
        "PRODUCTION_SHADOW_CGROUP_PARENT"
    ]
    services = {}
    for name in (
        f"{manifest.role}_db",
        f"{manifest.role}_restore_tool",
    ):
        if name.endswith("_db"):
            volumes = [
                {
                    "type": "bind",
                    "source": str(manifest.paths.postgres),
                    "target": "/var/lib/postgresql/data",
                }
            ]
        else:
            volumes = [
                {
                    "type": "bind",
                    "source": str(manifest.paths.restore_input_root),
                    "target": "/run/restore-input",
                    "read_only": True,
                },
                {
                    "type": "bind",
                    "source": str(manifest.paths.uploads),
                    "target": "/run/restore-target/uploads",
                },
                {
                    "type": "bind",
                    "source": str(manifest.paths.audit),
                    "target": "/run/restore-target/audit",
                },
            ]
        services[name] = {
            "image": POSTGRES_IMAGE_ID,
            "cgroup_parent": cgroup,
            "cpus": 2,
            "mem_limit": "2147483648",
            "pids_limit": 512,
            "labels": {
                "trading-bot.production.operation-id": OPERATION_ID,
            },
            "logging": {
                "driver": "json-file",
                "options": {"max-file": "5", "max-size": "20m"},
            },
            "networks": {manifest.role: None},
            "restart": (
                "unless-stopped" if name.endswith("_db") else "no"
            ),
            "volumes": volumes,
        }
        if name.endswith("_db"):
            services[name].update(
                {
                    "command": [
                        "postgres",
                        "-c",
                        "timezone=UTC",
                        "-c",
                        "log_timezone=UTC",
                    ],
                    "entrypoint": None,
                    "environment": {
                        "TZ": "UTC",
                        "PGTZ": "UTC",
                        "POSTGRES_USER": "owner",
                        "POSTGRES_PASSWORD": "secret-value",
                        "POSTGRES_DB": "database",
                    },
                    "healthcheck": {
                        "test": [
                            "CMD-SHELL",
                            "pg_isready -U $${POSTGRES_USER} "
                            "-d $${POSTGRES_DB}",
                        ],
                        "interval": "5s",
                        "timeout": "3s",
                        "retries": 30,
                    },
                }
            )
        else:
            services[name].update(
                {
                    "command": [
                        "sh",
                        "-ec",
                        "echo 'invoke with docker compose run and an "
                        "explicit restore command' >&2; exit 64",
                    ],
                    "entrypoint": None,
                    "environment": {
                        "PGHOST": f"{manifest.role}_db",
                        "PGUSER": "owner",
                        "PGPASSWORD": "secret-value",
                        "PGDATABASE": "database",
                    },
                    "healthcheck": {"disable": True},
                }
            )
    return {
        "name": manifest.paths.project_name,
        "services": services,
        "networks": {
            manifest.role: {
                "name": (
                    f"{manifest.paths.project_name}_{manifest.role}"
                ),
                "internal": True,
                "labels": {
                    "trading-bot.production.operation-id": OPERATION_ID,
                },
            }
        },
    }


def image_inspect_document() -> dict:
    return {
        "Id": POSTGRES_IMAGE_ID,
        "Config": {
            "Cmd": ["postgres"],
            "Entrypoint": ["docker-entrypoint.sh"],
            "Env": [
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin",
                "PG_MAJOR=17",
            ],
            "Labels": {
                "org.opencontainers.image.title": "postgres",
            },
            "User": "",
            "WorkingDir": "",
            "StopSignal": "SIGINT",
        },
    }


def database_container_row(
    fixture: Fixture,
    *,
    identifier: str = "1" * 64,
    config_hash: str = DATABASE_CONFIG_HASH,
    attached: bool = True,
) -> dict:
    manifest = fixture.manifest
    network_name = f"{manifest.paths.project_name}_{manifest.role}"
    endpoint = {
        "NetworkID": NETWORK_ID if attached else "",
        "EndpointID": ENDPOINT_ID if attached else "",
        "Gateway": "172.30.0.1" if attached else "",
        "IPAddress": "172.30.0.2" if attached else "",
        "IPPrefixLen": 16 if attached else 0,
        "IPv6Gateway": "",
        "GlobalIPv6Address": "",
        "GlobalIPv6PrefixLen": 0,
        "MacAddress": "02:42:ac:1e:00:02" if attached else "",
    }
    return {
        "Id": identifier,
        "Name": (
            f"/{manifest.paths.project_name}-{manifest.role}_db-1"
        ),
        "Image": POSTGRES_IMAGE_ID,
        "Config": {
            "Image": POSTGRES_IMAGE_ID,
            "Cmd": [
                "postgres",
                "-c",
                "timezone=UTC",
                "-c",
                "log_timezone=UTC",
            ],
            "Entrypoint": ["docker-entrypoint.sh"],
            "Env": [
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin",
                "PG_MAJOR=17",
                "TZ=UTC",
                "PGTZ=UTC",
                "POSTGRES_USER=owner",
                "POSTGRES_PASSWORD=secret-value",
                "POSTGRES_DB=database",
            ],
            "Healthcheck": {
                "Test": [
                    "CMD-SHELL",
                    "pg_isready -U ${POSTGRES_USER} "
                    "-d ${POSTGRES_DB}",
                ],
                "Interval": 5_000_000_000,
                "Timeout": 3_000_000_000,
                "Retries": 30,
                "StartPeriod": 0,
                "StartInterval": 0,
            },
            "Labels": {
                "org.opencontainers.image.title": "postgres",
                "trading-bot.production.operation-id": OPERATION_ID,
                "com.docker.compose.project": (
                    manifest.paths.project_name
                ),
                "com.docker.compose.service": f"{manifest.role}_db",
                "com.docker.compose.oneoff": "False",
                "com.docker.compose.container-number": "1",
                "com.docker.compose.config-hash": config_hash,
            },
            "User": "",
            "WorkingDir": "",
            "StopSignal": "SIGINT",
        },
        "HostConfig": {
            "RestartPolicy": {
                "Name": "unless-stopped",
                "MaximumRetryCount": 0,
            },
            "CgroupParent": MODULE._compose_environment(manifest)[1][
                "PRODUCTION_SHADOW_CGROUP_PARENT"
            ],
            "NanoCpus": 2_000_000_000,
            "Memory": 2 * 1024**3,
            "MemoryReservation": 0,
            "MemorySwap": 0,
            "PidsLimit": 512,
            "CpuShares": 0,
            "CpuPeriod": 0,
            "CpuQuota": 0,
            "CpusetCpus": "",
            "CpusetMems": "",
            "AutoRemove": False,
            "Privileged": False,
            "ReadonlyRootfs": False,
            "PublishAllPorts": False,
            "PortBindings": {},
            "CapAdd": None,
            "CapDrop": None,
            "SecurityOpt": None,
            "Devices": [],
            "DeviceRequests": None,
            "PidMode": "",
            "IpcMode": "private",
            "UTSMode": "",
            "UsernsMode": "",
            "Links": None,
            "ExtraHosts": None,
            "Dns": [],
            "DnsOptions": [],
            "DnsSearch": [],
            "GroupAdd": None,
            "Sysctls": None,
            "Tmpfs": None,
            "NetworkMode": network_name,
            "Binds": [
                f"{manifest.paths.postgres}:"
                "/var/lib/postgresql/data:rw"
            ],
            "LogConfig": {
                "Type": "json-file",
                "Config": {"max-file": "5", "max-size": "20m"},
            },
        },
        "NetworkSettings": {"Networks": {network_name: endpoint}},
        "Mounts": [
            {
                "Type": "bind",
                "Source": str(manifest.paths.postgres),
                "Destination": "/var/lib/postgresql/data",
                "RW": True,
            }
        ],
        "State": {
            "Running": True,
            "Status": "running",
            "Health": {"Status": "healthy"},
        },
    }


def restore_oneoff_row(
    fixture: Fixture,
    *,
    identifier: str = "7" * 64,
    config_hash: str = RESTORE_TOOL_CONFIG_HASH,
) -> dict:
    manifest = fixture.manifest
    network_name = f"{manifest.paths.project_name}_{manifest.role}"
    return {
        "Id": identifier,
        "Name": (
            f"/{manifest.paths.project_name}-"
            f"{manifest.role}_restore_tool-run-abcd1234"
        ),
        "Image": POSTGRES_IMAGE_ID,
        "Config": {
            "Image": POSTGRES_IMAGE_ID,
            "Cmd": [
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "--no-psqlrc",
                "-Atqc",
                "SELECT 1",
            ],
            "Entrypoint": ["docker-entrypoint.sh"],
            "Env": [
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin",
                "PG_MAJOR=17",
                f"PGHOST={manifest.role}_db",
                "PGUSER=owner",
                "PGPASSWORD=secret-value",
                "PGDATABASE=database",
            ],
            "Healthcheck": {
                "Test": ["NONE"],
                "Interval": 0,
                "Timeout": 0,
                "Retries": 0,
                "StartPeriod": 0,
                "StartInterval": 0,
            },
            "Labels": {
                "org.opencontainers.image.title": "postgres",
                "trading-bot.production.operation-id": OPERATION_ID,
                "trading-bot.production.restore-generation": GENERATION,
                "com.docker.compose.project": (
                    manifest.paths.project_name
                ),
                "com.docker.compose.service": (
                    f"{manifest.role}_restore_tool"
                ),
                "com.docker.compose.oneoff": "True",
                "com.docker.compose.config-hash": config_hash,
            },
            "User": "",
            "WorkingDir": "",
            "StopSignal": "SIGINT",
        },
        "HostConfig": {
            "RestartPolicy": {
                "Name": "no",
                "MaximumRetryCount": 0,
            },
            "CgroupParent": MODULE._compose_environment(manifest)[1][
                "PRODUCTION_SHADOW_CGROUP_PARENT"
            ],
            "NanoCpus": 2_000_000_000,
            "Memory": 2 * 1024**3,
            "MemoryReservation": 0,
            "MemorySwap": 0,
            "PidsLimit": 512,
            "CpuShares": 0,
            "CpuPeriod": 0,
            "CpuQuota": 0,
            "CpusetCpus": "",
            "CpusetMems": "",
            "AutoRemove": True,
            "Privileged": False,
            "ReadonlyRootfs": False,
            "PublishAllPorts": False,
            "PortBindings": {},
            "CapAdd": None,
            "CapDrop": None,
            "SecurityOpt": None,
            "Devices": [],
            "DeviceRequests": None,
            "PidMode": "",
            "IpcMode": "private",
            "UTSMode": "",
            "UsernsMode": "",
            "Links": None,
            "ExtraHosts": None,
            "Dns": [],
            "DnsOptions": [],
            "DnsSearch": [],
            "GroupAdd": None,
            "Sysctls": None,
            "Tmpfs": None,
            "NetworkMode": network_name,
            "Binds": [
                f"{manifest.paths.restore_input_root}:"
                "/run/restore-input:ro",
                f"{manifest.paths.uploads}:"
                "/run/restore-target/uploads:rw",
                f"{manifest.paths.audit}:"
                "/run/restore-target/audit:rw",
            ],
            "LogConfig": {
                "Type": "json-file",
                "Config": {"max-file": "5", "max-size": "20m"},
            },
        },
        "NetworkSettings": {"Networks": {network_name: {}}},
        "Mounts": [
            {
                "Type": "bind",
                "Source": str(manifest.paths.restore_input_root),
                "Destination": "/run/restore-input",
                "RW": False,
            },
            {
                "Type": "bind",
                "Source": str(manifest.paths.uploads),
                "Destination": "/run/restore-target/uploads",
                "RW": True,
            },
            {
                "Type": "bind",
                "Source": str(manifest.paths.audit),
                "Destination": "/run/restore-target/audit",
                "RW": True,
            },
            {
                "Type": "volume",
                "Name": "8" * 64,
                "Destination": "/var/lib/postgresql/data",
                "RW": True,
            },
        ],
        "State": {"Running": False, "Status": "exited"},
    }


def network_inspect_row(
    fixture: Fixture,
    *,
    database_row: dict | None,
) -> dict:
    manifest = fixture.manifest
    network_name = f"{manifest.paths.project_name}_{manifest.role}"
    containers = {}
    if database_row is not None:
        endpoint = database_row["NetworkSettings"]["Networks"][network_name]
        if endpoint["EndpointID"]:
            containers[database_row["Id"]] = {
                "Name": database_row["Name"].removeprefix("/"),
                "EndpointID": endpoint["EndpointID"],
                "MacAddress": endpoint["MacAddress"],
                "IPv4Address": (
                    f"{endpoint['IPAddress']}/{endpoint['IPPrefixLen']}"
                ),
                "IPv6Address": "",
            }
    return {
        "Id": NETWORK_ID,
        "Name": network_name,
        "Driver": "bridge",
        "Scope": "local",
        "Internal": True,
        "Attachable": False,
        "Ingress": False,
        "ConfigOnly": False,
        "EnableIPv4": True,
        "EnableIPv6": False,
        "Options": {},
        "ConfigFrom": {"Network": ""},
        "Labels": {
            "trading-bot.production.operation-id": OPERATION_ID,
            "com.docker.compose.network": manifest.role,
            "com.docker.compose.project": manifest.paths.project_name,
            "com.docker.compose.version": COMPOSE_VERSION,
        },
        "IPAM": {
            "Driver": "default",
            "Options": None,
            "Config": [
                {
                    "Subnet": "172.30.0.0/16",
                    "Gateway": "172.30.0.1",
                }
            ],
        },
        "Containers": containers,
    }


class RuntimeDocker:
    def __init__(
        self,
        fixture: Fixture,
        *,
        container_present: bool,
        network_present: bool,
        post_up_mutation=None,
    ) -> None:
        self.fixture = fixture
        self.container_present = container_present
        self.network_present = network_present
        self.post_up_mutation = post_up_mutation
        self.up_calls = 0
        self.rm_calls = 0

    def __call__(self, args, _env, _stdin):
        if args[1] == "ps":
            return "1" * 64 if self.container_present else ""
        if args[1:3] == ["network", "ls"]:
            return NETWORK_ID if self.network_present else ""
        if args[1:3] == ["volume", "ls"]:
            return ""
        if args[1:3] == ["network", "inspect"]:
            row = (
                database_container_row(self.fixture)
                if self.container_present
                else None
            )
            if row is not None and self.post_up_mutation is not None:
                self.post_up_mutation(row)
            return json.dumps(
                [network_inspect_row(self.fixture, database_row=row)]
            )
        if args[1:3] == ["image", "inspect"]:
            if "--format" in args:
                return POSTGRES_IMAGE_ID
            return json.dumps([image_inspect_document()])
        if args[1] == "inspect":
            row = database_container_row(self.fixture)
            if self.post_up_mutation is not None:
                self.post_up_mutation(row)
            return json.dumps([row])
        if args[1:3] == ["compose", "version"]:
            return COMPOSE_VERSION
        if "config" in args:
            if "--hash" in args:
                service = args[-1]
                digest = (
                    DATABASE_CONFIG_HASH
                    if service.endswith("_db")
                    else RESTORE_TOOL_CONFIG_HASH
                )
                return f"{service} {digest}\n"
            return json.dumps(rendered_config(self.fixture))
        if "up" in args:
            self.up_calls += 1
            self.container_present = True
            self.network_present = True
            return ""
        if args[1] == "rm":
            self.rm_calls += 1
            return ""
        return ""


def source_row(seed: str) -> dict:
    def value(offset: int) -> str:
        return format(
            ((int(seed, 16) - 1 + offset) % 15) + 1,
            "x",
        )

    artifacts = {
        "database-backup": {
            "sha256": seed * 64,
            "bytes": 10,
            "restored_tree_sha256": None,
        },
        "uploads-archive": {
            "sha256": value(1) * 64,
            "bytes": 11,
            "restored_tree_sha256": value(2) * 64,
        },
        "audit-archive": {
            "sha256": value(3) * 64,
            "bytes": 12,
            "restored_tree_sha256": value(4) * 64,
        },
    }
    database = {
        "alembic_revision": "source_1",
        "fingerprint_algorithm": (
            "pg-copy-jsonl-sha256-canonical-session-v1"
        ),
        "database_fingerprint_sha256": value(5) * 64,
        "row_count": 12,
        "table_count": 3,
    }
    restore_input = {
        "source_snapshot_manifest_sha256": value(6) * 64,
        "source_snapshot_binding_sha256": value(7) * 64,
        "freeze_evidence_sha256": value(8) * 64,
        "live_lease_claim_sha256": "a" * 64,
        "source_identity_sha256": value(9) * 64,
        "artifacts": artifacts,
        "source_database": database,
    }
    return {
        **restore_input,
        "restore_input_sha256": hashlib.sha256(
            canonical(restore_input)
        ).hexdigest(),
        "freeze_generation_sha256": "b" * 64,
        "source_container_ids": {"database": "1" * 64},
        "restore_drill_sha256": "c" * 64,
        "redis_rollback_metadata_sha256": "d" * 64,
        "redis_restore_included": False,
    }


def restore_set_document() -> dict:
    sources = {
        "bot_fi": source_row("1"),
        "webapp_fi": source_row("5"),
    }
    nginx = {
        "state": "legacy-frozen",
        "aggregate_sha256": "1" * 64,
        "state_receipt_sha256": "2" * 64,
        "global_generation_sha256": "b" * 64,
        "role_generation_sha256": {
            "bot_fi": "3" * 64,
            "webapp_fi": "4" * 64,
        },
        "role_bindings": {
            "bot_fi": {"host": "bot"},
            "webapp_fi": {"host": "web"},
        },
        "journal_sha256": "5" * 64,
        "journal_sequence": 4,
        "journal_tail_sha256": "6" * 64,
        "external_readback_sha256": "7" * 64,
    }
    claim = {
        key: None
        for key in MODULE.RESTORE_SET.SNAPSHOT_AUTHORIZATION_CLAIM_OUTPUT_FIELDS
    }
    claim.update(
        {
            "claim_sha256": "a" * 64,
            "claim_epoch": 1,
            "previous_claim_sha256": "0" * 64,
            "nonce": "8" * 64,
            "owner_action": "capture-frozen-final-snapshots",
            "claim_document_status": "active",
            "controller_lock_path_at_issue": "/controller.lock",
            "legacy_frozen_receipt_sha256": "2" * 64,
            "receipt_journal_sha256": "5" * 64,
            "receipt_journal_sequence": 4,
            "receipt_journal_tail_sha256": "6" * 64,
            "controller_journal_event_count": 4,
            "claim_declared_controller_authoritative_at_issue": True,
            "copied_material_authoritative": False,
            "automatic_expiry_allowed": False,
            "reconciliation_required_after_crash": True,
            "claim_liveness_asserted": False,
            "future_install_or_restore_authority_implied": False,
            "fresh_live_authority_required_before_install_or_restore": True,
        }
    )
    transport = {
        key: "9" * 64
        for key in MODULE.RESTORE_SET.IR_TRANSPORT_OUTPUT_FIELDS
    }
    transport.update(
        {
            "provider": "arvan-s3",
            "bucket": "bucket",
            "private": True,
            "versioned": True,
            "encryption": "age",
            "recipient": "age1" + "q" * 58,
            "plaintext_restore_input_set_sha256": sources["webapp_fi"][
                "restore_input_sha256"
            ],
            "ciphertext_bytes": 10,
            "object_key": "key",
            "version_id": "version",
            "exact_version_readback_verified": True,
        }
    )
    target_map = json.loads(canonical(MODULE.RESTORE_SET.TARGET_MAP))
    postgres_set = {
        target: {
            "source_role": row["source_role"],
            "artifact": sources[row["source_role"]]["artifacts"][
                "database-backup"
            ],
            "source_database": sources[row["source_role"]][
                "source_database"
            ],
        }
        for target, row in target_map.items()
    }
    file_set = {
        target: {
            "source_role": row["source_role"],
            "uploads-archive": sources[row["source_role"]]["artifacts"][
                "uploads-archive"
            ],
            "audit-archive": sources[row["source_role"]]["artifacts"][
                "audit-archive"
            ],
        }
        for target, row in target_map.items()
    }
    constraints = {
        key: False for key in MODULE.RESTORE_SET.CONSTRAINT_FIELDS
    }
    constraints.update(
        {
            "plan_only_default": True,
            "legacy_redis_restore_included": False,
            "snapshot_authorization_claim_copy_is_not_live_authority": True,
            "snapshot_authorization_claim_liveness_asserted": False,
            "future_install_or_restore_authority_implied": False,
            "fresh_live_authority_required_before_install_or_restore": True,
        }
    )
    basis = {
        "schema": "production-shadow-frozen-final-restore-generation-v1",
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": RELEASE_TREE_SHA,
        "controller_manifest_sha256": CONTROLLER_SHA256,
        "approval_sha256": "3" * 64,
        "target_map": target_map,
        "sources": sources,
        "nginx_freeze": nginx,
        "snapshot_authorization_claim": claim,
        "webapp_ir_transport": transport,
    }
    return {
        "schema": MODULE.RESTORE_SET.SCHEMA,
        "status": "sealed",
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": RELEASE_TREE_SHA,
        "legacy_release_sha": "9" * 40,
        "controller_manifest_sha256": CONTROLLER_SHA256,
        "approval_sha256": "3" * 64,
        "approval_policy_sha256": "4" * 64,
        "restore_generation_sha256": hashlib.sha256(
            canonical(basis)
        ).hexdigest(),
        "target_map": target_map,
        "sources": sources,
        "postgres_snapshot_set_sha256": hashlib.sha256(
            canonical(postgres_set)
        ).hexdigest(),
        "reviewed_file_snapshot_set_sha256": hashlib.sha256(
            canonical(file_set)
        ).hexdigest(),
        "nginx_freeze": nginx,
        "snapshot_authorization_claim": claim,
        "webapp_ir_transport": transport,
        "constraints": constraints,
    }


class FrozenFinalRestoreWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = Fixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.fixture.close()
        self.temporary.cleanup()

    def test_generation_paths_never_double_append_role(self):
        paths = self.fixture.paths
        self.assertEqual(
            paths.data_generation_root,
            Path(self.temporary.name)
            / "data"
            / OPERATION_ID
            / "frozen-final-generations"
            / GENERATION,
        )
        self.assertEqual(
            paths.postgres,
            paths.data_generation_root / "bot-fi" / "postgres",
        )
        self.assertEqual(
            paths.restore_input_root,
            paths.data_generation_root / "restore-input" / "bot-fi",
        )
        self.assertNotIn("/bot-fi/bot-fi/", str(paths.postgres))
        other = MODULE.runtime_paths(
            OPERATION_ID,
            RELEASE_SHA,
            "1" * 64,
            "bot_fi",
        )
        self.assertNotEqual(paths.project_name, other.project_name)
        self.assertNotEqual(paths.data_generation_root, other.data_generation_root)

    def test_project_identity_retains_192_bit_digest(self):
        base, project = MODULE._project_identity(
            OPERATION_ID,
            GENERATION,
            "webapp_ir",
        )
        self.assertRegex(base, r"^tb3f-[0-9a-f]{48}$")
        self.assertEqual(project, f"{base}-webapp-ir")

    def test_release_worker_imports_with_repo_dependencies(self):
        result = subprocess.run(
            [sys.executable, str(MODULE.RUNNING_WORKER_PATH), "--help"],
            cwd="/tmp",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            result.stderr.decode("utf-8", errors="replace"),
        )
        self.assertIn(b"--role-manifest", result.stdout)

    def test_worker_path_must_be_exact_tracked_immutable_release_file(self):
        tracked = (
            "100644 "
            + "1" * 40
            + " 0\tscripts/"
            "production_shadow_frozen_final_restore_worker.py"
        )
        with (
            mock.patch.object(
                MODULE,
                "_run_readonly",
                side_effect=[
                    RELEASE_SHA,
                    RELEASE_TREE_SHA,
                    "",
                    "",
                    "",
                    "",
                    tracked,
                ],
            ),
            mock.patch.object(
                MODULE,
                "_verify_release_file",
            ) as verify,
        ):
            MODULE._verify_immutable_release(
                release_root=self.fixture.paths.release_root,
                release_sha=RELEASE_SHA,
                release_tree_sha=RELEASE_TREE_SHA,
                worker_path=self.fixture.worker,
                worker_sha256="3" * 64,
            )
        verify.assert_called_once_with(
            self.fixture.worker,
            expected_sha256="3" * 64,
        )
        copied = (
            self.fixture.paths.secret_generation_root
            / self.fixture.worker.name
        )
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "path differs",
        ):
            MODULE._verify_immutable_release(
                release_root=self.fixture.paths.release_root,
                release_sha=RELEASE_SHA,
                release_tree_sha=RELEASE_TREE_SHA,
                worker_path=copied,
                worker_sha256="3" * 64,
            )

    def test_compose_is_exact_restore_only_and_overrides_rehearsal(self):
        config = rendered_config(self.fixture)

        def callback(args, env, _stdin):
            if "config" in args:
                return json.dumps(config)
            if args[1:3] == ["image", "inspect"]:
                return POSTGRES_IMAGE_ID
            return ""

        runner = FakeRunner(callback)
        evidence = MODULE._verify_role_compose(
            self.fixture.manifest,
            runner,
        )
        self.assertEqual(
            evidence["data_generation_root"],
            str(self.fixture.paths.data_generation_root),
        )
        config_call = next(call for call in runner.calls if "config" in call[0])
        env = config_call[1]
        self.assertEqual(
            env["PRODUCTION_SHADOW_DATA_ROOT"],
            str(self.fixture.paths.data_generation_root),
        )
        self.assertEqual(
            env["PRODUCTION_SHADOW_PROJECT"],
            self.fixture.paths.project_base,
        )
        self.assertNotEqual(
            env["PRODUCTION_SHADOW_DATA_ROOT"],
            str(Path(self.temporary.name) / "rehearsal-data"),
        )

    def test_compose_rejects_rehearsal_bind_after_override(self):
        config = rendered_config(self.fixture)
        config["services"]["bot_fi_db"]["volumes"][0]["source"] = str(
            Path(self.temporary.name) / "rehearsal-data" / "bot-fi" / "postgres"
        )
        runner = FakeRunner(
            lambda args, _env, _stdin: (
                json.dumps(config) if "config" in args else ""
            )
        )
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "escaped",
        ):
            MODULE._verify_role_compose(self.fixture.manifest, runner)

    def test_compose_rejects_any_app_service(self):
        payload = yaml.safe_load(self.fixture.role_compose.read_text())
        payload["services"]["bot_fi_api"] = {
            "image": POSTGRES_IMAGE_ID,
            "profiles": ["bot-fi-public"],
        }
        write_root_file(
            self.fixture.role_compose,
            yaml.safe_dump(payload, sort_keys=True).encode(),
        )
        self.fixture.document["role_compose_sha256"] = hashlib.sha256(
            self.fixture.role_compose.read_bytes()
        ).hexdigest()
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "restore-only",
        ):
            MODULE._verify_role_compose(
                self.fixture.manifest,
                FakeRunner(),
            )

    def test_role_manifest_fields_fail_closed_before_side_effects(self):
        path = self.fixture.paths.secret_generation_root / "bad.json"
        document = {
            "schema": MODULE.ROLE_MANIFEST_SCHEMA,
            "status": "installed",
            "role": "bot_fi",
            "unexpected": True,
        }
        write_root_file(path, canonical(document))
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "fields",
        ):
            MODULE.load_role_manifest(path)

    def test_restore_set_recomputes_generation_and_restore_input_closure(self):
        document = restore_set_document()
        payload = canonical(document)
        digest = hashlib.sha256(payload).hexdigest()
        path = (
            Path(self.temporary.name)
            / digest
            / MODULE.RESTORE_SET.OUTPUT_FILENAME
        )
        write_root_file(path, payload)
        loaded, observed = MODULE.load_restore_set(path)
        self.assertEqual(observed, digest)
        self.assertEqual(
            loaded["restore_generation_sha256"],
            document["restore_generation_sha256"],
        )

        document["sources"]["bot_fi"]["artifacts"][
            "database-backup"
        ]["bytes"] += 1
        tampered = canonical(document)
        tampered_digest = hashlib.sha256(tampered).hexdigest()
        tampered_path = (
            Path(self.temporary.name)
            / tampered_digest
            / MODULE.RESTORE_SET.OUTPUT_FILENAME
        )
        write_root_file(tampered_path, tampered)
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "restore-input digest differs",
        ):
            MODULE.load_restore_set(tampered_path)

    def test_installed_restore_set_skips_only_publication_namespace_check(self):
        document = restore_set_document()
        payload = canonical(document)
        digest = hashlib.sha256(payload).hexdigest()
        installed = (
            self.fixture.paths.secret_generation_root
            / "frozen-final-restore-set.json"
        )
        write_root_file(installed, payload)
        loaded, observed = MODULE.load_restore_set(
            installed,
            require_publication_namespace=False,
        )
        self.assertEqual(loaded, document)
        self.assertEqual(observed, digest)
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "digest namespace",
        ):
            MODULE.load_restore_set(installed)

        document["sources"]["webapp_fi"]["source_identity_sha256"] = "f" * 64
        write_root_file(installed, canonical(document))
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "restore-input digest differs",
        ):
            MODULE.load_restore_set(
                installed,
                require_publication_namespace=False,
            )

    def test_restore_set_rejects_legacy_writer_claim_as_snapshot_authority(self):
        document = restore_set_document()
        document["snapshot_authorization_claim"][
            "owner_action"
        ] = "restore-legacy-writers"
        payload = canonical(document)
        digest = hashlib.sha256(payload).hexdigest()
        path = (
            Path(self.temporary.name)
            / digest
            / MODULE.RESTORE_SET.OUTPUT_FILENAME
        )
        write_root_file(path, payload)
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "provenance contract",
        ):
            MODULE.load_restore_set(path)

    def test_live_lease_requires_dedicated_owner_action_and_canonical_paths(self):
        restore_set = restore_set_document()
        receipt_payload = canonical({"state": "legacy-frozen"})
        receipt_sha256 = hashlib.sha256(receipt_payload).hexdigest()
        restore_set["nginx_freeze"][
            "state_receipt_sha256"
        ] = receipt_sha256
        controller_root = (
            Path(self.temporary.name)
            / "secret"
            / OPERATION_ID
            / "nginx-coordinator"
        )
        receipt_path = (
            controller_root
            / "receipts"
            / f"legacy-frozen-{receipt_sha256}.json"
        )
        write_root_file(receipt_path, receipt_payload)
        claim = {
            key: None for key in MODULE.RESTORE_SET.LIVE_LEASE_FIELDS
        }
        claim.update(
            {
                "schema": MODULE.LIVE_LEASE_CLAIM_SCHEMA,
                "status": "active",
                "owner_action": MODULE.LIVE_LEASE_OWNER_ACTION,
                "operation_id": OPERATION_ID,
                "release_sha": RELEASE_SHA,
                "release_tree_sha": RELEASE_TREE_SHA,
                "aggregate_sha256": restore_set["nginx_freeze"][
                    "aggregate_sha256"
                ],
                "claim_epoch": 2,
                "previous_claim_sha256": "a" * 64,
                "nonce": "f" * 64,
                "controller_pid": 123,
                "controller_lock_path": str(
                    controller_root / "coordinator.lock"
                ),
                "controller_authoritative": True,
                "remote_copy_authoritative": False,
                "automatic_expiry_allowed": False,
                "reconciliation_required_after_crash": True,
                "legacy_frozen_receipt_path": str(receipt_path),
                "legacy_frozen_receipt_sha256": receipt_sha256,
                "receipt_journal_sha256": restore_set["nginx_freeze"][
                    "journal_sha256"
                ],
                "receipt_journal_sequence": restore_set["nginx_freeze"][
                    "journal_sequence"
                ],
                "receipt_journal_tail_sha256": restore_set[
                    "nginx_freeze"
                ]["journal_tail_sha256"],
                "controller_journal_event_count": 9,
                "receipt_state": "legacy-frozen",
                "receipt_global_generation_sha256": restore_set[
                    "nginx_freeze"
                ]["global_generation_sha256"],
                "receipt_role_generation_sha256": restore_set[
                    "nginx_freeze"
                ]["role_generation_sha256"],
                "receipt_role_bindings": restore_set["nginx_freeze"][
                    "role_bindings"
                ],
                "receipt_readbacks": {},
            }
        )
        claim_payload = canonical(claim)
        claim_sha256 = hashlib.sha256(claim_payload).hexdigest()
        claim_path = (
            controller_root
            / "live-leases"
            / "claims"
            / f"{claim_sha256}.json"
        )
        write_root_file(claim_path, claim_payload)
        with mock.patch.object(
            MODULE,
            "load_restore_set",
            return_value=(restore_set, "1" * 64),
        ):
            lease = MODULE.load_live_lease(
                manifest=self.fixture.manifest,
                claim_path=claim_path,
                claim_sha256=claim_sha256,
                claim_epoch=2,
                receipt_path=receipt_path,
            )
        self.assertEqual(lease.epoch, 2)
        self.assertEqual(
            lease.document["owner_action"],
            MODULE.LIVE_LEASE_OWNER_ACTION,
        )

        claim["owner_action"] = "restore-legacy-writers"
        old_payload = canonical(claim)
        old_sha256 = hashlib.sha256(old_payload).hexdigest()
        old_path = claim_path.with_name(f"{old_sha256}.json")
        write_root_file(old_path, old_payload)
        with (
            mock.patch.object(
                MODULE,
                "load_restore_set",
                return_value=(restore_set, "1" * 64),
            ),
            self.assertRaisesRegex(
                MODULE.FrozenFinalRestoreWorkerError,
                "binding differs",
            ),
        ):
            MODULE.load_live_lease(
                manifest=self.fixture.manifest,
                claim_path=old_path,
                claim_sha256=old_sha256,
                claim_epoch=2,
                receipt_path=receipt_path,
            )

    def test_authority_verification_requires_lock_and_monotonic_sequence(self):
        def valid(_lease, boundary):
            return {
                "schema": MODULE.LIVE_AUTHORITY_SCHEMA,
                "status": "verified-live",
                "boundary": boundary,
                "claim_sha256": self.fixture.lease.sha256,
                "claim_epoch": self.fixture.lease.epoch,
                "claim_nonce": self.fixture.lease.nonce,
                "legacy_frozen_receipt_sha256": (
                    self.fixture.lease.receipt_sha256
                ),
                "controller_lock_held": True,
                "controller_authoritative": True,
                "verification_sequence": 2,
                "verification_nonce": "b" * 64,
            }

        result, digest, sequence = MODULE._authority_verification(
            valid,
            self.fixture.lease,
            "before:bot_fi:verify-inputs",
            previous_sequence=1,
        )
        self.assertEqual(sequence, 2)
        self.assertEqual(digest, hashlib.sha256(canonical(result)).hexdigest())

        def unlocked(lease, boundary):
            value = valid(lease, boundary)
            value["controller_lock_held"] = False
            return value

        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "differs",
        ):
            MODULE._authority_verification(
                unlocked,
                self.fixture.lease,
                "before:bot_fi:verify-inputs",
                previous_sequence=1,
            )
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "differs",
        ):
            MODULE._authority_verification(
                valid,
                self.fixture.lease,
                "before:bot_fi:verify-inputs",
                previous_sequence=2,
            )

    def test_static_claim_never_satisfies_apply(self):
        with (
            mock.patch.object(
                MODULE,
                "load_role_manifest",
                return_value=self.fixture.manifest,
            ),
            mock.patch.object(
                MODULE,
                "load_live_lease",
                return_value=self.fixture.lease,
            ),
            self.assertRaisesRegex(
                MODULE.FrozenFinalRestoreWorkerError,
                "live-authority verifier",
            ),
        ):
            MODULE.execute(
                role_manifest_path=Path("/ignored"),
                live_lease_claim_path=Path("/ignored-claim"),
                live_lease_claim_sha256=self.fixture.lease.sha256,
                live_lease_claim_epoch=2,
                legacy_frozen_receipt_path=Path("/ignored-receipt"),
                apply=True,
                confirm=MODULE.confirmation_phrase(
                    self.fixture.manifest,
                    self.fixture.lease,
                ),
                runner=FakeRunner(),
            )

    def test_failed_live_authority_does_not_create_journal_or_lock(self):
        def invalid(_lease, boundary):
            return {
                "schema": MODULE.LIVE_AUTHORITY_SCHEMA,
                "status": "verified-live",
                "boundary": boundary,
                "claim_sha256": self.fixture.lease.sha256,
                "claim_epoch": self.fixture.lease.epoch,
                "claim_nonce": self.fixture.lease.nonce,
                "legacy_frozen_receipt_sha256": (
                    self.fixture.lease.receipt_sha256
                ),
                "controller_lock_held": False,
                "controller_authoritative": True,
                "verification_sequence": 1,
                "verification_nonce": "b" * 64,
            }

        with (
            mock.patch.object(
                MODULE,
                "load_role_manifest",
                return_value=self.fixture.manifest,
            ),
            mock.patch.object(
                MODULE,
                "load_live_lease",
                return_value=self.fixture.lease,
            ),
            self.assertRaisesRegex(
                MODULE.FrozenFinalRestoreWorkerError,
                "differs",
            ),
        ):
            MODULE.execute(
                role_manifest_path=Path("/ignored"),
                live_lease_claim_path=Path("/ignored-claim"),
                live_lease_claim_sha256=self.fixture.lease.sha256,
                live_lease_claim_epoch=2,
                legacy_frozen_receipt_path=Path("/ignored-receipt"),
                apply=True,
                confirm=MODULE.confirmation_phrase(
                    self.fixture.manifest,
                    self.fixture.lease,
                ),
                runner=FakeRunner(),
                authority_verifier=invalid,
            )
        self.assertFalse(self.fixture.paths.journal.exists())
        self.assertFalse(self.fixture.paths.evidence.exists())
        self.assertFalse(self.fixture.paths.lock.exists())

    def test_plan_does_not_create_runtime_directories(self):
        journal = self.fixture.paths.journal
        with (
            mock.patch.object(
                MODULE,
                "load_role_manifest",
                return_value=self.fixture.manifest,
            ),
            mock.patch.object(
                MODULE,
                "load_live_lease",
                return_value=self.fixture.lease,
            ),
        ):
            result = MODULE.execute(
                role_manifest_path=Path("/ignored"),
                live_lease_claim_path=Path("/ignored-claim"),
                live_lease_claim_sha256=self.fixture.lease.sha256,
                live_lease_claim_epoch=2,
                legacy_frozen_receipt_path=Path("/ignored-receipt"),
            )
        self.assertEqual(result["status"], "planned")
        self.assertFalse(journal.exists())
        self.assertFalse(result["runtime_mutated"])

    def test_initialize_creates_only_generation_stores_and_pristine_redis(self):
        result = MODULE._initialize_generation(self.fixture.manifest)
        self.assertTrue(result["redis_pristine"])
        for path in (
            self.fixture.paths.postgres,
            self.fixture.paths.redis,
            self.fixture.paths.uploads,
            self.fixture.paths.audit,
        ):
            self.assertTrue(path.is_dir())
        (self.fixture.paths.redis / "dump.rdb").write_bytes(b"x")
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "Redis",
        ):
            MODULE._initialize_generation(self.fixture.manifest)

    def test_nonempty_database_is_never_adopted_on_fresh_action(self):
        self.fixture.initialize_stores()
        state = MODULE.DatabaseState(
            alembic_revision="source_1",
            database_fingerprint_sha256="7" * 64,
            row_count=12,
            table_count=3,
        )
        with (
            mock.patch.object(
                MODULE,
                "_start_database",
                return_value="1" * 64,
            ),
            mock.patch.object(MODULE, "_database_state", return_value=state),
            self.assertRaisesRegex(
                MODULE.FrozenFinalRestoreWorkerError,
                "never adopted",
            ),
        ):
            MODULE._restore_postgres(
                self.fixture.manifest,
                FakeRunner(),
                resumed=False,
            )

    def test_resume_accepts_only_exact_operation_restore(self):
        self.fixture.initialize_stores()
        exact = MODULE.DatabaseState(
            alembic_revision="source_1",
            database_fingerprint_sha256="7" * 64,
            row_count=12,
            table_count=3,
        )
        with (
            mock.patch.object(
                MODULE,
                "_start_database",
                return_value="1" * 64,
            ),
            mock.patch.object(MODULE, "_database_state", return_value=exact),
            mock.patch.object(MODULE, "_cleanup_oneoffs", return_value=[]),
        ):
            result = MODULE._restore_postgres(
                self.fixture.manifest,
                FakeRunner(),
                resumed=True,
            )
        self.assertTrue(result["restore_recovered_after_crash"])
        self.assertFalse(result["database_adopted"])

        mismatched = MODULE.DatabaseState(
            alembic_revision="source_1",
            database_fingerprint_sha256="8" * 64,
            row_count=12,
            table_count=3,
        )
        with (
            mock.patch.object(
                MODULE,
                "_start_database",
                return_value="1" * 64,
            ),
            mock.patch.object(
                MODULE,
                "_database_state",
                return_value=mismatched,
            ),
            self.assertRaisesRegex(
                MODULE.FrozenFinalRestoreWorkerError,
                "never adopted",
            ),
        ):
            MODULE._restore_postgres(
                self.fixture.manifest,
                FakeRunner(),
                resumed=True,
            )

    def test_postgres_restore_is_single_transaction_and_never_pulls(self):
        self.fixture.initialize_stores()
        empty = MODULE.DatabaseState(None, None, 0, 0)
        exact = MODULE.DatabaseState(
            "source_1",
            "7" * 64,
            12,
            3,
        )
        observed_command: list[str] = []

        def oneoff(_manifest, _runner, *, command, timeout, stdin):
            observed_command.extend(command)
            self.assertIsNot(stdin, MODULE.subprocess.DEVNULL)
            return ""

        with (
            mock.patch.object(
                MODULE,
                "_start_database",
                return_value="1" * 64,
            ),
            mock.patch.object(
                MODULE,
                "_database_state",
                side_effect=[empty, exact],
            ),
            mock.patch.object(MODULE, "_compose_oneoff", side_effect=oneoff),
            mock.patch.object(MODULE, "_cleanup_oneoffs", return_value=[]),
        ):
            result = MODULE._restore_postgres(
                self.fixture.manifest,
                FakeRunner(),
                resumed=False,
            )
        command_text = " ".join(observed_command)
        self.assertIn("--single-transaction", command_text)
        self.assertIn("$PGDATABASE", command_text)
        self.assertFalse(result["database_adopted"])

    def test_database_preflight_rejects_foreign_bind_before_up(self):
        self.fixture.initialize_stores()
        identifier = "1" * 64
        row = {
            "Id": identifier,
            "Image": POSTGRES_IMAGE_ID,
            "Config": {
                "Image": POSTGRES_IMAGE_ID,
                "Labels": {
                    "com.docker.compose.project": (
                        self.fixture.paths.project_name
                    ),
                    "com.docker.compose.service": "bot_fi_db",
                    "com.docker.compose.oneoff": "False",
                    "trading-bot.production.operation-id": OPERATION_ID,
                },
            },
            "HostConfig": {
                "Privileged": False,
                "PortBindings": {},
                "NetworkMode": (
                    f"{self.fixture.paths.project_name}_bot_fi"
                ),
            },
            "NetworkSettings": {
                "Networks": {
                    f"{self.fixture.paths.project_name}_bot_fi": {}
                }
            },
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": "/srv/legacy/postgres",
                    "Destination": "/var/lib/postgresql/data",
                    "RW": True,
                }
            ],
        }
        mutating = False

        def callback(args, _env, _stdin):
            nonlocal mutating
            if "up" in args:
                mutating = True
                self.fail("Compose up must not run after foreign preflight")
            if args[1] == "ps":
                return identifier
            if args[1] == "inspect":
                return json.dumps([row])
            return ""

        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "bind escaped",
        ):
            MODULE._start_database(
                self.fixture.manifest,
                FakeRunner(callback),
                resumed=False,
            )
        self.assertFalse(mutating)

    def test_database_up_is_no_recreate_no_build_no_pull(self):
        self.fixture.initialize_stores()
        runtime = RuntimeDocker(
            self.fixture,
            container_present=False,
            network_present=False,
        )
        runner = FakeRunner(runtime)
        MODULE._start_database(
            self.fixture.manifest,
            runner,
            resumed=False,
        )
        observed = next(
            args
            for args, _env in runner.calls
            if "up" in args
        )
        self.assertIn("--no-recreate", observed)
        self.assertIn("--no-build", observed)
        self.assertEqual(
            observed[observed.index("--pull") + 1],
            "never",
        )
        self.assertNotIn("--remove-orphans", observed)

    def test_fresh_database_rejects_pgdata_before_any_docker_mutation(self):
        self.fixture.initialize_stores()
        (self.fixture.paths.postgres / "PG_VERSION").write_text(
            "17\n",
            encoding="ascii",
        )
        runtime = RuntimeDocker(
            self.fixture,
            container_present=False,
            network_present=False,
        )
        runner = FakeRunner(runtime)
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "empty PostgreSQL",
        ):
            MODULE._start_database(
                self.fixture.manifest,
                runner,
                resumed=False,
            )
        self.assertEqual(runtime.up_calls, 0)
        self.assertEqual(runtime.rm_calls, 0)
        self.assertFalse(
            any("run" in args for args, _env in runner.calls)
        )

    def test_fresh_database_rejects_exact_existing_container_without_mutation(
        self,
    ):
        self.fixture.initialize_stores()
        runtime = RuntimeDocker(
            self.fixture,
            container_present=True,
            network_present=True,
        )
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "zero project residue",
        ):
            MODULE._start_database(
                self.fixture.manifest,
                FakeRunner(runtime),
                resumed=False,
            )
        self.assertEqual(runtime.up_calls, 0)
        self.assertEqual(runtime.rm_calls, 0)

    def test_fresh_database_rejects_oneoff_without_removing_it(self):
        self.fixture.initialize_stores()
        identifier = "7" * 64
        row = restore_oneoff_row(self.fixture, identifier=identifier)
        removed = False

        def callback(args, _env, _stdin):
            nonlocal removed
            if args[1] == "ps":
                return identifier
            if args[1] == "inspect":
                return json.dumps([row])
            if args[1:3] in (["network", "ls"], ["volume", "ls"]):
                return ""
            if args[1] == "rm":
                removed = True
                self.fail("fresh restore must not remove one-off residue")
            if "up" in args:
                self.fail("fresh restore must not run Compose up")
            return ""

        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "zero project residue",
        ):
            MODULE._start_database(
                self.fixture.manifest,
                FakeRunner(callback),
                resumed=False,
            )
        self.assertFalse(removed)

    def test_fresh_database_rejects_existing_exact_network_before_up(self):
        self.fixture.initialize_stores()
        runtime = RuntimeDocker(
            self.fixture,
            container_present=False,
            network_present=True,
        )
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "zero project residue",
        ):
            MODULE._start_database(
                self.fixture.manifest,
                FakeRunner(runtime),
                resumed=False,
            )
        self.assertEqual(runtime.up_calls, 0)

    def test_active_resume_accepts_only_exact_database_and_network(self):
        self.fixture.initialize_stores()
        runtime = RuntimeDocker(
            self.fixture,
            container_present=True,
            network_present=True,
        )
        identifier = MODULE._start_database(
            self.fixture.manifest,
            FakeRunner(runtime),
            resumed=True,
        )
        self.assertEqual(identifier, "1" * 64)
        self.assertEqual(runtime.up_calls, 1)
        self.assertEqual(runtime.rm_calls, 0)

    def test_database_runtime_contract_rejects_critical_drift(self):
        self.fixture.initialize_stores()
        runtime = RuntimeDocker(
            self.fixture,
            container_present=True,
            network_present=True,
        )
        contract = MODULE._database_runtime_contract(
            self.fixture.manifest,
            FakeRunner(runtime),
        )
        exact = database_container_row(self.fixture)
        MODULE._validate_database_runtime(
            exact,
            self.fixture.manifest,
            contract,
        )

        def command(row):
            row["Config"]["Cmd"][-1] = "log_timezone=Europe/Helsinki"

        def environment(row):
            row["Config"]["Env"][-1] = "POSTGRES_DB=foreign"

        def healthcheck(row):
            row["Config"]["Healthcheck"]["Interval"] += 1

        def user(row):
            row["Config"]["User"] = "65534"

        def cgroup(row):
            row["HostConfig"]["CgroupParent"] = "/foreign"

        def restart(row):
            row["HostConfig"]["RestartPolicy"]["Name"] = "always"

        def config_hash(row):
            row["Config"]["Labels"][
                "com.docker.compose.config-hash"
            ] = "9" * 64

        def resource(row):
            row["HostConfig"]["MemorySwap"] = 1

        def isolation(row):
            row["HostConfig"]["AutoRemove"] = True

        for label, mutate in (
            ("command", command),
            ("environment", environment),
            ("healthcheck", healthcheck),
            ("user", user),
            ("cgroup", cgroup),
            ("restart", restart),
            ("config-hash", config_hash),
            ("resource", resource),
            ("isolation", isolation),
        ):
            with self.subTest(label=label):
                row = copy.deepcopy(exact)
                mutate(row)
                with self.assertRaisesRegex(
                    MODULE.FrozenFinalRestoreWorkerError,
                    "immutable runtime config differs",
                ):
                    MODULE._validate_database_runtime(
                        row,
                        self.fixture.manifest,
                        contract,
                    )

    def test_post_up_runtime_drift_is_rejected_before_restore_query(self):
        self.fixture.initialize_stores()

        def mutate(row):
            row["Config"]["Labels"][
                "com.docker.compose.config-hash"
            ] = "9" * 64

        runtime = RuntimeDocker(
            self.fixture,
            container_present=False,
            network_present=False,
            post_up_mutation=mutate,
        )
        runner = FakeRunner(runtime)
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "immutable runtime config differs",
        ):
            MODULE._start_database(
                self.fixture.manifest,
                runner,
                resumed=False,
            )
        self.assertEqual(runtime.up_calls, 1)
        self.assertFalse(
            any("run" in args for args, _env in runner.calls)
        )

    def test_network_runtime_contract_rejects_drift_and_foreign_endpoint(self):
        self.fixture.initialize_stores()
        runtime = RuntimeDocker(
            self.fixture,
            container_present=True,
            network_present=True,
        )
        contract = MODULE._network_runtime_contract(
            self.fixture.manifest,
            FakeRunner(runtime),
        )
        database = database_container_row(self.fixture)
        containers = {database["Id"]: database}
        exact = network_inspect_row(
            self.fixture,
            database_row=database,
        )
        MODULE._validate_network_runtime(exact, contract, containers)
        compatible = copy.deepcopy(exact)
        compatible.pop("EnableIPv4")
        MODULE._validate_network_runtime(
            compatible,
            contract,
            containers,
        )

        def driver(row):
            row["Driver"] = "macvlan"

        def options(row):
            row["Options"] = {"foreign": "true"}

        def ipam(row):
            row["IPAM"]["Config"][0]["Gateway"] = "10.0.0.1"

        def label(row):
            row["Labels"]["foreign"] = "true"

        def endpoint(row):
            row["Containers"]["9" * 64] = {
                "Name": "foreign",
                "EndpointID": "a" * 64,
                "MacAddress": "02:42:ac:1e:00:09",
                "IPv4Address": "172.30.0.9/16",
                "IPv6Address": "",
            }

        for name, mutate in (
            ("driver", driver),
            ("options", options),
            ("ipam", ipam),
            ("label", label),
            ("foreign-endpoint", endpoint),
        ):
            with self.subTest(name=name):
                row = copy.deepcopy(exact)
                mutate(row)
                with self.assertRaises(
                    MODULE.FrozenFinalRestoreWorkerError
                ):
                    MODULE._validate_network_runtime(
                        row,
                        contract,
                        containers,
                    )

    def test_oneoff_cleanup_requires_exact_restore_tool_config_hash(self):
        self.fixture.initialize_stores()

        def run_cleanup(row):
            present = True
            base = RuntimeDocker(
                self.fixture,
                container_present=False,
                network_present=False,
            )

            def callback(args, env, stdin):
                nonlocal present
                if args[1] == "ps":
                    return row["Id"] if present else ""
                if args[1] == "inspect":
                    return json.dumps([row])
                if args[1] == "rm":
                    present = False
                    return ""
                return base(args, env, stdin)

            runner = FakeRunner(callback)
            return MODULE._cleanup_oneoffs(
                self.fixture.manifest,
                runner,
            ), base, runner

        exact = restore_oneoff_row(self.fixture)
        removed, _base, runner = run_cleanup(exact)
        self.assertEqual(removed[0]["container_id"], exact["Id"])
        self.assertTrue(
            any(args[1] == "rm" for args, _env in runner.calls)
        )

        drifted = restore_oneoff_row(
            self.fixture,
            config_hash="9" * 64,
        )
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "immutable runtime config differs",
        ):
            run_cleanup(drifted)

    def test_intermediate_symlink_is_rejected_before_docker_mutation(self):
        self.fixture.initialize_stores()
        original = self.fixture.paths.role_data_root.with_name(
            "bot-fi-original"
        )
        self.fixture.paths.role_data_root.rename(original)
        legacy = Path(self.temporary.name) / "legacy"
        legacy.mkdir(mode=0o700)
        self.fixture.paths.role_data_root.symlink_to(
            legacy,
            target_is_directory=True,
        )
        called = False

        def callback(_args, _env, _stdin):
            nonlocal called
            called = True
            return ""

        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "ancestry",
        ):
            MODULE._start_database(
                self.fixture.manifest,
                FakeRunner(callback),
                resumed=False,
            )
        self.assertFalse(called)

    def test_directory_creation_fsyncs_its_parent(self):
        original_fsync = os.fsync
        with mock.patch.object(
            MODULE.os,
            "fsync",
            side_effect=original_fsync,
        ) as fsync:
            MODULE._ensure_private_directory(
                self.fixture.paths.journal,
                create=True,
            )
        self.assertGreaterEqual(fsync.call_count, 1)

    def test_resumable_archive_verifies_existing_members(self):
        archive = self.fixture.paths.restore_input_root / "resume.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            directory = tarfile.TarInfo("nested")
            directory.type = tarfile.DIRTYPE
            directory.uid = directory.gid = 0
            directory.mtime = 0
            directory.mode = 0o755
            output.addfile(directory)
            payload = b"exact-content"
            member = tarfile.TarInfo("nested/value.txt")
            member.uid = member.gid = 0
            member.mtime = 0
            member.mode = 0o644
            member.size = len(payload)
            output.addfile(member, io.BytesIO(payload))
        archive.chmod(0o600)
        binding = MODULE.ArtifactBinding(
            path=archive,
            sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            bytes=archive.stat().st_size,
            restored_tree_sha256="1" * 64,
        )
        target = self.fixture.paths.data_generation_root / "resume-target"
        target.mkdir(mode=0o700)
        (target / "nested").mkdir(mode=0o755)
        existing = target / "nested" / "value.txt"
        existing.write_bytes(b"exact-content")
        existing.chmod(0o644)
        result = MODULE._restore_archive_resumable(binding, target)
        self.assertTrue(result["resume_safe_member_verification"])
        existing.write_bytes(b"tampered")
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "partial restore file",
        ):
            MODULE._restore_archive_resumable(binding, target)

    def test_archive_resume_continues_operation_owned_partial_without_buffering(self):
        archive = self.fixture.paths.restore_input_root / "partial.tar.gz"
        payload = (b"0123456789abcdef" * (256 * 1024)) + b"tail"
        with tarfile.open(archive, "w:gz") as output:
            member = tarfile.TarInfo("large.bin")
            member.uid = member.gid = 0
            member.mtime = 0
            member.mode = 0o640
            member.size = len(payload)
            output.addfile(member, io.BytesIO(payload))
        archive.chmod(0o600)
        binding = MODULE.ArtifactBinding(
            path=archive,
            sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            bytes=archive.stat().st_size,
            restored_tree_sha256="1" * 64,
        )
        target = self.fixture.paths.data_generation_root / "partial-target"
        target.mkdir(mode=0o700)
        partial = target / "large.bin"
        partial.write_bytes(payload[: 1024 * 1024 + 17])
        partial.chmod(0o600)
        MODULE._restore_archive_resumable(binding, target)
        self.assertEqual(partial.read_bytes(), payload)
        self.assertEqual(partial.stat().st_mode & 0o777, 0o640)

    def test_archive_rejects_noncanonical_aliases(self):
        for value in ("./value", "nested//value", "nested/../value"):
            with self.subTest(value=value), self.assertRaisesRegex(
                MODULE.FrozenFinalRestoreWorkerError,
                "unsafe path",
            ):
                MODULE._safe_member_path(value)

    def test_archive_fd_detects_path_replacement(self):
        binding = self.fixture.manifest.artifacts["database-backup"]
        replacement = binding.path.with_name("replacement.dump")
        write_root_file(replacement, b"different")
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "changed while being consumed",
        ):
            with MODULE._held_artifact(binding):
                original = binding.path.with_name("original-held.dump")
                binding.path.rename(original)
                replacement.rename(binding.path)

    def test_fresh_file_restore_never_adopts_nonempty_target(self):
        self.fixture.initialize_stores()
        (self.fixture.paths.uploads / "foreign").write_bytes(b"x")
        with (
            mock.patch.object(
                MODULE,
                "_restore_archive_resumable",
            ) as restore,
            self.assertRaisesRegex(
                MODULE.FrozenFinalRestoreWorkerError,
                "nonempty",
            ),
        ):
            MODULE._restore_files(
                self.fixture.manifest,
                resumed=False,
            )
        restore.assert_not_called()

    def test_restore_files_rejects_tree_digest_mismatch_and_redis(self):
        self.fixture.initialize_stores()
        with (
            mock.patch.object(
                MODULE,
                "_restore_archive_resumable",
                return_value={"member_count": 1},
            ),
            mock.patch.object(MODULE, "_tree_digest", return_value="0" * 64),
            self.assertRaisesRegex(
                MODULE.FrozenFinalRestoreWorkerError,
                "tree differs",
            ),
        ):
            MODULE._restore_files(self.fixture.manifest, resumed=True)

    def test_restore_files_fsyncs_all_final_directories_before_return(self):
        self.fixture.initialize_stores()
        trees = iter(["4" * 64, "5" * 64])
        with (
            mock.patch.object(
                MODULE,
                "_restore_archive_resumable",
                return_value={"member_count": 1},
            ),
            mock.patch.object(
                MODULE,
                "_tree_digest",
                side_effect=lambda _path: next(trees),
            ),
            mock.patch.object(
                MODULE,
                "_fsync_tree_directories",
            ) as fsync,
        ):
            result = MODULE._restore_files(
                self.fixture.manifest,
                resumed=True,
            )
        self.assertTrue(result["redis_pristine"])
        self.assertEqual(
            fsync.call_args_list,
            [
                mock.call(self.fixture.paths.uploads),
                mock.call(self.fixture.paths.audit),
                mock.call(self.fixture.paths.uploads),
                mock.call(self.fixture.paths.audit),
                mock.call(self.fixture.paths.redis),
            ],
        )

    def test_cleanup_refuses_foreign_container_before_rm(self):
        identifier = "1" * 64
        row = {
            "Id": identifier,
            "Image": POSTGRES_IMAGE_ID,
            "Config": {
                "Image": POSTGRES_IMAGE_ID,
                "Labels": {
                    "com.docker.compose.project": "foreign",
                    "com.docker.compose.service": "bot_fi_restore_tool",
                    "com.docker.compose.oneoff": "True",
                    "trading-bot.production.operation-id": OPERATION_ID,
                },
            },
            "HostConfig": {
                "Privileged": False,
                "PortBindings": {},
                "NetworkMode": (
                    f"{self.fixture.paths.project_name}_bot_fi"
                ),
            },
            "NetworkSettings": {
                "Networks": {
                    f"{self.fixture.paths.project_name}_bot_fi": {}
                }
            },
            "Mounts": [],
        }

        def callback(args, _env, _stdin):
            if args[1] == "ps":
                return identifier
            if args[1] == "inspect":
                return json.dumps([row])
            self.fail("foreign container must not be removed")

        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "escaped",
        ):
            MODULE._cleanup_oneoffs(
                self.fixture.manifest,
                FakeRunner(callback),
            )

    def test_container_inventory_requires_no_trunc_and_full_ids(self):
        full_identifier = "a" * 64

        def compliant(args, _env, _stdin):
            if args[1] == "ps":
                return (
                    full_identifier
                    if "--no-trunc" in args
                    else "a" * 12
                )
            return ""

        runner = FakeRunner(compliant)
        self.assertEqual(
            MODULE._project_container_ids(
                self.fixture.manifest,
                runner,
            ),
            [full_identifier],
        )
        ps_call = next(args for args, _env in runner.calls if args[1] == "ps")
        self.assertIn("--no-trunc", ps_call)

        broken = FakeRunner(
            lambda args, _env, _stdin: (
                "b" * 12 if args[1] == "ps" else ""
            )
        )
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "inventory is invalid",
        ):
            MODULE._project_container_ids(
                self.fixture.manifest,
                broken,
            )

    def test_container_inspect_must_return_requested_full_id(self):
        requested = "a" * 64
        row = {"Id": "b" * 64}
        runner = FakeRunner(
            lambda args, _env, _stdin: (
                json.dumps([row]) if args[1] == "inspect" else ""
            )
        )
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "inspection is invalid",
        ):
            MODULE._inspect_container(
                requested,
                self.fixture.manifest,
                runner,
            )

    def test_append_only_journal_replays_crash_and_resume(self):
        self.fixture.paths.journal.mkdir(mode=0o700)
        events: list[dict] = []
        MODULE._append_event(
            self.fixture.manifest,
            self.fixture.lease,
            events,
            kind="started",
            action="verify-inputs",
            attempt=1,
            evidence_sha256=None,
            authority_verification_sha256="1" * 64,
        )
        loaded, completed, active, evidence = MODULE._read_events(
            self.fixture.manifest,
            self.fixture.lease,
        )
        self.assertEqual(active, "verify-inputs")
        self.assertEqual(completed, [])
        MODULE._append_event(
            self.fixture.manifest,
            self.fixture.lease,
            loaded,
            kind="resumed",
            action="verify-inputs",
            attempt=2,
            evidence_sha256=None,
            authority_verification_sha256="2" * 64,
        )
        MODULE._append_event(
            self.fixture.manifest,
            self.fixture.lease,
            loaded,
            kind="completed",
            action="verify-inputs",
            attempt=2,
            evidence_sha256="3" * 64,
            authority_verification_sha256=None,
        )
        _, completed, active, evidence = MODULE._read_events(
            self.fixture.manifest,
            self.fixture.lease,
        )
        self.assertEqual(completed, ["verify-inputs"])
        self.assertIsNone(active)
        self.assertEqual(evidence["verify-inputs"], "3" * 64)
        self.assertEqual(
            sorted(path.name for path in self.fixture.paths.journal.iterdir()),
            ["000001.json", "000002.json", "000003.json"],
        )

    def test_journal_is_bound_to_exact_live_claim(self):
        self.fixture.paths.journal.mkdir(mode=0o700)
        events: list[dict] = []
        MODULE._append_event(
            self.fixture.manifest,
            self.fixture.lease,
            events,
            kind="started",
            action="verify-inputs",
            attempt=1,
            evidence_sha256=None,
            authority_verification_sha256="1" * 64,
        )
        different = MODULE.LeaseBinding(
            document={},
            path=self.fixture.lease.path.with_name(f"{'b' * 64}.json"),
            sha256="b" * 64,
            epoch=3,
            nonce="c" * 64,
            receipt_path=self.fixture.lease.receipt_path,
            receipt_sha256=self.fixture.lease.receipt_sha256,
        )
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreWorkerError,
            "binding differs",
        ):
            MODULE._read_events(self.fixture.manifest, different)

    def test_evidence_and_result_bind_installer_claim_and_observed_state(self):
        final = {
            "database": {
                "alembic_revision": "source_1",
                "database_fingerprint_sha256": "7" * 64,
                "row_count": 12,
                "table_count": 3,
            },
            "file_trees": {
                "uploads": "4" * 64,
                "audit": "5" * 64,
            },
        }
        result = MODULE._result_document(
            self.fixture.manifest,
            self.fixture.lease,
            "6" * 64,
            final,
        )
        self.assertEqual(
            result["installer_receipt_sha256"],
            INSTALLER_SHA256,
        )
        self.assertEqual(
            result["live_lease_claim_nonce"],
            self.fixture.lease.nonce,
        )
        self.assertEqual(result["redis_restore_bytes"], 0)
        self.assertFalse(result["claim_consumed_by_worker"])
        self.assertEqual(
            result["claim_consume_outcome"],
            MODULE.LIVE_LEASE_SUCCESS_OUTCOME,
        )

    def test_completed_journal_selects_exact_evidence_and_tolerates_valid_orphan(self):
        self.fixture.paths.evidence.mkdir(mode=0o700)
        _first, journal_digest = MODULE._publish_evidence(
            self.fixture.manifest,
            self.fixture.lease,
            "verify-final",
            {"database": {}, "file_trees": {}, "attempt": 1},
        )
        _second, orphan_digest = MODULE._publish_evidence(
            self.fixture.manifest,
            self.fixture.lease,
            "verify-final",
            {"database": {}, "file_trees": {}, "attempt": 2},
        )
        self.assertNotEqual(journal_digest, orphan_digest)
        MODULE._validate_orphan_evidence(
            self.fixture.manifest,
            self.fixture.lease,
            action="verify-final",
            journal_digest=journal_digest,
        )
        selected = MODULE._load_action_evidence(
            self.fixture.manifest,
            self.fixture.lease,
            action="verify-final",
            digest=journal_digest,
        )
        self.assertEqual(selected["semantic"]["attempt"], 1)

    def test_execute_resumes_crash_at_exact_active_action(self):
        sequence = 0

        def authority(lease, boundary):
            nonlocal sequence
            sequence += 1
            return {
                "schema": MODULE.LIVE_AUTHORITY_SCHEMA,
                "status": "verified-live",
                "boundary": boundary,
                "claim_sha256": lease.sha256,
                "claim_epoch": lease.epoch,
                "claim_nonce": lease.nonce,
                "legacy_frozen_receipt_sha256": lease.receipt_sha256,
                "controller_lock_held": True,
                "controller_authoritative": True,
                "verification_sequence": sequence,
                "verification_nonce": f"{sequence:064x}",
            }

        call_count = 0

        def first_actions(action, *_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            if action == "initialize-generation":
                return MODULE._initialize_generation(
                    self.fixture.manifest
                )
            if action == "restore-postgres":
                raise MODULE.FrozenFinalRestoreWorkerError("injected crash")
            return {"action": action}

        confirm = MODULE.confirmation_phrase(
            self.fixture.manifest,
            self.fixture.lease,
        )
        patches = (
            mock.patch.object(
                MODULE,
                "load_role_manifest",
                return_value=self.fixture.manifest,
            ),
            mock.patch.object(
                MODULE,
                "load_live_lease",
                return_value=self.fixture.lease,
            ),
        )
        with patches[0], patches[1], mock.patch.object(
            MODULE,
            "_action_semantic",
            side_effect=first_actions,
        ):
            with self.assertRaisesRegex(
                MODULE.FrozenFinalRestoreWorkerError,
                "injected crash",
            ):
                MODULE.execute(
                    role_manifest_path=Path("/ignored"),
                    live_lease_claim_path=Path("/ignored-claim"),
                    live_lease_claim_sha256=self.fixture.lease.sha256,
                    live_lease_claim_epoch=2,
                    legacy_frozen_receipt_path=Path("/ignored-receipt"),
                    apply=True,
                    confirm=confirm,
                    runner=FakeRunner(),
                    authority_verifier=authority,
                )
        _, completed, active, _ = MODULE._read_events(
            self.fixture.manifest,
            self.fixture.lease,
        )
        self.assertEqual(
            completed,
            ["verify-inputs", "initialize-generation"],
        )
        self.assertEqual(active, "restore-postgres")

        sequence = 100

        def resumed_actions(action, *_args, **kwargs):
            if action == "restore-postgres":
                self.assertTrue(kwargs["resumed"])
            if action == "verify-final":
                return {
                    "database": {
                        "alembic_revision": "source_1",
                        "database_fingerprint_sha256": "7" * 64,
                        "row_count": 12,
                        "table_count": 3,
                    },
                    "file_trees": {
                        "uploads": "4" * 64,
                        "audit": "5" * 64,
                    },
                }
            return {"action": action}

        with (
            mock.patch.object(
                MODULE,
                "load_role_manifest",
                return_value=self.fixture.manifest,
            ),
            mock.patch.object(
                MODULE,
                "load_live_lease",
                return_value=self.fixture.lease,
            ),
            mock.patch.object(
                MODULE,
                "_action_semantic",
                side_effect=resumed_actions,
            ),
        ):
            result = MODULE.execute(
                role_manifest_path=Path("/ignored"),
                live_lease_claim_path=Path("/ignored-claim"),
                live_lease_claim_sha256=self.fixture.lease.sha256,
                live_lease_claim_epoch=2,
                legacy_frozen_receipt_path=Path("/ignored-receipt"),
                apply=True,
                confirm=confirm,
                runner=FakeRunner(),
                authority_verifier=authority,
            )
        self.assertEqual(result["status"], "restored")
        self.assertEqual(result["completed_actions"], list(MODULE.ACTIONS))
        kinds = [
            event["kind"]
            for event in MODULE._read_events(
                self.fixture.manifest,
                self.fixture.lease,
            )[0]
            if event["action"] == "restore-postgres"
        ]
        self.assertEqual(kinds, ["started", "resumed", "completed"])


if __name__ == "__main__":
    unittest.main()
