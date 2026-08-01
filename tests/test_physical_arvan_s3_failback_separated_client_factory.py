"""No-network tests for the concrete reverse role-only S3 client seam."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_arvan_s3_failback_separated_client_factory as factory_module
from core import physical_arvan_s3_failback_separated_credential_loader as credentials
from core import physical_ir_to_fi_object_storage_failback_preflight as preflight
from core.object_delta_role_matrix_rollover import (
    build_object_delta_role_matrix_witnessed_term_proof,
    verify_object_delta_role_matrix_witnessed_term,
)
from core.physical_arvan_s3_failback_route_commitment import (
    derive_physical_arvan_s3_failback_four_role_route_binding_sha256,
    derive_physical_arvan_s3_failback_route_scope_sha256,
)
from tests.physical_arvan_s3_four_role_fixture import make_four_role_fixture
from tests.physical_arvan_s3_four_role_live_iam_fixture import (
    make_four_role_live_iam_durable_admission_fixture,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
REGION = "ir-thr-at1"
BUCKET = "private-physical-failback"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
IR_ACCESS = "IR-PUBLISHER-ACCESS-FACTORY-20260731"
IR_SECRET = "IR-PUBLISHER-SECRET-FACTORY-20260731"
FI_ACCESS = "FI-RECEIVER-ACCESS-FACTORY-20260731"
FI_SECRET = "FI-RECEIVER-SECRET-FACTORY-20260731"
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_arvan_s3_failback_separated_client_factory.py"
)


def _identity(access_key: str) -> str:
    return hashlib.sha256(
        b"gold-trade-arvan-s3-machine-user-identity-v1\x00" + access_key.encode("ascii")
    ).hexdigest()


def _sha(char: str) -> str:
    return char * 64


class _Raw:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get_bucket_versioning(self, **request: object):
        self.calls.append(("versioning", dict(request)))
        return {"Status": "Enabled"}

    def get_bucket_acl(self, **request: object):
        self.calls.append(("acl", dict(request)))
        return {"Owner": {"ID": "root"}, "Grants": []}

    def list_object_versions(self, **request: object):
        self.calls.append(("list", dict(request)))
        return {"Versions": [], "DeleteMarkers": [], "IsTruncated": False}

    def put_object(self, **request: object):
        self.calls.append(("put", dict(request)))
        return {"VersionId": "version-001"}

    def head_object(self, **request: object):
        self.calls.append(("head", dict(request)))
        return {"VersionId": request["VersionId"], "ContentLength": 1, "Metadata": {}}

    def get_object(self, **request: object):
        self.calls.append(("get", dict(request)))
        return {"VersionId": request["VersionId"], "Body": object(), "Metadata": {}}


@unittest.skipUnless(os.geteuid() == 0, "factory is intentionally root-only")
class PhysicalArvanS3FailbackSeparatedClientFactoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="arvan-failback-factory-")
        root = Path(self.temporary.name).resolve()
        root.chmod(0o700)
        self.security = root / "security"
        self.security.mkdir(mode=0o700)
        self.security.chmod(0o700)
        self.ir_path = self.security / "arvan-s3-ir-publisher-credentials.json"
        self.fi_path = self.security / "arvan-s3-fi-receiver-credentials.json"
        self._write(
            self.ir_path,
            role="ir-publisher",
            action_profile="ir-publisher-immutable-create-only-v1",
            access_key=IR_ACCESS,
            secret_key=IR_SECRET,
        )
        self._write(
            self.fi_path,
            role="fi-receiver",
            action_profile="fi-receiver-exact-readonly-v1",
            access_key=FI_ACCESS,
            secret_key=FI_SECRET,
        )
        self.exact_prefix = "physical-failback/ir-fi-factory-20260731/" + RELEASE + "/"
        self.four_role_fixture = make_four_role_fixture(
            campaign_id="ir-fi-factory-20260731",
            release_sha=RELEASE,
            fi_publisher_identity_sha256=_sha("a"),
            ir_receiver_identity_sha256=_sha("b"),
            ir_publisher_identity_sha256=_identity(IR_ACCESS),
            fi_receiver_identity_sha256=_identity(FI_ACCESS),
            endpoint=ENDPOINT,
            region=REGION,
            reverse_bucket=BUCKET,
        )
        self.binding = self.four_role_fixture.binding
        self.live_iam = make_four_role_live_iam_durable_admission_fixture(
            binding=self.binding,
            observed_at=NOW,
        )
        observed = preflight.build_physical_ir_to_fi_object_storage_failback_observation(
            binding=self.binding,
            four_role_projection_binding=self.four_role_fixture.verified_binding,
            four_role_live_iam_binding=self.live_iam.live_iam_binding,
            four_role_live_iam_durable_admission=self.live_iam.live_iam_durable_admission,
            observed_at=NOW,
        )
        self.preflight = preflight.verify_physical_ir_to_fi_object_storage_failback_preflight(
            observed,
            binding=self.binding,
            four_role_projection_binding=self.four_role_fixture.verified_binding,
            four_role_live_iam_binding=self.live_iam.live_iam_binding,
            four_role_live_iam_durable_admission=self.live_iam.live_iam_durable_admission,
            now=NOW,
        )
        self.preflight_config = self.four_role_fixture.preflight_config(
            four_role_live_iam_binding=self.live_iam.live_iam_binding,
            four_role_live_iam_durable_admission=self.live_iam.live_iam_durable_admission,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, path: Path, *, role: str, action_profile: str, access_key: str, secret_key: str) -> None:
        path.write_text(
            json.dumps(
                {
                    "schema": credentials.PHYSICAL_ARVAN_S3_MACHINE_USER_CREDENTIAL_SCHEMA,
                    "role": role,
                    "action_profile": action_profile,
                    "access_key": access_key,
                    "secret_key": secret_key,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            encoding="ascii",
        )
        path.chmod(0o600)

    def _loader_config(self, **changes: object):
        values: dict[str, object] = {
            "endpoint": ENDPOINT,
            "region": REGION,
            "bucket": BUCKET,
            "enabled": True,
        }
        values.update(changes)
        return credentials.RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig(**values)

    def _config(self, **changes: object):
        values: dict[str, object] = {
            "credential_loader_config": self._loader_config(),
            "preflight_config": self.preflight_config,
            "enabled": True,
        }
        values.update(changes)
        return factory_module.RootOwnedArvanS3FailbackSeparatedClientFactoryConfig(**values)

    def _factory(self, **changes: object):
        return factory_module.RootOwnedArvanS3FailbackSeparatedClientFactory(self._config(**changes))

    def _paths(self):
        return mock.patch.multiple(
            credentials,
            FIXED_ARVAN_S3_IR_PUBLISHER_CREDENTIAL_FILE=self.ir_path,
            FIXED_ARVAN_S3_FI_RECEIVER_CREDENTIAL_FILE=self.fi_path,
        )

    @staticmethod
    def _term():
        signer = Ed25519PrivateKey.generate()
        proof = build_object_delta_role_matrix_witnessed_term_proof(
            holder_site="webapp_ir",
            writer_epoch=41,
            writer_lease_id="ir-factory-writer-41",
            witness_transition_id="ir-factory-transition-41",
            issued_at=NOW - timedelta(seconds=10),
            expires_at=NOW + timedelta(seconds=100),
            witness_signer=signer,
        )
        public = signer.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return verify_object_delta_role_matrix_witnessed_term(
            proof,
            witness_public_key=public,
            maximum_lease_duration_seconds=120,
            safety_margin_seconds=5,
            now=NOW,
        )

    def test_config_is_default_off_and_binding_is_required(self) -> None:
        disabled = self._config(enabled=False, credential_loader_config=self._loader_config(enabled=False), preflight_config=preflight.PhysicalIrToFiObjectStorageFailbackPreflightConfig(binding=self.binding))
        normalized = factory_module.validate_root_owned_arvan_s3_failback_separated_client_factory_config(disabled)
        self.assertFalse(normalized.enabled)
        with self.assertRaises(factory_module.ArvanS3FailbackSeparatedClientFactoryError):
            factory_module.validate_root_owned_arvan_s3_failback_separated_client_factory_config(
                self._config(preflight_config=preflight.PhysicalIrToFiObjectStorageFailbackPreflightConfig())
            )
        with self._paths(), mock.patch.object(credentials, "_load_credential") as opened:
            with self.assertRaisesRegex(
                factory_module.ArvanS3FailbackSeparatedClientFactoryError,
                "^ARVAN_S3_FAILBACK_SEPARATED_CLIENT_FACTORY_DISABLED$",
            ):
                factory_module.RootOwnedArvanS3FailbackSeparatedClientFactory(disabled).ir_publisher_identity_projection()
            opened.assert_not_called()
        with self.assertRaisesRegex(
            factory_module.ArvanS3FailbackSeparatedClientFactoryError,
            "ROUTE_COMMITMENT_MISMATCH",
        ):
            self._factory(credential_loader_config=self._loader_config(bucket="another-private-bucket"))

    def test_identity_projections_open_only_the_local_role_file(self) -> None:
        instance = self._factory()
        absent = self.security / "must-not-open.json"
        with mock.patch.object(credentials, "FIXED_ARVAN_S3_IR_PUBLISHER_CREDENTIAL_FILE", self.ir_path), mock.patch.object(
            credentials, "FIXED_ARVAN_S3_FI_RECEIVER_CREDENTIAL_FILE", absent
        ), mock.patch.object(credentials, "_load_credential", wraps=credentials._load_credential) as opened:
            projection = instance.ir_publisher_identity_projection()
        self.assertEqual("ir-publisher", projection.role)
        self.assertEqual(_identity(IR_ACCESS), projection.identity_sha256)
        self.assertEqual([mock.call(self.ir_path, expected_role="ir-publisher", expected_action_profile="ir-publisher-immutable-create-only-v1")], opened.call_args_list)

        with mock.patch.object(credentials, "FIXED_ARVAN_S3_IR_PUBLISHER_CREDENTIAL_FILE", absent), mock.patch.object(
            credentials, "FIXED_ARVAN_S3_FI_RECEIVER_CREDENTIAL_FILE", self.fi_path
        ), mock.patch.object(credentials, "_load_credential", wraps=credentials._load_credential) as opened:
            projection = instance.fi_receiver_identity_projection()
        self.assertEqual("fi-receiver", projection.role)
        self.assertEqual(_identity(FI_ACCESS), projection.identity_sha256)
        self.assertEqual([mock.call(self.fi_path, expected_role="fi-receiver", expected_action_profile="fi-receiver-exact-readonly-v1")], opened.call_args_list)

    def test_ir_admission_and_callback_use_only_ir_publisher_and_failback_prefix(self) -> None:
        instance = self._factory()
        term = self._term()
        admission = instance.admit_ir_publisher_failback_handoff(
            preflight=self.preflight, current_witnessed_term=term, now=NOW
        )
        admission = instance.require_ir_publisher_failback_handoff_admission(
            admission, preflight=self.preflight, current_witnessed_term=term, now=NOW
        )
        raw = _Raw()
        with self._paths(), mock.patch.object(factory_module, "_load_boto_sdk", return_value=(object(), object())), mock.patch.object(
            factory_module, "_create_raw_client", return_value=raw
        ), mock.patch.object(credentials, "_load_credential", wraps=credentials._load_credential) as opened:
            result = instance.execute_ir_publisher_failback_handoff(
                admission=admission,
                now=NOW,
                operation=lambda client, route: (
                    client.list_object_versions(Bucket=BUCKET, Prefix=self.exact_prefix + "wal.age"),
                    route.object_storage_namespace,
                ),
            )
        self.assertEqual(({"Versions": [], "DeleteMarkers": [], "IsTruncated": False}, "physical-failback"), result)
        self.assertEqual([mock.call(self.ir_path, expected_role="ir-publisher", expected_action_profile="ir-publisher-immutable-create-only-v1")], opened.call_args_list)
        self.assertEqual("list", raw.calls[0][0])

    def test_fi_admission_and_callback_uses_only_fi_receiver_and_exact_reads_once(self) -> None:
        instance = self._factory()
        term = self._term()
        admission = instance.admit_fi_receiver_failback_exact_pull(
            preflight=self.preflight, current_witnessed_term=term, now=NOW
        )
        admission = instance.require_fi_receiver_failback_exact_pull_admission(
            admission, preflight=self.preflight, current_witnessed_term=term, now=NOW
        )
        raw = _Raw()
        with self._paths(), mock.patch.object(factory_module, "_load_boto_sdk", return_value=(object(), object())), mock.patch.object(
            factory_module, "_create_raw_client", return_value=raw
        ), mock.patch.object(credentials, "_load_credential", wraps=credentials._load_credential) as opened:
            result = instance.execute_fi_receiver_failback_exact_pull(
                admission=admission,
                now=NOW,
                operation=lambda client, route: (
                    client.head_object(Bucket=BUCKET, Key=self.exact_prefix + "base.age", VersionId="version-001"),
                    client.get_object(Bucket=BUCKET, Key=self.exact_prefix + "base.age", VersionId="version-001"),
                    route.object_storage_namespace,
                ),
            )
        self.assertEqual("physical-failback", result[2])
        self.assertEqual([mock.call(self.fi_path, expected_role="fi-receiver", expected_action_profile="fi-receiver-exact-readonly-v1")], opened.call_args_list)
        self.assertEqual(["head", "get"], [name for name, _request in raw.calls])

    def test_normal_namespace_wrong_identity_and_stale_admission_fail_closed(self) -> None:
        instance = self._factory()
        term = self._term()
        admission = instance.admit_ir_publisher_failback_handoff(
            preflight=self.preflight, current_witnessed_term=term, now=NOW
        )
        raw = _Raw()
        with self._paths(), mock.patch.object(factory_module, "_load_boto_sdk", return_value=(object(), object())), mock.patch.object(
            factory_module, "_create_raw_client", return_value=raw
        ):
            with self.assertRaisesRegex(
                factory_module.ArvanS3FailbackSeparatedClientFactoryError,
                "OBJECT_KEY_INVALID",
            ):
                instance.execute_ir_publisher_failback_handoff(
                    admission=admission,
                    now=NOW,
                    operation=lambda client, route: client.get_object(
                        Bucket=BUCKET, Key="physical-wal/campaign/wal.age", VersionId="version-001"
                    ),
                )
            with self.assertRaisesRegex(
                factory_module.ArvanS3FailbackSeparatedClientFactoryError,
                "OBJECT_KEY_INVALID",
            ):
                instance.execute_ir_publisher_failback_handoff(
                    admission=admission,
                    now=NOW,
                    operation=lambda client, route: client.get_object(
                        Bucket=BUCKET,
                        Key="physical-failback/other-campaign/wal.age",
                        VersionId="version-001",
                    ),
                )
        self._write(
            self.ir_path,
            role="ir-publisher",
            action_profile="ir-publisher-immutable-create-only-v1",
            access_key="WRONG-IR-PUBLISHER-ACCESS",
            secret_key=IR_SECRET,
        )
        with self._paths(), mock.patch.object(factory_module, "_load_boto_sdk") as sdk:
            with self.assertRaisesRegex(
                factory_module.ArvanS3FailbackSeparatedClientFactoryError,
                "IR_CREDENTIAL_MISMATCH",
            ):
                instance.execute_ir_publisher_failback_handoff(
                    admission=admission, now=NOW, operation=lambda client, route: None
                )
            sdk.assert_not_called()
        with self.assertRaisesRegex(factory_module.ArvanS3FailbackSeparatedClientFactoryError, "IR_ADMISSION_INVALID"):
            instance.execute_ir_publisher_failback_handoff(
                admission=admission, now=NOW + timedelta(seconds=301), operation=lambda client, route: None
            )

    def test_callback_proxy_is_revoked_and_cannot_escape_in_a_container(self) -> None:
        instance = self._factory()
        term = self._term()
        admission = instance.admit_fi_receiver_failback_exact_pull(
            preflight=self.preflight, current_witnessed_term=term, now=NOW
        )
        raw = _Raw()
        retained: dict[str, object] = {}
        with self._paths(), mock.patch.object(factory_module, "_load_boto_sdk", return_value=(object(), object())), mock.patch.object(
            factory_module, "_create_raw_client", return_value=raw
        ):
            self.assertEqual(
                "completed",
                instance.execute_fi_receiver_failback_exact_pull(
                    admission=admission,
                    now=NOW,
                    operation=lambda client, route: (retained.setdefault("client", client), "completed")[1],
                ),
            )
            with self.assertRaisesRegex(
                factory_module.ArvanS3FailbackSeparatedClientFactoryError,
                "CALLBACK_REVOKED",
            ):
                retained["client"].get_object(  # type: ignore[union-attr]
                    Bucket=BUCKET,
                    Key=self.exact_prefix + "base.age",
                    VersionId="version-001",
                )

        admission = instance.admit_fi_receiver_failback_exact_pull(
            preflight=self.preflight, current_witnessed_term=term, now=NOW
        )
        with self._paths(), mock.patch.object(factory_module, "_load_boto_sdk", return_value=(object(), object())), mock.patch.object(
            factory_module, "_create_raw_client", return_value=_Raw()
        ):
            with self.assertRaisesRegex(
                factory_module.ArvanS3FailbackSeparatedClientFactoryError,
                "CALLBACK_INVALID",
            ):
                instance.execute_fi_receiver_failback_exact_pull(
                    admission=admission,
                    now=NOW,
                    operation=lambda client, route: {"nested": [client]},
                )

    def test_exact_prefix_applies_to_receiver_and_publisher_markers(self) -> None:
        instance = self._factory()
        term = self._term()
        ir_admission = instance.admit_ir_publisher_failback_handoff(
            preflight=self.preflight, current_witnessed_term=term, now=NOW
        )
        with self._paths(), mock.patch.object(factory_module, "_load_boto_sdk", return_value=(object(), object())), mock.patch.object(
            factory_module, "_create_raw_client", return_value=_Raw()
        ):
            with self.assertRaisesRegex(
                factory_module.ArvanS3FailbackSeparatedClientFactoryError,
                "OBJECT_KEY_INVALID",
            ):
                instance.execute_ir_publisher_failback_handoff(
                    admission=ir_admission,
                    now=NOW,
                    operation=lambda client, route: client.list_object_versions(
                        Bucket=BUCKET,
                        Prefix=self.exact_prefix + "wal.age",
                        KeyMarker="physical-failback/other-campaign/wal.age",
                    ),
                )
        fi_admission = instance.admit_fi_receiver_failback_exact_pull(
            preflight=self.preflight, current_witnessed_term=term, now=NOW
        )
        with self._paths(), mock.patch.object(factory_module, "_load_boto_sdk", return_value=(object(), object())), mock.patch.object(
            factory_module, "_create_raw_client", return_value=_Raw()
        ):
            with self.assertRaisesRegex(
                factory_module.ArvanS3FailbackSeparatedClientFactoryError,
                "OBJECT_KEY_INVALID",
            ):
                instance.execute_fi_receiver_failback_exact_pull(
                    admission=fi_admission,
                    now=NOW,
                    operation=lambda client, route: client.get_object(
                        Bucket=BUCKET,
                        Key="physical-failback/other-campaign/base.age",
                        VersionId="version-001",
                    ),
                )

    def test_source_has_no_normal_factory_or_network_imports(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(MODULE_PATH))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(item.name for item in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        self.assertFalse(any("physical_arvan_s3_separated_client_factory" in item for item in imports))
        self.assertFalse(any("physical_arvan_s3_separated_credential_loader" in item for item in imports))
        self.assertFalse(any(item.split(".", 1)[0] in {"socket", "subprocess", "requests", "paramiko"} for item in imports))
        self.assertNotIn("arvan-s3-fi-publisher-credentials.json", source)
        self.assertNotIn("arvan-s3-ir-receiver-credentials.json", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
