from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from core.three_site_full_matrix_campaign import scenarios_for_execution_class
from scripts.build_three_site_full_matrix_backend import (
    FullMatrixBackendBuildError,
    TIMEOUTS,
    build_documents,
)


class BuildThreeSiteFullMatrixBackendTests(unittest.TestCase):
    def test_builds_all_sealed_layers_and_catalog(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = root / "plan.json"
            plan.write_text('{"schema":"fixture"}\n', encoding="utf-8")
            plan.chmod(0o600)
            runtime_path = root / "runtime.json"
            campaign_id = str(uuid.uuid4())
            group_id = str(uuid.uuid4())
            runtime, backend = build_documents(
                campaign_id=campaign_id,
                gate_group_id=group_id,
                execution_class="shared-host-safe",
                release_sha="a" * 40,
                live_plan=plan,
                runtime_output=runtime_path,
            )
            expected_catalog = {
                phase: list(scenarios)
                for phase, scenarios in scenarios_for_execution_class(
                    "shared-host-safe"
                ).items()
            }
            self.assertEqual(runtime["supported_scenarios"], expected_catalog)
            self.assertEqual(backend["supported_scenarios"], expected_catalog)
            self.assertEqual(runtime["driver_config"]["timeouts_seconds"], TIMEOUTS)
            self.assertEqual(backend["timeouts_seconds"], TIMEOUTS)
            self.assertEqual(
                runtime["driver_config"]["runtime_plan"]["sha256"],
                hashlib.sha256(plan.read_bytes()).hexdigest(),
            )
            runtime_raw = (
                json.dumps(runtime, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            self.assertEqual(
                backend["runtime_config"],
                {
                    "path": str(runtime_path),
                    "sha256": hashlib.sha256(runtime_raw).hexdigest(),
                },
            )

    def test_rejects_non_owner_only_live_plan(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = root / "plan.json"
            plan.write_text('{"schema":"fixture"}\n', encoding="utf-8")
            plan.chmod(0o644)
            with self.assertRaises(FullMatrixBackendBuildError):
                build_documents(
                    campaign_id=str(uuid.uuid4()),
                    gate_group_id=str(uuid.uuid4()),
                    execution_class="shared-host-safe",
                    release_sha="a" * 40,
                    live_plan=plan,
                    runtime_output=root / "runtime.json",
                )


if __name__ == "__main__":
    unittest.main()
