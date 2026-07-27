from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts import orchestrate_wa_ir_production_artifacts as ORCHESTRATOR
from scripts import wa_ir_production_operation as MODULE


OPERATION_ID = "12345678-1234-4234-8234-123456789abc"
SOURCE_REVISION = "f2c7d8e9a0b1"
INTERMEDIATE_REVISION = "a875b6c7d9e0"
TARGET_REVISION = "b986c7d8e0f1"
CONCURRENT_INDEX = "ix_fixture_resume"
TABLE_STREAM = b'{"id":1}\n{"id":2}\n'
TABLE_DIGEST = hashlib.sha256(TABLE_STREAM).hexdigest()
SEQUENCE_ROWS = ["users_id_seq|2"]
SEQUENCE_STREAM = b'{"sequence_name":"users_id_seq","last_value":2}\n'
FINGERPRINT = hashlib.sha256(
    json.dumps(
        {
            "algorithm": MODULE.DATABASE_FINGERPRINT_ALGORITHM,
            "session_settings": {
                **MODULE.DATABASE_FINGERPRINT_SESSION_SETTINGS,
                "client_encoding": MODULE.DATABASE_FINGERPRINT_CLIENT_ENCODING,
            },
            "tables": [["users", 2, len(TABLE_STREAM), TABLE_DIGEST]],
            "sequences": {
                "records": 1,
                "bytes": len(SEQUENCE_STREAM),
                "sha256": hashlib.sha256(SEQUENCE_STREAM).hexdigest(),
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()


def secure_file(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def tar_bytes(files: dict[str, bytes], *, gzip: bool = False) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz" if gzip else "w:") as archive:
        for name, payload in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            member.mode = 0o600
            archive.addfile(member, io.BytesIO(payload))
    return output.getvalue()


def docker_archive(
    role: str,
    release_sha: str,
    *,
    repo_tags: list[str] | None = None,
) -> tuple[bytes, str]:
    config = json.dumps(
        {
            "architecture": "amd64",
            "os": "linux",
            "rootfs": {"type": "layers", "diff_ids": []},
            "config": {
                "Labels": {
                    "org.opencontainers.image.revision": release_sha,
                }
            },
            "fixture_role": role,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    image_id = "sha256:" + hashlib.sha256(config).hexdigest()
    layer = b"empty-layer"
    manifest = json.dumps(
        [
            {
                "Config": f"{image_id.removeprefix('sha256:')}.json",
                "RepoTags": [] if repo_tags is None else repo_tags,
                "Layers": ["layer/layer.tar"],
            }
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return (
        tar_bytes(
            {
                "manifest.json": manifest,
                f"{image_id.removeprefix('sha256:')}.json": config,
                "layer/layer.tar": layer,
            }
        ),
        image_id,
    )


class OperationFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.operation_root = root / OPERATION_ID
        self.incoming = self.operation_root / "incoming"
        self.operation_root.mkdir(mode=0o700)
        self.incoming.mkdir(mode=0o700)
        self.release_bundle, self.release_sha, self.release_tree_sha = (
            self._release_bundle()
        )
        bootstrap_path = self.root / "bound-bootstrap.pyz"
        self.bootstrap_sha256, self.bootstrap_bytes = (
            ORCHESTRATOR.build_bootstrap_agent(
                self.root / "release-source",
                bootstrap_path,
            )
        )
        bootstrap_path.unlink()
        self.app_archive, self.app_id = docker_archive("app", self.release_sha)
        self.db_archive, self.db_id = docker_archive("postgres", self.release_sha)
        self.runtime_env = self._runtime_env()
        references = "\n".join(
            f"  {key.lower()}: ${{{key}:?required}}"
            for key in sorted(
                line.split("=", 1)[0]
                for line in self.runtime_env.decode().splitlines()
            )
        )
        self.role_compose = (
            "name: ${PRODUCTION_SHADOW_PROJECT:?required}-webapp-ir\n"
            "x-prepare-environment:\n"
            f"{references}\n"
            "services: {}\n"
        ).encode()
        self.ca = b"test-only-ca\n"
        self.runtime_archive = tar_bytes(
            {
                "role-compose.yml": self.role_compose,
                "runtime.env.role": self.runtime_env,
                "ca.crt": self.ca,
            }
        )
        self.payloads = {
            "release-archive": self.release_bundle,
            "app-image-archive": self.app_archive,
            "db-image-archive": self.db_archive,
            "database-backup": b"PGDMP test fixture",
            "uploads-archive": tar_bytes({"avatar.bin": b"avatar"}, gzip=True),
            "audit-archive": tar_bytes({"audit.jsonl": b"{}\n"}, gzip=True),
            "runtime-material": self.runtime_archive,
        }
        for kind, (name, _format) in MODULE.EXPECTED_ARTIFACTS.items():
            secure_file(self.incoming / name, self.payloads[kind])
        self.document = self._manifest_document()
        secure_file(
            self.incoming / "operation-manifest.json",
            json.dumps(
                self.document,
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
        )
        self.manifest = MODULE.load_manifest(
            self.incoming / "operation-manifest.json",
            required_uid=os.geteuid(),
        )

    def _release_bundle(self) -> tuple[bytes, str, str]:
        repository = self.root / "release-source"
        repository.mkdir(mode=0o700)
        subprocess.run(
            ["git", "init", "--quiet", str(repository)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["git", "-C", str(repository), "config", "user.name", "Test"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repository), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        (repository / "README").write_text("exact release fixture\n", encoding="ascii")
        (repository / "scripts").mkdir()
        (repository / "scripts" / "manage_webapp_writer.py").write_text(
            "# fixture\n",
            encoding="ascii",
        )
        source_root = Path(__file__).resolve().parents[1]
        for relative in ORCHESTRATOR._BOOTSTRAP_SOURCE_FILES:
            destination = repository / relative
            destination.write_bytes((source_root / relative).read_bytes())
        versions = repository / "migrations" / "versions"
        versions.mkdir(parents=True)
        (versions / "source.py").write_text(
            f'revision = "{SOURCE_REVISION}"\ndown_revision = None\n',
            encoding="ascii",
        )
        (versions / "intermediate.py").write_text(
            (
                f'revision = "{INTERMEDIATE_REVISION}"\n'
                f'down_revision = "{SOURCE_REVISION}"\n'
                "SQL = '''CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                f"{CONCURRENT_INDEX} ON users (id)'''\n"
            ),
            encoding="ascii",
        )
        (versions / "target.py").write_text(
            (
                f'revision = "{TARGET_REVISION}"\n'
                f'down_revision = "{INTERMEDIATE_REVISION}"\n'
            ),
            encoding="ascii",
        )
        subprocess.run(
            ["git", "-C", str(repository), "add", "."],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repository), "commit", "--quiet", "-m", "fixture"],
            check=True,
        )
        release_sha = subprocess.check_output(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            text=True,
        ).strip()
        tree_sha = subprocess.check_output(
            ["git", "-C", str(repository), "rev-parse", "HEAD^{tree}"],
            text=True,
        ).strip()
        bundle = self.root / "release.bundle"
        subprocess.run(
            ["git", "-C", str(repository), "bundle", "create", str(bundle), "HEAD"],
            check=True,
        )
        payload = bundle.read_bytes()
        return payload, release_sha, tree_sha

    def _runtime_env(self) -> bytes:
        project = f"trading-bot-wa-ir-{OPERATION_ID.replace('-', '')}"
        values = {
            "PRODUCTION_SHADOW_APP_IMAGE_ID": self.app_id,
            "PRODUCTION_SHADOW_CGROUP_PARENT": project,
            "PRODUCTION_SHADOW_DATA_ROOT": str(self.operation_root / "data"),
            "PRODUCTION_SHADOW_DR_CA_SHA256": "1" * 64,
            "PRODUCTION_SHADOW_DR_TLS_ATTESTATION_SHA256": "2" * 64,
            "PRODUCTION_SHADOW_DR_TLS_ATTESTED_AT_EPOCH": "1785170000",
            "PRODUCTION_SHADOW_OPERATION_ID": OPERATION_ID,
            "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID": self.db_id,
            "PRODUCTION_SHADOW_PROJECT": project,
            "PRODUCTION_SHADOW_PROJECT_ROOT": str(self.operation_root),
            "PRODUCTION_SHADOW_RELEASE_ROOT": str(self.operation_root / "release"),
            "PRODUCTION_SHADOW_RELEASE_SHA": self.release_sha,
            "PRODUCTION_SHADOW_SECRET_ROOT": str(self.operation_root / "secrets"),
            "WEBAPP_IR_APP_DB_PASSWORD": "app-password",
            "WEBAPP_IR_BLOB_DB_PASSWORD": "blob-password",
            "WEBAPP_IR_CONTROL_DB_PASSWORD": "control-password",
            "WEBAPP_IR_DELIVERY_DB_PASSWORD": "delivery-password",
            "WEBAPP_IR_EFFECT_DB_PASSWORD": "effect-password",
            "WEBAPP_IR_OBSERVER_DB_PASSWORD": "observer-password",
            "WEBAPP_IR_POSTGRES_DB": "trading_bot",
            "WEBAPP_IR_POSTGRES_PASSWORD": "database-password",
            "WEBAPP_IR_POSTGRES_USER": "postgres",
            "WEBAPP_IR_PROJECTION_DB_PASSWORD": "projection-password",
            "WEBAPP_IR_PUBLIC_WEBAPP_URL": "https://coin.gold-trade.ir",
            "WEBAPP_IR_RECEIVER_DB_PASSWORD": "receiver-password",
        }
        return (
            "\n".join(f"{key}={values[key]}" for key in sorted(values)) + "\n"
        ).encode()

    def _manifest_document(self) -> dict[str, object]:
        artifacts = []
        for kind, (name, artifact_format) in MODULE.EXPECTED_ARTIFACTS.items():
            payload = self.payloads[kind]
            artifacts.append(
                {
                    "kind": kind,
                    "destination_name": name,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "bytes": len(payload),
                    "format": artifact_format,
                }
            )
        runtime_hash = hashlib.sha256(self.runtime_env).hexdigest()
        return {
            "schema": MODULE.MANIFEST_SCHEMA,
            "operation_id": OPERATION_ID,
            "release_sha": self.release_sha,
            "release_tree_sha": self.release_tree_sha,
            "bootstrap": {
                "artifact_kind": ORCHESTRATOR.BOOTSTRAP_ARTIFACT_KIND,
                "destination_name": ORCHESTRATOR.BOOTSTRAP_DESTINATION_NAME,
                "sha256": self.bootstrap_sha256,
                "bytes": self.bootstrap_bytes,
                "format": "python-zipapp",
                "source_release_sha": self.release_sha,
                "source_release_tree_sha": self.release_tree_sha,
            },
            "expected_migration_revision": TARGET_REVISION,
            "source_database": {
                "alembic_revision": SOURCE_REVISION,
                "fingerprint_algorithm": MODULE.DATABASE_FINGERPRINT_ALGORITHM,
                "database_fingerprint_sha256": FINGERPRINT,
                "row_count": 2,
                "table_count": 1,
            },
            "artifacts": artifacts,
            "images": [
                {
                    "role": "app",
                    "artifact_kind": "app-image-archive",
                    "image_id": self.app_id,
                    "repo_tags": [],
                    "os": "linux",
                    "architecture": "amd64",
                },
                {
                    "role": "postgres",
                    "artifact_kind": "db-image-archive",
                    "image_id": self.db_id,
                    "repo_tags": [],
                    "os": "linux",
                    "architecture": "amd64",
                },
            ],
            "runtime": {
                "artifact_kind": "runtime-material",
                "role": "webapp-ir",
                "entries": [
                    {
                        "archive_path": "role-compose.yml",
                        "destination": MODULE.ROLE_COMPOSE_RELATIVE_PATH.as_posix(),
                        "sha256": hashlib.sha256(self.role_compose).hexdigest(),
                        "bytes": len(self.role_compose),
                        "mode": "0600",
                    },
                    {
                        "archive_path": "runtime.env.role",
                        "destination": MODULE.ROLE_ENV_RELATIVE_PATH.as_posix(),
                        "sha256": runtime_hash,
                        "bytes": len(self.runtime_env),
                        "mode": "0600",
                    },
                    {
                        "archive_path": "ca.crt",
                        "destination": "secrets/tls/ca.crt",
                        "sha256": hashlib.sha256(self.ca).hexdigest(),
                        "bytes": len(self.ca),
                        "mode": "0600",
                    }
                ],
                "required_env_keys": sorted(
                    line.split("=", 1)[0]
                    for line in self.runtime_env.decode().splitlines()
                ),
            },
            "compose": {
                "relative_path": MODULE.ROLE_COMPOSE_RELATIVE_PATH.as_posix(),
                "project_name": (
                    f"trading-bot-wa-ir-{OPERATION_ID.replace('-', '')}-webapp-ir"
                ),
                "services": dict(MODULE.EXPECTED_SERVICES),
            },
            "safety": dict(MODULE.EXPECTED_SAFETY),
        }


def valid_compose_config(fixture: OperationFixture) -> dict[str, object]:
    manifest = fixture.manifest
    runtime = MODULE.parse_safe_dotenv(fixture.runtime_env)
    project_base = f"trading-bot-wa-ir-{OPERATION_ID.replace('-', '')}"
    database_name = runtime["WEBAPP_IR_POSTGRES_DB"]
    owner_user = runtime["WEBAPP_IR_POSTGRES_USER"]
    owner_password = runtime["WEBAPP_IR_POSTGRES_PASSWORD"]
    owner = {
        "TZ": "UTC",
        "ENVIRONMENT": "production",
        "TRUSTED_PROXY_CIDRS": "127.0.0.1/32,::1/128,172.16.0.0/12",
        "TOPOLOGY_SCHEMA_VERSION": "three-site-dr-v1",
        "THREE_SITE_DR_ENABLED": "true",
        "DR_EVENT_PROTOCOL_ENABLED": "true",
        "DR_EVENT_PROTOCOL_STRICT": "true",
        "DR_SYNC_VERIFY_TLS": "true",
        "DR_SYNC_CA_BUNDLE": "/run/production-dr-ca/ca.crt",
        "RELEASE_SHA": fixture.release_sha,
        "BACKGROUND_JOBS_ENABLED": "false",
        "SERVER_MODE": "iran",
        "LOGICAL_AUTHORITY": "webapp",
        "PHYSICAL_SITE": "webapp_ir",
        "DATABASE_URL": (
            f"postgresql+asyncpg://{owner_user}:{owner_password}"
            f"@webapp_ir_db/{database_name}"
        ),
        "SYNC_DATABASE_URL": (
            f"postgresql://{owner_user}:{owner_password}"
            f"@webapp_ir_db/{database_name}"
        ),
        "POSTGRES_USER": owner_user,
        "POSTGRES_PASSWORD": owner_password,
        "POSTGRES_DB": database_name,
        "FRONTEND_URL": runtime["WEBAPP_IR_PUBLIC_WEBAPP_URL"],
        "PUBLIC_WEBAPP_URL": runtime["WEBAPP_IR_PUBLIC_WEBAPP_URL"],
        "JWT_SECRET_KEY": "production-shadow-prepare-does-not-serve-jwt-webapp-ir",
        "REDIS_URL": "redis://webapp_ir_redis:6379/0",
        "REDIS_HOST": "webapp_ir_redis",
    }
    role_passwords = {
        "THREE_SITE_APP_DB_PASSWORD": runtime["WEBAPP_IR_APP_DB_PASSWORD"],
        "THREE_SITE_RECEIVER_DB_PASSWORD": runtime[
            "WEBAPP_IR_RECEIVER_DB_PASSWORD"
        ],
        "THREE_SITE_DELIVERY_DB_PASSWORD": runtime[
            "WEBAPP_IR_DELIVERY_DB_PASSWORD"
        ],
        "THREE_SITE_PROJECTION_DB_PASSWORD": runtime[
            "WEBAPP_IR_PROJECTION_DB_PASSWORD"
        ],
        "THREE_SITE_BLOB_DB_PASSWORD": runtime["WEBAPP_IR_BLOB_DB_PASSWORD"],
        "THREE_SITE_EFFECT_DB_PASSWORD": runtime["WEBAPP_IR_EFFECT_DB_PASSWORD"],
        "THREE_SITE_CONTROL_DB_PASSWORD": runtime[
            "WEBAPP_IR_CONTROL_DB_PASSWORD"
        ],
        "THREE_SITE_OBSERVER_DB_PASSWORD": runtime[
            "WEBAPP_IR_OBSERVER_DB_PASSWORD"
        ],
    }
    commands = {
        "webapp_ir_restore_tool": [
            "sh",
            "-ec",
            "echo 'invoke with docker compose run and an explicit restore command' >&2; exit 64",
        ],
        "webapp_ir_db_roles": [
            "python",
            "scripts/provision_three_site_database_roles.py",
            "--role-prefix",
            "webapp_ir",
        ],
        "webapp_ir_migration": ["python", "manage.py"],
        "webapp_ir_db_roles_post_migration": [
            "python",
            "scripts/provision_three_site_database_roles.py",
            "--role-prefix",
            "webapp_ir",
        ],
        "webapp_ir_db_fencing": [
            "python",
            "scripts/activate_three_site_database_fencing.py",
            "--site",
            "webapp_ir",
            "--application-role",
            "webapp_ir_app",
            "--projection-role",
            "webapp_ir_projection",
            "--receiver-role",
            "webapp_ir_receiver",
            "--delivery-role",
            "webapp_ir_delivery",
            "--blob-role",
            "webapp_ir_blob",
            "--effect-role",
            "webapp_ir_effect",
            "--control-role",
            "webapp_ir_control",
            "--observer-role",
            "webapp_ir_observer",
            "--operator",
            "production-shadow-compose",
            "--apply",
            "--confirm",
            "ENABLE-THREE-SITE-DATABASE-FENCING",
        ],
        "webapp_ir_writer_fence": [
            "python",
            "scripts/manage_webapp_writer.py",
            "fence",
            "--expected-epoch",
            "1",
            "--expected-active-site",
            "webapp_fi",
            "--operator",
            f"production-shadow:{OPERATION_ID}",
            "--reason",
            "initialize WebApp-IR as an operation-bound locally fenced standby",
            "--apply",
            "--confirm",
            "writer:fence:webapp_ir:1:1",
        ],
    }
    dependencies = {
        "webapp_ir_db": {},
        "webapp_ir_restore_tool": {
            "webapp_ir_db": {"condition": "service_healthy", "required": True}
        },
        "webapp_ir_db_roles": {
            "webapp_ir_db": {"condition": "service_healthy", "required": True}
        },
        "webapp_ir_migration": {
            "webapp_ir_db_roles": {
                "condition": "service_completed_successfully",
                "required": True,
            }
        },
        "webapp_ir_db_roles_post_migration": {
            "webapp_ir_migration": {
                "condition": "service_completed_successfully",
                "required": True,
            }
        },
        "webapp_ir_db_fencing": {
            "webapp_ir_db_roles_post_migration": {
                "condition": "service_completed_successfully",
                "required": True,
            }
        },
        "webapp_ir_writer_fence": {
            "webapp_ir_db_fencing": {
                "condition": "service_completed_successfully",
                "required": True,
            }
        },
    }
    ca_mount = {
        "type": "bind",
        "source": str(fixture.operation_root / "secrets" / "tls" / "ca.crt"),
        "target": "/run/production-dr-ca/ca.crt",
        "read_only": True,
    }
    services: dict[str, dict[str, object]] = {}
    for name in manifest.services.values():
        postgres_service = name in {"webapp_ir_db", "webapp_ir_restore_tool"}
        service: dict[str, object] = {
            "image": fixture.db_id if postgres_service else fixture.app_id,
            "pull_policy": "never",
            "cgroup_parent": project_base,
            "cpus": 2 if postgres_service else 1,
            "mem_limit": "2147483648" if postgres_service else "805306368",
            "pids_limit": 512 if postgres_service else 256,
            "networks": {"webapp_ir": None},
            "depends_on": dependencies[name],
            "profiles": (
                [
                    "webapp-ir-data-ready",
                    "webapp-ir-restore",
                    "webapp-ir-prepare",
                    "webapp-ir-private",
                    "webapp-ir-acceptance",
                    "webapp-ir-activation",
                    "webapp-ir-effects",
                    "webapp-ir-observe",
                ]
                if name == "webapp_ir_db"
                else [
                    "webapp-ir-restore"
                    if name == "webapp_ir_restore_tool"
                    else "webapp-ir-prepare"
                ]
            ),
            "labels": {
                "trading-bot.production.operation-id": OPERATION_ID,
            },
            "restart": "unless-stopped" if name == "webapp_ir_db" else "no",
            "volumes": [],
        }
        if name == "webapp_ir_db":
            service["command"] = [
                "postgres",
                "-c",
                "timezone=UTC",
                "-c",
                "log_timezone=UTC",
            ]
            service["environment"] = {
                "TZ": "UTC",
                "PGTZ": "UTC",
                "POSTGRES_USER": owner_user,
                "POSTGRES_PASSWORD": owner_password,
                "POSTGRES_DB": database_name,
            }
            service["volumes"] = [
                {
                    "type": "volume",
                    "source": "webapp_ir_postgres",
                    "target": "/var/lib/postgresql/data",
                }
            ]
        elif name == "webapp_ir_restore_tool":
            service["command"] = commands[name]
            service["environment"] = {
                "PGHOST": "webapp_ir_db",
                "PGUSER": owner_user,
                "PGPASSWORD": owner_password,
                "PGDATABASE": database_name,
            }
        else:
            service["command"] = commands[name]
            service["volumes"] = [dict(ca_mount)]
            if name in {
                "webapp_ir_db_roles",
                "webapp_ir_db_roles_post_migration",
            }:
                service["environment"] = {**owner, **role_passwords}
            elif name == "webapp_ir_writer_fence":
                control_password = runtime["WEBAPP_IR_CONTROL_DB_PASSWORD"]
                control_async = (
                    f"postgresql+asyncpg://webapp_ir_control:{control_password}"
                    f"@webapp_ir_db/{database_name}"
                )
                service["environment"] = {
                    **owner,
                    "TRADING_BOT_SERVICE": "writer_control_cli",
                    "DATABASE_URL": control_async,
                    "SYNC_DATABASE_URL": (
                        f"postgresql://webapp_ir_control:{control_password}"
                        f"@webapp_ir_db/{database_name}"
                    ),
                    "DR_CONTROL_DATABASE_URL": control_async,
                    "POSTGRES_USER": "webapp_ir_control",
                    "POSTGRES_PASSWORD": control_password,
                    "WRITER_WITNESS_REQUIRED": "false",
                    "WRITER_WITNESS_AUTO_RENEW_ENABLED": "false",
                }
            else:
                service["environment"] = dict(owner)
        services[name] = service
    return {
        "name": manifest.project_name,
        "services": services,
        "networks": {
            "webapp_ir": {
                "internal": True,
                "name": f"{manifest.project_name}_webapp_ir",
            }
        },
        "volumes": {
            "webapp_ir_postgres": {
                "driver": "local",
                "driver_opts": {
                    "type": "none",
                    "o": "bind",
                    "device": str(
                        fixture.operation_root / "data" / "webapp-ir" / "postgres"
                    ),
                },
                "name": f"{manifest.project_name}_webapp_ir_postgres",
            }
        },
    }


class ProductionOperationTests(unittest.TestCase):
    def test_operation_lock_rejects_concurrent_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with MODULE._operation_lock(root, required_uid=os.geteuid()):
                with self.assertRaises(MODULE.ProductionOperationError):
                    with MODULE._operation_lock(root, required_uid=os.geteuid()):
                        self.fail("a concurrent invocation acquired the operation lock")
            with MODULE._operation_lock(root, required_uid=os.geteuid()):
                pass

    def test_file_materialization_reconciles_only_exact_crash_residue(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            payload = b"exact runtime material\n"
            digest = hashlib.sha256(payload).hexdigest()
            destination = root / "runtime.env.role"
            temporary = MODULE._materializing_path(destination)

            secure_file(temporary, b"partial")
            self.assertEqual(
                MODULE._write_or_verify_file(
                    destination,
                    io.BytesIO(payload),
                    expected_sha256=digest,
                    expected_bytes=len(payload),
                    required_uid=os.geteuid(),
                ),
                "created",
            )
            self.assertEqual(destination.read_bytes(), payload)
            self.assertFalse(temporary.exists())

            linked_destination = root / "ca.crt"
            linked_temporary = MODULE._materializing_path(linked_destination)
            secure_file(linked_temporary, payload)
            os.link(linked_temporary, linked_destination)
            self.assertEqual(
                MODULE._write_or_verify_file(
                    linked_destination,
                    io.BytesIO(b"unused"),
                    expected_sha256=digest,
                    expected_bytes=len(payload),
                    required_uid=os.geteuid(),
                ),
                "already-present",
            )
            self.assertFalse(linked_temporary.exists())

            ambiguous_destination = root / "compose.yml"
            ambiguous_temporary = MODULE._materializing_path(ambiguous_destination)
            foreign_link = root / "foreign-link"
            secure_file(ambiguous_temporary, payload)
            os.link(ambiguous_temporary, foreign_link)
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._write_or_verify_file(
                    ambiguous_destination,
                    io.BytesIO(payload),
                    expected_sha256=digest,
                    expected_bytes=len(payload),
                    required_uid=os.geteuid(),
                )
            self.assertTrue(ambiguous_temporary.exists())
            self.assertTrue(foreign_link.exists())

    def test_release_materialization_rebuilds_scoped_partial_directory(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            release = fixture.operation_root / "release"
            temporary = fixture.operation_root / ".release.materializing"
            temporary.mkdir(mode=0o700)
            secure_file(temporary / "partial", b"incomplete clone")

            MODULE._materialize_release_bundle(
                fixture.incoming / "release.bundle",
                release,
                manifest=fixture.manifest,
                required_uid=os.geteuid(),
            )
            self.assertFalse(temporary.exists())
            MODULE._verify_materialized_release(
                release,
                manifest=fixture.manifest,
            )

    def test_executing_bootstrap_is_exact_path_mode_and_identity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            executable = (
                fixture.operation_root / MODULE.BOOTSTRAP_RELATIVE_PATH
            )
            executable.parent.mkdir(mode=0o700)
            observed = ORCHESTRATOR.build_bootstrap_agent(
                fixture.root / "release-source",
                executable,
            )
            self.assertEqual(
                observed,
                (
                    fixture.manifest.bootstrap_sha256,
                    fixture.manifest.bootstrap_bytes,
                ),
            )
            executable.chmod(0o700)
            with patch.object(sys, "argv", [str(executable)]):
                MODULE._verify_executing_bootstrap(
                    fixture.manifest,
                    operation_root=fixture.operation_root,
                    required_uid=os.geteuid(),
                )
                executable.write_bytes(executable.read_bytes() + b"mutation")
                executable.chmod(0o700)
                with self.assertRaises(MODULE.ProductionOperationError):
                    MODULE._verify_executing_bootstrap(
                        fixture.manifest,
                        operation_root=fixture.operation_root,
                        required_uid=os.geteuid(),
                    )

    def test_manifest_plan_and_materialization_are_exact_and_create_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            planned = MODULE.plan(
                fixture.manifest,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            self.assertEqual(planned["status"], "planned")
            self.assertFalse(planned["public_app_started"])
            paths = MODULE.verify_incoming(
                fixture.manifest,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            materialized = MODULE.materialize(
                fixture.manifest,
                paths,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            self.assertTrue(Path(materialized["compose"]).is_file())
            runtime = fixture.operation_root / MODULE.ROLE_ENV_RELATIVE_PATH
            self.assertEqual(stat.S_IMODE(runtime.stat().st_mode), 0o600)
            self.assertEqual(runtime.read_bytes(), fixture.runtime_env)
            # A lost attestation can be retried without replacing a file.
            repeated = MODULE.materialize(
                fixture.manifest,
                paths,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            self.assertEqual(repeated, materialized)
            (fixture.operation_root / "release" / "untracked").write_text(
                "drift\n",
                encoding="ascii",
            )
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE.materialize(
                    fixture.manifest,
                    paths,
                    operation_root=fixture.operation_root,
                    required_uid=os.geteuid(),
                )

    def test_materialization_requires_fresh_empty_redis_directory(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            paths = MODULE.verify_incoming(
                fixture.manifest,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            data = fixture.operation_root / "data"
            data.mkdir(mode=0o700)
            role_data = data / "webapp-ir"
            role_data.mkdir(mode=0o700)
            redis = role_data / "redis"
            redis.mkdir(mode=0o700)
            secure_file(redis / "appendonly.aof", b"legacy")
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE.materialize(
                    fixture.manifest,
                    paths,
                    operation_root=fixture.operation_root,
                    required_uid=os.geteuid(),
                )

    def test_manifest_rejects_missing_or_duplicate_artifact_and_unsafe_runtime_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            missing = json.loads(json.dumps(fixture.document))
            missing["artifacts"].pop()
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._load_manifest_bytes(json.dumps(missing).encode())

            duplicate = json.loads(json.dumps(fixture.document))
            duplicate["artifacts"][1]["kind"] = duplicate["artifacts"][0]["kind"]
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._load_manifest_bytes(json.dumps(duplicate).encode())

            traversal = json.loads(json.dumps(fixture.document))
            traversal["runtime"]["entries"][0]["destination"] = "../runtime.env"
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._load_manifest_bytes(json.dumps(traversal).encode())

    def test_compose_validator_accepts_only_exact_prepare_contract(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            paths = MODULE.verify_incoming(
                fixture.manifest,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            MODULE.materialize(
                fixture.manifest,
                paths,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            baseline = valid_compose_config(fixture)
            with patch.object(MODULE, "_run", return_value=json.dumps(baseline)):
                observed = MODULE._validate_compose_config(
                    fixture.manifest,
                    operation_root=fixture.operation_root,
                )
            self.assertEqual(set(observed["services"]), set(MODULE.EXPECTED_SERVICES.values()))

            mutations = []
            extra_service = json.loads(json.dumps(baseline))
            extra_service["services"]["webapp_ir_api"] = {}
            mutations.append(extra_service)
            host_port = json.loads(json.dumps(baseline))
            host_port["services"]["webapp_ir_db"]["ports"] = ["5432:5432"]
            mutations.append(host_port)
            build = json.loads(json.dumps(baseline))
            build["services"]["webapp_ir_migration"]["build"] = "."
            mutations.append(build)
            restore_mount = json.loads(json.dumps(baseline))
            restore_mount["services"]["webapp_ir_restore_tool"]["volumes"] = [
                {
                    "type": "bind",
                    "source": "/srv/foreign",
                    "target": "/run/restore-input",
                    "read_only": True,
                }
            ]
            mutations.append(restore_mount)
            foreign_env = json.loads(json.dumps(baseline))
            foreign_env["services"]["webapp_ir_migration"]["environment"][
                "WEBAPP_FI_POSTGRES_PASSWORD"
            ] = "foreign"
            mutations.append(foreign_env)
            wrong_fence = json.loads(json.dumps(baseline))
            wrong_fence["services"]["webapp_ir_writer_fence"]["command"][-1] = (
                "writer:fence:webapp_ir:2:1"
            )
            mutations.append(wrong_fence)
            for mutation in mutations:
                with self.subTest(mutation=mutations.index(mutation)):
                    with (
                        patch.object(MODULE, "_run", return_value=json.dumps(mutation)),
                        self.assertRaises(MODULE.ProductionOperationError),
                    ):
                        MODULE._validate_compose_config(
                            fixture.manifest,
                            operation_root=fixture.operation_root,
                        )

    def test_prepare_runtime_rejects_private_plane_and_unused_env_material(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            compose = root / "compose.yml"
            compose.write_text("name: ${ALLOWED:?required}\n", encoding="ascii")
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._validate_role_local_environment_closure(
                    compose,
                    {
                        "ALLOWED": "yes",
                        "WEBAPP_IR_DR_PEERS_JSON": '["https://peer.invalid"]',
                    },
                )
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._validate_role_local_environment_closure(
                    compose,
                    {"ALLOWED": "yes", "UNUSED_SECRET": "value"},
                )

    def test_tar_validation_rejects_traversal_link_special_and_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cases: list[tuple[str, list[tarfile.TarInfo]]] = []

            traversal = tarfile.TarInfo("../escape")
            traversal.size = 1
            cases.append(("traversal.tar", [traversal]))

            link = tarfile.TarInfo("link")
            link.type = tarfile.SYMTYPE
            link.linkname = "target"
            cases.append(("link.tar", [link]))

            special = tarfile.TarInfo("device")
            special.type = tarfile.CHRTYPE
            cases.append(("special.tar", [special]))

            first = tarfile.TarInfo("same")
            first.size = 1
            second = tarfile.TarInfo("same")
            second.size = 1
            cases.append(("duplicate.tar", [first, second]))

            for filename, members in cases:
                path = root / filename
                with tarfile.open(path, "w:") as archive:
                    for member in members:
                        archive.addfile(
                            member,
                            io.BytesIO(b"x") if member.isreg() else None,
                        )
                path.chmod(0o600)
                with self.subTest(filename=filename):
                    with self.assertRaises(MODULE.ProductionOperationError):
                        MODULE.verify_tar_archive(path, mode="r:")

    def test_oneoff_cleanup_catches_late_container_and_removes_anonymous_volume(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            identifier = "a" * 12
            volume_name = "b" * 64
            inspection = [
                {
                    "Id": identifier,
                    "Config": {
                        "Image": fixture.db_id,
                        "Labels": {
                            "com.docker.compose.project": fixture.manifest.project_name,
                            "com.docker.compose.oneoff": "True",
                            "com.docker.compose.service": "webapp_ir_restore_tool",
                            "trading-bot.production.operation-id": OPERATION_ID,
                        },
                    },
                    "Mounts": [
                        {
                            "Type": "volume",
                            "Name": volume_name,
                            "Source": f"/var/lib/docker/volumes/{volume_name}/_data",
                            "Destination": "/var/lib/postgresql/data",
                            "Driver": "local",
                            "RW": True,
                        }
                    ],
                }
            ]
            calls: list[list[str]] = []

            def fake_run(arguments, *, timeout, stdin=-3):  # noqa: ANN001, ARG001
                calls.append(list(arguments))
                if arguments[1] == "inspect":
                    return json.dumps(inspection)
                if arguments[1] == "rm":
                    return identifier
                raise AssertionError(arguments)

            with (
                patch.object(
                    MODULE,
                    "_oneoff_ids",
                    side_effect=[[], [identifier], [], []],
                ),
                patch.object(MODULE, "_run", side_effect=fake_run),
                patch.object(MODULE.time, "sleep"),
            ):
                MODULE._cleanup_operation_oneoffs(
                    fixture.manifest,
                    operation_root=fixture.operation_root,
                )
            self.assertIn(
                [
                    MODULE.DOCKER,
                    "rm",
                    "--force",
                    "--volumes",
                    identifier,
                ],
                calls,
            )

            inspection[0]["Mounts"][0]["Destination"] = "/foreign"
            with (
                patch.object(MODULE, "_run", return_value=json.dumps(inspection)),
                self.assertRaises(MODULE.ProductionOperationError),
            ):
                MODULE._validate_oneoff_for_cleanup(
                    identifier,
                    fixture.manifest,
                    operation_root=fixture.operation_root,
                )

    def test_safe_dotenv_accepts_json_but_rejects_interpolation_and_ambiguity(self) -> None:
        parsed = MODULE.parse_safe_dotenv(
            b'PEERS_JSON=["https://one.invalid","https://two.invalid"]\nTOKEN=abc_123-DEF=\n'
        )
        self.assertEqual(parsed["TOKEN"], "abc_123-DEF=")
        for payload in (
            b"KEY=${AMBIENT}\n",
            b"KEY=one\nKEY=two\n",
            b" KEY=value\n",
            b'KEY="quoted-value"\n',
            b"KEY=value#comment\n",
            b"KEY=value\\escape\n",
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(MODULE.ProductionOperationError):
                    MODULE.parse_safe_dotenv(payload)

    def test_docker_archive_requires_one_exact_config_id_and_tag_set(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            app = next(image for image in fixture.manifest.images if image.role == "app")
            MODULE._docker_archive_identity(
                fixture.incoming / "app-image.tar",
                app,
                release_sha=fixture.release_sha,
            )
            wrong = MODULE.Image(
                role=app.role,
                artifact_kind=app.artifact_kind,
                image_id="sha256:" + "f" * 64,
                repo_tags=app.repo_tags,
                os=app.os,
                architecture=app.architecture,
            )
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._docker_archive_identity(
                    fixture.incoming / "app-image.tar",
                    wrong,
                    release_sha=fixture.release_sha,
                )
            tagged_archive, tagged_id = docker_archive(
                "app",
                fixture.release_sha,
                repo_tags=["registry.invalid/app:latest"],
            )
            tagged_path = fixture.root / "tagged.tar"
            secure_file(tagged_path, tagged_archive)
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._docker_archive_identity(
                    tagged_path,
                    MODULE.Image(
                        role="app",
                        artifact_kind="app-image-archive",
                        image_id=tagged_id,
                        repo_tags=(),
                        os="linux",
                        architecture="amd64",
                    ),
                    release_sha=fixture.release_sha,
                )
            wrong_revision_archive, wrong_revision_id = docker_archive(
                "app",
                "0" * 40,
            )
            wrong_revision_path = fixture.root / "wrong-revision.tar"
            secure_file(wrong_revision_path, wrong_revision_archive)
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._docker_archive_identity(
                    wrong_revision_path,
                    MODULE.Image(
                        role="app",
                        artifact_kind="app-image-archive",
                        image_id=wrong_revision_id,
                        repo_tags=(),
                        os="linux",
                        architecture="amd64",
                    ),
                    release_sha=fixture.release_sha,
                )

    def test_prepare_database_runs_only_db_restore_migration_and_exact_fence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            paths = MODULE.verify_incoming(
                fixture.manifest,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            MODULE.materialize(
                fixture.manifest,
                paths,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            calls: list[list[str]] = []
            database_created = False
            migration_revision = SOURCE_REVISION
            writer_fenced = False

            def fake_run(arguments, *, timeout, stdin=-3):  # noqa: ANN001, ARG001
                nonlocal database_created, migration_revision, writer_fenced
                calls.append(list(arguments))
                joined = " ".join(arguments)
                sql = arguments[-1] if "-Atqc" in arguments else ""
                if arguments[:2] == [MODULE.DOCKER, "ps"]:
                    return ""
                if " compose " in f" {joined} " and " ps " in f" {joined} ":
                    return "a" * 64 if database_created else ""
                if " up " in f" {joined} ":
                    database_created = True
                    return ""
                if (
                    " run " in f" {joined} "
                    and MODULE.EXPECTED_SERVICES["migration"] in arguments
                ):
                    migration_revision = TARGET_REVISION
                    return ""
                if (
                    " run " in f" {joined} "
                    and MODULE.EXPECTED_SERVICES["writer_fence"] in arguments
                ):
                    writer_fenced = True
                    return json.dumps({"status": "applied", "applied": True})
                if "SELECT 1" == sql:
                    return "1"
                if "SELECT count(*) FROM pg_class" in sql:
                    return "0"
                if sql == "SELECT version_num FROM alembic_version":
                    return migration_revision
                if "SELECT tablename FROM pg_tables" in sql:
                    return "users"
                if 'FROM public."users"' in sql:
                    return f"2|{TABLE_DIGEST}"
                if "FROM pg_sequences" in sql:
                    return "\n".join(SEQUENCE_ROWS)
                if "FROM pg_index i" in sql:
                    return (
                        f"{CONCURRENT_INDEX}|true|true"
                        if migration_revision == TARGET_REVISION
                        else ""
                    )
                if "SELECT json_build_object(" in sql:
                    return json.dumps(
                        {
                            "active_site": None if writer_fenced else "webapp_fi",
                            "writer_epoch": 1,
                            "control_state": "fenced" if writer_fenced else "active",
                            "witness_lease_id": None,
                        }
                    )
                return ""

            with (
                patch.object(MODULE, "_run", side_effect=fake_run),
                patch.object(MODULE, "_validate_compose_config"),
                patch.object(MODULE, "_validate_database_container"),
                patch.object(
                    MODULE,
                    "_database_fingerprint",
                    return_value=(FINGERPRINT, 2, 1),
                ),
                patch.object(MODULE.time, "sleep"),
            ):
                attestation = MODULE.prepare_database(
                    fixture.manifest,
                    operation_root=fixture.operation_root,
                )
            self.assertEqual(
                attestation["writer_state"],
                {
                    "active_site": None,
                    "writer_epoch": 1,
                    "control_state": "fenced",
                    "witness_lease_id": None,
                },
            )
            joined_calls = [" ".join(call) for call in calls]
            fence = next(
                call
                for call in joined_calls
                if " run " in f" {call} "
                and MODULE.EXPECTED_SERVICES["writer_fence"] in call
            )
            self.assertNotIn("run_writer_control_agent", fence)
            self.assertTrue(attestation["writer_fence_command_applied"])
            restore_call = next(
                call
                for call in calls
                if "pg_restore" in call
            )
            self.assertIn("--single-transaction", restore_call)
            up_calls = [call for call in calls if "up" in call]
            self.assertEqual(len(up_calls), 1)
            self.assertEqual(up_calls[0][-1], "webapp_ir_db")
            forbidden = (
                "webapp_ir_api",
                "webapp_ir_dr_receiver",
                "webapp_ir_dr_delivery",
                "webapp_ir_dr_projection",
                "webapp_ir_writer_control python -m scripts.run_writer_control_agent",
            )
            self.assertFalse(
                any(token in command for token in forbidden for command in joined_calls)
            )

    def test_database_fingerprint_is_manifest_bound_and_streaming(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            observed_sql: list[str] = []

            def stream(  # noqa: ANN001, ARG001
                _prefix,
                _manifest,
                *,
                sql,
                timeout,
                cleanup_evidence,
            ):
                observed_sql.append(sql)
                if "pg_sequences" in sql:
                    return MODULE.StreamDigest(
                        hashlib.sha256(SEQUENCE_STREAM).hexdigest(),
                        len(SEQUENCE_STREAM),
                        1,
                    )
                return MODULE.StreamDigest(
                    TABLE_DIGEST,
                    len(TABLE_STREAM),
                    2,
                )

            with (
                patch.object(MODULE, "_psql", return_value="users"),
                patch.object(
                    MODULE,
                    "_compose_streaming_copy_sha256",
                    side_effect=stream,
                ),
            ):
                observed = MODULE._database_fingerprint(
                    ["fixture"],
                    fixture.manifest,
                )
            self.assertEqual(observed, (FINGERPRINT, 2, 1))
            self.assertEqual(len(observed_sql), 2)
            self.assertTrue(all("COPY (" in sql for sql in observed_sql))
            self.assertFalse(any("string_agg" in sql for sql in observed_sql))

    def test_streaming_digest_has_bounded_memory_record_contract(self) -> None:
        payload = b"row-1\nrow-2\n"
        observed = MODULE._run_streaming_sha256(
            [
                sys.executable,
                "-c",
                (
                    "import os;"
                    f"os.write(1,{payload!r})"
                ),
            ],
            timeout=30,
        )
        self.assertEqual(
            observed,
            MODULE.StreamDigest(
                hashlib.sha256(payload).hexdigest(),
                len(payload),
                2,
            ),
        )

    def test_intermediate_migration_resume_repairs_index_and_rejects_off_corridor(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            paths = MODULE.verify_incoming(
                fixture.manifest,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            MODULE.materialize(
                fixture.manifest,
                paths,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            completed = set(MODULE._PHASES[:5])
            phase_evidence: dict[str, dict[str, object]] = {}

            def run_with_revision(
                start_revision: str,
                *,
                persisted_migration: bool = False,
            ):
                calls: list[list[str]] = []
                revision = start_revision
                index_status: tuple[bool, bool] | None = (False, False)
                operation_phases = (
                    set(MODULE._PHASES[:6])
                    if persisted_migration
                    else completed
                )

                def fake_run(arguments, *, timeout, stdin=-3):  # noqa: ANN001, ARG001
                    nonlocal revision, index_status
                    calls.append(list(arguments))
                    joined = " ".join(arguments)
                    sql = arguments[-1] if "-Atqc" in arguments else ""
                    if arguments[:2] == [MODULE.DOCKER, "ps"]:
                        return ""
                    if " compose " in f" {joined} " and " ps " in f" {joined} ":
                        return "c" * 64
                    if (
                        " run " in f" {joined} "
                        and MODULE.EXPECTED_SERVICES["migration"] in arguments
                    ):
                        revision = TARGET_REVISION
                        index_status = (True, True)
                        return ""
                    if sql == "SELECT 1":
                        return "1"
                    if "SELECT count(*) FROM pg_class" in sql:
                        return "1"
                    if sql == "SELECT version_num FROM alembic_version":
                        return revision
                    if "FROM pg_index i" in sql:
                        if index_status is None:
                            return ""
                        return (
                            f"{CONCURRENT_INDEX}|"
                            f"{'true' if index_status[0] else 'false'}|"
                            f"{'true' if index_status[1] else 'false'}"
                        )
                    if sql.startswith("DROP INDEX CONCURRENTLY"):
                        index_status = None
                        return ""
                    if "SELECT json_build_object(" in sql:
                        return json.dumps(
                            {
                                "active_site": None,
                                "writer_epoch": 1,
                                "control_state": "fenced",
                                "witness_lease_id": None,
                            }
                        )
                    return ""

                with (
                    patch.object(MODULE, "_run", side_effect=fake_run),
                    patch.object(MODULE, "_validate_compose_config"),
                    patch.object(MODULE, "_validate_database_container"),
                    patch.object(MODULE.time, "sleep"),
                ):
                    result = MODULE.prepare_database(
                        fixture.manifest,
                        operation_root=fixture.operation_root,
                        completed_phases=operation_phases,
                        phase_done=lambda phase, evidence: phase_evidence.__setitem__(
                            phase,
                            dict(evidence),
                        ),
                    )
                return result, calls

            result, calls = run_with_revision(INTERMEDIATE_REVISION)
            migration_evidence = phase_evidence["database-migrated"]
            self.assertEqual(
                migration_evidence["resumed_from_revision"],
                INTERMEDIATE_REVISION,
            )
            self.assertEqual(
                migration_evidence["repaired_concurrent_indexes"],
                [CONCURRENT_INDEX],
            )
            self.assertEqual(
                migration_evidence["service_order"],
                ["migration", "roles_post_migration", "fencing"],
            )
            self.assertEqual(result["migration_revision"], TARGET_REVISION)
            self.assertTrue(
                any(
                    arguments[-1].startswith("DROP INDEX CONCURRENTLY")
                    for arguments in calls
                    if "-Atqc" in arguments
                )
            )
            run_services = [
                arguments[arguments.index("-T") + 1]
                for arguments in calls
                if "-T" in arguments and "run" in arguments
            ]
            self.assertNotIn("webapp_ir_db_roles", run_services)
            self.assertFalse(any("pg_restore" in arguments for arguments in calls))

            with self.assertRaises(MODULE.ProductionOperationError):
                run_with_revision("deadbeef0000")
            with self.assertRaises(MODULE.ProductionOperationError):
                run_with_revision(
                    TARGET_REVISION,
                    persisted_migration=True,
                )

    def test_migration_graph_is_exact_and_finds_concurrent_index(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            paths = MODULE.verify_incoming(
                fixture.manifest,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            MODULE.materialize(
                fixture.manifest,
                paths,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            graph = MODULE._load_migration_graph(fixture.operation_root / "release")
            corridor = MODULE._migration_corridor(
                graph,
                source_revision=SOURCE_REVISION,
                target_revision=TARGET_REVISION,
            )
            self.assertEqual(
                set(corridor),
                {SOURCE_REVISION, INTERMEDIATE_REVISION, TARGET_REVISION},
            )
            self.assertEqual(
                MODULE._concurrent_index_names(graph, corridor),
                (CONCURRENT_INDEX,),
            )

    def test_execute_resumes_from_durable_phase_without_reloading_images(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))

            def crash_after_database_start(*args, **kwargs):  # noqa: ANN002, ANN003
                kwargs["phase_done"](
                    "database-started",
                    {"container_id": "a" * 64},
                )
                raise MODULE.ProductionOperationError("injected crash")

            with (
                patch.object(
                    MODULE,
                    "load_images",
                    return_value=[
                        {"role": "app", "image_id": fixture.app_id},
                        {"role": "postgres", "image_id": fixture.db_id},
                    ],
                ),
                patch.object(
                    MODULE,
                    "prepare_database",
                    side_effect=crash_after_database_start,
                ),
                self.assertRaises(MODULE.ProductionOperationError),
            ):
                MODULE.execute(
                    fixture.manifest,
                    operation_root=fixture.operation_root,
                    required_uid=os.geteuid(),
                    confirm=MODULE.confirmation_phrase(fixture.manifest),
                )

            state = MODULE._load_or_create_state(
                fixture.manifest,
                operation_root=fixture.operation_root,
            )
            self.assertEqual(
                state["completed_phases"],
                list(MODULE._PHASES[:4]),
            )
            self.assertEqual(
                stat.S_IMODE(
                    (fixture.operation_root / "operation-state.json").stat().st_mode
                ),
                0o600,
            )

            def complete_resume(*args, **kwargs):  # noqa: ANN002, ANN003
                self.assertEqual(
                    kwargs["completed_phases"],
                    set(MODULE._PHASES[:4]),
                )
                for phase in (
                    "database-restored",
                    "database-migrated",
                    "writer-fenced",
                ):
                    kwargs["phase_done"](phase, {"resumed": True})
                return {
                    "migration_revision": TARGET_REVISION,
                    "writer_state": {
                        "active_site": None,
                        "writer_epoch": 1,
                        "control_state": "fenced",
                        "witness_lease_id": None,
                    },
                    "bounded_ephemeral_oneoff_cleanup_performed": False,
                    "removed_ephemeral_resources": [],
                }

            with (
                patch.object(MODULE, "materialize") as rematerialize,
                patch.object(MODULE, "load_images") as reload_images,
                patch.object(MODULE, "_validate_loaded_image"),
                patch.object(
                    MODULE,
                    "prepare_database",
                    side_effect=complete_resume,
                ),
            ):
                result = MODULE.execute(
                    fixture.manifest,
                    operation_root=fixture.operation_root,
                    required_uid=os.geteuid(),
                    confirm=MODULE.confirmation_phrase(fixture.manifest),
                )
            rematerialize.assert_not_called()
            reload_images.assert_not_called()
            self.assertEqual(
                result["completed_phases"],
                list(MODULE._PHASES),
            )
            self.assertEqual(
                result["status"],
                "wa-ir-shadow-data-ready-fenced",
            )


if __name__ == "__main__":
    unittest.main()
