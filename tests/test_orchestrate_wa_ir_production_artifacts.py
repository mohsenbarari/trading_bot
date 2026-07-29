from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode
import zipfile

from scripts import orchestrate_wa_ir_production_artifacts as MODULE
from scripts import produce_wa_ir_source_database_attestation as SOURCE
from scripts import wa_ir_production_operation as WA_OPERATION
from scripts.wa_ir_production_object_storage_transport import (
    EphemeralPresignedGet,
    PublishedObject,
)
from tests.test_wa_ir_production_operation import (
    OPERATION_ID,
    OperationFixture,
    secure_file,
)


def source_attestation(fixture: OperationFixture, path: Path) -> None:
    document = {
        "schema": SOURCE.ATTESTATION_SCHEMA,
        "status": "source-backup-database-attested",
        "operation_id": fixture.manifest.operation_id,
        "release_sha": fixture.manifest.release_sha,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "database_backup_sha256": fixture.manifest.artifacts[
            "database-backup"
        ].sha256,
        "database_backup_bytes": fixture.manifest.artifacts[
            "database-backup"
        ].bytes,
        "database_backup_object_key": (
            "dark-standby/source-backup/database.dump"
        ),
        "database_backup_version_id": "source-version-1",
        "postgres_image_id": next(
            image.image_id
            for image in fixture.manifest.images
            if image.role == "postgres"
        ),
        "scratch_postgres_system_id": "9000000000000000001",
        "scratch_container_id": "f" * 64,
        "recovered_prior_scratch_residue": False,
        "source_database": dict(fixture.manifest.source_database),
        "restore_single_transaction": True,
        "scratch_network_mode": "none",
        "source_database_mutated": False,
        "source_or_current_mounted": False,
        "scratch_resources_removed": True,
        "zero_residue": True,
    }
    secure_file(
        path,
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode(),
    )


def published_object(
    fixture: OperationFixture,
    artifact: MODULE.LocalArtifact,
    *,
    nonce: str = "a" * 32,
) -> PublishedObject:
    plaintext_sha256, plaintext_bytes = MODULE._hash_regular(
        artifact.source,
        maximum=artifact.max_bytes,
    )
    ciphertext_sha256 = hashlib.sha256(
        f"{artifact.kind}:{plaintext_sha256}".encode()
    ).hexdigest()
    key = (
        f"{MODULE.DEFAULT_PREFIX}/{fixture.manifest.operation_id}/"
        f"{artifact.kind}/{nonce}-{ciphertext_sha256}.age"
    )
    return PublishedObject(
        bucket=MODULE.PRODUCTION_BUCKET,
        object_key=key,
        version_id=f"version-{artifact.kind}",
        plaintext_sha256=plaintext_sha256,
        plaintext_bytes=plaintext_bytes,
        ciphertext_sha256=ciphertext_sha256,
        ciphertext_bytes=plaintext_bytes + 128,
        metadata={
            "destination-name": artifact.destination_name,
            "release-sha": fixture.manifest.release_sha,
            "transport-schema": MODULE.TRANSPORT_SCHEMA,
            "operation-id": fixture.manifest.operation_id,
            "artifact-kind": artifact.kind,
            "plaintext-sha256": plaintext_sha256,
            "ciphertext-sha256": ciphertext_sha256,
        },
    )


def presigned_get(published: PublishedObject) -> EphemeralPresignedGet:
    now = datetime.now(timezone.utc)
    date = now.strftime("%Y%m%d")
    query = urlencode(
        {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": (
                f"access/{date}/ir-thr-at1/s3/aws4_request"
            ),
            "X-Amz-Date": now.strftime("%Y%m%dT%H%M%SZ"),
            "X-Amz-Expires": "300",
            "X-Amz-Signature": "f" * 64,
            "X-Amz-SignedHeaders": "host",
            "versionId": published.version_id,
        }
    )
    return EphemeralPresignedGet(
        (
            "https://s3.ir-thr-at1.arvanstorage.ir/"
            f"{published.bucket}/{published.object_key}?{query}"
        ),
        expires_in_seconds=300,
        object_key=published.object_key,
        version_id=published.version_id,
    )


def receive_attestation(descriptor_payload: bytes) -> bytes:
    descriptor = json.loads(descriptor_payload)
    document = {
        "schema": MODULE.RECEIVE_ATTESTATION_SCHEMA,
        "status": "installed",
        "installation_result": "created",
        "operation_id": descriptor["operation_id"],
        "artifact_kind": descriptor["artifact_kind"],
        "destination_name": descriptor["destination_name"],
        "installed_relative_path": (
            f"{descriptor['operation_id']}/incoming/"
            f"{descriptor['destination_name']}"
        ),
        "bucket": descriptor["bucket"],
        "object_key": descriptor["object_key"],
        "version_id": descriptor["version_id"],
        "ciphertext_sha256": descriptor["ciphertext_sha256"],
        "ciphertext_bytes": descriptor["ciphertext_bytes"],
        "plaintext_sha256": descriptor["plaintext_sha256"],
        "plaintext_bytes": descriptor["plaintext_bytes"],
        "installed_mode": "0600",
        "presigned_url_persisted": False,
        "presigned_url_logged": False,
        "archive_extracted": False,
        "docker_image_loaded": False,
        "compose_started": False,
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


def operation_plan(fixture: OperationFixture) -> dict[str, object]:
    return {
        "schema": MODULE.OPERATION_ATTESTATION_SCHEMA,
        "status": "planned",
        "operation_id": fixture.manifest.operation_id,
        "release_sha": fixture.manifest.release_sha,
        "manifest_sha256": fixture.manifest.canonical_sha256,
        "required_confirmation": (
            f"stage-wa-ir-images:{fixture.manifest.operation_id}:"
            f"{fixture.manifest.release_sha}"
        ),
        "artifact_count": len(MODULE.EXPECTED_ARTIFACTS),
        "database_container_started": False,
        "public_app_started": False,
        "private_dr_workers_started": False,
        "writer_started": False,
        "persistent_resource_cleanup_performed": False,
        "bounded_ephemeral_oneoff_cleanup_performed": False,
        "removed_ephemeral_resources": [],
        "object_storage_mutated": False,
        "bootstrap_agent_verified": True,
        "bootstrap_agent_sha256": fixture.manifest.bootstrap_sha256,
        "bootstrap_agent_bytes": fixture.manifest.bootstrap_bytes,
    }


def operation_stage(fixture: OperationFixture) -> dict[str, object]:
    result = operation_plan(fixture)
    project_root = (
        MODULE.REMOTE_PROJECT_ROOT_PREFIX / fixture.manifest.operation_id
    )
    data_root = MODULE.REMOTE_DATA_ROOT_PREFIX / fixture.manifest.operation_id
    secret_root = (
        MODULE.REMOTE_SECRET_ROOT_PREFIX / fixture.manifest.operation_id
    )
    result.update(
        {
            "status": "wa-ir-images-staged",
            "required_confirmation": None,
            "materialized": {
                "release_root": str(
                    project_root
                    / "releases"
                    / fixture.manifest.release_sha
                ),
                "secrets_root": str(secret_root),
                "data_root": str(data_root),
                "runtime_material_installed": False,
                "uploads_tree": {
                    "tree_sha256": "1" * 64,
                    "directory_count": 0,
                    "file_count": 1,
                    "expanded_bytes": 6,
                },
                "audit_tree": {
                    "tree_sha256": "2" * 64,
                    "directory_count": 0,
                    "file_count": 1,
                    "expanded_bytes": 3,
                },
            },
            "images": list(fixture.image_stage["images"]),
            "image_stage": dict(fixture.image_stage),
            "stage_attestation_sha256": (
                fixture.stage_attestation_sha256
            ),
            "presigned_url_persisted": False,
            "legacy_resources_mutated": False,
            "completed_phases": [
                "received",
                "materialized",
                "images-loaded",
            ],
            "operation_state_sha256": "3" * 64,
            "cleanup_policy": MODULE._EXPECTED_STAGE_CLEANUP_POLICY,
            "functional_boundary": (
                MODULE._EXPECTED_STAGE_FUNCTIONAL_BOUNDARY
            ),
        }
    )
    return result


def operation_apply(fixture: OperationFixture) -> dict[str, object]:
    result = operation_plan(fixture)
    project_root = (
        MODULE.REMOTE_PROJECT_ROOT_PREFIX / fixture.manifest.operation_id
    )
    data_root = MODULE.REMOTE_DATA_ROOT_PREFIX / fixture.manifest.operation_id
    secret_root = (
        MODULE.REMOTE_SECRET_ROOT_PREFIX / fixture.manifest.operation_id
    )
    writer_state = {
        "active_site": None,
        "writer_epoch": 1,
        "control_state": "fenced",
        "witness_lease_id": None,
    }
    database_container = {
        "container_id": "a" * 64,
        "image_id": fixture.runtime_image_ids["postgres"],
        "project": fixture.manifest.project_name,
        "service": fixture.manifest.services["database"],
        "mount_type": "bind",
        "data_path": str(data_root / "webapp-ir" / "postgres"),
        "data_uid": fixture.manifest.postgres_runtime_uid,
        "data_gid": fixture.manifest.postgres_runtime_gid,
    }
    result.update(
        {
            "status": "wa-ir-shadow-data-ready-fenced",
            "required_confirmation": None,
            "database_container_started": True,
            "materialized": {
                "release_root": str(
                    project_root
                    / "releases"
                    / fixture.manifest.release_sha
                ),
                "secrets_root": str(secret_root),
                "data_root": str(data_root),
                "runtime_material_installed": True,
                "runtime_env": str(
                    secret_root / "webapp-ir" / "runtime.env.role"
                ),
                "compose": str(
                    project_root
                    / "rendered"
                    / "webapp-ir"
                    / "docker-compose.yml"
                ),
                "uploads_tree": {
                    "tree_sha256": "1" * 64,
                    "directory_count": 0,
                    "file_count": 1,
                    "expanded_bytes": 6,
                },
                "audit_tree": {
                    "tree_sha256": "2" * 64,
                    "directory_count": 0,
                    "file_count": 1,
                    "expanded_bytes": 3,
                },
            },
            "images": list(fixture.image_stage["images"]),
            "image_stage": dict(fixture.image_stage),
            "stage_attestation_sha256": (
                fixture.stage_attestation_sha256
            ),
            "final_prepare_material": {
                "final_prepare_manifest_sha256": hashlib.sha256(
                    json.dumps(
                        fixture.final_prepare_document,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
                "stage_attestation_sha256": (
                    fixture.stage_attestation_sha256
                ),
                "role_compose_sha256": hashlib.sha256(
                    fixture.role_compose
                ).hexdigest(),
                "role_env_sha256": hashlib.sha256(
                    fixture.runtime_env
                ).hexdigest(),
                "ca_sha256": hashlib.sha256(fixture.ca).hexdigest(),
                "runtime_image_ids": dict(fixture.runtime_image_ids),
                "runtime_env": str(
                    secret_root / "webapp-ir" / "runtime.env.role"
                ),
                "compose": str(
                    project_root
                    / "rendered"
                    / "webapp-ir"
                    / "docker-compose.yml"
                ),
            },
            "database": {
                "database_ready": True,
                "source_revision": fixture.manifest.source_database[
                    "alembic_revision"
                ],
                "migration_revision": fixture.manifest.expected_migration_revision,
                "restored_source_database_fingerprint_sha256": (
                    fixture.manifest.source_database[
                        "database_fingerprint_sha256"
                    ]
                ),
                "restored_source_database_row_count": (
                    fixture.manifest.source_database["row_count"]
                ),
                "restored_source_database_table_count": (
                    fixture.manifest.source_database["table_count"]
                ),
                "writer_fence_command_applied": True,
                "writer_state": writer_state,
                "database_container": database_container,
                "database_container_started": True,
                "public_app_started": False,
                "private_dr_workers_started": False,
                "writer_started": False,
                "persistent_resource_cleanup_performed": False,
                "bounded_ephemeral_oneoff_cleanup_performed": False,
                "removed_ephemeral_resources": [],
            },
            "presigned_url_persisted": False,
            "legacy_resources_mutated": False,
            "completed_phases": list(MODULE._COMPLETED_PHASES),
            "operation_state_sha256": "3" * 64,
            "cleanup_policy": MODULE._EXPECTED_CLEANUP_POLICY,
            "functional_boundary": MODULE._EXPECTED_FUNCTIONAL_BOUNDARY,
        }
    )
    return result


class ProductionArtifactOrchestratorTests(unittest.TestCase):
    def test_bootstrap_is_deterministic_and_bound_to_exact_release(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            work = fixture.root / "work"
            work.mkdir(mode=0o700)
            output = work / MODULE.BOOTSTRAP_DESTINATION_NAME
            secure_file(
                output.with_name(f".{output.name}.materializing"),
                b"partial zipapp",
            )
            observed = MODULE._build_bound_bootstrap_agent(
                fixture.manifest,
                fixture.incoming / "release.bundle",
                work_directory=work,
                output=output,
            )
            self.assertEqual(
                observed,
                (
                    fixture.manifest.bootstrap_sha256,
                    fixture.manifest.bootstrap_bytes,
                ),
            )
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(
                    archive.read("core/docker_image_identity.py"),
                    (
                        fixture.root
                        / "release-source"
                        / "core"
                        / "docker_image_identity.py"
                    ).read_bytes(),
                )
                self.assertEqual(
                    archive.read("core/__init__.py"),
                    b"",
                )
            os.link(
                output,
                output.with_name(f".{output.name}.materializing"),
            )
            secure_file(
                work / ".wa-ir-production-agent.candidate.pyz",
                b"stale candidate",
            )
            self.assertEqual(
                MODULE._build_bound_bootstrap_agent(
                    fixture.manifest,
                    fixture.incoming / "release.bundle",
                    work_directory=work,
                    output=output,
                ),
                observed,
            )
            self.assertFalse(
                output.with_name(f".{output.name}.materializing").exists()
            )
            self.assertFalse(
                (work / ".wa-ir-production-agent.candidate.pyz").exists()
            )
            wrong = json.loads(json.dumps(fixture.document))
            wrong["bootstrap"]["sha256"] = "0" * 64
            wrong_manifest = MODULE._load_manifest_bytes(
                json.dumps(wrong, sort_keys=True, separators=(",", ":")).encode()
            )
            second = fixture.root / "second"
            second.mkdir(mode=0o700)
            with self.assertRaises(MODULE.ProductionOrchestratorError):
                MODULE._build_bound_bootstrap_agent(
                    wrong_manifest,
                    fixture.incoming / "release.bundle",
                    work_directory=second,
                    output=second / MODULE.BOOTSTRAP_DESTINATION_NAME,
                )

    def test_orchestrator_lock_rejects_parallel_transfer_or_finalizer(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            journal = Path(raw)
            with MODULE._orchestrator_lock(journal):
                with self.assertRaises(MODULE.ProductionOrchestratorError):
                    with MODULE._orchestrator_lock(journal):
                        self.fail("parallel orchestrator invocation acquired the lock")
            with MODULE._orchestrator_lock(journal):
                pass

    def test_source_backup_prepublish_uses_transfer_journal_and_no_ssh(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            journal = fixture.root / "journal"
            journal.mkdir(mode=0o700)
            recipient = fixture.root / "recipient"
            credentials = fixture.root / "credentials"
            for path in (recipient, credentials):
                secure_file(path, b"private-fixture")
            artifact = MODULE.LocalArtifact(
                "database-backup",
                "database.dump",
                fixture.incoming / "database.dump",
            )
            published = published_object(fixture, artifact)
            with (
                patch.object(MODULE, "load_secure_credentials", return_value=Mock()),
                patch.object(MODULE, "build_client", return_value=Mock()),
                patch.object(
                    MODULE,
                    "publish_one",
                    return_value=published,
                ) as publish,
                patch.object(MODULE, "_run_ssh") as ssh,
            ):
                result = MODULE.publish_source_backup(
                    fixture.incoming / "database.dump",
                    operation_id=fixture.manifest.operation_id,
                    release_sha=fixture.manifest.release_sha,
                    recipient_file=recipient,
                    credentials_file=credentials,
                    journal_directory=journal,
                )
            self.assertEqual(
                result["status"],
                "source-backup-published-and-verified",
            )
            call = publish.call_args
            self.assertEqual(call.args[0], artifact)
            self.assertEqual(
                call.kwargs["journal_path"],
                journal / "publish-database-backup.json",
            )
            self.assertEqual(result["object"], published.evidence())
            self.assertFalse(result["presigned_url_persisted"])
            self.assertFalse(result["payload_bytes_over_ssh"])
            ssh.assert_not_called()

    def test_transfer_blocks_before_publish_without_exact_source_object(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            journal = fixture.root / "journal"
            journal.mkdir(mode=0o700)
            source = fixture.root / "source-attestation.json"
            identity = fixture.root / "ssh"
            source_attestation(fixture, source)
            secure_file(identity, b"private-fixture")
            with (
                patch.object(
                    MODULE,
                    "_verified_source_backup_publication",
                    side_effect=MODULE.ProductionOrchestratorError(
                        "source object differs"
                    ),
                ),
                patch.object(MODULE, "publish_one") as publish,
                patch.object(MODULE, "load_secure_credentials") as credentials,
                self.assertRaises(MODULE.ProductionOrchestratorError),
            ):
                MODULE.transfer_operation(
                    fixture.incoming / "operation-manifest.json",
                    fixture.incoming,
                    source_database_attestation=source,
                    recipient_file=fixture.root / "recipient",
                    credentials_file=fixture.root / "credentials",
                    journal_directory=journal,
                    ssh_identity=identity,
                )
            publish.assert_not_called()
            credentials.assert_not_called()

    def test_descriptor_and_ssh_delivery_are_control_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            identity = fixture.root / "ssh"
            secure_file(identity, b"fixture-private-key")
            artifact = MODULE.LocalArtifact(
                "database-backup",
                "database.dump",
                fixture.incoming / "database.dump",
            )
            published = published_object(fixture, artifact)
            presigned = presigned_get(published)
            descriptor = MODULE.build_receive_descriptor(
                operation_id=fixture.manifest.operation_id,
                artifact=artifact,
                published=published,
                presigned=presigned,
            )

            def runner(arguments, **kwargs):  # noqa: ANN001
                self.assertEqual(kwargs["input"], descriptor)
                self.assertNotIn(
                    fixture.payloads["database-backup"],
                    kwargs["input"],
                )
                command = arguments[-1]
                self.assertNotIn("scp", command)
                self.assertNotIn("rsync", command)
                self.assertNotIn("sftp", command)
                self.assertNotIn(
                    presigned.reveal_for_control_channel(),
                    " ".join(arguments),
                )
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout=receive_attestation(descriptor),
                    stderr=b"",
                )

            observed = MODULE.deliver_received_artifact(
                descriptor,
                ssh_identity=identity,
                runner=runner,
            )
            self.assertEqual(observed.artifact_kind, artifact.kind)

            invalid_url = EphemeralPresignedGet(
                presigned.reveal_for_control_channel().replace(
                    "X-Amz-SignedHeaders=host",
                    "X-Amz-SignedHeaders=content-type",
                ),
                expires_in_seconds=300,
                object_key=published.object_key,
                version_id=published.version_id,
            )
            with self.assertRaises(MODULE.ProductionOrchestratorError):
                MODULE.build_receive_descriptor(
                    operation_id=fixture.manifest.operation_id,
                    artifact=artifact,
                    published=published,
                    presigned=invalid_url,
                )

    def test_native_bootstrap_keeps_url_out_of_argv_and_installs_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            identity = fixture.root / "ssh"
            secure_file(identity, b"fixture-private-key")
            bootstrap = fixture.root / "bootstrap.pyz"
            secure_file(bootstrap, b"bootstrap")
            artifact = MODULE.LocalArtifact(
                MODULE.BOOTSTRAP_ARTIFACT_KIND,
                MODULE.BOOTSTRAP_DESTINATION_NAME,
                bootstrap,
                MODULE.MAX_BOOTSTRAP_BYTES,
            )
            published = published_object(fixture, artifact)
            presigned = presigned_get(published)

            def runner(arguments, **kwargs):  # noqa: ANN001
                control = kwargs["input"]
                self.assertEqual(len(control.decode().splitlines()), 9)
                live_url = presigned.reveal_for_control_channel()
                self.assertIn(live_url.encode(), control)
                self.assertNotIn(live_url, " ".join(arguments))
                self.assertIn("--config -", arguments[-1])
                self.assertNotIn('"$url"\n', arguments[-1].split("--output")[1])
                self.assertIn(".materializing", arguments[-1])
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout=(
                        f"{MODULE.BOOTSTRAP_ATTESTATION_SCHEMA}\n"
                        "created\n"
                        f"{fixture.manifest.operation_id}\n"
                        f"{published.object_key}\n"
                        f"{published.version_id}\n"
                        f"{published.plaintext_sha256}\n"
                    ).encode(),
                    stderr=b"",
                )

            result = MODULE.bootstrap_remote_agent(
                operation_id=fixture.manifest.operation_id,
                published=published,
                presigned=presigned,
                ssh_identity=identity,
                runner=runner,
            )
            self.assertEqual(result["installation_result"], "created")

    def test_remote_plan_and_apply_attestations_are_exact_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            identity = fixture.root / "ssh"
            secure_file(identity, b"fixture-private-key")
            invocations: list[tuple[list[str], bytes]] = []

            def runner_for(document):  # noqa: ANN001
                def runner(arguments, **kwargs):  # noqa: ANN001
                    invocations.append(
                        (list(arguments), bytes(kwargs["input"]))
                    )
                    return subprocess.CompletedProcess(
                        arguments,
                        0,
                        stdout=json.dumps(
                            document,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode(),
                        stderr=b"",
                    )

                return runner

            planned = MODULE.run_remote_operation(
                manifest=fixture.manifest,
                ssh_identity=identity,
                apply=False,
                runner=runner_for(operation_plan(fixture)),
            )
            self.assertEqual(planned["status"], "planned")
            staged = MODULE.run_remote_operation(
                manifest=fixture.manifest,
                ssh_identity=identity,
                apply=False,
                stage=True,
                confirm=(
                    f"stage-wa-ir-images:{fixture.manifest.operation_id}:"
                    f"{fixture.manifest.release_sha}"
                ),
                runner=runner_for(operation_stage(fixture)),
            )
            self.assertEqual(
                staged["stage_attestation_sha256"],
                fixture.stage_attestation_sha256,
            )
            applied = MODULE.run_remote_operation(
                manifest=fixture.manifest,
                ssh_identity=identity,
                apply=True,
                confirm=(
                    f"prepare-wa-ir:{fixture.manifest.operation_id}:"
                    f"{fixture.manifest.release_sha}"
                ),
                runner=runner_for(operation_apply(fixture)),
            )
            self.assertTrue(applied["database_container_started"])
            self.assertEqual(invocations[0][1], b"{}\n")
            self.assertEqual(invocations[1][1], b"")
            self.assertEqual(invocations[2][1], b"")
            for arguments, _control in invocations[:3]:
                remote = arguments[-1]
                self.assertIn("/usr/bin/python3 -I -B ", remote)
            self.assertNotIn("--control-fd", invocations[0][0][-1])
            self.assertIn("--control-fd 0", invocations[1][0][-1])
            self.assertIn("--control-fd 0", invocations[2][0][-1])

            legacy_materialized = operation_apply(fixture)
            legacy_root = (
                MODULE.REMOTE_OPERATIONS_ROOT
                / fixture.manifest.operation_id
            )
            legacy_materialized["materialized"]["release_root"] = str(
                legacy_root / "release"
            )
            with self.assertRaises(MODULE.ProductionOrchestratorError):
                MODULE.run_remote_operation(
                    manifest=fixture.manifest,
                    ssh_identity=identity,
                    apply=True,
                    confirm=(
                        f"prepare-wa-ir:{fixture.manifest.operation_id}:"
                        f"{fixture.manifest.release_sha}"
                    ),
                    runner=runner_for(legacy_materialized),
                )

            extra = operation_plan(fixture)
            extra["unexpected"] = "value"
            with self.assertRaises(MODULE.ProductionOrchestratorError):
                MODULE.run_remote_operation(
                    manifest=fixture.manifest,
                    ssh_identity=identity,
                    apply=False,
                    runner=runner_for(extra),
                )

            leaked = operation_plan(fixture)
            leaked["required_confirmation"] = "https://secret.invalid"
            with self.assertRaises(MODULE.ProductionOrchestratorError):
                MODULE.run_remote_operation(
                    manifest=fixture.manifest,
                    ssh_identity=identity,
                    apply=False,
                    runner=runner_for(leaked),
                )

    def test_streaming_ssh_holds_eof_only_pipe_until_remote_exit(self) -> None:
        code = (
            "import os,select,stat;"
            "assert stat.S_ISFIFO(os.fstat(0).st_mode);"
            "assert not select.select([0],[],[],0)[0];"
            "print('ok')"
        )
        with patch.object(MODULE, "SSH", "/usr/bin/python3"):
            output = MODULE._run_ssh(
                ["/usr/bin/python3", "-I", "-B", "-c", code],
                b"",
                timeout=5,
                liveness_only=True,
            )
        self.assertEqual(output, b"ok\n")

    def test_streaming_ssh_rejects_loss_flood_and_timeout(self) -> None:
        with (
            patch.object(MODULE, "SSH", "/usr/bin/python3"),
            self.assertRaisesRegex(
                MODULE.ProductionOrchestratorError,
                "failed closed",
            ),
        ):
            MODULE._run_ssh(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    "-c",
                    "raise SystemExit(7)",
                ],
                b"{}\n",
                timeout=5,
            )
        with (
            patch.object(MODULE, "SSH", "/usr/bin/python3"),
            patch.object(MODULE, "MAX_ATTESTATION_BYTES", 64),
            self.assertRaisesRegex(
                MODULE.ProductionOrchestratorError,
                "exceeded",
            ),
        ):
            MODULE._run_ssh(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    "-c",
                    "print('x' * 10000)",
                ],
                b"{}\n",
                timeout=5,
            )
        with (
            patch.object(MODULE, "SSH", "/usr/bin/python3"),
            self.assertRaisesRegex(
                MODULE.ProductionOrchestratorError,
                "timed out",
            ),
        ):
            MODULE._run_ssh(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    "-c",
                    "import time;time.sleep(5)",
                ],
                b"{}\n",
                timeout=1,
            )

    def test_streaming_ssh_kills_detached_setsid_child(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            pid_path = Path(raw) / "pid"
            code = (
                "import subprocess;"
                "from pathlib import Path;"
                "p=subprocess.Popen("
                "['/usr/bin/python3','-I','-B','-c',"
                "'import time;time.sleep(60)'],"
                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
                "stderr=subprocess.DEVNULL,start_new_session=True);"
                f"Path({str(pid_path)!r}).write_text(str(p.pid));"
                "print('done')"
            )
            with (
                patch.object(MODULE, "SSH", "/usr/bin/python3"),
                self.assertRaisesRegex(
                    MODULE.ProductionOrchestratorError,
                    "retained a descendant",
                ),
            ):
                MODULE._run_ssh(
                    ["/usr/bin/python3", "-I", "-B", "-c", code],
                    b"{}\n",
                    timeout=5,
                )
            pid = int(pid_path.read_text(encoding="ascii"))
            deadline = time.monotonic() + 2
            while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertFalse(Path(f"/proc/{pid}").exists())

    def test_streaming_ssh_reaps_double_fork_adopted_zombies(self) -> None:
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
            with patch.object(MODULE, "SSH", "/usr/bin/python3"):
                output = MODULE._run_ssh(
                    ["/usr/bin/python3", "-I", "-B", "-c", code],
                    b"{}\n",
                    timeout=5,
                )
            self.assertEqual(output, b"ok\n")
            pids = {
                int(child_path.read_text(encoding="ascii")),
                int(grandchild_path.read_text(encoding="ascii")),
            }
            self.assertTrue(
                all(not Path(f"/proc/{pid}").exists() for pid in pids)
            )
            self.assertEqual(MODULE._direct_child_baseline(), before)

    def test_streaming_ssh_timeout_kills_setsided_double_fork(self) -> None:
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
                patch.object(MODULE, "SSH", "/usr/bin/python3"),
                patch.object(WA_OPERATION, "PROCESS_TERM_GRACE_SECONDS", 0.1),
                patch.object(WA_OPERATION, "PROCESS_KILL_GRACE_SECONDS", 0.1),
                patch.object(
                    WA_OPERATION,
                    "PROCESS_TREE_QUIESCENCE_SECONDS",
                    0.05,
                ),
                self.assertRaisesRegex(
                    MODULE.ProductionOrchestratorError,
                    "timed out",
                ),
            ):
                MODULE._run_ssh(
                    ["/usr/bin/python3", "-I", "-B", "-c", code],
                    b"{}\n",
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

    def test_streaming_ssh_root_pidfd_contains_identity_failure(self) -> None:
        opened: list[tuple[int, int]] = []
        real_pidfd_open = os.pidfd_open

        def capture_pidfd(pid: int, flags: int = 0) -> int:
            descriptor = real_pidfd_open(pid, flags)
            opened.append((pid, descriptor))
            return descriptor

        with (
            patch.object(MODULE, "SSH", "/usr/bin/python3"),
            patch.object(
                MODULE,
                "_direct_child_baseline",
                return_value=frozenset(),
            ),
            patch.object(
                MODULE,
                "_read_process_identity",
                side_effect=MODULE.ProductionOperationError(
                    "forced SSH root identity failure"
                ),
            ),
            patch.object(
                MODULE.os,
                "pidfd_open",
                side_effect=capture_pidfd,
            ),
            self.assertRaisesRegex(
                MODULE.ProductionOrchestratorError,
                "process containment failed",
            ),
        ):
            MODULE._run_ssh(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    "-c",
                    "import time;time.sleep(60)",
                ],
                b"{}\n",
                timeout=5,
            )
        self.assertEqual(len(opened), 1)
        pid, descriptor = opened[0]
        self.assertFalse(Path(f"/proc/{pid}").exists())
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_streaming_ssh_cleanup_closes_all_after_baseexception(
        self,
    ) -> None:
        class FatalSsh(BaseException):
            pass

        class FatalSelectorClose(BaseException):
            pass

        with tempfile.TemporaryDirectory() as raw:
            pid_path = Path(raw) / "ssh-pid"
            original = FatalSsh("original SSH interruption")
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
                patch.object(MODULE, "SSH", "/usr/bin/python3"),
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
                self.assertRaises(FatalSsh) as raised,
            ):
                MODULE._run_ssh(
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
                    b"{}\n",
                    timeout=5,
                )
            self.assertIs(raised.exception, original)
            self.assertEqual(selector_close_calls, [True])
            self.assertEqual(len(spawned), 1)
            self.assertTrue(spawned[0].stdin.closed)
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

    def test_streaming_ssh_preserves_baseexception_after_cleanup_error(
        self,
    ) -> None:
        class FatalSsh(BaseException):
            pass

        with tempfile.TemporaryDirectory() as raw:
            pid_path = Path(raw) / "ssh-pid"
            original = FatalSsh("original SSH interruption")
            terminate = MODULE._terminate_process_tree

            def abort_select(*_args, **_kwargs):  # noqa: ANN002, ANN003
                deadline = time.monotonic() + 2
                while not pid_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                raise original

            def terminate_then_fail(*args, **kwargs):  # noqa: ANN002, ANN003
                terminate(*args, **kwargs)
                raise MODULE.ProductionOperationError(
                    "simulated SSH containment report failure"
                )

            with (
                patch.object(MODULE, "SSH", "/usr/bin/python3"),
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
                self.assertRaises(FatalSsh) as raised,
            ):
                MODULE._run_ssh(
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
                    b"{}\n",
                    timeout=5,
                )
            self.assertIs(raised.exception, original)
            pid = int(pid_path.read_text(encoding="ascii"))
            self.assertFalse(Path(f"/proc/{pid}").exists())
            self.assertIn(
                "containment cleanup also failed closed",
                "\n".join(getattr(raised.exception, "__notes__", ())),
            )

    def test_controller_signal_guard_is_reentrant_and_restored(self) -> None:
        before = {
            signum: signal.getsignal(signum)
            for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
        }
        with self.assertRaises(
            MODULE.ProductionOrchestratorCancellation
        ):
            with MODULE._signal_cancellation_guard():
                first = signal.getsignal(signal.SIGINT)
                self.assertTrue(callable(first))
                try:
                    first(signal.SIGINT, None)
                except MODULE.ProductionOrchestratorCancellation:
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

    def test_controller_signal_is_deferred_through_reconciliation(self) -> None:
        cleanup_finished = False
        with self.assertRaisesRegex(
            MODULE.ProductionOrchestratorCancellation,
            "SIGTERM",
        ):
            with MODULE._signal_cancellation_guard():
                with MODULE._signal_reconciliation_scope():
                    handler = signal.getsignal(signal.SIGTERM)
                    self.assertTrue(callable(handler))
                    handler(signal.SIGTERM, None)
                    cleanup_finished = True
        self.assertTrue(cleanup_finished)

    def test_reconciliation_signal_preserves_active_baseexception(self) -> None:
        class FatalControl(BaseException):
            pass

        original = FatalControl("original control failure")
        cleanup_finished = False
        with self.assertRaises(FatalControl) as raised:
            with MODULE._signal_cancellation_guard():
                try:
                    raise original
                finally:
                    with MODULE._signal_reconciliation_scope():
                        handler = signal.getsignal(signal.SIGINT)
                        self.assertTrue(callable(handler))
                        handler(signal.SIGINT, None)
                        cleanup_finished = True
        self.assertIs(raised.exception, original)
        self.assertTrue(cleanup_finished)

    def test_controller_signal_cancels_and_reaps_ssh_process(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            pid_path = Path(raw) / "ssh-pid"
            code = (
                "from pathlib import Path;"
                f"Path({str(pid_path)!r}).write_text("
                "__import__('os').getpid().__str__());"
                "__import__('time').sleep(60)"
            )

            def interrupt() -> None:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    try:
                        if pid_path.read_text(encoding="ascii").isdigit():
                            break
                    except OSError:
                        pass
                    time.sleep(0.01)
                os.kill(os.getpid(), signal.SIGTERM)

            sender = threading.Thread(target=interrupt)
            sender.start()
            with (
                patch.object(MODULE, "SSH", "/usr/bin/python3"),
                self.assertRaises(
                    MODULE.ProductionOrchestratorCancellation
                ),
            ):
                MODULE._run_ssh(
                    ["/usr/bin/python3", "-I", "-B", "-c", code],
                    b"{}\n",
                    timeout=5,
                )
            sender.join(timeout=2)
            self.assertFalse(sender.is_alive())
            pid = int(pid_path.read_text(encoding="ascii"))
            self.assertFalse(Path(f"/proc/{pid}").exists())

    def test_stage_outputs_are_canonical_bound_and_exact_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            attestation_path = fixture.root / "evidence" / "stage.json"
            binding_path = fixture.root / "evidence" / "binding.json"
            document = operation_stage(fixture)

            binding = MODULE.persist_stage_outputs(
                document,
                manifest=fixture.manifest,
                stage_attestation_output=attestation_path,
                stage_binding_output=binding_path,
            )

            expected_attestation = json.dumps(
                document,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
            self.assertEqual(attestation_path.read_bytes(), expected_attestation)
            self.assertFalse(attestation_path.read_bytes().endswith(b"\n"))
            self.assertEqual(
                attestation_path.stat().st_mode & 0o777,
                0o600,
            )
            self.assertEqual(binding_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                set(binding),
                MODULE._ROLE_IMAGE_STAGE_BINDING_FIELDS,
            )
            self.assertEqual(
                binding,
                {
                    "schema": MODULE.ROLE_IMAGE_STAGE_BINDING_SCHEMA,
                    "operation_id": fixture.manifest.operation_id,
                    "release_sha": fixture.manifest.release_sha,
                    "role": "webapp_ir",
                    "stage_operation_manifest_sha256": hashlib.sha256(
                        expected_attestation
                    ).hexdigest(),
                    "stage_attestation_sha256": (
                        fixture.stage_attestation_sha256
                    ),
                    "runtime_image_ids": fixture.runtime_image_ids,
                },
            )
            self.assertEqual(
                binding_path.read_bytes(),
                json.dumps(
                    binding,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode(),
            )

            repeated = MODULE.persist_stage_outputs(
                document,
                manifest=fixture.manifest,
                stage_attestation_output=attestation_path,
                stage_binding_output=binding_path,
            )
            self.assertEqual(repeated, binding)

    def test_stage_output_conflicts_and_tampering_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            attestation_path = fixture.root / "stage.json"
            binding_path = fixture.root / "binding.json"
            secure_file(attestation_path, b"foreign")
            with self.assertRaisesRegex(
                MODULE.ProductionOrchestratorError,
                "different bytes",
            ):
                MODULE.persist_stage_outputs(
                    operation_stage(fixture),
                    manifest=fixture.manifest,
                    stage_attestation_output=attestation_path,
                    stage_binding_output=binding_path,
                )
            self.assertFalse(binding_path.exists())

            tampered = operation_stage(fixture)
            tampered["stage_attestation_sha256"] = "0" * 64
            with self.assertRaisesRegex(
                MODULE.ProductionOrchestratorError,
                "binding differs",
            ):
                MODULE.build_stage_binding(
                    tampered,
                    manifest=fixture.manifest,
                )

            relative = Path("relative-stage.json")
            with self.assertRaisesRegex(
                MODULE.ProductionOrchestratorError,
                "distinct absolute paths",
            ):
                MODULE.persist_stage_outputs(
                    operation_stage(fixture),
                    manifest=fixture.manifest,
                    stage_attestation_output=relative,
                    stage_binding_output=binding_path,
                )
            with self.assertRaisesRegex(
                MODULE.ProductionOrchestratorError,
                "distinct absolute paths",
            ):
                MODULE.persist_stage_outputs(
                    operation_stage(fixture),
                    manifest=fixture.manifest,
                    stage_attestation_output=binding_path,
                    stage_binding_output=binding_path,
                )

    def test_final_prepare_transfer_is_one_exact_post_stage_object(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            journal = fixture.root / "journal"
            journal.mkdir(mode=0o700)
            identity = fixture.root / "ssh"
            recipient = fixture.root / "recipient"
            credentials = fixture.root / "credentials"
            stage_path = fixture.root / "stage-attestation.json"
            for path, payload in (
                (identity, b"fixture-private-key"),
                (recipient, b"age1fixture"),
                (credentials, b"fixture-credentials"),
            ):
                secure_file(path, payload)
            secure_file(
                stage_path,
                json.dumps(
                    operation_stage(fixture),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode(),
            )
            artifacts: list[MODULE.LocalArtifact] = []

            def fake_publish(artifact, **_kwargs):  # noqa: ANN001
                artifacts.append(artifact)
                return published_object(fixture, artifact)

            def fake_presign(_client, published, **_kwargs):  # noqa: ANN001
                return presigned_get(published)

            def fake_deliver(descriptor, **_kwargs):  # noqa: ANN001
                document = json.loads(descriptor.decode())
                return MODULE.RemoteAttestation(
                    artifact_kind=document["artifact_kind"],
                    destination_name=document["destination_name"],
                    object_key=document["object_key"],
                    version_id=document["version_id"],
                    plaintext_sha256=document["plaintext_sha256"],
                    plaintext_bytes=document["plaintext_bytes"],
                    installation_result="created",
                )

            with (
                patch.object(
                    MODULE,
                    "_load_orchestrator_journal",
                    return_value={"phase": "transferred"},
                ),
                patch.object(
                    MODULE,
                    "load_secure_credentials",
                    return_value=object(),
                ),
                patch.object(
                    MODULE,
                    "build_client",
                    return_value=object(),
                ),
                patch.object(
                    MODULE,
                    "publish_one",
                    side_effect=fake_publish,
                ),
                patch.object(
                    MODULE,
                    "presign_exact_get",
                    side_effect=fake_presign,
                ),
                patch.object(
                    MODULE,
                    "deliver_received_artifact",
                    side_effect=fake_deliver,
                ),
            ):
                result = MODULE.transfer_final_prepare_material(
                    fixture.incoming / "operation-manifest.json",
                    fixture.final_archive_path,
                    stage_path,
                    recipient_file=recipient,
                    credentials_file=credentials,
                    journal_directory=journal,
                    ssh_identity=identity,
                )
            self.assertEqual(result["status"], "final-prepare-transferred")
            self.assertEqual(len(artifacts), 1)
            self.assertEqual(
                (
                    artifacts[0].kind,
                    artifacts[0].destination_name,
                ),
                (
                    MODULE.FINAL_PREPARE_ARTIFACT_KIND,
                    MODULE.FINAL_PREPARE_DESTINATION_NAME,
                ),
            )
            persisted = (
                journal / "final-prepare-transfer.json"
            ).read_bytes()
            self.assertNotIn(b"https://", persisted)
            self.assertEqual(
                json.loads(persisted)["object"]["version_id"],
                result["object"]["version_id"],
            )

            with (
                patch.object(
                    MODULE,
                    "_load_orchestrator_journal",
                    return_value={"phase": "transferred"},
                ),
                patch.object(MODULE, "publish_one") as no_publish,
            ):
                repeated = MODULE.transfer_final_prepare_material(
                    fixture.incoming / "operation-manifest.json",
                    fixture.final_archive_path,
                    stage_path,
                    recipient_file=recipient,
                    credentials_file=credentials,
                    journal_directory=journal,
                    ssh_identity=identity,
                )
            no_publish.assert_not_called()
            self.assertEqual(
                repeated["status"],
                "final-prepare-already-transferred",
            )

    def test_transfer_publishes_exact_artifact_closure_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            journal = fixture.root / "journal"
            journal.mkdir(mode=0o700)
            recipient = fixture.root / "recipient"
            credentials = fixture.root / "credentials"
            identity = fixture.root / "ssh"
            source = fixture.root / "source-attestation.json"
            for path in (recipient, credentials, identity):
                secure_file(path, b"private-fixture")
            source_attestation(fixture, source)
            published_kinds: list[str] = []
            database_artifact = MODULE.LocalArtifact(
                "database-backup",
                "database.dump",
                fixture.incoming / "database.dump",
            )
            source_backup_publication = published_object(
                fixture,
                database_artifact,
            )

            def publish(artifact, **_kwargs):  # noqa: ANN001
                published_kinds.append(artifact.kind)
                return published_object(fixture, artifact)

            def presign(_client, published, **_kwargs):  # noqa: ANN001
                return presigned_get(published)

            def deliver(descriptor, **_kwargs):  # noqa: ANN001
                parsed = json.loads(descriptor)
                return MODULE.RemoteAttestation(
                    artifact_kind=parsed["artifact_kind"],
                    destination_name=parsed["destination_name"],
                    object_key=parsed["object_key"],
                    version_id=parsed["version_id"],
                    plaintext_sha256=parsed["plaintext_sha256"],
                    plaintext_bytes=parsed["plaintext_bytes"],
                    installation_result="created",
                )

            def bootstrap(**kwargs):  # noqa: ANN003
                published = kwargs["published"]
                return {
                    "schema": MODULE.BOOTSTRAP_ATTESTATION_SCHEMA,
                    "installation_result": "created",
                    "operation_id": fixture.manifest.operation_id,
                    "object_key": published.object_key,
                    "version_id": published.version_id,
                    "plaintext_sha256": published.plaintext_sha256,
                    "presigned_url_persisted": False,
                }

            with (
                patch.object(MODULE, "load_secure_credentials", return_value=Mock()),
                patch.object(MODULE, "build_client", return_value=Mock()),
                patch.object(
                    MODULE,
                    "_verified_source_backup_publication",
                    return_value=source_backup_publication,
                ) as verified_source,
                patch.object(MODULE, "publish_one", side_effect=publish) as publish_mock,
                patch.object(MODULE, "presign_exact_get", side_effect=presign),
                patch.object(MODULE, "deliver_received_artifact", side_effect=deliver),
                patch.object(MODULE, "bootstrap_remote_agent", side_effect=bootstrap),
            ):
                result = MODULE.transfer_operation(
                    fixture.incoming / "operation-manifest.json",
                    fixture.incoming,
                    source_database_attestation=source,
                    recipient_file=recipient,
                    credentials_file=credentials,
                    journal_directory=journal,
                    ssh_identity=identity,
                )
                self.assertEqual(
                    result["object_count"],
                    len(MODULE.EXPECTED_ARTIFACTS) + 2,
                )
                self.assertEqual(
                    verified_source.call_args.args[0],
                    journal / "publish-database-backup.json",
                )
                database_publish_call = next(
                    call
                    for call in publish_mock.call_args_list
                    if call.args[0].kind == "database-backup"
                )
                self.assertEqual(
                    database_publish_call.kwargs["journal_path"],
                    journal / "publish-database-backup.json",
                )
                self.assertEqual(
                    set(published_kinds),
                    set(MODULE.EXPECTED_ARTIFACTS)
                    | {
                        MODULE.BOOTSTRAP_ARTIFACT_KIND,
                        "operation-manifest",
                    },
                )
                state_payload = (journal / "orchestrator.json").read_bytes()
                self.assertNotIn(b"https://", state_payload)
                publish_mock.reset_mock()
                repeated = MODULE.transfer_operation(
                    fixture.incoming / "operation-manifest.json",
                    fixture.incoming,
                    source_database_attestation=source,
                    recipient_file=recipient,
                    credentials_file=credentials,
                    journal_directory=journal,
                    ssh_identity=identity,
                )
                self.assertEqual(repeated["status"], "already-transferred")
                publish_mock.assert_not_called()

    def test_orchestrator_journal_rejects_presigned_url(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "journal.json"
            with self.assertRaises(MODULE.ProductionOrchestratorError):
                MODULE._write_orchestrator_journal(
                    path,
                    {"url": "https://secret.invalid"},
                    create=True,
                )

    def test_orchestrator_journal_is_exact_closed_and_object_bound(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            path = fixture.root / "orchestrator.json"
            artifact = MODULE.LocalArtifact(
                "database-backup",
                "database.dump",
                fixture.incoming / "database.dump",
            )
            published = published_object(fixture, artifact)
            remote = MODULE.RemoteAttestation(
                artifact_kind=artifact.kind,
                destination_name=artifact.destination_name,
                object_key=published.object_key,
                version_id=published.version_id,
                plaintext_sha256=published.plaintext_sha256,
                plaintext_bytes=published.plaintext_bytes,
                installation_result="created",
            )
            baseline = {
                "schema": MODULE.ORCHESTRATOR_JOURNAL_SCHEMA,
                "operation_id": fixture.manifest.operation_id,
                "release_sha": fixture.manifest.release_sha,
                "manifest_sha256": fixture.manifest.canonical_sha256,
                "phase": "publishing",
                "objects": {artifact.kind: published.evidence()},
                "remote_attestations": {artifact.kind: remote.evidence()},
                "presigned_url_persisted": False,
                "cleanup_policy": MODULE._TRANSFER_CLEANUP_POLICY,
            }
            MODULE._write_orchestrator_journal(path, baseline, create=True)
            observed = MODULE._load_orchestrator_journal(
                path,
                operation_id=fixture.manifest.operation_id,
                release_sha=fixture.manifest.release_sha,
                manifest_sha256=fixture.manifest.canonical_sha256,
            )
            self.assertEqual(observed, baseline)

            mutations: list[dict[str, object]] = []
            extra = json.loads(json.dumps(baseline))
            extra["unexpected"] = "field"
            mutations.append(extra)
            wrong_release = json.loads(json.dumps(baseline))
            wrong_release["objects"][artifact.kind]["metadata"]["release-sha"] = (
                "0" * 40
            )
            mutations.append(wrong_release)
            wrong_version = json.loads(json.dumps(baseline))
            wrong_version["remote_attestations"][artifact.kind]["version_id"] = (
                "different-version"
            )
            mutations.append(wrong_version)
            leaked = json.loads(json.dumps(baseline))
            leaked["objects"][artifact.kind]["metadata"]["release-sha"] = (
                "AWS4_REQUEST"
            )
            mutations.append(leaked)
            for mutation in mutations:
                MODULE._write_orchestrator_journal(path, baseline, create=False)
                if mutation is leaked:
                    with self.assertRaises(MODULE.ProductionOrchestratorError):
                        MODULE._write_orchestrator_journal(
                            path,
                            mutation,
                            create=False,
                        )
                    continue
                MODULE._write_orchestrator_journal(path, mutation, create=False)
                with self.assertRaises(MODULE.ProductionOrchestratorError):
                    MODULE._load_orchestrator_journal(
                        path,
                        operation_id=fixture.manifest.operation_id,
                        release_sha=fixture.manifest.release_sha,
                        manifest_sha256=fixture.manifest.canonical_sha256,
                    )

    def test_finalizer_removes_only_exact_local_ciphertexts_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = OperationFixture(Path(raw))
            journal = fixture.root / "journal"
            journal.mkdir(mode=0o700)
            kinds = set(MODULE.EXPECTED_ARTIFACTS) | {
                MODULE.BOOTSTRAP_ARTIFACT_KIND,
                "operation-manifest",
            }
            state: dict[str, object] = {
                "schema": MODULE.ORCHESTRATOR_JOURNAL_SCHEMA,
                "operation_id": fixture.manifest.operation_id,
                "release_sha": fixture.manifest.release_sha,
                "manifest_sha256": fixture.manifest.canonical_sha256,
                "phase": "transferred",
                "objects": {},
                "remote_attestations": {},
                "presigned_url_persisted": False,
                "cleanup_policy": MODULE._TRANSFER_CLEANUP_POLICY,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "functional_boundary": MODULE._TRANSFER_FUNCTIONAL_BOUNDARY,
            }
            publications: dict[str, dict[str, object]] = {}
            publication_journals: list[Path] = []
            for index, kind in enumerate(sorted(kinds), start=1):
                destination = MODULE._expected_destination_name(kind)
                plaintext = f"plain:{kind}".encode()
                ciphertext = f"age-encrypted:{kind}".encode()
                plaintext_sha256 = hashlib.sha256(plaintext).hexdigest()
                ciphertext_sha256 = hashlib.sha256(ciphertext).hexdigest()
                object_key = (
                    f"{MODULE.DEFAULT_PREFIX}/{fixture.manifest.operation_id}/"
                    f"{kind}/{index:032x}-{ciphertext_sha256}.age"
                )
                evidence = {
                    "bucket": MODULE.PRODUCTION_BUCKET,
                    "object_key": object_key,
                    "version_id": f"version-{index}",
                    "plaintext_sha256": plaintext_sha256,
                    "plaintext_bytes": len(plaintext),
                    "ciphertext_sha256": ciphertext_sha256,
                    "ciphertext_bytes": len(ciphertext),
                    "metadata": {
                        "destination-name": destination,
                        "release-sha": fixture.manifest.release_sha,
                        "transport-schema": MODULE.TRANSPORT_SCHEMA,
                        "operation-id": fixture.manifest.operation_id,
                        "artifact-kind": kind,
                        "plaintext-sha256": plaintext_sha256,
                        "ciphertext-sha256": ciphertext_sha256,
                    },
                    "presigned_url_persisted": False,
                }
                state["objects"][kind] = evidence
                if kind == MODULE.BOOTSTRAP_ARTIFACT_KIND:
                    remote = {
                        "schema": MODULE.BOOTSTRAP_ATTESTATION_SCHEMA,
                        "installation_result": "created",
                        "operation_id": fixture.manifest.operation_id,
                        "object_key": object_key,
                        "version_id": f"version-{index}",
                        "plaintext_sha256": plaintext_sha256,
                        "presigned_url_persisted": False,
                    }
                else:
                    remote = {
                        "artifact_kind": kind,
                        "destination_name": destination,
                        "object_key": object_key,
                        "version_id": f"version-{index}",
                        "plaintext_sha256": plaintext_sha256,
                        "plaintext_bytes": len(plaintext),
                        "installation_result": "created",
                    }
                state["remote_attestations"][kind] = remote
                publication_path = journal / f"publish-{kind}.json"
                secure_file(publication_path, b"retained publication evidence")
                publication_journals.append(publication_path)
                secure_file(
                    MODULE._journal_ciphertext_path(publication_path),
                    ciphertext,
                )
                publications[kind] = {
                    "phase": "verified",
                    "operation_id": fixture.manifest.operation_id,
                    "artifact_kind": kind,
                    "version_id": f"version-{index}",
                    "object_key": object_key,
                    "ciphertext_bytes": len(ciphertext),
                    "ciphertext_sha256": ciphertext_sha256,
                }
            MODULE._write_orchestrator_journal(
                journal / "orchestrator.json",
                state,
                create=True,
            )

            def load_publication(path: Path) -> dict[str, object]:
                kind = path.name.removeprefix("publish-").removesuffix(".json")
                return publications[kind]

            with patch.object(
                MODULE,
                "_load_journal",
                side_effect=load_publication,
            ):
                result = MODULE.finalize_local_ciphertexts(
                    journal,
                    operation_id=fixture.manifest.operation_id,
                    release_sha=fixture.manifest.release_sha,
                    manifest_sha256=fixture.manifest.canonical_sha256,
                )
                repeated = MODULE.finalize_local_ciphertexts(
                    journal,
                    operation_id=fixture.manifest.operation_id,
                    release_sha=fixture.manifest.release_sha,
                    manifest_sha256=fixture.manifest.canonical_sha256,
                )
            self.assertEqual(set(result["removed"]), kinds)
            self.assertEqual(result["already_absent"], [])
            self.assertEqual(set(repeated["already_absent"]), kinds)
            self.assertEqual(repeated["removed"], [])
            self.assertTrue(all(path.is_file() for path in publication_journals))
            self.assertTrue((journal / "orchestrator.json").is_file())
            self.assertTrue(result["publication_journals_retained"])
            self.assertFalse(result["object_storage_objects_deleted"])
            self.assertFalse(result["remote_operation_resources_deleted"])


if __name__ == "__main__":
    unittest.main()
