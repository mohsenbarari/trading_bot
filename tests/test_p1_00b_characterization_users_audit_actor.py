"""Characterization unit freezing the isolated behavior of api.routers.users._audit_actor.

This test module characterizes only _audit_actor and does not complete P1-00b
or establish Offer/Request-origin interaction evidence.
"""

from __future__ import annotations

import types
import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import _audit_actor
from core.enums import UserRole


class UsersAuditActorCharacterizationTests(unittest.TestCase):
    """Characterization tests freezing current behavior of _audit_actor.

    This unit characterizes only _audit_actor and does not complete P1-00b
    nor establish Offer/Request-origin interaction evidence.
    """

    def test_audit_actor_with_super_admin_enum_role(self):
        """Actor with id and UserRole.SUPER_ADMIN produces expected dictionary."""
        actor = types.SimpleNamespace(id=17, role=UserRole.SUPER_ADMIN)
        result = _audit_actor(actor)
        self.assertEqual(result, {"actor_id": 17, "actor_role": "مدیر ارشد"})

    def test_audit_actor_with_raw_string_role_preserves_string(self):
        """Actor with id and raw string role preserves that raw string."""
        actor = types.SimpleNamespace(id=42, role="support_agent")
        result = _audit_actor(actor)
        self.assertEqual(result, {"actor_id": 42, "actor_role": "support_agent"})

    def test_audit_actor_with_nested_value_object_role(self):
        """Actor with nested value-object role preserves id and unwraps value attribute."""
        custom_role_obj = types.SimpleNamespace(value="custom-role")
        actor = types.SimpleNamespace(id=99, role=custom_role_obj)
        result = _audit_actor(actor)
        self.assertEqual(result, {"actor_id": 99, "actor_role": "custom-role"})

    def test_audit_actor_normalization_loss_none_input(self):
        """None input yields actor_id and actor_role as None."""
        result = _audit_actor(None)
        self.assertEqual(result, {"actor_id": None, "actor_role": None})

    def test_audit_actor_normalization_loss_missing_role_attribute(self):
        """Actor missing role attribute yields actor_id and actor_role as None."""
        actor = types.SimpleNamespace(id=10)
        result = _audit_actor(actor)
        self.assertEqual(result, {"actor_id": None, "actor_role": None})

    def test_audit_actor_normalization_loss_none_role(self):
        """Actor whose role is None yields actor_id and actor_role as None."""
        actor = types.SimpleNamespace(id=20, role=None)
        result = _audit_actor(actor)
        self.assertEqual(result, {"actor_id": None, "actor_role": None})

    def test_audit_actor_with_empty_string_role_retains_actor(self):
        """Falsey non-None empty string role retains actor, preserves id, and emits empty string."""
        actor = types.SimpleNamespace(id=123, role="")
        result = _audit_actor(actor)
        self.assertEqual(result, {"actor_id": 123, "actor_role": ""})


if __name__ == "__main__":
    unittest.main()
