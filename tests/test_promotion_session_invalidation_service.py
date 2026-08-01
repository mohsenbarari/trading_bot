from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID

from core.application_writer_term import ValidatedWriterTerm
from core.services.promotion_session_invalidation_service import (
    PromotionAccessTokenEpochError,
    PromotionSessionInvalidationBinding,
    PromotionSessionInvalidationError,
    bind_promotion_session_invalidation,
    enforce_access_token_auth_epoch,
    invalidate_sessions_on_promotion,
    require_access_token_current_for_epoch,
    require_active_promotion_session_invalidation_binding,
)
from models.promotion_auth_epoch import PromotionAuthEpoch, PromotionAuthEpochOperation


NOW = datetime(2026, 7, 31, 12, 0, 0, 500_000, tzinfo=timezone.utc)
OPERATION_ID = UUID("12345678-1234-4234-9234-123456789abc")


def writer_term(
    *,
    site: str = "webapp_ir",
    epoch: int = 8,
    lease_id: str = "lease-8",
    transition_id: str = "transition-8",
) -> ValidatedWriterTerm:
    return ValidatedWriterTerm(
        holder_site=site,
        writer_epoch=epoch,
        lease_id=lease_id,
        issued_at=NOW - timedelta(seconds=20),
        expires_at=NOW + timedelta(seconds=30),
        witness_transition_id=transition_id,
    )


def binding(*, term: ValidatedWriterTerm | None = None, operation_id: UUID = OPERATION_ID):
    return PromotionSessionInvalidationBinding(
        operation_id=operation_id,
        writer_term=term or writer_term(),
    )


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _UpdateResult:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


class FakePromotionTransaction:
    """Minimal caller-owned AsyncSession-shaped transaction for unit tests."""

    def __init__(
        self,
        *,
        epoch: PromotionAuthEpoch | None = None,
        operation: PromotionAuthEpochOperation | None = None,
        flush_error: Exception | None = None,
    ):
        self.epoch = epoch
        self.operation = operation
        self.flush_error = flush_error
        self.executed = []
        self.added = []
        self._update_count = 0
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.flush = AsyncMock(side_effect=flush_error)

    def add(self, value):
        self.added.append(value)
        if isinstance(value, PromotionAuthEpoch):
            self.epoch = value
        if isinstance(value, PromotionAuthEpochOperation):
            self.operation = value

    async def execute(self, statement):
        self.executed.append(statement)
        if getattr(statement, "is_select", False):
            if "promotion_auth_epoch_operations" in str(statement):
                params = statement.compile().params
                requested_operation = params.get("operation_id_1")
                if (
                    self.operation is not None
                    and requested_operation == self.operation.operation_id
                ):
                    return _ScalarResult(self.operation)
                return _ScalarResult(None)
            return _ScalarResult(self.epoch)
        # session, pending-login, and active-recovery bulk updates.
        rowcount = (4, 2, 3)[self._update_count]
        self._update_count += 1
        return _UpdateResult(rowcount)


def stored_epoch(
    *,
    operation_id: UUID = OPERATION_ID,
    term: ValidatedWriterTerm | None = None,
    cutover_at: datetime = NOW - timedelta(seconds=5),
    minimum_token_iat: int | None = None,
) -> PromotionAuthEpoch:
    term = term or writer_term()
    return PromotionAuthEpoch(
        id=1,
        operation_id=str(operation_id),
        writer_site=term.holder_site,
        writer_epoch=term.writer_epoch,
        writer_lease_id=term.lease_id,
        witness_transition_id=term.witness_transition_id,
        cutover_at=cutover_at,
        minimum_token_iat=(
            int(cutover_at.timestamp()) if minimum_token_iat is None else minimum_token_iat
        ),
    )


def stored_operation(
    *,
    operation_id: UUID = OPERATION_ID,
    term: ValidatedWriterTerm | None = None,
    cutover_at: datetime = NOW - timedelta(seconds=5),
    minimum_token_iat: int | None = None,
) -> PromotionAuthEpochOperation:
    term = term or writer_term()
    return PromotionAuthEpochOperation(
        operation_id=str(operation_id),
        writer_site=term.holder_site,
        writer_epoch=term.writer_epoch,
        writer_lease_id=term.lease_id,
        witness_transition_id=term.witness_transition_id,
        cutover_at=cutover_at,
        minimum_token_iat=(
            int(cutover_at.timestamp()) if minimum_token_iat is None else minimum_token_iat
        ),
    )


class PromotionSessionInvalidationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_binding_refuses_default_disabled_writer_term_policy(self):
        with patch(
            "core.services.promotion_session_invalidation_service.require_application_writer_term",
            return_value=None,
        ):
            with self.assertRaisesRegex(PromotionSessionInvalidationError, "enforcement is disabled"):
                bind_promotion_session_invalidation(operation_id=OPERATION_ID)

    async def test_creates_epoch_and_stages_all_auth_state_without_committing(self):
        term = writer_term()
        db = FakePromotionTransaction()
        with patch(
            "core.services.promotion_session_invalidation_service.require_application_writer_term",
            return_value=term,
        ) as live_term:
            result = await invalidate_sessions_on_promotion(db, binding=binding(term=term), now=NOW)

        self.assertTrue(result.applied)
        self.assertEqual(result.writer_site, "webapp_ir")
        self.assertEqual(result.writer_epoch, 8)
        # ceil(12:00:00.500) prevents a same-second pre-cutover JWT from
        # passing merely because NumericDate has whole-second precision.
        self.assertEqual(result.minimum_token_iat, math_ceil_timestamp(NOW))
        self.assertEqual((result.invalidated_sessions, result.expired_login_requests, result.cancelled_recovery_requests), (4, 2, 3))
        self.assertIsInstance(db.epoch, PromotionAuthEpoch)
        self.assertEqual(db.epoch.operation_id, str(OPERATION_ID))
        self.assertEqual(len(db.executed), 5)
        self.assertIsInstance(db.operation, PromotionAuthEpochOperation)
        db.flush.assert_awaited_once()
        db.commit.assert_not_awaited()
        db.rollback.assert_not_awaited()
        self.assertEqual(live_term.call_count, 2)

    async def test_exact_replay_is_idempotent_but_other_operation_same_term_is_refused(self):
        term = writer_term()
        existing = stored_epoch(term=term)
        db = FakePromotionTransaction(epoch=existing, operation=stored_operation(term=term))
        with patch(
            "core.services.promotion_session_invalidation_service.require_application_writer_term",
            return_value=term,
        ):
            result = await invalidate_sessions_on_promotion(db, binding=binding(term=term), now=NOW)

        self.assertFalse(result.applied)
        self.assertEqual(len(db.executed), 2)
        db.flush.assert_not_awaited()
        db.commit.assert_not_awaited()

        db = FakePromotionTransaction(epoch=stored_epoch(term=term), operation=stored_operation(term=term))
        other_binding = binding(
            term=term,
            operation_id=UUID("22345678-1234-4234-9234-123456789abc"),
        )
        with patch(
            "core.services.promotion_session_invalidation_service.require_application_writer_term",
            return_value=term,
        ):
            with self.assertRaisesRegex(PromotionSessionInvalidationError, "already bound"):
                await invalidate_sessions_on_promotion(db, binding=other_binding, now=NOW)
        self.assertEqual(len(db.executed), 2)
        db.flush.assert_not_awaited()

    async def test_term_regression_and_binding_mismatch_fail_before_mutation(self):
        active = writer_term(epoch=8)
        old = writer_term(epoch=7, lease_id="lease-7", transition_id="transition-7")
        db = FakePromotionTransaction(
            epoch=stored_epoch(term=active),
            operation=stored_operation(term=active),
        )
        with patch(
            "core.services.promotion_session_invalidation_service.require_application_writer_term",
            return_value=old,
        ):
            with self.assertRaisesRegex(PromotionSessionInvalidationError, "regressed"):
                await invalidate_sessions_on_promotion(
                    db,
                    binding=binding(
                        term=old,
                        operation_id=UUID("32345678-1234-4234-9234-123456789abc"),
                    ),
                    now=NOW,
                )
        self.assertEqual(len(db.executed), 2)
        db.flush.assert_not_awaited()

        db = FakePromotionTransaction()
        with patch(
            "core.services.promotion_session_invalidation_service.require_application_writer_term",
            return_value=active,
        ):
            with self.assertRaisesRegex(PromotionSessionInvalidationError, "does not match"):
                require_active_promotion_session_invalidation_binding(binding(term=old))
        self.assertEqual(db.executed, [])

    async def test_flush_failure_never_commits_partial_cutover(self):
        term = writer_term()
        db = FakePromotionTransaction(flush_error=RuntimeError("constraint failure"))
        with patch(
            "core.services.promotion_session_invalidation_service.require_application_writer_term",
            return_value=term,
        ):
            with self.assertRaisesRegex(RuntimeError, "constraint failure"):
                await invalidate_sessions_on_promotion(db, binding=binding(term=term), now=NOW)

        self.assertEqual(len(db.executed), 5)
        db.flush.assert_awaited_once()
        db.commit.assert_not_awaited()
        db.rollback.assert_not_awaited()

    async def test_epoch_rejects_legacy_sessionless_and_old_tokens_but_allows_fresh_access(self):
        epoch = stored_epoch(minimum_token_iat=1_700_000_001)
        # Before any row exists, the existing token behavior is retained.
        require_access_token_current_for_epoch({"sub": "5"}, None, now=NOW)

        for payload in (
            {"sub": "5", "type": "access"},
            {"sub": "5", "type": "access", "iat": 1_700_000_000},
            {"sub": "5", "type": "refresh", "iat": 1_700_000_001},
        ):
            with self.assertRaises(PromotionAccessTokenEpochError):
                require_access_token_current_for_epoch(payload, epoch, now=NOW)

        # No sid is required after a cutover: a *fresh* sessionless access
        # JWT remains valid, whereas legacy sessionless JWTs are rejected.
        require_access_token_current_for_epoch(
            {"sub": "5", "type": "access", "iat": 1_700_000_001},
            epoch,
            now=datetime.fromtimestamp(1_700_000_002, tz=timezone.utc),
        )

        db = FakePromotionTransaction(epoch=epoch)
        await enforce_access_token_auth_epoch(
            db,
            {"sub": "5", "type": "access", "iat": 1_700_000_001},
            now=datetime.fromtimestamp(1_700_000_002, tz=timezone.utc),
        )

    async def test_operation_id_used_by_a_prior_term_cannot_be_replayed(self):
        prior_term = writer_term(epoch=8)
        current_term = writer_term(epoch=9, lease_id="lease-9", transition_id="transition-9")
        current_operation = UUID("42345678-1234-4234-9234-123456789abc")
        db = FakePromotionTransaction(
            epoch=stored_epoch(operation_id=current_operation, term=current_term),
            operation=stored_operation(operation_id=OPERATION_ID, term=prior_term),
        )
        with patch(
            "core.services.promotion_session_invalidation_service.require_application_writer_term",
            return_value=current_term,
        ):
            with self.assertRaisesRegex(PromotionSessionInvalidationError, "replay conflicts"):
                await invalidate_sessions_on_promotion(
                    db,
                    binding=binding(term=current_term, operation_id=OPERATION_ID),
                    now=NOW,
                )
        self.assertEqual(len(db.executed), 2)


def math_ceil_timestamp(value: datetime) -> int:
    seconds = value.timestamp()
    whole = int(seconds)
    return whole if seconds == whole else whole + 1


if __name__ == "__main__":
    unittest.main()
