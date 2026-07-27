from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core.dr_event_protocol import canonical_json_bytes
from core.human_approval import (
    HumanApprovalRelayCommand,
    approval_subject,
    issue_human_approval_relay_receipt,
)
from core.human_approval_issuer import (
    authenticate_and_issue_session,
    totp_code,
)
from core.secure_file_io import append_hash_chained_jsonl, verify_hash_chained_jsonl
from core.three_site_full_matrix_campaign import (
    BOUND_ARTIFACTS,
    CAMPAIGN_SCHEMA,
    FullMatrixCampaignError,
    OPERATION_EVIDENCE_SCHEMA,
    PHASES,
    PHASE_SCENARIOS,
    SCENARIO_EVIDENCE_SCHEMA,
    customer_actor_pair_contracts,
    verify_complete_matrix,
    verify_scenario_evidence,
)
from core.three_site_sync_timing import (
    SYNC_TIMING_ASSERTION,
    sync_timing_policy,
    verify_sync_timing_evidence,
)
from core.three_site_full_matrix_runner import (
    CampaignIdentity,
    FullMatrixRunnerError,
    _identity_fields,
    _operation_id,
    _validate_preflight,
    run_full_matrix_campaign,
)
from core.three_site_full_matrix_midpoint import (
    FullMatrixMidpointError,
    MIDPOINT_ACTIONS,
    MIDPOINT_SESSION_ACTIONS,
    assemble_midpoint_bundle,
    validate_midpoint_journal,
)
from core.three_site_execution_safety import SHARED_HOST_SAFE
from tests.three_site_sync_timing_fixtures import make_sync_timing_artifact
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
        self.finalize_calls = 0
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

    def _operation_artifact(
        self,
        name: str,
        *,
        identity: CampaignIdentity,
        operation_kind: str,
        operation_id: str,
        operation_context: dict,
        residue_count: int = 0,
    ) -> dict:
        raw_name = f"raw-operation-{name}.json"
        raw_body = canonical_json_bytes(
            {
                "operation_kind": operation_kind,
                "operation_id": operation_id,
                "operation_context": operation_context,
                "residue_count": residue_count,
            }
        ) + b"\n"
        raw_path = self.root / raw_name
        raw_path.write_bytes(raw_body)
        raw_path.chmod(0o600)
        raw_record = {
            "path": raw_name,
            "sha256": hashlib.sha256(raw_body).hexdigest(),
            "size": len(raw_body),
        }
        names = {
            "preflight": (
                "campaign_identity_bound", "prerequisites_verified",
                "topology_ready", "production_boundary",
            ),
            "recovery": (
                "faults_removed", "writer_state_safe", "residue_zero",
                "production_boundary",
            ),
            "cleanup": (
                "faults_removed", "writer_state_safe", "residue_zero",
                "production_boundary",
            ),
            "finalize": (
                "all_faults_removed", "writer_state_safe", "residue_zero",
                "production_boundary",
            ),
        }[operation_kind]
        assertions = []
        for assertion_name in names:
            if assertion_name == "production_boundary":
                expected = observed = False
            elif assertion_name == "residue_zero":
                expected = observed = 0
            else:
                expected = observed = True
            assertions.append(
                {
                    "name": assertion_name,
                    "status": "passed",
                    "expected": expected,
                    "observed": observed,
                    "evidence_refs": [raw_name],
                }
            )
        payload = {
            "schema": OPERATION_EVIDENCE_SCHEMA,
            "status": "passed",
            **_identity_fields(identity),
            "operation_kind": operation_kind,
            "operation_id": operation_id,
            "operation_context": operation_context,
            "assertions": assertions,
            "evidence_refs": [raw_record],
            "residue_count": residue_count,
            "production_touched": False,
        }
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

    async def preflight(
        self, identity: CampaignIdentity, *, operation_id: str
    ) -> dict:
        self.preflight_calls += 1
        return {
            **self._identity(identity),
            "operation_id": operation_id,
            **self._operation_artifact(
                "preflight", identity=identity, operation_kind="preflight",
                operation_id=operation_id,
                operation_context={
                    "phase": "", "scenario_id": "", "iteration": 0,
                    "failed": None, "attempt": 0,
                },
            ),
        }

    async def recover_interrupted(
        self,
        identity: CampaignIdentity,
        *,
        phase: str,
        scenario_id: str,
        iteration: int,
        attempt: int,
        operation_id: str,
    ) -> dict:
        key = (iteration, phase, scenario_id)
        self.recovered.append(key)
        value = {
            **self._identity(identity),
            "phase": phase,
            "scenario_id": scenario_id,
            "iteration": iteration,
            "attempt": attempt,
            "residue_count": 0,
            "operation_id": operation_id,
        }
        return {
            **value,
            **self._operation_artifact(
                f"recover-{iteration:02d}-{phase}-{scenario_id}-a{attempt}",
                identity=identity, operation_kind="recovery",
                operation_id=operation_id,
                operation_context={
                    "phase": phase, "scenario_id": scenario_id,
                    "iteration": iteration, "failed": None, "attempt": attempt,
                },
            ),
        }

    async def execute_scenario(
        self,
        identity: CampaignIdentity,
        *,
        phase: str,
        scenario_id: str,
        iteration: int,
        attempt: int,
        operation_id: str,
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
        raw_records = [raw_record]
        names = [
            "operation_executed", "expected_outcome", "production_boundary",
            f"oracle:{scenario_id}",
        ]
        if duration == 86400:
            names.append("minimum_duration")
        assertions = []
        for assertion in names:
            if assertion == "minimum_duration":
                expected, observed = 86400, duration
            elif assertion == "production_boundary":
                expected = observed = False
            elif assertion == "operation_executed":
                expected = observed = {
                    "operation_id": operation_id,
                    "scenario_id": scenario_id,
                    "iteration": iteration,
                    "attempt": attempt,
                }
            else:
                expected = observed = {"verified": True}
            assertions.append(
                {
                    "name": assertion,
                    "status": "passed",
                    "expected": expected,
                    "observed": observed,
                    "evidence_refs": [raw_name],
                }
            )
        for assertion_name, contract in customer_actor_pair_contracts(
            scenario_id
        ).items():
            pair_name = contract["actor_pair"]
            pair_raw_name = (
                f"raw-customer-{iteration:02d}-{scenario_id}-{pair_name}.json"
            )
            pair_raw_payload = canonical_json_bytes(
                {"key": key, "customer_contract": contract}
            ) + b"\n"
            pair_raw_path = self.root / pair_raw_name
            pair_raw_path.write_bytes(pair_raw_payload)
            pair_raw_path.chmod(0o600)
            pair_raw_record = {
                "path": pair_raw_name,
                "sha256": hashlib.sha256(pair_raw_payload).hexdigest(),
                "size": len(pair_raw_payload),
            }
            raw_records.append(pair_raw_record)
            assertions.append(
                {
                    "name": assertion_name,
                    "status": "passed",
                    "expected": contract,
                    "observed": contract,
                    "evidence_refs": [pair_raw_name],
                }
            )
        if sync_timing_policy(scenario_id) is not None:
            timing = make_sync_timing_artifact(
                scenario_id,
                captured_at=started_at,
            )
            timing_name = (
                f"raw-sync-timing-{iteration:02d}-{scenario_id}.json"
            )
            timing_payload = canonical_json_bytes(timing) + b"\n"
            timing_path = self.root / timing_name
            timing_path.write_bytes(timing_payload)
            timing_path.chmod(0o600)
            raw_records.append(
                {
                    "path": timing_name,
                    "sha256": hashlib.sha256(timing_payload).hexdigest(),
                    "size": len(timing_payload),
                }
            )
            assertions.append(
                {
                    "name": SYNC_TIMING_ASSERTION,
                    "status": "passed",
                    "expected": sync_timing_policy(scenario_id),
                    "observed": verify_sync_timing_evidence(
                        timing,
                        scenario_id=scenario_id,
                    ),
                    "evidence_refs": [timing_name],
                }
            )
        name = f"scenario-{iteration:02d}-{phase}-{scenario_id}.json"
        payload = canonical_json_bytes(
            {
                "schema": SCENARIO_EVIDENCE_SCHEMA,
                "status": "passed" if self.fail_key != key else "failed",
                **_identity_fields(identity),
                "phase": phase,
                "scenario_id": scenario_id,
                "iteration": iteration,
                "attempt": attempt,
                "operation_id": operation_id,
                "oracle_id": f"{phase}.{scenario_id}.v1",
                "started_at": started_at.isoformat(),
                "finished_at": (started_at + timedelta(seconds=duration)).isoformat(),
                "duration_seconds": duration,
                "assertions": assertions,
                "evidence_refs": raw_records,
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
            "attempt": attempt,
            "operation_id": operation_id,
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
        operation_id: str,
    ) -> dict:
        self.cleanups.append((iteration, phase, failed))
        value = {
            **self._identity(identity),
            "phase": phase,
            "iteration": iteration,
            "residue_count": 0,
            "operation_id": operation_id,
        }
        return {
            **value,
            **self._operation_artifact(
                f"cleanup-{iteration:02d}-{phase}-{'failed' if failed else 'passed'}",
                identity=identity, operation_kind="cleanup",
                operation_id=operation_id,
                operation_context={
                    "phase": phase, "scenario_id": "", "iteration": iteration,
                    "failed": failed, "attempt": 0,
                },
            ),
        }

    async def finalize(
        self, identity: CampaignIdentity, *, operation_id: str
    ) -> dict:
        self.finalize_calls += 1
        value = {
            **self._identity(identity),
            "residue_count": 0,
            "operation_id": operation_id,
        }
        return {
            **value,
            **self._operation_artifact(
                "finalize",
                identity=identity, operation_kind="finalize",
                operation_id=operation_id,
                operation_context={
                    "phase": "", "scenario_id": "", "iteration": 0,
                    "failed": None, "attempt": 0,
                },
            ),
        }


class ThreeSiteFullMatrixRunnerTests(unittest.IsolatedAsyncioTestCase):
    def _inputs(self):  # noqa: ANN202
        now = datetime.now(timezone.utc)
        campaign, policy, keys = _signed_campaign(now)
        stack = tempfile.TemporaryDirectory()
        root = Path(stack.name)
        root.chmod(0o700)
        witness_private_key = Ed25519PrivateKey.generate()
        witness_public_key = base64.b64encode(
            witness_private_key.public_key().public_bytes(
                Encoding.Raw, PublicFormat.Raw
            )
        ).decode("ascii")
        witness_public_key_file = root / "witness-relay-public.key"
        witness_public_key_file.write_text(witness_public_key + "\n")
        witness_public_key_file.chmod(0o600)
        bound: dict[str, Path] = {}
        for name in BOUND_ARTIFACTS:
            path = root / f"bound-{name}.json"
            if name == "failover_backend_config":
                payload = canonical_json_bytes(
                    {
                        "schema": "three-site-staging-failover-backend-v1",
                        "campaign_id": campaign["campaign_id"],
                        "release_sha": campaign["release_sha"],
                        "witness": {"public_key": witness_public_key},
                    }
                )
            elif name == "failover_control_config":
                payload = canonical_json_bytes(
                    {
                        "schema": "three-site-full-matrix-failover-control-v1",
                        "campaign_id": campaign["campaign_id"],
                        "gate_group_id": campaign["gate_group_id"],
                        "execution_class": campaign["execution_class"],
                        "release_sha": campaign["release_sha"],
                        "backend_config": str(
                            (
                                root
                                / "bound-failover_backend_config.json"
                            ).resolve()
                        ),
                        "relay_credentials": str(
                            (root / "relay-credentials.env").resolve()
                        ),
                        "witness_relay_public_key_file": str(
                            witness_public_key_file.resolve()
                        ),
                        "journal_root": str(root.resolve()),
                    }
                )
            else:
                payload = f"bound:{name}".encode()
            path.write_bytes(payload)
            path.chmod(0o600)
            campaign["bound_artifacts"][name] = hashlib.sha256(payload).hexdigest()
            bound[name] = path
        _sign(campaign, keys)
        if not hasattr(self, "_midpoint_context"):
            self._midpoint_context = {}
        self._midpoint_context[root] = (keys, witness_private_key)
        if not hasattr(self, "_initial_sessions"):
            self._initial_sessions = {}
        self._initial_sessions[root] = self._set_relay_start_approval(
            campaign=campaign,
            policy=policy,
            root=root,
            issued_at=now,
        )
        return stack, now, campaign, policy, bound, root, root / "campaign.jsonl"

    def _midpoint_bundle(
        self,
        *,
        paused: dict,
        campaign: dict,
        policy: dict,
        root: Path,
        issued_at: datetime | None = None,
        ttl_seconds: int = 48 * 60 * 60,
        witness_private_key: Ed25519PrivateKey | None = None,
        allowed_actions: list[str] | None = None,
        session_token: dict | None = None,
    ) -> dict:
        enrollment, bound_witness_private_key = self._midpoint_context[root]
        pause_time = datetime.fromisoformat(
            next(
                record["timestamp"]
                for record in verify_hash_chained_jsonl(
                    root / "campaign.jsonl", label="test journal"
                )
                if record["event"] == "campaign_paused"
            )
        )
        issued_at = issued_at or pause_time + timedelta(microseconds=1)
        witness_private_key = witness_private_key or bound_witness_private_key
        session = session_token
        if session is None:
            session, _state, _audit = authenticate_and_issue_session(
                secrets_payload=enrollment.secrets_payload,
                state_payload=enrollment.state_payload,
                policy_payload=enrollment.policy_payload,
                private_key_envelope=enrollment.private_key_envelope,
                password="test matrix approval passphrase",
                totp=totp_code(enrollment.totp_secret, at=issued_at)[1],
                recovery_code=None,
                release_sha=campaign["release_sha"],
                allowed_actions=(
                    allowed_actions or list(MIDPOINT_SESSION_ACTIONS)
                ),
                ttl_seconds=ttl_seconds,
                now=issued_at,
            )
        subjects = {
            item["action"]: item["subject"]
            for item in paused["refresh_subjects"]
        }
        receipts = {}
        for index, action in enumerate(MIDPOINT_ACTIONS):
            command = HumanApprovalRelayCommand(
                action=action,
                environment="staging",
                subject=subjects[action],
                request_id=f"midpoint-{index}-{uuid4()}",
            )
            receipts[action] = issue_human_approval_relay_receipt(
                session,
                policy_payload=policy,
                command=command,
                witness_private_key=witness_private_key,
                now=issued_at,
                receipt_id=str(uuid4()),
            )
        unsigned = {
            key: value for key, value in campaign.items() if key != "approvals"
        }
        return assemble_midpoint_bundle(
            campaign=campaign,
            campaign_hash=hashlib.sha256(
                canonical_json_bytes(unsigned)
            ).hexdigest(),
            pre_pause_journal_head=paused["pre_pause_journal_head"],
            receipts=receipts,
        )

    def _set_relay_start_approval(
        self,
        *,
        campaign: dict,
        policy: dict,
        root: Path,
        issued_at: datetime,
        ttl_seconds: int = 48 * 60 * 60,
        allowed_actions: list[str] | None = None,
    ) -> dict:
        enrollment, witness_private_key = self._midpoint_context[root]
        unsigned = {
            key: value for key, value in campaign.items() if key != "approvals"
        }
        subject = approval_subject(
            artifact_type=CAMPAIGN_SCHEMA,
            artifact_sha256=hashlib.sha256(
                canonical_json_bytes(unsigned)
            ).hexdigest(),
            release_sha=campaign["release_sha"],
            bindings={
                "campaign_id": campaign["campaign_id"],
                "gate_group_id": campaign["gate_group_id"],
                "execution_class": campaign["execution_class"],
            },
        )
        session, _state, _audit = authenticate_and_issue_session(
            secrets_payload=enrollment.secrets_payload,
            state_payload=enrollment.state_payload,
            policy_payload=enrollment.policy_payload,
            private_key_envelope=enrollment.private_key_envelope,
            password="test matrix approval passphrase",
            totp=totp_code(enrollment.totp_secret, at=issued_at)[1],
            recovery_code=None,
            release_sha=campaign["release_sha"],
            allowed_actions=allowed_actions or list(MIDPOINT_SESSION_ACTIONS),
            ttl_seconds=ttl_seconds,
            now=issued_at,
        )
        command = HumanApprovalRelayCommand(
            action="start_full_matrix",
            environment="staging",
            subject=subject,
            request_id=f"campaign-start-{uuid4()}",
        )
        campaign["approvals"] = [
            issue_human_approval_relay_receipt(
                session,
                policy_payload=policy,
                command=command,
                witness_private_key=witness_private_key,
                now=issued_at,
                receipt_id=str(uuid4()),
            )
        ]
        return session

    async def _run_to_completion(
        self,
        *,
        campaign: dict,
        policy: dict,
        bound: dict[str, Path],
        root: Path,
        journal: Path,
        backend: FakeBackend,
        now: datetime,
        monotonic=None,  # noqa: ANN001
    ) -> dict:
        result = await run_full_matrix_campaign(
            campaign=campaign,
            approver_policy=policy,
            bound_artifacts=bound,
            artifact_root=root,
            journal=journal,
            backend=backend,
            now=now,
            monotonic=monotonic,
        )
        if result["status"] != "paused":
            return result
        bundle = self._midpoint_bundle(
            paused=result, campaign=campaign, policy=policy, root=root
        )
        return await run_full_matrix_campaign(
            campaign=campaign,
            approver_policy=policy,
            bound_artifacts=bound,
            artifact_root=root,
            journal=journal,
            backend=backend,
            midpoint_refresh_bundle=bundle,
            now=now,
            monotonic=monotonic,
        )

    async def test_executes_every_scenario_twice_and_emits_final_report(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        with stack:
            backend = FakeBackend(root)
            report = await self._run_to_completion(
                campaign=campaign,
                policy=policy,
                bound=bound,
                root=root,
                journal=journal,
                backend=backend,
                now=now + timedelta(minutes=1),
            )
            expected = campaign["repetitions"] * sum(
                len(scenarios) for scenarios in campaign["required_scenarios"].values()
            )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["scenario_execution_count"], expected)
            self.assertEqual(len(backend.executed), expected)
            self.assertEqual(
                len(backend.cleanups),
                campaign["repetitions"] * len(campaign["required_phases"]),
            )
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
                for phase in campaign["required_phases"]
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

            forged = root / "forged-midpoint-journal.jsonl"
            for row in records:
                unsigned = {
                    key: value
                    for key, value in row.items()
                    if key not in {"previous_hash", "event_hash"}
                }
                if unsigned["event"] == "campaign_resumed":
                    unsigned = json.loads(json.dumps(unsigned))
                    unsigned["refresh_summary"]["bundle_sha256"] = "f" * 64
                append_hash_chained_jsonl(forged, unsigned)
            with self.assertRaisesRegex(
                FullMatrixCampaignError, "midpoint refresh proof"
            ):
                verify_complete_matrix(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    phase_evidence=retained,
                    artifact_root=root,
                    execution_journal=forged,
                    now=now + timedelta(days=30),
                )

            (root / "operation-preflight.json").unlink()
            with self.assertRaisesRegex(
                FullMatrixCampaignError, "retained preflight.*artifact"
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

    async def test_historical_direct_journal_needs_no_witness_or_midpoint(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        with stack:
            await self._run_to_completion(
                campaign=campaign,
                policy=policy,
                bound=bound,
                root=root,
                journal=journal,
                backend=FakeBackend(root),
                now=now + timedelta(minutes=1),
            )
            legacy_journal = root / "legacy-direct-campaign.jsonl"
            for row in verify_hash_chained_jsonl(journal):
                if row["event"] in {
                    "campaign_paused",
                    "campaign_resumed",
                    "campaign_completed",
                }:
                    continue
                append_hash_chained_jsonl(
                    legacy_journal,
                    {
                        key: value
                        for key, value in row.items()
                        if key not in {"previous_hash", "event_hash"}
                    },
                )
            enrollment, _witness_private_key = self._midpoint_context[root]
            _sign(campaign, enrollment)
            retained = [
                json.loads(
                    (
                        root
                        / f"{campaign['campaign_id']}-i{iteration:02d}-{phase}-evidence.json"
                    ).read_text()
                )
                for iteration in range(1, campaign["repetitions"] + 1)
                for phase in campaign["required_phases"]
            ]
            with patch(
                "core.three_site_full_matrix_campaign.load_bound_witness_public_key",
                side_effect=AssertionError("legacy audit loaded Witness material"),
            ):
                report = verify_complete_matrix(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    phase_evidence=retained,
                    artifact_root=root,
                    execution_journal=legacy_journal,
                    now=now + timedelta(days=30),
                )
            self.assertEqual(report["status"], "passed")
            self.assertTrue(report["authoritative_controller_journal"])

    async def test_midpoint_pause_is_zero_residue_and_idempotent_without_proof(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        with stack:
            backend = FakeBackend(root)
            paused = await run_full_matrix_campaign(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                artifact_root=root,
                journal=journal,
                backend=backend,
                now=now + timedelta(minutes=1),
            )
            self.assertEqual(paused["status"], "paused")
            self.assertEqual(paused["cleanup_residue_count"], 0)
            self.assertEqual(paused["refresh_bundle_status"], "required")
            before = journal.read_bytes()
            repeated = await run_full_matrix_campaign(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                artifact_root=root,
                journal=journal,
                backend=FakeBackend(root),
                now=now + timedelta(minutes=1),
            )
            self.assertEqual(repeated["status"], "paused")
            self.assertEqual(repeated["pause_event_hash"], paused["pause_event_hash"])
            self.assertEqual(journal.read_bytes(), before)
            records = verify_hash_chained_jsonl(journal, label="test journal")
            self.assertEqual(records[-1]["event"], "campaign_paused")
            self.assertEqual(
                sum(row["event"] == "campaign_paused" for row in records), 1
            )
            self.assertFalse(
                any(
                    row.get("iteration") == 2
                    or (
                        isinstance(row.get("operation_context"), dict)
                        and row["operation_context"].get("iteration") == 2
                    )
                    for row in records
                )
            )
            self.assertFalse(
                any(row["event"] == "campaign_blocked" for row in records)
            )

    async def test_midpoint_requires_exact_final_cleanup_quartet(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        with stack:
            paused = await run_full_matrix_campaign(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                artifact_root=root,
                journal=journal,
                backend=FakeBackend(root),
                now=now + timedelta(minutes=1),
            )
            records = verify_hash_chained_jsonl(journal, label="test journal")
            pause_index = next(
                index
                for index, record in enumerate(records)
                if record["event"] == "campaign_paused"
            )

            cases = {}

            missing_start = deepcopy(records)
            del missing_start[pause_index - 3]
            cases["missing cleanup start"] = missing_start

            moved_start = deepcopy(records)
            moved_start[pause_index - 4], moved_start[pause_index - 3] = (
                moved_start[pause_index - 3],
                moved_start[pause_index - 4],
            )
            cases["moved cleanup start"] = moved_start

            substituted_pair = deepcopy(records)
            substituted_pair[pause_index - 2]["operation_id"] = str(uuid4())
            cases["substituted cleanup pass"] = substituted_pair

            mismatched_result = deepcopy(records)
            mismatched_result[pause_index - 1]["cleanup_result"] = {
                **mismatched_result[pause_index - 1]["cleanup_result"],
                "artifact_size": (
                    mismatched_result[pause_index - 1]["cleanup_result"][
                        "artifact_size"
                    ]
                    + 1
                ),
            }
            cases["phase/result mismatch"] = mismatched_result

            residue = deepcopy(records)
            residue[pause_index - 2]["result"]["residue_count"] = 1
            residue[pause_index - 1]["cleanup_result"]["residue_count"] = 1
            cases["nonzero residue"] = residue

            production = deepcopy(records)
            production[pause_index - 2]["result"]["production_touched"] = True
            production[pause_index - 1]["cleanup_result"][
                "production_touched"
            ] = True
            cases["production touched"] = production

            for label, candidate in cases.items():
                with self.subTest(label=label):
                    with self.assertRaises(FullMatrixMidpointError):
                        validate_midpoint_journal(
                            candidate,
                            campaign=campaign,
                            campaign_hash=paused["campaign_hash"],
                        )

    async def test_expired_unresumed_pause_is_read_only_and_idempotent(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        with stack:
            paused = await run_full_matrix_campaign(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                artifact_root=root,
                journal=journal,
                backend=FakeBackend(root),
                now=now + timedelta(minutes=1),
            )
            midpoint_bundle = self._midpoint_bundle(
                paused=paused,
                campaign=campaign,
                policy=policy,
                root=root,
            )
            before = journal.read_bytes()
            for attempt in range(2):
                with self.subTest(attempt=attempt):
                    resumed = FakeBackend(root)
                    repeated = await run_full_matrix_campaign(
                        campaign=campaign,
                        approver_policy=policy,
                        bound_artifacts=bound,
                        artifact_root=root,
                        journal=journal,
                        backend=resumed,
                        midpoint_refresh_bundle=midpoint_bundle,
                        now=now + timedelta(hours=49),
                    )
                    self.assertEqual(repeated["status"], "paused")
                    self.assertEqual(
                        repeated["pause_event_hash"],
                        paused["pause_event_hash"],
                    )
                    self.assertEqual(journal.read_bytes(), before)
                    self.assertEqual(resumed.preflight_calls, 0)
                    self.assertEqual(resumed.finalize_calls, 0)
                    self.assertEqual(resumed.executed, [])
                    self.assertEqual(resumed.recovered, [])
                    self.assertEqual(resumed.cleanups, [])

    async def test_finalize_intent_and_completion_order_matrix(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        with stack:
            report = await self._run_to_completion(
                campaign=campaign,
                policy=policy,
                bound=bound,
                root=root,
                journal=journal,
                backend=FakeBackend(root),
                now=now + timedelta(minutes=1),
            )
            self.assertEqual(report["status"], "passed")
            records = verify_hash_chained_jsonl(journal, label="test journal")
            validate_midpoint_journal(
                records,
                campaign=campaign,
                campaign_hash=report["campaign_hash"],
            )
            pause_index = next(
                index
                for index, record in enumerate(records)
                if record["event"] == "campaign_paused"
            )
            resume_index = next(
                index
                for index, record in enumerate(records)
                if record["event"] == "campaign_resumed"
            )
            finalize_start_index = next(
                index
                for index, record in enumerate(records)
                if record["event"] == "operation_started"
                and record["operation_kind"] == "finalize"
            )
            finalize_pass_index = finalize_start_index + 1
            finalized_index = finalize_pass_index + 1

            cases = {}

            pre_pause = deepcopy(records)
            pre_pause[pause_index - 4]["operation_kind"] = "finalize"
            pre_pause[pause_index - 4]["operation_context"] = {
                "phase": "",
                "scenario_id": "",
                "iteration": 0,
                "failed": None,
                "attempt": 0,
            }
            cases["iteration-zero finalize marker before pause"] = pre_pause

            between_checkpoint_events = deepcopy(records)
            between_checkpoint_events.insert(
                resume_index,
                deepcopy(records[finalize_start_index]),
            )
            cases["finalize intent between pause and resume"] = (
                between_checkpoint_events
            )

            before_iteration_two_completion = deepcopy(records)
            finalize_pair = before_iteration_two_completion[
                finalize_start_index : finalize_pass_index + 1
            ]
            del before_iteration_two_completion[
                finalize_start_index : finalize_pass_index + 1
            ]
            final_iteration_two_phase = max(
                index
                for index, record in enumerate(before_iteration_two_completion)
                if record.get("event") == "phase_passed"
                and record.get("iteration") == 2
            )
            before_iteration_two_completion[
                final_iteration_two_phase:final_iteration_two_phase
            ] = finalize_pair
            cases["finalize before final iteration-two phase"] = (
                before_iteration_two_completion
            )

            finalized_before_pass = deepcopy(records)
            finalized_before_pass[finalize_pass_index], finalized_before_pass[
                finalized_index
            ] = (
                finalized_before_pass[finalized_index],
                finalized_before_pass[finalize_pass_index],
            )
            cases["campaign finalized before operation pass"] = (
                finalized_before_pass
            )

            mismatched_finalized_result = deepcopy(records)
            mismatched_finalized_result[finalized_index]["result"] = {
                **mismatched_finalized_result[finalized_index]["result"],
                "residue_count": 1,
            }
            cases["finalized/result mismatch"] = mismatched_finalized_result

            for label, candidate in cases.items():
                with self.subTest(label=label):
                    with self.assertRaises(FullMatrixMidpointError):
                        validate_midpoint_journal(
                            candidate,
                            campaign=campaign,
                            campaign_hash=report["campaign_hash"],
                        )

    async def test_runner_rejects_divergent_campaign_bound_witness_keys(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        with stack:
            control = json.loads(bound["failover_control_config"].read_text())
            key_path = Path(control["witness_relay_public_key_file"])
            divergent = Ed25519PrivateKey.generate().public_key().public_bytes(
                Encoding.Raw, PublicFormat.Raw
            )
            key_path.write_text(base64.b64encode(divergent).decode() + "\n")
            key_path.chmod(0o600)
            backend = FakeBackend(root)
            with self.assertRaisesRegex(
                FullMatrixRunnerError, "Witness identity"
            ):
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
            self.assertFalse(journal.exists())

    async def test_campaign_start_relay_receipt_uses_bound_witness_key(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        with stack:
            session = self._initial_sessions[root]
            paused = await run_full_matrix_campaign(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                artifact_root=root,
                journal=journal,
                backend=FakeBackend(root),
                now=now + timedelta(minutes=1),
            )
            self.assertEqual(paused["status"], "paused")
            pause_time = datetime.fromisoformat(
                next(
                    row["timestamp"]
                    for row in verify_hash_chained_jsonl(journal)
                    if row["event"] == "campaign_paused"
                )
            )
            old_session_bundle = self._midpoint_bundle(
                paused=paused,
                campaign=campaign,
                policy=policy,
                root=root,
                issued_at=pause_time + timedelta(microseconds=1),
                session_token=session,
            )
            before = journal.read_bytes()
            rejected = await run_full_matrix_campaign(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                artifact_root=root,
                journal=journal,
                backend=FakeBackend(root),
                midpoint_refresh_bundle=old_session_bundle,
                now=now + timedelta(minutes=1),
            )
            self.assertEqual(rejected["refresh_bundle_status"], "rejected")
            self.assertEqual(journal.read_bytes(), before)

    async def test_new_run_rejects_direct_approval_before_bound_material(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        with stack:
            enrollment, _witness_private_key = self._midpoint_context[root]
            _sign(campaign, enrollment)
            bound["failover_backend_config"] = root / "must-not-be-opened.json"
            backend = FakeBackend(root)
            with self.assertRaisesRegex(
                FullMatrixRunnerError, "requires a Witness relay"
            ):
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
            self.assertFalse(journal.exists())

    async def test_initial_relay_session_requires_exact_scope_and_runway(self):
        for name, ttl_seconds, actions, error in (
            (
                "short-runway",
                46 * 60 * 60,
                list(MIDPOINT_SESSION_ACTIONS),
                "not fresh enough",
            ),
            (
                "broad-scope",
                48 * 60 * 60,
                [*MIDPOINT_SESSION_ACTIONS, "approve_inventory"],
                "session scope",
            ),
        ):
            with self.subTest(name=name):
                stack, now, campaign, policy, bound, root, journal = self._inputs()
                with stack:
                    self._set_relay_start_approval(
                        campaign=campaign,
                        policy=policy,
                        root=root,
                        issued_at=now,
                        ttl_seconds=ttl_seconds,
                        allowed_actions=actions,
                    )
                    backend = FakeBackend(root)
                    with self.assertRaisesRegex(
                        FullMatrixCampaignError, error
                    ):
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
                    self.assertFalse(journal.exists())

    async def test_midpoint_rejects_tamper_wrong_key_old_session_and_short_expiry(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        with stack:
            paused = await run_full_matrix_campaign(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                artifact_root=root,
                journal=journal,
                backend=FakeBackend(root),
                now=now + timedelta(minutes=1),
            )
            pause_time = datetime.fromisoformat(
                next(
                    row["timestamp"]
                    for row in verify_hash_chained_jsonl(journal)
                    if row["event"] == "campaign_paused"
                )
            )
            valid = self._midpoint_bundle(
                paused=paused, campaign=campaign, policy=policy, root=root
            )
            tampered = json.loads(json.dumps(valid))
            tampered["probes"][0]["receipt"]["subject"]["bindings"][
                "next_iteration"
            ] = 3
            wrong_key = self._midpoint_bundle(
                paused=paused,
                campaign=campaign,
                policy=policy,
                root=root,
                witness_private_key=Ed25519PrivateKey.generate(),
            )
            old_session = self._midpoint_bundle(
                paused=paused,
                campaign=campaign,
                policy=policy,
                root=root,
                issued_at=pause_time - timedelta(seconds=1),
            )
            enrollment, witness_private_key = self._midpoint_context[root]
            pre_pause_session_at = pause_time - timedelta(microseconds=1)
            pre_pause_session, _state, _audit = authenticate_and_issue_session(
                secrets_payload=enrollment.secrets_payload,
                state_payload=enrollment.state_payload,
                policy_payload=enrollment.policy_payload,
                private_key_envelope=enrollment.private_key_envelope,
                password="test matrix approval passphrase",
                totp=totp_code(
                    enrollment.totp_secret,
                    at=pre_pause_session_at,
                )[1],
                recovery_code=None,
                release_sha=campaign["release_sha"],
                allowed_actions=list(MIDPOINT_SESSION_ACTIONS),
                ttl_seconds=48 * 60 * 60,
                now=pre_pause_session_at,
            )
            pre_pause_session_bundle = self._midpoint_bundle(
                paused=paused,
                campaign=campaign,
                policy=policy,
                root=root,
                issued_at=pause_time + timedelta(microseconds=1),
                session_token=pre_pause_session,
            )
            short_expiry = self._midpoint_bundle(
                paused=paused,
                campaign=campaign,
                policy=policy,
                root=root,
                ttl_seconds=120,
            )
            broad_scope = self._midpoint_bundle(
                paused=paused,
                campaign=campaign,
                policy=policy,
                root=root,
                allowed_actions=[
                    *MIDPOINT_SESSION_ACTIONS,
                    "approve_inventory",
                ],
            )
            second_session = self._midpoint_bundle(
                paused=paused, campaign=campaign, policy=policy, root=root
            )
            mixed_session = json.loads(json.dumps(valid))
            mixed_session["probes"][2]["receipt"] = second_session["probes"][2][
                "receipt"
            ]
            shared_session_at = pause_time + timedelta(seconds=1)
            shared_session, _state, _audit = authenticate_and_issue_session(
                secrets_payload=enrollment.secrets_payload,
                state_payload=enrollment.state_payload,
                policy_payload=enrollment.policy_payload,
                private_key_envelope=enrollment.private_key_envelope,
                password="test matrix approval passphrase",
                totp=totp_code(
                    enrollment.totp_secret,
                    at=shared_session_at,
                )[1],
                recovery_code=None,
                release_sha=campaign["release_sha"],
                allowed_actions=list(MIDPOINT_SESSION_ACTIONS),
                ttl_seconds=48 * 60 * 60,
                now=shared_session_at,
            )
            inconsistent_session_time = self._midpoint_bundle(
                paused=paused,
                campaign=campaign,
                policy=policy,
                root=root,
                issued_at=pause_time + timedelta(seconds=2),
                session_token=shared_session,
            )
            inconsistent_receipt = inconsistent_session_time["probes"][2][
                "receipt"
            ]
            inconsistent_receipt["session_issued_at"] = (
                shared_session_at + timedelta(microseconds=1)
            ).isoformat()
            inconsistent_unsigned = {
                key: value
                for key, value in inconsistent_receipt.items()
                if key != "witness_signature"
            }
            inconsistent_receipt["witness_signature"] = base64.b64encode(
                witness_private_key.sign(
                    canonical_json_bytes(inconsistent_unsigned)
                )
            ).decode("ascii")
            before = journal.read_bytes()
            for name, bundle in {
                "tampered": tampered,
                "wrong-key": wrong_key,
                "old-session": old_session,
                "pre-pause-session": pre_pause_session_bundle,
                "short-expiry": short_expiry,
                "broad-scope": broad_scope,
                "mixed-session": mixed_session,
                "inconsistent-session-issued-at": inconsistent_session_time,
            }.items():
                with self.subTest(name=name):
                    rejected = await run_full_matrix_campaign(
                        campaign=campaign,
                        approver_policy=policy,
                        bound_artifacts=bound,
                        artifact_root=root,
                        journal=journal,
                        backend=FakeBackend(root),
                        midpoint_refresh_bundle=bundle,
                        now=now + timedelta(minutes=1),
                    )
                    self.assertEqual(rejected["status"], "paused")
                    self.assertEqual(
                        rejected["refresh_bundle_status"], "rejected"
                    )
                    self.assertEqual(journal.read_bytes(), before)
            records = verify_hash_chained_jsonl(journal)
            self.assertFalse(
                any(
                    row["event"] in {"campaign_resumed", "campaign_blocked"}
                    for row in records
                )
            )

    async def test_crash_after_midpoint_resume_is_historically_verifiable(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        with stack:
            backend = FakeBackend(root)
            paused = await run_full_matrix_campaign(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                artifact_root=root,
                journal=journal,
                backend=backend,
                now=now + timedelta(minutes=1),
            )
            bundle = self._midpoint_bundle(
                paused=paused, campaign=campaign, policy=policy, root=root
            )
            original = __import__(
                "core.three_site_full_matrix_runner",
                fromlist=["_journal_event"],
            )._journal_event

            def crash_after_resume(path, identity, *, event, **fields):
                record = original(path, identity, event=event, **fields)
                if event == "campaign_resumed":
                    raise KeyboardInterrupt("simulated crash after durable resume")
                return record

            with patch(
                "core.three_site_full_matrix_runner._journal_event",
                side_effect=crash_after_resume,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    await run_full_matrix_campaign(
                        campaign=campaign,
                        approver_policy=policy,
                        bound_artifacts=bound,
                        artifact_root=root,
                        journal=journal,
                        backend=backend,
                        midpoint_refresh_bundle=bundle,
                        now=now + timedelta(minutes=1),
                    )
            self.assertEqual(
                verify_hash_chained_jsonl(journal)[-1]["event"],
                "campaign_resumed",
            )
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
            records = verify_hash_chained_jsonl(journal)
            self.assertEqual(
                sum(row["event"] == "campaign_resumed" for row in records), 1
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

    async def test_preflight_cannot_pass_with_a_false_typed_assertion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            identity = CampaignIdentity(
                campaign_id="11111111-1111-4111-8111-111111111111",
                gate_group_id="22222222-2222-4222-8222-222222222222",
                execution_class=SHARED_HOST_SAFE,
                campaign_hash="b" * 64,
                release_sha="a" * 40,
                activation_sha="a" * 40,
                repetitions=2,
            )
            operation_id = _operation_id(identity, "preflight")
            result = await FakeBackend(root).preflight(
                identity, operation_id=operation_id
            )
            path = root / result["artifact_path"]
            evidence = json.loads(path.read_text())
            assertion = next(
                item for item in evidence["assertions"]
                if item["name"] == "topology_ready"
            )
            assertion["observed"] = False
            body = canonical_json_bytes(evidence) + b"\n"
            path.write_bytes(body)
            path.chmod(0o600)
            digest = hashlib.sha256(body).hexdigest()
            result.update(
                artifact_sha256=digest,
                artifact_size=len(body),
                evidence_hash=digest,
            )
            with self.assertRaisesRegex(
                FullMatrixCampaignError, "assertion did not pass"
            ):
                _validate_preflight(
                    result, identity, operation_id=operation_id,
                    artifact_root=root,
                )

    async def test_preflight_effect_crash_replays_same_journaled_operation(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()

        class CrashAfterPreflightEffect(FakeBackend):
            async def preflight(self, identity, *, operation_id):  # noqa: ANN001
                await super().preflight(identity, operation_id=operation_id)
                raise KeyboardInterrupt("synthetic crash after preflight effect")

        with stack:
            with self.assertRaises(KeyboardInterrupt):
                await run_full_matrix_campaign(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    artifact_root=root,
                    journal=journal,
                    backend=CrashAfterPreflightEffect(root),
                    now=now + timedelta(minutes=1),
                )
            artifact_before = (root / "operation-preflight.json").read_bytes()
            resumed = FakeBackend(root)
            report = await self._run_to_completion(
                campaign=campaign,
                policy=policy,
                bound=bound,
                root=root,
                journal=journal,
                backend=resumed,
                now=now + timedelta(minutes=1),
            )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(resumed.preflight_calls, 1)
            self.assertEqual(
                (root / "operation-preflight.json").read_bytes(),
                artifact_before,
            )
            records = verify_hash_chained_jsonl(journal, label="test journal")
            starts = [
                row for row in records
                if row["event"] == "campaign_started"
                and row["operation_kind"] == "preflight"
            ]
            passes = [
                row for row in records
                if row["event"] == "operation_passed"
                and row["operation_kind"] == "preflight"
            ]
            self.assertEqual(len(starts), 1)
            self.assertEqual(len(passes), 1)
            self.assertEqual(starts[0]["operation_id"], passes[0]["operation_id"])

    async def test_load_scenario_cannot_self_report_300_rps_when_observed_is_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            identity = CampaignIdentity(
                campaign_id="11111111-1111-4111-8111-111111111111",
                gate_group_id="22222222-2222-4222-8222-222222222222",
                execution_class=SHARED_HOST_SAFE,
                campaign_hash="b" * 64,
                release_sha="a" * 40,
                activation_sha="a" * 40,
                repetitions=2,
            )
            phase = next(
                name for name, scenarios in PHASE_SCENARIOS.items()
                if "three_hundred_rps_fifty_fifty" in scenarios
            )
            scenario_id = "three_hundred_rps_fifty_fifty"
            operation_id = _operation_id(
                identity, "scenario", phase=phase, scenario_id=scenario_id,
                iteration=1, attempt=1,
            )
            result = await FakeBackend(root).execute_scenario(
                identity, phase=phase, scenario_id=scenario_id,
                iteration=1, attempt=1, operation_id=operation_id,
            )
            path = root / result["artifact_path"]
            evidence = json.loads(path.read_text())
            assertion = next(
                item for item in evidence["assertions"]
                if item["name"] == "expected_outcome"
            )
            assertion["expected"] = {"requests_per_second": 300}
            assertion["observed"] = {"requests_per_second": 0}
            with self.assertRaisesRegex(
                FullMatrixCampaignError, "expected outcome differs"
            ):
                verify_scenario_evidence(
                    evidence,
                    campaign={
                        "campaign_id": identity.campaign_id,
                        "release_sha": identity.release_sha,
                        "activation_sha": identity.activation_sha,
                    },
                    campaign_hash=identity.campaign_hash,
                    phase=phase,
                    scenario_id=scenario_id,
                    iteration=1,
                    attempt=1,
                    operation_id=operation_id,
                    artifact_root=root,
                )

    async def test_operation_artifact_reuse_is_rejected_by_standalone_verifier(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        with stack:
            backend = FakeBackend(root)
            report = await self._run_to_completion(
                campaign=campaign,
                policy=policy,
                bound=bound,
                root=root,
                journal=journal,
                backend=backend,
                now=now + timedelta(minutes=1),
            )
            self.assertEqual(report["status"], "passed")
            records = verify_hash_chained_jsonl(journal, label="test journal")
            preflight = next(
                row for row in records
                if row["event"] == "operation_passed"
                and row["operation_kind"] == "preflight"
            )["result"]
            finalized = next(
                row for row in records if row["event"] == "campaign_finalized"
            )
            finalize_pass = next(
                row for row in records
                if row["event"] == "operation_passed"
                and row["operation_kind"] == "finalize"
            )
            reused_fields = {
                "artifact_path": preflight["artifact_path"],
                "artifact_sha256": preflight["artifact_sha256"],
                "artifact_size": preflight["artifact_size"],
                "evidence_hash": preflight["evidence_hash"],
            }
            finalize_pass["result"].update(**reused_fields)
            finalized["result"].update(**reused_fields)
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
                for phase in campaign["required_phases"]
            ]
            with self.assertRaisesRegex(
                FullMatrixCampaignError,
                "reused|identity/status|midpoint refresh proof",
            ):
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
            before = journal.read_bytes()
            retried = FakeBackend(root)
            with self.assertRaisesRegex(FullMatrixRunnerError, "new campaign"):
                await run_full_matrix_campaign(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    artifact_root=root,
                    journal=journal,
                    backend=retried,
                    now=now + timedelta(hours=49),
                )
            self.assertEqual(journal.read_bytes(), before)
            self.assertEqual(retried.preflight_calls, 0)
            self.assertEqual(retried.finalize_calls, 0)
            self.assertEqual(retried.executed, [])
            self.assertEqual(retried.recovered, [])
            self.assertEqual(retried.cleanups, [])

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
            report = await self._run_to_completion(
                campaign=campaign,
                policy=policy,
                bound=bound,
                root=root,
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
            report = await self._run_to_completion(
                campaign=campaign,
                policy=policy,
                bound=bound,
                root=root,
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
            await self._run_to_completion(
                campaign=campaign,
                policy=policy,
                bound=bound,
                root=root,
                journal=journal,
                backend=FakeBackend(root),
                now=now + timedelta(minutes=1),
            )
            before = journal.read_bytes()
            resumed = FakeBackend(root)
            with self.assertRaisesRegex(FullMatrixRunnerError, "immutable"):
                await run_full_matrix_campaign(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    artifact_root=root,
                    journal=journal,
                    backend=resumed,
                    now=now + timedelta(hours=49),
                )
            self.assertEqual(journal.read_bytes(), before)
            self.assertEqual(resumed.preflight_calls, 0)
            self.assertEqual(resumed.finalize_calls, 0)
            self.assertEqual(resumed.executed, [])
            self.assertEqual(resumed.recovered, [])
            self.assertEqual(resumed.cleanups, [])

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
            paused = await run_full_matrix_campaign(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                artifact_root=root,
                journal=journal,
                backend=first_backend,
                now=now + timedelta(minutes=1),
            )
            self.assertEqual(paused["status"], "paused")
            midpoint_bundle = self._midpoint_bundle(
                paused=paused,
                campaign=campaign,
                policy=policy,
                root=root,
            )
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
                        midpoint_refresh_bundle=midpoint_bundle,
                        now=now + timedelta(minutes=1),
                    )
            self.assertEqual(
                verify_hash_chained_jsonl(journal)[-1]["event"],
                "campaign_finalized",
            )

            resumed_backend = FakeBackend(root)
            report = await self._run_to_completion(
                campaign=campaign,
                policy=policy,
                bound=bound,
                root=root,
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

    async def test_expired_finalized_checkpoint_is_read_only_and_idempotent(self):
        stack, now, campaign, policy, bound, root, journal = self._inputs()
        with stack:
            paused = await run_full_matrix_campaign(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                artifact_root=root,
                journal=journal,
                backend=FakeBackend(root),
                now=now + timedelta(minutes=1),
            )
            midpoint_bundle = self._midpoint_bundle(
                paused=paused,
                campaign=campaign,
                policy=policy,
                root=root,
            )
            original_journal_event = __import__(
                "core.three_site_full_matrix_runner",
                fromlist=["_journal_event"],
            )._journal_event

            def crash_before_completion(path, identity, *, event, **fields):
                if event == "campaign_completed":
                    raise KeyboardInterrupt(
                        "simulated controller death after finalization"
                    )
                return original_journal_event(
                    path,
                    identity,
                    event=event,
                    **fields,
                )

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
                        backend=FakeBackend(root),
                        midpoint_refresh_bundle=midpoint_bundle,
                        now=now + timedelta(minutes=1),
                    )
            self.assertEqual(
                verify_hash_chained_jsonl(journal)[-1]["event"],
                "campaign_finalized",
            )
            before = journal.read_bytes()
            for attempt in range(2):
                with self.subTest(attempt=attempt):
                    resumed = FakeBackend(root)
                    report = await run_full_matrix_campaign(
                        campaign=campaign,
                        approver_policy=policy,
                        bound_artifacts=bound,
                        artifact_root=root,
                        journal=journal,
                        backend=resumed,
                        now=now + timedelta(hours=49),
                    )
                    self.assertEqual(report["status"], "passed")
                    self.assertEqual(journal.read_bytes(), before)
                    self.assertEqual(resumed.preflight_calls, 0)
                    self.assertEqual(resumed.finalize_calls, 0)
                    self.assertEqual(resumed.executed, [])
                    self.assertEqual(resumed.recovered, [])
                    self.assertEqual(resumed.cleanups, [])

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
            report = await self._run_to_completion(
                campaign=campaign,
                policy=policy,
                bound=bound,
                root=root,
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
            self.assertEqual(expired.preflight_calls, 0)
            self.assertEqual(expired.finalize_calls, 0)
            self.assertEqual(expired.executed, [])
            self.assertEqual(
                verify_hash_chained_jsonl(journal)[-1]["event"], "campaign_blocked"
            )
            before = journal.read_bytes()
            retried = FakeBackend(root)
            with self.assertRaisesRegex(FullMatrixRunnerError, "new campaign"):
                await run_full_matrix_campaign(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    artifact_root=root,
                    journal=journal,
                    backend=retried,
                    now=now + timedelta(hours=50),
                )
            self.assertEqual(journal.read_bytes(), before)
            self.assertEqual(retried.preflight_calls, 0)
            self.assertEqual(retried.finalize_calls, 0)
            self.assertEqual(retried.executed, [])
            self.assertEqual(retried.recovered, [])
            self.assertEqual(retried.cleanups, [])

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
