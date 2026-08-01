from __future__ import annotations

from pathlib import Path
import os
import tempfile
import types
import unittest
from unittest import mock

import core.physical_arvan_s3_client_factory as factory_module


ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
REGION = "ir-thr-at1"
BUCKET = "private-physical-blobs"


class FakeRawClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.fail = False

    def get_object(self, **request: object):
        self.calls.append(("get_object", dict(request)))
        if self.fail:
            raise RuntimeError("synthetic secret failure")
        return {"ok": True}

    def get_bucket_versioning(self, **request: object):
        self.calls.append(("get_bucket_versioning", dict(request)))
        return {"Status": "Enabled"}

    def get_bucket_acl(self, **request: object):
        self.calls.append(("get_bucket_acl", dict(request)))
        return {"ok": True}

    def list_object_versions(self, **request: object):
        self.calls.append(("list_object_versions", dict(request)))
        return {"Versions": [], "DeleteMarkers": [], "IsTruncated": False}

    def put_object(self, **request: object):
        self.calls.append(("put_object", dict(request)))
        return {"VersionId": "version-1"}

    def head_object(self, **request: object):
        self.calls.append(("head_object", dict(request)))
        return {"VersionId": "version-1"}


class FakeSdk:
    def __init__(self) -> None:
        self.config_calls: list[dict[str, object]] = []
        self.session_calls: list[dict[str, object]] = []
        self.client_calls: list[tuple[str, dict[str, object]]] = []
        self.raw_clients: list[FakeRawClient] = []

        outer = self

        class Config:
            def __init__(self, **kwargs: object) -> None:
                outer.config_calls.append(dict(kwargs))

        class Session:
            def __init__(self, **kwargs: object) -> None:
                outer.session_calls.append(dict(kwargs))

            def client(self, name: str, **kwargs: object) -> FakeRawClient:
                outer.client_calls.append((name, dict(kwargs)))
                raw = FakeRawClient()
                outer.raw_clients.append(raw)
                return raw

        self.boto3 = types.SimpleNamespace(
            session=types.SimpleNamespace(Session=Session),
        )
        self.botocore_config = types.SimpleNamespace(Config=Config)


@unittest.skipUnless(os.geteuid() == 0, "client-factory contract explicitly requires root")
class PhysicalArvanS3ClientFactoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="physical-arvan-s3-client-")
        self.root = Path(self.temporary.name).resolve()
        os.chmod(self.root, 0o700)
        self.credentials = self.root / "arvan-s3-credentials.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_credentials(self, raw: bytes, *, mode: int = 0o600) -> None:
        self.credentials.write_bytes(raw)
        os.chmod(self.credentials, mode)

    @staticmethod
    def _config(**overrides: object) -> factory_module.RootOwnedArvanS3ClientFactoryConfig:
        values: dict[str, object] = {
            "endpoint": ENDPOINT,
            "region": REGION,
            "bucket": BUCKET,
            "enabled": True,
            "direct_site_control": "forbidden",
            "destination_object_ingest": "pull-only",
        }
        values.update(overrides)
        return factory_module.RootOwnedArvanS3ClientFactoryConfig(**values)

    def _owner(self, **overrides: object) -> factory_module.RootOwnedArvanS3ClientFactory:
        return factory_module.RootOwnedArvanS3ClientFactory(self._config(**overrides))

    def test_config_validation_is_pure_default_disabled_and_rejects_drift(self) -> None:
        with mock.patch.object(
            factory_module,
            "FIXED_ARVAN_S3_CREDENTIAL_FILE",
            self.credentials,
        ), mock.patch.object(factory_module, "_load_boto_sdk") as loader:
            owner = factory_module.RootOwnedArvanS3ClientFactory(
                self._config(enabled=False, endpoint=ENDPOINT + "/")
            )
            exact = owner.exact_pull_client_factory()
            publish = owner.physical_publish_client_factory()
            self.assertFalse(hasattr(factory_module, "boto3"))
            with self.assertRaisesRegex(factory_module.ArvanS3ClientFactoryError, "DISABLED"):
                exact(endpoint=ENDPOINT, region=REGION)
            with self.assertRaisesRegex(factory_module.ArvanS3ClientFactoryError, "DISABLED"):
                publish()
            loader.assert_not_called()

        accepted = factory_module.validate_root_owned_arvan_s3_client_factory_config(
            self._config(endpoint=ENDPOINT + "/")
        )
        self.assertEqual(ENDPOINT, accepted.endpoint)
        for bad in (
            self._config(endpoint="http://s3.ir-thr-at1.arvanstorage.ir"),
            self._config(endpoint=ENDPOINT + "/path"),
            self._config(region="wrong-region"),
            self._config(bucket="https://evil.invalid"),
            self._config(enabled=1),
        ):
            with self.assertRaises(factory_module.ArvanS3ClientFactoryError):
                factory_module.validate_root_owned_arvan_s3_client_factory_config(bad)
        with self.assertRaises(factory_module.ArvanS3ClientFactoryError):
            factory_module.validate_root_owned_arvan_s3_client_factory_config(
                self._config(enabled=True).__class__(
                    endpoint=ENDPOINT,
                    region=REGION,
                    bucket=BUCKET,
                    enabled=True,
                    direct_site_control="forbidden",
                    destination_object_ingest="pull-only",
                    schema="wrong",
                )
            )
        with self.assertRaises(factory_module.ArvanS3ClientFactoryError):
            factory_module.validate_root_owned_arvan_s3_client_factory_config(
                self._config(destination_object_ingest="wrong")
            )

    def test_credential_file_is_fixed_private_exact_and_errors_redact_material(self) -> None:
        self._write_credentials(
            b'{"access_key":"ACCESS-UNIQUE-NEVER-LOG","secret_key":"SECRET-UNIQUE-NEVER-LOG","extra":"x"}'
        )
        owner = self._owner()
        with mock.patch.object(factory_module, "FIXED_ARVAN_S3_CREDENTIAL_FILE", self.credentials), mock.patch.object(
            factory_module, "_load_boto_sdk"
        ) as loader:
            with self.assertRaises(factory_module.ArvanS3ClientFactoryError) as raised:
                owner.physical_publish_client_factory()()
            self.assertEqual("ARVAN_S3_FACTORY_CREDENTIAL_FILE_INVALID", raised.exception.code)
            self.assertNotIn("ACCESS-UNIQUE", str(raised.exception))
            self.assertNotIn("SECRET-UNIQUE", str(raised.exception))
            loader.assert_not_called()

        self._write_credentials(b'{"access_key":"ACCESS","secret_key":"SECRET"}', mode=0o644)
        with mock.patch.object(factory_module, "FIXED_ARVAN_S3_CREDENTIAL_FILE", self.credentials):
            with self.assertRaisesRegex(factory_module.ArvanS3ClientFactoryError, "CREDENTIAL_FILE"):
                owner.physical_publish_client_factory()()

        self.credentials.unlink()
        target = self.root / "actual-credentials.json"
        target.write_bytes(b'{"access_key":"ACCESS","secret_key":"SECRET"}')
        os.chmod(target, 0o600)
        self.credentials.symlink_to(target)
        with mock.patch.object(factory_module, "FIXED_ARVAN_S3_CREDENTIAL_FILE", self.credentials):
            with self.assertRaisesRegex(factory_module.ArvanS3ClientFactoryError, "CREDENTIAL_FILE"):
                owner.physical_publish_client_factory()()

    def test_exact_caller_drift_fails_before_sdk_or_credential_read(self) -> None:
        owner = self._owner()
        with mock.patch.object(factory_module, "FIXED_ARVAN_S3_CREDENTIAL_FILE", self.credentials), mock.patch.object(
            factory_module, "_load_boto_sdk"
        ) as loader:
            exact = owner.exact_pull_client_factory()
            with self.assertRaisesRegex(factory_module.ArvanS3ClientFactoryError, "ENDPOINT_MISMATCH"):
                exact(endpoint="https://s3.ir-thr-at1.arvanstorage.ir/other", region=REGION)
            with self.assertRaisesRegex(factory_module.ArvanS3ClientFactoryError, "REGION_MISMATCH"):
                exact(endpoint=ENDPOINT, region="elsewhere")
            loader.assert_not_called()

    def test_lazy_sdk_receipt_pinning_bounded_posture_and_bucket_scope(self) -> None:
        self._write_credentials(b'{"access_key":"ACCESSKEY123","secret_key":"SECRETKEY+/="}')
        sdk = FakeSdk()
        owner = self._owner()
        with mock.patch.object(factory_module, "FIXED_ARVAN_S3_CREDENTIAL_FILE", self.credentials), mock.patch.object(
            factory_module,
            "_load_boto_sdk",
            return_value=(sdk.boto3, sdk.botocore_config),
        ) as loader:
            exact = owner.exact_pull_client_factory()
            self.assertEqual([], sdk.session_calls)
            client = exact(endpoint=ENDPOINT, region=REGION)
            loader.assert_called_once_with()
            self.assertEqual(
                [{"aws_access_key_id": "ACCESSKEY123", "aws_secret_access_key": "SECRETKEY+/=", "region_name": REGION}],
                sdk.session_calls,
            )
            self.assertNotIn("aws_session_token", sdk.session_calls[0])
            self.assertNotIn("profile_name", sdk.session_calls[0])
            self.assertEqual(
                [
                    {
                        "signature_version": "s3v4",
                        "connect_timeout": factory_module.ARVAN_S3_CONNECT_TIMEOUT_SECONDS,
                        "read_timeout": factory_module.ARVAN_S3_READ_TIMEOUT_SECONDS,
                        "retries": {"max_attempts": factory_module.ARVAN_S3_MAX_ATTEMPTS, "mode": "standard"},
                        "s3": {"addressing_style": "path"},
                        "proxies": {},
                    }
                ],
                sdk.config_calls,
            )
            self.assertEqual("s3", sdk.client_calls[0][0])
            self.assertEqual(ENDPOINT, sdk.client_calls[0][1]["endpoint_url"])
            self.assertEqual(REGION, sdk.client_calls[0][1]["region_name"])
            self.assertTrue(sdk.client_calls[0][1]["use_ssl"])
            self.assertTrue(sdk.client_calls[0][1]["verify"])
            with self.assertRaisesRegex(factory_module.ArvanS3ClientFactoryError, "BUCKET_MISMATCH"):
                client.get_object(Bucket="another-bucket", Key="x", VersionId="v")
            self.assertEqual([], sdk.raw_clients[0].calls)
            self.assertEqual(
                {"ok": True},
                client.get_object(Bucket=BUCKET, Key="object/key", VersionId="version-1"),
            )
            self.assertEqual(
                [("get_object", {"Bucket": BUCKET, "Key": "object/key", "VersionId": "version-1"})],
                sdk.raw_clients[0].calls,
            )
            published = owner.physical_publish_client_factory()()
            self.assertEqual(2, len(sdk.session_calls))
            self.assertEqual({"Status": "Enabled"}, published.get_bucket_versioning(Bucket=BUCKET))
            sdk.raw_clients[1].fail = True
            with self.assertRaises(factory_module.ArvanS3ClientFactoryError) as raised:
                published.get_object(Bucket=BUCKET, Key="x", VersionId="v")
            self.assertEqual("ARVAN_S3_FACTORY_CLIENT_OPERATION_FAILED", raised.exception.code)
            self.assertNotIn("synthetic secret failure", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
