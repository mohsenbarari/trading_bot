from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.dr_event_protocol import canonical_json_bytes
from core.secure_file_io import append_hash_chained_jsonl, verify_hash_chained_jsonl
from core.three_site_full_matrix_campaign import (
    BOUND_ARTIFACTS,
    PHASES,
    PHASE_SCENARIOS,
    verify_complete_matrix,
)
from core.three_site_full_matrix_runner import (
    CampaignIdentity,
    FullMatrixRunnerError,
    _identity_fields,
    run_full_matrix_campaign,
)
from tests.test_three_site_full_matrix_campaign import _sign, _signed_campaign


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


class FakeBackend:
    def __init__(
        self,
        root: Path,
        *,
        fail_key: tuple[int, str, str] | None = None,
        crash_key: tuple[int, str, str] | None = None,
    ) -> None:
        self.root = root
        self.fail_key = fail_key
        self.crash_key = crash_key
        self.crashed = False
        self.preflight_calls = 0
        self.executed: list[tuple[int, str, str]] = []
        self.recovered: list[tuple[int, str, str]] = []
        self.cleanups: list[tuple[int, str, bool]] = []

    @staticmethod
    def _identity(identity: CampaignIdentity) -> dict:
        return {
            **_identity_fields(identity),
            "status": "passed",
            "production_touched": False,
        }

    async def preflight(self, identity: CampaignIdentity) -> dict:
        self.preflight_calls += 1
        return {**self._identity(identity), "evidence_hash": _hash("preflight")}

    async def recover_interrupted(
        self,
        identity: CampaignIdentity,
        *,
        phase: str,
        scenario_id: str,
        iteration: int,
    ) -> dict:
        key = (iteration, phase, scenario_id)
        self.recovered.append(key)
        return {
            **self._identity(identity),
            "phase": phase,
            "scenario_id": scenario_id,
            "iteration": iteration,
            "residue_count": 0,
            "evidence_hash": _hash(f"recover:{key}"),
        }

    async def execute_scenario(
        self,
        identity: CampaignIdentity,
        *,
        phase: str,
        scenario_id: str,
        iteration: int,
    ) -> dict:
        key = (iteration, phase, scenario_id)
        self.executed.append(key)
        if self.crash_key == key and not self.crashed:
            self.crashed = True
            raise KeyboardInterrupt("simulated controller death")
        name = f"scenario-{iteration:02d}-{phase}-{scenario_id}.json"
        payload = canonical_json_bytes({"key": key, "oracle": True}) + b"\n"
        path = self.root / name
        path.write_bytes(payload)
        path.chmod(0o600)
        return {
            **self._identity(identity),
            "status": "failed" if self.fail_key == key else "passed",
            "phase": phase,
            "scenario_id": scenario_id,
            "iteration": iteration,
            "assertion_count": 3,
            "artifact_path": name,
            "artifact_sha256": hashlib.sha256(payload).hexdigest(),
            "artifact_size": len(payload),
            "evidence_hash": hashlib.sha256(payload).hexdigest(),
        }

    async def cleanup_phase(
        self,
        identity: CampaignIdentity,
        *,
        phase: str,
        iteration: int,
        failed: bool,
    ) -> dict:
        self.cleanups.append((iteration, phase, failed))
        return {
            **self._identity(identity),
            "phase": phase,
            "iteration": iteration,
            "residue_count": 0,
            "evidence_hash": _hash(f"cleanup:{iteration}:{phase}:{failed}"),
        }

    async def finalize(self, identity: CampaignIdentity) -> dict:
        return {
            **self._identity(identity),
            "residue_count": 0,
            "evidence_hash": _hash("finalize"),
        }


class ThreeSiteFullMatrixRunnerTests(unittest.IsolatedAsyncioTestCase):
    def _inputs(self):  # noqa: ANN202
        now = datetime.now(timezone.utc)
        campaign, policy, keys = _signed_campaign(now)
        stack = tempfile.TemporaryDirectory()
        root = Path(stack.name)
        root.chmod(0o700)
        bound: dict[str, Path] = {}
        for name in BOUND_ARTIFACTS:
            path = root / f"bound-{name}.json"
            payload = f"bound:{name}".encode()
            path.write_bytes(payload)
            path.chmod(0o600)
            campaign["bound_artifacts"][name] = hashlib.sha256(payload).hexdigest()
            bound[name] = path
        _sign(campaign, keys)
        return stack, now, campaign, policy, bound, root, root / "campaign.jsonl"

    async def test_executes_every_scenario_twice_and_emits_final_report(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        with stack:
            backend = FakeBackend(root)
            report = await run_full_matrix_campaign(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                artifact_root=root,
                journal=journal,
                backend=backend,
                now=now + timedelta(minutes=1),
            )
            expected = campaign["repetitions"] * sum(
                len(PHASE_SCENARIOS[phase]) for phase in PHASES
            )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["scenario_execution_count"], expected)
            self.assertEqual(len(backend.executed), expected)
            self.assertEqual(len(backend.cleanups), campaign["repetitions"] * len(PHASES))
            records = verify_hash_chained_jsonl(journal)
            self.assertEqual(records[-1]["event"], "campaign_completed")
            retained = [
                json.loads(
                    (
                        root
                        / f"{campaign['campaign_id']}-i{iteration:02d}-{phase}-evidence.json"
                    ).read_text()
                )
                for iteration in range(1, campaign["repetitions"] + 1)
                for phase in PHASES
            ]
            offline = verify_complete_matrix(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                phase_evidence=retained,
                artifact_root=root,
                execution_journal=journal,
                now=now + timedelta(minutes=1),
            )
            self.assertTrue(offline["authoritative_controller_journal"])
            self.assertEqual(offline["report_hash"], report["report_hash"])
            retained_audit = verify_complete_matrix(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                phase_evidence=retained,
                artifact_root=root,
                execution_journal=journal,
                now=now + timedelta(days=30),
            )
            self.assertEqual(retained_audit["report_hash"], report["report_hash"])

    async def test_scenario_failure_cleans_and_permanently_blocks_campaign(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        first = (1, PHASES[0], PHASE_SCENARIOS[PHASES[0]][0])
        with stack:
            backend = FakeBackend(root, fail_key=first)
            with self.assertRaisesRegex(FullMatrixRunnerError, "did not pass"):
                await run_full_matrix_campaign(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    artifact_root=root,
                    journal=journal,
                    backend=backend,
                    now=now + timedelta(minutes=1),
                )
            self.assertEqual(backend.cleanups, [(1, PHASES[0], True)])
            with self.assertRaisesRegex(FullMatrixRunnerError, "new campaign"):
                await run_full_matrix_campaign(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    artifact_root=root,
                    journal=journal,
                    backend=FakeBackend(root),
                    now=now + timedelta(minutes=1),
                )

    async def test_interrupted_scenario_requires_zero_residue_recovery_then_resumes(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        first = (1, PHASES[0], PHASE_SCENARIOS[PHASES[0]][0])
        interrupted = (1, PHASES[0], PHASE_SCENARIOS[PHASES[0]][1])
        with stack:
            crashed = FakeBackend(root, crash_key=interrupted)
            with self.assertRaises(KeyboardInterrupt):
                await run_full_matrix_campaign(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    artifact_root=root,
                    journal=journal,
                    backend=crashed,
                    now=now + timedelta(minutes=1),
                )
            resumed = FakeBackend(root)
            report = await run_full_matrix_campaign(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                artifact_root=root,
                journal=journal,
                backend=resumed,
                now=now + timedelta(minutes=1),
            )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(resumed.preflight_calls, 0)
            self.assertEqual(resumed.recovered, [interrupted])
            self.assertNotIn(first, resumed.executed)
            self.assertEqual(resumed.executed[0], interrupted)

    async def test_same_scenario_can_recover_after_two_controller_crashes(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        interrupted = (1, PHASES[0], PHASE_SCENARIOS[PHASES[0]][0])
        with stack:
            for _attempt in range(2):
                crashing_backend = FakeBackend(root, crash_key=interrupted)
                with self.assertRaises(KeyboardInterrupt):
                    await run_full_matrix_campaign(
                        campaign=campaign,
                        approver_policy=policy,
                        bound_artifacts=bound,
                        artifact_root=root,
                        journal=journal,
                        backend=crashing_backend,
                        now=now + timedelta(minutes=1),
                    )
            resumed = FakeBackend(root)
            report = await run_full_matrix_campaign(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                artifact_root=root,
                journal=journal,
                backend=resumed,
                now=now + timedelta(minutes=1),
            )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(resumed.recovered, [interrupted])
            self.assertEqual(resumed.executed[0], interrupted)

    async def test_completed_campaign_cannot_execute_or_append_again(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        with stack:
            await run_full_matrix_campaign(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                artifact_root=root,
                journal=journal,
                backend=FakeBackend(root),
                now=now + timedelta(minutes=1),
            )
            before = journal.read_bytes()
            with self.assertRaisesRegex(FullMatrixRunnerError, "immutable"):
                await run_full_matrix_campaign(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    artifact_root=root,
                    journal=journal,
                    backend=FakeBackend(root),
                    now=now + timedelta(minutes=1),
                )
            self.assertEqual(journal.read_bytes(), before)

    async def test_crash_after_finalization_resumes_without_reexecuting_live_work(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        with stack:
            original_journal_event = __import__(
                "core.three_site_full_matrix_runner",
                fromlist=["_journal_event"],
            )._journal_event

            def crash_before_completion(path, identity, *, event, **fields):
                if event == "campaign_completed":
                    raise KeyboardInterrupt("simulated controller death after finalization")
                return original_journal_event(
                    path,
                    identity,
                    event=event,
                    **fields,
                )

            first_backend = FakeBackend(root)
            with patch(
                "core.three_site_full_matrix_runner._journal_event",
                side_effect=crash_before_completion,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    await run_full_matrix_campaign(
                        campaign=campaign,
                        approver_policy=policy,
                        bound_artifacts=bound,
                        artifact_root=root,
                        journal=journal,
                        backend=first_backend,
                        now=now + timedelta(minutes=1),
                    )
            self.assertEqual(
                verify_hash_chained_jsonl(journal)[-1]["event"],
                "campaign_finalized",
            )

            resumed_backend = FakeBackend(root)
            report = await run_full_matrix_campaign(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                artifact_root=root,
                journal=journal,
                backend=resumed_backend,
                now=now + timedelta(minutes=1),
            )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(resumed_backend.preflight_calls, 0)
            self.assertEqual(resumed_backend.executed, [])
            self.assertEqual(resumed_backend.recovered, [])
            self.assertEqual(resumed_backend.cleanups, [])
            self.assertEqual(
                verify_hash_chained_jsonl(journal)[-1]["event"],
                "campaign_completed",
            )

    async def test_crash_between_phases_does_not_repeat_completed_phase_cleanup(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        with stack:
            original_journal_event = __import__(
                "core.three_site_full_matrix_runner",
                fromlist=["_journal_event"],
            )._journal_event
            next_phase = PHASES[1]
            next_scenario = PHASE_SCENARIOS[next_phase][0]

            def crash_before_next_phase(path, identity, *, event, **fields):
                if (
                    event == "scenario_started"
                    and fields.get("phase") == next_phase
                    and fields.get("scenario_id") == next_scenario
                ):
                    raise KeyboardInterrupt("simulated controller death between phases")
                return original_journal_event(
                    path,
                    identity,
                    event=event,
                    **fields,
                )

            first_backend = FakeBackend(root)
            with patch(
                "core.three_site_full_matrix_runner._journal_event",
                side_effect=crash_before_next_phase,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    await run_full_matrix_campaign(
                        campaign=campaign,
                        approver_policy=policy,
                        bound_artifacts=bound,
                        artifact_root=root,
                        journal=journal,
                        backend=first_backend,
                        now=now + timedelta(minutes=1),
                    )
            self.assertEqual(first_backend.cleanups, [(1, PHASES[0], False)])

            resumed_backend = FakeBackend(root)
            report = await run_full_matrix_campaign(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                artifact_root=root,
                journal=journal,
                backend=resumed_backend,
                now=now + timedelta(minutes=1),
            )
            self.assertEqual(report["status"], "passed")
            self.assertNotIn((1, PHASES[0], False), resumed_backend.cleanups)
            self.assertEqual(
                resumed_backend.executed[0],
                (1, next_phase, next_scenario),
            )
            phase_passes = [
                (record["iteration"], record["phase"])
                for record in verify_hash_chained_jsonl(journal)
                if record["event"] == "phase_passed"
            ]
            self.assertEqual(len(phase_passes), len(set(phase_passes)))

    async def test_expired_resume_performs_recovery_and_cleanup_without_forward_work(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        interrupted = (1, PHASES[0], PHASE_SCENARIOS[PHASES[0]][1])
        with stack:
            crashed = FakeBackend(root, crash_key=interrupted)
            with self.assertRaises(KeyboardInterrupt):
                await run_full_matrix_campaign(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    artifact_root=root,
                    journal=journal,
                    backend=crashed,
                    now=now + timedelta(minutes=1),
                )
            expired = FakeBackend(root)
            with self.assertRaisesRegex(FullMatrixRunnerError, "cleanup only"):
                await run_full_matrix_campaign(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    artifact_root=root,
                    journal=journal,
                    backend=expired,
                    now=now + timedelta(hours=49),
                )
            self.assertEqual(expired.recovered, [interrupted])
            self.assertEqual(expired.cleanups, [(1, PHASES[0], True)])
            self.assertEqual(expired.executed, [])
            self.assertEqual(
                verify_hash_chained_jsonl(journal)[-1]["event"], "campaign_blocked"
            )

    async def test_journal_identity_drift_is_rejected_before_backend_use(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        with stack:
            unsigned = {key: value for key, value in campaign.items() if key != "approvals"}
            append_hash_chained_jsonl(
                journal,
                {
                    "schema": "three-site-staging-full-matrix-journal-v1",
                    "timestamp": now.isoformat(),
                    "event": "campaign_started",
                    "campaign_id": campaign["campaign_id"],
                    "campaign_hash": hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest(),
                    "release_sha": "c" * 40,
                    "activation_sha": campaign["activation_sha"],
                },
            )
            backend = FakeBackend(root)
            with self.assertRaisesRegex(FullMatrixRunnerError, "identity"):
                await run_full_matrix_campaign(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    artifact_root=root,
                    journal=journal,
                    backend=backend,
                    now=now + timedelta(minutes=1),
                )
            self.assertEqual(backend.preflight_calls, 0)


if __name__ == "__main__":
    unittest.main()
