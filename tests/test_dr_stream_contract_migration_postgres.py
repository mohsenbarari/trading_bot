"""Opt-in PostgreSQL regression for the immutable e653 -> f764 upgrade path."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

PARENT_REVISION = "e653f4a5b7c8"
HEAD_REVISION = "f764a5b6c8d9"
ADMIN_URL = str(os.getenv("DR_STREAM_MIGRATION_TEST_ADMIN_URL", "")).strip()
REPO_ROOT = Path(__file__).resolve().parents[1]


def _canonical_json_bytes(payload) -> bytes:  # noqa: ANN001
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_json(payload) -> str:  # noqa: ANN001
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _transaction_hash(envelope: dict[str, object]) -> str:
    member = {
        key: envelope[key]
        for key in (
            "event_id",
            "producer_sequence",
            "transaction_position",
            "aggregate_type",
            "aggregate_id",
            "aggregate_db_id",
            "aggregate_version",
            "operation",
            "canonical_payload_hash",
            "schema_version",
            "writer_epoch",
            "tombstone",
        )
    }
    return _sha256_json([member])


def _destination_transaction_hash(
    envelope: dict[str, object], *, destination: str
) -> str:
    member = dict(envelope)
    streams = envelope["destination_streams"]
    assert isinstance(streams, dict)
    stream = streams[destination]
    assert isinstance(stream, dict)
    member["transaction_position"] = int(stream["transaction_position"])
    return _transaction_hash(member)


@unittest.skipUnless(
    ADMIN_URL,
    "set DR_STREAM_MIGRATION_TEST_ADMIN_URL to an isolated PostgreSQL admin endpoint",
)
class DrStreamContractMigrationPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source = make_url(ADMIN_URL)
        cls.admin_url = source.set(database="postgres").render_as_string(
            hide_password=False
        )
        cls.admin_engine = create_engine(
            cls.admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.admin_engine.dispose()

    @contextmanager
    def _scratch_database(self):
        database_name = "stage4_registration_stream_" + uuid4().hex[:12]
        with self.admin_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
        database_url = make_url(ADMIN_URL).set(database=database_name).render_as_string(
            hide_password=False
        )
        try:
            yield database_url
        finally:
            with self.admin_engine.connect() as connection:
                connection.exec_driver_sql(
                    f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'
                )

    def _migrate(self, database_url: str, revision: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "SYNC_DATABASE_URL": database_url,
                "DATABASE_URL": database_url,
                "TRADING_BOT_MIGRATION_MODE": "scratch",
                "TRADING_BOT_EXPECTED_CHECKOUT": str(REPO_ROOT),
                "TRADING_BOT_EXPECTED_ALEMBIC_HEAD": HEAD_REVISION,
            }
        )
        return subprocess.run(
            [
                sys.executable,
                "scripts/run_guarded_scratch_alembic.py",
                "upgrade",
                revision,
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_database_already_stamped_e653_receives_forward_remediation(self) -> None:
        with self._scratch_database() as database_url:
            parent = self._migrate(database_url, PARENT_REVISION)
            self.assertEqual(parent.returncode, 0, parent.stderr or parent.stdout)
            engine = create_engine(database_url, pool_pre_ping=True)
            try:
                with engine.connect() as connection:
                    self.assertEqual(
                        connection.scalar(text("SELECT version_num FROM alembic_version")),
                        PARENT_REVISION,
                    )
                    self.assertEqual(
                        connection.scalar(
                            text(
                                "SELECT count(*) FROM pg_trigger "
                                "WHERE tgname='trg_dr_event_destination_binding'"
                            )
                        ),
                        0,
                    )
                    self.assertFalse(
                        connection.scalar(
                            text(
                                "SELECT to_regprocedure("
                                "'trading_bot_local_dr_destination_binding_valid(text)') "
                                "IS NOT NULL"
                            )
                        )
                    )
            finally:
                engine.dispose()

            upgraded = self._migrate(database_url, "head")
            self.assertEqual(upgraded.returncode, 0, upgraded.stderr or upgraded.stdout)
            engine = create_engine(database_url, pool_pre_ping=True)
            try:
                with engine.connect() as connection:
                    self.assertEqual(
                        connection.scalar(text("SELECT version_num FROM alembic_version")),
                        HEAD_REVISION,
                    )
                    self.assertEqual(
                        connection.scalar(
                            text(
                                "SELECT count(*) FROM pg_trigger "
                                "WHERE tgname='trg_dr_event_destination_binding' "
                                "AND tgenabled='A'"
                            )
                        ),
                        1,
                    )
                    self.assertTrue(
                        connection.scalar(
                            text(
                                "SELECT to_regprocedure("
                                "'trading_bot_local_dr_destination_binding_valid(text)') "
                                "IS NOT NULL"
                            )
                        )
                    )
            finally:
                engine.dispose()

    def test_noncanonical_legacy_json_scalars_fail_history_preflight(self) -> None:
        with self._scratch_database() as database_url:
            parent = self._migrate(database_url, PARENT_REVISION)
            self.assertEqual(parent.returncode, 0, parent.stderr or parent.stdout)
            event_id = str(uuid4())
            transaction_id = str(uuid4())
            created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            payload = {"id": 991, "name": "legacy-string-scalar"}
            streams = {
                destination: {
                    "sequence": 1,
                    "transaction_id": transaction_id,
                    "transaction_position": 1,
                    "transaction_size": 1,
                    "transaction_hash": "0" * 64,
                }
                for destination in ("webapp_fi", "webapp_ir")
            }
            envelope = {
                "protocol_version": 2,
                "event_id": event_id,
                "origin_authority": "foreign",
                "origin_physical_site": "bot_fi",
                "producer_epoch": 1,
                "producer_sequence": 1,
                "aggregate_type": "commodities",
                "aggregate_id": "991",
                "aggregate_db_id": "991",
                "aggregate_version": None,
                "operation": "INSERT",
                "canonical_payload": payload,
                "canonical_payload_hash": _sha256_json(payload),
                "schema_version": 1,
                "causation_id": None,
                "idempotency_key": None,
                "writer_epoch": None,
                "tombstone": False,
                "created_at": created_at,
                "transaction_id": transaction_id,
                "transaction_position": 1,
                "transaction_size": 1,
                "transaction_hash": "0" * 64,
                "destination_streams": streams,
            }
            envelope["transaction_hash"] = _transaction_hash(envelope)
            for destination in streams:
                streams[destination]["transaction_hash"] = _destination_transaction_hash(
                    envelope, destination=destination
                )
                streams[destination]["sequence"] = "1"
                streams[destination]["transaction_position"] = "1"
                streams[destination]["transaction_size"] = "1"
            event_envelope_hash = _sha256_json(envelope)

            engine = create_engine(database_url, pool_pre_ping=True)
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO dr_producer_cursors ("
                            "origin_authority,origin_physical_site,producer_epoch,last_sequence"
                            ") VALUES ('foreign','bot_fi',1,1)"
                        )
                    )
                    for destination in streams:
                        connection.execute(
                            text(
                                "INSERT INTO dr_destination_cursors ("
                                "origin_authority,origin_physical_site,producer_epoch,"
                                "destination_site,last_sequence) VALUES ("
                                "'foreign','bot_fi',1,:destination,1)"
                            ),
                            {"destination": destination},
                        )
                    connection.execute(
                        text(
                            "INSERT INTO dr_events ("
                            "event_id,protocol_version,origin_authority,origin_physical_site,"
                            "producer_epoch,producer_sequence,aggregate_type,aggregate_id,"
                            "aggregate_db_id,operation,canonical_payload,canonical_payload_hash,"
                            "envelope_hash,schema_version,tombstone,created_at,transaction_id,"
                            "transaction_position,transaction_size,transaction_hash,"
                            "destination_streams,source_xid) VALUES ("
                            ":event_id,2,'foreign','bot_fi',1,1,'commodities','991','991',"
                            "'INSERT',CAST(:payload AS JSONB),:payload_hash,:envelope_hash,1,false,"
                            "CAST(:created_at AS TIMESTAMPTZ),:transaction_id,1,1,"
                            ":transaction_hash,CAST(:streams AS JSONB),42)"
                        ),
                        {
                            "event_id": event_id,
                            "payload": _canonical_json_bytes(payload).decode(),
                            "payload_hash": envelope["canonical_payload_hash"],
                            "envelope_hash": event_envelope_hash,
                            "created_at": created_at,
                            "transaction_id": transaction_id,
                            "transaction_hash": envelope["transaction_hash"],
                            "streams": _canonical_json_bytes(streams).decode(),
                        },
                    )
                    for destination in streams:
                        connection.execute(
                            text(
                                "INSERT INTO dr_event_destination_sequences ("
                                "event_id,destination_site,origin_authority,"
                                "origin_physical_site,producer_epoch,destination_sequence) "
                                "VALUES (:event_id,:destination,'foreign','bot_fi',1,1)"
                            ),
                            {"event_id": event_id, "destination": destination},
                        )
                with engine.connect() as connection:
                    self.assertTrue(
                        connection.scalar(
                            text(
                                "SELECT trading_bot_dr_event_payload_integrity_valid(:event_id)"
                            ),
                            {"event_id": event_id},
                        )
                    )
            finally:
                engine.dispose()

            rejected = self._migrate(database_url, "head")
            self.assertNotEqual(rejected.returncode, 0)
            evidence = rejected.stderr + rejected.stdout
            self.assertIn("DR history preflight rejected local event", evidence)
            self.assertIn(event_id, evidence)

            engine = create_engine(database_url, pool_pre_ping=True)
            try:
                with engine.connect() as connection:
                    self.assertEqual(
                        connection.scalar(text("SELECT version_num FROM alembic_version")),
                        PARENT_REVISION,
                    )
                    self.assertFalse(
                        connection.scalar(
                            text(
                                "SELECT to_regprocedure("
                                "'trading_bot_dr_json_positive_integer(jsonb,bigint)') "
                                "IS NOT NULL"
                            )
                        )
                    )
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
