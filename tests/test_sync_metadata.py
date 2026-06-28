import unittest

from core.sync_metadata import build_sync_metadata, build_sync_public_identity, deserialize_sync_data
from core.sync_protocol import (
    SYNC_PROTOCOL_VERSION,
    SYNC_REGISTRY_VERSION,
    build_sync_protocol_metadata,
    current_sync_registry_fingerprint,
    validate_sync_protocol_metadata,
)


class SyncMetadataTests(unittest.TestCase):
    def test_offer_metadata_uses_public_identity_authority_and_version(self):
        metadata = build_sync_metadata(
            "offers",
            12,
            "UPDATE",
            {
                "offer_public_id": "ofr_12",
                "home_server": "iran",
                "version_id": 4,
                "idempotency_key": "offer-expire:12",
            },
            change_log_id=77,
        )

        self.assertEqual(
            metadata,
            {
                "aggregate_table": "offers",
                "aggregate_id": "ofr_12",
                "aggregate_db_id": 12,
                "source_server": "foreign",
                "source_sequence": 77,
                "authority_server": "iran",
                "operation": "UPDATE",
                "authoritative_version": 4,
                "event_sequence": 4,
                "outbox_id": 77,
                "command_idempotency_id": "offer-expire:12",
            },
        )

    def test_metadata_falls_back_to_outbox_sequence_for_unversioned_tables(self):
        metadata = build_sync_metadata(
            "users",
            5,
            "INSERT",
            {"full_name": "User 5"},
            change_log_id=91,
        )

        self.assertEqual(metadata["aggregate_id"], "5")
        self.assertIsNone(metadata["authoritative_version"])
        self.assertEqual(metadata["source_server"], "foreign")
        self.assertEqual(metadata["source_sequence"], 91)
        self.assertEqual(metadata["event_sequence"], 91)
        self.assertEqual(metadata["outbox_id"], 91)

    def test_publication_state_metadata_uses_dedupe_key_and_owner_server(self):
        metadata = build_sync_metadata(
            "offer_publication_states",
            44,
            "UPDATE",
            {
                "dedupe_key": "offer-publication:telegram_channel:ofr_8",
                "offer_public_id": "ofr_8",
                "surface": "telegram_channel",
                "publication_owner_server": "foreign",
                "version_id": 3,
            },
            change_log_id=101,
        )

        self.assertEqual(metadata["aggregate_id"], "offer-publication:telegram_channel:ofr_8")
        self.assertEqual(metadata["authority_server"], "foreign")
        self.assertEqual(metadata["authoritative_version"], 3)

    def test_trade_delivery_receipt_metadata_uses_dedupe_key_and_destination_server(self):
        metadata = build_sync_metadata(
            "trade_delivery_receipts",
            55,
            "UPDATE",
            {
                "dedupe_key": "trade_completed:webapp:10025:7",
                "event_type": "trade_completed",
                "trade_number": 10025,
                "recipient_user_id": 7,
                "channel": "webapp",
                "destination_server": "iran",
            },
            change_log_id=111,
        )

        self.assertEqual(metadata["aggregate_id"], "trade_completed:webapp:10025:7")
        self.assertEqual(metadata["authority_server"], "iran")
        self.assertEqual(
            build_sync_public_identity(
                "trade_delivery_receipts",
                55,
                {
                    "dedupe_key": "trade_completed:telegram:10025:8",
                    "trade_number": 10025,
                },
            ),
            {
                "table": "trade_delivery_receipts",
                "kind": "dedupe_key",
                "value": "trade_completed:telegram:10025:8",
                "record_id": 55,
                "references": {"trade_number": "10025"},
            },
        )

    def test_user_block_metadata_uses_pair_identity(self):
        metadata = build_sync_metadata(
            "user_blocks",
            7,
            "DELETE",
            {
                "id": 7,
                "blocker_id": 11,
                "blocked_id": 12,
            },
            change_log_id=113,
            source_server="iran",
        )

        self.assertEqual(metadata["aggregate_id"], "11:12")
        self.assertEqual(metadata["aggregate_db_id"], 7)
        self.assertEqual(metadata["source_server"], "iran")
        self.assertEqual(metadata["source_sequence"], 113)

    def test_offer_request_metadata_uses_request_home_and_idempotency_not_offer_public_id(self):
        first = build_sync_metadata(
            "offer_requests",
            31,
            "INSERT",
            {
                "offer_public_id": "ofr_shared",
                "request_home_server": "foreign",
                "idempotency_key": "request-a",
            },
            change_log_id=201,
        )
        second = build_sync_metadata(
            "offer_requests",
            32,
            "INSERT",
            {
                "offer_public_id": "ofr_shared",
                "request_home_server": "foreign",
                "idempotency_key": "request-b",
            },
            change_log_id=202,
        )

        self.assertEqual(first["aggregate_table"], "offer_requests")
        self.assertEqual(first["aggregate_id"], "foreign:request-a")
        self.assertEqual(first["authority_server"], "foreign")
        self.assertEqual(second["aggregate_id"], "foreign:request-b")
        self.assertNotEqual(first["aggregate_id"], second["aggregate_id"])

    def test_offer_request_metadata_falls_back_to_record_id_for_legacy_payload_without_idempotency(self):
        metadata = build_sync_metadata(
            "offer_requests",
            33,
            "INSERT",
            {"offer_public_id": "ofr_legacy"},
            change_log_id=203,
        )

        self.assertEqual(metadata["aggregate_id"], "33")

    def test_trading_settings_metadata_uses_setting_key_not_record_id_zero(self):
        open_time = build_sync_metadata(
            "trading_settings",
            0,
            "UPDATE",
            {"key": "market_open_time_local", "value": "10:00"},
            change_log_id=301,
        )
        close_time = build_sync_metadata(
            "trading_settings",
            0,
            "UPDATE",
            {"key": "market_close_time_local", "value": "17:00"},
            change_log_id=302,
        )

        self.assertEqual(open_time["aggregate_table"], "trading_settings")
        self.assertEqual(open_time["aggregate_id"], "market_open_time_local")
        self.assertEqual(open_time["aggregate_db_id"], 0)
        self.assertEqual(close_time["aggregate_id"], "market_close_time_local")
        self.assertNotEqual(open_time["aggregate_id"], close_time["aggregate_id"])

    def test_natural_key_metadata_uses_logical_identity_for_id_drift_tables(self):
        relation = build_sync_metadata(
            "customer_relations",
            23,
            "DELETE",
            {"invitation_token": "cust-token-23", "owner_user_id": 2},
            change_log_id=401,
        )
        commodity = build_sync_metadata(
            "commodities",
            14,
            "DELETE",
            {"name": "gold-main"},
            change_log_id=402,
        )
        notification = build_sync_metadata(
            "notifications",
            19,
            "DELETE",
            {"dedupe_key": "trade_completed:webapp:10019:1"},
            change_log_id=403,
        )

        self.assertEqual(relation["aggregate_id"], "cust-token-23")
        self.assertEqual(relation["aggregate_db_id"], 23)
        self.assertEqual(commodity["aggregate_id"], "gold-main")
        self.assertEqual(notification["aggregate_id"], "trade_completed:webapp:10019:1")

    def test_public_identity_payloads_prefer_stable_cross_server_keys(self):
        self.assertEqual(
            build_sync_public_identity("offers", 12, {"offer_public_id": "ofr_12"}),
            {
                "table": "offers",
                "kind": "offer_public_id",
                "value": "ofr_12",
                "record_id": 12,
            },
        )
        self.assertEqual(
            build_sync_public_identity(
                "trades",
                15,
                {"trade_number": 10015, "offer_public_id": "ofr_12"},
            ),
            {
                "table": "trades",
                "kind": "trade_number",
                "value": "10015",
                "record_id": 15,
                "references": {"offer_public_id": "ofr_12"},
            },
        )
        self.assertEqual(
            build_sync_public_identity(
                "offer_publication_states",
                9,
                {
                    "dedupe_key": "offer-publication:webapp_market:ofr_12",
                    "offer_public_id": "ofr_12",
                },
            )["value"],
            "offer-publication:webapp_market:ofr_12",
        )
        self.assertEqual(
            build_sync_public_identity(
                "customer_relations",
                23,
                {"invitation_token": "cust-token-23"},
            ),
            {
                "table": "customer_relations",
                "kind": "invitation_token",
                "value": "cust-token-23",
                "record_id": 23,
            },
        )

    def test_sync_protocol_metadata_carries_current_registry_contract(self):
        metadata = build_sync_protocol_metadata(producer_server="foreign")

        self.assertEqual(metadata["protocol_version"], SYNC_PROTOCOL_VERSION)
        self.assertEqual(metadata["payload_schema_version"], 2)
        self.assertEqual(metadata["registry_version"], SYNC_REGISTRY_VERSION)
        self.assertEqual(metadata["registry_fingerprint"], current_sync_registry_fingerprint())
        self.assertEqual(metadata["producer"], {"server_mode": "foreign"})
        self.assertTrue(validate_sync_protocol_metadata(metadata).ok)

    def test_sync_protocol_validation_rejects_unsupported_future_version(self):
        metadata = build_sync_protocol_metadata(producer_server="foreign")
        metadata["protocol_version"] = SYNC_PROTOCOL_VERSION + 1

        result = validate_sync_protocol_metadata(metadata)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "unsupported_protocol_version")
        self.assertEqual(result.details["producer_protocol_version"], SYNC_PROTOCOL_VERSION + 1)

    def test_sync_protocol_validation_rejects_future_payload_schema_version(self):
        metadata = build_sync_protocol_metadata(producer_server="foreign")
        metadata["payload_schema_version"] = 99

        result = validate_sync_protocol_metadata(metadata)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "unsupported_payload_schema_version")
        self.assertEqual(result.details["producer_payload_schema_version"], 99)

    def test_sync_protocol_validation_rejects_current_registry_fingerprint_mismatch(self):
        metadata = build_sync_protocol_metadata(producer_server="foreign")
        metadata["registry_fingerprint"] = "different-reg"

        result = validate_sync_protocol_metadata(metadata)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "registry_fingerprint_mismatch")
        self.assertEqual(result.details["producer_registry_fingerprint"], "different-reg")
        self.assertEqual(result.details["local_registry_fingerprint"], current_sync_registry_fingerprint())

    def test_sync_protocol_validation_rejects_newer_required_registry_version(self):
        metadata = build_sync_protocol_metadata(producer_server="foreign")
        metadata["min_consumer_registry_version"] = 99

        result = validate_sync_protocol_metadata(metadata)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "producer_requires_newer_registry")
        self.assertEqual(result.details["required_consumer_registry_version"], 99)

    def test_sync_protocol_validation_accepts_declared_legacy_compatible_payload(self):
        metadata = {
            "protocol_version": 1,
            "min_consumer_protocol_version": 1,
            "payload_schema_version": 1,
            "registry_version": 1,
            "registry_fingerprint": "legacy-registry",
            "producer": {"server_mode": "iran"},
        }

        result = validate_sync_protocol_metadata(metadata)

        self.assertTrue(result.ok)

    def test_deserialize_sync_data_returns_non_json_string_unchanged(self):
        self.assertEqual(deserialize_sync_data('{"id": 1}'), {"id": 1})
        self.assertEqual(deserialize_sync_data("not-json"), "not-json")
