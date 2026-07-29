from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import unittest

from scripts import production_shadow_convergence_compose_execution as MODULE
from scripts import production_shadow_convergence_runtime_targets as TARGETS


CAMPAIGN_ID = "22222222-2222-4222-8222-222222222222"
OPERATION_ID = "11111111-1111-4111-8111-111111111111"
RELEASE_SHA = "a" * 40
MANIFEST_SHA256 = "b" * 64

def image_ids() -> dict[str, str]:
    return {
        "app": "sha256:" + "1" * 64,
        "postgres": "sha256:" + "2" * 64,
        "redis": "sha256:" + "3" * 64,
        "nginx": "sha256:" + "4" * 64,
    }


def runtime_environment(*, role: str) -> dict[str, str]:
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
        "DATABASE_URL": f"postgresql+asyncpg://{role}_observer:secret@{role}_db/{role}_shadow",
        "SYNC_DATABASE_URL": f"postgresql://{role}_observer:secret@{role}_db/{role}_shadow",
        "POSTGRES_USER": f"{role}_observer",
        "POSTGRES_PASSWORD": "secret",
        "POSTGRES_DB": f"{role}_shadow",
    }


def canonical_compose(*, role: str) -> dict[str, object]:
    shape = TARGETS.observer_service_shape(role=role)
    return {
        "services": {
            shape["service"]: {
                "profiles": shape["profiles"],
                "restart": shape["restart"],
                "command": shape["command"],
                "depends_on": {f"{role}_db": {"condition": "service_healthy"}},
                "networks": shape["networks"],
            }
        },
        "networks": {
            role: {
                "labels": dict(TARGETS.OBSERVER_OPERATION_NETWORK_LABELS),
                "internal": True,
            }
        },
    }


def binding(*, role: str, compose: dict[str, object]) -> dict[str, object]:
    row = TARGETS.derive_runtime_target_binding(
        runtime_environment(role=role),
        role=role,
        release_sha=RELEASE_SHA,
        observer_service=TARGETS.validate_canonical_observer_service(
            compose,
            role=role,
            label="fixture Compose",
        ),
    )["runtime_target_row"]
    target_set: dict[str, object] = {
        "schema": TARGETS.CONVERGENCE_RUNTIME_TARGET_SET_SCHEMA,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "canonical_compose_sha256": "c" * 64,
        "roles": {
            candidate: row if candidate == role else TARGETS.derive_runtime_target_binding(
                runtime_environment(role=candidate),
                role=candidate,
                release_sha=RELEASE_SHA,
            )["runtime_target_row"]
            for candidate in TARGETS.CONVERGENCE_RUNTIME_TARGET_ROLES
        },
        "target_set_sha256": "0" * 64,
    }
    target_set["target_set_sha256"] = TARGETS.runtime_target_set_digest(target_set)
    descriptor = TARGETS.runtime_target_set_descriptor(target_set)
    document = TARGETS.build_observer_runtime_target_binding(
        campaign_id=CAMPAIGN_ID,
        operation_id=OPERATION_ID,
        release_sha=RELEASE_SHA,
        manifest_sha256=MANIFEST_SHA256,
        canonical_compose_sha256="c" * 64,
        role=role,
        convergence_runtime_targets=descriptor,
        runtime_target_row=row,
        role_material_sha256="d" * 64,
        role_runtime_image_ids=image_ids(),
    )
    return {
        key: document[key]
        for key in (
            "campaign_id",
            "operation_id",
            "release_sha",
            "manifest_sha256",
            "canonical_compose_sha256",
            "role",
            "role_material_sha256",
            "role_runtime_image_ids",
            "binding_sha256",
        )
    }


def rendered_service(*, role: str) -> dict[str, object]:
    shape = TARGETS.observer_service_shape(role=role)
    release = MODULE.canonical_release_root(
        operation_id=OPERATION_ID,
        release_sha=RELEASE_SHA,
    )
    inputs = MODULE.canonical_runtime_input_root(
        operation_id=OPERATION_ID,
        role=role,
    )
    return {
        "image": image_ids()["app"],
        "pull_policy": "never",
        "profiles": shape["profiles"],
        "restart": shape["restart"],
        "command": shape["command"],
        "depends_on": {f"{role}_db": {"condition": "service_healthy"}},
        "networks": shape["networks"],
        "volumes": [
            {
                "type": "bind",
                "source": release,
                "target": release,
                "read_only": True,
                "bind": {"create_host_path": False},
            },
            {
                "type": "bind",
                "source": inputs,
                "target": inputs,
                "read_only": True,
                "bind": {"create_host_path": False},
            },
        ],
        "env_file": [
            MODULE.canonical_role_environment_path(
                operation_id=OPERATION_ID,
                role=role,
            )
        ],
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
    }


def plan(*, role: str = "bot_fi") -> dict[str, object]:
    compose = canonical_compose(role=role)
    return MODULE.build_execution_plan(
        campaign_id=CAMPAIGN_ID,
        operation_id=OPERATION_ID,
        release_sha=RELEASE_SHA,
        manifest_sha256=MANIFEST_SHA256,
        canonical_compose_sha256="c" * 64,
        canonical_compose=compose,
        rendered_observer_service=rendered_service(role=role),
        role=role,
        project_name=f"tb3p-{OPERATION_ID.replace('-', '')}-{role.replace('_', '-')}",
        role_compose_path=MODULE.canonical_role_compose_path(
            operation_id=OPERATION_ID,
            role=role,
        ),
        role_compose_sha256="e" * 64,
        role_environment_path=MODULE.canonical_role_environment_path(
            operation_id=OPERATION_ID,
            role=role,
        ),
        role_environment_sha256="f" * 64,
        collector_sha256="a" * 64,
        collector_delegate_sha256="b" * 64,
        collector_source_manifest_sha256="c" * 64,
        role_material_sha256="d" * 64,
        runtime_image_ids=image_ids(),
        runtime_target_binding=binding(role=role, compose=compose),
    )


def inspections(document: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    return (
        {
            "source": "docker-network-inspect-v1",
            "name": document["network_name"],
            "id_sha256": "8" * 64,
            "operation_id": OPERATION_ID,
            "project_name": document["project_name"],
            "internal": True,
        },
        {
            "source": "docker-container-inspect-v1",
            "id_sha256": "9" * 64,
            "operation_id": OPERATION_ID,
            "project_name": document["project_name"],
            "service": document["service"],
            "oneoff": True,
            "network_name": document["network_name"],
        },
    )


class ComposeObserverExecutionTests(unittest.TestCase):
    def test_plan_is_exact_one_shot_and_redacted(self) -> None:
        document = plan()
        self.assertEqual(document["schema"], MODULE.PLAN_SCHEMA)
        self.assertEqual(document["status"], "planned-not-executed")
        self.assertEqual(
            document["compose_argv"][-6:],
            [
                "run",
                "--cidfile",
                document["container_id_file"],
                "--rm",
                "--no-deps",
                "bot_fi_sync_observer",
            ],
        )
        self.assertEqual(
            document["collector_argv"][:5],
            [
                MODULE.CONTAINER_COLLECTOR_INTERPRETER,
                "-B",
                "-I",
                "-S",
                document["collector_path"],
            ],
        )
        self.assertNotEqual(document["collector_argv"], MODULE.OBSERVER_DUMMY_COMMAND)
        self.assertEqual(
            rendered_service(role="bot_fi")["env_file"],
            [document["role_environment_path"]],
        )
        self.assertEqual(document["cleanup_probe_argv"][:4], [MODULE.DOCKER, "ps", "--all", "--quiet"])
        self.assertTrue(document["release_mount"]["read_only"])
        self.assertTrue(document["runtime_input_mount"]["read_only"])
        self.assertTrue(document["production_mutation_forbidden"])
        self.assertTrue(document["object_storage_contact_forbidden"])
        self.assertEqual(MODULE.validate_execution_plan(document), document)

    def test_all_runtime_roles_bind_their_own_service_and_network(self) -> None:
        for role in TARGETS.CONVERGENCE_RUNTIME_TARGET_ROLES:
            with self.subTest(role=role):
                document = plan(role=role)
                self.assertEqual(document["service"], f"{role}_sync_observer")
                self.assertEqual(document["internal_network"], role)
                self.assertIn(role.replace("_", "-"), document["profile"])

    def test_plan_rejects_service_mount_or_image_drift(self) -> None:
        compose = canonical_compose(role="bot_fi")
        cases = []
        wrong_image = rendered_service(role="bot_fi")
        wrong_image["image"] = image_ids()["postgres"]
        cases.append(wrong_image)
        writable = rendered_service(role="bot_fi")
        writable["volumes"][0]["read_only"] = False  # type: ignore[index]
        cases.append(writable)
        wrong_target = rendered_service(role="bot_fi")
        wrong_target["volumes"][1]["target"] = "/run/unsafe"  # type: ignore[index]
        cases.append(wrong_target)
        with_port = rendered_service(role="bot_fi")
        with_port["ports"] = ["127.0.0.1:1:1"]
        cases.append(with_port)
        for service in cases:
            with self.subTest(service=service):
                with self.assertRaises(MODULE.ComposeObserverExecutionContractError):
                    MODULE.build_execution_plan(
                        campaign_id=CAMPAIGN_ID,
                        operation_id=OPERATION_ID,
                        release_sha=RELEASE_SHA,
                        manifest_sha256=MANIFEST_SHA256,
                        canonical_compose_sha256="c" * 64,
                        canonical_compose=compose,
                        rendered_observer_service=service,
                        role="bot_fi",
                        project_name=f"tb3p-{OPERATION_ID.replace('-', '')}-bot-fi",
                        role_compose_path=MODULE.canonical_role_compose_path(
                            operation_id=OPERATION_ID, role="bot_fi"
                        ),
                        role_compose_sha256="e" * 64,
                        role_environment_path=MODULE.canonical_role_environment_path(
                            operation_id=OPERATION_ID, role="bot_fi"
                        ),
                        role_environment_sha256="f" * 64,
                        collector_sha256="a" * 64,
                        collector_delegate_sha256="b" * 64,
                        collector_source_manifest_sha256="c" * 64,
                        role_material_sha256="d" * 64,
                        runtime_image_ids=image_ids(),
                        runtime_target_binding=binding(role="bot_fi", compose=compose),
                    )

    def test_plan_rejects_all_forbidden_or_unknown_rendered_service_keys(self) -> None:
        compose = canonical_compose(role="bot_fi")
        forbidden = {
            "entrypoint": ["/bin/sh"],
            "environment": {"DANGEROUS": "1"},
            "privileged": True,
            "cap_add": ["SYS_ADMIN"],
            "devices": ["/dev/null:/dev/null"],
            "pid": "host",
            "ipc": "host",
            "network_mode": "host",
            "build": ".",
            "extends": {"service": "unsafe"},
            "dns": ["1.1.1.1"],
            "unrecognized_compose_override": "unsafe",
        }
        for key, value in forbidden.items():
            with self.subTest(key=key):
                service = rendered_service(role="bot_fi")
                service[key] = value
                with self.assertRaises(MODULE.ComposeObserverExecutionContractError):
                    MODULE.build_execution_plan(
                        campaign_id=CAMPAIGN_ID,
                        operation_id=OPERATION_ID,
                        release_sha=RELEASE_SHA,
                        manifest_sha256=MANIFEST_SHA256,
                        canonical_compose_sha256="c" * 64,
                        canonical_compose=compose,
                        rendered_observer_service=service,
                        role="bot_fi",
                        project_name=f"tb3p-{OPERATION_ID.replace('-', '')}-bot-fi",
                        role_compose_path=MODULE.canonical_role_compose_path(
                            operation_id=OPERATION_ID, role="bot_fi"
                        ),
                        role_compose_sha256="e" * 64,
                        role_environment_path=MODULE.canonical_role_environment_path(
                            operation_id=OPERATION_ID, role="bot_fi"
                        ),
                        role_environment_sha256="f" * 64,
                        collector_sha256="a" * 64,
                        collector_delegate_sha256="b" * 64,
                        collector_source_manifest_sha256="c" * 64,
                        role_material_sha256="d" * 64,
                        runtime_image_ids=image_ids(),
                        runtime_target_binding=binding(role="bot_fi", compose=compose),
                    )

    def test_plan_rejects_canonical_network_or_binding_drift(self) -> None:
        compose = canonical_compose(role="bot_fi")
        compose["networks"]["bot_fi"]["internal"] = False  # type: ignore[index]
        with self.assertRaises(MODULE.ComposeObserverExecutionContractError):
            MODULE.build_execution_plan(
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                manifest_sha256=MANIFEST_SHA256,
                canonical_compose_sha256="c" * 64,
                canonical_compose=compose,
                rendered_observer_service=rendered_service(role="bot_fi"),
                role="bot_fi",
                project_name=f"tb3p-{OPERATION_ID.replace('-', '')}-bot-fi",
                role_compose_path=MODULE.canonical_role_compose_path(
                    operation_id=OPERATION_ID, role="bot_fi"
                ),
                role_compose_sha256="e" * 64,
                role_environment_path=MODULE.canonical_role_environment_path(
                    operation_id=OPERATION_ID, role="bot_fi"
                ),
                role_environment_sha256="f" * 64,
                collector_sha256="a" * 64,
                collector_delegate_sha256="b" * 64,
                collector_source_manifest_sha256="c" * 64,
                role_material_sha256="d" * 64,
                runtime_image_ids=image_ids(),
                runtime_target_binding=binding(role="bot_fi", compose=canonical_compose(role="bot_fi")),
            )

    def test_plan_rejects_canonical_compose_digest_drift(self) -> None:
        compose = canonical_compose(role="bot_fi")
        with self.assertRaisesRegex(
            MODULE.ComposeObserverExecutionContractError,
            "runtime target binding material differs",
        ):
            MODULE.build_execution_plan(
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                manifest_sha256=MANIFEST_SHA256,
                canonical_compose_sha256="f" * 64,
                canonical_compose=compose,
                rendered_observer_service=rendered_service(role="bot_fi"),
                role="bot_fi",
                project_name=f"tb3p-{OPERATION_ID.replace('-', '')}-bot-fi",
                role_compose_path=MODULE.canonical_role_compose_path(
                    operation_id=OPERATION_ID, role="bot_fi"
                ),
                role_compose_sha256="e" * 64,
                role_environment_path=MODULE.canonical_role_environment_path(
                    operation_id=OPERATION_ID, role="bot_fi"
                ),
                role_environment_sha256="f" * 64,
                collector_sha256="a" * 64,
                collector_delegate_sha256="b" * 64,
                collector_source_manifest_sha256="c" * 64,
                role_material_sha256="d" * 64,
                runtime_image_ids=image_ids(),
                runtime_target_binding=binding(role="bot_fi", compose=compose),
            )

    def test_plan_rejects_role_file_path_or_digest_substitution(self) -> None:
        for field, value in (
            ("role_compose_path", "/srv/untrusted/docker-compose.yml"),
            ("role_environment_path", "/root/untrusted/runtime.env.role"),
            ("role_compose_sha256", "2" * 64),
            ("role_environment_sha256", "3" * 64),
        ):
            with self.subTest(field=field):
                document = copy.deepcopy(plan())
                document[field] = value
                document["plan_sha256"] = MODULE._plan_digest(document)
                with self.assertRaises(MODULE.ComposeObserverExecutionContractError):
                    MODULE.validate_execution_plan(document)

    def test_receipt_is_bound_to_plan_and_rejects_mutation_or_digest_drift(self) -> None:
        document = plan()
        started = datetime(2026, 7, 29, tzinfo=timezone.utc)
        receipt = MODULE.build_execution_receipt(
            plan=document,
            image_id=image_ids()["app"],
            stdout=b'{"redacted":true}',
            stderr_bytes=0,
            exit_code=0,
            started_at=started,
            finished_at=started + timedelta(milliseconds=25),
            container_removed=True,
            cleanup_verified=True,
            network_inspection=inspections(document)[0],
            container_inspection=inspections(document)[1],
        )
        self.assertEqual(MODULE.validate_execution_receipt(receipt, plan=document), receipt)
        for field, value in (
            ("production_mutated", True),
            ("object_storage_contacted", True),
            ("container_removed", False),
            ("cleanup_verified", False),
        ):
            with self.subTest(field=field):
                forged = copy.deepcopy(receipt)
                forged[field] = value
                forged["receipt_sha256"] = MODULE._receipt_digest(forged)
                with self.assertRaises(MODULE.ComposeObserverExecutionContractError):
                    MODULE.validate_execution_receipt(forged, plan=document)

    def test_receipt_requires_inspect_derived_operation_identity(self) -> None:
        document = plan()
        started = datetime(2026, 7, 29, tzinfo=timezone.utc)
        network, container = inspections(document)
        receipt = MODULE.build_execution_receipt(
            plan=document,
            image_id=image_ids()["app"],
            stdout=b"{}",
            stderr_bytes=0,
            exit_code=0,
            started_at=started,
            finished_at=started + timedelta(milliseconds=1),
            container_removed=True,
            cleanup_verified=True,
            network_inspection=network,
            container_inspection=container,
        )
        for member, field, value in (
            ("network_inspection", "operation_id", CAMPAIGN_ID),
            ("network_inspection", "source", "untrusted"),
            ("container_inspection", "project_name", "other-project"),
            ("container_inspection", "network_name", "other-network"),
        ):
            with self.subTest(member=member, field=field):
                forged = copy.deepcopy(receipt)
                forged[member][field] = value
                forged["receipt_sha256"] = MODULE._receipt_digest(forged)
                with self.assertRaises(MODULE.ComposeObserverExecutionContractError):
                    MODULE.validate_execution_receipt(forged, plan=document)


if __name__ == "__main__":
    unittest.main()
