from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.application_writer_term import ValidatedWriterTerm
from core import db
from core import external_effect_execution_gate as gate


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


def active_term(
    *,
    holder_site: str = "webapp_fi",
    epoch: int = 7,
    lease_id: str = "lease-7",
    transition_id: str = "transition-7",
) -> ValidatedWriterTerm:
    return ValidatedWriterTerm(
        holder_site=holder_site,
        writer_epoch=epoch,
        lease_id=lease_id,
        issued_at=NOW - timedelta(seconds=20),
        expires_at=NOW + timedelta(seconds=70),
        witness_transition_id=transition_id,
    )


def authorization(
    term: ValidatedWriterTerm,
    *,
    scopes: tuple[str, ...] = (gate.EXTERNAL_EFFECT_SCOPE_TRADE_TELEGRAM_DELIVERY,),
    holder_site: str | None = None,
    epoch: int | None = None,
    issued_at: datetime = NOW - timedelta(seconds=5),
    expires_at: datetime = NOW + timedelta(seconds=40),
) -> gate.ExternalEffectExecutionAuthorization:
    return gate.ExternalEffectExecutionAuthorization(
        authorization_id="external-effects-7",
        holder_site=term.holder_site if holder_site is None else holder_site,
        writer_epoch=term.writer_epoch if epoch is None else epoch,
        writer_lease_id=term.lease_id,
        writer_term_issued_at=term.issued_at,
        writer_term_expires_at=term.expires_at,
        witness_transition_id=term.witness_transition_id,
        authorized_scopes=tuple(sorted(scopes)),
        reconciliation_decision=gate.RECONCILIATION_DECISION_COMPLETE_NO_RESEND,
        reconciliation_evidence_sha256="a" * 64,
        reconciliation_completed_at=issued_at - timedelta(seconds=1),
        issued_at=issued_at,
        expires_at=expires_at,
    )


def enabled_policy(path: Path) -> gate.ExternalEffectExecutionGatePolicy:
    return gate.ExternalEffectExecutionGatePolicy(
        enabled=True,
        local_site="webapp_fi",
        authorization_file=path,
        owner_uid=os.geteuid(),
        safety_margin_seconds=5,
        max_authorization_duration_seconds=60,
    )


class ExternalEffectExecutionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "external-effect-authorization.json"
        self.term = active_term()
        self.policy = enabled_policy(self.path)

    def install(self, value: gate.ExternalEffectExecutionAuthorization | None = None) -> None:
        gate.write_external_effect_execution_authorization(
            self.path,
            value or authorization(self.term),
            owner_uid=os.geteuid(),
        )

    def test_disabled_policy_never_opens_a_term_or_authorization_file(self) -> None:
        result = gate.require_external_effect_execution_authorization(
            gate.ExternalEffectExecutionGatePolicy(
                enabled=False,
                local_site="not-a-webapp-site",
                authorization_file=Path("relative-and-never-opened.json"),
            ),
            active_writer_term=None,
            scope=gate.EXTERNAL_EFFECT_SCOPE_TRADE_TELEGRAM_DELIVERY,
            now=NOW,
        )

        self.assertIsNone(result)

    def test_enabled_matching_authorization_is_root_owned_and_term_bound(self) -> None:
        self.install()

        loaded = gate.require_external_effect_execution_authorization(
            self.policy,
            active_writer_term=self.term,
            scope=gate.EXTERNAL_EFFECT_SCOPE_TRADE_TELEGRAM_DELIVERY,
            now=NOW,
        )

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.writer_epoch, self.term.writer_epoch)
        self.assertEqual(loaded.reconciliation_decision, gate.RECONCILIATION_DECISION_COMPLETE_NO_RESEND)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_missing_enabled_authorization_fails_closed_before_any_worker_can_use_it(self) -> None:
        with self.assertRaisesRegex(gate.ExternalEffectExecutionGateError, "cannot be opened safely"):
            gate.require_external_effect_execution_authorization(
                self.policy,
                active_writer_term=self.term,
                scope=gate.EXTERNAL_EFFECT_SCOPE_SMS_PROVIDER_DELIVERY,
                now=NOW,
            )

    def test_term_change_or_wrong_epoch_fails_closed(self) -> None:
        self.install()
        new_term = replace(self.term, writer_epoch=self.term.writer_epoch + 1, lease_id="lease-8")

        with self.assertRaisesRegex(gate.ExternalEffectExecutionGateError, "active Writer Witness term"):
            gate.require_external_effect_execution_authorization(
                self.policy,
                active_writer_term=new_term,
                scope=gate.EXTERNAL_EFFECT_SCOPE_TRADE_TELEGRAM_DELIVERY,
                now=NOW,
            )

    def test_wrong_holder_and_missing_scope_fail_closed(self) -> None:
        wrong_holder_term = active_term(holder_site="webapp_ir")
        self.install(authorization(wrong_holder_term))

        with self.assertRaisesRegex(gate.ExternalEffectExecutionGateError, "active holder"):
            gate.require_external_effect_execution_authorization(
                self.policy,
                active_writer_term=wrong_holder_term,
                scope=gate.EXTERNAL_EFFECT_SCOPE_TRADE_TELEGRAM_DELIVERY,
                now=NOW,
            )

        self.install(authorization(self.term))
        with self.assertRaisesRegex(gate.ExternalEffectExecutionGateError, "does not permit"):
            gate.require_external_effect_execution_authorization(
                self.policy,
                active_writer_term=self.term,
                scope=gate.EXTERNAL_EFFECT_SCOPE_OFFER_TELEGRAM_PUBLICATION,
                now=NOW,
            )

    def test_expired_or_stale_authorization_fails_closed(self) -> None:
        self.install(
            authorization(
                self.term,
                issued_at=NOW - timedelta(seconds=10),
                expires_at=NOW,
            )
        )

        with self.assertRaisesRegex(gate.ExternalEffectExecutionGateError, "expired"):
            gate.require_external_effect_execution_authorization(
                self.policy,
                active_writer_term=self.term,
                scope=gate.EXTERNAL_EFFECT_SCOPE_TRADE_TELEGRAM_DELIVERY,
                now=NOW,
            )

    def test_reconciliation_no_resend_decision_is_not_optional(self) -> None:
        value = authorization(self.term)
        invalid = gate.external_effect_execution_authorization_mapping(value)
        invalid["reconciliation_decision"] = "retry_previous_effects"

        with self.assertRaisesRegex(gate.ExternalEffectExecutionGateError, "decision"):
            gate.parse_external_effect_execution_authorization(invalid)

    def test_atomic_replacement_does_not_damage_previous_authorization_on_failure(self) -> None:
        self.install()
        original = self.path.read_bytes()
        replacement = replace(authorization(self.term), authorization_id="external-effects-8")

        with patch("core.external_effect_execution_gate.os.replace", side_effect=OSError("injected")), self.assertRaisesRegex(
            gate.ExternalEffectExecutionGateError,
            "atomically written",
        ):
            gate.write_external_effect_execution_authorization(
                self.path,
                replacement,
                owner_uid=os.geteuid(),
            )

        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob(".*.tmp")), [])

        gate.write_external_effect_execution_authorization(
            self.path,
            replacement,
            owner_uid=os.geteuid(),
        )
        self.assertEqual(
            gate.load_external_effect_execution_authorization(
                self.path,
                owner_uid=os.geteuid(),
            ).authorization_id,
            "external-effects-8",
        )

    def test_insecure_or_symlinked_authorization_is_rejected(self) -> None:
        self.install()
        self.path.chmod(0o644)
        with self.assertRaisesRegex(gate.ExternalEffectExecutionGateError, "0600"):
            gate.load_external_effect_execution_authorization(self.path, owner_uid=os.geteuid())

        target = self.path.parent / "target.json"
        self.path.chmod(0o600)
        self.path.replace(target)
        self.path.symlink_to(target)
        with self.assertRaisesRegex(gate.ExternalEffectExecutionGateError, "opened safely"):
            gate.load_external_effect_execution_authorization(self.path, owner_uid=os.geteuid())

    def test_db_wrapper_rejects_a_term_replacement_during_authorization_read(self) -> None:
        self.install()
        replacement = replace(self.term, writer_epoch=8, lease_id="lease-8")
        with patch("core.db.external_effect_execution_gate_policy", return_value=self.policy), patch(
            "core.db.require_application_writer_term",
            side_effect=[self.term, replacement],
        ), patch(
            "core.external_effect_execution_gate._utc_now",
            return_value=NOW,
        ), self.assertRaisesRegex(gate.ExternalEffectExecutionGateError, "changed while"):
            db.require_external_effect_execution_authorization(
                gate.EXTERNAL_EFFECT_SCOPE_TRADE_TELEGRAM_DELIVERY
            )

    def test_disabled_settings_projection_does_not_read_a_path_setting(self) -> None:
        class DisabledSettings:
            external_effect_execution_gate_enforced = False

            def __getattr__(self, name: str) -> object:
                raise AssertionError(f"disabled projection unexpectedly read {name}")

        policy = gate.policy_from_settings(DisabledSettings())
        self.assertFalse(policy.enabled)
        self.assertIsNone(policy.authorization_file)

    def test_disabled_db_wrapper_does_not_open_the_writer_term(self) -> None:
        with patch.object(db.settings, "external_effect_execution_gate_enforced", False), patch.object(
            db.settings, "application_writer_term_enforced", False
        ), patch("core.db.require_application_writer_term") as writer_term:
            result = db.require_external_effect_execution_authorization(
                gate.EXTERNAL_EFFECT_SCOPE_TRADE_TELEGRAM_DELIVERY
            )

        self.assertIsNone(result)
        writer_term.assert_not_called()

    def test_disabled_external_effect_gate_rechecks_an_enabled_writer_term(self) -> None:
        with patch.object(db.settings, "external_effect_execution_gate_enforced", False), patch.object(
            db.settings, "application_writer_term_enforced", True
        ), patch("core.db.require_application_writer_term") as writer_term:
            result = db.require_external_effect_execution_authorization(
                gate.EXTERNAL_EFFECT_SCOPE_TRADE_TELEGRAM_DELIVERY
            )

        self.assertIsNone(result)
        writer_term.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
