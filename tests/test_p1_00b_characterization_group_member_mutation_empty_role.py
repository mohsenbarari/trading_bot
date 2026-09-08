"""Characterization test for GroupMemberMutationResponse empty-string role preservation."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import GroupMemberMutationResponse


class TestGroupMemberMutationEmptyRole(unittest.TestCase):
    """Verify GroupMemberMutationResponse preserves an explicitly supplied empty-string role."""

    def test_group_member_mutation_response_empty_role_preserved(self):
        response = GroupMemberMutationResponse(
            chat_id=10,
            user_id=20,
            role="",
            member_count=3,
        )
        self.assertEqual(response.role, "")
        self.assertIsInstance(response.role, str)


if __name__ == "__main__":
    unittest.main()
