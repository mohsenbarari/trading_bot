from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.legacy_three_site_staging_runtime_fence import (
    LegacyThreeSiteStagingRuntimeRetiredError,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts/bootstrap_three_site_staging_writer_lease.py"
SPEC = importlib.util.spec_from_file_location("staging_writer_lease_bootstrap", SCRIPT_PATH)
assert SPEC and SPEC.loader
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class StagingWriterLeaseBootstrapTests(unittest.TestCase):
    def test_run_is_retired_before_runtime_or_witness_access(self):
        args = bootstrap.argparse.Namespace(
            campaign_id="not-read",
            request_id="not-read",
            expected_release_sha="not-read",
            apply=True,
            confirm="not-read",
        )
        with (
            patch.object(bootstrap, "resolve_runtime_identity", side_effect=AssertionError),
            patch.object(bootstrap, "writer_witness_client_from_settings", side_effect=AssertionError),
            patch.object(bootstrap, "initialize_local_writer_lease_once", side_effect=AssertionError),
        ):
            with self.assertRaises(LegacyThreeSiteStagingRuntimeRetiredError):
                asyncio.run(bootstrap.run(args))

    def test_cli_blocks_before_parser(self):
        with patch.object(bootstrap.argparse, "ArgumentParser", side_effect=AssertionError):
            self.assertEqual(bootstrap.main(["--unsafe"]), 2)


if __name__ == "__main__":
    unittest.main()
