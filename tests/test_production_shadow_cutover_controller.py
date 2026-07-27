from __future__ import annotations

from datetime import datetime, timezone
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.production_shadow_cutover_controller import (
    APPLY_CONFIRMATION,
    ARTIFACT_FIELDS,
    CutoverContractError,
    EXPECTED_TOPOLOGY,
    FIRST_WRITE_COMMIT_CONFIRMATION,
    MANIFEST_SCHEMA,
    PHASES,
    POLICY_FIELDS,
    ProductionCutoverJournal,
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
EXPECTED_PHASE_ORDER = [
    "pre_freeze_evidence",
    "write_block_install",
    "write_block_nginx_test",
    "write_block_reload",
    "stop_legacy_writers",
    "zero_client_readback",
    "write_block_readback",
    "final_snapshot_hashes",
    "shadow_restore",
    "shadow_migrate",
    "shadow_roles",
    "shadow_fence",
    "witness_lease",
    "convergence_gate",
    "readonly_upstream_switch",
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
            "shadow_compose_project": _shadow_project(CAMPAIGN_ID),
            "shadow_root": str(_shadow_root(CAMPAIGN_ID)),
            "controller_journal_path": str(secure_root / "journal.json"),
            "controller_evidence_root": str(secure_root / "evidence"),
        },
        "artifacts": {
            "release_bundle_sha256": "d" * 64,
            "role_material_sha256": "e" * 64,
            "app_image_id": "sha256:" + "f" * 64,
            "postgres_image_id": "sha256:" + "1" * 64,
            "postgres_image_ref": f"trading_bot_postgres_boottime:15-{RELEASE_SHA}",
            "legacy_bot_rollback_sha256": "2" * 64,
            "legacy_webapp_rollback_sha256": "3" * 64,
            "shadow_compose_sha256": "4" * 64,
            "cutover_approval_sha256": "5" * 64,
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
        self.assertTrue(gate["irreversible_boundary"])
        self.assertEqual(gate["required_completed_phase"], PHASES[-1])
        self.assertEqual(
            gate["required_confirmation"], FIRST_WRITE_COMMIT_CONFIRMATION
        )
        self.assertIn(APPLY_CONFIRMATION, gate["argv_template"])
        gaps = {item["component"]: item["status"] for item in plan["operational_gaps"]}
        self.assertEqual(gaps["first-business-write-executor"], "intentionally-absent")
        self.assertEqual(gaps["phase-evidence-schema-verifiers"], "missing")

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
        self.assertTrue(all(isinstance(command["argv"], list) for command in all_commands))
        self.assertTrue(all(command["render_only"] for command in all_commands))
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
        for command in all_commands:
            if command["role"] in {"webapp_ir", "witness"}:
                self.assertEqual(
                    command["payload_transfer"],
                    "object-storage-private-versioned-age",
                )
                self.assertIn("object-storage-private-versioned-age", command["argv"])

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
                evidence_sha256=f"{index:064x}",
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
            completed = journal.complete_phase(PHASES[0], evidence_sha256=digest)
            self.assertEqual(completed["status"], "active")
            self.assertEqual(completed["completed_phases"], [PHASES[0]])
            self.assertEqual(
                [event["kind"] for event in completed["events"]],
                ["journal_created", "phase_started", "phase_completed"],
            )
            self.assertEqual(
                journal.complete_phase(PHASES[0], evidence_sha256=digest),
                completed,
            )
            with self.assertRaisesRegex(CutoverContractError, "differs"):
                journal.complete_phase(PHASES[0], evidence_sha256="6" * 64)

    def test_phase_order_and_durable_start_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.journal(directory)
            self.create(journal)
            with self.assertRaisesRegex(CutoverContractError, "out of order"):
                journal.begin_phase(PHASES[1])
            with self.assertRaisesRegex(CutoverContractError, "no matching durable start"):
                journal.complete_phase(PHASES[0], evidence_sha256="5" * 64)

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

    def test_first_write_commit_is_explicit_and_prohibits_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.journal(directory)
            self.create(journal)
            ready = self.complete_all(journal)
            self.assertEqual(ready["status"], "ready_for_commit")
            self.assertTrue(ready["rollback_eligible"])
            acceptance = ready["phase_evidence_sha256"][PHASES[-1]]

            with self.assertRaisesRegex(CutoverContractError, "confirmation"):
                journal.commit_first_business_write(
                    evidence_sha256=acceptance,
                    confirmation="wrong",
                )
            with self.assertRaisesRegex(CutoverContractError, "must equal"):
                journal.commit_first_business_write(
                    evidence_sha256="f" * 64,
                    confirmation=FIRST_WRITE_COMMIT_CONFIRMATION,
                )
            committed = journal.commit_first_business_write(
                evidence_sha256=acceptance,
                confirmation=FIRST_WRITE_COMMIT_CONFIRMATION,
            )
            self.assertEqual(committed["status"], "first_write_committed")
            self.assertTrue(committed["first_business_write_allowed"])
            self.assertFalse(committed["rollback_eligible"])
            self.assertEqual(
                journal.commit_first_business_write(
                    evidence_sha256=acceptance,
                    confirmation=FIRST_WRITE_COMMIT_CONFIRMATION,
                ),
                committed,
            )
            with self.assertRaisesRegex(CutoverContractError, "prohibited"):
                journal.record_rollback(
                    reason="too late",
                    evidence_sha256="8" * 64,
                )

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

    def test_event_history_reorder_or_rehash_gap_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.journal(directory)
            self.create(journal)
            journal.begin_phase(PHASES[0])
            journal.complete_phase(PHASES[0], evidence_sha256="5" * 64)
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
