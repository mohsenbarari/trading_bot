"""Characterization unit freezing the isolated behavior of api.routers.users._role_value.

This test module characterizes only _role_value and does not complete P1-00b
or establish Offer/Request-origin interaction evidence.
"""

from __future__ import annotations

import types
import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import _role_value
from core.enums import UserRole


class UsersRoleValueCharacterizationTests(unittest.TestCase):
    """Characterization tests freezing current behavior of _role_value."""

    def test_role_value_returns_none_for_none_input(self):
        """None input returns None directly."""
        self.assertIsNone(_role_value(None))

    def test_role_value_returns_persian_string_for_user_role_watch(self):
        """UserRole.WATCH enum member returns exact Persian string 'تماشا'."""
        result = _role_value(UserRole.WATCH)
        self.assertEqual(result, "تماشا")
        self.assertEqual(result, UserRole.WATCH.value)

    def test_role_value_returns_value_attribute_for_custom_object(self):
        """Object exposing a value attribute returns that attribute value."""
        obj = types.SimpleNamespace(value="custom-value")
        self.assertEqual(_role_value(obj), "custom-value")

    def test_role_value_returns_none_when_object_value_is_none(self):
        """Object exposing a value attribute of None returns None."""
        obj = types.SimpleNamespace(value=None)
        self.assertIsNone(_role_value(obj))

    def test_role_value_returns_plain_string_by_identity(self):
        """Plain string without a value attribute is returned by identity."""
        raw_role = "plain-string"
        result = _role_value(raw_role)
        self.assertIs(result, raw_role)
        self.assertEqual(result, "plain-string")

    def test_role_value_returns_integer_zero_by_identity(self):
        """Integer zero without a value attribute is returned by identity."""
        zero_role = 0
        result = _role_value(zero_role)
        self.assertIs(result, zero_role)
        self.assertEqual(result, 0)

    def test_role_value_returns_arbitrary_object_without_value_by_identity(self):
        """Arbitrary object without a value attribute is returned unchanged."""
        obj = types.SimpleNamespace(role_name="custom")
        result = _role_value(obj)
        self.assertIs(result, obj)


if __name__ == "__main__":
    unittest.main()
