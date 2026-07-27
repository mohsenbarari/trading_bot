from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import tempfile
from unittest import mock
import unittest

from scripts import orchestrate_production_shadow_finland_artifacts as MODULE
from scripts import production_shadow_finland_stage as STAGE


OPERATION_ID = "12345678-1234-4abc-8def-1234567890ab"
RELEASE_SHA = "1" * 40
RELEASE_TREE_SHA = "2" * 40


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def secure_file(path: Path, payload: bytes, mode: int) -> None:
    path.write_bytes(payload)
    path.chmod(mode)


def descriptor(index: int) -> tuple[dict, str]:
    value = {
        "architecture": "amd64",
        "os": "linux",
        "created": f"2026-07-{index + 1:02d}T00:00:00Z",
        "config_sha256": "sha256:" + f"{index + 1:x}" * 64,
        "rootfs_type": "layers",
        "rootfs_layers": ["sha256:" + f"{index + 5:x}" * 64],
    }
    return value, STAGE.verify_content_descriptor(value)


class ControllerFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.inputs = root / "inputs"
        self.inputs.mkdir(mode=0o700)
        self.agent = root / "production-shadow-finland-stage.py"
        secure_file(
            self.agent,
            b"#!/usr/bin/env python3\n# immutable stage agent\n",
            0o755,
        )
        self.identity = root / "id_ed25519"
        secure_file(self.identity, b"private-key-placeholder", 0o600)

        self.payloads = {
            "release-bundle": b"release-bundle",
            "app-image-archive": b"app-image-archive",
            "postgres-image-archive": b"postgres-image-archive",
            "redis-image-archive": b"redis-image-archive",
            "nginx-image-archive": b"nginx-image-archive",
        }
        for kind, payload in self.payloads.items():
            secure_file(
                self.inputs / STAGE.ARTIFACT_FILENAMES[kind],
                payload,
                0o600,
            )
        images = {}
        observations = {}
        contracts = {}
        for index, role in enumerate(STAGE.IMAGE_ROLES):
            content_descriptor, content_identity = descriptor(index)
            payload = self.payloads[f"{role}-image-archive"]
            images[role] = {
                "archive_sha256": hashlib.sha256(payload).hexdigest(),
                "archive_bytes": len(payload),
                "config_digest": "sha256:" + f"{index + 9:x}" * 64,
                "content_descriptor": content_descriptor,
                "content_identity": content_identity,
            }
            observations[role] = {
                "image_id": "sha256:" + f"{index + 1:x}" * 64,
                "informational_only": True,
            }
            contracts[role] = {
                "os": "linux",
                "architecture": "amd64",
                "repo_tags": [],
                "oci_revision": (
                    RELEASE_SHA
                    if role in STAGE.RELEASE_BOUND_IMAGE_ROLES
                    else None
                ),
            }
        contracts["postgres"]["runtime_user"] = {
            "uid": 70,
            "gid": 70,
            "uid_label": STAGE.POSTGRES_RUNTIME_UID_LABEL,
            "gid_label": STAGE.POSTGRES_RUNTIME_GID_LABEL,
        }
        bundle = self.payloads["release-bundle"]
        self.closure = {
            "schema": MODULE.RELEASE_CLOSURE_SCHEMA,
            "operation_id": OPERATION_ID,
            "release": {
                "commit_sha": RELEASE_SHA,
                "tree_sha": RELEASE_TREE_SHA,
                "bundle": {
                    "filename": "release.bundle",
                    "sha256": hashlib.sha256(bundle).hexdigest(),
                    "bytes": len(bundle),
                },
            },
            "images": images,
            "source_engine_observations": observations,
            "verified_image_contracts": contracts,
            "constraints": {
                "source_backup_included": False,
                "role_material_included": False,
                "secrets_included": False,
                "network_transfer_performed": False,
                "container_runtime_changed": False,
            },
        }
        self.closure_path = self.inputs / "closure-manifest.json"
        secure_file(
            self.closure_path,
            json.dumps(self.closure, sort_keys=True, indent=2).encode() + b"\n",
            0o600,
        )

    def orchestrate(self, **overrides):  # noqa: ANN003
        values = {
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": RELEASE_TREE_SHA,
            "closure_manifest": self.closure_path,
            "stage_agent": self.agent,
            "ssh_identity": self.identity,
            "observed_host_addresses": {MODULE.BOT_FI_HOST},
        }
        values.update(overrides)
        return MODULE.orchestrate(**values)

    def closure_and_sources(self):
        closure, raw, digest = MODULE.load_release_closure(
            self.closure_path,
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            release_tree_sha=RELEASE_TREE_SHA,
        )
        sources = MODULE._artifact_sources(
            self.closure_path,
            closure,
            required_uid=0,
        )
        return closure, raw, digest, sources


def host_result(manifest: dict, manifest_sha256: str, offset: int) -> dict:
    characters = "89ab" if offset == 0 else "cdef"
    runtime_ids = {
        role: "sha256:" + character * 64
        for role, character in zip(STAGE.IMAGE_ROLES, characters)
    }
    paths = STAGE.canonical_paths(
        manifest["operation_id"],
        manifest["release_sha"],
        manifest["role"],
    )
    return {
        "schema": STAGE.RESULT_SCHEMA,
        "status": "staged",
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "role": manifest["role"],
        "operation_manifest_sha256": manifest_sha256,
        "stage_attestation_sha256": (
            "4" * 64 if offset == 0 else "5" * 64
        ),
        "stage_attestation_path": str(paths["attestation"]),
        "runtime_image_ids": runtime_ids,
        "containers_started": False,
        "services_started": False,
        "networks_created": False,
        "volumes_created": False,
        "current_mutated": False,
        "data_mutated": False,
    }


class FinlandArtifactOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = ControllerFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_default_is_a_zero_execution_dry_plan(self):
        calls: list[list[str]] = []

        def forbidden_runner(arguments, **_kwargs):  # noqa: ANN001
            calls.append(arguments)
            raise AssertionError("dry plan executed a command")

        result = self.fixture.orchestrate(runner=forbidden_runner)
        self.assertEqual(result["schema"], MODULE.PLAN_SCHEMA)
        self.assertEqual(result["status"], "planned")
        self.assertEqual(calls, [])
        self.assertEqual(set(result["roles"]), set(MODULE.ROLES))
        self.assertEqual(
            result["required_confirmation"],
            (
                "STAGE-PRODUCTION-SHADOW-FINLAND-ARTIFACTS:"
                f"{OPERATION_ID}:{RELEASE_SHA}"
            ),
        )
        for field in (
            "object_storage_used",
            "arvan_endpoint_contacted",
            "containers_created",
            "containers_started",
            "services_started",
            "networks_created",
            "volumes_created",
            "current_mutated",
            "data_mutated",
        ):
            self.assertIs(result[field], False)

    def test_manifest_and_incoming_artifact_inventory_are_exact(self):
        closure, _raw, _digest, _sources = self.fixture.closure_and_sources()
        agent_sha = hashlib.sha256(self.fixture.agent.read_bytes()).hexdigest()
        for role in MODULE.ROLES:
            manifest = MODULE.build_stage_manifest(
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                release_tree_sha=RELEASE_TREE_SHA,
                role=role,
                closure=closure,
                agent_sha256=agent_sha,
            )
            self.assertEqual(set(manifest), STAGE.MANIFEST_FIELDS)
            self.assertEqual(
                manifest["schema"],
                "production-shadow-finland-image-stage-manifest-v1",
            )
            self.assertEqual(set(manifest["artifacts"]), set(STAGE.ARTIFACT_KINDS))
            self.assertEqual(
                {
                    kind: manifest["artifacts"][kind]["filename"]
                    for kind in STAGE.ARTIFACT_KINDS
                },
                STAGE.ARTIFACT_FILENAMES,
            )
            self.assertEqual(
                set(manifest["image_artifacts"]),
                set(STAGE.IMAGE_ROLES),
            )
            for row in manifest["image_artifacts"].values():
                self.assertEqual(set(row), STAGE.IMAGE_ARTIFACT_FIELDS)
            self.assertEqual(manifest["pull_policy"], "never")
            self.assertEqual(manifest["postgres_runtime_uid"], 70)
            self.assertEqual(manifest["postgres_runtime_gid"], 70)

    def test_webapp_uses_only_exact_trusted_ssh_and_scp_endpoint(self):
        plan = self.fixture.orchestrate()
        webapp = plan["roles"]["webapp_fi"]
        self.assertEqual(webapp["host"], "65.109.220.59")
        self.assertEqual(webapp["transport"], "trusted-ssh-scp")
        commands = webapp["commands"]
        for name in ("prepare", "bootstrap_install", "version", "stage"):
            argv = commands[name]
            self.assertEqual(argv[0], MODULE.SSH)
            self.assertIn("BatchMode=yes", argv)
            self.assertIn("StrictHostKeyChecking=yes", argv)
            self.assertIn(
                "UserKnownHostsFile=/root/.ssh/known_hosts",
                argv,
            )
            self.assertEqual(argv[argv.index("-p") + 1], "37067")
            self.assertEqual(argv[-2], "root@65.109.220.59")
        bootstrap_scp = commands["bootstrap_transfer"]
        self.assertEqual(bootstrap_scp[0], MODULE.SCP)
        self.assertEqual(bootstrap_scp[bootstrap_scp.index("-P") + 1], "37067")
        self.assertTrue(
            bootstrap_scp[-1].startswith("root@65.109.220.59:")
        )
        self.assertTrue(bootstrap_scp[-1].endswith(".transfer"))
        rendered = json.dumps(plan, sort_keys=True).lower()
        self.assertNotIn(MODULE.WA_IR_HOST, rendered)
        self.assertNotIn(MODULE.WITNESS_HOST, rendered)
        self.assertNotIn("webapp_ir", rendered)
        self.assertNotIn("witness", rendered)

    def test_remote_stage_request_is_canonical_base64_and_nonsecret(self):
        plan = self.fixture.orchestrate()
        remote_command = plan["roles"]["webapp_fi"]["commands"]["stage"][-1]
        remote_argv = shlex.split(remote_command)
        encoded = remote_argv[remote_argv.index("--request-b64") + 1]
        request = STAGE._decode_request(encoded, bootstrap=False)
        self.assertEqual(request["operation_id"], OPERATION_ID)
        self.assertEqual(request["role"], "webapp_fi")
        self.assertEqual(request["pull_policy"], "never")
        self.assertEqual(set(request), STAGE.REQUEST_FIELDS)
        serialized = json.dumps(request, sort_keys=True).lower()
        for fragment in ("secret", "token", "password", "private_key", "url"):
            self.assertNotIn(fragment, serialized)

    def test_remote_command_quoting_prevents_shell_injection(self):
        values = [
            "/usr/bin/python3",
            "/fixed/agent.py",
            "--value",
            "literal;touch /tmp/not-executed",
        ]
        command = MODULE._remote_command(values)
        self.assertEqual(shlex.split(command), values)
        with self.assertRaises(
            (MODULE.FinlandArtifactOrchestratorError, STAGE.FinlandStageError)
        ):
            self.fixture.orchestrate(
                operation_id=OPERATION_ID + ";touch-/tmp/injected"
            )

    def test_plan_has_no_forbidden_runtime_or_mutable_paths(self):
        plan = self.fixture.orchestrate()
        rendered = json.dumps(plan, sort_keys=True).lower()
        for fragment in (
            "/current",
            "docker build",
            "docker pull",
            "docker run",
            "docker compose",
            "service start",
            "network create",
            "volume create",
            "object-storage",
        ):
            self.assertNotIn(fragment, rendered)
        self.assertIs(plan["arvan_endpoint_contacted"], False)
        self.assertEqual(plan["pull_policy"], "never")
        self.assertEqual(
            len(plan["roles"]["bot_fi"]["incoming_files"]),
            7,
        )
        self.assertEqual(
            {
                row["kind"]
                for row in plan["roles"]["bot_fi"]["incoming_files"]
            },
            {
                "bootstrap-agent",
                "operation-manifest",
                *STAGE.ARTIFACT_KINDS,
            },
        )

    def test_closure_content_descriptor_forgery_is_rejected(self):
        forged = json.loads(json.dumps(self.fixture.closure))
        forged["images"]["app"]["content_descriptor"]["created"] = (
            "2025-01-01T00:00:00Z"
        )
        secure_file(
            self.fixture.closure_path,
            canonical(forged),
            0o600,
        )
        with self.assertRaisesRegex(
            MODULE.FinlandArtifactOrchestratorError,
            "content identity differs",
        ):
            self.fixture.orchestrate()

    def test_apply_requires_exact_confirmation_before_controller_writes(self):
        controller_prefix = self.root / "controller-secret"
        controller_prefix.mkdir(mode=0o700)
        with mock.patch.object(
            MODULE,
            "CONTROLLER_SECRET_ROOT_PREFIX",
            controller_prefix,
        ):
            with self.assertRaisesRegex(
                MODULE.FinlandArtifactOrchestratorError,
                "confirmation mismatch",
            ):
                self.fixture.orchestrate(
                    apply=True,
                    confirm="wrong",
                )
        self.assertEqual(list(controller_prefix.iterdir()), [])

    def test_create_only_controller_output_never_overwrites(self):
        destination = self.root / "evidence.json"
        payload = canonical({"fixed": True})
        first = MODULE._write_create_only(
            destination,
            payload,
            required_uid=0,
        )
        second = MODULE._write_create_only(
            destination,
            payload,
            required_uid=0,
        )
        self.assertEqual(first, second)
        with self.assertRaisesRegex(
            MODULE.FinlandArtifactOrchestratorError,
            "destination differs",
        ):
            MODULE._write_create_only(
                destination,
                canonical({"fixed": False}),
                required_uid=0,
            )
        self.assertEqual(destination.read_bytes(), payload)

    def test_local_partial_copy_resumes_and_reconciles_safe_partial(self):
        source = self.root / "source"
        destination_dir = self.root / "destination"
        destination_dir.mkdir(mode=0o700)
        destination = destination_dir / "artifact"
        payload = b"immutable-payload"
        secure_file(source, payload, 0o600)
        expected = hashlib.sha256(payload).hexdigest()
        arguments = {
            "source": source,
            "destination": destination,
            "expected_sha256": expected,
            "expected_bytes": len(payload),
            "source_modes": frozenset({0o600}),
            "destination_mode": 0o600,
            "required_uid": 0,
        }
        MODULE._copy_to_partial(**arguments)
        partial = STAGE.transfer_partial_path(destination)
        self.assertEqual(partial.read_bytes(), payload)
        MODULE._copy_to_partial(**arguments)
        self.assertEqual(partial.read_bytes(), payload)
        secure_file(partial, b"safe-incomplete", 0o600)
        MODULE._copy_to_partial(**arguments)
        self.assertEqual(partial.read_bytes(), payload)
        self.assertFalse(destination.exists())
        os.link(partial, destination)
        MODULE._copy_to_partial(**arguments)
        STAGE._publish_transfer_partial(
            destination,
            expected_sha256=expected,
            expected_bytes=len(payload),
            required_uid=0,
            mode=0o600,
        )
        self.assertFalse(partial.exists())
        self.assertEqual(destination.stat().st_nlink, 1)

    def test_apply_resume_emits_exact_binding_summaries(self):
        controller_prefix = self.root / "controller-secret"
        controller_prefix.mkdir(mode=0o700)
        calls: list[str] = []

        def stage_local(**kwargs):  # noqa: ANN003
            calls.append("bot_fi")
            return host_result(
                kwargs["manifest"],
                kwargs["manifest_sha256"],
                0,
            )

        def stage_remote(**kwargs):  # noqa: ANN003
            calls.append("webapp_fi")
            return host_result(
                kwargs["manifest"],
                kwargs["manifest_sha256"],
                1,
            )

        confirmation = MODULE.confirmation_phrase(OPERATION_ID, RELEASE_SHA)
        crashed = False

        def checkpoint(name: str) -> None:
            nonlocal crashed
            if name == "after-role:bot_fi" and not crashed:
                crashed = True
                raise RuntimeError("simulated controller interruption")

        patches = (
            mock.patch.object(
                MODULE,
                "CONTROLLER_SECRET_ROOT_PREFIX",
                controller_prefix,
            ),
            mock.patch.object(
                MODULE,
                "_stage_local_role",
                side_effect=stage_local,
            ),
            mock.patch.object(
                MODULE,
                "_stage_remote_role",
                side_effect=stage_remote,
            ),
        )
        with patches[0], patches[1], patches[2]:
            with self.assertRaisesRegex(RuntimeError, "interruption"):
                self.fixture.orchestrate(
                    apply=True,
                    confirm=confirmation,
                    checkpoint=checkpoint,
                )
            result = self.fixture.orchestrate(
                apply=True,
                confirm=confirmation,
            )
        self.assertEqual(calls, ["bot_fi", "webapp_fi"])
        self.assertEqual(result["status"], "staged")
        self.assertEqual(
            set(result["stage_bindings"]),
            {"schema", "operation_id", "release_sha", "roles"},
        )
        self.assertEqual(
            set(result["stage_bindings"]["roles"]),
            set(MODULE.ROLES),
        )
        for role in MODULE.ROLES:
            summary = result["binding_summaries"][role]
            self.assertEqual(set(summary), MODULE.ROLE_BINDING_FIELDS)
            self.assertEqual(
                summary["schema"],
                "production-shadow-role-image-stage-binding-v1",
            )
            self.assertEqual(summary["role"], role)
            self.assertEqual(
                set(summary["runtime_image_ids"]),
                set(STAGE.IMAGE_ROLES),
            )
            rendered = canonical(summary).decode("ascii").lower()
            for forbidden in (
                "/root/",
                "/srv/",
                "http:",
                "https:",
                "ssh:",
                "secret",
            ):
                self.assertNotIn(forbidden, rendered)
            self.assertEqual(
                result["stage_bindings"]["roles"][role],
                {
                    "stage_operation_manifest_sha256": summary[
                        "stage_operation_manifest_sha256"
                    ],
                    "stage_attestation_sha256": summary[
                        "stage_attestation_sha256"
                    ],
                    "runtime_image_ids": summary["runtime_image_ids"],
                },
            )
        evidence_path = Path(result["evidence_path"])
        self.assertTrue(evidence_path.is_file())
        self.assertEqual(stat.S_IMODE(evidence_path.stat().st_mode), 0o600)
        self.assertEqual(
            hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            result["evidence_sha256"],
        )
        evidence = json.loads(evidence_path.read_bytes())
        self.assertNotIn("evidence_path", evidence)
        self.assertNotIn("evidence_sha256", evidence)
        journal = json.loads(
            (
                controller_prefix
                / OPERATION_ID
                / MODULE.CONTROLLER_DIRECTORY
                / MODULE.CONTROLLER_JOURNAL_FILENAME
            ).read_bytes()
        )
        self.assertEqual(journal["status"], "complete")
        self.assertEqual(journal["completed_roles"], list(MODULE.ROLES))

    def test_different_destination_runtime_ids_are_not_bound_to_config_digest(
        self,
    ):
        closure, _raw, _digest, _sources = self.fixture.closure_and_sources()
        agent_sha = hashlib.sha256(self.fixture.agent.read_bytes()).hexdigest()
        manifest = MODULE.build_stage_manifest(
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            release_tree_sha=RELEASE_TREE_SHA,
            role="webapp_fi",
            closure=closure,
            agent_sha256=agent_sha,
        )
        manifest_sha = hashlib.sha256(canonical(manifest)).hexdigest()
        result = host_result(manifest, manifest_sha, 1)
        validated = MODULE._validate_host_result(
            result,
            manifest=manifest,
            manifest_sha256=manifest_sha,
        )
        for role in STAGE.IMAGE_ROLES:
            self.assertNotEqual(
                validated["runtime_image_ids"][role],
                manifest["image_artifacts"][role]["config_digest"],
            )


if __name__ == "__main__":
    unittest.main()
