"""Adversarial local tests for separated Arvan machine-user admission.

Only temporary files with synthetic credential-shaped strings are used.  The
module under test never imports an SDK or invokes an S3 client.
"""

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

from core import physical_arvan_s3_immutability_live_probe as live_probe
from core import physical_arvan_s3_separated_credential_loader as loader


ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
REGION = "ir-thr-at1"
BUCKET = "private-physical-recovery"
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_arvan_s3_separated_credential_loader.py"
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


@unittest.skipUnless(os.geteuid() == 0, "root-owned credential files require root")
class PhysicalArvanS3SeparatedCredentialLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="arvan-separated-credentials-")
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)
        self.security = self.root / "security"
        self.security.mkdir(mode=0o700)
        self.security.chmod(0o700)
        self.fi_file = self.security / "arvan-s3-fi-publisher-credentials.json"
        self.ir_file = self.security / "arvan-s3-ir-receiver-credentials.json"
        self._write_credential(
            self.fi_file,
            role="fi-publisher",
            action_profile="fi-publisher-immutable-create-only-v1",
            access_key=_FI_ACCESS,
            secret_key=_FI_SECRET,
        )
        self._write_credential(
            self.ir_file,
            role="ir-receiver",
            action_profile="ir-receiver-exact-readonly-v1",
            access_key=_IR_ACCESS,
            secret_key=_IR_SECRET,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_credential(
        self,
        path: Path,
        *,
        role: str,
        action_profile: str,
        access_key: str,
        secret_key: str,
        raw: bytes | None = None,
        mode: int = 0o600,
    ) -> None:
        payload = raw
        if payload is None:
            payload = json.dumps(
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
        path.write_bytes(payload)
        path.chmod(mode)

    @staticmethod
    def _config(**overrides: object) -> loader.RootOwnedArvanS3SeparatedCredentialLoaderConfig:
        values: dict[str, object] = {
            "endpoint": ENDPOINT,
            "region": REGION,
            "bucket": BUCKET,
            "enabled": True,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "fi_publisher_action_profile": "fi-publisher-immutable-create-only-v1",
            "ir_receiver_action_profile": "ir-receiver-exact-readonly-v1",
            "direct_site_control": "forbidden",
            "destination_object_ingest": "pull-only",
        }
        values.update(overrides)
        return loader.RootOwnedArvanS3SeparatedCredentialLoaderConfig(**values)

    def _load(
        self,
        config: loader.RootOwnedArvanS3SeparatedCredentialLoaderConfig | None = None,
    ) -> loader.VerifiedArvanS3SeparatedCredentialPair:
        with mock.patch.object(
            loader,
            "FIXED_ARVAN_S3_FI_PUBLISHER_CREDENTIAL_FILE",
            self.fi_file,
        ), mock.patch.object(
            loader,
            "FIXED_ARVAN_S3_IR_RECEIVER_CREDENTIAL_FILE",
            self.ir_file,
        ):
            return loader.load_root_owned_arvan_s3_separated_credential_pair(
                self._config() if config is None else config
            )

    def test_admits_two_separate_files_and_projects_only_fingerprints(self) -> None:
        pair = self._load()
        projection = loader.project_root_owned_arvan_s3_immutability_probe_credentials(
            pair,
            config=self._config(),
        )

        self.assertEqual(_identity(_FI_ACCESS), pair.fi_publisher_identity_sha256)
        self.assertEqual(_identity(_IR_ACCESS), pair.ir_receiver_identity_sha256)
        self.assertNotEqual(pair.fi_publisher_identity_sha256, pair.ir_receiver_identity_sha256)
        self.assertEqual("webapp_fi", pair.source_site)
        self.assertEqual("webapp_ir", pair.destination_site)
        self.assertEqual("fi-publisher", projection.fi_publisher_role)
        self.assertEqual("ir-receiver", projection.ir_receiver_role)
        self.assertEqual(
            live_probe._FI_ALLOWED_OPERATIONS,
            projection.fi_publisher_allowed_operations,
        )
        self.assertEqual(
            live_probe._IR_ALLOWED_OPERATIONS,
            projection.ir_receiver_allowed_operations,
        )
        self.assertEqual(ENDPOINT, projection.endpoint)
        self.assertEqual(BUCKET, projection.bucket)

        public_rendering = repr(pair) + repr(projection) + repr(asdict(pair))
        for raw in (_FI_ACCESS, _FI_SECRET, _IR_ACCESS, _IR_SECRET, str(self.fi_file), str(self.ir_file)):
            self.assertNotIn(raw, public_rendering)
        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN") as raised:
            pickle.dumps(pair)
        self.assertNotIn(_FI_SECRET, str(raised.exception))
        self.assertNotIn(_IR_SECRET, str(raised.exception))

    def test_receiver_local_private_admission_never_opens_fi_secret(self) -> None:
        # This private seam is used only by the reviewed WA-IR exact-pull
        # runtime. The FI file is deliberately absent: receiver-local startup
        # may validate the common route but must not copy/read FI credentials.
        absent_fi = self.security / "fi-must-not-be-opened.json"
        with mock.patch.object(
            loader,
            "FIXED_ARVAN_S3_FI_PUBLISHER_CREDENTIAL_FILE",
            absent_fi,
        ), mock.patch.object(
            loader,
            "FIXED_ARVAN_S3_IR_RECEIVER_CREDENTIAL_FILE",
            self.ir_file,
        ), mock.patch.object(
            loader,
            "_load_credential",
            wraps=loader._load_credential,
        ) as opened:
            facts, receiver = loader._load_root_owned_ir_receiver_credential_facts(  # type: ignore[attr-defined]
                self._config()
            )
        self.assertEqual(ENDPOINT, facts.endpoint)
        self.assertEqual(REGION, facts.region)
        self.assertEqual(BUCKET, facts.bucket)
        self.assertEqual(_identity(_IR_ACCESS), receiver.identity_sha256)
        self.assertEqual(
            [mock.call(
                self.ir_file,
                expected_role="ir-receiver",
                expected_action_profile="ir-receiver-exact-readonly-v1",
            )],
            opened.call_args_list,
        )

        with mock.patch.object(loader, "_load_credential") as unopened:
            with self.assertRaisesRegex(
                loader.ArvanS3SeparatedCredentialLoaderError,
                "^ARVAN_S3_SEPARATED_CREDENTIAL_LOADER_DISABLED$",
            ):
                loader._load_root_owned_ir_receiver_credential_facts(  # type: ignore[attr-defined]
                    self._config(enabled=False)
                )
        unopened.assert_not_called()

    def test_fi_recovery_private_admission_never_opens_ir_secret(self) -> None:
        # This symmetric private seam is used only by the reviewed WA-FI
        # encrypted recovery-material handoff. The IR file is deliberately
        # absent: FI publishing must not read/copy receiver credentials.
        absent_ir = self.security / "ir-must-not-be-opened.json"
        with mock.patch.object(
            loader,
            "FIXED_ARVAN_S3_FI_PUBLISHER_CREDENTIAL_FILE",
            self.fi_file,
        ), mock.patch.object(
            loader,
            "FIXED_ARVAN_S3_IR_RECEIVER_CREDENTIAL_FILE",
            absent_ir,
        ), mock.patch.object(
            loader,
            "_load_credential",
            wraps=loader._load_credential,
        ) as opened:
            facts, publisher = loader._load_root_owned_fi_publisher_credential_facts(  # type: ignore[attr-defined]
                self._config()
            )
        self.assertEqual(ENDPOINT, facts.endpoint)
        self.assertEqual(REGION, facts.region)
        self.assertEqual(BUCKET, facts.bucket)
        self.assertEqual(_identity(_FI_ACCESS), publisher.identity_sha256)
        self.assertEqual(
            [mock.call(
                self.fi_file,
                expected_role="fi-publisher",
                expected_action_profile="fi-publisher-immutable-create-only-v1",
            )],
            opened.call_args_list,
        )

        with mock.patch.object(loader, "_load_credential") as unopened:
            with self.assertRaisesRegex(
                loader.ArvanS3SeparatedCredentialLoaderError,
                "^ARVAN_S3_SEPARATED_CREDENTIAL_LOADER_DISABLED$",
            ):
                loader._load_root_owned_fi_publisher_credential_facts(  # type: ignore[attr-defined]
                    self._config(enabled=False)
                )
        unopened.assert_not_called()

    def test_config_is_default_off_pure_and_factory_policy_aligned(self) -> None:
        disabled = self._config(enabled=False, endpoint=ENDPOINT + "/")
        with mock.patch.object(loader, "_load_credential") as read_file:
            with self.assertRaisesRegex(
                loader.ArvanS3SeparatedCredentialLoaderError,
                "^ARVAN_S3_SEPARATED_CREDENTIAL_LOADER_DISABLED$",
            ):
                self._load(disabled)
            read_file.assert_not_called()

        normalized = loader.validate_root_owned_arvan_s3_separated_credential_loader_config(disabled)
        self.assertFalse(normalized.enabled)
        self.assertEqual(ENDPOINT, normalized.endpoint)
        with self.assertRaisesRegex(
            loader.ArvanS3SeparatedCredentialLoaderError,
            "^ARVAN_S3_SEPARATED_CREDENTIAL_LEGACY_PROFILE_MIGRATION_REQUIRED$",
        ):
            loader.validate_root_owned_arvan_s3_separated_credential_loader_config(
                self._config(fi_publisher_action_profile="fi-publisher-immutable-preflight-v1")
            )
        for bad in (
            self._config(source_site="webapp_ir"),
            self._config(destination_site="webapp_fi"),
            self._config(fi_publisher_action_profile="broad-list-and-delete"),
            self._config(ir_receiver_action_profile="put-object"),
            self._config(direct_site_control="allowed"),
            self._config(destination_object_ingest="push"),
            self._config(endpoint="http://s3.ir-thr-at1.arvanstorage.ir"),
            self._config(region="wrong-region"),
            self._config(enabled=1),
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(loader.ArvanS3SeparatedCredentialLoaderError):
                    loader.validate_root_owned_arvan_s3_separated_credential_loader_config(bad)

    def test_nonroot_and_file_collision_fail_before_opening_credentials(self) -> None:
        with mock.patch.object(loader.os, "geteuid", return_value=1000), mock.patch.object(
            loader,
            "_load_credential",
        ) as read_file:
            with self.assertRaisesRegex(
                loader.ArvanS3SeparatedCredentialLoaderError,
                "^ARVAN_S3_SEPARATED_CREDENTIAL_ROOT_REQUIRED$",
            ):
                self._load()
            read_file.assert_not_called()

        pair = self._load()
        with mock.patch.object(loader.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(
                loader.ArvanS3SeparatedCredentialLoaderError,
                "^ARVAN_S3_SEPARATED_CREDENTIAL_ROOT_REQUIRED$",
            ):
                loader.require_verified_arvan_s3_separated_credential_pair(
                    pair,
                    config=self._config(),
                )

        with mock.patch.object(
            loader,
            "FIXED_ARVAN_S3_FI_PUBLISHER_CREDENTIAL_FILE",
            self.fi_file,
        ), mock.patch.object(
            loader,
            "FIXED_ARVAN_S3_IR_RECEIVER_CREDENTIAL_FILE",
            self.fi_file,
        ), mock.patch.object(loader, "_load_credential") as read_file:
            with self.assertRaisesRegex(
                loader.ArvanS3SeparatedCredentialLoaderError,
                "^ARVAN_S3_SEPARATED_CREDENTIAL_FILE_COLLISION$",
            ):
                loader.load_root_owned_arvan_s3_separated_credential_pair(self._config())
            read_file.assert_not_called()

    def test_unsafe_mode_or_symlink_is_refused_without_secret_echo(self) -> None:
        self.fi_file.chmod(0o640)
        with self.assertRaises(loader.ArvanS3SeparatedCredentialLoaderError) as raised:
            self._load()
        self.assertEqual("ARVAN_S3_SEPARATED_CREDENTIAL_FILE_INVALID", raised.exception.code)
        self.assertNotIn(_FI_SECRET, str(raised.exception))

        self.fi_file.chmod(0o600)
        self.security.chmod(0o750)
        with self.assertRaisesRegex(
            loader.ArvanS3SeparatedCredentialLoaderError,
            "^ARVAN_S3_SEPARATED_CREDENTIAL_FILE_INVALID$",
        ):
            self._load()

        self.security.chmod(0o700)
        target = self.security / "not-followed.json"
        self._write_credential(
            target,
            role="fi-publisher",
            action_profile="fi-publisher-immutable-create-only-v1",
            access_key="OTHER-FI-ACCESS",
            secret_key="OTHER-FI-SECRET",
        )
        self.fi_file.unlink()
        self.fi_file.symlink_to(target)
        with self.assertRaisesRegex(
            loader.ArvanS3SeparatedCredentialLoaderError,
            "^ARVAN_S3_SEPARATED_CREDENTIAL_FILE_INVALID$",
        ):
            self._load()

    def test_role_action_json_shape_and_duplicate_fields_fail_closed(self) -> None:
        self._write_credential(
            self.fi_file,
            role="ir-receiver",
            action_profile="fi-publisher-immutable-create-only-v1",
            access_key=_FI_ACCESS,
            secret_key=_FI_SECRET,
        )
        with self.assertRaisesRegex(
            loader.ArvanS3SeparatedCredentialLoaderError,
            "^ARVAN_S3_SEPARATED_CREDENTIAL_SCOPE_INVALID$",
        ):
            self._load()

        self._write_credential(
            self.fi_file,
            role="fi-publisher",
            action_profile="fi-publisher-immutable-preflight-v1",
            access_key=_FI_ACCESS,
            secret_key=_FI_SECRET,
        )
        with self.assertRaisesRegex(
            loader.ArvanS3SeparatedCredentialLoaderError,
            "^ARVAN_S3_SEPARATED_CREDENTIAL_LEGACY_PROFILE_MIGRATION_REQUIRED$",
        ):
            self._load()
        duplicate_secret = (
            b'{"schema":"gold-trade-physical-arvan-s3-machine-user-credential-v1",'
            b'"role":"fi-publisher","action_profile":"fi-publisher-immutable-create-only-v1",'
            b'"access_key":"FI-ACCESS-UNIQUE-20260731",'
            b'"secret_key":"NEVER-ECHO-ME-ONE","secret_key":"NEVER-ECHO-ME-TWO"}'
        )
        self._write_credential(
            self.fi_file,
            role="fi-publisher",
            action_profile="fi-publisher-immutable-create-only-v1",
            access_key=_FI_ACCESS,
            secret_key=_FI_SECRET,
            raw=duplicate_secret,
        )
        with self.assertRaises(loader.ArvanS3SeparatedCredentialLoaderError) as raised:
            self._load()
        self.assertEqual("ARVAN_S3_SEPARATED_CREDENTIAL_FILE_INVALID", raised.exception.code)
        self.assertNotIn("NEVER-ECHO-ME", str(raised.exception))

    def test_same_identity_or_secret_pair_is_rejected(self) -> None:
        self._write_credential(
            self.ir_file,
            role="ir-receiver",
            action_profile="ir-receiver-exact-readonly-v1",
            access_key=_FI_ACCESS,
            secret_key="IR-DIFFERENT-SECRET",
        )
        with self.assertRaisesRegex(
            loader.ArvanS3SeparatedCredentialLoaderError,
            "^ARVAN_S3_SEPARATED_CREDENTIALS_NOT_SEPARATE$",
        ):
            self._load()

        self._write_credential(
            self.ir_file,
            role="ir-receiver",
            action_profile="ir-receiver-exact-readonly-v1",
            access_key=_IR_ACCESS,
            secret_key=_IR_SECRET,
        )
        self._write_credential(
            self.ir_file,
            role="ir-receiver",
            action_profile="ir-receiver-exact-readonly-v1",
            access_key="IR-DIFFERENT-ACCESS",
            secret_key=_FI_SECRET,
        )
        with self.assertRaisesRegex(
            loader.ArvanS3SeparatedCredentialLoaderError,
            "^ARVAN_S3_SEPARATED_CREDENTIALS_NOT_SEPARATE$",
        ):
            self._load()

    def test_opaque_pair_is_bound_to_config_and_tamper_fails(self) -> None:
        pair = self._load()
        self.assertIs(
            pair,
            loader.require_verified_arvan_s3_separated_credential_pair(
                pair,
                config=self._config(),
            ),
        )
        with self.assertRaisesRegex(
            loader.ArvanS3SeparatedCredentialLoaderError,
            "^ARVAN_S3_SEPARATED_CREDENTIAL_PAIR_TAMPERED$",
        ):
            loader.require_verified_arvan_s3_separated_credential_pair(
                pair,
                config=self._config(bucket="another-private-bucket"),
            )
        object.__setattr__(pair, "fi_publisher_identity_sha256", pair.ir_receiver_identity_sha256)
        with self.assertRaisesRegex(
            loader.ArvanS3SeparatedCredentialLoaderError,
            "^ARVAN_S3_SEPARATED_CREDENTIAL_PAIR_TAMPERED$",
        ):
            loader.project_root_owned_arvan_s3_immutability_probe_credentials(
                pair,
                config=self._config(),
            )

    def test_source_has_no_sdk_network_probe_or_command_dependency(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(MODULE_PATH))
        imports: set[str] = set()
        call_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    call_names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    call_names.add(node.func.attr)
        self.assertTrue(imports.issubset({
            "__future__", "dataclasses", "hashlib", "json", "os", "pathlib", "re", "stat", "typing", "core",
        }))
        self.assertFalse(imports & {
            "boto3", "botocore", "paramiko", "requests", "socket", "subprocess", "urllib",
        })
        self.assertFalse(call_names & {
            "client", "connect", "get_object", "head_object", "list_objects", "put_object", "run", "Popen", "system",
        })


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
