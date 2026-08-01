from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import patch

import core.physical_arvan_immutability_preflight as immutability


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


def binding(**overrides: object) -> immutability.PhysicalArvanImmutabilityPreflightBinding:
    values: dict[str, object] = {
        "campaign_id": "physical-arvan-preflight-20260731",
        "release_sha": "3138d0c2a8d20a84042c3a438fbc88db7a4db498",
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "route_binding_sha256": "a" * 64,
        "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
        "region": "ir-thr-at1",
        "bucket": "private-physical-recovery",
        "minimum_retention_days": 90,
    }
    values.update(overrides)
    return immutability.PhysicalArvanImmutabilityPreflightBinding(**values)


def denied(*operations: str) -> tuple[immutability.PhysicalArvanDeniedOperationObservation, ...]:
    return tuple(
        immutability.PhysicalArvanDeniedOperationObservation(
            operation=operation,
            outcome=immutability.ARVAN_DISPOSABLE_DELETE_DENIED,
        )
        for operation in operations
    )


def restrictions(
    **overrides: object,
) -> tuple[immutability.PhysicalArvanCredentialRestrictionObservation, ...]:
    values: dict[str, immutability.PhysicalArvanCredentialRestrictionObservation] = {
        "fi": immutability.PhysicalArvanCredentialRestrictionObservation(
            role="fi-publisher",
            credential_posture="scoped-credential-probed",
            credential_identity_sha256="b" * 64,
            allowed_operations=(
                "GetBucketAcl",
                "GetBucketVersioning",
                "GetObjectLockConfiguration",
                "PutObject:create-only",
                "ListObjectVersions:exact-key",
                "GetObjectRetention:exact-version",
                "GetObject:exact-version",
                "HeadObject:exact-version",
            ),
            denied_operations=denied(
                "DeleteObject", "DeleteObjectVersion", "PutObject:overwrite"
            ),
        ),
        "ir": immutability.PhysicalArvanCredentialRestrictionObservation(
            role="ir-receiver",
            credential_posture="scoped-credential-probed",
            credential_identity_sha256="c" * 64,
            allowed_operations=("GetObject:exact-version", "HeadObject:exact-version"),
            denied_operations=denied(
                "DeleteObject",
                "DeleteObjectVersion",
                "ListBucket",
                "ListObjectVersions",
                "PutObject",
            ),
        ),
        "witness": immutability.PhysicalArvanCredentialRestrictionObservation(
            role="witness-controller",
            credential_posture="no-object-storage-credential-issued",
            credential_identity_sha256=None,
            allowed_operations=(),
            denied_operations=(),
        ),
    }
    values.update(overrides)
    return (values["fi"], values["ir"], values["witness"])


def disposable_probe(
    **overrides: object,
) -> immutability.PhysicalArvanDisposableImmutabilityProbe:
    values: dict[str, object] = {
        "object_key": "physical-preflight/physical-arvan-preflight-20260731/arvan-immutability/nonce-20260731.age",
        "version_id": "preflight-version-20260731",
        "ciphertext_sha256": "d" * 64,
        "ciphertext_bytes": 427,
        "delete_version_outcome": "access-denied",
        "delete_marker_outcome": "access-denied",
        "exact_version_get_outcome": "exact-version-get-succeeded",
        "retrieved_version_id": "preflight-version-20260731",
        "retrieved_ciphertext_sha256": "d" * 64,
        "retrieved_ciphertext_bytes": 427,
    }
    values.update(overrides)
    return immutability.PhysicalArvanDisposableImmutabilityProbe(**values)


def observation(
    **overrides: object,
) -> immutability.PhysicalArvanImmutabilityPreflightObservation:
    values: dict[str, object] = {
        "binding": binding(),
        "versioning_status": "Enabled",
        "acl_posture": "private-canonical-owner-only-v1",
        "retention_mode": "provider-verified-immutable-retention-v1",
        "retention_policy_evidence_sha256": "e" * 64,
        "retention_days": 180,
        "credential_restrictions": restrictions(),
        "disposable_probe": disposable_probe(),
        "observed_at": NOW,
    }
    values.update(overrides)
    return immutability.build_physical_arvan_immutability_preflight_observation(**values)


class _Probe:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls = 0

    def collect(self, **_kwargs: object):
        self.calls += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class PhysicalArvanImmutabilityPreflightTests(unittest.TestCase):
    def test_exact_evidence_is_opaque_fresh_and_projectable(self) -> None:
        raw = observation()
        verified = immutability.verify_physical_arvan_immutability_preflight(
            raw,
            binding=binding(),
            now=NOW,
        )
        self.assertIs(
            verified,
            immutability.require_verified_physical_arvan_immutability_preflight(
                verified,
                binding=binding(),
                now=NOW,
            ),
        )
        projection = immutability.project_verified_physical_arvan_immutability_preflight(
            verified,
            binding=binding(),
            now=NOW,
        )
        self.assertEqual("webapp_fi", projection.source_site)
        self.assertEqual("webapp_ir", projection.destination_site)
        self.assertEqual(180, projection.retention_days)
        self.assertNotIn(
            b'"evidence_sha256":',
            immutability.canonical_physical_arvan_immutability_preflight_evidence_bytes(raw),
        )

    def test_tamper_hash_staleness_and_future_observation_fail_closed(self) -> None:
        raw = observation()
        for bad in (
            replace(raw, evidence_sha256="f" * 64),
            replace(raw, observed_at=NOW - timedelta(seconds=301)),
            replace(raw, observed_at=NOW + timedelta(seconds=6)),
        ):
            with self.assertRaises(immutability.PhysicalArvanImmutabilityPreflightError):
                immutability.verify_physical_arvan_immutability_preflight(
                    bad,
                    binding=binding(),
                    now=NOW,
                )

    def test_disposable_delete_survival_is_a_hard_requirement(self) -> None:
        # The builder itself refuses to produce misleading evidence.
        with self.assertRaises(immutability.PhysicalArvanImmutabilityPreflightError):
            observation(disposable_probe=disposable_probe(delete_marker_outcome="succeeded"))

    def test_builder_rejects_wrong_scope_and_unseparated_credentials(self) -> None:
        with self.assertRaises(immutability.PhysicalArvanImmutabilityPreflightError):
            observation(
                disposable_probe=disposable_probe(
                    object_key="physical-preflight/other/arvan-immutability/nonce-20260731.age"
                )
            )
        with self.assertRaises(immutability.PhysicalArvanImmutabilityPreflightError):
            observation(
                credential_restrictions=restrictions(
                    ir=replace(restrictions()[1], credential_identity_sha256="b" * 64)
                )
            )

    def test_retention_acl_versioning_and_role_posture_cannot_drift(self) -> None:
        cases = (
            {"versioning_status": "Suspended"},
            {"acl_posture": "public-read"},
            {"retention_mode": "versioned-best-effort"},
            {"retention_days": 89},
            {
                "credential_restrictions": restrictions(
                    witness=replace(
                        restrictions()[2],
                        credential_posture="scoped-credential-probed",
                        credential_identity_sha256="f" * 64,
                    )
                )
            },
        )
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises(immutability.PhysicalArvanImmutabilityPreflightError):
                    observation(**values)

    def test_binding_only_accepts_normal_arvan_route_and_retention_floor(self) -> None:
        for bad in (
            binding(destination_site="webapp_fi"),
            binding(endpoint="http://s3.ir-thr-at1.arvanstorage.ir"),
            binding(region="wrong-region"),
            binding(bucket="https://wrong.invalid"),
            binding(minimum_retention_days=6),
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(immutability.PhysicalArvanImmutabilityPreflightError):
                    immutability.verify_physical_arvan_immutability_preflight(
                        observation(), binding=bad, now=NOW
                    )

    def test_default_off_and_invalid_config_do_not_touch_probe(self) -> None:
        probe = _Probe(observation())
        with self.assertRaises(immutability.PhysicalArvanImmutabilityPreflightError):
            immutability.collect_physical_arvan_immutability_preflight(
                config=immutability.PhysicalArvanImmutabilityPreflightConfig(
                    binding=binding(), enabled=False
                ),
                probe=probe,
                now=NOW,
            )
        self.assertEqual(0, probe.calls)
        with self.assertRaises(immutability.PhysicalArvanImmutabilityPreflightError):
            immutability.collect_physical_arvan_immutability_preflight(
                config=object(),  # type: ignore[arg-type]
                probe=probe,
                now=NOW,
            )
        self.assertEqual(0, probe.calls)

    def test_collect_requires_root_then_verifies_injected_live_probe(self) -> None:
        probe = _Probe(observation())
        config = immutability.PhysicalArvanImmutabilityPreflightConfig(
            binding=binding(), enabled=True
        )
        with patch.object(immutability.os, "geteuid", return_value=1000):
            with self.assertRaises(immutability.PhysicalArvanImmutabilityPreflightError):
                immutability.collect_physical_arvan_immutability_preflight(
                    config=config, probe=probe, now=NOW
                )
        self.assertEqual(0, probe.calls)
        with patch.object(immutability.os, "geteuid", return_value=0):
            verified = immutability.collect_physical_arvan_immutability_preflight(
                config=config, probe=probe, now=NOW
            )
        self.assertEqual(1, probe.calls)
        self.assertIsInstance(verified, immutability.VerifiedPhysicalArvanImmutabilityPreflight)

    def test_replaced_opaque_result_is_not_trusted(self) -> None:
        verified = immutability.verify_physical_arvan_immutability_preflight(
            observation(), binding=binding(), now=NOW
        )
        forged = replace(verified, maximum_evidence_age_seconds=1)
        with self.assertRaises(immutability.PhysicalArvanImmutabilityPreflightError):
            immutability.require_verified_physical_arvan_immutability_preflight(
                forged, binding=binding(), now=NOW
            )


if __name__ == "__main__":
    unittest.main()
