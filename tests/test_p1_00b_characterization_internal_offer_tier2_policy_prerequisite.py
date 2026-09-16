"""P1-00b characterization test: TIER_2 policy snapshot and commission prerequisite.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Canonical blocker: P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN
Scenario: origin_internal__tier2_policy_prerequisite__real_relation_snapshot_commission
Gap reference: docs/refactor/two-server/section-1/inventory/scenario-records.json#ORIGIN-INTERNAL__WEBAPP__POLICY-SNAPSHOT

Explicit exclusions:
- No create_trade or combined live flow invocation.
- No request ledger persistence or origin OUTCOME-SUCCESS assertion.
- No notification audience, side-effect retry, Telegram/admin/accountant guards.
- No seed acceptance, blocker/scenario-record mutation, reconciliation, or Stage closure.

Hermetic and deterministic under APP_ENV_FILE=config/unit-test.env.example.
"""
from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

# Canonical network fence must be installed before repository application imports.
install_p1_00_network_fence()

from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from core.enums import UserRole
from core.server_routing import SERVER_IRAN
from core.services.customer_relation_service import (
    apply_customer_commission,
    get_active_customer_relation_for_customer,
)
from core.services.offer_request_ledger_service import customer_relation_snapshot
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.offer import OfferType
from models.user import User
from tests.test_p1_00b_characterization_origin_interactions import (
    _make_owner,
    _sqlite_requests,
)
from tests.test_p1_00b_characterization_real_async_session_helper import real_async_session


class P100bInternalOfferTier2PolicyPrerequisiteCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterize TIER_2 customer relation snapshot and commission policy prerequisite."""

    def setUp(self) -> None:
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sqlite_path = str(Path(self.temp_dir.name) / "test_p1_00b_internal_offer_tier2_prereq.db")
        self.engine, _ = _sqlite_requests(self.sqlite_path)
        self.addCleanup(self._dispose_engine)

        original_indexes = set(CustomerRelation.__table__.indexes)
        original_index_identities = {id(idx) for idx in original_indexes}
        original_index_names = sorted(idx.name for idx in original_indexes if idx.name is not None)
        discarded_indexes = []
        try:
            seen_indexes = set()
            for idx in list(CustomerRelation.__table__.indexes):
                if idx.name in seen_indexes:
                    CustomerRelation.__table__.indexes.discard(idx)
                    discarded_indexes.append(idx)
                else:
                    seen_indexes.add(idx.name)
            CustomerRelation.__table__.create(self.engine, checkfirst=True)
        finally:
            for idx in discarded_indexes:
                CustomerRelation.__table__.indexes.add(idx)

        self.assertEqual(CustomerRelation.__table__.indexes, original_indexes)
        self.assertEqual(
            {id(idx) for idx in CustomerRelation.__table__.indexes},
            original_index_identities,
        )
        self.assertEqual(
            sorted(idx.name for idx in CustomerRelation.__table__.indexes if idx.name is not None),
            original_index_names,
        )

    def _dispose_engine(self) -> None:
        try:
            self.engine.dispose()
        finally:
            self.temp_dir.cleanup()

    async def test_real_relation_yields_current_tier2_snapshot_and_commission(self) -> None:
        """Verify real relation row query, snapshot projection, and SELL commission math."""
        owner_user_id = 9
        customer_user_id = 5
        relation_id = 44
        management_name = "desk"
        commission_rate = Decimal("1.5")
        min_qty = 1
        max_qty = 10
        max_daily_tr = 8
        max_daily_vol = 20
        raw_fixture_price = 123456
        expected_rounded_price = 125400

        owner = _make_owner(id=owner_user_id, role=UserRole.STANDARD, home_server=SERVER_IRAN)
        self.assertEqual(owner.id, owner_user_id)
        self.assertEqual(owner.role, UserRole.STANDARD)

        requester = _make_owner(id=customer_user_id, role=UserRole.STANDARD, home_server=SERVER_IRAN)
        self.assertEqual(requester.id, customer_user_id)
        self.assertEqual(requester.role, UserRole.STANDARD)

        tier2_relation = CustomerRelation(
            id=relation_id,
            owner_user_id=owner_user_id,
            customer_user_id=customer_user_id,
            invitation_token="tok_internal_offer_tier2_prereq_44",
            management_name=management_name,
            customer_tier=CustomerTier.TIER_2,
            commission_rate=commission_rate,
            min_trade_quantity=min_qty,
            max_trade_quantity=max_qty,
            max_daily_trades=max_daily_tr,
            max_daily_commodity_volume=max_daily_vol,
            status=CustomerRelationStatus.ACTIVE,
        )

        # 1. Persist models in Session 1 using real AsyncSession.
        async with real_async_session(self.engine) as session_persist:
            if await session_persist.get(User, owner_user_id) is None:
                session_persist.add(owner)
            if await session_persist.get(User, customer_user_id) is None:
                session_persist.add(requester)
            session_persist.add(tier2_relation)
            await session_persist.commit()

        # 2. Reload relation via real repository query in independent Session 2.
        async with real_async_session(self.engine) as session_query:
            reloaded_relation = await get_active_customer_relation_for_customer(
                session_query,
                customer_user_id=customer_user_id,
            )
            self.assertIsNotNone(reloaded_relation)
            self.assertEqual(reloaded_relation.id, relation_id)
            self.assertEqual(reloaded_relation.owner_user_id, owner_user_id)
            self.assertEqual(reloaded_relation.customer_user_id, customer_user_id)
            self.assertEqual(reloaded_relation.customer_tier, CustomerTier.TIER_2)
            self.assertEqual(reloaded_relation.customer_tier.value, "tier2")
            self.assertEqual(reloaded_relation.management_name, management_name)
            self.assertEqual(reloaded_relation.commission_rate, commission_rate)
            self.assertEqual(reloaded_relation.min_trade_quantity, min_qty)
            self.assertEqual(reloaded_relation.max_trade_quantity, max_qty)
            self.assertEqual(reloaded_relation.max_daily_trades, max_daily_tr)
            self.assertEqual(reloaded_relation.max_daily_commodity_volume, max_daily_vol)
            self.assertEqual(reloaded_relation.status, CustomerRelationStatus.ACTIVE)
            self.assertIsNone(reloaded_relation.deleted_at)

            # Project snapshot from loaded row.
            snapshot = customer_relation_snapshot(reloaded_relation)
            self.assertEqual(snapshot["customer_relation_id"], relation_id)
            self.assertEqual(snapshot["customer_owner_user_id"], owner_user_id)
            self.assertEqual(snapshot["customer_tier_snapshot"], "tier2")
            self.assertEqual(snapshot["customer_management_name_snapshot"], management_name)
            self.assertEqual(snapshot["customer_commission_rate_snapshot"], commission_rate)
            self.assertEqual(
                snapshot["customer_commission_context"],
                {
                    "min_trade_quantity": min_qty,
                    "max_trade_quantity": max_qty,
                    "max_daily_trades": max_daily_tr,
                    "max_daily_commodity_volume": max_daily_vol,
                },
            )

            # Apply current commission behavior for SELL and internal offer fixture price.
            calculated_price = apply_customer_commission(
                raw_price=raw_fixture_price,
                rate=reloaded_relation.commission_rate,
                offer_type=OfferType.SELL,
            )
            self.assertEqual(calculated_price, expected_rounded_price)

        # 3. Open independent Session 3 and prove durable active persistence.
        async with real_async_session(self.engine) as session_verify:
            verified_relation = await get_active_customer_relation_for_customer(
                session_verify,
                customer_user_id=customer_user_id,
            )
            self.assertIsNotNone(verified_relation)
            self.assertEqual(verified_relation.id, relation_id)
            self.assertEqual(verified_relation.owner_user_id, owner_user_id)
            self.assertEqual(verified_relation.customer_user_id, customer_user_id)
            self.assertEqual(verified_relation.customer_tier, CustomerTier.TIER_2)
            self.assertEqual(verified_relation.customer_tier.value, "tier2")
            self.assertEqual(verified_relation.management_name, management_name)
            self.assertEqual(verified_relation.commission_rate, commission_rate)
            self.assertEqual(verified_relation.min_trade_quantity, min_qty)
            self.assertEqual(verified_relation.max_trade_quantity, max_qty)
            self.assertEqual(verified_relation.max_daily_trades, max_daily_tr)
            self.assertEqual(verified_relation.max_daily_commodity_volume, max_daily_vol)
            self.assertEqual(verified_relation.status, CustomerRelationStatus.ACTIVE)
            self.assertIsNone(verified_relation.deleted_at)


if __name__ == "__main__":
    unittest.main()
