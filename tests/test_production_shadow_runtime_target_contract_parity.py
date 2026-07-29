from __future__ import annotations

import copy
import unittest

from scripts import production_shadow_convergence_observer_worker as WORKER
from scripts import production_shadow_convergence_runtime_targets as TARGETS


CAMPAIGN_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2944"
OPERATION_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2945"
RELEASE_SHA = "1ddf277bc51ebe7c9b4d4d488c843efe90fc16e2"
MANIFEST_SHA256 = "7" * 64
COMPOSE_SHA256 = "b" * 64


def runtime_environment(role: str) -> dict[str, str]:
    username = f"{role}_observer"
    database = f"{role}_shadow"
    password = "parity-password"
    return {
        "TZ": "UTC",
        "ENVIRONMENT": "production",
        "TOPOLOGY_SCHEMA_VERSION": "three-site-dr-v1",
        "THREE_SITE_DR_ENABLED": "true",
        "DR_EVENT_PROTOCOL_ENABLED": "true",
        "DR_EVENT_PROTOCOL_STRICT": "true",
        "RELEASE_SHA": RELEASE_SHA,
        "SERVER_MODE": "foreign" if role == "bot_fi" else "iran",
        "LOGICAL_AUTHORITY": "foreign" if role == "bot_fi" else "webapp",
        "PHYSICAL_SITE": role,
        "DATABASE_URL": f"postgresql+asyncpg://{username}:{password}@{role}_db/{database}",
        "SYNC_DATABASE_URL": f"postgresql://{username}:{password}@{role}_db/{database}",
        "POSTGRES_USER": username,
        "POSTGRES_PASSWORD": password,
        "POSTGRES_DB": database,
    }


def target_set() -> dict[str, object]:
    document: dict[str, object] = {
        "schema": TARGETS.CONVERGENCE_RUNTIME_TARGET_SET_SCHEMA,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "canonical_compose_sha256": COMPOSE_SHA256,
        "roles": {
            role: TARGETS.derive_runtime_target_binding(
                runtime_environment(role),
                role=role,
                release_sha=RELEASE_SHA,
            )["runtime_target_row"]
            for role in TARGETS.CONVERGENCE_RUNTIME_TARGET_ROLES
        },
        "target_set_sha256": "0" * 64,
    }
    document["target_set_sha256"] = TARGETS.runtime_target_set_digest(document)
    return document


def target_binding(targets: dict[str, object]) -> dict[str, object]:
    return TARGETS.build_observer_runtime_target_binding(
        campaign_id=CAMPAIGN_ID,
        operation_id=OPERATION_ID,
        release_sha=RELEASE_SHA,
        manifest_sha256=MANIFEST_SHA256,
        canonical_compose_sha256=COMPOSE_SHA256,
        role="bot_fi",
        convergence_runtime_targets=TARGETS.runtime_target_set_descriptor(targets),
        runtime_target_row=targets["roles"]["bot_fi"],  # type: ignore[index]
        role_material_sha256="c" * 64,
        role_runtime_image_ids={
            "app": "sha256:" + "1" * 64,
            "postgres": "sha256:" + "2" * 64,
            "redis": "sha256:" + "3" * 64,
            "nginx": "sha256:" + "4" * 64,
        },
    )


class RuntimeTargetContractParityTests(unittest.TestCase):
    def assert_same_outcome(self, worker_call, target_call) -> None:  # noqa: ANN001
        worker_result = target_result = None
        worker_error = target_error = None
        try:
            worker_result = worker_call()
        except Exception as exc:  # Both contracts deliberately use different error classes.
            worker_error = exc
        try:
            target_result = target_call()
        except Exception as exc:
            target_error = exc
        self.assertEqual(
            worker_error is None,
            target_error is None,
            msg=(f"worker={worker_error!r}; target={target_error!r}"),
        )
        if worker_error is None:
            self.assertEqual(worker_result, target_result)

    def test_environment_derivation_corpus_matches_the_published_contract(self) -> None:
        valid = runtime_environment("bot_fi")
        cases: list[tuple[str, dict[str, str]]] = [("valid", valid)]
        wrong_host = copy.deepcopy(valid)
        wrong_host["DATABASE_URL"] = wrong_host["DATABASE_URL"].replace(
            "@bot_fi_db/", "@127.0.0.1/"
        )
        cases.append(("database-host", wrong_host))
        mismatched_password = copy.deepcopy(valid)
        mismatched_password["SYNC_DATABASE_URL"] = mismatched_password[
            "SYNC_DATABASE_URL"
        ].replace("parity-password", "another-password")
        cases.append(("sync-password", mismatched_password))
        identity_drift = copy.deepcopy(valid)
        identity_drift["PHYSICAL_SITE"] = "webapp_fi"
        cases.append(("identity", identity_drift))
        malformed = copy.deepcopy(valid)
        malformed["DATABASE_URL"] = "postgresql+asyncpg://bad"
        cases.append(("malformed-url", malformed))

        for label, environment in cases:
            with self.subTest(label=label):
                self.assert_same_outcome(
                    lambda: WORKER._derive_runtime_target_binding(  # noqa: SLF001
                        environment, role="bot_fi", release_sha=RELEASE_SHA
                    ),
                    lambda: TARGETS.derive_runtime_target_binding(
                        environment, role="bot_fi", release_sha=RELEASE_SHA
                    ),
                )

    def test_target_set_payload_corpus_matches_the_published_contract(self) -> None:
        valid = target_set()
        descriptor = TARGETS.runtime_target_set_descriptor(valid)
        cases: list[tuple[str, bytes, dict[str, object]]] = [
            ("valid", TARGETS._canonical_json(valid), descriptor),  # noqa: SLF001
        ]
        stale_row = copy.deepcopy(valid)
        stale_row["roles"]["bot_fi"]["runtime_identity_sha256"] = "f" * 64  # type: ignore[index]
        cases.append(("row-digest", TARGETS._canonical_json(stale_row), descriptor))  # noqa: SLF001
        unexpected_role = copy.deepcopy(valid)
        unexpected_role["roles"]["witness"] = copy.deepcopy(  # type: ignore[index]
            unexpected_role["roles"]["bot_fi"]  # type: ignore[index]
        )
        unexpected_role["target_set_sha256"] = TARGETS.runtime_target_set_digest(unexpected_role)
        cases.append(("role-coverage", TARGETS._canonical_json(unexpected_role), TARGETS.runtime_target_set_descriptor(unexpected_role)))  # noqa: SLF001
        noncanonical = TARGETS._canonical_json(valid).replace(b'"schema":', b'"schema" :')  # noqa: SLF001
        cases.append(("noncanonical", noncanonical, descriptor))

        for label, payload, candidate_descriptor in cases:
            with self.subTest(label=label):
                self.assert_same_outcome(
                    lambda: WORKER._validate_runtime_target_payload_descriptor(  # noqa: SLF001
                        payload,
                        candidate_descriptor,
                        operation_id=OPERATION_ID,
                        release_sha=RELEASE_SHA,
                        canonical_compose_sha256=COMPOSE_SHA256,
                        label="parity target set",
                    ),
                    lambda: TARGETS.validate_runtime_target_payload_descriptor(
                        payload,
                        candidate_descriptor,
                        operation_id=OPERATION_ID,
                        release_sha=RELEASE_SHA,
                        canonical_compose_sha256=COMPOSE_SHA256,
                        label="parity target set",
                    ),
                )

    def test_binding_corpus_matches_the_published_contract(self) -> None:
        valid_targets = target_set()
        valid = target_binding(valid_targets)
        cases: list[tuple[str, dict[str, object]]] = [("valid", valid)]
        binding_digest = copy.deepcopy(valid)
        binding_digest["binding_sha256"] = "f" * 64
        cases.append(("binding-digest", binding_digest))
        duplicate_image = copy.deepcopy(valid)
        duplicate_image["role_runtime_image_ids"]["postgres"] = duplicate_image[  # type: ignore[index]
            "role_runtime_image_ids"
        ]["app"]  # type: ignore[index]
        duplicate_image["binding_sha256"] = TARGETS._observer_runtime_target_binding_digest(duplicate_image)  # noqa: SLF001
        cases.append(("image-ids", duplicate_image))
        contract_drift = copy.deepcopy(valid)
        contract_drift["execution_contract"] = "host-observer-v1"
        contract_drift["binding_sha256"] = TARGETS._observer_runtime_target_binding_digest(contract_drift)  # noqa: SLF001
        cases.append(("execution-contract", contract_drift))
        compose_drift = copy.deepcopy(valid)
        compose_drift["canonical_compose_sha256"] = "0" * 64
        compose_drift["binding_sha256"] = TARGETS._observer_runtime_target_binding_digest(compose_drift)  # noqa: SLF001
        cases.append(("canonical-compose", compose_drift))

        for label, candidate in cases:
            with self.subTest(label=label):
                self.assert_same_outcome(
                    lambda: WORKER._validate_observer_runtime_target_binding(  # noqa: SLF001
                        candidate,
                        campaign_id=CAMPAIGN_ID,
                        operation_id=OPERATION_ID,
                        release_sha=RELEASE_SHA,
                        manifest_sha256=MANIFEST_SHA256,
                        role="bot_fi",
                        label="parity binding",
                    ),
                    lambda: TARGETS.validate_observer_runtime_target_binding(
                        candidate,
                        campaign_id=CAMPAIGN_ID,
                        operation_id=OPERATION_ID,
                        release_sha=RELEASE_SHA,
                        manifest_sha256=MANIFEST_SHA256,
                        role="bot_fi",
                        label="parity binding",
                    ),
                )


if __name__ == "__main__":
    unittest.main()
