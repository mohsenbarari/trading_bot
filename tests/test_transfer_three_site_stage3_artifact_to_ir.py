from pathlib import Path
import unittest

from scripts.transfer_three_site_stage3_artifact_to_ir import (
    Stage3ArtifactTransferError,
    artifact_spec,
    confirmation_phrase,
    require_transfer_inventory_stage,
)


class Stage3ArtifactTransferTests(unittest.TestCase):
    release = "0e63a7ec" + "1" * 32
    campaign = "fd34231d-f52e-498a-aab4-438c99d88fc5"

    def test_release_bundle_destination_is_campaign_scoped(self):
        name, destination = artifact_spec(
            Path("/tmp/three-site-stage3-0e63a7ec.bundle"),
            release_sha=self.release,
            campaign_id=self.campaign,
        )
        self.assertEqual(name, "three-site-stage3-0e63a7ec.bundle")
        self.assertEqual(
            destination,
            Path("/tmp/stage3-0e63a7ec-fd34231d-transfer/three-site-stage3-0e63a7ec.bundle"),
        )

    def test_postgres_archive_is_allowed(self):
        name, _destination = artifact_spec(
            Path("/tmp/trading-bot-postgres-boottime-0e63a7ec.tar.zst"),
            release_sha=self.release,
            campaign_id=self.campaign,
        )
        self.assertEqual(name, "trading-bot-postgres-boottime-0e63a7ec.tar.zst")

    def test_application_archive_is_allowed(self):
        name, destination = artifact_spec(
            Path("/tmp/trading-bot-three-site-app-0e63a7ec.tar.zst"),
            release_sha=self.release,
            campaign_id=self.campaign,
        )
        self.assertEqual(name, "trading-bot-three-site-app-0e63a7ec.tar.zst")
        self.assertEqual(
            destination,
            Path(
                "/tmp/stage3-0e63a7ec-fd34231d-transfer/"
                "trading-bot-three-site-app-0e63a7ec.tar.zst"
            ),
        )

    def test_third_party_archive_is_allowed(self):
        name, destination = artifact_spec(
            Path("/tmp/trading-bot-three-site-third-party-0e63a7ec.tar.zst"),
            release_sha=self.release,
            campaign_id=self.campaign,
        )
        self.assertEqual(
            name, "trading-bot-three-site-third-party-0e63a7ec.tar.zst"
        )
        self.assertEqual(
            destination,
            Path(
                "/tmp/stage3-0e63a7ec-fd34231d-transfer/"
                "trading-bot-three-site-third-party-0e63a7ec.tar.zst"
            ),
        )

    def test_other_release_or_filename_is_rejected(self):
        for name in (
            "three-site-stage3-deadbeef.bundle",
            "trading-bot-postgres-boottime-0e63a7ec.tar",
            "trading-bot-three-site-app-deadbeef.tar.zst",
            "trading-bot-three-site-third-party-deadbeef.tar.zst",
            "planned-inventory.json",
        ):
            with self.subTest(name=name), self.assertRaises(Stage3ArtifactTransferError):
                artifact_spec(
                    Path("/tmp") / name,
                    release_sha=self.release,
                    campaign_id=self.campaign,
                )

    def test_confirmation_binds_campaign_role_and_hash(self):
        digest = "a" * 64
        self.assertEqual(
            confirmation_phrase(self.campaign, "webapp-ir", digest),
            f"transfer-stage3-artifact:{self.campaign}:webapp-ir:{digest}",
        )

    def test_latest_approved_inventory_stage_is_reusable_for_transfer(self):
        self.assertEqual(
            require_transfer_inventory_stage({"inventory_stage": "planned"}),
            "planned",
        )
        self.assertEqual(
            require_transfer_inventory_stage({"inventory_stage": "provisioned"}),
            "provisioned",
        )
        for stage in ("", "draft", "production"):
            with self.subTest(stage=stage), self.assertRaises(
                Stage3ArtifactTransferError
            ):
                require_transfer_inventory_stage({"inventory_stage": stage})


if __name__ == "__main__":
    unittest.main()
