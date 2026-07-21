"""Opt-in real-PostgreSQL proof for strict three-site database roles.

The test is skipped unless four scratch-only URLs are supplied. It must never
be pointed at a runtime database.
"""

from __future__ import annotations

import os
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from core.dr_event_protocol import (
    canonical_json_bytes,
    destination_transaction_hash,
    envelope_hash,
    sha256_json,
    transaction_hash_from_envelopes,
    validate_envelope,
)


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
        corrupt_envelope_hash: bool = False,
        destinations: tuple[str, ...] = ("bot_fi", "webapp_ir"),
        destination_numeric_override: tuple[str, object] | None = None,
    ) -> None:  # noqa: ANN001
        event_id = str(uuid4())
        transaction_id = str(uuid4())
        sequence = int(
            connection.scalar(
                text(
                    "INSERT INTO dr_producer_cursors ("
                    "origin_authority,origin_physical_site,producer_epoch,last_sequence) "
                    "VALUES ('webapp','webapp_fi',1,1) ON CONFLICT ("
                    "origin_authority,origin_physical_site,producer_epoch) DO UPDATE SET "
                    "last_sequence=dr_producer_cursors.last_sequence+1,updated_at=clock_timestamp() "
                    "RETURNING last_sequence"
                )
            )
        )
        created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        canonical_payload = json.loads(canonical_json_bytes(payload))
        streams = {}
        for site in destinations:
            destination_sequence = int(
                connection.scalar(
                    text(
                        "INSERT INTO dr_destination_cursors ("
                        "origin_authority,origin_physical_site,producer_epoch,destination_site,last_sequence) "
                        "VALUES ('webapp','webapp_fi',1,:site,1) ON CONFLICT ("
                        "origin_authority,origin_physical_site,producer_epoch,destination_site) "
                        "DO UPDATE SET last_sequence=dr_destination_cursors.last_sequence+1,"
                        "updated_at=clock_timestamp() RETURNING last_sequence"
                    ),
                    {"site": site},
                )
            )
            streams[site] = {
                "sequence": destination_sequence,
                "transaction_id": transaction_id,
                "transaction_position": 1,
                "transaction_size": 1,
                "transaction_hash": "0" * 64,
            }
        envelope = {
            "protocol_version": 2,
            "event_id": event_id,
            "origin_authority": "webapp",
            "origin_physical_site": "webapp_fi",
            "producer_epoch": 1,
            "producer_sequence": sequence,
            "aggregate_type": table,
            "aggregate_id": str(record_id),
            "aggregate_db_id": str(record_id),
            "aggregate_version": None,
            "operation": operation,
            "canonical_payload": canonical_payload,
            "canonical_payload_hash": sha256_json(canonical_payload),
            "schema_version": 1,
            "causation_id": None,
            "idempotency_key": None,
            "writer_epoch": 1,
            "tombstone": operation == "DELETE",
            "created_at": created_at,
            "transaction_id": transaction_id,
            "transaction_position": 1,
            "transaction_size": 1,
            "transaction_hash": "0" * 64,
            "destination_streams": streams,
        }
        envelope["transaction_hash"] = transaction_hash_from_envelopes([envelope])
        for site in streams:
            streams[site]["transaction_hash"] = destination_transaction_hash(
                [envelope], destination_site=site
            )
        if destination_numeric_override is not None:
            field, value = destination_numeric_override
            for stream in streams.values():
                stream[field] = value
        validated_envelope_hash = (
            validate_envelope(envelope).envelope_hash
            if set(destinations) == {"bot_fi", "webapp_ir"}
            and destination_numeric_override is None
            else envelope_hash(envelope)
        )
        connection.execute(
            text(
                "INSERT INTO dr_events ("
                "event_id, protocol_version, origin_authority, origin_physical_site, "
                "producer_epoch, producer_sequence, aggregate_type, aggregate_id, "
                "aggregate_db_id, aggregate_version, operation, canonical_payload, "
                "canonical_payload_hash, envelope_hash, schema_version, causation_id, "
                "idempotency_key, writer_epoch, tombstone, created_at, transaction_id, "
                "transaction_position, transaction_size, transaction_hash, "
                "destination_streams, source_xid"
                ") VALUES ("
                ":event_id, 2, 'webapp', 'webapp_fi', 1, :sequence, :table, :record_id, "
                ":record_id, NULL, :operation, CAST(:payload AS JSONB), :payload_hash, "
                ":envelope_hash, 1, NULL, NULL, 1, :tombstone, CAST(:created_at AS TIMESTAMPTZ), "
                ":transaction_id, 1, 1, :transaction_hash, CAST(:streams AS JSONB), txid_current())"
            ),
            {
                "event_id": event_id,
                "sequence": sequence,
                "table": table,
                "record_id": str(record_id),
                "operation": operation,
                "tombstone": operation == "DELETE",
                "payload": canonical_json_bytes(canonical_payload).decode(),
                "payload_hash": envelope["canonical_payload_hash"],
                "envelope_hash": "b" * 64 if corrupt_envelope_hash else validated_envelope_hash,
                "created_at": created_at,
                "transaction_id": transaction_id,
                "transaction_hash": envelope["transaction_hash"],
                "streams": canonical_json_bytes(streams).decode(),
            },
        )

    def test_application_cannot_fabricate_coverage_with_wrong_integrity_hashes(self) -> None:
        record_id = self._scratch_id()
        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                self._writer_settings(connection)
                self._record_coverage_event(
                    connection,
                    table="commodities",
                    record_id=record_id,
                    operation="INSERT",
                    payload={"id": record_id, "name": f"hash-{record_id}"},
                    corrupt_envelope_hash=True,
                )
                connection.execute(
                    text("INSERT INTO commodities (id, name) VALUES (:id, :name)"),
                    {"id": record_id, "name": f"hash-{record_id}"},
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

        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                connection.execute(text("SET session_replication_role = 'replica'"))

        with self.engines["owner"].connect() as connection:
            non_always = list(
                connection.execute(
                    text(
                        "SELECT trigger.tgname FROM pg_trigger trigger "
                        "JOIN pg_class relation ON relation.oid=trigger.tgrelid "
                        "JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace "
                        "WHERE namespace.nspname='public' AND NOT trigger.tgisinternal "
                        "AND trigger.tgname IN ("
                        "'trg_three_site_writer_term','trg_three_site_event_coverage',"
                        "'trg_three_site_mutation_capture','trg_three_site_cursor_guard',"
                        "'trg_three_site_cursor_tail','trg_dr_events_immutable',"
                        "'trg_dr_event_finalized','trg_dr_receiver_source_xid',"
                        "'trg_dr_bind_local_sequences','trg_dr_event_destination_binding',"
                        "'trg_dr_event_mutation_binding',"
                        "'trg_dr_effect_intent_immutable','trg_dr_effect_fanout_intent_immutable') "
                        "AND trigger.tgenabled <> 'A'"
                    )
                )
            )
        self.assertEqual(non_always, [])

    def test_application_has_no_delete_capability_on_service_internal_state(self) -> None:
        tables = (
            "dr_event_deliveries",
            "dr_replay_nonces",
            "dr_stream_checkpoints",
            "dr_projection_versions",
            "dr_event_destination_sequences",
        )
        with self.engines["application"].connect() as connection:
            effective = {
                table: bool(
                    connection.scalar(
                        text("SELECT has_table_privilege(current_user, :table, 'DELETE')"),
                        {"table": f"public.{table}"},
                    )
                )
                for table in tables
            }
        self.assertEqual(effective, {table: False for table in tables})

    def test_mutation_capture_is_transaction_temporary_and_rejects_temp_poisoning(self) -> None:
        with self.engines["owner"].connect() as connection:
            self.assertIsNone(
                connection.scalar(text("SELECT to_regclass('public.dr_authoritative_mutations')"))
            )
        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                connection.execute(
                    text("CREATE TEMP TABLE trading_bot_authoritative_mutations (value integer)")
                )
                self._writer_settings(connection)
                record_id = self._scratch_id()
                connection.execute(
                    text("INSERT INTO commodities (id, name) VALUES (:id, :name)"),
                    {"id": record_id, "name": f"temp-poison-{record_id}"},
                )

    def test_local_event_requires_exact_destinations_and_a_real_mutation(self) -> None:
        for destinations in (("bot_fi",), ("bot_fi", "webapp_fi", "webapp_ir")):
            with self.subTest(destinations=destinations), self.assertRaises(DBAPIError):
                with self.engines["application"].begin() as connection:
                    self._writer_settings(connection)
                    record_id = self._scratch_id()
                    self._record_coverage_event(
                        connection,
                        table="commodities",
                        record_id=record_id,
                        operation="INSERT",
                        payload={"id": record_id, "name": f"entitlement-{record_id}"},
                        destinations=destinations,
                    )

        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                self._writer_settings(connection)
                record_id = self._scratch_id()
                self._record_coverage_event(
                    connection,
                    table="commodities",
                    record_id=record_id,
                    operation="INSERT",
                    payload={"id": record_id, "name": f"orphan-{record_id}"},
                )

    def test_application_cannot_jump_or_delete_local_sequence_cursors(self) -> None:
        with self.engines["application"].begin() as connection:
            self._writer_settings(connection)
            record_id = self._scratch_id()
            payload = {"id": record_id, "name": f"cursor-seed-{record_id}"}
            self._record_coverage_event(
                connection,
                table="commodities",
                record_id=record_id,
                operation="INSERT",
                payload=payload,
            )
            connection.execute(
                text("INSERT INTO commodities (id, name) VALUES (:id, :name)"),
                payload,
            )
        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                self._writer_settings(connection)
                connection.execute(
                    text(
                        "UPDATE dr_producer_cursors SET last_sequence=last_sequence+2 "
                        "WHERE origin_authority='webapp' AND origin_physical_site='webapp_fi' "
                        "AND producer_epoch=1"
                    )
                )
        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                self._writer_settings(connection)
                connection.execute(
                    text(
                        "DELETE FROM dr_destination_cursors "
                        "WHERE origin_authority='webapp' AND origin_physical_site='webapp_fi' "
                        "AND producer_epoch=1 AND destination_site='bot_fi'"
                    )
                )

    def test_webapp_cursor_transitions_reject_a_missing_intermediate_event(self) -> None:
        record_id = self._scratch_id()
        payload = {"id": record_id, "name": f"webapp-cursor-gap-{record_id}"}
        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                self._writer_settings(connection)
                connection.execute(
                    text(
                        "INSERT INTO dr_producer_cursors ("
                        "origin_authority,origin_physical_site,producer_epoch,last_sequence) "
                        "VALUES ('webapp','webapp_fi',1,1) ON CONFLICT ("
                        "origin_authority,origin_physical_site,producer_epoch) DO UPDATE SET "
                        "last_sequence=dr_producer_cursors.last_sequence+1,"
                        "updated_at=clock_timestamp()"
                    )
                )
                for site in ("bot_fi", "webapp_ir"):
                    connection.execute(
                        text(
                            "INSERT INTO dr_destination_cursors ("
                            "origin_authority,origin_physical_site,producer_epoch,"
                            "destination_site,last_sequence) VALUES ("
                            "'webapp','webapp_fi',1,:site,1) ON CONFLICT ("
                            "origin_authority,origin_physical_site,producer_epoch,"
                            "destination_site) DO UPDATE SET "
                            "last_sequence=dr_destination_cursors.last_sequence+1,"
                            "updated_at=clock_timestamp()"
                        ),
                        {"site": site},
                    )
                self._record_coverage_event(
                    connection,
                    table="commodities",
                    record_id=record_id,
                    operation="INSERT",
                    payload=payload,
                )
                connection.execute(
                    text("INSERT INTO commodities (id, name) VALUES (:id, :name)"),
                    payload,
                )

    def test_database_rejects_noncanonical_destination_numeric_scalars(self) -> None:
        hostile_values = (
            ("string", "1"),
            ("fractional", 1.5),
            ("zero", 0),
            ("negative", -1),
            ("boolean", True),
            ("null", None),
        )
        maximums = {
            "sequence": 9223372036854775808,
            "transaction_position": 2147483648,
            "transaction_size": 2147483648,
        }
        for field in maximums:
            cases = hostile_values + (("oversized", maximums[field]),)
            for label, value in cases:
                with self.subTest(field=field, case=label), self.assertRaises(DBAPIError):
                    with self.engines["application"].begin() as connection:
                        self._writer_settings(connection)
                        record_id = self._scratch_id()
                        payload = {
                            "id": record_id,
                            "name": f"numeric-{field}-{label}-{record_id}",
                        }
                        self._record_coverage_event(
                            connection,
                            table="commodities",
                            record_id=record_id,
                            operation="INSERT",
                            payload=payload,
                            destination_numeric_override=(field, value),
                        )
                        connection.execute(
                            text("INSERT INTO commodities (id, name) VALUES (:id, :name)"),
                            payload,
                        )

    def test_rolled_back_savepoint_allocations_do_not_poison_cursor_tail(self) -> None:
        rolled_back_id = self._scratch_id()
        committed_id = self._scratch_id()
        with self.engines["application"].begin() as connection:
            self._writer_settings(connection)
            savepoint = connection.begin_nested()
            rolled_back_payload = {
                "id": rolled_back_id,
                "name": f"savepoint-rollback-{rolled_back_id}",
            }
            self._record_coverage_event(
                connection,
                table="commodities",
                record_id=rolled_back_id,
                operation="INSERT",
                payload=rolled_back_payload,
            )
            connection.execute(
                text("INSERT INTO commodities (id, name) VALUES (:id, :name)"),
                rolled_back_payload,
            )
            savepoint.rollback()

            committed_payload = {
                "id": committed_id,
                "name": f"savepoint-commit-{committed_id}",
            }
            self._record_coverage_event(
                connection,
                table="commodities",
                record_id=committed_id,
                operation="INSERT",
                payload=committed_payload,
            )
            connection.execute(
                text("INSERT INTO commodities (id, name) VALUES (:id, :name)"),
                committed_payload,
            )
        with self.engines["owner"].connect() as connection:
            rows = set(
                connection.execute(
                    text("SELECT id FROM commodities WHERE id IN (:rolled_back, :committed)"),
                    {"rolled_back": rolled_back_id, "committed": committed_id},
                ).scalars()
            )
        self.assertEqual(rows, {committed_id})

    def test_two_concurrent_allocators_commit_consecutive_historical_tail_events(self) -> None:
        barrier = Barrier(2)
        record_ids = (self._scratch_id(), self._scratch_id())

        def allocate(record_id: int) -> None:
            with self.engines["application"].begin() as connection:
                self._writer_settings(connection)
                barrier.wait(timeout=10)
                payload = {"id": record_id, "name": f"concurrent-{record_id}"}
                self._record_coverage_event(
                    connection,
                    table="commodities",
                    record_id=record_id,
                    operation="INSERT",
                    payload=payload,
                )
                connection.execute(
                    text("INSERT INTO commodities (id, name) VALUES (:id, :name)"),
                    payload,
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(allocate, record_id) for record_id in record_ids]
            for future in futures:
                future.result(timeout=30)

        with self.engines["owner"].connect() as connection:
            sequences = list(
                connection.execute(
                    text(
                        "SELECT producer_sequence FROM dr_events "
                        "WHERE aggregate_type='commodities' "
                        "AND aggregate_db_id IN (:first_id, :second_id) "
                        "ORDER BY producer_sequence"
                    ),
                    {"first_id": str(record_ids[0]), "second_id": str(record_ids[1])},
                ).scalars()
            )
        self.assertEqual(len(sequences), 2)
        self.assertEqual(sequences[1], sequences[0] + 1)

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

    def test_fenced_source_rejects_a_fresh_application_reconnect(self) -> None:
        """A process that reconnects after drain cannot extend the source tail."""
        record_id = self._scratch_id()
        with self.engines["owner"].begin() as connection:
            original = dict(
                connection.execute(
                    text(
                        "SELECT active_site, control_state, reason "
                        "FROM webapp_writer_state WHERE authority='webapp'"
                    )
                ).mappings().one()
            )
            connection.execute(
                text(
                    "UPDATE webapp_writer_state SET active_site=NULL, "
                    "control_state='fenced', reason='scratch-reconnect-fence' "
                    "WHERE authority='webapp'"
                )
            )
        try:
            # This begins a new physical database session after the committed
            # admission fence.  Supplying the old, otherwise-valid Writer term
            # must not let it update cursors, append an event, or mutate data.
            self.engines["application"].dispose()
            with self.assertRaises(DBAPIError):
                with self.engines["application"].begin() as connection:
                    self._writer_settings(connection)
                    self._record_coverage_event(
                        connection,
                        table="commodities",
                        record_id=record_id,
                        operation="INSERT",
                        payload={"id": record_id, "name": f"late-{record_id}"},
                    )
                    connection.execute(
                        text("INSERT INTO commodities (id, name) VALUES (:id, :name)"),
                        {"id": record_id, "name": f"late-{record_id}"},
                    )
        finally:
            with self.engines["owner"].begin() as connection:
                connection.execute(
                    text(
                        "UPDATE webapp_writer_state SET active_site=:active_site, "
                        "control_state=:control_state, reason=:reason "
                        "WHERE authority='webapp'"
                    ),
                    original,
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

    def test_receiver_cannot_explicitly_supply_source_xid(self) -> None:
        for source_xid in (None, 424242):
            with self.subTest(source_xid=source_xid), self.assertRaises(DBAPIError):
                with self.engines["receiver"].begin() as connection:
                    self._projection_scope(connection, "receiver")
                    connection.execute(
                        text(
                            "INSERT INTO dr_events ("
                            "event_id, protocol_version, origin_authority, origin_physical_site, "
                            "producer_epoch, producer_sequence, aggregate_type, aggregate_id, "
                            "operation, canonical_payload, canonical_payload_hash, envelope_hash, "
                            "schema_version, tombstone, source_xid"
                            ") VALUES ("
                            ":event_id, 1, 'foreign', 'bot_fi', 1, :sequence, "
                            "'commodities', 'source-xid-test', 'INSERT', '{}'::jsonb, "
                            "repeat('a',64), repeat('b',64), 1, false, :source_xid)"
                        ),
                        {
                            "event_id": str(uuid4()),
                            "sequence": uuid4().int % 9_000_000_000 + 1,
                            "source_xid": source_xid,
                        },
                    )

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
        # This test isolates the effect-intent state machine. Seed its immutable
        # foreign-key parent through the receiver's foreign-event path so the
        # owner role is never used as a runtime-DML bypass.
        with self.engines["receiver"].begin() as connection:
            self._projection_scope(connection, "receiver")
            connection.execute(
                text(
                    "INSERT INTO dr_events ("
                    "event_id, protocol_version, origin_authority, origin_physical_site, "
                    "producer_epoch, producer_sequence, aggregate_type, aggregate_id, "
                    "operation, canonical_payload, canonical_payload_hash, envelope_hash, "
                    "schema_version, writer_epoch"
                    ") VALUES ("
                    ":event_id, 1, 'foreign', 'bot_fi', 1, :producer_sequence, "
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
        with self.engines["application"].begin() as connection:
            self._writer_settings(connection)
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
