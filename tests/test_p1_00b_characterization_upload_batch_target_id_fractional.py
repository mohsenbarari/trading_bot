"""Characterization test for UploadBatchCreateRequest fractional target_id failure boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadBatchCreateRequest
from models.upload_session import UploadBatchMessageKind, UploadRoomKind


class TestUploadBatchTargetIdFractionalCharacterization(unittest.TestCase):
    """Characterize UploadBatchCreateRequest rejection of fractional target_id."""

    def test_upload_batch_target_id_fractional_rejected(self):
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadBatchCreateRequest(
                room_kind=UploadRoomKind.DIRECT,
                target_id=42.5,
                message_kind=UploadBatchMessageKind.SINGLE,
                expected_items=1,
                idempotency_key="synthetic-fractional-target-batch-001",
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("target_id",))
        self.assertEqual(errors[0]["type"], "int_from_float")
        self.assertEqual(errors[0]["input"], 42.5)


if __name__ == "__main__":
    unittest.main()
