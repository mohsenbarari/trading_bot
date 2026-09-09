"""Characterization test for UploadBatchCreateRequest explicit room_kind=None failure boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadBatchCreateRequest
from models.upload_session import UploadBatchMessageKind


class TestUploadBatchRoomKindNullCharacterization(unittest.TestCase):
    """Characterize UploadBatchCreateRequest rejection of explicit room_kind=None at schema level."""

    def test_explicit_null_room_kind_is_rejected(self):
        """Characterize ValidationError when room_kind=None is passed to UploadBatchCreateRequest."""
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadBatchCreateRequest(
                room_kind=None,
                target_id=10,
                message_kind=UploadBatchMessageKind.SINGLE,
                expected_items=1,
                idempotency_key="synthetic-null-room-kind-batch-001",
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("room_kind",))
        self.assertEqual(errors[0]["type"], "enum")
        self.assertIsNone(errors[0]["input"])


if __name__ == "__main__":
    unittest.main()
