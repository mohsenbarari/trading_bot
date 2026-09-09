"""Characterization test for UploadBatchCreateRequest omitted room_kind failure boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadBatchCreateRequest
from models.upload_session import UploadBatchMessageKind


class TestUploadBatchRoomKindMissingCharacterization(unittest.TestCase):
    """Characterize UploadBatchCreateRequest rejection of omitted room_kind."""

    def test_omitted_room_kind_is_rejected(self):
        """Characterize ValidationError when room_kind is omitted from UploadBatchCreateRequest."""
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadBatchCreateRequest(
                target_id=10,
                message_kind=UploadBatchMessageKind.SINGLE,
                expected_items=1,
                idempotency_key="synthetic-batch-idempotency-key-001",
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("room_kind",))
        self.assertEqual(errors[0]["type"], "missing")


if __name__ == "__main__":
    unittest.main()
