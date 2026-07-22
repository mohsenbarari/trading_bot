from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from scripts.verify_integration_migration_histories import (
    HISTORY_LABELS,
    MigrationHistoryError,
    parse_history_urls,
    verify_histories,
)


def _urls() -> dict[str, str]:
    return {
        label: f"postgresql://scratch/{label}"
        for label in HISTORY_LABELS
    }


class IntegrationMigrationHistoryTests(unittest.TestCase):
    def test_parser_requires_exact_closed_history_set(self):
        payload = [
            {"label": label, "database_url": url}
            for label, url in sorted(_urls().items())
        ]
        self.assertEqual(parse_history_urls(json.dumps(payload)), _urls())
        payload.pop()
        with self.assertRaises(MigrationHistoryError):
            parse_history_urls(json.dumps(payload))

    def test_equivalent_contracts_pass_and_one_drift_fails(self):
        with patch(
            "scripts.verify_integration_migration_histories.database_contract",
            return_value={"revision": "head", "policy": [1, 2, 3]},
        ):
            result = verify_histories(_urls(), expected_head="head")
        self.assertEqual(result["status"], "equivalent")
        self.assertEqual(len(set(result["fingerprints"].values())), 1)

        def drift(url: str, *, expected_head: str):
            return {
                "revision": expected_head,
                "policy": "drift" if url.endswith("queue_parent") else "baseline",
            }

        with patch(
            "scripts.verify_integration_migration_histories.database_contract",
            side_effect=drift,
        ), self.assertRaisesRegex(MigrationHistoryError, "differs"):
            verify_histories(_urls(), expected_head="head")


if __name__ == "__main__":
    unittest.main()
