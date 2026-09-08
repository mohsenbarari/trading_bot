"""Characterization test for GroupMemberMutationResponse with explicit mutation fields."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import GroupMemberMutationResponse


class TestGroupMemberMutationResponseExplicitFlags(unittest.TestCase):
    """Verify GroupMemberMutationResponse construction with explicit mutation flags."""

    def test_group_member_mutation_response_explicit_flags_succeed(self):
        response = GroupMemberMutationResponse(
            chat_id=10,
            user_id=20,
            role="admin",
            removed=True,
            left=True,
            member_count=2,
            unchanged=True,
        )
        self.assertEqual(response.chat_id, 10)
        self.assertEqual(response.user_id, 20)
        self.assertIsInstance(response.role, str)
        self.assertEqual(response.role, "admin")
        self.assertEqual(response.member_count, 2)
        self.assertEqual(response.removed, True)
        self.assertIs(response.removed, True)
        self.assertEqual(response.left, True)
        self.assertIs(response.left, True)
        self.assertEqual(response.unchanged, True)
        self.assertIs(response.unchanged, True)


if __name__ == "__main__":
    unittest.main()
