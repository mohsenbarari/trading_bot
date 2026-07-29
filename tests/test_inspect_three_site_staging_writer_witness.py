from __future__ import annotations

import argparse
import asyncio
import unittest
from unittest.mock import patch

from scripts import inspect_three_site_staging_writer_witness as inspection
from scripts.legacy_three_site_staging_runtime_fence import (
    LegacyThreeSiteStagingRuntimeRetiredError,
)


class InspectThreeSiteStagingWriterWitnessTests(unittest.TestCase):
    def test_run_blocks_before_runtime_or_witness_request(self):
        args = argparse.Namespace(
            request_id="not-read",
            expected_release_sha="not-read",
        )
        with (
            patch.object(inspection, "resolve_runtime_identity", side_effect=AssertionError),
            patch.object(inspection, "writer_witness_client_from_settings", side_effect=AssertionError),
        ):
            with self.assertRaises(LegacyThreeSiteStagingRuntimeRetiredError):
                asyncio.run(inspection.run(args))

    def test_cli_blocks_before_parser(self):
        with patch.object(inspection.argparse, "ArgumentParser", side_effect=AssertionError):
            self.assertEqual(inspection.main(["--unsafe"]), 2)


if __name__ == "__main__":
    unittest.main()
