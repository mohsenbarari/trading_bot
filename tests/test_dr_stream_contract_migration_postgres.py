"""Opt-in PostgreSQL regressions for the immutable e653 -> current DR upgrade path."""

from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

PARENT_REVISION = "e653f4a5b7c8"
LOCKED_PARENT_REVISION = "a875b6c7d9e0"
HEAD_REVISION = "b986c7d8e0f1"
ADMIN_URL = str(os.getenv("DR_STREAM_MIGRATION_TEST_ADMIN_URL", "")).strip()
SCRATCH_CLUSTER_SYSTEM_ID = str(
    os.getenv("TRADING_BOT_EXPECTED_SCRATCH_CLUSTER_SYSTEM_ID", "")
).strip()
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
    ADMIN_URL and SCRATCH_CLUSTER_SYSTEM_ID,
    "set the isolated PostgreSQL admin URL and expected scratch-cluster system identifier",
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
        with cls.admin_engine.connect() as connection:
            observed_system_id = str(
                connection.execute(
                    text("SELECT system_identifier::text FROM pg_control_system()")
                ).scalar_one()
            )
        if observed_system_id != SCRATCH_CLUSTER_SYSTEM_ID:
            cls.admin_engine.dispose()
            raise RuntimeError(
                "DR stream migration test admin URL is not the expected scratch cluster"
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

    @staticmethod
    def _insert_legacy_event(
        connection,
        *,
        event_id: str,
        protocol_version: int,
        producer_sequence: int,
        destination_sequence: int | None = None,
    ) -> None:
        payload = {"id": producer_sequence, "name": f"legacy-{producer_sequence}"}
        transaction_id = str(uuid4()) if protocol_version == 2 else None
        streams = (
            {
                destination: {
                    "sequence": destination_sequence,
                    "transaction_id": transaction_id,
                    "transaction_position": 1,
                    "transaction_size": 1,
                    "transaction_hash": "e" * 64,
                }
                for destination in ("webapp_fi", "webapp_ir")
            }
            if protocol_version == 2
            else None
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
                ":event_id,:protocol_version,'foreign','bot_fi',77,:producer_sequence,"
                "'commodities',:aggregate_id,:aggregate_id,'INSERT',"
                "CAST(:payload AS JSONB),:payload_hash,:envelope_hash,1,false,"
                "clock_timestamp(),:transaction_id,:transaction_position,"
                ":transaction_size,:transaction_hash,CAST(:streams AS JSONB),NULL)"
            ),
            {
                "event_id": event_id,
                "protocol_version": protocol_version,
                "producer_sequence": producer_sequence,
                "aggregate_id": str(producer_sequence),
                "payload": _canonical_json_bytes(payload).decode(),
                "payload_hash": _sha256_json(payload),
                "envelope_hash": "f" * 64,
                "transaction_id": transaction_id,
                "transaction_position": 1 if protocol_version == 2 else None,
                "transaction_size": 1 if protocol_version == 2 else None,
                "transaction_hash": "d" * 64 if protocol_version == 2 else None,
                "streams": (
                    _canonical_json_bytes(streams).decode()
                    if streams is not None
                    else None
                ),
            },
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
                    for value, maximum, expected in (
                        (1, 9223372036854775807, True),
                        (9223372036854775807, 9223372036854775807, True),
                        (9223372036854775808, 9223372036854775807, False),
                        (2147483647, 2147483647, True),
                        (2147483648, 2147483647, False),
                        (0, 2147483647, False),
                        (-1, 2147483647, False),
                        ("1", 2147483647, False),
                        (1.5, 2147483647, False),
                        (True, 2147483647, False),
                        (None, 2147483647, False),
                    ):
                        with self.subTest(value=value, maximum=maximum):
                            observed = connection.scalar(
                                text(
                                    "SELECT trading_bot_dr_json_positive_integer("
                                    "CAST(:value AS JSONB),:maximum)"
                                ),
                                {
                                    "value": json.dumps(value),
                                    "maximum": maximum,
                                },
                            )
                            self.assertIs(observed, expected)
            finally:
                engine.dispose()

    def test_cursor_only_producer_history_is_rejected_transactionally(self) -> None:
        with self._scratch_database() as database_url:
            parent = self._migrate(database_url, LOCKED_PARENT_REVISION)
            self.assertEqual(parent.returncode, 0, parent.stderr or parent.stdout)
            engine = create_engine(database_url, pool_pre_ping=True)
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO dr_producer_cursors ("
                            "origin_authority,origin_physical_site,producer_epoch,last_sequence"
                            ") VALUES ('foreign','bot_fi',77,5)"
                        )
                    )
            finally:
                engine.dispose()

            rejected = self._migrate(database_url, "head")
            self.assertNotEqual(rejected.returncode, 0)
            evidence = rejected.stderr + rejected.stdout
            self.assertIn("incomplete foreign/bot_fi/77 producer stream", evidence)
            self.assertIn("exactly contiguous from 1 through the cursor tail", evidence)

            engine = create_engine(database_url, pool_pre_ping=True)
            try:
                with engine.connect() as connection:
                    self.assertEqual(
                        connection.scalar(text("SELECT version_num FROM alembic_version")),
                        LOCKED_PARENT_REVISION,
                    )
                    function_definition = str(
                        connection.scalar(
                            text(
                                "SELECT pg_get_functiondef("
                                "'trading_bot_dr_event_immutable()'::regprocedure)"
                            )
                        )
                    )
                    self.assertNotIn(
                        "NEW.source_xid IS NOT DISTINCT FROM OLD.source_xid",
                        function_definition,
                    )
            finally:
                engine.dispose()

    def test_cursor_only_destination_history_is_rejected(self) -> None:
        with self._scratch_database() as database_url:
            parent = self._migrate(database_url, LOCKED_PARENT_REVISION)
            self.assertEqual(parent.returncode, 0, parent.stderr or parent.stdout)
            engine = create_engine(database_url, pool_pre_ping=True)
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO dr_producer_cursors ("
                            "origin_authority,origin_physical_site,producer_epoch,last_sequence"
                            ") VALUES ('foreign','bot_fi',77,1)"
                        )
                    )
                    self._insert_legacy_event(
                        connection,
                        event_id=str(uuid4()),
                        protocol_version=1,
                        producer_sequence=1,
                    )
                    connection.execute(
                        text(
                            "INSERT INTO dr_destination_cursors ("
                            "origin_authority,origin_physical_site,producer_epoch,"
                            "destination_site,last_sequence) VALUES ("
                            "'foreign','bot_fi',77,'webapp_fi',1)"
                        )
                    )
            finally:
                engine.dispose()

            rejected = self._migrate(database_url, "head")
            self.assertNotEqual(rejected.returncode, 0)
            evidence = rejected.stderr + rejected.stdout
            self.assertIn(
                "incomplete foreign/bot_fi/77/webapp_fi destination stream",
                evidence,
            )

    def test_missing_first_internal_gap_and_cursor_tail_drift_are_rejected(self) -> None:
        scenarios = (
            ("missing_first", 2, (2,)),
            ("internal_gap", 3, (1, 3)),
            ("cursor_tail_ahead", 3, (1, 2)),
            ("cursor_tail_behind", 1, (1, 2)),
        )
        with self._scratch_database() as database_url:
            parent = self._migrate(database_url, LOCKED_PARENT_REVISION)
            self.assertEqual(parent.returncode, 0, parent.stderr or parent.stdout)
            engine = create_engine(database_url, pool_pre_ping=True)
            try:
                for label, cursor_tail, sequences in scenarios:
                    with self.subTest(label=label):
                        with engine.begin() as connection:
                            connection.execute(
                                text(
                                    "INSERT INTO dr_producer_cursors ("
                                    "origin_authority,origin_physical_site,producer_epoch,"
                                    "last_sequence) VALUES ('foreign','bot_fi',77,:tail)"
                                ),
                                {"tail": cursor_tail},
                            )
                            for sequence in sequences:
                                self._insert_legacy_event(
                                    connection,
                                    event_id=str(uuid4()),
                                    protocol_version=1,
                                    producer_sequence=sequence,
                                )
                        rejected = self._migrate(database_url, "head")
                        self.assertNotEqual(rejected.returncode, 0)
                        self.assertIn(
                            "incomplete foreign/bot_fi/77 producer stream",
                            rejected.stderr + rejected.stdout,
                        )
                        with engine.begin() as connection:
                            self.assertEqual(
                                connection.scalar(
                                    text("SELECT version_num FROM alembic_version")
                                ),
                                LOCKED_PARENT_REVISION,
                            )
                            connection.execute(text("TRUNCATE TABLE dr_events CASCADE"))
                            connection.execute(
                                text(
                                    "DELETE FROM dr_producer_cursors "
                                    "WHERE origin_physical_site='bot_fi' AND producer_epoch=77"
                                )
                            )
            finally:
                engine.dispose()

    def test_supported_v1_and_pre_source_xid_v2_prefix_upgrades(self) -> None:
        with self._scratch_database() as database_url:
            parent = self._migrate(database_url, LOCKED_PARENT_REVISION)
            self.assertEqual(parent.returncode, 0, parent.stderr or parent.stdout)
            engine = create_engine(database_url, pool_pre_ping=True)
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO dr_producer_cursors ("
                            "origin_authority,origin_physical_site,producer_epoch,last_sequence"
                            ") VALUES ('foreign','bot_fi',77,2)"
                        )
                    )
                    for destination in ("webapp_fi", "webapp_ir"):
                        connection.execute(
                            text(
                                "INSERT INTO dr_destination_cursors ("
                                "origin_authority,origin_physical_site,producer_epoch,"
                                "destination_site,last_sequence) VALUES ("
                                "'foreign','bot_fi',77,:destination,1)"
                            ),
                            {"destination": destination},
                        )
                    self._insert_legacy_event(
                        connection,
                        event_id=str(uuid4()),
                        protocol_version=1,
                        producer_sequence=1,
                    )
                    self._insert_legacy_event(
                        connection,
                        event_id=str(uuid4()),
                        protocol_version=2,
                        producer_sequence=2,
                        destination_sequence=1,
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
            finally:
                engine.dispose()

    def test_migration_lock_includes_a_writer_commit_before_preflight(self) -> None:
        with self._scratch_database() as database_url:
            parent = self._migrate(database_url, LOCKED_PARENT_REVISION)
            self.assertEqual(parent.returncode, 0, parent.stderr or parent.stdout)
            writer_engine = create_engine(database_url, pool_pre_ping=True)
            observer_engine = create_engine(database_url, pool_pre_ping=True)
            writer = writer_engine.connect()
            transaction = writer.begin()
            try:
                writer.execute(
                    text(
                        "INSERT INTO dr_producer_cursors ("
                        "origin_authority,origin_physical_site,producer_epoch,last_sequence"
                        ") VALUES ('foreign','bot_fi',88,1)"
                    )
                )
                with ThreadPoolExecutor(max_workers=1) as executor:
                    migration = executor.submit(self._migrate, database_url, "head")
                    observed_wait = False
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        with observer_engine.connect() as observer:
                            observed_wait = bool(
                                observer.scalar(
                                    text(
                                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                        "WHERE datname=current_database() "
                                        "AND query ILIKE '%LOCK TABLE%dr_events%' "
                                        "AND wait_event_type='Lock')"
                                    )
                                )
                            )
                        if observed_wait:
                            break
                        time.sleep(0.1)
                    self.assertTrue(observed_wait, "migration never waited at the write-excluding lock")
                    transaction.commit()
                    rejected = migration.result(timeout=30)
                self.assertNotEqual(rejected.returncode, 0)
                evidence = rejected.stderr + rejected.stdout
                self.assertIn("incomplete foreign/bot_fi/88 producer stream", evidence)
            finally:
                if transaction.is_active:
                    transaction.rollback()
                writer.close()
                writer_engine.dispose()
                observer_engine.dispose()

    def test_placeholder_finalization_cannot_change_source_xid(self) -> None:
        with self._scratch_database() as database_url:
            upgraded = self._migrate(database_url, "head")
            self.assertEqual(upgraded.returncode, 0, upgraded.stderr or upgraded.stdout)
            event_id = str(uuid4())
            transaction_id = str(uuid4())
            payload = {"id": 1, "name": "placeholder"}
            placeholder_streams = {
                destination: {
                    "sequence": 1,
                    "transaction_id": transaction_id,
                    "transaction_position": 0,
                    "transaction_size": 0,
                    "transaction_hash": "0" * 64,
                }
                for destination in ("webapp_fi", "webapp_ir")
            }
            engine = create_engine(database_url, pool_pre_ping=True)
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO dr_events ("
                            "event_id,protocol_version,origin_authority,origin_physical_site,"
                            "producer_epoch,producer_sequence,aggregate_type,aggregate_id,"
                            "aggregate_db_id,operation,canonical_payload,canonical_payload_hash,"
                            "envelope_hash,schema_version,tombstone,created_at,transaction_id,"
                            "transaction_position,transaction_size,transaction_hash,"
                            "destination_streams,source_xid) VALUES ("
                            ":event_id,2,'foreign','bot_fi',91,1,'commodities','1','1',"
                            "'INSERT',CAST(:payload AS JSONB),:payload_hash,:zero_hash,1,false,"
                            "clock_timestamp(),:transaction_id,1,0,:zero_hash,"
                            "CAST(:streams AS JSONB),NULL)"
                        ),
                        {
                            "event_id": event_id,
                            "payload": _canonical_json_bytes(payload).decode(),
                            "payload_hash": _sha256_json(payload),
                            "zero_hash": "0" * 64,
                            "transaction_id": transaction_id,
                            "streams": _canonical_json_bytes(placeholder_streams).decode(),
                        },
                    )
                with engine.begin() as connection:
                    with self.assertRaisesRegex(Exception, "dr_events are immutable"):
                        connection.execute(
                            text(
                                "UPDATE dr_events SET transaction_size=1,"
                                "transaction_hash=:hash,envelope_hash=:hash,source_xid=42,"
                                "destination_streams=jsonb_set(jsonb_set(jsonb_set(jsonb_set("
                                "destination_streams::jsonb, '{webapp_fi,transaction_position}', '1'),"
                                "'{webapp_fi,transaction_size}', '1'),"
                                "'{webapp_ir,transaction_position}', '1'),"
                                "'{webapp_ir,transaction_size}', '1') "
                                "WHERE event_id=:event_id"
                            ),
                            {"event_id": event_id, "hash": "a" * 64},
                        )
            finally:
                engine.dispose()

    def test_destination_binding_identity_mismatch_is_rejected(self) -> None:
        with self._scratch_database() as database_url:
            parent = self._migrate(database_url, LOCKED_PARENT_REVISION)
            self.assertEqual(parent.returncode, 0, parent.stderr or parent.stdout)
            event_id = str(uuid4())
            transaction_id = str(uuid4())
            created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            payload = {"id": 992, "name": "binding-mismatch"}
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
                "aggregate_id": "992",
                "aggregate_db_id": "992",
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
                            ":event_id,2,'foreign','bot_fi',1,1,'commodities','992','992',"
                            "'INSERT',CAST(:payload AS JSONB),:payload_hash,:envelope_hash,1,false,"
                            "CAST(:created_at AS TIMESTAMPTZ),:transaction_id,1,1,"
                            ":transaction_hash,CAST(:streams AS JSONB),NULL)"
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
                                "VALUES (:event_id,:destination,:authority,'bot_fi',1,1)"
                            ),
                            {
                                "event_id": event_id,
                                "destination": destination,
                                "authority": (
                                    "webapp" if destination == "webapp_fi" else "foreign"
                                ),
                            },
                        )
                with engine.begin() as connection:
                    for trigger in (
                        "trg_dr_events_immutable",
                        "trg_dr_event_destination_binding",
                        "trg_dr_event_mutation_binding",
                    ):
                        connection.execute(
                            text(f"ALTER TABLE dr_events DISABLE TRIGGER {trigger}")
                        )
                with engine.begin() as connection:
                    connection.execute(
                        text("UPDATE dr_events SET source_xid=42 WHERE event_id=:event_id"),
                        {"event_id": event_id},
                    )
                with engine.begin() as connection:
                    for trigger in (
                        "trg_dr_events_immutable",
                        "trg_dr_event_destination_binding",
                        "trg_dr_event_mutation_binding",
                    ):
                        connection.execute(
                            text(f"ALTER TABLE dr_events ENABLE ALWAYS TRIGGER {trigger}")
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
                    self.assertTrue(
                        connection.scalar(
                            text(
                                "SELECT trading_bot_dr_event_entitlement_valid(:event_id)"
                            ),
                            {"event_id": event_id},
                        )
                    )
                    self.assertTrue(
                        connection.scalar(
                            text(
                                "SELECT trading_bot_dr_destination_schema_valid(:event_id)"
                            ),
                            {"event_id": event_id},
                        )
                    )
                    self.assertFalse(
                        connection.scalar(
                            text(
                                "SELECT trading_bot_local_dr_destination_binding_valid(:event_id)"
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
