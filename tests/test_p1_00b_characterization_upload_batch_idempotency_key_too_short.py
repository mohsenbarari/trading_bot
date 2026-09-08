"""Characterization test for UploadBatchCreateRequest idempotency_key minimum-length boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadBatchCreateRequest
from models.upload_session import UploadBatchMessageKind, UploadRoomKind


class TestUploadBatchIdempotencyKeyTooShortCharacterization(unittest.TestCase):
    """Characterize rejection of idempotency_key shorter than 8 characters for UploadBatchCreateRequest."""

    def test_upload_batch_idempotency_key_too_short_rejected(self):
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadBatchCreateRequest(
                idempotency_key="shortid",
                room_kind=UploadRoomKind.DIRECT,
                target_id=10,
                message_kind=UploadBatchMessageKind.SINGLE,
                expected_items=1,
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("idempotency_key",))
        self.assertEqual(errors[0]["type"], "string_too_short")
        self.assertEqual(errors[0]["ctx"]["min_length"], 8)
        self.assertEqual(errors[0]["input"], "shortid")


if __name__ == "__main__":
    unittest.main()
