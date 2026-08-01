from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

from core.services.promotion_continuity_participants import (
    PromotionContinuityParticipantsError,
    stage_promotion_auth_and_upload_cleanup,
)
from core.services.promotion_session_invalidation_service import (
    PromotionSessionInvalidationResult,
    PromotionSessionInvalidationTermFacts,
)
from core.services.promotion_upload_cleanup_service import PromotionUploadCleanupResult


OPERATION_ID = UUID("12345678-1234-4234-9234-123456789abc")
CUTOVER_AT = datetime(2026, 7, 31, 12, 30, 1, tzinfo=timezone.utc)
FACTS = PromotionSessionInvalidationTermFacts(
    operation_id=OPERATION_ID,
    writer_site="webapp_ir",
    writer_epoch=12,
    writer_lease_id="lease-12",
    witness_transition_id="transition-12",
)


def auth_result(**overrides):
    values = dict(
        operation_id=OPERATION_ID,
        writer_site="webapp_ir",
        writer_epoch=12,
        writer_lease_id="lease-12",
        witness_transition_id="transition-12",
        cutover_at=CUTOVER_AT,
        minimum_token_iat=int(CUTOVER_AT.timestamp()),
        invalidated_sessions=4,
        expired_login_requests=2,
        cancelled_recovery_requests=1,
        applied=True,
    )
    values.update(overrides)
    return PromotionSessionInvalidationResult(**values)


def upload_result(**overrides):
    values = dict(
        operation_id=OPERATION_ID,
        writer_site="webapp_ir",
        writer_epoch=12,
        writer_lease_id="lease-12",
        witness_transition_id="transition-12",
        cutover_at=CUTOVER_AT,
        cancelled_session_ids=("upload-1",),
        cancelled_batch_ids=("batch-1",),
        applied=True,
    )
    values.update(overrides)
    return PromotionUploadCleanupResult(**values)


class PromotionContinuityParticipantsTests(unittest.IsolatedAsyncioTestCase):
    async def test_stages_auth_then_upload_under_one_binding_without_transaction_control(self):
        db = Mock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        sequence: list[str] = []

        async def invalidate(*args, **kwargs):
            sequence.append("auth")
            return auth_result()

        async def cleanup(*args, **kwargs):
            sequence.append("uploads")
            return upload_result()

        with patch(
            "core.services.promotion_continuity_participants.require_active_promotion_session_invalidation_binding",
            side_effect=[FACTS, FACTS],
        ), patch(
            "core.services.promotion_continuity_participants.invalidate_sessions_on_promotion",
            side_effect=invalidate,
        ), patch(
            "core.services.promotion_continuity_participants.cancel_and_expire_unfinalized_uploads_on_promotion",
            side_effect=cleanup,
        ):
            result = await stage_promotion_auth_and_upload_cleanup(
                db,
                binding=object(),
                now=CUTOVER_AT,
            )

        self.assertEqual(sequence, ["auth", "uploads"])
        self.assertEqual(result.auth.operation_id, OPERATION_ID)
        self.assertEqual(result.uploads.cancelled_session_ids, ("upload-1",))
        db.commit.assert_not_awaited()
        db.rollback.assert_not_awaited()

    async def test_stops_when_term_changes_after_mutations(self):
        changed = PromotionSessionInvalidationTermFacts(
            operation_id=OPERATION_ID,
            writer_site="webapp_ir",
            writer_epoch=13,
            writer_lease_id="lease-13",
            witness_transition_id="transition-13",
        )
        with patch(
            "core.services.promotion_continuity_participants.require_active_promotion_session_invalidation_binding",
            side_effect=[FACTS, changed],
        ), patch(
            "core.services.promotion_continuity_participants.invalidate_sessions_on_promotion",
            new=AsyncMock(return_value=auth_result()),
        ), patch(
            "core.services.promotion_continuity_participants.cancel_and_expire_unfinalized_uploads_on_promotion",
            new=AsyncMock(return_value=upload_result()),
        ):
            with self.assertRaisesRegex(PromotionContinuityParticipantsError, "term changed"):
                await stage_promotion_auth_and_upload_cleanup(Mock(), binding=object())

    async def test_stops_when_participant_evidence_is_not_the_same_operation(self):
        with patch(
            "core.services.promotion_continuity_participants.require_active_promotion_session_invalidation_binding",
            side_effect=[FACTS, FACTS],
        ), patch(
            "core.services.promotion_continuity_participants.invalidate_sessions_on_promotion",
            new=AsyncMock(return_value=auth_result()),
        ), patch(
            "core.services.promotion_continuity_participants.cancel_and_expire_unfinalized_uploads_on_promotion",
            new=AsyncMock(return_value=upload_result(writer_epoch=13)),
        ):
            with self.assertRaisesRegex(PromotionContinuityParticipantsError, "mismatched"):
                await stage_promotion_auth_and_upload_cleanup(Mock(), binding=object())

    async def test_rejects_bool_epoch_in_a_forged_participant_result(self):
        with patch(
            "core.services.promotion_continuity_participants.require_active_promotion_session_invalidation_binding",
            side_effect=[FACTS, FACTS],
        ), patch(
            "core.services.promotion_continuity_participants.invalidate_sessions_on_promotion",
            new=AsyncMock(return_value=auth_result(writer_epoch=True)),
        ), patch(
            "core.services.promotion_continuity_participants.cancel_and_expire_unfinalized_uploads_on_promotion",
            new=AsyncMock(return_value=upload_result(writer_epoch=True)),
        ):
            with self.assertRaisesRegex(PromotionContinuityParticipantsError, "writer term is invalid"):
                await stage_promotion_auth_and_upload_cleanup(Mock(), binding=object())

    async def test_wraps_a_participant_failure_without_trying_commit_or_rollback(self):
        db = Mock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        with patch(
            "core.services.promotion_continuity_participants.require_active_promotion_session_invalidation_binding",
            return_value=FACTS,
        ), patch(
            "core.services.promotion_continuity_participants.invalidate_sessions_on_promotion",
            new=AsyncMock(side_effect=RuntimeError("DB write failed")),
        ):
            # Unexpected exceptions deliberately propagate: only known
            # participant safety errors get contextual wrapping.
            with self.assertRaisesRegex(RuntimeError, "DB write failed"):
                await stage_promotion_auth_and_upload_cleanup(db, binding=object())
        db.commit.assert_not_awaited()
        db.rollback.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
