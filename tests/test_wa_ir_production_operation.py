from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from scripts import orchestrate_wa_ir_production_artifacts as ORCHESTRATOR
from scripts import wa_ir_production_operation as MODULE


OPERATION_ID = "12345678-1234-4234-8234-123456789abc"
SOURCE_REVISION = "f2c7d8e9a0b1"
INTERMEDIATE_REVISION = "a875b6c7d9e0"
TARGET_REVISION = "c097d8e9f1a2"
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


@contextmanager
def disconnectable_control_pipe():  # noqa: ANN202
    read_fd, write_fd = os.pipe()
    holder = subprocess.Popen(
        [
            "/usr/bin/python3",
            "-I",
            "-B",
            "-c",
            "import time; time.sleep(60)",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        pass_fds=(write_fd,),
        close_fds=True,
    )
    os.close(write_fd)
    try:
        yield read_fd, holder
    finally:
        os.close(read_fd)
        if holder.poll() is None:
            holder.terminate()
        try:
            holder.wait(timeout=2)
        except subprocess.TimeoutExpired:
            holder.kill()
            holder.wait(timeout=2)


@contextmanager
def live_control_pipe():  # noqa: ANN202
    with disconnectable_control_pipe() as (read_fd, _holder):
        yield read_fd


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
    runtime_uid: int | None = None,
    runtime_gid: int | None = None,
) -> tuple[bytes, str]:
    labels = {
        "org.opencontainers.image.revision": release_sha,
    }
    if role == "postgres":
        if runtime_uid is None or runtime_gid is None:
            raise ValueError("PostgreSQL fixture runtime identity is required")
        labels.update(
            {
                MODULE.POSTGRES_RUNTIME_UID_LABEL: str(runtime_uid),
                MODULE.POSTGRES_RUNTIME_GID_LABEL: str(runtime_gid),
            }
        )
    layer = b"empty-layer"
    layer_digest = "sha256:" + hashlib.sha256(layer).hexdigest()
    config = json.dumps(
        {
            "architecture": "amd64",
            "os": "linux",
            "created": "2026-07-27T00:00:00Z",
            "rootfs": {"type": "layers", "diff_ids": [layer_digest]},
            "config": {
                "Labels": labels,
                "Env": [f"FIXTURE_ROLE={role}"],
            },
            "fixture_role": role,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    image_id = "sha256:" + hashlib.sha256(config).hexdigest()
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


def docker_archive_semantic(
    payload: bytes,
) -> tuple[dict[str, object], str]:
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        manifest = json.loads(
            archive.extractfile("manifest.json").read().decode()
        )
        config = json.loads(
            archive.extractfile(manifest[0]["Config"]).read().decode()
        )
    return MODULE.image_content_descriptor_from_archive_config(config)


def docker_inspection(
    payload: bytes,
    runtime_image_id: str,
) -> dict[str, object]:
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        manifest = json.loads(
            archive.extractfile("manifest.json").read().decode()
        )
        config = json.loads(
            archive.extractfile(manifest[0]["Config"]).read().decode()
        )
    return {
        "Id": runtime_image_id,
        "Architecture": config["architecture"],
        "Os": config["os"],
        "Created": config["created"],
        "Config": config["config"],
        "RootFS": {
            "Type": config["rootfs"]["type"],
            "Layers": config["rootfs"]["diff_ids"],
        },
    }


class OperationFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.operation_root = root / OPERATION_ID
        self.incoming = self.operation_root / "incoming"
        self.project_prefix = root / "canonical-projects"
        self.data_prefix = root / "canonical-data"
        self.secret_prefix = root / "canonical-secrets"
        MODULE.PROJECT_ROOT_PREFIX = self.project_prefix
        MODULE.DATA_ROOT_PREFIX = self.data_prefix
        MODULE.SECRET_ROOT_PREFIX = self.secret_prefix
        ORCHESTRATOR.REMOTE_PROJECT_ROOT_PREFIX = self.project_prefix
        ORCHESTRATOR.REMOTE_DATA_ROOT_PREFIX = self.data_prefix
        ORCHESTRATOR.REMOTE_SECRET_ROOT_PREFIX = self.secret_prefix
        self.project_root = self.project_prefix / OPERATION_ID
        self.data_root = self.data_prefix / OPERATION_ID
        self.secret_root = self.secret_prefix / OPERATION_ID
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
        self.db_archive, self.db_id = docker_archive(
            "postgres",
            self.release_sha,
            runtime_uid=999,
            runtime_gid=999,
        )
        self.redis_archive, self.redis_id = docker_archive(
            "redis",
            self.release_sha,
        )
        self.nginx_archive, self.nginx_id = docker_archive(
            "nginx",
            self.release_sha,
        )
        self.image_semantics = {
            role: docker_archive_semantic(payload)
            for role, payload in {
                "app": self.app_archive,
                "postgres": self.db_archive,
                "redis": self.redis_archive,
                "nginx": self.nginx_archive,
            }.items()
        }
        self.runtime_image_ids = {
            "app": "sha256:" + "a" * 63 + "1",
            "postgres": "sha256:" + "b" * 63 + "2",
            "redis": "sha256:" + "c" * 63 + "3",
            "nginx": "sha256:" + "d" * 63 + "4",
        }
        self.ca = b"test-only-ca\n"
        self.runtime_env = self._runtime_env()
        runtime_image_env = {
            "app": "PRODUCTION_SHADOW_APP_IMAGE_ID",
            "postgres": "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID",
            "redis": "PRODUCTION_SHADOW_REDIS_IMAGE_ID",
            "nginx": "PRODUCTION_SHADOW_NGINX_IMAGE_ID",
        }
        references = "\n".join(
            f"  {key.lower()}: ${{{key}:?required}}"
            for key in sorted(
                line.split("=", 1)[0]
                for line in self.runtime_env.decode().splitlines()
                if line.split("=", 1)[0]
                not in set(runtime_image_env.values())
            )
        )
        self.role_compose = (
            "name: ${PRODUCTION_SHADOW_PROJECT:?required}-webapp-ir\n"
            "x-production-shadow-runtime-image-ids:\n"
            + "\n".join(
                f"  {role}: ${{{key}:?required}}"
                for role, key in runtime_image_env.items()
            )
            + "\n"
            "x-prepare-environment:\n"
            f"{references}\n"
            "services: {}\n"
        ).encode()
        self.payloads = {
            "release-bundle": self.release_bundle,
            "app-image-archive": self.app_archive,
            "postgres-image-archive": self.db_archive,
            "redis-image-archive": self.redis_archive,
            "nginx-image-archive": self.nginx_archive,
            "database-backup": b"PGDMP test fixture",
            "uploads-archive": tar_bytes({"avatar.bin": b"avatar"}, gzip=True),
            "audit-archive": tar_bytes({"audit.jsonl": b"{}\n"}, gzip=True),
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
        self.image_stage = self._image_stage_document()
        self.stage_attestation_sha256 = hashlib.sha256(
            json.dumps(
                self.image_stage,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        self.final_prepare_document = self._final_prepare_document()
        self.runtime_archive = tar_bytes(
            {
                MODULE.FINAL_PREPARE_MANIFEST_NAME: json.dumps(
                    self.final_prepare_document,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode(),
                "role-compose.yml": self.role_compose,
                "runtime.env.role": self.runtime_env,
                "ca.crt": self.ca,
            }
        )
        self.final_archive_path = (
            self.root / MODULE.FINAL_PREPARE_DESTINATION_NAME
        )
        secure_file(self.final_archive_path, self.runtime_archive)

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
            destination.parent.mkdir(parents=True, exist_ok=True)
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
        project = f"tb3p-{OPERATION_ID.replace('-', '')}"
        values = {
            "PRODUCTION_SHADOW_APP_IMAGE_ID": self.runtime_image_ids[
                "app"
            ],
            "PRODUCTION_SHADOW_CGROUP_PARENT": project,
            "PRODUCTION_SHADOW_DATA_ROOT": str(self.data_root),
            "PRODUCTION_SHADOW_DR_CA_SHA256": hashlib.sha256(
                self.ca
            ).hexdigest(),
            "PRODUCTION_SHADOW_DR_TLS_ATTESTATION_SHA256": "2" * 64,
            "PRODUCTION_SHADOW_DR_TLS_ATTESTED_AT_EPOCH": "1785170000",
            "PRODUCTION_SHADOW_OPERATION_ID": OPERATION_ID,
            "PRODUCTION_SHADOW_NGINX_IMAGE_ID": self.runtime_image_ids[
                "nginx"
            ],
            "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID": self.runtime_image_ids[
                "postgres"
            ],
            "PRODUCTION_SHADOW_PROJECT": project,
            "PRODUCTION_SHADOW_PROJECT_ROOT": str(self.project_root),
            "PRODUCTION_SHADOW_RELEASE_ROOT": str(
                self.project_root / "releases" / self.release_sha
            ),
            "PRODUCTION_SHADOW_RELEASE_SHA": self.release_sha,
            "PRODUCTION_SHADOW_REDIS_IMAGE_ID": self.runtime_image_ids[
                "redis"
            ],
            "PRODUCTION_SHADOW_SECRET_ROOT": str(self.secret_root),
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
        image_archives = {
            "app": ("app-image-archive", self.app_archive, self.app_id),
            "postgres": (
                "postgres-image-archive",
                self.db_archive,
                self.db_id,
            ),
            "redis": (
                "redis-image-archive",
                self.redis_archive,
                self.redis_id,
            ),
            "nginx": (
                "nginx-image-archive",
                self.nginx_archive,
                self.nginx_id,
            ),
        }
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
            "image_artifacts": {
                role: {
                    "archive_sha256": hashlib.sha256(payload).hexdigest(),
                    "archive_bytes": len(payload),
                    "config_digest": config_digest,
                    "content_descriptor": self.image_semantics[role][0],
                    "content_identity": self.image_semantics[role][1],
                }
                for role, (
                    _artifact_kind,
                    payload,
                    config_digest,
                ) in image_archives.items()
            },
            "postgres_runtime_uid": 999,
            "postgres_runtime_gid": 999,
            "compose": {
                "relative_path": MODULE.ROLE_COMPOSE_RELATIVE_PATH.as_posix(),
                "project_name": (
                    f"tb3p-{OPERATION_ID.replace('-', '')}-webapp-ir"
                ),
                "services": dict(MODULE.EXPECTED_SERVICES),
            },
            "safety": dict(MODULE.EXPECTED_SAFETY),
        }

    def _image_stage_document(self) -> dict[str, object]:
        images = [
            {
                "role": role,
                "runtime_image_id": self.runtime_image_ids[role],
                "config_digest": self.manifest.image_artifacts[
                    role
                ].config_digest,
                "content_descriptor": dict(
                    self.manifest.image_artifacts[
                        role
                    ].content_descriptor
                ),
                "content_identity": self.manifest.image_artifacts[
                    role
                ].content_identity,
                "source": "object-storage-archive",
            }
            for role in MODULE.IMAGE_ROLES
        ]
        return {
            "schema": MODULE.IMAGE_STAGE_ATTESTATION_SCHEMA,
            "operation_id": OPERATION_ID,
            "release_sha": self.release_sha,
            "operation_manifest_sha256": self.manifest.canonical_sha256,
            "role": "webapp_ir",
            "image_artifacts": ORCHESTRATOR._expected_image_artifact_bindings(
                self.manifest
            ),
            "runtime_image_ids": dict(self.runtime_image_ids),
            "images": images,
            "containers_started": False,
            "services_started": False,
        }

    def _final_prepare_document(self) -> dict[str, object]:
        entries = [
            {
                "archive_path": archive_path,
                "destination": destination,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "mode": "0600",
            }
            for archive_path, destination, payload in (
                (
                    "role-compose.yml",
                    MODULE.ROLE_COMPOSE_RELATIVE_PATH.as_posix(),
                    self.role_compose,
                ),
                (
                    "runtime.env.role",
                    MODULE.ROLE_ENV_RELATIVE_PATH.as_posix(),
                    self.runtime_env,
                ),
                (
                    "ca.crt",
                    MODULE.ROLE_CA_RELATIVE_PATH.as_posix(),
                    self.ca,
                ),
            )
        ]
        return {
            "schema": MODULE.FINAL_PREPARE_MANIFEST_SCHEMA,
            "operation_id": OPERATION_ID,
            "release_sha": self.release_sha,
            "operation_manifest_sha256": self.manifest.canonical_sha256,
            "stage_attestation_sha256": (
                self.stage_attestation_sha256
            ),
            "role": "webapp_ir",
            "runtime_image_ids": dict(self.runtime_image_ids),
            "entries": entries,
            "required_env_keys": sorted(
                line.split("=", 1)[0]
                for line in self.runtime_env.decode().splitlines()
            ),
        }


def materialize_fixture(
    manifest: MODULE.OperationManifest,
    paths: dict[str, Path],
    *,
    operation_root: Path,
    required_uid: int,
) -> dict[str, object]:
    materialized = dict(
        MODULE.materialize_stage(
            manifest,
            paths,
            operation_root=operation_root,
            required_uid=required_uid,
        )
    )
    final_archive = (
        operation_root.parent / MODULE.FINAL_PREPARE_DESTINATION_NAME
    )
    with tarfile.open(final_archive, mode="r:") as archive:
        final_document = json.loads(
            archive.extractfile(
                MODULE.FINAL_PREPARE_MANIFEST_NAME
            ).read().decode()
        )
    with patch.object(
        MODULE,
        "_validate_runtime_image_set",
        return_value=[],
    ):
        final = MODULE.install_final_prepare_material(
            manifest,
            final_archive,
            operation_root=operation_root,
            expected_stage_attestation_sha256=final_document[
                "stage_attestation_sha256"
            ],
            expected_runtime_image_ids=final_document[
                "runtime_image_ids"
            ],
            required_uid=required_uid,
        )
    materialized.update(
        {
            "runtime_material_installed": True,
            "runtime_env": final["runtime_env"],
            "compose": final["compose"],
        }
    )
    return materialized


def valid_compose_config(fixture: OperationFixture) -> dict[str, object]:
    manifest = fixture.manifest
    runtime = MODULE.parse_safe_dotenv(fixture.runtime_env)
    project_base = f"tb3p-{OPERATION_ID.replace('-', '')}"
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
        "source": str(fixture.secret_root / "tls" / "ca.crt"),
        "target": "/run/production-dr-ca/ca.crt",
        "read_only": True,
    }
    services: dict[str, dict[str, object]] = {}
    for name in manifest.services.values():
        postgres_service = name in {"webapp_ir_db", "webapp_ir_restore_tool"}
        service: dict[str, object] = {
            "image": (
                fixture.runtime_image_ids["postgres"]
                if postgres_service
                else fixture.runtime_image_ids["app"]
            ),
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
                    "type": "bind",
                    "source": str(
                        fixture.data_root / "webapp-ir" / "postgres"
                    ),
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
        "x-production-shadow-operation": {
            "operation_id": OPERATION_ID,
            "project_root": str(fixture.project_root),
            "release_root": str(
                fixture.project_root / "releases" / fixture.release_sha
            ),
            "data_root": str(fixture.data_root),
            "secret_root": str(fixture.secret_root),
            "dr_ca_sha256": runtime[
                "PRODUCTION_SHADOW_DR_CA_SHA256"
            ],
            "dr_tls_attestation_sha256": "2" * 64,
            "dr_tls_attested_at_epoch": "1785170000",
        },
        "x-production-shadow-runtime-image-ids": {
            "app": fixture.runtime_image_ids["app"],
            "postgres": fixture.runtime_image_ids["postgres"],
            "redis": fixture.runtime_image_ids["redis"],
            "nginx": fixture.runtime_image_ids["nginx"],
        },
        "services": services,
        "networks": {
            "webapp_ir": {
                "ipam": {},
                "internal": True,
                "labels": {
                    "trading-bot.production.operation-id": (
                        manifest.operation_id
                    ),
                },
                "name": f"{manifest.project_name}_webapp_ir",
            }
        },
    }


def valid_database_container_inspect(
    fixture: OperationFixture,
    *,
    identifier: str = "d" * 64,
    running: bool = False,
) -> list[dict[str, object]]:
    network_name = f"{fixture.manifest.project_name}_webapp_ir"
    postgres = fixture.data_root / "webapp-ir" / "postgres"
    return [
        {
            "Id": identifier,
            "Image": fixture.runtime_image_ids["postgres"],
            "Config": {
                "Image": fixture.runtime_image_ids["postgres"],
                "Labels": {
                    "com.docker.compose.oneoff": "False",
                    "com.docker.compose.project": fixture.manifest.project_name,
                    "com.docker.compose.service": (
                        fixture.manifest.services["database"]
                    ),
                    "trading-bot.production.operation-id": OPERATION_ID,
                },
            },
            "HostConfig": {
                "Binds": [
                    f"{postgres}:/var/lib/postgresql/data:rw",
                ],
                "NetworkMode": network_name,
                "PortBindings": {},
                "Privileged": False,
                "RestartPolicy": {"Name": "unless-stopped"},
            },
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": str(postgres),
                    "Destination": "/var/lib/postgresql/data",
                    "Mode": "rw",
                    "RW": True,
                    "Propagation": "rprivate",
                },
            ],
            "NetworkSettings": {
                "Networks": {
                    network_name: {},
                },
            },
            "State": {
                "Running": running,
                "Status": "running" if running else "created",
            },
        }
    ]


def valid_network_inspect(
    fixture: OperationFixture,
    *,
    container_id: str | None = None,
) -> list[dict[str, object]]:
    network_name = f"{fixture.manifest.project_name}_webapp_ir"
    containers = (
        {}
        if container_id is None
        else {
            container_id: {
                "Name": (
                    f"{fixture.manifest.project_name}-"
                    f"{fixture.manifest.services['database']}-1"
                ),
            },
        }
    )
    return [
        {
            "Name": network_name,
            "Id": "e" * 64,
            "Created": "2026-07-27T00:00:00Z",
            "Scope": "local",
            "Driver": "bridge",
            "EnableIPv6": False,
            "IPAM": {
                "Driver": "default",
                "Options": None,
                "Config": [
                    {
                        "Subnet": "172.28.0.0/16",
                        "Gateway": "172.28.0.1",
                    },
                ],
            },
            "Internal": True,
            "Attachable": False,
            "Ingress": False,
            "ConfigFrom": {"Network": ""},
            "ConfigOnly": False,
            "Containers": containers,
            "Options": {},
            "Labels": {
                "com.docker.compose.network": "webapp_ir",
                "com.docker.compose.project": fixture.manifest.project_name,
                "com.docker.compose.version": "5.1.4",
                "trading-bot.production.operation-id": OPERATION_ID,
            },
        }
    ]


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

    def test_state_startup_reconciles_only_exact_stale_temporaries(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            state_path = fixture.operation_root / "operation-state.json"
            legacy = fixture.operation_root / ".operation-state.json.4242.tmp"
            current = fixture.operation_root / ".operation-state.json.materializing"
            secure_file(legacy, b"legacy partial")
            secure_file(current, b"current partial")
            state = MODULE._load_or_create_state(
                fixture.manifest,
                operation_root=fixture.operation_root,
            )
            self.assertEqual(state["completed_phases"], ["received"])
            self.assertFalse(legacy.exists())
            self.assertFalse(current.exists())
            self.assertTrue(state_path.is_file())

            ambiguous = (
                fixture.operation_root / ".operation-state.json.5151.tmp"
            )
            foreign_link = fixture.operation_root / "foreign-state-link"
            secure_file(ambiguous, b"ambiguous")
            os.link(ambiguous, foreign_link)
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._load_or_create_state(
                    fixture.manifest,
                    operation_root=fixture.operation_root,
                )
            self.assertTrue(ambiguous.exists())
            self.assertTrue(foreign_link.exists())

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
            canonical = MODULE._canonical_operation_paths(fixture.manifest)
            MODULE._ensure_canonical_operation_directories(
                canonical,
                required_uid=os.geteuid(),
            )
            release = canonical.release_root
            temporary = release.with_name(f".{release.name}.materializing")
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
            materialized = materialize_fixture(
                fixture.manifest,
                paths,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            self.assertTrue(Path(materialized["compose"]).is_file())
            self.assertEqual(
                materialized["release_root"],
                str(
                    fixture.project_root
                    / "releases"
                    / fixture.release_sha
                ),
            )
            self.assertEqual(
                materialized["data_root"],
                str(fixture.data_root),
            )
            self.assertEqual(
                materialized["secrets_root"],
                str(fixture.secret_root),
            )
            self.assertFalse((fixture.operation_root / "release").exists())
            self.assertFalse((fixture.operation_root / "data").exists())
            self.assertFalse((fixture.operation_root / "secrets").exists())
            self.assertFalse((fixture.operation_root / "rendered").exists())
            for directory in (
                fixture.project_prefix,
                fixture.project_root,
                fixture.data_prefix,
                fixture.data_root,
                fixture.secret_prefix,
                fixture.secret_root,
            ):
                metadata = directory.stat(follow_symlinks=False)
                self.assertTrue(stat.S_ISDIR(metadata.st_mode))
                self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o700)
            runtime = fixture.secret_root / "webapp-ir" / "runtime.env.role"
            self.assertEqual(stat.S_IMODE(runtime.stat().st_mode), 0o600)
            self.assertEqual(runtime.read_bytes(), fixture.runtime_env)
            # A lost attestation can be retried without replacing a file.
            repeated = materialize_fixture(
                fixture.manifest,
                paths,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            self.assertEqual(repeated, materialized)
            (
                fixture.project_root
                / "releases"
                / fixture.release_sha
                / "untracked"
            ).write_text(
                "drift\n",
                encoding="ascii",
            )
            with self.assertRaises(MODULE.ProductionOperationError):
                materialize_fixture(
                    fixture.manifest,
                    paths,
                    operation_root=fixture.operation_root,
                    required_uid=os.geteuid(),
                )

    def test_materialization_rejects_symlinked_canonical_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            foreign = fixture.root / "foreign-projects"
            foreign.mkdir(mode=0o700)
            fixture.project_prefix.symlink_to(
                foreign,
                target_is_directory=True,
            )
            paths = MODULE.verify_incoming(
                fixture.manifest,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            with self.assertRaises(MODULE.ProductionOperationError):
                materialize_fixture(
                    fixture.manifest,
                    paths,
                    operation_root=fixture.operation_root,
                    required_uid=os.geteuid(),
                )
            self.assertEqual(list(foreign.iterdir()), [])

        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            foreign = fixture.root / "foreign-parent"
            foreign.mkdir(mode=0o700)
            linked_parent = fixture.root / "linked-parent"
            linked_parent.symlink_to(
                foreign,
                target_is_directory=True,
            )
            MODULE.PROJECT_ROOT_PREFIX = linked_parent / "canonical-projects"
            paths = MODULE.verify_incoming(
                fixture.manifest,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            with self.assertRaises(MODULE.ProductionOperationError):
                materialize_fixture(
                    fixture.manifest,
                    paths,
                    operation_root=fixture.operation_root,
                    required_uid=os.geteuid(),
                )
            self.assertEqual(list(foreign.iterdir()), [])

    def test_materialization_requires_fresh_empty_redis_directory(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            paths = MODULE.verify_incoming(
                fixture.manifest,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            data = fixture.data_root
            fixture.data_prefix.mkdir(mode=0o700)
            data.mkdir(mode=0o700)
            role_data = data / "webapp-ir"
            role_data.mkdir(mode=0o700)
            redis = role_data / "redis"
            redis.mkdir(mode=0o700)
            secure_file(redis / "appendonly.aof", b"legacy")
            with self.assertRaises(MODULE.ProductionOperationError):
                materialize_fixture(
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

            traversal = json.loads(
                json.dumps(fixture.final_prepare_document)
            )
            traversal["entries"][0]["destination"] = "../runtime.env"
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._load_final_prepare_manifest_bytes(
                    json.dumps(traversal).encode(),
                    manifest=fixture.manifest,
                    expected_stage_attestation_sha256=(
                        fixture.stage_attestation_sha256
                    ),
                )

            legacy_project = json.loads(json.dumps(fixture.document))
            legacy_project["compose"]["project_name"] = (
                f"trading-bot-wa-ir-{OPERATION_ID.replace('-', '')}"
                "-webapp-ir"
            )
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._load_manifest_bytes(
                    json.dumps(legacy_project).encode()
                )

    def test_manifest_requires_exact_four_image_archive_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))

            missing = json.loads(json.dumps(fixture.document))
            missing["image_artifacts"].pop("nginx")
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._load_manifest_bytes(json.dumps(missing).encode())

            duplicate_content = json.loads(json.dumps(fixture.document))
            duplicate_content["image_artifacts"]["nginx"][
                "content_descriptor"
            ] = duplicate_content["image_artifacts"]["app"][
                "content_descriptor"
            ]
            duplicate_content["image_artifacts"]["nginx"][
                "content_identity"
            ] = duplicate_content["image_artifacts"]["app"][
                "content_identity"
            ]
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._load_manifest_bytes(
                    json.dumps(duplicate_content).encode()
                )

            duplicate_id = json.loads(json.dumps(fixture.document))
            duplicate_id["image_artifacts"]["nginx"][
                "config_digest"
            ] = duplicate_id["image_artifacts"]["app"][
                "config_digest"
            ]
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._load_manifest_bytes(json.dumps(duplicate_id).encode())

            missing_runtime_owner = json.loads(json.dumps(fixture.document))
            missing_runtime_owner.pop("postgres_runtime_uid")
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._load_manifest_bytes(
                    json.dumps(missing_runtime_owner).encode()
                )

            invalid_runtime_owner = json.loads(json.dumps(fixture.document))
            invalid_runtime_owner["postgres_runtime_gid"] = 0
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._load_manifest_bytes(
                    json.dumps(invalid_runtime_owner).encode()
                )

            swapped = json.loads(json.dumps(fixture.document))
            redis = swapped["image_artifacts"]["redis"]
            nginx = swapped["image_artifacts"]["nginx"]
            redis["archive_sha256"], nginx["archive_sha256"] = (
                nginx["archive_sha256"],
                redis["archive_sha256"],
            )
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._load_manifest_bytes(json.dumps(swapped).encode())

            wrong_archive_id = json.loads(json.dumps(fixture.document))
            redis = wrong_archive_id["image_artifacts"]["redis"]
            redis["config_digest"] = "sha256:" + "a" * 64
            manifest = MODULE._load_manifest_bytes(
                json.dumps(wrong_archive_id).encode()
            )
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._docker_archive_identity(
                    fixture.incoming / "redis-image.tar",
                    manifest.image_artifacts["redis"],
                    release_sha=fixture.release_sha,
                )

    def test_compose_validator_accepts_only_exact_prepare_contract(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            paths = MODULE.verify_incoming(
                fixture.manifest,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            materialize_fixture(
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
            wrong_operation_root = json.loads(json.dumps(baseline))
            wrong_operation_root["x-production-shadow-operation"][
                "project_root"
            ] = str(fixture.operation_root)
            mutations.append(wrong_operation_root)
            wrong_runtime_map = json.loads(json.dumps(baseline))
            wrong_runtime_map[
                "x-production-shadow-runtime-image-ids"
            ]["redis"] = wrong_runtime_map[
                "x-production-shadow-runtime-image-ids"
            ]["nginx"]
            mutations.append(wrong_runtime_map)
            foreign_bind = json.loads(json.dumps(baseline))
            foreign_bind["services"]["webapp_ir_db"]["volumes"][0][
                "source"
            ] = "/srv/foreign-postgres"
            mutations.append(foreign_bind)
            named_mount = json.loads(json.dumps(baseline))
            named_mount["services"]["webapp_ir_db"]["volumes"][0].update(
                {
                    "type": "volume",
                    "source": "webapp_ir_postgres",
                }
            )
            named_mount["volumes"] = {
                "webapp_ir_postgres": {
                    "driver": "local",
                    "driver_opts": {
                        "type": "none",
                        "o": "bind",
                        "device": "/srv/foreign-postgres",
                    },
                    "name": (
                        f"{fixture.manifest.project_name}_webapp_ir_postgres"
                    ),
                },
            }
            mutations.append(named_mount)
            missing_network_label = json.loads(json.dumps(baseline))
            missing_network_label["networks"]["webapp_ir"]["labels"] = {}
            mutations.append(missing_network_label)
            custom_network_ipam = json.loads(json.dumps(baseline))
            custom_network_ipam["networks"]["webapp_ir"]["ipam"] = {
                "driver": "custom",
            }
            mutations.append(custom_network_ipam)
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

    def test_database_container_requires_exact_secure_direct_bind(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            paths = MODULE.verify_incoming(
                fixture.manifest,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            materialize_fixture(
                fixture.manifest,
                paths,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            identifier = "d" * 64
            stopped = valid_database_container_inspect(
                fixture,
                identifier=identifier,
            )
            with patch.object(
                MODULE,
                "_run",
                return_value=json.dumps(stopped),
            ):
                self.assertFalse(
                    MODULE._validate_database_container(
                        identifier,
                        fixture.manifest,
                    )
                )
            running = valid_database_container_inspect(
                fixture,
                identifier=identifier,
                running=True,
            )
            postgres = fixture.data_root / "webapp-ir" / "postgres"
            with (
                patch.object(
                    MODULE,
                    "_run",
                    return_value=json.dumps(running),
                ),
                patch.object(
                    MODULE,
                    "_validate_postgres_bind_source",
                ) as runtime_owner,
            ):
                self.assertTrue(
                    MODULE._validate_database_container(
                        identifier,
                        fixture.manifest,
                    )
                )
            runtime_owner.assert_called_once_with(
                fixture.manifest,
                initialized=True,
            )

            mutations = []
            named_volume = json.loads(json.dumps(stopped))
            named_volume[0]["HostConfig"]["Binds"] = None
            named_volume[0]["Mounts"][0] = {
                "Type": "volume",
                "Name": (
                    f"{fixture.manifest.project_name}_webapp_ir_postgres"
                ),
                "Source": (
                    "/var/lib/docker/volumes/"
                    f"{fixture.manifest.project_name}_webapp_ir_postgres/_data"
                ),
                "Destination": "/var/lib/postgresql/data",
                "Driver": "local",
                "RW": True,
            }
            mutations.append(named_volume)
            foreign_bind = json.loads(json.dumps(stopped))
            foreign_bind[0]["HostConfig"]["Binds"] = [
                "/srv/foreign:/var/lib/postgresql/data:rw",
            ]
            foreign_bind[0]["Mounts"][0]["Source"] = "/srv/foreign"
            mutations.append(foreign_bind)
            extra_mount = json.loads(json.dumps(stopped))
            extra_mount[0]["Mounts"].append(
                {
                    "Type": "bind",
                    "Source": "/srv/foreign",
                    "Destination": "/foreign",
                    "RW": False,
                    "Propagation": "rprivate",
                }
            )
            mutations.append(extra_mount)
            for mutation in mutations:
                with self.subTest(mutation=mutations.index(mutation)):
                    with (
                        patch.object(
                            MODULE,
                            "_run",
                            return_value=json.dumps(mutation),
                        ),
                        self.assertRaises(MODULE.ProductionOperationError),
                    ):
                        MODULE._validate_database_container(
                            identifier,
                            fixture.manifest,
                        )

            self.assertIsNotNone(
                MODULE._validate_postgres_bind_source(
                    fixture.manifest,
                    initialized=False,
                )
            )
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._validate_postgres_bind_source(
                    fixture.manifest,
                    initialized=True,
                )

            runtime_metadata = os.stat_result(
                (
                    stat.S_IFDIR | 0o700,
                    1,
                    1,
                    1,
                    999,
                    999,
                    0,
                    0,
                    0,
                    0,
                )
            )
            with (
                patch.object(
                    MODULE,
                    "_require_real_owned_directory_chain",
                ),
                patch.object(
                    Path,
                    "stat",
                    return_value=runtime_metadata,
                ),
            ):
                self.assertEqual(
                    MODULE._validate_postgres_bind_source(
                        fixture.manifest,
                        initialized=True,
                    ).st_uid,
                    999,
                )

            wrong_runtime_metadata = os.stat_result(
                (
                    stat.S_IFDIR | 0o700,
                    1,
                    1,
                    1,
                    998,
                    999,
                    0,
                    0,
                    0,
                    0,
                )
            )
            with (
                patch.object(
                    MODULE,
                    "_require_real_owned_directory_chain",
                ),
                patch.object(
                    Path,
                    "stat",
                    return_value=wrong_runtime_metadata,
                ),
                self.assertRaises(MODULE.ProductionOperationError),
            ):
                MODULE._validate_postgres_bind_source(
                    fixture.manifest,
                    initialized=True,
                )

            postgres.rmdir()
            foreign = fixture.root / "foreign-postgres"
            foreign.mkdir(mode=0o700)
            postgres.symlink_to(foreign, target_is_directory=True)
            with (
                patch.object(
                    MODULE,
                    "_run",
                    return_value=json.dumps(stopped),
                ),
                self.assertRaises(MODULE.ProductionOperationError),
            ):
                MODULE._validate_database_container(
                    identifier,
                    fixture.manifest,
                )

    def test_operation_network_rejects_foreign_identity_and_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            expected_name = (
                f"{fixture.manifest.project_name}_webapp_ir"
            )
            container_id = "d" * 64

            with patch.object(MODULE, "_run", return_value=""):
                self.assertIsNone(
                    MODULE._validate_operation_network(
                        fixture.manifest,
                        expected_container_id=None,
                        require_present=False,
                        require_attached=False,
                    )
                )
                with self.assertRaises(MODULE.ProductionOperationError):
                    MODULE._validate_operation_network(
                        fixture.manifest,
                        expected_container_id=None,
                        require_present=True,
                        require_attached=False,
                    )

            exact = valid_network_inspect(
                fixture,
                container_id=container_id,
            )

            def network_run(arguments, *, timeout, stdin=-3):  # noqa: ANN001, ARG001
                docker = arguments[len(MODULE.DOCKER_BASE) :]
                if docker[:2] == ["network", "ls"]:
                    return expected_name
                if docker[:2] == ["network", "inspect"]:
                    return json.dumps(exact)
                self.fail(f"unexpected Docker network command: {arguments}")

            with patch.object(MODULE, "_run", side_effect=network_run):
                observed = MODULE._validate_operation_network(
                    fixture.manifest,
                    expected_container_id=container_id,
                    require_present=True,
                    require_attached=True,
                )
            self.assertEqual(observed["network_id"], "e" * 64)

            exact_oneoff = "c" * 64
            exact[0]["Containers"][exact_oneoff] = {
                "Name": "exact-operation-oneoff",
            }
            with patch.object(MODULE, "_run", side_effect=network_run):
                MODULE._validate_operation_network(
                    fixture.manifest,
                    expected_container_id=container_id,
                    allowed_container_ids={exact_oneoff},
                    require_present=True,
                    require_attached=True,
                )
            exact[0]["Containers"].pop(exact_oneoff)

            mutations = []
            unlabeled = json.loads(json.dumps(exact))
            unlabeled[0]["Labels"] = {}
            mutations.append(unlabeled)
            foreign_label = json.loads(json.dumps(exact))
            foreign_label[0]["Labels"]["foreign"] = "value"
            mutations.append(foreign_label)
            external = json.loads(json.dumps(exact))
            external[0]["Internal"] = False
            mutations.append(external)
            wrong_driver = json.loads(json.dumps(exact))
            wrong_driver[0]["Driver"] = "overlay"
            mutations.append(wrong_driver)
            custom_options = json.loads(json.dumps(exact))
            custom_options[0]["Options"] = {"foreign": "value"}
            mutations.append(custom_options)
            custom_ipam = json.loads(json.dumps(exact))
            custom_ipam[0]["IPAM"]["Options"] = {"foreign": "value"}
            mutations.append(custom_ipam)
            foreign_endpoint = json.loads(json.dumps(exact))
            foreign_endpoint[0]["Containers"]["f" * 64] = {
                "Name": "foreign",
            }
            mutations.append(foreign_endpoint)
            for mutation in mutations:
                with self.subTest(mutation=mutations.index(mutation)):
                    def mutated_run(  # noqa: ANN001, ARG001
                        arguments,
                        *,
                        timeout,
                        stdin=-3,
                    ):
                        docker = arguments[len(MODULE.DOCKER_BASE) :]
                        if docker[:2] == ["network", "ls"]:
                            return expected_name
                        return json.dumps(mutation)

                    with (
                        patch.object(
                            MODULE,
                            "_run",
                            side_effect=mutated_run,
                        ),
                        self.assertRaises(MODULE.ProductionOperationError),
                    ):
                        MODULE._validate_operation_network(
                            fixture.manifest,
                            expected_container_id=container_id,
                            require_present=True,
                            require_attached=True,
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
            materialize_fixture(
                fixture.manifest,
                MODULE.verify_incoming(
                    fixture.manifest,
                    operation_root=fixture.operation_root,
                    required_uid=os.geteuid(),
                ),
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            identifier = "a" * 12
            volume_name = "b" * 64
            inspection = [
                {
                    "Id": identifier,
                    "Config": {
                        "Image": fixture.runtime_image_ids["postgres"],
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
                docker = arguments[len(MODULE.DOCKER_BASE) :]
                if docker[0] == "inspect":
                    return json.dumps(inspection)
                if docker[0] == "rm":
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
                    *MODULE.DOCKER_BASE,
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

    def test_project_inventory_rejects_missing_label_and_foreign_service(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            identifier = "f" * 64
            inspection = [
                {
                    "Id": identifier,
                    "Config": {
                        "Image": fixture.runtime_image_ids["app"],
                        "Labels": {
                            "com.docker.compose.project": (
                                fixture.manifest.project_name
                            ),
                            "com.docker.compose.oneoff": "True",
                            "com.docker.compose.service": (
                                fixture.manifest.services["roles"]
                            ),
                        },
                    },
                    "Mounts": [],
                },
            ]
            with (
                patch.object(
                    MODULE,
                    "_project_container_ids",
                    return_value=[identifier],
                ),
                patch.object(
                    MODULE,
                    "_run",
                    return_value=json.dumps(inspection),
                ),
                self.assertRaises(MODULE.ProductionOperationError),
            ):
                MODULE._oneoff_ids(
                    fixture.manifest,
                    operation_root=fixture.operation_root,
                )

            inspection[0]["Config"]["Labels"][
                "trading-bot.production.operation-id"
            ] = OPERATION_ID
            inspection[0]["Config"]["Labels"][
                "com.docker.compose.service"
            ] = "webapp_ir_api"
            with (
                patch.object(
                    MODULE,
                    "_project_container_ids",
                    return_value=[identifier],
                ),
                patch.object(
                    MODULE,
                    "_run",
                    return_value=json.dumps(inspection),
                ),
                self.assertRaises(MODULE.ProductionOperationError),
            ):
                MODULE._oneoff_ids(
                    fixture.manifest,
                    operation_root=fixture.operation_root,
                )

            inspection[0]["Config"]["Labels"][
                "com.docker.compose.service"
            ] = fixture.manifest.services["database"]
            inspection[0]["Config"]["Labels"][
                "com.docker.compose.oneoff"
            ] = "False"
            inspection[0]["Config"]["Labels"].pop(
                "trading-bot.production.operation-id"
            )
            with (
                patch.object(
                    MODULE,
                    "_project_container_ids",
                    return_value=[identifier],
                ),
                patch.object(
                    MODULE,
                    "_run",
                    return_value=json.dumps(inspection),
                ),
                self.assertRaises(MODULE.ProductionOperationError),
            ):
                MODULE._oneoff_ids(
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

            postgres = next(
                image
                for image in fixture.manifest.images
                if image.role == "postgres"
            )
            wrong_owner_archive, wrong_owner_id = docker_archive(
                "postgres",
                fixture.release_sha,
                runtime_uid=998,
                runtime_gid=999,
            )
            wrong_owner_path = fixture.root / "wrong-owner.tar"
            secure_file(wrong_owner_path, wrong_owner_archive)
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._docker_archive_identity(
                    wrong_owner_path,
                    MODULE.Image(
                        role="postgres",
                        artifact_kind="postgres-image-archive",
                        image_id=wrong_owner_id,
                        repo_tags=(),
                        os="linux",
                        architecture="amd64",
                        runtime_uid=999,
                        runtime_gid=999,
                    ),
                    release_sha=fixture.release_sha,
                )

            loaded_inspection = docker_inspection(
                fixture.db_archive,
                fixture.runtime_image_ids["postgres"],
            )
            MODULE._local_image_semantic_evidence(
                loaded_inspection,
                image=fixture.manifest.image_artifacts["postgres"],
                manifest=fixture.manifest,
            )
            loaded_inspection["Config"]["Labels"][
                MODULE.POSTGRES_RUNTIME_UID_LABEL
            ] = "998"
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._local_image_semantic_evidence(
                    loaded_inspection,
                    image=fixture.manifest.image_artifacts[
                        "postgres"
                    ],
                    manifest=fixture.manifest,
                )

    def test_load_images_accepts_cross_engine_and_exact_preexisting_ids_but_rejects_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            paths = MODULE.verify_incoming(
                fixture.manifest,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            inspections = {
                fixture.runtime_image_ids["app"]: docker_inspection(
                    fixture.app_archive,
                    fixture.runtime_image_ids["app"],
                ),
                fixture.runtime_image_ids["postgres"]: docker_inspection(
                    fixture.db_archive,
                    fixture.runtime_image_ids["postgres"],
                ),
                fixture.runtime_image_ids["redis"]: docker_inspection(
                    fixture.redis_archive,
                    fixture.runtime_image_ids["redis"],
                ),
                fixture.runtime_image_ids["nginx"]: docker_inspection(
                    fixture.nginx_archive,
                    fixture.runtime_image_ids["nginx"],
                ),
            }
            inventory_calls = 0
            load_calls: list[list[str]] = []

            def fake_run(arguments, *, timeout, stdin=-3):  # noqa: ANN001, ARG001
                nonlocal inventory_calls
                docker = arguments[len(MODULE.DOCKER_BASE) :]
                if docker[:2] == ["image", "ls"]:
                    inventory_calls += 1
                    if inventory_calls == 1:
                        return "\n".join(
                            (
                                fixture.runtime_image_ids["redis"],
                                fixture.runtime_image_ids["nginx"],
                            )
                        )
                    return "\n".join(
                        [
                            *fixture.runtime_image_ids.values(),
                            fixture.runtime_image_ids["app"],
                        ]
                    )
                if docker[:2] == ["image", "inspect"]:
                    return json.dumps([inspections[docker[2]]])
                if docker[:2] == ["image", "load"]:
                    load_calls.append(list(arguments))
                    return "loaded"
                raise AssertionError(arguments)

            with patch.object(MODULE, "_run", side_effect=fake_run):
                evidence = MODULE.load_images(
                    fixture.manifest,
                    paths,
                )
            self.assertEqual(
                {
                    item["role"]: item["runtime_image_id"]
                    for item in evidence
                },
                fixture.runtime_image_ids,
            )
            self.assertTrue(
                all(
                    fixture.runtime_image_ids[role]
                    != fixture.manifest.image_artifacts[
                        role
                    ].config_digest
                    for role in MODULE.IMAGE_ROLES
                )
            )
            self.assertEqual(len(load_calls), 2)

            exact = {
                image.content_identity: ()
                for image in fixture.manifest.image_artifacts.values()
            }
            ambiguous_before = dict(exact)
            ambiguous_before[
                fixture.manifest.image_artifacts["app"].content_identity
            ] = (
                {"runtime_image_id": fixture.runtime_image_ids["app"]},
                {"runtime_image_id": "sha256:" + "e" * 64},
            )
            with (
                patch.object(MODULE, "_docker_archive_identity"),
                patch.object(
                    MODULE,
                    "_enumerate_local_images",
                    return_value=ambiguous_before,
                ),
                patch.object(MODULE, "_run") as no_load,
                self.assertRaises(MODULE.ProductionOperationError),
            ):
                MODULE.load_images(fixture.manifest, paths)
            no_load.assert_not_called()

            ambiguous_after = {
                image.content_identity: (
                    {
                        "runtime_image_id": (
                            fixture.runtime_image_ids[role]
                        )
                    },
                )
                for role, image in fixture.manifest.image_artifacts.items()
            }
            ambiguous_after[
                fixture.manifest.image_artifacts["app"].content_identity
            ] = (
                {"runtime_image_id": fixture.runtime_image_ids["app"]},
                {"runtime_image_id": "sha256:" + "e" * 64},
            )
            with (
                patch.object(MODULE, "_docker_archive_identity"),
                patch.object(
                    MODULE,
                    "_enumerate_local_images",
                    side_effect=[exact, ambiguous_after],
                ),
                patch.object(MODULE, "_run", return_value=""),
                self.assertRaises(MODULE.ProductionOperationError),
            ):
                MODULE.load_images(fixture.manifest, paths)

    def test_load_images_safely_retries_after_partial_idempotent_load(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            paths = MODULE.verify_incoming(
                fixture.manifest,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            empty = {
                image.content_identity: ()
                for image in fixture.manifest.image_artifacts.values()
            }
            partial = dict(empty)
            for role in ("app", "postgres", "redis"):
                partial[
                    fixture.manifest.image_artifacts[role].content_identity
                ] = (
                    {
                        "runtime_image_id": fixture.runtime_image_ids[
                            role
                        ]
                    },
                )
            complete = {
                image.content_identity: (
                    {
                        "runtime_image_id": fixture.runtime_image_ids[
                            role
                        ]
                    },
                )
                for role, image in fixture.manifest.image_artifacts.items()
            }
            load_attempts: list[str] = []
            fail_once = True

            def load(arguments, *, timeout):  # noqa: ANN001, ARG001
                nonlocal fail_once
                self.assertEqual(
                    arguments[: len(MODULE.DOCKER_BASE)],
                    list(MODULE.DOCKER_BASE),
                )
                self.assertEqual(
                    arguments[
                        len(MODULE.DOCKER_BASE) : len(MODULE.DOCKER_BASE) + 2
                    ],
                    ["image", "load"],
                )
                load_attempts.append(Path(arguments[-1]).name)
                if (
                    fail_once
                    and len(load_attempts) == 3
                ):
                    fail_once = False
                    raise MODULE.ProductionOperationError(
                        "simulated interrupted Docker load"
                    )
                return "loaded"

            with (
                patch.object(MODULE, "_docker_archive_identity"),
                patch.object(
                    MODULE,
                    "_enumerate_local_images",
                    side_effect=[empty, partial, partial, complete],
                ),
                patch.object(MODULE, "_run", side_effect=load),
                patch.object(
                    MODULE,
                    "_validate_runtime_image_set",
                    return_value=[{"status": "validated"}],
                ),
            ):
                with self.assertRaisesRegex(
                    MODULE.ProductionOperationError,
                    "interrupted",
                ):
                    MODULE.load_images(fixture.manifest, paths)
                evidence = MODULE.load_images(fixture.manifest, paths)
            self.assertEqual(evidence, [{"status": "validated"}])
            self.assertEqual(len(load_attempts), 4)
            self.assertEqual(
                load_attempts,
                [
                    "app-image.tar",
                    "postgres-image.tar",
                    "redis-image.tar",
                    "nginx-image.tar",
                ],
            )

    def test_runtime_image_set_rejects_role_swap(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            inspections = {
                fixture.runtime_image_ids["app"]: docker_inspection(
                    fixture.app_archive,
                    fixture.runtime_image_ids["app"],
                ),
                fixture.runtime_image_ids["postgres"]: docker_inspection(
                    fixture.db_archive,
                    fixture.runtime_image_ids["postgres"],
                ),
                fixture.runtime_image_ids["redis"]: docker_inspection(
                    fixture.redis_archive,
                    fixture.runtime_image_ids["redis"],
                ),
                fixture.runtime_image_ids["nginx"]: docker_inspection(
                    fixture.nginx_archive,
                    fixture.runtime_image_ids["nginx"],
                ),
            }
            swapped = dict(fixture.runtime_image_ids)
            swapped["app"], swapped["postgres"] = (
                swapped["postgres"],
                swapped["app"],
            )
            with (
                patch.object(
                    MODULE,
                    "_inspect_local_image",
                    side_effect=lambda image_id: inspections[image_id],
                ),
                self.assertRaises(MODULE.ProductionOperationError),
            ):
                MODULE._validate_runtime_image_set(
                    fixture.manifest,
                    swapped,
                )

    def test_final_prepare_manifest_is_post_stage_and_runtime_id_bound(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            self.assertNotIn("runtime_image_ids", fixture.document)
            self.assertNotIn("runtime", fixture.document)
            for role, image in fixture.document[
                "image_artifacts"
            ].items():
                self.assertNotIn(
                    fixture.runtime_image_ids[role],
                    json.dumps(image, sort_keys=True),
                )

            wrong_stage = json.loads(
                json.dumps(fixture.final_prepare_document)
            )
            wrong_stage["stage_attestation_sha256"] = "f" * 64
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._load_final_prepare_manifest_bytes(
                    json.dumps(wrong_stage).encode(),
                    manifest=fixture.manifest,
                    expected_stage_attestation_sha256=(
                        fixture.stage_attestation_sha256
                    ),
                )

            wrong_runtime = json.loads(
                json.dumps(fixture.final_prepare_document)
            )
            wrong_runtime["runtime_image_ids"]["app"] = (
                "sha256:" + "f" * 64
            )
            parsed = MODULE._load_final_prepare_manifest_bytes(
                json.dumps(wrong_runtime).encode(),
                manifest=fixture.manifest,
                expected_stage_attestation_sha256=(
                    fixture.stage_attestation_sha256
                ),
            )
            with (
                patch.object(
                    MODULE,
                    "_load_final_prepare_archive",
                    return_value=parsed,
                ),
                self.assertRaises(MODULE.ProductionOperationError),
            ):
                MODULE.install_final_prepare_material(
                    fixture.manifest,
                    fixture.final_archive_path,
                    operation_root=fixture.operation_root,
                    expected_stage_attestation_sha256=(
                        fixture.stage_attestation_sha256
                    ),
                    expected_runtime_image_ids=(
                        fixture.runtime_image_ids
                    ),
                    required_uid=os.geteuid(),
                )

            noncanonical = io.BytesIO()
            with tarfile.open(fileobj=noncanonical, mode="w:") as archive:
                for name, payload in (
                    (
                        MODULE.FINAL_PREPARE_MANIFEST_NAME,
                        json.dumps(
                            fixture.final_prepare_document,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode(),
                    ),
                    ("role-compose.yml", fixture.role_compose),
                    ("runtime.env.role", fixture.runtime_env),
                    ("ca.crt", fixture.ca),
                ):
                    member = tarfile.TarInfo(name)
                    member.size = len(payload)
                    member.mode = 0o600
                    member.mtime = 1
                    archive.addfile(member, io.BytesIO(payload))
            noncanonical_path = fixture.root / "noncanonical-final.tar"
            secure_file(noncanonical_path, noncanonical.getvalue())
            with self.assertRaises(MODULE.ProductionOperationError):
                MODULE._load_final_prepare_archive(
                    noncanonical_path,
                    manifest=fixture.manifest,
                    expected_stage_attestation_sha256=(
                        fixture.stage_attestation_sha256
                    ),
                    required_uid=os.geteuid(),
                )

    def test_prepare_database_runs_only_db_restore_migration_and_exact_fence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            paths = MODULE.verify_incoming(
                fixture.manifest,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            materialize_fixture(
                fixture.manifest,
                paths,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            calls: list[list[str]] = []
            database_created = False
            database_running = False
            migration_revision = SOURCE_REVISION
            writer_fenced = False
            lifecycle_events: list[str] = []

            def fake_run(arguments, *, timeout, stdin=-3):  # noqa: ANN001, ARG001
                nonlocal database_created, database_running
                nonlocal migration_revision, writer_fenced
                calls.append(list(arguments))
                joined = " ".join(arguments)
                sql = arguments[-1] if "-Atqc" in arguments else ""
                docker = arguments[len(MODULE.DOCKER_BASE) :]
                if docker[:1] == ["ps"]:
                    return ""
                if " compose " in f" {joined} " and " ps " in f" {joined} ":
                    return "a" * 64 if database_created else ""
                if " create " in f" {joined} ":
                    lifecycle_events.append("compose-create")
                    database_created = True
                    return ""
                if docker[:1] == ["start"]:
                    lifecycle_events.append("docker-start")
                    database_running = True
                    return arguments[-1]
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

            def validate_container(*_args, **_kwargs):  # noqa: ANN002, ANN003
                lifecycle_events.append(
                    "validate-running"
                    if database_running
                    else "validate-stopped"
                )
                return database_running

            def validate_network(*_args, **kwargs):  # noqa: ANN002
                lifecycle_events.append(
                    "network-attached"
                    if kwargs["require_attached"]
                    else "network-preflight"
                )
                return None

            with (
                patch.object(MODULE, "_run", side_effect=fake_run),
                patch.object(MODULE, "_validate_compose_config"),
                patch.object(
                    MODULE,
                    "_validate_database_container",
                    side_effect=validate_container,
                ),
                patch.object(
                    MODULE,
                    "_validate_operation_network",
                    side_effect=validate_network,
                ),
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
            create_calls = [call for call in calls if "create" in call]
            self.assertEqual(len(create_calls), 1)
            self.assertEqual(create_calls[0][-1], "webapp_ir_db")
            self.assertIn("--pull", create_calls[0])
            self.assertIn("never", create_calls[0])
            self.assertIn("--no-recreate", create_calls[0])
            self.assertFalse(any("up" in call for call in calls))
            self.assertEqual(
                lifecycle_events[:6],
                [
                    "network-preflight",
                    "compose-create",
                    "validate-stopped",
                    "network-preflight",
                    "docker-start",
                    "validate-running",
                ],
            )
            self.assertEqual(lifecycle_events[6], "network-attached")
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
            materialize_fixture(
                fixture.manifest,
                paths,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            completed = set(MODULE._PHASES[:6])
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
                    set(MODULE._PHASES[:7])
                    if persisted_migration
                    else completed
                )

                def fake_run(arguments, *, timeout, stdin=-3):  # noqa: ANN001, ARG001
                    nonlocal revision, index_status
                    calls.append(list(arguments))
                    joined = " ".join(arguments)
                    sql = arguments[-1] if "-Atqc" in arguments else ""
                    docker = arguments[len(MODULE.DOCKER_BASE) :]
                    if docker[:1] == ["ps"]:
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
                    patch.object(
                        MODULE,
                        "_validate_database_container",
                        return_value=True,
                    ),
                    patch.object(MODULE, "_validate_operation_network"),
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
            materialize_fixture(
                fixture.manifest,
                paths,
                operation_root=fixture.operation_root,
                required_uid=os.geteuid(),
            )
            graph = MODULE._load_migration_graph(
                fixture.project_root / "releases" / fixture.release_sha
            )
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
            self.assertEqual(
                MODULE._migration_corridor(
                    graph,
                    source_revision=TARGET_REVISION,
                    target_revision=TARGET_REVISION,
                ),
                (TARGET_REVISION,),
            )

    def test_runtime_command_contract_is_isolated(self) -> None:
        self.assertEqual(
            MODULE.DOCKER_BASE,
            (
                "/usr/bin/docker",
                "--host=unix:///run/docker.sock",
            ),
        )
        self.assertEqual(MODULE._SAFE_ENV["DOCKER_CONFIG"], "/nonexistent")
        self.assertEqual(MODULE._SAFE_GIT_ENV["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertIn("--no-replace-objects", MODULE.GIT_BASE)
        self.assertIn("--no-optional-locks", MODULE.GIT_BASE)
        self.assertIn("core.fsmonitor=false", MODULE.GIT_BASE)
        self.assertIn("core.untrackedCache=false", MODULE.GIT_BASE)
        self.assertIn("core.hooksPath=/dev/null", MODULE.GIT_BASE)
        self.assertEqual(MODULE.REPO_ROOT, Path(MODULE.__file__).resolve().parents[1])

    def test_controller_eof_before_mutation_is_rejected(self) -> None:
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        try:
            with self.assertRaisesRegex(
                MODULE.ProductionOperationCancellation,
                "before mutation",
            ):
                with MODULE._controller_authority_guard(read_fd):
                    self.fail("closed authority entered mutation")
        finally:
            os.close(read_fd)

    def test_controller_pipe_rejects_a_locally_retained_writer(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            with self.assertRaisesRegex(
                MODULE.ProductionOperationError,
                "writer is retained locally",
            ):
                with MODULE._controller_authority_guard(read_fd):
                    self.fail("unsafe controller pipe entered mutation")
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_controller_eof_during_command_kills_exact_process(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            pid_path = Path(raw) / "pid"
            with disconnectable_control_pipe() as (
                read_fd,
                holder,
            ):
                def disconnect() -> None:
                    time.sleep(0.15)
                    holder.terminate()
                    holder.wait(timeout=2)

                closer = threading.Thread(target=disconnect)
                closer.start()
                with self.assertRaises(
                    MODULE.ProductionOperationCancellation
                ):
                    with MODULE._controller_authority_guard(read_fd):
                        MODULE._run(
                            [
                                "/usr/bin/python3",
                                "-I",
                                "-B",
                                "-c",
                                (
                                    "from pathlib import Path;"
                                    f"Path({str(pid_path)!r}).write_text("
                                    "__import__('os').getpid().__str__());"
                                    "__import__('time').sleep(60)"
                                ),
                            ],
                            timeout=5,
                        )
                closer.join(timeout=2)
            self.assertFalse(closer.is_alive())
            pid = int(pid_path.read_text(encoding="ascii"))
            deadline = time.monotonic() + 2
            while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertFalse(Path(f"/proc/{pid}").exists())

    def test_controller_signal_guard_is_reentrant_and_restored(self) -> None:
        before = {
            signum: signal.getsignal(signum)
            for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
        }
        with live_control_pipe() as control_fd:
            with self.assertRaises(
                MODULE.ProductionOperationCancellation
            ):
                with MODULE._controller_authority_guard(control_fd):
                    first = signal.getsignal(signal.SIGINT)
                    self.assertTrue(callable(first))
                    try:
                        first(signal.SIGINT, None)
                    except MODULE.ProductionOperationCancellation:
                        second = signal.getsignal(signal.SIGTERM)
                        self.assertTrue(callable(second))
                        second(signal.SIGTERM, None)
                        raise
        self.assertEqual(
            {
                signum: signal.getsignal(signum)
                for signum in before
            },
            before,
        )

    def test_operator_signal_remains_latched_after_retry_catches_it(self) -> None:
        with (
            live_control_pipe() as control_fd,
            patch.object(MODULE.subprocess, "Popen") as spawn,
            self.assertRaisesRegex(
                MODULE.ProductionOperationCancellation,
                "SIGTERM",
            ),
        ):
            with MODULE._controller_authority_guard(control_fd):
                handler = signal.getsignal(signal.SIGTERM)
                self.assertTrue(callable(handler))
                try:
                    handler(signal.SIGTERM, None)
                except MODULE.ProductionOperationError:
                    pass
                MODULE._run(
                    [
                        "/usr/bin/python3",
                        "-I",
                        "-B",
                        "-c",
                        "raise SystemExit(0)",
                    ],
                    timeout=5,
                )
        spawn.assert_not_called()

    def test_controller_eof_is_deferred_until_reconciliation_finishes(
        self,
    ) -> None:
        completed_cleanup = False
        with disconnectable_control_pipe() as (read_fd, holder):
            with self.assertRaisesRegex(
                MODULE.ProductionOperationCancellation,
                "liveness was lost",
            ):
                with MODULE._controller_authority_guard(read_fd) as authority:
                    with MODULE._late_reconciliation_scope():
                        holder.terminate()
                        holder.wait(timeout=2)
                        self.assertTrue(authority.lost_event.wait(timeout=2))
                        time.sleep(0.1)
                        completed_cleanup = True
        self.assertTrue(completed_cleanup)

    def test_incremental_runner_rejects_flood_and_timeout(self) -> None:
        with (
            patch.object(MODULE, "MAX_COMMAND_OUTPUT_BYTES", 64),
            self.assertRaisesRegex(
                MODULE.ProductionOperationError,
                "stdout exceeded",
            ),
        ):
            MODULE._run(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    "-c",
                    "print('x' * 10000)",
                ],
                timeout=5,
            )
        with self.assertRaisesRegex(
            MODULE.ProductionOperationError,
            "timed out",
        ):
            MODULE._run(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    "-c",
                    "import time; time.sleep(5)",
                ],
                timeout=1,
            )

    def test_incremental_runner_kills_detached_setsid_child(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            pid_path = Path(raw) / "detached-pid"
            code = (
                "import subprocess;"
                "from pathlib import Path;"
                "p=subprocess.Popen("
                "['/usr/bin/python3','-I','-B','-c',"
                "'import time;time.sleep(60)'],"
                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
                "stderr=subprocess.DEVNULL,start_new_session=True);"
                f"Path({str(pid_path)!r}).write_text(str(p.pid))"
            )
            with self.assertRaisesRegex(
                MODULE.ProductionOperationError,
                "retained a descendant",
            ):
                MODULE._run(
                    ["/usr/bin/python3", "-I", "-B", "-c", code],
                    timeout=5,
                )
            pid = int(pid_path.read_text(encoding="ascii"))
            deadline = time.monotonic() + 2
            while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertFalse(Path(f"/proc/{pid}").exists())

    def test_incremental_runner_reaps_rapid_double_fork_zombies(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            child_path = Path(raw) / "child"
            grandchild_path = Path(raw) / "grandchild"
            before = MODULE._direct_child_baseline()
            code = (
                "import os,time;"
                "from pathlib import Path;"
                "pid=os.fork();"
                "\nif pid==0:"
                "\n os.setsid(); grand=os.fork();"
                f"\n if grand==0: Path({str(grandchild_path)!r}).write_text("
                "str(os.getpid())); os._exit(0)"
                f"\n Path({str(child_path)!r}).write_text(str(os.getpid()));"
                " os._exit(0)"
                "\ntime.sleep(.3);print('ok')"
            )
            self.assertEqual(
                MODULE._run(
                    ["/usr/bin/python3", "-I", "-B", "-c", code],
                    timeout=5,
                ),
                "ok",
            )
            pids = {
                int(child_path.read_text(encoding="ascii")),
                int(grandchild_path.read_text(encoding="ascii")),
            }
            deadline = time.monotonic() + 2
            while (
                any(Path(f"/proc/{pid}").exists() for pid in pids)
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            self.assertTrue(
                all(not Path(f"/proc/{pid}").exists() for pid in pids)
            )
            self.assertEqual(MODULE._direct_child_baseline(), before)

    def test_incremental_timeout_kills_setsided_double_fork(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            pid_path = Path(raw) / "grandchild-pid"
            temporary_pid_path = Path(raw) / "grandchild-pid.partial"
            sentinel = Path(raw) / "grandchild-survived"
            code = (
                "import os,signal,time\n"
                "if os.fork()==0:\n"
                " os.setsid()\n"
                " if os.fork()!=0: time.sleep(60);os._exit(0)\n"
                " signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                f" with open({str(temporary_pid_path)!r},'w') as f:"
                " f.write(str(os.getpid()));f.flush();os.fsync(f.fileno())\n"
                f" os.replace({str(temporary_pid_path)!r},"
                f"{str(pid_path)!r})\n"
                " time.sleep(1.3)\n"
                f" open({str(sentinel)!r},'wb').write(b'survived')\n"
                " os._exit(0)\n"
                f"while not os.path.exists({str(pid_path)!r}):"
                " time.sleep(0.005)\n"
                "time.sleep(60)\n"
            )
            with (
                patch.object(MODULE, "PROCESS_TERM_GRACE_SECONDS", 0.1),
                patch.object(MODULE, "PROCESS_KILL_GRACE_SECONDS", 0.1),
                patch.object(
                    MODULE,
                    "PROCESS_TREE_QUIESCENCE_SECONDS",
                    0.05,
                ),
                self.assertRaisesRegex(
                    MODULE.ProductionOperationError,
                    "timed out",
                ),
            ):
                MODULE._run(
                    ["/usr/bin/python3", "-I", "-B", "-c", code],
                    timeout=1,
                )
            time.sleep(0.5)
            self.assertTrue(pid_path.is_file())
            self.assertFalse(sentinel.exists())
            self.assertFalse(
                Path(
                    f"/proc/{pid_path.read_text(encoding='ascii')}"
                ).exists()
            )

    def test_identity_bound_signal_refuses_reused_pid(self) -> None:
        identity = MODULE.ProcessIdentity(
            pid=4242,
            parent_pid=os.getpid(),
            process_group=4242,
            session_id=4242,
            start_time=100,
            state="S",
        )
        reused = MODULE.ProcessIdentity(
            pid=4242,
            parent_pid=os.getpid(),
            process_group=4242,
            session_id=4242,
            start_time=101,
            state="S",
        )
        with (
            patch.object(MODULE, "_process_identity", return_value=reused),
            patch.object(MODULE.os, "pidfd_open") as pidfd_open,
        ):
            MODULE._signal_identity(identity, signal.SIGKILL)
        pidfd_open.assert_not_called()

    def test_owned_processes_refuses_reused_root_pid(self) -> None:
        root_identity = MODULE.ProcessIdentity(
            pid=4242,
            parent_pid=os.getpid(),
            process_group=4242,
            session_id=4242,
            start_time=100,
            state="S",
        )
        reused = MODULE.ProcessIdentity(
            pid=4242,
            parent_pid=os.getpid(),
            process_group=4242,
            session_id=4242,
            start_time=101,
            state="S",
        )
        with patch.object(
            MODULE,
            "_process_snapshot",
            return_value={reused.pid: reused},
        ):
            self.assertEqual(
                MODULE._owned_processes(
                    root_identity,
                    baseline_children=frozenset(),
                ),
                (),
            )

    def test_root_pidfd_contains_when_proc_identity_is_unavailable(
        self,
    ) -> None:
        opened: list[tuple[int, int]] = []
        real_pidfd_open = os.pidfd_open

        def capture_pidfd(pid: int, flags: int = 0) -> int:
            descriptor = real_pidfd_open(pid, flags)
            opened.append((pid, descriptor))
            return descriptor

        with (
            patch.object(
                MODULE,
                "_direct_child_baseline",
                return_value=frozenset(),
            ),
            patch.object(
                MODULE,
                "_read_process_identity",
                side_effect=MODULE.ProductionOperationError(
                    "forced subprocess identity failure"
                ),
            ),
            patch.object(
                MODULE.os,
                "pidfd_open",
                side_effect=capture_pidfd,
            ),
            self.assertRaisesRegex(
                MODULE.ProductionOperationError,
                "forced subprocess identity failure",
            ),
        ):
            MODULE._run(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    "-c",
                    "import time;time.sleep(60)",
                ],
                timeout=5,
            )
        self.assertEqual(len(opened), 1)
        pid, descriptor = opened[0]
        self.assertFalse(Path(f"/proc/{pid}").exists())
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_runner_cleanup_closes_streams_and_pidfd_after_baseexception(
        self,
    ) -> None:
        class FatalRunner(BaseException):
            pass

        class FatalSelectorClose(BaseException):
            pass

        with tempfile.TemporaryDirectory() as raw:
            pid_path = Path(raw) / "pid"
            original = FatalRunner("original runner interruption")
            selector = MODULE.selectors.DefaultSelector()
            selector_close_calls: list[bool] = []
            spawned: list[subprocess.Popen[bytes]] = []
            opened: list[int] = []
            real_popen = subprocess.Popen
            real_pidfd_open = os.pidfd_open

            class HostileSelector:
                def register(self, *args, **kwargs):  # noqa: ANN002, ANN003
                    return selector.register(*args, **kwargs)

                def unregister(self, *args, **kwargs):  # noqa: ANN002, ANN003
                    return selector.unregister(*args, **kwargs)

                def select(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
                    deadline = time.monotonic() + 2
                    while (
                        not pid_path.exists()
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.01)
                    raise original

                def close(self) -> None:
                    selector.close()
                    selector_close_calls.append(True)
                    raise FatalSelectorClose("forced selector close failure")

            def capture_spawn(*args, **kwargs):  # noqa: ANN002, ANN003
                process = real_popen(*args, **kwargs)
                spawned.append(process)
                return process

            def capture_pidfd(pid: int, flags: int = 0) -> int:
                descriptor = real_pidfd_open(pid, flags)
                opened.append(descriptor)
                return descriptor

            with (
                patch.object(
                    MODULE.selectors,
                    "DefaultSelector",
                    return_value=HostileSelector(),
                ),
                patch.object(
                    MODULE.subprocess,
                    "Popen",
                    side_effect=capture_spawn,
                ),
                patch.object(
                    MODULE.os,
                    "pidfd_open",
                    side_effect=capture_pidfd,
                ),
                self.assertRaises(FatalRunner) as raised,
            ):
                MODULE._run(
                    [
                        "/usr/bin/python3",
                        "-I",
                        "-B",
                        "-c",
                        (
                            "from pathlib import Path;"
                            f"Path({str(pid_path)!r}).write_text("
                            "__import__('os').getpid().__str__());"
                            "__import__('time').sleep(60)"
                        ),
                    ],
                    timeout=5,
                )
            self.assertIs(raised.exception, original)
            self.assertEqual(selector_close_calls, [True])
            self.assertEqual(len(spawned), 1)
            self.assertTrue(spawned[0].stdout.closed)
            self.assertTrue(spawned[0].stderr.closed)
            self.assertEqual(len(opened), 1)
            with self.assertRaises(OSError):
                os.fstat(opened[0])
            self.assertFalse(Path(f"/proc/{spawned[0].pid}").exists())
            self.assertIn(
                "FatalSelectorClose",
                "\n".join(getattr(raised.exception, "__notes__", ())),
            )

    def test_incremental_runner_preserves_baseexception_after_cleanup_error(
        self,
    ) -> None:
        class FatalRunner(BaseException):
            pass

        with tempfile.TemporaryDirectory() as raw:
            pid_path = Path(raw) / "pid"
            original = FatalRunner("original runner interruption")
            terminate = MODULE._terminate_process_tree

            def abort_select(*_args, **_kwargs):  # noqa: ANN002, ANN003
                deadline = time.monotonic() + 2
                while not pid_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                raise original

            def terminate_then_fail(*args, **kwargs):  # noqa: ANN002, ANN003
                terminate(*args, **kwargs)
                raise MODULE.ProductionOperationError(
                    "simulated containment report failure"
                )

            with (
                patch.object(
                    MODULE.selectors.DefaultSelector,
                    "select",
                    side_effect=abort_select,
                ),
                patch.object(
                    MODULE,
                    "_terminate_process_tree",
                    side_effect=terminate_then_fail,
                ),
                self.assertRaises(FatalRunner) as raised,
            ):
                MODULE._run(
                    [
                        "/usr/bin/python3",
                        "-I",
                        "-B",
                        "-c",
                        (
                            "from pathlib import Path;"
                            f"Path({str(pid_path)!r}).write_text("
                            "__import__('os').getpid().__str__());"
                            "__import__('time').sleep(60)"
                        ),
                    ],
                    timeout=5,
                )
            self.assertIs(raised.exception, original)
            pid = int(pid_path.read_text(encoding="ascii"))
            self.assertFalse(Path(f"/proc/{pid}").exists())
            self.assertIn(
                "containment cleanup also failed closed",
                "\n".join(getattr(raised.exception, "__notes__", ())),
            )

    def test_one_shot_cleanup_runs_for_baseexception_reconciliation(self) -> None:
        class FatalAudit(BaseException):
            pass

        prefix = [
            *MODULE.DOCKER_BASE,
            "compose",
            "--env-file",
            "/tmp/runtime.env",
            "--file",
            "/tmp/compose.yml",
        ]
        manifest = Mock()
        manifest.operation_id = OPERATION_ID
        manifest.services = {"database": "database"}
        canonical = Mock()
        canonical.project_root = Path("/tmp/project")
        canonical.compose = Path("/tmp/compose.yml")
        canonical.runtime_env = Path("/tmp/runtime.env")
        cleanup_depths: list[int] = []

        def cleanup(*_args, **_kwargs):  # noqa: ANN002, ANN003
            cleanup_depths.append(MODULE._LATE_RECONCILIATION_DEPTH)
            raise MODULE.ProductionOperationError(
                "simulated reconciliation failure"
            )

        with (
            patch.object(
                MODULE,
                "_canonical_operation_paths",
                return_value=canonical,
            ),
            patch.object(MODULE, "_oneoff_ids", return_value=[]),
            patch.object(MODULE, "_run", side_effect=FatalAudit("stop")),
            patch.object(
                MODULE,
                "_cleanup_operation_oneoffs",
                side_effect=cleanup,
            ) as reconciler,
            self.assertRaises(FatalAudit) as raised,
        ):
            MODULE._compose_one_shot(
                prefix,
                manifest,
                profile="prepare",
                service="oneoff",
                timeout=30,
            )
        reconciler.assert_called_once()
        self.assertEqual(cleanup_depths, [1])
        self.assertIn(
            "reconciliation also failed closed",
            "\n".join(getattr(raised.exception, "__notes__", ())),
        )

    def test_late_image_reconciliation_preserves_original_baseexception(
        self,
    ) -> None:
        class FatalLoad(BaseException):
            pass

        manifest = Mock()
        manifest.release_sha = "a" * 40
        manifest.postgres_runtime_uid = 999
        manifest.postgres_runtime_gid = 999
        manifest.image_artifacts = {
            role: Mock(
                artifact_kind=f"{role}-image",
                content_identity=f"identity-{role}",
            )
            for role in MODULE.IMAGE_ROLES
        }
        paths = {
            f"{role}-image": Path(f"/tmp/{role}-image.tar")
            for role in MODULE.IMAGE_ROLES
        }
        empty = {
            f"identity-{role}": ()
            for role in MODULE.IMAGE_ROLES
        }
        original = FatalLoad("original image-load interruption")
        with (
            patch.object(MODULE, "_docker_archive_identity"),
            patch.object(
                MODULE,
                "_enumerate_local_images",
                side_effect=[
                    empty,
                    MODULE.ProductionOperationError(
                        "late reconciliation failed"
                    ),
                ],
            ),
            patch.object(MODULE, "_run", side_effect=original),
            patch.object(MODULE, "LATE_IMAGE_RECONCILIATION_ATTEMPTS", 1),
            self.assertRaises(FatalLoad) as raised,
        ):
            MODULE.load_images(manifest, paths)
        self.assertIs(raised.exception, original)

    def test_execute_resumes_from_durable_phase_without_reloading_images(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            staged_images = list(fixture.image_stage["images"])

            with (
                patch.object(
                    MODULE,
                    "load_images",
                    return_value=staged_images,
                ),
                patch.object(
                    MODULE,
                    "_validate_runtime_image_set",
                    return_value=staged_images,
                ),
                live_control_pipe() as control_fd,
            ):
                MODULE.execute_stage(
                    fixture.manifest,
                    operation_root=fixture.operation_root,
                    required_uid=os.geteuid(),
                    confirm=MODULE.stage_confirmation_phrase(
                        fixture.manifest
                    ),
                    control_fd=control_fd,
                )
            secure_file(
                fixture.incoming
                / MODULE.FINAL_PREPARE_DESTINATION_NAME,
                fixture.runtime_archive,
            )

            class FatalAudit(BaseException):
                pass

            def crash_after_database_start(*args, **kwargs):  # noqa: ANN002, ANN003
                kwargs["phase_done"](
                    "database-started",
                    {"container_id": "a" * 64},
                )
                raise FatalAudit("injected crash")

            with (
                patch.object(
                    MODULE,
                    "_validate_runtime_image_set",
                    return_value=staged_images,
                ),
                patch.object(
                    MODULE,
                    "prepare_database",
                    side_effect=crash_after_database_start,
                ),
                live_control_pipe() as control_fd,
                self.assertRaises(FatalAudit),
            ):
                MODULE.execute(
                    fixture.manifest,
                    operation_root=fixture.operation_root,
                    required_uid=os.geteuid(),
                    confirm=MODULE.confirmation_phrase(fixture.manifest),
                    control_fd=control_fd,
                )

            state = MODULE._load_or_create_state(
                fixture.manifest,
                operation_root=fixture.operation_root,
            )
            self.assertEqual(
                state["completed_phases"],
                list(MODULE._PHASES[:5]),
            )
            self.assertEqual(
                stat.S_IMODE(
                    (fixture.operation_root / "operation-state.json").stat().st_mode
                ),
                0o600,
            )
            postgres_path = fixture.data_root / "webapp-ir" / "postgres"
            original_path_stat = Path.stat
            root_metadata = original_path_stat(
                postgres_path,
                follow_symlinks=False,
            )
            runtime_metadata = os.stat_result(
                (
                    root_metadata.st_mode,
                    root_metadata.st_ino,
                    root_metadata.st_dev,
                    root_metadata.st_nlink,
                    999,
                    999,
                    root_metadata.st_size,
                    root_metadata.st_atime,
                    root_metadata.st_mtime,
                    root_metadata.st_ctime,
                )
            )

            def runtime_postgres_stat(path, *args, **kwargs):  # noqa: ANN001
                if (
                    path == postgres_path
                    and kwargs.get("follow_symlinks", True) is False
                ):
                    return runtime_metadata
                return original_path_stat(path, *args, **kwargs)

            def complete_resume(*args, **kwargs):  # noqa: ANN002, ANN003
                self.assertEqual(
                    kwargs["completed_phases"],
                    set(MODULE._PHASES[:5]),
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
                patch.object(
                    MODULE,
                    "materialize_stage",
                ) as rematerialize,
                patch.object(MODULE, "load_images") as reload_images,
                patch.object(
                    MODULE,
                    "_validate_runtime_image_set",
                    return_value=staged_images,
                ),
                patch.object(Path, "stat", new=runtime_postgres_stat),
                patch.object(
                    MODULE,
                    "prepare_database",
                    side_effect=complete_resume,
                ),
                live_control_pipe() as control_fd,
            ):
                result = MODULE.execute(
                    fixture.manifest,
                    operation_root=fixture.operation_root,
                    required_uid=os.geteuid(),
                    confirm=MODULE.confirmation_phrase(fixture.manifest),
                    control_fd=control_fd,
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
