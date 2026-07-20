from __future__ import annotations

from pathlib import Path
import unittest

from core.dr_delivery_worker import parse_peer_urls
from core.dr_sync_auth import parse_pairwise_keys


ROOT = Path(__file__).resolve().parents[1]


def _values() -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in (
        ROOT / "deploy/staging/env.three-site.staging.example"
    ).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or name in result:
            raise AssertionError(f"invalid/duplicate staging env entry: {name}")
        result[name] = value
    return result


class ThreeSiteStagingEnvExampleTests(unittest.TestCase):
    def test_dr_peer_and_pairwise_key_shapes_match_runtime_parsers(self):
        values = _values()
        expected_peers = {
            "bot_fi": {"webapp_fi"},
            "webapp_fi": {"bot_fi", "webapp_ir"},
            "webapp_ir": {"webapp_fi"},
        }
        prefixes = {
            "bot_fi": "BOT_FI",
            "webapp_fi": "WEBAPP_FI",
            "webapp_ir": "WEBAPP_IR",
        }
        for site, expected in expected_peers.items():
            prefix = prefixes[site]
            self.assertEqual(
                set(parse_peer_urls(values[f"{prefix}_DR_PEERS_JSON"], local_site=site)),
                expected,
            )
            keys = parse_pairwise_keys(values[f"{prefix}_DR_PAIRWISE_KEYS_JSON"])
            observed_peers = {
                key.destination_site if key.source_site == site else key.source_site
                for key in keys.values()
            }
            self.assertEqual(observed_peers, expected)

    def test_initial_queue_mode_and_object_prefix_are_fail_closed(self):
        values = _values()
        self.assertEqual(values["TELEGRAM_DELIVERY_EXECUTION_OWNER"], "legacy")
        self.assertEqual(values["TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED"], "false")
        self.assertEqual(values["TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY"], "false")
        self.assertTrue(values["DR_BLOB_OBJECT_PREFIX"].startswith("staging/"))


if __name__ == "__main__":
    unittest.main()
