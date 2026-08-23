from pathlib import Path
import unittest

from bot.handlers.telegram_publisher_b2b import build_publisher_b2b_router
from bot.handlers.telegram_publisher_channel_callbacks import (
    build_publisher_channel_callback_router,
)
from bot.middlewares import (
    AuthMiddleware,
    CallbackReceiptMiddleware,
    TradeContentionGateMiddleware,
)


def _middleware_types(observer) -> tuple[type, ...]:
    return tuple(type(middleware) for middleware in observer.outer_middleware)


class TelegramPublisherB2BMiddlewareTests(unittest.TestCase):
    def test_b2b_router_does_not_open_an_auth_session(self):
        router = build_publisher_b2b_router(
            identity="publisher_1",
            expected_primary_bot_id=100,
        )

        self.assertEqual(_middleware_types(router.message), ())
        self.assertEqual(_middleware_types(router.callback_query), ())
        self.assertNotIn(AuthMiddleware, _middleware_types(router.message))

    def test_channel_callback_router_keeps_the_previous_auth_chain(self):
        router = build_publisher_channel_callback_router()

        self.assertEqual(
            _middleware_types(router.callback_query),
            (
                CallbackReceiptMiddleware,
                TradeContentionGateMiddleware,
                AuthMiddleware,
            ),
        )

    def test_publisher_dispatcher_keeps_only_identity_middleware(self):
        source = Path("run_bot.py").read_text(encoding="utf-8")
        start = source.index("def configured_publisher_b2b_pollers")
        end = source.index("async def supervise_pollers")
        poller_source = source[start:end]

        self.assertIn("TelegramBotIdentityMiddleware(identity)", poller_source)
        self.assertNotIn("CallbackReceiptMiddleware()", poller_source)
        self.assertNotIn("TradeContentionGateMiddleware()", poller_source)
        self.assertNotIn("AuthMiddleware(AsyncSessionLocal)", poller_source)


if __name__ == "__main__":
    unittest.main()
