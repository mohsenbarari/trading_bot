"""Characterization test for UploadBatchCreateRequest unsupported caption_policy boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadBatchCreateRequest
from models.upload_session import (
    UploadBatchMessageKind,
    UploadRoomKind,
)


class TestUploadBatchCaptionPolicyUnsupportedCharacterization(unittest.TestCase):
    """Characterize rejection of unsupported caption_policy for UploadBatchCreateRequest."""

    def test_upload_batch_caption_policy_unsupported_rejected(self):
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadBatchCreateRequest(
                room_kind=UploadRoomKind.DIRECT,
                target_id=10,
                message_kind=UploadBatchMessageKind.SINGLE,
                expected_items=1,
                idempotency_key="synthetic-caption-policy-001",
                caption_policy="all_items",
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("caption_policy",))
        self.assertEqual(errors[0]["type"], "enum")
        self.assertEqual(errors[0]["input"], "all_items")


if __name__ == "__main__":
    unittest.main()
