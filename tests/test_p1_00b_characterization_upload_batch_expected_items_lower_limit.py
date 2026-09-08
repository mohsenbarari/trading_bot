"""Characterization test for UploadBatchCreateRequest expected_items lower-bound boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadBatchCreateRequest
from models.upload_session import UploadBatchMessageKind, UploadRoomKind


class TestUploadBatchExpectedItemsLowerLimitCharacterization(unittest.TestCase):
    """Characterize rejection of expected_items below lower limit for UploadBatchCreateRequest."""

    def test_upload_batch_expected_items_lower_limit_rejected(self):
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadBatchCreateRequest(
                room_kind=UploadRoomKind.DIRECT,
                target_id=10,
                message_kind=UploadBatchMessageKind.ALBUM,
                expected_items=0,
                idempotency_key="synthetic-batch-lower-limit-001",
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("expected_items",))
        self.assertEqual(errors[0]["type"], "greater_than_equal")
        self.assertEqual(errors[0]["ctx"]["ge"], 1)
        self.assertEqual(errors[0]["input"], 0)


if __name__ == "__main__":
    unittest.main()
