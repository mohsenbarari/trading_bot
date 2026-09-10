"""Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Seed: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184
Stage card: P1-00b
Assignment ID: WA-P1-00B-FREEZE-20260904-001

Characterize helper-level early configuration exits and real authority-resolver
failure paths of the real api.routers.offers._selection_inference_for_missing_commodity
under P1-00b. This evidence is strictly limited to helper-level early exits and
resolver failures, not handler/seed or Stage closure.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from contextlib import contextmanager
from typing import Any, Iterator
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import api.routers.offers as offers_module
from api.routers.offers import _selection_inference_for_missing_commodity
from core.market_intelligence.product_snapshot_reader import (
    ProductSnapshotUnavailable,
    configured_product_snapshot_authority_path,
)

EXPECTED_UNCONFIGURED_PAYLOAD: dict[str, Any] = {
    "mode": "SELECTABLE",
    "status": "ABSTAIN",
    "decision_key": None,
    "snapshot_generated_at_utc": None,
    "snapshot_receipt": None,
    "reason": "SNAPSHOT_PATH_UNCONFIGURED",
    "candidates": [],
}


class SyntheticResolverError(RuntimeError):
    """Synthetic exception representing authority path resolution failure."""


class SyntheticModeStringConversionError(RuntimeError):
    """Synthetic ordinary exception sentinel for mode string conversion failure."""


class SyntheticAuthorityPathPropertyError(RuntimeError):
    """Synthetic ordinary exception sentinel for authority path property failure."""


class SyntheticAuthorityPathBoolError(RuntimeError):
    """Synthetic ordinary exception sentinel for authority path __bool__ failure."""


class SyntheticAuthorityPathStrError(RuntimeError):
    """Synthetic ordinary exception sentinel for authority path __str__ failure."""


class InstrumentedAuthorityPath:
    """Synthetic path object tracking __bool__ and __str__ invocations and raising sentinels."""

    def __init__(
        self,
        event_log: list[str],
        *,
        bool_exc: Exception | None = None,
        str_exc: Exception | None = None,
    ) -> None:
        self._event_log = event_log
        self._bool_exc = bool_exc
        self._str_exc = str_exc

    def __bool__(self) -> bool:
        self._event_log.append("path_value:bool")
        if self._bool_exc is not None:
            raise self._bool_exc
        return True

    def __str__(self) -> str:
        self._event_log.append("path_value:str")
        if self._str_exc is not None:
            raise self._str_exc
        return "/synthetic/instrumented_path.json"


class FailingStringMode:
    """Synthetic truthy mode value whose __str__ raises an ordinary exception sentinel."""

    def __init__(self, exc: Exception, event_log: list[str]) -> None:
        self._exc = exc
        self._event_log = event_log

    def __bool__(self) -> bool:
        self._event_log.append("mode_value:bool")
        return True

    def __str__(self) -> str:
        self._event_log.append("mode_value:str")
        raise self._exc


class InstrumentedSettings:
    """Instrumented synthetic settings tracking ordered property access and forbidden reads."""

    def __init__(
        self,
        *,
        product_estimator_snapshot_mode: Any = "LEGACY",
        coin_intelligence_inference_snapshot_path: Any = "",
        product_estimator_private_primary_snapshot_path: Any = "",
        coin_intelligence_inference_auto_selection_enabled: Any = False,
        event_log: list[str] | None = None,
    ) -> None:
        self._mode = product_estimator_snapshot_mode
        self._legacy_path = coin_intelligence_inference_snapshot_path
        self._private_primary_path = product_estimator_private_primary_snapshot_path
        self._auto_selection = coin_intelligence_inference_auto_selection_enabled
        self.access_log: list[str] = event_log if event_log is not None else []
        self.forbidden_accesses: list[str] = []

    @property
    def product_estimator_snapshot_mode(self) -> Any:
        self.access_log.append("product_estimator_snapshot_mode")
        if isinstance(self._mode, BaseException):
            raise self._mode
        return self._mode

    @property
    def coin_intelligence_inference_snapshot_path(self) -> Any:
        self.access_log.append("coin_intelligence_inference_snapshot_path")
        if isinstance(self._legacy_path, BaseException):
            raise self._legacy_path
        return self._legacy_path

    @property
    def product_estimator_private_primary_snapshot_path(self) -> Any:
        self.access_log.append("product_estimator_private_primary_snapshot_path")
        if isinstance(self._private_primary_path, BaseException):
            raise self._private_primary_path
        return self._private_primary_path

    @property
    def coin_intelligence_inference_auto_selection_enabled(self) -> Any:
        self.access_log.append("coin_intelligence_inference_auto_selection_enabled")
        if isinstance(self._auto_selection, BaseException):
            raise self._auto_selection
        return self._auto_selection

    def __getattr__(self, name: str) -> Any:
        self.forbidden_accesses.append(name)
        self.access_log.append(f"forbidden:{name}")
        raise AttributeError(f"Unexpected settings attribute access: {name!r}")


class SyntheticSettings:
    """Isolated synthetic settings container for selection configuration exit tests."""

    def __init__(
        self,
        *,
        product_estimator_snapshot_mode: str = "LEGACY",
        coin_intelligence_inference_snapshot_path: str = "",
        product_estimator_private_primary_snapshot_path: str = "",
        coin_intelligence_inference_auto_selection_enabled: bool = False,
    ) -> None:
        self.product_estimator_snapshot_mode = product_estimator_snapshot_mode
        self.coin_intelligence_inference_snapshot_path = (
            coin_intelligence_inference_snapshot_path
        )
        self.product_estimator_private_primary_snapshot_path = (
            product_estimator_private_primary_snapshot_path
        )
        self.coin_intelligence_inference_auto_selection_enabled = (
            coin_intelligence_inference_auto_selection_enabled
        )


class StrictFakeAsyncSession:
    """Strict fake async session exposing only commit and rollback as AsyncMocks,

    rejecting any unexpected attribute access to guarantee complete isolation.
    """

    def __init__(self) -> None:
        self.commit = AsyncMock(name="StrictFakeAsyncSession.commit")
        self.rollback = AsyncMock(name="StrictFakeAsyncSession.rollback")

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(
            f"StrictFakeAsyncSession rejected unexpected DB attribute access: {name!r}"
        )


@contextmanager
def _selection_inference_spies(
    *,
    settings_obj: Any,
    resolver_spy: MagicMock,
) -> Iterator[dict[str, Any]]:
    reader_factory_spy = MagicMock(name="_configured_product_snapshot_reader_spy")
    datetime_mock = MagicMock(name="datetime_mock")
    observation_spy = AsyncMock(name="observe_coin_inference_shadow_spy")
    payload_formatter_spy = MagicMock(name="_catalog_inference_shadow_payload_spy")
    log_trading_event_spy = MagicMock(name="log_trading_event_spy")

    with patch.object(offers_module, "settings", settings_obj), \
         patch.object(
             offers_module,
             "configured_product_snapshot_authority_path",
             resolver_spy,
         ), \
         patch.object(
             offers_module,
             "_configured_product_snapshot_reader",
             reader_factory_spy,
         ), \
         patch.object(offers_module, "datetime", datetime_mock), \
         patch.object(
             offers_module,
             "observe_coin_inference_shadow",
             observation_spy,
         ), \
         patch.object(
             offers_module,
             "_catalog_inference_shadow_payload",
             payload_formatter_spy,
         ), \
         patch.object(
             offers_module,
             "log_trading_event",
             log_trading_event_spy,
         ):
        yield {
            "resolver_spy": resolver_spy,
            "reader_factory_spy": reader_factory_spy,
            "datetime_mock": datetime_mock,
            "observation_spy": observation_spy,
            "payload_formatter_spy": payload_formatter_spy,
            "log_trading_event_spy": log_trading_event_spy,
        }


class SelectionInferenceConfigurationExitsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def _assert_suppression(
        self,
        spies: dict[str, Any],
        strict_session: StrictFakeAsyncSession,
    ) -> None:
        reader_factory_spy = spies["reader_factory_spy"]
        datetime_mock = spies["datetime_mock"]
        observation_spy = spies["observation_spy"]
        payload_formatter_spy = spies["payload_formatter_spy"]
        log_trading_event_spy = spies["log_trading_event_spy"]

        reader_factory_spy.assert_not_called()
        self.assertEqual(reader_factory_spy.call_count, 0)

        datetime_mock.now.assert_not_called()
        self.assertEqual(datetime_mock.now.call_count, 0)

        observation_spy.assert_not_called()
        observation_spy.assert_not_awaited()
        self.assertEqual(observation_spy.call_count, 0)
        self.assertEqual(observation_spy.await_count, 0)

        payload_formatter_spy.assert_not_called()
        self.assertEqual(payload_formatter_spy.call_count, 0)

        log_trading_event_spy.assert_not_called()
        self.assertEqual(log_trading_event_spy.call_count, 0)

        strict_session.commit.assert_not_called()
        strict_session.commit.assert_not_awaited()
        self.assertEqual(strict_session.commit.call_count, 0)
        self.assertEqual(strict_session.commit.await_count, 0)

        strict_session.rollback.assert_not_called()
        strict_session.rollback.assert_not_awaited()
        self.assertEqual(strict_session.rollback.call_count, 0)
        self.assertEqual(strict_session.rollback.await_count, 0)

    async def test_empty_path_legacy_mode_whitespace(self) -> None:
        """LEGACY mode with whitespace snapshot path returns unconfigured fallback."""
        synthetic_settings = SyntheticSettings(
            product_estimator_snapshot_mode="LEGACY",
            coin_intelligence_inference_snapshot_path="   ",
            product_estimator_private_primary_snapshot_path="",
        )
        resolver_spy = MagicMock(
            wraps=configured_product_snapshot_authority_path
        )
        strict_session = StrictFakeAsyncSession()

        with _selection_inference_spies(
            settings_obj=synthetic_settings,
            resolver_spy=resolver_spy,
        ) as spies:
            result = await _selection_inference_for_missing_commodity(
                strict_session,
                price=50_000_000,
                settlement_type="cash",
                low_date_hint=False,
                pack_hint=False,
            )

        self.assertEqual(result, EXPECTED_UNCONFIGURED_PAYLOAD)
        self.assertEqual(len(result), 7)
        self.assertEqual(
            set(result.keys()),
            {
                "mode",
                "status",
                "decision_key",
                "snapshot_generated_at_utc",
                "snapshot_receipt",
                "reason",
                "candidates",
            },
        )
        self.assertIsNone(result["decision_key"])
        self.assertIsNone(result["snapshot_generated_at_utc"])
        self.assertIsNone(result["snapshot_receipt"])
        self.assertEqual(result["reason"], "SNAPSHOT_PATH_UNCONFIGURED")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["mode"], "SELECTABLE")
        self.assertEqual(result["status"], "ABSTAIN")

        resolver_spy.assert_called_once_with(synthetic_settings)
        self.assertEqual(resolver_spy.call_count, 1)
        self.assertIs(resolver_spy.call_args[0][0], synthetic_settings)

        self._assert_suppression(spies, strict_session)

    async def test_empty_path_legacy_mode_empty_string(self) -> None:
        """LEGACY mode with empty string snapshot path returns unconfigured fallback."""
        synthetic_settings = SyntheticSettings(
            product_estimator_snapshot_mode="LEGACY",
            coin_intelligence_inference_snapshot_path="",
            product_estimator_private_primary_snapshot_path="",
        )
        resolver_spy = MagicMock(
            wraps=configured_product_snapshot_authority_path
        )
        strict_session = StrictFakeAsyncSession()

        with _selection_inference_spies(
            settings_obj=synthetic_settings,
            resolver_spy=resolver_spy,
        ) as spies:
            result = await _selection_inference_for_missing_commodity(
                strict_session,
                price=50_000_000,
                settlement_type="tomorrow",
                low_date_hint=True,
                pack_hint=False,
            )

        self.assertEqual(result, EXPECTED_UNCONFIGURED_PAYLOAD)
        self.assertEqual(len(result), 7)
        self.assertEqual(
            set(result.keys()),
            {
                "mode",
                "status",
                "decision_key",
                "snapshot_generated_at_utc",
                "snapshot_receipt",
                "reason",
                "candidates",
            },
        )
        self.assertIsNone(result["decision_key"])
        self.assertIsNone(result["snapshot_generated_at_utc"])
        self.assertIsNone(result["snapshot_receipt"])
        self.assertEqual(result["reason"], "SNAPSHOT_PATH_UNCONFIGURED")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["mode"], "SELECTABLE")
        self.assertEqual(result["status"], "ABSTAIN")

        resolver_spy.assert_called_once_with(synthetic_settings)
        self.assertEqual(resolver_spy.call_count, 1)
        self.assertIs(resolver_spy.call_args[0][0], synthetic_settings)

        self._assert_suppression(spies, strict_session)

    async def test_empty_path_private_primary_mode_does_not_fall_back(self) -> None:
        """PRIVATE_PRIMARY with whitespace path does not fall back to nonempty legacy path."""
        synthetic_settings = SyntheticSettings(
            product_estimator_snapshot_mode="PRIVATE_PRIMARY",
            product_estimator_private_primary_snapshot_path="   ",
            coin_intelligence_inference_snapshot_path="/synthetic/legacy/authority_snapshot.json",
        )
        resolver_spy = MagicMock(
            wraps=configured_product_snapshot_authority_path
        )
        strict_session = StrictFakeAsyncSession()

        with _selection_inference_spies(
            settings_obj=synthetic_settings,
            resolver_spy=resolver_spy,
        ) as spies:
            result = await _selection_inference_for_missing_commodity(
                strict_session,
                price=75_000_000,
                settlement_type="cash",
                low_date_hint=False,
                pack_hint=True,
            )

        self.assertEqual(result, EXPECTED_UNCONFIGURED_PAYLOAD)
        self.assertEqual(len(result), 7)
        self.assertEqual(
            set(result.keys()),
            {
                "mode",
                "status",
                "decision_key",
                "snapshot_generated_at_utc",
                "snapshot_receipt",
                "reason",
                "candidates",
            },
        )
        self.assertIsNone(result["decision_key"])
        self.assertIsNone(result["snapshot_generated_at_utc"])
        self.assertIsNone(result["snapshot_receipt"])
        self.assertEqual(result["reason"], "SNAPSHOT_PATH_UNCONFIGURED")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["mode"], "SELECTABLE")
        self.assertEqual(result["status"], "ABSTAIN")

        resolver_spy.assert_called_once_with(synthetic_settings)
        self.assertEqual(resolver_spy.call_count, 1)
        self.assertIs(resolver_spy.call_args[0][0], synthetic_settings)

        self._assert_suppression(spies, strict_session)

    async def test_resolver_failure_escapes_outside_try_block(self) -> None:
        """Resolver exception escapes directly outside try block without being swallowed."""
        synthetic_settings = SyntheticSettings(
            product_estimator_snapshot_mode="LEGACY",
            coin_intelligence_inference_snapshot_path="/synthetic/path.json",
            product_estimator_private_primary_snapshot_path="",
        )
        synthetic_exc = SyntheticResolverError(
            "synthetic-authority-resolver-failure-sentinel"
        )
        resolver_spy = MagicMock(side_effect=synthetic_exc)
        strict_session = StrictFakeAsyncSession()

        with _selection_inference_spies(
            settings_obj=synthetic_settings,
            resolver_spy=resolver_spy,
        ) as spies:
            with self.assertRaises(SyntheticResolverError) as ctx:
                await _selection_inference_for_missing_commodity(
                    strict_session,
                    price=50_000_000,
                    settlement_type="cash",
                    low_date_hint=False,
                    pack_hint=False,
                )

            self.assertIs(ctx.exception, synthetic_exc)

        resolver_spy.assert_called_once_with(synthetic_settings)
        self.assertEqual(resolver_spy.call_count, 1)
        self.assertIs(resolver_spy.call_args[0][0], synthetic_settings)

        self._assert_suppression(spies, strict_session)

    async def test_real_resolver_failure_invalid_snapshot_mode(self) -> None:
        """Invalid nonempty snapshot mode raises ProductSnapshotUnavailable via real normalization."""
        synthetic_settings = InstrumentedSettings(
            product_estimator_snapshot_mode="   invalid_mode   ",
            coin_intelligence_inference_snapshot_path="",
            product_estimator_private_primary_snapshot_path="",
        )
        resolver_spy = MagicMock(
            wraps=configured_product_snapshot_authority_path
        )
        strict_session = StrictFakeAsyncSession()

        with _selection_inference_spies(
            settings_obj=synthetic_settings,
            resolver_spy=resolver_spy,
        ) as spies:
            with self.assertRaises(ProductSnapshotUnavailable) as ctx:
                await _selection_inference_for_missing_commodity(
                    strict_session,
                    price=50_000_000,
                    settlement_type="cash",
                    low_date_hint=False,
                    pack_hint=False,
                )

        self.assertIs(type(ctx.exception), ProductSnapshotUnavailable)
        self.assertIsInstance(ctx.exception, ProductSnapshotUnavailable)
        self.assertEqual(ctx.exception.reason_code, "PRODUCT_SNAPSHOT_MODE_INVALID")
        self.assertEqual(str(ctx.exception), "PRODUCT_SNAPSHOT_MODE_INVALID")
        self.assertEqual(ctx.exception.args, ("PRODUCT_SNAPSHOT_MODE_INVALID",))

        resolver_spy.assert_called_once_with(synthetic_settings)
        self.assertEqual(resolver_spy.call_count, 1)
        self.assertIs(resolver_spy.call_args[0][0], synthetic_settings)

        self.assertEqual(
            synthetic_settings.access_log,
            ["product_estimator_snapshot_mode"],
        )
        self.assertNotIn(
            "product_estimator_private_primary_snapshot_path",
            synthetic_settings.access_log,
        )
        self.assertNotIn(
            "coin_intelligence_inference_snapshot_path",
            synthetic_settings.access_log,
        )
        self.assertNotIn(
            "coin_intelligence_inference_auto_selection_enabled",
            synthetic_settings.access_log,
        )
        self.assertEqual(synthetic_settings.forbidden_accesses, [])

        self._assert_suppression(spies, strict_session)

    async def test_real_resolver_failure_mode_string_conversion_error(self) -> None:
        """Mode value __str__ failure escapes with exact ordered getter and __bool__/__str__ traces."""
        synthetic_exc = SyntheticModeStringConversionError(
            "synthetic-mode-string-conversion-sentinel"
        )
        events: list[str] = []
        failing_mode = FailingStringMode(synthetic_exc, events)
        synthetic_settings = InstrumentedSettings(
            product_estimator_snapshot_mode=failing_mode,
            coin_intelligence_inference_snapshot_path="",
            product_estimator_private_primary_snapshot_path="",
            event_log=events,
        )
        resolver_spy = MagicMock(
            wraps=configured_product_snapshot_authority_path
        )
        strict_session = StrictFakeAsyncSession()

        with _selection_inference_spies(
            settings_obj=synthetic_settings,
            resolver_spy=resolver_spy,
        ) as spies:
            with self.assertRaises(SyntheticModeStringConversionError) as ctx:
                await _selection_inference_for_missing_commodity(
                    strict_session,
                    price=50_000_000,
                    settlement_type="tomorrow",
                    low_date_hint=True,
                    pack_hint=False,
                )

        self.assertIs(ctx.exception, synthetic_exc)

        self.assertEqual(
            events,
            [
                "product_estimator_snapshot_mode",
                "mode_value:bool",
                "mode_value:str",
            ],
        )
        self.assertEqual(
            events.count("product_estimator_snapshot_mode"), 1
        )
        self.assertEqual(events.count("mode_value:bool"), 1)
        self.assertEqual(events.count("mode_value:str"), 1)

        str_idx = events.index("mode_value:str")
        self.assertEqual(events[str_idx - 1], "mode_value:bool")
        self.assertEqual(events[str_idx - 2], "product_estimator_snapshot_mode")

        resolver_spy.assert_called_once_with(synthetic_settings)
        self.assertEqual(resolver_spy.call_count, 1)
        self.assertIs(resolver_spy.call_args[0][0], synthetic_settings)

        self.assertNotIn(
            "product_estimator_private_primary_snapshot_path",
            synthetic_settings.access_log,
        )
        self.assertNotIn(
            "coin_intelligence_inference_snapshot_path",
            synthetic_settings.access_log,
        )
        self.assertNotIn(
            "coin_intelligence_inference_auto_selection_enabled",
            synthetic_settings.access_log,
        )
        self.assertEqual(synthetic_settings.forbidden_accesses, [])

        self._assert_suppression(spies, strict_session)

    async def test_real_resolver_failure_selected_authority_path_property_error(
        self,
    ) -> None:
        """Selected authority path property error escapes with exact ordered getter trace."""
        synthetic_exc = SyntheticAuthorityPathPropertyError(
            "synthetic-authority-path-property-sentinel"
        )
        synthetic_settings = InstrumentedSettings(
            product_estimator_snapshot_mode="   private_primary   ",
            product_estimator_private_primary_snapshot_path=synthetic_exc,
            coin_intelligence_inference_snapshot_path="/synthetic/fallback.json",
        )
        resolver_spy = MagicMock(
            wraps=configured_product_snapshot_authority_path
        )
        strict_session = StrictFakeAsyncSession()

        with _selection_inference_spies(
            settings_obj=synthetic_settings,
            resolver_spy=resolver_spy,
        ) as spies:
            with self.assertRaises(SyntheticAuthorityPathPropertyError) as ctx:
                await _selection_inference_for_missing_commodity(
                    strict_session,
                    price=75_000_000,
                    settlement_type="cash",
                    low_date_hint=False,
                    pack_hint=True,
                )

        self.assertIs(ctx.exception, synthetic_exc)

        self.assertEqual(
            synthetic_settings.access_log,
            [
                "product_estimator_snapshot_mode",
                "product_estimator_private_primary_snapshot_path",
            ],
        )
        self.assertEqual(
            synthetic_settings.access_log.count("product_estimator_snapshot_mode"),
            1,
        )
        self.assertEqual(
            synthetic_settings.access_log.count(
                "product_estimator_private_primary_snapshot_path"
            ),
            1,
        )

        resolver_spy.assert_called_once_with(synthetic_settings)
        self.assertEqual(resolver_spy.call_count, 1)
        self.assertIs(resolver_spy.call_args[0][0], synthetic_settings)

        self.assertNotIn(
            "coin_intelligence_inference_snapshot_path",
            synthetic_settings.access_log,
        )
        self.assertNotIn(
            "coin_intelligence_inference_auto_selection_enabled",
            synthetic_settings.access_log,
        )
        self.assertEqual(synthetic_settings.forbidden_accesses, [])

        self._assert_suppression(spies, strict_session)

    async def test_real_resolver_failure_selected_authority_path_bool_error(
        self,
    ) -> None:
        """Selected authority path __bool__ failure escapes outside try block for all authority modes."""
        mode_matrix = [
            (
                "LEGACY",
                "coin_intelligence_inference_snapshot_path",
                "product_estimator_private_primary_snapshot_path",
            ),
            (
                "PRIVATE_SHADOW",
                "coin_intelligence_inference_snapshot_path",
                "product_estimator_private_primary_snapshot_path",
            ),
            (
                "PRIVATE_PRIMARY",
                "product_estimator_private_primary_snapshot_path",
                "coin_intelligence_inference_snapshot_path",
            ),
        ]
        for mode, selected_prop, alternate_prop in mode_matrix:
            with self.subTest(mode=mode):
                synthetic_exc = SyntheticAuthorityPathBoolError(
                    f"synthetic-authority-path-bool-sentinel-{mode.lower()}"
                )
                events: list[str] = []
                failing_path = InstrumentedAuthorityPath(
                    events,
                    bool_exc=synthetic_exc,
                )
                synthetic_settings = InstrumentedSettings(
                    product_estimator_snapshot_mode=mode,
                    **{
                        selected_prop: failing_path,
                        alternate_prop: f"/synthetic/alternate_{alternate_prop}.json",
                    },
                    event_log=events,
                )
                resolver_spy = MagicMock(
                    wraps=configured_product_snapshot_authority_path
                )
                strict_session = StrictFakeAsyncSession()

                with _selection_inference_spies(
                    settings_obj=synthetic_settings,
                    resolver_spy=resolver_spy,
                ) as spies:
                    with self.assertRaises(SyntheticAuthorityPathBoolError) as ctx:
                        await _selection_inference_for_missing_commodity(
                            strict_session,
                            price=50_000_000,
                            settlement_type="cash",
                            low_date_hint=False,
                            pack_hint=False,
                        )

                self.assertIs(ctx.exception, synthetic_exc)

                expected_events = [
                    "product_estimator_snapshot_mode",
                    selected_prop,
                    "path_value:bool",
                ]
                self.assertEqual(events, expected_events)
                self.assertEqual(
                    events.count("product_estimator_snapshot_mode"), 1
                )
                self.assertEqual(events.count(selected_prop), 1)
                self.assertEqual(events.count("path_value:bool"), 1)
                self.assertEqual(events.count("path_value:str"), 0)
                self.assertNotIn("path_value:str", events)

                resolver_spy.assert_called_once_with(synthetic_settings)
                self.assertEqual(resolver_spy.call_count, 1)
                self.assertIs(resolver_spy.call_args[0][0], synthetic_settings)

                self.assertNotIn(
                    alternate_prop,
                    synthetic_settings.access_log,
                )
                self.assertNotIn(
                    "coin_intelligence_inference_auto_selection_enabled",
                    synthetic_settings.access_log,
                )
                self.assertEqual(synthetic_settings.forbidden_accesses, [])

                self._assert_suppression(spies, strict_session)

    async def test_real_resolver_failure_selected_authority_path_str_error(
        self,
    ) -> None:
        """Selected authority path __str__ failure escapes outside try block for all authority modes."""
        mode_matrix = [
            (
                "LEGACY",
                "coin_intelligence_inference_snapshot_path",
                "product_estimator_private_primary_snapshot_path",
            ),
            (
                "PRIVATE_SHADOW",
                "coin_intelligence_inference_snapshot_path",
                "product_estimator_private_primary_snapshot_path",
            ),
            (
                "PRIVATE_PRIMARY",
                "product_estimator_private_primary_snapshot_path",
                "coin_intelligence_inference_snapshot_path",
            ),
        ]
        for mode, selected_prop, alternate_prop in mode_matrix:
            with self.subTest(mode=mode):
                synthetic_exc = SyntheticAuthorityPathStrError(
                    f"synthetic-authority-path-str-sentinel-{mode.lower()}"
                )
                events: list[str] = []
                failing_path = InstrumentedAuthorityPath(
                    events,
                    str_exc=synthetic_exc,
                )
                synthetic_settings = InstrumentedSettings(
                    product_estimator_snapshot_mode=mode,
                    **{
                        selected_prop: failing_path,
                        alternate_prop: f"/synthetic/alternate_{alternate_prop}.json",
                    },
                    event_log=events,
                )
                resolver_spy = MagicMock(
                    wraps=configured_product_snapshot_authority_path
                )
                strict_session = StrictFakeAsyncSession()

                with _selection_inference_spies(
                    settings_obj=synthetic_settings,
                    resolver_spy=resolver_spy,
                ) as spies:
                    with self.assertRaises(SyntheticAuthorityPathStrError) as ctx:
                        await _selection_inference_for_missing_commodity(
                            strict_session,
                            price=50_000_000,
                            settlement_type="tomorrow",
                            low_date_hint=True,
                            pack_hint=False,
                        )

                self.assertIs(ctx.exception, synthetic_exc)

                expected_events = [
                    "product_estimator_snapshot_mode",
                    selected_prop,
                    "path_value:bool",
                    "path_value:str",
                ]
                self.assertEqual(events, expected_events)
                self.assertEqual(
                    events.count("product_estimator_snapshot_mode"), 1
                )
                self.assertEqual(events.count(selected_prop), 1)
                self.assertEqual(events.count("path_value:bool"), 1)
                self.assertEqual(events.count("path_value:str"), 1)

                resolver_spy.assert_called_once_with(synthetic_settings)
                self.assertEqual(resolver_spy.call_count, 1)
                self.assertIs(resolver_spy.call_args[0][0], synthetic_settings)

                self.assertNotIn(
                    alternate_prop,
                    synthetic_settings.access_log,
                )
                self.assertNotIn(
                    "coin_intelligence_inference_auto_selection_enabled",
                    synthetic_settings.access_log,
                )
                self.assertEqual(synthetic_settings.forbidden_accesses, [])

                self._assert_suppression(spies, strict_session)


if __name__ == "__main__":
    unittest.main()
