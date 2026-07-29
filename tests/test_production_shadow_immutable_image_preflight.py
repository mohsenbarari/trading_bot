from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts import production_shadow_immutable_image_preflight as MODULE
from scripts import production_shadow_convergence_runtime_targets as TARGETS


CAMPAIGN_ID = "22222222-2222-4222-8222-222222222222"
OPERATION_ID = "11111111-1111-4111-8111-111111111111"
RELEASE_SHA = "a" * 40
MANIFEST_SHA256 = "b" * 64
BINDING_SHA256 = "c" * 64


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
        "DATABASE_URL": f"postgresql+asyncpg://{role}_observer:secret@{role}_db/{role}",
        "SYNC_DATABASE_URL": f"postgresql://{role}_observer:secret@{role}_db/{role}",
        "POSTGRES_USER": f"{role}_observer",
        "POSTGRES_PASSWORD": "secret",
        "POSTGRES_DB": role,
    }


def runtime_target_binding(*, role: str = "webapp_ir") -> dict[str, object]:
    rows = {
        candidate: TARGETS.derive_runtime_target_binding(
            runtime_environment(role=candidate),
            role=candidate,
            release_sha=RELEASE_SHA,
        )["runtime_target_row"]
        for candidate in TARGETS.CONVERGENCE_RUNTIME_TARGET_ROLES
    }
    target_set: dict[str, object] = {
        "schema": TARGETS.CONVERGENCE_RUNTIME_TARGET_SET_SCHEMA,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "canonical_compose_sha256": "d" * 64,
        "roles": rows,
        "target_set_sha256": "0" * 64,
    }
    target_set["target_set_sha256"] = TARGETS.runtime_target_set_digest(target_set)
    return TARGETS.build_observer_runtime_target_binding(
        campaign_id=CAMPAIGN_ID,
        operation_id=OPERATION_ID,
        release_sha=RELEASE_SHA,
        manifest_sha256=MANIFEST_SHA256,
        canonical_compose_sha256="d" * 64,
        role=role,
        convergence_runtime_targets=TARGETS.runtime_target_set_descriptor(target_set),
        runtime_target_row=rows[role],
        role_material_sha256="e" * 64,
        role_runtime_image_ids=image_ids(),
    )


def plan() -> dict[str, object]:
    return MODULE.build_plan(
        campaign_id=CAMPAIGN_ID,
        operation_id=OPERATION_ID,
        release_sha=RELEASE_SHA,
        manifest_sha256=MANIFEST_SHA256,
        role="webapp_ir",
        runtime_target_binding=runtime_target_binding(),
    )


def install(root: Path, *, binding: dict[str, object] | None = None) -> dict[str, str]:
    return MODULE.install_from_runtime_target_binding(
        campaign_id=CAMPAIGN_ID,
        operation_id=OPERATION_ID,
        release_sha=RELEASE_SHA,
        manifest_sha256=MANIFEST_SHA256,
        role="webapp_ir",
        runtime_target_binding=runtime_target_binding() if binding is None else binding,
        install_root=root,
    )


def image_inspection() -> dict[str, object]:
    return {
        "Id": image_ids()["app"],
        "Architecture": "amd64",
        "Os": "linux",
        "Created": "2026-07-29T00:00:00Z",
        "Config": {"Env": ["PATH=/usr/local/bin:/usr/bin:/bin"], "Entrypoint": None},
        "RootFS": {"Type": "layers", "Layers": ["sha256:" + "5" * 64]},
    }


def container_inspection(document: dict[str, object]) -> dict[str, object]:
    return {
        "Id": "6" * 64,
        "Name": f"/{document['container_name']}",
        "Image": image_ids()["app"],
        "Config": {
            "Image": image_ids()["app"],
            "User": MODULE.PROBE_USER,
            "WorkingDir": "/",
            "Cmd": list(MODULE.PROBE_RUNTIME_ARGV),
            "Entrypoint": None,
            "Labels": document["labels"],
        },
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "CapAdd": [],
            "SecurityOpt": ["no-new-privileges:true"],
            "PidsLimit": MODULE.PROBE_PIDS_LIMIT,
            "Memory": MODULE.PROBE_MEMORY_BYTES,
            "NanoCpus": MODULE.PROBE_NANO_CPUS,
            "Privileged": False,
            "PortBindings": {},
            "Binds": [],
            "VolumesFrom": [],
            "Devices": [],
            "DeviceRequests": [],
            "PidMode": "",
            "IpcMode": "private",
            "UTSMode": "",
            "UsernsMode": "",
            "CgroupnsMode": "private",
            "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=16m"},
            "AutoRemove": False,
            "RestartPolicy": {"Name": "no"},
        },
        "Mounts": [],
        "NetworkSettings": {"Networks": {}},
    }


def probe_stdout() -> bytes:
    document = {
        "schema": MODULE.OUTPUT_SCHEMA,
        "status": "passed",
        "python_major": 3,
        "python_minor": 11,
        "isolated": True,
        "no_site": True,
        "safe_path": True,
        "dependency_versions": dict(MODULE.EXPECTED_DEPENDENCIES),
        "installed_roots": [
            "/usr/local/lib/python3.11/site-packages",
            "/usr/lib/python3/dist-packages",
        ],
    }
    return json.dumps(document, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii") + b"\n"


def command_result(
    stdout: bytes = b"",
    *,
    stderr: bytes = b"",
    exit_code: int = 0,
) -> MODULE.ImmutableImagePreflightCommandResult:
    started = datetime(2026, 7, 29, tzinfo=timezone.utc)
    return MODULE.ImmutableImagePreflightCommandResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        started_at=started,
        finished_at=started + timedelta(milliseconds=25),
    )


class ImmutableImagePreflightTests(unittest.TestCase):
    def test_plan_is_exact_local_only_and_no_network(self) -> None:
        document = plan()
        self.assertEqual(MODULE.validate_plan(document), document)
        argv = document["create_argv"]
        self.assertIn("--pull=never", argv)
        self.assertIn("--network=none", argv)
        self.assertIn("--read-only", argv)
        self.assertIn("--cap-drop=ALL", argv)
        self.assertIn("--security-opt=no-new-privileges:true", argv)
        self.assertNotIn("--env", argv)
        self.assertNotIn("--volume", argv)
        self.assertEqual(argv[-len(MODULE.PROBE_RUNTIME_ARGV):], list(MODULE.PROBE_RUNTIME_ARGV))
        self.assertEqual(document["start_argv"][:3], [MODULE.DOCKER, "start", "--attach"])

    def test_plan_rejects_any_argv_or_image_substitution(self) -> None:
        for field, value in (("create_argv", ["docker"]), ("app_image_id", "sha256:" + "9" * 64)):
            with self.subTest(field=field):
                document = copy.deepcopy(plan())
                document[field] = value
                document["plan_sha256"] = MODULE._plan_digest(document)
                with self.assertRaises(MODULE.ImmutableImagePreflightContractError):
                    MODULE.validate_plan(document)
        binding = runtime_target_binding()
        binding["role_runtime_image_ids"]["app"] = "sha256:" + "9" * 64  # type: ignore[index]
        with self.assertRaises(MODULE.ImmutableImagePreflightContractError):
            MODULE.build_plan(
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                manifest_sha256=MANIFEST_SHA256,
                role="webapp_ir",
                runtime_target_binding=binding,
            )

    def test_probe_output_requires_fixed_interpreter_and_dependencies(self) -> None:
        output = probe_stdout()
        self.assertEqual(MODULE.validate_probe_output(output)["dependency_versions"], MODULE.EXPECTED_DEPENDENCIES)
        altered = json.loads(output)
        altered["isolated"] = False
        payload = json.dumps(altered, separators=(",", ":"), sort_keys=True).encode() + b"\n"
        with self.assertRaises(MODULE.ImmutableImagePreflightContractError):
            MODULE.validate_probe_output(payload)

    def test_image_inspection_binds_exact_id_and_canonical_descriptor(self) -> None:
        result = MODULE.inspect_image(image_inspection(), plan=plan())
        self.assertEqual(result["image_id"], image_ids()["app"])
        self.assertRegex(result["image_content_identity"], r"^sha256:[0-9a-f]{64}$")
        altered = image_inspection()
        altered["Id"] = image_ids()["postgres"]
        with self.assertRaises(MODULE.ImmutableImagePreflightContractError):
            MODULE.inspect_image(altered, plan=plan())
        altered = image_inspection()
        altered["Config"]["Entrypoint"] = ["sh"]  # type: ignore[index]
        with self.assertRaises(MODULE.ImmutableImagePreflightContractError):
            MODULE.inspect_image(altered, plan=plan())

    def test_container_inspection_requires_all_isolation_controls(self) -> None:
        document = plan()
        proof = MODULE.inspect_container(container_inspection(document), plan=document)
        self.assertEqual(proof["network_mode"], "none")
        self.assertEqual(proof["mount_count"], 0)
        for field, value in (("NetworkMode", "bridge"), ("ReadonlyRootfs", False), ("CapDrop", []), ("CapAdd", ["NET_RAW"]), ("Tmpfs", {})):
            with self.subTest(field=field):
                altered = container_inspection(document)
                altered["HostConfig"][field] = value  # type: ignore[index]
                with self.assertRaises(MODULE.ImmutableImagePreflightContractError):
                    MODULE.inspect_container(altered, plan=document)

    def test_receipt_is_redacted_bound_and_requires_zero_residue(self) -> None:
        document = plan()
        started = datetime(2026, 7, 29, tzinfo=timezone.utc)
        receipt = MODULE.build_receipt(
            plan=document,
            image_inspection=image_inspection(),
            container_inspection=container_inspection(document),
            stdout=probe_stdout(),
            stderr_bytes=0,
            exit_code=0,
            started_at=started,
            finished_at=started + timedelta(milliseconds=25),
            container_residue=b"",
            volume_residue=b"",
            network_residue=b"",
        )
        self.assertEqual(MODULE.validate_receipt(receipt, plan=document), receipt)
        self.assertNotIn("Config", receipt)
        self.assertNotIn(MODULE.PROBE_SOURCE, json.dumps(receipt))
        self.assertEqual(receipt["probe_stdout_sha256"], hashlib.sha256(probe_stdout()).hexdigest())
        with self.assertRaises(MODULE.ImmutableImagePreflightContractError):
            MODULE.build_receipt(
                plan=document,
                image_inspection=image_inspection(),
                container_inspection=container_inspection(document),
                stdout=probe_stdout(),
                stderr_bytes=0,
                exit_code=0,
                started_at=started,
                finished_at=started + timedelta(milliseconds=25),
                container_residue=b"unexpected",
                volume_residue=b"",
                network_residue=b"",
            )
        altered = copy.deepcopy(receipt)
        altered["residue_checks_sha256"] = "f" * 64
        altered["receipt_sha256"] = MODULE._receipt_digest(altered)
        with self.assertRaises(MODULE.ImmutableImagePreflightContractError):
            MODULE.validate_receipt(altered, plan=document)

    def test_receipt_rejects_container_or_dependency_proof_drift(self) -> None:
        document = plan()
        started = datetime(2026, 7, 29, tzinfo=timezone.utc)
        receipt = MODULE.build_receipt(
            plan=document,
            image_inspection=image_inspection(),
            container_inspection=container_inspection(document),
            stdout=probe_stdout(),
            stderr_bytes=0,
            exit_code=0,
            started_at=started,
            finished_at=started + timedelta(milliseconds=25),
            container_residue=b"",
            volume_residue=b"",
            network_residue=b"",
        )
        altered = copy.deepcopy(receipt)
        altered["container_proof"]["network_mode"] = "bridge"
        altered["receipt_sha256"] = MODULE._receipt_digest(altered)
        with self.assertRaises(MODULE.ImmutableImagePreflightContractError):
            MODULE.validate_receipt(altered, plan=document)
        altered = copy.deepcopy(receipt)
        altered["dependency_versions"] = {"asyncpg": "bad", "sqlalchemy": "2.0.31"}
        altered["receipt_sha256"] = MODULE._receipt_digest(altered)
        with self.assertRaises(MODULE.ImmutableImagePreflightContractError):
            MODULE.validate_receipt(altered, plan=document)

    def test_installer_publishes_only_canonical_create_only_pair_and_readback(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result = install(root)
            paths = MODULE.canonical_install_paths(
                operation_id=OPERATION_ID,
                role="webapp_ir",
                install_root=root,
            )
            self.assertEqual(result["plan_path"], str(paths["plan"]))
            self.assertEqual(result["material_path"], str(paths["material"]))
            self.assertEqual(result["receipt_path"], str(paths["receipt"]))
            self.assertFalse(paths["receipt"].exists())
            installed_plan, material, read_paths = MODULE.load_installed_inputs(
                operation_id=OPERATION_ID,
                role="webapp_ir",
                install_root=root,
            )
            self.assertEqual(read_paths, paths)
            self.assertEqual(installed_plan["plan_sha256"], result["plan_sha256"])
            self.assertEqual(material["material_sha256"], result["material_sha256"])
            self.assertEqual(material["receipt_schema"], MODULE.RECEIPT_SCHEMA)
            self.assertEqual(
                material["receipt_verification_schema"], MODULE.RECEIPT_VERIFICATION_SCHEMA
            )
            self.assertEqual(paths["plan"].stat().st_mode & 0o777, 0o600)
            self.assertEqual(paths["material"].stat().st_mode & 0o777, 0o600)
            self.assertEqual(install(root), result)

    def test_installer_rejects_binding_drift_before_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            binding = runtime_target_binding()
            binding["role_runtime_image_ids"]["app"] = "sha256:" + "9" * 64  # type: ignore[index]
            with self.assertRaises(MODULE.ImmutableImagePreflightContractError):
                install(root, binding=binding)
            paths = MODULE.canonical_install_paths(
                operation_id=OPERATION_ID,
                role="webapp_ir",
                install_root=root,
            )
            self.assertFalse(paths["directory"].exists())

    def test_installer_rejects_collision_before_any_new_output(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            paths = MODULE.canonical_install_paths(
                operation_id=OPERATION_ID,
                role="webapp_ir",
                install_root=root,
            )
            current = root
            for component in paths["directory"].relative_to(root).parts:
                current = current / component
                current.mkdir(mode=0o700)
                current.chmod(0o700)
            paths["plan"].write_bytes(b"{}\n")
            paths["plan"].chmod(0o600)
            with self.assertRaisesRegex(
                MODULE.ImmutableImagePreflightContractError, "plan collision differs"
            ):
                install(root)
            self.assertFalse(paths["material"].exists())

    def test_installer_readback_and_redacted_receipt_verifier_fail_closed_on_drift(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            install(root)
            plan_document, material, paths = MODULE.load_installed_inputs(
                operation_id=OPERATION_ID,
                role="webapp_ir",
                install_root=root,
            )
            started = datetime(2026, 7, 29, tzinfo=timezone.utc)
            receipt = MODULE.build_receipt(
                plan=plan_document,
                image_inspection=image_inspection(),
                container_inspection=container_inspection(plan_document),
                stdout=probe_stdout(),
                stderr_bytes=0,
                exit_code=0,
                started_at=started,
                finished_at=started + timedelta(milliseconds=25),
                container_residue=b"",
                volume_residue=b"",
                network_residue=b"",
            )
            verification = MODULE.build_receipt_verification(
                plan=plan_document, material=material, receipt=receipt
            )
            self.assertEqual(
                MODULE.validate_receipt_verification(
                    verification, plan=plan_document, material=material
                ),
                verification,
            )
            altered = copy.deepcopy(verification)
            altered["app_image_id"] = image_ids()["postgres"]
            altered["verification_sha256"] = MODULE._verification_digest(altered)
            with self.assertRaises(MODULE.ImmutableImagePreflightContractError):
                MODULE.validate_receipt_verification(
                    altered, plan=plan_document, material=material
                )
            paths["material"].write_bytes(b"{}\n")
            paths["material"].chmod(0o600)
            with self.assertRaises(MODULE.ImmutableImagePreflightContractError):
                MODULE.load_installed_inputs(
                    operation_id=OPERATION_ID,
                    role="webapp_ir",
                    install_root=root,
                )

    def test_executor_uses_only_installed_plan_runner_and_publishes_redacted_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            install(root)
            installed, _material, _paths = MODULE.load_installed_inputs(
                operation_id=OPERATION_ID,
                role="webapp_ir",
                install_root=root,
            )
            image_payload = json.dumps([image_inspection()]).encode("ascii")
            container_payload = json.dumps([container_inspection(installed)]).encode("ascii")
            outcomes = iter(
                [
                    command_result(image_payload),
                    command_result(b"6" * 64 + b"\n"),
                    command_result(container_payload),
                    command_result(probe_stdout()),
                    command_result(),
                    command_result(),
                    command_result(),
                    command_result(),
                ]
            )
            observed: list[tuple[str, ...]] = []

            def runner(argv: tuple[str, ...]) -> MODULE.ImmutableImagePreflightCommandResult:
                observed.append(argv)
                return next(outcomes)

            result = MODULE.execute_installed_preflight(
                operation_id=OPERATION_ID,
                role="webapp_ir",
                runner=runner,
                install_root=root,
            )
            self.assertEqual(result["status"], "completed-local-only")
            self.assertEqual(
                observed,
                [
                    tuple(installed[field])
                    for field in (
                        "image_inspect_argv",
                        "create_argv",
                        "container_inspect_argv",
                        "start_argv",
                        "remove_argv",
                        "container_residue_argv",
                        "volume_residue_argv",
                        "network_residue_argv",
                    )
                ],
            )
            self.assertNotIn("Config", json.dumps(result))
            self.assertEqual(result["verification"]["receipt_sha256"], result["receipt_sha256"])
            self.assertEqual(
                MODULE.execute_installed_preflight(
                    operation_id=OPERATION_ID,
                    role="webapp_ir",
                    runner=lambda _argv: self.fail("existing receipt must not re-run"),
                    install_root=root,
                )["status"],
                "already-completed-local-only",
            )

    def test_executor_cleans_created_container_and_rejects_inspect_or_residue_drift(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            install(root)
            installed, _material, paths = MODULE.load_installed_inputs(
                operation_id=OPERATION_ID,
                role="webapp_ir",
                install_root=root,
            )
            image_payload = json.dumps([image_inspection()]).encode("ascii")
            bad_container = container_inspection(installed)
            bad_container["HostConfig"]["NetworkMode"] = "bridge"  # type: ignore[index]
            outcomes = iter(
                [
                    command_result(image_payload),
                    command_result(b"6" * 64 + b"\n"),
                    command_result(json.dumps([bad_container]).encode("ascii")),
                    command_result(),
                    command_result(),
                    command_result(),
                    command_result(),
                ]
            )
            observed: list[tuple[str, ...]] = []

            def runner(argv: tuple[str, ...]) -> MODULE.ImmutableImagePreflightCommandResult:
                observed.append(argv)
                return next(outcomes)

            with self.assertRaisesRegex(MODULE.ImmutableImagePreflightContractError, "inspection differs"):
                MODULE.execute_installed_preflight(
                    operation_id=OPERATION_ID,
                    role="webapp_ir",
                    runner=runner,
                    install_root=root,
                )
            self.assertEqual(observed[3:], [
                tuple(installed["remove_argv"]),
                tuple(installed["container_residue_argv"]),
                tuple(installed["volume_residue_argv"]),
                tuple(installed["network_residue_argv"]),
            ])
            self.assertFalse(paths["receipt"].exists())

    def test_executor_rejects_nonempty_final_residue_without_a_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            install(root)
            installed, _material, paths = MODULE.load_installed_inputs(
                operation_id=OPERATION_ID,
                role="webapp_ir",
                install_root=root,
            )
            outcomes = iter(
                [
                    command_result(json.dumps([image_inspection()]).encode("ascii")),
                    command_result(b"6" * 64 + b"\n"),
                    command_result(json.dumps([container_inspection(installed)]).encode("ascii")),
                    command_result(probe_stdout()),
                    command_result(),
                    command_result(b"unexpected-container\n"),
                ]
            )
            with self.assertRaisesRegex(
                MODULE.ImmutableImagePreflightContractError,
                "cleanup or zero-residue",
            ):
                MODULE.execute_installed_preflight(
                    operation_id=OPERATION_ID,
                    role="webapp_ir",
                    runner=lambda _argv: next(outcomes),
                    install_root=root,
                )
            self.assertFalse(paths["receipt"].exists())
if __name__ == "__main__":
    unittest.main()
