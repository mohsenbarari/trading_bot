import unittest

from core.sync_metadata import build_sync_metadata, deserialize_sync_data


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

    def test_deserialize_sync_data_returns_non_json_string_unchanged(self):
        self.assertEqual(deserialize_sync_data('{"id": 1}'), {"id": 1})
        self.assertEqual(deserialize_sync_data("not-json"), "not-json")
