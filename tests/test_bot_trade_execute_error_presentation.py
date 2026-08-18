import unittest

from bot.handlers.trade_execute import _user_facing_trade_error


class BotTradeExecuteErrorPresentationTests(unittest.TestCase):
    def test_terminal_overtime_errors_are_short_and_do_not_leak_api_metadata(self):
        detail = {
            "error_code": "overtime_cancelled_by_requester",
            "message": "امکان انجام این معامله وجود ندارد.",
            "workflow": "overtime",
            "request_public_id": "orq_private_identifier",
            "offer_id": 62,
        }

        message = _user_facing_trade_error(detail)

        self.assertEqual(message, "این درخواست قبلاً لغو شده است.")
        self.assertNotIn("request_public_id", message)
        self.assertNotIn("offer_id", message)
        self.assertNotIn("orq_private_identifier", message)

    def test_structured_errors_use_the_safe_message_and_unknown_values_fail_closed(self):
        self.assertEqual(
            _user_facing_trade_error({"error_code": "other", "message": "پیام کوتاه"}),
            "پیام کوتاه",
        )
        self.assertEqual(
            _user_facing_trade_error({"request_public_id": "private"}),
            "امکان انجام این معامله وجود ندارد.",
        )
        self.assertEqual(
            _user_facing_trade_error(None),
            "امکان انجام این معامله وجود ندارد.",
        )


if __name__ == "__main__":
    unittest.main()
