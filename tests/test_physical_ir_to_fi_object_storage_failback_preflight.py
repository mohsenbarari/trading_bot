from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pickle
import unittest

from core import physical_ir_to_fi_object_storage_failback_preflight as preflight
from tests.physical_arvan_s3_four_role_fixture import make_four_role_fixture
from tests.physical_arvan_s3_four_role_live_iam_fixture import (
    make_four_role_live_iam_durable_admission_fixture,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
RELEASE_SHA = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"


def sha(character: str) -> str:
    return character * 64


def binding(**changes: object) -> preflight.PhysicalIrToFiObjectStorageFailbackBinding:
    selected = make_four_role_fixture(
        campaign_id="ir-fi-failback-20260731",
        release_sha=RELEASE_SHA,
        fi_publisher_identity_sha256=sha("a"),
        ir_receiver_identity_sha256=sha("b"),
        ir_publisher_identity_sha256=sha("c"),
        fi_receiver_identity_sha256=sha("d"),
    ).binding
    return replace(selected, **changes)


def compatible_fixture():
    return make_four_role_fixture(
        campaign_id="ir-fi-failback-20260731",
        release_sha=RELEASE_SHA,
        fi_publisher_identity_sha256=sha("a"),
        ir_receiver_identity_sha256=sha("b"),
        ir_publisher_identity_sha256=sha("c"),
        fi_receiver_identity_sha256=sha("d"),
    )


class PhysicalIrToFiObjectStorageFailbackPreflightTests(unittest.TestCase):
    def verified(
        self,
        *,
        candidate: preflight.PhysicalIrToFiObjectStorageFailbackBinding | None = None,
        observed_at: datetime = NOW,
    ) -> preflight.VerifiedPhysicalIrToFiObjectStorageFailbackPreflight:
        fixture = compatible_fixture()
        selected = candidate or fixture.binding
        preflight.validate_physical_ir_to_fi_object_storage_failback_binding(selected)
        live = make_four_role_live_iam_durable_admission_fixture(
            binding=selected,
            observed_at=observed_at,
        )
        observation = preflight.build_physical_ir_to_fi_object_storage_failback_observation(
            binding=selected,
            four_role_projection_binding=fixture.verified_binding,
            four_role_live_iam_binding=live.live_iam_binding,
            four_role_live_iam_durable_admission=live.live_iam_durable_admission,
            observed_at=observed_at,
        )
        return preflight.verify_physical_ir_to_fi_object_storage_failback_preflight(
            observation,
            binding=selected,
            four_role_projection_binding=fixture.verified_binding,
            four_role_live_iam_binding=live.live_iam_binding,
            four_role_live_iam_durable_admission=live.live_iam_durable_admission,
            now=NOW,
        )

    def test_verifies_exact_four_role_failback_binding_and_is_nonserializable(self) -> None:
        fixture = compatible_fixture()
        selected = fixture.binding
        live = make_four_role_live_iam_durable_admission_fixture(binding=selected, observed_at=NOW)
        observation = preflight.build_physical_ir_to_fi_object_storage_failback_observation(
            binding=selected,
            four_role_projection_binding=fixture.verified_binding,
            four_role_live_iam_binding=live.live_iam_binding,
            four_role_live_iam_durable_admission=live.live_iam_durable_admission,
            observed_at=NOW,
        )
        verified = preflight.verify_physical_ir_to_fi_object_storage_failback_preflight(
            observation,
            binding=selected,
            four_role_projection_binding=fixture.verified_binding,
            four_role_live_iam_binding=live.live_iam_binding,
            four_role_live_iam_durable_admission=live.live_iam_durable_admission,
            now=NOW,
        )
        config = fixture.preflight_config(
            four_role_live_iam_binding=live.live_iam_binding,
            four_role_live_iam_durable_admission=live.live_iam_durable_admission,
        )

        required = preflight.require_verified_physical_ir_to_fi_object_storage_failback_preflight(
            verified,
            config=config,
            now=NOW + timedelta(seconds=30),
        )

        self.assertIs(verified, required)
        self.assertEqual("physical-failback", verified.binding.object_storage_namespace)
        self.assertEqual("webapp_ir", verified.observation.source_site)
        self.assertEqual("webapp_fi", verified.observation.destination_site)
        self.assertEqual(
            (
                ("fi_publisher", "fi-publisher-immutable-create-only-v1"),
                ("ir_receiver", "ir-receiver-exact-readonly-v1"),
                ("ir_publisher", "ir-publisher-immutable-create-only-v1"),
                ("fi_receiver", "fi-receiver-exact-readonly-v1"),
            ),
            verified.observation.identity_profiles,
        )
        with self.assertRaises(TypeError):
            pickle.dumps(verified)

    def test_collision_or_wrong_namespace_never_verifies(self) -> None:
        cases = (
            binding(reverse_route_scope_sha256=sha("b")),
            binding(fi_receiver_identity_sha256=sha("f")),
            binding(object_storage_namespace="physical-wal"),
        )
        for candidate in cases:
            with self.subTest(candidate=candidate):
                with self.assertRaises(preflight.PhysicalIrToFiObjectStorageFailbackPreflightError):
                    self.verified(candidate=candidate)

    def test_tampered_or_stale_observation_fails_closed(self) -> None:
        fixture = compatible_fixture()
        selected = fixture.binding
        live = make_four_role_live_iam_durable_admission_fixture(binding=selected, observed_at=NOW)
        observation = preflight.build_physical_ir_to_fi_object_storage_failback_observation(
            binding=selected,
            four_role_projection_binding=fixture.verified_binding,
            four_role_live_iam_binding=live.live_iam_binding,
            four_role_live_iam_durable_admission=live.live_iam_durable_admission,
            observed_at=NOW,
        )
        self.assertEqual(
            live.live_iam_durable_admission.gate.aggregate_sha256,
            observation.provider_preflight_evidence_sha256,
        )
        with self.assertRaisesRegex(
            preflight.PhysicalIrToFiObjectStorageFailbackPreflightError,
            "TAMPERED",
        ):
            preflight.verify_physical_ir_to_fi_object_storage_failback_preflight(
                replace(observation, evidence_sha256=sha("3")),
                binding=selected,
                four_role_projection_binding=fixture.verified_binding,
                four_role_live_iam_binding=live.live_iam_binding,
                four_role_live_iam_durable_admission=live.live_iam_durable_admission,
                now=NOW,
            )
        stale = preflight.build_physical_ir_to_fi_object_storage_failback_observation(
            binding=selected,
            four_role_projection_binding=fixture.verified_binding,
            four_role_live_iam_binding=live.live_iam_binding,
            four_role_live_iam_durable_admission=live.live_iam_durable_admission,
            observed_at=NOW - timedelta(seconds=121),
        )
        with self.assertRaisesRegex(
            preflight.PhysicalIrToFiObjectStorageFailbackPreflightError,
            "STALE",
        ):
            preflight.verify_physical_ir_to_fi_object_storage_failback_preflight(
                stale,
                binding=selected,
                four_role_projection_binding=fixture.verified_binding,
                four_role_live_iam_binding=live.live_iam_binding,
                four_role_live_iam_durable_admission=live.live_iam_durable_admission,
                now=NOW,
            )

    def test_disabled_policy_cannot_consume_a_verified_preflight(self) -> None:
        selected = binding()
        with self.assertRaisesRegex(
            preflight.PhysicalIrToFiObjectStorageFailbackPreflightError,
            "DISABLED",
        ):
            preflight.require_verified_physical_ir_to_fi_object_storage_failback_preflight(
                self.verified(candidate=selected),
                config=preflight.PhysicalIrToFiObjectStorageFailbackPreflightConfig(
                    binding=selected
                ),
                now=NOW,
            )

    def test_enabled_config_requires_current_verified_live_iam_durable_admission(self) -> None:
        fixture = compatible_fixture()
        selected = fixture.binding
        verified = self.verified(candidate=selected)
        with self.assertRaisesRegex(
            preflight.PhysicalIrToFiObjectStorageFailbackPreflightError,
            "LIVE_IAM_DURABLE_ADMISSION_REQUIRED",
        ):
            preflight.require_verified_physical_ir_to_fi_object_storage_failback_preflight(
                verified,
                config=fixture.preflight_config(),
                now=NOW,
            )

    def test_raw_gate_cannot_substitute_for_durable_admission(self) -> None:
        fixture = compatible_fixture()
        selected = fixture.binding
        live = make_four_role_live_iam_durable_admission_fixture(
            binding=selected,
            observed_at=NOW,
        )
        verified = self.verified(candidate=selected)
        with self.assertRaisesRegex(
            preflight.PhysicalIrToFiObjectStorageFailbackPreflightError,
            "LIVE_IAM_DURABLE_ADMISSION_REQUIRED",
        ):
            preflight.require_verified_physical_ir_to_fi_object_storage_failback_preflight(
                verified,
                config=fixture.preflight_config(
                    four_role_live_iam_binding=live.live_iam_binding,
                    four_role_live_iam_durable_admission=live.live_iam_durable_admission.gate,
                ),
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
