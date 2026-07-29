from __future__ import annotations

import copy
from dataclasses import fields, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from core.canonical_json import canonical_json_bytes
from scripts import orchestrate_production_shadow_restore_phase as MODULE


SHA40 = "1" * 40
TREE40 = "2" * 40
LEGACY40 = "3" * 40
SHA256 = "4" * 64
OTHER256 = "5" * 64
CAMPAIGN = "11111111-1111-4111-8111-111111111111"
OPERATION = "22222222-2222-4222-8222-222222222222"
NOW = datetime(2026, 7, 28, 4, 0, tzinfo=timezone.utc)
SSH_IDENTITY = Path("/root/.ssh/id_ed25519")
KNOWN_HOSTS = Path("/root/.ssh/known_hosts")
LIVE_LEASE_CLAIM = {
    "operation_id": OPERATION,
    "release_sha": SHA40,
    "nonce": "6" * 64,
}
TRUSTED_TEST_TEMP_ROOT = Path("/root/trading-bot/trading_bot/tmp")


def private_directory(path: Path) -> Path:
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.chmod(0o700)
    return path


def context(root: Path) -> MODULE.CoordinatorContext:
    prior_phases = MODULE.CONTROLLER.PHASES[
        : MODULE.CONTROLLER.PHASES.index(MODULE.PHASE)
    ]
    postgres = "a" * 64
    files = "b" * 64
    prior_records = {
        phase: {
            "document": {
                "phase": phase,
                "claims": (
                    {
                        "postgres_snapshot_set_sha256": {
                            "value": postgres
                        },
                        "reviewed_file_snapshot_set_sha256": {
                            "value": files
                        },
                    }
                    if phase == "final_snapshot_hashes"
                    else {}
                ),
            },
            "file_sha256": ("c" * 63) + str(index % 10),
            "path": os.fspath(root / f"{phase}.json"),
        }
        for index, phase in enumerate(prior_phases)
    }
    sources = {}
    for source_role, marker in (("bot_fi", "d"), ("webapp_fi", "e")):
        sources[source_role] = {
            "source_database": {
                "alembic_revision": f"{marker}-revision",
                "database_fingerprint_sha256": marker * 64,
                "row_count": 10,
                "table_count": 2,
            },
            "artifacts": {
                "uploads-archive": {
                    "restored_tree_sha256": marker * 64,
                },
                "audit-archive": {
                    "restored_tree_sha256": marker.upper() * 64,
                },
            },
        }
    restore_set = {
        "restore_generation_sha256": "6" * 64,
        "postgres_snapshot_set_sha256": postgres,
        "reviewed_file_snapshot_set_sha256": files,
        "constraints": {"legacy_redis_restore_included": False},
        "target_map": {
            "bot_fi": {"source_role": "bot_fi"},
            "webapp_fi": {"source_role": "webapp_fi"},
            "webapp_ir": {"source_role": "webapp_fi"},
        },
        "sources": sources,
    }
    manifest = {
        "campaign_id": CAMPAIGN,
        "operation_id": OPERATION,
        "release_sha": SHA40,
        "release_tree_sha": TREE40,
        "legacy_release_sha": LEGACY40,
        "topology": {
            role: {"host": MODULE.RESTORE.ROLE_HOSTS[role]}
            for role in MODULE.ROLES
        },
        "deployment": {
            "controller_journal_path": os.fspath(root / "journal.json"),
            "controller_evidence_root": os.fspath(root / "evidence"),
        },
    }
    prefix = list(prior_phases)
    journal = {
        "status": "phase_started",
        "started_phase": MODULE.PHASE,
        "completed_phases": prefix,
        "phase_evidence_sha256": {phase: SHA256 for phase in prefix},
        "phase_verification_sha256": {phase: OTHER256 for phase in prefix},
    }
    return MODULE.CoordinatorContext(
        manifest_path=root / "manifest.json",
        manifest=manifest,
        manifest_sha256=SHA256,
        plan={
            "plan_sha256": OTHER256,
            "phases": [],
        },
        plan_sha256=OTHER256,
        restore_set_path=root / "restore-set.json",
        restore_set=restore_set,
        restore_set_sha256="7" * 64,
        requests={
            role: {
                "role": role,
                "release_root": os.fspath(
                    MODULE.INVENTORY.PROJECT_ROOT_PREFIX
                    / OPERATION
                    / "releases"
                    / SHA40
                ),
                "worker_sha256": "9" * 64,
            }
            for role in MODULE.ROLES
        },
        restore_output_directory=root / "restore-output",
        coordinator_output_directory=root / "coordinator",
        prior_paths={
            phase: root / f"{phase}.json" for phase in prior_phases
        },
        prior_records=prior_records,
        journal=journal,
    )


def inventory_trust() -> MODULE.InventorySshTrust:
    return MODULE.InventorySshTrust(
        known_hosts=KNOWN_HOSTS,
        ssh_identity=SSH_IDENTITY,
        ssh_identity_sha256=hashlib.sha256(
            SSH_IDENTITY.read_bytes()
        ).hexdigest(),
    )


def bounded_result(
    control: MODULE.InventoryControl | MODULE.ValidationControl,
    *,
    stdout: bytes,
    stderr: bytes = b"",
    returncode: int = 0,
    timed_out: bool = False,
    stdout_limit_exceeded: bool = False,
    stderr_limit_exceeded: bool = False,
    process_group_terminated: bool = False,
) -> MODULE.BoundedProcessResult:
    return MODULE.BoundedProcessResult(
        control_sha256=MODULE._process_control_sha256(control),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        stdin_bytes_sent=len(control.stdin),
        deadline_enforced=True,
        stdout_limit_enforced=True,
        stderr_limit_enforced=True,
        timed_out=timed_out,
        stdout_limit_exceeded=stdout_limit_exceeded,
        stderr_limit_exceeded=stderr_limit_exceeded,
        process_group_cleanup_performed=True,
        process_group_terminated=process_group_terminated,
    )


def isolated_python_control(
    source: str,
    *,
    stdin: bytes = b"",
    max_stdout_bytes: int = 4096,
    max_stderr_bytes: int = 4096,
    timeout_seconds: float = 2.0,
) -> MODULE.ValidationControl:
    return MODULE.ValidationControl(
        role="bot_fi",
        argv=(
            "/usr/bin/env",
            "-i",
            "PATH=/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE=1",
            "PYTHONWARNINGS=error",
            "/usr/bin/python3",
            "-I",
            "-B",
            "-c",
            source,
        ),
        stdin=stdin,
        max_stdout_bytes=max_stdout_bytes,
        max_stderr_bytes=max_stderr_bytes,
        timeout_seconds=timeout_seconds,
        start_new_session=True,
        terminate_process_group_on_exit=True,
        kill_process_group_after_seconds=0.1,
    )


def nginx_inputs(
    ctx: MODULE.CoordinatorContext,
    *,
    ssh_identity: Path | None = None,
    known_hosts: Path | None = None,
    operation_id: str | None = None,
    coordinator_root: Path | None = None,
) -> SimpleNamespace:
    selected_identity = (
        SSH_IDENTITY if ssh_identity is None else ssh_identity
    )
    selected_known_hosts = (
        KNOWN_HOSTS if known_hosts is None else known_hosts
    )
    identity_sha256 = (
        hashlib.sha256(selected_identity.read_bytes()).hexdigest()
        if selected_identity.is_file()
        else SHA256
    )
    return SimpleNamespace(
        ssh_identity=selected_identity,
        known_hosts=selected_known_hosts,
        ssh_identity_sha256=identity_sha256,
        operation_id=operation_id or ctx.manifest["operation_id"],
        release_sha=ctx.manifest["release_sha"],
        release_tree_sha=ctx.manifest["release_tree_sha"],
        coordinator_root=(
            coordinator_root
            if coordinator_root is not None
            else ctx.restore_output_directory.parent
        ),
    )


def completion(ctx: MODULE.CoordinatorContext) -> dict:
    roles = {}
    for index, role in enumerate(MODULE.ROLES, 1):
        source_role = ctx.restore_set["target_map"][role]["source_role"]
        source = ctx.restore_set["sources"][source_role]
        restore = {
            "database": dict(source["source_database"]),
            "file_trees": {
                "uploads": source["artifacts"][
                    "uploads-archive"
                ]["restored_tree_sha256"],
                "audit": source["artifacts"][
                    "audit-archive"
                ]["restored_tree_sha256"],
            },
            "redis_restore_bytes": 0,
            "redis_pristine": True,
        }
        roles[role] = {
            "host_result": {
                "role_manifest": {
                    "path": f"/secure/{role}.json",
                    "canonical_document_sha256": str(index) * 64,
                },
                "action_evidence": {
                    "verify-final": {
                        "document": {
                            "semantic": {
                                "database_container_id": str(index) * 64,
                                "database_host_config_sha256": (
                                    str(index + 3) * 64
                                ),
                            }
                        }
                    }
                },
                "restore_result": {"document": restore},
                "worker_return": {"result": restore},
            }
        }
    return {
        "schema": MODULE.RESTORE.COMPLETION_SCHEMA,
        "redis_restored": False,
        "roles": roles,
    }


class FakeLease:
    def __init__(self, order: list[str] | None = None) -> None:
        self.order = order
        self.claim_path = Path("/root/secure/live-lease/claim.json")
        self.claim_sha256 = SHA256
        self.claim = copy.deepcopy(LIVE_LEASE_CLAIM)
        self.consume = mock.Mock()

    def verify(self) -> None:
        if self.order is not None:
            self.order.append("lease-verify")


class RestorePhaseCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        global KNOWN_HOSTS, SSH_IDENTITY
        # The real immutable-release check rejects /tmp because its ancestor
        # is world-writable. Keep this integration fixture below the
        # root-owned primary checkout instead of weakening that production
        # guard for a test-only path.
        self.temporary = tempfile.TemporaryDirectory(dir=TRUSTED_TEST_TEMP_ROOT)
        self.root = private_directory(Path(self.temporary.name))
        self.original_ssh_identity = SSH_IDENTITY
        self.original_known_hosts = KNOWN_HOSTS
        ssh_directory = private_directory(self.root / "ssh")
        SSH_IDENTITY = ssh_directory / "id_ed25519"
        SSH_IDENTITY.write_bytes(b"test-private-key\n")
        SSH_IDENTITY.chmod(0o600)
        KNOWN_HOSTS = ssh_directory / "known_hosts"
        KNOWN_HOSTS.write_bytes(b"host ssh-ed25519 test-public-key\n")
        KNOWN_HOSTS.chmod(0o600)
        self.context = context(self.root)
        self.nginx_claim_loader = mock.patch.object(
            MODULE.NGINX,
            "_load_claim_from_controller",
            return_value=(copy.deepcopy(LIVE_LEASE_CLAIM), {}),
        )
        self.nginx_claim_loader.start()

    def tearDown(self) -> None:
        global KNOWN_HOSTS, SSH_IDENTITY
        self.nginx_claim_loader.stop()
        SSH_IDENTITY = self.original_ssh_identity
        KNOWN_HOSTS = self.original_known_hosts
        self.temporary.cleanup()

    def test_cli_is_plan_only(self) -> None:
        parser = MODULE._parser()
        options = {action.dest for action in parser._actions}
        self.assertNotIn("apply", options)
        self.assertNotIn("confirm", options)
        self.assertEqual(
            [field.name for field in fields(MODULE.EvidencePublication)],
            ["derivation_path", "derivation_sha256"],
        )
        self.assertEqual(len(MODULE.DERIVATION_FIELDS), 25)

    def test_plan_confirmation_binds_exact_restore_requests(self) -> None:
        phase = {
            "phase": MODULE.PHASE,
            "execution_supported": False,
            "journal_begin_required_before_commands": True,
            "journal_completion_requires_release_verifier_receipt": True,
            "commands": [
                {
                    "role": role,
                    "argv": [
                        "/usr/bin/python3",
                        "--operation",
                        MODULE.OPERATION,
                    ],
                    "render_only": True,
                    "executor_available": False,
                }
                for role in MODULE.ROLES
            ],
        }
        changed_requests = copy.deepcopy(self.context.requests)
        changed_requests["webapp_ir"]["worker_sha256"] = "8" * 64
        changed = replace(self.context, requests=changed_requests)
        with (
            mock.patch.object(
                MODULE,
                "_phase_plan_row",
                return_value=phase,
            ),
            mock.patch.object(
                MODULE,
                "_derive_inventory_agent_release_sha256",
                return_value=SHA256,
            ),
        ):
            original_plan = MODULE._plan_document(self.context)
            changed_plan = MODULE._plan_document(changed)
            self.assertNotEqual(
                original_plan["restore_request_sha256"],
                changed_plan["restore_request_sha256"],
            )
            self.assertNotEqual(
                original_plan["required_confirmation"],
                changed_plan["required_confirmation"],
            )
            with mock.patch.object(
                MODULE,
                "_derive_inventory_agent_release_sha256",
                return_value=OTHER256,
            ):
                changed_agent_plan = MODULE._plan_document(self.context)
            self.assertEqual(
                original_plan["inventory_agent_sha256"],
                SHA256,
            )
            self.assertEqual(
                changed_agent_plan["inventory_agent_sha256"],
                OTHER256,
            )
            self.assertNotEqual(
                original_plan["required_confirmation"],
                changed_agent_plan["required_confirmation"],
            )
            callbacks = [mock.Mock() for _index in range(6)]
            with (
                mock.patch.object(
                    MODULE,
                    "_load_context",
                    return_value=changed,
                ),
                self.assertRaisesRegex(
                    MODULE.RestorePhaseCoordinatorError,
                    "exact digest-bound confirmation",
                ),
            ):
                MODULE.apply_restore_phase(
                    manifest_path=self.root / "manifest.json",
                    restore_set_path=self.root / "restore-set.json",
                    requests={},
                    prior_phase_evidence={},
                    approval_path=self.root / "approval.json",
                    approval_policy_path=self.root / "policy.json",
                    nginx_inputs=nginx_inputs(changed),
                    lease=None,
                    prepare_restore_request=callbacks[0],
                    invoke_restore_host=callbacks[1],
                    inventory_invoke=callbacks[2],
                    inventory_agent_sha256=SHA256,
                    ssh_identity=SSH_IDENTITY,
                    known_hosts=KNOWN_HOSTS,
                    validation_runner=callbacks[3],
                    controller_callback=callbacks[4],
                    evidence_publisher=callbacks[5],
                    confirm=original_plan["required_confirmation"],
                )
        for callback in callbacks:
            callback.assert_not_called()

    def test_apply_inventory_agent_must_match_confirmed_plan(self) -> None:
        callbacks = [mock.Mock() for _index in range(6)]
        with (
            mock.patch.object(MODULE, "_load_context", return_value=self.context),
            mock.patch.object(
                MODULE,
                "_plan_document",
                return_value={
                    "required_confirmation": "exact",
                    "inventory_agent_sha256": SHA256,
                },
            ),
            self.assertRaisesRegex(
                MODULE.RestorePhaseCoordinatorError,
                "differs from the confirmed plan",
            ),
        ):
            MODULE.apply_restore_phase(
                manifest_path=self.root / "manifest.json",
                restore_set_path=self.root / "restore-set.json",
                requests={},
                prior_phase_evidence={},
                approval_path=self.root / "approval.json",
                approval_policy_path=self.root / "policy.json",
                nginx_inputs=nginx_inputs(self.context),
                lease=None,
                prepare_restore_request=callbacks[0],
                invoke_restore_host=callbacks[1],
                inventory_invoke=callbacks[2],
                inventory_agent_sha256=OTHER256,
                ssh_identity=SSH_IDENTITY,
                known_hosts=KNOWN_HOSTS,
                validation_runner=callbacks[3],
                controller_callback=callbacks[4],
                evidence_publisher=callbacks[5],
                confirm="exact",
            )
        for callback in callbacks:
            callback.assert_not_called()

    def test_inventory_agent_digest_is_derived_from_immutable_release(
        self,
    ) -> None:
        project_root = self.root / "production-shadow"
        release_root = (
            project_root
            / OPERATION
            / "releases"
            / SHA40
        )
        agent_path = release_root / MODULE.INVENTORY.AGENT_RELATIVE
        agent_path.parent.mkdir(parents=True)
        agent_path.write_bytes(b"inventory-agent-v1\n")
        agent_path.chmod(0o644)
        requests = {
            role: {
                **request,
                "release_root": os.fspath(release_root),
            }
            for role, request in self.context.requests.items()
        }
        ctx = replace(self.context, requests=requests)
        with mock.patch.object(
            MODULE.INVENTORY,
            "PROJECT_ROOT_PREFIX",
            project_root,
        ):
            observed = MODULE._derive_inventory_agent_release_sha256(ctx)
            self.assertEqual(
                observed,
                hashlib.sha256(b"inventory-agent-v1\n").hexdigest(),
            )
            agent_path.write_bytes(b"inventory-agent-v2\n")
            agent_path.chmod(0o644)
            changed = MODULE._derive_inventory_agent_release_sha256(ctx)
        self.assertNotEqual(observed, changed)

    def test_published_evidence_path_is_canonical(self) -> None:
        digest = "a" * 64
        expected = (
            self.context.coordinator_output_directory
            / "phase-evidence"
            / f"{MODULE.PHASE}.{digest}.json"
        )
        self.assertEqual(
            MODULE._canonical_published_evidence_path(
                self.context,
                path=expected,
                evidence_sha256=digest,
            ),
            expected,
        )
        with self.assertRaisesRegex(
            MODULE.RestorePhaseCoordinatorError,
            "path is not canonical",
        ):
            MODULE._canonical_published_evidence_path(
                self.context,
                path=self.root / f"{MODULE.PHASE}.{digest}.json",
                evidence_sha256=digest,
            )

    def test_database_container_ids_come_only_from_validated_completion(self) -> None:
        observed = MODULE._database_container_ids(completion(self.context))
        self.assertEqual(
            observed,
            {
                "bot_fi": "1" * 64,
                "webapp_fi": "2" * 64,
                "webapp_ir": "3" * 64,
            },
        )
        tampered = completion(self.context)
        tampered["roles"]["webapp_ir"]["host_result"][
            "action_evidence"
        ]["verify-final"]["document"]["semantic"][
            "database_container_id"
        ] = "short"
        with self.assertRaisesRegex(
            MODULE.RestorePhaseCoordinatorError,
            "container identity",
        ):
            MODULE._database_container_ids(tampered)

    def test_database_host_config_digests_are_completion_bound(self) -> None:
        document = completion(self.context)
        observed = MODULE._database_host_config_sha256s(document)
        self.assertEqual(
            observed,
            {
                role: str(index + 3) * 64
                for index, role in enumerate(MODULE.ROLES, 1)
            },
        )
        for value in (None, "0" * 64, "short"):
            with self.subTest(value=value):
                tampered = copy.deepcopy(document)
                semantic = tampered["roles"]["bot_fi"]["host_result"][
                    "action_evidence"
                ]["verify-final"]["document"]["semantic"]
                if value is None:
                    del semantic["database_host_config_sha256"]
                else:
                    semantic["database_host_config_sha256"] = value
                with self.assertRaises(
                    MODULE.RestorePhaseCoordinatorError
                ):
                    MODULE._database_host_config_sha256s(tampered)

    def test_inventory_request_is_exact_release_and_role_derived(self) -> None:
        request = MODULE._inventory_request(
            self.context,
            role="webapp_ir",
            action="capture-before",
            inventory_agent_sha256=SHA256,
            expected_operation_container_id=None,
            expected_operation_host_config_sha256=None,
            role_manifest_path=None,
            role_manifest_sha256=None,
        )
        self.assertEqual(
            request,
            MODULE.INVENTORY.validate_request(request),
        )
        self.assertEqual(request["agent_sha256"], SHA256)
        self.assertEqual(request["worker_sha256"], "9" * 64)
        self.assertEqual(request["expected_host"], "95.38.164.29")
        self.assertIsNone(request["expected_operation_container_id"])
        with mock.patch.dict(
            os.environ,
            {
                "PYTHONPATH": "/tmp/hostile",
                "PYTHONHOME": "/tmp/hostile-home",
            },
        ):
            argv = MODULE.inventory_session_arguments(
                request,
                ssh_trust=inventory_trust(),
            )
        expected_host_argv = (
            "/usr/bin/env",
            "-i",
            "PATH=/usr/bin:/bin",
            "HOME=/root",
            "LANG=C.UTF-8",
            "LC_ALL=C.UTF-8",
            "PYTHONDONTWRITEBYTECODE=1",
            "/usr/bin/python3",
            "-I",
            "-B",
            request["agent_path"],
            "--host-stdio",
        )
        self.assertEqual(argv[0], "/usr/bin/ssh")
        self.assertEqual(
            argv[argv.index("-F") : argv.index("-F") + 2],
            ("-F", "/dev/null"),
        )
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("ClearAllForwardings=yes", argv)
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertEqual(argv[-2], "root@95.38.164.29")
        self.assertEqual(argv[-1], " ".join(expected_host_argv))
        self.assertNotIn("PYTHONPATH", argv[-1])
        self.assertNotIn("PYTHONHOME", argv[-1])
        self.assertNotIn("scp", " ".join(argv))
        local_request = MODULE._inventory_request(
            self.context,
            role="bot_fi",
            action="capture-before",
            inventory_agent_sha256=SHA256,
            expected_operation_container_id=None,
            expected_operation_host_config_sha256=None,
            role_manifest_path=None,
            role_manifest_sha256=None,
        )
        self.assertEqual(
            MODULE.inventory_session_arguments(
                local_request,
                ssh_trust=inventory_trust(),
            ),
            (
                *expected_host_argv[:-2],
                local_request["agent_path"],
                "--host-stdio",
            ),
        )

    def test_inventory_ssh_paths_must_match_nginx_trust_anchor(self) -> None:
        with self.assertRaisesRegex(
            MODULE.RestorePhaseCoordinatorError,
            "differ from validated Nginx trust",
        ):
            MODULE._bind_inventory_ssh_trust(
                self.context,
                nginx_inputs=nginx_inputs(
                    self.context,
                    ssh_identity=Path("/root/.ssh/other"),
                ),
                ssh_identity=SSH_IDENTITY,
                known_hosts=KNOWN_HOSTS,
            )
        with self.assertRaisesRegex(
            MODULE.RestorePhaseCoordinatorError,
            "differ from validated Nginx trust",
        ):
            MODULE._bind_inventory_ssh_trust(
                self.context,
                nginx_inputs=nginx_inputs(self.context),
                ssh_identity=SSH_IDENTITY,
                known_hosts=Path("/root/.ssh/other-known-hosts"),
            )

    def test_changed_ssh_identity_fails_before_phase_mutation(self) -> None:
        inputs = nginx_inputs(self.context)
        SSH_IDENTITY.write_bytes(b"changed-private-key\n")
        SSH_IDENTITY.chmod(0o600)
        callbacks = [mock.Mock() for _index in range(6)]
        begin = mock.Mock()
        lease = mock.Mock()
        with (
            mock.patch.object(MODULE, "_load_context", return_value=self.context),
            mock.patch.object(
                MODULE,
                "_plan_document",
                return_value={
                    "required_confirmation": "exact",
                    "inventory_agent_sha256": SHA256,
                },
            ),
            mock.patch.object(MODULE, "_begin_phase", begin),
            self.assertRaisesRegex(
                MODULE.RestorePhaseCoordinatorError,
                "trust file identity differs",
            ),
        ):
            MODULE.apply_restore_phase(
                manifest_path=self.root / "manifest.json",
                restore_set_path=self.root / "restore-set.json",
                requests={},
                prior_phase_evidence={},
                approval_path=self.root / "approval.json",
                approval_policy_path=self.root / "policy.json",
                nginx_inputs=inputs,
                lease=lease,
                prepare_restore_request=callbacks[0],
                invoke_restore_host=callbacks[1],
                inventory_invoke=callbacks[2],
                inventory_agent_sha256=SHA256,
                ssh_identity=SSH_IDENTITY,
                known_hosts=KNOWN_HOSTS,
                validation_runner=callbacks[3],
                controller_callback=callbacks[4],
                evidence_publisher=callbacks[5],
                confirm="exact",
            )
        begin.assert_not_called()
        lease.consume.assert_not_called()
        for callback in callbacks:
            callback.assert_not_called()

    def test_empty_known_hosts_is_rejected_at_trust_binding(self) -> None:
        KNOWN_HOSTS.write_bytes(b"")
        KNOWN_HOSTS.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.RestorePhaseCoordinatorError,
            "trust file identity differs",
        ):
            MODULE._bind_inventory_ssh_trust(
                self.context,
                nginx_inputs=nginx_inputs(self.context),
                ssh_identity=SSH_IDENTITY,
                known_hosts=KNOWN_HOSTS,
            )

    def test_wa_exact_version_must_match_sealed_restore_set(self) -> None:
        sealed = {
            "provider": "arvan-s3",
            "bucket": "private-versioned",
            "recipient": "age1" + "q" * 58,
            "private": True,
            "versioned": True,
            "encryption": "age",
            "object_key": "restore/exact.age",
            "version_id": "version-1",
            "ciphertext_sha256": "a" * 64,
            "readback_receipt_sha256": "b" * 64,
            "exact_version_readback_verified": True,
        }
        expected = {
            "campaign_id": self.context.manifest["campaign_id"],
            "operation_id": self.context.manifest["operation_id"],
            "release_sha": self.context.manifest["release_sha"],
            "release_tree_sha": self.context.manifest[
                "release_tree_sha"
            ],
            "controller_manifest_sha256": self.context.manifest_sha256,
            "restore_set_sha256": self.context.restore_set_sha256,
            "restore_generation_sha256": self.context.restore_set[
                "restore_generation_sha256"
            ],
        }
        requests = {}
        for role in MODULE.ROLES:
            requests[role] = {
                **expected,
                "action": "plan",
                "role": role,
                "expected_host": self.context.manifest["topology"][role][
                    "host"
                ],
                "wa_exact_version": (
                    {
                        **{
                            field: sealed[field]
                            for field in (
                                "provider",
                                "private",
                                "versioned",
                                "encryption",
                                "bucket",
                                "recipient",
                                "object_key",
                                "version_id",
                                "ciphertext_sha256",
                                "readback_receipt_sha256",
                                "exact_version_readback_verified",
                            )
                        },
                        "payload_bytes_over_ssh": False,
                        "presigned_url_persisted": False,
                    }
                    if role == "webapp_ir"
                    else None
                ),
            }
        requests["webapp_ir"]["wa_exact_version"]["version_id"] = (
            "alternate-version"
        )
        restore_set = {
            **self.context.restore_set,
            "webapp_ir_transport": sealed,
        }
        prepare_callback = mock.Mock()
        with (
            mock.patch.object(
                MODULE.RESTORE,
                "validate_host_request",
                side_effect=lambda value: dict(value),
            ),
            mock.patch.object(MODULE.RESTORE, "controller_plan"),
            self.assertRaisesRegex(
                MODULE.RestorePhaseCoordinatorError,
                "exact-VersionId differs",
            ),
        ):
            MODULE._validate_restore_requests(
                requests,
                manifest=self.context.manifest,
                manifest_sha256=self.context.manifest_sha256,
                restore_set=restore_set,
                restore_set_sha256=self.context.restore_set_sha256,
            )
        prepare_callback.assert_not_called()

    def test_claim_values_bind_completion_snapshot_redis_and_inventory(self) -> None:
        inventory_closure = {"non_operation_resource_delta_count": 0}
        inventory_closure_sha256 = hashlib.sha256(
            MODULE._canonical_json(inventory_closure) + b"\n"
        ).hexdigest()
        values = MODULE._derive_claim_values(
            self.context,
            completion=completion(self.context),
            completion_sha256="8" * 64,
            inventory_closure=inventory_closure,
            inventory_closure_sha256=inventory_closure_sha256,
        )
        self.assertEqual(set(values), set(MODULE.CLAIMS))
        self.assertEqual(values["restore_result_set_sha256"], "8" * 64)
        self.assertEqual(
            values["inventory_closure_sha256"],
            inventory_closure_sha256,
        )
        self.assertEqual(values["legacy_redis_restore_byte_count"], 0)
        with self.assertRaisesRegex(
            MODULE.RestorePhaseCoordinatorError,
            "inventory closure",
        ):
            MODULE._derive_claim_values(
                self.context,
                completion=completion(self.context),
                completion_sha256="8" * 64,
                inventory_closure={
                    "non_operation_resource_delta_count": 1
                },
                inventory_closure_sha256=hashlib.sha256(
                    MODULE._canonical_json(
                        {"non_operation_resource_delta_count": 1}
                    )
                    + b"\n"
                ).hexdigest(),
            )
        with self.assertRaisesRegex(
            MODULE.RestorePhaseCoordinatorError,
            "inventory closure",
        ):
            MODULE._derive_claim_values(
                self.context,
                completion=completion(self.context),
                completion_sha256="8" * 64,
                inventory_closure=inventory_closure,
                inventory_closure_sha256="7" * 64,
            )

    def test_inventory_control_is_bounded_and_has_no_application_payload(
        self,
    ) -> None:
        controls: list[MODULE.InventoryControl] = []

        def invoke(control):
            controls.append(control)
            request = json.loads(control.stdin.decode("ascii"))
            payload = json.dumps(
                {"role": request["role"]},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
            return bounded_result(control, stdout=payload + b"\n")

        with mock.patch.object(
            MODULE.INVENTORY,
            "validate_response",
            side_effect=lambda value, request: {
                **value,
                "role": request["role"],
            },
        ):
            observed = MODULE._capture_inventory_set(
                self.context,
                action="capture-before",
                invoke=invoke,
                inventory_agent_sha256=SHA256,
                ssh_trust=inventory_trust(),
            )
        self.assertEqual(set(observed), set(MODULE.ROLES))
        self.assertEqual([item.role for item in controls], list(MODULE.ROLES))
        self.assertTrue(
            all(
                item.application_payload_bytes_over_ssh == 0
                and len(item.stdin) <= MODULE.INVENTORY.MAX_CONTROL_BYTES
                and item.max_stdout_bytes
                == MODULE.INVENTORY.MAX_RESPONSE_BYTES + 1
                and item.max_stderr_bytes
                == MODULE.MAX_CONTROL_STDERR_BYTES
                and item.timeout_seconds
                == MODULE.CONTROL_TIMEOUT_SECONDS
                and item.start_new_session is True
                and item.terminate_process_group_on_exit is True
                and item.kill_process_group_after_seconds
                == MODULE.PROCESS_GROUP_TERM_GRACE_SECONDS
                for item in controls
            )
        )
        self.assertNotEqual(controls[0].argv[0], "/usr/bin/ssh")
        self.assertEqual(controls[2].argv[0], "/usr/bin/ssh")

    def test_claim_derivation_is_create_only_without_caller_claims(
        self,
    ) -> None:
        role_paths = {
            role: self.root / f"{role}-validation.json"
            for role in MODULE.ROLES
        }
        values = {
            "postgres_restore_verified": True,
            "reviewed_file_restore_verified": True,
            "legacy_redis_restore_byte_count": 0,
            "non_operation_resource_delta_count": 0,
            "inventory_closure_sha256": "0" * 63 + "1",
            "restored_postgres_snapshot_set_sha256": "a" * 64,
            "restored_reviewed_file_snapshot_set_sha256": "b" * 64,
            "restore_result_set_sha256": "8" * 64,
        }
        kwargs = {
            "completion_path": self.root / "completion.json",
            "completion_sha256": "8" * 64,
            "post_consumption_path": self.root / "post.json",
            "post_consumption_sha256": "9" * 64,
            "inventory_closure_path": self.root / "inventory.json",
            "inventory_closure_sha256": "0" * 63 + "1",
            "role_validation_paths": role_paths,
            "role_validation_sha256": {},
            "values": values,
            "now": NOW,
        }
        for role, path in role_paths.items():
            payload = json.dumps({"role": role}).encode("ascii") + b"\n"
            path.write_bytes(payload)
            path.chmod(0o600)
            kwargs["role_validation_sha256"][role] = hashlib.sha256(
                payload
            ).hexdigest()
        first = MODULE._write_claim_derivation(self.context, **kwargs)
        second = MODULE._write_claim_derivation(self.context, **kwargs)
        self.assertEqual(first[1:], second[1:])
        document, digest = MODULE._secure_json(
            first[1],
            label="test derivation",
        )
        self.assertEqual(digest, first[2])
        self.assertFalse(document["caller_claim_sources_accepted"])
        self.assertEqual(
            set(document["claims"]),
            set(MODULE.CLAIMS),
        )
        role_paths["webapp_ir"].write_bytes(b'{"tampered":true}\n')
        role_paths["webapp_ir"].chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.RestorePhaseCoordinatorError,
            "role validation digest differs",
        ):
            MODULE._write_claim_derivation(self.context, **kwargs)

    def test_publisher_passes_only_derivation_receipt_to_hardened_api(
        self,
    ) -> None:
        publication = MODULE.EvidencePublication(
            derivation_path=self.root / "derivation.json",
            derivation_sha256=SHA256,
        )
        planned = {
            "status": "planned",
            "derivation_path": os.fspath(publication.derivation_path),
            "derivation_sha256": SHA256,
            "required_confirmation": "exact-derived",
            "journal_mutated": False,
            "production_contacted": False,
        }
        published = {
            **planned,
            "status": "published",
            "self_verification_status": "verified",
        }
        execute = mock.Mock(side_effect=(planned, published))
        with mock.patch.object(
            MODULE.PRODUCER,
            "execute_derived",
            execute,
        ):
            observed = MODULE.publish_derived_evidence(publication)
        self.assertEqual(observed, published)
        self.assertEqual(execute.call_count, 2)
        first = execute.call_args_list[0].kwargs
        second = execute.call_args_list[1].kwargs
        self.assertEqual(
            first,
            {
                "derivation_path": publication.derivation_path,
                "derivation_sha256": SHA256,
            },
        )
        self.assertEqual(
            second,
            {
                **first,
                "apply": True,
                "confirm": "exact-derived",
            },
        )

    def test_real_derivation_is_accepted_by_real_producer(self) -> None:
        from tests import (
            test_orchestrate_production_shadow_frozen_final_restore
            as restore_fixture,
        )
        from tests import (
            test_production_shadow_frozen_final_restore_worker
            as worker_fixture,
        )
        from tests import (
            test_production_shadow_global_docker_inventory_agent
            as inventory_fixture,
        )
        from tests import (
            test_produce_production_shadow_frozen_final_restore_phase_evidence
            as producer_fixture,
        )
        from tests.test_production_shadow_cutover_controller import (
            manifest_payload,
        )

        integration_root = private_directory(self.root / "integration")
        secure_root = lambda campaign_id: (  # noqa: E731
            PurePosixPath(integration_root / "secure") / campaign_id
        )
        with (
            mock.patch.object(
                MODULE.CONTROLLER,
                "_secure_root",
                secure_root,
            ),
            mock.patch(
                "tests.test_production_shadow_cutover_controller._secure_root",
                secure_root,
            ),
        ):
            manifest = manifest_payload()
        manifest["created_at"] = producer_fixture.NOW.isoformat()
        manifest["artifacts"]["phase_evidence_schema_sha256"] = (
            MODULE.VERIFY.PHASE_EVIDENCE_CONTRACT_SHA256
        )
        manifest_sha256 = hashlib.sha256(
            canonical_json_bytes(manifest)
        ).hexdigest()

        restore_set = worker_fixture.restore_set_document()
        restore_set.update(
            {
                "campaign_id": manifest["campaign_id"],
                "operation_id": manifest["operation_id"],
                "release_sha": manifest["release_sha"],
                "release_tree_sha": manifest["release_tree_sha"],
                "legacy_release_sha": manifest["legacy_release_sha"],
                "controller_manifest_sha256": manifest_sha256,
                "approval_sha256": manifest["artifacts"][
                    "cutover_approval_sha256"
                ],
            }
        )
        for source in restore_set["sources"].values():
            source["source_database"] = {
                "alembic_revision": "head",
                "fingerprint_algorithm": (
                    "pg-copy-jsonl-sha256-canonical-session-v1"
                ),
                "database_fingerprint_sha256": "e" * 64,
                "row_count": 10,
                "table_count": 2,
            }
            source["artifacts"]["uploads-archive"][
                "restored_tree_sha256"
            ] = manifest_sha256
            source["artifacts"]["audit-archive"][
                "restored_tree_sha256"
            ] = "f" * 64
            restore_input = {
                field: source[field]
                for field in (
                    "source_snapshot_manifest_sha256",
                    "source_snapshot_binding_sha256",
                    "freeze_evidence_sha256",
                    "live_lease_claim_sha256",
                    "source_identity_sha256",
                    "artifacts",
                    "source_database",
                )
            }
            source["restore_input_sha256"] = hashlib.sha256(
                MODULE.RESTORE.canonical_json(restore_input)
            ).hexdigest()
        restore_set["webapp_ir_transport"][
            "plaintext_restore_input_set_sha256"
        ] = restore_set["sources"]["webapp_fi"]["restore_input_sha256"]
        restore_set["webapp_ir_transport"].update(
            {
                field: value
                for field, value in restore_fixture.wa_version(
                    object_key=(
                        "production-shadow/"
                        f"{manifest['campaign_id']}/"
                        f"{manifest['operation_id']}/"
                        "v-restore-001/bundle.age"
                    )
                ).items()
                if field
                in {
                    "provider",
                    "private",
                    "versioned",
                    "encryption",
                    "bucket",
                    "recipient",
                    "object_key",
                    "version_id",
                    "ciphertext_sha256",
                    "readback_receipt_sha256",
                    "exact_version_readback_verified",
                }
            }
        )
        postgres_set = {
            target: {
                "source_role": row["source_role"],
                "artifact": restore_set["sources"][row["source_role"]][
                    "artifacts"
                ]["database-backup"],
                "source_database": restore_set["sources"][
                    row["source_role"]
                ]["source_database"],
            }
            for target, row in restore_set["target_map"].items()
        }
        file_set = {
            target: {
                "source_role": row["source_role"],
                "uploads-archive": restore_set["sources"][
                    row["source_role"]
                ]["artifacts"]["uploads-archive"],
                "audit-archive": restore_set["sources"][
                    row["source_role"]
                ]["artifacts"]["audit-archive"],
            }
            for target, row in restore_set["target_map"].items()
        }
        restore_set["postgres_snapshot_set_sha256"] = hashlib.sha256(
            MODULE.RESTORE.canonical_json(postgres_set)
        ).hexdigest()
        restore_set[
            "reviewed_file_snapshot_set_sha256"
        ] = hashlib.sha256(
            MODULE.RESTORE.canonical_json(file_set)
        ).hexdigest()
        generation_basis = {
            "schema": (
                "production-shadow-frozen-final-restore-generation-v1"
            ),
            "operation_id": restore_set["operation_id"],
            "release_sha": restore_set["release_sha"],
            "release_tree_sha": restore_set["release_tree_sha"],
            "controller_manifest_sha256": restore_set[
                "controller_manifest_sha256"
            ],
            "approval_sha256": restore_set["approval_sha256"],
            "target_map": restore_set["target_map"],
            "sources": restore_set["sources"],
            "nginx_freeze": restore_set["nginx_freeze"],
            "snapshot_authorization_claim": restore_set[
                "snapshot_authorization_claim"
            ],
            "webapp_ir_transport": restore_set[
                "webapp_ir_transport"
            ],
        }
        restore_set["restore_generation_sha256"] = hashlib.sha256(
            MODULE.RESTORE.canonical_json(generation_basis)
        ).hexdigest()
        restore_set_payload = MODULE.RESTORE.canonical_json(restore_set)
        restore_set_sha256 = hashlib.sha256(
            restore_set_payload
        ).hexdigest()

        patches = (
            mock.patch.object(MODULE.CONTROLLER, "_secure_root", secure_root),
            mock.patch(
                "tests.test_production_shadow_cutover_controller._secure_root",
                secure_root,
            ),
            mock.patch.object(
                producer_fixture,
                "POSTGRES_SNAPSHOT_SET_SHA256",
                restore_set["postgres_snapshot_set_sha256"],
            ),
            mock.patch.object(
                producer_fixture,
                "FILE_SNAPSHOT_SET_SHA256",
                restore_set["reviewed_file_snapshot_set_sha256"],
            ),
            mock.patch.object(
                restore_fixture,
                "CAMPAIGN_ID",
                manifest["campaign_id"],
            ),
            mock.patch.object(
                restore_fixture,
                "OPERATION_ID",
                manifest["operation_id"],
            ),
            mock.patch.object(
                restore_fixture,
                "RELEASE_SHA",
                manifest["release_sha"],
            ),
            mock.patch.object(
                restore_fixture,
                "RELEASE_TREE_SHA",
                manifest["release_tree_sha"],
            ),
            mock.patch.object(
                restore_fixture,
                "SHA_A",
                manifest_sha256,
            ),
            mock.patch.object(
                restore_fixture,
                "SHA_B",
                restore_set_sha256,
            ),
            mock.patch.object(
                restore_fixture,
                "SHA_C",
                restore_set["restore_generation_sha256"],
            ),
            mock.patch.object(
                inventory_fixture,
                "OPERATION_ID",
                manifest["operation_id"],
            ),
            mock.patch.object(
                MODULE.NGINX,
                "CONTROLLER_SECRET_PREFIX",
                PurePosixPath(integration_root / "nginx-secure"),
            ),
            mock.patch.object(
                MODULE.WORKER,
                "PROJECT_ROOT_PREFIX",
                integration_root / "project",
            ),
            mock.patch.object(
                MODULE.INVENTORY,
                "PROJECT_ROOT_PREFIX",
                integration_root / "project",
            ),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        fixture = producer_fixture.PhaseEvidenceFixture(
            integration_root / "producer"
        )
        restore_set_path = integration_root / "restore-set.json"
        restore_set_path.write_bytes(restore_set_payload)
        restore_set_path.chmod(0o600)
        observed_restore_set, observed_restore_set_sha256 = (
            MODULE.RESTORE.WORKER.load_restore_set(
                restore_set_path,
                require_publication_namespace=False,
            )
        )
        self.assertEqual(observed_restore_set, restore_set)
        self.assertEqual(
            observed_restore_set_sha256,
            restore_set_sha256,
        )

        requests = {
            role: restore_fixture.request_for(role)
            for role in MODULE.ROLES
        }
        inventory_agent_payload = b"exact immutable inventory agent\n"
        inventory_agent_path = Path(
            requests["bot_fi"]["release_root"]
        ) / MODULE.INVENTORY.AGENT_RELATIVE
        inventory_agent_path.parent.mkdir(
            parents=True,
            mode=0o700,
            exist_ok=True,
        )
        inventory_agent_path.parent.chmod(0o700)
        inventory_agent_path.write_bytes(inventory_agent_payload)
        inventory_agent_path.chmod(0o644)
        inventory_agent_sha256 = hashlib.sha256(
            inventory_agent_payload
        ).hexdigest()
        results = {}
        container_ids = {
            role: str(index) * 64
            for index, role in enumerate(MODULE.ROLES, 1)
        }
        for role in MODULE.ROLES:
            result = restore_fixture.synthetic_host_result(requests[role])
            final = result["action_evidence"]["verify-final"]
            final["document"]["semantic"][
                "database_container_id"
            ] = container_ids[role]
            final["document"]["semantic"][
                "database_host_config_sha256"
            ] = str(MODULE.ROLES.index(role) + 4) * 64
            restore_fixture.refresh_readback(final)
            final_sha256 = final["canonical_document_sha256"]
            restored = result["restore_result"]
            restored["document"]["final_evidence_sha256"] = final_sha256
            restore_fixture.refresh_readback(restored)
            result["worker_return"]["action_evidence_sha256"][
                "verify-final"
            ] = final_sha256
            result["worker_return"]["result"] = copy.deepcopy(
                restored["document"]
            )
            result["worker_return"]["result_sha256"] = restored[
                "canonical_document_sha256"
            ]
            previous = MODULE.RESTORE.ZERO_SHA256
            for event in result["journal_events"]:
                event["previous_event_sha256"] = previous
                if event["kind"] == "completed":
                    event["evidence_sha256"] = result[
                        "action_evidence"
                    ][event["action"]]["canonical_document_sha256"]
                event["event_sha256"] = (
                    MODULE.RESTORE.WORKER._event_hash(event)
                )
                previous = event["event_sha256"]
            results[role] = MODULE.RESTORE.validate_host_result(
                result,
                request=requests[role],
            )

        restore_output = private_directory(
            Path(
                MODULE.RESTORE.canonical_controller_output_directory(
                    requests
                )
            )
        )
        completion_document, completion_sha256 = (
            MODULE.RESTORE.build_completion(requests, results)
        )
        completion_path, persisted_completion_sha256 = (
            MODULE.RESTORE.persist_completion(
                restore_output,
                completion_document,
            )
        )
        self.assertEqual(
            persisted_completion_sha256,
            completion_sha256,
        )
        claim_sha256 = completion_document[
            "live_lease_claim_sha256"
        ]
        for role in MODULE.ROLES:
            request_path = MODULE.RESTORE._prepared_request_path(
                restore_output,
                role=role,
                claim_sha256=claim_sha256,
            )
            request_path.parent.mkdir(
                parents=True,
                mode=0o700,
                exist_ok=True,
            )
            request_path.parent.chmod(0o700)
            request_path.write_bytes(
                MODULE.RESTORE.canonical_json(requests[role])
            )
            request_path.chmod(0o600)

        consumption_path = (
            restore_output.parent
            / "live-leases"
            / "consumptions"
            / (
                completion_document["live_lease_claim_sha256"]
                + ".json"
            )
        )
        consumption_path.parent.mkdir(
            parents=True,
            mode=0o700,
            exist_ok=True,
        )
        consumption_path.parent.chmod(0o700)
        consumption_document = {
            "schema": MODULE.NGINX.LIVE_LEASE_CONSUMPTION_SCHEMA,
            "status": "consumed",
            "owner_action": MODULE.WORKER.LIVE_LEASE_OWNER_ACTION,
            "operation_id": completion_document["operation_id"],
            "release_sha": completion_document["release_sha"],
            "release_tree_sha": completion_document["release_tree_sha"],
            "aggregate_sha256": "1" * 64,
            "claim_sha256": completion_document[
                "live_lease_claim_sha256"
            ],
            "claim_epoch": completion_document["live_lease_claim_epoch"],
            "claim_nonce": completion_document["live_lease_claim_nonce"],
            "outcome": MODULE.WORKER.LIVE_LEASE_SUCCESS_OUTCOME,
            "outcome_sha256": completion_sha256,
            "readiness_audit_sha256": None,
            "final_state": "legacy-frozen",
            "final_state_receipt_sha256": completion_document[
                "legacy_frozen_receipt_sha256"
            ],
            "controller_journal_sha256": "2" * 64,
            "controller_journal_event_count": 1,
            "controller_evidence_count": 1,
            "controller_evidence_tail_sha256": "3" * 64,
            "consumer_pid": 1,
            "consumption_nonce": "4" * 64,
            "adopted_after_crash": False,
            "controller_lock_path": os.fspath(
                restore_output.parent / "coordinator.lock"
            ),
            "controller_authoritative": True,
            "automatic": False,
        }
        consumption_path.write_bytes(
            MODULE.RESTORE.canonical_json(consumption_document)
        )
        consumption_path.chmod(0o600)
        consumption_sha256 = hashlib.sha256(
            consumption_path.read_bytes()
        ).hexdigest()
        post_document, post_sha256 = (
            MODULE.RESTORE.build_post_consumption_receipt(
                completion_path=completion_path,
                completion_sha256=completion_sha256,
                completion=completion_document,
                consumption_path=consumption_path,
                consumption_sha256=consumption_sha256,
            )
        )
        post_path = restore_output / f"consumption-{post_sha256}.json"
        post_path.write_bytes(MODULE.RESTORE.canonical_json(post_document))
        post_path.chmod(0o600)

        prior_records = {}
        for phase, path in fixture.prior_paths.items():
            document, digest = MODULE.VERIFY.read_root_only_evidence(path)
            prior_records[phase] = {
                "document": document,
                "file_sha256": digest,
                "path": os.fspath(path),
            }
        journal, _journal_sha256 = MODULE._secure_json(
            fixture.journal_path,
            label="integration journal",
        )
        ctx = MODULE.CoordinatorContext(
            manifest_path=fixture.manifest_path,
            manifest=fixture.validated_manifest,
            manifest_sha256=fixture.manifest_sha256,
            plan=fixture.plan,
            plan_sha256=fixture.plan["plan_sha256"],
            restore_set_path=restore_set_path,
            restore_set=restore_set,
            restore_set_sha256=restore_set_sha256,
            requests=requests,
            restore_output_directory=restore_output,
            coordinator_output_directory=private_directory(
                Path(
                    fixture.validated_manifest["deployment"][
                        "controller_evidence_root"
                    ]
                )
                / "shadow-restore-coordinator"
            ),
            prior_paths=dict(fixture.prior_paths),
            prior_records=prior_records,
            journal=journal,
        )
        private_directory(
            ctx.coordinator_output_directory / "phase-evidence"
        )

        def inventory_response(request, *, after):
            role = request["role"]
            patcher = mock.patch.object(
                inventory_fixture,
                "OPERATION_CONTAINER_ID",
                container_ids[role],
            )
            with (
                patcher,
                mock.patch.object(
                    MODULE.INVENTORY,
                    "_verify_execution_context",
                    return_value=(
                        mock.Mock(),
                        [request["expected_host"]],
                    ),
                ),
                mock.patch.object(
                    MODULE.INVENTORY,
                    "_validate_operation_closure",
                    return_value=(
                        request[
                            "expected_operation_host_config_sha256"
                        ]
                        if after
                        else None
                    ),
                ),
            ):
                snapshot = inventory_fixture._snapshot(
                    request,
                    after=after,
                )
                return MODULE.INVENTORY.execute_request(
                    request,
                    runner=inventory_fixture.FakeRunner(
                        [snapshot, copy.deepcopy(snapshot)]
                    ),
                )

        baseline_roles = {}
        after_responses = {}
        for role in MODULE.ROLES:
            before_request = MODULE._inventory_request(
                ctx,
                role=role,
                action="capture-before",
                inventory_agent_sha256=inventory_agent_sha256,
                expected_operation_container_id=None,
                expected_operation_host_config_sha256=None,
                role_manifest_path=None,
                role_manifest_sha256=None,
            )
            baseline_roles[role] = {
                "request": before_request,
                "response": inventory_response(
                    before_request,
                    after=False,
                ),
            }
            role_manifest = results[role]["role_manifest"]
            after_request = MODULE._inventory_request(
                ctx,
                role=role,
                action="capture-after",
                inventory_agent_sha256=inventory_agent_sha256,
                expected_operation_container_id=container_ids[role],
                expected_operation_host_config_sha256=(
                    results[role]["action_evidence"]["verify-final"][
                        "document"
                    ]["semantic"]["database_host_config_sha256"]
                ),
                role_manifest_path=Path(role_manifest["path"]),
                role_manifest_sha256=role_manifest[
                    "canonical_document_sha256"
                ],
            )
            after_responses[role] = inventory_response(
                after_request,
                after=True,
            )
        baseline = MODULE._baseline_document(
            ctx,
            baseline_roles,
            inventory_agent_sha256=inventory_agent_sha256,
        )
        baseline_path, baseline_sha256, _publication = (
            MODULE._persist_document(
                ctx.coordinator_output_directory / "inventory",
                prefix="baseline",
                document=baseline,
            )
        )

        def invoke_after(control):
            request = json.loads(control.stdin.decode("ascii"))
            response = after_responses[request["role"]]
            return bounded_result(
                control,
                stdout=MODULE.INVENTORY.canonical_json(response) + b"\n",
            )

        closure, closure_path, closure_sha256 = (
            MODULE._inventory_closure(
                ctx,
                baseline=baseline,
                baseline_path=baseline_path,
                baseline_sha256=baseline_sha256,
                completion=completion_document,
                completion_path=completion_path,
                completion_sha256=completion_sha256,
                inventory_invoke=invoke_after,
                inventory_agent_sha256=inventory_agent_sha256,
                ssh_trust=inventory_trust(),
            )
        )
        values = MODULE._derive_claim_values(
            ctx,
            completion=completion_document,
            completion_sha256=completion_sha256,
            inventory_closure=closure,
            inventory_closure_sha256=closure_sha256,
        )
        role_validation_paths = {}
        role_validation_sha256 = {}
        for role in MODULE.ROLES:
            payload = fixture.role_paths[role].read_bytes()
            path, digest = MODULE._persist_payload(
                ctx.coordinator_output_directory / "role-validations",
                prefix=role,
                payload=payload,
                maximum=MODULE.MAX_VALIDATION_BYTES,
            )
            role_validation_paths[role] = path
            role_validation_sha256[role] = digest
        _claims, derivation_path, derivation_sha256 = (
            MODULE._write_claim_derivation(
                ctx,
                completion_path=completion_path,
                completion_sha256=completion_sha256,
                post_consumption_path=post_path,
                post_consumption_sha256=post_sha256,
                inventory_closure_path=closure_path,
                inventory_closure_sha256=closure_sha256,
                role_validation_paths=role_validation_paths,
                role_validation_sha256=role_validation_sha256,
                values=values,
                now=producer_fixture.NOW,
            )
        )
        planned = MODULE.PRODUCER.execute_derived(
            derivation_path=derivation_path,
            derivation_sha256=derivation_sha256,
            now=producer_fixture.NOW,
        )
        self.assertEqual(planned["status"], "planned")
        self.assertEqual(
            planned["self_verification_status"],
            "verified",
        )
        self.assertEqual(
            planned["derivation_sha256"],
            derivation_sha256,
        )
        self.assertFalse(planned["output_mutated"])
        inventory_agent_path.write_bytes(b"alternate inventory agent\n")
        inventory_agent_path.chmod(0o644)
        with self.assertRaisesRegex(
            MODULE.PRODUCER.FrozenFinalRestorePhaseEvidenceError,
            "agent differs from immutable release",
        ):
            MODULE.PRODUCER.execute_derived(
                derivation_path=derivation_path,
                derivation_sha256=derivation_sha256,
                now=producer_fixture.NOW,
            )

    def test_role_validation_persists_exact_host_stdout_bytes(self) -> None:
        commands = []
        payloads: dict[str, bytes] = {}
        controls: list[MODULE.ValidationControl] = []
        for role in MODULE.ROLES:
            argv = [
                "/usr/bin/python3",
                "--operation",
                MODULE.OPERATION,
            ]
            if role == "webapp_ir":
                argv = [
                    "/usr/bin/ssh",
                    "-F",
                    "/dev/null",
                    "BatchMode=yes",
                    "IdentitiesOnly=yes",
                    "StrictHostKeyChecking=yes",
                    "UserKnownHostsFile=/root/.ssh/known_hosts",
                    "ConnectTimeout=10",
                    "--operation",
                    MODULE.OPERATION,
                ]
            commands.append(
                {
                    "role": role,
                    "argv": argv,
                    "payload_transfer": (
                        "object-storage-private-versioned-age"
                        if role == "webapp_ir"
                        else "none"
                    ),
                    "render_only": True,
                    "executor_available": False,
                }
            )
            payloads[role] = (
                json.dumps(
                    {"role": role, "spacing": "preserved"},
                    sort_keys=True,
                ).encode("utf-8")
                + b"\n"
            )
        phase = {
            "phase": MODULE.PHASE,
            "execution_supported": False,
            "journal_begin_required_before_commands": True,
            "journal_completion_requires_release_verifier_receipt": True,
            "commands": commands,
        }

        def readback(values, **_kwargs):
            paths = {
                value.partition("=")[0]: Path(value.partition("=")[2])
                for value in values
            }
            digests = {}
            for role, path in paths.items():
                observed = path.read_bytes()
                self.assertEqual(observed, payloads[role])
                digests[role] = hashlib.sha256(observed).hexdigest()
            return (
                {role: SHA256 for role in MODULE.ROLES},
                digests,
                {role: NOW.isoformat() for role in MODULE.ROLES},
            )

        def run_validation(control):
            controls.append(control)
            return bounded_result(
                control,
                stdout=payloads[control.role],
            )

        with (
            mock.patch.object(MODULE, "_phase_plan_row", return_value=phase),
            mock.patch.object(
                MODULE.VERIFY,
                "_read_role_validation_records",
                side_effect=readback,
            ),
        ):
            paths, digests = MODULE._run_role_validations(
                self.context,
                runner=run_validation,
            )
        self.assertEqual(set(paths), set(MODULE.ROLES))
        self.assertEqual(
            [control.role for control in controls],
            list(MODULE.ROLES),
        )
        self.assertTrue(
            all(
                control.stdin == b""
                and control.max_stdout_bytes
                == MODULE.MAX_VALIDATION_BYTES
                and control.max_stderr_bytes
                == MODULE.MAX_CONTROL_STDERR_BYTES
                and control.timeout_seconds
                == MODULE.CONTROL_TIMEOUT_SECONDS
                and control.start_new_session is True
                and control.terminate_process_group_on_exit is True
                and control.kill_process_group_after_seconds
                == MODULE.PROCESS_GROUP_TERM_GRACE_SECONDS
                for control in controls
            )
        )
        self.assertEqual(
            digests,
            {
                role: hashlib.sha256(payloads[role]).hexdigest()
                for role in MODULE.ROLES
            },
        )

    def test_phase_verification_receipt_is_canonical_and_journal_bound(
        self,
    ) -> None:
        evidence_sha256 = "a" * 64
        document = {"verified": True}
        payload = (
            json.dumps(
                document,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        receipt_sha256 = hashlib.sha256(payload).hexdigest()
        journal = {
            "phase_verification_sha256": {
                MODULE.PHASE: receipt_sha256
            }
        }
        token = MODULE.CONTROLLER.VerifiedPhaseCompletion(
            phase=MODULE.PHASE,
            evidence_sha256=evidence_sha256,
            receipt_sha256=receipt_sha256,
        )
        with (
            mock.patch.object(
                MODULE,
                "read_secure_bytes",
                return_value=payload,
            ),
            mock.patch.object(
                MODULE.CONTROLLER,
                "_validate_phase_verification_result",
                return_value=(token, payload),
            ),
        ):
            path = MODULE._validate_phase_verification_receipt(
                self.context,
                journal=journal,
                evidence_sha256=evidence_sha256,
            )
        self.assertEqual(
            path.name,
            f"{MODULE.PHASE}.{receipt_sha256}.json",
        )
        wrong_token = MODULE.CONTROLLER.VerifiedPhaseCompletion(
            phase=MODULE.PHASE,
            evidence_sha256="b" * 64,
            receipt_sha256=receipt_sha256,
        )
        with (
            mock.patch.object(
                MODULE,
                "read_secure_bytes",
                return_value=payload,
            ),
            mock.patch.object(
                MODULE.CONTROLLER,
                "_validate_phase_verification_result",
                return_value=(wrong_token, payload),
            ),
            self.assertRaisesRegex(
                MODULE.RestorePhaseCoordinatorError,
                "differs from journal evidence",
            ),
        ):
            MODULE._validate_phase_verification_receipt(
                self.context,
                journal=journal,
                evidence_sha256=evidence_sha256,
            )

    def test_started_phase_resume_revalidates_runtime_authorization(
        self,
    ) -> None:
        callback = mock.Mock()
        with (
            mock.patch.object(
                MODULE,
                "_verify_runtime_authorization",
                side_effect=MODULE.RestorePhaseCoordinatorError(
                    "production cutover authorization is invalid or expired"
                ),
            ),
            self.assertRaisesRegex(
                MODULE.RestorePhaseCoordinatorError,
                "invalid or expired",
            ),
        ):
            MODULE._begin_phase(
                self.context,
                callback=callback,
                approval_path=self.root / "approval.json",
                approval_policy_path=self.root / "policy.json",
            )
        callback.assert_not_called()

    def test_noop_begin_callback_cannot_leave_journal_active(self) -> None:
        active = {
            **self.context.journal,
            "status": "active",
            "started_phase": None,
        }
        callback = mock.Mock(
            return_value={
                "action": "begin-phase",
                "production_contacted": False,
                "journal": active,
            }
        )
        with (
            mock.patch.object(
                MODULE,
                "_verify_runtime_authorization",
                return_value=(
                    self.root / "approval.json",
                    self.root / "policy.json",
                ),
            ),
            mock.patch.object(
                MODULE,
                "_read_cutover_journal",
                side_effect=(active, active),
            ),
            self.assertRaisesRegex(
                MODULE.RestorePhaseCoordinatorError,
                "did not durably start",
            ),
        ):
            MODULE._begin_phase(
                self.context,
                callback=callback,
                approval_path=self.root / "approval.json",
                approval_policy_path=self.root / "policy.json",
            )
        callback.assert_called_once()

    def test_already_complete_apply_requires_canonical_readback(self) -> None:
        journal = {
            **self.context.journal,
            "status": "active",
            "started_phase": None,
            "completed_phases": [
                *self.context.journal["completed_phases"],
                MODULE.PHASE,
            ],
            "phase_evidence_sha256": {
                **self.context.journal["phase_evidence_sha256"],
                MODULE.PHASE: "a" * 64,
            },
            "phase_verification_sha256": {
                **self.context.journal["phase_verification_sha256"],
                MODULE.PHASE: "b" * 64,
            },
        }
        completed_context = replace(self.context, journal=journal)
        callback = mock.Mock()
        with (
            mock.patch.object(
                MODULE,
                "_load_context",
                return_value=completed_context,
            ),
            mock.patch.object(
                MODULE,
                "_plan_document",
                return_value={
                    "required_confirmation": "exact",
                    "inventory_agent_sha256": SHA256,
                },
            ),
            mock.patch.object(
                MODULE,
                "_validate_completed_phase_readback",
                side_effect=MODULE.RestorePhaseCoordinatorError(
                    "completed shadow_restore evidence is invalid"
                ),
            ) as validate,
            self.assertRaisesRegex(
                MODULE.RestorePhaseCoordinatorError,
                "evidence is invalid",
            ),
        ):
            MODULE.apply_restore_phase(
                manifest_path=self.root / "manifest.json",
                restore_set_path=self.root / "restore-set.json",
                requests={},
                prior_phase_evidence={},
                approval_path=self.root / "approval.json",
                approval_policy_path=self.root / "policy.json",
                nginx_inputs=nginx_inputs(completed_context),
                lease=None,
                prepare_restore_request=callback,
                invoke_restore_host=callback,
                inventory_invoke=callback,
                inventory_agent_sha256=SHA256,
                ssh_identity=SSH_IDENTITY,
                known_hosts=KNOWN_HOSTS,
                validation_runner=callback,
                controller_callback=callback,
                evidence_publisher=callback,
                confirm="exact",
            )
        validate.assert_called_once()
        callback.assert_not_called()

    def test_missing_baseline_with_restore_journal_never_recaptures(self) -> None:
        private_directory(self.context.restore_output_directory)
        (
            self.context.restore_output_directory / "controller-journal.json"
        ).write_text("{}", encoding="utf-8")
        invoke = mock.Mock()
        with self.assertRaisesRegex(
            MODULE.RestorePhaseCoordinatorError,
            "without a complete pre-restore baseline",
        ):
            MODULE._persist_or_load_baseline(
                self.context,
                inventory_invoke=invoke,
                inventory_agent_sha256=SHA256,
                ssh_trust=inventory_trust(),
            )
        invoke.assert_not_called()

    def test_unsafe_baseline_reference_fails_instead_of_recapture(self) -> None:
        inventory = private_directory(
            self.context.coordinator_output_directory / "inventory"
        )
        target = self.root / "target.json"
        target.write_text("{}\n", encoding="utf-8")
        target.chmod(0o600)
        (inventory / "baseline-reference.json").symlink_to(target)
        with self.assertRaises(MODULE.RestorePhaseCoordinatorError):
            MODULE._persist_or_load_baseline(
                self.context,
                inventory_invoke=mock.Mock(),
                inventory_agent_sha256=SHA256,
                ssh_trust=inventory_trust(),
            )

    def test_restore_journal_without_baseline_fails_before_begin(self) -> None:
        private_directory(self.context.restore_output_directory)
        restore_journal = (
            self.context.restore_output_directory / "controller-journal.json"
        )
        restore_journal.write_text("{}\n", encoding="utf-8")
        restore_journal.chmod(0o600)
        original = restore_journal.read_bytes()
        callbacks = [mock.Mock() for _index in range(6)]
        begin = mock.Mock()
        lease = FakeLease()
        with (
            mock.patch.object(MODULE, "_load_context", return_value=self.context),
            mock.patch.object(
                MODULE,
                "_plan_document",
                return_value={
                    "required_confirmation": "exact",
                    "inventory_agent_sha256": SHA256,
                },
            ),
            mock.patch.object(
                MODULE,
                "_validate_inventory_agent_release_binding",
                return_value=SHA256,
            ),
            mock.patch.object(MODULE, "_begin_phase", begin),
            self.assertRaisesRegex(
                MODULE.RestorePhaseCoordinatorError,
                "without a complete pre-restore baseline",
            ),
        ):
            MODULE.apply_restore_phase(
                manifest_path=self.root / "manifest.json",
                restore_set_path=self.root / "restore-set.json",
                requests={},
                prior_phase_evidence={},
                approval_path=self.root / "approval.json",
                approval_policy_path=self.root / "policy.json",
                nginx_inputs=nginx_inputs(self.context),
                lease=lease,
                prepare_restore_request=callbacks[0],
                invoke_restore_host=callbacks[1],
                inventory_invoke=callbacks[2],
                inventory_agent_sha256=SHA256,
                ssh_identity=SSH_IDENTITY,
                known_hosts=KNOWN_HOSTS,
                validation_runner=callbacks[3],
                controller_callback=callbacks[4],
                evidence_publisher=callbacks[5],
                confirm="exact",
            )
        begin.assert_not_called()
        lease.consume.assert_not_called()
        self.assertEqual(restore_journal.read_bytes(), original)
        for callback in callbacks:
            callback.assert_not_called()

    def test_unsafe_baseline_fails_before_begin(self) -> None:
        inventory = private_directory(
            self.context.coordinator_output_directory / "inventory"
        )
        target = self.root / "unsafe-baseline-target.json"
        target.write_text("{}\n", encoding="utf-8")
        target.chmod(0o600)
        reference = inventory / "baseline-reference.json"
        reference.symlink_to(target)
        callbacks = [mock.Mock() for _index in range(6)]
        begin = mock.Mock()
        lease = FakeLease()
        with (
            mock.patch.object(MODULE, "_load_context", return_value=self.context),
            mock.patch.object(
                MODULE,
                "_plan_document",
                return_value={
                    "required_confirmation": "exact",
                    "inventory_agent_sha256": SHA256,
                },
            ),
            mock.patch.object(
                MODULE,
                "_validate_inventory_agent_release_binding",
                return_value=SHA256,
            ),
            mock.patch.object(MODULE, "_begin_phase", begin),
            self.assertRaises(MODULE.RestorePhaseCoordinatorError),
        ):
            MODULE.apply_restore_phase(
                manifest_path=self.root / "manifest.json",
                restore_set_path=self.root / "restore-set.json",
                requests={},
                prior_phase_evidence={},
                approval_path=self.root / "approval.json",
                approval_policy_path=self.root / "policy.json",
                nginx_inputs=nginx_inputs(self.context),
                lease=lease,
                prepare_restore_request=callbacks[0],
                invoke_restore_host=callbacks[1],
                inventory_invoke=callbacks[2],
                inventory_agent_sha256=SHA256,
                ssh_identity=SSH_IDENTITY,
                known_hosts=KNOWN_HOSTS,
                validation_runner=callbacks[3],
                controller_callback=callbacks[4],
                evidence_publisher=callbacks[5],
                confirm="exact",
            )
        begin.assert_not_called()
        lease.consume.assert_not_called()
        self.assertTrue(reference.is_symlink())
        self.assertEqual(target.read_bytes(), b"{}\n")
        for callback in callbacks:
            callback.assert_not_called()

    def test_post_receipt_requires_actual_consumption_audit(self) -> None:
        completion_value = {
            "operation_id": OPERATION,
            "release_sha": SHA40,
            "restore_generation_sha256": "6" * 64,
            "live_lease_claim_sha256": "7" * 64,
            "live_lease_claim_epoch": 4,
            "live_lease_claim_nonce": "8" * 64,
        }
        completion_path = self.root / "completion.json"
        completion_sha256 = "9" * 64
        consumption_path = self.root / "consumption.json"
        consumption_sha256 = "a" * 64
        post, post_sha256 = MODULE.RESTORE.build_post_consumption_receipt(
            completion_path=completion_path,
            completion_sha256=completion_sha256,
            completion=completion_value,
            consumption_path=consumption_path,
            consumption_sha256=consumption_sha256,
        )
        post_path = self.root / f"consumption-{post_sha256}.json"
        post_path.write_bytes(MODULE.RESTORE.canonical_json(post))
        post_path.chmod(0o600)
        store = SimpleNamespace(
            document={
                "post_consumption": {
                    "path": os.fspath(post_path),
                    "sha256": post_sha256,
                },
                "consumption": {
                    "path": os.fspath(consumption_path),
                    "sha256": consumption_sha256,
                },
            }
        )
        authority = {
            "claim_path": os.fspath(self.root / "claim.json"),
            "claim_sha256": "7" * 64,
        }
        prepared = {
            role: {"role": role, "authority": authority}
            for role in MODULE.ROLES
        }
        audit = {
            "schema": MODULE.NGINX.LIVE_LEASE_CONSUMPTION_SCHEMA,
            "status": "consumed",
            "claim_sha256": "7" * 64,
            "claim_epoch": 4,
            "claim_nonce": "8" * 64,
            "outcome": MODULE.WORKER.LIVE_LEASE_SUCCESS_OUTCOME,
            "outcome_sha256": completion_sha256,
        }
        with (
            mock.patch.object(
                MODULE.RESTORE,
                "validate_host_request",
                side_effect=lambda value: dict(value),
            ),
            mock.patch.object(
                MODULE.RESTORE,
                "coordinator_consumption_readback",
                return_value=(
                    consumption_path,
                    consumption_sha256,
                    audit,
                ),
            ) as readback,
        ):
            observed = MODULE._post_consumption_receipt(
                completion=completion_value,
                completion_path=completion_path,
                completion_sha256=completion_sha256,
                prepared_requests=prepared,
                nginx_inputs=nginx_inputs(self.context),
                store=store,
            )
        self.assertEqual(observed, (post, post_path, post_sha256))
        readback.assert_called_once()
        forged = {**audit, "outcome_sha256": "b" * 64}
        with (
            mock.patch.object(
                MODULE.RESTORE,
                "validate_host_request",
                side_effect=lambda value: dict(value),
            ),
            mock.patch.object(
                MODULE.RESTORE,
                "coordinator_consumption_readback",
                return_value=(
                    consumption_path,
                    consumption_sha256,
                    forged,
                ),
            ),
            self.assertRaisesRegex(
                MODULE.RestorePhaseCoordinatorError,
                "audit binding differs",
            ),
        ):
            MODULE._post_consumption_receipt(
                completion=completion_value,
                completion_path=completion_path,
                completion_sha256=completion_sha256,
                prepared_requests=prepared,
                nginx_inputs=nginx_inputs(self.context),
                store=store,
            )

    def test_apply_persists_post_inventory_before_lease_consumption(self) -> None:
        order: list[str] = []
        phase_evidence = (
            self.context.coordinator_output_directory
            / "phase-evidence"
            / f"{MODULE.PHASE}.{'b' * 64}.json"
        )
        derivation = self.root / "derivation.json"
        lease = FakeLease(order)
        complete = completion(self.context)
        private_directory(self.context.restore_output_directory)

        def run_restore(**kwargs):
            order.append("restore")
            kwargs["checkpoint"]("after-completion-before-consume")
            order.append("consume")
            return {"status": "complete"}

        def inventory_closure(*args, **kwargs):
            del args, kwargs
            order.append("post-inventory")
            return (
                {"non_operation_resource_delta_count": 0},
                self.root / "inventory.json",
                "a" * 64,
            )

        publication = {
            "status": "published",
            "self_verification_status": "verified",
            "journal_mutated": False,
            "production_contacted": False,
            "output": os.fspath(phase_evidence),
            "evidence_sha256": "b" * 64,
        }
        evidence = {
            "phase": MODULE.PHASE,
            "operation_id": OPERATION,
            "claims": {
                claim: {"source_sha256": "c" * 64, "value": value}
                for claim, value in {
                    "postgres_restore_verified": True,
                    "reviewed_file_restore_verified": True,
                    "legacy_redis_restore_byte_count": 0,
                    "non_operation_resource_delta_count": 0,
                    "inventory_closure_sha256": "a" * 64,
                    "restored_postgres_snapshot_set_sha256": "a" * 64,
                    "restored_reviewed_file_snapshot_set_sha256": "b" * 64,
                    "restore_result_set_sha256": "d" * 64,
                }.items()
            },
        }
        derivation_document = {
            "claims": {
                claim: {"source_sha256": "c" * 64}
                for claim in MODULE.CLAIMS
            }
        }
        final_journal = {
            "phase_verification_sha256": {MODULE.PHASE: "e" * 64}
        }
        with (
            mock.patch.object(MODULE, "_load_context", return_value=self.context),
            mock.patch.object(
                MODULE,
                "_plan_document",
                return_value={
                    "required_confirmation": "exact",
                    "inventory_agent_sha256": SHA256,
                },
            ),
            mock.patch.object(MODULE, "_begin_phase"),
            mock.patch.object(
                MODULE,
                "_validate_inventory_agent_release_binding",
                return_value=SHA256,
            ),
            mock.patch.object(
                MODULE,
                "_persist_or_load_baseline",
                return_value=(
                    {"roles": {}},
                    self.root / "baseline.json",
                    "f" * 64,
                ),
            ),
            mock.patch.object(
                MODULE.RESTORE,
                "_validate_exact_controller_live_lease",
            ),
            mock.patch.object(
                MODULE.RESTORE,
                "recover_consumed_controller_operation",
                return_value=None,
            ),
            mock.patch.object(
                MODULE.RESTORE,
                "run_three_roles_under_lease",
                side_effect=run_restore,
            ),
            mock.patch.object(
                MODULE,
                "_load_validated_restore_closure",
                return_value=(
                    complete,
                    self.root / "completion.json",
                    "d" * 64,
                    {},
                    mock.Mock(),
                ),
            ),
            mock.patch.object(
                MODULE,
                "_inventory_closure",
                side_effect=inventory_closure,
            ),
            mock.patch.object(
                MODULE,
                "_post_consumption_receipt",
                return_value=(
                    {},
                    self.root / "post.json",
                    "9" * 64,
                ),
            ),
            mock.patch.object(
                MODULE,
                "_run_role_validations",
                return_value=(
                    {
                        role: self.root / f"{role}.json"
                        for role in MODULE.ROLES
                    },
                    {},
                ),
            ),
            mock.patch.object(
                MODULE,
                "_derive_claim_values",
                return_value={
                    claim: evidence["claims"][claim]["value"]
                    for claim in MODULE.CLAIMS
                },
            ),
            mock.patch.object(
                MODULE,
                "_write_claim_derivation",
                return_value=(
                    {
                        claim: self.root / f"{claim}.json"
                        for claim in MODULE.CLAIMS
                    },
                    derivation,
                    "1" * 64,
                ),
            ),
            mock.patch.object(
                MODULE.VERIFY,
                "read_root_only_evidence",
                return_value=(evidence, "b" * 64),
            ),
            mock.patch.object(
                MODULE,
                "_secure_json",
                return_value=(derivation_document, "1" * 64),
            ),
            mock.patch.object(
                MODULE,
                "_complete_phase",
                return_value=final_journal,
            ),
        ):
            result = MODULE.apply_restore_phase(
                manifest_path=self.root / "manifest.json",
                restore_set_path=self.root / "restore-set.json",
                requests={},
                prior_phase_evidence={},
                approval_path=self.root / "approval.json",
                approval_policy_path=self.root / "policy.json",
                nginx_inputs=nginx_inputs(self.context),
                lease=lease,
                prepare_restore_request=mock.Mock(),
                invoke_restore_host=mock.Mock(),
                inventory_invoke=mock.Mock(),
                inventory_agent_sha256=SHA256,
                ssh_identity=SSH_IDENTITY,
                known_hosts=KNOWN_HOSTS,
                validation_runner=mock.Mock(),
                controller_callback=mock.Mock(),
                evidence_publisher=lambda _publication: publication,
                confirm="exact",
                now=NOW,
            )
        self.assertEqual(result["status"], "complete")
        self.assertLess(
            order.index("post-inventory"),
            order.index("consume"),
        )

    def test_post_inventory_failure_prevents_consumption(self) -> None:
        order: list[str] = []
        lease = FakeLease()

        def run_restore(**kwargs):
            kwargs["checkpoint"]("after-completion-before-consume")
            order.append("consume")
            return {}

        private_directory(self.context.restore_output_directory)
        with (
            mock.patch.object(MODULE, "_load_context", return_value=self.context),
            mock.patch.object(
                MODULE,
                "_plan_document",
                return_value={
                    "required_confirmation": "exact",
                    "inventory_agent_sha256": SHA256,
                },
            ),
            mock.patch.object(MODULE, "_begin_phase"),
            mock.patch.object(
                MODULE,
                "_validate_inventory_agent_release_binding",
                return_value=SHA256,
            ),
            mock.patch.object(
                MODULE,
                "_persist_or_load_baseline",
                return_value=(
                    {"roles": {}},
                    self.root / "baseline.json",
                    SHA256,
                ),
            ),
            mock.patch.object(
                MODULE.RESTORE,
                "_validate_exact_controller_live_lease",
            ),
            mock.patch.object(
                MODULE.RESTORE,
                "recover_consumed_controller_operation",
                return_value=None,
            ),
            mock.patch.object(
                MODULE.RESTORE,
                "run_three_roles_under_lease",
                side_effect=run_restore,
            ),
            mock.patch.object(
                MODULE,
                "_load_validated_restore_closure",
                return_value=(
                    completion(self.context),
                    self.root / "completion.json",
                    OTHER256,
                    {},
                    mock.Mock(),
                ),
            ),
            mock.patch.object(
                MODULE,
                "_inventory_closure",
                side_effect=MODULE.RestorePhaseCoordinatorError(
                    "delta"
                ),
            ),
        ):
            with self.assertRaisesRegex(
                MODULE.RestorePhaseCoordinatorError,
                "delta",
            ):
                MODULE.apply_restore_phase(
                    manifest_path=self.root / "manifest.json",
                    restore_set_path=self.root / "restore-set.json",
                    requests={},
                    prior_phase_evidence={},
                    approval_path=self.root / "approval.json",
                    approval_policy_path=self.root / "policy.json",
                    nginx_inputs=nginx_inputs(self.context),
                    lease=lease,
                    prepare_restore_request=mock.Mock(),
                    invoke_restore_host=mock.Mock(),
                    inventory_invoke=mock.Mock(),
                    inventory_agent_sha256=SHA256,
                    ssh_identity=SSH_IDENTITY,
                    known_hosts=KNOWN_HOSTS,
                    validation_runner=mock.Mock(),
                    controller_callback=mock.Mock(),
                    evidence_publisher=mock.Mock(),
                    confirm="exact",
                )
        self.assertNotIn("consume", order)

    def test_skipped_preconsume_checkpoint_never_captures_afterward(
        self,
    ) -> None:
        private_directory(self.context.restore_output_directory)
        capture = mock.Mock()
        with (
            mock.patch.object(MODULE, "_load_context", return_value=self.context),
            mock.patch.object(
                MODULE,
                "_plan_document",
                return_value={
                    "required_confirmation": "exact",
                    "inventory_agent_sha256": SHA256,
                },
            ),
            mock.patch.object(MODULE, "_begin_phase"),
            mock.patch.object(
                MODULE,
                "_validate_inventory_agent_release_binding",
                return_value=SHA256,
            ),
            mock.patch.object(
                MODULE,
                "_persist_or_load_baseline",
                return_value=(
                    {"roles": {}},
                    self.root / "baseline.json",
                    OTHER256,
                ),
            ),
            mock.patch.object(
                MODULE.RESTORE,
                "_validate_exact_controller_live_lease",
            ),
            mock.patch.object(
                MODULE.RESTORE,
                "recover_consumed_controller_operation",
                return_value=None,
            ),
            mock.patch.object(
                MODULE.RESTORE,
                "run_three_roles_under_lease",
                return_value={"status": "complete"},
            ),
            mock.patch.object(
                MODULE,
                "_load_validated_restore_closure",
                return_value=(
                    completion(self.context),
                    self.root / "completion.json",
                    "8" * 64,
                    {},
                    mock.Mock(),
                ),
            ),
            mock.patch.object(MODULE, "_inventory_closure", capture),
            self.assertRaisesRegex(
                MODULE.RestorePhaseCoordinatorError,
                "pre-consume inventory closure",
            ),
        ):
            MODULE.apply_restore_phase(
                manifest_path=self.root / "manifest.json",
                restore_set_path=self.root / "restore-set.json",
                requests={},
                prior_phase_evidence={},
                approval_path=self.root / "approval.json",
                approval_policy_path=self.root / "policy.json",
                nginx_inputs=nginx_inputs(self.context),
                lease=FakeLease(),
                prepare_restore_request=mock.Mock(),
                invoke_restore_host=mock.Mock(),
                inventory_invoke=mock.Mock(),
                inventory_agent_sha256=SHA256,
                ssh_identity=SSH_IDENTITY,
                known_hosts=KNOWN_HOSTS,
                validation_runner=mock.Mock(),
                controller_callback=mock.Mock(),
                evidence_publisher=mock.Mock(),
                confirm="exact",
            )
        capture.assert_not_called()

    def test_nginx_input_mismatch_fails_before_host_or_consume(self) -> None:
        lease = mock.Mock()
        callbacks = [mock.Mock() for _index in range(6)]
        with (
            mock.patch.object(MODULE, "_load_context", return_value=self.context),
            mock.patch.object(
                MODULE,
                "_plan_document",
                return_value={
                    "required_confirmation": "exact",
                    "inventory_agent_sha256": SHA256,
                },
            ),
            self.assertRaisesRegex(
                MODULE.RestorePhaseCoordinatorError,
                "differ from validated Nginx trust",
            ),
        ):
            MODULE.apply_restore_phase(
                manifest_path=self.root / "manifest.json",
                restore_set_path=self.root / "restore-set.json",
                requests={},
                prior_phase_evidence={},
                approval_path=self.root / "approval.json",
                approval_policy_path=self.root / "policy.json",
                nginx_inputs=nginx_inputs(
                    self.context,
                    coordinator_root=self.root / "wrong-controller",
                ),
                lease=lease,
                prepare_restore_request=callbacks[0],
                invoke_restore_host=callbacks[1],
                inventory_invoke=callbacks[2],
                inventory_agent_sha256=SHA256,
                ssh_identity=SSH_IDENTITY,
                known_hosts=KNOWN_HOSTS,
                validation_runner=callbacks[3],
                controller_callback=callbacks[4],
                evidence_publisher=callbacks[5],
                confirm="exact",
            )
        for callback in callbacks:
            callback.assert_not_called()
        lease.consume.assert_not_called()

    def test_live_lease_nginx_material_mismatch_is_nonmutating(self) -> None:
        lease = FakeLease()
        callbacks = [mock.Mock() for _index in range(6)]
        begin = mock.Mock()
        ensure = mock.Mock()
        with (
            mock.patch.object(MODULE, "_load_context", return_value=self.context),
            mock.patch.object(
                MODULE,
                "_plan_document",
                return_value={
                    "required_confirmation": "exact",
                    "inventory_agent_sha256": SHA256,
                },
            ),
            mock.patch.object(
                MODULE,
                "_validate_inventory_agent_release_binding",
                return_value=SHA256,
            ),
            mock.patch.object(
                MODULE.RESTORE,
                "_validate_exact_controller_live_lease",
            ),
            mock.patch.object(
                MODULE.NGINX,
                "_load_claim_from_controller",
                side_effect=MODULE.NGINX.NginxCoordinatorError(
                    "aggregate differs"
                ),
            ),
            mock.patch.object(MODULE, "_begin_phase", begin),
            mock.patch.object(
                MODULE,
                "_ensure_private_directory",
                ensure,
            ),
            self.assertRaisesRegex(
                MODULE.RestorePhaseCoordinatorError,
                "differs from validated coordinator inputs",
            ),
        ):
            MODULE.apply_restore_phase(
                manifest_path=self.root / "manifest.json",
                restore_set_path=self.root / "restore-set.json",
                requests={},
                prior_phase_evidence={},
                approval_path=self.root / "approval.json",
                approval_policy_path=self.root / "policy.json",
                nginx_inputs=nginx_inputs(self.context),
                lease=lease,
                prepare_restore_request=callbacks[0],
                invoke_restore_host=callbacks[1],
                inventory_invoke=callbacks[2],
                inventory_agent_sha256=SHA256,
                ssh_identity=SSH_IDENTITY,
                known_hosts=KNOWN_HOSTS,
                validation_runner=callbacks[3],
                controller_callback=callbacks[4],
                evidence_publisher=callbacks[5],
                confirm="exact",
            )
        begin.assert_not_called()
        ensure.assert_not_called()
        lease.consume.assert_not_called()
        self.assertFalse(self.context.coordinator_output_directory.exists())
        for callback in callbacks:
            callback.assert_not_called()

    def test_missing_or_wrong_first_lease_is_nonmutating(self) -> None:
        callbacks = [mock.Mock() for _index in range(6)]
        begin = mock.Mock()
        ensure = mock.Mock()
        common = {
            "manifest_path": self.root / "manifest.json",
            "restore_set_path": self.root / "restore-set.json",
            "requests": {},
            "prior_phase_evidence": {},
            "approval_path": self.root / "approval.json",
            "approval_policy_path": self.root / "policy.json",
            "nginx_inputs": nginx_inputs(self.context),
            "prepare_restore_request": callbacks[0],
            "invoke_restore_host": callbacks[1],
            "inventory_invoke": callbacks[2],
            "inventory_agent_sha256": SHA256,
            "ssh_identity": SSH_IDENTITY,
            "known_hosts": KNOWN_HOSTS,
            "validation_runner": callbacks[3],
            "controller_callback": callbacks[4],
            "evidence_publisher": callbacks[5],
            "confirm": "exact",
        }
        with (
            mock.patch.object(MODULE, "_load_context", return_value=self.context),
            mock.patch.object(
                MODULE,
                "_plan_document",
                return_value={
                    "required_confirmation": "exact",
                    "inventory_agent_sha256": SHA256,
                },
            ),
            mock.patch.object(
                MODULE,
                "_validate_inventory_agent_release_binding",
                return_value=SHA256,
            ),
            mock.patch.object(MODULE, "_begin_phase", begin),
            mock.patch.object(
                MODULE,
                "_ensure_private_directory",
                ensure,
            ),
            self.assertRaisesRegex(
                MODULE.RestorePhaseCoordinatorError,
                "first inventory baseline requires",
            ),
        ):
            MODULE.apply_restore_phase(lease=None, **common)
        begin.assert_not_called()
        ensure.assert_not_called()
        self.assertFalse(self.context.coordinator_output_directory.exists())
        wrong_lease = mock.Mock()
        with (
            mock.patch.object(MODULE, "_load_context", return_value=self.context),
            mock.patch.object(
                MODULE,
                "_plan_document",
                return_value={
                    "required_confirmation": "exact",
                    "inventory_agent_sha256": SHA256,
                },
            ),
            mock.patch.object(
                MODULE,
                "_validate_inventory_agent_release_binding",
                return_value=SHA256,
            ),
            mock.patch.object(MODULE, "_begin_phase", begin),
            mock.patch.object(
                MODULE,
                "_ensure_private_directory",
                ensure,
            ),
            mock.patch.object(
                MODULE.RESTORE,
                "_validate_exact_controller_live_lease",
                side_effect=RuntimeError("wrong lease"),
            ),
            self.assertRaisesRegex(
                MODULE.RestorePhaseCoordinatorError,
                "exact Nginx live lease is not held",
            ),
        ):
            MODULE.apply_restore_phase(lease=wrong_lease, **common)
        begin.assert_not_called()
        ensure.assert_not_called()
        wrong_lease.consume.assert_not_called()
        self.assertFalse(self.context.coordinator_output_directory.exists())
        for callback in callbacks:
            callback.assert_not_called()

    def test_unconsumed_resume_recaptures_preconsume_inventory_closure(
        self,
    ) -> None:
        private_directory(self.context.restore_output_directory)
        (
            self.context.restore_output_directory / "controller-journal.json"
        ).write_text("{}\n", encoding="utf-8")
        private_directory(self.context.coordinator_output_directory)
        inventory_directory = private_directory(
            self.context.coordinator_output_directory / "inventory"
        )
        (inventory_directory / "baseline-reference.json").write_text(
            "{}\n",
            encoding="utf-8",
        )
        (inventory_directory / "zero-delta-reference.json").write_text(
            "{}\n",
            encoding="utf-8",
        )
        capture = mock.Mock(
            return_value=(
                {"non_operation_resource_delta_count": 0},
                self.root / "zero-delta.json",
                SHA256,
            )
        )
        load_reference = mock.Mock(
            return_value=(
                {"non_operation_resource_delta_count": 0},
                self.root / "zero-delta.json",
                SHA256,
            )
        )

        def run_restore(**kwargs):
            kwargs["checkpoint"]("after-completion-before-consume")
            raise MODULE.RestorePhaseCoordinatorError("resume-stop")

        with (
            mock.patch.object(MODULE, "_load_context", return_value=self.context),
            mock.patch.object(
                MODULE,
                "_plan_document",
                return_value={
                    "required_confirmation": "exact",
                    "inventory_agent_sha256": SHA256,
                },
            ),
            mock.patch.object(MODULE, "_begin_phase"),
            mock.patch.object(
                MODULE,
                "_validate_inventory_agent_release_binding",
                return_value=SHA256,
            ),
            mock.patch.object(
                MODULE,
                "_load_existing_baseline",
                return_value=(
                    {"roles": {}},
                    self.root / "baseline.json",
                    OTHER256,
                ),
            ),
            mock.patch.object(
                MODULE.RESTORE,
                "_validate_exact_controller_live_lease",
            ),
            mock.patch.object(
                MODULE.RESTORE,
                "recover_consumed_controller_operation",
                side_effect=MODULE.RESTORE.ConsumptionAuditAbsent(
                    "not consumed"
                ),
            ),
            mock.patch.object(
                MODULE.RESTORE,
                "run_three_roles_under_lease",
                side_effect=run_restore,
            ),
            mock.patch.object(
                MODULE,
                "_load_validated_restore_closure",
                return_value=(
                    completion(self.context),
                    self.root / "completion.json",
                    "8" * 64,
                    {},
                    mock.Mock(),
                ),
            ),
            mock.patch.object(MODULE, "_inventory_closure", capture),
            mock.patch.object(
                MODULE,
                "_load_inventory_closure_reference",
                load_reference,
            ),
            self.assertRaisesRegex(
                MODULE.RestorePhaseCoordinatorError,
                "resume-stop",
            ),
        ):
            MODULE.apply_restore_phase(
                manifest_path=self.root / "manifest.json",
                restore_set_path=self.root / "restore-set.json",
                requests={},
                prior_phase_evidence={},
                approval_path=self.root / "approval.json",
                approval_policy_path=self.root / "policy.json",
                nginx_inputs=nginx_inputs(self.context),
                lease=FakeLease(),
                prepare_restore_request=mock.Mock(),
                invoke_restore_host=mock.Mock(),
                inventory_invoke=mock.Mock(),
                inventory_agent_sha256=SHA256,
                ssh_identity=SSH_IDENTITY,
                known_hosts=KNOWN_HOSTS,
                validation_runner=mock.Mock(),
                controller_callback=mock.Mock(),
                evidence_publisher=mock.Mock(),
                confirm="exact",
            )
        capture.assert_called_once()
        load_reference.assert_not_called()

    def test_consumed_recovery_does_not_require_or_consume_second_lease(
        self,
    ) -> None:
        private_directory(self.context.restore_output_directory)
        (
            self.context.restore_output_directory / "controller-journal.json"
        ).write_text("{}\n", encoding="utf-8")
        private_directory(self.context.coordinator_output_directory)
        baseline_dir = private_directory(
            self.context.coordinator_output_directory / "inventory"
        )
        (baseline_dir / "baseline-reference.json").write_text(
            "{}\n",
            encoding="utf-8",
        )
        run_restore = mock.Mock()
        order: list[str] = []
        inventory = mock.Mock()
        referenced_inventory = (
            {"non_operation_resource_delta_count": 0},
            self.root / "inventory.json",
            SHA256,
        )

        def recover(**kwargs):
            order.append("recovery-lock-entered")
            kwargs["checkpoint"](
                "after-recovered-post-consumption-receipt"
            )
            order.append("recovery-lock-exiting")
            return {
                "status": "complete-recovered-after-consume",
                "second_consume_performed": False,
            }

        def load_reference_inside_lock(*_args, **_kwargs):
            order.append("inventory-reference-verified")
            return referenced_inventory

        with (
            mock.patch.object(MODULE, "_load_context", return_value=self.context),
            mock.patch.object(
                MODULE,
                "_plan_document",
                return_value={
                    "required_confirmation": "exact",
                    "inventory_agent_sha256": SHA256,
                },
            ),
            mock.patch.object(MODULE, "_begin_phase"),
            mock.patch.object(
                MODULE,
                "_validate_inventory_agent_release_binding",
                return_value=SHA256,
            ),
            mock.patch.object(
                MODULE,
                "_load_existing_baseline",
                return_value=(
                    {"roles": {}},
                    self.root / "baseline.json",
                    OTHER256,
                ),
            ),
            mock.patch.object(
                MODULE.RESTORE,
                "recover_consumed_controller_operation",
                side_effect=recover,
            ),
            mock.patch.object(
                MODULE.RESTORE,
                "run_three_roles_under_lease",
                run_restore,
            ),
            mock.patch.object(
                MODULE,
                "_load_validated_restore_closure",
                return_value=(
                    completion(self.context),
                    self.root / "completion.json",
                    "8" * 64,
                    {},
                    mock.Mock(),
                ),
            ),
            mock.patch.object(
                MODULE,
                "_inventory_closure",
                inventory,
            ),
            mock.patch.object(
                MODULE,
                "_load_inventory_closure_reference",
                side_effect=load_reference_inside_lock,
            ) as load_reference,
            mock.patch.object(
                MODULE,
                "_post_consumption_receipt",
                side_effect=MODULE.RestorePhaseCoordinatorError("stop"),
            ),
        ):
            with self.assertRaisesRegex(
                MODULE.RestorePhaseCoordinatorError,
                "stop",
            ):
                MODULE.apply_restore_phase(
                    manifest_path=self.root / "manifest.json",
                    restore_set_path=self.root / "restore-set.json",
                    requests={},
                    prior_phase_evidence={},
                    approval_path=self.root / "approval.json",
                    approval_policy_path=self.root / "policy.json",
                    nginx_inputs=nginx_inputs(self.context),
                    lease=None,
                    prepare_restore_request=mock.Mock(),
                    invoke_restore_host=mock.Mock(),
                    inventory_invoke=mock.Mock(),
                    inventory_agent_sha256=SHA256,
                    ssh_identity=SSH_IDENTITY,
                    known_hosts=KNOWN_HOSTS,
                    validation_runner=mock.Mock(),
                    controller_callback=mock.Mock(),
                    evidence_publisher=mock.Mock(),
                    confirm="exact",
                )
        run_restore.assert_not_called()
        inventory.assert_not_called()
        load_reference.assert_called_once()
        self.assertEqual(
            order,
            [
                "recovery-lock-entered",
                "inventory-reference-verified",
                "recovery-lock-exiting",
            ],
        )

    def test_bounded_runner_preserves_exact_stdin_framing(self) -> None:
        payload = b'{"exact":"stdin framing"}\n'
        control = isolated_python_control(
            "import sys; "
            "sys.stdout.buffer.write(sys.stdin.buffer.read())",
            stdin=payload,
        )
        result = MODULE.run_bounded_process(control)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, payload)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdin_bytes_sent, len(payload))
        self.assertEqual(
            result.control_sha256,
            MODULE._process_control_sha256(control),
        )
        self.assertTrue(result.deadline_enforced)
        self.assertTrue(result.stdout_limit_enforced)
        self.assertTrue(result.stderr_limit_enforced)
        self.assertFalse(result.timed_out)
        self.assertFalse(result.stdout_limit_exceeded)
        self.assertFalse(result.stderr_limit_exceeded)
        self.assertTrue(result.process_group_cleanup_performed)

    def test_bounded_runner_cancels_oversized_stdout(self) -> None:
        control = isolated_python_control(
            "import os,time; "
            "os.write(1, b'x' * 8192); "
            "time.sleep(30)",
            max_stdout_bytes=127,
        )
        started = time.monotonic()
        result = MODULE.run_bounded_process(control)
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertEqual(result.stdout, b"x" * 127)
        self.assertTrue(result.stdout_limit_exceeded)
        self.assertFalse(result.stderr_limit_exceeded)
        self.assertFalse(result.timed_out)
        self.assertTrue(result.process_group_cleanup_performed)
        self.assertTrue(result.process_group_terminated)

    def test_bounded_runner_cancels_oversized_stderr(self) -> None:
        control = isolated_python_control(
            "import os,time; "
            "os.write(2, b'e' * 8192); "
            "time.sleep(30)",
            max_stderr_bytes=113,
        )
        started = time.monotonic()
        result = MODULE.run_bounded_process(control)
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertEqual(result.stderr, b"e" * 113)
        self.assertFalse(result.stdout_limit_exceeded)
        self.assertTrue(result.stderr_limit_exceeded)
        self.assertFalse(result.timed_out)
        self.assertTrue(result.process_group_cleanup_performed)
        self.assertTrue(result.process_group_terminated)

    def test_bounded_runner_enforces_deadline(self) -> None:
        control = isolated_python_control(
            "import time; time.sleep(30)",
            timeout_seconds=0.1,
        )
        started = time.monotonic()
        result = MODULE.run_bounded_process(control)
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertEqual(result.stdout, b"")
        self.assertTrue(result.timed_out)
        self.assertFalse(result.stdout_limit_exceeded)
        self.assertFalse(result.stderr_limit_exceeded)
        self.assertTrue(result.process_group_cleanup_performed)
        self.assertTrue(result.process_group_terminated)

    def test_bounded_runner_root_pidfd_contains_identity_failure(self) -> None:
        opened: list[tuple[int, int]] = []
        real_pidfd_open = os.pidfd_open

        def capture_pidfd(pid: int, flags: int = 0) -> int:
            descriptor = real_pidfd_open(pid, flags)
            opened.append((pid, descriptor))
            return descriptor

        with (
            mock.patch.object(
                MODULE,
                "_direct_child_baseline",
                return_value=frozenset(),
            ),
            mock.patch.object(
                MODULE,
                "_process_identity",
                return_value=None,
            ),
            mock.patch.object(
                MODULE.os,
                "pidfd_open",
                side_effect=capture_pidfd,
            ),
            self.assertRaisesRegex(
                MODULE.RestorePhaseCoordinatorError,
                "identity is unavailable",
            ),
        ):
            MODULE.run_bounded_process(
                isolated_python_control(
                    "import time;time.sleep(60)",
                    timeout_seconds=5,
                )
            )
        self.assertEqual(len(opened), 1)
        pid, descriptor = opened[0]
        self.assertFalse(Path(f"/proc/{pid}").exists())
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_bounded_runner_cleanup_close_preserves_interrupt(self) -> None:
        selector = mock.Mock()
        selector.get_map.return_value = {
            "active": SimpleNamespace(data="stdout")
        }
        selector.select.side_effect = KeyboardInterrupt
        selector.close.side_effect = RuntimeError(
            "forced selector close failure"
        )
        with (
            mock.patch.object(
                MODULE.selectors,
                "DefaultSelector",
                return_value=selector,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            MODULE.run_bounded_process(
                isolated_python_control(
                    "import time;time.sleep(60)",
                    timeout_seconds=5,
                )
            )
        selector.close.assert_called_once_with()

    def test_bounded_runner_terminates_forked_descendant(self) -> None:
        ready = self.root / "forked-descendant-ready"
        source = "\n".join(
            (
                "import os",
                "import signal",
                "import time",
                f"ready = {os.fspath(ready)!r}",
                "pid = os.fork()",
                "if pid == 0:",
                "    signal.signal(signal.SIGTERM, signal.SIG_IGN)",
                "    with open(ready, 'wb') as stream:",
                "        stream.write(b'ready\\n')",
                "    time.sleep(30)",
                "    os._exit(0)",
                "deadline = time.monotonic() + 10",
                "while not os.path.exists(ready):",
                "    if time.monotonic() >= deadline:",
                "        raise RuntimeError('descendant did not start')",
                "    time.sleep(0.005)",
                "os.write(1, (str(pid) + '\\n').encode('ascii'))",
            )
        )
        result = MODULE.run_bounded_process(
            isolated_python_control(source, timeout_seconds=15.0)
        )
        child_pid = int(result.stdout.decode("ascii"))
        self.assertEqual(result.returncode, 0)
        self.assertTrue(ready.is_file())
        self.assertTrue(result.process_group_cleanup_performed)
        self.assertTrue(result.process_group_terminated)
        deadline = time.monotonic() + 2
        child_path = Path(f"/proc/{child_pid}")
        while child_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(
            child_path.exists(),
            "forked descendant or adopted zombie remained in /proc",
        )

    def test_bounded_runner_reaps_rapid_detached_double_fork(self) -> None:
        ready = self.root / "detached-grandchild-pid"
        source = "\n".join(
            (
                "import os",
                "import signal",
                "import time",
                f"ready = {os.fspath(ready)!r}",
                "middle = os.fork()",
                "if middle == 0:",
                "    os.setsid()",
                "    grandchild = os.fork()",
                "    if grandchild == 0:",
                "        signal.signal(signal.SIGTERM, signal.SIG_IGN)",
                "        partial = ready + '.partial'",
                "        with open(partial, 'w', encoding='ascii') as stream:",
                "            stream.write(str(os.getpid()) + '\\n')",
                "            stream.flush()",
                "            os.fsync(stream.fileno())",
                "        os.replace(partial, ready)",
                "        time.sleep(30)",
                "        os._exit(0)",
                "    os._exit(0)",
                "deadline = time.monotonic() + 10",
                "while not os.path.exists(ready):",
                "    if time.monotonic() >= deadline:",
                "        raise RuntimeError('grandchild did not start')",
                "    time.sleep(0.005)",
                "with open(ready, encoding='ascii') as stream:",
                "    grandchild = int(stream.read().strip())",
                "os.write(1, (str(grandchild) + '\\n').encode('ascii'))",
            )
        )
        result = MODULE.run_bounded_process(
            isolated_python_control(source, timeout_seconds=15.0)
        )
        grandchild_pid = int(result.stdout.decode("ascii"))
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.process_group_cleanup_performed)
        self.assertTrue(result.process_group_terminated)
        grandchild_path = Path(f"/proc/{grandchild_pid}")
        deadline = time.monotonic() + 2
        while grandchild_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(
            grandchild_path.exists(),
            "detached grandchild or adopted zombie remained in /proc",
        )

    def test_injected_runners_must_return_complete_typed_contract(
        self,
    ) -> None:
        control = isolated_python_control("raise SystemExit(0)")
        with self.assertRaisesRegex(
            MODULE.RestorePhaseCoordinatorError,
            "typed bounded result",
        ):
            MODULE._invoke_bounded_process(
                lambda _control: (0, b"", b""),
                control,
                label="injected test",
            )
        complete = bounded_result(control, stdout=b"")
        incomplete_results = (
            replace(complete, control_sha256="0" * 64),
            replace(complete, deadline_enforced=False),
            replace(complete, stdout_limit_enforced=False),
            replace(complete, stderr_limit_enforced=False),
            replace(
                complete,
                process_group_cleanup_performed=False,
            ),
        )
        for incomplete in incomplete_results:
            with (
                self.subTest(result=incomplete),
                self.assertRaisesRegex(
                    MODULE.RestorePhaseCoordinatorError,
                    "bounded result contract",
                ),
            ):
                MODULE._invoke_bounded_process(
                    lambda _control: incomplete,
                    control,
                    label="injected test",
                )

    def test_live_apply_defaults_to_concrete_bounded_runners(self) -> None:
        defaults = MODULE.apply_restore_phase.__kwdefaults__
        self.assertIsNotNone(defaults)
        self.assertIsNone(defaults["inventory_invoke"])
        self.assertIsNone(defaults["validation_runner"])

    def test_source_never_completes_cutover_journal_directly(self) -> None:
        source = Path(MODULE.__file__).read_text(encoding="utf-8")
        self.assertNotIn(".complete_phase(", source)
        self.assertIn("start_new_session=control.start_new_session", source)
        self.assertIn("env={}", source)
        self.assertNotIn("subprocess.run(", source)
        self.assertNotIn("os.killpg(", source)
        self.assertNotIn("/usr/bin/scp", source)


if __name__ == "__main__":
    unittest.main()
