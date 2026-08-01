"""Adversarial tests for the injected four-role immutable-storage bridge.

These tests use only small callback fakes.  No SDK, credential, socket, S3
client, or provider endpoint is instantiated.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
from pathlib import Path
from unittest import mock
import unittest

from core import physical_arvan_s3_four_role_immutability_live_probe_runtime as runtime_module
from core import physical_arvan_s3_four_role_immutability_preflight as immutable
from core import physical_arvan_s3_role_profiles as profiles
from core import physical_ir_to_fi_object_storage_failback_preflight as failback
from tests.physical_arvan_s3_four_role_live_iam_fixture import (
    make_four_role_live_iam_durable_admission_fixture,
)


CAMPAIGN = "four-role-live-runtime-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
NOW = datetime(2026, 7, 31, 20, 0, 0, tzinfo=timezone.utc)
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_arvan_s3_four_role_immutability_live_probe_runtime.py"
)


class _ExplosiveGenericClientSurface:
    """A callable fake whose generic provider-looking members must stay unused."""

    def __init__(self, callback: object) -> None:
        self._callback = callback
        self.generic_calls: list[str] = []

    def __call__(self, request: object) -> object:
        return self._callback(request)

    def put_object(self, **kwargs: object) -> None:
        del kwargs
        self.generic_calls.append("put_object")
        raise AssertionError("runtime must not invoke provider client methods")

    def delete_object(self, **kwargs: object) -> None:
        del kwargs
        self.generic_calls.append("delete_object")
        raise AssertionError("runtime must not invoke provider client methods")

    def list_object_versions(self, **kwargs: object) -> None:
        del kwargs
        self.generic_calls.append("list_object_versions")
        raise AssertionError("runtime must not invoke provider client methods")


class PhysicalArvanS3FourRoleImmutabilityLiveProbeRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.failback_binding = failback.PhysicalIrToFiObjectStorageFailbackBinding(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            route_binding_sha256="4" * 64,
            normal_route_scope_sha256="2" * 64,
            reverse_route_scope_sha256="3" * 64,
            fi_publisher_identity_sha256="5" * 64,
            ir_receiver_identity_sha256="6" * 64,
            ir_publisher_identity_sha256="7" * 64,
            fi_receiver_identity_sha256="8" * 64,
        )
        fixture = make_four_role_live_iam_durable_admission_fixture(
            binding=self.failback_binding,
            observed_at=NOW,
        )
        self.live_iam_binding = fixture.live_iam_binding
        self.admission = fixture.live_iam_durable_admission
        self.binding = immutable.PhysicalArvanS3FourRoleImmutabilityPreflightBinding(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
            region="ir-thr-at1",
            bucket="private-four-role-live-runtime",
            bucket_access_posture="private",
            normal_object_storage_namespace="physical-wal",
            reverse_object_storage_namespace="physical-failback",
            minimum_retention_days=90,
            normal_route_scope_sha256=self.live_iam_binding.normal_route_scope_sha256,
            reverse_route_scope_sha256=self.live_iam_binding.reverse_route_scope_sha256,
            four_role_route_binding_sha256=self.live_iam_binding.four_role_binding_sha256,
            fi_publisher_identity_sha256=self.live_iam_binding.fi_publisher_identity_sha256,
            ir_receiver_identity_sha256=self.live_iam_binding.ir_receiver_identity_sha256,
            ir_publisher_identity_sha256=self.live_iam_binding.ir_publisher_identity_sha256,
            fi_receiver_identity_sha256=self.live_iam_binding.fi_receiver_identity_sha256,
        )
        self.calls: list[str] = []

    def _identity(self, role: str) -> str:
        return {
            "fi-publisher": self.binding.fi_publisher_identity_sha256,
            "ir-receiver": self.binding.ir_receiver_identity_sha256,
            "ir-publisher": self.binding.ir_publisher_identity_sha256,
            "fi-receiver": self.binding.fi_receiver_identity_sha256,
        }[role]

    def _profile(self, role: str) -> str:
        return {
            "fi-publisher": profiles.ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
            "ir-receiver": profiles.ARVAN_S3_IR_RECEIVER_EXACT_READONLY_PROFILE,
            "ir-publisher": profiles.ARVAN_S3_IR_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
            "fi-receiver": profiles.ARVAN_S3_FI_RECEIVER_EXACT_READONLY_PROFILE,
        }[role]

    def _publisher_readback(
        self,
        request: runtime_module.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest,
        *,
        bucket_readback: runtime_module.PhysicalArvanS3FourRoleImmutabilityBucketReadback | None,
    ) -> runtime_module.PhysicalArvanS3FourRoleImmutabilityPublisherReadback:
        self.calls.append(request.role)
        content_sha256 = "a" * 64 if request.role == "fi-publisher" else "b" * 64
        version = "version-normal-001" if request.role == "fi-publisher" else "version-reverse-001"
        return runtime_module.PhysicalArvanS3FourRoleImmutabilityPublisherReadback(
            schema=runtime_module.PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA,
            direction=request.direction,
            role=request.role,
            identity_sha256=request.identity_sha256,
            probe_nonce_sha256=request.probe_nonce_sha256,
            object_key=request.object_key,
            object_version_id=version,
            content_sha256=content_sha256,
            content_bytes=4096,
            retention_until=request.retention_not_before,
            create_only_outcome="create-only-succeeded",
            overwrite_outcome="access-denied",
            object_removal_outcome="access-denied",
            version_removal_outcome="access-denied",
            bucket_readback=bucket_readback,
        )

    def _receiver_readback(
        self,
        request: runtime_module.PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest,
    ) -> runtime_module.PhysicalArvanS3FourRoleImmutabilityReceiverReadback:
        self.calls.append(request.role)
        version = request.immutable_version
        return runtime_module.PhysicalArvanS3FourRoleImmutabilityReceiverReadback(
            schema=runtime_module.PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA,
            direction=request.direction,
            role=request.role,
            identity_sha256=request.identity_sha256,
            probe_nonce_sha256=version.probe_nonce_sha256,
            object_key=version.object_key,
            object_version_id=version.object_version_id,
            exact_head_version_id=version.object_version_id,
            exact_get_version_id=version.object_version_id,
            exact_get_content_sha256=version.content_sha256,
            exact_get_content_bytes=version.content_bytes,
            put_outcome="access-denied",
            object_removal_outcome="access-denied",
            version_removal_outcome="access-denied",
            bucket_enumeration_outcome="access-denied",
            version_enumeration_outcome="access-denied",
        )

    def _adapter(self, role: str, callback: object) -> runtime_module.PhysicalArvanS3FourRoleImmutabilityRoleLocalAdapter:
        return runtime_module.PhysicalArvanS3FourRoleImmutabilityRoleLocalAdapter(
            role=role,
            identity_sha256=self._identity(role),
            action_profile=self._profile(role),
            readback_adapter=callback,
        )

    def _config(
        self,
        *,
        enabled: bool = True,
        fi_publisher_callback: object | None = None,
        ir_receiver_callback: object | None = None,
        ir_publisher_callback: object | None = None,
        fi_receiver_callback: object | None = None,
    ) -> runtime_module.PhysicalArvanS3FourRoleImmutabilityLiveProbeConfig:
        bucket = runtime_module.PhysicalArvanS3FourRoleImmutabilityBucketReadback(
            acl_posture="private-canonical-owner-only-v1",
            versioning_status="Enabled",
            retention_mode="s3-object-lock-compliance-v1",
            retention_days=90,
        )
        fi_callback = fi_publisher_callback or (
            lambda request: self._publisher_readback(request, bucket_readback=bucket)
        )
        ir_pub_callback = ir_publisher_callback or (
            lambda request: self._publisher_readback(request, bucket_readback=None)
        )
        ir_recv_callback = ir_receiver_callback or self._receiver_readback
        fi_recv_callback = fi_receiver_callback or self._receiver_readback
        return runtime_module.PhysicalArvanS3FourRoleImmutabilityLiveProbeConfig(
            binding=self.binding,
            fi_publisher_adapter=self._adapter("fi-publisher", fi_callback),
            ir_receiver_adapter=self._adapter("ir-receiver", ir_recv_callback),
            ir_publisher_adapter=self._adapter("ir-publisher", ir_pub_callback),
            fi_receiver_adapter=self._adapter("fi-receiver", fi_recv_callback),
            enabled=enabled,
        )

    def _collect(
        self,
        config: runtime_module.PhysicalArvanS3FourRoleImmutabilityLiveProbeConfig,
        *,
        admission: object | None = None,
    ) -> immutable.PhysicalArvanS3FourRoleImmutabilityPreflightObservation:
        return runtime_module.PhysicalArvanS3FourRoleImmutabilityLiveProbeRuntime(config).collect(
            admission=self.admission if admission is None else admission,
            live_iam_binding=self.live_iam_binding,
            failback_binding=self.failback_binding,
            observed_at=NOW,
        )

    def test_collects_four_explicit_role_local_readbacks_into_pure_observation(self) -> None:
        fi_surface = _ExplosiveGenericClientSurface(
            lambda request: self._publisher_readback(
                request,
                bucket_readback=runtime_module.PhysicalArvanS3FourRoleImmutabilityBucketReadback(
                    acl_posture="private-canonical-owner-only-v1",
                    versioning_status="Enabled",
                    retention_mode="s3-object-lock-compliance-v1",
                    retention_days=90,
                ),
            )
        )
        ir_surface = _ExplosiveGenericClientSurface(self._receiver_readback)
        ir_publisher_surface = _ExplosiveGenericClientSurface(
            lambda request: self._publisher_readback(request, bucket_readback=None)
        )
        fi_receiver_surface = _ExplosiveGenericClientSurface(self._receiver_readback)
        with mock.patch.object(
            runtime_module.secrets,
            "token_bytes",
            side_effect=[b"n" * 32, b"r" * 32],
        ):
            observation = self._collect(
                self._config(
                    fi_publisher_callback=fi_surface,
                    ir_receiver_callback=ir_surface,
                    ir_publisher_callback=ir_publisher_surface,
                    fi_receiver_callback=fi_receiver_surface,
                )
            )
        self.assertEqual(
            self.calls,
            ["fi-publisher", "ir-receiver", "ir-publisher", "fi-receiver"],
        )
        self.assertEqual(observation.status, "four-role-immutable-observed")
        verified = immutable.verify_physical_arvan_s3_four_role_immutability_preflight(
            observation,
            config=immutable.PhysicalArvanS3FourRoleImmutabilityPreflightConfig(
                binding=self.binding,
                enabled=True,
                maximum_evidence_age_seconds=120,
            ),
            admission=self.admission,
            live_iam_binding=self.live_iam_binding,
            failback_binding=self.failback_binding,
            observed_at=NOW,
        )
        self.assertEqual(verified.observation, observation)
        self.assertEqual(
            observation.normal_direction.immutable_version.object_key,
            "physical-wal/four-role-live-runtime-20260731/"
            "3138d0c2a8d20a84042c3a438fbc88db7a4db498/"
            "four-role-immutability/fi-publisher-to-ir-receiver/"
            + hashlib.sha256(b"n" * 32).hexdigest()
            + ".age",
        )
        self.assertEqual(
            observation.normal_direction.retention_policy_evidence_sha256,
            observation.reverse_direction.retention_policy_evidence_sha256,
        )
        for surface in (fi_surface, ir_surface, ir_publisher_surface, fi_receiver_surface):
            self.assertEqual(surface.generic_calls, [])

    def test_collect_has_no_caller_controlled_probe_nonce_argument(self) -> None:
        parameters = inspect.signature(
            runtime_module.PhysicalArvanS3FourRoleImmutabilityLiveProbeRuntime.collect
        ).parameters
        self.assertNotIn("normal_probe_nonce_sha256", parameters)
        self.assertNotIn("reverse_probe_nonce_sha256", parameters)

    def test_nonce_collision_fails_before_any_role_local_callback(self) -> None:
        with mock.patch.object(runtime_module.secrets, "token_bytes", return_value=b"x" * 32):
            with self.assertRaises(runtime_module.PhysicalArvanS3FourRoleImmutabilityLiveProbeError) as raised:
                self._collect(self._config())
        self.assertEqual(
            raised.exception.code,
            "ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_NONCE_COLLISION",
        )
        self.assertEqual(self.calls, [])

    def test_default_or_disabled_runtime_never_invokes_explicit_adapters(self) -> None:
        calls: list[str] = []

        def should_not_run(request: object) -> object:
            del request
            calls.append("called")
            raise AssertionError("disabled runtime reached an adapter")

        runtime = runtime_module.PhysicalArvanS3FourRoleImmutabilityLiveProbeRuntime(
            self._config(
                enabled=False,
                fi_publisher_callback=should_not_run,
                ir_receiver_callback=lambda request: should_not_run(request),
                ir_publisher_callback=lambda request: should_not_run(request),
                fi_receiver_callback=lambda request: should_not_run(request),
            )
        )
        self.assertEqual(calls, [])
        with self.assertRaises(runtime_module.PhysicalArvanS3FourRoleImmutabilityLiveProbeError) as raised:
            self._collect(runtime._config)
        self.assertEqual(
            raised.exception.code,
            "ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_DISABLED",
        )
        self.assertEqual(calls, [])

    def test_bad_admission_is_rejected_before_any_callback(self) -> None:
        with mock.patch.object(runtime_module.secrets, "token_bytes") as token_bytes:
            with self.assertRaises(runtime_module.PhysicalArvanS3FourRoleImmutabilityLiveProbeError) as raised:
                self._collect(self._config(), admission=object())
        self.assertEqual(
            raised.exception.code,
            "ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_ADMISSION_INVALID",
        )
        token_bytes.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_role_callback_collision_is_rejected_before_any_callback(self) -> None:
        def shared_callback(request: object) -> object:
            del request
            self.calls.append("shared")
            raise AssertionError("collided callback must not run")

        config = self._config(
            fi_publisher_callback=shared_callback,
            ir_receiver_callback=shared_callback,
            ir_publisher_callback=shared_callback,
            fi_receiver_callback=shared_callback,
        )
        with self.assertRaises(runtime_module.PhysicalArvanS3FourRoleImmutabilityLiveProbeError) as raised:
            self._collect(config)
        self.assertEqual(
            raised.exception.code,
            "ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_ROLE_COLLISION",
        )
        self.assertEqual(self.calls, [])

    def test_incomplete_publisher_readback_blocks_receivers_and_reverse_path(self) -> None:
        def incomplete(
            request: runtime_module.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest,
        ) -> runtime_module.PhysicalArvanS3FourRoleImmutabilityPublisherReadback:
            result = self._publisher_readback(request, bucket_readback=None)
            return replace(result, bucket_readback=None)

        with self.assertRaises(runtime_module.PhysicalArvanS3FourRoleImmutabilityLiveProbeError) as raised:
            self._collect(self._config(fi_publisher_callback=incomplete))
        self.assertEqual(
            raised.exception.code,
            "ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_BUCKET_READBACK_INVALID",
        )
        self.assertEqual(self.calls, ["fi-publisher"])

    def test_wrong_exact_receiver_selector_is_rejected_before_reverse_path(self) -> None:
        def wrong_receiver(
            request: runtime_module.PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest,
        ) -> runtime_module.PhysicalArvanS3FourRoleImmutabilityReceiverReadback:
            return replace(self._receiver_readback(request), exact_get_version_id="other-version")

        with self.assertRaises(runtime_module.PhysicalArvanS3FourRoleImmutabilityLiveProbeError) as raised:
            self._collect(self._config(ir_receiver_callback=wrong_receiver))
        self.assertEqual(
            raised.exception.code,
            "ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_RECEIVER_READBACK_INVALID",
        )
        self.assertEqual(self.calls, ["fi-publisher", "ir-receiver"])

    def test_nonroot_is_refused_without_readback_invocation(self) -> None:
        with mock.patch.object(runtime_module.os, "geteuid", return_value=1000):
            with self.assertRaises(runtime_module.PhysicalArvanS3FourRoleImmutabilityLiveProbeError) as raised:
                self._collect(self._config())
        self.assertEqual(
            raised.exception.code,
            "ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_REQUIRES_ROOT",
        )
        self.assertEqual(self.calls, [])

    def test_module_has_no_sdk_network_or_provider_operation_surface(self) -> None:
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
        self.assertTrue(
            {"boto3", "botocore", "socket", "subprocess", "requests", "urllib"}.isdisjoint(imports)
        )
        attributes = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertTrue(
            {
                "put_object",
                "delete_object",
                "delete_objects",
                "list_objects",
                "list_objects_v2",
                "list_object_versions",
                "get_object",
                "head_object",
            }.isdisjoint(attributes)
        )
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("physical_arvan_s3_immutability_live_probe import", source)
        self.assertNotIn("physical_arvan_s3_separated_client_factory", source)
