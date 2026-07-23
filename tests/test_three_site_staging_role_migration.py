from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scripts import run_three_site_staging_role_migration as role_migration
from scripts.run_three_site_staging_role_migration import (
    LocalRoleBackend,
    RoleMigrationError,
    _secure_json,
    apply_action,
    main,
)
from scripts.three_site_staging_migration_journal import MigrationJournal


CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
RELEASE_SHA = "a" * 40
PLAN_SHA = "b" * 64
COMPOSE_SHA = "c" * 64
ENV_SHA = "d" * 64


class _Backend:
    def __init__(self, role: str, *, fail: str | None = None):
        self.role = role
        self.fail = fail
        self.calls: list[str] = []

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if self.fail == name:
            raise RuntimeError(f"failed {name}")

    def restore_seed(self) -> None:
        self._call("restore_seed")

    def configure_database(self) -> None:
        self._call("configure_database")

    def start_private(self) -> None:
        self._call("start_private")

    def start_workers(self) -> None:
        self._call("start_workers")

    def start_public(self) -> None:
        self._call("start_public")

    def attest_writer_state(self) -> dict:
        self._call("attest_writer_state")
        return {
            "active_site": "webapp_fi",
            "writer_epoch": 1,
            "control_state": "active",
        }


def _journal(path: Path, role: str) -> MigrationJournal:
    journal = MigrationJournal(path)
    journal.create(
        campaign_id=CAMPAIGN_ID,
        release_sha=RELEASE_SHA,
        plan_sha256=PLAN_SHA,
        role=role,
        role_compose_sha256=COMPOSE_SHA,
        role_env_sha256=ENV_SHA,
        image_inventory_sha256="e" * 64,
    )
    return journal


def _context() -> dict:
    return {
        "verified_plan": {
            "campaign_id": CAMPAIGN_ID,
            "release_sha": RELEASE_SHA,
            "plan_sha256": PLAN_SHA,
        }
    }


def _write_evidence(
    path: Path,
    *,
    schema: str,
    role: str,
    journal: MigrationJournal,
) -> None:
    extra = {"campaign_journals_sha256": "e" * 64}
    if schema == "three-site-staging-routing-hold-v1":
        extra["routing_observation_sha256"] = "f" * 64
    elif schema == "three-site-staging-role-acceptance-v1":
        extra["acceptance_observation_sha256"] = "f" * 64
    path.write_text(
        json.dumps(
            {
                "schema": schema,
                "status": "passed",
                "campaign_id": CAMPAIGN_ID,
                "release_sha": RELEASE_SHA,
                "plan_sha256": PLAN_SHA,
                "role": role,
                "issued_at": datetime.now(timezone.utc).isoformat(),
                "role_journal_state_sha256": journal.load()["state_sha256"],
                **extra,
            }
        )
    )
    path.chmod(0o600)


class ThreeSiteStagingRoleMigrationTests(unittest.TestCase):
    @staticmethod
    def _local_backend() -> LocalRoleBackend:
        backend = object.__new__(LocalRoleBackend)
        backend.prefix = ["docker", "compose"]
        backend.db_service = "webapp_fi_db"
        backend._psql = MagicMock(return_value="0")
        return backend

    def test_database_migration_quiescence_rejects_running_application_service(self):
        backend = self._local_backend()

        def fake_run(arguments, **_kwargs):
            if arguments[-2:] == ["config", "--services"]:
                return "webapp_fi_db\nwebapp_fi_redis\nwebapp_fi_api\nwebapp_fi_migration"
            if arguments[-3:] == ["ps", "-q", "webapp_fi_api"]:
                return "running-container-id"
            return ""

        with patch.object(role_migration, "_run", side_effect=fake_run):
            with self.assertRaisesRegex(RoleMigrationError, "service to be stopped"):
                backend._assert_database_migration_quiescent()
        backend._psql.assert_not_called()

    def test_database_migration_quiescence_requires_stable_zero_clients(self):
        backend = self._local_backend()

        def fake_run(arguments, **_kwargs):
            if arguments[-2:] == ["config", "--services"]:
                return "webapp_fi_db\nwebapp_fi_redis\nwebapp_fi_api\nwebapp_fi_migration"
            return ""

        with patch.object(role_migration, "_run", side_effect=fake_run), patch.object(
            role_migration.time, "sleep"
        ):
            backend._assert_database_migration_quiescent()
        self.assertEqual(backend._psql.call_count, 3)

        backend._psql.reset_mock()
        backend._psql.return_value = "1"
        with patch.object(role_migration, "_run", side_effect=fake_run):
            with self.assertRaisesRegex(RoleMigrationError, "zero other client sessions"):
                backend._assert_database_migration_quiescent()

    def test_private_start_waits_for_app_release_and_tls_health_on_every_role(self):
        for role in ("bot_fi", "webapp_fi", "webapp_ir", "witness"):
            with self.subTest(role=role):
                backend = object.__new__(LocalRoleBackend)
                backend.role = role
                backend.prefix = ["docker", "compose"]
                backend._wait_services_ready = MagicMock()
                backend._wait_infrastructure_ready = MagicMock()
                with patch.object(role_migration, "_run", return_value=""):
                    backend.start_private()
                backend._wait_services_ready.assert_called_once_with(
                    role_migration.ROLE_PRIVATE[role][:-1]
                )
                backend._wait_infrastructure_ready.assert_called_once_with(
                    role_migration.ROLE_PRIVATE[role][-1:]
                )

    def test_webapp_role_requires_ordered_external_barriers_and_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = _journal(root / "role.json", "webapp_fi")
            backend = _Backend("webapp_fi")
            context = _context()
            apply_action(
                action="restore-seed", journal=journal, backend=backend,
                context=context, evidence_path=None,
            )
            apply_action(
                action="configure-database", journal=journal, backend=backend,
                context=context, evidence_path=None,
            )
            apply_action(
                action="start-private", journal=journal, backend=backend,
                context=context, evidence_path=None,
            )
            apply_action(
                action="attest-writer-state", journal=journal, backend=backend,
                context=context, evidence_path=None,
            )
            barrier = root / "barrier.json"
            _write_evidence(
                barrier,
                schema="three-site-staging-private-barrier-v1",
                role="webapp_fi",
                journal=journal,
            )
            apply_action(
                action="start-workers", journal=journal, backend=backend,
                context=context, evidence_path=barrier,
            )
            hold = root / "hold.json"
            _write_evidence(
                hold,
                schema="three-site-staging-routing-hold-v1",
                role="webapp_fi",
                journal=journal,
            )
            apply_action(
                action="start-public", journal=journal, backend=backend,
                context=context, evidence_path=hold,
            )
            acceptance = root / "acceptance.json"
            _write_evidence(
                acceptance,
                schema="three-site-staging-role-acceptance-v1",
                role="webapp_fi",
                journal=journal,
            )
            committed = apply_action(
                action="accept", journal=journal, backend=backend,
                context=context, evidence_path=acceptance,
            )
            self.assertEqual(committed["status"], "committed")
            self.assertEqual(
                backend.calls,
                [
                    "restore_seed", "configure_database", "start_private", "attest_writer_state",
                    "start_workers", "start_public",
                ],
            )
            committed_states = {"webapp_fi": committed}
            from scripts.three_site_staging_migration_journal import ROLE_PHASES
            for other_role in ("bot_fi", "webapp_ir", "witness"):
                other = _journal(root / f"{other_role}.json", other_role)
                for phase in ROLE_PHASES[other_role]:
                    other.begin_phase(phase)
                    other.complete_phase(phase)
                committed_states[other_role] = other.commit(
                    acceptance_evidence_sha256="9" * 64
                )
            role_journals = {
                role: state["state_sha256"] for role, state in committed_states.items()
            }
            global_commit = root / "global-commit.json"
            global_commit.write_text(
                json.dumps(
                    {
                        "schema": "three-site-staging-global-commit-v2",
                        "status": "passed",
                        "campaign_id": CAMPAIGN_ID,
                        "release_sha": RELEASE_SHA,
                        "plan_sha256": PLAN_SHA,
                        "issued_at": datetime.now(timezone.utc).isoformat(),
                        "campaign_journals_sha256": hashlib.sha256(
                            json.dumps(
                                role_journals, sort_keys=True, separators=(",", ":")
                            ).encode()
                        ).hexdigest(),
                        "role_journals": role_journals,
                        "committed_role_states": committed_states,
                        "all_roles_committed": True,
                    }
                )
            )
            global_commit.chmod(0o600)
            self.assertEqual(
                main(
                    [
                        "finish", "--role", "webapp_fi", "--journal", str(journal.path),
                        "--evidence", str(global_commit),
                    ]
                ),
                0,
            )
            self.assertEqual(journal.load()["status"], "finished")

    def test_failed_phase_is_not_forward_resumable(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = _journal(Path(directory) / "role.json", "bot_fi")
            backend = _Backend("bot_fi", fail="restore_seed")
            with self.assertRaisesRegex(RuntimeError, "failed restore_seed"):
                apply_action(
                    action="restore-seed", journal=journal, backend=backend,
                    context=_context(), evidence_path=None,
                )
            self.assertEqual(journal.load()["status"], "rollback_required")
            with self.assertRaisesRegex(Exception, "current state"):
                apply_action(
                    action="restore-seed", journal=journal, backend=_Backend("bot_fi"),
                    context=_context(), evidence_path=None,
                )

    def test_sensitive_json_rejects_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"schema":"one","schema":"two"}')
            path.chmod(0o600)
            with self.assertRaisesRegex(RoleMigrationError, "duplicate key"):
                _secure_json(path, label="duplicate")

    def test_status_needs_only_role_and_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "role.json"
            _journal(path, "witness")
            self.assertEqual(
                main(["status", "--role", "witness", "--journal", str(path)]),
                0,
            )

    def test_rollback_rejects_bundle_not_bound_to_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "role.json"
            _journal(path, "witness")
            compose = root / "compose.yml"
            compose.write_text("name: different\n")
            compose.chmod(0o640)
            env = root / "role.env"
            env.write_text("WITNESS_POSTGRES_USER=witness\nWITNESS_POSTGRES_DB=witness\n")
            env.chmod(0o600)
            self.assertNotEqual(hashlib.sha256(compose.read_bytes()).hexdigest(), COMPOSE_SHA)
            self.assertEqual(
                main(
                    [
                        "rollback", "--role", "witness", "--journal", str(path),
                        "--role-compose", str(compose), "--env-file", str(env),
                    ]
                ),
                1,
            )


if __name__ == "__main__":
    unittest.main()
