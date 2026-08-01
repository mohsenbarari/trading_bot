from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from core.application_writer_term import (
    ApplicationWriterTermError,
    ApplicationWriterTermPolicy,
    policy_from_settings,
    require_active_writer_term,
    validate_application_writer_term_runtime,
)
from core.production_writer_lease import LEASE_SCHEMA


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def lease_payload(
    *,
    holder_site: str = "webapp_fi",
    issued_at: datetime = NOW - timedelta(seconds=30),
    expires_at: datetime = NOW + timedelta(seconds=30),
) -> dict[str, object]:
    return {
        "schema": LEASE_SCHEMA,
        "holder_site": holder_site,
        "writer_epoch": 7,
        "lease_id": "lease-7",
        "issued_at": issued_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "witness_transition_id": "transition-7",
        "proof_sha256": "a" * 64,
    }


def write_lease(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, 0o600)


def enabled_policy(path: Path, *, local_site: str = "webapp_fi") -> ApplicationWriterTermPolicy:
    return ApplicationWriterTermPolicy(
        enabled=True,
        local_site=local_site,
        lease_file=path,
        owner_uid=os.geteuid(),
    )


def valid_runtime_settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "application_writer_term_enforced": True,
        "application_writer_term_local_site": "webapp_fi",
        "application_writer_term_lease_file": "/run/trading-bot-writer-term/writer-lease.json",
        "application_writer_term_safety_margin_seconds": 15,
        "application_writer_term_max_lease_duration_seconds": 60,
        "single_writer_runtime_enabled": True,
        "database_schema_bootstrap_enabled": False,
        "server_mode": "foreign",
        "trading_bot_service": "api",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ApplicationWriterTermTests(unittest.TestCase):
    def test_disabled_policy_does_not_open_or_validate_a_path(self) -> None:
        result = require_active_writer_term(
            ApplicationWriterTermPolicy(
                enabled=False,
                local_site="not-a-site",
                lease_file=Path("relative-never-opened.json"),
            ),
            now=NOW,
        )
        self.assertIsNone(result)

    def test_disabled_settings_projection_does_not_read_optional_term_fields(self) -> None:
        class DisabledSettings:
            application_writer_term_enforced = False

            def __getattr__(self, name: str) -> object:
                raise AssertionError(f"unexpected settings read: {name}")

        self.assertFalse(policy_from_settings(DisabledSettings()).enabled)

    def test_valid_matching_lease_returns_non_secret_term_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer-lease.json"
            write_lease(path, lease_payload())
            term = require_active_writer_term(enabled_policy(path), now=NOW)

        self.assertIsNotNone(term)
        assert term is not None
        self.assertEqual(term.holder_site, "webapp_fi")
        self.assertEqual(term.writer_epoch, 7)
        self.assertEqual(term.lease_id, "lease-7")
        self.assertFalse(hasattr(term, "proof_sha256"))

    def test_invalid_or_wrong_holder_lease_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer-lease.json"
            write_lease(path, lease_payload(holder_site="webapp_ir"))
            with self.assertRaisesRegex(ApplicationWriterTermError, "does not match"):
                require_active_writer_term(enabled_policy(path), now=NOW)

            write_lease(path, lease_payload(expires_at=NOW + timedelta(seconds=5)))
            with self.assertRaisesRegex(ApplicationWriterTermError, "safety margin"):
                require_active_writer_term(enabled_policy(path), now=NOW)

            os.chmod(path, 0o644)
            with self.assertRaisesRegex(ApplicationWriterTermError, "unavailable or unsafe"):
                require_active_writer_term(enabled_policy(path), now=NOW)

    def test_symlinked_parent_and_too_long_lease_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real_parent = root / "real"
            real_parent.mkdir(mode=0o700)
            path = real_parent / "writer-lease.json"
            write_lease(path, lease_payload())
            linked_parent = root / "linked"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            with self.assertRaisesRegex(ApplicationWriterTermError, "ancestors are unsafe"):
                require_active_writer_term(enabled_policy(linked_parent / path.name), now=NOW)

            write_lease(path, lease_payload(expires_at=NOW + timedelta(seconds=61)))
            with self.assertRaisesRegex(ApplicationWriterTermError, "duration exceeds"):
                require_active_writer_term(
                    replace(enabled_policy(path), max_lease_duration_seconds=60),
                    now=NOW,
                )

    def test_static_runtime_validation_requires_single_writer_schema_safe_site_and_service(self) -> None:
        settings = valid_runtime_settings()
        policy = validate_application_writer_term_runtime(settings, expected_service="api")
        self.assertTrue(policy.enabled)
        iran_policy = validate_application_writer_term_runtime(
            valid_runtime_settings(
                application_writer_term_local_site="webapp_ir",
                server_mode="iran",
            ),
            expected_service="api",
        )
        self.assertEqual(iran_policy.local_site, "webapp_ir")

        for overridden, message in (
            ({"single_writer_runtime_enabled": False}, "SINGLE_WRITER_RUNTIME_ENABLED"),
            ({"database_schema_bootstrap_enabled": True}, "DATABASE_SCHEMA_BOOTSTRAP_ENABLED"),
            ({"server_mode": "iran"}, "does not match SERVER_MODE"),
            ({"trading_bot_service": "bot"}, "TRADING_BOT_SERVICE"),
        ):
            with self.subTest(overridden=overridden), self.assertRaisesRegex(
                ApplicationWriterTermError, message
            ):
                validate_application_writer_term_runtime(
                    valid_runtime_settings(**overridden),
                    expected_service="api",
                )

    def test_invalid_enabled_settings_are_not_silently_treated_as_disabled(self) -> None:
        settings = valid_runtime_settings(application_writer_term_enforced="yes")
        with self.assertRaisesRegex(ApplicationWriterTermError, "enforcement setting"):
            policy_from_settings(settings)


if __name__ == "__main__":
    unittest.main()
