"""Characterization test for UploadBatchCreateRequest explicit target_id=None failure boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadBatchCreateRequest
from models.upload_session import UploadBatchMessageKind, UploadRoomKind


class TestUploadBatchTargetIdNullCharacterization(unittest.TestCase):
    """Characterize UploadBatchCreateRequest rejection of explicit target_id=None at schema level."""

    def test_explicit_null_target_id_is_rejected(self):
        """Characterize ValidationError when target_id=None is passed to UploadBatchCreateRequest."""
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadBatchCreateRequest(
                room_kind=UploadRoomKind.DIRECT,
                target_id=None,
                message_kind=UploadBatchMessageKind.SINGLE,
                expected_items=1,
                idempotency_key="synthetic-null-target-batch-001",
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("target_id",))
        self.assertEqual(errors[0]["type"], "int_type")
        self.assertIsNone(errors[0]["input"])


if __name__ == "__main__":
    unittest.main()
