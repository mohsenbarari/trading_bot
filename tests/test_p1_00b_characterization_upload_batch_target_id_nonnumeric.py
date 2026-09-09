"""Characterization test for UploadBatchCreateRequest nonnumeric target_id failure boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadBatchCreateRequest
from models.upload_session import UploadBatchMessageKind, UploadRoomKind


class TestUploadBatchTargetIdNonnumericCharacterization(unittest.TestCase):
    """Characterize UploadBatchCreateRequest rejection of nonnumeric target_id."""

    def test_upload_batch_target_id_nonnumeric_rejected(self):
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadBatchCreateRequest(
                room_kind=UploadRoomKind.DIRECT,
                target_id="not-an-integer",
                message_kind=UploadBatchMessageKind.SINGLE,
                expected_items=1,
                idempotency_key="synthetic-nonnumeric-target-batch-001",
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("target_id",))
        self.assertEqual(errors[0]["type"], "int_parsing")
        self.assertEqual(errors[0]["input"], "not-an-integer")


if __name__ == "__main__":
    unittest.main()
