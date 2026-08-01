from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from core.application_writer_term import ApplicationWriterTermError
from bot.middlewares.writer_term import WriterTermMiddleware


class WriterTermMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_or_disabled_term_allows_the_update(self) -> None:
        handler = AsyncMock(return_value="handled")
        middleware = WriterTermMiddleware()

        with patch("bot.middlewares.writer_term.require_application_writer_term") as require_term:
            result = await middleware(handler, object(), {})

        self.assertEqual(result, "handled")
        require_term.assert_called_once_with()
        handler.assert_awaited_once()

    async def test_invalid_term_refuses_before_the_handler(self) -> None:
        handler = AsyncMock()
        middleware = WriterTermMiddleware()

        with patch(
            "bot.middlewares.writer_term.require_application_writer_term",
            side_effect=ApplicationWriterTermError("writer term is expired"),
        ):
            result = await middleware(handler, object(), {})

        self.assertIsNone(result)
        handler.assert_not_awaited()
