from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import unittest

from scripts.fresh_campaign_secure_io import (
    FreshCampaignSecureIOError,
    SecureOutputDirectory,
    read_secure_material_tree,
    read_secure_root_file,
)


class FreshCampaignSecureIOTests(unittest.TestCase):
    def test_private_file_reader_rejects_links_and_broad_modes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            source.write_bytes(b"trusted\n")
            source.chmod(0o600)
            self.assertEqual(
                read_secure_root_file(
                    source, label="source", expected_mode=0o600, max_size=128
                ),
                b"trusted\n",
            )
            source.chmod(0o640)
            with self.assertRaises(FreshCampaignSecureIOError):
                read_secure_root_file(
                    source, label="source", expected_mode=0o600, max_size=128
                )
            source.chmod(0o600)
            linked = root / "linked"
            os.symlink(source, linked)
            with self.assertRaises(FreshCampaignSecureIOError):
                read_secure_root_file(
                    linked, label="linked", expected_mode=0o600, max_size=128
                )

    def test_transaction_publishes_one_closed_no_replace_tree(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            output = root / "campaign-material"
            with SecureOutputDirectory(output) as transaction:
                transaction.mkdir("roles")
                transaction.write("roles/bot-fi.env", b"A=B\n", mode=0o600)
                transaction.write("manifest.json", b"{}\n", mode=0o600)
                transaction.publish(before_publish=lambda: None)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            self.assertEqual(
                read_secure_material_tree(output)["roles/bot-fi.env"],
                (b"A=B\n", 0o600),
            )
            with self.assertRaises(FreshCampaignSecureIOError):
                SecureOutputDirectory(output)

    def test_transaction_failure_has_no_published_residue(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            output = root / "campaign-material"
            with self.assertRaisesRegex(RuntimeError, "injected"):
                with SecureOutputDirectory(output) as transaction:
                    transaction.write("manifest.json", b"{}\n", mode=0o600)
                    raise RuntimeError("injected")
            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".campaign-material.creating-*")), [])


if __name__ == "__main__":
    unittest.main()
