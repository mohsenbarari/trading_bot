"""Characterization unit freezing the isolated behavior of api.routers.users._is_admin_role.

This test module characterizes only _is_admin_role and does not complete P1-00b
or establish Offer/Request-origin interaction evidence.
"""

from __future__ import annotations

import types
import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import ADMIN_ROLE_VALUES, _is_admin_role
from core.enums import UserRole


class UsersIsAdminRoleCharacterizationTests(unittest.TestCase):
    """Characterization tests freezing current behavior of _is_admin_role."""

    def test_admin_role_values_authoritative_constant(self):
        """Authoritative constant ADMIN_ROLE_VALUES contains exactly SUPER_ADMIN and MIDDLE_MANAGER values."""
        self.assertEqual(
            ADMIN_ROLE_VALUES,
            {UserRole.SUPER_ADMIN.value, UserRole.MIDDLE_MANAGER.value},
        )

    def test_is_admin_role_returns_true_for_super_admin(self):
        """Assert True for UserRole.SUPER_ADMIN enum member and raw Persian value string."""
        self.assertTrue(_is_admin_role(UserRole.SUPER_ADMIN))
        self.assertTrue(_is_admin_role(UserRole.SUPER_ADMIN.value))
        self.assertTrue(_is_admin_role("مدیر ارشد"))

    def test_is_admin_role_returns_true_for_middle_manager(self):
        """Assert True for UserRole.MIDDLE_MANAGER enum member and raw Persian value string."""
        self.assertTrue(_is_admin_role(UserRole.MIDDLE_MANAGER))
        self.assertTrue(_is_admin_role(UserRole.MIDDLE_MANAGER.value))
        self.assertTrue(_is_admin_role("مدیر میانی"))

    def test_is_admin_role_returns_false_for_watch(self):
        """Assert False for UserRole.WATCH non-admin member and raw Persian value string."""
        self.assertFalse(_is_admin_role(UserRole.WATCH))
        self.assertFalse(_is_admin_role(UserRole.WATCH.value))
        self.assertFalse(_is_admin_role("تماشا"))

    def test_is_admin_role_returns_false_for_standard(self):
        """Assert False for UserRole.STANDARD non-admin member and raw Persian value string."""
        self.assertFalse(_is_admin_role(UserRole.STANDARD))
        self.assertFalse(_is_admin_role(UserRole.STANDARD.value))
        self.assertFalse(_is_admin_role("عادی"))

    def test_is_admin_role_returns_false_for_police(self):
        """Assert False for UserRole.POLICE non-admin member and raw Persian value string."""
        self.assertFalse(_is_admin_role(UserRole.POLICE))
        self.assertFalse(_is_admin_role(UserRole.POLICE.value))
        self.assertFalse(_is_admin_role("پلیس"))

    def test_is_admin_role_returns_false_for_none(self):
        """None input returns False."""
        self.assertFalse(_is_admin_role(None))

    def test_is_admin_role_returns_false_for_unmatched_raw_string(self):
        """Arbitrary unmatched raw string returns False."""
        self.assertFalse(_is_admin_role("unmatched_string"))

    def test_is_admin_role_returns_false_for_custom_object_with_unmatched_value(self):
        """Custom object whose value attribute equals an unmatched string returns False."""
        obj = types.SimpleNamespace(value="unmatched_string")
        self.assertFalse(_is_admin_role(obj))


if __name__ == "__main__":
    unittest.main()
