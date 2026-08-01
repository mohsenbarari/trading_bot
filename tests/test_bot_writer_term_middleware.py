from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from bot.middlewares.writer_term import WriterTermMiddleware
from core.application_writer_term import ApplicationWriterTermError


class WriterTermMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_or_disabled_policy_allows_update(self) -> None:
        handler = AsyncMock(return_value="handled")
        with patch("bot.middlewares.writer_term.require_application_writer_term") as require_term:
            result = await WriterTermMiddleware()(handler, object(), {})

        self.assertEqual(result, "handled")
        require_term.assert_called_once_with()
        handler.assert_awaited_once()

    async def test_invalid_term_refuses_before_handler(self) -> None:
        handler = AsyncMock()
        with patch(
            "bot.middlewares.writer_term.require_application_writer_term",
            side_effect=ApplicationWriterTermError("writer term is expired"),
        ):
            result = await WriterTermMiddleware()(handler, object(), {})

        self.assertIsNone(result)
        handler.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
