from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from scripts.coordinate_three_site_staging_migration import (
    ACCEPTANCE_CHECKS,
    MigrationCoordinationError,
    REQUIRED_PHASE,
    build_documents,
)
from scripts.three_site_staging_migration_journal import MigrationJournal, ROLE_PHASES


CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
RELEASE_SHA = "a" * 40
PLAN_SHA = "b" * 64


def _journals(root: Path) -> tuple[dict[str, MigrationJournal], dict[str, dict]]:
    objects = {}
    for role in ROLE_PHASES:
        journal = MigrationJournal(root / f"{role}.json")
        journal.create(
            campaign_id=CAMPAIGN_ID,
            release_sha=RELEASE_SHA,
            plan_sha256=PLAN_SHA,
            role=role,
            role_compose_sha256="c" * 64,
            role_env_sha256="d" * 64,
        )
        objects[role] = journal
    return objects, {role: journal.load() for role, journal in objects.items()}


def _advance(journal: MigrationJournal, through: str) -> None:
    for phase in ROLE_PHASES[journal.load()["role"]]:
        journal.begin_phase(phase)
        journal.complete_phase(phase)
        if phase == through:
            return


def _states(journals: dict[str, MigrationJournal]) -> dict[str, dict]:
    return {role: journal.load() for role, journal in journals.items()}


def _secure_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value))
    path.chmod(0o600)


class ThreeSiteStagingMigrationCoordinationTests(unittest.TestCase):
    def test_private_barrier_requires_all_cross_role_preconditions(self):
        with tempfile.TemporaryDirectory() as directory:
            journals, states = _journals(Path(directory))
            with self.assertRaisesRegex(MigrationCoordinationError, "waiting"):
                build_documents(action="private-barrier", journals=states)
            for role, phase in REQUIRED_PHASE["private-barrier"].items():
                _advance(journals[role], phase)
            documents = build_documents(
                action="private-barrier", journals=_states(journals)
            )
            self.assertEqual(set(documents), {"bot_fi", "webapp_fi", "webapp_ir"})
            self.assertTrue(
                all(
                    document["schema"] == "three-site-staging-private-barrier-v1"
                    for document in documents.values()
                )
            )

    def test_routing_hold_rejects_stale_or_changed_route_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journals, _states_initial = _journals(root)
            for role, phase in REQUIRED_PHASE["routing-hold"].items():
                _advance(journals[role], phase)
            observation = root / "routing.json"
            _secure_json(
                observation,
                {
                    "schema": "three-site-staging-routing-observation-v1",
                    "campaign_id": CAMPAIGN_ID,
                    "release_sha": RELEASE_SHA,
                    "plan_sha256": PLAN_SHA,
                    "observed_at": (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(),
                    "domain": "gold-trading.ir",
                    "record": "app",
                    "current_origin_ip": "10.30.0.9",
                    "expected_legacy_origin_ip": "10.30.0.9",
                    "routing_held": True,
                    "change_applied": False,
                    "initial_convergence": True,
                    "event_checkpoint_evidence_sha256": "a" * 64,
                    "database_parity_evidence_sha256": "b" * 64,
                    "blob_parity_evidence_sha256": "c" * 64,
                },
            )
            with self.assertRaisesRegex(MigrationCoordinationError, "stale"):
                build_documents(
                    action="routing-hold",
                    journals=_states(journals),
                    routing_observation=observation,
                )
            payload = json.loads(observation.read_text())
            payload["observed_at"] = datetime.now(timezone.utc).isoformat()
            payload["change_applied"] = True
            _secure_json(observation, payload)
            with self.assertRaisesRegex(MigrationCoordinationError, "invalid"):
                build_documents(
                    action="routing-hold",
                    journals=_states(journals),
                    routing_observation=observation,
                )

    def test_acceptance_and_global_commit_are_four_role_barriers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journals, _states_initial = _journals(root)
            for role, phase in REQUIRED_PHASE["role-acceptance"].items():
                _advance(journals[role], phase)
            observations = []
            for role in ROLE_PHASES:
                path = root / f"accept-{role}.json"
                _secure_json(
                    path,
                    {
                        "schema": "three-site-staging-role-observation-v1",
                        "campaign_id": CAMPAIGN_ID,
                        "release_sha": RELEASE_SHA,
                        "plan_sha256": PLAN_SHA,
                        "role": role,
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                        "checks": {name: True for name in ACCEPTANCE_CHECKS},
                    },
                )
                observations.append(f"{role}={path}")
            documents = build_documents(
                action="role-acceptance",
                journals=_states(journals),
                acceptance_observations=observations,
            )
            self.assertEqual(set(documents), set(ROLE_PHASES))
            for role, journal in journals.items():
                journal.begin_phase("accepted")
                journal.complete_phase("accepted")
                journal.commit(acceptance_evidence_sha256="e" * 64)
            commit = build_documents(
                action="global-commit", journals=_states(journals)
            )["global-commit"]
            self.assertTrue(commit["all_roles_committed"])
            self.assertEqual(set(commit["role_journals"]), set(ROLE_PHASES))


if __name__ == "__main__":
    unittest.main()
