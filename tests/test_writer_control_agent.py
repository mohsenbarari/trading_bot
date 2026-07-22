import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts import run_writer_control_agent as agent


class WriterControlAgentTests(unittest.IsolatedAsyncioTestCase):
    def _settings(self, **overrides):
        values = {
            "trading_bot_service": "writer_control_agent",
            "writer_witness_required": True,
            "writer_witness_auto_renew_enabled": True,
            "dr_control_database_url": "postgresql+asyncpg://control@db/app",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_configuration_requires_dedicated_service_identity(self):
        with patch.object(
            agent, "settings", self._settings(trading_bot_service="api")
        ), self.assertRaises(agent.WriterControlAgentConfigurationError):
            agent.validate_writer_control_agent_configuration()

    def test_configuration_rejects_missing_control_database(self):
        identity = SimpleNamespace(is_webapp_site=True)
        with patch.object(
            agent, "settings", self._settings(dr_control_database_url="")
        ), patch.object(
            agent, "resolve_runtime_identity", return_value=identity
        ), self.assertRaises(agent.WriterControlAgentConfigurationError):
            agent.validate_writer_control_agent_configuration()

    def test_configuration_accepts_isolated_webapp_control_surface(self):
        identity = SimpleNamespace(is_webapp_site=True)
        with patch.object(agent, "settings", self._settings()), patch.object(
            agent, "resolve_runtime_identity", return_value=identity
        ), patch.object(
            agent, "writer_witness_client_configuration_reasons", return_value=()
        ):
            agent.validate_writer_control_agent_configuration()

    async def test_main_verifies_database_role_before_renewal(self):
        calls = []

        async def verify():
            calls.append("verify")

        async def renew():
            calls.append("renew")

        with patch.object(
            agent, "validate_writer_control_agent_configuration"
        ) as validate, patch.object(
            agent, "verify_three_site_database_role_bindings", side_effect=verify
        ), patch.object(
            agent, "writer_witness_renewal_loop", side_effect=renew
        ):
            await agent.main()

        validate.assert_called_once_with()
        self.assertEqual(calls, ["verify", "renew"])


if __name__ == "__main__":
    unittest.main()
