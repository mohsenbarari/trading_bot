"""Pure injected-client tests for the Arvan immutability live-probe adapter."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import os
from pathlib import Path
import unittest
from unittest import mock

import core.physical_arvan_immutability_preflight as preflight
import core.physical_arvan_s3_immutability_live_probe as live_probe


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
REGION = "ir-thr-at1"
BUCKET = "private-physical-recovery"
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_arvan_s3_immutability_live_probe.py"
)


def binding(**overrides: object) -> preflight.PhysicalArvanImmutabilityPreflightBinding:
    values: dict[str, object] = {
        "campaign_id": "physical-arvan-preflight-20260731",
        "release_sha": "3138d0c2a8d20a84042c3a438fbc88db7a4db498",
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "route_binding_sha256": "a" * 64,
        "endpoint": ENDPOINT,
        "region": REGION,
        "bucket": BUCKET,
        "minimum_retention_days": 90,
    }
    values.update(overrides)
    return preflight.PhysicalArvanImmutabilityPreflightBinding(**values)


class MemoryBody:
    def __init__(self, payload: bytes) -> None:
        self._stream = io.BytesIO(payload)

    def read(self, amount: int = -1) -> bytes:
        return self._stream.read(amount)


class SharedObjectState:
    def __init__(self) -> None:
        self.key: str | None = None
        self.version_id = "preflight-version-20260731"
        self.payload: bytes | None = None
        self.retention_until: datetime | None = None
        self.checksum_b64: str | None = None


class FakeS3Client:
    """In-memory S3-shaped client.  It records no credentials or URLs."""

    def __init__(self, *, role: str, state: SharedObjectState) -> None:
        self.role = role
        self.state = state
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.versioning: object = {"Status": "Enabled"}
        self.object_lock: object = {
            "ObjectLockConfiguration": {
                "ObjectLockEnabled": "Enabled",
                "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 180}},
            }
        }
        self.acl: object = {
            "Owner": {"ID": "owner-canonical-id"},
            "Grants": [
                {
                    "Grantee": {"Type": "CanonicalUser", "ID": "owner-canonical-id"},
                    "Permission": "FULL_CONTROL",
                }
            ],
        }
        self.accept_delete = False
        self.accept_overwrite = False
        self.return_wrong_version = False
        self.fail_operation: str | None = None

    def _record(self, method: str, request: dict[str, object]) -> None:
        self.calls.append((method, request))
        if self.fail_operation == method:
            raise RuntimeError("https://secret.invalid/?token=never-expose-this")

    def get_bucket_versioning(self, **request: object):
        self._record("get_bucket_versioning", dict(request))
        return self.versioning

    def get_bucket_acl(self, **request: object):
        self._record("get_bucket_acl", dict(request))
        return self.acl

    def get_object_lock_configuration(self, **request: object):
        self._record("get_object_lock_configuration", dict(request))
        return self.object_lock

    def put_object(self, **request: object):
        copied = dict(request)
        self._record("put_object", copied)
        is_first_create = (
            self.role == "fi"
            and self.state.key is None
            and copied.get("IfNoneMatch") == "*"
        )
        if is_first_create:
            body = copied.get("Body")
            retain_until = copied.get("ObjectLockRetainUntilDate")
            checksum = copied.get("ChecksumSHA256")
            if not isinstance(body, bytes) or not isinstance(retain_until, datetime):
                raise AssertionError("adapter did not create the exact fixed object request")
            if not isinstance(checksum, str):
                raise AssertionError("adapter omitted the fixed checksum header")
            self.state.key = copied.get("Key") if isinstance(copied.get("Key"), str) else None
            self.state.payload = body
            self.state.retention_until = retain_until
            self.state.checksum_b64 = checksum
            return {"VersionId": self.state.version_id}
        if self.accept_overwrite:
            return {"VersionId": "accepted-overwrite"}
        raise live_probe.InjectedS3AccessDenied()

    def list_object_versions(self, **request: object):
        self._record("list_object_versions", dict(request))
        if self.role == "ir":
            raise live_probe.InjectedS3AccessDenied()
        return {
            "Versions": [
                {
                    "Key": self.state.key,
                    "VersionId": self.state.version_id,
                    "IsLatest": True,
                    "Size": len(self._payload()),
                }
            ],
            "DeleteMarkers": [],
            "IsTruncated": False,
        }

    def list_objects_v2(self, **request: object):
        self._record("list_objects_v2", dict(request))
        raise live_probe.InjectedS3AccessDenied()

    def get_object_retention(self, **request: object):
        self._record("get_object_retention", dict(request))
        return {
            "Retention": {
                "Mode": "COMPLIANCE",
                "RetainUntilDate": self._retention_until(),
            }
        }

    def _payload(self) -> bytes:
        assert self.state.payload is not None
        return self.state.payload

    def _retention_until(self) -> datetime:
        assert self.state.retention_until is not None
        return self.state.retention_until

    def _metadata(self) -> dict[str, object]:
        payload = self._payload()
        version = "wrong-version" if self.return_wrong_version else self.state.version_id
        return {
            "VersionId": version,
            "ContentLength": len(payload),
            "ContentType": "application/octet-stream",
            "CacheControl": "no-store",
            "ChecksumSHA256": self.state.checksum_b64,
            "ObjectLockMode": "COMPLIANCE",
            "ObjectLockRetainUntilDate": self._retention_until(),
        }

    def head_object(self, **request: object):
        self._record("head_object", dict(request))
        return self._metadata()

    def get_object(self, **request: object):
        self._record("get_object", dict(request))
        payload = self._payload()
        return {
            **self._metadata(),
            "ContentRange": f"bytes 0-{len(payload) - 1}/{len(payload)}",
            "AcceptRanges": "bytes",
            "Body": MemoryBody(payload),
        }

    def delete_object(self, **request: object):
        self._record("delete_object", dict(request))
        if self.accept_delete:
            return {"accepted": True}
        raise live_probe.InjectedS3AccessDenied()


class PhysicalArvanS3ImmutabilityLiveProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._reset_clients()

    def _reset_clients(self) -> None:
        self.state = SharedObjectState()
        self.fi = FakeS3Client(role="fi", state=self.state)
        self.ir = FakeS3Client(role="ir", state=self.state)
        self.binding = binding()

    def _config(
        self,
        **overrides: object,
    ) -> live_probe.PhysicalArvanS3ImmutabilityLiveProbeConfig:
        values: dict[str, object] = {
            "binding": self.binding,
            "enabled": True,
            "fi_publisher": live_probe.PhysicalArvanS3ImmutabilityScopedClient(
                credential_identity_sha256="b" * 64,
                client=self.fi,
            ),
            "ir_receiver": live_probe.PhysicalArvanS3ImmutabilityScopedClient(
                credential_identity_sha256="c" * 64,
                client=self.ir,
            ),
        }
        values.update(overrides)
        return live_probe.PhysicalArvanS3ImmutabilityLiveProbeConfig(**values)

    def _collect(
        self,
        adapter: live_probe.PhysicalArvanS3ImmutabilityLiveProbe | None = None,
    ) -> preflight.PhysicalArvanImmutabilityPreflightObservation:
        probe = adapter or live_probe.PhysicalArvanS3ImmutabilityLiveProbe(self._config())
        with mock.patch.object(live_probe.os, "geteuid", return_value=0), mock.patch.object(
            live_probe.secrets,
            "token_bytes",
            return_value=b"x" * live_probe._ROOT_PINNED_RANDOM_BYTES,
        ), mock.patch.object(live_probe.secrets, "token_hex", return_value="1" * 32):
            return probe.collect(binding=self.binding, observed_at=NOW)

    def test_exact_create_only_version_readback_and_preflight_verification(self) -> None:
        raw = self._collect()
        self.assertEqual("Enabled", raw.versioning_status)
        self.assertEqual("s3-object-lock-compliance-v1", raw.retention_mode)
        self.assertEqual("preflight-version-20260731", raw.disposable_probe.version_id)
        self.assertEqual(
            raw.disposable_probe.version_id,
            raw.disposable_probe.retrieved_version_id,
        )
        self.assertTrue(
            raw.disposable_probe.object_key.startswith(
                "physical-preflight/physical-arvan-preflight-20260731/arvan-immutability/"
            )
        )
        self.assertTrue(raw.disposable_probe.object_key.endswith(".age"))
        self.assertEqual(
            hashlib.sha256(self.state.payload or b"").hexdigest(),
            raw.disposable_probe.ciphertext_sha256,
        )

        fi_put = next(request for method, request in self.fi.calls if method == "put_object")
        self.assertEqual("*", fi_put["IfNoneMatch"])
        self.assertEqual("private", fi_put["ACL"])
        self.assertEqual("SHA256", fi_put["ChecksumAlgorithm"])
        self.assertEqual("COMPLIANCE", fi_put["ObjectLockMode"])
        self.assertEqual("no-store", fi_put["CacheControl"])
        self.assertEqual("application/octet-stream", fi_put["ContentType"])
        self.assertFalse("Endpoint" in fi_put or "URL" in fi_put)

        fi_allowed_by_call = tuple(
            {
                "get_bucket_acl": "GetBucketAcl",
                "get_bucket_versioning": "GetBucketVersioning",
                "get_object_lock_configuration": "GetObjectLockConfiguration",
                "list_object_versions": "ListObjectVersions:exact-key",
                "get_object_retention": "GetObjectRetention:exact-version",
                "get_object": "GetObject:exact-version",
                "head_object": "HeadObject:exact-version",
            }.get(method, "PutObject:create-only")
            for method, request in self.fi.calls
            if method not in {"delete_object", "put_object"}
            or (method == "put_object" and request.get("IfNoneMatch") == "*")
        )
        self.assertEqual(raw.credential_restrictions[0].allowed_operations, fi_allowed_by_call)
        self.assertEqual(
            raw.credential_restrictions[1].allowed_operations,
            tuple(
                {
                    "get_object": "GetObject:exact-version",
                    "head_object": "HeadObject:exact-version",
                }[method]
                for method, _ in self.ir.calls
                if method in {"get_object", "head_object"}
            ),
        )
        self.assertEqual(
            {method for method, _ in self.fi.calls},
            {
                "get_bucket_acl",
                "get_bucket_versioning",
                "get_object_lock_configuration",
                "put_object",
                "list_object_versions",
                "get_object_retention",
                "get_object",
                "head_object",
                "delete_object",
            },
        )
        self.assertEqual(
            {method for method, _ in self.ir.calls},
            {
                "delete_object",
                "list_objects_v2",
                "list_object_versions",
                "put_object",
                "get_object",
                "head_object",
            },
        )

        for client in (self.fi, self.ir):
            head = next(request for method, request in client.calls if method == "head_object")
            get = next(request for method, request in client.calls if method == "get_object")
            self.assertEqual(raw.disposable_probe.version_id, head["VersionId"])
            self.assertEqual(raw.disposable_probe.version_id, get["VersionId"])
            self.assertEqual(
                f"bytes=0-{raw.disposable_probe.ciphertext_bytes - 1}",
                get["Range"],
            )
            self.assertEqual("ENABLED", get["ChecksumMode"])

        verified = preflight.verify_physical_arvan_immutability_preflight(
            raw,
            binding=self.binding,
            now=NOW,
        )
        self.assertEqual(raw.evidence_sha256, verified.observation.evidence_sha256)

        self._reset_clients()
        adapter = live_probe.PhysicalArvanS3ImmutabilityLiveProbe(self._config())
        outer_config = preflight.PhysicalArvanImmutabilityPreflightConfig(
            binding=self.binding,
            enabled=True,
        )
        with mock.patch.object(preflight.os, "geteuid", return_value=0), mock.patch.object(
            live_probe.secrets,
            "token_bytes",
            return_value=b"y" * live_probe._ROOT_PINNED_RANDOM_BYTES,
        ), mock.patch.object(live_probe.secrets, "token_hex", return_value="2" * 32):
            outer_verified = preflight.collect_physical_arvan_immutability_preflight(
                config=outer_config,
                probe=adapter,
                now=NOW,
            )
        self.assertEqual(self.binding, outer_verified.binding)

    def test_disabled_or_missing_config_makes_no_client_call(self) -> None:
        disabled = live_probe.PhysicalArvanS3ImmutabilityLiveProbe(
            self._config(enabled=False)
        )
        missing = live_probe.PhysicalArvanS3ImmutabilityLiveProbe()
        for adapter, code in (
            (disabled, "ARVAN_S3_IMMUTABILITY_LIVE_PROBE_DISABLED"),
            (missing, "ARVAN_S3_IMMUTABILITY_LIVE_PROBE_DISABLED"),
        ):
            with self.subTest(code=code), mock.patch.object(
                live_probe.os, "geteuid", return_value=0
            ):
                with self.assertRaisesRegex(
                    live_probe.PhysicalArvanS3ImmutabilityLiveProbeError,
                    "^" + code + "$",
                ):
                    adapter.collect(binding=self.binding, observed_at=NOW)
        self.assertEqual([], self.fi.calls)
        self.assertEqual([], self.ir.calls)

        outer_config = preflight.PhysicalArvanImmutabilityPreflightConfig(
            binding=self.binding,
            enabled=False,
        )
        with mock.patch.object(preflight.os, "geteuid", return_value=0):
            with self.assertRaises(preflight.PhysicalArvanImmutabilityPreflightError):
                preflight.collect_physical_arvan_immutability_preflight(
                    config=outer_config,
                    probe=disabled,
                    now=NOW,
                )
        self.assertEqual([], self.fi.calls)
        self.assertEqual([], self.ir.calls)

    def test_missing_versioning_or_object_lock_fails_before_disposable_write(self) -> None:
        cases = (
            ("versioning", {}, "ARVAN_S3_IMMUTABILITY_VERSIONING_UNPROVEN"),
            ("object_lock", {}, "ARVAN_S3_IMMUTABILITY_OBJECT_LOCK_UNPROVEN"),
        )
        for attribute, value, code in cases:
            with self.subTest(attribute=attribute):
                setattr(self.fi, attribute, value)
                with self.assertRaisesRegex(
                    live_probe.PhysicalArvanS3ImmutabilityLiveProbeError,
                    "^" + code + "$",
                ):
                    self._collect()
                self.assertFalse(any(method == "put_object" for method, _ in self.fi.calls))
                self.assertEqual([], self.ir.calls)
                self._reset_clients()

    def test_accepted_delete_or_overwrite_is_not_misreported_as_immutable(self) -> None:
        for client_name, attribute in (
            ("fi", "accept_delete"),
            ("fi", "accept_overwrite"),
            ("ir", "accept_delete"),
            ("ir", "accept_overwrite"),
        ):
            with self.subTest(client=client_name, attribute=attribute):
                setattr(getattr(self, client_name), attribute, True)
                with self.assertRaisesRegex(
                    live_probe.PhysicalArvanS3ImmutabilityLiveProbeError,
                    "^ARVAN_S3_IMMUTABILITY_DENIED_OPERATION_ACCEPTED$",
                ):
                    self._collect()
                self._reset_clients()

    def test_nonroot_fails_before_any_injected_client_call(self) -> None:
        adapter = live_probe.PhysicalArvanS3ImmutabilityLiveProbe(self._config())
        with mock.patch.object(live_probe.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(
                live_probe.PhysicalArvanS3ImmutabilityLiveProbeError,
                "^ARVAN_S3_IMMUTABILITY_REQUIRES_ROOT$",
            ):
                adapter.collect(binding=self.binding, observed_at=NOW)
        self.assertEqual([], self.fi.calls)
        self.assertEqual([], self.ir.calls)

    def test_cross_credential_reuse_fails_before_any_client_call(self) -> None:
        reused_identity = live_probe.PhysicalArvanS3ImmutabilityScopedClient(
            credential_identity_sha256="b" * 64,
            client=self.ir,
        )
        same_client = live_probe.PhysicalArvanS3ImmutabilityScopedClient(
            credential_identity_sha256="c" * 64,
            client=self.fi,
        )
        for scoped_ir in (reused_identity, same_client):
            with self.subTest(scoped_ir=scoped_ir.credential_identity_sha256):
                adapter = live_probe.PhysicalArvanS3ImmutabilityLiveProbe(
                    self._config(ir_receiver=scoped_ir)
                )
                with mock.patch.object(live_probe.os, "geteuid", return_value=0):
                    with self.assertRaisesRegex(
                        live_probe.PhysicalArvanS3ImmutabilityLiveProbeError,
                        "^ARVAN_S3_IMMUTABILITY_CREDENTIALS_NOT_SEPARATE$",
                    ):
                        adapter.collect(binding=self.binding, observed_at=NOW)
                self.assertEqual([], self.fi.calls)
                self.assertEqual([], self.ir.calls)

    def test_binding_drift_cannot_redirect_bucket_or_endpoint_before_any_call(self) -> None:
        adapter = live_probe.PhysicalArvanS3ImmutabilityLiveProbe(self._config())
        redirected = binding(
            endpoint="https://s3.ir-thr-at2.arvanstorage.ir",
            region="ir-thr-at2",
            bucket="other-private-bucket",
        )
        with mock.patch.object(live_probe.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(
                live_probe.PhysicalArvanS3ImmutabilityLiveProbeError,
                "^ARVAN_S3_IMMUTABILITY_BINDING_MISMATCH$",
            ):
                adapter.collect(binding=redirected, observed_at=NOW)
        self.assertEqual([], self.fi.calls)
        self.assertEqual([], self.ir.calls)
        self.assertNotIn(ENDPOINT, repr(self._config()))

    def test_wrong_version_and_external_error_fail_closed_without_secret_or_endpoint(self) -> None:
        self.fi.return_wrong_version = True
        with self.assertRaisesRegex(
            live_probe.PhysicalArvanS3ImmutabilityLiveProbeError,
            "^ARVAN_S3_IMMUTABILITY_EXACT_VERSION_READBACK_INVALID$",
        ):
            self._collect()

        self.setUp()
        self.fi.fail_operation = "get_bucket_versioning"
        with self.assertRaises(live_probe.PhysicalArvanS3ImmutabilityLiveProbeError) as raised:
            self._collect()
        self.assertEqual("ARVAN_S3_IMMUTABILITY_CLIENT_OPERATION_FAILED", raised.exception.code)
        self.assertNotIn("secret.invalid", str(raised.exception))
        self.assertNotIn("token", str(raised.exception))
        self.assertNotIn(ENDPOINT, str(raised.exception))

    def test_preflight_rejects_broader_allowed_operation_claims(self) -> None:
        raw = self._collect()
        broader_fi = replace(
            raw.credential_restrictions[0],
            allowed_operations=raw.credential_restrictions[0].allowed_operations
            + ("ListBucket",),
        )
        with self.assertRaises(preflight.PhysicalArvanImmutabilityPreflightError):
            preflight.build_physical_arvan_immutability_preflight_observation(
                binding=self.binding,
                versioning_status=raw.versioning_status,
                acl_posture=raw.acl_posture,
                retention_mode=raw.retention_mode,
                retention_policy_evidence_sha256=raw.retention_policy_evidence_sha256,
                retention_days=raw.retention_days,
                credential_restrictions=(
                    broader_fi,
                    raw.credential_restrictions[1],
                    raw.credential_restrictions[2],
                ),
                disposable_probe=raw.disposable_probe,
                observed_at=NOW,
            )

    def test_module_has_no_s3_sdk_network_or_subprocess_implementation(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertTrue(
            {
                "boto3",
                "botocore",
                "subprocess",
                "socket",
                "requests",
                "urllib",
                "http",
            }.isdisjoint(imports)
        )
        self.assertNotIn("generate_presigned", source)
        self.assertNotIn("create_subprocess", source)
        self.assertNotIn("os.system", source)


if __name__ == "__main__":
    unittest.main()
