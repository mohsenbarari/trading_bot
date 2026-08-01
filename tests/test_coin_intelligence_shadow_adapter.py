from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from core.config import Settings
from core.market_intelligence import shadow


class FailingService:
    def observe_implicit_commodity(self, **_kwargs):
        raise RuntimeError("must not reach the parser")


class CoinIntelligenceShadowAdapterTests(
    unittest.IsolatedAsyncioTestCase
):
    def test_shadow_feature_is_disabled_by_default(self) -> None:
        self.assertFalse(
            Settings.model_fields[
                "coin_intelligence_shadow_enabled"
            ].default
        )

    async def test_disabled_adapter_is_a_noop(self) -> None:
        with patch.object(
            shadow,
            "_configured_service",
            return_value=None,
        ), patch.object(
            shadow,
            "record_shadow_observation",
        ) as record:
            await shadow.observe_implicit_commodity_shadow(
                price=75_800,
                settlement="cash",
                current_commodity="امام",
            )

        record.assert_not_called()

    async def test_unexpected_runtime_failure_is_contained_and_counted(
        self,
    ) -> None:
        recorded = []

        with patch.object(
            shadow,
            "_configured_service",
            return_value=FailingService(),
        ), patch.object(
            shadow,
            "record_shadow_observation",
            side_effect=recorded.append,
        ):
            await shadow.observe_implicit_commodity_shadow(
                price=75_800,
                settlement="cash",
                current_commodity="امام",
            )

        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0].status, "RUNTIME_ERROR")
        self.assertEqual(recorded[0].current_commodity, "امام")
        self.assertIsNone(recorded[0].inferred_commodity)

    async def test_project_offer_uses_post_commit_cutoff(self) -> None:
        cutoff = datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc)
        event = SimpleNamespace(
            kind="OFFER",
            local_id=41,
            observed_after_commit_at_utc=cutoff,
        )
        with patch(
            "core.market_intelligence.job_queue.enqueue_project_job",
            new=AsyncMock(),
        ) as enqueue, patch.object(
            shadow,
            "record_shadow_runtime_event",
        ):
            await shadow._enqueue_project_market_event(event)

        enqueue.assert_awaited_once_with(
            kind="OFFER",
            local_id=41,
            requested_at_utc=cutoff,
        )


class CoinIntelligenceShadowSettingsTests(unittest.TestCase):
    @staticmethod
    def _settings(**overrides) -> Settings:
        values = {
            "database_url": (
                "postgresql+asyncpg://test:test@127.0.0.1/test"
            ),
            "sync_database_url": (
                "postgresql+psycopg2://test:test@127.0.0.1/test"
            ),
            "postgres_db": "test",
            "postgres_user": "test",
            "postgres_password": "test",
            "frontend_url": "http://localhost:3000",
            "redis_url": "redis://127.0.0.1:6379/15",
            "jwt_secret_key": "test-only-not-production",
        }
        values.update(overrides)
        return Settings(**values)

    def test_subfeature_cannot_start_without_top_level_shadow(self) -> None:
        for field in (
            "coin_intelligence_shadow_persist_enabled",
            "coin_intelligence_shadow_project_events_enabled",
            "coin_intelligence_shadow_numeric_v2_enabled",
            "coin_intelligence_shadow_feature_v2_enabled",
            "coin_intelligence_shadow_quality_gate_enabled",
            "coin_intelligence_shadow_low_date_v2_enabled",
            "coin_intelligence_shadow_basis_v2_enabled",
            "coin_intelligence_shadow_online_residual_v1_enabled",
            "coin_intelligence_shadow_durable_worker_enabled",
            "coin_intelligence_shadow_gemma_parser_enabled",
        ):
            with self.subTest(field=field), self.assertRaises(
                ValidationError
            ):
                self._settings(**{field: True})

    def test_runtime_bounds_fail_closed(self) -> None:
        for values in (
            {"coin_intelligence_shadow_timeout_seconds": 0},
            {"coin_intelligence_shadow_timeout_seconds": 31},
            {"coin_intelligence_shadow_max_inflight": 0},
            {"coin_intelligence_shadow_max_inflight": 1025},
            {"coin_intelligence_shadow_sample_rate": -0.01},
            {"coin_intelligence_shadow_sample_rate": 1.01},
            {"coin_intelligence_shadow_worker_poll_seconds": 0},
            {"coin_intelligence_shadow_worker_lease_seconds": 4},
            {"coin_intelligence_shadow_worker_max_attempts": 21},
            {"coin_intelligence_shadow_gemma_timeout_seconds": 181},
        ):
            with self.subTest(values=values), self.assertRaises(
                ValidationError
            ):
                self._settings(**values)

    def test_project_event_feature_dependencies_are_atomic(self) -> None:
        with self.assertRaises(ValidationError):
            self._settings(
                coin_intelligence_shadow_enabled=True,
                coin_intelligence_shadow_project_events_enabled=True,
            )
        with self.assertRaises(ValidationError):
            self._settings(
                coin_intelligence_shadow_enabled=True,
                coin_intelligence_shadow_persist_enabled=True,
                coin_intelligence_shadow_numeric_v2_enabled=True,
            )
        enabled = self._settings(
            coin_intelligence_shadow_enabled=True,
            coin_intelligence_shadow_persist_enabled=True,
            coin_intelligence_shadow_project_events_enabled=True,
            coin_intelligence_shadow_durable_worker_enabled=True,
            coin_intelligence_shadow_numeric_v2_enabled=True,
            coin_intelligence_shadow_feature_v2_enabled=True,
            coin_intelligence_shadow_quality_gate_enabled=True,
            coin_intelligence_shadow_low_date_v2_enabled=True,
            coin_intelligence_shadow_basis_v2_enabled=True,
            coin_intelligence_shadow_online_residual_v1_enabled=True,
        )
        self.assertTrue(enabled.coin_intelligence_shadow_numeric_v2_enabled)
        self.assertTrue(enabled.coin_intelligence_shadow_online_residual_v1_enabled)


if __name__ == "__main__":
    unittest.main()
