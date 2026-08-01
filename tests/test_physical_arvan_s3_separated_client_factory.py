"""Injected-SDK tests for the root-only paired Arvan client factory.

The fake SDK and fake S3-shaped clients below stay entirely in process.  No
test imports boto3, contacts Arvan, opens a network socket, or runs Docker.
"""

from __future__ import annotations

import ast
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

from core import physical_arvan_s3_immutability_live_probe as live_probe
from core import physical_arvan_s3_separated_client_factory as factory
from core import physical_arvan_s3_separated_credential_loader as credentials
from core.physical_arvan_immutability_preflight import PhysicalArvanImmutabilityPreflightBinding


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
REGION = "ir-thr-at1"
BUCKET = "private-physical-recovery"
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_arvan_s3_separated_client_factory.py"
)

_FI_ACCESS = "FI-ACCESS-UNIQUE-20260731"
_FI_SECRET = "FI-SECRET-UNIQUE-20260731"
_IR_ACCESS = "IR-ACCESS-UNIQUE-20260731"
_IR_SECRET = "IR-SECRET-UNIQUE-20260731"


def _identity(access_key: str) -> str:
    return hashlib.sha256(
        b"gold-trade-arvan-s3-machine-user-identity-v1\x00"
        + access_key.encode("ascii")
    ).hexdigest()


def _binding(**changes: object) -> PhysicalArvanImmutabilityPreflightBinding:
    fields: dict[str, object] = {
        "campaign_id": "physical-arvan-client-pair-20260731",
        "release_sha": "3138d0c2a8d20a84042c3a438fbc88db7a4db498",
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "route_binding_sha256": "a" * 64,
        "endpoint": ENDPOINT,
        "region": REGION,
        "bucket": BUCKET,
        "minimum_retention_days": 90,
    }
    fields.update(changes)
    return PhysicalArvanImmutabilityPreflightBinding(**fields)


class _MemoryBody:
    def __init__(self, payload: bytes) -> None:
        self._stream = io.BytesIO(payload)

    def read(self, amount: int = -1) -> bytes:
        return self._stream.read(amount)


class _SharedObject:
    def __init__(self) -> None:
        self.key: str | None = None
        self.version_id = "separated-client-probe-version-20260731"
        self.payload: bytes | None = None
        self.retention_until: datetime | None = None
        self.checksum_b64: str | None = None


class _RawS3Client:
    def __init__(self, *, role: str, shared: _SharedObject) -> None:
        self.role = role
        self.shared = shared
        self.calls: list[tuple[str, dict[str, object]]] = []

    def _record(self, method: str, request: dict[str, object]) -> None:
        self.calls.append((method, request))

    def _payload(self) -> bytes:
        assert self.shared.payload is not None
        return self.shared.payload

    def _retention(self) -> datetime:
        assert self.shared.retention_until is not None
        return self.shared.retention_until

    def _metadata(self) -> dict[str, object]:
        payload = self._payload()
        return {
            "VersionId": self.shared.version_id,
            "ContentLength": len(payload),
            "ContentType": "application/octet-stream",
            "CacheControl": "no-store",
            "ChecksumSHA256": self.shared.checksum_b64,
            "ObjectLockMode": "COMPLIANCE",
            "ObjectLockRetainUntilDate": self._retention(),
        }

    def get_bucket_acl(self, **request: object) -> dict[str, object]:
        self._record("get_bucket_acl", dict(request))
        return {
            "Owner": {"ID": "canonical-owner"},
            "Grants": [
                {
                    "Grantee": {"Type": "CanonicalUser", "ID": "canonical-owner"},
                    "Permission": "FULL_CONTROL",
                }
            ],
        }

    def get_bucket_versioning(self, **request: object) -> dict[str, object]:
        self._record("get_bucket_versioning", dict(request))
        return {"Status": "Enabled"}

    def get_object_lock_configuration(self, **request: object) -> dict[str, object]:
        self._record("get_object_lock_configuration", dict(request))
        return {
            "ObjectLockConfiguration": {
                "ObjectLockEnabled": "Enabled",
                "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 180}},
            }
        }

    def put_object(self, **request: object) -> dict[str, object]:
        copied = dict(request)
        self._record("put_object", copied)
        if (
            self.role == "fi"
            and self.shared.key is None
            and copied.get("IfNoneMatch") == "*"
        ):
            payload = copied.get("Body")
            retention = copied.get("ObjectLockRetainUntilDate")
            checksum = copied.get("ChecksumSHA256")
            if not isinstance(payload, bytes) or not isinstance(retention, datetime) or not isinstance(checksum, str):
                raise AssertionError("factory/live probe request drift")
            self.shared.key = copied.get("Key") if isinstance(copied.get("Key"), str) else None
            self.shared.payload = payload
            self.shared.retention_until = retention
            self.shared.checksum_b64 = checksum
            return {"VersionId": self.shared.version_id}
        raise live_probe.InjectedS3AccessDenied()

    def list_object_versions(self, **request: object) -> dict[str, object]:
        self._record("list_object_versions", dict(request))
        if self.role != "fi":
            raise live_probe.InjectedS3AccessDenied()
        return {
            "Versions": [
                {
                    "Key": self.shared.key,
                    "VersionId": self.shared.version_id,
                    "IsLatest": True,
                    "Size": len(self._payload()),
                }
            ],
            "DeleteMarkers": [],
            "IsTruncated": False,
        }

    def list_objects_v2(self, **request: object) -> dict[str, object]:
        self._record("list_objects_v2", dict(request))
        raise live_probe.InjectedS3AccessDenied()

    def get_object_retention(self, **request: object) -> dict[str, object]:
        self._record("get_object_retention", dict(request))
        return {"Retention": {"Mode": "COMPLIANCE", "RetainUntilDate": self._retention()}}

    def head_object(self, **request: object) -> dict[str, object]:
        self._record("head_object", dict(request))
        return self._metadata()

    def get_object(self, **request: object) -> dict[str, object]:
        self._record("get_object", dict(request))
        payload = self._payload()
        return {
            **self._metadata(),
            "ContentRange": f"bytes 0-{len(payload) - 1}/{len(payload)}",
            "AcceptRanges": "bytes",
            "Body": _MemoryBody(payload),
        }

    def delete_object(self, **request: object) -> dict[str, object]:
        self._record("delete_object", dict(request))
        raise live_probe.InjectedS3AccessDenied()


class _FakeSdk:
    def __init__(self, *, same_raw_client: bool = False) -> None:
        self.shared = _SharedObject()
        self.fi = _RawS3Client(role="fi", shared=self.shared)
        self.ir = _RawS3Client(role="ir", shared=self.shared)
        self.same_raw_client = same_raw_client
        self.config_calls: list[dict[str, object]] = []
        self.session_calls: list[dict[str, object]] = []
        self.client_calls: list[tuple[str, dict[str, object]]] = []
        outer = self

        class _Config:
            def __init__(self, **kwargs: object) -> None:
                outer.config_calls.append(dict(kwargs))

        class _Session:
            def __init__(self, **kwargs: object) -> None:
                outer.session_calls.append(dict(kwargs))
                self.access_key = kwargs.get("aws_access_key_id")

            def client(self, name: str, **kwargs: object) -> _RawS3Client:
                outer.client_calls.append((name, dict(kwargs)))
                if self.access_key == _FI_ACCESS:
                    return outer.fi
                if self.access_key == _IR_ACCESS:
                    return outer.fi if outer.same_raw_client else outer.ir
                raise AssertionError("unexpected credential input")

        self.boto3 = types.SimpleNamespace(session=types.SimpleNamespace(Session=_Session))
        self.botocore_config = types.SimpleNamespace(Config=_Config)


@unittest.skipUnless(os.geteuid() == 0, "root-only paired client factory requires root")
class PhysicalArvanS3SeparatedClientFactoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="arvan-separated-client-factory-")
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)
        self.security = self.root / "security"
        self.security.mkdir(mode=0o700)
        self.security.chmod(0o700)
        self.fi_file = self.security / "fi.json"
        self.ir_file = self.security / "ir.json"
        self._write(self.fi_file, "fi-publisher", "fi-publisher-immutable-create-only-v1", _FI_ACCESS, _FI_SECRET)
        self._write(self.ir_file, "ir-receiver", "ir-receiver-exact-readonly-v1", _IR_ACCESS, _IR_SECRET)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write(path: Path, role: str, profile: str, access_key: str, secret_key: str) -> None:
        path.write_bytes(
            json.dumps(
                {
                    "schema": credentials.PHYSICAL_ARVAN_S3_MACHINE_USER_CREDENTIAL_SCHEMA,
                    "role": role,
                    "action_profile": profile,
                    "access_key": access_key,
                    "secret_key": secret_key,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        )
        path.chmod(0o600)

    def _loader_config(self, **changes: object) -> credentials.RootOwnedArvanS3SeparatedCredentialLoaderConfig:
        fields: dict[str, object] = {
            "endpoint": ENDPOINT,
            "region": REGION,
            "bucket": BUCKET,
            "enabled": True,
        }
        fields.update(changes)
        return credentials.RootOwnedArvanS3SeparatedCredentialLoaderConfig(**fields)

    def _config(self, **changes: object) -> factory.RootOwnedArvanS3SeparatedClientFactoryConfig:
        fields: dict[str, object] = {
            "credential_loader_config": self._loader_config(),
            "enabled": True,
        }
        fields.update(changes)
        return factory.RootOwnedArvanS3SeparatedClientFactoryConfig(**fields)

    def _owner(self, **changes: object) -> factory.RootOwnedArvanS3SeparatedClientFactory:
        return factory.RootOwnedArvanS3SeparatedClientFactory(self._config(**changes))

    def _file_patches(self):
        return mock.patch.multiple(
            credentials,
            FIXED_ARVAN_S3_FI_PUBLISHER_CREDENTIAL_FILE=self.fi_file,
            FIXED_ARVAN_S3_IR_RECEIVER_CREDENTIAL_FILE=self.ir_file,
        )

    def _collect(
        self,
        owner: factory.RootOwnedArvanS3SeparatedClientFactory,
        sdk: _FakeSdk,
        *,
        binding: PhysicalArvanImmutabilityPreflightBinding | None = None,
    ):
        with self._file_patches(), mock.patch.object(
            factory,
            "_load_boto_sdk",
            return_value=(sdk.boto3, sdk.botocore_config),
        ), mock.patch.object(
            live_probe.secrets,
            "token_bytes",
            return_value=b"x" * live_probe._ROOT_PINNED_RANDOM_BYTES,
        ), mock.patch.object(live_probe.secrets, "token_hex", return_value="1" * 32):
            return owner.collect_immutability_preflight(
                binding=_binding() if binding is None else binding,
                observed_at=NOW,
            )

    def test_two_transient_clients_feed_existing_live_probe_and_only_public_projection_escapes(self) -> None:
        owner = self._owner()
        sdk = _FakeSdk()
        with self._file_patches(), mock.patch.object(factory, "_load_boto_sdk") as sdk_loader:
            projection = owner.credential_projection()
            sdk_loader.assert_not_called()
        self.assertEqual("fi-publisher", projection.fi_publisher_role)
        self.assertEqual("ir-receiver", projection.ir_receiver_role)
        self.assertEqual(_identity(_FI_ACCESS), projection.fi_publisher_identity_sha256)
        self.assertEqual(_identity(_IR_ACCESS), projection.ir_receiver_identity_sha256)
        self.assertEqual(live_probe._FI_ALLOWED_OPERATIONS, projection.fi_publisher_allowed_operations)
        self.assertEqual(live_probe._IR_ALLOWED_OPERATIONS, projection.ir_receiver_allowed_operations)
        self.assertEqual(
            {
                "schema",
                "fi_publisher_role",
                "fi_publisher_identity_sha256",
                "fi_publisher_action_profile",
                "fi_publisher_allowed_operations",
                "ir_receiver_role",
                "ir_receiver_identity_sha256",
                "ir_receiver_action_profile",
                "ir_receiver_allowed_operations",
            },
            set(asdict(projection)),
        )
        rendered = repr(projection) + repr(asdict(projection))
        for secret_or_path in (_FI_ACCESS, _FI_SECRET, _IR_ACCESS, _IR_SECRET, str(self.fi_file), str(self.ir_file), ENDPOINT, BUCKET):
            self.assertNotIn(secret_or_path, rendered)

        observation = self._collect(owner, sdk)
        self.assertEqual(
            (_identity(_FI_ACCESS), _identity(_IR_ACCESS)),
            tuple(item.credential_identity_sha256 for item in observation.credential_restrictions[:2]),
        )
        self.assertEqual(2, len(sdk.session_calls))
        self.assertEqual({_FI_ACCESS, _IR_ACCESS}, {item["aws_access_key_id"] for item in sdk.session_calls})
        self.assertEqual({_FI_SECRET, _IR_SECRET}, {item["aws_secret_access_key"] for item in sdk.session_calls})
        self.assertEqual(2, len(sdk.client_calls))
        for name, request in sdk.client_calls:
            self.assertEqual("s3", name)
            self.assertEqual(ENDPOINT, request["endpoint_url"])
            self.assertEqual(REGION, request["region_name"])
            self.assertTrue(request["use_ssl"])
            self.assertTrue(request["verify"])
        self.assertIsNot(sdk.fi, sdk.ir)
        self.assertTrue(any(method == "put_object" for method, _ in sdk.fi.calls))
        self.assertTrue(any(method == "get_object" for method, _ in sdk.ir.calls))
        self.assertEqual({"_config"}, set(vars(owner)))

    def test_role_local_identity_projections_open_only_their_own_credential(self) -> None:
        owner = self._owner()
        absent = self.security / "must-not-open.json"
        with mock.patch.object(credentials, "FIXED_ARVAN_S3_FI_PUBLISHER_CREDENTIAL_FILE", self.fi_file), mock.patch.object(
            credentials, "FIXED_ARVAN_S3_IR_RECEIVER_CREDENTIAL_FILE", absent
        ), mock.patch.object(credentials, "_load_credential", wraps=credentials._load_credential) as opened, mock.patch.object(
            factory, "_load_boto_sdk"
        ) as sdk_loader:
            fi = owner.fi_publisher_identity_projection()
        self.assertEqual("fi-publisher", fi.role)
        self.assertEqual(_identity(_FI_ACCESS), fi.identity_sha256)
        self.assertEqual(credentials.ARVAN_S3_FI_PUBLISHER_EXPECTED_PROBE_ACTIONS, fi.allowed_operations)
        self.assertEqual(
            [mock.call(self.fi_file, expected_role="fi-publisher", expected_action_profile="fi-publisher-immutable-create-only-v1")],
            opened.call_args_list,
        )
        sdk_loader.assert_not_called()

        with mock.patch.object(credentials, "FIXED_ARVAN_S3_FI_PUBLISHER_CREDENTIAL_FILE", absent), mock.patch.object(
            credentials, "FIXED_ARVAN_S3_IR_RECEIVER_CREDENTIAL_FILE", self.ir_file
        ), mock.patch.object(credentials, "_load_credential", wraps=credentials._load_credential) as opened, mock.patch.object(
            factory, "_load_boto_sdk"
        ) as sdk_loader:
            ir = owner.ir_receiver_identity_projection()
        self.assertEqual("ir-receiver", ir.role)
        self.assertEqual(_identity(_IR_ACCESS), ir.identity_sha256)
        self.assertEqual(credentials.ARVAN_S3_IR_RECEIVER_EXPECTED_PROBE_ACTIONS, ir.allowed_operations)
        self.assertEqual(
            [mock.call(self.ir_file, expected_role="ir-receiver", expected_action_profile="ir-receiver-exact-readonly-v1")],
            opened.call_args_list,
        )
        sdk_loader.assert_not_called()

    def test_disabled_nonroot_binding_and_credential_failures_happen_before_sdk_or_probe(self) -> None:
        disabled = factory.RootOwnedArvanS3SeparatedClientFactory(
            self._config(
                enabled=False,
                credential_loader_config=self._loader_config(enabled=False),
            )
        )
        sdk = _FakeSdk()
        with mock.patch.object(factory, "_load_boto_sdk") as sdk_loader, mock.patch.object(
            credentials,
            "_load_root_owned_separated_credential_facts",
        ) as credential_load:
            with self.assertRaisesRegex(factory.ArvanS3SeparatedClientFactoryError, "^ARVAN_S3_SEPARATED_CLIENT_FACTORY_DISABLED$"):
                disabled.collect_immutability_preflight(binding=_binding(), observed_at=NOW)
            sdk_loader.assert_not_called()
            credential_load.assert_not_called()

        owner = self._owner()
        with mock.patch.object(factory, "_load_boto_sdk") as sdk_loader, mock.patch.object(
            credentials,
            "_load_root_owned_separated_credential_facts",
        ) as credential_load:
            with self.assertRaisesRegex(factory.ArvanS3SeparatedClientFactoryError, "^ARVAN_S3_SEPARATED_CLIENT_FACTORY_BINDING_MISMATCH$"):
                owner.collect_immutability_preflight(
                    binding=_binding(bucket="another-private-bucket"),
                    observed_at=NOW,
                )
            sdk_loader.assert_not_called()
            credential_load.assert_not_called()
        self.assertEqual([], sdk.fi.calls)
        self.assertEqual([], sdk.ir.calls)

        self._write(self.ir_file, "ir-receiver", "ir-receiver-exact-readonly-v1", _FI_ACCESS, "IR-DIFFERENT-SECRET")
        with self._file_patches(), mock.patch.object(factory, "_load_boto_sdk") as sdk_loader:
            with self.assertRaisesRegex(factory.ArvanS3SeparatedClientFactoryError, "^ARVAN_S3_SEPARATED_CLIENT_FACTORY_CREDENTIAL_ADMISSION_FAILED$"):
                owner.collect_immutability_preflight(binding=_binding(), observed_at=NOW)
            sdk_loader.assert_not_called()

    def test_same_raw_client_is_refused_before_the_live_probe_uses_it(self) -> None:
        owner = self._owner()
        sdk = _FakeSdk(same_raw_client=True)
        with self._file_patches(), mock.patch.object(
            factory,
            "_load_boto_sdk",
            return_value=(sdk.boto3, sdk.botocore_config),
        ):
            with self.assertRaisesRegex(factory.ArvanS3SeparatedClientFactoryError, "^ARVAN_S3_SEPARATED_CLIENT_FACTORY_CLIENTS_NOT_SEPARATE$"):
                owner.collect_immutability_preflight(binding=_binding(), observed_at=NOW)
        self.assertEqual([], sdk.fi.calls)
        self.assertEqual([], sdk.ir.calls)

    def test_config_and_runtime_are_root_only_and_nonserializing(self) -> None:
        config = self._config()
        self.assertNotIn(_FI_SECRET, repr(config))
        for bad in (
            self._config(client_construction_mode="legacy-single-client"),
            self._config(credential_loader_config=self._loader_config(enabled=False)),
            factory.RootOwnedArvanS3SeparatedClientFactoryConfig(),
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(factory.ArvanS3SeparatedClientFactoryError):
                    factory.validate_root_owned_arvan_s3_separated_client_factory_config(bad)

        owner = self._owner()
        with mock.patch.object(factory.os, "geteuid", return_value=1000), mock.patch.object(
            credentials,
            "_load_root_owned_separated_credential_facts",
        ) as credential_load, mock.patch.object(factory, "_load_boto_sdk") as sdk_loader:
            with self.assertRaisesRegex(factory.ArvanS3SeparatedClientFactoryError, "^ARVAN_S3_SEPARATED_CLIENT_FACTORY_ROOT_REQUIRED$"):
                owner.collect_immutability_preflight(binding=_binding(), observed_at=NOW)
            credential_load.assert_not_called()
            sdk_loader.assert_not_called()

    def test_source_has_no_direct_sdk_network_or_command_import(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(MODULE_PATH))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        self.assertTrue(imports.issubset({
            "__future__", "collections", "dataclasses", "datetime", "importlib", "os", "typing", "core",
        }))
        self.assertFalse(imports & {
            "boto3", "botocore", "paramiko", "requests", "socket", "subprocess", "urllib",
        })
        self.assertNotIn("generate_presigned", source)
        self.assertNotIn("os.system", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
