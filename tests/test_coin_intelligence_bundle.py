from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from core.market_intelligence.bundle import (
    BundleValidationError,
    DEFAULT_BUNDLE_DIRECTORY,
    SUPPORTED_CODE_VERSION,
    load_runtime_bundle,
)


class CoinIntelligenceBundleTests(unittest.TestCase):
    @staticmethod
    def _refresh_member_metadata(
        bundle: Path,
        name: str,
    ) -> None:
        member = bundle / name
        manifest_path = bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][name] = {
            "sha256": hashlib.sha256(member.read_bytes()).hexdigest(),
            "size_bytes": member.stat().st_size,
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_repository_shadow_bundle_verifies(self) -> None:
        bundle = load_runtime_bundle()

        self.assertEqual(bundle.manifest["status"], "SHADOW_NOT_PROMOTED")
        self.assertEqual(
            bundle.manifest["minimum_compatible_code_version"],
            SUPPORTED_CODE_VERSION,
        )
        self.assertFalse(bundle.manifest["source_model"]["is_llm"])
        self.assertFalse(bundle.manifest["training_payload_embedded"])
        self.assertFalse(
            bundle.manifest["absolute_machine_paths_embedded"]
        )
        self.assertEqual(
            bundle.bundle_version,
            "coin-inference-shadow-20260725-v9-safety-gated",
        )

    def test_checksum_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "bundle"
            shutil.copytree(DEFAULT_BUNDLE_DIRECTORY, copied)
            with (copied / "feature_schema.json").open(
                "a",
                encoding="utf-8",
            ) as handle:
                handle.write(" ")

            with self.assertRaisesRegex(
                BundleValidationError,
                "bundle_size_mismatch:feature_schema.json",
            ):
                load_runtime_bundle(copied)

    def test_non_shadow_manifest_is_rejected_before_loading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "bundle"
            shutil.copytree(DEFAULT_BUNDLE_DIRECTORY, copied)
            manifest_path = copied / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["status"] = "PROMOTED"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                BundleValidationError,
                "bundle_is_not_shadow",
            ):
                load_runtime_bundle(copied)

    def test_unexpected_bundle_member_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "bundle"
            shutil.copytree(DEFAULT_BUNDLE_DIRECTORY, copied)
            (copied / "training.sqlite3").write_bytes(b"not-runtime-data")

            with self.assertRaisesRegex(
                BundleValidationError,
                "bundle_unexpected_member",
            ):
                load_runtime_bundle(copied)

    def test_internally_consistent_promoted_policy_is_still_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "bundle"
            shutil.copytree(DEFAULT_BUNDLE_DIRECTORY, copied)
            policy_path = copied / "commodity_ranker_policy.json"
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["status"] = "PROMOTED"
            policy_path.write_text(
                json.dumps(policy, ensure_ascii=False),
                encoding="utf-8",
            )
            self._refresh_member_metadata(
                copied,
                "commodity_ranker_policy.json",
            )

            with self.assertRaisesRegex(
                BundleValidationError,
                "bundle_ranker_policy_not_shadow",
            ):
                load_runtime_bundle(copied)

    def test_catalog_ids_are_not_exposed_as_database_ids(self) -> None:
        bundle = load_runtime_bundle()
        catalog = bundle.commodity_catalog

        self.assertIn("catalog_version", catalog)
        self.assertTrue(
            all("name" in row for row in catalog["commodities"])
        )
        self.assertIn("امام", bundle.canonical_commodity_names)
        self.assertFalse(hasattr(bundle, "commodity_database_id"))

    def test_interval_contract_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "bundle"
            shutil.copytree(DEFAULT_BUNDLE_DIRECTORY, copied)
            interval_path = copied / "interval_policy.json"
            interval = json.loads(interval_path.read_text(encoding="utf-8"))
            interval["calibration"]["operational_target_coverage"] = 0.9
            interval_path.write_text(
                json.dumps(interval, ensure_ascii=False),
                encoding="utf-8",
            )
            self._refresh_member_metadata(copied, "interval_policy.json")

            with self.assertRaisesRegex(
                BundleValidationError,
                "bundle_operational_interval_target_mismatch",
            ):
                load_runtime_bundle(copied)

    def test_missing_anchor_runtime_safety_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "bundle"
            shutil.copytree(DEFAULT_BUNDLE_DIRECTORY, copied)
            policy_path = copied / "coin_anchor_transfer_policy.json"
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy.pop("runtime_safety")
            policy_path.write_text(
                json.dumps(policy, ensure_ascii=False),
                encoding="utf-8",
            )
            self._refresh_member_metadata(
                copied,
                "coin_anchor_transfer_policy.json",
            )

            with self.assertRaisesRegex(
                BundleValidationError,
                "bundle_anchor_runtime_safety_missing",
            ):
                load_runtime_bundle(copied)

    def test_catalog_alias_metadata_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "bundle"
            shutil.copytree(DEFAULT_BUNDLE_DIRECTORY, copied)
            catalog_path = copied / "commodity_catalog.json"
            catalog = json.loads(
                catalog_path.read_text(encoding="utf-8")
            )
            catalog["commodities"][0]["aliases"] = ["امامی"]
            catalog_path.write_text(
                json.dumps(catalog, ensure_ascii=False),
                encoding="utf-8",
            )
            self._refresh_member_metadata(
                copied,
                "commodity_catalog.json",
            )

            with self.assertRaisesRegex(
                BundleValidationError,
                "bundle_commodity_catalog_alias_forbidden",
            ):
                load_runtime_bundle(copied)

    def test_duplicate_canonical_catalog_name_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "bundle"
            shutil.copytree(DEFAULT_BUNDLE_DIRECTORY, copied)
            catalog_path = copied / "commodity_catalog.json"
            catalog = json.loads(
                catalog_path.read_text(encoding="utf-8")
            )
            catalog["commodities"][1]["name"] = (
                catalog["commodities"][0]["name"]
            )
            catalog_path.write_text(
                json.dumps(catalog, ensure_ascii=False),
                encoding="utf-8",
            )
            self._refresh_member_metadata(
                copied,
                "commodity_catalog.json",
            )

            with self.assertRaisesRegex(
                BundleValidationError,
                "bundle_commodity_catalog_name_duplicate",
            ):
                load_runtime_bundle(copied)


if __name__ == "__main__":
    unittest.main()
