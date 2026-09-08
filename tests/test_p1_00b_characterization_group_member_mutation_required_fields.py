"""Characterization test for GroupMemberMutationResponse required field omissions."""

import unittest

from pydantic import ValidationError

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import GroupMemberMutationResponse


class TestGroupMemberMutationRequiredFields(unittest.TestCase):
    """Verify GroupMemberMutationResponse validation errors when omitting required fields."""

    def test_omitting_required_field_raises_validation_error(self):
        valid_mapping = {
            "chat_id": 10,
            "user_id": 20,
            "member_count": 3,
        }
        for field in ("chat_id", "user_id", "member_count"):
            with self.subTest(field=field):
                field_values = dict(valid_mapping)
                del field_values[field]
                with self.assertRaises(ValidationError) as ctx:
                    GroupMemberMutationResponse(**field_values)
                self.assertTrue(
                    any(
                        err.get("loc") == (field,) and err.get("type") == "missing"
                        for err in ctx.exception.errors()
                    )
                )


if __name__ == "__main__":
    unittest.main()
