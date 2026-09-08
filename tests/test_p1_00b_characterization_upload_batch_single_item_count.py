"""Characterization test for UploadBatchCreateRequest single-item batch shape boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadBatchCreateRequest
from models.upload_session import UploadBatchMessageKind, UploadRoomKind


class TestUploadBatchSingleItemCountCharacterization(unittest.TestCase):
    """Verify UploadBatchCreateRequest rejects single-item batches with expected_items != 1."""

    def test_single_item_batch_with_two_expected_items_raises_validation_error(self):
        with self.assertRaisesRegex(
            pydantic.ValidationError,
            "Single-item batches must expect exactly one item",
        ):
            UploadBatchCreateRequest(
                room_kind=UploadRoomKind.DIRECT,
                target_id=10,
                message_kind=UploadBatchMessageKind.SINGLE,
                expected_items=2,
                idempotency_key="synthetic-idempotency-key-001",
            )


if __name__ == "__main__":
    unittest.main()
