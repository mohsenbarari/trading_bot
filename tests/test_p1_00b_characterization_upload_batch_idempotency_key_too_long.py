"""Characterization test for UploadBatchCreateRequest idempotency_key maximum-length boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadBatchCreateRequest
from models.upload_session import UploadBatchMessageKind, UploadRoomKind


class TestUploadBatchIdempotencyKeyTooLongCharacterization(unittest.TestCase):
    """Characterize rejection of idempotency_key longer than 128 characters for UploadBatchCreateRequest."""

    def test_upload_batch_idempotency_key_too_long_rejected(self):
        too_long_key = "a" * 129
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadBatchCreateRequest(
                idempotency_key=too_long_key,
                room_kind=UploadRoomKind.DIRECT,
                target_id=10,
                message_kind=UploadBatchMessageKind.SINGLE,
                expected_items=1,
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("idempotency_key",))
        self.assertEqual(errors[0]["type"], "string_too_long")
        self.assertEqual(errors[0]["ctx"]["max_length"], 128)
        self.assertEqual(errors[0]["input"], too_long_key)


if __name__ == "__main__":
    unittest.main()
