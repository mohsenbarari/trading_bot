from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from unittest import TestCase
from unittest.mock import patch

from core.market_intelligence.market_snapshot import (
    build_market_snapshot,
    publish_market_snapshot_atomically,
)
from core.market_intelligence.market_store import initialize_market_store
from scripts.relay_staging_coin_inference_snapshot import (
    StagingSnapshotRelayError,
    _inside,
    _validated_snapshot,
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
            compose.count(
                "OFFER_MODEL_PRICE_GUARD_ENABLED: "
                "${STAGING_OFFER_MODEL_PRICE_GUARD_ENABLED:-true}"
            ),
            3,
        )
        self.assertEqual(
            compose.count(
                "OFFER_MODEL_PRICE_GUARD_MAX_SNAPSHOT_AGE_SECONDS: "
                "${STAGING_COIN_INFERENCE_MAXIMUM_AGE_SECONDS:-120}"
            ),
            3,
        )
        self.assertEqual(
            compose.count(
                "source: ${STAGING_COIN_INFERENCE_SNAPSHOT_HOST_DIR:-"
                "/srv/trading-bot/staging-data/coin-intelligence}"
            ),
            3,
        )
        self.assertEqual(
            compose.count(
                "target: ${STAGING_COIN_INFERENCE_SNAPSHOT_CONTAINER_DIR:-"
                "/app/runtime/coin-inference}"
            ),
            3,
        )
        self.assertEqual(compose.count("read_only: true"), 3)
        self.assertNotIn(
            "source: ${STAGING_COIN_INFERENCE_SNAPSHOT_HOST_PATH",
            compose,
        )
        self.assertNotIn("COIN_INTELLIGENCE_INFERENCE_AUTO_SELECTION_ENABLED: true", compose)

    def test_relay_accepts_fresh_valid_no_data_but_rejects_stale_snapshot(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        with tempfile.TemporaryDirectory() as directory:
            snapshot_path = Path(directory) / "coin-rates.json"
            connection = sqlite3.connect(":memory:")
            connection.row_factory = sqlite3.Row
            try:
                initialize_market_store(connection)
                snapshot = build_market_snapshot(connection, as_of_utc=now)
                snapshot["snapshot_status"] = "NO_DATA_COIN_RATE_STATE"
                publish_market_snapshot_atomically(snapshot_path, snapshot)
            finally:
                connection.close()

            loaded = _validated_snapshot(snapshot_path, maximum_age_seconds=120)
            self.assertEqual(loaded["rates"]["estimated_count"], 0)
            self.assertEqual(loaded["rates"]["no_data_count"], 14)

            unmarked = dict(snapshot)
            unmarked["snapshot_status"] = "PARTIAL_COIN_RATE_STATE"
            publish_market_snapshot_atomically(snapshot_path, unmarked)
            with self.assertRaisesRegex(
                StagingSnapshotRelayError,
                "snapshot_no_data_state_invalid",
            ):
                _validated_snapshot(snapshot_path, maximum_age_seconds=120)

            stale = dict(snapshot)
            stale["generated_at_utc"] = (
                now - timedelta(minutes=10)
            ).isoformat().replace("+00:00", "Z")
            publish_market_snapshot_atomically(snapshot_path, stale)
            with self.assertRaisesRegex(
                StagingSnapshotRelayError,
                "snapshot_stale_or_future",
            ):
                _validated_snapshot(snapshot_path, maximum_age_seconds=120)

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
        self.assertIn(
            "STAGING_OFFER_MODEL_PRICE_GUARD_ENABLED:-true",
            deploy,
        )
        self.assertIn("publish_coin_intelligence_snapshot.py\" check", deploy)
        self.assertIn("FRESH_NO_DATA", deploy)
        self.assertIn(
            "coin inference auto-selection must remain disabled in staging",
            deploy,
        )

    def test_deploy_check_rejects_auto_selection_before_snapshot_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stub_dir = Path(directory)
            for name in ("docker", "nginx"):
                executable = stub_dir / name
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                executable.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{stub_dir}:{environment.get('PATH', '')}",
                    "STAGING_COIN_INFERENCE_PREVIEW_ENABLED": "true",
                    "STAGING_COIN_INFERENCE_SELECTION_ENABLED": "true",
                    "STAGING_COIN_INFERENCE_AUTO_SELECTION_ENABLED": "true",
                }
            )
            completed = subprocess.run(
                ["bash", str(ROOT / "scripts/deploy_staging.sh"), "check"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(
            "coin inference auto-selection must remain disabled in staging",
            completed.stderr,
        )

    def test_deploy_check_rejects_any_freshness_override_above_120_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stub_dir = Path(directory)
            for name in ("docker", "nginx"):
                executable = stub_dir / name
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                executable.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{stub_dir}:{environment.get('PATH', '')}",
                    "STAGING_COIN_INFERENCE_MAXIMUM_AGE_SECONDS": "121",
                }
            )
            completed = subprocess.run(
                ["bash", str(ROOT / "scripts/deploy_staging.sh"), "check"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(
            "staging coin inference maximum age must remain exactly 120 seconds",
            completed.stderr,
        )

    def test_tracked_staging_publish_services_use_explicit_no_data_flag(self) -> None:
        template = (
            ROOT
            / "deploy/coin_intelligence/systemd/coin-intelligence-staging-snapshot-publish.service.template"
        ).read_text(encoding="utf-8")
        drop_in = (
            ROOT
            / "deploy/coin_intelligence/systemd/coin-intelligence-staging-snapshot-publish.service.d/host-python-toman.conf"
        ).read_text(encoding="utf-8")
        flag = "--publish-staging-no-data-snapshot"
        self.assertEqual(template.count(flag), 1)
        self.assertEqual(drop_in.count(flag), 1)
        confirmation = "--environment staging --confirm publish-staging-no-data-snapshot"
        self.assertIn(confirmation, template)
        self.assertIn(confirmation, drop_in)
        self.assertNotIn("SuccessExitStatus=3", template)
        self.assertNotIn("SuccessExitStatus=3", drop_in)
