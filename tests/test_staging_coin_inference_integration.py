from __future__ import annotations

import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from scripts.relay_staging_coin_inference_snapshot import (
    StagingSnapshotRelayError,
    _inside,
)
from scripts.sync_staging_commodity_catalog import (
    ManifestCommodity,
    StagingCatalogSyncError,
    build_catalog_plan,
    normalize_manifest,
    validate_staging_target,
)


ROOT = Path(__file__).resolve().parents[1]


class StagingCommodityCatalogContractTests(TestCase):
    def test_manifest_and_plan_add_only_missing_natural_keys(self) -> None:
        manifest = normalize_manifest(
            {
                "schema_version": 1,
                "source": "production-read-only",
                "commodities": [
                    {"name": "امام", "aliases": ["امام", "امامی"]},
                    {"name": "بهار", "aliases": ["بهار", "آزادی"]},
                ],
            }
        )
        plan = build_catalog_plan(
            manifest,
            existing_commodities={"امام": {"امامی"}},
            existing_alias_owners={"امامی": "امام"},
        )
        self.assertEqual([item.name for item in plan.commodities_to_add], ["بهار"])
        self.assertEqual(
            set(plan.aliases_to_add),
            {("بهار", "بهار"), ("بهار", "آزادی"), ("امام", "امام")},
        )

    def test_conflicting_alias_owner_fails_closed(self) -> None:
        with self.assertRaisesRegex(StagingCatalogSyncError, "staging_alias_owner_conflict"):
            build_catalog_plan(
                (ManifestCommodity(name="بهار", aliases=("مشترک",)),),
                existing_commodities={"امام": {"مشترک"}},
                existing_alias_owners={"مشترک": "امام"},
            )

    def test_alias_cannot_shadow_another_commodity_name(self) -> None:
        with self.assertRaisesRegex(
            StagingCatalogSyncError,
            "manifest_alias_commodity_name_conflict",
        ):
            normalize_manifest(
                {
                    "schema_version": 1,
                    "source": "production-read-only",
                    "commodities": [
                        {"name": "امام", "aliases": ["بهار"]},
                        {"name": "بهار", "aliases": []},
                    ],
                }
            )

    def test_target_requires_staging_environment_and_database(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "staging",
                "SERVER_MODE": "iran",
                "DATABASE_URL": "postgresql+asyncpg://u:p@db/trading_bot_staging",
            },
            clear=False,
        ):
            validate_staging_target(
                "trading_bot_staging",
                expected_server_mode="iran",
            )
            with self.assertRaisesRegex(StagingCatalogSyncError, "not_staging"):
                validate_staging_target(
                    "trading_bot",
                    expected_server_mode="iran",
                )

    def test_catalog_import_rejects_non_authority_peer(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "staging",
                "SERVER_MODE": "foreign",
                "DATABASE_URL": "postgresql+asyncpg://u:p@db/trading_bot_staging",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(
                StagingCatalogSyncError,
                "requires_iran_authority",
            ):
                validate_staging_target(
                    "trading_bot_staging",
                    expected_server_mode="iran",
                )


class StagingSnapshotRelayContractTests(TestCase):
    def test_destinations_must_stay_under_staging_root(self) -> None:
        root, path = _inside(
            "/srv/trading-bot/staging-data/coin-intelligence",
            "/srv/trading-bot/staging-data/coin-intelligence/coin-rates.json",
            field="local_snapshot",
        )
        self.assertEqual(path.parent, root)
        with self.assertRaisesRegex(StagingSnapshotRelayError, "outside_root"):
            _inside(
                "/srv/trading-bot/staging-data/coin-intelligence",
                "/srv/trading-bot/production-data/coin-rates.json",
                field="local_snapshot",
            )

    def test_staging_compose_mounts_snapshot_read_only_for_user_surfaces(self) -> None:
        compose = (ROOT / "deploy/staging/docker-compose.staging.yml").read_text()
        self.assertEqual(
            compose.count(
                "COIN_INTELLIGENCE_INFERENCE_SELECTION_ENABLED: "
                "${STAGING_COIN_INFERENCE_SELECTION_ENABLED:-false}"
            ),
            3,
        )
        self.assertEqual(
            compose.count("source: ${STAGING_COIN_INFERENCE_SNAPSHOT_HOST_PATH:-/dev/null}"),
            3,
        )
        self.assertEqual(compose.count("read_only: true"), 3)
        self.assertNotIn("COIN_INTELLIGENCE_INFERENCE_AUTO_SELECTION_ENABLED: true", compose)

    def test_deploy_defaults_to_confirmation_only_and_freshness_gate(self) -> None:
        deploy = (ROOT / "scripts/deploy_staging.sh").read_text()
        self.assertIn(
            "STAGING_COIN_INFERENCE_SELECTION_ENABLED:-true",
            deploy,
        )
        self.assertIn(
            "STAGING_COIN_INFERENCE_AUTO_SELECTION_ENABLED:-false",
            deploy,
        )
        self.assertIn("publish_coin_intelligence_snapshot.py\" check", deploy)
