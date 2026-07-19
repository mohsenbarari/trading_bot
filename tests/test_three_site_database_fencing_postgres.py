"""Opt-in real-PostgreSQL proof for strict three-site database roles.

The test is skipped unless four scratch-only URLs are supplied. It must never
be pointed at a runtime database.
"""

from __future__ import annotations

import os
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError


URL_NAMES = {
    "owner": "THREE_SITE_FENCING_TEST_OWNER_URL",
    "application": "THREE_SITE_FENCING_TEST_APP_URL",
    "projection": "THREE_SITE_FENCING_TEST_PROJECTION_URL",
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
            database_name = str(connection.scalar(text("SELECT current_database()")))
            if not database_name.startswith("stage4_registration_"):
                raise RuntimeError("database fencing integration test requires a guarded scratch database")
            connection.execute(
                text(
                    "UPDATE webapp_writer_state SET "
                    "witness_lease_id = 'scratch-lease', "
                    "witness_lease_issued_at = clock_timestamp(), "
                    "witness_lease_expires_at = clock_timestamp() + interval '10 minutes', "
                    "witness_proof_hash = repeat('a', 64), "
                    "witness_transition_id = 'scratch-transition', "
                    "witness_local_boot_id = '12345678-1234-4234-8234-123456789abc', "
                    "witness_local_boottime_deadline = 999999999, "
                    "witness_observed_wall_at = clock_timestamp(), "
                    "witness_observed_boottime = 1, witness_clock_offset_ms = 0 "
                    "WHERE authority = 'webapp'"
                )
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

    def test_application_role_fails_closed_without_writer_capability(self) -> None:
        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                connection.execute(
                    text("INSERT INTO commodities (id, name) VALUES (990001, 'unscoped-scratch')")
                )

    def test_application_role_accepts_current_term_and_rejects_stale_term(self) -> None:
        with self.engines["application"].begin() as connection:
            self._writer_settings(connection)
            connection.execute(
                text("INSERT INTO commodities (id, name) VALUES (990002, 'scoped-scratch')")
            )
        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                self._writer_settings(connection)
                connection.execute(
                    text("SELECT set_config('trading_bot.writer_epoch', '999', true)")
                )
                connection.execute(
                    text("UPDATE commodities SET name = 'stale-scratch' WHERE id = 990002")
                )

    def test_projection_role_has_closed_table_and_field_capability(self) -> None:
        with self.engines["projection"].begin() as connection:
            connection.execute(
                text("SELECT set_config('trading_bot.mutation_capability', 'projection', true)")
            )
            connection.execute(
                text("UPDATE commodities SET name = 'projected-scratch' WHERE id = 990002")
            )
        with self.assertRaises(DBAPIError):
            with self.engines["projection"].begin() as connection:
                connection.execute(
                    text("SELECT set_config('trading_bot.mutation_capability', 'projection', true)")
                )
                connection.execute(
                    text("UPDATE users SET admin_password_hash = 'forbidden' WHERE id = -1")
                )

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
                    ":event_id, 1, 'webapp', 'webapp_fi', 1, 990003, "
                    "'scratch_effect', :aggregate_id, 'INSERT', '{}'::jsonb, repeat('b', 64), "
                    "repeat('c', 64), 1, 1"
                    ")"
                ),
                {"event_id": event_id, "aggregate_id": effect_id},
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
            connection.execute(
                text("UPDATE dr_effect_outbox SET status = 'inflight' WHERE effect_id = :effect_id"),
                {"effect_id": effect_id},
            )

        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                self._writer_settings(connection)
                connection.execute(
                    text(
                        "UPDATE dr_effect_outbox SET payload = '{\"changed\": true}'::jsonb "
                        "WHERE effect_id = :effect_id"
                    ),
                    {"effect_id": effect_id},
                )


if __name__ == "__main__":
    unittest.main()
