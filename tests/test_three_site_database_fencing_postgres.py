"""Opt-in real-PostgreSQL proof for strict three-site database roles.

The test is skipped unless four scratch-only URLs are supplied. It must never
be pointed at a runtime database.
"""

from __future__ import annotations

import os
import json
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError


URL_NAMES = {
    "owner": "THREE_SITE_FENCING_TEST_OWNER_URL",
    "application": "THREE_SITE_FENCING_TEST_APP_URL",
    "projection": "THREE_SITE_FENCING_TEST_PROJECTION_URL",
    "receiver": "THREE_SITE_FENCING_TEST_RECEIVER_URL",
    "delivery": "THREE_SITE_FENCING_TEST_DELIVERY_URL",
    "blob": "THREE_SITE_FENCING_TEST_BLOB_URL",
    "effect": "THREE_SITE_FENCING_TEST_EFFECT_URL",
    "control": "THREE_SITE_FENCING_TEST_CONTROL_URL",
}


@unittest.skipUnless(
    all(os.environ.get(name) for name in URL_NAMES.values()),
    "scratch three-site fencing URLs are not configured",
)
class ThreeSiteDatabaseFencingPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engines = {
            role: create_engine(os.environ[name], pool_pre_ping=True)
            for role, name in URL_NAMES.items()
        }
        with cls.engines["owner"].begin() as connection:
            cls.owner_role = str(connection.scalar(text("SELECT current_user")))
            database_name = str(connection.scalar(text("SELECT current_database()")))
            if not database_name.startswith("stage4_registration_"):
                raise RuntimeError("database fencing integration test requires a guarded scratch database")
            boot_id, boottime = connection.execute(
                text("SELECT trading_bot_boot_id(), trading_bot_boottime_seconds()")
            ).one()
            connection.execute(
                text(
                    "UPDATE webapp_writer_state SET "
                    "witness_lease_id = 'scratch-lease', "
                    "witness_lease_issued_at = clock_timestamp(), "
                    "witness_lease_expires_at = clock_timestamp() + interval '10 minutes', "
                    "witness_proof_hash = repeat('a', 64), "
                    "witness_transition_id = 'scratch-transition', "
                    "witness_local_boot_id = :boot_id, "
                    "witness_local_boottime_deadline = :deadline, "
                    "witness_observed_wall_at = clock_timestamp(), "
                    "witness_observed_boottime = 1, witness_clock_offset_ms = 0 "
                    "WHERE authority = 'webapp'"
                ),
                {"boot_id": str(boot_id), "deadline": float(boottime) + 600.0},
            )

    @classmethod
    def tearDownClass(cls) -> None:
        for engine in cls.engines.values():
            engine.dispose()

    def _writer_settings(self, connection) -> None:  # noqa: ANN001
        state = connection.execute(
            text(
                "SELECT writer_epoch, transition_id, witness_lease_id "
                "FROM webapp_writer_state WHERE authority = 'webapp'"
            )
        ).mappings().one()
        settings = {
            "trading_bot.mutation_capability": "writer",
            "trading_bot.physical_site": "webapp_fi",
            "trading_bot.writer_epoch": str(state["writer_epoch"]),
            "trading_bot.transition_id": str(state["transition_id"]),
            "trading_bot.witness_lease_id": str(state["witness_lease_id"]),
        }
        for key, value in settings.items():
            connection.execute(text("SELECT set_config(:key, :value, true)"), {"key": key, "value": value})

    def _scratch_id(self) -> int:
        return 900_000_000 + uuid4().int % 90_000_000

    def _projection_scope(self, connection, scope: str) -> None:  # noqa: ANN001
        connection.execute(
            text("SELECT set_config('trading_bot.mutation_capability', 'projection', true)")
        )
        connection.execute(
            text("SELECT set_config('trading_bot.projection_scope', :scope, true)"),
            {"scope": scope},
        )

    def _seed_projection_commodity(self, record_id: int) -> None:
        with self.engines["projection"].begin() as connection:
            self._projection_scope(connection, "projector")
            connection.execute(
                text("INSERT INTO commodities (id, name) VALUES (:id, :name)"),
                {"id": record_id, "name": f"projection-seed-{record_id}"},
            )

    def _record_coverage_event(
        self,
        connection,
        *,
        table: str,
        record_id: int,
        operation: str,
        payload: dict[str, object],
    ) -> None:  # noqa: ANN001
        event_id = str(uuid4())
        sequence = uuid4().int % 9_000_000_000 + 1
        connection.execute(
            text(
                "INSERT INTO dr_events ("
                "event_id, protocol_version, origin_authority, origin_physical_site, "
                "producer_epoch, producer_sequence, aggregate_type, aggregate_id, "
                "aggregate_db_id, operation, canonical_payload, canonical_payload_hash, "
                "envelope_hash, schema_version, writer_epoch, tombstone, source_xid"
                ") VALUES ("
                ":event_id, 1, 'webapp', 'webapp_fi', 1, :sequence, :table, :record_id, "
                ":record_id, :operation, CAST(:payload AS JSONB), repeat('a', 64), repeat('b', 64), "
                "1, 1, :tombstone, txid_current())"
            ),
            {
                "event_id": event_id,
                "sequence": sequence,
                "table": table,
                "record_id": str(record_id),
                "operation": operation,
                "tombstone": operation == "DELETE",
                "payload": json.dumps(payload, sort_keys=True, separators=(",", ":")),
            },
        )

    def test_application_role_fails_closed_without_writer_capability(self) -> None:
        record_id = self._scratch_id()
        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                connection.execute(
                    text("INSERT INTO commodities (id, name) VALUES (:id, :name)"),
                    {"id": record_id, "name": f"unscoped-{record_id}"},
                )

    def test_runtime_roles_cannot_set_role_or_disable_writer_trigger(self) -> None:
        with self.engines["application"].connect() as connection:
            current_role = str(connection.scalar(text("SELECT current_user")))
            state = connection.execute(
                text(
                    "SELECT rolcanlogin, rolinherit FROM pg_roles WHERE rolname = current_user"
                )
            ).mappings().one()
            self.assertTrue(state["rolcanlogin"])
            self.assertFalse(state["rolinherit"])
            membership_count = int(
                connection.scalar(
                    text(
                        "WITH RECURSIVE paths AS ("
                        " SELECT roleid, member FROM pg_auth_members WHERE member = "
                        " (SELECT oid FROM pg_roles WHERE rolname = current_user)"
                        " UNION ALL SELECT membership.roleid, membership.member "
                        " FROM pg_auth_members membership JOIN paths ON membership.member = paths.roleid"
                        ") SELECT count(*) FROM paths"
                    )
                )
                or 0
            )
            self.assertEqual(membership_count, 0, current_role)

        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                connection.execute(text(f'SET ROLE "{self.owner_role}"'))

        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                connection.execute(
                    text("ALTER TABLE commodities DISABLE TRIGGER trg_three_site_writer_term")
                )

    def test_application_role_accepts_current_term_and_rejects_stale_term(self) -> None:
        record_id = self._scratch_id()
        with self.engines["application"].begin() as connection:
            self._writer_settings(connection)
            self._record_coverage_event(
                connection,
                table="commodities",
                record_id=record_id,
                operation="INSERT",
                payload={"id": record_id, "name": f"scoped-{record_id}"},
            )
            connection.execute(
                text("INSERT INTO commodities (id, name) VALUES (:id, :name)"),
                {"id": record_id, "name": f"scoped-{record_id}"},
            )
        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                self._writer_settings(connection)
                connection.execute(
                    text("SELECT set_config('trading_bot.writer_epoch', '999', true)")
                )
                connection.execute(
                    text("UPDATE commodities SET name = 'stale-scratch' WHERE id = :id"),
                    {"id": record_id},
                )

    def test_application_cannot_fabricate_coverage_with_wrong_payload(self) -> None:
        record_id = self._scratch_id()
        self._seed_projection_commodity(record_id)
        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                self._writer_settings(connection)
                self._record_coverage_event(
                    connection,
                    table="commodities",
                    record_id=record_id,
                    operation="UPDATE",
                    payload={"id": record_id, "name": "forged-event-payload"},
                )
                connection.execute(
                    text("UPDATE commodities SET name = 'actual-row-payload' WHERE id = :id"),
                    {"id": record_id},
                )

    def test_writable_cte_is_rejected_at_commit_without_same_transaction_event(self) -> None:
        record_id = self._scratch_id()
        self._seed_projection_commodity(record_id)
        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                self._writer_settings(connection)
                connection.execute(
                    text(
                        "WITH changed AS ("
                        " UPDATE commodities SET name = 'cte-bypass' WHERE id = :id RETURNING id"
                        ") SELECT count(*) FROM changed"
                    ),
                    {"id": record_id},
                )

    def test_runtime_role_has_no_implicit_function_execution_surface(self) -> None:
        with self.engines["application"].connect() as connection:
            executable = int(
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_proc procedure "
                        "JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace "
                        "WHERE namespace.nspname = 'public' "
                        "AND has_function_privilege(current_user, procedure.oid, 'EXECUTE')"
                    )
                )
                or 0
            )
        self.assertEqual(executable, 0)

    def test_database_rejects_expired_boottime_even_when_wall_clock_lease_is_future(self) -> None:
        record_id = self._scratch_id()
        self._seed_projection_commodity(record_id)
        with self.engines["owner"].begin() as connection:
            connection.execute(
                text(
                    "UPDATE webapp_writer_state SET "
                    "witness_lease_expires_at = clock_timestamp() + interval '1 hour', "
                    "witness_local_boottime_deadline = trading_bot_boottime_seconds() - 1 "
                    "WHERE authority = 'webapp'"
                )
            )
        try:
            with self.assertRaises(DBAPIError):
                with self.engines["application"].begin() as connection:
                    self._writer_settings(connection)
                    self._record_coverage_event(
                        connection,
                        table="commodities",
                        record_id=record_id,
                        operation="UPDATE",
                        payload={"id": record_id, "name": "expired-boottime"},
                    )
                    connection.execute(
                        text("UPDATE commodities SET name = 'expired-boottime' WHERE id = :id"),
                        {"id": record_id},
                    )
        finally:
            with self.engines["owner"].begin() as connection:
                connection.execute(
                    text(
                        "UPDATE webapp_writer_state SET "
                        "witness_lease_expires_at = clock_timestamp() + interval '10 minutes', "
                        "witness_local_boottime_deadline = trading_bot_boottime_seconds() + 600 "
                        "WHERE authority = 'webapp'"
                    )
                )

    def test_projection_role_has_closed_table_and_field_capability(self) -> None:
        record_id = self._scratch_id()
        self._seed_projection_commodity(record_id)
        with self.engines["projection"].begin() as connection:
            self._projection_scope(connection, "projector")
            connection.execute(
                text("UPDATE commodities SET name = :name WHERE id = :id"),
                {"id": record_id, "name": f"projected-{record_id}"},
            )
        with self.assertRaises(DBAPIError):
            with self.engines["projection"].begin() as connection:
                self._projection_scope(connection, "projector")
                connection.execute(
                    text("UPDATE users SET admin_password_hash = 'forbidden' WHERE id = -1")
                )
        with self.assertRaises(DBAPIError):
            with self.engines["projection"].connect() as connection:
                connection.execute(text("SELECT admin_password_hash FROM users LIMIT 1"))

    def test_projection_role_can_store_remote_v2_event_without_source_xid(self) -> None:
        event_id = str(uuid4())
        transaction_id = str(uuid4())
        sequence = uuid4().int % 9_000_000_000 + 1
        with self.engines["receiver"].begin() as connection:
            self._projection_scope(connection, "receiver")
            connection.execute(
                text(
                    "INSERT INTO dr_events ("
                    "event_id, protocol_version, origin_authority, origin_physical_site, "
                    "producer_epoch, producer_sequence, aggregate_type, aggregate_id, "
                    "aggregate_db_id, aggregate_version, operation, canonical_payload, "
                    "canonical_payload_hash, envelope_hash, schema_version, writer_epoch, "
                    "tombstone, transaction_id, transaction_position, transaction_size, "
                    "transaction_hash, destination_streams"
                    ") VALUES ("
                    ":event_id, 2, 'foreign', 'bot_fi', 7, :sequence, 'commodities', '91', "
                    "'91', 1, 'INSERT', CAST(:payload AS JSONB), "
                    "repeat('a',64), repeat('b',64), 1, NULL, false, :transaction_id, 1, 1, "
                    "repeat('c',64), CAST(:destination_streams AS JSONB))"
                ),
                {
                    "event_id": event_id,
                    "sequence": sequence,
                    "transaction_id": transaction_id,
                    "payload": json.dumps({"id": 91, "name": "remote"}),
                    "destination_streams": json.dumps(
                        {"webapp_fi": {"sequence": 1}}
                    ),
                },
            )
        with self.engines["owner"].connect() as connection:
            source_xid = connection.scalar(
                text("SELECT source_xid FROM dr_events WHERE event_id=:event_id"),
                {"event_id": event_id},
            )
        self.assertIsNone(source_xid)

    def test_projection_role_can_persist_encrypted_blob_identity(self) -> None:
        content_hash = uuid4().hex + uuid4().hex
        object_key = f"encrypted/scratch/{content_hash}"
        with self.engines["blob"].begin() as connection:
            self._projection_scope(connection, "blob")
            connection.execute(
                text(
                    "INSERT INTO dr_blob_manifests ("
                    "content_hash, size_bytes, mime_type, local_path, object_key, "
                    "object_ciphertext_hash, object_ciphertext_size, encryption_key_id, "
                    "encryption_algorithm, state"
                    ") VALUES ("
                    ":content_hash, 4, 'application/octet-stream', '/tmp/scratch', :object_key, "
                    "repeat('d',64), 40, 'scratch-key', 'AES-256-GCM-v1', 'uploaded')"
                ),
                {"content_hash": content_hash, "object_key": object_key},
            )
            connection.execute(
                text(
                    "UPDATE dr_blob_manifests SET object_version_id='version-1', "
                    "object_etag='etag-1' WHERE content_hash=:content_hash"
                ),
                {"content_hash": content_hash},
            )

    def test_each_dr_service_role_is_confined_to_its_own_scope(self) -> None:
        with self.assertRaises(DBAPIError):
            with self.engines["delivery"].begin() as connection:
                self._projection_scope(connection, "receiver")
                connection.execute(
                    text(
                        "INSERT INTO dr_replay_nonces "
                        "(key_id, nonce, source_site, destination_site, request_hash, expires_at) "
                        "VALUES ('scope-test', :nonce, 'bot_fi', 'webapp_fi', repeat('a',64), "
                        "clock_timestamp() + interval '1 minute')"
                    ),
                    {"nonce": uuid4().hex},
                )
        with self.assertRaises(DBAPIError):
            with self.engines["receiver"].begin() as connection:
                self._projection_scope(connection, "receiver")
                connection.execute(
                    text("UPDATE commodities SET name='forbidden-receiver' WHERE id=-1")
                )
        with self.assertRaises(DBAPIError):
            with self.engines["blob"].connect() as connection:
                connection.execute(text("SELECT admin_password_hash FROM users LIMIT 1"))

    def test_control_role_is_limited_to_control_state(self) -> None:
        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                connection.execute(
                    text("UPDATE webapp_writer_state SET reason = 'forbidden' WHERE authority = 'webapp'")
                )
        with self.engines["control"].connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text("SELECT set_config('trading_bot.mutation_capability', 'control', true)")
            )
            connection.execute(
                text(
                    "UPDATE webapp_writer_state SET reason = :reason "
                    "WHERE authority = 'webapp'"
                ),
                {"reason": "scratch-control-" + uuid4().hex},
            )
            connection.execute(
                text(
                    "UPDATE dr_durability_state SET connectivity_mode = 'online', "
                    "evidence_hash = repeat('f', 64), "
                    "evidence_expires_at = clock_timestamp() + interval '1 minute', "
                    "updated_by = 'scratch-control' WHERE singleton_id = 1"
                )
            )
            transaction.rollback()
        with self.assertRaises(DBAPIError):
            with self.engines["control"].begin() as connection:
                connection.execute(
                    text(
                        "UPDATE dr_database_runtime SET enforcement_enabled = false "
                        "WHERE singleton_id = 1"
                    )
                )
        with self.assertRaises(DBAPIError):
            with self.engines["control"].begin() as connection:
                connection.execute(
                    text("DELETE FROM webapp_writer_state WHERE authority = 'webapp'")
                )

    def test_effect_status_is_mutable_but_effect_intent_is_immutable(self) -> None:
        event_id = str(uuid4())
        effect_id = str(uuid4())
        idempotency_key = "scratch-effect-" + uuid4().hex
        producer_sequence = uuid4().int % 9_000_000_000 + 1
        with self.engines["application"].begin() as connection:
            self._writer_settings(connection)
            connection.execute(
                text(
                    "INSERT INTO dr_events ("
                    "event_id, protocol_version, origin_authority, origin_physical_site, "
                    "producer_epoch, producer_sequence, aggregate_type, aggregate_id, "
                    "operation, canonical_payload, canonical_payload_hash, envelope_hash, "
                    "schema_version, writer_epoch"
                    ") VALUES ("
                    ":event_id, 1, 'webapp', 'webapp_fi', 1, :producer_sequence, "
                    "'scratch_effect', :aggregate_id, 'INSERT', '{}'::jsonb, repeat('b', 64), "
                    "repeat('c', 64), 1, 1"
                    ")"
                ),
                {
                    "event_id": event_id,
                    "aggregate_id": effect_id,
                    "producer_sequence": producer_sequence,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO dr_effect_outbox ("
                    "effect_id, event_id, origin_physical_site, executor_site, writer_epoch, "
                    "effect_type, provider, destination_key_hash, idempotency_key, payload, payload_hash"
                    ") VALUES ("
                    ":effect_id, :event_id, 'webapp_fi', 'webapp_fi', 1, 'push', 'webpush', "
                    "repeat('d', 64), :idempotency_key, '{}'::jsonb, repeat('e', 64)"
                    ")"
                ),
                {
                    "effect_id": effect_id,
                    "event_id": event_id,
                    "idempotency_key": idempotency_key,
                },
            )
        with self.engines["effect"].begin() as connection:
            self._projection_scope(connection, "effect")
            connection.execute(
                text("UPDATE dr_effect_outbox SET status = 'inflight' WHERE effect_id = :effect_id"),
                {"effect_id": effect_id},
            )

        with self.assertRaises(DBAPIError):
            with self.engines["effect"].begin() as connection:
                self._projection_scope(connection, "effect")
                connection.execute(
                    text(
                        "UPDATE dr_effect_outbox SET payload = '{\"changed\": true}'::jsonb "
                        "WHERE effect_id = :effect_id"
                    ),
                    {"effect_id": effect_id},
                )


if __name__ == "__main__":
    unittest.main()
