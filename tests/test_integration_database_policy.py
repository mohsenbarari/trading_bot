"""Cross-feature database policy invariants for the integrated topology."""

from __future__ import annotations

import unittest

from core.dr_event_receiver import _event_values
from core.dr_event_protocol import sha256_json, validate_envelope
from core.dr_database_roles import PROJECTION_SERVICE_SCOPES, projection_scope_for_service
from scripts.activate_three_site_database_fencing import (
    BOT_LOCAL_EXECUTION_TABLES,
    DR_SERVICE_INTERNAL_GRANTS,
)
from scripts.provision_bot_database_roles import (
    BOT_DR_SERVICE_GRANTS,
    BOT_LOCAL_QUEUE_APPLICATION_GRANTS,
)


EXPECTED_BOT_LOCAL_EXECUTION_TABLES = frozenset(
    {
        "telegram_delivery_jobs",
        "telegram_delivery_provider_outcomes",
        "telegram_delivery_reconciliation_evidence",
        "telegram_delivery_runtime_gates",
        "telegram_delivery_resume_operations",
        "telegram_delivery_feeder_states",
        "telegram_scheduled_operations",
        "telegram_interaction_anchor_states",
        "telegram_channel_membership_sagas",
    }
)


class IntegrationDatabasePolicyTests(unittest.TestCase):
    def test_private_dr_processes_have_closed_distinct_database_scopes(self):
        self.assertEqual(
            set(PROJECTION_SERVICE_SCOPES.values()),
            {"receiver", "delivery", "projector", "blob", "effect"},
        )
        self.assertEqual(set(DR_SERVICE_INTERNAL_GRANTS), set(PROJECTION_SERVICE_SCOPES.values()))
        self.assertEqual(set(BOT_DR_SERVICE_GRANTS), {"receiver", "delivery", "projector"})
        self.assertEqual(projection_scope_for_service("dr_receiver"), "receiver")
        with self.assertRaises(RuntimeError):
            projection_scope_for_service("api")

    def test_bot_queue_grants_and_webapp_deny_set_are_the_same_closed_surface(self):
        self.assertEqual(
            frozenset(BOT_LOCAL_QUEUE_APPLICATION_GRANTS),
            EXPECTED_BOT_LOCAL_EXECUTION_TABLES,
        )
        self.assertEqual(BOT_LOCAL_EXECUTION_TABLES, EXPECTED_BOT_LOCAL_EXECUTION_TABLES)
        self.assertTrue(
            all(
                permissions == "SELECT, INSERT, UPDATE, DELETE"
                for permissions in BOT_LOCAL_QUEUE_APPLICATION_GRANTS.values()
            )
        )

    def test_remote_event_insert_omits_source_local_xid(self):
        payload = {
            "protocol_version": 2,
            "event_id": "00000000-0000-4000-8000-000000000001",
            "origin_authority": "foreign",
            "origin_physical_site": "bot_fi",
            "producer_epoch": 1,
            "producer_sequence": 1,
            "aggregate_type": "commodities",
            "aggregate_id": "1",
            "aggregate_db_id": "1",
            "aggregate_version": 1,
            "operation": "INSERT",
            "canonical_payload": {"id": 1, "name": "gold"},
            "canonical_payload_hash": sha256_json({"id": 1, "name": "gold"}),
            "schema_version": 1,
            "causation_id": None,
            "idempotency_key": None,
            "writer_epoch": None,
            "tombstone": False,
            "created_at": "2026-07-20T00:00:00+00:00",
            "transaction_id": "00000000-0000-4000-8000-000000000002",
            "transaction_position": 1,
            "transaction_size": 1,
            "transaction_hash": "4" * 64,
            "destination_streams": {
                "webapp_fi": {
                    "sequence": 1,
                    "transaction_id": "00000000-0000-4000-8000-000000000002",
                    "transaction_position": 1,
                    "transaction_size": 1,
                    "transaction_hash": "4" * 64,
                },
                "webapp_ir": {
                    "sequence": 1,
                    "transaction_id": "00000000-0000-4000-8000-000000000002",
                    "transaction_position": 1,
                    "transaction_size": 1,
                    "transaction_hash": "4" * 64,
                },
            },
        }
        values = _event_values(validate_envelope(payload))
        self.assertNotIn("source_xid", values)


if __name__ == "__main__":
    unittest.main()
