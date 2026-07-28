from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
import yaml

from core.docker_image_identity import (
    image_content_descriptor_from_archive_config,
)
from scripts import install_production_shadow_precommit_inputs as MODULE
from scripts import production_shadow_precommit_worker as WORKER
from scripts import produce_production_shadow_prepare_material as PREPARE
from scripts import produce_production_shadow_source_snapshot as SOURCE
from scripts.render_three_site_production_shadow_role_compose import (
    canonical_role_compose_bytes,
    render_role_compose,
)


OPERATION_ID = "22222222-2222-4222-8222-222222222222"
RELEASE_SHA = "a" * 40
RELEASE_TREE_SHA = "b" * 40


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def secure_file(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(mode)


def tar_bytes(
    files: dict[str, bytes],
    *,
    gzip: bool = False,
    unsafe_link: bool = False,
) -> bytes:
    output = io.BytesIO()
    mode = "w:gz" if gzip else "w:"
    with tarfile.open(fileobj=output, mode=mode, format=tarfile.GNU_FORMAT) as archive:
        for name, payload in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            member.mode = 0o600
            member.mtime = 0
            archive.addfile(member, io.BytesIO(payload))
        if unsafe_link:
            member = tarfile.TarInfo("unsafe-link")
            member.type = tarfile.SYMTYPE
            member.linkname = "../outside"
            member.uid = 0
            member.gid = 0
            member.mode = 0o600
            member.mtime = 0
            archive.addfile(member)
    return output.getvalue()


def certificate(operation_id: str) -> bytes:
    key = ed25519.Ed25519PrivateKey.generate()
    name = x509.Name(
        [
            x509.NameAttribute(
                x509.NameOID.COMMON_NAME,
                f"production-shadow-dr-ca-{operation_id}",
            )
        ]
    )
    now = datetime.now(timezone.utc)
    value = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=90))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=0),
            critical=True,
        )
        .sign(key, algorithm=None)
    )
    return value.public_bytes(serialization.Encoding.PEM)


def docker_archive(
    kind: str,
    release_sha: str,
) -> tuple[bytes, str, dict[str, object], str, dict[str, object]]:
    labels: dict[str, str] = {}
    if kind in {"app", "postgres"}:
        labels["org.opencontainers.image.revision"] = release_sha
    if kind == "postgres":
        labels["trading-bot.postgres.runtime-uid"] = "70"
        labels["trading-bot.postgres.runtime-gid"] = "70"
    layer = f"fixture-layer:{kind}".encode("ascii")
    layer_digest = "sha256:" + hashlib.sha256(layer).hexdigest()
    config = {
        "architecture": "amd64",
        "os": "linux",
        "created": "2026-07-27T00:00:00Z",
        "rootfs": {"type": "layers", "diff_ids": [layer_digest]},
        "config": {
            "Labels": labels,
            "Env": [f"FIXTURE_ROLE={kind}"],
        },
    }
    config_payload = canonical(config)
    image_id = "sha256:" + hashlib.sha256(config_payload).hexdigest()
    manifest = canonical(
        [
            {
                "Config": f"{image_id.removeprefix('sha256:')}.json",
                "RepoTags": [],
                "Layers": ["layer/layer.tar"],
            }
        ]
    )
    payload = tar_bytes(
        {
            "manifest.json": manifest,
            f"{image_id.removeprefix('sha256:')}.json": config_payload,
            "layer/layer.tar": layer,
        }
    )
    descriptor, content_identity = image_content_descriptor_from_archive_config(
        config
    )
    inspect = {
        "Id": image_id,
        "Os": "linux",
        "Architecture": "amd64",
        "Created": config["created"],
        "Config": config["config"],
        "RootFS": {
            "Type": "layers",
            "Layers": [layer_digest],
        },
        "RepoTags": [],
        "RepoDigests": [],
    }
    return payload, image_id, descriptor, content_identity, inspect


def git_blob(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


class InstallFixture:
    def __init__(self, root: Path, role: str) -> None:
        self.root = root
        self.role = role
        self.prefix_patch = mock.patch.multiple(
            WORKER,
            PROJECT_ROOT_PREFIX=root / "project",
            DATA_ROOT_PREFIX=root / "data",
            SECRET_ROOT_PREFIX=root / "secret",
        )
        self.prefix_patch.start()
        for prefix in (
            WORKER.PROJECT_ROOT_PREFIX,
            WORKER.DATA_ROOT_PREFIX,
            WORKER.SECRET_ROOT_PREFIX,
        ):
            prefix.mkdir(mode=0o700)
        self.paths = WORKER.operation_paths(
            OPERATION_ID,
            RELEASE_SHA,
            role,
        )
        (self.paths.project_root / "incoming").mkdir(
            parents=True,
            mode=0o700,
        )
        self.paths.release_root.mkdir(parents=True, mode=0o700)

        self.release_sources = {
            "scripts/production_shadow_precommit_worker.py": (
                b"worker fixture\n"
            ),
            "scripts/produce_production_shadow_readonly_acceptance.py": (
                b"acceptance fixture\n"
            ),
        }
        for relative, payload in self.release_sources.items():
            secure_file(
                self.paths.release_root / relative,
                payload,
                mode=0o644,
            )
        self.bundle = b"exact release bundle fixture"
        secure_file(self.paths.artifacts["release-bundle"], self.bundle)

        self.image_inspections: dict[str, dict[str, object]] = {}
        self.image_rows: dict[str, dict[str, object]] = {}
        self.runtime_ids: dict[str, str] = {}
        self.artifact_rows: dict[str, dict[str, object]] = {
            "release-bundle": self.artifact_row(self.bundle),
        }
        for kind in sorted(WORKER.IMAGE_FIELDS):
            (
                payload,
                image_id,
                descriptor,
                content_identity,
                inspect,
            ) = docker_archive(kind, RELEASE_SHA)
            secure_file(
                self.paths.artifacts[f"{kind}-image-archive"],
                payload,
            )
            self.runtime_ids[kind] = image_id
            self.image_inspections[image_id] = inspect
            self.image_rows[kind] = {
                "archive_sha256": hashlib.sha256(payload).hexdigest(),
                "archive_bytes": len(payload),
                "config_digest": image_id,
                "content_descriptor": descriptor,
                "content_identity": content_identity,
            }
            self.artifact_rows[f"{kind}-image-archive"] = self.artifact_row(
                payload
            )

        canonical_compose = yaml.safe_load(
            (
                Path(__file__).resolve().parents[1]
                / "deploy"
                / "production"
                / "docker-compose.three-site-shadow.yml"
            ).read_text(encoding="utf-8")
        )
        rendered = render_role_compose(
            canonical_compose,
            role=role.replace("_", "-"),
            scope="precommit",
        )
        self.compose = canonical_role_compose_bytes(rendered)
        environment_values = {
            "PRODUCTION_SHADOW_OPERATION_ID": OPERATION_ID,
            "PRODUCTION_SHADOW_PROJECT": self.paths.project_base,
            "PRODUCTION_SHADOW_CGROUP_PARENT": self.paths.project_base,
            "PRODUCTION_SHADOW_PROJECT_ROOT": str(self.paths.project_root),
            "PRODUCTION_SHADOW_RELEASE_ROOT": str(self.paths.release_root),
            "PRODUCTION_SHADOW_DATA_ROOT": str(self.paths.data_root),
            "PRODUCTION_SHADOW_SECRET_ROOT": str(self.paths.secret_root),
            "PRODUCTION_SHADOW_RELEASE_SHA": RELEASE_SHA,
            "PRODUCTION_SHADOW_APP_IMAGE_ID": self.runtime_ids["app"],
            "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID": self.runtime_ids[
                "postgres"
            ],
            "PRODUCTION_SHADOW_REDIS_IMAGE_ID": self.runtime_ids["redis"],
            "PRODUCTION_SHADOW_NGINX_IMAGE_ID": self.runtime_ids["nginx"],
            WORKER.ROLE_SERVICES[role]["database_env"]: (
                f"{role}_shadow"
            ),
        }
        self.environment = (
            "".join(
                f"{name}={environment_values[name]}\n"
                for name in sorted(environment_values)
            )
        ).encode("ascii")
        self.ca = certificate(OPERATION_ID)
        role_path = role.replace("_", "-")
        entries = [
            {
                "archive_path": name,
                "destination": destination,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "mode": "0600",
            }
            for name, destination, payload in (
                (
                    "role-compose.yml",
                    f"rendered/{role_path}/docker-compose.yml",
                    self.compose,
                ),
                (
                    "runtime.env.role",
                    f"secrets/{role_path}/runtime.env.role",
                    self.environment,
                ),
                ("ca.crt", "secrets/tls/ca.crt", self.ca),
            )
        ]
        internal = {
            "schema": PREPARE.FI_FINAL_PREPARE_SCHEMA,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "operation_manifest_sha256": "1" * 64,
            "stage_attestation_sha256": "2" * 64,
            "role": role,
            "runtime_image_ids": self.runtime_ids,
            "entries": entries,
            "required_env_keys": sorted(environment_values),
        }
        self.role_archive = tar_bytes(
            {
                PREPARE.FINAL_PREPARE_MANIFEST_NAME: canonical(internal),
                "role-compose.yml": self.compose,
                "runtime.env.role": self.environment,
                "ca.crt": self.ca,
            }
        )
        self.input_root = root / "inputs"
        self.input_root.mkdir(mode=0o700)
        self.role_material_path = (
            self.input_root / f"role-material-{role_path}.tar"
        )
        secure_file(self.role_material_path, self.role_archive)
        self.artifact_rows["role-material"] = self.artifact_row(
            self.role_archive
        )

        self.source_root = root / "source"
        self.source_root.mkdir(mode=0o700)
        self.database = b"database dump fixture\n"
        self.uploads = tar_bytes(
            {"uploads/file.txt": b"upload fixture"},
            gzip=True,
        )
        self.audit = tar_bytes(
            {"audit/event.jsonl": b'{"fixture":true}\n'},
            gzip=True,
        )
        source_payloads = {
            "database-backup": self.database,
            "uploads-archive": self.uploads,
            "audit-archive": self.audit,
        }
        source_filenames = {
            "database-backup": "database.dump",
            "uploads-archive": "uploads.tar.gz",
            "audit-archive": "audit.tar.gz",
        }
        trees = {
            "database-backup": None,
            "uploads-archive": "3" * 64,
            "audit-archive": "4" * 64,
        }
        for kind, payload in source_payloads.items():
            secure_file(
                self.source_root / source_filenames[kind],
                payload,
            )
            self.artifact_rows[kind] = self.artifact_row(
                payload,
                tree=trees[kind],
            )

        self.source_database = {
            "alembic_revision": "source_1",
            "fingerprint_algorithm": (
                "pg-copy-jsonl-sha256-canonical-session-v1"
            ),
            "database_fingerprint_sha256": "5" * 64,
            "row_count": 10,
            "table_count": 2,
        }
        self.source_document = self.build_source_document()
        self.source_manifest_path = (
            self.source_root / SOURCE.MANIFEST_FILE
        )
        secure_file(
            self.source_manifest_path,
            canonical(self.source_document),
        )

        self.precommit_document = {
            "schema": WORKER.MANIFEST_SCHEMA,
            "operation_id": OPERATION_ID,
            "role": role,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": RELEASE_TREE_SHA,
            "controller_manifest_sha256": "6" * 64,
            "approval_sha256": "7" * 64,
            "role_material_sha256": hashlib.sha256(
                self.role_archive
            ).hexdigest(),
            "canonical_compose_sha256": "8" * 64,
            "role_compose_sha256": hashlib.sha256(
                self.compose
            ).hexdigest(),
            "environment_sha256": hashlib.sha256(
                self.environment
            ).hexdigest(),
            "worker_sha256": hashlib.sha256(
                self.release_sources[
                    "scripts/production_shadow_precommit_worker.py"
                ]
            ).hexdigest(),
            "acceptance_producer_sha256": hashlib.sha256(
                self.release_sources[
                    "scripts/produce_production_shadow_readonly_acceptance.py"
                ]
            ).hexdigest(),
            "image_artifacts": self.image_rows,
            "runtime_image_ids": self.runtime_ids,
            "artifacts": self.artifact_rows,
            "source_database": self.source_database,
            "target_migration_revision": "target_2",
            "postgres_runtime_uid": 70,
            "postgres_runtime_gid": 70,
        }
        self.precommit_path = self.input_root / "precommit-operation.json"
        self.write_precommit()

    @staticmethod
    def artifact_row(
        payload: bytes,
        *,
        tree: str | None = None,
    ) -> dict[str, object]:
        return {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "restored_tree_sha256": tree,
        }

    def build_source_document(self) -> dict[str, object]:
        project = SOURCE.SOURCE_PROJECTS[self.role]
        image_references = {
            **SOURCE.SOURCE_IMAGE_REFERENCES[self.role],
            "restore_postgres": (
                f"trading_bot_postgres_boottime:15-{RELEASE_SHA}"
            ),
        }
        source_image_ids = {
            "database": "sha256:" + "9" * 64,
            "application": "sha256:" + "a" * 64,
            "redis": "sha256:" + "b" * 64,
            "restore_postgres": self.runtime_ids["postgres"],
        }
        images = {
            kind: {
                "reference": image_references[kind],
                "image_id": source_image_ids[kind],
            }
            for kind in SOURCE.IMAGE_KEYS
        }
        volume_names = {
            kind: f"{project}_{suffix}"
            for kind, suffix in SOURCE.VOLUME_SUFFIXES.items()
        }
        volumes = {
            kind: {
                "name": volume_names[kind],
                "driver": "local",
                "mountpoint": (
                    f"/var/lib/docker/volumes/{volume_names[kind]}/_data"
                ),
                "labels_sha256": hashlib.sha256(
                    f"labels:{kind}".encode()
                ).hexdigest(),
                "options_sha256": hashlib.sha256(
                    f"options:{kind}".encode()
                ).hexdigest(),
            }
            for kind in SOURCE.VOLUME_KEYS
        }
        containers: dict[str, dict[str, object]] = {}
        for index, kind in enumerate(
            ("database", "application", "redis"),
            start=1,
        ):
            mounts = {
                volume_kind: {
                    "name": volume_names[volume_kind],
                    "source": volumes[volume_kind]["mountpoint"],
                    "destination": destination,
                    "rw": True,
                }
                for volume_kind, destination in SOURCE.SOURCE_MOUNTS[
                    kind
                ].items()
            }
            containers[kind] = {
                "id": str(index) * 64,
                "name": SOURCE.SOURCE_CONTAINERS[kind],
                "image_id": source_image_ids[kind],
                "image_reference": image_references[kind],
                "project": project,
                "service": SOURCE.SOURCE_SERVICES[kind],
                "running": True,
                "started_at": "2026-07-27T00:00:00Z",
                "restart_count": 0,
                "mounts": mounts,
                "other_mount_count": 0,
                "other_mounts_sha256": hashlib.sha256(b"[]").hexdigest(),
            }
        public = {
            "containers": containers,
            "images": images,
            "volumes": volumes,
        }
        source = {
            **public,
            "identity_sha256": hashlib.sha256(canonical(public)).hexdigest(),
        }
        return {
            "schema": SOURCE.MANIFEST_SCHEMA,
            "status": "source-snapshot-created",
            "operation_id": OPERATION_ID,
            "role": self.role,
            "mode": "live-baseline",
            "release_sha": RELEASE_SHA,
            "legacy_release_sha": "c" * 40,
            "source_project": project,
            "controller_manifest_sha256": "6" * 64,
            "approval_sha256": "7" * 64,
            "binding_sha256": "8" * 64,
            "freeze_evidence_sha256": None,
            "source": source,
            "artifacts": {
                kind: self.artifact_rows[kind]
                for kind in MODULE.SOURCE_ARTIFACT_FILES
            },
            "source_database": self.source_database,
            "file_snapshots": {
                "uploads": {
                    "source_volume": volume_names["uploads"],
                    "pre_tree_sha256": "3" * 64,
                    "archive_tree_sha256": "3" * 64,
                    "post_tree_sha256": "3" * 64,
                    "member_count": 1,
                    "expanded_bytes": len(b"upload fixture"),
                    "stable_attempt": 1,
                },
                "audit": {
                    "source_volume": volume_names["audit"],
                    "pre_tree_sha256": "4" * 64,
                    "archive_tree_sha256": "4" * 64,
                    "post_tree_sha256": "4" * 64,
                    "member_count": 1,
                    "expanded_bytes": len(b'{"fixture":true}\n'),
                    "stable_attempt": 1,
                },
            },
            "redis_rollback_only": {
                "policy": "sealed-rollback-evidence-only",
                "source_volume": volume_names["redis"],
                "tree_sha256": "d" * 64,
                "metadata_sha256": "e" * 64,
                "member_count": 1,
                "bytes": 1,
                "stable_attempt": 1,
                "archive_created": False,
                "restore": False,
            },
            "restore_drill": {
                "status": "passed",
                "postgres_image_reference": (
                    f"trading_bot_postgres_boottime:15-{RELEASE_SHA}"
                ),
                "postgres_image_id": self.runtime_ids["postgres"],
                "postgres_runtime_uid": 70,
                "postgres_runtime_gid": 70,
                "scratch_postgres_system_id": "123456789012345678",
                "single_transaction": True,
                "network_mode": "none",
                "pull_policy": "never",
                "source_or_current_mounted": False,
                "recovered_prior_residue": False,
                "scratch_resources_removed": True,
                "zero_residue": True,
            },
            "source_mutated": False,
            "current_mutated": False,
            "source_stopped_or_restarted": False,
            "redis_restored": False,
        }

    def write_precommit(self) -> None:
        secure_file(
            self.precommit_path,
            canonical(self.precommit_document) + b"\n",
        )

    def release_run(self, arguments: list[str], **_kwargs) -> str:
        if arguments[:3] == [WORKER.GIT, "bundle", "list-heads"]:
            return f"{RELEASE_SHA} refs/heads/main"
        if arguments[0] != WORKER.GIT:
            raise AssertionError(f"unexpected non-Git command: {arguments}")
        if "hash-object" in arguments:
            return git_blob(Path(arguments[-1]).read_bytes())
        if "ls-tree" in arguments:
            relative = arguments[-1]
            return (
                f"100644 blob {git_blob(self.release_sources[relative])}"
                f"\t{relative}"
            )
        if arguments[-1] == "HEAD^{tree}":
            return RELEASE_TREE_SHA
        if arguments[-1] == "--abbrev-ref":
            raise AssertionError(f"malformed Git invocation: {arguments}")
        if "--abbrev-ref" in arguments:
            return "HEAD"
        if arguments[-1] == "--show-toplevel":
            return str(self.paths.release_root)
        if arguments[-1] == "HEAD":
            return RELEASE_SHA
        if "status" in arguments or arguments[-1] == "remote":
            return ""
        raise AssertionError(f"unexpected Git invocation: {arguments}")

    def worker_run(self, arguments: list[str], **kwargs) -> str:
        if arguments[0] == WORKER.GIT:
            return self.release_run(arguments, **kwargs)
        if arguments[:3] == [WORKER.DOCKER, "image", "inspect"]:
            return json.dumps(
                [self.image_inspections[arguments[3]]],
                sort_keys=True,
                separators=(",", ":"),
            )
        if arguments[0] == WORKER.DOCKER and "config" in arguments:
            return ""
        if arguments[:2] == [WORKER.DOCKER, "ps"]:
            return ""
        if arguments[0] == WORKER.DOCKER and "ps" in arguments:
            return ""
        raise AssertionError(f"unexpected worker command: {arguments}")

    def close(self) -> None:
        self.prefix_patch.stop()


class ProductionShadowPrecommitInputInstallerTests(unittest.TestCase):
    def test_plan_apply_idempotence_and_worker_integration_for_both_fi_roles(
        self,
    ) -> None:
        for role in WORKER.ROLE_NAMES:
            with self.subTest(role=role), tempfile.TemporaryDirectory() as raw:
                fixture = InstallFixture(Path(raw), role)
                try:
                    source_before = {
                        path: (
                            path.stat().st_ino,
                            path.read_bytes(),
                            path.stat().st_mtime_ns,
                        )
                        for path in (
                            fixture.source_manifest_path,
                            fixture.source_root / "database.dump",
                            fixture.source_root / "uploads.tar.gz",
                            fixture.source_root / "audit.tar.gz",
                        )
                    }
                    with mock.patch.object(
                        WORKER,
                        "_run",
                        side_effect=fixture.release_run,
                    ):
                        planned = MODULE.execute_installation(
                            role=role,
                            precommit_manifest=fixture.precommit_path,
                            role_material=fixture.role_material_path,
                            source_snapshot_manifest=(
                                fixture.source_manifest_path
                            ),
                        )
                    self.assertEqual(planned["status"], "planned")
                    self.assertFalse(fixture.paths.data_root.exists())
                    self.assertFalse(fixture.paths.secret_root.exists())
                    self.assertFalse(fixture.paths.compose.exists())

                    required = MODULE.confirmation_phrase(
                        OPERATION_ID,
                        role,
                        RELEASE_SHA,
                    )
                    with mock.patch.object(
                        WORKER,
                        "_run",
                        side_effect=fixture.release_run,
                    ):
                        installed = MODULE.execute_installation(
                            role=role,
                            precommit_manifest=fixture.precommit_path,
                            role_material=fixture.role_material_path,
                            source_snapshot_manifest=(
                                fixture.source_manifest_path
                            ),
                            apply=True,
                            confirm=required,
                        )
                    self.assertEqual(installed["status"], "installed")
                    loaded = WORKER.load_manifest(fixture.paths.manifest)
                    self.assertEqual(loaded.role, role)
                    self.assertTrue(
                        fixture.paths.manifest.read_bytes().endswith(b"\n")
                    )
                    self.assertEqual(
                        fixture.paths.artifacts["database-backup"].read_bytes(),
                        fixture.database,
                    )
                    self.assertEqual(
                        fixture.paths.artifacts["uploads-archive"].read_bytes(),
                        fixture.uploads,
                    )
                    self.assertEqual(
                        fixture.paths.artifacts["audit-archive"].read_bytes(),
                        fixture.audit,
                    )
                    for spec in (
                        fixture.paths.manifest,
                        fixture.paths.environment,
                        fixture.paths.compose,
                        fixture.paths.secret_root / "tls" / "ca.crt",
                        *fixture.paths.artifacts.values(),
                    ):
                        self.assertEqual(stat_mode(spec), 0o600)
                        self.assertEqual(spec.stat().st_nlink, 1)
                    self.assertEqual(
                        source_before,
                        {
                            path: (
                                path.stat().st_ino,
                                path.read_bytes(),
                                path.stat().st_mtime_ns,
                            )
                            for path in source_before
                        },
                    )

                    with mock.patch.object(
                        WORKER,
                        "_run",
                        side_effect=fixture.release_run,
                    ):
                        repeated_plan = MODULE.preflight_installation(
                            role=role,
                            precommit_manifest=fixture.precommit_path,
                            role_material=fixture.role_material_path,
                            source_snapshot_manifest=(
                                fixture.source_manifest_path
                            ),
                        )
                    installed_inodes = {
                        spec.path: spec.path.stat().st_ino
                        for spec in repeated_plan.outputs
                    }
                    with mock.patch.object(
                        WORKER,
                        "_run",
                        side_effect=fixture.release_run,
                    ):
                        repeated = MODULE.execute_installation(
                            role=role,
                            precommit_manifest=fixture.precommit_path,
                            role_material=fixture.role_material_path,
                            source_snapshot_manifest=(
                                fixture.source_manifest_path
                            ),
                            apply=True,
                            confirm=required,
                        )
                    self.assertEqual(repeated["status"], "already-installed")
                    self.assertEqual(
                        installed_inodes,
                        {
                            path: path.stat().st_ino
                            for path in installed_inodes
                        },
                    )

                    with mock.patch.object(
                        WORKER,
                        "_run",
                        side_effect=fixture.worker_run,
                    ):
                        evidence = WORKER._verify_installation(  # noqa: SLF001
                            loaded,
                            fixture.paths,
                        )
                    self.assertTrue(evidence["exact_release_verified"])
                    self.assertTrue(evidence["zero_oneoff_residue"])
                finally:
                    fixture.close()

    def test_wrong_confirmation_is_non_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = InstallFixture(Path(raw), "bot_fi")
            try:
                with (
                    mock.patch.object(
                        WORKER,
                        "_run",
                        side_effect=fixture.release_run,
                    ),
                    self.assertRaisesRegex(
                        MODULE.PrecommitInputInstallError,
                        "requires --confirm",
                    ),
                ):
                    MODULE.execute_installation(
                        role=fixture.role,
                        precommit_manifest=fixture.precommit_path,
                        role_material=fixture.role_material_path,
                        source_snapshot_manifest=fixture.source_manifest_path,
                        apply=True,
                        confirm="wrong",
                    )
                self.assertFalse(fixture.paths.data_root.exists())
                self.assertFalse(fixture.paths.secret_root.exists())
                self.assertFalse(fixture.paths.compose.exists())
            finally:
                fixture.close()

    def test_role_archive_rejects_non_regular_member_even_when_bound(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = InstallFixture(Path(raw), "bot_fi")
            try:
                unsafe = tar_bytes(
                    {
                        PREPARE.FINAL_PREPARE_MANIFEST_NAME: canonical(
                            {
                                "schema": PREPARE.FI_FINAL_PREPARE_SCHEMA,
                                "operation_id": OPERATION_ID,
                                "release_sha": RELEASE_SHA,
                                "operation_manifest_sha256": "1" * 64,
                                "stage_attestation_sha256": "2" * 64,
                                "role": fixture.role,
                                "runtime_image_ids": fixture.runtime_ids,
                                "entries": [],
                                "required_env_keys": [],
                            }
                        ),
                        "role-compose.yml": fixture.compose,
                        "runtime.env.role": fixture.environment,
                        "ca.crt": fixture.ca,
                    },
                    unsafe_link=True,
                )
                secure_file(fixture.role_material_path, unsafe)
                digest = hashlib.sha256(unsafe).hexdigest()
                fixture.precommit_document["role_material_sha256"] = digest
                fixture.precommit_document["artifacts"]["role-material"] = (
                    fixture.artifact_row(unsafe)
                )
                fixture.write_precommit()
                with (
                    mock.patch.object(
                        WORKER,
                        "_run",
                        side_effect=fixture.release_run,
                    ),
                    self.assertRaisesRegex(
                        MODULE.PrecommitInputInstallError,
                        "member closure",
                    ),
                ):
                    MODULE.preflight_installation(
                        role=fixture.role,
                        precommit_manifest=fixture.precommit_path,
                        role_material=fixture.role_material_path,
                        source_snapshot_manifest=fixture.source_manifest_path,
                    )
            finally:
                fixture.close()


def stat_mode(path: Path) -> int:
    return path.stat(follow_symlinks=False).st_mode & 0o7777


if __name__ == "__main__":
    unittest.main()
