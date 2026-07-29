from __future__ import annotations

import argparse
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.legacy_three_site_staging_runtime_fence import (
    LegacyThreeSiteStagingRuntimeRetiredError,
)
from scripts import publish_wa_ir_object_storage_preflight as publication


class PublishWaIrObjectStoragePreflightTests(unittest.TestCase):
    def test_execute_blocks_before_credentials_materials_or_provider_access(self):
        args = argparse.Namespace(
            release_sha="not-read",
            prefix="not-read",
            url_ttl_seconds=900,
            remote_secure_materials_dir=Path("/unsafe/materials"),
            remote_age_identity=Path("/unsafe/identity"),
            bucket="not-read",
            apply=True,
            confirm="not-read",
        )
        with (
            patch.object(publication, "_credentials", side_effect=AssertionError),
            patch.object(publication, "_client", side_effect=AssertionError),
            patch.object(publication, "build_role_materials", side_effect=AssertionError),
        ):
            with self.assertRaises(LegacyThreeSiteStagingRuntimeRetiredError):
                publication.execute(args)

    def test_effectful_helpers_are_retired_before_io(self):
        with self.assertRaises(LegacyThreeSiteStagingRuntimeRetiredError):
            publication.build_role_materials(Path("/unsafe/source"), Path("/unsafe/output"))
        with self.assertRaises(LegacyThreeSiteStagingRuntimeRetiredError):
            publication._upload_and_readback(
                object(), bucket="unsafe", key="unsafe", source=Path("/unsafe/source"), metadata={}
            )

    def test_cli_blocks_before_parser(self):
        with patch.object(publication, "parse_args", side_effect=AssertionError):
            self.assertEqual(publication.main(["--unsafe"]), 2)


if __name__ == "__main__":
    unittest.main()
