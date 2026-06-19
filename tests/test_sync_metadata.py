import unittest

from core.sync_metadata import build_sync_metadata, build_sync_public_identity, deserialize_sync_data
from core.sync_protocol import (
    SYNC_PROTOCOL_VERSION,
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

    def test_sync_protocol_metadata_carries_current_registry_contract(self):
        metadata = build_sync_protocol_metadata(producer_server="foreign")

        self.assertEqual(metadata["protocol_version"], SYNC_PROTOCOL_VERSION)
        self.assertEqual(metadata["payload_schema_version"], 2)
        self.assertEqual(metadata["registry_version"], 2)
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
