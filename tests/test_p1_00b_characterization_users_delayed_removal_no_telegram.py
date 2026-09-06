"""Characterization test for api.routers.users.enqueue_delayed_removal_telegram_notification_api.

This unit covers only the telegram_id=None early return, does not establish successful delivery or Offer/Request-origin evidence, and does not complete P1-00b.
"""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import enqueue_delayed_removal_telegram_notification_api


class TestUsersDelayedRemovalNoTelegram(unittest.IsolatedAsyncioTestCase):
    """This unit covers only the telegram_id=None early return, does not establish successful delivery or Offer/Request-origin evidence, and does not complete P1-00b."""

    async def test_enqueue_delayed_removal_telegram_notification_api_no_telegram_id(self):
        """This unit covers only the telegram_id=None early return, does not establish successful delivery or Offer/Request-origin evidence, and does not complete P1-00b."""
        user = SimpleNamespace(telegram_id=None)
        db_sentinel = object()

        with patch(
            "core.services.telegram_notification_outbox_service.enqueue_delayed_restriction_telegram_notification_once",
            new_callable=AsyncMock,
        ) as mock_enqueue:
            result = await enqueue_delayed_removal_telegram_notification_api(
                db_sentinel,  # type: ignore[arg-type]
                user=user,  # type: ignore[arg-type]
                is_block=True,
                delay_seconds=120,
            )

            self.assertIsNone(result)
            mock_enqueue.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
