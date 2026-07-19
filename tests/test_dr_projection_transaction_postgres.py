"""Opt-in PostgreSQL proof that one destination transaction is applied atomically."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text

from core.dr_event_protocol import (
    destination_transaction_hash,
    sha256_json,
    transaction_hash_from_envelopes,
    validate_envelope,
)


@unittest.skipUnless(
    os.environ.get("DR_PROJECTION_TX_TEST_OWNER_URL")
    and os.environ.get("DR_PROJECTION_TX_TEST_PROJECTION_URL"),
    "scratch DR projection transaction URLs are not configured",
)
class DrProjectionTransactionPostgresTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner = create_engine(os.environ["DR_PROJECTION_TX_TEST_OWNER_URL"])
        cls.projection = create_engine(os.environ["DR_PROJECTION_TX_TEST_PROJECTION_URL"])
        with cls.owner.connect() as connection:
            database_name = str(connection.scalar(text("SELECT current_database()")))
            if not database_name.startswith("stage4_registration_"):
                raise RuntimeError("DR projection test requires a guarded scratch database")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.owner.dispose()
        cls.projection.dispose()

    def _envelopes(self, *, epoch: int, first_id: int, second_id: int):
        transaction_id = str(uuid4())
        envelopes = []
        for position, record_id in enumerate((first_id, second_id), 1):
            payload = {"id": record_id, "name": f"atomic-projection-{record_id}"}
            envelopes.append(
                {
                    "protocol_version": 2,
                    "event_id": str(uuid4()),
                    "origin_authority": "webapp",
                    "origin_physical_site": "webapp_fi",
                    "producer_epoch": epoch,
                    "producer_sequence": 100 + position,
                    "aggregate_type": "commodities",
                    "aggregate_id": str(record_id),
                    "aggregate_db_id": str(record_id),
                    "aggregate_version": None,
                    "operation": "INSERT",
                    "canonical_payload": payload,
                    "canonical_payload_hash": sha256_json(payload),
                    "schema_version": 1,
                    "causation_id": None,
                    "idempotency_key": None,
                    "writer_epoch": epoch,
                    "tombstone": False,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "transaction_id": transaction_id,
                    "transaction_position": position,
                    "transaction_size": 2,
                    "transaction_hash": "0" * 64,
                    "destination_streams": {
                        "bot_fi": {
                            "sequence": position,
                            "transaction_id": transaction_id,
                            "transaction_position": position,
                            "transaction_size": 2,
                            "transaction_hash": "0" * 64,
                        }
                    },
                }
            )
        global_hash = transaction_hash_from_envelopes(envelopes)
        destination_hash = destination_transaction_hash(
            envelopes, destination_site="bot_fi"
        )
        for envelope in envelopes:
            envelope["transaction_hash"] = global_hash
            envelope["destination_streams"]["bot_fi"]["transaction_hash"] = destination_hash
        return [validate_envelope(envelope) for envelope in envelopes]

    async def test_incomplete_group_is_invisible_then_both_members_apply_in_one_commit(self) -> None:
        from core.config import settings
        from core.dr_projection_worker import apply_next_dr_projection

        epoch = 100_000 + uuid4().int % 100_000
        first_id = 700_000_000 + uuid4().int % 10_000_000
        second_id = 710_000_000 + uuid4().int % 10_000_000
        validated = self._envelopes(epoch=epoch, first_id=first_id, second_id=second_id)
        with self.projection.begin() as connection:
            connection.execute(
                text("SELECT set_config('trading_bot.mutation_capability', 'projection', true)")
            )
            # Keep this scratch test repeatable after an interrupted prior run.
            connection.execute(
                text(
                    "UPDATE dr_stream_checkpoints SET contiguous_applied_sequence="
                    "contiguous_received_sequence WHERE contiguous_applied_sequence < "
                    "contiguous_received_sequence"
                )
            )
            for index, item in enumerate(validated):
                payload = item.payload
                connection.execute(
                    text(
                        "INSERT INTO dr_events ("
                        "event_id,protocol_version,origin_authority,origin_physical_site,"
                        "producer_epoch,producer_sequence,aggregate_type,aggregate_id,"
                        "aggregate_db_id,aggregate_version,operation,canonical_payload,"
                        "canonical_payload_hash,envelope_hash,schema_version,causation_id,"
                        "idempotency_key,writer_epoch,tombstone,created_at,transaction_id,"
                        "transaction_position,transaction_size,transaction_hash,destination_streams"
                        ") VALUES ("
                        ":event_id,:protocol_version,:origin_authority,:origin_physical_site,"
                        ":producer_epoch,:producer_sequence,:aggregate_type,:aggregate_id,"
                        ":aggregate_db_id,:aggregate_version,:operation,CAST(:canonical_payload AS JSONB),"
                        ":canonical_payload_hash,:envelope_hash,:schema_version,:causation_id,"
                        ":idempotency_key,:writer_epoch,:tombstone,CAST(:created_at AS TIMESTAMPTZ),"
                        ":transaction_id,:transaction_position,:transaction_size,:transaction_hash,"
                        "CAST(:destination_streams AS JSONB))"
                    ),
                    {
                        **{key: value for key, value in payload.items() if key not in {"canonical_payload", "destination_streams"}},
                        "canonical_payload": json.dumps(payload["canonical_payload"], sort_keys=True),
                        "destination_streams": json.dumps(payload["destination_streams"], sort_keys=True),
                        "envelope_hash": item.envelope_hash,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO dr_event_receipts ("
                        "event_id,destination_site,origin_physical_site,producer_epoch,"
                        "producer_sequence,envelope_hash,received_from_site,status) VALUES ("
                        ":event_id,'bot_fi','webapp_fi',:epoch,:sequence,:hash,'webapp_fi',:status)"
                    ),
                    {
                        "event_id": payload["event_id"],
                        "epoch": epoch,
                        "sequence": index + 1,
                        "hash": item.envelope_hash,
                        "status": "received" if index == 0 else "blocked_gap",
                    },
                )
            connection.execute(
                text(
                    "INSERT INTO dr_stream_checkpoints ("
                    "destination_site,origin_physical_site,producer_epoch,"
                    "contiguous_received_sequence,contiguous_applied_sequence) "
                    "VALUES ('bot_fi','webapp_fi',:epoch,1,0)"
                ),
                {"epoch": epoch},
            )

        with patch.multiple(
            settings,
            three_site_dr_enabled=True,
            dr_event_protocol_enabled=True,
            dr_event_protocol_strict=True,
            topology_schema_version="three-site-dr-v1",
            logical_authority="foreign",
            physical_site="bot_fi",
            server_mode="foreign",
        ):
            self.assertEqual(await apply_next_dr_projection(), "deferred")
            with self.owner.connect() as connection:
                count = int(
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM commodities WHERE name IN (:first,:second)"
                        ),
                        {
                            "first": f"atomic-projection-{first_id}",
                            "second": f"atomic-projection-{second_id}",
                        },
                    )
                    or 0
                )
            self.assertEqual(count, 0)

            with self.projection.begin() as connection:
                connection.execute(
                    text("SELECT set_config('trading_bot.mutation_capability', 'projection', true)")
                )
                connection.execute(
                    text(
                        "UPDATE dr_event_receipts SET status='received' "
                        "WHERE destination_site='bot_fi' AND origin_physical_site='webapp_fi' "
                        "AND producer_epoch=:epoch AND producer_sequence=2"
                    ),
                    {"epoch": epoch},
                )
                connection.execute(
                    text(
                        "UPDATE dr_stream_checkpoints SET contiguous_received_sequence=2 "
                        "WHERE destination_site='bot_fi' AND origin_physical_site='webapp_fi' "
                        "AND producer_epoch=:epoch"
                    ),
                    {"epoch": epoch},
                )
            self.assertEqual(await apply_next_dr_projection(), "applied")

        with self.owner.connect() as connection:
            count = int(
                connection.scalar(
                    text("SELECT count(*) FROM commodities WHERE name IN (:first,:second)"),
                    {
                        "first": f"atomic-projection-{first_id}",
                        "second": f"atomic-projection-{second_id}",
                    },
                )
                or 0
            )
            checkpoint = int(
                connection.scalar(
                    text(
                        "SELECT contiguous_applied_sequence FROM dr_stream_checkpoints "
                        "WHERE destination_site='bot_fi' AND origin_physical_site='webapp_fi' "
                        "AND producer_epoch=:epoch"
                    ),
                    {"epoch": epoch},
                )
            )
        self.assertEqual(count, 2)
        self.assertEqual(checkpoint, 2)


if __name__ == "__main__":
    unittest.main()
