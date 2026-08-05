"""Stage 9: opaque overtime callback identity and status copy."""

import unittest

from core.offer_overtime_bot_copy import (
    M10_REQUESTER_STATUS_QUEUED,
    M11_REQUESTER_STATUS_PRESENTED,
    M12_REQUESTER_CANCEL_BUTTON,
    M15_REQUESTER_STATUS_CANCELLED,
)
from core.offer_request_identity import generate_offer_request_public_id
from core.telegram_delivery_overtime_owner_approval_contract import (
    build_overtime_owner_approval_callback_data,
    parse_overtime_owner_approval_callback_data,
)
from core.telegram_delivery_overtime_requester_status_contract import (
    build_overtime_requester_cancel_callback_data,
    build_overtime_requester_cancel_reply_markup,
    parse_overtime_requester_cancel_callback_data,
)
from bot.overtime_request_status import (
    requester_status_text_for_result_status,
    terminal_requester_status_text,
)
from models.offer_request import OfferRequestStatus


class OvertimeBotCallbackContractTests(unittest.TestCase):
    def test_owner_and_cancel_callbacks_use_opaque_public_id(self):
        public_id = generate_offer_request_public_id()
        approve = build_overtime_owner_approval_callback_data(
            request_public_id=public_id, decision="approve"
        )
        reject = build_overtime_owner_approval_callback_data(
            request_public_id=public_id, decision="reject"
        )
        cancel = build_overtime_requester_cancel_callback_data(
            request_public_id=public_id
        )
        self.assertEqual(
            parse_overtime_owner_approval_callback_data(approve),
            (public_id, "approve"),
        )
        self.assertEqual(
            parse_overtime_owner_approval_callback_data(reject),
            (public_id, "reject"),
        )
        self.assertEqual(parse_overtime_requester_cancel_callback_data(cancel), public_id)
        self.assertIsNone(parse_overtime_requester_cancel_callback_data("otc:12:cancel"))
        markup = build_overtime_requester_cancel_reply_markup(
            request_public_id=public_id
        )
        self.assertEqual(
            markup["inline_keyboard"][0][0]["text"],
            M12_REQUESTER_CANCEL_BUTTON,
        )

    def test_requester_status_copy_matches_inventory(self):
        self.assertEqual(
            requester_status_text_for_result_status(
                OfferRequestStatus.OVERTIME_QUEUED
            ),
            M10_REQUESTER_STATUS_QUEUED,
        )
        self.assertEqual(
            requester_status_text_for_result_status(
                OfferRequestStatus.OVERTIME_DELIVERING
            ),
            M11_REQUESTER_STATUS_PRESENTED,
        )
        self.assertEqual(
            terminal_requester_status_text(cancelled=True),
            M15_REQUESTER_STATUS_CANCELLED,
        )


if __name__ == "__main__":
    unittest.main()
