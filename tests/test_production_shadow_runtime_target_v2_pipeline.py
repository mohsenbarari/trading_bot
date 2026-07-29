from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import build_production_shadow_precommit_manifests as PRECOMMIT
from scripts import (
    orchestrate_production_shadow_pre_freeze_evidence as PREFREEZE,
)
from scripts import (
    orchestrate_production_shadow_prepared_clone_inventory as PREPARED,
)
from scripts import produce_production_shadow_prepare_material as PRODUCER
from scripts import production_shadow_convergence_runtime_targets as TARGETS
from scripts import production_shadow_cutover_controller as CONTROLLER
from tests.test_produce_production_shadow_prepare_material import PrepareFixture


class RuntimeTargetV2PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.fixture = PrepareFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _consumer_manifest(result: dict[str, object]) -> dict[str, object]:
        bindings = result["controller_bindings"]
        assert isinstance(bindings, dict)
        return {
            "schema": CONTROLLER.MANIFEST_SCHEMA,
            "capabilities": list(TARGETS.RUNTIME_TARGET_CAPABILITIES),
            "operation_id": result["operation_id"],
            "release_sha": result["release_sha"],
            "topology": {role: {} for role in PRODUCER.ALL_ROLES},
            "artifacts": {
                "shadow_compose_sha256": result[
                    "canonical_compose_sha256"
                ],
                "role_materials": bindings["role_materials"],
                "role_runtime_image_ids": bindings[
                    "role_runtime_image_ids"
                ],
                "convergence_runtime_targets": bindings[
                    "convergence_runtime_targets"
                ],
            },
        }

    def test_real_producer_output_binds_all_three_consumers(self) -> None:
        result = self.fixture.produce()
        metadata_path = self.fixture.output / "prepare-materials.json"
        metadata = json.loads(metadata_path.read_bytes())
        manifest = self._consumer_manifest(result)

        precommit_metadata, precommit_details = PRECOMMIT._validate_prepare_set(
            metadata_path,
            controller=manifest,
            role_material_directory=self.fixture.output,
        )
        self.assertEqual(precommit_metadata, metadata)
        self.assertEqual(set(precommit_details), {"bot_fi", "webapp_fi"})

        self.assertEqual(
            PREPARED._validate_prepare_metadata(metadata, manifest=manifest),
            metadata,
        )

        context = PREFREEZE.CoordinatorContext(
            manifest_path=self.root / "manifest.json",
            approval_path=self.root / "approval.json",
            approval_policy_path=self.root / "policy.json",
            manifest=manifest,
            manifest_sha256="1" * 64,
            plan={},
            plan_sha256="2" * 64,
            output_root=self.root / "evidence",
            journal={},
        )
        records = {}
        self.assertEqual(
            PREFREEZE._validate_prepare_metadata(
                context,
                metadata_path,
                self.fixture.compose,
                self.fixture.stage_document,
                records,
            ),
            metadata,
        )
        self.assertEqual(
            set(records),
            {
                "prepare_metadata",
                "bot_fi_role_material",
                "webapp_fi_role_material",
                "webapp_ir_role_material",
                "witness_role_material",
                "canonical_compose",
            },
        )


if __name__ == "__main__":
    unittest.main()
