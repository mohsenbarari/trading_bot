import json
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from core.legacy_direct_fi_ir_transport_fence import LegacyDirectFiIrTransportRetiredError
from core.sync_parity import build_table_parity_snapshot
from scripts.compare_sync_parity import _compare, _fetch_json, main


def write_snapshot(path: Path, table_name: str, rows: list[dict]) -> None:
    payload = {
        "status": "ok",
        "schema_version": 1,
        "mode": "quick",
        "tables": {
            table_name: build_table_parity_snapshot(table_name, rows),
        },
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


class SyncParityScriptTests(unittest.TestCase):
    def test_compare_returns_nonzero_for_business_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "local.json"
            peer = Path(tmp) / "peer.json"
            write_snapshot(local, "offers", [{"id": 1, "offer_public_id": "ofr_1", "price": 100}])
            write_snapshot(peer, "offers", [{"id": 1, "offer_public_id": "ofr_1", "price": 101}])

            with redirect_stdout(StringIO()):
                code = _compare(
                    SimpleNamespace(
                        local_snapshot=str(local),
                        peer_snapshot=str(peer),
                        local_url=None,
                        peer_url=None,
                        local_observability_key=None,
                        peer_observability_key=None,
                        sample_limit=5,
                        record_url=[],
                        record_observability_key=None,
                    )
                )

        self.assertEqual(code, 2)

    def test_compare_returns_nonzero_for_incomplete_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "local.json"
            peer = Path(tmp) / "peer.json"
            incomplete_payload = {
                "status": "ok",
                "schema_version": 1,
                "mode": "deep",
                "tables": {
                    "offers": build_table_parity_snapshot(
                        "offers",
                        [
                            {"id": 1, "offer_public_id": "ofr_1", "price": 100},
                            {"id": 2, "offer_public_id": "ofr_2", "price": 100},
                        ],
                        max_rows=1,
                    ),
                },
            }
            local.write_text(json.dumps(incomplete_payload, sort_keys=True), encoding="utf-8")
            write_snapshot(peer, "offers", [{"id": 1, "offer_public_id": "ofr_1", "price": 100}])

            stdout = StringIO()
            with redirect_stdout(stdout):
                code = _compare(
                    SimpleNamespace(
                        local_snapshot=str(local),
                        peer_snapshot=str(peer),
                        local_url=None,
                        peer_url=None,
                        local_observability_key=None,
                        peer_observability_key=None,
                        sample_limit=5,
                        record_url=[],
                        record_observability_key=None,
                    )
                )

        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "incomplete")

    def test_compare_adds_summary_and_can_publish_result(self):
        posted = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"status":"ok"}'

        def fake_urlopen(request, timeout):
            posted.append(
                {
                    "url": request.full_url,
                    "headers": dict(request.header_items()),
                    "body": json.loads(request.data.decode("utf-8")),
                    "timeout": timeout,
                }
            )
            return FakeResponse()

        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "local.json"
            peer = Path(tmp) / "peer.json"
            write_snapshot(local, "offers", [{"id": 1, "offer_public_id": "ofr_1", "price": 100}])
            write_snapshot(peer, "offers", [{"id": 1, "offer_public_id": "ofr_1", "price": 100}])

            stdout = StringIO()
            with unittest.mock.patch("scripts.compare_sync_parity.urllib.request.urlopen", side_effect=fake_urlopen):
                with redirect_stdout(stdout):
                    code = _compare(
                        SimpleNamespace(
                            local_snapshot=str(local),
                            peer_snapshot=str(peer),
                            local_url=None,
                            peer_url=None,
                            local_observability_key=None,
                            peer_observability_key=None,
                            sample_limit=5,
                            record_url=["http://127.0.0.1:8000/api/sync/parity/status"],
                            record_observability_key="obs-key",
                        )
                    )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["mode"], "quick")
        self.assertEqual(payload["summary"]["status"], "ok")
        self.assertEqual(posted[0]["url"], "http://127.0.0.1:8000/api/sync/parity/status")
        self.assertEqual(posted[0]["headers"]["X-observability-api-key"], "obs-key")
        self.assertEqual(posted[0]["body"]["status"], "ok")

    def test_remote_snapshot_url_is_fenced_before_any_request_or_snapshot_read(self):
        args = SimpleNamespace(
            local_snapshot="should-not-be-read.json",
            peer_snapshot=None,
            local_url=None,
            peer_url="https://wa-ir.example/api/sync/parity",
            local_observability_key=None,
            peer_observability_key=None,
            sample_limit=5,
            record_url=[],
            record_observability_key=None,
        )

        with unittest.mock.patch(
            "scripts.compare_sync_parity._read_json",
            side_effect=AssertionError("snapshot read"),
        ), unittest.mock.patch(
            "scripts.compare_sync_parity.urllib.request.urlopen",
            side_effect=AssertionError("network request"),
        ):
            with self.assertRaises(LegacyDirectFiIrTransportRetiredError):
                _compare(args)

    def test_remote_publication_url_is_fenced_before_request_creation(self):
        with unittest.mock.patch(
            "scripts.compare_sync_parity.urllib.request.urlopen",
            side_effect=AssertionError("network request"),
        ):
            with self.assertRaises(LegacyDirectFiIrTransportRetiredError):
                _fetch_json("https://wa-ir.example/api/sync/parity", None)

    def test_cli_emits_redacted_blocked_payload_for_remote_url(self):
        stdout = StringIO()
        argv = [
            "compare_sync_parity.py",
            "compare",
            "--local-url",
            "https://wa-fi.example/api/sync/parity",
            "--peer-snapshot",
            "not-read.json",
        ]
        with unittest.mock.patch("sys.argv", argv), unittest.mock.patch(
            "scripts.compare_sync_parity.urllib.request.urlopen",
            side_effect=AssertionError("network request"),
        ), redirect_stdout(stdout):
            code = main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "blocked_legacy_direct_fi_ir_transport_retired")
        self.assertEqual(payload["component"], "sync-parity-compare")

    def test_compare_adds_complete_artifact_metadata_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "local.json"
            peer = Path(tmp) / "peer.json"
            local_payload = {
                "status": "ok",
                "schema_version": 1,
                "mode": "deep",
                "server_mode": "foreign",
                "release_sha": "local-sha",
                "snapshot_at": "2026-06-28T04:59:00Z",
                "tables": {
                    "offers": build_table_parity_snapshot(
                        "offers",
                        [{"id": 1, "offer_public_id": "ofr_1", "price": 100}],
                    ),
                },
            }
            peer_payload = {
                "status": "ok",
                "schema_version": 1,
                "mode": "deep",
                "server_mode": "iran",
                "release_sha": "peer-sha",
                "snapshot_at": "2026-06-28T04:59:01Z",
                "tables": {
                    "offers": build_table_parity_snapshot(
                        "offers",
                        [{"id": 1, "offer_public_id": "ofr_1", "price": 100}],
                    ),
                },
            }
            local.write_text(json.dumps(local_payload, sort_keys=True), encoding="utf-8")
            peer.write_text(json.dumps(peer_payload, sort_keys=True), encoding="utf-8")

            stdout = StringIO()
            with redirect_stdout(stdout):
                code = _compare(
                    SimpleNamespace(
                        local_snapshot=str(local),
                        peer_snapshot=str(peer),
                        local_url=None,
                        peer_url=None,
                        local_observability_key=None,
                        peer_observability_key=None,
                        sample_limit=5,
                        record_url=[],
                        record_observability_key=None,
                        comparison_artifact_hash="sha256:comparison",
                        artifact_reference="tmp/parity/comparison.json",
                    )
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["summary"]["artifact_metadata_complete"])
        self.assertEqual(payload["artifact_metadata"]["local_server_mode"], "foreign")
        self.assertEqual(payload["artifact_metadata"]["peer_release_sha"], "peer-sha")
        self.assertEqual(payload["summary"]["artifact_metadata"]["artifact_reference"], "tmp/parity/comparison.json")


if __name__ == "__main__":
    unittest.main()
