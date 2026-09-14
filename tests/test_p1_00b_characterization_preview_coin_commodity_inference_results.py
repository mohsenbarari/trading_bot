"""Hermetic characterization tests for preview_coin_commodity_inference handler."""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.offers import (
    CoinInferencePreviewRequest,
    CoinInferencePreviewResponse,
    preview_coin_commodity_inference,
)
from core.market_intelligence.coin_catalog import (
    CatalogCoinCommodityCandidate,
    CatalogCoinCommodityInference,
)
from core.market_intelligence.coin_inference_shadow import CoinInferenceShadowObservation


class _TrackingAsyncDbSession:
    def __init__(self, *, commit_error: Exception | None = None) -> None:
        self.commits = 0
        self.rollbacks = 0
        self._commit_error = commit_error

    async def commit(self) -> None:
        self.commits += 1
        if self._commit_error is not None:
            raise self._commit_error

    async def rollback(self) -> None:
        self.rollbacks += 1


def _sample_candidate() -> CatalogCoinCommodityCandidate:
    return CatalogCoinCommodityCandidate(
        commodity_id=71,
        commodity_code="IMAM",
        commodity_name="تمام بهار آزادی طرح جدید",
        center_project_price=195_000,
        lower_project_price=193_000,
        upper_project_price=197_000,
        confidence="HIGH",
        distance_to_center_relative=0.00025,
    )


def _sample_observation(
    *,
    decision_key: str,
    snapshot_receipt: str,
    candidate: CatalogCoinCommodityCandidate,
) -> CoinInferenceShadowObservation:
    inference = CatalogCoinCommodityInference(
        status="AUTO_SELECT",
        settlement_term="CASH",
        candidates=(candidate,),
        snapshot_generated_at_utc="2026-09-14T10:30:00Z",
        snapshot_receipt=snapshot_receipt,
        reason=None,
    )
    return CoinInferenceShadowObservation(
        decision_key=decision_key,
        decision=inference,
        edit_candidates=(),
    )


class PreviewCoinCommodityInferenceResultCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cash_success_maps_complete_response_and_observer_call(self) -> None:
        db = _TrackingAsyncDbSession()
        candidate = _sample_candidate()
        decision_key = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90"
        snapshot_receipt = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        observation = _sample_observation(
            decision_key=decision_key,
            snapshot_receipt=snapshot_receipt,
            candidate=candidate,
        )

        configured_reader_sentinel = object()
        snapshot_path = "/hermetic/test_snapshot.json"
        request = CoinInferencePreviewRequest(price=195_000, settlement_type="cash")

        with (
            patch(
                "api.routers.offers._coin_inference_preview_path_or_error",
                return_value=snapshot_path,
            ),
            patch(
                "api.routers.offers._configured_product_snapshot_reader",
                return_value=configured_reader_sentinel,
            ),
            patch(
                "api.routers.offers.observe_coin_inference_shadow",
                new=AsyncMock(return_value=observation),
            ) as observe_mock,
        ):
            result = await preview_coin_commodity_inference(
                payload=request,
                db=db,
                _current_user=SimpleNamespace(id=101),
            )

        observe_mock.assert_awaited_once()
        args, kwargs = observe_mock.await_args
        self.assertEqual(len(args), 1)
        self.assertIs(args[0], db)
        self.assertEqual(kwargs["snapshot_path"], snapshot_path)
        self.assertEqual(kwargs["submitted_project_price"], 195_000)
        self.assertEqual(kwargs["settlement_term"], "CASH")
        self.assertEqual(kwargs["source_surface"], "WEBAPP")
        self.assertIs(kwargs["snapshot_reader"], configured_reader_sentinel)

        now_utc = kwargs["now_utc"]
        self.assertIsInstance(now_utc, datetime)
        self.assertIsNotNone(now_utc.tzinfo)
        self.assertEqual(now_utc.tzinfo, timezone.utc)

        self.assertIsInstance(result, CoinInferencePreviewResponse)
        self.assertEqual(result.status, "AUTO_SELECT")
        self.assertEqual(result.decision_key, decision_key)
        self.assertEqual(result.settlement_type, "cash")
        self.assertEqual(result.snapshot_generated_at_utc, "2026-09-14T10:30:00Z")
        self.assertEqual(result.snapshot_receipt, snapshot_receipt)
        self.assertIsNone(result.reason)

        self.assertEqual(len(result.candidates), 1)
        candidate_result = result.candidates[0]
        self.assertEqual(candidate_result.commodity_id, 71)
        self.assertEqual(candidate_result.commodity_code, "IMAM")
        self.assertEqual(candidate_result.commodity_name, "تمام بهار آزادی طرح جدید")
        self.assertEqual(candidate_result.center_project_price, 195_000)
        self.assertEqual(candidate_result.lower_project_price, 193_000)
        self.assertEqual(candidate_result.upper_project_price, 197_000)
        self.assertEqual(candidate_result.confidence, "HIGH")
        self.assertEqual(candidate_result.distance_to_center_relative, 0.00025)

        self.assertEqual(result.edit_candidates, [])
        self.assertEqual(db.commits, 1)
        self.assertEqual(db.rollbacks, 0)

    async def test_commit_failure_rolls_back_logs_and_returns_unavailable(self) -> None:
        db = _TrackingAsyncDbSession(commit_error=RuntimeError("database commit failed"))
        candidate = _sample_candidate()
        decision_key = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90"
        snapshot_receipt = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        observation = _sample_observation(
            decision_key=decision_key,
            snapshot_receipt=snapshot_receipt,
            candidate=candidate,
        )

        configured_reader_sentinel = object()
        snapshot_path = "/hermetic/test_snapshot.json"
        request = CoinInferencePreviewRequest(price=195_000, settlement_type="cash")
        expected_detail = f"پیش{chr(0x200c)}نمایش تشخیص کالا در حال حاضر در دسترس نیست."

        with (
            patch(
                "api.routers.offers._coin_inference_preview_path_or_error",
                return_value=snapshot_path,
            ),
            patch(
                "api.routers.offers._configured_product_snapshot_reader",
                return_value=configured_reader_sentinel,
            ),
            patch(
                "api.routers.offers.observe_coin_inference_shadow",
                new=AsyncMock(return_value=observation),
            ),
            patch("api.routers.offers.log_trading_event") as mock_log_event,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await preview_coin_commodity_inference(
                    payload=request,
                    db=db,
                    _current_user=SimpleNamespace(id=101),
                )

        self.assertEqual(exc_info.exception.status_code, 503)
        self.assertEqual(exc_info.exception.detail, expected_detail)
        self.assertEqual(db.commits, 1)
        self.assertEqual(db.rollbacks, 1)

        mock_log_event.assert_called_once()
        log_args, log_kwargs = mock_log_event.call_args
        self.assertGreaterEqual(len(log_args), 2)
        self.assertEqual(log_args[1], "coin_inference_preview.unavailable")
        self.assertEqual(log_kwargs["level"], "warning")
        self.assertEqual(log_kwargs["action"], "coin_inference_preview")
        self.assertEqual(log_kwargs["result"], "abstained")
        self.assertEqual(log_kwargs["error_class"], "RuntimeError")

    async def test_observer_http_exception_is_re_raised_without_transaction_or_unavailable_log(self) -> None:
        db = _TrackingAsyncDbSession()
        configured_reader_sentinel = object()
        snapshot_path = "/hermetic/test_snapshot.json"
        request = CoinInferencePreviewRequest(price=195_000, settlement_type="cash")
        concrete_http_exception = HTTPException(status_code=403, detail="Observer inference access denied")

        with (
            patch(
                "api.routers.offers._coin_inference_preview_path_or_error",
                return_value=snapshot_path,
            ),
            patch(
                "api.routers.offers._configured_product_snapshot_reader",
                return_value=configured_reader_sentinel,
            ),
            patch(
                "api.routers.offers.observe_coin_inference_shadow",
                new=AsyncMock(side_effect=concrete_http_exception),
            ),
            patch("api.routers.offers.log_trading_event") as mock_log_event,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await preview_coin_commodity_inference(
                    payload=request,
                    db=db,
                    _current_user=SimpleNamespace(id=101),
                )

        self.assertIs(exc_info.exception, concrete_http_exception)
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "Observer inference access denied")
        self.assertEqual(db.commits, 0)
        self.assertEqual(db.rollbacks, 0)
        mock_log_event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
