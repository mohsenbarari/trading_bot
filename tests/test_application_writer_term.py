from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from types import SimpleNamespace

from core.application_writer_term import (
    ApplicationWriterTermError,
    ApplicationWriterTermPolicy,
    policy_from_settings,
    require_active_writer_term,
)
from core.production_writer_lease import LEASE_SCHEMA
from scripts import production_writer_lease_agent as writer_lease_agent


NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


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
    """Use the test process owner; production defaults to root ownership."""

    return ApplicationWriterTermPolicy(
        enabled=True,
        local_site=local_site,
        lease_file=path,
        owner_uid=os.geteuid(),
    )


def agent_proof(
    *,
    holder_site: str,
    issued_at: datetime,
    expires_at: datetime,
) -> dict[str, object]:
    """Minimal post-Witness proof shape consumed by the lease agent writer."""

    return {
        "version": 1,
        "authority": "webapp",
        "holder_site": holder_site,
        "writer_epoch": 9,
        "lease_id": "lease-9",
        "issued_at": issued_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "witness_transition_id": "transition-9",
        "signature": "dGVzdC1zaWduYXR1cmU=",
    }


def ir_agent_compatible_policy(path: Path) -> ApplicationWriterTermPolicy:
    return ApplicationWriterTermPolicy(
        enabled=True,
        local_site="webapp_ir",
        lease_file=path,
        owner_uid=0,
        safety_margin_seconds=15,
        max_lease_duration_seconds=60,
    )


class ApplicationWriterTermTests(unittest.TestCase):
    def test_default_policy_is_disabled(self) -> None:
        self.assertIsNone(require_active_writer_term(ApplicationWriterTermPolicy(), now=NOW))

    def test_policy_defaults_to_root_owned_lease(self) -> None:
        self.assertEqual(ApplicationWriterTermPolicy().owner_uid, 0)

    def test_explicit_disabled_policy_permits_without_opening_a_lease(self) -> None:
        result = require_active_writer_term(
            ApplicationWriterTermPolicy(
                enabled=False,
                local_site="not-a-webapp-site",
                lease_file=Path("relative-and-never-opened.json"),
            ),
            now=NOW,
        )

        self.assertIsNone(result)

    def test_disabled_settings_projection_does_not_read_term_path_settings(self) -> None:
        class DisabledSettings:
            application_writer_term_enforced = False

            def __getattr__(self, name: str) -> object:
                raise AssertionError(f"disabled projection unexpectedly read {name}")

        policy = policy_from_settings(DisabledSettings())

        self.assertFalse(policy.enabled)
        self.assertIsNone(policy.lease_file)

    def test_enabled_settings_projection_forces_root_owner(self) -> None:
        policy = policy_from_settings(
            SimpleNamespace(
                application_writer_term_enforced=True,
                application_writer_term_local_site="webapp_fi",
                application_writer_term_lease_file="/run/trading-bot/writer-term.json",
                application_writer_term_safety_margin_seconds=5,
                application_writer_term_max_lease_duration_seconds=90,
                application_writer_term_owner_uid=1234,
            )
        )

        self.assertTrue(policy.enabled)
        self.assertEqual(policy.owner_uid, 0)
        self.assertEqual(policy.lease_file, Path("/run/trading-bot/writer-term.json"))

    def test_enabled_matching_lease_returns_only_validated_term_details(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer-lease.json"
            write_lease(path, lease_payload())

            term = require_active_writer_term(
                enabled_policy(path),
                now=NOW,
            )

        self.assertIsNotNone(term)
        assert term is not None
        self.assertEqual(term.holder_site, "webapp_fi")
        self.assertEqual(term.writer_epoch, 7)
        self.assertEqual(term.lease_id, "lease-7")
        self.assertEqual(term.witness_transition_id, "transition-7")
        self.assertFalse(hasattr(term, "proof_sha256"))

    def test_enabled_policy_requires_a_lease_file(self) -> None:
        with self.assertRaisesRegex(ApplicationWriterTermError, "lease file is required"):
            require_active_writer_term(
                ApplicationWriterTermPolicy(enabled=True, local_site="webapp_fi"),
                now=NOW,
            )

    def test_enabled_policy_rejects_a_relative_lease_path(self) -> None:
        with self.assertRaisesRegex(ApplicationWriterTermError, "absolute path"):
            require_active_writer_term(
                ApplicationWriterTermPolicy(
                    enabled=True,
                    local_site="webapp_fi",
                    lease_file=Path("writer-lease.json"),
                ),
                now=NOW,
            )

    def test_enabled_policy_fails_closed_when_the_lease_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing-writer-lease.json"

            with self.assertRaisesRegex(ApplicationWriterTermError, "unavailable or unsafe"):
                require_active_writer_term(
                    enabled_policy(path),
                    now=NOW,
                )

    def test_enabled_policy_fails_closed_for_unsafe_lease_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer-lease.json"
            write_lease(path, lease_payload())
            os.chmod(path, 0o644)

            with self.assertRaisesRegex(ApplicationWriterTermError, "unavailable or unsafe"):
                require_active_writer_term(
                    enabled_policy(path),
                    now=NOW,
                )

    def test_enabled_policy_fails_closed_for_a_symlinked_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "writer-lease.json"
            path = Path(directory) / "writer-lease-link.json"
            write_lease(target, lease_payload())
            path.symlink_to(target)

            with self.assertRaisesRegex(ApplicationWriterTermError, "unavailable or unsafe"):
                require_active_writer_term(
                    enabled_policy(path),
                    now=NOW,
                )

    def test_enabled_policy_fails_closed_for_a_symlinked_lease_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target_directory = Path(directory) / "lease-directory"
            target_directory.mkdir(mode=0o700)
            path = target_directory / "writer-lease.json"
            linked_directory = Path(directory) / "linked-directory"
            write_lease(path, lease_payload())
            linked_directory.symlink_to(target_directory, target_is_directory=True)

            with self.assertRaisesRegex(ApplicationWriterTermError, "ancestors are unsafe"):
                require_active_writer_term(
                    enabled_policy(linked_directory / "writer-lease.json"),
                    now=NOW,
                )

    def test_enabled_policy_fails_closed_for_an_uncontrolled_writable_lease_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lease_directory = Path(directory) / "unsafe-lease-directory"
            lease_directory.mkdir(mode=0o700)
            path = lease_directory / "writer-lease.json"
            write_lease(path, lease_payload())
            os.chmod(lease_directory, 0o777)

            with self.assertRaisesRegex(ApplicationWriterTermError, "writable by an unsafe principal"):
                require_active_writer_term(enabled_policy(path), now=NOW)

    def test_enabled_policy_fails_closed_for_malformed_lease_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer-lease.json"
            path.write_text("{not-json", encoding="utf-8")
            os.chmod(path, 0o600)

            with self.assertRaisesRegex(ApplicationWriterTermError, "unavailable or unsafe"):
                require_active_writer_term(
                    enabled_policy(path),
                    now=NOW,
                )

    def test_enabled_policy_fails_closed_for_an_expired_lease_with_injected_clock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer-lease.json"
            write_lease(path, lease_payload(expires_at=NOW))

            with self.assertRaisesRegex(ApplicationWriterTermError, "expired"):
                require_active_writer_term(
                    enabled_policy(path),
                    now=NOW,
                )

    def test_enabled_policy_rejects_expiry_at_the_safety_margin_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer-lease.json"
            write_lease(path, lease_payload(expires_at=NOW + timedelta(seconds=5)))

            with self.assertRaisesRegex(ApplicationWriterTermError, "safety margin"):
                require_active_writer_term(enabled_policy(path), now=NOW)

    def test_enabled_policy_rejects_a_nonpositive_safety_margin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer-lease.json"
            write_lease(path, lease_payload())

            with self.assertRaisesRegex(ApplicationWriterTermError, "safety margin"):
                require_active_writer_term(
                    replace(enabled_policy(path), safety_margin_seconds=0),
                    now=NOW,
                )

    def test_enabled_policy_accepts_expiry_just_past_the_safety_margin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer-lease.json"
            write_lease(path, lease_payload(expires_at=NOW + timedelta(seconds=6)))

            self.assertIsNotNone(require_active_writer_term(enabled_policy(path), now=NOW))

    def test_enabled_policy_accepts_duration_at_the_maximum_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer-lease.json"
            write_lease(
                path,
                lease_payload(
                    issued_at=NOW - timedelta(seconds=30),
                    expires_at=NOW + timedelta(seconds=60),
                ),
            )

            self.assertIsNotNone(require_active_writer_term(enabled_policy(path), now=NOW))

    def test_enabled_policy_rejects_duration_above_the_maximum_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer-lease.json"
            write_lease(
                path,
                lease_payload(
                    issued_at=NOW - timedelta(seconds=30),
                    expires_at=NOW + timedelta(seconds=61),
                ),
            )

            with self.assertRaisesRegex(ApplicationWriterTermError, "duration exceeds"):
                require_active_writer_term(enabled_policy(path), now=NOW)

    def test_enabled_policy_rejects_a_nonpositive_maximum_duration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer-lease.json"
            write_lease(path, lease_payload())

            with self.assertRaisesRegex(ApplicationWriterTermError, "maximum duration"):
                require_active_writer_term(
                    replace(enabled_policy(path), max_lease_duration_seconds=0),
                    now=NOW,
                )

    def test_enabled_policy_fails_closed_for_a_future_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer-lease.json"
            write_lease(
                path,
                lease_payload(
                    issued_at=NOW + timedelta(seconds=1),
                    expires_at=NOW + timedelta(seconds=31),
                ),
            )

            with self.assertRaisesRegex(ApplicationWriterTermError, "not active yet"):
                require_active_writer_term(
                    enabled_policy(path),
                    now=NOW,
                )

    def test_enabled_policy_fails_closed_for_the_other_webapp_holder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer-lease.json"
            write_lease(path, lease_payload(holder_site="webapp_ir"))

            with self.assertRaisesRegex(ApplicationWriterTermError, "does not match"):
                require_active_writer_term(
                    enabled_policy(path),
                    now=NOW,
                )

    def test_enabled_policy_requires_an_exact_webapp_site_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer-lease.json"
            write_lease(path, lease_payload())

            with self.assertRaisesRegex(ApplicationWriterTermError, "local site is invalid"):
                require_active_writer_term(
                    enabled_policy(path, local_site="webapp-fi"),
                    now=NOW,
                )

    def test_enabled_policy_rejects_a_naive_injected_clock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer-lease.json"
            write_lease(path, lease_payload())

            with self.assertRaisesRegex(ApplicationWriterTermError, "timezone-aware"):
                require_active_writer_term(
                    enabled_policy(path),
                    now=NOW.replace(tzinfo=None),
                )


class ApplicationWriterTermAgentCompatibilityTests(unittest.TestCase):
    @unittest.skipUnless(os.geteuid() == 0, "requires the root-owned lease-agent contract")
    def test_root_agent_lease_matches_ir_writer_term_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer-lease.json"
            writer_lease_agent._write_lease(
                path,
                proof=agent_proof(
                    holder_site="webapp_ir",
                    issued_at=NOW - timedelta(seconds=30),
                    expires_at=NOW + timedelta(seconds=30),
                ),
            )

            metadata = path.stat()
            self.assertEqual(metadata.st_uid, 0)
            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
            term = require_active_writer_term(ir_agent_compatible_policy(path), now=NOW)
            self.assertIsNotNone(term)
            assert term is not None
            self.assertEqual(term.holder_site, "webapp_ir")
            self.assertEqual(term.writer_epoch, 9)
            self.assertEqual(term.lease_id, "lease-9")

            writer_lease_agent._write_lease(
                path,
                proof=agent_proof(
                    holder_site="webapp_fi",
                    issued_at=NOW - timedelta(seconds=30),
                    expires_at=NOW + timedelta(seconds=30),
                ),
            )
            with self.assertRaisesRegex(ApplicationWriterTermError, "does not match"):
                require_active_writer_term(ir_agent_compatible_policy(path), now=NOW)

            writer_lease_agent._write_lease(
                path,
                proof=agent_proof(
                    holder_site="webapp_ir",
                    issued_at=NOW - timedelta(seconds=45),
                    expires_at=NOW + timedelta(seconds=15),
                ),
            )
            with self.assertRaisesRegex(ApplicationWriterTermError, "safety margin"):
                require_active_writer_term(ir_agent_compatible_policy(path), now=NOW)


if __name__ == "__main__":
    unittest.main()
