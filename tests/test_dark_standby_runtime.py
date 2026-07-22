from __future__ import annotations

import unittest

from core.dark_standby import DarkStandbyRuntimeError, assert_not_dark_standby


class DarkStandbyRuntimeTests(unittest.TestCase):
    def test_every_application_capable_entrypoint_is_rejected(self) -> None:
        for service in (
            "api",
            "app",
            "bot",
            "migration",
            "schema_init",
            "sync_worker",
            "redis_restore",
            "nginx",
            "background_worker",
        ):
            with self.subTest(service=service):
                with self.assertRaisesRegex(DarkStandbyRuntimeError, "DB-only"):
                    assert_not_dark_standby(service, environ={"DARK_STANDBY_MODE": "1"})

    def test_normal_runtime_is_unchanged(self) -> None:
        assert_not_dark_standby("api", environ={"DARK_STANDBY_MODE": "0"})

    def test_unknown_service_cannot_create_a_bypass(self) -> None:
        with self.assertRaisesRegex(DarkStandbyRuntimeError, "unknown"):
            assert_not_dark_standby("custom-writer", environ={"DARK_STANDBY_MODE": "0"})
