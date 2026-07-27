from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from scripts import orchestrate_wa_ir_production_artifacts as MODULE
from scripts import produce_wa_ir_source_database_attestation as SOURCE
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
            f"prepare-wa-ir:{fixture.manifest.operation_id}:"
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
        "image_id": next(
            image.image_id
            for image in fixture.manifest.images
            if image.role == "postgres"
        ),
        "project": fixture.manifest.project_name,
        "service": fixture.manifest.services["database"],
        "volume_name": (
            f"{fixture.manifest.project_name}_webapp_ir_postgres"
        ),
        "data_path": str(data_root / "webapp-ir" / "postgres"),
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
            "images": [
                {
                    "role": image.role,
                    "image_id": image.image_id,
                    "source": "object-storage-archive",
                }
                for image in fixture.manifest.images
            ],
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

            def runner_for(document):  # noqa: ANN001
                def runner(arguments, **_kwargs):  # noqa: ANN001
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

    def test_transfer_publishes_exact_nine_artifacts_and_resumes(self) -> None:
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
                self.assertEqual(result["object_count"], 9)
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
