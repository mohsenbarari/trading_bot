"""Opt-in real-PostgreSQL proof for the Bot-FI foreign-authority fence."""

from __future__ import annotations

import os
import json
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from core.dr_event_protocol import (
    V1_ENVELOPE_FIELDS,
    canonical_json_bytes,
    destination_transaction_hash,
    envelope_hash,
    sha256_json,
    transaction_hash_from_envelopes,
    validate_envelope,
)


URL_NAMES = {
    "owner": "BOT_FENCING_TEST_OWNER_URL",
    "application": "BOT_FENCING_TEST_APP_URL",
    "projection": "BOT_FENCING_TEST_PROJECTION_URL",
    "receiver": "BOT_FENCING_TEST_RECEIVER_URL",
    "delivery": "BOT_FENCING_TEST_DELIVERY_URL",
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

    def _foreign_writer(self, connection, *, epoch: int = 1) -> None:  # noqa: ANN001
        connection.execute(
            text("SELECT set_config('trading_bot.mutation_capability', 'foreign_writer', true)")
        )
        connection.execute(
            text("SELECT set_config('trading_bot.physical_site', 'bot_fi', true)")
        )
        connection.execute(
            text("SELECT set_config('trading_bot.dr_producer_epoch', :epoch, true)"),
            {"epoch": str(epoch)},
        )

    def _projection_scope(self, connection, scope: str) -> None:  # noqa: ANN001
        connection.execute(
            text("SELECT set_config('trading_bot.mutation_capability', 'projection', true)")
        )
        connection.execute(
            text("SELECT set_config('trading_bot.projection_scope', :scope, true)"),
            {"scope": scope},
        )

    def _coverage_event(
        self,
        connection,
        *,
        record_id: int,
        operation: str,
        payload: dict[str, object],
    ) -> None:  # noqa: ANN001
        event_id = str(uuid4())
        transaction_id = str(uuid4())
        sequence = int(
            connection.scalar(
                text(
                    "INSERT INTO dr_producer_cursors ("
                    "origin_authority,origin_physical_site,producer_epoch,last_sequence) "
                    "VALUES ('foreign','bot_fi',1,1) ON CONFLICT ("
                    "origin_authority,origin_physical_site,producer_epoch) DO UPDATE SET "
                    "last_sequence=dr_producer_cursors.last_sequence+1,updated_at=clock_timestamp() "
                    "RETURNING last_sequence"
                )
            )
        )
        streams = {}
        for site in ("webapp_fi", "webapp_ir"):
            destination_sequence = int(
                connection.scalar(
                    text(
                        "INSERT INTO dr_destination_cursors ("
                        "origin_authority,origin_physical_site,producer_epoch,destination_site,last_sequence) "
                        "VALUES ('foreign','bot_fi',1,:site,1) ON CONFLICT ("
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
        canonical_payload = json.loads(canonical_json_bytes(payload))
        created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        envelope = {
            "protocol_version": 2,
            "event_id": event_id,
            "origin_authority": "foreign",
            "origin_physical_site": "bot_fi",
            "producer_epoch": 1,
            "producer_sequence": sequence,
            "aggregate_type": "commodities",
            "aggregate_id": str(record_id),
            "aggregate_db_id": str(record_id),
            "aggregate_version": None,
            "operation": operation,
            "canonical_payload": canonical_payload,
            "canonical_payload_hash": sha256_json(canonical_payload),
            "schema_version": 1,
            "causation_id": None,
            "idempotency_key": None,
            "writer_epoch": None,
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
        validated = validate_envelope(envelope)
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
                ":event_id, 2, 'foreign', 'bot_fi', 1, :sequence, 'commodities', "
                ":record_id, :record_id, NULL, :operation, CAST(:payload AS JSONB), "
                ":payload_hash, :envelope_hash, 1, NULL, NULL, NULL, :tombstone, "
                "CAST(:created_at AS TIMESTAMPTZ), :transaction_id, 1, 1, "
                ":transaction_hash, CAST(:streams AS JSONB), txid_current())"
            ),
            {
                "event_id": event_id,
                "sequence": sequence,
                "record_id": str(record_id),
                "operation": operation,
                "tombstone": operation == "DELETE",
                "payload": canonical_json_bytes(canonical_payload).decode(),
                "payload_hash": envelope["canonical_payload_hash"],
                "envelope_hash": validated.envelope_hash,
                "created_at": created_at,
                "transaction_id": transaction_id,
                "transaction_hash": envelope["transaction_hash"],
                "streams": canonical_json_bytes(streams).decode(),
            },
        )

    def _finalize_with_tampered_destination_streams(
        self,
        connection,
        *,
        event_id: str,
        tamper: str,
    ) -> None:  # noqa: ANN001
        row = connection.execute(
            text("SELECT * FROM dr_events WHERE event_id=:event_id FOR UPDATE"),
            {"event_id": event_id},
        ).mappings().one()
        created_at = row["created_at"]
        if isinstance(created_at, datetime):
            created_at = created_at.astimezone(timezone.utc).isoformat()
        streams = json.loads(json.dumps(dict(row["destination_streams"] or {})))
        first_destination = sorted(streams)[0]
        if tamper == "sequence":
            streams[first_destination]["sequence"] += 1
        elif tamper == "remove_destination":
            streams.pop(first_destination)
        elif tamper == "extra_stream_field":
            streams[first_destination]["unexpected"] = "not-canonical"
        else:  # pragma: no cover - helper misuse guard
            raise AssertionError(f"unknown destination tamper: {tamper}")
        envelope = {
            key: row[key]
            for key in V1_ENVELOPE_FIELDS
            if key != "created_at"
        } | {
            "created_at": created_at,
            "transaction_id": row["transaction_id"],
            "transaction_position": int(row["transaction_position"]),
            "transaction_size": 1,
            "transaction_hash": "0" * 64,
            "destination_streams": streams,
        }
        group_hash = transaction_hash_from_envelopes([envelope])
        for destination, stream in streams.items():
            stream.update(transaction_position=1, transaction_size=1)
            stream["transaction_hash"] = destination_transaction_hash(
                [envelope], destination_site=destination
            )
        envelope["transaction_hash"] = group_hash
        connection.execute(
            text(
                "UPDATE dr_events SET transaction_size=1, "
                "transaction_hash=:transaction_hash, "
                "destination_streams=CAST(:destination_streams AS JSONB), "
                "envelope_hash=:envelope_hash WHERE event_id=:event_id"
            ),
            {
                "transaction_hash": group_hash,
                "destination_streams": canonical_json_bytes(streams).decode(),
                "envelope_hash": envelope_hash(envelope),
                "event_id": event_id,
            },
        )

    def _seed_projection_commodity(self, record_id: int) -> None:
        with self.engines["projection"].begin() as connection:
            self._projection_scope(connection, "projector")
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
                connection,
                record_id=with_capability,
                operation="INSERT",
                payload={"id": with_capability, "name": f"bot-authority-{with_capability}"},
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
        for role in ("application", "projection", "receiver", "delivery"):
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
                executable = set(
                    connection.execute(
                        text(
                            "SELECT procedure.proname FROM pg_proc procedure JOIN pg_namespace namespace "
                            "ON namespace.oid=procedure.pronamespace WHERE namespace.nspname='public' "
                            "AND has_function_privilege(current_user, procedure.oid, 'EXECUTE')"
                        )
                    ).scalars()
                )
                self.assertEqual(
                    executable,
                    {"trading_bot_cleanup_expired_replay_nonces"}
                    if role == "projection"
                    else set(),
                    role,
                )
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
                self._projection_scope(connection, "projector")
                connection.execute(
                    text(
                        "INSERT INTO push_subscriptions "
                        "(id,user_id,endpoint_hash,endpoint,p256dh,auth,enabled) "
                        "VALUES (:id,-1,repeat('a',64),'https://example.invalid','x','y',true)"
                    ),
                    {"id": record_id},
                )

    def test_bot_receiver_and_delivery_roles_cannot_impersonate_projector(self) -> None:
        for role, scope in (("receiver", "projector"), ("delivery", "receiver")):
            with self.subTest(role=role, scope=scope), self.assertRaises(DBAPIError):
                with self.engines[role].begin() as connection:
                    self._projection_scope(connection, scope)
                    connection.execute(
                        text("UPDATE commodities SET name='scope-bypass' WHERE id=-1")
                    )

    def test_bot_application_can_complete_local_queue_crud_but_not_webapp_private_dml(self) -> None:
        source_id = "scratch-queue-" + uuid4().hex
        with self.engines["application"].begin() as connection:
            self._foreign_writer(connection)
            job_id = connection.scalar(
                text(
                    "INSERT INTO telegram_delivery_jobs ("
                    "dedupe_key, feeder_kind, feeder_rank, source_natural_id, source_version, "
                    "action_kind, bot_identity, destination_key, destination_class, method, "
                    "payload, template_version, payload_hash, priority, priority_rank"
                    ") VALUES ("
                    ":dedupe_key, 'direct', 0, :source_id, 1, 'general_immediate', "
                    "'primary', :destination_key, 'private', 'sendMessage', "
                    "CAST(:payload AS JSON), 'scratch-v1', repeat('a',64), 0, 0"
                    ") RETURNING id"
                ),
                {
                    "dedupe_key": source_id,
                    "source_id": source_id,
                    "destination_key": "private:" + source_id,
                    "payload": json.dumps({"chat_id": 91, "text": source_id}),
                },
            )
            connection.execute(
                text(
                    "UPDATE telegram_delivery_jobs SET state='leased', worker_id='scratch', "
                    "lease_token=lease_token+1, lease_until=clock_timestamp()+interval '1 minute' "
                    "WHERE id=:job_id"
                ),
                {"job_id": job_id},
            )
            connection.execute(
                text("DELETE FROM telegram_delivery_jobs WHERE id=:job_id"),
                {"job_id": job_id},
            )

        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                self._foreign_writer(connection)
                connection.execute(
                    text(
                        "INSERT INTO push_subscriptions "
                        "(id,user_id,endpoint_hash,endpoint,p256dh,auth,enabled) "
                        "VALUES (:id,-1,repeat('b',64),'https://example.invalid','x','y',true)"
                    ),
                    {"id": self._scratch_id()},
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
                self._foreign_writer(connection, epoch=17)
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

    def test_each_cursor_increment_requires_its_own_event_and_destination_binding(self) -> None:
        record_id = self._scratch_id()
        payload = {"id": record_id, "name": f"cursor-gap-{record_id}"}
        with self.assertRaises(DBAPIError):
            with self.engines["application"].begin() as connection:
                self._foreign_writer(connection)
                connection.execute(
                    text(
                        "INSERT INTO dr_producer_cursors ("
                        "origin_authority,origin_physical_site,producer_epoch,last_sequence) "
                        "VALUES ('foreign','bot_fi',1,1) ON CONFLICT ("
                        "origin_authority,origin_physical_site,producer_epoch) DO UPDATE SET "
                        "last_sequence=dr_producer_cursors.last_sequence+1,"
                        "updated_at=clock_timestamp()"
                    )
                )
                for site in ("webapp_fi", "webapp_ir"):
                    connection.execute(
                        text(
                            "INSERT INTO dr_destination_cursors ("
                            "origin_authority,origin_physical_site,producer_epoch,"
                            "destination_site,last_sequence) VALUES ("
                            "'foreign','bot_fi',1,:site,1) ON CONFLICT ("
                            "origin_authority,origin_physical_site,producer_epoch,"
                            "destination_site) DO UPDATE SET "
                            "last_sequence=dr_destination_cursors.last_sequence+1,"
                            "updated_at=clock_timestamp()"
                        ),
                        {"site": site},
                    )
                # A second allocation with only its final event used to make
                # the deferred max-tail check accept the missing first event.
                self._coverage_event(
                    connection,
                    record_id=record_id,
                    operation="INSERT",
                    payload=payload,
                )
                connection.execute(
                    text("INSERT INTO commodities (id, name) VALUES (:id, :name)"),
                    payload,
                )

    def test_finalization_cannot_rewrite_or_extend_allocated_destination_streams(self) -> None:
        from core.dr_event_protocol import append_local_dr_event
        from core.config import settings

        for tamper in ("sequence", "remove_destination", "extra_stream_field"):
            with self.subTest(tamper=tamper), self.assertRaises(DBAPIError):
                with patch.multiple(
                    settings,
                    three_site_dr_enabled=True,
                    dr_event_protocol_enabled=True,
                    dr_event_protocol_strict=True,
                    topology_schema_version="three-site-dr-v1",
                    logical_authority="foreign",
                    physical_site="bot_fi",
                    server_mode="foreign",
                    dr_producer_epoch=31,
                ):
                    with self.engines["application"].begin() as connection:
                        self._foreign_writer(connection, epoch=31)
                        record_id = self._scratch_id()
                        payload = {
                            "id": record_id,
                            "name": f"finalize-{tamper}-{record_id}",
                        }
                        event_id = append_local_dr_event(
                            connection,
                            table_name="commodities",
                            record_id=record_id,
                            operation="INSERT",
                            data=payload,
                            change_log_id=None,
                            transaction_id=str(uuid4()),
                            transaction_position=1,
                        )
                        connection.execute(
                            text("INSERT INTO commodities (id, name) VALUES (:id, :name)"),
                            payload,
                        )
                        self._finalize_with_tampered_destination_streams(
                            connection,
                            event_id=str(event_id),
                            tamper=tamper,
                        )

    def test_two_valid_events_can_share_one_transaction_without_sequence_gaps(self) -> None:
        from core.dr_event_protocol import append_local_dr_event, finalize_local_dr_transaction
        from core.config import settings

        transaction_id = str(uuid4())
        event_ids: list[str] = []
        with patch.multiple(
            settings,
            three_site_dr_enabled=True,
            dr_event_protocol_enabled=True,
            dr_event_protocol_strict=True,
            topology_schema_version="three-site-dr-v1",
            logical_authority="foreign",
            physical_site="bot_fi",
            server_mode="foreign",
            dr_producer_epoch=32,
        ):
            with self.engines["application"].begin() as connection:
                self._foreign_writer(connection, epoch=32)
                for position in (1, 2):
                    record_id = self._scratch_id()
                    payload = {
                        "id": record_id,
                        "name": f"valid-multi-{position}-{record_id}",
                    }
                    event_id = append_local_dr_event(
                        connection,
                        table_name="commodities",
                        record_id=record_id,
                        operation="INSERT",
                        data=payload,
                        change_log_id=None,
                        transaction_id=transaction_id,
                        transaction_position=position,
                    )
                    event_ids.append(str(event_id))
                    connection.execute(
                        text("INSERT INTO commodities (id, name) VALUES (:id, :name)"),
                        payload,
                    )
                finalize_local_dr_transaction(connection, event_ids)

        with self.engines["owner"].connect() as connection:
            sequences = list(
                connection.execute(
                    text(
                        "SELECT producer_sequence FROM dr_events "
                        "WHERE event_id = ANY(:event_ids) ORDER BY producer_sequence"
                    ),
                    {"event_ids": event_ids},
                ).scalars()
            )
        self.assertEqual(len(sequences), 2)
        self.assertEqual(sequences[1], sequences[0] + 1)


if __name__ == "__main__":
    unittest.main()
