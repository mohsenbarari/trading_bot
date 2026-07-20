from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import hashlib
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


def _check(observation: dict) -> dict:
    return {
        "status": "passed",
        "observation": observation,
        "observation_sha256": hashlib.sha256(
            json.dumps(observation, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def _routing(root: Path, *, observed_at: str) -> Path:
    artifacts = {}
    for name, status in (
        ("event_checkpoint", "converged"),
        ("database_parity", "equivalent"),
        ("blob_parity", "passed"),
    ):
        path = root / f"{name}.json"
        payload = {"schema": f"test-{name}-v1", "status": status}
        _secure_json(path, payload)
        artifacts[name] = {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "schema": payload["schema"],
            "status": status,
        }
    path = root / "routing.json"
    _secure_json(
        path,
        {
            "schema": "three-site-staging-routing-observation-v2",
            "campaign_id": CAMPAIGN_ID,
            "release_sha": RELEASE_SHA,
            "plan_sha256": PLAN_SHA,
            "observed_at": observed_at,
            "domain": "gold-trading.ir",
            "record": "app",
            "current_origin_ip": "10.30.0.9",
            "expected_legacy_origin_ip": "10.30.0.9",
            "provider_read_sha256": "a" * 64,
            "artifacts": artifacts,
        },
    )
    return path


def _acceptance(root: Path, *, role: str, routing: Path) -> Path:
    revision = "002" if role == "witness" else "d542e3f4a6b7"
    observations = {
        "database_identity": {"role": role},
        "migration_head": {"observed": revision, "expected": revision},
        "private_tls": {"verified": True},
        "service_health": {
            "services": [
                {
                    "name": f"{role}-service",
                    "running": True,
                    "health": "healthy",
                    "restart_count": 0,
                    "image_id": "sha256:" + "1" * 64,
                }
            ]
        },
        "direct_origin_http": {"status_code": 200},
        "production_boundaries_untouched": {
            "routing_change_applied": False,
            "test_domain": "gold-trading.ir",
        },
        "unexpected_errors_absent": {"restart_count_total": 0},
        "queue_owner_legacy": {"producer_mode": "legacy", "executor_mode": "legacy"},
        "routing_still_held": {"verified": True},
    }
    path = root / f"accept-{role}.json"
    _secure_json(
        path,
        {
            "schema": "three-site-staging-role-observation-v2",
            "campaign_id": CAMPAIGN_ID,
            "release_sha": RELEASE_SHA,
            "plan_sha256": PLAN_SHA,
            "role": role,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "collector": "collect_three_site_staging_migration_observation.py",
            "checks": {name: _check(observations[name]) for name in ACCEPTANCE_CHECKS},
            "routing_observation": {
                "path": str(routing),
                "sha256": hashlib.sha256(routing.read_bytes()).hexdigest(),
            },
        },
    )
    return path


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
            observation = _routing(
                root,
                observed_at=(datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(),
            )
            with self.assertRaisesRegex(MigrationCoordinationError, "stale"):
                build_documents(
                    action="routing-hold",
                    journals=_states(journals),
                    routing_observation=observation,
                )
            payload = json.loads(observation.read_text())
            payload["observed_at"] = datetime.now(timezone.utc).isoformat()
            payload["current_origin_ip"] = "10.30.0.10"
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
            routing = _routing(root, observed_at=datetime.now(timezone.utc).isoformat())
            for role in ROLE_PHASES:
                path = _acceptance(root, role=role, routing=routing)
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
