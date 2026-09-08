"""Characterization test for UploadBatchCreateRequest idempotency_key maximum-length boundary."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadBatchCreateRequest
from models.upload_session import (
    UploadBatchMessageKind,
    UploadCaptionPolicy,
    UploadRoomKind,
)


class TestUploadBatchIdempotencyKeyMaximumCharacterization(unittest.TestCase):
    """Characterize acceptance of idempotency_key at the 128-character maximum for UploadBatchCreateRequest."""

    def test_upload_batch_idempotency_key_maximum_length_succeeds(self):
        idempotency_key = "aB1cD2eF3gH4jK5m" * 8
        self.assertEqual(len(idempotency_key), 128)

        request = UploadBatchCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id=10,
            message_kind=UploadBatchMessageKind.SINGLE,
            expected_items=1,
            idempotency_key=idempotency_key,
        )

        self.assertEqual(request.room_kind, UploadRoomKind.DIRECT)
        self.assertEqual(request.target_id, 10)
        self.assertEqual(request.message_kind, UploadBatchMessageKind.SINGLE)
        self.assertEqual(request.expected_items, 1)
        self.assertEqual(request.idempotency_key, idempotency_key)
        self.assertEqual(len(request.idempotency_key), 128)
        self.assertEqual(request.caption_policy, UploadCaptionPolicy.NONE)


if __name__ == "__main__":
    unittest.main()
