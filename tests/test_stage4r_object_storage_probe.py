from __future__ import annotations

from pathlib import Path
import unittest

from scripts.run_stage4r_object_storage_probe import (
    PROBE_SCHEMA,
    Stage4rObjectProbeError,
    _probe_blob_bytes,
    _require_applied_results,
    _run_id,
    _stage_probe_blob_delivery,
)


class Stage4rObjectStorageProbeTests(unittest.TestCase):
    def test_probe_bootstraps_repository_root_for_direct_file_execution(self):
        source = Path("scripts/run_stage4r_object_storage_probe.py").read_text(encoding="utf-8")
        self.assertIn("REPO_ROOT = Path(__file__).resolve().parents[1]", source)
        self.assertIn("sys.path.insert(0, str(REPO_ROOT))", source)

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

    def test_blob_probe_flushes_manifest_before_adding_delivery(self):
        calls: list[str] = []

        class RecordingSession:
            def add(self, value):  # noqa: ANN001
                calls.append(type(value).__name__)

            async def flush(self):
                calls.append("flush")

        import asyncio

        asyncio.run(
            _stage_probe_blob_delivery(
                RecordingSession(),
                content_hash="a" * 64,
                size_bytes=1,
                local_path="/tmp/stage4r-probe",
            )
        )
        self.assertEqual(calls, ["DrBlobManifest", "flush", "DrBlobDelivery"])


if __name__ == "__main__":
    unittest.main()
