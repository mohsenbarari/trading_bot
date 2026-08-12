from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import joblib

from scripts.calibrate_morning_reopen_anchor import _write_candidate_artifacts
from scripts.train_residual_shadow_and_calibrate import _write_shadow_artifacts


class ResearchArtifactSafetyTests(unittest.TestCase):
    def test_morning_candidate_does_not_touch_runtime_without_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "research" / "candidate.json"
            runtime = root / "runtime" / "candidate.json"
            output.parent.mkdir()
            runtime.parent.mkdir()

            staged = _write_candidate_artifacts(
                candidate_path=output,
                runtime_candidate_path=runtime,
                text='{"model":"candidate"}',
                stage_runtime_artifacts=False,
            )

            self.assertFalse(staged)
            self.assertTrue(output.is_file())
            self.assertFalse(runtime.exists())

    def test_residual_shadow_stages_runtime_only_with_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "research" / "shadow.joblib"
            runtime = root / "runtime" / "shadow.joblib"
            output.parent.mkdir()
            runtime.parent.mkdir()
            artifact = {"shadow_version": "test"}

            staged = _write_shadow_artifacts(
                artifact=artifact,
                output_path=output,
                runtime_path=runtime,
                stage_runtime_artifact=False,
            )
            self.assertFalse(staged)
            self.assertEqual(joblib.load(output), artifact)
            self.assertFalse(runtime.exists())

            staged = _write_shadow_artifacts(
                artifact=artifact,
                output_path=output,
                runtime_path=runtime,
                stage_runtime_artifact=True,
            )
            self.assertTrue(staged)
            self.assertEqual(joblib.load(runtime), artifact)


if __name__ == "__main__":
    unittest.main()
