"""Characterization test for GroupMemberMutationResponse default field values."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import GroupMemberMutationResponse


class TestGroupMemberMutationResponseDefaults(unittest.TestCase):
    """Verify GroupMemberMutationResponse minimal construction and default field values."""

    def test_group_member_mutation_response_defaults_succeed(self):
        response = GroupMemberMutationResponse(
            chat_id=10,
            user_id=20,
            member_count=3,
        )
        self.assertEqual(response.chat_id, 10)
        self.assertEqual(response.user_id, 20)
        self.assertEqual(response.member_count, 3)
        self.assertIsNone(response.role)
        self.assertEqual(response.role, None)
        self.assertEqual(response.removed, False)
        self.assertIs(response.removed, False)
        self.assertEqual(response.left, False)
        self.assertIs(response.left, False)
        self.assertEqual(response.unchanged, False)
        self.assertIs(response.unchanged, False)


if __name__ == "__main__":
    unittest.main()
