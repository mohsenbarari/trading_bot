"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001 without changing product behavior, blockers, evidence promotion, executed-seed registry, or Stage completion."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import GroupCreateRequest


class TestGroupCreateRequestDescriptionWhitespace(unittest.TestCase):
    """Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001 without changing product behavior, blockers, evidence promotion, executed-seed registry, or Stage completion."""

    def test_group_create_request_whitespace_description_normalized_to_none(self):
        request = GroupCreateRequest(title="Valid Title", description="   ")
        self.assertIsNone(request.description)


if __name__ == "__main__":
    unittest.main()
