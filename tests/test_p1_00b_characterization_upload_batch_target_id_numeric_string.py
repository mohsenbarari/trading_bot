"""Characterization test for UploadBatchCreateRequest numeric-string target_id coercion."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadBatchCreateRequest
from models.upload_session import (
    UploadBatchMessageKind,
    UploadRoomKind,
)


class TestUploadBatchTargetIdNumericStringCharacterization(unittest.TestCase):
    """Characterize UploadBatchCreateRequest coercion of numeric-string target_id."""

    def test_upload_batch_target_id_numeric_string_coerced(self):
        idempotency_key = "synthetic-numeric-string-target-key-001"
        request = UploadBatchCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id="42",
            message_kind=UploadBatchMessageKind.SINGLE,
            expected_items=1,
            idempotency_key=idempotency_key,
        )

        self.assertEqual(request.target_id, 42)
        self.assertIs(type(request.target_id), int)


if __name__ == "__main__":
    unittest.main()
