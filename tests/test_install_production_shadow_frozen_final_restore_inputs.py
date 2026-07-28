from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

import yaml

from scripts import (
    install_production_shadow_frozen_final_restore_inputs as MODULE,
)
from tests.test_production_shadow_cutover_controller import manifest_payload
from tests.test_production_shadow_frozen_final_restore_worker import (
    restore_set_document,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def secure_file(path: Path, payload: bytes, mode: int = 0o600) -> str:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(payload)
    path.chmod(mode)
    return hashlib.sha256(payload).hexdigest()


def snapshot_archive(
    *,
    name: str = "snapshot.txt",
    payload: bytes = b"snapshot",
    trailing_slash: bool = False,
) -> bytes:
    raw = io.BytesIO()
    with gzip.GzipFile(
        filename="",
        fileobj=raw,
        mode="wb",
        mtime=0,
    ) as compressed:
        with tarfile.open(
            fileobj=compressed,
            mode="w|",
            format=tarfile.GNU_FORMAT,
        ) as archive:
            member = tarfile.TarInfo(
                name + ("/" if trailing_slash else "")
            )
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            member.mtime = 0
            member.mode = 0o644
            member.pax_headers = {}
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return raw.getvalue()


def refresh_restore_set(document: dict) -> None:
    for source in document["sources"].values():
        restore_input = {
            "source_snapshot_manifest_sha256": source[
                "source_snapshot_manifest_sha256"
            ],
            "source_snapshot_binding_sha256": source[
                "source_snapshot_binding_sha256"
            ],
            "freeze_evidence_sha256": source[
                "freeze_evidence_sha256"
            ],
            "live_lease_claim_sha256": source[
                "live_lease_claim_sha256"
            ],
            "source_identity_sha256": source[
                "source_identity_sha256"
            ],
            "artifacts": source["artifacts"],
            "source_database": source["source_database"],
        }
        source["restore_input_sha256"] = hashlib.sha256(
            canonical(restore_input)
        ).hexdigest()
    document["webapp_ir_transport"][
        "plaintext_restore_input_set_sha256"
    ] = document["sources"]["webapp_fi"]["restore_input_sha256"]
    postgres_set = {
        target: {
            "source_role": row["source_role"],
            "artifact": document["sources"][row["source_role"]][
                "artifacts"
            ]["database-backup"],
            "source_database": document["sources"][row["source_role"]][
                "source_database"
            ],
        }
        for target, row in document["target_map"].items()
    }
    file_set = {
        target: {
            "source_role": row["source_role"],
            "uploads-archive": document["sources"][row["source_role"]][
                "artifacts"
            ]["uploads-archive"],
            "audit-archive": document["sources"][row["source_role"]][
                "artifacts"
            ]["audit-archive"],
        }
        for target, row in document["target_map"].items()
    }
    document["postgres_snapshot_set_sha256"] = hashlib.sha256(
        canonical(postgres_set)
    ).hexdigest()
    document["reviewed_file_snapshot_set_sha256"] = hashlib.sha256(
        canonical(file_set)
    ).hexdigest()
    basis = {
        "schema": "production-shadow-frozen-final-restore-generation-v1",
        "operation_id": document["operation_id"],
        "release_sha": document["release_sha"],
        "release_tree_sha": document["release_tree_sha"],
        "controller_manifest_sha256": document[
            "controller_manifest_sha256"
        ],
        "approval_sha256": document["approval_sha256"],
        "target_map": document["target_map"],
        "sources": document["sources"],
        "nginx_freeze": document["nginx_freeze"],
        "snapshot_authorization_claim": document[
            "snapshot_authorization_claim"
        ],
        "webapp_ir_transport": document["webapp_ir_transport"],
    }
    document["restore_generation_sha256"] = hashlib.sha256(
        canonical(basis)
    ).hexdigest()


class Fixture:
    def __init__(self, root: Path, role: str = "bot_fi") -> None:
        self.root = root
        self.role = role
        self.project_prefix = root / "project"
        self.data_prefix = root / "data"
        self.secret_prefix = root / "secret"
        for path in (
            self.project_prefix,
            self.data_prefix,
            self.secret_prefix,
        ):
            path.mkdir(mode=0o700, parents=True)
            path.chmod(0o700)
        self.prefix_patch = mock.patch.multiple(
            MODULE.WORKER,
            PROJECT_ROOT_PREFIX=self.project_prefix,
            DATA_ROOT_PREFIX=self.data_prefix,
            SECRET_ROOT_PREFIX=self.secret_prefix,
        )
        self.prefix_patch.start()
        self.release_patch = mock.patch.object(
            MODULE,
            "_verify_immutable_release",
        )
        self.release_probe = self.release_patch.start()
        self.controller = manifest_payload()
        operation = self.controller["operation_id"]
        release = self.controller["release_sha"]
        self.project_root = self.project_prefix / operation
        self.release_root = self.project_root / "releases" / release
        self.incoming = self.project_root / "incoming"
        self.incoming.mkdir(mode=0o700, parents=True)
        self.release_root.mkdir(mode=0o700, parents=True)
        self.canonical_compose = (
            self.release_root / MODULE.TEMPLATE.CANONICAL_COMPOSE_RELATIVE_PATH
        )
        canonical_payload = (
            REPO_ROOT
            / MODULE.TEMPLATE.CANONICAL_COMPOSE_RELATIVE_PATH
        ).read_bytes()
        secure_file(self.canonical_compose, canonical_payload, 0o644)
        self.worker = (
            self.release_root
            / "scripts"
            / "production_shadow_frozen_final_restore_worker.py"
        )
        secure_file(
            self.worker,
            MODULE.WORKER.RUNNING_WORKER_PATH.read_bytes(),
            0o644,
        )

        canonical_document = yaml.safe_load(
            canonical_payload.decode("utf-8")
        )
        prepare_compose = MODULE.render_role_compose(
            canonical_document,
            role=MODULE.WORKER.ROLE_PATHS[role],
            scope="prepare",
        )
        prepare_compose[
            "x-production-shadow-runtime-image-ids"
        ] = dict(MODULE.PREPARE.RUNTIME_IMAGE_COMPOSE_EXTENSION)
        role_compose = MODULE.canonical_role_compose_bytes(
            prepare_compose
        )
        required = MODULE.required_environment_names(prepare_compose)
        optional = (
            MODULE.referenced_environment_names(prepare_compose)
            - required
        ) | MODULE.PREPARE.IMAGE_ENV_NAMES
        values = {
            name: "fixture"
            for name in required | optional
        }
        values.update(
            MODULE.PREPARE._operation_values(  # noqa: SLF001
                operation,
                release,
            )
        )
        values.update(
            {
                MODULE.PREPARE.IMAGE_ENV_BY_KIND[kind]: self.controller[
                    "artifacts"
                ]["role_runtime_image_ids"][role][kind]
                for kind in MODULE.PREPARE.IMAGE_KINDS
            }
        )
        environment = (
            MODULE.PREPARE.canonical_role_env_bytes(
                values,
                required_names=required,
                optional_names=optional,
            )
        )
        ca = (
            b"-----BEGIN CERTIFICATE-----\n"
            b"ZmFrZQ==\n"
            b"-----END CERTIFICATE-----\n"
        )
        payloads = {
            "role-compose.yml": role_compose,
            "runtime.env.role": environment,
            "ca.crt": ca,
        }
        destinations = {
            "role-compose.yml": (
                f"rendered/{MODULE.WORKER.ROLE_PATHS[role]}"
                "/docker-compose.yml"
            ),
            "runtime.env.role": (
                f"secrets/{MODULE.WORKER.ROLE_PATHS[role]}"
                "/runtime.env.role"
            ),
            "ca.crt": "secrets/tls/ca.crt",
        }
        internal = {
            "schema": (
                MODULE.PREPARE.WA_IR_FINAL_PREPARE_SCHEMA
                if role == "webapp_ir"
                else MODULE.PREPARE.FI_FINAL_PREPARE_SCHEMA
            ),
            "operation_id": operation,
            "release_sha": release,
            "operation_manifest_sha256": "a" * 64,
            "stage_attestation_sha256": "b" * 64,
            "role": role,
            "runtime_image_ids": dict(
                self.controller["artifacts"][
                    "role_runtime_image_ids"
                ][role]
            ),
            "entries": [
                {
                    "archive_path": name,
                    "destination": destinations[name],
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "bytes": len(payload),
                    "mode": "0600",
                }
                for name, payload in payloads.items()
            ],
            "required_env_keys": sorted(
                MODULE.parse_env_values(
                    environment.decode("ascii")
                )
            ),
        }
        material_payload = MODULE.PREPARE._tar_bytes(  # noqa: SLF001
            {
                MODULE.PREPARE.FINAL_PREPARE_MANIFEST_NAME: canonical(
                    internal
                ),
                **payloads,
            }
        )
        self.role_material = (
            self.incoming / MODULE.PREPARE.ROLE_ARCHIVE_NAMES[role]
        )
        material_sha = secure_file(
            self.role_material,
            material_payload,
        )
        self.controller["artifacts"]["role_materials"][role][
            "sha256"
        ] = material_sha
        self.controller["artifacts"]["role_materials"][role][
            "bytes"
        ] = len(material_payload)
        self.controller["artifacts"]["shadow_compose_sha256"] = (
            hashlib.sha256(canonical_payload).hexdigest()
        )
        self.controller_path = root / "inputs" / "controller.json"
        self.controller_sha256 = secure_file(
            self.controller_path,
            canonical(self.controller),
        )

        self.database_backup = (
            root / "inputs" / "artifacts" / "database.dump"
        )
        self.uploads_archive = (
            root / "inputs" / "artifacts" / "uploads.tar.gz"
        )
        self.audit_archive = (
            root / "inputs" / "artifacts" / "audit.tar.gz"
        )
        database = b"PGDMP-frozen-final"
        uploads = snapshot_archive(
            name="upload.txt",
            payload=b"upload",
        )
        audit = snapshot_archive(
            name="audit.log",
            payload=b"audit",
        )
        secure_file(self.database_backup, database)
        secure_file(self.uploads_archive, uploads)
        secure_file(self.audit_archive, audit)

        self.restore_document = restore_set_document()
        self.restore_document.update(
            {
                "campaign_id": self.controller["campaign_id"],
                "operation_id": operation,
                "release_sha": release,
                "release_tree_sha": self.controller["release_tree_sha"],
                "legacy_release_sha": self.controller[
                    "legacy_release_sha"
                ],
                "controller_manifest_sha256": self.controller_sha256,
                "approval_sha256": self.controller["artifacts"][
                    "cutover_approval_sha256"
                ],
                "approval_policy_sha256": self.controller["artifacts"][
                    "human_approval_policy_sha256"
                ],
            }
        )
        freeze_generation = self.controller["artifacts"][
            "nginx_freeze_generation_sha256"
        ]
        self.restore_document["nginx_freeze"][
            "global_generation_sha256"
        ] = freeze_generation
        for source in self.restore_document["sources"].values():
            source["freeze_generation_sha256"] = freeze_generation
            source["live_lease_claim_sha256"] = self.restore_document[
                "snapshot_authorization_claim"
            ]["claim_sha256"]
        source_role = self.restore_document["target_map"][role][
            "source_role"
        ]
        source = self.restore_document["sources"][source_role]
        source["artifacts"] = {
            "database-backup": {
                "sha256": hashlib.sha256(database).hexdigest(),
                "bytes": len(database),
                "restored_tree_sha256": None,
            },
            "uploads-archive": {
                "sha256": hashlib.sha256(uploads).hexdigest(),
                "bytes": len(uploads),
                "restored_tree_sha256": "c" * 64,
            },
            "audit-archive": {
                "sha256": hashlib.sha256(audit).hexdigest(),
                "bytes": len(audit),
                "restored_tree_sha256": "d" * 64,
            },
        }
        self.transport_manifest: Path | None = None
        self.transport_readback: Path | None = None
        refresh_restore_set(self.restore_document)
        if role == "webapp_ir":
            self._write_wa_transport(version_id="version-1")
        self._write_restore_set()

    def _write_wa_transport(self, *, version_id: str) -> None:
        source_hash = self.restore_document["sources"]["webapp_fi"][
            "restore_input_sha256"
        ]
        ciphertext = hashlib.sha256(b"ciphertext").hexdigest()
        readback = {
            "schema": MODULE.RESTORE_SET.IR_READBACK_SCHEMA,
            "status": "read-back-verified",
            "operation_id": self.controller["operation_id"],
            "release_sha": self.controller["release_sha"],
            "source_role": "webapp_fi",
            "target_role": "webapp_ir",
            "provider": "arvan-s3",
            "bucket": "private-bucket",
            "object_key": "frozen/final/object.age",
            "version_id": version_id,
            "ciphertext_sha256": ciphertext,
            "ciphertext_bytes": 17,
            "exact_version_requested": True,
            "body_sha256": ciphertext,
            "body_bytes": 17,
        }
        readback_payload = canonical(readback)
        readback_sha = hashlib.sha256(readback_payload).hexdigest()
        transport = {
            "schema": MODULE.RESTORE_SET.IR_TRANSPORT_SCHEMA,
            "status": "read-back-verified",
            "operation_id": self.controller["operation_id"],
            "release_sha": self.controller["release_sha"],
            "release_tree_sha": self.controller["release_tree_sha"],
            "controller_manifest_sha256": self.controller_sha256,
            "approval_sha256": self.controller["artifacts"][
                "cutover_approval_sha256"
            ],
            "source_role": "webapp_fi",
            "target_role": "webapp_ir",
            "provider": "arvan-s3",
            "bucket": "private-bucket",
            "private": True,
            "versioned": True,
            "encryption": "age",
            "recipient": "age1" + "q" * 58,
            "plaintext_restore_input_set_sha256": source_hash,
            "ciphertext_sha256": ciphertext,
            "ciphertext_bytes": 17,
            "object_key": "frozen/final/object.age",
            "version_id": version_id,
            "readback_receipt_sha256": readback_sha,
        }
        transport_payload = canonical(transport)
        self.transport_manifest = (
            self.root / "inputs" / "wa-transport.json"
        )
        self.transport_readback = (
            self.root / "inputs" / "wa-readback.json"
        )
        transport_sha = secure_file(
            self.transport_manifest,
            transport_payload,
        )
        secure_file(self.transport_readback, readback_payload)
        self.restore_document["webapp_ir_transport"] = {
            "transport_manifest_sha256": transport_sha,
            "readback_receipt_sha256": readback_sha,
            "provider": "arvan-s3",
            "bucket": "private-bucket",
            "private": True,
            "versioned": True,
            "encryption": "age",
            "recipient": "age1" + "q" * 58,
            "plaintext_restore_input_set_sha256": source_hash,
            "ciphertext_sha256": ciphertext,
            "ciphertext_bytes": 17,
            "object_key": "frozen/final/object.age",
            "version_id": version_id,
            "exact_version_readback_verified": True,
        }
        refresh_restore_set(self.restore_document)

    def _write_restore_set(self) -> None:
        payload = canonical(self.restore_document)
        digest = hashlib.sha256(payload).hexdigest()
        self.restore_path = (
            self.root
            / "inputs"
            / "restore-sets"
            / digest
            / MODULE.RESTORE_SET.OUTPUT_FILENAME
        )
        secure_file(self.restore_path, payload)

    def rewrite_restore_set(self) -> None:
        refresh_restore_set(self.restore_document)
        self._write_restore_set()

    def kwargs(self) -> dict:
        return {
            "controller_manifest": self.controller_path,
            "restore_set": self.restore_path,
            "role_material": self.role_material,
            "database_backup": self.database_backup,
            "uploads_archive": self.uploads_archive,
            "audit_archive": self.audit_archive,
            "canonical_compose": self.canonical_compose,
            "worker": self.worker,
            "expected_role": self.role,
            "webapp_ir_transport_manifest": self.transport_manifest,
            "webapp_ir_readback_receipt": self.transport_readback,
        }

    def close(self) -> None:
        self.release_patch.stop()
        self.prefix_patch.stop()


class FrozenFinalRestoreInputInstallerTests(unittest.TestCase):
    def make_fixture(self, role: str = "bot_fi") -> Fixture:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        fixture = Fixture(Path(temporary.name), role)
        self.addCleanup(fixture.close)
        return fixture

    def test_plan_derives_each_role_and_generation_paths(self) -> None:
        for role in MODULE.WORKER.ROLE_NAMES:
            with self.subTest(role=role):
                fixture = self.make_fixture(role)
                result = MODULE.execute_installation(**fixture.kwargs())
                self.assertEqual(result["status"], "planned")
                self.assertEqual(result["role"], role)
                self.assertEqual(
                    result["data_generation_root"],
                    str(
                        fixture.data_prefix
                        / fixture.controller["operation_id"]
                        / "frozen-final-generations"
                        / fixture.restore_document[
                            "restore_generation_sha256"
                        ]
                    ),
                )
                plan = MODULE.preflight_installation(**fixture.kwargs())
                self.assertNotIn(
                    "worker",
                    {spec.kind for spec in plan.outputs},
                )
                self.assertEqual(
                    plan.role_manifest["worker_path"],
                    str(fixture.worker),
                )
                self.assertEqual(
                    plan.installer_receipt["installed_files"]["worker"][
                        "sha256"
                    ],
                    hashlib.sha256(fixture.worker.read_bytes()).hexdigest(),
                )

    def test_wrong_caller_role_is_rejected(self) -> None:
        fixture = self.make_fixture()
        values = fixture.kwargs()
        values["expected_role"] = "webapp_fi"
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreInputInstallError,
            "caller role assertion",
        ):
            MODULE.preflight_installation(**values)

    def test_wrong_source_artifact_is_rejected(self) -> None:
        fixture = self.make_fixture()
        secure_file(
            fixture.database_backup,
            b"X" * fixture.database_backup.stat().st_size,
        )
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreInputInstallError,
            "identity differs",
        ):
            MODULE.preflight_installation(**fixture.kwargs())

    def test_wa_wrong_version_id_readback_is_rejected(self) -> None:
        fixture = self.make_fixture("webapp_ir")
        readback = json.loads(fixture.transport_readback.read_text())
        readback["version_id"] = "wrong-version"
        secure_file(fixture.transport_readback, canonical(readback))
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreInputInstallError,
            "exact-version",
        ):
            MODULE.preflight_installation(**fixture.kwargs())

    def test_rehearsal_overlap_is_rejected(self) -> None:
        fixture = self.make_fixture()
        rehearsal = (
            fixture.data_prefix
            / fixture.controller["operation_id"]
            / "restore-input"
            / "bot-fi"
        )
        database = rehearsal / "database.dump"
        uploads = rehearsal / "uploads.tar.gz"
        audit = rehearsal / "audit.tar.gz"
        secure_file(database, fixture.database_backup.read_bytes())
        secure_file(uploads, fixture.uploads_archive.read_bytes())
        secure_file(audit, fixture.audit_archive.read_bytes())
        values = fixture.kwargs()
        values.update(
            {
                "database_backup": database,
                "uploads_archive": uploads,
                "audit_archive": audit,
            }
        )
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreInputInstallError,
            "rehearsal",
        ):
            MODULE.preflight_installation(**values)

    def test_redis_archive_member_is_rejected(self) -> None:
        fixture = self.make_fixture()
        payload = snapshot_archive(name="redis/dump.rdb")
        # The path itself is unsafe before the missing-parent detail matters.
        secure_file(fixture.uploads_archive, payload)
        source = fixture.restore_document["sources"]["bot_fi"]
        source["artifacts"]["uploads-archive"].update(
            {
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        )
        fixture.rewrite_restore_set()
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreInputInstallError,
            "unsafe or Redis",
        ):
            MODULE.preflight_installation(**fixture.kwargs())

    def test_noncanonical_trailing_slash_member_is_rejected(self) -> None:
        fixture = self.make_fixture()
        payload = snapshot_archive(
            name="alias",
            payload=b"x",
            trailing_slash=True,
        )
        secure_file(fixture.uploads_archive, payload)
        source = fixture.restore_document["sources"]["bot_fi"]
        source["artifacts"]["uploads-archive"].update(
            {
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        )
        fixture.rewrite_restore_set()
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreInputInstallError,
            "unsafe",
        ):
            MODULE.preflight_installation(**fixture.kwargs())

    def test_legacy_writer_historical_claim_is_rejected(self) -> None:
        fixture = self.make_fixture()
        fixture.restore_document["snapshot_authorization_claim"][
            "owner_action"
        ] = "restore-legacy-writers"
        fixture.rewrite_restore_set()
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreInputInstallError,
            "restore set|provenance",
        ):
            MODULE.preflight_installation(**fixture.kwargs())

    def test_existing_different_output_is_rejected(self) -> None:
        fixture = self.make_fixture()
        plan = MODULE.preflight_installation(**fixture.kwargs())
        destination = next(
            spec.path
            for spec in plan.outputs
            if spec.kind == "database-backup"
        )
        secure_file(destination, b"different")
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreInputInstallError,
            "identity differs|unsafe",
        ):
            MODULE.preflight_installation(**fixture.kwargs())

    def test_symlink_and_hardlink_inputs_are_rejected(self) -> None:
        fixture = self.make_fixture()
        original = fixture.database_backup
        hardlink = original.with_name("hardlink.dump")
        os.link(original, hardlink)
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreInputInstallError,
            "root-only 0600",
        ):
            MODULE.preflight_installation(**fixture.kwargs())
        hardlink.unlink()
        link = original.with_name("link.dump")
        link.symlink_to(original)
        values = fixture.kwargs()
        values["database_backup"] = link
        with self.assertRaises(
            MODULE.FrozenFinalRestoreInputInstallError,
        ):
            MODULE.preflight_installation(**values)

    def test_create_link_crash_resumes_without_overwrite(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        root.chmod(0o700)
        destination = root / "result.json"
        payload = b'{"safe":true}'
        spec = MODULE.OutputSpec(
            kind="result",
            path=destination,
            sha256=hashlib.sha256(payload).hexdigest(),
            bytes=len(payload),
            payload=payload,
        )

        class Gate:
            def verify(self, _boundary):
                return {}

        real_unlink = MODULE._unlink_partial
        calls = 0

        def crash_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("crash-after-create-link")
            return real_unlink(*args, **kwargs)

        with mock.patch.object(
            MODULE,
            "_unlink_partial",
            side_effect=crash_once,
        ):
            with self.assertRaisesRegex(RuntimeError, "crash"):
                MODULE._publish_spec(spec, gate=Gate())
        partial = destination.with_name(MODULE._partial_name(spec))
        self.assertTrue(destination.exists())
        self.assertTrue(partial.exists())
        self.assertEqual(destination.stat().st_ino, partial.stat().st_ino)
        self.assertEqual(
            MODULE._publish_spec(spec, gate=Gate()),
            "reused",
        )
        self.assertFalse(partial.exists())
        self.assertEqual(destination.read_bytes(), payload)
        self.assertEqual(destination.stat().st_nlink, 1)

    def test_mid_copy_hard_crash_discards_only_bound_partial(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        root.chmod(0o700)
        source = root / "database.source"
        payload = b"PGDMP-frozen-final-database"
        secure_file(source, payload)
        destination = root / "database.dump"
        spec = MODULE.OutputSpec(
            kind="database-backup",
            path=destination,
            sha256=hashlib.sha256(payload).hexdigest(),
            bytes=len(payload),
            source=source,
        )
        boundaries: list[str] = []

        class Gate:
            def verify(self, boundary):
                boundaries.append(boundary)
                return {}

        class SimulatedHardCrash(BaseException):
            pass

        def crash_mid_copy(_source, destination_fd, **_kwargs):
            os.write(destination_fd, payload[:7])
            os.fsync(destination_fd)
            raise SimulatedHardCrash("crash-mid-copy")

        with mock.patch.object(
            MODULE,
            "_copy_held_source",
            side_effect=crash_mid_copy,
        ):
            with self.assertRaisesRegex(
                SimulatedHardCrash,
                "crash-mid-copy",
            ):
                MODULE._publish_spec(spec, gate=Gate())
        partial = destination.with_name(MODULE._partial_name(spec))
        self.assertFalse(destination.exists())
        self.assertEqual(partial.read_bytes(), payload[:7])
        source_metadata = source.stat()
        self.assertEqual(
            MODULE._inspect_spec(
                spec,
                source_identities=frozenset(
                    {(source_metadata.st_dev, source_metadata.st_ino)}
                ),
            ),
            ("absent", "recoverable"),
        )

        self.assertEqual(
            MODULE._publish_spec(spec, gate=Gate()),
            "created",
        )
        self.assertFalse(partial.exists())
        self.assertEqual(destination.read_bytes(), payload)
        self.assertIn(
            "before-discard-incomplete:database-backup",
            boundaries,
        )

    def test_corrupt_incomplete_partial_is_rejected_without_mutation(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        root.chmod(0o700)
        source = root / "database.source"
        payload = b"PGDMP-frozen-final-database"
        secure_file(source, payload)
        destination = root / "database.dump"
        spec = MODULE.OutputSpec(
            kind="database-backup",
            path=destination,
            sha256=hashlib.sha256(payload).hexdigest(),
            bytes=len(payload),
            source=source,
        )
        partial = destination.with_name(MODULE._partial_name(spec))
        corrupt = b"not-the-prefix"
        secure_file(partial, corrupt)
        source_metadata = source.stat()

        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreInputInstallError,
            "not a bound prefix",
        ):
            MODULE._inspect_spec(
                spec,
                source_identities=frozenset(
                    {(source_metadata.st_dev, source_metadata.st_ino)}
                ),
            )
        self.assertEqual(partial.read_bytes(), corrupt)
        self.assertFalse(destination.exists())

    def test_directory_boundary_label_matches_controller_contract(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        root.chmod(0o700)
        boundaries: list[str] = []

        class Gate:
            def verify(self, boundary):
                boundaries.append(boundary)
                return {}

        root_fd = os.open(root, MODULE._directory_flags())
        try:
            child_fd = MODULE._mkdir_open_at(
                root_fd,
                "child",
                label="PostgreSQL store / Child [A-Z]",
                gate=Gate(),
            )
            os.close(child_fd)
        finally:
            os.close(root_fd)
        self.assertEqual(len(boundaries), 1)
        self.assertRegex(
            boundaries[0],
            r"^[a-z0-9][a-z0-9:._-]{0,255}$",
        )
        self.assertNotIn(" ", boundaries[0])
        self.assertNotIn("/", boundaries[0])
        self.assertEqual(
            MODULE._authority_boundary_token(
                "PostgreSQL store / Child [A-Z]"
            ),
            MODULE._authority_boundary_token(
                "PostgreSQL store / Child [A-Z]"
            ),
        )

    def test_non_live_callback_fails_before_mutation(self) -> None:
        authority = MODULE.AuthorityBinding(
            envelope={},
            claim={"status": "active"},
            claim_sha256="1" * 64,
            claim_epoch=2,
            claim_nonce="2" * 64,
            receipt={},
            receipt_sha256="3" * 64,
        )

        def not_live(_claim, boundary):
            return {
                "schema": MODULE.WORKER.LIVE_AUTHORITY_SCHEMA,
                "status": "verified-live",
                "boundary": boundary,
                "claim_sha256": "1" * 64,
                "claim_epoch": 2,
                "claim_nonce": "2" * 64,
                "legacy_frozen_receipt_sha256": "3" * 64,
                "controller_lock_held": False,
                "controller_authoritative": True,
                "verification_sequence": 1,
                "verification_nonce": "4" * 64,
            }

        gate = MODULE._AuthorityGate(authority, not_live)
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreInputInstallError,
            "not live",
        ):
            gate.verify("before-mkdir:test")
        self.assertEqual(gate.transcript, [])

    def test_authority_transcript_is_hash_chained_and_bound(self) -> None:
        authority = MODULE.AuthorityBinding(
            envelope={},
            claim={"status": "active"},
            claim_sha256="1" * 64,
            claim_epoch=2,
            claim_nonce="2" * 64,
            receipt={},
            receipt_sha256="3" * 64,
        )
        sequence = 0

        def live(_claim, boundary):
            nonlocal sequence
            sequence += 1
            return {
                "schema": MODULE.WORKER.LIVE_AUTHORITY_SCHEMA,
                "status": "verified-live",
                "boundary": boundary,
                "claim_sha256": "1" * 64,
                "claim_epoch": 2,
                "claim_nonce": "2" * 64,
                "legacy_frozen_receipt_sha256": "3" * 64,
                "controller_lock_held": True,
                "controller_authoritative": True,
                "verification_sequence": sequence,
                "verification_nonce": format(sequence, "064x"),
            }

        gate = MODULE._AuthorityGate(authority, live)
        gate.verify("before-copy-source:database-backup")
        gate.verify("before-create-link:database-backup")
        self.assertEqual(len(gate.transcript), 2)
        self.assertEqual(
            gate.transcript[1]["previous_event_sha256"],
            gate.transcript[0]["event_sha256"],
        )
        self.assertEqual(
            gate.tail_sha256,
            gate.transcript[-1]["event_sha256"],
        )

    def test_installed_closure_loads_with_real_worker_manifest_parser(
        self,
    ) -> None:
        fixture = self.make_fixture()
        plan = MODULE.preflight_installation(**fixture.kwargs())
        authority = MODULE.AuthorityBinding(
            envelope={"status": "authorized"},
            claim={"status": "active"},
            claim_sha256="1" * 64,
            claim_epoch=2,
            claim_nonce="2" * 64,
            receipt={"state": "legacy-frozen"},
            receipt_sha256="3" * 64,
        )
        sequence = 0

        def live(_claim, boundary):
            nonlocal sequence
            sequence += 1
            return {
                "schema": MODULE.WORKER.LIVE_AUTHORITY_SCHEMA,
                "status": "verified-live",
                "boundary": boundary,
                "claim_sha256": authority.claim_sha256,
                "claim_epoch": authority.claim_epoch,
                "claim_nonce": authority.claim_nonce,
                "legacy_frozen_receipt_sha256": authority.receipt_sha256,
                "controller_lock_held": True,
                "controller_authoritative": True,
                "verification_sequence": sequence,
                "verification_nonce": format(sequence, "064x"),
            }

        with mock.patch.object(
            MODULE,
            "_load_authority",
            return_value=authority,
        ):
            result = MODULE.execute_installation(
                **fixture.kwargs(),
                apply=True,
                confirm=MODULE.confirmation_phrase(plan),
                execution_envelope=fixture.root / "authority-envelope.json",
                fresh_live_lease_claim=fixture.root / "live-claim.json",
                legacy_frozen_receipt=fixture.root / "legacy-receipt.json",
                live_authority_verifier=live,
            )
        self.assertEqual(result["status"], "installed")
        role_manifest = (
            plan.paths.secret_generation_root
            / "restore-role-manifest.json"
        )
        with mock.patch.multiple(
            MODULE.WORKER,
            _verify_immutable_release=mock.DEFAULT,
            RUNNING_WORKER_PATH=fixture.worker,
        ):
            loaded = MODULE.WORKER.load_role_manifest(role_manifest)
        self.assertEqual(loaded.role, fixture.role)
        self.assertEqual(
            loaded.restore_generation_sha256,
            fixture.restore_document["restore_generation_sha256"],
        )

    def test_artifact_drift_is_detected_while_held_copy_runs(self) -> None:
        fixture = self.make_fixture()
        identity = MODULE._hash_secure_file(
            fixture.database_backup,
            label="database",
            maximum=MODULE.MAX_ARTIFACT_BYTES,
        )
        fixture.database_backup.write_bytes(b"drift")
        fixture.database_backup.chmod(0o600)
        output = fixture.root / "copy"
        descriptor = os.open(
            output,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with self.assertRaisesRegex(
                MODULE.FrozenFinalRestoreInputInstallError,
                "unsafe|binding|changed",
            ):
                MODULE._copy_held_source(
                    fixture.database_backup,
                    descriptor,
                    expected_sha256=identity.sha256,
                    expected_bytes=identity.bytes,
                    label="database",
                )
        finally:
            os.close(descriptor)

    def test_cli_apply_is_fail_closed(self) -> None:
        fixture = self.make_fixture()
        with mock.patch.object(
            MODULE,
            "preflight_installation",
            return_value=mock.sentinel.plan,
        ):
            args = [
                "--controller-manifest",
                str(fixture.controller_path),
                "--restore-set",
                str(fixture.restore_path),
                "--role-material",
                str(fixture.role_material),
                "--database-backup",
                str(fixture.database_backup),
                "--uploads-archive",
                str(fixture.uploads_archive),
                "--audit-archive",
                str(fixture.audit_archive),
                "--canonical-compose",
                str(fixture.canonical_compose),
                "--worker",
                str(fixture.worker),
                "--apply",
            ]
            self.assertEqual(MODULE.main(args), 2)

    def test_real_worker_import_smoke_has_no_bytecode_residue(self) -> None:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(MODULE.WORKER.RUNNING_WORKER_PATH),
                "--help",
            ],
            cwd=REPO_ROOT,
            env=env,
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

    def test_staged_installer_startup_creates_no_release_residue(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = root / "release"
            shutil.copytree(
                REPO_ROOT / "scripts",
                release / "scripts",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            shutil.copytree(
                REPO_ROOT / "core",
                release / "core",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            subprocess.run(
                ["/usr/bin/git", "init", "-q", str(release)],
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            subprocess.run(
                ["/usr/bin/git", "add", "scripts", "core"],
                cwd=release,
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-c",
                    "user.name=fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "commit",
                    "-q",
                    "-m",
                    "staged immutable release",
                ],
                cwd=release,
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            subprocess.run(
                ["/usr/bin/git", "checkout", "-q", "--detach", "HEAD"],
                cwd=release,
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            before = {
                path.relative_to(release).as_posix()
                for path in release.rglob("*")
                if ".git" not in path.relative_to(release).parts
            }
            env = dict(os.environ)
            env.pop("PYTHONHOME", None)
            env.pop("PYTHONPATH", None)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(
                        release
                        / "scripts"
                        / (
                            "install_production_shadow_frozen_final_"
                            "restore_inputs.py"
                        )
                    ),
                    "--help",
                ],
                cwd=release,
                env=env,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            self.assertEqual(
                result.returncode,
                0,
                result.stderr.decode("utf-8", errors="replace"),
            )
            after = {
                path.relative_to(release).as_posix()
                for path in release.rglob("*")
                if ".git" not in path.relative_to(release).parts
            }
            self.assertEqual(after, before)
            status = subprocess.run(
                [
                    "/usr/bin/git",
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                    "--ignored",
                ],
                cwd=release,
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(status.stdout, b"")


if __name__ == "__main__":
    unittest.main()
