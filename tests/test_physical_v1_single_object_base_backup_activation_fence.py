"""Static fences for the retired V1 single-object base-backup chain.

The old modules remain readable in the source worktree for migration and
forensic verification.  This test deliberately does not import them: it
proves that the reviewed release selection and Full-Matrix boundaries cannot
silently reactivate one of their capture/handoff/materialization paths.
"""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

from core import physical_release_candidate_inventory as inventory


_ROOT = Path(__file__).resolve().parents[1]
_READINESS_PATH = _ROOT / "core/physical_full_matrix_campaign_readiness.py"


def _core_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names if alias.name.startswith("core."))
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "core":
                modules.update("core." + alias.name for alias in node.names)
            elif node.module.startswith("core."):
                modules.add(node.module)
    return modules


class PhysicalV1SingleObjectBaseBackupActivationFenceTests(unittest.TestCase):
    def test_retired_activation_sources_exist_but_are_not_reviewed_or_imported(self) -> None:
        reviewed = {relative for _group, relative in inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS}
        retired_modules = {
            relative.removesuffix(".py").replace("/", ".")
            for relative in inventory.RETIRED_V1_SINGLE_OBJECT_BASE_BACKUP_ACTIVATION_PATHS
        }
        for relative in inventory.RETIRED_V1_SINGLE_OBJECT_BASE_BACKUP_ACTIVATION_PATHS:
            with self.subTest(path=relative):
                self.assertTrue((_ROOT / relative).is_file())
                self.assertNotIn(relative, reviewed)

        for relative in sorted(reviewed):
            if not relative.startswith("core/") or not relative.endswith(".py"):
                continue
            with self.subTest(reviewed_path=relative):
                self.assertFalse(_core_imports(_ROOT / relative) & retired_modules)

    def test_legacy_compatibility_readers_remain_explicit_and_nonactivation(self) -> None:
        reviewed = {relative for _group, relative in inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS}
        self.assertTrue(inventory.V1_SINGLE_OBJECT_BASE_BACKUP_COMPATIBILITY_ONLY_PATHS)
        self.assertFalse(
            inventory.V1_SINGLE_OBJECT_BASE_BACKUP_COMPATIBILITY_ONLY_PATHS
            & inventory.RETIRED_V1_SINGLE_OBJECT_BASE_BACKUP_ACTIVATION_PATHS
        )
        self.assertTrue(
            inventory.V1_SINGLE_OBJECT_BASE_BACKUP_COMPATIBILITY_ONLY_PATHS <= reviewed
        )

    def test_readiness_has_a_v1_fence_and_cannot_credit_the_v1_bundle_slot(self) -> None:
        source = _READINESS_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "v1-single-object-base-backup-activation-fenced",
            source,
        )
        self.assertIn(
            "reasons.add(PHYSICAL_FULL_MATRIX_V1_SINGLE_OBJECT_BASE_BACKUP_ACTIVATION_FENCE_REASON)",
            source,
        )
        self.assertNotIn('observed_slots.add("physical-wal-bundle")', source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
