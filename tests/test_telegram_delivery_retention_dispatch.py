from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest

from core.services.telegram_delivery_retention_service import (
    _purge_publisher_dispatch_command_for_job,
)


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class TelegramDeliveryRetentionDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_absent_command_allows_job_purge(self):
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_Result(None)),
            delete=AsyncMock(),
            flush=AsyncMock(),
        )

        self.assertEqual(
            await _purge_publisher_dispatch_command_for_job(
                db,
                job_id=9,
                dry_run=False,
            ),
            "absent",
        )
        db.delete.assert_not_awaited()

    async def test_terminal_command_is_deleted_with_its_job(self):
        command = SimpleNamespace(state="acknowledged", job_id=9)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_Result(command)),
            delete=AsyncMock(),
            flush=AsyncMock(),
        )

        self.assertEqual(
            await _purge_publisher_dispatch_command_for_job(
                db,
                job_id=9,
                dry_run=False,
            ),
            "purged",
        )
        db.delete.assert_awaited_once_with(command)
        db.flush.assert_awaited_once()

    async def test_live_command_blocks_job_purge(self):
        for state in ("pending", "sent", "retry_due"):
            command = SimpleNamespace(state=state, job_id=9)
            db = SimpleNamespace(
                execute=AsyncMock(return_value=_Result(command)),
                delete=AsyncMock(),
                flush=AsyncMock(),
            )
            with self.subTest(state=state):
                self.assertEqual(
                    await _purge_publisher_dispatch_command_for_job(
                        db,
                        job_id=9,
                        dry_run=False,
                    ),
                    "blocked",
                )
                db.delete.assert_not_awaited()

    async def test_dry_run_does_not_delete_a_terminal_command(self):
        command = SimpleNamespace(state="failed", job_id=9)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_Result(command)),
            delete=AsyncMock(),
            flush=AsyncMock(),
        )

        self.assertEqual(
            await _purge_publisher_dispatch_command_for_job(
                db,
                job_id=9,
                dry_run=True,
            ),
            "purged",
        )
        db.delete.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
