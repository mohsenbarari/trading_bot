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
    FullMatrixCampaignError,
    PHASES,
    PHASE_SCENARIOS,
    SCENARIO_EVIDENCE_SCHEMA,
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
        self.elapsed = 0.0

    def monotonic(self) -> float:
        return self.elapsed

    @staticmethod
    def _identity(identity: CampaignIdentity) -> dict:
        return {
            **_identity_fields(identity),
            "status": "passed",
            "production_touched": False,
        }

    def _operation_artifact(self, name: str, payload: dict) -> dict:
        relative = f"operation-{name}.json"
        body = canonical_json_bytes(payload) + b"\n"
        path = self.root / relative
        if path.exists() and path.read_bytes() != body:
            raise AssertionError("operation artifact changed on replay")
        path.write_bytes(body)
        path.chmod(0o600)
        digest = hashlib.sha256(body).hexdigest()
        return {
            "artifact_path": relative,
            "artifact_sha256": digest,
            "artifact_size": len(body),
            "evidence_hash": digest,
        }

    async def preflight(self, identity: CampaignIdentity) -> dict:
        self.preflight_calls += 1
        return {
            **self._identity(identity),
            **self._operation_artifact("preflight", {"operation": "preflight"}),
        }

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
        value = {
            **self._identity(identity),
            "phase": phase,
            "scenario_id": scenario_id,
            "iteration": iteration,
            "residue_count": 0,
        }
        return {
            **value,
            **self._operation_artifact(
                f"recover-{iteration:02d}-{phase}-{scenario_id}",
                {"operation": "recover", "key": key, "residue_count": 0},
            ),
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
        duration = 86400 if scenario_id == "twenty_four_hour_endurance_no_growth" else 1
        started_at = datetime.now(timezone.utc)
        self.elapsed += duration
        raw_name = f"raw-{iteration:02d}-{phase}-{scenario_id}.json"
        raw_payload = canonical_json_bytes({"key": key, "observed": True}) + b"\n"
        raw_path = self.root / raw_name
        raw_path.write_bytes(raw_payload)
        raw_path.chmod(0o600)
        raw_record = {
            "path": raw_name,
            "sha256": hashlib.sha256(raw_payload).hexdigest(),
            "size": len(raw_payload),
        }
        names = [
            "operation_executed", "expected_outcome", "production_boundary",
            f"oracle:{scenario_id}",
        ]
        if duration == 86400:
            names.append("minimum_duration")
        assertions = [
            {
                "name": assertion,
                "status": "passed",
                "expected": 86400 if assertion == "minimum_duration" else (
                    False if assertion == "production_boundary" else True
                ),
                "observed": duration if assertion == "minimum_duration" else (
                    False if assertion == "production_boundary" else True
                ),
                "evidence_refs": [raw_name],
            }
            for assertion in names
        ]
        name = f"scenario-{iteration:02d}-{phase}-{scenario_id}.json"
        payload = canonical_json_bytes(
            {
                "schema": SCENARIO_EVIDENCE_SCHEMA,
                "status": "passed" if self.fail_key != key else "failed",
                **_identity_fields(identity),
                "phase": phase,
                "scenario_id": scenario_id,
                "iteration": iteration,
                "oracle_id": f"{phase}.{scenario_id}.v1",
                "started_at": started_at.isoformat(),
                "finished_at": (started_at + timedelta(seconds=duration)).isoformat(),
                "duration_seconds": duration,
                "assertions": assertions,
                "evidence_refs": [raw_record],
                "cleanup_residue_count": 0,
                "production_touched": False,
            }
        ) + b"\n"
        path = self.root / name
        path.write_bytes(payload)
        path.chmod(0o600)
        return {
            **self._identity(identity),
            "status": "failed" if self.fail_key == key else "passed",
            "phase": phase,
            "scenario_id": scenario_id,
            "iteration": iteration,
            "assertion_count": len(assertions),
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
        value = {
            **self._identity(identity),
            "phase": phase,
            "iteration": iteration,
            "residue_count": 0,
        }
        return {
            **value,
            **self._operation_artifact(
                f"cleanup-{iteration:02d}-{phase}-{'failed' if failed else 'passed'}",
                {
                    "operation": "cleanup",
                    "iteration": iteration,
                    "phase": phase,
                    "failed": failed,
                    "residue_count": 0,
                },
            ),
        }

    async def finalize(self, identity: CampaignIdentity) -> dict:
        value = {
            **self._identity(identity),
            "residue_count": 0,
        }
        return {
            **value,
            **self._operation_artifact(
                "finalize", {"operation": "finalize", "residue_count": 0}
            ),
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

            (root / "operation-preflight.json").unlink()
            with self.assertRaisesRegex(
                FullMatrixCampaignError, "retained preflight artifact"
            ):
                verify_complete_matrix(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    phase_evidence=retained,
                    artifact_root=root,
                    execution_journal=journal,
                    now=now + timedelta(days=30),
                )

    async def test_endurance_claim_requires_controller_monotonic_duration(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        with stack:
            with self.assertRaisesRegex(
                FullMatrixRunnerError, "before 24 monotonic hours"
            ):
                await run_full_matrix_campaign(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    artifact_root=root,
                    journal=journal,
                    backend=FakeBackend(root),
                    now=now + timedelta(minutes=1),
                    monotonic=lambda: 0.0,
                )

    async def test_operation_artifact_reuse_is_rejected_by_standalone_verifier(self):
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
            self.assertEqual(report["status"], "passed")
            records = verify_hash_chained_jsonl(journal, label="test journal")
            preflight = next(
                row for row in records if row["event"] == "campaign_started"
            )["result"]
            finalized = next(
                row for row in records if row["event"] == "campaign_finalized"
            )
            final_path = root / finalized["result"]["artifact_path"]
            preflight_path = root / preflight["artifact_path"]
            final_path.write_bytes(preflight_path.read_bytes())
            final_path.chmod(0o600)
            finalized["result"].update(
                artifact_path=preflight["artifact_path"],
                artifact_sha256=preflight["artifact_sha256"],
                artifact_size=preflight["artifact_size"],
                evidence_hash=preflight["evidence_hash"],
            )
            # Rebuild the hash chain to model a fully re-signed journal forgery;
            # the semantic verifier must still reject artifact reuse.
            forged = root / "forged-journal.jsonl"
            for row in records[:-1]:
                append_hash_chained_jsonl(
                    forged,
                    {
                        key: value
                        for key, value in row.items()
                        if key not in {"previous_hash", "event_hash"}
                    },
                )
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
            with self.assertRaisesRegex(FullMatrixCampaignError, "reused"):
                verify_complete_matrix(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    phase_evidence=retained,
                    artifact_root=root,
                    execution_journal=forged,
                    now=now + timedelta(days=30),
                )

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
