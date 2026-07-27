from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import produce_wa_ir_source_database_attestation as PRODUCER
from scripts import wa_ir_production_operation as CONSUMER
from scripts.wa_ir_production_object_storage_transport import PublishedObject


OPERATION_ID = "12345678-1234-4234-8234-123456789abc"
RELEASE_SHA = "a" * 40
TABLE_STREAM = b'{"id":1}\n{"id":2}\n'
SEQUENCE_STREAM = b'{"sequence_name":"users_id_seq","last_value":2}\n'


class SourceDatabaseAttestationTests(unittest.TestCase):
    @staticmethod
    def _scratch_documents():
        name = f"tb-wa-src-attest-{OPERATION_ID.replace('-', '')}"
        volume = f"{name}-pgdata"
        image = "sha256:" + "c" * 64
        labels = {
            PRODUCER._LABEL_OPERATION: OPERATION_ID,
            PRODUCER._LABEL_PURPOSE: PRODUCER._PURPOSE,
        }
        container = {
            "Id": "d" * 64,
            "Name": f"/{name}",
            "Image": image,
            "Config": {"Image": image, "Labels": labels},
            "HostConfig": {
                "NetworkMode": "none",
                "PortBindings": {},
                "Privileged": False,
                "RestartPolicy": {"Name": "no"},
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
            "Labels": labels,
            "Options": {},
        }
        return name, volume, image, container, volume_document

    def test_source_producer_and_restore_consumer_share_exact_contract(self) -> None:
        observed_sql: list[str] = []

        def query(sql: str) -> str:
            if "alembic_version" in sql:
                return "f2c7d8e9a0b1"
            if "pg_tables" in sql:
                return "users"
            raise AssertionError(sql)

        def stream(sql: str) -> CONSUMER.StreamDigest:
            observed_sql.append(sql)
            payload = SEQUENCE_STREAM if "pg_sequences" in sql else TABLE_STREAM
            return CONSUMER.StreamDigest(
                hashlib.sha256(payload).hexdigest(),
                len(payload),
                payload.count(b"\n"),
            )

        attestation = PRODUCER.build_attestation(
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            database_backup_sha256="b" * 64,
            database_backup_bytes=1234,
            database_backup_object_key=(
                "dark-standby/source-backup/database.dump"
            ),
            database_backup_version_id="version-1",
            postgres_image_id="sha256:" + "c" * 64,
            scratch_postgres_system_id="9000000000000000001",
            query=query,
            stream_copy=stream,
        )
        consumer_fingerprint = CONSUMER._fingerprint_from_streams(
            ["users"],
            stream,
        )
        source = attestation["source_database"]
        self.assertEqual(
            (
                source["database_fingerprint_sha256"],
                source["row_count"],
                source["table_count"],
            ),
            consumer_fingerprint,
        )
        self.assertEqual(
            source["fingerprint_algorithm"],
            CONSUMER.DATABASE_FINGERPRINT_ALGORITHM,
        )
        self.assertTrue(all("COPY (" in sql for sql in observed_sql))
        self.assertFalse(any("string_agg" in sql for sql in observed_sql))

    def test_source_psql_invocation_pins_canonical_session(self) -> None:
        arguments = PRODUCER._scratch_psql_arguments(
            "scratch",
            sql="COPY (SELECT 1) TO STDOUT",
            streaming=True,
        )
        joined = "\n".join(arguments)
        self.assertIn(
            f"PGOPTIONS={CONSUMER.DATABASE_FINGERPRINT_PGOPTIONS}",
            joined,
        )
        self.assertIn(
            "PGCLIENTENCODING=UTF8",
            joined,
        )
        self.assertIn("--no-psqlrc", arguments)
        self.assertIn("default_transaction_read_only=on", joined)

    def test_consumer_compose_stream_pins_same_canonical_session(self) -> None:
        manifest = unittest.mock.Mock()
        manifest.operation_id = OPERATION_ID
        manifest.services = {"restore": "webapp_ir_restore_tool"}
        prefix = [
            CONSUMER.DOCKER,
            "compose",
            "--project-name",
            "fixture",
            "--env-file",
            (
                "/srv/trading-bot/dark-standby/operations/"
                f"{OPERATION_ID}/secrets/webapp-ir/runtime.env.role"
            ),
            "--file",
            (
                "/srv/trading-bot/dark-standby/operations/"
                f"{OPERATION_ID}/rendered/webapp-ir/docker-compose.yml"
            ),
        ]
        with (
            patch.object(CONSUMER, "_oneoff_ids", return_value=[]),
            patch.object(
                CONSUMER,
                "_run_streaming_sha256",
                return_value=CONSUMER.StreamDigest("0" * 64, 0, 0),
            ) as run,
            patch.object(CONSUMER, "_cleanup_operation_oneoffs"),
        ):
            CONSUMER._compose_streaming_copy_sha256(
                prefix,
                manifest,
                sql="COPY (SELECT 1) TO STDOUT",
                timeout=30,
            )
        arguments = run.call_args.args[0]
        joined = "\n".join(arguments)
        self.assertIn(
            f"PGOPTIONS={CONSUMER.DATABASE_FINGERPRINT_PGOPTIONS}",
            joined,
        )
        self.assertIn("PGCLIENTENCODING=UTF8", joined)

    def test_scratch_identity_rejects_network_or_foreign_mount(self) -> None:
        operation_id = OPERATION_ID
        name = "scratch"
        volume = "scratch-pgdata"
        image = "sha256:" + "c" * 64
        document = {
            "Id": "d" * 64,
            "Name": f"/{name}",
            "Image": image,
            "Config": {
                "Image": image,
                "Labels": {
                    PRODUCER._LABEL_OPERATION: operation_id,
                    PRODUCER._LABEL_PURPOSE: PRODUCER._PURPOSE,
                },
            },
            "HostConfig": {
                "NetworkMode": "none",
                "PortBindings": {},
                "Privileged": False,
                "RestartPolicy": {"Name": "no"},
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
        self.assertEqual(
            PRODUCER._validate_scratch_container(
                document,
                operation_id=operation_id,
                name=name,
                volume=volume,
                postgres_image_id=image,
            ),
            "d" * 64,
        )
        document["HostConfig"]["RestartPolicy"] = {"Name": "unless-stopped"}
        with self.assertRaises(PRODUCER.SourceDatabaseAttestationError):
            PRODUCER._validate_scratch_container(
                document,
                operation_id=operation_id,
                name=name,
                volume=volume,
                postgres_image_id=image,
            )
        document["HostConfig"]["RestartPolicy"] = {"Name": "no"}
        document["HostConfig"]["NetworkMode"] = "bridge"
        with self.assertRaises(PRODUCER.SourceDatabaseAttestationError):
            PRODUCER._validate_scratch_container(
                document,
                operation_id=operation_id,
                name=name,
                volume=volume,
                postgres_image_id=image,
            )
        document["HostConfig"]["NetworkMode"] = "none"
        document["Mounts"][0] = {
            "Type": "bind",
            "Source": "/srv/trading-bot/current",
            "Destination": "/foreign",
            "RW": False,
        }
        with self.assertRaises(PRODUCER.SourceDatabaseAttestationError):
            PRODUCER._validate_scratch_container(
                document,
                operation_id=operation_id,
                name=name,
                volume=volume,
                postgres_image_id=image,
            )

    def test_held_backup_detects_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            backup = root / "database.dump"
            backup.write_bytes(b"PGDMP source")
            backup.chmod(0o600)
            descriptor, identity = PRODUCER._open_stable_backup(backup)
            try:
                replacement = root / "replacement"
                replacement.write_bytes(b"PGDMP replacement")
                replacement.chmod(0o600)
                os.replace(replacement, backup)
                with self.assertRaises(
                    PRODUCER.SourceDatabaseAttestationError
                ):
                    PRODUCER._verify_held_backup(
                        descriptor,
                        backup,
                        identity,
                    )
            finally:
                os.close(descriptor)

    def test_held_backup_is_rehashed_after_restore(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            backup = Path(raw) / "database.dump"
            backup.write_bytes(b"PGDMP stable source")
            backup.chmod(0o600)
            descriptor, identity = PRODUCER._open_stable_backup(backup)
            try:
                PRODUCER._rehash_held_backup(
                    descriptor,
                    backup,
                    identity,
                )
                with backup.open("r+b") as changed:
                    changed.write(b"X")
                    changed.flush()
                    os.fsync(changed.fileno())
                with self.assertRaises(
                    PRODUCER.SourceDatabaseAttestationError
                ):
                    PRODUCER._rehash_held_backup(
                        descriptor,
                        backup,
                        identity,
                    )
            finally:
                os.close(descriptor)

    def test_inspect_only_exact_not_found_means_absent(self) -> None:
        missing = subprocess.CompletedProcess(
            [],
            1,
            stdout=b"",
            stderr=b"Error: No such container: scratch\n",
        )
        with patch.object(PRODUCER.subprocess, "run", return_value=missing):
            self.assertIsNone(
                PRODUCER._inspect_optional("container", "scratch")
            )
        ambiguous = subprocess.CompletedProcess(
            [],
            1,
            stdout=b"",
            stderr=b"permission denied\n",
        )
        with (
            patch.object(PRODUCER.subprocess, "run", return_value=ambiguous),
            self.assertRaises(PRODUCER.SourceDatabaseAttestationError),
        ):
            PRODUCER._inspect_optional("container", "scratch")

    def test_source_object_identity_requires_verified_publication_journal(self) -> None:
        identity = PRODUCER.BackupIdentity(
            "b" * 64,
            1234,
            (1, 2, 3, 0, 1, 1234, 4),
        )
        state = {
            "phase": "verified",
            "operation_id": OPERATION_ID,
            "artifact_kind": PRODUCER._SOURCE_BACKUP_ARTIFACT_KIND,
            "requested_metadata": {
                "destination-name": "database.dump",
                "release-sha": RELEASE_SHA,
            },
        }
        published = PublishedObject(
            bucket=PRODUCER.PRODUCTION_BUCKET,
            object_key=(
                f"dark-standby/source-backup/{OPERATION_ID}/"
                f"database-backup/{'a' * 32}-{'c' * 64}.age"
            ),
            version_id="version-1",
            plaintext_sha256=identity.sha256,
            plaintext_bytes=identity.bytes,
            ciphertext_sha256="c" * 64,
            ciphertext_bytes=1400,
            metadata={},
        )
        with (
            patch.object(PRODUCER, "_load_journal", return_value=state),
            patch.object(
                PRODUCER,
                "_validate_prepared_journal",
                return_value=published,
            ),
        ):
            self.assertEqual(
                PRODUCER._verified_publication(
                    Path("/secure/journal"),
                    operation_id=OPERATION_ID,
                    release_sha=RELEASE_SHA,
                    backup_identity=identity,
                ),
                (published.object_key, published.version_id),
            )
            state["phase"] = "uploaded"
            with self.assertRaises(
                PRODUCER.SourceDatabaseAttestationError
            ):
                PRODUCER._verified_publication(
                    Path("/secure/journal"),
                    operation_id=OPERATION_ID,
                    release_sha=RELEASE_SHA,
                    backup_identity=identity,
                )

    def test_crash_residue_is_validated_then_removed_exactly(self) -> None:
        name, volume, image, container, volume_document = (
            self._scratch_documents()
        )
        with (
            patch.object(
                PRODUCER,
                "_inspect_optional",
                side_effect=[
                    container,
                    volume_document,
                    None,
                    None,
                ],
            ),
            patch.object(PRODUCER, "_run", return_value="") as run,
        ):
            self.assertTrue(
                PRODUCER._cleanup_exact_scratch(
                    operation_id=OPERATION_ID,
                    container=name,
                    volume=volume,
                    postgres_image_id=image,
                )
            )
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                [PRODUCER.DOCKER, "container", "rm", "--force", name],
                [PRODUCER.DOCKER, "volume", "rm", volume],
            ],
        )

        foreign = dict(volume_document)
        foreign["Labels"] = {"foreign": "true"}
        with (
            patch.object(
                PRODUCER,
                "_inspect_optional",
                side_effect=[None, foreign],
            ),
            patch.object(PRODUCER, "_run") as run,
            self.assertRaises(PRODUCER.SourceDatabaseAttestationError),
        ):
            PRODUCER._cleanup_exact_scratch(
                operation_id=OPERATION_ID,
                container=name,
                volume=volume,
                postgres_image_id=image,
            )
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
