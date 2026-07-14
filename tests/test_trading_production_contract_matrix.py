import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks
from sqlalchemy import CheckConstraint

from api.routers.trades import TradeCreate, _execute_trade_authoritatively, _forward_trade_if_remote_home
from core.enums import UserAccountStatus, UserRole
from models.offer import Offer, OfferStatus, OfferType
from models.offer_request import OfferRequest, OfferRequestStatus
from models.trade import Trade


class FakeExecuteResult:
    def __init__(self, *, single=None, single_or_none=None):
        self._single = single
        self._single_or_none = single_or_none

    def scalar_one(self):
        return self._single

    def scalar_one_or_none(self):
        return self._single_or_none


class FakeDB:
    def __init__(self, *, get_results=None, execute_results=None, scalar_result=None):
        self.get_results = list(get_results or [])
        self.execute_results = list(execute_results or [])
        self.scalar_result = scalar_result
        self.refresh = AsyncMock()
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.flush = AsyncMock()
        self.added = []
        self.offer_requests = []

    async def get(self, _model, _id, **_kwargs):
        if not self.get_results:
            raise AssertionError("Unexpected get() call")
        return self.get_results.pop(0)

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    async def scalar(self, _stmt):
        return self.scalar_result

    def add(self, item):
        if isinstance(item, OfferRequest):
            self.offer_requests.append(item)
            return
        self.added.append(item)


def make_user(**overrides):
    data = {
        "id": 5,
        "role": UserRole.STANDARD,
        "account_status": UserAccountStatus.ACTIVE,
        "trading_restricted_until": None,
        "mobile_number": "09120000000",
        "account_name": "user5",
        "telegram_id": 555,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_offer(**overrides):
    data = {
        "id": 7,
        "status": OfferStatus.ACTIVE,
        "user_id": 9,
        "quantity": 10,
        "remaining_quantity": 10,
        "is_wholesale": True,
        "lot_sizes": None,
        "offer_type": OfferType.SELL,
        "price": 123456,
        "commodity_id": 1,
        "offer_public_id": "ofr_contract_7",
        "commodity": SimpleNamespace(name="Gold"),
        "home_server": "foreign",
        "user": SimpleNamespace(
            account_name="seller",
            mobile_number="09125555555",
            telegram_id=999,
        ),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_context(owner_user, actor_user=None):
    actor = actor_user or owner_user
    return SimpleNamespace(
        owner_user=owner_user,
        actor_user=actor,
        relation=None,
        is_accountant_context=owner_user.id != actor.id,
    )


CONTRACT_COVERAGE = {
    "offer_create_success": (
        "tests/test_offers_router_create_success.py",
        "test_create_offer_stamps_home_server_and_links_republished_offer",
    ),
    "offer_market_and_user_guards": (
        "tests/test_offers_router_create_guards.py",
        "test_create_offer_rejects_when_market_is_closed",
    ),
    "offer_accountant_denied": (
        "tests/test_offers_router_create_guards.py",
        "test_create_offer_rejects_accountant_context",
    ),
    "offer_lifecycle_consistency": (
        "tests/test_offers_router_create_success.py",
        "test_create_offer_tolerates_post_commit_cache_and_realtime_failures",
    ),
    "trade_standard_success": (
        "tests/test_trades_router_authoritative_success.py",
        "test_execute_trade_authoritatively_persists_trade_and_runs_side_effects",
    ),
    "trade_watch_inactive_restricted_closed_guards": (
        "tests/test_trades_router_authoritative_guards.py",
        "test_execute_trade_authoritatively_rejects_watch_restricted_and_limit_failures",
    ),
    "trade_self_and_blocked_offer_guards": (
        "tests/test_trades_router_authoritative_guards.py",
        "test_execute_trade_authoritatively_rejects_missing_inactive_self_and_blocked_offers",
    ),
    "retail_lot_suggestion": (
        "tests/test_trades_router_authoritative_guards.py",
        "test_execute_trade_authoritatively_returns_lot_suggestion_payload",
    ),
    "tier2_customer_chain_legs": (
        "tests/test_trades_router_authoritative_success.py",
        "test_execute_trade_authoritatively_creates_two_legs_for_tier2_customer_on_outsider_owner_offer",
    ),
    "tier1_tier2_three_leg_chain": (
        "tests/test_trades_router_authoritative_success.py",
        "test_execute_trade_authoritatively_creates_three_legs_for_tier1_source_and_other_owner_tier2_sell_offer",
    ),
    "actor_only_history_hidden": (
        "tests/test_trades_router_reads.py",
        "test_get_trade_denies_actor_only_history_row_for_self_viewer",
    ),
    "customer_profit_uses_historical_trade_prices": (
        "tests/test_customers_router.py",
        "test_get_my_customer_trade_stats_uses_historical_trade_prices",
    ),
    "customer_profit_survives_relation_status_and_rate_changes": (
        "tests/test_customers_router.py",
        "test_get_my_customer_trade_stats_preserves_historical_profit_after_relation_status_and_rate_changes",
    ),
    "customer_history_survives_relation_status_change": (
        "tests/test_trades_router_reads.py",
        "test_get_trades_with_user_preserves_target_customer_history_after_relation_status_change",
    ),
    "trade_owner_accountant_audience_independence": (
        "tests/test_trades_router_authoritative_success.py",
        "test_execute_trade_authoritatively_persists_trade_and_runs_side_effects",
    ),
    "trade_forwarding_wrapper": (
        "tests/test_trades_router_execution_wrappers.py",
        "test_create_trade_returns_forwarded_response_when_remote_home",
    ),
    "internal_signature_and_timeout": (
        "tests/test_server_routing_and_trade_forwarding.py",
        "test_verify_internal_signature_rejects_stale_missing_or_wrong_key_payloads",
    ),
    "cross_server_authority_hardening": (
        "tests/test_trades_router_execution_wrappers.py",
        "test_forward_trade_if_remote_home_covers_both_cross_server_directions_and_idempotency",
    ),
    "trade_payload_validation": (
        "tests/test_trade_service_validation_and_payloads.py",
        "test_validate_offer_trade_amount_covers_error_paths_and_success",
    ),
    "tier2_notification_privacy": (
        "tests/test_trades_router_authoritative_success.py",
        "test_execute_trade_authoritatively_projects_tier2_customer_price_on_owner_offer",
    ),
}


class TradingProductionContractMatrixTests(unittest.IsolatedAsyncioTestCase):
    def test_contract_matrix_source_files_keep_named_contract_coverage(self):
        missing = []
        for contract_name, (relative_path, required_snippet) in CONTRACT_COVERAGE.items():
            source_path = Path(relative_path)
            if not source_path.exists():
                missing.append(f"{contract_name}: missing {relative_path}")
                continue
            if required_snippet not in source_path.read_text(encoding="utf-8"):
                missing.append(f"{contract_name}: missing snippet {required_snippet}")

        self.assertEqual(missing, [], "\n".join(missing))

    def test_model_constraints_support_money_path_contracts(self):
        offer_constraints = {
            constraint.name
            for constraint in Offer.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }
        trade_constraints = {
            constraint.name
            for constraint in Trade.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }

        self.assertIn("ck_offers_quantity_positive", offer_constraints)
        self.assertIn("ck_offers_price_positive", offer_constraints)
        self.assertIn("ck_offers_remaining_nonnegative", offer_constraints)
        self.assertTrue(Offer.__table__.c.idempotency_key.unique)

        self.assertIn("ck_trades_quantity_positive", trade_constraints)
        self.assertIn("ck_trades_price_positive", trade_constraints)
        self.assertTrue(Trade.__table__.c.trade_number.unique)
        self.assertTrue(Trade.__table__.c.idempotency_key.unique)

    async def test_idempotent_replay_returns_existing_trade_without_offer_mutation(self):
        owner_user = make_user(id=5)
        offer = make_offer()
        existing_trade = SimpleNamespace(id=88, offer_user_id=offer.user_id, responder_user_id=owner_user.id)
        db = FakeDB(
            get_results=[offer],
            execute_results=[
                FakeExecuteResult(single=owner_user),
                FakeExecuteResult(single_or_none=None),
                FakeExecuteResult(single_or_none=existing_trade),
            ],
            scalar_result=10000,
        )

        with patch(
            "api.routers.trades.evaluate_current_market_schedule",
            new=AsyncMock(return_value=SimpleNamespace(is_open=True, reason="daily_window_open")),
        ), patch(
            "api.routers.trades.load_offer_request_by_idempotency",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.trades.check_user_limits",
            return_value=(True, None),
        ), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch(
            "core.services.block_service.is_blocked",
            new=AsyncMock(return_value=(False, None)),
        ), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.trades._load_trade_identity_map_for_user_ids",
            new=AsyncMock(return_value={}),
        ), patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value={}),
        ), patch(
            "api.routers.trades.trade_to_response",
            return_value={"id": 88, "replayed": True},
        ) as response_mock:
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4, idempotency_key="idem-1"),
                BackgroundTasks(),
                db=db,
                context=make_context(owner_user),
            )

        self.assertEqual(result, {"id": 88, "replayed": True})
        self.assertEqual(db.added, [])
        self.assertEqual(len(db.offer_requests), 1)
        self.assertEqual(db.offer_requests[0].result_status, OfferRequestStatus.COMPLETED_TRADE)
        self.assertEqual(db.offer_requests[0].resulting_trade_id, 88)
        db.commit.assert_awaited_once()
        db.rollback.assert_not_awaited()
        self.assertEqual(offer.remaining_quantity, 10)
        self.assertEqual(offer.status, OfferStatus.ACTIVE)
        response_mock.assert_called_once_with(existing_trade, identity_map={}, customer_relation_map={})

    async def test_remote_home_forward_payload_preserves_delegated_actor_context(self):
        owner_user = make_user(id=5)
        actor_user = make_user(id=44)
        edge_received_at = datetime(2026, 6, 16, 12, 30, tzinfo=timezone.utc)
        db = FakeDB(get_results=[make_offer(home_server="iran")])

        with patch("api.routers.trades.is_remote_home", return_value=True), patch(
            "api.routers.trades.current_server",
            return_value="foreign",
        ), patch(
            "api.routers.trades.forward_trade_to_home_server",
            new=AsyncMock(return_value=(202, {"forwarded": True})),
        ) as forward_mock:
            response = await _forward_trade_if_remote_home(
                db=db,
                trade_data=TradeCreate(offer_id=7, quantity=4, idempotency_key="idem-remote"),
                context=make_context(owner_user, actor_user),
                edge_received_at=edge_received_at,
            )

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(json.loads(response.body), {"forwarded": True})
        forward_mock.assert_awaited_once()
        target_home_server, payload = forward_mock.await_args.args
        self.assertEqual(target_home_server, "iran")
        self.assertEqual(
            payload,
            {
                "offer_id": 7,
                "offer_public_id": "ofr_contract_7",
                "quantity": 4,
                "responder_user_id": 5,
                "actor_user_id": 44,
                "edge_received_at": edge_received_at.isoformat(),
                "source_surface": "webapp",
                "source_server": "foreign",
                "idempotency_key": "idem-remote",
            },
        )


if __name__ == "__main__":
    unittest.main()
