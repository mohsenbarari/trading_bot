import unittest
from unittest.mock import AsyncMock, MagicMock

from core.telegram_delivery_queue_owner import (
    TelegramDeliveryQueueAlreadyOwnedError,
    TelegramDeliveryQueueOwnerLease,
    TelegramDeliveryQueueOwnerLeaseLostError,
    acquire_telegram_delivery_queue_owner,
)


class TelegramDeliveryQueueOwnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_acquire_holds_one_backend_and_releases_it(self):
        engine = MagicMock()
        connection = AsyncMock()
        result = MagicMock()
        result.one.return_value = (True, 731)
        connection.execute.return_value = result
        engine.connect = AsyncMock(return_value=connection)

        lease = await acquire_telegram_delivery_queue_owner(engine)

        self.assertEqual(lease.backend_pid, 731)
        await lease.close()
        self.assertEqual(connection.execute.await_count, 2)
        connection.close.assert_awaited_once()

    async def test_second_process_owner_is_rejected_before_start(self):
        engine = MagicMock()
        connection = AsyncMock()
        result = MagicMock()
        result.one.return_value = (False, 732)
        connection.execute.return_value = result
        engine.connect = AsyncMock(return_value=connection)

        with self.assertRaisesRegex(
            TelegramDeliveryQueueAlreadyOwnedError, "already_active"
        ):
            await acquire_telegram_delivery_queue_owner(engine)

        connection.close.assert_awaited_once()

    async def test_backend_replacement_fails_closed_before_provider_entry(self):
        connection = AsyncMock()
        result = MagicMock()
        result.one.return_value = (9002, True)
        connection.execute.return_value = result
        lease = TelegramDeliveryQueueOwnerLease(
            connection=connection,
            backend_pid=9001,
        )

        with self.assertRaisesRegex(
            TelegramDeliveryQueueOwnerLeaseLostError, "session_changed"
        ):
            await lease.assert_held()

    async def test_missing_advisory_lock_fails_closed_on_same_backend(self):
        connection = AsyncMock()
        result = MagicMock()
        result.one.return_value = (9001, False)
        connection.execute.return_value = result
        lease = TelegramDeliveryQueueOwnerLease(
            connection=connection,
            backend_pid=9001,
        )

        with self.assertRaisesRegex(
            TelegramDeliveryQueueOwnerLeaseLostError, "lock_lost"
        ):
            await lease.assert_held()


if __name__ == "__main__":
    unittest.main()
