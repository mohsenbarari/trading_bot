from __future__ import annotations

from hashlib import sha256
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

from apps.coin_rate_estimator.telegram_price_collector.config import (
    DEFAULT_CHANNELS,
    source_code_for_channel,
)
from core.market_intelligence.private_capture import ACCOUNT_SOURCES
from scripts import quiesce_production_legacy_market_collectors as handoff


RELEASE = "a" * 40


class LegacyMarketCollectorHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="collector-handoff-")
        self.root = Path(self.temporary.name)
        self.approved = self.root / "secure"
        self.systemd = self.root / "systemd"
        self.systemd.mkdir()
        self.host_role = "bot"
        for unit in handoff.ROLE_UNITS[self.host_role]:
            path = self.systemd / unit
            path.write_text(f"[Unit]\nDescription={unit}\n", encoding="utf-8")
            path.chmod(0o644)
        self.journal = self.approved / "handoff.json"
        self.operation_lock = self.root / "queue" / "production-release.lock"
        self.active = {
            unit: True for unit in handoff.ROLE_UNITS[self.host_role]
        }
        self.enabled = {
            unit: True for unit in handoff.ROLE_UNITS[self.host_role]
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def command(self, arguments, *, allow=(0,)):
        action = arguments[1]
        unit = arguments[-1]
        if action == "is-active":
            code = 0 if self.active[unit] else 3
        elif action == "is-enabled":
            code = 0 if self.enabled[unit] else 1
        elif action in {"stop", "start"}:
            self.active[unit] = action == "start"
            code = 0
        elif action in {"disable", "enable"}:
            self.enabled[unit] = action == "enable"
            code = 0
        else:
            raise AssertionError(arguments)
        self.assertIn(code, allow)
        return type("Result", (), {"returncode": code, "stdout": "", "stderr": ""})()

    def context(self):
        return (
            patch.object(handoff, "APPROVED_ROOT", self.approved),
            patch.object(handoff, "SYSTEMD_ROOT", self.systemd),
            patch.object(handoff, "OPERATION_LOCK_PATH", self.operation_lock),
            patch.object(handoff, "_run", side_effect=self.command),
        )

    def common(self) -> dict[str, object]:
        return {
            "journal": self.journal,
            "release_sha": RELEASE,
            "host_role": self.host_role,
        }

    def select_role(self, host_role: str) -> None:
        self.host_role = host_role
        for unit in handoff.ROLE_UNITS[host_role]:
            path = self.systemd / unit
            if not path.exists():
                path.write_text(
                    f"[Unit]\nDescription={unit}\n", encoding="utf-8"
                )
                path.chmod(0o644)
        self.active = {
            unit: True for unit in handoff.ROLE_UNITS[host_role]
        }
        self.enabled = {
            unit: True for unit in handoff.ROLE_UNITS[host_role]
        }

    def proof(self, status: str) -> tuple[Path, str]:
        path = self.approved / f"{status.lower()}.json"
        payload = {"release_sha": RELEASE, "status": status}
        if status == "PASS":
            payload = {
                "schema": handoff.primary_verifier.RECEIPT_SCHEMA,
                "status": "PASS",
                "created_at_utc": datetime.now(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                ),
                "release_sha": RELEASE,
                "release_tree": "b" * 40,
                "image_ids": {
                    "bot": "sha256:" + "c" * 64,
                    "web": "sha256:" + "d" * 64,
                },
                "maximum_age_seconds": handoff.primary_verifier.MAXIMUM_AGE_SECONDS,
                "reason_code": None,
                "checks": list(handoff.primary_verifier.CHECKS),
                "stream_count": 9,
                "highest_sequence": 10,
                "snapshot": {
                    "contract": handoff.primary_verifier.WEB_VIEW_CONTRACT,
                    "lane": "PRIVATE_PRIMARY",
                    "status": "OK",
                    "snapshot_hash": "e" * 64,
                    "file_sha256": "f" * 64,
                },
                "capture_backfill": {
                    "not_before_utc": handoff.primary_verifier.AUTHORIZED_BACKFILL_NOT_BEFORE_UTC,
                    "source_codes": handoff.primary_verifier.AUTHORIZED_BACKFILL_SOURCE_CODES.split(","),
                    "max_messages": 250000,
                },
                "catchup_verification": {
                    "receipt_sha256": "1" * 64,
                    "age_seconds": 1,
                },
                "artifacts": {"catchup_receipt_sha256": "1" * 64},
                "read_only_runtime_verification": True,
                "product_or_runtime_mutated": False,
                "payload_values_included": False,
                "pii_included": False,
                "secrets_disclosed": False,
            }
        path.write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path, sha256(path.read_bytes()).hexdigest()

    def record_transferred_authority(self) -> None:
        payload = json.loads(self.journal.read_text(encoding="utf-8"))
        payload["status"] = "AUTHORITY_TRANSFERRED"
        payload["authority_transfer"] = {
            "bluegreen_journal_path_sha256": "1" * 64,
            "prepared_bluegreen_journal_sha256": "2" * 64,
            "authorization_bluegreen_journal_sha256": "3" * 64,
            "marker_authority_sha256": "4" * 64,
        }
        handoff._atomic(self.journal, payload)

    def test_owner_inventory_matches_the_real_capture_source_contract(self) -> None:
        public_sources = frozenset(
            source_code_for_channel(channel) for channel in DEFAULT_CHANNELS
        )
        self.assertEqual(
            handoff.UNIT_SOURCE_OWNERSHIP[
                "coin-public-market-telegram.service"
            ],
            public_sources,
        )
        self.assertEqual(
            handoff.UNIT_SOURCE_OWNERSHIP["coin-capture.service"],
            ACCOUNT_SOURCES["account2"],
        )
        self.assertEqual(
            handoff.UNIT_SOURCE_OWNERSHIP["market-channel-capture.service"],
            ACCOUNT_SOURCES["account1"],
        )
        self.assertEqual(
            handoff.UNIT_SOURCE_OWNERSHIP[
                "coin-group-event-telegram.service"
            ],
            ACCOUNT_SOURCES["account2"],
        )
        self.assertEqual(
            handoff.UNIT_SOURCE_OWNERSHIP[
                "trading-bot-private-gold-collector.service"
            ],
            frozenset({"MELTED_PRIMARY_FLOW"}),
        )
        self.assertNotIn("coin-rate-estimator-dashboard.service", handoff.UNITS)
        self.assertNotIn(
            "coin-intelligence-capture-shadow-input.timer", handoff.UNITS
        )

    def test_inventory_requires_only_units_owned_by_the_selected_host(self) -> None:
        # No web-role unit exists in the default bot fixture, yet bot
        # inventory is complete and must not fail on a remote-host unit.
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            self.assertEqual(
                set(handoff._inventory("bot")),
                set(handoff.ROLE_UNITS["bot"]),
            )
            missing = self.systemd / "coin-public-market-telegram.service"
            missing.unlink()
            with self.assertRaisesRegex(
                handoff.CollectorHandoffError, "unit_missing"
            ):
                handoff._inventory("bot")

    def test_verify_rejects_active_or_enabled_standalone_capture_owner(self) -> None:
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            handoff.quiesce(**self.common())
            for unit in ("coin-public-market-telegram.service",):
                for state in ("active", "enabled"):
                    with self.subTest(unit=unit, state=state):
                        target = self.active if state == "active" else self.enabled
                        target[unit] = True
                        with self.assertRaisesRegex(
                            handoff.CollectorHandoffError,
                            "overlap_not_quiesced",
                        ):
                            handoff.verify(
                                **self.common(),
                            )
                        target[unit] = False

    def test_web_verify_rejects_active_or_enabled_capture_owner(self) -> None:
        self.select_role("web")
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            handoff.quiesce(**self.common())
            for unit in handoff.ROLE_SERVICES["web"]:
                for state in ("active", "enabled"):
                    with self.subTest(unit=unit, state=state):
                        target = self.active if state == "active" else self.enabled
                        target[unit] = True
                        with self.assertRaisesRegex(
                            handoff.CollectorHandoffError,
                            "overlap_not_quiesced",
                        ):
                            handoff.verify(**self.common())
                        target[unit] = False

    def test_rollback_restores_exact_mixed_state_for_new_owner_units(self) -> None:
        expected = {
            unit: {"active": self.active[unit], "enabled": self.enabled[unit]}
            for unit in handoff.ROLE_UNITS[self.host_role]
        }
        expected["coin-public-market-telegram.service"] = {
            "active": False,
            "enabled": True,
        }
        for unit, state in expected.items():
            self.active[unit] = state["active"]
            self.enabled[unit] = state["enabled"]
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            handoff.quiesce(**self.common())
            rollback, digest = self.proof("ROLLED_BACK")
            handoff.restore(
                journal=self.journal,
                release_sha=RELEASE,
                primary_rollback=rollback,
                expected_primary_rollback_sha256=digest,
                host_role=self.host_role,
            )
        for unit, state in expected.items():
            self.assertEqual(self.active[unit], state["active"])
            self.assertEqual(self.enabled[unit], state["enabled"])

    def test_web_rollback_restores_exact_mixed_owner_state(self) -> None:
        self.select_role("web")
        expected = {
            "coin-capture.service": {"active": True, "enabled": False},
            "market-channel-capture.service": {
                "active": False,
                "enabled": True,
            },
        }
        for unit, state in expected.items():
            self.active[unit] = state["active"]
            self.enabled[unit] = state["enabled"]
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            handoff.quiesce(**self.common())
            rollback, digest = self.proof("ROLLED_BACK")
            handoff.restore(
                primary_rollback=rollback,
                expected_primary_rollback_sha256=digest,
                **self.common(),
            )
        for unit, state in expected.items():
            self.assertEqual(self.active[unit], state["active"])
            self.assertEqual(self.enabled[unit], state["enabled"])

    def test_quiesce_is_recoverable_until_primary_commit(self) -> None:
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            value = handoff.quiesce(**self.common())
            self.assertEqual(value["status"], "QUIESCED")
            self.assertFalse(any(self.active.values()))
            self.assertFalse(any(self.enabled.values()))
            rollback, digest = self.proof("ROLLED_BACK")
            restored = handoff.restore(
                journal=self.journal,
                release_sha=RELEASE,
                primary_rollback=rollback,
                expected_primary_rollback_sha256=digest,
                host_role=self.host_role,
            )
            self.assertEqual(restored["status"], "RESTORED")
            self.assertTrue(all(self.active.values()))
            self.assertTrue(all(self.enabled.values()))

    def test_quiesce_persists_prior_state_before_mutation_and_recovers_failure(self) -> None:
        first_disable = True

        def failing_command(arguments, *, allow=(0,)):
            nonlocal first_disable
            action = arguments[1]
            if action == "stop":
                self.assertTrue(self.journal.exists())
                self.assertEqual(
                    json.loads(self.journal.read_text(encoding="utf-8"))["status"],
                    "PREPARED",
                )
            if action == "disable" and first_disable:
                first_disable = False
                raise handoff.CollectorHandoffError("synthetic_systemd_failure")
            return self.command(arguments, allow=allow)

        with (
            patch.object(handoff, "APPROVED_ROOT", self.approved),
            patch.object(handoff, "SYSTEMD_ROOT", self.systemd),
            patch.object(handoff, "OPERATION_LOCK_PATH", self.operation_lock),
            patch.object(handoff, "_run", side_effect=failing_command),
        ):
            with self.assertRaisesRegex(
                handoff.CollectorHandoffError, "synthetic_systemd_failure"
            ):
                handoff.quiesce(**self.common())
            self.assertTrue(all(self.active.values()))
            self.assertTrue(all(self.enabled.values()))
            self.assertFalse(self.operation_lock.exists())
            self.assertEqual(
                json.loads(self.journal.read_text(encoding="utf-8"))["status"],
                "RESTORED_AFTER_QUIESCE_FAILURE",
            )

    def test_recover_restores_an_interrupted_prepared_handoff(self) -> None:
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            self.approved.mkdir(mode=0o700)
            lock_binding = handoff._acquire_maintenance_lock(
                **self.common()
            )
            prior = handoff._inventory(self.host_role)
            payload = {
                "schema": handoff.SCHEMA,
                "status": "PREPARED",
                "host_role": self.host_role,
                "release_sha": RELEASE,
                "created_at_utc": handoff._now(),
                "verified_at_utc": handoff._now(),
                "prior_units": prior,
                "current_units": prior,
                "maintenance_lock": lock_binding,
                "primary_verification_sha256": None,
                "primary_rollback_sha256": None,
                "state_deleted": False,
                "secrets_disclosed": False,
            }
            handoff._atomic(self.journal, payload, exclusive=True)
            self.active[handoff.TIMERS[0]] = False
            self.enabled[handoff.TIMERS[0]] = False
            interrupted_service = "coin-public-market-telegram.service"
            self.active[interrupted_service] = False
            self.enabled[interrupted_service] = False

            recovered = handoff.recover(
                **self.common()
            )
            self.assertEqual(
                recovered["status"], "RESTORED_AFTER_QUIESCE_FAILURE"
            )
            self.assertTrue(all(self.active.values()))
            self.assertTrue(all(self.enabled.values()))
            self.assertFalse(self.operation_lock.exists())

    def test_committed_primary_can_never_reenable_legacy_collectors(self) -> None:
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            handoff.quiesce(**self.common())
            self.record_transferred_authority()
            verification, digest = self.proof("PASS")
            committed = handoff.commit(
                journal=self.journal,
                release_sha=RELEASE,
                primary_verification=verification,
                expected_primary_verification_sha256=digest,
                host_role=self.host_role,
            )
            self.assertEqual(committed["status"], "PRIMARY_COMMITTED")
            validated = handoff.validate_committed_handoff(
                journal=self.journal,
                expected_journal_sha256=sha256(
                    self.journal.read_bytes()
                ).hexdigest(),
                release_sha=RELEASE,
                expected_primary_verification_sha256=digest,
                host_role=self.host_role,
                expected_maintenance_lock=committed["maintenance_lock"],
            )
            self.assertEqual(validated["status"], "PRIMARY_COMMITTED")
            rollback, rollback_digest = self.proof("ROLLED_BACK")
            with self.assertRaisesRegex(
                handoff.CollectorHandoffError, "committed_restore_forbidden"
            ):
                handoff.restore(
                    journal=self.journal,
                    release_sha=RELEASE,
                    primary_rollback=rollback,
                    expected_primary_rollback_sha256=rollback_digest,
                    host_role=self.host_role,
                )
            self.assertFalse(any(self.active.values()))

    def test_primary_commit_rejects_an_unrelated_pass_receipt(self) -> None:
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            handoff.quiesce(**self.common())
            self.record_transferred_authority()
            path = self.approved / "unrelated-pass.json"
            path.write_text(
                json.dumps({"release_sha": RELEASE, "status": "PASS"}),
                encoding="utf-8",
            )
            path.chmod(0o600)
            with self.assertRaisesRegex(
                handoff.CollectorHandoffError,
                "primary_proof_invalid",
            ):
                handoff.commit(
                    journal=self.journal,
                    release_sha=RELEASE,
                    primary_verification=path,
                    expected_primary_verification_sha256=sha256(
                        path.read_bytes()
                    ).hexdigest(),
                    host_role=self.host_role,
                )

    def test_restore_resumes_after_crash_with_units_partially_restored(self) -> None:
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            handoff.quiesce(**self.common())
            rollback, digest = self.proof("ROLLED_BACK")
            payload = json.loads(self.journal.read_text(encoding="utf-8"))
            payload["status"] = "RESTORING"
            payload["primary_rollback_sha256"] = digest
            handoff._atomic(self.journal, payload)
            self.active[handoff.SERVICES[0]] = True

            restored = handoff.restore(
                journal=self.journal,
                release_sha=RELEASE,
                primary_rollback=rollback,
                expected_primary_rollback_sha256=digest,
                host_role=self.host_role,
            )
            self.assertEqual(restored["status"], "RESTORED")
            self.assertTrue(all(self.active.values()))
            self.assertTrue(all(self.enabled.values()))
            self.assertFalse(self.operation_lock.exists())

    def test_restore_retry_releases_lock_left_after_terminal_journal(self) -> None:
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            handoff.quiesce(**self.common())
            rollback, digest = self.proof("ROLLED_BACK")
            payload = json.loads(self.journal.read_text(encoding="utf-8"))
            payload["status"] = "RESTORED"
            payload["primary_rollback_sha256"] = digest
            payload["current_units"] = handoff._restore_prior_units(
                payload["prior_units"], host_role=self.host_role
            )
            handoff._atomic(self.journal, payload)
            self.assertTrue(self.operation_lock.exists())

            restored = handoff.restore(
                journal=self.journal,
                release_sha=RELEASE,
                primary_rollback=rollback,
                expected_primary_rollback_sha256=digest,
                host_role=self.host_role,
            )
            self.assertEqual(restored["status"], "RESTORED")
            self.assertFalse(self.operation_lock.exists())

    def test_terminal_restore_revalidates_live_units_without_lock(self) -> None:
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            handoff.quiesce(**self.common())
            rollback, digest = self.proof("ROLLED_BACK")
            payload = json.loads(self.journal.read_text(encoding="utf-8"))
            payload["status"] = "RESTORED"
            payload["primary_rollback_sha256"] = digest
            payload["current_units"] = handoff._restore_prior_units(
                payload["prior_units"], host_role=self.host_role
            )
            handoff._atomic(self.journal, payload)
            handoff._release_maintenance_lock(
                self.journal, RELEASE, self.host_role
            )
            self.active[handoff.ROLE_SERVICES[self.host_role][0]] = False
            with self.assertRaisesRegex(
                handoff.CollectorHandoffError, "terminal_state_drift"
            ):
                handoff.restore(
                    journal=self.journal,
                    release_sha=RELEASE,
                    primary_rollback=rollback,
                    expected_primary_rollback_sha256=digest,
                    host_role=self.host_role,
                )

    def test_terminal_recovery_revalidates_live_units_without_lock(self) -> None:
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            self.approved.mkdir(mode=0o700)
            lock = handoff._acquire_maintenance_lock(
                **self.common()
            )
            prior = handoff._inventory(self.host_role)
            payload = {
                "schema": handoff.SCHEMA,
                "status": "RESTORED_AFTER_QUIESCE_FAILURE",
                "host_role": self.host_role,
                "release_sha": RELEASE,
                "created_at_utc": handoff._now(),
                "verified_at_utc": handoff._now(),
                "prior_units": prior,
                "current_units": prior,
                "maintenance_lock": lock,
                "primary_verification_sha256": None,
                "primary_rollback_sha256": None,
                "authority_transfer": None,
                "state_deleted": False,
                "secrets_disclosed": False,
            }
            handoff._atomic(self.journal, payload, exclusive=True)
            handoff._release_maintenance_lock(
                self.journal, RELEASE, self.host_role
            )
            self.enabled[handoff.ROLE_TIMERS[self.host_role][0]] = False
            with self.assertRaisesRegex(
                handoff.CollectorHandoffError, "terminal_state_drift"
            ):
                handoff.recover(**self.common())

    def test_postwrite_terminal_drift_is_caught_before_lock_release(self) -> None:
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            handoff.quiesce(**self.common())
            rollback, digest = self.proof("ROLLED_BACK")
            original_atomic = handoff._atomic

            def atomic_with_race(path, payload, *, exclusive=False):
                original_atomic(path, payload, exclusive=exclusive)
                if payload.get("status") == "RESTORED":
                    self.active[handoff.ROLE_SERVICES[self.host_role][0]] = False

            with patch.object(
                handoff, "_atomic", side_effect=atomic_with_race
            ):
                with self.assertRaisesRegex(
                    handoff.CollectorHandoffError, "terminal_state_drift"
                ):
                    handoff.restore(
                        journal=self.journal,
                        release_sha=RELEASE,
                        primary_rollback=rollback,
                        expected_primary_rollback_sha256=digest,
                        host_role=self.host_role,
                    )
            self.assertTrue(self.operation_lock.exists())

    def test_handoff_transition_lock_serializes_commit_and_restore(self) -> None:
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            handoff.quiesce(**self.common())
            rollback, rollback_digest = self.proof("ROLLED_BACK")
            with handoff._maintenance_guard(
                self.journal, RELEASE, self.host_role
            ):
                with self.assertRaisesRegex(
                    handoff.CollectorHandoffError, "transition_locked"
                ):
                    handoff.restore(
                        journal=self.journal,
                        release_sha=RELEASE,
                        primary_rollback=rollback,
                        expected_primary_rollback_sha256=rollback_digest,
                        host_role=self.host_role,
                    )
            self.assertEqual(
                json.loads(self.journal.read_text(encoding="utf-8"))["status"],
                "QUIESCED",
            )
            self.assertFalse(any(self.active.values()))

    def test_authority_wal_blocks_ordinary_recovery_until_coordinated_rollback(
        self,
    ) -> None:
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            handoff.quiesce(**self.common())
            bluegreen = self.root / "bluegreen.json"
            bluegreen.write_text("{}\n", encoding="utf-8")
            bluegreen.chmod(0o600)
            descriptor = os.open(self.operation_lock, os.O_RDWR)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                expected_lock = json.loads(
                    self.operation_lock.read_text(encoding="utf-8")
                )
                prepared = handoff.prepare_capture_authority_transfer_with_held_lock(
                    descriptor=descriptor,
                    journal=self.journal,
                    release_sha=RELEASE,
                    host_role=self.host_role,
                    expected_lock=expected_lock,
                    bluegreen_journal=bluegreen,
                    prepared_bluegreen_journal_sha256="5" * 64,
                    marker_authority_sha256="6" * 64,
                )
                self.assertEqual(prepared["status"], "AUTHORITY_TRANSFERRING")
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

            with self.assertRaisesRegex(
                handoff.CollectorHandoffError, "recovery_state_invalid"
            ):
                handoff.recover(**self.common())
            rollback, rollback_digest = self.proof("ROLLED_BACK")
            with self.assertRaisesRegex(
                handoff.CollectorHandoffError, "restore_state_invalid"
            ):
                handoff.restore(
                    journal=self.journal,
                    release_sha=RELEASE,
                    primary_rollback=rollback,
                    expected_primary_rollback_sha256=rollback_digest,
                    host_role=self.host_role,
                )

            descriptor = os.open(self.operation_lock, os.O_RDWR)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                restored_authority = (
                    handoff.mark_capture_authority_restored_with_held_lock(
                        descriptor=descriptor,
                        journal=self.journal,
                        release_sha=RELEASE,
                        host_role=self.host_role,
                        expected_lock=expected_lock,
                        bluegreen_journal=bluegreen,
                        marker_authority_sha256="6" * 64,
                    )
                )
                self.assertEqual(restored_authority["status"], "QUIESCED")
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

            restored = handoff.restore(
                journal=self.journal,
                release_sha=RELEASE,
                primary_rollback=rollback,
                expected_primary_rollback_sha256=rollback_digest,
                host_role=self.host_role,
            )
            self.assertEqual(restored["status"], "RESTORED")

    def test_host_local_authority_commands_bind_exact_receipt_and_rollback(
        self,
    ) -> None:
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            handoff.quiesce(**self.common())
            bluegreen = self.root / "bluegreen.json"
            bluegreen.write_text("{}\n", encoding="utf-8")
            bluegreen.chmod(0o600)
            first_digest = sha256(self.journal.read_bytes()).hexdigest()
            prepared = handoff.prepare_authority(
                expected_journal_sha256=first_digest,
                bluegreen_journal=bluegreen,
                prepared_bluegreen_journal_sha256="5" * 64,
                marker_authority_sha256="6" * 64,
                **self.common(),
            )
            self.assertEqual(prepared["status"], "AUTHORITY_TRANSFERRING")
            with self.assertRaisesRegex(
                handoff.CollectorHandoffError, "journal_digest_invalid"
            ):
                handoff.mark_authority_transferred(
                    expected_journal_sha256=first_digest,
                    bluegreen_journal=bluegreen,
                    authorization_bluegreen_journal_sha256="7" * 64,
                    marker_authority_sha256="6" * 64,
                    **self.common(),
                )
            transferred = handoff.mark_authority_transferred(
                expected_journal_sha256=sha256(
                    self.journal.read_bytes()
                ).hexdigest(),
                bluegreen_journal=bluegreen,
                authorization_bluegreen_journal_sha256="7" * 64,
                marker_authority_sha256="6" * 64,
                **self.common(),
            )
            self.assertEqual(transferred["status"], "AUTHORITY_TRANSFERRED")
            restored_authority = handoff.mark_authority_restored(
                expected_journal_sha256=sha256(
                    self.journal.read_bytes()
                ).hexdigest(),
                bluegreen_journal=bluegreen,
                marker_authority_sha256="6" * 64,
                **self.common(),
            )
            self.assertEqual(restored_authority["status"], "QUIESCED")
            rollback, rollback_digest = self.proof("ROLLED_BACK")
            restored = handoff.restore(
                primary_rollback=rollback,
                expected_primary_rollback_sha256=rollback_digest,
                **self.common(),
            )
            self.assertEqual(restored["status"], "RESTORED")

    def test_guard_does_not_relabel_transition_io_failure_as_lock_corruption(
        self,
    ) -> None:
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            handoff.quiesce(**self.common())
            with self.assertRaisesRegex(OSError, "synthetic_journal_io_failure"):
                with handoff._maintenance_guard(
                    self.journal, RELEASE, self.host_role
                ):
                    raise OSError("synthetic_journal_io_failure")

    def test_quiesce_same_journal_is_idempotent_after_live_verify(self) -> None:
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            first = handoff.quiesce(**self.common())
            self.assertEqual(first["status"], "QUIESCED")
            first_bytes = self.journal.read_bytes()
            second = handoff.quiesce(**self.common())
            self.assertEqual(second["status"], "QUIESCED")
            self.assertEqual(self.journal.read_bytes(), first_bytes)
            self.assertFalse(any(self.active.values()))
            other = self.approved / "other-run.json"
            other.write_bytes(first_bytes)
            other.chmod(0o600)
            with self.assertRaisesRegex(
                handoff.CollectorHandoffError,
                "collector_handoff_journal_exists|collector_handoff_journal_invalid",
            ):
                handoff.quiesce(
                    journal=other,
                    release_sha="b" * 40,
                    host_role=self.host_role,
                )
            self.assertEqual(self.journal.read_bytes(), first_bytes)

    def test_prepared_journal_retry_continues_same_binding(self) -> None:
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            self.approved.mkdir(mode=0o700)
            lock_binding = handoff._acquire_maintenance_lock(**self.common())
            prior = handoff._inventory(self.host_role)
            payload = {
                "schema": handoff.SCHEMA,
                "status": "PREPARED",
                "host_role": self.host_role,
                "release_sha": RELEASE,
                "created_at_utc": handoff._now(),
                "verified_at_utc": handoff._now(),
                "prior_units": prior,
                "current_units": prior,
                "maintenance_lock": lock_binding,
                "primary_verification_sha256": None,
                "primary_rollback_sha256": None,
                "authority_transfer": None,
                "state_deleted": False,
                "secrets_disclosed": False,
            }
            handoff._atomic(self.journal, payload, exclusive=True)
            result = handoff.quiesce(**self.common())
            self.assertEqual(result["status"], "QUIESCED")
            self.assertFalse(any(self.active.values()))

    def test_sigkill_between_prepared_and_wal_retries_same_journal(self) -> None:
        ready = self.root / "prepared.ready"
        child_code = r"""
import json, os, sys, time
from pathlib import Path
from unittest.mock import patch
from scripts import quiesce_production_legacy_market_collectors as handoff
approved, systemd, lock, journal, ready, release, role = sys.argv[1:8]
active = {unit: True for unit in handoff.ROLE_UNITS[role]}
enabled = {unit: True for unit in handoff.ROLE_UNITS[role]}
def command(arguments, *, allow=(0,)):
    action = arguments[1]; unit = arguments[-1]
    if action == "is-active":
        code = 0 if active[unit] else 3
    elif action == "is-enabled":
        code = 0 if enabled[unit] else 1
    elif action in {"stop", "start"}:
        active[unit] = action == "start"; code = 0
    elif action in {"disable", "enable"}:
        enabled[unit] = action == "enable"; code = 0
    else:
        raise AssertionError(arguments)
    return type("Result", (), {"returncode": code, "stdout": "", "stderr": ""})()
real_complete = handoff._complete_quiesce_from_prepared
def hang(**kwargs):
    Path(ready).write_text("prepared\n", encoding="utf-8")
    time.sleep(60)
    return real_complete(**kwargs)
with patch.object(handoff, "APPROVED_ROOT", Path(approved)), \
     patch.object(handoff, "SYSTEMD_ROOT", Path(systemd)), \
     patch.object(handoff, "OPERATION_LOCK_PATH", Path(lock)), \
     patch.object(handoff, "_run", side_effect=command), \
     patch.object(handoff, "_complete_quiesce_from_prepared", side_effect=hang):
    handoff.quiesce(journal=Path(journal), release_sha=release, host_role=role)
"""
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                child_code,
                str(self.approved),
                str(self.systemd),
                str(self.operation_lock),
                str(self.journal),
                str(ready),
                RELEASE,
                self.host_role,
            ],
            cwd=str(Path(__file__).resolve().parents[1]),
            env={
                "HOME": os.environ.get("HOME", "/root"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                "APP_ENV_FILE": str(
                    Path(__file__).resolve().parents[1]
                    / "config/unit-test.env.example"
                ),
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.time() + 10
            while time.time() < deadline and not ready.is_file():
                if child.poll() is not None:
                    self.fail("quiesce child exited before PREPARED")
                time.sleep(0.05)
            self.assertTrue(ready.is_file())
            payload = json.loads(self.journal.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "PREPARED")
            self.assertIs(payload["state_deleted"], False)
            prepared_bytes = self.journal.read_bytes()
            os.kill(child.pid, signal.SIGKILL)
            self.assertEqual(child.wait(timeout=5), -signal.SIGKILL)
            self.assertEqual(self.journal.read_bytes(), prepared_bytes)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
        context = self.context()
        with context[0], context[1], context[2], context[3]:
            result = handoff.quiesce(**self.common())
            self.assertEqual(result["status"], "QUIESCED")
            self.assertIs(result["state_deleted"], False)
            self.assertFalse(any(self.active.values()))
            after = self.journal.read_bytes()
            retry = handoff.quiesce(**self.common())
            self.assertEqual(retry["status"], "QUIESCED")
            self.assertEqual(self.journal.read_bytes(), after)


if __name__ == "__main__":
    unittest.main()
