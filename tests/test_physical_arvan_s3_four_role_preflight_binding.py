"""Pure tests for exact four-role profile/route compatibility binding."""

from __future__ import annotations

from dataclasses import replace
import pickle
import unittest

from core import physical_arvan_s3_failback_separated_client_factory as reverse_factory
from core import physical_arvan_s3_failback_separated_credential_loader as reverse_credentials
from core import physical_arvan_s3_four_role_preflight_binding as binder
from core import physical_arvan_s3_role_profiles as profiles
from core import physical_arvan_s3_separated_client_factory as normal_factory
from core import physical_arvan_s3_separated_credential_loader as normal_credentials
from core.physical_arvan_s3_role_local_identity import (
    PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA,
    ArvanS3RoleLocalIdentityProjection,
)
from core.physical_arvan_s3_role_local_route_policy import ArvanS3RoleLocalRoutePolicy
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
    PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
)


ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
REGION = "ir-thr-at1"
NORMAL_BUCKET = "private-physical-recovery"
REVERSE_BUCKET = "private-physical-failback"
CAMPAIGN = "four-role-binder-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"


def _sha(character: str) -> str:
    return character * 64


class PhysicalArvanS3FourRolePreflightBindingTests(unittest.TestCase):
    def _normal_route_policy(self, **changes: object):
        values: dict[str, object] = {
            "endpoint": ENDPOINT,
            "region": REGION,
            "bucket": NORMAL_BUCKET,
            "enabled": True,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "object_storage_namespace": PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
        }
        values.update(changes)
        return ArvanS3RoleLocalRoutePolicy(**values)

    def _reverse_route_policy(self, **changes: object):
        values: dict[str, object] = {
            "endpoint": ENDPOINT,
            "region": REGION,
            "bucket": REVERSE_BUCKET,
            "enabled": True,
            "source_site": "webapp_ir",
            "destination_site": "webapp_fi",
            "object_storage_namespace": PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
        }
        values.update(changes)
        return ArvanS3RoleLocalRoutePolicy(**values)

    def _normal_projection(self, *, role: str, identity: str, **changes: object):
        profiles_by_role = {
            profiles.ARVAN_S3_FI_PUBLISHER_ROLE: (
                profiles.ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
                profiles.ARVAN_S3_FI_PUBLISHER_EXPECTED_ACTIONS,
            ),
            profiles.ARVAN_S3_IR_RECEIVER_ROLE: (
                profiles.ARVAN_S3_IR_RECEIVER_EXACT_READONLY_PROFILE,
                profiles.ARVAN_S3_IR_RECEIVER_EXPECTED_ACTIONS,
            ),
        }
        action_profile, allowed_operations = profiles_by_role[role]
        values: dict[str, object] = {
            "schema": PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA,
            "role": role,
            "identity_sha256": identity,
            "action_profile": action_profile,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "object_storage_namespace": PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
            "allowed_operations": allowed_operations,
        }
        values.update(changes)
        return ArvanS3RoleLocalIdentityProjection(**values)

    def _reverse_projection(self, *, role: str, identity: str, **changes: object):
        profiles_by_role = {
            profiles.ARVAN_S3_IR_PUBLISHER_ROLE: (
                profiles.ARVAN_S3_IR_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
                profiles.ARVAN_S3_IR_PUBLISHER_EXPECTED_ACTIONS,
            ),
            profiles.ARVAN_S3_FI_RECEIVER_ROLE: (
                profiles.ARVAN_S3_FI_RECEIVER_EXACT_READONLY_PROFILE,
                profiles.ARVAN_S3_FI_RECEIVER_EXPECTED_ACTIONS,
            ),
        }
        action_profile, allowed_operations = profiles_by_role[role]
        values: dict[str, object] = {
            "schema": PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA,
            "role": role,
            "identity_sha256": identity,
            "action_profile": action_profile,
            "source_site": "webapp_ir",
            "destination_site": "webapp_fi",
            "object_storage_namespace": PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
            "allowed_operations": allowed_operations,
        }
        values.update(changes)
        return ArvanS3RoleLocalIdentityProjection(**values)

    def _binding(
        self,
        *,
        fi_publisher=None,
        ir_receiver=None,
        ir_publisher=None,
        fi_receiver=None,
        **changes: object,
    ):
        fi_publisher = fi_publisher or self._normal_projection(
            role=profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
            identity=_sha("a"),
        )
        ir_receiver = ir_receiver or self._normal_projection(
            role=profiles.ARVAN_S3_IR_RECEIVER_ROLE,
            identity=_sha("b"),
        )
        ir_publisher = ir_publisher or self._reverse_projection(
            role=profiles.ARVAN_S3_IR_PUBLISHER_ROLE,
            identity=_sha("c"),
        )
        fi_receiver = fi_receiver or self._reverse_projection(
            role=profiles.ARVAN_S3_FI_RECEIVER_ROLE,
            identity=_sha("d"),
        )
        values: dict[str, object] = {
            "campaign_id": CAMPAIGN,
            "release_sha": RELEASE,
            "fi_publisher_identity_sha256": fi_publisher.identity_sha256,
            "ir_receiver_identity_sha256": ir_receiver.identity_sha256,
            "ir_publisher_identity_sha256": ir_publisher.identity_sha256,
            "fi_receiver_identity_sha256": fi_receiver.identity_sha256,
            "normal_route_policy": self._normal_route_policy(),
            "reverse_route_policy": self._reverse_route_policy(),
        }
        values.update(changes)
        return binder.derive_physical_ir_to_fi_object_storage_failback_binding(**values)

    def _verified(self, **changes: object):
        fi_publisher = changes.pop(
            "fi_publisher",
            self._normal_projection(role=profiles.ARVAN_S3_FI_PUBLISHER_ROLE, identity=_sha("a")),
        )
        ir_receiver = changes.pop(
            "ir_receiver",
            self._normal_projection(role=profiles.ARVAN_S3_IR_RECEIVER_ROLE, identity=_sha("b")),
        )
        ir_publisher = changes.pop(
            "ir_publisher",
            self._reverse_projection(role=profiles.ARVAN_S3_IR_PUBLISHER_ROLE, identity=_sha("c")),
        )
        fi_receiver = changes.pop(
            "fi_receiver",
            self._reverse_projection(role=profiles.ARVAN_S3_FI_RECEIVER_ROLE, identity=_sha("d")),
        )
        normal_route_policy = changes.pop("normal_route_policy", self._normal_route_policy())
        reverse_route_policy = changes.pop("reverse_route_policy", self._reverse_route_policy())
        selected = changes.pop(
            "binding",
            self._binding(
                fi_publisher=fi_publisher,
                ir_receiver=ir_receiver,
                ir_publisher=ir_publisher,
                fi_receiver=fi_receiver,
                normal_route_policy=normal_route_policy,
                reverse_route_policy=reverse_route_policy,
            ),
        )
        self.assertFalse(changes)
        return binder.bind_physical_arvan_s3_four_role_preflight(
            binding=selected,
            normal_route_policy=normal_route_policy,
            reverse_route_policy=reverse_route_policy,
            fi_publisher_projection=fi_publisher,
            ir_receiver_projection=ir_receiver,
            ir_publisher_projection=ir_publisher,
            fi_receiver_projection=fi_receiver,
        )

    def test_derives_exact_profiles_and_consumable_opaque_binding(self) -> None:
        verified = self._verified()
        self.assertIs(
            verified,
            binder.require_verified_physical_arvan_s3_four_role_preflight_binding(
                verified,
                binding=verified.binding,
            ),
        )
        self.assertEqual(
            profiles.ARVAN_S3_FOUR_ROLE_IDENTITY_PROFILES,
            (
                ("fi_publisher", "fi-publisher-immutable-create-only-v1"),
                ("ir_receiver", "ir-receiver-exact-readonly-v1"),
                ("ir_publisher", "ir-publisher-immutable-create-only-v1"),
                ("fi_receiver", "fi-receiver-exact-readonly-v1"),
            ),
        )
        with self.assertRaises(TypeError):
            pickle.dumps(verified)

    def test_legacy_profile_never_aliases_to_canonical_profile(self) -> None:
        legacy_projection = self._normal_projection(
            role=profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
            identity=_sha("a"),
            action_profile=profiles.ARVAN_S3_LEGACY_FI_PUBLISHER_IMMUTABLE_PREFLIGHT_PROFILE,
        )
        with self.assertRaisesRegex(
            binder.PhysicalArvanS3FourRolePreflightBindingError,
            "NORMAL_PROFILE_INVALID",
        ):
            self._verified(fi_publisher=legacy_projection)
        with self.assertRaisesRegex(
            binder.PhysicalArvanS3FourRolePreflightBindingError,
            "NORMAL_ROUTE_POLICY_INVALID",
        ):
            self._verified(
                normal_route_policy=normal_credentials.RootOwnedArvanS3SeparatedCredentialLoaderConfig(
                    endpoint=ENDPOINT,
                    region=REGION,
                    bucket=NORMAL_BUCKET,
                    enabled=True,
                )
            )
        with self.assertRaisesRegex(
            binder.PhysicalArvanS3FourRolePreflightBindingError,
            "REVERSE_ROUTE_POLICY_INVALID",
        ):
            self._verified(
                reverse_route_policy=reverse_credentials.RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig(
                    endpoint=ENDPOINT,
                    region=REGION,
                    bucket=REVERSE_BUCKET,
                    enabled=True,
                )
            )

    def test_route_policies_are_exact_directional_and_enabled(self) -> None:
        with self.assertRaisesRegex(
            binder.PhysicalArvanS3FourRolePreflightBindingError,
            "NORMAL_ROUTE_POLICY_INVALID",
        ):
            self._verified(normal_route_policy=self._normal_route_policy(destination_site="webapp_fi"))
        with self.assertRaisesRegex(
            binder.PhysicalArvanS3FourRolePreflightBindingError,
            "REVERSE_ROUTE_POLICY_DISABLED",
        ):
            self._verified(reverse_route_policy=self._reverse_route_policy(enabled=False))

    def test_operation_surfaces_are_exact_including_reverse_bucket_reads(self) -> None:
        self.assertIn("GetBucketAcl", profiles.ARVAN_S3_IR_PUBLISHER_EXPECTED_ACTIONS)
        self.assertIn("GetBucketVersioning", profiles.ARVAN_S3_IR_PUBLISHER_EXPECTED_ACTIONS)
        bad_ir = self._reverse_projection(
            role=profiles.ARVAN_S3_IR_PUBLISHER_ROLE,
            identity=_sha("c"),
            allowed_operations=("PutObject:create-only",),
        )
        with self.assertRaisesRegex(
            binder.PhysicalArvanS3FourRolePreflightBindingError,
            "REVERSE_PROJECTION_INVALID",
        ):
            self._verified(ir_publisher=bad_ir)
        bad_normal = self._normal_projection(
            role=profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
            identity=_sha("a"),
            allowed_operations=("PutObject:create-only",),
        )
        with self.assertRaisesRegex(
            binder.PhysicalArvanS3FourRolePreflightBindingError,
            "NORMAL_PROJECTION_INVALID",
        ):
            self._verified(fi_publisher=bad_normal)

    def test_legacy_dual_role_factory_projection_types_are_fenced(self) -> None:
        legacy_normal = normal_factory.ArvanS3NormalLocalIdentityProjection(
            schema=normal_factory.ARVAN_S3_SEPARATED_CLIENT_FACTORY_SCHEMA,
            role=profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
            identity_sha256=_sha("a"),
            action_profile=profiles.ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
            source_site="webapp_fi",
            destination_site="webapp_ir",
            object_storage_namespace="physical-wal",
            allowed_operations=profiles.ARVAN_S3_FI_PUBLISHER_EXPECTED_ACTIONS,
        )
        with self.assertRaisesRegex(
            binder.PhysicalArvanS3FourRolePreflightBindingError,
            "NORMAL_PROJECTION_INVALID",
        ):
            self._verified(fi_publisher=legacy_normal)

        legacy_reverse = reverse_factory.ArvanS3FailbackLocalIdentityProjection(
            schema=reverse_factory.ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_SCHEMA,
            role=profiles.ARVAN_S3_IR_PUBLISHER_ROLE,
            identity_sha256=_sha("c"),
            action_profile=profiles.ARVAN_S3_IR_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
            source_site="webapp_ir",
            destination_site="webapp_fi",
            object_storage_namespace="physical-failback",
            allowed_operations=profiles.ARVAN_S3_IR_PUBLISHER_EXPECTED_ACTIONS,
        )
        with self.assertRaisesRegex(
            binder.PhysicalArvanS3FourRolePreflightBindingError,
            "REVERSE_PROJECTION_INVALID",
        ):
            self._verified(ir_publisher=legacy_reverse)

    def test_binding_hash_or_identity_substitution_is_rejected(self) -> None:
        selected = self._binding()
        with self.assertRaisesRegex(
            binder.PhysicalArvanS3FourRolePreflightBindingError,
            "BINDING_MISMATCH",
        ):
            self._verified(binding=replace(selected, route_binding_sha256=_sha("e")))
        forged = binder.VerifiedPhysicalArvanS3FourRolePreflightBinding(
            schema=binder.PHYSICAL_ARVAN_S3_FOUR_ROLE_PREFLIGHT_BINDING_SCHEMA,
            binding=selected,
            projection_commitment_sha256="f" * 64,
        )
        with self.assertRaisesRegex(
            binder.PhysicalArvanS3FourRolePreflightBindingError,
            "BINDING_REQUIRED",
        ):
            binder.require_verified_physical_arvan_s3_four_role_preflight_binding(
                forged,
                binding=selected,
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
