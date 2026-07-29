from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.legacy_three_site_staging_runtime_fence import (
    LegacyThreeSiteStagingRuntimeRetiredError,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts/request_three_site_human_approval_relay.py"
SPEC = importlib.util.spec_from_file_location("human_approval_relay_request", SCRIPT_PATH)
assert SPEC and SPEC.loader
relay_request = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(relay_request)


class RelayRequestTests(unittest.TestCase):
    def test_request_is_retired_before_subject_credential_or_network_io(self):
        args = relay_request.argparse.Namespace(
            action="approve_inventory",
            subject=Path("/unsafe/subject.json"),
            policy=Path("/unsafe/policy.json"),
            credentials=Path("/unsafe/relay.env"),
            output=Path("/unsafe/receipt.json"),
            timeout_seconds=5.0,
        )
        with (
            patch.object(relay_request, "_strict_json", side_effect=AssertionError),
            patch.object(relay_request, "_credential_values", side_effect=AssertionError),
            patch.object(relay_request.httpx, "Client", side_effect=AssertionError),
        ):
            with self.assertRaises(LegacyThreeSiteStagingRuntimeRetiredError):
                relay_request.request_receipt(args)

    def test_cli_blocks_before_parser(self):
        with patch.object(relay_request.argparse, "ArgumentParser", side_effect=AssertionError):
            self.assertEqual(relay_request.main(["--unsafe"]), 2)


if __name__ == "__main__":
    unittest.main()
