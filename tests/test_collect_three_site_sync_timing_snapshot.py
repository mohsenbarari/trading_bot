from __future__ import annotations

import argparse
import asyncio
import unittest
from unittest.mock import patch

from scripts import collect_three_site_sync_timing_snapshot as snapshot
from scripts.legacy_three_site_staging_runtime_fence import (
    LegacyThreeSiteStagingRuntimeRetiredError,
)


class CollectThreeSiteSyncTimingSnapshotTests(unittest.TestCase):
    def test_collect_blocks_before_runtime_or_database_access(self):
        with (
            patch.object(snapshot, "resolve_runtime_identity", side_effect=AssertionError),
            patch.object(snapshot, "AsyncSessionLocal", side_effect=AssertionError),
        ):
            with self.assertRaises(LegacyThreeSiteStagingRuntimeRetiredError):
                asyncio.run(snapshot.collect("not-read", clock={}))

    def test_cli_blocks_before_parser_or_clock_input_read(self):
        with (
            patch.object(snapshot.argparse, "ArgumentParser", side_effect=AssertionError),
            patch.object(snapshot, "secure_json", side_effect=AssertionError),
        ):
            self.assertEqual(snapshot.main(["--unsafe"]), 2)


if __name__ == "__main__":
    unittest.main()
