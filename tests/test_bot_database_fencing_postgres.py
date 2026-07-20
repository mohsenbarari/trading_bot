"""Opt-in real-PostgreSQL proof for the Bot-FI foreign-authority fence."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError


URL_NAMES = {
    "owner": "BOT_FENCING_TEST_OWNER_URL",
    "application": "BOT_FENCING_TEST_APP_URL",
    "projection": "BOT_FENCING_TEST_PROJECTION_URL",
}


@unittest.skipUnless(
    all(os.environ.get(name) for name in URL_NAMES.values()),
    "scratch Bot fencing URLs are not configured",
)
class BotDatabaseFencingPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engines = {
            role: create_engine(os.environ[name], pool_pre_ping=True)
            for role, name in URL_NAMES.items()
        }
        with cls.engines["owner"].connect() as connection:
            cls.owner_role = str(connection.scalar(text("SELECT current_user")))
            database_name = str(connection.scalar(text("SELECT current_database()")))
            if not database_name.startswith("stage4_registration_"):
                raise RuntimeError("Bot fencing integration test requires a guarded scratch database")
            runtime = connection.execute(
                text(
                    "SELECT enforcement_enabled, physical_site, application_role, projection_role, "
                    "control_role, require_witness_lease FROM dr_database_runtime WHERE singleton_id=1"
                )
            ).mappings().one()
            expected = {
                "enforcement_enabled": True,
                "physical_site": "bot_fi",
                "application_role": "bot_fi_app",
                "projection_role": "bot_fi_projection",
                "control_role": None,
                "require_witness_lease": False,
            }
            if dict(runtime) != expected:
                raise RuntimeError(f"Bot database fence is not active: {dict(runtime)!r}")

    @classmethod
    def tearDownClass(cls) -> None:
        for engine in cls.engines.values():
            engine.dispose()

    def _scratch_id(self) -> int:
        return 800_000_000 + uuid4().int % 90_000_000

    def _foreign_writer(self, connection) -> None:  # noqa: ANN001
        connection.execute(
            text("SELECT set_config('trading_bot.mutation_capability', 'foreign_writer', true)")
        )
        connection.execute(
            text("SELECT set_config('trading_bot.physical_site', 'bot_fi', true)")
        )

    def _coverage_event(self, connection, *, record_id: int, operation: str) -> None:  # noqa: ANN001
        connection.execute(
            text(
                "INSERT INTO dr_events ("
                "event_id, protocol_version, origin_authority, origin_physical_site, "
                "producer_epoch, producer_sequence, aggregate_type, aggregate_id, "
                "aggregate_db_id, operation, canonical_payload, canonical_payload_hash, "
                "envelope_hash, schema_version, tombstone, source_xid"
                ") VALUES ("
                ":event_id, 1, 'foreign', 'bot_fi', 1, :sequence, 'commodities', "
                ":record_id, :record_id, :operation, '{}'::jsonb, repeat('a',64), "
                "repeat('b',64), 1, :tombstone, txid_current())"
            ),
            {
                "event_id": str(uuid4()),
                "sequence": uuid4().int % 9_000_000_000 + 1,
                "record_id": str(record_id),
                "operation": operation,
                "tombstone": operation == "DELETE",
            },
        )

    def _seed_projection_commodity(self, record_id: int) -> None:
        with self.engines["projection"].begin() as connection:
            connection.execute(
                text("SELECT set_config('trading_bot.mutation_capability', 'projection', true)")
            )
            connection.execute(
                text("INSERT INTO commodities (id, name) VALUES (:id, :name)"),
                {"id": record_id, "name": f"bot-projection-{record_id}"},
            )

    def test_application_requires_foreign_writer_and_same_transaction_event(self) -> None:
        without_capability = self._scratch_id()
        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                connection.execute(
                    text("INSERT INTO commodities (id, name) VALUES (:id, :name)"),
                    {"id": without_capability, "name": f"no-capability-{without_capability}"},
                )

        with_capability = self._scratch_id()
        with self.engines["application"].begin() as connection:
            self._foreign_writer(connection)
            self._coverage_event(
                connection, record_id=with_capability, operation="INSERT"
            )
            connection.execute(
                text("INSERT INTO commodities (id, name) VALUES (:id, :name)"),
                {"id": with_capability, "name": f"bot-authority-{with_capability}"},
            )

    def test_writable_cte_cannot_commit_without_event(self) -> None:
        record_id = self._scratch_id()
        self._seed_projection_commodity(record_id)
        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                self._foreign_writer(connection)
                connection.execute(
                    text(
                        "WITH changed AS (UPDATE commodities SET name='bot-cte-bypass' "
                        "WHERE id=:id RETURNING id) SELECT count(*) FROM changed"
                    ),
                    {"id": record_id},
                )

    def test_roles_have_no_owner_path_function_execution_or_control_write(self) -> None:
        for role in ("application", "projection"):
            with self.engines[role].connect() as connection:
                state = connection.execute(
                    text(
                        "SELECT rolinherit, rolsuper, rolcreaterole, rolcreatedb, "
                        "rolreplication, rolbypassrls FROM pg_roles WHERE rolname=current_user"
                    )
                ).mappings().one()
                self.assertFalse(any(state.values()), role)
                memberships = int(
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM pg_auth_members membership "
                            "WHERE membership.member=(SELECT oid FROM pg_roles WHERE rolname=current_user) "
                            "OR membership.roleid=(SELECT oid FROM pg_roles WHERE rolname=current_user)"
                        )
                    )
                    or 0
                )
                self.assertEqual(memberships, 0, role)
                executable = int(
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM pg_proc procedure JOIN pg_namespace namespace "
                            "ON namespace.oid=procedure.pronamespace WHERE namespace.nspname='public' "
                            "AND has_function_privilege(current_user, procedure.oid, 'EXECUTE')"
                        )
                    )
                    or 0
                )
                self.assertEqual(executable, 0, role)
                with self.assertRaises(DBAPIError):
                    connection.execute(text(f'SET ROLE "{self.owner_role}"'))
                connection.rollback()

        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                connection.execute(
                    text("UPDATE dr_database_runtime SET enforcement_enabled=false WHERE singleton_id=1")
                )

    def test_bot_projection_is_product_only(self) -> None:
        record_id = self._scratch_id()
        self._seed_projection_commodity(record_id)
        with self.assertRaises(DBAPIError):
            with self.engines["projection"].begin() as connection:
                connection.execute(
                    text("SELECT set_config('trading_bot.mutation_capability', 'projection', true)")
                )
                connection.execute(
                    text(
                        "INSERT INTO push_subscriptions "
                        "(id,user_id,endpoint_hash,endpoint,p256dh,auth,enabled) "
                        "VALUES (:id,-1,repeat('a',64),'https://example.invalid','x','y',true)"
                    ),
                    {"id": record_id},
                )

    def test_real_protocol_v2_commit_has_contiguous_authorized_destination_streams(self) -> None:
        from core.dr_event_protocol import append_local_dr_event, finalize_local_dr_transaction
        from core.config import settings

        record_id = self._scratch_id()
        transaction_id = str(uuid4())
        with patch.multiple(
            settings,
            three_site_dr_enabled=True,
            dr_event_protocol_enabled=True,
            dr_event_protocol_strict=True,
            topology_schema_version="three-site-dr-v1",
            logical_authority="foreign",
            physical_site="bot_fi",
            server_mode="foreign",
            dr_producer_epoch=17,
        ):
            with self.engines["application"].begin() as connection:
                self._foreign_writer(connection)
                event_id = append_local_dr_event(
                    connection,
                    table_name="commodities",
                    record_id=record_id,
                    operation="INSERT",
                    data={"id": record_id, "name": f"protocol-v2-{record_id}"},
                    change_log_id=None,
                    transaction_id=transaction_id,
                    transaction_position=1,
                )
                self.assertIsNotNone(event_id)
                connection.execute(
                    text("INSERT INTO commodities (id, name) VALUES (:id, :name)"),
                    {"id": record_id, "name": f"protocol-v2-{record_id}"},
                )
                finalize_local_dr_transaction(connection, [str(event_id)])
                finalized = connection.execute(
                    text(
                        "SELECT transaction_id, transaction_position, transaction_size, "
                        "transaction_hash, envelope_hash, destination_streams, "
                        "(SELECT count(*) FROM dr_events member WHERE "
                        "member.origin_physical_site=dr_events.origin_physical_site AND "
                        "member.producer_epoch=dr_events.producer_epoch AND "
                        "member.transaction_id=dr_events.transaction_id) AS member_count "
                        "FROM dr_events WHERE event_id=:event_id"
                    ),
                    {"event_id": event_id},
                ).mappings().one()
                self.assertEqual(
                    {
                        "transaction_id": finalized["transaction_id"],
                        "transaction_position": finalized["transaction_position"],
                        "transaction_size": finalized["transaction_size"],
                        "transaction_hash_is_final": finalized["transaction_hash"] != "0" * 64,
                        "envelope_hash_is_final": finalized["envelope_hash"] != "0" * 64,
                        "has_destination_streams": bool(finalized["destination_streams"]),
                        "member_count": finalized["member_count"],
                    },
                    {
                        "transaction_id": transaction_id,
                        "transaction_position": 1,
                        "transaction_size": 1,
                        "transaction_hash_is_final": True,
                        "envelope_hash_is_final": True,
                        "has_destination_streams": True,
                        "member_count": 1,
                    },
                )

        with self.engines["owner"].connect() as connection:
            row = connection.execute(
                text(
                    "SELECT protocol_version, destination_streams, envelope_hash "
                    "FROM dr_events WHERE event_id=:event_id"
                ),
                {"event_id": event_id},
            ).mappings().one()
            deliveries = set(
                connection.execute(
                    text(
                        "SELECT destination_site FROM dr_event_deliveries "
                        "WHERE event_id=:event_id"
                    ),
                    {"event_id": event_id},
                ).scalars()
            )
        self.assertEqual(row["protocol_version"], 2)
        self.assertEqual(set(row["destination_streams"]), {"webapp_fi", "webapp_ir"})
        self.assertEqual(deliveries, {"webapp_fi"})
        for stream in row["destination_streams"].values():
            self.assertGreaterEqual(int(stream["sequence"]), 1)
            self.assertEqual(stream["transaction_position"], 1)
            self.assertEqual(stream["transaction_size"], 1)
            self.assertNotEqual(stream["transaction_hash"], "0" * 64)
        self.assertNotEqual(row["envelope_hash"], "0" * 64)


if __name__ == "__main__":
    unittest.main()
