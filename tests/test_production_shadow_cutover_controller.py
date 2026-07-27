from __future__ import annotations

from datetime import datetime, timezone
from contextlib import redirect_stdout
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest import mock

from core.docker_image_identity import verify_content_descriptor
from scripts.production_shadow_cutover_controller import (
    APPLY_CONFIRMATION,
    ARTIFACT_FIELDS,
    CutoverContractError,
    EXPECTED_TOPOLOGY,
    FIRST_WRITE_COMMIT_CONFIRMATION,
    FORWARD_ONLY_COMMIT_GATE,
    HOST_AGENT_CONTRACT_SHA256,
    MANIFEST_SCHEMA,
    PHASES,
    PHASE_SPECS,
    PHASE_VERIFICATION_SCHEMA,
    POSTCOMMIT_JOURNAL_STATUS,
    POSTCOMMIT_SPECS,
    POLICY_FIELDS,
    PRODUCTION_VHOSTS,
    REMOTE_AGENT_PATH,
    ProductionCutoverJournal,
    VerifiedPhaseCompletion,
    _persist_phase_verification_receipt,
    _validate_phase_verification_result,
    _secure_root,
    _shadow_project,
    _shadow_root,
    read_root_only_manifest,
    render_plan,
    validate_manifest,
    main,
)


CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
OPERATION_ID = "22222222-2222-4222-8222-222222222222"
RELEASE_SHA = "a" * 40
LEGACY_RELEASE_SHA = "b" * 40


def image_content_binding(seed: str) -> dict:
    descriptor = {
        "architecture": "amd64",
        "os": "linux",
        "created": f"2026-07-27T00:00:0{seed}Z",
        "config_sha256": "sha256:" + seed * 64,
        "rootfs_type": "layers",
        "rootfs_layers": ["sha256:" + seed * 64],
    }
    return {
        "content_descriptor": descriptor,
        "content_identity": verify_content_descriptor(descriptor),
    }


def verified_completion(
    phase: str,
    evidence_sha256: str,
    *,
    receipt_sha256: str = "e" * 64,
) -> VerifiedPhaseCompletion:
    return VerifiedPhaseCompletion(
        phase=phase,
        evidence_sha256=evidence_sha256,
        receipt_sha256=receipt_sha256,
    )


EXPECTED_PHASE_ORDER = [
    "pre_freeze_evidence",
    "shadow_startup_normalization",
    "freeze_generation_install",
    "freeze_generation_test",
    "freeze_generation_activate",
    "stop_legacy_writers",
    "zero_writer_surface_readback",
    "final_snapshot_hashes",
    "pristine_shadow_redis",
    "shadow_restore",
    "shadow_roles_pre_migration",
    "shadow_migrate",
    "shadow_roles_post_migration",
    "shadow_fence",
    "witness_lease",
    "convergence_gate",
    "readonly_upstream_switch",
    "precommit_no_due_mutator_delta",
    "precommit_provider_free_queue_rehydrate",
    "precommit_irreversible_effect_watchers",
    "pre_first_write_acceptance",
]


def manifest_payload() -> dict:
    secure_root = _secure_root(CAMPAIGN_ID)
    return {
        "schema": MANIFEST_SCHEMA,
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "release_sha": RELEASE_SHA,
        "release_tree_sha": "c" * 40,
        "legacy_release_sha": LEGACY_RELEASE_SHA,
        "topology": json.loads(json.dumps(EXPECTED_TOPOLOGY)),
        "deployment": {
            "production_hostname": "coin.gold-trade.ir",
            "legacy_compose_project": "trading_bot",
            "shadow_compose_project": _shadow_project(OPERATION_ID),
            "shadow_root": str(_shadow_root(OPERATION_ID)),
            "controller_journal_path": str(secure_root / "journal.json"),
            "controller_evidence_root": str(secure_root / "evidence"),
        },
        "artifacts": {
            "release_bundle_sha256": "d" * 64,
            "release_bundle_bytes": 101,
            "role_materials": {
                role: {
                    "sha256": str(index) * 64,
                    "bytes": 102 + index,
                    "transport": EXPECTED_TOPOLOGY[role]["transport"],
                    "format": (
                        "production-shadow-witness-material-tar"
                        if role == "witness"
                        else "production-shadow-role-material-tar"
                    ),
                }
                for index, role in enumerate(EXPECTED_TOPOLOGY, 1)
            },
            "image_artifacts": {
                kind: {
                    "archive_sha256": "0" * 63 + archive_suffix,
                    "archive_bytes": 103 + index,
                    "config_digest": "sha256:" + config_char * 64,
                    **image_content_binding(content_char),
                }
                for index, (
                    kind,
                    archive_suffix,
                    config_char,
                    content_char,
                ) in enumerate(
                    (
                        ("app", "1", "a", "5"),
                        ("postgres", "2", "b", "6"),
                        ("redis", "3", "c", "7"),
                        ("nginx", "4", "d", "8"),
                    )
                )
            },
            "role_runtime_image_ids": {
                role: {
                    kind: "sha256:" + value * 64
                    for kind, value in zip(
                        ("app", "postgres", "redis", "nginx"),
                        values,
                        strict=True,
                    )
                }
                for role, values in {
                    "bot_fi": ("1", "2", "3", "4"),
                    "webapp_fi": ("5", "6", "7", "8"),
                    "webapp_ir": ("9", "a", "b", "c"),
                }.items()
            },
            "postgres_runtime_uid": 70,
            "postgres_runtime_gid": 70,
            "postgres_image_ref": f"trading_bot_postgres_boottime:15-{RELEASE_SHA}",
            "legacy_bot_rollback_sha256": "2" * 64,
            "legacy_webapp_rollback_sha256": "3" * 64,
            "legacy_bot_redis_rollback_sha256": "6" * 64,
            "legacy_webapp_redis_rollback_sha256": "7" * 64,
            "shadow_compose_sha256": "4" * 64,
            "cutover_approval_sha256": "5" * 64,
            "nginx_freeze_generation_sha256": "8" * 64,
            "nginx_rollback_generation_sha256": "9" * 64,
            "postcommit_executor_contract_sha256": "a" * 64,
            "phase_evidence_schema_sha256": "b" * 64,
            "host_agent_sha256": "c" * 64,
            "host_agent_contract_sha256": HOST_AGENT_CONTRACT_SHA256,
            "phase_evidence_verifier_sha256": "d" * 64,
        },
        "policy": {field: True for field in POLICY_FIELDS},
    }


class ProductionShadowManifestTests(unittest.TestCase):
    def test_exact_manifest_and_plan_preserve_audited_order(self):
        manifest = validate_manifest(manifest_payload())
        plan = render_plan(manifest, manifest_sha256="4" * 64)

        self.assertEqual(list(PHASES), EXPECTED_PHASE_ORDER)
        self.assertEqual([item["phase"] for item in plan["phases"]], EXPECTED_PHASE_ORDER)
        self.assertFalse(plan["executes_commands"])
        self.assertFalse(plan["live_io_supported"])
        self.assertEqual(plan["apply_scope"], "root-only-local-journal-transitions")
        self.assertEqual(len(plan["plan_sha256"]), 64)
        self.assertEqual(
            plan["plan_sha256"],
            render_plan(manifest, manifest_sha256="4" * 64)["plan_sha256"],
        )
        self.assertEqual(
            plan["execution_backend"], "not-implemented-in-controller-slice"
        )
        self.assertTrue(plan["rollback"]["eligible_until_commit_gate"])
        self.assertTrue(plan["rollback"]["prohibited_after_commit_gate"])
        self.assertTrue(plan["rollback"]["preserves_shadow_volumes_and_artifacts"])
        gate = plan["first_business_write_commit_gate"]
        self.assertFalse(gate["enabled"])
        self.assertTrue(gate["hard_disabled"])
        self.assertTrue(gate["irreversible_boundary"])
        self.assertEqual(gate["required_completed_phase"], PHASES[-1])
        self.assertEqual(
            gate["required_confirmation"], FIRST_WRITE_COMMIT_CONFIRMATION
        )
        self.assertIn(APPLY_CONFIRMATION, gate["prospective_argv_template"])
        self.assertEqual(plan["production_vhosts"], PRODUCTION_VHOSTS)
        self.assertEqual(
            plan["postcommit_forward_recovery"]["first_write_boundary_phase"],
            FORWARD_ONLY_COMMIT_GATE,
        )
        postcommit = plan["postcommit_forward_recovery"]["commands"]
        self.assertEqual(
            [item["phase"] for item in postcommit],
            [spec.phase for spec in POSTCOMMIT_SPECS],
        )
        self.assertFalse(any(item["first_write_boundary"] for item in postcommit))
        self.assertTrue(all(item["business_write_allowed"] for item in postcommit))
        self.assertTrue(all(item["forward_only"] for item in postcommit))
        self.assertTrue(
            all(
                item["required_journal_status"] == POSTCOMMIT_JOURNAL_STATUS
                for item in postcommit
            )
        )
        self.assertEqual(
            postcommit[0]["prerequisite_phase"],
            FORWARD_ONLY_COMMIT_GATE,
        )
        self.assertTrue(
            all(
                command["business_write_allowed"]
                and command["required_journal_status"] == POSTCOMMIT_JOURNAL_STATUS
                for item in postcommit
                for command in item["commands"]
            )
        )
        self.assertTrue(
            all(not item["business_write_allowed"] for item in plan["phases"])
        )
        self.assertEqual(
            plan["redis_contract"]["legacy"],
            "sealed-rollback-evidence-only",
        )
        self.assertFalse(
            plan["webapp_ir_standby_contract"][
                "public_route_enabled_before_promotion"
            ]
        )
        gaps = {item["component"]: item["status"] for item in plan["operational_gaps"]}
        self.assertEqual(
            gaps["postcommit-forward-recovery-executor"],
            "missing-hard-blocker",
        )
        self.assertEqual(
            gaps["phase-evidence-schema-verifiers"],
            "controller-wired-local-verifier",
        )
        verification = plan["phase_evidence_verification"]
        self.assertTrue(
            verification[
                "semantic_verification_required_before_journal_completion"
            ]
        )
        self.assertTrue(
            verification["expected_role_request_sha256_required"]
        )
        self.assertTrue(
            verification[
                "expected_role_source_artifact_readback_sha256_required"
            ]
        )
        self.assertTrue(verification["exact_release_path_required"])
        self.assertTrue(
            verification["verifier_path"].startswith(
                str(_shadow_root(OPERATION_ID))
            )
        )
        self.assertTrue(verification["executor_wired"])
        self.assertTrue(
            verification["controller_executes_release_bound_verifier"]
        )
        agent_contract = plan["host_agent_contract"]
        self.assertEqual(
            agent_contract["sha256"],
            HOST_AGENT_CONTRACT_SHA256,
        )
        self.assertTrue(agent_contract["self_hash_required"])
        self.assertTrue(agent_contract["local_host_identity_required"])
        self.assertFalse(agent_contract["operation_execution_supported"])

    def test_unknown_root_and_nested_fields_fail_closed(self):
        root_extra = manifest_payload()
        root_extra["unexpected"] = True
        with self.assertRaisesRegex(CutoverContractError, "fields are not exact"):
            validate_manifest(root_extra)

        nested_extra = manifest_payload()
        nested_extra["topology"]["webapp_ir"]["identity_file"] = "/tmp/key"
        with self.assertRaisesRegex(CutoverContractError, "fields are not exact"):
            validate_manifest(nested_extra)

        artifact_missing = manifest_payload()
        artifact_missing["artifacts"].pop(next(iter(ARTIFACT_FIELDS)))
        with self.assertRaisesRegex(CutoverContractError, "fields are not exact"):
            validate_manifest(artifact_missing)

        zero_artifact = manifest_payload()
        zero_artifact["artifacts"]["release_bundle_sha256"] = "0" * 64
        with self.assertRaisesRegex(CutoverContractError, "not a SHA-256"):
            validate_manifest(zero_artifact)

        duplicate_image = manifest_payload()
        duplicate_image["artifacts"]["role_runtime_image_ids"]["bot_fi"][
            "nginx"
        ] = duplicate_image["artifacts"]["role_runtime_image_ids"]["bot_fi"][
            "redis"
        ]
        with self.assertRaisesRegex(CutoverContractError, "runtime image"):
            validate_manifest(duplicate_image)

        duplicate_archive = manifest_payload()
        duplicate_archive["artifacts"]["image_artifacts"]["nginx"][
            "archive_sha256"
        ] = duplicate_archive["artifacts"]["image_artifacts"]["redis"][
            "archive_sha256"
        ]
        with self.assertRaisesRegex(
            CutoverContractError,
            "archive_sha256",
        ):
            validate_manifest(duplicate_archive)

        forged_content = manifest_payload()
        forged_content["artifacts"]["image_artifacts"]["app"][
            "content_descriptor"
        ]["created"] = "2026-07-27T01:00:00Z"
        with self.assertRaisesRegex(CutoverContractError, "identity differs"):
            validate_manifest(forged_content)

        witness_runtime = manifest_payload()
        witness_runtime["artifacts"]["role_runtime_image_ids"]["witness"] = {}
        with self.assertRaisesRegex(CutoverContractError, "three Docker roles"):
            validate_manifest(witness_runtime)

    def test_topology_policy_and_paths_are_pinned(self):
        wrong_host = manifest_payload()
        wrong_host["topology"]["webapp_ir"]["host"] = "127.0.0.1"
        with self.assertRaisesRegex(CutoverContractError, "canonical production pin"):
            validate_manifest(wrong_host)

        relaxed = manifest_payload()
        relaxed["policy"]["direct_payload_to_webapp_ir_forbidden"] = False
        with self.assertRaisesRegex(CutoverContractError, "must be true"):
            validate_manifest(relaxed)

        current_path = manifest_payload()
        current_path["deployment"]["shadow_root"] = "/srv/trading-bot/current"
        with self.assertRaisesRegex(CutoverContractError, "not exact"):
            validate_manifest(current_path)

        stock_postgres = manifest_payload()
        stock_postgres["artifacts"]["postgres_image_ref"] = "postgres:15-alpine"
        with self.assertRaisesRegex(CutoverContractError, "custom PostgreSQL"):
            validate_manifest(stock_postgres)

    def test_rendered_commands_are_argv_only_and_wa_payloads_use_object_storage(self):
        plan = render_plan(validate_manifest(manifest_payload()), manifest_sha256="4" * 64)
        all_commands = [
            command
            for phase in plan["phases"]
            for command in phase["commands"]
        ]
        all_commands.extend(
            command
            for phase in plan["rollback"]["commands"]
            for command in phase["commands"]
        )
        all_commands.extend(
            command
            for phase in plan["postcommit_forward_recovery"]["commands"]
            for command in phase["commands"]
        )
        self.assertTrue(all(isinstance(command["argv"], list) for command in all_commands))
        self.assertTrue(all(command["render_only"] for command in all_commands))
        for command in all_commands:
            argv = command["argv"]
            if argv[0] == "/usr/bin/ssh":
                remote = argv[argv.index(REMOTE_AGENT_PATH) :]
                self.assertEqual(
                    shlex.split(" ".join(remote)),
                    remote,
                )
            role = argv[argv.index("--role") + 1]
            runtime_ids = json.loads(
                base64.urlsafe_b64decode(
                    argv[argv.index("--runtime-image-ids-b64") + 1]
                ).decode("utf-8")
            )
            vhosts = json.loads(
                base64.urlsafe_b64decode(
                    argv[argv.index("--production-vhosts-b64") + 1]
                ).decode("utf-8")
            )
            self.assertEqual(
                vhosts,
                json.loads(json.dumps(PRODUCTION_VHOSTS)),
            )
            if role == "witness":
                self.assertEqual(runtime_ids, {})
            else:
                self.assertEqual(
                    runtime_ids,
                    manifest_payload()["artifacts"][
                        "role_runtime_image_ids"
                    ][role],
                )
        self.assertTrue(all(not command["executor_available"] for command in all_commands))
        rendered = "\n".join(" ".join(command["argv"]) for command in all_commands).lower()
        for forbidden in (
            "/current",
            " staging",
            "scp ",
            "rsync ",
            "sftp ",
            "docker compose down",
            "volume rm",
            "downgrade",
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertIn("sealed-rollback-evidence-only", rendered)
        self.assertIn("pristine-empty-no-restore", rendered)
        for command in all_commands:
            if command["role"] in {"webapp_ir", "witness"}:
                self.assertEqual(
                    command["payload_transfer"],
                    "object-storage-private-versioned-age",
                )
                self.assertIn("object-storage-private-versioned-age", command["argv"])

        preparation = [
            command
            for phase in plan["reversible_preparation"]["phases"]
            for command in phase["commands"]
        ]
        self.assertEqual(
            {
                command["argv"][command["argv"].index("--operation") + 1]
                for command in preparation
            },
            {
                "verify-installation",
                "bootstrap-database",
                "restore-shadow",
                "prepare-shadow",
                "readonly-acceptance",
            },
        )
        self.assertTrue(all("--execute" in command["argv"] for command in preparation))
        self.assertTrue(all(command["executor_available"] for command in preparation))
        self.assertTrue(all(not command["business_write_allowed"] for command in preparation))

    def test_freeze_rollback_and_forward_recovery_contracts_are_complete(self):
        plan = render_plan(
            validate_manifest(manifest_payload()),
            manifest_sha256="4" * 64,
        )
        phases = {item["phase"]: item for item in plan["phases"]}
        self.assertEqual(
            {command["role"] for command in phases["freeze_generation_install"]["commands"]},
            {"bot_fi", "webapp_fi"},
        )
        self.assertEqual(
            {
                command["role"]
                for command in phases["zero_writer_surface_readback"]["commands"]
            },
            {"bot_fi", "webapp_fi", "witness"},
        )
        self.assertLess(
            EXPECTED_PHASE_ORDER.index("pristine_shadow_redis"),
            EXPECTED_PHASE_ORDER.index("shadow_restore"),
        )
        self.assertLess(
            EXPECTED_PHASE_ORDER.index("shadow_roles_pre_migration"),
            EXPECTED_PHASE_ORDER.index("shadow_migrate"),
        )
        self.assertLess(
            EXPECTED_PHASE_ORDER.index("shadow_migrate"),
            EXPECTED_PHASE_ORDER.index("shadow_roles_post_migration"),
        )
        self.assertIn(
            "no token",
            phases["precommit_provider_free_queue_rehydrate"][
                "description"
            ].lower(),
        )
        nginx = plan["nginx_generation_transaction"]
        self.assertFalse(nginx["cross_host_instantaneous_atomicity_claimed"])
        self.assertEqual(
            nginx["coordination_model"],
            "ordered-fail-closed-per-host-generation-readback",
        )
        self.assertTrue(nginx["per_host_generation_readback_required"])

        rollback = plan["rollback"]["commands"]
        self.assertEqual(
            [item["phase"] for item in rollback[:2]],
            [
                "rollback_refence_and_revoke_lease",
                "rollback_lease_readback",
            ],
        )
        for item in rollback[2:5]:
            self.assertEqual(
                {command["role"] for command in item["commands"]},
                {"bot_fi", "webapp_fi"},
            )

        postcommit = {
            item["phase"]: item
            for item in plan["postcommit_forward_recovery"]["commands"]
        }
        self.assertEqual(
            {
                command["role"]
                for command in postcommit[
                    "postcommit_activate_webapp_apis"
                ]["commands"]
            },
            {"webapp_fi", "webapp_ir"},
        )
        self.assertEqual(
            {
                command["role"]
                for command in postcommit[
                    "postcommit_activate_fi_effects"
                ]["commands"]
            },
            {"webapp_fi"},
        )
        self.assertNotIn("postcommit_rehydrate_bot_queue", postcommit)
        self.assertIn("postcommit_forward_only_unblock", postcommit)
        rendered = json.dumps(plan, sort_keys=True)
        for vhost in (
            "coin.362514.ir",
            "mini-app.362514.ir",
            "coin.gold-trade.ir",
        ):
            self.assertIn(vhost, rendered)

    def test_root_only_manifest_reader_rejects_mode_symlink_and_duplicate_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest_payload()), encoding="utf-8")
            path.chmod(0o600)
            loaded, digest = read_root_only_manifest(path, owner_uid=os.geteuid())
            self.assertEqual(loaded["campaign_id"], CAMPAIGN_ID)
            self.assertEqual(len(digest), 64)

            path.chmod(0o640)
            with self.assertRaisesRegex(CutoverContractError, "mode 0600"):
                read_root_only_manifest(path, owner_uid=os.geteuid())
            path.chmod(0o600)

            link = root / "manifest-link.json"
            link.symlink_to(path)
            with self.assertRaisesRegex(CutoverContractError, "securely open"):
                read_root_only_manifest(link, owner_uid=os.geteuid())

            duplicate = root / "duplicate.json"
            duplicate.write_text(
                '{"schema":"x","schema":"y"}',
                encoding="utf-8",
            )
            duplicate.chmod(0o600)
            with self.assertRaisesRegex(CutoverContractError, "duplicate manifest field"):
                read_root_only_manifest(duplicate, owner_uid=os.geteuid())

    @unittest.skipUnless(os.geteuid() == 0, "production CLI is intentionally root-only")
    def test_cli_defaults_to_plan_and_non_apply_transition_never_writes_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest_payload()), encoding="utf-8")
            path.chmod(0o600)
            output = io.StringIO()
            with (
                mock.patch.object(
                    ProductionCutoverJournal,
                    "create",
                    side_effect=AssertionError("journal create must not run"),
                ),
                mock.patch(
                    "sys.argv",
                    ["production-shadow-cutover-controller", "--manifest", str(path)],
                ),
                redirect_stdout(output),
            ):
                self.assertEqual(main(), 0)
            planned = json.loads(output.getvalue())
            self.assertEqual(planned["status"], "planned")
            self.assertFalse(planned["executes_commands"])

            output = io.StringIO()
            with (
                mock.patch.object(
                    ProductionCutoverJournal,
                    "create",
                    side_effect=AssertionError("journal create must not run"),
                ),
                mock.patch(
                    "sys.argv",
                    [
                        "production-shadow-cutover-controller",
                        "--manifest",
                        str(path),
                        "--action",
                        "create-journal",
                    ],
                ),
                redirect_stdout(output),
            ):
                self.assertEqual(main(), 0)
            transition = json.loads(output.getvalue())
            self.assertEqual(transition["status"], "planned")
            self.assertFalse(transition["journal_mutated"])
            self.assertEqual(
                transition["required_apply_confirmation"],
                APPLY_CONFIRMATION,
            )

            output = io.StringIO()
            with (
                mock.patch.object(
                    ProductionCutoverJournal,
                    "create",
                    side_effect=AssertionError("journal create must not run"),
                ),
                mock.patch(
                    "sys.argv",
                    [
                        "production-shadow-cutover-controller",
                        "--manifest",
                        str(path),
                        "--action",
                        "commit-first-business-write",
                        "--evidence-sha256",
                        "f" * 64,
                    ],
                ),
                redirect_stdout(output),
            ):
                self.assertEqual(main(), 2)
            blocked = json.loads(output.getvalue())
            self.assertEqual(blocked["status"], "blocked")
            self.assertFalse(blocked["irreversible_commit_enabled"])

    @unittest.skipUnless(os.geteuid() == 0, "production CLI is intentionally root-only")
    def test_cli_complete_phase_runs_release_verifier_and_rejects_raw_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest_payload()), encoding="utf-8")
            path.chmod(0o600)
            token = verified_completion(PHASES[0], "7" * 64)
            receipt = b'{"status":"verified"}\n'
            journal = mock.Mock(unsafe=True)
            journal.assert_bindings.return_value = {"status": "phase_started"}
            journal.complete_phase.return_value = {
                "status": "active",
                "completed_phases": [PHASES[0]],
            }
            output = io.StringIO()
            argv = [
                "production-shadow-cutover-controller",
                "--manifest",
                str(path),
                "--action",
                "complete-phase",
                "--phase",
                PHASES[0],
                "--evidence",
                "/root/evidence.json",
                "--approval",
                "/root/approval.json",
                "--apply",
                "--confirm",
                APPLY_CONFIRMATION,
            ]
            with (
                mock.patch(
                    "scripts.production_shadow_cutover_controller.ProductionCutoverJournal",
                    return_value=journal,
                ),
                mock.patch(
                    "scripts.production_shadow_cutover_controller._run_release_phase_verifier",
                    return_value=(token, receipt),
                ) as verifier,
                mock.patch(
                    "scripts.production_shadow_cutover_controller._persist_phase_verification_receipt",
                    return_value=Path("/root/receipt.json"),
                ) as persist,
                mock.patch("sys.argv", argv),
                redirect_stdout(output),
            ):
                self.assertEqual(main(), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "active")
            verifier.assert_called_once()
            persist.assert_called_once()
            journal.complete_phase.assert_called_once_with(
                PHASES[0],
                verification=token,
            )

            blocked_output = io.StringIO()
            with (
                mock.patch.object(
                    ProductionCutoverJournal,
                    "complete_phase",
                    side_effect=AssertionError("raw digest must not advance"),
                ),
                mock.patch(
                    "sys.argv",
                    [
                        "production-shadow-cutover-controller",
                        "--manifest",
                        str(path),
                        "--action",
                        "complete-phase",
                        "--phase",
                        PHASES[0],
                        "--evidence-sha256",
                        "7" * 64,
                    ],
                ),
                redirect_stdout(blocked_output),
            ):
                self.assertEqual(main(), 2)
            blocked = json.loads(blocked_output.getvalue())
            self.assertEqual(blocked["status"], "blocked")
            self.assertIn("never accepts", blocked["error"])

    @unittest.skipUnless(os.geteuid() == 0, "verification receipt is root-only")
    def test_verifier_result_bindings_and_receipt_are_exact(self):
        manifest = validate_manifest(manifest_payload())
        manifest_sha256 = "4" * 64
        plan = render_plan(manifest, manifest_sha256=manifest_sha256)
        spec = next(item for item in PHASE_SPECS if item.phase == PHASES[0])
        timestamp = datetime.now(timezone.utc).isoformat()
        result = {
            "schema": PHASE_VERIFICATION_SCHEMA,
            "status": "verified",
            "phase": PHASES[0],
            "operation": spec.operation,
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "legacy_release_sha": LEGACY_RELEASE_SHA,
            "manifest_sha256": manifest_sha256,
            "plan_sha256": plan["plan_sha256"],
            "approval_sha256": manifest["artifacts"][
                "cutover_approval_sha256"
            ],
            "phase_evidence_schema_sha256": manifest["artifacts"][
                "phase_evidence_schema_sha256"
            ],
            "manifest_artifact_bindings_sha256": hashlib.sha256(
                json.dumps(
                    manifest["artifacts"],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "prior_phase_evidence_closure_sha256": "1" * 64,
            "phase_input_closure_sha256": "2" * 64,
            "prior_phase_count": 0,
            "evidence_sha256": "3" * 64,
            "verified_roles": list(spec.roles),
            "verified_claim_count": 1,
            "captured_at": timestamp,
            "verified_at": timestamp,
            "production_contacted": False,
        }
        token, receipt = _validate_phase_verification_result(
            result,
            phase=PHASES[0],
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            plan_sha256=plan["plan_sha256"],
        )
        self.assertEqual(token.evidence_sha256, "3" * 64)
        with tempfile.TemporaryDirectory() as directory:
            receipt_path = _persist_phase_verification_receipt(
                token=token,
                receipt=receipt,
                evidence_root=Path(directory),
            )
            self.assertEqual(receipt_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                _persist_phase_verification_receipt(
                    token=token,
                    receipt=receipt,
                    evidence_root=Path(directory),
                ),
                receipt_path,
            )

        forged = dict(result)
        forged["production_contacted"] = True
        with self.assertRaisesRegex(CutoverContractError, "mismatched bindings"):
            _validate_phase_verification_result(
                forged,
                phase=PHASES[0],
                manifest=manifest,
                manifest_sha256=manifest_sha256,
                plan_sha256=plan["plan_sha256"],
            )


class ProductionShadowCutoverJournalTests(unittest.TestCase):
    def journal(self, directory: str) -> ProductionCutoverJournal:
        return ProductionCutoverJournal(
            Path(directory).resolve() / "journal.json",
            owner_uid=os.geteuid(),
        )

    def create(self, journal: ProductionCutoverJournal) -> dict:
        return journal.create(
            manifest_sha256="4" * 64,
            plan_sha256="6" * 64,
            campaign_id=CAMPAIGN_ID,
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            legacy_release_sha=LEGACY_RELEASE_SHA,
        )

    def complete_all(self, journal: ProductionCutoverJournal) -> dict:
        state = journal.load()
        for index, phase in enumerate(PHASES, 1):
            journal.begin_phase(phase)
            state = journal.complete_phase(
                phase,
                verification=verified_completion(
                    phase,
                    f"{index:064x}",
                    receipt_sha256=f"{index + 100:064x}",
                ),
            )
        return state

    def test_crash_visible_resume_and_completion_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.journal(directory)
            created = self.create(journal)
            self.assertEqual(created["status"], "active")
            self.assertEqual(journal.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(created["events"][0]["kind"], "journal_created")
            self.assertEqual(created["event_tail_sha256"], created["events"][0]["event_hash"])

            started = journal.begin_phase(PHASES[0])
            self.assertEqual(started["status"], "phase_started")
            self.assertEqual(started["started_phase"], PHASES[0])
            self.assertEqual(journal.begin_phase(PHASES[0]), started)
            self.assertEqual(ProductionCutoverJournal(
                journal.path, owner_uid=os.geteuid()
            ).load(), started)
            with self.assertRaisesRegex(CutoverContractError, "different"):
                journal.begin_phase(PHASES[1])

            digest = "5" * 64
            completion = verified_completion(PHASES[0], digest)
            completed = journal.complete_phase(
                PHASES[0],
                verification=completion,
            )
            self.assertEqual(completed["status"], "active")
            self.assertEqual(completed["completed_phases"], [PHASES[0]])
            self.assertEqual(
                completed["phase_verification_sha256"][PHASES[0]],
                completion.receipt_sha256,
            )
            self.assertEqual(
                [event["kind"] for event in completed["events"]],
                ["journal_created", "phase_started", "phase_completed"],
            )
            self.assertEqual(
                journal.complete_phase(PHASES[0], verification=completion),
                completed,
            )
            with self.assertRaisesRegex(CutoverContractError, "differs"):
                journal.complete_phase(
                    PHASES[0],
                    verification=verified_completion(PHASES[0], "6" * 64),
                )

    def test_raw_digest_or_invalid_verifier_completion_cannot_advance(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.journal(directory)
            self.create(journal)
            journal.begin_phase(PHASES[0])
            with self.assertRaises(TypeError):
                journal.complete_phase(  # type: ignore[call-arg]
                    PHASES[0],
                    evidence_sha256="5" * 64,
                )
            with self.assertRaisesRegex(
                CutoverContractError,
                "release-verifier completion",
            ):
                journal.complete_phase(
                    PHASES[0],
                    verification=VerifiedPhaseCompletion(
                        phase=PHASES[0],
                        evidence_sha256="0" * 64,
                        receipt_sha256="e" * 64,
                    ),
                )
            state = journal.load()
            self.assertEqual(state["status"], "phase_started")
            self.assertEqual(state["completed_phases"], [])

    def test_phase_order_and_durable_start_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.journal(directory)
            self.create(journal)
            with self.assertRaisesRegex(CutoverContractError, "out of order"):
                journal.begin_phase(PHASES[1])
            with self.assertRaisesRegex(CutoverContractError, "no matching durable start"):
                journal.complete_phase(
                    PHASES[0],
                    verification=verified_completion(PHASES[0], "5" * 64),
                )

    def test_rollback_is_allowed_before_commit_and_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.journal(directory)
            self.create(journal)
            journal.begin_phase(PHASES[0])
            rolled_back = journal.record_rollback(
                reason="validated legacy rollback completed",
                evidence_sha256="7" * 64,
            )
            self.assertEqual(rolled_back["status"], "rolled_back")
            self.assertFalse(rolled_back["rollback_eligible"])
            self.assertFalse(rolled_back["first_business_write_allowed"])
            self.assertEqual(
                journal.record_rollback(
                    reason="validated legacy rollback completed",
                    evidence_sha256="7" * 64,
                ),
                rolled_back,
            )
            with self.assertRaises(CutoverContractError):
                journal.begin_phase(PHASES[0])

    def test_first_write_commit_is_hard_disabled_without_executor(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.journal(directory)
            self.create(journal)
            ready = self.complete_all(journal)
            self.assertEqual(ready["status"], "ready_for_commit")
            self.assertTrue(ready["rollback_eligible"])
            acceptance = ready["phase_evidence_sha256"][PHASES[-1]]

            with self.assertRaisesRegex(CutoverContractError, "hard-disabled"):
                journal.commit_first_business_write(
                    evidence_sha256=acceptance,
                    confirmation=FIRST_WRITE_COMMIT_CONFIRMATION,
                )
            unchanged = journal.load()
            self.assertEqual(unchanged["status"], "ready_for_commit")
            self.assertFalse(unchanged["first_business_write_allowed"])
            rolled_back = journal.record_rollback(
                reason="executor unavailable",
                evidence_sha256="8" * 64,
            )
            self.assertEqual(rolled_back["status"], "rolled_back")

    def test_create_is_idempotent_but_binding_changes_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.journal(directory)
            created = self.create(journal)
            self.assertEqual(self.create(journal), created)
            self.assertEqual(
                journal.assert_bindings(
                    manifest_sha256="4" * 64,
                    plan_sha256="6" * 64,
                    campaign_id=CAMPAIGN_ID,
                    operation_id=OPERATION_ID,
                    release_sha=RELEASE_SHA,
                    legacy_release_sha=LEGACY_RELEASE_SHA,
                ),
                created,
            )
            with self.assertRaisesRegex(CutoverContractError, "differs"):
                journal.assert_bindings(
                    manifest_sha256="4" * 64,
                    plan_sha256="7" * 64,
                    campaign_id=CAMPAIGN_ID,
                    operation_id=OPERATION_ID,
                    release_sha=RELEASE_SHA,
                    legacy_release_sha=LEGACY_RELEASE_SHA,
                )
            with self.assertRaisesRegex(CutoverContractError, "different bindings"):
                journal.create(
                    manifest_sha256="9" * 64,
                    plan_sha256="6" * 64,
                    campaign_id=CAMPAIGN_ID,
                    operation_id=OPERATION_ID,
                    release_sha=RELEASE_SHA,
                    legacy_release_sha=LEGACY_RELEASE_SHA,
                )

    def test_journal_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.journal(directory)
            self.create(journal)
            payload = json.loads(journal.path.read_text(encoding="utf-8"))
            payload["rollback_eligible"] = False
            journal.path.write_text(json.dumps(payload), encoding="utf-8")
            journal.path.chmod(0o600)
            with self.assertRaisesRegex(CutoverContractError, "schema, identity, or hash"):
                journal.load()

    def test_forged_committed_status_is_not_a_valid_journal_state(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.journal(directory)
            self.create(journal)
            payload = json.loads(journal.path.read_text(encoding="utf-8"))
            payload["status"] = "first_write_committed"
            payload["rollback_eligible"] = False
            payload["first_business_write_allowed"] = True
            from scripts.production_shadow_cutover_controller import _state_hash

            payload["state_sha256"] = _state_hash(payload)
            journal.path.write_text(json.dumps(payload), encoding="utf-8")
            journal.path.chmod(0o600)
            with self.assertRaisesRegex(
                CutoverContractError,
                "schema, identity, or hash",
            ):
                journal.load()

    def test_event_history_reorder_or_rehash_gap_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.journal(directory)
            self.create(journal)
            journal.begin_phase(PHASES[0])
            journal.complete_phase(
                PHASES[0],
                verification=verified_completion(PHASES[0], "5" * 64),
            )
            payload = json.loads(journal.path.read_text(encoding="utf-8"))
            payload["events"][1], payload["events"][2] = (
                payload["events"][2],
                payload["events"][1],
            )
            # Recomputing the outer state hash cannot hide a broken event chain.
            from scripts.production_shadow_cutover_controller import _state_hash

            payload["state_sha256"] = _state_hash(payload)
            journal.path.write_text(json.dumps(payload), encoding="utf-8")
            journal.path.chmod(0o600)
            with self.assertRaisesRegex(CutoverContractError, "event hash chain"):
                journal.load()


if __name__ == "__main__":
    unittest.main()
