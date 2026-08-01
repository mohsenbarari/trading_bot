"""Fake-S3 tests for the bounded four-role immutability collector.

No test imports boto3, opens a credential file, creates an S3 client, or makes
a socket/provider request.  The fake records the exact S3-shaped operation
sequence so capability and selector boundaries remain reviewable.
"""

from __future__ import annotations

import ast
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from unittest import mock
import unittest

from core import physical_arvan_s3_four_role_immutability_live_probe_runtime as runtime
from core import physical_arvan_s3_four_role_immutability_role_local_collector as collector
from core import physical_arvan_s3_four_role_immutability_preflight as immutable
from core import physical_arvan_s3_role_local_credential_reader as credential_reader
from core import physical_arvan_s3_role_profiles as profiles
from core import physical_ir_to_fi_object_storage_failback_preflight as failback
from core.physical_arvan_s3_role_local_route_policy import ArvanS3RoleLocalRoutePolicy
from tests.physical_arvan_s3_four_role_live_iam_fixture import (
    make_four_role_live_iam_durable_admission_fixture,
)


CAMPAIGN = "four-role-collector-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
REGION = "ir-thr-at1"
BUCKET = "private-four-role-collector"
NOW = datetime(2026, 7, 31, 21, 0, 0, tzinfo=timezone.utc)
RETENTION_DAYS = 90
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_arvan_s3_four_role_immutability_role_local_collector.py"
)


_ROLE_FACTS = {
    profiles.ARVAN_S3_FI_PUBLISHER_ROLE: (
        "fi-publisher-to-ir-receiver",
        "webapp_fi",
        "webapp_ir",
        "physical-wal",
    ),
    profiles.ARVAN_S3_IR_RECEIVER_ROLE: (
        "fi-publisher-to-ir-receiver",
        "webapp_fi",
        "webapp_ir",
        "physical-wal",
    ),
    profiles.ARVAN_S3_IR_PUBLISHER_ROLE: (
        "ir-publisher-to-fi-receiver",
        "webapp_ir",
        "webapp_fi",
        "physical-failback",
    ),
    profiles.ARVAN_S3_FI_RECEIVER_ROLE: (
        "ir-publisher-to-fi-receiver",
        "webapp_ir",
        "webapp_fi",
        "physical-failback",
    ),
}
_IDENTITIES = {
    profiles.ARVAN_S3_FI_PUBLISHER_ROLE: "a" * 64,
    profiles.ARVAN_S3_IR_RECEIVER_ROLE: "b" * 64,
    profiles.ARVAN_S3_IR_PUBLISHER_ROLE: "c" * 64,
    profiles.ARVAN_S3_FI_RECEIVER_ROLE: "d" * 64,
}


class _S3Error(Exception):
    def __init__(self, code: str) -> None:
        super().__init__("redacted")
        self.response = {"Error": {"Code": code}}


class _Body:
    def __init__(self, value: bytes) -> None:
        self._value = value
        self._offset = 0
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._value) - self._offset
        result = self._value[self._offset : self._offset + size]
        self._offset += len(result)
        return result

    def close(self) -> None:
        self.closed = True


class _FakeS3:
    """Stateful fake with no fallback or generic provider surface."""

    def __init__(
        self,
        *,
        role: str,
        payload: bytes | None = None,
        key: str | None = None,
        version_id: str = "version-immutable-001",
        retention_until: datetime | None = None,
        object_lock_days: int = RETENTION_DAYS,
        accept_denied_operation: str | None = None,
        error_denied_operation: str | None = None,
    ) -> None:
        self.role = role
        self.payload = payload
        self.key = key
        self.version_id = version_id
        self.retention_until = retention_until or (NOW + timedelta(days=RETENTION_DAYS))
        self.object_lock_days = object_lock_days
        self.accept_denied_operation = accept_denied_operation
        self.error_denied_operation = error_denied_operation
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    @property
    def _checksum_b64(self) -> str:
        if self.payload is None:
            raise AssertionError("fake object is not initialized")
        return base64.b64encode(hashlib.sha256(self.payload).digest()).decode("ascii")

    def _record(self, operation: str, kwargs: dict[str, object]) -> None:
        self.calls.append((operation, dict(kwargs)))

    def _deny(self, operation: str) -> None:
        if self.accept_denied_operation == operation:
            return
        if self.error_denied_operation == operation:
            raise _S3Error("InternalError")
        raise _S3Error("AccessDenied")

    def get_bucket_acl(self, **kwargs: object) -> dict[str, object]:
        self._record("get_bucket_acl", kwargs)
        return {
            "Owner": {"ID": "owner-canonical-id"},
            "Grants": [
                {
                    "Grantee": {"Type": "CanonicalUser", "ID": "owner-canonical-id"},
                    "Permission": "FULL_CONTROL",
                }
            ],
        }

    def get_bucket_versioning(self, **kwargs: object) -> dict[str, object]:
        self._record("get_bucket_versioning", kwargs)
        return {"Status": "Enabled"}

    def get_object_lock_configuration(self, **kwargs: object) -> dict[str, object]:
        self._record("get_object_lock_configuration", kwargs)
        return {
            "ObjectLockConfiguration": {
                "ObjectLockEnabled": "Enabled",
                "Rule": {
                    "DefaultRetention": {
                        "Mode": "COMPLIANCE",
                        "Days": self.object_lock_days,
                    }
                },
            }
        }

    def put_object(self, **kwargs: object) -> dict[str, object]:
        self._record("put_object", kwargs)
        if "IfNoneMatch" not in kwargs:
            self._deny("put_object")
            return {}
        body = kwargs["Body"]
        if type(body) is not bytes:
            raise AssertionError("collector must use byte payload")
        self.payload = body
        self.key = str(kwargs["Key"])
        retain = kwargs["ObjectLockRetainUntilDate"]
        if not isinstance(retain, datetime):
            raise AssertionError("missing immutable retention")
        self.retention_until = retain
        return {"VersionId": self.version_id}

    def list_object_versions(self, **kwargs: object) -> dict[str, object]:
        self._record("list_object_versions", kwargs)
        if self.role in {
            profiles.ARVAN_S3_IR_RECEIVER_ROLE,
            profiles.ARVAN_S3_FI_RECEIVER_ROLE,
        }:
            self._deny("list_object_versions")
            return {}
        if self.payload is None or self.key is None:
            raise AssertionError("publisher listed before create")
        return {
            "IsTruncated": False,
            "Versions": [
                {
                    "Key": self.key,
                    "VersionId": self.version_id,
                    "IsLatest": True,
                    "Size": len(self.payload),
                }
            ],
            "DeleteMarkers": [],
        }

    def get_object_retention(self, **kwargs: object) -> dict[str, object]:
        self._record("get_object_retention", kwargs)
        return {"Retention": {"Mode": "COMPLIANCE", "RetainUntilDate": self.retention_until}}

    def _metadata(self) -> dict[str, object]:
        if self.payload is None:
            raise AssertionError("fake object is not initialized")
        return {
            "VersionId": self.version_id,
            "ContentLength": len(self.payload),
            "ContentType": "application/octet-stream",
            "CacheControl": "no-store",
            "ChecksumSHA256": self._checksum_b64,
            "ObjectLockMode": "COMPLIANCE",
            "ObjectLockRetainUntilDate": self.retention_until,
        }

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self._record("get_object", kwargs)
        if self.payload is None:
            raise AssertionError("fake object is not initialized")
        result = self._metadata()
        result.update(
            {
                "ContentRange": f"bytes 0-{len(self.payload) - 1}/{len(self.payload)}",
                "AcceptRanges": "bytes",
                "Body": _Body(self.payload),
            }
        )
        return result

    def head_object(self, **kwargs: object) -> dict[str, object]:
        self._record("head_object", kwargs)
        return self._metadata()

    def delete_object(self, **kwargs: object) -> dict[str, object]:
        self._record("delete_object", kwargs)
        self._deny("delete_object")
        return {}

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        self._record("list_objects_v2", kwargs)
        self._deny("list_objects_v2")
        return {}

    def close(self) -> None:
        self.closed = True


def _profile_for(role: str) -> str:
    return {
        profiles.ARVAN_S3_FI_PUBLISHER_ROLE: profiles.ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
        profiles.ARVAN_S3_IR_RECEIVER_ROLE: profiles.ARVAN_S3_IR_RECEIVER_EXACT_READONLY_PROFILE,
        profiles.ARVAN_S3_IR_PUBLISHER_ROLE: profiles.ARVAN_S3_IR_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
        profiles.ARVAN_S3_FI_RECEIVER_ROLE: profiles.ARVAN_S3_FI_RECEIVER_EXACT_READONLY_PROFILE,
    }[role]


def _credential(identity: str) -> credential_reader.ArvanS3RoleLocalCredentialFacts:
    return credential_reader.ArvanS3RoleLocalCredentialFacts(
        access_key="access-key-" + identity[:8],
        secret_key="secret-key-" + identity[:8],
        identity_sha256=identity,
        device=1,
        inode=2,
    )


class PhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorTests(unittest.TestCase):
    def _facts(self, role: str) -> tuple[str, str, str, str]:
        return _ROLE_FACTS[role]

    def _config(
        self,
        role: str,
        *,
        enabled: bool = True,
        retention_days: int = RETENTION_DAYS,
    ) -> collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorConfig:
        _direction, source, destination, namespace = self._facts(role)
        return collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorConfig(
            role=role,
            route_policy=ArvanS3RoleLocalRoutePolicy(
                endpoint=ENDPOINT,
                region=REGION,
                bucket=BUCKET,
                enabled=enabled,
                source_site=source,
                destination_site=destination,
                object_storage_namespace=namespace,
            ),
            retention_days=retention_days,
            enabled=enabled,
        )

    def _publisher_request(
        self,
        role: str,
        *,
        nonce: str = "e" * 64,
    ) -> runtime.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest:
        direction, _source, _destination, namespace = self._facts(role)
        return runtime.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest(
            schema=runtime.PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA,
            direction=direction,
            role=role,
            identity_sha256=_IDENTITIES[role],
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            endpoint=ENDPOINT,
            region=REGION,
            bucket=BUCKET,
            object_storage_namespace=namespace,
            probe_nonce_sha256=nonce,
            object_key=(
                f"{namespace}/{CAMPAIGN}/{RELEASE}/four-role-immutability/"
                f"{direction}/{nonce}.age"
            ),
            observed_at=NOW,
            minimum_retention_days=RETENTION_DAYS,
            retention_not_before=NOW
            + timedelta(
                days=RETENTION_DAYS,
                seconds=runtime.PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_MAX_TRANSPORT_GRACE_SECONDS,
            ),
        )

    def _receiver_request(
        self,
        role: str,
        *,
        payload: bytes = b"immutable-receiver-exact-body",
        nonce: str = "f" * 64,
    ) -> runtime.PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest:
        direction, _source, _destination, namespace = self._facts(role)
        key = (
            f"{namespace}/{CAMPAIGN}/{RELEASE}/four-role-immutability/"
            f"{direction}/{nonce}.age"
        )
        version = immutable.PhysicalArvanS3FourRoleImmutableVersionObservation(
            probe_nonce_sha256=nonce,
            object_key=key,
            object_version_id="version-immutable-001",
            content_sha256=hashlib.sha256(payload).hexdigest(),
            content_bytes=len(payload),
            retention_until=NOW
            + timedelta(
                days=RETENTION_DAYS,
                seconds=runtime.PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_MAX_TRANSPORT_GRACE_SECONDS,
            ),
            exact_head_version_id="version-immutable-001",
            exact_get_version_id="version-immutable-001",
            exact_get_content_sha256=hashlib.sha256(payload).hexdigest(),
            exact_get_content_bytes=len(payload),
        )
        return runtime.PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest(
            schema=runtime.PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA,
            direction=direction,
            role=role,
            identity_sha256=_IDENTITIES[role],
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            endpoint=ENDPOINT,
            region=REGION,
            bucket=BUCKET,
            object_storage_namespace=namespace,
            immutable_version=version,
            observed_at=NOW,
            retention_not_before=NOW
            + timedelta(
                days=RETENTION_DAYS,
                seconds=runtime.PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_MAX_TRANSPORT_GRACE_SECONDS,
            ),
        )

    def _collect_with_fake(
        self,
        owner: collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector,
        role: str,
        fake: _FakeS3,
        request: object,
        *,
        random_bytes: bytes = b"p" * 384,
    ) -> object:
        route = credential_reader.ArvanS3RoleLocalRouteFacts(
            endpoint=ENDPOINT,
            region=REGION,
            bucket=BUCKET,
        )
        with (
            mock.patch.object(collector._client_support, "require_role_local_root"),
            mock.patch.object(
                collector._credential_reader,
                "load_root_owned_arvan_s3_role_local_credential",
                return_value=(route, _credential(_IDENTITIES[role])),
            ),
            mock.patch.object(
                collector._client_support,
                "load_role_local_boto_sdk",
                return_value=(object(), object()),
            ) as load_sdk,
            mock.patch.object(
                collector._client_support,
                "create_role_local_raw_s3_client",
                return_value=fake,
            ) as create_client,
            mock.patch.object(collector.secrets, "token_bytes", return_value=random_bytes),
        ):
            result = owner.collect(request)
        load_sdk.assert_called_once()
        create_client.assert_called_once()
        return result

    def test_fi_publisher_maps_bounded_probe_to_exact_semantic_readback(self) -> None:
        role = profiles.ARVAN_S3_FI_PUBLISHER_ROLE
        owner = collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector(self._config(role))
        fake = _FakeS3(role=role)
        request = self._publisher_request(role)
        result = self._collect_with_fake(owner, role, fake, request)
        self.assertIsInstance(
            result,
            runtime.PhysicalArvanS3FourRoleImmutabilityPublisherReadback,
        )
        assert isinstance(result, runtime.PhysicalArvanS3FourRoleImmutabilityPublisherReadback)
        self.assertEqual("create-only-succeeded", result.create_only_outcome)
        self.assertEqual("access-denied", result.overwrite_outcome)
        self.assertEqual("access-denied", result.object_removal_outcome)
        self.assertEqual("access-denied", result.version_removal_outcome)
        self.assertEqual(RETENTION_DAYS, result.bucket_readback.retention_days if result.bucket_readback else None)
        self.assertEqual(
            [
                "get_bucket_acl",
                "get_bucket_versioning",
                "get_object_lock_configuration",
                "put_object",
                "list_object_versions",
                "get_object_retention",
                "get_object",
                "head_object",
                "delete_object",
                "delete_object",
                "put_object",
            ],
            [name for name, _request in fake.calls],
        )
        create_request = fake.calls[3][1]
        overwrite_request = fake.calls[-1][1]
        self.assertEqual("*", create_request["IfNoneMatch"])
        self.assertNotIn("IfNoneMatch", overwrite_request)
        self.assertEqual(request.object_key, create_request["Key"])
        self.assertTrue(fake.closed)
        self.assertFalse(hasattr(result, "client"))
        self.assertFalse(hasattr(result, "access_key"))

    def test_ir_publisher_has_no_object_lock_configuration_or_retention_surface(self) -> None:
        role = profiles.ARVAN_S3_IR_PUBLISHER_ROLE
        owner = collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector(self._config(role))
        fake = _FakeS3(role=role)
        result = self._collect_with_fake(owner, role, fake, self._publisher_request(role))
        self.assertIsNone(result.bucket_readback)
        names = [name for name, _request in fake.calls]
        self.assertIn("get_bucket_acl", names)
        self.assertIn("get_bucket_versioning", names)
        self.assertNotIn("get_object_lock_configuration", names)
        self.assertNotIn("get_object_retention", names)

    def test_both_receivers_prove_only_exact_get_head_and_denied_mutation_listing(self) -> None:
        for role in (
            profiles.ARVAN_S3_IR_RECEIVER_ROLE,
            profiles.ARVAN_S3_FI_RECEIVER_ROLE,
        ):
            with self.subTest(role=role):
                request = self._receiver_request(role)
                fake = _FakeS3(
                    role=role,
                    payload=b"immutable-receiver-exact-body",
                    key=request.immutable_version.object_key,
                    retention_until=request.immutable_version.retention_until,
                )
                owner = collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector(
                    self._config(role)
                )
                result = self._collect_with_fake(owner, role, fake, request)
                self.assertEqual(request.immutable_version.object_version_id, result.exact_head_version_id)
                self.assertEqual(request.immutable_version.object_version_id, result.exact_get_version_id)
                self.assertEqual(
                    [
                        "get_object",
                        "head_object",
                        "put_object",
                        "delete_object",
                        "delete_object",
                        "list_objects_v2",
                        "list_object_versions",
                    ],
                    [name for name, _request in fake.calls],
                )
                self.assertTrue(fake.closed)

    def test_wrong_request_is_refused_before_credential_or_sdk_open(self) -> None:
        role = profiles.ARVAN_S3_FI_PUBLISHER_ROLE
        owner = collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector(self._config(role))
        wrong = replace(self._publisher_request(role), object_key="physical-wal/not-allowed.age")
        with (
            mock.patch.object(collector._client_support, "require_role_local_root"),
            mock.patch.object(
                collector._credential_reader,
                "load_root_owned_arvan_s3_role_local_credential",
            ) as credential_load,
            mock.patch.object(collector._client_support, "load_role_local_boto_sdk") as sdk_load,
        ):
            with self.assertRaises(collector.PhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorError) as raised:
                owner.collect(wrong)
        self.assertEqual("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_REQUEST_INVALID", raised.exception.code)
        credential_load.assert_not_called()
        sdk_load.assert_not_called()

    def test_disabled_config_is_refused_before_credential_or_sdk_open(self) -> None:
        role = profiles.ARVAN_S3_FI_PUBLISHER_ROLE
        owner = collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector(
            self._config(role, enabled=False)
        )
        with (
            mock.patch.object(collector._client_support, "require_role_local_root"),
            mock.patch.object(
                collector._credential_reader,
                "load_root_owned_arvan_s3_role_local_credential",
            ) as credential_load,
            mock.patch.object(collector._client_support, "load_role_local_boto_sdk") as sdk_load,
        ):
            with self.assertRaises(collector.PhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorError) as raised:
                owner.collect(self._publisher_request(role))
        self.assertEqual("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_DISABLED", raised.exception.code)
        credential_load.assert_not_called()
        sdk_load.assert_not_called()

    def test_rotated_or_wrong_credential_identity_is_refused_before_sdk_client(self) -> None:
        role = profiles.ARVAN_S3_FI_PUBLISHER_ROLE
        owner = collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector(self._config(role))
        route = credential_reader.ArvanS3RoleLocalRouteFacts(
            endpoint=ENDPOINT,
            region=REGION,
            bucket=BUCKET,
        )
        with (
            mock.patch.object(collector._client_support, "require_role_local_root"),
            mock.patch.object(
                collector._credential_reader,
                "load_root_owned_arvan_s3_role_local_credential",
                return_value=(route, _credential("9" * 64)),
            ),
            mock.patch.object(collector._client_support, "load_role_local_boto_sdk") as sdk_load,
        ):
            with self.assertRaises(collector.PhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorError) as raised:
                owner.collect(self._publisher_request(role))
        self.assertEqual("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_IDENTITY_MISMATCH", raised.exception.code)
        sdk_load.assert_not_called()

    def test_accepted_or_wrong_denial_error_fails_closed(self) -> None:
        role = profiles.ARVAN_S3_FI_PUBLISHER_ROLE
        for accepted, expected in (
            ("delete_object", "ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_DENIED_OPERATION_ACCEPTED"),
            (None, "ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_DENIAL_UNPROVEN"),
        ):
            with self.subTest(accepted=accepted):
                fake = _FakeS3(
                    role=role,
                    accept_denied_operation=accepted,
                    error_denied_operation="delete_object" if accepted is None else None,
                )
                owner = collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector(
                    self._config(role)
                )
                route = credential_reader.ArvanS3RoleLocalRouteFacts(
                    endpoint=ENDPOINT,
                    region=REGION,
                    bucket=BUCKET,
                )
                with (
                    mock.patch.object(collector._client_support, "require_role_local_root"),
                    mock.patch.object(
                        collector._credential_reader,
                        "load_root_owned_arvan_s3_role_local_credential",
                        return_value=(route, _credential(_IDENTITIES[role])),
                    ),
                    mock.patch.object(
                        collector._client_support,
                        "load_role_local_boto_sdk",
                        return_value=(object(), object()),
                    ),
                    mock.patch.object(
                        collector._client_support,
                        "create_role_local_raw_s3_client",
                        return_value=fake,
                    ),
                    mock.patch.object(collector.secrets, "token_bytes", return_value=b"q" * 384),
                ):
                    with self.assertRaises(collector.PhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorError) as raised:
                        owner.collect(self._publisher_request(role))
                self.assertEqual(expected, raised.exception.code)
                self.assertTrue(fake.closed)

    def test_object_lock_default_must_exactly_match_the_root_configured_retention(self) -> None:
        role = profiles.ARVAN_S3_FI_PUBLISHER_ROLE
        fake = _FakeS3(role=role, object_lock_days=RETENTION_DAYS - 1)
        owner = collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector(self._config(role))
        route = credential_reader.ArvanS3RoleLocalRouteFacts(
            endpoint=ENDPOINT,
            region=REGION,
            bucket=BUCKET,
        )
        with (
            mock.patch.object(collector._client_support, "require_role_local_root"),
            mock.patch.object(
                collector._credential_reader,
                "load_root_owned_arvan_s3_role_local_credential",
                return_value=(route, _credential(_IDENTITIES[role])),
            ),
            mock.patch.object(
                collector._client_support,
                "load_role_local_boto_sdk",
                return_value=(object(), object()),
            ),
            mock.patch.object(
                collector._client_support,
                "create_role_local_raw_s3_client",
                return_value=fake,
            ),
        ):
            with self.assertRaises(collector.PhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorError) as raised:
                owner.collect(self._publisher_request(role))
        self.assertEqual("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_OBJECT_LOCK_UNPROVEN", raised.exception.code)
        self.assertEqual(
            ["get_bucket_acl", "get_bucket_versioning", "get_object_lock_configuration"],
            [name for name, _request in fake.calls],
        )
        self.assertTrue(fake.closed)

    def test_identity_projection_and_adapter_open_only_one_role_credential_without_sdk(self) -> None:
        role = profiles.ARVAN_S3_IR_PUBLISHER_ROLE
        owner = collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector(self._config(role))
        route = credential_reader.ArvanS3RoleLocalRouteFacts(
            endpoint=ENDPOINT,
            region=REGION,
            bucket=BUCKET,
        )
        with (
            mock.patch.object(collector._client_support, "require_role_local_root"),
            mock.patch.object(
                collector._credential_reader,
                "load_root_owned_arvan_s3_role_local_credential",
                return_value=(route, _credential(_IDENTITIES[role])),
            ) as credential_load,
            mock.patch.object(collector._client_support, "load_role_local_boto_sdk") as sdk_load,
        ):
            projection = owner.identity_projection()
            adapter = owner.live_probe_adapter()
        self.assertEqual(role, projection.role)
        self.assertEqual(_profile_for(role), projection.action_profile)
        self.assertEqual(role, adapter.role)
        self.assertEqual(projection.identity_sha256, adapter.identity_sha256)
        self.assertEqual(2, credential_load.call_count)
        self.assertEqual(
            collector.FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_CREDENTIAL_FILE_BY_ROLE[role],
            credential_load.call_args.kwargs["fixed_credential_file"],
        )
        sdk_load.assert_not_called()

    def test_four_role_adapters_integrate_with_runtime_using_only_fake_s3(self) -> None:
        """The existing runtime can consume four real collector adapters.

        The fake publisher states are copied to each receiver only when that
        receiver's separately configured client is created.  No fake object
        is shared as a client, which preserves the role-local seam property.
        """

        failback_binding = failback.PhysicalIrToFiObjectStorageFailbackBinding(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            route_binding_sha256="4" * 64,
            normal_route_scope_sha256="2" * 64,
            reverse_route_scope_sha256="3" * 64,
            fi_publisher_identity_sha256=_IDENTITIES[profiles.ARVAN_S3_FI_PUBLISHER_ROLE],
            ir_receiver_identity_sha256=_IDENTITIES[profiles.ARVAN_S3_IR_RECEIVER_ROLE],
            ir_publisher_identity_sha256=_IDENTITIES[profiles.ARVAN_S3_IR_PUBLISHER_ROLE],
            fi_receiver_identity_sha256=_IDENTITIES[profiles.ARVAN_S3_FI_RECEIVER_ROLE],
        )
        fixture = make_four_role_live_iam_durable_admission_fixture(
            binding=failback_binding,
            observed_at=NOW,
        )
        binding = immutable.PhysicalArvanS3FourRoleImmutabilityPreflightBinding(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            endpoint=ENDPOINT,
            region=REGION,
            bucket=BUCKET,
            bucket_access_posture="private",
            normal_object_storage_namespace="physical-wal",
            reverse_object_storage_namespace="physical-failback",
            minimum_retention_days=RETENTION_DAYS,
            normal_route_scope_sha256=fixture.live_iam_binding.normal_route_scope_sha256,
            reverse_route_scope_sha256=fixture.live_iam_binding.reverse_route_scope_sha256,
            four_role_route_binding_sha256=fixture.live_iam_binding.four_role_binding_sha256,
            fi_publisher_identity_sha256=fixture.live_iam_binding.fi_publisher_identity_sha256,
            ir_receiver_identity_sha256=fixture.live_iam_binding.ir_receiver_identity_sha256,
            ir_publisher_identity_sha256=fixture.live_iam_binding.ir_publisher_identity_sha256,
            fi_receiver_identity_sha256=fixture.live_iam_binding.fi_receiver_identity_sha256,
        )
        roles = (
            profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
            profiles.ARVAN_S3_IR_RECEIVER_ROLE,
            profiles.ARVAN_S3_IR_PUBLISHER_ROLE,
            profiles.ARVAN_S3_FI_RECEIVER_ROLE,
        )
        owners = {
            role: collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector(
                self._config(role)
            )
            for role in roles
        }
        fakes = {
            profiles.ARVAN_S3_FI_PUBLISHER_ROLE: _FakeS3(
                role=profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
                version_id="version-normal-immutable-001",
            ),
            profiles.ARVAN_S3_IR_RECEIVER_ROLE: _FakeS3(
                role=profiles.ARVAN_S3_IR_RECEIVER_ROLE,
                version_id="version-normal-immutable-001",
            ),
            profiles.ARVAN_S3_IR_PUBLISHER_ROLE: _FakeS3(
                role=profiles.ARVAN_S3_IR_PUBLISHER_ROLE,
                version_id="version-reverse-immutable-001",
            ),
            profiles.ARVAN_S3_FI_RECEIVER_ROLE: _FakeS3(
                role=profiles.ARVAN_S3_FI_RECEIVER_ROLE,
                version_id="version-reverse-immutable-001",
            ),
        }
        route = credential_reader.ArvanS3RoleLocalRouteFacts(
            endpoint=ENDPOINT,
            region=REGION,
            bucket=BUCKET,
        )
        role_by_access_key = {
            "access-key-" + identity[:8]: role for role, identity in _IDENTITIES.items()
        }

        def credential_load(**kwargs: object):
            role = kwargs["expected_role"]
            if type(role) is not str:
                raise AssertionError("collector did not pin a string role")
            return route, _credential(_IDENTITIES[role])

        def client_create(**kwargs: object) -> _FakeS3:
            access_key = kwargs["access_key"]
            if type(access_key) is not str:
                raise AssertionError("collector did not use a local credential")
            role = role_by_access_key[access_key]
            fake = fakes[role]
            if role == profiles.ARVAN_S3_IR_RECEIVER_ROLE:
                source = fakes[profiles.ARVAN_S3_FI_PUBLISHER_ROLE]
                fake.payload = source.payload
                fake.key = source.key
                fake.version_id = source.version_id
                fake.retention_until = source.retention_until
            elif role == profiles.ARVAN_S3_FI_RECEIVER_ROLE:
                source = fakes[profiles.ARVAN_S3_IR_PUBLISHER_ROLE]
                fake.payload = source.payload
                fake.key = source.key
                fake.version_id = source.version_id
                fake.retention_until = source.retention_until
            return fake

        with (
            mock.patch.object(runtime.os, "geteuid", return_value=0),
            mock.patch.object(collector._client_support, "require_role_local_root"),
            mock.patch.object(
                collector._credential_reader,
                "load_root_owned_arvan_s3_role_local_credential",
                side_effect=credential_load,
            ),
            mock.patch.object(
                collector._client_support,
                "load_role_local_boto_sdk",
                return_value=(object(), object()),
            ),
            mock.patch.object(
                collector._client_support,
                "create_role_local_raw_s3_client",
                side_effect=client_create,
            ),
            mock.patch.object(
                runtime.secrets,
                "token_bytes",
                side_effect=[b"n" * 32, b"r" * 32, b"f" * 384, b"i" * 384],
            ),
        ):
            adapters = {role: owners[role].live_probe_adapter() for role in roles}
            observation = runtime.PhysicalArvanS3FourRoleImmutabilityLiveProbeRuntime(
                runtime.PhysicalArvanS3FourRoleImmutabilityLiveProbeConfig(
                    binding=binding,
                    fi_publisher_adapter=adapters[profiles.ARVAN_S3_FI_PUBLISHER_ROLE],
                    ir_receiver_adapter=adapters[profiles.ARVAN_S3_IR_RECEIVER_ROLE],
                    ir_publisher_adapter=adapters[profiles.ARVAN_S3_IR_PUBLISHER_ROLE],
                    fi_receiver_adapter=adapters[profiles.ARVAN_S3_FI_RECEIVER_ROLE],
                    enabled=True,
                )
            ).collect(
                admission=fixture.live_iam_durable_admission,
                live_iam_binding=fixture.live_iam_binding,
                failback_binding=failback_binding,
                observed_at=NOW,
            )
        self.assertEqual("four-role-immutable-observed", observation.status)
        self.assertEqual(
            observation.normal_direction.immutable_version.object_key,
            f"physical-wal/{CAMPAIGN}/{RELEASE}/four-role-immutability/"
            f"fi-publisher-to-ir-receiver/{hashlib.sha256(b'n' * 32).hexdigest()}.age",
        )
        self.assertEqual(
            observation.reverse_direction.immutable_version.object_key,
            f"physical-failback/{CAMPAIGN}/{RELEASE}/four-role-immutability/"
            f"ir-publisher-to-fi-receiver/{hashlib.sha256(b'r' * 32).hexdigest()}.age",
        )
        for fake in fakes.values():
            self.assertTrue(fake.closed)
        self.assertEqual(
            ["get_object", "head_object", "put_object", "delete_object", "delete_object", "list_objects_v2", "list_object_versions"],
            [name for name, _request in fakes[profiles.ARVAN_S3_IR_RECEIVER_ROLE].calls],
        )

    def test_fixed_config_parser_is_role_pinned_and_rejects_unknown_material(self) -> None:
        body = (
            b'{"bucket":"private-four-role-collector","enabled":false,'
            b'"endpoint":"https://s3.ir-thr-at1.arvanstorage.ir",'
            b'"region":"ir-thr-at1","retention_days":90,'
            b'"role":"fi-publisher",'
            b'"schema":"gold-trade-physical-arvan-s3-four-role-immutability-role-local-collector-config-v1"}'
        )
        parsed = collector._parse_fixed_collector_config(body, expected_role="fi-publisher")
        self.assertFalse(parsed.enabled)
        self.assertEqual("fi-publisher", parsed.role)
        with self.assertRaises(collector.PhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorError) as raised:
            collector._parse_fixed_collector_config(body, expected_role="ir-publisher")
        self.assertEqual("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_COLLECTOR_CONFIG_FILE_INVALID", raised.exception.code)
        unknown_field = body[:-1] + b',"unexpected":true}'
        with self.assertRaises(collector.PhysicalArvanS3FourRoleImmutabilityRoleLocalCollectorError):
            collector._parse_fixed_collector_config(unknown_field, expected_role="fi-publisher")

    def test_module_has_no_direct_site_or_retired_paired_runtime_surface(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertTrue({"socket", "subprocess", "requests", "urllib"}.isdisjoint(imports))
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("physical_arvan_s3_separated_client_factory", source)
        self.assertNotIn("physical_arvan_s3_failback_separated_client_factory", source)
        self.assertNotIn("ssh", source.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
