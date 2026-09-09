"""Direct-characterization tests for api.routers.chat.get_channels under P1-00b.

Characterizes list_manageable_channels failure boundary under WA-P1-00B-FREEZE-20260904-001.
This is direct characterization only, without executed-seed promotion,
canonical-blocker reduction or Stage completion.
Does not claim authentication, concurrent or process-reload coverage.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import get_channels  # noqa: E402


class CharacterizeChatGetChannelsListingFailureTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing get_channels dependency failure boundary."""

    @patch("api.routers.chat.list_manageable_channels", new_callable=AsyncMock)
    async def test_get_channels_raises_when_list_manageable_channels_fails(
        self,
        mock_list_manageable_channels: AsyncMock,
    ) -> None:
        db_sentinel = object()
        current_user_sentinel = object()
        sentinel_error = RuntimeError("list-manageable-channels-failure-sentinel")
        mock_list_manageable_channels.side_effect = sentinel_error

        with self.assertRaises(RuntimeError) as context:
            await get_channels(
                current_user=current_user_sentinel,
                db=db_sentinel,
            )

        self.assertIs(context.exception, sentinel_error)
        mock_list_manageable_channels.assert_awaited_once_with(db_sentinel)


if __name__ == "__main__":
    unittest.main()
