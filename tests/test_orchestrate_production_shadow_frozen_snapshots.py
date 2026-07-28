from __future__ import annotations

from contextlib import ExitStack, nullcontext
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import (
    orchestrate_production_shadow_frozen_snapshots as FROZEN,
)


OPERATION_ID = "123e4567-e89b-42d3-a456-426614174000"
RELEASE_SHA = "1" * 40
RELEASE_TREE_SHA = "2" * 40
AGGREGATE_SHA = "3" * 64
RECEIPT_SHA = "4" * 64
CLAIM_SHA = "5" * 64
CLAIM_NONCE = "6" * 64
CONSUMPTION_SHA = "7" * 64
OUTCOME_SHA = "8" * 64
BINDING_SHA = {"bot_fi": "9" * 64, "webapp_fi": "a" * 64}
FREEZE_SHA = {"bot_fi": "b" * 64, "webapp_fi": "c" * 64}
RELEASE_FILE_SHA = {
    "agent": "d" * 64,
    "producer": "e" * 64,
    "freeze_worker": "f" * 64,
    "lease_worker": "1" * 64,
}


def file_rows(role: str) -> dict[str, dict[str, object]]:
    return {
        name: {
            "sha256": hashlib.sha256(
                f"{role}:{name}".encode("ascii")
            ).hexdigest(),
            "bytes": len(role) + len(name) + 1,
        }
        for name in FROZEN.SNAPSHOT_FILENAMES
    }


def binding(role: str, *, mode: str = "frozen-final") -> SimpleNamespace:
    return SimpleNamespace(
        operation_id=OPERATION_ID,
        release_sha=RELEASE_SHA,
        legacy_release_sha="0" * 39 + "1",
        role=role,
        mode=mode,
        source_project="trading_bot" if role == "bot_fi" else "current",
        controller_manifest_sha256="2" * 64,
        approval_sha256="3" * 64,
        canonical_sha256=BINDING_SHA[role],
    )


def inputs(tmp: Path) -> SimpleNamespace:
    roles = {}
    for role in FROZEN.ROLES:
        roles[role] = SimpleNamespace(
            manifest_sha256=hashlib.sha256(
                f"manifest:{role}".encode("ascii")
            ).hexdigest(),
            manifest={
                "archive": {
                    "sha256": hashlib.sha256(
                        f"archive:{role}".encode("ascii")
                    ).hexdigest()
                }
            },
        )
    return SimpleNamespace(
        operation_id=OPERATION_ID,
        release_sha=RELEASE_SHA,
        release_tree_sha=RELEASE_TREE_SHA,
        aggregate_sha256=AGGREGATE_SHA,
        roles=roles,
        known_hosts=tmp / "known_hosts",
        ssh_identity=tmp / "id_ed25519",
    )


class FakeLease:
    def __init__(self, claim_path: Path) -> None:
        self.claim_path = claim_path
        self.claim_sha256 = CLAIM_SHA
        self.claim = {
            "owner_action": "capture-frozen-final-snapshots",
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": RELEASE_TREE_SHA,
            "aggregate_sha256": AGGREGATE_SHA,
            "legacy_frozen_receipt_sha256": RECEIPT_SHA,
            "claim_epoch": 1,
            "nonce": CLAIM_NONCE,
        }
        self.verify_count = 0
        self.consume_calls: list[tuple[str, str]] = []

    def verify(self) -> dict[str, object]:
        self.verify_count += 1
        return {
            "claim_sha256": self.claim_sha256,
            "phase": "legacy-frozen",
        }

    def consume(
        self,
        *,
        outcome: str,
        outcome_sha256: str,
    ) -> tuple[Path, str]:
        self.consume_calls.append((outcome, outcome_sha256))
        return self.claim_path.parent.parent / "consumption.json", CONSUMPTION_SHA


class LeaseContext:
    def __init__(self, lease: FakeLease) -> None:
        self.lease = lease
        self.exited_with: type[BaseException] | None = None

    def __enter__(self) -> FakeLease:
        return self.lease

    def __exit__(self, exc_type, _exc, _traceback) -> bool:
        self.exited_with = exc_type
        return False


class ControllerHarness:
    def __init__(
        self,
        case: unittest.TestCase,
        tmp: Path,
        *,
        journal: dict[str, object] | None = None,
        fail_web_freeze: bool = False,
    ) -> None:
        self.case = case
        self.tmp = tmp
        self.inputs = inputs(tmp)
        self.bindings = {role: binding(role) for role in FROZEN.ROLES}
        self.store = copy.deepcopy(journal)
        self.fail_web_freeze = fail_web_freeze
        self.actions: list[tuple[str, str]] = []
        self.material_roles: list[str] = []
        self.checkpoints: list[str] = []
        self.stack = ExitStack()

        self.secret_root = tmp / "secret"
        self.project_root = tmp / "project"
        self.output_root = tmp / "output"
        self.nginx_root = tmp / "nginx"
        self.stack.enter_context(
            mock.patch.object(FROZEN, "SECRET_ROOT_PREFIX", self.secret_root)
        )
        self.stack.enter_context(
            mock.patch.object(FROZEN, "PROJECT_ROOT_PREFIX", self.project_root)
        )
        self.stack.enter_context(
            mock.patch.object(FROZEN, "SOURCE_OUTPUT_ROOT", self.output_root)
        )
        self.stack.enter_context(
            mock.patch.object(
                FROZEN.NGINX_GENERATION,
                "DEFAULT_OPERATION_BASE",
                self.nginx_root,
            )
        )
        self.claim_path = FROZEN.canonical_paths(
            OPERATION_ID,
            RELEASE_SHA,
            lease_claim_sha256=CLAIM_SHA,
        )["lease_claim"]
        self.receipt_path = FROZEN.canonical_paths(
            OPERATION_ID,
            RELEASE_SHA,
            state_receipt_sha256=RECEIPT_SHA,
        )["state_receipt"]
        self.lease = FakeLease(self.claim_path)
        self.context = LeaseContext(self.lease)

        self.stack.enter_context(
            mock.patch.object(
                FROZEN,
                "_load_inputs_and_receipt",
                return_value=self.inputs,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                FROZEN,
                "load_bindings",
                return_value=self.bindings,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                FROZEN,
                "_release_file_hashes",
                return_value=RELEASE_FILE_SHA,
            )
        )
        self.stack.enter_context(
            mock.patch.object(FROZEN, "_assert_ssh_material")
        )
        self.stack.enter_context(
            mock.patch.object(FROZEN, "_ensure_controller_directories")
        )
        self.stack.enter_context(
            mock.patch.object(
                FROZEN, "_controller_lock", return_value=nullcontext()
            )
        )
        self.stack.enter_context(
            mock.patch.object(FROZEN.os, "geteuid", return_value=0)
        )
        self.stack.enter_context(
            mock.patch.object(
                FROZEN.FINLAND_STAGE, "_verify_role_host"
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                FROZEN,
                "_hash_file",
                return_value=(CLAIM_SHA, 100),
            )
        )
        self.hold = self.stack.enter_context(
            mock.patch.object(
                FROZEN.NGINX,
                "hold_coordinator_live_lease",
                return_value=self.context,
            )
        )
        self.resume = self.stack.enter_context(
            mock.patch.object(
                FROZEN.NGINX,
                "resume_coordinator_live_lease",
                return_value=self.context,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                FROZEN,
                "_read_journal",
                side_effect=self._read_journal,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                FROZEN,
                "_write_journal",
                side_effect=self._write_journal,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                FROZEN,
                "_journal_write_existing",
                side_effect=self._write_existing,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                FROZEN,
                "_install_role_material",
                side_effect=self._install_material,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                FROZEN,
                "_invoke_bound_action",
                side_effect=self._action,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                FROZEN,
                "_collect_file",
                return_value="created",
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                FROZEN,
                "_verify_collected_role",
                side_effect=self._collection,
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                FROZEN,
                "_persist_outcome",
                return_value=OUTCOME_SHA,
            )
        )

    def close(self) -> None:
        self.stack.close()

    def _read_journal(self, *_args, **_kwargs):
        return copy.deepcopy(self.store)

    def _write_journal(self, _path, journal, **_kwargs) -> None:
        self.store = copy.deepcopy(journal)

    def _write_existing(self, *, journal, **_kwargs) -> None:
        self.store = copy.deepcopy(journal)

    def _install_material(self, *, role, **_kwargs) -> None:
        self.material_roles.append(role)

    def _action(self, *, action, role, **_kwargs):
        self.actions.append((action, role))
        if self.fail_web_freeze and (action, role) == (
            "freeze",
            "webapp_fi",
        ):
            raise FROZEN.FrozenSnapshotOrchestratorError(
                "simulated partial stop failure"
            )
        result = {
            "freeze_evidence_sha256": FREEZE_SHA[role],
            "files": {},
        }
        if action == "snapshot":
            result["files"] = file_rows(role)
        return result

    def _collection(
        self,
        *,
        role,
        binding,
        freeze_sha256,
        lease_claim_sha256,
        paths,
    ):
        del paths
        return {
            "freeze_evidence_sha256": freeze_sha256,
            "lease_claim_sha256": lease_claim_sha256,
            "manifest_binding_sha256": binding.canonical_sha256,
            "files": file_rows(role),
        }

    def call(self, *, resume: bool = False, apply: bool = True):
        confirmation = FROZEN.confirmation_phrase(
            OPERATION_ID,
            RELEASE_SHA,
            nginx_aggregate_sha256=AGGREGATE_SHA,
            state_receipt_sha256=RECEIPT_SHA,
            binding_sha256=BINDING_SHA,
        )
        return FROZEN.orchestrate(
            aggregate_path=self.tmp / "aggregate.json",
            bot_fi_nginx_manifest=self.tmp / "bot-manifest.json",
            bot_fi_nginx_archive=self.tmp / "bot.tar",
            webapp_fi_nginx_manifest=self.tmp / "web-manifest.json",
            webapp_fi_nginx_archive=self.tmp / "web.tar",
            bot_fi_binding=self.tmp / "bot-binding.json",
            webapp_fi_binding=self.tmp / "web-binding.json",
            state_receipt_path=self.receipt_path,
            state_receipt_sha256=RECEIPT_SHA,
            known_hosts=self.inputs.known_hosts,
            ssh_identity=self.inputs.ssh_identity,
            resume_claim_path=self.claim_path if resume else None,
            resume_claim_sha256=CLAIM_SHA if resume else None,
            resume_claim_nonce=CLAIM_NONCE if resume else None,
            apply=apply,
            confirm=confirmation if apply else None,
            checkpoint=self.checkpoints.append,
            observed_host_addresses={FROZEN.BASE.BOT_FI_HOST},
        )


class FrozenSnapshotContractTests(unittest.TestCase):
    def test_plan_is_inert_and_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = ControllerHarness(self, Path(directory))
            try:
                result = harness.call(apply=False)
            finally:
                harness.close()
        self.assertEqual(result["status"], "planned")
        self.assertFalse(result["network_io"])
        self.assertFalse(result["filesystem_mutated"])
        self.assertFalse(result["production_mutated"])
        self.assertFalse(result["automatic_restore_planned"])
        self.assertEqual(harness.actions, [])
        self.assertEqual(harness.material_roles, [])
        harness.hold.assert_not_called()
        harness.resume.assert_not_called()

    def test_fake_success_freezes_both_before_snapshot_and_consumes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = ControllerHarness(self, Path(directory))
            try:
                result = harness.call()
                journal = copy.deepcopy(harness.store)
            finally:
                harness.close()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            harness.actions[:4],
            [
                ("freeze", "bot_fi"),
                ("freeze", "webapp_fi"),
                ("verify", "bot_fi"),
                ("verify", "webapp_fi"),
            ],
        )
        self.assertEqual(harness.material_roles, list(FROZEN.ROLES))
        self.assertEqual(
            harness.lease.consume_calls,
            [("handoff-shadow-readonly", OUTCOME_SHA)],
        )
        self.assertEqual(journal["status"], "complete")
        self.assertTrue(result["legacy_writers_frozen"])
        self.assertFalse(result["automatic_restore_performed"])
        self.assertFalse(result["object_storage_used"])
        self.assertFalse(result["wa_contacted"])

    def test_partial_freeze_failure_is_unresolved_and_never_restores(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = ControllerHarness(
                self,
                Path(directory),
                fail_web_freeze=True,
            )
            try:
                with self.assertRaisesRegex(
                    FROZEN.FrozenSnapshotOrchestratorError,
                    "partial stop",
                ):
                    harness.call()
                journal = copy.deepcopy(harness.store)
            finally:
                harness.close()
        self.assertEqual(journal["status"], "reconciliation-required")
        self.assertEqual(journal["roles"]["bot_fi"]["phase"], "frozen")
        self.assertEqual(
            journal["roles"]["webapp_fi"]["phase"],
            "material-installed",
        )
        self.assertNotIn(("restore", "bot_fi"), harness.actions)
        self.assertNotIn(("restore", "webapp_fi"), harness.actions)
        self.assertEqual(harness.lease.consume_calls, [])
        self.assertIsNotNone(harness.context.exited_with)

    def test_resume_adopts_exact_claim_and_skips_recorded_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = ControllerHarness(
                self,
                root,
                fail_web_freeze=True,
            )
            try:
                with self.assertRaises(FROZEN.FrozenSnapshotOrchestratorError):
                    first.call()
                partial = copy.deepcopy(first.store)
            finally:
                first.close()

            resumed = ControllerHarness(self, root, journal=partial)
            try:
                result = resumed.call(resume=True)
                actions = list(resumed.actions)
            finally:
                resumed.close()
        self.assertEqual(result["status"], "complete")
        resumed.hold.assert_not_called()
        resumed.resume.assert_called_once_with(
            inputs=resumed.inputs,
            expected_owner_action="capture-frozen-final-snapshots",
            claim_path=resumed.claim_path,
            expected_claim_sha256=CLAIM_SHA,
            expected_nonce=CLAIM_NONCE,
        )
        self.assertNotIn(("freeze", "bot_fi"), actions)
        self.assertIn(("freeze", "webapp_fi"), actions)

    def test_resume_requires_exact_triplet(self) -> None:
        fake_inputs = inputs(Path("/tmp"))
        with self.assertRaisesRegex(
            FROZEN.FrozenSnapshotOrchestratorError,
            "path, digest, and nonce",
        ):
            FROZEN._validate_resume_arguments(
                inputs=fake_inputs,
                claim_path=Path("/tmp/claim"),
                claim_sha256=None,
                claim_nonce=None,
            )

    def test_consumed_claim_crash_window_is_recovered_from_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_inputs = inputs(root)
            bindings = {role: binding(role) for role in FROZEN.ROLES}
            with (
                mock.patch.object(
                    FROZEN, "SECRET_ROOT_PREFIX", root / "secret"
                ),
                mock.patch.object(
                    FROZEN, "PROJECT_ROOT_PREFIX", root / "project"
                ),
                mock.patch.object(
                    FROZEN, "SOURCE_OUTPUT_ROOT", root / "output"
                ),
                mock.patch.object(
                    FROZEN.NGINX_GENERATION,
                    "DEFAULT_OPERATION_BASE",
                    root / "nginx",
                ),
            ):
                paths = FROZEN.canonical_paths(
                    OPERATION_ID,
                    RELEASE_SHA,
                    state_receipt_sha256=RECEIPT_SHA,
                )
                claim_path = FROZEN.canonical_paths(
                    OPERATION_ID,
                    RELEASE_SHA,
                    lease_claim_sha256=CLAIM_SHA,
                )["lease_claim"]
                journal = FROZEN._initial_journal(
                    inputs=fake_inputs,
                    bindings=bindings,
                    state_receipt_sha256=RECEIPT_SHA,
                )
                journal["lease"] = {
                    "claim_path": str(claim_path),
                    "claim_sha256": CLAIM_SHA,
                    "claim_epoch": 1,
                }
                for role in FROZEN.ROLES:
                    snapshot = {
                        "freeze_evidence_sha256": FREEZE_SHA[role],
                        "lease_claim_sha256": CLAIM_SHA,
                        "files": file_rows(role),
                    }
                    collection = {
                        **snapshot,
                        "manifest_binding_sha256": BINDING_SHA[role],
                    }
                    journal["roles"][role] = {
                        "phase": "collected",
                        "freeze_evidence_sha256": FREEZE_SHA[role],
                        "snapshot": snapshot,
                        "collection": collection,
                    }
                journal["outcome_sha256"] = OUTCOME_SHA
                journal["status"] = "reconciliation-required"
                journal["state_sha256"] = FROZEN._state_sha256(journal)
                audit = {
                    "outcome": "handoff-shadow-readonly",
                    "outcome_sha256": OUTCOME_SHA,
                    "final_state": "legacy-frozen",
                    "final_state_receipt_sha256": RECEIPT_SHA,
                    "readiness_audit_sha256": None,
                    "automatic": False,
                }
                with (
                    mock.patch.object(
                        FROZEN.NGINX,
                        "load_live_lease_claim_material",
                        return_value=(
                            {
                                "owner_action": (
                                    "capture-frozen-final-snapshots"
                                ),
                                "claim_epoch": 1,
                                "nonce": CLAIM_NONCE,
                            },
                            CLAIM_SHA,
                        ),
                    ),
                    mock.patch.object(
                        FROZEN.NGINX,
                        "_load_consumption_audit",
                        return_value=(audit, CONSUMPTION_SHA),
                    ),
                    mock.patch.object(
                        FROZEN,
                        "_verify_collected_role",
                        side_effect=lambda **kwargs: journal["roles"][
                            kwargs["role"]
                        ]["collection"],
                    ),
                    mock.patch.object(
                        FROZEN,
                        "_persist_outcome",
                        return_value=OUTCOME_SHA,
                    ),
                    mock.patch.object(
                        FROZEN, "_journal_write_existing"
                    ) as write_journal,
                ):
                    recovered = FROZEN._recover_consumed_lease(
                        journal=journal,
                        inputs=fake_inputs,
                        bindings=bindings,
                        state_receipt_path=paths["state_receipt"],
                        state_receipt_sha256=RECEIPT_SHA,
                        paths=paths,
                        required_uid=0,
                    )
        self.assertTrue(recovered)
        self.assertEqual(journal["status"], "complete")
        self.assertEqual(
            journal["consumption_sha256"],
            CONSUMPTION_SHA,
        )
        write_journal.assert_called_once()

    def test_leased_call_verifies_immediately_around_rpc(self) -> None:
        events: list[str] = []

        class Lease:
            def verify(self):
                events.append("verify")

        result = FROZEN._leased_call(
            Lease(),
            label="fake",
            call=lambda: events.append("rpc") or 17,
            checkpoint=lambda name: events.append(name),
        )
        self.assertEqual(result, 17)
        self.assertEqual(
            events,
            [
                "verify",
                "before-rpc:fake",
                "rpc",
                "verify",
                "after-rpc:fake",
            ],
        )

    def test_binding_mode_must_be_frozen_final(self) -> None:
        with mock.patch.object(
            FROZEN.SOURCE,
            "load_binding",
            return_value=binding("bot_fi", mode="live-baseline"),
        ):
            with self.assertRaisesRegex(
                FROZEN.FrozenSnapshotOrchestratorError,
                "frozen-final",
            ):
                FROZEN._binding(
                    Path("/tmp/binding.json"),
                    operation_id=OPERATION_ID,
                    release_sha=RELEASE_SHA,
                    role="bot_fi",
                )

    def test_host_request_rejects_canonical_json_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_inputs = inputs(root)
            with (
                mock.patch.object(
                    FROZEN, "SECRET_ROOT_PREFIX", root / "secret"
                ),
                mock.patch.object(
                    FROZEN, "PROJECT_ROOT_PREFIX", root / "project"
                ),
                mock.patch.object(
                    FROZEN, "SOURCE_OUTPUT_ROOT", root / "output"
                ),
                mock.patch.object(
                    FROZEN.NGINX_GENERATION,
                    "DEFAULT_OPERATION_BASE",
                    root / "nginx",
                ),
            ):
                request = FROZEN.build_host_request(
                    action="freeze",
                    inputs=fake_inputs,
                    role="bot_fi",
                    binding_sha256=BINDING_SHA["bot_fi"],
                    state_receipt_sha256=RECEIPT_SHA,
                    lease_claim_sha256=CLAIM_SHA,
                    release_file_sha256=RELEASE_FILE_SHA,
                )
                request["unexpected"] = True
                encoded = FROZEN.encode_host_request(request)
                with self.assertRaisesRegex(
                    FROZEN.FrozenSnapshotOrchestratorError,
                    "exact canonical",
                ):
                    FROZEN.decode_host_request(encoded)

    def test_host_request_contains_no_nonce_or_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_inputs = inputs(root)
            with (
                mock.patch.object(
                    FROZEN, "SECRET_ROOT_PREFIX", root / "operation"
                ),
                mock.patch.object(
                    FROZEN, "PROJECT_ROOT_PREFIX", root / "project"
                ),
                mock.patch.object(
                    FROZEN, "SOURCE_OUTPUT_ROOT", root / "output"
                ),
                mock.patch.object(
                    FROZEN.NGINX_GENERATION,
                    "DEFAULT_OPERATION_BASE",
                    root / "nginx",
                ),
            ):
                request = FROZEN.build_host_request(
                    action="snapshot",
                    inputs=fake_inputs,
                    role="webapp_fi",
                    binding_sha256=BINDING_SHA["webapp_fi"],
                    state_receipt_sha256=RECEIPT_SHA,
                    lease_claim_sha256=CLAIM_SHA,
                    release_file_sha256=RELEASE_FILE_SHA,
                )
        payload = FROZEN.canonical_json(request).decode("ascii").lower()
        self.assertNotIn("nonce", payload)
        self.assertNotIn("password", payload)
        self.assertNotIn("database_url", payload)
        self.assertNotIn("redis_url", payload)
        self.assertNotIn(CLAIM_NONCE, payload)

    def test_ssh_and_scp_are_pinned_to_webapp_port_37067(self) -> None:
        identity = Path("/root/.ssh/id_ed25519")
        known_hosts = Path("/root/.ssh/known_hosts")
        ssh = FROZEN.ssh_arguments(
            identity,
            known_hosts=known_hosts,
            remote_arguments=["/usr/bin/python3", "-B", "/safe/agent.py"],
        )
        self.assertIn("37067", ssh)
        self.assertIn("StrictHostKeyChecking=yes", ssh)
        self.assertIn(f"UserKnownHostsFile={known_hosts}", ssh)
        self.assertEqual(
            ssh[-2],
            f"root@{FROZEN.BASE.WEBAPP_FI_HOST}",
        )
        self.assertNotIn(FROZEN.BASE.BOT_FI_HOST, " ".join(ssh))
        with self.assertRaises(FROZEN.FrozenSnapshotOrchestratorError):
            FROZEN._remote_command(
                ["/usr/bin/python3", "safe;touch-/tmp/unsafe"]
            )
        claim = FROZEN.canonical_paths(
            OPERATION_ID,
            RELEASE_SHA,
            lease_claim_sha256=CLAIM_SHA,
        )["lease_claim"]
        partial = claim.with_name(f".{claim.name}.{CLAIM_SHA}.transfer")
        scp = FROZEN.scp_upload_arguments(
            identity,
            known_hosts=known_hosts,
            source=Path("/root/source.json"),
            remote_destination=partial,
            operation_id=OPERATION_ID,
        )
        self.assertIn("37067", scp)
        self.assertEqual(
            scp[-1],
            f"root@{FROZEN.BASE.WEBAPP_FI_HOST}:{partial}",
        )
        with self.assertRaises(FROZEN.FrozenSnapshotOrchestratorError):
            FROZEN.scp_upload_arguments(
                identity,
                known_hosts=known_hosts,
                source=Path("/root/source.json"),
                remote_destination=(
                    FROZEN.SECRET_ROOT_PREFIX
                    / OPERATION_ID
                    / "arbitrary"
                    / ".file.transfer"
                ),
                operation_id=OPERATION_ID,
            )

    def test_freeze_worker_rpc_carries_lease_claim(self) -> None:
        request = {
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": RELEASE_TREE_SHA,
            "role": "bot_fi",
            "binding_path": "/safe/binding.json",
            "binding_sha256": BINDING_SHA["bot_fi"],
            "state_receipt_path": "/safe/receipt.json",
            "state_receipt_sha256": RECEIPT_SHA,
            "lease_claim_path": "/safe/claim.json",
            "lease_claim_sha256": CLAIM_SHA,
            "nginx_aggregate_sha256": AGGREGATE_SHA,
            "nginx_manifest_path": "/safe/manifest.json",
            "nginx_manifest_sha256": "d" * 64,
            "nginx_archive_path": "/safe/archive.tar",
            "nginx_archive_sha256": "e" * 64,
        }
        observed: list[str] = []
        worker_result = {
            "schema": FROZEN.FREEZE.RESULT_SCHEMA,
            "status": "frozen",
            "action": "freeze",
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "role": "bot_fi",
            "binding_sha256": BINDING_SHA["bot_fi"],
            "coordinated_state_receipt_sha256": RECEIPT_SHA,
            "nginx_aggregate_sha256": AGGREGATE_SHA,
            "nginx_manifest_sha256": "d" * 64,
            "live_lease_claim_sha256": CLAIM_SHA,
            "live_lease_claim_epoch": 1,
            "freeze_evidence_sha256": FREEZE_SHA["bot_fi"],
            "database_container_running": True,
            "redis_container_running": True,
            "legacy_writer_process_count": 0,
            "writer_database_client_count": 0,
            "file_mutator_process_count": 0,
            "production_mutated": True,
        }

        def runner(argv, **_kwargs):
            observed.extend(argv)
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=FROZEN.canonical_json(worker_result),
                stderr=b"",
            )

        result = FROZEN._call_freeze_worker(
            request,
            action="freeze",
            binding=binding("bot_fi"),
            runner=runner,
            paths={"freeze_worker": Path("/safe/freeze.py")},
        )
        self.assertEqual(result["live_lease_claim_sha256"], CLAIM_SHA)
        claim_index = observed.index("--live-lease-claim")
        self.assertEqual(observed[claim_index + 1], "/safe/claim.json")
        digest_index = observed.index("--live-lease-claim-sha256")
        self.assertEqual(observed[digest_index + 1], CLAIM_SHA)
        self.assertNotIn("--restore", observed)


class FrozenSnapshotPublicationTests(unittest.TestCase):
    def test_material_publication_is_create_only_root_0600(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            partial = root / ".binding.transfer"
            final = root / "binding.json"
            payload = b'{"safe":true}'
            partial.write_bytes(payload)
            os.chmod(partial, 0o600)
            digest = hashlib.sha256(payload).hexdigest()
            publication = FROZEN._promote_material_file(
                final,
                partial,
                digest=digest,
                label="test material",
                required_uid=os.geteuid(),
            )
            self.assertEqual(publication, "created")
            self.assertFalse(partial.exists())
            self.assertEqual(final.read_bytes(), payload)
            self.assertEqual(final.stat().st_mode & 0o777, 0o600)
            self.assertEqual(final.stat().st_nlink, 1)

    def test_existing_material_drift_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            partial = root / ".binding.transfer"
            final = root / "binding.json"
            partial.write_bytes(b"expected")
            final.write_bytes(b"foreign")
            os.chmod(partial, 0o600)
            os.chmod(final, 0o600)
            digest = hashlib.sha256(b"expected").hexdigest()
            with self.assertRaisesRegex(
                FROZEN.FrozenSnapshotOrchestratorError,
                "existing test material differs",
            ):
                FROZEN._promote_material_file(
                    final,
                    partial,
                    digest=digest,
                    label="test material",
                    required_uid=os.geteuid(),
                )
            self.assertEqual(final.read_bytes(), b"foreign")

    def test_collection_publish_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            partial = root / ".database.dump.transfer"
            final = root / "database.dump"
            partial.write_bytes(b"expected")
            final.write_bytes(b"foreign")
            os.chmod(partial, 0o600)
            os.chmod(final, 0o600)
            digest = hashlib.sha256(b"expected").hexdigest()
            with self.assertRaises(
                FROZEN.FrozenSnapshotOrchestratorError
            ):
                FROZEN._publish_collection_file(
                    partial,
                    final,
                    expected_sha256=digest,
                    expected_bytes=len(b"expected"),
                    required_uid=os.geteuid(),
                    maximum=1024,
                )
            self.assertEqual(final.read_bytes(), b"foreign")

    def test_journal_hash_chain_detects_event_tamper(self) -> None:
        fake_inputs = inputs(Path("/tmp"))
        bindings = {role: binding(role) for role in FROZEN.ROLES}
        journal = FROZEN._initial_journal(
            inputs=fake_inputs,
            bindings=bindings,
            state_receipt_sha256=RECEIPT_SHA,
        )
        FROZEN._append_event(
            journal,
            kind="lease-held",
            role=None,
            details={"claim_sha256": CLAIM_SHA},
        )
        FROZEN._validate_journal(
            journal,
            inputs=fake_inputs,
            bindings=bindings,
            state_receipt_sha256=RECEIPT_SHA,
        )
        tampered = copy.deepcopy(journal)
        tampered["events"][0]["kind"] = "lease-lost"
        with self.assertRaises(
            FROZEN.FrozenSnapshotOrchestratorError
        ):
            FROZEN._validate_journal(
                tampered,
                inputs=fake_inputs,
                bindings=bindings,
                state_receipt_sha256=RECEIPT_SHA,
            )

    def test_non_0600_material_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            partial = root / ".binding.transfer"
            final = root / "binding.json"
            partial.write_bytes(b"expected")
            os.chmod(partial, 0o644)
            digest = hashlib.sha256(b"expected").hexdigest()
            with self.assertRaises(
                FROZEN.FrozenSnapshotOrchestratorError
            ):
                FROZEN._promote_material_file(
                    final,
                    partial,
                    digest=digest,
                    label="test material",
                    required_uid=os.geteuid(),
                )

    def test_create_link_crash_residue_is_reconciled_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            final = root / "journal.json"
            temporary = root / ".journal.json.1234.0123456789abcdef.tmp"
            temporary.write_bytes(b'{"state":"safe"}')
            os.chmod(temporary, 0o600)
            os.link(temporary, final)
            self.assertEqual(final.stat().st_nlink, 2)
            FROZEN._reconcile_create_link(
                final,
                required_uid=os.geteuid(),
            )
            self.assertFalse(temporary.exists())
            self.assertEqual(final.stat().st_nlink, 1)
            self.assertEqual(final.read_bytes(), b'{"state":"safe"}')


if __name__ == "__main__":
    unittest.main()
