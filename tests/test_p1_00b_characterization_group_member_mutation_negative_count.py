"""Characterization test for GroupMemberMutationResponse negative member_count preservation."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import GroupMemberMutationResponse


class TestGroupMemberMutationNegativeCount(unittest.TestCase):
    """Verify GroupMemberMutationResponse accepts and preserves a negative member_count."""

    def test_group_member_mutation_response_negative_count_preserved(self):
        response = GroupMemberMutationResponse(
            chat_id=10,
            user_id=20,
            member_count=-1,
        )
        self.assertEqual(response.member_count, -1)
        self.assertIsInstance(response.member_count, int)
        self.assertEqual(response.chat_id, 10)
        self.assertEqual(response.user_id, 20)


if __name__ == "__main__":
    unittest.main()
