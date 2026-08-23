from pathlib import Path
import unittest

from core.config import Settings
from core.telegram_bot_runtime_role import (
    TELEGRAM_BOT_RUNTIME_ROLE_ALL,
    TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR,
    TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY,
)
from core.telegram_dispatch_latency_pool import (
    POOL_SCHEMA_VERSION,
    POSTGRES_ADMIN_RESERVE,
    PRODUCTION_ALL_MAX_OVERFLOW,
    PRODUCTION_ALL_POOL_SIZE,
    PRODUCTION_EXECUTOR_MAX_OVERFLOW,
    PRODUCTION_EXECUTOR_POOL_SIZE,
    RECOMMENDED_PRIMARY_MAX_OVERFLOW,
    RECOMMENDED_PRIMARY_POOL_SIZE,
    SESSIONS_PER_SLOT,
    compose_pool_for_bot_role,
    configured_service_ceilings,
    locked_telegram_dispatch_pools,
    production_role_pools,
    required_connections_for_role,
    role_ceilings_fit_postgres,
    slot_count_for_role,
)
from tests.test_deployment_surface_guard import compose_service_block
from tests.test_telegram_delivery_queue_config import _settings


class TelegramDispatchLatencyPoolTests(unittest.TestCase):
    def test_role_pools_cover_slot_math_and_stay_under_postgres_budget(self):
        lock = locked_telegram_dispatch_pools()
        roles = {role.role: role for role in lock.roles}

        self.assertEqual(lock.schema_version, POOL_SCHEMA_VERSION)
        self.assertEqual(lock.evidence_kind, "code_derived_role_pool_lock")
        self.assertFalse(lock.live_wait_samples_collected)
        self.assertEqual(SESSIONS_PER_SLOT, 1)
        self.assertEqual(slot_count_for_role(TELEGRAM_BOT_RUNTIME_ROLE_ALL), 9)
        self.assertEqual(slot_count_for_role(TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY), 0)
        self.assertEqual(slot_count_for_role(TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR), 9)
        self.assertEqual(required_connections_for_role(TELEGRAM_BOT_RUNTIME_ROLE_ALL), 25)
        self.assertEqual(
            required_connections_for_role(TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY),
            12,
        )
        self.assertEqual(
            required_connections_for_role(TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR),
            19,
        )

        self.assertEqual(roles[TELEGRAM_BOT_RUNTIME_ROLE_ALL].configured_ceiling, 25)
        self.assertEqual(roles[TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY].configured_ceiling, 20)
        self.assertEqual(
            roles[TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR].configured_ceiling,
            25,
        )
        for role in lock.roles:
            self.assertGreaterEqual(role.configured_ceiling, role.required_connections)
        self.assertTrue(role_ceilings_fit_postgres(production_role_pools()))
        self.assertEqual(lock.postgres_max_connections, 500)
        self.assertEqual(lock.postgres_admin_reserve, POSTGRES_ADMIN_RESERVE)
        ceilings = configured_service_ceilings()
        self.assertLessEqual(
            ceilings["app"]
            + ceilings["sync"]
            + ceilings["bot_primary"]
            + ceilings["bot_executor"],
            lock.postgres_max_connections - POSTGRES_ADMIN_RESERVE,
        )
        self.assertEqual(
            Settings.model_fields["db_pool_size"].default,
            PRODUCTION_ALL_POOL_SIZE,
        )
        self.assertEqual(
            Settings.model_fields["db_max_overflow"].default,
            PRODUCTION_ALL_MAX_OVERFLOW,
        )

    def test_settings_defaults_stay_valid_for_the_combined_bot_role(self):
        settings = _settings()
        self.assertEqual(settings.db_pool_size, 15)
        self.assertEqual(settings.db_max_overflow, 10)
        self.assertEqual(settings.postgres_max_connections, 500)
        self.assertGreaterEqual(
            settings.db_pool_size + settings.db_max_overflow,
            required_connections_for_role(TELEGRAM_BOT_RUNTIME_ROLE_ALL),
        )

    def test_compose_keeps_calculated_ceilings_for_bot_roles(self):
        repo_root = Path(__file__).resolve().parents[1]
        bot = compose_service_block(repo_root / "docker-compose.yml", "bot")
        executor = compose_service_block(
            repo_root / "docker-compose.yml",
            "bot_executor",
        )
        db_block = compose_service_block(repo_root / "docker-compose.yml", "db")

        self.assertIn(f"DB_POOL_SIZE: ${{DB_BOT_POOL_SIZE:-{PRODUCTION_ALL_POOL_SIZE}}}", bot)
        self.assertIn(
            f"DB_MAX_OVERFLOW: ${{DB_BOT_MAX_OVERFLOW:-{PRODUCTION_ALL_MAX_OVERFLOW}}}",
            bot,
        )
        self.assertIn("Role `all` defaults to 15+10", bot)
        self.assertIn("DB_BOT_POOL_SIZE=12", bot)
        self.assertEqual(compose_pool_for_bot_role("primary")["db_pool_size"], 12)
        self.assertEqual(compose_pool_for_bot_role("primary")["db_max_overflow"], 8)
        self.assertIn(
            "DB_POOL_SIZE: ${DB_EXECUTOR_POOL_SIZE:-"
            f"{PRODUCTION_EXECUTOR_POOL_SIZE}}}",
            executor,
        )
        self.assertIn(
            "DB_MAX_OVERFLOW: ${DB_EXECUTOR_MAX_OVERFLOW:-"
            f"{PRODUCTION_EXECUTOR_MAX_OVERFLOW}}}",
            executor,
        )
        self.assertIn("Role `executor` ceiling 25", executor)
        self.assertIn("max_connections=500", db_block)
        self.assertIn(str(RECOMMENDED_PRIMARY_POOL_SIZE), bot)
        self.assertIn(str(RECOMMENDED_PRIMARY_MAX_OVERFLOW), bot)
        self.assertNotIn("bot_publishers:", (repo_root / "docker-compose.yml").read_text())
        staging_bot = compose_service_block(
            repo_root / "deploy/staging/docker-compose.staging.yml",
            "bot",
        )
        self.assertIn("DB_POOL_SIZE: ${STAGING_DB_BOT_POOL_SIZE:-15}", staging_bot)
        self.assertIn("DB_MAX_OVERFLOW: ${STAGING_DB_BOT_MAX_OVERFLOW:-10}", staging_bot)

    def test_pool_document_does_not_invent_live_waits(self):
        text = Path(
            "docs/TELEGRAM_DISPATCH_LATENCY_POOL_20260823.md"
        ).read_text(encoding="utf-8")

        self.assertIn("code_derived_role_pool_lock", text)
        self.assertIn("destination_next", text)
        self.assertNotIn("p50=", text.lower())
        self.assertNotIn("queue pool timeout", text.lower())


if __name__ == "__main__":
    unittest.main()
