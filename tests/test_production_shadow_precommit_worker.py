from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import tarfile
import unittest
from unittest import mock

from core.docker_image_identity import verify_content_descriptor
from scripts import production_shadow_precommit_worker as MODULE
from scripts.render_three_site_production_shadow_role_compose import (
    canonical_role_compose_bytes,
    render_role_compose,
)


OPERATION_ID = "22222222-2222-4222-8222-222222222222"
RELEASE_SHA = "a" * 40
RELEASE_TREE_SHA = "b" * 40


def image_content_binding(seed: str) -> dict:
    descriptor = {
        "architecture": "amd64",
        "os": "linux",
        "created": f"2026-07-27T00:00:0{seed}Z",
        "config_sha256": "sha256:" + seed * 64,
        "rootfs_type": "layers",
        "rootfs_layers": ["sha256:" + seed * 64],
    }
    return {
        "content_descriptor": descriptor,
        "content_identity": verify_content_descriptor(descriptor),
    }


class PrecommitFixture:
    def __init__(self, root: Path, role: str = "bot_fi") -> None:
        self.root = root
        self.role = role
        self.prefix_patch = mock.patch.multiple(
            MODULE,
            PROJECT_ROOT_PREFIX=root / "project",
            DATA_ROOT_PREFIX=root / "data",
            SECRET_ROOT_PREFIX=root / "secret",
        )
        self.prefix_patch.start()
        self.paths = MODULE.operation_paths(OPERATION_ID, RELEASE_SHA, role)
        self.paths.manifest.parent.mkdir(parents=True, mode=0o700)
        os.chmod(self.paths.manifest.parent, 0o700)
        self.document = {
            "schema": MODULE.MANIFEST_SCHEMA,
            "operation_id": OPERATION_ID,
            "role": role,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": RELEASE_TREE_SHA,
            "controller_manifest_sha256": "1" * 64,
            "approval_sha256": "2" * 64,
            "role_material_sha256": "3" * 64,
            "canonical_compose_sha256": "4" * 64,
            "role_compose_sha256": "f" * 64,
            "environment_sha256": "5" * 64,
            "worker_sha256": "6" * 64,
            "acceptance_producer_sha256": "7" * 64,
            "runtime_image_ids": {
                "app": "sha256:" + "1" * 64,
                "postgres": "sha256:" + "2" * 64,
                "redis": "sha256:" + "3" * 64,
                "nginx": "sha256:" + "4" * 64,
            },
            "image_artifacts": {
                kind: {
                    "archive_sha256": archive_sha256,
                    "archive_bytes": archive_bytes,
                    "config_digest": "sha256:" + config_char * 64,
                    **image_content_binding(content_char),
                }
                for kind, archive_sha256, archive_bytes, config_char, content_char in (
                    ("app", "1" * 64, 102, "5", "9"),
                    ("postgres", "2" * 64, 103, "6", "a"),
                    ("redis", "3" * 64, 104, "7", "b"),
                    ("nginx", "4" * 64, 105, "8", "c"),
                )
            },
            "artifacts": {
                "release-bundle": {
                    "sha256": "8" * 64,
                    "bytes": 100,
                    "restored_tree_sha256": None,
                },
                "role-material": {
                    "sha256": "3" * 64,
                    "bytes": 101,
                    "restored_tree_sha256": None,
                },
                "app-image-archive": {
                    "sha256": "1" * 64,
                    "bytes": 102,
                    "restored_tree_sha256": None,
                },
                "postgres-image-archive": {
                    "sha256": "2" * 64,
                    "bytes": 103,
                    "restored_tree_sha256": None,
                },
                "redis-image-archive": {
                    "sha256": "3" * 64,
                    "bytes": 104,
                    "restored_tree_sha256": None,
                },
                "nginx-image-archive": {
                    "sha256": "4" * 64,
                    "bytes": 105,
                    "restored_tree_sha256": None,
                },
                "database-backup": {
                    "sha256": "9" * 64,
                    "bytes": 200,
                    "restored_tree_sha256": None,
                },
                "uploads-archive": {
                    "sha256": "a" * 64,
                    "bytes": 300,
                    "restored_tree_sha256": "b" * 64,
                },
                "audit-archive": {
                    "sha256": "c" * 64,
                    "bytes": 400,
                    "restored_tree_sha256": "d" * 64,
                },
            },
            "source_database": {
                "alembic_revision": "source_1",
                "fingerprint_algorithm": (
                    "pg-copy-jsonl-sha256-canonical-session-v1"
                ),
                "database_fingerprint_sha256": "e" * 64,
                "row_count": 10,
                "table_count": 2,
            },
            "target_migration_revision": "target_2",
            "postgres_runtime_uid": 70,
            "postgres_runtime_gid": 70,
        }
        self.write_manifest()

    def write_manifest(self) -> None:
        self.paths.manifest.write_text(
            json.dumps(
                self.document,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        self.paths.manifest.chmod(0o600)

    def close(self) -> None:
        self.prefix_patch.stop()


class ProductionShadowPrecommitWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = PrecommitFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.fixture.close()
        self.temporary.cleanup()

    def test_paths_are_operation_derived_and_match_compose_contract(self):
        paths = self.fixture.paths
        compact = OPERATION_ID.replace("-", "")
        self.assertEqual(paths.project_base, f"tb3p-{compact}")
        self.assertEqual(paths.project_name, f"tb3p-{compact}-bot-fi")
        self.assertEqual(
            paths.project_root,
            Path(self.temporary.name) / "project" / OPERATION_ID,
        )
        self.assertEqual(
            paths.release_root,
            paths.project_root / "releases" / RELEASE_SHA,
        )
        self.assertEqual(
            paths.environment,
            Path(self.temporary.name)
            / "secret"
            / OPERATION_ID
            / "bot-fi"
            / "runtime.env.role",
        )

    def test_worker_accepts_only_bounded_precommit_role_compose(self):
        manifest = MODULE.load_manifest(self.fixture.paths.manifest)
        canonical_path = (
            Path(__file__).resolve().parents[1]
            / "deploy"
            / "production"
            / "docker-compose.three-site-shadow.yml"
        )
        canonical = MODULE.yaml.safe_load(
            canonical_path.read_text(encoding="utf-8")
        )
        rendered = render_role_compose(
            canonical,
            role="bot-fi",
            scope="precommit",
        )
        self.fixture.paths.compose.parent.mkdir(parents=True, mode=0o700)
        self.fixture.paths.compose.write_bytes(
            canonical_role_compose_bytes(rendered)
        )
        self.fixture.paths.compose.chmod(0o600)
        with mock.patch.object(MODULE, "_run", return_value=""):
            MODULE._verify_compose(manifest, self.fixture.paths)

        poisoned = dict(rendered)
        poisoned["services"] = dict(rendered["services"])
        poisoned["services"]["bot_fi_api"] = {
            "profiles": ["bot-fi-public"],
            "image": "${PRODUCTION_SHADOW_APP_IMAGE_ID:?required}",
            "command": ["python", "-c", "raise SystemExit(0)"],
        }
        self.fixture.paths.compose.write_bytes(
            canonical_role_compose_bytes(poisoned)
        )
        with (
            mock.patch.object(MODULE, "_run", return_value=""),
            self.assertRaisesRegex(
                MODULE.PrecommitWorkerError,
                "service closure is not exact",
            ),
        ):
            MODULE._verify_compose(manifest, self.fixture.paths)

    def test_manifest_rejects_path_shape_mode_and_binding_tampering(self):
        loaded = MODULE.load_manifest(self.fixture.paths.manifest)
        self.assertEqual(loaded.operation_id, OPERATION_ID)

        alternate = self.fixture.paths.manifest.with_name("alternate.json")
        alternate.write_bytes(self.fixture.paths.manifest.read_bytes())
        alternate.chmod(0o600)
        with self.assertRaisesRegex(MODULE.PrecommitWorkerError, "path"):
            MODULE.load_manifest(alternate)

        self.fixture.paths.manifest.chmod(0o640)
        with self.assertRaisesRegex(MODULE.PrecommitWorkerError, "unsafe"):
            MODULE.load_manifest(self.fixture.paths.manifest)
        self.fixture.paths.manifest.chmod(0o600)

        self.fixture.document["unexpected"] = True
        self.fixture.write_manifest()
        with self.assertRaisesRegex(MODULE.PrecommitWorkerError, "fields"):
            MODULE.load_manifest(self.fixture.paths.manifest)

    def test_manifest_allows_source_revision_equal_to_target(self):
        self.fixture.document["target_migration_revision"] = (
            self.fixture.document["source_database"]["alembic_revision"]
        )
        self.fixture.write_manifest()
        loaded = MODULE.load_manifest(self.fixture.paths.manifest)
        self.assertEqual(
            loaded.target_migration_revision,
            loaded.source_database["alembic_revision"],
        )

    def test_plan_is_non_mutating_and_confirmation_is_exact(self):
        result = MODULE.execute_action(
            self.fixture.paths.manifest,
            action="verify-installation",
            apply=False,
            confirm=None,
        )
        self.assertEqual(result["status"], "planned")
        self.assertFalse(result["business_write_allowed"])
        self.assertFalse(result["freeze_allowed"])
        self.assertFalse(self.fixture.paths.journal_directory.exists())

        with self.assertRaisesRegex(MODULE.PrecommitWorkerError, "requires"):
            MODULE.execute_action(
                self.fixture.paths.manifest,
                action="verify-installation",
                apply=True,
                confirm="wrong",
            )
        self.assertFalse(self.fixture.paths.journal_directory.exists())

    def test_apply_enforces_order_and_persists_root_only_evidence(self):
        manifest = MODULE.load_manifest(self.fixture.paths.manifest)
        fake = mock.Mock(
            return_value={
                "zero_oneoff_residue": True,
                "semantic_marker": "exact",
            }
        )
        with mock.patch.dict(
            MODULE.ACTION_IMPLEMENTATIONS,
            {
                "verify-installation": fake,
                "bootstrap-database": fake,
            },
        ):
            with self.assertRaisesRegex(
                MODULE.PrecommitWorkerError,
                "verify-installation first",
            ):
                MODULE.execute_action(
                    self.fixture.paths.manifest,
                    action="bootstrap-database",
                    apply=True,
                    confirm=MODULE.confirmation_phrase(
                        manifest,
                        "bootstrap-database",
                    ),
                )
            first = MODULE.execute_action(
                self.fixture.paths.manifest,
                action="verify-installation",
                apply=True,
                confirm=MODULE.confirmation_phrase(
                    manifest,
                    "verify-installation",
                ),
            )
            second = MODULE.execute_action(
                self.fixture.paths.manifest,
                action="bootstrap-database",
                apply=True,
                confirm=MODULE.confirmation_phrase(
                    manifest,
                    "bootstrap-database",
                ),
            )
        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "completed")
        self.assertEqual(
            self.fixture.paths.journal.stat().st_mode & 0o777,
            0o600,
        )
        for action in ("verify-installation", "bootstrap-database"):
            evidence = self.fixture.paths.evidence_directory / f"{action}.json"
            self.assertEqual(evidence.stat().st_mode & 0o777, 0o600)
            document = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertFalse(document["business_write_allowed"])
            self.assertFalse(document["freeze_performed"])
            self.assertFalse(document["legacy_mutated"])

    def test_crash_after_intent_is_resumable_without_duplicate_start(self):
        manifest = MODULE.load_manifest(self.fixture.paths.manifest)
        attempts = 0

        def flaky(_manifest, _paths):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise MODULE.PrecommitWorkerError("injected crash")
            return {"zero_oneoff_residue": True, "value": "stable"}

        with mock.patch.dict(
            MODULE.ACTION_IMPLEMENTATIONS,
            {"verify-installation": flaky},
        ):
            confirmation = MODULE.confirmation_phrase(
                manifest,
                "verify-installation",
            )
            with self.assertRaisesRegex(
                MODULE.PrecommitWorkerError,
                "injected crash",
            ):
                MODULE.execute_action(
                    self.fixture.paths.manifest,
                    action="verify-installation",
                    apply=True,
                    confirm=confirmation,
                )
            result = MODULE.execute_action(
                self.fixture.paths.manifest,
                action="verify-installation",
                apply=True,
                confirm=confirmation,
            )
        self.assertEqual(result["status"], "completed")
        state = json.loads(
            self.fixture.paths.journal.read_text(encoding="utf-8")
        )
        self.assertEqual(state["attempts"]["verify-installation"], 2)
        self.assertEqual(
            [event["kind"] for event in state["events"]],
            ["started", "completed"],
        )

    def test_crash_after_evidence_recovers_without_replaying_implementation(self):
        manifest = MODULE.load_manifest(self.fixture.paths.manifest)
        confirmation = MODULE.confirmation_phrase(
            manifest,
            "verify-installation",
        )
        original_write = MODULE._write_state
        writes = 0

        def fail_completion(path, state, *, create):
            nonlocal writes
            writes += 1
            if writes == 3:
                raise MODULE.PrecommitWorkerError("crash after evidence")
            return original_write(path, state, create=create)

        first_impl = mock.Mock(
            return_value={"zero_oneoff_residue": True, "value": "first"}
        )
        with (
            mock.patch.dict(
                MODULE.ACTION_IMPLEMENTATIONS,
                {"verify-installation": first_impl},
            ),
            mock.patch.object(MODULE, "_write_state", fail_completion),
        ):
            with self.assertRaisesRegex(
                MODULE.PrecommitWorkerError,
                "crash after evidence",
            ):
                MODULE.execute_action(
                    self.fixture.paths.manifest,
                    action="verify-installation",
                    apply=True,
                    confirm=confirmation,
                )
        different = mock.Mock(
            return_value={"zero_oneoff_residue": True, "value": "different"}
        )
        with mock.patch.dict(
            MODULE.ACTION_IMPLEMENTATIONS,
            {"verify-installation": different},
        ):
            result = MODULE.execute_action(
                self.fixture.paths.manifest,
                action="verify-installation",
                apply=True,
                confirm=confirmation,
            )
        self.assertEqual(result["status"], "recovered-completed")
        self.assertFalse(result["implementation_replayed"])
        different.assert_not_called()
        state = json.loads(
            self.fixture.paths.journal.read_text(encoding="utf-8")
        )
        self.assertEqual(state["attempts"]["verify-installation"], 1)
        self.assertEqual(
            [event["kind"] for event in state["events"]],
            ["started", "completed"],
        )

    def test_crash_recovery_rejects_tampered_completed_evidence(self):
        manifest = MODULE.load_manifest(self.fixture.paths.manifest)
        confirmation = MODULE.confirmation_phrase(
            manifest,
            "verify-installation",
        )
        original_write = MODULE._write_state
        writes = 0

        def fail_completion(path, state, *, create):
            nonlocal writes
            writes += 1
            if writes == 3:
                raise MODULE.PrecommitWorkerError("crash after evidence")
            return original_write(path, state, create=create)

        fake = mock.Mock(
            return_value={"zero_oneoff_residue": True, "value": "first"}
        )
        with (
            mock.patch.dict(
                MODULE.ACTION_IMPLEMENTATIONS,
                {"verify-installation": fake},
            ),
            mock.patch.object(MODULE, "_write_state", fail_completion),
            self.assertRaisesRegex(
                MODULE.PrecommitWorkerError,
                "crash after evidence",
            ),
        ):
            MODULE.execute_action(
                self.fixture.paths.manifest,
                action="verify-installation",
                apply=True,
                confirm=confirmation,
            )
        evidence_path = (
            self.fixture.paths.evidence_directory
            / "verify-installation.json"
        )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["approval_sha256"] = "f" * 64
        evidence_path.write_text(
            json.dumps(evidence, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        evidence_path.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.PrecommitWorkerError,
            "differs from the operation",
        ):
            MODULE.execute_action(
                self.fixture.paths.manifest,
                action="verify-installation",
                apply=True,
                confirm=confirmation,
            )

    def test_artifact_and_oneoff_scope_guards_reject_escape(self):
        unsafe = self.fixture.paths.manifest.with_name("symlink.json")
        unsafe.symlink_to(self.fixture.paths.manifest)
        with self.assertRaisesRegex(MODULE.PrecommitWorkerError, "unsafe"):
            MODULE._read_root_file(
                unsafe,
                label="test",
                maximum=MODULE.MAX_JSON_BYTES,
            )

        manifest = MODULE.load_manifest(self.fixture.paths.manifest)
        foreign = {
            "Id": "f" * 64,
            "Config": {
                "Image": manifest.runtime_image_ids["app"],
                "Labels": {
                    "com.docker.compose.project": "legacy",
                    "com.docker.compose.oneoff": "True",
                    "com.docker.compose.service": "bot_fi_migration",
                    "trading-bot.production.operation-id": OPERATION_ID,
                },
            },
            "Mounts": [],
        }
        with (
            mock.patch.object(
                MODULE,
                "_run",
                return_value=json.dumps([foreign]),
            ),
            self.assertRaisesRegex(
                MODULE.PrecommitWorkerError,
                "outside the exact operation",
            ),
        ):
            MODULE._validate_oneoff(
                "f" * 64,
                manifest,
                self.fixture.paths,
            )

    def test_data_directory_bindings_reject_preexisting_and_substituted_stores(
        self,
    ):
        manifest = MODULE.load_manifest(self.fixture.paths.manifest)
        self.fixture.paths.journal_directory.mkdir(parents=True, mode=0o700)
        role_root = (
            self.fixture.paths.data_root
            / manifest.role.replace("_", "-")
        )
        self.fixture.paths.data_root.mkdir(parents=True, mode=0o700)
        self.fixture.paths.data_root.chmod(0o700)
        role_root.mkdir(mode=0o700)
        uploads = role_root / "uploads"
        uploads.mkdir(mode=0o700)
        (uploads / "legacy.bin").write_bytes(b"legacy")
        with self.assertRaisesRegex(
            MODULE.PrecommitWorkerError,
            "preexisting uploads data directory is not empty",
        ):
            MODULE._new_directory_bindings(manifest, self.fixture.paths)

        (uploads / "legacy.bin").unlink()
        bindings = MODULE._new_directory_bindings(
            manifest,
            self.fixture.paths,
        )
        self.assertEqual(set(bindings["stores"]), set(MODULE.STORE_NAMES))

        original = uploads.with_name("uploads-original")
        uploads.rename(original)
        uploads.symlink_to(original, target_is_directory=True)
        with self.assertRaisesRegex(
            MODULE.PrecommitWorkerError,
            "uploads data directory cannot be opened safely",
        ):
            MODULE._attest_data_directories(
                manifest,
                self.fixture.paths,
                postgres_started=False,
            )

    def test_data_directory_bindings_reject_inode_and_redis_drift(self):
        manifest = MODULE.load_manifest(self.fixture.paths.manifest)
        self.fixture.paths.journal_directory.mkdir(parents=True, mode=0o700)
        MODULE._new_directory_bindings(manifest, self.fixture.paths)
        role_root = (
            self.fixture.paths.data_root
            / manifest.role.replace("_", "-")
        )

        audit = role_root / "audit"
        original = audit.with_name("audit-original")
        audit.rename(original)
        audit.mkdir(mode=0o700)
        with self.assertRaisesRegex(
            MODULE.PrecommitWorkerError,
            "audit data directory identity changed",
        ):
            MODULE._attest_data_directories(
                manifest,
                self.fixture.paths,
                postgres_started=False,
            )

        audit.rmdir()
        original.rename(audit)
        (role_root / "redis" / "dump.rdb").write_bytes(b"forbidden")
        with self.assertRaisesRegex(
            MODULE.PrecommitWorkerError,
            "Redis target is not pristine-empty",
        ):
            MODULE._attest_data_directories(
                manifest,
                self.fixture.paths,
                postgres_started=False,
            )

    def test_postgres_directory_owner_transition_is_exact(self):
        manifest = MODULE.load_manifest(self.fixture.paths.manifest)
        self.fixture.paths.journal_directory.mkdir(parents=True, mode=0o700)
        bindings = MODULE._new_directory_bindings(
            manifest,
            self.fixture.paths,
        )
        original = MODULE._stable_directory_entries

        def runtime_owned(path, *, label, allowed_owners):
            if path.name != "postgres":
                return original(
                    path,
                    label=label,
                    allowed_owners=allowed_owners,
                )
            owner = (
                MODULE.POSTGRES_RUNTIME_UID,
                MODULE.POSTGRES_RUNTIME_GID,
            )
            if owner not in allowed_owners:
                raise MODULE.PrecommitWorkerError(
                    "postgres data directory changed or is unsafe"
                )
            metadata = path.stat(follow_symlinks=False)
            metadata = mock.Mock(
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino,
                st_uid=owner[0],
                st_gid=owner[1],
            )
            return metadata, []

        with mock.patch.object(
            MODULE,
            "_stable_directory_entries",
            side_effect=runtime_owned,
        ):
            evidence = MODULE._attest_data_directories(
                manifest,
                self.fixture.paths,
                postgres_started=True,
            )
        self.assertEqual(
            (
                evidence["postgres"]["uid"],
                evidence["postgres"]["gid"],
            ),
            (MODULE.POSTGRES_RUNTIME_UID, MODULE.POSTGRES_RUNTIME_GID),
        )
        self.assertEqual(
            bindings["stores"]["postgres"]["initial_uid"],
            0,
        )
        with (
            mock.patch.object(
                MODULE,
                "_stable_directory_entries",
                side_effect=runtime_owned,
            ),
            self.assertRaisesRegex(
                MODULE.PrecommitWorkerError,
                "postgres data directory changed or is unsafe",
            ),
        ):
            MODULE._attest_data_directories(
                manifest,
                self.fixture.paths,
                postgres_started=False,
            )

    def test_network_rejects_missing_binding_and_foreign_endpoint(self):
        manifest = MODULE.load_manifest(self.fixture.paths.manifest)
        identifier = "e" * 64
        database = "d" * 64
        network_name = (
            f"{self.fixture.paths.project_name}_"
            f"{MODULE.ROLE_SERVICES[manifest.role]['network']}"
        )
        document = [
            {
                "Id": identifier,
                "Name": network_name,
                "Scope": "local",
                "Driver": "bridge",
                "Internal": True,
                "Attachable": False,
                "Ingress": False,
                "ConfigOnly": False,
                "Options": {},
                "Labels": {
                    "com.docker.compose.network": "bot_fi",
                    "com.docker.compose.project": (
                        self.fixture.paths.project_name
                    ),
                    "com.docker.compose.version": "2.38.2",
                    "trading-bot.production.operation-id": OPERATION_ID,
                },
                "IPAM": {
                    "Driver": "default",
                    "Options": {},
                    "Config": [
                        {
                            "Subnet": "172.29.0.0/16",
                            "Gateway": "172.29.0.1",
                        }
                    ],
                },
                "Containers": {
                    database: {"EndpointID": "endpoint-database"},
                },
            }
        ]
        with mock.patch.object(
            MODULE,
            "_run",
            return_value=json.dumps(document),
        ):
            evidence = MODULE._validate_network(
                identifier,
                manifest,
                self.fixture.paths,
                allowed_container_ids=frozenset({database}),
            )
        self.assertEqual(evidence["endpoint_count"], 1)

        missing_label = json.loads(json.dumps(document))
        missing_label[0]["Labels"].pop(
            "trading-bot.production.operation-id"
        )
        with (
            mock.patch.object(
                MODULE,
                "_run",
                return_value=json.dumps(missing_label),
            ),
            self.assertRaisesRegex(
                MODULE.PrecommitWorkerError,
                "exact internal binding",
            ),
        ):
            MODULE._validate_network(
                identifier,
                manifest,
                self.fixture.paths,
                allowed_container_ids=frozenset({database}),
            )

        foreign = json.loads(json.dumps(document))
        foreign[0]["Containers"]["f" * 64] = {
            "EndpointID": "endpoint-foreign"
        }
        with (
            mock.patch.object(
                MODULE,
                "_run",
                return_value=json.dumps(foreign),
            ),
            self.assertRaisesRegex(
                MODULE.PrecommitWorkerError,
                "foreign endpoint",
            ),
        ):
            MODULE._validate_network(
                identifier,
                manifest,
                self.fixture.paths,
                allowed_container_ids=frozenset({database}),
            )

    def test_database_container_rejects_swapped_postgres_bind(self):
        manifest = MODULE.load_manifest(self.fixture.paths.manifest)
        identifier = "d" * 64
        network = (
            f"{self.fixture.paths.project_name}_"
            f"{MODULE.ROLE_SERVICES[manifest.role]['network']}"
        )
        document = [
            {
                "Id": identifier,
                "Image": manifest.runtime_image_ids["postgres"],
                "Config": {
                    "Image": manifest.runtime_image_ids["postgres"],
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
                    "NetworkMode": network,
                },
                "State": {"Running": False},
                "NetworkSettings": {"Networks": {network: {}}},
                "Mounts": [
                    {
                        "Type": "bind",
                        "Source": str(
                            self.fixture.paths.data_root
                            / "bot-fi"
                            / "postgres"
                        ),
                        "Destination": "/var/lib/postgresql/data",
                        "RW": True,
                    }
                ],
            }
        ]
        with mock.patch.object(
            MODULE,
            "_run",
            return_value=json.dumps(document),
        ):
            MODULE._validate_database_container(
                identifier,
                manifest,
                self.fixture.paths,
                require_running=False,
            )
        document[0]["Mounts"][0]["Source"] = str(
            self.fixture.paths.data_root / "webapp-fi" / "postgres"
        )
        with (
            mock.patch.object(
                MODULE,
                "_run",
                return_value=json.dumps(document),
            ),
            self.assertRaisesRegex(
                MODULE.PrecommitWorkerError,
                "bind mount differs",
            ),
        ):
            MODULE._validate_database_container(
                identifier,
                manifest,
                self.fixture.paths,
                require_running=False,
            )

    def test_oneoff_cleanup_requires_exact_operation_labels_and_mounts(self):
        manifest = MODULE.load_manifest(self.fixture.paths.manifest)
        identifier = "f" * 64
        network = (
            f"{self.fixture.paths.project_name}_"
            f"{MODULE.ROLE_SERVICES[manifest.role]['network']}"
        )
        document = [
            {
                "Id": identifier,
                "Image": manifest.runtime_image_ids["app"],
                "Config": {
                    "Image": manifest.runtime_image_ids["app"],
                    "Volumes": {},
                    "Labels": {
                        "com.docker.compose.project": (
                            self.fixture.paths.project_name
                        ),
                        "com.docker.compose.oneoff": "True",
                        "com.docker.compose.service": "bot_fi_migration",
                        "trading-bot.production.operation-id": OPERATION_ID,
                    },
                },
                "HostConfig": {
                    "Privileged": False,
                    "PortBindings": {},
                    "NetworkMode": network,
                },
                "NetworkSettings": {"Networks": {network: {}}},
                "Mounts": [
                    {
                        "Type": "bind",
                        "Source": str(
                            self.fixture.paths.secret_root
                            / "tls"
                            / "ca.crt"
                        ),
                        "Destination": "/run/production-dr-ca/ca.crt",
                        "RW": False,
                    }
                ],
            }
        ]
        with mock.patch.object(
            MODULE,
            "_run",
            return_value=json.dumps(document),
        ):
            evidence = MODULE._validate_oneoff(
                identifier,
                manifest,
                self.fixture.paths,
            )
        self.assertEqual(evidence["container_id"], identifier)

        document[0]["Config"]["Labels"].pop(
            "trading-bot.production.operation-id"
        )
        with (
            mock.patch.object(
                MODULE,
                "_run",
                return_value=json.dumps(document),
            ),
            self.assertRaisesRegex(
                MODULE.PrecommitWorkerError,
                "outside the exact operation",
            ),
        ):
            MODULE._validate_oneoff(
                identifier,
                manifest,
                self.fixture.paths,
            )

    def test_project_inventory_never_ignores_foreign_non_database_container(
        self,
    ):
        manifest = MODULE.load_manifest(self.fixture.paths.manifest)
        database = "d" * 64
        foreign = "f" * 64

        def run(arguments, **_kwargs):
            if arguments[:2] == [MODULE.DOCKER, "compose"]:
                return database
            return f"{database}\n{foreign}\n"

        with mock.patch.object(MODULE, "_run", side_effect=run):
            self.assertEqual(
                MODULE._oneoff_ids(
                    manifest,
                    self.fixture.paths,
                ),
                [foreign],
            )

    def test_bootstrap_inspects_created_database_before_start(self):
        manifest = MODULE.load_manifest(self.fixture.paths.manifest)
        database = "d" * 64
        network = "e" * 64
        observed: list[str] = []

        def inspect_database(
            _identifier,
            _manifest,
            _paths,
            *,
            require_running,
        ):
            observed.append(f"inspect:{require_running}")
            return {
                "container_id": database,
                "running": require_running is True,
            }

        def inspect_network(
            _identifier,
            _manifest,
            _paths,
            *,
            allowed_container_ids,
        ):
            observed.append(
                "network:" + ",".join(sorted(allowed_container_ids))
            )
            return {"network_id": network}

        def run(arguments, **_kwargs):
            if "create" in arguments:
                observed.append("create")
            if "start" in arguments:
                observed.append("start")
            return ""

        with (
            mock.patch.object(
                MODULE,
                "_verify_static_bindings",
                return_value={"static": True},
            ),
            mock.patch.object(MODULE, "_cleanup_oneoffs", return_value=[]),
            mock.patch.object(MODULE, "_oneoff_ids", return_value=[]),
            mock.patch.object(
                MODULE,
                "_database_container",
                side_effect=["", database],
            ),
            mock.patch.object(
                MODULE,
                "_network_identifier",
                side_effect=["", network],
            ),
            mock.patch.object(
                MODULE,
                "_attest_data_directories",
                return_value={"postgres": {}},
            ),
            mock.patch.object(
                MODULE,
                "_validate_database_container",
                side_effect=inspect_database,
            ),
            mock.patch.object(
                MODULE,
                "_validate_network",
                side_effect=inspect_network,
            ),
            mock.patch.object(MODULE, "_psql", return_value="1"),
            mock.patch.object(
                MODULE,
                "_operation_non_database_containers",
                return_value=[],
            ),
            mock.patch.object(MODULE, "_run", side_effect=run),
        ):
            evidence = MODULE._bootstrap_database(
                manifest,
                self.fixture.paths,
            )
        self.assertTrue(evidence["database_ready"])
        self.assertLess(observed.index("create"), observed.index("inspect:False"))
        self.assertLess(observed.index("inspect:False"), observed.index("start"))
        self.assertLess(observed.index("start"), observed.index("inspect:True"))

    def test_bootstrap_adopts_stopped_exact_container_without_recreate(self):
        manifest = MODULE.load_manifest(self.fixture.paths.manifest)
        database = "d" * 64
        network = "e" * 64
        commands: list[list[str]] = []
        inspections = iter(
            [
                {
                    "container_id": database,
                    "running": False,
                },
                {
                    "container_id": database,
                    "running": True,
                },
            ]
        )

        with (
            mock.patch.object(
                MODULE,
                "_verify_static_bindings",
                return_value={"static": True},
            ),
            mock.patch.object(MODULE, "_cleanup_oneoffs", return_value=[]),
            mock.patch.object(MODULE, "_oneoff_ids", return_value=[]),
            mock.patch.object(
                MODULE,
                "_database_container",
                return_value=database,
            ),
            mock.patch.object(
                MODULE,
                "_network_identifier",
                return_value=network,
            ),
            mock.patch.object(
                MODULE,
                "_attest_data_directories",
                return_value={"postgres": {}},
            ),
            mock.patch.object(
                MODULE,
                "_validate_database_container",
                side_effect=lambda *_args, **_kwargs: next(inspections),
            ),
            mock.patch.object(
                MODULE,
                "_validate_network",
                return_value={"network_id": network},
            ),
            mock.patch.object(MODULE, "_psql", return_value="1"),
            mock.patch.object(
                MODULE,
                "_operation_non_database_containers",
                return_value=[],
            ),
            mock.patch.object(
                MODULE,
                "_run",
                side_effect=lambda arguments, **_kwargs: (
                    commands.append(arguments) or ""
                ),
            ),
        ):
            evidence = MODULE._bootstrap_database(
                manifest,
                self.fixture.paths,
            )
        self.assertTrue(evidence["adopted_existing_database"])
        self.assertFalse(any("create" in command for command in commands))
        self.assertEqual(
            sum("start" in command for command in commands),
            1,
        )

    def test_loaded_postgres_image_requires_release_and_runtime_labels(self):
        manifest = MODULE.load_manifest(self.fixture.paths.manifest)

        def inspect(arguments, **_kwargs):
            image_id = arguments[-1]
            role = next(
                key for key, value in manifest.runtime_image_ids.items()
                if value == image_id
            )
            labels = {}
            if role in {"app", "postgres"}:
                labels["org.opencontainers.image.revision"] = RELEASE_SHA
            if role == "postgres":
                labels["trading-bot.postgres.runtime-uid"] = "999"
                labels["trading-bot.postgres.runtime-gid"] = "70"
            return json.dumps(
                [{"Id": image_id, "Config": {"Labels": labels}}]
            )

        with (
            mock.patch.object(MODULE, "_run", side_effect=inspect),
            mock.patch.object(
                MODULE,
                "image_content_descriptor",
                side_effect=lambda document, **_kwargs: (
                    (
                        dict(
                            manifest.image_artifacts[
                                next(
                                    kind
                                    for kind, image_id in (
                                        manifest.runtime_image_ids.items()
                                    )
                                    if image_id == document["Id"]
                                )
                            ].content_descriptor
                        ),
                        manifest.image_artifacts[
                            next(
                                kind
                                for kind, image_id in (
                                    manifest.runtime_image_ids.items()
                                )
                                if image_id == document["Id"]
                            )
                        ].content_identity,
                    )
                ),
            ),
            self.assertRaisesRegex(
                MODULE.PrecommitWorkerError,
                "runtime UID/GID",
            ),
        ):
            MODULE._verify_images(manifest)

    def test_content_identity_is_storage_driver_independent_and_layer_bound(self):
        config = {
            "User": "70:70",
            "Env": ["A=1", "B=2"],
            "Entrypoint": ["/entrypoint"],
            "Cmd": ["postgres"],
            "WorkingDir": "/var/lib/postgresql",
            "Labels": {
                "org.opencontainers.image.revision": RELEASE_SHA,
            },
        }
        layers = [
            "sha256:" + "1" * 64,
            "sha256:" + "2" * 64,
        ]
        archive_config = {
            "architecture": "amd64",
            "os": "linux",
            "created": "2026-07-27T00:00:00Z",
            "rootfs": {"type": "layers", "diff_ids": layers},
            "config": config,
        }
        engine_inspect = {
            "Id": "sha256:" + "f" * 64,
            "Architecture": "amd64",
            "Os": "linux",
            "Created": "2026-07-27T00:00:00Z",
            "RootFS": {"Type": "layers", "Layers": layers},
            "Config": config,
        }
        self.assertEqual(
            MODULE.image_content_descriptor_from_archive_config(
                archive_config
            ),
            MODULE.image_content_descriptor(engine_inspect),
        )

        changed = json.loads(json.dumps(engine_inspect))
        changed["RootFS"]["Layers"][1] = "sha256:" + "3" * 64
        self.assertNotEqual(
            MODULE.image_content_descriptor_from_archive_config(
                archive_config
            ),
            MODULE.image_content_descriptor(changed),
        )

    def test_held_artifact_detects_path_swap_during_consumption(self):
        artifact = self.fixture.paths.manifest.parent / "held.bin"
        artifact.write_bytes(b"bound-payload")
        artifact.chmod(0o600)
        binding = MODULE.ArtifactBinding(
            sha256=hashlib.sha256(b"bound-payload").hexdigest(),
            bytes=len(b"bound-payload"),
            restored_tree_sha256=None,
        )
        replacement = artifact.with_name("replacement.bin")
        replacement.write_bytes(b"bound-payload")
        replacement.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.PrecommitWorkerError,
            "changed while being consumed",
        ):
            with MODULE._held_artifact(
                artifact,
                binding,
                label="held test artifact",
            ) as stream:
                self.assertEqual(stream.read(), b"bound-payload")
                os.replace(replacement, artifact)


if __name__ == "__main__":
    unittest.main()
