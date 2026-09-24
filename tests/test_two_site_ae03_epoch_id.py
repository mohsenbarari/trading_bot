#!/usr/bin/env python3
"""Hermetic unit tests for pure rescope_epoch_basename derivation."""
from __future__ import annotations

import copy
from pathlib import Path
import re
import unittest
from unittest.mock import patch

try:
    from scripts.two_site_assignment_scope import AssignmentScope
    from scripts.two_site_codex_autopilot import (
        CONTROL_PATHS,
        MAX_RESCOPE_RECEIPT_BYTES,
        PROTECTED_REFS,
        AutopilotError,
        rescope_epoch_basename,
    )
except ModuleNotFoundError:
    from two_site_assignment_scope import AssignmentScope  # type: ignore[no-redef]
    from two_site_codex_autopilot import (  # type: ignore[no-redef]
        CONTROL_PATHS,
        MAX_RESCOPE_RECEIPT_BYTES,
        PROTECTED_REFS,
        AutopilotError,
        rescope_epoch_basename,
    )


def _make_valid_repository(
    *,
    head: str = "1" * 40,
    status: str = "",
    use_dataclass_scope: bool = False,
    scope_sha: str = "a" * 64,
    protected_ref_sha: str = "2" * 40,
    control_sha: str = "3" * 40,
) -> dict[str, object]:
    refs = {ref: protected_ref_sha for ref in sorted(PROTECTED_REFS)}
    controls = {p.as_posix(): control_sha for p in sorted(CONTROL_PATHS)}
    if use_dataclass_scope:
        scope: object = AssignmentScope(
            assignment_id="assign-001",
            stage_id="stage-001",
            sha256=scope_sha,
            expires_at="2026-10-01T00:00:00Z",
            allowed_paths=["scripts/two_site_codex_autopilot.py"],
            forbidden_paths=[],
        )
    else:
        scope = {
            "assignment_id": "assign-001",
            "stage_id": "stage-001",
            "sha256": scope_sha,
            "expires_at": "2026-10-01T00:00:00Z",
            "allowed_paths": ["scripts/two_site_codex_autopilot.py"],
            "forbidden_paths": [],
        }
    return {
        "head": head,
        "status": status,
        "protected_refs": refs,
        "control_blobs": controls,
        "assignment_scope": scope,
    }


class TestRescopeEpochBasename(unittest.TestCase):
    def test_canonical_identity_and_equivalence(self) -> None:
        repo_dict = _make_valid_repository(use_dataclass_scope=False)
        repo_dc = _make_valid_repository(use_dataclass_scope=True)

        base1 = rescope_epoch_basename(repo_dict)
        base2 = rescope_epoch_basename(repo_dc)

        self.assertEqual(base1, base2)
        self.assertEqual(len(base1), 72)
        self.assertTrue(base1.startswith("rescope-"))
        self.assertRegex(base1, r"^rescope-[0-9a-f]{64}$")
        self.assertTrue(Path(base1).name == base1)

    def test_sensitivity_to_every_identity_component(self) -> None:
        base = rescope_epoch_basename(_make_valid_repository())

        head_changed = _make_valid_repository(head="4" * 40)
        self.assertNotEqual(rescope_epoch_basename(head_changed), base)

        scope_changed = _make_valid_repository(scope_sha="b" * 64)
        self.assertNotEqual(rescope_epoch_basename(scope_changed), base)

        ctrl_changed = _make_valid_repository()
        ctrl_blobs = dict(ctrl_changed["control_blobs"])  # type: ignore[arg-type]
        first_ctrl = sorted(ctrl_blobs.keys())[0]
        ctrl_blobs[first_ctrl] = "5" * 40
        ctrl_changed["control_blobs"] = ctrl_blobs
        self.assertNotEqual(rescope_epoch_basename(ctrl_changed), base)

        ref_changed = _make_valid_repository()
        ref_blobs = dict(ref_changed["protected_refs"])  # type: ignore[arg-type]
        first_ref = sorted(ref_blobs.keys())[0]
        ref_blobs[first_ref] = "6" * 40
        ref_changed["protected_refs"] = ref_blobs
        self.assertNotEqual(rescope_epoch_basename(ref_changed), base)

    def test_dict_order_insensitivity(self) -> None:
        repo = _make_valid_repository(use_dataclass_scope=False)
        base_original = rescope_epoch_basename(repo)

        reordered: dict[str, object] = {
            k: repo[k] for k in reversed(list(repo.keys()))
        }
        reordered["protected_refs"] = {
            k: repo["protected_refs"][k]  # type: ignore[index]
            for k in reversed(list(repo["protected_refs"].keys()))  # type: ignore[union-attr]
        }
        reordered["control_blobs"] = {
            k: repo["control_blobs"][k]  # type: ignore[index]
            for k in reversed(list(repo["control_blobs"].keys()))  # type: ignore[union-attr]
        }
        reordered["assignment_scope"] = {
            k: repo["assignment_scope"][k]  # type: ignore[index]
            for k in reversed(list(repo["assignment_scope"].keys()))  # type: ignore[union-attr]
        }

        self.assertEqual(rescope_epoch_basename(reordered), base_original)

    def test_rejections_and_pure_behavior(self) -> None:
        with self.assertRaises(AutopilotError):
            rescope_epoch_basename(Path("/tmp/repo"))
        with self.assertRaises(AutopilotError):
            rescope_epoch_basename(["not", "a", "mapping"])
        with self.assertRaises(AutopilotError):
            rescope_epoch_basename(None)

        dirty_repo = _make_valid_repository(status="M modified.py")
        with self.assertRaises(AutopilotError):
            rescope_epoch_basename(dirty_repo)

        extra_key_repo = _make_valid_repository()
        extra_key_repo["unexpected_key"] = "untrusted"
        with self.assertRaises(AutopilotError):
            rescope_epoch_basename(extra_key_repo)

        oversized_repo = _make_valid_repository(use_dataclass_scope=False)
        oversized_repo["assignment_scope"]["allowed_paths"] = [  # type: ignore[index]
            "x" * 100 for _ in range(200)
        ]
        with self.assertRaises(AutopilotError):
            rescope_epoch_basename(oversized_repo)

        clean_repo = _make_valid_repository(use_dataclass_scope=False)
        snapshot_before = copy.deepcopy(clean_repo)
        with patch("builtins.open") as mock_open, patch("os.mkdir") as mock_mkdir, patch("os.open") as mock_os_open:
            res = rescope_epoch_basename(clean_repo)
            mock_open.assert_not_called()
            mock_mkdir.assert_not_called()
            mock_os_open.assert_not_called()

        self.assertEqual(clean_repo, snapshot_before)
        self.assertEqual(len(res), 72)


if __name__ == "__main__":
    unittest.main()
