"""Characterization test for UploadBatchCreateRequest unsupported room_kind boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadBatchCreateRequest
from models.upload_session import UploadBatchMessageKind


class TestUploadBatchRoomKindUnsupportedCharacterization(unittest.TestCase):
    """Characterize rejection of unsupported room_kind for UploadBatchCreateRequest."""

    def test_upload_batch_room_kind_unsupported_rejected(self):
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadBatchCreateRequest(
                room_kind="unsupported",
                target_id=10,
                message_kind=UploadBatchMessageKind.SINGLE,
                expected_items=1,
                idempotency_key="synthetic-room-kind-001",
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("room_kind",))
        self.assertEqual(errors[0]["type"], "enum")
        self.assertEqual(errors[0]["input"], "unsupported")


if __name__ == "__main__":
    unittest.main()
