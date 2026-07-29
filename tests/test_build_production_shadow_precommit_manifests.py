from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest import mock

from core.production_shadow_authorization import authorization_basis_sha256
from scripts import build_production_shadow_precommit_manifests as MODULE
from scripts import production_shadow_precommit_worker as WORKER
from tests.test_production_shadow_cutover_controller import manifest_payload


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def secure_file(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(mode)


def deterministic_tar(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.GNU_FORMAT) as archive:
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
    return output.getvalue()


class PrecommitBuildFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.controller_path = root / "controller.json"
        self.artifact_directory = root / "artifacts"
        self.role_directory = root / "roles"
        self.prepare_path = self.role_directory / "prepare-metadata.json"
        self.release_root = root / "release"
        self.output = root / "output"
        self.artifact_directory.mkdir(mode=0o700)
        self.role_directory.mkdir(mode=0o700)
        self.release_root.mkdir(mode=0o755)
        self.output.mkdir(mode=0o700)

        self.controller = manifest_payload()
        self.operation_id = self.controller["operation_id"]
        self.release_sha = self.controller["release_sha"]
        self.runtime_ids = self.controller["artifacts"][
            "role_runtime_image_ids"
        ]

        scripts = self.release_root / "scripts"
        scripts.mkdir(mode=0o755)
        secure_file(
            scripts / "production_shadow_precommit_worker.py",
            b"worker fixture\n",
            0o644,
        )
        secure_file(
            scripts / "produce_production_shadow_readonly_acceptance.py",
            b"acceptance fixture\n",
            0o644,
        )
        versions = self.release_root / "migrations" / "versions"
        versions.mkdir(parents=True, mode=0o755)
        secure_file(
            versions / "001_base.py",
            b"revision = 'base_1'\ndown_revision = None\n",
            0o644,
        )
        secure_file(
            versions / "002_head.py",
            b"revision = 'head_2'\ndown_revision = 'base_1'\n",
            0o644,
        )

        self.bundle = b"exact release bundle fixture"
        secure_file(
            self.artifact_directory / "release.bundle",
            self.bundle,
        )
        self.controller["artifacts"]["release_bundle_sha256"] = (
            hashlib.sha256(self.bundle).hexdigest()
        )
        self.controller["artifacts"]["release_bundle_bytes"] = len(
            self.bundle
        )

        self.images: dict[str, bytes] = {}
        for index, kind in enumerate(MODULE.IMAGE_KINDS, start=1):
            payload = f"exact {kind} image archive {index}".encode()
            self.images[kind] = payload
            secure_file(
                self.artifact_directory / MODULE.IMAGE_FILENAMES[kind],
                payload,
            )
            row = self.controller["artifacts"]["image_artifacts"][kind]
            row["archive_sha256"] = hashlib.sha256(payload).hexdigest()
            row["archive_bytes"] = len(payload)

        self.role_payloads: dict[str, bytes] = {}
        self.role_details: dict[str, dict[str, str]] = {}
        for role in MODULE.ROLES:
            role_path = role.replace("_", "-")
            compose = f"services:\n  {role_path}: {{}}\n".encode()
            environment = (
                f"PRODUCTION_SHADOW_OPERATION_ID={self.operation_id}\n"
            ).encode()
            ca = f"fixture-ca:{role}".encode()
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
                        compose,
                    ),
                    (
                        "runtime.env.role",
                        f"secrets/{role_path}/runtime.env.role",
                        environment,
                    ),
                    ("ca.crt", "secrets/tls/ca.crt", ca),
                )
            ]
            internal = {
                "schema": MODULE.FINAL_PREPARE_SCHEMA,
                "operation_id": self.operation_id,
                "release_sha": self.release_sha,
                "operation_manifest_sha256": (
                    hashlib.sha256(f"stage:{role}".encode()).hexdigest()
                ),
                "stage_attestation_sha256": (
                    hashlib.sha256(f"attest:{role}".encode()).hexdigest()
                ),
                "role": role,
                "runtime_image_ids": self.runtime_ids[role],
                "entries": entries,
                "required_env_keys": [
                    "PRODUCTION_SHADOW_OPERATION_ID",
                ],
            }
            internal_payload = canonical(internal)
            archive = deterministic_tar(
                {
                    "final-prepare-manifest.json": internal_payload,
                    "role-compose.yml": compose,
                    "runtime.env.role": environment,
                    "ca.crt": ca,
                }
            )
            filename = f"role-material-{role_path}.tar"
            secure_file(self.role_directory / filename, archive)
            self.role_payloads[role] = archive
            self.role_details[role] = {
                "filename": filename,
                "sha256": hashlib.sha256(archive).hexdigest(),
                "bytes": len(archive),
                "format": "production-shadow-role-material-tar",
                "transport": self.controller["topology"][role]["transport"],
                "internal_manifest_sha256": hashlib.sha256(
                    internal_payload
                ).hexdigest(),
                "stage_operation_manifest_sha256": internal[
                    "operation_manifest_sha256"
                ],
                "stage_attestation_sha256": internal[
                    "stage_attestation_sha256"
                ],
            }
            self.controller["artifacts"]["role_materials"][role] = {
                key: self.role_details[role][key]
                for key in ("sha256", "bytes", "format", "transport")
            }

        for index, role in enumerate(("webapp_ir", "witness"), start=1):
            digest = hashlib.sha256(f"foreign-role:{role}".encode()).hexdigest()
            material = self.controller["artifacts"]["role_materials"][role]
            material["sha256"] = digest
            material["bytes"] = 1000 + index
            self.role_details[role] = {
                "filename": f"role-material-{role.replace('_', '-')}.tar",
                **material,
                "internal_manifest_sha256": hashlib.sha256(
                    f"internal:{role}".encode()
                ).hexdigest(),
                "stage_operation_manifest_sha256": hashlib.sha256(
                    f"stage:{role}".encode()
                ).hexdigest(),
                "stage_attestation_sha256": hashlib.sha256(
                    f"attest:{role}".encode()
                ).hexdigest(),
            }

        self.closure = {
            "schema": MODULE.CLOSURE_SCHEMA,
            "operation_id": self.operation_id,
            "release": {
                "commit_sha": self.release_sha,
                "tree_sha": self.controller["release_tree_sha"],
                "bundle": {
                    "filename": "release.bundle",
                    "sha256": hashlib.sha256(self.bundle).hexdigest(),
                    "bytes": len(self.bundle),
                },
            },
            "images": self.controller["artifacts"]["image_artifacts"],
            "source_engine_observations": {
                kind: {
                    "image_id": f"sha256:{str(index) * 64}",
                    "informational_only": True,
                }
                for index, kind in enumerate(MODULE.IMAGE_KINDS, start=1)
            },
            "verified_image_contracts": {
                kind: {
                    "os": "linux",
                    "architecture": "amd64",
                    "repo_tags": [],
                    "oci_revision": (
                        self.release_sha
                        if kind in {"app", "postgres"}
                        else None
                    ),
                }
                for kind in MODULE.IMAGE_KINDS
            },
            "constraints": {
                "source_backup_included": False,
                "role_material_included": False,
                "secrets_included": False,
                "network_transfer_performed": False,
                "container_runtime_changed": False,
            },
        }
        self.closure["verified_image_contracts"]["postgres"][
            "runtime_user"
        ] = {
            "uid": 70,
            "gid": 70,
            "uid_label": "trading-bot.postgres.runtime-uid",
            "gid_label": "trading-bot.postgres.runtime-gid",
        }
        self.closure_path = (
            self.artifact_directory / "closure-manifest.json"
        )
        secure_file(
            self.closure_path,
            json.dumps(self.closure, sort_keys=True, indent=2).encode()
            + b"\n",
        )

        self.prepare = {
            "schema": MODULE.PREPARE_SET_SCHEMA,
            "capabilities": list(
                MODULE.runtime_targets.RUNTIME_TARGET_CAPABILITIES
            ),
            "operation_id": self.operation_id,
            "release_sha": self.release_sha,
            "canonical_compose_sha256": self.controller["artifacts"][
                "shadow_compose_sha256"
            ],
            "dr_ca_sha256": "1" * 64,
            "dr_tls_attestation_sha256": "2" * 64,
            "dr_tls_attested_at_epoch": 1785190000,
            "roles": self.role_details,
            "controller_bindings": {
                "role_materials": self.controller["artifacts"][
                    "role_materials"
                ],
                "role_runtime_image_ids": self.runtime_ids,
                "convergence_runtime_targets": self.controller["artifacts"][
                    "convergence_runtime_targets"
                ],
            },
            "activation_secrets_included": False,
            "precommit_manifest_bound": False,
        }
        secure_file(self.prepare_path, canonical(self.prepare))
        secure_file(self.controller_path, canonical(self.controller))
        pending_controller = json.loads(canonical(self.controller))
        pending_controller["artifacts"]["cutover_approval_sha256"] = "0" * 64
        controller_receipt = MODULE.runtime_targets.build_runtime_target_derivation_receipt(
            campaign_id=pending_controller["campaign_id"],
            operation_id=pending_controller["operation_id"],
            release_sha=pending_controller["release_sha"],
            template_sha256=hashlib.sha256(
                canonical(pending_controller)
            ).hexdigest(),
            authorization_basis_sha256=authorization_basis_sha256(
                pending_controller
            ),
            canonical_compose_sha256=pending_controller["artifacts"][
                "shadow_compose_sha256"
            ],
            convergence_runtime_targets=pending_controller["artifacts"][
                "convergence_runtime_targets"
            ],
        )
        secure_file(
            MODULE.runtime_targets.runtime_target_derivation_receipt_path(
                self.controller_path
            ),
            canonical(controller_receipt),
        )
        self.controller_sha256 = hashlib.sha256(
            canonical(self.controller)
        ).hexdigest()

        self.source_paths: dict[str, Path] = {}
        for index, role in enumerate(MODULE.ROLES, start=1):
            source_directory = root / f"source-{role}"
            source_directory.mkdir(mode=0o700)
            artifact_rows: dict[str, dict[str, object]] = {}
            for kind, filename, tree in (
                ("database-backup", "database.dump", None),
                ("uploads-archive", "uploads.tar.gz", f"{index + 2}" * 64),
                ("audit-archive", "audit.tar.gz", f"{index + 4}" * 64),
            ):
                payload = f"{role}:{kind}".encode()
                secure_file(source_directory / filename, payload)
                artifact_rows[kind] = {
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "bytes": len(payload),
                    "restored_tree_sha256": tree,
                }
            project = "trading_bot" if role == "bot_fi" else "current"
            volume = lambda suffix: f"{project}_{suffix}"  # noqa: E731
            source = {
                "schema": MODULE.SOURCE_SNAPSHOT_SCHEMA,
                "status": "source-snapshot-created",
                "operation_id": self.operation_id,
                "role": role,
                "mode": "live-baseline",
                "release_sha": self.release_sha,
                "legacy_release_sha": self.controller["legacy_release_sha"],
                "source_project": project,
                "controller_manifest_sha256": self.controller_sha256,
                "approval_sha256": self.controller["artifacts"][
                    "cutover_approval_sha256"
                ],
                "binding_sha256": f"{index}" * 64,
                "freeze_evidence_sha256": None,
                "source": {
                    "containers": {},
                    "images": {},
                    "volumes": {},
                    "identity_sha256": f"{index + 1}" * 64,
                },
                "artifacts": artifact_rows,
                "source_database": {
                    "alembic_revision": "base_1",
                    "fingerprint_algorithm": (
                        "pg-copy-jsonl-sha256-canonical-session-v1"
                    ),
                    "database_fingerprint_sha256": f"{index + 6}" * 64,
                    "row_count": 10,
                    "table_count": 2,
                },
                "file_snapshots": {
                    "uploads": {
                        "source_volume": volume("uploads_data"),
                        "pre_tree_sha256": f"{index + 2}" * 64,
                        "archive_tree_sha256": f"{index + 2}" * 64,
                        "post_tree_sha256": f"{index + 2}" * 64,
                        "member_count": 1,
                        "expanded_bytes": 1,
                        "stable_attempt": 1,
                    },
                    "audit": {
                        "source_volume": volume("audit_data"),
                        "pre_tree_sha256": f"{index + 4}" * 64,
                        "archive_tree_sha256": f"{index + 4}" * 64,
                        "post_tree_sha256": f"{index + 4}" * 64,
                        "member_count": 1,
                        "expanded_bytes": 1,
                        "stable_attempt": 1,
                    },
                },
                "redis_rollback_only": {
                    "policy": "sealed-rollback-evidence-only",
                    "source_volume": volume("redis_data"),
                    "tree_sha256": f"{index + 7}" * 64,
                    "metadata_sha256": f"{index + 8}" * 64,
                    "member_count": 1,
                    "bytes": 1,
                    "stable_attempt": 1,
                    "archive_created": False,
                    "restore": False,
                },
                "restore_drill": {
                    "status": "passed",
                    "postgres_image_reference": (
                        "trading_bot_postgres_boottime:15-"
                        + self.release_sha
                    ),
                    "postgres_image_id": (
                        self.runtime_ids[role]["postgres"]
                    ),
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
            path = source_directory / "source-snapshot-manifest.json"
            secure_file(path, canonical(source))
            self.source_paths[role] = path

    def build(self):  # noqa: ANN201
        return MODULE.build_manifests(
            controller_manifest=self.controller_path,
            release_closure=self.closure_path,
            prepare_metadata=self.prepare_path,
            role_material_directory=self.role_directory,
            bot_fi_source_snapshot=self.source_paths["bot_fi"],
            webapp_fi_source_snapshot=self.source_paths["webapp_fi"],
            release_root=self.release_root,
        )

    def argv(self, *, apply: bool = False) -> list[str]:
        values = [
            "--controller-manifest",
            str(self.controller_path),
            "--release-closure",
            str(self.closure_path),
            "--prepare-metadata",
            str(self.prepare_path),
            "--role-material-directory",
            str(self.role_directory),
            "--bot-fi-source-snapshot",
            str(self.source_paths["bot_fi"]),
            "--webapp-fi-source-snapshot",
            str(self.source_paths["webapp_fi"]),
            "--release-root",
            str(self.release_root),
            "--output-directory",
            str(self.output),
        ]
        if apply:
            values.extend(
                (
                    "--apply",
                    "--confirm",
                    MODULE.confirmation_phrase(
                        self.operation_id,
                        self.release_sha,
                    ),
                )
            )
        return values


class ProductionShadowPrecommitManifestBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = PrecommitBuildFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_builds_exact_worker_compatible_manifests(self) -> None:
        manifests, summary = self.fixture.build()
        self.assertEqual(summary["status"], "validated")
        self.assertFalse(summary["network_io"])
        self.assertFalse(summary["production_mutated"])
        self.assertEqual(set(manifests), set(MODULE.ROLES))
        for role, document in manifests.items():
            self.assertEqual(set(document), WORKER.MANIFEST_FIELDS)
            self.assertEqual(document["role"], role)
            self.assertEqual(document["target_migration_revision"], "head_2")
            self.assertEqual(
                document["controller_manifest_sha256"],
                self.fixture.controller_sha256,
            )
            self.assertEqual(
                document["runtime_image_ids"],
                self.fixture.runtime_ids[role],
            )
            self.assertEqual(set(document["artifacts"]), set(WORKER.ARTIFACT_KINDS))
            self.assertEqual(
                document["artifacts"]["database-backup"],
                json.loads(
                    self.fixture.source_paths[role].read_bytes()
                )["artifacts"]["database-backup"],
            )

        publications = MODULE.publish_manifests(
            manifests,
            output_directory=self.fixture.output,
        )
        self.assertEqual(set(publications.values()), {"created"})
        repeated = MODULE.publish_manifests(
            manifests,
            output_directory=self.fixture.output,
        )
        self.assertEqual(set(repeated.values()), {"reused"})
        for role in MODULE.ROLES:
            output = self.fixture.output / MODULE.OUTPUT_FILENAMES[role]
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                output.read_bytes(),
                canonical(manifests[role]) + b"\n",
            )

            with mock.patch.multiple(
                WORKER,
                PROJECT_ROOT_PREFIX=self.fixture.root / "installed-project",
                DATA_ROOT_PREFIX=self.fixture.root / "installed-data",
                SECRET_ROOT_PREFIX=self.fixture.root / "installed-secret",
            ):
                installed = WORKER.operation_paths(
                    self.fixture.operation_id,
                    self.fixture.release_sha,
                    role,
                ).manifest
                secure_file(installed, output.read_bytes())
                loaded = WORKER.load_manifest(installed)
                self.assertEqual(loaded.role, role)
                self.assertEqual(
                    loaded.canonical_sha256,
                    hashlib.sha256(canonical(manifests[role])).hexdigest(),
                )

    def test_default_main_is_plan_only_and_apply_is_exact_idempotent(self) -> None:
        captured = io.StringIO()
        with redirect_stdout(captured):
            status = MODULE.main(self.fixture.argv())
        self.assertEqual(status, 0)
        planned = json.loads(captured.getvalue())
        self.assertEqual(planned["status"], "planned")
        self.assertFalse(planned["outputs_mutated"])
        self.assertEqual(list(self.fixture.output.iterdir()), [])

        captured = io.StringIO()
        with redirect_stdout(captured):
            status = MODULE.main(self.fixture.argv(apply=True))
        self.assertEqual(status, 0)
        applied = json.loads(captured.getvalue())
        self.assertEqual(applied["status"], "published")
        self.assertEqual(set(applied["publications"].values()), {"created"})

        captured = io.StringIO()
        with redirect_stdout(captured):
            status = MODULE.main(self.fixture.argv(apply=True))
        self.assertEqual(status, 0)
        repeated = json.loads(captured.getvalue())
        self.assertEqual(set(repeated["publications"].values()), {"reused"})

    def test_closure_prepare_snapshot_and_output_tampering_fail_closed(self) -> None:
        original = self.fixture.closure_path.read_bytes()
        closure = json.loads(original)
        closure["images"]["app"]["archive_bytes"] += 1
        secure_file(
            self.fixture.closure_path,
            json.dumps(closure, sort_keys=True, indent=2).encode() + b"\n",
        )
        with self.assertRaisesRegex(
            MODULE.PrecommitManifestBuildError,
            "controller manifest",
        ):
            self.fixture.build()
        secure_file(self.fixture.closure_path, original)

        source_path = self.fixture.source_paths["bot_fi"]
        source = json.loads(source_path.read_bytes())
        source["controller_manifest_sha256"] = "f" * 64
        secure_file(source_path, canonical(source))
        with self.assertRaisesRegex(
            MODULE.PrecommitManifestBuildError,
            "snapshot binding differs",
        ):
            self.fixture.build()
        source["controller_manifest_sha256"] = (
            self.fixture.controller_sha256
        )
        secure_file(source_path, canonical(source))

        manifests, _summary = self.fixture.build()
        foreign = self.fixture.output / MODULE.OUTPUT_FILENAMES["bot_fi"]
        secure_file(foreign, b"foreign")
        with self.assertRaisesRegex(
            MODULE.PrecommitManifestBuildError,
            "different bytes",
        ):
            MODULE.publish_manifests(
                manifests,
                output_directory=self.fixture.output,
            )
        self.assertFalse(
            (
                self.fixture.output
                / MODULE.OUTPUT_FILENAMES["webapp_fi"]
            ).exists()
        )

    def test_prepare_runtime_target_descriptor_must_match_v3_manifest(self) -> None:
        prepare = json.loads(self.fixture.prepare_path.read_bytes())
        prepare["controller_bindings"]["convergence_runtime_targets"][
            "sha256"
        ] = "f" * 64
        secure_file(self.fixture.prepare_path, canonical(prepare))
        with self.assertRaisesRegex(
            MODULE.PrecommitManifestBuildError,
            "runtime target descriptor",
        ):
            self.fixture.build()

        legacy = json.loads(self.fixture.prepare_path.read_bytes())
        legacy["schema"] = MODULE.PREPARE.LEGACY_SET_SCHEMA
        del legacy["capabilities"]
        secure_file(self.fixture.prepare_path, canonical(legacy))
        with self.assertRaisesRegex(
            MODULE.PrecommitManifestBuildError,
            "fresh v3 prepare material",
        ):
            self.fixture.build()

    def test_role_archive_and_migration_graph_are_closed(self) -> None:
        role_path = (
            self.fixture.role_directory
            / MODULE.OUTPUT_FILENAMES["bot_fi"].replace(
                "precommit-operation-",
                "role-material-",
            ).replace(".json", ".tar")
        )
        original = role_path.read_bytes()
        secure_file(role_path, original + b"tamper")
        with self.assertRaises(MODULE.PrecommitManifestBuildError):
            self.fixture.build()
        secure_file(role_path, original)

        versions = self.fixture.release_root / "migrations" / "versions"
        secure_file(
            versions / "003_other_head.py",
            b"revision = 'head_3'\ndown_revision = 'base_1'\n",
            0o644,
        )
        with self.assertRaisesRegex(
            MODULE.PrecommitManifestBuildError,
            "one closed head",
        ):
            self.fixture.build()


if __name__ == "__main__":
    unittest.main()
