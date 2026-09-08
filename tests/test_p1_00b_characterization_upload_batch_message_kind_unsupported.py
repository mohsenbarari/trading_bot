"""Characterization test for UploadBatchCreateRequest unsupported message_kind boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadBatchCreateRequest
from models.upload_session import UploadRoomKind


class TestUploadBatchMessageKindUnsupportedCharacterization(unittest.TestCase):
    """Characterize rejection of unsupported message_kind for UploadBatchCreateRequest."""

    def test_upload_batch_message_kind_unsupported_rejected(self):
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadBatchCreateRequest(
                room_kind=UploadRoomKind.DIRECT,
                target_id=10,
                message_kind="unsupported",
                expected_items=1,
                idempotency_key="synthetic-message-kind-001",
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("message_kind",))
        self.assertEqual(errors[0]["type"], "enum")
        self.assertEqual(errors[0]["input"], "unsupported")


if __name__ == "__main__":
    unittest.main()
