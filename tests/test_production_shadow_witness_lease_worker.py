from __future__ import annotations

import argparse
import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from core.writer_witness_contract import sign_witness_lease_proof
from scripts import production_shadow_witness_lease_worker as MODULE


CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
OPERATION_ID = "22222222-2222-4222-8222-222222222222"
RELEASE_SHA = "a" * 40
RELEASE_TREE_SHA = "b" * 40
NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def digest(seed: str) -> str:
    return hashlib.sha256(seed.encode("ascii")).hexdigest()


class TickClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        self.value += timedelta(seconds=1)
        return self.value


class SequenceClock:
    def __init__(self, *values: datetime) -> None:
        self.values = list(values)

    def __call__(self) -> datetime:
        if not self.values:
            raise AssertionError("test clock was called too often")
        return self.values.pop(0)


class FakeBackend:
    def __init__(
        self,
        *,
        role: str = "webapp_fi",
        now: datetime = NOW,
        duration: int = 180,
        bad_signature: bool = False,
        short_lifetime: bool = False,
        foreign_lease: bool = False,
        fail_after_witness_commit: bool = False,
    ) -> None:
        self.role = role
        self.now = now
        self.duration = duration
        self.bad_signature = bad_signature
        self.short_lifetime = short_lifetime
        self.foreign_lease = foreign_lease
        self.fail_after_witness_commit = fail_after_witness_commit
        self.private = Ed25519PrivateKey.generate()
        public = self.private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.public = base64.b64encode(public).decode("ascii")
        self.private_b64 = base64.b64encode(
            self.private.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
        ).decode("ascii")
        self.business = digest(f"{role}-business")
        self.database = digest(f"{role}-database")
        self.business_rows = 42
        self.transition_id = (
            "33333333-3333-4333-8333-333333333333"
        )
        self.local_proof: dict | None = None
        self.witness_proof: dict | None = None
        self.receipts: dict[str, dict] = {}
        self.acquire_calls: list[str] = []
        self.renew_calls: list[str] = []
        self.status_calls: list[str] = []
        self.reconcile_calls: list[str] = []
        self.created_lease_count = 0
        if foreign_lease:
            self.local_proof = self._proof(
                lease_id="foreign-lease",
                transition_id="foreign-transition",
                issued_at=now - timedelta(seconds=10),
                expires_at=now + timedelta(seconds=120),
            )

    def reconcile_authorized_oneoff(
        self,
        *,
        request_id: str,
        authority,
    ) -> None:
        if not callable(authority):
            raise AssertionError("test reconciliation authority is unavailable")
        self.reconcile_calls.append(request_id)

    def _proof(
        self,
        *,
        lease_id: str,
        transition_id: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> dict:
        proof = sign_witness_lease_proof(
            holder_site="webapp_fi",
            writer_epoch=1,
            lease_id=lease_id,
            issued_at=issued_at,
            expires_at=expires_at,
            witness_transition_id=transition_id,
            private_key_base64=self.private_b64,
        )
        if self.bad_signature:
            proof["signature"] = base64.b64encode(b"x" * 64).decode(
                "ascii"
            )
        return proof

    def witness_public_key(self) -> str:
        return self.public

    def writer_state(self) -> MODULE.WriterState:
        proof = self.local_proof
        return MODULE.WriterState(
            role=self.role,
            singleton_count=1,
            control_state=(
                "active" if self.role == "webapp_fi" else "fenced"
            ),
            active_site=(
                "webapp_fi" if self.role == "webapp_fi" else None
            ),
            writer_epoch=1,
            transition_id=self.transition_id,
            witness_lease_id=(
                proof["lease_id"] if proof is not None else None
            ),
            witness_lease_issued_at=(
                proof["issued_at"] if proof is not None else None
            ),
            witness_lease_expires_at=(
                proof["expires_at"] if proof is not None else None
            ),
            witness_proof_hash=(
                MODULE._proof_hash(proof)
                if proof is not None
                else None
            ),
            witness_transition_id=(
                proof["witness_transition_id"]
                if proof is not None
                else None
            ),
            business_state_sha256=self.business,
            business_row_count=self.business_rows,
            database_identity_sha256=self.database,
        )

    def runtime_surface(self) -> MODULE.RuntimeSurface:
        return MODULE.RuntimeSurface(
            database_running=True,
            operation_oneoff_count=0,
            api_running_count=0,
            effect_running_count=0,
            bot_running_count=0,
            public_service_running_count=0,
            redis_running_count=0,
            other_running_count=0,
        )

    def acquire(
        self,
        *,
        campaign_id: str,
        request_id: str,
        release_sha: str,
        duration_seconds: int,
    ) -> dict:
        self.acquire_calls.append(request_id)
        self.assert_bindings(
            campaign_id=campaign_id,
            release_sha=release_sha,
            duration_seconds=duration_seconds,
        )
        if request_id not in self.receipts:
            self.created_lease_count += 1
            issued = self.now + timedelta(seconds=2)
            lifetime = 40 if self.short_lifetime else duration_seconds
            self.witness_proof = self._proof(
                lease_id="lease-operation-bound",
                transition_id="witness-acquire-transition",
                issued_at=issued,
                expires_at=issued + timedelta(seconds=lifetime),
            )
            self.receipts[request_id] = self.witness_proof
            if self.fail_after_witness_commit:
                self.fail_after_witness_commit = False
                raise MODULE.WitnessLeaseWorkerError(
                    "simulated response loss before local import"
                )
        proof = self.receipts[request_id]
        self.local_proof = proof
        return {"proof": proof}

    def renew(
        self,
        *,
        request_id: str,
        release_sha: str,
        duration_seconds: int,
    ) -> dict:
        self.renew_calls.append(request_id)
        self.assert_bindings(
            campaign_id=CAMPAIGN_ID,
            release_sha=release_sha,
            duration_seconds=duration_seconds,
        )
        if self.local_proof is None:
            raise MODULE.WitnessLeaseWorkerError("no local lease")
        if request_id not in self.receipts:
            issued = self.now + timedelta(seconds=2)
            proof = self._proof(
                lease_id=self.local_proof["lease_id"],
                transition_id=f"renew-{request_id}",
                issued_at=issued,
                expires_at=issued + timedelta(seconds=duration_seconds),
            )
            self.receipts[request_id] = proof
        proof = self.receipts[request_id]
        self.witness_proof = proof
        self.local_proof = proof
        return {"proof": proof}

    def witness_status(
        self,
        *,
        request_id: str,
        release_sha: str,
    ) -> dict:
        self.status_calls.append(request_id)
        if release_sha != RELEASE_SHA or self.witness_proof is None:
            raise MODULE.WitnessLeaseWorkerError("status mismatch")
        proof = self.witness_proof
        return {
            "status": "ok",
            "request_id": request_id,
            "observer_site": "webapp_fi",
            "holder_site": proof["holder_site"],
            "writer_epoch": proof["writer_epoch"],
            "lease_id": proof["lease_id"],
            "lease_status": "leased",
            "expires_at": proof["expires_at"],
            "lease_live": True,
            "witness_receipt_hash": digest(
                f"status-{request_id}-{proof['witness_transition_id']}"
            ),
        }

    @staticmethod
    def assert_bindings(
        *,
        campaign_id: str,
        release_sha: str,
        duration_seconds: int,
    ) -> None:
        if (
            campaign_id != CAMPAIGN_ID
            or release_sha != RELEASE_SHA
            or duration_seconds != 180
        ):
            raise AssertionError("test backend binding differs")


def policy(action: str) -> dict:
    return {
        "lease_duration_seconds": 180,
        "minimum_remaining_seconds": 60,
        "safety_margin_seconds": 15,
        "max_clock_skew_seconds": 5,
        "witness_reason": (
            f"initial three-site staging migration campaign {CAMPAIGN_ID}"
            if action == "acquire"
            else "automatic active-writer lease renewal"
            if action == "renew"
            else "read-only Witness lease status"
        ),
        "local_operator": (
            f"staging-migration:{CAMPAIGN_ID}"
            if action == "acquire"
            else "witness-renewer:webapp_fi"
            if action == "renew"
            else "controller-readback"
        ),
    }


def request(
    root: Path,
    backend: FakeBackend,
    *,
    role: str = "webapp_fi",
    action: str = "acquire",
    renewal_sequence: int = 0,
) -> dict:
    manifest = root / role / "restore-role-manifest.json"
    return MODULE.build_request(
        campaign_id=CAMPAIGN_ID,
        operation_id=OPERATION_ID,
        role=role,
        action=action,
        renewal_sequence=renewal_sequence,
        release_sha=RELEASE_SHA,
        release_tree_sha=RELEASE_TREE_SHA,
        expected_host=(
            "65.109.220.59"
            if role == "webapp_fi"
            else "95.38.164.29"
        ),
        controller_manifest_sha256=digest("manifest"),
        controller_plan_sha256=digest("plan"),
        approval_sha256=digest("approval"),
        role_manifest_path=manifest,
        role_manifest_sha256=digest(f"{role}-role-manifest"),
        worker_sha256=digest("worker"),
        bootstrap_sha256=digest("bootstrap"),
        status_sha256=digest("status"),
        client_sha256=digest("client"),
        contract_sha256=digest("contract"),
        control_protocol_sha256=digest("control"),
        witness_public_key=backend.public,
        witness_public_key_sha256=hashlib.sha256(
            backend.public.encode("ascii")
        ).hexdigest(),
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        output_root=manifest.parent / "witness-lease",
        lease_policy=policy(action),
    )


class WitnessLeaseWorkerTests(unittest.TestCase):
    def apply(
        self,
        value: dict,
        backend: FakeBackend,
        *,
        authority=lambda _checkpoint: True,
        now: datetime = NOW,
    ) -> dict:
        return MODULE.execute(
            value,
            apply=True,
            confirm=MODULE.confirmation_phrase(value),
            authority=authority,
            backend=backend,
            now=now,
            clock=TickClock(now),
        )

    def oneoff_cleanup_backend(self, *, state: dict):
        request_id = "44444444-4444-4444-8444-444444444444"
        project = "three-site-operation"
        image = "sha256:" + "1" * 64
        container_id = "c" * 64
        database_id = "d" * 64
        backend = object.__new__(MODULE.ExactReleaseBackend)
        backend.RESTORE = types.SimpleNamespace(
            DOCKER_BASE=("/usr/bin/docker",)
        )
        backend.manifest = types.SimpleNamespace(
            paths=types.SimpleNamespace(project_name=project),
            operation_id=OPERATION_ID,
            app_image_id=image,
        )
        backend.request = {"transition_request_id": request_id}
        backend.role = "webapp_fi"
        backend.database_id = None
        name = backend._oneoff_name(request_id)
        calls: list[list[str]] = []
        events: list[str] = []
        present = True

        def run(arguments, **_kwargs):
            nonlocal present
            command = list(arguments)
            calls.append(command)
            if "ps" in command:
                return (
                    f"{container_id}\n".encode("ascii")
                    if present
                    else b""
                )
            if "rm" in command:
                events.append("rm")
                present = False
            return b""

        backend._run = run
        backend._json_output = mock.Mock(
            return_value=[
                {
                    "Id": container_id,
                    "Name": f"/{name}",
                    "Image": image,
                    "Config": {
                        "Labels": {
                            "com.docker.compose.project": project,
                            "com.docker.compose.service": (
                                "webapp_fi_writer_control"
                            ),
                            "com.docker.compose.oneoff": "True",
                            "trading-bot.production.operation-id": (
                                OPERATION_ID
                            ),
                        }
                    },
                    "Mounts": [],
                    "State": copy.deepcopy(state),
                }
            ]
        )
        backend._database_container_id = mock.Mock(
            return_value=database_id
        )
        backend.runtime_surface = mock.Mock(
            return_value=MODULE.RuntimeSurface(
                database_running=True,
                operation_oneoff_count=0,
                api_running_count=0,
                effect_running_count=0,
                bot_running_count=0,
                public_service_running_count=0,
                redis_running_count=0,
                other_running_count=0,
            )
        )
        return backend, request_id, calls, events

    def test_request_ids_are_deterministic_and_action_bound(self):
        first = MODULE.deterministic_request_id(
            operation_id=OPERATION_ID,
            action="acquire",
            renewal_sequence=0,
            purpose="transition",
        )
        self.assertEqual(
            first,
            MODULE.deterministic_request_id(
                operation_id=OPERATION_ID,
                action="acquire",
                renewal_sequence=0,
                purpose="transition",
            ),
        )
        self.assertNotEqual(
            first,
            MODULE.deterministic_request_id(
                operation_id=OPERATION_ID,
                action="renew",
                renewal_sequence=1,
                purpose="transition",
            ),
        )

    def test_plan_is_non_mutating_and_binds_explicit_lifetime(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            value = request(Path(directory), backend)
            result = MODULE.plan(value, now=NOW)
        self.assertTrue(result["mutates_production"])
        self.assertFalse(result["production_contacted"])
        self.assertEqual(
            result["lease_policy"]["lease_duration_seconds"], 180
        )
        self.assertIn(value["request_sha256"], result["required_confirmation"])

    def test_acquire_imports_one_signed_live_lease_without_business_write(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            value = request(Path(directory), backend)
            result = self.apply(value, backend)
            replay = self.apply(value, backend)
        self.assertEqual(result, replay)
        self.assertEqual(backend.created_lease_count, 1)
        self.assertEqual(len(backend.acquire_calls), 1)
        self.assertTrue(result["witness_signature_verified"])
        self.assertEqual(result["singleton_live_lease_count"], 1)
        self.assertEqual(result["lease_epoch"], 1)
        self.assertEqual(result["business_write_count"], 0)
        self.assertGreaterEqual(result["remaining_lifetime_seconds"], 60)

    def test_response_loss_after_witness_commit_replays_same_request(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend(fail_after_witness_commit=True)
            value = request(Path(directory), backend)
            with self.assertRaisesRegex(
                MODULE.WitnessLeaseWorkerError,
                "response loss",
            ):
                self.apply(value, backend)
            result = self.apply(value, backend)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(backend.created_lease_count, 1)
        self.assertEqual(
            backend.acquire_calls,
            [value["transition_request_id"]] * 2,
        )

    def test_controller_eof_after_local_import_recovers_without_second_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            value = request(Path(directory), backend)
            denied = False

            def authority(checkpoint: str) -> bool:
                nonlocal denied
                if checkpoint == "before-fresh-witness-status" and not denied:
                    denied = True
                    return False
                return True

            with self.assertRaises(MODULE.WitnessLeaseCancellation):
                self.apply(value, backend, authority=authority)
            result = self.apply(value, backend)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(backend.created_lease_count, 1)
        self.assertEqual(
            backend.acquire_calls,
            [value["transition_request_id"]] * 2,
        )

    def test_result_published_before_journal_completion_recovers_locally(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            value = request(Path(directory), backend)

            def authority(checkpoint: str) -> bool:
                return checkpoint != "before-journal-completion"

            with self.assertRaises(MODULE.WitnessLeaseCancellation):
                self.apply(value, backend, authority=authority)
            calls = (
                len(backend.acquire_calls),
                len(backend.status_calls),
            )
            result = self.apply(value, backend)
            journal_path, _result_path = MODULE._journal_paths(value)
            journal = json.loads(journal_path.read_text("ascii"))
        self.assertEqual(result["status"], "verified")
        self.assertEqual(
            calls,
            (
                len(backend.acquire_calls),
                len(backend.status_calls) - 1,
            ),
        )
        self.assertEqual(
            backend.status_calls[-1],
            MODULE._replay_status_request_id(value),
        )
        self.assertNotEqual(
            backend.status_calls[-1],
            value["status_request_id"],
        )
        self.assertEqual(journal["status"], "completed")

    def test_started_journal_result_recovery_requires_fresh_backend_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            value = request(Path(directory), backend)

            def authority(checkpoint: str) -> bool:
                return checkpoint != "before-journal-completion"

            with self.assertRaises(MODULE.WitnessLeaseCancellation):
                self.apply(value, backend, authority=authority)
            transition_calls = list(backend.acquire_calls)
            status_calls = list(backend.status_calls)
            with mock.patch.object(
                MODULE,
                "ExactReleaseBackend",
                return_value=backend,
            ) as constructor:
                result = MODULE.execute(
                    value,
                    apply=True,
                    confirm=MODULE.confirmation_phrase(value),
                    authority=lambda _checkpoint: True,
                    backend=None,
                    now=NOW,
                    clock=TickClock(NOW),
                )
        self.assertEqual(result["status"], "verified")
        self.assertEqual(backend.acquire_calls, transition_calls)
        constructor.assert_called_once_with(value)
        self.assertEqual(
            backend.status_calls,
            status_calls + [MODULE._replay_status_request_id(value)],
        )

    def test_completed_replay_rejects_an_expired_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            value = request(Path(directory), backend)
            self.apply(value, backend)
            journal_path, _result_path = MODULE._journal_paths(value)
            completed = json.loads(journal_path.read_text("ascii"))
            reconcile_calls = list(backend.reconcile_calls)
            with self.assertRaisesRegex(
                MODULE.WitnessLeaseWorkerError,
                "signed Witness proof",
            ):
                self.apply(
                    value,
                    backend,
                    now=NOW + timedelta(minutes=4),
                )
            observed = json.loads(journal_path.read_text("ascii"))
        self.assertEqual(observed, completed)
        self.assertEqual(backend.status_calls, [value["status_request_id"]])
        self.assertEqual(backend.reconcile_calls, reconcile_calls)

    def test_recovery_cannot_complete_after_the_lease_expires(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            value = request(Path(directory), backend)
            with self.assertRaises(MODULE.WitnessLeaseCancellation):
                self.apply(
                    value,
                    backend,
                    authority=lambda checkpoint: (
                        checkpoint != "before-journal-completion"
                    ),
                )
            journal_path, _result_path = MODULE._journal_paths(value)
            with self.assertRaisesRegex(
                MODULE.WitnessLeaseWorkerError,
                "signed Witness proof",
            ):
                self.apply(
                    value,
                    backend,
                    now=NOW + timedelta(minutes=4),
                )
            journal = json.loads(journal_path.read_text("ascii"))
        self.assertEqual(journal["status"], "started")
        self.assertIsNone(journal["result_sha256"])

    def test_replay_fails_closed_if_request_expires_after_status_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            value = request(Path(directory), backend)
            self.apply(value, backend)
            journal_path, _result_path = MODULE._journal_paths(value)
            completed = json.loads(journal_path.read_text("ascii"))
            with self.assertRaisesRegex(
                MODULE.WitnessLeaseWorkerError,
                "request is stale",
            ):
                MODULE.execute(
                    value,
                    apply=True,
                    confirm=MODULE.confirmation_phrase(value),
                    authority=lambda _checkpoint: True,
                    backend=backend,
                    now=NOW,
                    clock=SequenceClock(
                        NOW + timedelta(seconds=1),
                        NOW + timedelta(seconds=2),
                        NOW + timedelta(minutes=11),
                    ),
                )
            observed = json.loads(journal_path.read_text("ascii"))
        self.assertEqual(observed, completed)
        self.assertEqual(
            backend.status_calls[-1],
            MODULE._replay_status_request_id(value),
        )

    def test_completed_replay_rejects_a_fresh_status_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            value = request(Path(directory), backend)
            self.apply(value, backend)
            backend.witness_proof = backend._proof(
                lease_id="different-live-lease",
                transition_id="different-live-transition",
                issued_at=NOW,
                expires_at=NOW + timedelta(seconds=180),
            )
            with self.assertRaisesRegex(
                MODULE.WitnessLeaseWorkerError,
                "does not exactly read back",
            ):
                self.apply(value, backend)
        self.assertEqual(
            backend.status_calls[-1],
            MODULE._replay_status_request_id(value),
        )

    def test_completed_replay_authority_denial_returns_no_stale_result(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            value = request(Path(directory), backend)
            self.apply(value, backend)
            journal_path, _result_path = MODULE._journal_paths(value)
            completed = json.loads(journal_path.read_text("ascii"))
            with self.assertRaises(MODULE.WitnessLeaseCancellation):
                self.apply(
                    value,
                    backend,
                    authority=lambda checkpoint: (
                        checkpoint != "before-replay-fresh-witness-status"
                    ),
                )
            observed = json.loads(journal_path.read_text("ascii"))
        self.assertEqual(observed, completed)
        self.assertEqual(backend.status_calls, [value["status_request_id"]])

    def test_completed_replay_rejects_changed_fi_local_state(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            value = request(Path(directory), backend)
            self.apply(value, backend)
            backend.business = digest("changed-fi-business")
            with self.assertRaisesRegex(
                MODULE.WitnessLeaseWorkerError,
                "fresh replay local readback differs",
            ):
                self.apply(value, backend)
        self.assertEqual(backend.status_calls, [value["status_request_id"]])

    def test_completed_ir_replay_rejects_changed_fenced_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend(role="webapp_ir")
            value = request(
                Path(directory),
                backend,
                role="webapp_ir",
                action="readback",
            )
            self.apply(value, backend)
            backend.business = digest("changed-ir-business")
            with self.assertRaisesRegex(
                MODULE.WitnessLeaseWorkerError,
                "fresh replay local readback differs",
            ):
                self.apply(value, backend)
        self.assertEqual(backend.status_calls, [])

    def test_forged_rehashed_proof_cannot_complete_started_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            value = request(Path(directory), backend)

            def authority(checkpoint: str) -> bool:
                return checkpoint != "before-journal-completion"

            with self.assertRaises(MODULE.WitnessLeaseCancellation):
                self.apply(value, backend, authority=authority)
            journal_path, result_path = MODULE._journal_paths(value)
            forged = json.loads(result_path.read_text("ascii"))
            forged["signed_proof"]["signature"] = base64.b64encode(
                b"x" * 64
            ).decode("ascii")
            proof_hash = MODULE._proof_hash(forged["signed_proof"])
            forged["signed_proof_sha256"] = proof_hash
            forged["after_state"]["witness_proof_hash"] = proof_hash
            forged["after_state"]["state_sha256"] = MODULE._sha256(
                MODULE._canonical_json(
                    {
                        key: item
                        for key, item in forged["after_state"].items()
                        if key != "state_sha256"
                    }
                )
            )
            forged["lease_readback_sha256"] = MODULE._sha256(
                MODULE._canonical_json(
                    {
                        "role": "webapp_fi",
                        "signed_proof_sha256": proof_hash,
                        "witness_status_receipt_sha256": forged[
                            "witness_status"
                        ]["witness_receipt_hash"],
                        "local_writer_state_sha256": forged[
                            "after_state"
                        ]["state_sha256"],
                        "lease_id": forged["signed_proof"]["lease_id"],
                        "writer_epoch": forged["signed_proof"][
                            "writer_epoch"
                        ],
                        "expires_at": forged["signed_proof"][
                            "expires_at"
                        ],
                    }
                )
            )
            forged["response_sha256"] = MODULE._sha256(
                MODULE._canonical_json(
                    {
                        key: item
                        for key, item in forged.items()
                        if key != "response_sha256"
                    }
                )
            )
            result_path.write_bytes(
                MODULE._canonical_json(forged) + b"\n"
            )
            with mock.patch.object(
                MODULE,
                "ExactReleaseBackend",
                side_effect=AssertionError("backend must not be constructed"),
            ):
                with self.assertRaisesRegex(
                    MODULE.WitnessLeaseWorkerError,
                    "signed Witness proof",
                ):
                    MODULE.execute(
                        value,
                        apply=True,
                        confirm=MODULE.confirmation_phrase(value),
                        authority=lambda _checkpoint: True,
                        backend=None,
                        now=NOW,
                        clock=TickClock(NOW),
                    )
            journal = json.loads(journal_path.read_text("ascii"))
        self.assertEqual(journal["status"], "started")
        self.assertIsNone(journal["result_sha256"])

    def test_rehashed_result_with_copied_tail_cannot_change_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            value = request(Path(directory), backend)

            def authority(checkpoint: str) -> bool:
                return checkpoint != "before-journal-completion"

            with self.assertRaises(MODULE.WitnessLeaseCancellation):
                self.apply(value, backend, authority=authority)
            journal_path, result_path = MODULE._journal_paths(value)
            forged = json.loads(result_path.read_text("ascii"))
            forged["witness_status"]["witness_receipt_hash"] = digest(
                "forged-status-receipt"
            )
            forged["lease_readback_sha256"] = MODULE._sha256(
                MODULE._canonical_json(
                    {
                        "role": "webapp_fi",
                        "signed_proof_sha256": forged[
                            "signed_proof_sha256"
                        ],
                        "witness_status_receipt_sha256": forged[
                            "witness_status"
                        ]["witness_receipt_hash"],
                        "local_writer_state_sha256": forged[
                            "after_state"
                        ]["state_sha256"],
                        "lease_id": forged["signed_proof"]["lease_id"],
                        "writer_epoch": forged["signed_proof"][
                            "writer_epoch"
                        ],
                        "expires_at": forged["signed_proof"][
                            "expires_at"
                        ],
                    }
                )
            )
            forged["response_sha256"] = MODULE._sha256(
                MODULE._canonical_json(
                    {
                        key: item
                        for key, item in forged.items()
                        if key != "response_sha256"
                    }
                )
            )
            result_path.write_bytes(
                MODULE._canonical_json(forged) + b"\n"
            )
            with mock.patch.object(
                MODULE,
                "ExactReleaseBackend",
                side_effect=AssertionError("backend must not be constructed"),
            ):
                with self.assertRaisesRegex(
                    MODULE.WitnessLeaseWorkerError,
                    "journal semantics",
                ):
                    MODULE.execute(
                        value,
                        apply=True,
                        confirm=MODULE.confirmation_phrase(value),
                        authority=lambda _checkpoint: True,
                        backend=None,
                        now=NOW,
                        clock=TickClock(NOW),
                    )
            journal = json.loads(journal_path.read_text("ascii"))
        self.assertEqual(journal["status"], "started")
        self.assertIsNone(journal["result_sha256"])

    def test_fresh_foreign_local_lease_blocks_before_acquire(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend(foreign_lease=True)
            value = request(Path(directory), backend)
            with self.assertRaisesRegex(
                MODULE.WitnessLeaseWorkerError,
                "foreign lease",
            ):
                self.apply(value, backend)
        self.assertEqual(backend.acquire_calls, [])

    def test_bad_signature_and_short_lifetime_fail_closed(self):
        for kwargs, message in (
            ({"bad_signature": True}, "signed Witness proof"),
            ({"short_lifetime": True}, "signed Witness proof"),
        ):
            with self.subTest(kwargs=kwargs):
                with tempfile.TemporaryDirectory() as directory:
                    backend = FakeBackend(**kwargs)
                    value = request(Path(directory), backend)
                    with self.assertRaisesRegex(
                        MODULE.WitnessLeaseWorkerError,
                        message,
                    ):
                        self.apply(value, backend)

    def test_authority_cancel_before_transition_makes_no_remote_call(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            value = request(Path(directory), backend)
            with self.assertRaises(MODULE.WitnessLeaseCancellation):
                self.apply(
                    value,
                    backend,
                    authority=lambda checkpoint: (
                        checkpoint != "before-acquire-transition"
                    ),
                )
        self.assertEqual(backend.acquire_calls, [])

    def test_ir_readback_is_exact_fenced_non_holder(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend(role="webapp_ir")
            value = request(
                Path(directory),
                backend,
                role="webapp_ir",
                action="readback",
            )
            result = self.apply(value, backend)
        self.assertFalse(result["witness_signature_verified"])
        self.assertEqual(result["singleton_live_lease_count"], 0)
        self.assertEqual(result["before_state"], result["after_state"])
        self.assertEqual(backend.acquire_calls, [])
        self.assertEqual(backend.renew_calls, [])
        self.assertEqual(backend.status_calls, [])

    def test_renew_keeps_epoch_and_lease_but_advances_signed_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = FakeBackend()
            acquired = self.apply(request(root, backend), backend)
            old_proof = acquired["signed_proof"]
            backend.now += timedelta(seconds=1)
            renewed_request = request(
                root,
                backend,
                action="renew",
                renewal_sequence=1,
            )
            renewed = self.apply(renewed_request, backend)
        self.assertEqual(
            renewed["signed_proof"]["lease_id"],
            old_proof["lease_id"],
        )
        self.assertEqual(renewed["lease_epoch"], 1)
        self.assertNotEqual(
            renewed["signed_proof"]["witness_transition_id"],
            old_proof["witness_transition_id"],
        )
        self.assertGreater(
            MODULE._parse_any_timestamp(
                renewed["signed_proof"]["expires_at"],
                label="renewed",
            ),
            MODULE._parse_any_timestamp(
                old_proof["expires_at"],
                label="old",
            ),
        )

    def test_request_rejects_replay_parameter_and_policy_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            value = request(Path(directory), backend)
            for mutate in (
                lambda item: item.__setitem__(
                    "transition_request_id",
                    "44444444-4444-4444-8444-444444444444",
                ),
                lambda item: item["lease_policy"].__setitem__(
                    "minimum_remaining_seconds", 180
                ),
                lambda item: item["lease_policy"].__setitem__(
                    "witness_reason", "changed"
                ),
            ):
                candidate = copy.deepcopy(value)
                mutate(candidate)
                candidate["request_sha256"] = MODULE._request_digest(
                    candidate
                )
                with self.assertRaises(MODULE.WitnessLeaseWorkerError):
                    MODULE.validate_request(candidate, now=NOW)

    def test_request_rejects_cross_role_canonical_host(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            candidate = request(Path(directory), backend)
            candidate["expected_host"] = MODULE.CONTROL.ROLE_HOSTS[
                "webapp_ir"
            ]
            candidate["request_sha256"] = MODULE._request_digest(candidate)
            with self.assertRaisesRegex(
                MODULE.WitnessLeaseWorkerError,
                "release, host, or constraints",
            ):
                MODULE.validate_request(candidate, now=NOW)
        self.assertEqual(backend.acquire_calls, [])
        self.assertEqual(backend.renew_calls, [])

    def test_ir_readback_rejects_nonzero_sequence_with_valid_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend(role="webapp_ir")
            candidate = request(
                Path(directory),
                backend,
                role="webapp_ir",
                action="readback",
            )
            candidate["renewal_sequence"] = 1
            for purpose in ("transition", "status"):
                candidate[f"{purpose}_request_id"] = (
                    MODULE.deterministic_request_id(
                        operation_id=OPERATION_ID,
                        action="readback",
                        renewal_sequence=1,
                        purpose=purpose,
                    )
                )
            candidate["request_sha256"] = MODULE._request_digest(candidate)
            with self.assertRaisesRegex(
                MODULE.WitnessLeaseWorkerError,
                "action/role/sequence",
            ):
                MODULE.validate_request(candidate, now=NOW)
        self.assertEqual(backend.acquire_calls, [])
        self.assertEqual(backend.renew_calls, [])
        self.assertEqual(backend.status_calls, [])

    def test_partial_local_import_and_result_tamper_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            value = request(Path(directory), backend)
            result = self.apply(value, backend)
            partial = copy.deepcopy(result)
            partial["after_state"]["witness_transition_id"] = None
            partial["response_sha256"] = MODULE._sha256(
                MODULE._canonical_json(
                    {
                        key: item
                        for key, item in partial.items()
                        if key != "response_sha256"
                    }
                )
            )
            with self.assertRaisesRegex(
                MODULE.WitnessLeaseWorkerError,
                "partial",
            ):
                MODULE.validate_result(partial, request=value)

            tampered = copy.deepcopy(result)
            tampered["lease_epoch"] = 2
            tampered["response_sha256"] = MODULE._sha256(
                MODULE._canonical_json(
                    {
                        key: item
                        for key, item in tampered.items()
                        if key != "response_sha256"
                    }
                )
            )
            with self.assertRaisesRegex(
                MODULE.WitnessLeaseWorkerError,
                "closure",
            ):
                MODULE.validate_result(tampered, request=value)

    def test_wrong_release_container_helper_calls_neither_client_nor_core(self):
        args = argparse.Namespace(
            container_action="renew",
            request_id="44444444-4444-4444-8444-444444444444",
            campaign_id=CAMPAIGN_ID,
            expected_release_sha="f" * 40,
            lease_duration_seconds=180,
        )
        fake_settings = mock.Mock(
            release_sha=RELEASE_SHA,
            writer_witness_lease_duration_seconds=180,
        )
        identity = mock.Mock()
        client = mock.Mock()
        renew = mock.AsyncMock()
        initialize = mock.AsyncMock()
        config_module = types.ModuleType("core.config")
        config_module.settings = fake_settings
        identity_module = types.ModuleType("core.runtime_identity")
        identity_module.resolve_runtime_identity = identity
        client_module = types.ModuleType("core.writer_witness_client")
        client_module.initialize_local_writer_lease_once = initialize
        client_module.renew_local_writer_lease_once = renew
        client_module.writer_witness_client_from_settings = client
        with mock.patch.dict(
            sys.modules,
            {
                "core.config": config_module,
                "core.runtime_identity": identity_module,
                "core.writer_witness_client": client_module,
            },
        ):
            with self.assertRaisesRegex(
                MODULE.WitnessLeaseWorkerError,
                "release or lease policy",
            ):
                __import__("asyncio").run(
                    MODULE._container_lease_action(args)
                )
        identity.assert_not_called()
        client.assert_not_called()
        renew.assert_not_called()
        initialize.assert_not_called()

    def test_runtime_inventory_reports_forbidden_live_api(self):
        project = "three-site-operation"
        operation = OPERATION_ID
        runtime_ids = {
            "app": "sha256:" + "1" * 64,
            "postgres": "sha256:" + "2" * 64,
            "redis": "sha256:" + "3" * 64,
            "nginx": "sha256:" + "4" * 64,
        }

        def row(
            identifier: str,
            service: str,
            image: str,
            *,
            running: bool,
            oneoff: bool = False,
        ) -> dict:
            return {
                "Id": identifier,
                "Image": image,
                "Config": {
                    "Labels": {
                        "com.docker.compose.project": project,
                        "com.docker.compose.service": service,
                        "com.docker.compose.oneoff": (
                            "True" if oneoff else "False"
                        ),
                        "trading-bot.production.operation-id": operation,
                    }
                },
                "State": {
                    "Status": "running" if running else "exited"
                },
            }

        database_id = "a" * 64
        surface = (
            MODULE.ExactReleaseBackend._runtime_surface_from_rows(
                [
                    row(
                        database_id,
                        "webapp_fi_db",
                        runtime_ids["postgres"],
                        running=True,
                    ),
                    row(
                        "b" * 64,
                        "webapp_fi_api",
                        runtime_ids["app"],
                        running=True,
                    ),
                ],
                role="webapp_fi",
                project_name=project,
                operation_id=operation,
                database_id=database_id,
                runtime_ids=runtime_ids,
            )
        ).document()
        self.assertEqual(surface["api_running_count"], 1)
        self.assertEqual(surface["public_service_running_count"], 1)
        with self.assertRaisesRegex(
            MODULE.WitnessLeaseWorkerError,
            "database-only",
        ):
            MODULE._validate_zero_surface(surface)

    def test_authorized_exited_oneoff_is_cleaned_before_inventory(self):
        backend = object.__new__(MODULE.ExactReleaseBackend)
        request_id = "44444444-4444-4444-8444-444444444444"
        project = "three-site-operation"
        image = "sha256:" + "1" * 64
        container_id = "c" * 64
        database_id = "d" * 64
        backend.RESTORE = types.SimpleNamespace(
            DOCKER_BASE=("/usr/bin/docker",)
        )
        backend.manifest = types.SimpleNamespace(
            paths=types.SimpleNamespace(project_name=project),
            operation_id=OPERATION_ID,
            app_image_id=image,
        )
        backend.request = {"transition_request_id": request_id}
        backend.role = "webapp_fi"
        backend.database_id = None
        name = backend._oneoff_name(request_id)
        calls: list[list[str]] = []
        present = True

        def run(arguments, **_kwargs):
            nonlocal present
            command = list(arguments)
            calls.append(command)
            if "ps" in command:
                return (
                    f"{container_id}\n".encode("ascii")
                    if present
                    else b""
                )
            if "rm" in command:
                present = False
            return b""

        backend._run = run
        backend._json_output = mock.Mock(
            return_value=[
                {
                    "Id": container_id,
                    "Name": f"/{name}",
                    "Image": image,
                    "Config": {
                        "Labels": {
                            "com.docker.compose.project": project,
                            "com.docker.compose.service": (
                                "webapp_fi_writer_control"
                            ),
                            "com.docker.compose.oneoff": "True",
                            "trading-bot.production.operation-id": (
                                OPERATION_ID
                            ),
                        }
                    },
                    "Mounts": [],
                    "State": {"Status": "exited"},
                }
            ]
        )
        backend._database_container_id = mock.Mock(
            return_value=database_id
        )
        backend.runtime_surface = mock.Mock(
            return_value=MODULE.RuntimeSurface(
                database_running=True,
                operation_oneoff_count=0,
                api_running_count=0,
                effect_running_count=0,
                bot_running_count=0,
                public_service_running_count=0,
                redis_running_count=0,
                other_running_count=0,
            )
        )

        backend.reconcile_authorized_oneoff(
            request_id=request_id,
            authority=lambda _checkpoint: True,
        )

        remove_calls = [call for call in calls if "rm" in call]
        self.assertEqual(len(remove_calls), 1)
        self.assertNotIn("--force", remove_calls[0])
        self.assertEqual(backend.database_id, database_id)
        backend._database_container_id.assert_called_once_with()
        backend.runtime_surface.assert_called_once_with()

    def test_authorized_created_oneoff_is_cleaned_only_when_unstarted(self):
        backend, request_id, calls, events = self.oneoff_cleanup_backend(
            state={
                "Status": "created",
                "Pid": 0,
                "Running": False,
                "StartedAt": "0001-01-01T00:00:00Z",
                "FinishedAt": "0001-01-01T00:00:00Z",
            }
        )

        def authority(checkpoint: str) -> bool:
            events.append(checkpoint)
            return True

        backend.reconcile_authorized_oneoff(
            request_id=request_id,
            authority=authority,
        )

        remove_calls = [call for call in calls if "rm" in call]
        self.assertEqual(len(remove_calls), 1)
        self.assertNotIn("--force", remove_calls[0])
        self.assertEqual(
            events,
            ["before-stale-oneoff-removal", "rm"],
        )
        self.assertEqual(
            len([call for call in calls if "ps" in call]),
            2,
        )
        backend._database_container_id.assert_called_once_with()
        backend.runtime_surface.assert_called_once_with()

    def test_created_oneoff_that_may_have_started_is_not_removed(self):
        backend, request_id, calls, events = self.oneoff_cleanup_backend(
            state={
                "Status": "created",
                "Pid": 17,
                "Running": False,
                "StartedAt": "0001-01-01T00:00:00Z",
                "FinishedAt": "0001-01-01T00:00:00Z",
            }
        )
        with self.assertRaisesRegex(
            MODULE.WitnessLeaseWorkerError,
            "not provably unstarted",
        ):
            backend.reconcile_authorized_oneoff(
                request_id=request_id,
                authority=lambda checkpoint: events.append(checkpoint) or True,
            )
        self.assertFalse(any("rm" in call for call in calls))
        self.assertEqual(events, [])
        backend._database_container_id.assert_not_called()

    def test_created_oneoff_authority_denial_preserves_the_residue(self):
        backend, request_id, calls, events = self.oneoff_cleanup_backend(
            state={
                "Status": "created",
                "Pid": 0,
                "Running": False,
                "StartedAt": "",
                "FinishedAt": "",
            }
        )
        with self.assertRaises(MODULE.WitnessLeaseCancellation):
            backend.reconcile_authorized_oneoff(
                request_id=request_id,
                authority=lambda checkpoint: events.append(checkpoint) or False,
            )
        self.assertFalse(any("rm" in call for call in calls))
        self.assertEqual(events, ["before-stale-oneoff-removal"])
        backend._database_container_id.assert_not_called()

    def test_killed_controller_residue_replays_same_request_zero_residue(self):
        class ResidueBackend(FakeBackend):
            def __init__(self):
                super().__init__()
                self.residue = True
                self.cleanup_count = 0

            def reconcile_authorized_oneoff(
                self,
                *,
                request_id: str,
                authority,
            ) -> None:
                if not callable(authority):
                    raise AssertionError("reconciliation authority is unavailable")
                self.reconcile_calls.append(request_id)
                if request_id != MODULE.deterministic_request_id(
                    operation_id=OPERATION_ID,
                    action="acquire",
                    renewal_sequence=0,
                    purpose="transition",
                ):
                    raise MODULE.WitnessLeaseWorkerError(
                        "unexpected residue request"
                    )
                if self.residue:
                    self.cleanup_count += 1
                    self.residue = False

            def runtime_surface(self):
                surface = super().runtime_surface()
                if not self.residue:
                    return surface
                return MODULE.RuntimeSurface(
                    database_running=True,
                    operation_oneoff_count=1,
                    api_running_count=0,
                    effect_running_count=0,
                    bot_running_count=0,
                    public_service_running_count=0,
                    redis_running_count=0,
                    other_running_count=0,
                )

        with tempfile.TemporaryDirectory() as directory:
            backend = ResidueBackend()
            value = request(Path(directory), backend)
            with self.assertRaises(MODULE.WitnessLeaseCancellation):
                self.apply(
                    value,
                    backend,
                    authority=lambda checkpoint: (
                        checkpoint
                        != "before-stale-oneoff-reconciliation"
                    ),
                )
            result = self.apply(value, backend)
        self.assertEqual(backend.cleanup_count, 1)
        self.assertEqual(
            backend.acquire_calls,
            [value["transition_request_id"]],
        )
        self.assertEqual(
            result["after_surface"]["operation_oneoff_count"],
            0,
        )

    def test_foreign_or_live_oneoff_is_never_removed(self):
        request_id = "44444444-4444-4444-8444-444444444444"
        project = "three-site-operation"
        image = "sha256:" + "1" * 64
        container_id = "c" * 64
        for service, state, error in (
            ("foreign_service", "exited", "foreign"),
            ("webapp_fi_writer_control", "running", "still live"),
            ("webapp_fi_writer_control", "paused", "still live"),
            ("webapp_fi_writer_control", "restarting", "still live"),
            ("webapp_fi_writer_control", "removing", "still live"),
        ):
            with self.subTest(service=service, state=state):
                backend = object.__new__(MODULE.ExactReleaseBackend)
                backend.RESTORE = types.SimpleNamespace(
                    DOCKER_BASE=("/usr/bin/docker",)
                )
                backend.manifest = types.SimpleNamespace(
                    paths=types.SimpleNamespace(project_name=project),
                    operation_id=OPERATION_ID,
                    app_image_id=image,
                )
                backend.request = {
                    "transition_request_id": request_id
                }
                backend.role = "webapp_fi"
                backend.database_id = None
                name = backend._oneoff_name(request_id)
                calls: list[list[str]] = []

                def run(arguments, **_kwargs):
                    command = list(arguments)
                    calls.append(command)
                    return f"{container_id}\n".encode("ascii")

                backend._run = run
                backend._json_output = mock.Mock(
                    return_value=[
                        {
                            "Id": container_id,
                            "Name": f"/{name}",
                            "Image": image,
                            "Config": {
                                "Labels": {
                                    "com.docker.compose.project": project,
                                    "com.docker.compose.service": service,
                                    "com.docker.compose.oneoff": "True",
                                    (
                                        "trading-bot.production."
                                        "operation-id"
                                    ): OPERATION_ID,
                                }
                            },
                            "Mounts": [],
                            "State": {"Status": state},
                        }
                    ]
                )
                backend._database_container_id = mock.Mock()
                with self.assertRaisesRegex(
                    MODULE.WitnessLeaseWorkerError,
                    error,
                ):
                    backend.reconcile_authorized_oneoff(
                        request_id=request_id,
                        authority=lambda _checkpoint: True,
                    )
                self.assertFalse(
                    any("rm" in command for command in calls)
                )
                backend._database_container_id.assert_not_called()

    def test_runtime_inventory_reports_forbidden_oneoff_residue(self):
        project = "three-site-operation"
        runtime_ids = {
            "app": "sha256:" + "1" * 64,
            "postgres": "sha256:" + "2" * 64,
            "redis": "sha256:" + "3" * 64,
            "nginx": "sha256:" + "4" * 64,
        }

        def row(identifier, service, image, status, oneoff):
            return {
                "Id": identifier,
                "Image": image,
                "Config": {
                    "Labels": {
                        "com.docker.compose.project": project,
                        "com.docker.compose.service": service,
                        "com.docker.compose.oneoff": str(oneoff),
                        "trading-bot.production.operation-id": (
                            OPERATION_ID
                        ),
                    }
                },
                "State": {"Status": status},
            }

        surface = MODULE.ExactReleaseBackend._runtime_surface_from_rows(
            [
                row(
                    "a" * 64,
                    "webapp_fi_db",
                    runtime_ids["postgres"],
                    "running",
                    False,
                ),
                row(
                    "b" * 64,
                    "webapp_fi_writer_control",
                    runtime_ids["app"],
                    "exited",
                    True,
                ),
            ],
            role="webapp_fi",
            project_name=project,
            operation_id=OPERATION_ID,
            database_id="a" * 64,
            runtime_ids=runtime_ids,
        ).document()
        self.assertEqual(surface["operation_oneoff_count"], 1)
        with self.assertRaisesRegex(
            MODULE.WitnessLeaseWorkerError,
            "database-only",
        ):
            MODULE._validate_zero_surface(surface)

    def test_result_files_and_journal_are_owner_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = FakeBackend()
            value = request(root, backend)
            self.apply(value, backend)
            journal, result = MODULE._journal_paths(value)
            for path in (journal, result):
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
                self.assertEqual(os.stat(path).st_uid, os.geteuid())
            journal_value = json.loads(journal.read_text("ascii"))
            self.assertEqual(journal_value["status"], "completed")
            self.assertEqual(
                hashlib.sha256(result.read_bytes()).hexdigest(),
                journal_value["result_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
