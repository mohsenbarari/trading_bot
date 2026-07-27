from __future__ import annotations

from contextlib import redirect_stdout
import copy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from core.docker_image_identity import verify_content_descriptor
from scripts import build_production_shadow_source_snapshot_binding as MODULE
from scripts.produce_production_shadow_source_snapshot import (
    SOURCE_CONTAINERS,
    SOURCE_IMAGE_REFERENCES,
    SOURCE_PROJECTS,
    load_binding,
)
from scripts.production_shadow_cutover_controller import (
    EXPECTED_TOPOLOGY,
    HOST_AGENT_CONTRACT_SHA256,
    MANIFEST_SCHEMA,
    POLICY_FIELDS,
    _secure_root,
    _shadow_project,
    _shadow_root,
)


CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
OPERATION_ID = "22222222-2222-4222-8222-222222222222"
RELEASE_SHA = "a" * 40
LEGACY_RELEASE_SHA = "b" * 40


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def image_binding(seed: str) -> dict:
    descriptor = {
        "architecture": "amd64",
        "os": "linux",
        "created": f"2026-07-27T00:00:0{seed}Z",
        "config_sha256": "sha256:" + seed * 64,
        "rootfs_type": "layers",
        "rootfs_layers": ["sha256:" + seed * 64],
    }
    return {
        "content_descriptor": descriptor,
        "content_identity": verify_content_descriptor(descriptor),
    }


def controller_manifest() -> dict:
    secure_root = _secure_root(CAMPAIGN_ID)
    return {
        "schema": MANIFEST_SCHEMA,
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "release_sha": RELEASE_SHA,
        "release_tree_sha": "c" * 40,
        "legacy_release_sha": LEGACY_RELEASE_SHA,
        "topology": copy.deepcopy(EXPECTED_TOPOLOGY),
        "deployment": {
            "production_hostname": "coin.gold-trade.ir",
            "legacy_compose_project": "trading_bot",
            "shadow_compose_project": _shadow_project(OPERATION_ID),
            "shadow_root": str(_shadow_root(OPERATION_ID)),
            "controller_journal_path": str(secure_root / "journal.json"),
            "controller_evidence_root": str(secure_root / "evidence"),
        },
        "artifacts": {
            "release_bundle_sha256": "d" * 64,
            "release_bundle_bytes": 101,
            "role_materials": {
                role: {
                    "sha256": str(index) * 64,
                    "bytes": 101 + index,
                    "transport": EXPECTED_TOPOLOGY[role]["transport"],
                    "format": (
                        "production-shadow-witness-material-tar"
                        if role == "witness"
                        else "production-shadow-role-material-tar"
                    ),
                }
                for index, role in enumerate(EXPECTED_TOPOLOGY, 1)
            },
            "image_artifacts": {
                kind: {
                    "archive_sha256": "0" * 63 + suffix,
                    "archive_bytes": 200 + index,
                    "config_digest": "sha256:" + config * 64,
                    **image_binding(content),
                }
                for index, (kind, suffix, config, content) in enumerate(
                    (
                        ("app", "1", "a", "5"),
                        ("postgres", "2", "b", "6"),
                        ("redis", "3", "c", "7"),
                        ("nginx", "4", "d", "8"),
                    )
                )
            },
            "role_runtime_image_ids": {
                role: {
                    kind: "sha256:" + value * 64
                    for kind, value in zip(
                        ("app", "postgres", "redis", "nginx"),
                        values,
                        strict=True,
                    )
                }
                for role, values in {
                    "bot_fi": ("1", "2", "3", "4"),
                    "webapp_fi": ("5", "6", "7", "8"),
                    "webapp_ir": ("9", "a", "b", "c"),
                }.items()
            },
            "postgres_runtime_uid": 70,
            "postgres_runtime_gid": 70,
            "postgres_image_ref": (
                f"trading_bot_postgres_boottime:15-{RELEASE_SHA}"
            ),
            "legacy_bot_rollback_sha256": "2" * 64,
            "legacy_webapp_rollback_sha256": "3" * 64,
            "legacy_bot_redis_rollback_sha256": "6" * 64,
            "legacy_webapp_redis_rollback_sha256": "7" * 64,
            "shadow_compose_sha256": "4" * 64,
            "cutover_approval_sha256": "5" * 64,
            "human_approval_policy_sha256": "e" * 64,
            "nginx_freeze_generation_sha256": "8" * 64,
            "nginx_rollback_generation_sha256": "9" * 64,
            "nginx_shadow_readonly_generation_sha256": "f" * 64,
            "nginx_shadow_writable_generation_sha256": "0" * 63 + "1",
            "postcommit_executor_contract_sha256": "a" * 64,
            "phase_evidence_schema_sha256": "b" * 64,
            "host_agent_sha256": "c" * 64,
            "host_agent_contract_sha256": HOST_AGENT_CONTRACT_SHA256,
            "phase_evidence_verifier_sha256": "d" * 64,
        },
        "policy": {field: True for field in POLICY_FIELDS},
    }


class SourceSnapshotBindingBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.controller = self.root / "controller.json"
        self.document = controller_manifest()
        self.controller.write_bytes(canonical_bytes(self.document))
        self.controller.chmod(0o600)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_main(self, arguments: list[str]) -> tuple[int, dict]:
        output = io.StringIO()
        with redirect_stdout(output):
            status = MODULE.main(arguments)
        return status, json.loads(output.getvalue())

    def base_arguments(
        self,
        *,
        role: str = "bot_fi",
        mode: str = "live-baseline",
    ) -> list[str]:
        return [
            "--controller-manifest",
            str(self.controller),
            "--role",
            role,
            "--mode",
            mode,
            "--output-directory",
            str(self.root),
        ]

    def test_default_plan_is_deterministic_and_has_no_external_io(self):
        status, first = self.run_main(self.base_arguments())
        second_status, second = self.run_main(self.base_arguments())
        self.assertEqual((status, second_status), (0, 0))
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "planned")
        self.assertFalse(first["output_mutated"])
        self.assertFalse(first["docker_contacted"])
        self.assertFalse(first["network_io"])
        self.assertFalse(Path(first["output"]).exists())

    def test_apply_publishes_exact_producer_consumable_binding(self):
        _, plan = self.run_main(
            self.base_arguments(role="webapp_fi", mode="frozen-final")
        )
        arguments = self.base_arguments(
            role="webapp_fi",
            mode="frozen-final",
        ) + ["--apply", "--confirm", plan["required_confirmation"]]
        status, result = self.run_main(arguments)
        self.assertEqual(status, 0)
        self.assertEqual(result["publication"], "created")
        path = Path(result["output"])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        binding = load_binding(path)
        self.assertEqual(binding.role, "webapp_fi")
        self.assertEqual(binding.mode, "frozen-final")
        self.assertEqual(binding.source_project, SOURCE_PROJECTS["webapp_fi"])
        self.assertEqual(binding.containers, SOURCE_CONTAINERS)
        self.assertEqual(
            binding.images["application"],
            SOURCE_IMAGE_REFERENCES["webapp_fi"]["application"],
        )
        self.assertEqual(
            binding.canonical_sha256,
            result["binding_sha256"],
        )

        retry_status, retry = self.run_main(arguments)
        self.assertEqual(retry_status, 0)
        self.assertEqual(retry["publication"], "reused")

    def test_wrong_confirmation_and_plan_confirmation_fail_closed(self):
        status, result = self.run_main(
            self.base_arguments() + ["--apply", "--confirm", "wrong"]
        )
        self.assertEqual(status, 1)
        self.assertIn("apply requires", result["error"])
        status, result = self.run_main(
            self.base_arguments() + ["--confirm", "wrong"]
        )
        self.assertEqual(status, 1)
        self.assertIn("valid only", result["error"])

    def test_noncanonical_controller_and_output_conflict_fail_closed(self):
        self.controller.write_bytes(canonical_bytes(self.document) + b"\n")
        status, result = self.run_main(self.base_arguments())
        self.assertEqual(status, 1)
        self.assertIn("not canonical", result["error"])

        self.controller.write_bytes(canonical_bytes(self.document))
        output = self.root / MODULE.OUTPUT_NAMES[("bot_fi", "live-baseline")]
        output.write_bytes(b"{}")
        output.chmod(0o600)
        _, plan = self.run_main(self.base_arguments())
        status, result = self.run_main(
            self.base_arguments()
            + ["--apply", "--confirm", plan["required_confirmation"]]
        )
        self.assertEqual(status, 1)
        self.assertIn("overwrite", result["error"])
        self.assertEqual(output.read_bytes(), b"{}")

    def test_symlink_controller_and_unsafe_output_directory_fail_closed(self):
        target = self.root / "target.json"
        target.write_bytes(canonical_bytes(self.document))
        target.chmod(0o600)
        self.controller.unlink()
        self.controller.symlink_to(target)
        status, result = self.run_main(self.base_arguments())
        self.assertEqual(status, 1)
        self.assertIn("invalid", result["error"])

        self.controller.unlink()
        self.controller.write_bytes(canonical_bytes(self.document))
        self.controller.chmod(0o600)
        self.root.chmod(0o755)
        status, result = self.run_main(self.base_arguments())
        self.assertEqual(status, 1)
        self.assertIn("mode 0700", result["error"])

    def test_binding_changes_with_role_mode_or_controller_hash(self):
        controller, digest = MODULE.load_controller(self.controller)
        bindings = {
            (role, mode): MODULE.build_binding(
                controller,
                controller_sha256=digest,
                role=role,
                mode=mode,
            )
            for role in ("bot_fi", "webapp_fi")
            for mode in ("live-baseline", "frozen-final")
        }
        digests = {
            hashlib.sha256(canonical_bytes(value)).hexdigest()
            for value in bindings.values()
        }
        self.assertEqual(len(digests), 4)
        changed = copy.deepcopy(controller)
        changed["created_at"] = "2026-07-27T01:00:00+00:00"
        changed_digest = hashlib.sha256(canonical_bytes(changed)).hexdigest()
        rebound = MODULE.build_binding(
            changed,
            controller_sha256=changed_digest,
            role="bot_fi",
            mode="live-baseline",
        )
        self.assertNotEqual(
            bindings[("bot_fi", "live-baseline")][
                "controller_manifest_sha256"
            ],
            rebound["controller_manifest_sha256"],
        )

    def test_unexpected_error_is_redacted(self):
        with mock.patch.object(
            MODULE,
            "load_controller",
            side_effect=RuntimeError("sensitive detail"),
        ):
            status, result = self.run_main(self.base_arguments())
        self.assertEqual(status, 1)
        self.assertNotIn("sensitive", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
