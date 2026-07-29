from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import produce_production_shadow_convergence_compose_inputs as MODULE
from scripts import production_shadow_convergence_compose_execution as EXECUTION
from scripts import production_shadow_convergence_runtime_targets as TARGETS
from scripts import produce_production_shadow_prepare_material as PREPARE
from scripts.render_three_site_production_shadow_role_compose import canonical_role_compose_bytes


CAMPAIGN_ID = "22222222-2222-4222-8222-222222222222"
OPERATION_ID = "11111111-1111-4111-8111-111111111111"
RELEASE_SHA = "a" * 40
MANIFEST_SHA256 = "b" * 64
SHADOW_COMPOSE_SHA256 = "c" * 64


def image_ids(role: str) -> dict[str, str]:
    offset = {"bot_fi": "1", "webapp_fi": "2", "webapp_ir": "3"}[role]
    return {
        "app": "sha256:" + offset * 64,
        "postgres": "sha256:" + "4" * 64,
        "redis": "sha256:" + "5" * 64,
        "nginx": "sha256:" + "6" * 64,
    }


def runtime_environment(role: str) -> dict[str, str]:
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


def canonical_compose(role: str) -> dict[str, object]:
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


class ComposeInputProducerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project_root = self.root / "project"
        self.secret_root = self.root / "secret"
        self.project_root.mkdir(mode=0o700)
        self.secret_root.mkdir(mode=0o700)
        self.patches = (
            mock.patch.object(EXECUTION, "PROJECT_ROOT_PREFIX", str(self.project_root)),
            mock.patch.object(EXECUTION, "SECRET_ROOT_PREFIX", str(self.secret_root)),
        )
        for patch in self.patches:
            patch.start()
        self.manifest = self._install_inputs()

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        self.temporary.cleanup()

    def _binding(
        self,
        role: str,
        *,
        role_material_sha256: str,
    ) -> tuple[dict[str, object], dict[str, object]]:
        compose = canonical_compose(role)
        rows = {
            candidate: TARGETS.derive_runtime_target_binding(
                runtime_environment(candidate),
                role=candidate,
                release_sha=RELEASE_SHA,
                observer_service=(
                    TARGETS.validate_canonical_observer_service(
                        canonical_compose(candidate),
                        role=candidate,
                        label="fixture Compose",
                    )
                    if candidate == role
                    else None
                ),
            )["runtime_target_row"]
            for candidate in TARGETS.CONVERGENCE_RUNTIME_TARGET_ROLES
        }
        target_set: dict[str, object] = {
            "schema": TARGETS.CONVERGENCE_RUNTIME_TARGET_SET_SCHEMA,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "canonical_compose_sha256": SHADOW_COMPOSE_SHA256,
            "roles": rows,
            "target_set_sha256": "0" * 64,
        }
        target_set["target_set_sha256"] = TARGETS.runtime_target_set_digest(target_set)
        binding = TARGETS.build_observer_runtime_target_binding(
            campaign_id=CAMPAIGN_ID,
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            manifest_sha256=MANIFEST_SHA256,
            canonical_compose_sha256=SHADOW_COMPOSE_SHA256,
            role=role,
            convergence_runtime_targets=TARGETS.runtime_target_set_descriptor(target_set),
            runtime_target_row=rows[role],
            role_material_sha256=role_material_sha256,
            role_runtime_image_ids=image_ids(role),
        )
        return binding, target_set

    def _install_inputs(self) -> dict[str, object]:
        materials: dict[str, dict[str, object]] = {}
        runtime_ids: dict[str, dict[str, str]] = {}
        project_operation = self.project_root / OPERATION_ID
        secret_operation = self.secret_root / OPERATION_ID
        collector_path = (
            project_operation
            / "releases"
            / RELEASE_SHA
            / EXECUTION.CONTAINER_COLLECTOR_RELATIVE
        )
        collector_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        repo_root = Path(__file__).resolve().parents[1]
        for directory in ("core", "models"):
            shutil.copytree(
                repo_root / directory,
                project_operation / "releases" / RELEASE_SHA / directory,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            for source_file in (project_operation / "releases" / RELEASE_SHA / directory).rglob("*.py"):
                source_file.chmod(0o600)
        collector_path.write_bytes(
            (repo_root / EXECUTION.CONTAINER_COLLECTOR_RELATIVE).read_bytes()
        )
        collector_path.chmod(0o600)
        delegate_path = (
            project_operation
            / "releases"
            / RELEASE_SHA
            / EXECUTION.CONTAINER_COLLECTOR_DELEGATE_RELATIVE
        )
        delegate_path.write_bytes(
            (repo_root / EXECUTION.CONTAINER_COLLECTOR_DELEGATE_RELATIVE).read_bytes()
        )
        delegate_path.chmod(0o600)
        for role in EXECUTION.ROLES:
            role_path = role.replace("_", "-")
            compose_path = project_operation / "rendered" / role_path / "docker-compose.yml"
            environment_path = secret_operation / role_path / "runtime.env.role"
            material_path = project_operation / "incoming" / f"role-material-{role_path}.tar"
            runtime_root = secret_operation / "convergence-observer-runtime" / role
            compose_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            environment_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            material_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            runtime_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            compose_bytes = canonical_role_compose_bytes(canonical_compose(role))
            environment_bytes = "".join(
                f"{MODULE.IMAGE_ENV_BY_KIND[kind]}={value}\n"
                for kind, value in image_ids(role).items()
            ).encode("ascii")
            ca_bytes = b"fixture-ca\n"
            payloads = {
                "role-compose.yml": compose_bytes,
                "runtime.env.role": environment_bytes,
                "ca.crt": ca_bytes,
            }
            internal_manifest: dict[str, object] = {
                "schema": (
                    PREPARE.WA_IR_FINAL_PREPARE_SCHEMA
                    if role == "webapp_ir"
                    else PREPARE.FI_FINAL_PREPARE_SCHEMA
                ),
                "operation_id": OPERATION_ID,
                "release_sha": RELEASE_SHA,
                "operation_manifest_sha256": "7" * 64,
                "stage_attestation_sha256": "8" * 64,
                "role": role,
                "runtime_image_ids": image_ids(role),
                "entries": [
                    {
                        "archive_path": name,
                        "destination": name,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "bytes": len(payload),
                        "mode": "0600",
                    }
                    for name, payload in payloads.items()
                ],
                "required_env_keys": sorted(
                    MODULE.IMAGE_ENV_BY_KIND.values()
                ),
            }
            files = {
                "final-prepare-manifest.json": MODULE._canonical_json(internal_manifest),
                **payloads,
            }
            compose_path.write_bytes(compose_bytes)
            environment_path.write_bytes(environment_bytes)
            material_path.write_bytes(PREPARE._tar_bytes(files))
            for path in (compose_path, environment_path, material_path):
                path.chmod(0o600)
            material_sha = hashlib.sha256(material_path.read_bytes()).hexdigest()
            binding_path = runtime_root / "runtime-target-binding.json"
            binding, target_set = self._binding(role, role_material_sha256=material_sha)
            binding_path.write_bytes(TARGETS._canonical_json(binding))
            binding_path.chmod(0o600)
            target_set_path = runtime_root / "convergence-runtime-targets.json"
            target_set_path.write_bytes(TARGETS._canonical_json(target_set))
            target_set_path.chmod(0o600)
            materials[role] = {
                "sha256": material_sha,
                "bytes": material_path.stat().st_size,
                "transport": "fixture",
                "format": "production-shadow-role-material-tar",
            }
            runtime_ids[role] = image_ids(role)
        return {
            "schema": TARGETS.CUTOVER_MANIFEST_SCHEMA,
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": "d" * 40,
            "artifacts": {
                "shadow_compose_sha256": SHADOW_COMPOSE_SHA256,
                "role_materials": materials,
                "role_runtime_image_ids": runtime_ids,
            },
        }

    def _produce(self) -> dict[str, dict[str, str]]:
        def sealed_paths(**kwargs):  # noqa: ANN003
            root = kwargs["release_root"]
            return sorted(
                path.relative_to(root).as_posix()
                for directory in ("core", "models")
                for path in (root / directory).rglob("*.py")
            ) + [
                EXECUTION.CONTAINER_COLLECTOR_RELATIVE,
                EXECUTION.CONTAINER_COLLECTOR_DELEGATE_RELATIVE,
            ]

        def sealed_blob(**kwargs):  # noqa: ANN003
            return (kwargs["release_root"] / kwargs["relative_path"]).read_bytes()

        with (
            mock.patch.object(MODULE.cutover, "validate_manifest", return_value=self.manifest),
            mock.patch.object(MODULE, "_verify_sealed_release_identity"),
            mock.patch.object(MODULE, "_sealed_collector_python_paths", side_effect=sealed_paths),
            mock.patch.object(MODULE, "_sealed_git_blob", side_effect=sealed_blob),
        ):
            return MODULE.produce_from_validated_manifest(
                self.manifest,
                manifest_sha256=MANIFEST_SHA256,
            )

    def test_creates_exact_pairs_for_all_runtime_roles(self) -> None:
        result = self._produce()
        self.assertEqual(set(result), set(EXECUTION.ROLES))
        for role in EXECUTION.ROLES:
            with self.subTest(role=role):
                plan_path = Path(result[role]["plan_path"])
                material_path = Path(result[role]["material_path"])
                self.assertTrue(plan_path.is_file())
                self.assertTrue(material_path.is_file())
                self.assertEqual(plan_path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(material_path.stat().st_mode & 0o777, 0o600)
                plan = json.loads(plan_path.read_text(encoding="ascii"))
                self.assertEqual(EXECUTION.validate_execution_plan(plan), plan)
                self.assertEqual(plan["role"], role)
                self.assertEqual(plan["runtime_image_ids"], image_ids(role))
                self.assertIn(
                    f"PRODUCTION_SHADOW_OPERATION_ID={OPERATION_ID}\n",
                    Path(plan["role_environment_path"]).read_text(encoding="ascii"),
                )
                overlay = MODULE.yaml.safe_load(
                    Path(plan["role_compose_path"]).read_text(encoding="ascii")
                )
                service = overlay["services"][f"{role}_sync_observer"]
                self.assertEqual(service["image"], image_ids(role)["app"])
                self.assertEqual(service["env_file"], [plan["role_environment_path"]])
                self.assertTrue(all(mount["read_only"] for mount in service["volumes"]))
                self.assertTrue(service["read_only"])
                self.assertEqual(service["cap_drop"], ["ALL"])
                self.assertEqual(service["security_opt"], ["no-new-privileges:true"])
                self.assertNotIn("entrypoint", service)
                self.assertNotIn("privileged", service)
                self.assertNotIn("witness", result)

    def test_rejects_target_set_drift(self) -> None:
        path = (
            self.secret_root
            / OPERATION_ID
            / "convergence-observer-runtime"
            / "bot_fi"
            / "convergence-runtime-targets.json"
        )
        document = json.loads(path.read_text(encoding="ascii"))
        document["target_set_sha256"] = "0" * 64
        path.write_bytes(TARGETS._canonical_json(document))
        with self.assertRaises(MODULE.ComposeInputProducerError):
            self._produce()

    def test_material_only_exact_residue_resumes_with_plan(self) -> None:
        result = self._produce()
        plan_path = Path(result["bot_fi"]["plan_path"])
        material_path = Path(result["bot_fi"]["material_path"])
        material = material_path.read_bytes()
        plan_path.unlink()
        resumed = self._produce()
        self.assertTrue(Path(resumed["bot_fi"]["plan_path"]).is_file())
        self.assertEqual(material_path.read_bytes(), material)

    def test_nonidentical_plan_collision_fails_without_material_overwrite(self) -> None:
        result = self._produce()
        plan_path = Path(result["bot_fi"]["plan_path"])
        material_path = Path(result["bot_fi"]["material_path"])
        material = material_path.read_bytes()
        plan_path.write_bytes(b"collision")
        plan_path.chmod(0o600)
        with self.assertRaisesRegex(MODULE.ComposeInputProducerError, "collision"):
            self._produce()
        self.assertEqual(material_path.read_bytes(), material)

    def test_source_change_after_candidate_construction_blocks_publication(self) -> None:
        source = (
            self.project_root / OPERATION_ID / "rendered" / "bot-fi" / "docker-compose.yml"
        )
        original_preflight = MODULE._preflight_output
        changed = False

        def mutate_after_candidates(*args, **kwargs):  # noqa: ANN001, ANN202
            nonlocal changed
            original_preflight(*args, **kwargs)
            if not changed:
                source.write_bytes(source.read_bytes() + b"# changed\n")
                source.chmod(0o600)
                changed = True

        with mock.patch.object(MODULE, "_preflight_output", side_effect=mutate_after_candidates):
            with self.assertRaisesRegex(MODULE.ComposeInputProducerError, "changed before publication"):
                self._produce()

    def test_rejects_noncanonical_role_path(self) -> None:
        original = MODULE._role_paths

        def drifted(**kwargs):  # noqa: ANN001
            paths = original(**kwargs)
            paths["compose"] = self.root / "outside" / "docker-compose.yml"
            return paths

        with mock.patch.object(MODULE, "_role_paths", side_effect=drifted):
            with self.assertRaises(MODULE.ComposeInputProducerError):
                self._produce()

    def test_rejects_manifest_binding_digest_drift(self) -> None:
        self.manifest["artifacts"]["shadow_compose_sha256"] = "d" * 64
        with self.assertRaisesRegex(MODULE.ComposeInputProducerError, "material, image, or binding differs"):
            self._produce()

    def test_rejects_role_material_image_and_binding_drift(self) -> None:
        cases = ("material", "image", "binding")
        for case in cases:
            with self.subTest(case=case):
                role = "bot_fi"
                role_path = role.replace("_", "-")
                if case == "material":
                    (self.project_root / OPERATION_ID / "incoming" / f"role-material-{role_path}.tar").write_bytes(b"drift")
                elif case == "image":
                    env_path = self.secret_root / OPERATION_ID / role_path / "runtime.env.role"
                    env_path.write_text(
                        env_path.read_text(encoding="ascii").replace("sha256:1", "sha256:9"),
                        encoding="ascii",
                    )
                else:
                    binding_path = self.secret_root / OPERATION_ID / "convergence-observer-runtime" / role / "runtime-target-binding.json"
                    binding = json.loads(binding_path.read_text(encoding="ascii"))
                    binding["binding_sha256"] = "0" * 64
                    binding_path.write_bytes(TARGETS._canonical_json(binding))
                with self.assertRaises(MODULE.ComposeInputProducerError):
                    self._produce()

    def test_sealed_git_read_uses_fixed_environment_and_no_inherited_fds(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"ok\n", stderr=b""
        )
        with mock.patch.object(MODULE.subprocess, "run", return_value=completed) as run:
            self.assertEqual(
                MODULE._sealed_git_output(
                    self.project_root,
                    ["rev-parse", "--verify", f"{RELEASE_SHA}^{{commit}}"],
                    label="fixture",
                ),
                b"ok\n",
            )
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], MODULE.GIT)
        self.assertIn("--no-replace-objects", argv)
        self.assertEqual(run.call_args.kwargs["env"], MODULE.GIT_SAFE_ENV)
        self.assertTrue(run.call_args.kwargs["close_fds"])
        self.assertEqual(run.call_args.kwargs["stdin"], MODULE.subprocess.DEVNULL)

    def test_sealed_collector_scope_accepts_executable_python_and_rejects_invalid_module(self) -> None:
        tree = b"".join(
            (
                b"100644 blob 1" + b"a" * 39 + b"\tcore/__init__.py\0",
                b"100755 blob 2" + b"b" * 39 + b"\tcore/tool.py\0",
                b"100644 blob 3" + b"c" * 39 + b"\tmodels/__init__.py\0",
                b"100644 blob 4" + b"d" * 39 + b"\t"
                + EXECUTION.CONTAINER_COLLECTOR_RELATIVE.encode("ascii") + b"\0",
                b"100644 blob 5" + b"e" * 39 + b"\t"
                + EXECUTION.CONTAINER_COLLECTOR_DELEGATE_RELATIVE.encode("ascii") + b"\0",
            )
        )
        with mock.patch.object(MODULE, "_sealed_git_output", return_value=tree):
            paths = MODULE._sealed_collector_python_paths(
                release_root=self.project_root,
                release_tree_sha="d" * 40,
            )
        self.assertIn("core/tool.py", paths)

        invalid = tree + b"100644 blob 6" + b"f" * 39 + b"\tcore/__pycache__/evil.py\0"
        with mock.patch.object(MODULE, "_sealed_git_output", return_value=invalid):
            with self.assertRaisesRegex(MODULE.ComposeInputProducerError, "module path is invalid"):
                MODULE._sealed_collector_python_paths(
                    release_root=self.project_root,
                    release_tree_sha="d" * 40,
                )

    def test_release_identity_requires_commit_tree_match(self) -> None:
        with mock.patch.object(MODULE, "_sealed_git_output", side_effect=[b"a" * 40 + b"\n", b"e" * 40 + b"\n"]):
            with self.assertRaisesRegex(MODULE.ComposeInputProducerError, "commit/tree differs"):
                MODULE._verify_sealed_release_identity(
                    release_root=self.project_root,
                    release_sha=RELEASE_SHA,
                    release_tree_sha="d" * 40,
                )


if __name__ == "__main__":
    unittest.main()
