"""Focused non-authorizing composition tests for the three selected P0s."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import unittest
from uuid import UUID

from core.application_writer_term import ValidatedWriterTerm
from core.external_effect_execution_gate import (
    EXTERNAL_EFFECT_EXECUTION_SCOPES,
    RECONCILIATION_DECISION_COMPLETE_NO_RESEND,
    ExternalEffectExecutionAuthorization,
)
from core.promotion_p0_continuity_preflight import (
    PromotionP0ContinuityPreflightBinding,
    PromotionP0ContinuityPreflightConfig,
    PromotionP0ContinuityPreflightError,
    PromotionP0ContinuityPreflightInputs,
    require_verified_promotion_p0_continuity_preflight,
    verify_promotion_p0_continuity_preflight,
)
from core.services.promotion_continuity_participants import (
    PromotionContinuityParticipantsResult,
)
from core.services.promotion_session_invalidation_service import (
    PromotionSessionInvalidationResult,
)
from core.services.promotion_upload_cleanup_service import PromotionUploadCleanupResult


NOW = datetime(2026, 8, 1, 9, 0, 0, tzinfo=timezone.utc)
OPERATION_ID = UUID("12345678-1234-4234-9234-123456789abc")
AUTHORIZATION_ID = "promotion-no-resend-20260801"
EVIDENCE_SHA256 = "e" * 64


def writer_term(**overrides: object) -> ValidatedWriterTerm:
    values: dict[str, object] = {
        "holder_site": "webapp_ir",
        "writer_epoch": 12,
        "lease_id": "lease-12",
        "issued_at": NOW - timedelta(seconds=20),
        "expires_at": NOW + timedelta(seconds=60),
        "witness_transition_id": "transition-12",
    }
    values.update(overrides)
    return ValidatedWriterTerm(**values)


def auth_result(**overrides: object) -> PromotionSessionInvalidationResult:
    cutover = NOW - timedelta(seconds=1)
    values: dict[str, object] = {
        "operation_id": OPERATION_ID,
        "writer_site": "webapp_ir",
        "writer_epoch": 12,
        "writer_lease_id": "lease-12",
        "witness_transition_id": "transition-12",
        "cutover_at": cutover,
        "minimum_token_iat": math.ceil(cutover.timestamp()),
        "invalidated_sessions": 4,
        "expired_login_requests": 2,
        "cancelled_recovery_requests": 1,
        "applied": True,
    }
    values.update(overrides)
    return PromotionSessionInvalidationResult(**values)


def upload_result(**overrides: object) -> PromotionUploadCleanupResult:
    values: dict[str, object] = {
        "operation_id": OPERATION_ID,
        "writer_site": "webapp_ir",
        "writer_epoch": 12,
        "writer_lease_id": "lease-12",
        "witness_transition_id": "transition-12",
        "cutover_at": NOW - timedelta(seconds=1),
        "cancelled_session_ids": ("upload-1",),
        "cancelled_batch_ids": ("batch-1",),
        "applied": True,
    }
    values.update(overrides)
    return PromotionUploadCleanupResult(**values)


def participants(**overrides: object) -> PromotionContinuityParticipantsResult:
    values: dict[str, object] = {
        "auth": auth_result(),
        "uploads": upload_result(),
    }
    values.update(overrides)
    return PromotionContinuityParticipantsResult(**values)


def external_authorization(**overrides: object) -> ExternalEffectExecutionAuthorization:
    term = writer_term()
    values: dict[str, object] = {
        "authorization_id": AUTHORIZATION_ID,
        "holder_site": term.holder_site,
        "writer_epoch": term.writer_epoch,
        "writer_lease_id": term.lease_id,
        "writer_term_issued_at": term.issued_at,
        "writer_term_expires_at": term.expires_at,
        "witness_transition_id": term.witness_transition_id,
        "authorized_scopes": tuple(sorted(EXTERNAL_EFFECT_EXECUTION_SCOPES)),
        "reconciliation_decision": RECONCILIATION_DECISION_COMPLETE_NO_RESEND,
        "reconciliation_evidence_sha256": EVIDENCE_SHA256,
        "reconciliation_completed_at": NOW - timedelta(seconds=10),
        "issued_at": NOW - timedelta(seconds=5),
        "expires_at": NOW + timedelta(seconds=30),
    }
    values.update(overrides)
    return ExternalEffectExecutionAuthorization(**values)


def binding(**overrides: object) -> PromotionP0ContinuityPreflightBinding:
    values: dict[str, object] = {
        "operation_id": OPERATION_ID,
        "writer_term": writer_term(),
        "external_effect_authorization_id": AUTHORIZATION_ID,
        "external_effect_reconciliation_evidence_sha256": EVIDENCE_SHA256,
    }
    values.update(overrides)
    return PromotionP0ContinuityPreflightBinding(**values)


def inputs(**overrides: object) -> PromotionP0ContinuityPreflightInputs:
    values: dict[str, object] = {
        "auth_upload_result": participants(),
        "external_effect_authorization": external_authorization(),
    }
    values.update(overrides)
    return PromotionP0ContinuityPreflightInputs(**values)


class PromotionP0ContinuityPreflightTests(unittest.TestCase):
    def enabled_config(self, **overrides: object) -> PromotionP0ContinuityPreflightConfig:
        values: dict[str, object] = {
            "enabled": True,
            "maximum_evidence_age_seconds": 60,
        }
        values.update(overrides)
        return PromotionP0ContinuityPreflightConfig(**values)

    def test_default_off_rejects_before_untrusted_inputs_are_inspected(self) -> None:
        with self.assertRaisesRegex(
            PromotionP0ContinuityPreflightError,
            "PREFLIGHT_DISABLED",
        ):
            verify_promotion_p0_continuity_preflight(
                PromotionP0ContinuityPreflightConfig(),
                object(),
                object(),
                now=NOW,
            )

    def test_exact_auth_upload_and_all_scope_no_resend_decision_are_bound_together(self) -> None:
        result = verify_promotion_p0_continuity_preflight(
            self.enabled_config(),
            binding(),
            inputs(),
            now=NOW,
        )

        self.assertEqual(OPERATION_ID, result.operation_id)
        self.assertEqual("webapp_ir", result.writer_site)
        self.assertEqual(12, result.writer_epoch)
        self.assertEqual(AUTHORIZATION_ID, result.external_effect_authorization_id)
        self.assertEqual(EVIDENCE_SHA256, result.external_effect_reconciliation_evidence_sha256)
        self.assertIs(
            result,
            require_verified_promotion_p0_continuity_preflight(
                result,
                now=NOW + timedelta(seconds=1),
            ),
        )
        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
            result.__reduce_ex__(4)

    def test_clean_upload_surface_is_a_successful_no_op_not_a_promotion_blocker(self) -> None:
        result = verify_promotion_p0_continuity_preflight(
            self.enabled_config(),
            binding(),
            inputs(
                auth_upload_result=participants(
                    uploads=upload_result(
                        cancelled_session_ids=(),
                        cancelled_batch_ids=(),
                        applied=False,
                    )
                )
            ),
            now=NOW,
        )
        self.assertEqual(OPERATION_ID, result.operation_id)

    def test_upload_cleanup_applied_flag_must_match_its_cancelled_ids(self) -> None:
        cases = {
            "claimed-work-without-ids": upload_result(
                cancelled_session_ids=(),
                cancelled_batch_ids=(),
                applied=True,
            ),
            "unclaimed-work-with-ids": upload_result(applied=False),
        }
        for label, uploads in cases.items():
            with self.subTest(label=label), self.assertRaisesRegex(
                PromotionP0ContinuityPreflightError,
                "AUTH_UPLOAD_MISMATCH",
            ):
                verify_promotion_p0_continuity_preflight(
                    self.enabled_config(),
                    binding(),
                    inputs(auth_upload_result=participants(uploads=uploads)),
                    now=NOW,
                )

    def test_auth_upload_participants_cannot_be_missing_or_from_another_term(self) -> None:
        cases = {
            "missing": inputs(auth_upload_result=object()),
            "other-term": inputs(
                auth_upload_result=participants(
                    uploads=upload_result(writer_epoch=13),
                )
            ),
            "not-applied": inputs(
                auth_upload_result=participants(auth=auth_result(applied=False)),
            ),
        }
        for label, candidate in cases.items():
            with self.subTest(label=label), self.assertRaisesRegex(
                PromotionP0ContinuityPreflightError,
                "AUTH_UPLOAD",
            ):
                verify_promotion_p0_continuity_preflight(
                    self.enabled_config(),
                    binding(),
                    candidate,
                    now=NOW,
                )

    def test_external_effect_receipt_must_pin_the_same_term_id_hash_and_every_scope(self) -> None:
        partial_scopes = tuple(sorted(EXTERNAL_EFFECT_EXECUTION_SCOPES))[:-1]
        cases = {
            "wrong-authorisation-id": inputs(
                external_effect_authorization=external_authorization(
                    authorization_id="different-no-resend-receipt"
                )
            ),
            "wrong-evidence": inputs(
                external_effect_authorization=external_authorization(
                    reconciliation_evidence_sha256="a" * 64
                )
            ),
            "missing-scope": inputs(
                external_effect_authorization=external_authorization(
                    authorized_scopes=partial_scopes
                )
            ),
            "other-term": inputs(
                external_effect_authorization=external_authorization(writer_epoch=13)
            ),
        }
        for label, candidate in cases.items():
            with self.subTest(label=label), self.assertRaisesRegex(
                PromotionP0ContinuityPreflightError,
                "EXTERNAL_EFFECT",
            ):
                verify_promotion_p0_continuity_preflight(
                    self.enabled_config(),
                    binding(),
                    candidate,
                    now=NOW,
                )

    def test_provenance_recheck_fails_after_no_resend_receipt_expires_and_forgery_is_rejected(self) -> None:
        result = verify_promotion_p0_continuity_preflight(
            self.enabled_config(),
            binding(),
            inputs(),
            now=NOW,
        )
        with self.assertRaisesRegex(
            PromotionP0ContinuityPreflightError,
            "EXTERNAL_EFFECT_STALE_OR_EXPIRED",
        ):
            require_verified_promotion_p0_continuity_preflight(
                result,
                now=NOW + timedelta(seconds=31),
            )

        forged = replace(result, writer_epoch=13)
        with self.assertRaisesRegex(
            PromotionP0ContinuityPreflightError,
            "CAPABILITY_REQUIRED",
        ):
            require_verified_promotion_p0_continuity_preflight(forged, now=NOW)

    def test_preflight_has_no_database_or_runtime_transport_dependency(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "promotion_p0_continuity_preflight.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_modules.add(node.module)
        for forbidden in (
            "core.db",
            "sqlalchemy",
            "subprocess",
            "socket",
            "requests",
            "httpx",
            "boto3",
            "paramiko",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, imported_modules)


if __name__ == "__main__":
    unittest.main()
