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
    RUNTIME_CONFIG_SCHEMA,
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
                parser.add_argument('--operation-id', required=True)
                parser.add_argument('--campaign-id', required=True)
                parser.add_argument('--campaign-hash', required=True)
                parser.add_argument('--release-sha', required=True)
                parser.add_argument('--activation-sha', required=True)
                parser.add_argument('--artifact-root', type=Path, required=True)
                parser.add_argument('--runtime-config', type=Path, required=True)
                parser.add_argument('--phase')
                parser.add_argument('--scenario-id')
                parser.add_argument('--iteration', type=int)
                parser.add_argument('--attempt', type=int)
                parser.add_argument('--failed')
                args = parser.parse_args()
                runtime = json.loads(args.runtime_config.read_text())
                path = args.artifact_root / f'{args.operation}.json'
                body = json.dumps({
                    'operation': args.operation,
                    'runtime_marker': runtime['test_marker'],
                }, sort_keys=True).encode() + b'\\n'
                path.write_bytes(body)
                path.chmod(0o600)
                digest = hashlib.sha256(body).hexdigest()
                print(json.dumps({
                    'status': 'passed', 'campaign_id': args.campaign_id,
                    'campaign_hash': args.campaign_hash, 'release_sha': args.release_sha,
                    'activation_sha': args.activation_sha, 'production_touched': False,
                    'operation_id': args.operation_id,
                    'driver_attempt': args.attempt,
                    'evidence_hash': digest, 'artifact_path': path.name,
                    'artifact_sha256': digest, 'artifact_size': len(body),
                }))
                """
            ).strip()
            + "\n"
        )
        driver.chmod(0o644)
        runtime = root / "runtime.json"
        runtime.write_text(json.dumps({
            "schema": RUNTIME_CONFIG_SCHEMA,
            "campaign_id": "11111111-1111-4111-8111-111111111111",
            "release_sha": "a" * 40,
            "production_forbidden": True,
            "supported_scenarios": {
                phase: list(PHASE_SCENARIOS[phase]) for phase in PHASES
            },
            "test_marker": "sealed-runtime",
        }, sort_keys=True) + "\n")
        runtime.chmod(0o600)
        config = {
            "schema": CONFIG_SCHEMA,
            "campaign_id": "11111111-1111-4111-8111-111111111111",
            "release_sha": "a" * 40,
            "production_forbidden": True,
            "driver": {
                "path": "scripts/full_matrix_drivers/driver.py",
                "sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
            },
            "runtime_config": {
                "path": str(runtime.resolve()),
                "sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
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
            result = await backend.preflight(
                identity, operation_id="22222222-2222-4222-8222-222222222222"
            )
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["evidence_hash"], result["artifact_sha256"])
            self.assertTrue((artifact_root / result["artifact_path"]).is_file())
            raw = json.loads((artifact_root / result["artifact_path"]).read_text())
            self.assertEqual(raw["runtime_marker"], "sealed-runtime")
            backend.close()

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

    async def test_scenario_attempt_is_forwarded_as_fixed_argv(self):
        stack, repo, artifact_root, config, identity = self._fixture()
        with stack:
            backend = CommandFullMatrixBackend(
                config=config,
                repo_root=repo,
                artifact_root=artifact_root,
                campaign_id=identity.campaign_id,
                release_sha=identity.release_sha,
            )
            phase = next(iter(PHASES))
            result = await backend.execute_scenario(
                identity,
                phase=phase,
                scenario_id=PHASE_SCENARIOS[phase][0],
                iteration=1,
                attempt=2,
                operation_id="22222222-2222-4222-8222-222222222222",
            )
            self.assertEqual(result["driver_attempt"], 2)
            backend.close()

    async def test_runtime_hash_or_unsafe_mode_fails_closed(self):
        stack, repo, artifact_root, config, identity = self._fixture()
        with stack:
            config["runtime_config"]["sha256"] = "0" * 64
            with self.assertRaisesRegex(FullMatrixRunnerError, "runtime hash"):
                CommandFullMatrixBackend(
                    config=config,
                    repo_root=repo,
                    artifact_root=artifact_root,
                    campaign_id=identity.campaign_id,
                    release_sha=identity.release_sha,
                )

        stack, repo, artifact_root, config, identity = self._fixture()
        with stack:
            Path(config["runtime_config"]["path"]).chmod(0o644)
            with self.assertRaisesRegex(FullMatrixRunnerError, "runtime file is unsafe"):
                CommandFullMatrixBackend(
                    config=config,
                    repo_root=repo,
                    artifact_root=artifact_root,
                    campaign_id=identity.campaign_id,
                    release_sha=identity.release_sha,
                )

    async def test_invocation_uses_sealed_bytes_after_path_replacement(self):
        stack, repo, artifact_root, config, identity = self._fixture()
        with stack:
            backend = CommandFullMatrixBackend(
                config=config,
                repo_root=repo,
                artifact_root=artifact_root,
                campaign_id=identity.campaign_id,
                release_sha=identity.release_sha,
            )
            driver = repo / config["driver"]["path"]
            driver.write_text("raise SystemExit(91)\n")
            result = await backend.preflight(
                identity, operation_id="22222222-2222-4222-8222-222222222222"
            )
            self.assertEqual(result["status"], "passed")
            backend.close()

    async def test_invocation_uses_sealed_runtime_after_path_replacement(self):
        stack, repo, artifact_root, config, identity = self._fixture()
        with stack:
            backend = CommandFullMatrixBackend(
                config=config,
                repo_root=repo,
                artifact_root=artifact_root,
                campaign_id=identity.campaign_id,
                release_sha=identity.release_sha,
            )
            runtime = Path(config["runtime_config"]["path"])
            replacement = runtime.with_suffix(".replacement")
            replacement.write_text("{}\n")
            replacement.chmod(0o600)
            replacement.replace(runtime)
            result = await backend.preflight(
                identity, operation_id="22222222-2222-4222-8222-222222222222"
            )
            raw = json.loads((artifact_root / result["artifact_path"]).read_text())
            self.assertEqual(raw["runtime_marker"], "sealed-runtime")
            backend.close()

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
