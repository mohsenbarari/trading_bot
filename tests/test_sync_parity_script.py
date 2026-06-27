import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from core.sync_parity import build_table_parity_snapshot
from scripts.compare_sync_parity import _compare


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
                    )
                )

        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
