"""Isolated Market Pipeline migration rehearsal contracts."""

from __future__ import annotations

from pathlib import Path
import unittest

from scripts import rehearse_market_pipeline_archive_migration as rehearsal
from scripts.backup_market_pipeline_archive import restore_smoke


class RehearseMarketPipelineArchiveMigrationTests(unittest.TestCase):
    def test_rehearsal_never_composes_or_touches_live_project(self) -> None:
        source = Path(rehearsal.__file__).read_text(encoding="utf-8")
        self.assertIn("isolated_clone", source)
        self.assertIn("source_database_mutated", source)
        self.assertNotIn("docker compose", source)
        self.assertNotIn("compose up", source)
        self.assertNotIn("quiesce", source)
        self.assertIn("container:{container}", source)
        self.assertIn(rehearsal.CONFIRMATION, source)

    def test_restore_smoke_exposes_before_cleanup_hook(self) -> None:
        source = Path(restore_smoke.__code__.co_filename).read_text(encoding="utf-8")
        body = source.split("def restore_smoke(", 1)[1].split("\ndef _write_receipt", 1)[0]
        self.assertIn("before_cleanup", body)
        self.assertIn("before_cleanup(container)", body)
