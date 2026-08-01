from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.promotion_session_invalidation_service import (
    PromotionSessionInvalidationError,
    PromotionSessionInvalidationTermFacts,
)
from core.services.promotion_upload_cleanup_service import (
    PromotionUploadCleanupError,
    cancel_and_expire_unfinalized_uploads_on_promotion,
)
from models.promotion_auth_epoch import PromotionAuthEpoch
from models.upload_session import (
    UploadBatch,
    UploadBatchStatus,
    UploadSession,
    UploadSessionStatus,
)


class _Result:
    def __init__(self, *, scalar=None, rows=()):
        self._scalar = scalar
        self._rows = list(rows)

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


class _RecordingAsyncSession(AsyncSession):
    def __init__(self, results):
        super().__init__()
        self._results = list(results)
        self.statements = []
        self.flush_count = 0

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        if not self._results:
            raise AssertionError("unexpected SQL statement")
        return self._results.pop(0)

    async def flush(self, *args, **kwargs):
        self.flush_count += 1


class PromotionUploadCleanupServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.operation_id = uuid4()
        self.cutover_at = datetime(2026, 7, 31, 11, 15, 30, tzinfo=timezone.utc)
        self.facts = PromotionSessionInvalidationTermFacts(
            operation_id=self.operation_id,
            writer_site="webapp_ir",
            writer_epoch=17,
            writer_lease_id="lease-20260731-00017",
            witness_transition_id="transition-20260731-00017",
        )

    def _epoch(self, **overrides):
        values = {
            "id": 1,
            "operation_id": str(self.operation_id),
            "writer_site": self.facts.writer_site,
            "writer_epoch": self.facts.writer_epoch,
            "writer_lease_id": self.facts.writer_lease_id,
            "witness_transition_id": self.facts.witness_transition_id,
            "cutover_at": self.cutover_at,
            "minimum_token_iat": int(self.cutover_at.timestamp()),
        }
        values.update(overrides)
        return PromotionAuthEpoch(**values)

    async def _call(self, db, *, side_effect=None):
        with patch(
            "core.services.promotion_upload_cleanup_service.require_active_promotion_session_invalidation_binding",
            side_effect=side_effect or [self.facts, self.facts],
        ):
            return await cancel_and_expire_unfinalized_uploads_on_promotion(
                db,
                binding=object(),
            )

    async def test_cancels_only_nonvisible_uploads_after_epoch_and_exact_term(self):
        batch = UploadBatch(id="batch-1", status=UploadBatchStatus.UPLOADING)
        uploaded = UploadSession(
            id="session-1",
            batch_id="batch-1",
            status=UploadSessionStatus.UPLOADED,
            final_chat_file_id=None,
        )
        finalizing = UploadSession(
            id="session-2",
            batch_id=None,
            status=UploadSessionStatus.FINALIZING,
            final_chat_file_id=None,
        )
        already_expired = UploadSession(
            id="session-expired",
            batch_id="batch-1",
            status=UploadSessionStatus.EXPIRED,
            final_chat_file_id=None,
        )
        db = _RecordingAsyncSession(
            [
                _Result(),
                _Result(scalar=self._epoch()),
                _Result(rows=[batch]),
                _Result(rows=[uploaded, finalizing, already_expired]),
            ]
        )

        result = await self._call(db)

        self.assertTrue(result.applied)
        self.assertEqual(result.cancelled_batch_ids, ("batch-1",))
        self.assertEqual(result.cancelled_session_ids, ("session-1", "session-2"))
        self.assertEqual(result.cutover_at, self.cutover_at)
        self.assertEqual(batch.status, UploadBatchStatus.CANCELLED)
        self.assertEqual(batch.expires_at, self.cutover_at)
        self.assertEqual(uploaded.status, UploadSessionStatus.CANCELLED)
        self.assertEqual(finalizing.status, UploadSessionStatus.CANCELLED)
        self.assertEqual(already_expired.status, UploadSessionStatus.EXPIRED)
        self.assertIn(str(self.operation_id), uploaded.last_error)
        self.assertEqual(db.flush_count, 1)
        self.assertEqual(len(db.statements), 4)
        self.assertIn("LOCK TABLE upload_batches, upload_sessions", str(db.statements[0]))

    async def test_finalized_or_ready_upload_is_a_hard_stop_without_mutation(self):
        batch = UploadBatch(id="batch-1", status=UploadBatchStatus.UPLOADING)
        visible = UploadSession(
            id="session-visible",
            batch_id="batch-1",
            status=UploadSessionStatus.READY,
            final_chat_file_id="file-visible",
        )
        db = _RecordingAsyncSession(
            [
                _Result(),
                _Result(scalar=self._epoch()),
                _Result(rows=[batch]),
                _Result(rows=[visible]),
            ]
        )

        with self.assertRaisesRegex(
            PromotionUploadCleanupError,
            "database-visible finalized upload",
        ):
            await self._call(db)

        self.assertEqual(batch.status, UploadBatchStatus.UPLOADING)
        self.assertEqual(visible.status, UploadSessionStatus.READY)
        self.assertEqual(db.flush_count, 0)

    async def test_committing_batch_is_a_hard_stop(self):
        batch = UploadBatch(id="batch-commit", status=UploadBatchStatus.COMMITTING)
        db = _RecordingAsyncSession(
            [
                _Result(),
                _Result(scalar=self._epoch()),
                _Result(rows=[batch]),
                _Result(rows=[]),
            ]
        )

        with self.assertRaisesRegex(
            PromotionUploadCleanupError,
            "database-visible commit boundary",
        ):
            await self._call(db)
        self.assertEqual(db.flush_count, 0)

    async def test_requires_auth_epoch_staged_by_the_same_operation(self):
        db = _RecordingAsyncSession([_Result(), _Result(scalar=None)])

        with self.assertRaisesRegex(
            PromotionUploadCleanupError,
            "requires a staged auth epoch",
        ):
            await self._call(db)
        self.assertEqual(len(db.statements), 2)
        self.assertEqual(db.flush_count, 0)

    async def test_term_is_rechecked_after_the_table_lock(self):
        db = _RecordingAsyncSession(
            [
                _Result(),
                _Result(scalar=self._epoch()),
                _Result(rows=[]),
                _Result(rows=[]),
            ]
        )
        lost_term = PromotionSessionInvalidationError("term lost")

        with self.assertRaisesRegex(PromotionUploadCleanupError, "lost its active Writer Witness term"):
            await self._call(db, side_effect=[self.facts, lost_term])
        self.assertEqual(db.flush_count, 0)

    async def test_no_inflight_records_is_idempotent_and_does_not_flush(self):
        db = _RecordingAsyncSession(
            [
                _Result(),
                _Result(scalar=self._epoch()),
                _Result(rows=[]),
                _Result(rows=[]),
            ]
        )

        result = await self._call(db)

        self.assertFalse(result.applied)
        self.assertEqual(result.cancelled_batch_ids, ())
        self.assertEqual(result.cancelled_session_ids, ())
        self.assertEqual(db.flush_count, 0)

    async def test_rejects_epoch_bound_to_another_term_or_operation(self):
        db = _RecordingAsyncSession(
            [
                _Result(),
                _Result(scalar=self._epoch(writer_epoch=self.facts.writer_epoch + 1)),
            ]
        )

        with self.assertRaisesRegex(PromotionUploadCleanupError, "does not match"):
            await self._call(db)
        self.assertEqual(db.flush_count, 0)

    async def test_rejects_non_async_session(self):
        with self.assertRaisesRegex(PromotionUploadCleanupError, "AsyncSession"):
            await cancel_and_expire_unfinalized_uploads_on_promotion(
                object(),
                binding=object(),
            )


if __name__ == "__main__":
    unittest.main()
