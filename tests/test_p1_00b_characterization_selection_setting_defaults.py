"""Characterization tests for _selection_inference_for_missing_commodity default setting resolution.

Context:
  - Gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
  - Seed: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184
  - Stage Card: docs/refactor/two-server/section-1/stages/P1-00b.md
  - Assignment ID: WA-P1-00B-FREEZE-20260904-001
  - Scope: Helper-level behavior freeze for _selection_inference_for_missing_commodity
    in api/routers/offers.py only. No claim of handler seed closure, Stage
    acceptance, execution evidence, or production readiness.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from datetime import datetime, timezone
from typing import Callable
import unittest
from unittest.mock import AsyncMock, Mock, patch

from api.routers import offers as offers_module
from core.market_intelligence.coin_catalog import (
    CatalogCoinCommodityCandidate,
    CatalogCoinCommodityEditCandidate,
    CatalogCoinCommodityInference,
)
from core.market_intelligence.coin_inference_shadow import (
    CoinInferenceShadowObservation,
)


SYNTHETIC_AUTHORITY_PATH = "/var/run/market/authority/snapshot.json"
FIXED_NOW_UTC = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)


class StrictFakeSession:
    """Fake AsyncSession exposing strictly commit and rollback async methods."""

    def __init__(self) -> None:
        self.commit = AsyncMock(name="db.commit")
        self.rollback = AsyncMock(name="db.rollback")


class IsolatedAbsentSettings:
    """Settings instance where target setting is genuinely absent."""

    def __init__(self, on_access: Callable[[str], None]) -> None:
        self._on_access = on_access

    def __getattr__(self, name: str) -> None:
        if name == "coin_intelligence_inference_auto_selection_enabled":
            self._on_access(name)
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute {name!r}"
            )
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute {name!r}"
        )


class IsolatedRaisingPropertySettings:
    """Settings instance with property getter raising synthetic AttributeError."""

    def __init__(
        self,
        error_instance: AttributeError,
        on_access: Callable[[str], None],
    ) -> None:
        self._error_instance = error_instance
        self._on_access = on_access

    @property
    def coin_intelligence_inference_auto_selection_enabled(self) -> bool:
        self._on_access("coin_intelligence_inference_auto_selection_enabled")
        raise self._error_instance


class SelectionSettingDefaultsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Behavior-freeze tests for missing and AttributeError auto-selection setting defaulting."""

    async def _run_selection_default_scenario(
        self,
        *,
        settings_obj: object,
        events: list[str],
        expected_setting_event: str,
    ) -> None:
        synthetic_candidate = CatalogCoinCommodityCandidate(
            commodity_id=101,
            commodity_code="EMAMI_86",
            commodity_name="Emami Coin 86",
            center_project_price=100_000_000,
            lower_project_price=99_000_000,
            upper_project_price=101_000_000,
            confidence="HIGH",
            distance_to_center_relative=0.005,
        )
        synthetic_edit_candidate = CatalogCoinCommodityEditCandidate(
            commodity_id=102,
            commodity_code="BAHAR_86",
            commodity_name="Bahar Azadi 86",
        )
        synthetic_decision = CatalogCoinCommodityInference(
            status="MATCHED",
            settlement_term="CASH",
            candidates=(synthetic_candidate,),
            snapshot_generated_at_utc="2026-09-10T11:59:55Z",
            snapshot_receipt="sha256-synthetic-receipt",
            reason=None,
        )
        synthetic_observation = CoinInferenceShadowObservation(
            decision_key="synthetic-decision-key-12345",
            decision=synthetic_decision,
            edit_candidates=(synthetic_edit_candidate,),
        )

        fake_db = StrictFakeSession()
        fake_reader = Mock(name="SyntheticProductSnapshotReader")

        bool_call_args: list[object] = []

        def resolver_side_effect(s: object) -> str:
            events.append("path_resolution")
            return SYNTHETIC_AUTHORITY_PATH

        mock_resolver = Mock(
            side_effect=resolver_side_effect,
            name="configured_product_snapshot_authority_path",
        )

        mock_datetime = Mock(spec=datetime, name="datetime")

        def clock_side_effect(tz: object = None) -> datetime:
            events.append("clock")
            return FIXED_NOW_UTC

        mock_datetime.now = Mock(
            side_effect=clock_side_effect,
            name="datetime.now",
        )

        def reader_factory_side_effect() -> object:
            events.append("reader_construction")
            return fake_reader

        mock_reader_factory = Mock(
            side_effect=reader_factory_side_effect,
            name="_configured_product_snapshot_reader",
        )

        async def observer_side_effect(*args: object, **kwargs: object) -> CoinInferenceShadowObservation:
            events.append("observer_completion")
            return synthetic_observation

        mock_observer = AsyncMock(
            side_effect=observer_side_effect,
            name="observe_coin_inference_shadow",
        )

        async def commit_side_effect() -> None:
            events.append("commit_completion")

        fake_db.commit.side_effect = commit_side_effect

        real_formatter = offers_module._catalog_inference_shadow_payload
        captured_formatter_payloads: list[dict[str, object]] = []

        def formatter_spy(
            decision: object,
            *,
            decision_key: str,
            edit_candidates: object = (),
            mode: str = "SHADOW_ONLY",
        ) -> dict[str, object]:
            ret = real_formatter(
                decision,
                decision_key=decision_key,
                edit_candidates=edit_candidates,
                mode=mode,
            )
            events.append("formatter_completion")
            captured_formatter_payloads.append(ret)
            mock_formatter.return_value = ret
            return ret

        mock_formatter = Mock(
            side_effect=formatter_spy,
            name="_catalog_inference_shadow_payload",
        )

        real_bool = bool

        def bool_spy(arg: object) -> bool:
            events.append("bool_evaluation")
            bool_call_args.append(arg)
            return real_bool(arg)

        mock_log_trading_event = Mock(name="log_trading_event")

        had_bool_override = "bool" in vars(offers_module)
        orig_bool_val = getattr(offers_module, "bool", None)
        setattr(offers_module, "bool", bool_spy)

        try:
            with (
                patch.object(offers_module, "settings", settings_obj),
                patch.object(
                    offers_module,
                    "configured_product_snapshot_authority_path",
                    mock_resolver,
                ),
                patch.object(offers_module, "datetime", mock_datetime),
                patch.object(
                    offers_module,
                    "_configured_product_snapshot_reader",
                    mock_reader_factory,
                ),
                patch.object(
                    offers_module,
                    "observe_coin_inference_shadow",
                    mock_observer,
                ),
                patch.object(
                    offers_module,
                    "_catalog_inference_shadow_payload",
                    mock_formatter,
                ),
                patch.object(
                    offers_module,
                    "log_trading_event",
                    mock_log_trading_event,
                ),
            ):
                result = await offers_module._selection_inference_for_missing_commodity(
                    fake_db,
                    price=100_000_000,
                    settlement_type="cash",
                    low_date_hint=False,
                    pack_hint=False,
                )
                events.append("helper_return")
        finally:
            if had_bool_override:
                setattr(offers_module, "bool", orig_bool_val)
            else:
                delattr(offers_module, "bool")

        # Assert temporary bool override is removed/restored afterward
        self.assertEqual("bool" in vars(offers_module), had_bool_override)
        if had_bool_override:
            self.assertIs(getattr(offers_module, "bool", None), orig_bool_val)

        # Causal event sequence assertion
        expected_events = [
            "path_resolution",
            "clock",
            expected_setting_event,
            "bool_evaluation",
            "reader_construction",
            "observer_completion",
            "commit_completion",
            "formatter_completion",
            "helper_return",
        ]
        self.assertEqual(events, expected_events)

        # Boundary call and identity assertions
        mock_resolver.assert_called_once_with(settings_obj)
        self.assertIs(mock_resolver.call_args[0][0], settings_obj)

        mock_datetime.now.assert_called_once_with(timezone.utc)
        self.assertIs(mock_datetime.now.call_args[0][0], timezone.utc)

        self.assertEqual(len(bool_call_args), 1)
        self.assertIs(bool_call_args[0], False)

        mock_reader_factory.assert_called_once_with()

        mock_observer.assert_called_once_with(
            fake_db,
            snapshot_path=SYNTHETIC_AUTHORITY_PATH,
            submitted_project_price=100_000_000,
            settlement_term="CASH",
            source_surface="WEBAPP",
            now_utc=FIXED_NOW_UTC,
            candidate_scope="ALL",
            force_confirmation=True,
            snapshot_reader=fake_reader,
        )
        mock_observer.assert_awaited_once_with(
            fake_db,
            snapshot_path=SYNTHETIC_AUTHORITY_PATH,
            submitted_project_price=100_000_000,
            settlement_term="CASH",
            source_surface="WEBAPP",
            now_utc=FIXED_NOW_UTC,
            candidate_scope="ALL",
            force_confirmation=True,
            snapshot_reader=fake_reader,
        )
        obs_args, obs_kwargs = mock_observer.await_args
        self.assertIs(obs_args[0], fake_db)
        self.assertEqual(obs_kwargs["snapshot_path"], SYNTHETIC_AUTHORITY_PATH)
        self.assertEqual(obs_kwargs["submitted_project_price"], 100_000_000)
        self.assertEqual(obs_kwargs["settlement_term"], "CASH")
        self.assertEqual(obs_kwargs["source_surface"], "WEBAPP")
        self.assertIs(obs_kwargs["now_utc"], FIXED_NOW_UTC)
        self.assertEqual(obs_kwargs["candidate_scope"], "ALL")
        self.assertIs(obs_kwargs["force_confirmation"], True)
        self.assertIs(obs_kwargs["snapshot_reader"], fake_reader)

        fake_db.commit.assert_called_once_with()
        fake_db.commit.assert_awaited_once_with()
        self.assertEqual(fake_db.commit.await_count, 1)

        self.assertEqual(fake_db.rollback.call_count, 0)
        self.assertEqual(fake_db.rollback.await_count, 0)

        mock_log_trading_event.assert_not_called()

        mock_formatter.assert_called_once_with(
            synthetic_observation.decision,
            decision_key=synthetic_observation.decision_key,
            edit_candidates=synthetic_observation.edit_candidates,
            mode="SELECTABLE",
        )
        fmt_args, fmt_kwargs = mock_formatter.call_args
        self.assertIs(fmt_args[0], synthetic_observation.decision)
        self.assertIs(fmt_kwargs["decision_key"], synthetic_observation.decision_key)
        self.assertIs(fmt_kwargs["edit_candidates"], synthetic_observation.edit_candidates)
        self.assertEqual(fmt_kwargs["mode"], "SELECTABLE")
        self.assertEqual(len(captured_formatter_payloads), 1)
        self.assertIs(result, captured_formatter_payloads[0])
        self.assertIs(result, mock_formatter.return_value)

        expected_payload: dict[str, object] = {
            "mode": "SELECTABLE",
            "status": "MATCHED",
            "decision_key": "synthetic-decision-key-12345",
            "snapshot_generated_at_utc": "2026-09-10T11:59:55Z",
            "snapshot_receipt": "sha256-synthetic-receipt",
            "reason": None,
            "candidates": [
                {
                    "commodity_id": 101,
                    "commodity_code": "EMAMI_86",
                    "commodity_name": "Emami Coin 86",
                    "center_project_price": 100_000_000,
                    "lower_project_price": 99_000_000,
                    "upper_project_price": 101_000_000,
                    "confidence": "HIGH",
                    "distance_to_center_relative": 0.005,
                }
            ],
            "edit_candidates": [
                {
                    "commodity_id": 102,
                    "commodity_code": "BAHAR_86",
                    "commodity_name": "Bahar Azadi 86",
                }
            ],
        }
        self.assertEqual(result, expected_payload)

    async def test_auto_selection_attribute_absent_defaults_to_false(self) -> None:
        events: list[str] = []
        setting_evaluations: list[str] = []

        def on_setting_access(name: str) -> None:
            events.append("attribute_lookup")
            setting_evaluations.append(name)

        settings_obj = IsolatedAbsentSettings(on_access=on_setting_access)

        await self._run_selection_default_scenario(
            settings_obj=settings_obj,
            events=events,
            expected_setting_event="attribute_lookup",
        )

        self.assertEqual(
            setting_evaluations,
            ["coin_intelligence_inference_auto_selection_enabled"],
        )

    async def test_auto_selection_property_attribute_error_defaults_to_false(self) -> None:
        events: list[str] = []
        setting_evaluations: list[str] = []
        synthetic_error = AttributeError("synthetic auto-selection getter failure")

        def on_setting_access(name: str) -> None:
            events.append("property_getter")
            setting_evaluations.append(name)

        settings_obj = IsolatedRaisingPropertySettings(
            error_instance=synthetic_error,
            on_access=on_setting_access,
        )

        await self._run_selection_default_scenario(
            settings_obj=settings_obj,
            events=events,
            expected_setting_event="property_getter",
        )

        self.assertEqual(
            setting_evaluations,
            ["coin_intelligence_inference_auto_selection_enabled"],
        )
