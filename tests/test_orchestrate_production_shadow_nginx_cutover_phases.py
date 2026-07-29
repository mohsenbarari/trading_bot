from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest import mock

from core.canonical_json import canonical_json_bytes
from scripts import (
    orchestrate_production_shadow_nginx_cutover_phases as MODULE,
)
from tests.test_production_shadow_cutover_controller import (
    manifest_payload,
)
from tests.test_verify_production_shadow_phase_evidence import (
    APPROVAL_SHA256,
    MANIFEST_ARTIFACTS,
    MANIFEST_SHA256,
    PLAN_SHA256,
    evidence_for,
)


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
OPERATION_ID = "22222222-2222-4222-8222-222222222222"
RELEASE_SHA = "a" * 40
RELEASE_TREE_SHA = "c" * 40
LEGACY_RELEASE_SHA = "b" * 40


def private_directory(path: Path) -> Path:
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.chmod(0o700)
    return path


def private_document(path: Path, document: dict) -> str:
    payload = canonical_json_bytes(document)
    path.write_bytes(payload)
    path.chmod(0o600)
    return hashlib.sha256(payload).hexdigest()


class ExternalLivenessPipe:
    """Keep the liveness writer in a separate controller process."""

    def __init__(self) -> None:
        self.read_fd, write_fd = os.pipe()
        command_read_fd, self._command_write_fd = os.pipe()
        self._pid = os.fork()
        if self._pid == 0:
            try:
                os.close(self.read_fd)
                os.close(self._command_write_fd)
                try:
                    os.read(command_read_fd, 1)
                finally:
                    os.close(command_read_fd)
                    os.close(write_fd)
            finally:
                os._exit(0)
        os.close(write_fd)
        os.close(command_read_fd)

    def close_writer(self) -> None:
        if self._command_write_fd >= 0:
            os.close(self._command_write_fd)
            self._command_write_fd = -1
        if self._pid > 0:
            waited, _status = os.waitpid(self._pid, 0)
            if waited != self._pid:
                raise AssertionError("liveness controller was not reaped")
            self._pid = -1

    def __enter__(self) -> "ExternalLivenessPipe":
        return self

    def __exit__(self, *_exc: object) -> None:
        try:
            self.close_writer()
        finally:
            os.close(self.read_fd)


def phase_spec(phase: str):  # noqa: ANN201
    return next(
        spec for spec in MODULE.CONTROLLER.PHASE_SPECS
        if spec.phase == phase
    )


class FakeJournal:
    def __init__(
        self,
        prior_sha256: dict[str, str],
        *,
        completed_extra: tuple[str, ...] = (),
        started_phase: str | None = None,
    ):
        completed = [*MODULE.INITIAL_PRIOR_PHASES, *completed_extra]
        self.state = {
            "status": "phase_started" if started_phase else "active",
            "completed_phases": completed,
            "phase_evidence_sha256": {
                phase: prior_sha256[phase] for phase in completed
            },
            "phase_verification_sha256": {
                phase: "f" * 64 for phase in completed
            },
            "started_phase": started_phase,
            "first_business_write_allowed": False,
            "state_sha256": "1" * 64,
            "event_tail_sha256": "2" * 64,
        }
        self.events: list[tuple[str, str]] = []

    def assert_bindings(self, **_bindings):  # noqa: ANN003, ANN201
        return copy.deepcopy(self.state)

    def begin_phase(self, phase: str):  # noqa: ANN201
        self.events.append(("begin", phase))
        if self.state["started_phase"] == phase:
            return copy.deepcopy(self.state)
        expected = MODULE.CONTROLLER.PHASES[
            len(self.state["completed_phases"])
        ]
        if phase != expected:
            raise MODULE.CONTROLLER.CutoverContractError("out of order")
        self.state["status"] = "phase_started"
        self.state["started_phase"] = phase
        return copy.deepcopy(self.state)

    def complete_phase(self, phase: str, *, verification):  # noqa: ANN001, ANN201
        self.events.append(("complete", phase))
        if self.state["started_phase"] != phase:
            raise MODULE.CONTROLLER.CutoverContractError("not started")
        self.state["completed_phases"].append(phase)
        self.state["phase_evidence_sha256"][phase] = (
            verification.evidence_sha256
        )
        self.state["phase_verification_sha256"][phase] = (
            verification.receipt_sha256
        )
        self.state["status"] = "active"
        self.state["started_phase"] = None
        self.state["state_sha256"] = hashlib.sha256(
            canonical_json_bytes(self.state["completed_phases"])
        ).hexdigest()
        self.state["event_tail_sha256"] = hashlib.sha256(
            canonical_json_bytes(self.events)
        ).hexdigest()
        return copy.deepcopy(self.state)


class FakeNginxExecutor:
    def __init__(
        self,
        context: MODULE.BridgeContext,
        events: list[tuple[str, str]],
    ):
        self.context = context
        self.events = events
        self.calls: list[tuple[str, str | None]] = []
        self.state = "legacy-normal"
        self.count = 0
        self.compensate_activation = False
        self.fabricate_readback = False
        self.reuse_action_receipt = False
        self.raise_baseexception: BaseException | None = None
        self.signal_during_action = False

    def _readbacks(self, state: str) -> dict:
        generation = self.context.nginx_inputs.aggregate[
            "generation_sha256"
        ][state]
        return {
            role: {
                "expected_host": self.context.manifest["topology"][role][
                    "host"
                ],
                "state": state,
                "generation_sha256": generation,
            }
            for role in MODULE.ROLE_ORDER
        }

    @staticmethod
    def _external(state: str) -> dict:
        blocked = state == "legacy-frozen"
        probes = (
            {"get": 200, "post": 503, "websocket": 503}
            if blocked
            else {"get": 200}
        )
        return {
            "states": [state],
            "states_by_role": {
                role: state for role in MODULE.ROLE_ORDER
            },
            "blocked_probes_performed": blocked,
            "write_method_probe_performed": blocked,
            "vhosts": {
                "coin.362514.ir": dict(probes),
                "mini-app.362514.ir": dict(probes),
                "coin.gold-trade.ir": dict(probes),
            },
        }

    def __call__(self, **kwargs):  # noqa: ANN003, ANN201
        action = kwargs["action"]
        target = kwargs["target_state"]
        self.calls.append((action, target))
        self.events.append(("nginx", action))
        if self.raise_baseexception is not None:
            error = self.raise_baseexception
            self.raise_baseexception = None
            raise error
        if action == "activate" and self.compensate_activation:
            self.state = "legacy-normal"
            result = self._result(
                action,
                target,
                status="compensated-failed",
                state="legacy-normal",
            )
        else:
            if action == "activate":
                self.state = str(target)
                status = "activated"
            elif action == "install":
                status = "installed"
            elif action == "test":
                status = "tested"
            elif action == "readback":
                status = "read-back"
            else:
                raise AssertionError(action)
            result = self._result(
                action,
                target,
                status=status,
                state=self.state,
            )
        if self.signal_during_action and action != "readback":
            self.signal_during_action = False
            handler = signal.getsignal(signal.SIGTERM)
            if not callable(handler):
                raise AssertionError("SIGTERM handler is not installed")
            handler(signal.SIGTERM, None)
        return result

    def _result(
        self,
        action: str,
        target: str | None,
        *,
        status: str,
        state: str,
    ) -> dict:
        if not (
            action == "readback"
            and self.reuse_action_receipt
            and self.count
        ):
            self.count += 1
        source_action = (
            "install"
            if action == "readback" and self.fabricate_readback
            else action
        )
        receipt = {
            "source_action": source_action,
            "requested_target_state": target,
            "coordinator_status": status,
            "state": state,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": RELEASE_TREE_SHA,
            "aggregate_sha256": (
                self.context.nginx_inputs.aggregate_sha256
            ),
            "role_bindings": {
                role: {
                    "expected_host": self.context.manifest["topology"][
                        role
                    ]["host"],
                    "manifest_sha256": (
                        self.context.nginx_inputs.roles[
                            role
                        ].manifest_sha256
                    ),
                    "archive_sha256": (
                        self.context.nginx_inputs.roles[role].manifest[
                            "archive"
                        ]["sha256"]
                    ),
                }
                for role in MODULE.ROLE_ORDER
            },
            "global_generation_sha256": (
                self.context.nginx_inputs.aggregate[
                    "generation_sha256"
                ][state]
            ),
            "vhost_generation_sha256": {
                "coin.362514.ir": {},
                "mini-app.362514.ir": {},
                "coin.gold-trade.ir": {},
            },
            "readbacks": self._readbacks(state),
            "external_readback": self._external(state),
            "journal_sha256": "3" * 64,
            "evidence_count": self.count,
            "evidence_tail_sha256": (
                f"{self.count:064x}"[-64:]
                if self.count
                else "4" * 64
            ),
            "production_contacted": True,
            "active_configuration_mutated": False,
            "current_mutated": False,
            "container_mutated": False,
            "volume_mutated": False,
            "data_mutated": False,
        }
        payload = canonical_json_bytes(receipt)
        digest = hashlib.sha256(payload).hexdigest()
        path = (
            self.context.nginx_inputs.receipts_root
            / f"{state}-{digest}.json"
        )
        if not path.exists():
            path.write_bytes(payload)
            path.chmod(0o600)
        return {
            "schema": MODULE.NGINX.RESULT_SCHEMA,
            "status": status,
            "action": action,
            "target_state": target,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": RELEASE_TREE_SHA,
            "aggregate_sha256": (
                self.context.nginx_inputs.aggregate_sha256
            ),
            "active_configuration_mutated": (
                action == "activate"
                and status in {"activated", "compensated-failed"}
            ),
            "current_mutated": False,
            "container_mutated": False,
            "volume_mutated": False,
            "data_mutated": False,
            "state_receipt_path": os.fspath(path),
            "state_receipt_sha256": digest,
        }


class FatalControl(BaseException):
    pass


class NginxCutoverPhaseBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        private_directory(self.root)
        self.evidence_root = private_directory(self.root / "evidence")
        self.receipts_root = private_directory(self.root / "receipts")
        self.prior_paths: dict[str, Path] = {}
        self.prior_sha256: dict[str, str] = {}
        for phase in MODULE.INITIAL_PRIOR_PHASES:
            path = self.root / f"{phase}.json"
            digest = private_document(
                path,
                evidence_for(phase, captured_at=NOW),
            )
            self.prior_paths[phase] = path
            self.prior_sha256[phase] = digest
        manifest = manifest_payload()
        manifest["artifacts"] = copy.deepcopy(MANIFEST_ARTIFACTS)
        manifest["deployment"]["controller_evidence_root"] = os.fspath(
            self.evidence_root
        )
        manifest["deployment"]["controller_journal_path"] = os.fspath(
            self.root / "journal.json"
        )
        aggregate = {
            "generation_sha256": {
                "legacy-normal": manifest["artifacts"][
                    "nginx_rollback_generation_sha256"
                ],
                "legacy-frozen": manifest["artifacts"][
                    "nginx_freeze_generation_sha256"
                ],
                "shadow-readonly": manifest["artifacts"][
                    "nginx_shadow_readonly_generation_sha256"
                ],
                "shadow-writable": manifest["artifacts"][
                    "nginx_shadow_writable_generation_sha256"
                ],
            }
        }
        role_rows = {
            "bot_fi": SimpleNamespace(
                manifest_sha256="1" * 64,
                manifest={"archive": {"sha256": "2" * 64}},
            ),
            "webapp_fi": SimpleNamespace(
                manifest_sha256="3" * 64,
                manifest={"archive": {"sha256": "4" * 64}},
            ),
        }
        release_root = Path(
            MODULE.CONTROLLER._operation_release_root(  # noqa: SLF001
                OPERATION_ID,
                RELEASE_SHA,
            )
        )
        nginx_inputs = SimpleNamespace(
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            release_tree_sha=RELEASE_TREE_SHA,
            release_root=release_root,
            aggregate_sha256="6" * 64,
            worker_path=release_root / MODULE.NGINX.WORKER_RELATIVE_PATH,
            worker_sha256="7" * 64,
            ssh_identity_sha256="8" * 64,
            aggregate=aggregate,
            roles=role_rows,
            receipts_root=self.receipts_root,
        )
        paths = MODULE.BridgePaths(
            aggregate=self.root / "aggregate.json",
            bot_fi_manifest=self.root / "bot-manifest.json",
            bot_fi_archive=self.root / "bot.tar",
            webapp_fi_manifest=self.root / "web-manifest.json",
            webapp_fi_archive=self.root / "web.tar",
            known_hosts=self.root / "known-hosts",
            ssh_identity=self.root / "identity",
        )
        request = {
            "approval_sha256": APPROVAL_SHA256,
            "constraints": {
                field: False for field in MODULE.CONSTRAINT_FIELDS
            },
        }
        self.context = MODULE.BridgeContext(
            request_path=self.root / "request.json",
            request=request,
            manifest_path=self.root / "manifest.json",
            manifest=manifest,
            manifest_sha256=MANIFEST_SHA256,
            plan={"plan_sha256": PLAN_SHA256},
            approval_path=self.root / "approval.json",
            approval_policy_path=self.root / "policy.json",
            nginx_paths=paths,
            nginx_inputs=nginx_inputs,
            prior_paths=self.prior_paths,
            output_root=self.evidence_root / "nginx-cutover-phases",
        )
        self.events: list[tuple[str, str]] = []
        self.journal = FakeJournal(self.prior_sha256)
        self.executor = FakeNginxExecutor(self.context, self.events)
        self.patches = [
            mock.patch.object(
                MODULE.CONTROLLER,
                "ProductionCutoverJournal",
                return_value=self.journal,
            ),
            mock.patch.object(
                MODULE,
                "_verify_authorization",
                side_effect=lambda _context: self.events.append(
                    ("auth", "verified")
                ),
            ),
            mock.patch.object(
                MODULE,
                "_revalidate_nginx_inputs",
                return_value=None,
            ),
            mock.patch.object(
                MODULE.NGINX,
                "load_state_receipt",
                side_effect=self._load_receipt,
            ),
            mock.patch.object(
                MODULE.CONTROLLER,
                "_run_release_phase_verifier",
                side_effect=self._verify,
            ),
            mock.patch.object(
                MODULE.CONTROLLER,
                "_persist_phase_verification_receipt",
                return_value=self.root / "verification.json",
            ),
        ]
        self.mocks = [patcher.start() for patcher in self.patches]

    def tearDown(self) -> None:
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temporary.cleanup()

    @staticmethod
    def _load_receipt(
        path: Path,
        expected_state: str,
        *_identity,
    ) -> tuple[dict, str]:
        payload = path.read_bytes()
        document = json.loads(payload)
        if document["state"] != expected_state:
            raise MODULE.NGINX.NginxCoordinatorError("state differs")
        return document, hashlib.sha256(payload).hexdigest()

    def _verify(self, **kwargs):  # noqa: ANN003, ANN201
        self.events.append(("verify", kwargs["phase"]))
        payload = kwargs["evidence_path"].read_bytes()
        evidence_sha256 = hashlib.sha256(payload).hexdigest()
        receipt = canonical_json_bytes(
            {
                "phase": kwargs["phase"],
                "evidence_sha256": evidence_sha256,
            }
        )
        return (
            MODULE.CONTROLLER.VerifiedPhaseCompletion(
                phase=kwargs["phase"],
                evidence_sha256=evidence_sha256,
                receipt_sha256=hashlib.sha256(receipt).hexdigest(),
            ),
            receipt,
        )

    def _execute(self, **kwargs):  # noqa: ANN003, ANN201
        with ExternalLivenessPipe() as liveness:
            return MODULE.execute_bridge(
                self.context,
                apply=True,
                confirm=MODULE.confirmation_phrase(self.context),
                controller_liveness_fd=liveness.read_fd,
                nginx_executor=self.executor,
                now_fn=lambda: NOW,
                **kwargs,
            )

    def test_phase_mapping_is_exact_and_excludes_other_corridors(self) -> None:
        self.assertEqual(
            MODULE.PHASE_ACTIONS,
            {
                "freeze_generation_install": (
                    "install",
                    None,
                    "legacy-normal",
                ),
                "freeze_generation_test": (
                    "test",
                    "legacy-frozen",
                    "legacy-normal",
                ),
                "freeze_generation_activate": (
                    "activate",
                    "legacy-frozen",
                    "legacy-frozen",
                ),
            },
        )
        rendered = canonical_json_bytes(MODULE.PHASE_ACTIONS)
        self.assertNotIn(b"shadow-writable", rendered)
        self.assertNotIn(b"rollback-freeze", rendered)

    def test_plan_is_nonmutating_and_rejects_apply_authority(self) -> None:
        result = MODULE.execute_bridge(self.context)
        self.assertEqual(result["status"], "planned")
        self.assertFalse(result["production_contacted"])
        self.assertFalse(result["journal_mutated"])
        self.assertFalse(result["writable_generation_supported"])
        self.assertFalse(result["postcommit_supported"])
        self.assertFalse(result["rollback_supported"])
        with self.assertRaisesRegex(
            MODULE.NginxCutoverPhaseBridgeError,
            "plan mode",
        ):
            MODULE.execute_bridge(
                self.context,
                confirm=MODULE.confirmation_phrase(self.context),
            )

    def test_apply_requires_exact_confirmation_and_anonymous_pipe(self) -> None:
        with self.assertRaisesRegex(
            MODULE.NginxCutoverPhaseBridgeError,
            "confirmation",
        ):
            MODULE.execute_bridge(
                self.context,
                apply=True,
                confirm="wrong",
            )
        with self.assertRaisesRegex(
            MODULE.NginxCutoverPhaseBridgeError,
            "anonymous controller-liveness",
        ):
            MODULE.execute_bridge(
                self.context,
                apply=True,
                confirm=MODULE.confirmation_phrase(self.context),
            )
        regular = self.root / "not-a-pipe"
        regular.write_bytes(b"")
        regular.chmod(0o600)
        descriptor = os.open(regular, os.O_RDONLY)
        try:
            with self.assertRaisesRegex(
                MODULE.NginxCutoverPhaseBridgeError,
                "anonymous read pipe",
            ):
                MODULE.execute_bridge(
                    self.context,
                    apply=True,
                    confirm=MODULE.confirmation_phrase(self.context),
                    controller_liveness_fd=descriptor,
                )
        finally:
            os.close(descriptor)

    def test_full_three_phase_flow_begins_before_action_and_verifies(self) -> None:
        result = self._execute()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            self.executor.calls,
            [
                ("install", None),
                ("readback", None),
                ("test", "legacy-frozen"),
                ("readback", None),
                ("activate", "legacy-frozen"),
                ("readback", None),
            ],
        )
        self.assertEqual(
            self.journal.events,
            [
                ("begin", "freeze_generation_install"),
                ("complete", "freeze_generation_install"),
                ("begin", "freeze_generation_test"),
                ("complete", "freeze_generation_test"),
                ("begin", "freeze_generation_activate"),
                ("complete", "freeze_generation_activate"),
            ],
        )
        for phase in MODULE.PHASES:
            begin_index = self.events.index(("nginx", MODULE.PHASE_ACTIONS[phase][0]))
            verify_index = self.events.index(("verify", phase))
            self.assertLess(begin_index, verify_index)
        self.assertEqual(result["next_phase"], "stop_legacy_writers")
        self.assertTrue(result["production_contacted"])
        self.assertTrue(result["journal_mutated"])
        self.assertTrue(result["active_configuration_mutated"])
        self.assertFalse(result["business_write_observed"])
        self.assertFalse(result["current_mutated"])
        self.assertFalse(result["container_mutated"])
        self.assertFalse(result["volume_mutated"])
        self.assertFalse(result["data_mutated"])
        self.assertFalse(result["object_storage_mutated"])

    def test_generated_evidence_is_root_only_and_receipt_derived(self) -> None:
        result = self._execute()
        for phase, digest in result["phase_evidence_sha256"].items():
            path = (
                self.context.output_root
                / "phases"
                / phase
                / "evidence"
                / f"{phase.replace('_', '-')}-{digest}.json"
            )
            metadata = path.stat()
            self.assertEqual(metadata.st_uid, 0)
            self.assertEqual(metadata.st_gid, 0)
            self.assertEqual(metadata.st_mode & 0o777, 0o600)
            evidence = json.loads(path.read_bytes())
            self.assertEqual(evidence["phase"], phase)
            self.assertFalse(evidence["business_write_observed"])
            self.assertEqual(
                evidence["claims"][
                    "manifest_freeze_generation_sha256"
                ]["value"],
                self.context.manifest["artifacts"][
                    "nginx_freeze_generation_sha256"
                ],
            )
        activation = json.loads(
            (
                self.context.output_root
                / "phases"
                / "freeze_generation_activate"
                / "evidence"
                / (
                    "freeze-generation-activate-"
                    + result["phase_evidence_sha256"][
                        "freeze_generation_activate"
                    ]
                    + ".json"
                )
            ).read_bytes()
        )
        self.assertEqual(
            activation["claims"]["write_blocked_vhost_count"]["value"],
            3,
        )
        self.assertTrue(
            activation["claims"][
                "per_host_generation_readback_verified"
            ]["value"]
        )
        self.assertTrue(
            activation["claims"]["compensating_restore_ready"]["value"]
        )

    def test_generated_evidence_passes_the_real_verifier_contract(self) -> None:
        result = self._execute()
        evidence_paths = dict(self.prior_paths)
        for phase in MODULE.PHASES:
            digest = result["phase_evidence_sha256"][phase]
            phase_root = (
                self.context.output_root / "phases" / phase
            )
            evidence_path = (
                phase_root
                / "evidence"
                / f"{phase.replace('_', '-')}-{digest}.json"
            )
            evidence, observed_digest = (
                MODULE.VERIFY.read_root_only_evidence(evidence_path)
            )
            self.assertEqual(observed_digest, digest)
            role_paths = {}
            for role in MODULE.ROLE_ORDER:
                matches = list(
                    (phase_root / "role-validation").glob(
                        f"role-validation-{role.replace('_', '-')}-*.json"
                    )
                )
                self.assertEqual(len(matches), 1)
                role_paths[role] = matches[0]
            claim_paths = {}
            for claim in MODULE.VERIFY.PHASE_CLAIM_RULES[phase]:
                matches = list(
                    (phase_root / "claim-sources").glob(
                        f"claim-{claim.replace('_', '-')}-*.json"
                    )
                )
                self.assertEqual(len(matches), 1)
                claim_paths[claim] = matches[0]
            request_hashes, role_hashes, observed_at = (
                MODULE.VERIFY._read_role_validation_records(  # noqa: SLF001
                    [
                        f"{role}={role_paths[role]}"
                        for role in MODULE.ROLE_ORDER
                    ],
                    phase=phase,
                    manifest=self.context.manifest,
                    manifest_sha256=self.context.manifest_sha256,
                )
            )
            dynamic, claim_hashes = (
                MODULE.VERIFY._read_claim_source_records(  # noqa: SLF001
                    [
                        f"{claim}={claim_paths[claim]}"
                        for claim in MODULE.VERIFY.PHASE_CLAIM_RULES[
                            phase
                        ]
                    ],
                    phase=phase,
                    manifest=self.context.manifest,
                    manifest_sha256=self.context.manifest_sha256,
                    now=NOW,
                )
            )
            prior_phases = MODULE.CONTROLLER.PHASES[
                : MODULE.CONTROLLER.PHASES.index(phase)
            ]
            prior_records = {}
            prior_digests = {}
            for prior in prior_phases:
                document, prior_digest = (
                    MODULE.VERIFY.read_root_only_evidence(
                        evidence_paths[prior]
                    )
                )
                prior_records[prior] = {
                    "document": document,
                    "file_sha256": prior_digest,
                }
                prior_digests[prior] = prior_digest
            verification = MODULE.VERIFY.verify_phase_evidence(
                evidence,
                expected_phase=phase,
                expected_campaign_id=CAMPAIGN_ID,
                expected_operation_id=OPERATION_ID,
                expected_release_sha=RELEASE_SHA,
                expected_legacy_release_sha=LEGACY_RELEASE_SHA,
                expected_manifest_sha256=MANIFEST_SHA256,
                expected_plan_sha256=PLAN_SHA256,
                expected_approval_sha256=APPROVAL_SHA256,
                expected_phase_evidence_schema_sha256=(
                    MODULE.VERIFY.PHASE_EVIDENCE_CONTRACT_SHA256
                ),
                expected_manifest_artifacts=self.context.manifest[
                    "artifacts"
                ],
                expected_role_request_sha256=request_hashes,
                expected_role_source_artifact_sha256=role_hashes,
                expected_role_observed_at=observed_at,
                expected_dynamic_claim_values=dynamic,
                expected_claim_source_sha256=claim_hashes,
                expected_prior_phase_evidence_sha256=prior_digests,
                prior_phase_evidence_records=prior_records,
                now=NOW,
                evidence_file_sha256=digest,
            )
            self.assertEqual(verification["status"], "verified")
            evidence_paths[phase] = evidence_path

    def test_release_verifier_receives_exact_sources_and_prior_prefix(self) -> None:
        verifier = self.mocks[4]
        self._execute()
        self.assertEqual(verifier.call_count, 3)
        for index, call in enumerate(verifier.call_args_list):
            phase = MODULE.PHASES[index]
            kwargs = call.kwargs
            self.assertEqual(kwargs["phase"], phase)
            self.assertEqual(len(kwargs["role_validation"]), 2)
            self.assertEqual(
                len(kwargs["claim_source"]),
                len(MODULE.VERIFY.PHASE_CLAIM_RULES[phase]),
            )
            self.assertEqual(
                len(kwargs["prior_phase_evidence"]),
                MODULE.CONTROLLER.PHASES.index(phase),
            )
            self.assertEqual(
                kwargs["approval_path"],
                self.context.approval_path,
            )
            self.assertEqual(
                kwargs["approval_policy_path"],
                self.context.approval_policy_path,
            )

    def test_compensated_partial_activation_stays_started_and_resumable(self) -> None:
        self.executor.compensate_activation = True
        with self.assertRaisesRegex(
            MODULE.NginxCutoverPhaseBridgeError,
            "compensated and remains resumable",
        ):
            self._execute()
        self.assertEqual(
            self.journal.state["completed_phases"],
            [
                *MODULE.INITIAL_PRIOR_PHASES,
                "freeze_generation_install",
                "freeze_generation_test",
            ],
        )
        self.assertEqual(
            self.journal.state["started_phase"],
            "freeze_generation_activate",
        )
        self.assertEqual(self.executor.calls[-1], ("readback", None))
        self.assertNotIn(
            ("complete", "freeze_generation_activate"),
            self.journal.events,
        )

    def test_started_phase_resume_replays_idempotent_action(self) -> None:
        self.journal.state["status"] = "phase_started"
        self.journal.state["started_phase"] = "freeze_generation_install"
        result = self._execute()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            self.journal.events[0],
            ("begin", "freeze_generation_install"),
        )
        self.assertEqual(self.executor.calls[0], ("install", None))

    def test_completed_resume_reuses_evidence_without_nginx_calls(self) -> None:
        first = self._execute()
        self.executor.calls.clear()
        self.journal.events.clear()
        second = self._execute()
        self.assertEqual(second["status"], "completed")
        self.assertEqual(self.executor.calls, [])
        self.assertEqual(self.journal.events, [])
        self.assertEqual(
            second["phase_evidence_sha256"],
            first["phase_evidence_sha256"],
        )
        self.assertFalse(second["production_contacted"])
        self.assertFalse(second["journal_mutated"])
        self.assertFalse(second["active_configuration_mutated"])
        self.assertTrue(
            all(
                row["status"] == "reused-completed"
                for row in second["phase_results"].values()
            )
        )

    def test_completed_resume_fails_on_tampered_evidence(self) -> None:
        first = self._execute()
        phase = "freeze_generation_install"
        digest = first["phase_evidence_sha256"][phase]
        path = (
            self.context.output_root
            / "phases"
            / phase
            / "evidence"
            / f"{phase.replace('_', '-')}-{digest}.json"
        )
        path.write_bytes(b'{"tampered":true}')
        path.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.NginxCutoverPhaseBridgeError,
            "completed freeze_generation_install evidence",
        ):
            self._execute()
        self.assertEqual(self.executor.calls[-6:], [
            ("install", None),
            ("readback", None),
            ("test", "legacy-frozen"),
            ("readback", None),
            ("activate", "legacy-frozen"),
            ("readback", None),
        ])

    def test_fabricated_or_non_delayed_readback_fails_closed(self) -> None:
        self.executor.fabricate_readback = True
        with self.assertRaisesRegex(
            MODULE.NginxCutoverPhaseBridgeError,
            "result or receipt binding differs",
        ):
            self._execute()
        self.assertEqual(
            self.journal.state["started_phase"],
            "freeze_generation_install",
        )
        self.executor.fabricate_readback = False
        self.executor.reuse_action_receipt = True
        self.executor.count = 0
        self.executor.calls.clear()
        self.journal = FakeJournal(self.prior_sha256)
        self.patches[0].stop()
        self.patches[0] = mock.patch.object(
            MODULE.CONTROLLER,
            "ProductionCutoverJournal",
            return_value=self.journal,
        )
        self.patches[0].start()
        with self.assertRaisesRegex(
            MODULE.NginxCutoverPhaseBridgeError,
            "delayed external readback",
        ):
            self._execute()

    def test_expired_approval_blocks_before_or_after_action(self) -> None:
        authorization = self.mocks[1]
        authorization.side_effect = MODULE.NginxCutoverPhaseBridgeError(
            "production cutover authorization is invalid or expired"
        )
        with self.assertRaisesRegex(
            MODULE.NginxCutoverPhaseBridgeError,
            "invalid or expired",
        ):
            self._execute()
        self.assertEqual(self.executor.calls, [])
        authorization.side_effect = [
            None,
            None,
            MODULE.NginxCutoverPhaseBridgeError(
                "production cutover authorization is invalid or expired"
            ),
        ]
        authorization.reset_mock()
        with self.assertRaisesRegex(
            MODULE.NginxCutoverPhaseBridgeError,
            "invalid or expired",
        ):
            self._execute()
        self.assertEqual(
            self.journal.state["started_phase"],
            "freeze_generation_install",
        )
        self.assertEqual(
            self.executor.calls,
            [("install", None), ("readback", None)],
        )

    def test_baseexception_is_preserved_and_phase_remains_started(self) -> None:
        original = FatalControl("fatal controller failure")
        self.executor.raise_baseexception = original
        with self.assertRaises(FatalControl) as raised:
            self._execute()
        self.assertIs(raised.exception, original)
        self.assertEqual(
            self.journal.state["started_phase"],
            "freeze_generation_install",
        )
        self.assertNotIn(
            ("complete", "freeze_generation_install"),
            self.journal.events,
        )

    def test_preexisting_controller_eof_blocks_before_journal_begin(self) -> None:
        with ExternalLivenessPipe() as liveness:
            liveness.close_writer()
            with self.assertRaisesRegex(
                MODULE.NginxCutoverPhaseBridgeCancellation,
                "reached EOF",
            ):
                MODULE.execute_bridge(
                    self.context,
                    apply=True,
                    confirm=MODULE.confirmation_phrase(self.context),
                    controller_liveness_fd=liveness.read_fd,
                    nginx_executor=self.executor,
                    now_fn=lambda: NOW,
                )
        self.assertEqual(self.journal.events, [])
        self.assertEqual(self.executor.calls, [])

    def test_signal_during_begin_defers_until_durable_readback(self) -> None:
        original_begin = self.journal.begin_phase

        def begin_and_signal(phase):  # noqa: ANN001, ANN202
            state = original_begin(phase)
            handler = signal.getsignal(signal.SIGTERM)
            if not callable(handler):
                raise AssertionError("SIGTERM handler is not installed")
            handler(signal.SIGTERM, None)
            return state

        with mock.patch.object(
            self.journal,
            "begin_phase",
            side_effect=begin_and_signal,
        ):
            with self.assertRaises(
                MODULE.NginxCutoverPhaseBridgeCancellation
            ):
                self._execute()
        self.assertEqual(
            self.journal.state["started_phase"],
            "freeze_generation_install",
        )
        self.assertEqual(self.executor.calls, [])

    def test_signal_during_action_defers_through_delayed_readback(self) -> None:
        self.executor.signal_during_action = True
        with self.assertRaises(
            MODULE.NginxCutoverPhaseBridgeCancellation
        ):
            self._execute()
        self.assertEqual(
            self.journal.state["started_phase"],
            "freeze_generation_install",
        )
        self.assertEqual(
            self.executor.calls,
            [("install", None), ("readback", None)],
        )
        self.assertNotIn(
            ("complete", "freeze_generation_install"),
            self.journal.events,
        )

    def test_controller_eof_and_one_shot_signal_cancel_and_restore(self) -> None:
        before = {
            signum: signal.getsignal(signum)
            for signum in (
                signal.SIGHUP,
                signal.SIGINT,
                signal.SIGTERM,
                signal.SIGUSR1,
            )
        }
        with ExternalLivenessPipe() as liveness:
            with self.assertRaises(
                MODULE.NginxCutoverPhaseBridgeCancellation
            ):
                with MODULE._signal_cancellation_guard():
                    with MODULE.ControllerLiveness(
                        liveness.read_fd
                    ):
                        liveness.close_writer()
                        deadline = time.monotonic() + 2
                        while time.monotonic() < deadline:
                            time.sleep(0.02)
                        self.fail("liveness EOF did not cancel")
        self.assertEqual(
            {
                signum: signal.getsignal(signum)
                for signum in before
            },
            before,
        )
        with self.assertRaises(
            MODULE.NginxCutoverPhaseBridgeCancellation
        ):
            with MODULE._signal_cancellation_guard():
                handler = signal.getsignal(signal.SIGTERM)
                self.assertTrue(callable(handler))
                try:
                    handler(signal.SIGTERM, None)
                except MODULE.NginxCutoverPhaseBridgeCancellation:
                    second = signal.getsignal(signal.SIGINT)
                    self.assertTrue(callable(second))
                    second(signal.SIGINT, None)
                    raise

    def test_controller_liveness_rejects_local_duplicate_writer(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            with self.assertRaisesRegex(
                MODULE.NginxCutoverPhaseBridgeError,
                "retains a liveness pipe writer",
            ):
                MODULE.ControllerLiveness(read_fd)
        finally:
            os.close(write_fd)
            os.close(read_fd)

    def test_liveness_setup_and_thread_start_failures_close_duplicate(self) -> None:
        with ExternalLivenessPipe() as external:
            duplicated: list[int] = []
            real_dup = os.dup

            def duplicate(descriptor):  # noqa: ANN001, ANN202
                result = real_dup(descriptor)
                duplicated.append(result)
                return result

            setup_failure = FatalControl("set-blocking failed")
            with mock.patch.object(
                MODULE.os,
                "dup",
                side_effect=duplicate,
            ), mock.patch.object(
                MODULE.os,
                "set_blocking",
                side_effect=setup_failure,
            ):
                with self.assertRaises(FatalControl) as raised:
                    MODULE.ControllerLiveness(external.read_fd)
            self.assertIs(raised.exception, setup_failure)
            with self.assertRaises(OSError):
                os.fstat(duplicated[0])

            liveness = MODULE.ControllerLiveness(external.read_fd)
            liveness_fd = liveness._fd  # noqa: SLF001
            start_failure = FatalControl("thread start failed")
            with mock.patch.object(
                liveness._thread,  # noqa: SLF001
                "start",
                side_effect=start_failure,
            ):
                with self.assertRaises(FatalControl) as raised:
                    liveness.__enter__()
            self.assertIs(raised.exception, start_failure)
            with self.assertRaises(OSError):
                os.fstat(liveness_fd)

    def test_liveness_cleanup_errors_do_not_replace_primary(self) -> None:
        with ExternalLivenessPipe() as external:
            liveness = MODULE.ControllerLiveness(external.read_fd)
            liveness.__enter__()
            primary = FatalControl("primary failure")
            join_failure = OSError("join failure")
            alive_failure = RuntimeError("alive failure")
            try:
                with mock.patch.object(
                    liveness._thread,  # noqa: SLF001
                    "join",
                    side_effect=join_failure,
                ), mock.patch.object(
                    liveness._thread,  # noqa: SLF001
                    "is_alive",
                    side_effect=alive_failure,
                ):
                    try:
                        raise primary
                    except FatalControl as caught:
                        liveness.__exit__(
                            type(caught),
                            caught,
                            caught.__traceback__,
                        )
                        raise
            except FatalControl as raised:
                self.assertIs(raised, primary)
            else:
                self.fail("primary BaseException was not preserved")
            liveness._thread.join(timeout=1.0)  # noqa: SLF001
            self.assertFalse(liveness._thread.is_alive())  # noqa: SLF001
            notes = getattr(primary, "__notes__", [])
            self.assertTrue(
                any("join failure" in note for note in notes)
            )
            self.assertTrue(
                any("alive failure" in note for note in notes)
            )

    def test_signal_install_failure_restores_installed_handlers(self) -> None:
        handled = (
            signal.SIGHUP,
            signal.SIGINT,
            signal.SIGTERM,
            signal.SIGUSR1,
        )
        before = {
            signum: signal.getsignal(signum) for signum in handled
        }
        real_signal = signal.signal
        failed = False

        def fail_once(signum, handler):  # noqa: ANN001, ANN202
            nonlocal failed
            if (
                signum == signal.SIGTERM
                and callable(handler)
                and not failed
            ):
                failed = True
                raise OSError("synthetic install failure")
            return real_signal(signum, handler)

        with mock.patch.object(
            MODULE.signal,
            "signal",
            side_effect=fail_once,
        ):
            with self.assertRaisesRegex(
                OSError,
                "synthetic install failure",
            ):
                with MODULE._signal_cancellation_guard():
                    self.fail("guard body must not run")
        self.assertEqual(
            {
                signum: signal.getsignal(signum)
                for signum in handled
            },
            before,
        )

    def test_signal_restore_attempts_all_and_preserves_primary(self) -> None:
        handled = (
            signal.SIGHUP,
            signal.SIGINT,
            signal.SIGTERM,
            signal.SIGUSR1,
        )
        before = {
            signum: signal.getsignal(signum) for signum in handled
        }
        real_signal = signal.signal
        call_count = {signum: 0 for signum in handled}
        restored: list[signal.Signals] = []

        def fail_one_restore(signum, handler):  # noqa: ANN001, ANN202
            call_count[signum] += 1
            result = real_signal(signum, handler)
            if call_count[signum] == 2:
                restored.append(signum)
                if signum == signal.SIGTERM:
                    raise OSError("synthetic restoration failure")
            return result

        primary = FatalControl("signal body failure")
        with mock.patch.object(
            MODULE.signal,
            "signal",
            side_effect=fail_one_restore,
        ):
            with self.assertRaises(FatalControl) as raised:
                with MODULE._signal_cancellation_guard():
                    raise primary
        self.assertIs(raised.exception, primary)
        self.assertEqual(set(restored), set(handled))
        self.assertEqual(
            {
                signum: signal.getsignal(signum)
                for signum in handled
            },
            before,
        )
        self.assertTrue(
            any(
                "synthetic restoration failure" in note
                for note in getattr(primary, "__notes__", [])
            )
        )

    def test_reconciliation_signal_preserves_active_baseexception(self) -> None:
        original = FatalControl("original")
        with self.assertRaises(FatalControl) as raised:
            with MODULE._signal_cancellation_guard():
                try:
                    raise original
                finally:
                    with MODULE._signal_reconciliation_scope():
                        handler = signal.getsignal(signal.SIGINT)
                        self.assertTrue(callable(handler))
                        handler(signal.SIGINT, None)
        self.assertIs(raised.exception, original)

    def test_create_only_evidence_detects_existing_tamper(self) -> None:
        private_directory(self.context.output_root)
        directory = self.context.output_root / "test"
        path, _digest = MODULE._persist_document(
            directory,
            prefix="record",
            document={"value": 1},
        )
        path.write_bytes(b'{"value":2}')
        path.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.NginxCutoverPhaseBridgeError,
            "existing bridge evidence differs",
        ):
            MODULE._persist_document(
                directory,
                prefix="record",
                document={"value": 1},
            )

    def test_caller_asserted_capability_is_rejected(self) -> None:
        request = {
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": RELEASE_TREE_SHA,
            "controller_manifest_sha256": MANIFEST_SHA256,
            "plan_sha256": PLAN_SHA256,
            "approval_sha256": APPROVAL_SHA256,
            "approval_policy_sha256": self.context.manifest["artifacts"][
                "human_approval_policy_sha256"
            ],
            "nginx_aggregate_sha256": (
                self.context.nginx_inputs.aggregate_sha256
            ),
            "known_hosts_sha256": "9" * 64,
            "ssh_identity_sha256": (
                self.context.nginx_inputs.ssh_identity_sha256
            ),
            "nginx_role_manifest_sha256": {
                role: self.context.nginx_inputs.roles[
                    role
                ].manifest_sha256
                for role in MODULE.ROLE_ORDER
            },
            "nginx_role_archive_sha256": {
                role: self.context.nginx_inputs.roles[role].manifest[
                    "archive"
                ]["sha256"]
                for role in MODULE.ROLE_ORDER
            },
            "constraints": {
                field: False for field in MODULE.CONSTRAINT_FIELDS
            },
        }
        request["constraints"]["rollback_allowed"] = True
        with self.assertRaisesRegex(
            MODULE.NginxCutoverPhaseBridgeError,
            "forbidden bridge capability",
        ):
            MODULE._validate_request_bindings(
                request,
                manifest=self.context.manifest,
                manifest_sha256=MANIFEST_SHA256,
                plan=self.context.plan,
                nginx_inputs=self.context.nginx_inputs,
                approval_sha256=APPROVAL_SHA256,
                approval_policy_sha256=self.context.manifest[
                    "artifacts"
                ]["human_approval_policy_sha256"],
                known_hosts_sha256="9" * 64,
            )

    def test_root_only_canonical_request_loads_exact_bound_inputs(self) -> None:
        approval = b"approval"
        policy = b"policy"
        known_hosts = b"known-hosts"
        for name, payload in (
            ("approval.json", approval),
            ("policy.json", policy),
            ("known-hosts", known_hosts),
        ):
            path = self.root / name
            path.write_bytes(payload)
            path.chmod(0o600)
        manifest = copy.deepcopy(self.context.manifest)
        manifest["artifacts"]["cutover_approval_sha256"] = (
            hashlib.sha256(approval).hexdigest()
        )
        manifest["artifacts"]["human_approval_policy_sha256"] = (
            hashlib.sha256(policy).hexdigest()
        )
        nginx_paths = {
            "aggregate": os.fspath(self.context.nginx_paths.aggregate),
            "bot_fi_manifest": os.fspath(
                self.context.nginx_paths.bot_fi_manifest
            ),
            "bot_fi_archive": os.fspath(
                self.context.nginx_paths.bot_fi_archive
            ),
            "webapp_fi_manifest": os.fspath(
                self.context.nginx_paths.webapp_fi_manifest
            ),
            "webapp_fi_archive": os.fspath(
                self.context.nginx_paths.webapp_fi_archive
            ),
            "known_hosts": os.fspath(self.root / "known-hosts"),
            "ssh_identity": os.fspath(
                self.context.nginx_paths.ssh_identity
            ),
        }
        request = {
            "schema": MODULE.REQUEST_SCHEMA,
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": RELEASE_TREE_SHA,
            "controller_manifest_path": os.fspath(
                self.context.manifest_path
            ),
            "controller_manifest_sha256": MANIFEST_SHA256,
            "plan_sha256": PLAN_SHA256,
            "approval_path": os.fspath(self.root / "approval.json"),
            "approval_sha256": manifest["artifacts"][
                "cutover_approval_sha256"
            ],
            "approval_policy_path": os.fspath(
                self.root / "policy.json"
            ),
            "approval_policy_sha256": manifest["artifacts"][
                "human_approval_policy_sha256"
            ],
            "nginx_paths": nginx_paths,
            "nginx_aggregate_sha256": (
                self.context.nginx_inputs.aggregate_sha256
            ),
            "nginx_role_manifest_sha256": {
                role: self.context.nginx_inputs.roles[
                    role
                ].manifest_sha256
                for role in MODULE.ROLE_ORDER
            },
            "nginx_role_archive_sha256": {
                role: self.context.nginx_inputs.roles[role].manifest[
                    "archive"
                ]["sha256"]
                for role in MODULE.ROLE_ORDER
            },
            "known_hosts_sha256": hashlib.sha256(
                known_hosts
            ).hexdigest(),
            "ssh_identity_sha256": (
                self.context.nginx_inputs.ssh_identity_sha256
            ),
            "prior_phase_evidence": {
                phase: {
                    "path": os.fspath(self.prior_paths[phase]),
                    "sha256": self.prior_sha256[phase],
                }
                for phase in MODULE.INITIAL_PRIOR_PHASES
            },
            "constraints": {
                field: False for field in MODULE.CONSTRAINT_FIELDS
            },
        }
        request_path = self.root / "bridge-request.json"
        private_document(request_path, request)
        with (
            mock.patch.object(
                MODULE.CONTROLLER,
                "read_root_only_manifest",
                return_value=(manifest, MANIFEST_SHA256),
            ),
            mock.patch.object(
                MODULE.CONTROLLER,
                "render_plan",
                return_value={"plan_sha256": PLAN_SHA256},
            ),
            mock.patch.object(
                MODULE,
                "_load_nginx_inputs",
                return_value=self.context.nginx_inputs,
            ),
        ):
            loaded = MODULE.load_bridge_request(request_path)
        self.assertEqual(loaded.manifest, manifest)
        self.assertEqual(loaded.nginx_inputs, self.context.nginx_inputs)
        self.assertEqual(loaded.prior_paths, self.prior_paths)
        self.assertEqual(
            loaded.output_root,
            self.evidence_root / "nginx-cutover-phases",
        )

    def test_noncanonical_request_is_rejected_before_any_action(self) -> None:
        path = self.root / "noncanonical.json"
        path.write_bytes(b'{"schema":"x"}\n')
        path.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.NginxCutoverPhaseBridgeError,
            "canonical encoding",
        ):
            MODULE._read_request(path)


if __name__ == "__main__":
    unittest.main()
