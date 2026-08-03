from __future__ import annotations

import unittest

from scripts.run_stage4r_object_storage_probe import (
    PROBE_SCHEMA,
    Stage4rObjectProbeError,
    _probe_blob_bytes,
    _require_applied_results,
    _run_id,
)


class Stage4rObjectStorageProbeTests(unittest.TestCase):
    def test_probe_blob_bytes_are_canonical_non_business_marker(self):
        payload = _probe_blob_bytes(run_id="11111111-1111-4111-8111-111111111111", release_sha="a" * 40)
        self.assertEqual(
            payload,
            b'{"kind":"non_business_blob_readback","release_sha":"'
            + (b"a" * 40)
            + b'","run_id":"11111111-1111-4111-8111-111111111111","schema":"'
            + PROBE_SCHEMA.encode("utf-8")
            + b'"}',
        )

    def test_run_id_is_uuid_and_canonicalized(self):
        self.assertEqual(_run_id("11111111-1111-4111-8111-111111111111"), "11111111-1111-4111-8111-111111111111")
        with self.assertRaises(Stage4rObjectProbeError):
            _run_id("not-a-run-id")

    def test_event_replay_requires_exactly_applied_existing_events(self):
        event_ids = ("event-a", "event-b")
        _require_applied_results(
            {
                "results": [
                    {"event_id": "event-a", "status": "applied"},
                    {"event_id": "event-b", "status": "applied"},
                ]
            },
            event_ids,
        )
        with self.assertRaises(Stage4rObjectProbeError):
            _require_applied_results(
                {"results": [{"event_id": "event-a", "status": "received"}]},
                event_ids,
            )


if __name__ == "__main__":
    unittest.main()
