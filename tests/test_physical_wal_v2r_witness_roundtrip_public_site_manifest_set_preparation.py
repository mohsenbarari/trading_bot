"""Adversarial tests for all-site public V2R manifest-set preparation.

The fixture creates an already-admitted V2R full bundle using the existing
claims-only issuer.  The subject under test itself does not touch a provider,
credential, filesystem, deployment, or network transport.
"""

from __future__ import annotations

import ast
from dataclasses import fields
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import unittest

from core import (
    physical_wal_v2r_witness_roundtrip_public_site_manifest_set_preparation as subject,
)
from test_physical_wal_v2r_witness_roundtrip_public_site_manifest_renderer import (
    NOW,
    PhysicalWalV2rWitnessRoundtripPublicSiteManifestRendererTests as _BundleFixture,
)


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_wal_v2r_witness_roundtrip_public_site_manifest_set_preparation.py"
)


class PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.fixture = _BundleFixture(
            "test_renders_exact_2_2_4_public_slices_that_round_trip_existing_admission"
        )
        self.fixture.setUp()
        self.full_bundle = self.fixture.full_bundle
        self.config = (
            subject.PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationConfig(
                expected_full_bundle_sha256=self.full_bundle.full_bundle_sha256,
                enabled=True,
            )
        )

    def _prepare(self):
        return subject.prepare_physical_wal_v2r_witness_roundtrip_public_site_manifest_set(
            config=self.config,
            full_bundle=self.full_bundle,
            now=NOW,
        )

    def test_prepares_exact_complete_2_2_4_set_and_exposes_only_public_bytes(self) -> None:
        prepared = self._prepare()
        self.assertEqual(
            subject.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_SITE_MANIFEST_SET_PREPARATION_SCHEMA,
            prepared.schema,
        )
        self.assertEqual("v2r-public-site-manifest-set-prepared", prepared.status)
        self.assertEqual(self.full_bundle.full_bundle_sha256, prepared.full_bundle_sha256)
        self.assertFalse(prepared.provider_facts_verified)
        self.assertFalse(prepared.writer_authorized)
        self.assertFalse(prepared.promotion_authorized)
        self.assertFalse(prepared.traffic_authorized)
        self.assertFalse(prepared.phase5_authorized)
        self.assertFalse(prepared.execution_authorized)
        self.assertFalse(prepared.full_matrix_authorized)
        self.assertFalse(prepared.full_matrix_executed)

        manifests = subject.render_prepared_physical_wal_v2r_witness_roundtrip_public_site_manifest_set(
            prepared=prepared,
            now=NOW,
        )
        self.assertEqual(3, len(manifests))
        expected = (("wa-fi", 2), ("wa-ir", 2), ("witness", 4))
        for raw, (site, count) in zip(manifests, expected, strict=True):
            item = json.loads(raw.decode("ascii"))
            self.assertEqual(site, item["site"])
            self.assertEqual(count, len(item["roles"]))
            self.assertEqual(self.full_bundle.full_bundle_sha256, item["full_bundle_sha256"])
            self.assertTrue(all(role["local_site"] == site for role in item["roles"]))
        self.assertEqual(
            tuple(hashlib.sha256(raw).hexdigest() for raw in manifests),
            (
                prepared.wa_fi_manifest_sha256,
                prepared.wa_ir_manifest_sha256,
                prepared.witness_manifest_sha256,
            ),
        )

    def test_default_off_raw_input_and_bundle_pin_mismatch_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationError,
            "CONFIG_INVALID",
        ):
            subject.prepare_physical_wal_v2r_witness_roundtrip_public_site_manifest_set(
                config=(
                    subject.PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationConfig(
                        expected_full_bundle_sha256=self.full_bundle.full_bundle_sha256
                    )
                ),
                full_bundle=self.full_bundle,
                now=NOW,
            )
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationError,
            "BUNDLE_PIN_MISMATCH",
        ):
            subject.prepare_physical_wal_v2r_witness_roundtrip_public_site_manifest_set(
                config=(
                    subject.PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationConfig(
                        expected_full_bundle_sha256=hashlib.sha256(b"foreign").hexdigest(),
                        enabled=True,
                    )
                ),
                full_bundle=self.full_bundle,
                now=NOW,
            )
        with self.assertRaises(TypeError):
            subject.PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationConfig(
                role="forbidden"  # type: ignore[call-arg]
            )
        self.assertEqual(
            {"config", "full_bundle", "now"},
            set(
                inspect.signature(
                    subject.prepare_physical_wal_v2r_witness_roundtrip_public_site_manifest_set
                ).parameters
            ),
        )

    def test_opaque_bundle_and_preparation_tampering_fail_closed(self) -> None:
        prepared = self._prepare()
        object.__setattr__(prepared, "phase5_authorized", True)
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationError,
            "PREPARED_TAMPERED",
        ):
            subject.render_prepared_physical_wal_v2r_witness_roundtrip_public_site_manifest_set(
                prepared=prepared,
                now=NOW,
            )

        fresh = self._prepare()
        object.__setattr__(self.full_bundle, "authorizes_phase5", True)
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationError,
            "BUNDLE_INVALID",
        ):
            subject.render_prepared_physical_wal_v2r_witness_roundtrip_public_site_manifest_set(
                prepared=fresh,
                now=NOW,
            )

    def test_source_has_no_normal_v2_recovery_provider_or_runtime_imports(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse(
            any(
                token in name
                for name in imported
                for token in (
                    "physical_wal_v2_witness_roundtrip",
                    "physical_arvan",
                    "recovery",
                    "boto3",
                    "botocore",
                    "docker",
                    "http",
                    "os",
                    "pathlib",
                    "requests",
                    "socket",
                    "subprocess",
                )
            )
        )
        self.assertEqual(
            {
                "physical_wal_v2r_witness_roundtrip_full_bundle_manifest_admission",
                "physical_wal_v2r_witness_roundtrip_public_site_manifest_renderer",
            },
            {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module == "core"
                for alias in node.names
            },
        )
        forbidden = {
            "role",
            "credential",
            "iam",
            "provider",
            "path",
            "runtime",
            "service",
            "publisher",
            "transport",
        }
        self.assertFalse(
            forbidden
            & {
                token
                for field in fields(
                    subject.PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationConfig
                )
                for token in field.name.split("_")
            }
        )


if __name__ == "__main__":
    unittest.main()
