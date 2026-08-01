from __future__ import annotations

import ast
from dataclasses import fields, replace
import hashlib
import inspect
import json
import unittest

from core import physical_wal_v2_witness_roundtrip_deployment_plan as deployment


def _paths(site: str, local_role: str) -> tuple[str, str]:
    return (
        f"/etc/trading-bot/v2/{site}/config/{local_role}.json",
        f"/etc/trading-bot/v2/{site}/credentials/{local_role}.json",
    )


class PhysicalWalV2WitnessRoundtripDeploymentPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reference = deployment.PhysicalWalV2WitnessRoundtripPublicFullBundleReference(
            bundle_id="full-bundle-attestation-000001",
            release_sha="e" * 40,
            full_bundle_attestation_sha256="a" * 64,
            deployment_binding_sha256="b" * 64,
            deployment_authority_public_key_sha256="c" * 64,
            roundtrip_configuration_sha256="d" * 64,
        )
        fi_source_config, fi_source_credential = _paths(
            "wa-fi", "fi-writer-source-outbox"
        )
        fi_ack_config, fi_ack_credential = _paths("wa-fi", "fi-writer-ack-inbox")
        ir_standby_config, ir_standby_credential = _paths(
            "wa-ir", "ir-standby-ack-inbox"
        )
        ir_outbox_config, ir_outbox_credential = _paths(
            "wa-ir", "ir-durable-ack-outbox"
        )
        witness_fi_ingress_config, witness_fi_ingress_credential = _paths(
            "witness", "witness-fi-ingress"
        )
        witness_ir_egress_config, witness_ir_egress_credential = _paths(
            "witness", "witness-ir-egress"
        )
        witness_ir_ingress_config, witness_ir_ingress_credential = _paths(
            "witness", "witness-ir-ingress"
        )
        witness_fi_egress_config, witness_fi_egress_credential = _paths(
            "witness", "witness-fi-egress"
        )
        self.config = deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanConfig(
            plan_id="v2-deployment-plan-000001",
            release_sha="e" * 40,
            full_bundle_reference=self.reference,
            wa_fi=deployment.PhysicalWalV2WitnessRoundtripWaFiLocalServiceConfig(
                fi_writer_source_outbox_config_path=fi_source_config,
                fi_writer_source_outbox_credential_path=fi_source_credential,
                fi_writer_ack_inbox_config_path=fi_ack_config,
                fi_writer_ack_inbox_credential_path=fi_ack_credential,
            ),
            wa_ir=deployment.PhysicalWalV2WitnessRoundtripWaIrLocalServiceConfig(
                ir_standby_ack_inbox_config_path=ir_standby_config,
                ir_standby_ack_inbox_credential_path=ir_standby_credential,
                ir_durable_ack_outbox_config_path=ir_outbox_config,
                ir_durable_ack_outbox_credential_path=ir_outbox_credential,
            ),
            witness=deployment.PhysicalWalV2WitnessRoundtripWitnessLocalServiceConfig(
                witness_fi_ingress_config_path=witness_fi_ingress_config,
                witness_fi_ingress_credential_path=witness_fi_ingress_credential,
                witness_ir_egress_config_path=witness_ir_egress_config,
                witness_ir_egress_credential_path=witness_ir_egress_credential,
                witness_ir_ingress_config_path=witness_ir_ingress_config,
                witness_ir_ingress_credential_path=witness_ir_ingress_credential,
                witness_fi_egress_config_path=witness_fi_egress_config,
                witness_fi_egress_credential_path=witness_fi_egress_credential,
            ),
            enabled=True,
        )

    def _admission(
        self,
        manifest: bytes,
        *,
        expected_plan_id: str | None = None,
        expected_release_sha: str | None = None,
        expected_reference=None,
    ) -> deployment.PhysicalWalV2WitnessRoundtripServiceManifestAdmissionConfig:
        return deployment.PhysicalWalV2WitnessRoundtripServiceManifestAdmissionConfig(
            expected_plan_id=self.config.plan_id if expected_plan_id is None else expected_plan_id,
            expected_release_sha=(
                self.config.release_sha if expected_release_sha is None else expected_release_sha
            ),
            expected_full_bundle_reference=(
                self.reference if expected_reference is None else expected_reference
            ),
            expected_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
            enabled=True,
        )

    def test_exact_eight_role_topology_is_rendered_as_three_local_default_off_artifacts(self) -> None:
        rendered = deployment.render_physical_wal_v2_witness_roundtrip_deployment_plan(
            config=self.config
        )
        wa_fi = deployment.parse_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest(
            rendered.wa_fi_service_manifest
        )
        wa_ir = deployment.parse_physical_wal_v2_witness_roundtrip_wa_ir_service_manifest(
            rendered.wa_ir_service_manifest
        )
        witness = deployment.parse_physical_wal_v2_witness_roundtrip_witness_service_manifest(
            rendered.witness_service_manifest
        )
        self.assertEqual(
            ("fi-writer-source-outbox", "fi-writer-ack-inbox"),
            tuple(service.local_role for service in wa_fi.services),
        )
        self.assertEqual(
            ("ir-standby-ack-inbox", "ir-durable-ack-outbox"),
            tuple(service.local_role for service in wa_ir.services),
        )
        self.assertEqual(
            (
                "witness-fi-ingress",
                "witness-ir-egress",
                "witness-ir-ingress",
                "witness-fi-egress",
            ),
            tuple(service.local_role for service in witness.services),
        )
        all_roles = tuple(
            service.local_role
            for manifest in (wa_fi, wa_ir, witness)
            for service in manifest.services
        )
        self.assertEqual(8, len(all_roles))
        self.assertEqual(8, len(set(all_roles)))
        for manifest, site in ((wa_fi, "wa-fi"), (wa_ir, "wa-ir"), (witness, "witness")):
            self.assertEqual(site, manifest.site)
            self.assertEqual(
                "default-off-no-install-network-or-start-authority-v1",
                manifest.activation,
            )
            self.assertEqual(self.reference, manifest.full_bundle_reference)
            self.assertTrue(
                all(
                    service.local_config_path.startswith(f"/etc/trading-bot/v2/{site}/")
                    and service.local_credential_path.startswith(
                        f"/etc/trading-bot/v2/{site}/"
                    )
                    for service in manifest.services
                )
            )
        self.assertEqual(
            hashlib.sha256(rendered.wa_fi_service_manifest).hexdigest(),
            rendered.wa_fi_manifest_sha256,
        )
        self.assertEqual(
            hashlib.sha256(rendered.wa_ir_service_manifest).hexdigest(),
            rendered.wa_ir_manifest_sha256,
        )
        self.assertEqual(
            hashlib.sha256(rendered.witness_service_manifest).hexdigest(),
            rendered.witness_manifest_sha256,
        )
        raw_by_site = {
            "wa-fi": rendered.wa_fi_service_manifest.decode("ascii"),
            "wa-ir": rendered.wa_ir_service_manifest.decode("ascii"),
            "witness": rendered.witness_service_manifest.decode("ascii"),
        }
        self.assertNotIn("/wa-ir/", raw_by_site["wa-fi"])
        self.assertNotIn("/witness/", raw_by_site["wa-fi"])
        self.assertNotIn("/wa-fi/", raw_by_site["wa-ir"])
        self.assertNotIn("/witness/", raw_by_site["wa-ir"])
        self.assertNotIn("/wa-fi/", raw_by_site["witness"])
        self.assertNotIn("/wa-ir/", raw_by_site["witness"])
        for raw_manifest in raw_by_site.values():
            self.assertNotIn("://", raw_manifest)
            self.assertNotIn("AKIA", raw_manifest)
            self.assertNotIn("secret_access_key", raw_manifest)
            self.assertNotIn("password", raw_manifest)

    def test_peer_paths_or_secret_shaped_values_are_rejected_before_render(self) -> None:
        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "DEPLOYMENT_CONFIG_INVALID",
        ):
            deployment.render_physical_wal_v2_witness_roundtrip_deployment_plan(
                config=deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanConfig()
            )

        foreign_path = replace(
            self.config.wa_fi,
            fi_writer_source_outbox_config_path=(
                "/etc/trading-bot/v2/wa-ir/config/fi-writer-source-outbox.json"
            ),
        )
        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "DEPLOYMENT_LOCAL_PATH_INVALID",
        ):
            deployment.render_physical_wal_v2_witness_roundtrip_deployment_plan(
                config=replace(self.config, wa_fi=foreign_path)
            )

        secret_shaped = replace(
            self.config.wa_fi,
            fi_writer_source_outbox_credential_path="AKIA0123456789ABCDEF",
        )
        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "DEPLOYMENT_LOCAL_PATH_INVALID",
        ):
            deployment.render_physical_wal_v2_witness_roundtrip_deployment_plan(
                config=replace(self.config, wa_fi=secret_shaped)
            )

    def test_manifest_integrity_and_fixed_topology_reject_tampering(self) -> None:
        rendered = deployment.render_physical_wal_v2_witness_roundtrip_deployment_plan(
            config=self.config
        )
        changed = json.loads(rendered.wa_fi_service_manifest.decode("ascii"))
        changed["services"][0]["local_role"] = "witness-fi-ingress"
        stale_lock_payload = deployment._canonical(
            changed,
            code="test",
        )
        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "MANIFEST_INTEGRITY_INVALID",
        ):
            deployment.parse_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest(
                stale_lock_payload
            )

        unsigned = dict(changed)
        unsigned.pop("render_lock_sha256")
        changed["render_lock_sha256"] = hashlib.sha256(
            deployment._canonical(unsigned, code="test")
        ).hexdigest()
        topology_tamper = deployment._canonical(changed, code="test")
        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "MANIFEST_TOPOLOGY_INVALID",
        ):
            deployment.parse_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest(
                topology_tamper
            )

        injected = json.loads(rendered.wa_ir_service_manifest.decode("ascii"))
        injected["remote_endpoint"] = "https://example.invalid"
        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "MANIFEST_INVALID",
        ):
            deployment.parse_physical_wal_v2_witness_roundtrip_wa_ir_service_manifest(
                deployment._canonical(injected, code="test")
            )

        wrong_version = json.loads(rendered.witness_service_manifest.decode("ascii"))
        wrong_version["version"] = True
        unsigned = dict(wrong_version)
        unsigned.pop("render_lock_sha256")
        wrong_version["render_lock_sha256"] = hashlib.sha256(
            deployment._canonical(unsigned, code="test")
        ).hexdigest()
        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "MANIFEST_TOPOLOGY_INVALID",
        ):
            deployment.parse_physical_wal_v2_witness_roundtrip_witness_service_manifest(
                deployment._canonical(wrong_version, code="test")
            )

    def test_release_is_exactly_cross_pinned_to_the_signed_full_bundle_reference(self) -> None:
        """No renderer, parser, or local admission can relabel a bundle."""

        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "DEPLOYMENT_RELEASE_CROSS_PIN_MISMATCH",
        ):
            deployment.render_physical_wal_v2_witness_roundtrip_deployment_plan(
                config=replace(self.config, release_sha="f" * 40)
            )

        rendered = deployment.render_physical_wal_v2_witness_roundtrip_deployment_plan(
            config=self.config
        )
        relabelled = json.loads(rendered.wa_fi_service_manifest.decode("ascii"))
        # Model a caller that has all inputs required to regenerate a canonical
        # render lock, but attempts to associate release e with a reference to
        # release f.  The parser itself must fail before any named admission or
        # fresh-full-bundle bridge can use it.
        relabelled["full_bundle_reference"]["release_sha"] = "f" * 40
        unsigned = dict(relabelled)
        unsigned.pop("render_lock_sha256")
        relabelled["render_lock_sha256"] = hashlib.sha256(
            deployment._canonical(unsigned, code="test")
        ).hexdigest()
        canonical_relabelled = deployment._canonical(relabelled, code="test")
        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "MANIFEST_RELEASE_CROSS_PIN_MISMATCH",
        ):
            deployment.parse_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest(
                canonical_relabelled
            )

        mismatched_root_config = self._admission(
            canonical_relabelled,
            expected_reference=replace(self.reference, release_sha="f" * 40),
        )
        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "MANIFEST_ADMISSION_CONFIG_RELEASE_CROSS_PIN_MISMATCH",
        ):
            deployment.require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_admission(
                canonical_relabelled,
                config=mismatched_root_config,
            )

    def test_root_pinned_local_admission_rejects_valid_cross_plan_release_or_bundle_substitution(self) -> None:
        rendered = deployment.render_physical_wal_v2_witness_roundtrip_deployment_plan(
            config=self.config
        )
        admission = self._admission(rendered.wa_fi_service_manifest)
        admitted = (
            deployment.require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_admission(
                rendered.wa_fi_service_manifest,
                config=admission,
            )
        )
        self.assertEqual("wa-fi", admitted.site)

        alternate_reference = replace(
            self.reference,
            bundle_id="full-bundle-attestation-000002",
            release_sha="f" * 40,
            full_bundle_attestation_sha256="f" * 64,
        )
        alternate = deployment.render_physical_wal_v2_witness_roundtrip_deployment_plan(
            config=replace(
                self.config,
                plan_id="v2-deployment-plan-000002",
                release_sha="f" * 40,
                full_bundle_reference=alternate_reference,
            )
        )
        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "MANIFEST_ADMISSION_CROSS_PIN_MISMATCH",
        ):
            deployment.require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_admission(
                alternate.wa_fi_service_manifest,
                config=admission,
            )

        # Even if an attacker substitutes the alternate exact manifest hash,
        # the independently root-pinned public plan/release/full-bundle pins
        # still reject it.
        hash_only_substitution = replace(
            admission,
            expected_manifest_sha256=hashlib.sha256(
                alternate.wa_fi_service_manifest
            ).hexdigest(),
        )
        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "MANIFEST_ADMISSION_CROSS_PIN_MISMATCH",
        ):
            deployment.require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_admission(
                alternate.wa_fi_service_manifest,
                config=hash_only_substitution,
            )

        site_substitution = self._admission(rendered.wa_ir_service_manifest)
        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "MANIFEST_ADMISSION_INVALID",
        ):
            deployment.require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_admission(
                rendered.wa_ir_service_manifest,
                config=site_substitution,
            )

    def test_public_surface_is_pure_named_only_and_carries_no_legacy_or_secret_transport(self) -> None:
        source = inspect.getsource(deployment)
        tree = ast.parse(source)
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertFalse(
            imports
            & {
                "boto3",
                "botocore",
                "http",
                "requests",
                "socket",
                "subprocess",
                "urllib",
                "os",
                "pathlib",
            }
        )
        for forbidden in (
            "physical_wal_v2_remote_ack",
            "physical_arvan_s3_",
            "fi-to-ir",
            "ir-to-fi",
            "getattr(",
            "__getattribute__",
            "access_key_id",
            "secret_access_key",
            "endpoint_url",
            "role_selector",
        ):
            self.assertNotIn(forbidden, source)
        self.assertEqual(
            {
                "plan_id",
                "release_sha",
                "full_bundle_reference",
                "wa_fi",
                "wa_ir",
                "witness",
                "enabled",
            },
            {
                item.name
                for item in fields(
                    deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanConfig
                )
            },
        )
        self.assertEqual(
            {
                "expected_plan_id",
                "expected_release_sha",
                "expected_full_bundle_reference",
                "expected_manifest_sha256",
                "enabled",
            },
            {
                item.name
                for item in fields(
                    deployment.PhysicalWalV2WitnessRoundtripServiceManifestAdmissionConfig
                )
            },
        )
        self.assertEqual(
            {
                "bundle_id",
                "release_sha",
                "full_bundle_attestation_sha256",
                "deployment_binding_sha256",
                "deployment_authority_public_key_sha256",
                "roundtrip_configuration_sha256",
            },
            {
                item.name
                for item in fields(
                    deployment.PhysicalWalV2WitnessRoundtripPublicFullBundleReference
                )
            },
        )
        self.assertEqual(
            {
                "fi_writer_source_outbox_config_path",
                "fi_writer_source_outbox_credential_path",
                "fi_writer_ack_inbox_config_path",
                "fi_writer_ack_inbox_credential_path",
            },
            {
                item.name
                for item in fields(
                    deployment.PhysicalWalV2WitnessRoundtripWaFiLocalServiceConfig
                )
            },
        )
        self.assertEqual(
            {
                "ir_standby_ack_inbox_config_path",
                "ir_standby_ack_inbox_credential_path",
                "ir_durable_ack_outbox_config_path",
                "ir_durable_ack_outbox_credential_path",
            },
            {
                item.name
                for item in fields(
                    deployment.PhysicalWalV2WitnessRoundtripWaIrLocalServiceConfig
                )
            },
        )
        self.assertEqual(
            {
                "witness_fi_ingress_config_path",
                "witness_fi_ingress_credential_path",
                "witness_ir_egress_config_path",
                "witness_ir_egress_credential_path",
                "witness_fi_egress_config_path",
                "witness_fi_egress_credential_path",
                "witness_ir_ingress_config_path",
                "witness_ir_ingress_credential_path",
            },
            {
                item.name
                for item in fields(
                    deployment.PhysicalWalV2WitnessRoundtripWitnessLocalServiceConfig
                )
            },
        )
