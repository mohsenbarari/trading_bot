"""Characterization test for UploadBatchCreateRequest album two-item success path."""

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


class TestUploadBatchAlbumSuccessCharacterization(unittest.TestCase):
    """Verify UploadBatchCreateRequest accepts album batches with two items."""

    def test_album_batch_with_two_items_succeeds(self):
        idempotency_key = "synthetic-album-idempotency-key-001"
        request = UploadBatchCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id=10,
            message_kind=UploadBatchMessageKind.ALBUM,
            expected_items=2,
            idempotency_key=idempotency_key,
        )

        self.assertEqual(request.room_kind, UploadRoomKind.DIRECT)
        self.assertEqual(request.target_id, 10)
        self.assertEqual(request.message_kind, UploadBatchMessageKind.ALBUM)
        self.assertEqual(request.expected_items, 2)
        self.assertEqual(request.idempotency_key, idempotency_key)
        self.assertEqual(request.caption_policy, UploadCaptionPolicy.NONE)


if __name__ == "__main__":
    unittest.main()
