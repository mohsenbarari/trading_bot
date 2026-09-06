"""Characterization unit freezing the isolated behavior of api.routers.users._normalize_actor.

This test module characterizes only _normalize_actor and does not complete P1-00b
or establish Offer/Request-origin interaction evidence.
"""

from __future__ import annotations

import types
import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import _normalize_actor


class UsersNormalizeActorCharacterizationTests(unittest.TestCase):
    """Characterization tests freezing current behavior of _normalize_actor."""

    def test_normalize_actor_returns_actor_with_non_none_role_by_identity(self):
        """Actor with non-None role is returned by identity, not merely equality."""
        actor = types.SimpleNamespace(role="custom_role")
        result = _normalize_actor(actor)
        self.assertIs(result, actor)

    def test_normalize_actor_returns_actor_with_empty_string_role_by_identity(self):
        """Falsey non-None empty string role returns actor by identity."""
        actor = types.SimpleNamespace(role="")
        result = _normalize_actor(actor)
        self.assertIs(result, actor)

    def test_normalize_actor_returns_actor_with_integer_zero_role_by_identity(self):
        """Falsey non-None integer zero role returns actor by identity."""
        actor = types.SimpleNamespace(role=0)
        result = _normalize_actor(actor)
        self.assertIs(result, actor)

    def test_normalize_actor_returns_actor_with_false_role_by_identity(self):
        """Falsey non-None False role returns actor by identity."""
        actor = types.SimpleNamespace(role=False)
        result = _normalize_actor(actor)
        self.assertIs(result, actor)

    def test_normalize_actor_returns_none_when_actor_has_no_role_attribute(self):
        """Object with no role attribute produces None."""
        actor = types.SimpleNamespace()
        result = _normalize_actor(actor)
        self.assertIsNone(result)

    def test_normalize_actor_returns_none_when_actor_role_is_none(self):
        """Object whose role is None produces None."""
        actor = types.SimpleNamespace(role=None)
        result = _normalize_actor(actor)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
