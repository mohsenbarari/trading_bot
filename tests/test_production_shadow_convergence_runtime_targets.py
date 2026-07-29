from __future__ import annotations

import copy
import json
import unittest

from scripts import production_shadow_convergence_runtime_targets as MODULE


def descriptor() -> dict[str, object]:
    return {
        "schema": MODULE.CONVERGENCE_RUNTIME_TARGET_SET_SCHEMA,
        "filename": MODULE.CONVERGENCE_RUNTIME_TARGETS_FILENAME,
        "sha256": "a" * 64,
        "bytes": 1024,
        "target_set_sha256": "b" * 64,
        "roles": list(MODULE.CONVERGENCE_RUNTIME_TARGET_ROLES),
    }


def runtime_environment(*, role: str = "bot_fi") -> dict[str, str]:
    release_sha = "a" * 40
    return {
        "TZ": "UTC",
        "ENVIRONMENT": "production",
        "TOPOLOGY_SCHEMA_VERSION": "three-site-dr-v1",
        "THREE_SITE_DR_ENABLED": "true",
        "DR_EVENT_PROTOCOL_ENABLED": "true",
        "DR_EVENT_PROTOCOL_STRICT": "true",
        "RELEASE_SHA": release_sha,
        "SERVER_MODE": "foreign" if role == "bot_fi" else "iran",
        "LOGICAL_AUTHORITY": "foreign" if role == "bot_fi" else "webapp",
        "PHYSICAL_SITE": role,
        "DATABASE_URL": (
            f"postgresql+asyncpg://{role}_observer:secret@{role}_db/{role}_shadow"
        ),
        "SYNC_DATABASE_URL": (
            f"postgresql://{role}_observer:secret@{role}_db/{role}_shadow"
        ),
        "POSTGRES_USER": f"{role}_observer",
        "POSTGRES_PASSWORD": "secret",
        "POSTGRES_DB": f"{role}_shadow",
    }


def target_set() -> dict[str, object]:
    document: dict[str, object] = {
        "schema": MODULE.CONVERGENCE_RUNTIME_TARGET_SET_SCHEMA,
        "operation_id": "11111111-1111-4111-8111-111111111111",
        "release_sha": "a" * 40,
        "canonical_compose_sha256": "b" * 64,
        "roles": {
            role: MODULE.derive_runtime_target_binding(
                runtime_environment(role=role),
                role=role,
                release_sha="a" * 40,
            )["runtime_target_row"]
            for role in MODULE.CONVERGENCE_RUNTIME_TARGET_ROLES
        },
        "target_set_sha256": "0" * 64,
    }
    document["target_set_sha256"] = MODULE.runtime_target_set_digest(document)
    return document


def runtime_image_ids() -> dict[str, str]:
    return {
        "app": "sha256:" + "1" * 64,
        "postgres": "sha256:" + "2" * 64,
        "redis": "sha256:" + "3" * 64,
        "nginx": "sha256:" + "4" * 64,
    }


def canonical_observer_compose(*, role: str) -> dict[str, object]:
    shape = MODULE.observer_service_shape(role=role)
    return {
        "services": {
            shape["service"]: {
                "profiles": shape["profiles"],
                "restart": shape["restart"],
                "command": shape["command"],
                "depends_on": {
                    f"{role}_db": {"condition": "service_healthy"}
                },
                "networks": shape["networks"],
                "environment": {},
            }
        },
        "networks": {
            role: {
                "labels": dict(MODULE.OBSERVER_OPERATION_NETWORK_LABELS),
                "internal": True,
            }
        },
    }


class ConvergenceRuntimeTargetDescriptorTests(unittest.TestCase):
    def test_descriptor_and_inert_capability_are_exact(self) -> None:
        observed = descriptor()
        self.assertEqual(
            MODULE.validate_runtime_target_descriptor(
                observed,
                label="fixture descriptor",
            ),
            observed,
        )
        self.assertEqual(
            MODULE.validate_runtime_target_capabilities(
                list(MODULE.RUNTIME_TARGET_CAPABILITIES),
                label="fixture capabilities",
            ),
            list(MODULE.RUNTIME_TARGET_CAPABILITIES),
        )

    def test_descriptor_rejects_witness_or_any_shape_drift(self) -> None:
        cases: list[dict[str, object]] = []

        with_witness = copy.deepcopy(descriptor())
        with_witness["roles"] = [
            *MODULE.CONVERGENCE_RUNTIME_TARGET_ROLES,
            "witness",
        ]
        cases.append(with_witness)

        reordered = copy.deepcopy(descriptor())
        reordered["roles"] = list(
            reversed(MODULE.CONVERGENCE_RUNTIME_TARGET_ROLES)
        )
        cases.append(reordered)

        zero = copy.deepcopy(descriptor())
        zero["sha256"] = "0" * 64
        cases.append(zero)

        oversized = copy.deepcopy(descriptor())
        oversized["bytes"] = MODULE.MAX_CONVERGENCE_RUNTIME_TARGET_BYTES + 1
        cases.append(oversized)

        extra = copy.deepcopy(descriptor())
        extra["runtime_proof"] = "not-allowed"
        cases.append(extra)

        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(MODULE.ConvergenceRuntimeTargetDescriptorError):
                    MODULE.validate_runtime_target_descriptor(
                        value,
                        label="fixture descriptor",
                    )

    def test_capability_does_not_accept_activation_or_empty_set(self) -> None:
        for value in (
            [],
            ["convergence-runtime-target-descriptor-active-v1"],
            [
                MODULE.RUNTIME_TARGET_DESCRIPTOR_CAPABILITY,
                "convergence-runtime-target-observer-v1",
            ],
        ):
            with self.subTest(value=value):
                with self.assertRaises(MODULE.ConvergenceRuntimeTargetDescriptorError):
                    MODULE.validate_runtime_target_capabilities(
                        value,
                        label="fixture capabilities",
                    )

    def test_runtime_binding_derives_only_nonsecret_compose_target_digests(self) -> None:
        environment = runtime_environment()
        binding = MODULE.derive_runtime_target_binding(
            environment,
            role="bot_fi",
            release_sha="a" * 40,
        )
        self.assertEqual(set(binding), MODULE.CONVERGENCE_RUNTIME_BINDING_FIELDS)
        self.assertEqual(
            set(binding["runtime_target_row"]),
            MODULE.CONVERGENCE_RUNTIME_TARGET_ROLE_FIELDS,
        )
        serialized = json.dumps(binding, sort_keys=True)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("bot_fi_shadow", serialized)
        password_changed = dict(environment)
        password_changed["DATABASE_URL"] = password_changed["DATABASE_URL"].replace(
            ":secret@", ":other-secret@"
        )
        password_changed["SYNC_DATABASE_URL"] = password_changed["SYNC_DATABASE_URL"].replace(
            ":secret@", ":other-secret@"
        )
        password_changed["POSTGRES_PASSWORD"] = "other-secret"
        self.assertEqual(
            MODULE.derive_runtime_target_binding(
                password_changed,
                role="bot_fi",
                release_sha="a" * 40,
            ),
            binding,
        )

    def test_runtime_binding_rejects_host_or_identity_drift(self) -> None:
        host_drift = runtime_environment()
        host_drift["DATABASE_URL"] = host_drift["DATABASE_URL"].replace(
            "@bot_fi_db/", "@127.0.0.1/"
        )
        identity_drift = runtime_environment()
        identity_drift["PHYSICAL_SITE"] = "webapp_fi"
        for environment in (host_drift, identity_drift):
            with self.subTest(environment=environment):
                with self.assertRaises(MODULE.ConvergenceRuntimeTargetBindingError):
                    MODULE.derive_runtime_target_binding(
                        environment,
                        role="bot_fi",
                        release_sha="a" * 40,
                    )

    def test_canonical_compose_observer_shape_is_required_by_target_binding(self) -> None:
        for role in MODULE.CONVERGENCE_RUNTIME_TARGET_ROLES:
            with self.subTest(role=role):
                compose = canonical_observer_compose(role=role)
                shape = MODULE.validate_canonical_observer_service(
                    compose,
                    role=role,
                    label="fixture canonical Compose",
                )
                self.assertEqual(shape, MODULE.observer_service_shape(role=role))
                self.assertEqual(
                    MODULE.derive_runtime_target_binding(
                        runtime_environment(role=role),
                        role=role,
                        release_sha="a" * 40,
                        observer_service=shape,
                    ),
                    MODULE.derive_runtime_target_binding(
                        runtime_environment(role=role),
                        role=role,
                        release_sha="a" * 40,
                    ),
                )

    def test_canonical_compose_observer_rejects_static_contract_drift(self) -> None:
        cases: list[dict[str, object]] = []

        profile = canonical_observer_compose(role="bot_fi")
        profile["services"]["bot_fi_sync_observer"]["profiles"] = ["bot-fi-private"]  # type: ignore[index]
        cases.append(profile)

        restart = canonical_observer_compose(role="bot_fi")
        restart["services"]["bot_fi_sync_observer"]["restart"] = "unless-stopped"  # type: ignore[index]
        cases.append(restart)

        command = canonical_observer_compose(role="bot_fi")
        command["services"]["bot_fi_sync_observer"]["command"] = ["python", "-m", "unsafe"]  # type: ignore[index]
        cases.append(command)

        dependency = canonical_observer_compose(role="bot_fi")
        dependency["services"]["bot_fi_sync_observer"]["depends_on"] = {  # type: ignore[index]
            "bot_fi_db": {"condition": "service_started"}
        }
        cases.append(dependency)

        network = canonical_observer_compose(role="bot_fi")
        network["networks"]["bot_fi"]["internal"] = False  # type: ignore[index]
        cases.append(network)

        for compose in cases:
            with self.subTest(compose=compose):
                with self.assertRaisesRegex(
                    MODULE.ConvergenceRuntimeTargetBindingError,
                    "observer service definition differs",
                ):
                    MODULE.validate_canonical_observer_service(
                        compose,
                        role="bot_fi",
                        label="fixture canonical Compose",
                    )

    def test_target_set_payload_reopens_exact_descriptor(self) -> None:
        document = target_set()
        descriptor = MODULE.runtime_target_set_descriptor(document)
        payload = MODULE._canonical_json(document)
        self.assertEqual(
            MODULE.validate_runtime_target_payload_descriptor(
                payload,
                descriptor,
                operation_id="11111111-1111-4111-8111-111111111111",
                release_sha="a" * 40,
                canonical_compose_sha256="b" * 64,
                label="fixture target set",
            ),
            document,
        )
        forged = copy.deepcopy(document)
        forged["roles"]["bot_fi"]["async_database_target_sha256"] = "c" * 64
        forged["target_set_sha256"] = MODULE.runtime_target_set_digest(forged)
        with self.assertRaises(MODULE.ConvergenceRuntimeTargetBindingError):
            MODULE.validate_runtime_target_payload_descriptor(
                MODULE._canonical_json(forged),
                descriptor,
                operation_id="11111111-1111-4111-8111-111111111111",
                release_sha="a" * 40,
                canonical_compose_sha256="b" * 64,
                label="fixture target set",
            )

    def test_role_binding_is_nonsecret_and_exactly_request_bound(self) -> None:
        document = target_set()
        descriptor = MODULE.runtime_target_set_descriptor(document)
        binding = MODULE.build_observer_runtime_target_binding(
            campaign_id="22222222-2222-4222-8222-222222222222",
            operation_id="11111111-1111-4111-8111-111111111111",
            release_sha="a" * 40,
            manifest_sha256="c" * 64,
            canonical_compose_sha256="b" * 64,
            role="bot_fi",
            convergence_runtime_targets=descriptor,
            runtime_target_row=document["roles"]["bot_fi"],
            role_material_sha256="d" * 64,
            role_runtime_image_ids=runtime_image_ids(),
        )
        self.assertEqual(
            MODULE.validate_observer_runtime_target_binding(
                binding,
                campaign_id="22222222-2222-4222-8222-222222222222",
                operation_id="11111111-1111-4111-8111-111111111111",
                release_sha="a" * 40,
                manifest_sha256="c" * 64,
                role="bot_fi",
                label="fixture role binding",
            ),
            binding,
        )
        serialized = json.dumps(binding, sort_keys=True)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("bot_fi_shadow", serialized)
        forged = copy.deepcopy(binding)
        forged["runtime_target_row"]["runtime_identity_sha256"] = "d" * 64
        forged["binding_sha256"] = MODULE._observer_runtime_target_binding_digest(forged)
        with self.assertRaises(MODULE.ConvergenceRuntimeTargetBindingError):
            MODULE.validate_observer_runtime_target_binding(
                forged,
                campaign_id="22222222-2222-4222-8222-222222222222",
                operation_id="11111111-1111-4111-8111-111111111111",
                release_sha="a" * 40,
                manifest_sha256="c" * 64,
                role="bot_fi",
                label="fixture role binding",
            )
