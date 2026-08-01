"""No-network tests for the one-role credential/factory artifact split."""

from __future__ import annotations

import unittest
from unittest import mock

from core import physical_arvan_s3_fi_publisher_role_factory as fi_publisher_factory
from core import physical_arvan_s3_fi_receiver_failback_role_factory as fi_receiver_factory
from core import physical_arvan_s3_ir_publisher_failback_role_factory as ir_publisher_factory
from core import physical_arvan_s3_ir_receiver_role_loader as ir_receiver_loader
from core import physical_arvan_s3_role_profiles as profiles
from core import physical_arvan_s3_role_local_credential_reader as credential_reader
from core.physical_arvan_s3_role_local_identity import (
    PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA,
)
from core.physical_arvan_s3_role_local_route_policy import ArvanS3RoleLocalRoutePolicy
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
    PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
)


ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
REGION = "ir-thr-at1"


def _credential(*, identity: str):
    return credential_reader.ArvanS3RoleLocalCredentialFacts(
        access_key="access-" + identity[:8],
        secret_key="secret-" + identity[:8],
        identity_sha256=identity,
        device=1,
        inode=2,
    )


class PhysicalArvanS3RoleLocalArtifactsTests(unittest.TestCase):
    def _normal(self):
        return ArvanS3RoleLocalRoutePolicy(
            endpoint=ENDPOINT,
            region=REGION,
            bucket="private-physical-recovery",
            enabled=True,
            source_site="webapp_fi",
            destination_site="webapp_ir",
            object_storage_namespace=PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
        )

    def _reverse(self):
        return ArvanS3RoleLocalRoutePolicy(
            endpoint=ENDPOINT,
            region=REGION,
            bucket="private-physical-failback",
            enabled=True,
            source_site="webapp_ir",
            destination_site="webapp_fi",
            object_storage_namespace=PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
        )

    def test_normal_fi_factory_has_no_receiver_surface_and_opens_only_fi_secret(self) -> None:
        owner = fi_publisher_factory.RootOwnedArvanS3FiPublisherRoleFactory(
            fi_publisher_factory.RootOwnedArvanS3FiPublisherRoleFactoryConfig(
                route_policy=self._normal(),
                enabled=True,
            )
        )
        route = credential_reader.ArvanS3RoleLocalRouteFacts(
            endpoint=ENDPOINT,
            region=REGION,
            bucket="private-physical-recovery",
        )
        with (
            mock.patch.object(fi_publisher_factory._client_support, "require_role_local_root"),
            mock.patch.object(
                fi_publisher_factory._credential_reader,
                "load_root_owned_arvan_s3_role_local_credential",
                return_value=(route, _credential(identity="a" * 64)),
            ) as fi_load,
        ):
            projection = owner.identity_projection()
        fi_load.assert_called_once()
        self.assertEqual(
            fi_publisher_factory.FIXED_ARVAN_S3_FI_PUBLISHER_ROLE_CREDENTIAL_FILE,
            fi_load.call_args.kwargs["fixed_credential_file"],
        )
        self.assertEqual(profiles.ARVAN_S3_FI_PUBLISHER_ROLE, fi_load.call_args.kwargs["expected_role"])
        self.assertFalse(hasattr(owner, "ir_receiver_identity_projection"))
        self.assertEqual(PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA, projection.schema)
        self.assertEqual(profiles.ARVAN_S3_FI_PUBLISHER_ROLE, projection.role)

    def test_normal_ir_loader_has_no_publisher_surface_and_opens_only_ir_secret(self) -> None:
        owner = ir_receiver_loader.RootOwnedArvanS3IrReceiverRoleLoader(
            ir_receiver_loader.RootOwnedArvanS3IrReceiverRoleLoaderConfig(
                route_policy=self._normal()
            )
        )
        route = credential_reader.ArvanS3RoleLocalRouteFacts(
            endpoint=ENDPOINT,
            region=REGION,
            bucket="private-physical-recovery",
        )
        with (
            mock.patch.object(
                ir_receiver_loader._reader,
                "load_root_owned_arvan_s3_role_local_credential",
                return_value=(route, _credential(identity="b" * 64)),
            ) as ir_load,
        ):
            projection = owner.identity_projection()
        ir_load.assert_called_once()
        self.assertEqual(
            ir_receiver_loader.FIXED_ARVAN_S3_IR_RECEIVER_ROLE_CREDENTIAL_FILE,
            ir_load.call_args.kwargs["fixed_credential_file"],
        )
        self.assertEqual(profiles.ARVAN_S3_IR_RECEIVER_ROLE, ir_load.call_args.kwargs["expected_role"])
        self.assertFalse(hasattr(owner, "load_fi_publisher_credential_facts"))
        self.assertEqual(profiles.ARVAN_S3_IR_RECEIVER_ROLE, projection.role)

    def test_reverse_ir_factory_collects_only_ir_secret_without_preflight_circularity(self) -> None:
        owner = ir_publisher_factory.RootOwnedArvanS3IrPublisherFailbackRoleFactory(
            ir_publisher_factory.RootOwnedArvanS3IrPublisherFailbackRoleFactoryConfig(
                route_policy=self._reverse(),
                enabled=True,
            )
        )
        route = credential_reader.ArvanS3RoleLocalRouteFacts(
            endpoint=ENDPOINT,
            region=REGION,
            bucket="private-physical-failback",
        )
        credential = credential_reader.ArvanS3RoleLocalCredentialFacts(
            access_key="reverse-ir-access",
            secret_key="reverse-ir-secret",
            identity_sha256="c" * 64,
            device=3,
            inode=4,
        )
        with (
            mock.patch.object(ir_publisher_factory._client_support, "require_role_local_root"),
            mock.patch.object(
                ir_publisher_factory._credential_reader,
                "load_root_owned_arvan_s3_role_local_credential",
                return_value=(route, credential),
            ) as ir_load,
        ):
            projection = owner.identity_projection()
        ir_load.assert_called_once()
        self.assertEqual(
            ir_publisher_factory.FIXED_ARVAN_S3_IR_PUBLISHER_ROLE_CREDENTIAL_FILE,
            ir_load.call_args.kwargs["fixed_credential_file"],
        )
        self.assertEqual(profiles.ARVAN_S3_IR_PUBLISHER_ROLE, ir_load.call_args.kwargs["expected_role"])
        self.assertFalse(hasattr(owner, "fi_receiver_identity_projection"))
        self.assertEqual(profiles.ARVAN_S3_IR_PUBLISHER_ROLE, projection.role)

    def test_reverse_fi_factory_collects_only_fi_secret_without_preflight_circularity(self) -> None:
        owner = fi_receiver_factory.RootOwnedArvanS3FiReceiverFailbackRoleFactory(
            fi_receiver_factory.RootOwnedArvanS3FiReceiverFailbackRoleFactoryConfig(
                route_policy=self._reverse(),
                enabled=True,
            )
        )
        route = credential_reader.ArvanS3RoleLocalRouteFacts(
            endpoint=ENDPOINT,
            region=REGION,
            bucket="private-physical-failback",
        )
        credential = credential_reader.ArvanS3RoleLocalCredentialFacts(
            access_key="reverse-fi-access",
            secret_key="reverse-fi-secret",
            identity_sha256="d" * 64,
            device=5,
            inode=6,
        )
        with (
            mock.patch.object(fi_receiver_factory._client_support, "require_role_local_root"),
            mock.patch.object(
                fi_receiver_factory._credential_reader,
                "load_root_owned_arvan_s3_role_local_credential",
                return_value=(route, credential),
            ) as fi_load,
        ):
            projection = owner.identity_projection()
        fi_load.assert_called_once()
        self.assertEqual(
            fi_receiver_factory.FIXED_ARVAN_S3_FI_RECEIVER_ROLE_CREDENTIAL_FILE,
            fi_load.call_args.kwargs["fixed_credential_file"],
        )
        self.assertEqual(profiles.ARVAN_S3_FI_RECEIVER_ROLE, fi_load.call_args.kwargs["expected_role"])
        self.assertFalse(hasattr(owner, "ir_publisher_identity_projection"))
        self.assertEqual(profiles.ARVAN_S3_FI_RECEIVER_ROLE, projection.role)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
