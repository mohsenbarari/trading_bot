"""Adversarial local tests for the reverse, separate Arvan identities."""

from __future__ import annotations

import ast
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest import mock

from core import physical_arvan_s3_failback_separated_credential_loader as loader


ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
REGION = "ir-thr-at1"
BUCKET = "private-physical-recovery"
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_arvan_s3_failback_separated_credential_loader.py"
)
_IR_ACCESS = "IR-PUBLISHER-ACCESS-UNIQUE-20260731"
_IR_SECRET = "IR-PUBLISHER-SECRET-UNIQUE-20260731"
_FI_ACCESS = "FI-RECEIVER-ACCESS-UNIQUE-20260731"
_FI_SECRET = "FI-RECEIVER-SECRET-UNIQUE-20260731"


def _identity(access_key: str) -> str:
    return hashlib.sha256(
        b"gold-trade-arvan-s3-machine-user-identity-v1\x00" + access_key.encode("ascii")
    ).hexdigest()


@unittest.skipUnless(os.geteuid() == 0, "root-owned credential files require root")
class PhysicalArvanS3FailbackSeparatedCredentialLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="arvan-failback-credentials-")
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)
        self.security = self.root / "security"
        self.security.mkdir(mode=0o700)
        self.security.chmod(0o700)
        self.ir_file = self.security / "arvan-s3-ir-publisher-credentials.json"
        self.fi_file = self.security / "arvan-s3-fi-receiver-credentials.json"
        self._write(
            self.ir_file,
            role="ir-publisher",
            action_profile="ir-publisher-immutable-create-only-v1",
            access_key=_IR_ACCESS,
            secret_key=_IR_SECRET,
        )
        self._write(
            self.fi_file,
            role="fi-receiver",
            action_profile="fi-receiver-exact-readonly-v1",
            access_key=_FI_ACCESS,
            secret_key=_FI_SECRET,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(
        self,
        path: Path,
        *,
        role: str,
        action_profile: str,
        access_key: str,
        secret_key: str,
        mode: int = 0o600,
        raw: bytes | None = None,
    ) -> None:
        if raw is None:
            raw = json.dumps(
                {
                    "schema": loader.PHYSICAL_ARVAN_S3_MACHINE_USER_CREDENTIAL_SCHEMA,
                    "role": role,
                    "action_profile": action_profile,
                    "access_key": access_key,
                    "secret_key": secret_key,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
        path.write_bytes(raw)
        path.chmod(mode)

    @staticmethod
    def _config(**overrides: object) -> loader.RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig:
        values: dict[str, object] = {
            "endpoint": ENDPOINT,
            "region": REGION,
            "bucket": BUCKET,
            "enabled": True,
            "source_site": "webapp_ir",
            "destination_site": "webapp_fi",
            "object_storage_namespace": "physical-failback",
            "ir_publisher_action_profile": "ir-publisher-immutable-create-only-v1",
            "fi_receiver_action_profile": "fi-receiver-exact-readonly-v1",
            "direct_site_control": "forbidden",
            "destination_object_ingest": "pull-only",
        }
        values.update(overrides)
        return loader.RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig(**values)

    def _patched_paths(self):
        return mock.patch.multiple(
            loader,
            FIXED_ARVAN_S3_IR_PUBLISHER_CREDENTIAL_FILE=self.ir_file,
            FIXED_ARVAN_S3_FI_RECEIVER_CREDENTIAL_FILE=self.fi_file,
        )

    def _load(self, config: object | None = None):
        with self._patched_paths():
            return loader.load_root_owned_arvan_s3_failback_separated_credential_pair(
                self._config() if config is None else config
            )

    def test_pair_is_reverse_only_separated_and_redacted(self) -> None:
        pair = self._load()
        with self._patched_paths():
            projection = loader.project_root_owned_arvan_s3_failback_separated_credentials(
                pair, config=self._config()
            )
        self.assertEqual("webapp_ir", pair.source_site)
        self.assertEqual("webapp_fi", pair.destination_site)
        self.assertEqual("physical-failback", pair.object_storage_namespace)
        self.assertEqual(_identity(_IR_ACCESS), pair.ir_publisher_identity_sha256)
        self.assertEqual(_identity(_FI_ACCESS), pair.fi_receiver_identity_sha256)
        self.assertNotEqual(pair.ir_publisher_identity_sha256, pair.fi_receiver_identity_sha256)
        self.assertEqual("ir-publisher", projection.ir_publisher_role)
        self.assertEqual("fi-receiver", projection.fi_receiver_role)
        self.assertEqual(
            (
                "GetBucketAcl",
                "GetBucketVersioning",
                "PutObject:create-only",
                "ListObjectVersions:exact-key",
                "GetObject:exact-version",
                "HeadObject:exact-version",
            ),
            projection.ir_publisher_allowed_operations,
        )
        self.assertEqual(
            ("GetObject:exact-version", "HeadObject:exact-version"),
            projection.fi_receiver_allowed_operations,
        )
        public = repr(pair) + repr(projection) + repr(asdict(pair))
        for value in (_IR_ACCESS, _IR_SECRET, _FI_ACCESS, _FI_SECRET, str(self.ir_file), str(self.fi_file)):
            self.assertNotIn(value, public)
        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
            pickle.dumps(pair)

    def test_publisher_and_receiver_local_private_helpers_open_only_their_own_file(self) -> None:
        absent = self.security / "must-not-be-opened.json"
        with mock.patch.object(
            loader, "FIXED_ARVAN_S3_IR_PUBLISHER_CREDENTIAL_FILE", self.ir_file
        ), mock.patch.object(
            loader, "FIXED_ARVAN_S3_FI_RECEIVER_CREDENTIAL_FILE", absent
        ), mock.patch.object(loader, "_load_credential", wraps=loader._load_credential) as opened:
            facts, publisher = loader._load_root_owned_ir_publisher_credential_facts(self._config())  # type: ignore[attr-defined]
        self.assertEqual(ENDPOINT, facts.endpoint)
        self.assertEqual(_identity(_IR_ACCESS), publisher.identity_sha256)
        self.assertEqual(
            [mock.call(self.ir_file, expected_role="ir-publisher", expected_action_profile="ir-publisher-immutable-create-only-v1")],
            opened.call_args_list,
        )

        with mock.patch.object(
            loader, "FIXED_ARVAN_S3_IR_PUBLISHER_CREDENTIAL_FILE", absent
        ), mock.patch.object(
            loader, "FIXED_ARVAN_S3_FI_RECEIVER_CREDENTIAL_FILE", self.fi_file
        ), mock.patch.object(loader, "_load_credential", wraps=loader._load_credential) as opened:
            _facts, receiver = loader._load_root_owned_fi_receiver_credential_facts(self._config())  # type: ignore[attr-defined]
        self.assertEqual(_identity(_FI_ACCESS), receiver.identity_sha256)
        self.assertEqual(
            [mock.call(self.fi_file, expected_role="fi-receiver", expected_action_profile="fi-receiver-exact-readonly-v1")],
            opened.call_args_list,
        )

    def test_disabled_nonroot_and_collision_fail_before_opening_secret(self) -> None:
        with self._patched_paths(), mock.patch.object(loader, "_load_credential") as opened:
            with self.assertRaisesRegex(
                loader.ArvanS3FailbackSeparatedCredentialLoaderError,
                "^ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_LOADER_DISABLED$",
            ):
                loader.load_root_owned_arvan_s3_failback_separated_credential_pair(self._config(enabled=False))
            opened.assert_not_called()

        with self._patched_paths(), mock.patch.object(loader.os, "geteuid", return_value=1000), mock.patch.object(
            loader, "_load_credential"
        ) as opened:
            with self.assertRaisesRegex(
                loader.ArvanS3FailbackSeparatedCredentialLoaderError,
                "^ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_ROOT_REQUIRED$",
            ):
                loader.load_root_owned_arvan_s3_failback_separated_credential_pair(self._config())
            opened.assert_not_called()

        with mock.patch.object(
            loader, "FIXED_ARVAN_S3_IR_PUBLISHER_CREDENTIAL_FILE", self.ir_file
        ), mock.patch.object(
            loader, "FIXED_ARVAN_S3_FI_RECEIVER_CREDENTIAL_FILE", self.ir_file
        ), mock.patch.object(loader, "_load_credential") as opened:
            with self.assertRaisesRegex(
                loader.ArvanS3FailbackSeparatedCredentialLoaderError,
                "^ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_FILE_COLLISION$",
            ):
                loader.load_root_owned_arvan_s3_failback_separated_credential_pair(self._config())
            opened.assert_not_called()

    def test_route_profiles_namespace_and_endpoint_are_pinned(self) -> None:
        disabled = self._config(enabled=False, endpoint=ENDPOINT + "/")
        normalized = loader.validate_root_owned_arvan_s3_failback_separated_credential_loader_config(disabled)
        self.assertFalse(normalized.enabled)
        self.assertEqual(ENDPOINT, normalized.endpoint)
        for bad in (
            self._config(source_site="webapp_fi"),
            self._config(destination_site="webapp_ir"),
            self._config(object_storage_namespace="physical-wal"),
            self._config(ir_publisher_action_profile="fi-publisher-immutable-preflight-v1"),
            self._config(fi_receiver_action_profile="put-object"),
            self._config(direct_site_control="allowed"),
            self._config(destination_object_ingest="push"),
            self._config(region="wrong"),
            self._config(endpoint="http://s3.ir-thr-at1.arvanstorage.ir"),
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(loader.ArvanS3FailbackSeparatedCredentialLoaderError):
                    loader.validate_root_owned_arvan_s3_failback_separated_credential_loader_config(bad)

    def test_unsafe_file_role_shape_and_duplicate_identity_fail_closed(self) -> None:
        self.ir_file.chmod(0o640)
        with self.assertRaisesRegex(
            loader.ArvanS3FailbackSeparatedCredentialLoaderError,
            "^ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_FILE_INVALID$",
        ):
            self._load()
        self.ir_file.chmod(0o600)
        self._write(
            self.ir_file,
            role="fi-receiver",
            action_profile="ir-publisher-immutable-create-only-v1",
            access_key=_IR_ACCESS,
            secret_key=_IR_SECRET,
        )
        with self.assertRaisesRegex(
            loader.ArvanS3FailbackSeparatedCredentialLoaderError,
            "^ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_SCOPE_INVALID$",
        ):
            self._load()
        self._write(
            self.ir_file,
            role="ir-publisher",
            action_profile="ir-publisher-immutable-create-only-v1",
            access_key=_FI_ACCESS,
            secret_key="DIFFERENT-IR-SECRET",
        )
        with self.assertRaisesRegex(
            loader.ArvanS3FailbackSeparatedCredentialLoaderError,
            "^ARVAN_S3_FAILBACK_SEPARATED_CREDENTIALS_NOT_SEPARATE$",
        ):
            self._load()

    def test_pair_is_config_bound_and_tamper_fails(self) -> None:
        pair = self._load()
        with self._patched_paths():
            self.assertIs(
                pair,
                loader.require_verified_arvan_s3_failback_separated_credential_pair(
                    pair, config=self._config()
                ),
            )
            with self.assertRaisesRegex(
                loader.ArvanS3FailbackSeparatedCredentialLoaderError,
                "^ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_PAIR_TAMPERED$",
            ):
                loader.require_verified_arvan_s3_failback_separated_credential_pair(
                    pair, config=self._config(bucket="another-private-bucket")
                )
            object.__setattr__(pair, "ir_publisher_identity_sha256", pair.fi_receiver_identity_sha256)
            with self.assertRaises(loader.ArvanS3FailbackSeparatedCredentialLoaderError):
                loader.project_root_owned_arvan_s3_failback_separated_credentials(pair, config=self._config())

    def test_source_has_no_sdk_network_or_normal_credential_paths(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(MODULE_PATH))
        imports: set[str] = set()
        calls: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Call):
                calls.add(node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else "")
        self.assertFalse(imports & {"boto3", "botocore", "socket", "subprocess", "requests", "paramiko"})
        self.assertFalse(calls & {"client", "connect", "get_object", "put_object", "run", "Popen"})
        self.assertNotIn("arvan-s3-fi-publisher-credentials.json", source)
        self.assertNotIn("arvan-s3-ir-receiver-credentials.json", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
