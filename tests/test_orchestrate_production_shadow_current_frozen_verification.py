from __future__ import annotations

from contextlib import ExitStack, nullcontext
import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from scripts import (
    orchestrate_production_shadow_current_frozen_verification as CURRENT,
)


OPERATION_ID = "123e4567-e89b-42d3-a456-426614174000"
RELEASE_SHA = "1" * 40
RELEASE_TREE_SHA = "2" * 40
LEGACY_RELEASE_SHA = "3" * 40
AGGREGATE_SHA = "4" * 64
CAPTURE_RECEIPT_SHA = "5" * 64
CAPTURE_CLAIM_SHA = "6" * 64
CAPTURE_CONSUMPTION_SHA = "7" * 64
CAPTURE_OUTCOME_SHA = "8" * 64
FRESH_RECEIPT_SHA = "9" * 64
FRESH_CHALLENGE_SHA = "a" * 64
R2_CLAIM_SHA = "b" * 64
R2_NONCE = "c" * 64
GLOBAL_GENERATION_SHA = "d" * 64
BINDING_SHA = {"bot_fi": "e" * 64, "webapp_fi": "f" * 64}
FREEZE_EVIDENCE_SHA = {
    "bot_fi": hashlib.sha256(b"bot-freeze").hexdigest(),
    "webapp_fi": hashlib.sha256(b"web-freeze").hexdigest(),
}
ROLE_GENERATION_SHA = {
    "bot_fi": hashlib.sha256(b"bot-generation").hexdigest(),
    "webapp_fi": hashlib.sha256(b"web-generation").hexdigest(),
}
RELEASE_FILE_SHA = {
    key: hashlib.sha256(key.encode("ascii")).hexdigest()
    for key in CURRENT.FROZEN.RELEASE_FILE_KEYS
}
ISSUED_AT = 900
EXPIRES_AT = 2_000
OBSERVED_AT = 1_100


def _binding(role: str) -> SimpleNamespace:
    return SimpleNamespace(
        operation_id=OPERATION_ID,
        release_sha=RELEASE_SHA,
        legacy_release_sha=LEGACY_RELEASE_SHA,
        role=role,
        mode="frozen-final",
        canonical_sha256=BINDING_SHA[role],
    )


def _inputs(root: Path) -> SimpleNamespace:
    roles = {
        role: SimpleNamespace(
            manifest_sha256=hashlib.sha256(
                f"{role}-manifest".encode("ascii")
            ).hexdigest(),
            manifest={
                "archive": {
                    "sha256": hashlib.sha256(
                        f"{role}-archive".encode("ascii")
                    ).hexdigest()
                }
            },
        )
        for role in CURRENT.ROLES
    }
    return SimpleNamespace(
        operation_id=OPERATION_ID,
        release_sha=RELEASE_SHA,
        release_tree_sha=RELEASE_TREE_SHA,
        aggregate_sha256=AGGREGATE_SHA,
        roles=roles,
        known_hosts=root / "known_hosts",
        ssh_identity=root / "id_ed25519",
        coordinator_root=(
            root / "secret" / OPERATION_ID / "nginx-coordinator"
        ),
    )


def _fresh_receipt() -> dict[str, object]:
    return {
        "schema": (
            CURRENT.NGINX.PRE_FREEZE_FRESH_READBACK_RECEIPT_SCHEMA
        ),
        "readback_challenge_sha256": FRESH_CHALLENGE_SHA,
        "issued_at_epoch": ISSUED_AT,
        "expires_at_epoch": EXPIRES_AT,
        "captured_at_epoch": 950,
        "global_generation_sha256": GLOBAL_GENERATION_SHA,
        "readbacks": {
            role: {
                "captured_at_epoch": 960 + index,
                "generation_sha256": ROLE_GENERATION_SHA[role],
            }
            for index, role in enumerate(CURRENT.ROLES)
        },
    }


def _capture() -> dict[str, object]:
    return {
        "receipt": {"state": "legacy-frozen"},
        "receipt_sha256": CAPTURE_RECEIPT_SHA,
        "claim": {"claim_epoch": 1},
        "claim_path": Path("/capture/claim.json"),
        "claim_sha256": CAPTURE_CLAIM_SHA,
        "claim_epoch": 1,
        "outcome_sha256": CAPTURE_OUTCOME_SHA,
        "consumption_sha256": CAPTURE_CONSUMPTION_SHA,
        "journal": {
            "roles": {
                role: {
                    "freeze_evidence_sha256": FREEZE_EVIDENCE_SHA[role]
                }
                for role in CURRENT.ROLES
            }
        },
    }


def _claim(fresh_receipt_path: Path) -> dict[str, object]:
    return {
        "owner_action": CURRENT.OWNER_ACTION,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": RELEASE_TREE_SHA,
        "aggregate_sha256": AGGREGATE_SHA,
        "legacy_frozen_receipt_path": str(fresh_receipt_path),
        "legacy_frozen_receipt_sha256": FRESH_RECEIPT_SHA,
        "claim_epoch": 2,
        "previous_claim_sha256": CAPTURE_CLAIM_SHA,
        "nonce": R2_NONCE,
    }


def _host_result(role: str) -> dict[str, object]:
    writer_keys = {
        kind
        for kind, _name, _service in CURRENT.FROZEN.FREEZE.ROLE_WRITERS[
            role
        ]
    }
    ordinal = CURRENT.ROLES.index(role)
    return {
        "schema": CURRENT.FROZEN.HOST_CURRENT_VERIFY_SCHEMA,
        "status": "verified-current-frozen",
        "action": "verify-current",
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": RELEASE_TREE_SHA,
        "role": role,
        "mode": "frozen-final",
        "binding_sha256": BINDING_SHA[role],
        "state_receipt_sha256": FRESH_RECEIPT_SHA,
        "readback_challenge_sha256": FRESH_CHALLENGE_SHA,
        "issued_at_epoch": ISSUED_AT,
        "expires_at_epoch": EXPIRES_AT,
        "captured_at_epoch": 1_000 + ordinal,
        "lease_claim_sha256": R2_CLAIM_SHA,
        "lease_claim_epoch": 2,
        "previous_live_lease_claim_sha256": CAPTURE_CLAIM_SHA,
        "freeze_evidence_live_lease_claim_sha256": (
            CAPTURE_CLAIM_SHA
        ),
        "freeze_evidence_sha256": FREEZE_EVIDENCE_SHA[role],
        "role_freeze_generation_sha256": ROLE_GENERATION_SHA[role],
        "freeze_generation_sha256": GLOBAL_GENERATION_SHA,
        "source_container_ids": {
            kind: hashlib.sha256(
                f"{role}-source-{kind}".encode("ascii")
            ).hexdigest()
            for kind in CURRENT.SOURCE.SOURCE_CONTAINERS
        },
        "writer_container_ids": {
            kind: hashlib.sha256(
                f"{role}-writer-{kind}".encode("ascii")
            ).hexdigest()
            for kind in writer_keys
        },
        "journal_sha256": hashlib.sha256(
            f"{role}-journal".encode("ascii")
        ).hexdigest(),
        "legacy_writer_process_count": 0,
        "writer_database_client_count": 0,
        "file_mutator_process_count": 0,
        "database_container_running": True,
        "redis_container_running": True,
        "pull_policy": "never",
        "source_stopped_or_restarted": False,
        "source_mutated": False,
        "current_mutated": False,
        "service_mutated": False,
        "container_mutated": False,
        "volume_mutated": False,
        "data_mutated": False,
        "production_mutated": False,
    }


class _FakeLease:
    def __init__(
        self,
        harness: "_Harness",
        claim_path: Path,
        claim: dict[str, object],
    ) -> None:
        self.harness = harness
        self.claim_path = claim_path
        self.claim_sha256 = R2_CLAIM_SHA
        self.claim = claim
        self.verify_count = 0
        self.consume_count = 0

    def verify(self) -> dict[str, object]:
        self.verify_count += 1
        return {"claim_sha256": self.claim_sha256}

    def consume(
        self,
        *,
        outcome: str,
        outcome_sha256: str,
    ) -> tuple[Path, str]:
        self.consume_count += 1
        if outcome != CURRENT.OWNER_OUTCOME:
            raise AssertionError("unexpected verification outcome")
        self.harness.set_consumption(outcome_sha256)
        assert self.harness.consumption_sha256 is not None
        return (
            self.claim_path.parent / "consumption.json",
            self.harness.consumption_sha256,
        )


class _Harness:
    def __init__(
        self,
        case: unittest.TestCase,
        root: Path,
        *,
        fail_web_once: bool = False,
    ) -> None:
        self.case = case
        self.root = root
        self.inputs = _inputs(root)
        self.bindings = {
            role: _binding(role) for role in CURRENT.ROLES
        }
        self.capture = _capture()
        self.fresh_receipt = _fresh_receipt()
        self.stack = ExitStack()
        self.actions: list[str] = []
        self.installs: list[str] = []
        self.fail_web_once = fail_web_once
        self.web_failed = False
        self.consumption: dict[str, object] | None = None
        self.consumption_sha256: str | None = None

        self.secret_root = root / "secret"
        self.project_root = root / "project"
        self.output_root = root / "output"
        self.nginx_root = root / "nginx"
        self.stack.enter_context(
            mock.patch.object(
                CURRENT.FROZEN,
                "SECRET_ROOT_PREFIX",
                self.secret_root,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                CURRENT.FROZEN,
                "PROJECT_ROOT_PREFIX",
                self.project_root,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                CURRENT.FROZEN,
                "SOURCE_OUTPUT_ROOT",
                self.output_root,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                CURRENT.FROZEN.NGINX_GENERATION,
                "DEFAULT_OPERATION_BASE",
                self.nginx_root,
            )
        )
        self.paths = CURRENT._paths(OPERATION_ID, RELEASE_SHA)
        for key in (
            "verification_root",
            "host_results",
            "verification_receipts",
        ):
            self.paths[key].mkdir(mode=0o700, parents=True, exist_ok=True)
        self.fresh_receipt_path = CURRENT._capture_receipt_path(
            self.inputs,
            FRESH_RECEIPT_SHA,
        )
        self.claim_path = CURRENT.FROZEN.canonical_paths(
            OPERATION_ID,
            RELEASE_SHA,
            lease_claim_sha256=R2_CLAIM_SHA,
        )["lease_claim"]
        self.claim = _claim(self.fresh_receipt_path)
        self.lease = _FakeLease(self, self.claim_path, self.claim)

        self.stack.enter_context(
            mock.patch.object(
                CURRENT,
                "_load_inputs",
                return_value=self.inputs,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                CURRENT,
                "_load_bindings",
                return_value=self.bindings,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                CURRENT,
                "_validate_capture_completion",
                return_value=self.capture,
            )
        )
        self.stack.enter_context(
            mock.patch.object(CURRENT, "_ensure_directories")
        )
        self.stack.enter_context(
            mock.patch.object(
                CURRENT.FROZEN,
                "_controller_lock",
                return_value=nullcontext(),
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                CURRENT.FROZEN,
                "_release_file_hashes",
                return_value=RELEASE_FILE_SHA,
            )
        )
        self.stack.enter_context(
            mock.patch.object(CURRENT.FROZEN, "_assert_ssh_material")
        )
        self.stack.enter_context(
            mock.patch.object(
                CURRENT.FINLAND_STAGE,
                "_verify_role_host",
            )
        )
        self.stack.enter_context(
            mock.patch.object(CURRENT.os, "geteuid", return_value=0)
        )
        self.stack.enter_context(
            mock.patch.object(
                CURRENT,
                "_fresh_readback",
                side_effect=self.readback,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                CURRENT.NGINX,
                "hold_coordinator_live_lease",
                return_value=nullcontext(self.lease),
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                CURRENT.NGINX,
                "resume_coordinator_live_lease",
                return_value=nullcontext(self.lease),
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                CURRENT.NGINX,
                "load_state_receipt",
                side_effect=self.load_receipt,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                CURRENT.NGINX,
                "load_live_lease_claim_material",
                return_value=(self.claim, R2_CLAIM_SHA),
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                CURRENT.NGINX,
                "_load_claim_from_controller",
                return_value=(self.claim, self.fresh_receipt),
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                CURRENT.NGINX,
                "_load_consumption_audit",
                side_effect=self.load_consumption,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                CURRENT.FROZEN,
                "_install_role_material",
                side_effect=self.install,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                CURRENT.FROZEN,
                "_invoke_bound_action",
                side_effect=self.invoke,
            )
        )

    def close(self) -> None:
        self.stack.close()

    def readback(self, **_kwargs: object) -> tuple[dict, Path, str]:
        return (
            copy.deepcopy(self.fresh_receipt),
            self.fresh_receipt_path,
            FRESH_RECEIPT_SHA,
        )

    def load_receipt(self, *_args: object, **kwargs: object) -> tuple[dict, str]:
        if (
            kwargs.get("allow_historical") is not True
            and kwargs.get("observed_at_epoch", 1) > EXPIRES_AT
        ):
            raise CURRENT.NGINX.NginxCoordinatorError(
                "simulated expired fresh receipt"
            )
        return copy.deepcopy(self.fresh_receipt), FRESH_RECEIPT_SHA

    def install(self, *, role: str, **_kwargs: object) -> None:
        self.installs.append(role)

    def invoke(self, *, role: str, **_kwargs: object) -> dict[str, object]:
        self.actions.append(role)
        if role == "webapp_fi" and self.fail_web_once and not self.web_failed:
            self.web_failed = True
            raise CURRENT.FROZEN.FrozenSnapshotOrchestratorError(
                "simulated lost remote result"
            )
        return _host_result(role)

    def set_consumption(self, outcome_sha256: str) -> None:
        self.consumption = {
            "schema": CURRENT.NGINX.LIVE_LEASE_CONSUMPTION_SCHEMA,
            "status": "consumed",
            "owner_action": CURRENT.OWNER_ACTION,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": RELEASE_TREE_SHA,
            "aggregate_sha256": AGGREGATE_SHA,
            "claim_sha256": R2_CLAIM_SHA,
            "claim_epoch": 2,
            "claim_nonce": R2_NONCE,
            "outcome": CURRENT.OWNER_OUTCOME,
            "outcome_sha256": outcome_sha256,
            "readiness_audit_sha256": None,
            "final_state": "legacy-frozen",
            "final_state_receipt_sha256": FRESH_RECEIPT_SHA,
            "controller_journal_sha256": "1" * 64,
            "controller_journal_event_count": 2,
            "controller_evidence_count": 2,
            "controller_evidence_tail_sha256": "2" * 64,
            "consumer_pid": 123,
            "consumption_nonce": "3" * 64,
            "adopted_after_crash": False,
            "controller_lock_path": str(
                self.inputs.coordinator_root / "coordinator.lock"
            ),
            "controller_authoritative": True,
            "automatic": False,
        }
        self.consumption_sha256 = hashlib.sha256(
            CURRENT.canonical_json(self.consumption)
        ).hexdigest()

    def load_consumption(
        self,
        _inputs: object,
        *,
        claim: object,
        claim_sha256: str,
    ) -> tuple[dict, str] | None:
        self.case.assertEqual(claim, self.claim)
        self.case.assertEqual(claim_sha256, R2_CLAIM_SHA)
        if self.consumption is None:
            return None
        assert self.consumption_sha256 is not None
        return copy.deepcopy(self.consumption), self.consumption_sha256

    def kwargs(self, *, apply: bool = True) -> dict[str, object]:
        plan = CURRENT.render_plan(
            inputs=self.inputs,
            bindings=self.bindings,
            capture=self.capture,
        )
        return {
            "aggregate_path": self.root / "aggregate.json",
            "bot_fi_nginx_manifest": self.root / "bot-manifest.json",
            "bot_fi_nginx_archive": self.root / "bot-archive.tar",
            "webapp_fi_nginx_manifest": self.root / "web-manifest.json",
            "webapp_fi_nginx_archive": self.root / "web-archive.tar",
            "bot_fi_binding": self.root / "bot-binding.json",
            "webapp_fi_binding": self.root / "web-binding.json",
            "capture_state_receipt_path": self.root / "capture.json",
            "capture_state_receipt_sha256": CAPTURE_RECEIPT_SHA,
            "known_hosts": self.root / "known_hosts",
            "ssh_identity": self.root / "id_ed25519",
            "apply": apply,
            "confirm": plan["required_confirmation"] if apply else None,
            "now_fn": lambda: OBSERVED_AT,
        }


class CurrentFrozenVerificationTests(unittest.TestCase):
    def test_plan_is_inert(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            harness = _Harness(self, Path(raw))
            try:
                result = CURRENT.orchestrate(**harness.kwargs(apply=False))
            finally:
                harness.close()
        self.assertEqual(result["status"], "planned")
        self.assertFalse(result["network_contacted"])
        self.assertEqual(harness.actions, [])
        self.assertEqual(harness.installs, [])
        self.assertEqual(harness.lease.verify_count, 0)

    def test_apply_publishes_strict_two_role_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            harness = _Harness(self, Path(raw))
            try:
                result = CURRENT.orchestrate(**harness.kwargs())
                receipt, digest = (
                    CURRENT.load_current_frozen_verification_receipt(
                        Path(result["receipt_path"]),
                        expected_sha256=result["receipt_sha256"],
                        expected_operation_id=OPERATION_ID,
                        expected_release_sha=RELEASE_SHA,
                        expected_release_tree_sha=RELEASE_TREE_SHA,
                        expected_legacy_release_sha=LEGACY_RELEASE_SHA,
                        expected_nginx_aggregate_sha256=AGGREGATE_SHA,
                        expected_bindings=BINDING_SHA,
                        expected_capture_state_receipt_sha256=(
                            CAPTURE_RECEIPT_SHA
                        ),
                        observed_at_epoch=OBSERVED_AT,
                    )
                )
            finally:
                harness.close()
        self.assertEqual(digest, result["receipt_sha256"])
        self.assertEqual(set(receipt["host_results"]), set(CURRENT.ROLES))
        self.assertEqual(harness.actions, list(CURRENT.ROLES))
        self.assertEqual(harness.installs, list(CURRENT.ROLES))
        self.assertEqual(harness.lease.consume_count, 1)
        self.assertFalse(result["service_mutated"])

    def test_partial_failure_resumes_only_unresolved_host(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            harness = _Harness(self, Path(raw), fail_web_once=True)
            try:
                with self.assertRaises(
                    CURRENT.FROZEN.FrozenSnapshotOrchestratorError
                ):
                    CURRENT.orchestrate(**harness.kwargs())
                journal = json.loads(
                    harness.paths["verification_journal"].read_text()
                )
                self.assertEqual(
                    journal["status"],
                    "reconciliation-required",
                )
                self.assertIsNotNone(journal["roles"]["bot_fi"])
                self.assertIsNone(journal["roles"]["webapp_fi"])
                resume = harness.kwargs()
                resume.update(
                    {
                        "resume_claim_path": harness.claim_path,
                        "resume_claim_sha256": R2_CLAIM_SHA,
                        "resume_claim_nonce": R2_NONCE,
                    }
                )
                result = CURRENT.orchestrate(**resume)
            finally:
                harness.close()
        self.assertEqual(result["status"], "verified-current-frozen")
        self.assertEqual(
            harness.actions,
            ["bot_fi", "webapp_fi", "webapp_fi"],
        )
        self.assertEqual(
            harness.installs,
            ["bot_fi", "webapp_fi", "webapp_fi"],
        )

    def test_partial_resume_rejects_changed_persisted_host_result(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            harness = _Harness(self, Path(raw), fail_web_once=True)
            try:
                with self.assertRaises(
                    CURRENT.FROZEN.FrozenSnapshotOrchestratorError
                ):
                    CURRENT.orchestrate(**harness.kwargs())
                result_path = CURRENT._host_result_path(
                    harness.inputs,
                    R2_CLAIM_SHA,
                    "bot_fi",
                )
                changed = json.loads(result_path.read_text())
                changed["journal_sha256"] = "5" * 64
                result_path.write_bytes(CURRENT.canonical_json(changed))
                result_path.chmod(0o600)
                resume = harness.kwargs()
                resume.update(
                    {
                        "resume_claim_path": harness.claim_path,
                        "resume_claim_sha256": R2_CLAIM_SHA,
                        "resume_claim_nonce": R2_NONCE,
                    }
                )
                with self.assertRaisesRegex(
                    CURRENT.CurrentFrozenVerificationError,
                    "persisted current-freeze result changed",
                ):
                    CURRENT.orchestrate(**resume)
            finally:
                harness.close()

    def test_unconsumed_partial_claim_cannot_resume_after_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            harness = _Harness(self, Path(raw), fail_web_once=True)
            try:
                with self.assertRaises(
                    CURRENT.FROZEN.FrozenSnapshotOrchestratorError
                ):
                    CURRENT.orchestrate(**harness.kwargs())
                calls_before = list(harness.actions)
                resume = harness.kwargs()
                resume.update(
                    {
                        "resume_claim_path": harness.claim_path,
                        "resume_claim_sha256": R2_CLAIM_SHA,
                        "resume_claim_nonce": R2_NONCE,
                        "now_fn": lambda: EXPIRES_AT + 1,
                    }
                )
                with self.assertRaisesRegex(
                    CURRENT.CurrentFrozenVerificationError,
                    "expired or invalid",
                ):
                    CURRENT.orchestrate(**resume)
            finally:
                harness.close()
        self.assertEqual(harness.actions, calls_before)

    def test_consumed_journal_finalizes_without_resuming_claim(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            harness = _Harness(self, Path(raw))
            try:
                with mock.patch.object(
                    CURRENT,
                    "_finalize_consumed_journal",
                    side_effect=RuntimeError("simulated publication crash"),
                ):
                    with self.assertRaises(RuntimeError):
                        CURRENT.orchestrate(**harness.kwargs())
                self.assertEqual(harness.lease.consume_count, 1)
                expired = harness.kwargs()
                expired["now_fn"] = lambda: EXPIRES_AT + 100
                result = CURRENT.orchestrate(**expired)
                repeated = CURRENT.orchestrate(**expired)
            finally:
                harness.close()
        self.assertEqual(result["status"], "verified-current-frozen")
        self.assertEqual(repeated, result)
        self.assertEqual(harness.lease.consume_count, 1)
        self.assertEqual(harness.actions, list(CURRENT.ROLES))

    def test_orphaned_exact_claim_resumes_without_new_readback(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            harness = _Harness(self, Path(raw))
            try:
                arguments = harness.kwargs()
                arguments.update(
                    {
                        "resume_claim_path": harness.claim_path,
                        "resume_claim_sha256": R2_CLAIM_SHA,
                        "resume_claim_nonce": R2_NONCE,
                    }
                )
                result = CURRENT.orchestrate(**arguments)
                with self.assertRaisesRegex(
                    CURRENT.CurrentFrozenVerificationError,
                    "chain differs",
                ):
                    wrong = {**arguments, "resume_claim_nonce": "d" * 64}
                    harness.paths["verification_journal"].unlink()
                    CURRENT.orchestrate(**wrong)
            finally:
                harness.close()
        self.assertEqual(result["status"], "verified-current-frozen")
        self.assertEqual(harness.actions, list(CURRENT.ROLES))

    def test_public_loader_rejects_copy_but_not_preexisting_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            harness = _Harness(self, Path(raw))
            try:
                result = CURRENT.orchestrate(**harness.kwargs())
                receipt_path = Path(result["receipt_path"])
                old = 100
                os.utime(receipt_path, (old, old))
                loaded, digest = (
                    CURRENT.load_current_frozen_verification_receipt(
                        receipt_path,
                        expected_sha256=result["receipt_sha256"],
                        expected_operation_id=OPERATION_ID,
                        expected_release_sha=RELEASE_SHA,
                        expected_release_tree_sha=RELEASE_TREE_SHA,
                        expected_legacy_release_sha=LEGACY_RELEASE_SHA,
                        expected_nginx_aggregate_sha256=AGGREGATE_SHA,
                        expected_bindings=BINDING_SHA,
                        expected_capture_state_receipt_sha256=(
                            CAPTURE_RECEIPT_SHA
                        ),
                        observed_at_epoch=OBSERVED_AT,
                    )
                )
                with self.assertRaises(
                    CURRENT.CurrentFrozenVerificationError
                ):
                    CURRENT.load_current_frozen_verification_receipt(
                        receipt_path,
                        expected_sha256=result["receipt_sha256"],
                        expected_operation_id=OPERATION_ID,
                        expected_release_sha=RELEASE_SHA,
                        expected_release_tree_sha=RELEASE_TREE_SHA,
                        expected_legacy_release_sha=LEGACY_RELEASE_SHA,
                        expected_nginx_aggregate_sha256=AGGREGATE_SHA,
                        expected_bindings=BINDING_SHA,
                        expected_capture_state_receipt_sha256=(
                            CAPTURE_RECEIPT_SHA
                        ),
                        observed_at_epoch=EXPIRES_AT + 100,
                    )
                historical, historical_digest = (
                    CURRENT.load_current_frozen_verification_receipt(
                        receipt_path,
                        expected_sha256=result["receipt_sha256"],
                        expected_operation_id=OPERATION_ID,
                        expected_release_sha=RELEASE_SHA,
                        expected_release_tree_sha=RELEASE_TREE_SHA,
                        expected_legacy_release_sha=LEGACY_RELEASE_SHA,
                        expected_nginx_aggregate_sha256=AGGREGATE_SHA,
                        expected_bindings=BINDING_SHA,
                        expected_capture_state_receipt_sha256=(
                            CAPTURE_RECEIPT_SHA
                        ),
                        observed_at_epoch=EXPIRES_AT + 100,
                        allow_historical_completed=True,
                    )
                )
                copied = harness.root / "copied-receipt.json"
                copied.write_bytes(receipt_path.read_bytes())
                copied.chmod(0o600)
                with self.assertRaises(
                    CURRENT.CurrentFrozenVerificationError
                ):
                    CURRENT.load_current_frozen_verification_receipt(
                        copied,
                        expected_sha256=result["receipt_sha256"],
                        expected_operation_id=OPERATION_ID,
                        expected_release_sha=RELEASE_SHA,
                        expected_release_tree_sha=RELEASE_TREE_SHA,
                        expected_legacy_release_sha=LEGACY_RELEASE_SHA,
                        expected_nginx_aggregate_sha256=AGGREGATE_SHA,
                        expected_bindings=BINDING_SHA,
                        expected_capture_state_receipt_sha256=(
                            CAPTURE_RECEIPT_SHA
                        ),
                        observed_at_epoch=OBSERVED_AT,
                    )
            finally:
                harness.close()
        self.assertEqual(digest, result["receipt_sha256"])
        self.assertEqual(historical_digest, digest)
        self.assertEqual(historical, loaded)
        self.assertEqual(loaded["status"], "verified-current-frozen")

    def test_receipt_rejects_expiry_swap_and_substitution(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            harness = _Harness(self, Path(raw))
            try:
                result = CURRENT.orchestrate(**harness.kwargs())
                receipt = json.loads(Path(result["receipt_path"]).read_text())
                validate_kwargs = {
                    "expected_operation_id": OPERATION_ID,
                    "expected_release_sha": RELEASE_SHA,
                    "expected_release_tree_sha": RELEASE_TREE_SHA,
                    "expected_legacy_release_sha": LEGACY_RELEASE_SHA,
                    "expected_nginx_aggregate_sha256": AGGREGATE_SHA,
                    "expected_bindings": BINDING_SHA,
                    "expected_capture_state_receipt_sha256": (
                        CAPTURE_RECEIPT_SHA
                    ),
                }
                with self.assertRaises(
                    CURRENT.CurrentFrozenVerificationError
                ):
                    CURRENT.validate_current_frozen_verification_receipt(
                        receipt,
                        observed_at_epoch=EXPIRES_AT + 1,
                        **validate_kwargs,
                    )
                swapped = copy.deepcopy(receipt)
                swapped["host_results"]["bot_fi"] = receipt[
                    "host_results"
                ]["webapp_fi"]
                with self.assertRaises(
                    CURRENT.CurrentFrozenVerificationError
                ):
                    CURRENT.validate_current_frozen_verification_receipt(
                        swapped,
                        observed_at_epoch=OBSERVED_AT,
                        **validate_kwargs,
                    )
                substituted = copy.deepcopy(receipt)
                substituted["host_results"]["bot_fi"][
                    "journal_sha256"
                ] = "4" * 64
                with self.assertRaises(
                    CURRENT.CurrentFrozenVerificationError
                ):
                    CURRENT.validate_current_frozen_verification_receipt(
                        substituted,
                        observed_at_epoch=OBSERVED_AT,
                        **validate_kwargs,
                    )
            finally:
                harness.close()


if __name__ == "__main__":
    unittest.main()
