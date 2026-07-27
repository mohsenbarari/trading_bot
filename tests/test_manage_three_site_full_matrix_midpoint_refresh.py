from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from core.canonical_json import canonical_json_bytes
from core.three_site_full_matrix_midpoint import MIDPOINT_PAUSE_REASON
from scripts.manage_three_site_full_matrix_midpoint_refresh import (
    MidpointRefreshHelperError,
    _identity,
    _secure_output_parent,
)


class ManageThreeSiteFullMatrixMidpointRefreshTests(unittest.TestCase):
    def _identity_payloads(self) -> tuple[dict, dict]:
        campaign = {
            "schema": "three-site-staging-full-matrix-campaign-v1",
            "campaign_id": "campaign-1",
            "gate_group_id": "gate-1",
            "execution_class": "shared-host-safe",
            "release_sha": "a" * 40,
            "approvals": [{"schema": "not-part-of-the-campaign-hash"}],
        }
        unsigned = {
            key: value for key, value in campaign.items() if key != "approvals"
        }
        campaign_hash = hashlib.sha256(
            canonical_json_bytes(unsigned)
        ).hexdigest()
        paused = {
            "schema": "three-site-staging-full-matrix-paused-v1",
            "status": "paused",
            "reason": MIDPOINT_PAUSE_REASON,
            "campaign_id": campaign["campaign_id"],
            "campaign_hash": campaign_hash,
            "gate_group_id": campaign["gate_group_id"],
            "execution_class": campaign["execution_class"],
            "release_sha": campaign["release_sha"],
            "completed_iteration": 1,
            "next_iteration": 2,
            "pre_pause_journal_head": "b" * 64,
        }
        return campaign, paused

    def test_identity_requires_exact_pause_binding_and_hex_head(self):
        campaign, paused = self._identity_payloads()
        campaign_hash, journal_head = _identity(campaign, paused)
        self.assertEqual(campaign_hash, paused["campaign_hash"])
        self.assertEqual(journal_head, paused["pre_pause_journal_head"])

        mutations = {
            "reason": "some_other_reason",
            "gate_group_id": "another-gate",
            "execution_class": "dedicated-host-destructive",
            "pre_pause_journal_head": "z" * 64,
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                candidate = deepcopy(paused)
                candidate[field] = value
                with self.assertRaisesRegex(
                    MidpointRefreshHelperError,
                    "campaign/pause identity is invalid",
                ):
                    _identity(campaign, candidate)

    def test_output_parent_must_be_existing_real_owner_mode_0700(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            secure = root / "proofs"
            secure.mkdir(mode=0o700)
            self.assertEqual(
                _secure_output_parent(secure, label="test output"),
                secure,
            )

            insecure = root / "insecure"
            insecure.mkdir(mode=0o750)
            with self.assertRaisesRegex(
                MidpointRefreshHelperError, "mode-0700"
            ):
                _secure_output_parent(insecure, label="test output")

            missing = root / "missing"
            with self.assertRaisesRegex(
                MidpointRefreshHelperError, "must already exist"
            ):
                _secure_output_parent(missing, label="test output")
            self.assertFalse(missing.exists())

            alias = root / "alias"
            alias.symlink_to(secure, target_is_directory=True)
            with self.assertRaisesRegex(
                MidpointRefreshHelperError, "mode-0700"
            ):
                _secure_output_parent(alias, label="test output")

            relative = Path(os.path.relpath(secure, Path.cwd()))
            with self.assertRaisesRegex(
                MidpointRefreshHelperError, "must be absolute"
            ):
                _secure_output_parent(relative, label="test output")

    def test_output_parent_rejects_current_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            current = root / "current"
            current.mkdir(mode=0o700)
            output = current / "proofs"
            output.mkdir(mode=0o700)
            with self.assertRaisesRegex(
                MidpointRefreshHelperError, "live/current"
            ):
                _secure_output_parent(output, label="test output")


if __name__ == "__main__":
    unittest.main()
