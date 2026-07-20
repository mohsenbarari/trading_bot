from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from core.three_site_full_matrix_campaign import PHASES, PHASE_SCENARIOS
from core.three_site_full_matrix_command_backend import (
    CONFIG_SCHEMA,
    CommandFullMatrixBackend,
)
from core.three_site_full_matrix_runner import CampaignIdentity, FullMatrixRunnerError
from scripts.run_three_site_staging_full_matrix_campaign import (
    CONFIRM_ENV,
    CONFIRM_VALUE,
    _execute,
)


class CommandFullMatrixBackendTests(unittest.IsolatedAsyncioTestCase):
    def _fixture(self):  # noqa: ANN202
        stack = tempfile.TemporaryDirectory()
        root = Path(stack.name)
        repo = root / "repo"
        drivers = repo / "scripts" / "full_matrix_drivers"
        drivers.mkdir(parents=True)
        artifact_root = root / "artifacts"
        artifact_root.mkdir(mode=0o700)
        driver = drivers / "driver.py"
        driver.write_text(
            textwrap.dedent(
                """
                import argparse, hashlib, json
                from pathlib import Path
                parser = argparse.ArgumentParser()
                parser.add_argument('--operation', required=True)
                parser.add_argument('--campaign-id', required=True)
                parser.add_argument('--campaign-hash', required=True)
                parser.add_argument('--release-sha', required=True)
                parser.add_argument('--activation-sha', required=True)
                parser.add_argument('--artifact-root', type=Path, required=True)
                parser.add_argument('--phase')
                parser.add_argument('--scenario-id')
                parser.add_argument('--iteration', type=int)
                parser.add_argument('--failed')
                args = parser.parse_args()
                path = args.artifact_root / f'{args.operation}.json'
                body = json.dumps({'operation': args.operation}, sort_keys=True).encode() + b'\\n'
                path.write_bytes(body)
                path.chmod(0o600)
                digest = hashlib.sha256(body).hexdigest()
                print(json.dumps({
                    'status': 'passed', 'campaign_id': args.campaign_id,
                    'campaign_hash': args.campaign_hash, 'release_sha': args.release_sha,
                    'activation_sha': args.activation_sha, 'production_touched': False,
                    'evidence_hash': digest, 'artifact_path': path.name,
                    'artifact_sha256': digest, 'artifact_size': len(body),
                }))
                """
            ).strip()
            + "\n"
        )
        driver.chmod(0o644)
        config = {
            "schema": CONFIG_SCHEMA,
            "campaign_id": "11111111-1111-4111-8111-111111111111",
            "release_sha": "a" * 40,
            "production_forbidden": True,
            "driver": {
                "path": "scripts/full_matrix_drivers/driver.py",
                "sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
            },
            "supported_scenarios": {
                phase: list(PHASE_SCENARIOS[phase]) for phase in PHASES
            },
            "timeouts_seconds": {
                "preflight": 30,
                "recovery": 30,
                "scenario": 30,
                "endurance": 86430,
                "cleanup": 30,
                "finalize": 30,
            },
        }
        identity = CampaignIdentity(
            campaign_id=config["campaign_id"],
            campaign_hash="b" * 64,
            release_sha=config["release_sha"],
            activation_sha=config["release_sha"],
            repetitions=2,
        )
        return stack, repo, artifact_root, config, identity

    async def test_invokes_hash_pinned_driver_without_shell(self):
        stack, repo, artifact_root, config, identity = self._fixture()
        with stack:
            backend = CommandFullMatrixBackend(
                config=config,
                repo_root=repo,
                artifact_root=artifact_root,
                campaign_id=identity.campaign_id,
                release_sha=identity.release_sha,
            )
            result = await backend.preflight(identity)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["evidence_hash"], result["artifact_sha256"])
            self.assertTrue((artifact_root / result["artifact_path"]).is_file())

    async def test_driver_hash_or_catalog_drift_fails_closed(self):
        stack, repo, artifact_root, config, identity = self._fixture()
        with stack:
            config["driver"]["sha256"] = "0" * 64
            with self.assertRaisesRegex(FullMatrixRunnerError, "hash"):
                CommandFullMatrixBackend(
                    config=config,
                    repo_root=repo,
                    artifact_root=artifact_root,
                    campaign_id=identity.campaign_id,
                    release_sha=identity.release_sha,
                )

    async def test_execute_rejects_backend_config_not_bound_by_campaign_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            campaign = root / "campaign.json"
            policy = root / "policy.json"
            supplied_backend = root / "supplied-backend.json"
            bound_backend = root / "bound-backend.json"
            for path in (campaign, policy, supplied_backend, bound_backend):
                path.write_text(json.dumps({"fixture": path.name}) + "\n")
                path.chmod(0o600)
            bound = []
            from core.three_site_full_matrix_campaign import BOUND_ARTIFACTS

            for name in BOUND_ARTIFACTS:
                path = bound_backend if name == "full_matrix_backend_config" else root / name
                if not path.exists():
                    path.write_text("{}\n")
                    path.chmod(0o600)
                bound.append(f"{name}={path}")
            args = SimpleNamespace(
                campaign=campaign,
                approver_policy=policy,
                backend_config=supplied_backend,
                bound_artifact=bound,
                artifact_root=root,
                journal=root / "journal.jsonl",
            )
            with patch.dict(os.environ, {CONFIRM_ENV: CONFIRM_VALUE}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "campaign-bound"):
                    await _execute(args)


if __name__ == "__main__":
    unittest.main()
