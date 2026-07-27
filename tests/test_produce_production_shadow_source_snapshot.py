from __future__ import annotations

from contextlib import nullcontext, redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import types
import unittest
from unittest import mock

from scripts import produce_production_shadow_source_snapshot as MODULE
from scripts.wa_ir_production_operation import StreamDigest


OPERATION_ID = "123e4567-e89b-42d3-a456-426614174000"
NEW_RELEASE_SHA = "a" * 40
LEGACY_RELEASE_SHA = "b" * 40


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class SnapshotFixture:
    def __init__(
        self,
        root: Path,
        *,
        role: str = "bot_fi",
        mode: str = "live-baseline",
    ) -> None:
        self.root = root
        self.binding_path = root / "binding.json"
        project = MODULE.SOURCE_PROJECTS[role]
        self.document = {
            "schema": MODULE.BINDING_SCHEMA,
            "operation_id": OPERATION_ID,
            "release_sha": NEW_RELEASE_SHA,
            "legacy_release_sha": LEGACY_RELEASE_SHA,
            "role": role,
            "source_project": project,
            "containers": dict(MODULE.SOURCE_CONTAINERS),
            "images": {
                **MODULE.SOURCE_IMAGE_REFERENCES[role],
                "restore_postgres": (
                    "trading_bot_postgres_boottime:15-" + NEW_RELEASE_SHA
                ),
            },
            "volumes": {
                "database": f"{project}_postgres_data",
                "uploads": f"{project}_uploads_data",
                "audit": f"{project}_audit_data",
                "redis": f"{project}_redis_data",
            },
            "controller_manifest_sha256": "1" * 64,
            "approval_sha256": "2" * 64,
            "mode": mode,
        }
        self.write_binding()

    def write_binding(self) -> None:
        self.binding_path.write_bytes(canonical_bytes(self.document))
        self.binding_path.chmod(0o600)

    def binding(self) -> MODULE.SnapshotBinding:
        return MODULE.load_binding(self.binding_path)

    def freeze_document(self) -> dict:
        binding = self.binding()
        return {
            "schema": MODULE.FREEZE_SCHEMA,
            "operation_id": binding.operation_id,
            "release_sha": binding.release_sha,
            "legacy_release_sha": binding.legacy_release_sha,
            "role": binding.role,
            "source_project": binding.source_project,
            "controller_manifest_sha256": (
                binding.controller_manifest_sha256
            ),
            "approval_sha256": binding.approval_sha256,
            "production_vhosts": MODULE._expected_vhosts(),
            "source_container_ids": {
                "database": "1" * 64,
                "application": "2" * 64,
                "redis": "3" * 64,
            },
            "freeze_generation_sha256": "4" * 64,
            "freeze_active": True,
            "write_capable_route_count": 0,
            "legacy_writer_process_count": 0,
            "writer_database_client_count": 0,
            "file_mutator_process_count": 0,
        }

    def write_freeze(self, document: dict | None = None) -> Path:
        path = self.root / "freeze.json"
        path.write_bytes(
            canonical_bytes(document or self.freeze_document())
        )
        path.chmod(0o600)
        return path


def image_identity(
    fixture: SnapshotFixture,
    kind: str,
    character: str,
) -> MODULE.ImageIdentity:
    return MODULE.ImageIdentity(
        reference=fixture.document["images"][kind],
        image_id="sha256:" + character * 64,
        labels={},
    )


def container_document(
    fixture: SnapshotFixture,
    kind: str,
    image: MODULE.ImageIdentity,
    *,
    identifier_character: str,
) -> dict:
    mounts = []
    for volume_kind, destination in MODULE.SOURCE_MOUNTS[kind].items():
        name = fixture.document["volumes"][volume_kind]
        mounts.append(
            {
                "Type": "volume",
                "Name": name,
                "Source": f"/var/lib/docker/volumes/{name}/_data",
                "Destination": destination,
                "RW": True,
            }
        )
    return {
        "Id": identifier_character * 64,
        "Name": "/" + fixture.document["containers"][kind],
        "Image": image.image_id,
        "Config": {
            "Image": image.reference,
            "Labels": {
                "com.docker.compose.project": (
                    fixture.document["source_project"]
                ),
                "com.docker.compose.service": MODULE.SOURCE_SERVICES[kind],
                "com.docker.compose.oneoff": "False",
            },
        },
        "State": {
            "Running": True,
            "StartedAt": "2026-07-27T00:00:00Z",
        },
        "RestartCount": 0,
        "Mounts": mounts,
    }


class ProductionSourceSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = SnapshotFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_default_is_a_nonexecuting_plan_with_exact_paths_and_argv(self):
        output_root = self.root / "output"
        captured = io.StringIO()
        with (
            mock.patch.object(
                MODULE,
                "_inspect_optional",
                side_effect=AssertionError("dry plan inspected Docker"),
            ),
            redirect_stdout(captured),
        ):
            status = MODULE.main(
                [
                    "--binding",
                    str(self.fixture.binding_path),
                    "--output-root",
                    str(output_root),
                ]
            )
        self.assertEqual(status, 0)
        plan = json.loads(captured.getvalue())
        self.assertEqual(plan["status"], "planned")
        self.assertFalse(plan["executes_commands"])
        self.assertFalse(plan["source_mutation"])
        self.assertEqual(
            plan["output_directory"],
            str(
                output_root
                / OPERATION_ID
                / "bot_fi"
                / "live-baseline"
            ),
        )
        self.assertEqual(
            plan["artifacts"]["database-backup"],
            str(
                output_root
                / OPERATION_ID
                / "bot_fi"
                / "live-baseline"
                / "database.dump"
            ),
        )

        binding = self.fixture.binding()
        argv = MODULE.source_dump_arguments(
            binding,
            user="trading_bot",
            database="trading_bot",
        )
        self.assertEqual(
            argv,
            [
                MODULE.DOCKER,
                "exec",
                "--env",
                f"PGOPTIONS={MODULE.DATABASE_FINGERPRINT_PGOPTIONS}",
                "--env",
                "PGCLIENTENCODING=UTF8",
                "trading_bot_db",
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--no-password",
                "--serializable-deferrable",
                "--username",
                "trading_bot",
                "--dbname",
                "trading_bot",
            ],
        )
        self.assertNotIn("sh", argv)
        self.assertNotIn("-c", argv)

    def test_binding_is_exact_canonical_root_only_and_injection_safe(self):
        binding = self.fixture.binding()
        self.assertEqual(binding.source_project, "trading_bot")

        self.fixture.binding_path.write_bytes(
            canonical_bytes(self.fixture.document) + b"\n"
        )
        with self.assertRaisesRegex(
            MODULE.SourceSnapshotError, "canonical JSON"
        ):
            MODULE.load_binding(self.fixture.binding_path)

        self.fixture.write_binding()
        self.fixture.binding_path.chmod(0o640)
        with self.assertRaisesRegex(
            MODULE.SourceSnapshotError, "root-only"
        ):
            MODULE.load_binding(self.fixture.binding_path)

        self.fixture.write_binding()
        self.fixture.document["images"]["database"] = "--pull=always"
        self.fixture.write_binding()
        with self.assertRaisesRegex(
            MODULE.SourceSnapshotError, "image binding"
        ):
            MODULE.load_binding(self.fixture.binding_path)

    def test_role_project_and_canonical_container_bindings_are_closed(self):
        self.fixture.document["source_project"] = "current"
        self.fixture.write_binding()
        with self.assertRaisesRegex(
            MODULE.SourceSnapshotError, "not canonical"
        ):
            MODULE.load_binding(self.fixture.binding_path)

        self.fixture = SnapshotFixture(self.root)
        self.fixture.document["containers"]["database"] = "forged_db"
        self.fixture.write_binding()
        with self.assertRaisesRegex(
            MODULE.SourceSnapshotError, "not canonical"
        ):
            MODULE.load_binding(self.fixture.binding_path)

        web = SnapshotFixture(self.root, role="webapp_fi")
        self.assertEqual(web.binding().source_project, "current")

    def test_role_specific_application_image_bindings_are_exact(self):
        expected = {
            "bot_fi": "trading_bot_base",
            "webapp_fi": "trading_bot_base_iran",
        }
        for role, application_image in expected.items():
            with self.subTest(role=role):
                fixture = SnapshotFixture(self.root, role=role)
                binding = fixture.binding()
                self.assertEqual(
                    binding.images["application"],
                    application_image,
                )
                self.assertEqual(
                    binding.images["database"],
                    "postgres:15-alpine",
                )
                self.assertEqual(
                    binding.images["redis"],
                    "redis:7-alpine",
                )

                fixture.document["images"]["application"] = expected[
                    "webapp_fi" if role == "bot_fi" else "bot_fi"
                ]
                fixture.write_binding()
                with self.assertRaisesRegex(
                    MODULE.SourceSnapshotError,
                    "image binding",
                ):
                    fixture.binding()

    def test_legacy_application_revision_label_is_optional_but_exact(self):
        for role in MODULE.ROLE_NAMES:
            fixture = SnapshotFixture(self.root, role=role)
            binding = fixture.binding()
            for label, accepted in (
                (None, True),
                (LEGACY_RELEASE_SHA, True),
                (NEW_RELEASE_SHA, False),
            ):
                with self.subTest(role=role, revision=label):
                    identities = {
                        kind: image_identity(
                            fixture,
                            kind,
                            character,
                        )
                        for kind, character in zip(
                            MODULE.IMAGE_KEYS,
                            "1234",
                        )
                    }
                    image_documents = {
                        kind: {
                            "Id": identity.image_id,
                            "Config": {"Labels": {}},
                        }
                        for kind, identity in identities.items()
                    }
                    image_documents["application"]["Config"]["Labels"] = (
                        None
                        if label is None
                        else {
                            "org.opencontainers.image.revision": label,
                        }
                    )
                    image_documents["restore_postgres"]["Config"][
                        "Labels"
                    ] = {
                        "org.opencontainers.image.revision": NEW_RELEASE_SHA,
                        "trading-bot.postgres.runtime-uid": "70",
                        "trading-bot.postgres.runtime-gid": "70",
                    }
                    container_documents = {
                        kind: container_document(
                            fixture,
                            kind,
                            identities[kind],
                            identifier_character=character,
                        )
                        for kind, character in zip(
                            MODULE.SOURCE_CONTAINERS,
                            "567",
                        )
                    }
                    volume_documents = {
                        kind: {
                            "Name": name,
                            "Driver": "local",
                            "Scope": "local",
                            "Mountpoint": (
                                f"/var/lib/docker/volumes/{name}/_data"
                            ),
                            "Labels": {
                                "com.docker.compose.project": (
                                    binding.source_project
                                ),
                                "com.docker.compose.volume": (
                                    MODULE.VOLUME_SUFFIXES[kind]
                                ),
                            },
                            "Options": None,
                        }
                        for kind, name in binding.volumes.items()
                    }
                    documents = {
                        "image": {
                            binding.images[kind]: document
                            for kind, document in image_documents.items()
                        },
                        "container": {
                            binding.containers[kind]: document
                            for kind, document in (
                                container_documents.items()
                            )
                        },
                        "volume": {
                            binding.volumes[kind]: document
                            for kind, document in volume_documents.items()
                        },
                    }

                    def inspect(kind, name):
                        return copy.deepcopy(documents[kind][name])

                    context = (
                        nullcontext()
                        if accepted
                        else self.assertRaisesRegex(
                            MODULE.SourceSnapshotError,
                            "release label differs",
                        )
                    )
                    with (
                        mock.patch.object(
                            MODULE,
                            "_inspect_required",
                            side_effect=inspect,
                        ),
                        context,
                    ):
                        inventory = MODULE.inspect_source(binding)
                    if accepted:
                        self.assertEqual(
                            inventory.images["application"].reference,
                            binding.images["application"],
                        )

    def test_final_requires_exact_all_vhost_zero_writer_freeze(self):
        final = SnapshotFixture(self.root, mode="frozen-final")
        captured = io.StringIO()
        with redirect_stdout(captured):
            status = MODULE.main(
                [
                    "--binding",
                    str(final.binding_path),
                    "--output-root",
                    str(self.root / "out"),
                ]
            )
        self.assertEqual(status, 1)
        self.assertIn("requires --freeze-evidence", captured.getvalue())

        freeze_path = final.write_freeze()
        document, digest = MODULE.load_freeze_evidence(
            freeze_path,
            final.binding(),
        )
        self.assertEqual(document["production_vhosts"], {
            "bot_fi": ["coin.362514.ir", "mini-app.362514.ir"],
            "webapp_fi": ["coin.gold-trade.ir"],
        })
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

        poisoned = final.freeze_document()
        poisoned["writer_database_client_count"] = 1
        final.write_freeze(poisoned)
        with self.assertRaisesRegex(
            MODULE.SourceSnapshotError, "zero-writer"
        ):
            MODULE.load_freeze_evidence(
                freeze_path,
                final.binding(),
            )

        poisoned = final.freeze_document()
        poisoned["production_vhosts"]["bot_fi"].pop()
        final.write_freeze(poisoned)
        with self.assertRaisesRegex(
            MODULE.SourceSnapshotError, "zero-writer"
        ):
            MODULE.load_freeze_evidence(
                freeze_path,
                final.binding(),
            )

    def test_container_inspect_forgery_project_and_volume_mismatch_fail(self):
        binding = self.fixture.binding()
        image = image_identity(self.fixture, "database", "1")
        valid = container_document(
            self.fixture,
            "database",
            image,
            identifier_character="a",
        )
        observed = MODULE._critical_container_identity(
            valid,
            kind="database",
            binding=binding,
            image=image,
        )
        self.assertEqual(observed["id"], "a" * 64)
        self.assertEqual(
            observed["mounts"]["database"]["destination"],
            "/var/lib/postgresql/data",
        )

        forged = copy.deepcopy(valid)
        forged["Id"] = "short"
        with self.assertRaisesRegex(
            MODULE.SourceSnapshotError, "identity"
        ):
            MODULE._critical_container_identity(
                forged,
                kind="database",
                binding=binding,
                image=image,
            )

        wrong_project = copy.deepcopy(valid)
        wrong_project["Config"]["Labels"][
            "com.docker.compose.project"
        ] = "forged"
        with self.assertRaisesRegex(
            MODULE.SourceSnapshotError, "project label"
        ):
            MODULE._critical_container_identity(
                wrong_project,
                kind="database",
                binding=binding,
                image=image,
            )

        wrong_volume = copy.deepcopy(valid)
        wrong_volume["Mounts"][0]["Name"] = "foreign_pgdata"
        with self.assertRaisesRegex(
            MODULE.SourceSnapshotError, "volume mount"
        ):
            MODULE._critical_container_identity(
                wrong_volume,
                kind="database",
                binding=binding,
                image=image,
            )

        extra_mount = copy.deepcopy(valid)
        extra_mount["Mounts"].append(
            {
                "Type": "volume",
                "Name": "foreign_data",
                "Source": "/var/lib/docker/volumes/foreign_data/_data",
                "Destination": "/foreign",
                "RW": True,
            }
        )
        with self.assertRaisesRegex(
            MODULE.SourceSnapshotError, "volume destination"
        ):
            MODULE._critical_container_identity(
                extra_mount,
                kind="database",
                binding=binding,
                image=image,
            )

        app_image = image_identity(self.fixture, "application", "2")
        application = container_document(
            self.fixture,
            "application",
            app_image,
            identifier_character="b",
        )
        application["Mounts"].append(
            {
                "Type": "bind",
                "Source": "/srv/trading-bot/current/api",
                "Destination": "/app/api",
                "RW": True,
                "Propagation": "rprivate",
            }
        )
        observed_app = MODULE._critical_container_identity(
            application,
            kind="application",
            binding=binding,
            image=app_image,
        )
        self.assertEqual(observed_app["other_mount_count"], 1)
        self.assertRegex(
            observed_app["other_mounts_sha256"],
            r"^[0-9a-f]{64}$",
        )

    def test_volume_and_restore_image_inspection_are_exact(self):
        binding = self.fixture.binding()
        name = binding.volumes["uploads"]
        mountpoint = f"/var/lib/docker/volumes/{name}/_data"
        document = {
            "Name": name,
            "Driver": "local",
            "Scope": "local",
            "Mountpoint": mountpoint,
            "Labels": {
                "com.docker.compose.project": "trading_bot",
                "com.docker.compose.volume": "uploads_data",
            },
            "Options": None,
        }
        observed = MODULE._critical_volume_identity(
            document,
            kind="uploads",
            binding=binding,
            expected_mountpoint=mountpoint,
        )
        self.assertEqual(observed["mountpoint"], mountpoint)
        forged = dict(document)
        forged["Mountpoint"] = "/tmp/forged"
        with self.assertRaisesRegex(
            MODULE.SourceSnapshotError, "volume identity"
        ):
            MODULE._critical_volume_identity(
                forged,
                kind="uploads",
                binding=binding,
                expected_mountpoint=mountpoint,
            )

        image_document = {
            "Id": "sha256:" + "9" * 64,
            "Config": {
                "Labels": {
                    "org.opencontainers.image.revision": NEW_RELEASE_SHA,
                    "trading-bot.postgres.runtime-uid": "70",
                    "trading-bot.postgres.runtime-gid": "70",
                }
            },
        }
        with mock.patch.object(
            MODULE,
            "_inspect_required",
            return_value=image_document,
        ):
            identity = MODULE._image_identity(
                binding.images["restore_postgres"],
                expected_release_sha=NEW_RELEASE_SHA,
                require_postgres_runtime=True,
            )
        self.assertEqual(identity.image_id, image_document["Id"])
        image_document["Config"]["Labels"][
            "trading-bot.postgres.runtime-uid"
        ] = "999"
        with (
            mock.patch.object(
                MODULE,
                "_inspect_required",
                return_value=image_document,
            ),
            self.assertRaisesRegex(
                MODULE.SourceSnapshotError, "UID/GID"
            ),
        ):
            MODULE._image_identity(
                binding.images["restore_postgres"],
                expected_release_sha=NEW_RELEASE_SHA,
                require_postgres_runtime=True,
            )
        image_document["Config"]["Labels"][
            "trading-bot.postgres.runtime-uid"
        ] = "70"
        del image_document["Config"]["Labels"][
            "org.opencontainers.image.revision"
        ]
        with (
            mock.patch.object(
                MODULE,
                "_inspect_required",
                return_value=image_document,
            ),
            self.assertRaisesRegex(
                MODULE.SourceSnapshotError, "release label differs"
            ),
        ):
            MODULE._image_identity(
                binding.images["restore_postgres"],
                expected_release_sha=NEW_RELEASE_SHA,
                require_postgres_runtime=True,
            )

    def test_held_volume_rejects_mountpoint_path_swap(self):
        binding = self.fixture.binding()
        mountpoint = self.root / "uploads-volume"
        mountpoint.mkdir(mode=0o700)
        volume_document = {
            "Name": binding.volumes["uploads"],
            "Driver": "local",
            "Scope": "local",
            "Mountpoint": str(mountpoint),
            "Labels": {
                "com.docker.compose.project": binding.source_project,
                "com.docker.compose.volume": "uploads_data",
            },
            "Options": None,
        }
        identity = MODULE._critical_volume_identity(
            volume_document,
            kind="uploads",
            binding=binding,
            expected_mountpoint=str(mountpoint),
        )
        held = MODULE.hold_volume("uploads", identity)
        try:
            with mock.patch.object(
                MODULE,
                "_inspect_required",
                return_value=volume_document,
            ):
                MODULE.verify_held_volume(held, binding)
                original = mountpoint.with_name("uploads-volume-original")
                mountpoint.rename(original)
                mountpoint.mkdir(mode=0o700)
                with self.assertRaisesRegex(
                    MODULE.SourceSnapshotError, "mountpoint changed"
                ):
                    MODULE.verify_held_volume(held, binding)
        finally:
            os.close(held.descriptor)

    def test_deterministic_safe_archive_round_trip_matches_tree_digest(self):
        source = self.root / "source"
        source.mkdir(mode=0o700)
        nested = source / "nested"
        nested.mkdir(mode=0o775)
        payload = nested / "payload.txt"
        payload.write_bytes(b"stable payload\n")
        payload.chmod(0o664)
        descriptor = os.open(
            source,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            inventory = MODULE._scan_tree(descriptor)
            archive = self.root / "snapshot.tar.gz"
            MODULE._create_archive(descriptor, inventory, archive)
            artifact = MODULE._validate_archive(archive, inventory)
            self.assertEqual(artifact, MODULE._hash_secure_artifact(archive))
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
            second_archive = self.root / "snapshot-second.tar.gz"
            MODULE._create_archive(descriptor, inventory, second_archive)
            self.assertEqual(
                MODULE._hash_secure_artifact(second_archive),
                artifact,
            )
            expected_tree = MODULE._canonical_tree_digest(descriptor)

            restored = self.root / "restored"
            restored.mkdir(mode=0o700)
            result = subprocess.run(
                [
                    MODULE.TAR,
                    "-xzf",
                    str(archive),
                    "--no-same-owner",
                    "--no-same-permissions",
                    "-C",
                    str(restored),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            restored_descriptor = os.open(
                restored,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                self.assertEqual(
                    MODULE._canonical_tree_digest(restored_descriptor),
                    expected_tree,
                )
            finally:
                os.close(restored_descriptor)
            downstream = subprocess.run(
                [
                    MODULE.TAR,
                    "--sort=name",
                    "--mtime=@0",
                    "--owner=0",
                    "--group=0",
                    "--numeric-owner",
                    "-cf",
                    "-",
                    "-C",
                    str(restored),
                    ".",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(
                downstream.returncode,
                0,
                downstream.stderr.decode(),
            )
            self.assertEqual(
                hashlib.sha256(downstream.stdout).hexdigest(),
                expected_tree,
            )
            self.assertEqual((restored / "nested").stat().st_mode & 0o777, 0o755)
            self.assertEqual(
                (restored / "nested" / "payload.txt").stat().st_mode & 0o777,
                0o644,
            )
        finally:
            os.close(descriptor)

    def test_unsafe_members_and_hardlinks_are_rejected(self):
        archive_path = self.root / "unsafe.tar.gz"
        with tarfile.open(archive_path, mode="w:gz") as archive:
            member = tarfile.TarInfo("../escape")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
        archive_path.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.SourceSnapshotError, "unsafe member"
        ):
            MODULE._validate_archive_shape(archive_path)

        link_archive = self.root / "link.tar.gz"
        with tarfile.open(link_archive, mode="w:gz") as archive:
            member = tarfile.TarInfo("link")
            member.type = tarfile.SYMTYPE
            member.linkname = "/etc/shadow"
            archive.addfile(member)
        link_archive.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.SourceSnapshotError, "unsafe member"
        ):
            MODULE._validate_archive_shape(link_archive)

        tree = self.root / "hardlink-tree"
        tree.mkdir()
        first = tree / "first"
        first.write_bytes(b"x")
        os.link(first, tree / "second")
        descriptor = os.open(
            tree,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            with self.assertRaisesRegex(
                MODULE.SourceSnapshotError, "hard-linked"
            ):
                MODULE._scan_tree(descriptor)
        finally:
            os.close(descriptor)

    def test_unstable_tree_retries_are_bounded_and_remove_partials(self):
        binding = self.fixture.binding()
        directory = self.root / "held"
        directory.mkdir()
        descriptor = os.open(
            directory,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        metadata = os.fstat(descriptor)
        held = MODULE.HeldVolume(
            kind="uploads",
            name=binding.volumes["uploads"],
            mountpoint=directory,
            descriptor=descriptor,
            stat_fields=MODULE._directory_stat_fields(metadata),
            inspect_sha256="1" * 64,
        )
        inventory = MODULE.TreeInventory((), 0, 0, "a" * 64)
        destination = self.root / "unstable.tar.gz"

        def create(_descriptor, _inventory, path):
            path.write_bytes(b"partial")
            path.chmod(0o600)

        digests = iter(
            [
                value
                for _attempt in range(MODULE.MAX_SNAPSHOT_ATTEMPTS)
                for value in ("1" * 64, "2" * 64, "1" * 64)
            ]
        )
        try:
            with (
                mock.patch.object(MODULE, "verify_held_volume"),
                mock.patch.object(
                    MODULE, "_scan_tree", return_value=inventory
                ),
                mock.patch.object(
                    MODULE,
                    "_canonical_tree_digest",
                    side_effect=lambda _descriptor: next(digests),
                ),
                mock.patch.object(
                    MODULE, "_create_archive", side_effect=create
                ) as create_mock,
                mock.patch.object(
                    MODULE,
                    "_validate_archive",
                    return_value=("f" * 64, 7),
                ),
                self.assertRaisesRegex(
                    MODULE.SourceSnapshotError, "did not remain stable"
                ),
            ):
                MODULE.snapshot_file_volume(
                    held,
                    destination,
                    binding,
                )
            self.assertEqual(
                create_mock.call_count,
                MODULE.MAX_SNAPSHOT_ATTEMPTS,
            )
            self.assertFalse(destination.exists())
        finally:
            os.close(descriptor)

    def test_database_fingerprint_uses_preserved_stream_api_contract(self):
        def query(sql: str) -> str:
            if sql == "SELECT version_num FROM alembic_version":
                return "revision_1"
            self.assertIn("ORDER BY tablename", sql)
            return "alpha\nbeta"

        def stream(sql: str) -> StreamDigest:
            if 'public."alpha"' in sql:
                return StreamDigest("1" * 64, 10, 2)
            if 'public."beta"' in sql:
                return StreamDigest("2" * 64, 20, 3)
            self.assertIn("pg_sequences", sql)
            return StreamDigest("3" * 64, 5, 1)

        result = MODULE.build_source_database(
            query=query,
            stream_copy=stream,
        )
        self.assertEqual(
            set(result),
            MODULE.SOURCE_DATABASE_FIELDS,
        )
        self.assertEqual(
            result["fingerprint_algorithm"],
            "pg-copy-jsonl-sha256-canonical-session-v1",
        )
        self.assertEqual(result["row_count"], 5)
        self.assertEqual(result["table_count"], 2)
        self.assertRegex(
            result["database_fingerprint_sha256"],
            r"^[0-9a-f]{64}$",
        )

    def test_restore_command_is_single_transaction_and_dump_is_held(self):
        dump = self.root / "database.dump"
        dump.write_bytes(b"custom dump")
        dump.chmod(0o600)
        captured: list[str] = []

        def run(arguments, descriptor, *, timeout):
            captured.extend(arguments)
            self.assertGreaterEqual(descriptor, 0)
            self.assertEqual(timeout, 3600)

        with mock.patch.object(
            MODULE,
            "_run_with_input_descriptor",
            side_effect=run,
        ):
            MODULE._restore_dump("scratch", dump)
        self.assertIn("--single-transaction", captured)
        self.assertIn("--exit-on-error", captured)
        self.assertEqual(captured[:4], [
            MODULE.DOCKER,
            "exec",
            "--interactive",
            "scratch",
        ])

    def test_restore_uses_one_volume_one_container_no_network_and_no_pull(self):
        binding = self.fixture.binding()
        postgres = MODULE.ImageIdentity(
            reference=binding.images["restore_postgres"],
            image_id="sha256:" + "9" * 64,
            labels={
                "org.opencontainers.image.revision": NEW_RELEASE_SHA,
                "trading-bot.postgres.runtime-uid": "70",
                "trading-bot.postgres.runtime-gid": "70",
            },
        )
        container, volume = MODULE._scratch_names(binding)
        labels = {
            **postgres.labels,
            **MODULE._scratch_labels(binding),
        }
        scratch_container = {
            "Id": "8" * 64,
            "Name": f"/{container}",
            "Image": postgres.image_id,
            "Config": {
                "Image": postgres.image_id,
                "Labels": labels,
            },
            "HostConfig": {
                "NetworkMode": "none",
                "PortBindings": {},
                "Privileged": False,
                "RestartPolicy": {
                    "Name": "no",
                    "MaximumRetryCount": 0,
                },
            },
            "Mounts": [
                {
                    "Type": "volume",
                    "Name": volume,
                    "Destination": "/var/lib/postgresql/data",
                    "RW": True,
                }
            ],
        }
        scratch_volume = {
            "Name": volume,
            "Driver": "local",
            "Labels": MODULE._scratch_labels(binding),
            "Options": None,
        }
        calls: list[list[str]] = []

        def run(arguments, **_kwargs):
            calls.append(list(arguments))
            if arguments[1:3] == ["volume", "create"]:
                return volume
            if arguments[1] == "run":
                return "8" * 64
            raise AssertionError(arguments)

        def inspect(kind, _name):
            if kind == "volume":
                return scratch_volume
            if kind == "container":
                return scratch_container
            raise AssertionError(kind)

        dump = self.root / "database.dump"
        dump.write_bytes(b"dump")
        dump.chmod(0o600)
        source_database = {
            "alembic_revision": "source_1",
            "fingerprint_algorithm": MODULE.DATABASE_FINGERPRINT_ALGORITHM,
            "database_fingerprint_sha256": "7" * 64,
            "row_count": 1,
            "table_count": 1,
        }
        with (
            mock.patch.object(
                MODULE,
                "cleanup_exact_scratch",
                side_effect=[False, False],
            ) as cleanup,
            mock.patch.object(MODULE, "_run", side_effect=run),
            mock.patch.object(
                MODULE, "_inspect_required", side_effect=inspect
            ),
            mock.patch.object(
                MODULE.subprocess,
                "run",
                return_value=types.SimpleNamespace(returncode=0),
            ),
            mock.patch.object(
                MODULE,
                "_scratch_query",
                side_effect=["1", "123456789012345678"],
            ),
            mock.patch.object(MODULE, "_restore_dump"),
            mock.patch.object(
                MODULE,
                "build_source_database",
                return_value=source_database,
            ),
        ):
            observed_database, restore = MODULE.restore_and_fingerprint(
                binding,
                dump=dump,
                postgres_image=postgres,
            )
        self.assertEqual(observed_database, source_database)
        self.assertTrue(restore["zero_residue"])
        self.assertEqual(cleanup.call_count, 2)
        run_calls = [arguments for arguments in calls if arguments[1] == "run"]
        self.assertEqual(len(run_calls), 1)
        run_argv = run_calls[0]
        self.assertEqual(
            run_argv[run_argv.index("--network") + 1],
            "none",
        )
        self.assertEqual(
            run_argv[run_argv.index("--pull") + 1],
            "never",
        )
        self.assertEqual(run_argv[-1], postgres.image_id)
        self.assertNotIn("trading_bot_db", run_argv)
        self.assertNotIn("trading_bot_app", run_argv)
        self.assertNotIn("trading_bot_redis", run_argv)
        self.assertEqual(
            sum(
                arguments[1:3] == ["volume", "create"]
                for arguments in calls
            ),
            1,
        )

    def test_cleanup_removes_only_exact_operation_labeled_scratch(self):
        binding = self.fixture.binding()
        container, volume = MODULE._scratch_names(binding)
        container_document = {
            "Id": "8" * 64,
            "Name": f"/{container}",
            "Image": "sha256:" + "9" * 64,
            "Config": {
                "Image": "sha256:" + "9" * 64,
                "Labels": MODULE._scratch_labels(binding),
            },
            "HostConfig": {
                "NetworkMode": "none",
                "PortBindings": {},
                "Privileged": False,
                "RestartPolicy": {
                    "Name": "no",
                    "MaximumRetryCount": 0,
                },
            },
            "Mounts": [
                {
                    "Type": "volume",
                    "Name": volume,
                    "Destination": "/var/lib/postgresql/data",
                    "RW": True,
                }
            ],
        }
        volume_document = {
            "Name": volume,
            "Driver": "local",
            "Labels": MODULE._scratch_labels(binding),
            "Options": None,
        }
        with (
            mock.patch.object(
                MODULE,
                "_inspect_optional",
                side_effect=[
                    container_document,
                    volume_document,
                    None,
                    None,
                ],
            ),
            mock.patch.object(MODULE, "_run", return_value="") as run,
        ):
            self.assertTrue(MODULE.cleanup_exact_scratch(binding))
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                [
                    MODULE.DOCKER,
                    "container",
                    "rm",
                    "--force",
                    container,
                ],
                [MODULE.DOCKER, "volume", "rm", volume],
            ],
        )

        forged = copy.deepcopy(container_document)
        forged["Config"]["Labels"][MODULE.LABEL_OPERATION] = (
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        )
        with (
            mock.patch.object(
                MODULE,
                "_inspect_optional",
                return_value=forged,
            ),
            mock.patch.object(MODULE, "_run") as run,
            self.assertRaisesRegex(
                MODULE.SourceSnapshotError, "identity"
            ),
        ):
            MODULE.cleanup_exact_scratch(binding)
        run.assert_not_called()

    def test_create_only_output_and_crash_stage_retry(self):
        binding = self.fixture.binding()
        output_root = self.root / "output"
        output_root.mkdir(mode=0o700)
        paths = MODULE.output_paths(output_root, binding)
        paths.operation_root.mkdir(mode=0o700)
        paths.role_root.mkdir(mode=0o700)
        paths.final.mkdir(mode=0o700)
        with self.assertRaisesRegex(
            MODULE.SourceSnapshotError, "already exists"
        ):
            MODULE._prepare_staging(paths, output_root)

        paths.final.rmdir()
        paths.staging.mkdir(mode=0o700)
        partial = paths.staging / "database.dump"
        partial.write_bytes(b"partial")
        partial.chmod(0o600)
        MODULE._prepare_staging(paths, output_root)
        self.assertTrue(paths.staging.is_dir())
        self.assertEqual(list(paths.staging.iterdir()), [])
        existing = paths.staging / "database.dump"
        existing.write_bytes(b"existing")
        existing.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.SourceSnapshotError, "already exists"
        ):
            MODULE._new_artifact_descriptor(existing)
        MODULE._publish_staging(paths)
        self.assertFalse(paths.staging.exists())
        self.assertEqual(
            (paths.final / "database.dump").read_bytes(),
            b"existing",
        )
        paths.staging.mkdir(mode=0o700)
        with self.assertRaisesRegex(
            MODULE.SourceSnapshotError, "already exists"
        ):
            MODULE._publish_staging(paths)

    def test_failure_path_runs_exact_scratch_cleanup_for_retry(self):
        binding = self.fixture.binding()
        output_root = self.root / "output"
        output_root.mkdir(mode=0o700)
        with (
            mock.patch.object(
                MODULE,
                "inspect_source",
                side_effect=MODULE.SourceSnapshotError("crash"),
            ),
            mock.patch.object(
                MODULE,
                "cleanup_exact_scratch",
            ) as cleanup,
            self.assertRaisesRegex(MODULE.SourceSnapshotError, "crash"),
        ):
            MODULE.execute(
                binding,
                output_root=output_root,
                freeze_path=None,
                freeze_sha256=None,
            )
        cleanup.assert_called_once_with(binding)

    def test_output_must_not_overlap_source_or_docker_volume_data(self):
        output_root = self.root / "output-overlap"
        output_root.mkdir(mode=0o700)
        inventory = MODULE.SourceInventory(
            containers={},
            images={},
            volumes={
                "uploads": {
                    "mountpoint": str(output_root / "source-volume"),
                }
            },
            canonical_sha256="1" * 64,
        )
        with self.assertRaisesRegex(
            MODULE.SourceSnapshotError,
            "overlaps a source volume",
        ):
            MODULE._validate_output_separation(output_root, inventory)

    def test_operation_lock_rejects_concurrent_same_operation(self):
        binding = self.fixture.binding()
        lock_root = self.root / "locks"
        lock_root.mkdir(mode=0o755)
        lock_root.chmod(0o755)
        with mock.patch.object(MODULE, "LOCK_ROOT", lock_root):
            with MODULE.operation_lock(binding):
                with self.assertRaisesRegex(
                    MODULE.SourceSnapshotError, "is active"
                ):
                    with MODULE.operation_lock(binding):
                        self.fail("concurrent lock unexpectedly acquired")

    def test_completed_output_is_fully_resume_verified_without_overwrite(self):
        binding = self.fixture.binding()
        output_root = self.root / "resume-output"
        output_root.mkdir(mode=0o700)
        paths = MODULE.output_paths(output_root, binding)
        paths.operation_root.mkdir(mode=0o700)
        paths.role_root.mkdir(mode=0o700)
        paths.final.mkdir(mode=0o700)

        database_path = paths.final / "database.dump"
        database_path.write_bytes(b"PGDMP resume fixture")
        database_path.chmod(0o600)
        database_artifact = MODULE._hash_secure_artifact(database_path)

        snapshots: dict[str, MODULE.FileSnapshot] = {}
        for target, character in (("uploads", "a"), ("audit", "b")):
            archive_path = paths.final / f"{target}.tar.gz"
            with tarfile.open(archive_path, mode="w:gz") as archive:
                member = tarfile.TarInfo(f"{target}.txt")
                payload = f"{target}\n".encode()
                member.size = len(payload)
                member.uid = 0
                member.gid = 0
                member.mtime = 0
                archive.addfile(member, io.BytesIO(payload))
            archive_path.chmod(0o600)
            artifact_hash, artifact_bytes = MODULE._hash_secure_artifact(
                archive_path
            )
            snapshots[target] = MODULE.FileSnapshot(
                artifact_hash,
                artifact_bytes,
                character * 64,
                1,
                len(payload),
                1,
            )

        images = {
            kind: MODULE.ImageIdentity(
                binding.images[kind],
                "sha256:" + character * 64,
                {},
            )
            for kind, character in zip(MODULE.IMAGE_KEYS, "1234")
        }
        volumes = {
            kind: {
                "name": binding.volumes[kind],
                "driver": "local",
                "mountpoint": f"/var/lib/docker/volumes/{binding.volumes[kind]}/_data",
                "labels_sha256": hashlib.sha256(b"{}").hexdigest(),
                "options_sha256": hashlib.sha256(b"{}").hexdigest(),
            }
            for kind in MODULE.VOLUME_KEYS
        }
        containers = {}
        for kind, character in zip(MODULE.SOURCE_CONTAINERS, "567"):
            containers[kind] = {
                "id": character * 64,
                "name": binding.containers[kind],
                "image_id": images[kind].image_id,
                "image_reference": binding.images[kind],
                "project": binding.source_project,
                "service": MODULE.SOURCE_SERVICES[kind],
                "running": True,
                "started_at": "2026-07-27T00:00:00Z",
                "restart_count": 0,
                "mounts": {
                    volume_kind: {
                        "name": binding.volumes[volume_kind],
                        "source": volumes[volume_kind]["mountpoint"],
                        "destination": destination,
                        "rw": True,
                    }
                    for volume_kind, destination in MODULE.SOURCE_MOUNTS[
                        kind
                    ].items()
                },
                "other_mount_count": 0,
                "other_mounts_sha256": hashlib.sha256(b"[]").hexdigest(),
            }
        public = {
            "containers": containers,
            "images": {
                kind: {
                    "reference": identity.reference,
                    "image_id": identity.image_id,
                }
                for kind, identity in sorted(images.items())
            },
            "volumes": volumes,
        }
        inventory = MODULE.SourceInventory(
            containers=containers,
            images=images,
            volumes=volumes,
            canonical_sha256=hashlib.sha256(
                canonical_bytes(public)
            ).hexdigest(),
        )
        source_database = {
            "alembic_revision": "source_1",
            "fingerprint_algorithm": MODULE.DATABASE_FINGERPRINT_ALGORITHM,
            "database_fingerprint_sha256": "e" * 64,
            "row_count": 5,
            "table_count": 2,
        }
        redis = {
            "policy": "sealed-rollback-evidence-only",
            "source_volume": binding.volumes["redis"],
            "tree_sha256": "c" * 64,
            "metadata_sha256": "d" * 64,
            "member_count": 1,
            "bytes": 100,
            "stable_attempt": 1,
            "archive_created": False,
            "restore": False,
        }
        restore = {
            "status": "passed",
            "postgres_image_reference": binding.images["restore_postgres"],
            "postgres_image_id": images["restore_postgres"].image_id,
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
        }
        document = MODULE._manifest_document(
            binding,
            inventory=inventory,
            freeze_sha256=None,
            database=database_artifact,
            uploads=snapshots["uploads"],
            audit=snapshots["audit"],
            redis=redis,
            source_database=source_database,
            restore=restore,
        )
        paths.manifest.write_bytes(canonical_bytes(document))
        paths.manifest.chmod(0o600)
        self.assertEqual(
            MODULE.verify_completed_output(
                paths,
                binding,
                freeze_sha256=None,
            ),
            document,
        )
        with mock.patch.object(
            MODULE,
            "cleanup_exact_scratch",
        ) as cleanup:
            result = MODULE.execute(
                binding,
                output_root=output_root,
                freeze_path=None,
                freeze_sha256=None,
            )
        self.assertEqual(result["status"], "resume-verified")
        cleanup.assert_called_once_with(binding)

        poisoned = copy.deepcopy(document)
        poisoned["source"]["containers"]["database"]["project"] = "forged"
        paths.manifest.write_bytes(canonical_bytes(poisoned))
        paths.manifest.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.SourceSnapshotError,
            "container identity",
        ):
            MODULE.verify_completed_output(
                paths,
                binding,
                freeze_sha256=None,
            )

    def test_manifest_rows_are_precommit_compatible_and_redis_is_not_artifact(self):
        binding = self.fixture.binding()
        images = {
            kind: MODULE.ImageIdentity(
                binding.images[kind],
                "sha256:" + character * 64,
                {},
            )
            for kind, character in zip(MODULE.IMAGE_KEYS, "1234")
        }
        inventory = MODULE.SourceInventory(
            containers={
                kind: {"id": character * 64}
                for kind, character in zip(MODULE.SOURCE_CONTAINERS, "567")
            },
            images=images,
            volumes={
                kind: {
                    "name": binding.volumes[kind],
                    "mountpoint": f"/volumes/{kind}",
                }
                for kind in MODULE.VOLUME_KEYS
            },
            canonical_sha256="8" * 64,
        )
        uploads = MODULE.FileSnapshot(
            "a" * 64, 100, "b" * 64, 2, 10, 1
        )
        audit = MODULE.FileSnapshot(
            "c" * 64, 200, "d" * 64, 3, 20, 1
        )
        source_database = {
            "alembic_revision": "source_1",
            "fingerprint_algorithm": MODULE.DATABASE_FINGERPRINT_ALGORITHM,
            "database_fingerprint_sha256": "e" * 64,
            "row_count": 5,
            "table_count": 2,
        }
        document = MODULE._manifest_document(
            binding,
            inventory=inventory,
            freeze_sha256=None,
            database=("9" * 64, 50),
            uploads=uploads,
            audit=audit,
            redis={
                "policy": "sealed-rollback-evidence-only",
                "archive_created": False,
                "restore": False,
            },
            source_database=source_database,
            restore={
                "status": "passed",
                "single_transaction": True,
                "network_mode": "none",
                "pull_policy": "never",
                "zero_residue": True,
            },
        )
        self.assertEqual(set(document["artifacts"]), {
            "database-backup",
            "uploads-archive",
            "audit-archive",
        })
        for row in document["artifacts"].values():
            self.assertEqual(set(row), MODULE.ARTIFACT_FIELDS)
        self.assertIsNone(
            document["artifacts"]["database-backup"][
                "restored_tree_sha256"
            ]
        )
        self.assertEqual(
            document["artifacts"]["uploads-archive"][
                "restored_tree_sha256"
            ],
            "b" * 64,
        )
        self.assertEqual(
            set(document["source_database"]),
            MODULE.SOURCE_DATABASE_FIELDS,
        )
        self.assertNotIn("redis", document["artifacts"])
        self.assertFalse(document["redis_rollback_only"]["restore"])


if __name__ == "__main__":
    unittest.main()
